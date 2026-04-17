"""DataUpdateCoordinator for a single ShineMonitor plant."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ShineApiError, ShineAuthError, ShineClient, ShineConnectionError
from .const import CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL, DEVTYPE_GRID_TIE, DOMAIN, FIELD_MAP

_LOGGER = logging.getLogger(__name__)


@dataclass
class DeviceKey:
    """Identity tuple for a device plus its last-seen state."""

    collector_pn: str
    collector_alias: str
    collector_status: int
    devcode: int
    devaddr: int
    sn: str
    status: int
    com_status: int


@dataclass
class ShineData:
    """What every coordinator tick returns, read by all entities."""

    plant_info: dict[str, Any] = field(default_factory=dict)
    devices: list[DeviceKey] = field(default_factory=list)
    realtime: dict[str, dict[str, Any]] = field(default_factory=dict)
    energy: dict[str, dict[str, Any]] = field(default_factory=dict)
    alarms: list[dict[str, Any]] = field(default_factory=list)
    fields: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    collectors: list[dict[str, Any]] = field(default_factory=list)
    # Plant-level history used to build the real-time power graph and to
    # backfill long-term statistics so monthly/yearly bar charts don't have to
    # wait 30/365 days after install before showing anything.
    power_curve: list[dict[str, Any]] = field(default_factory=list)
    month_per_day: list[dict[str, Any]] = field(default_factory=list)
    year_per_month: list[dict[str, Any]] = field(default_factory=list)
    total_per_year: list[dict[str, Any]] = field(default_factory=list)


class ShineCoordinator(DataUpdateCoordinator[ShineData]):
    """Poll one plant every ``scan_interval`` seconds."""

    config_entry: ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: ShineClient,
        plantid: int,
    ) -> None:
        interval = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}:{plantid}",
            update_interval=timedelta(seconds=interval),
        )
        self.config_entry = entry
        self.client = client
        self.plantid = plantid
        self._static_loaded = False

    async def _async_load_static(self, data: ShineData) -> None:
        """Fetch things that rarely change (plant metadata, collectors, field schemas)."""
        try:
            data.plant_info = await self.client.query_plant_info(self.plantid)
            data.collectors = await self.client.query_collectors(self.plantid)
        except (ShineApiError, ShineConnectionError) as err:
            _LOGGER.warning("static plant metadata fetch failed: %s", err)

        # Discover per-devcode field catalogs.  Fall back to the static FIELD_MAP
        # if the API rejects the call.
        devcodes = {d.devcode for d in data.devices} or {632}
        for devcode in devcodes:
            try:
                data.fields[devcode] = await self.client.query_plant_device_charts_fields_by_type(
                    self.plantid, DEVTYPE_GRID_TIE, devcode
                )
            except (ShineApiError, ShineConnectionError) as err:
                _LOGGER.warning(
                    "field schema for devcode=%s failed (%s); using built-in map",
                    devcode,
                    err,
                )
                data.fields[devcode] = [
                    {"optional": key, "name": meta["name"], "uint": meta["unit"]}
                    for key, meta in FIELD_MAP.items()
                ]

        self._static_loaded = True

    async def _async_update_data(self) -> ShineData:
        data = ShineData()
        try:
            await self.client.ensure_valid_session()
            status = await self.client.query_plant_device_status(self.plantid)
            data.devices = _flatten_devices(status)

            if not self._static_loaded:
                await self._async_load_static(data)
            else:
                last = self.data
                data.plant_info = last.plant_info if last else {}
                data.collectors = last.collectors if last else []
                data.fields = last.fields if last else {}

            today = date.today().isoformat()
            realtime_tasks: dict[str, Any] = {}
            for dev in data.devices:
                if dev.com_status == 1 and dev.status == 1:
                    realtime_tasks[dev.sn] = self.client.query_device_real_last_data(
                        pn=dev.collector_pn,
                        devcode=dev.devcode,
                        sn=dev.sn,
                        devaddr=dev.devaddr,
                        date=today,
                    )
            if realtime_tasks:
                results = await asyncio.gather(
                    *realtime_tasks.values(), return_exceptions=True
                )
                for sn, result in zip(realtime_tasks.keys(), results, strict=True):
                    if isinstance(result, Exception):
                        _LOGGER.debug("realtime fetch for %s failed: %s", sn, result)
                        continue
                    data.realtime[sn] = result

            energy_rows = await self.client.query_plant_device_designated_information(
                self.plantid, DEVTYPE_GRID_TIE, "energy_today,energy_total"
            )
            data.energy = {row["sn"]: row for row in energy_rows if row.get("sn")}

            today_iso = date.today().isoformat()
            try:
                data.power_curve = await self.client.query_plant_active_output_power_one_day(
                    self.plantid, today_iso
                )
            except (ShineApiError, ShineConnectionError) as err:
                _LOGGER.debug("power curve fetch failed: %s", err)

            try:
                data.month_per_day = await self.client.query_plant_energy_month_per_day(
                    self.plantid, today_iso[:7]
                )
            except (ShineApiError, ShineConnectionError) as err:
                _LOGGER.debug("month_per_day fetch failed: %s", err)

            try:
                data.year_per_month = await self.client.query_plant_energy_year_per_month(
                    self.plantid, today_iso[:4]
                )
            except (ShineApiError, ShineConnectionError) as err:
                _LOGGER.debug("year_per_month fetch failed: %s", err)

            try:
                data.total_per_year = await self.client.query_plant_energy_total_per_year(
                    self.plantid
                )
            except (ShineApiError, ShineConnectionError) as err:
                _LOGGER.debug("total_per_year fetch failed: %s", err)

            try:
                data.alarms = await self.client.web_query_plants_warning(self.plantid)
            except (ShineApiError, ShineConnectionError) as err:
                _LOGGER.debug("alarm fetch failed: %s", err)
                data.alarms = []

        except ShineAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except ShineConnectionError as err:
            raise UpdateFailed(str(err)) from err
        except ShineApiError as err:
            raise UpdateFailed(str(err)) from err

        return data


def _flatten_devices(status: dict[str, Any]) -> list[DeviceKey]:
    devices: list[DeviceKey] = []
    for coll in status.get("collector", []) or []:
        for dev in coll.get("device", []) or []:
            devices.append(
                DeviceKey(
                    collector_pn=str(coll.get("pn", "")),
                    collector_alias=str(coll.get("alias", "")),
                    collector_status=int(coll.get("status", 0) or 0),
                    devcode=int(dev.get("devcode", 0) or 0),
                    devaddr=int(dev.get("devaddr", 0) or 0),
                    sn=str(dev.get("sn", "")),
                    status=int(dev.get("status", 0) or 0),
                    com_status=int(dev.get("comStatus", 0) or 0),
                )
            )
    return devices

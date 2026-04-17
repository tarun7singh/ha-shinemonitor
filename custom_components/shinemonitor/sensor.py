"""Sensor platform for ShineMonitor."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    UnitOfApparentPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfFrequency,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DeviceKey, ShineCoordinator
from .entity import ShineEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class UnitSpec:
    """How to render a sensor based on the API-reported unit string."""

    native_unit: str | None
    device_class: SensorDeviceClass | None
    state_class: SensorStateClass | None


def _unit_spec(raw_unit: str | None, field_name: str) -> UnitSpec:
    unit = (raw_unit or "").strip()
    name_l = (field_name or "").lower()

    if unit in ("V",):
        return UnitSpec(UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT)
    if unit in ("A",):
        return UnitSpec(UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT, SensorStateClass.MEASUREMENT)
    if unit in ("W",):
        return UnitSpec(UnitOfPower.WATT, SensorDeviceClass.POWER, SensorStateClass.MEASUREMENT)
    if unit.upper() in ("VA",):
        return UnitSpec(UnitOfApparentPower.VOLT_AMPERE, SensorDeviceClass.APPARENT_POWER, SensorStateClass.MEASUREMENT)
    if unit.upper() in ("HZ",):
        return UnitSpec(UnitOfFrequency.HERTZ, SensorDeviceClass.FREQUENCY, SensorStateClass.MEASUREMENT)
    if unit in ("°C", "C"):
        return UnitSpec(UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT)
    if unit in ("kWh",):
        state = (
            SensorStateClass.TOTAL_INCREASING
            if any(k in name_l for k in ("total", "today", "energy"))
            else SensorStateClass.MEASUREMENT
        )
        return UnitSpec(UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY, state)
    if unit in ("h",):
        return UnitSpec(UnitOfTime.HOURS, SensorDeviceClass.DURATION, SensorStateClass.TOTAL_INCREASING)
    if unit in ("s", "S"):
        return UnitSpec(UnitOfTime.SECONDS, SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT)
    if unit in ("%",):
        return UnitSpec(PERCENTAGE, None, SensorStateClass.MEASUREMENT)
    return UnitSpec(unit or None, None, None)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: ShineCoordinator = hass.data[DOMAIN][entry.entry_id]
    known_keys: set[str] = set()

    @callback
    def _add_new() -> None:
        data = coordinator.data
        if data is None:
            return
        new: list[SensorEntity] = []

        for dev in data.devices:
            schema = data.fields.get(dev.devcode, [])
            for field_def in schema:
                optional = str(field_def.get("optional") or "")
                if not optional:
                    continue
                key = f"device:{dev.sn}:{optional}"
                if key in known_keys:
                    continue
                known_keys.add(key)
                new.append(
                    ShineDeviceFieldSensor(
                        coordinator=coordinator,
                        device=dev,
                        field_id=optional,
                        field_name=str(field_def.get("name") or optional),
                        raw_unit=str(field_def.get("uint") or ""),
                    )
                )

        for row in data.energy.values():
            sn = row.get("sn")
            if not sn:
                continue
            for field_id in ("energy_today", "energy_total"):
                key = f"plant:{sn}:{field_id}"
                if key in known_keys:
                    continue
                known_keys.add(key)
                dev = next((d for d in data.devices if d.sn == sn), None)
                if dev is None:
                    continue
                new.append(
                    ShinePlantEnergySensor(
                        coordinator=coordinator,
                        device=dev,
                        field_id=field_id,
                    )
                )

        if "plant:current_power" not in known_keys:
            known_keys.add("plant:current_power")
            new.append(ShinePlantCurrentPowerSensor(coordinator))

        for period in ("month", "year"):
            key = f"plant:{period}_energy"
            if key in known_keys:
                continue
            known_keys.add(key)
            new.append(ShinePlantPeriodEnergySensor(coordinator, period))

        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class ShineDeviceFieldSensor(ShineEntity, SensorEntity):
    """One sensor per field reported by ``queryDeviceRealLastData``."""

    def __init__(
        self,
        coordinator: ShineCoordinator,
        device: DeviceKey,
        field_id: str,
        field_name: str,
        raw_unit: str,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        self._field_id = field_id
        self._attr_name = field_name
        self._attr_unique_id = f"{DOMAIN}:{device.sn}:{field_id}"

        spec = _unit_spec(raw_unit, field_name)
        self._attr_native_unit_of_measurement = spec.native_unit
        self._attr_device_class = spec.device_class
        self._attr_state_class = spec.state_class
        self._attr_device_info = self.device_info_for(device)

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data
        if data is None:
            return None
        payload = data.realtime.get(self._device.sn) or {}
        val = _extract_field_value(payload, self._field_id)
        return _coerce_number(val)

    @property
    def available(self) -> bool:
        # ``status`` is the inverter's operational state (0 = standby/night,
        # 1 = producing).  ``comStatus`` is the data-pipeline freshness flag.
        # Availability in HA terms should only track communication — an idle
        # inverter reporting a heartbeat is still a valid source of data.
        if not super().available:
            return False
        if self.coordinator.data is None:
            return False
        dev = next(
            (d for d in self.coordinator.data.devices if d.sn == self._device.sn),
            None,
        )
        if dev is None:
            return False
        return dev.com_status == 1


class ShinePlantEnergySensor(ShineEntity, SensorEntity):
    """Plant-level energy_today / energy_total — reliable even when the device is offline."""

    def __init__(
        self,
        coordinator: ShineCoordinator,
        device: DeviceKey,
        field_id: str,
    ) -> None:
        super().__init__(coordinator)
        self._device = device
        self._field_id = field_id
        # Distinct names so the auto-generated entity_id doesn't collide with
        # the inverter-device field of the same logical meaning.  Shown as
        # "<plant> Today" / "<plant> Lifetime" in the UI.
        self._attr_name = "Today" if field_id == "energy_today" else "Lifetime"
        self._attr_unique_id = f"{DOMAIN}:plant-energy:{device.sn}:{field_id}"
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_device_info = self.plant_device_info

    @property
    def native_value(self) -> Any:
        data = self.coordinator.data
        if data is None:
            return None
        row = data.energy.get(self._device.sn) or {}
        return _coerce_number(row.get(self._field_id))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None:
            return {}
        row = data.energy.get(self._device.sn) or {}
        attrs: dict[str, Any] = {}
        if "lts" in row:
            attrs["last_reported"] = row["lts"]
            try:
                attrs["last_reported_iso"] = datetime.strptime(
                    row["lts"], "%Y-%m-%d %H:%M:%S"
                ).isoformat()
            except (TypeError, ValueError):
                pass
        return attrs


class ShinePlantPeriodEnergySensor(ShineEntity, SensorEntity):
    """Month-to-date / year-to-date plant energy.

    Computes from the coordinator's already-fetched history tables:
      - ``month``: sum of ``queryPlantEnergyMonthPerDay`` for the current month
      - ``year``:  current year's entry from ``queryPlantEnergyTotalPerYear``
        (more authoritative than summing ``queryPlantEnergyYearPerMonth`` —
        the two can disagree for older years by hundreds of kWh).
    Resets to zero at the start of each period (``total_increasing`` semantics).
    """

    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: ShineCoordinator, period: str) -> None:
        super().__init__(coordinator)
        self._period = period
        self._attr_name = "Month" if period == "month" else "Year"
        self._attr_unique_id = (
            f"{DOMAIN}:plant:{coordinator.plantid}:{period}_energy"
        )
        self._attr_device_info = self.plant_device_info

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        if data is None:
            return None
        if self._period == "month":
            total = 0.0
            for row in data.month_per_day:
                v = _coerce_number(row.get("val"))
                if isinstance(v, (int, float)):
                    total += float(v)
            return round(total, 2) if data.month_per_day else None
        # Year: prefer total_per_year for the current year.
        today_year = str(datetime.now().year)
        for row in data.total_per_year:
            ts = str(row.get("ts") or "")
            if ts.startswith(today_year):
                v = _coerce_number(row.get("val"))
                if isinstance(v, (int, float)):
                    return round(float(v), 2)
        # Fallback: sum year_per_month (less authoritative).
        total = 0.0
        for row in data.year_per_month:
            v = _coerce_number(row.get("val"))
            if isinstance(v, (int, float)):
                total += float(v)
        return round(total, 2) if data.year_per_month else None


class ShinePlantCurrentPowerSensor(ShineEntity, SensorEntity):
    """Plant-level instantaneous power.

    Reads the last non-zero sample from today's 5-minute power curve
    (``queryPlantActiveOuputPowerOneDay``). Falls back to the final sample
    regardless of value so the graph still has a point outside daylight.
    """

    _attr_name = "Current power"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_state_class = SensorStateClass.MEASUREMENT
    # The underlying ``queryPlantActiveOuputPowerOneDay`` samples are in kW,
    # but HA's native unit for power is W. We multiply in ``native_value``
    # and declare W here so the dashboard shows a plain "2,170 W" or the
    # user's preferred kW formatting depending on their locale/settings.
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: ShineCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}:plant:{coordinator.plantid}:current_power"
        self._attr_device_info = self.plant_device_info

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        # "Unknown" (None) is reserved for the brief window before the
        # coordinator has made any successful refresh. Any successful refresh
        # — even one where the cloud returned no samples for today — is
        # reported as 0 W so the gauge/history cards render something
        # numeric instead of erroring with "non-numeric".
        if data is None:
            return None
        if not data.power_curve:
            return 0
        # Walk back from the newest sample to find the most recent
        # non-zero reading. Values are in kW; convert to W.
        for sample in reversed(data.power_curve):
            v = _coerce_number(sample.get("val"))
            if isinstance(v, (int, float)) and v > 0:
                return round(float(v) * 1000)
        return 0

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        attrs: dict[str, Any] = {}
        if data and data.power_curve:
            last = data.power_curve[-1]
            attrs["last_sample_ts"] = last.get("ts")
            # Find the ts of the last *non-zero* sample too — that's the
            # "last seen producing" time which is what the user usually cares
            # about when the inverter is idle at night.
            for sample in reversed(data.power_curve):
                v = _coerce_number(sample.get("val"))
                if isinstance(v, (int, float)) and v > 0:
                    attrs["last_nonzero_ts"] = sample.get("ts")
                    attrs["last_nonzero_value"] = v
                    break
        if data and data.energy:
            first_row = next(iter(data.energy.values()))
            attrs["device_last_report"] = first_row.get("lts")
        return attrs


def _extract_field_value(payload: dict[str, Any], field_id: str) -> Any:
    """``queryDeviceRealLastData`` returns values in a handful of shapes depending on device.

    We try, in order:
    1. ``payload[field_id]`` (flat dict).
    2. ``payload[field_id]["val"]``.
    3. ``payload["pars"][*]`` — a list of ``{id, val}`` pairs some devices use.
    4. ``payload["dat"]`` as a list of ``{optional/id, val}`` objects.
    """
    if field_id in payload:
        v = payload[field_id]
        if isinstance(v, dict) and "val" in v:
            return v["val"]
        return v

    for key in ("pars", "parameter", "dat"):
        bucket = payload.get(key)
        if isinstance(bucket, list):
            for item in bucket:
                if not isinstance(item, dict):
                    continue
                ident = item.get("optional") or item.get("id") or item.get("key")
                if ident == field_id:
                    return item.get("val")
    return None


def _coerce_number(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return v
    try:
        s = str(v).strip()
        if not s:
            return None
        return float(s) if ("." in s or "e" in s or "E" in s) else int(s)
    except (TypeError, ValueError):
        return v

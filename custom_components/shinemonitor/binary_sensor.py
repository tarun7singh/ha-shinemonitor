"""Binary sensor platform for ShineMonitor."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import DeviceKey, ShineCoordinator
from .entity import ShineEntity

# A plant is "producing" when its last non-zero power sample is this recent
# relative to the cloud's last-heard-from timestamp. 10 min = two upload
# cycles, long enough to tolerate a missed poll, short enough to catch the
# dusk-handoff within ~one check.
PRODUCING_FRESHNESS = timedelta(minutes=10)


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
        new: list[BinarySensorEntity] = []

        for dev in data.devices:
            key = f"binary:{dev.sn}:online"
            if key in known_keys:
                continue
            known_keys.add(key)
            new.append(ShineDeviceOnline(coordinator, dev))

        plant_alarm_key = "binary:plant:has_alarm"
        if plant_alarm_key not in known_keys:
            known_keys.add(plant_alarm_key)
            new.append(ShinePlantAlarm(coordinator))

        plant_producing_key = "binary:plant:producing"
        if plant_producing_key not in known_keys:
            known_keys.add(plant_producing_key)
            new.append(ShinePlantProducing(coordinator))

        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class ShineDeviceOnline(ShineEntity, BinarySensorEntity):
    """Is the cloud currently receiving fresh data from this device?

    Backed by the API's ``comStatus`` flag — which is what shinemonitor.com's
    own dashboard uses to color its "online" chips. We intentionally don't
    surface the parallel ``status`` field as a separate entity because its
    semantics are inverted from what most users expect (``status=1`` means
    "has an alarm", not "is online"); it's only exposed as a raw attribute
    for diagnostics.
    """

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "Online"

    def __init__(self, coordinator: ShineCoordinator, device: DeviceKey) -> None:
        super().__init__(coordinator)
        self._device = device
        self._attr_unique_id = f"{DOMAIN}:{device.sn}:online"
        self._attr_device_info = self.device_info_for(device)

    def _current_device(self) -> DeviceKey | None:
        if self.coordinator.data is None:
            return None
        return next(
            (d for d in self.coordinator.data.devices if d.sn == self._device.sn),
            None,
        )

    @property
    def is_on(self) -> bool | None:
        dev = self._current_device()
        return None if dev is None else dev.com_status == 1

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        dev = self._current_device()
        if dev is None:
            return {}
        return {"raw_status": dev.status, "raw_com_status": dev.com_status}


class ShinePlantProducing(ShineEntity, BinarySensorEntity):
    """Is the plant generating power right now?

    True when *at least one* device's data link is up AND the most recent
    non-zero sample in today's 5-minute power curve is no more than
    :data:`PRODUCING_FRESHNESS` behind the cloud's ``lts`` (last-reported
    time). This avoids false positives at night — when the curve still has
    non-zero samples from earlier in the day — while tolerating brief cloud
    drops between inverter heartbeats.
    """

    _attr_device_class = BinarySensorDeviceClass.POWER
    _attr_name = "Producing"

    def __init__(self, coordinator: ShineCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}:plant:{coordinator.plantid}:producing"
        self._attr_device_info = self.plant_device_info

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if data is None:
            return None
        if not any(d.com_status == 1 for d in data.devices):
            return False
        if not data.power_curve or not data.energy:
            return False
        try:
            lts_str = next(iter(data.energy.values())).get("lts") or ""
            lts_dt = datetime.strptime(lts_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            return False
        latest_nonzero_dt: datetime | None = None
        for sample in data.power_curve:
            try:
                v = float(sample.get("val") or 0)
            except (TypeError, ValueError):
                continue
            if v <= 0:
                continue
            try:
                ts = datetime.strptime(sample.get("ts", ""), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            if latest_nonzero_dt is None or ts > latest_nonzero_dt:
                latest_nonzero_dt = ts
        if latest_nonzero_dt is None:
            return False
        return (lts_dt - latest_nonzero_dt) <= PRODUCING_FRESHNESS

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        data = self.coordinator.data
        if data is None or not data.energy:
            return {}
        first = next(iter(data.energy.values()))
        return {"last_report": first.get("lts")}


class ShinePlantAlarm(ShineEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_name = "Alarm"

    def __init__(self, coordinator: ShineCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{DOMAIN}:plant:{coordinator.plantid}:alarm"
        self._attr_device_info = self.plant_device_info

    @property
    def is_on(self) -> bool | None:
        if self.coordinator.data is None:
            return None
        return bool(self.coordinator.data.alarms)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data is None:
            return {}
        alarms = self.coordinator.data.alarms
        return {"count": len(alarms), "latest": alarms[0] if alarms else None}

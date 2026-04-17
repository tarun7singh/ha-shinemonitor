"""Base entity for ShineMonitor."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import DeviceKey, ShineCoordinator


class ShineEntity(CoordinatorEntity[ShineCoordinator]):
    """Entity tied to the ShineMonitor plant coordinator."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: ShineCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def plant_device_info(self) -> DeviceInfo:
        """Device grouping for plant-level entities."""
        plant = self.coordinator.data.plant_info if self.coordinator.data else {}
        return DeviceInfo(
            identifiers={(DOMAIN, f"plant:{self.coordinator.plantid}")},
            name=plant.get("name") or f"Plant {self.coordinator.plantid}",
            manufacturer=MANUFACTURER,
            model="Plant",
            configuration_url="https://shinemonitor.com/",
        )

    def device_info_for(self, dev: DeviceKey) -> DeviceInfo:
        """Device grouping for an inverter/device."""
        firmware: str | None = None
        for coll in self.coordinator.data.collectors if self.coordinator.data else []:
            if coll.get("pn") == dev.collector_pn:
                firmware = coll.get("fireware") or firmware
                break
        return DeviceInfo(
            identifiers={(DOMAIN, f"device:{dev.sn}")},
            via_device=(DOMAIN, f"plant:{self.coordinator.plantid}"),
            name=dev.sn,
            manufacturer=MANUFACTURER,
            model=f"devcode {dev.devcode}",
            serial_number=dev.sn,
            sw_version=firmware,
        )

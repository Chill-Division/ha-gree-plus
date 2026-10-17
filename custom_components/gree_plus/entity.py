"""Base entity for Gree+ units."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo, format_mac
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import GreeDeviceCoordinator


class GreeEntity(CoordinatorEntity[GreeDeviceCoordinator]):
    """Common device info and availability."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: GreeDeviceCoordinator, key: str) -> None:
        super().__init__(coordinator)
        dev = coordinator.device
        self._attr_unique_id = f"{dev.mac}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, dev.mac)},
            connections={(CONNECTION_NETWORK_MAC, format_mac(dev.mac))},
            manufacturer="Gree",
            model=f"Gree+ air conditioner ({dev.model_id})" if dev.model_id else "Gree+ air conditioner",
            name=dev.name,
            sw_version=dev.firmware or None,
            suggested_area=dev.room or None,
        )

    @property
    def available(self) -> bool:
        """Unavailable when polls fail or the cloud reports the unit offline."""
        return super().available and self.coordinator.online and bool(self.coordinator.data)

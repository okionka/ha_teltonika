"""Update platform for Teltonika Extended — firmware status and OTA update."""
from __future__ import annotations

import logging

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, KEY_FIRMWARE
from .coordinator import TeltonikaCoordinator

_LOGGER = logging.getLogger(__name__)


def _a(obj, *keys, default=None):
    for k in keys:
        if obj is None:
            return default
        obj = getattr(obj, k, None)
    return default if obj is None else obj


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TeltonikaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([FirmwareUpdateEntity(coordinator, entry)])


class FirmwareUpdateEntity(CoordinatorEntity[TeltonikaCoordinator], UpdateEntity):
    """Firmware update entity — shows installed/available version."""

    _attr_has_entity_name = True
    _attr_name = "Firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(self, coordinator: TeltonikaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_firmware_update"
        sys = coordinator.system_info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Teltonika",
            model=_a(sys, "static", "model"),
            sw_version=_a(sys, "static", "fw_version"),
            configuration_url=f"https://{coordinator.host}",
        )

    @property
    def installed_version(self) -> str | None:
        """Current installed firmware version."""
        sys = self.coordinator.system_info
        # Primary: from firmware status endpoint
        fw = self.coordinator.data.get(KEY_FIRMWARE)
        if fw and _a(fw, "current", "version"):
            return _a(fw, "current", "version")
        # Fallback: from system/device/status
        return _a(sys, "static", "fw_version")

    @property
    def latest_version(self) -> str | None:
        """Latest available firmware version (None = up to date / unknown)."""
        fw = self.coordinator.data.get(KEY_FIRMWARE)
        if fw is None:
            return self.installed_version  # unknown → show as up to date
        update_version = _a(fw, "update", "version")
        if not update_version or update_version == self.installed_version:
            return self.installed_version
        return update_version

    @property
    def release_summary(self) -> str | None:
        fw = self.coordinator.data.get(KEY_FIRMWARE)
        return _a(fw, "update", "release_notes")

    async def async_release_notes(self) -> str | None:
        fw = self.coordinator.data.get(KEY_FIRMWARE)
        return _a(fw, "update", "changelog") or _a(fw, "update", "release_notes")

    async def async_install(
        self, version: str | None, backup: bool, **kwargs
    ) -> None:
        """Start firmware installation."""
        _LOGGER.warning(
            "Starting firmware update on %s — router will reboot",
            self.coordinator.host,
        )
        try:
            ok = await self.coordinator.client.install_firmware_update()
            if not ok:
                _LOGGER.error("Firmware update request rejected by router")
        except Exception as err:
            _LOGGER.error("Firmware update failed: %s", err)

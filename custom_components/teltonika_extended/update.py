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
    async_add_entities([
        FirmwareUpdateEntity(coordinator, entry),
        ModemFirmwareUpdateEntity(coordinator, entry),
    ])


class _FirmwareBase(CoordinatorEntity[TeltonikaCoordinator], UpdateEntity):
    _attr_has_entity_name = True
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator: TeltonikaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        sys = coordinator.system_info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Teltonika",
            model=_a(sys, "static", "model"),
            sw_version=_a(sys, "static", "fw_version"),
            configuration_url=f"https://{coordinator.host}",
        )
        self._entry = entry

    def _installed_from_system(self) -> str | None:
        """Reliable fallback: firmware version from /system/device/status."""
        return _a(self.coordinator.system_info, "static", "fw_version")


class FirmwareUpdateEntity(_FirmwareBase):
    """Router firmware — shows RUTX_R_00.07.10.2 vs RUTX_R_00.07.22.3."""

    _attr_name = "Firmware"
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(self, coordinator: TeltonikaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_firmware_update"

    @property
    def installed_version(self) -> str | None:
        fw = self.coordinator.data.get(KEY_FIRMWARE)
        # Prefer firmware endpoint value, fall back to system_info
        return (
            _a(fw, "current", "fw_version")
            or _a(fw, "installed_version")
            or self._installed_from_system()
        )

    @property
    def latest_version(self) -> str | None:
        fw = self.coordinator.data.get(KEY_FIRMWARE)
        latest = _a(fw, "update", "fw_version") or _a(fw, "latest_version")
        if not latest:
            # No update info available → report as up to date
            return self.installed_version
        return latest

    @property
    def release_summary(self) -> str | None:
        fw = self.coordinator.data.get(KEY_FIRMWARE)
        return _a(fw, "update", "changelog")

    async def async_release_notes(self) -> str | None:
        fw = self.coordinator.data.get(KEY_FIRMWARE)
        return _a(fw, "update", "changelog") or _a(fw, "update", "fw_version")

    async def async_install(self, version: str | None, backup: bool, **kwargs) -> None:
        _LOGGER.warning(
            "Starting router firmware update on %s (current=%s, target=%s) — "
            "router will reboot",
            self.coordinator.host,
            self.installed_version,
            version or self.latest_version,
        )
        try:
            ok = await self.coordinator.client.install_firmware_update()
            if not ok:
                _LOGGER.error("Firmware update request rejected by router")
        except Exception as err:
            _LOGGER.error("Firmware update failed: %s", err)


class ModemFirmwareUpdateEntity(_FirmwareBase):
    """Internal modem firmware — shows modem firmware version separately."""

    _attr_name = "Modem firmware"
    _attr_supported_features = UpdateEntityFeature.RELEASE_NOTES

    def __init__(self, coordinator: TeltonikaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_modem_firmware_update"

    @property
    def installed_version(self) -> str | None:
        fw = self.coordinator.data.get(KEY_FIRMWARE)
        modem_fw = _a(fw, "current", "modem_fw")
        if modem_fw:
            return modem_fw
        # Fallback: modem firmware from system static info
        return _a(self.coordinator.system_info, "static", "release", "revision")

    @property
    def latest_version(self) -> str | None:
        fw = self.coordinator.data.get(KEY_FIRMWARE)
        modem_update = _a(fw, "update", "modem_update_available")
        if modem_update:
            return f"{self.installed_version} (update available)"
        return self.installed_version

    async def async_release_notes(self) -> str | None:
        return "Modem firmware update available — install via router firmware update."

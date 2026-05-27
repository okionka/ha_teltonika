"""Button platform for Teltonika Extended."""
from __future__ import annotations
import logging
import os
from datetime import datetime

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BACKUP_DIR, DOMAIN
from .coordinator import TeltonikaCoordinator

_LOGGER = logging.getLogger(__name__)


def _a(obj, *keys, default=None):
    for k in keys:
        if obj is None:
            return default
        obj = getattr(obj, k, None)
    return default if obj is None else obj


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator: TeltonikaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        RebootButton(coordinator, entry),
        BackupButton(coordinator, entry, hass),
    ])


class _ButtonBase(CoordinatorEntity[TeltonikaCoordinator], ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry):
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


class RebootButton(_ButtonBase):
    _attr_name = "Reboot"
    _attr_icon = "mdi:restart"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_reboot"

    async def async_press(self) -> None:
        _LOGGER.warning("Rebooting %s", self.coordinator.host)
        try:
            await self.coordinator.client.reboot_device()
        except Exception as err:
            _LOGGER.error("Reboot failed: %s", err)


class BackupButton(_ButtonBase):
    """Downloads router config to /config/teltonika_backups/ on press."""
    _attr_name = "Backup configuration"
    _attr_icon = "mdi:content-save-all"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, hass):
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_backup"
        self._hass = hass

    async def async_press(self) -> None:
        coordinator = self.coordinator
        try:
            data = await coordinator.client.export_config()
        except Exception as err:
            _LOGGER.error("Backup failed: %s", err)
            self._hass.components.persistent_notification.async_create(
                f"Backup fehlgeschlagen: {err}",
                title="Teltonika Backup",
                notification_id="teltonika_backup_error",
            )
            return

        backup_dir = self._hass.config.path(BACKUP_DIR)
        os.makedirs(backup_dir, exist_ok=True)

        sys_info = coordinator.system_info
        hostname = _a(sys_info, "static", "hostname") or "router"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{hostname}_{timestamp}.tar.gz"
        filepath = os.path.join(backup_dir, filename)

        with open(filepath, "wb") as f:
            f.write(data)

        _LOGGER.info("Backup saved: %s (%d bytes)", filepath, len(data))
        self._hass.components.persistent_notification.async_create(
            f"Konfiguration gespeichert:\n"
            f"`/config/{BACKUP_DIR}/{filename}`\n\n"
            f"Größe: {len(data):,} Bytes",
            title="Teltonika Backup erfolgreich",
            notification_id="teltonika_backup_ok",
        )

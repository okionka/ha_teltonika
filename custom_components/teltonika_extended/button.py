"""Button platform for Teltonika Extended."""
from __future__ import annotations
import logging
import os
from datetime import datetime

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.components.persistent_notification import async_create as notify_create
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


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TeltonikaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        RebootButton(coordinator, entry, hass),
        BackupButton(coordinator, entry, hass),
    ])


class _ButtonBase(CoordinatorEntity[TeltonikaCoordinator], ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: TeltonikaCoordinator,
                 entry: ConfigEntry, hass: HomeAssistant) -> None:
        super().__init__(coordinator)
        self._hass = hass
        self._entry = entry
        sys = coordinator.system_info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Teltonika",
            model=_a(sys, "static", "model"),
            sw_version=_a(sys, "static", "fw_version"),
            configuration_url=f"https://{coordinator.host}",
        )


class RebootButton(_ButtonBase):
    _attr_name = "Reboot"
    _attr_icon = "mdi:restart"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, hass):
        super().__init__(coordinator, entry, hass)
        self._attr_unique_id = f"{entry.entry_id}_reboot"

    async def async_press(self) -> None:
        _LOGGER.warning("Rebooting %s", self.coordinator.host)
        try:
            await self.coordinator.client.reboot_device()
        except Exception as err:
            _LOGGER.error("Reboot failed: %s", err)


class BackupButton(_ButtonBase):
    """Saves router configuration to /config/teltonika_backups/ on press."""
    _attr_name = "Backup configuration"
    _attr_icon = "mdi:content-save-all"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, hass):
        super().__init__(coordinator, entry, hass)
        self._attr_unique_id = f"{entry.entry_id}_backup"

    async def async_press(self) -> None:
        coordinator = self.coordinator
        hass = self._hass

        try:
            data = await coordinator.client.export_config()
        except Exception as err:
            _LOGGER.error("Backup failed: %s", err)
            notify_create(
                hass,
                f"Backup fehlgeschlagen:\n{err}",
                title="Teltonika Backup",
                notification_id="teltonika_backup_error",
            )
            return

        backup_dir = hass.config.path(BACKUP_DIR)
        os.makedirs(backup_dir, exist_ok=True)

        hostname = _a(coordinator.system_info, "static", "hostname") or "router"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{hostname}_{timestamp}.tar.gz"
        filepath = os.path.join(backup_dir, filename)

        def _write() -> None:
            with open(filepath, "wb") as f:
                f.write(data)

        await hass.async_add_executor_job(_write)

        _LOGGER.info("Backup saved: %s (%d bytes)", filepath, len(data))
        notify_create(
            hass,
            f"Konfiguration gespeichert unter:\n"
            f"`/config/{BACKUP_DIR}/{filename}`\n\n"
            f"Größe: {len(data):,} Bytes",
            title="Teltonika Backup erfolgreich",
            notification_id="teltonika_backup_ok",
        )

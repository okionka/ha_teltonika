"""Button platform for Teltonika Extended."""
from __future__ import annotations
import logging
import os
from datetime import datetime

from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.components.persistent_notification import (
    async_create as notify_create,
    async_dismiss as notify_dismiss,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BACKUP_DIR, DOMAIN
from .coordinator import TeltonikaCoordinator

_LOGGER = logging.getLogger(__name__)
_BACKUP_STORE_VERSION = 1


def _a(obj, *keys, default=None):
    for k in keys:
        if obj is None:
            return default
        obj = getattr(obj, k, None)
    return default if obj is None else obj


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TeltonikaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        RebootButton(coordinator, entry, hass),
        BackupButton(coordinator, entry, hass),
    ])


class _ButtonBase(CoordinatorEntity[TeltonikaCoordinator], ButtonEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry, hass):
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
    """
    Backup flow:
      1. POST /backup/actions/generate  {data:{}}
      2. GET  /backup/errors/status     (poll)
      3. GET  /backup/actions/download
    """
    _attr_name = "Backup configuration"
    _attr_icon = "mdi:content-save-all"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, hass):
        super().__init__(coordinator, entry, hass)
        self._attr_unique_id = f"{entry.entry_id}_backup"
        self._store = Store(
            hass, _BACKUP_STORE_VERSION, f"{DOMAIN}_{entry.entry_id}_backup_status"
        )

    async def async_press(self) -> None:
        hass = self._hass
        coordinator = self.coordinator

        # Step 1: Generate
        notify_create(
            hass,
            "Schritt 1/3: Backup wird auf dem Router erstellt…",
            title="Teltonika Backup",
            notification_id="teltonika_backup_progress",
        )

        try:
            # Step 1: Generate backup on router
            await coordinator.client.backup.generate()
        except Exception as err:
            notify_dismiss(hass, "teltonika_backup_progress")
            notify_create(
                hass,
                f"Backup fehlgeschlagen bei 'generate':\n`{err}`",
                title="Teltonika Backup Fehler",
                notification_id="teltonika_backup_error",
            )
            await self._save_status(hass, "error", str(err))
            return

        # Step 2: Poll status
        notify_create(
            hass,
            "Schritt 2/3: Warte auf Backup-Fertigstellung…",
            title="Teltonika Backup",
            notification_id="teltonika_backup_progress",
        )

        try:
            await coordinator.client.backup.wait_until_ready()
        except (TimeoutError, RuntimeError) as err:
            notify_dismiss(hass, "teltonika_backup_progress")
            notify_create(
                hass,
                f"Backup-Erstellung fehlgeschlagen:\n`{err}`",
                title="Teltonika Backup Fehler",
                notification_id="teltonika_backup_error",
            )
            await self._save_status(hass, "error", str(err))
            return

        # Step 3: Download
        notify_create(
            hass,
            "Schritt 3/3: Backup wird heruntergeladen…",
            title="Teltonika Backup",
            notification_id="teltonika_backup_progress",
        )

        try:
            data = await coordinator.client.backup.download()
        except Exception as err:
            notify_dismiss(hass, "teltonika_backup_progress")
            notify_create(
                hass,
                f"Backup-Download fehlgeschlagen:\n`{err}`",
                title="Teltonika Backup Fehler",
                notification_id="teltonika_backup_error",
            )
            await self._save_status(hass, "error", str(err))
            return

        # Save to disk
        backup_dir = hass.config.path(BACKUP_DIR)
        os.makedirs(backup_dir, exist_ok=True)

        hostname = _a(coordinator.system_info, "static", "hostname") or "router"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{hostname}_{timestamp}.tar.gz"
        filepath = os.path.join(backup_dir, filename)

        await hass.async_add_executor_job(
            lambda: open(filepath, "wb").write(data)
        )

        _LOGGER.info("Backup saved: %s (%d bytes)", filepath, len(data))
        await self._save_status(hass, "success", filename, len(data), timestamp)

        notify_dismiss(hass, "teltonika_backup_progress")
        notify_create(
            hass,
            f"✅ Konfiguration gespeichert:\n"
            f"`/config/{BACKUP_DIR}/{filename}`\n\n"
            f"Größe: {len(data):,} Bytes\n\n"
            f"Wiederherstellen:\n"
            f"Service `teltonika_extended.restore_config`\n"
            f"mit `file_path: {BACKUP_DIR}/{filename}`",
            title="Teltonika Backup erfolgreich",
            notification_id="teltonika_backup_ok",
        )

        # Refresh coordinator so the status sensor updates immediately
        coordinator.async_set_updated_data({
            **coordinator.data,
            "backup_status": await self._load_status(hass),
        })

    async def _save_status(
        self, hass, status: str, info: str = "",
        size: int = 0, timestamp: str = ""
    ) -> None:
        await self._store.async_save({
            "status": status,
            "info": info,
            "size": size,
            "timestamp": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    async def _load_status(self, hass) -> dict:
        return await self._store.async_load() or {}

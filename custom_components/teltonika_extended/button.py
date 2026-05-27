"""Button platform for Teltonika Extended."""
from __future__ import annotations
import hashlib
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

from .const import BACKUP_DIR, CONF_MAX_BACKUPS, DEFAULT_MAX_BACKUPS, DOMAIN
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
        RestoreSelectedButton(coordinator, entry, hass),
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


# ---------------------------------------------------------------------------
# Reboot
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Backup
# ---------------------------------------------------------------------------

class BackupButton(_ButtonBase):
    """
    1. POST /backup/actions/generate  {"data": {}}  → sha256+md5
    2. POST /backup/actions/download  {"data": {"sha256": "..."}}  → bytes
    → save + verify + cleanup old backups
    """
    _attr_name = "Backup configuration"
    _attr_icon = "mdi:content-save-all"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, hass):
        super().__init__(coordinator, entry, hass)
        self._attr_unique_id = f"{entry.entry_id}_backup"
        self._store = Store(
            hass, _BACKUP_STORE_VERSION,
            f"{DOMAIN}_{entry.entry_id}_backup_status",
        )

    def _max_backups(self) -> int:
        return self._entry.options.get(CONF_MAX_BACKUPS, DEFAULT_MAX_BACKUPS)

    def _cleanup_old_backups(self, backup_dir: str, hostname: str) -> list[str]:
        """Delete oldest backups beyond max_backups limit. Returns deleted filenames."""
        max_b = self._max_backups()
        all_files = sorted(
            [f for f in os.listdir(backup_dir)
             if f.endswith(".tar.gz") and f.startswith(hostname)],
        )  # oldest first (alphabetical = chronological with timestamp filenames)
        deleted = []
        while len(all_files) > max_b:
            oldest = all_files.pop(0)
            try:
                os.remove(os.path.join(backup_dir, oldest))
                _LOGGER.info("Deleted old backup: %s", oldest)
                deleted.append(oldest)
            except OSError as e:
                _LOGGER.warning("Could not delete %s: %s", oldest, e)
        return deleted

    async def async_press(self) -> None:
        hass = self._hass
        coordinator = self.coordinator

        # Step 1: Generate
        notify_create(
            hass,
            "Schritt 1/2: Backup wird auf dem Router erstellt…",
            title="Teltonika Backup",
            notification_id="teltonika_backup_progress",
        )
        try:
            result = await coordinator.client.backup.generate()
        except Exception as err:
            notify_dismiss(hass, "teltonika_backup_progress")
            notify_create(
                hass,
                f"Backup-Erstellung fehlgeschlagen:\n`{err}`",
                title="Teltonika Backup Fehler",
                notification_id="teltonika_backup_error",
            )
            await self._save_status(hass, "error", str(err))
            return

        # Step 2: Download
        notify_create(
            hass,
            "Schritt 2/2: Backup wird heruntergeladen…",
            title="Teltonika Backup",
            notification_id="teltonika_backup_progress",
        )
        try:
            data = await coordinator.client.backup.download(sha256=result.sha256)
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

        # SHA256 verify
        checksum_ok = True
        if result.sha256:
            actual = hashlib.sha256(data).hexdigest()
            checksum_ok = actual == result.sha256
            if not checksum_ok:
                _LOGGER.warning("SHA256 mismatch: expected %s got %s",
                                result.sha256, actual)

        # Save to disk
        backup_dir = hass.config.path(BACKUP_DIR)
        os.makedirs(backup_dir, exist_ok=True)
        hostname = _a(coordinator.system_info, "static", "hostname") or "router"
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{hostname}_{timestamp}.tar.gz"
        filepath = os.path.join(backup_dir, filename)

        def _write_and_cleanup():
            with open(filepath, "wb") as f:
                f.write(data)
            return self._cleanup_old_backups(backup_dir, hostname)

        deleted = await hass.async_add_executor_job(_write_and_cleanup)

        await self._save_status(hass, "success", filename, len(data), timestamp)
        notify_dismiss(hass, "teltonika_backup_progress")

        checksum_line = (
            "✅ SHA256 verifiziert" if checksum_ok and result.sha256
            else ("⚠️ SHA256 Prüfung fehlgeschlagen" if not checksum_ok else "")
        )
        deleted_line = (
            f"\n🗑️ Gelöscht (Limit {self._max_backups()}): "
            + ", ".join(deleted) if deleted else ""
        )
        notify_create(
            hass,
            f"✅ Konfiguration gespeichert:\n"
            f"`/config/{BACKUP_DIR}/{filename}`\n\n"
            f"Größe: {len(data):,} Bytes\n"
            f"{checksum_line}"
            f"{deleted_line}\n\n"
            f"Wiederherstellen: **Backup auswählen** → **Restore selected backup**",
            title="Teltonika Backup erfolgreich",
            notification_id="teltonika_backup_ok",
        )
        coordinator.async_set_updated_data({
            **coordinator.data,
            "backup_status": await self._load_status(hass),
        })

    async def _save_status(self, hass, status, info="", size=0, timestamp=""):
        await self._store.async_save({
            "status": status, "info": info, "size": size,
            "timestamp": timestamp or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        })

    async def _load_status(self, hass):
        return await self._store.async_load() or {}


# ---------------------------------------------------------------------------
# Restore selected backup
# ---------------------------------------------------------------------------

class RestoreSelectedButton(_ButtonBase):
    """
    Reads the selected backup from the BackupFileSelect entity,
    then runs upload → validate → shows metadata + confirms.
    User calls restore_config_apply to complete.
    """
    _attr_name = "Restore selected backup"
    _attr_icon = "mdi:backup-restore"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, hass):
        super().__init__(coordinator, entry, hass)
        self._attr_unique_id = f"{entry.entry_id}_restore_selected"

    def _find_select_entity(self):
        """Find the BackupFileSelect entity for this entry."""
        from .select import BackupFileSelect
        for entity in self._hass.data.get("entity_registry_entries", {}).values() \
                if False else []:
            pass
        # Walk registered entities via entity registry
        try:
            from homeassistant.helpers import entity_registry as er
            registry = er.async_get(self._hass)
            for entry_id, entity in registry.entities.items():
                if (entity.platform == DOMAIN and
                        entity.config_entry_id == self._entry.entry_id and
                        "backup_select" in entity.unique_id):
                    state = self._hass.states.get(entity.entity_id)
                    if state:
                        return state.state
        except Exception:
            pass
        return None

    async def async_press(self) -> None:
        hass = self._hass
        coordinator = self.coordinator

        # Get selected backup filename from entity registry
        selected = self._find_select_entity()
        if not selected or selected in ("", "— kein Backup vorhanden —", "unavailable"):
            notify_create(
                hass,
                "Kein Backup ausgewählt.\n\n"
                "Wähle unter **Backup to restore** eine Datei aus.",
                title="Teltonika Restore",
                notification_id="teltonika_restore_nofile",
            )
            return

        filepath = hass.config.path(BACKUP_DIR, selected)
        if not os.path.exists(filepath):
            notify_create(
                hass,
                f"Datei nicht gefunden:\n`{filepath}`",
                title="Teltonika Restore Fehler",
                notification_id="teltonika_restore_error",
            )
            return

        notify_create(
            hass,
            f"Schritt 1/2: **{selected}** wird hochgeladen und validiert…",
            title="Teltonika Restore",
            notification_id="teltonika_restore_progress",
        )

        data = await hass.async_add_executor_job(
            lambda: open(filepath, "rb").read()
        )

        try:
            meta = await coordinator.client.restore_upload_validate(data)
        except Exception as err:
            notify_dismiss(hass, "teltonika_restore_progress")
            notify_create(
                hass,
                f"Upload/Validierung fehlgeschlagen:\n`{err}`",
                title="Teltonika Restore Fehler",
                notification_id="teltonika_restore_error",
            )
            return

        notify_dismiss(hass, "teltonika_restore_progress")
        valid_str = (
            "✅ Gültig" if meta.valid
            else ("❌ Ungültig — Restore abgebrochen" if meta.valid is False
                  else "⚠️ Validierung nicht eindeutig")
        )
        if meta.valid is False:
            notify_create(
                hass,
                f"Backup **{selected}** ist ungültig.\n\n{meta.summary()}",
                title="Teltonika Restore — Ungültig",
                notification_id="teltonika_restore_invalid",
            )
            return

        notify_create(
            hass,
            f"**Backup bereit zum Wiederherstellen:**\n\n"
            f"📁 `{selected}`\n\n"
            f"{meta.summary()}\n\n"
            f"**Status:** {valid_str}\n\n"
            f"---\n"
            f"**Zum Fortfahren:**\n"
            f"Service `teltonika_extended.restore_config_apply` aufrufen.\n\n"
            f"⚠️ Der Router wird nach dem Restore neu gestartet.",
            title="Teltonika Restore — Bestätigung",
            notification_id="teltonika_restore_confirm",
        )

"""Select platform — backup file selector for restore workflow."""
from __future__ import annotations
import logging
import os
from datetime import datetime

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
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
    hass: HomeAssistant, entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TeltonikaCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BackupFileSelect(coordinator, entry, hass)])


class BackupFileSelect(CoordinatorEntity[TeltonikaCoordinator], SelectEntity):
    """
    Lists all available backup files for the restore workflow.
    Selection persists across HA restarts.
    Refreshes automatically when coordinator updates (e.g. after new backup).
    """
    _attr_has_entity_name = True
    _attr_name = "Backup to restore"
    _attr_icon = "mdi:restore"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, hass):
        super().__init__(coordinator)
        self._hass = hass
        self._entry = entry
        self._selected: str | None = None
        self._options: list[str] = []
        self._attr_unique_id = f"{entry.entry_id}_backup_select"
        sys = coordinator.system_info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Teltonika",
            model=_a(sys, "static", "model"),
            sw_version=_a(sys, "static", "fw_version"),
            configuration_url=f"https://{coordinator.host}",
        )

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        await self._hass.async_add_executor_job(self._scan_backup_dir)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Refresh file list on every coordinator poll (picks up new backups)."""
        self.hass.async_add_executor_job(self._scan_and_write)

    def _scan_backup_dir(self) -> None:
        """Scan backup directory and refresh options (blocking — run in executor)."""
        backup_dir = self._hass.config.path(BACKUP_DIR)
        if not os.path.isdir(backup_dir):
            self._options = []
            return

        files = sorted(
            [f for f in os.listdir(backup_dir) if f.endswith(".tar.gz")],
            reverse=True,  # newest first
        )
        self._options = files
        # Auto-select newest if nothing selected or selection no longer exists
        if self._selected not in self._options:
            self._selected = files[0] if files else None

    def _scan_and_write(self) -> None:
        self._scan_backup_dir()
        # Schedule state write back on the event loop
        self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)

    @property
    def options(self) -> list[str]:
        return self._options if self._options else ["— kein Backup vorhanden —"]

    @property
    def current_option(self) -> str | None:
        return self._selected or ("— kein Backup vorhanden —" if not self._options else None)

    async def async_select_option(self, option: str) -> None:
        if option == "— kein Backup vorhanden —":
            return
        self._selected = option
        self.async_write_ha_state()
        _LOGGER.debug("Backup selected for restore: %s", option)

    def get_selected_path(self) -> str | None:
        """Return full path of selected backup, or None."""
        if not self._selected or self._selected == "— kein Backup vorhanden —":
            return None
        return self._hass.config.path(BACKUP_DIR, self._selected)

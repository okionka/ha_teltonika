"""Teltonika Extended integration."""
from __future__ import annotations

import logging
import os
from datetime import datetime

import voluptuous as vol
from teltasync import Teltasync

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .const import (
    BACKUP_DIR,
    CONF_VERIFY_SSL,
    DOMAIN,
    SERVICE_BACKUP,
    SERVICE_RESTORE,
)
from .coordinator import TeltonikaCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON, Platform.UPDATE]

RESTORE_SCHEMA = vol.Schema({
    vol.Required("file_path"): cv.string,
})


def _normalize_url(host: str) -> str:
    """Return full RutOS API base URL, e.g. https://192.168.7.1/api"""
    host = host.strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    if not host.endswith("/api"):
        host = f"{host}/api"
    return host


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    base_url = _normalize_url(entry.data[CONF_HOST])
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, False)

    session = async_get_clientsession(hass, verify_ssl=verify_ssl)
    client = Teltasync(
        base_url=base_url,
        username=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        session=session,
        verify_ssl=verify_ssl,
    )
    coordinator = TeltonikaCoordinator(hass, client, base_url)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Register services (once, domain-wide)
    if not hass.services.has_service(DOMAIN, SERVICE_BACKUP):
        _register_services(hass)

    return True


def _register_services(hass: HomeAssistant) -> None:
    """Register backup and restore services."""

    async def _handle_backup(call: ServiceCall) -> None:
        """Download router config and save to /config/teltonika_backups/."""
        entry_id = call.data.get("entry_id")
        # Pick first coordinator if no entry_id given
        coordinators = list(hass.data.get(DOMAIN, {}).values())
        if not coordinators:
            _LOGGER.error("No Teltonika integration configured")
            return
        coordinator: TeltonikaCoordinator = (
            hass.data[DOMAIN].get(entry_id, coordinators[0])
            if entry_id else coordinators[0]
        )

        try:
            data = await coordinator.client.export_config()
        except Exception as err:
            _LOGGER.error("Backup failed: %s", err)
            hass.components.persistent_notification.async_create(
                f"Teltonika backup failed: {err}",
                title="Teltonika Backup",
                notification_id="teltonika_backup_error",
            )
            return

        backup_dir = hass.config.path(BACKUP_DIR)
        os.makedirs(backup_dir, exist_ok=True)

        sys_info = coordinator.system_info
        hostname = (
            getattr(getattr(sys_info, "static", None), "hostname", None)
            or "router"
        )
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"{hostname}_{timestamp}.tar.gz"
        filepath = os.path.join(backup_dir, filename)

        with open(filepath, "wb") as f:
            f.write(data)

        _LOGGER.info("Router config backed up to %s (%d bytes)", filepath, len(data))
        hass.components.persistent_notification.async_create(
            f"Router configuration saved to:\n`/config/{BACKUP_DIR}/{filename}`\n\n"
            f"Size: {len(data):,} bytes",
            title="Teltonika Backup successful",
            notification_id="teltonika_backup_ok",
        )

    async def _handle_restore(call: ServiceCall) -> None:
        """Upload a config backup file to the router."""
        file_path: str = call.data["file_path"]
        entry_id = call.data.get("entry_id")
        coordinators = list(hass.data.get(DOMAIN, {}).values())
        if not coordinators:
            _LOGGER.error("No Teltonika integration configured")
            return
        coordinator: TeltonikaCoordinator = (
            hass.data[DOMAIN].get(entry_id, coordinators[0])
            if entry_id else coordinators[0]
        )

        # Resolve relative paths against /config
        if not os.path.isabs(file_path):
            file_path = hass.config.path(file_path)

        if not os.path.exists(file_path):
            _LOGGER.error("Restore file not found: %s", file_path)
            hass.components.persistent_notification.async_create(
                f"File not found: `{file_path}`",
                title="Teltonika Restore failed",
                notification_id="teltonika_restore_error",
            )
            return

        with open(file_path, "rb") as f:
            data = f.read()

        try:
            ok = await coordinator.client.import_config(data)
        except Exception as err:
            _LOGGER.error("Restore failed: %s", err)
            hass.components.persistent_notification.async_create(
                f"Restore failed: {err}",
                title="Teltonika Restore failed",
                notification_id="teltonika_restore_error",
            )
            return

        if ok:
            _LOGGER.info("Router config restored from %s", file_path)
            hass.components.persistent_notification.async_create(
                f"Configuration restored from:\n`{file_path}`\n\n"
                "The router is rebooting to apply settings.",
                title="Teltonika Restore successful",
                notification_id="teltonika_restore_ok",
            )
        else:
            _LOGGER.error("Router rejected restore from %s", file_path)

    hass.services.async_register(
        DOMAIN, SERVICE_BACKUP, _handle_backup,
        schema=vol.Schema({vol.Optional("entry_id"): cv.string}),
    )
    hass.services.async_register(
        DOMAIN, SERVICE_RESTORE, _handle_restore,
        schema=vol.Schema({
            vol.Required("file_path"): cv.string,
            vol.Optional("entry_id"): cv.string,
        }),
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        # Remove services when last entry is unloaded
        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_BACKUP)
            hass.services.async_remove(DOMAIN, SERVICE_RESTORE)
    return ok

"""Teltonika Extended integration."""
from __future__ import annotations
import logging
import os

import voluptuous as vol
from teltasync import Teltasync

from homeassistant.components.persistent_notification import async_create as notify_create
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .const import BACKUP_DIR, CONF_VERIFY_SSL, DOMAIN, SERVICE_BACKUP, SERVICE_RESTORE
from .coordinator import TeltonikaCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON, Platform.UPDATE]


def _normalize_url(host: str) -> str:
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

    if not hass.services.has_service(DOMAIN, SERVICE_RESTORE):
        _register_services(hass)

    return True


def _register_services(hass: HomeAssistant) -> None:

    def _get_coordinator(call: ServiceCall) -> TeltonikaCoordinator:
        entry_id = call.data.get("entry_id")
        coords = list(hass.data.get(DOMAIN, {}).values())
        if not coords:
            raise RuntimeError("No Teltonika integration configured")
        return hass.data[DOMAIN].get(entry_id, coords[0]) if entry_id else coords[0]

    async def _handle_restore_upload(call: ServiceCall) -> None:
        """
        Restore flow step 1+2: upload + validate.
        Shows metadata notification and instructs user to call restore_apply.
        """
        coordinator = _get_coordinator(call)
        file_path: str = call.data["file_path"]

        if not os.path.isabs(file_path):
            file_path = hass.config.path(file_path)

        if not os.path.exists(file_path):
            notify_create(
                hass,
                f"Datei nicht gefunden: `{file_path}`",
                title="Teltonika Restore — Fehler",
                notification_id="teltonika_restore_error",
            )
            return

        data = await hass.async_add_executor_job(
            lambda: open(file_path, "rb").read()
        )

        notify_create(
            hass,
            f"Backup wird hochgeladen und validiert…\n`{file_path}`",
            title="Teltonika Restore",
            notification_id="teltonika_restore_progress",
        )

        try:
            meta = await coordinator.client.restore_upload_validate(data)
        except Exception as err:
            _LOGGER.error("Restore upload/validate failed: %s", err)
            notify_create(
                hass,
                f"Upload/Validierung fehlgeschlagen:\n`{err}`",
                title="Teltonika Restore — Fehler",
                notification_id="teltonika_restore_error",
            )
            return

        summary = meta.summary()
        valid_str = "✅ Gültig" if meta.valid else ("❌ Ungültig" if meta.valid is False else "⚠️ Unbekannt")

        notify_create(
            hass,
            f"**Backup-Metadaten:**\n{summary}\n\n"
            f"**Status:** {valid_str}\n\n"
            f"Zum Wiederherstellen:\n"
            f"Service `teltonika_extended.restore_config_apply` aufrufen.\n\n"
            f"⚠️ Der Router wird nach dem Restore neu gestartet.",
            title="Teltonika Restore — Bestätigung erforderlich",
            notification_id="teltonika_restore_confirm",
        )

    async def _handle_restore_apply(call: ServiceCall) -> None:
        """Restore flow step 3: apply validated backup (router reboots)."""
        coordinator = _get_coordinator(call)

        notify_create(
            hass,
            "Restore wird angewendet — Router startet neu…",
            title="Teltonika Restore",
            notification_id="teltonika_restore_progress",
        )

        try:
            ok = await coordinator.client.restore_apply()
        except Exception as err:
            _LOGGER.error("Restore apply failed: %s", err)
            notify_create(
                hass,
                f"Restore fehlgeschlagen:\n`{err}`",
                title="Teltonika Restore — Fehler",
                notification_id="teltonika_restore_error",
            )
            return

        if ok:
            _LOGGER.info("Restore applied — router rebooting")
            notify_create(
                hass,
                "Router-Konfiguration wurde wiederhergestellt.\n"
                "Der Router startet neu. Bitte 1–2 Minuten warten.",
                title="Teltonika Restore erfolgreich",
                notification_id="teltonika_restore_ok",
            )

    hass.services.async_register(
        DOMAIN, SERVICE_RESTORE,
        _handle_restore_upload,
        schema=vol.Schema({
            vol.Required("file_path"): cv.string,
            vol.Optional("entry_id"): cv.string,
        }),
    )
    hass.services.async_register(
        DOMAIN, "restore_config_apply",
        _handle_restore_apply,
        schema=vol.Schema({vol.Optional("entry_id"): cv.string}),
    )


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_RESTORE)
            hass.services.async_remove(DOMAIN, "restore_config_apply")
    return ok

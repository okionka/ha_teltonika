"""Teltonika Extended integration."""
from __future__ import annotations
import logging
import os

import voluptuous as vol
from teltasync import Teltasync

from homeassistant.components.frontend import async_register_built_in_panel, async_remove_panel
from homeassistant.components.http import HomeAssistantView
from homeassistant.components.persistent_notification import async_create as notify_create
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import homeassistant.helpers.config_validation as cv

from .const import (
    BACKUP_DIR,
    CONF_EXTERNAL_ICON, CONF_EXTERNAL_PANEL, CONF_EXTERNAL_PROXY, CONF_EXTERNAL_TITLE, CONF_EXTERNAL_URL,
    CONF_SIDEBAR_PANEL, CONF_VERIFY_SSL,
    DEFAULT_EXTERNAL_ICON, DEFAULT_EXTERNAL_PANEL, DEFAULT_EXTERNAL_PROXY,
    DEFAULT_SIDEBAR_PANEL, DOMAIN, SERVICE_BACKUP, SERVICE_RESTORE,
)
from .coordinator import TeltonikaCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR, Platform.SWITCH, Platform.BUTTON, Platform.UPDATE, Platform.SELECT]


def _normalize_url(host: str) -> str:
    host = host.strip().rstrip("/")
    if not host.startswith(("http://", "https://")):
        host = f"https://{host}"
    if not host.endswith("/api"):
        host = f"{host}/api"
    return host


def _panel_url_path(entry: ConfigEntry) -> str:
    """Unique sidebar URL path per config entry."""
    return f"teltonika_{entry.entry_id[:8].lower()}"


def _register_proxy(hass: HomeAssistant) -> None:
    """Register the reverse proxy HTTP view (only once per HA instance)."""
    from .proxy import TeltonikaProxyView
    _KEY = f"{DOMAIN}_proxy_registered"
    if _KEY not in hass.data:          # use 'in' — hass.data is a dict, hasattr never works
        try:
            hass.http.register_view(TeltonikaProxyView())
            hass.data[_KEY] = True
            _LOGGER.info("Teltonika reverse proxy registered at /api/teltonika_proxy/")
        except Exception as err:
            _LOGGER.warning("Could not register proxy view: %s", err)


def _register_panel(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Add router WebUI as sidebar panel via reverse proxy."""
    url_path    = _panel_url_path(entry)
    proxy_url   = f"/api/teltonika_proxy/{entry.entry_id}/"
    title       = entry.title  # e.g. 'Teltonika RUTX50'

    try:
        async_register_built_in_panel(
            hass,
            component_name="iframe",
            sidebar_title=title,
            sidebar_icon="mdi:router-wireless",
            frontend_url_path=url_path,
            config={"url": proxy_url},  # same-origin proxy URL → no CORS/iframe blocks
            require_admin=False,
        )
        _LOGGER.info("Registered sidebar panel '%s' → %s", title, proxy_url)
    except Exception as err:
        _LOGGER.warning("Could not register sidebar panel: %s", err)


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

    # Register reverse proxy view (once per HA instance)
    _register_proxy(hass)

    # Sync external URL to coordinator (needed by proxy)
    coordinator.external_url = entry.options.get(CONF_EXTERNAL_URL, "").strip()

    # Register sidebar panel (router proxy) if enabled
    if entry.options.get(CONF_SIDEBAR_PANEL, DEFAULT_SIDEBAR_PANEL):
        _register_panel(hass, entry)

    # Register external URL panel if configured
    if entry.options.get(CONF_EXTERNAL_PANEL, DEFAULT_EXTERNAL_PANEL):
        _register_external_panel(hass, entry)

    # Re-register / remove panel when options change
    entry.async_on_unload(
        entry.add_update_listener(_async_options_updated)
    )

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


def _register_external_panel(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Add external URL as sidebar panel.

    By default uses a direct iframe (browser can reach public URL).
    Enable 'Via Proxy' option if X-Frame-Options blocks the direct iframe.
    """
    url = entry.options.get(CONF_EXTERNAL_URL, "").strip()
    if not url:
        _LOGGER.debug("External panel enabled but no URL configured")
        return

    title      = entry.options.get(CONF_EXTERNAL_TITLE, "").strip() or url
    icon       = entry.options.get(CONF_EXTERNAL_ICON, DEFAULT_EXTERNAL_ICON).strip()
    use_proxy  = entry.options.get(CONF_EXTERNAL_PROXY, DEFAULT_EXTERNAL_PROXY)
    panel_path = _panel_url_path(entry) + "_ext"

    if use_proxy:
        # Route through HA proxy → strips X-Frame-Options
        ext_id    = entry.entry_id + "_ext"
        panel_url = f"/api/teltonika_proxy/{ext_id}/"
        _LOGGER.info("External panel '%s' → via proxy → %s", title, url)
    else:
        # Direct iframe — works when browser can reach the URL directly
        panel_url = url
        _LOGGER.info("External panel '%s' → direct → %s", title, url)

    try:
        async_register_built_in_panel(
            hass,
            component_name="iframe",
            sidebar_title=title,
            sidebar_icon=icon,
            frontend_url_path=panel_path,
            config={"url": panel_url},
            require_admin=False,
        )
    except Exception as err:
        _LOGGER.warning("Could not register external panel: %s", err)


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update — toggle sidebar panels."""
    # Router proxy panel
    if entry.options.get(CONF_SIDEBAR_PANEL, DEFAULT_SIDEBAR_PANEL):
        _register_panel(hass, entry)
    else:
        try:
            async_remove_panel(hass, _panel_url_path(entry))
        except Exception:
            pass

    # Sync external URL to coordinator
    domain_data = hass.data.get(DOMAIN, {})
    for coord in domain_data.values():
        if hasattr(coord, "external_url"):
            coord.external_url = entry.options.get(CONF_EXTERNAL_URL, "").strip()

    # External URL panel
    ext_path = _panel_url_path(entry) + "_ext"
    if entry.options.get(CONF_EXTERNAL_PANEL, DEFAULT_EXTERNAL_PANEL):
        _register_external_panel(hass, entry)
    else:
        try:
            async_remove_panel(hass, ext_path)
        except Exception:
            pass


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        if not hass.data.get(DOMAIN):
            hass.services.async_remove(DOMAIN, SERVICE_RESTORE)
            hass.services.async_remove(DOMAIN, "restore_config_apply")
        # Remove sidebar panels
        for suffix in ("", "_ext"):
            try:
                async_remove_panel(hass, _panel_url_path(entry) + suffix)
            except Exception:
                pass
    return ok

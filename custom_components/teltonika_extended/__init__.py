"""Teltonika Extended integration."""
from __future__ import annotations

from teltasync import Teltasync

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_VERIFY_SSL, DOMAIN
from .coordinator import TeltonikaCoordinator

PLATFORMS = [Platform.SENSOR, Platform.SWITCH]


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
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return ok

"""DataUpdateCoordinator for Teltonika Extended."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from teltasync import Teltasync
from teltasync.modems import ModemStatusFull

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    KEY_BACKUP_STATUS,
    KEY_INTERFACES,
    KEY_DATA_USAGE,
    KEY_FIRMWARE,
    KEY_GPS,
    KEY_MOBILE,
    KEY_SYSTEM,
    KEY_WAN,
    KEY_WIRELESS,
)

_LOGGER = logging.getLogger(__name__)


def _base_url(host: str) -> str:
    """Strip /api suffix and return clean router base URL for browser links."""
    url = host.rstrip("/")
    if url.endswith("/api"):
        url = url[:-4]
    return url


class TeltonikaCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches all data from the router every 30 s."""

    def __init__(self, hass: HomeAssistant, client: Teltasync, host: str) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.client = client
        self.host = host
        self.system_info = None
        self._backup_store = Store(hass, 1, f"{DOMAIN}_{host}_backup_status")
        self._backup_status: dict = {}

    async def _async_setup(self) -> None:
        # Load persisted backup status
        self._backup_status = await self._backup_store.async_load() or {}
        try:
            self.system_info = await self.client.get_system_info()
        except Exception as err:
            raise ConfigEntryAuthFailed(f"Cannot connect to {self.host}: {err}") from err

    async def _async_update_data(self) -> dict[str, Any]:
        data: dict[str, Any] = {}

        # System
        try:
            data[KEY_SYSTEM] = await self.client.get_system_info()
            self.system_info = data[KEY_SYSTEM]
        except Exception as err:
            _LOGGER.warning("System info: %s", err)
            data[KEY_SYSTEM] = self.system_info

        # Mobile modems
        try:
            data[KEY_MOBILE] = await self.client.get_modem_status()
        except Exception as err:
            _LOGGER.warning("Modem status: %s", err)
            data[KEY_MOBILE] = []

        # GPS
        try:
            data[KEY_GPS] = await self.client.get_gps_status()
            if data[KEY_GPS] is not None:
                _LOGGER.debug(
                    "GPS response: fix=%s lat=%s lon=%s",
                    getattr(data[KEY_GPS], "fix", "?"),
                    getattr(data[KEY_GPS], "latitude", "?"),
                    getattr(data[KEY_GPS], "longitude", "?"),
                )
        except Exception as err:
            _LOGGER.warning("GPS unavailable: %s", err)
            data[KEY_GPS] = None

        # WAN
        try:
            data[KEY_WAN] = await self.client.get_wan_status()
        except Exception as err:
            _LOGGER.debug("WAN: %s", err)
            data[KEY_WAN] = None

        # Wireless
        try:
            data[KEY_WIRELESS] = await self.client.get_wireless_interfaces()
        except Exception as err:
            _LOGGER.debug("Wireless: %s", err)
            data[KEY_WIRELESS] = []

        # All network interfaces (for traffic stats + WAN IP fallback)
        try:
            resp = await self.client.network.get_interfaces()
            data[KEY_INTERFACES] = resp.data or [] if resp.success else []
        except Exception as err:
            _LOGGER.debug("Interfaces: %s", err)
            data[KEY_INTERFACES] = []

        # Firmware (polled every cycle — update check is cached by router)
        try:
            data[KEY_FIRMWARE] = await self.client.get_firmware_status()
        except Exception as err:
            _LOGGER.debug("Firmware status: %s", err)
            data[KEY_FIRMWARE] = None

        # Data usage
        usage_list = []
        for modem in (data[KEY_MOBILE] or []):
            if isinstance(modem, ModemStatusFull) and modem.id:
                try:
                    usage = await self.client.get_modem_data_usage(modem.id)
                    if usage:
                        usage_list.append(usage)
                except Exception as err:
                    _LOGGER.debug("Data usage modem %s: %s", modem.id, err)
        data[KEY_DATA_USAGE] = usage_list or None

        # Backup status from persistent storage (updated by BackupButton)
        data[KEY_BACKUP_STATUS] = self._backup_status

        return data

"""Switch platform for Teltonika Extended — SIM switch and WiFi toggle."""
from __future__ import annotations

import logging
from typing import Any

from teltasync.modems import ModemStatusFull
from teltasync.wireless import WirelessInterface

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, KEY_MOBILE, KEY_WIRELESS
from .coordinator import TeltonikaCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TeltonikaCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SwitchEntity] = []

    # SIM switch — one per online modem with ≥ 2 SIMs
    for modem in (coordinator.data.get(KEY_MOBILE) or []):
        if isinstance(modem, ModemStatusFull) and (modem.sim_count or 0) >= 2:
            entities.append(SimSwitch(coordinator, entry, modem.id))

    # WiFi switches — one per wireless interface
    for iface in (coordinator.data.get(KEY_WIRELESS) or []):
        if iface.id:
            entities.append(WifiSwitch(coordinator, entry, iface))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

def _a(obj: Any, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if obj is None:
            return default
        obj = getattr(obj, k, None) if not isinstance(obj, dict) else obj.get(k)
    return default if obj is None else obj


class _SwitchBase(CoordinatorEntity[TeltonikaCoordinator], SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator: TeltonikaCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        sys = coordinator.system_info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Teltonika",
            model=_a(sys, "static", "model") if sys else None,
            sw_version=_a(sys, "static", "fw_version") if sys else None,
            configuration_url=f"https://{coordinator.host}",
        )
        self._entry = entry


# ---------------------------------------------------------------------------
# SIM switch
# ---------------------------------------------------------------------------

class SimSwitch(_SwitchBase):
    """
    Switches between SIM1 and SIM2.
    ON  = SIM1 active
    OFF = SIM2 active
    """

    _attr_icon = "mdi:sim-outline"
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self,
        coordinator: TeltonikaCoordinator,
        entry: ConfigEntry,
        modem_id: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._modem_id = modem_id
        self._attr_unique_id = f"{entry.entry_id}_sim_switch_{modem_id}"
        self._attr_name = "SIM card"

    def _get_modem(self) -> ModemStatusFull | None:
        for m in (self.coordinator.data.get(KEY_MOBILE) or []):
            if isinstance(m, ModemStatusFull) and m.id == self._modem_id:
                return m
        return None

    @property
    def is_on(self) -> bool | None:
        """True = SIM1 active, False = SIM2 active."""
        modem = self._get_modem()
        if modem is None or modem.active_sim is None:
            return None
        return int(modem.active_sim) == 1

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        modem = self._get_modem()
        if modem is None:
            return {}
        return {
            "active_sim": modem.active_sim,
            "sim_count": modem.sim_count,
            "modem_id": self._modem_id,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Switch to SIM1."""
        try:
            await self.coordinator.client.switch_sim(self._modem_id)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("SIM switch failed: %s", err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Switch to SIM2 (toggle from current)."""
        try:
            await self.coordinator.client.switch_sim(self._modem_id)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("SIM switch failed: %s", err)


# ---------------------------------------------------------------------------
# WiFi switch
# ---------------------------------------------------------------------------

class WifiSwitch(_SwitchBase):
    """Enables or disables a wireless interface."""

    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(
        self,
        coordinator: TeltonikaCoordinator,
        entry: ConfigEntry,
        iface: WirelessInterface,
    ) -> None:
        super().__init__(coordinator, entry)
        self._iface_id = iface.id
        band_label = f" ({iface.band.upper()})" if iface.band else ""
        self._attr_unique_id = f"{entry.entry_id}_wifi_{iface.id}"
        self._attr_name = f"WiFi{band_label}"
        self._attr_icon = "mdi:wifi" if (iface.band or "").startswith("2") else "mdi:wifi"

    def _get_iface(self) -> WirelessInterface | None:
        for i in (self.coordinator.data.get(KEY_WIRELESS) or []):
            if i.id == self._iface_id:
                return i
        return None

    @property
    def is_on(self) -> bool | None:
        iface = self._get_iface()
        return iface.enabled if iface else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        iface = self._get_iface()
        if iface is None:
            return {}
        return {
            "ssid": iface.ssid,
            "band": iface.band,
            "channel": iface.channel,
            "clients": iface.clients,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.client.set_wifi_enabled(self._iface_id, True)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("WiFi enable failed: %s", err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            await self.coordinator.client.set_wifi_enabled(self._iface_id, False)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("WiFi disable failed: %s", err)

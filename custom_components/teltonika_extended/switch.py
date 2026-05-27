"""Switch platform for Teltonika Extended — SIM toggle + WiFi per interface."""
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
    entities: list[SwitchEntity] = []

    # SIM switch — one per modem (no sim_count check needed)
    for modem in (coordinator.data.get(KEY_MOBILE) or []):
        if isinstance(modem, ModemStatusFull) and modem.id:
            entities.append(SimSwitch(coordinator, entry, modem.id))
            break  # One SIM switch per device is sufficient

    # WiFi switches — from discovered wireless interfaces
    wireless = coordinator.data.get(KEY_WIRELESS) or []
    _LOGGER.debug("WiFi interfaces discovered: %s", [(i.id, i.band) for i in wireless])

    if wireless:
        for iface in wireless:
            if iface.id:
                entities.append(WifiSwitch(coordinator, entry, iface))
    else:
        # Fallback: create static WiFi switches for common interface names
        _LOGGER.warning(
            "No wireless interfaces from API — creating static WiFi switches. "
            "Update in HACS if this persists."
        )
        from teltasync.wireless import WirelessInterface as WI
        for iface_id, band in [("wlan0", "2g"), ("wlan1", "5g")]:
            static_iface = WI(id=iface_id, band=band, enabled=None)
            entities.append(WifiSwitch(coordinator, entry, static_iface))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class _SwitchBase(CoordinatorEntity[TeltonikaCoordinator], SwitchEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, entry):
        super().__init__(coordinator)
        sys = coordinator.system_info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Teltonika",
            model=_a(sys, "static", "model"),
            sw_version=_a(sys, "static", "fw_version"),
            configuration_url=f"https://{coordinator.host}",
        )
        self._entry = entry


# ---------------------------------------------------------------------------
# SIM switch  (ON = SIM1 active, OFF = SIM2 active)
# ---------------------------------------------------------------------------

class SimSwitch(_SwitchBase):
    """
    Toggles between SIM1 and SIM2.
      ON  → SIM1 active
      OFF → SIM2 active
    Uses set_sim() for direct selection (with toggle fallback).
    """
    _attr_icon = "mdi:sim-outline"
    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, modem_id: str):
        super().__init__(coordinator, entry)
        self._modem_id = modem_id
        self._attr_unique_id = f"{entry.entry_id}_sim_switch_{modem_id}"
        self._attr_name = "SIM card (SIM1 = ON)"

    def _get_modem(self) -> ModemStatusFull | None:
        for m in (self.coordinator.data.get(KEY_MOBILE) or []):
            if isinstance(m, ModemStatusFull) and m.id == self._modem_id:
                return m
        return None

    @property
    def is_on(self) -> bool | None:
        modem = self._get_modem()
        if modem is None:
            return None
        sim = getattr(modem, "active_sim", None)
        if sim is None:
            return None
        try:
            return int(sim) == 1
        except (ValueError, TypeError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        modem = self._get_modem()
        if not modem:
            return {}
        return {
            "active_sim": getattr(modem, "active_sim", None),
            "modem_id": self._modem_id,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Activate SIM1."""
        try:
            await self.coordinator.client.set_sim(self._modem_id, 1)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("SIM1 select failed: %s", err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Activate SIM2."""
        try:
            await self.coordinator.client.set_sim(self._modem_id, 2)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("SIM2 select failed: %s", err)


# ---------------------------------------------------------------------------
# WiFi switch
# ---------------------------------------------------------------------------

class WifiSwitch(_SwitchBase):
    """Enable/disable a wireless interface."""
    _attr_device_class = SwitchDeviceClass.SWITCH

    def __init__(self, coordinator, entry, iface: WirelessInterface):
        super().__init__(coordinator, entry)
        self._iface_id = iface.id or ""
        band = getattr(iface, "band_label", None) or (
            "5 GHz" if "5" in (iface.band or "") else "2.4 GHz"
        )
        self._attr_unique_id = f"{entry.entry_id}_wifi_{self._iface_id}"
        self._attr_name = f"WiFi {band}"
        self._attr_icon = "mdi:wifi" if "2" in band else "mdi:wifi-arrow-up-down"

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
        if not iface:
            return {"interface": self._iface_id}
        return {
            "interface": self._iface_id,
            "ssid": iface.ssid,
            "band": iface.band,
            "channel": iface.channel,
            "clients": iface.clients,
        }

    async def async_turn_on(self, **kwargs: Any) -> None:
        try:
            ok = await self.coordinator.client.set_wifi_enabled(self._iface_id, True)
            _LOGGER.debug("WiFi %s enable: %s", self._iface_id, ok)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("WiFi %s enable failed: %s", self._iface_id, err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        try:
            ok = await self.coordinator.client.set_wifi_enabled(self._iface_id, False)
            _LOGGER.debug("WiFi %s disable: %s", self._iface_id, ok)
            await self.coordinator.async_request_refresh()
        except Exception as err:
            _LOGGER.error("WiFi %s disable failed: %s", self._iface_id, err)

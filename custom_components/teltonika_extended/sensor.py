"""Sensor platform for Teltonika Extended."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from teltasync.modems import ModemStatusFull

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfInformation,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, KEY_DATA_USAGE, KEY_GPS, KEY_MOBILE, KEY_SYSTEM, KEY_WAN
from .coordinator import TeltonikaCoordinator


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------

def _fmt_mac(raw: str | None) -> str | None:
    """2097270DABD8  →  20:97:27:0D:AB:D8"""
    if not raw:
        return None
    clean = raw.replace(":", "").replace("-", "").upper()
    if len(clean) != 12:
        return raw
    return ":".join(clean[i:i+2] for i in range(0, 12, 2))


def _fmt_signal(raw: int | None, unit: str) -> str | None:
    """Return formatted signal string with unit, e.g. '-62 dBm'"""
    if raw is None:
        return None
    return f"{raw} {unit}"


# ---------------------------------------------------------------------------
# Description dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class TeltonikaSensorDesc(SensorEntityDescription):
    value_fn: Callable[[Any], Any] = lambda _: None
    group: str = ""
    sim: int | None = None


def _a(obj: Any, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if obj is None:
            return default
        obj = getattr(obj, k, None) if not isinstance(obj, dict) else obj.get(k)
    return default if obj is None else obj


# ---------------------------------------------------------------------------
# System sensors
# ---------------------------------------------------------------------------

SYSTEM_SENSORS: tuple[TeltonikaSensorDesc, ...] = (
    TeltonikaSensorDesc(
        key="hostname", name="Hostname",
        icon="mdi:router-network", group="system",
        value_fn=lambda d: _a(d, "static", "hostname"),
    ),
    TeltonikaSensorDesc(
        key="firmware", name="Firmware version",
        icon="mdi:chip", group="system",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "static", "fw_version"),
    ),
    TeltonikaSensorDesc(
        key="model", name="Device model",
        icon="mdi:router-wireless", group="system",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "static", "model"),
    ),
    TeltonikaSensorDesc(
        key="device_name", name="Device name",
        icon="mdi:router", group="system",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "static", "device_name"),
    ),
    TeltonikaSensorDesc(
        key="serial", name="Serial number",
        icon="mdi:barcode", group="system",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "mnf_info", "serial"),
    ),
    TeltonikaSensorDesc(
        key="mac", name="LAN MAC address",
        icon="mdi:ethernet", group="system",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _fmt_mac(_a(d, "mnf_info", "mac")),
    ),
    TeltonikaSensorDesc(
        key="lan_ip", name="LAN IP address",
        icon="mdi:ip-network-outline", group="system",
        value_fn=lambda d: _a(d, "board", "network", "lan", "default_ip"),
    ),
    TeltonikaSensorDesc(
        key="fw_build_date", name="Firmware build date",
        icon="mdi:calendar-clock", group="system",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "static", "fw_build_date"),
    ),
    TeltonikaSensorDesc(
        key="kernel_version", name="Kernel version",
        icon="mdi:linux", group="system",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "static", "kernel"),
    ),
)

# ---------------------------------------------------------------------------
# WAN sensors
# ---------------------------------------------------------------------------

WAN_SENSORS: tuple[TeltonikaSensorDesc, ...] = (
    TeltonikaSensorDesc(
        key="wan_ip", name="WAN IP address",
        icon="mdi:ip-network", group="wan",
        value_fn=lambda d: _a(d, "ip_address"),
    ),
    TeltonikaSensorDesc(
        key="wan_type", name="WAN type",
        icon="mdi:network-outline", group="wan",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "wan_type"),
    ),
    TeltonikaSensorDesc(
        key="wan_iface", name="WAN interface",
        icon="mdi:ethernet", group="wan",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "interface"),
    ),
)

# ---------------------------------------------------------------------------
# Mobile / modem sensors
# ---------------------------------------------------------------------------

MOBILE_SENSORS: tuple[TeltonikaSensorDesc, ...] = (
    TeltonikaSensorDesc(
        key="rssi", name="Signal strength (RSSI)",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0, group="mobile",
        value_fn=lambda d: _a(d, "rssi"),
    ),
    TeltonikaSensorDesc(
        key="rsrp", name="Reference signal power (RSRP)",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement="dBm",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0, group="mobile",
        value_fn=lambda d: _a(d, "rsrp"),
    ),
    TeltonikaSensorDesc(
        key="rsrq", name="Signal quality (RSRQ)",
        icon="mdi:signal", native_unit_of_measurement="dB",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0, group="mobile",
        value_fn=lambda d: _a(d, "rsrq"),
    ),
    TeltonikaSensorDesc(
        key="sinr", name="Signal/Noise ratio (SINR)",
        icon="mdi:signal-variant", native_unit_of_measurement="dB",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0, group="mobile",
        value_fn=lambda d: _a(d, "sinr"),
    ),
    TeltonikaSensorDesc(
        key="temperature", name="Modem temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1, group="mobile",
        value_fn=lambda d: _a(d, "temperature"),
    ),
    TeltonikaSensorDesc(
        key="operator", name="GSM operator",
        icon="mdi:antenna", group="mobile",
        value_fn=lambda d: _a(d, "operator"),
    ),
    TeltonikaSensorDesc(
        key="ntype", name="Network type",
        icon="mdi:network", group="mobile",
        value_fn=lambda d: _a(d, "ntype") or _a(d, "conntype"),
    ),
    TeltonikaSensorDesc(
        key="band", name="Frequency band",
        icon="mdi:sine-wave", group="mobile",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "band"),
    ),
    TeltonikaSensorDesc(
        key="data_conn_state", name="Connection state",
        icon="mdi:connection", group="mobile",
        value_fn=lambda d: _a(d, "data_conn_state"),
    ),
    TeltonikaSensorDesc(
        key="operator_state", name="Network registration",
        icon="mdi:sim", group="mobile",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "operator_state"),
    ),
    TeltonikaSensorDesc(
        key="active_sim", name="Active SIM",
        icon="mdi:sim", group="mobile",
        value_fn=lambda d: _a(d, "active_sim"),
    ),
    TeltonikaSensorDesc(
        key="simstate", name="SIM state",
        icon="mdi:sim-alert", group="mobile",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "simstate"),
    ),
    TeltonikaSensorDesc(
        key="imsi", name="IMSI",
        icon="mdi:sim", group="mobile",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "imsi"),
    ),
    TeltonikaSensorDesc(
        key="iccid", name="ICCID",
        icon="mdi:sim-outline", group="mobile",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "iccid"),
    ),
    TeltonikaSensorDesc(
        key="imei", name="Modem IMEI",
        icon="mdi:identifier", group="mobile",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "imei"),
    ),
    TeltonikaSensorDesc(
        key="cellid", name="Cell ID",
        icon="mdi:cell-phone-wireless", group="mobile",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "cellid"),
    ),
    TeltonikaSensorDesc(
        key="mobile_stage", name="Mobile stage",
        icon="mdi:progress-check", group="mobile",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "mobile_stage_description"),
    ),
)

# ---------------------------------------------------------------------------
# GPS sensors
# ---------------------------------------------------------------------------

GPS_SENSORS: tuple[TeltonikaSensorDesc, ...] = (
    TeltonikaSensorDesc(
        key="fix", name="GPS fix",
        icon="mdi:crosshairs", group="gps",
        value_fn=lambda d: _a(d, "fix_status") or ("Fix" if _a(d, "fix") else "No fix"),
    ),
    TeltonikaSensorDesc(
        key="latitude", name="GPS latitude",
        icon="mdi:latitude", native_unit_of_measurement="°",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=6, group="gps",
        value_fn=lambda d: _a(d, "latitude"),
    ),
    TeltonikaSensorDesc(
        key="longitude", name="GPS longitude",
        icon="mdi:longitude", native_unit_of_measurement="°",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=6, group="gps",
        value_fn=lambda d: _a(d, "longitude"),
    ),
    TeltonikaSensorDesc(
        key="altitude", name="GPS altitude",
        icon="mdi:altimeter", native_unit_of_measurement="m",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1, group="gps",
        value_fn=lambda d: _a(d, "altitude"),
    ),
    TeltonikaSensorDesc(
        key="speed", name="GPS speed",
        device_class=SensorDeviceClass.SPEED,
        native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1, group="gps",
        value_fn=lambda d: _a(d, "speed"),
    ),
    TeltonikaSensorDesc(
        key="satellites", name="GPS satellites",
        icon="mdi:satellite-variant",
        state_class=SensorStateClass.MEASUREMENT, group="gps",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "num_satellites"),
    ),
    TeltonikaSensorDesc(
        key="accuracy", name="GPS accuracy (HDOP)",
        icon="mdi:crosshairs-gps", native_unit_of_measurement="m",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2, group="gps",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "accuracy"),
    ),
    TeltonikaSensorDesc(
        key="datetime", name="GPS datetime",
        icon="mdi:clock-time-four-outline", group="gps",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _a(d, "datetime") or _a(d, "date"),
    ),
    # Raw debug sensor — shows full GPS API response as JSON string
    TeltonikaSensorDesc(
        key="gps_raw", name="GPS raw response",
        icon="mdi:code-json", group="gps",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: str(d.model_dump(exclude_none=True)) if d else None,
    ),
)

# ---------------------------------------------------------------------------
# Data usage
# ---------------------------------------------------------------------------

def _usage_sensors(sim: int) -> tuple[TeltonikaSensorDesc, ...]:
    lbl = f"SIM{sim}"
    mb  = UnitOfInformation.MEGABYTES
    ti  = SensorStateClass.TOTAL_INCREASING

    def _s(key, label, attr):
        icon = "mdi:download" if key.startswith("rx") else "mdi:upload"
        return TeltonikaSensorDesc(
            key=f"sim{sim}_{key}", name=f"{lbl} {label}",
            icon=icon, native_unit_of_measurement=mb,
            state_class=ti, group="usage", sim=sim,
            value_fn=lambda d, a=attr: _a(d, a),
        )

    return (
        _s("rx_today",      "received today",       "rx_today"),
        _s("tx_today",      "sent today",            "tx_today"),
        _s("rx_24h",        "received last 24 h",   "rx_last_24h"),
        _s("tx_24h",        "sent last 24 h",        "tx_last_24h"),
        _s("rx_week",       "received this week",   "rx_week"),
        _s("tx_week",       "sent this week",        "tx_week"),
        _s("rx_last_7d",    "received last 7 days", "rx_last_7d"),
        _s("tx_last_7d",    "sent last 7 days",      "tx_last_7d"),
        _s("rx_month",      "received this month",  "rx_month"),
        _s("tx_month",      "sent this month",       "tx_month"),
        _s("rx_last_30d",   "received last 30 days","rx_last_30d"),
        _s("tx_last_30d",   "sent last 30 days",     "tx_last_30d"),
        _s("rx_last_month", "received last month",  "rx_last_month"),
        _s("tx_last_month", "sent last month",       "tx_last_month"),
        _s("rx_last_week",  "received last week",   "rx_last_week"),
        _s("tx_last_week",  "sent last week",        "tx_last_week"),
    )

ALL_USAGE = _usage_sensors(1) + _usage_sensors(2)


# ---------------------------------------------------------------------------
# async_setup_entry
# ---------------------------------------------------------------------------

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: TeltonikaCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []

    for desc in SYSTEM_SENSORS:
        entities.append(_SystemSensor(coordinator, entry, desc))

    if coordinator.data.get(KEY_WAN) is not None:
        for desc in WAN_SENSORS:
            entities.append(_WanSensor(coordinator, entry, desc))

    modems = coordinator.data.get(KEY_MOBILE) or []
    for idx, modem in enumerate(modems):
        if isinstance(modem, ModemStatusFull):
            for desc in MOBILE_SENSORS:
                entities.append(_ModemSensor(coordinator, entry, desc, idx))

    # GPS sensors are always added when the endpoint is reachable,
    # even without a satellite fix (values will be None until fix acquired)
    gps_data = coordinator.data.get(KEY_GPS)
    if gps_data is not None:
        for desc in GPS_SENSORS:
            entities.append(_GpsSensor(coordinator, entry, desc))

    usage_list = coordinator.data.get(KEY_DATA_USAGE) or []
    for modem_usage in usage_list:
        for desc in ALL_USAGE:
            sim_data = getattr(modem_usage, f"sim{desc.sim}", None)
            if sim_data is not None:
                entities.append(_UsageSensor(coordinator, entry, desc, modem_usage.modem_id))

    async_add_entities(entities)


# ---------------------------------------------------------------------------
# Base + concrete entity classes
# ---------------------------------------------------------------------------

class _Base(CoordinatorEntity[TeltonikaCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: TeltonikaSensorDesc

    def __init__(self, coordinator, entry, description):
        super().__init__(coordinator)
        self.entity_description = description
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_{description.key}"
        sys = coordinator.system_info
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer="Teltonika",
            model=_a(sys, "static", "model") if sys else None,
            sw_version=_a(sys, "static", "fw_version") if sys else None,
            configuration_url=f"https://{coordinator.host}",
        )


class _SystemSensor(_Base):
    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data.get(KEY_SYSTEM))


class _WanSensor(_Base):
    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data.get(KEY_WAN))


class _ModemSensor(_Base):
    def __init__(self, coordinator, entry, description, idx: int):
        super().__init__(coordinator, entry, description)
        self._idx = idx
        self._attr_unique_id = f"{entry.entry_id}_modem{idx}_{description.key}"
        if idx > 0:
            self._attr_name = f"Modem {idx + 1} {description.name}"

    @property
    def native_value(self):
        modems = self.coordinator.data.get(KEY_MOBILE) or []
        return self.entity_description.value_fn(modems[self._idx]) if self._idx < len(modems) else None


class _GpsSensor(_Base):
    @property
    def native_value(self):
        return self.entity_description.value_fn(self.coordinator.data.get(KEY_GPS))


class _UsageSensor(_Base):
    def __init__(self, coordinator, entry, description, modem_id: str):
        super().__init__(coordinator, entry, description)
        self._modem_id = modem_id
        self._attr_unique_id = f"{entry.entry_id}_{modem_id}_{description.key}"

    @property
    def native_value(self):
        for u in (self.coordinator.data.get(KEY_DATA_USAGE) or []):
            if u.modem_id == self._modem_id:
                sim_data = getattr(u, f"sim{self.entity_description.sim}", None)
                return self.entity_description.value_fn(sim_data) if sim_data else None
        return None

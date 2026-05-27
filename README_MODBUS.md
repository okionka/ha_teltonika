# Teltonika RUTX50 — Modbus TCP Reference

This document describes the **alternative Modbus TCP integration** for Teltonika routers.  
It serves as a reference alongside the native [`ha_teltonika`](./) integration.

> 📖 **Official Modbus documentation:**  
> [wiki.teltonika-networks.com/view/RUTX50_Monitoring_via_Modbus](https://wiki.teltonika-networks.com/view/RUTX50_Monitoring_via_Modbus)
>
> 📖 **RutOS API reference (REST):**  
> [developers.teltonika-networks.com](https://developers.teltonika-networks.com)

---

## When to use Modbus vs. the native integration

| Feature | Modbus YAML | Native integration (`ha_teltonika`) |
|---|---|---|
| Protocol | Modbus TCP (port 502) | RutOS REST API (HTTPS) |
| Configuration | Manual YAML | UI config flow |
| System uptime | ✅ Register 1 | ❌ Not in REST API |
| GPS data | ✅ Registers 143–183 | ✅ `/api/gps/status` |
| WAN/LAN IP | ✅ uint32 + template | ✅ Formatted natively |
| Data usage SIM1/2 | ✅ Full history | ✅ Full history |
| Signal quality (RSRP/RSRQ/SINR) | ❌ Only RSSI | ✅ Full LTE/5G metrics |
| SIM switch | ✅ Register 205 | ✅ Switch entity |
| WiFi on/off | ✅ Registers 203/390/391 | ✅ Switch entity per interface |
| Router reboot | ✅ Register 206 (commented) | ✅ Button entity |
| Firmware update | ❌ | ✅ Update entity |
| Backup / Restore | ❌ | ✅ Services |
| Can run in parallel | ✅ | ✅ Different protocols |

**Recommendation:** Run both integrations in parallel to get the full feature set.  
Modbus adds **system uptime** that the REST API does not expose.

---

## Prerequisites

### 1. Enable Modbus on the router

In the RUTX50 WebUI:  
**Services → Modbus → Modbus TCP Server**

| Setting | Value |
|---|---|
| Enable | ✓ |
| Port | `502` |
| Allow remote access | Only if connecting from outside LAN |

### 2. Enable Home Assistant Modbus integration

Add to `configuration.yaml`:
```yaml
homeassistant:
  packages: !include_dir_named packages/
```

Place `teltonika.yaml` in `/config/packages/`.

---

## Register map

All registers use **slave ID 1**. Register addresses are 0-based (Modbus address).

### System registers (read-only)

| Register address | Count | Data type | Sensor name | Unit | Notes |
|---|---|---|---|---|---|
| 1 | 2 | uint32 | System uptime | s | Seconds since boot |
| 3 | 2 | int32 | Mobile signal strength | dBm | RSSI |
| 5 | 2 | int32 | System temperature | — | ÷10 = °C, use `scale: 0.1` |
| 7 | 16 | string | System hostname | — | ASCII |
| 23 | 16 | string | GSM operator name | — | ASCII |
| 39 | 16 | string | Serial number | — | ASCII |
| 55 | 16 | string | LAN MAC address | — | ASCII, no separators |
| 71 | 16 | string | Device name | — | ASCII |
| 87 | 16 | string | Active SIM card | — | ASCII |
| 103 | 16 | string | Network registration info | — | ASCII |
| 119 | 16 | string | Network type | — | ASCII, e.g. `5G-NSA` |

### WAN / LAN (read-only)

| Register address | Count | Data type | Sensor name | Notes |
|---|---|---|---|---|
| 139 | 2 | uint32 | WAN IP address | Raw 32-bit integer → use template sensor |
| 394 | 2 | uint32 | LAN IP | Raw 32-bit integer → use template sensor |

**IP address conversion:**  
The router stores IPv4 addresses as 32-bit unsigned integers.  
Example: `178040762` → `10.156.175.186`

```
octet1 = ip // 16777216 % 256   →  10
octet2 = ip //    65536 % 256   → 156
octet3 = ip //      256 % 256   → 175
octet4 = ip             % 256   → 186
```

⚠️ Jinja2 does **not** support `>>` bitshift — use integer division as shown above.

### GPS (read-only)

| Register address | Count | Data type | Sensor name | Unit |
|---|---|---|---|---|
| 143 | 2 | float32 | GPS latitude | ° |
| 145 | 2 | float32 | GPS longitude | ° |
| 147 | 16 | string | GPS fix time | UTC datetime |
| 163 | 16 | string | GPS date and time | Local datetime |
| 179 | 2 | float32 | GPS speed | — |
| 181 | 2 | uint32 | GPS satellite count | — |
| 183 | 2 | float32 | GPS accuracy | HDOP |

### Mobile data — SIM1 (read-only)

| Register address | Data type | Sensor | Unit |
|---|---|---|---|
| 185 | uint32 | SIM1 received today | MB |
| 187 | uint32 | SIM1 sent today | MB |
| 189 | uint32 | SIM1 received this week | MB |
| 191 | uint32 | SIM1 sent this week | MB |
| 193 | uint32 | SIM1 received this month | MB |
| 195 | uint32 | SIM1 sent this month | MB |
| 197 | uint32 | SIM1 received last 24h | MB |
| 199 | uint32 | SIM1 sent last 24h | MB |
| 292 | uint32 | SIM1 received last 7 days | MB |
| 294 | uint32 | SIM1 sent last 7 days | MB |
| 296 | uint32 | SIM1 received last 30 days | MB |
| 298 | uint32 | SIM1 sent last 30 days | MB |
| 487 | uint32 | SIM1 received last month | MB |
| 491 | uint32 | SIM1 sent last month | MB |
| 503 | uint32 | SIM1 received last week | MB |
| 507 | uint32 | SIM1 sent last week | MB |

### Mobile data — SIM2 (read-only)

| Register address | Data type | Sensor | Unit |
|---|---|---|---|
| 300 | uint32 | SIM2 received today | MB |
| 302 | uint32 | SIM2 sent today | MB |
| 304 | uint32 | SIM2 received this week | MB |
| 306 | uint32 | SIM2 sent this week | MB |
| 308 | uint32 | SIM2 received this month | MB |
| 310 | uint32 | SIM2 sent this month | MB |
| 312 | uint32 | SIM2 received last 24h | MB |
| 314 | uint32 | SIM2 sent last 24h | MB |
| 316 | uint32 | SIM2 received last 7 days | MB |
| 318 | uint32 | SIM2 sent last 7 days | MB |
| 320 | uint32 | SIM2 received last 30 days | MB |
| 322 | uint32 | SIM2 sent last 30 days | MB |
| 495 | uint32 | SIM2 received last month | MB |
| 499 | uint32 | SIM2 sent last month | MB |
| 511 | uint32 | SIM2 received last week | MB |
| 515 | uint32 | SIM2 sent last week | MB |

### Modem identification (read-only)

| Register address | Count | Data type | Sensor |
|---|---|---|---|
| 328 | 8 | string | Modem ID |
| 348 | 16 | string | IMSI |
| 364 | 2 | uint32 | Unix timestamp |
| 366 | 12 | string | Local ISO time |
| 378 | 12 | string | UTC time |

### Holding registers — switches (read/write)

| Register address | Value ON | Value OFF | Switch |
|---|---|---|---|
| 203 | 1 | 0 | WiFi (all) ON/OFF |
| 204 | 1 | 0 | Mobile data ON/OFF |
| 205 | 2 | 1 | SIM card (2 = SIM2, 1 = SIM1, 0 = toggle) |
| 206 | 1 | 0 | **Router reboot** ⚠️ commented out by default |
| 390 | 1 | 0 | WiFi 2.4 GHz ON/OFF |
| 391 | 1 | 0 | WiFi 5 GHz ON/OFF |

---

## Template sensors

The YAML includes two template sensors to convert raw IP integer values to human-readable dotted-decimal format. They are defined in the same `teltonika.yaml` file under the `template:` key.

```yaml
template:
  - sensor:
      - name: "Teltonika - WAN IP address formatted"
        state: >
          {% set ip = states('sensor.teltonika_wan_ip_address') | int(0) %}
          {% if ip > 0 %}
            {{ (ip // 16777216) % 256 }}.{{ (ip // 65536) % 256 }}.{{ (ip // 256) % 256 }}.{{ ip % 256 }}
          {% else %}
            unavailable
          {% endif %}
```

**After reloading:** Template sensors require either a full HA restart or  
**Entwicklerwerkzeuge → YAML → Template-Entitäten neu laden**.

---

## Installation

1. Copy [`modbus_reference/teltonika.yaml`](modbus_reference/teltonika.yaml) to `/config/packages/teltonika.yaml`

2. Adjust the host IP if your router uses a different address:
   ```yaml
   modbus:
     - name: "teltonika"
       host: 192.168.7.1   # ← change if needed
   ```

3. In `configuration.yaml`, enable packages if not already done:
   ```yaml
   homeassistant:
     packages: !include_dir_named packages/
   ```

4. Restart Home Assistant

5. Verify in **Einstellungen → Geräte & Dienste → Entities** — search for `Teltonika`

---

## Troubleshooting

### Sensors show `unavailable`
- Check Modbus is enabled in the router WebUI (Services → Modbus)
- Verify port 502 is reachable: `nc -zv 192.168.7.1 502`
- Check HA logs for Modbus errors

### WAN IP shows a number instead of dotted notation
- The template sensor handles the conversion
- Make sure the `template:` block is in the same YAML file or another loaded package
- Reload template entities: Entwicklerwerkzeuge → YAML → Template-Entitäten neu laden

### Template error: `unexpected '>'`
- Jinja2 does not support bitshift `>>` — the template uses `//` integer division instead
- Ensure you use the version from this repo, not an older version with `>>`

### GPS shows 0.0 / no data
- GPS requires a valid satellite fix
- Check GPS is enabled in the router: Services → GPS
- Values will be `0.0` when there is no fix — this is normal behaviour

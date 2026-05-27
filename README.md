# ha_teltonika

![Teltonika Extended](icon.png)

Home Assistant custom integration for **Teltonika routers**.  
Tested with **RUTX50** — other RutOS devices (RUT, RUTX, TRB series) should work too.

Uses [okionka/teltasync](https://github.com/okionka/teltasync) — extended fork with GPS, WAN, data-usage, firmware and backup support.

> 📖 **RutOS API reference:** [developers.teltonika-networks.com](https://developers.teltonika-networks.com)

## Sensors

| Group | Sensors |
|---|---|
| **System** | Hostname, LAN IP, LAN MAC *(formatted)*, Firmware `DIAG`, Model `DIAG`, Serial `DIAG`, Device name `DIAG` |
| **WAN** | WAN IP address, WAN type `DIAG`, WAN interface `DIAG` |
| **Mobile** | RSSI, RSRP, RSRQ, SINR, Temperature, Operator, Network type, Connection state, Active SIM, SIM state `DIAG`, IMSI `DIAG`, ICCID `DIAG`, IMEI `DIAG`, Cell-ID `DIAG`, Band `DIAG`, Network registration `DIAG`, Mobile stage `DIAG` |
| **GPS** | Latitude, Longitude, Altitude, Speed, Fix, Satellites `DIAG`, Accuracy `DIAG`, Datetime `DIAG` |
| **Data usage SIM1/SIM2** | today / last 24h / this week / last 7 days / this month / last 30 days / last month / last week |

`DIAG` = tagged as **Diagnostic** (visible under the Diagnostics section in the device page)

## Controls

| Type | Entity | Description |
|---|---|---|
| 🔘 Button | **Reboot** | Reboots the router |
| 🔀 Switch | **SIM card** | ON = SIM1, OFF = SIM2 |
| 📶 Switch | **WiFi (2g/5g)** | Enable/disable per wireless interface |
| 🔄 Update | **Firmware** | Shows installed vs. available firmware, triggers OTA update |

## Services

### `teltonika_extended.backup_config`
Downloads the router configuration and saves it to `/config/teltonika_backups/`.

```yaml
service: teltonika_extended.backup_config
```

### `teltonika_extended.restore_config`
Uploads a backup file to the router. The router reboots to apply it.

```yaml
service: teltonika_extended.restore_config
data:
  file_path: teltonika_backups/RUTX50_20250527_120000.tar.gz
```

## Installation via HACS

1. HACS → **Custom repositories** → `https://github.com/okionka/ha_teltonika` → Category: **Integration**
2. Install **Teltonika Extended**
3. Restart Home Assistant
4. **Settings → Devices & Services → Add Integration → Teltonika Extended**
5. Enter IP address (e.g. `192.168.7.1`), username and password

## Tested hardware

| Device | Firmware | Status |
|---|---|---|
| RUTX50 | RutOS 7.x | ✅ Tested |
| Other RutOS devices | 7.x | Should work |

## Notes

- Can run in parallel with the Modbus YAML integration
- Polling interval: 30 seconds
- Requires RutOS REST API enabled: **Services → API**

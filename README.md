# ha_teltonika

![Teltonika Extended](icon.png)

Home Assistant custom integration for **Teltonika routers**.  
Tested with **RUTX50** — other RutOS devices (RUT, RUTX, TRB series) should work too.

Uses [okionka/teltasync](https://github.com/okionka/teltasync) — extended fork with GPS, WAN and data-usage support.

## Sensors

| Group | Sensors |
|---|---|
| **System** | Hostname, Firmware, Serial number, Model, LAN MAC, Device name |
| **Mobile** | RSSI, RSRP, RSRQ, SINR, Temperature, Operator, Network type, Connection state, IMSI, ICCID, IMEI, Cell-ID, Band, SIM state, Active SIM, Mobile stage |
| **GPS** | Latitude, Longitude, Altitude, Speed, Satellites, Accuracy, Fix, Datetime |
| **WAN** | IP address, WAN type, Interface |
| **Data usage SIM1/SIM2** | today / last 24h / this week / last 7 days / this month / last 30 days / last month / last week |

GPS, WAN and data-usage sensors only appear when the corresponding API endpoints are available on the device firmware (auto-detected).

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
- Requires RutOS REST API enabled on the router (Services → API)

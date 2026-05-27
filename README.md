# ha_teltonika

Home Assistant custom component for Teltonika routers (RUTX50 etc.)  
Extended native integration using [okionka/teltasync](https://github.com/okionka/teltasync).

## Sensors

| Gruppe | Sensoren |
|---|---|
| **System** | Hostname, Firmware, Seriennummer, Modell, LAN MAC, Gerätename |
| **Mobil** | RSSI, RSRP, RSRQ, SINR, Temperatur, Operator, Netztyp, Verbindungsstatus, IMSI, ICCID, IMEI, Cell-ID, Band, SIM-Status, aktive SIM, Mobile-Stage |
| **GPS** | Latitude, Longitude, Altitude, Speed, Satellites, Accuracy, Fix, Datetime |
| **WAN** | IP-Adresse, WAN-Typ, Interface |
| **Datenvolumen SIM1/SIM2** | heute / letzte 24h / diese Woche / letzte 7 Tage / diesen Monat / letzte 30 Tage / letzten Monat / letzte Woche |

## Installation

1. In `/config/custom_components/` den Ordner `teltonika_extended/` kopieren
2. HA neu starten
3. **Einstellungen → Geräte & Dienste → Teltonika Extended hinzufügen**

## Hinweise

- GPS-, WAN- und Datenvolumen-Sensoren erscheinen nur wenn die API-Endpunkte 
  auf der Firmware verfügbar sind (automatische Erkennung)
- Kann parallel zur Modbus YAML betrieben werden
- Polling-Intervall: 30 Sekunden

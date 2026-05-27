# ha_teltonika

![Teltonika Extended](icon.png)

Home Assistant custom integration für **Teltonika-Router**.  
Getestet mit **RUTX50** — andere RutOS-Geräte (RUT, RUTX, TRB-Serie) sollten ebenfalls funktionieren.

Verwendet [okionka/teltasync](https://github.com/okionka/teltasync) — erweiterter Fork mit GPS, WAN, Datennvolumen, Firmware und Backup.

> 📖 **RutOS API-Referenz:** [developers.teltonika-networks.com](https://developers.teltonika-networks.com)  
> 📖 **Modbus TCP Referenz:** [README_MODBUS.md](README_MODBUS.md)  
> 📖 **Marken-PR:** [homeassistant/brands #10388](https://github.com/home-assistant/brands/pull/10388)

---

## Sensoren

| Gruppe | Sensoren |
|---|---|
| **System** | Hostname, WAN IP, LAN IP, LAN MAC, Firmware, Modell, Seriennummer `DIAG`, Gerätename `DIAG`, Firmware Build-Datum `DIAG`, Kernel-Version `DIAG` |
| **Mobil** | RSSI, RSRP, RSRQ, SINR, Temperatur, Operator, Netztyp, Verbindungsstatus, Aktive SIM, SIM-Status `DIAG`, IMSI `DIAG`, ICCID `DIAG`, IMEI `DIAG`, Cell-ID `DIAG`, Band `DIAG`, Netzregistrierung `DIAG`, Mobile-Stage `DIAG` |
| **GPS** | Latitude, Longitude, Altitude, Speed, Fix, Satellites `DIAG`, Accuracy `DIAG`, Datetime `DIAG` *(deaktiviert per Default)* |
| **WAN** | WAN IP (Interface), WAN-Typ `DIAG`, WAN-Interface `DIAG` |
| **Interface-Traffic** | WiFi 2.4 GHz rx/tx, WiFi 5 GHz rx/tx, Mobile-Interface rx/tx |
| **Datenvolumen SIM1/SIM2** | heute / letzte 24h / diese Woche / letzte 7 Tage / diesen Monat / letzte 30 Tage / letzten Monat / letzte Woche |
| **Firmware** | Firmware-Update-Entity, Modem-Firmware-Update-Entity, Firmware available `DIAG`, Firmware Build-Datum `DIAG`, Modem-Firmware `DIAG`, Firmware-Check-Response `DIAG` |
| **Backup** | Last backup status, Last backup file `DIAG`, Last backup size `DIAG`, Last backup time `DIAG` |

`DIAG` = als **Diagnose** markiert (ausgeklappt unter „Diagnose" auf der Geräteseite)

---

## Steuerung

| Typ | Entität | Beschreibung |
|---|---|---|
| 🔘 Button | **Reboot** | Router neu starten |
| 💾 Button | **Backup configuration** | Konfiguration sichern (→ `/config/teltonika_backups/`) |
| 🔀 Switch | **SIM card (SIM1 = ON)** | EIN = SIM1 aktiv, AUS = SIM2 aktiv |
| 📶 Switch | **WiFi 2.4 GHz** | 2,4-GHz-WLAN ein/aus |
| 📶 Switch | **WiFi 5 GHz** | 5-GHz-WLAN ein/aus |
| 🔄 Update | **Firmware** | Zeigt installierte vs. verfügbare Version, OTA-Install |
| 🔄 Update | **Modem firmware** | Zeigt Modem-Firmware-Version |

---

## Services

### `teltonika_extended.restore_config`
Schritt 1+2: Backup hochladen + validieren → HA-Benachrichtigung mit Metadaten.

```yaml
service: teltonika_extended.restore_config
data:
  file_path: teltonika_backups/RUTX50_20260527_120000.tar.gz
```

### `teltonika_extended.restore_config_apply`
Schritt 3: Backup anwenden (Router startet neu).

```yaml
service: teltonika_extended.restore_config_apply
```

---

## Backup-Ablauf

Der Backup-Button führt folgende API-Aufrufe aus:

```
POST /backup/actions/generate  {"data": {}}
  ← {success: true, data: {sha256: "...", md5: "..."}}

POST /backup/actions/download  {"data": {"sha256": "..."}}
  ← binary .tar.gz

→ gespeichert in /config/teltonika_backups/<hostname>_<timestamp>.tar.gz
→ SHA256 wird verifiziert
```

---

## Installation via HACS

1. HACS → **Benutzerdefinierte Repositories** → `https://github.com/okionka/ha_teltonika` → Kategorie: **Integration**
2. **Teltonika Extended** installieren
3. Home Assistant neu starten
4. **Einstellungen → Geräte & Dienste → Integration hinzufügen → Teltonika Extended**
5. IP-Adresse (z.B. `192.168.7.1`), Benutzername und Passwort eingeben

---

## Getestete Hardware

| Gerät | Firmware | Status |
|---|---|---|
| RUTX50 | RutOS 7.x | ✅ Getestet |
| Andere RutOS-Geräte | 7.x | Sollte funktionieren |

---

## Hinweise

- Kann parallel zur [Modbus YAML-Integration](README_MODBUS.md) betrieben werden
- Polling-Intervall: 30 Sekunden
- Erfordert aktivierte RutOS REST API: **Dienste → API**
- GPS-Datetime-Sensor ist per Default deaktiviert (verhindert Aktivitätslog-Spam)
  → Aktivieren: Einstellungen → Entitäten → GPS datetime (UTC)

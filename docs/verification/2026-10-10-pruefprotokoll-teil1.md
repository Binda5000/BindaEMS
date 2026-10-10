# Prüfprotokoll Teil 1 – 2026-10-10

| Angabe | Wert |
|---|---|
| Datum | 2026-10-10 |
| Konfiguration | /config/config.yaml |
| MQTT-Aufzeichnung | 120 s |
| vebus-Firmware | 1366 |
| Venus OS | v3.80 |
| EVCS-Firmware evcs | 0000.0000 |
| Wall-Connector-Firmware twc | 26.34.0+g1a5f79be65b2b4 |

| Punkt | Prüfung | Ergebnis | Details |
|---|---|---|---|
| 17.1-1 | vebus-Phasen | OK | 3 Phasen erkannt. |
| 17.1-2 | ESS-Modus | OK | ESS-Modus 1 wie erwartet. |
| 17.1-2 | BatteryLife-Zustand | OK | BatteryLife-Zustand 10 wie erwartet. |
| 17.1-2 | Dynamic ESS | FEHLER | Dynamic ESS ist aktiv (Modus 1). |
| 17.1-2 | Victron-Ladefenster | OK | Keine Victron-Ladefenster aktiv. |
| 17.1-2 | Min-SOC und USV-Reserve | OK | Victron-Min-SOC entspricht der USV-Reserve. |
| 17.1-2 | Peak-Shaving-Einstellungen | INFO | settings.cgwacs.acexportlimit = -1; settings.cgwacs.acinputlimit = 20; settings.cgwacs.alwayspeakshave = 1; settings.cgwacs.batterylife.minimumsoclimit = 20; settings.cgwacs.batterylife.soclimit = 62 |
| 17.1-3 | MQTT-Dienste und Instanzen | OK | Gefunden: acload 32; adc 0; battery 512; digitalinputs 0; evcharger 40; fronius 0; grid 30; hub4 0; logger 0; modbusclient 0; modbustcp 0; platform 0; pvinverter 0; settings 0; shelly 0; system 0; temperature 24, 25; vebus 276 |
| 17.1-4 | Datenfrische | OK | Kritische Messwerte seit mindestens 30 s aktuell. |
| 17.1-4 | Netzzähler P/I/U je Phase | OK | grid.l1.power_w = -79.1; grid.l1.current_a = -1.316; grid.l1.voltage_v = 233.1; grid.l2.power_w = -11.1; grid.l2.current_a = -1.634; grid.l2.voltage_v = 233.6; grid.l3.power_w = 80.8; grid.l3.current_a = 2.287; grid.l3.voltage_v = 233.8 |
| 17.1-4 | Vorzeichen des Netzstroms | INFO | vorzeichenbehaftet |
| 17.1-4 | Netzzähler Aktualisierung | OK | Median-Alter von grid.l1.power_w 0.8 s, Aktualisierung etwa alle 1.6 s (24 Proben) |
| 17.1-4 | Netzzähler Zählerstände | OK | grid.energy_import_kwh = 30283.8; grid.energy_export_kwh = 5440.7 |
| 17.1-5 | PV-Position huawei | OK | huawei: AC-Out |
| 17.1-5 | Zählerstände PV und Subzähler | OK | load.obergeschoss.energy_kwh = 75; pv.huawei.energy_kwh = 601.9 |
| 17.1-6 | Akku | OK | battery.soc_pct = 34; battery.voltage_v = 52.38; battery.current_a = -24.3; battery.power_w = -1272; battery.ccl_a = 480; battery.dcl_a = 540; battery.installed_capacity_ah = 1880 |
| 17.1-7 | hub4-Overrides | INFO | ess.override.feedinexcess = 2; ess.override.forcecharge = 0 |
| 17.1-8 | EVCS-Modus evcs | OK | EVCS im manuellen Modus. |
| 17.1-8 | EVCS-Produkt-ID | WARNUNG | Produkt-ID 0x0000 – unbekannt |
| 17.1-8 | EVCS-Firmware | INFO | Firmware 0000.0000 |
| 17.1-8 | EVCS-Register ≠ 0 | INFO | Alle Register 0. |
| 17.1-9 | Wall-Connector-Vitals | OK | contactor_closed = False, vehicle_connected = False, evse_state = 1, currentA_a = 0.2, currentB_a = 0.0, currentC_a = 0.2, voltageA_v = 0.0, voltageB_v = 2.6, voltageC_v = 7.4, session_energy_wh = 5338.2 |
| 17.1-10 | Tessie-Felder | OK | Alle benötigten Felder vorhanden. |
| 17.1-10 | Tessie-Latenz tesla | INFO | 0.32 s |
| 17.1-11 | HA sensor.aussen_volkswagen_e_golf_state_of_charge | OK | sensor.aussen_volkswagen_e_golf_state_of_charge = 75 |
| 17.1-11 | HA sensor.og_serverraum_steckdose_serverschrank_power | OK | sensor.og_serverraum_steckdose_serverschrank_power = 356 |
| 17.1-11 | InfluxDB-Datenbank | OK | Datenbanken: homeassistant, bindaems |
| 17.1-11 | InfluxDB-Retention-Policies | OK | raw (90 Tage, Standard) und long (unbegrenzt) vorhanden. |
| 17.1-11 | HA-Messungen in InfluxDB | INFO | homeassistant: 3321 Messungen, je Entität benannt (`measurement_attr: entity_id`, z. B. `binary_sensor.…`); Liste gekürzt, weil das Repository öffentlich ist |
| 17.1-12 | Uhrzeit gegenüber der VM | OK | homeassistant: -0.4 s; influxdb: -0.5 s |

## Manuell zu ergänzen

| Punkt | Prüfung | Ergebnis |
|---|---|---|
| 17.1-3 | Sicherheitsprofil und Modbus-Dienstliste am Cerbo | offen |
| 17.1-12 | NTP am Cerbo synchron | offen |

## Bewertung vom 10.10.2026

- **Dynamic ESS aktiv (17.1-2):** Spec 3.2 und 7.2 verlangen DESS aus, bevor das EMS schreibt.
  Wann DESS ausgeschaltet wird, entscheidet der Betreiber. Offen.
- **EVCS-Registerabbild leer (17.1-8):** Die Register 5000–5199 sind alle 0, auch Produkt-ID
  und Firmware. Der „manuelle Modus“ stammt aus Register 5009 = 0 und ist deshalb nicht
  belegt. Leistung und Energie der EVCS kommen über den GX (`evcharger` 40) und sind davon
  nicht betroffen. Offen: Adresse und Modbus-Einstellungen der EVCS prüfen; der Weg für das
  Schreiben wird in Prüfprotokoll Teil 2 festgelegt (Spec 8.6).
- **Peak Shaving (17.1-2):** Importlimit 20 A mit „immer“. Spec 8.1 verlangt es knapp unter
  der Hausanschlusssicherung (`grid.fuse_a`). Offen.

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
  Entscheidung des Betreibers: DESS bleibt an, bis Phase 2 live schaltet. Bis dahin ist der
  Fehler in der Selbstprüfung erwartet; die Abnahme-Checkliste lässt ihn zu.
- **EVCS-Registerabbild leer (17.1-8):** Die Register 5000–5199 sind alle 0, auch Produkt-ID
  und Firmware. Der „manuelle Modus“ stammte aus Register 5009 = 0 und war damit nicht belegt.
  - Die Rohdaten zeigen den Weg des Cerbo: `evcharger/40/Mgmt/Connection` = „Modbus TCP
    192.168.81.41“ (`dbus-modbus-client`), Produkt-ID 49190 = 0xC026, Modell AC22NS, Modus 0
    (manuell), Firmware 133631. Die EVCS ist also über Modbus TCP erreichbar und tatsächlich
    im manuellen Modus.
  - Nachtrag: Laut Betreiber stand `wallboxes.evcs.host` schon auf 192.168.81.41. Die
    Adresse erklärt die Nullen also nicht. BindaEMS liest wie der Cerbo: Funktionscode 3,
    Unit-ID 1, ab Register 5000. Die Ursache ist offen und wird vor Phase 2b geklärt.
  - Seit dieser Prüfung liest die Selbstprüfung den EVCS-Modus vom Cerbo. Ein leeres
    Registerabbild meldet das Werkzeug als WARNUNG mit dem Weg des Cerbo.
  - Leistung und Energie der EVCS kommen über den Cerbo und sind nicht betroffen. Den Weg für
    das Schreiben legt Prüfprotokoll Teil 2 fest (Spec 8.6).
- **Peak Shaving (17.1-2):** Das Importlimit steht auf 20 A mit „immer“, die
  Hausanschlusssicherung hat 35 A. Spec 8.1 verlangt das Limit knapp unter der Sicherung,
  z. B. 33 A. Nachtrag: Laut Betreiber steht das Limit bei etwa 33 A; diese Prüfung las 20 A.
- **Akku (17.1-6):** 1880 Ah, etwa 96 kWh; `battery.usable_kwh` ist 96 (Angabe des Betreibers).

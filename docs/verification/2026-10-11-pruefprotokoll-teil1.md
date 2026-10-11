# Prüfprotokoll Teil 1 – 2026-10-11

| Angabe | Wert |
|---|---|
| Datum | 2026-10-11 |
| Konfiguration | /config/config.yaml |
| MQTT-Aufzeichnung | 120 s |
| vebus-Firmware | 1366 |
| Venus OS | v3.80 |
| EVCS-Firmware evcs | v2.09 |
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
| 17.1-4 | Netzzähler P/I/U je Phase | OK | grid.l1.power_w = -102.1; grid.l1.current_a = -1.531; grid.l1.voltage_v = 231.3; grid.l2.power_w = -192.7; grid.l2.current_a = -1.66; grid.l2.voltage_v = 232.2; grid.l3.power_w = 281.6; grid.l3.current_a = 2.193; grid.l3.voltage_v = 231 |
| 17.1-4 | Vorzeichen des Netzstroms | INFO | vorzeichenbehaftet |
| 17.1-4 | Netzzähler Aktualisierung | OK | Median-Alter von grid.l1.power_w 0.0 s, Aktualisierung etwa alle 0.1 s (24 Proben) |
| 17.1-4 | Netzzähler Zählerstände | OK | grid.energy_import_kwh = 30309.9; grid.energy_export_kwh = 5441.3 |
| 17.1-5 | PV-Position huawei | OK | huawei: AC-Out |
| 17.1-5 | Zählerstände PV und Subzähler | OK | load.obergeschoss.energy_kwh = 80.7; pv.huawei.energy_kwh = 630.27 |
| 17.1-6 | Akku | OK | battery.soc_pct = 46; battery.voltage_v = 52.62; battery.current_a = -21.8; battery.power_w = -1147; battery.ccl_a = 480; battery.dcl_a = 540; battery.installed_capacity_ah = 1880 |
| 17.1-7 | hub4-Overrides | INFO | ess.override.feedinexcess = 2; ess.override.forcecharge = 0 |
| 17.1-8 | EVCS-Modus evcs | OK | EVCS im manuellen Modus. |
| 17.1-8 | EVCS-Produkt-ID | OK | Produkt-ID 0xC026 |
| 17.1-8 | EVCS-Firmware | INFO | Firmware v2.09 |
| 17.1-8 | EVCS-Register ≠ 0 | INFO | 5000 = 49190 (0xC026); 5007 = 2 (0x0002); 5008 = 2559 (0x09FF); 5016 = 12 (0x000C); 5017 = 16 (0x0010); 5020 = 4214 (0x1076); 5021 = 464 (0x01D0); 5022 = 4 (0x0004); 5023 = 11 (0x000B); 5024 = 38242 (0x9562); 5025 = 1215 (0x04BF); 5026 = 1 (0x0001); 5049 = 1 (0x0001); 5050 = 65535 (0xFFFF); 5051 = 65535 (0xFFFF); 5052 = 65535 (0xFFFF); 5053 = 65535 (0xFFFF); 5055 = 65535 (0xFFFF); 5056 = 65535 (0xFFFF); 5057 = 65535 (0xFFFF); 5058 = 65535 (0xFFFF); 5059 = 65535 (0xFFFF); 5062 = 6 (0x0006); 5063 = 100 (0x0064); 5065 = 2 (0x0002); 5066 = 80 (0x0050); 5067 = 20 (0x0014); 5069 = 30 (0x001E); 5070 = 60 (0x003C); 5071 = 65036 (0xFE0C); 5072 = 64136 (0xFA88); 5073 = 65286 (0xFF06); 5074 = 250 (0x00FA); 5075 = 65436 (0xFF9C); 5076 = 2 (0x0002); 5077 = 1000 (0x03E8); 5078 = 30 (0x001E); 5079 = 60 (0x003C); 5080 = 10 (0x000A); 5081 = 500 (0x01F4); 5082 = 80 (0x0050); 5083 = 100 (0x0064); 5084 = 25 (0x0019); 5085 = 400 (0x0190); 5086 = 400 (0x0190); 5087 = 7 (0x0007); 5088 = 45 (0x002D); 5089 = 15 (0x000F); 5090 = 10 (0x000A); 5091 = 64036 (0xFA24); 5092 = 300 (0x012C); 5093 = 10 (0x000A); 5101 = 10 (0x000A); 5102 = 1 (0x0001); 5103 = 30 (0x001E); 5105 = 150 (0x0096); 5106 = 10 (0x000A); 5107 = 5 (0x0005); 5108 = 2000 (0x07D0); 5109 = 1 (0x0001); 5110 = 600 (0x0258); 5111 = 21 (0x0015); 5112 = 1871 (0x074F); 5113 = 834 (0x0342); 5114 = 1 (0x0001); 5115 = 805 (0x0325); 5116 = 2 (0x0002); 5118 = 5 (0x0005); 5119 = 1 (0x0001); 5120 = 20 (0x0014); 5127 = 175 (0x00AF); 5130 = 1 (0x0001); 5131 = 60 (0x003C); 5132 = 100 (0x0064); 5133 = 1 (0x0001); 5134 = 500 (0x01F4); 5136 = 100 (0x0064); 5138 = 99 (0x0063); 5139 = 10 (0x000A); 5141 = 136 (0x0088); 5143 = 300 (0x012C); Seriennummer (5001–5006) und Name (5027–5048) stehen nur in den Rohdaten. |
| 17.1-9 | Wall-Connector-Vitals | OK | contactor_closed = False, vehicle_connected = False, evse_state = 1, currentA_a = 0.2, currentB_a = 0.0, currentC_a = 0.2, voltageA_v = 0.0, voltageB_v = 2.6, voltageC_v = 7.4, session_energy_wh = 5338.2 |
| 17.1-10 | Tessie-Felder | OK | Alle benötigten Felder vorhanden. |
| 17.1-10 | Tessie-Latenz tesla | INFO | 0.31 s |
| 17.1-11 | HA sensor.aussen_volkswagen_e_golf_state_of_charge | OK | sensor.aussen_volkswagen_e_golf_state_of_charge = 57 |
| 17.1-11 | HA sensor.og_serverraum_steckdose_serverschrank_power | OK | sensor.og_serverraum_steckdose_serverschrank_power = 369 |
| 17.1-11 | InfluxDB-Datenbank | OK | Datenbanken: homeassistant, bindaems |
| 17.1-11 | InfluxDB-Retention-Policies | OK | raw (90 Tage, Standard) und long (unbegrenzt) vorhanden. |
| 17.1-11 | HA-Messungen in InfluxDB | INFO | homeassistant: 3322 Messungen; die Namen stehen nur in den Rohdaten. |
| 17.1-12 | Uhrzeit gegenüber der VM | OK | homeassistant: -0.9 s; influxdb: -1.0 s |

## Manuell zu ergänzen

| Punkt | Prüfung | Ergebnis |
|---|---|---|
| 17.1-3 | Sicherheitsprofil und Modbus-Dienstliste am Cerbo | offen |
| 17.1-12 | NTP am Cerbo synchron | offen |

## Bewertung vom 11.10.2026

- **Gekürzt beim Einchecken:** Seriennummer und Name der EVCS (Register 5001–5006 und ab 5027)
  sowie die Namen der HA-Messungen. Das Werkzeug lässt sie seit diesem Stand selbst weg, damit der
  Bericht ins öffentliche Repository passt; in den Rohdaten bleiben sie.
- **EVCS (17.1-8):** Seit der korrigierten IP-Whitelist liefert die EVCS ihr Registerabbild
  (Prüfprotokoll vom 10.10.2026, Nachtrag). Produkt-ID 0xC026 (AC22NS), Firmware v2.09,
  manueller Modus (5009 = 0), Sollstrom 12 A (5016), Maximalstrom 16 A (5017). Die WARNUNG vom
  10.10.2026 ist erledigt.
- **Dynamic ESS (17.1-2):** unverändert aktiv. Der FEHLER ist erwartet: DESS bleibt nach
  Entscheidung des Betreibers an, bis Victron live geschaltet wird.
- **Peak Shaving (17.1-2):** Der Cerbo meldet wie am 10.10.2026 `Settings/CGwacs/AcInputLimit`
  = 20 A mit `AlwaysPeakShave` = 1. Laut Betreiber steht das Importlimit bei etwa 33 A, Spec 8.1
  verlangt es knapp unter der 35-A-Hausanschlusssicherung. Offen: am Cerbo prüfen, welcher Wert
  gilt.
- **Übrige Punkte:** wie am 10.10.2026, ohne Befund. Offen sind nur die manuellen Punkte 17.1-3
  und 17.1-12.

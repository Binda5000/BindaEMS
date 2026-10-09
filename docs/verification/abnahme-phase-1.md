# Abnahme Phase 1 – Checkliste

Spec Abschnitt 18, Phase 1, „Abnahme“: 7 Tage lückenlose Aufzeichnung, Tages-Energiebilanz
innerhalb ±2 % von VRM, Preise und Prognose sichtbar, nachweislich keine Schreibpfade zu Geräten.

Diese Checkliste füllst du an der Anlage aus. Kopiere sie dafür nach
`docs/verification/JJJJ-MM-TT-abnahme-phase-1.md`, trage die Ergebnisse ein und checke sie ein.
Voraussetzung: Prüfprotokoll Teil 1 und das Prüfprotokoll „Preise“ sind abgeschlossen
(siehe `README.md` in diesem Verzeichnis).

| Angabe | Wert |
|---|---|
| Anlage | |
| Version core / app (UI → System) | |
| Aufzeichnung von – bis | |
| Prüfer | |

---

## 1. 7 Tage lückenlose Aufzeichnung

**Fundort im UI:** Verlauf, Tagesbilanz, System.

Prüfschritte:

1. **Verlauf:** Erster Tag und Letzter Tag auf die 7 Prüftage setzen (oder „7 Tage“), Reihen
   Netz, PV, Akku und Haus wählen. Die Linien laufen ohne Unterbrechung durch.
2. **Tagesbilanz** (unter dem Diagramm): An allen 7 Tagen steht volle Abdeckung, also
   „96 von 96“. Am Tag der Umstellung auf Sommerzeit gilt „92 von 92“, am Tag der Umstellung auf
   Winterzeit „100 von 100“.
3. **System:** Die Komponente InfluxDB meldet „InfluxDB: alles übertragen“, core ist verbunden
   und die Selbstprüfung zeigt keinen Fehler.

Nachweis: Bildschirmfoto von Verlauf und Tagesbilanz, Werte in der Tabelle.

| Tag | Abdeckung | Lücken im Diagramm | Bemerkung |
|---|---|---|---|
| 1 | von | ☐ keine | |
| 2 | von | ☐ keine | |
| 3 | von | ☐ keine | |
| 4 | von | ☐ keine | |
| 5 | von | ☐ keine | |
| 6 | von | ☐ keine | |
| 7 | von | ☐ keine | |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 2. Tages-Energiebilanz innerhalb ±2 % von VRM

**Fundort im UI:** Verlauf → Tagesbilanz (Spalten PV, Bezug, Einspeisung, Haus, Wallboxen) und
je Tag „Zähler“ → „Zählerstände“.

Prüfschritte:

1. Mindestens 3 der 7 Tage wählen, darunter ein sonniger Tag mit Einspeisung.
2. In VRM für jeden Tag PV-Ertrag, Netzbezug, Einspeisung und Verbrauch ablesen.
3. In der Tagesbilanz dieselben Größen ablesen; Verbrauch = Haus + Wallboxen.
4. Abweichung in % = (BindaEMS − VRM) / VRM × 100. Jede Größe muss innerhalb ±2 % liegen.
5. Die Zählerstände (Netz Bezug, Netz Einspeisung) mit den Zählerdaten in VRM vergleichen. Sie
   helfen, eine Abweichung einer Seite zuzuordnen.

| Tag | Größe | VRM (kWh) | BindaEMS (kWh) | Abweichung (%) | innerhalb ±2 % |
|---|---|---|---|---|---|
| | PV | | | | ☐ |
| | Netzbezug | | | | ☐ |
| | Einspeisung | | | | ☐ |
| | Verbrauch | | | | ☐ |
| | PV | | | | ☐ |
| | Netzbezug | | | | ☐ |
| | Einspeisung | | | | ☐ |
| | Verbrauch | | | | ☐ |
| | PV | | | | ☐ |
| | Netzbezug | | | | ☐ |
| | Einspeisung | | | | ☐ |
| | Verbrauch | | | | ☐ |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 3. Preise und Prognose sichtbar

**Fundort im UI:** Übersicht, System.

Prüfschritte:

1. **Übersicht:** „Strompreis“ zeigt den Preis jetzt in ct/kWh und die Balken der nächsten 3 h.
   Das Diagramm „Preise und PV heute/morgen“ zeigt Bezugspreis, Einspeisung und PV-Prognose; ab
   dem Nachmittag (Veröffentlichung der Day-Ahead-Preise) auch den Folgetag.
2. **System → Preise:** letzter Erfolg aktuell, Netto/Brutto erkannt (z. B. „brutto (erkannt,
   Verhältnis … aus … Slots)“), Herkunft der Tage „smartENERGY“, keine Befunde.
3. **System → PV-Prognose:** Status in Ordnung, Ausgabezeit der letzten Stunde, kWh für heute
   und die zwei Folgetage.
4. Das Prüfprotokoll „Preise“ liegt bestanden vor (Datum eintragen).

| Punkt | Ergebnis |
|---|---|
| Preis jetzt und nächste 3 h sichtbar | ☐ |
| Diagramm heute (und morgen) | ☐ |
| Netto/Brutto erkannt | ☐ |
| PV-Prognose aktuell | ☐ |
| Prüfprotokoll „Preise“ vom | |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 4. Nachweislich keine Schreibpfade zu Geräten

### 4.1 Im Code

- Der core publiziert am Cerbo nur `R/<portal>/keepalive` (Abruf der Werte). Er liest über
  Modbus nur und schickt an Wall Connector, Tessie und Home Assistant nur `GET`-Anfragen.
- Die app schreibt nur nach InfluxDB (Messungen `price`, `forecast`, `ledger`) und auf ihre
  eigenen MQTT-Topics für Home Assistant (Discovery und Zustände unter dem eigenen Präfix).
- Phase 1 enthält keine Schreib- oder Steuerfunktion; der core läuft im Modus OBSERVE
  („System“ → Betriebsart „Beobachten (nur lesend)“).

### 4.2 An der Anlage: 24 h ohne Schreibzugriff

Auf dem Broker des Cerbo 24 h lang alle Schreib-Topics mitschneiden, mit denselben Zugangsdaten
wie der core (`victron.mqtt` in `config.yaml`; bei TLS mit dem Zertifikat des Cerbo):

```bash
mosquitto_sub -h <cerbo> -p 8883 --cafile <cerbo-ca.pem> -v -t 'W/#' | tee w-topics.log
```

Erwartet: keine Nachricht von BindaEMS. Schreibzugriffe anderer Clients (VRM, Remote Console)
in dieser Zeit vermeiden oder im Protokoll begründen.

### 4.3 Einstellungen vorher und nachher

EVCS-Modus und ESS-Einstellungen (ESS-Modus, BatteryLife, Min-SOC, Dynamic ESS, Ladefenster)
zu Beginn und am Ende der 7 Tage mit dem Prüfprotokoll Teil 1 erfassen; „System“ →
Selbstprüfung zeigt sie laufend. Alle Werte sind unverändert.

| Punkt | Ergebnis |
|---|---|
| Code geprüft (4.1) | ☐ |
| 24 h ohne Nachricht unter `W/#` (4.2), Mitschnitt vom | |
| Einstellungen vorher = nachher (4.3), Prüfprotokolle vom / vom | |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## Gesamtergebnis

☐ Phase 1 abgenommen ☐ nicht abgenommen, offene Punkte:

Datum: ________ Unterschrift: ________

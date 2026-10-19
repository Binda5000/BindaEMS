# Abnahme Phase 1 – Protokoll

Spec Abschnitt 18, Phase 1, „Abnahme“: 7 Tage lückenlose Aufzeichnung, Tages-Energiebilanz
innerhalb ±2 % von VRM, Preise und Prognose sichtbar, nachweislich keine Schreibpfade zu Geräten.

Angelegt am 19.10.2026 aus der Checkliste `abnahme-phase-1.md`. Die Punkte 4.2 und 4.3 hat der
Betreiber am 19.10.2026 als überholt festgelegt (Begründung unter 4); die Abschnitte 1 bis 3 und 4.1
füllt er an der Anlage aus.
Voraussetzung: Prüfprotokoll Teil 1 und das Prüfprotokoll „Preise“ sind abgeschlossen
(siehe `README.md` in diesem Verzeichnis).

| Angabe | Wert |
|---|---|
| Anlage | |
| Version core / app (UI → System) | `fb23bd0` (ausgerollt am 18.10.2026) |
| Aufzeichnung von – bis | |
| Prüfer | |

---

## 1. 7 Tage lückenlose Aufzeichnung

**Fundort im UI:** Verlauf, Tagesbilanz, System.

Prüfschritte:

1. **Verlauf:** Erster Tag und Letzter Tag auf die 7 Prüftage setzen (oder „7 Tage“), Reihen
   Netz, PV, Akku und Haus wählen. Die Linien laufen ohne Unterbrechung durch.
2. **Tagesbilanz** (unter dem Diagramm): An allen 7 Tagen steht volle Abdeckung, also
   „96 von 96 · 100 %“. Am Tag der Umstellung auf Sommerzeit gilt „92 von 92 · 100 %“, am Tag
   der Umstellung auf Winterzeit „100 von 100 · 100 %“. Die Prozentzahl ist der Anteil der
   aufgezeichneten Zeit und wird abgerundet: Schon eine Lücke von mehr als 5 s drückt sie unter
   100 %, auch wenn alle Viertelstunden Daten haben.
3. **System:** Die Komponente InfluxDB meldet „InfluxDB: alles übertragen“, core ist verbunden
   und die Selbstprüfung zeigt keinen Fehler. Einzige Ausnahme: „Dynamic ESS ist aktiv“. DESS
   bleibt nach Entscheidung des Betreibers vom 10.10.2026 an, bis Victron live geschaltet wird.
   Seit Phase 2a zeigt die Karte „Aktoren“ deshalb bei Victron „gesperrt: Dynamic ESS ist aktiv …“;
   das ist erwartet.

Nachweis: Bildschirmfoto von Verlauf und Tagesbilanz, Werte in der Tabelle.

| Tag | Abdeckung (Viertelstunden · %) | Lücken im Diagramm | Bemerkung |
|---|---|---|---|
| 1 | von · % | ☐ keine | |
| 2 | von · % | ☐ keine | |
| 3 | von · % | ☐ keine | |
| 4 | von · % | ☐ keine | |
| 5 | von · % | ☐ keine | |
| 6 | von · % | ☐ keine | |
| 7 | von · % | ☐ keine | |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 2. Tages-Energiebilanz innerhalb ±2 % von VRM

**Fundort im UI:** Verlauf → Tagesbilanz (Spalten PV, Bezug, Einspeisung, Haus, Wallboxen) und
je Tag „Zähler“ → „Tageswerte“.

Prüfschritte:

1. Mindestens 3 der 7 Tage wählen, darunter ein sonniger Tag mit Einspeisung.
2. In VRM für jeden Tag PV-Ertrag, Netzbezug, Einspeisung und Verbrauch ablesen.
3. In der Tagesbilanz dieselben Größen ablesen; Verbrauch = Haus + Wallboxen.
4. Abweichung in % = (BindaEMS − VRM) / VRM × 100. Jede Größe muss innerhalb ±2 % liegen.
5. Die Tageswerte der Zähler (Netz Bezug, Netz Einspeisung; Differenz der Zählerstände über den
   Tag) mit den Zählerdaten in VRM vergleichen. Sie helfen, eine Abweichung einer Seite zuzuordnen.

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

- Der core publiziert am Cerbo `R/<portal>/keepalive` (Abruf der Werte). Seit Phase 2a kommt der
  Heartbeat `W/<portal>/bindaems/0/Heartbeat` an den eigenen Watchdog hinzu, seit Phase 2b haben
  die Aktoren Schreibwege: MQTT-Aufträge an den Cerbo (Netz-Sollwert-Override, Entladegrenze,
  Min-SOC, EVCS-Strom und Laden über den GX-Dienst `evcharger`) und `POST`-Befehle an Tessie.
  Beschrieben wird nur ein live geschalteter Aktor: mit dem Heartbeat, solange der core regelt, und
  mit den Sicherwerten im sicheren Zustand und bei der Übergabe. Das ist vor Plan 2d nie der Fall:
  Alle Aktoren laufen im Dry-Run, und live schaltet nur eine Absicht über die interne API
  (`PUT /v1/intent`), die erst die Bedienung in Plan 2d sendet. Der core liest über Modbus nur und
  schickt an Wall Connector und Home Assistant nur `GET`-Anfragen; an Tessie gehen Lese-Anfragen,
  Befehle nur für einen live geschalteten Tesla.
- Die app schreibt nur nach InfluxDB (Messungen `price`, `forecast`, `ledger`) und auf ihre
  eigenen MQTT-Topics für Home Assistant (Discovery und Zustände unter dem eigenen Präfix).
- Bis Plan 2d enthält BindaEMS keine Bedienung, die einen Aktor live schaltet; der core läuft im
  Modus OBSERVE („System“ → Betriebsart „Beobachten (nur lesend)“, Grund „Regelbetrieb ist aus.“).
  Unter „System“ → „Aktoren“ steht bei jedem Aktor „Angefordert“ und „Wirksam“ auf „Dry-Run“, und
  die Karte „Watchdog am Cerbo“ zeigt den Heartbeat „aus (kein Aktor live)“.

> **Überholt seit dem 17.10.2026 (Festlegung des Betreibers vom 19.10.2026):** Wallbox (`wallbox.evcs`)
> und Tesla sind seit dem 17.10.2026 live, seit dem 19.10.2026 ist Dynamic ESS aus, und Victron läuft
> im Dry-Run der Planausführung. Ein live geschalteter Aktor schreibt bestimmungsgemäß (Heartbeat unter
> `W/<portal>/bindaems/0/Heartbeat`, Befehle an EVCS und Tesla), und die ESS-Einstellungen sind
> gewollt verändert. 4.2 und 4.3 gingen nur noch in einem eigenen Fenster mit allen Aktoren im
> Dry-Run und Regelbetrieb aus; darauf verzichtet der Betreiber. Den Nachweis, dass nur live
> geschaltete Aktoren schreiben, führen die Prüfprotokolle Teil 2 und das Entscheidungslog (Dry-Run
> sendet nichts). 4.1 gilt weiter.

### 4.2 An der Anlage: 24 h ohne Schreibzugriff

Auf dem Broker des Cerbo 24 h lang alle Schreib-Topics mitschneiden, mit denselben Zugangsdaten
wie der core (`victron.mqtt` in `config.yaml`; bei TLS mit dem Zertifikat des Cerbo):

```bash
mosquitto_sub -h <cerbo> -p 8883 --cafile <cerbo-ca.pem> -v -t 'W/#' | tee w-topics.log
```

Erwartet: keine Nachricht von BindaEMS. Schreibzugriffe anderer Clients (VRM, Remote Console)
in dieser Zeit vermeiden oder im Protokoll begründen. Das gilt auch für die Watchdog-Probe
(Betriebshandbuch 14) und für Teil 2 (Betriebshandbuch 11): Die Probe sendet Heartbeats unter
`W/<portal>/bindaems/0/Heartbeat`, Teil 2 schreibt auf die Geräte. Beides also nicht während des
Mitschnitts durchführen.

### 4.3 Einstellungen vorher und nachher

EVCS-Modus und ESS-Einstellungen (ESS-Modus, BatteryLife, Min-SOC, Dynamic ESS, Ladefenster)
zu Beginn und am Ende der 7 Tage mit dem Prüfprotokoll Teil 1 erfassen; „System“ →
Selbstprüfung zeigt sie laufend. Alle Werte sind unverändert. Die Watchdog-Probe setzt EVCS-Strom,
Min-SOC und Entladegrenze auf die Sicherwerte, und Teil 2 verstellt Werte und stellt sie wieder her;
führe beides vor dem Beginn der 7 Tage oder nach ihrem Ende durch.

| Punkt | Ergebnis |
|---|---|
| Code geprüft (4.1) | ☐ |
| 24 h ohne Nachricht unter `W/#` (4.2), Mitschnitt vom | überholt (siehe oben) |
| Einstellungen vorher = nachher (4.3), Prüfprotokolle vom / vom | überholt (siehe oben) |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## Gesamtergebnis

☐ Phase 1 abgenommen ☐ nicht abgenommen, offene Punkte:

Datum: ________ Unterschrift: ________

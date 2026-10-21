# Abnahme Phase 4 – Checkliste

Spec Abschnitt 18, Phase 4, „Abnahme“: Abrechnung über 30 Tage innerhalb ±1 % der Zählerstände;
alle HA-Bedienelemente wirken in beide Richtungen; Ziel-SOC in mindestens 95 % der machbaren
geplanten Sessions erreicht. Phase 4 ist in zwei Pläne geteilt (4a „Statistik“, 4b „Geplantes
Laden“). Die Abschnitte 1 bis 3 prüfen den ersten Punkt, die Abrechnung (**Plan 4a**); Abschnitt 4
prüft den Ziel-SOC und Abschnitt 5 die Bedienelemente in Home Assistant (**Plan 4b**).

Diese Checkliste füllst du an der Anlage aus. Kopiere sie dafür nach
`docs/verification/JJJJ-MM-TT-abnahme-phase-4.md`, trage die Ergebnisse ein und checke sie ein. Die
beiden Teile dürfen zu verschiedenen Zeiten laufen; dann kopierst du die Checkliste für jeden Teil und
füllst dort nur seine Abschnitte aus.

- **Teil Statistik (Abschnitte 1 bis 3):** Voraussetzung ist, dass Plan 4a ausgerollt ist
  (Betriebshandbuch, Abschnitt 8, „Statistik ausrollen (Plan 4a)“) und OeMAG-Werte und Grundgebühren
  im Tarif eingetragen sind. Die 30 Tage beginnen mit dem Ausrollen; die Abnahme der Phase 3 (Victron
  live, 30 echte Tage) ist davon unabhängig.
- **Teil Geplantes Laden (Abschnitte 4 und 5):** Voraussetzung ist, dass Plan 4b ausgerollt ist
  (Betriebshandbuch, „Laderegelung“, „Geplantes Laden ausrollen (Plan 4b)“), also nach der Abnahme der
  Phase 3. Für Abschnitt 4 laden EVCS und Tesla live (Betriebshandbuch, „Laderegelung“, „Umstieg von
  evcc“): Im Dry-Run zählt die app die geplanten Ladungen zwar auch, aber dann lädt evcc, und das
  Ergebnis sagt nichts über BindaEMS.

| Angabe | Wert |
|---|---|
| Anlage | |
| Version core / app (UI → System) | |
| Ausgerollt am | |
| Prüfzeitraum Statistik von – bis (30 Tage) | |
| Plan 4b ausgerollt am | |
| EVCS und Tesla live seit | |
| Prüfzeitraum Ziel-SOC von – bis | |
| Prüfer | |

---

## 1. Abrechnung über 30 Tage innerhalb ±1 % der Zählerstände

**Fundort im UI:** Statistik → „Monat“ → Karte „Abgleich mit den Zählern“. Die Karte gilt für einen
Kalendermonat; die 30 Tage beginnen selten am Ersten und liegen oft in zwei Monaten. Dann nimmst du
den Abruf der API, dessen Summen sich über den ganzen Zeitraum bilden:

```bash
curl -b jar "$APP/api/ledger/checks?from=JJJJ-MM-TT&to=JJJJ-MM-TT"
```

(`jar` und `$APP` wie im Betriebshandbuch, Abschnitt 8, „Alternative für Skripte (curl)“.) Der Abgleich
vergleicht je Tag die Summe der Abrechnung mit der Differenz der Zählerstände um Mitternacht
(Betriebshandbuch, Abschnitt 8, „Abgleich mit den Zählern“).

Prüfschritte:

1. Wenige Tage nach dem Ende der 30 Tage den Abruf ausführen (der Abgleich der letzten Tage läuft
   nachts um 02:45 Uhr). `days` enthält je Tag und Größe eine Zeile, `totals` die Summen.
2. In `totals` stehen für `import` (Netzbezug) und `export` (Einspeisung) `ledger_kwh`, `counter_kwh`
   und `deviation_pct`. Beide Abweichungen liegen innerhalb ±1 %.
3. `totals` zählt nur Tage mit Zählerwert. Prüfe in `days`, dass alle 30 Tage dabei sind: keine
   Zeile mit `counter_kwh` `null` und dem Grund „Zählerstand fehlt“ oder „Zähler zurückgesetzt“.
   Fehlt ein Tag, zählt er nicht mit; begründe ihn in Abschnitt 2 und prüfe ihn von Hand wie in
   Abschnitt 3.
4. Die Wallboxen (`wb:<Schlüssel>`) notierst du mit; sie sind kein Abnahmekriterium. PV (`pv`) wird
   gezeigt, aber nicht bewertet, denn DC-gekoppelte PV hat keinen Zähler.
5. Im Kopf der Statistik („Monat“) steht „{n} Viertelstunden geschätzt“, wenn die app Lücken aus den
   Zählerständen nachgerechnet hat (Betriebshandbuch, „Lücken nachrechnen“). Eine Schätzung rechnet
   aus denselben Zählern und verbessert den Abgleich an diesen Tagen von selbst. Trage die Zahl ein
   und vermerke die Tage mit Schätzung in Abschnitt 3.

Nachweis: Ausgabe von `GET /api/ledger/checks` (`totals`) oder Bildschirmfoto der Karte.

| Größe | Abrechnung (kWh) | Zähler (kWh) | Abweichung (%) | innerhalb ±1 % | geprüfte Tage |
|---|---|---|---|---|---|
| Netzbezug (`import`) | | | | ☐ | von 30 |
| Einspeisung (`export`) | | | | ☐ | von 30 |
| PV (`pv`), nur notiert | | | – | – | von 30 |
| Wallbox ______ , nur notiert | | | | – | von 30 |
| Wallbox ______ , nur notiert | | | | – | von 30 |

Geschätzte Viertelstunden im Zeitraum: ________ (Tage: ________________)

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 2. Markierte Tage haben eine Ursache

**Fundort im UI:** Statistik → Karte „Abgleich mit den Zählern“, Zeile „Mehr als 1 % Abweichung am
{Tage}.“; in der API `flagged` in `days`.

Ein Tag ist markiert bei mehr als 1 % und mehr als 0,1 kWh Abweichung. Auch bei bestandener
Gesamtsumme (Abschnitt 1) darf kein Tag ohne Erklärung bleiben: Einzelne Abweichungen können sich
über 30 Tage ausgleichen.

Prüfschritte:

1. Alle markierten Tage der 30 Tage aus der Karte oder aus `days` (`flagged`) in die Tabelle
   übernehmen, mit Größe und Abweichung.
2. Je Tag die Ursache suchen und eintragen, etwa:
   - **Lücke in der Abrechnung** (app, core oder InfluxDB waren aus): Verlauf → Tagesbilanz zeigt
     eine Abdeckung unter 100 %; nach einer Nachrechnung steht der Tag geschätzt.
   - **Zählertausch oder Zähler zurückgesetzt:** der Tag trägt „Zähler zurückgesetzt“ und ist nicht
     markiert, sondern ungeprüft.
   - **Preise oder Tarif geändert:** ändert nur die Kosten, nie die Energie; keine Ursache einer
     Abweichung der Energie.
   - **Zeitumstellung:** Der 25.10.2026 hat 100 Viertelstunden; die Abdeckung muss „100 von 100“
     lauten.
   - **ohne erkennbare Ursache:** ein Befund; Tag und Werte festhalten und melden.
3. Tage ohne Markierung, aber mit Grund „Zählerstand fehlt“ oder „Zähler zurückgesetzt“ (`note`, dabei
   ist `counter_kwh` `null`) sind ungeprüft und zählen nicht in `totals`. Ein Tag mit „Zähler zeigt
   0 kWh“ ist nie markiert, geht aber mit seinen Werten in `totals` ein: Ohne Bezug oder Einspeisung
   an dem Tag (Abrechnung ebenfalls 0) ist das erwartet; steht in der Abrechnung dort mehr als 0 kWh,
   ist es ein Befund (der Zähler stand still oder die Abrechnung hat Energie, die es nicht gab), und
   die Summe in `totals` weicht dadurch ab. Trage ihn in die Tabelle ein.

| Tag | Größe | Abweichung (%) | Ursache | erklärt |
|---|---|---|---|---|
| | | | | ☐ |
| | | | | ☐ |
| | | | | ☐ |
| | | | | ☐ |

Keine markierten Tage: ☐

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 3. Einzelne Tage gegen den Zähler des Netzbetreibers

**Fundort im UI:** Verlauf → Tagesbilanz (Spalten „Bezug“ und „Einspeisung“ in kWh), oder
`GET /api/stats?period=day&date=JJJJ-MM-TT` (`energy.import_kwh` und `energy.export_kwh`).

Der Abgleich nutzt die Zähler, die der core liest. Zusätzlich prüfst du einige Tage gegen den Zähler
des Netzbetreibers (Smart Meter, Portal des Netzbetreibers oder Ablesung), denn nur er ist von
BindaEMS unabhängig.

Prüfschritte:

1. Mindestens 3 Tage wählen, darunter ein sonniger Tag mit Einspeisung und, wenn vorhanden, ein
   Tag, der im Abgleich markiert war (Abschnitt 2).
2. Beim Netzbetreiber Bezug und Einspeisung des Tages ablesen (Tageswerte von Mitternacht zu
   Mitternacht, Ortszeit).
3. In BindaEMS dieselben Größen für denselben Tag ablesen.
4. Abweichung in % = (BindaEMS − Netzbetreiber) / Netzbetreiber × 100. Jede Größe liegt innerhalb
   ±1 %. Eine größere Abweichung erklärst du (etwa Zeitpunkt der Ablesung, Auflösung des Zählers,
   Lücke in der Abrechnung, Abschnitt 2).

| Tag | Größe | Netzbetreiber (kWh) | BindaEMS (kWh) | Abweichung (%) | innerhalb ±1 % |
|---|---|---|---|---|---|
| | Netzbezug | | | | ☐ |
| | Einspeisung | | | | ☐ |
| | Netzbezug | | | | ☐ |
| | Einspeisung | | | | ☐ |
| | Netzbezug | | | | ☐ |
| | Einspeisung | | | | ☐ |

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 4. Ziel-SOC

Spec 18, Phase 4: Ziel-SOC in mindestens 95 % der machbaren geplanten Sessions erreicht.

**Fundort im UI:** Statistik → „Monat“ → Karte „Geplantes Laden“: „{a} von {b} machbaren Ladungen
erreicht ({pct} %)“, darunter „Nicht machbar: {n} · Vor der Abfahrt abgesteckt: {n} · Ohne Ladestand:
{n}“ und „Vor der Abfahrt geändert oder verlassen: {n}“. Die Zahlen stehen auch in der API:

```bash
curl -b jar "$APP/api/stats?period=month&date=JJJJ-MM-TT"
```

(`jar` und `$APP` wie im Betriebshandbuch, Abschnitt 8, „Alternative für Skripte (curl)“.) Der Teil
`planned` der Antwort zählt die geplanten Ladungen mit Abfahrt im Monat: `reached` von `feasible`
machbaren erreicht, `missed` machbar und verfehlt, dazu `infeasible` (nicht machbar), `early` (vor der
Abfahrt abgesteckt), `unknown` (ohne Ladestand) und `changed` (vor der Abfahrt geändert oder
verlassen). Nur `reached` und `missed` gehen in die Quote; die Regeln stehen im Betriebshandbuch,
Abschnitt 8, „Statistik je Zeitraum“, „Geplantes Laden“.

**Prüfzeitraum:** mindestens ein ganzer Kalendermonat, in dem EVCS und Tesla die ganze Zeit live waren
und die Autos regelmäßig „Geplant“ laden (etwa jede Nacht mit Abfahrt am Morgen). Eine Ladung zählt im
Monat ihrer Abfahrt. Je mehr machbare Ladungen, desto aussagekräftiger: Bei 20 machbaren entspricht
schon eine verfehlte 5 %. Reicht ein Monat dafür nicht, nimm den nächsten dazu und addiere die Zahlen.

Prüfschritte:

1. Nach dem Ende des Prüfzeitraums (die letzte Abfahrt muss vorbei sein; offene Ladungen zählen nicht)
   `GET /api/stats?period=month` für jeden Monat abrufen und `planned` in die Tabelle übernehmen.
2. Quote = `reached` ÷ `feasible` × 100, über alle Monate summiert. Bestanden bei mindestens 95 %.
3. **Jede verfehlte Ladung** (`missed`) bekommt eine Begründung. Die Monate oder Tage mit `missed` über
   0 findest du mit `GET /api/stats?period=day&date=JJJJ-MM-TT`; die einzelnen Zeilen liest du aus der
   Tabelle `planned_charge` der app, nur lesend (nur die machbaren, `feasible = 1`, wie `missed`; eine
   nicht machbare verfehlte zählt unter `infeasible`):

   ```bash
   cd /opt/bindaems
   docker compose exec ems-app python -c "import sqlite3; db = sqlite3.connect('file:/data/bindaems.sqlite3?mode=ro', uri=True); [print(r) for r in db.execute(\"select vehicle, departure_at, target_soc_pct, started_at, start_soc_pct, start_soc_source, end_soc_pct, end_soc_source, outcome from planned_charge where outcome = 'missed' and feasible = 1 order by departure_at\")]"
   ```

   Zu jeder Zeile siehst du in den **Entscheidungen** (Fahrzeug, Zeitraum vom Beginn bis zur
   Abfahrt) und auf der Seite **Zeitplan** nach, warum das Ziel fehlte, etwa:
   - **Sperre oder Ausfall:** Der Aktor war gesperrt oder nicht steuerbar, Tessie oder die Wallbox
     fielen aus, der core stand (Alarme unter **System**).
   - **Leistung begrenzt:** Der Phasenwächter, Überlast oder der 15-min-Wächter hielten das Auto unter
     der geplanten Leistung (Gründe `phase_limit`, `overload`, `peak_guard`).
   - **Ladestand falsch geschätzt:** Der e-Golf meldete an der Abfahrt weniger als geschätzt.
   - **Auto hörte selbst auf:** Ladelimit in der Tesla-App gesenkt (ohne `charge_limit_sync`), Akku
     des Autos kalt.
   - **ohne erkennbare Ursache:** ein Befund; Zeile und Entscheidungen festhalten und melden.
4. Nicht machbare, abgesteckte, unbekannte und geänderte Ladungen zählen nicht in die Quote. Fällt
   eine Zahl davon auf (etwa viele `unknown`, weil der Ladestand des e-Golf fehlt, oder viele
   `infeasible`, weil die Autos spät angesteckt werden), notiere sie mit einer Erklärung.

Nachweis: Ausgabe von `GET /api/stats?period=month` (`planned`) je Monat oder Bildschirmfoto der Karte,
dazu die Zeilen der verfehlten Ladungen.

| Monat | `reached` | `feasible` | `missed` | `infeasible` | `early` | `unknown` | `changed` |
|---|---|---|---|---|---|---|---|
| | | | | | | | |
| | | | | | | | |
| Summe | | | | | | | |

Quote `reached` ÷ `feasible`: ________ % ☐ mindestens 95 %

| Fahrzeug | Abfahrt | Ziel (%) | Ladestand an der Abfahrt (%) | Begründung | erklärt |
|---|---|---|---|---|---|
| | | | | | ☐ |
| | | | | | ☐ |
| | | | | | ☐ |

Keine verfehlte Ladung: ☐

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## 5. HA in beide Richtungen

Spec 18, Phase 4: Alle HA-Bedienelemente wirken in beide Richtungen. Ein Test der app prüft das je
Bedienelement mit der Demo-Welt (`tests/integration/test_ha_round_trip.py`); an der Anlage prüfst du
es einmal von Hand.

Prüfschritte, je Zeile der Tabelle:

1. **In Home Assistant ändern und im UI sehen:** Den Wert in Home Assistant ändern (bei einem
   Bedienelement je Fahrzeug oder je Wallbox an einem davon), dann im UI auf der genannten Seite
   prüfen, dass er dort steht (die Seite lädt den Stand höchstens nach einigen Sekunden neu). Wo die
   Bedienung im Änderungsprotokoll steht (Seite **Protokoll**, nur Admins), trägt sie die Quelle
   `ha`.
2. **Im UI ändern und in Home Assistant sehen:** Den Wert im UI ändern (ab „Bedienen“), dann in Home
   Assistant prüfen, dass die Entität ihn zeigt. Eine **Taste ohne Zustand** (Boost, Boost beenden,
   Neu planen) hat keine Richtung UI → Home Assistant; dort prüfst du nur die Wirkung aus Home
   Assistant.
3. Den alten Wert danach wiederherstellen. „Regelbetrieb“ und „Lademodus“ änderst du nur in einem
   Moment, in dem das niemanden stört (etwa kein Auto lädt); ein Wechsel des Regelbetriebs löst die
   Übergabe aus (Betriebshandbuch, Abschnitt 13).

| Bedienelement | Gerät in HA | Seite im UI | HA → UI | UI → HA |
|---|---|---|---|---|
| Lademodus (je Fahrzeug) | „BindaEMS <Fahrzeug>“ | Laden, Fahrzeugkarte | ☐ | ☐ |
| Ziel-SOC (je Fahrzeug) | „BindaEMS <Fahrzeug>“ | Laden, Fahrzeugkarte in „Geplant“ | ☐ | ☐ |
| Abfahrtszeit (je Fahrzeug) | „BindaEMS <Fahrzeug>“ | Laden, Fahrzeugkarte in „Geplant“ („Abfahrt“) | ☐ | ☐ |
| Boost (je Fahrzeug) | „BindaEMS <Fahrzeug>“ | Laden, „Boost beenden“ und „bis HH:MM“ | ☐ | – (Taste ohne Zustand) |
| Boost beenden (je Fahrzeug) | „BindaEMS <Fahrzeug>“ | Laden, wieder „Boost“ | ☐ | – (Taste ohne Zustand) |
| Automatik Ziel und Abfahrt (je Fahrzeug) | „BindaEMS <Fahrzeug>“ | Laden, Fahrzeugkarte („Ziel und Abfahrt automatisch“) | ☐ | ☐ |
| Zuordnung (je Wallbox) | „BindaEMS EVCS“ / „BindaEMS Wall Connector“ | Laden, Wallbox-Karte | ☐ | ☐ |
| Regelbetrieb | „BindaEMS“ | System, „Bedienung“ | ☐ | ☐ |
| Gast-Lademodus | „BindaEMS“ | Laden, „Gast“ | ☐ | ☐ |
| Netzladen erlaubt | „BindaEMS“ | Zeitplan | ☐ | ☐ |
| Akku-Unterstützung erlaubt | „BindaEMS“ | Laden, „Akku-Unterstützung“ | ☐ | ☐ |
| Unterstützungsschwelle | „BindaEMS“ | Laden, „Akku-Unterstützung“ | ☐ | ☐ |
| Neu planen | „BindaEMS“ | Zeitplan, Anlass „Neu planen“ | ☐ | – (Taste ohne Zustand) |

Ein ungültiger Wert aus Home Assistant (etwa Ziel-SOC 10 oder Abfahrtszeit „7 Uhr“) ändert nichts und
erscheint als persistente Meldung „Befehl aus Home Assistant abgelehnt: …“ (Betriebshandbuch,
Abschnitt 7): ☐ geprüft

Datum: ________ Ergebnis: ☐ bestanden ☐ nicht bestanden Unterschrift: ________

---

## Gesamtergebnis

☐ Teil Statistik (Plan 4a, Abschnitte 1 bis 3) abgenommen ☐ nicht abgenommen, offene Punkte:

☐ Teil Geplantes Laden (Plan 4b, Abschnitte 4 und 5) abgenommen ☐ nicht abgenommen, offene Punkte:

Phase 4 ist abgenommen, wenn beide Teile bestanden sind.

Datum: ________ Unterschrift: ________

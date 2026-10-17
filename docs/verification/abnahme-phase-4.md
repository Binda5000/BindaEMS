# Abnahme Phase 4 – Checkliste, Teil Statistik

Spec Abschnitt 18, Phase 4, „Abnahme“: Abrechnung über 30 Tage innerhalb ±1 % der Zählerstände;
alle HA-Bedienelemente wirken in beide Richtungen; Ziel-SOC in mindestens 95 % der machbaren
geplanten Sessions erreicht. Phase 4 ist in zwei Pläne geteilt (4a „Statistik“, 4b „Geplantes
Laden“). Diese Checkliste gilt für **Plan 4a**: Sie prüft den ersten Punkt, die Abrechnung. Die beiden
anderen Punkte stehen unten als „folgt mit Plan 4b“.

Diese Checkliste füllst du an der Anlage aus. Kopiere sie dafür nach
`docs/verification/JJJJ-MM-TT-abnahme-phase-4.md`, trage die Ergebnisse ein und checke sie ein.
Voraussetzung: Plan 4a ist ausgerollt (Betriebshandbuch, Abschnitt 8, „Statistik ausrollen (Plan 4a)“)
und OeMAG-Werte und Grundgebühren sind im Tarif eingetragen. Die 30 Tage beginnen mit dem Ausrollen;
die Abnahme der Phase 3 (Victron live, 30 echte Tage) ist davon unabhängig.

| Angabe | Wert |
|---|---|
| Anlage | |
| Version core / app (UI → System) | |
| Ausgerollt am | |
| Prüfzeitraum von – bis (30 Tage) | |
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
3. Tage ohne Markierung, aber mit Grund (`note`: „Zählerstand fehlt“, „Zähler zurückgesetzt“,
   „Zähler zeigt 0 kWh“) sind ungeprüft. Ein Grund „Zähler zeigt 0 kWh“ an einem Tag ohne Bezug
   oder Einspeisung ist erwartet.

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

## 4. Folgt mit Plan 4b

Die anderen beiden Punkte der Abnahme der Phase 4 gehören zu Plan 4b („Geplantes Laden“, der den core
ändert und nach der Abnahme der Phase 3 an die Anlage kommt). Sie sind in dieser Checkliste noch nicht
prüfbar:

| Punkt (Spec 18, Phase 4) | Stand |
|---|---|
| Alle HA-Bedienelemente wirken in beide Richtungen | folgt mit Plan 4b |
| Ziel-SOC in mindestens 95 % der machbaren geplanten Sessions erreicht | folgt mit Plan 4b |

---

## Gesamtergebnis

☐ Teil Statistik (Plan 4a) abgenommen ☐ nicht abgenommen, offene Punkte:

Phase 4 ist erst abgenommen, wenn auch die beiden Punkte aus Abschnitt 4 mit Plan 4b bestanden sind.

Datum: ________ Unterschrift: ________

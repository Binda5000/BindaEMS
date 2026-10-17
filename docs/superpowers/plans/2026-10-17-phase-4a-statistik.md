# Phase 4a – Statistik: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die app rechnet die Abrechnung zu einer vollständigen Statistik aus und zeigt sie:
- Ladesessions je Wallbox mit Fahrzeug, Energie (PV, Akku, Netz) und Kosten.
- Einstandspreis des Akkus und damit Kosten je Auto (Spec 11.4).
- Täglicher Abgleich der Abrechnung mit den Zählerständen, Markierung ab 1 % Abweichung.
- Nachrechnung von Lücken aus den Zählerständen, als „geschätzt“ markiert.
- Energie je Verbraucher auch für vergangene Tage.
- Seite „Statistik“ für Tag, Woche, Monat und Jahr mit Monatsspitzen, Leistungspreis und Grundgebühren.
- Statistik-Sensoren in Home Assistant und ein Grafana-Startdashboard.

**Architecture:** 4a ändert nur app, UI, Tests und `deploy/`; core und `shared` bleiben unverändert.
- **`app/ledger/`:** `costing.py` (Einstandspreis je Slot), `counters.py` (Zählerstände aus InfluxDB), `checks.py` (Abgleich je Tag), `estimate.py` (Nachrechnung von Lücken).
- **`app/charging/`** (neu): `plug.py` (Steckzustand aus den Signalen), `tracker.py` (Belegung und Sessions aus State und Health), `sessions.py` (Energie und Kosten je Session und je Auto), `routes.py`.
- **`app/stats/`** (neu): `service.py` (Kennzahlen je Zeitraum), `routes.py`.
- **`app/consumers/`:** `EnergyCache` rechnet auch einen vergangenen Tag; `days.py` speichert die Tageswerte.
- **Migration `0010`:** `assignment_log`, `charging_session`, `ledger_cost`, `ledger_check`, `consumer_day`.
- **UI:** Seite „Statistik“, Session in der Fahrzeugkarte auf „Laden“.
- **`deploy/grafana/`:** Startdashboard als JSON.

**Tech Stack:** wie Plan 3c. Keine neuen Abhängigkeiten, keine neuen Schlüssel in `config.yaml`.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, vor allem 1.3, 9.1 („Gebühren“), 11.1, 11.3, 11.4, 12, 13, 15 und 18 (Phase 4).
- Verweise wie „Spec 11.4“ beziehen sich darauf.
- Die Absätze „Stand Plan …“ der Spec gehen dem älteren Text davor vor; die Präzisierungen unten gehen beiden vor.
- Vorgänger ist Plan 3c (`docs/superpowers/plans/2026-10-17-phase-3c-planausfuehrung.md`, auf `main` seit `7c522c4`). Seine Global Constraints und die der Pläne davor gelten weiter.

## Einordnung

Phase 4 hat laut Spec 18 vier Teile: Modus „Geplant“, Statistik, HA vollständig, Grafana. Der Betreiber hat am 17.10.2026 entschieden, mit der Statistik zu beginnen.
- **Warum zuerst:** Die Statistik hängt nicht von den Ergebnissen an der Anlage ab. Sie ändert den core nicht und stört damit weder den Dry-Run der Planausführung noch die Abnahme der Phase 3. Die 30 Tage für die Abnahme der Abrechnung (±1 %, Spec 18) können laufen, während Phase 3 abgenommen wird.
- **Ergebnis an der Anlage:** Nur `ems-app` wird neu gebaut und gestartet (`docker compose up -d --build ems-app`). Der core läuft weiter, der Regelbetrieb muss dafür nicht aus.
- **Danach:** Plan 4b „Geplantes Laden“ (Entscheidung 1).

## Vorgeschlagene Entscheidungen (zur Freigabe)

Der Plan ist auf diese Vorschläge geschrieben; eine andere Wahl ändert die genannten Tasks.

**Freigabe:** Der Betreiber hat den Plan am 17.10.2026 freigegeben, mit einer Änderung an den Entscheidungen 4 und 5: PV-Strom, der ins Auto geht (direkt oder über den Akku), ist kostenlos statt mit dem OeMAG-Wert bewertet.

1. **Zuschnitt der Phase 4** (Spec 18).
   - **4a „Statistik“ (dieser Plan):** Ladesessions, Einstandspreis, Kosten je Auto, Abgleich und Nachrechnung, Verbraucher je Tag, Seite „Statistik“, Monatsspitzen, Statistik-Sensoren in HA, Grafana.
   - **4b „Geplantes Laden“:** Modus „Geplant“ mit Ziel-SOC und Abfahrt, die Autos im Planer, Regel R2, späteste Startzeit, Tesla-Ladelimit, in HA Ziel-SOC, Abfahrtszeit, „Fertig um“ und „Ziel erreichbar“, Benachrichtigungen komplett. 4b ändert core und Optimierer und kommt nach der Abnahme der Phase 3 an die Anlage.
2. **Nur app, core unverändert** (alle Tasks). Kein neuer Typ in `shared`: `ChargingSession` (Spec 11.1) lebt in `app/charging/sessions.py`, denn nur die app braucht sie. So bleibt das core-Image gleich, und das Ausrollen braucht keinen Neustart des core.
3. **Ladesessions aus dem Steckzustand** (Tasks 3, 4).
   - Eine Session ist die Zeit, in der an einer Wallbox ein Auto steckt. Den Steckzustand liest die app aus dem State des core: EVCS `wallbox.<id>.status` ungleich 0, Wall Connector `wallbox.<id>.vehicle_connected`. Das gilt auch bei Regelbetrieb aus.
   - Das Fahrzeug kommt aus der Belegung in Health (`wallboxes[].occupant`, alle 10 s), die als `assignment_log` gespeichert wird. Es ist das Fahrzeug mit der längsten beobachteten Zeit in der Session; ohne Beobachtung „Unbekannt“, ein Gast „Gast“.
   - Nach einer Pause der Beobachtung (core oder app aus) läuft eine Session weiter, wenn das Auto noch steckt und die Pause höchstens 30 min dauerte; sonst endet sie bei der letzten Beobachtung.
   - Alternative: Sessions aus der Ladeenergie der Abrechnung (Folgen von Slots mit Energie). Sie trennte jede PV-Pause und kennte keine Steckzeiten.
4. **Einstandspreis des Akkus** (Spec 11.4; Task 2).
   - **PV-Strom ist kostenlos** (Entscheidung des Betreibers vom 17.10.2026, abweichend von Spec 11.4): PV-Ladung zählt mit 0 ct, Netzladung mit dem Bezugspreis. Der Mittelwert über den Inhalt ist damit der Netzanteil zum Bezugspreis. Der Inhalt ist auf `battery.usable_kwh` begrenzt, damit Wandlungsverluste nicht endlos Energie ansammeln.
   - Eine Entladung kostet den Mittelwert zu Beginn des Slots plus Verschleiß (`planning.wear_ct`, Standard 2 ct/kWh). Bei leerem Inhalt gilt nur der Verschleiß.
   - Ein fehlender Bezugspreis zählt mit dem aktuellen Mittelwert; sobald er bekannt ist (Nachtrag), wird ab dort neu gerechnet.
5. **Kosten je Auto** (Spec 11.4, mit Entscheidung 4): Netz → Auto × Bezugspreis + PV → Auto × 0 + Akku → Auto × Einstandspreis, je Slot der Session zugeordnet, die an der Wallbox lief (Task 5). Fehlt ein Bezugspreis, ist die Summe „unbekannt“, wie bei den Tageskosten. Der entgangene Einspeiseerlös erscheint nicht in den Kosten je Auto; die Tagesbilanz (Einspeiseerlös, Ersparnis) bleibt davon unberührt.
6. **Abgleich** (Spec 11.4; Task 6).
   - Täglich um 02:45 Uhr für die letzten 7 vollständigen Tage, beim Start für die letzten 35.
   - Verglichen werden Netzbezug, Einspeisung und jede Wallbox: die Summe der Abrechnung gegen die Differenz der Zählerstände um Mitternacht (`energy` in InfluxDB, letzter Wert in der halben Stunde vor Mitternacht).
   - **Markierung**, wenn die Abweichung mehr als 1 % und mehr als 0,1 kWh beträgt. PV wird gezeigt, aber nicht markiert, denn DC-gekoppelte PV hat keinen Zähler.
   - Fehlt ein Zählerstand oder sinkt er (Zählertausch), steht der Tag ohne Markierung mit dem Grund da.
7. **Nachrechnung von Lücken** (Spec 11.4; Task 7).
   - Eine Lücke ist eine Folge von Viertelstunden ohne Abrechnung, höchstens 7 Tage lang, mit Zählerständen an beiden Rändern (höchstens 15 min entfernt). Netzbezug und Einspeisung kommen aus dem Netzzähler, PV und Wallboxen aus ihren Zählern, der Akku aus dem SOC an den Rändern mal `battery.usable_kwh`, das Haus aus der Bilanz. Würde das Haus negativ, ist der Rest nicht gezählte DC-PV.
   - Die Energie wird gleichmäßig auf die Viertelstunden verteilt und nach der Regel des core (Spec 11.4) den Flüssen zugeordnet. Die Slots tragen `origin = "estimate"` und `covered_s = 0`; echte Daten ersetzen sie, die Abdeckung bleibt ehrlich.
   - Läuft nachts vor dem Abgleich.
8. **Verbraucher je Tag** (Task 8). Die app speichert die Energie je Verbraucher je Tag in `consumer_day`, nachts für gestern und beim Start für die fehlenden Tage der letzten 31. Es gilt dieselbe Rechnung wie für „heute“ (Spec 13, Verbraucher). Ältere Tage bleiben leer, denn die Rohdaten in InfluxDB halten 90 Tage.
9. **Seite „Statistik“** (Spec 13; Tasks 9, 12, 13).
   - Zeiträume Tag, Woche (Montag bis Sonntag), Monat, Jahr, mit Blättern und „Heute“.
   - Kacheln: Autarkie, Eigenverbrauch, Kosten, Ersparnis, Einspeiseerlös. Diagramm „Energie“ je Stunde (Tag), je Tag (Woche, Monat) oder je Monat (Jahr).
   - Karten: „Kosten“ (mit Grundgebühren und, für Monat und Jahr, Leistungspreis), „Autos“ (mit Akku → Autos), „Ladesessions“, „Verbraucher“, „Monatsspitzen“ (Monat und Jahr), „Abgleich mit den Zählern“.
   - Die Ersparnis bleibt wie heute ohne Grundgebühren und Leistungspreis (Plan 1b, Präzisierung 8): Grundgebühren fielen auch ohne Anlage an, und den Leistungspreis „ohne Anlage“ kennt niemand.
10. **Grundgebühren und Leistungspreis in der Statistik** (Spec 9.1). Grundgebühren zählen anteilig je Tag (`monthly_fees_eur(day) / Tage im Monat`). Der Leistungspreis zählt je Monat aus der Monatsspitze (`power_cost_eur`), nur in Monat und Jahr, und nur, wenn im Monat ein Leistungspreis gilt.
11. **Home Assistant** (Spec 12; Task 10): Sensoren „Ersparnis heute“, „Ersparnis Monat“, „Autarkie heute“ und je Fahrzeug „Ladeenergie“ (laufende oder letzte Session). Sie werden bei jedem neuen Slot und alle 15 min neu gerechnet, nicht im 5-s-Takt der übrigen Werte.
12. **Grafana** (Spec 15; Task 15): ein Startdashboard `deploy/grafana/bindaems.json` für die InfluxDB `bindaems` mit Leistungen, SOC, Preisen, Abrechnung je Tag und den Entscheidungen als Markierungen. Die Datenquelle wählt man beim Import.
13. **Abnahme** (Spec 18; Task 16): `docs/verification/abnahme-phase-4.md` beschreibt den Teil der Statistik: 30 Tage ab dem Ausrollen, Netzbezug und Einspeisung im Abgleich innerhalb ±1 %. Die beiden anderen Punkte der Abnahme (HA in beide Richtungen, Ziel-SOC) folgen mit 4b.

## Präzisierungen gegenüber der Spec

1. **Zuschnitt:** Aus Spec 18, Phase 4, kommen „Statistik“, „Grafana“ und die Statistik-Sensoren in 4a. Alles zum Modus „Geplant“ kommt mit 4b (Entscheidung 1).
2. **Migration `0010`** (`v0010_statistics.py`, `revision = "0010"`):
   - `assignment_log`: `id` (aufsteigend), `ts`, `wallbox` (String 32), `occupant` (String 32, Schlüssel des Fahrzeugs, `guest` oder leer), `kind` (String 10, Wert von `AssignmentKind` oder leer); Index auf `(wallbox, ts)`.
   - `charging_session`: `id` (aufsteigend), `wallbox`, `vehicle` (String 32, Schlüssel, `guest` oder leer), `start`, `end` (leer, solange offen), `last_seen`; Index auf `start`.
   - `ledger_cost`: `slot_start` (Primärschlüssel), `stock_kwh`, `avg_ct` (leer ohne Inhalt), `discharge_ct`.
   - `ledger_check`: `day` (Datum als Text, `JJJJ-MM-TT`), `signal` (String 40), `ledger_kwh`, `counter_kwh` (leer), `deviation_pct` (leer), `flagged` (bool), `note` (String 80, leer), `checked_at`; Primärschlüssel `(day, signal)`.
   - `consumer_day`: `day`, `consumer_id`, `kwh` (leer), `note` (String 80, leer); Primärschlüssel `(day, consumer_id)`.
   - Zeiten in `UtcDateTime` wie `ledger_slot`.
3. **Herkunft `estimate`:** `Origin = Literal["stream", "influx", "estimate"]`. `DaySummary` bekommt `estimated_slots: int`. Die Abdeckung zählt geschätzte Slots mit `covered_s = 0`.
4. **Einstandspreis je Slot** (`costing.py`), chronologisch, mit Zustand `(stock_kwh, avg_ct)` vom Slot davor (Startzustand `(0, None)`):
   - `discharge_ct = (avg_ct oder 0) + wear_ct`.
   - Entladung zuerst: `stock = max(0, stock − Σ battery>*)`.
   - Dann Ladung: `e_pv = pv>battery` zu 0 ct, `e_grid = grid>battery` zu `import_price_ct`; ein fehlender Bezugspreis zählt mit `avg_ct`; ohne `avg_ct` zählt der Teil nicht in den Mittelwert, nur in den Inhalt.
   - `avg = (avg · stock + Σ p · e) / (stock + Σ e)` über die Teile mit Preis; `stock = min(stock + e_pv + e_grid, usable_kwh)`.
   - Ändert sich ein Slot oder ein Preis, wird ab dem frühesten geänderten Slot neu gerechnet (`mark_dirty`); eine Änderung von `planning.wear_ct` rechnet alles neu (der Tarif wirkt über `reprice`, das die Slots markiert).
5. **Steckzustand** (`plug.py`): `wallbox_connected(cfg, key, value) -> bool | None` mit `value` wie `LiveState.value`. EVCS: `status` ist eine Zahl, verbunden ist `status != 0`. Wall Connector: `vehicle_connected` als bool. Unbekannt, wenn das Signal fehlt. Ein Test vergleicht das mit `core/control/charge_inputs.py` (die Tests dürfen beide importieren).
6. **Sessions** (`tracker.py`), je Beobachtung `(ts, je Wallbox: connected, occupant, kind)` nach jeder Antwort von Health:
   - `connected` wahr ohne offene Session: neue Session ab `ts`.
   - `connected` falsch mit offener Session: Ende `ts`.
   - `connected` unbekannt: nichts, `last_seen` bleibt.
   - Liegt `last_seen` mehr als `SESSION_GAP = 30 min` vor `ts`, endet die offene Session bei `last_seen`; steckt das Auto, beginnt eine neue.
   - `last_seen` wird höchstens jede Minute gespeichert, Anfang und Ende sofort.
   - `assignment_log` bekommt eine Zeile, wenn sich `(occupant, kind)` einer Wallbox ändert; die erste Beobachtung nach dem Start schreibt immer.
   - **Fahrzeug der Session:** aus `assignment_log` im Zeitraum der Session, das nicht leere `occupant` mit der längsten Dauer (bei Gleichstand das spätere); `vehicle` wird bei jeder Änderung der Belegung und beim Ende neu bestimmt.
7. **Slot und Session:** Ein Slot gehört an einer Wallbox zu der Session mit der größten Überdeckung (bei Gleichstand der späteren). Wallbox-Energie ohne Session zählt als „Unbekannt“.
8. **Zählerstände** (`counters.py`): `readings_at(signals, instants) -> dict[(signal, instant), float | None]` aus `"long"."energy"` mit `last("kwh")` in `[instant − 30 min, instant)`, eine Abfrage `GROUP BY time(30m),"source","id","direction"` über den ganzen Zeitraum. Die Signale bilden sich auf die Tags ab wie im core (Tabelle unten). Für die Ränder einer Lücke liest `reading_near(signal, instant, before: bool)` aus `"raw"."energy"` den letzten Wert in `[instant − 15 min, instant]` bzw. den ersten in `[instant, instant + 15 min]`; ebenso der SOC aus `"raw"."soc"` (`id = "battery"`, Feld `pct`).

   | Signal | `source` | `id` | `direction` |
   |---|---|---|---|
   | `grid.energy_import_kwh` | `grid` | `grid` | `import` |
   | `grid.energy_export_kwh` | `grid` | `grid` | `export` |
   | `pv.<X>.energy_kwh` | `pv` | `X` | `forward` |
   | `wallbox.<X>.total_kwh` | `wallbox` | `X` | `total` |

9. **Abgleich je Tag** (`checks.py`): Signale `import`, `export`, `pv` (ohne Markierung) und `wb:<id>` je Wallbox.
   - `ledger_kwh`: `import_wh`, `export_wh`, Σ `pv>*` bzw. Σ `*>wb:<id>` der Slots des Tages, geschätzte eingeschlossen.
   - `counter_kwh`: Zählerstand um Mitternacht danach minus davor; PV über alle `pv.*`-Zähler.
   - `deviation_pct = (ledger − counter) / counter · 100`, bei `counter_kwh = 0` leer.
   - `flagged = |deviation_pct| > 1 and |ledger − counter| > 0.1` (nicht für `pv`).
   - Gründe in `note`: Texte unten.
10. **Nachrechnung** (`estimate.py`): `allocate_wh(pv, battery_net, house, wallboxes)` ist dieselbe Wasserfall-Regel wie `core/accounting/flows.py::allocate` (ein Property-Test vergleicht beide). Je Lücke mit `n` Slots: `import`, `export`, `pv`, `wb_X` aus den Zählern, `battery_net = (soc_end − soc_start) / 100 · usable_kwh` (ohne SOC 0), `house = import − export + pv − Σ wb − battery_net`; ist `house < 0`, dann `pv −= house` und `house = 0`. Je Slot ein `n`-tel; `grid` folgt als Rest.
11. **Statistik je Zeitraum** (`stats/service.py`): Grundlage sind `LedgerService.days`, die Slots, `ledger_cost`, die Sessions, `consumer_day` und `ledger_check`. Woche nach ISO (Montag bis Sonntag). Die Buckets: Tag 24 (bzw. 23 oder 25) Stunden in Wiener Zeit, Woche und Monat je Tag, Jahr je Monat.
12. **APIs** (alle ab „Lesen“):
    - `GET /api/stats?period=day|week|month|year&date=JJJJ-MM-TT` (Antwort unten); ein anderes `period` gibt 422.
    - `GET /api/charging/sessions?from=JJJJ-MM-TT&to=JJJJ-MM-TT[&vehicle=<key>]`: neueste zuerst, höchstens 400 Tage, höchstens 200 Einträge.
    - `GET /api/ledger/checks?from=…&to=…`: je Tag und Signal, dazu die Summen über den Zeitraum, höchstens 400 Tage.
13. **HA** (am Gerät „BindaEMS“): `savings_today` „Ersparnis heute“ und `savings_month` „Ersparnis Monat“ (€, `device_class` `monetary`, zwei Nachkommastellen, Variante „ohne Anlage“, Attribut `same_import_eur`), `autarky_today` „Autarkie heute“ (%, ganze Zahl, Attribut `month_pct`). Je Fahrzeug `<v>_charge_energy` „Ladeenergie“ (kWh, eine Nachkommastelle, `device_class` `energy`, `state_class` `measurement`; Attribute `start`, `end`, `pv_kwh`, `battery_kwh`, `grid_kwh`, `cost_eur`, `month_kwh`). Ohne Daten „unbekannt“.

### Antwort von `GET /api/stats`

```json
{
  "period": "month", "first": "2026-10-01", "last": "2026-10-31",
  "complete": false, "coverage": 0.98, "estimated_slots": 4,
  "energy": {"pv_kwh": 0, "import_kwh": 0, "export_kwh": 0, "house_kwh": 0, "wallbox_kwh": 0,
             "battery_charge_kwh": 0, "battery_discharge_kwh": 0, "consumption_kwh": 0},
  "money": {"import_eur": 0, "revenue_eur": 0, "net_eur": 0, "fees_eur": 0, "power_eur": null,
            "total_eur": 0, "savings_no_plant_eur": 0, "savings_same_import_eur": 0},
  "ratios": {"autarky": 0.6, "self_consumption": 0.8},
  "buckets": [{"start": "2026-10-01", "pv_kwh": 0, "import_kwh": 0, "export_kwh": 0,
               "consumption_kwh": 0, "net_eur": 0}],
  "vehicles": [{"key": "tesla", "name": "Tesla", "kwh": 0, "pv_kwh": 0, "battery_kwh": 0,
                "grid_kwh": 0, "cost_eur": 0, "sessions": 0}],
  "battery_to_vehicles_kwh": 0,
  "consumers": [{"id": 1, "name": "Küche", "kwh": 0, "note": null}],
  "month_peaks": [{"month": "2026-10", "kw": 6.4, "slot_start": "…", "power_eur": null}],
  "checks": {"days": 7, "flagged_days": ["2026-10-12"],
             "totals": [{"signal": "import", "ledger_kwh": 0, "counter_kwh": 0, "deviation_pct": 0.2}]}
}
```

- `buckets[].start` ist bei „Tag“ ein Zeitpunkt (ISO mit Zone), sonst ein Datum bzw. `JJJJ-MM`.
- `vehicles` in der Reihenfolge von `config.yaml`, dann `guest` („Gast“) und `unknown` („Unbekannt“), nur mit Energie über 0.
- `money.power_eur` und `month_peaks[].power_eur` nur bei „Monat“ und „Jahr“ und nur mit Leistungspreis, sonst `null`. `total_eur = net_eur + fees_eur + (power_eur oder 0)`, `null`, wenn `net_eur` unbekannt ist.
- Für Zahlen gilt die Rundung von `ledger/routes.py` (3 Stellen).

## Global Constraints

- **Gilt weiter:** alle Global Constraints aus 1a bis 3c, auch „Jede Task lässt das ganze Repository grün, auch den UI-Vertragstest und `tests/unit/test_docs.py`“.
- **Kein Eingriff in core und `shared`:** Keine Datei unter `src/bindaems/core/`, `src/bindaems/shared/` oder `cerbo-watchdog/` ändert sich. `lint-imports` bleibt grün; die app importiert nichts aus dem core (Tests dürfen beides).
- **Zeit:** Tage in Wiener Zeit (`LOCAL_TZ`, `local_day_slots`); Tage mit 92 und 100 Slots müssen in Buckets, Abgleich und Grundgebühren stimmen. Die app nimmt ihre `Clock`.
- **Unbekannt bleibt unbekannt:** Fehlt ein Preis oder Zählerstand, ist die Summe `None` (UI „–“), nie 0.
- **Echte Daten vor Schätzung:** Eine Nachrechnung überschreibt nie einen vorhandenen Slot.
- **Lasten:** Jede Abfrage an InfluxDB ist eine Abfrage je Zeitraum, nicht je Tag oder Slot. Die HA-Werte der Statistik entstehen nicht im 5-s-Takt.
- **Dokumentation unter Testzwang:** neue HA-Objekte, Endpunkte und Seiten stehen in `tests/unit/test_docs.py` und `docs/betrieb.md` und ändern sich in derselben Task.
- **UI:** wie in 3c (44 × 44 px, keine waagrechte Scrollleiste bei 360 px, valibot-Schema je Antwort, Vertragsdateien in `GET_ENDPOINTS` und `SCHEMAS`, Texte über `format.ts`, Diagramme über `EChart.svelte`).
- **Texte:** exakt wie in „Texte“; Tests prüfen den Wortlaut.
- **Öffentliches Repository:** keine Daten der Anlage; Werte in Tests und Demo sind erfunden.

## Review Focus

1. **Sommerzeitwechsel** (Tag mit 92 oder 100 Slots).
   - Erwartet: 23 bzw. 25 Stunden-Buckets, Abgleich mit den Zählerständen um die richtige Mitternacht, Grundgebühr des Tages einmal.
   - Tests: Task 9 `test_day_buckets_on_dst_days`, Task 6 `test_midnight_readings_on_dst_change`.
2. **Auto steckt über einen Neustart von app oder core.**
   - Erwartet: eine Session, solange die Pause höchstens 30 min dauert; nach längerer Pause endet sie bei der letzten Beobachtung und eine neue beginnt; abgezogen während der Pause heißt Ende bei der letzten Beobachtung.
   - Tests: Task 4 `test_session_survives_short_gap`, `test_long_gap_splits_the_session`, `test_unplugged_during_gap_ends_at_last_seen`, `test_open_session_is_resumed_after_restart`.
3. **Fehlende Preise und ihr Nachtrag.**
   - Erwartet: Kosten „unbekannt“, Einstandspreis ohne Sprung; nach `fill_missing_prices` oder `reprice` stimmen Einstandspreis und Kosten je Auto.
   - Tests: Task 2 `test_missing_price_counts_at_average`, `test_settings_change_recomputes_all`; Task 5 `test_vehicle_cost_unknown_without_price`.
4. **Zählertausch oder fehlender Zählerstand.**
   - Erwartet: keine Markierung, Grund „Zählerstand fehlt“ bzw. „Zähler zurückgesetzt“; die Summen über den Zeitraum lassen den Tag aus.
   - Tests: Task 6 `test_counter_reset_is_not_flagged`, `test_missing_reading_is_noted`, `test_totals_skip_unchecked_days`.
5. **Nachrechnung neben echten Daten.**
   - Erwartet: Eine Lücke ohne Zähler an einem Rand bleibt leer; echte Slots, die später kommen (Nachtrag aus InfluxDB), ersetzen die geschätzten; ein zweiter Lauf schätzt nichts doppelt.
   - Tests: Task 7 `test_gap_without_reading_stays_empty`, `test_real_slot_replaces_estimate`, `test_second_run_changes_nothing`.

## Texte

- **Navigation:** „Statistik“ (Symbol `stats`), nach „Verlauf“.
- **Zeiträume:** „Tag“, „Woche“, „Monat“, „Jahr“; Tasten „Zurück“, „Weiter“ (als Pfeile mit `aria-label`) und „Heute“. Überschrift: Tag „Fr., 09.10.2026“, Woche „KW 41 · 05.10.–11.10.2026“, Monat „Oktober 2026“, Jahr „2026“.
- **Kacheln:** „Autarkie“, „Eigenverbrauch“, „Kosten“, „Ersparnis“ (Untertitel „gegenüber Fixtarif ohne Anlage“), „Einspeiseerlös“.
- **Hinweise im Kopf:** „Abdeckung {pct} %“; mit Schätzung zusätzlich „{n} Viertelstunden geschätzt“; laufender Zeitraum „bis jetzt“.
- **Diagramm:** Titel „Energie“, Reihen „PV“, „Bezug“, „Einspeisung“, „Verbrauch“.
- **Karte „Kosten“:** Zeilen „Bezug“, „Einspeiseerlös“, „Netto“, „Grundgebühren“, „Leistungspreis“ (nur Monat und Jahr), „Summe“; darunter „Ersparnis ohne Anlage“ und „Ersparnis bei gleichem Netzbezug“.
- **Karte „Autos“:** Spalten „Auto“, „Energie“, „PV“, „Akku“, „Netz“, „Kosten“, „ct/kWh“, „Sessions“; Zeile „Akku → Autos“. Leer: „Im Zeitraum wurde kein Auto geladen.“
- **Karte „Ladesessions“:** Spalten „Beginn“, „Ende“, „Wallbox“, „Auto“, „Energie“, „PV / Akku / Netz“, „Kosten“; offene Session „läuft“. Leer: „Keine Ladesessions im Zeitraum.“
- **Karte „Verbraucher“:** Spalten „Verbraucher“, „Energie“; ohne Wert „–“ mit dem Grund als Titel. Leer: „Keine Verbraucher angelegt.“
- **Karte „Monatsspitzen“:** Spalten „Monat“, „Spitze“, „Zeitpunkt“, „Leistungspreis“.
- **Karte „Abgleich mit den Zählern“:** Spalten „Größe“, „Abrechnung“, „Zähler“, „Abweichung“; Größen „Netzbezug“, „Einspeisung“, „PV (ohne Markierung)“, „Wallbox {Name}“. Markierte Tage: „Mehr als 1 % Abweichung am {Tage}.“ Ohne geprüfte Tage: „Noch kein Tag abgeglichen.“
- **Gründe im Abgleich (`note`):** „Zählerstand fehlt“, „Zähler zurückgesetzt“, „Zähler zeigt 0 kWh“.
- **Fahrzeugkarte auf „Laden“:** „Session seit {HH:MM} · {kWh} kWh (PV {a} · Akku {b} · Netz {c})“; nach dem Ende „Letzte Session {TT.MM.} {HH:MM}–{HH:MM} · {kWh} kWh · {€} €“ (Kosten nur, wenn bekannt).
- **Namen:** Gast „Gast“, ohne Fahrzeug „Unbekannt“.
- **HA:** „Ersparnis heute“, „Ersparnis Monat“, „Autarkie heute“, „Ladeenergie“.

---

### Task 1: Migration 0010 und Herkunft „geschätzt“

**Files:**
- Create: `src/bindaems/app/db/migrations/versions/v0010_statistics.py`
- Modify: `src/bindaems/app/db/schema.py` (fünf Tabellen nach Präzisierung 2), `src/bindaems/app/ledger/service.py` (`Origin`, `DaySummary.estimated_slots`), `src/bindaems/app/ledger/routes.py` (nichts außer, falls nötig, der Ausgabe von `estimated_slots`), `ui/src/lib/api/schemas.ts` (`DaySummarySchema.estimated_slots`), Vertragsdatei `ledger-days.json` neu schreiben
- Test: `tests/unit/app/test_db.py`, `tests/unit/app/ledger/test_service.py`

**Interfaces:**
- Produces: Tabellen `assignment_log`, `charging_session`, `ledger_cost`, `ledger_check`, `consumer_day` in `schema.py`; `Origin = Literal["stream", "influx", "estimate"]`; `DaySummary.estimated_slots: int` (direkt nach `expected_slots`).

- [ ] **Step 1: Write the failing tests**

```python
def test_migration_0010_creates_the_statistics_tables_and_downgrades(engine: Engine) -> None:
    # nach upgrade: alle fünf Tabellen mit den Spalten aus Präzisierung 2; version_num "0010";
    # downgrade auf "0009" entfernt sie wieder

def test_estimated_slots_are_counted_and_do_not_add_coverage(ledger, clock) -> None:
    # ein Slot origin="estimate", covered_s=0, ein echter mit 900 s
    day = ledger.days(D, D)[0]
    assert day.estimated_slots == 1
    assert day.coverage == pytest.approx(900 / (day.expected_slots * 900))
```

`test_migrations_match_schema` deckt den Abgleich von Migration und `schema.py` ab; in den älteren Migrationstests wird die erwartete `version_num` auf `"0010"` gehoben.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/test_db.py tests/unit/app/ledger -q`
Expected: FAIL

- [ ] **Step 3: Implement** Migration und Schema nach Präzisierung 2; `estimated_slots` in `_summary` aus `origin == "estimate"`. Vertragsdateien: `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app tests/integration/test_ui_contract.py -q && cd ui && pnpm test -- contract`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests ui/src/lib/api
git commit -m "feat(app): Migration 0010 für Statistik und Herkunft „geschätzt“"
```

### Task 2: Einstandspreis des Akkus

**Files:**
- Create: `src/bindaems/app/ledger/costing.py`
- Modify: `src/bindaems/app/ledger/service.py` (`ingest`, `_apply_prices` rufen `costing.mark_dirty(slot_start)`), `src/bindaems/app/runtime.py` (`LedgerCosting` bauen, `settings.on_change` → `mark_dirty(None)` bei Änderung von `planning.wear_ct`; `catch_up` nach jedem Lauf von `_ledger_loop` und nach `handle_stream_message`)
- Test: `tests/unit/app/ledger/test_costing.py`

**Interfaces:**
- Consumes: `LedgerSlot` (`ledger/service.py`), `RuntimeSettings.planning.wear_ct`, `cfg.battery.usable_kwh`.
- Produces:
  - `@dataclass(frozen=True) class SlotCost: slot_start: datetime; stock_kwh: float; avg_ct: float | None; discharge_ct: float`
  - `def cost_basis(slots: Sequence[LedgerSlot], start: tuple[float, float | None], *, wear_ct: float, usable_kwh: float) -> list[SlotCost]` (rein, Präzisierung 4)
  - `class LedgerCosting` mit `__init__(engine, ledger: LedgerService, settings: SettingsService, usable_kwh: float)`, `mark_dirty(start: datetime | None) -> None` (`None` = alles), `catch_up() -> int` (rechnet ab dem frühesten markierten Slot, gibt die Zahl der geschriebenen Slots zurück; Lock wie `LedgerService`), `costs(start: datetime, end: datetime) -> dict[datetime, SlotCost]` (ruft zuerst `catch_up`)

- [ ] **Step 1: Write the failing tests**

```python
def test_pv_charge_is_free() -> None:
    # pv>battery 2000 Wh, dann battery>house 1000 Wh, wear 2
    costs = cost_basis([charge, discharge], (0.0, None), wear_ct=2.0, usable_kwh=10)
    assert costs[0].avg_ct == 0.0 and costs[0].stock_kwh == 2.0
    assert costs[1].discharge_ct == 2.0 and costs[1].stock_kwh == 1.0

def test_grid_and_pv_charge_mix_by_energy() -> None:
    # 1 kWh PV zu 0 ct + 1 kWh Netz zu 30 ct → 15 ct

def test_discharge_uses_average_at_slot_start() -> None:
    # im selben Slot entladen und laden: discharge_ct aus dem Mittelwert davor

def test_empty_battery_discharge_costs_wear_only() -> None: ...
def test_stock_is_capped_at_usable_capacity() -> None:
    # 12 kWh geladen bei usable 10 → stock 10; avg gewichtet mit dem alten Inhalt vor dem Kappen

def test_missing_price_counts_at_average() -> None:
    # grid>battery ohne import_price_ct: avg unverändert, stock steigt

def test_catch_up_recomputes_from_the_earliest_dirty_slot(costing, ledger) -> None: ...
def test_settings_change_recomputes_all(costing, settings_service) -> None:
    # neuer wear_ct → discharge_ct aller Slots neu; reprice markiert ab dem ersten Tag
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/ledger/test_costing.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierung 4. `catch_up` liest den Zustand aus der Zeile von `ledger_cost` vor dem frühesten markierten Slot, rechnet in Blöcken von 7 Tagen bis zum letzten Slot und ersetzt die Zeilen (`DELETE` ab Start, `INSERT`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/ledger -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests
git commit -m "feat(app): Einstandspreis des Akkus je Viertelstunde"
```

### Task 3: Steckzustand und Belegung

**Files:**
- Create: `src/bindaems/app/charging/__init__.py`, `src/bindaems/app/charging/plug.py`, `src/bindaems/app/charging/tracker.py` (nur Beobachtung und `assignment_log`; Sessions in Task 4)
- Modify: `src/bindaems/app/runtime.py` (`ChargingTracker` bauen, `live_feed.health_handlers.append(tracker.wake)`, Job `("charging", tracker.run)`)
- Test: `tests/unit/app/charging/test_plug.py`, `tests/unit/app/charging/test_tracker.py`

**Interfaces:**
- Consumes: `LiveState.value`, `LiveState.health` (`app/core_link.py`), `Config.wallboxes` (`EvcsConfig`, `TwcConfig`).
- Produces:
  - `def wallbox_connected(wallbox: EvcsConfig | TwcConfig, key: str, value: Callable[[str], Value]) -> bool | None`
  - `@dataclass(frozen=True) class WallboxSeen: wallbox: str; connected: bool | None; occupant: str | None; kind: str | None`
  - `def observe(cfg: Config, live: LiveState) -> list[WallboxSeen]` (Belegung aus `health["wallboxes"]`, fehlend = `None`)
  - `class ChargingTracker` mit `__init__(engine, clock, cfg, live)`, `wake() -> None` (Health-Handler, setzt nur ein Event), `async run() -> None` (wartet auf das Event, beobachtet, schreibt in einem Thread), `record(now: datetime, seen: Sequence[WallboxSeen]) -> None` (synchron, für Tests)

- [ ] **Step 1: Write the failing tests**

```python
def test_evcs_connected_from_status() -> None:
    # status 0 → False, 1 → True, fehlt → None

def test_twc_connected_from_flag() -> None: ...

@given(status=st.integers(0, 10) | st.none(), flag=st.booleans() | st.none())
def test_plug_state_matches_the_core(status, flag) -> None:
    # gleiches Ergebnis wie core.control.charge_inputs._evcs_state/_twc_state (Element 0)

def test_assignment_change_is_logged_once(tracker, engine) -> None:
    # zweimal dieselbe Belegung → eine Zeile; dann occupant "tesla" → zweite Zeile

def test_first_observation_after_start_is_always_logged(engine) -> None: ...
def test_observation_without_health_has_no_occupant(live) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/charging -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierungen 5 und 6 (Teil `assignment_log`). `wake` darf nicht blockieren; der Schreibzugriff läuft über `asyncio.to_thread`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/charging tests/integration/test_app_runtime.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests
git commit -m "feat(app): Steckzustand und Belegung der Wallboxen aufzeichnen"
```

### Task 4: Ladesessions

**Files:**
- Modify: `src/bindaems/app/charging/tracker.py` (Sessions nach Präzisierung 6)
- Test: `tests/unit/app/charging/test_tracker.py`

**Interfaces:**
- Consumes: Task 3.
- Produces:
  - `SESSION_GAP = timedelta(minutes=30)`, `LAST_SEEN_EVERY = timedelta(minutes=1)`
  - `@dataclass(frozen=True) class StoredSession: id: int; wallbox: str; vehicle: str | None; start: datetime; end: datetime | None; last_seen: datetime`
  - `def session_vehicle(log: Sequence[tuple[datetime, str | None]], start: datetime, end: datetime) -> str | None` (rein: längste Dauer, bei Gleichstand das spätere)
  - `ChargingTracker.sessions(start: datetime, end: datetime, vehicle: str | None = None) -> list[StoredSession]` (Sessions, die `[start, end)` überdecken, neueste zuerst)

- [ ] **Step 1: Write the failing tests**

```python
def test_plug_in_opens_and_unplug_closes_a_session(tracker) -> None: ...
def test_unknown_plug_state_keeps_the_session_open(tracker) -> None: ...
def test_session_survives_short_gap(tracker) -> None:
    # Beobachtungen 10:00, dann 10:25 (gesteckt) → eine Session
def test_long_gap_splits_the_session(tracker) -> None:
    # 10:00, dann 10:45 (gesteckt) → erste endet 10:00, zweite beginnt 10:45
def test_unplugged_during_gap_ends_at_last_seen(tracker) -> None: ...
def test_open_session_is_resumed_after_restart(engine, clock) -> None:
    # neuer ChargingTracker auf derselben Datenbank, Auto steckt noch → dieselbe id
def test_vehicle_is_the_longest_occupant(tracker) -> None:
    # unclear 5 min, dann tesla 2 h → "tesla"; nur guest → "guest"; nie belegt → None
def test_last_seen_is_written_at_most_every_minute(tracker, engine) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/charging/test_tracker.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierung 6. Beim Start liest der Tracker die offenen Sessions (`end IS NULL`); die erste Beobachtung entscheidet mit `SESSION_GAP` über Fortsetzung oder Ende.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/charging -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests
git commit -m "feat(app): Ladesessions aus Steckzustand und Belegung"
```

### Task 5: Energie und Kosten je Session und je Auto

**Files:**
- Create: `src/bindaems/app/charging/sessions.py`, `src/bindaems/app/charging/routes.py`
- Modify: `src/bindaems/app/api/server.py` bzw. `runtime.py` (Router `charging_router` einhängen), `docs/betrieb.md` (Endpunkt), `tests/unit/test_docs.py`
- Test: `tests/unit/app/charging/test_sessions.py`, `tests/unit/app/charging/test_routes.py`

**Interfaces:**
- Consumes: `LedgerService.slots`, `LedgerCosting.costs` (Task 2), `ChargingTracker.sessions` (Task 4).
- Produces:
  - `@dataclass(frozen=True) class FlowSplit: kwh: float; pv_kwh: float; battery_kwh: float; grid_kwh: float; cost_eur: float | None`
  - `@dataclass(frozen=True) class ChargingSession: id: int; wallbox: str; vehicle: str | None; start: datetime; end: datetime | None; split: FlowSplit`
  - `def slot_sessions(sessions: Sequence[StoredSession], wallbox: str, slot: datetime, now: datetime) -> StoredSession | None` (Präzisierung 7; offene Sessions enden bei `now`)
  - `def attribute(slots: Sequence[LedgerSlot], costs: Mapping[datetime, SlotCost], sessions: Sequence[StoredSession], now: datetime) -> tuple[dict[int, FlowSplit], dict[str, FlowSplit]]` (je Session-`id` und je Fahrzeug, Schlüssel `<key>`, `guest`, `unknown`)
  - `class SessionService` mit `sessions(first: date, last: date, vehicle: str | None) -> list[ChargingSession]` und `by_vehicle(start: datetime, end: datetime) -> dict[str, FlowSplit]`
  - Route `GET /api/charging/sessions` (Präzisierung 12), je Eintrag `id`, `wallbox`, `wallbox_name`, `vehicle`, `vehicle_name`, `start`, `end`, `open`, `kwh`, `pv_kwh`, `battery_kwh`, `grid_kwh`, `cost_eur`

- [ ] **Step 1: Write the failing tests**

```python
def test_split_and_cost_of_a_session() -> None:
    # pv>wb:twc 1000 Wh (0 ct), battery>wb:twc 500 Wh (discharge_ct 12),
    # grid>wb:twc 500 Wh (30 ct) → 2,0 kWh, Kosten 0 + 0,06 + 0,15 = 0,21 €

def test_slot_belongs_to_the_session_with_most_overlap() -> None: ...
def test_energy_without_session_is_unknown() -> None: ...
def test_vehicle_cost_unknown_without_price() -> None: ...
def test_open_session_counts_up_to_now() -> None: ...
def test_sessions_route_is_newest_first_and_limited(client_as) -> None: ...
def test_sessions_route_rejects_more_than_400_days(client_as) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/charging -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierungen 7 und 12. Die Slots liest der Dienst einmal für den ganzen Zeitraum der gefundenen Sessions.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app tests/unit/test_docs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app docs tests
git commit -m "feat(app): Energie und Kosten je Ladesession und je Auto"
```

### Task 6: Abgleich mit den Zählerständen

**Files:**
- Create: `src/bindaems/app/ledger/counters.py`, `src/bindaems/app/ledger/checks.py`
- Modify: `src/bindaems/app/ledger/routes.py` (`GET /api/ledger/checks`), `src/bindaems/app/runtime.py` (Job `("ledger-nightly", self._ledger_nightly_loop)`: beim Start 35 Tage, danach täglich um `LEDGER_NIGHTLY_AT = (2, 45)` 7 Tage), `docs/betrieb.md`, `tests/unit/test_docs.py`
- Test: `tests/unit/app/ledger/test_counters.py`, `tests/unit/app/ledger/test_checks.py`, `tests/unit/app/ledger/test_routes.py`

**Interfaces:**
- Consumes: `InfluxReader.query`, `quote_ident`, `quote_str`, `RP_LONG`, `RP_RAW` (`app/history/influx.py`), `LedgerService.days`/`slots`.
- Produces:
  - `def counter_tags(signal: str) -> tuple[str, str, str] | None` (Tabelle in Präzisierung 8)
  - `async def readings_at(reader: InfluxReader, signals: Sequence[str], instants: Sequence[datetime]) -> dict[tuple[str, datetime], float | None]`
  - `async def reading_near(reader: InfluxReader, signal: str, instant: datetime, *, before: bool) -> float | None` und `async def soc_near(reader, instant, *, before: bool) -> float | None` (für Task 7)
  - `@dataclass(frozen=True) class DayCheck: day: date; signal: str; ledger_kwh: float; counter_kwh: float | None; deviation_pct: float | None; flagged: bool; note: str | None`
  - `def check_day(day: date, slots: Sequence[LedgerSlot], start_readings: Mapping[str, float | None], end_readings: Mapping[str, float | None], wallboxes: Sequence[str], pv_signals: Sequence[str]) -> list[DayCheck]` (rein, Präzisierung 9)
  - `class LedgerChecks` mit `async run(first: date, last: date) -> int`, `checks(first: date, last: date) -> list[DayCheck]`, `totals(checks: Sequence[DayCheck]) -> list[dict[str, Any]]` (je Signal Summen nur über Tage mit Zählerwert, Abweichung daraus)

- [ ] **Step 1: Write the failing tests**

```python
def test_flagged_above_one_percent_and_100_wh() -> None:
    # import: Abrechnung 10,2 kWh, Zähler 10,0 → 2 %, markiert
def test_small_absolute_difference_is_not_flagged() -> None:
    # export: 0,50 gegen 0,45 kWh → 11 %, aber 0,05 kWh → nicht markiert
def test_pv_is_never_flagged() -> None: ...
def test_counter_reset_is_not_flagged() -> None:  # note "Zähler zurückgesetzt"
def test_missing_reading_is_noted() -> None:      # note "Zählerstand fehlt"
def test_zero_counter_has_no_deviation() -> None: # note "Zähler zeigt 0 kWh"
def test_totals_skip_unchecked_days() -> None: ...
async def test_readings_at_uses_one_query(reader) -> None:
    # 8 Mitternächte, 4 Signale → genau eine Abfrage auf "long"."energy" mit GROUP BY time(30m)
async def test_midnight_readings_on_dst_change(reader) -> None:
    # 25.10.2026: Mitternacht in Wien ist 22:00 UTC davor, 23:00 UTC danach
def test_checks_route_returns_days_and_totals(client_as) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/ledger -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierungen 8 und 9. `run` liest die Zählerstände für alle Mitternächte `first … last + 1` in einer Abfrage und ersetzt die Zeilen von `ledger_check` der Tage. Fehler beim Lesen protokolliert der Job und versucht es in der nächsten Nacht wieder.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app tests/unit/test_docs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app docs tests
git commit -m "feat(app): täglicher Abgleich der Abrechnung mit den Zählerständen"
```

### Task 7: Nachrechnung von Lücken

**Files:**
- Create: `src/bindaems/app/ledger/estimate.py`
- Modify: `src/bindaems/app/runtime.py` (der Nachtlauf schätzt zuerst die letzten 7 Tage, dann der Abgleich), `docs/betrieb.md` („Tarif und Abrechnung“: Schätzung)
- Test: `tests/unit/app/ledger/test_estimate.py`, `tests/property/test_estimate_allocation.py`

**Interfaces:**
- Consumes: `reading_near`, `soc_near` (Task 6), `LedgerService.ingest(flows, "estimate")`, `parse_slot_flows`, `cfg.battery.usable_kwh`, `cfg.wallbox_order`.
- Produces:
  - `def allocate_wh(pv: float, battery_net: float, house: float, wallboxes: Mapping[str, float]) -> dict[str, float]` (Präzisierung 10)
  - `def gaps(present: Iterable[datetime], start: datetime, end: datetime) -> list[tuple[datetime, int]]` (Beginn und Zahl der fehlenden Slots, nur vollständig vergangene)
  - `def estimate_gap(start: datetime, n: int, energies: Mapping[str, float], battery_net_kwh: float, wallbox_order: Sequence[str]) -> list[SlotFlows]`
  - `class GapEstimator` mit `async run(start: datetime, end: datetime) -> int` (geschätzte Slots)

- [ ] **Step 1: Write the failing tests**

```python
@given(...)
def test_allocation_matches_the_core(pv, battery, house, wallboxes) -> None:
    # allocate_wh(...) == core.accounting.flows.allocate(pv, grid, battery, house, wallboxes)
    # (bis auf 1e-9 je Fluss), grid aus der Bilanz

def test_gap_energy_is_spread_evenly() -> None:
    # 4 Slots, Bezug 4 kWh, PV 0, Haus aus Bilanz → je Slot grid>house 1000 Wh
def test_negative_house_becomes_dc_pv() -> None: ...
def test_battery_from_soc_difference() -> None:
    # SOC 50 → 60 % bei 10 kWh → battery_net 1 kWh, als pv>battery bzw. grid>battery
def test_gap_without_reading_stays_empty(estimator) -> None: ...
def test_gap_longer_than_7_days_is_skipped(estimator) -> None: ...
def test_real_slot_replaces_estimate(ledger) -> None:
    # erst Schätzung (covered_s 0), dann ingest(..., "influx") mit 600 s → ersetzt
def test_second_run_changes_nothing(estimator) -> None: ...
def test_estimates_are_marked(ledger) -> None:  # origin "estimate", covered_s 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/ledger/test_estimate.py tests/property/test_estimate_allocation.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierungen 7 und 10. Die Ränder liest der Schätzer je Lücke; Netzbezug und Einspeisung müssen an beiden Rändern bekannt sein, sonst bleibt die Lücke leer (Log mit dem Grund). Fehlende PV- oder Wallbox-Zähler zählen 0.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/ledger tests/property -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app docs tests
git commit -m "feat(app): Lücken der Abrechnung aus den Zählerständen nachrechnen"
```

### Task 8: Energie je Verbraucher und Tag

**Files:**
- Create: `src/bindaems/app/consumers/days.py`
- Modify: `src/bindaems/app/consumers/energy.py` (`refresh` nutzt `window_energy`), `src/bindaems/app/runtime.py` (Nachtlauf: gestern; beim Start die fehlenden Tage der letzten 31)
- Test: `tests/unit/app/consumers/test_energy.py`, `tests/unit/app/consumers/test_days.py`

**Interfaces:**
- Consumes: `EnergyCache`, `ConsumerService` (Verbraucherliste).
- Produces:
  - `EnergyCache.window_energy(consumers: Iterable[Consumer], start: datetime, end: datetime) -> tuple[dict[int, tuple[float | None, str | None]], float | None]` (dieselbe Rechnung wie `refresh`, ohne den Cache zu ändern; `refresh` ruft sie mit `day_start(now) … now`)
  - `class ConsumerDays` mit `async store(day: date) -> int`, `async fill_missing(today: date, days: int = 31) -> int`, `energy(first: date, last: date) -> dict[int, tuple[float | None, str | None]]` (Summe je Verbraucher; ein Tag ohne Wert macht die Summe `None` mit dem Grund des ersten solchen Tages)

- [ ] **Step 1: Write the failing tests**

```python
async def test_window_energy_matches_refresh_for_today(cache, reader) -> None: ...
async def test_store_writes_one_row_per_consumer(days, engine) -> None: ...
async def test_fill_missing_skips_stored_days(days) -> None: ...
def test_sum_is_unknown_when_a_day_is_missing(days) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/consumers -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Entscheidung 8. Der Tag ist `[lokale Mitternacht, nächste Mitternacht)`; `store` ersetzt die Zeilen des Tages.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/consumers -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests
git commit -m "feat(app): Energie je Verbraucher und Tag speichern"
```

### Task 9: Statistik-Dienst und API

**Files:**
- Create: `src/bindaems/app/stats/__init__.py`, `src/bindaems/app/stats/service.py`, `src/bindaems/app/stats/routes.py`
- Modify: `src/bindaems/app/ledger/service.py` (`month_peaks(first: date, last: date) -> list[MonthPeak]`, je lokalem Monat eine Abfrage-Gruppe statt je Monat einer Abfrage), `src/bindaems/app/runtime.py` (Router), `docs/betrieb.md`, `tests/unit/test_docs.py`
- Test: `tests/unit/app/stats/test_service.py`, `tests/unit/app/stats/test_routes.py`, `tests/unit/app/ledger/test_service.py`

**Interfaces:**
- Consumes: `LedgerService.days`, `.slots`, `.month_peaks`; `SessionService.by_vehicle`, `.sessions` (Task 5); `ConsumerDays.energy` (Task 8), für heute `EnergyCache`; `LedgerChecks.checks`/`.totals` (Task 6); `monthly_fees_eur`, `power_rate`, `power_cost_eur` (`tariff/calc.py`).
- Produces:
  - `Period = Literal["day", "week", "month", "year"]`
  - `def period_range(period: Period, day: date) -> tuple[date, date]`
  - `def fees_eur(first: date, last: date, tariff: TariffSettings) -> float` (anteilig je Tag)
  - `class StatsService` mit `period(period: Period, day: date) -> dict[str, Any]` (Antwort nach „Antwort von `GET /api/stats`“) und `today() -> TodayStats` (für Task 10: `savings_today_eur`, `savings_same_import_today_eur`, `savings_month_eur`, `savings_same_import_month_eur`, `autarky_today`, `autarky_month`, alle `float | None`)

- [ ] **Step 1: Write the failing tests**

```python
def test_week_is_monday_to_sunday() -> None:
    assert period_range("week", date(2026, 10, 9)) == (date(2026, 10, 5), date(2026, 10, 11))
def test_fees_are_prorated_per_day(settings) -> None:
    # 9 € je Monat, Oktober (31 Tage), 10 Tage → 9 · 10 / 31
def test_day_buckets_on_dst_days(stats) -> None:
    # 25.10.2026 → 25 Buckets, 29.03.2026 → 23
def test_year_has_month_buckets_and_peaks(stats) -> None: ...
def test_power_price_only_for_month_and_year(stats) -> None: ...
def test_total_is_null_when_net_is_unknown(stats) -> None: ...
def test_vehicles_in_config_order_then_guest_and_unknown(stats) -> None: ...
def test_empty_period_has_zero_energy_and_no_ratios(stats) -> None:
    # Zukunft: complete false, coverage 0, autarky null
def test_stats_route_rejects_unknown_period(client_as) -> None: ...
def test_month_peaks_per_month(ledger) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/stats tests/unit/app/ledger -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierung 11, Entscheidungen 9 und 10. Kennzahlen des Zeitraums entstehen aus den Summen der Tage (nicht als Mittel der Tageskennzahlen): Autarkie `1 − Bezug ÷ Verbrauch`, Eigenverbrauch `(PV − Einspeisung) ÷ PV`, Ersparnis wie `_summary`, jeweils `None`, sobald ein Tag den Wert nicht kennt.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app tests/unit/test_docs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app docs tests
git commit -m "feat(app): Statistik je Tag, Woche, Monat und Jahr"
```

### Task 10: Statistik-Sensoren in Home Assistant

**Files:**
- Modify: `src/bindaems/app/ha/entities.py` (`HaInputs.stats: TodayStats | None`, `HaInputs.sessions: Mapping[str, ChargingSession | None]`, `month_kwh` je Fahrzeug; neue `EntitySpec` nach Präzisierung 13), `src/bindaems/app/runtime.py` (Cache `_stats_inputs`, neu bei jedem neuen Slot und alle 15 min, gelesen von `_build_ha_inputs`), `docs/betrieb.md` (Abschnitt 7, Tabelle der Entitäten), `tests/unit/test_docs.py`, `tests/app_helpers.py` (`sample_inputs`)
- Test: `tests/unit/app/ha/test_entities.py`, `tests/integration/test_app_runtime.py`

**Interfaces:**
- Consumes: `StatsService.today()` (Task 9), `SessionService.sessions` (Task 5).
- Produces: Objekte `savings_today`, `savings_month`, `autarky_today` am Gerät „BindaEMS“ (nach `month_peak`), `<v>_charge_energy` je Fahrzeug (nach `<v>_wallbox`).

- [ ] **Step 1: Write the failing tests**

```python
def test_entities_from_config() -> None:  # bestehender Test: neue IDs an den genannten Stellen
def test_savings_sensors_are_monetary_with_two_decimals() -> None:
    # 1.234 → "1.23", Attribut same_import_eur
def test_autarky_today_is_a_whole_percent() -> None:  # 0.634 → "63", Attribut month_pct 58
def test_charge_energy_shows_open_or_last_session() -> None:
    # offene Session vor beendeter; Attribute start, end (null), pv_kwh, battery_kwh, grid_kwh, cost_eur, month_kwh
def test_statistics_sensors_unknown_without_data() -> None: ...
async def test_stats_inputs_refresh_on_new_slot_not_every_5_s(runtime) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/ha tests/integration/test_app_runtime.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierung 13 und Entscheidung 11.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app tests/integration tests/unit/test_docs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app docs tests
git commit -m "feat(ha): Ersparnis, Autarkie und Ladeenergie als Sensoren"
```

### Task 11: Demo-Welt und Vertragsdateien

**Files:**
- Modify: `tests/ui_world.py` (14 Tage Abrechnung vor `T_DEMO` aus einem erfundenen Tagesprofil mit `pv>wb`, `battery>wb` und `grid>wb`, darunter ein Tag mit zwei geschätzten Slots; je Wallbox zwei beendete Sessions und eine offene für die Garage; `assignment_log` passend; Abgleich für 7 Tage, einer markiert; `consumer_day` für 7 Tage; OeMAG-Wert für Oktober; eine Grundgebühr), `tests/integration/test_ui_contract.py` (`GET_ENDPOINTS["stats-day"]`, `["stats-month"]`, `["stats-year"]`, `["charging-sessions"]`, `["ledger-checks"]`), Vertragsdateien neu schreiben, `ui/src/lib/api/schemas.ts` (`StatsSchema`, `ChargingSessionSchema`, `LedgerChecksSchema`), `ui/src/lib/api/endpoints.ts` (`api.stats(period, day)`, `api.chargingSessions(first, last, vehicle?)`, `api.ledgerChecks(first, last)`), `ui/src/lib/api/contract.test.ts` (`SCHEMAS`), `ui/src/lib/testing/fixtures.ts` (`stats()`, `chargingSession()`)
- Test: `tests/integration/test_ui_contract.py`, `ui/src/lib/api/contract.test.ts`

**Interfaces:**
- Consumes: Tasks 5, 6, 9.
- Produces: die Schemas und Endpunkte für Tasks 12 bis 14; Fixtures `stats(overrides)`, `chargingSession(overrides)`.

- [ ] **Step 1: Write the failing test.** `GET_ENDPOINTS` um die fünf Einträge ergänzen; `SCHEMAS` um dieselben Dateien.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_ui_contract.py -q`
Expected: FAIL (Vertragsdateien fehlen)

- [ ] **Step 3: Implement** die Demo-Daten und Schemas; `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration -q && cd ui && pnpm check && pnpm test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests ui/src/lib
git commit -m "test(ui): Demo-Welt und Verträge für Statistik und Ladesessions"
```

### Task 12: Seite „Statistik“ – Kopf, Kacheln, Diagramm, Kosten

**Files:**
- Create: `ui/src/routes/statistik/+page.svelte`, `ui/src/routes/statistik/page.test.ts`, `ui/src/lib/stats/period.ts` (+ Test), `ui/src/lib/stats/view.ts` (+ Test), `ui/src/lib/stats/StatTiles.svelte` (+ Test), `ui/src/lib/stats/CostCard.svelte` (+ Test)
- Modify: `ui/src/lib/nav.ts` (`'stats'`, Eintrag nach „Verlauf“), `ui/src/lib/components/icons.ts` (Symbol `stats`: drei Balken), `ui/src/lib/nav.test.ts`, `ui/src/lib/charts/options.ts` (`statsEnergyOption(stats, palette)`), `ui/tests/e2e/pages.spec.ts` (`/statistik`)
- Test: wie oben, `ui/tests/e2e/stats.spec.ts`

**Interfaces:**
- Consumes: `api.stats` (Task 11), `EChart.svelte`, `format.ts`.
- Produces:
  - `type Period = 'day' | 'week' | 'month' | 'year'`; `periodTitle(period, day): string`, `shift(period, day, step: -1 | 1): string`, `todayIso(): string` (Wiener Zeit)
  - `tiles(stats): Tile[]`, `costRows(stats): Row[]` in `view.ts`
  - URL-Parameter `?zeitraum=tag|woche|monat|jahr&datum=JJJJ-MM-TT` (Standard `tag` und heute)

- [ ] **Step 1: Write the failing tests**

```ts
it('titles each period', () => {
	expect(periodTitle('day', '2026-10-09')).toBe('Fr., 09.10.2026');
	expect(periodTitle('week', '2026-10-09')).toBe('KW 41 · 05.10.–11.10.2026');
	expect(periodTitle('month', '2026-10-09')).toBe('Oktober 2026');
	expect(periodTitle('year', '2026-10-09')).toBe('2026');
});
it('shifts a month from 31 January to the end of February', () => {
	expect(shift('month', '2026-01-31', 1)).toBe('2026-02-28');
});
it('shows the five tiles with unknown values as –', ...);
it('shows power price only for month and year', ...);
it('page reads period and date from the URL and reloads on change', ...);
```

E2E: `/statistik` öffnen, „Monat“ wählen, Überschrift „Oktober 2026“, Diagramm-Canvas sichtbar, Zelle „Grundgebühren“ vorhanden; bei 360 px ohne waagrechte Scrollleiste.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pnpm test -- stats statistik nav`
Expected: FAIL

- [ ] **Step 3: Implement** nach Entscheidung 9 und „Texte“. Der laufende Zeitraum lädt alle 60 s neu, vergangene nur einmal.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && pnpm check && pnpm lint && pnpm test && pnpm build && pnpm e2e`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui
git commit -m "feat(ui): Seite Statistik mit Kacheln, Energie und Kosten"
```

### Task 13: Seite „Statistik“ – Autos, Ladesessions, Verbraucher, Monatsspitzen, Abgleich

**Files:**
- Create: `ui/src/lib/stats/VehicleCard.svelte`, `ui/src/lib/stats/SessionsCard.svelte`, `ui/src/lib/stats/ConsumersCard.svelte`, `ui/src/lib/stats/PeaksCard.svelte`, `ui/src/lib/stats/ChecksCard.svelte`, je mit Test
- Modify: `ui/src/routes/statistik/+page.svelte` (Ressource `api.chargingSessions(first, last)`), `ui/src/lib/stats/view.ts` (`vehicleRows`, `sessionRows`, `checkRows`), `ui/tests/e2e/stats.spec.ts`
- Test: wie oben

**Interfaces:**
- Consumes: Tasks 11 und 12.
- Produces: die fünf Karten; `ct/kWh` = `cost_eur / kwh · 100`, „–“ ohne Kosten oder Energie.

- [ ] **Step 1: Write the failing tests**

```ts
it('lists vehicles with PV, battery and grid share and the battery-to-cars row', ...);
it('marks an open session as läuft', ...);
it('names flagged days in the checks card', () => {
	// flagged_days ["2026-10-12"] → „Mehr als 1 % Abweichung am 12.10.“
});
it('shows the peaks card only for month and year', ...);
it('shows the consumer reason as title when kWh is missing', ...);
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pnpm test -- stats`
Expected: FAIL

- [ ] **Step 3: Implement** nach „Texte“. Tabellen mit mehr als drei Spalten scrollen in ihrer Karte, nicht die Seite.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && pnpm check && pnpm lint && pnpm test && pnpm build && pnpm e2e`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui
git commit -m "feat(ui): Autos, Ladesessions, Verbraucher, Monatsspitzen und Abgleich"
```

### Task 14: Session in der Fahrzeugkarte auf „Laden“

**Files:**
- Modify: `ui/src/routes/laden/+page.svelte` (Ressource `api.chargingSessions(heute − 30 Tage, heute)` alle 60 s), `ui/src/lib/charging/VehicleCard.svelte`, `ui/src/lib/charging/view.ts` (`latestSession(sessions, vehicle)`, `sessionLine(session)`), Tests daneben, `ui/src/routes/laden/page.test.ts`
- Test: wie oben

**Interfaces:**
- Consumes: `api.chargingSessions` (Task 11).
- Produces: die Zeile nach „Texte“, Fahrzeugkarte; keine Zeile ohne Session.

- [ ] **Step 1: Write the failing tests**

```ts
it('prefers the open session', ...);
it('formats the last session with cost only when known', () => {
	expect(sessionLine(done({ cost_eur: null }))).toBe('Letzte Session 08.10. 18:00–21:30 · 12,4 kWh');
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pnpm test -- charging laden`
Expected: FAIL

- [ ] **Step 3: Implement** nach „Texte“.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && pnpm check && pnpm lint && pnpm test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui
git commit -m "feat(ui): laufende und letzte Ladesession in der Fahrzeugkarte"
```

### Task 15: Grafana-Startdashboard

**Files:**
- Create: `deploy/grafana/bindaems.json`, `tests/unit/test_grafana.py`
- Modify: `docs/betrieb.md` (neuer Abschnitt „Grafana“ nach „InfluxDB einrichten“: Import, Datenquelle wählen, was die Panels zeigen), `tests/unit/test_docs.py`

**Interfaces:**
- Produces: Dashboard mit `__inputs` für die Datenquelle `DS_BINDAEMS` (InfluxQL), Zeilen:
  - „Leistung“: `power` (`source` grid, pv, battery, load, wallbox; `phase` total) aus `raw`
  - „Ladestand“: `soc` aus `raw`
  - „Preise“: `price` (`kind` `import_gross`, `feed_in`) aus `long`
  - „Abrechnung je Tag“: `ledger` (`kwh`, `eur`, `GROUP BY time(1d), "flow"` mit `tz('Europe/Vienna')`) aus `long`
  - Markierungen aus `decision`

- [ ] **Step 1: Write the failing tests**

```python
def test_dashboard_is_valid_json_with_datasource_input() -> None: ...
def test_every_query_uses_a_known_measurement_and_field() -> None:
    # Messungen und Felder aus Spec 11.2; keine feste Datenquellen-UID
def test_no_plant_data_in_dashboard() -> None:  # keine IPs, Namen der Anlage, Portal-ID
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_grafana.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Entscheidung 12.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_grafana.py tests/unit/test_docs.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add deploy docs tests
git commit -m "feat(deploy): Grafana-Startdashboard"
```

### Task 16: Dokumentation, Abnahme und Stand der Spec

**Files:**
- Create: `docs/verification/abnahme-phase-4.md` (Teil Statistik)
- Modify: `docs/betrieb.md` („Tarif und Abrechnung“: Einstandspreis, Kosten je Auto, Abgleich, Schätzung; „Web-UI“: Seite „Statistik“, Session auf „Laden“; Ausrollen von 4a nur mit `ems-app`), `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absätze „Stand Plan 4a“ in 11.1, 11.3, 11.4, 12, 13, 15 und 18), `docs/uebergabe.md` (Stand, „Entschieden in Plan 4a“, „Bewusst offen gelassen“, „Offen an der Anlage“, nächster Schritt Plan 4b), `docs/verification/README.md`
- Test: `uv run pytest tests/unit/test_docs.py -q`

- [ ] **Step 1: Write „Stand Plan 4a“** in die genannten Abschnitte der Spec, mit den Entscheidungen und den Festlegungen der Umsetzung.
- [ ] **Step 2: Write the acceptance checklist:** 30 Tage ab dem Ausrollen; im Abgleich (`/statistik`, „Monat“, oder `GET /api/ledger/checks?from=…&to=…`) liegen Netzbezug und Einspeisung über die 30 Tage innerhalb ±1 %; markierte Tage mit Ursache; Vergleich einzelner Tage mit dem Zähler des Netzbetreibers; Unterschrift. Die Punkte „HA in beide Richtungen“ und „Ziel-SOC“ stehen als „folgt mit Plan 4b“ darin.
- [ ] **Step 3: Add to the guide** die Schritte an der Anlage: `docker compose up -d --build ems-app` (der core bleibt, Regelbetrieb bleibt an), Migration läuft beim Start, unter **Statistik** die ersten Werte prüfen, OeMAG-Werte und Grundgebühren im Tarif nachtragen, Grafana-Dashboard importieren (optional).
- [ ] **Step 4: Run the whole repository**

Run: `uv run ruff format --check . && uv run ruff check . && uv run mypy src && uv run lint-imports && uv run pytest -q --cov && cd ui && pnpm check && pnpm lint && pnpm test && pnpm build && pnpm e2e`
Expected: PASS, Abdeckung ≥ 75 % gesamt und ≥ 90 % für safety/control/actuators; `git diff main --stat -- src/bindaems/core src/bindaems/shared cerbo-watchdog` ist leer.

- [ ] **Step 5: Commit**

```bash
git add docs
git commit -m "docs: Stand Plan 4a in Spec, Betriebshandbuch, Abnahme und Übergabe"
```

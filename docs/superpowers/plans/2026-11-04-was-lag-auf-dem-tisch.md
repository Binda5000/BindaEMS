# Was lag auf dem Tisch? Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Jede Nacht rechnet die app einen vergangenen Tag im Nachhinein mit perfekter Voraussicht nach und zeigt auf der Seite „Statistik“ in Euro, wie viel zwischen dem echten Tag und dem besten Plan lag, zerlegt nach Ursache: PV-Prognose, Lastprognose, Schätzpreise, Akkuverluste und Regelung. So sieht der Betreiber, welche Verbesserung (Ideen 2.1, 2.2, 2.3, 9.3, 18.2) sich am meisten lohnt, bevor eine davon gebaut wird.

**Architecture:** Nur app und UI; der core bleibt gleich. Der Szenario-Runner (`tools/scenario/runner.py`) kann schon einen Tag mit dem echten Planer auf Ist-Werten nachspielen. Er bekommt feste Ladeleistungen der Autos je Slot, Schätzpreise je Slot, „alle Preise bekannt“ und ein Ende der Auswertung (Task 1). Ein reines Modul `tools/scenario/hindsight.py` rechnet je Tag acht Varianten (jede Kombination aus PV, Last und Preis echt oder prognostiziert) und zerlegt den Unterschied nach Shapley (Task 2). `tools/export_days.py` baut aus der Datenbank die Datei des Tages und die echten Kosten (Task 3). In der app legt ein Nachtdienst die Ergebnisse in SQLite ab (Tasks 4 und 5), eine Route liefert sie (Task 6), eine Karte auf „Statistik“ zeigt sie (Task 7).

**Tech Stack:** Python 3.12, pydantic, PuLP/HiGHS (über den bestehenden Planer), SQLAlchemy Core und Alembic, FastAPI, InfluxDB 1.x (Lesen), SvelteKit 2 (Svelte 5), valibot, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, 9.5 „Planungsablauf“ und „Stand Plan 3b“ (Szenario-Vergleich), 10 „Prognosen und Lernen“ (10.5 Grenzen des Lernens), 11.3 „SQLite“, 11.4 „Viertelstunden-Abrechnung“, 13 „Web-UI“ (Statistik). Idee: `/mnt/project-files/ideen/brainstorming-2026-11.md`, 18.1.

**Grundlage:** Dieser Plan setzt auf dem Stand des Branches `claude/project-thread-ambh18` auf (Plan „Netzgrenze je Phase“, `102628d`): `BatteryState.grid_phase_import_w`, `ScenarioBattery.grid_phase_import_w`, `grid_import_limits_w`, das Nachladen auf die Reserve und der gedachte Satz der Monatsspitze sind da. Kommt „Netzgrenze je Phase“ vor diesem Plan auf `main`, wird dieser Branch per Rebase nachgezogen.

## Begriffe

- **Echter Tag:** was an der Anlage passiert ist, aus der Abrechnung (`ledger_slot`) und dem Akku-SOC aus InfluxDB.
- **Nachgespielt** (Variante ∅): der heutige Planer auf den Ist-Werten des Tages, mit den Prognosen aus dem Archiv und den Schätzpreisen, die er damals gehabt hätte; alle 15 min neu geplant wie der Dienst.
- **Perfekt** (Variante PV + Last + Preis): derselbe Planer, aber mit den Ist-Werten als Prognose und allen Preisen bekannt.
- **Auf dem Tisch:** echte Kosten − Kosten „perfekt“. Das ist die Summe der fünf Ursachen.

## Entscheidungen

Jede Entscheidung hat eine Empfehlung; mit der Freigabe gelten die Empfehlungen, wenn der Betreiber nichts anderes sagt. Entscheidung 9 hat der Betreiber am 4.11.2026 auf der Karte im Thread getroffen.

1. **„Perfekt“ heißt: derselbe Planer mit perfekter Voraussicht,** nicht das theoretische Optimum. Alle Regeln gelten weiter (Reserve, Regel R1, Budget der Monatsspitze, Netzgrenze je Phase, Nachladen auf die Reserve, Trägheit). Grund: Die Frage ist „was brächte eine bessere Prognose diesem Planer?“. Ein freies Optimum über den Tag würde Regeln brechen, die bewusst gelten, und Geld zeigen, das keine Verbesserung holen kann.
2. **Zerlegung nach Shapley über drei Prognose-Ursachen, dazu zwei Rest-Ursachen.** Je Tag acht Läufe: jede Teilmenge `S` von {PV, Last, Preis} mit echten Werten statt Prognose, Kosten `C(S)`. Der Anteil einer Ursache `i` ist der Mittelwert ihres Gewinns über alle Reihenfolgen:
   `φ_i = 1/3·[C(∅) − C(i)] + 1/6·[C(j) − C(i,j)] + 1/6·[C(k) − C(i,k)] + 1/3·[C(j,k) − C(i,j,k)]`.
   Die drei Anteile summieren sich genau zu `C(∅) − C(PV,Last,Preis)`, unabhängig von der Reihenfolge. Der Rest `echt − C(∅)` teilt sich in **Akkuverluste** (geschätzt, Entscheidung 6) und **Regelung und Rest** (alles andere). Alternative: vier Läufe nacheinander (PV, dann Last, dann Preis); halb so teuer, aber das Ergebnis hängt von der Reihenfolge ab, gerade PV und Preis wirken zusammen.
3. **Ein Anteil darf negativ sein.** Eine schlechte Prognose kann zufällig billiger sein („Glück“). Die Anzeige zeigt das Vorzeichen und den Zusatz „Glück“, statt auf 0 zu klemmen; über einen Monat mittelt sich Glück heraus.
4. **Autos laden in jedem Lauf genau wie am echten Tag.** Je Slot geht die echte Ladeleistung aller Wallboxen (Flüsse `*>wb:*`) als feste Last in die Ausführung, der Akku darf ihr höchstens so viel geben wie echt (`battery>wb:*`). Der Planer kennt sie in keinem Lauf (wie im PV-Modus an der Anlage). Folge: Der Rückblick bewertet den Hausakku, nicht das Verschieben des Autoladens; was der echte Planer bei „Geplant“ mit den Autos abgestimmt hat, landet in „Regelung und Rest“. Autos als planbare Ansteckvorgänge sind ein eigener späterer Plan.
5. **Tag D lokal, mit D+1 als Vorschau.** Die Datei enthält D und D+1; gespielt und gezählt wird nur D (00:00 bis 24:00 Ortszeit), D+1 dient dem Planer als Horizont (wie an der Anlage, wo er nach 14 Uhr den Folgetag sieht). Das Ergebnis für D steht deshalb am Morgen von D+2.
6. **Kosten eines Tages** in ct, für echt und jede Variante gleich gerechnet, Preise brutto aus `PriceService` mit den aktuellen Einstellungen (wie `export_days`):
   - Energie: Σ Bezug · Bezugspreis − Σ Einspeisung · Einspeisepreis (ohne Einspeisepreis 0),
   - Verschleiß: `planning.wear_ct` je kWh Akku → Auto,
   - Leistungspreis: nur wenn der Tag einen echten Satz hat (`power_rate`): `power_cost_eur(max(Spitze_D, Spitze_vorher)) − power_cost_eur(Spitze_vorher)`; der gedachte Satz aus „Netzgrenze je Phase“ ist ein Steuersignal und zählt nicht,
   - SOC am Tagesende: Varianten werden auf den echten End-SOC bezogen: `− (soc_end_Variante − soc_end_echt) · η_dis · Median der Bezugspreise von D` (wie `end_soc_value_ct`).
   - **Akkuverluste:** `Verlust = Ladung − Entladung − ΔSOC · usable_kwh` (kWh), echt aus den Flüssen und dem SOC, nachgespielt aus dem Akkumodell; Anteil = `(Verlust_echt − Verlust_∅) · Median der Bezugspreise von D`. Das ist eine Schätzung (Standby und Wirkungsgrad, Idee 9.3) und heißt in der Anzeige so.
7. **Schätzpreise wie der Planer:** Für jeden Slot der Datei steht der Schätzpreis, den `prices/estimate.py` (`SpotHistory`, 28 Tage, Tagestyp) zum Stand **Mitternacht des Vortags** des Slots geliefert hätte, brutto über `import_price`. Der Runner nimmt ihn für Slots nach `known_until` statt seines 7-Tage-Medians. Bisherige Szenariodateien rechnen unverändert.
8. **Takt 15 min, nachts um 04:30 Uhr, höchstens 2 h je Nacht.** Je Tag 8 Läufe mit je 96 Neuplanungen. Das Rechnen läuft in einem Kindprozess (`spawn`) mit Zeitlimit 30 min je Tag und niedriger Priorität (`os.nice(10)`), damit der Planungsdienst nie wartet. Was um 06:30 Uhr nicht fertig ist, kommt in der nächsten Nacht. `app.hindsight.step_minutes: 60` macht es viermal schneller (für eine langsame VM).
9. **Wo es läuft: jede Nacht in der app, mit Karte auf „Statistik“** (Entscheidung des Betreibers vom 4.11.2026, Karte „Wo soll der Rückblick laufen?“: „Jede Nacht in der app“). Alternative: nur ein Werkzeug, das der Betreiber von Hand im Container startet; weniger Code, aber keine laufende Zahl und kein Monatsvergleich.
10. **Welche Tage:** ab `app.hindsight.first_day` (an der Anlage 2026-11-04, der erste ganze Tag mit Victron live; ohne Angabe die letzten 14 Tage), höchstens 62 Tage zurück, bis D+2 ≤ heute. Neu gerechnet werden nur: Tage ohne Ergebnis; Tage, die mit Grund übersprungen wurden, bis 7 Tage danach (die Abrechnung füllt Lücken nachts nach); Tage mit vorläufigem Einspeisepreis, sobald der OeMAG-Wert ihres Monats eingetragen ist. Ein neuer Planer rechnet alte Tage nicht neu; jedes Ergebnis trägt die Version der app.
11. **Übersprungen** wird ein Tag ohne vollständige Daten (D oder D+1 nicht exportierbar: „unvollständig“, „keine Preise“), ohne SOC um Mitternacht in InfluxDB („kein SOC“) oder nach Zeitlimit oder Fehler des Kindprozesses („Rechnung gescheitert“). Ein übersprungener Tag steht mit Grund in der Ablage und in der Anzeige.
12. **Ablage:** neue Tabelle `hindsight_day` (Migration `0016`), ein Eintrag je Tag, 400 Tage, dann gelöscht (Wartung).
13. **Anzeige:** Karte „Was lag auf dem Tisch?“ auf „Statistik“ für Tag, Woche, Monat und Jahr, unter der Kostenkarte. Kein HA-Sensor, keine Benachrichtigung, keine Änderung am Planer. Die Komponente „Rückblick“ erscheint auf „System“.
14. **Ausrollen:** nur `ems-app` (Migration `0016`, Sicherung vorher), der core bleibt. **Nicht vor dem 23.11.2026** (bis zum 22.11. laufen Abnahmen); mit Stufe 2 oder einem späteren Sammel-Pull, wie der Betreiber entscheidet, nie ems-app allein ohne die Reihenfolge aus `sammel-pull.md`. Nichts schaltet an der Anlage: Der Rückblick liest nur.
15. **Abnahme:** 14 Nächte nach dem Ausrollen. Bestanden, wenn jede Nacht jeder fällige Tag gerechnet oder mit Grund übersprungen ist, die Komponente „Rückblick“ keinen Fehler zeigt, kein Plan zwischen 04:30 und 06:30 Uhr ausfiel (Zeitplan, „System“), und der Betreiber an zwei Tagen die echten Kosten der Karte mit „Netto“ der Statistik vergleicht (Abweichung nur durch Verschleiß, Leistungspreis und geänderte Tarifeinstellungen erklärbar).

## Global Constraints

- Keine Änderung am core, an `shared/plan.py`, an der Safety-Schicht oder am Planer (`app/planning/*`); der Rückblick ruft den Planer nur auf.
- Neue Felder in Szenariodateien sind optional mit Standard; Dateien der Version 1 und 2 laden und rechnen unverändert (`scenario_days` vor dem 17.11.2026 und danach gleich). `export_days` schreibt ohne neue Optionen weiter Version 1.
- Importvertrag: `bindaems.app` importiert `bindaems.tools` nicht, **außer** `bindaems.app.hindsight` (neuer Vertrag in `pyproject.toml` mit `ignore_imports`); `bindaems.tools` importiert `bindaems.app.hindsight` nicht.
- Geldbeträge intern in ct (float), in der API in € auf 2 Stellen gerundet erst beim Ausgeben.
- Texte deutsch, Anrede „du“ im UI, Zahlen mit Komma. Commits deutsch im Stil `feat(app): …`, keine Modellbezeichnungen im Repo.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy` strict (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators), UI `pnpm check`, `pnpm lint`, `pnpm test`. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Tests, die eine `CoreRuntime` oder die app bauen, legen `data_dir` unter `tmp_path` (CI läuft nicht als root).

## Review Focus

- **Tag der Zeitumstellung** (92 oder 100 Slots, z. B. 25.10.2026): Datei, Ende der Auswertung um lokale Mitternacht, Tagesgrenzen und Median laufen ohne Fehler und zählen genau die Slots des Tages (Task 1 `test_until_counts_only_the_local_day_on_dst_change`, Task 3 `test_hindsight_input_on_autumn_change_has_100_slots`).
- **Kein OeMAG-Wert:** Einspeisepreis fehlt → Einspeisung zählt 0, das Ergebnis ist vorläufig und wird neu gerechnet, sobald der Monat eingetragen ist; die Karte zeigt „vorläufig“ (Task 3 `test_real_day_without_feed_in_is_provisional`, Task 5 `test_provisional_day_is_recomputed_when_month_is_known`, Task 7).
- **SOC um Mitternacht fehlt** (InfluxDB nicht erreichbar oder Lücke): Tag mit „kein SOC“ übersprungen, kein Absturz des Dienstes, Wiederholung bis 7 Tage danach (Task 5 `test_missing_soc_skips_day_and_retries`).
- **Kindprozess hängt oder stirbt:** nach 30 min beendet, Tag „Rechnung gescheitert“, der Dienst rechnet den nächsten Tag; die Planung läuft weiter (Task 5 `test_hanging_child_is_killed_and_day_marked_failed`).
- **„Perfekt“ teurer als „nachgespielt“** (die Heuristik der Trägheit oder Regel R1 kann mit perfekter Voraussicht anders entscheiden): kein Fehler, die Anteile dürfen negativ sein, die Summe der Ursachen bleibt `echt − perfekt` auf 0,01 ct (Task 2 `test_causes_sum_to_table_for_any_costs`).

---

### Task 1: Runner mit festen Autos, Schätzpreisen, bekannten Preisen und Ende der Auswertung

**Files:**
- Modify: `src/bindaems/tools/scenario/data.py` (`ScenarioSlot`, `ScenarioFile`)
- Modify: `src/bindaems/tools/scenario/runner.py` (`price_inputs`, `run_scenario`, `_days`, `DayResult`)
- Test: `tests/unit/tools/scenario/test_runner.py`

**Interfaces:**
- Produces:
  - `ScenarioSlot.ev_w: NonNegativeFinite = 0.0` (feste Ladeleistung aller Autos in W, dem Planer unbekannt), `ScenarioSlot.ev_battery_w: NonNegativeFinite = 0.0` (so viel davon darf der Akku geben; Validator `ev_battery_w ≤ ev_w`), `ScenarioSlot.import_estimate_ct: Finite | None = None` (Schätzpreis brutto, Entscheidung 7).
  - `ScenarioFile.version: Literal[1, 2, 3]`; `ScenarioFile.prices_known: bool = False`. Validator: ein Slot mit `ev_w > 0`, `ev_battery_w > 0` oder `import_estimate_ct` und `prices_known=True` verlangen Version 3 („… verlangt Version 3“).
  - `known_until(data, now)`: mit `data.prices_known` immer `_data_end(data)`.
  - `price_inputs(data, now)`: nach `known_until` zuerst `item.import_estimate_ct` (mit `item.feed_in_ct`, `origin="estimated"`), sonst wie bisher der Median der Vortage.
  - `run_scenario(data, *, step_slots=1, grid_charge=None, until: datetime | None = None) -> ScenarioResult`: gespielt werden nur Slots mit `start < until` (Optimierer und „Victron allein“); die Slots danach sind nur Horizont des Planers. Alle Summen, `days`, Spitzen und `end_delta` zählen nur die gespielten Slots. `until` muss auf einem Slotbeginn der Datei liegen und nach dem ersten Slot (`ValueError` sonst).
  - In der Ausführung (Optimierer und „Victron allein“) kommt `item.ev_w` und `item.ev_battery_w` zu den Werten der geplanten Autos dazu (`replace(slot, ev_w=…, ev_battery_w=…)`); der Planer (`_plan_at`) sieht sie nicht.
  - `DayResult` bekommt `soc_end_kwh: float = 0.0`, `battery_loss_kwh: float = 0.0` (`Σ (charge_pv_w + charge_grid_w − discharge_w) · SLOT_H / 1000 − (soc_end − soc_start)` über die Slots des Tages), `peak_kw: float = 0.0` (höchster `import_w / 1000` des Tages), alle für den Optimierer.

- [ ] **Step 1: Failing tests** in `test_runner.py`:
  - `test_version_2_file_rejects_fixed_ev`: `ScenarioFile` Version 2 mit einem Slot `ev_w=1000` → `ValidationError` mit „Version 3“; `ev_battery_w=600, ev_w=500` → `ValidationError`.
  - `test_estimate_from_slot_replaces_median`: Datei Version 3, zwei Tage, `import_estimate_ct=11.0` an jedem Slot von Tag 2; `price_inputs(data, Tag 1 10:01 lokal)` → jeder Preis von Tag 2 ist `11.0` mit `origin == "estimated"`.
  - `test_prices_known_makes_every_price_primary`: wie oben mit `prices_known=True` → alle Preise bis Datenende `origin == "primary"` mit dem Ist-Preis.
  - `test_fixed_ev_adds_load_but_not_to_plan`: ein Tag mit `ev_w=3000` von 18:00 bis 20:00 und `ev_battery_w=0`; Bezug der Ausführung in diesen Slots ist um 3000 W höher als im selben Lauf ohne `ev_w` (PV 0, Akku an der Reserve), und die Eingangsdaten der Pläne (über einen Spion auf `assemble_inputs`) enthalten keine Fahrzeuge und dieselbe `load_w` wie ohne.
  - `test_until_stops_simulation_but_keeps_horizon`: zwei Tage, `until` = Mitternacht zwischen beiden → `len(result.days) == 1`, und der letzte Plan des Laufs hat Slots über `until` hinaus.
  - `test_until_counts_only_the_local_day_on_dst_change`: synthetische Datei mit dem 25.10.2026 (100 Slots) und dem 26.10.; `until` = lokale Mitternacht 26.10. → ein Tag, und `days[0]` zählt 100 Slots (über `peak_kw` und `battery_loss_kwh` einer Datei mit bekannter Lastspitze in Slot 99 geprüft).
  - `test_day_loss_matches_model_efficiency`: ein Tag, an dem der Akku nur aus PV lädt (4 kWh Ladung, keine Entladung, nicht voll), `roundtrip_pct=90` → `days[0].battery_loss_kwh == pytest.approx(4 · (1 − eta(90)))` (das Akkumodell bucht `ΔSOC = Ladung · η_ch`).
  - `test_version_1_and_2_files_compute_unchanged`: die bestehenden Erwartungen von `scenario_days` auf `winter-spread` (Anzahl 1, Takt 60) ändern sich nicht (bestehender Test läuft weiter grün; kein neuer Wert).
- [ ] **Step 2:** `uv run pytest tests/unit/tools/scenario/test_runner.py -q` → die neuen Tests scheitern.
- [ ] **Step 3:** Felder, Validator, `known_until`, `price_inputs`, `until` und die `DayResult`-Felder umsetzen. `report.py` bleibt unverändert.
- [ ] **Step 4:** `uv run pytest tests/unit/tools -q` grün, ganze Prüfkette.
- [ ] **Step 5:** Commit `feat(tools): Szenario-Runner mit festen Autos, Schätzpreisen und Ende der Auswertung`.

### Task 2: Rückblick eines Tages (rein)

**Files:**
- Create: `src/bindaems/tools/scenario/hindsight.py`
- Test: `tests/unit/tools/scenario/test_hindsight.py`

**Interfaces:**
- Consumes: Task 1 (`run_scenario(..., until=…)`, `DayResult.soc_end_kwh`, `.battery_loss_kwh`, `.peak_kw`, `ScenarioFile.prices_known`).
- Produces:
  - `Factor = Literal["pv", "load", "price"]`, `FACTORS: Final = ("pv", "load", "price")`
  - `Cause = Literal["pv", "load", "price", "losses", "control"]`, `CAUSES: Final = ("pv", "load", "price", "losses", "control")`
  - `@dataclass(frozen=True) class RealDay`: `day: date`, `cost_ct: float` (Energie + Verschleiß, ohne Leistungspreis), `peak_kw: float`, `charge_kwh: float`, `discharge_kwh: float`, `soc_start_pct: float`, `soc_end_pct: float`, `feed_in_provisional: bool`.
  - `@dataclass(frozen=True) class HindsightDay`: `day: date`, `real_ct: float`, `replay_ct: float`, `perfect_ct: float`, `causes: Mapping[Cause, float]`, `variants_ct: Mapping[str, float]` (Schlüssel `variant_key`), `loss_real_kwh: float`, `loss_replay_kwh: float`, `feed_in_provisional: bool`, `step_minutes: int`; Property `table_ct = real_ct − perfect_ct`.
  - `variant_key(truth: frozenset[Factor]) -> str` → Faktoren in der Reihenfolge von `FACTORS` mit `+`, leer `"none"` (z. B. `"pv+price"`).
  - `variant_file(data: ScenarioFile, truth: frozenset[Factor]) -> ScenarioFile`: `pv` → je Slot `pv_forecast_w = {"h0_6": pv_w}` und `pv_p25_w = {"h0_6": pv_w}`; `load` → `load_forecast_w = {"h0_6": load_w}`; `price` → `prices_known=True`. Version 3.
  - `shapley(costs: Mapping[frozenset[Factor], float]) -> dict[Factor, float]` nach Entscheidung 2; `KeyError`, wenn eine der acht Teilmengen fehlt.
  - `peak_cost_ct(day: date, peak_kw: float, before_kw: float | None, tariff: TariffSettings) -> float` nach Entscheidung 6 (0 ohne echten Satz).
  - `analyse_day(data: ScenarioFile, real: RealDay, *, step_slots: int) -> HindsightDay`: `until` = lokale Mitternacht nach `real.day`; die Datei beginnt um lokale Mitternacht von `real.day` (sonst `ValueError`) und trägt `battery.start_soc_pct == real.soc_start_pct`. Je Variante `C(S) = day.optimizer_ct + peak_cost_ct(...) − (day.soc_end_kwh − real.soc_end_pct/100 · usable_kwh) · eta(roundtrip_pct) · median(Bezugspreise von D)`. `real_ct = real.cost_ct + peak_cost_ct(day, real.peak_kw, data.month_peak_kw, tariff)`. `causes["losses"] = (loss_real − loss_∅) · median(...)` mit `loss_real = charge_kwh − discharge_kwh − (soc_end − soc_start)/100 · usable_kwh`; `causes["control"] = real_ct − C(∅) − causes["losses"]`.

- [ ] **Step 1: Failing tests** in `test_hindsight.py`:
  - `test_shapley_without_interaction`: Kosten ∅ 100, pv 80, load 90, price 100, pv+load 70, pv+price 80, load+price 90, alle 70 → `{"pv": 20, "load": 10, "price": 0}`.
  - `test_shapley_splits_interaction_evenly`: ∅ 100, jede einzelne 100, pv+load 80, pv+price 100, load+price 100, alle 80 → `pv == load == 10`, `price == 0`.
  - `test_shapley_needs_all_eight_variants`: sieben Einträge → `KeyError`.
  - `test_causes_sum_to_table_for_any_costs` (hypothesis, rein über eine Hilfsfunktion `_causes(real_ct, costs, losses_ct)`, die `analyse_day` nutzt): `sum(causes.values()) == pytest.approx(real_ct − costs[alle], abs=0.01)` für beliebige endliche Kosten, auch wenn „perfekt“ teurer als ∅ ist.
  - `test_variant_file_replaces_only_chosen_factor`: `variant_file(data, {"pv"})` hat `pv_forecast_w == {"h0_6": pv_w}`, die Lastprognose und `prices_known` wie vorher.
  - `test_peak_cost_only_with_real_rate`: Tarif ohne Leistungspreis → 0; mit Satz 5 €/kW und Spitze 6 kW nach 4 kW vorher → `power_cost_eur(6) − power_cost_eur(4)` in ct; Spitze unter der vorherigen → 0.
  - `test_perfect_forecasts_leave_nothing_for_forecasts` (`@pytest.mark.slow`): `synthetic("winter-spread", …)` über 2 Tage, Prognosen gleich Ist und `import_estimate_ct` gleich Ist-Preis, `RealDay` aus „nachgespielt“ gebaut → `|causes[pv]|, |causes[load]|, |causes[price]| < 0.5` ct, `step_slots=4`.
  - `test_too_high_pv_forecast_shows_in_pv` (`@pytest.mark.slow`): wie oben, aber PV-Prognose = 3 · Ist und `grid_charge_allowed=True` → `causes["pv"] > 0` und `causes["pv"]` ist die größte der drei Prognose-Ursachen.
  - `test_analyse_day_needs_day_start`: Datei beginnt um 06:00 → `ValueError`.
- [ ] **Step 2:** `uv run pytest tests/unit/tools/scenario/test_hindsight.py -q` → scheitert (Modul fehlt).
- [ ] **Step 3:** Modul umsetzen; die acht Läufe nacheinander im selben Prozess (das Parallelisieren übernimmt niemand, Entscheidung 8).
- [ ] **Step 4:** Tests grün (auch `-m slow`), ganze Prüfkette.
- [ ] **Step 5:** Commit `feat(tools): Rückblick eines Tages mit perfekter Voraussicht und Zerlegung nach Ursachen`.

### Task 3: Datei und echte Kosten eines Tages aus der Datenbank

**Files:**
- Modify: `src/bindaems/tools/export_days.py`
- Test: `tests/unit/tools/test_export_days.py`

**Interfaces:**
- Consumes: Task 1 (Felder Version 3), Task 2 (`RealDay`).
- Produces:
  - `hindsight_input(engine: Engine, cfg: Config, day: date, *, soc_start_pct: float, soc_end_pct: float, clock: Clock) -> tuple[ScenarioFile, RealDay] | str`: Datei Version 3 mit `day` und `day + 1` (beide über das bestehende `_day`, also gleiche Lückenregel und Gründe „unvollständig“, „keine Preise“; fehlt einer, kommt sein Grund zurück), `start_soc_pct=soc_start_pct`, `month_peak_kw` vor `day`; je Slot `ev_w = Σ Flüsse *>wb:* · 4` (W), `ev_battery_w = Σ battery>wb:* · 4`, `import_estimate_ct` nach Entscheidung 7 (eine `SpotHistory` je Tag, Stand lokale Mitternacht des Vortags; `None`, wenn sie keinen Wert hat).
  - `RealDay` aus den Slots von `day`: `cost_ct = Σ (import_wh · import_gross_ct − export_wh · (feed_in_ct or 0)) / 1000 + wear_ct · Σ battery>wb:* / 1000`, `peak_kw = max(import_wh · 4 / 1000)`, `charge_kwh = Σ *>battery / 1000`, `discharge_kwh = Σ battery>* / 1000`, `feed_in_provisional = Monat von day nicht in feed_in.monthly_ct`.
  - `_day` bekommt die Schlüsselwörter `with_cars: bool = False` und `with_estimates: bool = False`; `export_days` und die Kommandozeile bleiben ohne sie (Version 1 unverändert).

- [ ] **Step 1: Failing tests** in `test_export_days.py` (mit der bestehenden Datenbank-Hilfe):
  - `test_hindsight_input_has_two_days_and_version_3`: 2 Tage Abrechnung und Preise → 192 Slots, Version 3, `start_soc_pct` wie übergeben.
  - `test_hindsight_input_takes_cars_from_wallbox_flows`: Slot mit `pv>wb:evcs` 250 Wh und `battery>wb:evcs` 100 Wh → `ev_w == 1400.0`, `ev_battery_w == 400.0`, `load_w` ohne Auto.
  - `test_hindsight_input_estimate_is_as_of_previous_midnight`: Börsenpreise mit bekanntem Median vor dem Vortag, dazu ein Ausreißer am Vortag selbst → `import_estimate_ct` ist der Bruttopreis des Medians ohne den Ausreißer.
  - `test_hindsight_input_missing_next_day_returns_reason`: D+1 ohne Preise → `"keine Preise"`.
  - `test_hindsight_input_on_autumn_change_has_100_slots`: `day = 2026-10-25` → die ersten 100 Slots gehören zum 25.10.
  - `test_real_day_cost_from_ledger`: zwei Slots (1 kWh Bezug zu 30 ct, 2 kWh Einspeisung zu 8 ct) → `cost_ct == 30 − 16`.
  - `test_real_day_without_feed_in_is_provisional`: kein OeMAG-Wert → Einspeisung zählt 0 und `feed_in_provisional is True`.
  - `test_export_days_still_writes_version_1`: bestehender Aufruf → `version == 1`, kein Slot mit `ev_w`.
- [ ] **Step 2:** `uv run pytest tests/unit/tools/test_export_days.py -q` → neue Tests scheitern.
- [ ] **Step 3:** umsetzen.
- [ ] **Step 4:** Tests grün, ganze Prüfkette.
- [ ] **Step 5:** Commit `feat(tools): Datei und echte Kosten eines Tages für den Rückblick`.

### Task 4: Ablage der Ergebnisse

**Files:**
- Create: `src/bindaems/app/db/migrations/versions/v0016_hindsight.py`, `src/bindaems/app/hindsight/__init__.py`, `src/bindaems/app/hindsight/store.py`
- Modify: `src/bindaems/app/db/schema.py`, `pyproject.toml` (Importvertrag)
- Test: `tests/unit/app/hindsight/test_store.py`, `tests/unit/app/test_db.py`

**Interfaces:**
- Consumes: Task 2 (`HindsightDay`, `CAUSES`).
- Produces:
  - Tabelle `hindsight_day`: `day` (Text `JJJJ-MM-TT`, Primärschlüssel), `computed_at`, `status` (`ok` | `skipped`), `reason` (Text oder `NULL`), `real_ct`, `replay_ct`, `perfect_ct`, `pv_ct`, `load_ct`, `price_ct`, `losses_ct`, `control_ct` (alle `NULL` bei `skipped`), `feed_in_provisional` (bool), `step_minutes`, `version` (Text, `bindaems.__version__`), `detail` (JSON: `variants_ct`, `loss_real_kwh`, `loss_replay_kwh`).
  - `@dataclass(frozen=True) class StoredDay`: `day: date`, `computed_at: datetime`, `status: Literal["ok", "skipped"]`, `reason: str | None`, `result: HindsightDay | None`.
  - `HindsightStore(engine)`: `save(result: HindsightDay, now: datetime) -> None`, `skip(day: date, reason: str, now: datetime) -> None` (beides als Upsert), `between(first: date, last: date) -> list[StoredDay]` (nach Tag sortiert), `cleanup(today: date) -> int` (löscht Tage vor `today − 400`).
  - Importvertrag in `pyproject.toml`: `name = "app importiert tools nur im Rückblick"`, `type = "forbidden"`, `source_modules = ["bindaems.app"]`, `forbidden_modules = ["bindaems.tools"]`, `ignore_imports = ["bindaems.app.hindsight.** -> bindaems.tools.**"]`; dazu `name = "Werkzeuge kennen den Rückblick der app nicht"`, `source_modules = ["bindaems.tools"]`, `forbidden_modules = ["bindaems.app.hindsight"]`.

- [ ] **Step 1: Failing tests:** `test_save_and_read_roundtrip` (alle Felder einschließlich `detail` kommen gleich zurück), `test_skip_overwrites_result` und umgekehrt, `test_between_sorted_and_inclusive`, `test_cleanup_keeps_400_days`; in `test_db.py` `test_migration_0016_creates_hindsight_day` (Upgrade und Downgrade).
- [ ] **Step 2:** Tests scheitern.
- [ ] **Step 3:** Migration, Schema, Store und Importvertrag umsetzen.
- [ ] **Step 4:** Tests grün, `uv run lint-imports` grün, ganze Prüfkette.
- [ ] **Step 5:** Commit `feat(app): Ablage des Rückblicks je Tag (Migration 0016)`.

### Task 5: Nachtdienst „Rückblick“

**Files:**
- Create: `src/bindaems/app/hindsight/process.py`, `src/bindaems/app/hindsight/service.py`
- Modify: `src/bindaems/shared/config.py` (`AppConfig.hindsight`), `src/bindaems/app/runtime.py` (Job `hindsight`, Status, Wartung), `deploy/config.example.yaml`
- Test: `tests/unit/app/hindsight/test_service.py`, `tests/unit/app/hindsight/test_process.py`, `tests/unit/shared/test_config.py`

**Interfaces:**
- Consumes: Task 2 (`analyse_day`), Task 3 (`hindsight_input`), Task 4 (`HindsightStore`).
- Produces:
  - `class HindsightConfig(_Model)`: `enabled: bool = True`, `first_day: date | None = None`, `step_minutes: Literal[15, 60] = 15`; `AppConfig.hindsight: HindsightConfig = Field(default_factory=HindsightConfig)`.
  - `RUN_AT: Final = time(4, 30)`, `RUN_BUDGET: Final = timedelta(hours=2)`, `DAY_TIMEOUT_S: Final = 1800.0`, `MAX_BACK_DAYS: Final = 62`, `DEFAULT_BACK_DAYS: Final = 14`, `RETRY_SKIPPED_DAYS: Final = 7`.
  - `due_days(today: date, stored: Sequence[StoredDay], cfg: HindsightConfig, months_known: Collection[str]) -> list[date]` (rein, Entscheidung 10): älteste zuerst, letzter Tag `today − 2`.
  - `async soc_at(reader: InfluxReader, moment: datetime) -> float | None`: Mittel von `soc.pct` mit `id='battery'` in `[moment − 2 min, moment + 2 min)` aus RP `long`, sonst `raw`; `None` ohne Wert oder bei `InfluxQueryError`.
  - `async run_day_in_child(data: ScenarioFile, real: RealDay, step_slots: int, timeout_s: float) -> HindsightDay | str`: Kindprozess `spawn` wie `planning/process.py` (JSON hinein, Ergebnis über `Pipe`, nach Zeitlimit `terminate`, nach `JOIN_S` `kill`), im Kind zuerst `os.nice(10)`; Rückgabe `"Rechnung gescheitert"` bei Zeitlimit, Ausnahme oder Ende ohne Antwort. `HindsightDay` und `RealDay` gehen als JSON über ein pydantic-`TypeAdapter`.
  - `class HindsightService`: `__init__(engine, cfg: Config, reader: InfluxReader, settings: CurrentSettings, clock: Clock, store: HindsightStore, runner=run_day_in_child)`, `async run()` (täglich um `RUN_AT` Ortszeit, beim Start nichts), `async run_once(now: datetime) -> int` (rechnet die fälligen Tage; ein neuer Tag beginnt nur, solange die Uhr vor `now + RUN_BUDGET` steht; liefert die Zahl gerechneter Tage), `status() -> ComponentStatus` (Name „Rückblick“; `ok` mit letztem Lauf und Zahl der Tage, `warning` wenn der letzte Lauf einen Tag „Rechnung gescheitert“ hatte, `off` mit `enabled: false`).
  - Je Tag: SOC um lokale Mitternacht von D und D+1 (`soc_at`), dann `hindsight_input` in `asyncio.to_thread`, dann `runner`; jeder Grund geht als `store.skip`, ein Ergebnis als `store.save`. `step_slots = step_minutes // 15`.
  - In `runtime.py`: Job `("hindsight", self.hindsight.run)` nur mit `enabled`; `store.cleanup` im bestehenden `_maintenance_loop`; Status in der Liste der Komponenten.

- [ ] **Step 1: Failing tests:**
  - `test_due_days_*` (rein): ohne `first_day` die letzten 14 Tage bis `today − 2`; `first_day` vor `today − 62` wird auf 62 Tage gekappt; ein Tag mit Ergebnis entfällt; ein übersprungener Tag kommt bis 7 Tage nach D wieder, danach nicht; `test_provisional_day_is_recomputed_when_month_is_known` (vorläufig und Monat jetzt in `months_known` → fällig).
  - `test_missing_soc_skips_day_and_retries`: `soc_at` gibt `None` → `skip(day, "kein SOC")`; in der nächsten Nacht ist der Tag wieder fällig.
  - `test_run_once_stops_after_budget`: Runner-Fake verbraucht je Tag 50 min (Fake-Uhr) → nach 3 Tagen Schluss, der Rest bleibt fällig.
  - `test_hanging_child_is_killed_and_day_marked_failed` (`test_process.py`, mit einer Kind-Funktion, die schläft, und `timeout_s=1`): Rückgabe `"Rechnung gescheitert"`, kein Kind lebt danach (`multiprocessing.active_children() == []`).
  - `test_child_returns_result`: kleine synthetische Datei mit `step_slots=4` → `HindsightDay` mit demselben `day`.
  - `test_soc_at_reads_long_then_raw` und `test_soc_at_returns_none_on_query_error` (Fake-Reader).
  - `test_config.py`: `app.hindsight` fehlt → Standard; `step_minutes: 30` → `ValidationError`.
- [ ] **Step 2:** Tests scheitern.
- [ ] **Step 3:** umsetzen; `config.example.yaml` bekommt `app.hindsight` auskommentiert mit `first_day: 2026-11-04` als Beispiel.
- [ ] **Step 4:** Tests grün, ganze Prüfkette.
- [ ] **Step 5:** Commit `feat(app): Rückblick jede Nacht um 04:30 Uhr im eigenen Prozess`.

### Task 6: API für den Rückblick

**Files:**
- Create: `src/bindaems/app/hindsight/routes.py`
- Modify: `src/bindaems/app/runtime.py` (Router), `tests/ui_world.py` (Demo-Daten), `ui/src/lib/api/contract/hindsight-month.json` (neu, erzeugt)
- Test: `tests/unit/app/hindsight/test_routes.py`, `tests/integration/test_ui_contract.py`

**Interfaces:**
- Consumes: Task 4 (`HindsightStore.between`), `app/stats/service.period_range`, `Period`.
- Produces: `hindsight_router(store: HindsightStore, clock: Clock, guard: Guard) -> APIRouter`, `GET /api/stats/hindsight?period=day|week|month|year&day=JJJJ-MM-TT` (Rolle Lesen; ohne `day` heute). Antwort:
  ```json
  {"first": "2026-11-01", "last": "2026-11-30", "days_in_range": 30, "analysed_days": 23,
   "pending_days": 2, "provisional": false, "step_minutes": 15,
   "totals": {"real_eur": 41.20, "replay_eur": 38.10, "perfect_eur": 28.80, "table_eur": 12.40,
              "causes_eur": {"pv": 5.10, "load": 2.30, "price": 0.80, "losses": 1.90, "control": 2.30}},
   "days": [{"day": "2026-11-04", "table_eur": 0.62, "causes_eur": {"pv": 0.40, "...": 0},
             "provisional": false}],
   "skipped": [{"day": "2026-11-09", "reason": "kein SOC"}]}
  ```
  `pending_days` = Tage im Zeitraum ab `first_day` bis `heute − 2` ohne Eintrag, dazu `heute − 1` und `heute`, soweit im Zeitraum. Summen über die Tage mit `status == "ok"`, gerundet erst in der Antwort. `provisional` ist wahr, wenn ein Tag vorläufig ist.

- [ ] **Step 1: Failing tests:** `test_month_sums_causes_and_counts_days`, `test_skipped_days_listed_with_reason`, `test_empty_range_has_zero_days_and_null_totals` (`totals` ist `null`, wenn `analysed_days == 0`), `test_needs_read_role`, `test_rejects_bad_period` (422); Vertragstest erzeugt `hindsight-month.json`.
- [ ] **Step 2:** Tests scheitern.
- [ ] **Step 3:** Router und Demo-Welt umsetzen; `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py`.
- [ ] **Step 4:** Tests grün, ganze Prüfkette.
- [ ] **Step 5:** Commit `feat(app): Rückblick als API für die Statistik`.

### Task 7: Karte „Was lag auf dem Tisch?“ auf „Statistik“

**Files:**
- Create: `ui/src/lib/stats/HindsightCard.svelte`, `ui/src/lib/stats/hindsight.ts`, `ui/src/lib/stats/HindsightCard.test.ts`, `ui/src/lib/stats/hindsight.test.ts`
- Modify: `ui/src/lib/api/schemas.ts`, `ui/src/lib/api/endpoints.ts`, `ui/src/routes/statistik/+page.svelte` (Karte unter der Kostenkarte), `ui/tests/e2e/stats.spec.ts`
- Test: wie oben, dazu der Schematest gegen `hindsight-month.json`

**Interfaces:**
- Consumes: Task 6 (Antwort, Vertragsdatei).
- Produces: `api.hindsight(period, day)`, valibot-Schema `Hindsight`; in `hindsight.ts`:
  - `CAUSE_LABELS: Record<Cause, string>` = `pv` „PV-Prognose“, `load` „Lastprognose“, `price` „Schätzpreise“, `losses` „Akkuverluste (geschätzt)“, `control` „Regelung und Rest“.
  - `CAUSE_HINTS: Record<Cause, string>` = `pv` „Schnee, Hochnebel, vorsichtiger planen“, `load` „Anwesenheit, besseres Lastmodell“, `price` „bessere Schätzpreise am Vormittag“, `losses` „echte Wirkungsgrade und Standby im Planer“, `control` „Regelung, Autos bei ‚Geplant‘, Abweichungen der Anlage“.
  - `causeRows(totals) -> {cause, label, hint, eur, luck: boolean, share: number}[]`, absteigend nach `eur`; `luck` bei `eur < 0`; `share` = Anteil an der Summe der positiven Werte (für die Balkenbreite, 0 bei negativen).
  - `summaryText(data) -> string`:
    - mit Tagen: „In {analysed_days} von {days_in_range} Tagen hätte perfekte Voraussicht {table_eur} € gespart.“ (`table_eur` mit Komma, 2 Stellen)
    - ohne Tage, aber fällige: „Noch keine Tage berechnet. Ein Tag erscheint am übernächsten Morgen.“
    - Tagesansicht ohne Eintrag: „Dieser Tag wird am übernächsten Morgen berechnet.“
  - Zusatz bei `provisional`: „Vorläufig: Für einen Monat fehlt noch der OeMAG-Wert.“
  - Erklärzeile unter der Überschrift (fester Text): „Der Planer rechnet den Tag im Nachhinein mit den echten Werten nach. Je größer ein Balken, desto mehr lohnt sich die Verbesserung daneben.“
  - Negative Werte: „−0,40 € (Glück)“.
  - Übersprungene Tage als eingeklappte Liste „{n} Tage übersprungen“ mit Datum und Grund.

- [ ] **Step 1: Failing tests:** `hindsight.test.ts`: Sortierung, `luck`, `share`, die drei Texte von `summaryText`; `HindsightCard.test.ts`: rendert fünf Zeilen mit Beschriftung und Hinweis, „(Glück)“ bei negativem Wert, die Liste der übersprungenen Tage, den Vorläufig-Satz; Schematest: `hindsight-month.json` passt. E2E: Statistik Monat zeigt die Karte mit „Was lag auf dem Tisch?“ und „PV-Prognose“.
- [ ] **Step 2:** `cd ui && pnpm test` → neue Tests scheitern.
- [ ] **Step 3:** umsetzen; Balken als einfache CSS-Balken mit den Farben der Palette (kein ECharts nötig), bei 390 px Breite ohne waagrechtes Scrollen, im hellen und dunklen Thema lesbar.
- [ ] **Step 4:** `pnpm check && pnpm lint && pnpm test`, dann `pnpm build && pnpm e2e` (lokal mit `launchOptions.executablePath: /opt/pw-browsers/chromium`); Bildschirmfoto bei 390 px hell und dunkel nach `/mnt/project-files/plaene/was-lag-auf-dem-tisch/`.
- [ ] **Step 5:** Commit `feat(ui): Karte „Was lag auf dem Tisch?“ auf der Statistik`.

### Task 8: Doku

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absatz „Stand Plan Was lag auf dem Tisch?“ unter 10.5 und ein Satz in 13 „Statistik“), `docs/betrieb.md` (Abschnitt „Rückblick (Was lag auf dem Tisch?)“: was die Zahlen bedeuten, `app.hindsight`, Laufzeit, Gründe fürs Überspringen, Ausrollen mit Sicherung vor `0016`), `docs/uebergabe.md` (Stand, Ausrollen nicht vor 23.11.2026, Abnahme nach Entscheidung 15), `tests/unit/test_docs.py`
- Test: `tests/unit/test_docs.py::test_guide_describes_the_hindsight` (die fünf Beschriftungen aus `CAUSE_LABELS`, der Schlüssel `app.hindsight.first_day` und die Komponente „Rückblick“ stehen in `docs/betrieb.md`)

- [ ] **Step 1:** Doku-Test schreiben, scheitert.
- [ ] **Step 2:** Doku schreiben, Test grün, ganze Prüfkette (Python und UI).
- [ ] **Step 3:** Commit `docs: Rückblick in Spec, Betrieb und Übergabe`.

## Nicht in diesem Plan

- Autos als planbare Ansteckvorgänge im Rückblick (Verschieben des Autoladens bewerten, Entscheidung 4).
- Ein Werkzeug für die Kommandozeile; die Zahlen stehen in der Ablage und in der API.
- HA-Sensoren oder Benachrichtigungen zum Rückblick.
- Neu rechnen alter Tage nach einem neuen Planer (Entscheidung 10); bei Bedarf eine Taste in einem späteren Plan.

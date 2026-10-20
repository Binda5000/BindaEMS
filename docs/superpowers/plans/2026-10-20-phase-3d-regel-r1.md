# Phase 3d – Regel R1 nur bis der Akku leer ist: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Regel R1 sperrt Netzladen nur noch, wenn die Netzenergie beim nächsten PV-Überschuss noch im Akku wäre, nicht mehr, sobald irgendwo im restlichen Horizont eingespeist wird.

**Architecture:** Nur app (Planung) und Szenario-Werkzeug; core, `shared`, UI, HA und `config.yaml` bleiben unverändert.
- **Nachrechnung** (`app/planning/plausibility.py`): Die neue Funktion `export_while_charged_kwh` ersetzt `export_after_kwh`. Sie zählt die Einspeisung ab dem `NETZLADEN`-Slot nur bis zum Ende des ersten Slots, in dem der Akku leer ist. Prüfung, Kürzung der Absichten, Ersatzplan, Erklärung und Szenario-Werkzeug rechnen damit.
- **Optimierer** (`app/planning/optimizer.py`): Statt `r1_t` sperrt eine Kette „Netzenergie kann noch im Akku sein“ (`c_t`) die PV-Einspeisung, bis eine Binärvariable „Akku leer“ (`e_t`) sie löst.

**Tech Stack:** wie Plan 3b (Python, PuLP mit HiGHS, pytest, Hypothesis). Keine neuen Abhängigkeiten, keine Migration.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Regel R (Abschnitt Optimierer, „Regel R1 hart, ohne ρ“), Ersatzplan, Plausibilität und Präzisierung 19 (Szenarien). Die Absätze „Stand Plan …“ gehen dem älteren Text vor; dieser Plan fügt „Stand Plan 3d“ hinzu (Task 4).

## Einordnung

- **Anlass:** Der Betreiber hat am 20.10.2026 klargestellt, wofür R1 gedacht ist: Der Akku soll nicht mit Netzstrom gefüllt werden, wenn noch genug PV kommt. Laden aus dem Netz soll möglich sein, wenn die erwartete PV den Bedarf bis dahin nicht deckt.
- **Heute** (`optimizer.py`, Nebenbedingung `r1_t`): Kein Netzladen in Slot t, sobald laut P50 von t bis zum Horizontende (bis 36 h) irgendwann eingespeist wird. Beispiel: heute trüb, morgen sonnig. Dann lädt der Planer heute nicht billig aus dem Netz, obwohl der Akku bis morgen früh ohnehin leer wäre.
- **Neu:** Die Sperre reicht vom `NETZLADEN`-Slot bis zum Ende des ersten Slots, in dem der Akku leer ist (SOC auf der Reserve). Ist der Akku vorher leer, verdrängt der Netzstrom keine PV; dann rechnen nur noch die Kosten.
- **Warum „leer“ das richtige Kriterium ist:** Mit und ohne Netzladen laufen die SOC-Verläufe erst wieder zusammen, wenn der Akku auf der Reserve ankommt. Bleibt er darüber, hat er beim PV-Überschuss mehr Energie, und es wird um genau diese Menge mehr eingespeist.
- **Ausführung:** Victron ist bis etwa 2.11.2026 im Dry-Run der Planausführung. Die neue Regel wirkt an der Anlage erst mit einem Update der app; das Image `latest` enthält schon 4b und 6a (Entscheidung 6).
- **Parallel:** Plan 5a wird in einem anderen Thread umgesetzt. Beide ändern `docs/uebergabe.md` und die Spec; wer zweiter nach `main` geht, führt die Doku zusammen.

## Vorgeschlagene Entscheidungen (zur Freigabe)

1. **„Akku leer“** heißt SOC am Ende des Slots höchstens Reserve plus Toleranz (`lo_kwh` aus `params_of`). Toleranz im Optimierer 0,05 kWh (`R1_EMPTY_TOL_KWH`), in der Nachrechnung 0,1 kWh (`EMPTY_TOL_KWH`, gleich `R1_TOL_KWH`). Die größere Toleranz der Nachrechnung fängt die Rundung der Absichten ab.
2. **Fenster der Regel:** vom `NETZLADEN`-Slot (eingeschlossen) bis zum Ende des ersten Slots, in dem der Akku leer ist (eingeschlossen). Wird er bis zum Horizontende nicht leer, gilt das ganze Restfenster wie bisher. Die Toleranz der Einspeisung im Fenster bleibt 0,1 kWh (`R1_TOL_KWH`).
3. **R2 (Autos) bleibt unverändert.** R2 erlaubt Netzenergie fürs Auto schon heute nur so weit, wie der PV-Überschuss sie nicht deckt.
4. **Erklärung:** Code `REGEL_R_BLOCKIERT_NETZLADEN` und seine Parameter `um` und `kwh` bleiben (kein Schema- oder UI-Wechsel). `um` ist neu der Beginn der ersten Einspeisung im Fenster statt des Horizontendes, `kwh` die Einspeisung im Fenster. Neuer Satz: „Kein Netzladen: Ab {um} Uhr speist die PV laut Prognose {kwh} kWh ein, bevor der Akku leer ist (Regel R).“
5. **Szenario-Werkzeug:** Ein Verstoß gegen Regel R (Präzisierung 19) und die Kennzahl „Netzladen vor tatsächlicher Einspeisung“ rechnen mit demselben Fenster. Die Kennzahl prüft über den ganzen Lauf statt je lokalem Tag; die Gruppierung je Tag entfällt dort.
6. **Ausrollen:** Das entscheidet der Betreiber beim Abschluss. Vorschlag: zusammen mit 4b und 6a in Abschnitt 9 der Schrittliste. Wirken kann die Regel erst, wenn Victron live ist.

## Global Constraints

- Deutsch, Commits im Stil `fix(app): …`/`feat(app): …`/`docs: …`, keine Modellbezeichnungen im Repo.
- Vor jedem Commit: `ruff format --check`, `ruff check`, `mypy` (strict laut `pyproject.toml`; src und cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators). UI wird nicht geändert; `pnpm check`, `pnpm lint` und `pnpm test` laufen trotzdem einmal am Ende (Task 4).
- In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Kein Aktor ist live; nichts an der Anlage schalten.
- Zeitlimit des Optimierers bleibt 20 s (`TIME_LIMIT_S`), `MIP_GAP` 0,005.

## Review Focus

1. **Lösezeit:** Bis zu 144 zusätzliche Binärvariablen. Winter mit Auto und Leistungspreis lag im Dev-Container schon an der 20-s-Grenze. Task 2 misst vorher und nachher.
2. **SOC unter der Reserve beim Start** (`lo_kwh = soc0`): Der Akku zählt ab Slot 0 als leer; Netzladen darf dann nicht trotzdem gesperrt sein, und die Kette darf nicht unlösbar werden (Task 2, Test 3).
3. **Rundung der Absichten:** Die Nachrechnung kommt bei 0,06–0,09 kWh über der Reserve an, wo der Optimierer 0,05 kWh plante. Das darf kein Befund sein, und die Kürzungsschleife in `_keep_rule_r1` muss enden (Task 1, Test 2).
4. **Kein Leerstand bis Horizontende:** Verhalten wie bisher; Einspeisung irgendwo danach sperrt (Task 1, Test 1c; Task 2, Test 2).
5. **Akku auf der Reserve während PV einspeist** (Überschuss über der Ladeleistung): Der Slot zählt als leer und löst die Sperre für spätere Slots; die Einspeisung in diesem Slot zählt noch mit (Task 1, Test 1b).

---

### Task 1: Nachrechnung – Einspeisung nur bis der Akku leer ist

**Files:**
- Modify: `src/bindaems/app/planning/plausibility.py` (`export_after_kwh` ersetzen, Prüfung `r1`)
- Modify: `src/bindaems/app/planning/intents.py` (`_keep_rule_r1`)
- Modify: `src/bindaems/app/planning/fallback.py` (Kandidatenprüfung)
- Test: `tests/unit/app/planning/test_plausibility.py`, `tests/unit/app/planning/test_intents.py`, `tests/unit/app/planning/test_fallback.py`

**Interfaces:**
- Produces:
  - `EMPTY_TOL_KWH: Final = 0.1` in `plausibility.py`
  - `charged_window_end(steps: Sequence[SimStep], index: int, lo_kwh: float) -> int`: erster Index j ≥ `index` mit `steps[j].soc_end_kwh <= lo_kwh + EMPTY_TOL_KWH`, zurück `j + 1`; ohne solchen Slot `len(steps)`.
  - `export_while_charged_kwh(steps: Sequence[SimStep], index: int, lo_kwh: float) -> float`: Einspeisung in kWh über `steps[index:charged_window_end(...)]`.
  - `export_after_kwh` entfällt; alle Aufrufer nehmen `export_while_charged_kwh` mit `params_of(inputs).lo_kwh`.

- [ ] **Step 1: Failing tests schreiben**

In `test_plausibility.py` (Schritte mit `SimStep` direkt bauen, Reserve `lo = 2.0`):
- `test_export_while_charged_stops_when_battery_is_empty`: SOC-Enden `[4.0, 2.05, 3.0]`, Einspeisung `[0, 0, 3000]` W → `export_while_charged_kwh(steps, 0, 2.0) == 0.0` und `charged_window_end(steps, 0, 2.0) == 2` (1a).
- `test_export_while_charged_counts_export_in_the_empty_slot`: SOC-Enden `[4.0, 2.0]`, Einspeisung `[0, 2000]` W → `== 0.5` (1b, Review Focus 5).
- `test_export_while_charged_without_empty_slot_counts_to_the_end`: SOC-Enden `[4.0, 3.0, 5.0]`, Einspeisung `[0, 0, 4000]` W → `== 1.0` und `charged_window_end(...) == 3` (1c).
- `test_r1_allows_grid_charge_used_up_before_pv`: `scenario(prices=[5.0, 40.0, 8.0], pv=[0.0, 0.0, 9000.0], load=[500.0, 4000.0, 500.0], battery=small_battery(), soc_pct=20.0, grid_charge=True)`, Absichten `[NETZLADEN 2000 W, EIGENVERBRAUCH, EIGENVERBRAUCH]`. Die 0,5 kWh Netzladung sind in Slot 1 verbraucht, der Akku ist leer, bevor Slot 2 einspeist. Erwartet: kein Befund, der mit „Regel R verletzt“ beginnt. Heute: Befund.
- `test_r1_on_resimulation_is_reported` bleibt unverändert (90 % SOC, keine Leerung).

In `test_intents.py` (Review Focus 3):
- `test_grid_charge_survives_rounding_above_the_reserve`: Optimierer-Flüsse mit Netzladen in Slot 0, Entladung bis Reserve + 0,05 kWh in Slot 1, PV-Einspeisung in Slot 2. Nach `derive_intents` steht in Slot 0 weiter `NETZLADEN`, und `export_while_charged_kwh(resimulate(inputs, intents), 0, lo) <= R1_TOL_KWH`.
- In `test_grid_charge_is_cut_so_the_resimulation_keeps_rule_r1` die letzte Zeile auf `export_while_charged_kwh(..., 1, params_of(inputs).lo_kwh)` umstellen; sonst bleibt der Test.

In `test_fallback.py`:
- `test_grid_charge_is_blocked_by_feed_in_after_it` aufteilen:
  - `test_fallback_charges_what_the_evening_uses_before_pv`: das bisherige `blocked`-Szenario (PV ab Slot 12). Erwartet: mindestens ein `NETZLADEN`, und für jeden `NETZLADEN`-Slot i gilt `export_while_charged_kwh(steps, i, lo) <= R1_TOL_KWH` an der Nachrechnung der Absichten. Heute: kein `NETZLADEN`.
  - `test_grid_charge_is_blocked_when_it_lasts_into_pv`: dieselbe Basis mit PV 8000 W ab Slot 10, also mitten im teuren Block. Erwartet: kein `NETZLADEN`. Die Zahlen so wählen, dass die kleinste Ladung des Ersatzplans (ein Slot mit voller Leistung) bei Einspeisebeginn noch mehr als 0,1 kWh über der Reserve liegt; die Rechnung als Kommentar in den Test.

- [ ] **Step 2: Tests laufen lassen, sie scheitern**

Run: `uv run pytest tests/unit/app/planning/test_plausibility.py tests/unit/app/planning/test_intents.py tests/unit/app/planning/test_fallback.py -q`
Expected: FAIL (ImportError für `export_while_charged_kwh`, danach die Verhaltens-Tests).

- [ ] **Step 3: Implementieren**

`charged_window_end` und `export_while_charged_kwh` in `plausibility.py`. In `check_plan`, `_keep_rule_r1` (beide Stellen: Erkennung und `excess_w`) und `fallback_intents` (Kandidat) `export_after_kwh(steps, i)` durch `export_while_charged_kwh(steps, i, params.lo_kwh)` ersetzen. Docstrings und Modul-Docstring von `fallback.py` auf „bis der Akku leer ist“ anpassen.

- [ ] **Step 4: Tests laufen lassen, sie bestehen**

Run: `uv run pytest tests/unit/app/planning -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(app): Regel R an der Nachrechnung nur bis der Akku leer ist"
```

### Task 2: Optimierer – Sperre der Einspeisung bis zum Leerstand

**Files:**
- Modify: `src/bindaems/app/planning/optimizer.py` (Block „Regel R1“, Modul-Docstring)
- Test: `tests/unit/app/planning/test_optimizer.py`, `tests/property/test_optimizer.py`

**Interfaces:**
- Consumes: `params_of(inputs).lo_kwh`, `hi_kwh`.
- Produces: `R1_EMPTY_TOL_KWH: Final = 0.05` in `optimizer.py`. `OptimizerResult` und `SlotFlows` bleiben unverändert.

- [ ] **Step 1: Failing tests schreiben** (`test_optimizer.py`)

1. `test_r1_allows_grid_charge_used_up_before_pv_surplus`: das bisherige `blocked`-Szenario aus `test_r1_blocks_grid_charge_before_pv_surplus` (Preise `[5]*4 + [40]*4 + [8]*8`, PV ab Slot 8 mit 9000 W, Last `[500]*4 + [3000]*4 + [500]*8`, `small_battery()`, SOC 20 %, Netzladen an). Erwartet: `sum(f.gr_b for f in flows[:4]) > 0`; für jeden Slot i mit `gr_b > 1` ist die PV-Einspeisung von i bis zum ersten Slot j ≥ i mit `soc_end_kwh <= lo + R1_EMPTY_TOL_KWH` (eingeschlossen) höchstens 0,001 kWh. Heute: kein Netzladen.
2. `test_r1_blocks_grid_charge_that_lasts_into_pv_surplus`: Preise `[5]*4 + [8]*8 + [40]*4`, PV `[0]*4 + [9000]*8 + [0]*4`, Last `[500]*4 + [500]*8 + [3000]*4`, `feed_in=20.0`, `small_battery()`, SOC 20 %, Netzladen an. Ohne R1 lohnte es sich, billig zu laden und dafür PV zu 20 ct einzuspeisen; der Akku wird vor der PV nicht leer. Erwartet: `all(f.gr_b < 1 for f in flows)`. (Besteht auch heute; sichert die Sperre.)
3. `test_r1_with_soc_below_reserve_at_start`: SOC 10 % bei Reserve 20 % (`lo = soc0`), Preise `[5]*4 + [40]*4`, PV `[0]*8`, Last `[500]*4 + [3000]*4`. Erwartet: `status == "optimal"` und Netzladen in den ersten 4 Slots (Review Focus 2).
- `test_r1_blocks_grid_charge_before_pv_surplus` entfällt (ersetzt durch 1 und 2).

`tests/property/test_optimizer.py`: Die Prüfung `later <= 0.001` nach `gr_b > 1` zählt nur noch die `pv_x` von `index` bis zum ersten Slot mit `soc_end_kwh <= params.lo_kwh + R1_EMPTY_TOL_KWH` (eingeschlossen); Testname bleibt.

- [ ] **Step 2: Tests laufen lassen, Test 1 scheitert**

Run: `uv run pytest tests/unit/app/planning/test_optimizer.py -q -k r1`
Expected: FAIL in `test_r1_allows_grid_charge_used_up_before_pv_surplus`.

- [ ] **Step 3: Lösezeit vorher messen**

Run: `uv run pytest tests/integration/test_planning_scenarios.py tests/property/test_optimizer.py -q --durations=10`
Die zehn längsten Dauern im Ledger notieren.

- [ ] **Step 4: Implementieren**

Den Block `r1_t` ersetzen. Nur wenn `z` nicht leer ist, mit `first = min(z)` für `t in range(first, count)`:

```text
e_t ∈ {0,1}:   soc[t] ≤ lo + R1_EMPTY_TOL_KWH + (hi − lo) · (1 − e_t)          # Akku am Ende von t leer
c_t ∈ [0,1]:   c_t ≥ z_t (wenn t ∈ z);  c_t ≥ c_{t−1} − e_{t−1} (t > first)      # Netzenergie noch im Akku
               pv_x[t] ≤ slot.pv_w · (1 − c_t)   (nur wenn slot.pv_w > 0)
```

Namen der Nebenbedingungen `leer_{t}`, `netz_im_akku_{t}`, `r1_{t}`. `c_t` bleibt stetig: Es ist nur nach unten beschränkt, und der Löser drückt es so weit wie möglich. Kommentar und Modul-Docstring („Regel R1 ist hart: …“) auf die neue Fassung umschreiben.

- [ ] **Step 5: Tests laufen lassen, sie bestehen; Lösezeit nachher**

Run: `uv run pytest tests/unit/app/planning tests/property/test_optimizer.py -q`
Expected: PASS.
Run: `uv run pytest tests/integration/test_planning_scenarios.py tests/property/test_optimizer.py -q --durations=10`
Expected: PASS, kein Lauf mit `time_limit`. Die Dauern ins Ledger. Wächst die längste um mehr als 50 %, vor dem Commit dem Controller melden (Review Focus 1); dann `e_t` und `c_t` nur ab dem ersten Slot mit `pv_w > 0` nach `first` anlegen und neu messen.

- [ ] **Step 6: Commit**

```bash
git commit -m "feat(app): Optimierer sperrt Einspeisung nur, solange Netzenergie im Akku ist"
```

### Task 3: Erklärung und Szenario-Werkzeug

**Files:**
- Modify: `src/bindaems/app/planning/explain.py` (Satz und Ermittlung von `REGEL_R_BLOCKIERT_NETZLADEN`)
- Modify: `src/bindaems/tools/scenario/runner.py` (Verstoß in `_run`, `_charge_before_export_kwh`, Modul-Docstring „Regel R (Präzisierung 19)“)
- Modify: `docs/betrieb.md` (Tabellenzeile `REGEL_R_BLOCKIERT_NETZLADEN`)
- Test: `tests/unit/app/planning/test_explain.py`, `tests/unit/tools/scenario/test_runner.py`, `tests/unit/test_docs.py`

**Interfaces:**
- Consumes: `export_while_charged_kwh`, `charged_window_end`, `EMPTY_TOL_KWH` aus Task 1.

- [ ] **Step 1: Failing tests schreiben**

- `test_explain.py`: Der erwartete Satz für `{"um": "12:00", "kwh": 7.25}` wird „Kein Netzladen: Ab 12:00 Uhr speist die PV laut Prognose 7,3 kWh ein, bevor der Akku leer ist (Regel R).“ (Rundung wie bisher mit `_de(…, 1)`). Neuer Test `test_rule_r_reason_names_first_export_before_empty`: ein Plan, in dem sich Netzladen lohnte und die PV ab 13:00 einspeist, bevor der Akku leer ist → `params["um"] == "13:00"` (nicht das Horizontende). Neuer Test `test_no_rule_r_reason_when_battery_empties_before_pv`: wie oben, aber der Akku ist vor 13:00 leer → kein `REGEL_R_BLOCKIERT_NETZLADEN`.
- `test_docs.py`: Beispiel `{"um": "17:00", "kwh": 8.4}` erwartet den neuen Satz in der Tabelle von `docs/betrieb.md`.
- `test_runner.py`: neuer Test `test_charge_before_export_ignores_charge_used_up_before_pv`: ein Lauf, in dem der Akku nach dem Netzladen nachts leer wird und am Morgen PV einspeist → `charge_before_export_kwh == 0` und `r1_violations == 0`. Der Test mit `40 < r1_violations < 96` bleibt (der Akku lädt dort jeden Slot und wird nie leer); verschieben sich die Grenzen, die neue Zahl mit Begründung im Kommentar festhalten.

- [ ] **Step 2: Tests laufen lassen, sie scheitern**

Run: `uv run pytest tests/unit/app/planning/test_explain.py tests/unit/tools/scenario/test_runner.py tests/unit/test_docs.py -q`
Expected: FAIL.

- [ ] **Step 3: Implementieren**

- `explain.py`: `exported = export_while_charged_kwh(steps, index, lo)`. Über `R1_TOL_KWH` ist `um` der Beginn des ersten Slots in `range(index, charged_window_end(...))` mit `export_w > 0`; Satz nach Entscheidung 4.
- `runner.py`: `broken` mit `export_while_charged_kwh(current.steps, position, params.lo_kwh)`. `_charge_before_export_kwh(steps, lo_kwh)` prüft über alle Schritte des Laufs statt je Tag; `_day_groups` bleibt für die Tageswerte.
- `docs/betrieb.md`: Tabellenzeile mit neuem Satz und der Bedingung „… aber die PV speist mehr als 0,1 kWh ein, bevor der Akku leer ist“.

- [ ] **Step 4: Tests laufen lassen, sie bestehen**

Run: `uv run pytest tests/unit -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git commit -m "feat(app): Erklärung und Szenarien rechnen Regel R bis der Akku leer ist"
```

### Task 4: Spec, Betriebshandbuch, Übergabe und Gesamtlauf

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absatz „Stand Plan 3d (20.10.2026)“ hinter Regel R; Verweise in Ersatzplan, Plausibilität und Präzisierung 19)
- Modify: `docs/betrieb.md` (Absatz „Regel R“ in „Planung“, Prüfpunkt „Regel R“ in „An der Anlage prüfen“)
- Modify: `docs/uebergabe.md` (Stand 3d, Rulings, aufgeschobene Kleinigkeiten, Ausrollen nach Entscheidung 6)

- [ ] **Step 1: Spec-Absatz schreiben**

„Stand Plan 3d (20.10.2026): Regel R1 sperrt Netzladen in Slot t nur, wenn laut P50 PV eingespeist wird, bevor der Akku wieder leer ist (SOC am Slotende ≤ Reserve + 0,05 kWh im Optimierer, + 0,1 kWh an der Nachrechnung). Formuliert mit `e_t` (Akku leer) und `c_t` (Netzenergie im Akku): `c_t ≥ z_t`, `c_t ≥ c_{t−1} − e_{t−1}`, `pv_x_t ≤ PV_t · (1 − c_t)`.“ Dazu die drei Verweise (Ersatzplan, Plausibilität, Präzisierung 19) auf „bis der Akku leer ist“.

- [ ] **Step 2: Betriebshandbuch**

Absatz „Regel R“ neu: Lädt der Optimierer aus dem Netz, speist die PV laut Prognose nicht ein, bevor der Akku wieder leer ist; heute trüb und morgen sonnig darf er also heute laden, was bis morgen verbraucht ist. Prüfpunkt „Regel R“: Kein Block „Netzladen“ darf bis in einen Zeitraum mit Einspeisung reichen, ohne dass der Akku dazwischen auf der Reserve ankommt.

- [ ] **Step 3: Gesamtlauf**

Run: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS; uv run ruff format --check . && uv run ruff check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports && uv run pytest -q --cov --cov-report=term --cov-fail-under=75 && uv run coverage report --include="src/bindaems/core/safety/*,src/bindaems/core/control/*,src/bindaems/core/actuators/*" --fail-under=90`, danach in `ui/`: `pnpm check && pnpm lint && pnpm test`.
Expected: alles grün, Abdeckung ≥ 75 % gesamt und ≥ 90 % safety/control/actuators.

- [ ] **Step 4: Übergabe nachführen und committen**

```bash
git commit -m "docs: Regel R1 bis der Akku leer ist (Plan 3d)"
```

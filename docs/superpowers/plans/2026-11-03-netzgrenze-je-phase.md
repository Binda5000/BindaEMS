# Netzgrenze je Phase im Planer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Optimierer, Ersatzplan und Plausibilitätsprüfung planen den Netzbezug je Phase höchstens bis zum Importziel am Cerbo (20 A − 1 A = 19 A je Phase), mit den echten Phasen jedes Autos. So plant der Planer Tesla und e-Golf nicht mehr gleichzeitig mit voller Leistung und nicht zusätzlich Netzladen, wenn das an der Anlage nicht geht.

**Architecture:** app (Planung), Szenario-Werkzeuge und in Task 6 die Planprüfung im core. Ein neues reines Modul `app/planning/grid_phases.py` rechnet die Grenzen aus der Konfiguration und die Anteile je Phase. `BatteryState` bekommt die Grenze je Phase, `PlanVehicleInput` die Netzphasen des Autos; beide Felder sind optional, damit gespeicherte Eingangsdaten und alte Szenarien unverändert laden und rechnen. Die Regelung im core bleibt unverändert: Sie regelt wie bisher je Phase (Phasenwächter, Importziel unter der Reserve-Absicherung); nur seine Planprüfung lässt in Task 6 das Nachladen auf die Reserve ohne Schalter zu.

**Tech Stack:** Python 3.12, pydantic, PuLP/HiGHS, pytest, hypothesis.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, 9.3 „Optimierungsmodell“ (Netzbezug, Stand Plan 4b „Variablen“), 9.4 „Regelbasierter Ersatzplan“ (Stand Plan 4b), 3.3 „Victron Peak Shaving“, 7.x „Phasenwächter“ (`phase_target`).

## Anlass

Screenshot vom 3.11.2026 21:15 (Zeitplan für den 4.11.): Tesla (bis 11 kW, 3-phasig) und e-Golf (7,4 kW, 2-phasig) laden im Plan gleichzeitig etwa 04:00–05:00, dazu Netzladen des Akkus. Der Optimierer begrenzt nur die Summe `imports_t ≤ grid_max_import_w` = (35 − 2) A · 230 V · 3 ≈ 22,8 kW (`optimizer.py`, Bedingung `bezug_t`; `service.py`, `_battery`). Er kennt weder das Importlimit am Cerbo (20 A je Phase, Peak Shaving „immer“, darüber entlädt der Multi den Akku) noch die Phasen der Autos. Der core lässt das nicht zu: Unter SOC Reserve + `reserve_guard_pct` (25 %) hält er die Autos am Importziel von 19 A je Phase. Folge an der Anlage: Der Plan ist nicht ausführbar, das zweite Auto lädt später als geplant, Netzladen fällt aus.

## Entscheidungen

1. **Grenze je Phase** (Entscheidung des Betreibers vom 3.11.2026, Karte „Importlimit 20 A je Phase“): `grid_phase_import_w = (grid.import_limit_a − control.import_margin_a) · grid.voltage_nominal_v`, an der Anlage (20 − 1) · 230 = **4370 W** je Phase. Nicht die Sicherung, kein Akku über 20 A im Modell.
2. **Summengrenze bleibt** wie bisher (`grid_max_import_w`, `bezug_t`); die Phasengrenze kommt dazu.
3. **Phasen eines Autos:** `wallbox.phase_map[: vehicle.phases]` seiner Wallbox, wie `core.control.charge_points._point` (für ein erwartetes Auto die Wallbox, an der es erwartet wird). Ein Auto lädt auf jeder seiner Phasen `Leistung / Anzahl Phasen`. Ohne Angabe (`None`, alte Eingangsdaten) gilt es als 3-phasig symmetrisch.
4. **Symmetrisch** verteilt sind Haus, Netzladen des Akkus und PV (je ein Drittel); die Phasengrenze zählt nur den Netzanteil: je Slot `t` und Phase `p`:
   `(gr_h_t + gr_b_t) / 3 + Σ_{c aktiv, p ∈ Phasen_c} gr_ev_ct / n_c ≤ R_t` mit
   `R_t = max(grid_phase_import_w, max(0, load_t − pv_t) / 3)`.
   Das `max` hält das Modell lösbar, wenn das Haus allein schon über der Grenze liegt (dann bleiben für Autos und Netzladen nur, was der Akku dem Haus abnimmt). PV ins Auto (`pv_ev`) und Akku ins Auto (`bt_ev`) zählen nicht.
5. **Ohne Grenze** (`grid_phase_import_w is None`): keine Phasenbedingung, alles wie bisher. Gesetzt wird sie im Planungsdienst und in `export_days`; die synthetischen Szenarien und `tests/plan_helpers.state()` bleiben ohne, damit bestehende Erwartungen gelten.
6. **Ersatzplan:** Im Netzschritt von `fallback_vehicles` hat jede Phase den Platz `max(0, grid_phase_import_w − max(0, load − pv) / 3)` abzüglich der Netzanteile der Autos davor; ein Auto bekommt höchstens `n_c · min_{p ∈ Phasen_c} Platz_p` zusätzlich, sonst (unter `min_w`) nicht diesen Slot. Der PV-Schritt bleibt unverändert und belegt keinen Platz. Beim Netzladen in `fallback_intents` gilt zusätzlich `headroom ≤ 3 · min_p (grid_phase_import_w − max(0, load − pv) / 3 − Σ_c ev_w_c · Anteil_c(p))`; hier zählt die ganze Ladeleistung der Autos (konservativ, nachts ohnehin gleich).
7. **Plausibilität:** Neuer Befund (Art `phase`), wenn die Flüsse des Optimierers auf einer Phase mehr als `R_t + BALANCE_TOL_W` aus dem Netz beziehen: „Netzbezug über dem Importlimit auf L1 um 04:00“. Dann gilt wie bei jedem Befund der Ersatzplan.
8. **Monatsspitze ohne Leistungspreis** (Wunsch des Betreibers vom 3.11.2026 21:58, Karte „Wie soll der Planer die Monatsspitze vor 2027 behandeln?“; Entscheidung des Betreibers vom 3.11.2026 22:02: „Gedachter Satz, auch Wächter“): Für einen Monat ohne Leistungspreis mit Satz rechnet der Optimierer mit einem gedachten Satz `planning.peak_soft_ct_per_kw` (ct je kW und Monat, Standard **100**, 0 = aus). Er entsteht als `PeakMonth(virtual=True, tier_kw=0, below_ct_per_kw=0, above_ct_per_kw=Satz, peak0_kw=bisherige Monatsspitze)` (für spätere Monate im Horizont 0): Bezug bis zur schon erreichten Spitze kostet nichts, jedes kW darüber den Satz. Ein gedachter Monat gibt sein **Budget** wie ein echter an den core (`builder._budgets`, gleiche Formel); der 15-min-Wächter hält es schon heute, wenn der Plan-Slot eins nennt (`core.control.peak_guard.guard_target_kw`), es braucht kein core-Update. Er setzt `Plan.power_price` nicht und zählt in keiner Kostenanzeige oder Statistik. Der Optimierer behandelt ihn wie einen echten Monat; `grid_charge_pays` zählt nur echte Monate (Ruling des Controllers zu Task 5, so in Spec 9.3: der gedachte Satz ist ein Steuersignal, kein Geld). Gibt es einen echten Satz, gilt nur dieser.
9. **Nachladen auf die Reserve** (Beobachtung des Betreibers vom 3.11.2026: ohne PV sinkt der SOC am Min-SOC um etwa 1 % in 9 h; unter Min-SOC − 5 % lädt Victron ESS „Optimiert ohne BatteryLife“ selbst mit voller Leistung nach, am 3.11. 22:35–23:05 mit 9 kW bei 11,5 kW Netz. Entscheidung des Betreibers 22:18: „Ja, immer“): Liegt der SOC zu Beginn unter der Reserve, plant der Planer ein Nachladen zurück auf die Reserve, **auch bei ausgeschaltetem „Netzladen erlaubt“**:
   - Optimierer: neue Variable `gr_r_t` (Netz → Akku, Nachladen) mit `0 ≤ gr_r_t ≤ recharge_cap_w(t) = min(RESERVE_RECHARGE_MAX_W, max_charge_w_t, max(0, max_grid_charge_setpoint_w − load_t))`, `RESERVE_RECHARGE_MAX_W = 3000` W; nur wenn `SOC₀ < reserve`. Sie zählt wie `gr_b` zur Ladung (`laden_t`), zum Bezug (`bezug_t`, Spitze, Phasengrenze symmetrisch) und kostet `import_ct` (ohne `margin_ct`). `Σ η_ch · gr_r_t · Δ ≤ reserve_kwh − SOC₀`. Ziel: `s_T + short_r ≥ reserve_kwh` am Horizontende, `short_r` kostet 2000 ct/kWh (wie die Fehlmenge eines Autos). PV in den Akku bleibt vorrangig, weil sie nichts kostet.
   - Regel R1 zählt den Akku als leer, sobald er höchstens an der Reserve steht (`reserve_kwh` statt `lo_kwh` als Leer-Grenze in `optimizer.py`, `intents._keep_rule_r1`, `plausibility`): Energie unter der Reserve verlässt den Akku nie, kann also nicht eingespeist werden.
   - Absicht: `BatteryIntent` bekommt `reserve_recharge: bool = False` (nur mit `NETZLADEN`). `derive_intents`: `NETZLADEN(P)` mit `P = round(gr_b + gr_r)`, gekappt auf `max(GC_t, recharge_cap_w(t))`; `reserve_recharge = gr_b < GRID_CHARGE_MIN_W`. `_drop_unprofitable` und der Befund „rechnet sich nicht“ lassen `reserve_recharge` stehen.
   - Ersatzplan: unter der Reserve `NETZLADEN(min(3000, recharge_cap_w, Phasenplatz), reserve_recharge=True)` in den billigsten Slots der ersten 24 h, bis die Nachrechnung die Reserve erreicht.
   - core (`core/control/intent.py`): Ein Plan mit `NETZLADEN` bei ausgeschaltetem Schalter ist gültig, wenn jeder solche Slot `reserve_recharge` hat und `grid_charge_w ≤ RESERVE_RECHARGE_MAX_W`; sonst wie heute „Netzladen, obwohl ausgeschaltet“. Die Konstante steht in `shared/plan.py`.
   - Erklärung: neuer Grund `NACHLADEN_RESERVE`, Text „Nachladen auf die Reserve, bevor der Victron selbst mit voller Leistung nachlädt“; Zeitplan-Beschriftung „Nachladen“.
10. **Ausrollen:** ems-app **und** ems-core zusammen (Task 6 ändert `shared/plan.py` und `core/control/intent.py`), ohne Migration, ohne ACL; mit dem nächsten Sammel-Pull (Stufe 2, ab 23.11.), nie allein.

## Global Constraints

- Keine Änderung an der Safety-Schicht, an der Datenbank oder an der API; im core nur die Planprüfung in `core/control/intent.py` (Task 6).
- Neue Felder sind optional mit Standard `None`; `PlanInputs` aus der Datenbank (gespeicherte Pläne) und Szenariodateien Version 1/2 laden unverändert.
- Lösezeit: `test_two_cars_over_36_hours_solve_in_time` muss weiter bestehen (keine neue Binärvariable).
- Texte deutsch, Commits deutsch im Stil `fix(app): …`, keine Modellbezeichnungen im Repo.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy` strict (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt). In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.

## Review Focus

- Haus allein über der Grenze (z. B. Last 15 kW ohne PV, Akku an der Reserve): Modell bleibt lösbar, Autos und Netzladen 0, keine `infeasible` (Task 2, `test_house_above_phase_limit_stays_feasible`).
- Gespeicherte Eingangsdaten ohne die neuen Felder: laden, und der Optimierer rechnet wie vorher (Task 1 und 2, `..._without_phase_limit_...`).
- Zwei Autos auf **verschiedenen** Phasen (z. B. 1-phasiger Gast auf L3 und 2-phasiger e-Golf auf L1/L2) dürfen gleichzeitig laden; die Grenze darf nicht wie eine Summengrenze wirken (Task 2, `test_cars_on_different_phases_share_no_room`).
- PV-Überschuss am Tag: Zwei Autos aus PV werden nicht durch die Phasengrenze gebremst (Task 2, `test_pv_into_two_cars_is_not_limited_by_phases`).
- Ersatzplan mit zwei Autos in denselben billigsten Slots: das zweite weicht in den nächsten Slot aus statt die Phase zu überziehen (Task 3).

---

### Task 1: Grenzen und Phasen in den Eingangsdaten

**Files:**
- Create: `src/bindaems/app/planning/grid_phases.py`
- Modify: `src/bindaems/app/planning/inputs.py` (`BatteryState`), `src/bindaems/app/planning/vehicles.py` (`PlanVehicleInput`, `_car_input`), `src/bindaems/app/planning/service.py` (`_battery`), `src/bindaems/tools/export_days.py` (`_battery`), `src/bindaems/tools/scenario/data.py` (`ScenarioBattery`, `ScenarioCar`), `src/bindaems/tools/scenario/runner.py` (`BatteryState(...)`, `_vehicle_inputs`)
- Test: `tests/unit/app/planning/test_grid_phases.py` (neu), `tests/unit/app/planning/test_service.py`, `tests/unit/app/planning/test_vehicles.py`, `tests/unit/tools/test_export_days.py`, `tests/unit/tools/scenario/test_runner.py`

**Interfaces:**
- Produces:
  - `PHASES: Final[tuple[Phase, Phase, Phase]] = ("L1", "L2", "L3")`
  - `grid_import_limits_w(grid: GridConfig, control: ControlConfig) -> tuple[float, float]` → `(Summe, je Phase)`: Summe wie bisher (`max_import_kw · 1000`, sonst `(fuse_a − fuse_margin_a) · U · 3`), je Phase nach Entscheidung 1. `service._battery` und `export_days._battery` nutzen sie statt ihrer eigenen Rechnung.
  - `car_grid_phases(cfg: Config, vehicle_key: str, wallbox_key: str) -> list[Phase]` → `list(cfg.wallboxes[wallbox_key].phase_map[: cfg.vehicles[vehicle_key].phases])`
  - `phase_shares(phases: Sequence[Phase] | None) -> dict[Phase, float]` → `{p: 1/len(phases)}`; `None` → `{L1: 1/3, L2: 1/3, L3: 1/3}`
  - `phase_room_w(limit_w: float, load_w: float, pv_w: float) -> float` → `max(limit_w, max(0, load_w − pv_w) / 3)` (`R_t`, Entscheidung 4)
  - `BatteryState.grid_phase_import_w: NonNegativeFinite | None = None`
  - `PlanVehicleInput.grid_phases: list[Phase] | None = None` (Validator: 1 bis 3 verschiedene Phasen)
  - `ScenarioBattery.grid_phase_import_w: NonNegativeFinite | None = None`, `ScenarioCar.grid_phases: list[Phase] | None = None`; der Runner reicht beide durch.

- [ ] **Step 1: Failing tests**
  - `test_grid_phases.py`: `grid_import_limits_w` mit `fuse_a=35, fuse_margin_a=2, import_limit_a=20, import_margin_a=1, U=230` → `(22770.0, 4370.0)`; mit `max_import_kw=11` → Summe `11000.0`, je Phase `4370.0`. `car_grid_phases` mit e-Golf (`phases: 2`) an EVCS mit `phase_map [L2, L3, L1]` → `["L2", "L3"]`. `phase_shares(["L1","L2"]) == {"L1": 0.5, "L2": 0.5}`, `phase_shares(None)` drei Drittel. `phase_room_w(4370, 15000, 0) == 5000.0`, `phase_room_w(4370, 600, 0) == 4370.0`, `phase_room_w(4370, 600, 5000) == 4370.0`.
  - `test_service.py`: `inputs.battery.grid_phase_import_w == (20 - 1) * 230.0` mit der Beispielkonfiguration.
  - `test_vehicles.py`: ein geplantes e-Golf aus Health hat `grid_phases == ["L1", "L2"]`, der Tesla am TWC `["L1", "L2", "L3"]`; ein erwartetes Auto die Phasen seiner erwarteten Wallbox.
  - `test_vehicles.py`: `PlanVehicleInput.model_validate(<dict ohne grid_phases>)` → `grid_phases is None`; `grid_phases=["L1", "L1"]` → `ValidationError`.
  - `test_export_days.py`: `battery.grid_phase_import_w == 4370.0`; `test_runner.py`: eine Szenariodatei Version 2 ohne die Felder lädt, der Runner übergibt `grid_phase_import_w=None`.
- [ ] **Step 2:** `pytest tests/unit/app/planning/test_grid_phases.py tests/unit/app/planning/test_service.py tests/unit/app/planning/test_vehicles.py tests/unit/tools -q` → neue Tests scheitern.
- [ ] **Step 3:** Modul und Felder umsetzen, `service._battery` und `export_days._battery` auf `grid_import_limits_w` umstellen, `_car_input` setzt `grid_phases=car_grid_phases(cfg, key, wallbox_key)`.
- [ ] **Step 4:** Tests grün, ganze Prüfkette.
- [ ] **Step 5:** Commit `feat(app): Netzgrenze je Phase und Phasen der Autos in den Eingangsdaten des Planers`.

### Task 2: Phasengrenze im Optimierer

**Files:**
- Modify: `src/bindaems/app/planning/optimizer.py` (`_solve`, nach `bezug_t`)
- Test: `tests/unit/app/planning/test_optimizer.py`, `tests/property/test_optimizer.py`

**Interfaces:**
- Consumes: `PHASES`, `phase_shares`, `phase_room_w` (Task 1), `BatteryState.grid_phase_import_w`, `PlanVehicleInput.grid_phases`
- Produces: Bedingung `phase_{t}_{p}` je Slot und Phase (Name z. B. `phase_12_L1`), nur wenn `grid_phase_import_w is not None`.

- [ ] **Step 1: Failing tests** in `test_optimizer.py`, alle mit `state(grid_phase_import_w=4370.0)`, Nacht ohne PV, Haus 600 W, Tesla = `planned_car()` (`grid_phases` L1–L3), e-Golf = `planned_car(key="egolf", name="e-Golf", wallbox="evcs", usable_kwh=38.6, min_w=6*230*2, max_w=16*230*2, grid_phases=["L1","L2"])`, beide mit Bedarf; Hilfsfunktion `phase_grid_w(inputs, flow, phase)` im Test nach Entscheidung 4:
  - `test_two_cars_stay_under_the_phase_limit`: in jedem Slot und jeder Phase `phase_grid_w ≤ 4370 + 1`; es gibt keinen Slot, in dem beide mit `max_w` laden.
  - `test_phase_limit_leaves_little_room_for_grid_charging`: `grid_charge_allowed=True`, nur der Tesla, Abfahrt nach einem Slot, Bedarf genau `max_w · 0,25 h` (lädt also in Slot 0 mit voller Leistung), Slot 0 billig und Slot 1 teuer → `gr_b` in Slot 0 ≤ `3 · (4370 − 200 − 3680) + 1` = 1471 W.
  - `test_house_above_phase_limit_stays_feasible`: Last 15 000 W, SOC an der Reserve → Status `optimal`, `gr_b == 0`, Autos 0 W, Fehlmenge > 0.
  - `test_cars_on_different_phases_share_no_room`: Gast-Auto `grid_phases=["L3"]`, `min_w=6*230, max_w=16*230`, und e-Golf auf L1/L2 → im billigsten Slot laden beide mit `max_w`.
  - `test_pv_into_two_cars_is_not_limited_by_phases`: PV 12 000 W, Haus 600 W, beide Autos mit Bedarf und R2 aus PV → Summe `pv_ev` im Slot > 4370 · 2.
  - `test_without_phase_limit_two_cars_charge_together`: `state()` ohne Grenze → derselbe Fall wie im ersten Test erlaubt beide gleichzeitig mit `max_w` (heutiges Verhalten).
  - Property-Test `tests/property/test_optimizer.py`: Strategie zieht `grid_phase_import_w` aus `None | 3000..8000` und `grid_phases` je Auto aus den Teilmengen der Größe 1–3; neue Zusicherung je Slot und Phase `≤ phase_room_w(...) + 1`.
- [ ] **Step 2:** `pytest tests/unit/app/planning/test_optimizer.py -q -k "phase or pv_into_two"` → scheitern (ohne `without_phase_limit`).
- [ ] **Step 3:** In `_solve` je Slot `t` und Phase `p` die Bedingung aus Entscheidung 4 mit `active[t]` und `c.grid[t]` hinzufügen.
- [ ] **Step 4:** Unit- und Property-Tests grün; `test_two_cars_over_36_hours_solve_in_time` grün; ganze Prüfkette.
- [ ] **Step 5:** Commit `fix(app): Optimierer plant den Netzbezug je Phase höchstens bis zum Importziel`.

### Task 3: Phasengrenze im Ersatzplan

**Files:**
- Modify: `src/bindaems/app/planning/fallback.py` (`fallback_vehicles`, `fallback_intents`)
- Test: `tests/unit/app/planning/test_fallback.py`

**Interfaces:**
- Consumes: `PHASES`, `phase_shares` (Task 1)
- Produces: keine neuen Namen; Docstrings beider Funktionen nennen die Grenze je Phase (Entscheidung 6).

- [ ] **Step 1: Failing tests** (Grenze 4370 W, Haus 600 W, keine PV, Autos wie in Task 2, Tesla zuerst):
  - `test_fallback_second_car_moves_to_the_next_slot`: gleiche Preise bis auf zwei billigste Slots → in keinem Slot laden beide; je Slot und Phase Netzanteil ≤ 4370 + 1; der e-Golf lädt in einem anderen Slot als der Tesla.
  - `test_fallback_grid_charge_respects_phase_headroom`: Tesla mit voller Leistung im billigsten Slot, `grid_charge_allowed=True`, teurer Slot danach knapp → `NETZLADEN` dort höchstens 1400 W (`ROUND_W`-Schritt unter 1470 W), oder kein `NETZLADEN` in diesem Slot.
  - `test_fallback_without_phase_limit_unchanged`: `state()` ohne Grenze → Ergebnis gleich wie heute (beide im billigsten Slot).
- [ ] **Step 2:** `pytest tests/unit/app/planning/test_fallback.py -q` → neue Tests scheitern.
- [ ] **Step 3:** Platz je Phase in `fallback_vehicles` führen (nur Netzschritt), `headroom_w` in `fallback_intents` zusätzlich nach Entscheidung 6 kappen; ohne Grenze nichts ändern.
- [ ] **Step 4:** Tests grün, ganze Prüfkette.
- [ ] **Step 5:** Commit `fix(app): Ersatzplan hält die Netzgrenze je Phase`.

### Task 4: Befund in der Plausibilitätsprüfung, Doku

**Files:**
- Modify: `src/bindaems/app/planning/plausibility.py` (`check_plan`, Docstring des Moduls)
- Test: `tests/unit/app/planning/test_plausibility.py`
- Docs: Spec 9.3 und 9.4 (neuer Absatz „**Stand Plan Netzgrenze je Phase** (3.11.2026)“ mit Entscheidungen 1–7), `docs/betrieb.md` „Planung“ (ein Absatz: Grenze je Phase, was sie an der Anlage heißt), `docs/uebergabe.md` (Stand; „Offen an der Anlage“: kommt mit Stufe 2, nur ems-app, keine Migration)

**Interfaces:**
- Consumes: `PHASES`, `phase_shares`, `phase_room_w` (Task 1)
- Produces: Befund-Art `phase`, Text `f"Netzbezug über dem Importlimit auf {p} um {at}"` (erste Phase in der Reihenfolge `PHASES`, erster Slot).

- [ ] **Step 1: Failing tests** mit `flows_result([flow(gr_h=600, ev={"tesla": (0, 11040), "egolf": (0, 7360)})])` und Grenze 4370:
  - `test_phase_over_import_limit_is_a_finding`: `check_plan(...)` enthält `"Netzbezug über dem Importlimit auf L1 um 04:00"` (Slotbeginn 04:00 Ortszeit).
  - `test_phase_check_without_limit_finds_nothing`: ohne Grenze kein Befund dieser Art.
  - `test_phase_check_counts_only_grid_share`: dieselben Autos mit `ev={"tesla": (11040, 0), "egolf": (7360, 0)}` und passender PV → kein Phasenbefund.
- [ ] **Step 2:** `pytest tests/unit/app/planning/test_plausibility.py -q` → scheitern.
- [ ] **Step 3:** Befund in der ersten Schleife über `result.flows` ergänzen.
- [ ] **Step 4:** Tests grün; ganze Prüfkette einschließlich UI (`pnpm check`, `lint`, `test`; unverändert) und Simulator-Szenarien.
- [ ] **Step 5:** Doku nachführen; Commit `fix(app): Plausibilität meldet Netzbezug über dem Importlimit je Phase` und `docs: Netzgrenze je Phase in Spec, Betrieb und Übergabe`.

### Task 5: Monatsspitze ohne Leistungspreis

**Files:**
- Modify: `src/bindaems/shared/settings.py` (`PlanningSettings`), `src/bindaems/app/planning/inputs.py` (`PeakMonth`, `peak_months`, Aufruf in `assemble_inputs`), `src/bindaems/app/planning/builder.py` (`_budgets`, `power_price`), `src/bindaems/app/planning/plausibility.py` (`grid_charge_pays`, Docstring), `ui/src/lib/settings/PlanningEditor.svelte`, `ui/src/lib/settings/PlanningView.svelte` (Feld „Spitze vermeiden ohne Leistungspreis“, ct je kW und Monat)
- Test: `tests/unit/app/planning/test_inputs.py`, `tests/unit/app/planning/test_builder.py`, `tests/unit/app/planning/test_optimizer.py`, `ui/src/lib/settings/PlanningEditor.test.ts`, `PlanningView.test.ts`
- Docs: Spec 9.3 (im Absatz von Task 4 ergänzen), `docs/betrieb.md` „Planung“ (Einstellung erklären) und „15-min-Wächter“ (ohne Leistungspreis bringt der Plan mit gedachtem Satz ein Budget; ausschalten mit Satz 0)

**Interfaces:**
- Produces: `PlanningSettings.peak_soft_ct_per_kw: NonNegative = 100.0`; `PeakMonth.virtual: bool = False`; `peak_months(starts, tariff, month_peak_kw, soft_ct_per_kw: float = 0.0) -> list[PeakMonth]`

- [ ] **Step 1: Failing tests**
  - `test_inputs.py`: November 2026 ohne Satz, `soft_ct_per_kw=100`, Monatsspitze 9,2 → ein `PeakMonth(month="2026-11", virtual=True, peak0_kw=9.2, tier_kw=0, below_ct_per_kw=0, above_ct_per_kw=100)`; mit `soft_ct_per_kw=0` → `[]`; Januar 2027 mit echtem Satz → nur der echte Monat, `virtual=False`. Gespeicherte Eingangsdaten ohne `virtual` laden mit `False`.
  - `test_optimizer.py`, `test_soft_peak_spreads_two_cars`: zwei Autos mit Bedarf je ~10 kWh über 8 Nachtslots mit gleichem Preis, Monatsspitze 3 kW, Satz 100 → höchster Bezug im Plan ≤ max(3, nötiger Mittelwert) + 0,1 kW statt beide gleichzeitig; ohne Satz (0) laden sie wie bisher.
  - `test_builder.py`, `test_virtual_month_gives_a_budget`: `budget_kw` je Slot = auf 0,1 kW aufgerundet `max(peak0, höchster Bezug der Nachrechnung)`, `plan.power_price is False`; mit `peak_soft_ct_per_kw=0` alle `budget_kw` `None`.
  - UI: Editor zeigt das Feld mit Standard 100, speichert `planning.peak_soft_ct_per_kw`; Ansicht zeigt „100 ct je kW und Monat“, bei 0 „aus“.
- [ ] **Step 2:** `pytest tests/unit/app/planning -q -k "soft or virtual"` und `cd ui && pnpm vitest run src/lib/settings` → scheitern.
- [ ] **Step 3:** Umsetzen nach Entscheidung 8.
- [ ] **Step 4:** Tests grün; Lösezeit-Test grün; ganze Prüfkette mit UI und E2E.
- [ ] **Step 5:** Commit `feat(app): Planer meidet neue Monatsspitzen auch ohne Leistungspreis`, Doku nachführen.

### Task 6: Nachladen auf die Reserve

**Files:**
- Modify: `src/bindaems/shared/plan.py` (`BatteryIntent.reserve_recharge`, Validator: nur mit `NETZLADEN`; `RESERVE_RECHARGE_MAX_W`; `ReasonCode` `NACHLADEN_RESERVE`), `src/bindaems/app/planning/optimizer.py` (`gr_r`, R1-Leer-Grenze, `recharge_cap_w`), `src/bindaems/app/planning/intents.py`, `src/bindaems/app/planning/fallback.py`, `src/bindaems/app/planning/plausibility.py`, Erklärungen (`NACHLADEN_RESERVE`), `src/bindaems/core/control/intent.py`, `ui/src/lib/plan/model.ts` (Beschriftung und Grund)
- Test: `tests/unit/app/planning/test_optimizer.py`, `test_intents.py`, `test_fallback.py`, `test_plausibility.py`, `tests/unit/core/control/test_intent.py` (oder die vorhandene Datei der Planprüfung), `tests/unit/shared/test_plan.py`, `ui/src/lib/plan/model.test.ts`
- Docs: Spec 9.3/9.4 (Absatz von Task 4 ergänzen), `docs/betrieb.md` „Planung“ (Nachladen, auch ohne Schalter) und „Netzladen erlaubt“, `docs/uebergabe.md` (Ausrollen core+app)

**Interfaces:**
- Produces: `RESERVE_RECHARGE_MAX_W: Final = 3000.0` (`shared/plan.py`); `BatteryIntent.reserve_recharge: bool = False`; `recharge_cap_w(inputs: PlanInputs, index: int) -> float` in `optimizer.py` (0, wenn `SOC₀ ≥ reserve`)

- [ ] **Step 1: Failing tests** (Akku 96 kWh, Reserve 20 %, SOC₀ 16 %, Haus 600 W, keine PV, Preise mit zwei billigen Slots):
  - `test_recharge_to_reserve_with_switch_off`: `grid_charge_allowed=False` → Absichten `NETZLADEN` mit `reserve_recharge=True`, je Slot `grid_charge_w ≤ 3000`, Nachrechnung erreicht am Horizontende ≥ 20 % − 0,1 %, nicht mehr als 20 % + eine Slotladung; die billigen Slots zuerst.
  - `test_no_recharge_at_or_above_reserve`: SOC₀ 20 % → kein `gr_r`, Ergebnis wie heute.
  - `test_pv_refills_before_grid_recharge`: genug PV am nächsten Tag im Horizont → kein `NETZLADEN`.
  - `test_recharge_does_not_block_export_by_r1`: nach dem Nachladen auf 20 % und PV-Überschuss danach darf eingespeist werden (R1 sieht den Akku an der Reserve als leer).
  - `test_recharge_respects_phase_limit`: mit Grenze 4370 W und Tesla voll im billigen Slot → Nachladen dort ≤ 3 · 490 W, Rest in anderen Slots.
  - Plausibilität: `NETZLADEN(2500, reserve_recharge=True)` bei Schalter aus → kein Befund „obwohl ausgeschaltet“; `NETZLADEN(3500, reserve_recharge=True)` → Befund; ohne Flag → Befund wie heute.
  - Ersatzplan: `test_fallback_recharges_to_reserve`: Schalter aus, SOC₀ 16 % → `NETZLADEN` mit Flag in den billigsten Slots, Nachrechnung ≥ Reserve.
  - core: Plan mit `NETZLADEN(3000, reserve_recharge=True)` und `grid_charge_allowed=False` gültig; mit 3100 W oder ohne Flag ungültig („Netzladen, obwohl ausgeschaltet.“).
  - shared: `BatteryIntent(kind=EIGENVERBRAUCH, reserve_recharge=True)` → `ValidationError`; altes JSON ohne Feld lädt mit `False`.
  - UI: Slot mit `reserve_recharge` heißt „Nachladen“, Grund-Text wie Entscheidung 9.
- [ ] **Step 2:** neue Tests laufen lassen → scheitern.
- [ ] **Step 3:** Umsetzen nach Entscheidung 9.
- [ ] **Step 4:** ganze Prüfkette (Abdeckung core/control ≥ 90 %), UI, E2E, Simulator-Szenarien; Lösezeit-Test grün.
- [ ] **Step 5:** Commits `feat(app): Planer lädt den Akku unter der Reserve sanft selbst nach`, `feat(core): Nachladen auf die Reserve auch ohne Netzladen-Schalter`, Doku.

## Nicht in diesem Plan

- Die überlappenden „Netzladen“-Beschriftungen im Akku-Diagramm: auf main schon behoben (e5a3aa4), kommen mit Stufe 2.
- Der core: Er regelt schon je Phase. Starten zwei Autos in „Geplant“ wegen `latest_start` gleichzeitig, teilt der Phasenwächter wie bisher; der Plan macht das seltener, weil er sie nacheinander legt.
- Peak Shaving über 20 A mit dem Akku im Modell (Option „Sicherung 33 A, Rest aus dem Akku“, vom Betreiber nicht gewählt).

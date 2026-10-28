# Akku ins Auto, wenn er übrig ist – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Autos in „Geplant“ und „Sofort“ dürfen aus dem Hausakku laden, wenn das Haus diese Energie bis zur nächsten Füllung nicht braucht und Akkustrom im Slot billiger ist als Netzstrom; sonst bleibt es bei „kein Akku ins Auto“.

**Architecture:** Für „Geplant“ bekommt der Optimierer einen Fluss Akku → Auto (`bt_ev`) mit Verschleiß und Marge als Kosten, gesperrt, solange Netzenergie im Akku sein kann (Regel R1). Der Plan nennt je Slot und Auto die Leistung aus dem Akku (`ev_battery_w`), der core gibt einem geplanten Auto diesen Anteil, solange der SOC über dem geplanten SOC am Slotende liegt. Für „Sofort“ rechnet die app je Slot, ob übriger Akkustrom sich lohnt (`now_battery`); der core gibt einem Auto in Sofort dann seinen Fehlbetrag aus dem Akku, solange der SOC über der Plan-Schwelle liegt (dem SOC, den der Plan noch fürs Haus braucht). Ein Schalter „Akku ins Auto, wenn übrig“ (ab Werk aus) schaltet beides.

**Tech Stack:** Python 3.12, pydantic, PuLP/HiGHS, pytest, Hypothesis; Svelte 5, valibot, Vitest. Keine neuen Abhängigkeiten, keine Migration.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Abschnitte 7.6 (Akku-Unterstützung, „Stand Plan 3c“, „Stand Plan 4b“, „Stand Plan 5b“), 7.7 (Akku-Steuerung), 9.3 (Optimierungsmodell, „Stand Plan 3d“, „Stand Plan 4b“), 9.5 (Plausibilität). Betrieb: `docs/betrieb.md`, „Akku und Spitzen“, „Akku-Sollwert“, „Planung“.

## Einordnung

- **Anlass (Betreiber, 28.10.2026):** „Würde es Sinn machen, bei Sofort/Geplant Strom aus dem Akku zu nehmen, wenn das billiger wäre? Zumindest solange genug im Akku ist.“ Antwort im Thread: ja, aber nur mit Akkustrom, den das Haus bis zur nächsten PV-Füllung nicht braucht; im Sommer spürbar, im Winter fast nie. „Ja starte mal“ (10:16).
- **Heute** (main 13f23b3): „Sofort“ und „Geplant“ bekommen nie etwas aus dem Akku (`SUPPORT_MODES` = PV-Überschuss und Min+PV; Spec 7.6 „Stand Plan 4b“, „kein Fluss Akku → Auto“ in 9.3). Spec 7.6 „Stand Plan 5b“ hielt eine Optimierung der Unterstützung für offen.
- **Was sich für dich ändert:** Unter Einstellungen → Planung gibt es den Schalter „Akku ins Auto, wenn übrig“ (ab Werk aus). Ist er an, plant der Optimierer für Autos in „Geplant“ Akkustrom ein, wo er sich lohnt, und Autos in „Sofort“ laden in lohnenden Slots aus dem Akku, bis der SOC die Plan-Schwelle erreicht. Im Zeitplan stehen die Sätze „{Auto} lädt {kW} kW aus dem Akku statt aus dem Netz.“ und „Autos in Sofort dürfen aus dem Akku laden, solange er über {soc} % liegt.“, im Entscheidungslog der Grund „Akku übrig fürs Auto“. PV-Überschuss, Min+PV (Akku-Unterstützung ab 70 %) und Boost bleiben, wie sie sind.
- **Wirkt erst mit Victron live.** Im Trockenlauf stehen die Befehle nur im Entscheidungslog.
- **Ausrollen:** core und app **gemeinsam** (die Absicht hat neue Felder im Plan, ein alter core lehnt sie ab, `extra="forbid"`), mit dem nächsten gemeinsamen Pull. Keine Migration, keine ACL-Zeile. Den Schalter schaltest du erst ein, wenn Victron live ist.

## Entscheidungen (freigegeben vom Betreiber am 28.10.2026: „Freigegeben“)

1. **Zuschnitt:** „Geplant“ über den Optimierer, „Sofort“ über übrigen Akku nach der Plan-Schwelle. PV-Überschuss, Min+PV und Boost bleiben unverändert (die Unterstützung ab 70 % gilt weiter; die Plan-Schwelle darf sie wie bisher nur anheben). Nicht in diesem Plan: Akku für Gäste über die Planung (ein Gast in „Sofort“ zählt wie ein Auto in „Sofort“, siehe 6), Home Assistant, Ersatzplan.
2. **Nur PV-Energie aus dem Akku ins Auto:** Solange nach einem Netzladen Netzenergie im Akku sein kann (dieselbe Größe `c_t` wie Regel R1, bis der Akku wieder leer ist), gibt der Akku keinem Auto etwas. Sonst schöbe der Plan Netzstrom über den Akku ins Auto, an Regel R2 vorbei und mit rund 12 % Verlust.
3. **Kosten je kWh Akku → Auto im Optimierer:** Verschleiß `planning.wear_ct` (2 ct) plus Marge `planning.margin_ct` (2 ct), wie beim Netzladen. Die Verluste (`η_dis`) stecken im SOC. Die Marge hält den Plan von kleinen, unsicheren Gewinnen fern.
4. **Geplant im core:** Ein Auto in „Geplant“ bekommt aus dem Akku höchstens die Leistung, die der gültige Slot für es aus dem Akku nennt (`ev_battery_w`), und nur, solange der SOC über dem geplanten SOC am Ende des Slots liegt (`soc_end_pct`). Braucht das Haus mehr als geplant, endet der Akkuanteil früher und der Rest kommt aus dem Netz. Ohne gültigen Slot: nichts aus dem Akku (wie heute).
5. **Sofort in der app:** Ein Slot erlaubt übrigen Akku für „Sofort“ (`now_battery`), wenn der Schalter an ist, der Plan vom Optimierer kommt, die Absicht `EIGENVERBRAUCH` ist, keine Netzenergie im Akku sein kann (2) und sich Akkustrom rechnet: `import_ct − wear_ct − margin_ct ≥ v / η_dis`. `v` ist der Wert einer kWh, die im Akku bliebe: der Einspeisepreis des ersten späteren Slots, in dem der Akku laut Nachrechnung voll ist und einspeist (ohne Einspeisepreis 0), sonst der Restwert `end_value_ct`.
6. **Sofort im core:** In einem Slot mit `now_battery` bekommt jedes Auto in „Sofort“ (auch ein Gast) seinen ganzen Fehlbetrag aus dem Akku, solange der SOC über der Plan-Schwelle des Slots liegt (`support_soc_pct`, der SOC, den der Plan für seine späteren Entladungen braucht). Fällt er auf sie, ist Schluss, bis er wieder `control.support_hysteresis_pct` (5 Punkte) darüber liegt. Keine eigene Leistungsgrenze: Was der Akku nicht liefern kann, holt ESS ohnehin aus dem Netz.
7. **Schalter `planning.car_battery_allowed`** („Akku ins Auto, wenn übrig“), ab Werk **aus**, im UI unter Einstellungen → Planung neben „Netzladen erlaubt“. Eine Änderung plant neu (Auslöser `settings`, wie jede Planungseinstellung). Aus heißt: kein `bt_ev` im Optimierer, kein `now_battery`; der core regelt wie heute.
8. **Ersatzplan:** kein Akku ins Auto, kein `now_battery`.
9. **Anzeige:** Gründe im Zeitplan (`AUTO_AUS_AKKU`, `SOFORT_AUS_AKKU`), Grund `battery_spare` („Akku übrig fürs Auto“) im Entscheidungslog, `battery.spare` in Health. Das Diagramm des Zeitplans bleibt (die Ladeleistung je Auto enthält den Akkuanteil). Die Statistik weist „Akku → Autos“ schon aus (Plan 4a).
10. **Vergleich „Victron allein“ bleibt** wie er ist (Autos aus PV und Netz). Der Szenario-Runner zählt für Akku → Auto den Verschleiß als Kosten, damit der Vergleich ihn nicht beschönigt.
11. **Nicht in diesem Plan:** ein HA-Schalter (kann folgen, braucht keine ACL-Zeile), Akku ins Auto im Ersatzplan, eine Senkung der 70 % für PV-Autos über die Planung.

## Global Constraints

- Texte für den Betreiber auf Deutsch, Anrede „du“; Kommentare und Docstrings im Stil der Umgebung (deutsch).
- Feldnamen: `PlanningSettings.car_battery_allowed`, `PlanSlot.ev_battery_w`, `PlanSlot.now_battery`, `IntentPlanSlot.ev_battery_w`, `IntentPlanSlot.now_battery`, `ActiveSlot.vehicles_battery`, `ActiveSlot.now_battery`, `SlotFlows.ev_battery`, `SimSlot.ev_battery_w`, `SimStep.ev_battery_w`. Grundcodes `AUTO_AUS_AKKU`, `SOFORT_AUS_AKKU`; Grund im Log `battery_spare`, Text „Akku übrig fürs Auto“.
- Sätze wörtlich: `AUTO_AUS_AKKU` „{name} lädt {kw} kW aus dem Akku statt aus dem Netz.“, `SOFORT_AUS_AKKU` „Autos in Sofort dürfen aus dem Akku laden, solange er über {soc} % liegt.“; UI-Schalter „Akku ins Auto, wenn übrig“ mit Hilfetext „Autos in Geplant und Sofort laden aus dem Akku, wenn das Haus die Energie bis zur nächsten PV-Füllung nicht braucht und Akkustrom billiger ist als Netzstrom.“
- Alte gespeicherte Pläne und Absichten laden ohne die neuen Felder (Standard `{}` und `False`).
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy --strict` (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators), UI `pnpm check`, `pnpm lint`, `pnpm test`. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Commits deutsch im Stil `feat(core): …`. Kein Aktor wird live geschaltet. Keine Modellbezeichnungen im Repo.

## Review Focus

- **Absicht `HALTEN` oder `NETZLADEN` mit einem Akkuanteil im Slot** (Rundung der Absichten): Der Akku gibt dann nichts, auch nicht an „Geplant“ und „Sofort“ → Tests in Task 4 (`test_car_battery_slot_is_never_hold`) und Task 7 (`test_hold_blocks_planned_and_now_battery`).
- **SOC unbekannt oder springt** (Victron liefert kurz nichts): kein Akkuanteil in diesem Zyklus, der Stand der Hysterese bleibt → Test in Task 7 (`test_spare_unknown_soc_blocks_without_changing_state`).
- **Alte Absicht ohne die neuen Felder und Plan ohne `soc_end_pct`:** lädt, gibt keinem Auto Akku → Tests in Task 1 und Task 7.
- **Akku → Auto, während PV eingespeist wird** (Verschleiß 0 eingestellt): verschenkt Energie → Plausibilität meldet es, Test in Task 4 (`test_car_battery_while_exporting_is_implausible`).
- **Zwei Autos in Sofort und ein geplantes Auto zugleich:** jede Wallbox bekommt höchstens ihren Fehlbetrag, die Reihenfolge der Priorität bleibt, die 3 kW der Unterstützung zählen nur PV-Autos → Test in Task 7 (`test_allowed_mixes_support_planned_and_spare`).

---

### Task 1: Felder in Einstellungen, Plan und Absicht

**Files:**
- Modify: `src/bindaems/shared/settings.py` (`PlanningSettings` ~228–237)
- Modify: `src/bindaems/shared/plan.py` (`ReasonCode`, `PlanSlot`, `IntentPlanSlot`, `intent_plan`)
- Test: `tests/unit/shared/test_settings_planning.py`, `tests/unit/shared/test_plan.py`

**Interfaces:**
- Produces: `PlanningSettings.car_battery_allowed: bool = False` (Kommentar: „Akku ins Auto, wenn übrig (Plan Akku ins Auto)“).
- Produces: `PlanSlot.ev_battery_w: dict[str, Finite] = {}` (W aus dem Akku je Auto, Teil von `ev_w`), `PlanSlot.now_battery: bool = False`.
- Produces: `IntentPlanSlot.ev_battery_w: dict[str, Annotated[int, Field(ge=0)]] = {}`, `IntentPlanSlot.now_battery: bool = False`.
- Produces: `ReasonCode` um `"AUTO_AUS_AKKU"` und `"SOFORT_AUS_AKKU"` erweitert.
- `intent_plan`: `ev_battery_w` auf ganze W gerundet, ohne erwartete Autos und ohne Einträge unter 1 W; beide neuen Felder nur, wenn die Absicht des Slots nach der Umwandlung durch `grid_charge_allowed` `EIGENVERBRAUCH` ist, sonst `{}` und `False`.

- [ ] **Step 1: Tests schreiben**

```python
def test_planning_settings_car_battery_defaults_off() -> None:
    assert PlanningSettings().car_battery_allowed is False
    assert PlanningSettings.model_validate({"grid_charge_allowed": True}).car_battery_allowed is False

def test_old_plan_slot_loads_without_battery_fields(plan_slot_json) -> None:
    slot = PlanSlot.model_validate(plan_slot_json)  # Fixture ohne die neuen Felder
    assert slot.ev_battery_w == {} and slot.now_battery is False

def test_intent_plan_carries_car_battery_and_now_battery() -> None:
    plan = _plan(slots=[_slot(ev_w={"tesla": 7000.4}, ev_battery_w={"tesla": 2500.6}, now_battery=True)])
    slot = intent_plan(plan, grid_charge_allowed=True).slots[0]
    assert slot.ev_battery_w == {"tesla": 2501}
    assert slot.now_battery is True

def test_intent_plan_drops_battery_fields_outside_eigenverbrauch() -> None:
    held = _slot(intent=BatteryIntent(kind=BatteryIntentKind.HALTEN),
                 ev_battery_w={"tesla": 2000.0}, now_battery=True)
    slot = intent_plan(_plan(slots=[held]), grid_charge_allowed=True).slots[0]
    assert slot.ev_battery_w == {} and slot.now_battery is False

def test_intent_plan_skips_expected_cars_and_tiny_battery_shares() -> None:
    plan = _plan(vehicles=[_car("tesla"), _car("egolf", expected=True)],
                 slots=[_slot(ev_battery_w={"tesla": 0.4, "egolf": 2000.0})])
    assert intent_plan(plan, grid_charge_allowed=True).slots[0].ev_battery_w == {}
```

- [ ] **Step 2: Tests laufen lassen, sie scheitern**

Run: `uv run pytest tests/unit/shared/test_plan.py tests/unit/shared/test_settings_planning.py -q`
Expected: FAIL (unbekannte Felder).

- [ ] **Step 3: Felder und `intent_plan` umsetzen** (Docstrings von `PlanSlot`, `IntentPlanSlot` und `intent_plan` um die Felder ergänzen).

- [ ] **Step 4: Tests grün**, dazu `tests/unit/shared` ganz.

- [ ] **Step 5: Commit** `feat(shared): Felder für Akku ins Auto in Plan, Absicht und Einstellungen`

### Task 2: Nachrechnung mit Akku → Auto

**Files:**
- Modify: `src/bindaems/app/planning/battery_model.py` (`SimSlot`, `SimStep`, `step`, `sim_slots`, Docstring des Moduls)
- Test: `tests/unit/app/planning/test_battery_model.py`

**Interfaces:**
- Produces: `SimSlot.ev_battery_w: float = 0.0` (W, die die geplanten Autos zusammen aus dem Akku bekommen dürfen; Teil von `ev_w`).
- Produces: `SimStep.ev_battery_w: float = 0.0` (W aus dem Akku in die Autos; in `discharge_w` enthalten, nicht in `import_w`).
- Produces: `sim_slots(inputs, ev_w=(), ev_battery_w=()) -> list[SimSlot]`; `ev_battery_w` ist eine Zuordnung je Slot wie `ev_w`, jeder Slot bekommt die Summe.
- Reihenfolge in `step`: PV deckt Haus, dann Autos, dann Akku (wie heute). Bei `EIGENVERBRAUCH` entlädt der Akku zuerst für das Haus, dann für die Autos höchstens `min(ev_battery_w, Rest der Autos nach PV, max_discharge_w − Entladung fürs Haus, lieferbar − Entladung fürs Haus)`; bei `HALTEN` und `NETZLADEN` nichts. `ev_grid_w` ist der Rest.

- [ ] **Step 1: Tests schreiben**

```python
def test_step_battery_feeds_planned_car_after_house() -> None:
    slot = _slot(pv_w=0, load_w=500, ev_w=7000, ev_battery_w=3000)
    result = step(20.0, slot, BatteryIntent(), PARAMS)
    assert result.discharge_w == pytest.approx(3500)
    assert result.ev_battery_w == pytest.approx(3000)
    assert result.ev_grid_w == pytest.approx(4000)
    assert result.import_w == pytest.approx(4000)

def test_step_hold_gives_the_car_nothing() -> None:
    slot = _slot(pv_w=0, load_w=500, ev_w=7000, ev_battery_w=3000)
    result = step(20.0, slot, BatteryIntent(kind=BatteryIntentKind.HALTEN), PARAMS)
    assert result.ev_battery_w == 0 and result.import_w == pytest.approx(7500)

def test_step_car_battery_limited_by_discharge_and_reserve() -> None:
    slot = _slot(pv_w=0, load_w=4000, ev_w=7000, ev_battery_w=7000, max_discharge_w=5000)
    assert step(20.0, slot, BatteryIntent(), PARAMS).ev_battery_w == pytest.approx(1000)
    near_reserve = PARAMS.lo_kwh + 0.1  # liefert nur noch 0,1 kWh · η_dis über den Slot
    assert step(near_reserve, slot, BatteryIntent(), PARAMS).discharge_w == pytest.approx(0.1 * PARAMS.eta_dis / SLOT_H * 1000)

def test_step_pv_first_then_battery_for_the_car() -> None:
    slot = _slot(pv_w=2000, load_w=500, ev_w=4000, ev_battery_w=4000)
    result = step(20.0, slot, BatteryIntent(), PARAMS)
    assert result.ev_pv_w == pytest.approx(1500)
    assert result.ev_battery_w == pytest.approx(2500)
    assert result.import_w == pytest.approx(0)
```

- [ ] **Step 2: scheitern lassen** – `uv run pytest tests/unit/app/planning/test_battery_model.py -q`
- [ ] **Step 3: umsetzen**
- [ ] **Step 4: grün**, dazu `tests/unit/app/planning` ganz (die übrigen Aufrufer übergeben noch nichts, das Verhalten bleibt gleich).
- [ ] **Step 5: Commit** `feat(app): Nachrechnung kennt Akku ins Auto`

### Task 3: Fluss Akku → Auto im Optimierer

**Files:**
- Modify: `src/bindaems/app/planning/optimizer.py` (`SlotFlows`, `_Car`, `_add_car`, `_solve`, `ev_power_w`, Docstring des Moduls)
- Test: `tests/unit/app/planning/test_optimizer.py`, `tests/property/test_optimizer.py`

**Interfaces:**
- Consumes: `PlanningSettings.car_battery_allowed` (Task 1).
- Produces: `SlotFlows.ev_battery: Mapping[str, float] = {}` (W aus dem Akku je Auto; nur Autos mit Variable im Slot).
- Produces: `ev_power_w(result)` zählt `pv + grid + battery`; neu `ev_battery_power_w(result: OptimizerResult) -> list[dict[str, float]]` (Akkuanteil je Auto und Slot ab `EV_ON_W`).
- Modell (nur mit `car_battery_allowed`, sonst gibt es `bt_ev` nicht):
  - `_Car.battery: dict[int, LpVariable]` je Slot im Fenster, `0 ≤ bt_ev ≤ max_w`; `power(t) = pv + grid + battery`.
  - Entladen: `bt_h_t + Σ_c bt_ev_ct ≤ max_discharge_w · (1 − y_t)` (ersetzt `entladen_t`).
  - SOC: `s_t = s_{t−1} + Δ · (η_ch · Laden − (bt_h_t + Σ bt_ev_ct) / η_dis)`.
  - Kosten: `Δ · (wear_ct + margin_ct) · Σ bt_ev_ct`.
  - Regel R1 (Entscheidung 2): für jedes `t ≥ first` mit `c_t`: `Σ bt_ev_ct ≤ (Σ max_w der Autos im Slot) · (1 − c_t)`.
  - Regel R2 zählt `bt_ev` nicht.

- [ ] **Step 1: Tests schreiben** (Eingaben mit den Hilfen der Datei; Slots einer Sommernacht und des Folgetags)

```python
def test_planned_car_takes_spare_battery_when_pv_refills_it(summer_night_inputs) -> None:
    # Akku 95 %, Haus nachts 400 W, Auto Geplant 22:00–07:00 mit 20 kWh Bedarf, Nachtpreis 18 ct,
    # Einspeisung 6 ct, ab 09:00 PV-Überschuss, der den Akku füllt und einspeist
    on = summer_night_inputs(car_battery_allowed=True)
    off = summer_night_inputs(car_battery_allowed=False)
    used_kwh = sum(sum(f.ev_battery.values()) for f in solve(on).flows) * SLOT_H / 1000
    assert used_kwh > 5.0
    assert all(not f.ev_battery for f in solve(off).flows)
    assert solve(on).objective_ct < solve(off).objective_ct

def test_planned_car_gets_no_battery_when_it_does_not_pay(winter_night_inputs) -> None:
    # Restwert 20 ct, Nachtpreis 12 ct: 12 − 2 − 2 < 20 / η_dis
    result = solve(winter_night_inputs(car_battery_allowed=True))
    assert all(sum(f.ev_battery.values()) < EV_ON_W for f in result.flows)

def test_no_battery_to_car_while_grid_energy_is_in_the_battery(grid_then_car_inputs) -> None:
    result = solve(grid_then_car_inputs(car_battery_allowed=True, grid_charge_allowed=True))
    charged = [t for t, f in enumerate(result.flows) if f.gr_b >= 100]
    assert charged
    empty = next(t for t, f in enumerate(result.flows) if t > charged[0] and f.soc_end_kwh <= LO_KWH + R1_EMPTY_TOL_KWH)
    assert all(sum(result.flows[t].ev_battery.values()) < EV_ON_W for t in range(charged[0], empty + 1))

def test_ev_power_counts_the_battery_share(summer_night_inputs) -> None:
    result = solve(summer_night_inputs(car_battery_allowed=True))
    for flows, powers in zip(result.flows, ev_power_w(result)):
        for key, (pv, grid) in flows.ev.items():
            assert powers.get(key, 0.0) == pytest.approx(pv + grid + flows.ev_battery.get(key, 0.0), abs=EV_ON_W)
```

Property-Test (`tests/property/test_optimizer.py`, bestehende Strategie um `car_battery_allowed` erweitern): `bt_h + Σ bt_ev ≤ max_discharge_w + 1`, nie Laden und Entladen zugleich (je mehr als 1 W), `Σ bt_ev = 0` bei ausgeschaltetem Schalter.

- [ ] **Step 2: scheitern lassen** – `uv run pytest tests/unit/app/planning/test_optimizer.py -q`
- [ ] **Step 3: umsetzen**
- [ ] **Step 4: grün**, dazu `tests/property/test_optimizer.py`; Lösezeit des längsten Planungsszenarios notieren (Task 6 misst sie).
- [ ] **Step 5: Commit** `feat(app): Optimierer plant Akku ins Auto für Geplant`

### Task 4: Absichten, Plan und Plausibilität

**Files:**
- Create: `src/bindaems/app/planning/spare.py`
- Modify: `src/bindaems/app/planning/intents.py`, `builder.py`, `plausibility.py`, `service.py` (`plan_once`)
- Test: `tests/unit/app/planning/test_spare.py`, `test_intents.py`, `test_builder.py`, `test_plausibility.py`, `test_service.py`

**Interfaces:**
- Consumes: `ev_battery_power_w` (Task 3), `sim_slots(…, ev_battery_w)` (Task 2), Felder aus Task 1.
- Produces in `spare.py`:
  - `FULL_TOL_KWH: Final = 0.05` (Akku gilt als voll bei SOC am Slotende ≥ `hi_kwh − FULL_TOL_KWH`)
  - `spare_value_ct(inputs: PlanInputs, steps: Sequence[SimStep], index: int) -> float` (Entscheidung 5: Einspeisepreis des ersten späteren Slots mit vollem Akku und `export_w > 0`, ohne Preis 0; sonst `inputs.end_value_ct`)
  - `now_battery_flags(inputs: PlanInputs, intents: Sequence[BatteryIntent], steps: Sequence[SimStep]) -> list[bool]` (Entscheidung 5; „keine Netzenergie im Akku“ heißt: kein `NETZLADEN`-Slot `s ≤ t`, dessen Fenster `charged_window_end(steps, s, lo_kwh)` über `t` reicht)
- `derive_intents`: Ein Slot mit `Σ ev_battery ≥ HOLD_MIN_W` ist `EIGENVERBRAUCH` (nie `HALTEN`); alle Nachrechnungen darin (`_keep_rule_r1`, `_drop_unprofitable`) bekommen den Akkuanteil.
- `build_plan(…, ev_battery_w: Sequence[Mapping[str, float]] = ())`: Nachrechnung mit Akkuanteil, `PlanSlot.ev_battery_w` je Auto, `PlanSlot.now_battery` aus `now_battery_flags` nur bei `source == "optimizer"`; `baseline_cost_ct` bleibt ohne Akkuanteil (Entscheidung 10). Die Plan-Schwelle (`support_thresholds`) rechnet aus der Nachrechnung mit Akkuanteil.
- `check_plan` meldet (Wortlaut): „Akku ins Auto um {at}, obwohl ausgeschaltet“, „Akku ins Auto um {at} bei Halten oder Netzladen“, „Akku ins Auto um {at}, solange Netzstrom im Akku ist“, „Akku ins Auto um {at}, während eingespeist wird“ (je Slot mit Akkuanteil über 1 W).
- `plan_once`: übergibt den Akkuanteil an Nachrechnung, `check_plan` und `build_plan`; der Ersatzplan bleibt ohne (Entscheidung 8).

- [ ] **Step 1: Tests schreiben**

```python
def test_spare_value_is_feed_in_when_the_battery_fills_later() -> None:
    steps = _steps(soc_end=[18, 17, 30, 30], export_w=[0, 0, 0, 800], hi_kwh=30)
    assert spare_value_ct(_inputs(feed_in=[6, 6, 6, 5], end_value_ct=15), steps, 0) == 5

def test_spare_value_is_end_value_when_the_battery_never_fills() -> None:
    steps = _steps(soc_end=[18, 17, 16, 15], export_w=[0, 0, 0, 0], hi_kwh=30)
    assert spare_value_ct(_inputs(end_value_ct=15), steps, 0) == 15

def test_now_battery_needs_switch_price_and_eigenverbrauch() -> None:
    inputs = _inputs(import_ct=[25, 8, 25], end_value_ct=10, car_battery_allowed=True)  # wear 2, margin 2
    flags = now_battery_flags(inputs, [EIGEN, EIGEN, HALTEN], _steps_never_full())
    assert flags == [True, False, False]  # 25−4 ≥ 10/0,938; 8−4 < 10,66; Halten
    off = inputs.model_copy(update={"planning": inputs.planning.model_copy(update={"car_battery_allowed": False})})
    assert now_battery_flags(off, [EIGEN] * 3, _steps_never_full()) == [False] * 3

def test_now_battery_off_while_grid_energy_is_in_the_battery() -> None:
    flags = now_battery_flags(_inputs(import_ct=[5, 30, 30]), [NETZLADEN_2000, EIGEN, EIGEN], _steps_until_empty_at(2))
    assert flags == [False, False, False]

def test_car_battery_slot_is_never_hold(result_with_small_house_discharge_and_car_battery) -> None:
    intents = derive_intents(INPUTS, result_with_small_house_discharge_and_car_battery)
    assert intents[0].kind is BatteryIntentKind.EIGENVERBRAUCH

def test_build_plan_fills_ev_battery_and_threshold_counts_it() -> None:
    plan = build_plan(INPUTS, [EIGEN] * 4, source="optimizer", solver_status="optimal",
                      ev_w=[{"tesla": 4000}] * 4, ev_battery_w=[{"tesla": 3000}] * 4)
    assert plan.slots[0].ev_battery_w == {"tesla": 3000}
    assert plan.slots[0].support_soc_pct > build_plan(INPUTS, [EIGEN] * 4, source="optimizer",
        solver_status="optimal", ev_w=[{"tesla": 4000}] * 4).slots[0].support_soc_pct

def test_fallback_plan_has_no_car_battery() -> None:
    plan = plan_once(INPUTS_WITH_CAR_BATTERY_ON, _failed_result())
    assert plan.source == "fallback"
    assert all(not s.ev_battery_w and not s.now_battery for s in plan.slots)

@pytest.mark.parametrize("case,text", [
    ("switch_off", "Akku ins Auto um 22:00, obwohl ausgeschaltet"),
    ("hold", "Akku ins Auto um 22:00 bei Halten oder Netzladen"),
    ("grid_in_battery", "Akku ins Auto um 22:00, solange Netzstrom im Akku ist"),
])
def test_check_plan_reports_wrong_car_battery(case, text) -> None:
    assert text in check_plan(*_case(case))

def test_car_battery_while_exporting_is_implausible() -> None:
    assert "Akku ins Auto um 12:00, während eingespeist wird" in check_plan(*_case("exporting"))
```

- [ ] **Step 2: scheitern lassen** – `uv run pytest tests/unit/app/planning -q`
- [ ] **Step 3: umsetzen**
- [ ] **Step 4: grün**, ganze Datei-Gruppe `tests/unit/app/planning` und `tests/property`.
- [ ] **Step 5: Commit** `feat(app): Plan nennt Akku ins Auto und übrigen Akku für Sofort`

### Task 5: Erklärungen im Zeitplan

**Files:**
- Modify: `src/bindaems/app/planning/explain.py` (`_SENTENCES`, `_car_reasons`, `reasons_for`)
- Test: `tests/unit/app/planning/test_explain.py`

**Interfaces:**
- Consumes: `PlanSlot.ev_battery_w`, `now_battery` (Task 4); `reasons_for(…, ev_battery_w: Sequence[Mapping[str, float]] = (), now_battery: Sequence[bool] = ())`, `build_plan` übergibt beides.
- `AUTO_AUS_AKKU` (Parameter `name`, `kw`) in Slots, in denen ein Auto mindestens `MENTION_W` aus dem Akku lädt, nach `ZIEL_SOC_ABFAHRT` desselben Autos; `ZIEL_SOC_ABFAHRT` zählt nur noch den Netzanteil (Ladeleistung minus PV minus Akku).
- `SOFORT_AUS_AKKU` (Parameter `soc` = Plan-Schwelle des Slots) nur im ersten Slot jeder zusammenhängenden Folge mit `now_battery`, hinter den Codes der Autos.

- [ ] **Step 1: Tests schreiben**

```python
def test_sentence_auto_aus_akku() -> None:
    reason = Reason(code="AUTO_AUS_AKKU", params={"name": "Tesla", "kw": 2.5})
    assert sentence(reason) == "Tesla lädt 2,5 kW aus dem Akku statt aus dem Netz."

def test_sentence_sofort_aus_akku() -> None:
    reason = Reason(code="SOFORT_AUS_AKKU", params={"soc": 62})
    assert sentence(reason) == "Autos in Sofort dürfen aus dem Akku laden, solange er über 62 % liegt."

def test_car_from_battery_only_is_not_ziel_soc_abfahrt() -> None:
    codes = _codes(ev_w=[{"tesla": 3000}], ev_battery_w=[{"tesla": 3000}])
    assert codes[0] == ["EIGENVERBRAUCH", "AUTO_AUS_AKKU"]

def test_sofort_aus_akku_only_at_the_start_of_a_run() -> None:
    codes = _codes(now_battery=[True, True, False, True])
    assert ["SOFORT_AUS_AKKU" in c for c in codes] == [True, False, False, True]
```

- [ ] **Step 2–4:** scheitern lassen, umsetzen, grün (`tests/unit/app/planning/test_explain.py`, `test_builder.py`).
- [ ] **Step 5: Commit** `feat(app): Erklärungen für Akku ins Auto`

### Task 6: Szenario-Runner und Planungsszenarien

**Files:**
- Modify: `src/bindaems/tools/scenario/runner.py` (`_wanted_w` bzw. Ladeschritt, `DayResult`, Kosten), `src/bindaems/tools/scenario/synthetic.py` (`synthetic(…, car_battery: bool = False)`), `src/bindaems/tools/scenario/report.py` (Spalte „Akku → Auto“)
- Test: `tests/integration/test_planning_scenarios.py`, `tests/unit/tools/scenario/…` (bestehende Tests des Runners)

**Interfaces:**
- Runner: Der Akkuanteil eines Autos im angewandten Slot ist `min(ev_battery_w[key], tatsächliche Ladeleistung)`; die Summe geht als `SimSlot.ev_battery_w` in `step`. `DayResult.car_battery_kwh: float`; `optimizer_ct` zählt dazu `wear_ct · Σ car_battery_kwh` (Entscheidung 10).
- `synthetic(kind, first, days, *, seed=1, car_battery=False)` setzt `PlanningSettings(grid_charge_allowed=True, car_battery_allowed=car_battery)`.

- [ ] **Step 1: Tests schreiben**

```python
def test_car_battery_saves_on_a_sunny_summer() -> None:
    off = run_scenario(synthetic("sommer-sonnig", date(2027, 7, 5), 2), step_slots=4)
    on = run_scenario(synthetic("sommer-sonnig", date(2027, 7, 5), 2, car_battery=True), step_slots=4)
    assert sum(d.car_battery_kwh for d in on.days) > 1.0
    assert on.adjusted_optimizer_ct < off.adjusted_optimizer_ct - 10.0
    assert on.targets_missed == 0 and on.r1_violations == 0 and on.r2_violations == 0
    assert on.min_soc_kwh >= on.reserve_kwh - 0.01

def test_car_battery_does_not_cost_in_winter() -> None:
    off = run_scenario(synthetic("winter-spread", date(2027, 1, 11), 2), step_slots=4)
    on = run_scenario(synthetic("winter-spread", date(2027, 1, 11), 2, car_battery=True), step_slots=4)
    assert on.adjusted_optimizer_ct <= off.adjusted_optimizer_ct + 1.0
    assert on.r1_violations == 0 and on.r2_violations == 0
```

Die Werte (> 1 kWh, 10 ct) sind die Untergrenze; das Ergebnis in Euro steht im Docstring der Datei wie bei den anderen Szenarien. Budget der Datei bleibt 150 s; liegt sie darüber, laufen die neuen Fälle mit `step_slots=8`.

- [ ] **Step 2–4:** scheitern lassen, umsetzen, grün (`uv run pytest tests/integration/test_planning_scenarios.py -q`, mit Zeit).
- [ ] **Step 5: Commit** `test(app): Planungsszenarien mit Akku ins Auto`

### Task 7: core gibt Geplant und Sofort den Akkuanteil

**Files:**
- Modify: `src/bindaems/core/control/plan_exec.py` (`ActiveSlot`, `active_slot`)
- Modify: `src/bindaems/core/control/battery.py` (`BatteryCar`, neue Klasse `Spare`, `allowed`, `BatteryNeed`, `BatteryController.assess`, `_reasons`, `health`, Docstrings)
- Modify: `src/bindaems/core/control/regulation.py` (`battery_cars`)
- Modify: `src/bindaems/shared/decisions.py` (Text `battery_spare`)
- Test: `tests/unit/core/control/test_battery.py`, `test_battery_controller.py`, `test_plan_exec.py`, `test_regulation.py`, `tests/unit/shared/test_decisions.py`

**Interfaces:**
- Consumes: `IntentPlanSlot.ev_battery_w`, `now_battery`, `soc_end_pct`, `support_soc_pct`.
- Produces: `ActiveSlot.vehicles_battery: Mapping[str, int] = {}`, `ActiveSlot.now_battery: bool = False`.
- Produces: `BatteryCar.vehicle: str | None = None` (Fahrzeug-Schlüssel des Ladepunkts, auch ein Gast; `None` für eine Wallbox ohne Ladepunkt). `battery_cars` setzt `car.vehicle`.
- Produces: `SPARE_MODES: Final = frozenset({Effective.NOW})`; `BATTERY_SPARE: Final = "battery_spare"`; `SpareReason = Literal["on", "off", "plan", "soc"]`.
- Produces: `class Spare` mit `reset() -> None` und `update(soc_pct: float | None, active: ActiveSlot | None) -> SpareReason`:
  - `off`: kein Slot oder `now_battery` falsch; `plan`: `HALTEN`/`NETZLADEN`; `soc`: SOC unbekannt (Stand bleibt), SOC ≤ `active.support_soc_pct` (sperrt) oder gesperrt und SOC unter `support_soc_pct + support_hysteresis_pct`; sonst `on`. Ohne `support_soc_pct` im Slot: `soc`.
- `allowed(cars, deficit, *, support_on, support_max_w, planned_w: Mapping[str, int] = {}, planned_on: bool = False, spare_on: bool = False) -> dict[str, float]`: zusätzlich zu heute bekommt ein Auto in `PLANNED` mit `vehicle` bei `planned_on` `min(Fehlbetrag, planned_w.get(vehicle, 0))`, ein Auto in `NOW` bei `spare_on` seinen ganzen Fehlbetrag; beides geht nicht vom Rest der 3 kW ab.
- `assess`: `planned_on` = Absicht `EIGENVERBRAUCH`, SOC bekannt und `SOC > active.soc_end_pct` (ohne `soc_end_pct`: aus); `spare_on` = `Spare.update(…) == "on"`. `BatteryNeed.spare_w: float = 0.0` (Summe der Anteile von `PLANNED` und `NOW`), `BatteryNeed.spare: SpareReason = "off"`.
- `_reasons`: bei `EIGENVERBRAUCH` zuerst `battery_spare`, wenn `spare_w > 0`, sonst wie heute `battery_support`/`battery_comp`; dahinter `peak_guard`.
- `health()`: zusätzlich `spare` (Stand) und `spare_w`; vor dem ersten `assess` `None`.
- `shared/decisions.py`: `"battery_spare": "Akku übrig fürs Auto"`.

- [ ] **Step 1: Tests schreiben**

```python
def test_planned_car_gets_its_plan_battery_share() -> None:
    cars = [BatteryCar("evcs", Effective.PLANNED, 7000.0, True, vehicle="tesla")]
    shares = allowed(cars, {"evcs": 7000.0}, support_on=False, support_max_w=3000,
                     planned_w={"tesla": 2500}, planned_on=True)
    assert shares == {"evcs": 2500.0}

def test_planned_share_off_at_the_planned_slot_end_soc() -> None:
    need = _assess(soc=61.0, active=_slot(vehicles={"tesla": 7000}, vehicles_battery={"tesla": 2500}, soc_end_pct=61.0),
                   cars=[_planned("tesla", 7000.0)], pv=0.0, house=500.0)
    assert need.allowed_w["evcs"] == 0.0 and need.wanted_w == pytest.approx(7000.0)

def test_now_car_takes_spare_above_the_plan_threshold() -> None:
    need = _assess(soc=72.0, active=_slot(now_battery=True, support_soc_pct=70.0),
                   cars=[_now("tesla", 11000.0)], pv=0.0, house=500.0)
    assert need.allowed_w["evcs"] == pytest.approx(11000.0)
    assert need.wanted_w == 0.0 and need.spare == "on"

def test_now_spare_hysteresis() -> None:
    controller = _controller()
    active = _slot(now_battery=True, support_soc_pct=70.0)
    assert _spare(controller, 70.0, active) == "soc"   # erreicht: gesperrt
    assert _spare(controller, 74.0, active) == "soc"   # unter 70 + 5
    assert _spare(controller, 75.0, active) == "on"

def test_spare_unknown_soc_blocks_without_changing_state() -> None:
    controller = _controller()
    active = _slot(now_battery=True, support_soc_pct=70.0)
    assert _spare(controller, 80.0, active) == "on"
    assert _spare(controller, None, active) == "soc"
    assert _spare(controller, 72.0, active) == "on"

def test_hold_blocks_planned_and_now_battery() -> None:
    held = _slot(kind=BatteryIntentKind.HALTEN, now_battery=True, vehicles_battery={"tesla": 3000}, support_soc_pct=50.0, soc_end_pct=50.0)
    need = _assess(soc=90.0, active=held, cars=[_planned("tesla", 7000.0), _now("egolf", 4000.0, wallbox="twc")])
    assert need.allowed_w == {"evcs": 0.0, "twc": 0.0} and need.spare == "plan"

def test_no_plan_keeps_todays_rule() -> None:
    need = _assess(soc=95.0, active=None, cars=[_now("tesla", 11000.0), _planned("egolf", 4000.0, wallbox="twc")])
    assert need.allowed_w == {"evcs": 0.0, "twc": 0.0} and need.spare == "off"

def test_allowed_mixes_support_planned_and_spare() -> None:
    cars = [BatteryCar("evcs", Effective.PV, 4000.0, True, vehicle="tesla"),
            BatteryCar("twc", Effective.NOW, 7000.0, True, vehicle="egolf"),
            BatteryCar("evcs2", Effective.PLANNED, 3000.0, True, vehicle="gast")]
    shares = allowed(cars, {"evcs": 4000.0, "twc": 7000.0, "evcs2": 3000.0}, support_on=True,
                     support_max_w=3000, planned_w={"gast": 1000}, planned_on=True, spare_on=True)
    assert shares == {"evcs": 3000.0, "twc": 7000.0, "evcs2": 1000.0}

def test_reason_battery_spare_first() -> None:
    commands = _decide_with(need=_need(spare_w=2500.0, allowed={"evcs": 2500.0}, wanted=4000.0))
    assert commands[0].reasons[0] == "battery_spare"

def test_active_slot_carries_battery_fields() -> None:
    slot = active_slot(_intent_plan(ev_battery_w={"tesla": 2500}, now_battery=True), NOW)
    assert slot.vehicles_battery == {"tesla": 2500} and slot.now_battery is True

def test_decision_text_battery_spare() -> None:
    assert REASON_TEXT["battery_spare"] == "Akku übrig fürs Auto"
```

- [ ] **Step 2: scheitern lassen** – `uv run pytest tests/unit/core/control -q`
- [ ] **Step 3: umsetzen** (Docstrings von `Support`, `allowed`, `BatteryController` und Modul nachführen; `Spare.reset` mit `Support.reset` nach einer Pause)
- [ ] **Step 4: grün**, dazu `tests/integration/test_runtime_battery.py` und die Abdeckung ≥ 90 % in `core/control`.
- [ ] **Step 5: Commit** `feat(core): Akkuanteil für Geplant und Sofort aus dem Plan`

### Task 8: Simulator-Szenarien

**Files:**
- Modify: `src/bindaems/tools/sim/scenarios.py` (`_Plan`, `_plan`, zwei Szenarien, `SCENARIOS`)
- Test: `tests/integration/test_sim_scenarios.py` (läuft alle Szenarien)

**Interfaces:**
- `_Plan` und `_plan` bekommen `vehicles_battery: Mapping[str, int] = {}`, `now_battery: bool = False`, `support_soc_pct: float | None = None`, `soc_end_pct: Sequence[float] | None = None` (je Slot) und reichen sie in `IntentPlanSlot` weiter.
- Szenario `akku-uebrig-sofort` („Sofort am Abend mit übrigem Akku“): 30 min, Tesla in „Sofort“, SOC 72 %, Plan `EIGENVERBRAUCH` mit `now_battery` und `support_soc_pct` 70, Victron live. Prüfungen: (a) solange der SOC über 70 % liegt, ab 2 min nach Beginn kein Override des core (Netzbezug höchstens Haus- und Autoleistung über der Entladegrenze des Modells plus 300 W); (b) nachdem der SOC 70 % erreicht hat, ab 2 min danach Netzbezug mindestens Autoleistung minus 300 W (Akku nicht ins Auto); (c) im Log steht `battery_spare` vor dem ersten `battery_comp`.
- Szenario `akku-uebrig-geplant` („Geplant in der Nacht aus dem Akku“): 45 min, e-Golf in „Geplant“ mit Slot-Leistung 2800 W und `ev_battery_w` 2800 W, `soc_end_pct` je Slot so, dass der Akkuanteil im dritten Slot endet. Prüfungen: (a) bis dahin kein Netzbezug über 300 W für das Auto; (b) danach Netzbezug mindestens Autoleistung minus 300 W; (c) der SOC fällt nie unter das kleinste `soc_end_pct` minus 1 Punkt.
- Scheitert ein Szenario, liegt der Fehler zuerst in der Regelung (eigener `fix(core)`-Commit mit Test); das Szenario wird nur geändert, wenn es physikalisch nicht zu schaffen ist.

- [ ] **Step 1: Szenarien schreiben**
- [ ] **Step 2: laufen lassen** – `uv run python -m bindaems.tools.sim akku-uebrig-sofort akku-uebrig-geplant` und `uv run pytest tests/integration/test_sim_scenarios.py -q`; Ergebnis: alle Prüfungen bestanden.
- [ ] **Step 3: Commit** `test(sim): Szenarien Akku übrig fürs Auto`

### Task 9: UI

**Files:**
- Modify: `ui/src/lib/settings/PlanningEditor.svelte` (+ Test), `ui/src/lib/api/schemas.ts` (Planungseinstellungen, Plan-Slot, `ReasonCode`, Health `battery.spare`/`spare_w` optional), `ui/src/lib/charging/view.ts` (`REASONS.battery_spare`), Vertragsdateien `ui/src/lib/api/contract/*.json` über `tests/integration/test_ui_contract.py`, `ui/src/lib/testing/plan.ts`
- Test: `ui/src/lib/settings/PlanningEditor.test.ts`, `ui/src/lib/api/contract.test.ts`, `ui/src/lib/charging/view.test.ts`

**Interfaces:**
- Schalter „Akku ins Auto, wenn übrig“ (Checkbox wie „Netzladen erlaubt“, Feld `car_battery_allowed`, Hilfetext aus den Global Constraints), Lesende sehen ihn ohne Bedienung.
- Schemas nehmen alte Antworten ohne die neuen Felder an.

- [ ] **Step 1: Tests schreiben** (`PlanningEditor` zeigt den Schalter und schickt `car_battery_allowed: true`; Schema nimmt einen Slot mit `ev_battery_w`/`now_battery` und einen ohne an; `reasonText(['battery_spare'])` ergibt „Akku übrig fürs Auto“; Vertrag aus der app mit neuen Feldern)
- [ ] **Step 2–4:** scheitern lassen, umsetzen, grün – `cd ui && pnpm check && pnpm lint && pnpm test`; Vertrag: `uv run pytest tests/integration/test_ui_contract.py -q`.
- [ ] **Step 5: Commit** `feat(ui): Schalter Akku ins Auto, wenn übrig`

### Task 10: Spec, Betriebshandbuch, Übergabe

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absätze „Stand Plan Akku ins Auto“ in 7.6, 7.7, 9.3 und 9.5; 12 Einstellungen)
- Modify: `docs/betrieb.md` („Akku-Sollwert“: Tabelle „Was je Modus aus dem Akku darf“, „Gründe“ mit `battery_spare`, „Planung“ mit Schalter, Kosten und Gründen im Zeitplan; „Rollback auf einen Stand vor Akku ins Auto“: Schalter aus, core und app zurück, in `intent.json` die Felder `ev_battery_w`/`now_battery` entfernen oder warten, bis der Plan abläuft)
- Modify: `docs/uebergabe.md` (Stand, Rulings, Deferred minors, „Offen an der Anlage“: gemeinsam ausrollen, Schalter erst mit Victron live)
- Test: `tests/unit/test_docs.py`

- [ ] **Step 1:** Texte schreiben; die Entscheidungen 1–11 stehen in der Spec als „Stand Plan Akku ins Auto“, mit Abweichungen aus der Umsetzung als „Festlegung bei der Umsetzung“.
- [ ] **Step 2:** `uv run pytest tests/unit/test_docs.py -q` grün.
- [ ] **Step 3: Commit** `docs: Akku ins Auto in Spec, Betriebshandbuch und Übergabe`

## Abnahme

1. **CI:** die ganze Prüfkette grün, Planungsszenarien in Budget, Simulator-Szenarien bestanden.
2. **An der Anlage, erst mit Victron live und Schalter an,** an einem sonnigen Tag mit einem Auto in „Geplant“ über Nacht: Der Zeitplan zeigt `AUTO_AUS_AKKU`, das Entscheidungslog `battery_spare`, die Statistik am Folgetag eine größere Zeile „Akku → Autos“; der Akku ist am Mittag trotzdem voll. An einem trüben Tag zeigt der Zeitplan keinen Akkuanteil.
3. **Sofort:** Am Abend mit SOC deutlich über der Plan-Schwelle lädt ein Auto in „Sofort“ ohne Override aus dem Akku und hört an der Schwelle auf (Health `battery.spare` wechselt auf `soc`).

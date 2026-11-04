# Ausgleichsladung des Akkus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hat der Hausakku seit `planning.equalize_days` (Standard 14) Tagen nicht mindestens `planning.equalize_hold_min` (Standard 60 min) voll gestanden, plant der Planer einmal eine Vollladung auf 100 % und hält den Akku danach so lange voll. Bevorzugt aus PV, sonst im billigsten Netzfenster. Im Zeitplan steht „Ausgleichsladung“, die Akku-Kachel zeigt „Zuletzt voll vor … Tagen“.

**Architecture:** Nur app und UI. Ein neues Modul `app/planning/equalize.py` merkt sich, wann der Akku zuletzt voll gehalten wurde (live SOC alle 30 s, beim Start aus InfluxDB `raw`, 90 Tage), und bildet daraus `EqualizeInput` für die Eingangsdaten. Ist die Ausgleichsladung fällig, bekommt der Optimierer ein weiches Ziel „einmal im Horizont H Slots am Stück voll“ mit Strafe je fehlender kWh. Absichten, Plausibilität, Gründe, Plan-Schwelle und Anzeige kennen das Fenster dieser Vollladung. Keine Migration, keine Änderung an core, Absicht oder Safety.

**Tech Stack:** Python 3.12, pydantic, PuLP/HiGHS, InfluxDB 1.x (InfluxQL), pytest; Svelte 5, valibot, Vitest.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, 9.3 „Optimierungsmodell“ (mit „Stand Plan Netzgrenze je Phase“), 9.4 „Regelbasierter Ersatzplan“, 9.6 „Erklärungen“, 11.2 „InfluxDB 1.x“; Idee 9.1 in `/mnt/project-files/ideen/brainstorming-2026-11.md`.

**Basis:** Branch `claude/project-thread-ambh18` (Netzgrenze je Phase, Kopf `102628d`). Der Plan setzt auf dessen Optimierer (`gr_r`, Phasengrenze, gedachter Satz der Monatsspitze) auf. Vor der Umsetzung wird der Arbeitsbranch auf den dann aktuellen Stand von `main` gebracht, auf dem Netzgrenze je Phase liegen muss.

## Anlass

Im Winter erreicht der Akku oft wochenlang keine 100 %. Viele Akkus (LiFePO4 mit passivem Ausgleich) gleichen die Zellen nur oben aus, und der SOC des BMS driftet ohne volle Ladung. Reserve, Nachladen und Plan rechnen aber mit diesem SOC. Victron ESS kennt keine automatische Ausgleichsladung für Lithium-Akkus mit BMS. Ziel: fertig vor Mitte Dezember, ausgerollt mit Stufe 2 (ab 23.11.).

## Entscheidungen

1. **Fällig:** `now − last_full_at ≥ equalize_days · 24 h`. `planning.equalize_days`: ganze Zahl 0 bis 60, Standard **14**, 0 = aus. Ist `last_full_at` unbekannt, aber die Abfrage hat geklappt (90 Tage ohne volle Ladung), ist sie fällig.
2. **Voll** heißt SOC ≥ `FULL_PCT` = **99,5 %**. **Voll gehalten** heißt: der SOC bleibt `planning.equalize_hold_min` am Stück voll. Einstellung: ganze Minuten 0 bis 240 in Schritten von 15, Standard **60**. Bei 0 genügt ein einzelner voller Wert. Erst „voll gehalten“ setzt `last_full_at`, sonst bräche der nächste Plan das Halten ab, sobald 100 % erreicht sind.
3. **Quelle von „zuletzt voll“:** `FullChargeTracker` (`app/planning/equalize.py`).
   - Beim Start fragt er InfluxDB `raw` (90 Tage, so lange hält `raw` die Daten): `SELECT min("pct"), max("pct") FROM "raw"."soc" WHERE "id" = 'battery' AND time > now() - 90d GROUP BY time(15m) fill(none)`.
   - Gelingt die Abfrage nicht, versucht er es bei jedem Planungslauf wieder, höchstens alle 15 min.
   - Danach beobachtet er den live SOC im 30-s-Takt der Planung (`_watch`).
   - Ohne erfolgreiche Abfrage und ohne beobachtete volle Ladung ist der Stand **unbekannt**. Dann gibt es keine Ausgleichsladung, und die Kachel zeigt nichts. Es gibt keine Migration und keine neue Tabelle.
4. **Planer:** Ist sie fällig, gilt `soc_max_pct = max(soc_max_pct, 100)` für den Lauf. Dazu kommt ein weiches Ziel: In einem Fenster von `H` Slots am Stück steht der SOC am Slotende bei mindestens `voll − FULL_TOL_KWH`. Der Optimierer wählt das Fenster selbst (Binärvariable je möglichem Start, Summe = 1).
   - `H = max(1, ceil(Rest-Haltezeit / 15 min))`. Die Rest-Haltezeit ist `equalize_hold_min` minus die Zeit, die der Akku schon voll steht.
   - Was am Ziel fehlt (`miss`, kWh), kostet `EQUALIZE_CT` = **100 ct/kWh**. Das liegt über jedem Bezugspreis, also lädt der Plan voll, wann immer der Horizont es zulässt, und unter der Fehlmenge eines Autos (2000 ct/kWh). Ein Auto bis zur Abfahrt geht also vor.
   - PV kostet nichts und wird deshalb bevorzugt. Reicht sie nicht, lädt der Plan im billigsten Netzfenster.
   - Ist „voll“ im Horizont nicht erreichbar, lädt er so weit wie möglich. Die Energie bleibt im Akku und wird später genutzt; die Kosten sind Verluste und Marge.
5. **Netz nur mit „Netzladen erlaubt“** (Vorschlag, Karte im Thread): Die Ausgleichsladung nutzt das normale Netzladen `gr_b` mit seiner Grenze `GC_t`. Ist der Schalter aus, kommt sie nur über PV und Halten zustande. Darum braucht es keine Änderung am core. An der Anlage ist der Schalter an.
6. **Bleibt, wie es ist:** Regel R1 (hart), die Summen- und Phasengrenze, die Monatsspitze (auch der gedachte Satz) und das Nachladen auf die Reserve gelten unverändert. Nur „rechnet sich“ gilt nicht für Netzladen im Fenster der Ausgleichsladung. Das gilt für `_drop_unprofitable` und für den Befund „Netzladen um … rechnet sich nicht“; beides sähe sonst die Ausgleichsladung als Fehler, und der Befund schaltete auf den Ersatzplan.
7. **Fenster der Ausgleichsladung:** Es läuft von Slot 0 bis zum Ende des Haltefensters (`range(0, full_index + H)`).
   - In den Halte-Slots ist die Plan-Schwelle der Akku-Unterstützung 100 %, damit kein Auto den Akku leert, während er voll halten soll.
   - Halte-Slots ohne PV werden `HALTEN` wie heute (der Akku entlädt nicht).
   - Ob das BMS beim Halten an der Ladeschlussspannung ausgleicht oder Strom braucht, hängt vom Akku ab. Die Frage nach Hersteller und Zellchemie ist offen; die Haltezeit ist deshalb eine Einstellung.
8. **Ersatzplan:** keine Ausgleichsladung. Der Plan meldet sie fällig ohne Zeitpunkt.
9. **Anzeige:**
   - Neue Gründe `AUSGLEICHSLADUNG` (Netzladen im Fenster) und `AUSGLEICH_HALTEN` (Halte-Slots, Absicht `HALTEN`), Texte unter Task 4.
   - Zeitplan: „Ausgleichsladung 2,5 kW“ und „Voll halten“.
   - Akku-Kachel: eine Zeile, Texte unter Task 5.
   - Unter Einstellungen → Planung kommen zwei Felder dazu.
10. **Ausrollen:** nur `ems-app` geändert, aber wie immer mit dem Sammel-Pull Stufe 2 (ab 23.11.), nie allein. Keine Migration, keine ACL-Zeile, keine Änderung an `config.yaml`. Rückweg: Gespeicherte Einstellungen mit den zwei neuen Feldern und Pläne mit den neuen Gründen lehnt eine ältere app ab. Der Weg ist derselbe wie bei Netzgrenze je Phase (`docs/betrieb.md`, Abschnitt 4).
11. **Aufgeschoben:** ein Sensor „Akku zuletzt voll“ in Home Assistant, eine Benachrichtigung „Ausgleichsladung geplant“ und Kosten der Ausgleichsladung im Plan.

## Global Constraints

- Keine Änderung an `core/`, an `BatteryIntent`, `IntentPlan`, `IntentPlanSlot`, an der Safety-Schicht, an der Datenbank (keine Migration) oder an `config.yaml`.
- Neue Felder sind optional mit Standard: `PlanInputs.equalize = None`, `Plan.equalize = None`, `PlanningSettings.equalize_days = 14`, `equalize_hold_min = 60`. Gespeicherte Eingangsdaten, Pläne, Einstellungsversionen und Szenariodateien laden unverändert. Ohne `equalize` rechnet der Optimierer genau wie vorher.
- Die Szenario-Werkzeuge (`tools/scenario`, `export_days`, `scenario_days`) bleiben unverändert und rechnen ohne Ausgleichsladung.
- Lösezeit: `test_two_cars_over_36_hours_solve_in_time` besteht weiter, auch mit fälliger Ausgleichsladung (neuer Test in Task 3).
- Texte deutsch, Commits deutsch im Stil `feat(app): …`, keine Modellbezeichnungen im Repo.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy` strict (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators); UI: `pnpm check`, `pnpm lint`, `pnpm test`. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`. Vor einem Fast-Forward nach `main` auch die GitHub-CI des Branch-Kopfs prüfen.

## Review Focus

- **Ausgleichsladung lässt sich nicht erreichen:** Schalter aus, keine PV, oder der Horizont ist kürzer als `H`. Das Modell bleibt lösbar (`miss > 0`), der Status ist `optimal`, und es gibt keinen Ersatzplan (Task 3, `test_equalize_unreachable_stays_feasible`).
- **Netzladen für die Ausgleichsladung rechnet sich nie:** `_drop_unprofitable` darf es nicht streichen, und `check_plan` darf es nicht als „rechnet sich nicht“ melden. Sonst fällt der Plan auf den Ersatzplan zurück (Task 4, `test_equalize_grid_charge_survives_profit_checks`).
- **Neuer Plan zwischen „100 % erreicht“ und „gehalten“:** Die Ausgleichsladung bleibt fällig, `H` schrumpft auf die Rest-Haltezeit, und der Plan hält weiter. Er beginnt nicht von vorn und entlädt nicht sofort (Task 1, `test_equalize_input_counts_held_time`; Task 2, `test_tracker_run_survives_replan`).
- **InfluxDB beim Start nicht erreichbar:** Kein Absturz und keine Ausgleichsladung. Der Stand ist unbekannt, und der nächste Versuch kommt frühestens nach 15 min (Task 2, `test_tracker_unknown_without_influx`).
- **Zwei Autos über 36 h mit fälliger Ausgleichsladung:** Der Lauf bleibt unter dem Zeitlimit. Das Auto mit Abfahrt bekommt seinen Bedarf vor der Ausgleichsladung (Task 3, `test_equalize_with_two_cars_solves_in_time`, `test_car_need_beats_equalize`).

---

### Task 1: Einstellungen, Eingangsdaten und reine Rechnung

**Files:**
- Create: `src/bindaems/app/planning/equalize.py` (nur der reine Teil; den Tracker ergänzt Task 2)
- Modify: `src/bindaems/shared/settings.py` (`PlanningSettings`), `src/bindaems/app/planning/inputs.py` (`PlanInputs`, `assemble_inputs`)
- Test: `tests/unit/app/planning/test_equalize.py` (neu), `tests/unit/app/planning/test_inputs.py`, `tests/unit/shared/test_settings.py`

**Interfaces:**
- Produces:
  - `PlanningSettings.equalize_days: Annotated[int, Field(ge=0, le=60)] = 14`
  - `PlanningSettings.equalize_hold_min: Annotated[int, Field(ge=0, le=240, multiple_of=15)] = 60`
  - In `equalize.py`: `FULL_PCT: Final = 99.5`, `FULL_TOL_KWH: Final = 0.05`, `EQUALIZE_CT: Final = 100.0`, `LOOKBACK: Final = timedelta(days=90)`
  - `class FullChargeState(NamedTuple)`: `known: bool`, `last_full_at: datetime | None`, `full_since: datetime | None` (Beginn des aktuellen vollen Laufs, `None`, wenn der Akku gerade nicht voll ist)
  - `class EqualizeInput(_Model)`: `last_full_at: AwareDatetime | None`, `due: bool`, `days: int`, `hold_slots: Annotated[int, Field(ge=1, le=16)]`, `target_pct: Finite = 100.0`
  - `equalize_input(now: datetime, state: FullChargeState, planning: PlanningSettings) -> EqualizeInput | None`
  - `PlanInputs.equalize: EqualizeInput | None = None`
  - `assemble_inputs(..., equalize: EqualizeInput | None = None)`: legt `equalize` in die Eingangsdaten. Bei `equalize.due` setzt es `battery.soc_max_pct = max(battery.soc_max_pct, equalize.target_pct)`.

- [ ] **Step 1: Failing tests** in `test_equalize.py` (`NOW = at("2026-12-01T12:00:00+00:00")`):
  - `test_equalize_off_with_zero_days`: `equalize_days=0` → `None`.
  - `test_equalize_unknown_state_is_none`: `FullChargeState(False, None, None)` → `None`.
  - `test_equalize_due_after_days`: `last_full_at = NOW − 14 d` → `due is True`; `NOW − 13 d 23 h` → `due is False`, `days == 14`.
  - `test_equalize_due_without_any_full`: `FullChargeState(True, None, None)` → `due is True`, `last_full_at is None`.
  - `test_equalize_input_counts_held_time`: `full_since = NOW − 20 min`, Haltezeit 60 → `hold_slots == 3` (40 min, aufgerundet); `full_since = NOW − 59 min` → `1`; ohne `full_since` → `4`; Haltezeit 0 → `1`.
- [ ] **Step 2:** Tests in `test_inputs.py`:
  - `test_assemble_raises_soc_max_when_equalize_due`: `state(soc_max_pct=95.0)` mit fälligem `EqualizeInput` → `inputs.battery.soc_max_pct == 100.0`; nicht fällig → `95.0`.
  - `test_saved_inputs_without_equalize_load`: `PlanInputs.model_validate(<sample_inputs() als dict ohne "equalize">).equalize is None`.
- [ ] **Step 3:** Test in `test_settings.py`: `PlanningSettings()` hat `equalize_days == 14` und `equalize_hold_min == 60`. `equalize_hold_min=50` → `ValidationError`, `equalize_days=61` → `ValidationError`. Eine gespeicherte Planung ohne die Felder lädt mit den Standardwerten.
- [ ] **Step 4:** `pytest tests/unit/app/planning/test_equalize.py tests/unit/app/planning/test_inputs.py tests/unit/shared/test_settings.py -q` → FAIL (Import bzw. Feld fehlt).
- [ ] **Step 5:** Implementieren, Tests grün, Prüfkette, Commit `feat(app): Ausgleichsladung in Einstellungen und Eingangsdaten des Planers`.

### Task 2: Zuletzt voll merken (Tracker) und im Planungsdienst verdrahten

**Files:**
- Modify: `src/bindaems/app/planning/equalize.py`, `src/bindaems/app/planning/service.py` (`__init__`, `_watch`, `collect`), `src/bindaems/app/runtime.py`
- Test: `tests/unit/app/planning/test_equalize.py`, `tests/unit/app/planning/test_service.py`

**Interfaces:**
- Consumes: `FullChargeState`, `equalize_input`, `FULL_PCT`, `LOOKBACK` (Task 1); `InfluxReader`, `RP_RAW`, `quote_str`, `SOC_ID` (`app/ledger/counters.py`).
- Produces:
  - `latest_full_run(buckets: Sequence[tuple[datetime, float, float]], hold: timedelta, now: datetime) -> FullChargeState`: Reine Rechnung aus 15-min-Eimern `(Beginn, min, max)`.
    - Haltezeit 0: `last_full_at` ist das Ende des letzten Eimers mit `max ≥ FULL_PCT`.
    - Sonst ist es das Ende des letzten Laufs aus `ceil(hold / 15 min)` lückenlos aufeinanderfolgenden Eimern mit `min ≥ FULL_PCT`.
    - Reicht ein voller Lauf bis zum Eimer von `now`, ist `full_since` sein Beginn.
    - Immer `known=True`.
  - `class FullChargeTracker`:
    - `__init__(self, reader: InfluxReader | None, clock: Clock)`
    - `async ensure_loaded(self, hold: timedelta) -> None`: fragt höchstens alle 15 min, bis eine Abfrage gelingt; `InfluxQueryError` wird geloggt (`code="equalize_history_unavailable"`) und lässt den Stand unbekannt.
    - `observe(self, soc_pct: float | None, at: datetime, hold: timedelta) -> bool`: Gibt `True` zurück, wenn dieser Aufruf eine volle Ladung als gehalten abschließt. `None` ändert nichts.
    - `state(self) -> FullChargeState`
  - `PlanningService(..., full_charge: FullChargeTracker | None = None)`:
    - `_watch` ruft `observe` mit dem live SOC auf. Bei `True` fordert es `"soc"` an, damit der nächste Plan das Halten beendet.
    - `collect` ruft `ensure_loaded` auf und reicht `equalize_input(now, state, planning)` an `assemble_inputs` weiter.
    - Ohne Tracker ist `equalize` immer `None`.
  - `runtime.py`: `FullChargeTracker(self.reader, self._clock)` an `PlanningService`.

- [ ] **Step 1: Failing tests** (`test_equalize.py`, Eimer als Hilfsliste):
  - `test_latest_full_run_needs_hold`: Vier Eimer ab 10:00 mit min 99,6 → `last_full_at == 11:00`. Nur drei → `None` (Haltezeit 60).
  - `test_latest_full_run_hold_zero_uses_max`: ein Eimer mit max 100, min 97 → `last_full_at` = sein Ende.
  - `test_latest_full_run_gap_breaks_run`: Vier volle Eimer mit einer Lücke von 15 min dazwischen → kein Lauf.
  - `test_latest_full_run_open_run_sets_full_since`: Die letzten zwei Eimer bis `now` sind voll → `full_since` ist der Beginn des ersten davon.
  - `test_tracker_loads_history_once` mit `FakeReader` (`tests/helpers.py`): Die Abfrage enthält `"raw"."soc"`, `'battery'` und `GROUP BY time(15m)`. Ein zweiter `ensure_loaded` fragt nicht erneut.
  - `test_tracker_unknown_without_influx`: Der Reader wirft `InfluxQueryError` → `state().known is False`. Ein erneuter Aufruf nach 10 min fragt nicht, nach 15 min schon.
  - `test_tracker_observe_completes_hold`: SOC 99,6 um 10:00 und 10:30 → `False`, Stand fällig. Um 11:00 → `True`, `last_full_at == 11:00`, `known is True` (auch ohne Influx).
  - `test_tracker_run_survives_replan`: SOC 99,6 um 10:00, 98,0 um 10:20 → `full_since is None`. Wieder 99,6 ab 10:30 → `full_since == 10:30`.
- [ ] **Step 2:** Tests in `test_service.py`:
  - `test_collect_passes_equalize_when_due`: Tracker-Stand von vor 15 Tagen → `inputs.equalize.due`. Ohne Tracker → `inputs.equalize is None`.
  - `test_watch_requests_soc_when_hold_completes`: Live SOC bleibt 60 min auf 100 → `pending() == "soc"`.
- [ ] **Step 3:** FAIL prüfen, implementieren, grün, Prüfkette, Commit `feat(app): Planer merkt sich, wann der Akku zuletzt voll war`.

### Task 3: Optimierer plant die Vollladung

**Files:**
- Modify: `src/bindaems/app/planning/optimizer.py` (`OptimizerResult`, `_solve`, neues `_add_equalize`, Modul-Docstring)
- Test: `tests/unit/app/planning/test_optimizer.py`, `tests/plan_helpers.py` (`scenario(..., equalize=None)` reicht das Feld durch)

**Interfaces:**
- Consumes: `EqualizeInput`, `EQUALIZE_CT`, `FULL_TOL_KWH` (Task 1).
- Produces:
  - `OptimizerResult.equalize_index: int | None = None`: erster Slot des Haltefensters, nur mit fälliger Ausgleichsladung und `miss ≤ FULL_TOL_KWH`.
  - `OptimizerResult.equalize_miss_kwh: float | None = None`: was am Ziel fehlt, `None` ohne fällige Ausgleichsladung.
  - `equalize_window(inputs: PlanInputs, result: OptimizerResult) -> range` in `equalize.py`: `range(0, equalize_index + hold_slots)`, sonst `range(0)`.
- Modell, nur bei `inputs.equalize` mit `due` und `len(slots) ≥ H` (`H = equalize.hold_slots`), `full = params.hi_kwh`, `span = hi − lo`:
  - je möglichem Start `t ∈ [0, count − H]` eine Binärvariable `f_t`, dazu `Σ f_t = 1`
  - für jedes `k < H`: `s_{t+k} + miss ≥ full − FULL_TOL_KWH − span · (1 − f_t)`
  - `miss ≥ 0` kostet `EQUALIZE_CT` je kWh
  - Ist `len(slots) < H`, gibt es keine Bedingung und `equalize_miss_kwh = full − max(s_t)`.

- [ ] **Step 1: Failing tests** (`_BIG_KWH`-Akku, `scenario`-Helfer, Haus 600 W):
  - `test_equalize_fills_in_cheapest_grid_window`: SOC 50 %, Netzladen an, keine PV, Preise mit 4 billigen Slots um 02:00, `hold_slots=4`. Ergebnis `optimal`, `equalize_index` gesetzt, `soc_end_kwh ≥ full − 0.05` in allen vier Halte-Slots, `gr_b > 0` nur vor dem Fenster und vor allem in den billigen Slots.
  - `test_equalize_prefers_pv`: Gleicher Aufbau mit PV-Bogen 9 kW morgen, die den Akku füllen kann → `Σ gr_b == 0`, Fenster in den PV-Slots.
  - `test_equalize_unreachable_stays_feasible`: Netzladen aus, keine PV → `optimal`, `equalize_index is None`, `equalize_miss_kwh > 0`. Dasselbe mit einem Horizont von 3 Slots und `hold_slots=4`.
  - `test_no_equalize_constraint_when_not_due`: `due=False` → dieselben Flüsse und dasselbe Ziel wie ohne `equalize` (`pytest.approx`).
  - `test_car_need_beats_equalize`: Auto mit Abfahrt 07:00 und Bedarf, der die Summengrenze in der Nacht ausschöpft → `shortfall_kwh == 0`, die Ausgleichsladung darf `miss > 0` haben.
  - `test_equalize_respects_phase_limit_and_r1`: Mit `grid_phase_import_w=4370` hält das Netzladen der Ausgleichsladung die Phasengrenze. Nach dem Netzladen speist der Plan bis zum leeren Akku nicht ein.
  - `@pytest.mark.slow test_equalize_with_two_cars_solves_in_time`: Aufbau wie `test_two_cars_over_36_hours_solve_in_time` plus fällige Ausgleichsladung → Status `optimal` oder `time_limit` mit Lösung, `solve_ms < TIME_LIMIT_S · 1000`.
- [ ] **Step 2:** FAIL prüfen, implementieren, grün, Prüfkette, Commit `feat(app): Optimierer plant die Ausgleichsladung`.

### Task 4: Absichten, Plausibilität, Gründe, Plan und API

**Files:**
- Modify: `src/bindaems/app/planning/intents.py` (`derive_intents`, `_drop_unprofitable`), `src/bindaems/app/planning/plausibility.py` (`check_plan`, Spread-Befund), `src/bindaems/app/planning/explain.py` (`_SENTENCES`, `_main_reason`, `reasons_for`), `src/bindaems/app/planning/builder.py` (`build_plan`, `support_thresholds`-Aufruf), `src/bindaems/app/planning/service.py` (`plan_once`), `src/bindaems/shared/plan.py` (`ReasonCode`, `PlanEqualize`, `Plan.equalize`), `src/bindaems/app/planning/routes.py` (`plan_json`)
- Test: `tests/unit/app/planning/test_intents.py`, `test_plausibility.py`, `test_explain.py`, `test_builder.py`, `test_routes.py`

**Interfaces:**
- Consumes: `equalize_window` (Task 3), `EqualizeInput` (Task 1).
- Produces:
  - `ReasonCode` bekommt `"AUSGLEICHSLADUNG"` und `"AUSGLEICH_HALTEN"`.
  - `class PlanEqualize(_Model)` in `shared/plan.py`:
    - `last_full_at: AwareDatetime | None`
    - `due: bool`
    - `days: int`
    - `hold_min: int`
    - `full_at: AwareDatetime | None`: Beginn des Haltefensters, nur wenn auch die Nachrechnung im ersten Fenster-Slot mindestens `target_pct − 1` erreicht
    - `until: AwareDatetime | None`: Ende des Haltefensters
  - `Plan.equalize: PlanEqualize | None = None` (ohne `EqualizeInput` im Lauf `None`; im Ersatzplan mit `full_at = until = None`).
  - `build_plan(..., equalize_window: range = range(0))`, `reasons_for(..., equalize_window: range = range(0))`, `check_plan(...)` liest das Fenster selbst aus `result`.
  - `plan_json` gibt `"equalize": null | {"last_full_at", "due", "days", "hold_min", "full_at", "until"}` (Zeiten als ISO).
- Texte (wörtlich):
  - `AUSGLEICHSLADUNG`, Parameter `tage` (ganze Zahl oder fehlt) und `um` (HH:MM, Beginn des Haltefensters):
    - mit `tage`: „Ausgleichsladung: Der Akku war seit {tage} Tagen nicht voll und lädt bis {um} Uhr auf 100 %.“
    - ohne `tage`: „Ausgleichsladung: Der Akku war lange nicht voll und lädt bis {um} Uhr auf 100 %.“
  - `AUSGLEICH_HALTEN`, Parameter `bis` (HH:MM): „Akku bleibt bis {bis} Uhr voll, damit das BMS die Zellen ausgleicht.“
  - Rangfolge in `_main_reason`:
    - `AUSGLEICHSLADUNG` gleich nach `NACHLADEN_RESERVE`, für jedes `NETZLADEN` ohne `reserve_recharge` im Fenster
    - `AUSGLEICH_HALTEN` vor den anderen `HALTEN`-Gründen, für `HALTEN` im Haltefenster
  - Plan-Schwelle: In den Halte-Slots ist `support_soc_pct` 100.

- [ ] **Step 1: Failing tests:**
  - `test_intents.py` `test_equalize_grid_charge_survives_profit_checks`: Aufbau aus Task 3, billig = teuer (Netzladen rechnet sich nie). Die `NETZLADEN`-Slots vor dem Fenster bleiben in `derive_intents`. `check_plan(...) == []`. Ohne `equalize` streicht `_drop_unprofitable` sie wie bisher.
  - `test_intents.py` `test_hold_window_is_halten_without_pv`: Die Halte-Slots in der Nacht sind `HALTEN`.
  - `test_explain.py`: beide Sätze wörtlich, mit und ohne `tage`; Rangfolge `NACHLADEN_RESERVE` vor `AUSGLEICHSLADUNG` vor `NETZLADEN_GUENSTIG`.
  - `test_builder.py`:
    - `test_plan_equalize_summary`: `full_at` und `until` entsprechen dem Fenster, `last_full_at` und `days` dem Eingang, `support_soc_pct == 100` in den Halte-Slots.
    - Wenn die Nachrechnung das Ziel verfehlt, ist `full_at is None`.
    - Ein Ersatzplan hat `due=True` und `full_at is None`.
  - `test_routes.py`: `plan_json` enthält `equalize` mit ISO-Zeiten. Ein Plan ohne das Feld (gespeichert) ergibt `"equalize": None`.
- [ ] **Step 2:** FAIL prüfen, implementieren, grün, Prüfkette, Commit `feat(app): Zeitplan erklärt die Ausgleichsladung`.

### Task 5: UI: Akku-Kachel, Zeitplan, Einstellungen

**Files:**
- Modify: `ui/src/lib/api/schemas.ts` (`PlanReasonCodeSchema`, `PlanEqualizeSchema`, `PlanSchema.equalize`, Einstellungsschema Planung), `ui/src/lib/api/contract/plan.json`, `ui/src/lib/api/contract/settings.json`, `ui/src/lib/plan/model.ts` (`intentLabel`, Gruppen), `ui/src/lib/dashboard/battery.ts` (`equalizeText`), `ui/src/lib/dashboard/BatteryTile.svelte`, `ui/src/lib/settings/model.ts`, `PlanningEditor.svelte`, `PlanningView.svelte`
- Test: `ui/src/lib/api/contract.test.ts`, `ui/src/lib/plan/model.test.ts`, `ui/src/lib/dashboard/battery.test.ts`, `BatteryTile.test.ts`, `ui/src/lib/settings/model.test.ts`, `PlanningEditor.test.ts`, `PlanningView.test.ts`

**Interfaces:**
- Consumes: `GET /api/plan` → `plan.equalize` (Task 4), Einstellungen `planning.equalize_days`, `planning.equalize_hold_min` (Task 1).
- Produces:
  - `PlanEqualizeSchema`: optional und nullable, Standard `null`.
  - `intentLabel(intent, gridChargeW, reserveRecharge, code?: PlanReasonCode)`:
    - `NETZLADEN` mit `AUSGLEICHSLADUNG` → „Ausgleichsladung 2,5 kW“
    - `HALTEN` mit `AUSGLEICH_HALTEN` → „Voll halten“
    - sonst wie bisher
  - `equalizeText(equalize: PlanEqualize | null, nowMs: number): string | null`, Texte wörtlich:
    - fällig mit `full_at`: „Ausgleichsladung: voll um {Wochentag HH:MM}“ (Format wie im Ausblick der Kachel)
    - fällig ohne `full_at`: „Ausgleichsladung fällig, im Plan nicht erreichbar“
    - nicht fällig, `last_full_at` heute: „Zuletzt voll heute“; gestern: „Zuletzt voll gestern“; sonst „Zuletzt voll vor {n} Tagen“ (Wiener Kalendertage)
    - `null` → keine Zeile
  - Einstellungen → Planung:
    - „Ausgleichsladung nach“ (Tage, 0 = aus; Fehlertext „Ganze Zahl 0–60, z. B. 14“)
    - „Voll halten“ (Minuten; Fehlertext „0–240 in Schritten von 15, z. B. 60“)
    - Ansicht: „nach 14 Tagen ohne volle Ladung, 60 min halten“ oder „aus“

- [ ] **Step 1: Failing tests:**
  - `contract.test.ts`: `plan.json` mit `equalize` parst, ohne parst es mit `null`, `AUSGLEICHSLADUNG` ist ein gültiger Code.
  - `model.test.ts`: die beiden Beschriftungen. Zwei Netzladen-Slots mit gleicher Leistung, einer mit `AUSGLEICHSLADUNG`, ergeben zwei Gruppen.
  - `battery.test.ts`: alle fünf Texte von `equalizeText` (fällig mit und ohne Zeit, heute, gestern, vor 9 Tagen über einen Monatswechsel).
  - `BatteryTile.test.ts`: Die Zeile erscheint mit Text und fehlt bei `null`.
  - Einstellungen: Felder, Prüfung, Ansicht „aus“ bei 0.
- [ ] **Step 2:** FAIL prüfen, implementieren, grün (`pnpm check`, `pnpm lint`, `pnpm test`), Commit `feat(ui): Ausgleichsladung in Akku-Kachel, Zeitplan und Einstellungen`.

### Task 6: Doku

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absatz „Stand Plan Ausgleichsladung“ in 9.3 nach „Stand Plan Netzgrenze je Phase“), `docs/betrieb.md` (Abschnitt 4 „Planung“: Tabelle der Gründe und die zwei Einstellungen; Abschnitt 12 „Zeitplan“: Beschriftungen; Rollback-Abschnitt aus Netzgrenze je Phase um die Felder `planning.equalize_days`, `planning.equalize_hold_min` und die Gründe ergänzen), `docs/uebergabe.md` (neuer Punkt: Stand, Rulings, Aufgeschobenes, „An der Anlage“)
- Test: `tests/unit/test_docs.py` (bestehende Prüfungen grün; neue Gründe stehen in der Tabelle in `docs/betrieb.md`, falls der Test die Codes gegen `ReasonCode` prüft)

- [ ] **Step 1:** Spec-Absatz mit den Entscheidungen 1 bis 11, Formeln aus Task 3, Texten aus Task 4 und 5.
- [ ] **Step 2:** `docs/betrieb.md`. Unter „An der Anlage beobachten“ nach Stufe 2:
  - Die Kachel zeigt „Zuletzt voll vor … Tagen“, passend zu Grafana (Panel Ladestand).
  - Spätestens 14 Tage nach der letzten vollen Ladung steht im Zeitplan „Ausgleichsladung“.
  - Danach steht der SOC 60 min auf 100 %, und die Kachel springt auf „Zuletzt voll heute“.
- [ ] **Step 3:** `docs/uebergabe.md`, `pytest tests/unit/test_docs.py -q` grün, Commit `docs: Ausgleichsladung in Spec, Betrieb und Übergabe`.

---

## Abschluss

Gesamtreview über den Branch, Fix-Welle für Critical und Important (jeder Fix mit einem Test, der vorher scheitert), Minors gesammelt. Abschlussnachricht mit Ergebnis und Tests, Rulings, Deferred minors und der Frage nach der Integration. Ausgerollt wird mit Stufe 2, nichts an der Anlage vor dem 23.11.

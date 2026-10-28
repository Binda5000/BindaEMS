# Vorklimatisieren über die Wallbox – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Beim Vorheizen und Vorkühlen nimmt das Auto den Strom von der Wallbox statt aus seinem Akku: BindaEMS stoppt einen Tesla nicht mehr, der wegen der Klimatisierung lädt, und lässt die EVCS für ein volles Auto (EVCS „geladen“) eingeschaltet, auch in PV-Überschuss.

**Architecture:** Zwei Regeln im core, beide nach dem Urteil der `ModeMachine` in `ChargeController.step` (die Maschine selbst bleibt gleich): **Regel K1** (Tesla mit Tessie): Meldet Tessie die Klimatisierung an und lädt das Auto, gilt das Urteil als „laden“ mit dem Mindeststrom des Ladepunkts, ohne eigenen Start, und der Selbststart zählt nicht. **Regel K2** (Auto an der EVCS): Ist das Auto im Ansteckvorgang „geladen“, gilt das Urteil als „laden“ mit dem Mindeststrom; die EVCS bleibt an. Beides schaltet ein Schalter je Fahrzeug in `config.yaml` ab. Der Tessie-Adapter liest dafür `climate_state`.

**Tech Stack:** Python 3.12, pydantic, httpx, pytest; Svelte 5/Vitest nur für den Grund-Text. Keine neuen Abhängigkeiten, keine Migration, keine Änderung der Absicht, keine ACL-Zeile.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Abschnitt 7.5 („Lademodi und Wallbox-Regler“, „Stand Plan 2c-1“: „Geladen“, „Stand Plan 2d“: „Selbststart“) und 6.5 (Tessie).

## Einordnung

- **Anlass (Betreiber, 28.10.2026):** „Der Tesla zieht beim Vorheizen/Vorkühlen den Strom von der Wallbox, aktuell versucht BindaEMS die Wallbox wieder zu deaktivieren. Beim e-Golf wird bei beendeter Ladung der Strom zum Vorheizen aus dem Akku genommen, können wir bei ‚vollem‘ e-Golf die Wallbox eingeschaltet lassen, auch im PV-Modus?“
- **Ursache laut Code (Annahme, an der Anlage zu bestätigen):**
  - *Tesla:* Startet die Klimatisierung, beginnt der Tesla selbst zu laden (Tessie `charging_state = "Charging"`). Will der Modus nicht laden (PV ohne Überschuss), sendet `_switch_off` nichts Neues, weil „Laden aus“ schon der letzte eigene Wert ist. Nach `control.tesla_against_s` (120 s) greift aber der **Selbststart** (`ChargeController._self_start`, Grund `self_start`) und schickt „Laden aus“ erneut, höchstens alle 600 s. Meldete Tessie dabei nicht „Charging“, würde BindaEMS gar nichts senden; dass du einen Stopp siehst, passt also nur zu „Charging“. Zu prüfen im Entscheidungslog: „Tesla Model 3 (PV-Überschuss, Selbststart): Laden aus“ zur Zeit eines Vorheizens.
  - *e-Golf:* Meldet die EVCS „geladen“ (Status 3), gilt das Auto bis zum Abstecken als geladen (`VehicleState.complete`), die Maschine sagt in jedem Modus „aus“ (Regel 3 in `ModeMachine.step`), und die EVCS bekommt einmal „Laden aus“. Danach kann die Standheizung nur aus dem Akku des e-Golf ziehen.
- **Was sich für dich ändert:** Während der Tesla klimatisiert und dabei lädt, bleibt er an, mit dem Mindeststrom (5 A × 3 ≈ 3,5 kW); im Entscheidungslog steht „Vorklimatisieren“. Ist die Klimatisierung aus, stoppt ihn der Selbststart wie heute nach 120 s. Ein voller e-Golf (EVCS „geladen“) behält die EVCS an mit dem Mindeststrom (6 A × 2 ≈ 2,8 kW), bis du absteckst; im Log „Geladen, bleibt an“. Zieht er nichts, kostet das nichts.
- **Ausrollen:** core und app zusammen mit dem nächsten gemeinsamen Pull (der core sendet neue Grund-Codes, die app kennt ihren Text; ein alter UI-Stand zeigt sonst nur den Code). Kein Schritt an `config.yaml` nötig: der Schalter ist ab Werk an. **Rückweg:** Hast du `climate_via_wallbox` in `config.yaml` eingetragen, vor einem Rollback des core entfernen (`extra="forbid"`).
- **Parallel:** Der Plan „Standard-Lademodus je Ladepunkt“ (Branch `claude/project-thread-7dml9y`) ändert nur app und UI; Überschneidung höchstens in `ui/src/lib/charging/view.ts` und `docs/betrieb.md`. Wer zuletzt nach `main` geht, rebased.

## Entscheidungen (freigegeben vom Betreiber am 28.10.2026: „Freigegeben“)

1. **Tesla: dulden, nicht selbst starten.** BindaEMS sendet während der Klimatisierung kein „Laden aus“ (weder nach dem Modus noch als Selbststart) und kein eigenes „Laden an“. Lädt der Tesla bei Klimatisierung nicht von selbst (etwa „Complete“), ändert sich nichts. (evcc startet in diesem Fall selbst mit Mindeststrom; das ist hier nicht nötig, weil der Tesla nach deiner Beobachtung selbst lädt, und es spart Start/Stopp-Befehle bei Tessie.)
2. **Klimatisierung an** heißt laut Tessie `climate_state.is_climate_on` oder `climate_state.is_preconditioning` wahr; Tessie erreichbar, Auto angesteckt, Zuordnung klar, Ladepunkt geregelt.
3. **Strom: der Mindeststrom des Ladepunkts** (`point.min_a`), nicht dringend. Den Rest der Klimaleistung nimmt der Tesla aus dem Akku, ein Überschuss darüber geht in den Akku des Autos. Bekommt der Tesla in PV mehr Überschuss, gilt der höhere Strom wie bisher (die Regel greift nur, wenn der Modus nicht laden will).
4. **e-Golf „voll“ = im Ansteckvorgang „geladen“** (EVCS-Status 3 bzw. Tessie „Complete“, `VehicleState.complete`), **nicht** ein erreichtes Ziel in „Geplant“: Dort ist der Akku nicht voll, und das Auto würde mit dem Mindeststrom weiterladen.
5. **EVCS bleibt an mit dem Mindeststrom** (`point.min_a`) bis zum Abstecken; ist sie aus (etwa nach einem Neustart des core), startet sie, wenn die Grenze des Wächters den Mindeststrom trägt (wie jeder Start, `carries`).
6. **Gilt in allen Modi außer „Aus“.** „Aus“ bleibt aus, auch bei Klimatisierung (der Stopp bleibt dringend).
7. **Schalter je Fahrzeug** in `config.yaml`: `vehicles.<Schlüssel>.climate_via_wallbox` (Standard `true`) schaltet K1 und K2 für dieses Fahrzeug ab. Gäste bekommen keine der Regeln (eine volle Gast-EVCS geht wie heute aus).
8. **Überlast und 15-min-Wächter gehen vor:** `EVCS_STOP`/`ALL_STOP` stoppen wie bisher, ab `MINIMUM` bleibt es beim Mindeststrom, und die Start-Sperre gilt für den Start nach Entscheidung 5.
9. **Ende der Klimatisierung:** Lädt der Tesla danach weiter, stoppt ihn der Selbststart nach `control.tesla_against_s` (die Zeit und der letzte Neuversuch beginnen mit dem Ende der Klimatisierung neu, damit nicht die 600 s eines Neuversuchs davor warten).
10. **Statistik:** Die Energie fürs Vorklimatisieren zählt zur Ladesession des Autos (sie geht durch die Wallbox) und gilt beim e-Golf als Last, nicht als Überschuss-Laden. Keine eigene Auswertung.
11. **Grund-Codes:** `climate` („Vorklimatisieren“) für K1, `keep_on_complete` („Geladen, bleibt an“) für K2, jeweils nach dem Modus-Code.

## Global Constraints

- Texte für den Betreiber auf Deutsch, Anrede „du“; Kommentare und Docstrings im Stil der Umgebung (deutsch).
- Namen: Signal `vehicle.<Schlüssel>.climate_on`, Feld `VehicleObs.climate_on`, Config `VehicleConfig.climate_via_wallbox`, Grund-Codes `climate` und `keep_on_complete` (Konstanten `CLIMATE`, `KEEP_ON_COMPLETE` in `core/control/charging_tesla.py` bzw. `charge_modes.py`).
- Die Safety-Schicht, `ModeMachine`, die Absicht und die app-Datenbank bleiben unverändert.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy --strict` (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators), UI `pnpm check`, `pnpm lint`, `pnpm test`. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Commits deutsch im Stil `feat(core): …`. Kein Aktor wird live geschaltet.

## Review Focus

- Tesla klimatisiert im Modus „Aus“: Der Stopp geht wie heute dringend hinaus → Test in Task 2 (`test_climate_does_not_hold_a_tesla_in_off`).
- Tessie fällt während der Klimatisierung aus (`reachable` falsch, `climate_on` veraltet): keine Regel K1, wie heute → Test in Task 2 (`test_climate_needs_a_reachable_tessie`).
- Die Klimatisierung endet kurz nach einem Selbststart-Neuversuch: Der Stopp kommt 120 s nach dem Ende, nicht erst 600 s nach dem Neuversuch → Test in Task 2 (`test_after_climate_the_self_start_begins_anew`).
- e-Golf in „Geplant“ mit erreichtem Ziel, aber nicht „geladen“: EVCS geht aus wie heute → Test in Task 3 (`test_reached_target_is_not_complete_for_keep_on`).
- Überlast `EVCS_STOP` bei vollem e-Golf: Die EVCS geht aus und bleibt in der Mindestpause aus → Test in Task 3 (`test_overload_stops_a_kept_evcs`).
- Gast an der EVCS „geladen“ und `climate_via_wallbox: false` beim e-Golf: EVCS aus wie heute → Tests in Task 3.

---

### Task 1: Klimatisierung aus Tessie und Schalter in `config.yaml`

**Files:**
- Modify: `src/bindaems/core/adapters/tessie.py` (`parse_state`)
- Modify: `src/bindaems/core/control/charge_inputs.py` (`VehicleObs`, `_vehicle_obs`)
- Modify: `src/bindaems/shared/config.py` (`VehicleConfig`)
- Modify: `deploy/config.example.yaml` (Kommentar beim Tesla und e-Golf)
- Modify: `tests/fixtures/tessie_state.json` (Abschnitt `climate_state` mit `is_climate_on: false`, `is_preconditioning: false`)
- Test: `tests/unit/core/adapters/test_tessie.py`, `tests/unit/core/control/test_charge_inputs.py`, `tests/unit/shared/test_config.py`

**Interfaces:**
- Produces: `parse_state(...)["climate_on"]: bool | None` – `True`, wenn `is_climate_on` oder `is_preconditioning` wahr ist; `False`, wenn beide `False` sind; sonst (fehlender Abschnitt, kein Wahrheitswert) `None`. Signal `vehicle.<Schlüssel>.climate_on` wie die übrigen Werte von `parse_state`.
- Produces: `VehicleObs.climate_on: bool | None = None` (letztes Feld mit Standard, damit `NO_VEHICLE_DATA` und die Helfer gleich bleiben), gefüllt in `_vehicle_obs` mit `_flag(snap, prefix + "climate_on")`.
- Produces: `VehicleConfig.climate_via_wallbox: bool = True` mit Kommentar „Vorklimatisieren über die Wallbox: Tesla beim Klimatisieren nicht stoppen, EVCS nach ‚geladen‘ an lassen (Plan Vorklimatisieren)“.

- [ ] **Step 1: Tests schreiben**

```python
def test_parse_state_climate_on_from_climate_or_preconditioning() -> None:
    for climate, pre, expected in [(True, False, True), (False, True, True), (False, False, False)]:
        data = {**STATE, "climate_state": {"is_climate_on": climate, "is_preconditioning": pre}}
        assert parse_state(data, HOME, 150.0)["climate_on"] is expected

def test_parse_state_climate_unknown_without_climate_state() -> None:
    data = {k: v for k, v in STATE.items() if k != "climate_state"}
    assert parse_state(data, HOME, 150.0)["climate_on"] is None
    data = {**STATE, "climate_state": {"is_climate_on": "yes"}}
    assert parse_state(data, HOME, 150.0)["climate_on"] is None
```

`test_charge_inputs.py`: `test_vehicle_obs_reads_climate_on` (Signal `True` mit Qualität OK → `obs.climate_on is True`; fehlt es → `None`). `test_config.py`: `test_climate_via_wallbox_defaults_on` (`VehicleConfig` ohne Schlüssel → `True`; `false` wird gelesen).

- [ ] **Step 2: Tests laufen lassen, sie scheitern** — `uv run pytest tests/unit/core/adapters/test_tessie.py tests/unit/core/control/test_charge_inputs.py tests/unit/shared/test_config.py -q`, erwartet: KeyError bzw. AttributeError.
- [ ] **Step 3: Umsetzen** wie unter Interfaces; der Fixture-Abschnitt hält bestehende Tests grün.
- [ ] **Step 4: Prüfkette grün.**
- [ ] **Step 5: Commit** `feat(core): Tessie meldet die Klimatisierung, Schalter climate_via_wallbox`

### Task 2: Regel K1 – Tesla beim Klimatisieren nicht stoppen

**Files:**
- Modify: `src/bindaems/core/control/charging_tesla.py` (Konstante `CLIMATE: Final = "climate"`, Funktion `climate_hold`, Modul-Docstring)
- Modify: `src/bindaems/core/control/charging.py` (`step`, `_Situation`, `_regulate`, Modul-Docstring)
- Modify: `src/bindaems/shared/decisions.py` (`"climate": "Vorklimatisieren"`), `ui/src/lib/charging/view.ts` (`climate: 'Vorklimatisieren'`)
- Test: `tests/unit/core/control/test_charging_tesla.py`, `ui/src/lib/charging/view.test.ts`

**Interfaces:**
- Consumes: `VehicleObs.climate_on`, `VehicleConfig.climate_via_wallbox` (Task 1).
- Produces: `climate_hold(cfg: Config, key: str, obs: VehicleObs, vehicle: VehicleState, mode: Effective, decision: ModeDecision) -> bool` in `charging_tesla.py`: wahr genau dann, wenn das Fahrzeug in `cfg.vehicles` steht, Tessie hat und `climate_via_wallbox` gilt, `obs.reachable`, `obs.climate_on is True`, `obs.charging is True`, `vehicle.plugged is True`, `vehicle.kind is not AssignmentKind.UNCLEAR`, `mode is not Effective.OFF` und `not decision.charge`.
- Produces: `_Situation.hold: str | None = None` – der Grund-Code einer Halte-Regel (`CLIMATE` hier, `KEEP_ON_COMPLETE` in Task 3), sonst `None`.

Ablauf in `step` (je geregeltem Ladepunkt, nach `self._decide`): Ist `climate_hold(...)` wahr, wird `decision = ModeDecision(True, None)`, `self._against.clear(key)` gerufen und die Situation bekommt `hold=CLIMATE`; der Schlüssel kommt in `self._climate` (neue Menge). Fällt ein Schlüssel aus `self._climate` heraus, ruft `step` einmal `self._against.clear(key)` (Entscheidung 9). `_self_start` sieht das neue Urteil (`charge` wahr) und zählt nicht.

In `_regulate` im Zweig „laden“: Mit `situation.hold` ist der Strom `point.min_a`, die Gründe `(*head, situation.hold)`, `urgent=False` (außer `minimum`, wie bisher), und `start` ist bei `CLIMATE` falsch (kein eigenes „Laden an“, Entscheidung 1). Der Zweig `minimum` bleibt vorrangig.

- [ ] **Step 1: Tests schreiben** (in `test_charging_tesla.py`, mit `tesla(plugged=True, charging=True, actual_current_a=16.0, climate_on=True)` am Wall Connector, Modus PV ohne Überschuss)
  - `test_a_climatising_tesla_is_not_stopped_by_the_self_start`: 600 s lang kein Befehl `charging=False`; es gibt einen Strombefehl 5 A (`min_a`) mit Gründen `("mode_pv", "climate")`.
  - `test_climate_sends_no_charging_on`: kein Befehl `charging=True`, auch wenn der letzte eigene Wert `False` war.
  - `test_climate_does_not_hold_a_tesla_in_off`: Modus Aus → dringendes „Laden aus“ im ersten Zyklus wie heute.
  - `test_climate_needs_a_reachable_tessie`: `reachable=False` → Selbststart-Stopp nach 120 s wie heute.
  - `test_climate_off_by_config`: `climate_via_wallbox: false` → Selbststart-Stopp nach 120 s.
  - `test_climate_without_charging_changes_nothing`: `charging=False, complete=True, climate_on=True` → keine Befehle.
  - `test_after_climate_the_self_start_begins_anew`: Selbststart-Neuversuch bei 120 s, Klimatisierung ab 130 s bis 300 s, danach lädt er weiter → „Laden aus“ mit `self_start` bei 420 s (300 + 120), nicht erst bei 720 s.
  - `test_climate_with_surplus_keeps_the_pv_current`: Will PV laden (Überschuss trägt 10 A), gilt der Strom aus dem Überschuss ohne `climate`.
  - `view.test.ts`: `reasonLabel('climate')` ist „Vorklimatisieren“.
- [ ] **Step 2: Tests laufen lassen, sie scheitern** — `uv run pytest tests/unit/core/control/test_charging_tesla.py -q -k climate`.
- [ ] **Step 3: Umsetzen** wie unter Interfaces; Docstrings von `charging.py` („Selbststart“) und `charging_tesla.py` um die Regel ergänzen.
- [ ] **Step 4: Prüfkette grün** (auch die bestehenden Selbststart-Tests).
- [ ] **Step 5: Commit** `feat(core): Tesla beim Vorklimatisieren nicht stoppen`

### Task 3: Regel K2 – EVCS bleibt nach „geladen“ an

**Files:**
- Modify: `src/bindaems/core/control/charge_modes.py` (Konstante `KEEP_ON_COMPLETE: Final = "keep_on_complete"`, Modul-Docstring)
- Modify: `src/bindaems/core/control/charging.py` (`step`, Docstring „Geladen“)
- Modify: `src/bindaems/shared/decisions.py` (`"keep_on_complete": "Geladen, bleibt an"`), `ui/src/lib/charging/view.ts`
- Test: `tests/unit/core/control/test_charging.py`, `ui/src/lib/charging/view.test.ts`

**Interfaces:**
- Consumes: `_Situation.hold` und der Zweig in `_regulate` aus Task 2; `VehicleConfig.climate_via_wallbox` (Task 1).
- Produces: `keep_on_complete(cfg: Config, key: str, point: ChargePoint, vehicle: VehicleState, mode: Effective) -> bool` in `charging.py` (modulweite Funktion): wahr genau dann, wenn `key` in `cfg.vehicles` steht und `climate_via_wallbox` gilt, `point.evcs`, `vehicle.plugged is True`, `vehicle.complete`, `vehicle.kind is not AssignmentKind.UNCLEAR` und `mode is not Effective.OFF`. Ein erreichtes Ziel in „Geplant“ (`plan.reached`) zählt nicht (Entscheidung 4).

Ablauf in `step`: Gilt `keep_on_complete(...)`, wird `decision = ModeDecision(True, None)` und `hold=KEEP_ON_COMPLETE`; `_regulate` setzt `point.min_a` mit `(*head, KEEP_ON_COMPLETE)` und darf starten (`start=may_start`, Entscheidung 5). K1 geht K2 vor, wenn beide gelten (Tesla an der EVCS). Die Teilnahme am Überschuss bleibt, wie sie ist: Ein „geladenes“ Auto nimmt nicht teil, seine Leistung ist Last (`can_charge`).

- [ ] **Step 1: Tests schreiben** (e-Golf an der EVCS, Modus PV, ohne Überschuss)
  - `test_a_complete_egolf_keeps_the_evcs_on`: lädt mit 10 A, dann `evcs(complete=True)` → kein `charging=False`, ein Strombefehl 6 A mit `("mode_pv", "keep_on_complete")`; 30 min später immer noch kein Stopp.
  - `test_a_complete_egolf_starts_a_stopped_evcs`: EVCS aus (letzter eigener Wert `False`), `complete=True`, Grenze trägt 6 A → Strom 6 A, dann `charging=True`.
  - `test_keep_on_respects_the_start_lock`: wie oben mit Start-Sperre des 15-min-Wächters → kein Start.
  - `test_reached_target_is_not_complete_for_keep_on`: „Geplant“, Ziel erreicht, `complete=False` → `charging=False` mit `target_reached` wie heute.
  - `test_keep_on_not_in_off`: Modus Aus, `complete=True` → dringendes „Laden aus“.
  - `test_overload_stops_a_kept_evcs`: Überlast `EVCS_STOP` → „Laden aus“ mit `overload`, danach in `min_off_s` kein Start.
  - `test_a_complete_guest_evcs_goes_off`: Gast an der EVCS, `complete=True` → „Laden aus“ wie heute.
  - `test_keep_on_off_by_config`: `climate_via_wallbox: false` beim e-Golf → „Laden aus“ wie heute.
  - `test_unplugging_ends_keep_on`: Abstecken → „Laden aus“ einmal, wie heute.
  - `view.test.ts`: `reasonLabel('keep_on_complete')` ist „Geladen, bleibt an“.
- [ ] **Step 2: Tests laufen lassen, sie scheitern** — `uv run pytest tests/unit/core/control/test_charging.py -q -k keep_on`.
- [ ] **Step 3: Umsetzen** wie unter Interfaces.
- [ ] **Step 4: Prüfkette grün.**
- [ ] **Step 5: Commit** `feat(core): EVCS bleibt nach „geladen“ an fürs Vorklimatisieren`

### Task 4: Simulator – zwei Szenarien

**Files:**
- Modify: `src/bindaems/tools/sim/cars.py` (`Tesla`: Feld `climate_on: bool = False`, in `state()` als `climate_state: {"is_climate_on": …, "is_preconditioning": False}`)
- Modify: `src/bindaems/tools/sim/scenarios.py` (Ereignis `_Climate(vehicle, on: bool)`, das beim Einschalten zusätzlich wie `_SelfStart` das Laden im Auto startet und beim Ausschalten nur `climate_on` zurücksetzt; Szenarien `tesla-vorklimatisieren`, `egolf-voll-bleibt-an`; Liste der Szenarien)
- Test: `tests/unit/tools/sim/test_sim_cars.py` (`climate_state` im Zustand des Tesla), `tests/unit/tools/sim/test_sim_checks.py` (eine neue Prüfung, falls nötig); die Zahl der Szenarien steigt um 2

**Interfaces:**
- Consumes: Regeln K1 und K2 (Tasks 2 und 3), Prüfungen `_stops_within`, `_idle_from`, `_evcs_off_from`.

- `tesla-vorklimatisieren`: `EVENING`, 40 min, Tesla am Wall Connector in PV ohne Überschuss, SOC 60 %. Bei 5 min Klimatisierung an (Auto lädt selbst), bei 25 min aus. Prüfungen: zwischen 5 und 25 min kein „Laden aus“ an `vehicle.tesla` und Strom höchstens 5 A ab 7 min; danach Stopp binnen `SELF_START_WITHIN_S` nach 25 min.
- `egolf-voll-bleibt-an`: `MORNING`, e-Golf an der EVCS in PV, SOC 98 %, wird „geladen“; Prüfung: ab „geladen“ bis zum Ende kein `charging=False` an der EVCS, angeforderter Strom 6 A. Neue Prüfung `_evcs_on_from(key, begin_s)` nur, wenn sich `_evcs_off_from` nicht wiederverwenden lässt.

- [ ] **Step 1:** Szenarien schreiben und laufen lassen, gegen den Stand vor Task 2 scheitern sie (auf dem Branch genügt der Nachweis mit `git stash` der core-Änderung oder die Begründung im Ledger).
- [ ] **Step 2:** `uv run python -m bindaems.tools.sim --szenario tesla-vorklimatisieren` und `--szenario egolf-voll-bleibt-an` → Rückgabe 0; alle Szenarien grün.
- [ ] **Step 3: Prüfkette grün, Commit** `test(sim): Szenarien fürs Vorklimatisieren`

Scheitert ein Szenario, liegt der Fehler zuerst in der Regelung (eigener `fix(core)`-Commit mit Test).

### Task 5: Doku

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (7.5: Absatz „**Stand Plan Vorklimatisieren**“ mit den Entscheidungen 1 bis 11; 6.5: `climate_state`; 7.10: die Grund-Codes; 16: Szenarien jetzt +2)
- Modify: `docs/betrieb.md` („Laderegelung“, „Tesla“ und „Lademodi“: was „Vorklimatisieren“ und „Geladen, bleibt an“ im Log heißen, der Schalter, der Rückweg)
- Modify: `docs/uebergabe.md` (Kurzfassung und „Offen an der Anlage“: mit dem nächsten gemeinsamen Pull, core+app, kein Schritt an `config.yaml`; bei der Abnahme einmal vorheizen und das Log ansehen)

- [ ] **Step 1:** Doku schreiben; `uv run pytest tests/docs -q` (falls Doku-Tests bestehen) grün.
- [ ] **Step 2: Commit** `docs: Vorklimatisieren über die Wallbox`

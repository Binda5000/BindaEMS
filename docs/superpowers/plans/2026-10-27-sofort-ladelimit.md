# Ladelimit für „Sofort“ – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Im Lademodus „Sofort“ lädt jedes eigene Auto und ein Gast höchstens mit einem einstellbaren Strom statt immer mit vollem Strom; einstellbar im UI (Seite „Laden“) und in Home Assistant.

**Architecture:** Die Absicht bekommt je Fahrzeug und für den Gast ein Feld `now_max_a` (ganze A je Phase, `None` = volle Leistung). Der core kappt damit den Höchststrom des Ladepunkts, solange der geltende Modus „Sofort“ ist; alle Wächter, Zeiten und Befehlslimits bleiben, wie sie sind, denn der Wunsch wird nur kleiner. Die app bekommt die Bedienungen `SetNowLimit` und `SetGuestNowLimit` (API, Änderungsprotokoll, HA-Zahlen), das UI eine Auswahl in der Fahrzeug- und der Gastkarte.

**Tech Stack:** Python 3.12, pydantic, FastAPI, aiomqtt, pytest; Svelte 5, valibot, Vitest. Keine neuen Abhängigkeiten, keine Migration.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Abschnitt 7.5 („Sofort: maximaler Strom innerhalb der Wächter“, „Verteilung“ unter „Stand Plan 2c-1“) und Abschnitt 12 (Bedienelemente „Je Fahrzeug“).

## Einordnung

- **Anlass (Betreiber, 27.10.2026):** „Ich würde beim Sofort-Laden gerne ein Ladelimit einstellen können, ich will nicht immer mit voller Leistung laden.“
- **Was sich für dich ändert:** In der Fahrzeugkarte steht bei „Sofort“ eine Auswahl „Höchstens“ mit „Volle Leistung“ und jedem Strom vom Mindest- bis zum Höchststrom des Autos, jeweils mit kW (Tesla 3 Phasen: 5 A ≈ 3,5 kW bis 16 A ≈ 11,0 kW; e-Golf 2 Phasen: 6 A ≈ 2,8 kW bis 16 A ≈ 7,4 kW). Die Gastkarte bekommt dieselbe Auswahl im Strombereich der EVCS (6 A ≈ 4,1 kW bis 16 A ≈ 11,0 kW auf 3 Phasen). In Home Assistant kommen je Auto die Zahl „Sofort-Höchststrom“ (A) und für den Gast „Gast-Sofort-Höchststrom“ dazu.
- **Ausrollen:** core und app **gemeinsam** (die Absicht hat ein neues Feld, ein alter core lehnt es ab, `extra="forbid"`), mit dem nächsten gemeinsamen Pull; keine Migration, keine ACL-Zeile (Plattform `number` ist schon freigegeben). Rückweg: vor einem Rollback des core in jedem Auto „Volle Leistung“ wählen reicht nicht, weil das Feld mit `null` gespeichert bleibt; das Betriebshandbuch bekommt dafür den Abschnitt „Rollback auf einen Stand vor dem Sofort-Limit“ (Datei `intent.json` unter `core.data_dir`: Schlüssel `now_max_a` entfernen), wie bei 4b und 5b.
- **Parallel:** Plan 5c (Branch `claude/project-thread-x8izsc`) und „Abfahrt als Uhrzeit“ (`claude/project-thread-4xp3qu`) ändern `app/ha/entities.py`, `app/ha/commands.py` und `VehicleCard.svelte` an anderen Stellen. Neue Entität ist `number`, keine neue Plattform, keine Änderung an `allowed_topic` oder der ACL. Wer zuletzt nach `main` geht, rebased.

## Entscheidungen (freigegeben vom Betreiber am 27.10.2026 mit Änderung 5: „Limit bitte auch für Gäste, ansonsten passt alles“)

1. **Einheit: Ampere je Phase** in der Absicht, ganze Zahl im Bereich `min_a`–`max_a` des Fahrzeugs aus `config.yaml`. Die Ladepunkte schalten nur ganze A; ein kW-Wert müsste ohnehin auf A gerundet werden, und dieselben kW sind beim Tesla (3 Phasen) und e-Golf (2 Phasen) verschiedene A. Das UI zeigt zu jedem Wert die kW (`A × grid.voltage_nominal_v × Phasen`), Home Assistant den Strom in A.
2. **Je Auto (und einmal für Gäste) und dauerhaft**, wie Ziel-SOC und Abfahrt: Das Limit bleibt stehen über Abstecken, Neustart und Moduswechsel, bis du es änderst. Standard ist „Volle Leistung“ (`None`). Kein Schlüssel in `config.yaml`.
3. **Nur „Sofort“.** Boost bleibt volle Leistung (so kommst du ohne Umstellen schnell auf voll), „Geplant“ bleibt unverändert (die späteste Startzeit rechnet mit voller Leistung), PV-Überschuss und Min+PV sind ohnehin vom Überschuss begrenzt.
4. **Harte Grenze auch bei Sonne:** Das Auto in „Sofort“ lädt nie über dem Limit, auch wenn mehr PV-Überschuss da wäre. Der Rest geht wie bisher an PV-Autos, Klimaanlage, Akku oder ins Netz. (Alternative, nicht in diesem Plan: „Sofort mit Limit, darüber PV“, das wäre Min+PV mit eigenem Mindeststrom.)
5. **Limit auch für Gäste** (Änderung des Betreibers vom 27.10.2026: „Limit bitte auch für Gäste“): ein Wert `guest.now_max_a` für jeden Gast, dauerhaft wie bei den Autos. Gültig ist der Strombereich der EVCS (kleinstes `min_a` bis größtes `max_a` aller EVCS; heute 6–16 A), das UI rechnet die kW mit den Phasen der EVCS. Wirken kann es nur an der EVCS: Am Wall Connector hat ein Gast keinen Aktor und bleibt Last wie bisher.
6. **Wer das Limit wählt, der Wert gilt sofort:** Ändert sich das Limit während des Ladens, sinkt bzw. steigt der Strom im nächsten Regelzyklus (beim Tesla über die üblichen Befehlslimits von Tessie, `current_interval_s` und `per_hour`).
7. **„Volle Leistung“ ist `None`:** Wer den Höchststrom des Autos (beim Gast: das größte `max_a` der EVCS) wählt (UI oder HA), speichert die app `None`; so folgt das Auto einem später höheren `max_a` in `config.yaml`.
8. **Grund im Entscheidungslog:** Kappt das Limit den Strom, trägt der Befehl nach `mode_now` den Grund `now_limit` („Sofort-Limit“) statt keinem; kappt ein Wächter darunter, steht wie bisher dessen Grund.

## Global Constraints

- Texte für den Betreiber auf Deutsch, Anrede „du“; Code-Kommentare und Docstrings im Stil der Umgebung (deutsch).
- Feldname `now_max_a` überall (Absicht, API, UI-Schema); Grund-Code `now_limit`, Text „Sofort-Limit“.
- HA: `object_id` `<oid>_now_max_a`, Name „Sofort-Höchststrom“, und `guest_now_max_a`, Name „Gast-Sofort-Höchststrom“; Plattform `number`, Einheit `A`, Schritt 1.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy --strict` (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators), UI `pnpm check`, `pnpm lint`, `pnpm test`. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Commits deutsch im Stil `feat(core): …`. Kein Aktor wird live geschaltet.

## Review Focus

- Limit unter dem Mindeststrom des **Ladepunkts** (Tesla mit 5 A an der EVCS, deren `min_a` 6 ist): Der Strom darf nie unter `point.min_a` fallen → Test in Task 2 (`test_now_limit_never_below_the_point_minimum`).
- Boost auf einem Auto mit Limit: volle Leistung, nach dem Boost wieder das Limit → Test in Task 2 (`test_boost_ignores_the_now_limit`).
- PV-Überschuss größer als das Limit: Das Auto bleibt am Limit, ein zweites PV-Auto bekommt den Rest, die Klimaanlage sieht den Rest als ungenutzt (`unmet_surplus_w`) → Tests in Task 2 und Szenario in Task 3.
- Absicht aus einer Zeit vor dem Plan (ohne `now_max_a`) und ein später kleineres `max_a` in `config.yaml`: lädt ohne Fehler, der core klemmt auf den Ladepunkt → Tests in Task 1 und Task 2.
- HA sendet `10.0` oder `"16"` (Höchststrom): `10.0` wird 10 A, der Höchststrom wird „Volle Leistung“ (`None`), `4` beim Tesla wird abgelehnt → Test in Task 5.
- Gast am Wall Connector mit Limit: kein Befehl, keine Ausnahme, Last wie bisher; Gast an der EVCS mit 8 A → 8 A → Tests in Task 2.

---

### Task 1: Feld `now_max_a` in der Absicht

**Files:**
- Modify: `src/bindaems/shared/control.py` (`VehicleIntent` ~136–160, `GuestIntent` ~198)
- Test: `tests/unit/shared/test_control.py`

**Interfaces:**
- Produces: `VehicleIntent.now_max_a: Annotated[int, Field(ge=1, le=32)] | None = None` (Docstring: „Höchststrom je Phase in „Sofort“ (ganze A); `None` heißt volle Leistung. Der core klemmt auf den Strombereich des Ladepunkts.“)
- Produces: `GuestIntent.now_max_a` mit demselben Typ und Standard (Docstring entsprechend für einen Gast).

- [ ] **Step 1: Tests schreiben**

```python
def test_vehicle_intent_now_max_a_defaults_to_full_power() -> None:
    assert VehicleIntent().now_max_a is None
    assert VehicleIntent.model_validate({"mode": "now"}).now_max_a is None  # alte Absicht

@pytest.mark.parametrize("value", [0, 33, 10.5, "x"])
def test_vehicle_intent_now_max_a_rejects_outside_1_to_32(value) -> None:
    with pytest.raises(ValidationError):
        VehicleIntent.model_validate({"now_max_a": value})

def test_guest_intent_now_max_a_like_a_vehicle() -> None:
    assert GuestIntent().now_max_a is None
    assert GuestIntent(mode=ChargeMode.NOW, now_max_a=8).now_max_a == 8
    with pytest.raises(ValidationError):
        GuestIntent.model_validate({"now_max_a": 0})

def test_vehicle_intent_now_max_a_round_trips() -> None:
    entry = VehicleIntent(mode=ChargeMode.NOW, now_max_a=10)
    assert VehicleIntent.model_validate(entry.model_dump(mode="json")) == entry
```

- [ ] **Step 2: Laufen lassen, scheitert** — `pytest tests/unit/shared/test_control.py -q -k now_max_a` → FAIL (Feld fehlt bzw. `extra` verboten)
- [ ] **Step 3: Feld ergänzen** (strikt wie `target_soc_pct`, also keine 10,5 A)
- [ ] **Step 4: Laufen lassen, besteht**; dazu `pytest tests/unit/core/control/test_intent.py tests/unit/app/control -q` (gespeicherte Absichten laden weiter)
- [ ] **Step 5: Commit** `feat(shared): Höchststrom für „Sofort“ in der Absicht je Fahrzeug und für Gäste`

### Task 2: core kappt „Sofort“ auf das Limit

**Files:**
- Modify: `src/bindaems/core/control/charge_points.py` (neue Funktion `now_capped`)
- Modify: `src/bindaems/core/control/charging.py` (`step` ~533, `_Situation`, `_why` ~1582)
- Modify: `src/bindaems/core/control/charge_modes.py` (Konstante `NOW_LIMIT = "now_limit"`)
- Modify: `src/bindaems/shared/decisions.py` (Text `"now_limit": "Sofort-Limit"`)
- Test: `tests/unit/core/control/test_charge_points.py`, `tests/unit/core/control/test_charging.py`, `tests/unit/shared/test_decisions.py`

**Interfaces:**
- Consumes: `VehicleIntent.now_max_a` (Task 1)
- Produces: `now_capped(point: ChargePoint, max_a: int | None) -> ChargePoint` — ohne Limit derselbe Punkt, sonst `replace(point, max_a=min(point.max_a, max(max_a, point.min_a)))`.
- Produces: Grund-Code `NOW_LIMIT` in `charge_modes.py`.

Vorgehen in `step`: Direkt nach `points = charge_points(...)` wird jeder Ladepunkt, dessen Fahrzeug den geltenden Modus `Effective.NOW` hat (also nicht Boost), durch `now_capped(point, limit)` ersetzt; `limit` ist für einen Gast (`guest_wallbox(key) is not None`) `intent.guest.now_max_a`, sonst `intent.vehicles[key].now_max_a` (ohne Absicht oder Eintrag kein Limit). Eine kleine Hilfsfunktion `_now_limit(key, intent) -> int | None` neben `_mode` hält das an einer Stelle. Alles danach (`_allocate`, `wanted_a`, `SurplusCar.max_a`, `_hold`, `_unmet`, Tesla-Fenster) liest so den gekappten Höchststrom; `reserved_a` betrifft nur ungeregelte Punkte und bleibt, weil das Limit nur für geregelte Punkte gesetzt wird (der Ersatz läuft nur für `point.regulated`). `_Situation` bekommt `capped: bool` (Limit aktiv und kleiner als der ungekappte `max_a`); `_why` gibt im Modus `NOW` mit `capped` und `limit >= wanted` den Grund `(NOW_LIMIT,)` zurück, sonst wie bisher.

- [ ] **Step 1: Tests schreiben**

`test_charge_points.py`:
```python
def test_now_capped_lowers_max_a_but_never_below_min_a() -> None:
    point = make_point(min_a=6, max_a=16)
    assert now_capped(point, None) is point
    assert now_capped(point, 10).max_a == 10
    assert now_capped(point, 5).max_a == 6        # Mindeststrom der EVCS geht vor
    assert now_capped(point, 20).max_a == 16      # nie über den Ladepunkt
```

`test_charging.py` (mit den Helfern aus `charge_helpers.py`, e-Golf an der EVCS, Tesla am TWC):
```python
def test_now_limit_caps_the_current_and_names_the_reason() -> None:
    # e-Golf „Sofort“, now_max_a=10, viel Netzreserve → EVCS-Strom 10.0 A, Gründe (..., "mode_now", "now_limit")

def test_now_limit_never_below_the_point_minimum() -> None:
    # Tesla an der EVCS (min_a 6), now_max_a=5 → 6.0 A

def test_boost_ignores_the_now_limit() -> None:
    # e-Golf „Sofort“, now_max_a=8, boost_until in 1 h → 16.0 A; nach Ablauf → 8.0 A

def test_now_limit_leaves_the_rest_of_the_surplus_to_a_pv_car() -> None:
    # 9 kW Überschuss, e-Golf „Sofort“ 8 A (2 Phasen ≈ 3,7 kW), Tesla PV → Tesla bekommt den Rest
    # (floor((9000 − 8·230·2) / (230·3)) A), e-Golf 8 A

def test_now_limit_lowers_unmet_surplus_like_a_full_car() -> None:
    # e-Golf „Sofort“ 8 A lädt mit 8 A → unmet_surplus_w() == 0 statt (16−8)·230·2

def test_now_limit_change_while_charging_lowers_the_current_next_cycle() -> None:
    # 16 A ladend, neue Absicht now_max_a=10 → nächster Zyklus 10.0 A, nicht dringend

def test_guard_below_the_now_limit_keeps_its_own_reason() -> None:
    # now_max_a=12, Phasenwächter erlaubt 9 A → 9.0 A mit Grund des Wächters, ohne now_limit

def test_now_limit_from_a_stale_intent_above_config_max_is_clamped() -> None:
    # now_max_a=32 bei max_a 16 → 16.0 A, kein now_limit

def test_guest_now_limit_caps_the_guest_at_the_evcs() -> None:
    # Gast an der EVCS, guest.mode=now, guest.now_max_a=8 → 8.0 A mit now_limit

def test_guest_now_limit_does_not_touch_a_vehicle() -> None:
    # guest.now_max_a=8, e-Golf „Sofort“ ohne eigenes Limit → 16.0 A

def test_guest_now_limit_at_the_wall_connector_sends_nothing() -> None:
    # Gast am TWC (kein Aktor) mit Limit → keine Befehle, reserved_a unverändert
```

`test_decisions.py`: Text des Grundes `now_limit` ist „Sofort-Limit“ (Tabelle `_REASON_TEXT`, über die öffentliche Textfunktion des Moduls prüfen).

- [ ] **Step 2: Laufen lassen, scheitert** — `pytest tests/unit/core/control/test_charge_points.py tests/unit/core/control/test_charging.py tests/unit/shared/test_decisions.py -q -k "now_limit or now_capped"` → FAIL
- [ ] **Step 3: Umsetzen** wie oben; Modul-Docstring von `charging.py` (Ablauf) und Docstring von `wanted_a` („Sofort und Boost `max_a`“ → „… `max_a`, in Sofort mit Limit gekappt“) nachziehen.
- [ ] **Step 4: Laufen lassen, besteht**; dann `pytest tests/unit/core -q` und Abdeckung `control` ≥ 90 %.
- [ ] **Step 5: Commit** `feat(core): „Sofort“ lädt höchstens mit dem Limit des Fahrzeugs oder Gastes`

### Task 3: Simulator-Szenario „Sofort mit Limit“

**Files:**
- Modify: `src/bindaems/tools/sim/scenarios.py` (`_intent` um `limits: Mapping[str, int] | None = None`, neues Szenario `_now_limit`, Eintrag in `SCENARIOS`)
- Test: `tests/integration/test_sim_scenarios.py` (läuft über `SCENARIOS`), ggf. Liste in `tests/unit/test_docs.py`

**Interfaces:**
- Consumes: `VehicleIntent.now_max_a`
- Produces: Szenario `name="sofort-limit"`, Titel „Sofort mit Ladelimit“

Sommer, 1 h, e-Golf an der EVCS in „Sofort“ mit `now_max_a=8`, Tesla am TWC in PV, Wetter wie die übrigen Sommer-Szenarien. Der Gast kommt in diesem Szenario nicht vor (Tests in Task 2 decken ihn). Checks (mit den vorhandenen `Check`-Bausteinen):
- „EVCS nie über 8 A angefordert.“ (jeder Strombefehl an die EVCS ≤ 8 A)
- „Der Tesla lädt aus dem Überschuss.“ (Tesla lädt in mindestens der Hälfte der Zeit)
- die üblichen Sicherheits-Checks des Szenarios (keine Überlast, kein Alarm).

- [ ] **Step 1: Szenario schreiben**, **Step 2:** `pytest tests/integration/test_sim_scenarios.py -q -k sofort-limit` laufen lassen. Scheitert ein Check, liegt der Fehler zuerst in der Regelung (eigener `fix(core)`-Commit mit Test in Task 2s Datei); das Szenario nur ändern, wenn es physikalisch nicht zu schaffen ist.
- [ ] **Step 3: Commit** `test(sim): Szenario „Sofort mit Ladelimit“`

### Task 4: Bedienung in der app (API und Änderungsprotokoll)

**Files:**
- Modify: `src/bindaems/app/control/intent.py` (`SetNowLimit`, `Change`, `derive`, Protokolltext ~376, `IntentService._check`)
- Modify: `src/bindaems/app/control/routes.py` (`NowLimitBody`, `PUT /api/control/vehicles/{key}/now-limit`)
- Modify: `ui/src/lib/api/contract/control.json` (Absicht mit `now_max_a`), falls der Vertragstest die Antwort von `/api/control` prüft
- Test: `tests/unit/app/control/test_intent.py`, `tests/unit/app/control/test_routes.py`

**Interfaces:**
- Produces: `@dataclass(frozen=True) class SetNowLimit: vehicle: str; max_a: int | None` und `@dataclass(frozen=True) class SetGuestNowLimit: max_a: int | None` (beide in `Change` aufgenommen).
- Produces: `guest_range(cfg: Config) -> tuple[int, int] | None` in `intent.py`: kleinstes `min_a` und größtes `max_a` aller EVCS; `None` ohne EVCS (dann lehnt `_check` jeden Wert außer `None` ab: „Ohne EVCS gibt es kein Limit für Gäste.“).
- Produces: `PUT /api/control/vehicles/{key}/now-limit` und `PUT /api/control/guest/now-limit`, Body `{"max_a": int | null}` (`strict=True`, `extra="forbid"`), Rolle „Bedienen“, Antwort wie die anderen Bedienungen.
- `derive`: setzt `vehicles[key].now_max_a` bzw. `guest.now_max_a`; `max_a >=` Höchststrom (Fahrzeug: `cfg.vehicles[key].max_a`, Gast: oberes Ende von `guest_range`) wird `None` (Entscheidung 7). Der Gastmodus bleibt dabei unverändert, und `SetGuestMode` lässt `now_max_a` stehen.
- `_check`: unbekanntes Fahrzeug 404 vor Bereich; außerhalb `min_a`–`max_a` → `Rejected(["Sofort-Höchststrom „{n} A“ liegt außerhalb von {min}–{max} A."])`.
- Protokoll: Ziel `<Fahrzeugname>` bzw. `Gast` (wie bei `SetGuestMode`), Text `Sofort-Höchststrom 10 A` bzw. `Sofort-Höchststrom volle Leistung`.

- [ ] **Step 1: Tests schreiben**

```python
def test_set_now_limit_stores_whole_amps() -> ...      # derive(..., SetNowLimit("egolf", 10)) → now_max_a == 10, mode bleibt
def test_set_now_limit_at_vehicle_max_is_full_power() -> ...  # 16 → None
def test_set_now_limit_none_clears() -> ...
def test_set_now_limit_outside_vehicle_range_is_rejected() -> ...  # tesla 4 und 17 → Rejected mit „außerhalb von 5–16 A“
def test_set_now_limit_unknown_vehicle_is_404() -> ...
def test_now_limit_route_requires_operator_and_rejects_floats() -> ...  # viewer 403, {"max_a": 10.5} 422
def test_now_limit_is_audited() -> ...                 # Protokolleintrag „Sofort-Höchststrom 10 A“
def test_set_guest_now_limit_within_evcs_range() -> ...  # 8 → guest.now_max_a 8, Modus bleibt; 16 → None; 5 und 17 → Rejected „außerhalb von 6–16 A“
def test_set_guest_mode_keeps_the_guest_limit() -> ...
def test_guest_now_limit_route() -> ...                  # Operator 200, viewer 403
```

- [ ] **Step 2: scheitert**, **Step 3: umsetzen**, **Step 4:** `pytest tests/unit/app/control tests/integration -q` besteht
- [ ] **Step 5: Commit** `feat(app): Sofort-Höchststrom je Fahrzeug und für Gäste bedienen`

### Task 5: Home Assistant „Sofort-Höchststrom“

**Files:**
- Modify: `src/bindaems/app/ha/entities.py` (`build_entities`, neben `{oid}_target_soc`; Zustandsfunktion `_now_max_state`)
- Modify: `src/bindaems/app/ha/commands.py` (`_table`: `{oid}_now_max_a`)
- Test: `tests/unit/app/ha/test_entities.py`, `tests/unit/app/ha/test_commands.py`, `tests/integration/test_ha_round_trip.py`

**Interfaces:**
- Consumes: `SetNowLimit` (Task 4)
- Produces: `EntitySpec(f"{oid}_now_max_a", "number", "Sofort-Höchststrom", device, _now_max_state(cfg, key), unit="A", command=True, min=float(car.min_a), max=float(car.max_a), step=1.0)`. Zustand: `now_max_a` der Absicht, ohne Wert `car.max_a`; ohne Absicht `unknown`.
- Befehl: ganze Zahl über `_whole_number` (nimmt `10` und `10.0`); außerhalb → `Refused("Sofort-Höchststrom „…“ liegt außerhalb von 5–16 A.")`; sonst `SetNowLimit(key, n)` (die Normalisierung auf `None` macht Task 4).
- Gast: `EntitySpec("guest_now_max_a", "number", "Gast-Sofort-Höchststrom", …)` auf dem Gerät des Gast-Lademodus, Bereich aus `guest_range`, Zustand `guest.now_max_a` oder das obere Ende; nur wenn `guest_range` nicht `None` ist. Befehl → `SetGuestNowLimit(n)`.

- [ ] **Step 1: Tests schreiben** (Discovery: Plattform `number`, `min`/`max`/`step`, `unit_of_measurement: "A"`; Zustand `"10"` bzw. `"16"` ohne Limit; Befehle `"10.0"` → `SetNowLimit("egolf", 10)`, `"4"` beim Tesla → `Refused`, `"abc"` → ignoriert; Rundreise `published="16"`, `shown="10"`; dasselbe für `guest_now_max_a` mit 6–16 A und `SetGuestNowLimit(8)`)
- [ ] **Step 2: scheitert**, **Step 3: umsetzen**, **Step 4:** `pytest tests/unit/app/ha tests/integration/test_ha_round_trip.py -q` besteht
- [ ] **Step 5: Commit** `feat(ha): Sofort-Höchststrom je Fahrzeug und für Gäste als Zahl`

### Task 6: UI – Auswahl „Höchstens“ in Fahrzeug- und Gastkarte

**Files:**
- Modify: `ui/src/lib/api/schemas.ts` (`VehicleIntentSchema.now_max_a` und im Gast-Schema `now_max_a`, je `v.optional(v.nullable(v.number()))`)
- Modify: `ui/src/lib/api/endpoints.ts` (`setNowLimit(vehicle: string, maxA: number | null)`, `setGuestNowLimit(maxA: number | null)`)
- Modify: `ui/src/lib/charging/GuestCard.svelte` (dieselbe Auswahl bei Gastmodus `now`, Bereich und Phasen der EVCS aus `limits.wallboxes`; ohne EVCS keine Auswahl)
- Modify: `ui/src/lib/charging/view.ts` (`nowLimitOptions`, `nowLimitText`, Grundtext `now_limit: 'Sofort-Limit'`)
- Modify: `ui/src/lib/charging/VehicleCard.svelte` (Auswahl bei `mode === 'now'`), Seite `ui/src/routes/laden/+page.svelte` (Prop `onNowLimit`), Test `ui/src/routes/laden/page.test.ts`
- Test: `ui/src/lib/charging/view.test.ts`, `ui/src/lib/charging/VehicleCard.test.ts`, Test der Gastkarte (neu `GuestCard.test.ts`, falls es keinen gibt)

**Interfaces:**
- Produces: `nowLimitOptions(vehicle: {min_a: number; max_a: number; phases: number}, voltage: number): {value: number | null; label: string}[]` — erste Option `{value: null, label: 'Volle Leistung (16 A · 11,0 kW)'}`, dann absteigend `max_a − 1` bis `min_a` als `'10 A · 6,9 kW'` (kW mit einer Nachkommastelle, deutsches Komma).
- Produces: `nowLimitText(maxA: number | null, vehicle, voltage): string | null` — `null` ohne Limit, sonst `'Sofort mit höchstens 10 A (6,9 kW)'`; steht in der Karte auch für „Ansehen“, wenn der Modus „Sofort“ ist.
- Die Auswahl sendet beim Ändern sofort (wie die Lademodus-Auswahl), `busy` sperrt sie währenddessen; Fehler erscheinen wie bei Ziel und Abfahrt.

- [ ] **Step 1: Tests schreiben** (Optionen für Tesla 3 Phasen 230 V: 12 Einträge, erster „Volle Leistung (16 A · 11,0 kW)“, letzter „5 A · 3,5 kW“; e-Golf 2 Phasen: „6 A · 2,8 kW“; Karte zeigt die Auswahl nur bei `now` und nur ab „Bedienen“; Wahl „10 A · 6,9 kW“ ruft `onNowLimit(10)`, „Volle Leistung“ `onNowLimit(null)`; alte Antwort ohne `now_max_a` gilt als volle Leistung; Gastkarte bei EVCS 6–16 A, 3 Phasen: erster Eintrag „Volle Leistung (16 A · 11,0 kW)“, letzter „6 A · 4,1 kW“, Wahl ruft `onGuestNowLimit`)
- [ ] **Step 2: scheitert**, **Step 3: umsetzen**, **Step 4:** `pnpm check && pnpm lint && pnpm test` grün; E2E (`pnpm e2e`, Seite „Laden“) grün
- [ ] **Step 5: Commit** `feat(ui): Sofort-Höchststrom in Fahrzeug- und Gastkarte`

### Task 7: Doku

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (7.5: Absatz „**Stand Plan Sofort-Limit** (`docs/superpowers/plans/2026-10-27-sofort-ladelimit.md`)“ mit den Entscheidungen 1–8; 12: Zeilen `<Fahrzeug>_now_max_a` und `guest_now_max_a`)
- Modify: `docs/betrieb.md` (Bedienung „Laden“, HA-Entitäten, Rollback-Abschnitt aus der Einordnung)
- Modify: `docs/uebergabe.md` (Stand, Ausrollen core+app gemeinsam)
- Test: `tests/unit/test_docs.py` (falls Szenario- oder Entitätslisten geprüft werden)

- [ ] **Step 1:** Texte schreiben, **Step 2:** `pytest tests/unit/test_docs.py -q` grün, **Step 3: Commit** `docs: Sofort-Limit in Spec, Betriebshandbuch und Übergabe`

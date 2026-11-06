# EVCS-Selbststart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Geht die EVCS an, obwohl der Modus des Autos „nicht laden“ will (Autostart nach dem Anstecken, VictronConnect, VRM, Neustart der EVCS), schickt die Regelung „Laden aus“ erneut. Heute bleibt sie stumm, und der e-Golf lädt stundenlang aus dem Netz (05.11.2026, 16:19 bis ~20:00, 3,19 kW im Modus PV-Überschuss ohne PV).

**Architecture:** Nur core, Laderegelung. `ChargeController._self_start` (heute nur für Fahrzeuge mit Tessie) bekommt einen zweiten Zweig für jedes Auto an einer EVCS (Fahrzeug oder Gast): Meldet das Rücklesen der EVCS „Laden an“ (`inputs.present[(Aktor, CHARGING)] is True`), während das Urteil „nicht laden“ ist, ohne Unterbrechung `control.evcs_against_s`, vergisst die Regelung ihren letzten Wert für `charging` der EVCS. So geht „Laden aus“ erneut hinaus, mit dem Grund `self_start`, höchstens alle `control.evcs_against_retry_s` je Auto. Die Zeiten führt eine zweite Instanz von `AgainstMode`.

**Tech Stack:** Python 3.12, pydantic, pytest, Simulator (`bindaems.tools.sim`).

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Abschnitt 7.5 „Selbststart“ (Zeile ~855) und die Regeln K1/K2 (Zeile ~900).

## Befund

- Grafana 05.11.: Die EVCS lädt 12:15 bis 14:48 aus PV, BindaEMS schickt um 14:48 „Laden aus“ (bestätigt). Um 16:19 geht sie wieder an, gleichmäßig mit 3,19 kW (7 A auf zwei Phasen, der letzte Sollstrom von BindaEMS), bis ~20:00. Das Entscheidungslog zeigt ab 14:48 keinen EVCS-Befehl mehr, um 16:19 nur noch Victron („Akku nicht ins Auto“, Netz-Sollwert folgt der Autolast, richtig). Kurz vor 16:19 kamen drei SOC-Werte des e-Golf schnell hintereinander, das Auto ist also aufgewacht.
- Code: Die Regelung schickt nur Änderungen gegenüber ihrem letzten eigenen Wert (`core/control/last_values.py`). `_switch_off` sieht „aus“ = letzter Wert und schickt nichts. Der Neuversuch „Laden aus“ gegen den Modus (`ChargeController._self_start`, `charging.py:1452`) prüft zuerst `key in self._tesla and tessie is not None`, gilt also nie für die EVCS. Kein Simulator-Szenario prüft eine EVCS, die von selbst angeht.
- **Auslöser (Binda, 05.11. 20:52 und 20:55):** Der e-Golf war um ~16:19 kurz abgesteckt; nach dem Wiederanstecken begann er zu laden. In VictronConnect war bei der EVCS **„Autostart“ an**; Binda hat ihn am 05.11. um 20:55 ausgeschaltet (fester Stand der Anlage: Modus manuell, Autostart aus). Die drei schnellen SOC-Werte kurz vor 16:19 passen dazu (Anstecken weckt den e-Golf).
- **Ablauf, der zum Code passt:** Der neue Ansteckvorgang lässt die Regelung den letzten Wert der EVCS vergessen (`_decide`, `charging.py:1345`); im Modus PV ohne Überschuss fragt sie „Laden aus“ erneut an. In diesem Zyklus meldet das Rücklesen noch 0, die Safety-Schicht antwortet `UNCHANGED`, und das ergibt keine Entscheidung (`executor.py:20`), darum steht im Log nichts. Sekunden später setzt Autostart die EVCS auf „an“; ab da ist „aus“ wieder der letzte eigene Wert, und die Regelung schickt nichts mehr.
- **Warum der Fix trotz „Autostart aus“:** Ein Start aus VictronConnect, VRM oder Home Assistant, ein Neustart der EVCS oder ein versehentlich wieder eingeschalteter Autostart führen zum selben stundenlangen Netzladen. Der Fix ist das Sicherheitsnetz dafür.

## Entscheidungen

1. **Signal ist das Rücklesen „Laden an“ der EVCS** (`EvcsActuator.present`: `start_stop` aus dem Modbus-Abbild, ersatzweise `gx_start_stop`), nicht der Ladestatus und nicht die Leistung. Begründung: „Laden aus“ erneut zu senden, hilft nur, wenn der Schalter an ist. Steht er auf 0 und die EVCS lädt trotzdem, wäre ein Neuversuch wirkungslos (die Safety-Schicht antwortet `UNCHANGED`). Das wäre ein anderer Fehler mit einem eigenen Plan. **Vor dem Ausrollen** (nicht vor der Umsetzung) prüft Binda in Grafana/Influx, dass `wallbox.evcs.start_stop` am 05.11. um ~16:19 auf 1 sprang (Abfrage im Abschnitt „Prüfung vor dem Ausrollen“). Erwartet ist, dass Autostart und der Start-Knopf dasselbe Register 5010 setzen, das wir lesen; belegt ist das nicht, darum die Prüfung an der Anlage.
2. **Zeiten:** `control.evcs_against_s` = 60 s, `control.evcs_against_retry_s` = 300 s. Die EVCS wird alle paar Sekunden gelesen (kein 60-s-Takt wie Tessie) und hat kein Wecken und kein Befehlsbudget wie der Tesla. 60 s liegen weit über der Bestätigungsfrist eines eigenen „Laden aus“ (10 s), sodass ein eigener Stopp nie als Selbststart zählt. Neue Schlüssel, weil `tesla_against_*` nach Namen und Doku nur den Tesla meint.
3. **Für wen:** jedes Auto an einer geregelten EVCS, auch der Gast und der Tesla an der EVCS (`EvcsOverrides`). Für den Tesla am Wall Connector bleibt der bestehende Zweig über Tessie unverändert. Steht ein Tesla an der EVCS, greift nur der EVCS-Zweig. Er ist schneller, und das Signal ist dasselbe Gerät, das gestoppt wird.
4. **Bedingungen wie beim Tesla:** Das Urteil will nicht laden (`decision.charge` falsch, also auch nicht unter K1/K2, die das Urteil auf „laden“ setzen), der Ansteckzustand ist bekannt, die Zuordnung ist nicht unklar, der Aktor ist steuerbar, und `charging` ist nicht blockiert (sonst läuft die Zeit weiter, wie beim Tesla). Abgesteckt (`plugged is False`) zählt nicht: Ein neuer Ansteckvorgang vergisst die Werte ohnehin, und das erste „Laden aus“ kommt dann nach den Regeln des Modus.
5. **Dringend:** nur im Modus „Aus“, wie jeder Stopp dort (unverändert `_regulate`). Ein Start/Stopp-Budget gibt es an der EVCS nicht: Stundenlimits gelten nur für Tesla-Aktoren, an der EVCS gilt nur der Mindestabstand zwischen Befehlen.
6. **Zeiten neu:** bei einem neuen Ansteckvorgang, beim gemerkten Vorgang, bei `idle`, in einem ungeregelten Zyklus und während K1/K2 halten (an denselben Stellen wie `self._against`).
7. **Grund im Log:** derselbe Code `self_start` („Selbststart“), also keine neue Übersetzung in `shared/decisions.py` und `ui/src/lib/charging/view.ts`. Im Log steht dann etwa „EVCS „evcs“ (PV-Überschuss, Überschuss, Selbststart): Laden aus“.
8. **Ausrollen:** core (und app wegen `extra=forbid` der Konfiguration, die app liest `config.yaml` mit), mit Stufe 2 des Sammel-Pulls (ab 23.11.), nie allein. Ohne neue Schlüssel in `config.yaml` gelten die Standardwerte.

## Prüfung vor dem Ausrollen (Binda)

Grafana → Explore → InfluxDB, Zeitraum 05.11. 14:00 bis 20:30:

```sql
SELECT last("value_num") FROM "status" WHERE "id" =~ /^wallbox\..+\.(start_stop|gx_start_stop|mode|gx_mode|status)$/ AND $timeFilter GROUP BY time(1m), "id" fill(previous)
```

Erwartet: `start_stop` und `gx_start_stop` springen um ~16:19 von 0 auf 1, `mode` bleibt 0 (manuell). Bleibt `start_stop` auf 0, gilt Entscheidung 1 nicht: Dann wird der Fix nicht ausgerollt, und ein eigener Plan klärt, wie die EVCS trotz „aus“ laden konnte.

## Review Focus

- Wiederanstecken mit Autostart (der Fall vom 05.11.): Neuer Ansteckvorgang, eigenes „Laden aus“ trifft auf Rücklesen 0 (`UNCHANGED`), danach springt das Rücklesen auf 1. Die Zeit beginnt mit dem neuen Vorgang und läuft ab dem Sprung; nach 60 s geht „Laden aus“ hinaus (Unit-Test und Szenario).
- Eigenes „Laden aus“ unterwegs: Das Rücklesen zeigt noch 1, bis die EVCS es übernimmt (bis 10 s). Das darf keinen Neuversuch auslösen und keinen Zähler vorziehen (Test: 30 s Rücklesen 1 nach eigenem Stopp → kein zweiter Stopp).
- K2 „geladen, bleibt an“: Die EVCS ist absichtlich an, das Rücklesen ist 1. Es darf kein Selbststart folgen (Test).
- Modus wechselt während der Wartezeit auf „Sofort“: Das Urteil will laden, die Zeit beginnt neu, und es geht kein Stopp hinaus (Test).
- Unbekanntes Rücklesen (`present` ohne `CHARGING`, Modbus und GX ohne Wert): zählt als „nicht an“, unterbricht die Zeit (Test).

---

### Task 1: Einstellungen `evcs_against_s` und `evcs_against_retry_s`

**Files:**
- Modify: `src/bindaems/shared/config.py` (`ControlConfig`, Docstring-Abschnitt „Selbststart“)
- Modify: `deploy/config.example.yaml` (Abschnitt `control`, nach `tesla_against_retry_s` bzw. bei den Tesla-Werten; Kommentar je Zeile wie dort)
- Modify: `docs/betrieb.md` (Tabelle der Einstellungen „Akku und Spitzen“, nach `control.tesla_against_retry_s`)
- Test: `tests/unit/shared/test_config.py`, `tests/unit/test_docs.py` (`BATTERY_SETTINGS`)

**Interfaces:**
- Produces: `ControlConfig.evcs_against_s: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 60.0`, `ControlConfig.evcs_against_retry_s: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 300.0`

- [ ] **Step 1: Failing tests.** In `test_config.py` die Liste der Felder (Zeile ~430) um beide Schlüssel ergänzen; neuer Test `test_evcs_self_start_defaults`: `(control.evcs_against_s, control.evcs_against_retry_s) == (60.0, 300.0)`; den bestehenden Parametrize-Test für `tesla_against_*` (Zeile ~693, lehnt 0, negativ, inf, nan ab) um beide neuen Felder erweitern. In `test_docs.py` beide Schlüssel in `BATTERY_SETTINGS`.
- [ ] **Step 2:** `pytest tests/unit/shared/test_config.py tests/unit/test_docs.py -q` → FAIL (Feld fehlt, Doku fehlt).
- [ ] **Step 3:** Felder in `ControlConfig`, Docstring „Selbststart an der EVCS (Plan EVCS-Selbststart)“ mit beiden Schlüsseln; Zeilen in `config.example.yaml` und in der Tabelle von `docs/betrieb.md` (Standardwert, Bedeutung wie bei `tesla_against_*`, „Rücklesen der EVCS zeigt Laden an“).
- [ ] **Step 4:** Tests grün.
- [ ] **Step 5: Commit** `feat(shared): Einstellungen für den Selbststart an der EVCS`

### Task 2: Regelung stoppt eine EVCS, die gegen den Modus an ist

**Files:**
- Modify: `src/bindaems/core/control/charging.py` (`ChargeController.__init__`, `_self_start`, die Stellen mit `self._against.clear`/`due`: Zeilen ~513, 661, 687–690, 825, 1345, 1452–1495; Modul-Docstring Absatz „Selbststart“; `_Situation.self_start`-Doku)
- Test: `tests/unit/core/control/test_charging.py` (neuer Abschnitt `# --- Selbststart an der EVCS (Plan EVCS-Selbststart) ---`)

**Interfaces:**
- Consumes: `ControlConfig.evcs_against_s`, `evcs_against_retry_s` (Task 1); `AgainstMode` (`charging_tesla.py`, unverändert)
- Produces: `ChargeController._against_evcs: AgainstMode`; `_self_start(now, key, actuator, point, vehicle, decision, inputs) -> bool` (neuer Parameter `point: ChargePoint` hinter `actuator`, für `point.evcs`)

- [ ] **Step 1: Failing tests** mit den Helfern aus `charge_helpers.py` (`run`, `evcs`, `grid`, `at_evcs`, `egolf_in`, `NO_SHARE`) und einem neuen Helfer im Test:

```python
def evcs_self_start_run(cfg, intent, seconds, *, on_from=60, **kw):
    """Der e-Golf steht an der EVCS, ohne Überschuss; bis ``on_from`` ist die EVCS aus
    (Rücklesen 7 A, aus), ab da meldet sie „Laden an“ und lädt mit 7 A auf zwei Phasen."""
```

  Ab `on_from`: `present=at_evcs(7.0, True)`, `evcs_obs=evcs(charging=True, phase_a={"L1": 7.0, "L2": 7.0, "L3": 0.0}, power_w=3220.0)`, `grid_obs=grid(grid_w=3200.0)`.

  - `test_an_evcs_switched_on_against_pv_gets_charging_off_again`: Intent PV; Stopps (`CHARGING`, Wert False) ab Sekunde 60 genau bei 120, Gründe enden mit `"self_start"` und beginnen mit `"mode_pv"`.
  - `test_evcs_self_start_is_retried_at_most_every_5_min`: 700 s → Stopps ab 60 bei `[120, 420]`.
  - `test_evcs_self_start_in_off_is_urgent`: Intent Aus → Stopp bei 120 mit `urgent=True`.
  - `test_own_stop_in_flight_is_no_self_start`: Die Regelung stoppt bei 0 selbst, das Rücklesen zeigt bis 30 noch „an“, danach „aus“ → genau ein Stopp (bei 0).
  - `test_keep_on_complete_is_no_self_start`: K2 (`evcs(complete=True)`, Rücklesen an) 700 s → kein „Laden aus“.
  - `test_now_resets_the_evcs_self_start_time`: PV bis 90, dann Sofort → kein Stopp ab 60.
  - `test_unknown_evcs_readback_is_no_self_start`: `present` ohne den Schlüssel `("wallbox.evcs", CHARGING)` ab 60 → kein Stopp ab 60.
  - `test_unplugged_evcs_is_no_self_start`: `evcs(connected=False)` und Rücklesen an → kein Stopp nach dem ersten.
  - `test_autostart_after_replug_gets_charging_off`: Bis 50 angesteckt und aus (Stopp bei 0), 50 bis 54 `evcs(connected=False)`, ab 55 wieder angesteckt mit Rücklesen aus (die Regelung fragt „aus“ erneut an, der Test nimmt es an), ab 58 Rücklesen an und Laden mit 7 A → ein Stopp mit `"self_start"` bei 118, keiner davor ab 58.
  - `test_a_guest_at_the_evcs_gets_self_start_too`: wie der erste Test mit dem Gast (`egolf_obs` abwesend, Muster aus `test_a_guest_at_the_evcs_charges_in_the_guest_mode`), Stopp bei 120.
- [ ] **Step 2:** `pytest tests/unit/core/control/test_charging.py -k self_start -q` → FAIL (kein Stopp nach dem ersten).
- [ ] **Step 3: Implementieren.** `_self_start` bekommt `point`. Zweig 1 (unverändert) für Fahrzeuge mit Tessie, **nur wenn `not point.evcs`**. Zweig 2 für `point.evcs`: `against = inputs.present.get((actuator, _CHARGING)) is True and not decision.charge and vehicle.plugged is True and vehicle.kind is not AssignmentKind.UNCLEAR`; Rest (sendbar, `due`, `forget_key`) wie Zweig 1 mit `self._against_evcs`. An jeder Stelle, an der heute `self._against.clear(key)`/`due(key, now, False)` steht, auch `self._against_evcs` behandeln; in `idle` für alle `self._fleet` (nicht nur Tesla), bei neuem/gemerktem Vorgang für jedes Auto (nicht nur `key in self._tesla`).
- [ ] **Step 4:** `pytest tests/unit/core/control -q` grün, auch alle bestehenden Tests von `test_charging_tesla.py` unverändert.
- [ ] **Step 5: Commit** `fix(core): EVCS bekommt erneut „Laden aus“, wenn sie gegen den Modus lädt`

### Task 3: Szenario, Spec und Doku

**Files:**
- Modify: `src/bindaems/tools/sim/scenarios.py` (Ereignis `_EvcsSwitchedOn`, Szenario `_egolf_selbststart`, Konstanten bei den anderen Selbststart-Konstanten, Eintrag in der Liste nach `_tesla_selbststart()`)
- Modify: `docs/betrieb.md` (Absatz „Selbststart“ ~3545 um die EVCS ergänzen, Grund-Tabelle `self_start` ~3673: „Ein Fahrzeug mit Tessie oder eine EVCS …“, Szenario-Tabelle ~6949 neue Zeile)
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absatz „Stand Plan EVCS-Selbststart“ unter 7.5 „Selbststart“)
- Modify: `docs/uebergabe.md` (Szenario-Anzahl, Offen-Punkt „core+app mit Stufe 2, vorher Influx-Prüfung `start_stop`“, Stand der Anlage „EVCS: Autostart aus seit 05.11.“)
- Modify: `docs/betrieb.md` (Abschnitt zur EVCS-Einrichtung: Autostart aus lassen, sonst lädt sie nach dem Anstecken bis zum Selbststart-Stopp 60 s lang)
- Test: `tests/integration/test_sim_scenarios.py` (läuft alle Szenarien), Anzahl-Tests der Szenarien, falls vorhanden

**Interfaces:**
- Consumes: `Evcs.start_stop` (`tools/sim/cars.py`), `_Unplug`, `_Plug`, `_stops_within`, `_idle_from`, `_setup`, `_intent`, `EVCS_BOX`, `wallbox_actuator`
- Produces: Szenario `egolf-autostart`, Ereignis `_EvcsSwitchedOn`

- [ ] **Step 1:** Szenario `egolf-autostart`, Titel „e-Golf neu angesteckt, EVCS startet selbst“, nach dem 05.11.: `EVENING`, 30 min, Seed 48 (nächste freie Nummer prüfen), e-Golf an der EVCS, 50 % SOC, Tesla abgesteckt, Intent e-Golf PV, Akku 30 %. Ereignisse: bei 5 min `_Unplug(EGOLF)`, bei 5 min 10 s `_Plug(EGOLF, EVCS_BOX)`, bei 5 min 13 s das neue Ereignis `_EvcsSwitchedOn(EVCS_BOX)`, das `model.wallboxes[EVCS_BOX].start_stop = 1` setzt wie Autostart (der core sendet nichts dazu). Prüfungen: `_stops_within(wallbox_actuator(EVCS_BOX), 5 min 13 s, 90 s)` (60 s plus Lesetakt plus Verzug) und `_idle_from(EGOLF, 5 min 13 s + 120 s)`.
- [ ] **Step 2:** Szenario einzeln laufen lassen (`pytest tests/integration/test_sim_scenarios.py -k egolf-autostart`): Auf Stand Task 2 grün. Gegenprobe: mit `git stash` von Task 2 rot. Das Ergebnis der Gegenprobe kommt ins Ledger.
- [ ] **Step 3:** Doku und Spec wie oben, `pytest tests/unit/test_docs.py -q` grün.
- [ ] **Step 4:** Ganze Kette laut Projektregeln (ruff format/check, mypy strict src + cerbo-watchdog, lint-imports, pytest mit Abdeckung ≥ 75 % gesamt, ≥ 90 % safety/control/actuators; UI unverändert, `pnpm check/lint/test` trotzdem einmal).
- [ ] **Step 5: Commit** `test(sim): Szenario e-Golf neu angesteckt, EVCS startet selbst` und `docs: Selbststart an der EVCS`

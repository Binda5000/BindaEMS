# Phase 2c-1 – Laderegelung: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BindaEMS regelt erstmals selbst, und zwar die beiden Ladepunkte:
- Lademodi je Auto (PV-Überschuss, Min+PV, Sofort, Aus, Boost) mit Phasenwächter je Modus
- Tesla mit Weckregeln, gleitendem Mittel, 90-s-Regel und erneutem Anfordern nach „nicht übernommen“
- vereinfachte Zuordnung Auto ↔ Wallbox, Degradation, die geparkten Befunde aus 2b und die Selbstprüfung des Multi-Modus
- ein Anlagensimulator mit zehn Szenarien in der CI

Im Dry-Run halten die Entscheidungen den Wert fest, den evcc eingestellt hat; so vergleicht der Betreiber beide.

**Architecture:**
- **Rein in `core/control`** (ohne I/O, Zeit als Argument):
  - `phase_guard.py`: Phasenwächter
  - `surplus.py`: Überschuss und seine Verteilung
  - `charge_modes.py`: Lademodi mit Zeiten
  - `charge_inputs.py`: Eingaben aus Snapshot und abgeleiteten Größen
  - `assignment.py`: Zuordnung, Ansteckvorgänge, Boost und Übersteuerung
  - `tesla_policy.py`: gleitendes Mittel und Weckkontingent
  - `charging.py`: `ChargeController`, der die Bausteine je Zyklus verbindet
- **Laufzeit:** Der Zyklus bleibt: Rücklesen, sicherer Zustand und Übergabe, Laderegelung, Heartbeat. Jeder Befehl der Regelung geht durch `CommandExecutor.execute`.
- **Tesla-Aktor:** bekommt einen Startweg mit Weckerlaubnis (`wake_capabilities`); der Ausführer nimmt ihn bei `Command.wake`.
- **Simulator** `bindaems.tools.sim`:
  - die echte Laufzeit mit echten Aktoren und simulierter Zeit
  - gefälscht sind nur die Geräte dahinter: der MQTT-Schreibweg und Tessie über `httpx.MockTransport`

**Tech Stack:** wie Plan 2b: Python 3.12 mit uv, pydantic v2, FastAPI, aiomqtt, pymodbus und httpx; Tests mit pytest, hypothesis, respx und pytest-cov; ruff, mypy und import-linter.

**Spec:** `docs/superpowers/specs/2026-10-12-phase-2c1-laderegelung-design.md` (im Plan „Design“).
- Verweise wie „Design 5.4“ beziehen sich darauf, „Spec 7.3“ auf `docs/superpowers/specs/2026-10-08-bindaems-design.md`.
- Wo das Design von der Spec abweicht, gilt das Design (Design 14.2).
- Ausführende lesen Design und Plan zusammen; die Präzisierungen unten gehen beiden vor.
- Vorgänger sind die Pläne 2a und 2b samt Entscheidungsdateien, alle umgesetzt. Ihre Global Constraints gelten weiter.

## Einordnung

Phase 2 ist in die Pläne 2a, 2b, 2c und 2d geteilt; der Betreiber hat 2c geteilt (Design 2):
- **2c-1 Laderegelung** (dieser Plan)
- 2c-2 Akku und Spitzen (eigener Plan)

Ergebnis von 2c-1:
- Alle Szenarien des Simulators sind in der CI grün.
- An der Anlage beginnt danach der Dry-Run neben evcc. Die Entscheidungen liest der Betreiber bis 2d über die Dateien bzw. `GET /v1/decisions`.
- Live gehen EVCS und Tesla gemeinsam erst nach 2d, mit abgeschaltetem evcc (Design 14.1).

Stand an der Anlage am 12.10.2026:
- Teil 2 bestanden: Tesla (dritter Lauf, Schlaf-Test) und EVCS; bei der EVCS fehlt noch „Verbindung unterbrochen“.
- Victron 17.2-8 bestanden: Das Importlimit hält 20 A je Phase, aber nur mit dem Multi auf „Ein“.
- evcc regelt beide Ladepunkte; Dynamic ESS bleibt an, bis Victron live geht.

## Präzisierungen gegenüber dem Design

1. **Dateien (Design 4.1).** Das Design nennt `phase_guard.py` und `charging.py`. Die Laderegelung ist auf die Bausteine aus „Architecture“ verteilt; `charging.py` verbindet sie nur.
2. **Absicht (Design 3.1).**
   - **Ablehnungen:** Unbekannte Fahrzeuge, unbekannte Wallboxen, eine Wallbox bei mehreren Fahrzeugen und eine Priorität, die nicht jedes Fahrzeug genau einmal nennt, lehnt der core mit dem neuen Grund `invalid` ab (Status 422).
   - **Klemmung von `boost_until`:** auf höchstens 12 h nach dem Empfang. Empfang ist die Annahme der Version (`accepted_at`).
     - Eine Wiederholung derselben Version klemmt gegen denselben Zeitpunkt und bleibt so eine Wiederholung, auch nach einem Neustart: `accepted_at` steht in `intent.json`.
     - Ein `boost_until` in der Vergangenheit ist kein Fehler; es gibt dann keinen Boost.
3. **Ansteckvorgänge und Bindung (Design 3.1, 8).**
   - **Ansteckvorgänge zählt der core je Wallbox:**
     - Ein neuer beginnt nur mit dem Wechsel von bekannt „kein Fahrzeug“ zu „Fahrzeug“.
     - „Kein Fahrzeug“ beendet ihn.
     - Ein unbekannter Wert ändert nichts.
     - Die Wallbox meldet ein Fahrzeug so: EVCS mit Status ≠ 0, Wall Connector mit „Fahrzeug verbunden“.
   - **Boost und Übersteuerung** bindet der core an den Ansteckvorgang der Wallbox, an der das Fahrzeug laut Absicht hängt:
     - Meldet sie ein Fahrzeug, an den laufenden Vorgang; sonst an den nächsten.
     - Sie gelten bis zum Ende dieses Vorgangs, danach erst wieder mit einer neuen Version.
     - **Nach einem Neustart:** Für eine Absicht, die vor dem Start der Regelung angenommen wurde, gilt die Bindung nur, wenn die Wallbox bei der ersten Auswertung ein Fahrzeug meldet. Ein Boost vom Vortag springt so nicht beim nächsten Anstecken an.
   - **Eine aktive Übersteuerung** eines Fahrzeugs auf Wallbox w nimmt w den anderen Fahrzeugen als Standard weg; sie haben dann keinen Ladepunkt.
4. **Angesteckt, lädt, geladen.**
   - **EVCS-Status:** Die Codes folgen der Dokumentation des GX-Dienstes `evcharger`: 0 getrennt, 1 verbunden, 2 lädt, 3 geladen. Jeder andere Code heißt „verbunden, lädt nicht“; das ist eine Annahme.
   - **„Lädt die Wallbox bei angestecktem Auto nicht mehr“** (Design 6.3) ist Status 3.
   - **Wall Connector:** Er lädt, wenn die Summe seiner Phasenströme mindestens 1 A beträgt.
   - **Fahrzeug mit Tessie** an Wallbox w:
     - Meldet w ein Fahrzeug, ist es angesteckt.
     - Meldet w keins, aber Tessie „zu Hause und angesteckt“, ist es unbekannt; der Widerspruch klärt sich über die Zuordnung.
     - Ohne Wert von w gilt Tessie.
   - **Fahrzeug ohne Tessie:** nur der Wert von w.
   - **Unbekannt angesteckt:** Die Regelung hält still. Sie sendet kein „Laden an/aus“ und senkt den Strom nur, wenn der Phasenwächter es verlangt.
5. **Zuordnung unklar (Design 8).**
   - **Widerspruch:** Ein Fahrzeug mit Tessie, das an einem Wall Connector stehen sollte, meldet über Tessie „zu Hause“ und „angesteckt“. Der Wall Connector meldet kein Fahrzeug, eine EVCS aber eines. Das ist das Bild „Tesla an der EVCS“.
   - **Zeiten:** 60 s Widerspruch ohne Unterbrechung → unklar; 60 s ohne Widerspruch → wieder klar.
   - **Unbekannte Werte** widersprechen nie.
   - **Eine aktive Übersteuerung** hebt „unklar“ sofort auf.
   - **Solange unklar** rechnet der Wächter die EVCS mit allen Phasen ihrer `phase_map` und ihrem eigenen Mindest- und Höchststrom.
6. **Last je Phase (Design 5.1, 5.5, 9).**
   - **Gemessener Strom eines Ladepunkts:**
     - mit erreichbarer Wallbox `derived.wallbox_phase_a` dieser Wallbox
     - beim Tesla mit getrennter Wallbox `actual_current_a` aus Tessie auf den ersten `phases` Phasen der `phase_map`
     - sonst unbekannt
   - **Geregelt** ist ein Ladepunkt, wenn alles zutrifft:
     - Sein Aktor ist steuerbar, beim Tesla auch nur weckbar.
     - Er pausiert nicht (Zuordnung unklar).
     - Seine Wallbox ist erreichbar, oder er ist ein Tesla mit Strom aus Tessie.
   - Nur der gemessene Strom geregelter Ladepunkte wird abgezogen (`I_wb`), auf jeder Phase, für die er einen Wert hat. Ist er unbekannt, sind die Phasen des Ladepunkts voll.
   - Der Strom eines **nicht geregelten** Ladepunkts bleibt Last.
   - **Zuschläge bei Ausfällen,** nur für nicht geregelte Ladepunkte:
     - EVCS getrennt: `safe_a` auf allen Phasen ihrer `phase_map`
     - Tesla mit getrenntem Wall Connector und ohne Strom aus Tessie: `max_a` auf allen drei Phasen
7. **Verteilung (Design 5.3, 6.2).**
   - **Sofort und Boost** ziehen ihren vollen Strom `max_a × U × Phasen` vom Überschuss ab, bevor PV-Autos ihren Anteil bekommen; der Phasenwächter bleibt dabei außen vor. Das ist vorsichtig, wenn der Wächter sie kappt.
   - **Ein PV-Auto in seinen Stopp-Minuten** verbraucht seinen Mindeststrom vom Überschuss.
   - **Der Wächter** teilt in der Reihenfolge `priority` zu. Für die folgenden Autos zählt bei jedem früheren das Größere aus Zuteilung und gemessenem Strom: Eine Senkung, die noch nicht wirkt, bleibt so reserviert.
8. **Überlast (Design 5.4).**
   - **Aufheben:** „Wieder unter dem Ziel“ heißt 60 s lang alle Phasen höchstens bei `fuse_a − fuse_margin_a`.
   - **Unbekannter Netzstrom einer Phase** zählt weder als über noch als unter der Grenze.
   - **Nach einem Stopp wegen Überlast** gilt die Mindestpause `min_off_s`, gegen Schleifen aus Stopp und Neustart. Überlast hebt nur die Mindestlaufzeit auf.
9. **Wann ein Auto lädt (Design 6.3, 6.4).**
   - **Abgesteckt:** Die Regelung will „Laden aus“. Die EVCS bekommt das einmal; der nächste Ansteckvorgang startet so nicht von selbst.
   - **Neuer Ansteckvorgang:**
     - PV-Überschuss und Aus beginnen mit „Laden aus“, auch wenn das Auto selbst gestartet hat.
     - Sofort, Boost und Min+PV starten sofort.
   - **Erster Regelzyklus nach dem Start des core und nach einer Pause** (kein Regelzyklus, etwa SAFE, OBSERVE oder „Regelbetrieb aus“):
     - PV-Überschuss übernimmt den beobachteten Zustand; ein ladendes Auto wird nicht sofort gestoppt.
     - Die Mindestlaufzeit gilt dann als erfüllt.
   - **Nach einer Pause** vergisst die Regelung ihre letzten Befehle.
10. **Nur Änderungen (Design 4.4).** Als letzter eigener Wert je Aktor und Art zählt:
    - Gesetzt wird er bei einem Urteil mit Wert (gesendet oder Dry-Run) und bei `UNCHANGED`, denn dann hat das Gerät den Wert schon.
    - `DEFERRED` und `REJECTED` setzen ihn nicht; die Regelung bietet den Befehl im nächsten Zyklus wieder an.
    - `ERROR`, `TIMEOUT` und `NOT_SENT` löschen ihn.
    - Ein `ERROR` mit `not_taken` merkt sich außerdem den Grund `not_taken_retry` für den nächsten Befehl dieser Art.
11. **Dringend (Design 4.4, 5.4)** sind:
    - Senkungen unter den Ist-Strom, wenn das Ziel die Sicherung ist (Sofort oder Boost ohne Reserve-Schutz)
    - alle Befehle wegen Überlast

    Alle anderen sind normal, auch Senkungen am Importlimit. Diese fängt Peak Shaving ab.
12. **Tesla (Design 6.5, 7).**
    - **Strom:** `floor(min(Mittel des Wunschstroms über tesla_average_s, niedrigster freier Wert über tesla_caution_s))`, begrenzt auf Mindest- und Höchststrom.
      - Ein unbekannter freier Wert zählt im Fenster als 0.
      - Beide Fenster beginnen mit jedem neuen Ansteckvorgang und nach einer Pause neu.
    - **Wecken:**
      - nur für einen gewollten Start, wenn der Aktor nicht steuerbar, aber weckbar ist (`wake_capabilities`), und nur im Kontingent
      - Das Kontingent zählt jedes Urteil mit Wert für einen Weckstart, live wie im Dry-Run; so bleibt der Dry-Run-Vergleich ehrlich.
13. **Modus und Eingaben im Entscheidungslog (Design 12).**
    - Den Modus tragen die Gründe (`mode_*`, `boost`), denn `inputs` enthält nur Zahlen.
    - Neue Eingaben je Befehl:
      - `surplus_w`: Überschuss
      - `limit_a`: freier Strom des Phasenwächters
      - `actual_current_a`: Ist-Sollstrom des Aktors
      - `actual_charging`: Ist-Zustand, 1 = lädt, 0 = nicht

      Im Dry-Run sind die Ist-Werte die von evcc.
14. **Health (Design 11)** bekommt `vehicles`: eine Liste je Fahrzeug in der Reihenfolge von `config.yaml` (Felder in Task 9).
15. **Selbstprüfung (Design 11):** neue Prüfpunkte `multi_mode` und `import_limit`; sie sperren nichts.
16. **Simulator (Design 13).**
    - **Aktoren:** Statt gefälschter Aktoren nutzt er die echten. Gefälscht sind die Geräte, so prüfen die Szenarien auch die Aktoren.
    - **Topologie:**
      - Alle Lasten hängen am AC-Ausgang der Multis, die PV am AC-Eingang.
      - ESS gleicht die Summe der Phasen aus, jede Phase mit einem Drittel der Akkuleistung.
      - Peak Shaving wirkt je Phase.
    - **Szenarien** dauern 1 bis 2 h simulierte Zeit.
    - **Kennzahlen** (Task 14):
      - Statt „Übergänge bis 10 s“ gelten beim Tesla 100 s, wegen der 90-s-Regel.
      - „Keine Akku-Energie in PV-Autos“ heißt: höchstens 5 % ihrer Energie.
    - **Lastsprünge** sind so bemessen, dass ein korrekt regelnder core 35 A halten kann.

## Global Constraints

- **Gilt weiter:** alle Global Constraints aus 1a, 1b, 2a und 2b.
- **Schreibwege:** unverändert gegenüber 2b; es gibt keinen neuen Pfad und keinen neuen Tessie-Befehl.
  - Die Regelung schreibt nur EVCS-Strom und -Laden sowie Tesla-Strom und -Laden, nie Victron.
  - Der Weckstart ist `start_charging`; Tessie weckt dabei selbst.
- **Jeder Befehl** der Regelung geht durch `CommandExecutor.execute`, also genau einmal durch `SafetyLayer.authorize`. Dringend nur nach Präzisierung 11.
- **Keine Regelung** in START, OBSERVE und SAFE und nie bei ausgeschaltetem Regelbetrieb (Design 4.3).
- **Reinheit:** Die neuen Module in `core/control` importieren nichts aus `core.actuators`, `core.adapters`, `core.api`, `core.runtime`, `core.telemetry`, `core.accounting` und `core.checks` (bestehender Importvertrag).
- **Zeit:**
  - Zeiten der Regelung (Timer, Fenster, Überlast, Weckabstand) laufen auf der Uhr der Safety-Schicht (`ChargeInputs.now`).
  - `boost_until` und der Kalendertag des Weckkontingents (Wiener Zeit) gehen nach der Wanduhr (`ChargeInputs.wall_now`).
- **Abdeckung:** ≥ 90 % für `core/safety`, `core/control` und `core/actuators`, ≥ 75 % insgesamt.
- **Texte:** exakt wie in der Tabelle „Texte“; Tests prüfen den Wortlaut. Zahlen in Begründungen über `de()`, in der Selbstprüfung über `fmt()`.
- **Öffentliches Repository:**
  - Simulator, Tests und Berichte enthalten keine Daten der Anlage; der Ort kommt aus `deploy/config.example.yaml`.
  - Keine Verweise auf Ledger-Einträge (R…) in Code und Tests.
- **Ströme in Befehlen** sind ganze Ampere als `float` (`8.0`).

## Review Focus

1. **Neustart des core, während ein Auto lädt:**
   - Erwartet: Es gibt keinen sofortigen Stopp oder Start.
   - PV-Überschuss mit Überschuss lädt weiter; ohne Überschuss stoppt es frühestens nach `stop_after_s` ab dem ersten Regelzyklus.
   - Tests: Task 4 `test_first_step_takes_over_observed_charging`, Task 10 `test_restart_while_charging_does_not_stop`.
2. **Moduswechsel, während ein Schreibvorgang offen ist:**
   - Erwartet: Die blockierte Art wartet, ohne als gesendet zu gelten; die nächste freie Gelegenheit sendet den neuen Wert einmal.
   - Test: Task 8 `test_blocked_kind_waits_and_follows_after_result`.
3. **EVCS-Status unbekannt oder flatternd:**
   - Erwartet:
     - Ein unbekannter Status beendet keinen Ansteckvorgang und startet nichts.
     - Ein flatternder Widerspruch macht die Zuordnung nie unklar.
   - Tests: Task 6 `test_unknown_plug_state_keeps_the_session` und `test_flapping_contradiction_never_turns_unclear`, Task 8 `test_unknown_evcs_status_holds_still`.
4. **Gespeicherte Absicht aus 2b und Absicht ohne Eintrag für ein Fahrzeug:**
   - Erwartet: Sie lädt fehlerfrei; das Fahrzeug lädt im PV-Überschuss, die Priorität folgt `config.yaml`, der Vorrang ist `battery_until_soc` mit 30 %.
   - Tests: Task 1 `test_intent_file_from_2b_loads_with_defaults`, Task 8 `test_vehicle_without_entry_charges_in_pv`.
5. **Weckkontingent an Mitternacht und bei der Zeitumstellung:**
   - Erwartet: Der Tag wechselt um 0 Uhr Wiener Zeit, auch am 25-Stunden-Tag; der Mindestabstand läuft auf der Uhr der Safety-Schicht.
   - Tests: Task 7 `test_quota_resets_at_vienna_midnight` und `test_quota_day_survives_the_dst_change`.

## Texte

**Gründe** der Regelung (`_REASON_TEXT` in `shared/decisions.py`):

| Code | Text |
|---|---|
| `mode_pv` | PV-Überschuss |
| `mode_min_pv` | Min+PV |
| `mode_now` | Sofort |
| `mode_off` | Aus |
| `boost` | Boost |
| `pv_surplus` | Überschuss |
| `phase_limit` | Phasengrenze |
| `import_limit` | Importlimit |
| `reserve_guard` | Schutz der USV-Reserve |
| `overload` | Überlast |
| `wake` | Wecken |
| `not_taken_retry` | erneut nach „nicht übernommen“ |
| `assignment_unclear` | Zuordnung unklar |
| `resume_after_outage` | Wiederaufnahme nach Ausfall |

Beispiel: `EVCS „evcs“ (PV-Überschuss, Überschuss): Strom 8 A – Dry-Run, nicht gesendet.`

**Absicht** (`IntentReply.details`):

| Fall | Text |
|---|---|
| unbekanntes Fahrzeug | Unbekanntes Fahrzeug „{v}“. |
| unbekannte Wallbox | Unbekannte Wallbox „{w}“ für „{v}“. |
| Wallbox mehrfach | Wallbox „{w}“ ist mehreren Fahrzeugen zugeordnet. |
| Priorität | Die Priorität muss jedes Fahrzeug genau einmal nennen. |
| Boost geklemmt | {Name}: Boost auf 12 h begrenzt, bis {HH:MM}. |

- {v} und {w} sind Schlüssel, {Name} ist `vehicles.<v>.name`, {HH:MM} ist Wiener Zeit.
- **Reihenfolge der Ablehnungen:**
  - unbekannte Fahrzeuge, sortiert
  - unbekannte Wallboxen, nach Fahrzeug sortiert
  - mehrfache Wallboxen, sortiert
  - Priorität

**Selbstprüfung** (`CheckResult`):

| ID | Status | Text |
|---|---|---|
| `multi_mode` | ok | Multi auf „Ein“. |
| `multi_mode` | warn | Multi nicht auf „Ein“ (Modus {m}); das Importlimit wirkt nicht. |
| `multi_mode` | unknown | Multi-Modus nicht verfügbar. |
| `import_limit` | ok | Importlimit am Cerbo {x} A wie konfiguriert. |
| `import_limit` | warn | Importlimit am Cerbo {x} A, konfiguriert {y} A. |
| `import_limit` | warn | Importlimit am Cerbo aus. |
| `import_limit` | warn | Importlimit am Cerbo nicht verfügbar. |

- `multi_mode` liest `vebus.mode`; 3 ist „Ein“.
- `import_limit` liest `settings.cgwacs.acinputlimit`:
  - −1 ist „aus“.
  - Eine Abweichung über 0,5 A ist eine Warnung.
  - Fehlt der Wert, ist das ebenfalls eine Warnung (Design 11).

**Simulator:** Szenarien, Kennzahlen und Bericht stehen in den Tasks 14 und 15.

## Dateistruktur

```
src/bindaems/shared/config.py                  (geändert) import_limit_a, ControlConfig, WakeConfig
src/bindaems/shared/control.py                 (geändert) ChargeMode, SurplusOrder, SurplusPriority, VehicleIntent, Intent, Command.wake
src/bindaems/shared/decisions.py               (geändert) Gründe der Regelung
src/bindaems/core/control/intent.py            (geändert) Prüfung, Boost-Klemmung, accepted_at
src/bindaems/core/control/phase_guard.py       Phasenwächter, Überlast, gleitendes Minimum
src/bindaems/core/control/surplus.py           Überschuss und Anteile
src/bindaems/core/control/charge_modes.py      Lademodi mit Zeiten
src/bindaems/core/control/charge_inputs.py     Beobachtungen und ChargeInputs
src/bindaems/core/control/assignment.py        Zuordnung, Ansteckvorgänge, Bindungen, „unklar“
src/bindaems/core/control/tesla_policy.py      gleitendes Mittel, Weckkontingent
src/bindaems/core/control/charging.py          ChargeController, ChargeCommand
src/bindaems/core/control/actuator.py, executor.py, context.py   (geändert)
src/bindaems/core/actuators/tesla.py           (geändert) wake_capabilities
src/bindaems/core/checks/selfcheck.py          (geändert) multi_mode, import_limit
src/bindaems/core/watchdog_client/heartbeat.py (geändert) Startwert je Lauf, Zähler vergessen
src/bindaems/core/runtime.py, core/api/app.py  (geändert)
src/bindaems/tools/sim/__init__.py, __main__.py, model.py, cars.py, scenarios.py, metrics.py, runner.py, report.py
tests/unit/core/control/{test_phase_guard,test_surplus,test_charge_modes,test_charge_inputs,test_assignment,
  test_tesla_policy,test_charging,test_charging_tesla}.py, charge_helpers.py
tests/property/test_phase_guard.py, tests/unit/tools/sim/test_sim_{model,cars,metrics,cli}.py,
tests/integration/test_runtime_charging.py, tests/integration/test_sim_scenarios.py
docs: Spec, Design (Stand), Betriebshandbuch, docs/uebergabe.md, deploy/config.example.yaml
```

Neue Pakete unter `src/bindaems` bekommen eine `__init__.py` (Layout-Test). Testverzeichnisse bekommen keine.

---

### Task 1: Konfiguration und Absicht

**Files:**
- Modify: `src/bindaems/shared/config.py`, `src/bindaems/shared/control.py`, `src/bindaems/core/control/intent.py`, `src/bindaems/core/api/app.py` (`_intent_status`), `src/bindaems/core/runtime.py` (Aufbau des `IntentStore`), `deploy/config.example.yaml`
- Test: `tests/unit/shared/test_config.py`, `tests/unit/shared/test_control.py`, `tests/unit/core/control/test_intent.py`, `tests/unit/core/api/test_api.py`

**Interfaces:**
- Consumes: `IntentStore`, `IntentReply`, `_Model`, `LOCAL_TZ` (bestehend)
- Produces:
  - **`shared/config.py`:**
    - `GridConfig.import_limit_a: float = 20.0` (> 0). Ein Validator verlangt `import_limit_a < fuse_a − fuse_margin_a`, Meldung „import_limit_a muss unter fuse_a − fuse_margin_a liegen“.
    - `class ControlConfig(_Model)` mit Standardwerten aus Design 3.2:

      | Feld | Standard | Bedingung |
      |---|---|---|
      | `import_margin_a` | `1.0` | ≥ 0 |
      | `surplus_margin_w` | `100.0` | ≥ 0 |
      | `start_after_s` | `120.0` | ≥ 0 |
      | `stop_after_s` | `300.0` | ≥ 0 |
      | `min_on_s` | `600.0` | ≥ 0 |
      | `min_off_s` | `600.0` | ≥ 0 |
      | `reserve_guard_pct` | `5.0` | 0 bis 100 |
      | `tesla_average_s` | `90.0` | > 0 |
      | `tesla_caution_s` | `60.0` | > 0 |
    - `Config.control: ControlConfig = Field(default_factory=ControlConfig)`. Ein Validator verlangt `control.import_margin_a < grid.import_limit_a`, Meldung „control.import_margin_a muss kleiner als grid.import_limit_a sein“.
    - `class WakeConfig(_Model)`: `interval_s: float = 1800.0` (≥ 0), `per_day: int = 6` (≥ 0). Dazu `VehicleConfig.wake: WakeConfig = Field(default_factory=WakeConfig)`.
  - **`shared/control.py`:**
    - `class ChargeMode(StrEnum)`: `PV = "pv"`, `MIN_PV = "min_pv"`, `NOW = "now"`, `OFF = "off"`
    - `class SurplusOrder(StrEnum)`: `CARS_FIRST = "cars_first"`, `BATTERY_FIRST = "battery_first"`, `BATTERY_UNTIL_SOC = "battery_until_soc"`
    - `class SurplusPriority(_Model)`: `kind: SurplusOrder = SurplusOrder.BATTERY_UNTIL_SOC`, `soc_pct: float = 30.0` (0 bis 100)
    - `class VehicleIntent(_Model)`: `mode: ChargeMode = ChargeMode.PV`, `boost_until: AwareDatetime | None = None`, `wallbox: str | None = None`
    - `Intent` bekommt:
      - `vehicles: dict[str, VehicleIntent] = Field(default_factory=dict)`
      - `priority: list[str] | None = None`
      - `surplus_priority: SurplusPriority = Field(default_factory=SurplusPriority)`
    - `Command.wake: bool = False` als letztes Feld; Docstring: Start mit Weckerlaubnis (Design 7.1), wirkt nur bei `charging` mit `True`.
  - **`core/control/intent.py`:**
    - `BOOST_MAX: Final = timedelta(hours=12)`
    - `IntentReply.rejection: Literal["stale", "unknown_actuator", "invalid"] | None`
    - `IntentStore.__init__(self, path, limits, *, vehicles: Mapping[str, str] | None = None, wallboxes: Collection[str] = ())`: `vehicles` bildet Schlüssel auf `name` ab, in der Reihenfolge von `config.yaml`.
    - `IntentStore.accepted_at -> datetime | None`: Annahmezeit der gültigen Absicht. `load` liest sie aus der Datei; fehlt sie oder ist sie ungültig, ist sie `None`.
    - **Regeln von `submit`, in dieser Reihenfolge:**
      1. unbekannte Aktoren (wie bisher)
      2. `invalid`: Hinweise nach „Texte“, die gültige Version bleibt
      3. Klemmen: Live wie bisher, Boost gegen den Empfang (Präzisierung 2). Die Hinweise kommen erst je Aktor, dann je Fahrzeug in der Reihenfolge von `config.yaml`.
      4. danach Version und Inhalt wie bisher
    - Empfang ist `now` bei einer neuen Version und `accepted_at` (sonst `now`) bei der gültigen.
  - **`core/api/app.py`:** `_intent_status` liefert 422 für `unknown_actuator` und `invalid`.
  - **`runtime.py`:** baut `IntentStore(…, vehicles={k: v.name for k, v in cfg.vehicles.items()}, wallboxes=list(cfg.wallboxes))`.
  - **`deploy/config.example.yaml`:**
    - `grid.import_limit_a: 20` mit Kommentar: Peak Shaving am Cerbo, wirkt nur mit dem Multi auf „Ein“
    - Abschnitt `control:` mit allen Standardwerten und je einer Kommentarzeile
    - `vehicles.tesla.wake: {interval_s: 1800, per_day: 6}`

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/shared/test_config.py
def test_charging_defaults(cfg) -> None:
    assert cfg.grid.import_limit_a == 20.0
    assert cfg.control == ControlConfig(
        import_margin_a=1.0, surplus_margin_w=100.0, start_after_s=120.0, stop_after_s=300.0,
        min_on_s=600.0, min_off_s=600.0, reserve_guard_pct=5.0, tesla_average_s=90.0,
        tesla_caution_s=60.0,
    )
    assert cfg.vehicles["tesla"].wake == WakeConfig(interval_s=1800.0, per_day=6)


def test_import_limit_must_stay_below_the_fuse_margin() -> None:
    with pytest.raises(ValidationError, match="import_limit_a muss unter fuse_a − fuse_margin_a liegen"):
        GridConfig(fuse_a=35, fuse_margin_a=2, import_limit_a=33)
    assert GridConfig(fuse_a=35, fuse_margin_a=2, import_limit_a=32.9).import_limit_a == 32.9


def test_import_margin_must_stay_below_the_import_limit(cfg) -> None:
    data = cfg.model_dump() | {"control": {"import_margin_a": 20}}
    with pytest.raises(ValidationError, match="control.import_margin_a muss kleiner als grid.import_limit_a sein"):
        Config.model_validate(data)


# tests/unit/shared/test_control.py
def test_intent_defaults_for_charging() -> None:
    intent = Intent(version=1)
    assert (intent.vehicles, intent.priority) == ({}, None)
    assert intent.surplus_priority == SurplusPriority(kind=SurplusOrder.BATTERY_UNTIL_SOC, soc_pct=30.0)
    assert VehicleIntent() == VehicleIntent(mode=ChargeMode.PV, boost_until=None, wallbox=None)


def test_boost_until_needs_a_time_zone() -> None:
    with pytest.raises(ValidationError):
        VehicleIntent(boost_until=datetime(2026, 10, 12, 18, 0))


# tests/unit/core/control/test_intent.py
VEHICLES = {"tesla": "Tesla Model 3", "egolf": "e-Golf"}


def charging_store(tmp_path, limits) -> IntentStore:
    return IntentStore(tmp_path / "intent.json", limits, vehicles=VEHICLES, wallboxes=("evcs", "twc"))


def test_invalid_vehicles_wallboxes_and_priority_are_rejected(tmp_path, limits) -> None:
    intent = Intent(
        version=1,
        vehicles={
            "golf": VehicleIntent(),
            "tesla": VehicleIntent(wallbox="garage"),
        },
        priority=["tesla"],
    )
    reply = charging_store(tmp_path, limits).submit(intent, T0)
    assert (reply.result, reply.rejection, reply.version) == ("rejected", "invalid", None)
    assert reply.details == (
        "Unbekanntes Fahrzeug „golf“.",
        "Unbekannte Wallbox „garage“ für „tesla“.",
        "Die Priorität muss jedes Fahrzeug genau einmal nennen.",
    )


def test_one_wallbox_for_two_vehicles_is_rejected(tmp_path, limits) -> None:
    intent = Intent(version=1, vehicles={
        "tesla": VehicleIntent(wallbox="evcs"), "egolf": VehicleIntent(wallbox="evcs"),
    })
    reply = charging_store(tmp_path, limits).submit(intent, T0)
    assert reply.details == ("Wallbox „evcs“ ist mehreren Fahrzeugen zugeordnet.",)


def test_boost_beyond_12_hours_is_clamped(tmp_path, limits) -> None:
    store = charging_store(tmp_path, limits)
    intent = Intent(version=1, vehicles={"tesla": VehicleIntent(boost_until=T0 + timedelta(hours=20))})
    reply = store.submit(intent, T0)  # T0 = 12:00 Wiener Zeit
    assert (reply.result, reply.details) == ("clamped", ("Tesla Model 3: Boost auf 12 h begrenzt, bis 00:00.",))
    assert store.current.vehicles["tesla"].boost_until == T0 + timedelta(hours=12)
    assert store.accepted_at == T0


def test_resubmitting_a_clamped_boost_later_is_a_repetition(tmp_path, limits) -> None:
    store = charging_store(tmp_path, limits)
    intent = Intent(version=1, vehicles={"tesla": VehicleIntent(boost_until=T0 + timedelta(hours=20))})
    store.submit(intent, T0)
    assert store.submit(intent, T0 + timedelta(minutes=5)).result == "clamped"
    again = charging_store(tmp_path, limits)
    again.load()
    assert again.accepted_at == T0
    assert again.submit(intent, T0 + timedelta(hours=1)).result == "clamped"


def test_intent_file_from_2b_loads_with_defaults(tmp_path, limits) -> None:  # Review Focus 4
    write_json_atomic(
        tmp_path / "intent.json",
        {"intent": {"version": 3, "control": True, "actuators": {"wallbox.evcs": "dry_run"}},
         "accepted_at": T0.isoformat()},
    )
    store = charging_store(tmp_path, limits)
    store.load()
    assert store.load_problem is None
    assert (store.current.vehicles, store.current.priority) == ({}, None)


# tests/unit/core/api/test_api.py
def test_invalid_charging_intent_is_422(...) -> None:
    # PUT /v1/intent mit vehicles={"golf": {}} → 422, result "rejected", details wie oben
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/shared tests/unit/core/control/test_intent.py tests/unit/core/api -q`
Expected: FAIL (`ImportError` für `ControlConfig`, `VehicleIntent` …)

- [ ] **Step 3: Umsetzen** wie in den Interfaces. Den Boost klemmt `_clamp(intent, received)`. Er formatiert {HH:MM} mit `received + BOOST_MAX` in `LOCAL_TZ`.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit tests/integration/test_runtime_control.py -q`
Expected: PASS; die bestehenden Tests der Absicht bleiben grün.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared src/bindaems/core/control/intent.py src/bindaems/core/api/app.py src/bindaems/core/runtime.py deploy/config.example.yaml tests/unit
git commit -m "feat(core): Absicht mit Lademodi, Boost, Übersteuerung und Überschuss-Vorrang; Konfiguration der Regelung"
```

---

### Task 2: Phasenwächter

**Files:**
- Create: `src/bindaems/core/control/phase_guard.py`
- Test: `tests/unit/core/control/test_phase_guard.py`, `tests/property/test_phase_guard.py`

**Interfaces:**
- Consumes: `Config`, `Phase` (Task 1)
- Produces:
  - `PHASES: Final[tuple[Phase, Phase, Phase]] = ("L1", "L2", "L3")`
  - `class GuardCause(StrEnum)`: `IMPORT_LIMIT = "import_limit"`, `RESERVE_GUARD = "reserve_guard"`, `PHASE_LIMIT = "phase_limit"`; der Wert ist der Grund-Code.
  - `@dataclass(frozen=True) class GuardLimits`:
    - Felder: `import_target_a`, `fuse_target_a`, `fuse_a`, `reserve_guard_soc_pct` (alle `float`)
    - `from_config(cfg)` → `import_limit_a − import_margin_a`, `fuse_a − fuse_margin_a`, `fuse_a`, `reserve_soc_pct + reserve_guard_pct`
  - `phase_target(limits, *, full_power: bool, soc_pct: float | None) -> tuple[float, GuardCause]` (Design 5.2):
    - nicht `full_power`: Importziel, `IMPORT_LIMIT`
    - `full_power` mit bekanntem SOC ≥ `reserve_guard_soc_pct`: Sicherungsziel, `PHASE_LIMIT`
    - sonst: Importziel, `RESERVE_GUARD`
  - `phase_loads(grid_a, battery_a, regulated, reserved=MappingProxyType({})) -> dict[Phase, float | None]` (Design 5.1, Präzisierung 6):
    - `grid_a`, `battery_a`: `Mapping[Phase, float | None]`
    - `regulated`: `Sequence[tuple[tuple[Phase, ...], Mapping[Phase, float] | None]]`, also je geregeltem Ladepunkt seine Phasen und seine Ströme je Netzphase
    - `reserved`: `Mapping[Phase, float]`, die Zuschläge
    - Ergebnis `I_netz + I_akku − Σ Ströme + Zuschlag`; die Ströme eines Ladepunkts zählen auf jeder Phase, für die er einen Wert hat.
    - `None` heißt voll: `grid_a` oder `battery_a` fehlt, oder ein geregelter Ladepunkt mit unbekannten Strömen (`None`) hat die Phase.
  - `@dataclass(frozen=True) class Claim`: `key: str`, `phases: tuple[Phase, ...]`, `target_a: float`, `wanted_a: float`, `holding_a: float = 0.0`
  - `@dataclass(frozen=True) class Grant`: `free_a: float | None`, `granted_a: int`
  - `allocate(loads, claims) -> dict[str, Grant]`: der Reihe nach (Präzisierung 7)
    - `free_a = min(target_a − load_p − used_p)` über die Phasen des Claims
    - `granted_a = max(0, min(int(wanted_a), floor(free_a)))`; mit unbekannter Last `Grant(None, 0)`
    - danach `used_p += max(granted_a, holding_a)` auf seinen Phasen
  - `class OverloadLevel(IntEnum)`: `NONE = 0`, `MINIMUM = 1`, `EVCS_STOP = 2`, `ALL_STOP = 3`
  - `class OverloadGuard` mit `__init__(self, limits: GuardLimits)` und `update(self, now, grid_a) -> OverloadLevel` (Design 5.4, Präzisierung 8):
    - Eine Phase liegt über `fuse_target_a`: nach 3 s `MINIMUM`, nach 6 s `EVCS_STOP`.
    - Eine Phase liegt über `fuse_a`: nach 10 s `ALL_STOP`.
    - Die Stufe hält, bis 60 s lang alle Phasen höchstens bei `fuse_target_a` liegen.
    - Konstanten `MIN_AFTER_S = 3.0`, `EVCS_STOP_AFTER_S = 6.0`, `ALL_STOP_AFTER_S = 10.0`, `CLEAR_AFTER_S = 60.0`.
  - `class SlidingMin` mit `__init__(self, window_s: float)`, `add(now, value: float)`, `value(now) -> float | None`: der kleinste Wert mit `now − t ≤ window_s`, ohne Wert `None`.

- [ ] **Step 1: Failing Tests schreiben**

```python
LIMITS = GuardLimits(import_target_a=19.0, fuse_target_a=33.0, fuse_a=35.0, reserve_guard_soc_pct=25.0)
ALL = ("L1", "L2", "L3")


def test_limits_from_the_example_config(cfg) -> None:
    assert GuardLimits.from_config(cfg) == LIMITS


@pytest.mark.parametrize(("full", "soc", "expected"), [
    (False, 80.0, (19.0, GuardCause.IMPORT_LIMIT)),
    (True, 80.0, (33.0, GuardCause.PHASE_LIMIT)),
    (True, 25.0, (33.0, GuardCause.PHASE_LIMIT)),
    (True, 24.9, (19.0, GuardCause.RESERVE_GUARD)),
    (True, None, (19.0, GuardCause.RESERVE_GUARD)),
])
def test_target_per_mode(full, soc, expected) -> None:
    assert phase_target(LIMITS, full_power=full, soc_pct=soc) == expected


def test_load_adds_battery_help_and_removes_regulated_wallboxes() -> None:
    loads = phase_loads(
        {"L1": 2.0, "L2": 18.0, "L3": -3.0},
        {"L1": 10.0, "L2": 0.0, "L3": 4.0},
        [(("L1", "L2"), {"L1": 6.0, "L2": 6.0, "L3": 0.0})],
        {"L3": 6.0},
    )
    assert loads == {"L1": 6.0, "L2": 12.0, "L3": 7.0}


def test_unknown_values_make_the_phase_full() -> None:
    loads = phase_loads({"L1": 2.0, "L2": None, "L3": 1.0}, {"L1": None, "L2": 0.0, "L3": 0.0},
                        [(("L3",), None)])
    assert loads == {"L1": None, "L2": None, "L3": None}


def test_allocation_follows_priority_and_holds_measured_current() -> None:
    grants = allocate({"L1": 10.0, "L2": 4.0, "L3": 4.0}, [
        Claim("tesla", ALL, 33.0, 16.0, holding_a=12.0),
        Claim("egolf", ("L1", "L2"), 19.0, 16.0),
    ])
    assert grants == {"tesla": Grant(23.0, 16), "egolf": Grant(-7.0, 0)}


def test_lower_priority_gets_what_is_left() -> None:
    grants = allocate({"L1": 2.0, "L2": 2.0, "L3": 2.0}, [
        Claim("tesla", ALL, 19.0, 10.0), Claim("egolf", ("L1", "L2"), 19.0, 16.0),
    ])
    assert grants["egolf"] == Grant(7.0, 7)


def test_unknown_load_grants_nothing() -> None:
    grants = allocate({"L1": None, "L2": 2.0, "L3": 2.0}, [Claim("egolf", ("L1", "L2"), 19.0, 16.0)])
    assert grants == {"egolf": Grant(None, 0)}


def test_overload_escalates_and_holds_for_60_s() -> None:
    guard = OverloadGuard(LIMITS)
    over = {"L1": 34.0, "L2": 10.0, "L3": 10.0}
    levels = [guard.update(T0 + timedelta(seconds=s), over) for s in range(8)]
    N, M, E = OverloadLevel.NONE, OverloadLevel.MINIMUM, OverloadLevel.EVCS_STOP
    assert levels == [N, N, N, M, M, M, E, E]
    calm = {"L1": 30.0, "L2": 10.0, "L3": 10.0}
    assert guard.update(T0 + timedelta(seconds=8), calm) == E
    assert guard.update(T0 + timedelta(seconds=67), calm) == E
    assert guard.update(T0 + timedelta(seconds=68), calm) == N


def test_ten_seconds_over_the_fuse_stops_everything() -> None:
    guard = OverloadGuard(LIMITS)
    over = {"L1": 36.0, "L2": 0.0, "L3": 0.0}
    assert [guard.update(T0 + timedelta(seconds=s), over) for s in (0, 9, 10)] == [
        OverloadLevel.NONE, OverloadLevel.EVCS_STOP, OverloadLevel.ALL_STOP]


def test_unknown_phase_neither_escalates_nor_clears() -> None:
    guard = OverloadGuard(LIMITS)
    for s in range(4):
        guard.update(T0 + timedelta(seconds=s), {"L1": 34.0, "L2": 0.0, "L3": 0.0})
    unknown = {"L1": None, "L2": 0.0, "L3": 0.0}
    assert guard.update(T0 + timedelta(seconds=100), unknown) == OverloadLevel.MINIMUM


def test_sliding_min_keeps_the_lowest_of_the_window() -> None:
    window = SlidingMin(60.0)
    for s, value in ((0, 12.0), (10, 8.0), (30, 14.0)):
        window.add(T0 + timedelta(seconds=s), value)
    assert window.value(T0 + timedelta(seconds=70)) == 8.0
    assert window.value(T0 + timedelta(seconds=71)) == 14.0
```

```python
# tests/property/test_phase_guard.py
@given(loads=..., claims=...)  # Lasten −50…60 A oder None, Ziele 19 oder 33, Wunsch und Halten 0…32 A,
                               # nichtleere Phasenmengen, eindeutige Schlüssel
def test_no_grant_exceeds_its_target(loads, claims) -> None:
    grants = allocate(loads, claims)
    used = dict.fromkeys(PHASES, 0.0)
    for claim in claims:
        grant = grants[claim.key]
        assert grant.granted_a >= 0
        if grant.granted_a > 0:
            for p in claim.phases:
                assert loads[p] is not None
                assert loads[p] + used[p] + grant.granted_a <= claim.target_a + 1e-9
        for p in claim.phases:
            used[p] += max(grant.granted_a, claim.holding_a)
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_phase_guard.py tests/property/test_phase_guard.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces. `OverloadGuard` merkt sich drei Zeitpunkte:
  - seit wann eine Phase über `fuse_target_a` liegt
  - seit wann eine über `fuse_a` liegt
  - seit wann alle darunter liegen

  Jeder beginnt neu, wenn seine Bedingung nicht mehr gilt. Eine unbekannte Phase erfüllt keine der Bedingungen: Sie liegt weder darüber noch darunter.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_phase_guard.py tests/property/test_phase_guard.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control/phase_guard.py tests/unit/core/control/test_phase_guard.py tests/property/test_phase_guard.py
git commit -m "feat(core): Phasenwächter mit Zielen je Modus, Verteilung und Überlast"
```

---

### Task 3: Überschuss und Anteile

**Files:**
- Create: `src/bindaems/core/control/surplus.py`
- Test: `tests/unit/core/control/test_surplus.py`

**Interfaces:**
- Consumes: `SurplusPriority`, `SurplusOrder` (Task 1)
- Produces:
  - `surplus_w(*, cars_w, grid_w, battery_w, soc_pct, priority: SurplusPriority, margin_w: float) -> float | None` (Design 6.1):
    - Formel: `cars_w − grid_w + anrechenbar − margin_w`; alle Größen in W
    - Fehlt `cars_w`, `grid_w` oder `battery_w`, ist das Ergebnis `None`.
    - **Entladen** (`battery_w < 0`): Die Akkuleistung zählt immer, und zwar negativ.
    - **Laden** zählt nur mit Vorrang der Autos:
      - `cars_first`: immer
      - `battery_first`: nie
      - `battery_until_soc`: ab bekanntem SOC ≥ `soc_pct`; ein unbekannter SOC zählt nicht
  - `@dataclass(frozen=True) class SurplusCar`: `key: str`, `full_power: bool`, `minimum: bool`, `phases: int`, `min_a: int`, `max_a: int`, `holding_min: bool = False`
  - `share_surplus(available_w: float | None, cars: Sequence[SurplusCar], voltage_v: float) -> dict[str, int | None]` (Design 6.2, Präzisierung 7):
    1. Autos mit `full_power` bekommen `max_a`. Ihre Leistung `max_a × U × Phasen` geht vorab vom Rest ab.
    2. Die übrigen kommen der Reihe nach. Ihr Strom ist `clamp(floor(Rest / (U × Phasen)), 0, max_a)`.
       - Bei `minimum` ist er mindestens `min_a`.
       - Liegt er unter `min_a`, bleibt er so stehen; mit `holding_min` verbraucht das Auto trotzdem `min_a`.
    3. Der verbrauchte Strom geht vom Rest ab: der eigene, `min_a` oder 0.
    - Ist `available_w` `None`, ergibt PV `None`, `minimum` `min_a` und `full_power` `max_a`.

- [ ] **Step 1: Failing Tests schreiben**

```python
U = 230.0
TESLA = SurplusCar("tesla", full_power=False, minimum=False, phases=3, min_a=5, max_a=16)
EGOLF = SurplusCar("egolf", full_power=False, minimum=False, phases=2, min_a=6, max_a=16)
B30 = SurplusPriority(kind=SurplusOrder.BATTERY_UNTIL_SOC, soc_pct=30.0)


def test_discharge_always_counts_against_the_cars() -> None:
    assert surplus_w(cars_w=4000.0, grid_w=500.0, battery_w=-1500.0, soc_pct=80.0,
                     priority=SurplusPriority(kind=SurplusOrder.CARS_FIRST), margin_w=100.0) == 1900.0


@pytest.mark.parametrize(("priority", "soc", "expected"), [
    (SurplusPriority(kind=SurplusOrder.CARS_FIRST), 10.0, 2900.0),
    (SurplusPriority(kind=SurplusOrder.BATTERY_FIRST), 90.0, 900.0),
    (B30, 29.9, 900.0),
    (B30, 30.0, 2900.0),
    (B30, None, 900.0),
])
def test_charging_battery_counts_by_priority(priority, soc, expected) -> None:
    assert surplus_w(cars_w=0.0, grid_w=-1000.0, battery_w=2000.0, soc_pct=soc,
                     priority=priority, margin_w=100.0) == expected


@pytest.mark.parametrize("missing", ["cars_w", "grid_w", "battery_w"])
def test_a_missing_value_makes_the_surplus_unknown(missing) -> None:
    values = {"cars_w": 0.0, "grid_w": -1000.0, "battery_w": 0.0} | {missing: None}
    assert surplus_w(**values, soc_pct=50.0, priority=B30, margin_w=100.0) is None


def test_full_power_cars_take_their_share_first() -> None:
    now = replace(TESLA, full_power=True)
    assert share_surplus(11000.0, [now, EGOLF], U) == {"tesla": 16, "egolf": 0}


def test_pv_cars_share_in_order() -> None:
    assert share_surplus(9000.0, [TESLA, EGOLF], U) == {"tesla": 13, "egolf": 0}
    assert share_surplus(9000.0, [EGOLF, TESLA], U) == {"egolf": 16, "tesla": 2}


def test_min_pv_takes_its_minimum_even_without_surplus() -> None:
    assert share_surplus(500.0, [replace(TESLA, minimum=True), EGOLF], U) == {"tesla": 5, "egolf": 0}


def test_a_pv_car_in_its_stop_minutes_uses_its_minimum() -> None:
    assert share_surplus(2000.0, [replace(EGOLF, holding_min=True), TESLA], U) == {"egolf": 4, "tesla": 0}


def test_unknown_surplus() -> None:
    cars = [TESLA, replace(EGOLF, minimum=True)]
    assert share_surplus(None, cars, U) == {"tesla": None, "egolf": 6}
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_surplus.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_surplus.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control/surplus.py tests/unit/core/control/test_surplus.py
git commit -m "feat(core): Überschuss und seine Verteilung auf die Autos"
```

---

### Task 4: Lademodi mit Zeiten

**Files:**
- Create: `src/bindaems/core/control/charge_modes.py`
- Test: `tests/unit/core/control/test_charge_modes.py`

**Interfaces:**
- Consumes: `ControlConfig` (Task 1)
- Produces:
  - `class Effective(StrEnum)`: `PV = "pv"`, `MIN_PV = "min_pv"`, `NOW = "now"`, `OFF = "off"`, `BOOST = "boost"`
  - `MODE_CODES: Final[Mapping[Effective, str]]`: `pv` → `mode_pv`, `min_pv` → `mode_min_pv`, `now` → `mode_now`, `off` → `mode_off`, `boost` → `boost`
  - `FULL_POWER: Final = frozenset({Effective.NOW, Effective.BOOST})`
  - `@dataclass(frozen=True) class ModeTimers`: `start_after_s`, `stop_after_s`, `min_on_s`, `min_off_s` (`float`), dazu `from_config(control: ControlConfig)`
  - `@dataclass(frozen=True) class ModeInput`:
    - `mode: Effective`, `plugged: bool | None`, `complete: bool`
    - `enough: bool | None`: Anteil und Grenze tragen den Mindeststrom
    - `observed_charging: bool | None`
    - `new_session: bool = False`, `resumed: bool = False`
  - `@dataclass(frozen=True) class ModeDecision`: `charge: bool`, `reason: str | None`; `"pv_surplus"`, wenn die Zeiten von PV-Überschuss in diesem Schritt einen Start oder Stopp ergaben
  - `class ModeMachine` mit `__init__(self, timers: ModeTimers)`, Property `charging -> bool`, `step(self, now, inp: ModeInput) -> ModeDecision` und `force_stop(self, now) -> None`
  - **Zeiten:** In jedem Schritt mit `plugged is True` merkt sich die Maschine, seit wann `enough is True` gilt bzw. nicht gilt, auch im ersten Schritt. Die Regeln 4 bis 6 setzen beide Zeitpunkte auf den aktuellen Schritt.
  - **Regeln von `step`**, die erste zutreffende entscheidet (Design 6.3, 6.4, Präzisierungen 8 und 9):
    1. `plugged is False`: aus. Zeiten, Moduswechsel und Pause vergessen; der nächste Ansteckvorgang beginnt frisch.
    2. `plugged is None`: die letzte Entscheidung halten, ohne Zeiten fortzuschreiben; vor der ersten: aus.
    3. `complete`: aus.
    4. Erster Schritt oder `resumed`:
       - PV: `charging = observed_charging is True`; eine Mindestlaufzeit gilt als erfüllt
       - Aus: aus
       - sonst: an
    5. `new_session`: PV und Aus aus, sonst an.
    6. Moduswechsel gegenüber dem letzten Schritt: sofort, ohne Zeiten und ohne Mindestzeiten.
       - Aus: aus
       - Sofort, Boost und Min+PV: an
       - PV: an genau dann, wenn `enough is True`
    7. Gleicher Modus:
       - Aus: aus
       - Sofort, Boost und Min+PV: an, außer innerhalb von `min_off_s` nach `force_stop`
       - PV:
         - **Start:** wenn `enough is True` seit `start_after_s` ohne Unterbrechung und seit dem letzten Stopp `min_off_s` vergangen sind (oder es keinen gab)
         - **Stopp:** wenn `enough is not True` seit `stop_after_s` und seit dem Start `min_on_s` vergangen sind (oder er unbekannt ist)
  - `force_stop(now)`: aus, letzter Stopp = `now`. Die Mindestpause gilt danach auch für Sofort, Boost und Min+PV (Präzisierung 8); ein Moduswechsel hebt sie auf.

- [ ] **Step 1: Failing Tests schreiben**

```python
TIMERS = ModeTimers(start_after_s=120.0, stop_after_s=300.0, min_on_s=600.0, min_off_s=600.0)


def at(s: float) -> datetime:
    return T0 + timedelta(seconds=s)


def inp(mode=Effective.PV, *, plugged=True, enough=True, observed=False, **kw) -> ModeInput:
    return ModeInput(mode=mode, plugged=plugged, complete=kw.pop("complete", False),
                     enough=enough, observed_charging=observed, **kw)


def run(machine, start: int, end: int, **kw) -> dict[int, ModeDecision]:
    return {s: machine.step(at(s), inp(**kw)) for s in range(start, end)}


def test_pv_starts_after_120_s_of_enough_surplus() -> None:
    machine = ModeMachine(TIMERS)
    decisions = run(machine, 0, 121)
    assert not any(d.charge for s, d in decisions.items() if s < 120)
    assert decisions[120] == ModeDecision(True, "pv_surplus")


def test_pv_stops_after_300_s_below_but_not_before_600_s_on() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 121)  # Start bei 120
    decisions = run(machine, 121, 721, enough=False)
    assert all(d.charge for s, d in decisions.items() if s < 720)
    assert decisions[720] == ModeDecision(False, "pv_surplus")


def test_restart_waits_for_the_minimum_pause() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 121)
    run(machine, 121, 721, enough=False)  # Stopp bei 720
    decisions = run(machine, 721, 1321)
    assert not decisions[1319].charge and decisions[1320].charge


def test_first_step_takes_over_observed_charging() -> None:  # Review Focus 1
    machine = ModeMachine(TIMERS)
    decisions = run(machine, 0, 301, enough=False, observed=True)
    assert all(d.charge for s, d in decisions.items() if s < 300)
    assert decisions[300] == ModeDecision(False, "pv_surplus")


def test_new_session_in_pv_starts_without_charging() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(plugged=False))
    assert machine.step(at(1), inp(observed=True, new_session=True)) == ModeDecision(False, None)


def test_mode_change_acts_at_once() -> None:
    machine = ModeMachine(TIMERS)
    run(machine, 0, 121)  # PV lädt
    assert not machine.step(at(121), inp(Effective.OFF)).charge
    assert machine.step(at(122), inp(Effective.NOW)).charge
    assert not machine.step(at(123), inp(Effective.PV, enough=False)).charge


def test_complete_stays_stopped() -> None:
    assert not ModeMachine(TIMERS).step(at(0), inp(Effective.NOW, complete=True)).charge


def test_unknown_plug_state_holds_the_decision() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(Effective.NOW))
    assert machine.step(at(1), inp(Effective.NOW, plugged=None)).charge


def test_force_stop_keeps_now_off_for_the_minimum_pause() -> None:
    machine = ModeMachine(TIMERS)
    machine.step(at(0), inp(Effective.NOW))
    machine.force_stop(at(10))
    assert not machine.step(at(609), inp(Effective.NOW)).charge
    assert machine.step(at(610), inp(Effective.NOW)).charge
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_charge_modes.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_charge_modes.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control/charge_modes.py tests/unit/core/control/test_charge_modes.py
git commit -m "feat(core): Lademodi mit Start- und Stopp-Zeiten"
```

---

### Task 5: Eingaben der Laderegelung

**Files:**
- Create: `src/bindaems/core/control/charge_inputs.py`
- Test: `tests/unit/core/control/test_charge_inputs.py`

**Interfaces:**
- Consumes:
  - `Derived`, `number`, `PHASES` aus `core/state/derived.py`
  - `Snapshot`, `Intent`, `CommandKind`, `CommandValue`
- Produces:
  - Konstanten: `EVCS_DISCONNECTED: Final = 0`, `EVCS_CHARGING: Final = 2`, `EVCS_CHARGED: Final = 3`, `TWC_CHARGING_A: Final = 1.0`
  - `@dataclass(frozen=True) class WallboxObs`:
    - `reachable: bool`
    - `connected: bool | None`, `charging: bool | None`, `complete: bool | None`
    - `phase_a: Mapping[Phase, float] | None`, `power_w: float | None`
  - `@dataclass(frozen=True) class VehicleObs`:
    - `reachable: bool`
    - `plugged: bool | None`, `charging: bool | None`, `complete: bool | None`, `asleep: bool | None`, `at_home: bool | None`
    - `actual_current_a: float | None`, `phases: int | None`
  - `NO_VEHICLE_DATA: Final = VehicleObs(False, None, None, None, None, None, None, None)`
  - `@dataclass(frozen=True) class GridObs`:
    - `phase_import_a: Mapping[Phase, float | None]`, `battery_feed_a: Mapping[Phase, float | None]`
    - `grid_w: float | None`, `battery_w: float | None`, `soc_pct: float | None`
  - `@dataclass(frozen=True) class ChargeInputs`:
    - `now`: Uhr der Safety-Schicht; `wall_now`: Wanduhr
    - `grid: GridObs`, `wallboxes: Mapping[str, WallboxObs]`, `vehicles: Mapping[str, VehicleObs]`
    - `present: Mapping[tuple[str, CommandKind], CommandValue]`
    - `controllable: Mapping[str, bool]`, `wakeable: Mapping[str, bool]`
    - `blocked: frozenset[tuple[str, CommandKind]]`
    - `intent: Intent | None`, `accepted_at: datetime | None`
  - `observe(snap, derived, cfg, connected: Callable[[str], bool | None]) -> tuple[GridObs, dict[str, WallboxObs], dict[str, VehicleObs]]`; Werte nur mit Qualität `OK` und als endliche Zahl:
    - **Netz:**
      - `phase_import_a = derived.phase_import_a`, `grid_w = derived.grid_w`
      - `battery_w` und `soc_pct` aus `battery.power_w` und `battery.soc_pct`
      - `battery_feed_a[p] = max(0, out − in) / U_p` aus `vebus.l<n>.ac_out_power_w`, `vebus.l<n>.ac_in_power_w` und `grid.l<n>.voltage_v`. Mit `U_p < 100 V` oder einem fehlenden Wert ist es `None`.
    - **EVCS** (`wallboxes.<k>` vom Typ EVCS):
      - Status `wallbox.<k>.status`: `connected = status ≠ 0`, `charging = status == 2`, `complete = status == 3`; ohne Status alle drei `None`
      - `phase_a = derived.wallbox_phase_a.get(k)`, `power_w = derived.wallbox_w.get(k)`
    - **Wall Connector:**
      - `connected` aus `wallbox.<k>.vehicle_connected` (bool)
      - `charging`: Summe von `wallbox.<k>.l1.current_a` bis `l3` ≥ 1 A; fehlt ein Strom, gilt `contactor_closed`
      - `complete = None`; `phase_a` und `power_w` wie bei der EVCS
    - **Wallbox allgemein:** `reachable = connected(k) is not False`
    - **Fahrzeug mit `tessie`:**
      - `reachable = connected(f"tessie:{k}") is True`
      - aus `vehicle.<k>.*`: `plugged`, `charging = charging_state == "Charging"`, `complete = charging_state == "Complete"`, `asleep = not online`, `at_home`, `actual_current_a`, `phases`
    - **Fahrzeug ohne Tessie:** `NO_VEHICLE_DATA`

- [ ] **Step 1: Failing Tests schreiben**

```python
def obs(cfg, values, connected=lambda source: True):
    snap_ = snap(values)
    return observe(snap_, derive(snap_, cfg), cfg, connected)


def test_battery_feed_per_phase(cfg) -> None:
    grid, _, _ = obs(cfg, {
        "vebus.l1.ac_in_power_w": 500.0, "vebus.l1.ac_out_power_w": 2800.0, "grid.l1.voltage_v": 230.0,
        "vebus.l2.ac_in_power_w": 1000.0, "vebus.l2.ac_out_power_w": 400.0, "grid.l2.voltage_v": 230.0,
        "vebus.l3.ac_in_power_w": 0.0, "vebus.l3.ac_out_power_w": 400.0,
    })
    assert grid.battery_feed_a == {"L1": 10.0, "L2": 0.0, "L3": None}


@pytest.mark.parametrize(("status", "expected"), [
    (0, (False, False, False)), (1, (True, False, False)), (2, (True, True, False)),
    (3, (True, False, True)), (7, (True, False, False)), (None, (None, None, None)),
])
def test_evcs_status_codes(cfg, status, expected) -> None:
    values = {} if status is None else {"wallbox.evcs.status": status}
    _, wallboxes, _ = obs(cfg, values)
    evcs = wallboxes["evcs"]
    assert (evcs.connected, evcs.charging, evcs.complete) == expected


def test_wall_connector_charges_from_one_ampere(cfg) -> None:
    currents = {"wallbox.twc.l1.current_a": 0.4, "wallbox.twc.l2.current_a": 0.3, "wallbox.twc.l3.current_a": 0.3}
    _, wallboxes, _ = obs(cfg, {"wallbox.twc.vehicle_connected": True, **currents})
    assert (wallboxes["twc"].connected, wallboxes["twc"].charging) == (True, True)


def test_tessie_signals(cfg) -> None:
    _, _, vehicles = obs(cfg, {
        "vehicle.tesla.plugged": True, "vehicle.tesla.charging_state": "Complete",
        "vehicle.tesla.online": False, "vehicle.tesla.at_home": True,
    })
    tesla = vehicles["tesla"]
    assert (tesla.plugged, tesla.charging, tesla.complete, tesla.asleep, tesla.at_home) == (
        True, False, True, True, True)
    assert vehicles["egolf"] == NO_VEHICLE_DATA


def test_unreachable_sources(cfg) -> None:
    _, wallboxes, vehicles = obs(cfg, {}, connected=lambda source: source not in ("evcs", "tessie:tesla"))
    assert not wallboxes["evcs"].reachable and wallboxes["twc"].reachable
    assert not vehicles["tesla"].reachable
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_charge_inputs.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces; `core.checks` bleibt tabu (Importvertrag).

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_charge_inputs.py -q && uv run lint-imports`
Expected: PASS, Importverträge eingehalten

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control/charge_inputs.py tests/unit/core/control/test_charge_inputs.py
git commit -m "feat(core): Eingaben der Laderegelung aus Snapshot und abgeleiteten Größen"
```

---

### Task 6: Zuordnung, Ansteckvorgänge, Boost und Übersteuerung

**Files:**
- Create: `src/bindaems/core/control/assignment.py`, `tests/unit/core/control/charge_helpers.py`
- Test: `tests/unit/core/control/test_assignment.py`

**Interfaces:**
- Consumes: `WallboxObs`, `VehicleObs` (Task 5), `Intent`, `VehicleIntent` (Task 1), `Config`
- Produces:
  - `class AssignmentKind(StrEnum)`: `DEFAULT = "default"`, `OVERRIDE = "override"`, `UNCLEAR = "unclear"`
  - `UNCLEAR_AFTER_S: Final = 60.0`, `CLEAR_AFTER_S: Final = 60.0`
  - `@dataclass(frozen=True) class VehicleState`:
    - `wallbox: str | None`, `kind: AssignmentKind`
    - `plugged: bool | None`, `charging: bool | None`
    - `complete: bool`: einmal „geladen“ in diesem Ansteckvorgang
    - `new_session: bool`
    - `boost_until: datetime | None`: nur ein aktiver Boost
  - `@dataclass(frozen=True) class AssignmentState`: `vehicles: Mapping[str, VehicleState]` (Reihenfolge von `config.yaml`), `unclear: bool`
  - `class Assignment`:
    - `__init__(self, cfg: Config, *, started: datetime)`; `started` ist die Wanduhr beim Start der Regelung.
    - `update(self, now, wall_now, intent, accepted_at, wallboxes, vehicles) -> AssignmentState`
  - **Ablauf von `update`** (Präzisierungen 3 bis 5):
    1. **Ansteckvorgänge je Wallbox** aus `WallboxObs.connected` (Präzisierung 3).
    2. **Bindungen** bei einer Version, die der Zuordnung neu ist, für jedes Fahrzeug mit `boost_until` oder `wallbox`:
       - an die Wallbox `wallbox` oder `default_wallbox`
       - an den laufenden Vorgang, wenn sie ein Fahrzeug meldet; sonst an den nächsten
       - Für `accepted_at < started` (oder `accepted_at` `None`) nur an den laufenden; sonst keine Bindung.
       - Eine Bindung endet mit ihrem Vorgang.
    3. **Wallbox je Fahrzeug:** mit gültiger Übersteuerung deren Wallbox (`OVERRIDE`), sonst `default_wallbox` (`DEFAULT`). Ist die Standard-Wallbox durch eine Übersteuerung belegt, ist es `None`.
    4. **Unklar** nach Präzisierung 5. Solange unklar, ist `kind` bei allen Fahrzeugen ohne Übersteuerung `UNCLEAR`.
    5. **Werte je Fahrzeug:**
       - `plugged` nach Präzisierung 4
       - `charging` von der Wallbox, ohne ihren Wert von Tessie
       - `complete` haftet von „geladen“ (Tessie oder EVCS) bis zum Ende des Vorgangs
       - `new_session` bei einem neuen Vorgang an seiner Wallbox
       - `boost_until` nur mit gültiger Bindung und `wall_now < boost_until`

- [ ] **Step 1: Hilfen und failing Tests schreiben**

Dieser Task legt `tests/unit/core/control/charge_helpers.py` an; Task 8 ergänzt sie. Die Vorgaben sind die Annahmen aller Tests der Laderegelung:
- `evcs(**kw) -> WallboxObs`: erreichbar und verbunden, lädt nicht, nicht geladen, `phase_a` 0 A je Phase, 0 W
- `twc(**kw) -> WallboxObs`: erreichbar, kein Fahrzeug, lädt nicht, `complete` `None`, 0 A je Phase, 0 W
- `tesla(**kw) -> VehicleObs`: erreichbar, nicht angesteckt, lädt nicht, nicht geladen, wach, zu Hause, 0 A, 3 Phasen
- `NONE = NO_VEHICLE_DATA`
- `grid(l1=2.0, l2=2.0, l3=2.0, *, feed=(0.0, 0.0, 0.0), grid_w=-4000.0, battery_w=0.0, soc=60.0) -> GridObs`

```python
def update(a, s, *, evcs_obs=None, twc_obs=None, tesla_obs=None, intent=None, accepted_at=T0):
    wallboxes = {"evcs": evcs_obs or evcs(), "twc": twc_obs or twc()}
    vehicles = {"tesla": tesla_obs or tesla(), "egolf": NONE}
    when = T0 + timedelta(seconds=s)
    return a.update(when, when, intent, accepted_at, wallboxes, vehicles)


def boosted(version: int, until: datetime) -> Intent:
    return Intent(version=version, control=True, vehicles={"egolf": VehicleIntent(boost_until=until)})


def test_default_assignment(cfg) -> None:
    state = update(Assignment(cfg, started=T0), 0, twc_obs=twc(connected=True))
    tesla_, egolf = state.vehicles["tesla"], state.vehicles["egolf"]
    assert (tesla_.wallbox, tesla_.kind, tesla_.plugged) == ("twc", AssignmentKind.DEFAULT, True)
    assert (egolf.wallbox, egolf.plugged, state.unclear) == ("evcs", True, False)


def test_new_session_only_after_a_known_unplug(cfg) -> None:
    a = Assignment(cfg, started=T0)
    assert not update(a, 0, evcs_obs=evcs(connected=None)).vehicles["egolf"].new_session
    assert not update(a, 1).vehicles["egolf"].new_session
    update(a, 2, evcs_obs=evcs(connected=False))
    assert update(a, 3).vehicles["egolf"].new_session


def test_unknown_plug_state_keeps_the_session(cfg) -> None:  # Review Focus 3
    a = Assignment(cfg, started=T0)
    intent = boosted(1, T0 + timedelta(hours=2))
    update(a, 0, intent=intent)
    for s in range(1, 31):
        update(a, s, evcs_obs=evcs(connected=None), intent=intent)
    assert update(a, 31, intent=intent).vehicles["egolf"].boost_until == T0 + timedelta(hours=2)


def test_boost_ends_when_unplugged(cfg) -> None:
    a = Assignment(cfg, started=T0)
    v1 = boosted(1, T0 + timedelta(hours=2))
    assert update(a, 0, intent=v1).vehicles["egolf"].boost_until is not None
    update(a, 1, evcs_obs=evcs(connected=False), intent=v1)
    assert update(a, 2, intent=v1).vehicles["egolf"].boost_until is None
    v2 = boosted(2, T0 + timedelta(hours=2))
    assert update(a, 3, intent=v2).vehicles["egolf"].boost_until is not None


def test_boost_set_before_plugging_in_applies_to_the_next_session(cfg) -> None:
    a = Assignment(cfg, started=T0)
    intent = boosted(1, T0 + timedelta(hours=2))
    assert update(a, 0, evcs_obs=evcs(connected=False), intent=intent).vehicles["egolf"].boost_until is None
    assert update(a, 1, intent=intent).vehicles["egolf"].boost_until is not None


def test_restored_intent_binds_only_a_plugged_car(cfg) -> None:
    a = Assignment(cfg, started=T0)
    intent = boosted(1, T0 + timedelta(hours=2))
    earlier = T0 - timedelta(hours=1)
    update(a, 0, evcs_obs=evcs(connected=False), intent=intent, accepted_at=earlier)
    assert update(a, 1, intent=intent, accepted_at=earlier).vehicles["egolf"].boost_until is None


def test_override_moves_the_vehicle_and_frees_its_default(cfg) -> None:
    intent = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(wallbox="evcs")})
    state = update(Assignment(cfg, started=T0), 0, intent=intent)
    assert (state.vehicles["tesla"].wallbox, state.vehicles["tesla"].kind) == ("evcs", AssignmentKind.OVERRIDE)
    assert state.vehicles["egolf"].wallbox is None


def at_the_evcs(a, s, intent=None):
    return update(a, s, twc_obs=twc(connected=False), tesla_obs=tesla(plugged=True, at_home=True),
                  intent=intent)


def test_contradiction_for_60_s_makes_it_unclear(cfg) -> None:
    a = Assignment(cfg, started=T0)
    assert not any(at_the_evcs(a, s).unclear for s in range(60))
    state = at_the_evcs(a, 60)
    assert state.unclear and state.vehicles["egolf"].kind == AssignmentKind.UNCLEAR
    assert state.vehicles["tesla"].plugged is None  # Präzisierung 4
    for s in range(61, 120):
        assert update(a, s).unclear
    assert not update(a, 121).unclear


def test_flapping_contradiction_never_turns_unclear(cfg) -> None:  # Review Focus 3
    a = Assignment(cfg, started=T0)
    for s in range(300):
        state = at_the_evcs(a, s) if (s // 30) % 2 == 0 else update(a, s)
        assert not state.unclear


def test_override_lifts_unclear_at_once(cfg) -> None:
    a = Assignment(cfg, started=T0)
    for s in range(61):
        at_the_evcs(a, s)
    intent = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(wallbox="evcs")})
    assert not at_the_evcs(a, 61, intent).unclear


def test_a_tesla_away_is_no_contradiction(cfg) -> None:
    a = Assignment(cfg, started=T0)
    for s in range(120):
        state = update(a, s, twc_obs=twc(connected=False), tesla_obs=tesla(plugged=True, at_home=False))
    assert not state.unclear


def test_complete_sticks_until_unplugged(cfg) -> None:
    a = Assignment(cfg, started=T0)
    update(a, 0, evcs_obs=evcs(complete=True))
    assert update(a, 1).vehicles["egolf"].complete
    update(a, 2, evcs_obs=evcs(connected=False))
    assert not update(a, 3).vehicles["egolf"].complete
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_assignment.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_assignment.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control/assignment.py tests/unit/core/control/test_assignment.py tests/unit/core/control/charge_helpers.py
git commit -m "feat(core): Zuordnung Auto–Wallbox mit Ansteckvorgängen, Übersteuerung und Hinweis bei Widerspruch"
```

---

### Task 7: Tesla – Start mit Weckerlaubnis, gleitendes Mittel und Weckkontingent

**Files:**
- Modify: `src/bindaems/core/control/actuator.py`, `src/bindaems/core/control/executor.py`, `src/bindaems/core/actuators/tesla.py`
- Create: `src/bindaems/core/control/tesla_policy.py`
- Test: `tests/unit/core/control/test_executor.py`, `tests/unit/core/actuators/test_tesla.py`, `tests/unit/core/control/test_tesla_policy.py`, `tests/unit/core/control/fakes.py` (`FakeWakeActuator`)

**Interfaces:**
- Consumes: `Command.wake` (Task 1), `Capabilities`, `Confirmation.NOT_TAKEN`, `LOCAL_TZ`
- Produces:
  - `@runtime_checkable class WakeStart(Protocol)` in `actuator.py`: `wake_capabilities(self, snap: Snapshot, now: datetime) -> Capabilities`
  - **Ausführer:**
    - `_hand_over` prüft `wake_capabilities` statt `capabilities`, wenn alles zutrifft: `command.wake`, Art `charging`, Wert `True` und der Aktor erfüllt `WakeStart`.
    - `WriteResult.not_taken: bool = False`; `poll` setzt es bei `Confirmation.NOT_TAKEN`.
  - **`TeslaActuator.wake_capabilities(snap, now)`:** wie `capabilities`, nur ohne `REASON_ASLEEP`. Die Gründe stehen in dieser Reihenfolge: Tessie nicht erreichbar, nicht zu Hause, nicht angesteckt, nach Fehlern gesperrt.
  - **`tesla_policy.py`:**
    - `class MovingAverage`: `__init__(window_s)`, `add(now, value: float)`, `value(now) -> float | None` (Mittel der Werte mit `now − t ≤ window_s`), `clear()`
    - `class WakeQuota`:
      - `__init__(interval_s: float, per_day: int)`
      - `allowed(now, wall_now) -> bool`
      - `note(now, wall_now) -> None`
      - `today(wall_now) -> int`
      - Der Abstand läuft auf `now`, der Tag ist `wall_now.astimezone(LOCAL_TZ).date()`.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_executor.py
def test_wake_start_uses_the_wake_capabilities(layer, ctx_live) -> None:
    car = FakeWakeActuator("vehicle.tesla", controllable=False, wakeable=True)
    executor = CommandExecutor(layer, {car.id: car}, sink, labels)
    executor.execute(Command("vehicle.tesla", CommandKind.CHARGING, True, wake=True), ctx_live, SNAP, T0, {})
    assert car.sent == [(CommandKind.CHARGING, True)]


def test_without_wake_a_sleeping_car_is_not_sent(layer, ctx_live) -> None:
    car = FakeWakeActuator("vehicle.tesla", controllable=False, wakeable=True)
    # derselbe Befehl ohne wake → NOT_SENT mit „nicht steuerbar: …“, car.sent == []


def test_wake_counts_only_for_starting(layer, ctx_live) -> None:
    # Command(..., CommandKind.CURRENT, 10.0, wake=True) an denselben Aktor → NOT_SENT


def test_not_taken_is_marked(layer, ctx_live) -> None:
    # FakeActuator, dessen confirm NOT_TAKEN liefert → poll: [WriteResult(..., DecisionResult.ERROR, text, not_taken=True)]


# tests/unit/core/actuators/test_tesla.py
def test_wake_capabilities_ignore_sleep(tesla_actuator) -> None:
    asleep = snap({"vehicle.tesla.online": False, "vehicle.tesla.at_home": True, "vehicle.tesla.plugged": True})
    assert tesla_actuator.wake_capabilities(asleep, T0) == Capabilities(True)
    unplugged = snap({"vehicle.tesla.online": False, "vehicle.tesla.at_home": True, "vehicle.tesla.plugged": False})
    assert tesla_actuator.wake_capabilities(unplugged, T0) == Capabilities(False, ("Fahrzeug nicht angesteckt.",))


# tests/unit/core/control/test_tesla_policy.py
def test_average_over_the_window() -> None:
    average = MovingAverage(90.0)
    for s, value in ((0, 10.0), (30, 16.0), (60, 7.0)):
        average.add(T0 + timedelta(seconds=s), value)
    assert average.value(T0 + timedelta(seconds=60)) == 11.0
    assert average.value(T0 + timedelta(seconds=91)) == 11.5


def test_quota_needs_the_interval_and_counts_per_day() -> None:
    quota = WakeQuota(1800.0, 2)
    quota.note(T0, T0)
    assert not quota.allowed(T0 + timedelta(seconds=1799), T0 + timedelta(seconds=1799))
    later = T0 + timedelta(seconds=1800)
    assert quota.allowed(later, later)
    quota.note(later, later)
    end = T0 + timedelta(hours=2)
    assert not quota.allowed(end, end) and quota.today(end) == 2


def test_quota_resets_at_vienna_midnight() -> None:  # Review Focus 5
    quota = WakeQuota(0.0, 1)
    evening = datetime(2026, 10, 12, 21, 59, tzinfo=UTC)  # 23:59 Wiener Sommerzeit
    quota.note(evening, evening)
    assert not quota.allowed(evening + timedelta(seconds=30), evening + timedelta(seconds=30))
    midnight = evening + timedelta(seconds=60)
    assert quota.allowed(midnight, midnight)


def test_quota_day_survives_the_dst_change() -> None:  # Review Focus 5
    quota = WakeQuota(0.0, 1)
    early = datetime(2026, 10, 24, 22, 30, tzinfo=UTC)  # 00:30 MESZ am 25.10. (25-Stunden-Tag)
    quota.note(early, early)
    late = datetime(2026, 10, 25, 22, 30, tzinfo=UTC)  # 23:30 MEZ am 25.10.
    assert not quota.allowed(late, late)
    next_day = late + timedelta(minutes=30)  # 00:00 MEZ am 26.10.
    assert quota.allowed(next_day, next_day)
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_executor.py tests/unit/core/actuators/test_tesla.py tests/unit/core/control/test_tesla_policy.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces. `FakeWakeActuator` erbt von `FakeActuator`. Sein `wake_capabilities` liefert `Capabilities(wakeable, ())`, sonst `Capabilities(False, ("Fahrzeug nicht angesteckt.",))`.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control src/bindaems/core/actuators/tesla.py tests/unit/core
git commit -m "feat(core): Tesla-Start mit Weckerlaubnis, gleitendes Mittel und Weckkontingent"
```

---

### Task 8: Laderegelung I – Phasenwächter, Modi, EVCS und Überlast

**Files:**
- Create: `src/bindaems/core/control/charging.py`
- Modify: `tests/unit/core/control/charge_helpers.py` (Vorgaben für Eingaben und Urteile)
- Test: `tests/unit/core/control/test_charging.py`

**Interfaces:**
- Consumes:
  - `GuardLimits`, `phase_target`, `phase_loads`, `Claim`, `allocate`, `OverloadGuard`, `OverloadLevel` (Task 2)
  - `surplus_w`, `SurplusCar`, `share_surplus` (Task 3)
  - `Effective`, `MODE_CODES`, `FULL_POWER`, `ModeTimers`, `ModeInput`, `ModeMachine` (Task 4)
  - `ChargeInputs` und die Beobachtungen (Task 5), `Assignment`, `AssignmentKind` (Task 6)
  - `Verdict`, `Outcome`, `SENDABLE`, `WriteResult`, `HardLimits`
- Produces:
  - `@dataclass(frozen=True) class ChargeCommand`: `command: Command`, `inputs: Mapping[str, float | None]`
  - `class ChargeController`:
    - `__init__(self, cfg: Config, limits: HardLimits, *, started: datetime)`
    - `step(self, inputs: ChargeInputs) -> list[ChargeCommand]`
    - `note_verdict(self, command: Command, verdict: Verdict | None, *, not_sent: bool = False) -> None`
    - `note_result(self, result: WriteResult) -> None`
    - `idle(self, now: datetime) -> None`
    - `vehicle_wallbox(self) -> dict[str, str | None]`
    - `health(self) -> list[dict[str, Any]]`
  - **Ladepunkt eines Fahrzeugs** an seiner Wallbox w (aus der Zuordnung):
    - **EVCS:**
      - Aktor `wallbox.<w>`
      - Phasen `phase_map[:phases des Fahrzeugs]`, solange unklar alle drei
      - Strom von `max(evcs.min_a, v.min_a)` bis `min(evcs.max_a, v.max_a)`, solange unklar der Bereich der EVCS
    - **Wall Connector und Fahrzeug mit Tessie:** Aktor `vehicle.<v>`, Phasen `phase_map[:phases]`, Strom von `v.min_a` bis `min(v.max_a, twc.max_a)`
    - **Wall Connector ohne Tessie:** kein Aktor, nur Last
  - **Ablauf von `step`** (Design 4 bis 6, Präzisierungen 4 bis 11):
    1. Erster Schritt oder nach `idle`: letzte eigene Werte vergessen, die Modi bekommen `resumed`.
    2. `Assignment.update`. Modus je Fahrzeug aus `intent.vehicles` (sonst `VehicleIntent()`), Boost bei aktivem `boost_until`. Reihenfolge `intent.priority` oder die von `config.yaml`.
    3. Ladepunkte, Geregelt-Status und Zuschläge (Präzisierung 6). Dazu `phase_loads` und `OverloadGuard.update` mit `grid.phase_import_a`.
    4. Überschuss:
       - `cars_w` = Summe `power_w` aller Wallboxen, mit einem fehlenden Wert `None`
       - `surplus_w(...)`, dann `share_surplus` über die geregelten Ladepunkte
       - `holding_min` hat ein PV-Auto, das laut Modus lädt.
    5. Claims in Reihenfolge der Priorität:
       - **Ziel:** `phase_target`
       - **Wunsch:**
         - Sofort und Boost: `max`
         - PV: der Anteil, ohne Wert 0
         - Min+PV: der Anteil, mindestens `min`
         - Aus: 0
       - **Halten:** der größte gemessene Strom
       - dann `allocate`
    6. Modi: `enough = Anteil ≥ min und free_a ≥ min`; fehlt eins davon, ist es `None`.
    7. Überlast:
       - ab `MINIMUM`: Strom = `min`, dringend, Grund `overload`
       - `EVCS_STOP` bei EVCS-Ladepunkten bzw. `ALL_STOP` bei allen: `force_stop` und „Laden aus“, dringend, `overload`
    8. **Strom, solange geladen werden soll:** `clamp(min(Wunsch, floor(free_a)), min, max)`; mit `free_a` `None` ist es `min`. Er ist dringend nach Präzisierung 11.
    9. **Befehle nur für geregelte Ladepunkte,** in Reihenfolge der Priorität und nur für Arten, die nicht in `blocked` stehen. Ein nicht steuerbarer Aktor bekommt nichts, außer dem Weckstart (Task 9):
       - **Start:** erst der Strom (wenn geändert), dann `charging=True`
       - **Laden:** der Strom, wenn geändert
       - **Nicht laden:** `charging=False`, wenn geändert, ohne Strom
       - Bei `plugged` `None` kein „Laden an/aus“ und der Strom nur gesenkt.
    10. **Gründe:**
        - erst `MODE_CODES[Modus]`
        - dann `pv_surplus`, wenn der Wert aus dem Anteil kommt oder `ModeDecision.reason` es nennt
        - sonst `GuardCause`, wenn der Wächter kappt
        - dazu `overload`, `wake`, `not_taken_retry`, `assignment_unclear` bzw. `resume_after_outage`
    11. **Eingaben** je Befehl: `surplus_w`, `limit_a` (`free_a`), `actual_current_a` und `actual_charging` (1.0, 0.0 oder `None`) aus `present`.
  - **„Nur Änderungen“** nach Präzisierung 10. `note_result` löscht den letzten Wert nur, wenn er dem Ergebnis entspricht (Befehle des Keepers bleiben unberührt).

- [ ] **Step 1: Hilfen und failing Tests schreiben**

`charge_helpers.py` (aus Task 6) bekommt dazu:
- `inputs(cfg, *, s=0, grid_obs=None, evcs_obs=None, twc_obs=None, tesla_obs=None, present=None, controllable=None, wakeable=None, blocked=frozenset(), intent=None, accepted_at=T0)`:
  - Beobachtungen ohne Angabe: `grid()`, `evcs()`, `twc()`, `tesla()`, für den e-Golf `NONE`
  - Zeit `T0 + s` auf beiden Uhren
  - `present`: EVCS 6 A und Laden aus, Tesla 16 A und Laden aus
  - `controllable`: `{"wallbox.evcs": True, "vehicle.tesla": False}`, `wakeable` leer
  - `intent`: `Intent(version=1, control=True)`
- `accept(controller, commands, outcome=Outcome.ACCEPTED)`: gibt jedem Befehl ein Dry-Run-Urteil mit seinem Wert.
- `run(controller, cfg, seconds, **kw) -> list[tuple[int, ChargeCommand]]`: ein Schritt je Sekunde. `kw` sind feste Eingaben oder Funktionen der Sekunde; alle Befehle werden angenommen.

```python
def cmd(t: int, kind, value, reasons, *, urgent=False):
    return (t, Command("wallbox.evcs", kind, value, urgent=urgent, reasons=reasons))


def as_commands(found):
    return [(t, c.command) for t, c in found]


def test_pv_start_after_120_s_sets_current_then_charging(cfg) -> None:
    found = run(ChargeController(cfg, HardLimits.from_config(cfg), started=T0), cfg, 121)
    assert as_commands(found) == [
        cmd(0, CommandKind.CHARGING, False, ("mode_pv",)),
        cmd(120, CommandKind.CURRENT, 8.0, ("mode_pv", "pv_surplus")),
        cmd(120, CommandKind.CHARGING, True, ("mode_pv", "pv_surplus")),
    ]
    assert found[1][1].inputs == {"surplus_w": 3900.0, "limit_a": 17.0,
                                  "actual_current_a": 6.0, "actual_charging": 0.0}


def test_pv_follows_the_surplus(cfg) -> None:
    # nach dem Start: evcs(charging=True, phase_a={"L1": 8.0, "L2": 8.0, "L3": 0.0}, power_w=3680.0),
    # grid(grid_w=-1380.0) → Strom 10.0 mit ("mode_pv", "pv_surplus")


def test_import_limit_caps_the_current(cfg) -> None:
    # PV lädt mit 10 A (phase_a L1/L2 10 A), grid(l1=18.0, l2=12.0, grid_w=-6000.0),
    # power_w 4600 → Anteil 16, frei min(19−8, 19−2) = 11 → 11.0 mit ("mode_pv", "import_limit")
    # mit l1=25.0 → frei 4 < 6 → 6.0 (Mindeststrom), dieselben Gründe


def test_now_is_lowered_urgently_at_the_fuse(cfg) -> None:
    # egolf now, lädt mit 16 A (phase_a L1/L2 16 A), present 16 A, grid(l1=36.0, l2=20.0)
    # → Strom 13.0, urgent=True, ("mode_now", "phase_limit")


def test_reserve_guard_limits_now_to_the_import_target(cfg) -> None:
    # wie oben mit soc=24.0 → Strom 6.0, urgent=False, ("mode_now", "reserve_guard")


def test_off_stops_at_once(cfg) -> None:
    # egolf off, evcs(charging=True), present Laden an → nur charging=False mit ("mode_off",)


def test_min_pv_keeps_its_minimum_without_surplus(cfg) -> None:
    # egolf min_pv, grid(grid_w=2000.0) → Schritt 0: Strom 6.0 und charging=True, ("mode_min_pv", "pv_surplus")


def test_unknown_surplus_holds_the_minimum_then_stops(cfg) -> None:
    # PV lädt (Übernahme), grid(grid_w=None) → Strom 6.0 mit ("mode_pv",), charging=False bei 300 s
    # mit ("mode_pv", "pv_surplus")


def test_unknown_evcs_status_holds_still(cfg) -> None:  # Review Focus 3
    found = run(ChargeController(cfg, HardLimits.from_config(cfg), started=T0), cfg, 200,
                evcs_obs=evcs(connected=None, charging=None, complete=None))
    assert found == []


def test_only_changes_are_sent(cfg) -> None:
    found = run(ChargeController(cfg, HardLimits.from_config(cfg), started=T0), cfg, 100)
    assert [t for t, _ in found] == [0]


def test_deferred_is_offered_again_and_unchanged_counts_as_sent(cfg) -> None:
    controller = ChargeController(cfg, HardLimits.from_config(cfg), started=T0)
    first = controller.step(inputs(cfg))
    accept(controller, first, Outcome.DEFERRED)
    again = controller.step(inputs(cfg, s=1))
    assert [c.command for c in again] == [c.command for c in first]
    accept(controller, again, Outcome.UNCHANGED)
    assert controller.step(inputs(cfg, s=2)) == []


def test_a_failure_offers_the_value_again(cfg) -> None:
    # nach dem Start bei 120: note_result(WriteResult("wallbox.evcs", CURRENT, 8.0, ERROR, "x"))
    # → Schritt 121 bietet Strom 8.0 wieder an


def test_blocked_kind_waits_and_follows_after_result(cfg) -> None:  # Review Focus 2
    # PV lädt seit 120; bei 130 Absicht v2 egolf off mit blocked={("wallbox.evcs", CHARGING)}
    # → kein Befehl; bei 131 ohne blocked → charging=False mit ("mode_off",)


def test_not_controllable_gets_nothing(cfg) -> None:
    found = run(ChargeController(cfg, HardLimits.from_config(cfg), started=T0), cfg, 200,
                controllable={"wallbox.evcs": False, "vehicle.tesla": False})
    assert found == []


def test_overload_sets_the_minimum_then_stops_the_evcs(cfg) -> None:
    # egolf now, lädt mit 16 A, grid(l1=34.0, l2=20.0) für 7 s
    # → bei 3: Strom 6.0, urgent=True, ("mode_now", "overload")
    # → bei 6: charging=False, urgent=True, ("mode_now", "overload")


def test_vehicle_without_entry_charges_in_pv(cfg) -> None:  # Review Focus 4
    intent = Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW)})
    found = run(ChargeController(cfg, HardLimits.from_config(cfg), started=T0), cfg, 121, intent=intent)
    assert [(t, c.command.kind) for t, c in found if c.command.actuator == "wallbox.evcs"] == [
        (0, CommandKind.CHARGING), (120, CommandKind.CURRENT), (120, CommandKind.CHARGING)]


def test_unplugged_car_gets_charging_off_once(cfg) -> None:
    # evcs(connected=False), present Laden an → nur bei 0: charging=False mit ("mode_pv",)


def test_health_per_vehicle(cfg) -> None:
    controller = ChargeController(cfg, HardLimits.from_config(cfg), started=T0)
    accept(controller, controller.step(inputs(cfg)))
    assert controller.health()[1] == {
        "id": "egolf", "name": "e-Golf", "mode": "pv", "boost_until": None, "wallbox": "evcs",
        "assignment": "default", "limit_a": 17.0, "plugged": True, "charging": False,
        "wakes_today": 0, "reason": "mode_pv",
    }
```

Die Tests mit Kommentar bekommen ihren Rumpf nach demselben Muster wie die ausgeschriebenen: Eingaben über `inputs`/`run`, Befehle als `(Sekunde, Command)` vergleichen, mit genau den genannten Werten und Gründen.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_charging.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces. `health()` liefert je Fahrzeug in der Reihenfolge von `config.yaml`:
  - `id`, `name`
  - `mode` (Wert von `Effective`), `boost_until` (ISO oder `None`)
  - `wallbox`, `assignment` (Wert von `AssignmentKind`)
  - `limit_a` (`free_a`), `plugged`, `charging`
  - `wakes_today` (bis Task 9: 0)
  - `reason`: der letzte Grund des letzten Befehls für das Fahrzeug, sonst `None`

  Die Tesla-Besonderheiten (Task 9) fehlen noch: Der Tesla-Ladepunkt nimmt vorerst den Strom wie die EVCS.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control -q && uv run lint-imports`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control/charging.py tests/unit/core/control
git commit -m "feat(core): Laderegelung – Phasenwächter, Modi, EVCS und Überlast"
```

---

### Task 9: Laderegelung II – Tesla, Zuordnung und Wiederaufnahme

**Files:**
- Modify: `src/bindaems/core/control/charging.py`
- Test: `tests/unit/core/control/test_charging_tesla.py`

**Interfaces:**
- Consumes: `MovingAverage`, `WakeQuota` (Task 7), `ChargeController` (Task 8)
- Produces (Design 7, 8, 9; Präzisierungen 5, 6 und 12):
  - **Tesla-Ladepunkt** (`vehicle.<v>` am Wall Connector):
    - Je Fahrzeug ein `MovingAverage(control.tesla_average_s)` über den Wunsch und ein `SlidingMin(control.tesla_caution_s)` über `free_a`; ohne Wert zählt 0.
    - Strom nach Präzisierung 12. Dringend nur bei `PHASE_LIMIT` und Überlast.
  - **Wecken:**
    - Wann: Der Modus will starten, der Aktor ist nicht steuerbar, aber in `wakeable`, und `WakeQuota.allowed`.
    - Dann `Command(..., CHARGING, True, wake=True)`, Gründe `(Modus, …, "wake")`, ohne Strom.
    - Je Fahrzeug ein `WakeQuota(wake.interval_s, wake.per_day)`.
    - `note_verdict` zählt einen Weckstart mit Urteil aus `SENDABLE`.
  - **Nach `not_taken`:** Der nächste Strombefehl dieser Art trägt zusätzlich `not_taken_retry`.
  - **Tesla an der EVCS** (Übersteuerung): einmal je Ansteckvorgang `vehicle.<v>` Strom `v.max_a` mit dem Modus als Grund, sobald steuerbar. Erledigt mit einem Urteil mit Wert oder `UNCHANGED`. Geregelt wird danach die EVCS mit den Werten des Tesla (3 Phasen).
  - **Unklar:**
    - keine Befehle an Fahrzeuge mit Tessie
    - EVCS ohne „Laden an/aus“; der Strom nur gesenkt, wenn `floor(free_a)` unter dem Ist-Strom liegt, nie unter `min`
    - Gründe `(Modus, "assignment_unclear", Ursache)`
  - **Wiederaufnahme:**
    - Nach `idle` trägt die erste Startfolge (Strom und `charging=True`) je Fahrzeug zusätzlich `resume_after_outage`.
    - Gleitendes Mittel und Fenster beginnen neu.
  - `vehicle_wallbox()`: `{vehicle_actuator(v): Wallbox}` für Fahrzeuge mit Tessie, solange unklar `None`.
  - `health()`: `wakes_today` aus dem Kontingent.

- [ ] **Step 1: Failing Tests schreiben**

```python
TWC_ON = twc(connected=True)


def tesla_inputs(cfg, s, **kw):
    kw.setdefault("controllable", {"wallbox.evcs": True, "vehicle.tesla": True})
    kw.setdefault("tesla_obs", tesla(plugged=True))
    kw.setdefault("twc_obs", TWC_ON)
    kw.setdefault("evcs_obs", evcs(connected=False))
    return inputs(cfg, s=s, **kw)


def currents(found, actuator="vehicle.tesla"):
    return [(t, c.command.value) for t, c in found
            if c.command.actuator == actuator and c.command.kind == CommandKind.CURRENT]


def test_tesla_current_follows_the_90_s_average(cfg) -> None:
    def grid_at(t: int):
        return grid(grid_w=-7000.0 if t < 60 else -11200.0)
    found = run(ChargeController(cfg, HardLimits.from_config(cfg), started=T0), cfg, 90,
                make=tesla_inputs, grid_obs=grid_at,
                tesla_obs=tesla(plugged=True, charging=True))
    assert currents(found) == [(0, 10.0), (71, 11.0), (89, 12.0)]


def test_tesla_gets_the_lowest_free_value_of_60_s(cfg) -> None:
    # tesla now, lädt mit 16 A (TWC 16 A je Phase), present 16 A;
    # grid(l1=20.0), bei Sekunde 10 einmal grid(l1=41.0)
    # → currents == [(0, 16.0), (10, 8.0), (71, 16.0)], der Befehl bei 10 dringend
    # mit ("mode_now", "phase_limit"), der bei 71 normal mit ("mode_now",)


def test_a_sleeping_tesla_is_woken_for_a_start_within_its_quota(cfg) -> None:
    # tesla now, tesla(plugged=True, asleep=True), controllable Tesla False, wakeable {"vehicle.tesla": True}
    # → bei 0: Command("vehicle.tesla", CHARGING, True, reasons=("mode_now", "wake"), wake=True)
    # → bei 5: note_result(ERROR); Sekunden 6 bis 1799: nichts; bei 1800: erneut der Weckstart


def test_no_wake_in_pv_without_enough_surplus(cfg) -> None:
    # PV, schlafend, grid(grid_w=0.0) für 300 s → keine Befehle an vehicle.tesla


def test_not_taken_is_requested_again_with_its_reason(cfg) -> None:
    # PV, lädt mit 10 A, present 10 A, grid(grid_w=-3700.0) → bei 0: Strom 5.0 ("mode_pv", "pv_surplus");
    # note_result(WriteResult("vehicle.tesla", CURRENT, 5.0, ERROR,
    #   "Fahrzeug übernahm 5 A nicht (Tessie meldet 10 A).", not_taken=True))
    # → bei 1: Strom 5.0 mit ("mode_pv", "pv_surplus", "not_taken_retry")


def test_tesla_at_the_evcs_gets_its_maximum_once(cfg) -> None:
    # Absicht tesla wallbox evcs, mode now; evcs() verbunden; tesla(plugged=True); beide steuerbar
    # → bei 0: vehicle.tesla Strom 16.0 ("mode_now",), dann wallbox.evcs Strom 16.0 und charging=True;
    #   danach 60 s nichts mehr


def test_unclear_pauses_the_tesla_and_holds_the_evcs(cfg) -> None:
    # egolf now; twc(connected=False), tesla(plugged=True), evcs(charging=True, phase_a 10 A je Phase),
    # present EVCS 10 A und Laden an
    # → ab 60: keine Befehle an vehicle.tesla, kein „Laden an/aus“ an die EVCS;
    # → bei 61 mit grid(l1=36.0): Strom 7.0, dringend, ("mode_now", "assignment_unclear", "phase_limit")


def test_resume_restarts_now_at_once(cfg) -> None:
    # egolf now, lädt; Schritte 0..9; idle(T0 + 10 s); bei 130 evcs(charging=False), present Laden aus
    # → Strom 16.0 und charging=True, beide mit ("mode_now", "resume_after_outage")


def test_resume_in_pv_waits_for_start_after(cfg) -> None:
    # PV mit Überschuss, idle bei 10, ab 130 wieder Schritte → Start bei 250 mit
    # ("mode_pv", "pv_surplus", "resume_after_outage")


def test_vehicle_wallbox_for_the_safety_layer(cfg) -> None:
    # Standard → {"vehicle.tesla": "twc"}; Übersteuerung auf evcs → {"vehicle.tesla": "evcs"};
    # unklar → {"vehicle.tesla": None}


def test_tesla_without_tessie_is_load(cfg) -> None:
    # Tesla weder steuerbar noch weckbar, TWC lädt mit 10 A je Phase, grid(l1=22.0, l2=22.0, l3=12.0,
    # grid_w=-8000.0), egolf PV lädt mit 10 A (phase_a L1/L2) → Strom 7.0 mit ("mode_pv", "import_limit")


def test_unreachable_evcs_reserves_its_safe_current(cfg) -> None:
    # evcs(reachable=False, connected=None, phase_a=None), tesla now lädt mit 16 A (TWC 16 A je Phase),
    # grid(l1=18.0, l2=18.0, l3=18.0, soc=20.0) → Tesla-Strom 11.0 mit ("mode_now", "reserve_guard")
    # (2 A Last + 6 A Zuschlag der EVCS, Ziel 19 A)


def test_wall_connector_outage_takes_the_current_from_tessie(cfg) -> None:
    # twc(reachable=False, connected=None, phase_a=None),
    # tesla(plugged=True, charging=True, actual_current_a=16.0, phases=3), tesla now,
    # grid(l1=30.0, l2=30.0, l3=30.0, soc=20.0) → der Tesla bleibt geregelt: Last 14 A, frei 5 A
    # → Tesla-Strom 5.0 mit ("mode_now", "reserve_guard")
```

`run` nimmt dafür ein Argument `make`, das die Eingaben baut (Standard `inputs`).

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_charging_tesla.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control -q && uv run coverage run -m pytest tests/unit/core/control -q && uv run coverage report --include="src/bindaems/core/control/*" --fail-under=90`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control/charging.py tests/unit/core/control
git commit -m "feat(core): Laderegelung – Tesla, Zuordnung und Wiederaufnahme"
```

---

### Task 10: Laufzeit – die Laderegelung im Regelzyklus

**Files:**
- Modify: `src/bindaems/core/runtime.py`, `src/bindaems/core/control/context.py`, `src/bindaems/shared/decisions.py`, `src/bindaems/core/api/app.py`
- Test: `tests/integration/test_runtime_charging.py`, `tests/unit/core/control/test_context.py`, `tests/unit/shared/test_decisions.py`, `tests/unit/core/api/test_api.py`

**Interfaces:**
- Consumes: `ChargeController`, `ChargeCommand` (Tasks 8, 9), `observe`, `ChargeInputs` (Task 5), `WakeStart` (Task 7)
- Produces:
  - **`CoreRuntime.__init__`:**
    - zusätzlich `http_client: httpx.AsyncClient | None = None`; ein übergebener Client ist der gemeinsame (`_http_client`)
    - `self.charger = ChargeController(cfg, self.limits, started=self._clock.now())`
  - **`_readback`:** gibt jedes Ergebnis von `poll` auch an `charger.note_result`.
  - **`_control`** (Design 4.2):
    1. Fähigkeiten aller Aktoren und `wake_capabilities` der `WakeStart`-Aktoren berechnen.
    2. Befehle des Keepers berechnen.
    3. In CONTROL oder DEGRADED mit `intent.control`:
       - `ChargeInputs` bauen. `blocked` = offene Schreibvorgänge ∪ (Aktoren in Übergabe × alle Arten).
       - `charger.step` aufrufen.
    4. Sonst `charger.idle(snow)`.
    5. Ein `SafetyContext` für alle Befehle, mit `vehicle_wallbox=charger.vehicle_wallbox()`.
    6. Erst die Befehle des Keepers ausführen, dann die der Regelung, mit `decision_inputs(...) | command.inputs`.
    7. Je Befehl der Regelung `charger.note_verdict(command, verdict, not_sent=…)`. `not_sent` heißt: `verdict.send` und kein offener Schreibvorgang danach.
    8. Ein live gesendeter Befehl macht den Aktor owned, wie beim Keeper.
  - **`safety_context(..., vehicle_wallbox: Mapping[str, str | None] = MappingProxyType({}))`**
  - **`_REASON_TEXT`:** alle Codes aus „Texte“
  - **Health:** `HealthReport.vehicles: list[dict[str, Any]] = Field(default_factory=list)`; der Zyklus legt `charger.health()` für `health()` ab.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/integration/test_runtime_charging.py
# Überschuss 5000 W: 4500 W Einspeisung + 600 W Laden des Akkus (SOC 50 % ≥ 30 %) − 100 W Marge
SURPLUS = {**{f"grid.l{n}.power_w": -1500.0 for n in (1, 2, 3)}, "grid.power_w": -4500.0}
EGOLF_PLUGGED = GOOD_STATE | {"wallbox.evcs.status": 1, "wallbox.evcs.gx_set_current_a": 6,
                              "wallbox.evcs.gx_start_stop": 0, "wallbox.evcs.gx_current_a": 0}


def charging_decisions(rt):
    decisions, _ = rt.decisions(T0 - timedelta(days=1), 1000)
    return [d for d in decisions if not set(d.codes) & {"safe_state", "handback", "grid_outage"}]


async def test_dry_run_decides_only_its_own_changes(runtime_env) -> None:
    rt, _, clock = runtime_env(state=EGOLF_PLUGGED, extra=SURPLUS)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 32 + 125)
    found = [(d.actuator, d.kind, d.requested, d.result, d.codes) for d in charging_decisions(rt)]
    assert found == [
        ("wallbox.evcs", CommandKind.CURRENT, 10.0, DecisionResult.DRY_RUN, ("mode_pv", "pv_surplus")),
        ("wallbox.evcs", CommandKind.CHARGING, True, DecisionResult.DRY_RUN, ("mode_pv", "pv_surplus")),
    ]
    # ein fremder Regler verstellt den Sollstrom: keine weitere Entscheidung
    rt.store.update("wallbox.evcs.gx_set_current_a", 14, source="victron", kind=SignalKind.STATE)
    run(rt, clock, 30)
    assert len(charging_decisions(rt)) == 2


async def test_decision_inputs_carry_surplus_and_actual_value(runtime_env) -> None:
    # wie oben: inputs der Strom-Entscheidung: surplus_w 5000.0, actual_current_a 6.0,
    # actual_charging 0.0, limit_a > 0, dazu soc_pct, battery_w und grid_l1_a bis grid_l3_a


async def test_live_evcs_follows_the_surplus(runtime_env) -> None:
    # live_evcs mit Plant (Watchdog wartet), Zustand EGOLF_PLUGGED und SURPLUS → nach dem Scharfschalten
    # und 120 s: plant.writes enthält ("evcharger/40/SetCurrent", 10) vor ("evcharger/40/StartStop", 1)


async def test_restart_while_charging_does_not_stop(runtime_env) -> None:  # Review Focus 1
    rt, _, clock = runtime_env(state=EGOLF_PLUGGED | {"wallbox.evcs.status": 2, "wallbox.evcs.gx_start_stop": 1})
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 32 + 290)
    stops = [d for d in charging_decisions(rt) if d.kind == CommandKind.CHARGING and d.requested is False]
    assert stops == []
    run(rt, clock, 20)
    assert [d.codes for d in charging_decisions(rt) if d.kind == CommandKind.CHARGING] == [("mode_pv", "pv_surplus")]


async def test_no_charging_commands_without_control(runtime_env) -> None:
    # Regelbetrieb aus, sonst wie oben → keine Entscheidungen der Regelung


async def test_handback_gets_no_charging_commands(runtime_env) -> None:
    # EVCS live und owned, dann Absicht v2 mit EVCS im Dry-Run bei Regelbetrieb an → während der Übergabe
    # nur Entscheidungen mit "handback"


async def test_health_lists_vehicles(runtime_env) -> None:
    # nach CONTROL: [v["id"] for v in rt.health().vehicles] == ["tesla", "egolf"],
    # egolf: plugged True, assignment "default"


# tests/unit/core/control/test_context.py
def test_vehicle_wallbox_reaches_the_context() -> None:
    # safety_context(..., vehicle_wallbox={"vehicle.tesla": "twc"}).vehicle_wallbox == {"vehicle.tesla": "twc"}


# tests/unit/shared/test_decisions.py
def test_charging_reasons_have_texts() -> None:
    text = decision_text("EVCS „evcs“", CommandKind.CURRENT, 8.0, DecisionResult.DRY_RUN,
                         reasons=("mode_pv", "pv_surplus"))
    assert text == "EVCS „evcs“ (PV-Überschuss, Überschuss): Strom 8 A – Dry-Run, nicht gesendet."
```

`run`, `GOOD_STATE`, `Plant` und `live_evcs` kommen aus `tests/integration/test_runtime_control.py` bzw. `plant.py`; gemeinsam Genutztes zieht in `tests/integration/conftest.py` um.

Die bestehenden Laufzeittests setzen keinen EVCS-Status. Die Regelung hält dort still (Präzisierung 4) und darf sie nicht ändern.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/integration/test_runtime_charging.py tests/unit/core/control/test_context.py tests/unit/shared/test_decisions.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces. `_control` bekommt `derived` schon heute; `observe` braucht zusätzlich `self.store.connected`.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/integration tests/unit/core -q`
Expected: PASS. Ändert sich `ui/src/lib/api/contract/system.json`, die Vertragsdateien mit `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py` neu schreiben und `cd ui && pnpm check && pnpm test` laufen lassen.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests ui/src/lib/api/contract
git commit -m "feat(core): Laderegelung im Regelzyklus, Health je Fahrzeug und Eingaben der Entscheidungen"
```

---

### Task 11: Laufzeit – die geparkten Befunde aus 2b

**Files:**
- Modify: `src/bindaems/core/runtime.py`, `src/bindaems/core/watchdog_client/heartbeat.py`, `tests/integration/conftest.py`
- Test: `tests/unit/core/watchdog_client/test_heartbeat.py`, `tests/integration/test_runtime_charging.py`

**Interfaces:**
- Consumes: `HeartbeatClient`, `WatchdogState`, `RELEASE`, `HEARTBEAT_MAX`
- Produces (Design 10):
  1. **Freigabe 0 erst ohne offenen Schreibvorgang:** `release_allowed` verlangt zusätzlich, dass `executor.pending()` keinen Aktor aus `_needs_watchdog` enthält.
  2. **Aufräumen auch nach einem gescheiterten Zyklus:** `_prune_decisions` läuft im `finally` von `cycle_once` nach dem Flush, mit `self._clock.now()` und `self._safety_clock.now()`.
  3. **Heartbeat-Zähler:**
     - `heartbeat_start(now: datetime) -> int = 1_000_000 + int(now.timestamp()) % 1_000_000_000`
     - `CoreRuntime.__init__(..., heartbeat_first: int | None = None)`; ohne Angabe `heartbeat_start(self._clock.now())`
     - `HeartbeatClient.on_cycle` leert `recent` nach einer bestätigten Freigabe: Status `released` und Watchdog `WAITING` oder Echo 0.
     - `runtime_env` setzt `heartbeat_first=1`, damit die bestehenden Tests ihre Zähler 1, 2, … behalten.
  4. **SAFE ohne schlafenden, abwesenden oder abgesteckten Tesla:** `_safe_targets` lässt einen Tesla-Aktor aus, wenn `vehicle.<k>.online`, `at_home` oder `plugged` mit Qualität `OK` `False` ist.
  5. **Auslösung des Watchdogs:** Meldet er `TRIPPED`, ruft `_update_operation` für jeden Aktor aus `_needs_watchdog` `_forget` auf.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/watchdog_client/test_heartbeat.py
def test_start_value_differs_per_run() -> None:
    noon = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
    assert heartbeat_start(noon) == 792_806_400
    assert heartbeat_start(noon + timedelta(seconds=1)) == 792_806_401
    assert 1_000_000 <= heartbeat_start(datetime(2100, 1, 1, tzinfo=UTC)) <= HEARTBEAT_MAX


def test_confirmed_release_forgets_own_counters() -> None:
    sent: list[int] = []
    client = HeartbeatClient(lambda v: sent.append(v) or True, 10.0, first=5)
    for s in (0, 10, 20):
        client.on_cycle(T0 + timedelta(seconds=s), required=True)
    assert client.recent == (5, 6, 7)
    client.on_cycle(T0 + timedelta(seconds=30), required=False, watchdog=WatchdogState.ARMED, echo=7)
    assert client.recent == (5, 6, 7)  # Freigabe gesendet, noch nicht bestätigt
    client.on_cycle(T0 + timedelta(seconds=31), required=False, watchdog=WatchdogState.WAITING, echo=0)
    assert client.recent == ()


# tests/integration/test_runtime_charging.py
async def test_release_waits_for_pending_writes(runtime_env, monkeypatch) -> None:
    # wie test_control_off_hands_back_then_observe (2b), aber executor.pending liefert bis zur
    # Sekunde 20 nach der Übergabe {("wallbox.evcs", CommandKind.CURRENT)}
    # → keine 0 vorher, danach die 0


async def test_prune_runs_after_a_failed_cycle(runtime_env, monkeypatch) -> None:
    rt, _, _ = runtime_env()
    pruned: list[datetime] = []
    monkeypatch.setattr(rt.decision_spool, "prune", pruned.append)
    monkeypatch.setattr(rt, "_record_cycle", lambda *a: (_ for _ in ()).throw(RuntimeError("Test")))
    with pytest.raises(RuntimeError):
        rt.cycle_once()
    assert len(pruned) == 1


async def test_default_heartbeat_starts_with_the_run_value(cfg, tmp_path) -> None:
    # CoreRuntime ohne heartbeat_first, Uhr bei 2026-10-12 12:00 UTC → erster Zähler 792_806_400


async def test_safe_state_skips_a_sleeping_tesla(runtime_env) -> None:
    # Tesla live (allow_tesla_live, Tessie-Token, Quelle "tessie:tesla" verbunden),
    # vehicle.tesla.online False, at_home True, plugged True; Cerbo-Daten veraltet → SAFE
    # → keine Entscheidung für vehicle.tesla, kein Alarm actuator.failed.vehicle.tesla


async def test_watchdog_trip_forgets_the_expectations(runtime_env) -> None:
    # wie test_foreign_change_locks_the_evcs (2b) bis zur Erwartung 16 A; dann Watchdog-Zustand 2
    # und gx_set_current_a 6 → 40 s später kein Alarm competitor.foreign.wallbox.evcs
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/watchdog_client tests/integration/test_runtime_charging.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core tests/integration -q`
Expected: PASS, auch die bestehenden Heartbeat-Tests

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core tests
git commit -m "fix(core): Freigabe erst ohne offenen Schreibvorgang, Heartbeat-Zähler je Lauf, SAFE ohne schlafenden Tesla"
```

---

### Task 12: Selbstprüfung von Multi-Modus und Importlimit

**Files:**
- Modify: `src/bindaems/core/checks/selfcheck.py`
- Test: `tests/unit/core/checks/test_selfcheck.py`, `tests/unit/core/safety/test_locks.py`

**Interfaces:**
- Consumes: `CheckResult`, `number`, `fmt`, `lock_causes`
- Produces:
  - `_multi_mode(snap) -> CheckResult` und `_import_limit(snap, cfg) -> CheckResult` mit den Texten aus „Texte“
  - `run_selfcheck` reiht sie hinter `min_soc` und vor die EVCS-Modi ein.

- [ ] **Step 1: Failing Tests schreiben**

```python
@pytest.mark.parametrize(("mode", "expected"), [
    (3, ("ok", "Multi auf „Ein“.")),
    (1, ("warn", "Multi nicht auf „Ein“ (Modus 1); das Importlimit wirkt nicht.")),
    (None, ("unknown", "Multi-Modus nicht verfügbar.")),
])
def test_multi_mode(cfg, mode, expected) -> None:
    values = {} if mode is None else {"vebus.mode": mode}
    result = next(r for r in run_selfcheck(snap(values), cfg, lambda s: None, T0) if r.id == "multi_mode")
    assert (result.status, result.message) == expected


@pytest.mark.parametrize(("limit", "expected"), [
    (20, ("ok", "Importlimit am Cerbo 20 A wie konfiguriert.")),
    (20.4, ("ok", "Importlimit am Cerbo 20.4 A wie konfiguriert.")),
    (25, ("warn", "Importlimit am Cerbo 25 A, konfiguriert 20 A.")),
    (-1, ("warn", "Importlimit am Cerbo aus.")),
    (None, ("warn", "Importlimit am Cerbo nicht verfügbar.")),
])
def test_import_limit(cfg, limit, expected) -> None:
    values = {} if limit is None else {"settings.cgwacs.acinputlimit": limit}
    result = next(r for r in run_selfcheck(snap(values), cfg, lambda s: None, T0) if r.id == "import_limit")
    assert (result.status, result.message) == expected


def test_the_new_checks_lock_nothing(limits) -> None:
    checks = [CheckResult("multi_mode", "warn", "x"), CheckResult("import_limit", "warn", "y")]
    assert lock_causes([], checks, limits) == {}
```

`fmt` zeigt Zahlen wie bisher mit Punkt; daran ändert dieser Plan nichts.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/checks tests/unit/core/safety/test_locks.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit tests/integration -q`
Expected: PASS. Bei einer Änderung von `system.json` die Vertragsdateien neu schreiben wie in Task 10.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/checks tests ui/src/lib/api/contract
git commit -m "feat(core): Selbstprüfung von Multi-Modus und Importlimit"
```

---

### Task 13: Simulator – Anlagenmodell

**Files:**
- Create: `src/bindaems/tools/sim/__init__.py`, `src/bindaems/tools/sim/model.py`, `src/bindaems/tools/sim/cars.py`
- Test: `tests/unit/tools/sim/test_sim_model.py`, `tests/unit/tools/sim/test_sim_cars.py`

**Interfaces:**
- Consumes: `Config`, `StateStore`, `HEARTBEAT_PATH`, `WATCHDOG_VERSION`, Tessie-URL und Befehlsnamen des Tesla-Aktors
- Produces:
  - `class PlantModel` in `model.py`:
    - `__init__(self, cfg: Config, rng: random.Random)`
    - `attach(store)`: registriert die Quellen wie die Adapter: `victron` 5 s ereignisgesteuert, `evcs` 5 s, `twc` 10 s, `tessie:<k>` 900 s.
    - `mqtt_write(path, value) -> bool` und `async tessie(request: httpx.Request) -> httpx.Response`
    - `step(now: datetime)`: eine Sekunde Physik
    - `publish(store, now)`: schreibt die Signale mit Quelle und Art wie die Adapter
    - `resolve(now)`: gibt Tessie-Antworten frei, deren Zeit gekommen ist
  - **Stellgrößen** für die Szenarien: `pv_w`, `house_w` (je Phase), `soc_pct`, `charger_only`, `outages: set[str]` aus `{"evcs", "twc", "tessie", "cerbo", "grid"}`, `foreign` (fremder Regler an/aus)
  - **Messgrößen** für die Kennzahlen: `grid_a`, `battery_feed_a` (je Phase), `battery_w`, `soc_pct`, je Fahrzeug `car_a` und `car_w`
  - `cars.py`: EVCS mit Auto, Wall Connector und Tesla mit Tessie als eigene Klassen. Ihre Namen wählt der Ausführende, `PlantModel` nutzt sie.
  - **Verhalten** (Design 13.1, Präzisierung 16):

    | Teil | Verhalten |
    |---|---|
    | Netz | `I_p = (P_acin_p − P_pv_p) / 230`; im Inselbetrieb 0 W, 0 V, `system.active_in_source` 240, sonst 1 |
    | Lasten | Haus und Wallboxen am AC-Ausgang: `P_acout_p = Haus_p + Wallboxen_p` |
    | PV | am AC-Eingang, gleich auf drei Phasen, `pv.<name>.power_w` und je Phase |
    | ESS (Multi „Ein“) | Akku gesamt `clamp(PV − Lasten, −max_discharge_w, max_charge_w)`, je Phase ein Drittel; ohne Entladung bei SOC ≤ Reserve, ohne Laden bei 100 %; `P_acin_p = P_acout_p + Akku_p` |
    | Peak Shaving | liegt eine Phase über `grid.import_limit_a`, entlädt der Multi dieser Phase zusätzlich, bis höchstens `max_discharge_w / 3` je Phase und nur über der Reserve |
    | „Nur Laden“ | `vebus.mode` 1: Akku nie negativ, kein Peak Shaving |
    | Akku | SOC aus der Leistung, `battery.usable_kwh`; `battery.power_w` positiv beim Laden |
    | EVCS | Sollstrom nach 1 s, Laden an/aus nach 2 s; Strom `min(Soll, Auto max)` auf `phase_map[:Phasen]`; Status 0, 1, 2, 3 nach Präzisierung 4; GX-Werte über `victron`, Modbus-Werte (`mode`, `start_stop`, `set_current_a`, `current_a`, `product_id`) über `evcs` |
    | Wall Connector | Ströme des Tesla je Phase, `vehicle_connected`, `contactor_closed`, `power_w` |
    | Tesla | 3 Phasen, Ladelimit 80 %; „Complete“ am Limit; schläft 15 min nach dem letzten Laden oder Befehl |
    | Tessie | Antwort nach 0,2 bis 2,5 s (Zufall mit Saat), bei schlafendem Auto erst nach 30 s Wecken; der erste Befehl auf 5 A geht verloren (Ergebnis `true`, Strom bleibt); Cache-Abfrage alle 30 s (lädt), 60 s (angesteckt) oder 600 s; Werte nur zu den Abfragezeiten |
    | Watchdog | Heartbeat ≠ 0 → nach 30 s scharf; 90 s ohne Heartbeat → ausgelöst, EVCS auf `safe_a`; 0 → wartet; meldet `watchdog.*` wie `tests/integration/plant.py` |
    | Ausfälle | `evcs`, `twc`, `tessie`: keine Werte, Quelle getrennt; bei `tessie` Befehle mit HTTP 503; `cerbo`: keine `victron`-Werte, `mqtt_write` liefert `False`; `grid`: Inselbetrieb |
    | fremder Regler | alle 45 s EVCS-Sollstrom 6 bis 16 A und Laden an, Tesla-Strom 5 bis 16 A, direkt am Gerät |
    | Selbstprüfung | `vebus.mode` 3 (bzw. 1), `settings.cgwacs.acinputlimit` 20, `vebus.phases` 3 und die Werte aus `GOOD_STATE` der Laufzeittests |

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/tools/sim/test_sim_model.py
def test_ess_covers_the_house_from_the_battery(model) -> None:
    model.house_w = (1000.0, 1000.0, 1000.0)
    model.step(T0)
    assert model.battery_w == pytest.approx(-3000.0)
    assert all(abs(model.grid_a[p]) < 0.01 for p in PHASES)


def test_peak_shaving_holds_the_import_limit(model) -> None:
    model.house_w = (8000.0, 300.0, 300.0)
    model.step(T0)
    assert model.grid_a["L1"] == pytest.approx(20.0)


def test_charger_only_does_not_discharge(model) -> None:
    model.charger_only = True
    model.house_w = (8000.0, 300.0, 300.0)
    model.step(T0)
    assert model.battery_w >= 0 and model.grid_a["L1"] == pytest.approx(8000.0 / 230)


def test_reserve_stops_discharge(model) -> None:
    model.soc_pct = 20.0
    model.house_w = (1000.0, 1000.0, 1000.0)
    model.step(T0)
    assert model.battery_w >= 0


def test_island_supplies_from_the_battery(model, store) -> None:
    model.outages.add("grid")
    model.step(T0)
    model.publish(store, T0)
    assert store.snapshot().ok("system.active_in_source") == 240


def test_watchdog_arms_trips_and_releases(model, store) -> None:
    # Heartbeat bei 0 → bei 30 s state 1; 90 s ohne → state 2 und EVCS-Sollstrom 6; Heartbeat 0 → state 0


# tests/unit/tools/sim/test_sim_cars.py
def test_evcs_follows_the_set_current_after_a_second(model) -> None:
    # e-Golf angesteckt, Laden an; mqtt_write("evcharger/40/SetCurrent", 10) bei T0 → bei T0+1 s
    # 10 A auf L1 und L2, Status 2


async def test_tessie_wakes_a_sleeping_car_before_starting(model) -> None:
    # Tesla schläft; POST start_charging → Antwort erst nach ≥ 30 s simulierter Zeit (resolve);
    # danach online und lädt


async def test_the_first_command_to_5_a_is_lost(model) -> None:
    # Tesla 10 A; set_charging_amps 5 → result true, Strom bleibt 10; derselbe Befehl erneut → 5


def test_tessie_values_change_only_at_poll_times(model, store) -> None:
    # lädt: charge_amps im Speicher nur alle 30 s neu (Zeitstempel der Lesung)
```

`model` und `store` sind Fixtures in `tests/unit/tools/sim/conftest.py`: Beispielkonfiguration, `random.Random(1)`, SOC 60 %, Multi „Ein“, PV 0, keine Autos angesteckt.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools/sim -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools/sim -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools/sim tests/unit/tools/sim
git commit -m "feat(tools): Anlagenmodell des Simulators"
```

---

### Task 14: Simulator – Lauf, Kennzahlen und Szenarien 1 bis 5

**Files:**
- Create: `src/bindaems/tools/sim/scenarios.py`, `src/bindaems/tools/sim/metrics.py`, `src/bindaems/tools/sim/runner.py`
- Test: `tests/unit/tools/sim/test_sim_metrics.py`, `tests/integration/test_sim_scenarios.py`

**Interfaces:**
- Consumes: `PlantModel` (Task 13), `CoreRuntime` mit `http_client` und `heartbeat_first` (Tasks 10, 11), `ManualClock`, `Decision`
- Produces:
  - **`scenarios.py`:**
    - `@dataclass(frozen=True) class Scenario` mit:
      - `name`, `title`, `start` (UTC), `duration_s`, `seed`
      - `setup: Callable[[PlantModel], None]`
      - `intent: Intent`, `live: bool`
      - `events: tuple[tuple[int, Callable[[PlantModel, CoreRuntime], None]], ...]`: Sekunde und Aktion, etwa ein Ausfall über `model.outages` oder eine neue Absicht über `rt.submit_intent`
      - `checks: Callable[[SimResult], list[str]]`
    - `SCENARIOS: Final[dict[str, Scenario]]` in der Reihenfolge des Designs
  - **`metrics.py`:**
    - `@dataclass(frozen=True) class Sample`:
      - `t: int`
      - `grid_a`, `battery_feed_a`, `battery_w`, `soc_pct`
      - je Fahrzeug `CarSample(charging: bool, current_a: float, power_w: float, mode: str, min_a: int)`
    - `measure(samples, decisions, cfg) -> Metrics`
    - `Metrics.failures(cfg) -> list[str]`, Text je Verletzung „{Kennzahl}: {Wert}, Grenze {Grenze}.“ mit Zahlen über `de()`
  - **`runner.py`:**
    - `@dataclass(frozen=True) class SimResult`: `scenario`, `metrics`, `decisions`, `health` (`list[tuple[int, HealthReport]]` alle 10 s), `samples`, `failures`
    - `async run_scenario(scenario, cfg, data_dir, *, duration_s: int | None = None) -> SimResult`
  - **Ablauf von `run_scenario`:**
    1. **Konfiguration:** `data_dir` als `core.data_dir`, `live_allowed` für `wallboxes.evcs` und `vehicles.tesla`.
    2. **Aufbau:** `ManualClock` als Wand- und Safety-Uhr, `CoreRuntime(…, adapters=[], sink=<verwirft>, serve_api=False, mqtt_write=model.mqtt_write, http_client=httpx.AsyncClient(transport=httpx.MockTransport(model.tessie)))`.
    3. **Je Sekunde:**
       1. Ereignisse ausführen
       2. `model.step` und `model.publish`
       3. `rt.cycle_once()`
       4. zehnmal `await asyncio.sleep(0)`
       5. `model.resolve`
       6. eine Probe nehmen
       7. die Uhr eine Sekunde weiterstellen
    4. **Ende:** `rt.decisions(...)` lesen, `await rt.shutdown()`.
  - **Kennzahlen** (Design 13.2, Präzisierung 16):

    | Kennzahl | Grenze | Messung |
    |---|---|---|
    | Höchster Phasenstrom | ≤ 35 A | größter Netzstrom einer Phase |
    | Längste Zeit über 33 A | ≤ 10 s | längste Folge von Sekunden mit einer Phase über 33 A |
    | Längste Zeit über dem Ziel in PV-Modi | ≤ 10 s, lädt der Tesla: ≤ 100 s | Siehe unten. |
    | Akku-Energie in PV-Autos | ≤ 5 % | Siehe unten. |
    | Starts je Auto und Stunde | ≤ 4 | Wechsel „lädt nicht“ → „lädt“ im Modell, gleitende Stunde |
    | Kürzester Abstand der Tesla-Strombefehle | ≥ 90 s | Entscheidungen `vehicle.tesla`/`current` mit Ergebnis ok oder Dry-Run, ohne Befund `urgent.bypass` |
    | Weckvorgänge | ≤ `wake.per_day` je Wiener Tag, Abstand ≥ `wake.interval_s` | Entscheidungen mit Grund `wake` |
    | Wiederholte Dry-Run-Entscheidungen | 0 | aufeinanderfolgende Dry-Run-Entscheidungen je Aktor und Art mit gleichem `requested` |

    - **Zeit über dem Ziel in PV-Modi:** Gezählt werden Sekunden, in denen alle ladenden Autos laut Absicht in PV-Überschuss oder Min+PV stehen und keines mit weniger als Mindeststrom + 0,5 A lädt. Die längste Folge mit einer Phase, in der `grid_a + battery_feed_a` über 19,5 A liegt, ist der Wert.
    - **Akku-Energie in PV-Autos:** `Σ min(Entladung, PV-Autos)` geteilt durch `Σ PV-Autos`, in denselben Sekunden.
  - **Szenarien 1 bis 5:** Ort und PV-Rechnung kommen aus der Beispielkonfiguration; Hauslast ist 300 W je Phase ± 50 W Rauschen.

    | Name | Titel | Start (Wien) | Dauer | PV | Haus zusätzlich | Akku | Autos und Absicht | Szenario-Prüfungen |
    |---|---|---|---|---|---|---|---|---|
    | `sonnig` | Sonniger Tag, beide Autos im PV-Modus | 21.06.2026 10:00 | 2 h | 9 kW · sin, Mittag 13:00 | – | 60 % | beide angesteckt, PV | jedes Auto lädt ≥ 60 min |
    | `wolkig` | Wolkiger Tag, beide Autos im PV-Modus | 21.06.2026 10:00 | 2 h | wie sonnig, Faktor 1,0/0,25 im Wechsel alle 30 bis 180 s | – | 60 % | wie sonnig | jedes Auto lädt ≥ 20 min |
    | `abend-sofort` | Abends beide Autos auf Sofort, mit Lastspitze | 12.10.2026 18:00 | 1 h | 0 | L1 +2,5 kW von 20 bis 25 min | 60 % | beide Sofort | EVCS-Strom während der Spitze unter dem vorher |
    | `reserve` | Sofort knapp über der USV-Reserve | 12.10.2026 18:00 | 1 h | 0 | – | 23 % | e-Golf Sofort, Tesla abgesteckt | Entscheidung mit `reserve_guard`; SOC nie unter 20 % |
    | `nur-laden` | Multi auf „Nur Laden“ mit Sofort | 12.10.2026 18:00 | 1 h | 0 | L1 +3 kW als Rampe über 20 min | 60 %, „Nur Laden“ | beide Sofort | Selbstprüfung `multi_mode` warnt |

    Alle Aktoren sind live. Die Absicht hat `control=True`, die Autos laden anfangs nicht, der Tesla ist wach.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/tools/sim/test_sim_metrics.py
def test_longest_time_above_33_a() -> None:
    samples = [sample(t, l1=34.0 if 5 <= t < 12 else 20.0) for t in range(20)]
    assert measure(samples, [], CFG).over_33_s == 7


def test_battery_share_of_pv_cars() -> None:
    # 10 Proben: PV-Auto 4000 W, Entladung 400 W in 5 davon → 5 %; mit 6 davon → Verstoß
    # „Akku-Energie in PV-Autos: 6 %, Grenze 5 %.“


def test_tesla_intervals_ignore_urgent_commands() -> None:
    # Entscheidungen bei 0, 60 (mit urgent.bypass) und 95 s → kürzester Abstand 95 s


def test_repeated_dry_run_decisions() -> None:
    # EVCS current 10, 10, 11 (dry_run) → 1


# tests/integration/test_sim_scenarios.py
@pytest.mark.parametrize("name", ["sonnig", "wolkig", "abend-sofort", "reserve", "nur-laden"])
async def test_scenario(name, cfg, tmp_path) -> None:
    result = await run_scenario(SCENARIOS[name], cfg, tmp_path)
    assert result.failures == []
```

`sample(t, l1=…)` baut eine `Sample` mit sonst ruhigen Werten; `CFG` ist die Beispielkonfiguration.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools/sim/test_sim_metrics.py tests/integration/test_sim_scenarios.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces.
  - **Scheitert ein Szenario,** liegt der Fehler zuerst in der Regelung: beheben, Test dazu im zuständigen Task-Modul, dann weiter.
  - **Das Szenario anpassen** nur, wenn es physikalisch nicht zu schaffen ist; das kommt in den Bericht des Tasks.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools/sim tests/integration/test_sim_scenarios.py -q --durations=10`
Expected: PASS; jedes Szenario unter 60 s Laufzeit

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests
git commit -m "feat(tools): Simulator – Lauf, Kennzahlen und Szenarien 1 bis 5"
```

---

### Task 15: Simulator – Szenarien 6 bis 10, Aufruf und Bericht

**Files:**
- Modify: `src/bindaems/tools/sim/scenarios.py`
- Create: `src/bindaems/tools/sim/report.py`, `src/bindaems/tools/sim/__main__.py`
- Test: `tests/integration/test_sim_scenarios.py`, `tests/unit/tools/sim/test_sim_cli.py`

**Interfaces:**
- Consumes: `run_scenario`, `SimResult`, `SCENARIOS` (Task 14)
- Produces:
  - **Szenarien 6 bis 10:**

    | Name | Titel | Start (Wien) | Dauer | Anlage | Autos, Absicht, Ereignisse | Szenario-Prüfungen |
    |---|---|---|---|---|---|---|
    | `tesla-5a` | Tesla auf 5 A mit verlorenem erstem Befehl | 21.06.2026 10:00 | 1 h | PV konstant 4,6 kW, Haus 300 W je Phase, Akku 90 % | Tesla angesteckt, Strom 10 A, PV; e-Golf abgesteckt | Entscheidung mit `not_taken_retry`; Tesla am Ende bei 5 A |
    | `tesla-schlaeft` | Schlafender Tesla im PV-Modus | 21.06.2026 09:00 | 1 h | PV 0 → 6 kW linear in 30 min, Akku 90 % | Tesla angesteckt und schlafend, PV; e-Golf abgesteckt | genau ein Weckvorgang; Tesla lädt am Ende |
    | `ausfaelle` | Ausfälle einzeln, Netzausfall mit Wiederaufnahme | 12.10.2026 18:00 | 1,5 h | PV 0, Akku 60 % | beide Sofort; Ausfälle: EVCS 10–15 min, Wall Connector 20–25, Tessie 30–35, Cerbo 40–41, Netz 50–53 | SAFE in beiden letzten Fenstern; ab 60 min laden beide; Entscheidung mit `resume_after_outage` |
    | `zuordnung` | Zuordnung unklar und Übersteuerung | 12.10.2026 18:00 | 1 h | PV 0, Akku 60 % | Tesla an der EVCS, Sofort; e-Golf abgesteckt; Übersteuerung `tesla: evcs` (Version 2) bei 10 min; Abstecken bei 40 min | Health zeigt „unklar“ vor 10 min; genau eine Entscheidung Tesla-Strom 16 A; ab 41 min lädt die EVCS nicht |
    | `dry-run` | Dry-Run neben einem fremden Regler | 21.06.2026 10:00 | 1 h | wie wolkig; fremder Regler an | beide PV; alle Aktoren Dry-Run | Wiederholte Dry-Run-Entscheidungen 0; je eine Dry-Run-Entscheidung an EVCS und Tesla; kein Alarm `competitor.foreign.*` |

  - **`report.py`:** `render_report(result: SimResult, config_path: str) -> str` mit
    - Überschrift `# Simulation „{Titel}“`
    - Tabelle „Angabe | Wert“: Szenario, simulierte Zeit in Wiener Zeit, Konfiguration
    - `## Kennzahlen`: Kennzahl | Wert | Grenze | Ergebnis (OK/FEHLER)
    - `## Szenario-Prüfungen`: je Prüfung eine Zeile mit OK oder FEHLER und Text
    - `## Entscheidungen`: Aktor | Art | Ergebnis | Anzahl, danach Gründe mit Anzahl
  - **`__main__.py`:**
    - Aufruf `python -m bindaems.tools.sim --szenario NAME --out PATH [--config deploy/config.example.yaml] [--stunden H]`
    - `main(argv) -> int`: 0, wenn alles grün ist, sonst 1; 2 bei unbekanntem Szenario oder einer Konfiguration ohne `evcs`, `twc`, `tesla` und `egolf`
    - Ausgabe: eine Zeile je Kennzahl; structlog auf WARNING

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/integration/test_sim_scenarios.py: die Parametrisierung umfasst alle zehn Namen
def test_all_scenarios_are_listed() -> None:
    assert list(SCENARIOS) == ["sonnig", "wolkig", "abend-sofort", "reserve", "nur-laden",
                               "tesla-5a", "tesla-schlaeft", "ausfaelle", "zuordnung", "dry-run"]


# tests/unit/tools/sim/test_sim_cli.py
async def test_cli_writes_the_report(tmp_path) -> None:
    out = tmp_path / "bericht.md"
    code = await asyncio.to_thread(main, ["--szenario", "reserve", "--stunden", "0.1", "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert code == 0 and text.startswith("# Simulation „Sofort knapp über der USV-Reserve“")
    assert "## Kennzahlen" in text and "| Höchster Phasenstrom |" in text


def test_unknown_scenario_is_an_error(tmp_path, capsys) -> None:
    assert main(["--szenario", "regen", "--out", str(tmp_path / "x.md")]) == 2
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/integration/test_sim_scenarios.py tests/unit/tools/sim/test_sim_cli.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces; für scheiternde Szenarien gilt die Regel aus Task 14.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools tests/integration/test_sim_scenarios.py -q --durations=10 && uv run python -m bindaems.tools.sim --szenario wolkig --out /tmp/bericht.md`
Expected: PASS; der Aufruf endet mit 0, und der Bericht steht in `/tmp/bericht.md`.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools/sim tests
git commit -m "feat(tools): Simulator – Szenarien 6 bis 10, Aufruf und Bericht"
```

---

### Task 16: Doku, Spec und Abdeckung

**Files:**
- Modify:
  - `docs/superpowers/specs/2026-10-08-bindaems-design.md`
  - `docs/superpowers/specs/2026-10-12-phase-2c1-laderegelung-design.md` (Stand)
  - `docs/betrieb.md`, `docs/uebergabe.md`, `deploy/config.example.yaml` (nur Kommentare)
- Test: `tests/unit/test_docs.py`

**Inhalt:**
- **Spec,** je ein Absatz „Stand Plan 2c-1“ (`docs/superpowers/plans/2026-10-12-phase-2c1-laderegelung.md`, Präzisierung n):

  | Abschnitt | Inhalt |
  |---|---|
  | 5.3 | Absicht mit `vehicles`, `priority`, `surplus_priority`; Ablehnung `invalid` |
  | 6.5 | Weckstart mit Erlaubnis und Kontingent; erneutes Anfordern nach „nicht übernommen“ |
  | 7.1 | Reihenfolge im Zyklus mit der Laderegelung |
  | 7.3 | Ziel je Modus, Last ohne Akku-Hilfe, Überlast-Stufen |
  | 7.5 | Überschuss-Formel, Verteilung, Mindestzeiten |
  | 7.8 | vereinfachte Zuordnung bis 2c-2 |
  | 8.4 | Degradation der Ladepunkte |
  | 13 bzw. Tests | Simulator und Szenarien |
  | 18 | Live-Schaltung von EVCS und Tesla gemeinsam nach 2d |
- **Design 2c-1:** Kopf „Stand: umgesetzt mit Plan 2c-1“ und ein Verweis auf die Präzisierungen.
- **Betriebshandbuch:**
  - Neuer Abschnitt „Laderegelung“:
    - Lademodi per `PUT /v1/intent` mit Beispiel-JSON (`vehicles`, `boost_until`, `wallbox`, `priority`, `surplus_priority`)
    - Dry-Run-Vergleich mit evcc über `GET /v1/decisions`, mit den Gründen und Eingaben
    - Umstieg von evcc nach 2d: beide Ladepunkte gemeinsam, evcc ganz aus
  - Neuer Abschnitt „Simulator“ mit Aufruf und Bericht.
  - „Meldungen des core“: neue Prüfpunkte der Selbstprüfung.
  - Abschnitt 14: Heartbeat-Startwert je Lauf; Freigabe erst ohne offenen Schreibvorgang.
- **`docs/uebergabe.md`:**
  - Stand 2c-1; erledigte Hinweise für 2c aus der Liste streichen.
  - Als Nächstes an der Anlage:
    1. Images ausrollen.
    2. Dry-Run neben evcc beginnen; die Teil-2-Werkzeuge schreiben dabei nicht.
    3. EVCS-Teil-2 „Verbindung unterbrochen“ und Victron 17.2-4 nachholen.
  - Danach: Plan 2c-2, dann 2d.
- **Abdeckung:** Der CI-Befehl bleibt; `core/control` muss mit den neuen Modulen ≥ 90 % halten.

- [ ] **Step 1: Ändern** wie oben; `tests/unit/test_docs.py` bekommt `test_operations_guide_describes_charging`. Er prüft „## Laderegelung“, „## Simulator“, `"vehicles"` und `-m bindaems.tools.sim`.

- [ ] **Step 2: Alle Prüfungen laufen lassen**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports && uv run pytest -q --cov --cov-fail-under=75 && uv run coverage report --include="src/bindaems/core/safety/*,src/bindaems/core/control/*,src/bindaems/core/actuators/*" --fail-under=90 && (cd ui && pnpm lint && pnpm check && pnpm test)`
Expected: alles grün, 4 Importverträge eingehalten

- [ ] **Step 3: Commit**

```bash
git add docs deploy/config.example.yaml tests/unit/test_docs.py
git commit -m "docs: Plan 2c-1 in Spec, Design, Betriebshandbuch und Übergabe"
```

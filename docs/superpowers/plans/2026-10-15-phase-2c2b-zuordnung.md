# Phase 2c-2b – Zuordnungserkennung: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der core erkennt selbst, welches Auto an welcher Wallbox steht, auch ein Gastfahrzeug, und regelt danach:
- Indizien aus Tessie, Home Assistant, Strömen, Phasen und Zeiten; je Wallbox „erkannt“, „angenommen“, „Gast“ oder „unklar“
- Tesla an der EVCS ohne Übersteuerung, e-Golf am Wall Connector als Last, Gast an der EVCS im Modus der Absicht
- Grund `assignment_detected` und Health je Wallbox
- Simulator mit e-Golf zu Hause oder weg, Gastfahrzeug mit 1 bis 3 Phasen und drei neuen Szenarien; `zuordnung` prüft einen echten Widerspruch

**Architecture:**
- **Rein in `core/control`** (ohne I/O, Zeit als Argument):
  - `clues.py`: die Indizien (Design 7.2) und die Zeiten, die sie halten müssen
  - `detection.py`: die Entscheidung (Design 7.3): erkannt, Gast, ausgeschlossen, Widerspruch
  - `fleet.py`: die Autos der Laderegelung, also die Fahrzeuge der Konfiguration und je Wallbox ein Gast
  - `assignment.py`: wer wo steht, aus Ansteckvorgängen, Übersteuerung und Erkennung
  - `charging.py`, `charge_points.py`, `charging_tesla.py`: der Gast als Auto ohne Tessie, der erkannte Tesla an der EVCS, der Grund und Health je Wallbox
- **Eingaben:** `vehicles.<name>.home_entity` aus Home Assistant, die Zeitstempel der Tessie-Werte und der Beginn jedes Ansteckvorgangs
- **Simulator:** Home Assistant mit „zu Hause“ und „weg“, ein Gastfahrzeug und ein Tesla, der woanders angesteckt ist

**Tech Stack:** wie Plan 2c-2a: Python 3.12 mit uv, pydantic v2, FastAPI, aiomqtt, pymodbus und httpx; Tests mit pytest, hypothesis, respx und pytest-cov; ruff, mypy und import-linter; der UI-Vertrag mit SvelteKit und pnpm.

**Spec:** `docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md` (im Plan „Design“), Abschnitt 7, dazu 3.2 (`home_entity`), 9 (Grund `assignment_detected`, Health je Wallbox), 10.1 und 10.3 (Simulator) und 11 (Inbetriebnahme Punkte 3 und 4, Abweichungen).
- Verweise wie „Design 7.3“ beziehen sich auf das Design, „Spec 7.8“ auf `docs/superpowers/specs/2026-10-08-bindaems-design.md`, „Design 2c-1“ auf `docs/superpowers/specs/2026-10-12-phase-2c1-laderegelung-design.md`.
- Wo das Design von der Spec abweicht, gilt das Design (Design 11.2). Die Präzisierungen unten gehen beiden vor.
- Vorgänger ist Plan 2c-2a (`docs/superpowers/plans/2026-10-14-phase-2c2a-akku-spitzen.md`), umgesetzt und auf `main`. Seine Global Constraints und die der Pläne davor gelten weiter.

## Einordnung

Ergebnis von 2c-2b:
- Die Zuordnung erkennt „Tesla an der EVCS“, „e-Golf am Wall Connector“ und einen Gast (Design, Entscheidung 5). Health zeigt je Wallbox, wer dort steht und warum.
- Alle Szenarien des Simulators sind in der CI grün: 17 bestehende unverändert, `zuordnung` bewusst geändert (Design 10.3), drei neue.
- An der Anlage läuft die Erkennung im Dry-Run mit (Design 11.1, Punkt 3). EVCS und Tesla bleiben im Dry-Run neben evcc bis nach 2d (Design 2c-1, 14.2).

Stand an der Anlage laut Übergabe: Images vom Stand nach Plan 2b; die Pläne 2c-1 und 2c-2a sind nicht ausgerollt. Nach 2c-2b folgt Plan 2d (Bedienung in UI und HA, die Tesla-Punkte vor der Live-Schaltung).

## Präzisierungen gegenüber dem Design

1. **Zuschnitt.** In 2c-2b kommt alles, was Plan 2c-2a (Präzisierung 1) aufgeschoben hat: Design 7 ganz, `home_entity` (3.2), der Grund `assignment_detected` und Health je Wallbox (9), die Fahrzeuge des Simulators (10.1), die Szenarien aus 10.3 und die Doku (11.1, Punkt 4). Nicht in 2c-2b: die Auswahl des Gast-Modus in UI und HA (2d).
2. **Indizien (Design 7.2).** Es gibt sie je Wallbox mit laufendem Ansteckvorgang (Design 2c-1, 8) und je Fahrzeug der Konfiguration; ohne Vorgang gibt es keine. Ein Indiz stützt das Fahrzeug an der Wallbox oder schließt es dort aus.
   - **Tessie** (Fahrzeug mit `tessie`): „zu Hause und angesteckt“ (`home_and_plugged`, bisher `_home_and_plugged` in `assignment.py`) stützt das Fahrzeug an jeder Wallbox mit Vorgang; sicher nicht (nicht angesteckt oder nicht zu Hause) schließt es an jeder aus.
   - **Stromvergleich** (mit `tessie`): Beide laden: die Wallbox (`charging`, `phase_a` bekannt) und das Auto laut Tessie (`charging`, `actual_current_a` mindestens 1 A, `phases` bekannt). Sie passen, wenn die Wallbox auf genau `phases` Netzphasen mindestens 1 A (`PHASE_ON_A`) misst und jeder dieser Ströme höchstens 1 A (`MATCH_A`) von `actual_current_a` abweicht. 60 s (`HOLD_S`) ohne Unterbrechung passend stützt, 60 s nicht passend schließt aus. Unter 1 A lädt das Auto hier nicht: Tessie meldet „Charging“ schon, bevor Strom fließt.
   - **Zeitliche Korrelation** (mit `tessie`): Das Fenster reicht von Tessies letzter Meldung „nicht angesteckt“ bis zur ersten „angesteckt“ danach, nach den Zeitstempeln der Werte (`VehicleObs.plugged_at`). Es stützt, wenn der Vorgang der Wallbox im Fenster begann, wobei der Anfang des Fensters um 60 s (`TESSIE_SETTLE_S`, Präzisierung 3) früher zählt, und wenn in derselben Zeit kein Vorgang an einer anderen Wallbox begann. Ohne beide Grenzen, ohne bekannten Beginn des Vorgangs oder mit einem Ende vor dem Anfang gibt es kein Indiz.
   - **Phasen-Signatur:** Die Wallbox lädt (`charging`, `phase_a` bekannt) und misst auf n ≥ 1 Netzphasen mindestens 1 A, 60 s lang dasselbe n. Das stützt jedes Fahrzeug mit `phases` = n und schließt jedes andere aus. Einen Gast stützt kein Indiz; er wird über Ausschlüsse erkannt (Präzisierung 4).
   - **Home Assistant** (Fahrzeug mit `home_entity`, auch eins mit Tessie): `home` stützt an jeder Wallbox mit Vorgang, jeder andere Zustand schließt an jeder aus.
   - **Ausschluss** (mit `tessie`): Tessie meldet „angesteckt“, und jede andere Wallbox meldet sicher kein Fahrzeug (`connected` falsch). Das stützt das Fahrzeug an dieser Wallbox.
   - Unbekannte Werte sprechen für nichts und schließen nichts aus. Fehlt eine Bedingung von Stromvergleich oder Phasen-Signatur, beginnen ihre 60 s neu.
   - **Reihenfolge** (Health): Wallboxen und Fahrzeuge wie in `config.yaml`, die Indizien wie in der Tabelle von Design 7.2 (`Clue`).
3. **Tessie-Werte und der Beginn eines Vorgangs (neu).**
   - Tessie fragt ein abgestecktes Auto nur alle 600 s ab, und die Tesla-API meldet das Anstecken mit Verzug. Ein Wert aus einer Abfrage vor oder kurz nach dem Beginn eines Vorgangs sagt über diesen Vorgang nichts.
   - Darum zählen Tessie, Stromvergleich und Ausschluss an einer Wallbox nur mit Werten, deren Abfrage (`plugged_at`) mindestens 60 s (`TESSIE_SETTLE_S`) nach dem Beginn ihres Vorgangs lag. Ist der Beginn unbekannt (der Vorgang lief schon beim Start des core), zählen sie immer; ohne `plugged_at` an einer Wallbox mit bekanntem Beginn nie.
   - So bleibt der Tesla, der am Wall Connector ankommt, dort „angenommen“ und geregelt wie in 2c-1, bis Tessie ihn neu abfragt. Ohne diese Regel schlösse ihn sein letzter Wert „nicht angesteckt“ für bis zu 10 min aus, und er lüde als angenommener Gast ungeregelt mit dem Höchststrom des Wall Connectors.
   - Für Home Assistant gilt das nicht: Sein Zustand kommt mit jeder Änderung, ein alter Wert ist kein veralteter. Meldet `home_entity` das Ankommen des e-Golf spät, gilt er bis dahin als Gast (angenommen); das Betriebshandbuch nennt das.
4. **Entscheidung (Design 7.3).**
   - **Kandidat** ist ein Fahrzeug an einer Wallbox mit mindestens 2 stützenden Indizien und keinem, das es ausschließt.
   - **Erkannt** ist es dort, wenn es 60 s ohne Unterbrechung Kandidat ist und an keiner anderen Wallbox Kandidat ist. Sind an einer Wallbox zwei Fahrzeuge erkannt, gilt keins.
   - **Ausgeschlossen** ist ein Fahrzeug an einer Wallbox, wenn ein Indiz es dort ausschließt oder es laut Übersteuerung an einer anderen Wallbox steht. Ein **Gast** ist an einer Wallbox erkannt, an der jedes Fahrzeug der Konfiguration 60 s ohne Unterbrechung ausgeschlossen ist.
   - **Widerspruch** (Abweichung vom Wortlaut von Design 7.3): Ein Fahrzeug ist an zwei Wallboxen Kandidat; oder es ist nirgends Kandidat und hat an einer Wallbox 2 stützende Indizien und eins, das es ausschließt. Nach dem Wortlaut wäre auch ein Fahrzeug, das an einer Wallbox Kandidat ist und an der anderen 2 Stützen und einen Ausschluss hat, ein Widerspruch. Tessie und Phasen-Signatur stützen den Tesla aber an jeder Wallbox, an der ein Auto mit drei Phasen lädt: Ein dreiphasiger Gast an der EVCS neben dem Tesla am Wall Connector machte die Zuordnung dann immer unklar, obwohl der Stromvergleich beide trennt.
   - **Unklar** wie in 2c-1 (`UNCLEAR_AFTER_S`, `CLEAR_AFTER_S`): Ein Widerspruch 60 s ohne Unterbrechung macht die Zuordnung unklar, 60 s ohne Widerspruch wieder klar, und eine aktive Übersteuerung hebt „unklar“ sofort auf. Solange sie unklar ist, gilt alles wie in 2c-1: jedes Fahrzeug ohne Übersteuerung an seiner Standard-Wallbox, sofern keine Übersteuerung sie belegt, mit der Art `unclear`, und kein Gast.
   - **Übersteuerung** geht vor (Design 7.1): An ihrer Wallbox entscheidet die Erkennung nicht, und das übersteuerte Fahrzeug ist an keiner anderen Wallbox Kandidat.
   - Die 60 s von „erkannt“ und „Gast“ beginnen neu, sobald die Bedingung einen Zyklus lang fehlt, und enden mit dem Vorgang.
5. **Wer steht wo (Design 7.1, 7.3).** Je Zyklus, solange die Zuordnung nicht unklar ist:
   1. jede Übersteuerung an ihrer Wallbox (`override`)
   2. je Wallbox mit Vorgang, in der Reihenfolge von `config.yaml`: das erkannte Fahrzeug, sonst ein erkannter Gast (`detected`)
   3. je übrige Wallbox mit Vorgang: ihr Standardfahrzeug (das erste Fahrzeug der Konfiguration mit dieser `default_wallbox`), wenn es dort gerade nicht ausgeschlossen ist und noch an keiner Wallbox steht; sonst „Gast, angenommen“. Beides hat die Art `default`.
   4. jedes Fahrzeug, das nirgends steht: seine Standard-Wallbox, wenn dort kein Vorgang läuft und keine Übersteuerung sie belegt (`default`; angesteckt ist es dann nicht, wie in 2c-1); sonst keine Wallbox
   - **Neu erkannt** (`VehicleState.fresh`): Das Auto ist an seiner Wallbox erkannt, und im vorigen Aufruf stand dort ein anderes Auto oder keins. Wird ein angenommenes Auto bestätigt, ist das keine neue Erkennung.
6. **Gast in der Regelung (Design 7.4, Entscheidung 6).**
   - Je Wallbox gibt es einen Gast mit dem Schlüssel `guest@<Wallbox>` (`fleet.guest_key`). Die Laderegelung behandelt ihn wie ein Fahrzeug ohne Tessie: Priorität hinter allen Fahrzeugen der Konfiguration in der Reihenfolge der Wallboxen, Modus `intent.guest.mode`, kein Boost, keine Übersteuerung.
   - Sein Ladepunkt hat alle drei Phasen der `phase_map`. An einer EVCS gelten `min_a` und `max_a` der EVCS, und sie wird geregelt wie jede EVCS. Am Wall Connector hat er keinen Aktor und ist Last; `max_a` des Wall Connectors gilt für den Zuschlag bei dessen Ausfall, `min_a` ist 6 A (`GUEST_MIN_A`).
   - Für Akku-Sollwert und 15-min-Wächter ist ein Gast ein Auto mit Ladepunkt (`CarView`) und dem Modus des Gastes, wie bisher eine Wallbox ohne Fahrzeug. Einen Gast an der EVCS kann der Wächter drosseln.
   - `health()` (`vehicles`) nennt weiter nur die Fahrzeuge der Konfiguration; den Gast zeigt `wallboxes` (Präzisierung 11).
7. **Tesla an der EVCS (Design 7.4).** Ein Fahrzeug mit Tessie, das an einer EVCS erkannt ist, regelt die Laderegelung wie mit Übersteuerung (`EvcsOverrides`): Mit jeder Ankunft an der EVCS bekommt es einmal `max_a`, danach regelt die EVCS mit seinen Phasen und Grenzen in seinem Modus. Wechselt an derselben EVCS nur die Art (erkannt ↔ Übersteuerung), ist das keine Ankunft.
8. **Grund `assignment_detected` (Design 7.4, 9).** Ab einer neuen Erkennung (Präzisierung 5) tragen die Befehle des Autos ihn als letzten Grund, bis zum ersten Zyklus mit Befehlen für das Auto, `max_a` an den Tesla eingeschlossen. Ist das Auto vorher nicht mehr erkannt, entfällt er. Die Regelung hängt ihn an die Gründe, die sie bisher gibt (`dataclasses.replace` der Befehle); `LastValues` merkt die Befehle wie bisher.
9. **Eingaben.**
   - `vehicles.<name>.home_entity` abonniert der core selbst unter dem Signal `vehicle.<name>.home_state` (`home_signal`), zusätzlich zu `homeassistant.entities` (`ha_entities`). Die Laufzeit gibt dem HA-Adapter diese Zuordnung.
   - `ChargeInputs.homes`: je Fahrzeug mit `home_entity` `True` bei `home`, `False` bei einem anderen Text, sonst `None` (`unknown`, `unavailable`, veraltet, kein Text). Fahrzeuge ohne `home_entity` fehlen.
   - `VehicleObs.plugged_at`: der Zeitstempel des Werts `vehicle.<name>.plugged`, also die Wanduhr seiner Abfrage, wenn er ein Wahrheitswert mit Qualität `OK` ist; sonst `None`.
   - Beginn eines Vorgangs: die Wanduhr des Zyklus, in dem er beginnt, nur nach bekanntem „kein Fahrzeug“ (wie `new_session`); sonst unbekannt.
10. **Uhren.** Die 60 s der Indizien und der Entscheidung laufen auf der Uhr der Safety-Schicht (`now`), wie „unklar“. Die zeitliche Korrelation und Präzisierung 3 rechnen auf der Wanduhr (`wall_now`), denn von ihr kommen die Zeitstempel der Signale. Springt die Wanduhr zurück, kann ein Fenster leer sein; dann gibt es kein Indiz und keine Ausnahme.
11. **Health (Design 9).** `HealthReport.wallboxes` nennt je Wallbox in der Reihenfolge von `config.yaml`:
    - `id`; `occupant`: der Schlüssel des Fahrzeugs, `guest` oder `null` (kein Vorgang); `name`: der Name des Fahrzeugs, `Gast` oder `null`
    - `assignment`: `detected`, `default` (angenommen), `override`, `unclear` oder `null`
    - `indicators`: die Indizien des letzten Zyklus an der Wallbox, je `{"vehicle": …, "clue": …, "supports": …}`
    - Vor dem ersten Zyklus und nach `idle` sind `occupant`, `name` und `assignment` `null`, `indicators` ist leer.
    - `vehicles[].assignment` kennt zusätzlich `detected`.
12. **Simulator (Design 10.1).**
    - **Home Assistant:** Quelle `ha` wie beim Adapter (ohne Frischegrenze, `invalidate_on_reconnect`). Je Fahrzeug mit `home_entity` meldet sie `home_signal` mit `home`, für die Fahrzeuge in `PlantModel.away` mit `not_home`. Die Beispielkonfiguration bekommt `home_entity` beim e-Golf; so ist er in allen Szenarien zu Hause, außer ein Szenario nennt ihn in `away`.
    - **Gast:** `GUEST_CAR = "gast"`, ein `Car` ohne Tessie (3 Phasen, 6 bis 16 A, 50 kWh) in `PlantModel.cars`, abgesteckt. Ein Szenario steckt ihn an und setzt seine Phasen (1 bis 3). Die Proben führen ihn mit dem Modus `intent.guest.mode`.
    - **Tesla woanders angesteckt** (`Tesla.elsewhere`): Er steckt an keiner Wallbox, aber Tessie meldet „Stopped“, also angesteckt und zu Hause (ein Ladekabel an einer Steckdose). Damit prüft `zuordnung` einen echten Widerspruch.
    - `_setup` bekommt `guest`, `guest_phases`, `away`, `tesla_elsewhere` und `evcs_on` (die EVCS lädt schon vor dem Start des core).
    - Jedes Auto lässt sich an jede Wallbox stecken (`plug`, unverändert).
13. **Bestehende Tests und Szenarien.**
    - Tests, die das Bild „Tesla an der EVCS“ (Tessie: zu Hause und angesteckt, Wall Connector ohne Fahrzeug, EVCS mit Fahrzeug) als „unklar“ prüfen, prüfen künftig einen echten Widerspruch: `CONTRADICTION` in `charge_helpers.py`, mit einer EVCS, die auf zwei Phasen lädt. Unklar wird es dann 60 s später, bei 120 s statt 60 s (erst die Phasen-Signatur, dann der Widerspruch). Wo ein Test das Bild nur braucht, prüft er jetzt „erkannt“.
    - Ändert sich ein anderer bestehender Test durch die Erkennung, liegt das zuerst an widersprüchlichen Eingaben, etwa einem e-Golf an einer EVCS, die auf drei Phasen lädt. Dann bekommt er widerspruchsfreie Eingaben, und seine Prüfung bleibt. Jede solche Änderung nennt der Bericht des Tasks.
    - `zuordnung`: Task 4 ändert nur die erste Prüfung (der Tesla an der EVCS ist vor 10 min „erkannt“ statt „unklar“). Task 8 baut das Szenario um auf einen echten Widerspruch und verschiebt das Bild „Tesla an der EVCS“ in `tesla-evcs` (Design 10.3).
    - Die übrigen 17 Szenarien bleiben unverändert grün: Ihre Fahrzeuge stehen an ihren Standard-Wallboxen und werden dort erkannt, das ändert ihre Regelung nicht.

## Global Constraints

- **Gilt weiter:** alle Global Constraints aus 1a, 1b, 2a, 2b, 2c-1 und 2c-2a.
- **Schreibwege:** unverändert gegenüber 2c-2a. Die Erkennung schreibt nichts. Es gibt keine neue Befehlsart, nur die bestehenden an EVCS und Tesla, jetzt auch für einen erkannten Tesla an der EVCS und einen Gast an der EVCS.
- **Home Assistant:** nur lesen (`subscribe_entities`), nie Dienstaufrufe, wie bisher.
- **Jeder Befehl** der Regelung geht durch `CommandExecutor.execute`, also genau einmal durch `SafetyLayer.authorize`.
- **Reinheit:** `clues.py`, `detection.py` und `fleet.py` importieren nichts aus `core.actuators`, `core.adapters`, `core.api`, `core.runtime`, `core.telemetry`, `core.accounting` und `core.checks` (Importvertrag). `home_signal` und `ha_entities` liegen in `shared/config.py`, denn Laufzeit, Regelung und Simulator brauchen sie.
- **Zeit:** nach Präzisierung 10; die Haltezeiten auf `ChargeInputs.now`, die Zeitstempel auf der Wanduhr.
- **Abdeckung:** ≥ 90 % für `core/safety`, `core/control` und `core/actuators`, ≥ 75 % insgesamt.
- **Texte:** exakt wie in „Texte“; Tests prüfen den Wortlaut. Zahlen über `de()`.
- **Öffentliches Repository:** Simulator, Tests und Berichte enthalten keine Daten der Anlage; `device_tracker.egolf` ist ein Beispielname. Keine Verweise auf Ledger-Einträge (R…).

## Review Focus

1. **Tessie veraltet, offline oder vor dem Anstecken abgefragt** (der Tesla kommt täglich so an):
   - Erwartet: Ein Wert aus einer Abfrage vor dem Beginn des Vorgangs schließt nichts aus; der Tesla am Wall Connector bleibt „angenommen“ und geregelt, bis Tessie neu fragt. Ohne Tessie wird nichts „unklar“, und an jeder Wallbox steht ihr Standardfahrzeug.
   - Tests: Task 2 `test_tessie_values_from_before_the_session_do_not_count`, Task 4 `test_arriving_tesla_stays_assumed_until_tessie_polls` und `test_tessie_offline_assumes_the_default_vehicle`.
2. **Indizien an der Grenze** (Ströme knapp an 1 A, Phasen beim Anlaufen, Tessie 30 s im Verzug):
   - Erwartet: kein Indiz vor 60 s ohne Unterbrechung, keine Erkennung vor weiteren 60 s, kein Hin und Her des Insassen.
   - Tests: Task 2 `test_current_comparison_needs_60_s_without_interruption`, Task 3 `test_detection_restarts_after_an_interruption`.
3. **Die Wanduhr springt** (NTP; Tessie-Werte mit Zeitstempeln in falscher Reihenfolge):
   - Erwartet: keine zeitliche Korrelation aus einem rückwärts laufenden Fenster, keine Ausnahme.
   - Test: Task 2 `test_timing_ignores_a_window_that_runs_backwards`.
4. **Der Insasse wechselt während eines Vorgangs** (angenommen → erkannt, erkannt → unklar → erkannt):
   - Erwartet: eine Übergabe je Wechsel (Werte vergessen, die Maschine übernimmt), `max_a` einmal je Ankunft, kein zweites „Laden an“, `assignment_detected` nur an den ersten Befehlen.
   - Tests: Task 5 `test_handover_from_the_assumed_to_the_detected_car_starts_once` und `test_new_detection_marks_only_the_first_commands`.
5. **Abstecken, auch nach einer Übersteuerung:**
   - Erwartet: Erkennung und Übersteuerung enden mit dem Vorgang. Ein neuer Vorgang beginnt „angenommen“, ohne die Zeiten des alten.
   - Tests: Task 3 `test_holds_end_with_the_session`, Task 4 `test_a_new_session_starts_without_the_old_detection`.

## Texte

**Grund** (`_REASON_TEXT` in `shared/decisions.py`, nach `peak_guard`):

| Code | Text |
|---|---|
| `assignment_detected` | Zuordnung erkannt |

Beispiel: `Tesla Model 3 (Sofort, Zuordnung erkannt): Strom 16 A – gesendet und bestätigt.`

**Konfiguration:** `vehicles.<name>.home_entity verlangt einen homeassistant-Abschnitt`

**Health-Werte** (Präzisierung 11):
- `wallboxes[].assignment`: `detected`, `default`, `override`, `unclear`, `null`
- `wallboxes[].occupant`: Schlüssel des Fahrzeugs, `guest`, `null`; `wallboxes[].name`: Name des Fahrzeugs, `Gast`, `null`
- `wallboxes[].indicators[].clue`: `tessie`, `current`, `timing`, `phases`, `home`, `elsewhere`

**Prüfungen des Simulators** ({m} ist `de(Sekunden / 60)`):

| Prüfung | Satz | Befund |
|---|---|---|
| `_shown_before(key, kind, until_s)` | `Die Gesundheit zeigt „{key}“ vor {m} min „{unklar/erkannt}“.` | `Die Gesundheit zeigt „{key}“ vor {until_s} s nie „{unklar/erkannt}“.` |
| `_clear_from(from_s)` | `Ab {m} min zeigt die Gesundheit kein Fahrzeug „unklar“.` | `Bei {t} s zeigt die Gesundheit „{key}“ „unklar“.` (die erste) |
| `_occupied_from(wallbox, occupant, from_s)` | `Ab {m} min steht an „{wallbox}“ laut Gesundheit „{occupant}“, erkannt.` | `Bei {t} s steht an „{wallbox}“ nicht „{occupant}“, erkannt.` (die erste) |
| `_no_decision_at(actuator)` | `Keine Entscheidung an „{actuator}“.` | `{n} Entscheidungen an „{actuator}“, die erste bei {t} s.` |

## Dateistruktur

```
src/bindaems/shared/config.py                   (geändert) VehicleConfig.home_entity, home_signal, ha_entities
src/bindaems/shared/decisions.py                (geändert) Grund assignment_detected
src/bindaems/core/control/charge_inputs.py      (geändert) VehicleObs.plugged_at, ChargeInputs.homes, homes()
src/bindaems/core/control/clues.py              Clue, Evidence, ClueTracker: die Indizien
src/bindaems/core/control/detection.py          Detection, Detector: die Entscheidung
src/bindaems/core/control/fleet.py              Gast-Schlüssel, fleet, CarSpec, car_spec
src/bindaems/core/control/assignment.py         (geändert) DETECTED, WallboxState, wer wo steht, fresh
src/bindaems/core/control/charge_points.py, charging.py, charging_tesla.py   (geändert)
src/bindaems/core/control/regulation.py         (geändert) Health wallboxes
src/bindaems/core/regulation_link.py, core/runtime.py, core/api/app.py   (geändert)
src/bindaems/tools/sim/cars.py, model.py, runner.py, scenarios.py   (geändert)
deploy/config.example.yaml                      (geändert) home_entity beim e-Golf
tests/unit/core/control/{test_clues,test_detection,test_fleet}.py (neu); test_assignment.py, test_charging.py,
  test_charging_tesla.py, test_charge_points.py, test_charge_inputs.py, test_regulation.py, charge_helpers.py (geändert)
tests/unit/shared/{test_config,test_decisions}.py, tests/unit/core/test_regulation_link.py,
  tests/unit/tools/sim/{test_sim_model,test_sim_runner,test_sim_checks}.py, tests/unit/test_docs.py (geändert)
tests/integration/{test_runtime,test_runtime_regulation,test_sim_scenarios}.py (geändert)
ui/src/lib/api/contract (neu geschrieben)
docs: Betriebshandbuch, Spec, Design (Stand), docs/uebergabe.md
```

Neue Pakete gibt es nicht; Testverzeichnisse bekommen keine `__init__.py`.

---

### Task 1: Konfiguration und Eingaben

**Files:**
- Modify: `src/bindaems/shared/config.py`, `src/bindaems/core/control/charge_inputs.py`, `src/bindaems/core/regulation_link.py`, `src/bindaems/core/runtime.py`, `deploy/config.example.yaml`
- Test: `tests/unit/shared/test_config.py`, `tests/unit/core/control/test_charge_inputs.py`, `tests/unit/core/test_regulation_link.py`, `tests/integration/test_runtime.py`

**Interfaces:**
- Consumes: `Snapshot.get` (`Reading.ts`), `HaAdapter`, `RegulationLink.step`
- Produces (Präzisierung 9):
  - `VehicleConfig.home_entity: str | None = None`; der Validator von `Config` lehnt es ohne Abschnitt `homeassistant` ab (Text in „Texte“), wie `soc_entity`
  - `home_signal(vehicle: str) -> str`: `vehicle.<vehicle>.home_state`
  - `ha_entities(cfg: Config) -> dict[str, str]`: `homeassistant.entities` und dazu je Fahrzeug mit `home_entity` `home_signal(key) → home_entity`; ohne Abschnitt `homeassistant` leer
  - `VehicleObs.plugged_at: datetime | None = None` (letztes Feld); `_vehicle_obs` setzt es
  - `ChargeInputs.homes: Mapping[str, bool | None] = field(default_factory=dict)` (letztes Feld)
  - `homes(snap: Snapshot, cfg: Config) -> dict[str, bool | None]` in `charge_inputs.py`
  - `RegulationLink.step` baut `ChargeInputs` mit `homes=homes(snap, cfg)`
  - `CoreRuntime.build_adapters`: `HaAdapter(cfg.homeassistant.model_copy(update={"entities": ha_entities(cfg)}), …)`
  - `deploy/config.example.yaml`: beim e-Golf `home_entity: device_tracker.egolf` mit Kommentar (device_tracker in Home Assistant, `home` heißt zu Hause; der core abonniert ihn selbst; er sollte das Ankommen binnen einer Minute melden)

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/shared/test_config.py
def test_home_entity_needs_homeassistant(tmp_path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d.pop("homeassistant")
        d["vehicles"]["egolf"].pop("soc_entity")  # sonst meldet sich zuerst soc_entity
    with pytest.raises(ValidationError, match="vehicles.egolf.home_entity verlangt einen homeassistant-Abschnitt"):
        load_config(write_cfg(tmp_path, mutate))


def test_home_entities_are_subscribed_under_their_signal(cfg) -> None:
    assert home_signal("egolf") == "vehicle.egolf.home_state"
    assert ha_entities(cfg) == {
        "vehicle.egolf.soc_pct": "sensor.egolf_soc",
        "vehicle.egolf.home_state": "device_tracker.egolf",
    }
    assert ha_entities(cfg.model_copy(update={"homeassistant": None})) == {}


# tests/unit/core/control/test_charge_inputs.py
@pytest.mark.parametrize(
    ("state", "expected"),
    [("home", True), ("not_home", False), ("Arbeit", False), (True, None), (None, None)],
)
def test_home_comes_from_home_assistant(cfg, state, expected) -> None:
    assert homes(snap({"vehicle.egolf.home_state": state}), cfg) == {"egolf": expected}


def test_a_stale_home_state_is_unknown(cfg) -> None:
    stale = snap({"vehicle.egolf.home_state": "home"}, quality=Quality.STALE)
    assert homes(stale, cfg) == {"egolf": None}


def test_plugged_at_is_the_time_of_the_tessie_value(cfg) -> None:
    at = T0 + timedelta(seconds=7)
    snap_ = snap({"vehicle.tesla.plugged": False}, ts=at)
    _, _, vehicles = observe(snap_, derive(snap_, cfg), cfg, lambda source: True)
    assert vehicles["tesla"].plugged_at == at
    assert obs(cfg, {"vehicle.tesla.plugged": "ja"})[2]["tesla"].plugged_at is None


# tests/unit/core/test_regulation_link.py
def test_the_home_state_reaches_the_regulation(cfg) -> None:
    # ein Zyklus mit "vehicle.egolf.home_state": "home" im Snapshot: Der Kern bekommt
    # RegulationInputs.charge.homes == {"egolf": True} (der Aufruf von regulation.step wird mitgeschnitten)


# tests/integration/test_runtime.py
def test_build_adapters_subscribes_the_home_entity(cfg) -> None:
    secrets = Secrets(internal_token=SecretStr(TOKEN), ha_token=SecretStr("h"))
    rt = CoreRuntime(cfg, secrets, sink=FakeSink(), serve_api=False)
    ha = next(adapter for adapter in rt.adapters if adapter.name == "ha")
    assert ha._signals["device_tracker.egolf"] == ["vehicle.egolf.home_state"]
```

`test_tessie_values` erwartet danach `VehicleObs(True, True, True, False, False, False, 11.0, 3, T0)`.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/shared/test_config.py tests/unit/core/control/test_charge_inputs.py tests/unit/core/test_regulation_link.py tests/integration/test_runtime.py -q`
Expected: FAIL (`ImportError` für `home_signal`, `ha_entities`, `homes`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces. `homes` liest den Text mit `_text`; ein anderer Typ ist unbekannt.

- [ ] **Step 4: Alles laufen lassen**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems deploy/config.example.yaml tests
git commit -m "feat(core): home_entity aus Home Assistant und Zeitstempel der Tessie-Werte für die Zuordnung"
```

---

### Task 2: Indizien

**Files:**
- Create: `src/bindaems/core/control/clues.py`
- Modify: `src/bindaems/core/control/assignment.py` (nur der Import von `home_and_plugged`)
- Test: `tests/unit/core/control/test_clues.py`

**Interfaces:**
- Consumes: `WallboxObs`, `VehicleObs` mit `plugged_at` (Task 1), `held_since` (`timing.py`)
- Produces (Präzisierungen 2, 3 und 10):
  - Konstanten: `HOLD_S: Final = 60.0`, `MATCH_A: Final = 1.0`, `PHASE_ON_A: Final = 1.0`, `TESSIE_SETTLE_S: Final = 60.0`, `STARTS_KEEP_S: Final = 86_400.0` (so lange merkt sich die Erkennung den Beginn eines Vorgangs)
  - `class Clue(StrEnum)`: `TESSIE = "tessie"`, `CURRENT = "current"`, `TIMING = "timing"`, `PHASES = "phases"`, `HOME = "home"`, `ELSEWHERE = "elsewhere"`, in dieser Reihenfolge
  - `@dataclass(frozen=True) class Evidence`: `wallbox: str`, `vehicle: str`, `clue: Clue`, `supports: bool` (`False` schließt aus)
  - `home_and_plugged(tessie: VehicleObs) -> bool | None`: aus `assignment._home_and_plugged` hierher verschoben; `assignment.py` importiert es
  - `class ClueTracker`:
    - `__init__(self, cfg: Config) -> None`
    - `update(self, now: datetime, wall_now: datetime, sessions: Mapping[str, datetime | None], wallboxes: Mapping[str, WallboxObs], vehicles: Mapping[str, VehicleObs], homes: Mapping[str, bool | None]) -> list[Evidence]`
    - `sessions`: je Wallbox mit laufendem Vorgang sein Beginn auf der Wanduhr, `None` heißt unbekannt; eine Wallbox ohne Vorgang fehlt
    - Zustand: die 60 s von Stromvergleich (je Wallbox und Fahrzeug, mit „passt“ oder „passt nicht“) und Phasen-Signatur (je Wallbox, mit n); je Fahrzeug mit Tessie die letzte Meldung „nicht angesteckt“ und die erste „angesteckt“ danach; je Wallbox die Beginne ihrer Vorgänge der letzten `STARTS_KEEP_S`

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_clues.py
AT_EVCS = {"evcs": None}  # an der EVCS läuft ein Vorgang, sein Beginn ist unbekannt
TWO = {"L1": 10.0, "L2": 10.0, "L3": 0.0}


def clues(tracker, s, *, sessions=AT_EVCS, evcs_obs=None, twc_obs=None, tesla_obs=None, homes=None):
    when = T0 + timedelta(seconds=s)
    wallboxes = {"evcs": evcs_obs or evcs(), "twc": twc_obs or twc()}
    vehicles = {"tesla": tesla_obs or tesla(), "egolf": NONE}
    return set(tracker.update(when, when, sessions, wallboxes, vehicles, homes or {}))


def of(found, clue):
    return {e for e in found if e.clue is clue}


def ev(wallbox, vehicle, clue, supports=True):
    return Evidence(wallbox, vehicle, clue, supports)


def test_tessie_supports_at_every_session_or_excludes(cfg) -> None:
    both = {"evcs": None, "twc": None}
    found = clues(ClueTracker(cfg), 0, sessions=both, twc_obs=twc(connected=True), tesla_obs=tesla(plugged=True))
    assert of(found, Clue.TESSIE) == {ev("evcs", "tesla", Clue.TESSIE), ev("twc", "tesla", Clue.TESSIE)}
    away = clues(ClueTracker(cfg), 0, tesla_obs=tesla(plugged=True, at_home=False))
    assert of(away, Clue.TESSIE) == {ev("evcs", "tesla", Clue.TESSIE, False)}
    assert of(clues(ClueTracker(cfg), 0), Clue.TESSIE) == {ev("evcs", "tesla", Clue.TESSIE, False)}


def test_home_assistant_supports_or_excludes(cfg) -> None:
    assert of(clues(ClueTracker(cfg), 0, homes={"egolf": True}), Clue.HOME) == {ev("evcs", "egolf", Clue.HOME)}
    assert of(clues(ClueTracker(cfg), 0, homes={"egolf": False}), Clue.HOME) == {ev("evcs", "egolf", Clue.HOME, False)}
    assert of(clues(ClueTracker(cfg), 0, homes={"egolf": None}), Clue.HOME) == set()


def test_the_other_wallbox_without_a_car_supports_a_plugged_tesla(cfg) -> None:
    plugged = tesla(plugged=True)
    assert of(clues(ClueTracker(cfg), 0, tesla_obs=plugged), Clue.ELSEWHERE) == {ev("evcs", "tesla", Clue.ELSEWHERE)}
    unknown = clues(ClueTracker(cfg), 0, twc_obs=twc(connected=None), tesla_obs=plugged)
    assert of(unknown, Clue.ELSEWHERE) == set()


def test_phase_signature_counts_after_60_s(cfg) -> None:
    tracker, two = ClueTracker(cfg), evcs(charging=True, phase_a=TWO)
    assert all(of(clues(tracker, s, evcs_obs=two), Clue.PHASES) == set() for s in range(60))
    assert of(clues(tracker, 60, evcs_obs=two), Clue.PHASES) == {
        ev("evcs", "tesla", Clue.PHASES, False),
        ev("evcs", "egolf", Clue.PHASES),
    }


def test_a_new_phase_count_starts_the_60_s_again(cfg) -> None:
    tracker = ClueTracker(cfg)
    for s in range(30):
        clues(tracker, s, evcs_obs=evcs(charging=True, phase_a=dict.fromkeys(PHASES, 10.0)))
    two = evcs(charging=True, phase_a=TWO)
    assert all(of(clues(tracker, s, evcs_obs=two), Clue.PHASES) == set() for s in range(30, 90))
    assert ev("evcs", "egolf", Clue.PHASES) in clues(tracker, 90, evcs_obs=two)


def test_current_comparison_needs_60_s_without_interruption(cfg) -> None:  # Review Focus 2
    tracker = ClueTracker(cfg)
    at_16 = evcs(charging=True, phase_a=dict.fromkeys(PHASES, 16.0))
    car = tesla(plugged=True, charging=True, actual_current_a=15.2, phases=3)
    for s in range(59):
        clues(tracker, s, evcs_obs=at_16, tesla_obs=car)
    clues(tracker, 59, evcs_obs=at_16, tesla_obs=replace(car, actual_current_a=0.5))  # unter 1 A
    assert all(of(clues(tracker, s, evcs_obs=at_16, tesla_obs=car), Clue.CURRENT) == set() for s in range(60, 120))
    assert of(clues(tracker, 120, evcs_obs=at_16, tesla_obs=car), Clue.CURRENT) == {ev("evcs", "tesla", Clue.CURRENT)}


def test_currents_that_do_not_match_exclude_after_60_s(cfg) -> None:
    tracker, two = ClueTracker(cfg), evcs(charging=True, phase_a=TWO)
    car = tesla(plugged=True, charging=True, actual_current_a=10.0, phases=3)  # drei Phasen, die EVCS misst zwei
    for s in range(60):
        clues(tracker, s, evcs_obs=two, tesla_obs=car)
    assert ev("evcs", "tesla", Clue.CURRENT, False) in clues(tracker, 60, evcs_obs=two, tesla_obs=car)


def test_tessie_values_from_before_the_session_do_not_count(cfg) -> None:  # Review Focus 1
    start = T0 + timedelta(seconds=100)
    early = tesla(plugged=False, plugged_at=T0 + timedelta(seconds=130))  # 30 s nach dem Beginn abgefragt
    assert clues(ClueTracker(cfg), 200, sessions={"evcs": start}, tesla_obs=early) == set()
    settled = replace(early, plugged_at=T0 + timedelta(seconds=160))  # 60 s danach
    found = clues(ClueTracker(cfg), 200, sessions={"evcs": start}, tesla_obs=settled)
    assert of(found, Clue.TESSIE) == {ev("evcs", "tesla", Clue.TESSIE, False)}


def test_timing_supports_the_session_between_two_tessie_values(cfg) -> None:
    tracker = ClueTracker(cfg)
    clues(tracker, 0, sessions={}, evcs_obs=evcs(connected=False), tesla_obs=tesla(plugged_at=T0))
    plugged = tesla(plugged=True, plugged_at=T0 + timedelta(seconds=600))
    found = clues(tracker, 600, sessions={"evcs": T0 + timedelta(seconds=100)}, tesla_obs=plugged)
    assert ev("evcs", "tesla", Clue.TIMING) in found


def test_timing_needs_the_only_session_in_the_window(cfg) -> None:
    tracker = ClueTracker(cfg)
    clues(tracker, 0, sessions={}, evcs_obs=evcs(connected=False), tesla_obs=tesla(plugged_at=T0))
    both = {"evcs": T0 + timedelta(seconds=100), "twc": T0 + timedelta(seconds=200)}
    plugged = tesla(plugged=True, plugged_at=T0 + timedelta(seconds=600))
    found = clues(tracker, 600, sessions=both, twc_obs=twc(connected=True), tesla_obs=plugged)
    assert of(found, Clue.TIMING) == set()


def test_timing_ignores_a_window_that_runs_backwards(cfg) -> None:  # Review Focus 3
    tracker = ClueTracker(cfg)
    clues(tracker, 0, sessions={}, evcs_obs=evcs(connected=False), tesla_obs=tesla(plugged_at=T0 + timedelta(seconds=900)))
    plugged = tesla(plugged=True, plugged_at=T0 + timedelta(seconds=600))  # die Wanduhr sprang zurück
    found = clues(tracker, 600, sessions={"evcs": T0 + timedelta(seconds=100)}, tesla_obs=plugged)
    assert of(found, Clue.TIMING) == set()


def test_unknown_values_are_no_evidence(cfg) -> None:  # Review Focus 1
    unknown = evcs(charging=None, phase_a=None)
    assert clues(ClueTracker(cfg), 0, evcs_obs=unknown, twc_obs=twc(connected=None), tesla_obs=NONE) == set()


def test_no_evidence_without_a_session(cfg) -> None:
    assert clues(ClueTracker(cfg), 0, sessions={}, tesla_obs=tesla(plugged=True), homes={"egolf": True}) == set()


def test_evidence_comes_in_a_fixed_order(cfg) -> None:
    wallboxes = {"evcs": evcs(), "twc": twc(connected=True)}
    vehicles = {"tesla": tesla(plugged=True), "egolf": NONE}
    found = ClueTracker(cfg).update(T0, T0, {"evcs": None, "twc": None}, wallboxes, vehicles, {"egolf": True})
    assert [(e.wallbox, e.vehicle, e.clue) for e in found] == [
        ("evcs", "tesla", Clue.TESSIE), ("evcs", "egolf", Clue.HOME),
        ("twc", "tesla", Clue.TESSIE), ("twc", "egolf", Clue.HOME),
    ]
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_clues.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces. Die 60 s mit `held_since`; ein Eintrag, dessen Bedingung fehlt oder dessen Wert („passt“, n) sich ändert, beginnt neu. Präzisierung 3 gilt für Tessie, Stromvergleich und Ausschluss, nicht für die zeitliche Korrelation.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control -q && uv run mypy src && uv run lint-imports`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control tests/unit/core/control/test_clues.py
git commit -m "feat(core): Indizien der Zuordnungserkennung"
```

---

### Task 3: Entscheidung

**Files:**
- Create: `src/bindaems/core/control/detection.py`
- Test: `tests/unit/core/control/test_detection.py`

**Interfaces:**
- Consumes: `ClueTracker`, `Evidence`, `HOLD_S` (Task 2), `held_since`
- Produces (Präzisierung 4):
  - `@dataclass(frozen=True) class Detection`:
    - `evidence: tuple[Evidence, ...]`: die Indizien des Zyklus (`ClueTracker.update`)
    - `detected: Mapping[str, str]`: Wallbox → erkanntes Fahrzeug
    - `guests: frozenset[str]`: die Wallboxen mit erkanntem Gast
    - `excluded: Mapping[str, frozenset[str]]`: je Wallbox mit Vorgang die Fahrzeuge, die dort jetzt ausgeschlossen sind
    - `contradiction: bool`: jetzt widersprechen sich die Indizien
  - `class Detector`:
    - `__init__(self, cfg: Config) -> None`; hält einen `ClueTracker`
    - `update(self, now: datetime, wall_now: datetime, sessions: Mapping[str, datetime | None], wallboxes: Mapping[str, WallboxObs], vehicles: Mapping[str, VehicleObs], homes: Mapping[str, bool | None], placed: Mapping[str, str]) -> Detection`
    - `placed`: Fahrzeug → Wallbox seiner gültigen Übersteuerung (`Assignment._active_overrides`). An diesen Wallboxen entscheidet die Erkennung nicht (kein Fahrzeug, kein Gast); ihre Indizien stehen trotzdem in `evidence`.
    - Die 60 s von „erkannt“ je Wallbox und Fahrzeug, von „Gast“ je Wallbox; die einer Wallbox ohne Vorgang fallen weg.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_detection.py
AT_EVCS = {"evcs": None}
BOTH = {"evcs": None, "twc": None}
TWO = {"L1": 10.0, "L2": 10.0, "L3": 0.0}
TESLA_AT_EVCS = {"tesla_obs": tesla(plugged=True)}  # Tessie und Ausschluss stützen den Tesla an der EVCS


def detect(d, s, *, sessions=AT_EVCS, evcs_obs=None, twc_obs=None, tesla_obs=None, homes=None, placed=None):
    when = T0 + timedelta(seconds=s)
    wallboxes = {"evcs": evcs_obs or evcs(), "twc": twc_obs or twc()}
    vehicles = {"tesla": tesla_obs or tesla(), "egolf": NONE}
    return d.update(when, when, sessions, wallboxes, vehicles, homes or {}, placed or {})


def test_tesla_at_the_evcs_is_detected_after_60_s(cfg) -> None:
    d = Detector(cfg)
    assert all(detect(d, s, **TESLA_AT_EVCS).detected == {} for s in range(60))
    found = detect(d, 60, **TESLA_AT_EVCS)
    assert (found.detected, found.contradiction) == ({"evcs": "tesla"}, False)


def test_egolf_at_the_wall_connector_by_home_and_phases(cfg) -> None:
    egolf = twc(connected=True, charging=True, phase_a={"L1": 16.0, "L2": 16.0, "L3": 0.0})
    kw = {"sessions": {"twc": None}, "evcs_obs": evcs(connected=False), "twc_obs": egolf, "homes": {"egolf": True}}
    d = Detector(cfg)
    assert all(detect(d, s, **kw).detected == {} for s in range(120))  # Phasen ab 60 s, erkannt 60 s danach
    assert detect(d, 120, **kw).detected == {"twc": "egolf"}


def test_guest_when_every_vehicle_is_excluded_for_60_s(cfg) -> None:
    kw = {"evcs_obs": evcs(charging=True, phase_a=dict.fromkeys(PHASES, 16.0)), "homes": {"egolf": False}}
    d = Detector(cfg)
    first = detect(d, 0, **kw)  # Tesla nicht angesteckt, e-Golf weg: beide sofort ausgeschlossen
    assert (first.guests, first.excluded["evcs"]) == (frozenset(), {"tesla", "egolf"})
    assert all(detect(d, s, **kw).guests == frozenset() for s in range(1, 60))
    assert detect(d, 60, **kw).guests == {"evcs"}


def test_a_vehicle_with_two_supports_at_two_wallboxes_is_a_contradiction(cfg) -> None:
    same = dict.fromkeys(PHASES, 16.0)  # Tesla am Wall Connector, dreiphasiger Gast an der EVCS, gleicher Strom
    kw = {
        "sessions": BOTH,
        "evcs_obs": evcs(charging=True, phase_a=same),
        "twc_obs": twc(connected=True, charging=True, phase_a=same),
        "tesla_obs": tesla(plugged=True, charging=True, actual_current_a=16.0, phases=3),
    }
    d = Detector(cfg)
    assert not any(detect(d, s, **kw).contradiction for s in range(60))
    found = detect(d, 60, **kw)
    assert (found.contradiction, found.detected) == (True, {})


def test_two_supports_and_an_exclusion_are_a_contradiction(cfg) -> None:
    kw = {"evcs_obs": evcs(charging=True, phase_a=TWO), **TESLA_AT_EVCS}  # die Phasen schließen den Tesla aus
    d = Detector(cfg)
    assert not any(detect(d, s, **kw).contradiction for s in range(60))
    found = detect(d, 60, **kw)
    assert (found.contradiction, found.detected) == (True, {})  # die Phasen zählen im selben Zyklus: nie erkannt


def test_an_exclusion_next_to_a_detection_is_no_contradiction(cfg) -> None:  # Präzisierung 4, Abweichung
    kw = {
        "sessions": BOTH,
        "evcs_obs": evcs(charging=True, phase_a=dict.fromkeys(PHASES, 10.0)),  # dreiphasiger Gast mit 10 A
        "twc_obs": twc(connected=True, charging=True, phase_a=dict.fromkeys(PHASES, 16.0)),
        "tesla_obs": tesla(plugged=True, charging=True, actual_current_a=16.0, phases=3),
        "homes": {"egolf": False},
    }
    d = Detector(cfg)
    assert not any(detect(d, s, **kw).contradiction for s in range(120))
    found = detect(d, 120, **kw)
    assert (found.detected, found.guests, found.contradiction) == ({"twc": "tesla"}, {"evcs"}, False)


def test_an_override_pins_its_wallbox(cfg) -> None:
    kw = {"sessions": BOTH, "twc_obs": twc(connected=True), "placed": {"egolf": "evcs"}}
    d = Detector(cfg)
    for s in range(60):
        detect(d, s, **kw)
    found = detect(d, 60, **kw)  # am Wall Connector: Tesla nicht angesteckt, e-Golf an der EVCS übersteuert
    assert (found.detected, found.guests, found.excluded["twc"]) == ({}, {"twc"}, {"tesla", "egolf"})


def test_detection_restarts_after_an_interruption(cfg) -> None:  # Review Focus 2
    d = Detector(cfg)
    for s in range(59):
        detect(d, s, **TESLA_AT_EVCS)
    detect(d, 59, tesla_obs=NONE)  # Tessie unbekannt: einen Zyklus kein Kandidat
    assert all(detect(d, s, **TESLA_AT_EVCS).detected == {} for s in range(60, 120))
    assert detect(d, 120, **TESLA_AT_EVCS).detected == {"evcs": "tesla"}


def test_holds_end_with_the_session(cfg) -> None:  # Review Focus 5
    d = Detector(cfg)
    for s in range(61):
        detect(d, s, **TESLA_AT_EVCS)
    detect(d, 61, sessions={}, evcs_obs=evcs(connected=False), **TESLA_AT_EVCS)
    assert detect(d, 62, **TESLA_AT_EVCS).detected == {}  # neuer Vorgang: die 60 s von vorn
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_detection.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** nach Präzisierung 4: erst die Indizien, dann Kandidaten und Ausschlüsse, dann die 60 s, zuletzt der Widerspruch. Ein Ausschluss, der in einem Zyklus zu zählen beginnt, verhindert in diesem Zyklus schon die Erkennung.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control -q && uv run mypy src && uv run lint-imports`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control/detection.py tests/unit/core/control/test_detection.py
git commit -m "feat(core): Entscheidung der Zuordnungserkennung: erkannt, Gast, Widerspruch"
```

---

### Task 4: Zuordnung mit Erkennung

**Files:**
- Create: `src/bindaems/core/control/fleet.py`
- Modify: `src/bindaems/core/control/assignment.py`, `src/bindaems/core/control/charging.py` (nur der Aufruf von `Assignment.update`), `src/bindaems/tools/sim/scenarios.py` (Prüfung `_shown_before`, erste Prüfung von `zuordnung`)
- Test: `tests/unit/core/control/test_fleet.py`, `tests/unit/core/control/test_assignment.py`, `tests/unit/core/control/charge_helpers.py`, `tests/unit/core/control/test_charging.py`, `tests/unit/core/control/test_charging_tesla.py`, `tests/unit/tools/sim/test_sim_checks.py`

**Interfaces:**
- Consumes: `Detector`, `Detection`, `Evidence` (Task 3), `ChargeInputs.homes` (Task 1)
- Produces (Präzisierungen 4, 5 und 9):
  - **`fleet.py`:** `GUEST: Final = "guest"`; `guest_key(wallbox: str) -> str` ergibt `guest@<wallbox>`; `guest_wallbox(key: str) -> str | None` die Wallbox eines Gast-Schlüssels, sonst `None`; `fleet(cfg: Config) -> list[str]`: die Fahrzeuge in der Reihenfolge von `config.yaml`, dahinter je Wallbox ihr Gast
  - **`assignment.py`:**
    - `AssignmentKind.DETECTED = "detected"`; Docstring: `DEFAULT` heißt angenommen
    - `VehicleState.fresh: bool = False` (letztes Feld), Präzisierung 5
    - `@dataclass(frozen=True) class WallboxState`: `occupant: str | None` (ein Schlüssel aus `fleet`), `kind: AssignmentKind | None`, `evidence: tuple[Evidence, ...]`; ohne Vorgang `None`, `None`, `()`
    - `AssignmentState`: `vehicles` je Schlüssel aus `fleet(cfg)` in dieser Reihenfolge, `unclear`, dazu `wallboxes: Mapping[str, WallboxState]` je Wallbox in der Reihenfolge von `config.yaml`. Ein Gast, der nirgends steht, hat `VehicleState(None, AssignmentKind.DEFAULT, None, None, False, False, None)`; einer an einer Wallbox `plugged`, `charging` und `complete` wie ein Fahrzeug ohne Tessie, ohne Boost.
    - `Assignment.update(now, wall_now, intent, accepted_at, wallboxes, vehicles, homes: Mapping[str, bool | None] = MappingProxyType({})) -> AssignmentState`
    - `_Port` merkt den Beginn des laufenden Vorgangs auf der Wanduhr (`started_at`, Präzisierung 9); `_contradiction` entfällt, „unklar“ kommt aus `Detection.contradiction`
  - **`ChargeController.step`** gibt `inputs.homes` an `update`. Die Gäste stehen ab jetzt in `AssignmentState.vehicles`; die Laderegelung beachtet sie erst ab Task 5 (`_order` nennt nur die Fahrzeuge der Konfiguration).
  - **`charge_helpers.py`:** `CONTRADICTION: Final[dict[str, Any]] = {"twc_obs": twc(connected=False), "tesla_obs": tesla(plugged=True), "evcs_obs": evcs(charging=True, phase_a={"L1": 10.0, "L2": 10.0, "L3": 0.0}, power_w=4600.0)}` und `UNCLEAR_FROM_S: Final = 120` (Präzisierung 13)
  - **`scenarios.py`:** `_unclear_before(key, until_s)` wird `_shown_before(key: str, kind: str, until_s: int) -> Check` mit `DETECTED: Final = "detected"` neben `UNCLEAR`; Sätze in „Texte“. Die erste Prüfung von `zuordnung` ist `_shown_before(TESLA, DETECTED, OVERRIDE_S)`.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_fleet.py
def test_fleet_is_the_vehicles_then_one_guest_per_wallbox(cfg) -> None:
    assert fleet(cfg) == ["tesla", "egolf", "guest@evcs", "guest@twc"]
    assert (guest_key("evcs"), guest_wallbox("guest@twc"), guest_wallbox("tesla")) == ("guest@evcs", "twc", None)


# tests/unit/core/control/test_assignment.py: update(...) bekommt homes=None und gibt es weiter
PICTURE = {"twc_obs": twc(connected=False), "tesla_obs": tesla(plugged=True)}  # „Tesla an der EVCS“


def test_tesla_at_the_evcs_is_detected(cfg) -> None:  # Design 7.3, letzter Punkt
    a = Assignment(cfg, started=T0)
    assert update(a, 0, **PICTURE).vehicles["egolf"].wallbox == "evcs"  # angenommen
    for s in range(1, 60):
        update(a, s, **PICTURE)
    state = update(a, 60, **PICTURE)
    tesla_ = state.vehicles["tesla"]
    assert (tesla_.wallbox, tesla_.kind, tesla_.fresh, tesla_.plugged) == ("evcs", AssignmentKind.DETECTED, True, True)
    assert (state.vehicles["egolf"].wallbox, state.unclear) == (None, False)
    assert (state.wallboxes["evcs"].occupant, state.wallboxes["evcs"].kind) == ("tesla", AssignmentKind.DETECTED)
    assert state.wallboxes["twc"] == WallboxState(None, None, ())
    assert not update(a, 61, **PICTURE).vehicles["tesla"].fresh


def test_an_excluded_default_vehicle_makes_an_assumed_guest(cfg) -> None:
    a = Assignment(cfg, started=T0)
    state = update(a, 0, homes={"egolf": False})  # e-Golf weg, Tesla nicht angesteckt
    guest = state.vehicles["guest@evcs"]
    assert (guest.wallbox, guest.kind, guest.plugged) == ("evcs", AssignmentKind.DEFAULT, True)
    assert (state.vehicles["egolf"].wallbox, state.vehicles["guest@twc"].wallbox) == (None, None)
    for s in range(1, 60):
        update(a, s, homes={"egolf": False})
    detected = update(a, 60, homes={"egolf": False}).vehicles["guest@evcs"]
    assert (detected.kind, detected.fresh) == (AssignmentKind.DETECTED, False)  # derselbe Insasse


def test_confirming_the_assumed_vehicle_is_no_new_detection(cfg) -> None:
    egolf = evcs(charging=True, phase_a={"L1": 10.0, "L2": 10.0, "L3": 0.0})
    a = Assignment(cfg, started=T0)
    states = [update(a, s, evcs_obs=egolf, homes={"egolf": True}) for s in range(121)]
    assert (states[119].vehicles["egolf"].kind, states[120].vehicles["egolf"].kind) == (
        AssignmentKind.DEFAULT, AssignmentKind.DETECTED,
    )
    assert not any(state.vehicles["egolf"].fresh for state in states)


def test_real_contradiction_makes_it_unclear(cfg) -> None:  # ersetzt test_contradiction_for_60_s_makes_it_unclear
    a = Assignment(cfg, started=T0)
    assert not any(update(a, s, **CONTRADICTION).unclear for s in range(UNCLEAR_FROM_S))
    state = update(a, UNCLEAR_FROM_S, **CONTRADICTION)
    assert state.unclear and state.vehicles["egolf"].kind is AssignmentKind.UNCLEAR
    assert (state.vehicles["egolf"].wallbox, state.vehicles["guest@evcs"].wallbox) == ("evcs", None)
    assert all(update(a, s).unclear for s in range(121, 180))
    assert not update(a, 181).unclear


def test_arriving_tesla_stays_assumed_until_tessie_polls(cfg) -> None:  # Review Focus 1
    a = Assignment(cfg, started=T0)
    stale = tesla(plugged=False, at_home=False, plugged_at=T0)  # letzte Abfrage vor dem Anstecken
    update(a, 0, twc_obs=twc(connected=False), tesla_obs=stale)
    for s in range(1, 600):
        state = update(a, s, twc_obs=twc(connected=True), tesla_obs=stale)
        assert (state.vehicles["tesla"].wallbox, state.vehicles["tesla"].kind) == ("twc", AssignmentKind.DEFAULT)


def test_tessie_offline_assumes_the_default_vehicle(cfg) -> None:  # Review Focus 1
    a = Assignment(cfg, started=T0)
    for s in range(300):
        state = update(a, s, twc_obs=twc(connected=True), tesla_obs=NONE)
        assert not state.unclear
    assert (state.vehicles["tesla"].wallbox, state.vehicles["tesla"].kind) == ("twc", AssignmentKind.DEFAULT)


def test_a_new_session_starts_without_the_old_detection(cfg) -> None:  # Review Focus 5
    a = Assignment(cfg, started=T0)
    for s in range(61):
        update(a, s, **PICTURE)
    gone = update(a, 61, evcs_obs=evcs(connected=False), twc_obs=twc(connected=False))
    assert (gone.vehicles["tesla"].wallbox, gone.wallboxes["evcs"].occupant) == ("twc", None)
    assert update(a, 62, **PICTURE).vehicles["egolf"].wallbox == "evcs"  # angenommen


def test_override_precedes_detection_until_unplugged(cfg) -> None:
    intent = Intent(version=1, control=True, vehicles={"egolf": VehicleIntent(wallbox="evcs")})
    a = Assignment(cfg, started=T0)
    for s in range(120):
        state = update(a, s, intent=intent, **PICTURE)
    assert (state.vehicles["egolf"].kind, state.vehicles["tesla"].wallbox) == (AssignmentKind.OVERRIDE, "twc")
    assert (state.wallboxes["evcs"].occupant, state.wallboxes["evcs"].kind) == ("egolf", AssignmentKind.OVERRIDE)
```

Dazu die bestehenden Tests nach Präzisierung 13:
- `test_assignment.py`: `at_the_evcs` wird `update(a, s, **CONTRADICTION, intent=intent)`; die Tests zu „unklar“ (Flattern, Übersteuerung hebt auf, Tesla weg, unbekannte Werte, keine EVCS, Unterbrechung, Übersteuerung unterbricht) rechnen mit `UNCLEAR_FROM_S` statt 60 s.
- `test_charging.py` (`test_unclear_assignment_counts_all_phases_of_the_evcs`, `vehicle_wallbox` bei „unklar“, `test_unclear_uses_the_range_of_the_evcs`) und `test_charging_tesla.py` (`test_unclear_pauses_the_tesla_and_holds_the_evcs`, `vehicle_wallbox` bei „unklar“, `UNCLEAR_EGOLF` und seine Tests): mit `CONTRADICTION`, die Zeiten 60 s später, die Ströme für zwei Phasen neu gerechnet. Der Test von `vehicle_wallbox` prüft zusätzlich das Bild „Tesla an der EVCS“: `{"vehicle.tesla": "evcs"}` ab 60 s.
- `test_sim_checks.py`: `test_the_health_must_show_unclear_before_the_override` wird `test_the_health_must_show_the_tesla_detected_before_the_override` mit dem Satz `Die Gesundheit zeigt „tesla“ vor 10 min „erkannt“.` und `"assignment": "detected"`.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control tests/unit/tools/sim/test_sim_checks.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** nach den Präzisierungen 4 und 5: Ansteckvorgänge, Bindungen, Übersteuerungen, dann `Detector.update` mit den laufenden Vorgängen und den Übersteuerungen als `placed`, dann „unklar“ (die Zeiten wie bisher, `contradiction` statt `_contradiction`), dann wer wo steht, dann die Werte je Auto und je Wallbox. `fresh` vergleicht den Insassen je Wallbox mit dem des vorigen Aufrufs.

- [ ] **Step 4: Alles laufen lassen**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports`
Expected: PASS, auch `tests/integration/test_sim_scenarios.py` mit `zuordnung` (Tesla an der EVCS bei etwa 90 s erkannt, `max_a` noch erst mit der Übersteuerung)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests
git commit -m "feat(core): Zuordnung mit Erkennung: erkannt, angenommen, Gast und echter Widerspruch"
```

---

### Task 5: Laderegelung mit Gast, erkanntem Tesla an der EVCS und Grund

**Files:**
- Modify: `src/bindaems/core/control/fleet.py`, `charge_points.py`, `charging.py`, `charging_tesla.py`, `src/bindaems/shared/decisions.py`
- Test: `tests/unit/core/control/test_fleet.py`, `test_charge_points.py`, `test_charging.py`, `test_charging_tesla.py`, `charge_helpers.py`, `tests/unit/shared/test_decisions.py`

**Interfaces:**
- Consumes: `fleet`, `guest_wallbox`, `AssignmentState` mit Gästen, `VehicleState.fresh`, `AssignmentKind.DETECTED` (Task 4)
- Produces (Präzisierungen 6 bis 8):
  - **`fleet.py`:** `GUEST_NAME: Final = "Gast"`, `GUEST_MIN_A: Final = 6`; `@dataclass(frozen=True) class CarSpec`: `name: str`, `phases: int`, `min_a: int`, `max_a: int`, `tessie: bool`; `car_spec(cfg: Config, key: str) -> CarSpec`: für ein Fahrzeug aus `VehicleConfig`, für einen Gast an einer EVCS deren `min_a` und `max_a`, am Wall Connector `GUEST_MIN_A` und dessen `max_a`, immer 3 Phasen und ohne Tessie
  - **`charge_points.py`:** `_point` liest `car_spec` statt `cfg.vehicles[key]`; **`charging._car_view`** ebenso
  - **`ChargeController`:**
    - eine `ModeMachine` je Schlüssel aus `fleet(cfg)`; `TeslaPolicy` weiter nur je Fahrzeug der Konfiguration, und `_decide` startet die Fenster nur für diese neu
    - `_order(intent)`: die bisherige Reihenfolge, dahinter die Gäste in der Reihenfolge von `fleet`
    - `_mode`: für einen Gast `intent.guest.mode` (ohne Absicht `GuestIntent().mode`), nie Boost
    - `idle` merkt die erste Startfolge für alle Schlüssel aus `fleet`
    - Grund nach Präzisierung 8: ein Satz neu erkannter Autos; die Befehle eines Autos darin bekommen `ASSIGNMENT_DETECTED` angehängt
  - **`charging_tesla.py`:** `ASSIGNMENT_DETECTED: Final = "assignment_detected"`; `EvcsOverrides.update` nimmt `OVERRIDE` und `DETECTED` und übergeht Schlüssel, die keine Fahrzeuge der Konfiguration sind
  - **`decisions.py`:** `_REASON_TEXT["assignment_detected"] = "Zuordnung erkannt"`
  - **`charge_helpers.inputs`** bekommt `homes: Mapping[str, bool | None] | None = None`

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_fleet.py
def test_a_guest_has_all_phases_and_the_range_of_its_wallbox(cfg) -> None:
    assert car_spec(cfg, "guest@evcs") == CarSpec("Gast", 3, 6, 16, False)
    assert car_spec(cfg, "guest@twc") == CarSpec("Gast", 3, 6, 16, False)
    assert car_spec(cfg, "egolf") == CarSpec("e-Golf", 2, 6, 16, False)
    assert car_spec(cfg, "tesla") == CarSpec("Tesla Model 3", 3, 5, 16, True)


# tests/unit/core/control/test_charge_points.py
def test_a_guest_gets_the_whole_evcs_and_is_load_at_the_wall_connector(cfg) -> None:
    vehicles = {"tesla": state(None), "egolf": state(None), "guest@evcs": state("evcs"), "guest@twc": state("twc")}
    points = charge_points(cfg, HardLimits.from_config(cfg), list(vehicles), vehicles, inputs(cfg, twc_obs=twc(connected=True)))
    guest = points["guest@evcs"]
    assert (guest.actuator, guest.phases, guest.min_a, guest.max_a, guest.regulated) == (
        "wallbox.evcs", ("L1", "L2", "L3"), 6, 16, True,
    )
    assert (points["guest@twc"].actuator, points["guest@twc"].regulated) == (None, False)


# tests/unit/core/control/test_charging_tesla.py
DETECTED_AT_THE_EVCS = {"twc_obs": twc(connected=False), "evcs_obs": evcs()}  # mit tesla_inputs: zu Hause, angesteckt
TESLA_NOW_EGOLF_OFF = Intent(
    version=1,
    control=True,
    vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW), "egolf": VehicleIntent(mode=ChargeMode.OFF)},
)
DETECTED = ("mode_now", "assignment_detected")


def test_a_detected_tesla_at_the_evcs_gets_its_maximum_once(cfg) -> None:  # Design 7.4
    found = run(controller(cfg), cfg, 300, make=tesla_inputs, intent=TESLA_NOW_EGOLF_OFF, **DETECTED_AT_THE_EVCS)
    assert [c.command for t, c in found if t == 60] == [
        Command("vehicle.tesla", CURRENT, 16.0, reasons=DETECTED),
        Command("wallbox.evcs", CURRENT, 16.0, reasons=DETECTED),
        Command("wallbox.evcs", CHARGING, True, reasons=DETECTED),
    ]
    assert [t for t, c in found if c.command.actuator == "vehicle.tesla"] == [60]


def test_new_detection_marks_only_the_first_commands(cfg) -> None:  # Review Focus 4
    found = run(controller(cfg), cfg, 300, make=tesla_inputs, intent=TESLA_NOW_EGOLF_OFF, **DETECTED_AT_THE_EVCS)
    assert {t for t, c in found if "assignment_detected" in c.command.reasons} == {60}


def test_handover_from_the_assumed_to_the_detected_car_starts_once(cfg) -> None:  # Review Focus 4
    # der e-Golf in Sofort angenommen an der EVCS, ab 60 s der Tesla erkannt (Sofort)
    both_now = Intent(
        version=1,
        control=True,
        vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW), "egolf": VehicleIntent(mode=ChargeMode.NOW)},
    )
    found = run(controller(cfg), cfg, 300, make=tesla_inputs, intent=both_now, **DETECTED_AT_THE_EVCS)
    starts = [
        t
        for t, c in found
        if c.command.actuator == "wallbox.evcs" and c.command.kind is CHARGING and c.command.value is True
    ]
    assert starts == [0, 60]  # der e-Golf bei 0, nach der Übergabe einmal der Tesla
    assert [t for t, c in found if c.command.actuator == "vehicle.tesla"] == [60]


# tests/unit/core/control/test_charging.py
GUEST_NOW = Intent(version=1, control=True, guest=GuestIntent(mode=ChargeMode.NOW))


def test_a_guest_at_the_evcs_charges_in_the_guest_mode(cfg) -> None:  # Design 7.4
    c = controller(cfg)
    found = run(c, cfg, 61, intent=GUEST_NOW, homes={"egolf": False})
    assert [(t, x.command) for t, x in found] == [
        (0, Command("wallbox.evcs", CURRENT, 16.0, reasons=("mode_now",))),
        (0, Command("wallbox.evcs", CHARGING, True, reasons=("mode_now",))),
    ]
    assert c.health()[1]["wallbox"] is None  # der e-Golf steht nirgends


def test_a_guest_at_the_wall_connector_is_load(cfg) -> None:  # Design 7.4
    c = controller(cfg)
    twc_16 = twc(connected=True, charging=True, phase_a=dict.fromkeys(("L1", "L2", "L3"), 16.0), power_w=11040.0)
    found = run(c, cfg, 61, twc_obs=twc_16, homes={"egolf": False}, controllable=BOTH)
    assert not any(x.command.actuator == "vehicle.tesla" for _, x in found)
    guest = next(car for car in c.cars() if car.wallbox == "twc")
    assert (guest.vehicle, guest.mode, guest.regulated) == ("guest@twc", Effective.PV, False)


def test_the_egolf_at_the_wall_connector_is_load_in_its_mode(cfg) -> None:  # Design 7.4
    c = controller(cfg)
    egolf = twc(connected=True, charging=True, phase_a={"L1": 16.0, "L2": 16.0, "L3": 0.0}, power_w=7360.0)
    intent = Intent(version=1, control=True, vehicles={"egolf": VehicleIntent(mode=ChargeMode.MIN_PV)})
    found = run(
        c, cfg, 121, twc_obs=egolf, evcs_obs=evcs(connected=False), homes={"egolf": True}, intent=intent,
        controllable=BOTH,
    )
    assert not any(x.command.actuator == "vehicle.tesla" for _, x in found)
    car = next(car for car in c.cars() if car.wallbox == "twc")  # erkannt bei 120 s: Phasen ab 60 s
    assert (car.vehicle, car.mode, car.regulated) == ("egolf", Effective.MIN_PV, False)
    assert c.health()[1]["assignment"] == "detected"


# tests/unit/shared/test_decisions.py
def test_assignment_detected_has_its_text() -> None:
    shown = decision_text(
        "Tesla Model 3", CommandKind.CURRENT, 16.0, DecisionResult.OK, reasons=("mode_now", "assignment_detected")
    )
    assert shown == "Tesla Model 3 (Sofort, Zuordnung erkannt): Strom 16 A – gesendet und bestätigt."
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control tests/unit/shared/test_decisions.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces; die Docstrings von `ChargeController`, `EvcsOverrides` und Modul nennen Gast und Erkennung.

- [ ] **Step 4: Alles laufen lassen**

Run: wie Task 4, Step 4
Expected: PASS; in `zuordnung` bekommt der Tesla `max_a` jetzt mit der Erkennung, und die Übersteuerung bei 10 min schickt keinen zweiten (`_one_current` bleibt erfüllt).

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests
git commit -m "feat(core): Laderegelung mit Gast, erkanntem Tesla an der EVCS und Grund assignment_detected"
```

---

### Task 6: Health je Wallbox

**Files:**
- Modify: `src/bindaems/core/control/charging.py`, `src/bindaems/core/control/regulation.py`, `src/bindaems/core/api/app.py`, `src/bindaems/core/runtime.py`, `ui/src/lib/api/contract/*` (neu geschrieben)
- Test: `tests/unit/core/control/test_charging.py`, `tests/unit/core/control/test_regulation.py`, `tests/integration/test_runtime_regulation.py`, `tests/integration/test_ui_contract.py`

**Interfaces:**
- Consumes: `AssignmentState.wallboxes` (Task 4), `guest_wallbox`, `GUEST`, `car_spec` (Tasks 4 und 5)
- Produces (Präzisierung 11):
  - `ChargeController.wallbox_health() -> list[dict[str, Any]]`: je Wallbox `{"id", "occupant", "name", "assignment", "indicators"}`, je Indiz `{"vehicle": key, "clue": clue.value, "supports": bool}`; vor dem ersten `step` und nach `idle` ohne Insassen
  - `Regulation.health()` hat dazu `wallboxes` (`ChargeController.wallbox_health`), hinter `peak`
  - `HealthReport.wallboxes: list[dict[str, Any]] = Field(default_factory=list)`, mit einer Zeile im Docstring
  - `CoreRuntime.health()` gibt `wallboxes=regulation["wallboxes"]`

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_charging.py
NOBODY = [
    {"id": wallbox, "occupant": None, "name": None, "assignment": None, "indicators": []}
    for wallbox in ("evcs", "twc")
]


def test_wallbox_health_shows_who_stands_where(cfg) -> None:
    c = controller(cfg)
    run(c, cfg, 61, twc_obs=twc(connected=False), tesla_obs=tesla(plugged=True))
    assert c.wallbox_health() == [
        {
            "id": "evcs",
            "occupant": "tesla",
            "name": "Tesla Model 3",
            "assignment": "detected",
            "indicators": [
                {"vehicle": "tesla", "clue": "tessie", "supports": True},
                {"vehicle": "tesla", "clue": "elsewhere", "supports": True},
            ],
        },
        NOBODY[1],
    ]


def test_wallbox_health_names_a_guest(cfg) -> None:
    c = controller(cfg)
    run(c, cfg, 1, homes={"egolf": False})
    evcs_ = c.wallbox_health()[0]
    assert (evcs_["occupant"], evcs_["name"], evcs_["assignment"]) == ("guest", "Gast", "default")


def test_wallbox_health_is_empty_before_the_first_step_and_after_idle(cfg) -> None:
    c = controller(cfg)
    assert c.wallbox_health() == NOBODY
    run(c, cfg, 1)
    c.idle(T0 + timedelta(seconds=1))
    assert c.wallbox_health() == NOBODY


# tests/unit/core/control/test_regulation.py
def test_health_has_the_wallboxes(cfg) -> None:
    health = Regulation(cfg, HardLimits.from_config(cfg), started=T0).health()
    assert list(health) == ["vehicles", "battery", "peak", "wallboxes"]


# tests/integration/test_runtime_regulation.py
async def test_health_reports_who_stands_where(runtime_env) -> None:
    # nach einigen Zyklen in CONTROL: [w["id"] for w in rt.health().wallboxes] == ["evcs", "twc"]
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control tests/integration/test_runtime_regulation.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest -q`, dann die Vertragsdateien der UI neu schreiben (`UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py`), dann `cd ui && pnpm check && pnpm test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests ui/src/lib/api/contract
git commit -m "feat(core): Health zeigt je Wallbox, wer dort steht, Art und Indizien"
```

---

### Task 7: Simulator – Home Assistant, Gast und Tesla woanders

**Files:**
- Modify: `src/bindaems/tools/sim/cars.py`, `model.py`, `runner.py`, `scenarios.py` (`_setup`, `_intent`)
- Test: `tests/unit/tools/sim/test_sim_model.py`, `tests/unit/tools/sim/test_sim_runner.py`, `tests/unit/tools/sim/test_sim_checks.py`

**Interfaces:**
- Consumes: `home_signal` (Task 1), `SOURCE` aus `core/adapters/homeassistant.py`
- Produces (Präzisierung 12):
  - **`cars.py`:** `Tesla.elsewhere: bool = False`; `charging_state()` ergibt „Stopped“, wenn er an keiner Wallbox steckt und `elsewhere` gilt
  - **`model.py`:**
    - `GUEST_CAR: Final = "gast"`; `PlantModel.cars[GUEST_CAR]` ist ein `Car` aus `VehicleConfig(name="Gast", usable_kwh=50.0, phases=3, min_a=6, max_a=16, default_wallbox=<erste Wallbox>)`, abgesteckt
    - `PlantModel.away: set[str]` (Standard leer)
    - `attach` registriert die Quelle `ha` (ohne Frischegrenze, `invalidate_on_reconnect=True`), wenn ein Fahrzeug `home_entity` hat; `publish` schreibt je solchem Fahrzeug `home_signal(key)` mit `home` oder `not_home` (Art `STATE`, Quelle `ha`) und meldet die Quelle verbunden
  - **`runner._sample`:** hinter den Fahrzeugen `cars[GUEST_CAR]`: Modus `intent.guest.mode.value` (ohne Absicht `pv`), `min_a` das `min_a` der EVCS, an der er steckt, sonst 6, `steered` wie `_steered` für den Schlüssel `GUEST_CAR`
  - **`scenarios.py`:**
    - `_setup(..., guest: str | None = None, guest_phases: int = 3, away: frozenset[str] = frozenset(), tesla_elsewhere: bool = False, evcs_on: bool = False)`: steckt den Gast an `guest`, setzt seine Phasen, `model.away`, `Tesla.elsewhere` und mit `evcs_on` `start_stop = 1` an der EVCS `EVCS_BOX`
    - `_intent(*, guest_mode: ChargeMode = ChargeMode.PV, **modes)`: mit `GuestIntent(mode=guest_mode)`
  - Die 18 Szenarien laufen unverändert und grün; der Gast steckt in keinem.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/tools/sim/test_sim_model.py
def test_home_assistant_reports_home_or_away(model, store, clock) -> None:
    run(model, store, clock, 1)
    reading = store.snapshot().get("vehicle.egolf.home_state")
    assert (reading.value, reading.source, reading.kind) == ("home", "ha", SignalKind.STATE)
    model.away = {"egolf"}
    run(model, store, clock, 1)
    assert store.snapshot().ok("vehicle.egolf.home_state") == "not_home"


def test_the_guest_charges_on_its_phases(model, store, clock) -> None:
    model.cars[GUEST_CAR].phases = 1
    model.plug(GUEST_CAR, "twc")
    run(model, store, clock, 1)
    assert (model.car_a[GUEST_CAR], model.car_w[GUEST_CAR]) == (16.0, 16.0 * 230.0)


def test_a_tesla_plugged_elsewhere_reports_stopped(model) -> None:
    car = model.teslas["tesla"]
    car.elsewhere = True
    values = car.poll(T0, 230.0, reachable=True)
    assert (car.charging_state(), values["plugged"], values["at_home"]) == ("Stopped", True, True)


# tests/unit/tools/sim/test_sim_runner.py
def test_samples_carry_the_guest_in_the_guest_mode(cfg) -> None:
    # Gast an der EVCS, Absicht mit guest.mode now und EVCS live: cars["gast"] mit mode "now", min_a 6, steered;
    # abgesteckt: charging False


# tests/unit/tools/sim/test_sim_checks.py
def test_setup_puts_the_guest_and_the_tesla_elsewhere(cfg) -> None:
    # _setup(model, soc_pct=60.0, tesla=None, tesla_elsewhere=True, guest="evcs", guest_phases=2,
    #        away=frozenset({"egolf"}), evcs_on=True): Gast an der EVCS mit 2 Phasen, Tesla.elsewhere,
    #        model.away == {"egolf"}, start_stop der EVCS 1
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools/sim -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools tests/integration/test_sim_scenarios.py -q`
Expected: PASS; alle 18 Szenarien grün und unverändert.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools/sim tests/unit/tools
git commit -m "feat(sim): Home Assistant mit e-Golf zu Hause oder weg, Gastfahrzeug und Tesla woanders angesteckt"
```

---

### Task 8: Simulator – Szenarien für die Zuordnung

**Files:**
- Modify: `src/bindaems/tools/sim/scenarios.py`, `docs/betrieb.md` (Abschnitt „Simulator“, Tabelle der Szenarien)
- Test: `tests/integration/test_sim_scenarios.py`, `tests/unit/tools/sim/test_sim_checks.py`

**Interfaces:**
- Consumes: Tasks 4 bis 7, die bestehenden Prüfungen (`_shown_before`, `_one_current`, `_charges_with`, `_evcs_off_from`)
- Produces (Design 10.3, Präzisierung 13):
  - neue Prüfungen `_clear_from(from_s: int) -> Check`, `_occupied_from(wallbox: str, occupant: str, from_s: int) -> Check` (liest `health.wallboxes`), `_no_decision_at(actuator: str) -> Check`; Sätze in „Texte“
  - `zuordnung` umgebaut, drei neue Szenarien in `SCENARIOS` hinter den 18 bestehenden. Alle haben `live=True` und Victron im Dry-Run; Hauslast und Rauschen wie bisher.

| Name | Titel | Start, Dauer, Saat | Aufbau und Absicht | Prüfungen |
|---|---|---|---|---|
| `zuordnung` (geändert) | Zuordnung unklar und Übersteuerung | `EVENING`, 1 h, 9 | SOC 60; Tesla woanders angesteckt (`tesla_elsewhere`); e-Golf an der EVCS, die schon lädt (`evcs_on`); e-Golf in Sofort; bei 10 min Übersteuerung `_Override(2, EGOLF, EVCS_BOX)`, bei 40 min `_Unplug(EGOLF)` | `_shown_before(EGOLF, UNCLEAR, OVERRIDE_S)`; `_clear_from(OVERRIDE_S + MINUTE_S)`; `_evcs_off_from(EVCS_BOX, EVCS_OFF_S)` |
| `tesla-evcs` | Tesla an der EVCS ohne Übersteuerung | `EVENING`, 30 min, 19 | SOC 60; Tesla an der EVCS, lädt selbst, 10 A im Auto; e-Golf abgesteckt; Tesla in Sofort | `_shown_before(TESLA, DETECTED, 2 * MINUTE_S)`; `_one_current(TESLA, TESLA_MAX_A)`; `_charges_with(TESLA, TESLA_MAX_A)` |
| `egolf-twc` | e-Golf am Wall Connector | `EVENING`, 30 min, 20 | SOC 60; e-Golf am Wall Connector; Tesla abgesteckt; e-Golf in PV-Überschuss | `_occupied_from(WALL_CONNECTOR, EGOLF, 4 * MINUTE_S)`; `_no_decision_at(vehicle_actuator(TESLA))`; `_charges_with(EGOLF, 16.0)` |
| `gast-evcs` | Gast an der EVCS, e-Golf weg | `EVENING`, 30 min, 21 | SOC 60; Gast mit 3 Phasen an der EVCS; e-Golf abgesteckt und weg (`away`); Tesla abgesteckt; `guest_mode` Sofort | `_occupied_from(EVCS_BOX, GUEST, 3 * MINUTE_S)`; `_charges_with(GUEST_CAR, 16.0)` |

- In `zuordnung` lädt die EVCS den e-Golf schon vor dem Start des core. So zählt die Phasen-Signatur, die den Tesla an der EVCS ausschließt, im selben Zyklus, in dem seine 60 s als Kandidat um wären; er wird nie kurz erkannt. Der Docstring des Moduls nennt das und die bewusste Änderung (Design 10.3).
- In `tesla-evcs` und `gast-evcs` zeigt der Strom am Ende den Modus: Abends ohne PV lüde ein Auto in PV-Überschuss nicht. Am Ende mit 16 A lädt es also im Modus Sofort des Tesla bzw. des Gastes.
- Scheitert ein Szenario, liegt der Fehler zuerst in Erkennung oder Regelung: ein eigener `fix(core)`-Commit mit Test. Ein Szenario ändert man nur, wenn es physikalisch nicht zu schaffen ist; dann steht die Abweichung im Docstring des Moduls.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/integration/test_sim_scenarios.py
def test_assignment_scenarios_are_registered() -> None:
    assert list(SCENARIOS)[-3:] == ["tesla-evcs", "egolf-twc", "gast-evcs"]
    assert len(SCENARIOS) == 21


# tests/unit/tools/sim/test_sim_checks.py (health(...) bekommt wallboxes=[])
def test_the_health_must_show_unclear_before_the_override(cfg) -> None:
    assert check("zuordnung", 0).text == "Die Gesundheit zeigt „egolf“ vor 10 min „unklar“."
    # wie bisher: gezeigt bei 590 s → []; erst bei 600 s → Befund


def test_after_the_override_nothing_may_be_unclear(cfg) -> None:
    clear = check("zuordnung", 1)
    assert clear.text == "Ab 11 min zeigt die Gesundheit kein Fahrzeug „unklar“."
    # „egolf“ unklar bei 650 s → []; bei 700 s → ["Bei 700 s zeigt die Gesundheit „egolf“ „unklar“."]


def test_the_tesla_must_be_detected_within_2_min(cfg) -> None:
    assert check("tesla-evcs", 0).text == "Die Gesundheit zeigt „tesla“ vor 2 min „erkannt“."


def test_exactly_one_decision_tesla_current_16_a(cfg) -> None:  # bisher „zuordnung“
    assert check("tesla-evcs", 1).text == "Genau eine Entscheidung „Strom 16 A“ an „tesla“."
    # Befunde wie bisher


def test_the_wall_connector_must_show_the_egolf(cfg) -> None:
    occupied = check("egolf-twc", 0)
    assert occupied.text == "Ab 4 min steht an „twc“ laut Gesundheit „egolf“, erkannt."
    # ab 240 s je Gesundheit {"id": "twc", "occupant": "egolf", "assignment": "detected"} → [];
    # bei 300 s „guest“ → ["Bei 300 s steht an „twc“ nicht „egolf“, erkannt."]


def test_no_decision_may_reach_the_tesla(cfg) -> None:
    none = check("egolf-twc", 1)
    assert none.text == "Keine Entscheidung an „vehicle.tesla“."
    # eine Entscheidung an den Tesla bei 90 s → ["1 Entscheidungen an „vehicle.tesla“, die erste bei 90 s."]
```

Die bestehende Parametrisierung über `SCENARIOS` läuft die Szenarien mit und verlangt `result.failures == []`.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/integration/test_sim_scenarios.py tests/unit/tools/sim/test_sim_checks.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen:** die Prüfungen und Szenarien; die Tabelle der Szenarien im Betriebshandbuch („Simulator“) um die drei neuen ergänzen und `zuordnung` neu beschreiben (`tests/unit/test_docs.py` vergleicht sie mit `SCENARIOS`).

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/integration/test_sim_scenarios.py tests/unit/tools/sim tests/unit/test_docs.py -q`, dann `uv run python -m bindaems.tools.sim --szenario zuordnung --out /tmp/zuordnung.md` und den Bericht lesen
Expected: PASS; alle 21 Szenarien grün.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools/sim/scenarios.py tests docs/betrieb.md
git commit -m "test(sim): Szenarien für die Zuordnungserkennung, zuordnung mit echtem Widerspruch"
```

---

### Task 9: Doku und Abdeckung

**Files:**
- Modify: `docs/betrieb.md`, `docs/superpowers/specs/2026-10-08-bindaems-design.md`, `docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md` (Kopf), `docs/uebergabe.md`
- Test: `tests/unit/test_docs.py`

**Interfaces:**
- Consumes: alles Vorige
- Produces:
  - **Betriebshandbuch,** Abschnitt „Zuordnung Auto und Wallbox“ (unter „Laderegelung“) neu:
    - wer wo steht: erkannt, angenommen, Gast (auch „Gast, angenommen“), Übersteuerung und unklar, mit den Indizien in Sätzen für den Betreiber
    - `home_entity`: einrichten (ein `device_tracker`, `home` heißt zu Hause), Folge eines späten Ankommens (Präzisierung 3)
    - Gast: Modus per `PUT /v1/intent` (`guest.mode`) mit einem JSON-Beispiel; Gast an der EVCS geregelt, am Wall Connector Last
    - Health: `wallboxes` mit allen Feldern; `vehicles[].assignment` mit `detected`
    - Übersteuerung wie bisher; sie geht der Erkennung vor
    - Inbetriebnahme: Die Erkennung läuft im Dry-Run mit; Health zeigt, wer wo steht (Design 11.1, Punkt 3)
  - **Gründe-Tabelle** in „Laderegelung“ um `assignment_detected`; die Liste der Fahrzeugfelder der Konfiguration um `home_entity`
  - **`tests/unit/test_docs.py`:** `CHARGING_REASONS` um `assignment_detected`; ein Test, dass der Abschnitt „Laderegelung“ die Zuordnung beschreibt
  - **Spec:** Absatz „Stand Plan 2c-2b“ in 7.8 (wer wo steht, Indizien, Entscheidung, Gast, Abweichung beim Widerspruch, Tessie-Werte vor dem Vorgang, Health je Wallbox)
  - **Design 2c-2:** Kopf „Stand: freigegeben am 14.10.2026; Plan 2c-2a umgesetzt (Abschnitte 1 bis 6, 8 bis 11), Plan 2c-2b umgesetzt (Abschnitt 7)“ und in der Liste darunter die Präzisierungen 3 und 4 dieses Plans
  - **Übergabe:** Kurzfassung und Stand, nächster Schritt Plan 2d, Inbetriebnahme Punkt 3, Testzahl, Rulings und aufgeschobene Kleinigkeiten der Phase

- [ ] **Step 1: Failing Test schreiben**

```python
# tests/unit/test_docs.py
CHARGING_REASONS = {..., "assignment_detected"}  # die bestehende Menge, um den neuen Grund ergänzt


def test_operations_guide_describes_the_assignment() -> None:
    section = _guide_section("Laderegelung")
    for topic in (
        "### Zuordnung Auto und Wallbox", "home_entity", '"wallboxes"', "Gast, angenommen", "guest", "detected",
    ):
        assert topic in section
```

- [ ] **Step 2: Test laufen lassen**

Run: `uv run pytest tests/unit/test_docs.py -q`
Expected: FAIL

- [ ] **Step 3: Doku schreiben** wie in den Interfaces.

- [ ] **Step 4: Alles laufen lassen**

Run:
```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports
uv run pytest -q --cov --cov-report=term --cov-fail-under=75
uv run coverage report --include="src/bindaems/core/safety/*,src/bindaems/core/control/*,src/bindaems/core/actuators/*" --fail-under=90
cd ui && pnpm lint && pnpm check && pnpm test
```
Expected: alles grün, Abdeckung in den Grenzen.

- [ ] **Step 5: Commit**

```bash
git add docs tests/unit/test_docs.py
git commit -m "docs: Zuordnungserkennung in Betriebshandbuch, Spec und Übergabe"
```

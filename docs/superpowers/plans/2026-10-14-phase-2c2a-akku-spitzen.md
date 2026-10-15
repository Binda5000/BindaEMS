# Phase 2c-2a – Akku und Spitzen: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BindaEMS steuert den Hausakku über den Netz-Sollwert-Override und hält auf Wunsch ein 15-min-Ziel:
- Umbau vorab: ein Regelungs-Kern, `charging.py` und `runtime.py` aufgeteilt, eine Regelung, die eine Ausnahme übersteht
- Akku-Sollwert: Der Akku liefert den Autos nur nach den Regeln der Akku-Unterstützung; der core frischt den Override vor seinem Timeout auf
- 15-min-Wächter mit Zielwert (Standard aus): Wallboxen drosseln, mit dem Akku überbrücken, sonst warnen
- Simulator mit ESS-Override, Verfall und DESS; acht neue Szenarien mit Victron live

Danach kann Victron in den Dry-Run und, mit abgeschaltetem DESS, live.

**Architecture:**
- **Rein in `core/control`** (ohne I/O, Zeit als Argument):
  - `regulation.py`: `Regulation`, der Kern und die einzige Schnittstelle der Laufzeit zur Regelung
  - `battery.py`: Akku-Sollwert (Fehlbetrag, Unterstützung, Override, Auffrischen)
  - `peak_guard.py`: 15-min-Wächter (Messung, Budget, Drosselung)
  - `last_values.py`: die letzten eigenen Werte, gemeinsam für Lade- und Akku-Regelung
  - `charge_points.py`, `timing.py`: Teile aus dem Umbau
- **Neben der Laufzeit:** `core/regulation_link.py` baut die Eingaben des Kerns, fängt seine Ausnahmen und legt Health und Alarme ab. `runtime.py` spricht nur noch mit diesem Modul.
- **Safety-Schicht:** `Command.refresh` lässt ein Auffrischen des Overrides durch; der Ausführer protokolliert ein gelungenes Auffrischen nicht.
- **Simulator:** ESS regelt auf den Override, der nach 300 s verfällt; DESS verwirft ihn. Ein Szenario kann Victron live schalten.

**Tech Stack:** wie Plan 2c-1: Python 3.12 mit uv, pydantic v2, FastAPI, aiomqtt, pymodbus und httpx; Tests mit pytest, hypothesis, respx und pytest-cov; ruff, mypy und import-linter.

**Spec:** `docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md` (im Plan „Design“), Abschnitte 1 bis 6 und 8 bis 11.
- Abschnitt 7 (Zuordnungserkennung) und die Teile von 3, 9 und 10 für die Erkennung gehören zu Plan 2c-2b (Präzisierung 1).
- Verweise wie „Design 5.3“ beziehen sich auf das Design, „Spec 7.6“ auf `docs/superpowers/specs/2026-10-08-bindaems-design.md`, „Design 2c-1“ auf `docs/superpowers/specs/2026-10-12-phase-2c1-laderegelung-design.md`.
- Wo das Design von der Spec abweicht, gilt das Design (Design 11.2). Die Präzisierungen unten gehen beiden vor.
- Vorgänger sind die Pläne 2a, 2b und 2c-1, alle umgesetzt. Ihre Global Constraints gelten weiter.

## Einordnung

2c-2 ist in zwei Pläne geteilt (Design, Entscheidung 7):
- **2c-2a Akku und Spitzen** (dieser Plan)
- 2c-2b Zuordnung (eigener Plan, danach)

Ergebnis von 2c-2a:
- Alle Szenarien des Simulators sind in der CI grün, die zehn bestehenden unverändert.
- Victron kann an der Anlage in den Dry-Run, sobald Dynamic ESS aus ist; live erst nach Design 11.1, Punkt 2.
- EVCS und Tesla bleiben im Dry-Run neben evcc bis nach 2d (Design 2c-1, 14.2).

Stand an der Anlage am 14.10.2026: 2c-1 ist nicht ausgerollt; evcc regelt beide Ladepunkte, Dynamic ESS ist an.

## Präzisierungen gegenüber dem Design

1. **Zuschnitt (Design 1).** Nicht in 2c-2a, sondern in 2c-2b:
   - `vehicles.<name>.home_entity` (Design 3.2) und der Grund `assignment_detected` (Design 9)
   - im Simulator der e-Golf „zu Hause“ oder „weg“, das Gastfahrzeug und „jedes Auto an jede Wallbox“ (Design 10.1), die Szenarien aus Design 10.3
   - Health je Wallbox (Design 9)

   In 2c-2a kommen von der Gast-Regel nur die Absicht `guest.mode` und ihr Modus für den Akku-Sollwert (Präzisierung 4).
2. **Umbau (Design 4.4).** Die Tasks 1 bis 3 ändern kein Verhalten:
   - Alle bestehenden Tests und Szenarien bleiben grün, ohne dass eine Testdatei sich ändert; neu sind nur Tests für neue Module.
   - Was wohin zieht, steht in den Tasks 1 bis 3. `charging.py` hat danach rund 700 Zeilen, `runtime.py` rund 1200.
   - `ChargeCommand` bleibt als Name für `ControlCommand` erhalten, `rt.charger` als Eigenschaft bis Task 8.
3. **Reihenfolge im Kern (Design 4.2).** `Regulation.step` rechnet:
   1. die Laderegelung (`ChargeController.step`) mit den Grenzen, die der 15-min-Wächter im vorigen Zyklus bestimmt hat
   2. die Autos je Wallbox aus der Zuordnung und den Modi dieses Zyklus (`ChargeController.cars`), dazu die Wallboxen ohne Fahrzeug
   3. Fehlbetrag und erlaubten Akku-Anteil (`BatteryController.assess`)
   4. den 15-min-Wächter (`PeakGuard.step`): Budget für diesen Zyklus, Grenzen für den nächsten
   5. den Override (`BatteryController.decide`)

   Die Grenzen des Wächters wirken so einen Zyklus (1 s) später. Dafür rechnen Akku-Sollwert und Wächter mit Zuordnung und Modi desselben Zyklus wie die Laderegelung. Die Befehle kommen in der Reihenfolge des Designs: erst die der Ladepunkte, dann der Override.
4. **Autos für den Akku-Sollwert (Design 5.2).**
   - **Die Reihenfolge:** erst die Fahrzeuge mit Ladepunkt nach Priorität (wie die Laderegelung), dann die Wallboxen ohne Fahrzeug in der Reihenfolge von `config.yaml`.
   - **Der Modus:** der geltende des Fahrzeugs (mit Boost); eine Wallbox ohne Fahrzeug hat `intent.guest.mode`. Das gilt auch, solange evcc die Ladepunkte regelt (EVCS und Tesla im Dry-Run, Design 5.2).
   - **Die Leistung:** `WallboxObs.power_w`. Fehlt sie, bei einem Fahrzeug mit Tessie `actual_current_a × phases × grid.voltage_nominal_v`, wenn Tessie erreichbar ist und beides kennt.
   - **Unbekannt:** Fehlt die Leistung und lädt das Auto nicht sicher nicht (`charging` ist nicht `False`), ist der gewollte Netzbezug unbekannt. Ein Auto, das sicher nicht lädt, zählt mit 0 W.
   - **Fehlbetrag:** Die PV (`Derived.pv_total_w`, mindestens 0) deckt zuerst die Hauslast (`Derived.house_load_w`, mindestens 0), dann die Autos in dieser Reihenfolge, wie `allocate` der Abrechnung.
5. **Unterstützung (Design 5.2).**
   - **SOC:** Sie wird möglich, wenn der SOC `support_soc_pct` erreicht, und gesperrt, wenn er unter `support_soc_pct − support_hysteresis_pct` fällt. Ein unbekannter SOC sperrt sie im Zyklus, ändert den Stand der Hysterese aber nicht.
   - **„Ein Auto hat einen Fehlbetrag“** heißt: Die Autos in PV-Überschuss und Min+PV haben zusammen mindestens `setpoint_deadband_w / 2` (100 W) Fehlbetrag. Darunter entsteht ohnehin kein Override (Präzisierung 6); das Rauschen der Hauslast soll keine Überbrückung beginnen oder verlängern.
   - **Überbrückung:** Sie beginnt mit dem ersten Zyklus mit Fehlbetrag und endet nach 60 s ohne (`BRIDGE_END_S`). Ab `support_max_s` ist die Unterstützung gesperrt, bis die Überbrückung endet.
   - **Stand für Health:** `on` (gilt), sonst der Grund der Sperre in dieser Reihenfolge: `mode` (kein Auto in PV-Überschuss oder Min+PV), `soc`, `time`.
   - **Nach einer Pause** (`idle`) beginnt der Stand neu: Der SOC muss `support_soc_pct` wieder erreichen.
   - **Verteilung:** In der Reihenfolge von Präzisierung 4 bekommt jedes Auto in PV-Überschuss oder Min+PV `min(Fehlbetrag, Rest von support_max_w)`, ein Auto in Boost seinen ganzen Fehlbetrag, Sofort und Aus nichts.
6. **Override (Design 5.3).**
   - **Ziel:** `T = round10(Basis + W')` mit `round10(x) = 10 · floor(x / 10 + 0,5)`. `W'` ist der gewollte Netzbezug nach dem Wächter (Präzisierung 12), die Basis `ess.grid_setpoint_w` (`ok_number`). Ist die Basis unbekannt, ist das Ziel unbekannt.
   - **An, aus und Änderung** gegenüber dem letzten eigenen Wert:
     - eine Zahl: Liegt `W'` unter 100 W (`setpoint_deadband_w / 2`), heißt das Ziel „kein Override“ (`None`). Sonst gilt `T`, wenn es mindestens `setpoint_deadband_w` vom letzten eigenen Wert abweicht.
     - „kein Override“ oder keiner: `T` erst ab `W' ≥ setpoint_deadband_w`
   - **„Kein Override“ geht nur hinaus,** wenn der letzte eigene Wert eine Zahl ist oder, ohne eigenen Wert, wenn der Cerbo einen Override meldet. Sonst gäbe es nach jedem Start und jedem `forget` eine Entscheidung „aufheben“ für nichts.
   - **Unbekanntes Ziel:** Es geht kein Befehl hinaus, auch kein Auffrischen. Ein Override verfällt so nach 300 s (Design 5.2).
   - **Gründe:** `battery_support`, wenn der erlaubte Akku-Anteil zusammen über 0 liegt, sonst `battery_comp`; dahinter `peak_guard`, wenn das Budget des Wächters `W'` gesenkt hat.
7. **Auffrischen (Design 5.5, 8).**
   - **Fällig** ist es, wenn der letzte eigene Wert eine Zahl ist, kein neuer Wert ansteht und seit dem letzten Urteil mit Wert über einen Override-Befehl (`verdict.sendable`) `override_refresh_s` vergangen sind, auf der Uhr der Safety-Schicht. Der Befehl trägt den letzten eigenen Wert (`LastValues.requested`), `refresh=True` und die Gründe dieses Zyklus.
   - **Die Safety-Schicht** überspringt für `refresh` bei `grid_setpoint` Schritt 9 ganz, also auch `rate.deadband`, nicht nur `rate.unchanged` (Abweichung vom Wortlaut von Design 8): Regel 2 und Regel 4 ändern den Wert eines Overrides von Zyklus zu Zyklus leicht. Ein Auffrischen, das der Cerbo um weniger als 50 W anders hält, wäre sonst `UNCHANGED`, und der Override verfiele.
   - Mit `refresh` ist ein Befehl nie `urgent_ok`; der Befund ist `urgent.ignored`.
   - **Entscheidungslog:** Der Ausführer schreibt für einen Befehl mit `refresh` keine Entscheidung bei `OK` und bei `DRY_RUN`, sonst wie bisher.
   - **Erkennung fremder Änderungen:** Die Erwartung eines Overrides (eine Zahl) verfällt `OVERRIDE_TIMEOUT_S` (300 s) nach ihrer Bestätigung, wie der Override selbst. Ein Auffrischen bestätigt sie neu. So ist der gewollte Rückfall keine fremde Änderung (Design 5.6): wenn die Regelung ruht, gesperrt ist, das Ziel nicht kennt oder eine Ausnahme wirft.
8. **Sperren (Design 5.6).** Gesperrt ist Victron, wenn `SafetyContext.locks` für `victron` Gründe nennt; nicht steuerbar nach seinen Fähigkeiten. In beiden Fällen sendet der Akku-Sollwert nichts und vergisst seine Werte; Fehlbetrag, Unterstützung und Ziel rechnet er weiter für Health. Ein offener Schreibvorgang oder eine Übergabe von Victron (`ChargeInputs.blocked`) hält nur den Befehl dieses Zyklus zurück.
9. **15-min-Wächter: Messung (Design 6.1).**
   - **Quelle:** `SlotAccumulator.current()` (neu) liefert den laufenden Slot. Die Energie bisher ist die Summe seiner Flüsse `grid>*`, die Leistung des Zyklus die Summe der Flüsse `grid>*` aus `allocate`.
   - **Boost:** Je Wallbox kommt ihr Netzanteil des Zyklus (`grid>wb:<w>`) mit. Der Wächter integriert ihn für die Autos in Boost selbst und zieht ihn ab, nach dem Halteprinzip der Abrechnung und über dieselben Lücken (`HOLD_MAX_S` = 5 s in `shared/timeutil.py`, auch Standard von `SlotAccumulator`); sonst passten `E_bisher` und die abgezogene Boost-Energie nach einem Stocken nicht zusammen.
   - **Netzanteil bei Boost (Gesamtreview):** Den Netzanteil je Wallbox rechnet `RegulationLink` mit einer zweiten Zuordnung (`allocate`, über `derived_flows` in `core/accounting/flows.py`) derselben abgeleiteten Größen, mit derselben Regel für unbekannte Werte. Darin bekommen die Wallboxen der Autos in Boost PV und Akku zuerst, dann die übrigen in der Reihenfolge von `wallbox_order`. So lädt der Akku-Sollwert: Boost darf aus dem Akku laden, der Fehlbetrag der anderen Autos geht ans Netz. Die Verteilung der Abrechnung (nur `wallbox_order`) buchte den Netzbezug eines anderen Autos sonst auf die Wallbox in Boost, und der Wächter zöge ihn ab. Die Wallboxen in Boost nennt `Regulation.boost_wallboxes` aus dem letzten Zyklus (einen Zyklus alt), über `_call` gefangen wie jeder Aufruf des Kerns. Ohne Wallbox in Boost bleibt es der Anteil der Abrechnung; `grid_wh` und `grid_w` ändern sich nicht.
   - **Uhr:** Der Wächter rechnet ganz auf der Wanduhr (`wall_now`), wie die Viertelstunden der Abrechnung.
   - **Lückenhaft** ist eine Viertelstunde bis zu ihrem Ende, wenn eins davon eintritt:
     - Zwischen zwei Zyklen mit bekannter Leistung liegen mehr als 10 s (`GAP_S`).
     - Der erste Zyklus mit bekannter Leistung kommt später als 10 s nach ihrem Beginn, etwa nach dem Start des core.
     - Die Regelung ruhte (`idle`).
     - Die Wanduhr springt innerhalb der Viertelstunde zurück.
10. **Vergleich (Design 6.2).**
    - **Verlangte Senkung** = `Bedarf − P_zul`, mit `W` vor dem Wächter und dem Mittel der letzten 30 s (ohne Boost). Der Bedarf ist mit Budget für den Akku `max(Mittel, min(Basis + W + S, C) − S)`, ohne Budget `max(Mittel, Basis + W)`, solange `P_zul` unter `C` liegt, sonst `max(Mittel, min(Basis + W, C))` (nächster Punkt); sind `W` oder die Basis unbekannt, ist er das Mittel.
    - **Feste Grenze `C` und gemerkte Senkung `S` (Gesamtreview, Nachrunde):** Der Kern gibt dem Wächter `W` ungekappt (`regulation.peak_wanted_w`) und beim Bau die feste Grenze `C` von Regel 2 ohne Budget (`HardLimits.grid_setpoint_max(None)`: `max_grid_charge_setpoint_w`, Absicherung, `max_import_kw`). Solange Victron gesperrt (`locks`) oder nicht steuerbar ist, gibt der Kern `W` nicht (`None`), und es zählt nur das Mittel.
      - **Mit Budget** (der SOC ist bekannt und liegt über `reserve_soc_pct + reserve_guard_pct`, wie für `PeakLimits.budget_w`, Präzisierung 12) zählt der Netzbezug der Autos höchstens bis `C`: Mehr setzt kein Override durch, und den Rest liefert der Akku wie ohne Ziel (Design, Entscheidung 3). `S` ist die Summe der gemerkten Senkungen (`cut_w`) der Grenzen, die nach dem Aufräumen dieses Schritts bleiben (die Autos sind noch da, im selben Modus); `Basis + W + S` ist so der Netzbezug ohne die Senkungen. Insgesamt senkt der Wächter nur um `min(Basis + W ohne Senkung, C) − P_zul`, und das einmal.
      - **Ohne Budget** hält Regel 2 den Override bei `C`, und der Netzbezug folgt ihm bis dorthin; er liegt bei höchstens `min(Basis + W, C)`. Solange `P_zul` unter `C` liegt, zählt `Basis + W` ganz, denn unter `P_zul` bringt den Netzbezug dann nur die ganze Senkung. Liegt `P_zul` bei `C` oder darüber, zählt `min(Basis + W, C)`, und es braucht keine Senkung: Was die Autos über `C` wollen, liefert der Akku wie ohne Ziel (Entscheidung 3), und ein Akku an seiner Reserve zeigt sich im Mittel (Fix-Runde nach dem Review der Nachrunde; vorher zählte `Basis + W` ohne Budget immer ganz, und bei einem Ziel von 10 kW drosselte der Wächter zwei Autos in Sofort um 8,7 kW, obwohl der Netzbezug bei 8 kW unter `P_zul` blieb).
      - Derselbe Bedarf gilt für das Aufheben (Präzisierung 11) und für `reduction_kw` in Health (mindestens 0); die Start-Sperre rechnet weiter nur mit dem Mittel (Präzisierung 13).
      - Bis zur Nachrunde kappte der Kern `W` bei `C − Basis`. Lag `P_zul` unter `C`, blieb die Senkung `C − P_zul`, solange die Autos mehr als `C` wollten: Der Wächter senkte in Stufen von `C − P_zul` je Wartezeit, mit Budget gegen Entscheidung 3, ohne Budget so langsam, dass der Netzbezug minutenlang über `P_zul` lag.
    - **Warum `Basis + W`:** Mit Victron live begrenzt Regel 2 den Override auf `P_zul`. ESS hielte den Netzbezug so immer unter `P_zul`, ganz aus dem Akku, und die Wallboxen würden nie gedrosselt (gegen die Reihenfolge von Design 6.3). Mit `W` drosselt der Wächter, sobald die Autos mehr Netz wollen, als das Budget trägt; das Mittel fängt den Rest, etwa einen Akku am Min-SOC.
    - **`P_zul`** nach Design 6.2 mit `t_rest = max(1 s, Ende − wall_now)`, also immer endlich.
11. **Drosselung (Design 6.3, Punkt 1).**
    - **Was sie senkt:** den Netzanteil eines Autos, seinen Beitrag zu `W` (`max(0, Fehlbetrag − erlaubt)`). Ein Auto ohne Netzanteil wird nicht gedrosselt, auch nicht in PV-Überschuss.
    - **Grenze:** je Auto eine Leistungsgrenze in W, frühestens im nächsten Zyklus wirksam. Die Laderegelung rechnet sie in Ampere um (`W / (U · Phasen des Ladepunkts)`) und nimmt sie wie die Grenze des Phasenwächters: Liegt sie unter dem freien Strom, gilt sie, und der Grund ist `peak_guard` statt des Grunds des Wächters. Ist der freie Strom unbekannt, bleibt er unbekannt.
    - **Boden:** PV-Überschuss 0 W, Min+PV und Sofort die Mindestleistung des Ladepunkts (`min_a × U × Phasen`). Boost und Aus drosselt der Wächter nie; die Laderegelung übergeht für ein Auto in Boost jede Grenze.
    - **Neue Drosselung** nur bei Senkung > 0 und frühestens 30 s (`SETTLE_S`) nach der letzten Änderung:
      - Zu senken ist `Senkung − ausstehend`, wobei `ausstehend` = Σ `max(0, Leistung − Grenze)` der gedrosselten Autos.
      - Reihenfolge: PV-Überschuss, dann Min+PV, dann Sofort; innerhalb einer Stufe zuerst das Auto mit der niedrigsten Priorität.
      - Je Auto: `Ausgang` ist das kleinere von Grenze und Leistung, ohne bekannte Leistung die Grenze und ohne Grenze die Leistung (Nachrunde; vorher immer die Grenze, und eine Grenze weit über der Leistung, etwa beim Tesla in Min+PV nach dem Wechsel auf 5 A, schluckte Senkungen ohne Wirkung). Es senkt um `min(Rest, Netzanteil, Ausgang − Boden)`, und die neue Grenze ist `Ausgang` minus diese Senkung. Gemerkt wird die gesamte Senkung des Autos. `ausstehend` bleibt `max(0, Leistung − Grenze)`.
    - **Aufheben,** ebenfalls frühestens 30 s nach der letzten Änderung und je Zyklus höchstens eine:
      - in umgekehrter Reihenfolge (Sofort vor Min+PV vor PV-Überschuss, innerhalb einer Stufe die höchste Priorität zuerst)
      - die erste Grenze, für die `P_zul − Bedarf ≥ ihre Senkung + 1000 W`, mit dem Bedarf aus Präzisierung 10
    - Mit dem Ende der Viertelstunde fallen alle Grenzen weg.
    - **Wer gedrosselt wird:** nur ein Auto an einem Ladepunkt, den die Laderegelung regelt; ein ungeregeltes Auto in Boost zählt nur für den Boost-Anteil. Verlässt ein Auto die Liste, wechselt es den Modus oder geht es in Boost oder Aus, fällt seine Grenze still weg (ohne neue Wartezeit).
12. **Akku-Anforderung und Budget (Design 5.3 Punkt 2, 6.2, 6.3 Punkt 2).**
    - **Budget:** `PeakLimits.budget_w` ist `P_zul`, solange der Wächter aktiv oder überschritten ist und der SOC über `battery.reserve_soc_pct + control.reserve_guard_pct` liegt. Sonst ist es `None`, auch bei unbekanntem SOC.
    - **Wirkung:** Der Akku-Sollwert setzt `W' = max(0, min(W, floor10(budget_w) − Basis))` mit `floor10(x) = 10 · floor(x / 10)`; so liegt `T` nie über dem Budget, und Regel 2 muss nicht auf einen Wert außerhalb des Zehner-Rasters klemmen. Dasselbe Budget geht als `grid_budget_w` in den `SafetyContext`.
    - **Folge:** Der Akku überbrückt genau, was die Wallboxen nicht oder noch nicht bringen, und die Schwelle der Unterstützung gilt dafür nicht. Unter der Schwelle senkt weder der Akku-Sollwert noch Regel 2 den Override für den Wächter; dann bleiben die Wallboxen, sonst gilt die Überschreitung.
    - **Abweichung von Design 4.3:** `grid_budget_w` ist nicht immer `P_zul`, sondern nur über der Schwelle; sonst entlüde Regel 2 den Akku bis zum Min-SOC.
13. **Letzte 120 s (Design 6.4).** Ab 120 s vor dem Ende der Viertelstunde startet kein Auto, dessen Mindestleistung über `P_zul − Mittel` liegt (`PeakLimits.no_start`, ohne Boost). Die Laderegelung behandelt das wie eine Grenze, die den Mindeststrom nicht trägt; ein Auto, das lädt, betrifft es nicht.
14. **Überschritten (Design 6.3, Punkt 3).**
    - **Zustand:** Liegt die Hochrechnung `(E_bisher + Mittel · t_rest) / 0,25 h` 60 s lang ohne Unterbrechung über dem Ziel (`EXCEEDED_AFTER_S`), ist die Viertelstunde überschritten, bis zu ihrem Ende.
    - **Alarm:** `peak.exceeded` (Warnung) nennt die Hochrechnung in diesem Moment auf 0,1 kW. Text und `since` bleiben bis zum Ende der Viertelstunde fest.
15. **Ausnahme (Design 4.5).**
    - **Gefangen wird in `RegulationLink`:** Wirft ein Aufruf des Kerns (`step`, `note_verdict`, `note_result`, `forget`, `health`, `alarms`, `vehicle_wallbox`, `grid_budget_w`), gilt der Zyklus als gescheitert (`failed`, ab `start_cycle` am Anfang des Zyklus):
      - Die Laufzeit führt danach keinen Befehl der Regelung mehr aus. Der Kern bekommt `idle`; wirft auch das, wird er neu gebaut.
      - Health und Alarme des Kerns bleiben auf dem Stand des letzten Zyklus ohne Ausnahme.
    - **Was weiterläuft:** Die Befehle des Keepers laufen trotzdem.
    - **Heartbeat:** Er bleibt in einem gescheiterten Zyklus aus (`allowed` falsch).
    - **Alarm `control.exception`:** Fehler, solange der letzte Zyklus mit Regelung scheiterte. `since` ist die Wanduhr des ersten gescheiterten Zyklus der Folge.
    - **Log:** Es nennt die erste Ausnahme einer Folge mit Traceback, jede weitere als eine Zeile.
16. **Health (Design 9).** `HealthReport` bekommt `battery` und `peak` (Felder in den Tasks 8 und 11). Werte, die fehlen, sind `null`.
17. **Simulator (Design 10).**
    - **Modell:**
      - ESS regelt die Summe der Phasen auf den Override, sonst auf `AcPowerSetPoint`.
      - Der Override verfällt 300 s nach dem letzten Schreiben einer Zahl.
      - Mit DESS an (`dess.mode` 1) verwirft das Modell den Override und nimmt keinen an.
    - **Freigaben:** `Scenario.victron_live` (Standard falsch) schaltet Victron zusätzlich live (`sim_config`: `victron.live_allowed`); `live` bleibt für EVCS und Tesla.
    - **Die zehn bestehenden Szenarien** bleiben, wie sie sind. In ihnen entscheidet der Akku-Sollwert im Dry-Run mit; ihre Prüfungen zählen Entscheidungen nur je Aktor.
    - **Eigene Werte:** Ab Task 8 hüllt der Lauf `note_verdict`, `forget` und `idle` von `rt.regulation` ein statt von `rt.charger`. So sieht die Kennzahl „Wiederholte Dry-Run-Entscheidungen“ auch die eigenen Werte des Akku-Sollwerts. Ohne sie zählte etwa im Szenario `dry-run` ein Override, der nach einem `UNCHANGED` für „kein Override“ wiederkommt, als Wiederholung.

## Global Constraints

- **Gilt weiter:** alle Global Constraints aus 1a, 1b, 2a, 2b und 2c-1.
- **Schreibwege:** unverändert gegenüber 2b, kein neuer Pfad.
  - Die Regelung schreibt an Victron nur `grid_setpoint` (`hub4/0/Overrides/Setpoint`), als Zahl in W oder `None`.
  - Entladegrenze und Min-SOC schreibt weiter nur der Keeper.
- **Jeder Befehl** der Regelung geht durch `CommandExecutor.execute`, also genau einmal durch `SafetyLayer.authorize`. Kein Befehl des Akku-Sollwerts ist dringend.
- **Keine Regelung** in START, OBSERVE und SAFE und nie bei ausgeschaltetem Regelbetrieb, auch nicht während der Übergabe danach (Design 5.6).
- **Reinheit:** Die neuen Module in `core/control` importieren nichts aus `core.actuators`, `core.adapters`, `core.api`, `core.runtime`, `core.telemetry`, `core.accounting` und `core.checks` (Importvertrag). `core/regulation_link.py` liegt außerhalb und darf das.
- **Zeit:**
  - Lade- und Akku-Regelung laufen auf der Uhr der Safety-Schicht (`ChargeInputs.now`), auch Überbrückung und Auffrischen.
  - Der 15-min-Wächter läuft auf der Wanduhr (`ChargeInputs.wall_now`), wie die Viertelstunden der Abrechnung.
- **Werte:** Overrides in W als `float`, Vielfache von 10. `grid_budget_w` ist nie NaN oder ±∞.
- **Abdeckung:** ≥ 90 % für `core/safety`, `core/control` und `core/actuators`, ≥ 75 % insgesamt.
- **Texte:** exakt wie in „Texte“; Tests prüfen den Wortlaut. Zahlen über `de()`.
- **Öffentliches Repository:** Simulator, Tests und Berichte enthalten keine Daten der Anlage; keine Verweise auf Ledger-Einträge (R…).
- **Umbau (Tasks 1 bis 3):** kein geändertes Verhalten, keine geänderte Testdatei (Präzisierung 2).

## Review Focus

1. **Der Override verfällt, obwohl der core ihn will,** etwa weil ein Auffrischen zurückgestellt, blockiert oder gescheitert ist:
   - Erwartet: Das Auffrischen folgt im nächsten möglichen Zyklus. Nach einem gescheiterten Schreiben sendet die Regelung den Wert neu, als normalen Befehl.
   - Verfällt er gewollt, gibt es keinen Alarm „fremde Änderung“.
   - Tests: Task 7 `test_blocked_refresh_follows_in_the_next_cycle` und `test_failed_refresh_sends_the_value_again`, Task 5 `test_override_expectation_lapses_with_the_override`.
2. **Victron gesperrt (DESS) oder nicht steuerbar, auch im Dry-Run:**
   - Erwartet: kein Befehl, keine Flut von Ablehnungen im Log, Health `battery.state` = `locked`.
   - Nach der Sperre beginnt der Akku-Sollwert ohne eigenen Wert.
   - Tests: Task 7 `test_locked_victron_gets_nothing_and_forgets`, Task 8 `test_dess_lock_sends_nothing`.
3. **PV, Hauslast oder die Leistung eines ladenden Autos fehlen:**
   - Erwartet: kein Befehl und kein Auffrischen, Health `unknown`. Kommen die Werte zurück, folgt der Override ohne Sprung über „kein Override“.
   - Tests: Task 6 `test_unknown_power_of_a_charging_car_makes_the_need_unknown`, Task 7 `test_unknown_need_keeps_the_override_without_refresh`.
4. **Start des core oder Pause mitten in einer Viertelstunde:**
   - Erwartet: Die Viertelstunde ist lückenhaft; der Wächter drosselt nicht nach einer halben Messung, und `grid_budget_w` ist `None` bis zur nächsten Viertelstunde.
   - Tests: Task 9 `test_start_mid_quarter_is_a_gap` und `test_idle_makes_the_quarter_a_gap`.
5. **Die Regelung wirft in jedem Zyklus:**
   - Erwartet: Keeper, Stream, Alarme und Entscheidungslog laufen weiter, der Heartbeat bleibt aus, Alarm `control.exception`. Hört die Ausnahme auf, regelt der nächste Zyklus wieder und der Alarm geht.
   - Tests: Task 3 `test_exception_in_the_regulation_keeps_the_keeper_running` und `test_regulation_recovers_after_the_exception`.

## Texte

**Gründe** (`_REASON_TEXT` in `shared/decisions.py`, nach den Gründen von 2c-1):

| Code | Text |
|---|---|
| `battery_comp` | Akku nicht ins Auto |
| `battery_support` | Akku-Unterstützung |
| `peak_guard` | 15-min-Wächter |

Beispiel: `Victron (ESS) (Akku nicht ins Auto): Netz-Sollwert 2340 W – Dry-Run, nicht gesendet.`

**Alarme:**

| ID | Stufe | Text |
|---|---|---|
| `control.exception` | Fehler | Regelung ausgesetzt: Ausnahme {Typ} im Regelzyklus. |
| `peak.exceeded` | Warnung | 15-min-Ziel überschritten: {x} kW in der Viertelstunde bis {HH:MM}. |

- {Typ} ist `type(exc).__name__`.
- {x} ist `de(round(Hochrechnung in kW, 1))`, {HH:MM} das Ende der Viertelstunde in Wiener Zeit.

**Health-Werte:**
- `battery.state`: `ok`, `unknown`, `locked`, `idle`
- `battery.support`: `on`, `soc`, `time`, `mode`
- `peak.state`: `off`, `gap`, `active`, `exceeded`

## Dateistruktur

```
src/bindaems/shared/config.py                   (geändert) ControlConfig: Akku-Sollwert, 15-min-Wächter
src/bindaems/shared/control.py                  (geändert) GuestIntent, Intent.guest, Command.refresh
src/bindaems/shared/decisions.py                (geändert) Gründe battery_comp, battery_support, peak_guard
src/bindaems/core/control/last_values.py        LastValues, ControlCommand (aus charging.py)
src/bindaems/core/control/timing.py             held_since (aus charge_modes.py und phase_guard.py)
src/bindaems/core/control/charge_points.py      Ladepunkte und Verteilung (aus charging.py)
src/bindaems/core/control/charging.py           (geändert) ChargeController: Grenzen des Wächters, cars()
src/bindaems/core/control/charging_tesla.py     (geändert) Tesla-Teil (aus charging.py)
src/bindaems/core/control/charge_modes.py, charge_inputs.py, phase_guard.py, context.py   (geändert)
src/bindaems/core/control/regulation.py         Regulation, RegulationInputs
src/bindaems/core/control/battery.py            Fehlbetrag, Unterstützung, BatteryController
src/bindaems/core/control/peak_guard.py         QuarterObs, PeakCar, PeakLimits, PeakGuard
src/bindaems/core/control/foreign.py, executor.py   (geändert) Verfall der Erwartung, Auffrischen im Log
src/bindaems/core/safety/layer.py               (geändert) Command.refresh in Schritt 2 und 9
src/bindaems/core/actuators/victron.py          (geändert) OVERRIDE_TIMEOUT_S
src/bindaems/core/accounting/flows.py           (geändert) SlotProgress, SlotAccumulator.current
src/bindaems/core/regulation_link.py            RegulationLink: Eingaben, Ausnahme, Health, Alarme
src/bindaems/core/runtime.py, core/api/app.py   (geändert)
src/bindaems/tools/sim/model.py, runner.py, metrics.py, scenarios.py   (geändert)
tests/unit/core/control/{test_last_values,test_timing,test_charge_points,test_regulation,test_battery,
  test_battery_controller,test_peak_guard,test_peak_throttle}.py, tests/unit/core/test_regulation_link.py
tests/property/{test_battery,test_peak_guard}.py
tests/integration/{test_runtime_regulation,test_runtime_battery}.py, tests/integration/plant.py (geändert)
docs: Spec, Design (Stand), Betriebshandbuch, docs/uebergabe.md, deploy/config.example.yaml
```

Neue Pakete gibt es nicht; Testverzeichnisse bekommen keine `__init__.py`.

---

### Task 1: Umbau I – gemeinsame Merker und doppelte Hilfen

**Files:**
- Create: `src/bindaems/core/control/last_values.py`, `src/bindaems/core/control/timing.py`
- Modify: `src/bindaems/core/control/charging.py`, `charge_modes.py`, `phase_guard.py`, `charge_inputs.py`, `context.py`
- Test: `tests/unit/core/control/test_last_values.py`, `tests/unit/core/control/test_timing.py`

**Interfaces:**
- Consumes: `Command`, `CommandKind`, `CommandValue` (`shared/control.py`), `Verdict`, `Outcome` (`core/safety/types.py`), `WriteResult` (`executor.py`), `DecisionResult`
- Produces:
  - **`last_values.py`** (aus `charging.py`: `_LastValues`, `_Sent`, `_FAILED`, `NOT_TAKEN_RETRY`, `ChargeCommand`, `ChargeController._command`):
    - `Key = tuple[str, CommandKind]`, `NOT_TAKEN_RETRY: Final = "not_taken_retry"`
    - `@dataclass(frozen=True) class ControlCommand`: `command: Command`, `inputs: Mapping[str, float | None]`; ein Befehl der Regelung und seine Eingaben für das Entscheidungslog
    - `class LastValues` mit den Methoden von `_LastValues` (`changed`, `requested`, `retry`, `judged`, `finished`, `forget`), dazu:
      - `holds(key: Key) -> bool`: ob es einen letzten eigenen Wert gibt. Nötig, weil `requested` für „keiner“ und für den Wert `None` (kein Override) dasselbe liefert.
      - `command(actuator: str, kind: CommandKind, value: CommandValue, reasons: tuple[str, ...], *, urgent: bool = False, wake: bool = False) -> Command`: der Körper von `ChargeController._command`
  - **`timing.py`:** `held_since(since: datetime | None, holds: bool, now: datetime) -> datetime | None`, der Körper der beiden `_since`; `charge_modes.py` und `phase_guard.py` nutzen es.
  - **`charge_modes.py`:** `PV_SURPLUS: Final = "pv_surplus"` ersetzt `_PV_SURPLUS`; `charging.py` importiert es und definiert es nicht mehr selbst.
  - **`charge_inputs.py`:** `ok_number(snap: Snapshot, signal: str) -> float | None` ersetzt `_number`; `context.py` nutzt es statt seines eigenen `_number`.
  - **`charging.py`:** `ChargeCommand = ControlCommand` bleibt als Name; `ChargeController` hält `LastValues` und ruft `self._last.command(...)`.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_last_values.py
CURRENT = ("wallbox.evcs", CommandKind.CURRENT)
SETPOINT = ("victron", CommandKind.GRID_SETPOINT)


def judge(last: LastValues, command: Command, outcome: Outcome = Outcome.ACCEPTED) -> bool:
    return last.judged(command, Verdict(command, outcome, command.value, (), True), not_sent=False)


def test_a_verdict_with_value_sets_the_last_value() -> None:
    last, command = LastValues(), Command("wallbox.evcs", CommandKind.CURRENT, 10.0)
    assert (last.holds(CURRENT), last.changed(CURRENT, 10.0)) == (False, True)
    assert judge(last, command)
    assert (last.holds(CURRENT), last.requested(CURRENT), last.changed(CURRENT, 10.0)) == (True, 10.0, False)


def test_deferred_and_rejected_set_nothing_unchanged_does() -> None:
    # DEFERRED und REJECTED: judged → False, holds False; UNCHANGED: judged → True, holds True


def test_not_taken_clears_the_value_and_marks_the_retry() -> None:
    last, command = LastValues(), Command("vehicle.tesla", CommandKind.CURRENT, 5.0)
    judge(last, command)
    last.finished(WriteResult("vehicle.tesla", CommandKind.CURRENT, 5.0, DecisionResult.ERROR, "", not_taken=True))
    assert not last.holds(("vehicle.tesla", CommandKind.CURRENT))
    retried = last.command("vehicle.tesla", CommandKind.CURRENT, 5.0, ("mode_pv",), urgent=True)
    assert retried == Command("vehicle.tesla", CommandKind.CURRENT, 5.0, reasons=("mode_pv", "not_taken_retry"))


def test_a_result_for_another_value_changes_nothing() -> None:
    # judged 10.0, finished(… 8.0, ERROR …) → holds weiter, requested 10.0


def test_holds_tells_no_override_from_no_value() -> None:
    last = LastValues()
    judge(last, Command("victron", CommandKind.GRID_SETPOINT, None))
    assert (last.holds(SETPOINT), last.requested(SETPOINT)) == (True, None)
    last.forget({"victron"})
    assert (last.holds(SETPOINT), last.requested(SETPOINT)) == (False, None)


# tests/unit/core/control/test_timing.py
def test_held_since_keeps_the_start_while_it_holds() -> None:
    assert held_since(None, True, T0) == T0
    assert held_since(T0, True, T0 + timedelta(seconds=5)) == T0
    assert held_since(T0, False, T0 + timedelta(seconds=6)) is None
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_last_values.py tests/unit/core/control/test_timing.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces: verschieben, nicht neu schreiben; Docstrings ziehen mit um.

- [ ] **Step 4: Alles laufen lassen**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports`
Expected: PASS, ohne Änderung an einer bestehenden Testdatei (`git diff --stat tests` zeigt nur die neuen Dateien).

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control tests/unit/core/control/test_last_values.py tests/unit/core/control/test_timing.py
git commit -m "refactor(core): Merker der Regelung als eigenes Modul, doppelte Hilfen zusammengeführt"
```

---

### Task 2: Umbau II – `charging.py` aufteilen

**Files:**
- Create: `src/bindaems/core/control/charge_points.py`
- Modify: `src/bindaems/core/control/charging.py`, `src/bindaems/core/control/charging_tesla.py`
- Test: `tests/unit/core/control/test_charge_points.py`

**Interfaces:**
- Consumes: `LastValues` (Task 1), `TeslaPolicy`, `ChargeInputs`, `VehicleState`, `HardLimits`
- Produces (verschoben, Verhalten gleich):

  | aus `charging.py` | nach |
  |---|---|
  | `_Point` | `charge_points.ChargePoint` (Felder unverändert) |
  | `_points`, `_point` | `charge_points.charge_points(cfg: Config, limits: HardLimits, order: Sequence[str], vehicles: Mapping[str, VehicleState], inputs: ChargeInputs) -> dict[str, ChargePoint]` |
  | `_reserved` | `charge_points.reserved_a(cfg: Config, points: Sequence[ChargePoint], wallboxes: Mapping[str, WallboxObs]) -> dict[Phase, float]` |
  | `_cars_w`, `_wanted`, `_holding`, `_enough`, `_can_charge` | `charge_points.cars_w`, `wanted_a`, `holding_a`, `enough`, `can_charge` (Signaturen wie bisher, ohne Unterstrich) |
  | `_tessie_current` | `charging_tesla.tessie_current` |
  | `_maximum` | `charging_tesla.maximum_command(cfg: Config, last: LastValues, key: str, mode: Effective, inputs: ChargeInputs) -> list[Command]` |
  | `_wake` | `charging_tesla.wake_command(last: LastValues, policy: TeslaPolicy, now: datetime, wall_now: datetime, actuator: str, reasons: tuple[str, ...], blocked: frozenset[Key]) -> list[Command]`; die Gründe baut weiter `ChargeController` |

  Die Absätze des Modul-Docstrings ziehen mit: „Nur Änderungen“ nach `last_values.py`, „Tesla, Zuordnung und Wiederaufnahme“ nach `charging_tesla.py`. `charging.py` behält `ChargeController`, `_View`, `_Cycle`, `_Situation`, `_mode`, `_why` und `_facts` und hat danach rund 700 Zeilen.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_charge_points.py
from tests.unit.core.control.charge_helpers import evcs, inputs, twc

def state(wallbox: str | None) -> VehicleState:
    return VehicleState(wallbox, AssignmentKind.DEFAULT, True, False, False, False, None)


def test_evcs_point_uses_the_vehicle_phases_and_range(cfg) -> None:
    vehicles = {"tesla": state("twc"), "egolf": state("evcs")}
    points = charge_points(cfg, HardLimits.from_config(cfg), ["tesla", "egolf"], vehicles, inputs(cfg))
    egolf = points["egolf"]
    assert (egolf.actuator, egolf.evcs, egolf.phases, egolf.min_a, egolf.max_a) == (
        "wallbox.evcs", True, ("L1", "L2"), 6, 16,
    )
    assert egolf.regulated and not points["tesla"].regulated  # Tesla weder steuerbar noch weckbar


def test_one_wallbox_is_one_point_for_the_first_by_priority(cfg) -> None:
    vehicles = {"tesla": state("evcs"), "egolf": state("evcs")}
    points = charge_points(cfg, HardLimits.from_config(cfg), ["egolf", "tesla"], vehicles, inputs(cfg))
    assert list(points) == ["egolf"]


def test_unreachable_evcs_reserves_its_safe_current(cfg) -> None:
    reserved = reserved_a(cfg, [], {"evcs": evcs(reachable=False), "twc": twc()})
    assert reserved == {"L1": 6.0, "L2": 6.0, "L3": 6.0}
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_charge_points.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen:** verschieben wie in der Tabelle; Methoden, die Zustand des Controllers lasen, bekommen ihn als Argument.

- [ ] **Step 4: Alles laufen lassen**

Run: wie Task 1, Step 4, dazu `wc -l src/bindaems/core/control/charging.py`
Expected: PASS, keine bestehende Testdatei geändert, rund 700 Zeilen.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control tests/unit/core/control/test_charge_points.py
git commit -m "refactor(core): Laderegelung in Ladepunkte, Tesla-Teil und Controller aufgeteilt"
```

---

### Task 3: Regelungs-Kern, Verdrahtung neben der Laufzeit, Ausnahme in der Regelung

**Files:**
- Create: `src/bindaems/core/control/regulation.py`, `src/bindaems/core/regulation_link.py`
- Modify: `src/bindaems/core/runtime.py`
- Test: `tests/unit/core/control/test_regulation.py`, `tests/unit/core/test_regulation_link.py`, `tests/integration/test_runtime_regulation.py`

**Interfaces:**
- Consumes: `ChargeController`, `ChargeInputs`, `observe`, `ControlCommand` (Task 1), `WakeStart`, `Capabilities`
- Produces:
  - **`core/control/regulation.py`:**
    - `@dataclass(frozen=True) class RegulationInputs`: `charge: ChargeInputs`. Die Tasks 8 und 11 ergänzen Felder.
    - `class Regulation`, die einzige Schnittstelle der Laufzeit zur Regelung (Design 4.1):
      - `__init__(self, cfg: Config, limits: HardLimits, *, started: datetime) -> None`, mit dem Attribut `charger: ChargeController`
      - `step(self, inputs: RegulationInputs) -> list[ControlCommand]`
      - `note_verdict(self, command: Command, verdict: Verdict | None, *, not_sent: bool = False) -> None`
      - `note_result(self, result: WriteResult) -> None`, `forget(self, actuators: Collection[str]) -> None`, `idle(self, now: datetime) -> None`
      - `vehicle_wallbox(self) -> dict[str, str | None]`
      - `health(self) -> dict[str, Any]`, vorerst `{"vehicles": charger.health()}`
      - `alarms(self, wall_now: datetime) -> list[Alarm]`, vorerst `[]`
      - `grid_budget_w(self) -> float | None`, vorerst `None`

      In diesem Task gibt jede Methode an `charger` weiter.
  - **`core/regulation_link.py`:**
    - `CONTROL_EXCEPTION: Final = "control.exception"`
    - `class RegulationLink`:
      - `__init__(self, cfg: Config, limits: HardLimits, *, started: datetime) -> None`; Attribut `regulation: Regulation`, Eigenschaft `charger` (→ `regulation.charger`, für den Simulator bis Task 8)
      - `step(self, *, snap: Snapshot, derived: Derived, now: datetime, snow: datetime, present: Mapping[Key, CommandValue], capabilities: Mapping[str, Capabilities], actuators: Mapping[str, Actuator], intent: Intent, accepted_at: datetime | None, blocked: frozenset[Key], connected: Callable[[str], bool | None]) -> list[ControlCommand]`: baut `ChargeInputs` wie heute `CoreRuntime._charge_inputs` (der Code zieht hierher) und ruft den Kern
      - `idle(self, snow: datetime) -> None`
      - `note_verdict`, `note_result`, `forget`: wie beim Kern
      - `vehicle_wallbox(self) -> dict[str, str | None]`, ohne Ergebnis `{}`; `grid_budget_w(self) -> float | None`, ohne Ergebnis `None`
      - `start_cycle(self) -> None`: am Anfang jedes Zyklus, vor dem Rücklesen; danach ist `failed` falsch
      - `failed: bool` (Eigenschaft): In diesem Zyklus warf ein Aufruf des Kerns.
      - `finish(self, wall_now: datetime) -> None`: legt am Ende des Regelteils Health und Alarme des Kerns ab (`health`, `alarms`). Der Alarm `control.exception` gilt ab dem ersten Zyklus mit `failed` bis zu einem Zyklus, in dem `step` ohne Ausnahme lief; Zyklen ohne Regelung und ohne Ausnahme lassen ihn stehen (Präzisierung 15). `since` ist der erste gescheiterte Zyklus.
      - `health(self) -> dict[str, Any]`: der abgelegte Stand, vor dem ersten `finish` der des frisch gebauten Kerns
      - `alarms(self) -> list[Alarm]`: die abgelegten Alarme des Kerns, dazu `control.exception` nach `finish`

      Jeder Aufruf des Kerns läuft in `try/except Exception` (Präzisierung 15). Nach einer Ausnahme ruft der Link `self.idle(snow)`, nicht `regulation.idle`: So sieht ein Einhüllen von außen (Simulator) auch diese Pause. Wirft `idle`, baut er den Kern neu.
  - **`runtime.py`:**
    - `self.regulation = RegulationLink(cfg, self.limits, started=self._clock.now())` statt `self.charger`; `charger` bleibt als Eigenschaft (→ `self.regulation.charger`).
    - `_charge_commands` und `_charge_inputs` entfallen; `_control` ruft `self.regulation.step(...)` bzw. `self.regulation.idle(snow)` in denselben Zuständen wie heute.
    - `_cycle` ruft zuerst `self.regulation.start_cycle()`.
    - Danach wie heute: Keeper-Befehle, `SafetyContext`, Ausführen. Vor jedem Befehl der Regelung bricht `_control` ab, wenn `self.regulation.failed` gilt. Am Ende steht `self.regulation.finish(now)`.
    - `_readback` ruft `self.regulation.note_result`, `_update_operation` und `_control` rufen `self.regulation.forget`.
    - `_update_alarms` bekommt `*self.regulation.alarms()`.
    - `_heartbeat`: `allowed` verlangt zusätzlich `not self.regulation.failed`.
    - `health()`: `vehicles=self.regulation.health()["vehicles"]`.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_regulation.py
def test_regulation_passes_the_charging_through(cfg) -> None:
    # Regulation(cfg, HardLimits.from_config(cfg), started=T0).step(RegulationInputs(inputs(cfg)))
    # liefert dieselben Befehle wie ein ChargeController mit denselben Eingaben; health()["vehicles"]
    # gleicht charger.health(), vehicle_wallbox() gleicht charger.vehicle_wallbox()


# tests/unit/core/test_regulation_link.py
def test_a_throwing_kernel_fails_the_cycle_and_rests(cfg, monkeypatch) -> None:
    # step des Kerns wirft RuntimeError("kaputt") → link.step(...) == [], link.failed, der Kern bekam idle;
    # nach link.finish(NOW): link.alarms() == [Alarm("control.exception", Severity.ERROR,
    #   "Regelung ausgesetzt: Ausnahme RuntimeError im Regelzyklus.", NOW)]


def test_the_alarm_keeps_the_start_of_the_streak_and_ends_with_a_good_cycle(cfg, monkeypatch) -> None:
    # zwei gescheiterte Zyklen bei NOW und NOW + 1 s → since NOW; danach ein Zyklus ohne Ausnahme → keine Alarme


def test_a_throwing_note_verdict_fails_the_cycle(cfg, monkeypatch) -> None:
    # note_verdict des Kerns wirft → link.failed, der Kern bekam idle; nach start_cycle ist failed wieder falsch


# tests/integration/test_runtime_regulation.py
async def test_exception_in_the_regulation_keeps_the_keeper_running(runtime_env, monkeypatch) -> None:
    # Aufbau wie test_handback_gets_no_charging_commands (EVCS live und owned, dann EVCS im Dry-Run);
    # rt.regulation.regulation.step wirft → cycle_once wirft nicht, die Entscheidungen mit „handback“
    # stehen im Log, rt.health().alarms nennt „control.exception“, und während 15 s mit Ausnahme
    # geht kein Heartbeat hinaus (der Zähler von watchdog_send steigt nicht)


async def test_regulation_recovers_after_the_exception(runtime_env, monkeypatch) -> None:
    # step wirft 3 Zyklen lang, dann nicht mehr → der Alarm ist weg, health().vehicles ist wieder gefüllt,
    # und die Regelung entscheidet wie ohne Ausnahme (dieselben Entscheidungen wie test_dry_run_decides_only_its_own_changes)
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_regulation.py tests/unit/core/test_regulation_link.py tests/integration/test_runtime_regulation.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Alles laufen lassen**

Run: wie Task 1, Step 4, dazu `uv run pytest tests/integration/test_sim_scenarios.py -q`
Expected: PASS, keine bestehende Testdatei geändert. `runtime.py` hat rund 1200 Zeilen.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests/unit/core tests/integration/test_runtime_regulation.py
git commit -m "feat(core): Regelungs-Kern mit eigener Verdrahtung; eine Ausnahme setzt nur die Regelung aus"
```

---

### Task 4: Konfiguration und Absicht des Gastes

**Files:**
- Modify: `src/bindaems/shared/config.py`, `src/bindaems/shared/control.py`, `deploy/config.example.yaml`
- Test: `tests/unit/shared/test_config.py`, `tests/unit/shared/test_control.py`, `tests/unit/core/control/test_intent.py`

**Interfaces:**
- Consumes: `ControlConfig`, `Intent`, `ChargeMode`, `_Model`
- Produces:
  - **`ControlConfig`** bekommt (Design 3.2):

    | Feld | Standard | Bedingung |
    |---|---|---|
    | `support_soc_pct` | `70.0` | 0 bis 100 |
    | `support_hysteresis_pct` | `5.0` | ≥ 0 |
    | `support_max_w` | `3000.0` | ≥ 0 |
    | `support_max_s` | `900.0` | ≥ 0 |
    | `setpoint_deadband_w` | `200.0` | > 0 (Gesamtreview; vorher ≥ 0) |
    | `override_refresh_s` | `240.0` | > 0, ≤ 280 |
    | `peak_target_kw` | `None` | > 0 |
    | `peak_margin_kw` | `0.3` | ≥ 0 |

    Die Bedingungen stehen als `Field(...)` an den Feldern; der Docstring nennt jedes Feld mit einer Zeile.
  - **`shared/control.py`:** `class GuestIntent(_Model)`: `mode: ChargeMode = ChargeMode.PV`; Docstring: Modus eines Autos ohne Fahrzeug der Konfiguration (Design 3.1), ohne Boost und Übersteuerung. Dazu `Intent.guest: GuestIntent = Field(default_factory=GuestIntent)` als letztes Feld. Der Docstring von `Intent` nennt es.
  - **`deploy/config.example.yaml`:** die neuen Felder unter `control:` mit je einer Kommentarzeile; `peak_target_kw` auskommentiert (`# peak_target_kw: 8   # Ziel des 15-min-Wächters in kW; ohne Wert ruht er`).

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/shared/test_config.py
def test_battery_and_peak_defaults(cfg) -> None:
    control = cfg.control
    assert (control.support_soc_pct, control.support_hysteresis_pct) == (70.0, 5.0)
    assert (control.support_max_w, control.support_max_s) == (3000.0, 900.0)
    assert (control.setpoint_deadband_w, control.override_refresh_s) == (200.0, 240.0)
    assert (control.peak_target_kw, control.peak_margin_kw) == (None, 0.3)


@pytest.mark.parametrize(
    ("field", "value"),
    [("override_refresh_s", 0), ("override_refresh_s", 281), ("peak_target_kw", 0),
     ("support_soc_pct", 101), ("support_max_w", -1), ("peak_margin_kw", -0.1)],
)
def test_invalid_battery_and_peak_values_are_rejected(field: str, value: float) -> None:
    with pytest.raises(ValidationError):
        ControlConfig(**{field: value})


def test_override_refresh_may_reach_280_s() -> None:
    assert ControlConfig(override_refresh_s=280).override_refresh_s == 280


# tests/unit/shared/test_control.py
def test_guest_charges_in_pv_by_default() -> None:
    assert Intent(version=1).guest == GuestIntent(mode=ChargeMode.PV)
    assert Intent.model_validate({"version": 2, "guest": {"mode": "now"}}).guest.mode is ChargeMode.NOW


# tests/unit/core/control/test_intent.py
def test_intent_file_from_2c1_loads_with_the_guest_default(tmp_path, limits) -> None:
    # intent.json im Format von 2c-1 (ohne „guest“) → load() gelingt, current.guest.mode is ChargeMode.PV
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/shared tests/unit/core/control/test_intent.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit -q`
Expected: PASS. Ändert sich ein Vertrag der UI (`ui/src/lib/api/contract/*.json`), ihn mit `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py` neu schreiben und `cd ui && pnpm check && pnpm test` laufen lassen.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared deploy/config.example.yaml tests/unit ui/src/lib/api/contract
git commit -m "feat(shared): Parameter für Akku-Sollwert und 15-min-Wächter, Modus des Gastes in der Absicht"
```

---

### Task 5: Auffrischen in Safety-Schicht, Ausführer und Erkennung fremder Änderungen

**Files:**
- Modify: `src/bindaems/shared/control.py`, `src/bindaems/core/safety/layer.py`, `src/bindaems/core/control/executor.py`, `src/bindaems/core/control/foreign.py`, `src/bindaems/core/actuators/victron.py`
- Test: `tests/unit/core/safety/test_layer.py`, `tests/unit/core/control/test_executor.py`, `tests/unit/core/control/test_foreign.py`, `tests/property/test_safety_layer.py`

**Interfaces:**
- Consumes: `SafetyLayer.authorize`, `CommandExecutor`, `ForeignChangeDetector`
- Produces:
  - **`Command.refresh: bool = False`** als letztes Feld; Docstring: Auffrischen eines Overrides mit gleichem Wert (Plan 2c-2a, Design 8); wirkt nur bei `grid_setpoint`.
  - **`SafetyLayer.authorize`:**
    - Schritt 2: `urgent_ok` verlangt zusätzlich `not command.refresh`.
    - Schritt 9 entfällt für `command.refresh` bei `grid_setpoint` (Präzisierung 7).
    - Der Docstring nennt beides.
  - **`CommandExecutor`:** Für einen Befehl mit `refresh` entsteht bei `DRY_RUN` und bei `OK` keine Entscheidung. `WriteResult` und `take_failures` bleiben, wie sie sind. Der Modul-Docstring nennt das.
  - **`victron.py`:** `OVERRIDE_TIMEOUT_S: Final = 300.0`; so lange hält der Cerbo einen Override ohne Schreiben (Prüfprotokoll Teil 2).
  - **`ForeignChangeDetector.expect(self, actuator, kind, value, *, lapses_at: datetime | None = None)`:** Eine Erwartung mit `lapses_at` entfällt in `check` ab `now >= lapses_at`, ohne Treffer. Ein neues `expect` ersetzt sie samt Frist. Die Laufzeit nutzt die Frist ab Task 8, wenn die Regelung den ersten Override mit Zahl sendet.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/safety/test_layer.py
SETPOINT = ("victron", K.GRID_SETPOINT)


def test_refresh_sends_the_same_override(layer) -> None:
    refresh = Command("victron", K.GRID_SETPOINT, 2000.0, refresh=True)
    verdict = layer.authorize(refresh, make_ctx(present={SETPOINT: 2000.0}))
    assert (verdict.outcome, verdict.value, verdict.send) == (Outcome.ACCEPTED, 2000.0, True)
    plain = Command("victron", K.GRID_SETPOINT, 2000.0)
    assert layer.authorize(plain, make_ctx(now=S(5), present={SETPOINT: 2000.0})).outcome is Outcome.UNCHANGED


def test_refresh_passes_inside_the_deadband(layer) -> None:
    refresh = Command("victron", K.GRID_SETPOINT, 2000.0, refresh=True)
    assert layer.authorize(refresh, make_ctx(present={SETPOINT: 1990.0})).outcome is Outcome.ACCEPTED


def test_refresh_keeps_interval_lock_and_state(layer) -> None:
    # Auffrischen bei S(0) angenommen; ein zweites bei S(1) → DEFERRED „rate.interval“;
    # mit locks={"victron": ("DESS",)} → REJECTED „lock“; in SAFE → REJECTED „state.inactive“


def test_refresh_is_never_urgent(layer) -> None:
    command = Command("victron", K.GRID_SETPOINT, 2000.0, urgent=True, refresh=True)
    verdict = layer.authorize(command, make_ctx(present={SETPOINT: 2000.0}))
    assert (verdict.outcome, verdict.findings[0].code) == (Outcome.ACCEPTED, "urgent.ignored")


def test_refresh_does_nothing_for_other_kinds(layer) -> None:
    command = Command("wallbox.evcs", K.CURRENT, 10.0, refresh=True)
    ctx = make_ctx(present={("wallbox.evcs", K.CURRENT): 10.0})
    assert layer.authorize(command, ctx).outcome is Outcome.UNCHANGED


# tests/unit/core/control/test_executor.py
def test_a_successful_refresh_is_not_logged() -> None:
    # Victron live: Auffrischen gesendet, poll mit Rücklesen 2000 → WriteResult OK, keine Entscheidung
def test_a_dry_run_refresh_is_not_logged() -> None:
    # Victron im Dry-Run: Urteil mit Wert, keine Entscheidung
def test_a_failed_or_rejected_refresh_is_logged() -> None:
    # TIMEOUT → Entscheidung „nicht bestätigt (Zeitüberschreitung)“ und in take_failures; gesperrt → REJECTED-Entscheidung


# tests/unit/core/control/test_foreign.py
NONE_PRESENT = {("victron", CommandKind.GRID_SETPOINT): None}


def test_override_expectation_lapses_with_the_override() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect("victron", CommandKind.GRID_SETPOINT, 2000.0, lapses_at=T0 + timedelta(seconds=300))
    found = [detector.check(NONE_PRESENT, frozenset(), T0 + timedelta(seconds=s)) for s in (300, 310, 320)]
    assert found == [[], [], []]


def test_a_refreshed_expectation_still_finds_a_foreign_change() -> None:
    # expect bei T0 (Frist T0+300), erneut bei T0+240 (Frist T0+540); „kein Override“ bei T0+300, +310, +320
    # → die dritte Prüfung meldet ForeignChange(…, expected=2000.0, actual=None, …)


# tests/property/test_safety_layer.py
# bestehende Strategien um refresh erweitern: Für jeden Befehl mit refresh gelten Sperre, Zustand,
# Betriebsart, Regeln 1–4 und der Mindestabstand wie ohne; nur „rate.unchanged“ und „rate.deadband“
# bei grid_setpoint fallen weg
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/safety/test_layer.py tests/unit/core/control/test_executor.py tests/unit/core/control/test_foreign.py tests/property/test_safety_layer.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core tests/property tests/integration -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests
git commit -m "feat(core): Auffrischen des Overrides durch die Safety-Schicht, ohne Eintrag im Log und ohne fremde Änderung"
```

---

### Task 6: Akku-Sollwert I – Fehlbetrag, Unterstützung, erlaubter Anteil

**Files:**
- Create: `src/bindaems/core/control/battery.py`
- Test: `tests/unit/core/control/test_battery.py`, `tests/property/test_battery.py`

**Interfaces:**
- Consumes: `Effective` (`charge_modes.py`), `held_since` (Task 1), `ControlConfig` (Task 4)
- Produces (`battery.py`, Teil 1; Design 5.2, Präzisierungen 4 und 5):
  - `BRIDGE_END_S: Final = 60.0`; `SupportReason = Literal["on", "soc", "time", "mode"]`; `SUPPORT_MODES: Final = frozenset({Effective.PV, Effective.MIN_PV})`
  - `@dataclass(frozen=True) class BatteryCar`: `wallbox: str`, `mode: Effective`, `power_w: float | None`, `charging: bool | None`
  - `deficits(pv_w: float, house_w: float, cars: Sequence[BatteryCar]) -> dict[str, float] | None`: der Fehlbetrag je Wallbox in der Reihenfolge von `cars`; `None` nach der Regel „unbekannt“ aus Präzisierung 4
  - `class Support`:
    - `__init__(self, control: ControlConfig) -> None`
    - `update(self, now: datetime, soc_pct: float | None, cars: Sequence[BatteryCar], deficit: Mapping[str, float]) -> tuple[SupportReason, float | None]`: der Stand und die Sekunden der laufenden Überbrückung (`None` ohne). Er rechnet auf der Uhr der Safety-Schicht.
    - `reset(self) -> None`
  - `allowed(cars: Sequence[BatteryCar], deficit: Mapping[str, float], *, support_on: bool, support_max_w: float) -> dict[str, float]`: der erlaubte Akku-Anteil je Wallbox (Präzisierung 5, „Verteilung“)

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_battery.py
def car(wallbox: str = "evcs", mode: Effective = Effective.PV, power: float | None = 4000.0,
        charging: bool | None = True) -> BatteryCar:
    return BatteryCar(wallbox, mode, power, charging)


def test_pv_covers_the_house_first_then_the_cars_by_priority() -> None:
    cars = [car("twc", Effective.PV, 4000.0), car("evcs", Effective.NOW, 3000.0)]
    assert deficits(6000.0, 1000.0, cars) == {"twc": 0.0, "evcs": 2000.0}


def test_sunny_car_has_no_deficit() -> None:  # Design 5.1: 6 kW PV, 1 kW Haus, 5 kW Auto
    assert deficits(6000.0, 1000.0, [car(power=5000.0)]) == {"evcs": 0.0}


def test_unknown_power_of_a_charging_car_makes_the_need_unknown() -> None:  # Review Focus 3
    assert deficits(6000.0, 1000.0, [car(power=None, charging=None)]) is None
    assert deficits(6000.0, 1000.0, [car(power=None, charging=False)]) == {"evcs": 0.0}


def test_negative_pv_and_house_count_as_zero() -> None:
    assert deficits(-20.0, -5.0, [car(power=1000.0)]) == {"evcs": 1000.0}


def test_support_needs_70_percent_and_ends_below_65() -> None:
    support, cars, deficit = Support(ControlConfig()), [car(mode=Effective.MIN_PV)], {"evcs": 500.0}
    socs = [69.9, 70.0, 65.0, 64.9, 69.0, 70.0]
    found = [support.update(T0 + timedelta(seconds=s), soc, cars, deficit)[0] for s, soc in enumerate(socs)]
    assert found == ["soc", "on", "on", "soc", "soc", "on"]


def test_unknown_soc_blocks_without_resetting_the_hysteresis() -> None:
    # 72 → „on“, None → „soc“, 66 → „on“


def test_bridging_blocks_after_900_s_until_60_s_without_deficit() -> None:
    # SOC 80, Fehlbetrag 500 W ab T0: „on“ bis T0+899 (bridging_s 899.0), „time“ ab T0+900;
    # ab T0+900 Fehlbetrag 0: bei T0+959 „time“, bei T0+960 ist die Überbrückung beendet;
    # Fehlbetrag 500 W bei T0+961 → („on“, 0.0)


def test_small_deficits_neither_start_nor_extend_the_bridging() -> None:
    # SOC 80: Fehlbetrag 99 W → („on“, None); 100 W → („on“, 0.0)


def test_without_pv_or_min_pv_cars_the_reason_is_mode() -> None:
    # nur ein Auto in Sofort, SOC 80 → („mode“, None)


def test_allowed_share_follows_the_mode_and_the_limit() -> None:
    cars = [car("a", Effective.PV), car("b", Effective.MIN_PV), car("c", Effective.NOW), car("d", Effective.BOOST)]
    deficit = {"a": 2000.0, "b": 2000.0, "c": 4000.0, "d": 5000.0}
    on = allowed(cars, deficit, support_on=True, support_max_w=3000.0)
    off = allowed(cars, deficit, support_on=False, support_max_w=3000.0)
    assert on == {"a": 2000.0, "b": 1000.0, "c": 0.0, "d": 5000.0}
    assert off == {"a": 0.0, "b": 0.0, "c": 0.0, "d": 5000.0}


# tests/property/test_battery.py
# Strategien: PV und Haus −1000 bis 20000 W, ein bis drei Autos mit Modus, Leistung 0 bis 11000 W
def test_wanted_grid_is_never_negative_nor_above_the_deficit(...) -> None:
    # W = Σ max(0, Fehlbetrag − erlaubt): 0 ≤ W ≤ Σ Fehlbetrag
def test_more_allowed_battery_never_means_more_grid(...) -> None:
    # support_max_w a ≤ b und support_on falsch ≤ wahr → W(b) ≤ W(a)
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_battery.py tests/property/test_battery.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_battery.py tests/property/test_battery.py -q && uv run lint-imports`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control/battery.py tests/unit/core/control/test_battery.py tests/property/test_battery.py
git commit -m "feat(core): Fehlbetrag der Autos und Akku-Unterstützung nach Modus, SOC und Dauer"
```

---

### Task 7: Akku-Sollwert II – Override, Auffrischen, Sperren, Health

**Files:**
- Modify: `src/bindaems/core/control/battery.py`, `src/bindaems/core/control/phase_guard.py`
- Test: `tests/unit/core/control/test_battery_controller.py`

**Interfaces:**
- Consumes: Task 6, `LastValues`, `ControlCommand` (Task 1), `Command.refresh` (Task 5), `VICTRON`
- Produces (`battery.py`, Teil 2; Design 5.3 bis 5.6, Präzisierungen 6 bis 8):
  - `BATTERY_COMP: Final = "battery_comp"`, `BATTERY_SUPPORT: Final = "battery_support"`
  - **`phase_guard.py`:** `GuardCause.PEAK_GUARD = "peak_guard"`; der Akku-Sollwert nennt den Grund als `GuardCause.PEAK_GUARD.value`, die Laderegelung ab Task 10 als Ursache
  - `@dataclass(frozen=True) class BatteryNeed`:
    - Felder: `pv_w: float`, `house_w: float`, `cars_w: float`, `deficit_w: Mapping[str, float]`, `allowed_w: Mapping[str, float]`, `support: SupportReason`, `bridging_s: float | None`
    - Eigenschaften: `grid_w -> dict[str, float]` (je Wallbox `max(0, Fehlbetrag − erlaubt)`) und `wanted_w -> float` (ihre Summe, `W`)
  - `class BatteryController`:
    - `__init__(self, cfg: Config) -> None`
    - `assess(self, now: datetime, soc_pct: float | None, pv_w: float | None, house_w: float | None, cars: Sequence[BatteryCar]) -> BatteryNeed | None`: `None`, wenn PV, Haus oder `deficits` unbekannt sind. Dann bleibt `Support` unberührt.
    - `decide(self, now: datetime, need: BatteryNeed | None, *, base_w: float | None, budget_w: float | None, present: Mapping[Key, CommandValue], controllable: bool, locked: bool, blocked: bool) -> list[ControlCommand]`: höchstens ein Befehl an `(VICTRON, GRID_SETPOINT)` nach den Präzisierungen 6 bis 8, mit `W' = max(0, min(W, floor10(budget_w) − base_w))` bei einem Budget (Präzisierung 12)
    - `note_verdict(self, command: Command, verdict: Verdict | None, *, not_sent: bool = False) -> None`: über `LastValues.judged`. Ein Urteil mit Wert (`sendable`, ohne `not_sent`) merkt `now` des letzten `decide` als Zeit fürs Auffrischen.
    - `note_result(self, result: WriteResult) -> None`, `forget(self, actuators: Collection[str]) -> None`
    - `idle(self) -> None`: vergisst die Werte und beginnt `Support` neu
    - `health(self) -> dict[str, Any]`:
      - `state`: `idle` vor dem ersten und nach `idle`, `locked`, `unknown` (Bedarf oder Basis unbekannt), sonst `ok`
      - `target_w` (`W'`), `override_w` (letzter eigener Wert als Zahl, sonst `None`), `support`, `bridging_s`
      - `refresh_age_s`: Sekunden seit dem letzten Urteil mit Wert über einen eigenen Override, sonst `None`
  - Die Eingaben jedes Befehls (Design 9): `pv_w`, `house_w`, `cars_w`, `deficit_w` (Summe), `allowed_w` (Summe), `target_w` (`W'`), `actual_setpoint_w` (Override laut Cerbo als Zahl; „kein Override“ und unbekannt als `None`).

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_battery_controller.py
SETPOINT = (VICTRON, CommandKind.GRID_SETPOINT)
FREE = {"base_w": 0.0, "budget_w": None, "present": {SETPOINT: None}, "controllable": True,
        "locked": False, "blocked": False}


def need(wanted: float, allowed: float = 0.0) -> BatteryNeed:
    return BatteryNeed(0.0, 500.0, wanted, {"evcs": wanted}, {"evcs": allowed}, "soc", None)


def accept(battery: BatteryController, commands: list[ControlCommand], dry_run: bool = True) -> None:
    for found in commands:
        command = found.command
        battery.note_verdict(command, Verdict(command, Outcome.ACCEPTED, command.value, (), dry_run))


def test_override_starts_at_200_w(cfg) -> None:
    battery = BatteryController(cfg)
    assert battery.decide(T0, need(190.0), **FREE) == []
    [found] = battery.decide(T0, need(204.0), **FREE)
    assert found.command == Command(VICTRON, CommandKind.GRID_SETPOINT, 200.0, reasons=("battery_comp",))
    assert found.inputs == {"pv_w": 0.0, "house_w": 500.0, "cars_w": 204.0, "deficit_w": 204.0,
                            "allowed_w": 0.0, "target_w": 204.0, "actual_setpoint_w": None}


def test_override_follows_only_changes_of_200_w(cfg) -> None:
    # eigener Wert 2000 (Urteil): W 2150 → []; W 2200 → 2200.0; W 2390 → []


def test_override_falls_away_below_100_w(cfg) -> None:
    # eigener Wert 2000: W 100 → 100.0; danach W 99 → None


def test_no_override_is_lifted_only_when_there_is_one(cfg) -> None:
    # ohne eigenen Wert, W 0: present {SETPOINT: None} → []; present {} → []; present {SETPOINT: 1500.0} → [None]


def test_target_adds_the_base_and_rounds_to_10_w(cfg) -> None:
    # Basis 50: W 1234 → 1280.0; W 1235 → 1290.0 (halbe Zehner aufwärts); Basis None → [] und Health „unknown“


def test_support_reason_when_battery_may_help(cfg) -> None:
    # need(3000.0, allowed=1000.0) → reasons ("battery_support",)


def test_refresh_follows_240_s_after_the_last_verdict(cfg) -> None:
    battery = BatteryController(cfg)
    accept(battery, battery.decide(T0, need(2000.0), **FREE))
    assert battery.decide(T0 + timedelta(seconds=239), need(2000.0), **FREE) == []
    [found] = battery.decide(T0 + timedelta(seconds=240), need(2050.0), **FREE)
    assert found.command == Command(VICTRON, CommandKind.GRID_SETPOINT, 2000.0, reasons=("battery_comp",), refresh=True)
    accept(battery, [found])
    assert battery.decide(T0 + timedelta(seconds=479), need(2000.0), **FREE) == []


def test_blocked_refresh_follows_in_the_next_cycle(cfg) -> None:  # Review Focus 1
    # fällig bei T0+240, aber blocked → []; bei T0+241 frei → Auffrischen


def test_failed_refresh_sends_the_value_again(cfg) -> None:  # Review Focus 1
    # Auffrischen bei T0+240 mit Urteil; note_result(WriteResult(VICTRON, GRID_SETPOINT, 2000.0, TIMEOUT, ""))
    # → bei T0+241 Command(…, 2000.0, reasons=("battery_comp",)) ohne refresh


def test_locked_victron_gets_nothing_and_forgets(cfg) -> None:  # Review Focus 2
    # eigener Wert 2000; locked → [] und health()["state"] == "locked"; danach frei mit W 2000 → 2000.0
    # (ohne eigenen Wert geht er neu hinaus); dasselbe mit controllable=False


def test_unknown_need_keeps_the_override_without_refresh(cfg) -> None:  # Review Focus 3
    # eigener Wert 2000 bei T0; need None bei T0+240 → [], Health „unknown“;
    # bei T0+241 W 2050 → Auffrischen 2000.0, kein „kein Override“ dazwischen


def test_budget_lowers_the_target_with_the_peak_reason(cfg) -> None:
    # W 5000, Basis 0, budget_w 3000 → 3000.0 mit ("battery_comp", "peak_guard");
    # budget_w −500 → W' 0, kein Befehl (der Cerbo meldet keinen Override)


def test_health_of_the_battery(cfg) -> None:
    # vor decide: {"state": "idle", "target_w": None, "override_w": None, "support": None,
    #              "bridging_s": None, "refresh_age_s": None}
    # nach Override 2000 bei T0 und decide bei T0+10: state "ok", target_w 2000.0, override_w 2000.0,
    #   support "soc", refresh_age_s 10.0


def test_assess_combines_deficit_support_and_allowed(cfg) -> None:
    # SOC 80, PV 0, Haus 500, ein Auto in Min+PV mit 2760 W → deficit 2760, allowed 2760, wanted_w 0.0,
    # support "on"; PV None → None
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_battery_controller.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control/battery.py src/bindaems/core/control/phase_guard.py tests/unit/core/control/test_battery_controller.py
git commit -m "feat(core): Akku-Sollwert mit Totband, Auffrischen und Sperren"
```

---

### Task 8: Akku-Sollwert im Kern und in der Laufzeit

**Files:**
- Modify: `src/bindaems/core/control/charging.py`, `src/bindaems/core/control/regulation.py`, `src/bindaems/core/regulation_link.py`, `src/bindaems/core/runtime.py`, `src/bindaems/shared/decisions.py`, `src/bindaems/core/api/app.py`, `src/bindaems/tools/sim/runner.py`, `tests/integration/plant.py`, `tests/integration/conftest.py`
- Test: `tests/unit/core/control/test_charging.py`, `tests/unit/core/control/test_regulation.py`, `tests/integration/test_runtime_battery.py`, `tests/unit/shared/test_decisions.py`, `tests/unit/tools/sim/test_sim_runner.py`

**Interfaces:**
- Consumes: `BatteryController`, `BatteryCar`, `BatteryNeed` (Tasks 6, 7), `RegulationLink` (Task 3), `ForeignChangeDetector.expect(…, lapses_at=…)`, `OVERRIDE_TIMEOUT_S` (Task 5)
- Produces:
  - **`charging.py`:**
    - `@dataclass(frozen=True) class CarView`: `vehicle: str`, `wallbox: str`, `mode: Effective`, `power_w: float | None`, `charging: bool | None`, `min_w: float`
    - `ChargeController.cars(self) -> tuple[CarView, ...]`: je Ladepunkt des letzten `step` in der Reihenfolge der Priorität. Leer vor dem ersten `step` und nach `idle`.
      - `power_w` nach Präzisierung 4
      - `charging` aus der Zuordnung
      - `min_w = min_a × U × Zahl der Phasen` des Ladepunkts, mit `U` = `grid.voltage_nominal_v`
  - **`RegulationInputs`** bekommt `pv_w: float | None`, `house_w: float | None`, `base_setpoint_w: float | None` und `locks: Mapping[str, tuple[str, ...]]`, alle mit Standard (`None` bzw. `{}`).
  - **`Regulation`:**
    - `step`: nach der Laderegelung `battery_cars(...)` und `BatteryController.assess`, dann `decide`; die Befehle hinter denen der Ladepunkte (Präzisierung 3). `controllable` von Victron aus `inputs.charge.controllable`, `blocked` aus `inputs.charge.blocked`, `present` aus `inputs.charge.present`.
    - `note_verdict`, `note_result`, `forget` und `idle` gehen bei `(VICTRON, GRID_SETPOINT)` an den Akku-Sollwert, sonst an die Laderegelung. `forget` und `idle` erreichen beide.
    - `health()` bekommt `"battery": battery.health()`.
    - Modulfunktion `battery_cars(cfg: Config, cars: Sequence[CarView], wallboxes: Mapping[str, WallboxObs], guest: ChargeMode) -> list[BatteryCar]` nach Präzisierung 4; die Wallboxen ohne Fahrzeug bekommen `Effective(guest.value)`.
  - **`RegulationLink.step`** bekommt `locks: Mapping[str, tuple[str, ...]]` und füllt die neuen Felder:
    - PV aus `derived.pv_total_w`, Haus aus `derived.house_load_w`
    - Basis aus `ok_number(snap, "ess.grid_setpoint_w")`
    - `locks` wie im `SafetyContext`
  - **`runtime.py`:**
    - übergibt `locks`
    - `_note_write(write, snow)`: ein bestätigter Override mit Zahl mit `lapses_at = snow + OVERRIDE_TIMEOUT_S` (Präzisierung 7); `_readback` gibt `snow` mit
    - `health()`: `battery=self.regulation.health()["battery"]`
    - Die Eigenschaften `charger` von `CoreRuntime` und `RegulationLink` entfallen.
  - **Simulator (`runner.py`):** `_own_values` hüllt `note_verdict`, `forget` und `idle` von `rt.regulation` ein (Präzisierung 17); die Tests in `test_sim_runner.py`, die `rt.charger` nutzen, nutzen `rt.regulation`.
  - **`HealthReport.battery: dict[str, Any] = Field(default_factory=dict)`;** der Docstring nennt die Felder aus Task 7.
  - **`_REASON_TEXT`:** die drei Gründe aus „Texte“.
  - **Tests:**
    - `Plant` übernimmt auch `hub4/0/Overrides/Setpoint` als `ess.override.setpoint`, solange `follow` gilt.
    - `GOOD_STATE` nennt `ess.grid_setpoint_w` nicht: Ohne Basis ist das Ziel unbekannt, und die bestehenden Laufzeittests sehen keine Entscheidung an Victron. Die neuen Tests setzen `ess.grid_setpoint_w` auf 0.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_charging.py
def test_cars_show_the_points_of_the_last_step(cfg) -> None:
    # e-Golf an der EVCS lädt mit 2760 W (evcs(charging=True, power_w=2760.0)), Modus Sofort → cars() ==
    # (CarView("egolf", "evcs", Effective.NOW, 2760.0, True, 2760.0),); nach idle → ()


# tests/unit/core/control/test_regulation.py
def test_wallbox_without_vehicle_counts_with_the_guest_mode(cfg) -> None:
    # Übersteuerung tesla → evcs: der Wall Connector hat kein Fahrzeug → battery_cars(...)[-1] ==
    # BatteryCar("twc", Effective.NOW, …) bei guest.mode now


def test_battery_commands_follow_the_charging_commands(cfg) -> None:
    # ein Zyklus mit Befehlen an die EVCS und einem Override: der Override kommt zuletzt


# tests/integration/test_runtime_battery.py
async def test_dry_run_override_for_a_car_in_now(runtime_env) -> None:
    # e-Golf an der EVCS lädt 3680 W in Sofort, keine PV, SOC 60 → Entscheidung an „victron“,
    # grid_setpoint 3680.0, Dry-Run, codes ("battery_comp",), inputs mit cars_w 3680.0 und target_w 3680.0


async def test_dess_lock_sends_nothing(runtime_env) -> None:  # Review Focus 2
    # wie oben mit dess.mode 1 → 120 s lang keine Entscheidung an „victron“, health().battery["state"] == "locked"


async def test_live_override_is_refreshed_without_a_decision(runtime_env) -> None:
    # Victron live (allow_victron_live, Plant folgt): eine Entscheidung „gesendet und bestätigt“ für 3680 W;
    # nach 240 s ein zweiter Schreibauftrag mit 3680 an hub4/0/Overrides/Setpoint, keine zweite Entscheidung


async def test_override_that_is_no_longer_refreshed_lapses_without_alarm(runtime_env) -> None:
    # wie oben; danach fehlt die PV (Signal entfernt): kein Auffrischen. Der Test löscht den Override
    # 300 s nach der letzten Bestätigung (ess.override.setpoint → None) → kein Alarm „competitor.foreign.victron“.
    # Löscht er ihn schon nach 100 s, kommt der Alarm wie bisher.


async def test_health_shows_the_battery(runtime_env) -> None:
    # nach dem ersten Override: health().battery["state"] == "ok", ["target_w"] == 3680.0, ["support"] == "soc"


# tests/unit/tools/sim/test_sim_runner.py
def test_own_values_of_the_battery_are_recorded(...) -> None:
    # der Lauf hüllt rt.regulation ein: ein Urteil über einen Override erscheint als OwnValue("victron", …)


# tests/unit/shared/test_decisions.py
def test_battery_reasons_have_texts() -> None:
    text = decision_text("Victron (ESS)", CommandKind.GRID_SETPOINT, 2340.0, DecisionResult.DRY_RUN,
                         reasons=("battery_comp",))
    assert text == "Victron (ESS) (Akku nicht ins Auto): Netz-Sollwert 2340 W – Dry-Run, nicht gesendet."
```

`allow_victron_live(cfg)` kommt nach `tests/integration/conftest.py` wie `allow_evcs_live`.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control tests/unit/shared/test_decisions.py tests/integration/test_runtime_battery.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest -q`
Expected: PASS, auch die Szenarien (Victron im Dry-Run). Die Vertragsdateien der UI mit `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py` neu schreiben, dann `cd ui && pnpm check && pnpm test`.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests ui/src/lib/api/contract
git commit -m "feat(core): Akku-Sollwert im Regelzyklus, Health und Gründe im Entscheidungslog"
```

---

### Task 9: 15-min-Wächter I – Messung, Budget, Hochrechnung, Zustände

**Files:**
- Create: `src/bindaems/core/control/peak_guard.py`
- Test: `tests/unit/core/control/test_peak_guard.py`, `tests/property/test_peak_guard.py`

**Interfaces:**
- Consumes: `Effective`, `MovingAverage` (`tesla_policy.py`), `held_since` (Task 1), `ControlConfig` (Task 4), `slot_end`, `LOCAL_TZ`, `de`
- Produces (`peak_guard.py`, Teil 1; Design 6.1, 6.2, 6.3 Punkt 3; Präzisierungen 9, 10, 12, 14):
  - `GAP_S: Final = 10.0`, `MEAN_S: Final = 30.0`, `EXCEEDED_AFTER_S: Final = 60.0`, `PEAK_EXCEEDED: Final = "peak.exceeded"`
  - `PeakState = Literal["off", "gap", "active", "exceeded"]`
  - `@dataclass(frozen=True) class QuarterObs`: `start: datetime` (Beginn der laufenden Viertelstunde), `grid_wh: float` (Netzbezug darin bis jetzt), `grid_w: float | None` (Netzbezug des Zyklus; `None` ohne Bilanz), `wallbox_grid_w: Mapping[str, float]` (Netzanteil je Wallbox im Zyklus)
  - `@dataclass(frozen=True) class PeakCar`: `vehicle: str`, `wallbox: str`, `mode: Effective`, `rank: int` (0 = höchste Priorität), `power_w: float | None`, `grid_w: float` (Netzanteil, Präzisierung 11), `min_w: float`
  - `@dataclass(frozen=True) class PeakLimits`: `budget_w: float | None = None`, `caps_w: Mapping[str, float] = field(default_factory=dict)`, `no_start: frozenset[str] = frozenset()`; dazu `NO_LIMITS: Final = PeakLimits()`
  - `class PeakGuard`:
    - `__init__(self, control: ControlConfig, reserve_soc_pct: float) -> None`
    - `step(self, wall_now: datetime, quarter: QuarterObs | None, cars: Sequence[PeakCar], wanted_w: float | None, base_w: float | None, soc_pct: float | None) -> PeakLimits`
      - Ohne Ziel: `NO_LIMITS`, Zustand `off`.
      - Sonst: Messung und Lücken nach Präzisierung 9, `P_zul` und Senkung nach Präzisierung 10, Budget nach Präzisierung 12, „überschritten“ nach Präzisierung 14.
      - Ohne `quarter` oder lückenhaft: Zustand `gap` und `NO_LIMITS`.
      - Ist `wanted_w` oder `base_w` unbekannt, zählt für die Senkung nur das Mittel.
      - In diesem Task bleiben `caps_w` und `no_start` leer.
    - `idle(self) -> None`: Die laufende Viertelstunde ist lückenhaft (Präzisierung 9).
    - `health(self) -> dict[str, Any]`:
      - `state`, `target_kw`
      - `energy_kwh` (E_bisher ohne Boost), `projected_kw` (Hochrechnung), `budget_kw` (`P_zul`), `reduction_kw` (verlangte Senkung, mindestens 0)
      - `quarter_end` (ISO, UTC)

      Ohne Wert jeweils `None`; `budget_kw` auch unter der Schwelle von Präzisierung 12.
    - `alarms(self) -> list[Alarm]`: `peak.exceeded` nach „Texte“, solange der Zustand `exceeded` ist

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_peak_guard.py
QUARTER = datetime(2026, 10, 14, 16, 0, tzinfo=UTC)  # 18:00 Wiener Zeit


def guard(target: float | None = 8.0) -> PeakGuard:
    return PeakGuard(ControlConfig(peak_target_kw=target), reserve_soc_pct=20.0)


def run(guard: PeakGuard, first_s: int, last_s: int, *, grid_w: float | None = 0.0,
        wh: Callable[[int], float] = lambda s: 0.0, wallbox_grid_w: Mapping[str, float] = {},
        cars: Sequence[PeakCar] = (), wanted_w: float | None = 0.0, soc: float | None = 60.0) -> PeakLimits:
    """Ein Schritt je Sekunde von QUARTER + first_s bis QUARTER + last_s; zurück die Grenzen des letzten."""


def test_without_a_target_the_guard_rests() -> None:
    g = guard(None)
    assert run(g, 0, 10) == NO_LIMITS
    assert g.health()["state"] == "off"


def test_budget_after_the_formula() -> None:  # Design 6.2
    g = guard()
    limits = run(g, 0, 300, wh=lambda s: 1000.0 if s == 300 else 0.0)
    # 18:05: (2000 Wh − 1000 Wh) · 3600 / 600 s − 300 W
    assert limits.budget_w == 5700.0
    assert g.health()["budget_kw"] == 5.7


def test_budget_reaches_the_battery_only_above_the_reserve_guard() -> None:  # Präzisierung 12
    # wie oben mit soc 25.0 → budget_w None, health budget_kw 5.7; soc 25.1 → 5700.0; soc None → None


def test_boost_energy_and_power_are_left_out() -> None:  # Design 6.1, 6.4
    # Auto „egolf“ an „evcs“ in Boost, wallbox_grid_w {"evcs": 3600.0} und grid_w 3600 für 61 Schritte
    # → energy_kwh == grid_wh/1000 − 0.06, Mittel 0, reduction_kw 0


def test_projection_and_reduction() -> None:
    # 18:05, E 1000 Wh, Mittel 9000 W → projected_kw 10.0 ((1000 + 9000 · 600 / 3600) · 4 / 1000),
    # reduction_kw 3.3 (9000 − 5700)


def test_wanted_grid_counts_when_it_is_higher_than_the_mean() -> None:  # Präzisierung 10
    # Mittel 0, Basis 0, wanted_w 10700 bei P_zul 5700 → reduction_kw 5.0


def test_gap_over_10_s_rests_until_the_next_quarter() -> None:
    # 11 Schritte mit grid_w None → „gap“, budget_w None; ab 18:15:00 (neue Viertelstunde, Werte da) „active“


def test_start_mid_quarter_is_a_gap() -> None:  # Review Focus 4
    # erster Schritt bei 18:05 → „gap“ bis 18:14:59; bei 18:15:00 „active“


def test_idle_makes_the_quarter_a_gap() -> None:  # Review Focus 4
    # aktiv bis 18:05, idle, Schritte ab 18:05:01 → „gap“ bis zum Ende der Viertelstunde


def test_exceeded_after_60_s_warns_until_the_quarter_end() -> None:  # Präzisierung 14
    g = guard()
    run(g, 0, 59, grid_w=12300.0, wh=lambda s: 12300.0 * s / 3600)
    assert g.alarms() == []
    run(g, 60, 60, grid_w=12300.0, wh=lambda s: 12300.0 * s / 3600)
    assert g.alarms() == [Alarm("peak.exceeded", Severity.WARNING,
                                "15-min-Ziel überschritten: 12,3 kW in der Viertelstunde bis 18:15.",
                                QUARTER + timedelta(seconds=60))]
    # danach bleibt der Alarm bis 18:14:59 gleich, ab 18:15:00 ist er weg


# tests/property/test_peak_guard.py
def test_budget_is_finite_and_follows_the_formula(...) -> None:
    # E 0 bis 10000 Wh, Sekunde 0 bis 899, Ziel 1 bis 50 kW, Marge 0 bis 2 kW:
    # budget_kw · 1000 ≈ (Ziel · 250 − E) · 3600 / max(1, 900 − Sekunde) − Marge · 1000, immer endlich
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_peak_guard.py tests/property/test_peak_guard.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces; das Mittel über `MovingAverage(MEAN_S)` auf der Wanduhr.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_peak_guard.py tests/property/test_peak_guard.py -q && uv run lint-imports`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control/peak_guard.py tests/unit/core/control/test_peak_guard.py tests/property/test_peak_guard.py
git commit -m "feat(core): 15-min-Wächter misst die Viertelstunde, rechnet hoch und warnt"
```

---

### Task 10: 15-min-Wächter II – Drosselung und Start-Sperre, Grenzen in der Laderegelung

**Files:**
- Modify: `src/bindaems/core/control/peak_guard.py`, `src/bindaems/core/control/charging.py`
- Test: `tests/unit/core/control/test_peak_throttle.py`, `tests/unit/core/control/test_charging.py`, `tests/property/test_peak_guard.py`

**Interfaces:**
- Consumes: Task 9, `ChargeController` (Tasks 2, 8), `GuardCause.PEAK_GUARD` (Task 7)
- Produces:
  - **`peak_guard.py`:**
    - `SETTLE_S: Final = 30.0`, `RELEASE_W: Final = 1000.0`, `LAST_S: Final = 120.0`
    - `PeakGuard.step` füllt `caps_w` nach Präzisierung 11 und `no_start` nach Präzisierung 13.
    - Grenzen und Start-Sperre gibt es nur in den Zuständen `active` und `exceeded`; sonst `NO_LIMITS` ohne Grenzen, und eine neue Viertelstunde beginnt ohne.
  - **`ChargeController.step(self, inputs: ChargeInputs, peak: PeakLimits = NO_LIMITS) -> list[ChargeCommand]`:**
    - Grenze und Start-Sperre wirken nach den Präzisierungen 11 und 13, nicht für ein Auto in Boost.
    - Senkungen wegen der Grenze sind normal, nie dringend.
    - Der Modul-Docstring nennt die Regel.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_peak_throttle.py
# Aufbau wie test_peak_guard.py: aktiv ab 18:00, Mittel 0, bei 18:05 E 1000 Wh → P_zul 5700 W
A = PeakCar("a", "twc", Effective.NOW, 0, 7000.0, 7000.0, 3450.0)
B = PeakCar("b", "evcs", Effective.MIN_PV, 1, 4000.0, 4000.0, 2760.0)
C = PeakCar("c", "x", Effective.PV, 2, 3000.0, 3000.0, 1380.0)


def test_throttles_pv_then_min_pv_then_now() -> None:  # Design 6.3
    limits = at_18_05(cars=[A, B, C], wanted_w=10700.0)  # Senkung 5000 W
    assert limits.caps_w == {"c": 0.0, "b": 2760.0, "a": 6240.0}


def test_within_a_level_the_lowest_priority_goes_first() -> None:
    # zwei Autos in Sofort (Rang 0 und 1, je 7000 W Netzanteil), Senkung 2000 → nur Rang 1 bekommt 5000.0


def test_boost_off_and_cars_without_grid_share_stay_free() -> None:
    # Boost 7000/7000, Aus 0/0, PV 3000 mit Netzanteil 0, Sofort 7000/7000; Senkung 2000 → nur Sofort: 5000.0


def test_next_throttling_waits_30_s_and_counts_what_is_pending() -> None:
    # Grenzen wie im ersten Test bei 18:05:00; bei 18:05:29 Senkung 1000 mehr → keine Änderung;
    # bei 18:05:30 mit unveränderter Leistung (ausstehend 5000 W) und Senkung 5000 → keine Änderung;
    # in einem zweiten Lauf bei 18:05:30 mit Senkung 6000 → „a“ sinkt um 1000 auf 5240.0


def test_a_cap_lifts_with_1_kw_headroom_in_reverse_order() -> None:
    # Grenzen wie im ersten Test; P_zul später so, dass P_zul − max(Mittel, Basis + W) = 760 + 1000 →
    # „a“ (Sofort) verliert seine Grenze, „b“ und „c“ behalten sie; 999 W weniger → keine Änderung


def test_quarter_end_lifts_all_caps() -> None:
    # bei 18:15:00 sind caps_w leer


def test_no_start_in_the_last_120_s() -> None:  # Präzisierung 13
    # bei 18:13:00 (t_rest 120 s) mit P_zul − Mittel = 3000 W: no_start == {"a"} (3450 > 3000, „b“ hat 2760);
    # bei 18:12:59 leer; ein Auto in Boost steht nie darin


# tests/unit/core/control/test_charging.py
def test_peak_cap_limits_the_current_with_its_reason(cfg) -> None:
    # e-Golf an der EVCS in Sofort lädt; PeakLimits(caps_w={"egolf": 4600.0}) → 4600 / (230 · 2) = 10 A:
    # ChargeCommand mit Command("wallbox.evcs", CURRENT, 10.0, reasons=("mode_now", "peak_guard")), nicht dringend


def test_peak_cap_below_the_minimum_stops_a_pv_car_by_its_rules(cfg) -> None:
    # PV-Auto lädt mit Überschuss; caps_w 0 → Strom min_a, „Laden aus“ erst nach stop_after_s


def test_no_start_waits(cfg) -> None:
    # e-Golf in Min+PV, angesteckt, lädt nicht: mit no_start {"egolf"} kein „Laden an“; ohne → „Laden an“


def test_boost_ignores_peak_limits(cfg) -> None:
    # Boost mit caps_w und no_start für das Fahrzeug → dieselben Befehle wie ohne


# tests/property/test_peak_guard.py
def test_throttling_keeps_the_order_and_leaves_boost_alone(...) -> None:
    # zufällige Autos (Modus, Rang, Leistung, Netzanteil, Mindestleistung) und Senkung: Eine Grenze bekommt
    # ein Auto einer Stufe nur, wenn jedes Auto einer früheren Stufe mit Netzanteil an seinem Boden oder ohne
    # Netzanteil ist; nie Boost oder Aus; keine Grenze unter dem Boden; die Summe der Senkungen ≤ Senkung
```

`at_18_05(cars, wanted_w)` ist eine Hilfe im Testmodul: Schritte ab 18:00:00 mit Mittel 0, bei 18:05:00 E 1000 Wh und die Autos.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_peak_throttle.py tests/unit/core/control/test_charging.py tests/property/test_peak_guard.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control tests/property -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control tests/unit/core/control tests/property/test_peak_guard.py
git commit -m "feat(core): 15-min-Wächter drosselt die Wallboxen in der Reihenfolge der Modi"
```

---

### Task 11: 15-min-Wächter im Kern und in der Laufzeit

**Files:**
- Modify: `src/bindaems/core/accounting/flows.py`, `src/bindaems/core/control/regulation.py`, `src/bindaems/core/regulation_link.py`, `src/bindaems/core/runtime.py`, `src/bindaems/core/control/context.py`, `src/bindaems/core/api/app.py`
- Test: `tests/unit/core/accounting/test_flows.py`, `tests/unit/core/control/test_regulation.py`, `tests/unit/core/control/test_context.py`, `tests/integration/test_runtime_battery.py`

**Interfaces:**
- Consumes: `PeakGuard`, `PeakLimits`, `NO_LIMITS`, `QuarterObs`, `PeakCar` (Tasks 9, 10), `wb_key`, `allocate`
- Produces:
  - **`flows.py`:**
    - `@dataclass(frozen=True) class SlotProgress`: `slot_start: datetime`, `flows_wh: Mapping[str, float]`, `covered_s: float`
    - `SlotAccumulator.current(self) -> SlotProgress | None`: der laufende Slot bis zum letzten `add`, `None` vor dem ersten
  - **`RegulationInputs.quarter: QuarterObs | None = None`**
  - **`Regulation`:**
    - hält `PeakGuard(cfg.control, cfg.battery.reserve_soc_pct)` und die Grenzen des letzten Zyklus (`NO_LIMITS` nach dem Start und nach `idle`)
    - `step`: Reihenfolge nach Präzisierung 3. `PeakCar` aus `CarView`; der Rang ist die Stelle in `cars()`, der Netzanteil `need.grid_w[wallbox]` (0 ohne Bedarf).
    - `grid_budget_w()` → `budget_w` dieses Zyklus
    - `alarms(wall_now)` → die des Wächters
    - `health()` bekommt `"peak"`, `idle` erreicht den Wächter.
  - **`RegulationLink.step`** bekommt `flows: Mapping[str, float] | None` und `slot: SlotProgress | None` und baut daraus `QuarterObs`:
    - `grid_wh`: Summe der Flüsse `grid>*` des Slots
    - `grid_w`: Summe der Flüsse `grid>*` des Zyklus, `None` ohne `flows`
    - `wallbox_grid_w`: je Wallbox `flows.get(wb_key("grid", w), 0.0)`
    - ohne `slot` ist `quarter` `None`
  - **`runtime.py`:**
    - `_cycle` rechnet `flows = self._flows(derived)` einmal und gibt sie an den Integrator und an `_control`.
    - `_control` übergibt `flows` und `self._accumulator.current()`.
    - `safety_context(..., grid_budget_w=self.regulation.grid_budget_w())`.
    - `health()`: `peak=self.regulation.health()["peak"]`.
  - **`safety_context(..., grid_budget_w: float | None = None)`:** Der Docstring nennt den Wächter als Quelle.
  - **`HealthReport.peak: dict[str, Any] = Field(default_factory=dict)`;** der Docstring nennt die Felder aus Task 9.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/accounting/test_flows.py
def test_current_reports_the_running_slot() -> None:
    acc = SlotAccumulator(counter_signals=[])
    assert acc.current() is None
    acc.add(T0, {"grid>house": 3600.0}, snap({}))
    acc.add(T0 + timedelta(seconds=2), {"grid>house": 3600.0}, snap({}))
    progress = acc.current()
    assert (progress.slot_start, dict(progress.flows_wh), progress.covered_s) == (T0, {"grid>house": 2.0}, 2.0)


# tests/unit/core/control/test_context.py
def test_grid_budget_reaches_the_context() -> None:
    # safety_context(..., grid_budget_w=5700.0).grid_budget_w == 5700.0; ohne Angabe None


# tests/unit/core/control/test_regulation.py
def test_guard_limits_reach_the_charging_one_cycle_later(cfg) -> None:
    # Ziel 8 kW, ab Viertelstundenbeginn Zyklen mit Sofort-Auto: im Zyklus, in dem die Grenze entsteht, noch
    # der alte Strom; im nächsten der gedrosselte mit „peak_guard“


# tests/integration/test_runtime_battery.py
async def test_peak_budget_reaches_the_safety_layer(runtime_env) -> None:
    # control.peak_target_kw 8, e-Golf in Sofort mit 7360 W, SOC 60, Victron im Dry-Run; ab 10:15 UTC
    # (erste ganze Viertelstunde) health().peak["state"] == "active", rt.regulation.grid_budget_w() ist endlich,
    # und der Override der Entscheidungen liegt höchstens bei P_zul


async def test_peak_exceeded_is_an_alarm_in_health(runtime_env) -> None:
    # Victron gesperrt (DESS) und hoher Netzbezug über dem Ziel → nach 60 s nennt health().alarms „peak.exceeded“
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/accounting tests/unit/core/control tests/integration/test_runtime_battery.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest -q`
Expected: PASS. Die Vertragsdateien der UI neu schreiben (`UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py`), dann `cd ui && pnpm check && pnpm test`.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests ui/src/lib/api/contract
git commit -m "feat(core): 15-min-Wächter im Regelzyklus, Budget für Regel 2 und Health"
```

---

### Task 12: Simulator – ESS mit Override, Verfall, DESS und Victron live

**Files:**
- Modify: `src/bindaems/tools/sim/model.py`, `runner.py`, `metrics.py`, `scenarios.py`
- Test: `tests/unit/tools/sim/test_sim_model.py`, `tests/unit/tools/sim/test_sim_runner.py`, `tests/unit/tools/sim/test_sim_metrics.py`

**Interfaces:**
- Consumes: `OVERRIDE_TIMEOUT_S` (Task 5), `rt.regulation` im Lauf (Task 8)
- Produces (Präzisierung 17):
  - **`PlantModel`:**
    - Stellgröße `dess: bool = False`: Der Cerbo meldet `Settings/DynamicEss/Mode` 1 statt 0.
    - **Override:** Ein Schreibauftrag an `SETPOINT_OVERRIDE_PATH` setzt den Override (Zahl) oder hebt ihn auf (`None`), mit eigener Behandlung vor dem allgemeinen Zweig für Einstellungen.
      - Er verfällt `OVERRIDE_TIMEOUT_S` nach dem letzten Schreiben einer Zahl.
      - Mit `dess` ist er aufgehoben, und ein Schreibauftrag wird angenommen, wirkt aber nicht.
      - Der Cerbo meldet den Override unter `SETPOINT_OVERRIDE_PATH`.
    - **ESS:** `total = PV − Lasten + S` mit `S` = Override, sonst `AcPowerSetPoint`; Peak Shaving und Reserve wie bisher.
    - Messgrößen: `override_w: float | None`, `override_age_s: float | None` (Sekunden seit dem letzten Schreiben, solange er gilt), `grid_w` (Summe der Phasen)
  - **`Sample`** bekommt am Ende `grid_w: float = 0.0`, `pv_w: float = 0.0`, `house_w: float = 0.0`, `override_w: float | None = None`, `override_age_s: float | None = None`.
  - **`Scenario`** bekommt am Ende:
    - `victron_live: bool = False`
    - `control: Mapping[str, Any] = MappingProxyType({})`: Werte für `ControlConfig`, etwa `{"peak_target_kw": 8.0}`
  - **`_setup(..., dess: bool = False)`;** neues Ereignis `_Boost(version: int, vehicle: str, until_s: int)`: eine neue Version der Absicht mit `boost_until = start + until_s`, sonst wie `_Override`.
  - **`runner.py`:**
    - `sim_config(cfg, data_dir, *, victron: bool = False, control: Mapping[str, Any] = MappingProxyType({}))`: mit `victron` zusätzlich `victron.live_allowed`. `control` geht über `ControlConfig.model_validate(cfg.control.model_dump() | control)` hinein.
    - `run_scenario` gibt `scenario.victron_live` und `scenario.control` an `sim_config`; `_intent` schaltet mit `victron_live` auch `victron` live.
    - `_sample` füllt die neuen Felder.
  - **`metrics.py`:** `battery_into_car_pct(samples: Sequence[Sample], key: str) -> float | None`. Gezählt wird in allen Sekunden, in denen das Auto `key` lädt, auch am Mindeststrom:
    - Akku ins Auto = `min(Leistung des Autos, max(0, Entladung − max(0, Haus − PV)))`; PV deckt zuerst das Haus, der Akku dann den Rest des Hauses (wie `allocate`). Lädt mehr als ein Auto, bekommt das Auto `key` den Rest nach den Autos vor ihm in `cars`.
    - Übergänge wie bei der Akku-Energie in PV-Autos.
    - Das Ergebnis ist der Anteil an seiner Energie in %, `None` ohne Laden.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/tools/sim/test_sim_model.py
def test_ess_follows_the_override(model) -> None:
    # PV 0, Hauslast 300 W je Phase; mqtt_write(SETPOINT_OVERRIDE_PATH, 3000.0), step → model.grid_w == 3000.0,
    # model.battery_w == 2100.0 (lädt), override_w 3000.0


def test_override_expires_300_s_after_the_last_write(model) -> None:
    # geschrieben bei t0: bei t0+299 noch da, bei t0+300 weg (ESS wieder auf AcPowerSetPoint 0);
    # erneut geschrieben bei t0+240: bei t0+539 noch da; override_age_s zählt ab dem letzten Schreiben


def test_dess_drops_and_ignores_the_override(model) -> None:
    # Override 3000; dess = True, step → override_w None, Topic Settings/DynamicEss/Mode 1;
    # ein neuer Schreibauftrag liefert True und wirkt nicht


# tests/unit/tools/sim/test_sim_runner.py
def test_victron_live_scenarios_allow_and_switch_victron(cfg, tmp_path) -> None:
    # sim_config(cfg, tmp_path, victron=True).victron.live_allowed; _intent mit victron_live nennt "victron": LIVE;
    # ohne victron_live wie bisher


def test_scenario_control_values_reach_the_config(cfg, tmp_path) -> None:
    # sim_config(cfg, tmp_path, control={"peak_target_kw": 8.0}).control.peak_target_kw == 8.0;
    # ein ungültiger Wert ist ein ValidationError


# tests/unit/tools/sim/test_sim_metrics.py
def test_battery_into_car_counts_after_pv_and_house() -> None:
    # PV 500, Haus 900, Entladung 1400, Auto 2000 W → 1000 W Akku ins Auto in der Sekunde
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools/sim -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools tests/integration/test_sim_scenarios.py -q`
Expected: PASS; die zehn Szenarien bleiben grün und unverändert.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests/unit/tools
git commit -m "feat(sim): ESS folgt dem Override, Verfall nach 300 s, DESS und Victron live je Szenario"
```

---

### Task 13: Simulator – Szenarien für Akku und Spitzen

**Files:**
- Modify: `src/bindaems/tools/sim/scenarios.py`, `docs/betrieb.md` (Abschnitt „Simulator“, Tabelle der Szenarien)
- Test: `tests/integration/test_sim_scenarios.py`

**Interfaces:**
- Consumes: Task 12, die bestehenden Prüfungen (`Check`, `Checks`, `_named`, `_no_foreign_alarm`)
- Produces: acht Szenarien (Design 10.2), in `SCENARIOS` nach den zehn bestehenden. Alle haben `live=True` und `victron_live=True`, außer wo die Tabelle es anders sagt; Hauslast und Rauschen wie bisher.

| Name | Titel | Start, Dauer, Saat | Aufbau und Absicht | Prüfungen |
|---|---|---|---|---|
| `akku-wolkig` | PV unter Wolken, SOC 60 % | `SUMMER`, 1 h, 11 | SOC 60, Tesla abgesteckt, e-Golf an der EVCS in PV; Wolken wie `wolkig` | `battery_into_car_pct(…, "egolf")` ≤ 5 %; mindestens eine Entscheidung an `victron` mit `battery_comp` |
| `akku-minpv` | Min+PV ohne Sonne, SOC 80 % | `EVENING`, 30 min, 12 | SOC 80, beide angesteckt, beide in Min+PV, keine PV | Akku in die Autos (`max(0, Entladung − Haus)`): ab 10 s nach dem ersten Laden bis 890 s danach höchstens 3200 W, ab 930 s danach höchstens 200 W |
| `akku-sofort` | Sofort am Abend, beide Autos | `EVENING`, 30 min, 13 | SOC 60, beide angesteckt, beide in Sofort | ab 2 min: In jeder Sekunde, in der Autos und Haus mehr als 8300 W ziehen, liegt der Netzbezug zwischen 7800 und 8200 W |
| `boost-abend` | Boost am Abend | `EVENING`, 45 min, 14 | SOC 35, Tesla abgesteckt, e-Golf an der EVCS mit Boost bis zum Ende | keine Entscheidung an `victron` mit einer Zahl; solange der SOC über 25 % liegt, ab 2 min Netzbezug ≤ 300 W; SOC nie unter 20 % |
| `override-stunde` | Eine Stunde mit aktivem Override | `EVENING`, 1 h, 15 | SOC 50, Tesla abgesteckt, e-Golf an der EVCS in Sofort | ab 2 min in jeder Probe ein Override, `override_age_s` ≤ 245; kein Alarm `competitor.foreign.*`; höchstens 5 Entscheidungen an `victron` |
| `dess-an` | Dynamic ESS an | `EVENING`, 30 min, 16 | SOC 60, `dess`, Tesla abgesteckt, e-Golf an der EVCS in Sofort | keine Entscheidung an `victron`; ab 1 min `battery.state` `locked` in jeder Gesundheit; der e-Golf lädt am Ende |
| `spitze-15min` | 15-min-Ziel 8 kW | `EVENING − 60 s`, 75 min, 17 | SOC 60, Tesla in Min+PV, e-Golf in Sofort, `control={"peak_target_kw": 8.0}`; ab 46 min (18:45) Boost für den e-Golf bis zum Ende (`_Boost(2, "egolf", …)`) | Netzbezug der Viertelstunden 18:00, 18:15 und 18:30 je ≤ 2,02 kWh; mindestens eine Entscheidung mit `peak_guard` vor 18:45; keine mit `peak_guard` an die EVCS ab 18:45; bei der ersten Drosselung eines Autos in Sofort lädt kein Auto in PV-Überschuss oder Min+PV über Mindeststrom + 0,5 A |
| `schieflast-pv` | PV mit schwerer Last auf L1 | `SUMMER`, 1 h, 18 | SOC 60, Tesla abgesteckt, e-Golf an der EVCS in PV; PV 12 kW konstant, 5,5 kW mehr auf L1 | mindestens eine Entscheidung an die EVCS mit `import_limit`; dazu die Kennzahlen wie überall |

- Rechnerisch begrenzt in `schieflast-pv` das Importziel auf L1 den e-Golf zwischen Mindest- und Höchststrom (ohne Rauschen etwa 9 A). Der Netzbezug einer Viertelstunde ist `Σ max(0, grid_w) · 1 s`.
- Scheitert ein Szenario, liegt der Fehler zuerst in der Regelung: ein eigener `fix(core)`-Commit mit Test. Ein Szenario ändert man nur, wenn es physikalisch nicht zu schaffen ist; dann steht die Abweichung im Docstring des Moduls wie die von 2c-1.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/integration/test_sim_scenarios.py
NEW = ["akku-wolkig", "akku-minpv", "akku-sofort", "boost-abend", "override-stunde", "dess-an",
       "spitze-15min", "schieflast-pv"]


def test_battery_and_peak_scenarios_are_registered() -> None:
    assert list(SCENARIOS)[-8:] == NEW
    assert all(SCENARIOS[name].victron_live for name in NEW)
    assert not any(SCENARIOS[name].victron_live for name in list(SCENARIOS)[:10])
```

Die bestehende Parametrisierung über `SCENARIOS` läuft die neuen Szenarien mit und verlangt `result.failures == []`.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/integration/test_sim_scenarios.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen:** die Szenarien und ihre Prüfungen; die Tabelle der Szenarien im Betriebshandbuch („Simulator“) um die acht ergänzen (`tests/unit/test_docs.py` vergleicht sie mit `SCENARIOS`).

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/integration/test_sim_scenarios.py tests/unit/test_docs.py -q`, dann `uv run python -m bindaems.tools.sim --szenario spitze-15min --out /tmp/spitze.md` und den Bericht lesen
Expected: PASS; alle 18 Szenarien grün.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools/sim/scenarios.py tests/integration/test_sim_scenarios.py docs/betrieb.md
git commit -m "test(sim): Szenarien für Akku-Sollwert, Auffrischen, DESS und 15-min-Wächter"
```

---

### Task 14: Doku, Spec und Abdeckung

**Files:**
- Modify: `docs/betrieb.md`, `docs/superpowers/specs/2026-10-08-bindaems-design.md`, `docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md` (Stand), `docs/uebergabe.md`
- Test: `tests/unit/test_docs.py`

**Interfaces:**
- Consumes: alles Vorige
- Produces:
  - **Betriebshandbuch**, neuer Abschnitt `## Akku und Spitzen` nach „Laderegelung“:
    - **Akku-Sollwert:** Grundsatz, Modi, Unterstützung, Totband, Auffrischen und Sperren in Sätzen für den Betreiber
    - **15-min-Wächter:** Ziel einstellen und abschalten, Drosselung, Akku, Warnung
    - **Health:** `battery` und `peak` mit allen Werten
    - **Gründe:** eine Tabelle wie in „Laderegelung“ (`| \`code\` | Text | Bedeutung |`) mit den drei Gründen aus „Texte“
    - **Victron in den Dry-Run und live:** Design 11.1 als Schritte:
      - DESS aus für den Dry-Run
      - Prüfprotokoll Teil 2, Victron 17.2-4
      - Watchdog-Probe mit vorher verstellten Victron-Werten
      - `victron.live_allowed`
    - **Alarme** `control.exception` und `peak.exceeded` mit Abhilfe
    - Keines der neuen Felder verlangt eine neue `config.ini` des Watchdogs (Design 3.2).
  - **`tests/unit/test_docs.py`:** `BATTERY_REASONS = {"battery_comp", "battery_support", "peak_guard"}` und ein Test wie `test_reason_codes_in_the_guide_match_the_decision_log` für den Abschnitt „Akku und Spitzen“
  - **Spec:**
    - Absätze „Stand Plan 2c-2a“ in 7.1 (Kern, Reihenfolge), 7.4 (Budget = Zielwert, Präzisierungen 9 bis 14), 7.6 und 7.7 (Mechanismus, Boost, Auffrischen) und 7.10 (Auffrischen nicht im Log)
    - Absätze „Stand Plan 2c-2a“ in 8.3 (Regel 2 mit Budget nur über der Schwelle, Regel 5 mit `refresh`) und 18 (Victron nach 2c-2a)
    - Die Abweichungen aus Design 11.2 und den Präzisierungen 7, 10 und 12 nennt sie ausdrücklich.
  - **Design 2c-2:** Kopf „Stand: freigegeben am 14.10.2026; Plan 2c-2a umgesetzt (Abschnitte 1 bis 6, 8 bis 11), Abschnitt 7 folgt mit Plan 2c-2b“
  - **Übergabe:**
    - Kurzfassung und Stand
    - die Liste „Vor der Live-Schaltung“: Punkte 4, 6 und 7 erledigt
    - Inbetriebnahme von Victron (Design 11.1)
    - nächster Schritt Plan 2c-2b
    - Testzahl, Rulings und aufgeschobene Kleinigkeiten der Phase

- [ ] **Step 1: Failing Test schreiben**

```python
# tests/unit/test_docs.py
BATTERY_REASONS = {"battery_comp", "battery_support", "peak_guard"}


def test_operations_guide_describes_battery_and_peak() -> None:
    text = Path("docs/betrieb.md").read_text(encoding="utf-8")
    for topic in ("## Akku und Spitzen", '"battery"', '"peak"', "control.exception", "peak.exceeded",
                  "victron.live_allowed", "peak_target_kw"):
        assert topic in text


def test_battery_reasons_in_the_guide_match_the_decision_log() -> None:
    # wie test_reason_codes_in_the_guide_match_the_decision_log, im Abschnitt „Akku und Spitzen“ mit BATTERY_REASONS
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
git commit -m "docs: Akku-Sollwert und 15-min-Wächter in Betriebshandbuch, Spec und Übergabe"
```

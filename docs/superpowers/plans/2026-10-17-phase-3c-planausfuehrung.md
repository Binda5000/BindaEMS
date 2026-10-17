# Phase 3c – Planausführung im core: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der core führt den Akkuplan der app aus:
- Der Plan reist als Teil der Absicht. Der core prüft ihn, klemmt ihn und achtet auf seine Gültigkeit.
- Die Absichten `EIGENVERBRAUCH`, `HALTEN` und `NETZLADEN(P)` laufen über den vorhandenen Netz-Sollwert-Override.
- Das Budget des 15-min-Wächters kommt aus der akzeptierten Monatsspitze des Plans.
- Die Akku-Unterstützung hat eine Plan-Schwelle, einen Schalter „Akku-Unterstützung erlaubt“ und eine Zahl „Unterstützungsschwelle“ in UI und HA.
- Preis und Plan-Slot stehen in den Entscheidungen.
- Der Simulator rechnet mit Wirkungsgrad und hat Plan-Szenarien.
- Der Zeitplan zeigt „Plan gegen Ist“ und den Stand der Ausführung statt „Vorschau“.
- Beim Boost zeigt die Seite „Laden“ die Mehrkosten.

**Architecture:** 3c ändert core, app, `shared`, UI und Simulator.
- **`shared/plan.py`:** `IntentPlan` und `IntentPlanSlot` sind der schlanke Teil des Plans, den der core bekommt. `intent_plan(plan, grid_charge_allowed)` baut ihn. `PlanSlot` bekommt die Plan-Schwelle `support_soc_pct`.
- **`shared/control.py`:** `Intent.plan` (optional) und `Intent.support` (`SupportIntent`).
- **core:**
  - `core/control/intent.py` prüft und klemmt den Plan.
  - `core/control/plan_exec.py` (neu) bestimmt den gültigen Slot (`ActiveSlot`).
  - `core/control/battery.py` setzt die Absicht um.
  - `core/control/peak_guard.py` nimmt das Ziel je Schritt.
  - `core/control/regulation.py` verdrahtet das, mit Health `plan` und den Eingaben der Entscheidungen.
- **app:**
  - `app/control/intent.py` hat `SetPlan` (ohne Änderungsprotokoll) und `SetSupport`.
  - `app/planning/sender.py` (neu) bringt den Plan zum core.
  - `app/planning/execution.py` (neu) leitet den Stand der Ausführung ab.
  - `app/planning/review.py` (neu) baut „Plan gegen Ist“.
  - `app/control/boost_cost.py` (neu) rechnet die Mehrkosten des Boosts.
  - HA bekommt die Komponente `number`.
- **UI:** Karte „Akku-Unterstützung“ auf „Laden“, Mehrkosten in der Fahrzeugkarte, Stand der Ausführung im Kopf des Zeitplans und Karte „Plan gegen Ist“.
- **Simulator:** Wirkungsgrad im Anlagenmodell, Ereignis `_Plan`, fünf Plan-Szenarien.

**Tech Stack:** wie Plan 3b. Keine neuen Abhängigkeiten, keine Migration, keine neuen Schlüssel in `config.yaml`.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, vor allem 5.3, 7.1, 7.4, 7.6, 7.7, 7.10, 8.3, 9.3 („Ausgabe“), 9.6, 12, 13, 16 und 18.
- Verweise wie „Spec 7.7“ beziehen sich darauf.
- Die Absätze „Stand Plan …“ der Spec gehen dem älteren Text davor vor; die Präzisierungen unten gehen beiden vor.
- Vorgänger ist Plan 3b (`docs/superpowers/plans/2026-10-16-phase-3b-optimierer-zeitplan.md`, auf `main` seit `6a6904a`). Seine Global Constraints und die der Pläne davor gelten weiter, ebenso die Vorab-Entscheidungen für 3b und 3c (Plan 3a, Entscheidung 6) und die Entscheidung des Betreibers vom 14. 10. 2026, dass Boost aus dem Akku laden darf (im Eigenverbrauch).

## Einordnung

3c ist der letzte der drei Pläne der Phase 3 (Tabelle in Spec 18, „Stand Plan 3a“).
- **Ergebnis an der Anlage:** Mit Regelbetrieb rechnet der core den Plan im Dry-Run mit; er entscheidet je Zyklus, was er an Victron senden würde. Live geht Victron erst, wenn ein Admin es schaltet, nach den vier Bedingungen aus Spec 8.7.
- **Danach** folgen 2 Wochen Dry-Run der Planausführung (DESS dafür aus), dann Victron live und die Abnahme der Phase 3 mit den 30 echten Tagen.
- **Unabhängig von den 30 Tagen:** 3c braucht sie nicht. Sie prüfen den Planer aus 3b. Ein Befund dort ändert den Planer, nicht die Ausführung.
- **Stand laut Übergabe:** 2d, 3a und 3b sind an der Anlage noch nicht ausgerollt, Victron ist nicht live.

## Vorgeschlagene Entscheidungen (zur Freigabe)

Der Plan ist auf diese Vorschläge geschrieben; eine andere Wahl ändert die genannten Tasks.

1. **Plan in der Absicht** (Spec 5.3; Tasks 1, 2, 10).
   - Die app schickt nur den bindenden Teil des Plans: die Slots vor `valid_until`, höchstens 8. Je Slot gehen Absicht, Budget, Preis und Plan-Schwelle mit, dazu `id`, `created_at`, `valid_until` und `grid_charge_allowed`. Das sind rund 2 KB statt 66 KB.
   - Jeder neue Plan ist eine neue Version der Absicht. Bedienungen behalten den Plan.
   - Ein Plan erzeugt keinen Eintrag im Änderungsprotokoll, sonst wären es rund 100 am Tag; der Nachweis ist das Plan-Archiv der app.
   - Alternative: ein eigener Endpunkt `PUT /v1/plan`. Er wiche von Spec 5.3 ab und bräuchte eine eigene Speicherung im core.
2. **Absichten über den vorhandenen Override** (Spec 7.7, Plan 3a Entscheidung 6; Task 4). Der gewollte Netzbezug über der Basis ist:
   - **`EIGENVERBRAUCH`:** wie heute die Kompensation der Autos aus 7.6.
   - **`HALTEN`:** `max(0, Hauslast + Autos − PV − Basis)`. Der Akku gibt nichts ab, und ein PV-Überschuss lädt ihn weiter.
   - **`NETZLADEN(P)`:** dasselbe plus `P`. Das entspricht dem Akkumodell der Vorschau.
   - **Für alle drei** gelten danach wie heute das Budget des Wächters, das Raster von 10 W, das Totband von 200 W, das Auffrischen und Regel 2.
   - **Folge des Totbands:** Unter 200 W Fehlbetrag entlädt der Akku auch bei `HALTEN` etwas, höchstens rund 0,05 kWh je Viertelstunde.
   - **Alternative:** der flüchtige Override `ess.override.maxdischargepower`. Er ist an der Anlage nicht geprüft.
3. **Boost bei `HALTEN` und `NETZLADEN` lädt aus dem Netz** (Task 4). Die Entladesperre gilt für alles. Im Eigenverbrauch darf Boost weiter aus dem Akku laden. Alternative: Boost zieht immer aus dem Akku; dann leert er den gehaltenen Akku vor der teuren Zeit.
4. **Prüfung und Gültigkeit im core** (Tasks 2, 3).
   - **Gültigkeit:** Der core prüft gegen seine eigene Uhr. Ohne gültigen Plan gilt `EIGENVERBRAUCH`.
   - **Klemmen:** `valid_until` höchstens 45 min nach der Annahme, `P` höchstens `victron.max_grid_charge_setpoint_w`.
   - **Ablehnen (422):** ein Plan mit Lücken, falschem Raster oder Netzladen trotz ausgeschaltetem Schalter.
   - **Abgelaufene Pläne** nimmt der core an, sie wirken nicht. So scheitert keine Bedienung an einem alten Plan.
5. **Budget** (Spec 7.4; Task 6).
   - Hat der aktuelle Slot ein `budget_kw`, ersetzt es `control.peak_target_kw`; sonst gilt wie bisher `peak_target_kw`.
   - Eine Überschreitung warnt weiter nur (`peak.exceeded`). Der nächste Plan sieht die neue Monatsspitze (Auslöser `month_peak`) und hebt das Budget.
   - Bis Sätze für den Leistungspreis eingetragen sind (frühestens 2027), ändert sich nichts.
6. **Plan-Schwelle der Unterstützung** (Spec 7.6; Tasks 5, 9).
   - Die Plan-Schwelle ist je Slot der SOC, den der Plan für seine späteren Entladungen noch braucht, rückwärts über den Horizont gerechnet (Präzisierung 6).
   - Die wirksame Schwelle ist das Maximum aus Unterstützungsschwelle und Plan-Schwelle.
   - Bei `HALTEN` und `NETZLADEN` gibt der Akku ohnehin nichts ab.
   - Liegt die Plan-Schwelle über der Unterstützungsschwelle, erklärt der neue Grundcode `UNTERSTUETZUNG_RESERVIERT` das.
   - Alternative: die Unterstützung im MILP optimieren. Das braucht die Autos im Planer (Phase 4).
7. **„Akku-Unterstützung erlaubt“ und „Unterstützungsschwelle“** gehören zur Absicht, wie der Gast-Modus (Tasks 1, 12, 13).
   - Bedienen dürfen „Bedienen“ und Admin, auf der Seite „Laden“ und in HA, jeweils mit Eintrag im Änderungsprotokoll.
   - Standard: erlaubt, Schwelle aus `control.support_soc_pct` (70 %). Bereich: USV-Reserve bis 100 %.
   - Alternative: Laufzeit-Einstellungen. Sie wirkten erst mit dem nächsten Plan und ohne Plan gar nicht.
8. **Übergabe des Plans** (Task 10).
   - Die app schickt einen Plan nach jedem Planungslauf, nach jeder neuen Verbindung zum core und alle 30 s, solange der core nicht den neuesten gültigen Plan hat.
   - Schaltet jemand „Netzladen erlaubt“ aus, geht sofort ein Plan ohne `NETZLADEN` hinaus; diese Slots werden zu `EIGENVERBRAUCH`. Der neue Plan folgt.
9. **Entscheidungslog** (Spec 7.10; Tasks 4, 7).
   - Neue Gründe `plan_hold` („Plan: Halten“) und `plan_grid_charge` („Plan: Netzladen“).
   - Neue Eingaben `price_ct`, `plan_slot_ts`, `plan_grid_charge_w` und `budget_kw`. Sie stehen nur da, wenn es den Wert gibt; ohne Plan bleiben die Entscheidungen wie heute.
10. **Stand der Ausführung statt „Vorschau“** (Task 11). Zeitplan und HA zeigen einen von sieben Ständen: core nicht erreichbar, noch nicht beim core, Regelbetrieb aus, core regelt nicht, Victron gesperrt, Dry-Run, ausgeführt. In HA ersetzt das Attribut `execution` das Attribut `preview: true`.
11. **„Plan gegen Ist“** (Tasks 14, 15). Eine Karte im Zeitplan vergleicht für heute bis jetzt je Viertelstunde den Slot des damals gültigen Plans mit der Abrechnung, dazu den SOC aus dem Verlauf.
    - Im Diagramm stehen geplanter SOC und Ist-SOC.
    - Die Tabelle summiert Netzbezug, Einspeisung, Netzladen und Bezugskosten nach Plan, Ist und Differenz.
12. **Mehrkosten beim Boost** (Spec 7.4: „Zusatz-kW × Monatssatz“; Task 16).
    - Die Seite „Laden“ zeigt „bis zu {€} € Leistungspreis im Monat“. Gerechnet wird der Leistungspreis für Monatsspitze plus volle Ladeleistung des Autos minus den für die Monatsspitze allein.
    - Das erscheint nur, wenn im Monat ein Leistungspreis mit Satz gilt.
13. **Simulator mit Wirkungsgrad** (Task 8). Alle Szenarien rechnen mit 88 % Roundtrip (`√0,88` je Richtung); das Modell selbst bleibt ohne Angabe verlustfrei. Dazu kommen fünf Plan-Szenarien: `plan-halten`, `plan-netzladen`, `plan-abgelaufen`, `plan-budget` und `plan-boost`.
14. **Nichts geht von selbst live.** Der Plan wirkt nur mit Regelbetrieb und nur in der Betriebsart, die Victron hat. Live bleibt hinter den vier Bedingungen aus Spec 8.7.

## Präzisierungen gegenüber der Spec

1. **Zuschnitt.** Aus Spec 18, Zeile 3c, kommt alles in 3c.
   - **Nicht in 3c:** Regel R2, die Autos im Planer und ihre Auslöser (Phase 4); der flüchtige Override der Entladegrenze; ein Totband in der Drosselung des Wächters (offen aus 2c-2a).
   - **„Akku → Auto“** (Spec 18, „Weiterhin offen für 3c“) ist die Plan-Schwelle aus Entscheidung 6; eine Optimierung der Unterstützung kommt mit den Autos in Phase 4.
2. **`IntentPlan`** (`shared/plan.py`): `id: int`, `created_at`, `valid_until` (beide `AwareDatetime`), `grid_charge_allowed: bool`, `slots: list[IntentPlanSlot]` (1 bis `MAX_INTENT_PLAN_SLOTS = 8`). `IntentPlanSlot`: `start`, `intent: BatteryIntent`, `budget_kw: Finite | None` (≥ 0), `import_ct: Finite | None`, `support_soc_pct: Finite | None` (0 bis 100).
   - **`intent_plan(plan, *, grid_charge_allowed)`** nimmt die Slots mit `start < valid_until`. Bei `grid_charge_allowed` falsch werden `NETZLADEN`-Slots zu `EIGENVERBRAUCH`.
   - **Methoden:** `valid_at(ts)` (`created_at <= ts < valid_until`) und `slot_at(ts)` wie bei `Plan`.
3. **`SupportIntent`** (`shared/control.py`): `allowed: bool = True`, `soc_pct: float | None = None` (0 bis 100, `None` = `control.support_soc_pct`). Die Absicht bekommt `plan: IntentPlan | None = None` und `support: SupportIntent = SupportIntent()`. Gespeicherte Absichten ohne die Felder laden mit den Standardwerten.
4. **Prüfung im core** (`IntentStore`):
   - **Abgelehnt als `invalid`, wenn:**
     - die Slots nicht auf dem 15-min-Raster liegen, nicht lückenlos aufsteigen oder mehr als 8 sind;
     - nicht `slots[0].start <= created_at < valid_until <= slots[-1].start + SLOT` gilt;
     - `valid_until − created_at > PLAN_VALID` ist;
     - ein `NETZLADEN`-Slot in einem Plan mit `grid_charge_allowed` falsch steht;
     - `support.soc_pct` unter `battery.reserve_soc_pct` liegt.
   - **Geklemmt** (`clamped`, mit Hinweis):
     - `valid_until` auf `received + PLAN_VALID`, wobei `received` wie beim Boost die Annahme der Version ist;
     - `grid_charge_w` auf `victron.max_grid_charge_setpoint_w`.
   - **Abgelaufen:** Ein Plan, der bei der Annahme schon abgelaufen ist, gilt als angenommen und wirkt nicht.
5. **Gültiger Slot im core:** `active_slot(plan, wall_now) -> ActiveSlot | None`. Er liefert nur bei `plan.valid_at(wall_now)` den Slot mit `slot_start(wall_now)`, auf der Wanduhr wie der Wächter (`charge.wall_now`).
   - **Health** `plan`: `id`, `state` (`none`, `valid`, `expired`), `valid_until`, `slot` (Beginn, ISO), `intent`, `grid_charge_w`, `budget_kw`, `support_soc_pct`, `price_ct`; was fehlt, ist `null`.
6. **Plan-Schwelle** (app, `support_thresholds`):
   - Mit `d_t = soc_start_pct_t − soc_end_pct_t` (Entladung in Prozentpunkten aus der Nachrechnung) gilt rückwärts `R_N = 0` und `R_t = max(0, d_t + R_{t+1})`.
   - Die Schwelle ist `min(100, ceil(reserve_pct + R_t − 1e-9))`.
   - **Grundcode `UNTERSTUETZUNG_RESERVIERT`** mit Parametern `soc` und `um`. Er kommt nach dem Hauptcode und vor `GESCHAETZT`, in `EIGENVERBRAUCH`-Slots, deren Schwelle über der konfigurierten Unterstützungsschwelle (`PlanInputs.support_soc_pct`) liegt. `um` ist der Beginn des ersten späteren Slots mit `R = 0`, sonst das Horizontende.
7. **Akku-Sollwert** (`BatteryController.decide` bekommt `active: ActiveSlot | None`). `W_ev` ist der heutige gewollte Netzbezug (Summe der Fehlbeträge minus erlaubter Anteil, 7.6).
   - **`HALTEN`:** `W = max(0, house_w + cars_w − pv_w − base_w)`.
   - **`NETZLADEN(P)`:** `W = max(0, house_w + cars_w − pv_w − base_w) + P`.
   - **`EIGENVERBRAUCH` oder kein Slot:** `W = W_ev`.
   - **Wie heute:** Danach gelten `_guarded`, Raster, Totband, Auffrischen, Sperren und Health unverändert.
   - **Gründe:** `plan_hold` bzw. `plan_grid_charge`, dahinter `peak_guard`, wenn das Budget gesenkt hat.
   - **Der Wächter** (`peak_wanted_w`) bekommt dieses `W`.
8. **Unterstützung:**
   - **Wirksame Schwelle:** `max(intent.support.soc_pct oder control.support_soc_pct, active.support_soc_pct oder 0)`. Die Hysterese gilt gegen die wirksame Schwelle des Zyklus.
   - **Gesperrt** ist sie bei `support.allowed` falsch (Grund `off`) und in `HALTEN` und `NETZLADEN` (Grund `plan`).
   - **Health** `battery` bekommt `support_soc_pct` (wirksam) und `support_source` (`config`, `intent`, `plan`).
9. **Wächter:** `PeakGuard.step` bekommt `target_kw: float | None`. Das ist `active.budget_kw`, wenn es eine Zahl ist, sonst `control.peak_target_kw`. Health `peak.target_kw` und der Stand `off` folgen dem Ziel des letzten Schritts.
10. **Eingaben der Entscheidungen:** Gibt es einen gültigen Slot, bekommen alle Entscheidungen eines Regelzyklus zusätzlich:
    - `plan_slot_ts`: Beginn in Sekunden seit 1970;
    - `price_ct`: `import_ct`, nur wenn bekannt;
    - `budget_kw`: das Ziel des Wächters, nur wenn gesetzt.

    Die Entscheidungen an Victron bekommen zusätzlich `plan_grid_charge_w`: `P`, sonst 0. Ohne gültigen Slot kommen keine neuen Schlüssel dazu.
11. **Übergabe** (`PlanSender`):
    - **Gewollter Plan:** `intent_plan(neuester gültiger Plan, grid_charge_allowed = aktuelle Einstellung)`. Der core hat ihn, wenn `(id, grid_charge_allowed)` des Plans in `live.health["intent"]["plan"]` gleich sind.
    - **Senden:** über `IntentService.apply(SetPlan(…), actor="planung", source="system")`, ohne Eintrag im Änderungsprotokoll.
    - **Anstöße:** nach `PlanningService._run`, nach jeder Verbindung (`LiveFeed.connect_handlers`), nach jeder Änderung der Einstellungen und alle 30 s.
    - **Fehler** (`CoreDown`, `Rejected`, `ConflictAgain`) werden geloggt; der nächste Anstoß versucht es neu. Ohne gültigen Plan schickt die app nichts.
12. **Stand der Ausführung** (`execution_state(plan, valid=…, connected=…, health=…, grid_charge_allowed=…)`), der erste zutreffende in dieser Reihenfolge:
    1. `none`: kein gültiger Plan;
    2. `unreachable`: nicht verbunden;
    3. `pending`: der core hat diesen Plan nicht (Präzisierung 11);
    4. `off`: `intent.control` falsch;
    5. `idle`: `mode` nicht `CONTROL` oder `DEGRADED`;
    6. `locked`: Aktor `victron` mit Sperrgründen;
    7. `dry_run`: wirksame Betriebsart von `victron` nicht `live`;
    8. sonst `live`.
13. **„Plan gegen Ist“** (`GET /api/plan/review?day=YYYY-MM-DD`, Standard heute, lokal; „Betrachter“).
    - **Welche Slots:** die vergangenen Viertelstunden des Tages (`start + SLOT <= jetzt`).
    - **„Geplant“:** der Slot `start` aus dem neuesten Plan mit `created_at < start + SLOT` und `valid_until > start`, der den Slot enthält.
    - **„Ist“:** aus `ledger_slot`. Netzbezug und Einspeisung sind `import_wh · 4` bzw. `export_wh · 4` W. Für die Akkuleistung zählen die Flüsse in den Akku positiv, die aus dem Akku negativ, mal 4; Netzladen ist `grid>battery · 4`. Die Bezugskosten sind `import_wh / 1000 · import_price_ct`, ohne Preis `null`.
    - **Summen** nur über Slots mit beidem.
    - **Grenzen:** Ein Tag außerhalb der letzten 30 Tage oder in der Zukunft ergibt 422.
    - **SOC:** Den Ist-SOC holt das UI über `GET /api/history` (`soc.battery`).
14. **Mehrkosten beim Boost** (`GET /api/control/boost-cost`, „Betrachter“).
    - **Monatsspitze:** `peak = max(Monatsspitze laut Abrechnung, minimum_kw)`.
    - **Je Fahrzeug:** `extra_kw = max_a · phases · grid.voltage_nominal_v / 1000` und `cost_eur = power_cost_eur(peak + extra_kw) − power_cost_eur(peak)` mit dem Satz des aktuellen lokalen Monats.
    - **Ohne Satz** in diesem Monat ist `applies` falsch, und die Fahrzeuge fehlen.
15. **Simulator:**
    - **Modell:** `PlantModel(cfg, rng, *, roundtrip_pct=100.0)`. Laden zählt `× η`, Entladen `÷ η`, mit `η = √(roundtrip_pct/100)`, auch für die Reserve-Grenze.
    - **Szenario:** Das Feld `Scenario.roundtrip_pct: float = 88.0` geht an das Modell.
    - **Plan-Ereignis:** `_Plan(slots, *, valid_s, grid_charge_allowed=True)` reicht zur Laufzeit eine Absicht mit Plan ein; `id` ist die Sekunde des Ereignisses.

## Global Constraints

- **Gilt weiter:** alle Global Constraints aus 1a bis 3b, auch „Jede Task lässt das ganze Repository grün, auch den UI-Vertragstest und `tests/unit/test_docs.py`“.
- **Grenzen der Importe:** `shared/plan.py` importiert nur `pydantic` und `bindaems.shared`. Der core importiert nichts aus `app`. Der core kennt keine Preise außer `IntentPlanSlot.import_ct`.
- **Zeit:** Der core prüft Gültigkeit und Slot auf der Wanduhr (`charge.wall_now` bzw. `self._clock.now()`). Die app nimmt ihre `Clock`. Tage mit 92 und 100 Slots müssen funktionieren.
- **Sicherheit:** Die Safety-Schicht bleibt unverändert und begrenzt jeden Sollwert (Regel 2, Regel 4). Kein neuer Schreibweg an Victron. Kein Code schaltet einen Aktor live.
- **Absicht:** Neue Felder haben Standardwerte. `tests/helpers.py::INTENT_CHARGING_DEFAULTS` bekommt `"plan": None` und `"support": {"allowed": True, "soc_pct": None}`.
- **Entscheidungen:** `inputs` bleiben Zahlen (`Mapping[str, float | None]`).
- **Dokumentation unter Testzwang:** neue Gründe in `BATTERY_REASONS`, neue HA-Objekte und Plattformen, die Szenarienliste mit Zahlwort und der Satz zur Ausführung stehen in `tests/unit/test_docs.py` und `docs/betrieb.md` und ändern sich in derselben Task.
- **UI:** wie in 3b (44 × 44 px, keine waagrechte Scrollleiste bei 360 px, valibot-Schema je Antwort, Vertragsdateien in `GET_ENDPOINTS`/`OTHER` und `SCHEMAS`, Texte über `format.ts`).
- **Texte:** exakt wie in „Texte“; Tests prüfen den Wortlaut.
- **Öffentliches Repository:** keine Daten der Anlage; Tarifwerte in Tests sind erfundene Beispiele.

## Review Focus

1. **Pläne alle 15 min neben Bedienungen.**
   - Erwartet:
     - Jede neue Version bindet Boost und Übersteuerung wie bisher; ein laufender Boost bleibt, ein verbrauchter springt nicht an.
     - Eine Bedienung mit einem abgelaufenen Plan in der Absicht geht durch.
     - Ein 409 zwischen Plan und Bedienung wiederholt sich genau einmal.
   - Tests: Task 2 `test_expired_plan_is_accepted_and_inert`, Task 10 `test_operator_change_keeps_the_plan` und `test_plan_send_retries_once_on_conflict`, Task 8 Szenario `plan-boost`.
2. **Neustart des core mit gespeichertem Plan.**
   - Erwartet: Bis `valid_until` führt der core ihn aus, danach `EIGENVERBRAUCH`, ohne Fehler beim Laden.
   - Tests: Task 3 `test_restart_uses_the_saved_plan_until_it_expires` (Integration), Task 2 `test_saved_intent_without_plan_loads_with_defaults`.
3. **Späte oder schiefe Pläne** (Uhr der app vorgestellt, Plan erst nach seinem Ablauf zugestellt).
   - Erwartet: `valid_until` geklemmt, abgelaufener Plan ohne Wirkung, kein Slot außerhalb der Gültigkeit.
   - Tests: Task 2 `test_valid_until_is_clamped_to_45_min_after_acceptance`, Task 3 `test_no_slot_outside_validity`.
4. **Großer Lastsprung bei `HALTEN`** (Sofort-Auto 11 kW plus Haus).
   - Erwartet: Regel 2 klemmt auf 8 kW, der Akku liefert nur den Rest, die Zahl der Entscheidungen bleibt durch das Totband begrenzt.
   - Tests: Task 4 `test_hold_above_rule_2_is_clamped_downstream`, Task 8 `plan-halten` (höchstens 40 Entscheidungen an Victron je Stunde).
5. **„Netzladen erlaubt“ aus während eines `NETZLADEN`-Slots.**
   - Erwartet: Der nächste Anstoß schickt den Plan ohne `NETZLADEN`, ohne auf den neuen Plan zu warten; der core lehnt einen Plan mit `NETZLADEN` bei ausgeschaltetem Schalter ab.
   - Tests: Task 10 `test_switching_grid_charge_off_sends_the_plan_without_grid_charge`, Task 2 `test_grid_charge_slot_with_switch_off_is_invalid`.

## Texte

- **Gründe der Entscheidungen:** `plan_hold` „Plan: Halten“, `plan_grid_charge` „Plan: Netzladen“.
- **Klemmhinweise des core:** „Plan bis {HH:MM} begrenzt.“ (Wiener Zeit), „Netzladen auf {W} W begrenzt (maximaler Netzladesetpoint).“
- **Ablehnungen des core:**
  - „Plan: Slots nicht im Viertelstundenraster oder nicht lückenlos.“
  - „Plan: zu viele Slots (höchstens 8).“
  - „Plan: Gültigkeit passt nicht zu den Slots.“
  - „Plan: Netzladen, obwohl ausgeschaltet.“
  - „Unterstützungsschwelle {soc} % liegt unter der USV-Reserve ({reserve} %).“
- **Grundcode:** `UNTERSTUETZUNG_RESERVIERT`: „Akku-Unterstützung fürs Auto erst ab {soc} %, weil der Plan den Akku bis {um} Uhr braucht.“
- **Stand der Ausführung** (Zeitplan-Kopf; `none` zeigt nichts):
  - `unreachable` „Der core ist nicht erreichbar; er führt den Plan nicht aus.“
  - `pending` „Der Plan ist noch nicht beim core.“
  - `off` „Regelbetrieb ist aus: Der core führt den Plan nicht aus.“
  - `idle` „Der core regelt gerade nicht ({Betriebszustand}).“
  - `locked` „Victron ist gesperrt: Der core führt den Plan nicht aus.“
  - `dry_run` „Dry-Run: Der core rechnet den Plan mit und sendet nichts an Victron.“
  - `live` „Der core führt den Plan aus.“
- **Karte „Akku-Unterstützung“** (Laden):
  - Schalter „Akku-Unterstützung erlaubt“, Zahl „Unterstützungsschwelle“ (in %), Taste „Übernehmen“.
  - Hinweis „Wirksam ab {soc} % ({Quelle}).“ mit den Quellen „Einstellung“, „Konfiguration“, „Plan“.
  - Ohne Health: „Wirksame Schwelle unbekannt.“
- **Audit:** Aktion `control.support`, Ziel „Akku-Unterstützung“. Kommentar „erlaubt, ab {soc} %“, „erlaubt, Schwelle aus der Konfiguration“ oder „gesperrt“.
- **HA:** Schalter `battery_support` „Akku-Unterstützung erlaubt“. Zahl `support_soc` „Unterstützungsschwelle“, Einheit `%`, Schritt 1, Bereich USV-Reserve bis 100. Ablehnung „Unterstützungsschwelle „{Wert}“ liegt außerhalb von {min}–100 %.“
- **Mehrkosten** (Fahrzeugkarte, nur bei `applies`): „Boost kann bis zu {eur} € Leistungspreis im Monat kosten ({kw} kW mehr Spitze).“, `eur` mit zwei Nachkommastellen, `kw` mit einer.
- **Karte „Plan gegen Ist (heute)“:**
  - Reihen „Geplanter SOC“ und „Ist-SOC“; Tabelle mit den Zeilen „Netzbezug“, „Einspeisung“, „Netzladen“ (kWh) und „Bezugskosten“ (€), den Spalten „Plan“, „Ist“ und „Differenz“.
  - Leer: „Für heute gibt es noch keine Viertelstunde mit Plan und Ist.“
  - Fehler: „Plan gegen Ist ist nicht verfügbar.“

---

### Task 1: Plan in der Absicht, Unterstützung in der Absicht, Plan-Schwelle im Slot

**Files:**
- Modify: `src/bindaems/shared/plan.py`, `src/bindaems/shared/control.py`, `tests/helpers.py`
- Test: `tests/unit/shared/test_plan.py`, `tests/unit/shared/test_control.py`

**Interfaces:**
- Produces:
  - `MAX_INTENT_PLAN_SLOTS: Final = 8`
  - `class IntentPlanSlot(_Model)`: `start: AwareDatetime`, `intent: BatteryIntent`, `budget_kw: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None = None`, `import_ct: Finite | None = None`, `support_soc_pct: Annotated[float, Field(ge=0, le=100)] | None = None`
  - `class IntentPlan(_Model)`: `id: int`, `created_at`, `valid_until`, `grid_charge_allowed: bool`, `slots: Annotated[list[IntentPlanSlot], Field(min_length=1)]`, `valid_at(ts) -> bool`, `slot_at(ts) -> IntentPlanSlot | None`
  - `def intent_plan(plan: Plan, *, grid_charge_allowed: bool) -> IntentPlan` (Präzisierung 2; `plan.id` muss gesetzt sein, sonst `ValueError`)
  - `PlanSlot.support_soc_pct: Finite | None = None`; `ReasonCode` mit `"UNTERSTUETZUNG_RESERVIERT"` (zwölf Codes)
  - `class SupportIntent(_Model)`: `allowed: bool = True`, `soc_pct: Annotated[float, Field(ge=0, le=100)] | None = None`
  - `Intent.plan: IntentPlan | None = None`, `Intent.support: SupportIntent = SupportIntent()`

- [ ] **Step 1: Write the failing tests**

```python
def test_intent_plan_keeps_the_binding_slots_only() -> None:
    plan = sample_plan(at("10:01"), ["10:00", "10:15", "10:30", "10:45", "11:00"]).model_copy(update={"id": 7})
    sent = intent_plan(plan, grid_charge_allowed=True)
    assert [s.start for s in sent.slots] == [at("10:00"), at("10:15"), at("10:30"), at("10:45")]
    assert (sent.id, sent.valid_until) == (7, at("10:46"))

def test_intent_plan_turns_grid_charge_into_self_consumption_when_switched_off() -> None:
    plan = sample_plan(at("10:01"), ["10:00", "10:15"], intents=[NETZLADEN_3000, HOLD]).model_copy(update={"id": 1})
    sent = intent_plan(plan, grid_charge_allowed=False)
    assert [s.intent.kind for s in sent.slots] == [EIGENVERBRAUCH, HALTEN]
    assert sent.grid_charge_allowed is False

def test_intent_plan_needs_a_stored_plan() -> None:
    with pytest.raises(ValueError):
        intent_plan(sample_plan(at("10:01"), ["10:00"]), grid_charge_allowed=True)

def test_intent_plan_carries_budget_price_and_threshold() -> None: ...
    # budget_kw, import_ct und support_soc_pct des PlanSlot kommen unverändert an

def test_stored_plan_without_threshold_loads() -> None:
    raw = sample_plan(at("10:01"), ["10:00"]).model_dump(mode="json")
    del raw["slots"][0]["support_soc_pct"]
    assert Plan.model_validate(raw).slots[0].support_soc_pct is None

def test_intent_defaults_have_no_plan_and_allow_support() -> None:
    intent = Intent(version=1)
    assert intent.plan is None
    assert intent.support == SupportIntent(allowed=True, soc_pct=None)

def test_intent_round_trips_with_a_plan() -> None:
    intent = Intent(version=2, plan=intent_plan(stored_plan(), grid_charge_allowed=True))
    assert Intent.model_validate(intent.model_dump(mode="json")) == intent

def test_support_threshold_out_of_range_is_invalid() -> None:
    with pytest.raises(ValidationError):
        SupportIntent(soc_pct=101)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/shared/test_plan.py tests/unit/shared/test_control.py -q`
Expected: FAIL (`ImportError: intent_plan`)

- [ ] **Step 3: Implement**
  - Typen und `intent_plan` in `shared/plan.py`; `IntentPlan.valid_at` und `slot_at` wie bei `Plan`.
  - `shared/control.py` importiert `IntentPlan` aus `bindaems.shared.plan` (kein Kreis: `plan.py` importiert `control.py` nicht).
  - `INTENT_CHARGING_DEFAULTS` um `"plan": None, "support": {"allowed": True, "soc_pct": None}` ergänzen.
  - `UNTERSTUETZUNG_RESERVIERT` in `PlanReasonCodeSchema` (`ui/src/lib/api/schemas.ts`) aufnehmen, damit der UI-Vertrag grün bleibt; der Satz folgt in Task 9.

- [ ] **Step 4: Run the full test suite**

Run: `uv run pytest -q -x && uv run mypy src && uv run lint-imports && cd ui && pnpm check && pnpm test`
Expected: PASS. Tests, die `intent.json` oder das JSON einer Absicht mit festen Dicts vergleichen, nehmen `INTENT_CHARGING_DEFAULTS`.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared tests ui/src/lib/api/schemas.ts
git commit -m "feat(shared): Plan und Akku-Unterstützung in der Absicht"
```

### Task 2: Prüfung und Klemmung des Plans im core

**Files:**
- Modify: `src/bindaems/core/control/intent.py`
- Test: `tests/unit/core/control/test_intent.py`, `tests/unit/core/api/test_api.py`

**Interfaces:**
- Consumes: Task 1 (`IntentPlan`, `SupportIntent`, `MAX_INTENT_PLAN_SLOTS`, `PLAN_VALID`).
- Produces: `IntentStore.submit` mit den Regeln aus Präzisierung 4. `IntentStore` braucht dafür `reserve_soc_pct` und `max_grid_charge_setpoint_w` aus `HardLimits` (sie liegen dort: `limits.setpoint_limits`, `limits` der Batterie; sonst als neue Konstruktor-Argumente mit Werten aus `cfg`).

- [ ] **Step 1: Write the failing tests** (Helper `plan_at(created, starts, *, valid_until=None, kinds=…, grid_charge_allowed=True)` im Test-Modul)

```python
def test_plan_is_accepted_and_saved(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    plan = plan_at(T0 + timedelta(minutes=1), ["10:00", "10:15", "10:30", "10:45"])
    assert store.submit(Intent(version=1, plan=plan), T0 + timedelta(minutes=1)).result == "accepted"
    assert read_json(tmp_path / "intent.json")["intent"]["plan"]["id"] == plan.id

def test_gap_in_plan_slots_is_invalid(tmp_path, limits) -> None:
    reply = make_store(tmp_path, limits).submit(Intent(version=1, plan=plan_at(T1, ["10:00", "10:30"])), T1)
    assert (reply.result, reply.rejection) == ("rejected", "invalid")
    assert reply.details == ("Plan: Slots nicht im Viertelstundenraster oder nicht lückenlos.",)

def test_more_than_eight_slots_is_invalid(tmp_path, limits) -> None: ...
def test_validity_beyond_the_last_slot_is_invalid(tmp_path, limits) -> None: ...
def test_validity_longer_than_45_min_is_invalid(tmp_path, limits) -> None: ...
def test_grid_charge_slot_with_switch_off_is_invalid(tmp_path, limits) -> None:
    # Review Focus 5; Satz „Plan: Netzladen, obwohl ausgeschaltet.“

def test_valid_until_is_clamped_to_45_min_after_acceptance(tmp_path, limits) -> None:
    # Review Focus 3: Plan der app von 10:20 mit valid_until 11:05, angenommen um 10:01
    reply = store.submit(Intent(version=1, plan=skewed), at("10:01"))
    assert reply.result == "clamped"
    assert store.current.plan.valid_until == at("10:46")
    assert reply.details == ("Plan bis 12:46 begrenzt.",)  # Wiener Zeit (UTC+2)

def test_grid_charge_above_the_setpoint_cap_is_clamped(tmp_path, limits) -> None:
    # 9000 W → 8000 W, Hinweis „Netzladen auf 8000 W begrenzt (maximaler Netzladesetpoint).“

def test_expired_plan_is_accepted_and_inert(tmp_path, limits) -> None:
    # Review Focus 1: Plan bis 10:46, angenommen um 11:30 → accepted, keine Klemmung, kein Fehler
    assert store.submit(Intent(version=1, plan=old), at("11:30")).result == "accepted"

def test_repeated_version_with_clamped_plan_is_a_repetition(tmp_path, limits) -> None:
    # gleiche Version, gleicher Inhalt nach dem Klemmen gegen dieselbe Annahmezeit → kein 409

def test_support_threshold_below_reserve_is_invalid(tmp_path, limits) -> None:
    # Satz „Unterstützungsschwelle 15 % liegt unter der USV-Reserve (20 %).“

def test_saved_intent_without_plan_loads_with_defaults(tmp_path, limits) -> None:
    # intent.json im Format von Plan 2d (ohne plan und support) lädt; plan None, support Standard

def test_put_intent_with_invalid_plan_is_422_with_details(view_client) -> None:  # test_api.py
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/control/test_intent.py tests/unit/core/api/test_api.py -q`
Expected: FAIL (plan accepted without check, no clamping)

- [ ] **Step 3: Implement** `_plan_problems(plan) -> list[str]` und `_support_problems(support) -> list[str]` in `_invalid`, das Klemmen des Plans in `_clamp`. Das Klemmen hängt wie beim Boost nur von `received` ab; so bleibt eine Wiederholung eine Wiederholung.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control/intent.py tests/unit/core
git commit -m "feat(core): Plan in der Absicht prüfen und klemmen"
```

### Task 3: Gültiger Slot und Health „plan“

**Files:**
- Create: `src/bindaems/core/control/plan_exec.py`
- Modify: `src/bindaems/core/control/regulation.py`, `src/bindaems/core/runtime.py`, `src/bindaems/core/api/app.py` (`HealthReport.plan`, Docstring)
- Test: `tests/unit/core/control/test_plan_exec.py`, `tests/unit/core/control/test_regulation.py`, `tests/integration/test_runtime_battery.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `@dataclass(frozen=True) class ActiveSlot`: `plan_id: int`, `start: datetime`, `intent: BatteryIntent`, `budget_kw: float | None`, `import_ct: float | None`, `support_soc_pct: float | None`
  - `def active_slot(plan: IntentPlan | None, wall_now: datetime) -> ActiveSlot | None`
  - `def plan_health(plan: IntentPlan | None, active: ActiveSlot | None, wall_now: datetime) -> dict[str, Any]` (Präzisierung 5)
  - `Regulation.step` setzt zu Beginn `self._active = active_slot(charge.intent.plan, charge.wall_now)`; dazu `Regulation.active() -> ActiveSlot | None` und `Regulation.health()["plan"]`. Ruht die Regelung, ist `plan` `plan_health(intent.plan, None, now)` mit dem Stand aus der Gültigkeit, ohne Absicht `none`.
  - `HealthReport.plan: dict[str, Any] = Field(default_factory=dict)`

- [ ] **Step 1: Write the failing tests**

```python
def test_active_slot_inside_validity() -> None:
    slot = active_slot(PLAN_1001, at("10:20"))
    assert (slot.start, slot.intent.kind) == (at("10:15"), HALTEN)

def test_no_slot_outside_validity() -> None:
    assert active_slot(PLAN_1001, at("10:46")) is None   # Review Focus 3
    assert active_slot(PLAN_1001, at("09:59")) is None
    assert active_slot(None, at("10:20")) is None

def test_plan_health_states() -> None:
    assert plan_health(None, None, at("10:20"))["state"] == "none"
    assert plan_health(PLAN_1001, active_slot(PLAN_1001, at("10:20")), at("10:20"))["state"] == "valid"
    assert plan_health(PLAN_1001, None, at("10:50"))["state"] == "expired"

def test_regulation_health_has_the_plan(cfg) -> None: ...   # test_regulation.py
async def test_restart_uses_the_saved_plan_until_it_expires(runtime_env) -> None:
    # Review Focus 2: Absicht mit HALTEN-Plan annehmen, core neu bauen (gleiches data_dir),
    # Health plan.state "valid" vor valid_until, "expired" danach
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/control/test_plan_exec.py tests/unit/core/control/test_regulation.py tests/integration/test_runtime_battery.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** `plan_exec.py` und die Verdrahtung. `CoreRuntime.health` gibt `regulation["plan"]` an `HealthReport`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core tests/integration/test_runtime_battery.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core tests
git commit -m "feat(core): gültiger Plan-Slot und Health plan"
```

### Task 4: Akku-Sollwert führt HALTEN und NETZLADEN aus

**Files:**
- Modify: `src/bindaems/core/control/battery.py`, `src/bindaems/core/control/regulation.py`, `src/bindaems/shared/decisions.py` (`_REASON_TEXT`), `tests/unit/test_docs.py` (`BATTERY_REASONS`), `docs/betrieb.md` („Akku und Spitzen“, Gründe)
- Test: `tests/unit/core/control/test_battery_controller.py`, `tests/unit/core/control/test_regulation.py`, `tests/property/test_battery.py`

**Interfaces:**
- Consumes: Task 3 (`ActiveSlot`, `Regulation._active`).
- Produces:
  - `PLAN_HOLD: Final = "plan_hold"`, `PLAN_GRID_CHARGE: Final = "plan_grid_charge"`
  - `def plan_wanted_w(need: BatteryNeed, base_w: float, active: ActiveSlot | None) -> float` (Präzisierung 7)
  - `BatteryController.decide(..., active: ActiveSlot | None = None)`
  - `peak_wanted_w(need, *, acting, base_w, active)`: gibt dasselbe `W` an den Wächter
  - `_inputs` bekommt `plan_grid_charge_w` (nur mit `active`)

- [ ] **Step 1: Write the failing tests** (mit `HOLD_SLOT = ActiveSlot(1, T0, BatteryIntent(kind=HALTEN), None, 30.0, None)` usw.)

```python
def test_hold_draws_the_house_and_cars_from_the_grid(cfg) -> None:
    need = assessed(cfg, pv_w=500.0, house_w=1800.0, cars=())   # Fehlbetrag 1300 W, Basis 0
    [found] = BatteryController(cfg).decide(T0, need, **FREE, active=HOLD_SLOT)
    assert found.command == setpoint(1300.0, "plan_hold")

def test_hold_with_pv_surplus_sends_no_override(cfg) -> None:
    # PV 3000, Haus 1000 → W = 0 → kein Override (PV lädt weiter)

def test_hold_counts_a_boost_car(cfg) -> None:
    # Entscheidung 3: Boost-Auto 7400 W, Haus 600, PV 0 → Sollwert 8000 W, Grund plan_hold

def test_grid_charge_adds_p_to_the_hold_target(cfg) -> None:
    # PV 0, Haus 800, P 3000 → 3800 W, Grund plan_grid_charge, inputs["plan_grid_charge_w"] == 3000

def test_grid_charge_with_pv_surplus_imports_p(cfg) -> None:
    # PV 4000, Haus 1000, P 2000 → 2000 W

def test_budget_caps_the_plan_target_with_the_peak_reason(cfg) -> None:
    # NETZLADEN 6000 W, budget_w 4000 → 4000 W, Gründe ("plan_grid_charge", "peak_guard")

def test_hold_below_the_deadband_sends_no_override(cfg) -> None:
    # Fehlbetrag 150 W → keine Zahl (Totband, Entscheidung 2)

def test_self_consumption_slot_behaves_like_no_plan(cfg) -> None:
    # gleiche Befehle mit active=EV_SLOT und active=None

def test_hold_above_rule_2_is_clamped_downstream(cfg) -> None:   # test_regulation.py, Review Focus 4
    # Sofort 11 kW + Haus 1 kW bei HALTEN: Befehl 12000 W, die Safety-Schicht (limits) klemmt auf 8000 W

def test_guard_sees_the_plan_target(cfg) -> None:   # test_regulation.py
    # peak_wanted_w gibt bei NETZLADEN P + Fehlbetrag zurück
```

  Property (`tests/property/test_battery.py`): Für zufällige PV, Hauslast, Autos und einen Slot mit `HALTEN` oder `NETZLADEN` ist das Ziel vor `_guarded` nie negativ und nie unter `W_hold`; bei `NETZLADEN` ist es genau `W_hold + P`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/control/test_battery_controller.py tests/unit/core/control/test_regulation.py tests/property/test_battery.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** `plan_wanted_w` und die Verzweigung in `decide`.
  - `plan_hold` bzw. `plan_grid_charge` ersetzen `battery_comp`/`battery_support`; `peak_guard` folgt dahinter.
  - Die Texte „Plan: Halten“ und „Plan: Netzladen“ kommen in `_REASON_TEXT`, beide Codes in `BATTERY_REASONS` und in die Tabelle der Gründe in `docs/betrieb.md`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit tests/property -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests docs/betrieb.md
git commit -m "feat(core): Akku-Sollwert führt HALTEN und NETZLADEN aus"
```

### Task 5: Unterstützung mit Plan-Schwelle und Schalter

**Files:**
- Modify: `src/bindaems/core/control/battery.py` (`Support`, `BatteryController.assess`, `health`), `src/bindaems/core/control/regulation.py`, `src/bindaems/core/api/app.py` (Docstring), `docs/betrieb.md`
- Test: `tests/unit/core/control/test_battery.py`, `tests/unit/core/control/test_battery_controller.py`

**Interfaces:**
- Consumes: Task 1 (`SupportIntent`), Task 3 (`ActiveSlot`).
- Produces:
  - `SupportReason = Literal["on", "soc", "time", "mode", "off", "plan"]`
  - `def effective_threshold(control: ControlConfig, support: SupportIntent, active: ActiveSlot | None) -> tuple[float, Literal["config", "intent", "plan"]]`. Die Quelle ist nur dann `plan`, wenn die Plan-Schwelle echt höher liegt.
  - `Support.update(now, soc_pct, cars, deficit, *, threshold_pct: float, allowed: bool)`. Reihenfolge der Gründe: `mode`, dann `off`, dann `plan` (vom Aufrufer bei `HALTEN`/`NETZLADEN` übergeben), dann `soc`, dann `time`. Die Hysterese beginnt bei `≥ threshold_pct` und endet bei `< threshold_pct − support_hysteresis_pct`.
  - `BatteryController.assess(..., support: SupportIntent = SupportIntent(), active: ActiveSlot | None = None)`
  - Health `battery`: `support_soc_pct`, `support_source`

- [ ] **Step 1: Write the failing tests**

```python
def test_plan_threshold_raises_the_effective_threshold(cfg) -> None:
    assert effective_threshold(cfg.control, SupportIntent(), slot_with(support_soc_pct=82.0)) == (82.0, "plan")
    assert effective_threshold(cfg.control, SupportIntent(soc_pct=60.0), slot_with(support_soc_pct=40.0)) == (60.0, "intent")
    assert effective_threshold(cfg.control, SupportIntent(), None) == (70.0, "config")

def test_support_switched_off_is_blocked_with_reason_off(cfg) -> None:
    support = Support(cfg.control)
    assert support.update(T0, 90.0, PV_CARS, DEFICIT, threshold_pct=70.0, allowed=False)[0] == "off"

def test_hysteresis_follows_the_moving_threshold(cfg) -> None:
    # Schwelle 70 → on bei 72 %; Schwelle steigt auf 80 → bei 72 % (unter 80 − 5) gesperrt "soc"

def test_hold_blocks_support_with_reason_plan(cfg) -> None:
    # assess mit active=HOLD_SLOT → need.support == "plan", allowed_w == 0

def test_health_shows_the_effective_threshold_and_source(cfg) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/control/test_battery.py tests/unit/core/control/test_battery_controller.py -q`
Expected: FAIL

- [ ] **Step 3: Implement.** `Regulation.step` gibt `charge.intent.support` und `self._active` an `assess`. `off` und `plan` stehen in `docs/betrieb.md` bei den Gründen in Health.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit tests/property -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests docs/betrieb.md
git commit -m "feat(core): Akku-Unterstützung mit Plan-Schwelle und Schalter"
```

### Task 6: Budget des Wächters aus dem Plan

**Files:**
- Modify: `src/bindaems/core/control/peak_guard.py`, `src/bindaems/core/control/regulation.py`, `docs/betrieb.md`
- Test: `tests/unit/core/control/test_peak_guard.py`, `tests/unit/core/control/test_regulation.py`, `tests/property/test_peak_guard.py`

**Interfaces:**
- Consumes: Task 3, Task 4 (`peak_wanted_w` mit Plan).
- Produces:
  - `PeakGuard.step(wall_now, quarter, cars, wanted_w, base_w, soc_pct, *, target_kw: float | None)`; `self._target_kw` aus `__init__` entfällt
  - `def guard_target_kw(control: ControlConfig, active: ActiveSlot | None) -> float | None` (Präzisierung 9)
  - `PeakGuard.target_kw() -> float | None` (das Ziel des letzten Schritts)

- [ ] **Step 1: Write the failing tests**

```python
def test_plan_budget_replaces_the_target(cfg) -> None:
    assert guard_target_kw(with_control(cfg, peak_target_kw=8.0).control, slot_with(budget_kw=5.5)) == 5.5
    assert guard_target_kw(with_control(cfg, peak_target_kw=8.0).control, slot_with(budget_kw=None)) == 8.0
    assert guard_target_kw(cfg.control, None) is None

def test_target_change_mid_quarter_changes_the_allowance(cfg) -> None:
    # gleiche Viertelstunde, Ziel 8 → 5 kW: P_zul sinkt entsprechend, Zustand "active"

def test_health_target_follows_the_last_step(cfg) -> None:
    # nach Schritt mit target_kw=None → state "off", target_kw None

def test_regulation_uses_the_plan_budget(cfg) -> None:   # test_regulation.py
```

  Die bestehenden Tests übergeben `target_kw` ausdrücklich, meist `cfg.control.peak_target_kw`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/control/test_peak_guard.py tests/unit/core/control/test_regulation.py -q`
Expected: FAIL

- [ ] **Step 3: Implement.** In `docs/betrieb.md` kommt ein Satz zum Budget aus dem Plan in den Abschnitt zum Wächter.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit tests/property -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests docs/betrieb.md
git commit -m "feat(core): Budget des 15-min-Wächters aus dem Plan"
```

### Task 7: Preis und Plan-Slot in den Entscheidungen

**Files:**
- Modify: `src/bindaems/core/control/regulation.py` (`Regulation.plan_inputs() -> dict[str, float]`), `src/bindaems/core/runtime.py` (`_control`: in `inputs` mischen), `docs/betrieb.md` (Eingaben der Entscheidungen)
- Test: `tests/unit/core/control/test_regulation.py`, `tests/integration/test_runtime_battery.py`

**Interfaces:**
- Consumes: Task 3, Task 6 (`PeakGuard.target_kw()`).
- Produces: `Regulation.plan_inputs()` liefert die Schlüssel aus Präzisierung 10 (`plan_slot_ts`, `price_ct`, `budget_kw`), ohne gültigen Slot ein leeres Dict. Die Laufzeit setzt sie nach `decision_inputs(...)` und vor `found.inputs`.

- [ ] **Step 1: Write the failing tests**

```python
def test_plan_inputs_without_plan_are_empty(cfg) -> None:
    assert regulation_after_step(cfg, plan=None).plan_inputs() == {}

def test_plan_inputs_with_slot(cfg) -> None:
    assert regulation_after_step(cfg, plan=PLAN_HOLD_1001, at_=at("10:20")).plan_inputs() == {
        "plan_slot_ts": at("10:15").timestamp(), "price_ct": 31.5, "budget_kw": 6.0}

async def test_decisions_carry_price_and_slot(runtime_env) -> None:
    # Integration: Victron-Entscheidung im Dry-Run mit HALTEN-Plan hat plan_slot_ts, price_ct, plan_grid_charge_w == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/control/test_regulation.py tests/integration/test_runtime_battery.py -q`
Expected: FAIL

- [ ] **Step 3: Implement.** Die neuen Schlüssel stehen in `docs/betrieb.md` bei den Eingaben der Entscheidungen.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests -q -k "regulation or runtime or decision"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests docs/betrieb.md
git commit -m "feat(core): Preis und Plan-Slot in den Entscheidungen"
```

### Task 8: Simulator mit Wirkungsgrad und Plan-Szenarien

**Files:**
- Modify: `src/bindaems/tools/sim/model.py`, `src/bindaems/tools/sim/scenarios.py`, `src/bindaems/tools/sim/runner.py`, `tests/integration/test_sim_scenarios.py`, `tests/unit/test_docs.py` (Zahl der Szenarien), `docs/betrieb.md` („Simulator“: Tabelle, Zahlwort `achtundzwanzig` dreimal), `docs/uebergabe.md` (Zahlwort)
- Test: `tests/unit/tools/sim/test_sim_model.py`, `tests/unit/tools/sim/test_sim_battery_checks.py`, `tests/integration/test_sim_scenarios.py`

**Interfaces:**
- Consumes: Tasks 1 bis 7 (der core führt Pläne aus der Absicht aus).
- Produces:
  - `PlantModel(cfg, rng, *, roundtrip_pct: float = 100.0)`
  - `Scenario.roundtrip_pct: float = 88.0`
  - `class _Plan` (event): `slots: tuple[tuple[int, BatteryIntent, float | None], ...]` (Versatz in Slots ab Beginn des Szenarios, Absicht, budget_kw), `valid_s: int`, `grid_charge_allowed: bool = True`
  - fünf Szenarien am Ende von `SCENARIOS` (zusammen 28)

- [ ] **Step 1: Write the failing model tests**

```python
def test_charging_stores_eta_times_the_energy(cfg) -> None:
    model = PlantModel(cfg, random.Random(1), roundtrip_pct=88.0)
    # 1 h mit 3000 W Laden → SOC steigt um 3 kWh · √0,88 / usable_kwh · 100

def test_discharging_draws_energy_divided_by_eta(cfg) -> None: ...
def test_reserve_limit_counts_eta(cfg) -> None: ...
def test_default_model_stays_lossless(cfg) -> None:
    # bestehendes Verhalten: roundtrip_pct=100 rechnet wie bisher
```

- [ ] **Step 2: Run tests to verify they fail**, dann den Wirkungsgrad in `model.py:558-560` und in `_above_reserve_w` einbauen

Run: `uv run pytest tests/unit/tools/sim/test_sim_model.py -q`
Expected: FAIL first, then PASS

- [ ] **Step 3: Write the scenarios** mit ihren Prüfungen im Stil von `override-stunde` (benannte `Final`-Toleranzen, deutsche Sätze, eine leere Auswahl ist ein Verstoß):
  - **`plan-halten`** („Halten am Abend“): `EVENING`, SOC 60 %, Victron live, ein Plan mit `HALTEN` über 4 Slots, alle 15 min ein neuer. Lastsprung auf L1 um +2 kW von Minute 20 bis 25.
    - Prüfungen: ab 2 min ist die mittlere Akkuleistung jeder Minute ≥ −`HOLD_W` (300 W); der SOC fällt höchstens um 1,5 Prozentpunkte; höchstens 40 Entscheidungen an Victron je Stunde.
  - **`plan-netzladen`** („Netzladen in der Nacht“): 02:00 Uhr lokal, SOC 30 %, `NETZLADEN(3000)` für 30 min.
    - Prüfungen: ab 2 min liegt der Netzbezug in `Haus + 3000 ± 300` W; der SOC steigt um mindestens 90 % von `3 kW · 0,5 h · √0,88`.
  - **`plan-abgelaufen`** („Plan läuft ab“): `HALTEN`, gültig 20 min, kein neuer Plan.
    - Prüfungen: bis Minute 20 hält der Akku wie oben; ab Minute 26 (Override verfallen, 300 s plus 60 s) gilt kein Override, und der Akku trägt das Haus (Akkuleistung ≤ −Haus + 300 W).
  - **`plan-budget`** („Netzladen unter dem Budget“): `NETZLADEN(6000)` mit `budget_kw` 4,0 über 3 Viertelstunden.
    - Prüfung: jede abgeschlossene Viertelstunde bezieht ≤ 1,0 kWh + 0,02 kWh aus dem Netz.
  - **`plan-boost`** („Boost während Halten“): e-Golf ab Minute 1 im Boost, Pläne mit `HALTEN` alle 15 min über 1 h.
    - Prüfungen: der Boost läuft über alle neuen Pläne weiter (der e-Golf lädt ≥ 90 % der Zeit); der Akku entlädt nicht ins Auto (`battery_into_car_pct` ≤ 5 %).

- [ ] **Step 4: Listen und Doku nachziehen:** `NAMES` und die Listen in `test_sim_scenarios.py` (`len(SCENARIOS) == 28`), die Tabelle der Szenarien in `docs/betrieb.md`, die Zahlwörter und `test_docs.py`.

Run: `uv run pytest tests/integration/test_sim_scenarios.py tests/unit/tools/sim tests/unit/test_docs.py -q`
Expected: PASS. Scheitert ein bestehendes Szenario am Wirkungsgrad, zuerst die Ursache suchen; eine Toleranz nur mit Begründung im Docstring des Szenarios lockern.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools/sim tests docs
git commit -m "feat(sim): Wirkungsgrad und Szenarien zur Planausführung"
```

### Task 9: Plan-Schwelle und Grundcode in der app

**Files:**
- Modify: `src/bindaems/app/planning/inputs.py` (`PlanInputs.support_soc_pct: Finite = 70.0`, Argument von `assemble_inputs`), `src/bindaems/app/planning/builder.py`, `src/bindaems/app/planning/explain.py`, `src/bindaems/app/planning/service.py` (gibt die konfigurierte Schwelle mit), `src/bindaems/app/planning/routes.py` (`slot_json`: `support_soc_pct`), `ui/src/lib/api/schemas.ts` (`PlanSlotSchema.support_soc_pct`), contract files, `docs/betrieb.md` („Erklärungen“)
- Test: `tests/unit/app/planning/test_builder.py`, `tests/unit/app/planning/test_explain.py`, `tests/unit/app/planning/test_routes.py`

**Interfaces:**
- Consumes: Task 1.
- Produces:
  - `def support_thresholds(slots: Sequence[PlanSlot], reserve_pct: float) -> list[tuple[float, datetime]]` liefert je Slot Schwelle und `um` (Präzisierung 6)
  - `build_plan` setzt `PlanSlot.support_soc_pct`; `reasons_for` hängt `UNTERSTUETZUNG_RESERVIERT` an
  - `PlanningService(..., support_pct: Callable[[], float] | None = None)`; Standard ist `cfg.control.support_soc_pct`, die Verdrahtung (Task 10) gibt den Wert der Absicht mit, wenn er gesetzt ist

- [ ] **Step 1: Write the failing tests**

```python
def test_thresholds_reserve_the_evening_discharge() -> None:
    # SOC 60 → 50 → 40 → 30 → 20 (je 10 Punkte Entladung), Reserve 20
    assert [t for t, _ in support_thresholds(slots_from(60, [10, 10, 10, 10]), 20.0)] == [60.0, 50.0, 40.0, 30.0]

def test_pv_refill_releases_the_threshold() -> None:
    # Entladung 10, dann Laden 30: R vor dem Laden = 0 → Schwelle = Reserve
    assert support_thresholds(slots_from(50, [10, -30]), 20.0)[0][0] == 20.0

def test_threshold_is_rounded_up_and_capped() -> None: ...

def test_reserved_code_only_above_the_configured_threshold() -> None:
    plan = build_plan(inputs_with(support_soc_pct=70.0, ...evening...), ...)
    codes = [[r.code for r in s.reasons] for s in plan.slots]
    assert "UNTERSTUETZUNG_RESERVIERT" in codes[0] and "UNTERSTUETZUNG_RESERVIERT" not in codes[-1]

def test_reserved_sentence() -> None:
    assert sentence(Reason(code="UNTERSTUETZUNG_RESERVIERT", params={"soc": 82, "um": "2026-10-16T19:00:00+00:00"})) == \
        "Akku-Unterstützung fürs Auto erst ab 82 %, weil der Plan den Akku bis 21:00 Uhr braucht."

def test_no_reserved_code_in_hold_slots() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning -q`
Expected: FAIL

- [ ] **Step 3: Implement.** Vertragsdateien mit `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py` neu schreiben; Code und Satz kommen in die Tabelle der Grundcodes in `docs/betrieb.md` (zwölf Codes).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests -q -k "planning or contract or docs" && (cd ui && pnpm check && pnpm test)`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/planning ui/src tests docs/betrieb.md
git commit -m "feat(app): Plan-Schwelle der Akku-Unterstützung"
```

### Task 10: Plan an den core übergeben

**Files:**
- Create: `src/bindaems/app/planning/sender.py`
- Modify: `src/bindaems/app/control/intent.py` (`SetPlan`, ohne Änderungsprotokoll), `src/bindaems/app/planning/service.py` (Zuhörer `on_plan` nach `_run`), `src/bindaems/app/runtime.py` (Verdrahtung, Job `("plan-sender", sender.run)`, `connect_handlers`, `support_pct` aus `intents.cached()`)
- Test: `tests/unit/app/planning/test_sender.py`, `tests/unit/app/control/test_intent.py`, `tests/integration/test_core_link.py`

**Interfaces:**
- Consumes: Tasks 1 und 2.
- Produces:
  - `@dataclass(frozen=True) class SetPlan`: `plan: IntentPlan | None`. Im `IntentService` gibt es dafür kein `_check`, kein Gate und keinen Eintrag im Änderungsprotokoll: `_audit` liefert `None`, und `_finish` lässt den Eintrag aus.
  - `PlanningService.on_plan(listener: Callable[[Plan], None]) -> None`; die Zuhörer laufen nach `self._plan = saved`, ihre Fehler werden nur geloggt.
  - `class PlanSender`: `__init__(intents: IntentService, planning: PlanningService, settings: SettingsService, live: LiveState, clock: Clock, *, interval_s: float = 30.0)`, `wake() -> None` (thread-safe), `async sync() -> Literal["sent", "current", "none", "failed"]`, `async run() -> None`
  - `ACTOR: Final = "planung"`, `SOURCE: Final = "system"` (`app/audit.py::Source` prüfen; kennt der Typ `system` nicht, wird er erweitert)

- [ ] **Step 1: Write the failing tests** (Fakes wie in `tests/unit/app/control/conftest.py`)

```python
async def test_new_plan_is_sent_once(sender, fake_client, planning) -> None:
    assert await sender.sync() == "sent"
    assert fake_client.sent[-1].plan == intent_plan(planning.current(), grid_charge_allowed=False)
    live_reports(fake_client.sent[-1])          # Health zeigt den Plan
    assert await sender.sync() == "current"

async def test_expired_plan_is_not_sent(sender, clock) -> None:
    clock.advance(timedelta(minutes=50))
    assert await sender.sync() == "none"

async def test_core_down_is_retried_on_the_next_sync(sender, fake_client) -> None:
    fake_client.error = CoreUnavailableError("weg")
    assert await sender.sync() == "failed"
    fake_client.error = None
    assert await sender.sync() == "sent"

async def test_switching_grid_charge_off_sends_the_plan_without_grid_charge(...) -> None:   # Review Focus 5
    # Plan mit NETZLADEN gesendet; Einstellung aus → sync sendet denselben Plan (gleiche id) mit
    # grid_charge_allowed False und EIGENVERBRAUCH statt NETZLADEN

async def test_plan_send_retries_once_on_conflict(...) -> None:   # Review Focus 1
async def test_operator_change_keeps_the_plan(service, fake_client) -> None:   # test_intent.py
    # gültige Absicht mit Plan; SetMode → gesendete Absicht hat denselben plan
async def test_plan_change_writes_no_audit_entry(service, audit) -> None: ...
async def test_reconnect_wakes_the_sender(...) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning/test_sender.py tests/unit/app/control/test_intent.py -q`
Expected: FAIL

- [ ] **Step 3: Implement.**
  - `run()` wartet auf ein `asyncio.Event` mit Zeitlimit `interval_s` und ruft `sync()`.
  - Anstöße: `planning.on_plan(lambda _: sender.wake())`, `settings.on_change(lambda *_: sender.wake())` und `live_feed.connect_handlers.append(sender.wake)`.
  - Der core hat den Plan, wenn der Plan in `live.health["intent"]` dieselbe `id` und dasselbe `grid_charge_allowed` hat. Ohne Verbindung liefert `sync()` `failed`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app tests/integration/test_core_link.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests
git commit -m "feat(app): Plan an den core übergeben"
```

### Task 11: Stand der Ausführung statt „Vorschau“

**Files:**
- Create: `src/bindaems/app/planning/execution.py`
- Modify: `src/bindaems/app/planning/routes.py` (`GET /api/plan`: `execution: {state, text} | null`), `src/bindaems/app/ha/entities.py` (`PREVIEW` entfällt, Attribut `execution` an den vier Plan-Sensoren, `HaInputs.execution`), `src/bindaems/app/runtime.py` (`_build_ha_inputs`), `ui/src/lib/api/schemas.ts`, `ui/src/lib/plan/PlanHeader.svelte`, Vertragsdateien, `tests/unit/test_docs.py` (`test_plan_preview_marker_in_the_guide_and_the_ui` → `test_plan_execution_texts_in_the_guide_and_the_ui`), `docs/betrieb.md` („Planung“)
- Test: `tests/unit/app/planning/test_execution.py`, `tests/unit/app/planning/test_routes.py`, `tests/unit/app/ha/test_entities.py`, `ui/src/lib/plan/PlanHeader.test.ts`

**Interfaces:**
- Consumes: Task 3 (Health `plan`), Task 10 (Vergleich über `(id, grid_charge_allowed)`).
- Produces:
  - `ExecutionState = Literal["none", "unreachable", "pending", "off", "idle", "locked", "dry_run", "live"]`
  - `def execution_state(plan: Plan | None, *, valid: bool, connected: bool, health: Mapping[str, Any] | None, grid_charge_allowed: bool) -> ExecutionState` (Präzisierung 12)
  - `def execution_text(state: ExecutionState, health: Mapping[str, Any] | None) -> str | None`

- [ ] **Step 1: Write the failing tests**

```python
@pytest.mark.parametrize(("case", "expected"), [
    ("no plan", "none"), ("disconnected", "unreachable"), ("other id", "pending"),
    ("control off", "off"), ("observe", "idle"), ("victron locked", "locked"),
    ("victron dry", "dry_run"), ("victron live", "live")])
def test_execution_state(case, expected) -> None: ...

def test_execution_texts() -> None:
    assert execution_text("dry_run", HEALTH) == "Dry-Run: Der core rechnet den Plan mit und sendet nichts an Victron."
    assert execution_text("idle", {"mode": "SAFE"}) == "Der core regelt gerade nicht (SAFE)."
    assert execution_text("none", None) is None

def test_plan_route_reports_the_execution(client_as) -> None: ...
def test_plan_sensors_carry_the_execution(...) -> None:   # ersetzt test_plan_sensors_are_marked_as_preview
```

  Vitest: `PlanHeader` zeigt `data.execution.text` statt der Zeile „Vorschau“, im Stil `.execution` (Stand `live` neutral, alle anderen in `--warn`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning/test_execution.py tests/unit/app/planning/test_routes.py tests/unit/app/ha -q`
Expected: FAIL

- [ ] **Step 3: Implement.**
  - Vertragsdateien neu schreiben.
  - In `docs/betrieb.md` („Planung“) ersetzen die sieben Texte und das Attribut `execution` den Satz zur Vorschau und `preview: true`.
  - Der neue Doku-Test prüft, dass jeder Text aus `execution.py` im Handbuch steht und `PlanHeader.svelte` die Zeile „Vorschau“ nicht mehr enthält.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests -q -k "planning or ha or contract or docs" && (cd ui && pnpm check && pnpm test)`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app ui/src tests docs/betrieb.md
git commit -m "feat(app): Stand der Ausführung im Zeitplan und in HA"
```

### Task 12: Akku-Unterstützung bedienen – app und Home Assistant

**Files:**
- Modify: `src/bindaems/app/control/intent.py` (`SetSupport`, mit Änderungsprotokoll), `src/bindaems/app/control/routes.py` (`PUT /api/control/support`), `src/bindaems/app/ha/entities.py` (Komponente `number`, `EntitySpec.min/max/step/unit`, Entitäten `battery_support` und `support_soc`, `HaInputs.support_allowed`, `HaInputs.support_soc_pct`), `src/bindaems/app/ha/commands.py` (Zahl-Parser, `SetSupport`), `src/bindaems/app/ha/publisher.py` (`allowed_topic` mit `number`), `src/bindaems/app/runtime.py`, `tests/unit/test_docs.py` (HA-Objekte und Plattformen), `docs/betrieb.md` (HA-Tabellen)
- Test: `tests/unit/app/control/test_intent.py`, `tests/unit/app/control/test_routes.py`, `tests/unit/app/ha/test_entities.py`, `tests/unit/app/ha/test_commands.py`, `tests/unit/app/ha/test_publisher.py`

**Interfaces:**
- Consumes: Task 1 (`SupportIntent`), Task 2 (der core prüft die Reserve).
- Produces:
  - `@dataclass(frozen=True) class SetSupport`: `allowed: bool`, `soc_pct: float | None`. `_check` lehnt `soc_pct` außerhalb von `[battery.reserve_soc_pct, 100]` mit dem Satz aus „Texte“ ab.
  - `PUT /api/control/support` („Bedienen“), body `SupportBody{allowed: bool, soc_pct: float | None}` (strikt); die Antwort wie bei den übrigen Änderungen.
  - HA:
    - `battery_support` (switch): `ON` → `SetSupport(True, aktuelle soc_pct)`, `OFF` → `SetSupport(False, aktuelle soc_pct)`.
    - `support_soc` (number, Zustand = Wert der Absicht oder `control.support_soc_pct`): eine Zahl → `SetSupport(aktuelles allowed, Wert)`; ein Wert außerhalb des Bereichs wird abgelehnt.

- [ ] **Step 1: Write the failing tests**

```python
async def test_set_support_changes_the_intent_and_audits(service, fake_client, audit) -> None:
    await service.apply(SetSupport(True, 60.0), actor="anna", source="ui")
    assert fake_client.sent[-1].support == SupportIntent(allowed=True, soc_pct=60.0)
    assert last_audit(audit) == ("control.support", "Akku-Unterstützung", "erlaubt, ab 60 %")

async def test_support_threshold_below_reserve_is_rejected(service) -> None: ...
def test_support_route_needs_operator(client_as) -> None: ...
def test_number_entity_discovery(cfg) -> None:
    config = discovery_payload("support_soc")
    assert (config["min"], config["max"], config["step"], config["unit_of_measurement"], config["mode"]) == (20.0, 100.0, 1.0, "%", "box")
async def test_number_command_out_of_range_is_rejected(handler) -> None:
    # „Unterstützungsschwelle „150“ liegt außerhalb von 20–100 %.“
async def test_support_switch_keeps_the_threshold(handler, intents) -> None: ...
def test_number_topics_are_allowed() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/control tests/unit/app/ha -q`
Expected: FAIL

- [ ] **Step 3: Implement.** Die HA-Tabellen in `docs/betrieb.md` („Home Assistant“ und „Akku und Spitzen“), die erwarteten Objekte in `test_docs.py` und die Plattformen (`number`) nachziehen.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests -q -k "control or ha or docs"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests docs/betrieb.md
git commit -m "feat(app): Akku-Unterstützung und Schwelle bedienen, auch in HA"
```

### Task 13: Karte „Akku-Unterstützung“ im UI

**Files:**
- Create: `ui/src/lib/charging/SupportCard.svelte`, `ui/src/lib/charging/SupportCard.test.ts`
- Modify: `ui/src/routes/laden/+page.svelte`, `ui/src/lib/api/endpoints.ts` (`setSupport`), `ui/src/lib/api/schemas.ts` (`IntentSchema.support`, `HealthSchema.battery.support_soc_pct/support_source`), Vertragsdateien (`OTHER`: `control-support`), `tests/integration/test_ui_contract.py`, `ui/src/lib/api/contract.test.ts`, `ui/tests/e2e/control.spec.ts`

**Interfaces:**
- Consumes: Task 12 (Route), Task 5 (Felder in Health).
- Produces: `SupportCard` mit den Props `support: {allowed, soc_pct}`, `effective: {soc_pct, source} | null`, `configured_pct: number`, `operate`, `busy`, `notice` und `onChange(allowed, soc_pct)`. `configured_pct` kommt aus `/api/limits`: `limits_json` bekommt dafür `control.support_soc_pct`, die Vertragsdatei `limits.json` ändert sich mit.

- [ ] **Step 1: Write the failing tests** (Vitest)
  - zeigt Schalter, Zahlfeld und den Hinweis „Wirksam ab 82 % (Plan).“
  - Lesende sehen die Werte ohne Bedienung
  - ein leeres Zahlfeld sendet `soc_pct: null`; ein Wert außerhalb von Reserve bis 100 zeigt einen Hinweis und sendet nichts
  - e2e: „Bedienen“ schaltet die Unterstützung aus, die Karte zeigt es nach dem Neuladen

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pnpm vitest run src/lib/charging/SupportCard.test.ts`
Expected: FAIL

- [ ] **Step 3: Implement** wie `GuestCard`, mit den Texten aus „Texte“.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_ui_contract.py -q && cd ui && pnpm check && pnpm lint && pnpm test && pnpm build && pnpm e2e`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui tests src/bindaems/app/settings
git commit -m "feat(ui): Karte Akku-Unterstützung auf der Seite Laden"
```

### Task 14: „Plan gegen Ist“ – Daten

**Files:**
- Create: `src/bindaems/app/planning/review.py`
- Modify: `src/bindaems/app/planning/store.py` (`plans_between(start: datetime, end: datetime) -> list[Plan]`), `src/bindaems/app/planning/routes.py` (`GET /api/plan/review`), `src/bindaems/app/runtime.py` (Abrechnung an den Router)
- Test: `tests/unit/app/planning/test_review.py`, `tests/unit/app/planning/test_store.py`, `tests/unit/app/planning/test_routes.py`

**Interfaces:**
- Consumes: `LedgerService.slots(start, end)` (`app/ledger/service.py:419`), `PlanStore`.
- Produces:
  - `def in_force(plans: Sequence[Plan], starts: Sequence[datetime]) -> dict[datetime, tuple[Plan, PlanSlot]]` (Präzisierung 13)
  - `def review(day: date, plans: Sequence[Plan], ledger: Sequence[LedgerSlot], now: datetime) -> dict[str, Any]` mit `{"day", "slots": [{"start", "planned": {...} | null, "actual": {...} | null}], "totals": {"planned": {...}, "actual": {...}}}`
  - `planned` = `intent`, `grid_charge_w`, `soc_end_pct`, `import_w`, `export_w`, `battery_w`, `import_cost_ct`, `plan_id`, `source`
  - `actual` = `import_w`, `export_w`, `battery_w`, `grid_charge_w`, `import_cost_ct`
  - Summen in `import_kwh`, `export_kwh`, `grid_charge_kwh`, `import_cost_eur`

- [ ] **Step 1: Write the failing tests**

```python
def test_newest_plan_created_before_the_slot_end_wins() -> None:
    plans = [plan_created("10:01", ...), plan_created("10:16", ...)]
    chosen = in_force(plans, [at("10:00"), at("10:15")])
    assert chosen[at("10:00")][0].created_at == at("10:01")
    assert chosen[at("10:15")][0].created_at == at("10:16")

def test_expired_plan_is_not_in_force() -> None: ...
def test_actual_battery_power_from_flows() -> None:
    # pv>battery 250 Wh, grid>battery 100 Wh, battery>house 50 Wh → battery_w 1200, grid_charge_w 400
def test_totals_only_over_slots_with_both() -> None: ...
def test_future_slots_are_left_out() -> None: ...
def test_review_route_rejects_days_outside_30_days(client_as) -> None: ...
def test_plans_between_reads_the_window(store) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning/test_review.py tests/unit/app/planning/test_store.py tests/unit/app/planning/test_routes.py -q`
Expected: FAIL

- [ ] **Step 3: Implement.** `plans_between` liest die Pläne mit `created_at` in `[start − PLAN_VALID, end)`; die Route liest `[lokale Mitternacht, min(now, Mitternacht + 1 Tag))`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/planning -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests
git commit -m "feat(app): Plan gegen Ist je Viertelstunde"
```

### Task 15: „Plan gegen Ist“ – UI und Demo-Welt

**Files:**
- Create: `ui/src/lib/plan/PlanReview.svelte`, `ui/src/lib/plan/PlanReview.test.ts`, `ui/src/lib/plan/review.ts` (`reviewOption(review, soc, palette)`, `reviewTotals(review)`)
- Modify: `ui/src/routes/zeitplan/+page.svelte` (Ressourcen `api.planReview()` alle 300 s und `api.history(['soc.battery'], today)`), `ui/src/lib/api/endpoints.ts`, `ui/src/lib/api/schemas.ts` (`PlanReviewSchema`), `tests/integration/test_ui_contract.py` (`GET_ENDPOINTS["plan-review"] = "/api/plan/review"`), `ui/src/lib/api/contract.test.ts`, `tests/ui_world.py` (ein zusätzlicher Plan um 09:01 vor dem Plan um 10:00, damit die Demo Plan und Ist für 09:00 bis 10:00 zeigt), `ui/src/routes/zeitplan/page.test.ts`, `ui/tests/e2e/plan.spec.ts`

**Interfaces:**
- Consumes: Task 14.

- [ ] **Step 1: Write the failing tests**
  - `reviewTotals` rechnet die Differenz als „Ist − Plan“ und rundet wie `format.ts`.
  - `reviewOption` hat zwei Reihen, „Geplanter SOC“ und „Ist-SOC“; der geplante SOC liegt am Ende jeder Viertelstunde.
  - Hat keine Viertelstunde beides, zeigt die Karte den Leertext.
  - e2e: der Gast sieht die Karte „Plan gegen Ist (heute)“ mit einer Tabelle.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pnpm vitest run src/lib/plan`
Expected: FAIL

- [ ] **Step 3: Implement** mit den Texten aus „Texte“, danach die Vertragsdateien neu schreiben.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/integration/test_ui_contract.py -q && cd ui && pnpm check && pnpm lint && pnpm test && pnpm build && pnpm e2e`
Expected: PASS (die Demo-Welt bleibt deterministisch)

- [ ] **Step 5: Commit**

```bash
git add ui tests
git commit -m "feat(ui): Plan gegen Ist im Zeitplan"
```

### Task 16: Mehrkosten beim Boost

**Files:**
- Create: `src/bindaems/app/control/boost_cost.py`, `ui/src/lib/charging/boostCost.ts`
- Modify: `src/bindaems/app/control/routes.py` (`GET /api/control/boost-cost`), `src/bindaems/app/runtime.py` (Einstellungen, Abrechnung und cfg an den Router), `ui/src/lib/charging/VehicleCard.svelte`, `ui/src/routes/laden/+page.svelte`, `ui/src/lib/api/endpoints.ts`, `ui/src/lib/api/schemas.ts`, Vertragsdateien (`GET_ENDPOINTS["boost-cost"]`), `docs/betrieb.md` („Laderegelung“, Boost)
- Test: `tests/unit/app/control/test_boost_cost.py`, `tests/unit/app/control/test_routes.py`, `ui/src/lib/charging/VehicleCard.test.ts`

**Interfaces:**
- Consumes: `power_rate`, `power_cost_eur` (`app/tariff/calc.py:143-173`), `LedgerService.month_peak(day)`.
- Produces: `def boost_cost(cfg: Config, tariff: TariffSettings, month_peak_kw: float | None, day: date) -> dict[str, Any]` (Präzisierung 14) mit `{"applies", "month_peak_kw", "vehicles": [{"id", "extra_kw", "cost_eur"}]}`.

- [ ] **Step 1: Write the failing tests**

```python
def test_no_power_rate_means_no_cost(cfg) -> None:
    assert boost_cost(cfg, TARIFF_WITHOUT_RATE, 5.0, date(2026, 10, 9)) == {"applies": False, "month_peak_kw": 5.0, "vehicles": []}

def test_cost_is_the_rate_difference(cfg) -> None:
    # erfundener Satz 4,00 €/kW/Monat brutto ohne Stufen, Spitze 5 kW, e-Golf 16 A · 3 · 230 V = 11,04 kW
    result = boost_cost(cfg, tariff_with_rate(4.0), 5.0, date(2027, 1, 15))
    assert result["vehicles"][0] == {"id": "egolf", "extra_kw": 11.04, "cost_eur": pytest.approx(44.16)}

def test_minimum_kw_counts_as_peak(cfg) -> None: ...
```

  Vitest: die Fahrzeugkarte zeigt den Satz nur mit `applies` und nur für Bedienende.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/control/test_boost_cost.py -q`
Expected: FAIL

- [ ] **Step 3: Implement.** Schlüssel und Leistung der Fahrzeuge kommen aus denselben Werten, die `limits_json` meldet.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests -q -k "control or contract or docs" && cd ui && pnpm check && pnpm test && pnpm build`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app ui tests docs/betrieb.md
git commit -m "feat: Mehrkosten beim Boost"
```

### Task 17: Dokumentation und Stand der Spec

**Files:**
- Modify: `docs/betrieb.md` („Akku und Spitzen“: Ausführung des Plans, „Victron in den Dry-Run und live“ mit den 2 Wochen Planausführung; „Planung“: Übergabe, Stände, „Plan gegen Ist“), `docs/superpowers/specs/2026-10-08-bindaems-design.md` (paragraphs „Stand Plan 3c“ in 5.3, 7.4, 7.6, 7.7, 7.10, 12, 13, 16, 18), `docs/uebergabe.md` (Stand, „Entschieden in Plan 3c“, „Bewusst offen gelassen“, „Offen an der Anlage“)
- Test: `uv run pytest tests/unit/test_docs.py -q`

- [ ] **Step 1: Write „Stand Plan 3c“** in die genannten Abschnitte der Spec, mit den Entscheidungen und den Festlegungen der Umsetzung.
- [ ] **Step 2: Add to the guide** die Schritte an der Anlage:
  1. Ausrollen, der core startet neu.
  2. DESS für die Zeit des Dry-Runs ausschalten.
  3. Unter **System** und **Zeitplan** prüfen, dass der Plan beim core liegt und der Stand „Dry-Run“ lautet.
  4. 2 Wochen beobachten: Entscheidungen mit `plan_hold` und `plan_grid_charge`, ihre Zahl je Stunde, „Plan gegen Ist“.
  5. Danach Victron live schalten (die vier Bedingungen).
- [ ] **Step 3: Update the status in the handoff**, dazu der nächste Schritt: der Dry-Run der Planausführung und die 30 echten Tage.
- [ ] **Step 4: Run the whole repository**

Run: `uv run ruff format --check . && uv run ruff check . && uv run mypy src && uv run lint-imports && uv run pytest -q --cov && cd ui && pnpm check && pnpm lint && pnpm test && pnpm build && pnpm e2e`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docs
git commit -m "docs: Stand Plan 3c in Spec, Betriebshandbuch und Übergabe"
```

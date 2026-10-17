# Phase 3b – Optimierer und Zeitplan: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die app plant den Hausakku für bis zu 36 h und zeigt den Plan als Vorschau, ohne dass der core ihn ausführt:
- Plan-Typen in `shared` (Akku-Absichten `EIGENVERBRAUCH`, `HALTEN`, `NETZLADEN(P)`)
- Optimierer (MILP mit HiGHS über PuLP, eigener Prozess, Zeitlimit) nur für den Akku, mit Regel R1, Wirtschaftlichkeit samt Marge, Leistungspreis und Planträgheit
- Absichten aus dem Ergebnis, nachgerechnet mit einem Akkumodell im Viertelstundenraster, Plausibilitätsprüfung und Ersatzplan
- Erklärungen je Slot mit Grundcodes und deutschen Sätzen
- Planungsablauf mit Auslösern, Speicherung (`plan`, `plan_inputs`) und Schalter „Netzladen erlaubt“
- UI-Seite „Zeitplan“ und Einstellungen „Planung“; HA-Sensoren zum Plan, Schalter „Netzladen erlaubt“, Taste „Neu planen“
- Szenario-Umgebung: erzeugte Tage in der CI, Export und Vergleich mit „Victron allein“ an der Anlage

**Architecture:** 3b ändert app, `shared` und UI; der core bleibt unverändert und bekommt keinen Plan.
- **`shared/plan.py`:** `BatteryIntent`, `PlanSlot`, `Plan`, Grundcodes. Der core liest sie erst mit 3c.
- **`app/planning/`** (neu): reine Bausteine `inputs.py` (Eingangsdaten, Horizont, Restwert, Leistungspreis je Monat), `battery_model.py` (Akkumodell), `optimizer.py` (MILP), `intents.py` (Absichten aus Flüssen), `plausibility.py`, `fallback.py` (Ersatzplan), `explain.py` (Grundcodes und Sätze), `builder.py` (Plan aus Absichten: Nachrechnung, Budget, Vergleich mit „Victron allein“), `triggers.py` (Auslöser); dazu `store.py` (SQLite), `process.py` (Lösen im Kindprozess), `service.py` (Planungsdienst) und `routes.py`.
- **Ablauf:** Eingangsdaten einfrieren → MILP im Kindprozess → Absichten ableiten → mit dem Akkumodell nachrechnen → prüfen → bei Fehler Ersatzplan → Erklärungen → speichern → UI und HA.
- **Szenarien:** `tools/scenario/` (Daten, erzeugte Tage, Lauf, Bericht) und die Werkzeuge `tools/export_days.py` und `tools/scenario_days.py`; sie nutzen dieselben Bausteine wie der Planungsdienst.

**Tech Stack:** wie Plan 3a. Neu in den Extras `app`: `pulp>=3.3,<4` und `highspy>=1.8` (bringt `numpy` mit).

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, vor allem 5.5, 7.7, 9.2 bis 9.6, 11.3, 12, 13, 16 und 18.
- Verweise wie „Spec 9.3“ beziehen sich darauf. Die Absätze „Stand Plan …“ der Spec gehen dem älteren Text davor vor. Die Präzisierungen unten gehen beiden vor.
- Vorgänger ist Plan 3a (`docs/superpowers/plans/2026-10-16-phase-3a-tarif-prognosen.md`), umgesetzt auf dem Branch `claude/project-thread-syy5yu`. Seine Global Constraints und die der Pläne davor gelten weiter, ebenso seine Entscheidung 6 (Vorab-Entscheidungen für 3b und 3c, freigegeben am 16. 10. 2026).

## Einordnung

3b ist der zweite der drei Pläne der Phase 3 (Tabelle in Spec 18, „Stand Plan 3a“). Ergebnis an der Anlage: Pläne entstehen alle 15 min als Vorschau in UI und HA; der core bekommt sie nicht. Mit dem Exportwerkzeug laufen die historischen Tage der Anlage durch die Szenario-Tests; die Abnahme der Phase 3 („30 Tage, niedrigere Kosten als ‚Victron allein‘, ohne Verletzung von Regel R“) wird damit an der Anlage messbar. 3c bringt die Ausführung im core.

Stand an der Anlage laut Übergabe: 2d und 3a sind noch nicht ausgerollt. Die Szenarien an der Anlage brauchen das Prognose-Archiv aus 3a; es füllt sich erst nach dem Ausrollen (60 Tage Aufbewahrung). Die Lastprognose läuft ohne HA-Historie (Entscheidung des Betreibers vom 16. 10. 2026).

## Vorgeschlagene Entscheidungen (zur Freigabe)

Der Plan ist auf diese Vorschläge geschrieben; eine andere Wahl ändert die genannten Tasks.

1. **Absichten aus den Flüssen, dann Nachrechnung** (Tasks 5 und 7). Das MILP (Spec 9.3 ohne Autos) liefert Flüsse; je Slot wird daraus eine Absicht (Präzisierung 7), und SOC, Flüsse, Kosten und Spitze des Plans kommen aus dem Akkumodell, das die Absichten so umsetzt wie später der core. So zeigt die Vorschau, was 3c ausführt. Alternative: Betriebsart-Binärvariablen im MILP (je Slot drei mehr, schwerer zu lösen, und die Nachrechnung bräuchte es trotzdem für die Szenarien).
2. **Akkumodell im Viertelstundenraster** (Task 2), rein, für Nachrechnung, Ersatzplan, Plausibilität, Szenarien und „Victron allein“. Alternative: der Anlagensimulator im Sekundentakt (Stunden Rechenzeit je Tag, Entscheidung 6 von 3a hat ihn für die Regelung vorgesehen).
3. **Wirtschaftlichkeit als Kosten im Ziel** (Task 4): Netzladen zahlt `π + Marge`, Entladen den Verschleiß `w`; Roundtrip 88 % geteilt als `η_ch = η_dis = √0,88`. Die Spread-Regel der Spec prüft die Plausibilität nach (Präzisierung 9). Alternative: harter Vorfilter der Ladeslots (übersieht Kombinationen mehrerer teurer Slots).
4. **Einstellungen „Planung“** in den Laufzeit-Einstellungen (Task 1): `grid_charge_allowed` (aus), `margin_ct` 2,0, `roundtrip_pct` 88, `wear_ct` 2,0, `end_value_factor` 0,9, `inertia_ct` 0,5. Admins ändern alles im Editor; „Netzladen erlaubt“ schaltet auch „Bedienen“ im Zeitplan und Home Assistant, jeweils als neue Version mit Eintrag im Änderungsprotokoll. Alternative: Werte in `config.yaml` (Neustart je Änderung, keine Bedienung).
5. **Leistungspreis im Ziel** nur, wenn im Monat ein Leistungspreis mit Satz gilt (Task 3). `peak₀ = max(bisherige Monatsspitze, minimum_kw)`; ein Horizont über das Monatsende bekommt für den neuen Monat eine zweite Spitze mit `peak₀ = minimum_kw`. Alternative: Spitze immer im Ziel (bis 2027 und ohne Satz eine Kostenbremse ohne Grund).
6. **Horizont** vom aktuellen Slot bis zum letzten bekannten Preis, höchstens 36 h; unter 4 h bekannt → auf 4 h mit Schätzpreisen verlängert und als „geschätzt“ markiert. Der Restwert `v_end` kommt aus dem Median der Preise der 24 h nach dem Horizont (Präzisierung 4).
7. **Eigener Prozess und Zeitlimit:** HiGHS mit 20 s und MIP-Gap 0,5 %, je Lauf ein frischer Kindprozess (`spawn`), nach 30 s beendet; dann Ersatzplan (Präzisierung 13). Alternative: Lösen im Prozess der app (ein hängender Solver blockiert einen Thread bis zum Neustart).
8. **Auslöser (Spec 9.5) ohne die der Autos** (Phase 4): jede Viertelstunde eine Minute nach Slotbeginn, neue Preise, neue PV- oder Lastprognose, SOC mehr als 5 Prozentpunkte vom Plan weg, neue Monatsspitze, geänderte Einstellungen zu Planung oder Tarif, „Neu planen“ in UI und HA. Ein Lauf zur Zeit; Auslöser während eines Laufs ergeben genau einen weiteren. Gültigkeit 45 min.
9. **Speicherung** in Migration `0009`: `plan` (Kopf, Slots als JSON) und `plan_inputs` (eingefrorene Eingangsdaten). Pläne 30 Tage, Eingangsdaten 7 Tage; nach 7 Tagen werden die Slots eines Plans auf die gekürzt, die er tatsächlich bestimmt hat (Präzisierung 15). Alternative ohne Kürzen: rund 190 MB je Monat in SQLite und in jeder der 14 Sicherungen.
10. **Ersatzplan** (Spec 9.4) nur für den Akku, „SOC reicht nur knapp“ genau definiert (Präzisierung 10).
11. **Erklärungen:** elf Grundcodes mit Satzvorlagen („Texte“). Der Satz entsteht beim Lesen aus Code und Parametern; gespeichert werden nur Codes und Parameter, so ändern sich Wortlaute ohne Migration.
12. **Seite „Zeitplan“** mit Kopf (Stand, Quelle, gültig bis, Kosten gegenüber „Victron allein“, Budget, Schalter, Taste), Diagramm „Preise und Prognosen“, Diagramm „Akku“ mit geplantem SOC und den Fenstern „Netzladen“ und „Halten“ und dem „Ablauf“ in Blöcken mit Satz. „Plan vs. Ist“ folgt mit 3c.
13. **Home Assistant:** Sensoren „Akku-Plan jetzt“, „Akku-Plan nächster Slot“, „Geplanter SOC“, „Budget“; Schalter „Netzladen erlaubt“; Taste „Neu planen“.
14. **Szenarien:** In der CI vier erzeugte Tagesarten (`sommer-sonnig`, `winter-spread`, `winter-leistung`, `wechselhaft`) je 2 Tage, neu geplant jede Stunde; an der Anlage die exportierten Tage, neu geplant jede Viertelstunde (Präzisierung 18). Regel-R-Verstöße zählen gegen den Plan, der zum Zeitpunkt galt; dazu eine Informationszahl „Netzladen vor tatsächlicher Einspeisung“ (Präzisierung 19).
15. **Kein Eingriff in den core**, kein Befehl an Geräte, keine Änderung an der Absicht.

## Präzisierungen gegenüber der Spec

1. **Zuschnitt.** Aus Spec 18 „Planung“ kommen in 3b Optimierer, Ersatzplan, Planträgheit und Erklärungen, die Zeitplan-Ansicht ohne „Plan vs. Ist“; aus „Netzladen“ Schalter, Marge und R1; die Szenario-Test-Umgebung. Nicht in 3b: Planausführung, Budget für den 15-min-Wächter, Unterstützungsschwelle und Akku → Auto (3c), R2, Autos und ihre Auslöser (Phase 4). Ohne Autos entfallen in Spec 9.3 `pv_ev`, `gr_ev`, `bt_ev`, `e`, `u`, `a_t` und die Fehlmenge.
2. **Plan-Typen** (`shared/plan.py`): Die Absicht je Slot ist `BatteryIntent(kind, grid_charge_w)` mit `kind` `EIGENVERBRAUCH`, `HALTEN` oder `NETZLADEN`; `grid_charge_w > 0` genau bei `NETZLADEN`. Ein Plan trägt Kopf (Zeit, Auslöser, Quelle `optimizer` oder `fallback`, Solver-Status, Grund des Ersatzplans, `valid_until`, Version der Einstellungen) und Slots mit Absicht, erwartetem SOC, Flüssen, Preisen, Kosten, Budget und Grundcodes.
3. **Einstellungen „Planung“** (`RuntimeSettings.planning`): `grid_charge_allowed` (Standard falsch), `margin_ct` (≥ 0, 2,0), `roundtrip_pct` (50–100, 88), `wear_ct` (≥ 0, 2,0), `end_value_factor` (0–1, 0,9), `inertia_ct` (≥ 0, 0,5 ct je kWh Abweichung). Gespeicherte Einstellungen ohne `planning` laden mit den Standardwerten.
4. **Horizont und Restwert (5.5, 9.2, 9.3).** Grundlage ist `PriceService.planning(aktueller Slot, + 60 h, jetzt)`.
   - Ohne Preis (auch ohne Schätzung) für den aktuellen Slot gibt es keinen Plan.
   - Bekannt sind die Slots ab dem aktuellen ohne Lücke mit Herkunft `primary` oder `fallback`, höchstens 144. Sind es weniger als 16, kommen lückenlos Slots mit Herkunft `estimated` dazu, bis 16 erreicht sind.
   - Der Horizont endet vor dem ersten Slot ohne PV- oder Lastprognose; unter 4 Slots gibt es keinen Plan.
   - `v_end = max(0, (η_dis · Median der Bezugspreise in [Horizontende, +24 h) − wear_ct) · end_value_factor)` in ct je kWh im Akku; ohne Preise dort 0.
5. **Akkugrenzen.** `lo = min(Reserve · Kap, SOC₀)`, `hi = max(soc_max · Kap, SOC₀)` (ein SOC außerhalb der Grenzen macht das Modell nicht unlösbar). In den ersten 4 Slots gelten zusätzlich die BMS-Grenzen `battery.ccl_a · battery.voltage_v` bzw. `battery.dcl_a · battery.voltage_v`, wenn beide Werte bekannt sind. Netzladen je Slot höchstens `GC_t = min(P_ch_t, max(0, max_grid_charge_setpoint_w − L_t))`, denn der Sollwert bei `NETZLADEN(P)` ist Hauslast + P (7.7). Netzbezug höchstens `grid.max_import_kw`, sonst `(fuse_a − fuse_margin_a) · voltage_nominal_v · 3`.
6. **MILP (9.3, ohne Autos).** Formulierung in Task 4. Gegenüber der Spec:
   - **R1 hart, ohne `ρ`:** Ohne Autos ist `z_t = 0` immer zulässig, `ρ` hätte keine Aufgabe. `M_t` ist die PV-Energie ab Slot t (enger als ein festes M). Die Plausibilität prüft R1 zusätzlich an der Nachrechnung.
   - **Marge als Kosten:** `m · gr_b` im Ziel (Entscheidung 3).
   - **Planträgheit:** in den ersten 4 Slots `inertia_ct · Δ · |b_t − b_prev,t| / 1000` mit der Netto-Akkuleistung `b = Laden − Entladen` des vorigen Plans (aus seiner Nachrechnung); Slots ohne vorigen Plan bleiben frei.
   - Ein Status „Zeitlimit mit Lösung“ (`sol_status` 2) gilt als Ergebnis (`time_limit`).
7. **Absichten aus den Flüssen.** Je Slot, mit dem SOC des Optimierers zu Slotbeginn:
   - `NETZLADEN(P)`, wenn `gr_b ≥ 100 W`; `P = min(100 · round(gr_b / 100), 100 · floor(GC_t / 100))`.
   - sonst `HALTEN`, wenn `Selbst ≥ 100 W` und `bt_h < 0,5 · Selbst`, mit `Selbst = min(max(0, L − PV), P_dis_t, (s_t − lo) · η_dis / Δ · 1000)`: Der Optimierer hält mehr als die Hälfte dessen zurück, was Eigenverbrauch liefern würde. Die Hälfte ist die Rundung auf die nächste ausführbare Absicht; das Beispiel „`Selbst − 100 W`“ des Auftrags würde jede Teilentladung zum Halten machen. Teilentladungen korrigiert die Neuplanung nach 15 min.
   - sonst `EIGENVERBRAUCH`.
8. **Akkumodell** (je Slot, Leistungen als Mittel, Δ = 0,25 h): `Überschuss = PV − L`.
   - `EIGENVERBRAUCH`: Überschuss lädt (höchstens `P_ch` und Platz bis `hi`), der Rest geht ins Netz; ein Fehlbetrag wird entladen (höchstens `P_dis` und Energie über `lo`), der Rest kommt aus dem Netz.
   - `HALTEN`: wie oben, aber ohne Entladen.
   - `NETZLADEN(P)`: Überschuss lädt wie oben; zusätzlich lädt das Netz `min(P, P_ch − PV-Ladung, Platz)`; ein Fehlbetrag kommt aus dem Netz, entladen wird nicht.
   - Kosten `Δ / 1000 · (Bezug · π − Einspeisung · φ)` mit `φ = 0` ohne Einspeisepreis.
9. **Plausibilität (9.5, Schritt 3).** Ein Plan des Optimierers ist unplausibel bei: Bilanz (PV und Haus) um mehr als 1 W verletzt; SOC des Optimierers oder der Nachrechnung mehr als 0,01 kWh außerhalb `[lo, hi]`; Lade- oder Entladeleistung über der Grenze oder Laden und Entladen gleichzeitig (je mehr als 1 W); `NETZLADEN` bei ausgeschaltetem Schalter; R1 an der Nachrechnung verletzt (nach einem `NETZLADEN`-Slot mehr als 0,1 kWh Einspeisung bis Horizontende, den Slot eingeschlossen); Spread-Regel (Spec 9.3, „Wirtschaftlichkeit“) für einen `NETZLADEN`-Slot nicht erfüllt: kein späterer Slot mit Entladung ≥ 100 W und `π_später · η_rt − π_t − w ≥ m`. Die Spread-Regel entfällt, wenn im Horizont ein Leistungspreis gilt, denn dann darf Netzladen auch einer Spitze ausweichen. Unplausibel → Ersatzplan mit dem ersten Befund als Grund.
10. **Ersatzplan (9.4), nur Akku.**
    - Grundlage: `EIGENVERBRAUCH` in allen Slots, nachgerechnet.
    - **Teuer** sind die Slots des heutigen lokalen Tages ab dem aktuellen Slot mit einem Bezugspreis über dem 80. Perzentil (`statistics.quantiles(…, n=10, method="inclusive")[7]`) dieser Slots. Der SOC „reicht nur knapp“, wenn die Nachrechnung in einem teuren Slot mehr als 100 W aus dem Netz bezieht, weil der Akku leer ist (Fehlbetrag nicht gedeckt).
    - **Halten:** Solange das gilt: Unter den Slots vor dem ersten solchen teuren Slot, die nicht teuer sind und in der Nachrechnung entladen, bekommt der mit dem niedrigsten Preis (bei Gleichstand der frühere) `HALTEN`; neu nachrechnen. Ende, wenn kein Kandidat bleibt.
    - **Netzladen**, wenn danach noch ein teurer Slot knapp ist, der Schalter an ist und für einen Kandidaten (nicht teuer, vor diesem Slot, nach Preis aufsteigend) gilt: Spread-Regel mit dem Preis dieses teuren Slots erfüllt und R1 an der Nachrechnung mit dem Kandidaten als `NETZLADEN` eingehalten. Dann `NETZLADEN(P)` mit `P = 100 · floor(GC_t / 100)`, neu nachrechnen, bis kein teurer Slot mehr knapp ist oder kein Kandidat bleibt.
    - **Budget:** `max(bisherige Monatsspitze, minimum_kw)` in jedem Slot eines Monats mit Leistungspreis.
11. **Budget** (akzeptierte Monatsspitze, 9.3 „Ausgabe“) beim Optimierer: je Monat mit Leistungspreis `ceil(max(Spitze des Optimierers, höchster Bezug der Nachrechnung im Monat) · 10) / 10` kW; ohne Leistungspreis leer.
12. **Erklärungen (9.6).** Je Slot ein Hauptcode, dazu `GESCHAETZT` bei Schätzpreis und `ERSATZPLAN` in Ersatzplänen (an erster Stelle). Regeln und Sätze in Task 7 und „Texte“. „Lohnt sich“ heißt: ein späterer Slot im Horizont bezieht in der Nachrechnung ≥ 100 W aus dem Netz und erfüllt die Spread-Regel mit dem Preis dieses Slots.
13. **Kindprozess statt `ProcessPoolExecutor`:** Python 3.12 kann die Arbeiter eines Pools nicht ohne private Attribute beenden (`terminate_workers` gibt es erst ab 3.14). Je Lauf startet `multiprocessing.get_context("spawn").Process`; Eingabe als JSON-Text, Ergebnis über eine `Pipe`. Der Start kostet rund 0,3 s (gemessen mit PuLP 3.3.2), bei höchstens einigen Läufen je Viertelstunde unerheblich.
14. **Auslöser über Fingerabdrücke:** Statt Rückrufe in Preisen und Prognosen zu verdrahten, prüft der Planungsdienst alle 30 s: Preise (letzter bekannter Slot und Zahl der `primary`-Slots in 48 h), `issued_at` von PV- und Lastprognose, SOC gegen den Plan, Monatsspitze gegen den Plan (mehr als 0,05 kW höher). Einstellungen kommen über `SettingsService.on_change`; ausgelöst wird nur, wenn sich `planning`, `tariff` oder `feed_in` ändern. Die Viertelstunde kommt zur Minute 1 nach Slotbeginn. Beim Start plant der Dienst einmal (`start`).
15. **Aufbewahrung:** Das Aufräumen läuft in der stündlichen Wartung der app (`_maintenance_loop`, wie die Entscheidungen). Nach 7 Tagen: `plan_inputs` gelöscht und `plan.slots` auf die Slots vor `valid_until` gekürzt (`trimmed` wahr); nach 30 Tagen der Plan gelöscht. Ein Plan hat rund 40 KB, bei 100 bis 150 Plänen am Tag.
16. **Fehlende Eingangsdaten** ergeben keinen neuen Plan, nur einen Status („Texte“): kein SOC vom core, kein Preis für den aktuellen Slot, keine PV-Prognose, keine Lastprognose, zu wenig Daten. Der letzte Plan bleibt bis `valid_until` sichtbar. Ohne Einspeisepreis (kein OeMAG-Wert) rechnet das Modell mit 0 ct.
17. **Zeitplan im UI:** Die Antwort `GET /api/plan` liefert Slots mit fertigem Satz (`text`); die Blöcke bildet das UI aus aufeinanderfolgenden Slots mit gleicher Absicht, gleichem `grid_charge_w` und gleichem Hauptcode. Der Schalter zeigt die aktuelle Einstellung, nicht die des Plans.
18. **Szenario-Lauf.** Für jeden Neuplanungszeitpunkt t (alle `step_slots` Slots):
    - SOC und Monatsspitze aus dem bisherigen Lauf, Prognose je Slot aus dem Archivwert der Horizontklasse von `slot − t` (fehlt er: die nächste vorhandene Klasse, sonst der Ist-Wert), Preise bekannt bis Ende heute, ab 14:00 Uhr lokal bis Ende morgen; danach Schätzung als Median des Bezugspreises derselben lokalen Uhrzeit an bis zu 7 Vortagen der Daten (mindestens 2 Werte).
    - Lösen im selben Prozess (`optimizer.solve`), Absichten, Ersatzplan wie im Dienst; bis zur nächsten Neuplanung gelten die Absichten des Plans auf den Ist-Werten (Akkumodell).
    - „Victron allein“: `EIGENVERBRAUCH` in allen Slots auf den Ist-Werten, gleicher Start-SOC.
    - Kosten je Lauf: Energie (Bezug − Einspeisung) plus Leistungspreis je Monat (`power_cost_eur` auf den höchsten Bezug des Laufs im Monat, mindestens die mitgegebene Spitze vor dem Lauf). Bereinigt: Optimierer-Kosten minus `(SOC_end − SOC_end,Victron) · η_dis · Median der Bezugspreise des letzten Tages`.
    - CI: stündlich (`step_slots = 4`), weil jede Viertelstunde viermal so lange liefe; die Gültigkeit von 45 min deckt die vier Slots einer Stunde (der vierte beginnt 44 min nach dem Plan). An der Anlage jede Viertelstunde.
19. **Regel R im Szenario:** Ein Verstoß ist ein angewandter `NETZLADEN`-Slot, nach dem der Plan dieses Zeitpunkts mehr als 0,1 kWh Einspeisung bis zu seinem Horizontende erwartet (dieselbe Prüfung wie die Plausibilität). Die Informationszahl „Netzladen vor tatsächlicher Einspeisung“ nennt die netzgeladene Energie an Tagen, an denen danach am selben lokalen Tag tatsächlich mehr als 0,1 kWh eingespeist wurde; sie hat keine Grenze, denn Prognosefehler machen sie unvermeidbar.

## Global Constraints

- **Gilt weiter:** alle Global Constraints aus 1a bis 3a.
- **core unverändert:** nichts unter `src/bindaems/core/`, nichts an der Absicht, kein Schreibweg. `shared/plan.py` importiert nur `pydantic` und `bindaems.shared`.
- **Zeit:** Die app nimmt ihre `Clock`; intern UTC, Monate, Tage und Uhrzeiten in Europe/Vienna (`LOCAL_TZ`). Slots über `slot_start`, `slots_between`; Horizonte über Tage mit 92 und 100 Slots müssen funktionieren.
- **Datenbank:** neue Tabellen nur über Migration `0009` (`revision = "0009"`, `down_revision = "0008"`, Datei `v0009_plans.py`), Zeitstempel dort als `String(32)`, in `db/schema.py` als `UtcDateTime`; DB-Arbeit aus asynchronem Code über `asyncio.to_thread`.
- **Solver:** PuLP mit `pulp.HiGHS(msg=False, timeLimit=20, gapRel=0.005)`; im Dienst nur im Kindprozess, in Tests und Szenarien im selben Prozess. `pulp>=3.3,<4`: PuLP 4.0 hat eine neue Oberfläche (Variablen nur über `LpProblem.add_variable`); der Plan ist auf die bisherige geschrieben. `highspy` liefert Wheels für linux x86_64 und aarch64 (manylinux 2.28) für Python 3.12, PuLP ist `py3-none-any`; das Image `python:3.12-slim` (Debian bookworm, glibc 2.36) passt. mypy: `[[tool.mypy.overrides]] module = ["pulp", "pulp.*"]`, `ignore_missing_imports = true` (PuLP hat kein `py.typed`).
- **Abhängigkeiten:** `uv add --optional app "pulp>=3.3,<4" "highspy>=1.8"`, `uv.lock` neu; in der Cloud vorher `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- **Abdeckung:** ≥ 75 % insgesamt (CI); `app/planning/` ist bis auf `service.py`, `process.py` und `routes.py` reine Rechenlogik, Ziel dort ≥ 90 % ohne neue Schwelle.
- **UI:** wie in 3a: 44 × 44 px, keine waagrechte Scrollleiste bei 360 px, `{#each}` über Texte vom Server mit dem Index als Schlüssel, valibot-Schema je Antwort, jede Vertragsdatei in `GET_ENDPOINTS`/`OTHER` und `SCHEMAS`. Jede Task lässt das ganze Repository grün, auch den UI-Vertragstest (Ruling aus 3a).
- **Texte:** exakt wie in „Texte“; Tests prüfen den Wortlaut. Zahlen in Sätzen mit Dezimalkomma (`kW`, `ct`, `kWh` mit einer Nachkommastelle, `%` ganzzahlig), Uhrzeiten lokal `HH:MM`; im UI über `format.ts`.
- **Öffentliches Repository:** keine Daten der Anlage. Die CI rechnet nur mit erzeugten Tagen; Exportdateien entstehen unter `/data` an der Anlage und werden nie eingecheckt. Tarifwerte in Tests und erzeugten Tagen sind erfundene Beispiele.

## Review Focus

1. **SOC außerhalb der Grenzen beim Planen** (unter der Reserve nach Inselbetrieb, über `soc_max` nach Kalibrierung): Erwartet: Das Modell bleibt lösbar, entlädt unter der Reserve nicht, und die Plausibilität meldet keinen Fehler für den Start-SOC. Tests: Task 2 `test_soc_below_reserve_never_discharges`, Task 4 `test_soc_below_reserve_is_feasible`, Task 5 `test_start_soc_outside_limits_is_plausible`.
2. **Zeitumstellung und Monatswechsel im Horizont** (100-Slot-Tag, Nacht zum 1. eines Monats mit Leistungspreis): Erwartet: lückenloser Horizont in UTC, zwei Spitzen mit eigenem `peak₀`, Budget je Monat. Tests: Task 3 `test_horizon_over_dst_day`, `test_peak_months_current_and_next`; Task 4 `test_month_boundary_gets_second_peak`.
3. **Unvollständige Eingangsdaten** (core getrennt, Preise nur 2 h, Prognose endet früher, kein OeMAG-Wert): Erwartet: Schätzpreise bis 4 h mit Markierung, Horizont endet an der Lücke, sonst kein neuer Plan mit klarer Meldung und der alte bleibt bis `valid_until`. Tests: Task 3 `test_short_prices_extend_to_4h_with_estimates`, `test_horizon_stops_at_missing_forecast`, `test_missing_feed_in_counts_as_zero`; Task 9 `test_unavailable_keeps_previous_plan_and_reports`.
4. **Solver hängt, stürzt ab oder findet nichts:** Erwartet: Kindprozess nach 30 s beendet, Ersatzplan mit Grund, der nächste Lauf startet normal. Tests: Task 9 `test_timeout_kills_child`, `test_failed_solver_gives_fallback`, `test_next_run_after_timeout_works`.
5. **Netzladen gegen Regel und Schalter** (Schalter aus bei negativem Preis, PV-Überschuss später, Leistungspreis ohne Satz): Erwartet: kein `NETZLADEN` bei ausgeschaltetem Schalter oder erwarteter Einspeisung, keine Spitze im Ziel ohne Satz. Tests: Task 4 `test_switch_off_never_grid_charges_even_at_negative_price`, `test_r1_blocks_grid_charge_before_pv_surplus`; Task 3 `test_missing_power_rate_has_no_peak_months`; Task 15 `r1_violations == 0` in allen Arten.

## Texte

- **Absichten:** „Eigenverbrauch“, „Halten“, „Netzladen“ (UI-Blöcke: „Netzladen {kW} kW“).
- **Quellen:** „Optimierer“, „Ersatzplan“.
- **Auslöser:** `start` „Start“, `slot` „Viertelstunde“, `prices` „neue Preise“, `pv_forecast` „neue PV-Prognose“, `load_forecast` „neue Lastprognose“, `soc` „SOC weicht ab“, `month_peak` „neue Monatsspitze“, `settings` „Einstellungen geändert“, `manual` „Neu planen“.
- **Sätze der Grundcodes** (`explain.sentence`; `{um}`, `{von}`, `{bis}` als `HH:MM`):
  - `NETZLADEN_GUENSTIG`: „Akku lädt {kw} kW aus dem Netz: jetzt {ct} ct, um {um} Uhr {ct_spaeter} ct.“
  - `HALTEN_FUER_SPITZE`: „Akku hält {soc} %, weil {von}–{bis} Uhr {ct} ct erwartet werden.“
  - `BUDGET_SPITZE`: „Akku hält {soc} %, damit die Monatsspitze unter {kw} kW bleibt.“
  - `HALTEN_RESTWERT`: „Akku hält {soc} % für die Zeit nach {um} Uhr.“
  - `PV_LADEN`: „Akku lädt {kw} kW aus PV-Überschuss.“
  - `PV_UEBERSCHUSS_ERWARTET`: „Akku deckt den Verbrauch; ab {um} Uhr lädt ihn die PV wieder.“
  - `REGEL_R_BLOCKIERT_NETZLADEN`: „Kein Netzladen: Laut Prognose speist die PV bis {um} Uhr noch {kwh} kWh ein (Regel R).“
  - `NETZLADEN_AUS`: „Netzladen würde sich lohnen (jetzt {ct} ct, um {um} Uhr {ct_spaeter} ct), ist aber ausgeschaltet.“
  - `EIGENVERBRAUCH`: „Akku deckt den Eigenverbrauch.“
  - `GESCHAETZT`: „Preis geschätzt.“
  - `ERSATZPLAN`: „Ersatzplan.“
  - Der Text eines Slots: die Sätze seiner Codes in Reihenfolge, durch ein Leerzeichen getrennt.
- **Gründe des Ersatzplans:** „Zeitlimit ohne Lösung“, „Solver ohne Lösung ({status})“, „Solver nach 30 s abgebrochen“, „Solver fehlgeschlagen: {Fehler}“, „Plan unplausibel: {Befund}“.
- **Befunde der Plausibilität:** „Bilanz verletzt um {HH:MM}“, „SOC außerhalb der Grenzen um {HH:MM}“, „Leistung über der Grenze um {HH:MM}“, „Netzladen um {HH:MM}, obwohl ausgeschaltet“, „Regel R verletzt: Netzladen um {HH:MM}, danach {kWh} kWh Einspeisung“, „Netzladen um {HH:MM} rechnet sich nicht“.
- **Komponenten-Status `planning`:** „Plan von {HH:MM} Uhr“ (ok), „Ersatzplan von {HH:MM} Uhr: {Grund}“, „Plan abgelaufen seit {HH:MM} Uhr“, „Kein Plan: {Grund}“ mit den Gründen „kein SOC vom core“, „kein Preis für den aktuellen Slot“, „keine PV-Prognose“, „keine Lastprognose“, „zu wenig Daten für einen Plan“. Vor dem ersten Lauf: „Noch kein Plan“.
- **Änderungsprotokoll** (Kommentar der Einstellungsversion): „Netzladen ein“, „Netzladen aus“.
- **Navigation:** „Zeitplan“ (Symbol `plan`) nach „Laden“.
- **Seite „Zeitplan“:**
  - Kopf: Zeile „Vorschau – der core führt den Plan noch nicht aus.“ (immer, mit und ohne Plan), „Plan von {HH:MM} Uhr“, Quelle („Optimierer“/„Ersatzplan“), „Grund: {Grund}“ beim Ersatzplan, „gültig bis {HH:MM} Uhr“ bzw. „Plan abgelaufen“, „Anlass: {Auslöser}“, „Erwartete Kosten“, „Victron allein“, „Budget“ (nur mit Leistungspreis), Schalter „Netzladen erlaubt“, Taste „Neu planen“, während eines Laufs „Plant …“, ohne Plan „Noch kein Plan.“ und der Status, Liste „Auffälligkeiten“.
  - Diagramme: „Preise und Prognosen“ mit den Reihen „Bezugspreis“, „Bezugspreis geschätzt“, „PV-Prognose“, „Lastprognose“; „Akku“ mit „Geplanter SOC“, „Reserve“ und den Fenstern „Netzladen“ und „Halten“.
  - „Ablauf“: je Block „{HH:MM}–{HH:MM}“, Absicht, „SOC {a} → {b} %“, Satz.
- **Einstellungen „Planung“:** Gruppe „Planung“, Felder „Netzladen erlaubt“, „Marge (ct/kWh)“, „Roundtrip-Wirkungsgrad (%)“, „Verschleiß (ct/kWh)“, „Vorsichtsfaktor Restwert“, „Planträgheit (ct/kWh)“; Prüftexte „Zahl ab 0, z. B. 2,0“, „Zahl von 50 bis 100“, „Zahl von 0 bis 1, z. B. 0,9“.
- **Home Assistant:** Sensor `battery_plan_now` „Akku-Plan jetzt“ und `battery_plan_next` „Akku-Plan nächster Slot“ (Zustand: Absicht; Attribute `reason` (Satz), `codes`, `planned_soc_pct`, `grid_charge_w`, `start`, `source`), Sensor `planned_soc` „Geplanter SOC“ (%, SOC am Ende des aktuellen Slots; Attribut `hourly`: `[{start, soc_pct}]` je volle Stunde), Sensor `budget` „Budget“ (kW, eine Nachkommastelle), Schalter `grid_charge` „Netzladen erlaubt“, Taste `replan` „Neu planen“. Die Sensoren `battery_plan_now`, `battery_plan_next`, `planned_soc` und `budget` tragen zusätzlich das Attribut `preview: true` (der core führt den Plan noch nicht aus).
- **Szenario-Bericht:** Titel „Szenario-Vergleich mit Victron allein“; Spalten „Tag“, „Victron allein (€)“, „Optimierer (€)“, „Differenz (€)“, „Netzladen (kWh)“, „Halten (Slots)“, „Regel R“, „Ersatzpläne“; Zeilen „Gesamt“, „Optimierer bereinigt um den End-SOC“, „Leistungspreis“, „Netzladen vor tatsächlicher Einspeisung“, „Takt: {n} min“; unter der Tabelle, wenn der Export Tage übersprungen hat, „Übersprungene Tage: {Datum, Datum, …}“ (Daten als JJJJ-MM-TT); Hinweis „Weniger als 30 Tage: Für die Abnahme der Phase 3 zählen mindestens 30.“; Ergebnis „Bestanden“ oder „Nicht bestanden“.

---

### Task 1: Plan-Typen und Einstellungen „Planung“

**Files:**
- Create: `src/bindaems/shared/plan.py`
- Modify: `src/bindaems/shared/settings.py`, `ui/src/lib/api/schemas.ts` (`planning` im Settings-Schema, `looseObject`), `ui/src/lib/api/contract/settings.json` (neu erzeugt)
- Test: `tests/unit/shared/test_plan.py` (neu), `tests/unit/shared/test_settings_planning.py` (neu)

**Interfaces:**
- Produces (`shared/plan.py`):
  - `class BatteryIntentKind(StrEnum)`: `EIGENVERBRAUCH`, `HALTEN`, `NETZLADEN` (Werte = Namen)
  - `class BatteryIntent(_Model)`: `kind: BatteryIntentKind = EIGENVERBRAUCH`, `grid_charge_w: Annotated[float, Field(ge=0, allow_inf_nan=False)] = 0.0`; Validator: `grid_charge_w > 0` genau bei `NETZLADEN`
  - `ReasonCode = Literal["NETZLADEN_GUENSTIG", "HALTEN_FUER_SPITZE", "BUDGET_SPITZE", "HALTEN_RESTWERT", "PV_LADEN", "PV_UEBERSCHUSS_ERWARTET", "REGEL_R_BLOCKIERT_NETZLADEN", "NETZLADEN_AUS", "EIGENVERBRAUCH", "GESCHAETZT", "ERSATZPLAN"]`
  - `class Reason(_Model)`: `code: ReasonCode`, `params: dict[str, float | str] = {}`
  - `PlanSource = Literal["optimizer", "fallback"]`; `PlanTrigger = Literal["start", "slot", "prices", "pv_forecast", "load_forecast", "soc", "month_peak", "settings", "manual"]`; `SolverStatus = Literal["optimal", "time_limit", "infeasible", "timeout", "error"]`
  - `class PlanSlot(_Model)`: `start: AwareDatetime`, `intent: BatteryIntent`, `soc_start_pct`, `soc_end_pct`, `pv_w`, `load_w`, `import_w`, `export_w`, `charge_pv_w`, `charge_grid_w`, `discharge_w`, `import_ct: float`, `feed_in_ct: float | None`, `estimated: bool`, `cost_ct: float`, `budget_kw: float | None`, `reasons: list[Reason]`
  - `class Plan(_Model)`: `id: int | None = None`, `created_at: AwareDatetime`, `trigger: PlanTrigger`, `source: PlanSource`, `solver_status: SolverStatus | None`, `fallback_reason: str | None`, `valid_until: AwareDatetime`, `settings_version: int`, `grid_charge_allowed: bool`, `reserve_pct: float`, `soc_max_pct: float`, `month_peak_kw: float | None`, `power_price: bool`, `expected_cost_ct: float`, `baseline_cost_ct: float`, `issues: list[str]`, `trimmed: bool = False`, `slots: list[PlanSlot]` (mindestens 1); Methoden `horizon_end -> datetime` (Property), `valid_at(ts: datetime) -> bool` (`created_at ≤ ts < valid_until`), `slot_at(ts: datetime) -> PlanSlot | None`
  - `PLAN_VALID: Final = timedelta(minutes=45)`
- Produces (`shared/settings.py`): `class PlanningSettings(_Model)` mit den Feldern aus Präzisierung 3; `RuntimeSettings.planning: PlanningSettings = Field(default_factory=PlanningSettings)`

- [ ] **Step 1: Write the failing tests**

```python
def test_netzladen_needs_power_and_others_none():
    assert BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2300).grid_charge_w == 2300
    with pytest.raises(ValidationError):
        BatteryIntent(kind=BatteryIntentKind.NETZLADEN)
    with pytest.raises(ValidationError):
        BatteryIntent(kind=BatteryIntentKind.HALTEN, grid_charge_w=100)

def test_plan_slot_lookup_and_validity():
    plan = sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00", "10:15", "10:30"])
    assert plan.valid_until == at("2026-10-16T10:46:00+00:00")
    assert plan.slot_at(at("2026-10-16T10:20:00+00:00")).start == at("2026-10-16T10:15:00+00:00")
    assert plan.slot_at(at("2026-10-16T10:45:00+00:00")) is None
    assert plan.horizon_end == at("2026-10-16T10:45:00+00:00")
    assert plan.valid_at(at("2026-10-16T10:45:59+00:00")) and not plan.valid_at(plan.valid_until)

def test_plan_roundtrips_as_json():
    plan = sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00"])
    assert Plan.model_validate_json(plan.model_dump_json()) == plan

def test_planning_defaults():
    p = PlanningSettings()
    assert (p.grid_charge_allowed, p.margin_ct, p.roundtrip_pct, p.wear_ct, p.end_value_factor, p.inertia_ct) == (
        False, 2.0, 88.0, 2.0, 0.9, 0.5)

def test_stored_settings_without_planning_load_defaults():
    assert RuntimeSettings.model_validate({}).planning == PlanningSettings()

@pytest.mark.parametrize("field,value", [("roundtrip_pct", 49.0), ("roundtrip_pct", 101.0),
                                         ("end_value_factor", 1.1), ("margin_ct", -0.1)])
def test_planning_rejects_out_of_range(field, value):
    with pytest.raises(ValidationError):
        PlanningSettings.model_validate({field: value})
```

`sample_plan(created, slots)` ist ein Helfer in `tests/plan_helpers.py` (neu, nutzen auch die Tasks 7 bis 14): Slots mit lokalen Uhrzeiten am 16. 10. 2026 (UTC = lokal − 2 h, hier vereinfacht als UTC-Uhrzeit angegeben), `EIGENVERBRAUCH`, SOC 50 %, Preise 20 ct, sonst Nullen; `valid_until = created + PLAN_VALID`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/shared/test_plan.py tests/unit/shared/test_settings_planning.py -q`
Expected: FAIL (ImportError `bindaems.shared.plan`)

- [ ] **Step 3: Implement** beide Module. `slot_at` sucht den Slot mit `start == slot_start(ts)`. Danach `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py` und im UI-Schema `RuntimeSettings` das Objekt `planning` (`v.looseObject` mit den sechs Feldern) ergänzen.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/shared tests/integration/test_ui_contract.py -q && cd ui && pnpm vitest run src/lib/api && pnpm check`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared/plan.py src/bindaems/shared/settings.py tests/plan_helpers.py tests/unit/shared ui/src/lib/api
git commit -m "feat(planung): Plan-Typen und Einstellungen der Planung"
```

### Task 2: Akkumodell

**Files:**
- Create: `src/bindaems/app/planning/__init__.py`, `src/bindaems/app/planning/battery_model.py`
- Test: `tests/unit/app/planning/test_battery_model.py`

**Interfaces:**
- Consumes: `BatteryIntent`, `BatteryIntentKind` (Task 1).
- Produces:
  - `SLOT_H: Final = 0.25`
  - `@dataclass(frozen=True) BatteryParams(capacity_kwh: float, lo_kwh: float, hi_kwh: float, eta_ch: float, eta_dis: float)`
  - `@dataclass(frozen=True) SimSlot(start: datetime, pv_w: float, load_w: float, import_ct: float, feed_in_ct: float | None, max_charge_w: float, max_discharge_w: float)`
  - `@dataclass(frozen=True) SimStep(soc_start_kwh: float, soc_end_kwh: float, import_w: float, export_w: float, charge_pv_w: float, charge_grid_w: float, discharge_w: float, cost_ct: float)` mit Property `battery_w` (`charge_pv_w + charge_grid_w − discharge_w`)
  - `eta(roundtrip_pct: float) -> float` (`sqrt(pct / 100)`)
  - `step(soc_kwh: float, slot: SimSlot, intent: BatteryIntent, params: BatteryParams) -> SimStep`
  - `simulate(soc0_kwh: float, slots: Sequence[SimSlot], intents: Sequence[BatteryIntent], params: BatteryParams) -> list[SimStep]`

- [ ] **Step 1: Write the failing tests**

```python
P = BatteryParams(capacity_kwh=10.0, lo_kwh=2.0, hi_kwh=10.0, eta_ch=0.9, eta_dis=0.9)
EV, HOLD = BatteryIntent(), BatteryIntent(kind=BatteryIntentKind.HALTEN)
def grid(w): return BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=w)
def slot(pv, load, ct=20.0, fi=5.0): return SimSlot(T0, pv, load, ct, fi, 5000.0, 5000.0)

def test_self_consumption_charges_surplus():
    s = step(5.0, slot(3000, 1000), EV, P)
    assert (s.charge_pv_w, s.export_w, s.import_w) == (2000, 0, 0)
    assert s.soc_end_kwh == pytest.approx(5.45)                       # 0,25 h · 2 kW · 0,9

def test_self_consumption_discharges_deficit():
    s = step(5.0, slot(0, 1000), EV, P)
    assert (s.discharge_w, s.import_w) == (1000, 0)
    assert s.soc_end_kwh == pytest.approx(5.0 - 0.25 / 0.9)

def test_hold_does_not_discharge_but_charges_from_pv():
    assert step(5.0, slot(0, 1000), HOLD, P).import_w == 1000
    assert step(5.0, slot(3000, 1000), HOLD, P).charge_pv_w == 2000

def test_grid_charge_adds_power_and_covers_deficit_from_grid():
    s = step(5.0, slot(0, 1000), grid(3000), P)
    assert (s.charge_grid_w, s.discharge_w, s.import_w) == (3000, 0, 4000)
    assert s.soc_end_kwh == pytest.approx(5.675)

def test_grid_charge_shares_the_charge_limit_with_pv():
    s = step(5.0, slot(5000, 1000), grid(3000), P)
    assert (s.charge_pv_w, s.charge_grid_w, s.import_w) == (4000, 1000, 1000)

def test_charge_limited_by_room():
    s = step(9.9, slot(6000, 1000), EV, P)
    assert s.charge_pv_w == pytest.approx(0.1 / 0.9 / 0.25 * 1000)
    assert s.soc_end_kwh == pytest.approx(10.0)

def test_soc_below_reserve_never_discharges():
    s = step(1.5, slot(0, 1000), EV, P)
    assert (s.discharge_w, s.import_w, s.soc_end_kwh) == (0, 1000, 1.5)

def test_cost_counts_import_and_feed_in():
    assert step(10.0, slot(3000, 1000, ct=30.0, fi=6.0), EV, P).cost_ct == pytest.approx(-0.25 * 2 * 6.0)
    assert step(2.0, slot(0, 1000, fi=None), EV, P).cost_ct == pytest.approx(0.25 * 1 * 20.0)

def test_simulate_chains_soc():
    steps = simulate(5.0, [slot(0, 1000)] * 2, [EV, EV], P)
    assert steps[1].soc_start_kwh == steps[0].soc_end_kwh
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning/test_battery_model.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierung 8. Platz in W: `max(0, hi − soc) / eta_ch / SLOT_H · 1000`; lieferbare Entladung in W: `max(0, soc − lo) · eta_dis / SLOT_H · 1000`. Kosten mit `feed_in_ct or 0.0`. Kein Zugriff auf Zeit oder Konfiguration.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/planning -q && uv run mypy src/bindaems/app/planning`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/planning tests/unit/app/planning
git commit -m "feat(planung): Akkumodell im Viertelstundenraster"
```

### Task 3: Eingangsdaten, Horizont, Restwert und Leistungspreis

**Files:**
- Create: `src/bindaems/app/planning/inputs.py`
- Modify: `src/bindaems/app/planning/battery_model.py` (`params_of`, `sim_slots`)
- Test: `tests/unit/app/planning/test_inputs.py`

**Interfaces:**
- Consumes: `PlanningSettings`, `TariffSettings`, `power_rate` (3a), `Plan`, `PlanTrigger` (Task 1), `eta`, `SimSlot`, `BatteryParams` (Task 2).
- Produces:
  - `PriceOrigin = Literal["primary", "fallback", "estimated"]`
  - `class PriceInput(_Model)`: `start: AwareDatetime`, `import_ct: float`, `feed_in_ct: float | None`, `origin: PriceOrigin`
  - `class BatteryState(_Model)`: `soc_pct`, `usable_kwh`, `reserve_pct`, `soc_max_pct`, `max_charge_w`, `max_discharge_w`, `limit_now_charge_w: float | None`, `limit_now_discharge_w: float | None`, `max_grid_charge_setpoint_w`, `grid_max_import_w`
  - `class PeakMonth(_Model)`: `month: str` (`JJJJ-MM`, lokal), `peak0_kw`, `tier_kw`, `below_ct_per_kw`, `above_ct_per_kw` (ct je kW und Monat, brutto)
  - `class SlotInput(_Model)`: `start`, `import_ct`, `feed_in_ct: float | None`, `origin: PriceOrigin`, `pv_w`, `load_w`, `prev_battery_w: float | None`
  - `class PlanInputs(_Model)`: `created_at`, `trigger: PlanTrigger`, `settings_version: int`, `planning: PlanningSettings`, `battery: BatteryState`, `month_peak_kw: float | None`, `peak_months: list[PeakMonth]`, `end_value_ct: float`, `slots: list[SlotInput]`
  - `class PlanningUnavailable(Exception)` mit `reason: str` (Wortlaute aus „Texte“, ohne „Kein Plan: “)
  - Konstanten `MAX_SLOTS = 144`, `MIN_KNOWN_SLOTS = 16`, `MIN_SLOTS = 4`, `LIMIT_NOW_SLOTS = 4`, `INERTIA_SLOTS = 4`, `END_WINDOW = timedelta(hours=24)`, `PRICE_WINDOW = timedelta(hours=60)`
  - `horizon_prices(prices: Sequence[PriceInput], now: datetime) -> tuple[list[PriceInput], list[PriceInput]]` (Horizont-Preise, Preise der 24 h danach)
  - `end_value_ct(after: Sequence[PriceInput], planning: PlanningSettings) -> float`
  - `peak_months(starts: Sequence[datetime], tariff: TariffSettings, month_peak_kw: float | None) -> list[PeakMonth]`
  - `assemble_inputs(*, now: datetime, trigger: PlanTrigger, prices: Sequence[PriceInput], pv_w: Mapping[datetime, float] | None, load_w: Mapping[datetime, float] | None, battery: BatteryState | None, planning: PlanningSettings, tariff: TariffSettings, month_peak_kw: float | None, previous: Plan | None, settings_version: int) -> PlanInputs` (wirft `PlanningUnavailable`)
  - in `battery_model.py`: `params_of(inputs: PlanInputs) -> BatteryParams` (Präzisierung 5) und `sim_slots(inputs: PlanInputs) -> list[SimSlot]` (BMS-Grenzen in den ersten `LIMIT_NOW_SLOTS`)

- [ ] **Step 1: Write the failing tests**

```python
NOW = at("2026-10-16T10:05:00+00:00")

def test_horizon_runs_to_last_known_price_max_36h():
    prices = known(NOW, slots=200) 
    horizon, after = horizon_prices(prices, NOW)
    assert len(horizon) == 144 and horizon[0].start == at("2026-10-16T10:00:00+00:00")
    assert after[0].start == horizon[-1].start + SLOT

def test_short_prices_extend_to_4h_with_estimates():
    prices = known(NOW, slots=8) + estimated(NOW + 8 * SLOT, slots=40)
    horizon, _ = horizon_prices(prices, NOW)
    assert len(horizon) == 16 and [p.origin for p in horizon[8:]] == ["estimated"] * 8

def test_no_price_for_current_slot_is_unavailable():
    with pytest.raises(PlanningUnavailable, match="kein Preis für den aktuellen Slot"):
        assemble(prices=known(NOW + SLOT, slots=20))

def test_horizon_stops_at_missing_forecast():
    inputs = assemble(prices=known(NOW, slots=40), pv=flat(NOW, 10, 0.0))
    assert len(inputs.slots) == 10

def test_too_short_horizon_is_unavailable():
    with pytest.raises(PlanningUnavailable, match="zu wenig Daten für einen Plan"):
        assemble(prices=known(NOW, slots=40), load=flat(NOW, 3, 500.0))

@pytest.mark.parametrize("field,reason", [("battery", "kein SOC vom core"), ("pv", "keine PV-Prognose"),
                                          ("load", "keine Lastprognose")])
def test_missing_inputs_are_unavailable(field, reason):
    with pytest.raises(PlanningUnavailable, match=reason):
        assemble(**{field: None})

def test_horizon_over_dst_day():
    now = at("2026-10-24T22:00:00+00:00")                                   # 25. 10. 00:00 lokal
    inputs = assemble(now=now, prices=known(now, slots=100))
    assert len(inputs.slots) == 100 and inputs.slots[-1].start == at("2026-10-25T22:45:00+00:00")

def test_end_value_from_median_after_horizon():
    after = [price(ct) for ct in (10.0, 20.0, 30.0)]
    assert end_value_ct(after, PlanningSettings()) == pytest.approx((math.sqrt(0.88) * 20 - 2) * 0.9)
    assert end_value_ct([], PlanningSettings()) == 0.0
    assert end_value_ct([price(1.0)], PlanningSettings()) == 0.0

def test_peak_months_current_and_next():
    tariff = with_power_price(value_eur=30.0, above_eur=60.0, valid_from="2027-01-01")
    starts = [at("2027-01-31T22:30:00+00:00"), at("2027-01-31T22:45:00+00:00"), at("2027-01-31T23:00:00+00:00")]
    months = peak_months(starts, tariff, month_peak_kw=3.4)
    assert [(m.month, m.peak0_kw) for m in months] == [("2027-01", 3.4), ("2027-02", 2.0)]
    assert months[0].below_ct_per_kw == pytest.approx(300.0) and months[0].above_ct_per_kw == pytest.approx(600.0)

def test_missing_power_rate_has_no_peak_months():
    assert peak_months([at("2027-03-01T10:00:00+00:00")], TariffSettings(), 5.0) == []

def test_missing_feed_in_counts_as_zero():
    inputs = assemble(prices=known(NOW, slots=20, feed_in=None))
    assert sim_slots(inputs)[0].feed_in_ct is None             # das Akkumodell rechnet mit 0 ct

def test_bms_limits_apply_to_first_hour():
    inputs = assemble(battery=state(limit_now_charge_w=3000.0))
    assert [s.max_charge_w for s in sim_slots(inputs)[3:5]] == [3000.0, 10000.0]

def test_previous_plan_battery_power_for_first_four_slots():
    inputs = assemble(previous=previous_plan(battery_w=-1500.0, slots=8))
    assert [s.prev_battery_w for s in inputs.slots[:5]] == [-1500.0] * 4 + [None]
```

`known`, `estimated`, `flat`, `price`, `state`, `with_power_price`, `previous_plan` und `assemble(**overrides)` sind Helfer im Test; `assemble` setzt Standardwerte (Akku wie `deploy/config.example.yaml`: 30 kWh, Reserve 20 %, 10 kW/12 kW, Sollwert 8000 W; PV und Last für 200 Slots). `previous_plan` nutzt `sample_plan` mit `charge_pv_w`/`discharge_w` so, dass `battery_w` passt. Die Sätze 300 und 600 ct: `30 · 100 / 12 · 1,2`, `60 · 100 / 12 · 1,2`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning/test_inputs.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierungen 4 und 5 und Entscheidung 5. Prüfreihenfolge in `assemble_inputs`: Akkuzustand, Preis des aktuellen Slots, PV, Last, Horizontlänge. `peak_months`: je lokalem Monat der Starts `power_rate(lokales Datum des ersten Slots im Monat, tariff)`; `None` oder `missing` → kein Eintrag; `peak0 = max(month_peak_kw or 0, minimum_kw)` nur für den Monat von `starts[0]`, sonst `minimum_kw`; Satz `€/kW/Jahr · 100 / 12 · vat_factor`. `prev_battery_w` aus `previous.slot_at(start)` (Netto `charge_pv_w + charge_grid_w − discharge_w`) für die ersten `INERTIA_SLOTS`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/planning -q && uv run mypy src/bindaems/app/planning`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/planning tests/unit/app/planning
git commit -m "feat(planung): Eingangsdaten mit Horizont, Restwert und Leistungspreis je Monat"
```

### Task 4: Optimierer

**Files:**
- Create: `src/bindaems/app/planning/optimizer.py`
- Modify: `pyproject.toml` (Extras `app`, mypy-Override), `uv.lock`
- Test: `tests/unit/app/planning/test_optimizer.py`, `tests/property/test_optimizer.py`

**Interfaces:**
- Consumes: `PlanInputs` (Task 3), `params_of`, `sim_slots`, `SLOT_H` (Tasks 2 und 3).
- Produces:
  - `@dataclass(frozen=True) SlotFlows(pv_h: float, pv_b: float, pv_x: float, gr_h: float, gr_b: float, bt_h: float, soc_end_kwh: float)` (W, kWh)
  - `@dataclass(frozen=True) OptimizerResult(status: SolverStatus, flows: tuple[SlotFlows, ...], peaks_kw: Mapping[str, float], objective_ct: float | None, solve_ms: int, message: str | None = None)`; `flows` leer ohne Lösung
  - `TIME_LIMIT_S: Final = 20.0`, `MIP_GAP: Final = 0.005`
  - `grid_charge_cap_w(inputs: PlanInputs, index: int) -> float` (`GC_t`; 0 bei ausgeschaltetem Schalter)
  - `solve(inputs: PlanInputs, *, time_limit_s: float = TIME_LIMIT_S, gap: float = MIP_GAP) -> OptimizerResult` (fängt jede Ausnahme: `status="error"`, `message=str(exc)`)

- [ ] **Step 1: Write the failing tests**

```python
def test_cheap_night_expensive_evening_grid_charges_when_allowed():
    inputs = scenario(prices=[10.0] * 4 + [40.0] * 4, pv=[0.0] * 8, load=[2000.0] * 8, soc_pct=20.0, grid_charge=True)
    result = solve(inputs)
    assert result.status == "optimal"
    assert sum(f.gr_b for f in result.flows[:4]) > 0 and sum(f.bt_h for f in result.flows[4:]) > 0

def test_small_spread_does_not_grid_charge():
    inputs = scenario(prices=[10.0] * 4 + [14.0] * 4, pv=[0.0] * 8, load=[2000.0] * 8, soc_pct=20.0, grid_charge=True)
    assert all(f.gr_b < 1 for f in solve(inputs).flows)          # 14 · 0,88 − 10 − 2 < 2 ct Marge

def test_switch_off_never_grid_charges_even_at_negative_price():
    inputs = scenario(prices=[-5.0] * 4 + [40.0] * 4, pv=[0.0] * 8, load=[2000.0] * 8, soc_pct=20.0, grid_charge=False)
    assert all(f.gr_b == 0 for f in solve(inputs).flows)

def test_r1_blocks_grid_charge_before_pv_surplus():
    # 10-kWh-Akku, 5 kW Ladeleistung: 8,5 kW Überschuss erzwingen später Einspeisung
    kw = dict(battery=small_battery(), soc_pct=20.0, grid_charge=True)
    blocked = scenario(prices=[5.0] * 4 + [40.0] * 4 + [8.0] * 8, pv=[0.0] * 8 + [9000.0] * 8,
                       load=[500.0] * 4 + [3000.0] * 4 + [500.0] * 8, **kw)
    free = scenario(prices=[5.0] * 4 + [40.0] * 4 + [8.0] * 8, pv=[0.0] * 16,
                    load=[500.0] * 4 + [3000.0] * 4 + [500.0] * 8, **kw)
    assert all(f.gr_b < 1 for f in solve(blocked).flows)
    assert sum(f.gr_b for f in solve(free).flows[:4]) > 0

def test_soc_below_reserve_is_feasible():
    result = solve(scenario(prices=[20.0] * 8, pv=[0.0] * 8, load=[1000.0] * 8, soc_pct=10.0))
    assert result.status == "optimal" and all(f.bt_h < 1 for f in result.flows)

def test_peak_term_limits_grid_import():
    inputs = scenario(prices=[5.0] * 4 + [40.0] * 4, pv=[0.0] * 8, load=[1000.0] * 8, soc_pct=20.0,
                      grid_charge=True, peak_months=[PeakMonth(month="2027-01", peak0_kw=2.0, tier_kw=10.0,
                                                               below_ct_per_kw=5000.0, above_ct_per_kw=5000.0)],
                      start="2027-01-15T00:00:00+00:00")
    result = solve(inputs)
    assert max(f.gr_h + f.gr_b for f in result.flows) <= 2001 and result.peaks_kw == {"2027-01": pytest.approx(2.0)}

def test_month_boundary_gets_second_peak():
    inputs = scenario(prices=[20.0] * 8, pv=[0.0] * 8, load=[3000.0] * 8, start="2027-01-31T22:00:00+00:00",
                      peak_months=[month("2027-01", 2.0), month("2027-02", 2.0)])
    assert set(solve(inputs).peaks_kw) == {"2027-01", "2027-02"}

def test_inertia_penalizes_deviation_from_previous_plan():
    base = dict(prices=[30.0] * 8, pv=[0.0] * 8, load=[1000.0] * 8, soc_pct=80.0, prev_battery_w=[0.0] * 4)
    assert all(f.bt_h < 1 for f in solve(scenario(**base, inertia_ct=100.0)).flows[:4])
    assert all(f.bt_h > 999 for f in solve(scenario(**base, inertia_ct=0.0)).flows[:4])

def test_solver_error_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(pulp.LpProblem, "solve", boom)
    result = solve(scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[500.0] * 4))
    assert (result.status, result.flows) == ("error", ()) and "boom" in result.message
```

`scenario(...)` baut `PlanInputs` direkt (Helfer in `tests/plan_helpers.py`, Standardakku wie in Task 3, Start `2026-10-16T00:00Z`, ohne Restwert und ohne Leistungspreis, wenn nicht angegeben); `small_battery()` = 10 kWh, Reserve 20 %, 5 kW laden und entladen; `month(m, peak0)` mit Sätzen 300/600 ct.

Property-Test (`tests/property/test_optimizer.py`, `max_examples=30`, `deadline=None`): 4 bis 12 Slots, Preise −5 bis 40 ct, PV 0 bis 9000 W, Last 100 bis 3000 W, SOC 10 bis 100 %, Schalter zufällig. Für jedes Ergebnis mit Lösung: SOC in `[lo − 0,01, hi + 0,01]`; `pv_x ≤ PV + 1`; nie Laden und Entladen über 1 W im selben Slot; Schalter aus → `gr_b == 0`; `gr_b_t > 1` → `Σ_{τ≥t} pv_x_τ · Δ / 1000 ≤ 0,001`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS; uv run pytest tests/unit/app/planning/test_optimizer.py tests/property/test_optimizer.py -q`
Expected: FAIL (ImportError)

- [ ] **Step 3: Add the dependencies and implement `solve`**

`uv add --optional app "pulp>=3.3,<4" "highspy>=1.8"`, mypy-Override für `pulp`. Das Modell (Leistungen W, Energie kWh, Preise ct/kWh, Δ = `SLOT_H`, `C` Kapazität, `PV_t`, `L_t` aus den Slots, `P_ch_t`, `P_dis_t` aus `sim_slots`, `π_t = import_ct`, `φ_t = feed_in_ct or 0`, `w`, `m`, `η` aus `planning`):

```text
Variablen ≥ 0 je t: pv_h, pv_b, pv_x, gr_h, gr_b ≤ GC_t, bt_h;  y_t ∈ {0,1};  z_t ∈ {0,1} nur mit Schalter
s_1..s_T ∈ [lo, hi];  s_0 = SOC₀ · C
je PeakMonth k: d1_k ∈ [0, max(0, tier_k − peak0_k)], d2_k ≥ 0
je t < 4 mit prev_t: dev_t ≥ 0

pv_h = min(PV_t, L_t)                               (PV deckt zuerst das Haus, wie im Akkumodell)
PV_t = pv_h + pv_b + pv_x
L_t  = pv_h + gr_h + bt_h
pv_b + gr_b ≤ P_ch_t · y_t ;  bt_h ≤ P_dis_t · (1 − y_t)
gr_b ≤ GC_t · z_t                                   (ohne Schalter: obere Schranke 0, kein z)
gr_h + gr_b ≤ grid_max_import_w
s_{t+1} = s_t + Δ/1000 · (η_ch · (pv_b + gr_b) − bt_h / η_dis)
R1 (mit Schalter, M_t = Σ_{τ≥t} Δ/1000 · PV_τ > 0):  Σ_{τ≥t} Δ/1000 · pv_x_τ ≤ M_t · (1 − z_t)
Spitze, t im Monat k:  (gr_h + gr_b) / 1000 ≤ peak0_k + d1_k + d2_k
Trägheit:  dev_t ≥ (pv_b + gr_b − bt_h) − prev_t ;  dev_t ≥ prev_t − (pv_b + gr_b − bt_h)

min  Σ_t Δ/1000 · (π_t · (gr_h + gr_b) − φ_t · pv_x + w · bt_h + m · gr_b)
   + Σ_k (below_k · d1_k + above_k · d2_k)
   + Σ_t Δ/1000 · inertia_ct · dev_t
   − end_value_ct · s_T
```

Status: `prob.sol_status` 1 → `optimal`, 2 → `time_limit`; sonst `infeasible` bei `prob.status == -1`, sonst `error` mit `pulp.LpStatus[status]` als `message`. `peaks_kw[k] = peak0_k + d1_k + d2_k`. `solve_ms` über `time.perf_counter`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv sync --all-extras && uv run pytest tests/unit/app/planning tests/property/test_optimizer.py -q && uv run mypy src`
Expected: PASS (die Datei unter 30 s)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock src/bindaems/app/planning/optimizer.py tests/plan_helpers.py tests/unit/app/planning tests/property/test_optimizer.py
git commit -m "feat(planung): Optimierer für den Akku mit Regel R1, Marge und Leistungspreis"
```

### Task 5: Absichten aus den Flüssen und Plausibilitätsprüfung

**Files:**
- Create: `src/bindaems/app/planning/intents.py`, `src/bindaems/app/planning/plausibility.py`
- Test: `tests/unit/app/planning/test_intents.py`, `tests/unit/app/planning/test_plausibility.py`

**Interfaces:**
- Consumes: `OptimizerResult`, `SlotFlows`, `grid_charge_cap_w` (Task 4), `SimStep`, `params_of`, `sim_slots` (Tasks 2, 3).
- Produces:
  - `GRID_CHARGE_MIN_W = 100.0`, `ROUND_W = 100.0`, `HOLD_SHARE = 0.5`, `HOLD_MIN_W = 100.0`
  - `derive_intents(inputs: PlanInputs, result: OptimizerResult) -> list[BatteryIntent]` (Präzisierung 7)
  - `BALANCE_TOL_W = 1.0`, `SOC_TOL_KWH = 0.01`, `R1_TOL_KWH = 0.1`, `USEFUL_W = 100.0`
  - `export_after_kwh(steps: Sequence[SimStep], index: int) -> float` (Einspeisung ab `index` bis zum Ende, Slot eingeschlossen)
  - `pays_off(inputs: PlanInputs, steps: Sequence[SimStep], index: int) -> tuple[int, float] | None` (späterer Slot und sein Preis, mit dem die Spread-Regel für `index` gilt; der teuerste solche Slot mit Netzbezug bzw. Entladung ≥ `USEFUL_W`, bei Gleichstand der frühere; `None` sonst)
  - `check_plan(inputs: PlanInputs, result: OptimizerResult, intents: Sequence[BatteryIntent], steps: Sequence[SimStep]) -> list[str]` (Befunde aus „Texte“, in Slot-Reihenfolge)

- [ ] **Step 1: Write the failing tests**

```python
def test_grid_charge_becomes_netzladen_rounded():
    inputs = scenario(prices=[10.0] * 3, pv=[0.0] * 3, load=[1000.0] * 3, grid_charge=True)
    result = flows_result([flow(gr_b=2340), flow(gr_b=60), flow(gr_b=7100)])   # GC = min(10000, 8000 − 1000)
    kinds = derive_intents(inputs, result)
    assert kinds[0] == BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2300)
    assert kinds[1].kind == BatteryIntentKind.EIGENVERBRAUCH
    assert kinds[2].grid_charge_w == 7000

@pytest.mark.parametrize("bt_h,kind", [(0.0, "HALTEN"), (900.0, "HALTEN"), (1000.0, "EIGENVERBRAUCH"),
                                       (2000.0, "EIGENVERBRAUCH")])
def test_withheld_discharge_becomes_halten(bt_h, kind):
    inputs = scenario(prices=[20.0], pv=[0.0], load=[2000.0], soc_pct=80.0)
    assert derive_intents(inputs, flows_result([flow(bt_h=bt_h, gr_h=2000 - bt_h)]))[0].kind == kind

def test_small_deficit_and_empty_battery_stay_eigenverbrauch():
    assert derive_intents(scenario(prices=[20.0], pv=[0.0], load=[80.0], soc_pct=80.0),
                          flows_result([flow(gr_h=80)]))[0].kind == BatteryIntentKind.EIGENVERBRAUCH
    assert derive_intents(scenario(prices=[20.0], pv=[0.0], load=[2000.0], soc_pct=20.0),
                          flows_result([flow(gr_h=2000)]))[0].kind == BatteryIntentKind.EIGENVERBRAUCH

def test_clean_plan_has_no_issues():
    inputs = scenario(prices=[10.0] * 4 + [40.0] * 4, pv=[0.0] * 8, load=[2000.0] * 8, soc_pct=20.0, grid_charge=True)
    result = solve(inputs)
    intents = derive_intents(inputs, result)
    assert check_plan(inputs, result, intents, resimulate(inputs, intents)) == []

def test_start_soc_outside_limits_is_plausible():
    inputs = scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[1000.0] * 4, soc_pct=10.0)
    result = solve(inputs)
    intents = derive_intents(inputs, result)
    assert check_plan(inputs, result, intents, resimulate(inputs, intents)) == []

def test_grid_charge_when_switched_off_is_reported():
    inputs = scenario(prices=[10.0], pv=[0.0], load=[1000.0], grid_charge=False)
    intents = [BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=1000)]
    issues = check_plan(inputs, flows_result([flow(gr_h=1000)]), intents, resimulate(inputs, intents))
    assert "Netzladen um 02:00, obwohl ausgeschaltet" in issues

def test_r1_on_resimulation_is_reported():
    inputs = scenario(prices=[5.0, 8.0], pv=[0.0, 9000.0], load=[500.0, 500.0], battery=small_battery(),
                      soc_pct=90.0, grid_charge=True)
    intents = [BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=1000), BatteryIntent()]
    issues = check_plan(inputs, flows_result([flow(gr_h=500, gr_b=1000), flow(pv_h=500, pv_x=8500)]),
                        intents, resimulate(inputs, intents))
    assert any(i.startswith("Regel R verletzt: Netzladen um 02:00, danach ") for i in issues)

def test_unprofitable_grid_charge_is_reported_without_power_price():
    inputs = scenario(prices=[10.0, 12.0], pv=[0.0, 0.0], load=[1000.0, 1000.0], soc_pct=20.0, grid_charge=True)
    intents = [BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=2000), BatteryIntent()]
    steps = resimulate(inputs, intents)
    assert "Netzladen um 02:00 rechnet sich nicht" in check_plan(inputs, flows_of(steps), intents, steps)
    with_peak = inputs.model_copy(update={"peak_months": [month("2026-10", 2.0)]})
    assert "Netzladen um 02:00 rechnet sich nicht" not in check_plan(with_peak, flows_of(steps), intents, steps)
```

`flow(**w)` füllt fehlende Flüsse mit 0 und `soc_end_kwh` passend; `flows_result(flows)` ist ein `OptimizerResult` mit `status="optimal"`; `resimulate(inputs, intents)` = `simulate(SOC₀, sim_slots(inputs), intents, params_of(inputs))`; `flows_of(steps)` baut die Flüsse aus der Nachrechnung. Die Uhrzeiten in den Befunden sind lokal (Start `2026-10-16T00:00Z` = 02:00).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning/test_intents.py tests/unit/app/planning/test_plausibility.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierungen 7 und 9. In `derive_intents` ist `s_t` der SOC zu Slotbeginn (`SOC₀ · C` bzw. `flows[t−1].soc_end_kwh`). `check_plan` prüft den SOC der Nachrechnung mit `lo`, `hi` aus `params_of` (die schon den Start-SOC einschließen) und meldet je Art nur den ersten Slot. Die Spread-Regel nutzt `pays_off`; mit `peak_months` entfällt sie.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/planning -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/planning tests/plan_helpers.py tests/unit/app/planning
git commit -m "feat(planung): Akku-Absichten aus den Flüssen und Plausibilitätsprüfung"
```

### Task 6: Ersatzplan

**Files:**
- Create: `src/bindaems/app/planning/fallback.py`
- Test: `tests/unit/app/planning/test_fallback.py`

**Interfaces:**
- Consumes: `simulate`, `params_of`, `sim_slots` (Tasks 2, 3), `export_after_kwh`, `R1_TOL_KWH`, `grid_charge_cap_w` (Tasks 4, 5).
- Produces:
  - `HIGH_PERCENTILE_INDEX = 7` (80. Perzentil aus `quantiles(n=10)`), `SHORT_W = 100.0`
  - `expensive_today(inputs: PlanInputs) -> list[int]` (Indizes)
  - `fallback_intents(inputs: PlanInputs) -> list[BatteryIntent]` (Präzisierung 10)

- [ ] **Step 1: Write the failing tests**

```python
EVENING = [12.0] * 8 + [40.0] * 4                       # 02:00–04:00 billig, 04:00–05:00 teuer (lokal)

def test_default_is_eigenverbrauch():
    inputs = scenario(prices=[20.0] * 8, pv=[0.0] * 8, load=[500.0] * 8, soc_pct=80.0)
    assert {i.kind for i in fallback_intents(inputs)} == {BatteryIntentKind.EIGENVERBRAUCH}

def test_holds_cheapest_discharging_slots_when_soc_short_for_expensive_evening():
    prices = [14.0, 12.0, 13.0, 12.5, 15.0, 15.0, 15.0, 15.0] + [40.0] * 4
    inputs = scenario(prices=prices, pv=[0.0] * 12, load=[2000.0] * 12, battery=small_battery(), soc_pct=40.0)
    intents = fallback_intents(inputs)                   # 2 kWh über der Reserve, der Abend braucht 2 kWh
    held = [i for i, x in enumerate(intents) if x.kind == BatteryIntentKind.HALTEN]
    assert held[:2] == [1, 3] and all(x.kind != BatteryIntentKind.HALTEN for x in intents[8:])

def test_no_hold_when_soc_suffices():
    inputs = scenario(prices=EVENING, pv=[0.0] * 12, load=[1000.0] * 12, soc_pct=90.0)
    assert all(i.kind == BatteryIntentKind.EIGENVERBRAUCH for i in fallback_intents(inputs))

def test_grid_charge_needs_switch_spread_and_r1():
    base = dict(prices=[8.0] * 8 + [40.0] * 4, load=[2000.0] * 12, battery=small_battery(), soc_pct=20.0)
    on = fallback_intents(scenario(pv=[0.0] * 12, grid_charge=True, **base))
    off = fallback_intents(scenario(pv=[0.0] * 12, grid_charge=False, **base))
    assert on[0] == BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=5000)
    assert all(i.kind != BatteryIntentKind.NETZLADEN for i in off)

def test_only_today_counts():
    inputs = scenario(prices=[20.0] * 88 + [60.0] * 8, pv=[0.0] * 96, load=[1500.0] * 96,
                      soc_pct=30.0, start="2026-10-16T20:00:00+00:00")          # 22:00 lokal: teuer erst morgen
    assert all(i.kind == BatteryIntentKind.EIGENVERBRAUCH for i in fallback_intents(inputs))
```

Im ersten Halten-Test sind die billigsten entladenden Slots vor dem Abend 12,0 ct (Index 1) und 12,5 ct (Index 3); das Halten endet, sobald die Nachrechnung im teuren Block nicht mehr knapp ist. `P = 5000`: `GC = min(5000, 8000 − 2000)`, auf 100 W abgerundet.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning/test_fallback.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierung 10. „Heute“ ist der lokale Tag von `inputs.slots[0].start`; die Schleifen enden spätestens nach so vielen Durchläufen, wie der Horizont Slots hat.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/planning -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/planning/fallback.py tests/unit/app/planning/test_fallback.py
git commit -m "feat(planung): regelbasierter Ersatzplan für den Akku"
```

### Task 7: Erklärungen und Plan aus Absichten

**Files:**
- Create: `src/bindaems/app/planning/explain.py`, `src/bindaems/app/planning/builder.py`
- Test: `tests/unit/app/planning/test_explain.py`, `tests/unit/app/planning/test_builder.py`

**Interfaces:**
- Consumes: Typen aus Task 1, `simulate` (Task 2), `pays_off`, `export_after_kwh`, `USEFUL_W` (Task 5).
- Produces:
  - `reasons_for(inputs: PlanInputs, intents: Sequence[BatteryIntent], steps: Sequence[SimStep], budgets: Sequence[float | None], *, fallback: bool) -> list[list[Reason]]`
  - `sentence(reason: Reason) -> str`; `slot_text(slot: PlanSlot) -> str`
  - `build_plan(inputs: PlanInputs, intents: Sequence[BatteryIntent], *, source: PlanSource, solver_status: SolverStatus | None, fallback_reason: str | None = None, issues: Sequence[str] = (), optimizer_peaks_kw: Mapping[str, float] | None = None) -> Plan` (Nachrechnung, Budget nach Präzisierung 10 bzw. 11, Gründe, `expected_cost_ct` = Summe der Slot-Kosten, `baseline_cost_ct`, `valid_until = created_at + PLAN_VALID`)
  - `baseline_cost_ct(inputs: PlanInputs) -> float` (alle Slots `EIGENVERBRAUCH`)

Regeln des Hauptcodes je Slot (erste passende):
- `NETZLADEN` → `NETZLADEN_GUENSTIG` {`kw` = P/1000, `ct` = Preis, `um`, `ct_spaeter` aus `pays_off`; ohne Treffer der teuerste spätere Slot}
- `HALTEN` → `HALTEN_FUER_SPITZE`, wenn später ein Block zusammenhängender Slots mit Entladung ≥ 100 W und höherem Preis folgt {`soc` = SOC am Slotende, `von`/`bis` = erster solcher Block, `ct` = sein höchster Preis}; sonst `BUDGET_SPITZE`, wenn ein Budget gilt und später ein Slot mit `L − PV > Budget` entlädt {`soc`, `kw` = Budget}; sonst `HALTEN_RESTWERT` {`soc`, `um` = Horizontende}
- `EIGENVERBRAUCH` → `PV_LADEN`, wenn PV-Ladung ≥ 100 W {`kw`}; sonst `REGEL_R_BLOCKIERT_NETZLADEN`, wenn der Schalter an ist, `pays_off` trifft und `export_after_kwh > R1_TOL_KWH` {`um` = Horizontende, `kwh`}; sonst `NETZLADEN_AUS`, wenn der Schalter aus ist und `pays_off` trifft {`ct`, `um`, `ct_spaeter`}; sonst `PV_UEBERSCHUSS_ERWARTET`, wenn der Slot ≥ 100 W entlädt und später PV-Ladung ≥ 100 W folgt {`um` = deren erster Slot}; sonst `EIGENVERBRAUCH`

- [ ] **Step 1: Write the failing tests**

```python
def test_sentences():
    assert sentence(Reason(code="NETZLADEN_GUENSTIG", params={"kw": 2.3, "ct": 10.0, "um": "18:00", "ct_spaeter": 40.0})) == \
        "Akku lädt 2,3 kW aus dem Netz: jetzt 10,0 ct, um 18:00 Uhr 40,0 ct."
    assert sentence(Reason(code="HALTEN_FUER_SPITZE", params={"soc": 64.4, "von": "18:00", "bis": "20:00", "ct": 38.0})) == \
        "Akku hält 64 %, weil 18:00–20:00 Uhr 38,0 ct erwartet werden."
    assert sentence(Reason(code="REGEL_R_BLOCKIERT_NETZLADEN", params={"um": "12:00", "kwh": 7.25})) == \
        "Kein Netzladen: Laut Prognose speist die PV bis 12:00 Uhr noch 7,3 kWh ein (Regel R)."

def test_every_code_has_a_sentence():
    for code in get_args(ReasonCode):
        assert sentence(Reason(code=code, params=SAMPLE_PARAMS[code])).endswith(".")

def test_halten_names_the_expensive_block():
    inputs = scenario(prices=[20.0] * 4 + [38.0] * 8, pv=[0.0] * 12, load=[1000.0] * 12, soc_pct=50.0)
    plan = build_plan(inputs, [HOLD] * 4 + [EV] * 8, source="optimizer", solver_status="optimal")
    assert plan.slots[0].reasons[0].code == "HALTEN_FUER_SPITZE"
    assert slot_text(plan.slots[0]).startswith("Akku hält 50 %, weil 03:00–05:00 Uhr 38,0 ct")

def test_estimated_price_and_fallback_add_codes():
    inputs = scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[500.0] * 4, origins=["primary"] * 3 + ["estimated"])
    plan = build_plan(inputs, [EV] * 4, source="fallback", solver_status="timeout",
                      fallback_reason="Solver nach 30 s abgebrochen")
    assert [r.code for r in plan.slots[3].reasons] == ["ERSATZPLAN", "EIGENVERBRAUCH", "GESCHAETZT"]
    assert plan.slots[3].estimated and slot_text(plan.slots[3]).endswith("Preis geschätzt.")

def test_switched_off_grid_charge_is_explained():
    inputs = scenario(prices=[10.0] * 4 + [40.0] * 4, pv=[0.0] * 8, load=[2000.0] * 8, soc_pct=20.0)
    plan = build_plan(inputs, [EV] * 8, source="optimizer", solver_status="optimal")
    assert plan.slots[0].reasons[0].code == "NETZLADEN_AUS"

def test_build_plan_resimulates_and_sets_budget():
    inputs = scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[3000.0, 3000.0, 4210.0, 3000.0], soc_pct=20.0,
                      peak_months=[month("2026-10", 2.0)])
    plan = build_plan(inputs, [EV] * 4, source="optimizer", solver_status="optimal", optimizer_peaks_kw={"2026-10": 3.5})
    assert [s.budget_kw for s in plan.slots] == [4.3] * 4                # ceil(4,21 · 10) / 10
    assert plan.slots[2].import_w == 4210 and plan.power_price

def test_fallback_budget_is_previous_peak():
    inputs = scenario(prices=[20.0] * 2, pv=[0.0] * 2, load=[5000.0] * 2, peak_months=[month("2026-10", 3.2)])
    plan = build_plan(inputs, [EV] * 2, source="fallback", solver_status="error", fallback_reason="x")
    assert [s.budget_kw for s in plan.slots] == [3.2, 3.2]

def test_baseline_cost_is_eigenverbrauch_everywhere():
    inputs = scenario(prices=[10.0] * 4 + [40.0] * 4, pv=[0.0] * 8, load=[2000.0] * 8, soc_pct=20.0)
    plan = build_plan(inputs, [HOLD] * 8, source="optimizer", solver_status="optimal")
    assert plan.baseline_cost_ct == pytest.approx(baseline_cost_ct(inputs)) == pytest.approx(0.25 * 2 * 4 * (10 + 40))
```

`SAMPLE_PARAMS` gibt je Code Parameter vor. Die Zahlenformatierung („64“, „7,3“) über eine Hilfsfunktion `_de(value, digits)` in `explain.py` (halbe Werte runden wie `round`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning/test_explain.py tests/unit/app/planning/test_builder.py -q`
Expected: FAIL

- [ ] **Step 3: Implement.** `build_plan` füllt `reserve_pct`, `soc_max_pct`, `month_peak_kw`, `settings_version`, `grid_charge_allowed` (aus `inputs.planning`) und `trigger` aus den Eingangsdaten; `power_price = bool(inputs.peak_months)`. Die Gründe entstehen nach dem Budget, denn `BUDGET_SPITZE` braucht es.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/planning -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/planning tests/unit/app/planning
git commit -m "feat(planung): Erklärungen je Slot und Plan aus nachgerechneten Absichten"
```

### Task 8: Speicherung

**Files:**
- Create: `src/bindaems/app/planning/store.py`, `src/bindaems/app/db/migrations/versions/v0009_plans.py`
- Modify: `src/bindaems/app/db/schema.py`
- Test: `tests/unit/app/planning/test_store.py`, `tests/unit/app/test_db.py`

**Interfaces:**
- Consumes: `Plan` (Task 1), `PlanInputs` (Task 3).
- Produces:
  - Tabelle `plan`: `id` (Integer, PK, autoincrement), `created_at`, `trigger` (String 16), `source` (String 10), `solver_status` (String 12, leer erlaubt), `fallback_reason` (String 200, leer erlaubt), `valid_until`, `settings_version` (Integer), `expected_cost_ct` (Float), `trimmed` (Boolean, Standard falsch), `data` (JSON: `Plan` ohne `id`); Index auf `created_at`
  - Tabelle `plan_inputs`: `plan_id` (Integer, PK), `data` (JSON: `PlanInputs`)
  - `KEEP_PLANS = timedelta(days=30)`, `KEEP_INPUTS = timedelta(days=7)`
  - `PlanStore(engine: Engine)` mit `save(plan: Plan, inputs: PlanInputs) -> Plan` (mit `id`), `latest() -> Plan | None`, `inputs_of(plan_id: int) -> PlanInputs | None`, `cleanup(now: datetime) -> tuple[int, int]` (gelöscht, gekürzt)

- [ ] **Step 1: Write the failing tests**

```python
def test_save_and_latest_roundtrip(store):
    first = store.save(sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00"]), sample_inputs())
    second = store.save(sample_plan(created="2026-10-16T10:16:00+00:00", slots=["10:15"]), sample_inputs())
    assert second.id == first.id + 1 and store.latest() == second
    assert store.inputs_of(first.id) == sample_inputs()

def test_latest_without_plan_is_none(store):
    assert store.latest() is None

def test_cleanup_trims_after_7_days_and_deletes_after_30(store):
    old = store.save(sample_plan(created="2026-09-10T10:01:00+00:00", slots=["10:00"]), sample_inputs())
    week = store.save(sample_plan(created="2026-10-08T10:01:00+00:00", slots=["10:00", "10:15", "10:30", "10:45"]),
                      sample_inputs())
    assert store.cleanup(at("2026-10-16T10:00:00+00:00")) == (1, 1)
    trimmed = store.latest()
    assert trimmed.id == week.id and trimmed.trimmed and [s.start.minute for s in trimmed.slots] == [0, 15, 30]
    assert store.inputs_of(week.id) is None and store.inputs_of(old.id) is None
    assert store.cleanup(at("2026-10-16T10:00:00+00:00")) == (0, 0)
```

`sample_inputs()` in `tests/plan_helpers.py`. Gekürzt bleiben die Slots mit `start < valid_until` (10:46 → 10:00, 10:15, 10:30). In `test_db.py`: Migration `0009` legt beide Tabellen an und lässt sich auf `0008` zurücknehmen.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning/test_store.py tests/unit/app/test_db.py -q`
Expected: FAIL

- [ ] **Step 3: Implement.** `save` schreibt beide Zeilen in einer Transaktion; `cleanup` löscht zuerst Pläne vor `now − KEEP_PLANS` samt Eingangsdaten, kürzt dann ungekürzte Pläne vor `now − KEEP_INPUTS` und löscht deren Eingangsdaten.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/planning/store.py src/bindaems/app/db tests/plan_helpers.py tests/unit/app/planning/test_store.py tests/unit/app/test_db.py
git commit -m "feat(planung): Pläne und Eingangsdaten in SQLite mit Aufbewahrung"
```

### Task 9: Lösen im Kindprozess und ein Planungslauf

**Files:**
- Create: `src/bindaems/app/planning/process.py`, `src/bindaems/app/planning/service.py`
- Test: `tests/unit/app/planning/test_process.py`, `tests/unit/app/planning/test_service.py`

**Interfaces:**
- Consumes: alles aus den Tasks 3 bis 8; `PriceService.planning`, `ForecastService.latest`, `LoadForecastService.latest`, `LedgerService.month_peak`, `LiveState.value`, `SettingsService`.
- Produces:
  - `Solver = Callable[[PlanInputs], Awaitable[OptimizerResult]]`
  - `OUTER_TIMEOUT_S: Final = 30.0`
  - `async solve_in_subprocess(inputs: PlanInputs, *, timeout_s: float = OUTER_TIMEOUT_S, target: Callable[[Connection, str], None] = child_main) -> OptimizerResult` (bei Zeitüberschreitung `status="timeout"`, Kind beendet; stirbt das Kind ohne Antwort `status="error"`)
  - `child_main(conn: Connection, inputs_json: str) -> None` (Modulebene, für `spawn`)
  - `async solve_inline(inputs: PlanInputs) -> OptimizerResult` (`asyncio.to_thread(solve, inputs)`; Tests, Demo, Szenarien)
  - `@dataclass(frozen=True) PlanningStatus(running: bool, unavailable: str | None, last_error: str | None)`
  - `PlanningService(cfg: Config, settings: SettingsService, prices: PriceService, forecast: ForecastService, load_forecast: LoadForecastService, ledger: LedgerService, live: LiveState, store: PlanStore, clock: Clock, *, solver: Solver = solve_in_subprocess, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep)` mit `current() -> Plan | None` (beim Bau `store.latest()`), `async collect(trigger: PlanTrigger) -> PlanInputs`, `async replan(trigger: PlanTrigger) -> Plan | None`, `status() -> PlanningStatus`, `component_status() -> ComponentStatus` (Name `planning`)
  - `plan_once(inputs: PlanInputs, result: OptimizerResult) -> Plan` (rein, in `service.py`: Absichten, Nachrechnung, Plausibilität, sonst Ersatzplan mit Grund; nutzen auch die Szenarien)

- [ ] **Step 1: Write the failing tests**

```python
async def test_subprocess_solves():
    result = await solve_in_subprocess(small_inputs())
    assert result.status == "optimal" and len(result.flows) == 4

async def test_timeout_kills_child():
    started = time.monotonic()
    result = await solve_in_subprocess(small_inputs(), timeout_s=0.5, target=sleep_forever)
    assert result.status == "timeout" and time.monotonic() - started < 5
    assert not [p for p in multiprocessing.active_children() if p.is_alive()]

async def test_next_run_after_timeout_works():
    await solve_in_subprocess(small_inputs(), timeout_s=0.5, target=sleep_forever)
    assert (await solve_in_subprocess(small_inputs())).status == "optimal"

async def test_replan_stores_optimizer_plan(service, store):
    plan = await service.replan("manual")
    assert plan.source == "optimizer" and plan.trigger == "manual" and store.latest() == plan == service.current()
    assert store.inputs_of(plan.id).settings_version == plan.settings_version

async def test_failed_solver_gives_fallback(service_with):
    service = service_with(solver=returns(OptimizerResult("timeout", (), {}, None, 30000)))
    plan = await service.replan("slot")
    assert (plan.source, plan.fallback_reason) == ("fallback", "Solver nach 30 s abgebrochen")
    assert service.component_status().message == "Ersatzplan von 12:01 Uhr: Solver nach 30 s abgebrochen"

async def test_implausible_result_gives_fallback(service_with):
    service = service_with(solver=returns_flows_with_soc_below_reserve())
    plan = await service.replan("slot")
    assert plan.source == "fallback" and plan.fallback_reason.startswith("Plan unplausibel: SOC außerhalb der Grenzen um ")

async def test_unavailable_keeps_previous_plan_and_reports(service, live):
    first = await service.replan("start")
    live.connected = False
    assert await service.replan("slot") is None
    assert service.current() == first
    assert service.component_status().message == "Kein Plan: kein SOC vom core"

async def test_collect_uses_bms_limits_and_month_peak(service, live, ledger):
    live_signals(live, {"battery.soc_pct": 55.0, "battery.ccl_a": 50.0, "battery.voltage_v": 52.0})
    ingest(ledger, "2026-10-16T08:00:00+00:00", {"grid>house": 1000})        # 4 kW
    inputs = await service.collect("manual")
    assert inputs.battery.limit_now_charge_w == 2600.0 and inputs.month_peak_kw == 4.0
```

`service` (Fixture in `tests/unit/app/planning/conftest.py`): echte Dienste der app wie in den 3a-Tests mit `ManualClock` auf `2026-10-16T10:01Z` (12:01 lokal), Preisen für 24 h, einer PV- und Lastprognose (Fakes mit `latest()`), `LiveState` mit SOC 50 % und `solver=solve_inline`. `sleep_forever(conn, text)` ist eine Funktion auf Modulebene im Testmodul (`spawn` braucht sie importierbar). `returns(result)` liefert einen Solver, der das Ergebnis zurückgibt.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning/test_process.py tests/unit/app/planning/test_service.py -q`
Expected: FAIL

- [ ] **Step 3: Implement.**
  - `solve_in_subprocess`: `ctx = multiprocessing.get_context("spawn")`, `Pipe(duplex=False)`, `Process(target=target, args=(child_conn, inputs.model_dump_json()), daemon=True)`; Warten über `asyncio.to_thread(parent.poll, timeout_s)`; danach `terminate()`, `join(2)`, falls nötig `kill()`. Das Kind sendet das `OptimizerResult` (Dataclass, picklebar).
  - `collect`: in einem Worker-Thread `prices.planning(slot_start(now), slot_start(now) + PRICE_WINDOW, now)` → `PriceInput`; `ledger.month_peak(lokales Datum)`; Akkuzustand aus `live.value("battery.soc_pct")`, BMS aus `battery.ccl_a`, `battery.dcl_a`, `battery.voltage_v`, Grenzen aus `cfg.battery`, `cfg.victron.max_grid_charge_setpoint_w`, Netz nach Präzisierung 5; dann `assemble_inputs`.
  - `replan`: `collect` → bei `PlanningUnavailable` Status setzen, `None` → `solver(inputs)` → `plan_once` → `store.save` (Thread) → `current`. Eine Ausnahme des Solvers ergibt den Ersatzplan „Solver fehlgeschlagen: …“.
  - Gründe des Ersatzplans nach „Texte“: `time_limit` ohne Flüsse → „Zeitlimit ohne Lösung“, `infeasible` → „Solver ohne Lösung (infeasible)“, `timeout` → „Solver nach 30 s abgebrochen“, `error` → „Solver fehlgeschlagen: {message}“.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/planning -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/planning tests/unit/app/planning
git commit -m "feat(planung): Lösen im eigenen Prozess mit Zeitlimit und Planungslauf mit Ersatzplan"
```

### Task 10: Auslöser, Laufzeit und Aufräumen

**Files:**
- Create: `src/bindaems/app/planning/triggers.py`
- Modify: `src/bindaems/app/planning/service.py` (`request`, `run`), `src/bindaems/app/runtime.py`
- Test: `tests/unit/app/planning/test_triggers.py`, `tests/unit/app/planning/test_service.py`, `tests/integration/test_app_runtime.py`

**Interfaces:**
- Consumes: `PlanningService` (Task 9).
- Produces:
  - `WATCH_S = 30.0`, `TICK_DELAY = timedelta(minutes=1)`, `SOC_DEVIATION_PP = 5.0`, `PEAK_RISE_KW = 0.05`
  - `@dataclass(frozen=True) Fingerprint(last_price: datetime | None, primary: int, pv_issued: datetime | None, load_issued: datetime | None)`
  - `changed(old: Fingerprint | None, new: Fingerprint) -> list[PlanTrigger]` (in der Reihenfolge `prices`, `pv_forecast`, `load_forecast`; `old is None` → `[]`)
  - `next_tick(now: datetime) -> datetime` (`slot_start(now) + SLOT + TICK_DELAY`)
  - `expected_soc_pct(plan: Plan, now: datetime) -> float | None` (linear im aktuellen Slot; `None` außerhalb oder bei ungültigem Plan)
  - `soc_deviates(plan: Plan | None, now: datetime, soc_pct: float | None) -> bool`
  - `peak_rose(plan: Plan | None, peak_kw: float | None) -> bool`
  - `settings_relevant(old: RuntimeSettings, new: RuntimeSettings) -> bool` (`planning`, `tariff` oder `feed_in` geändert)
  - `PlanningService.request(trigger: PlanTrigger) -> None` (threadsicher; ein wartender Auslöser bleibt, außer `manual` ersetzt ihn), `async run() -> None`
  - Laufzeit: `self.plan_store`, `self.planning`; Job `planning`; Komponente `planning` nach `load-forecast`; `_maintenance_loop` ruft `plan_store.cleanup`; `settings.on_change` → `request("settings")`, wenn `settings_relevant`

- [ ] **Step 1: Write the failing tests**

```python
def test_slot_tick_one_minute_after_slot_start():
    assert next_tick(at("2026-10-16T10:01:30+00:00")) == at("2026-10-16T10:16:00+00:00")

def test_changed_fingerprints():
    old = Fingerprint(at("2026-10-16T21:45:00+00:00"), 48, at("2026-10-16T10:07:00+00:00"), None)
    new = replace(old, last_price=at("2026-10-17T21:45:00+00:00"), load_issued=at("2026-10-16T10:09:00+00:00"))
    assert changed(old, new) == ["prices", "load_forecast"] and changed(None, new) == []

def test_soc_deviation_interpolates_in_slot():
    plan = sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00"], soc=(50.0, 60.0))
    assert expected_soc_pct(plan, at("2026-10-16T10:07:30+00:00")) == pytest.approx(55.0)
    assert not soc_deviates(plan, at("2026-10-16T10:07:30+00:00"), 59.9)
    assert soc_deviates(plan, at("2026-10-16T10:07:30+00:00"), 60.1)
    assert not soc_deviates(None, at("2026-10-16T10:07:30+00:00"), 10.0)

def test_peak_rose_and_settings_relevant():
    plan = sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00"], month_peak_kw=4.0)
    assert not peak_rose(plan, 4.05) and peak_rose(plan, 4.06)
    base = RuntimeSettings()
    assert settings_relevant(base, base.model_copy(update={"planning": PlanningSettings(margin_ct=3.0)}))
    assert not settings_relevant(base, base.model_copy(update={"wallbox_names": {"evcs": "Garage"}}))

async def test_requests_during_solve_coalesce_into_one_rerun(service_with):
    gate, calls = asyncio.Event(), []
    service = service_with(solver=blocking_solver(gate, calls))
    task = asyncio.create_task(service.run())
    await until(lambda: len(calls) == 1)                     # Lauf „start“ hängt
    for trigger in ("prices", "pv_forecast", "manual"):
        service.request(trigger)
    gate.set()
    await until(lambda: len(calls) == 2)
    await asyncio.sleep(0.05)
    assert [i.trigger for i in calls] == ["start", "manual"]
    task.cancel()

async def test_settings_change_requests_replan(service, settings_service):
    set_planning(settings_service, PlanningSettings(grid_charge_allowed=True))
    assert service.pending() == "settings"
```

`service.pending() -> PlanTrigger | None` (für Tests und Status). `blocking_solver(gate, calls)` hängt die Eingaben an `calls` und wartet beim ersten Aufruf auf `gate`. In `test_app_runtime.py`: die Komponentenliste enthält `planning` nach `load-forecast`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning/test_triggers.py tests/unit/app/planning/test_service.py tests/integration/test_app_runtime.py -q`
Expected: FAIL

- [ ] **Step 3: Implement.**
  - `request` setzt den wartenden Auslöser und ein `asyncio.Event`; aus einem anderen Thread (Einstellungen kommen aus dem Threadpool von FastAPI) über `loop.call_soon_threadsafe` (Schleife wird in `run` gemerkt, wie `LoopSink`).
  - `run`: `request("start")`, dann Schleife: Auslöser prüfen (Fingerabdruck über `prices.slots(aktueller Slot, + 48 h)` und `latest().issued_at`, SOC, `ledger.month_peak` im Thread; Viertelstunde ab `next_tick`), wartenden Auslöser abarbeiten (`replan`), dann auf das Ereignis oder `WATCH_S` warten. Eine Ausnahme in einem Durchlauf wird geloggt, die Schleife läuft weiter.
  - Laufzeit: `PlanStore(self.engine)`, `PlanningService(...)` nach der Lastprognose; Job `("planning", self.planning.run)`; Komponente; Listener; Aufräumen in `_maintenance_loop` mit Log „Alte Pläne aufgeräumt“ (`deleted`, `trimmed`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app tests/integration/test_app_runtime.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/planning src/bindaems/app/runtime.py tests/unit/app/planning tests/integration/test_app_runtime.py
git commit -m "feat(planung): Auslöser der Neuplanung, Job in der app und Aufräumen"
```

### Task 11: API – Plan, neu planen, Netzladen

**Files:**
- Create: `src/bindaems/app/planning/routes.py`
- Modify: `src/bindaems/app/planning/service.py` (`set_grid_charge`), `src/bindaems/app/runtime.py`
- Test: `tests/unit/app/planning/test_routes.py`

**Interfaces:**
- Consumes: `PlanningService` (Tasks 9, 10), `slot_text` (Task 7), `Guard`.
- Produces:
  - `PlanningService.set_grid_charge(on: bool, *, actor: str, source: Source) -> SettingsVersion` (neue Version mit Kommentar „Netzladen ein“/„Netzladen aus“; bei `VersionConflictError` einmal mit der neuen Version wiederholt; gleicher Wert → keine neue Version)
  - `planning_router(service: PlanningService, settings: SettingsService, clock: Clock, guard: Guard) -> APIRouter`:
    - `GET /api/plan` (Lesen) → `{status: {ok, message, running}, grid_charge_allowed, plan: null | {id, created_at, trigger, source, solver_status, fallback_reason, valid_until, valid, horizon_end, reserve_pct, soc_max_pct, month_peak_kw, power_price, expected_cost_eur, baseline_cost_eur, issues, slots: [{start, intent, grid_charge_w, soc_pct, pv_w, load_w, import_w, export_w, battery_w, import_ct, feed_in_ct, estimated, budget_kw, codes, text}]}}` (`soc_pct` = SOC am Slotende, `battery_w` netto, `codes` = Codes in Reihenfolge, `valid` = `valid_at(jetzt)`)
    - `POST /api/plan/replan` (Bedienen) → 202 `{queued: true}`
    - `PUT /api/plan/grid-charge` (Bedienen), Körper `{on: bool}` → `{grid_charge_allowed, settings_version}`; 409 mit dem Text der `VersionConflictError`, wenn auch die Wiederholung scheitert

- [ ] **Step 1: Write the failing tests**

```python
def test_get_plan_without_plan(client):
    body = client.get("/api/plan").json()
    assert body == {"status": {"ok": False, "message": "Noch kein Plan", "running": False},
                    "grid_charge_allowed": False, "plan": None}

async def test_get_plan_shape(client, planning):
    await planning.replan("manual")
    plan = client.get("/api/plan").json()["plan"]
    assert plan["source"] == "optimizer" and plan["valid"] is True
    assert set(plan["slots"][0]) == {"start", "intent", "grid_charge_w", "soc_pct", "pv_w", "load_w", "import_w",
                                     "export_w", "battery_w", "import_ct", "feed_in_ct", "estimated", "budget_kw",
                                     "codes", "text"}

def test_replan_requires_operator(client_as):
    assert client_as("viewer").post("/api/plan/replan").status_code == 403
    assert client_as("operator").post("/api/plan/replan").status_code == 202

def test_grid_charge_switch_creates_settings_version(client_as, settings_service, audit):
    body = client_as("operator").put("/api/plan/grid-charge", json={"on": True}).json()
    current = settings_service.current()
    assert body == {"grid_charge_allowed": True, "settings_version": current.version}
    assert (current.actor, current.source, current.comment) == ("bedienen", "ui", "Netzladen ein")

def test_grid_charge_same_value_creates_no_version(client_as, settings_service):
    before = settings_service.current().version
    client_as("operator").put("/api/plan/grid-charge", json={"on": False})
    assert settings_service.current().version == before

def test_grid_charge_retries_once_on_conflict(planning, settings_service, monkeypatch):
    conflict_once(monkeypatch, settings_service)
    assert planning.set_grid_charge(True, actor="x", source="ui").settings.planning.grid_charge_allowed
```

`client_as(role)` meldet einen Benutzer der Rolle an (`login_as` aus `tests/app_helpers.py`; Benutzernamen „gast“, „bedienen“, „admin“). `conflict_once` lässt das erste `update` mit `VersionConflictError` scheitern.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning/test_routes.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** (ohne `from __future__ import annotations`, wie die übrigen Routen); Router in `runtime.py` eintragen. Euro = ct / 100, auf 2 Stellen gerundet.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app tests/integration/test_app_runtime.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/planning src/bindaems/app/runtime.py tests/unit/app/planning/test_routes.py
git commit -m "feat(planung): API für Plan, Neuplanung und Schalter Netzladen"
```

### Task 12: Home Assistant

**Files:**
- Modify: `src/bindaems/app/ha/entities.py`, `src/bindaems/app/ha/commands.py`, `src/bindaems/shared/config.py` (`HA_SYSTEM_IDS`), `src/bindaems/app/runtime.py` (`_build_ha_inputs`, `HaCommandHandler`)
- Test: `tests/unit/app/ha/test_entities.py`, `tests/unit/app/ha/test_commands.py`

**Interfaces:**
- Consumes: `Plan`, `PlanSlot` (Task 1), `slot_text` (Task 7), `PlanningService.set_grid_charge`, `request` (Tasks 10, 11).
- Produces:
  - `HaInputs.plan: Plan | None = None` (nur gültig), `HaInputs.plan_now: PlanSlot | None = None`, `HaInputs.plan_next: PlanSlot | None = None`, `HaInputs.grid_charge_allowed: bool | None = None`
  - `INTENT_LABELS: Final[dict[BatteryIntentKind, str]]` („Texte“)
  - Entitäten `battery_plan_now`, `battery_plan_next`, `planned_soc`, `budget` (sensor), `grid_charge` (switch), `replan` (button), alle am Gerät „BindaEMS“, nach `month_peak` bzw. `regulation`; in `HA_SYSTEM_IDS`
  - `class PlanningCommands(Protocol)`: `set_grid_charge(on: bool, *, actor: str, source: Source) -> SettingsVersion`; `request(trigger: PlanTrigger) -> None`
  - `HaCommandHandler(cfg, intents, rejections, clock, planning: PlanningCommands | None = None)`; `_Command.parse` liefert `Change | SetGridCharge | Replan | None` mit `@dataclass(frozen=True) SetGridCharge(on: bool)` und `@dataclass(frozen=True) Replan`

- [ ] **Step 1: Write the failing tests**

```python
def test_plan_now_sensor(cfg):
    plan = sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00", "10:15"], intents=[HOLD_REASON, EV])
    inputs = sample_inputs(plan=plan, plan_now=plan.slots[0], plan_next=plan.slots[1])
    spec = entity(cfg, "battery_plan_now")
    assert spec.value(inputs) == "Halten"
    attrs = spec.attributes(inputs)
    assert attrs["reason"] == "Akku hält 50 %, weil 18:00–20:00 Uhr 38,0 ct erwartet werden."
    assert (attrs["codes"], attrs["planned_soc_pct"], attrs["source"]) == (["HALTEN_FUER_SPITZE"], 50.0, "optimizer")
    assert entity(cfg, "battery_plan_next").value(inputs) == "Eigenverbrauch"

def test_plan_sensors_unknown_without_valid_plan(cfg):
    inputs = sample_inputs()
    assert {entity(cfg, oid).value(inputs) for oid in ("battery_plan_now", "planned_soc", "budget")} == {"None"}

def test_build_inputs_ignores_expired_plan(...):
    ...  # build_inputs(…, plan=abgelaufener Plan) → plan_now None

def test_grid_charge_switch_state_and_discovery(cfg):
    assert state(cfg, "grid_charge", sample_inputs(grid_charge_allowed=True)) == "ON"
    assert config_of(cfg, "grid_charge")["command_topic"] == "bindaems/cmd/grid_charge"

async def test_grid_charge_switch_command(handler, planning):
    await handler("grid_charge", "ON")
    assert planning.calls == [("set_grid_charge", True, "home-assistant", "ha")]

async def test_replan_button_requests_manual(handler, planning):
    await handler("replan", "PRESS")
    assert planning.calls == [("request", "manual")]

async def test_unknown_grid_charge_payload_is_rejected(handler, rejections):
    await handler("grid_charge", "VIELLEICHT")
    assert rejections.texts == ["Befehl aus Home Assistant abgelehnt: Unbekannter Befehl „VIELLEICHT“ für „Netzladen erlaubt“."]
```

`test_entities_from_config` bekommt die sechs IDs an ihrer Stelle; `HOLD_REASON` ist eine `HALTEN`-Absicht mit Grund `HALTEN_FUER_SPITZE` (`soc` 50, 18:00–20:00, 38 ct) aus `tests/plan_helpers.py`. `planning` ist ein Fake, der Aufrufe aufzeichnet.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/ha tests/unit/shared -q`
Expected: FAIL

- [ ] **Step 3: Implement.** `build_inputs(…, plan: Plan | None = None, grid_charge_allowed: bool | None = None)` setzt `plan_now = plan.slot_at(now)` und `plan_next = plan.slot_at(now + SLOT)` nur bei `plan.valid_at(now)`. `planned_soc` = `plan_now.soc_end_pct` mit einer Nachkommastelle, Attribut `hourly` aus den Slots mit lokaler Minute 0 (Zeit lokal ISO, wie `prices`). `budget` aus `plan_now.budget_kw`. In `runtime._build_ha_inputs` `self.planning.current()` und `settings.planning.grid_charge_allowed` übergeben; `HaCommandHandler` bekommt `self.planning`. Ohne `planning` lehnt der Handler die beiden Befehle als unbekannt ab. Ablehnungen bei `VersionConflictError` mit deren Text.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/ha tests/unit/shared tests/integration/test_app_runtime.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/ha src/bindaems/shared/config.py src/bindaems/app/runtime.py tests/plan_helpers.py tests/unit/app/ha
git commit -m "feat(ha): Akku-Plan, geplanter SOC und Budget, Schalter Netzladen und Taste Neu planen"
```

### Task 13: UI – Schemas, Vertrag, Demo-Welt und Einstellungen „Planung“

**Files:**
- Modify: `ui/src/lib/api/schemas.ts`, `ui/src/lib/api/endpoints.ts`, `ui/src/lib/api/contract/*`, `ui/src/lib/api/contract.test.ts`, `tests/integration/test_ui_contract.py`, `tests/ui_world.py`, `ui/src/lib/settings/model.ts`, `ui/src/lib/settings/SettingsEditor.svelte`, `ui/src/routes/einstellungen/+page.svelte`
- Create: `ui/src/lib/settings/PlanningEditor.svelte`, `ui/src/lib/settings/PlanningView.svelte`
- Test: `ui/src/lib/settings/model.test.ts`, `ui/src/lib/settings/PlanningEditor.test.ts`

**Interfaces:**
- Consumes: Antworten aus Task 11.
- Produces:
  - `PlanSlotSchema`, `PlanSchema`, `PlanResponseSchema`, `GridChargeReplySchema`; Typen `PlanSlot`, `Plan`, `PlanResponse`
  - `api.plan(o?: Load): Promise<PlanResponse>`, `api.replan(): Promise<void>`, `api.setGridCharge(on: boolean): Promise<GridChargeReply>`
  - Vertragsdateien `plan.json` (`GET_ENDPOINTS["plan"] = "/api/plan"`) und `plan-grid-charge.json` (in `OTHER`, Antwort von `PUT /api/plan/grid-charge`)
  - Demo-Welt: nach den Prognosen ein Plan über `PlanningService` mit `solve_inline` (`await app.planning.replan("start")`)
  - `interface PlanningDraft { gridChargeAllowed: boolean; marginCt: string; roundtripPct: string; wearCt: string; endValueFactor: string; inertiaCt: string }`; `SettingsDraft.planning: PlanningDraft`; Fehlerschlüssel `planning.<feld>`
  - `<PlanningEditor bind:draft={draft.planning} errors />`, `<PlanningView planning />`

- [ ] **Step 1: Write the failing tests**

```ts
it('round-trips the planning settings', () => {
	const result = applyDraft(SETTINGS, draftFrom(SETTINGS));
	expect('settings' in result && result.settings.planning).toEqual(SETTINGS.planning);
});

it('checks the planning numbers', () => {
	const draft = draftFrom(SETTINGS);
	draft.planning.roundtripPct = '101';
	draft.planning.endValueFactor = '1,5';
	draft.planning.marginCt = '-1';
	const result = applyDraft(SETTINGS, draft);
	expect('errors' in result && result.errors).toMatchObject({
		'planning.roundtrip_pct': 'Zahl von 50 bis 100',
		'planning.end_value_factor': 'Zahl von 0 bis 1, z. B. 0,9',
		'planning.margin_ct': 'Zahl ab 0, z. B. 2,0'
	});
});

it('edits planning fields', async () => {
	const { draft } = renderEditor();
	await user.click(screen.getByRole('checkbox', { name: 'Netzladen erlaubt' }));
	await user.clear(screen.getByLabelText('Marge (ct/kWh)'));
	await user.type(screen.getByLabelText('Marge (ct/kWh)'), '2,5');
	expect(draft()).toMatchObject({ gridChargeAllowed: true, marginCt: '2,5' });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pnpm vitest run src/lib/settings`
Expected: FAIL

- [ ] **Step 3: Implement**, dann `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py` und `SCHEMAS` um `plan` und `plan-grid-charge` ergänzen. `PlanningEditor` ist ein `<fieldset>` mit `<legend>Planung</legend>`; auf 360 px stehen die Felder untereinander. `PlanningView` zeigt dieselben Werte in der Leseansicht der Einstellungen.

- [ ] **Step 4: Run tests to verify they pass**

Run: `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py -q && cd ui && pnpm check && pnpm lint && pnpm vitest run`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui tests/integration/test_ui_contract.py tests/ui_world.py
git commit -m "feat(ui): Einstellungen der Planung, Schemas und Vertrag des Plans"
```

### Task 14: UI – Seite „Zeitplan“

**Files:**
- Create: `ui/src/lib/plan/model.ts`, `ui/src/lib/plan/chart.ts`, `ui/src/lib/plan/PlanHeader.svelte`, `ui/src/lib/plan/PlanBlocks.svelte`, `ui/src/routes/zeitplan/+page.svelte`
- Modify: `ui/src/lib/nav.ts`, `ui/src/lib/components/icons.ts` (Symbol `plan`), `ui/tests/e2e/pages.spec.ts` (Seite in `PAGES`), `ui/tests/e2e/plan.spec.ts` (neu)
- Test: `ui/src/lib/plan/model.test.ts`, `ui/src/lib/plan/chart.test.ts`, `ui/src/lib/plan/PlanHeader.test.ts`, `ui/src/routes/zeitplan/page.test.ts`, `ui/src/lib/nav.test.ts`

**Interfaces:**
- Consumes: `api.plan`, `api.replan`, `api.setGridCharge`, Typen (Task 13); `canOperate`; `viennaTicks`; `format.ts`.
- Produces:
  - `INTENT_LABELS`, `TRIGGER_LABELS`, `SOURCE_LABELS` („Texte“)
  - `interface PlanBlock { start: string; end: string; intent: PlanSlot['intent']; gridChargeW: number; socFrom: number; socTo: number; text: string }`
  - `planBlocks(slots: PlanSlot[]): PlanBlock[]` (Präzisierung 17; `end` = Start des letzten Slots + 15 min; `socFrom` = SOC am Ende des Slots vor dem Block bzw. der erste Wert)
  - `blockLabel(block: PlanBlock): string` („Netzladen 2,3 kW“, „Halten“, „Eigenverbrauch“)
  - `planPriceOption(plan: Plan, nowIso: string, palette: Palette): EChartsCoreOption`, `planSocOption(plan: Plan, nowIso: string, palette: Palette): EChartsCoreOption`

- [ ] **Step 1: Write the failing tests**

```ts
it('groups slots into blocks by intent, power and main code', () => {
	const blocks = planBlocks([slot('10:00', 'HALTEN', 0, 'HALTEN_FUER_SPITZE'), slot('10:15', 'HALTEN', 0, 'HALTEN_FUER_SPITZE'),
		slot('10:30', 'NETZLADEN', 2300, 'NETZLADEN_GUENSTIG'), slot('10:45', 'NETZLADEN', 2000, 'NETZLADEN_GUENSTIG')]);
	expect(blocks.map((b) => [b.start, b.end, blockLabel(b)])).toEqual([
		[iso('10:00'), iso('10:30'), 'Halten'],
		[iso('10:30'), iso('10:45'), 'Netzladen 2,3 kW'],
		[iso('10:45'), iso('11:00'), 'Netzladen 2 kW']
	]);
});

it('marks estimated prices and intent windows', () => {
	const price = planPriceOption(PLAN, NOW, palette) as { series: { name: string }[] };
	expect(price.series.map((s) => s.name)).toEqual(['Bezugspreis', 'Bezugspreis geschätzt', 'PV-Prognose', 'Lastprognose']);
	const soc = planSocOption(PLAN, NOW, palette) as { series: { name: string; markArea?: { data: unknown[] } }[] };
	expect(soc.series[0].name).toBe('Geplanter SOC');
	expect(JSON.stringify(soc.series[0].markArea)).toContain('Halten');
});

it('shows source, validity and the fallback reason', () => {
	render(PlanHeader, { data: { ...RESPONSE, plan: { ...RESPONSE.plan!, source: 'fallback', fallback_reason: 'Zeitlimit ohne Lösung' } }, canOperate: false, now: NOW });
	expect(screen.getByText('Ersatzplan')).toBeTruthy();
	expect(screen.getByText('Grund: Zeitlimit ohne Lösung')).toBeTruthy();
	expect(screen.queryByRole('button', { name: 'Neu planen' })).toBeNull();
});

it('lets operators switch grid charging and replan', async () => {
	const { setGridCharge, replan } = renderPage({ role: 'operator' });
	await user.click(screen.getByRole('switch', { name: 'Netzladen erlaubt' }));
	await user.click(screen.getByRole('button', { name: 'Neu planen' }));
	expect(setGridCharge).toHaveBeenCalledWith(true);
	expect(replan).toHaveBeenCalled();
});

it('shows the empty state', () => {
	renderPage({ response: { status: { ok: false, message: 'Kein Plan: keine PV-Prognose', running: false }, grid_charge_allowed: false, plan: null } });
	expect(screen.getByText('Noch kein Plan.')).toBeTruthy();
	expect(screen.getByText('Kein Plan: keine PV-Prognose')).toBeTruthy();
});
```

E2E `plan.spec.ts`: `operator plans again` (Anmeldung „bedienen“, Seite „Zeitplan“ zeigt „Plan von“ und „Ablauf“, Taste „Neu planen“ → „Plan von“ bleibt sichtbar ohne Fehler); `pages.spec.ts` misst `/zeitplan` bei 360 px mit.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pnpm vitest run src/lib/plan src/routes/zeitplan src/lib/nav.test.ts`
Expected: FAIL

- [ ] **Step 3: Implement.** Die Seite lädt `api.plan` als `Resource` alle 60 s im Hintergrund; nach „Neu planen“ fragt sie alle 2 s, bis `plan.id` wechselt (höchstens 40 s), und zeigt dabei „Plant …“. Der Schalter folgt dem Muster von „Regelbetrieb“ in `ControlCard.svelte` (`role="switch"`, ab „Bedienen“ bedienbar, sonst nur Anzeige). Diagramme: Zeitachse von `now` bis `horizon_end` mit `viennaTicks`; „Bezugspreis geschätzt“ gestrichelt nur über Slots mit `estimated`; PV als Fläche, Last als Linie (kW); SOC 0–100 % mit Linie „Reserve“ bei `reserve_pct` und `markArea` je Block `NETZLADEN` und `HALTEN`. „Ablauf“ als Liste (`{#each}` mit Index).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && pnpm check && pnpm lint && pnpm vitest run && pnpm build && pnpm exec playwright test tests/e2e/plan.spec.ts tests/e2e/pages.spec.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui
git commit -m "feat(ui): Seite Zeitplan mit Preisen, Prognosen, geplantem SOC und Ablauf"
```

### Task 15: Szenario-Umgebung mit erzeugten Tagen

**Files:**
- Create: `src/bindaems/tools/scenario/__init__.py`, `src/bindaems/tools/scenario/data.py`, `src/bindaems/tools/scenario/synthetic.py`, `src/bindaems/tools/scenario/runner.py`
- Test: `tests/unit/tools/scenario/test_runner.py`, `tests/integration/test_planning_scenarios.py`

**Interfaces:**
- Consumes: `assemble_inputs`, `PriceInput`, `BatteryState` (Task 3), `solve` (Task 4), `plan_once` (Task 9), `simulate`, `sim_slots`, `params_of`, `eta` (Task 2), `export_after_kwh`, `R1_TOL_KWH` (Task 5), `horizon_class` (3a), `power_rate`, `power_cost_eur`, `import_price`, `feed_in_price` (3a).
- Produces:
  - `class ScenarioSlot(_Model)`: `start`, `pv_w`, `load_w`, `import_ct`, `feed_in_ct: float | None`, `pv_forecast_w: dict[HorizonClass, float]`, `load_forecast_w: dict[HorizonClass, float]`
  - `class ScenarioBattery(_Model)`: Felder von `BatteryConfig`, `max_grid_charge_setpoint_w`, `grid_max_import_w`, `start_soc_pct`
  - `class ScenarioFile(_Model)`: `version: Literal[1]`, `created_at`, `source: Literal["synthetic", "plant"]`, `name: str`, `battery: ScenarioBattery`, `planning: PlanningSettings`, `tariff: TariffSettings`, `month_peak_kw: float | None`, `skipped_days: list[str] = []`, `slots: list[ScenarioSlot]`
  - `DAY_TYPES = ("sommer-sonnig", "winter-spread", "winter-leistung", "wechselhaft")`
  - `synthetic(kind: str, first: date, days: int, *, seed: int = 1) -> ScenarioFile`
  - `DAY_AHEAD_AT = time(14)`, `ESTIMATE_DAYS = 7`
  - `@dataclass(frozen=True) DayResult(day: date, baseline_ct: float, optimizer_ct: float, grid_charge_kwh: float, hold_slots: int, r1_violations: int, fallbacks: int)`
  - `@dataclass(frozen=True) ScenarioResult(name: str, step_slots: int, days: list[DayResult], baseline_ct: float, optimizer_ct: float, adjusted_optimizer_ct: float, baseline_power_ct: float, optimizer_power_ct: float, r1_violations: int, charge_before_export_kwh: float, min_soc_kwh: float, reserve_kwh: float, plans: int, fallbacks: int)` (`*_ct` gesamt einschließlich Leistungspreis)
  - `run_scenario(data: ScenarioFile, *, step_slots: int = 1, grid_charge: bool | None = None) -> ScenarioResult` (Präzisierungen 18 und 19)

Erzeugte Tage (lokale Zeit, `random.Random(seed)`; `import_ct`/`feed_in_ct` über `import_price`/`feed_in_price` mit einem Beispieltarif: Börsenpreis + 10 ct netto, USt. 20 %, OeMAG 6 ct):

| Art | PV (Spitze, Zeit) | Börsenpreis netto | Sonst |
|---|---|---|---|
| `sommer-sonnig` | 9000 W, 05:30–20:30 | 9 ct, 10–16 Uhr 2 ct (am zweiten Tag 13:00 −1 ct), 18–21 Uhr 18 ct | Netzladen erlaubt |
| `winter-spread` | 2000 W, 08:00–16:30 | 0–5 Uhr 7 ct, 17–20 Uhr 32 ct, sonst 12 ct | Netzladen erlaubt |
| `winter-leistung` | wie `winter-spread` | wie `winter-spread` | dazu Leistungspreis 30/60 €/kW/Jahr ab 2027-01-01 (Beispielwerte) |
| `wechselhaft` | 6000 W, 06:30–19:30, je Slot mal Wolkenfaktor 0,2–1 (Zufallsweg) | 0–5 Uhr 10 ct, 17–21 Uhr 22 ct, sonst 6 ct | Netzladen erlaubt |

PV-Form `Spitze · sin(π · Anteil des Tagbogens)^1,5`; Last 350 W, 06:30–08:00 +800 W, 17:30–21:30 +1500 W, je Slot ±15 %. Prognosen: Ist mal `1 + e · k` mit `e` je Stunde gleichverteilt in ±0,2 (PV) bzw. ±0,1 (Last) und `k` = 0,5, 1, 1,5 für `h0_6`, `h6_24`, `h24`. Akku wie `deploy/config.example.yaml`, Start-SOC 50 %. Sommer im Juli 2027, Winter im Januar 2027, ab einem Montag.

- [ ] **Step 1: Write the failing tests**

```python
def test_baseline_never_grid_charges_or_holds():
    result = run_scenario(synthetic("winter-spread", date(2027, 1, 11), 1), step_slots=4, grid_charge=False)
    assert result.r1_violations == 0 and all(d.grid_charge_kwh == 0 for d in result.days)

def test_known_prices_follow_day_ahead_rule():
    data = synthetic("wechselhaft", date(2027, 1, 11), 2)
    assert known_until(data, local_dt("2027-01-11 13:45")) == local_dt("2027-01-12 00:00")
    assert known_until(data, local_dt("2027-01-11 14:00")) == local_dt("2027-01-13 00:00")

def test_forecast_uses_horizon_class_of_lead():
    data = synthetic("sommer-sonnig", date(2027, 7, 5), 2)
    s = data.slots[60]
    assert forecast_at(s, lead=timedelta(hours=2)).pv == s.pv_forecast_w["h0_6"]
    assert forecast_at(s, lead=timedelta(hours=30)).pv == s.pv_forecast_w["h24"]

def test_synthetic_days_are_deterministic():
    assert synthetic("wechselhaft", date(2027, 1, 11), 1) == synthetic("wechselhaft", date(2027, 1, 11), 1)
```

```python
# tests/integration/test_planning_scenarios.py – Budget der Datei: 90 s
CASES = [("sommer-sonnig", date(2027, 7, 5)), ("winter-spread", date(2027, 1, 11)),
         ("winter-leistung", date(2027, 1, 11)), ("wechselhaft", date(2027, 1, 11))]

@pytest.mark.parametrize("kind,first", CASES)
def test_optimizer_beats_victron_alone(kind, first):
    result = run_scenario(synthetic(kind, first, 2), step_slots=4)
    assert result.r1_violations == 0
    assert result.min_soc_kwh >= result.reserve_kwh - 0.01
    assert result.adjusted_optimizer_ct <= result.baseline_ct + 1.0           # 1 ct Toleranz für Rundung
    if kind.startswith("winter"):
        assert result.adjusted_optimizer_ct < result.baseline_ct - 10.0
        assert sum(d.grid_charge_kwh for d in result.days) > 0
```

`known_until` und `forecast_at` sind Hilfsfunktionen des Laufs (`runner.py`, öffentlich für die Tests). Scheitert ein Fall, liegt der Fehler zuerst im Planer (eigener `fix(planung)`-Commit mit Test); einen erzeugten Tag ändert man nur, wenn er physikalisch keinen Vorteil zulässt.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/tools/scenario tests/integration/test_planning_scenarios.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierungen 18 und 19. Der Lauf baut zu jedem Neuplanungszeitpunkt `PriceInput` (Herkunft `primary` bis `known_until`, danach `estimated`), PV und Last über `forecast_at`, `BatteryState` aus dem simulierten SOC, ruft `assemble_inputs` (mit `previous` = voriger Plan), `solve`, `plan_once` und wendet die Absichten bis zur nächsten Neuplanung auf die Ist-Werte an. Ein Slot ohne gültigen Plan (`valid_until ≤ Slotbeginn`) läuft mit `EIGENVERBRAUCH`. Die Monatsspitze beginnt mit `data.month_peak_kw`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/tools/scenario tests/integration/test_planning_scenarios.py -q --durations=5`
Expected: PASS, die Integrationsdatei unter 90 s; liegt sie darüber, rechnet sie 1 Tag je Art (Ruling im Ledger).

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools/scenario tests/unit/tools/scenario tests/integration/test_planning_scenarios.py
git commit -m "feat(planung): Szenario-Umgebung mit erzeugten Tagen und Vergleich mit Victron allein"
```

### Task 16: Werkzeuge `export_days` und `scenario_days`

**Files:**
- Create: `src/bindaems/tools/export_days.py`, `src/bindaems/tools/scenario_days.py`, `src/bindaems/tools/scenario/report.py`
- Test: `tests/unit/tools/test_export_days.py`, `tests/unit/tools/test_scenario_days.py`

**Interfaces:**
- Consumes: `ScenarioFile`, `run_scenario`, `synthetic` (Task 15); `open_database`, `DB_FILENAME`, `SettingsService`, `AuditLog`, `PriceStore`, `PriceService`, `LedgerService.slots`, `actual_from_ledger`, `ForecastArchive.records`, `LedgerService.month_peak` (1b, 3a).
- Produces:
  - `python -m bindaems.tools.export_days --config PFAD --von JJJJ-MM-TT --bis JJJJ-MM-TT --out PFAD [--start-soc 50] [--ohne-prognose]`; Rückgabe 0, 2 bei Fehlern (Konfiguration, Datenbank, kein Tag exportierbar); je Tag eine Zeile auf die Standardausgabe („{Datum}: {n} Slots, {m} ergänzt, Prognose {p} %“ bzw. „{Datum}: übersprungen ({Grund})“)
  - `export_days(engine: Engine, cfg: Config, first: date, last: date, *, start_soc_pct: float, with_forecast: bool, clock: Clock) -> ScenarioFile`
  - `MAX_FILLED = 4` (je Tag höchstens so viele unvollständige Slots, ergänzt als Mittel der Nachbarn)
  - `python -m bindaems.tools.scenario_days (--tage PFAD | --synthetisch ART [--anzahl N]) --out PFAD [--takt {15,60}] [--netzladen {an,aus,einstellung}]`; Rückgabe 0 bei „Bestanden“ (bereinigte Kosten ≤ Victron allein und keine Verletzung von Regel R), 1 sonst, 2 bei ungültiger Eingabe
  - `render_report(result: ScenarioResult, data: ScenarioFile) -> str` (Markdown, „Texte“)

- [ ] **Step 1: Write the failing tests**

```python
def test_export_days_from_app_database(app_db, cfg):
    fill_day(app_db, date(2026, 10, 14), pv_w=2000, load_w=600, price_ct=10.0, forecast=True)
    data = export_days(app_db.engine, cfg, date(2026, 10, 14), date(2026, 10, 14), start_soc_pct=50.0,
                       with_forecast=True, clock=ManualClock(at("2026-10-16T10:00:00+00:00")))
    assert data.source == "plant" and len(data.slots) == 96
    assert data.slots[40].pv_w == 2000 and data.slots[40].load_w == 600 and data.slots[40].pv_forecast_w

def test_day_with_too_many_gaps_is_skipped(app_db, cfg):
    fill_day(app_db, date(2026, 10, 14), pv_w=0, load_w=600, price_ct=10.0, incomplete=5)
    with pytest.raises(NoDaysError):
        export_days(app_db.engine, cfg, date(2026, 10, 14), date(2026, 10, 14), start_soc_pct=50.0,
                    with_forecast=False, clock=CLOCK)

def test_up_to_four_gaps_are_filled(app_db, cfg):
    fill_day(app_db, date(2026, 10, 14), pv_w=0, load_w=600, price_ct=10.0, incomplete=4)
    data = export_days(app_db.engine, cfg, date(2026, 10, 14), date(2026, 10, 14), start_soc_pct=50.0,
                       with_forecast=False, clock=CLOCK)
    assert len(data.slots) == 96 and data.slots[0].pv_forecast_w == {"h0_6": 0.0}

def test_scenario_days_report_and_exit_code(tmp_path, capsys):
    out = tmp_path / "bericht.md"
    code = main(["--synthetisch", "winter-spread", "--anzahl", "1", "--takt", "60", "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert code == 0 and text.startswith("# Szenario-Vergleich mit Victron allein")
    assert "| Tag | Victron allein (€) | Optimierer (€) |" in text and "Bestanden" in text
    assert "Weniger als 30 Tage: Für die Abnahme der Phase 3 zählen mindestens 30." in text

def test_scenario_days_rejects_bad_file(tmp_path):
    bad = tmp_path / "tage.json"
    bad.write_text("{}", encoding="utf-8")
    assert main(["--tage", str(bad), "--out", str(tmp_path / "b.md")]) == 2
```

`app_db` und `fill_day` sind Helfer im Test (App-Datenbank in `tmp_path` mit Migrationen, Abrechnung über `ingest`, Preise über `price_row`, Archiv über `ForecastArchive.record` mit Ausgabe 3 h vor dem Slot). Ohne Prognose ist sie gleich dem Ist-Wert unter `h0_6`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/tools/test_export_days.py tests/unit/tools/test_scenario_days.py -q`
Expected: FAIL

- [ ] **Step 3: Implement.** `export_days` liest Tag für Tag (lokale Tage): Ist-Werte über `actual_from_ledger`, Preise mit den aktuellen Einstellungen über `PriceService.slots`, Prognosen je Horizontklasse aus dem Archiv; ein Tag ohne Preis für einen Slot wird übersprungen („keine Preise“), ebenso mit mehr als `MAX_FILLED` unvollständigen Slots („unvollständig“); die Monatsspitze vor dem ersten Tag aus `ledger.month_peak` (nur Slots vor dem ersten Tag zählen: `month_peak` des Vortags, wenn er im selben Monat liegt). Die Datei geht als JSON nach `--out`; das Verzeichnis muss existieren. `scenario_days` lädt die Datei (`ScenarioFile.model_validate_json`), rechnet mit `step_slots = 1` (`--takt 15`, Standard) oder 4, schreibt den Bericht und je Tag eine Zeile auf die Standardausgabe; geloggt wird erst ab WARNING.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/tools -q && uv run mypy src`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools tests/unit/tools
git commit -m "feat(planung): Export historischer Tage und Szenario-Vergleich als Werkzeuge"
```

### Task 17: Dokumentation und Stand der Spec

**Files:**
- Modify: `docs/betrieb.md` (neuer Abschnitt „Planung“: Zeitplan, Schalter „Netzladen erlaubt“, Einstellungen „Planung“, Auslöser, Ersatzplan, Erklärungen mit der Tabelle der Grundcodes, HA-Entitäten, Werkzeuge `export_days` und `scenario_days` mit `docker compose run --rm --entrypoint python ems-app -m …` und Ablage unter `/data`, „An der Anlage prüfen“; Abschnitt 7 um die neuen HA-Entitäten; Abschnitt 12 um die Seite „Zeitplan“), `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absätze „Stand Plan 3b“ unter 5.5, 9.3, 9.4, 9.5, 9.6, 11.3, 12, 13, 16 und 18), `tests/unit/test_docs.py`
- Test: `tests/unit/test_docs.py`

- [ ] **Step 1: Write the failing test.** `test_documented_tool_commands_start_python_directly` verlangt zusätzlich `bindaems.tools.export_days` und `bindaems.tools.scenario_days`; neu `test_plan_reasons_in_the_guide_match_the_code` (jeder `ReasonCode` steht im Abschnitt „Planung“, jeder dort genannte Code existiert) und `test_operations_guide_describes_planning` (Abschnitt „Planung“ nennt „Zeitplan“, „Netzladen erlaubt“, „Ersatzplan“, „Neu planen“, „Victron allein“).
- [ ] **Step 2:** Run: `uv run pytest tests/unit/test_docs.py -q` — Expected: FAIL
- [ ] **Step 3:** Abschnitte schreiben. Der Absatz unter Spec 18 nennt die Entscheidungen 1 bis 15 so, wie der Betreiber sie freigegeben hat, und die Zeile 3b der Tabelle bekommt „(umgesetzt)“.
- [ ] **Step 4:** Run: `uv run pytest tests/unit/test_docs.py -q` — Expected: PASS
- [ ] **Step 5:** Alle Prüfungen: `uv run ruff format --check . && uv run ruff check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports && uv run pytest -q --cov --cov-fail-under=75` und im UI `pnpm check && pnpm lint && pnpm vitest run && pnpm build && pnpm e2e`. Expected: alles grün.
- [ ] **Step 6: Commit**

```bash
git add docs tests/unit/test_docs.py
git commit -m "docs: Optimierer und Zeitplan (Plan 3b) in Betriebshandbuch und Spec"
```

`docs/uebergabe.md` wird mit dem Abschluss der Phase nachgeführt, nicht in diesem Task.

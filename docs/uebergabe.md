# Übergabe an die nächste Session

Stand: 16.10.2026. Plan 2c-1 (Laderegelung) ist umgesetzt, je Task und im Gesamtreview geprüft
(„mit Korrekturen“, keine kritischen Befunde), und die Fix-Welle danach ist umgesetzt. Er ist auf
`main` (Stand `18c575a`, wie die Branches `ccr-e451cb4c-qeg1tm` und `ccr-9778345e-t3x44k` zu Beginn
der folgenden Sitzung). Danach sind die EVCS-Hysterese (Freigabe des Betreibers vom 14.10.2026) und
die kleinen Nacharbeiten aus dem Gesamtreview dazugekommen; beides ist per Fast-Forward auf `main`
(`47a09a7`, mit Zustimmung des Betreibers vom 14.10.2026). Das Design für 2c-2 hat der Betreiber am
14.10.2026 freigegeben (`docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md`).
**Plan 2c-2a (Akku und Spitzen) ist umgesetzt, je Task und im Gesamtreview geprüft, und die
Fix-Welle danach ist umgesetzt** (`docs/superpowers/plans/2026-10-14-phase-2c2a-akku-spitzen.md`).
**Plan 2c-2b (Zuordnungserkennung) ist umgesetzt, je Task und im Gesamtreview geprüft, und die
Fix-Welle danach ist umgesetzt** (`docs/superpowers/plans/2026-10-15-phase-2c2b-zuordnung.md`).
Beide Pläne sind per Fast-Forward auf `main` (`31721a6`, mit Zustimmung des Betreibers vom
15.10.2026, Tests auf `main` grün); der Arbeitsbranch `ccr-9778345e-t3x44k` ist danach gelöscht, eine
neue Sitzung beginnt auf `main`. Die drei kleinen Doku-Korrekturen aus 2c-2b (Ende von „Bewusst
offen gelassen“, „Doku-Korrekturen aus dem Re-Review der Fix-Welle von 2c-2b“) sind seit dem
15.10.2026 umgesetzt (Branch `claude/project-thread-57rkit`).
**Plan 2d (Bedienung in UI und HA) ist umgesetzt, im Gesamtreview geprüft, und die Fix-Welle danach
ist umgesetzt** (`docs/superpowers/plans/2026-10-15-phase-2d-bedienung.md`, 18 Tasks). Er ist per
Fast-Forward auf `main` (`0198900`, mit Zustimmung des Betreibers vom 16.10.2026, alle Prüfungen auf
`main` grün: 5762 Python-Tests, 351 Vitest, 14 E2E, Abdeckung 97,5 %).
Der Betreiber hat **Phase 3 am 16.10.2026 in drei Pläne geteilt**: 3a Tarif und Prognosen (nur app),
3b Optimierer und Zeitplan (Pläne als Vorschau, Szenario-Tests), 3c Planausführung im core (danach
2 Wochen Dry-Run, dann geht Victron live); die Vorab-Entscheidungen für 3b und 3c stehen in der
Einordnung des Plans 3a.
**Plan 3a (Tarif und Prognosen) ist umgesetzt, je Task und im Gesamtreview geprüft, und die Fix-Welle
danach ist umgesetzt** (`docs/superpowers/plans/2026-10-16-phase-3a-tarif-prognosen.md`, 14 Tasks,
Branch `claude/project-thread-syy5yu`); die Integration auf `main` steht noch aus (Zustimmung des
Betreibers). Als Nächstes folgen die Integration von 3a, dann der Plan 3b, und an der Anlage das
gemeinsame Ausrollen von 2d und 3a sowie die Inbetriebnahme Punkt 3 (Design 2c-2, 11.1): Die
Erkennung läuft im Dry-Run mit, und Health zeigt, wer wo steht (siehe „Offen an der Anlage“).
`main` ist seit dem 12.10.2026 auch der Standard-Branch auf GitHub.

## Kurzfassung

- **Phase 1 ist umgesetzt:** 1a (core, lesend), 1b (app-Backend), 1c (Web-UI) samt Gesamtreview,
  Fix-Durchgang und allen zurückgestellten Kleinigkeiten.
- **HA-Verbraucher (nach Phase 1):** Die app liest beide InfluxDB-Schemata der HA-Integration
  (Messung je Einheit oder je Entität). Fehlt ein Wert, nennen Liste und Formular den Grund.
- **Phase 2a (Sicherheitsbasis) ist umgesetzt** (Plan: `docs/superpowers/plans/2026-10-10-phase-2a-sicherheitsbasis.md`):
  - Safety-Schicht mit allen 8 Regeln aus Spec 8.3 und Property-Tests (`core/safety`)
  - Betriebszustände START, OBSERVE, CONTROL, DEGRADED und SAFE, Absicht mit Freigaben je Aktor
    (`core/control`), Sperren für Teilsysteme bei mitregelnden Systemen
  - Cerbo-Watchdog (`cerbo-watchdog/`) mit Installationsskript, dazu die Werkzeuge
    `watchdog_config` (erzeugt die `config.ini`) und `watchdog_probe` (Prüfprotokoll Teil 2, Punkt 5)
  - Status in UI (System-Seite) und HA (Sensor „Watchdog ausgelöst“)
  - Abdeckungsgrenzen und ein Importvertrag in der CI
  - An der Anlage installiert; die Watchdog-Probe ist bestanden (11.10.2026).
- **Phase 2b (Aktoren) ist umgesetzt und je Task geprüft** (Plan: `docs/superpowers/plans/2026-10-11-phase-2b-aktoren.md`,
  18 Tasks; Entscheidung des Betreibers vom 11.10.2026: subagent-driven):
  - Aktoren in `core/actuators`: EVCS (schreibt über den GX-Dienst `evcharger` des Cerbo, liest
    weiter per Modbus), Victron (Netz-Sollwert-Override, Entladegrenze und Min-SOC per MQTT) und
    Tesla (Befehle über Tessie im Hintergrund; geweckt wird das Auto seit 2c-1 nur durch einen
    Start mit Weckerlaubnis). Jeder Befehl geht durch den Ausführer (`core/control`), die
    Safety-Schicht und das Rücklesen; im Dry-Run wird er nur protokolliert.
  - sicherer Zustand und Übergabe am Ende des Live-Betriebs: erst die Sicherwerte, dann die Freigabe
    des Watchdogs
  - Heartbeat nur, solange der core regelt, und nur aus einem fehlerfreien Zyklus. Der core schärft
    den Watchdog selbst und wertet ihn nur mit eigenem Zähler und passendem Timeout als scharf. Das
    löst den Blocker „core hängt in OBSERVE“ aus dem Gesamtreview von 2a.
  - Erkennung fremder Änderungen (EVCS-Strom, Victron-Override, Tesla-Strom): Alarm und Sperre
  - Entscheidungslog im core: Spool unter `/data/decisions/`, Stream `decision`,
    `GET /v1/decisions`
  - Cerbo-Watchdog 1.1.0: „ausgelöst“ übersteht einen Neustart des Cerbo, Override-Aktion mit
    `victron.live_allowed`, `fsync` des Verzeichnisses
  - Werkzeug `verify_part2` für das Prüfprotokoll Teil 2 (EVCS, Tesla, Victron)
  - `core/actuators` gehört zum Importvertrag (die Safety-Schicht importiert es nicht) und zur
    Abdeckungsgrenze von 90 %
  - **Stand 2b:** Befehle entstanden nur aus dem sicheren Zustand und der Übergabe; die Regelung
    kam mit 2c-1, die Bedienung kommt mit 2d. Die Punkte aus „Vor einer Live-Schaltung in 2b zu
    klären“ (Gesamtreview 2a) sind erledigt.
  - **Gesamtreview über den ganzen Branch:** „bereit mit Korrekturen“, keine kritischen Befunde.
    Der Fix-Durchgang hat die vier wichtigen Befunde behoben, jeder mit einem Test, der vorher
    scheiterte:
    - Eine Sperre, die nur besteht, weil ein Prüfpunkt nicht lesbar ist (etwa im ersten Zyklus
      nach dem Start), verwirft die Werte des EMS nicht mehr; sonst ging die Übergabe des letzten
      Laufs verloren.
    - Ein Strom unter dem Sicherwert gilt als erreicht; der sichere Zustand erhöht nie einen
      Strom.
    - Wirksam live ist ein Aktor nur im Regelbetrieb, außer während seiner Übergabe.
    - Ein wirkungsloser Sicherheitstest prüft jetzt etwas.

    Dazu kamen Punkte im Werkzeug für Teil 2 (Schlaf-Test nur mit frischer Beobachtung, Victron:
    DESS vor jedem Schreiben, vorgefundene Entladegrenze von 0 W). Das Re-Review hat alles
    bestätigt. Entscheidungen, geparkte Befunde und aufgeschobene Kleinigkeiten:
    `docs/superpowers/plans/2026-10-11-phase-2b-entscheidungen.md`.
- **Phase 2c-1 (Laderegelung) ist umgesetzt, je Task und im Gesamtreview geprüft** (Plan:
  `docs/superpowers/plans/2026-10-12-phase-2c1-laderegelung.md`, Design:
  `docs/superpowers/specs/2026-10-12-phase-2c1-laderegelung-design.md`, 16 Tasks; der Betreiber hat 2c
  am 12.10.2026 in 2c-1 und 2c-2 geteilt, Ausführung subagent-driven):
  - Der core regelt die beiden Ladepunkte (EVCS und Tesla) selbst. Die Bausteine liegen rein in
    `core/control` (Aufbau unten):
    - Lademodi je Auto: PV-Überschuss (Standard), Min+PV, Sofort, Aus und Boost. Die Absicht bekommt
      `vehicles`, `priority` und `surplus_priority`; Ungültiges lehnt der core mit `invalid` ab.
    - Phasenwächter mit Zielen je Modus, Last ohne Akku-Hilfe und Überlast-Stufen
    - Tesla: gleitendes Mittel und 90-s-Regel, Weckstart mit Erlaubnis und Kontingent, erneutes
      Anfordern nach „nicht übernommen“ (nie dringend)
    - vereinfachte Zuordnung Auto ↔ Wallbox mit Übersteuerung und „unklar“ (die volle Erkennung
      folgt mit 2c-2), Degradation der Ladepunkte und Wiederaufnahme nach einem Inselbetrieb
  - Die geparkten Befunde aus 2b sind erledigt: Freigabe 0 erst ohne offenen Schreibvorgang,
    Aufräumen des Entscheidungslogs auch nach einem gescheiterten Zyklus, Heartbeat-Startwert je
    Lauf mit bestätigter Freigabe, SAFE ohne einen Tesla, der nicht lädt, und eine Auslösung des
    Watchdogs lässt die Erwartungen der Erkennung fremder Änderungen vergessen.
  - Selbstprüfung `multi_mode` und `import_limit`; Health mit `vehicles`; die Entscheidungen tragen
    Überschuss, Grenze des Wächters und Ist-Werte (im Dry-Run die von evcc).
  - Der Anlagensimulator `bindaems.tools.sim` lässt die echte Laufzeit mit echten Aktoren in
    simulierter Zeit gegen ein Modell der Anlage laufen. Zehn Szenarien sind in der CI grün
    (Betriebshandbuch, „Simulator“).
  - **Beim Bau geändert:** Das Prüffenster des Tesla-Stroms im Tessie-Cache reicht jetzt von 10 bis
    75 s nach der Bestätigung (Frist 85 s, vorher 10 bis 50 s und 75 s). Der Simulator zeigte, dass
    das alte Fenster die 60-s-Abfrage eines angesteckten, noch nicht ladenden Autos nur mit etwa zwei
    Dritteln Wahrscheinlichkeit fing, ein verlorener 5-A-Befehl blieb unbemerkt. Die übrigen
    Änderungen gegenüber Plan und Design stehen im Kopf des Designs und in den Absätzen „Stand
    Plan 2c-1“ der Spec.
  - **Fix-Welle nach dem Gesamtreview:** Stellt ein Stundenlimit des Tesla einen Befehl zurück,
    steht das jetzt als „zurückgestellt“ im Entscheidungslog (höchstens einmal je Minute); vorher
    brach der Dry-Run-Vergleich dort stumm ab. Der Simulator nennt den rohen Akku-Anteil und die
    Zurückstellungen als Informationszahlen und prüft in `reserve` die Reserve-Absicherung der
    Regelung; „SOC nie unter 20 %“ ist als Invariante des Modells gekennzeichnet. Dazu ein Test zur
    Dringlichkeit beim Steigen der Überlast-Stufe, Korrekturen der Doku und die Liste „Vor der
    Live-Schaltung von EVCS und Tesla“ (siehe „Offen an der Anlage“).
  - **EVCS-Hysterese (danach, Design 6.5):** Der EVCS-Strom sinkt sofort mit dem Anteil am
    Überschuss, steigt aber erst, wenn der Anteil den zuletzt angeforderten Strom um 1 A plus
    `control.evcs_hysteresis_a` (0,5 A) übersteigt, und dann auf den abgerundeten Anteil minus die
    Hysterese. Im Simulator sinken die EVCS-Strombefehle in `sonnig` von 77 auf 3 (knapp eine Stunde
    PV-Laden) und in `wolkig` von 101 auf 44 (dort folgt der e-Golf den Wolken); der e-Golf bekommt
    dafür 1 bis 1,5 % weniger Energie, die der Hausakku lädt. `sonnig` prüft höchstens 10 Befehle je
    Stunde. Gegenüber dem Vorschlag aus dem Gesamtreview (0,3 A oder nach 30 s) hat der Betreiber
    nach einer Messung 0,5 A ohne den 30-s-Pfad freigegeben: 0,3 A liegt unter dem Rauschen des
    Simulators (±0,33 A beim e-Golf), und der 30-s-Pfad brachte mehr Flattern statt weniger.
  - **Noch nicht (Stand nach 2c-1):** Befehle an Victron und der 15-min-Wächter (kamen mit 2c-2a),
    die Bedienung (2d). Live ist weiterhin kein Aktor.
- **Phase 2c-2a (Akku und Spitzen) ist umgesetzt, je Task und im Gesamtreview geprüft** (Plan:
  `docs/superpowers/plans/2026-10-14-phase-2c2a-akku-spitzen.md`, Design:
  `docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md`, 14 Tasks; der Betreiber hat 2c-2
  im Design vom 14.10.2026 (Entscheidung 7) in 2c-2a und 2c-2b geteilt, Ausführung subagent-driven):
  - **Umbau vorab, ohne Verhaltensänderung:** Die Laderegelung ist aufgeteilt (`charge_points.py`,
    `charging_tesla.py`, `last_values.py`, `timing.py`), der Regelungs-Kern `Regulation`
    (`core/control/regulation.py`) ist die einzige Schnittstelle der Laufzeit, und seine Verdrahtung
    liegt in `core/regulation_link.py`. Eine Ausnahme in der Regelung setzt nur sie aus: Keeper,
    Stream, Alarme und Entscheidungslog laufen weiter, der Heartbeat bleibt aus, und der Alarm
    `control.exception` steht, bis die Regelung wieder ohne Ausnahme gelaufen ist, auch nach dem
    Abschalten des Regelbetriebs. Das erledigt Punkt 4 der Liste „Vor der Live-Schaltung“.
  - **Akku-Sollwert** (`core/control/battery.py`): Der core setzt den Override des Netz-Sollwerts auf
    den **Fehlbetrag** der Autos (was ihnen nach PV und Haus fehlt, abzüglich des erlaubten
    Akku-Anteils), damit der Akku nicht ins Auto lädt. Aus dem Akku laden dürfen nur Autos in der
    Unterstützung (PV-Überschuss und Min+PV ab SOC 70 %, Hysterese 5 %, höchstens 3 kW und 15 min am
    Stück) und im Boost (ohne diese Grenzen). Dazu gehören das Totband von 200 W, das Auffrischen alle
    240 s (der Cerbo verwirft den Override nach 300 s), die Sperren (Victron gesperrt oder nicht
    steuerbar: kein Befehl, auch im Dry-Run nicht) und Health `battery`.
  - **15-min-Wächter** (`core/control/peak_guard.py`): Er misst die Viertelstunde aus der Abrechnung,
    rechnet hoch, drosselt die Wallboxen (erst PV-Überschuss, dann Min+PV, zuletzt Sofort), lässt den
    Akku überbrücken (Budget `P_zul` für den Override, nur über USV-Reserve + `control.reserve_guard_pct`)
    und warnt sonst (`peak.exceeded`). Das Ziel `control.peak_target_kw` ist optional, ohne Wert ruht er.
    Health `peak`.
  - **Safety-Schicht, Absicht und Log:** Ein Auffrischen (`Command.refresh`) lässt das Totband von
    Regel 5 aus, ist nie dringend und steht bei Erfolg nicht im Entscheidungslog. Neue Gründe
    `battery_comp`, `battery_support` und `peak_guard`; neue Absicht `guest.mode` (Modus eines Gastes,
    bis 2c-2b nur für den Akku-Sollwert).
  - **Simulator:** Das Modell kennt den Override (ESS folgt ihm, er verfällt nach 300 s, DESS verwirft
    ihn). Acht Szenarien kamen dazu; alle 18 sind in der CI grün (Betriebshandbuch, „Simulator“). Die
    Regelung brauchte keinen Fix. Zwei Szenarien haben einen angepassten Aufbau (unten,
    „Bewusst offen gelassen“).
  - **Gesamtreview, Fix-Welle und Nachrunde:** Der Gesamtreview fand zwei wichtige Lücken im
    15-min-Wächter (gewollter Netzbezug über der festen Grenze von Regel 2, Boost-Anteil aus der
    Verteilung der Abrechnung) und zwei Kleinigkeiten; die Fix-Welle hat sie behoben, eine
    Nachrunde danach zwei Restpunkte der Drosselung (alles unter „Bewusst offen gelassen“).
  - **Entschieden und aufgeschoben:** Die Rulings und die aufgeschobenen Kleinigkeiten stehen unter
    „Bewusst offen gelassen“.
  - **Noch nicht (Stand nach 2c-2a):** die volle Zuordnungserkennung (kam mit 2c-2b), die Bedienung
    (2d) und die Planausführung (Phase 3). Live ist weiterhin kein Aktor.
- **Phase 2c-2b (Zuordnungserkennung) ist umgesetzt, je Task und im Gesamtreview geprüft** (Plan:
  `docs/superpowers/plans/2026-10-15-phase-2c2b-zuordnung.md`, Design:
  `docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md`, Abschnitt 7, 9 Tasks;
  Ausführung subagent-driven):
  - **Erkennung** (rein, in `core/control`): `clues.py` wertet je Wallbox mit Ansteckvorgang und je
    Fahrzeug sechs Indizien aus (Tessie, Stromvergleich, zeitliche Korrelation, Phasen-Signatur, Home
    Assistant, Ausschluss), `detection.py` entscheidet daraus (Kandidat, erkannt, Gast, Widerspruch,
    je 60 s ohne Unterbrechung), `fleet.py` führt die Autos der Laderegelung (die Fahrzeuge und je
    Wallbox ein Gast mit dem Schlüssel `guest@<Wallbox>`), und `assignment.py` sagt je Zyklus, wer wo
    steht: Übersteuerung, erkannt, angenommen (auch „Gast, angenommen“) oder, nach einem Widerspruch
    von 60 s, unklar.
  - **Regelung:** Ein Fahrzeug mit Tessie, das an einer EVCS erkannt ist, regelt die Laderegelung wie
    mit Übersteuerung (einmal `max_a`, dann regelt die EVCS in seinem Modus). Der e-Golf am Wall
    Connector ist Last. Ein Gast an der EVCS wird im Modus `guest.mode` geregelt, am Wall Connector
    ist er Last. Neuer Grund `assignment_detected` („Zuordnung erkannt“).
  - **Eingaben und Health:** `vehicles.<name>.home_entity` (ein `device_tracker` in Home Assistant, den
    der core selbst abonniert), die Zeitstempel der Tessie-Werte (`VehicleObs.plugged_at`) und der
    Beginn jedes Ansteckvorgangs. `GET /v1/health` hat `wallboxes` (je Wallbox `id`, `occupant`,
    `name`, `assignment` und `indicators`); `vehicles[].assignment` kennt `detected`.
  - **Simulator:** Home Assistant (der e-Golf zu Hause oder weg), ein Gast ohne Tessie mit 1 bis 3
    Phasen, ein Tesla, der woanders angesteckt ist, und jedes Auto an jeder Wallbox. Drei Szenarien
    kamen dazu (`tesla-evcs`, `egolf-twc`, `gast-evcs`), `zuordnung` prüft einen echten Widerspruch;
    alle 21 laufen grün in `tests/integration/test_sim_scenarios.py` (Betriebshandbuch,
    „Simulator“). Keines scheiterte an Erkennung oder Regelung.
  - **Beim Bau geändert:** Tessie-Werte zählen an einer Wallbox erst eine Minute nach dem Beginn des
    Vorgangs, der Widerspruch ist enger gefasst als im Design (7.3), der Ausschluss verlangt „zu Hause
    und angesteckt“, Stromvergleich und zeitliche Korrelation sagen über ein Auto, das Tessie nicht
    zu Hause sieht, nichts, Tessie und Home Assistant stützen bei mehreren Vorgängen nur nach einer
    Elimination (Strom oder Phasen schließen das Fahrzeug an jeder anderen Wallbox aus), und ein Boost
    ohne Übersteuerung bindet an den Vorgang, an dem das Fahrzeug steht (Kopf des Designs 2c-2; Absatz
    „Stand Plan 2c-2b“ der Spec, 7.8). Die Rulings und die offenen Punkte stehen unter „Bewusst offen
    gelassen“.
  - **Gesamtreview und Fix-Welle:** Der Gesamtreview („mit Korrekturen“, keine kritischen Befunde)
    fand zwei wichtige Lücken: Ein Tesla, der den Wall Connector verließ, galt mit Tessies
    veraltetem „angesteckt“ an der EVCS als erkannt und bekam `max_a`, und der Stromvergleich konnte
    den Tesla an seinem eigenen Wall Connector ausschließen (dann „Gast“, danach „unklar“). Seit der
    Fix-Welle stützen Tessie und Ausschluss nach dem Ende eines Vorgangs erst mit einem Wert, der
    mindestens 60 s danach abgefragt wurde; der Stromvergleich wertet eine Phasenzahl von Tessie,
    die nicht zur Konfiguration passt, als unbekannt, und „passt nicht“ zählt erst mit einem Wert,
    der mindestens 60 s nach der letzten Stromänderung der Wallbox abgefragt wurde; nach einer Pause
    der Regelung von mehr als 60 s beginnen die 60 s der Erkennung neu; die Konfiguration lehnt
    Fahrzeug-Schlüssel `guest` und `guest@…` ab. Dazu kamen drei Prüfungen im Dry-Run, die vor der
    Live-Schaltung von EVCS und Tesla bestanden sein müssen (Betriebshandbuch, „Hinweise für die
    Anlage“).
  - **Noch nicht:** die Bedienung in UI und HA, auch die Auswahl des Gast-Modus (2d), und die
    Planausführung (Phase 3). Live ist weiterhin kein Aktor.
- **Phase 2d (Bedienung in UI und HA) ist umgesetzt und im Gesamtreview geprüft** (Plan: `docs/superpowers/plans/2026-10-15-phase-2d-bedienung.md`, 18 Tasks;
  Ausführung subagent-driven; auf `main` seit `0198900`, 16.10.2026):
  - **app, neues Paket `bindaems.app.control`:**
    - `intent.py`: der `IntentService` leitet jede Bedienung aus der gültigen Absicht ab und sendet sie
      an den core. Die Version ist das Größere aus Millisekunden seit 1970 und gültiger Version + 1; bei
      409 wendet er die Änderung einmal neu an. Boost und Übersteuerung schickt er nur zurück, wo
      Health sie zeigt.
    - `decisions.py`: der `DecisionStore` (SQLite, Migration `0007`) und der `DecisionCollector` (Stream,
      Rückstand, Aufräumen nach 180 Tagen, Markierungen für Grafana).
    - `routes.py`: `/api/control/…` und `/api/decisions…`. „Bedienen“ ändert Regelbetrieb, Lademodi,
      Boost, Zuordnung, Gast-Modus und Vorrang; nur „Admin“ schaltet live oder auf Dry-Run und quittiert
      Sperren.
  - **core:** eine fremde Änderung wird eine Entscheidung (Ergebnis `foreign`), Quittierung von Sperren
    (`POST /v1/locks/{actuator}/ack`), und Health nennt `intent_version`, `boost_pending`,
    `override_pending`, `watchdog.armed`, `actuators[].watchdog_needed` und `vehicles[].commands_hour`.
  - **Tesla vor der Live-Schaltung:** „Aus“ stoppt sofort (dringend), 4 Befehle je Stunde bleiben frei
    (`control.tesla_reserve_commands`), ein Selbststart wird nach 120 s erneut gestoppt
    (`control.tesla_against_s`, `control.tesla_against_retry_s`, Grund `self_start`), und Tessie fragt
    ein abgestecktes Auto zu Hause alle 60 s ab und sofort bei einem neuen Vorgang. Der Simulator hat
    dafür `tesla-aus` und `tesla-selbststart`: jetzt 23 Szenarien, alle grün.
  - **UI:** die Seiten „Laden“ und „Entscheidungen“, „Warum?“ auf der Übersicht, auf der System-Seite die
    Karte „Bedienung“ (Regelbetrieb, Live-Dialog mit Passwort und Bestätigungen, „Auf Dry-Run“) und die
    Taste „Quittieren“. Die Rolle „Bedienen“ hat damit erstmals eigene Rechte.
  - **HA:** Auswahl „Lademodus“, Tasten „Boost“ und „Boost beenden“, Sensor „Wallbox“ je Fahrzeug;
    Auswahl „Zuordnung“ und Sensor „Fahrzeug“ je Wallbox; Schalter „Regelbetrieb“, Auswahl
    „Gast-Lademodus“ und „Zuordnung unklar“. Befehle laufen über `bindaems/cmd/…`. Benachrichtigungen
    bei Fehler-Alarmen und abgelehnten Befehlen.
  - **Doku und Tests:** Betriebshandbuch (7, 12, 13, „Laderegelung“), Spec („Stand Plan 2d“ in 5.3, 7.5,
    7.9, 7.10, 8.7, 11.3, 12, 13, 16 und 18) und `tests/unit/test_docs.py`. Das Demo-Backend hat den
    Benutzer `bedienen`; sein core meldet DEGRADED (die Demo-Welt).
  - **Gesamtreview und Fix-Welle:** Die Fix-Welle hat behoben: die erneute Prüfung der
    Live-Schaltung je Versuch (auch bei leerer Auswahl), die Quittierung neben einer im selben Zyklus
    neu erkannten fremden Änderung, den verbrauchten Boost im core (er bindet mit keiner neuen Version
    wieder), Lademodus `null` in Health als unbekannter Stand, die HA-Auswahl „Zuordnung“ wie im UI,
    den Tag `kind` der Grafana-Markierungen und das Meldebudget mit eigenem Anteil für Ablehnungen.
  - **Entschieden und aufgeschoben:** Die Rulings und die aufgeschobenen Kleinigkeiten stehen unter
    „Bewusst offen gelassen“.
  - **Noch nicht:** Ziel-SOC, Abfahrt und der Modus „Geplant“ (Phase 4), die Planausführung (Phase 3),
    die Mehrkostenanzeige beim Boost, die Schalter „Netzladen erlaubt“ und „Akku-Unterstützung erlaubt“,
    die Zahl „Unterstützungsschwelle“, die Taste „neu planen“ und die HA-Sensoren Ladeenergie,
    Fertig-um und Ziel erreichbar. Live ist weiterhin kein Aktor.
- **Phase 3 ist in drei Pläne geteilt** (Entscheidung des Betreibers vom 16.10.2026; Einordnung und
  Vorab-Entscheidungen im Plan 3a): **3a** Tarif und Prognosen (nur app, `core` unverändert), **3b**
  Optimierer und Zeitplan (Pläne als Vorschau, der core bekommt sie noch nicht; Szenario-Tests über
  historische Tage), **3c** Planausführung im core (danach 2 Wochen Dry-Run, dann geht Victron live).
  Für 3b und 3c gilt vorab: „Victron allein“ ist der Eigenverbrauch des ESS ohne Netzladen und ohne
  Halten; die Szenario-Tests rechnen im Viertelstundenraster mit einem Akkumodell (die CI mit erzeugten
  Tagen, die 30 echten Tage laufen an der Anlage); Regel R2 kommt erst mit Phase 4; `HALTEN` setzt der
  core über den Netz-Sollwert-Override um (Sollwert = Last − PV, nicht unter 0).
- **Phase 3a (Tarif und Prognosen) ist umgesetzt, je Task und im Gesamtreview geprüft** (Plan:
  `docs/superpowers/plans/2026-10-16-phase-3a-tarif-prognosen.md`, 14 Tasks; Ausführung
  subagent-driven; Branch `claude/project-thread-syy5yu`; die Integration auf `main` steht aus):
  - **Tarif** (`shared/settings.py`, `app/tariff/calc.py`): vier Arten von Bestandteilen. Neu sind
    der **Leistungspreis** (€/kW/Jahr, Stufe nach ElWG bei 10 kW, Mindestverrechnung 2 kW, gültig ab
    1.1.2027), Monatsgebühren und Einspeisebestandteile mit Zeitfenstern. Die **Monatsspitze** rechnet
    die app aus dem Ledger; in HA gibt es den Sensor „Monatsspitze“. Leistungspreis und Gebühren
    fließen erst ab 3b und 3c in etwas ein. Der Tarif-Editor im UI (Einstellungen) ändert auch
    Zeitfenster und Gültigkeit.
  - **Tagestypen und Schätzpreise:** Werktag, Samstag, Sonn- und Feiertag (österreichische Feiertage);
    der geschätzte Spotpreis ist der Median der letzten 28 Tage je Viertelstunde und Tagestyp, daraus
    entstehen die Planungspreise (`PriceService.planning`, Herkunft `primary`, `fallback` oder
    `estimated`).
  - **Prognosen** (`app/forecast/`): ein Archiv (Migration `0008`, Tabellen `forecast_record` und
    `forecast_metric`), die PV-Kalibrierung je Stunde und Klarheit mit dem Band P25/P75, die
    Lastprognose (gewichteter Median über 56 Tage, Halbwertszeit 14 Tage) aus dem Ledger und optional
    der HA-Historie (`homeassistant.load_history`, braucht `influxdb.ha_database`), die Prognosegüte
    (MAE, Bias, relativer MAE) und ein Nachtlauf um 02:30.
  - **API und UI:** `/api/forecast/pv`, `/api/forecast/load` und `/api/forecast/quality`; im UI der
    Tarif-Editor, das PV-Band im Diagramm der Übersicht und die Karten „Lastprognose“ und
    „Prognosegüte“ auf der System-Seite. Betrieb: `docs/betrieb.md`, „Prognosen“; Spec: die Absätze
    „Stand Plan 3a“.
  - **Ledger-Korrektur:** Der Tageserlös der Einspeisung rechnet je Viertelstunde mit deren
    Einspeisepreis (Fix-Welle).
  - **Gesamtreview und Fix-Welle:** Der Gesamtreview („mit Korrekturen“) fand keinen kritischen und
    einen wichtigen Befund (der Tageserlös ignorierte die Zeitfenster der Einspeisung); die Fix-Welle hat
    ihn behoben, dazu drei kleine Punkte im UI („Heute (ab jetzt)“ in der Lastprognose, Stufe und
    Mindestverrechnung in der Tarifansicht, die ElWG-Vorlage mit Beginn 1.1.2027). Das Re-Review war
    sauber.
  - **Entschieden und aufgeschoben:** Die Rulings und die aufgeschobenen Kleinigkeiten stehen unter
    „Bewusst offen gelassen“ („Entschieden in Plan 3a“).
  - **Noch nicht:** der Optimierer, der Zeitplan und die Szenario-Tests (3b), die Planausführung im
    core (3c), die Kostenanzeige zu Leistungspreis und Gebühren und eine eigene Seite zur Prognosegüte
    (Phase 5). Live ist weiterhin kein Aktor, und 3a schaltet nichts an der Anlage.
- **Images:** `ghcr.io/binda5000/bindaems-core:latest` und `ghcr.io/binda5000/bindaems-app:latest`.
  Sie sind öffentlich, und die CI setzt `latest` bei jedem Push auf `main`. core und app werden
  immer gemeinsam aus demselben Stand ausgerollt (gleiche Compose-Datei, gleiche Image-Tags): Die
  UI-Schemas sind streng, eine abweichende Version zeigt auf der System-Seite nur „Unerwartete
  Antwort vom Server“.
- **Tests:** 5927 Python-Tests, davon 33 Property-Tests mit Hypothesis (6 für die Safety-Schicht,
  6 für den Phasenwächter, 12 für den Akku-Sollwert, 6 für den 15-min-Wächter, 3 sonstige; Plan 3a hat
  keinen neuen), und 403 UI-Tests (Vitest) sowie 17 E2E-Tests (Playwright); Abdeckung 97,59 %
  insgesamt und 99 bis 100 % für `core/safety`, `core/control` und `core/actuators` (Stand nach
  Plan 3a und der Fix-Welle aus dem Gesamtreview; auf `main` mit Plan 2d: 5762, 351 und 14, 97,5 %).
  Geprüft wird mit ruff, mypy strict (auch für den Watchdog), import-linter (4 Verträge), pytest
  mit Abdeckungsgrenzen (≥ 75 % insgesamt, ≥ 90 % für `core/safety`, `core/control` und
  `core/actuators`), Vitest mit svelte-check, Prettier und ESLint; dazu E2E mit Playwright.
- **Offen:**
  - die Integration von Plan 3a auf `main` (mit Zustimmung des Betreibers), danach der Plan 3b (siehe
    „Nächster Schritt“)
  - an der Anlage: Images von 2d und 3a ausrollen (Punkt „Zu 3a gehört“ unter „Offen an der Anlage“),
    Dry-Run neben evcc beginnen und dabei die Inbetriebnahme Punkt 3
    (die Erkennung läuft im Dry-Run mit, Health zeigt, wer wo steht), der Rest von Teil 2
    (EVCS-Verbindungsabbruch, Victron 17.2-4), Victron in den Dry-Run und live (siehe „Offen an der
    Anlage“)
  - vor der Live-Schaltung von EVCS und Tesla: die Liste „Vor der Live-Schaltung“ (siehe
    „Offen an der Anlage“)
  - die Abnahme der Phase 1 an der Anlage
  - kleine Punkte an der Anlage (siehe „Stand der Installation“): manuelle Punkte von Teil 1 und
    die Log-Zeile der Probe

## Arbeitsweise mit dem Betreiber

- Sprache Deutsch, Anrede „du“.
- **Ablauf je Phase (Spec 18):** Implementierungsplan, Freigabe, Umsetzung, Abnahme.
  Implementiert wird erst nach der Freigabe.
- **Werkzeuge:**
  - Pläne entstehen mit `superpowers:writing-plans` unter `docs/superpowers/plans/`.
  - Phase 1 lief inline („Native“, `superpowers:executing-plans`): Ledger mit `Ruling:`-Zeilen,
    am Ende ein frischer Gesamtreviewer.
  - Plan 2a lief mit `superpowers:subagent-driven-development`: je Task ein Implementierer und ein
    Reviewer, ein Ledger mit Rulings und aufgeschobenen Kleinigkeiten, am Ende der Gesamtreview.
  - Plan 2b lief ebenso subagent-driven (Entscheidung des Betreibers vom 11.10.2026).
  - Plan 2c-1 lief ebenso (Entscheidung des Betreibers vom 12.10.2026). Für die Szenarien des
    Simulators galt: Scheitert eines, liegt der Fehler zuerst in der Regelung (eigener
    `fix(core)`-Commit mit Test); ein Szenario ändert man nur, wenn es physikalisch nicht zu
    schaffen ist.
  - Plan 2c-2a lief ebenso (subagent-driven): je Task ein Implementierer und ein Reviewer, ein Ledger
    mit Rulings und aufgeschobenen Kleinigkeiten (ihr Inhalt steht unten unter „Bewusst offen
    gelassen“). Von den acht neuen Szenarien des Simulators scheiterten drei in der ersten Fassung; bei
    keinem lag der Fehler in der Regelung, und die drei wurden im Aufbau angepasst (Betriebshandbuch,
    „Simulator“). `spitze-15min` startet den Tesla seit der Nachrunde wieder wie geplant.
  - Plan 2c-2b lief ebenso (subagent-driven): je Task ein Implementierer und ein Reviewer, ein Ledger
    mit Rulings und aufgeschobenen Kleinigkeiten (ihr Inhalt steht unten unter „Bewusst offen
    gelassen“). Der Review von Task 2 fand, dass der Ausschluss den Ort des Autos ignorierte; die
    Fix-Runde (mit Test) hat das behoben, und die Rulings zu Stromvergleich und zeitlicher Korrelation
    kamen danach dazu. In den drei neuen Szenarien und im umgebauten `zuordnung` lag kein Fehler in der
    Erkennung oder Regelung.
  - Plan 3a lief ebenso (subagent-driven, 14 Tasks): je Task ein Implementierer und ein Reviewer, ein
    Ledger mit Rulings und aufgeschobenen Kleinigkeiten (ihr Inhalt steht unten unter „Bewusst offen
    gelassen“). Jede Task ließ das ganze Repository grün (auch den UI-Vertragstest), darum weitete
    Task 1 schon das UI-Schema der Tarifbestandteile; der Gesamtreview („mit Korrekturen“) fand einen
    wichtigen Befund, den die Fix-Welle behob.
  - Befunde werden nach ihrer Wirkung neu bewertet. Critical und Important kommen in einen
    Fix-Durchgang, jeder mit einem Test, der vorher scheitert. Minors werden gesammelt und auf
    Wunsch erledigt („bitte die kleinigkeiten erledigen“).
- **Abschlussnachricht einer Phase:**
  - Ergebnis und Tests
  - „Rulings“ (Entscheidungen mit Kosten, falls falsch)
  - „Deferred minors“
  - Frage nach der Integration (merge, PR oder so lassen)
- **Git:**
  - Arbeitsbranch ist der Branch, den die Sitzung vorgibt (Plan 2b und 2c-1: `ccr-e451cb4c-qeg1tm`,
    EVCS-Hysterese, Plan 2c-2a und 2c-2b: `ccr-9778345e-t3x44k`, beide nach der Integration
    gelöscht; Plan 2d: `claude/project-thread-o6klxp`, auf `main`; Plan 3a:
    `claude/project-thread-syy5yu`, noch nicht integriert); der Standard-Branch auf GitHub ist `main`.
  - Nach `main` nur mit Zustimmung; bisher per Fast-Forward-Merge, Tests auf `main`, dann Push.
  - Pull Requests nur auf ausdrücklichen Wunsch.
  - Commit-Nachrichten auf Deutsch im Stil `fix(ui): …`, mit den Attributionszeilen, die die
    Sitzung vorgibt.
  - Keine Modellbezeichnungen in Dateien des Repositorys.
- **Secrets** nur über `BINDAEMS_*`-Umgebungsvariablen; `.env` wird nie committet.

## Wichtige Dokumente

| Dokument | Inhalt |
|---|---|
| `docs/superpowers/specs/2026-10-08-bindaems-design.md` | Spec, verbindlich. Wichtig: 7 Regelung, 8 Sicherheit, 13 Web-UI, 14 Login, 17 Prüfprotokoll, 18 Phasenplan, 19 offene Punkte. Die Absätze „Stand Plan 2a/2b/2c-1/2c-2a/2c-2b/2d/3a“ gehen dem älteren Text davor vor |
| `docs/superpowers/plans/2026-10-08-phase-1a-fundament-datenerfassung.md` | Plan 1a (core) |
| `docs/superpowers/plans/2026-10-09-phase-1b-app-backend.md` | Plan 1b (app) |
| `docs/superpowers/plans/2026-10-09-phase-1c-web-ui.md` | Plan 1c (UI), mit Anhang A zu den API-Formen |
| `docs/superpowers/plans/2026-10-10-phase-2a-sicherheitsbasis.md` | Plan 2a (Sicherheitsbasis), mit dem Zuschnitt der Phase 2 in 2a–2d und den Präzisierungen gegenüber der Spec |
| `docs/superpowers/plans/2026-10-10-phase-2a-entscheidungen.md` | Entscheidungen, geparkte Befunde und aufgeschobene Kleinigkeiten aus der Umsetzung von Plan 2a |
| `docs/superpowers/plans/2026-10-11-phase-2b-aktoren.md` | Plan 2b (Aktoren), 18 Tasks; Entscheidungen des Betreibers vom 11.10.2026, Präzisierungen, Texte |
| `docs/superpowers/plans/2026-10-11-phase-2b-entscheidungen.md` | Entscheidungen, geparkte Befunde und aufgeschobene Kleinigkeiten aus der Umsetzung von Plan 2b |
| `docs/superpowers/specs/2026-10-12-phase-2c1-laderegelung-design.md` | Design 2c-1 (Laderegelung), umgesetzt; der Kopf nennt, was sich beim Bau geändert hat |
| `docs/superpowers/plans/2026-10-12-phase-2c1-laderegelung.md` | Plan 2c-1, 16 Tasks; Präzisierungen gegenüber dem Design, Texte, Szenarien des Simulators |
| `docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md` | Design 2c-2 (Akku-Sollwert, 15-min-Wächter, Zuordnungserkennung), Entscheidungen des Betreibers vom 14.10.2026; 2c-2a und 2c-2b umgesetzt; der Kopf nennt, was sich beim Bau geändert hat |
| `docs/superpowers/plans/2026-10-14-phase-2c2a-akku-spitzen.md` | Plan 2c-2a, 14 Tasks, umgesetzt: Umbau, Akku-Sollwert, 15-min-Wächter, Simulator; Präzisierungen gegenüber dem Design, Texte, Szenarien |
| `docs/superpowers/plans/2026-10-15-phase-2c2b-zuordnung.md` | Plan 2c-2b, 9 Tasks, umgesetzt: Zuordnungserkennung, Gast, Health je Wallbox, Simulator; Präzisierungen gegenüber dem Design, Texte, Szenarien |
| `docs/betrieb.md` | Betriebshandbuch: Installation, Proxy (6), HA (7), Tarif (8, seit 3a mit Leistungspreis, Gebühren und Einspeisung), Sicherung (9), Prüfprotokolle einschließlich Teil 2 (11), Web-UI mit den Seiten „Laden“ und „Entscheidungen“ (12), Betriebszustände, Absicht, Live-Schalter, Übergabe und Entscheidungslog (13), Watchdog am Cerbo (14); am Ende „Laderegelung“ (Modi, Absicht, Zuordnung Auto und Wallbox, Dry-Run-Vergleich mit evcc, Umstieg), „Akku und Spitzen“ (Akku-Sollwert, 15-min-Wächter, Health, Victron in den Dry-Run und live, Alarme), „Simulator“ und „Prognosen“ (seit 3a: PV-Kalibrierung, Lastprognose, Prognosegüte, Nachtlauf, Schätzpreise, „An der Anlage prüfen“) |
| `docs/superpowers/plans/2026-10-15-phase-2d-bedienung.md` | Plan 2d, 18 Tasks, umgesetzt, auf `main`: Bedienung in UI und HA, Entscheidungslog in SQLite, Tesla vor der Live-Schaltung; Präzisierungen gegenüber der Spec, Texte |
| `docs/superpowers/plans/2026-10-16-phase-3a-tarif-prognosen.md` | Plan 3a, 14 Tasks, umgesetzt (Branch `claude/project-thread-syy5yu`, noch nicht auf `main`): Tarifmodell mit Editor, Monatsspitze, Schätzpreise, Lastprognose, PV-Kalibrierung, Prognosegüte; die Einordnung teilt Phase 3 in 3a, 3b und 3c und nennt die Vorab-Entscheidungen für 3b und 3c |
| `cerbo-watchdog/README.md` | Watchdog: Zustände, Zeiten, D-Bus-Pfade, Installation und Probe |
| `docs/verification/abnahme-phase-1.md` | Checkliste für die Abnahme der Phase 1 |
| `docs/verification/README.md` | Prüfprotokoll Teil 1, Preise, Teil 2 (`verify_part2`) und Watchdog-Probe: Aufruf und Ergebnis |
| `README.md` | Entwicklung, Web-UI, Demo-Backend |

## Aufbau

- **`src/bindaems/core`**
  - liest die Geräte (Victron über MQTT, EVCS über Modbus, Wall Connector, Tessie,
    Home Assistant)
  - Zustandsspeicher, abgeleitete Größen, Plausibilität, Flüsse je Viertelstunde
  - InfluxDB-Schreiber mit Spool, interne API mit Stream
  - Pakete für Sicherheit und Aktoren (seit 2a und 2b):
    - `safety/`: harte Grenzen, die Regeln 1–7, Rate-Limits, Schreibbudget, Eingangsprüfung,
      Sperren, wirksame Betriebsarten (Regel 8) und die `SafetyLayer`. Sie ist rein.
    - `control/`: Betriebszustände (`operating.py`) und Absicht (`intent.py`, Datei `intent.json`);
      seit 2b die Aktor-Schnittstelle und der Ausführer (`actuator.py`, `executor.py`), der sichere
      Zustand (`safe_state.py`), die Übergabe (`ownership.py`), die Erkennung fremder Änderungen
      (`foreign.py`) und der Aufbau des `SafetyContext` (`context.py`); seit 2c-1 die Laderegelung
      und seit 2c-2a der Akku-Sollwert und der 15-min-Wächter (nächste Punkte). Auch rein.
    - Die Laderegelung in `control/` (seit 2c-1):
      - `phase_guard.py`: Phasenwächter (Ziele je Modus, Last, Verteilung, Überlast)
      - `surplus.py`: Überschuss und seine Verteilung auf die Autos
      - `charge_modes.py`: Lademodi mit Start-, Stopp- und Mindestzeiten
      - `charge_inputs.py`: Eingaben aus Snapshot und abgeleiteten Größen (seit 2c-2b auch `homes`
        aus Home Assistant und `plugged_at`, die Wanduhr der Tessie-Abfrage)
      - `assignment.py`: Zuordnung (wer steht wo, seit 2c-2b mit der Erkennung), Ansteckvorgänge, Boost
        und Übersteuerung
      - `tesla_policy.py`, `charging_tesla.py`: gleitendes Mittel, Weckkontingent, Tesla-Besonderheiten
      - `charging.py`: der `ChargeController` verbindet die Bausteine je Zyklus
      - seit 2c-2a aus `charging.py` ausgelagert: `charge_points.py` (Ladepunkte und Verteilung),
        `charging_tesla.py` (Tesla-Teil), `last_values.py` (Merker der letzten eigenen Werte, die
        Lade- und Akku-Regelung teilen) und `timing.py` (`held_since`, `lasted`)
    - Zuordnungserkennung in `control/` (seit 2c-2b, rein):
      - `clues.py`: die sechs Indizien (`Clue`, `Evidence`, `ClueTracker`) und `home_and_plugged`
      - `detection.py`: die Entscheidung (`Detector`, `Detection`): Kandidat, erkannt, Gast, Widerspruch
      - `fleet.py`: die Autos der Laderegelung, die Fahrzeuge und je Wallbox ein Gast (`fleet`,
        `guest_key`, `car_spec`)
      - `home_signal` und `ha_entities` liegen in `shared/config.py`, denn Laufzeit, Regelung und
        Simulator brauchen sie
    - Akku und Spitzen in `control/` (seit 2c-2a):
      - `regulation.py`: der Regelungs-Kern `Regulation`, die einzige Schnittstelle der Laufzeit zur
        Regelung (Reihenfolge im Zyklus: Spec 7.1, Stand Plan 2c-2a)
      - `battery.py`: Fehlbetrag, Unterstützung, erlaubter Akku-Anteil und der `BatteryController`
        (Override, Totband, Auffrischen, Sperren)
      - `peak_guard.py`: der 15-min-Wächter (Messung, Budget, Drosselung, Start-Sperre, Zustand)
    - `actuators/`: die konkreten Aktoren für EVCS (GX-Dienst `evcharger`), Victron (MQTT) und
      Tesla (Tessie)
    - `persistence/`: atomare JSON-Dateien (mit `fsync` des Verzeichnisses) und das Entscheidungslog
      (`decision_spool.py`)
    - `watchdog_client/`: Watchdog-Status lesen und Heartbeat senden
  - `regulation_link.py` (neben `runtime.py`, seit 2c-2a): die Verdrahtung der Regelung. Sie baut die
    Eingaben, gibt Urteile und Ergebnisse an den Kern zurück, legt Health und Alarme ab und fängt jede
    Ausnahme des Kerns. Sie liegt außerhalb von `core/control` und darf `core.accounting` importieren
    (die Viertelstunde des Wächters).
  - Auf Geräte schreibt der core den Heartbeat, die Sicherwerte im sicheren Zustand und die der
    Übergabe am Ende des Live-Betriebs, seit 2c-1 die Befehle der Laderegelung an EVCS und Tesla und
    seit 2c-2a den Netz-Sollwert-Override an Victron (nur diesen; Entladegrenze und Min-SOC schreibt
    nur der Keeper). Jeder Befehl geht durch die `SafetyLayer`.
- **`src/bindaems/app`**
  - FastAPI mit SQLite (SQLAlchemy Core)
  - Login, Sitzungen, TOTP, CSRF, Rollen
  - Einstellungen mit Versionen
  - Preise (smartENERGY und Referenzquelle, Brutto/Netto-Erkennung)
  - PV-Prognose (Open-Meteo), Abrechnung, Verbraucher, Verlauf aus InfluxDB
  - seit 3a: Tarifrechnung (`tariff/calc.py`), Tagestyp mit Feiertagen (`daytype.py`), Schätzpreise
    (`prices/estimate.py`) und die Prognosen (`forecast/`: Archiv, Kalibrierung, Lastprognose, Güte,
    Routen)
  - HA MQTT Discovery mit Bedienelementen (`ha/entities.py`, `ha/publisher.py`), den Befehlen aus
    Home Assistant (`ha/commands.py`, seit 2d) und den Benachrichtigungen (`ha/notify.py`, seit 2d)
  - die Bedienung (`control/`, seit 2d): `intent.py` (Absicht aus der app), `decisions.py`
    (Entscheidungslog in SQLite und Markierungen für Grafana), `routes.py`
  - liefert das UI unter `/` aus
- **`src/bindaems/shared`:** Konfiguration, Domänentypen (auch `control.py`: Befehle, Betriebsarten,
  Absicht; `decisions.py`: Entscheidung und ihr deutscher Text), Zeit (`AnchoredClock`), Influx-Client.
  **`src/bindaems/tools`:** Prüfprotokoll Teil 1, `watchdog_config`, `watchdog_probe`,
  `verify_part2` (mit dem Paket `part2/`) und der Anlagensimulator `sim/` (Modell, Fahrzeuge,
  Lauf, Kennzahlen, Szenarien, Bericht).
- **`cerbo-watchdog/`**
  - Python-Dienst für Venus OS (Python ≥ 3.8, nur Standardbibliothek und die Module des Geräts)
  - die Logik (`bindaems_watchdog/logic.py`) ist rein und in der CI getestet, die D-Bus-Anbindung
    (`service.py`) ist dünn
  - `install.sh` installiert, aktualisiert und entfernt per ssh
- **`ui/`**
  - SvelteKit 2 als SPA (Svelte 5 Runes), valibot-Schemas für jede Antwort, ECharts
  - Tests mit Vitest und Playwright
  - Die Vertragsdateien `ui/src/lib/api/contract/*.json` kommen aus der Demo-Welt
    (`tests/ui_world.py`).
- **`deploy/` und CI:**
  - `deploy/`: Dockerfiles, `docker-compose.yml`, `config.example.yaml`, `influxdb-setup.sh`
  - CI `.github/workflows/ci.yml` mit den Jobs `python`, `ui`, `e2e` und `docker`
  - Der Job `python` prüft zusätzlich mypy für das Watchdog-Paket, die Abdeckung (≥ 75 %
    insgesamt, ≥ 90 % für `core/safety`, `core/control` und `core/actuators`) und den
    Importvertrag, der Safety-Schicht und Betriebszustände rein hält.
  - Veröffentlicht wird von `main` und von Tags.

## Befehle

In dieser Cloud-Umgebung brauchen alle `uv`-Aufrufe vorher:
`export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.

```bash
# Python: Format, Lint, Typen (auch das Watchdog-Paket), Importgrenzen, Tests mit Abdeckung
uv run ruff format . && uv run ruff check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports
uv run pytest -q --cov --cov-report=term --cov-fail-under=75
uv run coverage report --include="src/bindaems/core/safety/*,src/bindaems/core/control/*,src/bindaems/core/actuators/*" --fail-under=90

# nur der Cerbo-Watchdog (läuft ohne D-Bus)
uv run pytest tests/unit/cerbo_watchdog -q

# Anlagensimulator: ein Szenario von Hand (Betriebshandbuch, „Simulator“); Rückgabe 0 = alles grün
uv run python -m bindaems.tools.sim --szenario wolkig --out bericht.md

# UI
cd ui && pnpm check && pnpm lint && pnpm test

# E2E (Playwright startet das Demo-Backend auf 8099 selbst)
cd ui && pnpm build && pnpm e2e

# Vertragsdateien neu schreiben, wenn sich API-Antworten ändern
UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py

# Demo-Backend mit gebautem UI
uv run python -m tests.e2e.ui_server --port 8099 --ui-dir ui/build
```

Demo-Zugänge: `admin`, `bedienen` (Bedienen), `gast` (Lesen) und `sicher` (Admin mit TOTP, Geheimnis
`JBSWY3DPEHPK3PXP`). Das Passwort ist jeweils `demo-passwort-1`.

## Stolpersteine

- **Demo-Uhr:** steht fest auf dem 09.10.2026, 10:00 in Wien, der Browser läuft auf dem echten
  Datum. Deshalb zeigt das Übersicht-Diagramm in der Demo Preise vom 09.10. neben der PV der
  echten Tage. Das betrifft nur die Demo.
- **Demo-Backend beenden:** nicht mit `pkill -f`, das trifft die eigene Shell. Die PID merken
  und gezielt beenden.
- **ECharts in Node (SSR):** endet nicht von selbst; danach `chart.dispose(); process.exit(0)`.
- **Standard-Branch:** Auf GitHub ist das seit dem 12.10.2026 `main`; der frühere Standard-Branch
  `claude/ems-haus-akku-pv-93c0s6` ist vollständig in `main` enthalten. Die CI hängt `latest`
  ausdrücklich an `refs/heads/main`.
- **Cookies:** Die Sitzungs-Cookies tragen „Secure“. Über `http://` klappt die Anmeldung nur mit
  `app.cookie_secure: false`, und das ist nur zum Testen gedacht. Die Anmeldeseite meldet das
  verworfene Cookie.
- **`{#each}` über Texte vom Server:** immer den Index als Schlüssel nehmen. Svelte bricht bei
  doppelten Schlüsseln ab, auch im Produktions-Build.
- **Einstellungen:** Die Schemas sind `looseObject`, weil das UI die Einstellungen vollständig
  zurückschickt. Unbekannte Felder dürfen nicht verloren gehen.
- **Sitzung:**
  - Automatische Abfragen senden `X-Bindaems-Background: 1` und verlängern die Sitzung nicht.
  - Bedienung (Tasten, Antippen) verlängert sie höchstens einmal je Minute.
- **Zeitachsen:** ECharts kennt keine Zeitzonen. Die Marken kommen aus `viennaTicks`
  (`ui/src/lib/time.ts`).
- **Bedienelemente:** mindestens 44 × 44 px; ein E2E-Test misst alle Seiten bei 360 px.
- **Schriften in der CI:** Dort wird `system-ui` zu DejaVu Sans, die deutlich breiter läuft als
  die lokale Schrift. Breitenfehler bei 360 px stellt man so nach (der Pfad muss absolut sein):
  `cd ui && FONTCONFIG_FILE=$PWD/tests/e2e/fonts-ci.conf pnpm e2e`.
- **Watchdog-Paket (Phase 2a):**
  - Es muss unter Python 3.8 laufen (ruff mit `py38` und ein `ast`-Test) und darf nichts aus
    `bindaems` importieren. Die Version steht zweimal: `WATCHDOG_VERSION` in
    `core/watchdog_client/status.py` und `VERSION` in `cerbo-watchdog/bindaems_watchdog/__init__.py`.
    `tests/unit/cerbo_watchdog/test_compat.py` prüft, dass sie gleich sind.
  - Das D-Bus-Gerüst läuft nur auf einem Venus-OS-Gerät. Die Python-Tests prüfen Logik,
    Konfiguration, Zustandsdatei, Aktionen und den Dienst mit Fakes und `install.sh` gegen ein
    nachgebildetes Gerät (Attrappen für ssh, svc und dbus). Der Schritt „Shellcheck“ im Job
    `docker` der CI prüft `install.sh`, `service/run` und `service/log/run` zusammen mit
    `deploy/influxdb-setup.sh`.
  - Jede Änderung an `victron.watchdog`, der USV-Reserve oder einem sicheren Strom in
    `config.yaml` verlangt eine neue `config.ini` auf dem Cerbo (Betriebshandbuch 14). Dazu zählt
    `victron.live_allowed`, denn mit ihm enthält die Datei die Aktion `override_setpoint`. Die Felder
    unter `control` aus 2c-2a gehören nicht dazu.
- **Neue Pakete unter `src/bindaems`** brauchen eine `__init__.py`; sonst sieht import-linter sie
  nicht (`tests/unit/test_layout.py`).
- **Gates für die Safety-Schicht:** `core/safety` und `core/control` dürfen nichts aus
  `core.actuators`, `core.adapters`, `core.api`, `core.runtime`, `core.telemetry`,
  `core.accounting` und `core.checks` importieren, auch nicht über Umwege (import-linter-Vertrag).
  Das gilt auch für `battery.py`, `peak_guard.py`, `regulation.py`, `clues.py`, `detection.py` und
  `fleet.py`. `core/regulation_link.py` liegt
  außerhalb von `core/control`; dort sind diese Importe erlaubt (es nutzt `core.accounting`).
  Der Vertrag prüft nur die Importe. Konvention, nicht geprüft: Zeit kommt als Argument, Dateien nur
  über `core.persistence.files`. Aktoren erreichen die Schicht nur über das Protokoll `Actuator`,
  das Entscheidungslog über `DecisionSink`.
- **Zwei Uhren (2b):** Fristen, Rate-Limits, Schreibbudget, sicherer Zustand, Erkennung fremder
  Änderungen, Heartbeat und Tesla-Fehler laufen auf der `AnchoredClock` (Wanduhr beim Start plus
  monotone Zeit). Messwerte, Viertelstunden und die Zeitpunkte der Entscheidungen bleiben bei der
  Wanduhr. Darum tragen Alarme der Safety-Schicht, des sicheren Zustands, der Aktoren und der
  fremden Änderungen eine andere Zeit als „seit“ als die übrigen (Betriebshandbuch 14). Die
  Laderegelung (2c-1) hält es ebenso: Ihre Zeiten (Timer, Fenster, Überlast, Weckabstand) laufen auf
  der Uhr der Safety-Schicht (`ChargeInputs.now`), `boost_until` und der Kalendertag des
  Weckkontingents (Wiener Zeit) nach der Wanduhr (`ChargeInputs.wall_now`). Der Akku-Sollwert
  (Überbrückung, Auffrischen) läuft ebenso auf der Uhr der Safety-Schicht, der 15-min-Wächter auf der
  Wanduhr, wie die Viertelstunden der Abrechnung; Tests müssen beide Uhren angeben. Die Erkennung
  (2c-2b) hält es wie „unklar“: Die 60 s der Indizien und der Entscheidung laufen auf der Uhr der
  Safety-Schicht, die zeitliche Korrelation, der Beginn der Vorgänge und die Ausnahme für Tessie-Werte
  auf der Wanduhr, denn von ihr kommen die Zeitstempel der Signale.
- **Tests mit respx und Tessie (2b):** Eine Anfrage ohne passende Route fängt der Tesla-Aktor als
  Fehler ab, und respx zeichnet sie nicht auf. Wer prüft, dass nichts gesendet wird, braucht eine
  Catch-all-Route und zählt deren Aufrufe.
- **Tests der Laderegelung (2c-1):** Fehlen die Ströme von EVCS und Wall Connector, gilt jede Phase
  als voll, und kein Auto startet (Design 5.5). Tests müssen die Ströme darum angeben, auch 0 A; nur
  ein fehlender Wert macht eine Phase voll. Die gemeinsamen Vorgaben stehen in
  `tests/unit/core/control/charge_helpers.py`. Die Regelung sendet nur Änderungen; wer einem Aktor
  Befehle gibt, ohne dass sie es sieht, ruft `ChargeController.forget` auf (die Laufzeit tut das für
  Keeper, Übergabe, fremde Systeme und den Wechsel der Betriebsart).
- **Tests mit der EVCS-Hysterese:** Die EVCS hält ihren zuletzt angeforderten Strom, bis der Anteil
  ihn um 1 A plus `control.evcs_hysteresis_a` (0,5 A) übersteigt. Ein Test, der eine Erhöhung an
  der EVCS erwartet, braucht also einen Anteil ab Sollwert + 1,5 A oder setzt die Hysterese auf 0
  (`with_control` in `tests/unit/core/control/test_charging.py`). Senkungen wirken sofort.
- **Tests der Erkennung (2c-2b):** Die Erkennung braucht 60 s ohne Unterbrechung, und die Phasen-Signatur
  und der Stromvergleich brauchen vorher 60 s Laden: Ein Test, der „erkannt“ erwartet, läuft mehr als
  120 s. Das Bild „Tesla an der EVCS“ (Tessie: zu Hause und angesteckt, Wall Connector ohne Fahrzeug,
  EVCS mit Fahrzeug) ist ein echter Widerspruch nur, wenn die EVCS auf zwei Phasen lädt:
  `CONTRADICTION` in `tests/unit/core/control/charge_helpers.py`, unklar ab `UNCLEAR_FROM_S` (120 s).
  Ein e-Golf an einer EVCS, die auf drei Phasen lädt, ergibt widersprüchliche Eingaben (die
  Phasen-Signatur schließt ihn aus). Tessie-Werte brauchen `plugged_at` (die Wanduhr der Abfrage), und
  Home Assistant kommt über `homes`; ohne Angabe sagt es über kein Fahrzeug etwas. Laufen an beiden
  Wallboxen Vorgänge, stützen Tessie und Home Assistant erst, wenn Strom oder Phasen das Fahrzeug an
  der anderen Wallbox ausschließen (Elimination), also frühestens nach 60 s Laden dort; vorher
  schließen sie nur aus. Ein Test, der dann eine Erkennung erwartet, lässt die andere Wallbox auf
  einer anderen Phasenzahl oder mit anderem Strom laden oder stützt das Fahrzeug über Strom und
  Phasen.
- **Simulator (2c-1, 2c-2a, 2c-2b, 2d):** Er nutzt die echten Aktoren und die Parser der Adapter. Ändert man deren
  Zeiten, Texte oder Signale, ändern sich die Szenarien mit. Alle dreiundzwanzig Szenarien laufen in
  `tests/integration/test_sim_scenarios.py`, dazu eine Abwandlung von `spitze-15min` für den
  Boost-Anteil des Wächters; je etwa 3 bis 13 s, zusammen rund 3 min (gemessen auf
  der Entwicklungsmaschine); der Job `python` der CI braucht damit 7 bis 11 min, der ganze Ablauf
  mit UI, E2E und Docker 9 bis 12 min (Läufe vom 14. und 15.10.2026). Die Szenarien sind
  deterministisch (feste Saat).
- **Dokumentationstests (2c-1, 2c-2a, 2c-2b, 2d):** `tests/unit/test_docs.py` prüft die JSON-Beispiele, die Tabellen
  der Gründe und die Optionen und Szenarien des Simulators im Betriebshandbuch gegen den Code, im
  Abschnitt „Akku und Spitzen“ außerdem die dokumentierten Standardwerte (die Tabelle der Einstellungen
  gegen `ControlConfig`) und die Felder von Health (das Beispiel gegen `health()` von Akku-Sollwert und
  Wächter). Seit 2c-2b prüft er auch den Ausschnitt von Health zu den Wallboxen (Felder und Werte gegen
  `wallbox_health()`, `AssignmentKind` und `Clue`), dass das Handbuch jede Art der Zuordnung und jedes
  Indiz nennt, und den Beispielsatz zu `assignment_detected` gegen `decision_text`. Ändert man die
  Absicht, einen Grund-Text, die Optionen von `bindaems.tools.sim`, einen dokumentierten Standardwert
  oder die Felder von Health, schlägt er an, bis das Handbuch stimmt. Die Werte der Beispiele prüft er
  nicht, die Zustände von Health nur gegen eine feste Liste im Test, und eine neue Einstellung, die in
  seiner Liste `BATTERY_SETTINGS` fehlt, bemerkt er nicht. Seit 2d kennt `CHARGING_REASONS` auch
  `self_start`, `BATTERY_SETTINGS` die drei Einstellungen `tesla_reserve_commands`,
  `tesla_against_s` und `tesla_against_retry_s`, und ein Test verlangt, dass Abschnitt 12 des
  Handbuchs „Laden“, „Entscheidungen“, „Warum?“ und „Bedienen“ nennt und Abschnitt 7 die
  Bedienelemente (Lademodus, Regelbetrieb, Boost, Zuordnung, `notify_service`). Die Tabelle der
  Szenarien prüft er gegen `SCENARIOS`; die Zahl im Text (dreiundzwanzig) prüft er mit.

## Stand der Installation beim Betreiber

- Docker auf einer VM im LAN des Betreibers, Images `latest` vom Stand nach Plan 2b (ausgerollt am
  12.10.2026). Die Pläne 2c-1, 2c-2a, 2c-2b, 2d und 3a sind noch nicht ausgerollt.
- Reverse Proxy mit HTTPS sowie Tarif und OeMAG-Wert im UI: laut Betreiber eingerichtet
  (11.10.2026). Zum Proxy gehören laut Betriebshandbuch 6: Port 8080 erreicht nur der Proxy,
  `app.trusted_proxies` ist gesetzt, `cookie_secure` steht wieder auf `true`.
- **Prüfprotokolle** in `docs/verification/`, Bewertungen jeweils am Ende des Protokolls:
  - Preise am 10.10.2026 bestanden (smartENERGY brutto).
  - Teil 1 am 10.10.2026 mit Befunden, am 11.10.2026 mit erreichbarer EVCS wiederholt.
  - Watchdog-Probe (Teil 2, Punkt 17.2-5) am 11.10.2026 bestanden, am 12.10.2026 mit 1.1.0
    erneut bestanden.
  - Teil 2 EVCS am 12.10.2026 bestanden: Schreibweg über den GX-Dienst `evcharger` bestätigt,
    fremde Änderung erkannt, Watchdog setzt den sicheren Strom. Offen ist „Verbindung
    unterbrochen“.
  - Teil 2 Victron am 12.10.2026 mit vorübergehend abgeschaltetem DESS: Override setzen und mit
    `null` aufheben bestätigt, Ruhezustand `null`, Entladesperre und Netzladen wirken. Neu: Der
    Override verschwindet nach 5 min ohne Auffrischen. Am Abend in einem zweiten Lauf 17.2-8
    „Importlimit“ bestanden: Mit einer Last, die eine Phase über 20 A gebracht hätte, blieb L3 bei
    höchstens 19,4 A, der Akku lieferte dabei 3072 W. Ein Vorlauf mit dem Multi auf „Nur Laden“
    (charger only) ergab 33,3 A auf L3 ohne Beitrag des Akkus: In diesem Modus wirkt das
    Importlimit nicht. Offen ist 17.2-4.
  - Teil 2 Tesla am 12.10.2026 bestanden (drei Läufe und Schlaf-Test): Das Auto folgt
    Strombefehlen über Tessie, „Laden aus und an“ wirkt, ein schlafendes Auto wird nicht geweckt
    (10 min Schlaf-Test). Ein erster Befehl auf 5 A wirkt über Tessie nicht, obwohl Tessie ihn
    bestätigt; der zweite wirkt (Versuch im Nachtrag zum zweiten Bericht). Seit dem dritten Lauf
    erkennt der Aktor das im Tessie-Cache, und das Werkzeug wiederholt den Befehl: „Verhalten bei
    5 A“ bestanden, der Wall Connector war 4 s nach der Wiederholung bei 5 A. „Fahrzeug nicht zu
    Hause.“ im ersten Lauf kam von einer falschen `site.location`, die der Betreiber korrigiert
    hat.
- **Befunde:**
  - Dynamic ESS bleibt nach Entscheidung des Betreibers an, bis Victron live geschaltet wird. Für den
    Dry-Run des Victron-Teils schaltet er es in einem Fenster ab, das er bestimmt (Design 2c-2, 11.1).
  - Die EVCS (AC22NS, Produkt-ID 0xC026) liest der Cerbo per Modbus TCP unter ihrer
    LAN-Adresse. BindaEMS bekam unter derselben Adresse zuerst nur Nullen. Ursache war die
    IP-Whitelist der EVCS: Die Adresse der VM stand nicht darauf. Die EVCS nimmt die Verbindung
    dann trotzdem an und liefert nur Nullen. Seit der Betreiber die Whitelist am 11.10.2026
    korrigiert hat, liest BindaEMS ihre Register selbst (Firmware v2.09, manueller Modus).
  - Seit dem 10.10.2026 folgt die Registerbelegung Victrons GX-Treiber: Sollstrom 5016,
    Maximalstrom 5017. Ohne bekannte Produkt-ID gelten die Registerwerte als ungültig; Strom
    und Modus kommen dann vom Cerbo.
  - **Peak Shaving:** Importlimit 20 A je Phase mit „immer“ (`Settings/CGwacs/AcInputLimit`,
    `AlwaysPeakShave` = 1). Das ist gewollt (Entscheidung des Betreibers vom 11.10.2026), also
    deutlich unter der 35-A-Sicherung. Bezug über 20 A je Phase deckt der Akku.
  - Akku 96 kWh nutzbar.
- **Watchdog:** Version 1.1.0 am Cerbo installiert (12.10.2026), Probe am selben Tag bestanden.
  Er wartet, solange der core nichts braucht (kein Aktor im Regelbetrieb live).
- **HA-Daten in InfluxDB:** Datenbank `homeassistant`, `bindaems` hat READ. HA schreibt mit
  `measurement_attr: entity_id`:
  - Messung = Entität (`sensor.…`), Tags `domain` und `entity_id` (ohne Domain)
  - Felder `value`, `unit_of_measurement_str`, `device_class_str`, `state_class_str`,
    `friendly_name_str`
  - Wichtig auch für die Lastprognose aus der HA-Historie.
- **Offen an der Anlage, als Nächstes (nach Plan 3a):**
  1. Die Images ausrollen: `docker compose pull && docker compose up -d` (Betriebshandbuch 4),
     nachdem Plan 3a auf `main` ist (die Pläne 2c-1, 2c-2a, 2c-2b, 2d und 3a rollen zusammen aus; 2d ist
     seit dem 16.10.2026 auf `main`). **Zu 2d gehört:**
     - Die Migration der app (`0007`, Tabelle `decision`) läuft beim Start der app von selbst. Die
       Entscheidungen der letzten 7 Tage holt die app dann aus den Dateien des core nach.
     - `BINDAEMS_HA_TOKEN` braucht jetzt auch die app (für die Benachrichtigungen). Die `.env` gilt
       schon für beide Dienste; sie hat den Token wegen `home_entity` ohnehin. Ohne ihn meldet die
       Komponente `ha-notify` unter **System** „Benachrichtigungen aus: BINDAEMS_HA_TOKEN fehlt.“
     - Optional `homeassistant.notify_service` in `config.yaml` (etwa `notify.mobile_app_handy`) für
       einen Push auf das Handy; die persistente Meldung in Home Assistant gibt es ohne ihn.
     - Die ACL des Mosquitto-Benutzers `bindaems` braucht Schreibrechte auf
       `homeassistant/select/bindaems/#`, `…/switch/…` und `…/button/…` und das Leserecht
       `bindaems/cmd/#` (Betriebshandbuch 7); sonst bleiben die Bedienelemente aus.
     - Ein Benutzer mit der Rolle „Bedienen“ ist für Home Assistant nicht nötig; HA hat die Rechte
       dieser Rolle ohne Benutzer. Für Menschen, die nur bedienen sollen, legst du einen an.
     - Neue Felder mit Standardwerten: `control.tesla_reserve_commands`, `control.tesla_against_s`,
       `control.tesla_against_retry_s`, `app.decisions_keep_days`, `homeassistant.notify_service`. Die
       Konfiguration prüft jetzt Fahrzeugnamen (nicht leer, nicht mit Leerzeichen am Rand, ohne
       Beachtung der Schreibweise eindeutig, nicht „Automatisch“) und die Objekt-IDs in Home Assistant;
       prüfe die `config.yaml` anhand von `deploy/config.example.yaml`. Der Watchdog bleibt
       unverändert, und keines der neuen Felder verlangt eine neue `config.ini`.
     **Zu 3a gehört** (Betriebshandbuch, „Prognosen“):
     - Die Migration der app (`0008`, Tabellen `forecast_record` und `forecast_metric`) läuft beim Start
       der app von selbst.
     - Neu und optional: `homeassistant.load_history` mit `entity` und `subtract` (verlangt
       `influxdb.ha_database`, sonst lehnt die app die Konfiguration beim Start ab); Muster in
       `deploy/config.example.yaml` und im Betriebshandbuch, „Prognosen“. Ohne sie rechnet die
       Lastprognose nur aus der Abrechnung.
     - Den Leistungspreis trägst du im Tarif-Editor (Einstellungen) ein, mit der Vorlage
       „Leistungspreis (ElWG)“ (Stufe 10 kW, Mindestverrechnung 2 kW, gültig ab 1.1.2027). Die Sätze
       stehen nicht vor; sie kommen aus dem Preisblatt des Netzbetreibers. Leistungspreis und Gebühren
       fließen erst ab 3b und 3c in etwas ein.
     - **Risiken an der Anlage prüfen** (Betriebshandbuch, „Prognosen“, „An der Anlage prüfen“ nennt die
       Schritte):
       - Die InfluxQL-Abfrage der HA-Historie ist nicht gegen die echte InfluxDB geprüft: `fill(previous)`
         auf dem Einheitenfeld und die 56-Tage-Abfrage in Minutenauflösung gegen die 15 s Zeitlimit und die
         Höchstzahl an Zeilen. Eine scheiternde Abfrage wiederholt die app stündlich; im Log steht dann
         „Lastverlauf aus Home Assistant nicht lesbar“.
       - Die Strahlung von Open-Meteo in den Tests ist künstlich erzeugt (kein Mitschnitt); ob Klarheit
         und Klassen an der Anlage sinnvoll ausfallen, zeigt erst der Betrieb.
       - Die PV-Kalibrierung lernt auch Abregelung (Einspeisegrenze, voller Akku) als niedrigeren
         Faktor.
     - **Hauslast aus Home Assistant:** entschieden am 16.10.2026: ohne. In HA gibt es keine Entität
       „nur Hauslast“, und `homeassistant.load_history` bleibt aus. Die Lastprognose lernt nur aus der
       Abrechnung; sie ist in den ersten Wochen ungenauer, trägt nach etwa 3 Wochen und ist nach 8 Wochen
       voll.
     Aus den Plänen davor: Die neuen Felder `grid.import_limit_a`, `control` (seit der Hysterese auch
     `control.evcs_hysteresis_a`, seit 2c-2a `support_soc_pct`, `support_hysteresis_pct`,
     `support_max_w`, `support_max_s`, `setpoint_deadband_w`, `override_refresh_s`, `peak_target_kw`
     und `peak_margin_kw`), `vehicles.<name>.wake` und seit 2c-2b `vehicles.<name>.home_entity` haben
     Standardwerte (`home_entity`: keiner); prüfe sie anhand von `deploy/config.example.yaml`. Für den
     e-Golf trägst du `home_entity` mit dem `device_tracker` aus Home Assistant ein; das verlangt den
     Abschnitt `homeassistant` in `config.yaml` und `BINDAEMS_HA_TOKEN` in der `.env`
     (Betriebshandbuch, „Zuordnung Auto und Wallbox“). Der Watchdog bleibt unverändert (1.1.0), und
     keines der neuen Felder verlangt eine neue `config.ini`. Der 15-min-Wächter ruht ohne
     `control.peak_target_kw`.
  2. Den Dry-Run neben evcc beginnen: den Regelbetrieb im UI einschalten (Karte „Bedienung“ unter
     **System** oder Seite „Laden“, als „Bedienen“ oder Admin); alle Aktoren bleiben im Dry-Run
     (Betriebshandbuch, „Laderegelung“; ohne app eine Absicht mit `"control": true` per
     `PUT /v1/intent`). Der core sendet dabei nichts auf die Geräte, und die Teil-2-Werkzeuge
     schreiben nicht mit: Sie schreiben nur, wenn du sie aufrufst. Die Entscheidungen sichtet der
     Betreiber auf der Seite „Entscheidungen“ und in Grafana (Betriebshandbuch 13, „Entscheidungslog“
     mit der Abfrage für die Markierungen).
     - Die EVCS-Hysterese ist eingebaut (14.10.2026). Ohne sie hätte der Dry-Run mit rund 100
       EVCS-Entscheidungen je Stunde PV-Laden begonnen; im Simulator sind es jetzt 3 bei ruhiger
       Sonne und rund 40 unter wechselnden Wolken.
     - Das Stundenbudget des Tesla kappt den Vergleich bei Wolken (Punkt 1 unten); seit der
       Fix-Welle steht das als „zurückgestellt“ im Log.
     - **An der Anlage im Dry-Run prüfen:** welcher EVCS-Status bei angestecktem Auto auf
       „Laden aus“ (`StartStop` = 0) folgt (ist es 3, bliebe ein Auto in PV nach dem ersten Stopp
       auf „geladen“ stehen, bis es abgesteckt wird); welche Phasenzahl Tessie (`charger_phases`)
       meldet, während der Tesla dreiphasig lädt (weicht sie von `vehicles.tesla.phases` ab, gibt es
       für ihn keinen Stromvergleich); wie oft die EVCS bei ruhiger Sonne ihren Strom ändert
       (deutlich über 10 Entscheidungen je Stunde heißt: Das Rauschen an der Anlage ist größer als
       die Hysterese, `control.evcs_hysteresis_a` anheben).
     - **Vor der Live-Schaltung von EVCS und Tesla im Dry-Run bestanden** (Betriebshandbuch,
       „Hinweise für die Anlage“; die Indizien stehen in Health unter `wallboxes`):
       - Verlässt der Tesla den Wall Connector, während der e-Golf an der EVCS steckt und nicht
         lädt, bleibt die EVCS „e-Golf, angenommen“, der Tesla wird dort nicht erkannt, und nach
         ≤ 60 s schließt Tessie ihn aus (Tessie meldet `plugged` bis zu 60 s veraltet).
       - Lädt der Tesla am Wall Connector und ändert die Regelung seinen Strom, erscheint er dort
         nie als Gast und die Zuordnung nie als unklar, und `current` schließt ihn dort nie aus.
       - Ein Gast an der EVCS bleibt nach Stromänderungen erkannt und wechselt nicht zum e-Golf: Der
         core leitet die Phasenzahl der EVCS aus Leistung und Strom ab, die nicht gleichzeitig
         gemessen werden, und ein abweichender Zyklus startet die 60 s der Phasen-Signatur neu.
     - **Inbetriebnahme Punkt 3** (Design 2c-2, 11.1; Betriebshandbuch, „Zuordnung Auto und Wallbox“,
       „Inbetriebnahme“): Die Erkennung läuft im Dry-Run mit, und `GET /v1/health` zeigt unter
       `wallboxes`, wer wo steht (dafür muss der core regeln: Regelbetrieb an, sonst ruht die
       Regelung). Prüfe, ob an jeder Wallbox mit Auto das richtige steht, ob „unklar“ ausbleibt, wie
       schnell `home_entity` das Ankommen des e-Golf meldet (steht nach dem Anstecken zuerst ein
       „Gast, angenommen“ an der EVCS, ist der Tracker zu spät) und ob ein Tesla an der EVCS, ein
       e-Golf am Wall Connector und ein Gast erkannt werden (rechne mit einigen Minuten: 60 s
       Kandidatur, und Tessie meldet ein neu angestecktes Auto bis zu 10 min verspätet).
  3. EVCS-Teil-2 „Verbindung unterbrochen“ und Victron 17.2-4 nachholen (17.2-4 ist Voraussetzung der
     Live-Schaltung von Victron, Punkt 4):
     - EVCS: den Schritt „Verbindung unterbrochen“ (Punkt `evcs` erneut, die Rückfrage mit `j`
       beantworten und das Netzwerkkabel der EVCS ziehen, wenn das Werkzeug es verlangt).
     - Victron: 17.2-4 „Kein Akku ins Auto“, während ein Auto an der EVCS lädt (die übrigen
       Rückfragen mit `n`). Dieser Punkt kommt vor Punkt 4: Dynamic ESS dafür vorübergehend abschalten
       (Entscheidung des Betreibers vom 12.10.2026) und danach wieder einschalten; erst ab dem Dry-Run
       des Victron-Teils (Punkt 4.1) bleibt DESS aus. Der Multi muss dabei auf „Ein“ stehen.
     - Die Berichte nach `docs/verification/` übernehmen.
     - Das verstellt die EVCS (Punkt `evcs`: Strom und „Laden an/aus“) und den Netz-Sollwert und
       damit den Akku (17.2-4 schreibt nur den Override), während evcc die EVCS regelt; den Tesla
       berührt keiner der Schritte. Notiere die Zeit, damit du die Entscheidungen dieser Zeit beim
       Vergleich mit evcc ausnimmst.
  4. **Victron in den Dry-Run und live** (Design 2c-2, 11.1; Betriebshandbuch, „Akku und Spitzen“,
     „Victron in den Dry-Run und live“), in dieser Reihenfolge (nach dem Ausrollen, Punkt 1):
     1. **DESS aus für den Dry-Run:** Solange DESS an ist, ist Victron gesperrt, und der Akku-Sollwert
        sendet auch im Dry-Run nichts. Das Fenster dafür bestimmt der Betreiber; bis Phase 3 läuft der
        Akku dann ohne Preisoptimierung.
     2. **Im Dry-Run beobachten:** wie viele Entscheidungen an `victron` je Stunde entstehen (im
        Simulator rund 67 unter schnell wechselnden Wolken, `akku-wolkig`), ob das Rauschen der
        Hauslast das Totband von 200 W übersteigt, und **ob der Wächter bei kleinen Senkungen
        nachdrosselt** (Entscheidungen mit `peak_guard`). Die Drosselung hat kein Totband: Im Modell
        gab es kein Nachdrosseln aus Rauschen, seit der Nachrunde aber eines aus dem alten Mittel
        (Boost-Abwandlung von `spitze-15min`, unter „Bewusst offen gelassen“). Nach einer Drosselung
        klingt das alte 30-s-Mittel genau mit der Wartezeit von 30 s ab (`SETTLE_S`, eine Sekunde
        Abstand), und das Modell hat kein Netzrauschen. An der Anlage könnten kleine Nachdrosselungen
        und eine verzögerte Aufhebung entstehen, die Marge `peak_margin_kw` fängt sie ab. Dann
        entscheidet der Betreiber, ob ein Totband in die Drosselung kommt (etwa eine Senkung unter
        100 W ignorieren). Stufen sind seit der Nachrunde nicht gewollt: Wollen die Autos mehr als
        die feste Grenze von Regel 2 und liegt `P_zul` darunter, senkt der Wächter mit Budget einmal
        um den Unterschied (in `spitze-15min` rund 300 W, 2 Entscheidungen mit `peak_guard` an der
        EVCS in 4440 Schritten: der e-Golf in der zweiten und dritten Viertelstunde je einmal von 16
        auf 15 A). Mehrere Entscheidungen mit `peak_guard` in kurzer Folge an derselben Wallbox wären
        also ein Befund.
     3. **Prüfprotokoll Teil 2, Victron 17.2-4 „Kein Akku ins Auto“,** falls es nicht schon unter
        Punkt 3 bestanden ist; DESS ist hier schon aus. Es prüft genau den Mechanismus des
        Akku-Sollwerts.
     4. **`victron.live_allowed: true`** in `config.yaml`, den core neu starten, die `config.ini`
        neu erzeugen und mit `install.sh` übertragen. Die Override-Aktion steht nur in einer
        `config.ini`, die mit der Freigabe erzeugt wurde; die Probe braucht sie, deshalb kommt die
        Freigabe vor der Probe (Design 2c-2, 11.1 nennt sie danach). Die Freigabe allein schaltet
        Victron nicht live.
     5. **Watchdog-Probe mit vorher verstellten Victron-Werten:** Heute verstellt nur der Punkt
        `evcs` von `verify_part2` etwas, und zwar den EVCS-Strom. Min-SOC, Entladegrenze und
        Override verstellt man vorher von Hand und prüft nach der Probe, dass die Auslösung sie
        zurückgesetzt hat.
     6. **Victron live schalten:** per Absicht (`"actuators": {"victron": "live"}`). DESS bleibt
        danach aus; schaltet man es wieder ein, sperrt sich Victron, und der Override verfällt nach
        300 s.
  - Teil 2 schreibt auf die Geräte: nicht während der 7 Tage der Abnahme Phase 1 und ihres
    24-h-Mitschnitts (`docs/verification/abnahme-phase-1.md`, 4.2 und 4.3).
  - **Vor der Live-Schaltung von EVCS und Tesla** waren diese Punkte zu entscheiden oder umzusetzen
    (Gesamtreview von 2c-1; je Befund und Abhilfe, knapp auch im Betriebshandbuch, „Laderegelung“,
    „Hinweise für die Anlage“). **Stand nach Plan 2d:** Die Punkte 1 bis 3 sind umgesetzt, 4, 6 und 7
    waren es schon mit 2c-2a, und 5 und 8 sind mit einer Entscheidung abgeschlossen. Offen ist nur,
    was an der Anlage zu prüfen ist (Dry-Run, drei Prüfungen zur Zuordnung, Teil 2). Dann gehen EVCS
    und Tesla gemeinsam mit dem Live-Schalter live (Betriebshandbuch, „Umstieg von evcc“):
    1. **Stundenbudget des Tesla** — erledigt mit Plan 2d: Unter PV-Wolken verbrauchte die Regelung die
       20 Befehle je Stunde (im Simulator in `wolkig` nach 2410 s; 8 von 19 Strombefehlen gingen an
       die 5-A-Eigenheit), und danach wirkten auch ein Stopp und ein Moduswechsel erst später (in
       einer Probe im Simulator wirkte „Aus“ nach 19 min), gegen Design 6.3. Jetzt ist der Stopp in
       „Aus“ dringend und umgeht die Stundenlimits, und `control.tesla_reserve_commands` (4) hält
       Befehle frei: Ist das Budget bis auf die Reserve verbraucht, geht ein Strombefehl nur noch
       hinaus, wenn er den Strom senkt und der Ist-Strom über der Grenze des Wächters liegt.
       Starts, Stopps, der Höchststrom an der EVCS und dringende Befehle sind frei. Im Simulator
       gibt es in `dry-run` und `tesla-aus` keine Zurückstellung wegen des Stundenlimits mehr.
    2. **Selbststart im laufenden Vorgang** — erledigt mit Plan 2d: Startet das Auto selbst
       (Tesla-App), sendet die Regelung nach 120 s gegen den Modus „Laden aus“ erneut, je Fahrzeug
       höchstens alle 600 s, im Start/Stopp-Budget (im Modus „Aus“ dringend). Szenario
       `tesla-selbststart`: gestoppt binnen 210 s.
    3. **Ansteckerkennung des Tesla** — erledigt mit Plan 2d: Tessie fragt ein abgestecktes Auto zu
       Hause alle 60 s ab (sonst weiter alle 600 s) und sofort, frühestens 10 s nach der letzten
       Abfrage, wenn an einer Wallbox ein Fahrzeug erscheint (`TessieAdapter.poll_soon`).
    4. **Ausnahme in der Regelung** — erledigt mit Plan 2c-2a: `RegulationLink` fängt jede Ausnahme
       des Kerns. Die Regelung ruht in diesem Zyklus (`idle`; wirft auch das, wird der Kern neu
       gebaut), die Laufzeit führt keinen Befehl der Regelung mehr aus, der Heartbeat bleibt aus, und
       der Keeper läuft weiter. Der Alarm `control.exception` (Fehler) bleibt, bis ein Zyklus mit
       Regelung ohne Ausnahme läuft, auch wenn die Regelung zwischendurch ruht (etwa nach dem
       Abschalten des Regelbetriebs); das Log nennt die erste Ausnahme einer Folge mit Traceback.
    5. **5-A-Eigenheit:** Befehle auf oder von 5 A gehen über Tessie beim ersten Mal verloren und
       kosten einen Neuversuch. Seit 2c-2a kommt hinzu: Startet der Tesla nachts in Min+PV mit 16 A,
       lädt er 90 s mit 16 A, bis der Befehl auf 5 A wirkt. Regel 2 begrenzt den Override auf 8 kW, und
       der Akku liefert den Rest: im Simulator bis zu 5,8 kW in die Autos für 90 s, obwohl die
       Unterstützung auf 3 kW begrenzt ist (`akku-minpv` startet den Tesla deshalb mit 5 A).
       Entscheidung (Plan 2d): Der Code bleibt bei 5 A (Design 7.2). Für die `config.yaml` der
       Anlage empfiehlt sich `vehicles.tesla.min_a: 6`; das entscheidet der Betreiber dort.
    6. **Simulator** — erledigt mit Plan 2c-2a: Das Szenario `schieflast-pv` (PV konstant 12 kW,
       5,5 kW mehr Last auf L1 ab 10 min) lässt das Importziel binden. Der e-Golf geht über `import_limit`
       von 11 auf 8 A, die längste Zeit über dem Ziel in PV-Modi ist 6 s (Grenze 10 s).
    7. **Technische Schulden vor 2c-2** — erledigt mit Plan 2c-2a (Umbau in den ersten drei Tasks, ohne
       geänderte Testdatei): `charging.py` gab die Ladepunkte und ihre Verteilung (`charge_points.py`),
       den Tesla-Teil (`charging_tesla.py`) und die Merker der letzten eigenen Werte (`last_values.py`)
       ab, die Verdrahtung der Regelung zog aus `runtime.py` nach `regulation_link.py`, und die
       doppelten Hilfen sind zusammengeführt (`held_since` und `lasted` in `timing.py`, `ok_number` in
       `charge_inputs.py`, `PV_SURPLUS` in `charge_modes.py`). Die Zeilenzahlen heute (`wc -l`):
       `charging.py` 895 (vorher 1065; die Grenzen des Wächters und `cars()` kamen dazu, der Plan
       nannte rund 700), `runtime.py` 1296 (vorher 1289, geplant rund 1200: Die Verdrahtung ist
       ausgezogen, dafür kamen die Erwartung des Overrides, das Budget und die Viertelstunde des
       Wächters dazu), `regulation.py` 300, `regulation_link.py` 325, `battery.py` 605, `peak_guard.py`
       661, `charge_points.py` 183, `charging_tesla.py` 194 und `last_values.py` 164. `charging.py` und
       `runtime.py` sind damit weiter groß; ein weiterer Abbau wäre ein eigener Umbau (etwa die
       Drosselfunktionen aus `peak_guard.py` nach `peak_throttle.py`, siehe „Bewusst offen gelassen“).

    Dazu kommen die Punkte „Offen vor dem Live-Betrieb des Tesla“ (Reserve des sicheren Zustands,
    Annahmen des Prüffensters) unter „Hinweise für die Pläne 2c-2 und 2d“.
  - **Kleine Nacharbeiten aus dem Gesamtreview von 2c-1:** erledigt am 14.10.2026, je ein Commit
    auf `ccr-9778345e-t3x44k`: Doku-Korrekturen (20 Dry-Run-Entscheidungen an den Tesla, `applied`
    einer Zurückstellung, „rechnet nie mit Akku-Leistung“), die Drossel des Entscheidungslogs
    unterscheidet den angeforderten Wert (ein zurückgestellter Stopp kurz nach einem
    zurückgestellten Start erscheint jetzt), und die LAN-Adressen der Anlage sind durch Platzhalter
    ersetzt (die Git-Historie behält sie).
- **Offen an der Anlage, aus Phase 1 und 2a:**
  1. Die manuellen Punkte von Teil 1 nachtragen: Sicherheitsprofil und Modbus-Dienstliste
     (17.1-3), NTP am Cerbo (17.1-12).
  2. Die Log-Zeile „Aktionen abgeschlossen: …“ der Probe vom 11.10.2026 nachreichen; sie zeigt, ob
     jede Aktion „ok“ oder „unverändert“ war.

## Nächste Schritte im Projekt

1. **Abnahme der Phase 1** nach `docs/verification/abnahme-phase-1.md`:
   - 7 Tage lückenlose Aufzeichnung (Tagesbilanz „96 von 96 · 100 %“)
   - Tagesbilanz innerhalb ±2 % von VRM
   - Preise und Prognose sichtbar
   - nachweislich keine Schreibpfade zu Geräten
2. **Phase 2**, Wallbox-Lastmanagement und PV-Überschussladen (Spec 18), in vier Plänen
   (Zuschnitt in `docs/superpowers/plans/2026-10-10-phase-2a-sicherheitsbasis.md`):
   - **2a Sicherheitsbasis:** umgesetzt, im Gesamtreview geprüft und an der Anlage installiert;
     die Watchdog-Probe ist bestanden (11.10.2026).
   - **2b Aktoren:** umgesetzt, je Task und im Gesamtreview geprüft, auf `main` und an der Anlage
     ausgerollt (12.10.2026); das Prüfprotokoll Teil 2 ist bis auf zwei Schritte bestanden (siehe
     „Offen an der Anlage“).
     - Aktoren für EVCS, Victron und Tesla mit Dry-Run und Rücklese-Prüfung
     - sicherer Zustand (Spec 8.5) und Übergabe am Ende des Live-Betriebs
     - Entscheidungslog im core (Spool, Stream, `GET /v1/decisions`)
     - Erkennung fremder Änderungen (Spec 7.9)
     - Werkzeug `verify_part2` für das Prüfprotokoll Teil 2, schreibend und gemeinsam mit dem
       Betreiber (Spec 17.2); an der Anlage am 12.10.2026 ausgeführt
     - Watchdog 1.1.0 mit der Aktion für den hub4-Override
   - **2c Regelung**, geteilt (Entscheidung des Betreibers vom 12.10.2026):
     - **2c-1 Laderegelung:** umgesetzt, je Task und im Gesamtreview geprüft, an der Anlage noch
       nicht ausgerollt (siehe Kurzfassung): Modi Sofort, PV, Min+PV, Aus und Boost, Phasenwächter,
       Tesla-Steuerung, vereinfachte Zuordnung Auto ↔ Wallbox, Degradation der Ladepunkte,
       Anlagensimulator mit Regelkreistests in der CI.
     - **2c-2a Akku und Spitzen:** umgesetzt, je Task und im Gesamtreview geprüft, auf `main`
       (`31721a6`, 15.10.2026): Umbau, Befehle der Regelung an Victron
       (Akku-Unterstützung und Setpoint-Kompensation, Spec 7.6 und 7.7, Auffrischen des Overrides vor
       seinem Timeout von 5 min), 15-min-Wächter (Spec 7.4), Simulator mit 18 Szenarien (mit 2d heute 23).
     - **2c-2b Zuordnung:** umgesetzt, je Task und im Gesamtreview geprüft, die Fix-Welle danach
       umgesetzt, auf `main` (`31721a6`, 15.10.2026): die volle Zuordnungserkennung (Spec 7.8, Design
       2c-2, Abschnitt 7) samt Gastfahrzeug und `vehicles.<name>.home_entity`, der Grund
       `assignment_detected`, Health je Wallbox und im Simulator der e-Golf „zu Hause“ oder „weg“,
       das Gastfahrzeug, jedes Auto an jeder Wallbox und die Szenarien aus Design 10.3 (damals 21, mit 2d 23).
   - **2d Bedienung:** umgesetzt, im Gesamtreview geprüft, auf `main` (`0198900`, 16.10.2026): Absicht aus der app
     (Regelbetrieb, Live-Schalter mit Bestätigung, Lademodi, Boost, Zuordnung, Gast-Modus, Vorrang),
     Entscheidungslog in SQLite, UI-Seiten „Laden“ und „Entscheidungen“, HA-Bedienelemente und
     Benachrichtigungen, die Tesla-Punkte vor der Live-Schaltung. Ziel-SOC und Abfahrt gehören zum
     Modus „Geplant“ und kommen mit Phase 4.

   Die Abnahme der Phase 2 besteht aus 2 Wochen Dry-Run, der schon nach 2c-1 beginnt, während 2c-2
   und 2d entstehen, und der Live-Schaltung danach. Abweichend von der Spec gehen EVCS und Tesla
   **gemeinsam** live, mit dem Live-Schalter im UI (seit 2d) und ganz abgeschaltetem evcc;
   Victron geht nach 2c-2a live, sobald Dynamic ESS aus ist (Design 2c-1, Abschnitt 14; Design 2c-2,
   Abschnitt 11.1).

   **EVCS in 2b:**
   - **Lesen:** Seit der korrigierten IP-Whitelist antwortet die EVCS BindaEMS direkt per Modbus
     (Prüfprotokoll Teil 1 vom 11.10.2026); gelesen wird weiter so.
   - **Schreiben:** über den GX-Dienst `evcharger` des Cerbo (`/SetCurrent`, `/StartStop`), wie
     der Watchdog (Entscheidung des Betreibers vom 11.10.2026). Das Prüfprotokoll Teil 2 hat den Weg
     an der Anlage am 12.10.2026 bestätigt.
   - Geschrieben wird nie der Maximalstrom (5017).

3. **Phase 3**, Planung und Optimierung (Spec 18), in drei Plänen (Entscheidung des Betreibers vom
   16.10.2026; Einordnung im Plan 3a):
   - **3a Tarif und Prognosen:** umgesetzt, je Task und im Gesamtreview geprüft, die Fix-Welle danach
     umgesetzt, auf dem Branch `claude/project-thread-syy5yu`; die Integration auf `main` steht aus
     (Kurzfassung). Nur app, der core bleibt unverändert.
   - **3b Optimierer und Zeitplan:** Plan-Typen, Optimierer (HiGHS über PuLP, eigener Prozess,
     Zeitlimit) nur für den Akku, Regel R1, Wirtschaftlichkeit, Schalter „Netzladen erlaubt“,
     Planungsablauf mit Speicherung, UI-Seite „Zeitplan“, HA-Sensoren und Taste „neu planen“,
     Szenario-Test-Umgebung mit dem Vergleich mit „Victron allein“. Pläne entstehen als Vorschau; der
     core bekommt sie noch nicht. Den Plan schreibt die nächste Sitzung (`superpowers:writing-plans`)
     und legt ihn dem Betreiber zur Freigabe vor.
   - **3c Planausführung im core:** Plan in der Absicht, Akku-Absichten `EIGENVERBRAUCH`, `HALTEN` und
     `NETZLADEN(P)`, Budget des 15-min-Wächters aus der akzeptierten Monatsspitze, Simulator mit
     Plan-Szenarien, „Plan vs. Ist“. Danach 2 Wochen Dry-Run der Planausführung, dann geht Victron live
     (Abnahme Phase 3).

### Nächster Schritt: Integration von Plan 3a, Plan 3b und die Anlage

1. Plan 2b ist auf `main`; Gesamtreview, Fix-Durchgang und Re-Review sind erledigt, die
   Entscheidungen stehen in `docs/superpowers/plans/2026-10-11-phase-2b-entscheidungen.md`. Plan
   2c-1 ist umgesetzt, je Task und im Gesamtreview geprüft, und die Fix-Welle danach ist umgesetzt;
   er ist auf `main`, ebenso die EVCS-Hysterese und die kleinen Nacharbeiten danach (`47a09a7`).
   Die Entscheidungen von 2c-1 stehen im Kopf des Designs und in
   den Absätzen „Stand Plan 2c-1“ der Spec, die offenen Punkte vor der Live-Schaltung in „Offen an
   der Anlage“.
2. An der Anlage die Schritte aus „Offen an der Anlage“.
3. Plan 2c-2a ist umgesetzt und im Gesamtreview geprüft, die Fix-Welle danach ist umgesetzt
   (`docs/superpowers/plans/2026-10-14-phase-2c2a-akku-spitzen.md`). Plan 2c-2b ist umgesetzt, je
   Task und im Gesamtreview geprüft, und die Fix-Welle danach ist umgesetzt
   (`docs/superpowers/plans/2026-10-15-phase-2c2b-zuordnung.md`). Beide sind auf `main`
   (`31721a6`, Fast-Forward mit Zustimmung des Betreibers vom 15.10.2026, Tests auf `main` grün).
4. **Plan 2d** (Bedienung in UI und HA) ist umgesetzt (Kurzfassung): die Auswahl des Gast-Modus, die
   Anzeige von Health je Wallbox, die Übersteuerung der Zuordnung und die Tesla-Punkte vor der
   Live-Schaltung. Gesamtreview und Fix-Welle sind erledigt, er ist auf `main` (`0198900`,
   Fast-Forward mit Zustimmung des Betreibers vom 16.10.2026, alle Prüfungen auf `main` grün). An der
   Anlage folgt die
   **Inbetriebnahme Punkt 3** (Design 2c-2, 11.1): Die Erkennung läuft im Dry-Run mit, und Health
   zeigt, wer wo steht. Sie braucht nicht 2d, nur die ausgerollten Images; die Schritte stehen unter
   „Offen an der Anlage“.
   - **Boost und Übersteuerung:** Eine neue Version der Absicht bindet jedes Fahrzeug neu, das
     `boost_until` oder `wallbox` nennt. Die app schickt deshalb einen abgelaufenen Boost nicht
     zurück (`IntentService`, Betriebshandbuch 13, „Absicht und Regelbetrieb“); wer eine Absicht
     per `curl` sendet, lässt beides weg, wo es nicht mehr gelten soll.
5. **Plan 3a** (Tarif und Prognosen) ist umgesetzt und im Gesamtreview geprüft (Kurzfassung), die
   Fix-Welle danach ist umgesetzt. Es folgen:
   - die **Integration auf `main`** (Fast-Forward, Tests auf `main`, dann Push; nur mit Zustimmung des
     Betreibers)
   - danach der **Plan 3b** „Optimierer und Zeitplan“ (Phase 3, Punkt 3 oben): Brainstorming, Plan,
     Freigabe des Betreibers, Umsetzung
   - an der Anlage das **gemeinsame Ausrollen von 2d und 3a**, sobald 3a auf `main` ist (Punkt „Zu 3a
     gehört“ unter „Offen an der Anlage“); die Lastprognose läuft ohne HA-Historie (Entscheidung
     vom 16.10.2026)

## Hinweise für die Pläne 2c-2 und 2d

Aus den Reviews der Pläne 2a bis 2c-2b. Plan 2c-2a hat die Punkte zur Regelung umgesetzt, Plan 2c-2b
die zur Zuordnung; die Reste stehen dort. Plan 2d hat die Punkte zur Bedienung umgesetzt (unten); die
übrigen Reste gehören zu Phase 3. Was
Plan 2c-1 aus der früheren Liste erledigt hat (Freigabe erst ohne offenen Schreibvorgang, Aufräumen des
Entscheidungslogs, Heartbeat-Zähler je Lauf, SAFE ohne Tesla, der nicht lädt, Auslösung des Watchdogs,
Laden nach einem Inselbetrieb, Selbstprüfung des Multi-Modus), steht in der Kurzfassung.

- **Regelung (2c-2a umgesetzt; Reste für Phase 3):**
  - Befehle der Regelung gehen durch `CommandExecutor.execute`: Er ruft `SafetyLayer.authorize`
    genau einmal je betrachtetem Befehl auf, denn `authorize` verändert Rate-Limiter und
    Schreibbudget. Die Laderegelung zeigt das Muster: Sie berechnet Befehle, die Laufzeit führt sie aus
    und gibt jedes Urteil mit `note_verdict` an sie zurück.
  - **Bei „Regelbetrieb aus“ regelt auch der Akku-Sollwert nicht** (so umgesetzt), nicht in DEGRADED
    oder SAFE mit dem Übergabe-Grund: Dort gehören dem core nur die Sicherwerte der Übergabe.
  - **Importlimit:** Peak Shaving steht an der Anlage bei 20 A je Phase mit „immer“ (gewollt,
    Spec 8.1). Es wirkt nur, solange der Multi auf „Ein“ steht: Mit „Nur Laden“ stieg L3 am
    12.10.2026 auf 33,3 A, nahe an der 35-A-Sicherung. Seit 2c-1 warnen die Selbstprüfungen
    `multi_mode` und `import_limit`, und der Phasenwächter rechnet ohne die Hilfe des Akkus. Die
    Setpoint-Kompensation (2c-2a) berücksichtigt das Importlimit nicht eigens; Regel 2 und das
    Importlimit wirken zusammen (Design 2c-2, 5.4).
  - `SafetyContext.modes` muss `ActuatorMode`-Werte enthalten; ein String „live“ bliebe still im
    Dry-Run. `wallbox_phase_a` ist nach Netzphasen geschlüsselt (der Erzeuger wendet `phase_map`
    an), und `present[(aktor, current)]` ist der Sollstrom, nicht der Messstrom. Das stellt
    `core/control/context.py` bereit.
  - `grid_budget_w` darf nie NaN oder ±∞ sein. Seit 2c-2a erzeugt es der 15-min-Wächter (`PeakGuard`),
    und es ist nie NaN oder ±∞. Es geht nur hinaus, wenn der SOC bekannt ist und über USV-Reserve +
    `control.reserve_guard_pct` liegt, sonst ist es `None` (Spec 8.3, Stand Plan 2c-2a).
  - `VictronMqttAdapter.write` nur aus dem Event-Loop-Thread aufrufen; es reiht nur ein.
  - Die `inputs` der Entscheidungen enthalten seit 2c-1 SOC, Akkuleistung, Netzstrom je Phase,
    Überschuss, Grenze des Wächters und Ist-Werte, seit 2c-2a beim Override auch `pv_w`, `house_w`,
    `cars_w`, `deficit_w`, `allowed_w`, `target_w` und `actual_setpoint_w`. Das Budget steht nur
    mittelbar darin (`target_w`); Preis und Plan kommen mit Phase 3 dazu. Weckregeln für den Tesla im
    Modus „Geplant“ kommen mit Phase 4.
  - **Tesla-Ladestrom:** Über Tessie wirkt ein Befehl, der auf 5 A setzt oder von 5 A weggeht, erst
    beim zweiten Mal, obwohl Tessie den ersten mit `result: true` bestätigt (Prüfprotokoll Teil 2
    vom 12.10.2026, Nachtrag). Der Tesla-Aktor prüft den Strom nach der Bestätigung im Tessie-Cache:
    Es zählt die erste Meldung 10 bis 75 s danach (`SETTLE_S`, `VERIFY_S`; die Frist beträgt 85 s).
    So fängt das Fenster auch die 60-s-Abfrage eines angesteckten, noch nicht ladenden Autos.
    Zeigt die Meldung einen anderen Strom, meldet der Aktor `NOT_TAKEN`; ohne Meldung gilt Tessies
    Bestätigung. Er sendet nicht selbst nach. Die Laderegelung fordert den Strom seit 2c-1 als
    normalen Befehl erneut an (Grund `not_taken_retry`), er wartet auf die 90-s-Regel. Auch einen
    bestätigten Befehl nimmt sie nicht als Wirkung, maßgeblich ist der gemessene Strom. Offen vor dem
    Live-Betrieb des Tesla:
    - **Reserve des sicheren Zustands** (Spec 8.5): Bei Netzausfall sendet der Keeper dem Tesla
      „Laden aus“ alle 10 s erneut, bis der Cache es zeigt, beim Laden bis zu 30 s. Das kostet bis
      zu 3 der 6 Start/Stopp-Befehle je Stunde und alle 3 Versuche des Vorfalls. Zahl und Abstand der
      Versuche oder `min_a` prüfen.
    - **Annahmen des Fensters:** Tessie bestätigt binnen etwa 5 s (bei einer langsameren Antwort
      verkürzt sich das Fenster), und der Cache zeigt das Auto binnen 10 s. Im Lauf vom 12.10.2026
      kam die erste gewertete Meldung erst nach rund 30 s; der Verzug ist damit nicht gemessen.
  - **Netz-Sollwert-Override:** Er verschwindet nach 5 min ohne Auffrischen (Prüfprotokoll Teil 2
    vom 12.10.2026). Seit 2c-2a frischt die Regelung einen gewollten Override alle 240 s auf.
  - **Entladegrenze (Phase 3):** Es gibt einen flüchtigen Override `ess.override.maxdischargepower`.
    Prüfen, ob er die persistente Entladegrenze ersetzen kann; das schont den Flash.
- **Bedienung (2d umgesetzt; was die Punkte unten für die Pflege bedeuten):**
  - **Boost und Übersteuerung:** Eine neue Version der Absicht bindet jedes Fahrzeug neu, das
    `boost_until` oder `wallbox` nennt. Die app schickt deshalb einen abgelaufenen Boost nicht
    zurück (`app/control/intent.py`, `_clean`).
  - Die Absicht kennt seit 2c-1 `vehicles`, `priority` und `surplus_priority` (Spec 5.3). Der core
    lehnt mit 422 und den Gründen in `details` ab: unbekannte Fahrzeuge oder Wallboxen, eine Wallbox
    für mehrere Fahrzeuge und eine Priorität, die nicht jedes Fahrzeug genau einmal nennt. Die app
    zeigt diese Sätze.
  - `GET /v1/health` nennt je Fahrzeug (`vehicles`) Modus, Boost bis, Wallbox, Zuordnung, Grenze,
    angesteckt, lädt, Weckvorgänge heute und den letzten Grund, und seit 2c-2b je Wallbox
    (`wallboxes`), wer dort steht, die Art der Zuordnung und die Indizien; die UI-Seite „Laden“ kann
    sie nutzen. `occupant` ist auch während eines Vorgangs `null`, solange die Zuordnung unklar ist
    und an der Wallbox kein Fahrzeug der Konfiguration steht.
  - **Gast-Modus:** `guest.mode` wählt man jetzt im UI (Karte „Gast“) und in HA („Gast-Lademodus“); der
    Modus gilt für jeden Gast an jeder Wallbox.
  - Die Quittierung von Sperren (Spec 7.9) gibt es seit 2d; sonst lösen sie sich nach
    `safety.lock_release_s`.
  - Die app nimmt für die Version der Absicht Millisekunden seit 1970.
  - Die app speichert die Entscheidungen (SQLite, Markierungen für Grafana) und holt den Rückstand
    mit `GET /v1/decisions`. Die Zeitzone im Query als `%2B` kodieren.
  - Eine erkannte fremde Änderung erzeugt neben Alarm und Sperre seit 2d eine Entscheidung (Ergebnis
    `foreign`).
  - Der LiveFeed der app ignoriert `mode_change`. In 2a reicht die Health-Abfrage alle 10 s.
  - `FakeView` in `tests/integration/test_core_link.py` kennt `intent()`, `submit_intent()` und
    `decisions()` noch nicht.
  - Entscheidungen tragen die Wanduhr. Springt sie zurück (NTP, Failover), bekommen neue
    Entscheidungen Zeiten vor dem letzten `since`, und ein Leser, der mit dem `ts` der letzten
    weiterliest, übersieht sie. Die app gleicht darum über die `id` ab: Der Rückstand beginnt 10 min
    vor dem jüngsten `ts` der Tabelle, und doppelte `id` werden übergangen.
- **Watchdog (vor der Live-Schaltung von Victron):**
  - Nur der Punkt `evcs` von `verify_part2` verstellt vor der Probe etwas, und zwar den EVCS-Strom.
    Min-SOC, Entladegrenze und die Override-Aktion (mit `victron.live_allowed`) prüft bisher nur
    `/LastTripFailures` zusammen mit dem Log des Cerbo (Betriebshandbuch 14, „Probe“).
  - Vor der Live-Schaltung von Victron auch diese Werte vor der Probe verstellen und prüfen, dass
    die Auslösung sie zurücksetzt. Die Schritte stehen unter „Offen an der Anlage“, Punkt 4.

## Bewusst offen gelassen

Entschieden in Phase 1c; im Commit-Verlauf bzw. der Abschlussnachricht begründet:

- HA-Verbraucherwerte bis 24 h alt, ohne Altersangabe; ältere zeigen „kein Wert in den letzten
  24 h“. HA schreibt nur bei Änderung.
- CSP `connect-src 'self'`: Auf sehr alten Safari-Versionen bliebe die Live-Verbindung getrennt.
- Kein Offline-Modus, kein Service Worker.
- Ein Konflikt beim Speichern der Einstellungen (409) bietet Neuladen, aber kein Zusammenführen.
- Der Einstellungs-Editor ändert nur Werte. Zeitfenster und Gültigkeit gehen über den
  YAML-Export/-Import.
- Nach echten 30 min Leerlauf geht ein ungespeicherter Entwurf mit der Abmeldung verloren.
- Die System-Seite zeigt nicht, wenn die Brutto/Netto-Erkennung vom eingestellten Modus abweicht.
- `theme-color` folgt dem System, nicht der manuellen Wahl im UI.
- Ein E2E-Test für die Neuverbindung der Live-Anzeige fehlt. Die Logik ist mit Unit-Tests
  abgedeckt.

Entschieden in Phase 2a; die Rulings stehen im Plan (Präzisierungen) bzw. im Commit-Verlauf:

- Ein Teilsystem wird gesperrt, der ganze core bleibt im Betrieb: Ein fehlgeschlagener Victron-Punkt
  sperrt nur Victron (Präzisierung 1).
- Ohne Bedarf gibt es keinen Heartbeat. Ein laufender core kann den Watchdog mit der 0
  abschalten (Präzisierung 2). Seit 2b besteht Bedarf nur im Regelbetrieb (Plan 2b, Präzisierung 2).
- Sperren lösen sich in 2a nur automatisch. Das Schreibbudget (`write_budget.json`) entsteht erst
  ab 2b; 2a legt nur Format und Klasse fest.
- `PUT /v1/intent` der internen API ist in 2a der einzige Weg zu einer Absicht. Die app sendet sie
  erst ab 2d, und die API prüft die Typen der Felder nicht streng (etwa `"version": true`).
- Die Texte der Plausibilitätsprüfung aus Phase 1 zeigen den Dezimalpunkt („300.0 V“), auch unter
  „Eingangsdaten“ auf der System-Seite.
- Die Probe behandelt nur SIGINT. `docker stop` oder SIGTERM beenden sie ohne Freigabe des
  Watchdogs (Betriebshandbuch 14). Ihre Schritte „scharf“, „Auslösung“ und „Freigabe“ können
  „OK“ melden, ohne dass sich der Zustand geändert hat, wenn der Startzustand schon dem Ziel
  entspricht.
- Die `config.ini` des Watchdogs ändert man nicht von Hand (Betriebshandbuch 14). Täte man es,
  verwürfe der Watchdog einen Abschnitt mit Tippfehler im Kopf (`[actions:x]`) still. Das
  Verzeichnis nach `os.replace` sichern core und Watchdog seit 2b per fsync.
- `health()` des core kann Felder aus zwei Zyklen mischen.
- UI: Ein deaktivierter Watchdog, den der Betrieb braucht, zeigt nur eine Warnung (der core meldet
  einen Fehler-Alarm). Nach einem Verbindungsabbruch zum core bleiben die Karten „Aktoren“ und
  „Watchdog am Cerbo“ bis etwa 25 s ohne Hinweis stehen. Die Datei `ui/src/routes/system/+page.svelte`
  hat rund 500 Zeilen; bei weiterem Wachstum gehören die Karten in eigene Komponenten.

Entschieden in Phase 2c-1; die Rulings stehen im Plan (Präzisierungen), im Kopf des Designs und in den
Absätzen „Stand Plan 2c-1“ der Spec:

- Die EVCS folgt dem Überschuss in ganzen Ampere mit Hysterese beim Erhöhen
  (`control.evcs_hysteresis_a`, 0,5 A; Design 6.5). Ganz ruhig bleibt ihr Strom nur, solange das
  Rauschen des Anteils von Spitze zu Spitze unter der Hysterese liegt; sonst flattert er seltener,
  aber weiter. Den Wert stellt man nach dem Dry-Run nach, ohne neues Image. Ein Zeitpfad („erhöhen
  nach etwa 30 s“) fehlt bewusst: In der Messung brachte er mehr Flattern statt weniger. Bleibt der
  Überschuss ruhig knapp über einer Stufe, lässt die EVCS bis zu 1,5 A liegen; das lädt der
  Hausakku.
- Bei schnell wechselnden Wolken deckt ohne Akku-Sollwert (Victron im Dry-Run) der Hausakku einen
  spürbaren Teil der PV-Ladung des Tesla, denn der Tesla darf seinem Mittel nur alle 90 s folgen. Das
  nimmt das Design in Kauf (Design 6.5). Im Simulator (`wolkig`, Victron im Dry-Run) waren es rund
  40 % seiner Ladeenergie, roh gerechnet: Die Kennzahl „Akku-Energie in PV-Autos“ nimmt die Übergänge
  aus und zeigt dort 0 %, der Bericht nennt den rohen Anteil aller PV-Autos (18 %) als
  Informationszahl. Eine Abhilfe wäre, den Tesla im PV-Modus am vorsichtigen Minimum statt am Mittel
  zu führen; das wäre eine Designänderung und eine Entscheidung des Betreibers. Mit Victron live
  verschiebt der Akku-Sollwert den Fehlbetrag aufs Netz, solange die Unterstützung nicht gilt.
- Ohne Ströme von EVCS oder Wall Connector startet die Regelung kein Auto (Design 5.5), auch wenn die
  Grenze sonst reichte.
- Der Tessie-Adapter trennt seine Quelle bei fehlschlagenden Abfragen nicht (nur beim Abbruch). Die
  Stufe „Tessie nicht erreichbar: Tesla nicht steuerbar“ (Spec 8.4) greift an der Anlage darum erst
  über die Fehler-Sperre des Aktors oder das Veralten der Werte nach 900 s.

Entschieden in Phase 2c-2a; die Rulings stehen im Plan (Präzisierungen), im Kopf des Designs und in
den Absätzen „Stand Plan 2c-2a“ der Spec. Hier die Entscheidungen mit Kosten, falls sie falsch sind:

- **Umbau:** `charging.py` und `runtime.py` sind nach dem Umbau größer geblieben als geplant
  (Zeilenzahlen: Liste „Vor der Live-Schaltung“, Punkt 7), denn die Tabellen der Tasks waren
  vollständig umgesetzt; mehr zu verschieben stand nicht im Plan (kosmetisch). Ein einziger
  bestehender Test wurde gelöscht, der das alte Verhalten festschrieb, dass eine Ausnahme der
  Regelung den ganzen Zyklus abbricht und der Keeper nicht schreibt; der neue Test
  `test_exception_in_the_regulation_keeps_the_keeper_running` deckt den Keeper ab (falls falsch: Die
  Versuchszählung des Keepers bei einer Ausnahme ist ungeprüft).
- **Alarm `control.exception`:** Er bleibt, bis ein Zyklus mit Regelung ohne Ausnahme läuft, auch
  wenn die Regelung zwischendurch ruht (etwa nach dem Abschalten des Regelbetriebs), und sein „seit“
  ist der erste gescheiterte Zyklus der Folge; der Heartbeat entscheidet dagegen je Zyklus. Sonst
  verschwände die Ursache, sobald der core nach der Auslösung des Watchdogs in SAFE geht, und der
  Alarm flatterte (falls falsch: Er steht länger als nötig).
- **Konfiguration:** Die acht neuen Felder von `ControlConfig` lehnen ausdrücklich ±∞ und NaN ab,
  weil `grid_budget_w` nie NaN oder ±∞ sein darf und ein unendliches Ziel ein unendliches `P_zul`
  ergäbe. Neun ältere Felder von `ControlConfig` (aus 2c-1) und `grid.max_import_kw` akzeptieren
  weiter +∞ (NaN und −∞ lehnen sie ab). Falls falsch: Eine Konfiguration mit `.inf` wird abgelehnt,
  was ohnehin sinnlos wäre.
- **Auffrischen:** `refresh` wirkt nur beim Netz-Sollwert, auch in der Dringlichkeit der
  Safety-Schicht und in der stillen Regel des Ausführers. Nach einer erkannten fremden Änderung hat
  die neue Erwartung keine Frist, denn sie ist dann der fremde Wert, nicht der eigene Override. Ein
  Override, dessen Alter nach einem Neustart unbekannt ist (der Cerbo hielt ihn schon), wird sofort
  aufgefrischt (falls falsch: ein Schreibauftrag mehr nach Start oder Keeper). Der Akku-Sollwert
  ignoriert Urteile und Ergebnisse fremder Schlüssel, damit keiner den Takt des Auffrischens
  verschiebt.
- **Akku-Sollwert:** `cars()` nennt jeden Ladepunkt, auch den eines abgesteckten Fahrzeugs an seiner
  Standard-Wallbox (0 W). Ein nicht endliches Budget macht das Ziel unbekannt. `W'` rechnet mit dem
  auf 10 W abgerundeten Budget, sonst klemmte Regel 2 fast jede Entscheidung, bei der das Budget
  bindet, auf `P_zul` (kein Vielfaches von 10 W, `range.ceiling` im Log); falls falsch, läge der
  Override bis zu 10 W unter dem Budget. Die Invarianten aus Design 10.4 (nie negativ, nie mehr als
  der Fehlbetrag, mehr erlaubter Akku-Anteil ergibt nie mehr Netzbezug) prüft ein Property-Test auch
  am Bedarf aus `assess`.
- **Wächter:** Er integriert Boost nach dem Halteprinzip der Abrechnung, und beide teilen
  `HOLD_MAX_S` (5 s). Integrierte er über Lücken bis 10 s, die Abrechnung aber nur bis 5 s, zöge er
  Energie ab, die in `grid_wh` nie stand (`E_bisher` zu klein, `P_zul` zu groß). Springt die Wanduhr
  zurück, ist die Viertelstunde lückenhaft. `exceeded` hat bis zum Ende der Viertelstunde Vorrang
  vor `gap`. Eine `QuarterObs` mit anderem Beginn als die laufende Viertelstunde zählt wie keine,
  ein kurzer Ausfall von `grid_w` (bis 10 s) rechnet mit `grid_wh` und dem Mittel weiter, und ein
  `P_zul`, das keine endliche Zahl ist, macht den Schritt zu einem, der nicht rechnet. Der Alarm
  `peak.exceeded` endet mit seiner Viertelstunde, auch wenn die Regelung ruht
  (`Regulation.alarms(wall_now)`). Nur ein Auto an einem geregelten Ladepunkt wird gedrosselt;
  ungeregelte Autos ohne Boost gehen gar nicht in den Wächter.
- **Drosselung:** Eine Senkung, die die Sicherung allein verlangt, bleibt dringend, auch wenn die
  Grenze des Wächters niedriger liegt (Sicherheit vor Wortlaut; falls falsch: ein dringender Befehl
  mehr). Wechselt ein gedrosseltes Auto zwischen PV-Überschuss, Min+PV und Sofort, verliert es seine
  Grenze still, ohne neue Wartezeit: Sonst behielte ein Auto, das von PV (0 W) auf Sofort wechselt,
  eine Grenze unter dem Boden von Sofort. Das gilt auch für ein Auto, das geht oder in Boost oder
  Aus wechselt (falls falsch: nach einem Moduswechsel kurz mehr Netzbezug, bis die nächste
  Drosselung nach 30 s greift). Die Senkung je Auto ist nie negativ; ohne Leistung zählt ein
  gedrosseltes Auto als nicht gefolgt. `peak_guard.py` blieb ungeteilt, nach der Nachrunde mit 661
  Zeilen (falls falsch: ein späterer Umzug der Drosselfunktionen nach `peak_throttle.py`).
- **Simulator:** `_Boost` trägt das absolute Ende des Boosts. Die Kennzahl der Akku-Energie im Auto
  überspringt je Entladefolge die ersten `TRANSITION_S` s, ohne den Tesla-Fall. Ein Auffrischen im
  Dry-Run zählt dort als eigener Wert ohne Entscheidung, sonst meldete die Kennzahl falsche
  Wiederholungen.
- **Szenarien:** Zwei haben einen angepassten Aufbau (Betriebshandbuch, „Simulator“), beide aus
  demselben Grund: Ihre Prüfung ließ sich im Modell sonst nicht erfüllen, obwohl die Regelung Plan
  und Design folgte. `akku-minpv` startet den Tesla bei 5 A (mit 16 A lädt er 90 s voll, Regel 2
  kappt bei 8 kW), `schieflast-pv` bringt die Last auf L1 erst nach 10 min (Notiz unten).
  `spitze-15min` startete den Tesla bis zur Nachrunde ebenfalls bei 5 A; mit ihren Regeln besteht
  es alle Prüfungen auch mit 16 A, wie im Plan. Seine Prüfung der Reihenfolge belegt trotzdem wenig
  und ist im Handbuch so benannt: Den e-Golf drosselt der Wächter erst, wenn der Tesla schon am
  Mindeststrom lädt, und eine falsche Reihenfolge fiele in der ersten Viertelstunde mit einer
  Senkung des Phasenwächters zusammen. Die Reihenfolge belegen die Unit- und Property-Tests der
  Drosselung (falls falsch: kein Ende-zu-Ende-Beleg; ein Szenario mit einem PV-Auto über dem
  Mindeststrom im Wächter wäre nötig).
- **Kein Totband in der Drosselung:** Im Modell war sie nicht nötig (seit der Nachrunde gibt es dort
  eine kleine Nachdrosselung aus dem alten Mittel, unten); die Beobachtung an der Anlage steht unter
  „Offen an der Anlage“, Punkt 4 (falls falsch: kleine Nachdrosselungen und verzögerte Aufhebung,
  begrenzt durch die Marge).

Der Gesamtreview von Plan 2c-2a (15.10.2026) fand zwei wichtige Lücken in der Verdrahtung des
15-min-Wächters und zwei Kleinigkeiten. Die Fix-Welle danach hat alle behoben, die beiden Lücken und
das Totband je mit einem Test, der vorher scheiterte; die Präzisierungen 9 und 10 des Plans, die
Absätze „Stand Plan 2c-2a“ der Spec, der Kopf des Designs 2c-2 und das Betriebshandbuch sind
nachgezogen.

- **Gewollter Netzbezug nur bis zur festen Grenze von Regel 2:** Der Wächter verglich `Basis + W`
  ungekappt mit `P_zul`. Regel 2 begrenzt den Override aber auf `max_grid_charge_setpoint_w`, die
  Absicherung und `max_import_kw`, und darüber liefert der Akku (Design 2c-2, Entscheidung 3). Zwei
  Autos in Sofort mit zusammen 18 kW wären bei einem Ziel von 10 kW um rund 10 kW gedrosselt worden,
  obwohl der Netzbezug ohnehin bei 8 kW blieb. Jetzt gibt der Kern dem Wächter `W` höchstens bis zu
  dieser Grenze, und solange Victron gesperrt oder nicht steuerbar ist, zählt nur das Mittel. In
  `spitze-15min` (Ziel 8 kW, `P_zul` knapp unter der Grenze) drosselte der Wächter den e-Golf danach
  je Viertelstunde in Stufen von 1 A statt in einem Schritt auf 9 A. Diese Stufen und die langsame
  Senkung ohne Budget, die die Kappung mit sich brachte, hat die Nachrunde beseitigt (unten).
- **Boost-Anteil mit PV und Akku zuerst an Boost:** Den Netzanteil, den der Wächter für Boost abzieht,
  nahm er aus der Verteilung der Abrechnung, die PV und Akku in der Reihenfolge von `config.yaml`
  verteilt (erst die EVCS). Lud der Tesla in Boost und der e-Golf in Sofort, buchte sie den Netzbezug
  des e-Golf auf den Wall Connector, und der Wächter zog ihn als Boost ab: im Simulator 1,51 kWh je
  Viertelstunde bei einem Ziel von 5 kW, ohne `peak.exceeded`. Jetzt rechnet die Verdrahtung den
  Anteil aus denselben abgeleiteten Größen ein zweites Mal, mit PV und Akku zuerst für die Wallboxen
  in Boost; dieselbe Probe blieb bei 1,24 kWh, seit der Nachrunde bei 1,20 kWh (unten; Test in
  `tests/integration/test_sim_scenarios.py`).
  Wer in Boost ist, weiß der Link aus dem letzten Zyklus des Kerns (einen Zyklus alt, so
  entschieden).
- **Kleinigkeiten:** `battery.override_w` in Health ist der angeforderte Wert vor den Regeln 2 und 4
  (jetzt so dokumentiert; was der Cerbo hält, zeigt `actual_setpoint_w`), und
  `control.setpoint_deadband_w` muss über 0 liegen. Dazu kamen Tests für den Stream in einem
  gescheiterten Zyklus und für die Frist der Erwartung eines Overrides (`_lapses_at`), die Reihenfolge
  der Inbetriebnahme im Kopf des Designs 2c-2 und kleine Korrekturen in dieser Übergabe und im
  Betriebshandbuch.
- **Offen:** Ein Szenario Ende zu Ende mit einem PV-Auto über seinem Mindeststrom im Wächter würde die
  Lücke der Prüfung der Reihenfolge in `spitze-15min` schließen (oben, „Szenarien“).

Die Nachrunde zu Plan 2c-2a (15.10.2026) hat die beiden Restpunkte der Drosselung geschlossen, die
nach der Fix-Welle blieben, jeden mit Tests, die vorher scheiterten; die Präzisierungen 10 und 11
des Plans, die Spec (7.4), der Kopf des Designs 2c-2 und das Betriebshandbuch sind nachgezogen.

- **Senkung bei einem Ziel unter der festen Grenze:** Mit der Kappung bei `C − Basis` blieb die
  verlangte Senkung `C − P_zul`, solange die Autos mehr als die feste Grenze `C` wollten. Mit Budget
  für den Akku senkte der Wächter so nach jeder Wartezeit erneut um diesen Unterschied und nahm dem
  Akku, was er nach Entscheidung 3 über `C` liefert; ohne Budget hält Regel 2 den Override bei `C`,
  und der Netzbezug lag minutenlang über `P_zul`, während der Wächter in Stufen senkte. Jetzt
  bekommt der Wächter `W` ungekappt und kennt `C` selbst. Mit Budget rechnet er mit dem Netzbezug
  ohne seine eigenen, gemerkten Senkungen und senkt insgesamt nur um den Teil unter `C`, der über
  `P_zul` liegt, und das einmal; ohne Budget senkt er, solange `P_zul` unter `C` liegt, in einem
  Schritt um alles, was über `P_zul` liegt, und sonst nicht (Fix-Runde, unten). In `spitze-15min`
  gibt es statt 20 Entscheidungen mit `peak_guard` an der EVCS (Stufen bis
  10 oder 9 A) noch 2, je einmal von 16 auf 15 A; der e-Golf lädt dort 8,85 statt 7,33 kWh, der
  Akku liefert 6,86 statt 5,24 kWh (mit dem alten Aufbau, den Tesla bei 5 A, wären es 8,76 und
  6,58 kWh), und die geprüften Viertelstunden liegen bei 1,961, 1,964 und 1,964 kWh (vorher 1,955,
  1,918 und 1,918, Grenze 2,02). Falls falsch: Eine gemerkte Senkung zählt auch, wenn ihre Grenze
  nicht mehr bindet, etwa beim Tesla, der von selbst auf 5 A ging; den Unterschied von höchstens
  `C − P_zul` liefert dann bis zum Ende der Viertelstunde der Akku.
- **Drosselung bei der echten Leistung:** Ein Auto mit Grenze sank bisher von der Grenze aus. Lag
  sie weit über seiner Leistung, etwa beim Tesla in Min+PV, den der Wächter bei 16 A drosselte und
  der ohne PV inzwischen mit 5 A lädt, senkte jede weitere Drosselung nur diese Grenze, ohne
  Wirkung. Jetzt setzt sie beim kleineren von Grenze und Leistung an: Der Tesla an seinem Boden
  wird übersprungen, und die Senkung geht an den e-Golf. Eine Folge zeigt die Boost-Abwandlung von
  `spitze-15min` (Ziel 5 kW): Dort liegt der Netzbezug 3 s lang über dem Override, bis der e-Golf
  seiner ersten Grenze folgt, und 30 s später verlangt das Mittel daraus noch 93 W. Diese Senkung
  fiel vorher in den Abstand zwischen der Grenze (4711 W) und dem Strom in ganzen Ampere (10 A,
  4600 W); jetzt geht der e-Golf von 10 auf 9 A, und die Viertelstunde liegt bei 1,20 statt
  1,24 kWh. Falls falsch: Eine kleine Senkung aus dem alten Mittel kostet an der Anlage ein Ampere
  bis zum Aufheben; ein Totband in der Drosselung (Punkt 4 unter „Offen an der Anlage“) finge sie.
- **`spitze-15min` mit 16 A:** Das Szenario startet den Tesla wieder wie im Plan und besteht alle
  Prüfungen; die erste Senkung der Viertelstunde ab 18:00 trifft dort den Tesla, den e-Golf erst
  die ab 18:15. Was die Prüfung der Reihenfolge damit belegt, steht oben unter „Szenarien“.
- **Fix-Runde nach dem Review der Nachrunde:** Ohne Budget zählte `Basis + W` zunächst immer ganz,
  auch wenn `P_zul` über `C` lag. Regel 2 hält den Override aber auch ohne Budget bei `C`, und der
  Netzbezug blieb dann ohnehin unter `P_zul`: Bei einem Ziel von 10 kW (`P_zul` 9,7 kW) und einem
  SOC an der Schwelle drosselte der Wächter zwei Autos in Sofort mit 18,4 kW um 8,7 kW (e-Golf auf
  2760 W, Tesla auf 6940 W), obwohl der Netzbezug bei 8 kW blieb; ein SOC, der um die Schwelle
  pendelte, löste dieselbe Drosselung aus. Jetzt zählt `Basis + W` ohne Budget nur ganz, solange
  `P_zul` unter `C` liegt, sonst bis `C`; was die Autos darüber wollen, liefert der Akku wie ohne
  Ziel, und ein Akku an seiner Reserve zeigt sich im Mittel. Die Szenarien ändern sich dadurch
  nicht (in `spitze-15min` hat der Akku immer ein Budget). Außerdem beschreibt das Handbuch die
  Schwelle des Aufhebens jetzt richtig: Wollen die Autos mit Budget mehr als `C`, hält der Override
  den Netzbezug bei `C`, sobald `P_zul` darüber liegt, und das Mittel zählt mit; eine Grenze fällt
  erst, wenn `P_zul` die Grenze `C` um 1 kW plus ihre Senkung übersteigt (im Simulator bei rund
  9,3 kW), nicht schon 1 kW darüber. Falls falsch: Ohne Budget verlangt der Wächter knapp unter
  `C` die ganze Senkung, knapp darüber keine; den Sprung macht die Grenze von Regel 2.

Notizen für später aus Plan 2c-2a:

- **Unstimmigkeit aus 2c-1:** Der Überschuss zählt die Ladeleistung des Akkus als verfügbar für die
  Autos (`battery_until_soc`, über 30 % SOC), der Phasenwächter zählt dieselbe Ladeleistung als
  feste Last der Phase. Bei einer schweren Einphasenlast startet ein PV-Auto deshalb nicht, solange
  der Akku lädt, obwohl es mit rund 9 A im Importziel liefe; es startet, sobald der Akku voll ist.
  Das Szenario `schieflast-pv` weicht darum aus (Last erst nach 10 min). Eine Entscheidung über
  Design 2c-1, 5.1 gehört dem Betreiber.
- **Wächter und Abrechnung teilen `HOLD_MAX_S`:** `E_bisher` kommt aus der Abrechnung (Design 2c-2,
  6.1), also fehlen darin Intervalle über 5 s und Zyklen ohne Bilanz, solange der Wächter sie nicht
  als Lücke erkennt (über 10 s). Das steht im Betriebshandbuch („Akku und Spitzen“, „Messung“).

Aufgeschobene Kleinigkeiten aus Plan 2c-2a (die inhaltlichen; sie sind klein, keine blockiert
etwas):

- **Doppelte Definitionen:** Die Regel „`refresh` zählt nur beim Netz-Sollwert“ steht dreimal
  (`core/safety/layer.py`, `core/control/executor.py`, `tools/sim/runner.py`); ein gemeinsames
  Prädikat neben `Command` verhinderte Abweichungen. Den Alias `_Key` für (Aktor, Art) gibt es in
  `executor.py`, `foreign.py`, `safe_state.py`, `runtime.py` und `tools/part2/session.py`; er ließe
  sich zusammenführen, wenn `Key` in `shared/control` läge. Den Schlüssel (`victron`,
  `grid_setpoint`) gibt es in `battery.py` und `regulation.py` doppelt, die Menge der
  Unterstützungs-Modi in `battery.py` und `charging.py`. Die Platzierung von `ControlCommand`,
  `LastValues` und `ok_number` ist plangemäß, aber schwach. `MovingAverage` liegt in
  `tesla_policy.py` und trägt einen Docstring „Zeit der Safety-Schicht“, der Wächter nutzt es auf
  der Wanduhr.
- **Grenzfälle im Verhalten:**
  - `health().peak` zeigt nach dem Ende einer überschrittenen Viertelstunde weiter `exceeded`,
    solange die Regelung ruht (nur Anzeige; der Alarm endet).
  - Ein eigener Override, der im Totband über einem inzwischen gefallenen Budget liegt, wird beim
    Auffrischen von Regel 2 auf einen Wert ohne 10-W-Raster geklemmt (nur wenn `P_zul` fällt, etwa
    in einer überschrittenen Viertelstunde).
  - `lowered` ist auch wahr, wenn das Abrunden auf 10 W `W'` um weniger als 5 W kürzt und das Ziel
    gleich bleibt; dann steht `peak_guard` an einem Wert, den der Wächter nicht geändert hat
    (schmales Band).
  - Im Zyklus eines Moduswechsels wendet die Laderegelung die Grenze des alten Modus noch einmal an
    (begrenzt auf 1 s).
  - Die Grenzen in W gegen ganze Ampere und die Nennspannung: Eine kleine Senkung (Rauschen) startet
    `SETTLE_S` neu, ohne den Strom zu ändern, und das „ausstehend“ eines Autos am Boden wird über
    230 V nie 0. Die Marge fängt es; ein Totband wäre eine Entscheidung des Betreibers (siehe „Offen
    an der Anlage“).
  - Ein dringender Befehl mit `refresh` bekommt `urgent.ignored` mit dem Text „nicht Richtung
    sicherer Zustand“, obwohl nur `refresh` die Dringlichkeit verhindert.
  - `projected_kw` und `reduction_kw` in Health können bei physikalisch unsinnigen Eingaben
    unendlich werden (nur das Budget ist garantiert endlich); `MovingAverage.value` wirft bei einer
    überlaufenden Summe einen `OverflowError`, und die Regelung setzte dann mit `control.exception`
    aus. Beides ist physikalisch unerreichbar.
- **Simulator:** Das Modell hebt den Override bei einer Auslösung des Watchdogs nicht auf (der echte
  Watchdog tut es mit `victron.live_allowed`), und kein Szenario verbindet heute den Ausfall
  `cerbo`, den Watchdog und Victron live. `sim_config` prüft `control` nur feldweise (die Prüfung
  von `import_margin_a` gegen das Importlimit läuft nicht erneut). Die Verstoß-Sätze einiger
  Szenario-Prüfungen runden ihren Wert (ein Verstoß um 0,001 läse sich wie die Grenze); je ein
  Aufruf von `shown_number` behöbe das.
- **Tests:** Es fehlen Tests dafür, dass der Modus des Gastes aus `intent.guest.mode` kommt (über
  `Regulation.step`) und dass `start_cycle` den Merker `_regulated` zurücksetzt; die Tests für
  `_lapses_at` und für den Stream in einem gescheiterten Zyklus kamen mit der Fix-Welle nach dem
  Gesamtreview. Die Hilfe für die 8-kW-Konfiguration steht dreimal in Tests,
  `test_runtime_regulation.py` holt Hilfen aus `test_runtime_charging.py` statt aus einem
  gemeinsamen Modul, und `test_control.py` pinnt die letzten beiden Felder von `Command` sowie
  `test_config.py` den genauen Leerraum der Beispielzeile `peak_target_kw`.

Entschieden in Phase 2c-2b; die Rulings stehen im Plan (Präzisierungen), im Kopf des Designs 2c-2 und
im Absatz „Stand Plan 2c-2b“ der Spec (7.8). Hier die Entscheidungen mit Kosten, falls sie falsch sind:

- **Ausschluss (`elsewhere`):** Er stützt ein Fahrzeug nur, wenn Tessie es „zu Hause und angesteckt“
  meldet, nicht schon bei „angesteckt“ (der Plan sagt „angesteckt“). Ein Auto, das woanders angesteckt
  ist (an einer Ladesäule), steht nie an unserer Wallbox; wörtlich ergäbe der Tesla an einer Ladesäule
  plus ein dreiphasiger Gast bei uns einen falschen Widerspruch (Tessie schließt aus, Ausschluss und
  Phasen stützen). Falls falsch: Bei unbekanntem Ort fehlt dieses Indiz; Tessie liefert den Ort
  praktisch immer.
- **Stromvergleich und zeitliche Korrelation** geben kein Indiz für ein Auto, das Tessie als „nicht zu
  Hause“ meldet (ein unbekannter Ort zählt nicht als „woanders“). Sonst ergäbe ein Tesla an einer
  öffentlichen AC-Säule mit 16 A auf drei Phasen neben einem dreiphasigen Gast bei uns mit 16 A auf
  drei Phasen einen falschen Widerspruch. Falls falsch: nichts Messbares; ein Auto unterwegs steht nie
  an unserer Wallbox, und Tessie schließt es ohnehin aus.
- **„Erkannt“:** Die 60 s zählen die Kandidatur an der Wallbox; „an keiner anderen Wallbox Kandidat“
  gilt je Zyklus. Nach einem kurzen Konflikt ist ein Fahrzeug sofort erkannt, wenn seine 60 s an der
  verbliebenen Wallbox schon liefen. Falls falsch: eine um bis zu 60 s frühere Erkennung nach einem
  Konflikt; ein Konflikt über 60 s macht ohnehin „unklar“, das 60 s nachwirkt.
- **Zwei erkannte Fahrzeuge an einer Wallbox:** Keins gilt als erkannt, und das ist kein Widerspruch
  (der Plan: „gilt keins“). Falls falsch: Die Wallbox fiele auf „unklar“ statt auf „angenommen“.
- **Boost-Bindung:** Ein Boost ohne Übersteuerung bindet an den Ansteckvorgang der Wallbox, an der das
  Fahrzeug im vorigen Zyklus stand (erkannt, angenommen oder an seiner Standard-Wallbox mit laufendem
  Vorgang), solange dieser Vorgang noch läuft; sonst wie bisher an die Standard-Wallbox (laufender
  oder nächster Vorgang). Sonst wirkte ein Boost für den an der EVCS erkannten Tesla dort nicht und
  spränge beim nächsten Vorgang am Wall Connector an. Falls falsch: Der Boost wirkt an der Wallbox, an
  der die Zuordnung das Auto sieht; liegt sie falsch, wirkt er am falschen Auto bis zum Abstecken.
  Die bekannte Grenze steht unten.
- **Grund `assignment_detected`:** nur bis zum ersten Zyklus mit Befehlen für das Auto (Präzisierung 8
  wörtlich). Ein zurückgestellter erster Befehl kommt ohne Grund wieder, und ist der Tesla bei der
  Erkennung nicht steuerbar, trägt sein späteres `max_a` den Grund nicht. Falls falsch: ein fehlender
  Grund an diesen späteren Befehlen im Log.
- **Szenario `tesla-evcs`:** Es prüft „erkannt“ vor 3 min statt binnen 2 min. Die Regelung beginnt im
  Simulator erst nach rund 60 s; Design 10.3 „binnen 2 min“ zählt ab Beginn der Regelung. Falls
  falsch: eine um 60 s lockerere Prüfung.
- **Prüfung `_all_charge_from`** („Ab 60 min laden beide Autos“, in `ausfaelle`) übergeht den Gast:
  „beide Autos“ sind die zwei Fahrzeuge der Konfiguration. Falls falsch: eine Prüfung, die einen Gast
  nie verlangt.
- **Health je Wallbox:** `occupant` ist `null` ohne Vorgang und auch während eines Vorgangs, solange
  die Zuordnung unklar ist und an der Wallbox kein Fahrzeug der Konfiguration steht (`assignment` ist
  dann `unclear`). Wer `wallboxes` liest, muss `null` also auch bei einem Vorgang abfangen (Betriebshandbuch,
  „Zuordnung Auto und Wallbox“).
- **Prüfungen `_clear_from` und `_occupied_from`** des Simulators melden zusätzlich „Der Lauf endet vor
  {from_s} s.“, wenn nach `from_s` keine Gesundheit mehr kommt (wie andere Prüfungen des Moduls; der
  Satz fehlt in „Texte“). Falls falsch: ein zusätzlicher Befundsatz.
- **Elimination (Tessie und Home Assistant bei mehreren Vorgängen):** Sie stützen ein Fahrzeug an
  einer Wallbox mit Vorgang nur, wenn Stromvergleich oder Phasen-Signatur es an jeder anderen Wallbox
  mit Vorgang ausschließen; bei genau einem Vorgang gilt das immer. Ausschließen tun sie wie bisher an
  jeder Wallbox mit Vorgang (Abweichung von Design 7.2 und von Präzisierung 2; entschieden nach dem
  Review der Doku). „Zu Hause (und angesteckt)“ sagt nicht, an welcher Wallbox das Fahrzeug steht:
  Zusammen mit der Phasen-Signatur erkannten Tessie und Home Assistant sonst einen dreiphasigen Gast
  an der EVCS als den Tesla, der nicht ladend am Wall Connector steckt (der Wall Connector zeigte
  „Gast, angenommen“), und einen zweiphasigen Gast am Wall Connector als den e-Golf, der „geladen“ an
  der EVCS steckt. Im Kernfall, e-Golf zweiphasig am Wall Connector und Tesla dreiphasig an der
  EVCS, sind beide erkannt. Für die Elimination zählen nur Strom und Phasen; eine Übersteuerung eines
  anderen Fahrzeugs an die andere Wallbox zählt nicht (nach dem Gesamtreview entschieden; ein
  übersteuertes Fahrzeug ist an jeder anderen Wallbox ohnehin ausgeschlossen). Falls falsch:
  Kommt ein zweiter Vorgang hinzu, fällt eine Erkennung, die auf Tessie oder Home Assistant beruht,
  auf „angenommen“ zurück, bis die andere Wallbox das Fahrzeug ausschließt (60 s Laden dort) und es
  60 s Kandidat war, und so lange, wie dort nichts lädt. Die Abweichung beim Widerspruch
  (Präzisierung 4) greift seitdem nur noch, wenn die zeitliche Korrelation an der anderen Wallbox
  stützt.
- **Tessie nach dem Ende eines Vorgangs** (nach dem Gesamtreview): Endet an einer Wallbox ein
  Vorgang (sie meldet kein Fahrzeug mehr, oder an ihr beginnt ein neuer), stützen Tessie und
  Ausschluss ein Fahrzeug an einer anderen Wallbox erst wieder mit einem Wert, dessen Abfrage
  (`plugged_at`) mindestens 60 s nach diesem Ende lag; ohne `plugged_at` nicht mehr. Ausschlüsse
  durch Tessie gelten sofort; Stromvergleich, zeitliche Korrelation und Home Assistant bleiben.
  Sonst galt der Tesla, der den Wall Connector verlässt, mit Tessies veraltetem „angesteckt“ an der
  EVCS als erkannt, an der der e-Golf ohne zu laden steckt: Er bekam `max_a`, und die EVCS lud kurz
  im Modus des Tesla. Falls falsch: Eine Erkennung, die auf Tessie und Ausschluss beruht, kommt nach
  dem Ende eines Vorgangs an der anderen Wallbox erst 60 s nach der ersten Abfrage, die mindestens
  60 s nach dem Ende liegt; bis dahin bleibt das Fahrzeug angenommen. Das unterbricht auch eine
  laufende Erkennung: Ist der Tesla nicht ladend an der EVCS über Tessie und zeitliche Korrelation
  erkannt und verlässt ein zweiphasiger Gast den Wall Connector, zeigt die EVCS rund 2 min lang
  „angenommen“ (e-Golf oder Gast), dann ist der Tesla neu erkannt, mit einem neuen
  `assignment_detected`.
- **Stromvergleich** (nach dem Gesamtreview): Meldet Tessie eine andere Phasenzahl als
  `vehicles.<name>.phases`, sagt er nichts (unplausibel, also unbekannt). „Passt nicht“ zählt an
  einer Wallbox erst mit einem Wert, dessen Abfrage mindestens 60 s nach der letzten Änderung der
  Ströme dieser Wallbox lag; erst dann beginnen die 60 s des Ausschlusses. Änderungen: eine Phase
  um mehr als 1 A, eine Phase beginnt oder endet zu laden, Laden beginnt oder endet, die Ströme
  werden bekannt oder unbekannt, der erste beobachtete Zyklus der Wallbox. „Passt“ bleibt. Sonst
  schloss der Stromvergleich den Tesla an seinem eigenen Wall Connector aus, wenn Tessie eine
  Stromänderung erst nach 60 s meldete oder eine falsche Phasenzahl: dort ein Gast, nach 60 s
  „unklar“, die Tesla-Befehle pausierten. Falls falsch: Ein Fahrzeug, das nur der Stromvergleich
  ausschließt, ist frühestens 60 s nach der ersten Abfrage ausgeschlossen, die 60 s nach der letzten
  Stromänderung liegt, und nie, solange sich der Strom öfter ändert; bei falscher Phasenzahl fehlt
  der Stromvergleich ganz.
- **Beobachtete Zeit** (nach dem Gesamtreview): Liegen zwischen zwei Zyklen der Erkennung mehr als
  60 s (die Regelung ruhte), beginnen alle 60 s von Indizien und Entscheidung neu; der erste Zyklus
  danach zählt wie der erste überhaupt, für den Stromvergleich auch als Änderung der Ströme. Die
  Zeiten von „unklar“ bleiben. Falls falsch: Nach einer Pause dauert jede Erkennung wieder
  mindestens 60 s, ein Ausschluss durch den Strom 2 min.

Offene Punkte aus Plan 2c-2b (nicht umgesetzt):

- **`tools/verify_part1.py`** (Prüfprotokoll 17.1-11) prüft nur `homeassistant.entities`, nicht
  `home_entity`, denn der core ergänzt diese Zuordnung erst zur Laufzeit (`ha_entities`). Ob der core
  den `device_tracker` liest, zeigt bis dahin Health: Läuft nur an einer Wallbox ein Vorgang und ist
  das Fahrzeug zu Hause, steht dort das Indiz `home` unter `indicators`. Bei zwei Vorgängen steht es
  dort erst nach der Elimination, wenn Strom oder Phasen das Fahrzeug an der anderen Wallbox
  ausschließen; „nicht zu Hause“ steht an jeder Wallbox mit Vorgang sofort.
- **`home_entity`** ersetzt einen gleichnamigen Eintrag in `homeassistant.entities` (das Signal
  `vehicle.<name>.home_state`) still; kein Test pinnt den Vorrang.
- **Übersteuerung und Elimination:** Eine Übersteuerung eines anderen Fahrzeugs an die andere
  Wallbox zählt für die Elimination nicht (entschieden nach dem Gesamtreview); ein übersteuertes
  Fahrzeug ist an jeder anderen Wallbox ohnehin ausgeschlossen. Ist etwa der e-Golf an den Wall
  Connector übersteuert und steht der Tesla an der EVCS, ohne zu laden, bleibt dort „Gast,
  angenommen“ mit dem Modus des Gastes, bis auch der Tesla übersteuert ist.
- **Phasenzahl der EVCS:** Der core leitet sie aus Leistung und Strom ab (`derived.py`), die nicht
  gleichzeitig gemessen werden (Leistung über MQTT vom GX, Strom über Modbus). Ein einziger
  abweichender Zyklus startet die 60 s der Phasen-Signatur neu; ein Gast an der EVCS, den bei
  anwesendem e-Golf nur die Phasen vom e-Golf trennen, kann so kurz zum e-Golf wechseln und zurück.
  Geprüft wird das im Dry-Run (oben, „Vor der Live-Schaltung von EVCS und Tesla“); Abhilfen wären
  die Leistung je Phase des GX (`wallbox.<id>.l<n>.power_w`) oder eine Toleranz von ein, zwei
  Zyklen.
- **Tessies Zeitstempel:** `plugged_at` ist die Zeit der Abfrage von BindaEMS (Präzisierung 9),
  nicht Tessies eigener Zeitstempel (`charge_state.timestamp`). Tessie antwortet aus seinem Cache;
  der eigene Zeitstempel würde die Regeln für Beginn, Ende und Stromänderung genauer machen. Das
  verlangt eine Entscheidung.
- **Simulator:** Sein Tessie meldet bei jeder Abfrage die Werte des Modells, und die EVCS meldet
  Leistung und Strom aus demselben Schritt. Es fehlen Regler für die Verzögerung von Tessie und der
  Leistung der EVCS; die Fälle des Gesamtreviews (veraltetes „angesteckt“, nachhinkender Strom,
  abweichende Phasenzahl der EVCS) pinnen so nur Unit-Tests.
- **Boost für ein Fahrzeug, dessen Standard-Wallbox ein anderes erkanntes Fahrzeug belegt,** geht
  verloren: Ein Boost für den e-Golf, während der erkannte Tesla die EVCS belegt, bindet an den Vorgang
  des Tesla und endet mit ihm (wie in 2c-1 bei Übersteuerung). Nach dem Abstecken des Tesla braucht es
  eine neue Version der Absicht.
- **Spätes Ankommen:** Meldet `home_entity` das Ankommen des e-Golf spät, gilt er bis dahin als Gast
  (angenommen) und lädt im Gast-Modus (`guest.mode`, Standard PV-Überschuss), nicht im Modus des e-Golf.
  Das Betriebshandbuch nennt es; ob der Tracker an der Anlage schnell genug ist, zeigt die
  Inbetriebnahme Punkt 3. Dasselbe gilt, solange ein Tracker zu Hause kurz `not_home` meldet.
- **Gast-Erkennung:** Erkannt ist ein Gast erst, wenn an seiner Wallbox jedes Fahrzeug der
  Konfiguration ausgeschlossen ist; bis dahin steht dort „Gast, angenommen“ oder das
  Standardfahrzeug. Ein Fahrzeug, das zu Hause angesteckt ist, schließen an der Wallbox des Gastes
  nur Strom oder Phasen aus, nicht Tessie oder Home Assistant. Ein dreiphasiger Gast an der EVCS neben
  dem Tesla, der nicht ladend am Wall Connector steckt, bleibt so „Gast, angenommen“, der Tesla
  angenommen am Wall Connector (vor der Elimination: der Tesla erkannt an der EVCS, am Wall Connector
  ein Gast); lädt der Tesla mit anderem Strom, ist der Gast nach rund 2 min erkannt, solange sich der
  Strom an der EVCS nicht ändert (ändert die Regelung ihn oft, etwa alle 45 s, bleibt dort „Gast,
  angenommen“). Ein Gast mit zwei
  Phasen sieht für den core aus wie der e-Golf, solange dieser laut Home Assistant zu Hause ist, denn
  der e-Golf meldet kein „angesteckt“: Allein an einer Wallbox ist er als e-Golf erkannt, auch wenn der
  e-Golf nicht angesteckt ist, und neben einem zweiten Auto ebenso, sobald dieses auf einer anderen
  Phasenzahl lädt und den e-Golf so an seiner Wallbox ausschließt; sonst bleiben die Wallboxen
  angenommen, etwa bei einem Gast am Wall Connector neben dem e-Golf an der EVCS: dort der e-Golf
  angenommen, am Wall Connector ein Gast, angenommen. Den dreiphasigen Gast an der EVCS neben dem
  Tesla, den zweiphasigen Gast am Wall Connector neben dem e-Golf, den Kernfall und den Rückfall einer
  Erkennung, wenn ein zweiter Vorgang beginnt, pinnen Tests in
  `tests/unit/core/control/test_assignment.py`.

Aufgeschobene Kleinigkeiten aus Plan 2c-2b (die inhaltlichen; sie sind klein, keine blockiert etwas):

- **Tests der Erkennung:** Nicht gepinnt ist die Phasen-Signatur mit einer Phase (n = 1);
  der Uhrentest erkennt nur gemischte Nutzung der beiden Uhren, nicht einen konsequenten Tausch.
  Es fehlen Controller-Tests dafür, dass ein neu erkannter Gast an seinen ersten EVCS-Befehlen
  `assignment_detected` trägt und dass der Merker eines neu erkannten Autos ein `idle` übersteht.
  `test_wallbox_health_is_empty_before_the_first_step_and_after_idle` prüft nicht, dass der Stand vor
  `idle` nicht leer war.
- **Docstrings und Doppelungen:** Der Docstring von `assignment.py` nennt die Boost-Regel ungenau
  („an dem das Fahrzeug steht“ statt „im vorigen Aufruf stand, solange dieser Vorgang läuft“).
  `_NOBODY` in `charging.py` wiederholt das Literal „ohne Vorgang“ aus `assignment.py`; ein vom
  Detektor anderswo erkanntes, übersteuertes Fahrzeug überschriebe seinen Platz in
  `_place_at_sessions` (heute unmöglich, der Detektor schließt es aus).
- **Simulator:** `model.py` nimmt für den Gast `next(iter(cfg.wallboxes))` (ohne Wallbox ein Fehler),
  überschriebe ein Fahrzeug mit dem Schlüssel `gast`, und ein unbekannter Schlüssel in `away` bleibt
  ohne Wirkung. `runner.py` wiederholt für den Gast die Feldzuordnung der Fahrzeugschleife, und
  `_clear_from` und `_occupied_from` in `scenarios.py` haben denselben Prolog („ab `from_s`
  filtern, sonst endet der Lauf“) doppelt. `zuordnung` pinnt „der Tesla ist nie kurz erkannt“ nur über
  den Test des Aufbaus.

Doku-Korrekturen aus dem Re-Review der Fix-Welle von 2c-2b (erledigt am 15.10.2026 auf
`claude/project-thread-57rkit`; der Code stimmte jeweils, nur die Texte waren ungenau; die Zeilen
gelten für `31721a6`):

- **Kosten der Sperre nach dem Ende eines Vorgangs:** Die Texte sagen, eine Erkennung über Tessie und
  Ausschluss komme nach dem Ende eines Vorgangs an der anderen Wallbox später. Die Sperre unterbricht
  aber auch eine laufende Erkennung: Ist der Tesla nicht ladend an der EVCS über Tessie und zeitliche
  Korrelation erkannt und verlässt ein zweiphasiger Gast den Wall Connector, zeigt die EVCS rund
  2 min lang „angenommen“ (e-Golf oder Gast), dann ist der Tesla neu erkannt, mit einem neuen
  `assignment_detected`. Ergänzen in `src/bindaems/core/control/clues.py` (Docstring, um Zeile
  46-50), `docs/betrieb.md` (um 1655-1657), Spec 7.8 (`2026-10-08-bindaems-design.md`, um 905), im
  Kopf des Designs 2c-2 (um 88-91) und hier („Tessie nach dem Ende eines Vorgangs“ unter den
  Entscheidungen nach dem Gesamtreview).
- **Gast nach rund 2 min erkannt:** Der Satz „lädt der Tesla mit einem anderen Strom, ist der Gast
  nach rund 2 min erkannt“ (`docs/betrieb.md` um 1789-1790 und hier unter „Gast-Erkennung“) gilt
  nur, solange sich der Strom an der EVCS nicht ändert. Ändert die Regelung ihn oft (etwa alle
  45 s), bleibt dort „Gast, angenommen“; der Absatz „Stromvergleich nach einer Änderung“ in
  `docs/betrieb.md` (um 1672-1673) sagt es richtig. Den Satz einschränken.
- **Wortlaut der Regel zu Übersteuerung und Elimination:** `docs/betrieb.md` (um 1626-1627),
  Spec 7.8 (um 908) und hier (Elimination und „Übersteuerung und Elimination“ unter den offenen
  Punkten) nennen die Übersteuerung des Fahrzeugs selbst an die andere Wallbox. Gemeint ist die
  Übersteuerung eines anderen Fahrzeugs an der anderen Wallbox; ein übersteuertes Fahrzeug ist an
  jeder anderen Wallbox ohnehin ausgeschlossen. `detection.py` (um 25-26) und `clues.py` (um 26-27)
  sagen es richtig. Im Beispiel gilt „stützen ihn dort weder `tessie` noch `elsewhere`“ nur, solange
  der übersteuerte e-Golf am Wall Connector nicht zweiphasig lädt; lädt er, stützt `tessie` den
  Tesla an der EVCS, allein reicht das nicht, und es bleibt „Gast, angenommen“.

Entschieden in Plan 2d; die Rulings stehen im Plan (Präzisierungen) und in den Absätzen „Stand Plan 2d“
der Spec:

- **Bereinigung der Absicht:** Die app schickt Boost und Übersteuerung nur zurück, wo Health sie zeigt
  (oder wo sie auf einen Ansteckvorgang warten), und vertraut Health nur, wenn seine `intent_version` die
  gültige Version erreicht; sonst gilt der Stand als unbekannt (Boost in der Zukunft und Übersteuerung
  bleiben, ein abgelaufener Boost entfällt). Die Regel gilt auch für das Fahrzeug, das die Bedienung
  gerade ändert, vor der Änderung; die Präzisierung nahm es zuerst aus. Dafür hat Health die Felder
  `intent_version`, `boost_pending` und `override_pending` bekommen (falls falsch: ein alter Boost
  könnte nach einer Bedienung beim nächsten Anstecken anspringen).
- **Version der Absicht:** Millisekunden seit 1970 oder die gültige Version + 1, was größer ist. Nach
  der ersten Absicht der app braucht eine Absicht per `curl` eine Version aus `GET /v1/intent` plus 1.
- **Reserve:** Sie hält nur Strombefehle zurück, die den Strom nicht senken oder den Ist-Strom nicht
  über die Grenze des Wächters; Starts, Stopps, der Höchststrom an der EVCS und dringende Befehle gehen
  immer. Die Konfiguration lehnt `control.tesla_reserve_commands` ab, der nicht unter `per_hour`
  jedes Fahrzeugs mit Tessie liegt.
- **Selbststart:** Die Regelung vergisst nur ihren letzten Wert für „Laden an/aus“, nicht den Strom, und
  zählt nur Korrekturen, die sie senden kann. Im Modus „Aus“ ist der Befehl dringend und umgeht das
  Start/Stopp-Limit.
- **Szenario `tesla-aus`:** Es belegt „Aus wirkt binnen 10 s“, aber nicht, dass der dringende Stopp das
  Stundenlimit umgeht: Die Reserve hält das Budget unter den Wolken offen, es gibt keine Zurückstellung
  (Betriebshandbuch, „Simulator“). Das Umgehen prüfen die Tests der Regelung.
- **Quittierung:** Der core antwortet aus dem Stand des letzten Zyklus und wendet die Quittierung im
  nächsten an. Quittierbar sind Sperren, deren Ursache fehlt, und solche, deren einzige Ursache der
  Alarm einer fremden Änderung ist; sonst 409 mit der Ursache. Schreibt der fremde Regler weiter,
  sperrt die Erkennung nach 3 Prüfungen erneut.
- **Live-Schalter:** nur im UI, nur für Admins; EVCS und Tesla (jeder Aktor außer Victron) verlangen
  zusätzlich die Bestätigung, dass evcc ganz abgeschaltet ist. Die Bestätigungen entfallen, wenn sich die
  Auswahl ändert; „Auf Dry-Run“ wählt alle live angeforderten Aktoren vor und braucht kein Passwort.
  Ein Merkmal für „Prüfprotokoll Teil 2 bestanden“ gibt es nicht, darum das Häkchen. Der Dialog lässt nur
  Aktoren mit `live_allowed` wählen.
- **Entscheidungslog in der app:** Der Rückstand beginnt 10 min vor dem jüngsten `ts` (Rücksprünge der
  Wanduhr), die `id` des core ist eindeutig, die Textsuche achtet nicht auf Groß- und Kleinschreibung
  (auch bei Umlauten). Die Quelle des Rückstands sind die Dateien des core (7 Tage): War die app länger
  weg, fehlen Ältere. Strombefehle, die die Reserve zurückhält, stehen nicht im Log.
- **Home Assistant:** Gehaltene Nachrichten auf `bindaems/cmd/…` ignoriert die app. Höchstens ein Befehl
  je Sekunde und Entität; nach jedem Befehl wird der Zustand neu veröffentlicht. HA hat die Rechte von
  „Bedienen“ ohne Benutzer, aber weder Live-Schalter noch Vorrang. Die Konfiguration lehnt Fahrzeugnamen
  ab, die leer, gepolstert, ohne Beachtung der Schreibweise doppelt oder „Automatisch“ sind, und
  Schlüssel, deren Objekt-ID in Home Assistant mit einer anderen Entität zusammenfiele. Der Sensor
  „Wallbox“ zeigt den Anzeigenamen der Wallbox (eigener Name, sonst „EVCS“ oder „Wall Connector“).
- **Benachrichtigungen:** Ein Fehler-Alarm wird nach 30 s einmal gemeldet (persistente Meldung
  `bindaems_<Alarm-ID>` und, falls eingestellt, der `notify`-Dienst) und beim Verschwinden entfernt;
  höchstens 10 Meldungen je Minute, davon höchstens 4 für Ablehnungen, der Rest geht ins Log. Ein
  Alarm gilt erst als gemeldet, wenn die persistente Meldung angekommen ist; sonst versucht die app es
  jede Minute erneut, solange er besteht. Ein gescheitertes Entfernen wiederholt sie ebenso. Ohne
  `BINDAEMS_HA_TOKEN` meldet die Komponente `ha-notify` „Benachrichtigungen aus: BINDAEMS_HA_TOKEN
  fehlt.“.
- **Demo-Backend:** Der core der Demo-Welt meldet DEGRADED. Der Benutzer `bedienen` (Rolle „Bedienen“) ist
  neu.
- **Nicht in 2d:** `assignment_log` und `charging_session` (Spec 11.3) folgen mit der Statistik; manuelle
  Zuordnungen stehen im Änderungsprotokoll, erkannte in den Entscheidungen und in Health.

Aufgeschobene Kleinigkeiten aus Plan 2d (aus den Reviews; keine davon gefährdet die Anlage):

- **Quittierung aus veraltetem Stand:** Die Quittierung gilt der Erkennung, die beim Anwenden steht.
  War eine neue fremde Änderung schon vor dem Klick erkannt, aber noch nicht auf der Seite des
  Betreibers, wird sie mitquittiert (Handbuch, Abschnitt 13). Abhilfe wäre, die gesehene Erkennung mit
  der Quittierung zu senden.
- **Boost nach Neustart des core:** Der core merkt sich verbrauchte Boosts nur im Speicher. Nach einem
  Neustart, oder bei Ab- und Anstecken während die Regelung ruht, kann ein alter Boost aus der Absicht
  an den laufenden Vorgang binden.
- **`PUT /v1/intent` am core:** Der Notfallweg kennt kein Passwort (Token genügt) und übernimmt jede
  neuere Version ohne Abgleich der Basis; schreiben app und ein direkter Aufruf im selben Moment, gilt
  der spätere.
- **Benachrichtigungen:** Kehrt ein Alarm zurück, während das Entfernen seiner alten Meldung noch
  aussteht, und verschwindet er vor einer neuen Meldung wieder, bleibt die alte Meldung in Home
  Assistant stehen. Ablehnungen und Alarme teilen sich die Verbindung zu Home Assistant der Reihe nach.
- **HA-Befehle:** Die Begrenzung auf einen Befehl je Sekunde misst beim Verarbeiten, nicht beim
  Eintreffen; antwortet der core langsam, werden gestaute Befehle ausgeführt statt verworfen. Ein
  Abruf der Absicht neben einer Bedienung kann den Zustand in Home Assistant für bis zu 5 s
  zurückspringen lassen.
- **UI:** Die Bestätigungen im Live-Dialog bleiben stehen, wenn eine Aktualisierung die Auswahl über
  `live_allowed` verkleinert (sichere Richtung). Die URL-Filter der Seite „Entscheidungen“ können bei
  sehr schnellem Wechsel kurz von der Anzeige abweichen. Ein ungültiger SOC-Wert wird still
  zurückgesetzt.
- **Entscheidungslog:** Ein `before`-Zeiger auf eine aufgeräumte Zeile liefert eine leere Seite; fällt
  die Datenbank aus, wächst der Puffer der app bis zur Rückkehr.
- **Tesla:** `poll_soon` verkürzt die Wartezeit nach einem Fehler (höchstens eine Abfrage mehr je
  Anstecken). Der Simulator beschriftet Boost-Sekunden nach der Absicht, nicht nach der Regelung.
- **Tests:** Einige Prüfungen greifen auf interne Felder zu (`_acks`), die Zeitstempel-Ersetzung der
  fremden Entscheidung ist nur über den Ablauf getestet.

Entschieden in Plan 3a; die Rulings stehen im Ledger der Umsetzung und, soweit sie das Verhalten
betreffen, im Plan (Präzisierungen) und in den Absätzen „Stand Plan 3a“ der Spec. Hier die
Entscheidungen mit Kosten, falls sie falsch sind:

- **Jede Task lässt das ganze Repository grün:** Task 1 weitet schon das UI-Schema der
  Tarifbestandteile (`v.variant('kind', …)`) und schreibt die Vertragsdateien neu; der Plan hatte das
  für Task 4 vorgesehen und Tasks 1 bis 3 rot gelassen. Falls falsch: etwas UI-Arbeit wandert zwischen
  den Tasks.
- **Texte im Tarif-Editor:** Der Plan gilt für das UI, auch wo der Server anders formuliert (Viertelstunde:
  „Uhrzeit auf einer Viertelstunde, z. B. 10:15“). Zusätzliche Prüftexte, die der Plan nicht listet
  („Name eingeben“, „Höchstens 80 Zeichen“, „Datum wählen“, „Wert größer 0“, „Wert ab N“, „Wert von 0 bis
  100“), spiegeln Prüfungen des Servers. Falls falsch: nur Wortlaute.
- **Abgelehntes Speichern:** Der Hinweis „Bitte die markierten Felder prüfen.“ steht nicht im Plan, ist
  aber nötig, damit ein gescheiterter Speichervorgang neben „Speichern“ etwas zeigt. Falls falsch: ein
  Text.
- **„Neu bewerten“:** Das Formular wird gleich nach dem Speichern geprüft, nicht erst nach dem
  Neuladen; es ist seit Phase 1c Zustand der Seite. Falls falsch: nur die Erwartung im E2E-Test.
- **HA-Historie mit Vorlauf:** Die Abfrage holt für jede Entität den letzten Wert vor dem Zeitraum
  (Seed), denn HA schreibt nur bei Änderung, und eine ruhende Wallbox (abzuziehende Entität) hat tagelang
  keinen Punkt; sonst fiele jeder Slot weg und `subtract` nützte nichts. Der Review stufte das von
  „Minor“ auf „Important“ hoch. Falls falsch: eine Seed-Abfrage mehr je Neuaufbau.
- **Zahlen im UI:** Negative Leistungen stehen mit dem ASCII-Minus aus `format.ts` („-80 W“), nicht mit
  „−80 W“ wie im Plan, denn `format.ts` ist der verbindliche Formatierer. Falls falsch: ein Formatierer
  für das ganze UI.
- **Fix-Welle nach dem Gesamtreview:** Sie nahm den wichtigen Befund (Tageserlös der Einspeisung
  ignorierte die Zeitfenster) und drei kleine Punkte (Beschriftung „Heute (ab jetzt)“, Stufe und
  Mindestverrechnung in der Tarifansicht, ElWG-Vorlage mit Name und Beginn 1.1.2027). Der dritte
  verhindert, dass ein Leistungspreis still für 2026 gilt, wenn 3b damit rechnet. Falls falsch: drei
  kleine UI-Änderungen zurücknehmen.
- **Tageserlös ohne Export:** Ein Tag ohne Einspeisung und ohne OeMAG-Wert meldet Erlös 0,0 statt
  `None`, wie `cost_eur` an Tagen ohne Bezug. Falls falsch: eine Bedingung in `_summary`.
- **`newComponent` mit `existingKinds`:** Der Parameter ist Pflicht; der erste Leistungspreis wird zur
  ElWG-Vorlage, und kein Aufrufer löst sie aus Versehen aus. Falls falsch: eine Signatur in einer
  Hilfsfunktion.

Aufgeschobene Kleinigkeiten aus Plan 3a (aus den Reviews; keine davon gefährdet die Anlage; das
Ledger nennt sie nach Task):

- **Zwei Fälle, die an der Anlage zählen** (Gesamtreview, M4 und M5):
  - Der Nachtlauf um 02:30 fällt in der Nacht der Zeitumstellung im März aus (die Stunde gibt es nicht;
    der Plan schreibt 02:30 vor); der nächste Lauf holt es nach.
  - `fill(previous)` der HA-Abfrage trägt Werte über lange Ausfälle von Home Assistant fort; der Median
    dämpft es. Dazu die Risiken unter „Offen an der Anlage“, „Zu 3a gehört“.
- **Tarif, Server:**
  - `PowerComponent.tier_kw` lässt unendlich zu (so im Plan).
  - Die Fehlerpfade des Servers tragen den Tag der Union (`tariff.components.2.energy_per_kwh.value_ct`);
    das UI schneidet ihn ab.
  - Ein Einspeise- oder Gebührenbestandteil ohne Wert zählt in der Rechnung still 0; die Warnung der
    Einstellungen („hat noch keinen Wert“) deckt alle Arten ab.
  - Es fehlen Tests für den letzten gültigen Tag (einschließlich) bei Gebühren und Leistungspreis und
    für den Wechsel Dezember zu Januar in `month_peak`; die Verdrahtung von `month_peak` in
    `runtime._build_ha_inputs` ist ungetestet, und ein Kommentar im ersten Test von `month_peak` ist
    unglücklich umbrochen.
- **Tarif-Editor im UI:**
  - Das Datum prüft nur die Form (2026-13-45 geht durch den Client; der Server lehnt es ab).
  - Der Wert eines Zeitfensters heißt nur „Wert“ (nicht nach dem Modus), und die Einheit steht
    außerhalb des Labels.
  - Die Auswahlgruppen und Schalter haben keine Gruppensemantik und kein `aria-describedby`.
  - Eine Änderung der Struktur löscht alle Fehler, auch die der OeMAG-Felder; der Hinweis „Bitte die
    markierten Felder prüfen.“ bleibt danach stehen.
  - `focusFirstError` tut still nichts, wenn zu einem Fehlerschlüssel des Clients kein Element
    gezeichnet ist.
  - Die lokale Variable `window` in `WindowEditor` verdeckt das globale Objekt.
  - Tests fehlen für: einen Spotbestandteil ohne Wert, Einspeise-Zeitfenster hin und zurück, das Ende
    24:00.
- **Tagestyp und Schätzpreise:**
  - Kein Test für den Frühlingstag mit 92 Slots und für ein naives Datum in `slot_key` (ValueError).
  - Kein Test für die Untergrenze `jetzt − 28 d` (einschließlich), für die Zeitumstellung (erster
    Durchlauf von 02:00, Planung über einen Tag mit 100 Slots) und für einen einzigen Wert desselben
    Tagestyps, der auf die Stufe über alle Tagestypen fällt.
  - `planning(start < jetzt − 28 d)` kennt gespeicherte Preise vor dem Fenster nicht; es dient nur der
    Vorausplanung (Hinweis im Docstring).
- **Archiv, Kalibrierung, Prognosegüte:**
  - Der Test der Zeitumstellung im Archiv füttert UTC-Schlüssel und übt die lokalen Falten nicht;
    zwei Schlüssel für denselben Zeitpunkt würden die Zahl in `record` überzählen.
  - Der Fixture der Strahlung ist künstlich (Docstring in `tests/unit/app/forecast/conftest.py`), und
    Tests pinnen seine Literale (12,1; 11,5); `test_clearness_comes_from_first_plane` nutzt nur eine
    Fläche; der Test des Archivfehlers prüft die Warnung nicht, und die Importe von `FIXTURE`
    sind gemischt.
  - Kalibrierung: Die Grenze des 30-Tage-Fensters ist ungetestet, Tests schreiben `service._cfg`, und
    `recalibrate` tut ohne Archiv still nichts.
  - Der Nachtlauf wartet höchstens 60 s auf die Kalibrierung, und das Budget umfasst die Güte;
    löst eine Ausnahme vor dem `try/finally` aus, bleibt das Ereignis bis zum Neustart ungesetzt. Der
    Test der Wartezeit schläft echte 50 ms. Es fehlt ein Test der Zeitumstellung für
    `ForecastQuality.update`, und die Konstanten in `runtime.py` hat der Formatter umbrochen (Kommentare
    stehen darüber).
- **Lastprognose und HA-Historie:**
  - Ein Lesefehler aus Home Assistant steht nur im Log, der Status bleibt `ok`.
  - `_ha_ok` ist ein Nebeneffekt der öffentlichen `history()`; die Reihen beider Schemata können sich
    überlappen; ein leeres Profil wird stündlich neu gebaut (gewollt, Kommentar); ein ungenutzter
    `monkeypatch`-Parameter; `LoadForecast.p50_w` ist ein veränderliches Dict.
  - Der Seed mit zwei `last()`-Selektoren kann bei einer Entität in beiden Schemata eine der Reihen
    wählen; eine HA-Entität, die länger als 7 Tage schweigt, fällt weiter weg.
- **UI der Prognosen:**
  - Die Höhe des Bandes `P75 − P25` ist nicht auf 0 begrenzt; ungetestet sind der Ausschluss von „PV P25“
    in der Legende und verschiedene x-Punkte der Reihen.
- **Doku:**
  - Im Betriebshandbuch fehlt beim Band die Schwellenangabe, „`fill(previous)` auf dem Einheitenfeld“
    ist ungenau formuliert, und die Zeile 3b der Tabelle in Spec 18 kürzt den freigegebenen Wortlaut.

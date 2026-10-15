# Übergabe an die nächste Session

Stand: 15.10.2026. Plan 2c-1 (Laderegelung) ist umgesetzt, je Task und im Gesamtreview geprüft
(„mit Korrekturen“, keine kritischen Befunde), und die Fix-Welle danach ist umgesetzt. Er ist auf
`main` (Stand `18c575a`, wie die Branches `ccr-e451cb4c-qeg1tm` und `ccr-9778345e-t3x44k` zu Beginn
der folgenden Sitzung). Danach sind die EVCS-Hysterese (Freigabe des Betreibers vom 14.10.2026) und
die kleinen Nacharbeiten aus dem Gesamtreview dazugekommen; beides ist per Fast-Forward auf `main`
(`47a09a7`, mit Zustimmung des Betreibers vom 14.10.2026). Das Design für 2c-2 hat der Betreiber am
14.10.2026 freigegeben (`docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md`).
**Plan 2c-2a (Akku und Spitzen) ist umgesetzt und je Task geprüft**
(`docs/superpowers/plans/2026-10-14-phase-2c2a-akku-spitzen.md`). Er liegt auf dem Branch
`ccr-9778345e-t3x44k`; der Gesamtreview und die Integration nach `main` stehen aus. Als Nächstes
folgt Plan 2c-2b (Zuordnungserkennung, Design 2c-2, Abschnitt 7).
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
- **Phase 2c-2a (Akku und Spitzen) ist umgesetzt und je Task geprüft** (Plan:
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
    Regelung brauchte keinen Fix. Drei Szenarien haben einen angepassten Aufbau (unten,
    „Bewusst offen gelassen“).
  - **Entschieden und aufgeschoben:** Die Rulings und die aufgeschobenen Kleinigkeiten stehen unter
    „Bewusst offen gelassen“.
  - **Noch nicht:** die volle Zuordnungserkennung (2c-2b), die Bedienung (2d) und die Planausführung
    (Phase 3). Live ist weiterhin kein Aktor.
- **Images:** `ghcr.io/binda5000/bindaems-core:latest` und `ghcr.io/binda5000/bindaems-app:latest`.
  Sie sind öffentlich, und die CI setzt `latest` bei jedem Push auf `main`. core und app werden
  immer gemeinsam aus demselben Stand ausgerollt (gleiche Compose-Datei, gleiche Image-Tags): Die
  UI-Schemas sind streng, eine abweichende Version zeigt auf der System-Seite nur „Unerwartete
  Antwort vom Server“.
- **Tests:** 5119 Python-Tests, davon 35 Property-Tests mit Hypothesis (6 für die Safety-Schicht,
  6 für den Phasenwächter, 12 für den Akku-Sollwert, 6 für den 15-min-Wächter), und 233 UI-Tests;
  Abdeckung 97 % insgesamt und 99 % für `core/safety`, `core/control` und `core/actuators`.
  Geprüft wird mit ruff, mypy strict (auch für den Watchdog), import-linter (4 Verträge), pytest
  mit Abdeckungsgrenzen (≥ 75 % insgesamt, ≥ 90 % für `core/safety`, `core/control` und
  `core/actuators`), Vitest mit svelte-check, Prettier und ESLint; dazu E2E mit Playwright.
- **Offen:**
  - der Gesamtreview von Plan 2c-2a und seine Integration nach `main`
  - Plan 2c-2b (Zuordnungserkennung) nach dem Design 2c-2, Abschnitt 7, schreiben, vom Betreiber
    durchsehen lassen und umsetzen, danach Plan 2d (siehe „Nächster Schritt“)
  - an der Anlage: Images ausrollen, Dry-Run neben evcc beginnen, der Rest von Teil 2
    (EVCS-Verbindungsabbruch, Victron 17.2-4), Victron in den Dry-Run und live (siehe „Offen an der
    Anlage“)
  - vor der Live-Schaltung von EVCS und Tesla (2d): die Liste „Vor der Live-Schaltung“ (siehe
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
    keinem lag der Fehler in der Regelung, und die drei sind im Aufbau angepasst (Betriebshandbuch,
    „Simulator“).
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
    EVCS-Hysterese: `ccr-9778345e-t3x44k`); der Standard-Branch auf GitHub ist `main`.
  - Nach `main` nur mit Zustimmung; bisher per Fast-Forward-Merge, Tests auf `main`, dann Push.
  - Pull Requests nur auf ausdrücklichen Wunsch.
  - Commit-Nachrichten auf Deutsch im Stil `fix(ui): …`, mit den Attributionszeilen, die die
    Sitzung vorgibt.
  - Keine Modellbezeichnungen in Dateien des Repositorys.
- **Secrets** nur über `BINDAEMS_*`-Umgebungsvariablen; `.env` wird nie committet.

## Wichtige Dokumente

| Dokument | Inhalt |
|---|---|
| `docs/superpowers/specs/2026-10-08-bindaems-design.md` | Spec, verbindlich. Wichtig: 7 Regelung, 8 Sicherheit, 13 Web-UI, 14 Login, 17 Prüfprotokoll, 18 Phasenplan, 19 offene Punkte. Die Absätze „Stand Plan 2a/2b/2c-1/2c-2a“ gehen dem älteren Text davor vor |
| `docs/superpowers/plans/2026-10-08-phase-1a-fundament-datenerfassung.md` | Plan 1a (core) |
| `docs/superpowers/plans/2026-10-09-phase-1b-app-backend.md` | Plan 1b (app) |
| `docs/superpowers/plans/2026-10-09-phase-1c-web-ui.md` | Plan 1c (UI), mit Anhang A zu den API-Formen |
| `docs/superpowers/plans/2026-10-10-phase-2a-sicherheitsbasis.md` | Plan 2a (Sicherheitsbasis), mit dem Zuschnitt der Phase 2 in 2a–2d und den Präzisierungen gegenüber der Spec |
| `docs/superpowers/plans/2026-10-10-phase-2a-entscheidungen.md` | Entscheidungen, geparkte Befunde und aufgeschobene Kleinigkeiten aus der Umsetzung von Plan 2a |
| `docs/superpowers/plans/2026-10-11-phase-2b-aktoren.md` | Plan 2b (Aktoren), 18 Tasks; Entscheidungen des Betreibers vom 11.10.2026, Präzisierungen, Texte |
| `docs/superpowers/plans/2026-10-11-phase-2b-entscheidungen.md` | Entscheidungen, geparkte Befunde und aufgeschobene Kleinigkeiten aus der Umsetzung von Plan 2b |
| `docs/superpowers/specs/2026-10-12-phase-2c1-laderegelung-design.md` | Design 2c-1 (Laderegelung), umgesetzt; der Kopf nennt, was sich beim Bau geändert hat |
| `docs/superpowers/plans/2026-10-12-phase-2c1-laderegelung.md` | Plan 2c-1, 16 Tasks; Präzisierungen gegenüber dem Design, Texte, Szenarien des Simulators |
| `docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md` | Design 2c-2 (Akku-Sollwert, 15-min-Wächter, Zuordnungserkennung), Entscheidungen des Betreibers vom 14.10.2026; 2c-2a umgesetzt, Abschnitt 7 (Zuordnung) folgt mit Plan 2c-2b; der Kopf nennt, was sich beim Bau geändert hat |
| `docs/superpowers/plans/2026-10-14-phase-2c2a-akku-spitzen.md` | Plan 2c-2a, 14 Tasks, umgesetzt: Umbau, Akku-Sollwert, 15-min-Wächter, Simulator; Präzisierungen gegenüber dem Design, Texte, Szenarien |
| `docs/betrieb.md` | Betriebshandbuch: Installation, Proxy (6), HA (7), Tarif (8), Sicherung (9), Prüfprotokolle einschließlich Teil 2 (11), Web-UI (12), Betriebszustände, Freigaben, Übergabe und Entscheidungslog (13), Watchdog am Cerbo (14); am Ende „Laderegelung“ (Modi, Absicht, Dry-Run-Vergleich mit evcc, Umstieg), „Akku und Spitzen“ (Akku-Sollwert, 15-min-Wächter, Health, Victron in den Dry-Run und live, Alarme) und „Simulator“ |
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
      - `charge_inputs.py`: Eingaben aus Snapshot und abgeleiteten Größen
      - `assignment.py`: Zuordnung, Ansteckvorgänge, Boost und Übersteuerung
      - `tesla_policy.py`, `charging_tesla.py`: gleitendes Mittel, Weckkontingent, Tesla-Besonderheiten
      - `charging.py`: der `ChargeController` verbindet die Bausteine je Zyklus
      - seit 2c-2a aus `charging.py` ausgelagert: `charge_points.py` (Ladepunkte und Verteilung),
        `charging_tesla.py` (Tesla-Teil), `last_values.py` (Merker der letzten eigenen Werte, die
        Lade- und Akku-Regelung teilen) und `timing.py` (`held_since`, `lasted`)
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
  - HA MQTT Discovery
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

Demo-Zugänge: `admin`, `gast` (Lesen) und `sicher` (Admin mit TOTP, Geheimnis
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
  Das gilt auch für `battery.py`, `peak_guard.py` und `regulation.py`. `core/regulation_link.py` liegt
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
  Wanduhr, wie die Viertelstunden der Abrechnung; Tests müssen beide Uhren angeben.
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
- **Simulator (2c-1, 2c-2a):** Er nutzt die echten Aktoren und die Parser der Adapter. Ändert man deren
  Zeiten, Texte oder Signale, ändern sich die Szenarien mit. Alle achtzehn Szenarien laufen in
  `tests/integration/test_sim_scenarios.py`; je etwa 3 bis 13 s, zusammen rund 2 min (gemessen auf
  der Entwicklungsmaschine); der Job `python` der CI braucht damit 7 bis 11 min, der ganze Ablauf
  mit UI, E2E und Docker 9 bis 12 min (Läufe vom 14. und 15.10.2026). Die Szenarien sind
  deterministisch (feste Saat).
- **Dokumentationstests (2c-1, 2c-2a):** `tests/unit/test_docs.py` prüft die JSON-Beispiele, die Tabellen
  der Gründe und die Optionen des Simulators im Betriebshandbuch gegen den Code, im Abschnitt „Akku und
  Spitzen“ außerdem die Standardwerte der Einstellungen sowie die Feldnamen und Zustände von Health.
  Ändert man die Absicht, einen Grund-Text, die Optionen von `bindaems.tools.sim`, die Standardwerte
  von `ControlConfig` oder die Felder und Zustände von Health, schlägt er an, bis das Handbuch stimmt.

## Stand der Installation beim Betreiber

- Docker auf einer VM im LAN des Betreibers, Images `latest` vom Stand nach Plan 2b (ausgerollt am
  12.10.2026). Die Pläne 2c-1 und 2c-2a sind noch nicht ausgerollt.
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
- **Offen an der Anlage, als Nächstes (nach Plan 2c-2a):**
  1. Die Images ausrollen: `docker compose pull && docker compose up -d` (Betriebshandbuch 4),
     nachdem Plan 2c-2a auf `main` ist (die Pläne 2c-1 und 2c-2a rollen zusammen aus). Die neuen
     Felder `grid.import_limit_a`, `control` (seit der Hysterese auch `control.evcs_hysteresis_a`, seit
     2c-2a `support_soc_pct`, `support_hysteresis_pct`, `support_max_w`, `support_max_s`,
     `setpoint_deadband_w`, `override_refresh_s`, `peak_target_kw` und `peak_margin_kw`) und
     `vehicles.<name>.wake` haben Standardwerte; prüfe sie anhand von `deploy/config.example.yaml`.
     Der Watchdog bleibt unverändert (1.1.0), und keines der neuen Felder verlangt eine neue
     `config.ini`. Der 15-min-Wächter ruht ohne `control.peak_target_kw`.
  2. Den Dry-Run neben evcc beginnen: eine Absicht mit `"control": true` per `PUT /v1/intent`, alle
     Aktoren bleiben im Dry-Run (Betriebshandbuch, „Laderegelung“). Der core sendet dabei nichts
     auf die Geräte, und die Teil-2-Werkzeuge schreiben nicht mit: Sie schreiben nur, wenn du sie
     aufrufst. Die Entscheidungen liest der Betreiber bis 2d über die Dateien bzw.
     `GET /v1/decisions`.
     - Die EVCS-Hysterese ist eingebaut (14.10.2026). Ohne sie hätte der Dry-Run mit rund 100
       EVCS-Entscheidungen je Stunde PV-Laden begonnen; im Simulator sind es jetzt 3 bei ruhiger
       Sonne und rund 40 unter wechselnden Wolken.
     - Das Stundenbudget des Tesla kappt den Vergleich bei Wolken (Punkt 1 unten); seit der
       Fix-Welle steht das als „zurückgestellt“ im Log.
     - **An der Anlage im Dry-Run prüfen:** welcher EVCS-Status bei angestecktem Auto auf
       „Laden aus“ (`StartStop` = 0) folgt (ist es 3, bliebe ein Auto in PV nach dem ersten Stopp
       auf „geladen“ stehen, bis es abgesteckt wird); was Tessie für `charger_phases` meldet; ob
       „unklar“ fälschlich erscheint, wenn der Tesla den Wall Connector verlässt, während der e-Golf
       an der EVCS hängt (Tessie meldet `plugged` bis zu 60 s veraltet); wie oft die EVCS bei ruhiger
       Sonne ihren Strom ändert (deutlich über 10 Entscheidungen je Stunde heißt: Das Rauschen an der
       Anlage ist größer als die Hysterese, `control.evcs_hysteresis_a` anheben).
  3. EVCS-Teil-2 „Verbindung unterbrochen“ und Victron 17.2-4 nachholen (17.2-4 ist Voraussetzung der
     Live-Schaltung von Victron, Punkt 4):
     - EVCS: den Schritt „Verbindung unterbrochen“ (Punkt `evcs` erneut, die Rückfrage mit `j`
       beantworten und das Netzwerkkabel der EVCS ziehen, wenn das Werkzeug es verlangt).
     - Victron: 17.2-4 „Kein Akku ins Auto“, während ein Auto an der EVCS lädt (die übrigen
       Rückfragen mit `n`). Dynamic ESS dafür wieder vorübergehend abschalten (Entscheidung des
       Betreibers vom 12.10.2026) und danach wieder einschalten. Der Multi muss dabei auf „Ein“
       stehen.
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
        gab es keine unnötige Drosselung (6 Änderungen in 4440 Schritten von `spitze-15min`), aber
        nach einer Drosselung klingt das alte 30-s-Mittel genau mit der Wartezeit von 30 s ab
        (`SETTLE_S`, eine Sekunde Abstand), und das Modell hat kein Netzrauschen. An der Anlage
        könnten kleine Nachdrosselungen und eine verzögerte Aufhebung entstehen, die Marge
        `peak_margin_kw` fängt sie ab. Dann entscheidet der Betreiber, ob ein Totband in die
        Drosselung kommt (etwa eine Senkung unter 100 W ignorieren).
     3. **Prüfprotokoll Teil 2, Victron 17.2-4 „Kein Akku ins Auto“:** Es prüft genau den
        Mechanismus des Akku-Sollwerts.
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
  - Danach: Plan 2c-2b, dann 2d.
  - **Vor der Live-Schaltung von EVCS und Tesla (2d)** müssen diese Punkte entschieden oder
    umgesetzt sein (Gesamtreview von 2c-1; je Befund und mögliche Abhilfe, knapp auch im
    Betriebshandbuch, „Laderegelung“, „Hinweise für die Anlage“):
    1. **Stundenbudget des Tesla:** Unter PV-Wolken verbraucht die Regelung die 20 Befehle je
       Stunde (im Simulator in `wolkig` nach 2410 s; 8 von 19 Strombefehlen gingen an die
       5-A-Eigenheit). Danach werden auch ein Stopp und ein Moduswechsel zurückgestellt (in einer
       Probe im Simulator wirkte „Aus“ erst nach 19 min), gegen Design 6.3 („Aus“ stoppt gleich);
       im Log steht das seit der Fix-Welle als „zurückgestellt“. Abhilfe: ein Budget in der
       Regelung (Plätze für Start/Stopp freihalten, Stromänderungen in PV sparen) und/oder den Stopp
       nach einem Moduswechsel dringend machen.
    2. **Selbststart im laufenden Vorgang:** Startet das Auto selbst (Tesla-App), korrigiert die
       Regelung das nicht; „Aus“ hält es nicht gestoppt, PV stoppt es nicht. Abhilfe: live „Laden
       aus“ erneut senden, wenn das Auto länger als N s gegen den Modus lädt, im
       Start/Stopp-Budget.
    3. **Ansteckerkennung des Tesla:** Tessie fragt abgesteckt nur alle 600 s ab; ein selbst
       startender Tesla lädt nach dem Anstecken bis zu 10 min ungeregelt (gegen Präzisierung 9).
       Abhilfe im Adapter: schneller abfragen, solange das Auto zu Hause oder online ist oder der
       Wall Connector ein neues Fahrzeug meldet.
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
       Unterstützung auf 3 kW begrenzt ist (`akku-minpv` und `spitze-15min` starten den Tesla deshalb
       mit 5 A). Abhilfe: `vehicles.tesla.min_a: 6` vermiede diese Befehle (Design 7.2 legt 5 A fest;
       Entscheidung des Betreibers).
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
       nannte rund 700), `runtime.py` 1303 (vorher 1289, geplant rund 1200: Die Verdrahtung ist
       ausgezogen, dafür kamen die Erwartung des Overrides, das Budget und die Viertelstunde des
       Wächters dazu), `regulation.py` 263, `regulation_link.py` 302, `battery.py` 602, `peak_guard.py`
       596, `charge_points.py` 183, `charging_tesla.py` 194 und `last_values.py` 164. `charging.py` und
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
     - **2c-2a Akku und Spitzen:** umgesetzt und je Task geprüft, Gesamtreview und Integration nach
       `main` stehen aus (siehe Kurzfassung): Umbau, Befehle der Regelung an Victron
       (Akku-Unterstützung und Setpoint-Kompensation, Spec 7.6 und 7.7, Auffrischen des Overrides vor
       seinem Timeout von 5 min), 15-min-Wächter (Spec 7.4), Simulator mit 18 Szenarien.
     - **2c-2b Zuordnung:** die volle Zuordnungserkennung (Spec 7.8, Design 2c-2, Abschnitt 7) samt
       Gastfahrzeug und `vehicles.<name>.home_entity`, der Grund `assignment_detected`, Health je
       Wallbox und im Simulator der e-Golf „zu Hause“ oder „weg“, das Gastfahrzeug, jedes Auto an
       jeder Wallbox und die Szenarien aus Design 10.3. Der Plan ist noch zu schreiben.
   - **2d Bedienung:** Absicht aus der app (Regelbetrieb, Live-Schalter mit Bestätigung, Lademodi,
     Ziele, Boost, Zuordnung), Entscheidungslog in SQLite, UI-Seiten „Laden“ und „Entscheidungen“,
     HA-Bedienelemente und Benachrichtigungen. Der Dry-Run beginnt schon nach 2c-1 per
     `PUT /v1/intent`; die Bedienung macht ihn bequemer.

   Die Abnahme der Phase 2 besteht aus 2 Wochen Dry-Run, der schon nach 2c-1 beginnt, während 2c-2
   und 2d entstehen, und der Live-Schaltung danach. Abweichend von der Spec gehen EVCS und Tesla
   **gemeinsam** live, erst nach 2d, mit dem Live-Schalter im UI und ganz abgeschaltetem evcc;
   Victron geht nach 2c-2a live, sobald Dynamic ESS aus ist (Design 2c-1, Abschnitt 14; Design 2c-2,
   Abschnitt 11.1).

   **EVCS in 2b:**
   - **Lesen:** Seit der korrigierten IP-Whitelist antwortet die EVCS BindaEMS direkt per Modbus
     (Prüfprotokoll Teil 1 vom 11.10.2026); gelesen wird weiter so.
   - **Schreiben:** über den GX-Dienst `evcharger` des Cerbo (`/SetCurrent`, `/StartStop`), wie
     der Watchdog (Entscheidung des Betreibers vom 11.10.2026). Das Prüfprotokoll Teil 2 hat den Weg
     an der Anlage am 12.10.2026 bestätigt.
   - Geschrieben wird nie der Maximalstrom (5017).

### Nächster Schritt: Gesamtreview, die Anlage und Plan 2c-2b

1. Plan 2b ist auf `main`; Gesamtreview, Fix-Durchgang und Re-Review sind erledigt, die
   Entscheidungen stehen in `docs/superpowers/plans/2026-10-11-phase-2b-entscheidungen.md`. Plan
   2c-1 ist umgesetzt, je Task und im Gesamtreview geprüft, und die Fix-Welle danach ist umgesetzt;
   er ist auf `main`, ebenso die EVCS-Hysterese und die kleinen Nacharbeiten danach (`47a09a7`).
   Die Entscheidungen von 2c-1 stehen im Kopf des Designs und in
   den Absätzen „Stand Plan 2c-1“ der Spec, die offenen Punkte vor der Live-Schaltung in „Offen an
   der Anlage“.
2. An der Anlage die Schritte aus „Offen an der Anlage“.
3. Plan 2c-2a ist umgesetzt (`docs/superpowers/plans/2026-10-14-phase-2c2a-akku-spitzen.md`). Es
   folgen der Gesamtreview über den ganzen Branch und, mit Zustimmung des Betreibers, die Integration
   nach `main`.
4. **Plan 2c-2b** (Zuordnungserkennung) nach dem Design 2c-2
   (`docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md`, Abschnitte 7 und 10.3)
   schreiben, vom Betreiber durchsehen lassen und umsetzen: `core/control/assignment.py` bekommt die
   Erkennung („wer steht wo“ je Wallbox mit Indizien, Entscheidung, Gast, angenommen, unklar),
   dazu `vehicles.<name>.home_entity`, der Grund `assignment_detected`, Health je Wallbox und im
   Simulator der e-Golf „zu Hause“ oder „weg“, ein Gastfahrzeug und jedes Auto an jeder Wallbox. Das
   Szenario `zuordnung` ändert sich bewusst (Tesla an der EVCS ist künftig erkannt statt unklar).
   Von der Gast-Regel gibt es bisher nur die Absicht `guest.mode`. Die Hinweise unten sind im
   Design eingearbeitet. Dann folgt Plan 2d.
   - **Hinweis für 2d:** Eine neue Version der Absicht bindet jedes Fahrzeug neu, das `boost_until`
     oder `wallbox` nennt. 2d darf deshalb einen abgelaufenen Boost, den es aus `GET /v1/intent`
     übernommen hat, nicht zurückschicken: In der nächsten Version fehlen `boost_until` und
     `wallbox`, wo der Boost oder die Übersteuerung nicht mehr gelten soll.

## Hinweise für die Pläne 2c-2 und 2d

Aus den Reviews der Pläne 2a bis 2c-2a. Plan 2c-2a hat die Punkte zur Regelung umgesetzt, die Reste
stehen dort. Die Pläne 2c-2b und 2d gibt es noch nicht; sie sollen die übrigen Punkte aufgreifen. Was
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
- **Bedienung (2d):**
  - **Boost und Übersteuerung:** Eine neue Version der Absicht bindet jedes Fahrzeug neu, das
    `boost_until` oder `wallbox` nennt. 2d darf deshalb einen abgelaufenen Boost, den es aus
    `GET /v1/intent` übernommen hat, nicht zurückschicken.
  - Die Absicht kennt seit 2c-1 `vehicles`, `priority` und `surplus_priority` (Spec 5.3). Der core
    lehnt mit 422 und den Gründen in `details` ab: unbekannte Fahrzeuge oder Wallboxen, eine Wallbox
    für mehrere Fahrzeuge und eine Priorität, die nicht jedes Fahrzeug genau einmal nennt. Die app
    zeigt diese Sätze.
  - `GET /v1/health` nennt je Fahrzeug (`vehicles`) Modus, Boost bis, Wallbox, Zuordnung, Grenze,
    angesteckt, lädt, Weckvorgänge heute und den letzten Grund; die UI-Seite „Laden“ kann sie nutzen.
  - Die Quittierung von Sperren (Spec 7.9) kommt hier; bisher lösen sie sich nur nach
    `safety.lock_release_s`.
  - Die app nimmt für die Version der Absicht Millisekunden seit 1970.
  - Die app speichert die Entscheidungen (SQLite, Markierungen für Grafana) und holt den Rückstand
    mit `GET /v1/decisions`. Die Zeitzone im Query als `%2B` kodieren.
  - Eine erkannte fremde Änderung erzeugt in 2b Alarm und Sperre, aber keinen Eintrag im
    Entscheidungslog; der Eintrag fehlt noch (Spec 7.9).
  - Der LiveFeed der app ignoriert `mode_change`. In 2a reicht die Health-Abfrage alle 10 s.
  - `FakeView` in `tests/integration/test_core_link.py` kennt `intent()`, `submit_intent()` und
    `decisions()` noch nicht.
  - Entscheidungen tragen die Wanduhr. Springt sie zurück (NTP, Failover), bekommen neue
    Entscheidungen Zeiten vor dem letzten `since`, und ein Leser, der mit dem `ts` der letzten
    weiterliest, übersieht sie. Die app braucht dafür einen Abgleich, etwa über die `id`.
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
  spürbaren Teil der PV-Ladung des Tesla, denn der Tesla darf seinem Mittel nur alle 90 s folgen. Im
  Simulator (`wolkig`, Victron im Dry-Run) waren es rund 40 % seiner Ladeenergie, roh gerechnet: Die
  Kennzahl „Akku-Energie in PV-Autos“ nimmt die Übergänge aus und zeigt dort 0 %, der Bericht nennt
  den rohen Anteil aller PV-Autos (18 %) als Informationszahl. Mit Victron live verschiebt der
  Akku-Sollwert den Fehlbetrag aufs Netz, solange die Unterstützung nicht gilt. Das nimmt das Design
  in Kauf (Design 6.5). Eine Abhilfe wäre, den Tesla im PV-Modus am vorsichtigen Minimum statt am
  Mittel zu führen; das wäre eine Designänderung und eine Entscheidung des Betreibers.
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
  gedrosseltes Auto als nicht gefolgt. `peak_guard.py` blieb mit 596 Zeilen ungeteilt (falls falsch:
  ein späterer Umzug der Drosselfunktionen nach `peak_throttle.py`).
- **Simulator:** `_Boost` trägt das absolute Ende des Boosts. Die Kennzahl der Akku-Energie im Auto
  überspringt je Entladefolge die ersten `TRANSITION_S` s, ohne den Tesla-Fall. Ein Auffrischen im
  Dry-Run zählt dort als eigener Wert ohne Entscheidung, sonst meldete die Kennzahl falsche
  Wiederholungen.
- **Szenarien:** Drei haben einen angepassten Aufbau (Betriebshandbuch, „Simulator“), jedes aus
  demselben Grund: Ihre Prüfung ließ sich im Modell sonst nicht erfüllen, obwohl die Regelung Plan
  und Design folgte. `akku-minpv` und `spitze-15min` starten den Tesla bei 5 A
  (mit 16 A lädt er 90 s voll, Regel 2 kappt bei 8 kW). Die Reihenfolge-Prüfung in `spitze-15min`
  ist deshalb mit dem Tesla am Mindeststrom trivial erfüllt und im Handbuch so benannt; die
  Reihenfolge belegen die Unit- und Property-Tests der Drosselung (falls falsch: kein
  Ende-zu-Ende-Beleg; ein Szenario mit einem PV-Auto über dem Mindeststrom im Wächter wäre nötig).
  `schieflast-pv` bringt die Last auf L1 erst nach 10 min (Notiz unten).
- **Kein Totband in der Drosselung:** Im Modell war sie nicht nötig; die Beobachtung an der Anlage
  steht unter „Offen an der Anlage“, Punkt 4 (falls falsch: kleine Nachdrosselungen und verzögerte
  Aufhebung, begrenzt durch die Marge).

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
  `Regulation.step`), dass `_lapses_at` für „kein Override“ und für andere Arten als `grid_setpoint`
  stimmt, dass `start_cycle` den Merker `_regulated` zurücksetzt und dass der Stream in einem
  gescheiterten Zyklus weiterläuft. Die Hilfe für die 8-kW-Konfiguration steht dreimal in Tests,
  `test_runtime_regulation.py` holt Hilfen aus `test_runtime_charging.py` statt aus einem
  gemeinsamen Modul, und `test_control.py` pinnt die letzten beiden Felder von `Command` sowie
  `test_config.py` den genauen Leerraum der Beispielzeile `peak_target_kw`.

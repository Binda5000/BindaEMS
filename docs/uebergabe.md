# Übergabe an die nächste Session

Stand: 14.10.2026. Plan 2c-1 (Laderegelung) ist umgesetzt, je Task und im Gesamtreview geprüft
(„mit Korrekturen“, keine kritischen Befunde), und die Fix-Welle danach ist umgesetzt. Er liegt auf
dem Branch `ccr-e451cb4c-qeg1tm`; die Integration nach `main` folgt. Plan 2b ist auf `main`
(Fast-Forward vom selben Branch mit Zustimmung des Betreibers). `main` ist seit dem 12.10.2026 auch
der Standard-Branch auf GitHub.

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
  - **Noch nicht:** Befehle an Victron und der 15-min-Wächter (2c-2), die Bedienung (2d). Live ist
    weiterhin kein Aktor.
- **Images:** `ghcr.io/binda5000/bindaems-core:latest` und `ghcr.io/binda5000/bindaems-app:latest`.
  Sie sind öffentlich, und die CI setzt `latest` bei jedem Push auf `main`. core und app werden
  immer gemeinsam aus demselben Stand ausgerollt (gleiche Compose-Datei, gleiche Image-Tags): Die
  UI-Schemas sind streng, eine abweichende Version zeigt auf der System-Seite nur „Unerwartete
  Antwort vom Server“.
- **Tests:** 4595 Python-Tests, davon 16 Property-Tests mit Hypothesis (5 für die Safety-Schicht,
  6 für den Phasenwächter), und 233 UI-Tests; Abdeckung 97 % insgesamt und 99 % für `core/safety`,
  `core/control` und `core/actuators`.
  Geprüft wird mit ruff, mypy strict (auch für den Watchdog), import-linter (4 Verträge), pytest
  mit Abdeckungsgrenzen (≥ 75 % insgesamt, ≥ 90 % für `core/safety`, `core/control` und
  `core/actuators`), Vitest mit svelte-check, Prettier und ESLint; dazu E2E mit Playwright.
- **Offen:**
  - an der Anlage: Images ausrollen, Dry-Run neben evcc beginnen (vorher die EVCS-Hysterese
    entscheiden), der Rest von Teil 2 (EVCS-Verbindungsabbruch, Victron 17.2-4; siehe „Offen an der
    Anlage“)
  - vor der Live-Schaltung von EVCS und Tesla (2d): die Liste „Vor der Live-Schaltung“ (siehe
    „Offen an der Anlage“)
  - Plan 2c-2 („Akku und Spitzen“) schreiben, danach Plan 2d (siehe „Nächster Schritt“)
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
  - Befunde werden nach ihrer Wirkung neu bewertet. Critical und Important kommen in einen
    Fix-Durchgang, jeder mit einem Test, der vorher scheitert. Minors werden gesammelt und auf
    Wunsch erledigt („bitte die kleinigkeiten erledigen“).
- **Abschlussnachricht einer Phase:**
  - Ergebnis und Tests
  - „Rulings“ (Entscheidungen mit Kosten, falls falsch)
  - „Deferred minors“
  - Frage nach der Integration (merge, PR oder so lassen)
- **Git:**
  - Arbeitsbranch ist der Branch, den die Sitzung vorgibt (Plan 2b: `ccr-e451cb4c-qeg1tm`); der
    Standard-Branch auf GitHub ist `main`.
  - Nach `main` nur mit Zustimmung; bisher per Fast-Forward-Merge, Tests auf `main`, dann Push.
  - Pull Requests nur auf ausdrücklichen Wunsch.
  - Commit-Nachrichten auf Deutsch im Stil `fix(ui): …`, mit den Attributionszeilen, die die
    Sitzung vorgibt.
  - Keine Modellbezeichnungen in Dateien des Repositorys.
- **Secrets** nur über `BINDAEMS_*`-Umgebungsvariablen; `.env` wird nie committet.

## Wichtige Dokumente

| Dokument | Inhalt |
|---|---|
| `docs/superpowers/specs/2026-10-08-bindaems-design.md` | Spec, verbindlich. Wichtig: 7 Regelung, 8 Sicherheit, 13 Web-UI, 14 Login, 17 Prüfprotokoll, 18 Phasenplan, 19 offene Punkte. Die Absätze „Stand Plan 2a/2b/2c-1“ gehen dem älteren Text davor vor |
| `docs/superpowers/plans/2026-10-08-phase-1a-fundament-datenerfassung.md` | Plan 1a (core) |
| `docs/superpowers/plans/2026-10-09-phase-1b-app-backend.md` | Plan 1b (app) |
| `docs/superpowers/plans/2026-10-09-phase-1c-web-ui.md` | Plan 1c (UI), mit Anhang A zu den API-Formen |
| `docs/superpowers/plans/2026-10-10-phase-2a-sicherheitsbasis.md` | Plan 2a (Sicherheitsbasis), mit dem Zuschnitt der Phase 2 in 2a–2d und den Präzisierungen gegenüber der Spec |
| `docs/superpowers/plans/2026-10-10-phase-2a-entscheidungen.md` | Entscheidungen, geparkte Befunde und aufgeschobene Kleinigkeiten aus der Umsetzung von Plan 2a |
| `docs/superpowers/plans/2026-10-11-phase-2b-aktoren.md` | Plan 2b (Aktoren), 18 Tasks; Entscheidungen des Betreibers vom 11.10.2026, Präzisierungen, Texte |
| `docs/superpowers/plans/2026-10-11-phase-2b-entscheidungen.md` | Entscheidungen, geparkte Befunde und aufgeschobene Kleinigkeiten aus der Umsetzung von Plan 2b |
| `docs/superpowers/specs/2026-10-12-phase-2c1-laderegelung-design.md` | Design 2c-1 (Laderegelung), umgesetzt; der Kopf nennt, was sich beim Bau geändert hat |
| `docs/superpowers/plans/2026-10-12-phase-2c1-laderegelung.md` | Plan 2c-1, 16 Tasks; Präzisierungen gegenüber dem Design, Texte, Szenarien des Simulators |
| `docs/betrieb.md` | Betriebshandbuch: Installation, Proxy (6), HA (7), Tarif (8), Sicherung (9), Prüfprotokolle einschließlich Teil 2 (11), Web-UI (12), Betriebszustände, Freigaben, Übergabe und Entscheidungslog (13), Watchdog am Cerbo (14); am Ende „Laderegelung“ (Modi, Absicht, Dry-Run-Vergleich mit evcc, Umstieg) und „Simulator“ |
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
      (nächster Punkt). Auch rein.
    - Die Laderegelung in `control/` (seit 2c-1):
      - `phase_guard.py`: Phasenwächter (Ziele je Modus, Last, Verteilung, Überlast)
      - `surplus.py`: Überschuss und seine Verteilung auf die Autos
      - `charge_modes.py`: Lademodi mit Start-, Stopp- und Mindestzeiten
      - `charge_inputs.py`: Eingaben aus Snapshot und abgeleiteten Größen
      - `assignment.py`: Zuordnung, Ansteckvorgänge, Boost und Übersteuerung
      - `tesla_policy.py`, `charging_tesla.py`: gleitendes Mittel, Weckkontingent, Tesla-Besonderheiten
      - `charging.py`: der `ChargeController` verbindet die Bausteine je Zyklus
    - `actuators/`: die konkreten Aktoren für EVCS (GX-Dienst `evcharger`), Victron (MQTT) und
      Tesla (Tessie)
    - `persistence/`: atomare JSON-Dateien (mit `fsync` des Verzeichnisses) und das Entscheidungslog
      (`decision_spool.py`)
    - `watchdog_client/`: Watchdog-Status lesen und Heartbeat senden
  - Auf Geräte schreibt der core den Heartbeat, die Sicherwerte im sicheren Zustand und die der
    Übergabe am Ende des Live-Betriebs und seit 2c-1 die Befehle der Laderegelung an EVCS und
    Tesla (nie an Victron; das folgt mit 2c-2). Jeder Befehl geht durch die `SafetyLayer`.
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
    `config.yaml` verlangt eine neue `config.ini` auf dem Cerbo (Betriebshandbuch 14).
- **Neue Pakete unter `src/bindaems`** brauchen eine `__init__.py`; sonst sieht import-linter sie
  nicht (`tests/unit/test_layout.py`).
- **Gates für die Safety-Schicht:** `core/safety` und `core/control` dürfen nichts aus
  `core.actuators`, `core.adapters`, `core.api`, `core.runtime`, `core.telemetry`,
  `core.accounting` und `core.checks` importieren, auch nicht über Umwege (import-linter-Vertrag).
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
  Weckkontingents (Wiener Zeit) nach der Wanduhr (`ChargeInputs.wall_now`).
- **Tests mit respx und Tessie (2b):** Eine Anfrage ohne passende Route fängt der Tesla-Aktor als
  Fehler ab, und respx zeichnet sie nicht auf. Wer prüft, dass nichts gesendet wird, braucht eine
  Catch-all-Route und zählt deren Aufrufe.
- **Tests der Laderegelung (2c-1):** Fehlen die Ströme von EVCS und Wall Connector, gilt jede Phase
  als voll, und kein Auto startet (Design 5.5). Tests müssen die Ströme darum angeben, auch 0 A; nur
  ein fehlender Wert macht eine Phase voll. Die gemeinsamen Vorgaben stehen in
  `tests/unit/core/control/charge_helpers.py`. Die Regelung sendet nur Änderungen; wer einem Aktor
  Befehle gibt, ohne dass sie es sieht, ruft `ChargeController.forget` auf (die Laufzeit tut das für
  Keeper, Übergabe, fremde Systeme und den Wechsel der Betriebsart).
- **Simulator (2c-1):** Er nutzt die echten Aktoren und die Parser der Adapter. Ändert man deren
  Zeiten, Texte oder Signale, ändern sich die Szenarien mit. Alle zehn Szenarien laufen in
  `tests/integration/test_sim_scenarios.py` mit je etwa 5 bis 20 s; die volle CI-Prüfung braucht
  deshalb rund 12 min. Die Szenarien sind deterministisch (feste Saat).
- **Dokumentationstests (2c-1):** `tests/unit/test_docs.py` prüft die JSON-Beispiele, die Tabelle der
  Gründe und die Optionen des Simulators im Betriebshandbuch gegen den Code. Ändert man die Absicht,
  einen Grund-Text oder die Optionen von `bindaems.tools.sim`, schlägt er an, bis das Handbuch stimmt.

## Stand der Installation beim Betreiber

- Docker auf der VM `192.168.82.20`, Images `latest` vom Stand nach Plan 2b (ausgerollt am
  12.10.2026). Plan 2c-1 ist noch nicht ausgerollt.
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
  - Dynamic ESS bleibt nach Entscheidung des Betreibers an, bis Victron live geschaltet wird.
  - Die EVCS (AC22NS, Produkt-ID 0xC026) liest der Cerbo per Modbus TCP unter
    192.168.81.41. BindaEMS bekam unter derselben Adresse zuerst nur Nullen. Ursache war die
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
- **Offen an der Anlage, als Nächstes (nach Plan 2c-1):**
  1. Die Images ausrollen: `docker compose pull && docker compose up -d` (Betriebshandbuch 4). Die
     neuen Felder `grid.import_limit_a`, `control` und `vehicles.<name>.wake` haben Standardwerte;
     prüfe sie anhand von `deploy/config.example.yaml`. Der Watchdog bleibt unverändert (1.1.0).
  2. Den Dry-Run neben evcc beginnen: eine Absicht mit `"control": true` per `PUT /v1/intent`, alle
     Aktoren bleiben im Dry-Run (Betriebshandbuch, „Laderegelung“). Der core sendet dabei nichts
     auf die Geräte, und die Teil-2-Werkzeuge schreiben nicht mit: Sie schreiben nur, wenn du sie
     aufrufst. Die Entscheidungen liest der Betreiber bis 2d über die Dateien bzw.
     `GET /v1/decisions`.
     - Vorher entscheidet der Betreiber über die EVCS-Hysterese (Punkt 8 der Liste „Vor der
       Live-Schaltung“ unten); sonst beginnt der Dry-Run mit rund 100 EVCS-Entscheidungen je Stunde
       PV-Laden.
     - Das Stundenbudget des Tesla kappt den Vergleich bei Wolken (Punkt 1 unten); seit der
       Fix-Welle steht das als „zurückgestellt“ im Log.
     - **An der Anlage im Dry-Run prüfen:** welcher EVCS-Status bei angestecktem Auto auf
       „Laden aus“ (`StartStop` = 0) folgt (ist es 3, bliebe ein Auto in PV nach dem ersten Stopp
       auf „geladen“ stehen, bis es abgesteckt wird); was Tessie für `charger_phases` meldet; ob
       „unklar“ fälschlich erscheint, wenn der Tesla den Wall Connector verlässt, während der e-Golf
       an der EVCS hängt (Tessie meldet `plugged` bis zu 60 s veraltet).
  3. EVCS-Teil-2 „Verbindung unterbrochen“ und Victron 17.2-4 nachholen:
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
  - Teil 2 schreibt auf die Geräte: nicht während der 7 Tage der Abnahme Phase 1 und ihres
    24-h-Mitschnitts (`docs/verification/abnahme-phase-1.md`, 4.2 und 4.3).
  - Danach: Plan 2c-2, dann 2d.
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
    4. **Ausnahme in der Regelung:** Wirft die Regelung in jedem Zyklus, laufen auch Keeper, Stream
       und Alarme nicht. Abhilfe: die Ausnahme fangen, die Regelung ruhen lassen, Alarm, Heartbeat
       zurückhalten, den Keeper weiter ausführen.
    5. **5-A-Eigenheit:** Befehle auf oder von 5 A gehen über Tessie beim ersten Mal verloren und
       kosten einen Neuversuch. Abhilfe: `vehicles.tesla.min_a: 6` vermiede diese Befehle (Design
       7.2 legt 5 A fest; Entscheidung des Betreibers).
    6. **Simulator:** Kein PV-Szenario lässt das Importziel binden, „Zeit über dem Ziel in PV-Modi“
       prüft darum wenig. Abhilfe: ein PV-Szenario mit schwerer Last auf einer Phase.
    7. **Technische Schulden vor 2c-2:** `charging.py` (rund 1040 Zeilen) und `runtime.py` (rund
       1290) sind groß, Hilfsfunktionen wie `_since`, `_number` und `PV_SURPLUS` gibt es doppelt.
       Abhilfe: aufteilen und zusammenführen, bevor 2c-2 dazukommt.
    8. **EVCS-Hysterese (vor dem Dry-Run zu entscheiden):** Der EVCS-Strom wechselt mit dem Rauschen
       der Hauslast zwischen zwei ganzen Ampere (im Simulator 77 bis 112 Befehle bzw.
       Dry-Run-Entscheidungen je Stunde PV-Laden). Abhilfe: Erhöhen erst ab einem Anteil von
       Sollwert + 1 A + etwa 0,3 A oder nach etwa 30 s, Senken sofort; das weicht von Design 6.5 ab
       („folgt jede Sekunde“), darum entscheidet der Betreiber.

    Dazu kommen die Punkte „Offen vor dem Live-Betrieb des Tesla“ (Reserve des sicheren Zustands,
    Annahmen des Prüffensters) unter „Hinweise für die Pläne 2c-2 und 2d“.
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
     - **2c-2 Akku und Spitzen:** Befehle der Regelung an Victron (Akku-Unterstützung und
       Setpoint-Kompensation, Spec 7.6 und 7.7, Auffrischen des Overrides vor seinem Timeout von
       5 min), 15-min-Wächter (Spec 7.4) und die volle Zuordnungserkennung (Spec 7.8). Der Plan ist
       noch zu schreiben.
   - **2d Bedienung:** Absicht aus der app (Regelbetrieb, Live-Schalter mit Bestätigung, Lademodi,
     Ziele, Boost, Zuordnung), Entscheidungslog in SQLite, UI-Seiten „Laden“ und „Entscheidungen“,
     HA-Bedienelemente und Benachrichtigungen. Der Dry-Run beginnt schon nach 2c-1 per
     `PUT /v1/intent`; die Bedienung macht ihn bequemer.

   Die Abnahme der Phase 2 besteht aus 2 Wochen Dry-Run, der schon nach 2c-1 beginnt, während 2c-2
   und 2d entstehen, und der Live-Schaltung danach. Abweichend von der Spec gehen EVCS und Tesla
   **gemeinsam** live, erst nach 2d, mit dem Live-Schalter im UI und ganz abgeschaltetem evcc;
   Victron folgt nach 2c-2, wenn Dynamic ESS aus ist (Design 2c-1, Abschnitt 14).

   **EVCS in 2b:**
   - **Lesen:** Seit der korrigierten IP-Whitelist antwortet die EVCS BindaEMS direkt per Modbus
     (Prüfprotokoll Teil 1 vom 11.10.2026); gelesen wird weiter so.
   - **Schreiben:** über den GX-Dienst `evcharger` des Cerbo (`/SetCurrent`, `/StartStop`), wie
     der Watchdog (Entscheidung des Betreibers vom 11.10.2026). Das Prüfprotokoll Teil 2 hat den Weg
     an der Anlage am 12.10.2026 bestätigt.
   - Geschrieben wird nie der Maximalstrom (5017).

### Nächster Schritt: die Anlage und Plan 2c-2

1. Plan 2b ist auf `main`; Gesamtreview, Fix-Durchgang und Re-Review sind erledigt, die
   Entscheidungen stehen in `docs/superpowers/plans/2026-10-11-phase-2b-entscheidungen.md`. Plan
   2c-1 ist umgesetzt, je Task und im Gesamtreview geprüft, und die Fix-Welle danach ist umgesetzt;
   die Integration nach `main` folgt. Seine Entscheidungen stehen im Kopf des Designs und in den
   Absätzen „Stand Plan 2c-1“ der Spec, die offenen Punkte vor der Live-Schaltung in „Offen an der
   Anlage“.
2. An der Anlage die Schritte aus „Offen an der Anlage“.
3. Danach Plan 2c-2 („Akku und Spitzen“) schreiben; die Hinweise unten gehören hinein. Dann folgt
   Plan 2d.
   - **Hinweis für 2d:** Eine neue Version der Absicht bindet jedes Fahrzeug neu, das `boost_until`
     oder `wallbox` nennt. 2d darf deshalb einen abgelaufenen Boost, den es aus `GET /v1/intent`
     übernommen hat, nicht zurückschicken: In der nächsten Version fehlen `boost_until` und
     `wallbox`, wo der Boost oder die Übersteuerung nicht mehr gelten soll.

## Hinweise für die Pläne 2c-2 und 2d

Aus den Reviews der Pläne 2a bis 2c-1. Die Pläne 2c-2 und 2d gibt es noch nicht; sie sollen diese
Punkte aufgreifen. Was Plan 2c-1 aus der früheren Liste erledigt hat (Freigabe erst ohne offenen
Schreibvorgang, Aufräumen des Entscheidungslogs, Heartbeat-Zähler je Lauf, SAFE ohne Tesla, der nicht
lädt, Auslösung des Watchdogs, Laden nach einem Inselbetrieb, Selbstprüfung des Multi-Modus), steht in
der Kurzfassung.

- **Regelung (2c-2):**
  - Befehle der Regelung gehen durch `CommandExecutor.execute`: Er ruft `SafetyLayer.authorize`
    genau einmal je betrachtetem Befehl auf, denn `authorize` verändert Rate-Limiter und
    Schreibbudget. Die Laderegelung zeigt das Muster: Sie berechnet Befehle, die Laufzeit führt sie aus
    und gibt jedes Urteil mit `note_verdict` an sie zurück.
  - **Bei „Regelbetrieb aus“ darf auch 2c-2 nicht regeln,** nicht in DEGRADED oder SAFE mit dem
    Übergabe-Grund: Dort gehören dem core nur die Sicherwerte der Übergabe.
  - **Importlimit:** Peak Shaving steht an der Anlage bei 20 A je Phase mit „immer“ (gewollt,
    Spec 8.1). Es wirkt nur, solange der Multi auf „Ein“ steht: Mit „Nur Laden“ stieg L3 am
    12.10.2026 auf 33,3 A, nahe an der 35-A-Sicherung. Seit 2c-1 warnen die Selbstprüfungen
    `multi_mode` und `import_limit`, und der Phasenwächter rechnet ohne die Hilfe des Akkus. Die
    Setpoint-Kompensation muss das Importlimit berücksichtigen.
  - `SafetyContext.modes` muss `ActuatorMode`-Werte enthalten; ein String „live“ bliebe still im
    Dry-Run. `wallbox_phase_a` ist nach Netzphasen geschlüsselt (der Erzeuger wendet `phase_map`
    an), und `present[(aktor, current)]` ist der Sollstrom, nicht der Messstrom. Das stellt
    `core/control/context.py` bereit.
  - `grid_budget_w` darf nie NaN oder ±∞ sein (Erzeuger ist der 15-min-Wächter). Heute ist es
    `None`, denn es gibt noch kein Budget.
  - `VictronMqttAdapter.write` nur aus dem Event-Loop-Thread aufrufen; es reiht nur ein.
  - Die `inputs` der Entscheidungen enthalten seit 2c-1 SOC, Akkuleistung, Netzstrom je Phase,
    Überschuss, Grenze des Wächters und Ist-Werte; Preis, Budget und Plan kommen mit 2c-2 bzw.
    Phase 3 dazu. Weckregeln für den Tesla im Modus „Geplant“ kommen mit Phase 4.
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
    vom 12.10.2026). Die Regelung muss einen gewollten Override vorher auffrischen.
  - **Entladegrenze:** Es gibt einen flüchtigen Override `ess.override.maxdischargepower`. Prüfen,
    ob er die persistente Entladegrenze ersetzen kann; das schont den Flash.
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
    die Auslösung sie zurücksetzt.

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

- Die EVCS folgt dem Überschuss in ganzen Ampere (Totband 1 A, 5 s Abstand in der Safety-Schicht). In
  den PV-Szenarien des Simulators waren das 77 bis 112 Strombefehle je Stunde, auch weil das
  Rauschen der Hauslast an der Abrundungsgrenze liegt. Über eine Hysterese beim Erhöhen entscheidet
  der Betreiber vor dem Dry-Run (Liste „Vor der Live-Schaltung“, Punkt 8).
- Bei schnell wechselnden Wolken deckt der Hausakku einen spürbaren Teil der PV-Ladung des Tesla,
  denn der Tesla darf seinem Mittel nur alle 90 s folgen. Im Simulator (`wolkig`) waren es rund 40 %
  seiner Ladeenergie, roh gerechnet: Die Kennzahl „Akku-Energie in PV-Autos“ nimmt die Übergänge aus
  und zeigt dort 0 %, der Bericht nennt den rohen Anteil aller PV-Autos (18 %) als
  Informationszahl. Das nimmt das Design in Kauf (Design 6.5). Eine Abhilfe wäre, den Tesla im
  PV-Modus am vorsichtigen Minimum statt am Mittel zu führen; das wäre eine Designänderung und eine
  Entscheidung des Betreibers.
- Ohne Ströme von EVCS oder Wall Connector startet die Regelung kein Auto (Design 5.5), auch wenn die
  Grenze sonst reichte.
- Der Tessie-Adapter trennt seine Quelle bei fehlschlagenden Abfragen nicht (nur beim Abbruch). Die
  Stufe „Tessie nicht erreichbar: Tesla nicht steuerbar“ (Spec 8.4) greift an der Anlage darum erst
  über die Fehler-Sperre des Aktors oder das Veralten der Werte nach 900 s.

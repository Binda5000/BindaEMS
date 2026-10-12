# Übergabe an die nächste Session

Stand: 12.10.2026. Plan 2b ist auf `main` (Fast-Forward vom Branch `ccr-e451cb4c-qeg1tm` mit
Zustimmung des Betreibers). `main` ist seit dem 12.10.2026 auch der Standard-Branch auf GitHub.

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
    Tesla (Befehle über Tessie im Hintergrund, weckt das Auto nie). Jeder Befehl geht durch den
    Ausführer (`core/control`), die Safety-Schicht und das Rücklesen; im Dry-Run wird er nur
    protokolliert.
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
  - **Noch keine Regelung (2c) und keine Bedienung (2d):** Befehle entstehen nur aus dem sicheren
    Zustand und der Übergabe, und im Regelbetrieb ist noch kein Aktor live. Die Punkte aus „Vor
    einer Live-Schaltung in 2b zu klären“ (Gesamtreview 2a) sind erledigt.
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
- **Images:** `ghcr.io/binda5000/bindaems-core:latest` und `ghcr.io/binda5000/bindaems-app:latest`.
  Sie sind öffentlich, und die CI setzt `latest` bei jedem Push auf `main`. core und app werden
  immer gemeinsam aus demselben Stand ausgerollt (gleiche Compose-Datei, gleiche Image-Tags): Die
  UI-Schemas sind streng, eine abweichende Version zeigt auf der System-Seite nur „Unerwartete
  Antwort vom Server“.
- **Tests:** 3627 Python-Tests, davon 10 Property-Tests mit Hypothesis (5 für die Safety-Schicht),
  und 233 UI-Tests; Abdeckung 96 % insgesamt und 99 % für `core/safety`, `core/control` und
  `core/actuators`.
  Geprüft wird mit ruff, mypy strict (auch für den Watchdog), import-linter (4 Verträge), pytest
  mit Abdeckungsgrenzen (≥ 75 % insgesamt, ≥ 90 % für `core/safety`, `core/control` und
  `core/actuators`), Vitest mit svelte-check, Prettier und ESLint; dazu E2E mit Playwright.
- **Offen:**
  - Plan 2c schreiben (siehe „Nächster Schritt“)
  - an der Anlage: der Rest von Teil 2 (EVCS-Verbindungsabbruch, Victron 17.2-4; siehe „Offen
    an der Anlage“)
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
| `docs/superpowers/specs/2026-10-08-bindaems-design.md` | Spec, verbindlich. Wichtig: 8 Sicherheit, 13 Web-UI, 14 Login, 17 Prüfprotokoll, 18 Phasenplan, 19 offene Punkte |
| `docs/superpowers/plans/2026-10-08-phase-1a-fundament-datenerfassung.md` | Plan 1a (core) |
| `docs/superpowers/plans/2026-10-09-phase-1b-app-backend.md` | Plan 1b (app) |
| `docs/superpowers/plans/2026-10-09-phase-1c-web-ui.md` | Plan 1c (UI), mit Anhang A zu den API-Formen |
| `docs/superpowers/plans/2026-10-10-phase-2a-sicherheitsbasis.md` | Plan 2a (Sicherheitsbasis), mit dem Zuschnitt der Phase 2 in 2a–2d und den Präzisierungen gegenüber der Spec |
| `docs/superpowers/plans/2026-10-10-phase-2a-entscheidungen.md` | Entscheidungen, geparkte Befunde und aufgeschobene Kleinigkeiten aus der Umsetzung von Plan 2a |
| `docs/superpowers/plans/2026-10-11-phase-2b-aktoren.md` | Plan 2b (Aktoren), 18 Tasks; Entscheidungen des Betreibers vom 11.10.2026, Präzisierungen, Texte |
| `docs/superpowers/plans/2026-10-11-phase-2b-entscheidungen.md` | Entscheidungen, geparkte Befunde und aufgeschobene Kleinigkeiten aus der Umsetzung von Plan 2b |
| `docs/betrieb.md` | Betriebshandbuch: Installation, Proxy (6), HA (7), Tarif (8), Sicherung (9), Prüfprotokolle einschließlich Teil 2 (11), Web-UI (12), Betriebszustände, Freigaben, Übergabe und Entscheidungslog (13), Watchdog am Cerbo (14) |
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
      (`foreign.py`) und der Aufbau des `SafetyContext` (`context.py`). Auch rein.
    - `actuators/`: die konkreten Aktoren für EVCS (GX-Dienst `evcharger`), Victron (MQTT) und
      Tesla (Tessie)
    - `persistence/`: atomare JSON-Dateien (mit `fsync` des Verzeichnisses) und das Entscheidungslog
      (`decision_spool.py`)
    - `watchdog_client/`: Watchdog-Status lesen und Heartbeat senden
  - Auf Geräte schreibt der core den Heartbeat, die Sicherwerte im sicheren Zustand und die der
    Übergabe am Ende des Live-Betriebs. Die Regelung folgt mit 2c. Jeder Befehl geht durch die
    `SafetyLayer`.
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
  **`src/bindaems/tools`:** Prüfprotokoll Teil 1, `watchdog_config`, `watchdog_probe` und
  `verify_part2` (mit dem Paket `part2/`).
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
  fremden Änderungen eine andere Zeit als „seit“ als die übrigen (Betriebshandbuch 14).
- **Tests mit respx und Tessie (2b):** Eine Anfrage ohne passende Route fängt der Tesla-Aktor als
  Fehler ab, und respx zeichnet sie nicht auf. Wer prüft, dass nichts gesendet wird, braucht eine
  Catch-all-Route und zählt deren Aufrufe.

## Stand der Installation beim Betreiber

- Docker auf der VM `192.168.82.20`, Images `latest` vom Stand nach Plan 2b (ausgerollt am
  12.10.2026).
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
- **Offen an der Anlage, als Nächstes (Plan 2b):**
  1. EVCS: den Schritt „Verbindung unterbrochen“ nachholen (Punkt `evcs` erneut, die Rückfrage
     mit `j` beantworten und das Netzwerkkabel der EVCS ziehen, wenn das Werkzeug es verlangt).
  2. Victron: 17.2-4 „Kein Akku ins Auto“ nachholen, während ein Auto an der EVCS lädt (die
     übrigen Rückfragen mit `n`). Dynamic ESS dafür wieder vorübergehend abschalten
     (Entscheidung des Betreibers vom 12.10.2026) und danach wieder einschalten. Der Multi muss
     dabei auf „Ein“ stehen.
  3. Die Berichte nach `docs/verification/` übernehmen.
  - Teil 2 schreibt auf die Geräte: nicht während der 7 Tage der Abnahme Phase 1 und ihres
    24-h-Mitschnitts (`docs/verification/abnahme-phase-1.md`, 4.2 und 4.3).
  - Danach: Plan 2c.
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
   - **2b Aktoren:** umgesetzt, je Task und im Gesamtreview geprüft und auf `main`; an der Anlage
     noch nicht ausgerollt (siehe „Offen an der Anlage“).
     - Aktoren für EVCS, Victron und Tesla mit Dry-Run und Rücklese-Prüfung
     - sicherer Zustand (Spec 8.5) und Übergabe am Ende des Live-Betriebs
     - Entscheidungslog im core (Spool, Stream, `GET /v1/decisions`)
     - Erkennung fremder Änderungen (Spec 7.9)
     - Werkzeug `verify_part2` für das Prüfprotokoll Teil 2, schreibend und gemeinsam mit dem
       Betreiber (Spec 17.2); an der Anlage noch nicht ausgeführt
     - Watchdog 1.1.0 mit der Aktion für den hub4-Override
   - **2c Regelung:** Regelzyklus, Phasenwächter, 15-min-Wächter, Modi Sofort, PV, Min+PV, Aus und
     Boost, Akku-Unterstützung, Zuordnung Auto ↔ Wallbox, Degradationsstufen und der
     Anlagensimulator mit Regelkreistests in der CI.
   - **2d Bedienung:** Absicht aus der app (Regelbetrieb, Live-Schalter mit Bestätigung, Lademodi,
     Ziele, Boost, Zuordnung), Entscheidungslog in SQLite, UI-Seiten „Laden“ und „Entscheidungen“,
     HA-Bedienelemente und Benachrichtigungen. Mit 2d beginnt der Dry-Run.

   Nach 2c und 2d kommt die Abnahme der Phase 2: 2 Wochen Dry-Run, dann die Live-Schaltung je
   Aktor in der Reihenfolge EVCS, Victron, Tesla.

   **EVCS in 2b:**
   - **Lesen:** Seit der korrigierten IP-Whitelist antwortet die EVCS BindaEMS direkt per Modbus
     (Prüfprotokoll Teil 1 vom 11.10.2026); gelesen wird weiter so.
   - **Schreiben:** über den GX-Dienst `evcharger` des Cerbo (`/SetCurrent`, `/StartStop`), wie
     der Watchdog (Entscheidung des Betreibers vom 11.10.2026). Das Prüfprotokoll Teil 2 bestätigt
     den Weg an der Anlage; das steht noch aus.
   - Geschrieben wird nie der Maximalstrom (5017).

### Nächster Schritt: die Anlage und Plan 2c

1. Plan 2b ist auf `main`; Gesamtreview, Fix-Durchgang und Re-Review sind erledigt, die
   Entscheidungen stehen in `docs/superpowers/plans/2026-10-11-phase-2b-entscheidungen.md`.
2. An der Anlage die Schritte aus „Offen an der Anlage“.
3. Danach Plan 2c schreiben; die geparkten Befunde aus der Entscheidungsdatei und die Hinweise
   unten gehören hinein.

## Hinweise für die Pläne 2c und 2d

Aus den Reviews der Pläne 2a und 2b. Die Pläne 2c und 2d gibt es noch nicht; sie sollen diese
Punkte aufgreifen:

- **Regelung (2c):**
  - Befehle der Regelung gehen durch `CommandExecutor.execute`: Er ruft `SafetyLayer.authorize`
    genau einmal je betrachtetem Befehl auf, denn `authorize` verändert Rate-Limiter und
    Schreibbudget.
  - **Bei „Regelbetrieb aus“ darf 2c nicht regeln,** auch nicht in DEGRADED oder SAFE mit dem
    Übergabe-Grund: Dort gehören dem core nur die Sicherwerte der Übergabe.
  - **Importlimit:** Peak Shaving steht an der Anlage bei 20 A je Phase mit „immer“ (gewollt,
    Spec 8.1). Phasenwächter und Setpoint-Kompensation müssen das berücksichtigen. Es wirkt nur,
    solange der Multi auf „Ein“ steht: Mit „Nur Laden“ stieg L3 am 12.10.2026 auf 33,3 A, nahe
    an der 35-A-Sicherung. Der core liest `vebus.mode`, prüft ihn aber noch nicht; eine
    Selbstprüfung mit Warnung, wenn der Multi nicht auf „Ein“ (3) steht, gehört in 2c.
  - `SafetyContext.modes` muss `ActuatorMode`-Werte enthalten; ein String „live“ bliebe still im
    Dry-Run. `wallbox_phase_a` ist nach Netzphasen geschlüsselt (der Erzeuger wendet `phase_map`
    an), und `present[(aktor, current)]` ist der Sollstrom, nicht der Messstrom. Das stellt
    `core/control/context.py` bereit.
  - `grid_budget_w` darf nie NaN oder ±∞ sein (Erzeuger ist der 15-min-Wächter). Heute ist es
    `None`, denn es gibt noch kein Budget.
  - `VictronMqttAdapter.write` nur aus dem Event-Loop-Thread aufrufen; es reiht nur ein.
  - Die `inputs` der Entscheidungen enthalten in 2b SOC, Akkuleistung und Netzstrom je Phase;
    Preis, Überschuss, Budget und Plan kommen mit 2c dazu. Weckregeln für den Tesla in den Modi
    Sofort und Geplant kommen ebenfalls mit 2c.
  - **Vor dem ersten Regelwert:** Die Freigabe 0 erst, wenn für den Aktor kein Schreibvorgang mehr
    offen ist. Ein Wert der Regelung, der nach der Freigabe noch ankommt, stünde sonst ohne
    Watchdog.
  - Das Aufräumen des Entscheidungslogs (`_prune_decisions`) läuft im Zyklus und fällt mit einem
    gescheiterten Zyklus aus; es gehört in `cycle_once` hinter den Flush.
  - **Vor der ersten Live-Schaltung:** Der Zähler beginnt in jedem Lauf bei 1, und nach einer
    bestätigten Freigabe bleiben die eigenen Zähler gemerkt (`HeartbeatClient.recent`). Ein Echo
    eines früheren Laufs kann so als eigenes gelten. Nach einer bestätigten Freigabe die Zähler
    vergessen und je Lauf unterscheidbare Startwerte wählen.
  - In SAFE versucht der core die Sicherwerte auch an einen Tesla, der schläft oder nicht zu Hause
    ist: Jeder Versuch endet „nicht gesendet“ und erzeugt Entscheidungen, einen Alarm und Einträge
    im Rate-Limiter.
  - Setzt der Watchdog bei einer Auslösung Werte, ändern sie sich ohne Schreiben des core. Bei
    Aktoren, die die Regelung hält, darf das nicht als fremde Änderung zählen.
  - Nach einem Inselbetrieb bleibt das Laden aus („Laden aus“ des sicheren Zustands); die Regelung
    muss es danach wieder aufnehmen.
  - **Tesla-Ladestrom:** Über Tessie wirkt ein Befehl, der auf 5 A setzt oder von 5 A weggeht, erst
    beim zweiten Mal, obwohl Tessie den ersten mit `result: true` bestätigt (Prüfprotokoll Teil 2
    vom 12.10.2026, Nachtrag). Seit dem 12.10.2026 prüft der Tesla-Aktor den Strom nach der
    Bestätigung im Tessie-Cache, mit der ersten Meldung ab 10 s bis 50 s danach (`SETTLE_S`,
    `VERIFY_S`). Zeigt sie einen anderen Strom, meldet er `NOT_TAKEN`; ohne Meldung gilt Tessies
    Bestätigung. Er sendet nicht selbst nach: Den neuen Versuch gibt der Aufrufer durch die
    Safety-Schicht (Spec 6.5, 8.5). Die Regelung muss einen Strom nach `NOT_TAKEN` erneut
    anfordern; als nicht dringender Befehl wartet der Neuversuch dabei auf die 90-s-Regel, wenn 2c
    keine Ausnahme vorsieht. Auch einen bestätigten Befehl nimmt sie nicht als Wirkung, maßgeblich
    ist der gemessene Strom. Der dritte Lauf vom 12.10.2026 bestätigt Prüfung und Wiederholung.
    Offen vor dem Live-Betrieb des Tesla: die Reserve des sicheren Zustands (Spec 8.5, Nachtrag)
    und ob die 10 s für den Cache reichen; im Lauf kam die erste gewertete Meldung erst nach rund
    30 s, der Verzug ist damit nicht gemessen.
  - **Netz-Sollwert-Override:** Er verschwindet nach 5 min ohne Auffrischen (Prüfprotokoll Teil 2
    vom 12.10.2026). Die Regelung muss einen gewollten Override vorher auffrischen.
  - **Entladegrenze:** Es gibt einen flüchtigen Override `ess.override.maxdischargepower`. Prüfen,
    ob er die persistente Entladegrenze ersetzen kann; das schont den Flash.
- **Bedienung (2d):**
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

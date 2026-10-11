# Übergabe an die nächste Session

Stand: 11.10.2026. Neue Arbeit liegt zuerst auf dem Arbeitsbranch
`claude/ems-haus-akku-pv-93c0s6`; was davon schon auf `main` ist, zeigt `git log main`.

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
  - Heartbeat an den Watchdog, nur solange ein Victron- oder EVCS-Aktor live angefordert ist
  - Status in UI (System-Seite) und HA (Sensor „Watchdog ausgelöst“)
  - Abdeckungsgrenzen und ein Importvertrag in der CI
  - BindaEMS schreibt damit noch auf kein Gerät. Ohne Bedienung (Plan 2d) ist nichts live: Der
    core steht in OBSERVE und sendet keinen Heartbeat, und ein installierter Watchdog wartet.
- **Images:** `ghcr.io/binda5000/bindaems-core:latest` und `ghcr.io/binda5000/bindaems-app:latest`.
  Sie sind öffentlich, und die CI setzt `latest` bei jedem Push auf `main`. core und app werden
  immer gemeinsam aus demselben Stand ausgerollt (gleiche Compose-Datei, gleiche Image-Tags): Die
  UI-Schemas sind streng, eine abweichende Version zeigt auf der System-Seite nur „Unerwartete
  Antwort vom Server“.
- **Tests:**
  - Python: 1823 (ruff, mypy strict auch für den Watchdog, import-linter mit 4 Verträgen);
    Abdeckung 95 % insgesamt, `core/safety` und `core/control` 99 %
  - darin: Cerbo-Watchdog 243 Fälle, Property-Tests 7 (davon 5 für die Safety-Schicht)
  - UI: Vitest 232 (svelte-check, Prettier, ESLint)
  - E2E: Playwright 11
- **Offen:**
  - an der Anlage: Watchdog installieren und die Probe laufen lassen (siehe unten)
  - die Installation beim Betreiber fertigstellen (HTTPS-Proxy)
  - die Abnahme der Phase 1 an der Anlage
  - der Plan 2b (Aktoren)

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
  - Befunde werden nach ihrer Wirkung neu bewertet. Critical und Important kommen in einen
    Fix-Durchgang, jeder mit einem Test, der vorher scheitert. Minors werden gesammelt und auf
    Wunsch erledigt („bitte die kleinigkeiten erledigen“).
- **Abschlussnachricht einer Phase:**
  - Ergebnis und Tests
  - „Rulings“ (Entscheidungen mit Kosten, falls falsch)
  - „Deferred minors“
  - Frage nach der Integration (merge, PR oder so lassen)
- **Git:**
  - Arbeitsbranch `claude/ems-haus-akku-pv-93c0s6`.
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
| `docs/betrieb.md` | Betriebshandbuch: Installation, Proxy (6), HA (7), Tarif (8), Sicherung (9), Prüfprotokolle (11), Web-UI (12), Betriebszustände und Freigaben (13), Watchdog am Cerbo (14) |
| `cerbo-watchdog/README.md` | Watchdog: Zustände, Zeiten, D-Bus-Pfade, Installation und Probe |
| `docs/verification/abnahme-phase-1.md` | Checkliste für die Abnahme der Phase 1 |
| `docs/verification/README.md` | Prüfprotokoll Teil 1, Preise und Watchdog-Probe: Aufruf und Ergebnis |
| `README.md` | Entwicklung, Web-UI, Demo-Backend |

## Aufbau

- **`src/bindaems/core`**
  - liest die Geräte (Victron über MQTT, EVCS über Modbus, Wall Connector, Tessie,
    Home Assistant)
  - Zustandsspeicher, abgeleitete Größen, Plausibilität, Flüsse je Viertelstunde
  - InfluxDB-Schreiber mit Spool, interne API mit Stream
  - seit 2a vier Pakete für die Sicherheit:
    - `safety/`: harte Grenzen, die Regeln 1–7, Rate-Limits, Schreibbudget, Eingangsprüfung,
      Sperren, wirksame Betriebsarten (Regel 8) und die `SafetyLayer`. Sie ist rein.
    - `control/`: Betriebszustände (`operating.py`) und Absicht (`intent.py`, Datei `intent.json`)
    - `persistence/`: atomare JSON-Dateien
    - `watchdog_client/`: Watchdog-Status lesen und Heartbeat senden
  - Auf Geräte schreibt der core nur den Heartbeat an den Watchdog, und den nur bei einem live
    angeforderten Victron- oder EVCS-Aktor. Die Aktoren und die Regelung folgen mit 2b und 2c; die
    `SafetyLayer` wird bisher nur von Tests aufgerufen.
- **`src/bindaems/app`**
  - FastAPI mit SQLite (SQLAlchemy Core)
  - Login, Sitzungen, TOTP, CSRF, Rollen
  - Einstellungen mit Versionen
  - Preise (smartENERGY und Referenzquelle, Brutto/Netto-Erkennung)
  - PV-Prognose (Open-Meteo), Abrechnung, Verbraucher, Verlauf aus InfluxDB
  - HA MQTT Discovery
  - liefert das UI unter `/` aus
- **`src/bindaems/shared`:** Konfiguration, Domänentypen (auch `control.py`: Befehle, Betriebsarten,
  Absicht), Influx-Client.
  **`src/bindaems/tools`:** Prüfprotokoll Teil 1, `watchdog_config` und `watchdog_probe`.
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
    insgesamt, ≥ 90 % für `core/safety` und `core/control`) und den Importvertrag, der die
    Safety-Schicht rein hält.
  - Veröffentlicht wird von `main` und von Tags.

## Befehle

In dieser Cloud-Umgebung brauchen alle `uv`-Aufrufe vorher:
`export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.

```bash
# Python: Format, Lint, Typen (auch das Watchdog-Paket), Importgrenzen, Tests mit Abdeckung
uv run ruff format . && uv run ruff check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports
uv run pytest -q --cov --cov-report=term --cov-fail-under=75
uv run coverage report --include="src/bindaems/core/safety/*,src/bindaems/core/control/*" --fail-under=90

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
- **Standard-Branch:** Auf GitHub ist das `claude/ems-haus-akku-pv-93c0s6`, nicht `main`. Die CI
  hängt `latest` deshalb ausdrücklich an `refs/heads/main`.
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
  `core.adapters`, `core.api`, `core.runtime`, `core.telemetry`, `core.accounting` und
  `core.checks` importieren, auch nicht über Umwege (import-linter-Vertrag). Zeit kommt als Argument,
  Dateien nur über `core.persistence.files`.

## Stand der Installation beim Betreiber

- Docker auf der VM `192.168.82.20`, Images `latest`.
- Das UI ist direkt über `http://192.168.82.20:8080` erreichbar, noch ohne HTTPS-Proxy. Die
  Anmeldung klappt deshalb nur mit `cookie_secure: false`.
- **Prüfprotokolle vom 10.10.2026** in `docs/verification/`: Preise bestanden (smartENERGY
  brutto). Teil 1 mit Befunden, siehe die Bewertung am Ende des Protokolls:
  - Dynamic ESS bleibt nach Entscheidung des Betreibers an, bis Victron live geschaltet wird.
  - Die EVCS (AC22NS, Produkt-ID 0xC026) liest der Cerbo per Modbus TCP unter
    192.168.81.41. BindaEMS bekam unter derselben Adresse nur Nullen. Ursache war die
    IP-Whitelist der EVCS: Die Adresse der VM stand nicht darauf. Die EVCS nimmt die Verbindung
    dann trotzdem an und liefert nur Nullen. Der Betreiber hat die Whitelist am 11.10.2026
    korrigiert; seitdem antwortet die EVCS BindaEMS. Teil 1 muss das noch im Protokoll belegen.
  - Seit dem 10.10.2026 folgt die Registerbelegung Victrons GX-Treiber: Sollstrom 5016,
    Maximalstrom 5017. Ohne bekannte Produkt-ID gelten die Registerwerte als ungültig; Strom
    und Modus kommen dann vom Cerbo.
  - Peak Shaving: Laut Betreiber steht das Importlimit knapp unter der 35-A-Sicherung (etwa
    33 A); das Protokoll vom 10.10. las noch 20 A.
  - Akku 96 kWh nutzbar.
- **Watchdog:** noch nicht installiert. Bis dahin zeigt das UI den Watchdog als „keine Antwort“,
  ohne Alarm, solange kein Aktor live angefordert ist.
- **HA-Daten in InfluxDB:** Datenbank `homeassistant`, `bindaems` hat READ. HA schreibt mit
  `measurement_attr: entity_id`:
  - Messung = Entität (`sensor.…`), Tags `domain` und `entity_id` (ohne Domain)
  - Felder `value`, `unit_of_measurement_str`, `device_class_str`, `state_class_str`,
    `friendly_name_str`
  - Wichtig auch für die Lastprognose aus der HA-Historie.
- **Als Nächstes an der Anlage:**
  1. **Images von Plan 2a ausrollen**, sobald 2a auf `main` ist, core und app aus demselben Stand:
     `docker compose pull && docker compose up -d`. Ohne sie fehlen die Werkzeuge
     `bindaems.tools.watchdog_config` und `bindaems.tools.watchdog_probe`, und die System-Seite
     zeigt den Status des Watchdogs nicht.
  2. Reverse Proxy mit HTTPS einrichten (Betriebshandbuch 6). Port 8080 soll nur der Proxy
     erreichen. `app.trusted_proxies` setzen, `cookie_secure` wieder auf `true`.
  3. InfluxDB einrichten (`deploy/influxdb-setup.sh`), den ersten Admin anlegen und TOTP
     einrichten.
  4. Prüfprotokoll Teil 1 wiederholen, jetzt mit erreichbarer EVCS: Produkt-ID, Firmware und
     Modus müssen aus den eigenen Registern kommen. Den Bericht nach `docs/verification/`
     übernehmen und `config.yaml` anhand der Abweichungen korrigieren. „Preise“ ist bestanden
     (10.10.2026).
  5. Tarifbestandteile und den OeMAG-Monatswert im UI eintragen (Einstellungen).
  6. **Watchdog installieren** (Betriebshandbuch 14): `config.ini` aus `config.yaml` erzeugen und mit
     `cerbo-watchdog/install.sh` auf den Cerbo bringen. Dafür braucht es den SSH-Zugang als root
     zum Cerbo und die EVCS unter `victron.instances.evcharger` in `config.yaml`.
  7. **Probe** gemeinsam mit dem Betreiber laufen lassen (Betriebshandbuch 14) und den Bericht nach
     `docs/verification/` übernehmen. Die Auslösung setzt die EVCS auf den sicheren Strom, den
     Min-SOC auf die USV-Reserve und die Entladegrenze auf unbegrenzt, sofern sie abwichen.
     Danach alle drei prüfen und wieder einstellen und im Log des Cerbo die Zeile zum Aktionslauf
     lesen.
  8. Im UI (**System** → „Watchdog am Cerbo“) und in HA („Watchdog ausgelöst“) prüfen, dass der
     Status stimmt: „wartet“ nach der Probe.

## Nächste Schritte im Projekt

1. **Abnahme der Phase 1** nach `docs/verification/abnahme-phase-1.md`:
   - 7 Tage lückenlose Aufzeichnung (Tagesbilanz „96 von 96 · 100 %“)
   - Tagesbilanz innerhalb ±2 % von VRM
   - Preise und Prognose sichtbar
   - nachweislich keine Schreibpfade zu Geräten
2. **Phase 2**, Wallbox-Lastmanagement und PV-Überschussladen (Spec 18), in vier Plänen
   (Zuschnitt in `docs/superpowers/plans/2026-10-10-phase-2a-sicherheitsbasis.md`):
   - **2a Sicherheitsbasis:** umgesetzt und im Gesamtreview geprüft (11.10.2026); dessen Befunde
     sind behoben. Offen sind die Arbeiten an der Anlage (siehe oben).
   - **2b Aktoren:**
     - Gerüst mit `capabilities()` und `apply()`, Dry-Run und Rücklese-Prüfung für EVCS, Victron
       und Tesla
     - sicherer Zustand (Spec 8.5)
     - Entscheidungslog im core (Spool, Stream, `GET /v1/decisions`)
     - Erkennung fremder Änderungen (Spec 7.9)
     - Werkzeug für das Prüfprotokoll Teil 2, schreibend und gemeinsam mit dem Betreiber
       (Spec 17.2)
     - Watchdog-Aktionen für die hub4-Overrides
   - **2c Regelung:** Regelzyklus, Phasenwächter, 15-min-Wächter, Modi Sofort, PV, Min+PV, Aus und
     Boost, Akku-Unterstützung, Zuordnung Auto ↔ Wallbox, Degradationsstufen und der
     Anlagensimulator mit Regelkreistests in der CI.
   - **2d Bedienung:** Absicht aus der app (Regelbetrieb, Live-Schalter mit Bestätigung, Lademodi,
     Ziele, Boost, Zuordnung), Entscheidungslog in SQLite, UI-Seiten „Laden“ und „Entscheidungen“,
     HA-Bedienelemente und Benachrichtigungen. Mit 2d beginnt der Dry-Run.

   Als Nächstes folgt der Plan 2b. Er wird mit `superpowers:writing-plans` geschrieben und dem
   Betreiber zur Freigabe vorgelegt (Spec 18); umgesetzt wird erst danach. Danach kommt die
   Abnahme der Phase 2: 2 Wochen Dry-Run, dann die Live-Schaltung je Aktor in der Reihenfolge EVCS,
   Victron, Tesla.

   **EVCS in 2b:** Seit der korrigierten IP-Whitelist antwortet die EVCS BindaEMS direkt per Modbus
   (siehe oben). Ob 2b direkt per Modbus oder über den GX-Dienst `evcharger` auf die EVCS schreibt,
   legt das Prüfprotokoll Teil 2 fest (Spec 8.6). Geschrieben wird in jedem Fall der Sollstrom
   (Register 5016, auf dem GX `/SetCurrent`), nie der Maximalstrom (5017). Der Watchdog ist nicht
   betroffen: Er schreibt über den GX-Dienst `evcharger` am Cerbo.

## Vor einer Live-Schaltung in 2b zu klären

Aus dem Gesamtreview von Plan 2a. Kein Victron- oder EVCS-Aktor geht live, bevor diese Punkte
entschieden und umgesetzt sind:

- **core hängt in OBSERVE (Blocker):** Startet der core mit einer gespeicherten Live-Absicht und
  erreicht CONTROL nicht, sendet er trotzdem Heartbeats. Der Watchdog bleibt ruhig, aber niemand
  regelt, und dringende Befehle gehen in OBSERVE nicht durch (Präzisierung 4). Damit trägt auch
  die Annahme aus Plan 2a nicht, in diesem Fall greife der Watchdog (Präzisierungen 2 und 4).
  Mögliche Wege:
  - die Heartbeats einstellen, wenn CONTROL, DEGRADED oder SAFE nicht binnen N s nach dem Start
    erreicht ist,
  - Befehle für den sicheren Zustand nach einem Neustart auch in OBSERVE zulassen,
  - SAFE statt OBSERVE, wenn Gründe für SAFE vorliegen und ein Aktor live angefordert ist.
- **Bevor 2b die `SafetyLayer` verdrahtet:**
  - `base_grid_setpoint_w` durch `finite_number` prüfen.
  - Befehlsarten mit `==` vergleichen.
  - Die Darstellung von `invalid_value` (repr) in der Länge begrenzen.
  - Einen gestorbenen MQTT-Sender und eine verlorene Freigabe 0 behandeln (unten „Heartbeat
    (2b)“).
  - Die Uhr festlegen, monoton verankert (unten „Uhr (2b)“).
  - Nach `os.replace` das Verzeichnis per `fsync` sichern: `intent.json`, `write_budget.json`,
    `state.json`.
  - Den Watchdog nur als scharf werten, wenn der zuletzt gesendete Heartbeat des core
    zurückkommt, und `/TimeoutSeconds` mit `victron.watchdog.timeout_s` vergleichen.
  - Entscheiden, ob „ausgelöst“ einen Neustart des Cerbo übersteht. Heute speichert `state.json`
    nur „scharf“; ein ausgelöster Watchdog startet danach als „wartet“.

## Hinweise für die Pläne 2b bis 2d

Aus den Reviews von Plan 2a. Die Pläne 2b bis 2d gibt es noch nicht; sie sollen diese Punkte
aufgreifen:

- **SafetyLayer aufrufen (2b):**
  - `authorize` verändert Rate-Limiter und Schreibbudget. Es wird genau einmal je betrachtetem
    Befehl aufgerufen.
  - `SafetyContext.modes` muss `ActuatorMode`-Werte enthalten. Ein String „live“ bliebe still im
    Dry-Run.
  - `wallbox_phase_a` ist nach Netzphasen geschlüsselt; der Erzeuger wendet `phase_map` an.
    `present[(aktor, current)]` ist der Sollstrom, nicht der Messstrom.
  - `grid_budget_w` darf nie NaN oder ±∞ sein (Erzeuger ist der 15-min-Wächter in 2c).
- **Uhr (2b):** Rate-Limiter und Schreibbudget rechnen mit der Wanduhr.
  - Der Rate-Limiter lebt nur im Speicher: Ein Neustart vergisst das Stundenfenster des Tesla.
  - Einträge des Schreibbudgets in der Zukunft zählen. Nach einer korrigierten Uhr bleiben
    persistente Schreibzugriffe gesperrt, bis die Uhr aufholt.
  - Der Heartbeat pausiert bei einer rückspringenden Uhr.
  - Zu entscheiden: eine monoton verankerte Uhr für die Safety-Schicht, gegebenenfalls eine
    Anlaufschonfrist für den Tesla.
- **Heartbeat (2b):**
  - `VictronMqttAdapter.write` reiht nur ein. Eine Freigabe 0 kurz vor einem Verbindungsabbruch
    geht verloren, und der Watchdog löst 90 s später unnötig aus. Erwägen: „freigegeben, aber
    Watchdog weiter scharf“ sendet die 0 erneut.
  - Ein gestorbener Sender bleibt unbemerkt, und `write` liefert weiter `True`. Die Sitzung bei
    beendetem Hilfstask beenden oder `write` mit `False` antworten lassen.
  - `write` nur aus dem Event-Loop-Thread aufrufen.
- **Watchdog (2b):** Die Aktionen für die hub4-Overrides kommen dazu, sobald das Prüfprotokoll
  Teil 2 die Pfade festlegt. Die Probe soll dann auch verstellte Werte prüfen.
- **Bedienung (2d):**
  - Die Quittierung von Sperren (Spec 7.9) kommt hier; in 2a lösen sie sich nur nach
    `safety.lock_release_s`.
  - Die app nimmt für die Version der Absicht Millisekunden seit 1970.
  - Der LiveFeed der app ignoriert `mode_change`. In 2a reicht die Health-Abfrage alle 10 s.
  - `FakeView` in `tests/integration/test_core_link.py` kennt `intent()` und `submit_intent()`
    noch nicht.

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
- Ohne live angeforderten Aktor gibt es keinen Heartbeat. Ein laufender core kann den Watchdog mit
  der 0 abschalten (Präzisierung 2).
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
  verwürfe der Watchdog einen Abschnitt mit Tippfehler im Kopf (`[actions:x]`) still. Nach
  `os.replace` sichert weder der core noch der Watchdog (`state.json`) das Verzeichnis per fsync.
- `health()` des core kann Felder aus zwei Zyklen mischen.
- UI: Ein deaktivierter Watchdog, den der Betrieb braucht, zeigt nur eine Warnung (der core meldet
  einen Fehler-Alarm). Nach einem Verbindungsabbruch zum core bleiben die Karten „Aktoren“ und
  „Watchdog am Cerbo“ bis etwa 25 s ohne Hinweis stehen. Die Datei `ui/src/routes/system/+page.svelte`
  hat rund 500 Zeilen; bei weiterem Wachstum gehören die Karten in eigene Komponenten.

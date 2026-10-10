# Übergabe an die nächste Session

Stand: 10.10.2026. Neue Arbeit liegt zuerst auf dem Arbeitsbranch
`claude/ems-haus-akku-pv-93c0s6`; was davon schon auf `main` ist, zeigt `git log main`.

## Kurzfassung

- **Phase 1 ist umgesetzt:** 1a (core, lesend), 1b (app-Backend), 1c (Web-UI) samt Gesamtreview,
  Fix-Durchgang und allen zurückgestellten Kleinigkeiten.
- **HA-Verbraucher (nach Phase 1):** Die app liest beide InfluxDB-Schemata der HA-Integration
  (Messung je Einheit oder je Entität). Fehlt ein Wert, nennen Liste und Formular den Grund.
- **Images:** `ghcr.io/binda5000/bindaems-core:latest` und `ghcr.io/binda5000/bindaems-app:latest`.
  Sie sind öffentlich, und die CI setzt `latest` bei jedem Push auf `main`.
- **Tests:**
  - Python: 638 (ruff, mypy strict, import-linter)
  - UI: Vitest 183 (svelte-check, Prettier, ESLint)
  - E2E: Playwright 11
- **Offen:**
  - die Installation beim Betreiber fertigstellen (HTTPS-Proxy)
  - die Abnahme der Phase 1 an der Anlage
  - danach der Plan für Phase 2

## Arbeitsweise mit dem Betreiber

- Sprache Deutsch, Anrede „du“.
- **Ablauf je Phase (Spec 18):** Implementierungsplan, Freigabe, Umsetzung, Abnahme.
  Implementiert wird erst nach der Freigabe.
- **Werkzeuge:**
  - Pläne entstehen mit `superpowers:writing-plans` unter `docs/superpowers/plans/`.
  - Phase 1 lief inline („Native“, `superpowers:executing-plans`): Ledger mit `Ruling:`-Zeilen,
    am Ende ein frischer Gesamtreviewer.
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
| `docs/betrieb.md` | Betriebshandbuch: Installation, Proxy (6), HA (7), Tarif (8), Sicherung (9), Prüfprotokolle (11), Web-UI (12) |
| `docs/verification/abnahme-phase-1.md` | Checkliste für die Abnahme der Phase 1 |
| `docs/verification/README.md` | Prüfprotokoll Teil 1 und Preise: Aufruf und Ergebnis |
| `README.md` | Entwicklung, Web-UI, Demo-Backend |

## Aufbau

- **`src/bindaems/core`**
  - liest die Geräte (Victron über MQTT, EVCS über Modbus, Wall Connector, Tessie,
    Home Assistant)
  - Zustandsspeicher, abgeleitete Größen, Plausibilität, Flüsse je Viertelstunde
  - InfluxDB-Schreiber mit Spool, interne API mit Stream
  - In Phase 1 schreibt der core nichts an Geräte.
- **`src/bindaems/app`**
  - FastAPI mit SQLite (SQLAlchemy Core)
  - Login, Sitzungen, TOTP, CSRF, Rollen
  - Einstellungen mit Versionen
  - Preise (smartENERGY und Referenzquelle, Brutto/Netto-Erkennung)
  - PV-Prognose (Open-Meteo), Abrechnung, Verbraucher, Verlauf aus InfluxDB
  - HA MQTT Discovery
  - liefert das UI unter `/` aus
- **`src/bindaems/shared`:** Konfiguration, Domänentypen, Influx-Client.
  **`src/bindaems/tools`:** Prüfprotokoll Teil 1.
- **`ui/`**
  - SvelteKit 2 als SPA (Svelte 5 Runes), valibot-Schemas für jede Antwort, ECharts
  - Tests mit Vitest und Playwright
  - Die Vertragsdateien `ui/src/lib/api/contract/*.json` kommen aus der Demo-Welt
    (`tests/ui_world.py`).
- **`deploy/` und CI:**
  - `deploy/`: Dockerfiles, `docker-compose.yml`, `config.example.yaml`, `influxdb-setup.sh`
  - CI `.github/workflows/ci.yml` mit den Jobs `python`, `ui`, `e2e` und `docker`
  - Veröffentlicht wird von `main` und von Tags.

## Befehle

In dieser Cloud-Umgebung brauchen alle `uv`-Aufrufe vorher:
`export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.

```bash
# Python: Format, Lint, Typen, Importgrenzen, Tests
uv run ruff format . && uv run ruff check . && uv run mypy src && uv run lint-imports && uv run pytest -q

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

## Stand der Installation beim Betreiber

- Docker auf der VM `192.168.82.20`, Images `latest`.
- Das UI ist direkt über `http://192.168.82.20:8080` erreichbar, noch ohne HTTPS-Proxy. Die
  Anmeldung klappt deshalb nur mit `cookie_secure: false`.
- **Prüfprotokolle vom 10.10.2026** in `docs/verification/`: Preise bestanden (smartENERGY
  brutto). Teil 1 mit Befunden, siehe die Bewertung am Ende des Protokolls:
  - Dynamic ESS bleibt nach Entscheidung des Betreibers an, bis Phase 2 live schaltet.
  - Die EVCS (AC22NS, Produkt-ID 0xC026) liest der Cerbo per Modbus TCP unter
    192.168.81.41. BindaEMS bekam unter derselben Adresse nur Nullen. `wallboxes.evcs.host`
    stand laut Betreiber schon richtig; die Ursache ist offen und wird vor 2b geklärt.
  - Peak Shaving: Laut Betreiber steht das Importlimit knapp unter der 35-A-Sicherung (etwa
    33 A); das Protokoll vom 10.10. las noch 20 A.
  - Akku 96 kWh nutzbar.
- **HA-Daten in InfluxDB:** Datenbank `homeassistant`, `bindaems` hat READ. HA schreibt mit
  `measurement_attr: entity_id`:
  - Messung = Entität (`sensor.…`), Tags `domain` und `entity_id` (ohne Domain)
  - Felder `value`, `unit_of_measurement_str`, `device_class_str`, `state_class_str`,
    `friendly_name_str`
  - Wichtig auch für die Lastprognose aus der HA-Historie.
- **Als Nächstes an der Anlage:**
  1. Reverse Proxy mit HTTPS einrichten (Betriebshandbuch 6). Port 8080 soll nur der Proxy
     erreichen. `app.trusted_proxies` setzen, `cookie_secure` wieder auf `true`.
  2. InfluxDB einrichten (`deploy/influxdb-setup.sh`), den ersten Admin anlegen und TOTP
     einrichten.
  3. Die Prüfprotokolle Teil 1 und „Preise“ laufen lassen, nach `docs/verification/` übernehmen
     und `config.yaml` anhand der Abweichungen korrigieren.
  4. Tarifbestandteile und den OeMAG-Monatswert im UI eintragen (Einstellungen).

## Nächste Schritte im Projekt

1. **Abnahme der Phase 1** nach `docs/verification/abnahme-phase-1.md`:
   - 7 Tage lückenlose Aufzeichnung (Tagesbilanz „96 von 96 · 100 %“)
   - Tagesbilanz innerhalb ±2 % von VRM
   - Preise und Prognose sichtbar
   - nachweislich keine Schreibpfade zu Geräten
2. **Plan Phase 2**, Wallbox-Lastmanagement und PV-Überschussladen (Spec 18):
   - Cerbo-Watchdog
   - Safety-Schicht mit Property-Tests
   - Aktoren mit Dry-Run und Rücklese-Prüfung
   - Entscheidungslog
   - Prüfprotokoll Teil 2, schreibend und gemeinsam mit dem Betreiber (Spec 17.2)
   - Regelung mit den Modi Sofort, PV, Min+PV, Aus und Boost
   - HA-Bedienelemente
   - Anlagensimulator in der CI

   Zuschnitt in die Pläne 2a–2d und der Plan 2a (Sicherheitsbasis) liegen zur Freigabe vor:
   `docs/superpowers/plans/2026-10-10-phase-2a-sicherheitsbasis.md`. Umgesetzt wird erst nach
   der Freigabe durch den Betreiber.

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

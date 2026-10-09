# Phase 1b – ems-app-Backend: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein lauffähiger Container `ems-app`, der sich mit dem core verbindet, Benutzer sicher anmeldet, Laufzeit-Einstellungen versioniert verwaltet, Verbraucher mit „Sonstiges“ abbildet, Preise (smartENERGY mit EPEX-Referenz, Brutto/Netto-Erkennung, Plausibilität, Ersatzquelle) und eine PV-Prognose bereitstellt, jede Viertelstunde abrechnet, Home Assistant per MQTT Discovery lesende Sensoren liefert und eine REST-/WebSocket-API für das Web-UI (Plan 1c) anbietet – ohne jeden Schreibzugriff auf Geräte.

**Architecture:** Zweiter Python-asyncio-Prozess neben dem core. FastAPI liefert REST und WebSocket. Dienste mit Datenbankzugriff sind synchron (SQLAlchemy Core auf SQLite, Migrationen mit Alembic); Endpunkte, die sie nutzen, sind `def` (Threadpool), asynchroner Code ruft sie mit `asyncio.to_thread` auf. Hintergrundaufgaben: Stream vom core (Zustand, Alarme, Viertelstunden-Flüsse), Preisabruf, PV-Prognose, Abrechnungs-Nachtrag aus InfluxDB, HA-Publisher, Pflege (Sitzungen, Sicherung). Bausteine, die beide Prozesse brauchen (InfluxDB-Schreiber mit Spool, Logging, Backoff, Broadcast, TLS), wandern nach `shared`.

**Tech Stack:** Python 3.12, FastAPI + uvicorn, SQLAlchemy 2 (Core) + Alembic, argon2-cffi, pyotp, httpx, websockets, aiomqtt, pydantic v2, PyYAML, structlog; Tests mit pytest, pytest-asyncio, respx, hypothesis; ruff, mypy (strict), import-linter; Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`. Ausführende lesen Spec und Plan zusammen; Verweise wie „Spec 6.7“ beziehen sich darauf. Vorgänger: `docs/superpowers/plans/2026-10-08-phase-1a-fundament-datenerfassung.md` (Plan 1a, umgesetzt und gemergt).

## Einordnung

| Plan | Inhalt | Stand |
|---|---|---|
| 1a | Fundament und Datenerfassung (ems-core, nur lesend) | fertig |
| **1b** (dieser Plan) | ems-app-Backend: Login und Rollen, Einstellungen, Verbraucher, Preise und Tarif v1, PV-Prognose, Abrechnung v1, HA-Sensoren, API für das UI | – |
| 1c | Web-UI: Login, Dashboard, System, Verbraucher, Verlauf, Einstellungen (lesend) | – |

Die Abnahme der Phase 1 (Spec 18) folgt nach 1c.

## Präzisierungen gegenüber der Spec

1. **smartENERGY liefert Stundenmittel, brutto.** Aufgezeichnet am 2026-10-09 (`tests/fixtures/smartenergy_2026-10-09.json`): `interval` ist 15, aber je Stunde sind die vier Werte gleich und betragen genau das 1,2-Fache des Stundenmittels der EPEX-AT-15-min-Preise (Energy-Charts) bzw. des aWATTar-Stundenpreises. Die Brutto/Netto-Erkennung (Spec 6.7) vergleicht deshalb **Stundenmittel**: je Stunde, in der beide Quellen alle vier Slots haben und |Referenz-Stundenmittel| > 2 ct ist, das Verhältnis der Mittel; davon der Median, sofern diese Stunden zusammen mindestens 24 Slots umfassen. Ein Vergleich einzelner Slots streute an diesem Tag zwischen 0,87 und 1,83.
2. **Ersatzquelle „Referenz + Aufschlag“:** Der Referenz-Spotpreis (netto) ersetzt den Börsenpreis-Bestandteil. Der Lieferantenaufschlag bleibt ein eigener Tarifbestandteil und wird nicht doppelt gezählt.
3. **Referenzquellen in 1b:** Energy-Charts (Standard) und aWATTar; die Auswahl ist eine Laufzeit-Einstellung. ENTSO-E (Token, XML) folgt bei Bedarf in einem späteren Plan.
4. **Tarifmodell v1:** nur Energiebestandteile (`energy_per_kwh`) mit Zeitfenstern, Gültigkeit und USt. Leistungspreis und Grundgebühren folgen in Phase 3. Der OeMAG-Wert ist eine eigene Einstellung `feed_in.monthly_ct` („JJJJ-MM“ → ct/kWh); es gilt der jüngste Monat, der nicht nach dem Slot liegt. Ohne Eintrag ist der Einspeisepreis unbekannt. Für Netzentgelte und Abgaben gibt es keine Vorgabewerte (Spec 9.1); fehlende Werte zählen als 0 und erscheinen als Warnung.
5. **Schätzpreise** (Spec 9.2) entstehen erst mit dem Planer in Phase 3.
6. **PV-Prognose:** nur P50 (ohne Kalibrierung gibt es keine Quantile). Open-Meteo liefert `minutely_15` überall; wo kein natives 15-min-Modell rechnet, interpoliert Open-Meteo selbst aus Stundenwerten. Der Wert zum Zeitpunkt T ist das Mittel über [T − 15 min, T) und gehört damit zum Slot, der bei T − 15 min beginnt. Die Prognose wird nicht in SQLite gespeichert; nach einem Neustart wird sie sofort neu abgerufen.
7. **Verbraucher-Quellen:** ein core-Signal (z. B. der Victron-Subzähler `load.obergeschoss.power_w` oder per `config.yaml` gemappte HA-Entitäten) oder eine HA-Entität, gelesen aus der HA-Datenbank in InfluxDB (`influxdb.ha_database`, Werte höchstens 24 h alt). Die app baut dafür keine eigene HA-Verbindung auf.
8. **Abrechnung v1:** Energien je Fluss, Bezugskosten mit dem Preis zum Slotzeitpunkt, Einspeiseerlös beim Lesen (der OeMAG-Wert steht erst nach Monatsende fest), Kennzahlen, Ersparnis (zwei Varianten) und Tageswerte der Zähler für den Abgleich mit VRM. Einstandspreis des Akkus, Kosten je Auto, Abgleich mit Markierung und Lückenschätzung folgen in Phase 4. „Ist-Kosten“ = Bezugskosten − Einspeiseerlös.
9. **InfluxDB:** Die app schreibt `price`, `forecast` und `ledger` direkt in die Retention Policy `long` (wenige Punkte, unbegrenzt aufbewahrt).
10. **HA-Entitäts-IDs** entstehen aus Gerätename und Entitätsname (`has_entity_name: true`); `object_id` wird nicht gesetzt. Unbekannte Werte werden als `None` veröffentlicht (HA setzt dann „unbekannt“).
11. **Sitzungen:** Im Cookie steht nur ein zufälliges Token (256 bit), in SQLite nur dessen SHA-256. Ein eigener Sitzungsschlüssel (Spec 11.5) ist dafür nicht nötig. Das CSRF-Cookie ist absichtlich nicht HttpOnly, weil das UI es lesen muss. `app.cookie_secure: false` erlaubt im Ausnahmefall reines HTTP im LAN; Standard ist `true`.
12. **Compose:** Der Reverse Proxy läuft außerhalb dieser VM. `ems-app` veröffentlicht deshalb Port 8080 auf der VM; `app.trusted_proxies` nennt die Adresse des Proxys. Läuft der Proxy doch als Container auf derselben VM, ersetzt ein gemeinsames Docker-Netz die Portfreigabe (Doku).
13. **Rolle „Bedienen“ (`operator`)** hat in 1b keine zusätzlichen Rechte gegenüber „Lesen“, weil es noch keine Bedienfunktionen gibt.

## Global Constraints

- **Paket und Werkzeuge:** Python ≥ 3.12, Paket `bindaems` unter `src/bindaems`, uv mit `uv.lock`. Extras `core`, `app`, `dev`. `httpx` wird Basisabhängigkeit (Task 1). mypy `strict` für ganz `src`.
- **Importgrenzen:** `bindaems.core` und `bindaems.app` importieren nur aus `bindaems.shared`, nie voneinander; `bindaems.shared` importiert keines von beiden. Tests dürfen alles importieren.
- **Zeit:** intern zeitzonenbewusst in UTC, naive `datetime` sind Fehler. Slots dauern 15 min. Tagesgrenzen, Zeitfenster der Tarife und Anzeige rechnen in Europe/Vienna (`LOCAL_TZ`); Tage haben 92, 96 oder 100 Slots (`local_day_slots`).
- **Keine Gerätezugriffe.** Die app spricht nur mit:
  - core: `GET /v1/health` und `WS /v1/stream` (Bearer-Token im Header, nie in der URL); `PUT /v1/intent` gibt es in 1b nicht
  - InfluxDB: `/query` (lesen) und `/write` (nur `price`, `forecast`, `ledger` in RP `long`)
  - Internet, nur `GET`: smartENERGY, Energy-Charts, aWATTar, Open-Meteo
  - Mosquitto: publizieren nur auf `<discovery_prefix>/(sensor|binary_sensor)/bindaems/<objekt>/config` und `<base_topic>/(status|state/<objekt>[/attributes])`, abonnieren nur `<discovery_prefix>/status`
- **Login und Sitzungen (Spec 14):**
  - Rollen `admin` (alles), `operator` („Bedienen“), `viewer` („Lesen“); keine Selbstregistrierung, erster Admin per CLI.
  - Passwörter Argon2id, mindestens 10 Zeichen.
  - Sitzungen serverseitig in SQLite; Cookie `bindaems_session` mit HttpOnly, Secure, SameSite=Strict.
  - Admin: Ablauf nach 30 min Leerlauf. Bedienen/Lesen: „angemeldet bleiben“ bis 30 Tage, sonst ebenfalls 30 min Leerlauf.
  - CSRF per Double-Submit: Cookie `bindaems_csrf`, Header `X-CSRF-Token` bei POST/PUT/PATCH/DELETE.
  - Sperre für 15 min nach 5 Fehlversuchen innerhalb von 15 min. TOTP optional.
  - Security-Header inklusive CSP. `X-Forwarded-*` nur von `app.trusted_proxies`.
  - Jede Änderung (Benutzer, Einstellungen, Verbraucher, Neubewertung) landet im Änderungsprotokoll `audit_log`; Passwörter und TOTP-Geheimnisse nie.
- **Preise (Spec 6.7, 9.2):**
  - Hauptquelle `GET https://apis.smartenergy.at/market/v1/price`; ausgewertet werden `tariff`, `unit`, `interval`, `data[{date, value}]`. Rohantworten werden archiviert.
  - Abruf stündlich; zwischen 12:30 und 16:00 Uhr alle 15 min, bis der Folgetag vollständig ist.
  - Verdächtig ist ein Tag, wenn ein Wert außerhalb −50…+100 ct/kWh netto liegt, Slots fehlen oder doppelt sind (Zeitumstellung beachtet) oder alle Werte gleich sind.
  - Brutto/Netto `auto`: ≈ 1,0 netto, ≈ 1,2 brutto, Toleranz ±0,05, mindestens 24 Slots, nur |Referenz| > 2 ct (Präzisierung 1); sonst Warnung und Ersatzeinstellung.
  - Verdächtig und Referenz verfügbar → Ersatzquelle, markiert als `fallback`.
- **Tarif (Spec 9.1, 3.9):** USt 20 %; Lieferantenaufschlag 1,2 ct netto; SNAP Faktor 0,8 vom 1. 4. bis 30. 9., 10–16 Uhr auf das Netznutzungsentgelt (Arbeitspreis); Fixtarif-Vergleich 30 ct/kWh brutto all-in; OeMAG nie negativ. Bezugspreis je Slot = Summe der Energiebestandteile × (1 + USt, wo sie gilt).
- **PV (Spec 6.8, 10.1):** `P = kWp · GTI/1000 · PR · (1 + γ·(T_zelle − 25 °C))`, `T_zelle = T_luft + GTI · (NOCT − 20)/800`; PR 0,85, γ −0,35 %/K, NOCT 45 °C; Summe über die Flächen, begrenzt auf `pv.inverter_ac_max_w`. Abruf stündlich. Azimut 0° = Süd, −90° = Ost, 90° = West.
- **Abrechnung (Spec 11.4):** Autarkie = 1 − Bezug ÷ Verbrauch; Eigenverbrauchsquote = (PV − Einspeisung) ÷ PV; Ersparnis „ohne Anlage“ = Verbrauch × Fixpreis − Ist-Kosten; „gleicher Netzbezug zum Fixpreis“ = Bezug × Fixpreis − Einspeiseerlös − Ist-Kosten.
- **InfluxDB-Messungen der app (Spec 11.2):** `price` (Tag `kind` ∈ `spot_raw`, `import_gross`, `reference`, `feed_in`; Feld `ct_kwh`), `forecast` (Tags `kind=pv`, `quantile=p50`; Feld `kw`), `ledger` (Tag `flow`; Felder `kwh`, `eur`).
- **HA (Spec 12):** Discovery `homeassistant/<komponente>/bindaems/<objekt>/config`, Zustände retained unter `bindaems/state/<objekt>`, Verfügbarkeit (Last Will) `bindaems/status`; bei `homeassistant/status` = `online` alles neu veröffentlichen. Geräte „BindaEMS“, „BindaEMS <Fahrzeug>“, „BindaEMS EVCS“, „BindaEMS Wall Connector“.
- **SQLite (Spec 11.3):** Datei `<app.data_dir>/bindaems.sqlite3`, WAL, Fremdschlüssel an, Migrationen mit Alembic (von Hand geschrieben, Dateinamen `vNNNN_<name>.py`). Nächtliche Online-Sicherung nach `app.backup_dir`, 14 Generationen.
- **Logs:** structlog als JSON auf stdout (`configure_logging`).
- **Sprache:** Nutzertexte (API-Fehler, Warnungen, Prüfprotokoll, HA-Namen) auf Deutsch, exakt wie im Plan angegeben; Code und Bezeichner auf Englisch.
- **Secrets** nur aus `BINDAEMS_*`; neu ist `BINDAEMS_HA_MQTT_PASSWORD`. Die app braucht `BINDAEMS_INTERNAL_TOKEN` für den core.
- **Konfiguration:** ungültige `config.yaml` oder Secrets → deutsche Meldung auf stderr, Exitcode 2.
- **Fehlerformat der API:** `{"detail": "<deutscher Text>"}`; Validierungsfehler bleiben im FastAPI-Format (422).
- **Zeitstempel in JSON:** ISO 8601 in UTC wie `datetime.isoformat()` (`2026-10-09T08:00:00+00:00`), Kalendertage als `JJJJ-MM-TT`.
- **Hintergrundaufgaben** fangen Fehler ab, loggen sie und laufen weiter; ein Fehler in einer Komponente beendet die app nicht.

## Review Focus

Die fünf Fälle, die am ehesten Probleme machen, jeweils mit dem Task, der den Test dafür enthält:

1. **smartENERGY liefert einen defekten Tag** (durchgehend 0 ct wie im September 2025, fehlende oder doppelte Slots, Wechsel brutto/netto) oder ist nicht erreichbar: Der Tag läuft über die Referenz als Ersatzquelle; gültige Primärwerte werden nie überschrieben; „Preise fehlen“ wird sichtbar. → Task 12 (`test_all_equal_day_is_suspicious`, `test_detect_vat_undetermined_ratio`), Task 13 (`test_all_zero_day_falls_back_to_reference`, `test_fallback_never_overwrites_primary`).
2. **Zeitumstellung** (2026-10-25 mit 100 Slots, 2027-03-28 mit 92): Preisprüfung, SNAP und Gültigkeit in Ortszeit, Tagessummen von Prognose und Abrechnung. → Task 10 (`test_validity_dates_use_local_date`), Task 12 (`test_autumn_dst_day_needs_100_slots`, `test_spring_dst_day_needs_92_slots`), Task 15 (`test_day_energy_on_dst_day_uses_local_slots`), Task 16 (`test_day_summary_on_dst_day_expects_100_slots`).
3. **core nicht erreichbar oder neu gestartet:** Die app läuft weiter, markiert den Zustand als veraltet, meldet das dem UI, verbindet sich mit Backoff neu und trägt verpasste Viertelstunden aus InfluxDB nach. → Task 7 (`test_disconnect_marks_state_stale_and_notifies`, `test_reconnect_uses_backoff`), Task 16 (`test_backfill_fills_slots_missed_while_app_was_down`).
4. **Home Assistant oder Mosquitto starten neu:** Discovery und Zustände werden nach der Birth-Message und nach jeder Wiederverbindung vollständig neu veröffentlicht; der Last Will meldet `offline`. → Task 17 (`test_ha_birth_message_republishes_everything`, `test_reconnect_after_error_republishes`, `test_last_will_is_offline`).
5. **Gespeicherte Einstellungen passen nach einem Update nicht mehr zum Schema, oder zwei Admins speichern gleichzeitig:** Die app startet mit Standardwerten und einer Warnung, ohne Daten zu verlieren; die zweite Änderung erhält 409. → Task 6 (`test_invalid_stored_settings_fall_back_to_defaults_with_warning`, `test_concurrent_update_conflicts`).

---

## Dateistruktur nach Phase 1b

```
src/bindaems/shared/{config,domain,timeutil,settings,logging,broadcast,retry,mqtt}.py
src/bindaems/shared/influx/{__init__,lineprotocol,spool,writer}.py
src/bindaems/core/…                                   (wie 1a, Importe auf shared umgestellt)
src/bindaems/app/{__init__,__main__,cli,runtime,healthcheck,backup,schedule,status,fetch,audit,core_link}.py
src/bindaems/app/db/{__init__,engine,schema}.py
src/bindaems/app/db/migrations/env.py
src/bindaems/app/db/migrations/versions/v0001_audit.py … v0006_ledger.py
src/bindaems/app/api/{__init__,server,live}.py
src/bindaems/app/auth/{__init__,service,web,routes}.py
src/bindaems/app/settings/{__init__,service,routes}.py
src/bindaems/app/history/{__init__,influx,series,routes}.py
src/bindaems/app/consumers/{__init__,service,values,routes}.py
src/bindaems/app/tariff/{__init__,calc}.py
src/bindaems/app/prices/{__init__,sources,checks,store,pipeline,service,scheduler,routes,verify}.py
src/bindaems/app/forecast/{__init__,openmeteo,pv_model,service,routes}.py
src/bindaems/app/ledger/{__init__,service,routes}.py
src/bindaems/app/ha/{__init__,entities,publisher}.py
deploy/Dockerfile.app  (neu) · deploy/{docker-compose.yml,config.example.yaml,.env.example}  (erweitert)
tests/app_helpers.py · tests/unit/app/… · tests/unit/shared/… · tests/integration/{test_core_link,test_app_runtime}.py
tests/property/test_consumer_tree.py
tests/fixtures/{smartenergy,energycharts_at,awattar,openmeteo_sued}_2026-10-09.json   (aufgezeichnet, liegen bei)
```

Testbefehle laufen im Repository-Wurzelverzeichnis. „Alle Prüfungen“ heißt:
`uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run lint-imports`.

---

### Task 1: Gemeinsame Bausteine nach `shared` verschieben

**Files:**
- Move (`git mv`, Verhalten unverändert):
  - `src/bindaems/core/telemetry/lineprotocol.py` → `src/bindaems/shared/influx/lineprotocol.py`
  - `src/bindaems/core/telemetry/spool.py` → `src/bindaems/shared/influx/spool.py`
  - `src/bindaems/core/telemetry/influx.py` → `src/bindaems/shared/influx/writer.py`
  - `src/bindaems/core/logging.py` → `src/bindaems/shared/logging.py`
  - `src/bindaems/core/broadcast.py` → `src/bindaems/shared/broadcast.py`
  - Tests: `tests/unit/core/telemetry/test_lineprotocol.py` und `test_spool.py` → `tests/unit/shared/influx/`; `test_influx_writer.py` → `tests/unit/shared/influx/test_writer.py`; die Fixture `writer_env` → `tests/unit/shared/influx/conftest.py`; `tests/unit/core/test_broadcast.py` → `tests/unit/shared/test_broadcast.py`
- Create: `src/bindaems/shared/influx/__init__.py`, `src/bindaems/shared/retry.py`, `src/bindaems/shared/mqtt.py`, `tests/unit/shared/test_retry.py`, `tests/unit/shared/test_mqtt.py`, `tests/unit/test_layout.py`
- Modify: `src/bindaems/core/adapters/base.py`, `src/bindaems/core/adapters/victron_mqtt.py`, `src/bindaems/core/telemetry/sampler.py`, alle betroffenen Importe in `src/` und `tests/` (u. a. `core/runtime.py`, `core/__main__.py`, `tests/helpers.py`, `tests/integration/test_runtime.py`); `tests/unit/core/adapters/test_base.py` und `test_victron_mqtt.py` (verschobene Tests entfernen); `pyproject.toml` (`httpx>=0.27` aus `core` in `dependencies`); `uv.lock`

**Interfaces:**
- Consumes: Code aus Plan 1a
- Produces (Namen und Verhalten wie bisher, neue Orte):
  - `bindaems.shared.influx.lineprotocol`: `Point`, `FieldValue`, `to_line`, `epoch_ms` und neu hier `class PointSink(Protocol)` mit `write(point: Point) -> None` (bisher in `core.telemetry.sampler`)
  - `bindaems.shared.influx.spool`: `DiskSpool`, `SpoolFile`
  - `bindaems.shared.influx.writer`: `InfluxWriter`, `WriterStats`
  - `bindaems.shared.logging`: `configure_logging(level: str = "INFO") -> None`
  - `bindaems.shared.broadcast`: `Broadcast`, `Message`
  - `bindaems.shared.retry`: `Backoff`, `StatusLog` (Logger-Name `bindaems.shared.retry`, Texte unverändert)
  - `bindaems.shared.mqtt`: `class TlsSettings(Protocol)` mit den Nur-Lese-Eigenschaften `tls: bool`, `tls_verify: bool`, `tls_ca_file: Path | None`; `build_tls_context(cfg: TlsSettings) -> ssl.SSLContext | None` (Verhalten wie bisher)

- [ ] **Step 1: Write the failing tests**

`tests/unit/test_layout.py`:

```python
MOVED = {
    "bindaems.core.telemetry.lineprotocol": "bindaems.shared.influx.lineprotocol",
    "bindaems.core.telemetry.spool": "bindaems.shared.influx.spool",
    "bindaems.core.telemetry.influx": "bindaems.shared.influx.writer",
    "bindaems.core.logging": "bindaems.shared.logging",
    "bindaems.core.broadcast": "bindaems.shared.broadcast",
}

@pytest.mark.parametrize(("old", "new"), MOVED.items())
def test_shared_building_blocks_moved(old: str, new: str) -> None:
    assert importlib.util.find_spec(old) is None
    assert importlib.util.find_spec(new) is not None

def test_retry_tls_and_sink_are_defined_in_shared() -> None:
    from bindaems.shared.influx.lineprotocol import PointSink
    from bindaems.shared.mqtt import build_tls_context
    from bindaems.shared.retry import Backoff, StatusLog

    assert Backoff.__module__ == StatusLog.__module__ == "bindaems.shared.retry"
    assert build_tls_context.__module__ == "bindaems.shared.mqtt"
    assert PointSink.__module__ == "bindaems.shared.influx.lineprotocol"
```

Die verschobenen Testdateien per `git mv` an den neuen Ort legen und ihre Importe auf die neuen Module umstellen. Die Tests für `Backoff`/`StatusLog` aus `test_base.py` nach `test_retry.py`, die für `build_tls_context` aus `test_victron_mqtt.py` nach `test_mqtt.py` übernehmen (dort weiter mit `MqttConfig` aufgerufen).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_layout.py tests/unit/shared -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.shared.influx'` u. a.)

- [ ] **Step 3: Move the modules**

`git mv` der Quellmodule, `PointSink` nach `lineprotocol.py`, `Backoff`/`StatusLog` nach `retry.py`, `build_tls_context` mit Parameter `TlsSettings` nach `mqtt.py`; alle Importe umstellen; `httpx` in die Basisabhängigkeiten; `uv lock`.

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen (siehe oben)
Expected: alles grün; Testanzahl = Stand vor dem Task + 6 (`test_layout.py`)

- [ ] **Step 5: Commit**

```bash
git add -A src tests pyproject.toml uv.lock
git commit -m "refactor: InfluxDB-Schreiber, Logging, Backoff, Broadcast und TLS nach shared"
```

---

### Task 2: Konfiguration und Secrets für die app

**Files:**
- Modify: `src/bindaems/shared/config.py`, `deploy/config.example.yaml`, `deploy/.env.example`
- Test: `tests/unit/shared/test_config.py`

**Interfaces:**
- Consumes: `_Model`, `Config`, `Secrets`, `HomeAssistantConfig` (Plan 1a)
- Produces:
  - `class AppConfig(_Model)`: `host: str = "0.0.0.0"`, `port: int = 8080` (1–65535), `data_dir: Path = Path("/data")`, `backup_dir: Path = Path("/backup")`, `core_url: str = "http://ems-core:8081"` (Muster `^https?://`), `trusted_proxies: list[str] = []` (jeder Eintrag muss `ipaddress.ip_network(value, strict=False)` bestehen, sonst Fehler „keine gültige IP-Adresse oder kein gültiges Netz: {value}“), `cookie_secure: bool = True`, `ui_dir: Path | None = None`
  - `class HaMqttConfig(_Model)`: `host: str`, `port: int = 1883`, `tls: bool = False`, `tls_verify: bool = True`, `tls_ca_file: Path | None = None`, `username: str | None = "bindaems"`, `discovery_prefix: str = "homeassistant"` und `base_topic: str = "bindaems"` (beide Muster `^[a-z0-9_]+$`), `publish_interval_s: float = 10.0` (> 0). Erfüllt `TlsSettings` (Task 1).
  - `HomeAssistantConfig.mqtt: HaMqttConfig | None = None`; `Config.app: AppConfig = Field(default_factory=AppConfig)`
  - `Secrets.ha_mqtt_password: SecretStr | None = None` (`BINDAEMS_HA_MQTT_PASSWORD`)

- [ ] **Step 1: Write the failing tests** (in `test_config.py`, mit dem vorhandenen Helfer `write_cfg`)

```python
def test_app_and_ha_mqtt_from_example() -> None:
    cfg = load_config(EXAMPLE)
    assert (cfg.app.port, cfg.app.core_url) == (8080, "http://ems-core:8081")
    assert (cfg.app.data_dir, cfg.app.backup_dir) == (Path("/data"), Path("/backup"))
    assert cfg.app.trusted_proxies == ["192.168.1.10"]
    assert cfg.app.cookie_secure is True and cfg.app.ui_dir is None
    mqtt = cfg.homeassistant.mqtt
    assert (mqtt.host, mqtt.port, mqtt.tls, mqtt.username) == ("homeassistant.lan", 1883, False, "bindaems")
    assert (mqtt.discovery_prefix, mqtt.base_topic, mqtt.publish_interval_s) == ("homeassistant", "bindaems", 10.0)

def test_app_section_is_optional(tmp_path: Path) -> None:
    cfg = load_config(write_cfg(tmp_path, lambda d: d.pop("app")))
    assert cfg.app.port == 8080 and cfg.app.trusted_proxies == []

@pytest.mark.parametrize("value", ["proxy.lan", "300.1.1.1", "10.0.0.0/33"])
def test_trusted_proxies_must_be_ip_or_network(tmp_path: Path, value: str) -> None:
    p = write_cfg(tmp_path, lambda d: d["app"].update(trusted_proxies=[value]))
    with pytest.raises(ConfigError, match="trusted_proxies"):
        load_config(p)

def test_trusted_proxies_accept_addresses_and_networks(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["app"].update(trusted_proxies=["10.0.0.0/8", "fd00::1"]))
    assert load_config(p).app.trusted_proxies == ["10.0.0.0/8", "fd00::1"]

def test_ha_mqtt_topics_must_be_plain_names(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["homeassistant"]["mqtt"].update(base_topic="bindaems/#"))
    with pytest.raises(ConfigError, match="base_topic"):
        load_config(p)

def test_ha_mqtt_password_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINDAEMS_INTERNAL_TOKEN", "x" * 32)
    monkeypatch.setenv("BINDAEMS_HA_MQTT_PASSWORD", "mqtt-geheim")
    assert load_secrets().ha_mqtt_password.get_secret_value() == "mqtt-geheim"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/shared/test_config.py -q`
Expected: FAIL (`AttributeError: 'Config' object has no attribute 'app'` u. a.)

- [ ] **Step 3: Implement the schema and extend the examples**

`config.example.yaml`: unter `homeassistant` ein Block `mqtt: {host: homeassistant.lan, port: 1883, username: bindaems}` mit Kommentar „MQTT Discovery am bestehenden Mosquitto (eigener Benutzer bindaems)“; neuer Abschnitt `app` mit `port: 8080`, `core_url: http://ems-core:8081`, `trusted_proxies: [192.168.1.10]` (Kommentar „IP-Adresse des Reverse Proxy“) und `cookie_secure: true`. `.env.example`: Zeile `BINDAEMS_HA_MQTT_PASSWORD=`.

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (8 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared/config.py deploy/config.example.yaml deploy/.env.example tests/unit/shared/test_config.py
git commit -m "feat(shared): Konfiguration für ems-app und HA-MQTT"
```

---

### Task 3: App-Grundgerüst: Datenbank, Migrationen, Änderungsprotokoll, HTTP-Grundlage

**Files:**
- Modify: `pyproject.toml` (Extra `app`), `uv.lock`, `.github/workflows/ci.yml` (`uv sync --locked --extra core --extra app --extra dev`)
- Create: `src/bindaems/app/db/{__init__,engine,schema}.py`, `src/bindaems/app/db/migrations/env.py`, `src/bindaems/app/db/migrations/versions/v0001_audit.py`, `src/bindaems/app/audit.py`, `src/bindaems/app/api/{__init__,server}.py`
- Test: `tests/app_helpers.py` (neu), `tests/unit/app/conftest.py`, `tests/unit/app/test_db.py`, `tests/unit/app/test_audit.py`, `tests/unit/app/api/test_server.py`

**Interfaces:**
- Consumes: `ensure_utc`, `Clock`, `ManualClock` (shared), `bindaems.__version__`
- Produces:
  - Extra `app = ["fastapi>=0.115", "uvicorn[standard]>=0.30", "websockets>=13", "aiomqtt>=2.3", "sqlalchemy>=2.0", "alembic>=1.13", "argon2-cffi>=23.1", "pyotp>=2.9"]` (alle Abhängigkeiten von 1b auf einmal)
  - `bindaems.app.db.engine`: `DB_FILENAME = "bindaems.sqlite3"`; `open_database(path: Path) -> Engine`; `migrate(engine: Engine) -> None`
  - `bindaems.app.db.schema`: `metadata: MetaData`; `class UtcDateTime(TypeDecorator[datetime])` (Ablage als `String(32)` mit `ensure_utc(v).isoformat(timespec="microseconds")`, Rückgabe zeitzonenbewusst in UTC); Tabelle `audit_log` (`id` Integer PK, `ts` UtcDateTime not null, `actor` String(64) not null, `source` String(16) not null, `action` String(64) not null, `target` String(200) null, `details` JSON null; Index `ix_audit_log_ts`)
  - `bindaems.app.audit`: `Source = Literal["ui", "ha", "cli", "system"]`; `@dataclass(frozen=True) AuditEntry(id: int, ts: datetime, actor: str, source: str, action: str, target: str | None, details: Mapping[str, Any] | None)`; `class AuditLog(engine: Engine, clock: Clock)` mit `record(actor: str, source: Source, action: str, target: str | None = None, details: Mapping[str, Any] | None = None, *, conn: Connection | None = None) -> None` (mit `conn` in der Transaktion des Aufrufers) und `recent(limit: int = 100) -> list[AuditEntry]` (neueste zuerst)
  - `bindaems.app.api.server`: `create_app(routers: Sequence[APIRouter], *, ui_dir: Path | None = None) -> FastAPI`

**Festlegungen:**
- `open_database`: legt das Verzeichnis an; `create_engine(f"sqlite:///{path}")`; bei jeder Verbindung `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000`, `PRAGMA synchronous=NORMAL`; einmalig `PRAGMA journal_mode=WAL`.
- `migrate`: Alembic-`Config()` ohne ini; `script_location` = Verzeichnis `migrations` neben `engine.py`; Verbindung über `config.attributes["connection"]`; `command.upgrade(config, "head")`. `env.py` nutzt `context.configure(connection=…, target_metadata=metadata, render_as_batch=True)`. Migrationen schreiben `UtcDateTime`-Spalten als `sa.String(32)` und importieren keinen App-Code. Revisionen heißen `"0001"`, `"0002"` …
- `create_app`:
  - `GET /health` ohne Anmeldung → `{"status": "ok", "version": "<__version__>"}`.
  - Keine OpenAPI-/Docs-Routen. 404 unter `/api/` → `{"detail": "Nicht gefunden"}`.
  - Reine ASGI-Middleware (WebSockets unverändert durchreichen) setzt auf jede HTTP-Antwort: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin`, `Cross-Origin-Opener-Policy: same-origin`, `Permissions-Policy: camera=(), microphone=(), geolocation=()`, `Content-Security-Policy: default-src 'self'; script-src 'self'{hashes}; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'`; unter `/api/` zusätzlich `Cache-Control: no-store`.
  - `{hashes}`: für jedes `<script>` ohne `src` in `ui_dir/index.html` ` 'sha256-<base64>'` über den Inhalt zwischen den Tags, beim Start berechnet.
  - Mit `ui_dir`: alle übrigen GET-Pfade liefern die Datei aus `ui_dir`, wenn sie existiert und aufgelöst innerhalb von `ui_dir` liegt; sonst `index.html` (SPA). `index.html` mit `Cache-Control: no-cache`, Dateien unter `/_app/immutable/` mit `public, max-age=31536000, immutable`.
- `tests/app_helpers.py`: `T_APP = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)` (10:00 Ortszeit); Tests und Fixtures importieren es von dort. `tests/unit/app/conftest.py`: Fixtures `clock` (`ManualClock(T_APP)`), `engine` (Datei in `tmp_path`, migriert, am Ende `dispose()`), `audit`.

- [ ] **Step 1: Write the failing tests**

```python
# test_db.py
def test_migrations_match_schema(engine: Engine) -> None:
    with engine.connect() as conn:
        assert compare_metadata(MigrationContext.configure(conn), metadata) == []

def test_migrate_twice_is_harmless(engine: Engine) -> None:
    migrate(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM alembic_version")).scalar_one() == 1

def test_sqlite_runs_in_wal_mode_with_foreign_keys(engine: Engine) -> None:
    with engine.connect() as conn:
        assert conn.execute(text("PRAGMA journal_mode")).scalar_one() == "wal"
        assert conn.execute(text("PRAGMA foreign_keys")).scalar_one() == 1

def test_open_database_creates_directory(tmp_path: Path) -> None:
    migrate(open_database(tmp_path / "neu" / "app.sqlite3"))
    assert (tmp_path / "neu" / "app.sqlite3").exists()

# test_audit.py
def test_audit_lists_newest_first_with_aware_timestamps(audit: AuditLog, clock: ManualClock) -> None:
    audit.record("chris", "ui", "settings.update", "einstellungen", {"version": 2})
    clock.advance(60)
    audit.record("cli", "cli", "user.create", "chris")
    first, second = audit.recent()
    assert (first.action, second.action) == ("user.create", "settings.update")
    assert second.details == {"version": 2} and second.ts == T_APP and second.ts.tzinfo is not None

def test_audit_limit(audit: AuditLog) -> None:
    for n in range(3):
        audit.record("chris", "ui", f"a{n}")
    assert [e.action for e in audit.recent(limit=2)] == ["a2", "a1"]

def test_naive_timestamps_are_rejected(engine: Engine) -> None:
    with pytest.raises(StatementError, match="naiver Zeitpunkt"), engine.begin() as conn:
        conn.execute(insert(audit_log).values(ts=datetime(2026, 10, 9), actor="x", source="ui", action="y"))

# api/test_server.py
def test_health_needs_no_login() -> None:
    assert TestClient(create_app([])).get("/health").json() == {"status": "ok", "version": "0.1.0"}

def test_security_headers_on_every_response() -> None:
    headers = TestClient(create_app([])).get("/health").headers
    assert headers["x-content-type-options"] == "nosniff" and headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "same-origin"
    assert "frame-ancestors 'none'" in headers["content-security-policy"]

def test_unknown_api_path_is_german_404_and_not_cached() -> None:
    response = TestClient(create_app([])).get("/api/gibt-es-nicht")
    assert response.status_code == 404 and response.json() == {"detail": "Nicht gefunden"}
    assert response.headers["cache-control"] == "no-store"

def test_no_openapi_or_docs() -> None:
    client = TestClient(create_app([]))
    assert client.get("/openapi.json").status_code == 404 and client.get("/docs").status_code == 404

def test_ui_with_spa_fallback_and_inline_script_hash(tmp_path: Path) -> None:
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<html><script>boot()</script></html>")
    (ui / "app.js").write_text("console.log(1)")
    (tmp_path / "geheim.txt").write_text("geheim")
    client = TestClient(create_app([], ui_dir=ui))
    index = client.get("/einstellungen/tarif")
    assert index.text.startswith("<html>") and index.headers["cache-control"] == "no-cache"
    csp = index.headers["content-security-policy"]
    assert "'sha256-MeZS89WlF0u+o0hCvHTBt4q1WHU+U+sJKgbdRUc36mY='" in csp
    assert client.get("/app.js").text == "console.log(1)"
    assert "geheim" not in client.get("/%2e%2e/geheim.txt").text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.app.db'`)

- [ ] **Step 3: Implement engine, schema, migration 0001, `AuditLog` and `create_app` as specified**

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (12 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock .github/workflows/ci.yml src/bindaems/app tests/app_helpers.py tests/unit/app
git commit -m "feat(app): SQLite mit Alembic, Änderungsprotokoll und HTTP-Grundgerüst"
```

---

### Task 4: Benutzer, Sitzungen, Sperre und TOTP (Dienst)

**Files:**
- Create: `src/bindaems/app/auth/{__init__,service}.py`, `src/bindaems/app/db/migrations/versions/v0002_auth.py`
- Modify: `src/bindaems/app/db/schema.py`, `tests/unit/app/conftest.py` (Fixture `auth`), `tests/app_helpers.py`
- Test: `tests/unit/app/auth/test_service.py`

**Interfaces:**
- Consumes: `AuditLog`, `UtcDateTime`, `metadata` (Task 3); `Clock`
- Produces:
  - Tabellen: `user` (`id` Integer PK, `username` String(32) unique not null, `password_hash` String(200) not null, `role` String(16) not null, `totp_secret` String(64) null, `totp_enabled` Boolean not null, `totp_last_step` Integer null, `created_at`, `updated_at`); `session` (`id` String(64) PK = SHA-256-Hex des Tokens, `user_id` FK `user.id` ON DELETE CASCADE not null mit Index, `csrf_token` String(64) not null, `created_at`, `last_seen_at`, `expires_at`, `remember` Boolean not null); `login_failure` (`id` Integer PK, `username` String(64) not null, `ts` not null; Index `ix_login_failure_username_ts`)
  - `Role = Literal["admin", "operator", "viewer"]`; `ROLE_RANK: dict[Role, int] = {"viewer": 0, "operator": 1, "admin": 2}`
  - `@dataclass(frozen=True) User(id: int, username: str, role: Role, totp_enabled: bool, created_at: datetime)`
  - `@dataclass(frozen=True) NewSession(token: str, csrf_token: str, expires_at: datetime, remember: bool, user: User)`
  - `@dataclass(frozen=True) SessionInfo(session_id: str, csrf_token: str, expires_at: datetime, remember: bool, user: User)`
  - `class AuthError(Exception)` mit Unterklassen und Texten: `InvalidCredentialsError` („Benutzername oder Passwort falsch“), `TotpRequiredError` („Bestätigungscode erforderlich“), `LockedError(until: datetime)` („Zu viele Fehlversuche – Anmeldung gesperrt bis {HH:MM} Uhr“, Ortszeit), `InvalidTotpError` („Bestätigungscode ungültig“), `UserExistsError` („Benutzer „{name}“ existiert bereits“), `UserNotFoundError` („Benutzer nicht gefunden“), `LastAdminError` („Der letzte Admin kann nicht entfernt oder herabgestuft werden“), `PasswordPolicyError` („Das Passwort muss mindestens 10 Zeichen lang sein“), `InvalidUsernameError` („Benutzername: 3–32 Zeichen aus a–z, 0–9, Punkt, Bindestrich, Unterstrich“)
  - `class AuthService(engine: Engine, clock: Clock, audit: AuditLog, *, hasher: PasswordHasher | None = None)`:
    - `create_user(username: str, password: str, role: Role, *, actor: str, source: Source) -> User`
    - `list_users() -> list[User]`, `get_user(user_id: int) -> User`
    - `set_role(user_id: int, role: Role, *, actor: str, source: Source) -> User`
    - `set_password(user_id: int, password: str, *, actor: str, source: Source, keep_session: str | None = None) -> None`
    - `verify_password(user_id: int, password: str) -> bool`
    - `delete_user(user_id: int, *, actor: str, source: Source) -> None`
    - `login(username: str, password: str, *, totp: str | None, remember: bool) -> NewSession`
    - `resolve(token: str, *, touch: bool = True) -> SessionInfo | None`, `logout(token: str) -> None`, `cleanup() -> int`
    - `totp_begin(user_id: int) -> tuple[str, str]` (Geheimnis, otpauth-URI), `totp_enable(user_id: int, code: str, *, actor: str, source: Source) -> None`, `totp_disable(user_id: int, password: str, *, actor: str, source: Source) -> None`
    - `warnings() -> list[str]`: je Admin ohne TOTP „Admin „{name}“ hat keine Zwei-Faktor-Anmeldung (TOTP).“
  - Konstanten: `IDLE_TIMEOUT = 30 min`, `REMEMBER_FOR = 30 d`, `LOCKOUT_FAILURES = 5`, `LOCKOUT_WINDOW = 15 min`, `LOCKOUT_FOR = 15 min`, `TOUCH_EVERY = 60 s`, `MIN_PASSWORD_LEN = 10`, `MAX_PASSWORD_LEN = 1024`, `USERNAME_RE = r"^[a-z0-9._-]{3,32}$"` (nach Kleinschreibung)
  - `tests/app_helpers.py` ergänzt: `PASSWORD = "richtig-geheim-1"`, `FAST_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1)`

**Festlegungen:**
- **Sperre:** die fünf jüngsten Fehlversuche des Benutzernamens; liegen zwischen ältestem und jüngstem höchstens 15 min, gilt die Sperre bis jüngster + 15 min, solange `now < until`. Während der Sperre wird das Passwort nicht geprüft und kein Fehlversuch gezählt. Fehlversuche sind: falsches Passwort, unbekannter Name (gegen einen festen Dummy-Hash geprüft, damit die Laufzeit gleich bleibt), falscher TOTP-Code. Erfolg löscht die Fehlversuche des Namens und rehasht bei Bedarf (`check_needs_rehash`).
- **Sitzung:** Token `secrets.token_urlsafe(32)`, gespeichert nur `sha256(token)`; CSRF-Token `secrets.token_urlsafe(32)`. `remember` gilt nur für `operator`/`viewer` (fest 30 d ab Anmeldung); sonst `expires_at = now + 30 min`, verlängert bei `resolve(touch=True)`, wenn seit `last_seen_at` mindestens 60 s vergangen sind. Abgelaufen ist eine Sitzung bei `now >= expires_at`; sie wird dann gelöscht. `resolve` liefert stets die aktuelle Rolle des Benutzers.
- **TOTP:** `pyotp.random_base32()`; URI `TOTP(secret).provisioning_uri(name=username, issuer_name="BindaEMS")`; gültig sind Codes der Schritte `t − 1`, `t`, `t + 1` mit `t = TOTP(secret).timecode(now)` (Vergleich mit `hmac.compare_digest` gegen `generate_otp(step)`), aber nur, wenn `step > totp_last_step`; der Schritt wird gespeichert.
- **Rollenwechsel** und Löschen beenden alle Sitzungen des Benutzers. Der letzte Admin darf weder herabgestuft noch gelöscht werden. `set_password` beendet alle Sitzungen außer `keep_session`.
- **Protokoll:** Aktionen `user.create`, `user.role`, `user.password`, `user.delete`, `user.totp_enable`, `user.totp_disable`; `target` = Benutzername; nie Passwörter oder Geheimnisse in `details`.

- [ ] **Step 1: Write the failing tests** (Fixture `auth` = `AuthService(engine, clock, audit, hasher=FAST_HASHER)`)

```python
def test_create_user_stores_argon2id_hash_and_lowercase_name(auth, engine) -> None:
    user = auth.create_user("Chris", PASSWORD, "admin", actor="cli", source="cli")
    assert (user.username, user.role) == ("chris", "admin")
    with engine.connect() as conn:
        stored = conn.execute(select(user_table.c.password_hash)).scalar_one()
    assert stored.startswith("$argon2id$") and PASSWORD not in stored

@pytest.mark.parametrize("name", ["ab", "mit leerzeichen", "x" * 33, "ümlaut"])
def test_invalid_usernames_rejected(auth, name: str) -> None:
    with pytest.raises(InvalidUsernameError):
        auth.create_user(name, PASSWORD, "viewer", actor="cli", source="cli")

def test_short_password_rejected(auth) -> None:
    with pytest.raises(PasswordPolicyError):
        auth.create_user("chris", "zu-kurz", "viewer", actor="cli", source="cli")

def test_duplicate_username_rejected_case_insensitive(auth) -> None:
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    with pytest.raises(UserExistsError):
        auth.create_user("CHRIS", PASSWORD, "viewer", actor="cli", source="cli")

def test_login_stores_only_the_token_hash(auth, engine) -> None:
    auth.create_user("chris", PASSWORD, "operator", actor="cli", source="cli")
    session = auth.login("Chris", PASSWORD, totp=None, remember=False)
    with engine.connect() as conn:
        ids = conn.execute(select(session_table.c.id)).scalars().all()
    assert ids == [hashlib.sha256(session.token.encode()).hexdigest()]
    assert auth.resolve(session.token).user.username == "chris"

def test_wrong_password_and_unknown_user_look_the_same(auth) -> None:
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    with pytest.raises(InvalidCredentialsError) as wrong:
        auth.login("chris", "falsch-falsch", totp=None, remember=False)
    with pytest.raises(InvalidCredentialsError) as unknown:
        auth.login("niemand", "falsch-falsch", totp=None, remember=False)
    assert str(wrong.value) == str(unknown.value) == "Benutzername oder Passwort falsch"

def test_lockout_after_five_failures_within_15_minutes(auth, clock) -> None:
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    for _ in range(5):  # Fehlversuche bei T+0 … T+4 min
        with pytest.raises(InvalidCredentialsError):
            auth.login("chris", "falsch-falsch", totp=None, remember=False)
        clock.advance(60)
    with pytest.raises(LockedError) as locked:
        auth.login("chris", PASSWORD, totp=None, remember=False)
    assert locked.value.until == T_APP + timedelta(minutes=19)
    clock.advance(timedelta(minutes=14))  # jetzt T+19 min
    assert auth.login("chris", PASSWORD, totp=None, remember=False).user.username == "chris"

def test_failures_spread_over_more_than_15_minutes_do_not_lock(auth, clock) -> None:
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    for _ in range(5):  # T+0, 4, 8, 12, 16 min
        with pytest.raises(InvalidCredentialsError):
            auth.login("chris", "falsch-falsch", totp=None, remember=False)
        clock.advance(timedelta(minutes=4))
    assert auth.login("chris", PASSWORD, totp=None, remember=False)

def test_unknown_usernames_are_locked_like_real_ones(auth) -> None:
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            auth.login("niemand", "falsch-falsch", totp=None, remember=False)
    with pytest.raises(LockedError):
        auth.login("niemand", "falsch-falsch", totp=None, remember=False)

def test_successful_login_clears_failures(auth) -> None:
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    for _ in range(2):
        for _ in range(4):
            with pytest.raises(InvalidCredentialsError):
                auth.login("chris", "falsch-falsch", totp=None, remember=False)
        assert auth.login("chris", PASSWORD, totp=None, remember=False)

def test_idle_session_expires_after_30_minutes(auth, clock) -> None:
    auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    session = auth.login("chris", PASSWORD, totp=None, remember=False)
    clock.advance(timedelta(minutes=30))
    assert auth.resolve(session.token) is None

def test_activity_extends_idle_session(auth, clock) -> None:
    auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    session = auth.login("chris", PASSWORD, totp=None, remember=False)
    clock.advance(timedelta(minutes=29))
    assert auth.resolve(session.token) is not None
    clock.advance(timedelta(minutes=29))
    assert auth.resolve(session.token) is not None

def test_remember_me_keeps_operator_logged_in_for_30_days(auth, clock) -> None:
    auth.create_user("chris", PASSWORD, "operator", actor="cli", source="cli")
    session = auth.login("chris", PASSWORD, totp=None, remember=True)
    clock.advance(timedelta(days=29))
    assert auth.resolve(session.token) is not None
    clock.advance(timedelta(days=1))
    assert auth.resolve(session.token) is None

def test_remember_me_is_ignored_for_admins(auth) -> None:
    auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    session = auth.login("chris", PASSWORD, totp=None, remember=True)
    assert session.remember is False and session.expires_at == T_APP + timedelta(minutes=30)

def test_logout_deletes_session(auth) -> None:
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    session = auth.login("chris", PASSWORD, totp=None, remember=False)
    auth.logout(session.token)
    assert auth.resolve(session.token) is None

def test_totp_required_once_enabled_and_codes_not_reusable(auth, clock) -> None:
    user = auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    secret, uri = auth.totp_begin(user.id)
    assert uri.startswith("otpauth://totp/BindaEMS:chris?")
    totp = pyotp.TOTP(secret)
    auth.totp_enable(user.id, totp.at(clock.now()), actor="chris", source="ui")
    with pytest.raises(TotpRequiredError):
        auth.login("chris", PASSWORD, totp=None, remember=False)
    clock.advance(30)
    code = totp.at(clock.now())
    assert auth.login("chris", PASSWORD, totp=code, remember=False)
    with pytest.raises(InvalidCredentialsError):
        auth.login("chris", PASSWORD, totp=code, remember=False)

def test_wrong_totp_codes_count_as_failures(auth, clock) -> None:
    user = auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    secret, _ = auth.totp_begin(user.id)
    auth.totp_enable(user.id, pyotp.TOTP(secret).at(clock.now()), actor="chris", source="ui")
    for _ in range(5):
        with pytest.raises(InvalidCredentialsError):
            auth.login("chris", PASSWORD, totp="000000", remember=False)
    with pytest.raises(LockedError):
        auth.login("chris", PASSWORD, totp="000000", remember=False)

def test_role_change_ends_sessions_and_last_admin_is_protected(auth) -> None:
    admin = auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    operator = auth.create_user("gast", PASSWORD, "operator", actor="chris", source="ui")
    session = auth.login("gast", PASSWORD, totp=None, remember=True)
    auth.set_role(operator.id, "viewer", actor="chris", source="ui")
    assert auth.resolve(session.token) is None
    with pytest.raises(LastAdminError):
        auth.set_role(admin.id, "operator", actor="chris", source="ui")
    with pytest.raises(LastAdminError):
        auth.delete_user(admin.id, actor="chris", source="ui")

def test_password_change_ends_other_sessions(auth) -> None:
    user = auth.create_user("chris", PASSWORD, "operator", actor="cli", source="cli")
    keep = auth.login("chris", PASSWORD, totp=None, remember=False)
    other = auth.login("chris", PASSWORD, totp=None, remember=False)
    auth.set_password(user.id, "neues-passwort-1", actor="chris", source="ui",
                      keep_session=auth.resolve(keep.token).session_id)
    assert auth.resolve(keep.token) is not None and auth.resolve(other.token) is None

def test_changes_are_audited_without_secrets(auth, audit) -> None:
    user = auth.create_user("chris", PASSWORD, "operator", actor="cli", source="cli")
    auth.set_role(user.id, "viewer", actor="admin", source="ui")
    entries = audit.recent()
    assert [e.action for e in entries] == ["user.role", "user.create"]
    assert PASSWORD not in json.dumps([e.details for e in entries])

def test_admins_without_totp_are_reported(auth) -> None:
    auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    assert auth.warnings() == ["Admin „chris“ hat keine Zwei-Faktor-Anmeldung (TOTP)."]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/auth -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.app.auth'`)

- [ ] **Step 3: Implement migration 0002 and `AuthService` as specified**

Falls mypy für `pyotp` keine Typen findet: `[[tool.mypy.overrides]] module = ["pyotp"]` mit `ignore_missing_imports = true`.

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (24 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests/app_helpers.py tests/unit/app pyproject.toml
git commit -m "feat(app): Benutzer, Sitzungen, Sperre und TOTP"
```

---

### Task 5: Anmeldung über HTTP, CSRF, Rollen, Benutzerverwaltung und CLI

**Files:**
- Create: `src/bindaems/app/auth/{web,routes}.py`, `src/bindaems/app/cli.py`, `src/bindaems/app/__main__.py`
- Modify: `tests/unit/app/conftest.py` (Fixtures `guard`, `make_client`), `tests/app_helpers.py` (`login_as`)
- Test: `tests/unit/app/auth/test_web.py`, `tests/unit/app/test_cli.py`

**Interfaces:**
- Consumes: `AuthService`, `SessionInfo`, `NewSession`, `Role`, `ROLE_RANK` (Task 4); `AuditLog` (Task 3); `create_app` (Task 3); `load_config`, `ConfigError` (shared)
- Produces:
  - `bindaems.app.auth.web`: `SESSION_COOKIE = "bindaems_session"`, `CSRF_COOKIE = "bindaems_csrf"`, `CSRF_HEADER = "X-CSRF-Token"`; `class Guard(auth: AuthService, *, cookie_secure: bool)` mit
    - `require(role: Role) -> Callable[[Request], SessionInfo]` (FastAPI-Abhängigkeit): ohne gültige Sitzung 401 „Nicht angemeldet“; Rang zu niedrig 403 „Keine Berechtigung“; bei POST/PUT/PATCH/DELETE muss `X-CSRF-Token` gleich `SessionInfo.csrf_token` sein (`hmac.compare_digest`), sonst 403 „CSRF-Token fehlt oder ist ungültig“
    - `async websocket_session(ws: WebSocket) -> SessionInfo | None` (Cookie, `touch=False`, über `asyncio.to_thread`)
    - `set_session_cookies(response: Response, session: NewSession) -> None`, `clear_session_cookies(response: Response) -> None`
  - `bindaems.app.auth.routes`: `auth_router(auth: AuthService, guard: Guard) -> APIRouter`, `users_router(auth: AuthService, guard: Guard) -> APIRouter`, `audit_router(audit: AuditLog, guard: Guard) -> APIRouter`
  - `bindaems.app.cli`: `main(argv: list[str] | None = None) -> int`; `bindaems.app.__main__` ruft `sys.exit(main())`
  - Benutzer-JSON: `{"id", "username", "role", "totp_enabled", "created_at"}`
  - Test-Helfer: `login_as(client: TestClient, auth: AuthService, role: Role, username: str | None = None) -> dict[str, str]` legt den Benutzer an, meldet ihn an und gibt `{"X-CSRF-Token": <token>}` zurück

**Festlegungen:**
- Cookies: beide mit `Secure` (= `cookie_secure`), `SameSite=Strict`, `Path=/`; `bindaems_session` HttpOnly, `bindaems_csrf` nicht. `Max-Age=2592000` nur bei `remember`.
- Endpunkte (Akteur = Benutzername der Sitzung, Quelle `ui`):

  | Methode und Pfad | Rolle | Körper → Antwort |
  |---|---|---|
  | `POST /api/auth/login` | – | `{username, password, totp?, remember?=false}` → 200 `{"user": …}` plus Cookies; 401 `{"detail": "Benutzername oder Passwort falsch"}`; 401 `{"detail": "Bestätigungscode erforderlich", "totp_required": true}`; 429 mit `Retry-After` (Sekunden bis `until`, aufgerundet) und `detail` aus `LockedError` |
  | `POST /api/auth/logout` | viewer | → 204, Cookies gelöscht |
  | `GET /api/auth/me` | viewer | → `{"user": …}` |
  | `POST /api/auth/password` | viewer | `{old_password, new_password}` → 204; altes Passwort falsch → 400 „Altes Passwort falsch“; Richtlinie → 422 |
  | `POST /api/auth/totp/setup` | viewer | → `{"secret", "uri"}` |
  | `POST /api/auth/totp/enable` | viewer | `{code}` → 204; 400 „Bestätigungscode ungültig“ |
  | `POST /api/auth/totp/disable` | viewer | `{password}` → 204; 400 „Passwort falsch“ |
  | `GET /api/users` | admin | → Liste |
  | `POST /api/users` | admin | `{username, password, role}` → 201 Benutzer; 409 bei `UserExistsError`; 422 bei Name/Passwort |
  | `PATCH /api/users/{id}` | admin | `{role?, password?}` → 200; 409 bei `LastAdminError`; 404 |
  | `DELETE /api/users/{id}` | admin | → 204; 409; 404 |
  | `GET /api/audit?limit=100` | admin | `limit` 1–1000 → `[{"id", "ts", "actor", "source", "action", "target", "details"}]` |

- Anmeldungen protokolliert structlog: Erfolg `info` „Anmeldung erfolgreich“, Fehler `warning` „Anmeldung fehlgeschlagen“, jeweils mit `username` und `ip` (`request.client.host`).
- CLI: Unterbefehle `create-admin --username NAME [--password-stdin]` und `set-password --username NAME [--password-stdin]`, jeweils mit `--config` (Default `/config/config.yaml`, über einen Eltern-Parser auch nach dem Unterbefehl erlaubt). Datenbank `cfg.app.data_dir / DB_FILENAME`, vorher `migrate`. Passwort aus der ersten Zeile von stdin oder zweimal per `getpass` („Passwörter stimmen nicht überein“). Ausgaben: „Admin „{name}“ angelegt.“ bzw. „Passwort für „{name}“ geändert.“ (stdout, Exit 0); Fehler als `AuthError`-Text auf stderr, Exit 1; ungültige Konfiguration Exit 2. Protokoll mit Akteur `cli`, Quelle `cli`.
- Test-Client: `TestClient(app, base_url="https://testserver")`, sonst schickt der Client Secure-Cookies nicht zurück. Fixture `make_client(auth, guard, audit)` liefert eine Fabrik `build(*routers)`, die `create_app([auth_router(auth, guard), users_router(auth, guard), audit_router(audit, guard), *routers])` in einen solchen Test-Client packt.

- [ ] **Step 1: Write the failing tests**

```python
def test_login_sets_strict_secure_cookies(make_client, auth) -> None:
    client = make_client()
    auth.create_user("chris", PASSWORD, "operator", actor="cli", source="cli")
    response = client.post("/api/auth/login", json={"username": "chris", "password": PASSWORD})
    assert response.status_code == 200 and response.json()["user"]["role"] == "operator"
    cookies = {c.split("=")[0]: c.lower() for c in response.headers.get_list("set-cookie")}
    for cookie in cookies.values():
        assert "secure" in cookie and "samesite=strict" in cookie and "max-age" not in cookie
    assert "httponly" in cookies["bindaems_session"] and "httponly" not in cookies["bindaems_csrf"]

def test_remember_sets_max_age_30_days(make_client, auth) -> None:
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    response = make_client().post("/api/auth/login",
                                  json={"username": "chris", "password": PASSWORD, "remember": True})
    assert "Max-Age=2592000" in response.headers["set-cookie"]

def test_cookies_without_secure_when_configured(auth, audit) -> None:
    guard = Guard(auth, cookie_secure=False)
    client = TestClient(create_app([auth_router(auth, guard)]))
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    response = client.post("/api/auth/login", json={"username": "chris", "password": PASSWORD})
    assert "secure" not in response.headers["set-cookie"].lower()

def test_api_requires_login(make_client) -> None:
    response = make_client().get("/api/auth/me")
    assert response.status_code == 401 and response.json() == {"detail": "Nicht angemeldet"}

def test_unsafe_requests_need_csrf_header(make_client, auth) -> None:
    client = make_client()
    csrf = login_as(client, auth, "viewer")
    assert client.post("/api/auth/logout").json() == {"detail": "CSRF-Token fehlt oder ist ungültig"}
    assert client.post("/api/auth/logout", headers=csrf).status_code == 204
    assert client.get("/api/auth/me").status_code == 401

def test_viewer_cannot_manage_users(make_client, auth) -> None:
    client = make_client()
    csrf = login_as(client, auth, "viewer")
    response = client.post("/api/users", json={"username": "x1x", "password": PASSWORD, "role": "admin"},
                           headers=csrf)
    assert response.status_code == 403 and response.json() == {"detail": "Keine Berechtigung"}

def test_admin_manages_users_and_changes_are_audited(make_client, auth) -> None:
    client = make_client()
    csrf = login_as(client, auth, "admin")
    created = client.post("/api/users", json={"username": "gast", "password": PASSWORD, "role": "viewer"},
                          headers=csrf)
    assert created.status_code == 201
    uid = created.json()["id"]
    assert client.patch(f"/api/users/{uid}", json={"role": "operator"}, headers=csrf).json()["role"] == "operator"
    assert client.delete(f"/api/users/{uid}", headers=csrf).status_code == 204
    actions = [entry["action"] for entry in client.get("/api/audit").json()]
    assert actions[:3] == ["user.delete", "user.role", "user.create"]

def test_last_admin_cannot_delete_itself(make_client, auth) -> None:
    client = make_client()
    csrf = login_as(client, auth, "admin", username="chris")
    me = client.get("/api/auth/me").json()["user"]["id"]
    response = client.delete(f"/api/users/{me}", headers=csrf)
    assert response.status_code == 409
    assert response.json() == {"detail": "Der letzte Admin kann nicht entfernt oder herabgestuft werden"}

def test_lockout_answers_429_with_retry_after(make_client, auth) -> None:
    client = make_client()
    auth.create_user("chris", PASSWORD, "viewer", actor="cli", source="cli")
    for _ in range(5):
        assert client.post("/api/auth/login",
                           json={"username": "chris", "password": "falsch-falsch"}).status_code == 401
    response = client.post("/api/auth/login", json={"username": "chris", "password": PASSWORD})
    assert response.status_code == 429 and response.headers["retry-after"] == "900"
    assert response.json() == {"detail": "Zu viele Fehlversuche – Anmeldung gesperrt bis 10:15 Uhr"}

def test_login_reports_totp_requirement(make_client, auth, clock) -> None:
    user = auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    secret, _ = auth.totp_begin(user.id)
    auth.totp_enable(user.id, pyotp.TOTP(secret).at(clock.now()), actor="chris", source="ui")
    response = make_client().post("/api/auth/login", json={"username": "chris", "password": PASSWORD})
    assert response.status_code == 401
    assert response.json() == {"detail": "Bestätigungscode erforderlich", "totp_required": True}

def test_password_change_requires_old_password(make_client, auth) -> None:
    client = make_client()
    csrf = login_as(client, auth, "operator")
    wrong = client.post("/api/auth/password", json={"old_password": "falsch-falsch",
                                                    "new_password": "neues-passwort-1"}, headers=csrf)
    assert wrong.status_code == 400 and wrong.json() == {"detail": "Altes Passwort falsch"}
    assert client.post("/api/auth/password", json={"old_password": PASSWORD,
                                                   "new_password": "neues-passwort-1"},
                       headers=csrf).status_code == 204

def test_totp_setup_and_enable_via_api(make_client, auth, clock) -> None:
    client = make_client()
    csrf = login_as(client, auth, "admin")
    secret = client.post("/api/auth/totp/setup", headers=csrf).json()["secret"]
    code = pyotp.TOTP(secret).at(clock.now())
    assert client.post("/api/auth/totp/enable", json={"code": code}, headers=csrf).status_code == 204
    assert client.get("/api/auth/me").json()["user"]["totp_enabled"] is True

# test_cli.py – write_app_config(tmp_path) schreibt das Beispiel mit app.data_dir = tmp_path
def test_create_admin_reads_password_from_stdin(tmp_path, monkeypatch, capsys) -> None:
    path = write_app_config(tmp_path)
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert main(["create-admin", "--username", "Chris", "--password-stdin", "--config", str(path)]) == 0
    assert "Admin „chris“ angelegt." in capsys.readouterr().out
    engine = open_database(tmp_path / DB_FILENAME)
    [user] = AuthService(engine, SystemClock(), AuditLog(engine, SystemClock())).list_users()
    assert (user.username, user.role) == ("chris", "admin")

def test_create_admin_twice_fails(tmp_path, monkeypatch, capsys) -> None:
    path = write_app_config(tmp_path)
    for expected in (0, 1):
        monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
        assert main(["create-admin", "--username", "chris", "--password-stdin",
                     "--config", str(path)]) == expected
    assert "existiert bereits" in capsys.readouterr().err

def test_set_password_for_unknown_user_fails(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    assert main(["set-password", "--username", "niemand", "--password-stdin",
                 "--config", str(write_app_config(tmp_path))]) == 1
    assert "Benutzer nicht gefunden" in capsys.readouterr().err

def test_cli_rejects_invalid_config(tmp_path, capsys) -> None:
    (tmp_path / "config.yaml").write_text("grid: {}\n")
    assert main(["create-admin", "--username", "chris", "--config", str(tmp_path / "config.yaml")]) == 2
    assert "Konfiguration ungültig" in capsys.readouterr().err
```

(Die Sperrzeit im Test: fünf Fehlversuche um 10:00 Ortszeit → gesperrt bis 10:15 Uhr.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/auth/test_web.py tests/unit/app/test_cli.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.app.auth.web'`)

- [ ] **Step 3: Implement `Guard`, the routers and the CLI as specified**

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (16 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests/app_helpers.py tests/unit/app
git commit -m "feat(app): Anmeldung, CSRF, Rollen, Benutzerverwaltung und CLI"
```

---

### Task 6: Laufzeit-Einstellungen mit Versionen und Änderungsprotokoll

**Files:**
- Create: `src/bindaems/shared/settings.py`, `src/bindaems/app/settings/{__init__,service,routes}.py`, `src/bindaems/app/db/migrations/versions/v0003_settings.py`
- Modify: `src/bindaems/app/db/schema.py`
- Test: `tests/unit/shared/test_settings.py`, `tests/unit/app/settings/test_service.py`, `tests/unit/app/settings/test_routes.py`

**Interfaces:**
- Consumes: `_Model` (shared/config), `AuditLog`, `Source` (Task 3); `Guard` (Task 5); `Config` (Task 2)
- Produces (`bindaems.shared.settings`, alle Modelle `extra="forbid"`, `frozen=True`):
  - `TimeWindow`: `months: list[int]` (1–12, nicht leer, eindeutig; Default alle), `weekdays: list[int]` (0 = Montag … 6, Default alle), `start: time = time(0)`, `end: time = time(0)` (`00:00` als Ende = Tagesende; sonst `end > start`; beide auf Viertelstunden, Sekunden 0), genau eines von `factor: float | None` (≥ 0) und `value_ct: float | None`
  - `PriceComponent`: `id` (`^[a-z0-9_]{1,40}$`), `name` (1–80 Zeichen), `kind: Literal["energy_per_kwh"] = "energy_per_kwh"`, `source: Literal["fixed", "spot"] = "fixed"`, `value_ct: float | None = None` (netto; bei `spot` muss es `None` sein), `vat: bool = True`, `valid_from: date | None = None`, `valid_until: date | None = None` (inklusive, `valid_from ≤ valid_until`), `windows: list[TimeWindow] = []`
  - `TariffSettings`: `vat_pct: float = 20.0` (0–100), `components: list[PriceComponent]` (Default siehe unten; IDs eindeutig; genau eine mit `source="spot"`, sonst „Genau ein Bestandteil muss den Börsenpreis liefern (source: spot)“), `fixed_price_gross_ct: float = 30.0` (≥ 0)
  - `FeedInSettings`: `monthly_ct: dict[str, float] = {}` (Schlüssel `^\d{4}-(0[1-9]|1[0-2])$`, Werte ≥ 0)
  - `PriceSettings`: `vat_mode: Literal["auto", "net", "gross"] = "auto"`, `vat_fallback: Literal["net", "gross"] = "gross"`, `reference_source: Literal["energy_charts", "awattar"] = "energy_charts"`
  - `PvModelSettings`: `performance_ratio: float = 0.85` (> 0, ≤ 1,2), `temp_coeff_pct_per_k: float = -0.35` (−2 … 0), `noct_c: float = 45.0` (20 … 80)
  - `RuntimeSettings`: `prices`, `tariff`, `feed_in`, `pv_model` (jeweils Default-Instanz)
  - `settings_warnings(settings: RuntimeSettings) -> list[str]`
- Produces (`bindaems.app.settings`):
  - Tabelle `settings_version` (`version` Integer PK, `created_at`, `actor` String(64), `source` String(16), `comment` String(200) null, `data` JSON not null)
  - `@dataclass(frozen=True) SettingsVersion(version: int, created_at: datetime, actor: str, source: str, comment: str | None, settings: RuntimeSettings)`
  - `VersionConflictError(current: int)` („Die Einstellungen wurden inzwischen geändert (aktuell Version {n}).“), `SettingsImportError(message: str)`
  - `class SettingsService(engine: Engine, clock: Clock, audit: AuditLog)`: `current() -> SettingsVersion`; `update(settings: RuntimeSettings, *, base_version: int, actor: str, source: Source, comment: str | None = None) -> SettingsVersion`; `versions(limit: int = 50) -> list[SettingsVersion]`; `export_yaml() -> str`; `import_yaml(text: str, *, actor: str, source: Source) -> SettingsVersion`; `warnings() -> list[str]`; `on_change(callback: Callable[[SettingsVersion], None]) -> None`
  - `diff_paths(old: Any, new: Any) -> list[dict[str, Any]]` (Pfade wie `tariff.components[2].value_ct`)
  - `settings_router(service: SettingsService, cfg: Config, guard: Guard) -> APIRouter`

**Festlegungen:**
- Standardbestandteile (Spec 9.1; Werte nur, wo die Spec sie nennt):

  | id | name | Festlegung |
  |---|---|---|
  | `spot` | Börsenpreis | `source="spot"` |
  | `supplier_markup` | Lieferantenaufschlag | `value_ct=1.2` |
  | `grid_usage` | Netznutzungsentgelt Arbeitspreis (Netzebene 7) | `value_ct=None`, Fenster SNAP: Monate 4–9, alle Wochentage, 10:00–16:00, `factor=0.8` |
  | `grid_loss` | Netzverlustentgelt | `value_ct=None` |
  | `electricity_tax` | Elektrizitätsabgabe | `value_ct=None` |
  | `renewables_levy` | Erneuerbaren-Förderbeitrag | `value_ct=None` |

- `settings_warnings`: je festem Bestandteil ohne Wert „Tarifbestandteil „{name}“ hat noch keinen Wert.“; ohne OeMAG-Eintrag „Noch kein OeMAG-Monatswert eingetragen.“
- Erster Start: leere Tabelle → Version 1 mit Standardwerten (Akteur und Quelle `system`, Kommentar „Standardwerte“).
- Gespeicherte Daten, die nicht validieren: Fehler loggen, Standardwerte mit der gespeicherten Versionsnummer verwenden, nichts überschreiben; `warnings()` enthält dann zuerst „Gespeicherte Einstellungen (Version {n}) passen nicht zum Schema – Standardwerte aktiv.“. Ein `update` mit `base_version=n` legt in diesem Zustand immer eine neue Version an (auch mit Standardwerten) und beendet ihn.
- `update`: andere `base_version` → `VersionConflictError`; inhaltlich gleich → aktuelle Version zurück, kein Eintrag; sonst neue Version und Protokolleintrag `settings.update` (`target="einstellungen"`, `details={"version": n, "changes": diff_paths(...)[:100]}`) in einer Transaktion; danach Listener aufrufen (Ausnahmen loggen, nicht weiterreichen).
- `export_yaml`: erste Zeile `# BindaEMS-Laufzeit-Einstellungen, Version {n}`, dann `yaml.safe_dump(model_dump(mode="json"), allow_unicode=True, sort_keys=False)`. `import_yaml`: kein Objekt → „YAML muss ein Objekt enthalten“; Validierungsfehler → Zeilen „{pfad}: {meldung}“; sonst `update(base_version=current, comment="Import")`.
- Endpunkte: `GET /api/settings` (viewer) → `{"version", "created_at", "actor", "source", "comment", "settings", "warnings"}`; `GET /api/settings/schema` (viewer) → `RuntimeSettings.model_json_schema()`; `PUT /api/settings` (admin) `{"base_version", "settings", "comment"?}` → 200 wie GET, 409 `{"detail": <VersionConflictError>}`, 422; `GET /api/settings/versions` (admin) → `[{"version", "created_at", "actor", "source", "comment"}]`; `GET /api/settings/export` (admin) → `text/yaml; charset=utf-8`, `Content-Disposition: attachment; filename="bindaems-einstellungen-v{n}.yaml"`; `POST /api/settings/import` (admin) `{"yaml": "…"}` → 200 wie GET, 422 `{"detail": "…"}`; `GET /api/limits` (viewer) → harte Grenzen aus `config.yaml` ohne Hosts und VIN: `grid` (`phases`, `voltage_nominal_v`, `fuse_a`, `fuse_margin_a`), `battery` (alle Felder), `victron` (`max_grid_charge_setpoint_w`, `persistent_writes`, `watchdog`), `wallboxes` (`type`, Stromgrenzen, `phase_map`, `live_allowed` falls vorhanden), `vehicles` (`name`, `usable_kwh`, `phases`, `min_a`, `max_a`, `default_wallbox`, `live_allowed`).

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/shared/test_settings.py
def test_defaults_follow_spec() -> None:
    s = RuntimeSettings()
    assert (s.tariff.vat_pct, s.tariff.fixed_price_gross_ct) == (20.0, 30.0)
    assert [c.id for c in s.tariff.components] == ["spot", "supplier_markup", "grid_usage", "grid_loss",
                                                    "electricity_tax", "renewables_levy"]
    assert s.tariff.components[1].value_ct == 1.2 and s.tariff.components[1].vat
    snap = s.tariff.components[2].windows[0]
    assert (snap.months, snap.start, snap.end, snap.factor) == ([4, 5, 6, 7, 8, 9], time(10), time(16), 0.8)
    assert (s.prices.vat_mode, s.prices.vat_fallback, s.prices.reference_source) == ("auto", "gross", "energy_charts")
    assert (s.pv_model.performance_ratio, s.pv_model.temp_coeff_pct_per_k, s.pv_model.noct_c) == (0.85, -0.35, 45.0)

def test_exactly_one_spot_component_required() -> None:
    with pytest.raises(ValidationError, match="Börsenpreis"):
        TariffSettings(components=[PriceComponent(id="a", name="A", value_ct=1.0)])

def test_window_needs_exactly_one_of_factor_and_value() -> None:
    with pytest.raises(ValidationError):
        TimeWindow(factor=0.8, value_ct=1.0)
    with pytest.raises(ValidationError):
        TimeWindow()

@pytest.mark.parametrize(("start", "end"), [(time(10, 10), time(16)), (time(16), time(10))])
def test_window_times_on_quarter_hours_and_ordered(start: time, end: time) -> None:
    with pytest.raises(ValidationError):
        TimeWindow(start=start, end=end, factor=1.0)
    assert TimeWindow(start=time(22), end=time(0), factor=1.0).end == time(0)

@pytest.mark.parametrize("monthly", [{"2026-13": 7.0}, {"2026-09": -1.0}])
def test_feed_in_months_and_values_validated(monthly: dict[str, float]) -> None:
    with pytest.raises(ValidationError):
        FeedInSettings(monthly_ct=monthly)

def test_warnings_list_missing_values() -> None:
    assert settings_warnings(RuntimeSettings()) == [
        "Tarifbestandteil „Netznutzungsentgelt Arbeitspreis (Netzebene 7)“ hat noch keinen Wert.",
        "Tarifbestandteil „Netzverlustentgelt“ hat noch keinen Wert.",
        "Tarifbestandteil „Elektrizitätsabgabe“ hat noch keinen Wert.",
        "Tarifbestandteil „Erneuerbaren-Förderbeitrag“ hat noch keinen Wert.",
        "Noch kein OeMAG-Monatswert eingetragen.",
    ]

# tests/unit/app/settings/test_service.py – Fixture service = SettingsService(engine, clock, audit);
# with_grid_usage(s, value) liefert eine Kopie mit grid_usage.value_ct = value
def test_first_start_creates_version_1_with_defaults(service) -> None:
    current = service.current()
    assert (current.version, current.actor, current.settings) == (1, "system", RuntimeSettings())

def test_update_creates_version_and_audits_diff(service, audit) -> None:
    new = with_grid_usage(RuntimeSettings(), 7.12)
    assert service.update(new, base_version=1, actor="chris", source="ui", comment="Netz NÖ 2026").version == 2
    assert service.current().settings == new
    entry = audit.recent()[0]
    assert entry.action == "settings.update"
    assert entry.details["changes"] == [{"path": "tariff.components[2].value_ct", "old": None, "new": 7.12}]

def test_unchanged_settings_create_no_version(service) -> None:
    assert service.update(RuntimeSettings(), base_version=1, actor="chris", source="ui").version == 1

def test_concurrent_update_conflicts(service) -> None:
    service.update(with_grid_usage(RuntimeSettings(), 7.0), base_version=1, actor="a", source="ui")
    with pytest.raises(VersionConflictError) as conflict:
        service.update(with_grid_usage(RuntimeSettings(), 8.0), base_version=1, actor="b", source="ha")
    assert conflict.value.current == 2

def test_listeners_notified_and_their_errors_contained(service) -> None:
    seen: list[int] = []
    service.on_change(lambda v: 1 / 0)
    service.on_change(lambda v: seen.append(v.version))
    service.update(with_grid_usage(RuntimeSettings(), 7.0), base_version=1, actor="a", source="ui")
    assert seen == [2]

def test_export_import_round_trip(service) -> None:
    text = service.export_yaml()
    assert text.startswith("# BindaEMS-Laufzeit-Einstellungen, Version 1\n")
    data = yaml.safe_load(text)
    data["tariff"]["fixed_price_gross_ct"] = 28.5
    imported = service.import_yaml(yaml.safe_dump(data), actor="chris", source="ui")
    assert (imported.version, imported.comment) == (2, "Import")
    assert imported.settings.tariff.fixed_price_gross_ct == 28.5

def test_import_rejects_invalid_yaml(service) -> None:
    with pytest.raises(SettingsImportError, match="YAML muss ein Objekt enthalten"):
        service.import_yaml("- liste\n", actor="chris", source="ui")
    with pytest.raises(SettingsImportError, match=r"tariff\.vat_pct"):
        service.import_yaml("tariff: {vat_pct: 150}\n", actor="chris", source="ui")

def test_invalid_stored_settings_fall_back_to_defaults_with_warning(engine, clock, audit) -> None:
    with engine.begin() as conn:
        conn.execute(insert(settings_version).values(version=1, created_at=T_APP, actor="system",
                                                    source="system", data={"veraltet": 1}))
    service = SettingsService(engine, clock, audit)
    assert (service.current().version, service.current().settings) == (1, RuntimeSettings())
    assert service.warnings()[0] == ("Gespeicherte Einstellungen (Version 1) passen nicht zum Schema "
                                     "– Standardwerte aktiv.")
    service.update(RuntimeSettings(), base_version=1, actor="chris", source="ui")
    assert service.current().version == 2 and not service.warnings()[0].startswith("Gespeicherte")

# tests/unit/app/settings/test_routes.py – client = make_client(settings_router(service, cfg, guard))
def test_viewer_reads_settings_with_warnings(client, auth) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/settings").json()
    assert body["version"] == 1 and body["settings"]["tariff"]["vat_pct"] == 20.0
    assert "Noch kein OeMAG-Monatswert eingetragen." in body["warnings"]

def test_only_admin_updates_and_conflict_is_409(client, auth) -> None:
    csrf = login_as(client, auth, "admin")
    settings = client.get("/api/settings").json()["settings"]
    settings["feed_in"]["monthly_ct"] = {"2026-09": 7.3}
    assert client.put("/api/settings", json={"base_version": 1, "settings": settings},
                      headers=csrf).json()["version"] == 2
    conflict = client.put("/api/settings", json={"base_version": 1, "settings": settings}, headers=csrf)
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "Die Einstellungen wurden inzwischen geändert (aktuell Version 2)."}

def test_operator_cannot_update(client, auth) -> None:
    csrf = login_as(client, auth, "operator")
    response = client.put("/api/settings", json={"base_version": 1, "settings": {}}, headers=csrf)
    assert response.status_code == 403

def test_invalid_settings_are_422(client, auth) -> None:
    csrf = login_as(client, auth, "admin")
    response = client.put("/api/settings", json={"base_version": 1, "settings": {"tariff": {"vat_pct": -1}}},
                          headers=csrf)
    assert response.status_code == 422

def test_export_and_import_via_api(client, auth) -> None:
    csrf = login_as(client, auth, "admin")
    exported = client.get("/api/settings/export")
    assert exported.headers["content-type"].startswith("text/yaml")
    assert 'filename="bindaems-einstellungen-v1.yaml"' in exported.headers["content-disposition"]
    text = exported.text.replace("fixed_price_gross_ct: 30.0", "fixed_price_gross_ct: 29.0")
    assert client.post("/api/settings/import", json={"yaml": text}, headers=csrf).json()["version"] == 2

def test_limits_show_hard_limits_without_hosts_or_vin(client, auth) -> None:
    login_as(client, auth, "viewer")
    limits = client.get("/api/limits").json()
    assert limits["grid"]["fuse_a"] == 35 and limits["battery"]["reserve_soc_pct"] == 20
    assert limits["wallboxes"]["evcs"]["max_a"] == 16
    assert "host" not in json.dumps(limits) and "5YJ3E7EB0MF000000" not in json.dumps(limits)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/shared/test_settings.py tests/unit/app/settings -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.shared.settings'`)

- [ ] **Step 3: Implement the models, migration 0003, the service and the router as specified**

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (22 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared/settings.py src/bindaems/app tests/unit/shared/test_settings.py tests/unit/app/settings
git commit -m "feat(app): versionierte Laufzeit-Einstellungen mit Änderungsprotokoll"
```

---

### Task 7: Verbindung zum core: Client, Live-Zustand, System-API und Live-WebSocket

**Files:**
- Create: `src/bindaems/app/core_link.py`, `src/bindaems/app/status.py`, `src/bindaems/app/api/live.py`
- Test: `tests/integration/test_core_link.py`, `tests/unit/app/test_core_link.py`, `tests/unit/app/api/test_live.py`

**Interfaces:**
- Consumes: `Broadcast`, `Backoff`, `StatusLog` (Task 1); `Guard` (Task 5); `Value` (shared)
- Produces:
  - `bindaems.app.status`: `@dataclass(frozen=True) ComponentStatus(name: str, ok: bool, message: str, since: datetime | None = None, details: Mapping[str, Any] = field(default_factory=dict))`; `StatusSource = Callable[[], ComponentStatus]`
  - `bindaems.app.core_link`:
    - `CoreUnavailableError(Exception)`
    - `class CoreClient(base_url: str, token: SecretStr, http: httpx.AsyncClient)`: `async health() -> dict[str, Any]` (Timeout 5 s); `stream() -> AbstractAsyncContextManager[AsyncIterator[dict[str, Any]]]` (`websockets.asyncio.client.connect(url, additional_headers={"Authorization": f"Bearer {token}"}, proxy=None)`, `http`→`ws`, `https`→`wss`). Netzwerkfehler, HTTP-Fehler, abgelehnter Handshake und Schließcode 4401 werden zu `CoreUnavailableError`.
    - `class LiveState`: Attribute `connected: bool`, `state: dict[str, Any] | None`, `alarms: list[dict[str, Any]]`, `health: dict[str, Any] | None`, `updated_at: datetime | None`; `value(signal: str) -> Value` (nur bei `connected` und `q == "ok"`, sonst `None`); `derived() -> Mapping[str, Any]` (leer, wenn nicht verbunden); `publish(msg: dict[str, Any]) -> None`; `subscribe()` (über `Broadcast`)
    - `SlotFlowsHandler = Callable[[dict[str, Any]], Awaitable[None]]`
    - `class LiveFeed(client: CoreClient, live: LiveState, clock: Clock, *, slot_flows_handlers: Sequence[SlotFlowsHandler] = (), health_interval_s: float = 10.0, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, backoff: Backoff | None = None)`: `async run() -> None`; `component_status() -> ComponentStatus` (Name `core`)
  - `bindaems.app.api.live`: `live_router(live: LiveState, guard: Guard, sources: Sequence[StatusSource], warnings: Callable[[], list[str]]) -> APIRouter`

**Festlegungen:**
- `LiveFeed.run` startet zwei Schleifen:
  - **Stream:** verbinden; erste Nachricht → `connected = True` und `publish({"type": "core", "data": {"connected": True}})`. Je Nachricht: `state` → `live.state`, `updated_at`, weiterreichen; `alarm` → `live.alarms`, weiterreichen; `slot_flows` → alle Handler (Ausnahmen loggen); andere Typen ignorieren. Ende oder Fehler → `connected = False`, `publish({"type": "core", "data": {"connected": False}})`, dann `await sleep(backoff.next())` (1, 2, 4 … 60 s); nach einer Nachricht `backoff.reset()`.
  - **Health:** alle `health_interval_s` `live.health = await client.health()`; Fehler → `None`.
- `component_status`: verbunden → „core verbunden“; sonst „core nicht erreichbar seit {HH:MM}“ (Ortszeit, `since` = Zeitpunkt der Trennung).
- Endpunkte (viewer): `GET /api/state` → `{"core_connected", "updated_at", "state", "alarms"}`; `GET /api/system` → `{"core": {"connected", "health"}, "components": [asdict(status) …], "warnings": warnings()}`.
- `WS /api/live`: annehmen; `Origin` vorhanden und `urlsplit(origin).netloc != Host` → schließen mit 4403; keine Sitzung → 4401; sonst `{"type": "hello", "data": {"core_connected", "state", "alarms"}}`, danach alle Nachrichten aus `live.subscribe()`. Alle 60 s prüft die Route die Sitzung erneut (ohne Verlängerung) und schließt bei Ablauf mit 4401.
- Integrationstest: echter core-API-Server aus `bindaems.core.api.app.create_api` mit einer `FakeView` wie in `tests/unit/core/api/test_api.py` unter uvicorn auf einem freien Port (Muster `test_real_client_sees_4401_not_http_403`).
- Im WebSocket-Test das Sitzungs-Cookie ausdrücklich als Header setzen (`headers={"cookie": "bindaems_session=…"}`); der Test-Client schickt Secure-Cookies nicht über `wss`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_core_link.py – Fixture core_api startet den echten core-API-Server
async def test_client_reads_health_and_stream_from_real_core_api(core_api) -> None:
    async with httpx.AsyncClient() as http:
        client = CoreClient(core_api.url, SecretStr(TOKEN), http)
        assert (await client.health())["mode"] == "OBSERVE"
        async with client.stream() as messages:
            assert (await anext(messages))["type"] == "state"

async def test_wrong_token_is_rejected_by_real_core_api(core_api) -> None:
    async with httpx.AsyncClient() as http:
        client = CoreClient(core_api.url, SecretStr("y" * 32), http)
        with pytest.raises(CoreUnavailableError):
            await client.health()
        with pytest.raises(CoreUnavailableError):
            async with client.stream() as messages:
                await anext(messages)

# tests/unit/app/test_core_link.py – FakeCoreClient spielt je Verbindung eine Nachrichtenliste ab,
# danach endet der Stream; sleeps zeichnet die Wartezeiten auf und bricht nach n Aufrufen ab
async def test_feed_caches_state_and_alarms_and_relays_them(feed_env) -> None:
    feed, live, _ = feed_env([[STATE_MSG, ALARM_MSG]])
    async with live.subscribe() as messages:
        await run_feed_until(feed, lambda: live.alarms == ALARM_MSG["data"] and not live.connected)
        types = [m["type"] async for m in take(messages, 4)]
    assert types == ["core", "state", "alarm", "core"]
    assert live.state == STATE_MSG["data"]

async def test_slot_flows_go_to_handlers_and_handler_errors_are_contained(feed_env) -> None:
    received: list[dict[str, Any]] = []
    async def failing(data: dict[str, Any]) -> None:
        raise RuntimeError("kaputt")
    async def collect(data: dict[str, Any]) -> None:
        received.append(data)
    feed, live, _ = feed_env([[SLOT_MSG, STATE_MSG]], handlers=[failing, collect])
    await run_feed_until(feed, lambda: live.state is not None)
    assert received == [SLOT_MSG["data"]]

async def test_disconnect_marks_state_stale_and_notifies(feed_env) -> None:
    feed, live, _ = feed_env([[STATE_MSG]])
    await run_feed_until(feed, lambda: live.state is not None and not live.connected)
    assert live.value("battery.soc_pct") is None and live.derived() == {}
    assert live.state == STATE_MSG["data"]  # bleibt zur Anzeige erhalten
    assert feed.component_status().message.startswith("core nicht erreichbar seit")

async def test_reconnect_uses_backoff(feed_env) -> None:
    feed, _, sleeps = feed_env([[], [], []])  # dreimal sofort getrennt
    await run_feed_until(feed, lambda: len(sleeps) >= 3)
    assert sleeps[:3] == [1.0, 2.0, 4.0]

def test_value_only_for_ok_quality_while_connected() -> None:
    live = LiveState()
    live.connected = True
    live.state = {"signals": {"battery.soc_pct": {"v": 55.0, "ts": "…", "q": "ok"},
                              "grid.power_w": {"v": 100.0, "ts": "…", "q": "stale"}}, "derived": {}}
    assert live.value("battery.soc_pct") == 55.0 and live.value("grid.power_w") is None

async def test_health_polled_and_cleared_on_failure(feed_env) -> None:
    feed, live, _ = feed_env([[STATE_MSG]], health=[{"mode": "OBSERVE"}, CoreUnavailableError("weg")])
    await run_feed_until(feed, lambda: live.health == {"mode": "OBSERVE"})
    await run_feed_until(feed, lambda: live.health is None)

# tests/unit/app/api/test_live.py – client = make_client(live_router(live, guard, [ok_source], lambda: ["W1"]))
def test_state_requires_login_and_returns_cache(client, auth, live) -> None:
    assert client.get("/api/state").status_code == 401
    login_as(client, auth, "viewer")
    body = client.get("/api/state").json()
    assert body["core_connected"] is True and body["state"] == live.state

def test_system_lists_components_and_warnings(client, auth) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/system").json()
    assert body["components"][0]["name"] == "prices" and body["warnings"] == ["W1"]

def test_live_websocket_sends_hello_then_relays(client, auth, live) -> None:
    login_as(client, auth, "viewer")
    cookie = f"bindaems_session={client.cookies['bindaems_session']}"
    with client.websocket_connect("/api/live", headers={"cookie": cookie}) as ws:
        assert ws.receive_json()["type"] == "hello"
        live.publish({"type": "state", "data": {"x": 1}})
        assert ws.receive_json() == {"type": "state", "data": {"x": 1}}

def test_live_websocket_rejects_missing_session(client) -> None:
    with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect("/api/live") as ws:
        ws.receive_json()
    assert closed.value.code == 4401

def test_live_websocket_rejects_foreign_origin(client, auth) -> None:
    login_as(client, auth, "viewer")
    headers = {"cookie": f"bindaems_session={client.cookies['bindaems_session']}",
               "origin": "https://boese.example"}
    with pytest.raises(WebSocketDisconnect) as closed, client.websocket_connect("/api/live", headers=headers) as ws:
        ws.receive_json()
    assert closed.value.code == 4403
```

`STATE_MSG`, `ALARM_MSG` und `SLOT_MSG` sind Beispielnachrichten der Typen `state`, `alarm` und `slot_flows` im core-Format. `feed_env`, `run_feed_until` (startet `feed.run()` als Task bis zur Bedingung, wie `run_until` in `tests/helpers.py`) und `take` gehören in `tests/unit/app/test_core_link.py`. In `test_live.py` ist `live` ein verbundener `LiveState` mit einem Beispielzustand und `ok_source` liefert `ComponentStatus("prices", True, "Preise bis 10.10. 23:45")`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/integration/test_core_link.py tests/unit/app/test_core_link.py tests/unit/app/api/test_live.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.app.core_link'`)

- [ ] **Step 3: Implement `CoreClient`, `LiveState`, `LiveFeed`, `ComponentStatus` and `live_router` as specified**

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (13 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests/integration/test_core_link.py tests/unit/app
git commit -m "feat(app): Verbindung zum core, Live-Zustand und System-API"
```

---

### Task 8: InfluxDB-Leser und Verlaufs-API

**Files:**
- Create: `src/bindaems/app/history/{__init__,influx,series,routes}.py`
- Test: `tests/unit/app/history/test_influx.py`, `tests/unit/app/history/test_series.py`, `tests/unit/app/history/test_routes.py`

**Interfaces:**
- Consumes: `InfluxConfig`, `Config` (shared); `Guard` (Task 5); `epoch_ms` (Task 1)
- Produces:
  - `bindaems.app.history.influx`: `RP_RAW = "raw"`, `RP_LONG = "long"`; `@dataclass(frozen=True) Series(name: str, tags: Mapping[str, str], columns: list[str], values: list[list[Any]])`; `InfluxQueryError(Exception)`; `class InfluxReader(cfg: InfluxConfig, password: SecretStr | None, http: httpx.AsyncClient)` mit `async query(q: str, *, database: str | None = None) -> list[Series]` (`GET {url}/query`, Parameter `db` (Default `cfg.database`), `q`, `epoch=ms`; Basic Auth wie der Schreiber; Timeout 15 s; Netzwerkfehler, Status ≠ 200 oder `"error"` in einem Ergebnis → `InfluxQueryError`); `quote_ident(name: str) -> str`, `quote_str(value: str) -> str` (Backslash und das jeweilige Anführungszeichen mit `\` maskiert)
  - `bindaems.app.history.series`: `@dataclass(frozen=True) SeriesSpec(id: str, label: str, unit: str, measurement: str, field: str, tags: Mapping[str, str], raw: bool = True, scale: float = 1.0)`; `build_catalog(cfg: Config) -> dict[str, SeriesSpec]`; `choose_rp_and_step(spec: SeriesSpec, start: datetime, end: datetime, now: datetime) -> tuple[str, int]`; `build_query(spec: SeriesSpec, rp: str, start: datetime, end: datetime, step_s: int) -> str`
  - `bindaems.app.history.routes`: `history_router(reader: InfluxReader, catalog: Mapping[str, SeriesSpec], clock: Clock, guard: Guard) -> APIRouter`

**Festlegungen:**
- Katalog:

  | id | label | unit | measurement.field | Tags | raw | scale |
  |---|---|---|---|---|---|---|
  | `grid` | Netz | W | `power.p_w` | source=grid, id=grid, phase=total | ja | 1 |
  | `pv` | PV | W | `power.p_w` | source=derived, id=pv_total, phase=total | ja | 1 |
  | `battery` | Akku | W | `power.p_w` | source=battery, id=battery, phase=total | ja | 1 |
  | `house` | Haus | W | `power.p_w` | source=derived, id=house_load, phase=total | ja | 1 |
  | `consumption` | Verbrauch gesamt | W | `power.p_w` | source=derived, id=consumption, phase=total | ja | 1 |
  | `wallbox.<name>` | Wallbox <name> | W | `power.p_w` | source=wallbox, id=<name>, phase=total | ja | 1 |
  | `soc.battery` | Akku-SOC | % | `soc.pct` | id=battery | ja | 1 |
  | `soc.<fahrzeug>` | SOC <vehicle.name> | % | `soc.pct` | id=<fahrzeug> | ja | 1 |
  | `price` | Bezugspreis | ct/kWh | `price.ct_kwh` | kind=import_gross | nein | 1 |
  | `forecast.pv` | PV-Prognose | W | `forecast.kw` | kind=pv, quantile=p50 | nein | 1000 |

- `choose_rp_and_step`: `raw`, wenn `spec.raw`, `end − start ≤ 2 d` und `start ≥ now − 89 d`, sonst `long`. Schritt = kleinster Wert aus `[10, 30, 60, 300, 900, 3600, 21600, 86400]` mit `(end − start)/step ≤ 1000`, bei `long` mindestens 60.
- `build_query`: `SELECT mean("<field>") AS "v" FROM "<rp>"."<measurement>" WHERE <tags nach Schlüssel sortiert als "k"='v', mit AND verbunden> AND time >= <start_ms>ms AND time < <end_ms>ms GROUP BY time(<step>s) fill(none)`
- `GET /api/history?series=grid,pv&from=<ISO>&to=<ISO>` (viewer): höchstens 8 Reihen; unbekannte Reihe → 422 „Unbekannte Reihe: {id}“; Zeiten ohne Zeitzone, `from ≥ to` oder Spanne > 400 d → 422; InfluxDB-Fehler → 502 „InfluxDB nicht erreichbar“. Antwort `{"rp", "step_s", "series": {id: {"label", "unit", "points": [[ms, wert × scale], …]}}}` ohne `null`-Werte. Je Reihe eine Abfrage.
- `GET /api/history/catalog` (viewer) → `[{"id", "label", "unit"}]` in Katalogreihenfolge.

- [ ] **Step 1: Write the failing tests**

```python
T0 = datetime(2026, 10, 9, tzinfo=UTC)  # 1791504000000 ms

async def test_query_parses_series_and_sends_auth(respx_mock, reader) -> None:
    route = respx_mock.get("http://influx.lan:8086/query").respond(json={"results": [{"statement_id": 0,
        "series": [{"name": "power", "tags": {"id": "grid"}, "columns": ["time", "v"],
                    "values": [[1791504000000, 512.5]]}]}]})
    assert await reader.query("SELECT 1", database="bindaems") == [
        Series("power", {"id": "grid"}, ["time", "v"], [[1791504000000, 512.5]])]
    request = route.calls.last.request
    assert (request.url.params["db"], request.url.params["epoch"]) == ("bindaems", "ms")
    assert request.headers["authorization"] == "Basic " + base64.b64encode(b"bindaems:geheim").decode()

async def test_error_in_result_raises(respx_mock, reader) -> None:
    respx_mock.get("http://influx.lan:8086/query").respond(json={"results": [{"error": "database not found"}]})
    with pytest.raises(InfluxQueryError, match="database not found"):
        await reader.query("SELECT 1")

async def test_http_error_raises(respx_mock, reader) -> None:
    respx_mock.get("http://influx.lan:8086/query").respond(401)
    with pytest.raises(InfluxQueryError):
        await reader.query("SELECT 1")

def test_quoting_escapes_quotes_and_backslashes() -> None:
    assert quote_str("it's\\") == "'it\\'s\\\\'"
    assert quote_ident('a"b') == '"a\\"b"'

def test_catalog_from_config(cfg) -> None:
    catalog = build_catalog(cfg)
    assert list(catalog) == ["grid", "pv", "battery", "house", "consumption", "wallbox.evcs", "wallbox.twc",
                             "soc.battery", "soc.tesla", "soc.egolf", "price", "forecast.pv"]
    assert catalog["soc.egolf"].label == "SOC e-Golf"

def test_query_text_for_one_hour(cfg) -> None:
    spec = build_catalog(cfg)["grid"]
    assert build_query(spec, "raw", T0, T0 + timedelta(hours=1), 10) == (
        'SELECT mean("p_w") AS "v" FROM "raw"."power" WHERE "id"=\'grid\' AND "phase"=\'total\' '
        "AND \"source\"='grid' AND time >= 1791504000000ms AND time < 1791507600000ms "
        "GROUP BY time(10s) fill(none)")

def test_choose_rp_and_step(cfg) -> None:
    catalog, now = build_catalog(cfg), T0 + timedelta(days=1)
    grid, price = catalog["grid"], catalog["price"]
    assert choose_rp_and_step(grid, T0, T0 + timedelta(hours=1), now) == ("raw", 10)
    assert choose_rp_and_step(grid, T0, T0 + timedelta(days=1), now) == ("raw", 300)
    assert choose_rp_and_step(grid, T0, T0 + timedelta(days=3), now) == ("long", 300)
    assert choose_rp_and_step(grid, T0 - timedelta(days=100), T0 - timedelta(days=100) + timedelta(hours=1),
                              now) == ("long", 60)
    assert choose_rp_and_step(price, T0, T0 + timedelta(hours=1), now) == ("long", 60)

def test_history_returns_scaled_points(client, auth, respx_mock) -> None:
    login_as(client, auth, "viewer")
    respx_mock.get("http://influx.lan:8086/query").respond(json={"results": [{"series": [{"name": "forecast",
        "columns": ["time", "v"], "values": [[1791504000000, 1.5], [1791504060000, None]]}]}]})
    body = client.get("/api/history", params={"series": "forecast.pv", "from": "2026-10-09T00:00:00Z",
                                              "to": "2026-10-09T01:00:00Z"}).json()
    assert (body["rp"], body["step_s"]) == ("long", 60)
    assert body["series"]["forecast.pv"] == {"label": "PV-Prognose", "unit": "W", "points": [[1791504000000, 1500.0]]}

@pytest.mark.parametrize("params", [
    {"series": "gibts", "from": "2026-10-09T00:00:00Z", "to": "2026-10-09T01:00:00Z"},
    {"series": "grid", "from": "2026-10-09T01:00:00Z", "to": "2026-10-09T00:00:00Z"},
    {"series": "grid", "from": "2025-01-01T00:00:00Z", "to": "2026-10-09T00:00:00Z"},
    {"series": "grid", "from": "2026-10-09T00:00:00", "to": "2026-10-09T01:00:00"},
])
def test_bad_requests_are_422(client, auth, params) -> None:
    login_as(client, auth, "viewer")
    assert client.get("/api/history", params=params).status_code == 422

def test_influx_down_is_502(client, auth, respx_mock) -> None:
    login_as(client, auth, "viewer")
    respx_mock.get("http://influx.lan:8086/query").mock(side_effect=httpx.ConnectError("weg"))
    response = client.get("/api/history", params={"series": "grid", "from": "2026-10-09T00:00:00Z",
                                                  "to": "2026-10-09T01:00:00Z"})
    assert response.status_code == 502 and response.json() == {"detail": "InfluxDB nicht erreichbar"}

def test_catalog_endpoint(client, auth) -> None:
    login_as(client, auth, "viewer")
    assert client.get("/api/history/catalog").json()[0] == {"id": "grid", "label": "Netz", "unit": "W"}
```

Fixtures: `reader` = `InfluxReader(cfg.influxdb, SecretStr("geheim"), http)`; `client` = `make_client(history_router(reader, build_catalog(cfg), clock, guard))`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/history -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.app.history'`)

- [ ] **Step 3: Implement reader, catalog and router as specified**

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (14 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/history tests/unit/app/history
git commit -m "feat(app): InfluxDB-Leser und Verlaufs-API"
```

---

### Task 9: Verbraucher mit Hierarchie und „Sonstiges“

**Files:**
- Create: `src/bindaems/app/consumers/{__init__,service,values,routes}.py`, `src/bindaems/app/db/migrations/versions/v0004_consumers.py`, `tests/property/test_consumer_tree.py`
- Modify: `src/bindaems/app/db/schema.py`
- Test: `tests/unit/app/consumers/test_service.py`, `tests/unit/app/consumers/test_values.py`, `tests/unit/app/consumers/test_routes.py`

**Interfaces:**
- Consumes: `AuditLog` (Task 3); `Guard` (Task 5); `LiveState` (Task 7); `InfluxReader`, `quote_str`, `quote_ident` (Task 8); `Config`
- Produces:
  - Tabelle `consumer` (`id` Integer PK, `name` String(60) not null, `group_name` String(60) null, `parent_id` FK `consumer.id` ON DELETE RESTRICT null, `color` String(7) not null, `source_kind` String(8) not null, `power_ref` String(200) not null, `power_unit` String(4) null, `sort` Integer not null, `created_at`, `updated_at`)
  - `class ConsumerInput(BaseModel, extra="forbid")`: `name` (1–60, getrimmt), `group: str | None = None`, `parent_id: int | None = None`, `color` (`^#[0-9a-fA-F]{6}$`), `source_kind: Literal["core", "ha"]`, `power_ref: str`, `power_unit: Literal["W", "kW"] | None = None`, `sort: int = 0`. Prüfung: `core` → `power_ref` passt auf `^[a-z0-9_]+(\.[a-z0-9_]+)*\.power_w$`, `power_unit` ist `None`; `ha` → `power_ref` passt auf `^[a-z_]+\.[a-z0-9_]+$`, `power_unit` gesetzt.
  - `@dataclass(frozen=True) Consumer(id: int, name: str, group: str | None, parent_id: int | None, color: str, source_kind: str, power_ref: str, power_unit: str | None, sort: int)`
  - `ConsumerError(message: str, status: int)`
  - `class ConsumerService(engine: Engine, clock: Clock, audit: AuditLog, *, ha_enabled: bool)`: `list() -> list[Consumer]` (nach `sort`, `name`), `get(consumer_id: int) -> Consumer`, `create(data: ConsumerInput, *, actor: str, source: Source) -> Consumer`, `update(consumer_id: int, data: ConsumerInput, *, actor: str, source: Source) -> Consumer`, `delete(consumer_id: int, *, actor: str, source: Source) -> None`
  - `bindaems.app.consumers.values`:
    - `@dataclass(frozen=True) HaRef(domain: str, object_id: str, unit: Literal["W", "kW"])` mit `from_consumer(c: Consumer) -> HaRef`
    - `class HaValueCache(reader: InfluxReader, database: str)`: `async refresh(refs: Sequence[HaRef]) -> None`, `power_w(ref: HaRef) -> float | None`
    - `@dataclass TreeNode(id: int | None, name: str, color: str | None, power_w: float | None, other_w: float | None, mismatch: bool, children: list[TreeNode])`
    - `build_tree(consumers: Sequence[Consumer], power: Callable[[Consumer], float | None], house_w: float | None) -> TreeNode`
    - `consumer_power(consumer: Consumer, live: LiveState, ha: HaValueCache | None) -> float | None`
    - `async candidates(cfg: Config, live: LiveState, reader: InfluxReader | None) -> dict[str, list[dict[str, str]]]`
  - `consumers_router(service: ConsumerService, live: LiveState, ha: HaValueCache | None, reader: InfluxReader | None, cfg: Config, guard: Guard) -> APIRouter`

**Festlegungen:**
- Fehler: Elternelement fehlt → 422 „Elternelement {id} existiert nicht“; Zyklus → 422 „Ein Verbraucher kann nicht unter sich selbst hängen“; Löschen mit Kindern → 409 „Verbraucher hat Unterverbraucher“; unbekannt → 404 „Verbraucher nicht gefunden“; `ha` ohne `influxdb.ha_database` → 422 „HA-Datenbank (influxdb.ha_database) ist nicht konfiguriert“. Protokoll `consumer.create`/`consumer.update`/`consumer.delete` mit `target` = Name.
- `HaValueCache.refresh`: eine Abfrage an `database`:
  `SELECT last("value") AS "v" FROM <Einheiten der Refs, sortiert, als Bezeichner, Komma-getrennt> WHERE time > now() - 24h AND (<je Ref ("domain"='…' AND "entity_id"='…'), mit OR verbunden>) GROUP BY "domain","entity_id"`.
  Serienname = Einheit; `kW` × 1000. Ein Wert zählt nur, wenn die Serie zur Einheit des Refs passt. Fehler → Cache leeren, Warnung loggen.
- `build_tree`: Wurzel `id=None`, Name „Haus“, Leistung `house_w` (core `derived.house_load_w`). Kinder der Wurzel sind Verbraucher ohne Elternelement. Jeder Knoten mit Kindern: `other_w = power_w − Σ Kinder`, wenn alle bekannt sind, sonst `None`; negativ → `other_w = 0.0`, `mismatch = True`. Blätter: `other_w = None`.
- `candidates`: `core` = Signale aus `live.state` auf `^load\.[^.]+\.power_w$` plus Schlüssel aus `cfg.homeassistant.entities`, die auf `.power_w` enden (sortiert, ohne Duplikate) als `{"ref": …}`; `ha` = Ergebnis von `SHOW SERIES FROM "W","kW"` in `ha_database`, Schlüssel wie `W,domain=sensor,entity_id=kueche` (an unmaskierten Kommas getrennt) → `{"entity_id": "sensor.kueche", "unit": "W"}`, sortiert; ohne Reader oder bei Fehler leer.
- Endpunkte: `GET /api/consumers` (viewer) → `{"tree": <TreeNode als JSON>, "consumers": [flache Liste]}`; `GET /api/consumers/candidates`, `POST /api/consumers` (201), `PATCH /api/consumers/{id}`, `DELETE /api/consumers/{id}` (204) nur für Admins. Für den Baum nutzt die Route den letzten Stand von `HaValueCache` (aufgefrischt wird im Hintergrund, Task 18).

- [ ] **Step 1: Write the failing tests**

```python
def k(id_: int, power: float | None, parent: int | None = None) -> tuple[Consumer, float | None]: ...
# erzeugt einen Verbraucher mit power_ref f"load.v{id_}.power_w" und der zugehörigen Leistung;
# tree_input(paare) -> (consumers, power) mit power(c) = Leistung aus dem Paar;
# core_input(name, parent_id=None, ref="load.x.power_w") -> ConsumerInput mit source_kind "core"

def test_create_list_update_delete_with_audit(service, audit) -> None:
    parent = service.create(ConsumerInput(name="Obergeschoss", color="#336699", source_kind="core",
                                          power_ref="load.obergeschoss.power_w"), actor="chris", source="ui")
    child = service.create(ConsumerInput(name="Küche", color="#aa0000", source_kind="ha", parent_id=parent.id,
                                         power_ref="sensor.kueche", power_unit="W"), actor="chris", source="ui")
    service.update(child.id, ConsumerInput(name="Küche OG", color="#aa0000", source_kind="ha", parent_id=parent.id,
                                           power_ref="sensor.kueche", power_unit="W"), actor="chris", source="ui")
    assert [c.name for c in service.list()] == ["Küche OG", "Obergeschoss"]
    service.delete(child.id, actor="chris", source="ui")
    assert [e.action for e in audit.recent()] == ["consumer.delete", "consumer.update", "consumer.create",
                                                  "consumer.create"]

def test_parent_must_exist_and_no_cycles(service) -> None:
    with pytest.raises(ConsumerError, match="Elternelement 99 existiert nicht"):
        service.create(core_input("A", parent_id=99), actor="chris", source="ui")
    a = service.create(core_input("A"), actor="chris", source="ui")
    b = service.create(core_input("B", parent_id=a.id), actor="chris", source="ui")
    with pytest.raises(ConsumerError, match="unter sich selbst"):
        service.update(a.id, core_input("A", parent_id=b.id), actor="chris", source="ui")

def test_delete_with_children_is_rejected(service) -> None:
    a = service.create(core_input("A"), actor="chris", source="ui")
    service.create(core_input("B", parent_id=a.id), actor="chris", source="ui")
    with pytest.raises(ConsumerError) as error:
        service.delete(a.id, actor="chris", source="ui")
    assert (error.value.status, str(error.value)) == (409, "Verbraucher hat Unterverbraucher")

@pytest.mark.parametrize("data", [
    {"source_kind": "core", "power_ref": "load.og.energy_kwh"},
    {"source_kind": "ha", "power_ref": "sensor.kueche"},
    {"source_kind": "core", "power_ref": "load.og.power_w", "power_unit": "W"},
])
def test_source_validation(data) -> None:
    with pytest.raises(ValidationError):
        ConsumerInput(name="X", color="#000000", **data)

def test_ha_consumer_needs_ha_database(engine, clock, audit) -> None:
    service = ConsumerService(engine, clock, audit, ha_enabled=False)
    with pytest.raises(ConsumerError, match="ha_database"):
        service.create(ConsumerInput(name="X", color="#000000", source_kind="ha", power_ref="sensor.x",
                                     power_unit="W"), actor="chris", source="ui")

async def test_ha_values_single_query_and_kw_scaling(respx_mock, reader) -> None:
    route = respx_mock.get("http://influx.lan:8086/query").respond(json={"results": [{"series": [
        {"name": "W", "tags": {"domain": "sensor", "entity_id": "kueche"}, "columns": ["time", "v"],
         "values": [[1791504000000, 120.0]]},
        {"name": "kW", "tags": {"domain": "sensor", "entity_id": "wp"}, "columns": ["time", "v"],
         "values": [[1791504000000, 1.5]]}]}]})
    cache = HaValueCache(reader, "homeassistant")
    kueche, wp = HaRef("sensor", "kueche", "W"), HaRef("sensor", "wp", "kW")
    await cache.refresh([kueche, wp])
    assert (cache.power_w(kueche), cache.power_w(wp)) == (120.0, 1500.0)
    request = route.calls.last.request
    assert request.url.params["db"] == "homeassistant"
    assert request.url.params["q"] == (
        'SELECT last("value") AS "v" FROM "W","kW" WHERE time > now() - 24h AND '
        "((\"domain\"='sensor' AND \"entity_id\"='kueche') OR (\"domain\"='sensor' AND \"entity_id\"='wp')) "
        'GROUP BY "domain","entity_id"')

def test_tree_computes_other_per_level() -> None:
    consumers, power = tree_input([k(1, 800.0), k(2, 300.0, parent=1), k(3, 200.0, parent=1), k(4, 500.0)])
    root = build_tree(consumers, power, house_w=2000.0)
    assert (root.name, root.other_w) == ("Haus", 700.0)
    assert root.children[0].other_w == 300.0 and root.children[0].children[0].other_w is None

def test_tree_negative_other_is_clamped_and_flagged() -> None:
    consumers, power = tree_input([k(1, 1500.0), k(2, 900.0)])
    root = build_tree(consumers, power, house_w=2000.0)
    assert (root.other_w, root.mismatch) == (0.0, True)

def test_tree_unknown_value_makes_other_unknown() -> None:
    consumers, power = tree_input([k(1, None), k(2, 900.0)])
    assert build_tree(consumers, power, house_w=2000.0).other_w is None

async def test_candidates_from_core_signals_and_ha_series(cfg, live, respx_mock, reader) -> None:
    respx_mock.get("http://influx.lan:8086/query").respond(json={"results": [{"series": [{"columns": ["key"],
        "values": [["W,domain=sensor,entity_id=kueche"], ["kW,domain=sensor,entity_id=wp"]]}]}]})
    result = await candidates(cfg, live, reader)
    assert result["core"] == [{"ref": "load.obergeschoss.power_w"}]
    assert result["ha"] == [{"entity_id": "sensor.kueche", "unit": "W"}, {"entity_id": "sensor.wp", "unit": "kW"}]

def test_tree_endpoint_with_live_values(client, auth, service, live) -> None:
    login_as(client, auth, "viewer")
    service.create(core_input("OG", ref="load.obergeschoss.power_w"), actor="chris", source="ui")
    tree = client.get("/api/consumers").json()["tree"]
    assert tree["power_w"] == live.derived()["house_load_w"] and tree["children"][0]["name"] == "OG"

def test_changes_are_admin_only(client, auth) -> None:
    csrf = login_as(client, auth, "operator")
    body = {"name": "X", "color": "#000000", "source_kind": "core", "power_ref": "load.x.power_w"}
    assert client.post("/api/consumers", json=body, headers=csrf).status_code == 403

# tests/property/test_consumer_tree.py
@given(st.lists(st.floats(min_value=0, max_value=5000), min_size=1, max_size=8))
def test_children_plus_other_equal_parent_when_consistent(children: list[float]) -> None:
    consumers, power = tree_input([k(i + 1, p) for i, p in enumerate(children)])
    house = sum(children) + 100.0
    root = build_tree(consumers, power, house_w=house)
    assert root.other_w == pytest.approx(100.0) and not root.mismatch
```

Fixture `live`: verbundener `LiveState` mit `state = {"signals": {"load.obergeschoss.power_w": {"v": 640.0, "ts": "…", "q": "ok"}}, "derived": {"house_load_w": 2100.0}}`. `client` = `make_client(consumers_router(service, live, None, reader, cfg, guard))`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/consumers tests/property/test_consumer_tree.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.app.consumers'`)

- [ ] **Step 3: Implement migration 0004, service, values and router as specified**

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (15 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests/unit/app/consumers tests/property/test_consumer_tree.py
git commit -m "feat(app): Verbraucher mit Hierarchie und „Sonstiges“"
```

---

### Task 10: Tarifmodell v1: Bezugs- und Einspeisepreis je Slot

**Files:**
- Create: `src/bindaems/app/tariff/{__init__,calc}.py`
- Test: `tests/unit/app/tariff/test_calc.py`

**Interfaces:**
- Consumes: `TariffSettings`, `PriceComponent`, `TimeWindow`, `FeedInSettings` (Task 6); `LOCAL_TZ`, `ensure_utc`
- Produces:
  - `@dataclass(frozen=True) ImportPrice(net_ct: float, gross_ct: float, parts_net: Mapping[str, float], missing: tuple[str, ...])`
  - `window_applies(window: TimeWindow, local_start: datetime) -> bool`: Monat ∈ `months`, `weekday()` ∈ `weekdays`, `start ≤ Uhrzeit < end` (`end == 00:00` = 24:00)
  - `component_net_ct(component: PriceComponent, slot_start: datetime, spot_net_ct: float) -> float | None`: `None`, wenn das lokale Datum des Slots außerhalb `valid_from … valid_until` liegt; Basis = `spot_net_ct` (bei `source="spot"`) oder `value_ct` (`None` zählt als 0); das erste passende Fenster ersetzt sie durch `Basis × factor` bzw. `value_ct` des Fensters
  - `import_price(slot_start: datetime, spot_net_ct: float, tariff: TariffSettings) -> ImportPrice`: `gross = Σ (teil × (1 + vat_pct/100), wenn vat, sonst teil)`; `missing` = IDs fester Bestandteile ohne Wert, die am Slot gelten und kein Fenster mit `value_ct` trifft
  - `feed_in_price(slot_start: datetime, feed_in: FeedInSettings) -> float | None`: Wert des jüngsten Monats `≤` lokaler Monat des Slots, sonst `None`

- [ ] **Step 1: Write the failing tests**

```python
def tariff() -> TariffSettings:
    """Standardbestandteile mit Werten: grid_usage 8.0, grid_loss 0.6, electricity_tax 1.5, renewables_levy 0.5."""

def tariff_with_extra(component: PriceComponent) -> TariffSettings:
    """tariff() plus ein zusätzlicher Bestandteil."""

def at(iso: str) -> datetime:
    return datetime.fromisoformat(iso)

def test_import_price_sums_components_with_vat() -> None:
    price = import_price(at("2026-10-09T08:00:00+00:00"), 10.0, tariff())
    assert price.net_ct == pytest.approx(21.8) and price.gross_ct == pytest.approx(26.16)
    assert price.parts_net["grid_usage"] == 8.0 and price.missing == ()

def test_snap_factor_in_summer_window() -> None:  # 10:00 MESZ
    assert import_price(at("2026-07-15T08:00:00+00:00"), 10.0, tariff()).net_ct == pytest.approx(20.2)

def test_snap_window_end_is_exclusive_in_local_time() -> None:  # 16:00 MESZ
    assert import_price(at("2026-07-15T14:00:00+00:00"), 10.0, tariff()).net_ct == pytest.approx(21.8)

def test_snap_starts_on_first_of_april() -> None:
    assert import_price(at("2026-04-01T08:00:00+00:00"), 10.0, tariff()).net_ct == pytest.approx(20.2)

def test_snap_not_in_october() -> None:
    assert import_price(at("2026-10-01T08:00:00+00:00"), 10.0, tariff()).net_ct == pytest.approx(21.8)

def test_validity_dates_use_local_date() -> None:
    t = tariff_with_extra(PriceComponent(id="alt", name="Alt", value_ct=1.0, valid_until=date(2026, 12, 31)))
    assert import_price(at("2026-12-31T22:45:00+00:00"), 10.0, t).parts_net.get("alt") == 1.0  # 23:45 Ortszeit
    assert "alt" not in import_price(at("2026-12-31T23:00:00+00:00"), 10.0, t).parts_net    # 00:00 am 1. 1.

def test_component_without_vat() -> None:
    t = tariff_with_extra(PriceComponent(id="frei", name="Frei", value_ct=1.0, vat=False))
    assert import_price(at("2026-10-09T08:00:00+00:00"), 10.0, t).gross_ct == pytest.approx(27.16)

def test_missing_values_are_reported() -> None:
    price = import_price(at("2026-10-09T08:00:00+00:00"), 10.0, TariffSettings())
    assert price.missing == ("grid_usage", "grid_loss", "electricity_tax", "renewables_levy")
    assert price.net_ct == pytest.approx(11.2)

def test_negative_spot_passes_through() -> None:
    assert import_price(at("2026-10-09T08:00:00+00:00"), -5.0, tariff()).net_ct == pytest.approx(6.8)

def test_window_with_absolute_value() -> None:
    window = TimeWindow(start=time(0), end=time(6), value_ct=4.0)
    t = tariff_with_extra(PriceComponent(id="nacht", name="Nacht", value_ct=2.0, windows=[window]))
    assert import_price(at("2026-10-09T02:00:00+00:00"), 10.0, t).parts_net["nacht"] == 4.0   # 04:00
    assert import_price(at("2026-10-09T08:00:00+00:00"), 10.0, t).parts_net["nacht"] == 2.0   # 10:00

def test_feed_in_uses_latest_month_not_after_slot() -> None:
    feed_in = FeedInSettings(monthly_ct={"2026-08": 6.1, "2026-09": 7.3})
    assert feed_in_price(at("2026-10-09T08:00:00+00:00"), feed_in) == 7.3
    assert feed_in_price(at("2026-08-31T21:45:00+00:00"), feed_in) == 6.1   # 23:45 am 31. 8.
    assert feed_in_price(at("2026-07-10T08:00:00+00:00"), feed_in) is None

def test_feed_in_month_boundary_in_local_time() -> None:
    feed_in = FeedInSettings(monthly_ct={"2026-08": 6.1, "2026-09": 7.3})
    assert feed_in_price(at("2026-08-31T22:00:00+00:00"), feed_in) == 7.3   # 00:00 am 1. 9.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/tariff -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.app.tariff'`)

- [ ] **Step 3: Implement `calc.py` as specified**

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (12 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/tariff tests/unit/app/tariff
git commit -m "feat(app): Tarifmodell v1 mit SNAP, Gültigkeit und USt"
```

---

### Task 11: Preisquellen: smartENERGY, Energy-Charts, aWATTar

**Files:**
- Create: `src/bindaems/app/fetch.py`, `src/bindaems/app/prices/{__init__,sources}.py`
- Use (liegen bei): `tests/fixtures/smartenergy_2026-10-09.json`, `tests/fixtures/energycharts_at_2026-10-09.json`, `tests/fixtures/awattar_2026-10-09.json`
- Test: `tests/unit/app/prices/test_sources.py`

**Interfaces:**
- Consumes: `Clock`, `bindaems.__version__`
- Produces:
  - `bindaems.app.fetch`: `@dataclass(frozen=True) RawResponse(source: str, url: str, fetched_at: datetime, status: int, body: str)` mit Eigenschaft `ok` (2xx); `FetchError(Exception)`; `async get_raw(http: httpx.AsyncClient, clock: Clock, source: str, url: str, params: Mapping[str, str | int | float]) -> RawResponse` (Timeout 15 s, Header `User-Agent: BindaEMS/<version>`; HTTP-Fehlerstatus wird zurückgegeben, Netzwerkfehler → `FetchError("{source}: {Typ}: {Text}")`)
  - `bindaems.app.prices.sources`:
    - `@dataclass(frozen=True) PriceInterval(start: datetime, end: datetime, ct_kwh: float)` (UTC, `start < end`)
    - `PriceParseError(Exception)`
    - `class SmartEnergySource(http, clock)`: `name = "smartenergy"`, `URL = "https://apis.smartenergy.at/market/v1/price"`, `async fetch() -> RawResponse`, `parse(body: str) -> list[PriceInterval]`
    - `class ReferenceSource(Protocol)`: `name: str`; `async fetch(start: datetime, end: datetime) -> RawResponse`; `parse(body: str) -> list[PriceInterval]`
    - `class EnergyChartsSource(http, clock)`: `name = "energy_charts"`, `URL = "https://api.energy-charts.info/price"`
    - `class AwattarSource(http, clock)`: `name = "awattar"`, `URL = "https://api.awattar.at/v1/marketdata"`
    - `make_reference(name: Literal["energy_charts", "awattar"], http: httpx.AsyncClient, clock: Clock) -> ReferenceSource`

**Festlegungen:**
- smartENERGY: JSON-Objekt; `unit` muss `ct/kWh` sein („smartENERGY: unbekannte Einheit „{unit}““); `interval` positive ganze Zahl in Minuten; jeder Eintrag `date` (ISO mit Offset, sonst „smartENERGY: Zeitpunkt ohne Zeitzone: {date}“) und `value` (endliche Zahl, kein bool) gilt für `[date, date + interval)`.
- Energy-Charts: Parameter `bzn=AT`, `start` und `end` als ISO-Zeitpunkte mit Ortszeit-Offset; Antwort `unix_seconds` und `price` gleich lang, `unit` muss `EUR / MWh` sein; ct = Preis / 10; Ende = nächster Zeitstempel, beim letzten Eintrag Start + Abstand der beiden letzten (bei nur einem Eintrag 15 min); `null`-Preise entfallen.
- aWATTar: Parameter `start` und `end` in Epoch-Millisekunden; `data[]` mit `start_timestamp`, `end_timestamp` (ms), `marketprice`, `unit` (Vergleich ohne Groß-/Kleinschreibung mit `eur/mwh`); ct = Preis / 10.
- Jede Formverletzung → `PriceParseError` mit Quellenname am Anfang.

- [ ] **Step 1: Write the failing tests**

```python
FIX = Path("tests/fixtures")

def test_smartenergy_recorded_response(source) -> None:
    intervals = source.parse((FIX / "smartenergy_2026-10-09.json").read_text())
    assert len(intervals) == 96
    first, last = intervals[0], intervals[-1]
    assert first == PriceInterval(datetime(2026, 10, 8, 22, tzinfo=UTC), datetime(2026, 10, 8, 22, 15, tzinfo=UTC), 17.824)
    assert (last.start, last.ct_kwh) == (datetime(2026, 10, 9, 21, 45, tzinfo=UTC), 25.405)

def test_smartenergy_interval_60_gives_hour_intervals(source) -> None:
    body = json.dumps({"tariff": "EPEXSPOTAT", "unit": "ct/kWh", "interval": 60,
                       "data": [{"date": "2026-10-09T00:00:00+02:00", "value": 9.5}]})
    [interval] = source.parse(body)
    assert interval.end - interval.start == timedelta(hours=1)

@pytest.mark.parametrize(("body", "message"), [
    ({"unit": "EUR/MWh", "interval": 15, "data": []}, "unbekannte Einheit"),
    ({"unit": "ct/kWh", "interval": 15, "data": [{"date": "2026-10-09T00:00:00", "value": 1}]}, "ohne Zeitzone"),
])
def test_smartenergy_rejects_bad_shapes(source, body, message) -> None:
    with pytest.raises(PriceParseError, match=message):
        source.parse(json.dumps(body))

async def test_smartenergy_fetch_sends_user_agent(respx_mock, source) -> None:
    route = respx_mock.get(SmartEnergySource.URL).respond(200, text="{}")
    raw = await source.fetch()
    assert raw.ok and raw.source == "smartenergy" and raw.fetched_at == T_APP
    assert route.calls.last.request.headers["user-agent"] == "BindaEMS/0.1.0"

def test_energy_charts_recorded_response(energy_charts) -> None:
    intervals = energy_charts.parse((FIX / "energycharts_at_2026-10-09.json").read_text())
    assert len(intervals) == 96 and intervals[0].ct_kwh == pytest.approx(15.45)
    assert intervals[-1].end == datetime(2026, 10, 9, 22, tzinfo=UTC)

async def test_energy_charts_request_parameters(respx_mock, energy_charts) -> None:
    route = respx_mock.get(EnergyChartsSource.URL).respond(200, text="{}")
    start = datetime(2026, 10, 9, tzinfo=LOCAL_TZ)
    await energy_charts.fetch(start, start + timedelta(days=2))
    params = route.calls.last.request.url.params
    assert (params["bzn"], params["start"], params["end"]) == ("AT", "2026-10-09T00:00:00+02:00",
                                                               "2026-10-11T00:00:00+02:00")

def test_awattar_recorded_response(awattar) -> None:
    intervals = awattar.parse((FIX / "awattar_2026-10-09.json").read_text())
    assert len(intervals) == 24 and intervals[0].ct_kwh == pytest.approx(14.853)
    assert intervals[0].end - intervals[0].start == timedelta(hours=1)

async def test_network_error_raises_fetch_error(respx_mock, source) -> None:
    respx_mock.get(SmartEnergySource.URL).mock(side_effect=httpx.ConnectError("weg"))
    with pytest.raises(FetchError, match="smartenergy: ConnectError"):
        await source.fetch()

async def test_http_error_is_returned_for_archiving(respx_mock, source) -> None:
    respx_mock.get(SmartEnergySource.URL).respond(503, text="Wartung")
    raw = await source.fetch()
    assert (raw.ok, raw.status, raw.body) == (False, 503, "Wartung")
```

Fixtures `source`, `energy_charts`, `awattar`: die Quellen mit einem `httpx.AsyncClient` und `ManualClock(T_APP)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/prices/test_sources.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.app.fetch'`)

- [ ] **Step 3: Implement `fetch.py` and `sources.py` as specified**

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (10 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/fetch.py src/bindaems/app/prices tests/unit/app/prices tests/fixtures
git commit -m "feat(app): Preisquellen smartENERGY, Energy-Charts und aWATTar"
```

---

### Task 12: Preisprüfung: Slots, Plausibilität, Brutto/Netto-Erkennung

**Files:**
- Create: `src/bindaems/app/prices/checks.py`
- Test: `tests/unit/app/prices/test_checks.py`

**Interfaces:**
- Consumes: `PriceInterval` (Task 11); `slot_start`, `local_day_slots`, `LOCAL_TZ`, `SLOT`
- Produces:
  - `to_slots(intervals: Sequence[PriceInterval]) -> dict[datetime, float]`: zeitgewichtetes Mittel je Slot; lange Intervalle gelten für jeden enthaltenen Slot; Slots, die nicht vollständig abgedeckt sind, entfallen
  - `duplicate_starts(intervals: Sequence[PriceInterval]) -> list[datetime]`
  - `by_local_day(intervals: Sequence[PriceInterval]) -> dict[date, list[PriceInterval]]` (nach dem lokalen Datum des Beginns)
  - `structure_findings(day: date, intervals: Sequence[PriceInterval]) -> list[str]`: „{n} Slots fehlen“ (gegen `local_day_slots(day)`), „Slot doppelt: {HH:MM}“ (Ortszeit), „alle Werte gleich ({v:.3f} ct)“ (alle Slotwerte exakt gleich)
  - `range_findings(net: Mapping[datetime, float]) -> list[str]`: je Wert außerhalb −50…100 „Wert außerhalb −50…100 ct/kWh: {v:.2f} ct um {HH:MM}“, höchstens 3 Einträge, danach „…“
  - `@dataclass(frozen=True) VatDetection(result: Literal["net", "gross"] | None, ratio: float | None, slots: int)`
  - `detect_vat(primary: Mapping[datetime, float], reference: Mapping[datetime, float], gross_factor: float) -> VatDetection` (Präzisierung 1: Stunden mit vier Slots in beiden Quellen und |Referenz-Stundenmittel| > 2 ct; `slots` = 4 × Anzahl dieser Stunden; unter 24 → `result=None`, `ratio=None`; sonst Median der Stundenverhältnisse; |Median − 1| ≤ 0,05 → `net`, |Median − gross_factor| ≤ 0,05 → `gross`, sonst `None` mit Verhältnis)
  - `is_hourly(slots: Mapping[datetime, float]) -> bool`: in jeder vollständigen Stunde sind die vier Werte gleich (mindestens eine vollständige Stunde)

- [ ] **Step 1: Write the failing tests**

```python
def iv(start: str, minutes: int, ct: float) -> PriceInterval: ...  # start als UTC-ISO

def day_intervals(day: date, value: Callable[[int], float]) -> list[PriceInterval]: ...
# je Slot von local_day_slots(day) ein 15-min-Intervall mit value(index)

def test_to_slots_hour_interval_fills_four_slots() -> None:
    slots = to_slots([iv("2026-10-09T08:00", 60, 12.0)])
    assert list(slots.values()) == [12.0] * 4 and min(slots) == datetime(2026, 10, 9, 8, tzinfo=UTC)

def test_to_slots_time_weighted_mean_of_short_intervals() -> None:
    slots = to_slots([iv("2026-10-09T08:00", 5, 10.0), iv("2026-10-09T08:05", 10, 16.0)])
    assert slots == {datetime(2026, 10, 9, 8, tzinfo=UTC): pytest.approx(14.0)}

def test_to_slots_drops_partially_covered_slot() -> None:
    assert to_slots([iv("2026-10-09T08:00", 10, 10.0)]) == {}

def test_structure_ok_for_recorded_day(smartenergy_intervals) -> None:
    assert structure_findings(date(2026, 10, 9), smartenergy_intervals) == []

def test_autumn_dst_day_needs_100_slots() -> None:
    day = date(2026, 10, 25)
    full = day_intervals(day, lambda i: 10.0 + i % 7)
    assert len(full) == 100 and structure_findings(day, full) == []
    assert structure_findings(day, full[:96]) == ["4 Slots fehlen"]

def test_spring_dst_day_needs_92_slots() -> None:
    day = date(2027, 3, 28)
    full = day_intervals(day, lambda i: 10.0 + i % 7)
    assert len(full) == 92 and structure_findings(day, full) == []

def test_all_equal_day_is_suspicious() -> None:  # Ausfall wie im September 2025
    day = date(2026, 10, 9)
    assert structure_findings(day, day_intervals(day, lambda i: 0.0)) == ["alle Werte gleich (0.000 ct)"]

def test_duplicate_slot_is_suspicious() -> None:
    day = date(2026, 10, 9)
    intervals = day_intervals(day, lambda i: 10.0 + i % 7)
    assert "Slot doppelt: 10:00" in structure_findings(day, [*intervals, intervals[40]])

def test_range_check_uses_net_values() -> None:
    t = datetime(2026, 10, 9, 8, tzinfo=UTC)
    assert range_findings({t: 110.0 / 1.2}) == []
    assert range_findings({t: 130.0 / 1.2}) == ["Wert außerhalb −50…100 ct/kWh: 108.33 ct um 10:00"]

def test_detect_vat_recorded_day_is_gross(smartenergy_slots, energy_charts_slots) -> None:
    detection = detect_vat(smartenergy_slots, energy_charts_slots, 1.2)
    assert (detection.result, detection.slots) == ("gross", 96)
    assert detection.ratio == pytest.approx(1.2, abs=0.001)

def test_detect_vat_with_hourly_reference(smartenergy_slots, awattar_slots) -> None:
    assert detect_vat(smartenergy_slots, awattar_slots, 1.2).result == "gross"

def test_detect_vat_net(energy_charts_slots) -> None:
    hourly = hourly_means(energy_charts_slots)  # Helfer: je Stunde Mittel auf alle vier Slots
    assert detect_vat(hourly, energy_charts_slots, 1.2).result == "net"

def test_detect_vat_ignores_small_reference_and_needs_24_slots(energy_charts_slots) -> None:
    small = {t: 1.0 for t in energy_charts_slots}
    assert detect_vat(small, small, 1.2) == VatDetection(None, None, 0)
    five_hours = dict(list(energy_charts_slots.items())[:20])
    assert detect_vat(five_hours, five_hours, 1.2) == VatDetection(None, None, 20)

def test_detect_vat_undetermined_ratio(energy_charts_slots) -> None:
    odd = {t: v * 1.1 for t, v in energy_charts_slots.items()}
    detection = detect_vat(odd, energy_charts_slots, 1.2)
    assert detection.result is None and detection.ratio == pytest.approx(1.1)

def test_is_hourly(smartenergy_slots, energy_charts_slots) -> None:
    assert is_hourly(smartenergy_slots) and not is_hourly(energy_charts_slots)
```

Fixtures lesen die drei Preisaufnahmen mit den Parsern aus Task 11 und bilden `*_slots` mit `to_slots`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/prices/test_checks.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.app.prices.checks'`)

- [ ] **Step 3: Implement `checks.py` as specified**

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (15 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/prices/checks.py tests/unit/app/prices/test_checks.py
git commit -m "feat(app): Preisprüfung mit Brutto/Netto-Erkennung über Stundenmittel"
```

---

### Task 13: Preis-Pipeline: Archiv, Speicherung, Ersatzquelle, Abfrage

**Files:**
- Create: `src/bindaems/app/prices/{store,pipeline,service}.py`, `src/bindaems/app/db/migrations/versions/v0005_prices.py`
- Modify: `src/bindaems/app/db/schema.py`
- Test: `tests/unit/app/prices/test_store.py`, `tests/unit/app/prices/test_pipeline.py`, `tests/unit/app/prices/test_service.py`

**Interfaces:**
- Consumes: `RawResponse` (Task 11), Quellen (Task 11), Prüfungen (Task 12), `import_price`, `feed_in_price` (Task 10), `SettingsService` (Task 6), `ComponentStatus` (Task 7), `Point`, `PointSink` (Task 1), `RP_LONG` (Task 8)
- Produces:
  - Tabellen: `price_raw` (`id` Integer PK, `source` String(32), `url` String(500), `fetched_at`, `last_fetched_at`, `status` Integer, `sha256` String(64), `body` Text); `price_slot` (`slot_start` UtcDateTime PK, `spot_raw_ct` Float null, `spot_net_ct` Float not null, `reference_ct` Float null, `origin` String(8) not null, `raw_id` FK `price_raw.id` null, `updated_at`)
  - `bindaems.app.prices.store`: `@dataclass(frozen=True) SlotRow(slot_start: datetime, spot_raw_ct: float | None, spot_net_ct: float, reference_ct: float | None, origin: Literal["primary", "fallback"], raw_id: int | None)`; `@dataclass(frozen=True) StoredPrice(` gleiche Felder `, updated_at: datetime)`; `class PriceStore(engine: Engine, clock: Clock)`: `archive(raw: RawResponse) -> int`, `save(rows: Sequence[SlotRow]) -> int`, `get(start: datetime, end: datetime) -> list[StoredPrice]`, `complete_day(day: date, origin: str | None = None) -> bool`
  - `bindaems.app.prices.pipeline`: `@dataclass(frozen=True) DayResult(day: date, origin: str | None, findings: list[str])`; `@dataclass(frozen=True) PriceStatus(last_attempt: datetime | None, last_success: datetime | None, vat_mode: str | None, vat_detection: VatDetection | None, days: list[DayResult], errors: list[str])`; `class PricePipeline(store: PriceStore, primary: SmartEnergySource, reference: Callable[[str], ReferenceSource], settings: SettingsService, prices: PriceService, clock: Clock, sink: PointSink | None = None)`: `async refresh() -> PriceStatus`, `restore() -> None`, `status() -> PriceStatus`, `republish() -> None`, `component_status() -> ComponentStatus` (Name `prices`)
  - `bindaems.app.prices.service`: `@dataclass(frozen=True) PriceView(start: datetime, spot_net_ct: float, import_net_ct: float, import_gross_ct: float, feed_in_ct: float | None, origin: str, missing: tuple[str, ...])`; `class PriceService(store: PriceStore, settings: SettingsService)`: `slots(start: datetime, end: datetime) -> list[PriceView]`, `at(slot: datetime) -> PriceView | None`, `import_gross_at(slot: datetime) -> float | None`

**Festlegungen:**
- `archive`: Ist der Inhalt (SHA-256 des Körpers) gleich dem zuletzt archivierten derselben Quelle, wird nur `last_fetched_at` aktualisiert und dessen `id` zurückgegeben; sonst neue Zeile.
- `save`: Upsert je `slot_start` (`sqlite.insert … on_conflict_do_update`). Eine vorhandene Zeile mit `origin="primary"` wird nur von einer neuen `primary`-Zeile ersetzt; `reference_ct` wird immer aktualisiert, wenn der neue Wert nicht `None` ist.
- `refresh()`, Ablauf:
  1. `today` = lokales Datum; Fenster = [today 00:00, today + 2 00:00) in Ortszeit.
  2. smartENERGY abrufen und archivieren (auch Fehlerstatus); bei 2xx parsen. `FetchError`, `PriceParseError` oder Status ≠ 2xx („{Quelle}: HTTP {status}“) → `errors`.
  3. Referenz laut `settings.prices.reference_source` für das Fenster abrufen, archivieren, parsen (Fehler → `errors`).
  4. Primärdaten nach lokalem Tag gruppieren; je Tag `structure_findings`.
  5. Brutto/Netto: `detect_vat` auf allen strukturell einwandfreien Primärtagen gegen die Referenz-Slots, Faktor `1 + vat_pct/100`. Wirksam: `net`/`gross` laut Einstellung; bei `auto` das Ergebnis, sonst die zuletzt erfolgreiche Erkennung (auch aus `restore`), sonst `vat_fallback` mit Fehlereintrag „Brutto/Netto nicht erkennbar (Verhältnis {r:.3f}) – Ersatzeinstellung „{brutto|netto}“ aktiv“ (`r` fehlt → „zu wenige Vergleichswerte“). Der Eintrag entfällt, wenn kein Primärtag strukturell einwandfrei ist, weil dann kein Primärwert verwendet wird.
  6. Netto = Rohwert / Faktor bei brutto; `range_findings` je Tag.
  7. Je Tag aus Primär- ∪ Referenzdaten: Primärtag ohne Befund → Zeilen `primary` (mit `reference_ct`, wo vorhanden); sonst, wenn die Referenz alle `local_day_slots` des Tages hat → Zeilen `fallback` mit `spot_net_ct` = Referenz und Eintrag „{TT.MM.}: smartENERGY verdächtig ({Befunde}) – Ersatzquelle {Name}“ (ohne Primärdaten: „{TT.MM.}: keine Daten von smartENERGY – Ersatzquelle {Name}“); sonst nichts speichern, Befunde in `DayResult`.
  8. `store.save`; danach für alle gespeicherten Slots Punkte in RP `long`: `price` mit `kind=spot_raw` (Rohwert, nur bei `primary`), `reference`, `import_gross` (über `PriceService`) und `feed_in` (wenn bekannt), Feld `ct_kwh`.
  9. Status aktualisieren; `last_success`, wenn mindestens ein Tag gespeichert wurde.
- `restore()`: `detect_vat` auf den gespeicherten Slots der letzten zwei Tage (`spot_raw_ct` gegen `reference_ct`); ein Ergebnis wird die zuletzt erfolgreiche Erkennung.
- `republish()`: schreibt `import_gross` und `feed_in` für gespeicherte Slots ab dem aktuellen Slot neu (als Einstellungs-Listener registriert, Task 18).
- `component_status()`: ok, wenn der aktuelle Slot einen Preis hat und `errors` leer ist; Meldung „Preise bis {TT.MM. HH:MM}“ bzw. der erste Fehlertext.
- `PriceService`: `import_price(slot, spot_net_ct, settings.tariff)` und `feed_in_price(slot, settings.feed_in)` mit den aktuellen Einstellungen.

- [ ] **Step 1: Write the failing tests**

```python
# test_store.py
def test_archive_deduplicates_identical_bodies(store, clock) -> None:
    first = store.archive(raw("smartenergy", "{}"))
    clock.advance(3600)
    assert store.archive(raw("smartenergy", "{}")) == first
    assert store.archive(raw("smartenergy", '{"neu": 1}')) != first

def test_fallback_never_overwrites_primary(store) -> None:
    store.save([row(T_SLOT, 10.0, "primary")])
    store.save([row(T_SLOT, 99.0, "fallback", reference_ct=9.0)])
    [stored] = store.get(T_SLOT, T_SLOT + SLOT)
    assert (stored.spot_net_ct, stored.origin, stored.reference_ct) == (10.0, "primary", 9.0)

def test_primary_replaces_fallback(store) -> None:
    store.save([row(T_SLOT, 9.0, "fallback")])
    store.save([row(T_SLOT, 10.0, "primary")])
    assert store.get(T_SLOT, T_SLOT + SLOT)[0].origin == "primary"

def test_complete_day(store) -> None:
    day = date(2026, 10, 25)
    store.save([row(t, 10.0, "primary") for t in local_day_slots(day)[:-1]])
    assert not store.complete_day(day)
    store.save([row(local_day_slots(day)[-1], 10.0, "fallback")])
    assert store.complete_day(day) and not store.complete_day(day, "primary")

# test_pipeline.py – respx liefert die Aufnahmen; clock = T_APP (09.10. 10:00 Ortszeit)
async def test_refresh_stores_net_prices_from_recorded_day(pipeline_env) -> None:
    pipeline, store, sink = pipeline_env(primary=FIX_SE, reference=FIX_EC)
    status = await pipeline.refresh()
    assert status.vat_mode == "gross" and status.vat_detection.result == "gross"
    first = store.get(datetime(2026, 10, 8, 22, tzinfo=UTC), datetime(2026, 10, 9, 22, tzinfo=UTC))[0]
    assert (first.spot_raw_ct, first.origin) == (17.824, "primary")
    assert first.spot_net_ct == pytest.approx(14.8533, abs=1e-4) and first.reference_ct == pytest.approx(15.45)
    assert [d.origin for d in status.days] == ["primary"] and status.errors == []

async def test_all_zero_day_falls_back_to_reference(pipeline_env) -> None:
    pipeline, store, _ = pipeline_env(primary=all_zero(FIX_SE), reference=FIX_EC)
    status = await pipeline.refresh()
    stored = store.get(datetime(2026, 10, 8, 22, tzinfo=UTC), datetime(2026, 10, 9, 22, tzinfo=UTC))
    assert {s.origin for s in stored} == {"fallback"} and stored[0].spot_net_ct == pytest.approx(15.45)
    assert status.errors == ["09.10.: smartENERGY verdächtig (alle Werte gleich (0.000 ct)) – Ersatzquelle energy_charts"]

async def test_suspicious_day_without_reference_stores_nothing(pipeline_env) -> None:
    pipeline, store, _ = pipeline_env(primary=all_zero(FIX_SE), reference=503)
    status = await pipeline.refresh()
    assert store.get(datetime(2026, 10, 8, tzinfo=UTC), datetime(2026, 10, 10, tzinfo=UTC)) == []
    assert status.days[0].findings == ["alle Werte gleich (0.000 ct)"]
    assert not pipeline.component_status().ok

async def test_undetermined_vat_uses_fallback_setting_with_warning(pipeline_env) -> None:
    pipeline, _, _ = pipeline_env(primary=scaled(FIX_SE, 1.1 / 1.2), reference=FIX_EC)
    status = await pipeline.refresh()
    assert status.vat_mode == "gross"
    assert "Brutto/Netto nicht erkennbar (Verhältnis 1.100) – Ersatzeinstellung „brutto“ aktiv" in status.errors

async def test_manual_vat_mode_overrides_detection(pipeline_env, settings_service) -> None:
    set_vat_mode(settings_service, "net")
    pipeline, store, _ = pipeline_env(primary=FIX_SE, reference=FIX_EC)
    status = await pipeline.refresh()
    assert status.vat_mode == "net" and status.vat_detection.result == "gross"
    assert store.get(T_FIRST, T_FIRST + SLOT)[0].spot_net_ct == 17.824

async def test_restore_redetects_vat_from_stored_slots(pipeline_env) -> None:
    pipeline, store, _ = pipeline_env(primary=FIX_SE, reference=FIX_EC)
    await pipeline.refresh()
    restored, _, _ = pipeline_env(primary=503, reference=503, store=store)
    restored.restore()
    assert (await restored.refresh()).vat_mode == "gross"

async def test_points_written_to_long_rp(pipeline_env) -> None:
    pipeline, _, sink = pipeline_env(primary=FIX_SE, reference=FIX_EC)
    await pipeline.refresh()
    kinds = {p.tags["kind"] for p in sink.points if p.measurement == "price"}
    assert kinds == {"spot_raw", "reference", "import_gross"} and {p.rp for p in sink.points} == {"long"}

async def test_settings_change_republishes_import_prices(pipeline_env, settings_service) -> None:
    pipeline, _, sink = pipeline_env(primary=FIX_SE, reference=FIX_EC)
    await pipeline.refresh()
    sink.points.clear()
    pipeline.republish()
    assert {p.tags["kind"] for p in sink.points} == {"import_gross"}

# test_service.py
def test_price_view_applies_tariff(service, store, settings_service) -> None:
    store.save([row(T_SLOT, 10.0, "primary")])  # T_SLOT = 2026-10-09T08:00Z
    view = service.at(T_SLOT)
    assert view.import_net_ct == pytest.approx(11.2) and view.import_gross_ct == pytest.approx(13.44)
    assert view.missing == ("grid_usage", "grid_loss", "electricity_tax", "renewables_levy")
    assert view.feed_in_ct is None and view.origin == "primary"

def test_unknown_slot_has_no_price(service) -> None:
    assert service.at(T_SLOT) is None and service.import_gross_at(T_SLOT) is None
```

Helfer im Testmodul: `raw(source, body)`, `row(slot, net, origin, reference_ct=None)`, `all_zero(fixture)` und `scaled(fixture, factor)` (ändern die Werte der smartENERGY-Aufnahme), `set_vat_mode(service, mode)`; `pipeline_env(primary, reference, store=None)` baut Pipeline, Store, `PriceService` und `FakeSink` mit respx-Routen (Fixture-Datei oder HTTP-Status).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/prices/test_store.py tests/unit/app/prices/test_pipeline.py tests/unit/app/prices/test_service.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.app.prices.store'`)

- [ ] **Step 3: Implement migration 0005, store, pipeline and service as specified**

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (14 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests/unit/app/prices
git commit -m "feat(app): Preis-Pipeline mit Archiv, Ersatzquelle und Tarifanwendung"
```

---

### Task 14: Abrufplan, Preis-API und Prüfprotokoll „Preise“ (Spec 17.1-13)

**Files:**
- Create: `src/bindaems/app/schedule.py`, `src/bindaems/app/prices/{scheduler,routes,verify}.py`
- Modify: `src/bindaems/app/cli.py` (Unterbefehl `verify-prices`), `docs/verification/README.md` (Abschnitt „Preise“)
- Test: `tests/unit/app/test_schedule.py`, `tests/unit/app/prices/test_scheduler.py`, `tests/unit/app/prices/test_routes.py`, `tests/unit/app/prices/test_verify.py`

**Interfaces:**
- Consumes: `PricePipeline`, `PriceService`, `PriceStore`, `PriceView` (Task 13); Quellen und Prüfungen (Tasks 11, 12); `Guard` (Task 5)
- Produces:
  - `bindaems.app.schedule`: `next_local(now: datetime, accept: Callable[[datetime], bool], *, limit_minutes: int = 2880) -> datetime` – geht ab der nächsten vollen Minute nach `now` minutenweise in UTC vorwärts und liefert den ersten Zeitpunkt, dessen Ortszeit `accept` erfüllt (DST-sicher); `ValueError`, wenn keiner im Limit liegt
  - `bindaems.app.prices.scheduler`: `next_price_fetch(now: datetime, tomorrow_complete: bool) -> datetime` – akzeptiert Ortszeit-Minute 2 jeder Stunde und, solange `tomorrow_complete` falsch ist, 12:30 ≤ Ortszeit < 16:00 mit Minute ∈ {0, 15, 30, 45}; `class PriceScheduler(pipeline: PricePipeline, store: PriceStore, clock: Clock, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep)` mit `async run() -> None` (sofort `refresh`, dann bis zum nächsten Termin schlafen; brachte der letzte Abruf keinen Erfolg (`last_success` fehlt oder liegt vor `last_attempt`), spätestens nach 5 min erneut; `tomorrow_complete` = `store.complete_day(morgen, "primary")`)
  - `bindaems.app.prices.routes`: `prices_router(service: PriceService, pipeline: PricePipeline, clock: Clock, guard: Guard) -> APIRouter`
  - `bindaems.app.prices.verify`: `async check_prices(http: httpx.AsyncClient, clock: Clock, out_dir: Path) -> int`
  - CLI: `verify-prices --out DIR` (ohne Konfiguration)

**Festlegungen:**
- Endpunkte:
  - `GET /api/prices?from&to` (viewer; Default: Beginn des lokalen Tages bis +2 Tage; Spanne ≤ 31 d, sonst 422) → `{"status": …, "slots": [PriceView …]}`
  - `GET /api/prices/now` (viewer) → `{"now": PriceView | null, "next_3h": [PriceView …]}` (12 Slots ab dem nächsten)
  - `POST /api/prices/refresh` (admin) → Status nach `pipeline.refresh()`
  - Status-JSON: `{"last_attempt", "last_success", "vat_mode", "vat_detection": {"result", "ratio", "slots"} | null, "days": [{"date", "origin", "findings"}], "errors"}`
- Prüfprotokoll: ruft smartENERGY sowie Energy-Charts und aWATTar für [heute, heute + 2) ab und schreibt
  - `out_dir/preise-{quelle}-{JJJJ-MM-TT}.json` (Rohantworten) und
  - `out_dir/pruefprotokoll-preise-{JJJJ-MM-TT}.md` mit den Zeilen
    - `Abruf: {TT.MM.JJJJ HH:MM} Uhr`
    - `smartENERGY: HTTP {status}, tariff {tariff}, unit {unit}, interval {interval}`
    - `smartENERGY Slots je Tag: {TT.MM.}: {n} …`
    - `Raster: Stundenwerte im 15-min-Raster` bzw. `Raster: 15-min-Werte` (`is_hourly`)
    - `Befunde smartENERGY: keine` bzw. die Texte aus `structure_findings`
    - je Referenz `{Name}: HTTP {status}, {n} Slots` bzw. `{Name}: nicht verfügbar ({Fehler})`
    - je Referenz `Brutto/Netto ({Name}): {brutto|netto|unbestimmt}, Verhältnis {r:.3f} aus {n} Slots`
    - Fazit `Ergebnis: bestanden` oder `Ergebnis: nicht bestanden`
  - Exitcode 0, wenn smartENERGY ohne Befund geparst wurde, mindestens eine Referenz verfügbar ist und eine Erkennung `net` oder `gross` ergab; sonst 1.
- `docs/verification/README.md`: Aufruf `docker compose run --rm -v "$PWD/verification:/out" ems-app verify-prices --out /out` und Hinweis, das Protokoll nach `docs/verification/` zu übernehmen.

- [ ] **Step 1: Write the failing tests**

```python
def local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=LOCAL_TZ)

def test_next_local_finds_first_matching_minute() -> None:
    now = local("2026-10-09T10:00:30")
    assert next_local(now, lambda t: t.minute == 7) == local("2026-10-09T10:07")

@pytest.mark.parametrize(("now", "complete", "expected"), [
    ("2026-10-09T10:00", False, "2026-10-09T10:02"),
    ("2026-10-09T12:20", False, "2026-10-09T12:30"),
    ("2026-10-09T12:31", False, "2026-10-09T12:45"),
    ("2026-10-09T15:50", False, "2026-10-09T16:02"),
    ("2026-10-09T12:31", True, "2026-10-09T13:02"),
])
def test_price_fetch_times(now: str, complete: bool, expected: str) -> None:
    assert next_price_fetch(local(now), complete) == local(expected)

def test_price_fetch_on_autumn_dst_day() -> None:  # 02:02 gibt es zweimal
    now = datetime(2026, 10, 25, 0, 3, tzinfo=UTC)  # 02:03 MESZ
    assert next_price_fetch(now, True) == datetime(2026, 10, 25, 1, 2, tzinfo=UTC)  # 02:02 MEZ

async def test_scheduler_refreshes_at_start_and_retries_after_failure() -> None:
    pipeline = FakePipeline(successes=[False, True])  # zählt refresh-Aufrufe
    sleeps: list[float] = []
    scheduler = PriceScheduler(pipeline, FakeStore(complete=True), ManualClock(local("2026-10-09T10:30")),
                               sleep=record_then_stop(sleeps, after=2))
    with pytest.raises(StopScheduler):
        await scheduler.run()
    assert pipeline.calls == 2 and sleeps[0] == 300.0

def test_prices_endpoint_returns_slots_and_status(client, auth, filled_store) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/prices").json()
    assert len(body["slots"]) == 96 and body["slots"][0]["origin"] == "primary"
    assert set(body["status"]) == {"last_attempt", "last_success", "vat_mode", "vat_detection", "days", "errors"}

def test_prices_now_and_next_3h(client, auth, filled_store) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/prices/now").json()
    assert body["now"]["start"] == "2026-10-09T08:00:00+00:00" and len(body["next_3h"]) == 12

def test_refresh_is_admin_only(client, auth) -> None:
    csrf = login_as(client, auth, "viewer")
    assert client.post("/api/prices/refresh", headers=csrf).status_code == 403

async def test_verify_writes_protocol_with_detection(respx_mock, tmp_path) -> None:
    mock_all_sources(respx_mock)  # die drei Aufnahmen
    async with httpx.AsyncClient() as http:
        assert await check_prices(http, ManualClock(T_APP), tmp_path) == 0
    text = (tmp_path / "pruefprotokoll-preise-2026-10-09.md").read_text()
    assert "Raster: Stundenwerte im 15-min-Raster" in text
    assert "Brutto/Netto (energy_charts): brutto, Verhältnis 1.200 aus 96 Slots" in text
    assert "Ergebnis: bestanden" in text and (tmp_path / "preise-smartenergy-2026-10-09.json").exists()

async def test_verify_fails_without_reference(respx_mock, tmp_path) -> None:
    mock_all_sources(respx_mock, energy_charts=503, awattar=503)
    async with httpx.AsyncClient() as http:
        assert await check_prices(http, ManualClock(T_APP), tmp_path) == 1
```

Helfer: `FakePipeline` liefert je `refresh` einen `PriceStatus` mit oder ohne Erfolg; `FakeStore(complete)` beantwortet `complete_day`; `record_then_stop(sleeps, after)` zeichnet Wartezeiten auf und löst beim `after`-ten Aufruf `StopScheduler` aus. `filled_store` speichert die 96 Slots des aufgezeichneten Tages als `primary`; `client` = `make_client(prices_router(service, pipeline, clock, guard))`; `mock_all_sources` setzt respx-Routen für die drei Quellen (Aufnahme oder HTTP-Status).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/test_schedule.py tests/unit/app/prices/test_scheduler.py tests/unit/app/prices/test_routes.py tests/unit/app/prices/test_verify.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.app.schedule'`)

- [ ] **Step 3: Implement schedule, scheduler, router, protocol and CLI command as specified**

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (13 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app docs/verification/README.md tests/unit/app
git commit -m "feat(app): Preisabruf nach Plan, Preis-API und Prüfprotokoll Preise"
```

---

### Task 15: PV-Prognose mit Open-Meteo und PV-Modell

**Files:**
- Create: `src/bindaems/app/forecast/{__init__,openmeteo,pv_model,service,routes}.py`
- Use (liegt bei): `tests/fixtures/openmeteo_sued_2026-10-09.json` (Fläche Süd, 30°, 48,20/16,37, `past_days=1`, `forecast_days=3`)
- Test: `tests/unit/app/forecast/test_openmeteo.py`, `tests/unit/app/forecast/test_pv_model.py`, `tests/unit/app/forecast/test_service.py`

**Interfaces:**
- Consumes: `RawResponse`, `get_raw`, `FetchError` (Task 11); `PvModelSettings`, `SettingsService` (Task 6); `next_local` (Task 14); `ComponentStatus` (Task 7); `Point`, `PointSink`; `RP_LONG`; `Config` (`site.location`, `pv`)
- Produces:
  - `bindaems.app.forecast.openmeteo`: `OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"`; `@dataclass(frozen=True) WeatherSlot(slot_start: datetime, gti_w_m2: float, temp_c: float)`; `ForecastParseError(Exception)`; `async fetch_plane(http: httpx.AsyncClient, clock: Clock, location: LatLon, plane: PvPlane) -> RawResponse`; `parse_plane(body: str) -> list[WeatherSlot]`
  - `bindaems.app.forecast.pv_model`: `cell_temperature_c(temp_air_c: float, gti_w_m2: float, noct_c: float) -> float`; `plane_power_w(gti_w_m2: float, temp_air_c: float, kwp: float, model: PvModelSettings) -> float`; `combine(planes: Sequence[Mapping[datetime, float]], inverter_ac_max_w: float) -> dict[datetime, float]`
  - `bindaems.app.forecast.service`: `@dataclass(frozen=True) PvForecast(issued_at: datetime, source: str, p50_w: Mapping[datetime, float])` mit `day_energy_kwh(day: date) -> float | None` und `power_at(ts: datetime) -> float | None`; `class ForecastService(http: httpx.AsyncClient, cfg: Config, settings: SettingsService, clock: Clock, sink: PointSink | None = None, *, sleep=asyncio.sleep)`: `async refresh() -> PvForecast | None`, `latest() -> PvForecast | None`, `async run() -> None`, `component_status() -> ComponentStatus` (Name `forecast`)
  - `bindaems.app.forecast.routes`: `forecast_router(service: ForecastService, clock: Clock, guard: Guard) -> APIRouter`

**Festlegungen:**
- Anfrage je Fläche: `latitude`, `longitude`, `minutely_15=global_tilted_irradiance,temperature_2m`, `tilt`, `azimuth` (Werte aus `config.yaml`, Open-Meteo-Konvention), `forecast_days=3`, `past_days=1`, `timezone=GMT`, `timeformat=unixtime`.
- `parse_plane`: `{"error": true, "reason": …}` → `ForecastParseError(reason)`; Einheit der Einstrahlung muss `W/m²` sein; Listen gleich lang; Slot = `utc(T) − 15 min` (Präzisierung 6); Einträge mit `null` entfallen.
- Modell: `cell = T_luft + GTI · (NOCT − 20)/800`; `P = max(0, kWp · 1000 · GTI/1000 · PR · (1 + γ/100 · (cell − 25)))` mit γ in %/K. `combine` summiert Slots, die in allen Flächen vorhanden sind, und begrenzt auf [0, `inverter_ac_max_w`].
- `refresh`: alle Flächen abrufen; scheitert eine (`FetchError`, Status ≠ 2xx, `ForecastParseError`), bleibt die bisherige Prognose, Status nicht ok mit Fehlertext. Sonst neue Prognose (`source="open_meteo"`, `issued_at = now`) und Punkte `forecast` (`kind=pv`, `quantile=p50`, Feld `kw` = W / 1000) in RP `long`.
- `run`: sofort `refresh`, danach zur Ortszeit-Minute 7 jeder Stunde (`next_local`); nach einem Fehler spätestens nach 10 min erneut.
- `day_energy_kwh`: Σ `p50_w × 0,25 / 1000` über die vorhandenen Slots von `local_day_slots(day)`; keine vorhanden → `None`.
- `GET /api/forecast/pv` (viewer) → `{"issued_at", "source", "slots": [{"start", "p50_w"}], "days": [{"date", "kwh"} für heute, morgen, übermorgen], "status": ComponentStatus}`; ohne Prognose `issued_at = null`, `slots = []`.

- [ ] **Step 1: Write the failing tests**

```python
FIXTURE = Path("tests/fixtures/openmeteo_sued_2026-10-09.json")

def test_value_at_t_belongs_to_preceding_slot() -> None:
    slots = {s.slot_start: s for s in parse_plane(FIXTURE.read_text())}
    assert len(slots) == 384
    morning = slots[datetime(2026, 10, 9, 5, 15, tzinfo=UTC)]  # Zeitstempel 05:30 UTC
    assert (morning.gti_w_m2, morning.temp_c) == (15.5, 12.2)
    assert slots[datetime(2026, 10, 9, 5, 0, tzinfo=UTC)].gti_w_m2 == 0.0

async def test_request_parameters(respx_mock, cfg) -> None:
    route = respx_mock.get(OPEN_METEO_URL).respond(200, text="{}")
    plane = PvPlane(name="Ost", kwp=5, tilt_deg=20, azimuth_deg=-90)
    async with httpx.AsyncClient() as http:
        await fetch_plane(http, ManualClock(T_APP), cfg.site.location, plane)
    params = route.calls.last.request.url.params
    assert params["minutely_15"] == "global_tilted_irradiance,temperature_2m"
    assert (params["tilt"], params["azimuth"], params["forecast_days"], params["past_days"]) == ("20.0", "-90.0", "3", "1")
    assert (params["timezone"], params["timeformat"]) == ("GMT", "unixtime")

def test_error_response_raises() -> None:
    with pytest.raises(ForecastParseError, match="Invalid value"):
        parse_plane('{"error": true, "reason": "Invalid value"}')

def test_cell_temperature() -> None:
    assert cell_temperature_c(20.0, 800.0, 45.0) == pytest.approx(45.0)

@pytest.mark.parametrize(("gti", "temp", "expected"), [(800.0, 20.0, 6324.0), (1000.0, 25.0, 7570.3125)])
def test_plane_power_reference_values(gti: float, temp: float, expected: float) -> None:
    assert plane_power_w(gti, temp, 10.0, PvModelSettings()) == pytest.approx(expected)

def test_no_irradiance_no_power() -> None:
    assert plane_power_w(0.0, -5.0, 10.0, PvModelSettings()) == 0.0

def test_combine_sums_planes_and_clips_at_inverter_limit() -> None:
    t1, t2 = T_APP, T_APP + SLOT
    combined = combine([{t1: 6000.0, t2: 1000.0}, {t1: 6000.0}], inverter_ac_max_w=10000.0)
    assert combined == {t1: 10000.0}

async def test_refresh_builds_forecast_and_writes_points(forecast_env) -> None:
    service, sink = forecast_env()
    forecast = await service.refresh()
    assert forecast.p50_w[datetime(2026, 10, 9, 5, 15, tzinfo=UTC)] == pytest.approx(
        plane_power_w(15.5, 12.2, 10.0, PvModelSettings()))
    assert {(p.measurement, p.rp, p.tags["kind"], p.tags["quantile"]) for p in sink.points} == {("forecast", "long", "pv", "p50")}

async def test_failed_plane_keeps_previous_forecast(forecast_env, respx_mock) -> None:
    service, _ = forecast_env()
    first = await service.refresh()
    respx_mock.routes["open_meteo"].respond(503)
    assert await service.refresh() is first and not service.component_status().ok

def test_day_energy_on_dst_day_uses_local_slots() -> None:
    day = date(2026, 10, 25)
    forecast = PvForecast(T_APP, "open_meteo", {t: 1000.0 for t in local_day_slots(day)})
    assert forecast.day_energy_kwh(day) == pytest.approx(25.0)  # 100 Slots × 0,25 h × 1 kW
    assert forecast.day_energy_kwh(date(2026, 10, 26)) is None

def test_forecast_endpoint(client, auth, filled_forecast) -> None:
    login_as(client, auth, "viewer")
    body = client.get("/api/forecast/pv").json()
    assert body["source"] == "open_meteo" and [d["date"] for d in body["days"]] == ["2026-10-09", "2026-10-10", "2026-10-11"]
```

`forecast_env()` nutzt das Beispiel-`cfg` (eine Fläche Süd, 10 kWp) und legt die respx-Route `open_meteo` auf `OPEN_METEO_URL` mit der Aufnahme an; `filled_forecast` ist ein Dienst nach erfolgreichem `refresh`, `client` = `make_client(forecast_router(service, clock, guard))`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/forecast -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.app.forecast'`)

- [ ] **Step 3: Implement client, model, service and router as specified**

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (12 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/forecast tests/unit/app/forecast
git commit -m "feat(app): PV-Prognose mit Open-Meteo und PV-Modell"
```

---

### Task 16: Abrechnung v1

**Files:**
- Create: `src/bindaems/app/ledger/{__init__,service,routes}.py`, `src/bindaems/app/db/migrations/versions/v0006_ledger.py`
- Modify: `src/bindaems/app/db/schema.py`
- Test: `tests/unit/app/ledger/test_service.py`, `tests/unit/app/ledger/test_routes.py`

**Interfaces:**
- Consumes: `SlotFlows` (shared); `PriceService` (Task 13); `SettingsService` (Task 6); `feed_in_price` (Task 10); `InfluxReader`, `RP_RAW`, `RP_LONG` (Task 8); `ComponentStatus` (Task 7); `Point`, `PointSink`; Nachrichtenformat `slot_flows` des core: `{"slot_start": ISO, "covered_s": float, "flows_wh": {fluss: Wh}, "counters": {signal: [start, ende]}}`; Flussnamen des core: `pv>house`, `pv>wb:<name>`, `pv>battery`, `pv>grid`, `battery>house`, `battery>wb:<name>`, `battery>grid`, `grid>house`, `grid>wb:<name>`, `grid>battery`
- Produces:
  - Tabelle `ledger_slot` (`slot_start` UtcDateTime PK, `covered_s` Float, `flows_wh` JSON, `counters` JSON null, `import_wh` Float, `export_wh` Float, `import_price_ct` Float null, `origin` String(8), `updated_at`)
  - `parse_slot_flows(data: Mapping[str, Any]) -> SlotFlows` (`ValueError` mit deutschem Text, wenn `slot_start` keine Zeitzone hat oder nicht auf 15 min liegt, `covered_s` außerhalb 0–900 liegt oder ein Fluss negativ ist)
  - `@dataclass(frozen=True) LedgerSlot(slot_start: datetime, covered_s: float, flows_wh: Mapping[str, float], counters: Mapping[str, tuple[float | None, float | None]], import_wh: float, export_wh: float, import_price_ct: float | None, origin: str)`
  - `@dataclass(frozen=True) DaySummary(date: date, slots: int, expected_slots: int, coverage: float, pv_kwh: float, import_kwh: float, export_kwh: float, house_kwh: float, wallbox_kwh: Mapping[str, float], battery_charge_kwh: float, battery_discharge_kwh: float, consumption_kwh: float, counter_kwh: Mapping[str, float], cost_eur: float | None, revenue_eur: float | None, net_cost_eur: float | None, autarky: float | None, self_consumption: float | None, savings_no_plant_eur: float | None, savings_same_import_eur: float | None)`
  - `class LedgerService(engine: Engine, clock: Clock, prices: PriceService, settings: SettingsService, audit: AuditLog, sink: PointSink | None = None)`: `ingest(flows: SlotFlows, origin: Literal["stream", "influx"]) -> bool`; `async handle_stream_message(data: dict[str, Any]) -> None`; `async backfill(reader: InfluxReader, start: datetime, end: datetime) -> int`; `fill_missing_prices() -> int`; `reprice(first: date, last: date, *, actor: str, source: Source) -> int`; `slots(start: datetime, end: datetime) -> list[LedgerSlot]`; `days(first: date, last: date) -> list[DaySummary]`; `component_status() -> ComponentStatus` (Name `ledger`)
  - `ledger_router(service: LedgerService, guard: Guard) -> APIRouter`

**Festlegungen:**
- `ingest`: fehlt der Slot → einfügen; vorhanden → nur ersetzen, wenn das neue `covered_s` größer ist. `import_wh` = Σ Flüsse mit Präfix `grid>`, `export_wh` = Σ Flüsse mit Suffix `>grid`; `import_price_ct` = `prices.import_gross_at(slot_start)`. Danach Punkte `ledger` in RP `long` je Fluss: `kwh` = Wh / 1000; bei `grid>…` zusätzlich `eur` = kWh × Bezugspreis / 100 (wenn bekannt), bei `…>grid` `eur` = −kWh × Einspeisepreis / 100 (wenn bekannt).
- `handle_stream_message`: `parse_slot_flows`, dann `ingest(…, "stream")` über `asyncio.to_thread`; ungültige Nachricht → Warnung „Ungültige slot_flows-Nachricht“, kein Fehler.
- `backfill`: Abfrage `SELECT "wh", "covered_s" FROM "raw"."flows" WHERE time >= <start>ms AND time < <end>ms GROUP BY "flow"`; je Zeitstempel ein Slot; `covered_s` aus dem Fluss `_coverage`, Slots ohne `_coverage` entfallen; dann `ingest(…, "influx")`. Rückgabe: Anzahl übernommener Slots.
- `fill_missing_prices`: Slots mit `import_wh > 0` und `import_price_ct IS NULL` erhalten den inzwischen bekannten Preis. `reprice`: alle Slots der lokalen Tage `first … last` mit dem aktuellen Tarif neu bewerten, Protokoll `ledger.reprice` (`details={"from", "to", "slots"}`).
- `days`: je lokalem Tag über `local_day_slots`:
  - `pv_kwh` = Σ `pv>…`; `house_kwh` = Σ `…>house`; `wallbox_kwh[name]` = Σ `…>wb:name`; `battery_charge_kwh` = Σ `…>battery`; `battery_discharge_kwh` = Σ `battery>…`; `consumption_kwh` = Haus + Wallboxen; `import_kwh`, `export_kwh` aus den Slots
  - `coverage` = Σ `covered_s` / (`expected_slots` × 900); `counter_kwh[signal]` = Σ (Ende − Start) über Slots mit beiden Werten
  - `cost_eur` = Σ `import_wh`/1000 × Preis/100; `None`, sobald ein Slot mit Bezug keinen Preis hat
  - `revenue_eur` = `export_kwh` × `feed_in_price` (Monat des Tages, aktuelle Einstellungen) / 100; `None` ohne OeMAG-Wert
  - `net_cost_eur` = Kosten − Erlös; `autarky` = 1 − Bezug/Verbrauch (`None` bei Verbrauch 0); `self_consumption` = (PV − Einspeisung)/PV (`None` bei PV 0)
  - `savings_no_plant_eur` = Verbrauch × Fixpreis/100 − `net_cost_eur`; `savings_same_import_eur` = Bezug × Fixpreis/100 − Erlös − `net_cost_eur`; jeweils `None`, wenn eine Eingangsgröße fehlt
- `component_status`: ok, wenn der jüngste Slot höchstens 30 min alt ist; Meldung „letzte Viertelstunde {HH:MM}“ bzw. „keine Abrechnungsdaten seit {HH:MM}“.
- Endpunkte: `GET /api/ledger/days?from=JJJJ-MM-TT&to=JJJJ-MM-TT` (viewer; inklusive; ≤ 400 Tage, sonst 422) → Liste der Tageswerte (Zahlen auf 3 Nachkommastellen gerundet); `GET /api/ledger/slots?from&to` (viewer; ≤ 7 d); `POST /api/ledger/reprice` (admin) `{"from", "to"}` → `{"repriced": n}`.

- [ ] **Step 1: Write the failing tests**

```python
S = datetime(2026, 10, 9, 8, 0, tzinfo=UTC)
MSG = {"slot_start": "2026-10-09T08:00:00+00:00", "covered_s": 900.0,
       "flows_wh": {"pv>house": 400.0, "pv>wb:evcs": 200.0, "pv>battery": 100.0, "pv>grid": 300.0,
                    "battery>house": 50.0, "grid>house": 150.0, "grid>wb:twc": 250.0},
       "counters": {"grid.energy_import_kwh": [100.0, 100.4], "grid.energy_export_kwh": [50.0, 50.3]}}

@pytest.mark.parametrize("change", [{"slot_start": "2026-10-09T08:07:00+00:00"},
                                    {"slot_start": "2026-10-09T08:00:00"}, {"covered_s": 901.0},
                                    {"flows_wh": {"pv>house": -1.0}}])
def test_parse_slot_flows_validates_message(change) -> None:
    with pytest.raises(ValueError):
        parse_slot_flows(MSG | change)

def test_ingest_stores_slot_with_import_price_and_mirrors_to_influx(ledger, prices_with_10ct, sink) -> None:
    assert ledger.ingest(parse_slot_flows(MSG), "stream")
    [slot] = ledger.slots(S, S + SLOT)
    assert (slot.import_wh, slot.export_wh, slot.import_price_ct) == (400.0, 300.0, pytest.approx(13.44))
    grid_house = next(p for p in sink.points if p.tags.get("flow") == "grid>house")
    assert grid_house.rp == "long" and grid_house.fields["kwh"] == 0.15
    assert grid_house.fields["eur"] == pytest.approx(0.15 * 13.44 / 100)

def test_ingest_keeps_record_with_larger_coverage(ledger) -> None:
    assert ledger.ingest(parse_slot_flows(MSG | {"covered_s": 600.0}), "stream")
    assert not ledger.ingest(parse_slot_flows(MSG | {"covered_s": 500.0}), "influx")
    assert ledger.ingest(parse_slot_flows(MSG), "influx")
    assert ledger.slots(S, S + SLOT)[0].covered_s == 900.0

async def test_backfill_fills_slots_missed_while_app_was_down(ledger, respx_mock, reader) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")
    route = respx_mock.get("http://influx.lan:8086/query").respond(json={"results": [{"series": [
        {"name": "flows", "tags": {"flow": "_coverage"}, "columns": ["time", "wh", "covered_s"],
         "values": [[1791532800000, None, 900.0], [1791533700000, None, 880.0]]},
        {"name": "flows", "tags": {"flow": "grid>house"}, "columns": ["time", "wh", "covered_s"],
         "values": [[1791532800000, 150.0, None], [1791533700000, 210.0, None]]}]}]})
    assert await ledger.backfill(reader, S, S + 2 * SLOT) == 1
    assert ledger.slots(S + SLOT, S + 2 * SLOT)[0].flows_wh == {"grid>house": 210.0}
    assert route.calls.last.request.url.params["q"] == (
        'SELECT "wh", "covered_s" FROM "raw"."flows" WHERE time >= 1791532800000ms '
        'AND time < 1791534600000ms GROUP BY "flow"')

def test_day_summary_energy_balance_and_kpis(ledger, prices_with_10ct, feed_in_7ct) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")
    [day] = ledger.days(date(2026, 10, 9), date(2026, 10, 9))
    assert (day.pv_kwh, day.import_kwh, day.export_kwh) == (1.0, 0.4, 0.3)
    assert (day.house_kwh, dict(day.wallbox_kwh)) == (0.6, {"evcs": 0.2, "twc": 0.25})
    assert (day.battery_charge_kwh, day.battery_discharge_kwh, day.consumption_kwh) == (0.1, 0.05, 1.05)
    assert day.counter_kwh["grid.energy_import_kwh"] == pytest.approx(0.4)
    assert (day.slots, day.expected_slots) == (1, 96) and day.coverage == pytest.approx(1 / 96)
    assert day.cost_eur == pytest.approx(0.4 * 0.1344) and day.revenue_eur == pytest.approx(0.3 * 0.07)
    assert day.autarky == pytest.approx(1 - 0.4 / 1.05) and day.self_consumption == pytest.approx(0.7)

def test_savings_follow_spec_formulas(ledger, prices_with_10ct, feed_in_7ct) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")
    [day] = ledger.days(date(2026, 10, 9), date(2026, 10, 9))
    net_cost = 0.4 * 0.1344 - 0.3 * 0.07
    assert day.net_cost_eur == pytest.approx(net_cost)
    assert day.savings_no_plant_eur == pytest.approx(1.05 * 0.30 - net_cost)
    assert day.savings_same_import_eur == pytest.approx(0.4 * 0.30 - 0.3 * 0.07 - net_cost)

def test_day_summary_on_dst_day_expects_100_slots(ledger) -> None:
    [day] = ledger.days(date(2026, 10, 25), date(2026, 10, 25))
    assert (day.expected_slots, day.slots, day.coverage) == (100, 0, 0.0)

def test_cost_unknown_when_import_slot_has_no_price(ledger) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")  # keine Preise gespeichert
    assert ledger.days(date(2026, 10, 9), date(2026, 10, 9))[0].cost_eur is None

def test_revenue_follows_feed_in_setting_at_read_time(ledger, prices_with_10ct, settings_service) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")
    assert ledger.days(date(2026, 10, 9), date(2026, 10, 9))[0].revenue_eur is None
    set_feed_in(settings_service, {"2026-10": 8.0})
    assert ledger.days(date(2026, 10, 9), date(2026, 10, 9))[0].revenue_eur == pytest.approx(0.3 * 0.08)

def test_fill_missing_prices_and_reprice(ledger, store, settings_service, audit) -> None:
    ledger.ingest(parse_slot_flows(MSG), "stream")
    store.save([row(S, 10.0, "primary")])
    assert ledger.fill_missing_prices() == 1
    set_grid_usage(settings_service, 8.0)
    assert ledger.reprice(date(2026, 10, 9), date(2026, 10, 9), actor="chris", source="ui") == 1
    assert ledger.slots(S, S + SLOT)[0].import_price_ct == pytest.approx(23.04)
    assert audit.recent()[0].action == "ledger.reprice"

async def test_stream_handler_ignores_invalid_messages(ledger) -> None:
    await ledger.handle_stream_message({"slot_start": "kaputt"})
    assert ledger.slots(S, S + SLOT) == []

def test_days_endpoint(client, auth, ledger) -> None:
    login_as(client, auth, "viewer")
    ledger.ingest(parse_slot_flows(MSG), "stream")
    [day] = client.get("/api/ledger/days", params={"from": "2026-10-09", "to": "2026-10-09"}).json()
    assert day["pv_kwh"] == 1.0 and day["expected_slots"] == 96

def test_reprice_is_admin_only(client, auth) -> None:
    csrf = login_as(client, auth, "operator")
    response = client.post("/api/ledger/reprice", json={"from": "2026-10-09", "to": "2026-10-09"}, headers=csrf)
    assert response.status_code == 403
```

(Bezugspreis bei 10 ct Spot mit Standardtarif: 11,2 ct netto → 13,44 ct brutto; mit `grid_usage` 8,0 ct: 19,2 ct netto → 23,04 ct brutto. Fixpreis 30 ct.)

Fixtures: `store` (leerer `PriceStore`), `ledger` = `LedgerService(engine, clock, PriceService(store, settings_service), settings_service, audit, sink)`, `prices_with_10ct` speichert für `S` einen Primärpreis von 10 ct, `feed_in_7ct` setzt `feed_in.monthly_ct = {"2026-10": 7.0}`; `set_feed_in` und `set_grid_usage` ändern die Einstellungen über `SettingsService.update`; `row` wie in Task 13; `reader` wie in Task 8; `client` = `make_client(ledger_router(ledger, guard))`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/ledger -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.app.ledger'`)

- [ ] **Step 3: Implement migration 0006, service and router as specified**

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (16 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests/unit/app/ledger
git commit -m "feat(app): Abrechnung v1 aus slot_flows mit Nachtrag aus InfluxDB"
```

---

### Task 17: Home Assistant: MQTT Discovery, nur lesend

**Files:**
- Create: `src/bindaems/app/ha/{__init__,entities,publisher}.py`
- Test: `tests/unit/app/ha/test_entities.py`, `tests/unit/app/ha/test_publisher.py`

**Interfaces:**
- Consumes: `HaMqttConfig`, `Config` (Task 2); `build_tls_context`, `Backoff` (Task 1); `LiveState` (Task 7); `PriceService`, `PriceStore` (Task 13); `ForecastService` (Task 15); `ComponentStatus`; `LOCAL_TZ`, `slot_start`, `bindaems.__version__`
- Produces:
  - `bindaems.app.ha.entities`:
    - `@dataclass(frozen=True) DeviceSpec(identifier: str, name: str, model: str)`
    - `@dataclass(frozen=True) HaInputs(core_connected: bool, mode: str | None, derived: Mapping[str, Any], signals: Mapping[str, Value], alarm_ids: Sequence[str], adapters_offline: Sequence[str] | None, price_now_ct: float | None, prices_ahead: Sequence[tuple[datetime, float]], feed_in_ct: float | None, prices_missing: bool, pv_today_kwh: float | None, pv_tomorrow_kwh: float | None)`
    - `@dataclass(frozen=True) EntitySpec(object_id: str, component: Literal["sensor", "binary_sensor"], name: str, device: DeviceSpec, value: Callable[[HaInputs], str], unit: str | None = None, device_class: str | None = None, state_class: str | None = None, entity_category: str | None = None, attributes: Callable[[HaInputs], Mapping[str, Any]] | None = None)`
    - `build_entities(cfg: Config) -> list[EntitySpec]`
    - `discovery(spec: EntitySpec, mqtt: HaMqttConfig) -> tuple[str, str]` (Topic, kompaktes JSON mit sortierten Schlüsseln)
    - `fmt(value: float | None, decimals: int) -> str` (`"None"` für unbekannt), `on_off(value: bool | None) -> str` (`"ON"`/`"OFF"`/`"None"`)
    - `build_inputs(live: LiveState, prices: PriceService, store: PriceStore, forecast: ForecastService, now: datetime) -> HaInputs`
  - `bindaems.app.ha.publisher`:
    - `class HaTransport(Protocol)`: asynchroner Kontextmanager; `async publish(topic: str, payload: str, *, retain: bool) -> None`; `async subscribe(topic: str) -> None`; `messages() -> AsyncIterator[tuple[str, bytes]]`
    - `class AiomqttHaTransport(cfg: HaMqttConfig, password: SecretStr | None)` (aiomqtt mit `will=aiomqtt.Will(f"{base}/status", "offline", qos=1, retain=True)`, `identifier=f"bindaems-app-{secrets.token_hex(4)}"`, TLS über `build_tls_context`, Veröffentlichungen mit QoS 1)
    - `allowed_topic(mqtt: HaMqttConfig) -> re.Pattern[str]`
    - `class HaPublisher(cfg: HaMqttConfig, entities: Sequence[EntitySpec], inputs: Callable[[], HaInputs], *, transport_factory: Callable[[], HaTransport], sleep: Callable[[float], Awaitable[None]] = asyncio.sleep, backoff: Backoff | None = None)`: `async run() -> None`, `component_status() -> ComponentStatus` (Name `ha`)

**Festlegungen:**
- Entitäten (Gerät „BindaEMS“, `identifier="bindaems"`, `model="EMS"`):

  | object_id | Komponente | Name | Einheit | device_class | state_class | Wert |
  |---|---|---|---|---|---|---|
  | `price_now` | sensor | Bezugspreis | ct/kWh | – | measurement | `price_now_ct`, 2 Stellen; Attribute `{"prices": [{"start": <ISO Ortszeit>, "ct": <2 Stellen>} …]}` aus `prices_ahead` (bis 36 h) |
  | `feed_in_price` | sensor | Einspeisepreis | ct/kWh | – | measurement | `feed_in_ct`, 2 Stellen |
  | `pv_forecast_today` | sensor | PV-Prognose heute | kWh | energy | – | 1 Stelle |
  | `pv_forecast_tomorrow` | sensor | PV-Prognose morgen | kWh | energy | – | 1 Stelle |
  | `grid_power` | sensor | Netzleistung | W | power | measurement | `derived["grid_w"]`, 0 Stellen |
  | `pv_power` | sensor | PV-Leistung | W | power | measurement | `derived["pv_total_w"]` |
  | `battery_power` | sensor | Akkuleistung | W | power | measurement | `derived["battery_w"]` |
  | `house_power` | sensor | Hausverbrauch | W | power | measurement | `derived["house_load_w"]` |
  | `battery_soc` | sensor | Akku-SOC | % | battery | measurement | `signals["battery.soc_pct"]`, 1 Stelle |
  | `ems_mode` | sensor | Betriebsart | – | – | – | `mode` oder `None`; `entity_category="diagnostic"` |
  | `core_online` | binary_sensor | core verbunden | – | connectivity | – | `core_connected`; diagnostic |
  | `problem_competitor` | binary_sensor | Mitregelndes System | – | problem | – | eine Alarm-ID beginnt mit `competitor.` (ohne core-Verbindung `None`) |
  | `problem_prices` | binary_sensor | Preise fehlen | – | problem | – | `prices_missing` |
  | `problem_device_offline` | binary_sensor | Gerät offline | – | problem | – | `adapters_offline` nicht leer (`None`, wenn unbekannt); Attribute `{"adapters": […]}` |

  Je Fahrzeug Gerät `bindaems_<id>` „BindaEMS <vehicle.name>“ (Modell „Fahrzeug“) mit `<id>_soc` „SOC“ (%, battery, measurement, `signals["vehicle.<id>.soc_pct"]`). Je Wallbox Gerät `bindaems_<name>` „BindaEMS EVCS“ bzw. „BindaEMS Wall Connector“ (Modell gleich der Bezeichnung) mit `<name>_power` „Ladeleistung“ (W, power, measurement, `derived["wallbox_w"][<name>]`).
- Discovery-Payload: `name`, `unique_id` = `bindaems_<object_id>`, `has_entity_name: true`, `state_topic` = `<base>/state/<object_id>`, `availability_topic` = `<base>/status`, `device` = `{"identifiers": [id], "name", "manufacturer": "BindaEMS", "model", "sw_version"}`, `origin` = `{"name": "BindaEMS", "sw_version"}`; optional `unit_of_measurement`, `device_class`, `state_class`, `entity_category`, `json_attributes_topic` = `<base>/state/<object_id>/attributes`; bei binary_sensor `payload_on: "ON"`, `payload_off: "OFF"`. Topic `<prefix>/<komponente>/bindaems/<object_id>/config`.
- `build_inputs`: `derived` und `signals` (nur Qualität ok) aus `live`, leer ohne Verbindung; `mode` aus `live.health["mode"]`; `alarm_ids` aus `live.alarms`; `adapters_offline` = Namen der Adapter mit `connected: false` aus `live.health` (ohne Health `None`); Preise aus `PriceService` (aktueller Slot und die folgenden bis 36 h); `prices_missing` = kein Preis für den aktuellen Slot oder (Ortszeit ≥ 16:00 und `store.complete_day(morgen)` falsch); PV aus `forecast.latest()`.
- `run()`: verbinden; `<base>/status` = `online` (retained); `<prefix>/status` abonnieren; alles veröffentlichen (Discovery, Zustände, Attribute; alles retained); danach parallel (a) alle `publish_interval_s` nur geänderte Zustands- und Attribut-Payloads, (b) Nachrichten: `online` auf `<prefix>/status` → alles neu veröffentlichen. Fehler → Cache leeren, `sleep(backoff.next())`, neu verbinden.
- Vor jeder Veröffentlichung prüft der Publisher das Topic gegen `allowed_topic`: `^(<prefix>/(sensor|binary_sensor)/bindaems/[a-z0-9_]+/config|<base>/(status|state/[a-z0-9_]+(/attributes)?))$`; anderes Topic → `RuntimeError`.

- [ ] **Step 1: Write the failing tests**

```python
def test_entities_from_config(cfg) -> None:
    ids = [e.object_id for e in build_entities(cfg)]
    assert ids == ["price_now", "feed_in_price", "pv_forecast_today", "pv_forecast_tomorrow", "grid_power",
                   "pv_power", "battery_power", "house_power", "battery_soc", "ems_mode", "core_online",
                   "problem_competitor", "problem_prices", "problem_device_offline", "tesla_soc", "egolf_soc",
                   "evcs_power", "twc_power"]
    names = {e.object_id: e.device.name for e in build_entities(cfg)}
    assert (names["egolf_soc"], names["twc_power"]) == ("BindaEMS e-Golf", "BindaEMS Wall Connector")

def test_discovery_payload_for_price_sensor(cfg) -> None:
    spec = entity(cfg, "price_now")
    topic, payload = discovery(spec, cfg.homeassistant.mqtt)
    data = json.loads(payload)
    assert topic == "homeassistant/sensor/bindaems/price_now/config"
    assert data["unique_id"] == "bindaems_price_now" and data["has_entity_name"] is True
    assert (data["state_topic"], data["availability_topic"]) == ("bindaems/state/price_now", "bindaems/status")
    assert data["json_attributes_topic"] == "bindaems/state/price_now/attributes"
    assert data["device"]["identifiers"] == ["bindaems"] and data["origin"]["name"] == "BindaEMS"
    assert (data["unit_of_measurement"], data["state_class"]) == ("ct/kWh", "measurement")

def test_binary_sensor_payloads(cfg) -> None:
    data = json.loads(discovery(entity(cfg, "problem_prices"), cfg.homeassistant.mqtt)[1])
    assert (data["device_class"], data["payload_on"], data["payload_off"]) == ("problem", "ON", "OFF")

def test_values_formatted_and_unknown_is_none(cfg) -> None:
    inputs = sample_inputs(price_now_ct=13.4449, core_connected=False, derived={})
    assert entity(cfg, "price_now").value(inputs) == "13.44"
    assert entity(cfg, "grid_power").value(inputs) == "None"
    assert entity(cfg, "problem_competitor").value(inputs) == "None"
    assert entity(cfg, "core_online").value(inputs) == "OFF"

def test_build_inputs_from_live_prices_and_forecast(live, price_service, store, forecast_service) -> None:
    inputs = build_inputs(live, price_service, store, forecast_service, T_APP)
    assert inputs.derived["grid_w"] == 512.0 and inputs.signals["battery.soc_pct"] == 55.0
    assert inputs.price_now_ct == pytest.approx(13.44) and not inputs.prices_missing
    assert inputs.pv_today_kwh is not None

async def test_connect_publishes_online_discovery_and_states_retained(publisher_env) -> None:
    publisher, transport = publisher_env()
    await run_publisher_until(publisher, lambda: transport.published_state("price_now") is not None)
    assert transport.published[0] == ("bindaems/status", "online", True)
    assert ("homeassistant/sensor/bindaems/price_now/config", True) in transport.topics_with_retain()
    assert all(retain for _, _, retain in transport.published)
    assert transport.subscriptions == ["homeassistant/status"]

async def test_last_will_is_offline(cfg) -> None:
    transport = AiomqttHaTransport(cfg.homeassistant.mqtt, None)
    will = transport.will
    assert (will.topic, will.payload, will.retain) == ("bindaems/status", "offline", True)

async def test_only_changed_states_are_republished(publisher_env) -> None:
    publisher, transport = publisher_env()
    await run_publisher_until(publisher, lambda: transport.ticks >= 2)  # zwei Intervalle ohne Änderung
    assert transport.count("bindaems/state/price_now") == 1

async def test_ha_birth_message_republishes_everything(publisher_env) -> None:
    publisher, transport = publisher_env(incoming=[("homeassistant/status", b"online")])
    await run_publisher_until(publisher, lambda: transport.count("homeassistant/sensor/bindaems/price_now/config") == 2)
    assert transport.count("bindaems/state/price_now") == 2

async def test_reconnect_after_error_republishes(publisher_env) -> None:
    publisher, transport = publisher_env(fail_first_connection=True)
    await run_publisher_until(publisher, lambda: transport.connections == 2 and transport.count("bindaems/status") >= 1)
    assert transport.sleeps[0] == 1.0

def test_only_allowed_topics_are_published(cfg) -> None:
    pattern = allowed_topic(cfg.homeassistant.mqtt)
    assert pattern.fullmatch("homeassistant/sensor/bindaems/price_now/config")
    assert pattern.fullmatch("bindaems/state/price_now/attributes")
    for topic in ("bindaems/cmd/mode", "homeassistant/select/bindaems/x/config", "R/abc/keepalive", "W/abc/x"):
        assert not pattern.fullmatch(topic)
```

`FakeTransport` zeichnet `published` (Topic, Payload, Retain), `subscriptions`, Verbindungen und Wartezeiten auf und liefert `incoming`; `ticks` zählt die Intervall-Schlafaufrufe. `AiomqttHaTransport.will` ist das dem Client übergebene `aiomqtt.Will`. `entity(cfg, object_id)` sucht eine Entität aus `build_entities(cfg)`; `sample_inputs(**änderungen)` liefert `HaInputs` mit plausiblen Werten. Fixtures für `build_inputs`: `live` mit `derived.grid_w = 512.0` und `battery.soc_pct = 55.0` (Qualität ok), `store`/`price_service` mit einem Primärpreis von 10 ct für `T_APP`, `forecast_service` mit einer Prognose für heute.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/ha -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.app.ha'`)

- [ ] **Step 3: Implement entities, inputs and publisher as specified**

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (11 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/ha tests/unit/app/ha
git commit -m "feat(app): Home-Assistant-Sensoren per MQTT Discovery (nur lesend)"
```

---

### Task 18: App-Laufzeit, Einstiegspunkt und Datensicherung

**Files:**
- Create: `src/bindaems/app/runtime.py`, `src/bindaems/app/backup.py`, `src/bindaems/app/healthcheck.py`
- Modify: `src/bindaems/app/cli.py` (Unterbefehl `serve`, Standard ohne Unterbefehl)
- Test: `tests/unit/app/test_backup.py`, `tests/unit/app/test_cli.py` (ergänzen), `tests/integration/test_app_runtime.py`, `tests/unit/test_deploy_files.py` (Healthcheck der app)

**Interfaces:**
- Consumes: alle vorherigen Tasks; `configure_logging`, `InfluxWriter`, `DiskSpool` (Task 1)
- Produces:
  - `bindaems.app.backup`: `backup_database(db_path: Path, backup_dir: Path, now: datetime, keep: int = 14) -> Path`
  - `bindaems.app.runtime`: `class AppRuntime(cfg: Config, secrets: Secrets, *, clock: Clock | None = None, serve: bool = True)` mit Attribut `app: FastAPI`, `async run() -> None`, `async shutdown() -> None`
  - `bindaems.app.healthcheck`: `main() -> int` (`GET http://127.0.0.1:{BINDAEMS_APP_PORT or 8080}/health`, `trust_env=False`, Timeout 3 s; 0 bei 200, sonst 1)
  - CLI `serve`: beginnt `argv` nicht mit einem Unterbefehl, wird `serve` vorangestellt. Konfiguration und Secrets laden (Fehler → Exit 2, Secrets-Meldung ohne Eingabewerte wie im core), `configure_logging`, `AppRuntime.run()`; SIGTERM/SIGINT → `shutdown()`, Exit 0; unerwartetes Ende → Exit 1

**Festlegungen:**
- `backup_database`: SQLite-Online-Backup (`sqlite3.Connection.backup`) nach `backup_dir/bindaems-{JJJJMMTT-HHMMSS, Ortszeit}.sqlite3` über eine `.tmp`-Datei mit anschließendem Umbenennen; danach nur Dateien `bindaems-*.sqlite3` über die neuesten `keep` hinaus löschen.
- `AppRuntime` baut:
  - `open_database(data_dir / DB_FILENAME)` und `migrate`
  - `AuditLog`, `AuthService`, `SettingsService`, `ConsumerService(ha_enabled = influxdb.ha_database is not None)`
  - `LiveState`; `CoreClient` mit eigenem `httpx.AsyncClient(trust_env=False)`; `LiveFeed` mit Handler `ledger.handle_stream_message`
  - `InfluxWriter` mit `DiskSpool(data_dir / "spool", 50 MB, 30 d)`; `InfluxReader`
  - `PriceStore`, `PriceService`, `PricePipeline` (Referenz über `make_reference`), `PriceScheduler`
  - `ForecastService`, `LedgerService`
  - `HaValueCache` (wenn `ha_database` gesetzt), `HaPublisher` (wenn `homeassistant.mqtt` gesetzt; Passwort `ha_mqtt_password`)
  - `Guard(cookie_secure=cfg.app.cookie_secure)`, alle Router, `create_app(…, ui_dir=cfg.app.ui_dir)`
  - Einstellungs-Listener: `pipeline.republish`
  - Statusquellen in dieser Reihenfolge: `core`, `prices`, `forecast`, `ledger`, `influx` (aus `WriterStats`: ok, wenn der Spool leer ist), `ha` (falls konfiguriert)
  - Warnungen für `/api/system` = `settings.warnings()` + `auth.warnings()`
- `run()` (TaskGroup):
  - `live_feed.run`, `price_scheduler.run` (vorher `pipeline.restore()`), `forecast.run`, `influx_writer.run`
  - Abrechnung: beim Start `backfill` der letzten 7 Tage, danach stündlich die letzten 6 h und `fill_missing_prices`
  - `HaValueCache`-Schleife alle 15 s mit den HA-Verbrauchern; `ha_publisher.run`
  - Pflege: stündlich `auth.cleanup()`; täglich 03:15 Ortszeit `backup_database` (über `next_local`)
  - bei `serve` uvicorn auf `cfg.app.host:port` mit `proxy_headers = bool(trusted_proxies)`, `forwarded_allow_ips = ",".join(trusted_proxies)`, `log_config=None`, `access_log=False`, `lifespan="off"`, Signalbehandlung wie im core (`capture_signals` überschrieben)
- `shutdown()`: `influx_writer.flush_now()`, Server beenden, Tasks abbrechen, HTTP-Clients schließen, `engine.dispose()`.
- Datenbankarbeit aus Hintergrundaufgaben läuft über `asyncio.to_thread`. Jede Hintergrundschleife fängt Fehler ab, loggt sie und läuft weiter (Global Constraints).

- [ ] **Step 1: Write the failing tests**

```python
def test_backup_creates_readable_copy_and_keeps_14(tmp_path) -> None:
    db = tmp_path / "bindaems.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE t (x INTEGER)")
        conn.execute("INSERT INTO t VALUES (42)")
    for day in range(16):
        path = backup_database(db, tmp_path / "backup", datetime(2026, 10, 1 + day, 1, 15, tzinfo=UTC))
    assert path.name == "bindaems-20261016-031500.sqlite3"
    backups = sorted((tmp_path / "backup").glob("bindaems-*.sqlite3"))
    assert len(backups) == 14 and backups[0].name == "bindaems-20261003-031500.sqlite3"
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT x FROM t").fetchone() == (42,)

def test_serve_returns_2_on_invalid_config(tmp_path, capsys) -> None:
    (tmp_path / "config.yaml").write_text("grid: {}\n")
    assert main(["serve", "--config", str(tmp_path / "config.yaml")]) == 2
    assert "Konfiguration ungültig" in capsys.readouterr().err

def test_serve_returns_2_without_internal_token(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.delenv("BINDAEMS_INTERNAL_TOKEN", raising=False)
    assert main(["--config", str(write_app_config(tmp_path))]) == 2
    assert "Secrets ungültig" in capsys.readouterr().err

def test_runtime_wires_routes_and_components(runtime) -> None:
    client = TestClient(runtime.app, base_url="https://testserver")
    runtime.auth.create_user("chris", PASSWORD, "admin", actor="cli", source="cli")
    assert client.post("/api/auth/login", json={"username": "chris", "password": PASSWORD}).status_code == 200
    body = client.get("/api/system").json()
    assert [c["name"] for c in body["components"]] == ["core", "prices", "forecast", "ledger", "influx", "ha"]
    assert "Admin „chris“ hat keine Zwei-Faktor-Anmeldung (TOTP)." in body["warnings"]
    for path in ("/api/settings", "/api/consumers", "/api/prices", "/api/forecast/pv", "/api/history/catalog"):
        assert client.get(path).status_code == 200, path

async def test_runtime_starts_and_stops_cleanly(runtime, respx_mock) -> None:
    respx_mock.route().respond(503)  # kein echter Netzverkehr
    task = asyncio.create_task(runtime.run())
    await asyncio.sleep(0.3)
    await runtime.shutdown()
    await asyncio.wait_for(task, timeout=2)

def test_app_healthcheck_returns_1_when_unreachable(monkeypatch) -> None:
    monkeypatch.setenv("BINDAEMS_APP_PORT", "1")
    from bindaems.app.healthcheck import main as health
    assert health() == 1
```

Fixture `runtime`: `AppRuntime(cfg, secrets, clock=ManualClock(T_APP), serve=False)` mit `cfg.app.data_dir = tmp_path`, `cfg.app.core_url = "http://127.0.0.1:1"` und `homeassistant.mqtt` auf `127.0.0.1:1` (Verbindungen scheitern sofort und laufen in den Backoff). `runtime.auth` ist als Attribut zugänglich. `respx_mock` mit `assert_all_called=False`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/test_backup.py tests/unit/app/test_cli.py tests/integration/test_app_runtime.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.app.backup'`)

- [ ] **Step 3: Implement backup, runtime, healthcheck and the `serve` command as specified**

- [ ] **Step 4: Run all checks**

Run: alle Prüfungen
Expected: grün (6 neue Tests)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests/unit/app tests/integration/test_app_runtime.py
git commit -m "feat(app): Laufzeit, Einstiegspunkt und nächtliche Sicherung"
```

---

### Task 19: Container, Compose, CI und Betriebsdoku

**Files:**
- Create: `deploy/Dockerfile.app`
- Modify: `deploy/docker-compose.yml`, `deploy/.env.example`, `.github/workflows/ci.yml`, `docs/betrieb.md`, `README.md`
- Test: `tests/unit/test_deploy_files.py`

**Interfaces:**
- Consumes: Einstiegspunkt `python -m bindaems.app` (Task 18), Healthcheck (Task 18)
- Produces: Image `ghcr.io/binda5000/bindaems-app`; Compose-Dienst `ems-app`

**Festlegungen:**
- `Dockerfile.app` wie `Dockerfile.core`, aber `uv sync --locked --no-dev --extra app`; Benutzer `ems` (UID 10001); `/data` und `/backup` angelegt und `ems` gehörend; `VOLUME ["/data", "/backup"]`; `EXPOSE 8080`; `HEALTHCHECK … CMD ["python", "-m", "bindaems.app.healthcheck"]`; `ENTRYPOINT ["python", "-m", "bindaems.app"]`; `CMD ["serve", "--config", "/config/config.yaml"]`.
- Compose `ems-app`:
  - `image: ghcr.io/binda5000/bindaems-app:${BINDAEMS_VERSION:-latest}`, `build` mit `deploy/Dockerfile.app`, `env_file: .env`
  - Volumes `./config.yaml:/config/config.yaml:ro`, `app-data:/data`, `backup:/backup`
  - `ports: ["8080:8080"]` mit Kommentar zu Präzisierung 12, `networks: [internal]`, `depends_on: [ems-core]`, `restart: unless-stopped`, Logging wie `ems-core`
  - neue Volumes `app-data` und `backup`
- `.env.example`: Kommentarzeile `# BINDAEMS_APP_PORT=8080` (Healthcheck, falls `app.port` geändert wurde).
- CI: der Job `docker` baut über eine Matrix `component: [core, app]` je `deploy/Dockerfile.${{ matrix.component }}` nach `ghcr.io/binda5000/bindaems-${{ matrix.component }}` (Tags wie bisher).
- `docs/betrieb.md` auf „Stand: Phase 1b“ mit neuen Abschnitten:
  - ems-app starten
  - ersten Admin anlegen: `docker compose run --rm ems-app create-admin --username <name>`
  - Reverse Proxy: Weiterleitung auf `<vm>:8080`, WebSocket-Upgrade für `/api/live`, `X-Forwarded-For`/`-Proto` setzen; `app.trusted_proxies`, `app.cookie_secure`; Firewall nur für den Proxy; Alternative gemeinsames Docker-Netz
  - Mosquitto: Benutzer `bindaems` mit ACL (schreiben `homeassistant/#` und `bindaems/#`, lesen `homeassistant/status`); Passwort in `BINDAEMS_HA_MQTT_PASSWORD`
  - Tarif nach der Inbetriebnahme eintragen (Netzentgelte, Abgaben, OeMAG) und Abrechnung mit `POST /api/ledger/reprice` neu bewerten
  - Prüfprotokoll Preise (Task 14)
  - Sicherung und Wiederherstellung (app stoppen, Sicherung als `/data/bindaems.sqlite3` zurückkopieren)
  - Einstellungen exportieren und importieren
  - Liste der HA-Entitäten
- `README.md`: Entwicklungsbefehl `uv sync --extra core --extra app --extra dev`.

- [ ] **Step 1: Write the failing tests** (in `test_deploy_files.py`)

```python
def test_compose_has_app_service() -> None:
    compose = yaml.safe_load(Path("deploy/docker-compose.yml").read_text())
    app = compose["services"]["ems-app"]
    assert Path(app["build"]["dockerfile"]).exists() and app["ports"] == ["8080:8080"]
    assert app["networks"] == ["internal"] and "ems-core" in app["depends_on"]
    assert {"./config.yaml:/config/config.yaml:ro", "app-data:/data", "backup:/backup"} <= set(app["volumes"])
    assert {"core-data", "app-data", "backup"} <= set(compose["volumes"])

def test_app_dockerfile_runs_as_non_root_with_healthcheck() -> None:
    text = Path("deploy/Dockerfile.app").read_text()
    assert "--extra app" in text and "USER ems" in text
    assert 'CMD ["python", "-m", "bindaems.app.healthcheck"]' in text
    assert 'ENTRYPOINT ["python", "-m", "bindaems.app"]' in text
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_deploy_files.py -q`
Expected: FAIL (`KeyError: 'ems-app'`)

- [ ] **Step 3: Add Dockerfile, Compose service, CI matrix and documentation as specified**

- [ ] **Step 4: Run all checks, the compose check and the image build**

Run: alle Prüfungen; dann `cp deploy/config.example.yaml deploy/config.yaml && touch deploy/.env && docker compose -f deploy/docker-compose.yml config -q` (danach beide Dateien wieder löschen); falls Docker verfügbar: `docker build -f deploy/Dockerfile.app -t bindaems-app:dev .`
Expected: grün (2 neue Tests); Compose-Prüfung ohne Ausgabe; Build erfolgreich (sonst übernimmt die CI den Build)

- [ ] **Step 5: Commit**

```bash
git add deploy .github/workflows/ci.yml docs/betrieb.md README.md tests/unit/test_deploy_files.py
git commit -m "feat(deploy): Container ems-app, Compose, CI und Betriebsdoku"
```

---

## Ausblick: Plan 1c (Web-UI)

- SvelteKit (adapter-static, TypeScript) mit ECharts, ausgeliefert über `app.ui_dir`; das Image der app erhält dafür eine Node-Build-Stufe.
- Seiten: Login (mit TOTP), Dashboard (Energiefluss-SVG aus `/api/live`, Preis jetzt und nächste 3 h, Warnungen), System (`/api/system`), Verbraucher (Baum mit „Sonstiges“, Verwaltung), Verlauf (`/api/history`), Einstellungen überwiegend lesend (Tarif, OeMAG, harte Grenzen), Benutzer (Admin).
- Mobil zuerst, hell und dunkel, PWA-Manifest; CSP-tauglich (Inline-Skripte nur über die Hashes aus Task 3).
- Danach Abnahme der Phase 1 (Spec 18).

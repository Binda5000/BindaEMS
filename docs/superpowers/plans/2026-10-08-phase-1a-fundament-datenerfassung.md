# Phase 1a – Fundament und Datenerfassung: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein lauffähiger Container `ems-core`, der Cerbo GX, EVCS NS, Tesla Wall Connector, Tessie und Home Assistant **nur liest**, daraus einen geprüften Zustand samt abgeleiteten Größen und Viertelstunden-Energieflüssen bildet, alles in InfluxDB 1.x aufzeichnet, mitregelnde Systeme erkennt, eine Selbstprüfung ausführt und den Zustand über eine interne API bereitstellt.

**Architecture:** Python-asyncio-Prozess. Adapter schreiben Messwerte mit Zeitstempel in einen `StateStore`. Ein 1-s-Zyklus bildet daraus einen `Snapshot` und berechnet abgeleitete Größen, Flusszuordnung, Abtastung für InfluxDB sowie Prüfungen. Eine FastAPI-App liefert Health, State und einen WebSocket-Stream für die spätere `ems-app`. In Phase 1a gibt es keinerlei Schreibzugriffe auf Geräte.

**Tech Stack:** Python 3.12, uv, pydantic v2 + pydantic-settings, PyYAML, aiomqtt, pymodbus 3.x, httpx, websockets, FastAPI + uvicorn, structlog; Tests mit pytest, pytest-asyncio, hypothesis, respx; ruff, mypy, import-linter; Docker Compose.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`. Ausführende lesen Spec und Plan zusammen; Abschnittsverweise wie „Spec 6.2“ beziehen sich darauf.

## Einordnung

Phase 1 der Spec (Abschnitt 18) wird in drei Pläne geteilt. Jeder liefert für sich lauffähige Software:

| Plan | Inhalt |
|---|---|
| **1a** (dieser Plan) | Fundament und Datenerfassung (ems-core, nur lesend) |
| **1b** | ems-app-Backend: Login und Rollen, Einstellungen, Verbraucher, Preise und Tarif v1, PV-Prognose, Abrechnung v1, HA-Sensoren |
| **1c** | Web-UI: Login, Dashboard, System, Verbraucher, Verlauf |

Die Abnahme der Phase 1 (Spec 18) erfolgt nach 1c. Die 7-tägige Aufzeichnung kann aber schon nach 1a beginnen.

## Präzisierungen gegenüber der Spec

1. **EVCS-Werte:** Leistung, Status und Energie der EVCS liest 1a über den GX-Dienst `evcharger` per MQTT. Grund: Per Modbus sind nur die Register 5000, 5007–5010, 5017 und 5018 belegt (Spec 3.5). Das Prüfprotokoll-Werkzeug (Task 19) erstellt einen Registerdump von 5000–5199; fehlende Register werden nach dem Abgleich in einem späteren Plan ergänzt.
2. **Flüsse in InfluxDB:** Der core schreibt die Viertelstunden-Flüsse zusätzlich als Messung `flows` nach InfluxDB. Das ergänzt Spec 11.2. Die app (Plan 1b) baut ihre Abrechnung daraus und aus dem Stream auf.
3. **HA-Werte als Zustandswerte:** HA-Werte gelten, solange die Verbindung steht und die Entität verfügbar ist. Bei Verbindungsverlust sind sie sofort „veraltet“ (Spec 8.4). Die 30-min-Grenze aus Spec 6.1 entfällt, weil HA nur Änderungen meldet und ein unveränderter SOC sonst fälschlich veralten würde.
4. **Eigene Bilanzrechnung:** Hauslast und Akku-AC-Leistung berechnet der core aus Netzzähler, vebus-AC-In/-Out und PV. Die systemcalc-Verbrauchswerte werden nur aufgezeichnet. So ist eindeutig, dass die netzseitigen Wallboxen abgezogen werden.

## Global Constraints

- **Paket und Werkzeuge:** Python ≥ 3.12. Das Paket `bindaems` liegt unter `src/bindaems`. Abhängigkeiten verwaltet uv mit `uv.lock`. Extras: `core` und `dev`; `app` kommt in Plan 1b.
- **Importgrenzen** (import-linter-Verträge):
  - `bindaems.core` und `bindaems.app` importieren nur aus `bindaems.shared`, nie voneinander.
  - `bindaems.shared` importiert weder `core` noch `app`.
  - `bindaems.tools` darf `core` und `shared` importieren.
- **Zeit:** intern zeitzonenbewusst in UTC. Naive `datetime` lösen `ValueError` aus. Slots dauern 15 min und beginnen um :00/:15/:30/:45 UTC. Anzeige-Zeitzone ist Europe/Vienna.
- **Keine Gerätezugriffe, die schreiben.** Erlaubt ist nur:
  - MQTT: Publizieren ausschließlich auf `R/<portal>/keepalive`.
  - Modbus: nur Holding-Register lesen.
  - Tessie: nur `GET /{vin}/state`.
  - HA-WebSocket: nur die Nachrichtentypen `auth`, `subscribe_entities` und `ping`.
  - Jeder Adapter hat einen Test, der das belegt.
- **Frische (Spec 6.1):**

  | Quelle | Grenze |
  |---|---|
  | Victron | 5 s |
  | EVCS (Modbus) | 5 s |
  | Wall Connector | 10 s |
  | Tessie | 900 s |
  | HA | keine Altersgrenze; nur Verbindungs- und Verfügbarkeitsstatus (Präzisierung 3) |

  Zustandswerte (`SignalKind.STATE`) gelten, solange ihre Quelle verbunden ist.
- **MQTT:**
  - Keepalive alle 30 s.
  - Der erste Keepalive nach jedem Verbindungsaufbau hat leeren Payload (vollständige Neuveröffentlichung). Danach ist der Payload `{"keepalive-options": ["suppress-republish"]}`.
  - TLS sowie Benutzer und Passwort werden unterstützt.
- **InfluxDB 1.x:**
  - Datenbank `bindaems`.
  - Retention Policy `raw` mit 90 d (Default) und `long` unbegrenzt.
  - Schreiben über `/write` mit `precision=ms`, gzip und Basic Auth.
  - Spool höchstens 500 MB bzw. 7 Tage.
- **Abtastung:**

  | Signale | Takt |
  |---|---|
  | Leistungen und Ströme von Netz, Akku, PV, Wallboxen; abgeleitete Leistungen | 2 s |
  | übrige Messwerte | 10 s |
  | SOC | 30 s |
  | Zählerstände | 60 s |
  | Zustandswerte | bei Änderung |
- **Regelzyklus:** 1 s, Budget 200 ms. Netzwerkzugriffe laufen nie im Zyklus selbst.
- **Logs:** structlog als JSON auf stdout.
- **Sprache:** Nutzertexte (Alarme, Selbstprüfung, Prüfprotokoll) auf Deutsch, exakt wie im Plan angegeben. Code und Bezeichner auf Englisch.
- **Secrets** kommen nur aus Umgebungsvariablen mit dem Präfix `BINDAEMS_`; nichts davon gehört ins Repository.
- **Konfiguration:** `config.yaml` wird beim Start validiert. Ist sie ungültig, endet der Prozess mit Exitcode 2 und einer deutschen Fehlermeldung.

## Review Focus

Die fünf Fälle, die am ehesten Probleme machen, jeweils mit dem Task, der den Test dafür enthält:

1. **MQTT-Payload `{"value": null}` oder kaputtes JSON** (Gerät am Cerbo getrennt): Das Signal wird `INVALID`, ohne Absturz und ohne dass ein Altwert als frisch gilt. → Task 5 (`test_null_value_yields_invalid_update`, `test_broken_json_ignored`) und Task 4 (`test_none_and_nan_are_invalid`).
2. **Neustart von Cerbo oder Broker:** Wiederverbinden mit Backoff. Der erste Keepalive danach ist wieder leer, Zustandswerte sind danach wieder `OK`. → Task 6 (`test_reconnect_requests_full_republish_again`).
3. **Zeitumstellung** (2026-03-29, 2026-10-25): Tage mit 92 bzw. 100 Slots; Slots werden über die Umstellung hinweg korrekt abgeschlossen. → Task 2 (`test_local_day_slots_dst_*`) und Task 12 (`test_accumulator_slots_across_dst_change`).
4. **InfluxDB stundenlang nicht erreichbar:** Der Spool wächst bis zur Grenze, dann werden die ältesten Dateien verworfen. `write()` blockiert nie. → Task 13 (`test_spool_enforces_max_bytes_drops_oldest`, `test_write_is_non_blocking`).
5. **Tesla schläft oder Tessie antwortet mit 5xx/429:** Kein Weckversuch, Backoff mindestens 60 s, Werte nach 900 s `STALE`. → Task 9 (`test_server_error_backs_off_at_least_60s`, `test_values_go_stale_after_900s`).

---

## Dateistruktur nach Phase 1a

```
pyproject.toml · uv.lock · .python-version · .gitignore · .dockerignore · README.md
.github/workflows/ci.yml
src/bindaems/__init__.py
src/bindaems/shared/{__init__,timeutil,config,domain}.py
src/bindaems/app/__init__.py                         (leer, Platzhalter für 1b)
src/bindaems/core/{__init__,__main__,runtime,broadcast,logging,healthcheck}.py
src/bindaems/core/state/{__init__,store,derived,plausibility}.py
src/bindaems/core/adapters/{__init__,base,victron_topics,victron_mqtt,evcs,twc,tessie,homeassistant}.py
src/bindaems/core/accounting/{__init__,flows}.py
src/bindaems/core/telemetry/{__init__,lineprotocol,spool,influx,sampler}.py
src/bindaems/core/checks/{__init__,competitors,selfcheck}.py
src/bindaems/core/api/{__init__,app}.py
src/bindaems/tools/{__init__,verify_checks,verify_part1}.py
deploy/{Dockerfile.core,docker-compose.yml,config.example.yaml,.env.example,influxdb-setup.sh}
docs/betrieb.md · docs/verification/README.md
tests/unit/…  tests/property/…  tests/integration/…  tests/fixtures/…
```

---

### Task 1: Projektgerüst und Werkzeuge

**Files:**
- Create: `pyproject.toml`, `.python-version` (`3.12`), `.gitignore`, `README.md`, `.github/workflows/ci.yml`
- Create: `src/bindaems/__init__.py`, `src/bindaems/shared/__init__.py`, `src/bindaems/core/__init__.py`, `src/bindaems/app/__init__.py`, `src/bindaems/tools/__init__.py`
- Test: `tests/unit/test_package.py`

**Interfaces:**
- Produces: `bindaems.__version__: str` (`"0.1.0"`)

**Festlegungen in `pyproject.toml`:**
- `[project]`:
  - `name = "bindaems"`, `requires-python = ">=3.12"`.
  - `dependencies = ["pydantic>=2.7", "pydantic-settings>=2.3", "pyyaml>=6.0", "structlog>=24.1"]`.
  - Extras:
    - `core = ["aiomqtt>=2.3", "pymodbus>=3.7", "httpx>=0.27", "websockets>=13", "fastapi>=0.115", "uvicorn[standard]>=0.30"]`
    - `dev = ["pytest>=8.3", "pytest-asyncio>=0.24", "hypothesis>=6.100", "respx>=0.21", "ruff>=0.6", "mypy>=1.11", "import-linter>=2.1", "types-PyYAML"]`
- Build-Backend: hatchling, Pakete aus `src/bindaems`.
- ruff: `line-length = 100`, `target-version = "py312"`, `select = ["E","F","I","B","UP","ASYNC","S","RUF"]`. In `tests/**` ist `S101` ignoriert.
- mypy: `strict = true` für `bindaems.shared` und `bindaems.core`; Plugin `pydantic.mypy`.
- pytest: `asyncio_mode = "auto"`, `testpaths = ["tests"]`.
- import-linter: drei `forbidden`-Verträge gemäß „Importgrenzen“ in den Global Constraints.
- CI (`ci.yml`): Läuft bei push und pull_request.
  - `astral-sh/setup-uv`, dann `uv sync --locked --extra core --extra dev`.
  - Danach `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`, `uv run lint-imports`, `uv run pytest -q`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_package.py
import bindaems

def test_version_is_defined() -> None:
    assert bindaems.__version__ == "0.1.0"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_package.py -v`
Expected: FAIL (`ModuleNotFoundError: bindaems` bzw. fehlendes `pyproject.toml`)

- [ ] **Step 3: Create the files listed above.** Die Paket-`__init__`-Dateien sind leer, nur `bindaems/__init__.py` enthält `__version__ = "0.1.0"`. Danach `uv lock` und `uv sync --extra core --extra dev`.
  - `README.md` (deutsch, kurz): Zweck, Links zu Spec und Plänen, Entwickler-Schnellstart (`uv sync --extra core --extra dev`, `uv run pytest`).

- [ ] **Step 4: Run tests and all checks**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run lint-imports`
Expected: `1 passed`; ruff, mypy und import-linter ohne Befund („Contracts: 3 kept, 0 broken“)

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock .python-version .gitignore README.md .github src tests
git commit -m "chore: Projektgerüst mit uv, ruff, mypy, pytest und CI"
```

---

### Task 2: Zeit- und Slot-Hilfen

**Files:**
- Create: `src/bindaems/shared/timeutil.py`
- Test: `tests/unit/shared/test_timeutil.py`

**Interfaces:**
- Produces:
  - Konstanten: `SLOT: timedelta` (15 min), `LOCAL_TZ: ZoneInfo` (`"Europe/Vienna"`).
  - Funktionen:
    - `ensure_utc(dt: datetime) -> datetime`
    - `slot_start(dt: datetime) -> datetime`
    - `slot_end(dt: datetime) -> datetime`
    - `slots_between(start: datetime, end: datetime) -> list[datetime]` (halboffen `[start, end)`)
    - `local_day_slots(day: date) -> list[datetime]` (UTC-Slotanfänge des lokalen Kalendertags)
  - Uhren:
    - `class Clock(Protocol): def now(self) -> datetime`
    - `class SystemClock` (liefert `datetime.now(UTC)`)
    - `class ManualClock(start: datetime)` mit `now()` und `advance(delta: timedelta | float) -> None` (float = Sekunden)

- [ ] **Step 1: Write the failing tests**

```python
from datetime import UTC, date, datetime, timedelta
import pytest
from bindaems.shared.timeutil import (LOCAL_TZ, ManualClock, ensure_utc, local_day_slots,
                                       slot_end, slot_start, slots_between)

def test_ensure_utc_rejects_naive() -> None:
    with pytest.raises(ValueError):
        ensure_utc(datetime(2026, 1, 1, 12, 0))

def test_ensure_utc_converts_vienna() -> None:
    assert ensure_utc(datetime(2026, 7, 1, 12, 0, tzinfo=LOCAL_TZ)) == datetime(2026, 7, 1, 10, 0, tzinfo=UTC)

def test_slot_start_floors_to_quarter_hour() -> None:
    assert slot_start(datetime(2026, 10, 8, 9, 44, 59, tzinfo=UTC)) == datetime(2026, 10, 8, 9, 30, tzinfo=UTC)

def test_slot_end() -> None:
    assert slot_end(datetime(2026, 10, 8, 9, 30, tzinfo=UTC)) == datetime(2026, 10, 8, 9, 45, tzinfo=UTC)

def test_slots_between_half_open() -> None:
    s = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
    assert slots_between(s, s + timedelta(hours=1)) == [s + timedelta(minutes=m) for m in (0, 15, 30, 45)]

def test_local_day_slots_normal_day() -> None:
    slots = local_day_slots(date(2026, 10, 8))
    assert len(slots) == 96 and slots[0] == datetime(2026, 10, 7, 22, 0, tzinfo=UTC)

def test_local_day_slots_dst_spring() -> None:
    slots = local_day_slots(date(2026, 3, 29))
    assert len(slots) == 92 and slots[0] == datetime(2026, 3, 28, 23, 0, tzinfo=UTC)

def test_local_day_slots_dst_autumn() -> None:
    slots = local_day_slots(date(2026, 10, 25))
    assert len(slots) == 100 and slots[0] == datetime(2026, 10, 24, 22, 0, tzinfo=UTC)

def test_manual_clock_advance() -> None:
    c = ManualClock(datetime(2026, 1, 1, tzinfo=UTC))
    c.advance(1.5)
    assert c.now() == datetime(2026, 1, 1, 0, 0, 1, 500000, tzinfo=UTC)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/shared/test_timeutil.py -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: Implement `timeutil.py`.** Slot-Berechnung in UTC über `dt.replace(minute=dt.minute - dt.minute % 15, second=0, microsecond=0)`. `local_day_slots` arbeitet von lokaler Mitternacht bis zur nächsten lokalen Mitternacht, beide nach UTC umgerechnet.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/shared/test_timeutil.py -v`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared/timeutil.py tests/unit/shared/test_timeutil.py
git commit -m "feat(shared): Zeit- und Slot-Hilfen inkl. Zeitumstellung"
```

---

### Task 3: Konfiguration – Schema, Loader, Beispiel

**Files:**
- Create: `src/bindaems/shared/config.py`, `deploy/config.example.yaml`, `deploy/.env.example`
- Create: `tests/conftest.py` mit der Fixture `cfg`, die `load_config(Path("deploy/config.example.yaml"))` zurückgibt. Spätere Tasks verwenden sie.
- Test: `tests/unit/shared/test_config.py`

**Interfaces:**
- Produces: alle Modelle als pydantic `BaseModel` mit `model_config = ConfigDict(extra="forbid", frozen=True)`.
  - `Phase = Literal["L1", "L2", "L3"]`
  - `LatLon(lat: float [-90, 90], lon: float [-180, 180])`
  - `SiteConfig(timezone: str = "Europe/Vienna", location: LatLon)`
  - `GridConfig(phases: Literal[3] = 3, voltage_nominal_v: float = 230.0, fuse_a: float (>0, ≤200), fuse_margin_a: float = 2.0 (≥0))`. Validator: `fuse_margin_a < fuse_a`.
  - `BatteryConfig(usable_kwh: float (>0), reserve_soc_pct: float [0, 100], max_charge_w: float (>0), max_discharge_w: float (>0), soc_max_pct: float = 100.0 (0, 100])`. Validator: `reserve_soc_pct < soc_max_pct`.
  - `MqttConfig(host: str, port: int = 8883, tls: bool = True, tls_verify: bool = True, tls_ca_file: Path | None = None, username: str | None = None, portal_id: str | None = None, keepalive_s: float = 30.0)`
  - `ModbusEndpoint(host: str, port: int = 502)`
  - `VictronInstances(grid: int | None = None, vebus: int | None = None, battery: int | None = None, pv: dict[int, str] = {}, acload: dict[int, str] = {}, evcharger: dict[int, str] = {})`. Die Dicts bilden Instanz → logische ID ab.
  - `VictronExpected(hub4_mode: int = 1, batterylife_states: list[int] = [10])`. Die Werte bestätigt das Prüfprotokoll.
  - Kleine Modelle: `WriteBudget(per_hour: int = 12, per_day: int = 100)`, `WatchdogConfig(heartbeat_s: float = 10.0, timeout_s: float = 90.0)`.
  - `VictronConfig(mqtt: MqttConfig, modbus: ModbusEndpoint, instances: VictronInstances = VictronInstances(), expected: VictronExpected = VictronExpected(), max_grid_charge_setpoint_w: float (≥0), persistent_writes: WriteBudget = WriteBudget(), watchdog: WatchdogConfig = WatchdogConfig())`
  - `EvcsConfig(type: Literal["victron_evcs_ns"], host: str, port: int = 502, unit_id: int = 1, min_a: int = 6, max_a: int = 16, safe_a: int = 6, phase_map: tuple[Phase, Phase, Phase] = ("L1","L2","L3"), live_allowed: bool = False)`. Validatoren:
    - `6 ≤ min_a ≤ safe_a ≤ max_a ≤ 32`
    - `phase_map` ist eine Permutation von L1–L3
  - `TwcConfig(type: Literal["tesla_wall_connector_gen3"], host: str, max_a: int = 16, phase_map: … = ("L1","L2","L3"))`
  - `WallboxConfig = Annotated[EvcsConfig | TwcConfig, Field(discriminator="type")]`
  - `TessieVehicle(vin: str (Länge 17))`
  - `VehicleConfig(name: str, usable_kwh: float (>0), phases: Literal[1, 2, 3], min_a: int, max_a: int, default_wallbox: str, live_allowed: bool = False, tessie: TessieVehicle | None = None, soc_entity: str | None = None, home_radius_m: float = 150.0)`
  - `PvPlane(name: str, kwp: float (>0), tilt_deg: float [0, 90], azimuth_deg: float [-180, 180])`, `PvConfig(planes: list[PvPlane] (min. 1), inverter_ac_max_w: float (>0))`
  - `HomeAssistantConfig(url: str, entities: dict[str, str] = {})`. Der Schlüssel ist der Signalname, der Wert die `entity_id`.
  - `InfluxConfig(url: str, database: str = "bindaems", username: str | None = None, ha_database: str | None = None)`
  - `TelemetryConfig(spool_dir: Path = Path("/data/spool"), spool_max_mb: int = 500, spool_max_age_days: int = 7)`, `CoreApiConfig(host: str = "0.0.0.0", port: int = 8081)`
  - `Config(site, grid, battery, victron, wallboxes: dict[str, WallboxConfig], wallbox_priority: list[str] | None = None, vehicles: dict[str, VehicleConfig], pv: PvConfig, homeassistant: HomeAssistantConfig | None = None, influxdb: InfluxConfig, telemetry: TelemetryConfig = TelemetryConfig(), core_api: CoreApiConfig = CoreApiConfig())`
    - Validatoren:
      - `default_wallbox` existiert in `wallboxes`.
      - `wallbox_priority` (falls gesetzt) ist eine Permutation der Wallbox-Schlüssel.
      - `soc_entity` gesetzt ⇒ `homeassistant` gesetzt.
    - Property `wallbox_order: list[str]`: `wallbox_priority` oder die Schlüsselreihenfolge.
  - `class ConfigError(Exception)` und `load_config(path: Path) -> Config`. Gelesen wird mit `yaml.safe_load`. Ein `ValidationError` wird zu `ConfigError` mit dem Text `"Konfiguration ungültig: <pfad>: <meldung>"`, eine Zeile je Fehler; `<pfad>` sind die mit Punkten verbundenen Feldnamen, z. B. `grid.fuse_margin_a`.
  - `class Secrets(BaseSettings)` mit `env_prefix="BINDAEMS_"`:
    - `mqtt_password`, `tessie_token`, `ha_token`, `influx_password`: jeweils `SecretStr | None = None`
    - `internal_token: SecretStr`, mindestens 32 Zeichen
  - `load_secrets() -> Secrets`
- `deploy/config.example.yaml`: Werte aus Spec 11.5, ergänzt um:
  - `victron.instances: {grid: 30, vebus: 276, battery: 512, pv: {31: huawei}, acload: {32: obergeschoss}, evcharger: {40: evcs}}`
  - `homeassistant: {url: http://homeassistant.lan:8123, entities: {vehicle.egolf.soc_pct: sensor.egolf_soc}}`
  - `influxdb: {url: http://influx.lan:8086, username: bindaems, ha_database: homeassistant}`
  - Tesla mit `tessie: {vin: 5YJ3E7EB0MF000000}`
  - Wallbox-Reihenfolge `evcs`, dann `twc`
- `deploy/.env.example`: die fünf `BINDAEMS_*`-Variablen mit leeren Werten und einem Kommentar zu `chmod 600`.

- [ ] **Step 1: Write the failing tests**

```python
from pathlib import Path
import pytest, yaml
from bindaems.shared.config import ConfigError, load_config, load_secrets

EXAMPLE = Path("deploy/config.example.yaml")

def write_cfg(tmp_path: Path, mutate) -> Path:
    data = yaml.safe_load(EXAMPLE.read_text())
    mutate(data)
    p = tmp_path / "config.yaml"
    p.write_text(yaml.safe_dump(data))
    return p

def test_example_config_is_valid() -> None:
    cfg = load_config(EXAMPLE)
    assert cfg.grid.fuse_a == 35
    assert cfg.battery.reserve_soc_pct == 20
    assert cfg.wallbox_order == ["evcs", "twc"]
    assert cfg.victron.instances.pv == {31: "huawei"}

def test_unknown_key_rejected(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["grid"].update(foo=1))
    with pytest.raises(ConfigError, match=r"grid\.foo"):
        load_config(p)

def test_fuse_margin_must_be_below_fuse(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["grid"].update(fuse_a=10, fuse_margin_a=10))
    with pytest.raises(ConfigError, match="Konfiguration ungültig"):
        load_config(p)

def test_reserve_must_be_below_soc_max(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["battery"].update(reserve_soc_pct=100))
    with pytest.raises(ConfigError):
        load_config(p)

def test_phase_map_must_be_permutation(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["wallboxes"]["evcs"].update(phase_map=["L1", "L1", "L2"]))
    with pytest.raises(ConfigError):
        load_config(p)

def test_evcs_current_order(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["wallboxes"]["evcs"].update(safe_a=4))
    with pytest.raises(ConfigError):
        load_config(p)

def test_vehicle_default_wallbox_must_exist(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["vehicles"]["tesla"].update(default_wallbox="garage"))
    with pytest.raises(ConfigError):
        load_config(p)

def test_internal_token_min_length(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINDAEMS_INTERNAL_TOKEN", "kurz")
    with pytest.raises(Exception):
        load_secrets()

def test_secrets_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BINDAEMS_INTERNAL_TOKEN", "x" * 32)
    monkeypatch.setenv("BINDAEMS_TESSIE_TOKEN", "abc")
    assert load_secrets().tessie_token.get_secret_value() == "abc"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/shared/test_config.py -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: Implement `config.py` and create both example files as specified**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/shared/test_config.py -v`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared/config.py deploy/config.example.yaml deploy/.env.example tests/unit/shared/test_config.py
git commit -m "feat(shared): validiertes Konfigurationsschema mit Beispielkonfiguration"
```

---

### Task 4: Domänentypen und Zustandsspeicher

**Files:**
- Create: `src/bindaems/shared/domain.py`, `src/bindaems/core/state/__init__.py`, `src/bindaems/core/state/store.py`
- Create: `tests/helpers.py` mit den gemeinsamen Test-Helfern:
  - `T0 = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)`
  - `snap(values: Mapping[str, Value], *, kind: SignalKind = MEASUREMENT, quality: Quality = OK, ts: datetime = T0, source: str = "victron") -> Snapshot`
  - `snap_at(ts: datetime, values, **kw) -> Snapshot`
  - Tasks 6 und 14 erweitern die Datei.
- Test: `tests/unit/core/state/test_store.py`

**Interfaces:**
- Consumes: `Clock`, `ManualClock` (Task 2)
- Produces, in `shared/domain.py`:
  - `Value = float | int | str | bool | None`
  - `class Quality(StrEnum)`: `OK="ok"`, `STALE="stale"`, `INVALID="invalid"`
  - `class SignalKind(StrEnum)`: `MEASUREMENT="measurement"`, `STATE="state"`
  - `@dataclass(frozen=True, slots=True) class Reading`: `value: Value; ts: datetime; quality: Quality; source: str; kind: SignalKind`
  - `@dataclass(frozen=True) class Snapshot`: `ts: datetime; readings: Mapping[str, Reading]` (eine `MappingProxyType`) mit
    - `get(signal) -> Reading | None`
    - `ok(signal) -> Value`: Wert nur bei `OK`, sonst `None`
    - `matching(prefix: str) -> dict[str, Reading]`
  - `class Severity(StrEnum)`: `INFO`, `WARNING`, `ERROR`
  - `@dataclass(frozen=True) class Alarm`: `id: str; severity: Severity; message: str; since: datetime`
- Produces, in `core/state/store.py`: `class StateStore(clock: Clock)` mit
  - `register_source(name: str, freshness_s: float | None) -> None`
  - `update(signal: str, value: Value, *, source: str, kind: SignalKind, ts: datetime | None = None) -> None`. Eine unbekannte Quelle löst `KeyError` aus. `None`, `NaN` und `±inf` ergeben `INVALID`.
  - `set_connected(source: str, connected: bool) -> None`
  - `snapshot() -> Snapshot`. Die Qualität wird bei jedem Aufruf neu bestimmt:
    - `MEASUREMENT`: `STALE`, wenn das Alter über `freshness_s` liegt oder die Quelle getrennt ist.
    - `STATE`: `STALE`, wenn die Quelle getrennt ist.
    - `INVALID` bleibt `INVALID`.
  - `ok_since(signal: str) -> datetime | None`: Beginn der aktuellen ununterbrochenen `OK`-Phase, ausgewertet bei jedem `snapshot()`.

- [ ] **Step 1: Write the failing tests**

```python
from datetime import UTC, datetime
import pytest
from bindaems.core.state.store import StateStore
from bindaems.shared.domain import Quality, SignalKind
from bindaems.shared.timeutil import ManualClock

T0 = datetime(2026, 10, 8, 10, 0, tzinfo=UTC)
M, S = SignalKind.MEASUREMENT, SignalKind.STATE

def make() -> tuple[ManualClock, StateStore]:
    clock = ManualClock(T0)
    store = StateStore(clock)
    store.register_source("victron", 5.0)
    store.set_connected("victron", True)
    return clock, store

def test_measurement_ok_then_stale() -> None:
    clock, s = make()
    s.update("grid.power_w", 1200.0, source="victron", kind=M)
    assert s.snapshot().get("grid.power_w").quality is Quality.OK
    clock.advance(5.1)
    assert s.snapshot().get("grid.power_w").quality is Quality.STALE

def test_state_signal_ok_while_connected() -> None:
    clock, s = make()
    s.update("ess.hub4_mode", 1, source="victron", kind=S)
    clock.advance(3600)
    assert s.snapshot().ok("ess.hub4_mode") == 1
    s.set_connected("victron", False)
    assert s.snapshot().get("ess.hub4_mode").quality is Quality.STALE

def test_none_and_nan_are_invalid() -> None:
    _, s = make()
    s.update("grid.l1.power_w", None, source="victron", kind=M)
    s.update("grid.l2.power_w", float("nan"), source="victron", kind=M)
    snap = s.snapshot()
    assert snap.get("grid.l1.power_w").quality is Quality.INVALID
    assert snap.get("grid.l2.power_w").quality is Quality.INVALID
    assert snap.ok("grid.l1.power_w") is None

def test_unknown_source_rejected() -> None:
    _, s = make()
    with pytest.raises(KeyError):
        s.update("x", 1, source="nope", kind=M)

def test_snapshot_is_immutable() -> None:
    _, s = make()
    with pytest.raises(TypeError):
        s.snapshot().readings["x"] = None  # type: ignore[index]

def test_ok_since_resets_after_stale() -> None:
    clock, s = make()
    s.update("battery.soc_pct", 50.0, source="victron", kind=M)
    s.snapshot()
    assert s.ok_since("battery.soc_pct") == T0
    clock.advance(10); s.snapshot()
    assert s.ok_since("battery.soc_pct") is None
    s.update("battery.soc_pct", 51.0, source="victron", kind=M); s.snapshot()
    assert s.ok_since("battery.soc_pct") == clock.now()

def test_matching_prefix() -> None:
    _, s = make()
    s.update("grid.l1.power_w", 1.0, source="victron", kind=M)
    s.update("battery.soc_pct", 2.0, source="victron", kind=M)
    assert set(s.snapshot().matching("grid.")) == {"grid.l1.power_w"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/state/test_store.py -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: Implement `domain.py` and `store.py` as specified**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/state/test_store.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared/domain.py src/bindaems/core/state tests/unit/core/state/test_store.py
git commit -m "feat(core): Zustandsspeicher mit Frische- und Qualitätsbewertung"
```

---

### Task 5: Victron-MQTT – Abbildung der Topics auf Signale

**Files:**
- Create: `src/bindaems/core/adapters/__init__.py`, `src/bindaems/core/adapters/victron_topics.py`
- Test: `tests/unit/core/adapters/test_victron_topics.py`

**Interfaces:**
- Consumes: `SignalKind`, `Value` (Task 4); `VictronInstances` (Task 3)
- Produces:
  - `@dataclass(frozen=True) class SignalUpdate: signal: str; value: Value; kind: SignalKind`
  - `class InstanceResolver(instances: VictronInstances)` mit `resolve(service: str, instance: int) -> str | None`:
    - **`grid`, `vebus`, `battery`:** Rückgabe `""`, wenn die Instanz der konfigurierten entspricht. Ist keine Instanz konfiguriert, gilt die zuerst gesehene als richtig. Andere Instanzen ergeben `None`.
    - **`pvinverter`, `acload`, `evcharger`:** konfigurierte ID, sonst Ersatz-ID `f"{service}{instance}"`.
    - **`system`, `settings`, `hub4`:** nur Instanz 0 gilt, Rückgabe `""`.
  - `parse_message(topic: str, payload: bytes, portal_id: str, resolver: InstanceResolver) -> list[SignalUpdate]`. Ausgewertet werden nur Topics der Form `N/<portal_id>/<service>/<instance>/<path>`. Der Payload ist JSON `{"value": …}`; ungültiges JSON ergibt `[]`.

**Abbildung (Pfad nach `N/<portal>/`):**

| Pfad | Signal | Art |
|---|---|---|
| `grid/<g>/Ac/L{n}/Power`, `/Current`, `/Voltage` | `grid.l{n}.power_w`, `.current_a`, `.voltage_v` | M |
| `grid/<g>/Ac/Power` | `grid.power_w` | M |
| `grid/<g>/Ac/Energy/Forward` / `Reverse` | `grid.energy_import_kwh` / `grid.energy_export_kwh` | M |
| `pvinverter/<p>/Ac/Power`, `Ac/L{n}/Power`, `Ac/Energy/Forward` | `pv.<id>.power_w`, `pv.<id>.l{n}.power_w`, `pv.<id>.energy_kwh` | M |
| `pvinverter/<p>/Position` | `pv.<id>.position` | S |
| `acload/<a>/Ac/Power`, `Ac/L{n}/Power`, `Ac/Energy/Forward` | `load.<id>.power_w`, `load.<id>.l{n}.power_w`, `load.<id>.energy_kwh` | M |
| `evcharger/<e>/Ac/Power`, `Ac/L{n}/Power`, `Ac/Energy/Forward`, `Current` | `wallbox.<id>.power_w`, `wallbox.<id>.l{n}.power_w`, `wallbox.<id>.total_kwh`, `wallbox.<id>.gx_current_a` | M |
| `evcharger/<e>/Status`, `Mode`, `SetCurrent`, `MaxCurrent`, `StartStop` | `wallbox.<id>.status`, `.gx_mode`, `.gx_set_current_a`, `.gx_max_current_a`, `.gx_start_stop` | S |
| `system/0/Ac/Consumption/L{n}/Power` | `consumption.l{n}.power_w` | M |
| `system/0/Ac/ConsumptionOnInput/L{n}/Power` / `ConsumptionOnOutput/…` | `consumption_input.l{n}.power_w` / `consumption_output.l{n}.power_w` | M |
| `system/0/Dc/Battery/Soc`, `Power`, `Voltage`, `Current` | `battery.soc_pct`, `battery.power_w` (+ = Laden), `battery.voltage_v`, `battery.current_a` | M |
| `system/0/Dc/Pv/Power` | `pv_dc.power_w` | M |
| `system/0/Ac/ActiveIn/Source` | `system.active_in_source` | S |
| `vebus/<v>/Ac/NumberOfPhases`, `State`, `Mode` | `vebus.phases`, `vebus.state`, `vebus.mode` | S |
| `vebus/<v>/Ac/ActiveIn/L{n}/P` / `Ac/Out/L{n}/P` | `vebus.l{n}.ac_in_power_w` / `vebus.l{n}.ac_out_power_w` | M |
| `battery/<b>/Info/MaxChargeCurrent` / `MaxDischargeCurrent`, `InstalledCapacity` | `battery.ccl_a` / `battery.dcl_a`, `battery.installed_capacity_ah` | S |
| `settings/0/Settings/CGwacs/Hub4Mode` | `ess.hub4_mode` | S |
| `settings/0/Settings/CGwacs/BatteryLife/State` | `ess.batterylife_state` | S |
| `settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit` | `ess.min_soc_pct` | S |
| `settings/0/Settings/CGwacs/AcPowerSetPoint` | `ess.grid_setpoint_w` | S |
| `settings/0/Settings/CGwacs/MaxDischargePower` | `ess.max_discharge_w` | S |
| `settings/0/Settings/SystemSetup/MaxChargeCurrent` | `dvcc.max_charge_current_a` | S |
| `settings/0/Settings/DynamicEss/Mode` | `dess.mode` | S |
| `settings/0/Settings/CGwacs/BatteryLife/Schedule/Charge/<k>/Day` | `ess.schedule.<k>.day` | S |
| `hub4/0/Overrides/<Name>` | `ess.override.<name klein>` | S |

Zusätzlich erzeugt **jedes** Topic unter `settings/0/Settings/CGwacs/`, `…/DynamicEss/` und `…/SystemSetup/` ein generisches Signal: `settings.` + Pfadsegmente ab dem Bereichsnamen, klein geschrieben und mit Punkten verbunden. Beispiel: `settings.cgwacs.hub4mode`, Art S. Damit findet das Prüfprotokoll auch Peak-Shaving-Einstellungen, deren Namen nicht bekannt sind.

- [ ] **Step 1: Write the failing tests**

```python
import json
from bindaems.core.adapters.victron_topics import InstanceResolver, SignalUpdate, parse_message
from bindaems.shared.config import VictronInstances
from bindaems.shared.domain import SignalKind

P = "c0619ab1234"
M, S = SignalKind.MEASUREMENT, SignalKind.STATE

def resolver() -> InstanceResolver:
    return InstanceResolver(VictronInstances(grid=30, vebus=276, battery=512, pv={31: "huawei"},
                                             acload={32: "obergeschoss"}, evcharger={40: "evcs"}))

def msg(path: str, value: object) -> tuple[str, bytes]:
    return f"N/{P}/{path}", json.dumps({"value": value}).encode()

def test_grid_phase_power() -> None:
    assert parse_message(*msg("grid/30/Ac/L2/Power", 812.5), P, resolver()) == [SignalUpdate("grid.l2.power_w", 812.5, M)]

def test_grid_energy_counters() -> None:
    r = resolver()
    assert parse_message(*msg("grid/30/Ac/Energy/Forward", 1234.5), P, r)[0].signal == "grid.energy_import_kwh"
    assert parse_message(*msg("grid/30/Ac/Energy/Reverse", 99.0), P, r)[0].signal == "grid.energy_export_kwh"

def test_pvinverter_uses_configured_id() -> None:
    assert parse_message(*msg("pvinverter/31/Ac/Power", 4000), P, resolver())[0].signal == "pv.huawei.power_w"

def test_unknown_pv_instance_gets_fallback_id() -> None:
    assert parse_message(*msg("pvinverter/33/Ac/Power", 1), P, resolver())[0].signal == "pv.pvinverter33.power_w"

def test_other_grid_instance_ignored() -> None:
    assert parse_message(*msg("grid/99/Ac/Power", 1), P, resolver()) == []

def test_vebus_phases_is_state() -> None:
    assert parse_message(*msg("vebus/276/Ac/NumberOfPhases", 3), P, resolver()) == [SignalUpdate("vebus.phases", 3, S)]

def test_vebus_ac_in_out() -> None:
    r = resolver()
    assert parse_message(*msg("vebus/276/Ac/ActiveIn/L1/P", 500), P, r)[0].signal == "vebus.l1.ac_in_power_w"
    assert parse_message(*msg("vebus/276/Ac/Out/L3/P", 300), P, r)[0].signal == "vebus.l3.ac_out_power_w"

def test_settings_alias_and_generic() -> None:
    out = parse_message(*msg("settings/0/Settings/CGwacs/Hub4Mode", 1), P, resolver())
    assert SignalUpdate("ess.hub4_mode", 1, S) in out
    assert SignalUpdate("settings.cgwacs.hub4mode", 1, S) in out

def test_schedule_and_dess() -> None:
    r = resolver()
    assert SignalUpdate("ess.schedule.2.day", -7, S) in parse_message(
        *msg("settings/0/Settings/CGwacs/BatteryLife/Schedule/Charge/2/Day", -7), P, r)
    assert SignalUpdate("dess.mode", 0, S) in parse_message(*msg("settings/0/Settings/DynamicEss/Mode", 0), P, r)

def test_hub4_override() -> None:
    assert parse_message(*msg("hub4/0/Overrides/Setpoint", 150), P, resolver()) == [SignalUpdate("ess.override.setpoint", 150, S)]

def test_evcharger() -> None:
    r = resolver()
    assert parse_message(*msg("evcharger/40/Ac/Power", 7360), P, r) == [SignalUpdate("wallbox.evcs.power_w", 7360, M)]
    assert parse_message(*msg("evcharger/40/Status", 2), P, r) == [SignalUpdate("wallbox.evcs.status", 2, S)]

def test_null_value_yields_invalid_update() -> None:
    assert parse_message(*msg("grid/30/Ac/L1/Power", None), P, resolver()) == [SignalUpdate("grid.l1.power_w", None, M)]

def test_broken_json_ignored() -> None:
    assert parse_message(f"N/{P}/grid/30/Ac/L1/Power", b"{kaputt", P, resolver()) == []

def test_other_portal_and_non_n_topics_ignored() -> None:
    r = resolver()
    assert parse_message("N/other/grid/30/Ac/Power", b'{"value": 1}', P, r) == []
    assert parse_message(f"R/{P}/keepalive", b"", P, r) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/adapters/test_victron_topics.py -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: Implement `victron_topics.py`.** Die Routen sind eine geordnete Liste aus (regulärer Ausdruck auf den Pfad, Signalvorlage, Art), getrennt nach Service. Das generische `settings.*`-Signal kommt bei den Settings-Teilbäumen immer zusätzlich hinzu.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/adapters/test_victron_topics.py -v`
Expected: `14 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/adapters/__init__.py src/bindaems/core/adapters/victron_topics.py tests/unit/core/adapters/test_victron_topics.py
git commit -m "feat(core): Abbildung der Victron-MQTT-Topics auf Signale"
```

---

### Task 6: Victron-MQTT-Adapter mit Keepalive und Wiederverbindung

**Files:**
- Create: `src/bindaems/core/adapters/base.py`, `src/bindaems/core/adapters/victron_mqtt.py`
- Test: `tests/unit/core/adapters/test_base.py`, `tests/unit/core/adapters/test_victron_mqtt.py`

**Interfaces:**
- Consumes:
  - `StateStore` (Task 4)
  - `parse_message`, `InstanceResolver` (Task 5)
  - `VictronConfig`, `Secrets` (Task 3)
- Produces, in `base.py`:
  - `@dataclass class AdapterHealth: name: str; connected: bool = False; last_ok: datetime | None = None; last_error: str | None = None; error_count: int = 0`
  - `class Adapter(Protocol)`: `name: str`, `async def run(self) -> None`, `def health(self) -> AdapterHealth`
  - `class Backoff(initial: float = 1.0, maximum: float = 60.0, factor: float = 2.0)` mit `next() -> float` und `reset() -> None`
- Produces, in `victron_mqtt.py`:
  - `@dataclass(frozen=True) class MqttMessage: topic: str; payload: bytes`
  - `class MqttTransport(Protocol)`: async context manager mit `subscribe(topic: str) -> None`, `publish(topic: str, payload: bytes) -> None` und `messages() -> AsyncIterator[MqttMessage]`
  - `class AiomqttTransport`: echte Implementierung mit `aiomqtt.Client`. Bei `tls=True` kommt ein `ssl.SSLContext`; bei `tls_verify=False` sind `check_hostname=False` und `CERT_NONE` gesetzt, `tls_ca_file` wird geladen, falls gesetzt.
  - `KEEPALIVE_SUPPRESS = b'{"keepalive-options": ["suppress-republish"]}'`
  - `SUBSCRIPTIONS = ("system/0/#", "grid/+/#", "pvinverter/+/#", "acload/+/#", "vebus/+/#", "battery/+/#", "evcharger/+/#", "hub4/0/#", "settings/0/Settings/CGwacs/#", "settings/0/Settings/DynamicEss/#", "settings/0/Settings/SystemSetup/#")`. Jeder Eintrag wird als `N/<portal>/<eintrag>` abonniert.
  - `class VictronMqttAdapter(cfg: VictronConfig, password: SecretStr | None, store: StateStore, transport_factory: Callable[[], MqttTransport], *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep)`
    - Feste Werte: `name = "victron"`; registriert die Quelle `"victron"` mit Frische 5.0.
    - Attribut `portal_id: str | None` (aus `cfg.mqtt.portal_id` oder automatisch ermittelt).
    - **Ablauf von `run()` je Verbindung:**
      1. `set_connected(True)`.
      2. Ist `portal_id` unbekannt: `N/+/system/0/Serial` abonnieren, der erste Wert ist die Portal-ID.
      3. `SUBSCRIPTIONS` abonnieren.
      4. Keepalive mit leerem Payload senden, danach alle `keepalive_s` mit `KEEPALIVE_SUPPRESS`.
      5. Nachrichten parsen und per `store.update` übernehmen.
    - **Bei einem Fehler:** `set_connected(False)` setzen, `health` aktualisieren, `sleep(backoff.next())` abwarten und neu verbinden. Nach einer Verbindung, über die mindestens eine Nachricht kam, wird der Backoff zurückgesetzt.

- [ ] **Step 1: Write the failing tests**

```python
# tests/unit/core/adapters/test_base.py
from bindaems.core.adapters.base import Backoff

def test_backoff_sequence_and_reset() -> None:
    b = Backoff()
    assert [b.next() for _ in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]
    b.reset()
    assert b.next() == 1
```

```python
# tests/unit/core/adapters/test_victron_mqtt.py  (Auszug – FakeTransport im Testmodul)
import asyncio, json
from bindaems.core.adapters.victron_mqtt import KEEPALIVE_SUPPRESS, MqttMessage, VictronMqttAdapter

class FakeTransport:
    """Zeichnet subscribe/publish auf; liefert vorgegebene Nachrichten; optional Fehler beim Betreten."""
    def __init__(self, messages: list[MqttMessage], fail_on_enter: bool = False, end_with_error: bool = False): ...

async def run_until(adapter, predicate, timeout=1.0) -> None: ...  # startet run() als Task, wartet auf predicate, bricht ab

async def test_first_keepalive_requests_full_republish(fake_env) -> None:
    adapter, transports, store = fake_env(keepalive_s=0.02)
    await run_until(adapter, lambda: len(transports[0].published) >= 3)
    t = transports[0]
    assert t.published[0] == (f"R/{P}/keepalive", b"")
    assert all(payload == KEEPALIVE_SUPPRESS for _, payload in t.published[1:])

async def test_publishes_only_keepalive_topic(fake_env) -> None:
    adapter, transports, _ = fake_env(keepalive_s=0.02)
    await run_until(adapter, lambda: len(transports[0].published) >= 2)
    assert {topic for topic, _ in transports[0].published} == {f"R/{P}/keepalive"}

async def test_messages_update_store(fake_env) -> None:
    adapter, _, store = fake_env(messages=[MqttMessage(f"N/{P}/grid/30/Ac/L1/Power", b'{"value": 500.0}')])
    await run_until(adapter, lambda: store.snapshot().ok("grid.l1.power_w") == 500.0)

async def test_subscribes_expected_filters(fake_env) -> None:
    adapter, transports, _ = fake_env()
    await run_until(adapter, lambda: len(transports[0].subscribed) >= 11)
    assert f"N/{P}/grid/+/#" in transports[0].subscribed
    assert f"N/{P}/settings/0/Settings/DynamicEss/#" in transports[0].subscribed

async def test_portal_discovery_when_not_configured(fake_env) -> None:
    adapter, transports, _ = fake_env(portal_id=None,
        messages=[MqttMessage("N/abc123/system/0/Serial", b'{"value": "abc123"}')])
    await run_until(adapter, lambda: "N/abc123/grid/+/#" in transports[0].subscribed)
    assert adapter.portal_id == "abc123"

async def test_reconnect_requests_full_republish_again(fake_env) -> None:
    adapter, transports, store = fake_env(first_fails=True, keepalive_s=0.02)
    await run_until(adapter, lambda: len(transports) == 2 and transports[1].published)
    assert adapter.sleeps[0] == 1.0
    assert transports[1].published[0] == (f"R/{P}/keepalive", b"")

async def test_disconnect_marks_state_signals_stale(fake_env) -> None:
    adapter, _, store = fake_env(end_with_error=True,
        messages=[MqttMessage(f"N/{P}/settings/0/Settings/CGwacs/Hub4Mode", b'{"value": 1}')])
    await run_until(adapter, lambda: adapter.health().error_count >= 1)
    assert store.snapshot().get("ess.hub4_mode").quality.value == "stale"
```

In `tests/helpers.py` kommt `async def run_until(adapter: Adapter, predicate: Callable[[], bool], timeout: float = 1.0) -> None` hinzu. Es startet `adapter.run()` als Task, prüft `predicate` alle 5 ms, bricht die Task danach ab und schlägt bei Timeout fehl. Die Konstante `P = "c0619ab1234"` liegt in der conftest.

Die Fixture `fake_env(keepalive_s=30.0, messages=(), portal_id=P, first_fails=False, end_with_error=False) -> (adapter, transports, store)` liegt in `tests/unit/core/adapters/conftest.py`:
- erzeugt `StateStore` mit `ManualClock` und `VictronConfig` mit Portal-ID `P`
- erzeugt einen `transport_factory`, der nacheinander `FakeTransport`-Instanzen erstellt und in der Liste `transports` ablegt
- verwendet ein `sleep`, das die Wartezeiten in `adapter.sleeps` aufzeichnet und sofort zurückkehrt

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/adapters/test_base.py tests/unit/core/adapters/test_victron_mqtt.py -v`
Expected: FAIL (`ImportError`)

- [ ] **Step 3: Implement `base.py` and `victron_mqtt.py` as specified.** Der Keepalive läuft als eigene Task im Kontext der Verbindung und wird beim Verbindungsende abgebrochen.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/adapters/test_base.py tests/unit/core/adapters/test_victron_mqtt.py -v`
Expected: `8 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/adapters/base.py src/bindaems/core/adapters/victron_mqtt.py tests/unit/core/adapters
git commit -m "feat(core): Victron-MQTT-Adapter mit Keepalive, Portal-Erkennung und Reconnect"
```

---

### Task 7: EVCS-NS-Adapter (Modbus, nur lesend)

**Files:**
- Create: `src/bindaems/core/adapters/evcs.py`
- Test: `tests/unit/core/adapters/test_evcs.py`

**Interfaces:**
- Consumes:
  - `StateStore` (Task 4)
  - `Backoff`, `AdapterHealth` (Task 6)
  - `EvcsConfig` (Task 3)
- Produces:
  - `class RegisterReader(Protocol)`: `async connect() -> None`, `async read_holding(address: int, count: int) -> list[int]`, `async close() -> None`. Absichtlich ohne Schreibmethode.
  - `class PymodbusReader(host: str, port: int, unit_id: int)`: kapselt `pymodbus.client.AsyncModbusTcpClient`. Ob die Unit-ID als `slave=` oder `device_id=` übergeben wird, hängt von der installierten pymodbus-Version ab und wird in einer privaten Hilfsfunktion `_read` gekapselt. Eine Fehlerantwort löst `ModbusReadError` aus.
  - Konstanten:
    - Leseblock: `BLOCK_START = 5000`, `BLOCK_COUNT = 20`
    - Register: `REG_PRODUCT_ID = 5000`, `REG_FW_HIGH = 5007`, `REG_FW_LOW = 5008`, `REG_MODE = 5009`, `REG_START_STOP = 5010`, `REG_SET_CURRENT = 5017`, `REG_ACTUAL_CURRENT_X10 = 5018`
    - `KNOWN_PRODUCT_IDS = frozenset({0xC024, 0xC025, 0xC026})`
  - `decode_block(start: int, regs: Sequence[int]) -> dict[str, Value]`. Schlüssel:
    - `product_id`
    - `firmware` als `f"{high:04x}.{low:04x}"`
    - `mode`, `start_stop`, `set_current_a`
    - `current_a` = Register 5018 ÷ 10
  - `class EvcsAdapter(name: str, cfg: EvcsConfig, store: StateStore, reader_factory: Callable[[], RegisterReader], *, poll_s: float = 1.0, sleep=asyncio.sleep)`
    - Quelle `name` mit Frische 5.0.
    - Signale `wallbox.<name>.<schlüssel>`: `current_a` ist M, alle anderen S.
    - Eine unbekannte Produkt-ID setzt `health().last_error = "Unbekannte Produkt-ID 0x…"`; die Werte werden trotzdem übernommen.

- [ ] **Step 1: Write the failing tests**

```python
from bindaems.core.adapters.evcs import EvcsAdapter, decode_block

REGS = [0xC025, 0, 0, 0, 0, 0, 0, 0x0102, 0x0003, 0, 1, 0, 0, 0, 0, 0, 0, 16, 125, 0]

def test_decode_block() -> None:
    d = decode_block(5000, REGS)
    assert d == {"product_id": 0xC025, "firmware": "0102.0003", "mode": 0, "start_stop": 1,
                 "set_current_a": 16, "current_a": 12.5}

async def test_poll_updates_store(evcs_env) -> None:
    adapter, reader, store = evcs_env(blocks=[REGS])
    await run_until(adapter, lambda: store.snapshot().ok("wallbox.evcs.current_a") == 12.5)
    assert store.snapshot().ok("wallbox.evcs.mode") == 0

async def test_read_errors_mark_disconnected_and_back_off(evcs_env) -> None:
    adapter, reader, store = evcs_env(blocks=[REGS, RuntimeError("timeout"), RuntimeError("timeout")])
    await run_until(adapter, lambda: adapter.health().error_count >= 2)
    assert adapter.sleeps[:2] == [1.0, 2.0]
    assert store.snapshot().get("wallbox.evcs.mode").quality.value == "stale"

async def test_only_read_calls(evcs_env) -> None:
    adapter, reader, _ = evcs_env(blocks=[REGS])
    await run_until(adapter, lambda: len(reader.calls) >= 3)
    assert {c[0] for c in reader.calls} <= {"connect", "read_holding", "close"}

async def test_unknown_product_id_reported(evcs_env) -> None:
    adapter, _, _ = evcs_env(blocks=[[0x1234] + REGS[1:]])
    await run_until(adapter, lambda: adapter.health().last_error is not None)
    assert "Unbekannte Produkt-ID 0x1234" in adapter.health().last_error
```

Die Fixture `evcs_env(blocks: list[list[int] | Exception]) -> (adapter, reader, store)` kommt in die Adapter-`conftest.py`; sie nutzt `cfg.wallboxes["evcs"]`. `run_until` stammt aus `tests/helpers.py`. `FakeReader` liefert die Einträge aus `blocks` der Reihe nach: eine Liste als Registerinhalt, eine Exception wird geworfen. Jeder Aufruf landet in `calls`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/adapters/test_evcs.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `evcs.py` as specified**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/adapters/test_evcs.py -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/adapters/evcs.py tests/unit/core/adapters/test_evcs.py tests/unit/core/adapters/conftest.py
git commit -m "feat(core): EVCS-NS-Adapter (Modbus, nur lesend)"
```

---

### Task 8: Wall-Connector-Adapter

**Files:**
- Create: `src/bindaems/core/adapters/twc.py`, `tests/fixtures/twc_vitals.json`, `tests/fixtures/twc_lifetime.json`
- Test: `tests/unit/core/adapters/test_twc.py`

**Interfaces:**
- Consumes: `StateStore`, `Backoff`, `TwcConfig`, `Phase`
- Produces:
  - `parse_vitals(data: Mapping[str, Any], phase_map: tuple[Phase, Phase, Phase]) -> dict[str, tuple[Value, SignalKind]]`. Schlüssel relativ zu `wallbox.<name>.`:
    - Zustände (S): `vehicle_connected`, `contactor_closed`, `evse_state`
    - Je Netzphase (M): `l{n}.current_a` und `l{n}.voltage_v`. Klemme A/B/C wird über `phase_map[0..2]` auf die Netzphase abgebildet.
    - `power_w` = Σ Strom × Spannung über A/B/C (M)
    - `session_kwh` = `session_energy_wh` ÷ 1000 (M)
    - Fehlt ein Feld, ist der betroffene Wert `None`. Auch `power_w` ist dann `None`.
  - `parse_lifetime(data) -> dict[str, tuple[Value, SignalKind]]` mit `{"total_kwh": (energy_wh / 1000, M)}`
  - `class TwcAdapter(name: str, cfg: TwcConfig, store: StateStore, client: httpx.AsyncClient, *, vitals_s: float = 2.0, lifetime_s: float = 60.0, sleep=asyncio.sleep)`
    - Quelle `name` mit Frische 10.0.
    - Abfragen: `GET http://<host>/api/1/vitals`, `/api/1/lifetime` und einmalig `/api/1/version`. Timeout 3 s.
- Fixture `twc_vitals.json`: `{"contactor_closed": true, "vehicle_connected": true, "session_s": 1234, "grid_v": 230.1, "grid_hz": 49.98, "vehicle_current_a": 16.1, "currentA_a": 16.0, "currentB_a": 16.1, "currentC_a": 15.9, "currentN_a": 0.2, "voltageA_v": 230.0, "voltageB_v": 231.0, "voltageC_v": 229.0, "session_energy_wh": 4321.5, "evse_state": 11}`
- Fixture `twc_lifetime.json`: `{"energy_wh": 1234567, "charge_starts": 321}`
- Test-Fixture `twc_env() -> (adapter, store)`, in der Adapter-`conftest.py`: nutzt `cfg.wallboxes["twc"]` (Host `twc.lan`), einen `httpx.AsyncClient` und eine aufzeichnende `sleep`-Funktion.

- [ ] **Step 1: Write the failing tests**

```python
import json
from pathlib import Path
import httpx, pytest
from bindaems.core.adapters.twc import TwcAdapter, parse_lifetime, parse_vitals

VITALS = json.loads(Path("tests/fixtures/twc_vitals.json").read_text())

def test_parse_vitals_power() -> None:
    assert parse_vitals(VITALS, ("L1", "L2", "L3"))["power_w"][0] == pytest.approx(11040.2)

def test_parse_vitals_maps_terminals_to_grid_phases() -> None:
    d = parse_vitals(VITALS, ("L2", "L3", "L1"))
    assert d["l2.current_a"][0] == 16.0 and d["l3.current_a"][0] == 16.1 and d["l1.current_a"][0] == 15.9

def test_parse_vitals_missing_field_invalidates_power() -> None:
    data = {k: v for k, v in VITALS.items() if k != "currentB_a"}
    d = parse_vitals(data, ("L1", "L2", "L3"))
    assert d["power_w"][0] is None and d["l2.current_a"][0] is None

def test_parse_lifetime() -> None:
    assert parse_lifetime({"energy_wh": 1234567})["total_kwh"][0] == pytest.approx(1234.567)

async def test_adapter_polls_and_updates_store(respx_mock, twc_env) -> None:
    respx_mock.get("http://twc.lan/api/1/vitals").respond(json=VITALS)
    respx_mock.get("http://twc.lan/api/1/lifetime").respond(json={"energy_wh": 1000})
    respx_mock.get("http://twc.lan/api/1/version").respond(json={"firmware_version": "25.42.1"})
    adapter, store = twc_env()
    await run_until(adapter, lambda: store.snapshot().ok("wallbox.twc.power_w") is not None)

async def test_http_error_marks_disconnected(respx_mock, twc_env) -> None:
    respx_mock.get("http://twc.lan/api/1/version").respond(json={})
    respx_mock.get("http://twc.lan/api/1/vitals").respond(500)
    adapter, _ = twc_env()
    await run_until(adapter, lambda: adapter.health().error_count >= 1)
    assert adapter.sleeps[0] == 1.0

async def test_only_get_requests(respx_mock, twc_env) -> None:
    respx_mock.get("http://twc.lan/api/1/vitals").respond(json=VITALS)
    respx_mock.get("http://twc.lan/api/1/lifetime").respond(json={"energy_wh": 1})
    respx_mock.get("http://twc.lan/api/1/version").respond(json={})
    adapter, _ = twc_env()
    await run_until(adapter, lambda: len(respx_mock.calls) >= 3)
    assert all(call.request.method == "GET" for call in respx_mock.calls)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/adapters/test_twc.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `twc.py` and create both fixtures as specified**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/adapters/test_twc.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/adapters/twc.py tests/fixtures/twc_*.json tests/unit/core/adapters/test_twc.py tests/unit/core/adapters/conftest.py
git commit -m "feat(core): Tesla-Wall-Connector-Adapter (lesend)"
```

---

### Task 9: Tessie-Adapter (nur lesend)

**Files:**
- Create: `src/bindaems/core/adapters/tessie.py`, `tests/fixtures/tessie_state.json`
- Test: `tests/unit/core/adapters/test_tessie.py`

**Interfaces:**
- Consumes: `StateStore`, `Backoff`, `VehicleConfig`, `LatLon`, `ManualClock`
- Produces:
  - `TESSIE_BASE_URL = "https://api.tessie.com"`
  - `haversine_m(a: LatLon, b: LatLon) -> float` (Erdradius 6 371 000 m)
  - `parse_state(data: Mapping[str, Any], home: LatLon, radius_m: float) -> dict[str, Value]`. Schlüssel relativ zu `vehicle.<name>.`:

    | Schlüssel | Quelle im Tessie-JSON |
    |---|---|
    | `soc_pct` | `charge_state.battery_level` |
    | `charge_limit_pct` | `charge_limit_soc` |
    | `charging_state` | `charging_state` |
    | `charge_amps` | `charge_amps` |
    | `current_request_a` | `charge_current_request` |
    | `actual_current_a` | `charger_actual_current` |
    | `phases` | `charger_phases` |
    | `charger_power_kw` | `charger_power` |
    | `plugged` | `charging_state` ist nicht „Disconnected“ |
    | `scheduled_charging` | `scheduled_charging_mode` ist nicht „Off“ |
    | `at_home` | Haversine-Abstand von `drive_state.latitude/longitude` ≤ Radius; ohne Standort `None` |
    | `online` | `state` = „online“ |

  - `poll_interval_s(charging_state: str | None, plugged: bool | None) -> float`: 30.0 bei „Charging“, sonst 60.0 wenn eingesteckt, sonst 600.0.
  - `class TessieAdapter(name: str, cfg: VehicleConfig, home: LatLon, token: SecretStr, store: StateStore, client: httpx.AsyncClient, *, sleep=asyncio.sleep)`
    - Quelle `f"tessie:{name}"` mit Frische 900.0; alle Werte sind M.
    - Anfrage ausschließlich `GET {TESSIE_BASE_URL}/{vin}/state?use_cache=true` mit `Authorization: Bearer <token>`, Timeout 20 s.
    - **Fehlerbehandlung:**
      - 429, 5xx oder Timeout: `Backoff(initial=60, maximum=900)`.
      - 401 oder 403: `last_error = "Tessie-Token ungültig"`, Wartezeit 900 s.
- Test-Fixture `tessie_env(with_clock: bool = False)`, in der Adapter-`conftest.py`:
  - Rückgabe `(adapter, store)` bzw. mit Uhr `(adapter, store, clock)`.
  - Fahrzeug `tesla` aus `cfg`, `VIN = "5YJ3E7EB0MF000000"`, Token `"tok"`, Heimatort `cfg.site.location`.
- Fixture `tessie_state.json`: `{"state": "online", "charge_state": {"battery_level": 63, "charge_limit_soc": 80, "charging_state": "Charging", "charge_amps": 16, "charge_current_request": 16, "charger_actual_current": 16, "charger_phases": 3, "charger_power": 11, "scheduled_charging_mode": "Off"}, "drive_state": {"latitude": 48.2, "longitude": 16.37}}`

- [ ] **Step 1: Write the failing tests**

```python
HOME = LatLon(lat=48.2, lon=16.37)
STATE = json.loads(Path("tests/fixtures/tessie_state.json").read_text())

def test_parse_state_values() -> None:
    d = parse_state(STATE, HOME, 150)
    assert d["soc_pct"] == 63 and d["charge_limit_pct"] == 80 and d["phases"] == 3
    assert d["plugged"] is True and d["scheduled_charging"] is False
    assert d["at_home"] is True and d["online"] is True

def test_parse_state_disconnected_and_away() -> None:
    data = json.loads(json.dumps(STATE))
    data["charge_state"]["charging_state"] = "Disconnected"
    data["drive_state"]["latitude"] = 48.3          # ≈ 11 119 m entfernt
    d = parse_state(data, HOME, 150)
    assert d["plugged"] is False and d["at_home"] is False

def test_parse_state_missing_location() -> None:
    data = {k: v for k, v in STATE.items() if k != "drive_state"}
    assert parse_state(data, HOME, 150)["at_home"] is None

def test_poll_interval() -> None:
    assert poll_interval_s("Charging", True) == 30.0
    assert poll_interval_s("Stopped", True) == 60.0
    assert poll_interval_s("Disconnected", False) == 600.0
    assert poll_interval_s(None, None) == 600.0

async def test_adapter_only_requests_state_endpoint(respx_mock, tessie_env) -> None:
    route = respx_mock.get(f"https://api.tessie.com/{VIN}/state").respond(json=STATE)
    adapter, store = tessie_env()
    await run_until(adapter, lambda: store.snapshot().ok("vehicle.tesla.soc_pct") == 63)
    req = route.calls[0].request
    assert req.url.params["use_cache"] == "true" and req.headers["Authorization"] == "Bearer tok"
    assert len(respx_mock.calls) == route.call_count            # keine anderen Endpunkte

async def test_server_error_backs_off_at_least_60s(respx_mock, tessie_env) -> None:
    respx_mock.get(f"https://api.tessie.com/{VIN}/state").respond(503)
    adapter, _ = tessie_env()
    await run_until(adapter, lambda: adapter.sleeps)
    assert adapter.sleeps[0] >= 60

async def test_values_go_stale_after_900s(respx_mock, tessie_env) -> None:
    respx_mock.get(f"https://api.tessie.com/{VIN}/state").mock(side_effect=[httpx.Response(200, json=STATE)] + [httpx.Response(503)] * 50)
    adapter, store, clock = tessie_env(with_clock=True)
    await run_until(adapter, lambda: store.snapshot().ok("vehicle.tesla.soc_pct") == 63)
    clock.advance(901)
    assert store.snapshot().get("vehicle.tesla.soc_pct").quality.value == "stale"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/adapters/test_tessie.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `tessie.py` and create the fixture as specified**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/adapters/test_tessie.py -v`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/adapters/tessie.py tests/fixtures/tessie_state.json tests/unit/core/adapters/test_tessie.py tests/unit/core/adapters/conftest.py
git commit -m "feat(core): Tessie-Adapter (nur lesend, ohne Wecken)"
```

---

### Task 10: Home-Assistant-Adapter (nur lesend)

**Files:**
- Create: `src/bindaems/core/adapters/homeassistant.py`
- Test: `tests/unit/core/adapters/test_homeassistant.py`

**Interfaces:**
- Consumes: `StateStore`, `Backoff`, `HomeAssistantConfig`
- Produces:
  - `class HaConnection(Protocol)`: `async send_json(msg: dict[str, Any]) -> None`, `async recv_json() -> dict[str, Any]`, `async close() -> None`
  - `async def connect_ws(url: str) -> HaConnection`: echte Implementierung mit `websockets`. Aus `http(s)://…` wird `ws(s)://…/api/websocket`.
  - `convert_state(state: str) -> Value`: `"unavailable"`, `"unknown"` und `""` ergeben `None`; `"on"`/`"off"` ergeben `True`/`False`; Zahlen ergeben `float`; alles andere bleibt `str`.
  - `class HaAdapter(cfg: HomeAssistantConfig, token: SecretStr, store: StateStore, connect: Callable[[str], Awaitable[HaConnection]] = connect_ws, *, ping_s: float = 30.0, sleep=asyncio.sleep)`
    - `name = "ha"`, Quelle `"ha"` mit Frische `None`; alle Werte sind S.
    - **Ablauf:**
      1. `auth_required` empfangen.
      2. `{"type": "auth", "access_token": token}` senden.
      3. `auth_ok` empfangen. Bei `auth_invalid`: `last_error = "HA-Token ungültig"`, Wartezeit 300 s.
      4. `{"id": 1, "type": "subscribe_entities", "entity_ids": [...]}` senden.
      5. Ereignisse verarbeiten:
         - `event.a`: neue Entitäten, Zustand `[eid]["s"]`
         - `event.c`: Änderungen, Zustand `[eid]["+"]["s"]`, falls vorhanden
         - `event.r`: entfernte Entitäten werden `INVALID`
    - Eine Entität kann auf mehrere Signale abgebildet sein (Umkehrung von `cfg.entities`).
    - Alle `ping_s` geht `{"id": n, "type": "ping"}` hinaus.

- [ ] **Step 1: Write the failing tests**

```python
async def test_auth_and_subscribe(ha_env) -> None:
    adapter, conn, store = ha_env(inbound=[{"type": "auth_required"}, {"type": "auth_ok"}])
    await run_until(adapter, lambda: len(conn.outbound) >= 2)
    assert conn.outbound[0] == {"type": "auth", "access_token": "tok"}
    assert conn.outbound[1]["type"] == "subscribe_entities"
    assert conn.outbound[1]["entity_ids"] == ["sensor.egolf_soc"]

async def test_initial_and_changed_states(ha_env) -> None:
    adapter, conn, store = ha_env(inbound=[
        {"type": "auth_required"}, {"type": "auth_ok"},
        {"id": 1, "type": "event", "event": {"a": {"sensor.egolf_soc": {"s": "55", "a": {}, "lu": 1791450000.0}}}},
        {"id": 1, "type": "event", "event": {"c": {"sensor.egolf_soc": {"+": {"s": "56"}}}}}])
    await run_until(adapter, lambda: store.snapshot().ok("vehicle.egolf.soc_pct") == 56.0)

async def test_unavailable_marks_invalid(ha_env) -> None:
    adapter, conn, store = ha_env(inbound=[{"type": "auth_required"}, {"type": "auth_ok"},
        {"id": 1, "type": "event", "event": {"a": {"sensor.egolf_soc": {"s": "unavailable", "a": {}}}}}])
    await run_until(adapter, lambda: store.snapshot().get("vehicle.egolf.soc_pct") is not None)
    assert store.snapshot().get("vehicle.egolf.soc_pct").quality.value == "invalid"

async def test_auth_invalid_backs_off(ha_env) -> None:
    adapter, conn, _ = ha_env(inbound=[{"type": "auth_required"}, {"type": "auth_invalid", "message": "x"}])
    await run_until(adapter, lambda: adapter.sleeps)
    assert adapter.sleeps[0] == 300 and adapter.health().last_error == "HA-Token ungültig"

async def test_never_sends_service_calls(ha_env) -> None:
    adapter, conn, _ = ha_env(inbound=[{"type": "auth_required"}, {"type": "auth_ok"}], ping_s=0.01)
    await run_until(adapter, lambda: len(conn.outbound) >= 4)
    assert {m["type"] for m in conn.outbound} <= {"auth", "subscribe_entities", "ping"}

async def test_disconnect_marks_values_stale(ha_env) -> None:
    adapter, conn, store = ha_env(inbound=[{"type": "auth_required"}, {"type": "auth_ok"},
        {"id": 1, "type": "event", "event": {"a": {"sensor.egolf_soc": {"s": "55", "a": {}}}}},
        ConnectionError("weg")])
    await run_until(adapter, lambda: adapter.health().error_count >= 1)
    assert store.snapshot().get("vehicle.egolf.soc_pct").quality.value == "stale"
```

`ha_env(inbound: list[dict | Exception], ping_s: float = 30.0) -> (adapter, conn, store)` erzeugt eine `FakeConnection` mit Eingangs-Skript; eine Exception im Skript wird bei `recv_json` geworfen. Konfiguriert ist `entities={"vehicle.egolf.soc_pct": "sensor.egolf_soc"}`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/adapters/test_homeassistant.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `homeassistant.py` as specified**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/adapters/test_homeassistant.py -v`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/adapters/homeassistant.py tests/unit/core/adapters/test_homeassistant.py tests/unit/core/adapters/conftest.py
git commit -m "feat(core): Home-Assistant-Adapter (WebSocket, nur lesend)"
```

---

### Task 11: Abgeleitete Größen und Plausibilität

**Files:**
- Create: `src/bindaems/core/state/derived.py`, `src/bindaems/core/state/plausibility.py`
- Test: `tests/unit/core/state/test_derived.py`, `tests/unit/core/state/test_plausibility.py`

**Interfaces:**
- Consumes: `Snapshot`, `Alarm`, `Severity` (Task 4); `Config` (Task 3)
- Produces:
  - `@dataclass(frozen=True) class Derived` mit den Feldern:
    - Leistungen: `grid_w`, `pv_total_w`, `consumption_w`, `battery_ac_w`, `battery_w`, `house_load_w` (jeweils `float | None`)
    - Verbrauch je Phase: `consumption_l: Mapping[Phase, float | None]`
    - Wallboxen: `wallbox_w: Mapping[str, float | None]`, `wallbox_phase_a: Mapping[str, Mapping[Phase, float]]`
    - Netzbezug je Phase: `phase_import_a: Mapping[Phase, float | None]`
    - `balance_residual_w: float | None`
  - `derive(snap: Snapshot, cfg: Config) -> Derived`. Es werden nur `OK`-Werte verwendet. Fehlt eine Eingangsgröße, ist das Ergebnis `None`.
    - `grid_w` = `grid.power_w`, ersatzweise Σ `grid.l{n}.power_w`
    - `phase_import_a[Lp]` = `grid.lp.power_w / grid.lp.voltage_v`, wenn die Spannung ≥ 100 V ist. Das Vorzeichen kommt aus der Leistung.
    - `pv_total_w` = Σ über Signale der Form `pv.<id>.power_w` (Gesamtwerte, **keine** Phasenwerte `pv.<id>.l{n}.power_w`) + `pv_dc.power_w` (fehlt DC, zählt 0)
    - `consumption_w` = (`grid_w` − Σ `vebus.l*.ac_in_power_w`) + (Σ `vebus.l*.ac_out_power_w` + Σ `pv.<id>.power_w`)
    - `consumption_l[Lp]` = (`grid.lp` − `ac_in_lp`) + (`ac_out_lp` + Σ `pv.*.lp.power_w`), nur wenn alle Phasenwerte da sind
    - `battery_ac_w` = Σ (`ac_in_lp` − `ac_out_lp`), positiv = Laden
    - `battery_w` = `battery_ac_w` + `pv_dc.power_w`
    - `wallbox_w[name]` = `wallbox.<name>.power_w`
    - `wallbox_phase_a`:
      - Wall Connector: `wallbox.<name>.l{n}.current_a`.
      - EVCS: Phasenzahl `k = clamp(round(power_w / (current_a · voltage_nominal_v)), 1, 3)`; Strom = `current_a` auf den ersten `k` Phasen aus `phase_map`, sonst 0. `current_a` stammt aus Modbus, ersatzweise `gx_current_a`.
    - `house_load_w` = `consumption_w` − Σ `wallbox_w`
    - `balance_residual_w` = `grid_w` + `pv_total_w` − `battery.power_w` − `consumption_w`
  - `class PlausibilityMonitor(window_s: float = 60.0)` mit `evaluate(snap: Snapshot, derived: Derived, now: datetime) -> list[Alarm]`. Regeln, Alarm-IDs und exakte Texte:

    | ID | Bedingung | Schwere | Meldung |
    |---|---|---|---|
    | `plaus.voltage.Lp` | Spannung außerhalb 180–270 V | WARNING | `f"Netzspannung {Lp} unplausibel: {v:.1f} V"` |
    | `plaus.grid_power.Lp` | \|P\| > 30 000 W | WARNING | `f"Netzleistung {Lp} unplausibel: {v:.0f} W"` |
    | `plaus.soc` | SOC außerhalb 0–100 | WARNING | `f"Akku-SOC unplausibel: {v:.1f} %"` |
    | `plaus.balance` | gleitender Mittelwert von \|Residuum\| über `window_s` größer als die Grenze | WARNING | `f"Energiebilanz unplausibel: Abweichung {r:.0f} W (Grenze {g:.0f} W)"` |

    Die Bilanzgrenze ist `max(500, 0,15 · max(consumption_w, |battery.power_w|))`. Die Bilanzregel greift erst, wenn das Zeitfenster vollständig mit Werten gefüllt ist.

- [ ] **Step 1: Write the failing tests**

```python
def snap(values: dict[str, float]) -> Snapshot: ...  # Helfer: alle Werte OK, Quelle "victron"

BASE = {f"grid.l{n}.power_w": 1000.0 for n in (1, 2, 3)} | {f"grid.l{n}.voltage_v": 230.0 for n in (1, 2, 3)} \
     | {f"vebus.l{n}.ac_in_power_w": 500.0 for n in (1, 2, 3)} | {f"vebus.l{n}.ac_out_power_w": 300.0 for n in (1, 2, 3)} \
     | {f"pv.huawei.l{n}.power_w": 1000.0 for n in (1, 2, 3)} | {"pv.huawei.power_w": 3000.0, "grid.power_w": 3000.0}

def test_phase_import_uses_power_sign(cfg) -> None:
    d = derive(snap(BASE | {"grid.l1.power_w": -2300.0, "grid.l1.current_a": 10.0}), cfg)
    assert d.phase_import_a["L1"] == pytest.approx(-10.0)

def test_consumption_and_battery_ac(cfg) -> None:
    d = derive(snap(BASE), cfg)
    assert d.consumption_l["L1"] == pytest.approx(1800.0)
    assert d.consumption_w == pytest.approx(5400.0) and d.battery_ac_w == pytest.approx(600.0)

def test_house_load_subtracts_wallboxes(cfg) -> None:
    d = derive(snap(BASE | {"wallbox.evcs.power_w": 2000.0, "wallbox.twc.power_w": 1000.0}), cfg)
    assert d.house_load_w == pytest.approx(2400.0)

def test_evcs_phase_inference_three_and_two_phase(cfg) -> None:
    d3 = derive(snap(BASE | {"wallbox.evcs.power_w": 11040.0, "wallbox.evcs.current_a": 16.0}), cfg)
    assert d3.wallbox_phase_a["evcs"] == {"L1": 16.0, "L2": 16.0, "L3": 16.0}
    d2 = derive(snap(BASE | {"wallbox.evcs.power_w": 7360.0, "wallbox.evcs.current_a": 16.0}), cfg)
    assert d2.wallbox_phase_a["evcs"] == {"L1": 16.0, "L2": 16.0, "L3": 0.0}

def test_missing_vebus_yields_none(cfg) -> None:
    d = derive(snap({k: v for k, v in BASE.items() if not k.startswith("vebus")}), cfg)
    assert d.consumption_w is None and d.house_load_w is None and d.battery_ac_w is None
```

```python
def test_voltage_out_of_range_alarm(cfg) -> None:
    s = snap(BASE | {"grid.l2.voltage_v": 150.0})
    alarms = PlausibilityMonitor().evaluate(s, derive(s, cfg), T0)
    assert any(a.id == "plaus.voltage.L2" and a.message == "Netzspannung L2 unplausibel: 150.0 V" for a in alarms)

def test_balance_alarm_after_full_window(cfg) -> None:
    mon = PlausibilityMonitor(window_s=60)
    bad = derive(snap(BASE | {"battery.power_w": -2000.0}), cfg)     # Residuum 3000+3000+2000−5400 = 2600 W > Grenze 810 W
    for i in range(61):
        alarms = mon.evaluate(snap(BASE | {"battery.power_w": -2000.0}), bad, T0 + timedelta(seconds=i))
    assert any(a.id == "plaus.balance" for a in alarms)

def test_no_balance_alarm_before_window_full(cfg) -> None:
    mon = PlausibilityMonitor(window_s=60)
    bad = derive(snap(BASE | {"battery.power_w": -2000.0}), cfg)
    assert not [a for a in mon.evaluate(snap(BASE), bad, T0) if a.id == "plaus.balance"]

def test_balance_within_tolerance(cfg) -> None:
    mon = PlausibilityMonitor(window_s=60)
    ok = derive(snap(BASE | {"battery.power_w": 400.0}), cfg)        # Residuum ≈ 200 W
    for i in range(61):
        alarms = mon.evaluate(snap(BASE | {"battery.power_w": 400.0}), ok, T0 + timedelta(seconds=i))
    assert not [a for a in alarms if a.id == "plaus.balance"]
```

Die Fixture `cfg` lädt `deploy/config.example.yaml`. Die Zeitkonstante `T0` ist wie in Task 4 definiert.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/state/test_derived.py tests/unit/core/state/test_plausibility.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `derived.py` and `plausibility.py` as specified**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/state/test_derived.py tests/unit/core/state/test_plausibility.py -v`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/state/derived.py src/bindaems/core/state/plausibility.py tests/unit/core/state
git commit -m "feat(core): abgeleitete Größen (Bilanz, Phasenströme) und Plausibilitätsprüfung"
```

---

### Task 12: Energiefluss-Zuordnung und Viertelstunden-Summen

**Files:**
- Modify: `src/bindaems/shared/domain.py` (`SlotFlows` ergänzen)
- Create: `src/bindaems/core/accounting/__init__.py`, `src/bindaems/core/accounting/flows.py`
- Test: `tests/unit/core/accounting/test_flows.py`, `tests/property/test_flow_conservation.py`

**Interfaces:**
- Consumes: `Snapshot` (Task 4), `slot_start` (Task 2)
- Produces:
  - In `shared/domain.py`: `@dataclass(frozen=True) class SlotFlows: slot_start: datetime; covered_s: float; flows_wh: Mapping[str, float]; counters: Mapping[str, tuple[float | None, float | None]]`
  - Flussschlüssel: `"pv>house"`, `"pv>battery"`, `"pv>grid"`, `"battery>house"`, `"battery>grid"`, `"grid>house"`, `"grid>battery"` sowie `wb_key(source: str, wallbox: str) -> str`, das `f"{source}>wb:{wallbox}"` liefert.
  - `allocate(pv_w: float, grid_w: float, battery_w: float, house_w: float, wallbox_w: Mapping[str, float]) -> dict[str, float]`. Es werden nur Einträge > 0 zurückgegeben. `pv_w`, `house_w` und die Wallbox-Leistungen werden auf ≥ 0 begrenzt; `grid_w` (+ = Bezug) und `battery_w` (+ = Laden) tragen ein Vorzeichen. `wallbox_w` ist in Prioritätsreihenfolge geordnet.

    **Reihenfolge der Zuordnung (Spec 11.4):**
    1. PV versorgt das Haus.
    2. PV versorgt die Wallboxen in Prioritätsreihenfolge.
    3. PV lädt den Akku (bis zur Ladeleistung).
    4. Restliche PV geht ins Netz.
    5. Die Akku-Entladung versorgt das restliche Haus.
    6. Die Akku-Entladung versorgt die restlichen Wallboxen.
    7. Restliche Akku-Entladung zählt als `battery>grid`.
    8. Das Netz deckt den Rest von Haus und Wallboxen sowie die restliche Akkuladung.
  - `class SlotAccumulator(counter_signals: Sequence[str], max_gap_s: float = 5.0)`
    - `add(ts: datetime, flows_w: Mapping[str, float], snap: Snapshot) -> SlotFlows | None`
      - **Halteprinzip:** Die Leistung des vorigen Abtastpunkts gilt bis zum aktuellen. Das Intervall `[t_prev, ts)` wird mit den Flüssen von `t_prev` integriert und dem Slot von `t_prev` zugerechnet, aber nur wenn `0 < ts − t_prev ≤ max_gap_s`.
      - Wechselt danach `slot_start(ts)`, wird der alte Slot abgeschlossen und zurückgegeben.
    - **Zählerstände je Slot:**
      - Start: der erste `OK`-Wert im Slot.
      - Ende: der Wert des ersten Abtastpunkts im Folgeslot (Grenzwert); dieser ist zugleich der Start des Folgeslots.
      - Bei `flush()`: der letzte gesehene Wert.
    - `flush() -> SlotFlows | None`: Liefert den laufenden Slot, sobald darin mindestens ein Abtastpunkt lag, auch ohne integrierte Zeit.

- [ ] **Step 1: Write the failing tests**

```python
def test_allocate_sunny_day() -> None:
    assert allocate(5000, -500, 1500, 1000, {"evcs": 2000, "twc": 0}) == \
        {"pv>house": 1000, "pv>wb:evcs": 2000, "pv>battery": 1500, "pv>grid": 500}

def test_allocate_night() -> None:
    assert allocate(0, 0, -800, 800, {}) == {"battery>house": 800}

def test_allocate_battery_assist_ev() -> None:
    assert allocate(2000, 1000, -1500, 500, {"evcs": 4000}) == \
        {"pv>house": 500, "pv>wb:evcs": 1500, "battery>wb:evcs": 1500, "grid>wb:evcs": 1000}

def test_allocate_grid_charging() -> None:
    assert allocate(0, 3500, 3000, 500, {}) == {"grid>house": 500, "grid>battery": 3000}

def test_allocate_priority_order() -> None:
    assert allocate(4000, 2000, 0, 0, {"twc": 3000, "evcs": 3000}) == \
        {"pv>wb:twc": 3000, "pv>wb:evcs": 1000, "grid>wb:evcs": 2000}

def test_allocate_reports_battery_to_grid() -> None:
    assert allocate(0, -1000, -1500, 500, {}) == {"battery>house": 500, "battery>grid": 1000}

def test_accumulator_integrates_and_closes_slot() -> None:
    acc = SlotAccumulator(counter_signals=["grid.energy_import_kwh"])
    t = datetime(2026, 10, 8, 9, 14, 58, tzinfo=UTC)
    assert acc.add(t, {"pv>house": 3600.0}, snap({"grid.energy_import_kwh": 100.0})) is None
    assert acc.add(t + timedelta(seconds=1), {"pv>house": 3600.0}, snap({"grid.energy_import_kwh": 100.5})) is None
    closed = acc.add(t + timedelta(seconds=2), {"pv>house": 3600.0}, snap({"grid.energy_import_kwh": 100.6}))
    assert closed.slot_start == datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
    assert closed.flows_wh["pv>house"] == pytest.approx(2.0) and closed.covered_s == pytest.approx(2.0)
    assert closed.counters["grid.energy_import_kwh"] == (100.0, 100.6)

def test_accumulator_skips_gaps() -> None:
    acc = SlotAccumulator(counter_signals=[])
    t = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
    acc.add(t, {"pv>house": 1000.0}, snap({}))
    acc.add(t + timedelta(seconds=10), {"pv>house": 1000.0}, snap({}))
    assert acc.flush().covered_s == 0.0

def test_accumulator_slots_across_dst_change() -> None:
    acc = SlotAccumulator(counter_signals=[])
    t = datetime(2026, 10, 25, 0, 59, 59, tzinfo=UTC)          # 02:59:59 MESZ, Umstellung um 01:00 UTC
    acc.add(t, {"grid>house": 3600.0}, snap({}))
    closed = acc.add(t + timedelta(seconds=1), {"grid>house": 3600.0}, snap({}))
    assert closed.slot_start == datetime(2026, 10, 25, 0, 45, tzinfo=UTC)
    assert acc.flush().slot_start == datetime(2026, 10, 25, 1, 0, tzinfo=UTC)
```

```python
# tests/property/test_flow_conservation.py
from hypothesis import given, strategies as st

@given(pv=st.floats(0, 20000), house=st.floats(0, 15000),
       wbs=st.lists(st.floats(0, 11000), max_size=2), battery=st.floats(-12000, 12000))
def test_flows_conserve_energy(pv: float, house: float, wbs: list[float], battery: float) -> None:
    wallbox_w = {f"wb{i}": w for i, w in enumerate(wbs)}
    grid = house + sum(wbs) + battery - pv
    flows = allocate(pv, grid, battery, house, wallbox_w)
    assert all(v >= 0 for v in flows.values())
    out = lambda src: sum(v for k, v in flows.items() if k.startswith(src + ">"))
    into = lambda dst: sum(v for k, v in flows.items() if k.endswith(">" + dst))
    assert out("pv") == pytest.approx(pv, abs=1e-6)
    assert out("grid") == pytest.approx(max(grid, 0), abs=1e-6)
    assert out("battery") == pytest.approx(max(-battery, 0), abs=1e-6)
    assert into("house") == pytest.approx(house, abs=1e-6)
    assert into("battery") == pytest.approx(max(battery, 0), abs=1e-6)
    assert into("grid") == pytest.approx(max(-grid, 0), abs=1e-6)
    for name, w in wallbox_w.items():
        assert into(f"wb:{name}") == pytest.approx(w, abs=1e-6)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/accounting tests/property/test_flow_conservation.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `SlotFlows` and `flows.py` as specified**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/accounting tests/property/test_flow_conservation.py -v`
Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared/domain.py src/bindaems/core/accounting tests/unit/core/accounting tests/property
git commit -m "feat(core): Zuordnung der Energieflüsse und Viertelstunden-Summen"
```

---

### Task 13: InfluxDB-Schreiber mit Spool

**Files:**
- Create: `src/bindaems/core/telemetry/__init__.py`, `src/bindaems/core/telemetry/lineprotocol.py`, `src/bindaems/core/telemetry/spool.py`, `src/bindaems/core/telemetry/influx.py`
- Test: `tests/unit/core/telemetry/test_lineprotocol.py`, `tests/unit/core/telemetry/test_spool.py`, `tests/unit/core/telemetry/test_influx_writer.py`

**Interfaces:**
- Consumes: `InfluxConfig`, `TelemetryConfig` (Task 3); `Clock` (Task 2)
- Produces:
  - `@dataclass(frozen=True) class Point: measurement: str; tags: Mapping[str, str]; fields: Mapping[str, float | int | bool | str]; ts: datetime; rp: str = "raw"`
  - `to_line(p: Point) -> str`
    - Tags und Felder werden alphabetisch sortiert.
    - Escaping:
      - Messungsname: Komma und Leerzeichen.
      - Tag-Schlüssel und -Werte sowie Feldschlüssel: Komma, `=` und Leerzeichen.
      - String-Felder in `"…"`, mit `\"` und `\\` escaped.
    - Formate: `int` mit Suffix `i`, `bool` als `true`/`false`, Zeitstempel in ms.
    - Ein Point ohne Felder löst `ValueError` aus.
  - `@dataclass(frozen=True) class SpoolFile: path: Path; rp: str; lines: list[str]`
  - `class DiskSpool(directory: Path, max_bytes: int, max_age: timedelta, clock: Clock)` mit
    - `append(rp: str, lines: Sequence[str]) -> None`: eine Datei pro Aufruf, Name `f"{epoch_ms:013d}-{seq:06d}-{rp}.lp"`
    - `oldest(n: int) -> list[SpoolFile]`, `remove(path: Path) -> None`
    - `enforce_limits() -> int`: löscht die ältesten Dateien, bis die Grenzen eingehalten sind; Rückgabe = Zahl gelöschter Dateien; Log-Warnung „Spool-Grenze erreicht, älteste Daten verworfen“
    - `size_bytes() -> int`
  - `class InfluxWriter(cfg: InfluxConfig, password: SecretStr | None, client: httpx.AsyncClient, spool: DiskSpool, *, flush_s: float = 1.0, batch_lines: int = 5000, queue_max: int = 100_000)`
    - `write(point: Point) -> None`: synchron, blockiert nie. Ist die Warteschlange voll, wird sie komplett in den Spool geschrieben.
    - `async run() -> None` und `async flush_now() -> None`.
    - Senden: `POST {url}/write?db=<database>&rp=<rp>&precision=ms` mit gzip-Body und Basic Auth (`username`, `password`).
    - **Je nach Antwort:**

      | Antwort | Reaktion |
      |---|---|
      | 204 | Danach bis zu 10 Spool-Dateien nachsenden, älteste zuerst. |
      | Netzwerkfehler, 5xx, 429 | Zeilen in den Spool. |
      | 400 | Zeilen verwerfen, `dropped_lines` erhöhen, Fehler loggen. |

    - `stats() -> WriterStats(sent_lines: int, spooled_lines: int, dropped_lines: int, spool_bytes: int)`.

Die Fixture `writer_env() -> (writer, spool)` in `tests/unit/core/telemetry/conftest.py` nutzt:
- `cfg.influxdb` mit `http://influx.lan:8086` und Datenbank `bindaems`
- einen `DiskSpool` unter `tmp_path` mit Grenzen von 10 MB und 7 Tagen
- einen `httpx.AsyncClient`

- [ ] **Step 1: Write the failing tests**

```python
TS = datetime(2026, 10, 8, 10, 0, 0, 123000, tzinfo=UTC)          # = 1791453600123 ms

def test_to_line_types_and_order() -> None:
    p = Point("power", {"source": "grid", "phase": "L1"}, {"p_w": 812.5, "n": 3, "ok": True, "txt": 'a "b"'}, TS)
    assert to_line(p) == 'power,phase=L1,source=grid n=3i,ok=true,p_w=812.5,txt="a \\"b\\"" 1791453600123'

def test_to_line_escapes_tags() -> None:
    p = Point("power", {"id": "Ober geschoss,1=2"}, {"p_w": 1.0}, TS)
    assert to_line(p).startswith("power,id=Ober\\ geschoss\\,1\\=2 ")

def test_to_line_rejects_empty_fields() -> None:
    with pytest.raises(ValueError):
        to_line(Point("power", {}, {}, TS))
```

```python
def test_spool_oldest_first(tmp_path) -> None:
    clock = ManualClock(TS); sp = DiskSpool(tmp_path, 10_000_000, timedelta(days=7), clock)
    sp.append("raw", ["a 1"]); clock.advance(1); sp.append("raw", ["b 2"])
    assert [f.lines for f in sp.oldest(2)] == [["a 1"], ["b 2"]]

def test_spool_enforces_max_bytes_drops_oldest(tmp_path) -> None:
    clock = ManualClock(TS); sp = DiskSpool(tmp_path, 120, timedelta(days=7), clock)
    for i in range(5):
        sp.append("raw", [f"m{i} f=1 {i}" * 4]); clock.advance(1)
    assert sp.enforce_limits() >= 1
    assert sp.size_bytes() <= 120
    remaining = [f.lines[0][:2] for f in sp.oldest(10)]
    assert "m0" not in remaining and remaining[-1] == "m4"

def test_spool_enforces_max_age(tmp_path) -> None:
    clock = ManualClock(TS); sp = DiskSpool(tmp_path, 10_000_000, timedelta(days=7), clock)
    sp.append("raw", ["alt 1"]); clock.advance(timedelta(days=8)); sp.append("raw", ["neu 1"])
    sp.enforce_limits()
    assert [f.lines for f in sp.oldest(5)] == [["neu 1"]]
```

```python
async def test_writer_posts_gzip_line_protocol(respx_mock, writer_env) -> None:
    route = respx_mock.post("http://influx.lan:8086/write").respond(204)
    writer, _ = writer_env()
    p = Point("power", {"source": "grid"}, {"p_w": 1.0}, TS)
    writer.write(p); await writer.flush_now()
    req = route.calls[0].request
    assert req.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(req.content).decode() == to_line(p)
    assert dict(req.url.params) == {"db": "bindaems", "rp": "raw", "precision": "ms"}

async def test_writer_spools_on_server_error(respx_mock, writer_env) -> None:
    respx_mock.post("http://influx.lan:8086/write").respond(503)
    writer, spool = writer_env()
    writer.write(Point("power", {}, {"p_w": 1.0}, TS)); await writer.flush_now()
    assert len(spool.oldest(10)) == 1 and writer.stats().spooled_lines == 1

async def test_writer_drains_spool_after_recovery(respx_mock, writer_env) -> None:
    route = respx_mock.post("http://influx.lan:8086/write").mock(side_effect=[httpx.Response(503), httpx.Response(204), httpx.Response(204)])
    writer, spool = writer_env()
    writer.write(Point("a", {}, {"v": 1.0}, TS)); await writer.flush_now()
    writer.write(Point("b", {}, {"v": 2.0}, TS)); await writer.flush_now()
    assert spool.oldest(10) == [] and route.call_count == 3

async def test_writer_drops_on_400(respx_mock, writer_env) -> None:
    respx_mock.post("http://influx.lan:8086/write").respond(400, json={"error": "partial write"})
    writer, spool = writer_env()
    writer.write(Point("a", {}, {"v": 1.0}, TS)); await writer.flush_now()
    assert writer.stats().dropped_lines == 1 and spool.oldest(10) == []

def test_write_is_non_blocking(writer_env) -> None:
    writer, _ = writer_env()
    writer.write(Point("a", {}, {"v": 1.0}, TS))          # ohne laufende Event-Loop-Aufgabe
    assert writer.stats().sent_lines == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/telemetry -v`
Expected: FAIL

- [ ] **Step 3: Implement `lineprotocol.py`, `spool.py`, `influx.py` as specified**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/telemetry -v`
Expected: `11 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/telemetry tests/unit/core/telemetry
git commit -m "feat(core): InfluxDB-1.x-Schreiber mit gzip, Spool und Wiederanlauf"
```

---

### Task 14: Telemetrie-Abtastung

**Files:**
- Create: `src/bindaems/core/telemetry/sampler.py`
- Test: `tests/unit/core/telemetry/test_sampler.py`

**Interfaces:**
- Consumes:
  - `Point` (Task 13)
  - `Snapshot`, `Reading`, `SignalKind` (Task 4)
  - `Derived` (Task 11)
  - `SlotFlows` (Task 12)
  - `Clock` (Task 2)
- Produces:
  - `class PointSink(Protocol): def write(self, point: Point) -> None`. `InfluxWriter` erfüllt das.
  - `signal_to_point(signal: str, reading: Reading) -> Point | None` gemäß Tabelle. `None` für Messwerte ohne Regel.
  - `cadence_for(signal: str, kind: SignalKind) -> float | None`. `None` heißt: Zustandswert, nur bei Änderung.
  - `class TelemetrySampler(sink: PointSink, clock: Clock)` mit
    - `on_cycle(snap: Snapshot, derived: Derived) -> None`: schreibt nur `OK`-Werte, die gemäß Takt fällig sind. Abgeleitete Werte (`house_load_w`, `battery_ac_w`, `pv_total_w`, `consumption_w`) gehen alle 2 s als `power{source=derived,id=<name ohne _w>,phase=total}` mit Feld `p_w` hinaus.
    - `write_slot_flows(flows: SlotFlows) -> None`: je Fluss `Point("flows", {"flow": key}, {"wh": wert}, ts=slot_start)`, dazu `Point("flows", {"flow": "_coverage"}, {"covered_s": …}, slot_start)`.

**Abbildung und Takt:**

| Signal | Messung, Tags | Feld | Takt |
|---|---|---|---|
| `grid.l{n}.power_w`, `.current_a`, `.voltage_v` | `power{source=grid,id=grid,phase=Ln}` | `p_w` / `i_a` / `u_v` | 2 s (P, I), 10 s (U) |
| `grid.power_w` | `power{source=grid,id=grid,phase=total}` | `p_w` | 2 s |
| `pv.<id>.power_w`, `pv.<id>.l{n}.power_w`, `pv_dc.power_w` | `power{source=pv,id=<id>/dc,phase=total/Ln}` | `p_w` | 2 s |
| `battery.power_w`, `.current_a`, `.voltage_v` | `power{source=battery,id=battery,phase=total}` | `p_w` / `i_a` / `u_v` | 2 s, 2 s, 10 s |
| `wallbox.<id>.power_w`, `.l{n}.current_a`, `.l{n}.voltage_v`, `.current_a` | `power{source=wallbox,id=<id>,phase=total/Ln}` | `p_w` / `i_a` / `u_v` | 2 s (P, I), 10 s (U) |
| `consumption*.l{n}.power_w`, `load.<id>[.l{n}].power_w` | `power{source=load,id=consumption/consumption_input/consumption_output/<id>,phase=…}` | `p_w` | 10 s |
| `vebus.l{n}.ac_in_power_w` / `ac_out_power_w` | `power{source=vebus,id=ac_in/ac_out,phase=Ln}` | `p_w` | 10 s |
| `vehicle.<id>.charger_power_kw` | `power{source=vehicle,id=<id>,phase=total}` | `p_w` (× 1000) | 10 s |
| `*.energy_import_kwh` / `*.energy_export_kwh` | `energy{source=<1. Segment>,id=<id>,direction=import/export}` | `kwh` | 60 s |
| `*.energy_kwh` / `*.total_kwh` / `*.session_kwh` | `energy{…,direction=forward/total/session}` | `kwh` | 60 s |
| `*.soc_pct` | `soc{id=battery bzw. <vehicle id>}` | `pct` | 30 s |
| alle `STATE`-Signale | `status{id=<signal>}` | `value_num` / `value_str` / `value_bool` (je nach Typ) | bei Änderung |

Die nach Typ getrennten Feldnamen in `status` vermeiden Feldtyp-Konflikte in InfluxDB 1.x.

In `tests/helpers.py` kommen hinzu:
- `class FakeSink`: sammelt geschriebene Punkte in `points: list[Point]`.
- `EMPTY_DERIVED`: ein `Derived`, in dem alle Felder `None` bzw. leere Abbildungen sind.

- [ ] **Step 1: Write the failing tests**

```python
def r(v, kind=SignalKind.MEASUREMENT) -> Reading:
    return Reading(v, T0, Quality.OK, "victron", kind)

def test_grid_phase_power_point() -> None:
    assert signal_to_point("grid.l2.power_w", r(812.5)) == Point("power", {"source": "grid", "id": "grid", "phase": "L2"}, {"p_w": 812.5}, T0)

def test_wallbox_current_point() -> None:
    assert signal_to_point("wallbox.twc.l3.current_a", r(16.0)) == Point("power", {"source": "wallbox", "id": "twc", "phase": "L3"}, {"i_a": 16.0}, T0)

def test_energy_counter_point() -> None:
    assert signal_to_point("grid.energy_import_kwh", r(1234.5)) == Point("energy", {"source": "grid", "id": "grid", "direction": "import"}, {"kwh": 1234.5}, T0)

def test_soc_points() -> None:
    assert signal_to_point("battery.soc_pct", r(63.0)) == Point("soc", {"id": "battery"}, {"pct": 63.0}, T0)
    assert signal_to_point("vehicle.tesla.soc_pct", r(55.0)) == Point("soc", {"id": "tesla"}, {"pct": 55.0}, T0)

def test_state_point_types() -> None:
    S = SignalKind.STATE
    assert signal_to_point("ess.hub4_mode", r(1, S)).fields == {"value_num": 1}
    assert signal_to_point("vehicle.tesla.charging_state", r("Charging", S)).fields == {"value_str": "Charging"}
    assert signal_to_point("wallbox.twc.vehicle_connected", r(True, S)).fields == {"value_bool": True}

def test_cadence_power_2s_voltage_10s() -> None:
    clock = ManualClock(T0); sink = FakeSink(); smp = TelemetrySampler(sink, clock)
    for _ in range(11):
        smp.on_cycle(snap_at(clock.now(), {"grid.l1.power_w": 1.0, "grid.l1.voltage_v": 230.0}), EMPTY_DERIVED)
        clock.advance(1)
    assert sum(1 for p in sink.points if "p_w" in p.fields and p.tags.get("source") == "grid") == 6
    assert sum(1 for p in sink.points if "u_v" in p.fields) == 2

def test_state_written_only_on_change() -> None:
    clock = ManualClock(T0); sink = FakeSink(); smp = TelemetrySampler(sink, clock)
    for v in (1, 1, 2):
        smp.on_cycle(snap_at(clock.now(), {"ess.hub4_mode": v}, kind=SignalKind.STATE), EMPTY_DERIVED); clock.advance(1)
    assert [p.fields["value_num"] for p in sink.points if p.measurement == "status"] == [1, 2]

def test_stale_readings_not_written() -> None:
    clock = ManualClock(T0); sink = FakeSink(); smp = TelemetrySampler(sink, clock)
    smp.on_cycle(snap_at(T0, {"grid.l1.power_w": 1.0}, quality=Quality.STALE), EMPTY_DERIVED)
    assert sink.points == []

def test_slot_flows_points() -> None:
    sink = FakeSink(); smp = TelemetrySampler(sink, ManualClock(T0))
    smp.write_slot_flows(SlotFlows(T0, 900.0, {"pv>house": 250.0}, {}))
    assert Point("flows", {"flow": "pv>house"}, {"wh": 250.0}, T0) in sink.points
    assert Point("flows", {"flow": "_coverage"}, {"covered_s": 900.0}, T0) in sink.points
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/telemetry/test_sampler.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `sampler.py` as specified.** Die Fälligkeit wird je Signal über den Zeitpunkt der letzten Ausgabe bestimmt. Fällig ist ein Signal, wenn `now − last ≥ cadence − 0,05 s`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/telemetry/test_sampler.py -v`
Expected: `9 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/telemetry/sampler.py tests/unit/core/telemetry/test_sampler.py
git commit -m "feat(core): Telemetrie-Abtastung und Abbildung auf InfluxDB-Messungen"
```

---

### Task 15: Mitregelnde Systeme erkennen und Selbstprüfung

**Files:**
- Create: `src/bindaems/core/checks/__init__.py`, `src/bindaems/core/checks/competitors.py`, `src/bindaems/core/checks/selfcheck.py`
- Test: `tests/unit/core/checks/test_competitors.py`, `tests/unit/core/checks/test_selfcheck.py`

**Interfaces:**
- Consumes: `Snapshot`, `Alarm`, `Severity` (Task 4); `Config` (Task 3)
- Produces:
  - `detect_competitors(snap: Snapshot, cfg: Config, now: datetime) -> list[Alarm]`. Es werden nur `OK`-Werte geprüft. Exakte Texte:

    | ID | Bedingung | Schwere | Meldung |
    |---|---|---|---|
    | `competitor.dess` | `dess.mode` ≠ 0 | ERROR | `f"Dynamic ESS ist aktiv (Modus {m}). Das EMS darf Victron nicht steuern."` |
    | `competitor.schedule.<k>` | `ess.schedule.<k>.day` ≥ 0 | ERROR | `f"Victron-Ladefenster {k} ist aktiv."` |
    | `competitor.evcs_mode.<name>` | `wallbox.<name>.mode` ≠ 0 bei EVCS-Wallboxen | ERROR | `f"EVCS „{name}“ ist nicht im manuellen Modus (Modus {m})."` |
    | `competitor.vehicle_schedule.<v>` | `vehicle.<v>.scheduled_charging` = True | WARNING | `f"Ladeplan im Fahrzeug „{cfg.vehicles[v].name}“ ist aktiv."` |

  - `@dataclass(frozen=True) class CheckResult: id: str; status: Literal["ok", "warn", "fail", "unknown"]; message: str`
  - `CRITICAL_SIGNALS = ("grid.l1.power_w", "grid.l2.power_w", "grid.l3.power_w", "battery.soc_pct", "vebus.l1.ac_in_power_w", "vebus.l2.ac_in_power_w", "vebus.l3.ac_in_power_w")`
  - `run_selfcheck(snap: Snapshot, cfg: Config, ok_since: Callable[[str], datetime | None], now: datetime) -> list[CheckResult]`, in dieser Reihenfolge und mit diesen exakten Texten:

    | ID | ok | fail / warn | unknown |
    |---|---|---|---|
    | `phases` | `"3 Phasen erkannt."` | fail: `f"Erwartet 3 Phasen, gemeldet {v}."` | `"Phasenzahl nicht verfügbar."` |
    | `ess_mode` | `f"ESS-Modus {v} wie erwartet."` | fail: `f"ESS-Modus {v}, erwartet {exp}."` | `"ESS-Modus nicht verfügbar."` |
    | `batterylife` | `f"BatteryLife-Zustand {v} wie erwartet."` | fail: `f"BatteryLife-Zustand {v}, erwartet {sorted(exp)}."` | `"BatteryLife-Zustand nicht verfügbar."` |
    | `dess` | `"Dynamic ESS ist aus."` | fail: `f"Dynamic ESS ist aktiv (Modus {v})."` | `"DESS-Modus nicht verfügbar."` |
    | `schedules` | `"Keine Victron-Ladefenster aktiv."` | fail: `f"Aktive Victron-Ladefenster: {', '.join(ks)}."` | `"Ladefenster nicht verfügbar."` (keine `ess.schedule.*`-Signale) |
    | `min_soc` | `"Victron-Min-SOC entspricht der USV-Reserve."` | fail (<): `f"Victron-Min-SOC {v:g} % liegt unter der USV-Reserve {r:g} %."`; warn (>): `f"Victron-Min-SOC {v:g} % liegt über der USV-Reserve {r:g} %."` | `"Victron-Min-SOC nicht verfügbar."` |
    | `evcs_mode.<name>` | `"EVCS im manuellen Modus."` | fail: `f"EVCS im Modus {v} statt manuell."` | `"EVCS-Modus nicht verfügbar."` |
    | `fresh_data` | `"Kritische Messwerte seit mindestens 30 s aktuell."` | fail: `f"Nicht aktuell: {', '.join(names)}."` | – |

- [ ] **Step 1: Write the failing tests**

```python
def test_dess_active_raises_error(cfg) -> None:
    alarms = detect_competitors(snap({"dess.mode": 1}, kind=STATE), cfg, T0)
    assert alarms == [Alarm("competitor.dess", Severity.ERROR, "Dynamic ESS ist aktiv (Modus 1). Das EMS darf Victron nicht steuern.", T0)]

def test_active_schedule_detected(cfg) -> None:
    alarms = detect_competitors(snap({"ess.schedule.0.day": 7, "ess.schedule.1.day": -7}, kind=STATE), cfg, T0)
    assert [a.id for a in alarms] == ["competitor.schedule.0"]

def test_evcs_not_manual(cfg) -> None:
    alarms = detect_competitors(snap({"wallbox.evcs.mode": 1}, kind=STATE), cfg, T0)
    assert alarms[0].message == "EVCS „evcs“ ist nicht im manuellen Modus (Modus 1)."

def test_vehicle_schedule_is_warning(cfg) -> None:
    alarms = detect_competitors(snap({"vehicle.tesla.scheduled_charging": True}, kind=STATE), cfg, T0)
    assert alarms[0].severity is Severity.WARNING

def test_quiet_when_all_clear(cfg) -> None:
    assert detect_competitors(snap({"dess.mode": 0, "ess.schedule.0.day": -7, "wallbox.evcs.mode": 0}, kind=STATE), cfg, T0) == []
```

```python
GOOD = {"vebus.phases": 3, "ess.hub4_mode": 1, "ess.batterylife_state": 10, "dess.mode": 0,
        "ess.schedule.0.day": -7, "ess.min_soc_pct": 20, "wallbox.evcs.mode": 0}

def by_id(results): return {r.id: r for r in results}

def test_all_ok(cfg) -> None:
    res = by_id(run_selfcheck(snap(GOOD, kind=STATE), cfg, lambda s: T0 - timedelta(seconds=31), T0))
    assert {k: v.status for k, v in res.items()} == {"phases": "ok", "ess_mode": "ok", "batterylife": "ok", "dess": "ok",
            "schedules": "ok", "min_soc": "ok", "evcs_mode.evcs": "ok", "fresh_data": "ok"}

def test_min_soc_below_reserve_fails(cfg) -> None:
    res = by_id(run_selfcheck(snap(GOOD | {"ess.min_soc_pct": 10}, kind=STATE), cfg, lambda s: T0 - timedelta(seconds=31), T0))
    assert res["min_soc"].status == "fail"
    assert res["min_soc"].message == "Victron-Min-SOC 10 % liegt unter der USV-Reserve 20 %."

def test_min_soc_above_reserve_warns(cfg) -> None:
    res = by_id(run_selfcheck(snap(GOOD | {"ess.min_soc_pct": 30}, kind=STATE), cfg, lambda s: T0 - timedelta(seconds=31), T0))
    assert res["min_soc"].status == "warn"

def test_missing_signal_is_unknown(cfg) -> None:
    res = by_id(run_selfcheck(snap({k: v for k, v in GOOD.items() if k != "vebus.phases"}, kind=STATE), cfg, lambda s: T0, T0))
    assert res["phases"] == CheckResult("phases", "unknown", "Phasenzahl nicht verfügbar.")

def test_fresh_data_requires_30s(cfg) -> None:
    res = by_id(run_selfcheck(snap(GOOD, kind=STATE), cfg, lambda s: T0 - timedelta(seconds=10), T0))
    assert res["fresh_data"].status == "fail" and res["fresh_data"].message.startswith("Nicht aktuell: grid.l1.power_w")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/checks -v`
Expected: FAIL

- [ ] **Step 3: Implement `competitors.py` and `selfcheck.py` as specified**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/checks -v`
Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/checks tests/unit/core/checks
git commit -m "feat(core): Erkennung mitregelnder Systeme und Selbstprüfung"
```

---

### Task 16: Interne API

**Files:**
- Create: `src/bindaems/core/api/__init__.py`, `src/bindaems/core/api/app.py`
- Test: `tests/unit/core/api/test_api.py`

**Interfaces:**
- Consumes:
  - `AdapterHealth` (Task 6)
  - `CheckResult` (Task 15)
  - `Alarm`, `Snapshot` (Task 4)
  - `Derived` (Task 11)
- Produces:
  - `class HealthReport(BaseModel)`: `mode: Literal["OBSERVE"]`, `version: str`, `adapters: list[dict[str, Any]]`, `selfcheck: list[dict[str, str]]`, `alarms: list[dict[str, str]]`, `cycle_ms_p95: float | None`
  - `class CoreView(Protocol)` mit `health() -> HealthReport`, `state() -> dict[str, Any]` und `subscribe() -> AbstractAsyncContextManager[AsyncIterator[dict[str, Any]]]`
  - `snapshot_to_json(snap: Snapshot, derived: Derived) -> dict[str, Any]` im Format `{"ts": iso, "signals": {sig: {"v": wert, "ts": iso, "q": "ok"}}, "derived": {feld: wert}}`. Abbildungen werden zu dicts, `None` zu `null`.
  - `create_api(view: CoreView, token: SecretStr) -> FastAPI` mit drei Endpunkten:
    - `GET /v1/health` → 200 mit `HealthReport`
    - `GET /v1/state` → 200 mit `view.state()`
    - `WS /v1/stream` → leitet die Nachrichten aus `view.subscribe()` weiter
  - **Authentifizierung:**
    - HTTP: Header `Authorization: Bearer <token>`, verglichen mit `hmac.compare_digest`; sonst 401.
    - WebSocket: Header oder Query-Parameter `token`; sonst Abbruch mit Code 4401.

- [ ] **Step 1: Write the failing tests**

```python
H = {"Authorization": "Bearer " + "x" * 32}

def test_health_requires_token(client) -> None:
    assert client.get("/v1/health").status_code == 401

def test_health_ok(client) -> None:
    body = client.get("/v1/health", headers=H).json()
    assert body["mode"] == "OBSERVE" and body["adapters"][0]["name"] == "victron"

def test_state_shape(client) -> None:
    body = client.get("/v1/state", headers=H).json()
    assert body["signals"]["grid.l1.power_w"] == {"v": 1000.0, "ts": "2026-10-08T10:00:00+00:00", "q": "ok"}

def test_stream_sends_messages(client) -> None:
    with client.websocket_connect("/v1/stream", headers=H) as ws:
        assert ws.receive_json()["type"] == "state"

def test_stream_rejects_bad_token(client) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/v1/stream?token=falsch") as ws:
            ws.receive_json()
    assert exc.value.code == 4401
```

Die Fixture `client` ist ein `TestClient(create_api(FakeView(), SecretStr("x" * 32)))`. `FakeView` liefert einen festen Health-Report, einen festen Zustand und genau eine Stream-Nachricht `{"type": "state", "data": {}}`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/api -v`
Expected: FAIL

- [ ] **Step 3: Implement `app.py` as specified**

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/api -v`
Expected: `5 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/api tests/unit/core/api
git commit -m "feat(core): interne API (Health, State, Stream) mit Token-Schutz"
```

---

### Task 17: Core-Laufzeit und Einstiegspunkt

**Files:**
- Create: `src/bindaems/core/runtime.py`, `src/bindaems/core/broadcast.py`, `src/bindaems/core/logging.py`, `src/bindaems/core/__main__.py`
- Test: `tests/unit/core/test_broadcast.py`, `tests/integration/test_runtime.py`

**Interfaces:**
- Consumes: alle vorherigen Tasks
- Produces:
  - `class Broadcast(maxsize: int = 100)` mit
    - `publish(msg: dict[str, Any]) -> None`: blockiert nie; bei einem langsamen Abonnenten wird die älteste Nachricht verworfen.
    - `subscribe() -> AbstractAsyncContextManager[AsyncIterator[dict[str, Any]]]`
  - `class CoreRuntime(cfg: Config, secrets: Secrets, *, clock: Clock = SystemClock(), adapters: Sequence[Adapter] | None = None, sink: PointSink | None = None, cycle_s: float = 1.0, serve_api: bool = True)` erfüllt `CoreView` (Task 16).
    - Öffentliche Attribute: `store: StateStore` und `selfcheck_runs: int`.
    - Ist `sink` gleich `None`, erzeugt die Laufzeit einen `InfluxWriter` mit `DiskSpool`; er dient als Sink, und `run()` startet seine Schleife. Wird ein `sink` übergeben (Tests), gibt es keine Writer-Schleife.
    - **`build_adapters()`**, falls keine Adapter übergeben wurden:
      - `VictronMqttAdapter`
      - je Wallbox ein `EvcsAdapter` oder `TwcAdapter`
      - `TessieAdapter` je Fahrzeug mit `tessie` und vorhandenem Token; fehlt das Token, Warnung „Tessie-Token fehlt – Fahrzeug {name} wird nicht gelesen“
      - `HaAdapter`, wenn `homeassistant` und ein Token gesetzt sind
    - **`cycle_once() -> None`** (synchron) in dieser Reihenfolge:
      1. `snapshot`
      2. `derive`
      3. Plausibilität prüfen
      4. Flüsse zuordnen (`allocate`), wenn `pv_total_w`, `grid_w`, `battery_w` und `house_load_w` vorliegen
      5. `accumulator.add`. Wird dabei ein Slot abgeschlossen: `sampler.write_slot_flows` und `broadcast {"type": "slot_flows", "data": …}`.
      6. `sampler.on_cycle`
      7. Alle 10 s: `detect_competitors` und `run_selfcheck`; geänderte Alarme mit `broadcast {"type": "alarm", "data": …}`.
      8. `broadcast {"type": "state", "data": snapshot_to_json(...)}`
      9. Zyklusdauer messen. Drei aufeinanderfolgende Zyklen über 200 ms erzeugen den Alarm `core.cycle_overrun` (WARNING, `"Regelzyklus überschreitet 200 ms."`).
    - **`async run() -> None`:** `asyncio.TaskGroup` mit den Adaptern, dem Writer, der Zyklusschleife und, wenn `serve_api` gesetzt ist, `uvicorn.Server` auf `core_api.host:port`.
    - **`async shutdown() -> None`:** `accumulator.flush()` wird geschrieben, dann `flush_now()` des Writers, dann werden die Tasks abgebrochen.
  - `configure_logging(level: str = "INFO") -> None`: structlog mit JSON-Renderer und UTC-ISO-Zeitstempeln.
  - `main(argv: list[str] | None = None) -> int` in `__main__.py`
    - Optionen: `--config` (Default `/config/config.yaml`) und `--log-level`.
    - Bei `ConfigError` oder ungültigen Secrets: deutsche Meldung auf stderr, beginnend mit `"Konfiguration ungültig"` bzw. `"Secrets ungültig"`; Rückgabe 2.
    - SIGTERM und SIGINT führen zu einem geordneten `shutdown()`; Rückgabe 0.

- [ ] **Step 1: Write the failing tests**

```python
async def test_broadcast_drops_oldest_for_slow_subscriber() -> None:
    b = Broadcast(maxsize=2)
    async with b.subscribe() as it:
        for i in range(3):
            b.publish({"i": i})
        assert [(await anext(it))["i"], (await anext(it))["i"]] == [1, 2]
```

Die Fixture `runtime_env(start: str = "2026-10-08T10:00:00+00:00")` liefert `(CoreRuntime, FakeSink, ManualClock)`:
- Die Laufzeit läuft mit `adapters=[]`, `serve_api=False` und `cfg` aus der Beispielkonfiguration.
- Vor jedem `cycle_once()` schreibt die Fixture über einen Hook direkt in `rt.store` (Quelle `"victron"`, als verbunden registriert):
  - die Werte `BASE` aus Task 11
  - `wallbox.evcs.power_w = 0`, `wallbox.twc.power_w = 0`
  - `battery.power_w = 600`, `battery.soc_pct = 50`

```python
# tests/integration/test_runtime.py
async def test_cycle_produces_state_and_points(runtime_env) -> None:
    rt, sink, clock = runtime_env()          # FakeAdapter hat Netz/vebus/PV/Akku-Signale gesetzt
    for _ in range(3):
        rt.cycle_once(); clock.advance(1)
    assert rt.state()["signals"]["grid.l1.power_w"]["v"] == 1000.0
    assert any(p.measurement == "power" for p in sink.points)
    assert rt.health().mode == "OBSERVE"

async def test_slot_close_writes_flows(runtime_env) -> None:
    rt, sink, clock = runtime_env(start="2026-10-08T09:14:58+00:00")
    for _ in range(4):
        rt.cycle_once(); clock.advance(1)
    assert any(p.measurement == "flows" for p in sink.points)

async def test_selfcheck_runs_every_10s(runtime_env) -> None:
    rt, _, clock = runtime_env()
    rt.cycle_once()
    assert rt.health().selfcheck
    assert rt.selfcheck_runs == 1
    for _ in range(10):
        clock.advance(1); rt.cycle_once()
    assert rt.selfcheck_runs == 2

def test_main_returns_2_on_invalid_config(tmp_path, capsys) -> None:
    p = tmp_path / "config.yaml"; p.write_text("grid: {fuse_a: -1}\n")
    assert main(["--config", str(p)]) == 2
    assert "Konfiguration ungültig" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/test_broadcast.py tests/integration/test_runtime.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `broadcast.py`, `logging.py`, `runtime.py`, `__main__.py` as specified.** `selfcheck_runs` ist ein öffentlicher Zähler, nur für Tests und Diagnose.

- [ ] **Step 4: Run tests to verify they pass, then the full suite**

Run: `uv run pytest -q && uv run mypy src && uv run lint-imports`
Expected: alle Tests grün, mypy und import-linter ohne Befund

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core tests/unit/core/test_broadcast.py tests/integration
git commit -m "feat(core): Laufzeit mit 1-s-Zyklus, Broadcast und Einstiegspunkt"
```

---

### Task 18: Container, Compose, InfluxDB-Einrichtung, Betriebsdoku

**Files:**
- Create: `deploy/Dockerfile.core`, `deploy/docker-compose.yml`, `deploy/influxdb-setup.sh`, `.dockerignore`, `src/bindaems/core/healthcheck.py`, `docs/betrieb.md`
- Modify: `.github/workflows/ci.yml` (Job `docker`)
- Test: `tests/unit/test_deploy_files.py`

**Interfaces:**
- Produces: `bindaems.core.healthcheck.main() -> int`. Ruft `GET http://127.0.0.1:<port>/v1/health` mit dem Token aus `BINDAEMS_INTERNAL_TOKEN` auf, Timeout 3 s. Rückgabe 0 bei Status 200, sonst 1.

**Festlegungen:**
- **`Dockerfile.core`:**
  - Basis `python:3.12-slim`; uv wird aus `ghcr.io/astral-sh/uv` mit festgelegtem Tag kopiert.
  - Installation mit `uv sync --locked --no-dev --extra core`.
  - Benutzer `ems` (UID 10001), `WORKDIR /app`, `VOLUME /data`.
  - `ENTRYPOINT ["python", "-m", "bindaems.core"]`, `CMD ["--config", "/config/config.yaml"]`.
  - `HEALTHCHECK CMD python -m bindaems.core.healthcheck`, Intervall 30 s.
- **`docker-compose.yml`**, Dienst `ems-core`:
  - Image `ghcr.io/binda5000/bindaems-core:${BINDAEMS_VERSION:-latest}`; `build` mit Kontext `..` und `dockerfile: deploy/Dockerfile.core`.
  - `env_file: .env`.
  - Volumes: `./config.yaml:/config/config.yaml:ro` und `core-data:/data`.
  - `restart: unless-stopped`.
  - Logging `json-file` mit `max-size: 10m`, `max-file: 5`.
  - Keine veröffentlichten Ports. Eine auskommentierte Zeile `127.0.0.1:8081:8081` dient der Diagnose.
  - Netz `internal`.
- **`influxdb-setup.sh`:**
  - bash mit `set -euo pipefail`; Umgebungsvariablen `INFLUX_URL`, `INFLUX_ADMIN_USER`, `INFLUX_ADMIN_PASSWORD`.
  - Idempotent, jeweils über `curl POST /query`:
    - `CREATE DATABASE "bindaems"`.
    - Retention Policy `"raw"` mit 90d als Default und `"long"` mit INF anlegen; gibt es sie schon, ersatzweise `ALTER RETENTION POLICY`.
    - Continuous Queries (`DROP CONTINUOUS QUERY` mit ignoriertem Fehler, dann `CREATE`):
      - `cq_power_1m`: `mean(p_w)`, `mean(i_a)`, `mean(u_v)` aus `raw.power` nach `long.power`, `GROUP BY time(1m), *`
      - `cq_soc_1m`: `mean(pct)`
      - `cq_energy_1m`: `last(kwh)`
      - `cq_flows_15m`: `sum(wh)` aus `raw.flows` nach `long.flows`, `GROUP BY time(15m), *`
- **`docs/betrieb.md`** (deutsch):
  - VM-Voraussetzungen (Spec 15) und Docker-Installation unter Debian.
  - Verzeichnis `/opt/bindaems` mit `config.yaml` und `.env` (`chmod 600`).
  - InfluxDB-Einrichtung mit dem Skript.
  - Bedienung: `docker compose up -d`, Logs, Update per `pull` und `up -d`, Rollback über `BINDAEMS_VERSION`.
  - Hinweise zu Proxmox-HA mit Replikation.
  - Aufruf des Prüfprotokolls (Task 19).
- **CI-Job `docker`:** baut das Image mit `docker/build-push-action`. Gepusht wird nur auf `main` und bei Tags nach GHCR (`permissions: packages: write`). Zusätzlich ein Schritt `shellcheck deploy/influxdb-setup.sh`.

- [ ] **Step 1: Write the failing tests**

```python
import yaml
from pathlib import Path

def test_compose_references_existing_files() -> None:
    compose = yaml.safe_load(Path("deploy/docker-compose.yml").read_text())
    svc = compose["services"]["ems-core"]
    assert Path(svc["build"]["dockerfile"]).exists()
    assert svc["env_file"] == [".env"] or svc["env_file"] == ".env"
    assert "ports" not in svc

def test_influx_setup_statements() -> None:
    text = Path("deploy/influxdb-setup.sh").read_text()
    for needle in ("CREATE DATABASE", "bindaems", "DURATION 90d", "DURATION INF",
                   "cq_power_1m", "cq_soc_1m", "cq_energy_1m", "cq_flows_15m"):
        assert needle in text

def test_healthcheck_returns_1_when_unreachable(monkeypatch) -> None:
    monkeypatch.setenv("BINDAEMS_INTERNAL_TOKEN", "x" * 32)
    monkeypatch.setenv("BINDAEMS_CORE_PORT", "1")          # nichts lauscht auf Port 1
    from bindaems.core.healthcheck import main
    assert main() == 1
```

Der Port kommt aus `BINDAEMS_CORE_PORT`, Default 8081. Im Container setzt Compose die Variable nicht.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_deploy_files.py -v`
Expected: FAIL

- [ ] **Step 3: Create all files as specified**

- [ ] **Step 4: Verify**

Run: `uv run pytest tests/unit/test_deploy_files.py -v`
Expected: `3 passed`

Run (falls lokal verfügbar): `docker build -f deploy/Dockerfile.core -t bindaems-core:dev . && docker compose -f deploy/docker-compose.yml config -q && shellcheck deploy/influxdb-setup.sh`
Expected: Build erfolgreich, keine Ausgabe von `config -q`, shellcheck ohne Befund. Fehlt Docker oder shellcheck lokal, gilt der grüne CI-Job `docker` als Nachweis.

- [ ] **Step 5: Commit**

```bash
git add deploy .dockerignore src/bindaems/core/healthcheck.py docs/betrieb.md .github/workflows/ci.yml tests/unit/test_deploy_files.py
git commit -m "build: Container, Compose, InfluxDB-Einrichtung und Betriebsdoku"
```

---

### Task 19: Werkzeug für das Prüfprotokoll Teil 1

**Files:**
- Create: `src/bindaems/tools/verify_checks.py`, `src/bindaems/tools/verify_part1.py`, `docs/verification/README.md`
- Test: `tests/unit/tools/test_verify_checks.py`

**Interfaces:**
- Consumes:
  - `parse_message`, `InstanceResolver` (Task 5)
  - `AiomqttTransport`, `KEEPALIVE_SUPPRESS` (Task 6)
  - `PymodbusReader` (Task 7)
  - `run_selfcheck` (Task 15)
  - `StateStore` (Task 4)
  - `Config`, `Secrets` (Task 3)
- Produces, in `verify_checks.py` (reine Funktionen):
  - `@dataclass(frozen=True) class Finding: item: str; title: str; status: Literal["ok", "warn", "fail", "info"]; detail: str`. `item` verweist auf den Spec-Punkt, z. B. `"17.1-4"`.
  - **Prüffunktionen:**

    | Funktion | Spec-Punkt | Inhalt |
    |---|---|---|
    | `check_selfcheck(results: Sequence[CheckResult]) -> list[Finding]` | 17.1-1/-2 | übernimmt die Ergebnisse der Selbstprüfung |
    | `check_mqtt_inventory(services: Mapping[str, set[int]], cfg: Config) -> Finding` | 17.1-3 | gefundene Services und Instanzen; `warn`, wenn eine konfigurierte Instanz fehlt |
    | `check_grid_meter(samples: Sequence[Snapshot]) -> list[Finding]` | 17.1-4 | Strom, Spannung und Leistung je Phase vorhanden; Vorzeichen des Stroms (siehe unten); Median-Aktualisierungsintervall von `grid.l1.power_w` |
    | `check_pv_and_submeter(snap: Snapshot) -> list[Finding]` | 17.1-5 | `ok`, wenn `pv.<id>.position` = 1 (AC-Out), sonst `warn`; Zählerstände vorhanden |
    | `check_battery(snap) -> Finding` | 17.1-6 | |
    | `check_hub4_overrides(snap) -> Finding` | 17.1-7 | `info` mit der Liste der `ess.override.*` |
    | `check_peak_shaving(snap) -> Finding` | 17.1-2 | `info` mit allen `settings.cgwacs.*`-Schlüsseln, die `peak`, `limit` oder `import` enthalten |
    | `check_evcs_dump(regs: Mapping[int, int]) -> list[Finding]` | 17.1-8 | Produkt-ID bekannt?; Firmware; `info` mit allen Registern ≠ 0 als Tabelle |
    | `check_twc(vitals: Mapping[str, Any]) -> Finding` | 17.1-9 | Pflichtfelder aus Task 8 vorhanden |
    | `check_tessie(state: Mapping[str, Any]) -> Finding` | 17.1-10 | Felder aus Task 9 vorhanden; `warn`, wenn ein Ladeplan aktiv ist |
    | `check_ha(entities: Mapping[str, Mapping[str, Any] \| None]) -> list[Finding]` | 17.1-11 | je Entität: existiert und verfügbar |
    | `check_influx(show: Mapping[str, Any]) -> list[Finding]` | 17.1-11 und Spec 11.2 | `bindaems` mit `raw` und `long` vorhanden; HA-Messungen gelistet |
    | `check_clock_offsets(offsets_s: Mapping[str, float]) -> Finding` | 17.1-12 | Abweichung der Uhrzeit von HA und InfluxDB gegenüber der VM, aus deren HTTP-Header `Date`; `warn` bei \|Abweichung\| > 2 s, mit Name und Wert im Detail. NTP am Cerbo bleibt eine manuelle Prüfung laut `docs/verification/README.md`. |

    Vorzeichen des Netzstroms: Liegt in einer Probe mit Leistung < −100 W auch ein Strom < 0 vor, lautet das Ergebnis „vorzeichenbehaftet“, sonst „vorzeichenlos“.
  - `render_report(findings: Sequence[Finding], meta: Mapping[str, str]) -> str`:
    - Markdown mit dem Titel `# Prüfprotokoll Teil 1 – <Datum>`.
    - Darunter eine Meta-Tabelle mit Firmwareständen und Konfigurationspfad.
    - Dann die Tabelle `| Punkt | Prüfung | Ergebnis | Details |`. Ergebnis ist `OK`, `WARNUNG`, `FEHLER` oder `INFO`.
- Produces, in `verify_part1.py`:
  - Aufruf: `python -m bindaems.tools.verify_part1 --config PATH --duration 120 --out docs/verification/`.
  - **Sammelt nur lesend:**
    - MQTT `N/<portal>/#` für die angegebene Dauer, mit Keepalive. Daraus Inventar und `Snapshot` über `parse_message` und `StateStore`; zusätzlich alle 5 s eine Probe für `check_grid_meter`.
    - EVCS-Register 5000–5199 in vier Leseblöcken zu je 50.
    - Wall Connector: `vitals`, `lifetime`, `version`.
    - Tessie: `GET /{vin}/state?use_cache=true`.
    - HA: `GET /api/states/<entity_id>` für jede konfigurierte Entität.
    - InfluxDB: `SHOW DATABASES`, `SHOW RETENTION POLICIES ON "bindaems"`, `SHOW MEASUREMENTS ON "<ha_database>"`.
  - **Ausgabe:**
    - `YYYY-MM-DD-pruefprotokoll-teil1.md` mit `render_report`.
    - `YYYY-MM-DD-pruefprotokoll-teil1-rohdaten.json` mit allen Rohdaten. Token und Passwörter werden nicht ausgegeben.
  - Ist eine Quelle nicht erreichbar, entsteht ein Finding mit `fail` und der Fehlermeldung; das Werkzeug läuft weiter.

- [ ] **Step 1: Write the failing tests**

```python
def test_grid_current_sign_detection() -> None:
    signed = [snap({"grid.l1.power_w": -2300.0, "grid.l1.current_a": -10.0, "grid.l1.voltage_v": 230.0})]
    unsigned = [snap({"grid.l1.power_w": -2300.0, "grid.l1.current_a": 10.0, "grid.l1.voltage_v": 230.0})]
    assert any("vorzeichenbehaftet" in f.detail for f in check_grid_meter(signed))
    assert any("vorzeichenlos" in f.detail for f in check_grid_meter(unsigned))

def test_pv_position_ac_out_ok() -> None:
    f = check_pv_and_submeter(snap({"pv.huawei.position": 1, "pv.huawei.energy_kwh": 5.0}, kind=STATE))
    assert f[0].status == "ok"

def test_pv_position_ac_in_warns() -> None:
    assert check_pv_and_submeter(snap({"pv.huawei.position": 0}, kind=STATE))[0].status == "warn"

def test_evcs_dump_unknown_product_warns() -> None:
    assert check_evcs_dump({5000: 0x1234, 5007: 1, 5008: 2})[0].status == "warn"

def test_evcs_dump_lists_nonzero_registers() -> None:
    findings = check_evcs_dump({5000: 0xC025, 5009: 0, 5017: 16, 5018: 125})
    assert any("5017" in f.detail and "5018" in f.detail and "5009" not in f.detail for f in findings if f.status == "info")

def test_mqtt_inventory_missing_instance_warns(cfg) -> None:
    f = check_mqtt_inventory({"grid": {30}, "vebus": {276}, "battery": {512}, "pvinverter": set(), "acload": {32}, "evcharger": {40}}, cfg)
    assert f.status == "warn" and "pvinverter 31" in f.detail

def test_ha_missing_entity_fails() -> None:
    f = check_ha({"sensor.egolf_soc": None})
    assert f[0].status == "fail"

def test_clock_offset_warns() -> None:
    f = check_clock_offsets({"homeassistant": 0.4, "influxdb": -3.2})
    assert f.status == "warn" and "influxdb" in f.detail

def test_report_contains_table_and_labels() -> None:
    md = render_report([Finding("17.1-1", "Phasen", "ok", "3 Phasen erkannt."),
                        Finding("17.1-8", "EVCS", "warn", "Unbekannte Produkt-ID")], {"Datum": "2026-10-08"})
    assert md.startswith("# Prüfprotokoll Teil 1 – 2026-10-08")
    assert "| 17.1-1 | Phasen | OK | 3 Phasen erkannt. |" in md
    assert "| 17.1-8 | EVCS | WARNUNG | Unbekannte Produkt-ID |" in md
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/tools -v`
Expected: FAIL

- [ ] **Step 3: Implement `verify_checks.py`, `verify_part1.py`, `docs/verification/README.md` as specified.** Die README beschreibt Aufruf, Ablageort und Wiederholung nach Firmware-Updates (Spec 17).

- [ ] **Step 4: Run tests and the full suite**

Run: `uv run pytest -q && uv run ruff check . && uv run mypy src && uv run lint-imports`
Expected: alle Tests grün, keine Befunde

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools docs/verification tests/unit/tools
git commit -m "feat(tools): Prüfprotokoll Teil 1 (nur lesend) mit Markdown-Bericht"
```

---

## Abschluss von Phase 1a

- **Inbetriebnahme beim Betreiber** nach `docs/betrieb.md`:
  1. InfluxDB einrichten.
  2. `config.yaml` und `.env` anlegen.
  3. `docker compose up -d`.
  4. `python -m bindaems.tools.verify_part1` ausführen. Die Abweichungen gegenüber der Konfiguration (Instanzen, `expected`-Werte) werden anschließend korrigiert.
- **Nachweis:**
  - In Grafana sind die Messungen `power`, `energy`, `soc`, `status` und `flows` sichtbar.
  - `/v1/health` meldet `OBSERVE`, alle Adapter verbunden, die Selbstprüfung ohne `fail`.
  - Ab hier läuft die 7-tägige Aufzeichnung für die Phase-1-Abnahme.

## Ausblick: Pläne 1b und 1c

**1b – ems-app-Backend** (eigener Plan nach Abschluss von 1a):
- FastAPI-App mit Client für die core-API (Health, State, Stream).
- Login: Rollen, Argon2, Sitzungen, CSRF, Sperre, optional TOTP; Admin wird per CLI angelegt.
- Einstellungsdienst mit Änderungsprotokoll in SQLite (Alembic).
- Verbraucher mit Hierarchie und „Sonstiges“.
- Preis-Pipeline: smartENERGY plus EPEX-Referenz, Brutto/Netto-Erkennung, Plausibilität (Spec 6.7). Dazu Prüfpunkt 17.1-13 im Prüfprotokoll.
- Tarifmodell v1 mit Energiebestandteilen und SNAP.
- PV-Prognose mit Open-Meteo und PV-Modell (Spec 10.1, ohne Kalibrierung).
- Abrechnung v1 aus `slot_flows` und `flows`.
- HA-Discovery-Sensoren nur lesend.
- Container `ems-app` in Compose.

**1c – Web-UI:**
- SvelteKit mit TypeScript und ECharts.
- Login, Dashboard mit Energiefluss-SVG, System-Seite (Adapter, Frische, Selbstprüfung, Alarme), Verbraucherverwaltung, Verlaufsdiagramme aus InfluxDB, Einstellungen überwiegend lesend.
- Mobil zuerst, hell und dunkel.

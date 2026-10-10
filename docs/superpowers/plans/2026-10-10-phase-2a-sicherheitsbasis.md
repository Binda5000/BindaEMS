# Phase 2a – Sicherheitsbasis: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der core bekommt alles, was er braucht, bevor er auf Geräte schreiben darf:
- harte Grenzen und eine Safety-Schicht mit allen acht Regeln aus Spec 8.3, belegt durch Property-Tests
- Betriebszustände mit Freigabe-Gate
- eine Absicht mit Dry-Run oder Live je Aktor
- Sperren bei mitregelnden Systemen
- den Totmann-Watchdog auf dem Cerbo samt Installationsskript, Heartbeat und Status in UI und HA

Nach 2a schreibt BindaEMS noch auf kein Gerät. Neu ist nur der Heartbeat an den eigenen Watchdog.

**Architecture:**
- **Neue Pakete im core:**
  - `safety/`: Grenzen, Regeln, Rate-Limits, Schreibbudget, Eingangsprüfung, Sperren, Betriebsarten und die `SafetyLayer`
  - `control/`: Betriebszustände und Absicht
  - `persistence/`: atomare JSON-Dateien
  - `watchdog_client/`: Watchdog-Status und Heartbeat
- **Safety-Schicht:**
  - Sie ist rein: Sie bekommt einen `Command` und einen `SafetyContext` und liefert ein `Verdict`.
  - Zeit, Messwerte, Freigaben und Sperren kommen von außen.
  - Die Aktoren rufen sie ab 2b auf.
- **Laufzeit in 2a:** Sie nutzt schon Betriebszustände, Absicht, Sperren, Eingangsprüfung und Heartbeat.
- **Watchdog:**
  - Eigenständiger Python-Dienst für Venus OS unter `cerbo-watchdog/`.
  - Seine Logik ist rein und in der CI getestet; die D-Bus-Anbindung ist dünn.

**Tech Stack:**
- Wie Phase 1:
  - Python 3.12 mit uv, pydantic v2, FastAPI und aiomqtt
  - Tests mit pytest, hypothesis und respx; ruff, mypy, import-linter
  - SvelteKit 2 mit Svelte 5, valibot, Vitest und Playwright 1.56.1
- Neu: pytest-cov.
- Cerbo-Watchdog: Python ≥ 3.8, nur mit der Standardbibliothek und den auf Venus OS vorhandenen Modulen `dbus`, `gi.repository.GLib` und `vedbus` (velib_python).

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, vor allem 5.3, 7.1, 7.2, 7.9, 8 und 18.
- Ausführende lesen Spec und Plan zusammen; Verweise wie „Spec 8.3“ beziehen sich darauf.
- Vorgänger sind die Pläne 1a–1c, alle umgesetzt. Ihre Global Constraints gelten weiter.

## Einordnung: Zuschnitt der Phase 2

Phase 2 (Spec 18) wird in vier Pläne geteilt. Jeder liefert für sich getestete, lauffähige Software. Auf Geräte schreibt BindaEMS erst ab 2b.

| Plan | Inhalt | Ergebnis an der Anlage |
|---|---|---|
| **2a** (dieser Plan) | Sicherheitsbasis: Konfiguration, Safety-Schicht mit Property-Tests, Betriebszustände, Absicht mit Freigaben, Sperren, Watchdog-Client, Cerbo-Watchdog mit Installation und Probe, Status in UI und HA | Watchdog installiert; Probe bestanden (Prüfprotokoll Teil 2, Punkt 5, noch ohne vorher verstellte Werte); Betriebszustand und Watchdog in UI und HA sichtbar |
| **2b** | Aktoren: Gerüst mit `capabilities()` und `apply()`, Dry-Run und Rücklese-Prüfung für EVCS, Victron und Tesla; sicherer Zustand (8.5); Entscheidungslog im core (Spool, Stream `decision`, `GET /v1/decisions`); Erkennung fremder Änderungen (7.9); Werkzeug für Prüfprotokoll Teil 2; Watchdog-Aktionen für die hub4-Overrides | Prüfprotokoll Teil 2 gemeinsam durchgeführt, endgültige Schreibwege festgelegt, `live_allowed` für jeden bestandenen Aktor |
| **2c** | Regelung: Regelzyklus (7.1), Phasenwächter (7.3), 15-min-Wächter (7.4, Zielwert bis 2027 optional), Modi Sofort, PV, Min+PV, Aus und Boost (7.5), Akku-Unterstützung mit Setpoint-Kompensation (7.6, 7.7), Zuordnung Auto ↔ Wallbox mit Übersteuerung (7.8), Degradationsstufen (8.4); Anlagensimulator `tools/sim` mit Regelkreistests in der CI | Simulator-Szenarien bestanden: keine Grenzverletzung, bei Wolkenprofilen höchstens 4 Start/Stopp je Auto und Stunde |
| **2d** | Bedienung: Absicht aus der app (Regelbetrieb, Live-Schalter mit Bestätigung, Lademodi, Ziele, Boost, Zuordnung); Entscheidungslog in SQLite; InfluxDB-Markierungen für Grafana; UI-Seiten „Laden“ und „Entscheidungen“ und „Warum?“; HA-Selects, -Numbers, -Switches, -Buttons und Benachrichtigungen | Dry-Run beginnt |

Danach folgt die Abnahme der Phase 2 (Spec 18):
1. 2 Wochen Dry-Run; die Entscheidungen werden gemeinsam gesichtet.
2. Live-Schaltung je Aktor, in der Reihenfolge EVCS, Victron, Tesla.
3. Vor der Live-Schaltung von Victron schaltet der Betreiber Dynamic ESS ab (Entscheidung vom 10.10.2026).

Vorher an der Anlage zu erledigen (aus Prüfprotokoll Teil 1):
- **Vor 2b:** `wallboxes.evcs.host` auf 192.168.81.41 stellen und Teil 1 wiederholen.
- **Vor dem ersten Live-Aktor:** das Peak-Shaving-Importlimit knapp unter die Hausanschlusssicherung stellen, z. B. auf 33 A (Ebene 0).

## Präzisierungen gegenüber der Spec

1. **Die Selbstprüfung sperrt Teilsysteme statt den ganzen core (7.2, 7.9).**
   - Für OBSERVE → CONTROL gelten nur die Bedingungen, die den ganzen core betreffen:
     - kritische Daten seit 30 s frisch
     - 3 Phasen mit je einem Gerät
     - Watchdog scharf, sobald ein Victron- oder EVCS-Aktor live angefordert ist
   - Fehlgeschlagene oder nicht prüfbare Victron-Punkte sperren das Teilsystem Victron. Das sind ESS-Modus, BatteryLife, DESS, Ladefenster und Min-SOC unter der Reserve.
   - Ein EVCS-Modus ungleich „manuell“ sperrt diese EVCS.
   - Das wirkt wie die Sperre bei mitregelnden Systemen (7.9), samt Freigabe nach 60 s ohne Ursache. Der core steht dann in DEGRADED, die Sperre ist der Grund.
   - **Grund:** Der Betreiber lässt Dynamic ESS an, bis Victron live geht; der 2-wöchige Dry-Run muss trotzdem laufen.
   - **Abweichung:** Für Victron ist das so streng wie die Spec, denn ein gesperrtes Teilsystem schreibt nie. Die EVCS darf dagegen regeln, obwohl ein Victron-Punkt fehlschlägt. Das ist vertretbar: Die EVCS-Regelung braucht nur Netzmessung und Phasenwächter, nicht den ESS-Modus. Ein Netzladen durch DESS erhöht den gemessenen Bezug, und der Phasenwächter nimmt dann die EVCS zurück.
2. **Heartbeat nur bei Bedarf, Freigabe mit 0 (8.6).**
   - **Wann:** Der core sendet den Heartbeat nur, solange mindestens ein Victron- oder EVCS-Aktor live angefordert ist (Absicht und `live_allowed`). Er sendet nur aus einem fehlerfreien Zyklus (7.1, Schritt 7).
   - **Dry-Run:** Ohne Live-Aktor bleibt der Watchdog „wartend“ und greift nie ein. Sonst würde er bei jedem Neustart über 90 s die EVCS auf den sicheren Strom setzen, obwohl BindaEMS sie gar nicht steuert.
   - **Freigabe:** Fällt die letzte solche Live-Anforderung weg, sendet der core einmal den Wert 0. Der Watchdog geht dann ohne Auslösung zurück auf „wartet“.
   - **Herunterfahren:** Dabei sendet der core keine 0. Dauert ein Neustart zu lange, löst der Watchdog deshalb aus.
   - **Grenze:** Ein laufender core kann den Watchdog so abschalten. Er könnte aber ohnehin falsch schreiben; der Watchdog schützt nur vor einem ausgefallenen core.
3. **Watchdog-Details (8.6):**
   - **Neustart des Cerbo:** Der Watchdog merkt sich „scharf“ in `/data/bindaems-watchdog/state.json` und startet danach wieder scharf. Eine unlesbare Datei gilt als scharf.
   - **Aktionen:** Jede liest zuerst und schreibt nur, wenn der Wert vom Sicherwert abweicht; danach liest sie zurück.
   - **Neuer D-Bus-Pfad `/LastTripFailures`:** die Zahl der Aktionen, die bei der letzten Auslösung trotz Wiederholungen fehlschlugen.
   - **`config.ini`:** erzeugt ein Werkzeug aus `config.yaml`. So stehen USV-Reserve, sicherer EVCS-Strom und Zeiten nur an einer Stelle.
   - **Aktionen in 2a:**
     - EVCS auf den sicheren Strom über den GX-Dienst `evcharger` (`/SetCurrent`); laut Teil 1 liest der Cerbo die EVCS bereits per Modbus TCP
     - Min-SOC auf die USV-Reserve
     - Entladegrenze auf unbegrenzt (−1)
   - Das Löschen der hub4-Overrides kommt mit 2b, sobald Teil 2 die Pfade festgelegt hat.
4. **Dringende Befehle (8.3).**
   - Gemeint ist ein Befehl mit `urgent=True`, der nachweislich Richtung sicherer Zustand geht: weniger Strom, Stopp, ein niedrigerer Netz-Sollwert, ein aufgehobener Override oder ein Sicherwert.
   - Er umgeht Mindestabstände, Stundenlimits, Totbänder (außer bei gleichem Wert) und das Schreibbudget, wird aber mitgezählt.
   - Sperren (7.9) gelten auch für ihn.
   - Geht ein als dringend markierter Befehl nicht Richtung Sicherheit, wird er normal geprüft.
   - **Grund:** Spec 7.3 verlangt, dass die EVCS im nächsten Zyklus reduziert und der Tesla bei Überlast sofort auf Mindeststrom geht.
5. **Rate-Limits je Aktor und Befehlsart (8.3, Regel 5).**

   | Aktor | Grenze |
   |---|---|
   | EVCS | Strom und Start/Stopp je mindestens 5 s auseinander |
   | Victron | Netz-Sollwert mindestens 2 s auseinander |
   | Tesla | Strom mindestens 90 s auseinander; höchstens 20 Befehle je Stunde, davon höchstens 6 Start/Stopp |

   - Die Stundenfenster gleiten.
   - Dry-Run-Entscheidungen zählen wie echte, damit das Dry-Run-Protokoll zeigt, was live passieren würde. Auch das Schreibbudget verbraucht der Dry-Run deshalb.
6. **Phasenvorhersage (8.3, Regel 4), bewusst vorsichtig:**
   - Eine Wallbox, die gerade nicht lädt, kann auf allen drei Phasen ihres `phase_map` ziehen. Lädt sie, zählen die Phasen mit mindestens 0,5 A.
   - Ein Tesla an einer unbekannten Wallbox kann auf allen drei Phasen ziehen.
   - Eine Änderung des Netz-Sollwerts verteilt sich gleichmäßig auf die drei Phasen (3-Phasen-ESS mit einem Gerät je Phase).
   - Verringerungen werden nie abgelehnt. Reicht selbst der Mindeststrom nicht, wird auf ihn reduziert; über den Stopp entscheidet die Regelung (2c).
7. **Sprungerkennung (8.3, Regel 7), nur für den Akku-SOC:**
   - Ändert er sich zwischen zwei Messungen innerhalb von 60 s um mehr als 5 Prozentpunkte, gilt er 60 s lang als unplausibel.
   - Netzleistungen springen legitim, etwa beim Start einer Wallbox. Für sie bleiben die Wertebereiche und die Energiebilanz aus Phase 1.
8. **Absicht in 2a (5.3):**
   - **Inhalt:** nur Version, Regelbetrieb und Betriebsart je Aktor. Lademodi, Übersteuerungen und Plan kommen mit 2c, 2d und Phase 3.
   - **Version:** eine streng steigende Zahl. Die app nimmt dafür Millisekunden seit 1970 (2d), damit auch eine aus der Sicherung wiederhergestellte app neuere Versionen erzeugt.
   - **Gespeichert** wird die angenommene, also geklemmte Absicht: Live gilt nur für Aktoren, die `config.yaml` bei der Annahme freigibt.
   - **Nach einem Neustart** gilt die gespeicherte Absicht weiter. Der core durchläuft trotzdem immer OBSERVE und das Gate (8.1, Ebene 4).
9. **Aktoren und Teilsysteme:**
   - Die Aktoren heißen `victron`, `wallbox.<name>` für jede EVCS und `vehicle.<name>` für jedes Fahrzeug mit Tessie.
   - Der Wall Connector und der e-Golf sind keine Aktoren.
   - Victron bekommt `live_allowed`. Spec 8.2 nennt es nur für Wallboxen und Fahrzeuge, aber 8.7 verlangt die Freigabe für jeden Aktor.
   - `live_allowed` bei einem Fahrzeug verlangt `tessie`.
10. **Typen:**
    - In `shared/control.py` (Spec 11.1): `Command`, Befehlsarten, Betriebsarten, Betriebszustände und die Absicht.
    - In `core/safety/types.py`: `Verdict` und `SafetyContext`, denn nur der core braucht sie.
    - `Decision` folgt mit 2b.
11. **Befehlsarten in 2a:**
    - EVCS und Tesla: `current` und `charging`.
    - Victron: `grid_setpoint` (flüchtig) sowie `max_discharge` und `min_soc` (beide persistent).
    - DVCC-Ladestrom und einen flüchtigen Entlade-Override ergänzen 2b oder Phase 3, wenn Teil 2 die Wege bestätigt.

## Global Constraints

- **Gilt weiter:** alle Global Constraints aus 1a und 1b:
  - Python ≥ 3.12, uv, Importgrenzen, intern UTC
  - deutsche Nutzertexte, Secrets nur aus `BINDAEMS_*`
  - `config.yaml` wird beim Start validiert
- **Schreibwege in 2a:**
  - Neu ist nur MQTT `W/<portal>/bindaems/0/Heartbeat` (Heartbeat und Freigabe 0). Der core nutzt ihn nur bei einem live angeforderten Victron- oder EVCS-Aktor; dazu kommt das Probe-Werkzeug.
  - Sonst bleibt alles lesend. Die Tests aus 1a, die das belegen, bleiben und werden erweitert.
- **Reine Safety-Schicht:**
  - `bindaems.core.safety` und `bindaems.core.control` importieren nichts aus `core.adapters`, `core.api`, `core.runtime`, `core.telemetry`, `core.accounting` oder `core.checks` (import-linter-Vertrag, Task 18).
  - Zeit kommt immer als Argument; Dateizugriffe laufen nur über `core.persistence.files`.
- **Abdeckung (Spec 16):** Zeilenabdeckung ≥ 90 % für `core/safety` und `core/control`, ≥ 75 % für das ganze Paket. Die CI prüft beides (Task 18).
- **Ablage des core:**
  - Dateien `intent.json` und `write_budget.json` unter `core.data_dir` (Default `/data`, Volume `core-data`).
  - Geschrieben wird atomar: temporäre Datei im selben Verzeichnis, dann `os.replace`.
  - Für unlesbare Dateien ist festgelegt, wie der core sicher startet (Tasks 6 und 11).
- **Cerbo-Watchdog:**
  - Python ≥ 3.8: ruff mit `target-version = "py38"` und ein `ast`-Test mit `feature_version=(3, 8)`.
  - Nur Standardbibliothek plus `dbus`, `gi.repository.GLib` und `vedbus` vom Gerät; keine Importe aus `bindaems`.
  - Läuft unter daemontools.
  - Schreibt nur in `/data/bindaems-watchdog/`, den Link `/service/bindaems-watchdog` und seinen Block in `/data/rc.local`.
- **Einheiten:**
  - Strom in A, in Befehlen ganzzahlig; Leistung in W; SOC in %.
  - Zahlen in Begründungstexten über `de()`: Dezimalkomma und echtes Minus.
- **Texte:** Begründungen, Gründe und Alarme exakt wie in den Tabellen dieses Plans; Tests prüfen den Wortlaut.

## Review Focus

1. **Neustart oder Update des core, während ein Aktor live ist** (`docker compose up -d`, Failover der VM):
   - Ist der core binnen 90 s zurück, löst der Watchdog nicht aus.
   - Sonst löst er genau einmal aus und ist nach 30 s stabilem Heartbeat wieder scharf.
   - Beim Herunterfahren gibt der core ihn nicht frei.
   - Tests: Task 12 `test_release_once_when_no_longer_required`, Task 13 `test_shutdown_sends_no_release`, Task 14 `test_trips_once_after_timeout` und `test_rearms_after_trip_with_stable_heartbeats`.
2. **Der Cerbo startet neu, während der core ausgefallen ist:** Der Watchdog startet scharf und löst nach dem Timeout aus, statt ewig zu warten.
   - Tests: Task 14 `test_restart_while_armed_trips_after_timeout` und `test_corrupt_state_file_starts_armed`.
3. **Dynamic ESS bleibt im Dry-Run an, oder eine Ursache flackert:**
   - Victron bleibt gesperrt, die EVCS darf trotzdem.
   - Eine Sperre löst sich erst nach 60 s ohne Ursache; kehrt die Ursache zurück, beginnt die Frist neu.
   - Tests: Task 8 `test_cause_must_be_absent_60s_before_release` und `test_returning_cause_restarts_the_release_wait`, Task 13 `test_dess_locks_victron_but_control_runs`.
4. **Unlesbare oder fehlende Dateien** (`intent.json`, `write_budget.json`, `state.json`):
   - Der core startet ohne Absicht in OBSERVE.
   - Persistente Schreibzugriffe bleiben 1 h gesperrt.
   - Der Watchdog startet scharf.
   - Jeder Fall erzeugt einen Alarm oder einen Log-Eintrag.
   - Tests: Task 6 `test_unreadable_file_blocks_for_an_hour`, Task 11 `test_corrupt_file_starts_without_intent`, Task 14 `test_corrupt_state_file_starts_armed`.
5. **Veraltete Messwerte genau dann, wenn reduziert werden muss:**
   - Dringende Verringerungen gehen durch, Erhöhungen nicht.
   - Bei unbekannten Phasenströmen wird nur verringert.
   - Tests: Task 4 `test_unknown_phase_currents_allow_only_decreases`, Task 9 `test_inputs_not_ok_rejects_increase_allows_urgent_decrease` und die Property-Tests.

## Dateistruktur

```
src/bindaems/shared/control.py              Aktor-IDs, Befehlsarten, Command, Betriebsarten, Betriebszustände, Intent
src/bindaems/shared/config.py               (geändert) Freigaben, Befehlslimits, safety, core
src/bindaems/core/persistence/files.py      atomare JSON-Dateien
src/bindaems/core/safety/types.py           de(), Outcome, Finding, Step, InputStatus, SafetyContext, Verdict
src/bindaems/core/safety/limits.py          HardLimits, ActuatorSpec, Sicherwerte, toward_safe()
src/bindaems/core/safety/ranges.py          Regeln 1–3
src/bindaems/core/safety/phases.py          Regel 4
src/bindaems/core/safety/rate.py            Regel 5
src/bindaems/core/safety/budget.py          Regel 6
src/bindaems/core/safety/inputs.py          Regel 7
src/bindaems/core/safety/locks.py           Sperren (7.9)
src/bindaems/core/safety/modes.py           wirksame Betriebsarten (Regel 8, 8.7)
src/bindaems/core/safety/layer.py           SafetyLayer
src/bindaems/core/control/operating.py      Betriebszustände (7.2)
src/bindaems/core/control/intent.py         IntentStore
src/bindaems/core/watchdog_client/status.py   Watchdog-Status und -Alarme
src/bindaems/core/watchdog_client/heartbeat.py
src/bindaems/core/adapters/victron_topics.py, victron_mqtt.py    (geändert) bindaems-Topics, write()
src/bindaems/core/state/store.py            (geändert) connected()
src/bindaems/core/api/app.py, core/runtime.py                    (geändert)
src/bindaems/tools/watchdog_config.py, watchdog_probe.py
src/bindaems/app/ha/entities.py             (geändert) „Watchdog ausgelöst“
cerbo-watchdog/                             README.md, install.sh, config.ini.example, ruff.toml,
                                            bindaems-watchdog.py, service/run, service/log/run,
                                            bindaems_watchdog/{__init__,config,logic,state_file,actions,service}.py
ui/src/lib/api/schemas.ts, ui/src/lib/system/view.ts, ui/src/routes/system/+page.svelte   (geändert)
tests/unit/core/{safety,control,persistence,watchdog_client}/…, tests/property/test_safety_layer.py,
tests/unit/cerbo_watchdog/…, tests/unit/tools/…, tests/integration/test_runtime_control.py
```

Neue Pakete unter `src/bindaems` bekommen eine `__init__.py` (Layout-Test). Testverzeichnisse bekommen keine.

---

### Task 1: Konfiguration für Freigaben, Befehlslimits und Ablage

**Files:**
- Modify: `src/bindaems/shared/config.py`, `deploy/config.example.yaml`
- Test: `tests/unit/shared/test_config.py`

**Interfaces:**
- Produces:
  - `GridConfig.max_import_kw: Annotated[float, Field(gt=0)] | None = None`: optionale maximale Bezugsleistung (Spec 8.2)
  - `VictronConfig.live_allowed: bool = False`
  - `class VehicleCommandLimits(_Model)`:
    - Felder: `current_interval_s: float = 90.0` (≥ 0), `current_step_a: int = 2` (≥ 1), `per_hour: int = 20` (≥ 0), `start_stop_per_hour: int = 6` (≥ 0)
    - Validator: `start_stop_per_hour` ≤ `per_hour`, Text `"start_stop_per_hour darf per_hour nicht übersteigen"`
  - `VehicleConfig.commands: VehicleCommandLimits` (Default `VehicleCommandLimits()`)
    - Validator auf `VehicleConfig`: `live_allowed` nur mit `tessie`, Text `"live_allowed verlangt einen tessie-Abschnitt"`
  - `class SafetyConfig(_Model)`: `lock_release_s: Annotated[float, Field(gt=0, le=3600)] = 60.0`
  - `class CoreConfig(_Model)`: `data_dir: Path = Path("/data")`
  - `Config.safety: SafetyConfig` und `Config.core: CoreConfig`, beide mit `default_factory`

- [ ] **Step 1: Failing Tests schreiben** (in `tests/unit/shared/test_config.py`; den Helfer `write_cfg` gibt es schon)

```python
def test_safety_defaults() -> None:
    cfg = load_config(EXAMPLE)
    assert cfg.victron.live_allowed is False
    assert cfg.grid.max_import_kw is None
    assert cfg.safety.lock_release_s == 60.0
    assert cfg.core.data_dir == Path("/data")
    assert cfg.vehicles["tesla"].commands == VehicleCommandLimits(
        current_interval_s=90.0, current_step_a=2, per_hour=20, start_stop_per_hour=6
    )


def test_vehicle_live_needs_tessie(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["vehicles"]["egolf"].update(live_allowed=True))
    with pytest.raises(ConfigError, match="live_allowed verlangt einen tessie-Abschnitt"):
        load_config(p)


def test_start_stop_limit_within_hourly_limit(tmp_path: Path) -> None:
    limits = {"per_hour": 5, "start_stop_per_hour": 6}
    p = write_cfg(tmp_path, lambda d: d["vehicles"]["tesla"].update(commands=limits))
    with pytest.raises(ConfigError, match="start_stop_per_hour darf per_hour nicht übersteigen"):
        load_config(p)


@pytest.mark.parametrize("seconds", [0, 3601])
def test_lock_release_bounds(tmp_path: Path, seconds: int) -> None:
    p = write_cfg(tmp_path, lambda d: d.update(safety={"lock_release_s": seconds}))
    with pytest.raises(ConfigError, match=r"safety\.lock_release_s"):
        load_config(p)


def test_max_import_must_be_positive(tmp_path: Path) -> None:
    p = write_cfg(tmp_path, lambda d: d["grid"].update(max_import_kw=0))
    with pytest.raises(ConfigError, match=r"grid\.max_import_kw"):
        load_config(p)
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/shared/test_config.py -q`
Expected: FAIL (`ImportError: cannot import name 'VehicleCommandLimits'`)

- [ ] **Step 3: Modelle umsetzen und `deploy/config.example.yaml` ergänzen**

In `config.example.yaml`:
- `victron.live_allowed: false` mit dem Kommentar „erst nach bestandenem Prüfprotokoll Teil 2 (Spec 8.7)“
- unter `vehicles.tesla` den Block `commands: {current_interval_s: 90, current_step_a: 2, per_hour: 20, start_stop_per_hour: 6}` mit dem Kommentar „Befehlslimits für Tessie (Spec 6.5)“
- die Abschnitte `safety: {lock_release_s: 60}` („Freigabe einer Sperre nach so vielen Sekunden ohne Ursache, Spec 7.9“) und `core: {data_dir: /data}`

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/shared/test_config.py -q`
Expected: PASS (die 6 neuen und alle bisherigen)

- [ ] **Step 5: Vertragsdatei der UI nachziehen**

`GET /api/limits` gibt `grid` vollständig aus (`limits_json`) und enthält jetzt `max_import_kw: null`.

Run: `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py -q && uv run pytest -q && (cd ui && pnpm test)`
Expected: Nur `ui/src/lib/api/contract/limits.json` ändert sich, danach ist alles grün. `LimitsSchema` ignoriert das neue Feld.

- [ ] **Step 6: Commit**

```bash
git add src/bindaems/shared/config.py deploy/config.example.yaml tests/unit/shared/test_config.py ui/src/lib/api/contract/limits.json
git commit -m "feat(shared): Freigaben, Befehlslimits und Ablage in der Konfiguration"
```

---

### Task 2: Steuerungstypen, Absicht und Aktorkatalog

**Files:**
- Create: `src/bindaems/shared/control.py`, `src/bindaems/core/safety/__init__.py`, `src/bindaems/core/safety/types.py`, `src/bindaems/core/safety/limits.py`
- Test: `tests/unit/shared/test_control.py`, `tests/unit/core/safety/conftest.py`, `tests/unit/core/safety/test_limits.py`

**Interfaces:**
- Consumes: `Config`, `VehicleCommandLimits`, `PhaseMap`, `Phase` (Task 1, `shared/config.py`)
- Produces in `shared/control.py`:
  - `VICTRON: Final = "victron"`, `wallbox_actuator(name: str) -> str` (`"wallbox.<name>"`), `vehicle_actuator(name: str) -> str` (`"vehicle.<name>"`)
  - `class CommandKind(StrEnum)`:

    | Wert | Bedeutung |
    |---|---|
    | `CURRENT = "current"` | Ladestrom in A, ganzzahlig (EVCS, Tesla) |
    | `CHARGING = "charging"` | Laden an (`True`) oder aus (`False`) |
    | `GRID_SETPOINT = "grid_setpoint"` | Netz-Sollwert in W; `None` hebt den Override auf |
    | `MAX_DISCHARGE = "max_discharge"` | Entladegrenze in W; −1 = unbegrenzt |
    | `MIN_SOC = "min_soc"` | Victron-Min-SOC in % |

  - `PERSISTENT_KINDS: frozenset[CommandKind] = frozenset({CommandKind.MAX_DISCHARGE, CommandKind.MIN_SOC})`
  - `CommandValue = float | bool | None`
  - `@dataclass(frozen=True) class Command`: `actuator: str`, `kind: CommandKind`, `value: CommandValue`, `urgent: bool = False`, `reasons: tuple[str, ...] = ()`
  - `class ActuatorMode(StrEnum)`: `DRY_RUN = "dry_run"`, `LIVE = "live"`
  - `class OperatingState(StrEnum)`: `START`, `OBSERVE`, `CONTROL`, `DEGRADED`, `SAFE`; die Werte sind die Namen
  - `class Intent(_Model)`: `version: Annotated[int, Field(ge=1)]`, `control: bool = False`, `actuators: dict[str, ActuatorMode] = Field(default_factory=dict)`
- Produces in `core/safety/types.py`:
  - `de(value: float) -> str`: `f"{value:.10g}"`, Punkt durch Komma und `-` durch `−` (U+2212) ersetzt
  - `class Outcome(StrEnum)`: `ACCEPTED`, `CLAMPED`, `REDUCED`, `UNCHANGED`, `DEFERRED`, `REJECTED`; die Werte sind die Namen in Kleinbuchstaben. Dazu `SENDABLE = frozenset({Outcome.ACCEPTED, Outcome.CLAMPED, Outcome.REDUCED})`
  - `@dataclass(frozen=True) class Finding`: `code: str`, `text: str`
  - `@dataclass(frozen=True) class Step`: `value: CommandValue`, `findings: tuple[Finding, ...] = ()`, `reject: bool = False`. Das ist das Ergebnis einer einzelnen Regel; bei `reject` ist `value` gleich `None`.
  - `@dataclass(frozen=True) class InputStatus`: `ok: bool`, `problems: tuple[str, ...] = ()`
  - `@dataclass(frozen=True) class SafetyContext`:

    | Feld | Typ | Bedeutung |
    |---|---|---|
    | `now` | `datetime` | Zeitpunkt der Prüfung |
    | `operating` | `OperatingState` | Betriebszustand (Regel 8) |
    | `modes` | `Mapping[str, ActuatorMode]` | wirksame Betriebsart je Aktor; fehlt = Dry-Run |
    | `locks` | `Mapping[str, tuple[str, ...]]` | Sperrgründe je Aktor; fehlt = frei |
    | `inputs` | `InputStatus` | Ergebnis von Regel 7 |
    | `phase_import_a` | `Mapping[Phase, float \| None]` | Netzbezug je Phase in A, positiv bei Bezug |
    | `wallbox_phase_a` | `Mapping[str, Mapping[Phase, float]]` | gemessener Strom je Wallbox-Schlüssel und Netzphase |
    | `vehicle_wallbox` | `Mapping[str, str \| None]` | Fahrzeug-Aktor → Wallbox-Schlüssel; fehlt oder `None` = unbekannt |
    | `present` | `Mapping[tuple[str, CommandKind], CommandValue]` | bekannter Ist-Wert je Aktor und Art; fehlt = unbekannt; beim Netz-Sollwert heißt `None` „kein Override aktiv“ |
    | `battery_discharge_w` | `float \| None = None` | aktuelle Entladeleistung, ≥ 0 |
    | `soc_pct` | `float \| None = None` | Akku-SOC |
    | `base_grid_setpoint_w` | `float = 0.0` | persistenter Netz-Sollwert; er gilt ohne Override |
    | `grid_budget_w` | `float \| None = None` | 15-min-Budget (Regel 2); `None` = keins |

  - `@dataclass(frozen=True) class Verdict`:
    - Felder: `command: Command`, `outcome: Outcome`, `value: CommandValue`, `findings: tuple[Finding, ...]`, `dry_run: bool`
    - Properties: `sendable` (`outcome in SENDABLE`) und `send` (`sendable and not dry_run`)
    - `value` ist bei `REJECTED` gleich `None`, sonst der Wert nach allen Regeln.
- Produces in `core/safety/limits.py`:
  - `Family = Literal["evcs", "victron", "tesla"]`
  - `@dataclass(frozen=True) class ActuatorSpec`: `id: str`, `family: Family`, `label: str`, `kinds: frozenset[CommandKind]`, `live_allowed: bool`, `needs_watchdog: bool`. Labels: `"Victron (ESS)"`, `f"EVCS „{key}“"`, beim Fahrzeug `vehicle.name`.
  - `@dataclass(frozen=True) class CurrentRange`: `min_a: int`, `max_a: int`, `safe_a: int`; beim Tesla ist `safe_a` gleich `min_a`.
  - `@dataclass(frozen=True) class HardLimits`:

    | Feld | Typ | Inhalt |
    |---|---|---|
    | `phase_limit_a` | `float` | `fuse_a − fuse_margin_a` |
    | `voltage_v` | `float` | `voltage_nominal_v` |
    | `setpoint_limits` | `tuple[tuple[float, str], ...]` | statische Obergrenzen des Netz-Sollwerts mit Namen, in dieser Reihenfolge: `max_grid_charge_setpoint_w` als „maximaler Netzladesetpoint“, `3 × voltage_v × phase_limit_a` als „Absicherung“, `max_import_kw × 1000` als „maximale Bezugsleistung“ (nur falls gesetzt) |
    | `reserve_soc_pct` | `float` | USV-Reserve |
    | `max_discharge_w` | `float` | aus `battery` |
    | `currents` | `Mapping[str, CurrentRange]` | je Aktor-ID (EVCS und Tesla) |
    | `wallbox_phases` | `Mapping[str, PhaseMap]` | je Wallbox-Schlüssel, EVCS und Wall Connector |
    | `vehicle_commands` | `Mapping[str, VehicleCommandLimits]` | je Fahrzeug-Aktor |
    | `persistent_per_hour`, `persistent_per_day` | `int` | Schreibbudget |
    | `actuators` | `Mapping[str, ActuatorSpec]` | Reihenfolge: `victron`, EVCS und Fahrzeuge je in Konfigurationsreihenfolge |

    Methoden:
    - `@classmethod from_config(cls, cfg: Config) -> HardLimits`
    - `grid_setpoint_max(self, budget_w: float | None) -> tuple[float, str]`: Minimum aus `setpoint_limits` und, falls gesetzt, `(budget_w, "Leistungsbudget")`. Bei Gleichstand gewinnt die zuerst genannte Grenze.
    - `safe_commands(self, actuator: str) -> tuple[Command, ...]`: alle Befehle mit `urgent=True` und `reasons=("safe_state",)`:
      - `victron`: `GRID_SETPOINT None`, `MAX_DISCHARGE −1.0`, `MIN_SOC <Reserve>`
      - `wallbox.<n>`: `CURRENT <safe_a>`
      - `vehicle.<n>`: `CURRENT <min_a>`
  - `toward_safe(command: Command, ctx: SafetyContext, limits: HardLimits) -> bool`: „Ist-Wert bekannt“ heißt, `(actuator, kind)` steht in `ctx.present`. Werte vom falschen Typ sind nie „Richtung sicher“.

    | Art | Richtung sicherer Zustand, wenn … |
    |---|---|
    | `current` | Wert ≤ bekannter Ist-Wert, oder Ist-Wert unbekannt und Wert ≤ `safe_a` |
    | `charging` | Wert ist `False` |
    | `grid_setpoint` | Wert ist `None`, oder Ist-Wert bekannt und Wert ≤ wirksamer Ist-Sollwert (Override, sonst `base_grid_setpoint_w`) |
    | `max_discharge` | Wert ist −1, oder bekannter Ist-Wert ≥ 0 und Wert ≥ Ist-Wert |
    | `min_soc` | Wert = Reserve, oder Ist-Wert bekannt und Reserve ≤ Wert ≤ Ist-Wert |

- Produces für Tests in `tests/unit/core/safety/conftest.py`:
  - Fixture `limits`: `HardLimits.from_config(cfg)`
  - Fixture `limits_live`: wie `limits`, aber mit `live_allowed: true` für `victron`, `wallboxes.evcs` und `vehicles.tesla`
  - `PHASES = ("L1", "L2", "L3")`
  - Funktion `make_ctx(**changes: Any) -> SafetyContext`. Ihre Defaults:
    - `now=T0`, `operating=CONTROL`
    - alle drei Aktoren `LIVE`, `locks={}`, `inputs=InputStatus(True)`
    - `phase_import_a` je Phase 10.0, `wallbox_phase_a={}`, `vehicle_wallbox={"vehicle.tesla": "twc"}`, `present={}`
    - `battery_discharge_w=0.0`, `soc_pct=50.0`, `base_grid_setpoint_w=0.0`, `grid_budget_w=None`

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/shared/test_control.py
def test_intent_defaults_and_validation() -> None:
    assert Intent(version=1) == Intent(version=1, control=False, actuators={})
    for bad in ({"version": 0}, {"version": 1, "actuators": {"victron": "an"}}, {"version": 1, "x": 1}):
        with pytest.raises(ValidationError):
            Intent.model_validate(bad)


def test_actuator_ids() -> None:
    assert (VICTRON, wallbox_actuator("evcs"), vehicle_actuator("tesla")) == (
        "victron", "wallbox.evcs", "vehicle.tesla",
    )


def test_de_formats_german() -> None:
    assert (de(10.7), de(-300.0), de(2070.0), de(22770.0)) == ("10,7", "−300", "2070", "22770")
```

```python
# tests/unit/core/safety/test_limits.py
def test_catalog_from_example(limits) -> None:
    assert list(limits.actuators) == ["victron", "wallbox.evcs", "vehicle.tesla"]
    evcs = limits.actuators["wallbox.evcs"]
    assert (evcs.family, evcs.label, evcs.needs_watchdog, evcs.live_allowed) == ("evcs", "EVCS „evcs“", True, False)
    tesla = limits.actuators["vehicle.tesla"]
    assert (tesla.label, tesla.needs_watchdog) == ("Tesla Model 3", False)
    assert limits.actuators["victron"].kinds == {CommandKind.GRID_SETPOINT, CommandKind.MAX_DISCHARGE, CommandKind.MIN_SOC}
    assert limits.currents["wallbox.evcs"] == CurrentRange(6, 16, 6)
    assert limits.currents["vehicle.tesla"] == CurrentRange(5, 16, 5)
    assert limits.wallbox_phases["twc"] == ("L1", "L2", "L3")


def test_phase_limit_and_setpoint_ceiling(limits) -> None:
    assert limits.phase_limit_a == 33.0
    assert limits.grid_setpoint_max(None) == (8000.0, "maximaler Netzladesetpoint")
    assert limits.grid_setpoint_max(5000.0) == (5000.0, "Leistungsbudget")


def test_fuse_and_max_import_bind(cfg) -> None:
    victron = cfg.victron.model_copy(update={"max_grid_charge_setpoint_w": 30000.0})
    high = cfg.model_copy(update={"victron": victron})
    assert HardLimits.from_config(high).grid_setpoint_max(None) == (22770.0, "Absicherung")
    capped = high.model_copy(update={"grid": cfg.grid.model_copy(update={"max_import_kw": 6.0})})
    assert HardLimits.from_config(capped).grid_setpoint_max(None) == (6000.0, "maximale Bezugsleistung")


def test_safe_commands(limits) -> None:
    def safe(kind, value, actuator="victron"):
        return Command(actuator, kind, value, urgent=True, reasons=("safe_state",))

    assert limits.safe_commands("victron") == (
        safe(CommandKind.GRID_SETPOINT, None),
        safe(CommandKind.MAX_DISCHARGE, -1.0),
        safe(CommandKind.MIN_SOC, 20.0),
    )
    assert limits.safe_commands("wallbox.evcs") == (safe(CommandKind.CURRENT, 6.0, "wallbox.evcs"),)
    assert limits.safe_commands("vehicle.tesla") == (safe(CommandKind.CURRENT, 5.0, "vehicle.tesla"),)


K = CommandKind
TOWARD_SAFE = [
    (Command("wallbox.evcs", K.CURRENT, 6.0), {("wallbox.evcs", K.CURRENT): 16.0}, True),
    (Command("wallbox.evcs", K.CURRENT, 16.0), {("wallbox.evcs", K.CURRENT): 6.0}, False),
    (Command("wallbox.evcs", K.CURRENT, 6.0), {}, True),
    (Command("wallbox.evcs", K.CURRENT, 10.0), {}, False),
    (Command("vehicle.tesla", K.CHARGING, False), {}, True),
    (Command("vehicle.tesla", K.CHARGING, True), {}, False),
    (Command("victron", K.GRID_SETPOINT, None), {}, True),
    (Command("victron", K.GRID_SETPOINT, 500.0), {("victron", K.GRID_SETPOINT): 1000.0}, True),
    (Command("victron", K.GRID_SETPOINT, 500.0), {("victron", K.GRID_SETPOINT): None}, False),
    (Command("victron", K.GRID_SETPOINT, 500.0), {}, False),
    (Command("victron", K.MAX_DISCHARGE, -1.0), {}, True),
    (Command("victron", K.MAX_DISCHARGE, 5000.0), {("victron", K.MAX_DISCHARGE): 3000.0}, True),
    (Command("victron", K.MAX_DISCHARGE, 5000.0), {("victron", K.MAX_DISCHARGE): -1.0}, False),
    (Command("victron", K.MIN_SOC, 20.0), {}, True),
    (Command("victron", K.MIN_SOC, 30.0), {("victron", K.MIN_SOC): 40.0}, True),
    (Command("victron", K.MIN_SOC, 30.0), {("victron", K.MIN_SOC): 25.0}, False),
    (Command("wallbox.evcs", K.CURRENT, True), {}, False),
]


@pytest.mark.parametrize(("command", "present", "expected"), TOWARD_SAFE)
def test_toward_safe(limits, command, present, expected) -> None:
    assert toward_safe(command, make_ctx(present=present), limits) is expected
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/shared/test_control.py tests/unit/core/safety -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems.shared.control'`)

- [ ] **Step 3: Module umsetzen wie unter „Interfaces“ beschrieben**

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/shared/test_control.py tests/unit/core/safety -q`
Expected: `24 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared/control.py src/bindaems/core/safety tests/unit/shared/test_control.py tests/unit/core/safety
git commit -m "feat(safety): Steuerungstypen, Absicht und Aktorkatalog mit harten Grenzen"
```

---

### Task 3: Regeln 1–3 – Wertebereiche

**Files:**
- Create: `src/bindaems/core/safety/ranges.py`
- Test: `tests/unit/core/safety/test_ranges.py`

**Interfaces:**
- Consumes: `Command`, `CommandKind` (Task 2), `HardLimits`, `Step`, `Finding`, `de()` (Task 2)
- Produces: `check_range(command: Command, limits: HardLimits, budget_w: float | None) -> Step`. Aktor und Art sind schon geprüft (Task 9); für `current` gilt `limits.currents[command.actuator]`.

Die Regeln werden in dieser Reihenfolge geprüft, je Art von oben nach unten:

| Art | Bedingung | Ergebnis | Code |
|---|---|---|---|
| alle | falscher Typ: `charging` braucht `bool`; Zahlen-Arten brauchen `int` oder `float`, aber kein `bool`; `None` nur beim Netz-Sollwert; dazu nicht endlich | ablehnen | `value.invalid` |
| `current` | nicht ganzzahlig | abrunden | `range.integer` |
| `current` | > `max_a` | auf `max_a` | `range.max` |
| `current` | < `min_a` (nach dem Abrunden) | ablehnen | `range.min` |
| `grid_setpoint` | `None` | unverändert | – |
| `grid_setpoint` | < 0 | auf 0 | `range.negative` |
| `grid_setpoint` | > `grid_setpoint_max(budget_w)` | auf die Grenze | `range.ceiling` |
| `max_discharge` | −1 | unverändert | – |
| `max_discharge` | < 0 und ≠ −1 | ablehnen | `range.invalid` |
| `max_discharge` | > `max_discharge_w` | auf `max_discharge_w` | `range.max` |
| `min_soc` | < Reserve | ablehnen | `range.reserve` |
| `min_soc` | > 100 | ablehnen | `range.invalid` |

| Code | Text |
|---|---|
| `value.invalid` | `f"Ungültiger Wert für „{kind}“: {value!r}."` |
| `range.integer` | `f"Auf ganze Ampere abgerundet: {de(v)} A → {n} A."` |
| `range.max` | `f"Auf den Höchstwert {de(limit)} {unit} begrenzt."` (`unit` ist A bzw. W) |
| `range.min` | `f"Unter dem Mindeststrom {min_a} A; zum Abschalten das Laden beenden."` |
| `range.negative` | `f"Netz-Sollwert nie negativ (kein Akku ins Netz): {de(v)} W → 0 W."` |
| `range.ceiling` | `f"Netz-Sollwert auf {de(limit)} W begrenzt ({name})."` |
| `range.invalid` | `f"Wert {de(v)} {unit} liegt außerhalb des zulässigen Bereichs."` (`unit` ist W bzw. %) |
| `range.reserve` | `f"Min-SOC {de(v)} % liegt unter der USV-Reserve {de(reserve)} %."` |

- [ ] **Step 1: Failing Tests schreiben**

```python
K = CommandKind
CASES = [
    (Command("wallbox.evcs", K.CURRENT, 20.0), 16.0, ["range.max"], False),
    (Command("wallbox.evcs", K.CURRENT, 10.7), 10.0, ["range.integer"], False),
    (Command("wallbox.evcs", K.CURRENT, 5.9), None, ["range.integer", "range.min"], True),
    (Command("vehicle.tesla", K.CURRENT, 4.0), None, ["range.min"], True),
    (Command("vehicle.tesla", K.CURRENT, 5.0), 5.0, [], False),
    (Command("wallbox.evcs", K.CHARGING, True), True, [], False),
    (Command("victron", K.GRID_SETPOINT, -300.0), 0.0, ["range.negative"], False),
    (Command("victron", K.GRID_SETPOINT, 9000.0), 8000.0, ["range.ceiling"], False),
    (Command("victron", K.GRID_SETPOINT, None), None, [], False),
    (Command("victron", K.MAX_DISCHARGE, -1.0), -1.0, [], False),
    (Command("victron", K.MAX_DISCHARGE, -5.0), None, ["range.invalid"], True),
    (Command("victron", K.MAX_DISCHARGE, 15000.0), 12000.0, ["range.max"], False),
    (Command("victron", K.MIN_SOC, 15.0), None, ["range.reserve"], True),
    (Command("victron", K.MIN_SOC, 101.0), None, ["range.invalid"], True),
    (Command("wallbox.evcs", K.CURRENT, True), None, ["value.invalid"], True),
    (Command("wallbox.evcs", K.CURRENT, float("nan")), None, ["value.invalid"], True),
    (Command("wallbox.evcs", K.CHARGING, 1.0), None, ["value.invalid"], True),
    (Command("victron", K.MIN_SOC, None), None, ["value.invalid"], True),
]


@pytest.mark.parametrize(("command", "value", "codes", "reject"), CASES)
def test_ranges(limits, command, value, codes, reject) -> None:
    step = check_range(command, limits, None)
    assert (step.value, [f.code for f in step.findings], step.reject) == (value, codes, reject)


def test_budget_lowers_ceiling(limits) -> None:
    step = check_range(Command("victron", K.GRID_SETPOINT, 6000.0), limits, 5000.0)
    assert step.value == 5000.0
    assert step.findings[0].text == "Netz-Sollwert auf 5000 W begrenzt (Leistungsbudget)."


def test_texts(limits) -> None:
    def text(command):
        return check_range(command, limits, None).findings[0].text

    assert text(Command("wallbox.evcs", K.CURRENT, 20.0)) == "Auf den Höchstwert 16 A begrenzt."
    assert text(Command("wallbox.evcs", K.CURRENT, 10.7)) == "Auf ganze Ampere abgerundet: 10,7 A → 10 A."
    assert text(Command("wallbox.evcs", K.CURRENT, 5.0)) == (
        "Unter dem Mindeststrom 6 A; zum Abschalten das Laden beenden."
    )
    assert text(Command("victron", K.GRID_SETPOINT, -300.0)) == (
        "Netz-Sollwert nie negativ (kein Akku ins Netz): −300 W → 0 W."
    )
    assert text(Command("victron", K.MIN_SOC, 15.0)) == "Min-SOC 15 % liegt unter der USV-Reserve 20 %."
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/safety/test_ranges.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: `check_range` nach den Tabellen umsetzen**

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/safety/test_ranges.py -q`
Expected: `20 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/safety/ranges.py tests/unit/core/safety/test_ranges.py
git commit -m "feat(safety): Regeln 1–3 – Sollwerte klemmen oder ablehnen"
```

---

### Task 4: Regel 4 – Phasenvorhersage

**Files:**
- Create: `src/bindaems/core/safety/phases.py`
- Test: `tests/unit/core/safety/test_phases.py`

**Interfaces:**
- Consumes: `Command`, `CommandValue`, `SafetyContext`, `HardLimits`, `Step`, `Finding`, `de()` (Task 2)
- Produces:
  - `ACTIVE_PHASE_A = 0.5`
  - `check_phases(command: Command, value: CommandValue, ctx: SafetyContext, limits: HardLimits) -> Step`. `value` ist der Wert nach Task 3.

**Grundbegriffe:**
- Spielraum je Phase: `h_p = limits.phase_limit_a − I_p`, mit `I_p = ctx.phase_import_a[p]`.
- Die bindende Phase in den Texten ist die mit dem kleinsten Spielraum unter den betroffenen Phasen; bei Gleichstand gilt die Reihenfolge L1, L2, L3.
- Fehlt `I_p` für eine betroffene Phase, wird eine Erhöhung abgelehnt (`phase.unknown`). Eine Verringerung bleibt unverändert.
- **Wallbox-Phasen:**
  - Gemessener Strom `m_p = ctx.wallbox_phase_a.get(key, {}).get(p, 0.0)`.
  - Aktiv sind die Phasen mit `m_p ≥ 0,5 A`. Ist keine aktiv, gelten alle drei Phasen aus `limits.wallbox_phases[key]`.
  - Beim Tesla ist `key = ctx.vehicle_wallbox.get(actuator)`. Ist er `None`, gelten L1–L3 mit `m_p = 0`.

**Regeln je Art:**

| Art | Erhöhung, wenn … | Prüfung |
|---|---|---|
| `current` | Ist-Strom (`present[(a, CURRENT)]`) unbekannt oder kleiner als der Wert | `erlaubt = floor(min_p(h_p + m_p))` über die aktiven Phasen. Wert ≤ erlaubt: unverändert. Sonst bei einer Erhöhung: erlaubt ≥ `min_a` → auf erlaubt (`phase.reduced`), sonst ablehnen (`phase.rejected`). Bei einer Verringerung: auf `max(min_a, erlaubt)` (`phase.reduced` bzw. `phase.minimum`) |
| `charging` | Wert `True` und Ist-Wert nicht `True` | Ladestrom `I_c` = bekannter Ist-Strom, sonst `max_a`. Geprüft wird `I_p − m_p + I_c ≤ Grenze` auf allen drei Phasen der Wallbox; verletzt → ablehnen (`phase.rejected`). `False` ist immer erlaubt. |
| `grid_setpoint` | Wert nicht `None` und größer als der wirksame Ist-Sollwert `S0` (Override, sonst `base_grid_setpoint_w`) | `S_max = S0 + 3 × U × min_p h_p`. Wert ≤ `S_max`: unverändert. Sonst: `S_max ≥ 0` → auf `floor(S_max)` (`phase.reduced`); `S_max < 0` und `S0 ≥ 0` → auf 0 (`phase.reduced`); sonst ablehnen (`phase.rejected`) |
| `max_discharge` | Wert ≥ 0 und kleiner als die aktuelle Entladeleistung `D` | `ΔP = D − Wert` ins Netz. `L_min = D − 3 × U × min_p h_p`. Wert ≥ `L_min`: unverändert; sonst auf `ceil(L_min)` (`phase.discharge`), über `max_discharge_w` auf −1. Ist `D` unbekannt, wird eine Verschärfung abgelehnt (`phase.battery_unknown`); das heißt Wert ≥ 0 und Ist-Wert unbekannt, −1 oder größer. |
| `min_soc` | Wert > Reserve und Wert > SOC | `ΔP = D`; wenn `I_p + ΔP / (3 × U)` auf einer Phase über der Grenze liegt, ablehnen (`phase.rejected`). Ist der SOC unbekannt, wird abgelehnt (`phase.soc_unknown`); ist `D` unbekannt, ebenfalls (`phase.battery_unknown`). |

`U` ist `limits.voltage_v`.

| Code | Text |
|---|---|
| `phase.reduced` | `f"Phase {p} würde über {de(limit)} A steigen; reduziert auf {de(v)} {unit}."` |
| `phase.minimum` | `f"Phase {p} würde über {de(limit)} A steigen; nur noch Mindeststrom {min_a} A möglich."` |
| `phase.rejected` | `f"Phase {p} würde über {de(limit)} A steigen."` |
| `phase.discharge` | `f"Phase {p} würde über {de(limit)} A steigen; Entladegrenze auf {de(v)} W angehoben."` |
| `phase.unknown` | `"Phasenströme unbekannt; nur Verringerungen erlaubt."` |
| `phase.battery_unknown` | `"Akkuleistung unbekannt."` |
| `phase.soc_unknown` | `"Akku-SOC unbekannt."` |

- [ ] **Step 1: Failing Tests schreiben** (Grenze 33 A; `IMP = {"L1": 30.0, "L2": 20.0, "L3": 10.0}`)

```python
K = CommandKind
IMP = {"L1": 30.0, "L2": 20.0, "L3": 10.0}
ALL = lambda a: {p: a for p in PHASES}  # noqa: E731


def test_evcs_increase_reduced_to_headroom(limits) -> None:
    ctx = make_ctx(phase_import_a=IMP, wallbox_phase_a={"evcs": ALL(6.0)},
                   present={("wallbox.evcs", K.CURRENT): 6.0})
    step = check_phases(Command("wallbox.evcs", K.CURRENT, 16.0), 16.0, ctx, limits)
    assert step.value == 9.0
    assert step.findings == (Finding("phase.reduced", "Phase L1 würde über 33 A steigen; reduziert auf 9 A."),)


def test_evcs_start_without_headroom_rejected(limits) -> None:
    step = check_phases(Command("wallbox.evcs", K.CURRENT, 16.0), 16.0, make_ctx(phase_import_a=IMP), limits)
    assert step.reject and step.findings[0].code == "phase.rejected"


def test_decrease_is_never_rejected(limits) -> None:
    ctx = make_ctx(phase_import_a={"L1": 45.0, "L2": 20.0, "L3": 10.0}, wallbox_phase_a={"evcs": ALL(16.0)},
                   present={("wallbox.evcs", K.CURRENT): 16.0})
    step = check_phases(Command("wallbox.evcs", K.CURRENT, 10.0), 10.0, ctx, limits)
    assert (step.value, step.reject) == (6.0, False)
    assert step.findings[0].text == "Phase L1 würde über 33 A steigen; nur noch Mindeststrom 6 A möglich."


def test_two_phase_car_ignores_unused_phase(limits) -> None:
    ctx = make_ctx(phase_import_a={"L1": 20.0, "L2": 20.0, "L3": 32.5},
                   wallbox_phase_a={"evcs": {"L1": 10.0, "L2": 10.0, "L3": 0.0}},
                   present={("wallbox.evcs", K.CURRENT): 10.0})
    assert check_phases(Command("wallbox.evcs", K.CURRENT, 16.0), 16.0, ctx, limits).value == 16.0


def test_unknown_phase_currents_allow_only_decreases(limits) -> None:
    ctx = make_ctx(phase_import_a={"L1": 30.0, "L2": None, "L3": 10.0},
                   present={("wallbox.evcs", K.CURRENT): 10.0})
    up = check_phases(Command("wallbox.evcs", K.CURRENT, 12.0), 12.0, ctx, limits)
    assert up.reject and up.findings[0].text == "Phasenströme unbekannt; nur Verringerungen erlaubt."
    assert not check_phases(Command("wallbox.evcs", K.CURRENT, 8.0), 8.0, ctx, limits).reject


def test_tesla_at_known_wallbox(limits) -> None:
    ctx = make_ctx(phase_import_a=IMP, wallbox_phase_a={"twc": ALL(8.0)},
                   present={("vehicle.tesla", K.CURRENT): 8.0})
    assert check_phases(Command("vehicle.tesla", K.CURRENT, 16.0), 16.0, ctx, limits).value == 11.0


def test_tesla_at_unknown_wallbox_uses_all_phases(limits) -> None:
    ctx = make_ctx(phase_import_a=IMP, vehicle_wallbox={"vehicle.tesla": None})
    assert check_phases(Command("vehicle.tesla", K.CURRENT, 16.0), 16.0, ctx, limits).reject


def test_start_checks_set_current(limits) -> None:
    present = {("wallbox.evcs", K.CURRENT): 10.0, ("wallbox.evcs", K.CHARGING): False}
    start, stop = Command("wallbox.evcs", K.CHARGING, True), Command("wallbox.evcs", K.CHARGING, False)
    assert check_phases(start, True, make_ctx(phase_import_a=IMP, present=present), limits).reject
    assert not check_phases(start, True, make_ctx(phase_import_a=ALL(20.0), present=present), limits).reject
    assert not check_phases(stop, False, make_ctx(phase_import_a=IMP, present=present), limits).reject


def test_setpoint_increase_reduced(limits) -> None:
    ctx = make_ctx(phase_import_a=IMP, present={("victron", K.GRID_SETPOINT): None})
    step = check_phases(Command("victron", K.GRID_SETPOINT, 6000.0), 6000.0, ctx, limits)
    assert step.value == 2070.0
    assert step.findings[0].text == "Phase L1 würde über 33 A steigen; reduziert auf 2070 W."
    assert check_phases(Command("victron", K.GRID_SETPOINT, 1000.0), 1000.0, ctx, limits).value == 1000.0


def test_setpoint_on_overloaded_phase(limits) -> None:
    over = {"L1": 36.0, "L2": 20.0, "L3": 10.0}
    from_override = make_ctx(phase_import_a=over, present={("victron", K.GRID_SETPOINT): 500.0})
    assert check_phases(Command("victron", K.GRID_SETPOINT, 3000.0), 3000.0, from_override, limits).value == 0.0
    from_feed_in = make_ctx(phase_import_a=over, base_grid_setpoint_w=-200.0,
                            present={("victron", K.GRID_SETPOINT): None})
    assert check_phases(Command("victron", K.GRID_SETPOINT, 0.0), 0.0, from_feed_in, limits).reject


def test_discharge_lock_raised_to_protect_phases(limits) -> None:
    ctx = make_ctx(phase_import_a=IMP, battery_discharge_w=9000.0,
                   present={("victron", K.MAX_DISCHARGE): -1.0})
    step = check_phases(Command("victron", K.MAX_DISCHARGE, 0.0), 0.0, ctx, limits)
    assert step.value == 6930.0
    assert step.findings[0].text == "Phase L1 würde über 33 A steigen; Entladegrenze auf 6930 W angehoben."


def test_min_soc_above_soc_needs_headroom(limits) -> None:
    ctx = make_ctx(phase_import_a=IMP, battery_discharge_w=9000.0, soc_pct=50.0)
    assert check_phases(Command("victron", K.MIN_SOC, 60.0), 60.0, ctx, limits).reject
    assert not check_phases(Command("victron", K.MIN_SOC, 40.0), 40.0, ctx, limits).reject
    unknown = check_phases(Command("victron", K.MIN_SOC, 60.0), 60.0, make_ctx(soc_pct=None), limits)
    assert unknown.findings[0].text == "Akku-SOC unbekannt."
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/safety/test_phases.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: `check_phases` nach der Tabelle umsetzen**

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/safety/test_phases.py -q`
Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/safety/phases.py tests/unit/core/safety/test_phases.py
git commit -m "feat(safety): Regel 4 – Phasenströme nach dem Befehl vorhersagen"
```

---

### Task 5: Regel 5 – Rate-Limits und Totbänder

**Files:**
- Create: `src/bindaems/core/safety/rate.py`
- Test: `tests/unit/core/safety/test_rate.py`

**Interfaces:**
- Consumes: `Command`, `CommandKind`, `PERSISTENT_KINDS` (Task 2), `HardLimits`, `SafetyContext`, `Finding`, `de()` (Task 2)
- Produces:
  - `EVCS_INTERVAL_S = 5.0`, `VICTRON_OVERRIDE_INTERVAL_S = 2.0`, `EVCS_DEADBAND_A = 1.0`, `VICTRON_DEADBAND_W = 50.0`
  - `class RateLimiter`:
    - `__init__(self, limits: HardLimits) -> None`
    - `check(self, command: Command, now: datetime) -> Finding | None`: prüft der Reihe nach Mindestabstand, Befehle je Stunde und Start/Stopp je Stunde
    - `record(self, command: Command, now: datetime) -> None`
  - `deadband(command: Command, value: CommandValue, ctx: SafetyContext, limits: HardLimits) -> Finding | None`

**Mindestabstände** gelten je Aktor und Art, seit dem letzten `record`:

| Familie | `current` | `charging` | `grid_setpoint` | persistente Arten |
|---|---|---|---|---|
| EVCS | 5 s | 5 s | – | – |
| Victron | – | – | 2 s | keiner (Schreibbudget, Task 6) |
| Tesla | `current_interval_s` (90 s) | keiner | – | – |

**Stundenlimits beim Tesla** (gleitendes Fenster):
- Gezählt werden die `record`-Zeitpunkte mit `now − t < 3600 s`.
- Alle Befehle zusammen dürfen höchstens `per_hour` erreichen, die `charging`-Befehle höchstens `start_stop_per_hour`.

**Totband:**
- Ohne bekannten Ist-Wert gibt es kein Totband.
- Gleicher Wert → `rate.unchanged`. Das gilt für `bool`, `None == None` und Zahlen mit Abstand < 1e-9.
- Sonst nur bei Zahlen:
  - EVCS-`current`: < 1 A
  - Tesla-`current`: < `current_step_a`
  - `grid_setpoint`: < 50 W → `rate.deadband`
- Persistente Arten haben nur den Vergleich auf Gleichheit.

| Code | Text |
|---|---|
| `rate.interval` | `f"Mindestabstand {de(s)} s noch nicht erreicht."` |
| `rate.per_hour` | `f"Höchstens {n} Befehle pro Stunde erreicht."` |
| `rate.start_stop` | `f"Höchstens {n} Start/Stopp pro Stunde erreicht."` |
| `rate.unchanged` | `"Wert unverändert."` |
| `rate.deadband` | `f"Änderung unter dem Totband ({de(band)} {unit})."` |

- [ ] **Step 1: Failing Tests schreiben**

```python
K = CommandKind
S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731


def test_evcs_current_interval(limits) -> None:
    rl, cmd = RateLimiter(limits), Command("wallbox.evcs", K.CURRENT, 10.0)
    rl.record(cmd, T0)
    assert rl.check(cmd, S(4)) == Finding("rate.interval", "Mindestabstand 5 s noch nicht erreicht.")
    assert rl.check(cmd, S(5)) is None


def test_kinds_are_independent(limits) -> None:
    rl = RateLimiter(limits)
    rl.record(Command("wallbox.evcs", K.CURRENT, 10.0), T0)
    assert rl.check(Command("wallbox.evcs", K.CHARGING, True), S(1)) is None


def test_victron_setpoint_interval(limits) -> None:
    rl, cmd = RateLimiter(limits), Command("victron", K.GRID_SETPOINT, 500.0)
    rl.record(cmd, T0)
    assert rl.check(cmd, S(1)).code == "rate.interval"
    assert rl.check(cmd, S(2)) is None


def test_persistent_kinds_have_no_interval(limits) -> None:
    rl, cmd = RateLimiter(limits), Command("victron", K.MIN_SOC, 30.0)
    rl.record(cmd, T0)
    assert rl.check(cmd, T0) is None


def test_tesla_current_interval(limits) -> None:
    rl, cmd = RateLimiter(limits), Command("vehicle.tesla", K.CURRENT, 10.0)
    rl.record(cmd, T0)
    assert rl.check(cmd, S(89)).text == "Mindestabstand 90 s noch nicht erreicht."
    assert rl.check(cmd, S(90)) is None


def test_tesla_hourly_limit(limits) -> None:
    rl = RateLimiter(limits)
    for i in range(20):
        rl.record(Command("vehicle.tesla", K.CURRENT, 10.0), S(91 * i))
    later = Command("vehicle.tesla", K.CURRENT, 12.0)
    assert rl.check(later, S(91 * 20)) == Finding("rate.per_hour", "Höchstens 20 Befehle pro Stunde erreicht.")
    assert rl.check(later, S(3601)) is None


def test_tesla_start_stop_limit(limits) -> None:
    rl = RateLimiter(limits)
    for i in range(6):
        rl.record(Command("vehicle.tesla", K.CHARGING, i % 2 == 0), S(60 * i))
    finding = rl.check(Command("vehicle.tesla", K.CHARGING, True), S(420))
    assert finding == Finding("rate.start_stop", "Höchstens 6 Start/Stopp pro Stunde erreicht.")


DEADBAND = [
    (Command("wallbox.evcs", K.CURRENT, 10.0), 10.0, "Wert unverändert."),
    (Command("vehicle.tesla", K.CURRENT, 11.0), 10.0, "Änderung unter dem Totband (2 A)."),
    (Command("vehicle.tesla", K.CURRENT, 12.0), 10.0, None),
    (Command("victron", K.GRID_SETPOINT, 1030.0), 1000.0, "Änderung unter dem Totband (50 W)."),
    (Command("victron", K.GRID_SETPOINT, None), None, "Wert unverändert."),
    (Command("victron", K.GRID_SETPOINT, None), 1000.0, None),
    (Command("victron", K.MIN_SOC, 30.0), 30.0, "Wert unverändert."),
    (Command("victron", K.MIN_SOC, 31.0), 30.0, None),
    (Command("wallbox.evcs", K.CHARGING, True), True, "Wert unverändert."),
]


@pytest.mark.parametrize(("command", "present", "text"), DEADBAND)
def test_deadband(limits, command, present, text) -> None:
    ctx = make_ctx(present={(command.actuator, command.kind): present})
    finding = deadband(command, command.value, ctx, limits)
    assert (finding.text if finding else None) == text


def test_no_deadband_without_present_value(limits) -> None:
    cmd = Command("victron", K.GRID_SETPOINT, 1000.0)
    assert deadband(cmd, 1000.0, make_ctx(), limits) is None
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/safety/test_rate.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: `RateLimiter` und `deadband` umsetzen** (Zeitstempel je Schlüssel in `collections.deque`; Einträge außerhalb des Fensters beim Prüfen verwerfen)

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/safety/test_rate.py -q`
Expected: `17 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/safety/rate.py tests/unit/core/safety/test_rate.py
git commit -m "feat(safety): Regel 5 – Mindestabstände, Stundenlimits und Totbänder"
```

---

### Task 6: Regel 6 – Schreibbudget und atomare Dateien

**Files:**
- Create: `src/bindaems/core/persistence/__init__.py`, `src/bindaems/core/persistence/files.py`, `src/bindaems/core/safety/budget.py`
- Test: `tests/unit/core/persistence/test_files.py`, `tests/unit/core/safety/test_budget.py`

**Interfaces:**
- Consumes: `Finding` (Task 2), `Alarm`, `Severity` (`shared/domain.py`)
- Produces in `core/persistence/files.py`:
  - `write_json_atomic(path: Path, data: Any) -> None`:
    - legt das Verzeichnis an
    - schreibt `<name>.tmp` im selben Verzeichnis, dann `flush` und `os.fsync`, dann `os.replace`
    - entfernt bei einem Fehler die temporäre Datei und wirft den Fehler weiter
  - `read_json(path: Path) -> Any`: `FileNotFoundError`, wenn die Datei fehlt; `ValueError` bei kaputtem JSON oder falscher Kodierung
- Produces in `core/safety/budget.py`, `class PersistentWriteBudget`:
  - `__init__(self, per_hour: int, per_day: int, path: Path, now: datetime) -> None`
    - Datei fehlt → leeres Budget.
    - Datei unlesbar oder nicht im Format `{"writes": [ISO-Zeit, …]}` → gesperrt bis `now + 1 h`, Alarm `safety.write_budget_file`, Log-Fehler „Datei des Schreibbudgets unlesbar“.
  - `check(self, now: datetime) -> Finding | None`
    - Ergebnis `budget.exhausted`, solange gesperrt oder solange Einträge die Grenze erreichen: ≥ `per_hour` mit `now − t < 1 h` oder ≥ `per_day` mit `now − t < 24 h`.
  - `record(self, now: datetime) -> None`
    - Eintrag anhängen, Einträge älter als 24 h verwerfen, Datei atomar schreiben.
    - Ein Schreibfehler wird geloggt; der Zähler im Speicher gilt weiter.
  - `note_rejection(self, now: datetime) -> None`: merkt die Ablehnung für den Alarm.
  - `alarms(self, now: datetime) -> list[Alarm]`

| Code / Alarm | Schwere | Text | aktiv |
|---|---|---|---|
| Finding `budget.exhausted` | – | `f"Schreibbudget für persistente Einstellungen erschöpft ({per_hour}/h, {per_day}/Tag)."` | – |
| `safety.write_budget` | WARNING | `"Schreibbudget für persistente Einstellungen erschöpft."` | ab der ersten `note_rejection`, bis `check()` wieder `None` liefert; `since` = erste Ablehnung |
| `safety.write_budget_file` | WARNING | `"Datei des Schreibbudgets unlesbar; persistente Einstellungen bleiben 1 h gesperrt."` | bis zum Ende der Sperre; `since` = Ladezeit |

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/persistence/test_files.py
def test_roundtrip_without_leftovers(tmp_path: Path) -> None:
    target = tmp_path / "sub" / "x.json"
    write_json_atomic(target, {"a": 1})
    assert read_json(target) == {"a": 1}
    assert [p.name for p in target.parent.iterdir()] == ["x.json"]


def test_read_errors(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_json(tmp_path / "fehlt.json")
    (tmp_path / "kaputt.json").write_text("kaputt")
    with pytest.raises(ValueError):
        read_json(tmp_path / "kaputt.json")


def test_failed_replace_keeps_old_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "x.json"
    write_json_atomic(target, {"v": 1})
    monkeypatch.setattr("bindaems.core.persistence.files.os.replace", Mock(side_effect=OSError("voll")))
    with pytest.raises(OSError):
        write_json_atomic(target, {"v": 2})
    assert read_json(target) == {"v": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]
```

```python
# tests/unit/core/safety/test_budget.py
M = lambda m: T0 + timedelta(minutes=m)  # noqa: E731
TEXT = "Schreibbudget für persistente Einstellungen erschöpft (12/h, 100/Tag)."


def test_hour_limit(tmp_path: Path) -> None:
    budget = PersistentWriteBudget(12, 100, tmp_path / "b.json", T0)
    for i in range(12):
        assert budget.check(M(i)) is None
        budget.record(M(i))
    assert budget.check(M(12)) == Finding("budget.exhausted", TEXT)
    assert budget.check(M(60) + timedelta(seconds=1)) is None


def test_day_limit(tmp_path: Path) -> None:
    budget = PersistentWriteBudget(12, 20, tmp_path / "b.json", T0)
    for i in [*range(12), *range(120, 128)]:
        budget.record(M(i))
    assert budget.check(M(240)).text == "Schreibbudget für persistente Einstellungen erschöpft (12/h, 20/Tag)."
    assert budget.check(M(24 * 60 + 12)) is None


def test_persists_across_restart(tmp_path: Path) -> None:
    first = PersistentWriteBudget(12, 100, tmp_path / "b.json", T0)
    for i in range(12):
        first.record(M(i))
    assert PersistentWriteBudget(12, 100, tmp_path / "b.json", M(13)).check(M(13)) is not None


def test_missing_file_is_empty(tmp_path: Path) -> None:
    assert PersistentWriteBudget(12, 100, tmp_path / "b.json", T0).check(T0) is None


def test_unreadable_file_blocks_for_an_hour(tmp_path: Path) -> None:
    (tmp_path / "b.json").write_text("kaputt")
    budget = PersistentWriteBudget(12, 100, tmp_path / "b.json", T0)
    assert budget.check(M(59)) is not None
    assert budget.alarms(M(59)) == [Alarm(
        "safety.write_budget_file", Severity.WARNING,
        "Datei des Schreibbudgets unlesbar; persistente Einstellungen bleiben 1 h gesperrt.", T0,
    )]
    assert budget.check(M(61)) is None
    assert budget.alarms(M(61)) == []


def test_exhausted_alarm_until_capacity_returns(tmp_path: Path) -> None:
    budget = PersistentWriteBudget(12, 100, tmp_path / "b.json", T0)
    for i in range(12):
        budget.record(M(i))
    budget.note_rejection(M(12))
    assert budget.alarms(M(30)) == [Alarm(
        "safety.write_budget", Severity.WARNING, "Schreibbudget für persistente Einstellungen erschöpft.", M(12),
    )]
    assert budget.alarms(M(61)) == []


def test_old_entries_dropped_from_file(tmp_path: Path) -> None:
    budget = PersistentWriteBudget(12, 100, tmp_path / "b.json", T0)
    budget.record(T0)
    later = T0 + timedelta(hours=25)
    budget.record(later)
    assert read_json(tmp_path / "b.json") == {"writes": [later.isoformat()]}
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/persistence tests/unit/core/safety/test_budget.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: `files.py` und `budget.py` umsetzen**

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/persistence tests/unit/core/safety/test_budget.py -q`
Expected: `10 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/persistence src/bindaems/core/safety/budget.py tests/unit/core/persistence tests/unit/core/safety/test_budget.py
git commit -m "feat(safety): Regel 6 – Schreibbudget für persistente Einstellungen"
```

---

### Task 7: Regel 7 – Eingangsdaten prüfen

**Files:**
- Create: `src/bindaems/core/safety/inputs.py`
- Test: `tests/unit/core/safety/test_inputs.py`

**Interfaces:**
- Consumes: `Snapshot`, `Alarm` (`shared/domain.py`), `InputStatus`, `de()` (Task 2)
- Produces:
  - `SAFETY_SIGNALS = ("grid.l1.power_w", "grid.l2.power_w", "grid.l3.power_w", "grid.l1.voltage_v", "grid.l2.voltage_v", "grid.l3.voltage_v", "battery.soc_pct", "battery.power_w")`
  - `SOC_JUMP_PP = 5.0`, `SOC_JUMP_WINDOW_S = 60.0`, `SOC_JUMP_HOLD_S = 60.0`
  - `class InputCheck` mit `evaluate(self, snap: Snapshot, plausibility: Sequence[Alarm], now: datetime) -> InputStatus`
- Probleme in dieser Reihenfolge:
  1. `f"Messwert fehlt oder ist veraltet: {', '.join(names)}."`: alle `SAFETY_SIGNALS` ohne Qualität `OK`, in Listenreihenfolge.
  2. die Meldung jedes übergebenen Alarms, dessen ID mit `plaus.` beginnt; andere Alarme zählen nicht.
  3. `f"Sprung im Akku-SOC: {de(a)} % → {de(b)} %."`, solange die Haltezeit läuft.
- Sprung:
  - `InputCheck` merkt sich die letzte gültige SOC-Messung (Wert und `Reading.ts`).
  - Eine neue Messung mit anderem `ts`, `|b − a| > 5` und Abstand ≤ 60 s setzt die Haltezeit auf `now + 60 s`.
  - Danach gilt die neue Messung als letzte.

- [ ] **Step 1: Failing Tests schreiben**

```python
GOOD = {
    "grid.l1.power_w": 1000.0, "grid.l2.power_w": 1000.0, "grid.l3.power_w": 1000.0,
    "grid.l1.voltage_v": 230.0, "grid.l2.voltage_v": 230.0, "grid.l3.voltage_v": 230.0,
    "battery.soc_pct": 50.0, "battery.power_w": 0.0,
}
S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731


def test_all_ok() -> None:
    assert InputCheck().evaluate(snap(GOOD), [], T0) == InputStatus(True, ())


def test_missing_or_stale_signal() -> None:
    values = {k: v for k, v in GOOD.items() if k != "grid.l2.voltage_v"}
    assert InputCheck().evaluate(snap(values), [], T0) == InputStatus(
        False, ("Messwert fehlt oder ist veraltet: grid.l2.voltage_v.",)
    )


def test_plausibility_alarm_passes_through() -> None:
    alarms = [
        Alarm("plaus.balance", Severity.WARNING, "Energiebilanz geht nicht auf.", T0),
        Alarm("competitor.dess", Severity.ERROR, "Dynamic ESS ist aktiv (Modus 1).", T0),
    ]
    assert InputCheck().evaluate(snap(GOOD), alarms, T0).problems == ("Energiebilanz geht nicht auf.",)


def test_soc_jump_holds_60s() -> None:
    check = InputCheck()
    check.evaluate(snap_at(T0, GOOD), [], T0)
    jumped = GOOD | {"battery.soc_pct": 58.0}
    assert check.evaluate(snap_at(S(10), jumped), [], S(10)).problems == ("Sprung im Akku-SOC: 50 % → 58 %.",)
    assert check.evaluate(snap_at(S(71), jumped), [], S(71)).ok


def test_small_soc_change_is_fine() -> None:
    check = InputCheck()
    check.evaluate(snap_at(T0, GOOD), [], T0)
    assert check.evaluate(snap_at(S(10), GOOD | {"battery.soc_pct": 53.0}), [], S(10)).ok


def test_change_after_long_gap_is_no_jump() -> None:
    check = InputCheck()
    check.evaluate(snap_at(T0, GOOD), [], T0)
    assert check.evaluate(snap_at(S(120), GOOD | {"battery.soc_pct": 60.0}), [], S(120)).ok
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/safety/test_inputs.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: `InputCheck` umsetzen**

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/safety/test_inputs.py -q`
Expected: `6 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/safety/inputs.py tests/unit/core/safety/test_inputs.py
git commit -m "feat(safety): Regel 7 – Frische, Plausibilität und SOC-Sprünge der Eingangsdaten"
```

---

### Task 8: Sperren und wirksame Betriebsarten

**Files:**
- Create: `src/bindaems/core/safety/locks.py`, `src/bindaems/core/safety/modes.py`
- Test: `tests/unit/core/safety/test_locks.py`, `tests/unit/core/safety/test_modes.py`

**Interfaces:**
- Consumes: `Alarm` (`shared/domain.py`), `Intent`, `ActuatorMode` (Task 2), `HardLimits` (Task 2)
- Produces in `locks.py`:
  - `class CheckLike(Protocol)`: lesbare Properties `id: str`, `status: str`, `message: str`. Ein `CheckResult` der Selbstprüfung passt dazu; `core.checks` wird nicht importiert.
  - `lock_causes(competitors: Sequence[Alarm], selfcheck: Sequence[CheckLike], limits: HardLimits) -> dict[str, list[str]]`. Zuerst kommen die Alarme in ihrer Reihenfolge, dann die Prüfpunkte. Ergebnisse gibt es nur für Aktoren aus `limits.actuators`; gleiche Texte erscheinen einmal.

    | Quelle | Bedingung | Sperrt | Grund |
    |---|---|---|---|
    | Alarm `competitor.dess`, `competitor.schedule.<k>` | – | `victron` | Alarmtext |
    | Alarm `competitor.evcs_mode.<n>` | – | `wallbox.<n>` | Alarmtext |
    | Alarm `competitor.vehicle_schedule.<v>` | – | `vehicle.<v>` | Alarmtext |
    | Prüfpunkt `ess_mode`, `batterylife`, `min_soc` | Status `fail` oder `unknown` | `victron` | Meldung des Prüfpunkts |
    | Prüfpunkt `dess`, `schedules` | Status `unknown` (ein `fail` steckt schon im Alarm) | `victron` | Meldung |
    | Prüfpunkt `evcs_mode.<n>` | Status `unknown` | `wallbox.<n>` | Meldung |
    | `phases`, `fresh_data`, Status `ok` oder `warn` | – | nichts (Gate in Task 10) | – |

  - `@dataclass(frozen=True) class LockInfo`: `reasons: tuple[str, ...]`, `since: datetime`, `cleared_at: datetime | None`
  - `class LockManager`:
    - `__init__(self, release_after_s: float) -> None`
    - `update(self, now: datetime, causes: Mapping[str, Sequence[str]]) -> None`:
      - Ein Aktor mit Ursachen wird gesperrt; `since` bleibt die erste Sperrzeit, `reasons` wird aktualisiert, `cleared_at` auf `None` gesetzt.
      - Bei einem gesperrten Aktor ohne Ursache wird beim ersten Mal `cleared_at = now` gesetzt; die Gründe bleiben.
    - `active(self, now: datetime) -> dict[str, LockInfo]`: entfernt Sperren mit `now − cleared_at ≥ release_after_s` und gibt die übrigen zurück.
- Produces in `modes.py`:
  - `REASON_NOT_ALLOWED = "In config.yaml nicht für den Live-Betrieb freigegeben (live_allowed)."`
  - `REASON_WATCHDOG_MODE = "Watchdog am Cerbo ist nicht scharf."`
  - `@dataclass(frozen=True) class ModeInfo`: `requested: ActuatorMode`, `effective: ActuatorMode`, `reasons: tuple[str, ...]`
  - `effective_modes(intent: Intent | None, limits: HardLimits, watchdog_armed: bool) -> dict[str, ModeInfo]`. Die Einträge stehen in der Reihenfolge von `limits.actuators`.
    - Angefordert ist `intent.actuators.get(id, DRY_RUN)`, ohne Absicht `DRY_RUN`.
    - Wirksam ist `LIVE` nur bei Anforderung `LIVE`, mit `live_allowed` und, wenn `needs_watchdog` gilt, mit scharfem Watchdog. Sonst gilt `DRY_RUN` mit dem ersten zutreffenden Grund.
  - `watchdog_required(intent: Intent | None, limits: HardLimits) -> bool`: `True`, wenn ein Aktor mit `needs_watchdog` und `live_allowed` live angefordert ist

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/safety/test_locks.py
S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731
DESS = "Dynamic ESS ist aktiv (Modus 1). Das EMS darf Victron nicht steuern."
EVCS = "EVCS „evcs“ ist nicht im manuellen Modus (Modus 1)."
PLAN = "Ladeplan im Fahrzeug „Tesla Model 3“ ist aktiv."


def alarm(id_: str, message: str) -> Alarm:
    return Alarm(id_, Severity.ERROR, message, T0)


def test_competitors_and_selfcheck_map_to_actuators(limits) -> None:
    competitors = [alarm("competitor.dess", DESS), alarm("competitor.evcs_mode.evcs", EVCS),
                   alarm("competitor.vehicle_schedule.tesla", PLAN)]
    selfcheck = [CheckResult("ess_mode", "fail", "ESS-Modus 3, erwartet 1."),
                 CheckResult("dess", "fail", "Dynamic ESS ist aktiv (Modus 1)."),
                 CheckResult("schedules", "unknown", "Ladefenster nicht verfügbar.")]
    assert lock_causes(competitors, selfcheck, limits) == {
        "victron": [DESS, "ESS-Modus 3, erwartet 1.", "Ladefenster nicht verfügbar."],
        "wallbox.evcs": [EVCS],
        "vehicle.tesla": [PLAN],
    }


def test_warn_ok_and_core_checks_do_not_lock(limits) -> None:
    selfcheck = [CheckResult("min_soc", "warn", "Victron-Min-SOC 30 % liegt über der USV-Reserve 20 %."),
                 CheckResult("phases", "fail", "Erwartet 3 Phasen, gemeldet 2."),
                 CheckResult("fresh_data", "fail", "Nicht aktuell: grid.l1.power_w."),
                 CheckResult("batterylife", "ok", "BatteryLife-Zustand 10 wie erwartet.")]
    assert lock_causes([], selfcheck, limits) == {}


def test_non_actuators_are_ignored(limits) -> None:
    egolf = alarm("competitor.vehicle_schedule.egolf", "Ladeplan im Fahrzeug „e-Golf“ ist aktiv.")
    assert lock_causes([egolf], [], limits) == {}


def test_lock_since_first_cause() -> None:
    locks = LockManager(60.0)
    locks.update(T0, {"victron": ["A"]})
    locks.update(S(10), {"victron": ["B"]})
    assert locks.active(S(10)) == {"victron": LockInfo(("B",), T0, None)}


def test_cause_must_be_absent_60s_before_release() -> None:
    locks = LockManager(60.0)
    locks.update(T0, {"victron": ["A"]})
    locks.update(S(10), {})
    assert locks.active(S(69)) == {"victron": LockInfo(("A",), T0, S(10))}
    assert locks.active(S(70)) == {}


def test_returning_cause_restarts_the_release_wait() -> None:
    locks = LockManager(60.0)
    locks.update(T0, {"victron": ["A"]})
    locks.update(S(10), {})
    locks.update(S(40), {"victron": ["A"]})
    locks.update(S(50), {})
    assert "victron" in locks.active(S(105))
    assert locks.active(S(110)) == {}


def test_release_time_is_configurable() -> None:
    locks = LockManager(5.0)
    locks.update(T0, {"victron": ["A"]})
    locks.update(S(1), {})
    assert locks.active(S(6)) == {}
```

```python
# tests/unit/core/safety/test_modes.py
LIVE, DRY = ActuatorMode.LIVE, ActuatorMode.DRY_RUN


def test_without_intent_everything_dry_run(limits) -> None:
    modes = effective_modes(None, limits, watchdog_armed=True)
    assert list(modes) == list(limits.actuators)
    assert set(modes.values()) == {ModeInfo(DRY, DRY, ())}


def test_live_needs_config_permission(limits) -> None:
    intent = Intent(version=1, actuators={"wallbox.evcs": LIVE})
    assert effective_modes(intent, limits, True)["wallbox.evcs"] == ModeInfo(
        LIVE, DRY, ("In config.yaml nicht für den Live-Betrieb freigegeben (live_allowed).",)
    )


def test_live_needs_armed_watchdog_for_victron_and_evcs(limits_live) -> None:
    intent = Intent(version=1, actuators={"wallbox.evcs": LIVE, "victron": LIVE})
    unarmed = effective_modes(intent, limits_live, watchdog_armed=False)
    assert unarmed["victron"] == ModeInfo(LIVE, DRY, ("Watchdog am Cerbo ist nicht scharf.",))
    assert effective_modes(intent, limits_live, watchdog_armed=True)["wallbox.evcs"].effective is LIVE


def test_tesla_live_without_watchdog(limits_live) -> None:
    intent = Intent(version=1, actuators={"vehicle.tesla": LIVE})
    assert effective_modes(intent, limits_live, watchdog_armed=False)["vehicle.tesla"].effective is LIVE


def test_watchdog_required_only_for_victron_and_evcs(limits, limits_live) -> None:
    tesla = Intent(version=1, actuators={"vehicle.tesla": LIVE})
    evcs = Intent(version=1, actuators={"wallbox.evcs": LIVE})
    assert watchdog_required(evcs, limits_live)
    assert not watchdog_required(tesla, limits_live)
    assert not watchdog_required(evcs, limits)
    assert not watchdog_required(None, limits_live)
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/safety/test_locks.py tests/unit/core/safety/test_modes.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: `locks.py` und `modes.py` umsetzen**

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/safety/test_locks.py tests/unit/core/safety/test_modes.py -q`
Expected: `12 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/safety/locks.py src/bindaems/core/safety/modes.py tests/unit/core/safety/test_locks.py tests/unit/core/safety/test_modes.py
git commit -m "feat(safety): Sperren für Teilsysteme und wirksame Betriebsarten je Aktor"
```

---

### Task 9: SafetyLayer und Property-Tests

**Files:**
- Create: `src/bindaems/core/safety/layer.py`
- Test: `tests/unit/core/safety/test_layer.py`, `tests/property/test_safety_layer.py`

**Interfaces:**
- Consumes:
  - aus Task 2: `HardLimits`, `toward_safe`, `SafetyContext`, `Verdict`, `Outcome`, `Finding`
  - aus den Tasks 3–6: `check_range`, `check_phases`, `RateLimiter`, `deadband`, `PersistentWriteBudget`
- Produces, `class SafetyLayer`:
  - `__init__(self, limits: HardLimits, budget: PersistentWriteBudget) -> None`
  - `authorize(self, command: Command, ctx: SafetyContext) -> Verdict`
  - `alarms(self, now: datetime) -> list[Alarm]`: die Alarme des Budgets

**Ablauf von `authorize`**, genau in dieser Reihenfolge:
1. Aktor in `limits.actuators` und Art in `spec.kinds`, sonst `REJECTED`.
2. `urgent_ok = command.urgent and toward_safe(command, ctx, limits)`. Ist `command.urgent` gesetzt, aber nicht `urgent_ok`, kommt `urgent.ignored` dazu, und es geht normal weiter.
3. **Zustand:** `ctx.operating` ist CONTROL oder DEGRADED, oder es gilt `urgent_ok` und der Zustand ist nicht START. Sonst `REJECTED` mit `state.inactive`.
4. **Sperre:** Ist `ctx.locks.get(id)` nicht leer, folgt `REJECTED` mit `lock`, auch bei dringenden Befehlen.
5. **Betriebsart:** `dry_run = ctx.modes.get(id, DRY_RUN) is not LIVE`.
6. **Eingangsdaten:** Ist `ctx.inputs.ok` falsch und gilt nicht `urgent_ok`, folgt `REJECTED` mit `inputs`.
7. **Regeln 1–3:** `check_range(command, limits, ctx.grid_budget_w)`; ein `reject` ergibt `REJECTED`.
8. **Regel 4:** `check_phases(command, value, ctx, limits)`; ein `reject` ergibt `REJECTED`.
9. **Totband:** `deadband(...)` ergibt `UNCHANGED`; bei `urgent_ok` zählt nur `rate.unchanged`.
10. **Rate-Limit:** Ein Finding aus `rate.check(command, ctx.now)` ergibt `DEFERRED`. Bei `urgent_ok` kommt stattdessen `urgent.bypass` dazu, und es geht weiter.
11. **Persistente Arten:** Ein Finding aus `budget.check(ctx.now)` ergibt `REJECTED` und `budget.note_rejection(ctx.now)`. Ausnahme: `urgent_ok` und der Wert ist der Sicherwert der Art aus `limits.safe_commands`.
12. **Erfassen:** `rate.record(command, ctx.now)`, bei persistenten Arten zusätzlich `budget.record(ctx.now)`. Das gilt auch im Dry-Run, aber nie bei `UNCHANGED`, `DEFERRED` oder `REJECTED`.
13. **Ergebnis:** `REDUCED`, wenn Regel 4 den Wert geändert hat; sonst `CLAMPED`, wenn die Regeln 1–3 ihn geändert haben; sonst `ACCEPTED`.

`findings` sammelt alle Findings in der Reihenfolge ihres Entstehens.

| Code | Text |
|---|---|
| `actuator.unknown` | `f"Unbekannter Aktor „{id}“."` |
| `kind.unsupported` | `f"„{label}“ kennt den Befehl „{kind}“ nicht."` |
| `state.inactive` | `f"Regelbetrieb nicht aktiv (Zustand {state})."` |
| `lock` | `f"Gesperrt: {'; '.join(reasons)}"` |
| `inputs` | `f"Eingangsdaten unplausibel: {'; '.join(problems)}"` |
| `urgent.ignored` | `"Als dringend markiert, aber nicht Richtung sicherer Zustand; es gelten die normalen Regeln."` |
| `urgent.bypass` | `"Dringend (Richtung sicherer Zustand): ohne Mindestabstand und Limits."` |

**Property-Tests** (`tests/property/test_safety_layer.py`, hypothesis):
- Jeder Fall bekommt eine frische `SafetyLayer` mit einem Budget in einem eigenen `tempfile.TemporaryDirectory()`; hypothesis verträgt keine funktionsbezogenen Fixtures.
- Konfiguration: `deploy/config.example.yaml`.
- `make_ctx` und `PHASES` kommen aus `tests.unit.core.safety.conftest`, `T0` aus `tests.helpers`.
- Kontexte entstehen über `make_ctx` mit zufälligen Feldern:
  - alle Betriebszustände und Betriebsarten
  - bis zu zwei Sperren, Eingangsdaten ok oder nicht
  - Phasenströme −60…60 A oder `None`, Wallbox-Ströme 0…32 A je Phase
  - Tesla an `twc`, an `evcs` oder an unbekannter Wallbox
  - beliebige `present`-Werte
  - Entladeleistung 0…15 kW oder `None`, SOC 0…100 oder `None`
  - Basis-Sollwert −500…500 W, Budget 0…30 kW oder `None`
- Befehle: alle Arten auf `victron`, `wallbox.evcs`, `vehicle.tesla` und dem Nicht-Aktor `wallbox.twc`. Werte sind `None`, `bool`, −50 000…50 000 sowie −1, NaN und ∞; `urgent` ist zufällig.
- Die Orakel im Test setzen die Tabellen dieses Plans unabhängig um:
  - `in_range` (Tasks 2 und 3)
  - `safer` (`toward_safe`-Tabelle aus Task 2)
  - `is_increase` und `phases_hold` (Erhöhungs-Spalte und Vorhersage aus Task 4)

| Test | Eigenschaft |
|---|---|
| `test_sendable_values_stay_within_hard_limits` | `sendable` ⇒ der Wert liegt im Bereich: Strom ganzzahlig in `[min_a, max_a]`; Netz-Sollwert `None` oder `0 ≤ v ≤ grid_setpoint_max(ctx.grid_budget_w)`; Entladegrenze −1 oder `0 ≤ v ≤ max_discharge_w`; Min-SOC `reserve ≤ v ≤ 100`; `charging` ist `bool` |
| `test_sendable_increases_keep_phases_below_limit` | `sendable` und Erhöhung ⇒ alle betroffenen Phasen bekannt und vorhergesagt ≤ `phase_limit_a` + 1e-6 |
| `test_gate_lock_inputs_and_dry_run` | `sendable` ⇒ nicht gesperrt; Zustand CONTROL oder DEGRADED oder (dringend, sicherer Wert und nicht START); Eingangsdaten ok oder (dringend und sicherer Wert). `send` ⇒ `ctx.modes[id]` ist `LIVE`. Nicht-Aktoren sind nie `sendable`. |
| `test_rate_limits_hold_over_sequences` | Folgen aus Zeitschritten von 0…120 s und nicht dringenden Befehlen (EVCS-Strom 6–16 A, EVCS Start/Stopp, Tesla-Strom 5–16 A, Tesla Start/Stopp, Netz-Sollwert 0–8000 W) in einem großzügigen Kontext (CONTROL, live, Spielraum 33 A je Phase, ohne `present`). Die `sendable`-Zeitpunkte halten alle Mindestabstände ein; beim Tesla gibt es in keinem Fenster von 3600 s mehr als 20 Befehle und mehr als 6 Start/Stopp. |
| `test_budget_holds_over_sequences` | Folgen von Min-SOC- und Entladegrenzen-Befehlen mit Zeitschritten von 0…600 s, bis zu 200 Schritte. Kein Fenster von 1 h enthält mehr als 12 und kein Fenster von 24 h mehr als 100 `sendable` persistente Befehle. |

`@settings(max_examples=500, deadline=None)` für die drei Einzeltests, `max_examples=100` für die Folgen.

- [ ] **Step 1: Failing Unit-Tests schreiben** (`tests/unit/core/safety/test_layer.py`)

```python
K = CommandKind
S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731


@pytest.fixture
def layer(limits, tmp_path: Path) -> SafetyLayer:
    return SafetyLayer(limits, PersistentWriteBudget(12, 100, tmp_path / "budget.json", T0))


def test_unknown_actuator_rejected(layer) -> None:
    v = layer.authorize(Command("wallbox.twc", K.CURRENT, 10.0), make_ctx())
    assert (v.outcome, v.value, v.findings[0].text) == (Outcome.REJECTED, None, "Unbekannter Aktor „wallbox.twc“.")


def test_unsupported_kind_rejected(layer) -> None:
    v = layer.authorize(Command("victron", K.CURRENT, 10.0), make_ctx())
    assert v.findings[0].text == "„Victron (ESS)“ kennt den Befehl „current“ nicht."


def test_observe_rejects_normal_commands(layer) -> None:
    v = layer.authorize(Command("wallbox.evcs", K.CURRENT, 10.0), make_ctx(operating=OperatingState.OBSERVE))
    assert (v.outcome, v.findings[-1].text) == (Outcome.REJECTED, "Regelbetrieb nicht aktiv (Zustand OBSERVE).")


def test_urgent_toward_safe_passes_in_safe_state(layer) -> None:
    ctx = make_ctx(operating=OperatingState.SAFE, present={("wallbox.evcs", K.CURRENT): 16.0})
    v = layer.authorize(Command("wallbox.evcs", K.CURRENT, 6.0, urgent=True), ctx)
    assert (v.outcome, v.value, v.send) == (Outcome.ACCEPTED, 6.0, True)


def test_urgent_without_safer_value_is_checked_normally(layer) -> None:
    ctx = make_ctx(operating=OperatingState.SAFE, present={("wallbox.evcs", K.CURRENT): 6.0})
    v = layer.authorize(Command("wallbox.evcs", K.CURRENT, 16.0, urgent=True), ctx)
    assert (v.outcome, [f.code for f in v.findings]) == (Outcome.REJECTED, ["urgent.ignored", "state.inactive"])


def test_lock_rejects_even_urgent(layer) -> None:
    reason = "EVCS „evcs“ ist nicht im manuellen Modus (Modus 1)."
    ctx = make_ctx(locks={"wallbox.evcs": (reason,)}, present={("wallbox.evcs", K.CURRENT): 16.0})
    v = layer.authorize(Command("wallbox.evcs", K.CURRENT, 6.0, urgent=True), ctx)
    assert (v.outcome, v.findings[-1].text) == (Outcome.REJECTED, f"Gesperrt: {reason}")


def test_dry_run_evaluates_but_does_not_send(layer) -> None:
    v = layer.authorize(Command("wallbox.evcs", K.CURRENT, 10.0), make_ctx(modes={"wallbox.evcs": ActuatorMode.DRY_RUN}))
    assert (v.outcome, v.dry_run, v.sendable, v.send) == (Outcome.ACCEPTED, True, True, False)


def test_inputs_not_ok_rejects_increase_allows_urgent_decrease(layer) -> None:
    ctx = make_ctx(inputs=InputStatus(False, ("Messwert fehlt oder ist veraltet: grid.l1.power_w.",)),
                   present={("wallbox.evcs", K.CURRENT): 10.0})
    up = layer.authorize(Command("wallbox.evcs", K.CURRENT, 12.0), ctx)
    assert up.findings[-1].text == "Eingangsdaten unplausibel: Messwert fehlt oder ist veraltet: grid.l1.power_w."
    assert layer.authorize(Command("wallbox.evcs", K.CURRENT, 6.0, urgent=True), ctx).outcome is Outcome.ACCEPTED


def test_clamp_then_phase_reduction(layer) -> None:
    ctx = make_ctx(phase_import_a={"L1": 30.0, "L2": 20.0, "L3": 10.0},
                   wallbox_phase_a={"evcs": {p: 6.0 for p in PHASES}}, present={("wallbox.evcs", K.CURRENT): 6.0})
    v = layer.authorize(Command("wallbox.evcs", K.CURRENT, 20.0), ctx)
    assert (v.outcome, v.value, [f.code for f in v.findings]) == (Outcome.REDUCED, 9.0, ["range.max", "phase.reduced"])


def test_deadband_gives_unchanged(layer) -> None:
    ctx = make_ctx(present={("victron", K.GRID_SETPOINT): 1000.0})
    v = layer.authorize(Command("victron", K.GRID_SETPOINT, 1030.0), ctx)
    assert (v.outcome, v.send) == (Outcome.UNCHANGED, False)


def test_interval_defers_and_urgent_bypasses(layer) -> None:
    def ctx(at: int, present: float):
        return make_ctx(now=S(at), present={("wallbox.evcs", K.CURRENT): present})

    assert layer.authorize(Command("wallbox.evcs", K.CURRENT, 10.0), ctx(0, 8.0)).outcome is Outcome.ACCEPTED
    assert layer.authorize(Command("wallbox.evcs", K.CURRENT, 12.0), ctx(2, 10.0)).outcome is Outcome.DEFERRED
    urgent = layer.authorize(Command("wallbox.evcs", K.CURRENT, 6.0, urgent=True), ctx(3, 10.0))
    assert (urgent.outcome, urgent.findings[-1].code) == (Outcome.ACCEPTED, "urgent.bypass")


def test_budget_rejects_and_raises_alarm(layer) -> None:
    def at(i: int, present: float = 20.0):
        return make_ctx(now=S(i), present={("victron", K.MIN_SOC): present})

    for i in range(12):
        assert layer.authorize(Command("victron", K.MIN_SOC, 30.0), at(i)).send
    rejected = layer.authorize(Command("victron", K.MIN_SOC, 30.0), at(12))
    assert (rejected.outcome, rejected.findings[-1].code) == (Outcome.REJECTED, "budget.exhausted")
    assert [a.id for a in layer.alarms(S(12))] == ["safety.write_budget"]
    assert layer.authorize(Command("victron", K.MIN_SOC, 20.0, urgent=True), at(13, 30.0)).send


def test_dry_run_counts_for_rate_limits(layer) -> None:
    dry = {"wallbox.evcs": ActuatorMode.DRY_RUN}
    layer.authorize(Command("wallbox.evcs", K.CURRENT, 10.0), make_ctx(modes=dry))
    later = layer.authorize(Command("wallbox.evcs", K.CURRENT, 12.0), make_ctx(now=S(1), modes=dry))
    assert later.outcome is Outcome.DEFERRED


def test_rejected_and_deferred_are_not_recorded(layer) -> None:
    layer.authorize(Command("wallbox.evcs", K.CURRENT, 10.0), make_ctx(operating=OperatingState.OBSERVE))
    assert layer.authorize(Command("wallbox.evcs", K.CURRENT, 10.0), make_ctx()).outcome is Outcome.ACCEPTED
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/safety/test_layer.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: `SafetyLayer` nach dem Ablauf oben umsetzen**

- [ ] **Step 4: Unit-Tests laufen lassen**

Run: `uv run pytest tests/unit/core/safety/test_layer.py -q`
Expected: `14 passed`

- [ ] **Step 5: Property-Tests nach der Tabelle schreiben und laufen lassen**

Run: `uv run pytest tests/property/test_safety_layer.py -q`
Expected: `5 passed`. Findet hypothesis ein Gegenbeispiel, ist das ein Fehler in der Safety-Schicht oder im Orakel. Ursache klären, nie die Eigenschaft abschwächen.

- [ ] **Step 6: Commit**

```bash
git add src/bindaems/core/safety/layer.py tests/unit/core/safety/test_layer.py tests/property/test_safety_layer.py
git commit -m "feat(safety): SafetyLayer mit Property-Tests – kein Befehl verletzt eine harte Grenze"
```

---

### Task 10: Betriebszustände

**Files:**
- Create: `src/bindaems/core/control/__init__.py`, `src/bindaems/core/control/operating.py`
- Test: `tests/unit/core/control/test_operating.py`

**Interfaces:**
- Consumes: `OperatingState` (Task 2)
- Produces:
  - Gründe als Konstanten:

    | Konstante | Text |
    |---|---|
    | `REASON_CONTROL_OFF` | `"Regelbetrieb ist aus."` |
    | `REASON_NOT_FRESH` | `"Kritische Messwerte noch nicht 30 s aktuell."` |
    | `REASON_PHASES` | `"3 Phasen mit je einem Gerät nicht bestätigt."` |
    | `REASON_WATCHDOG` | `"Watchdog am Cerbo ist nicht scharf."` |

  - `@dataclass(frozen=True) class OperatingInputs`: `control_requested: bool`, `data_fresh: bool`, `phases_ok: bool`, `watchdog_required: bool`, `watchdog_armed: bool`, `safe_reasons: tuple[str, ...] = ()`, `degraded_reasons: tuple[str, ...] = ()`
  - `@dataclass(frozen=True) class Transition`: `previous: OperatingState`, `state: OperatingState`, `reasons: tuple[str, ...]`, `ts: datetime`
  - `class OperatingStateMachine`:
    - Properties `state` (Start: START), `reasons` (Start: `()`), `since` (Start: `None`)
    - `step(self, now: datetime, inputs: OperatingInputs) -> Transition | None`: aktualisiert `reasons` immer; liefert nur bei einem Zustandswechsel eine `Transition` und setzt dann `since = now`

**Gate-Gründe** in dieser Reihenfolge: `safe_reasons`, dann `REASON_NOT_FRESH` (ohne `data_fresh`), `REASON_PHASES` (ohne `phases_ok`) und `REASON_WATCHDOG` (`watchdog_required` und nicht `watchdog_armed`). Das Gate ist offen, wenn keiner davon zutrifft.

**Zustandswechsel:**

| Zustand | Bedingung (der Reihe nach) | Neuer Zustand | Gründe |
|---|---|---|---|
| START | immer | OBSERVE | wie OBSERVE unten, aber ohne Weiterschalten |
| OBSERVE, SAFE | Regelbetrieb aus | OBSERVE | `REASON_CONTROL_OFF` |
| OBSERVE | Gate zu | OBSERVE | Gate-Gründe |
| SAFE | Gate zu | SAFE | Gate-Gründe |
| OBSERVE, SAFE | Gate offen | DEGRADED bei `degraded_reasons`, sonst CONTROL | `degraded_reasons` bzw. `()` |
| CONTROL, DEGRADED | Regelbetrieb aus | OBSERVE | `REASON_CONTROL_OFF` |
| CONTROL, DEGRADED | `safe_reasons` oder nicht `phases_ok` | SAFE | `safe_reasons` + `REASON_PHASES` (falls zutreffend) |
| CONTROL, DEGRADED | sonst | DEGRADED, wenn Gründe; sonst CONTROL | `degraded_reasons` + `REASON_WATCHDOG` (falls benötigt und nicht scharf) |

- [ ] **Step 1: Failing Tests schreiben**

```python
S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731
O = OperatingState


def inputs(**changes: Any) -> OperatingInputs:
    base = {"control_requested": True, "data_fresh": True, "phases_ok": True,
            "watchdog_required": False, "watchdog_armed": False}
    return OperatingInputs(**(base | changes))


def in_control() -> OperatingStateMachine:
    machine = OperatingStateMachine()
    machine.step(T0, inputs())
    machine.step(S(1), inputs())
    return machine


def test_first_step_leaves_start() -> None:
    machine = OperatingStateMachine()
    assert (machine.state, machine.since) == (O.START, None)
    assert machine.step(T0, inputs(control_requested=False)) == Transition(O.START, O.OBSERVE, (REASON_CONTROL_OFF,), T0)


def test_control_after_gate() -> None:
    machine = OperatingStateMachine()
    machine.step(T0, inputs())
    assert machine.step(S(1), inputs()) == Transition(O.OBSERVE, O.CONTROL, (), S(1))


def test_gate_reasons_in_order() -> None:
    machine = OperatingStateMachine()
    machine.step(T0, inputs())
    machine.step(S(1), inputs(data_fresh=False, phases_ok=False, watchdog_required=True,
                              safe_reasons=("Netzausfall (Inselbetrieb).",)))
    assert (machine.state, machine.reasons) == (
        O.OBSERVE, ("Netzausfall (Inselbetrieb).", REASON_NOT_FRESH, REASON_PHASES, REASON_WATCHDOG),
    )


def test_degraded_reasons() -> None:
    machine = OperatingStateMachine()
    machine.step(T0, inputs())
    t = machine.step(S(1), inputs(degraded_reasons=("EVCS „evcs“ nicht erreichbar.",)))
    assert (t.state, t.reasons) == (O.DEGRADED, ("EVCS „evcs“ nicht erreichbar.",))


def test_watchdog_loss_degrades() -> None:
    t = in_control().step(S(2), inputs(watchdog_required=True))
    assert (t.state, t.reasons) == (O.DEGRADED, (REASON_WATCHDOG,))


def test_safe_and_recovery_need_fresh_data() -> None:
    machine = in_control()
    assert machine.step(S(2), inputs(safe_reasons=("Netzausfall (Inselbetrieb).",))).state is O.SAFE
    assert machine.step(S(3), inputs(data_fresh=False)) is None
    assert machine.reasons == (REASON_NOT_FRESH,)
    assert machine.step(S(4), inputs()).state is O.CONTROL


def test_phase_mismatch_in_control_goes_safe() -> None:
    t = in_control().step(S(2), inputs(phases_ok=False))
    assert (t.state, t.reasons) == (O.SAFE, (REASON_PHASES,))


@pytest.mark.parametrize("trigger", [{}, {"degraded_reasons": ("x",)}, {"safe_reasons": ("x",)}])
def test_switch_off_goes_to_observe(trigger: dict[str, Any]) -> None:
    machine = in_control()
    machine.step(S(2), inputs(**trigger))
    t = machine.step(S(3), inputs(control_requested=False))
    assert (t.state, t.reasons) == (O.OBSERVE, (REASON_CONTROL_OFF,))


def test_reasons_update_without_transition() -> None:
    machine = OperatingStateMachine()
    machine.step(T0, inputs())
    machine.step(S(1), inputs(degraded_reasons=("A",)))
    assert machine.step(S(2), inputs(degraded_reasons=("B",))) is None
    assert machine.reasons == ("B",)
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_operating.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: `OperatingStateMachine` nach der Tabelle umsetzen**

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_operating.py -q`
Expected: `11 passed`

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control tests/unit/core/control
git commit -m "feat(control): Betriebszustände START, OBSERVE, CONTROL, DEGRADED und SAFE"
```

---

### Task 11: Absicht – Prüfung, Speicherung und API

**Files:**
- Create: `src/bindaems/core/control/intent.py`
- Modify: `src/bindaems/core/api/app.py`
- Test: `tests/unit/core/control/test_intent.py`, `tests/unit/core/api/test_api.py`

**Interfaces:**
- Consumes: `Intent`, `ActuatorMode` (Task 2), `HardLimits` (Task 2), `write_json_atomic`, `read_json` (Task 6)
- Produces in `intent.py`:
  - `INTENT_FILE_PROBLEM = "Gespeicherte Absicht unlesbar; der core startet ohne Absicht (Regelbetrieb aus)."`
  - `@dataclass(frozen=True) class IntentReply`: `result: Literal["accepted", "clamped", "rejected"]`, `version: int | None` (die danach gültige Version), `details: tuple[str, ...] = ()`, `rejection: Literal["stale", "unknown_actuator"] | None = None`
  - `class IntentStore`:
    - `__init__(self, path: Path, limits: HardLimits) -> None`
    - Properties `current: Intent | None` und `load_problem: str | None`
    - `load(self) -> None`: Datei fehlt → keine Absicht. Datei unlesbar oder ungültig → keine Absicht, `load_problem = INTENT_FILE_PROBLEM`, Log-Warnung.
    - `submit(self, intent: Intent, now: datetime) -> IntentReply`, Regeln der Reihe nach:

      | Fall | Ergebnis | Details |
      |---|---|---|
      | unbekannte Aktor-IDs | rejected, `unknown_actuator`; Version unverändert | je ID, sortiert: `f"Unbekannter Aktor „{id}“."` |
      | Version < gültige | rejected, `stale` | `f"Version {v} ist älter als die gültige Version {c}."` |
      | Version = gültige, gleicher Inhalt | accepted, keine neue Speicherung | – |
      | Version = gültige, anderer Inhalt | rejected, `stale` | `f"Version {v} ist bereits mit anderem Inhalt angenommen."` |
      | live ohne `live_allowed` | clamped: der Aktor wird auf `dry_run` gesetzt | `f"{label}: in config.yaml nicht für den Live-Betrieb freigegeben; bleibt im Dry-Run."` |
      | sonst | accepted | – |

    - Angenommene und geklemmte Absichten werden zur gültigen Absicht und als `{"intent": <JSON>, "accepted_at": <ISO>}` gespeichert; danach ist `load_problem` gleich `None`.
    - Scheitert das Speichern, gilt die Absicht trotzdem im Speicher. Dazu kommt `f"Absicht nicht gespeichert: {exc}"`, und der Fehler wird geloggt.
- Produces in `api/app.py`:
  - `CoreView` bekommt `intent(self) -> Intent | None` und `submit_intent(self, intent: Intent) -> IntentReply`.
  - `GET /v1/intent` liefert `{"intent": <JSON> | null}`.
  - `PUT /v1/intent` erwartet als Body eine `Intent`. Die Antwort ist `{"result", "version", "details"}` mit Status 200 (accepted, clamped), 409 (`stale`) oder 422 (`unknown_actuator`).
  - Beide Endpunkte verlangen das Token wie `/v1/health`.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_intent.py
LIVE, DRY = ActuatorMode.LIVE, ActuatorMode.DRY_RUN


def make_store(tmp_path: Path, limits: HardLimits) -> IntentStore:
    store = IntentStore(tmp_path / "intent.json", limits)
    store.load()
    return store


def test_first_intent_accepted_and_saved(tmp_path, limits_live) -> None:
    store = make_store(tmp_path, limits_live)
    reply = store.submit(Intent(version=5, control=True, actuators={"wallbox.evcs": LIVE}), T0)
    assert reply == IntentReply("accepted", 5)
    assert read_json(tmp_path / "intent.json") == {
        "intent": {"version": 5, "control": True, "actuators": {"wallbox.evcs": "live"}},
        "accepted_at": T0.isoformat(),
    }


def test_live_without_permission_is_clamped(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    reply = store.submit(Intent(version=1, actuators={"wallbox.evcs": LIVE}), T0)
    assert reply == IntentReply("clamped", 1, (
        "EVCS „evcs“: in config.yaml nicht für den Live-Betrieb freigegeben; bleibt im Dry-Run.",
    ))
    assert store.current.actuators == {"wallbox.evcs": DRY}


def test_unknown_actuator_rejected(tmp_path, limits) -> None:
    reply = make_store(tmp_path, limits).submit(Intent(version=1, actuators={"wallbox.twc": LIVE}), T0)
    assert reply == IntentReply("rejected", None, ("Unbekannter Aktor „wallbox.twc“.",), "unknown_actuator")


def test_older_version_rejected(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    store.submit(Intent(version=5), T0)
    assert store.submit(Intent(version=4, control=True), T0) == IntentReply(
        "rejected", 5, ("Version 4 ist älter als die gültige Version 5.",), "stale"
    )


def test_same_version_same_content_accepted(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    store.submit(Intent(version=5, control=True), T0)
    assert store.submit(Intent(version=5, control=True), T0) == IntentReply("accepted", 5)


def test_same_version_other_content_rejected(tmp_path, limits) -> None:
    store = make_store(tmp_path, limits)
    store.submit(Intent(version=5, control=True), T0)
    assert store.submit(Intent(version=5), T0) == IntentReply(
        "rejected", 5, ("Version 5 ist bereits mit anderem Inhalt angenommen.",), "stale"
    )


def test_saved_intent_reloaded(tmp_path, limits) -> None:
    make_store(tmp_path, limits).submit(Intent(version=7, control=True), T0)
    again = make_store(tmp_path, limits)
    assert (again.current, again.load_problem) == (Intent(version=7, control=True), None)


def test_corrupt_file_starts_without_intent(tmp_path, limits) -> None:
    (tmp_path / "intent.json").write_text("{kaputt")
    store = make_store(tmp_path, limits)
    assert (store.current, store.load_problem) == (None, INTENT_FILE_PROBLEM)
    store.submit(Intent(version=1), T0)
    assert store.load_problem is None


def test_save_failure_keeps_intent_in_memory(tmp_path, limits, monkeypatch) -> None:
    monkeypatch.setattr("bindaems.core.control.intent.write_json_atomic", Mock(side_effect=OSError("Platte voll")))
    store = make_store(tmp_path, limits)
    assert store.submit(Intent(version=2, control=True), T0) == IntentReply(
        "accepted", 2, ("Absicht nicht gespeichert: Platte voll",)
    )
    assert store.current == Intent(version=2, control=True)
```

In `tests/unit/core/api/test_api.py` bekommt `FakeView` die Attribute `current: Intent | None`, `reply: IntentReply` und `submitted: list[Intent]` sowie die beiden Methoden. Eine neue Fixture `view_client` liefert `(TestClient, FakeView)`.

```python
def test_put_intent_accepted(view_client) -> None:
    client, view = view_client
    view.reply = IntentReply("accepted", 3)
    r = client.put("/v1/intent", json={"version": 3, "control": True}, headers=H)
    assert (r.status_code, r.json()) == (200, {"result": "accepted", "version": 3, "details": []})
    assert view.submitted == [Intent(version=3, control=True)]


def test_put_intent_stale_is_409(view_client) -> None:
    client, view = view_client
    view.reply = IntentReply("rejected", 5, ("Version 4 ist älter als die gültige Version 5.",), "stale")
    assert client.put("/v1/intent", json={"version": 4}, headers=H).status_code == 409


def test_put_intent_unknown_actuator_is_422(view_client) -> None:
    client, view = view_client
    view.reply = IntentReply("rejected", None, ("Unbekannter Aktor „wallbox.twc“.",), "unknown_actuator")
    r = client.put("/v1/intent", json={"version": 1, "actuators": {"wallbox.twc": "live"}}, headers=H)
    assert (r.status_code, r.json()["details"]) == (422, ["Unbekannter Aktor „wallbox.twc“."])


def test_get_intent(view_client) -> None:
    client, view = view_client
    view.current = Intent(version=3)
    assert client.get("/v1/intent").status_code == 401
    assert client.get("/v1/intent", headers=H).json() == {
        "intent": {"version": 3, "control": False, "actuators": {}}
    }
```

`H` ist der Bearer-Header, den die bestehenden API-Tests schon nutzen.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_intent.py tests/unit/core/api/test_api.py -q`
Expected: FAIL (`ModuleNotFoundError` bzw. 404 für `/v1/intent`)

- [ ] **Step 3: `IntentStore` und die Endpunkte umsetzen**

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_intent.py tests/unit/core/api/test_api.py -q`
Expected: PASS (13 neue Tests, die bisherigen API-Tests unverändert grün)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control/intent.py src/bindaems/core/api/app.py tests/unit/core/control/test_intent.py tests/unit/core/api/test_api.py
git commit -m "feat(control): Absicht mit Version, Klemmung auf live_allowed und PUT/GET /v1/intent"
```

---

### Task 12: Watchdog-Client – Status lesen, Heartbeat senden

**Files:**
- Modify: `src/bindaems/core/adapters/victron_topics.py`, `src/bindaems/core/adapters/victron_mqtt.py`
- Create: `src/bindaems/core/watchdog_client/__init__.py`, `src/bindaems/core/watchdog_client/status.py`, `src/bindaems/core/watchdog_client/heartbeat.py`
- Test: `tests/unit/core/adapters/test_victron_topics.py`, `tests/unit/core/adapters/test_victron_mqtt.py`, `tests/unit/core/watchdog_client/test_status.py`, `tests/unit/core/watchdog_client/test_heartbeat.py`

**Interfaces:**
- Consumes: `Snapshot` (`shared/domain.py`), `MqttTransport`, `FakeTransport` (bestehend)
- Produces in `victron_topics.py`:
  - `InstanceResolver`: `bindaems` zählt zu den Diensten mit Instanz 0, wie `system`, `settings` und `hub4`.
  - `ROUTES["bindaems"]`: alle Signale sind `STATE`.

    | Pfad | Signal |
    |---|---|
    | `State` | `watchdog.state` |
    | `TripCount` | `watchdog.trip_count` |
    | `LastTripTime` | `watchdog.last_trip_time` (Unix-Sekunden, 0 = nie) |
    | `LastTripFailures` | `watchdog.last_trip_failures` |
    | `TimeoutSeconds` | `watchdog.timeout_s` |
    | `Version` | `watchdog.version` |
    | `Heartbeat` | `watchdog.heartbeat` |

- Produces in `victron_mqtt.py`:
  - `SUBSCRIPTIONS` bekommt `"bindaems/0/#"`.
  - `HEARTBEAT_PATH = "bindaems/0/Heartbeat"`, `WRITABLE_PATHS: frozenset[str] = frozenset({HEARTBEAT_PATH})`, `OUTBOX_SIZE = 100`
  - `VictronMqttAdapter.write(self, path: str, value: float | int | None) -> bool`:
    - Ein Pfad außerhalb von `WRITABLE_PATHS` → `ValueError(f"Schreibpfad nicht freigegeben: {path}")`.
    - Ohne laufende Sitzung → `False`.
    - Sonst stellt sie `(f"W/{portal}/{path}", json.dumps({"value": value}).encode())` in die Warteschlange der Sitzung und liefert `True`; ist die Warteschlange voll, liefert sie `False`.
  - Die Sitzung startet neben dem Keepalive einen Sender-Task, der die Warteschlange abarbeitet. Am Ende der Sitzung verfällt die Warteschlange.
- Produces in `watchdog_client/status.py`:
  - `WATCHDOG_VERSION = "1.0.0"`: muss `VERSION` in `cerbo-watchdog/bindaems_watchdog/__init__.py` entsprechen (Task 14 prüft das)
  - `class WatchdogState(StrEnum)`: `WAITING = "waiting"`, `ARMED = "armed"`, `TRIPPED = "tripped"`, `DISABLED = "disabled"`, `UNKNOWN = "unknown"`
  - `@dataclass(frozen=True) class WatchdogStatus`:
    - Felder: `state: WatchdogState`, `trip_count: int | None`, `last_trip: datetime | None`, `last_trip_failures: int | None`, `version: str | None`
    - Property `armed` (gleich `state is ARMED`), Klassenmethode `unknown() -> WatchdogStatus`
  - `read_watchdog(snap: Snapshot) -> WatchdogStatus`:
    - Nur `OK`-Werte zählen.
    - `watchdog.state` 0–3 → WAITING, ARMED, TRIPPED bzw. DISABLED; sonst UNKNOWN.
    - `last_trip_time` > 0 → UTC-Zeit, sonst `None`.
  - `watchdog_alarms(status: WatchdogStatus, required: bool, now: datetime) -> list[Alarm]`:

    | ID | Schwere | Bedingung | Text | `since` |
    |---|---|---|---|---|
    | `watchdog.tripped` | ERROR | Zustand TRIPPED | `"Der Watchdog am Cerbo hat ausgelöst und den sicheren Zustand hergestellt."`, bei Fehlschlägen ergänzt um `f" Fehlgeschlagene Aktionen: {n}."` | `last_trip`, sonst `now` |
    | `watchdog.unavailable` | ERROR | `required` und Zustand UNKNOWN | `"Watchdog am Cerbo antwortet nicht; Victron und EVCS bleiben im Dry-Run."` | `now` |
    | `watchdog.unavailable` | ERROR | `required` und Zustand DISABLED | `"Watchdog am Cerbo ist deaktiviert; Victron und EVCS bleiben im Dry-Run."` | `now` |
    | `watchdog.version` | WARNING | Version bekannt und ≠ `WATCHDOG_VERSION` | `f"Watchdog-Version {v} am Cerbo, erwartet {WATCHDOG_VERSION}; bitte mit install.sh aktualisieren."` | `now` |

- Produces in `watchdog_client/heartbeat.py`:
  - `HEARTBEAT_MAX = 2**31 - 1`, `HeartbeatPhase = Literal["off", "active", "released"]`
  - `class HeartbeatClient`:
    - `__init__(self, send: Callable[[int], bool], interval_s: float, *, first: int = 1) -> None`
    - Properties `status: HeartbeatPhase` (Start: `"off"`) und `last_sent: datetime | None`
    - `on_cycle(self, now: datetime, *, required: bool) -> None`:
      - Mit `required`: Ist noch nichts gesendet oder sind seit `last_sent` mindestens `interval_s − 0.05` s vergangen, wird der Zähler gesendet. Erst bei Erfolg folgen `last_sent = now`, `status = "active"` und der nächste Zähler; nach `HEARTBEAT_MAX` geht es mit 1 weiter.
      - Ohne `required`, aber bei Status `"active"`: Die 0 wird gesendet. Erst bei Erfolg folgen `status = "released"` und `last_sent = None`; schlägt das Senden fehl, folgt der nächste Versuch im nächsten Zyklus.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/adapters/test_victron_topics.py (ergänzen)
WATCHDOG = [("State", "watchdog.state", 1), ("TripCount", "watchdog.trip_count", 2),
            ("LastTripTime", "watchdog.last_trip_time", 1791600000),
            ("LastTripFailures", "watchdog.last_trip_failures", 0), ("TimeoutSeconds", "watchdog.timeout_s", 90),
            ("Version", "watchdog.version", "1.0.0"), ("Heartbeat", "watchdog.heartbeat", 17)]


@pytest.mark.parametrize(("path", "signal", "value"), WATCHDOG)
def test_watchdog_topics(cfg, path, signal, value) -> None:
    payload = json.dumps({"value": value}).encode()
    resolver = InstanceResolver(cfg.victron.instances)
    assert parse_message(f"N/{P}/bindaems/0/{path}", payload, P, resolver) == [
        SignalUpdate(signal, value, SignalKind.STATE)
    ]


def test_watchdog_only_instance_zero(cfg) -> None:
    resolver = InstanceResolver(cfg.victron.instances)
    assert parse_message(f"N/{P}/bindaems/1/State", b'{"value": 1}', P, resolver) == []
```

```python
# tests/unit/core/adapters/test_victron_mqtt.py: test_publishes_only_keepalive_topic wird ersetzt
async def test_publishes_only_keepalive_and_allowed_writes(fake_env) -> None:
    adapter, transports, _ = fake_env(keepalive_s=0.02, messages=[lambda: adapter.write(HEARTBEAT_PATH, 3)])
    await run_until(adapter, lambda: bool(transports) and len(transports[0].published) >= 3)
    allowed = {f"R/{P}/keepalive"} | {f"W/{P}/{path}" for path in WRITABLE_PATHS}
    assert {topic for topic, _ in transports[0].published} <= allowed


async def test_write_publishes_json_value_when_connected(fake_env) -> None:
    results: list[bool] = []
    adapter, transports, _ = fake_env(messages=[lambda: results.append(adapter.write(HEARTBEAT_PATH, 7))])
    await run_until(adapter, lambda: (f"W/{P}/bindaems/0/Heartbeat", b'{"value": 7}') in transports[0].published)
    assert results == [True]


def test_write_returns_false_without_session(fake_env) -> None:
    adapter, _, _ = fake_env()
    assert adapter.write(HEARTBEAT_PATH, 1) is False


def test_write_rejects_other_paths(fake_env) -> None:
    adapter, _, _ = fake_env()
    with pytest.raises(ValueError, match="Schreibpfad nicht freigegeben: hub4/0/Overrides/Setpoint"):
        adapter.write("hub4/0/Overrides/Setpoint", 0)


async def test_subscribes_watchdog_topics(fake_env) -> None:
    adapter, transports, _ = fake_env()
    # der erste Keepalive folgt erst nach allen Abonnements
    await run_until(adapter, lambda: bool(transports) and bool(transports[0].published))
    assert f"N/{P}/bindaems/0/#" in transports[0].subscribed
```

```python
# tests/unit/core/watchdog_client/test_status.py
STATE = SignalKind.STATE
W = WatchdogState


@pytest.mark.parametrize(("value", "state"), [(0, W.WAITING), (1, W.ARMED), (2, W.TRIPPED), (3, W.DISABLED), (7, W.UNKNOWN)])
def test_read_states(value: int, state: WatchdogState) -> None:
    assert read_watchdog(snap({"watchdog.state": value}, kind=STATE)).state is state


def test_missing_or_stale_state_is_unknown() -> None:
    assert read_watchdog(snap({}, kind=STATE)).state is W.UNKNOWN
    assert read_watchdog(snap({"watchdog.state": 1}, kind=STATE, quality=Quality.STALE)).state is W.UNKNOWN


def test_read_details() -> None:
    values = {"watchdog.state": 2, "watchdog.trip_count": 2, "watchdog.last_trip_time": 1791600000,
              "watchdog.last_trip_failures": 1, "watchdog.version": "1.0.0"}
    assert read_watchdog(snap(values, kind=STATE)) == WatchdogStatus(
        W.TRIPPED, 2, datetime.fromtimestamp(1791600000, UTC), 1, "1.0.0"
    )
    assert read_watchdog(snap({"watchdog.last_trip_time": 0}, kind=STATE)).last_trip is None


def test_alarm_when_tripped() -> None:
    last = datetime.fromtimestamp(1791600000, UTC)
    status = WatchdogStatus(W.TRIPPED, 3, last, 2, "1.0.0")
    assert watchdog_alarms(status, required=False, now=T0) == [Alarm(
        "watchdog.tripped", Severity.ERROR,
        "Der Watchdog am Cerbo hat ausgelöst und den sicheren Zustand hergestellt. Fehlgeschlagene Aktionen: 2.", last,
    )]


def test_unavailable_only_when_required() -> None:
    unknown = WatchdogStatus.unknown()
    assert watchdog_alarms(unknown, required=False, now=T0) == []
    assert watchdog_alarms(unknown, required=True, now=T0)[0].message == (
        "Watchdog am Cerbo antwortet nicht; Victron und EVCS bleiben im Dry-Run."
    )
    disabled = WatchdogStatus(W.DISABLED, 0, None, 0, "1.0.0")
    assert watchdog_alarms(disabled, required=True, now=T0)[0].message == (
        "Watchdog am Cerbo ist deaktiviert; Victron und EVCS bleiben im Dry-Run."
    )
    assert watchdog_alarms(WatchdogStatus(W.WAITING, 0, None, 0, "1.0.0"), required=True, now=T0) == []


def test_version_mismatch_warns() -> None:
    status = WatchdogStatus(W.ARMED, 0, None, 0, "0.9.0")
    assert watchdog_alarms(status, required=False, now=T0) == [Alarm(
        "watchdog.version", Severity.WARNING,
        "Watchdog-Version 0.9.0 am Cerbo, erwartet 1.0.0; bitte mit install.sh aktualisieren.", T0,
    )]
```

```python
# tests/unit/core/watchdog_client/test_heartbeat.py
S = lambda s: T0 + timedelta(seconds=s)  # noqa: E731


def recorder(results: Iterable[bool] = ()) -> tuple[list[int], Callable[[int], bool]]:
    sent: list[int] = []
    answers = iter(results)

    def send(value: int) -> bool:
        sent.append(value)
        return next(answers, True)

    return sent, send


def test_sends_every_interval_when_required() -> None:
    sent, send = recorder()
    client = HeartbeatClient(send, 10.0)
    for s in range(21):
        client.on_cycle(S(s), required=True)
    assert (sent, client.status) == ([1, 2, 3], "active")


def test_nothing_when_not_required() -> None:
    sent, send = recorder()
    client = HeartbeatClient(send, 10.0)
    client.on_cycle(T0, required=False)
    assert (sent, client.status) == ([], "off")


def test_release_once_when_no_longer_required() -> None:
    sent, send = recorder()
    client = HeartbeatClient(send, 10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(S(1), required=False)
    client.on_cycle(S(2), required=False)
    assert (sent, client.status, client.last_sent) == ([1, 0], "released", None)


def test_failed_send_retried_next_cycle() -> None:
    sent, send = recorder([False, True])
    client = HeartbeatClient(send, 10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(S(1), required=True)
    assert (sent, client.last_sent) == ([1, 1], S(1))


def test_counter_wraps_to_one() -> None:
    sent, send = recorder()
    client = HeartbeatClient(send, 10.0, first=HEARTBEAT_MAX)
    client.on_cycle(T0, required=True)
    client.on_cycle(S(10), required=True)
    assert sent == [HEARTBEAT_MAX, 1]
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/adapters tests/unit/core/watchdog_client -q`
Expected: FAIL (Importfehler für `HEARTBEAT_PATH` und `watchdog_client`)

- [ ] **Step 3: Umsetzen wie unter „Interfaces“ beschrieben**

Dazu den Docstring von `victron_mqtt.py` anpassen: Publiziert werden der Keepalive und die Pfade aus `WRITABLE_PATHS`.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/adapters tests/unit/core/watchdog_client -q`
Expected: PASS (28 neue bzw. umgestellte Tests, alle bisherigen grün)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/adapters src/bindaems/core/watchdog_client tests/unit/core/adapters tests/unit/core/watchdog_client
git commit -m "feat(core): Watchdog-Status lesen und Heartbeat an den Cerbo senden"
```

---

### Task 13: Laufzeit – Zustände, Absicht, Sperren und Heartbeat im Zyklus

**Files:**
- Modify: `src/bindaems/core/runtime.py`, `src/bindaems/core/api/app.py`, `src/bindaems/core/state/store.py`, `tests/integration/conftest.py`, `tests/ui_world.py`
- Test: `tests/integration/test_runtime_control.py`, `tests/unit/core/state/test_store.py`

**Interfaces:**
- Consumes: Tasks 2 und 6–12, `CRITICAL_SIGNALS` aus `core/checks/selfcheck.py`, `number` aus `core/checks/values.py`
- Produces in `store.py`: `StateStore.connected(self, source: str) -> bool | None`; `None` für unbekannte Quellen
- Produces in `api/app.py`: neue Felder in `HealthReport`; ihre Defaults halten bestehende Aufrufe gültig.

  | Feld | Typ | Inhalt |
  |---|---|---|
  | `mode` | `Literal["START", "OBSERVE", "CONTROL", "DEGRADED", "SAFE"]` | Betriebszustand |
  | `mode_since` | `str \| None = None` | ISO-Zeit des letzten Wechsels |
  | `mode_reasons` | `list[str] = []` | Gründe des Zustands |
  | `intent` | `dict[str, Any] \| None = None` | gültige Absicht als JSON |
  | `actuators` | `list[dict[str, Any]] = []` | je Aktor in Katalogreihenfolge `{"id", "label", "requested", "effective", "live_allowed", "reasons", "locked"}`; `locked` sind die Sperrgründe |
  | `watchdog` | `dict[str, Any]` | `{"state", "trip_count", "last_trip", "last_trip_failures", "version", "expected_version", "heartbeat", "required"}`; Default: Zustand `unknown`, Heartbeat `off`, `required` `False`, sonst `None` bzw. `WATCHDOG_VERSION` |
  | `inputs` | `dict[str, Any]` | `{"ok", "problems"}`; Default `{"ok": True, "problems": []}` |

- Produces in `runtime.py`:
  - **Konstruktor:** neuer Parameter `watchdog_send: Callable[[int], bool] | None = None`. Ohne ihn schreibt der Victron-Adapter aus `self.adapters` mit `write(HEARTBEAT_PATH, n)`; ohne Victron-Adapter ist das Ergebnis `False`.
  - **Neue öffentliche Attribute:**
    - `limits` (`HardLimits`)
    - `intents`: `IntentStore(cfg.core.data_dir / "intent.json", limits)`, wird im Konstruktor geladen
    - `locks`: `LockManager(cfg.safety.lock_release_s)`
    - `operating` (`OperatingStateMachine`)
    - `heartbeat`: `HeartbeatClient(send, cfg.victron.watchdog.heartbeat_s)`
  - **`CoreView`:**
    - `intent()` liefert die gültige Absicht.
    - `submit_intent(intent)` ruft `intents.submit(intent, clock.now())` auf.
    - `health()` füllt die neuen Felder; `mode` ist `operating.state`.
  - **Zusätzliche Schritte in `cycle_once`**, in dieser Reihenfolge nach den bisherigen Prüfungen:
    1. `inputs.evaluate(snap, plausibility, now)`
    2. In Zyklen, in denen die Prüfungen liefen: `locks.update(now, lock_causes(competitor_alarms, selfcheck, limits))`
    3. `read_watchdog(snap)`, `watchdog_required(intent, limits)`, `effective_modes(...)`, `locks.active(now)`
    4. `operating.step(now, OperatingInputs(...))`. Bei einem Wechsel:
       - Stream-Meldung `{"type": "mode_change", "data": {"mode", "previous", "reasons", "ts"}}`
       - `log.info("Betriebszustand gewechselt", …)`
    5. bisherige `state`-Meldung und Zyklusmessung
    6. Alarme aktualisieren: bisherige plus `watchdog_alarms(...)`, `core.safe` und `intent.file`
    7. als Letztes `heartbeat.on_cycle(now, required=…)`; nur ein Zyklus ohne Ausnahme kommt bis hierher
  - **`OperatingInputs` aus dem Zyklus:**
    - `control_requested`: gültige Absicht mit `control`
    - `data_fresh`: Für alle `CRITICAL_SIGNALS` gilt `store.ok_since(s)` ≤ `now − 30 s`.
    - `phases_ok`: `number(snap, "vebus.phases") == cfg.grid.phases`
    - `watchdog_required` und `watchdog_armed` wie in Schritt 3
    - `safe_reasons`:
      - `f"Cerbo-Daten veraltet: {', '.join(names)}."` für alle `CRITICAL_SIGNALS` ohne Qualität `OK`, in Listenreihenfolge
      - `"Netzausfall (Inselbetrieb)."`, wenn `system.active_in_source` gleich 240 ist
    - `degraded_reasons`, in dieser Reihenfolge:
      - je Wallbox mit `store.connected(<schlüssel>) is False`: `f"EVCS „{key}“ nicht erreichbar."` bzw. `f"Wall Connector „{key}“ nicht erreichbar."`
      - je Fahrzeug mit Tessie und `store.connected(f"tessie:{key}") is False`: `f"Tessie für „{vehicle.name}“ nicht erreichbar."`
      - je aktive Sperre: `f"{label} gesperrt: {'; '.join(reasons)}"`
  - **Neue Alarme:**
    - `core.safe` (ERROR): `f"Sicherer Zustand: {'; '.join(reasons)}"`, solange SAFE gilt; `since` = `mode_since`
    - `intent.file` (WARNING): `INTENT_FILE_PROBLEM`, solange `intents.load_problem` gesetzt ist
  - **Stabiles `since`:** `_update_alarms` übernimmt für jede Alarm-ID, die schon in der vorigen Liste stand, deren `since`. Das gilt auch für die bisherigen Alarme mitregelnder Systeme.
  - **`shutdown()`:** bleibt, wie es ist; es sendet keine Freigabe.
- Test-Infrastruktur:
  - `runtime_env` in `tests/integration/conftest.py` setzt `core.data_dir` auf `tmp_path / "core"`.
  - Neuer Parameter `configure: Callable[[Config], Config] | None` passt die Konfiguration an; weitere Schlüsselwortargumente gehen wie bisher an `CoreRuntime`.
  - `tests/ui_world.py` setzt `core.data_dir` unter das Datenverzeichnis der Demo.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/state/test_store.py (ergänzen)
def test_connected_reports_sources(clock) -> None:
    store = StateStore(clock)
    assert store.connected("evcs") is None
    store.register_source("evcs", 5.0)
    assert store.connected("evcs") is False
    store.set_connected("evcs", True)
    assert store.connected("evcs") is True
```

```python
# tests/integration/test_runtime_control.py
GOOD_STATE = {"vebus.phases": 3, "ess.hub4_mode": 1, "ess.batterylife_state": 10, "dess.mode": 0,
              "ess.schedule.0.day": -7, "ess.min_soc_pct": 20, "wallbox.evcs.gx_mode": 0}
DESS = "Dynamic ESS ist aktiv (Modus 1). Das EMS darf Victron nicht steuern."


def run(rt, clock, seconds: int) -> None:
    for _ in range(seconds):
        rt.cycle_once()
        clock.advance(1)


def record_broadcast(rt) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    publish = rt.broadcast.publish

    def both(message: dict[str, Any]) -> None:
        messages.append(message)
        publish(message)

    rt.broadcast.publish = both
    return messages


def allow_evcs_live(cfg: Config) -> Config:
    evcs = cfg.wallboxes["evcs"].model_copy(update={"live_allowed": True})
    return cfg.model_copy(update={"wallboxes": cfg.wallboxes | {"evcs": evcs}})


async def test_starts_in_start_then_observe(runtime_env) -> None:
    rt, _, _ = runtime_env()
    assert rt.health().mode == "START"
    rt.cycle_once()
    assert (rt.health().mode, rt.health().mode_reasons) == ("OBSERVE", ["Regelbetrieb ist aus."])


async def test_control_after_30s_fresh_data(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE)
    messages = record_broadcast(rt)
    assert rt.submit_intent(Intent(version=1, control=True)).result == "accepted"
    run(rt, clock, 32)
    assert rt.health().mode == "CONTROL"
    assert [m["data"]["mode"] for m in messages if m["type"] == "mode_change"] == ["OBSERVE", "CONTROL"]


async def test_dess_locks_victron_but_control_runs(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE | {"dess.mode": 1})
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 32)
    health = rt.health()
    assert health.mode == "DEGRADED"
    assert f"Victron (ESS) gesperrt: {DESS}" in health.mode_reasons
    locked = {a["id"]: a["locked"] for a in health.actuators}
    assert locked["victron"] == [DESS] and locked["wallbox.evcs"] == []


async def test_safe_when_cerbo_data_stale(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 32)
    rt.store.set_connected("victron", False)
    rt.cycle_once()
    health = rt.health()
    assert health.mode == "SAFE"
    assert health.mode_reasons[0].startswith("Cerbo-Daten veraltet: grid.l1.power_w, ")
    assert any(a["id"] == "core.safe" for a in health.alarms)


async def test_degraded_when_wallbox_unreachable(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE)
    rt.store.register_source("evcs", 5.0)
    rt.submit_intent(Intent(version=1, control=True))
    run(rt, clock, 32)
    assert rt.health().mode_reasons == ["EVCS „evcs“ nicht erreichbar."]


async def test_heartbeat_only_with_live_watchdog_actuator(runtime_env) -> None:
    sent: list[int] = []
    rt, _, clock = runtime_env(state=GOOD_STATE, configure=allow_evcs_live,
                               watchdog_send=lambda n: sent.append(n) or True)
    run(rt, clock, 15)
    assert sent == []
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 21)
    assert sent == [1, 2, 3]
    health = rt.health()
    assert (health.mode, health.watchdog["heartbeat"]) == ("OBSERVE", "active")
    assert "Watchdog am Cerbo ist nicht scharf." in health.mode_reasons
    rt.store.update("watchdog.state", 1, source="victron", kind=SignalKind.STATE)
    run(rt, clock, 1)
    assert rt.health().mode == "CONTROL"


async def test_release_when_live_dropped(runtime_env) -> None:
    sent: list[int] = []
    rt, _, clock = runtime_env(state=GOOD_STATE, configure=allow_evcs_live,
                               watchdog_send=lambda n: sent.append(n) or True)
    rt.submit_intent(Intent(version=1, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 2)
    rt.submit_intent(Intent(version=2))
    run(rt, clock, 1)
    assert (sent, rt.health().watchdog["heartbeat"]) == ([1, 0], "released")


async def test_shutdown_sends_no_release(runtime_env) -> None:
    sent: list[int] = []
    rt, _, clock = runtime_env(state=GOOD_STATE, configure=allow_evcs_live,
                               watchdog_send=lambda n: sent.append(n) or True)
    rt.submit_intent(Intent(version=1, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 2)
    await rt.shutdown()
    assert 0 not in sent


async def test_intent_survives_restart(runtime_env) -> None:
    first, _, _ = runtime_env()
    first.submit_intent(Intent(version=4, control=True))
    second, _, _ = runtime_env()
    assert second.intent() == Intent(version=4, control=True)


async def test_health_lists_actuators_and_watchdog(runtime_env) -> None:
    rt, _, _ = runtime_env()
    rt.cycle_once()
    health = rt.health()
    assert [a["id"] for a in health.actuators] == ["victron", "wallbox.evcs", "vehicle.tesla"]
    assert health.actuators[1] == {"id": "wallbox.evcs", "label": "EVCS „evcs“", "requested": "dry_run",
                                   "effective": "dry_run", "live_allowed": False, "reasons": [], "locked": []}
    assert health.watchdog == {"state": "unknown", "trip_count": None, "last_trip": None,
                               "last_trip_failures": None, "version": None, "expected_version": "1.0.0",
                               "heartbeat": "off", "required": False}
    assert health.inputs == {"ok": True, "problems": []}


async def test_alarm_since_is_stable(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE | {"dess.mode": 1})
    run(rt, clock, 12)
    first = {a["id"]: a["since"] for a in rt.health().alarms}
    run(rt, clock, 10)
    assert {a["id"]: a["since"] for a in rt.health().alarms}["competitor.dess"] == first["competitor.dess"]
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/integration/test_runtime_control.py tests/unit/core/state/test_store.py -q`
Expected: FAIL (`AttributeError: 'CoreRuntime' object has no attribute 'submit_intent'` u. a.)

- [ ] **Step 3: Laufzeit, Health und Store umsetzen**, dazu die Fixtures anpassen

- [ ] **Step 4: Vertragsdateien nachziehen und alles laufen lassen**

Run: `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py -q && uv run pytest -q && (cd ui && pnpm test)`
Expected: Nur `system.json` ändert sich: Es bekommt die neuen Health-Felder, `mode` ist weiter `OBSERVE`. Danach ist alles grün. Das UI-Schema ignoriert die neuen Felder, bis Task 17 sie nutzt. Bestehende Erwartungen bleiben gültig: Nach einem Zyklus steht der core in OBSERVE, vorher in START.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core tests/integration tests/unit/core/state tests/ui_world.py ui/src/lib/api/contract
git commit -m "feat(core): Betriebszustände, Absicht, Sperren und Heartbeat im Regelzyklus"
```

---

### Task 14: Cerbo-Watchdog – Logik, Konfiguration und Zustandsdatei

**Files:**
- Create:
  - `cerbo-watchdog/ruff.toml`, `cerbo-watchdog/config.ini.example`
  - `cerbo-watchdog/bindaems_watchdog/__init__.py`, `config.py`, `logic.py`, `state_file.py` (alle im Paket `bindaems_watchdog`)
- Modify: `pyproject.toml` (`pythonpath = [".", "cerbo-watchdog"]` unter `[tool.pytest.ini_options]`)
- Test: `tests/unit/cerbo_watchdog/test_logic.py`, `test_config.py`, `test_state_file.py`, `test_compat.py`

**Interfaces:**
- Produces:
  - `cerbo-watchdog/ruff.toml`: `extend = "../pyproject.toml"`, `target-version = "py38"`, `src = ["."]`
  - `bindaems_watchdog/__init__.py`: `VERSION = "1.0.0"`
  - Alle Module beginnen mit `from __future__ import annotations` und nutzen `typing.Optional`, `Tuple`, `Dict` und `List` statt `X | Y` oder `list[...]` außerhalb von Annotationen.
- Produces in `config.py`:
  - `class ConfigError(Exception)`
  - `@dataclass(frozen=True) class Action`: `name: str`, `service: str`, `path: str`, `value: object`, `instance: Optional[int] = None`, `tolerance: float = 0.5`. `value` ist ein JSON-Wert; `None` steht für „ungültig“ (auf D-Bus ein leeres Array).
  - `@dataclass(frozen=True) class Settings`: `enabled: bool`, `timeout_s: float`, `rearm_s: float`, `gap_s: float`, `retries: int`, `retry_s: float`, `state_file: str`, `actions: Tuple[Action, ...]`
  - `parse_settings(text: str) -> Settings` und `load_settings(path: str) -> Settings` (mit `configparser`)
    - Abschnitt `[watchdog]` mit diesen Schlüsseln und Defaults: `enabled = true`, `timeout_s = 90`, `rearm_s = 30`, `heartbeat_gap_s = 25`, `retries = 6`, `retry_s = 5`, `state_file = /data/bindaems-watchdog/state.json`.
    - Je Aktion ein Abschnitt `[action:<name>]` mit `service`, `path` und `value` (JSON), optional `instance` (int) und `tolerance` (float).
    - Die Aktionen behalten die Reihenfolge der Abschnitte.
    - Fehlertexte:

      | Fall | Text |
      |---|---|
      | kein Abschnitt `[watchdog]` | `"config.ini: Abschnitt [watchdog] fehlt."` |
      | Aktion unvollständig | `f"config.ini: [{section}] braucht service, path und value."` |
      | Wert kein JSON | `f"config.ini: [{section}] value ist kein JSON-Wert: {raw}"` |
      | Zeit ≤ 0 | `f"config.ini: {key} muss eine positive Zahl sein."` (für `timeout_s`, `rearm_s`, `heartbeat_gap_s`, `retry_s`) |
      | `retries` keine ganze Zahl ≥ 0 | `"config.ini: retries muss eine ganze Zahl ≥ 0 sein."` |
      | `heartbeat_gap_s` ≥ `timeout_s` | `"config.ini: heartbeat_gap_s muss kleiner als timeout_s sein."` |

- Produces in `logic.py`:
  - `WAITING, ARMED, TRIPPED, DISABLED = 0, 1, 2, 3`
  - `class WatchdogLogic`:
    - `__init__(self, settings: Settings, armed_at_start: bool, now: float) -> None`: DISABLED, wenn `enabled` falsch ist; sonst ARMED mit `last_seen = now`, wenn `armed_at_start`; sonst WAITING.
    - Attribut `state: int`
    - `heartbeat(self, value: int, now: float) -> None`:
      - **Wert > 0:** `last_seen = now`. Hat der Lauf noch nicht begonnen oder lag die letzte Lücke über `gap_s`, beginnt er neu (`run_start = now`).
      - **Wert 0:** aus ARMED oder TRIPPED nach WAITING; Lauf und `last_seen` zurücksetzen.
    - `tick(self, now: float) -> bool`; `True` heißt „jetzt auslösen“:
      - **ARMED:** Liegt `now − last_seen` über `timeout_s`, folgt TRIPPED; der Lauf wird zurückgesetzt, Ergebnis `True`.
      - **WAITING oder TRIPPED:** Liegt `now − last_seen` über `gap_s`, verfällt der Lauf. Sonst folgt ARMED, wenn `now − run_start ≥ rearm_s`.
      - **DISABLED:** nie.
    - Uhrzeiten sind monotone Sekunden.
- Produces in `state_file.py`:
  - `@dataclass(frozen=True) class StoredState`: `armed: bool = False`, `trip_count: int = 0`, `last_trip_time: int = 0`
  - `read_state(path: str) -> Tuple[StoredState, Optional[str]]`:
    - Datei fehlt → `(StoredState(), None)`.
    - Datei unlesbar → `(StoredState(armed=True), "state.json unlesbar; der Watchdog startet scharf.")`.
  - `write_state(path: str, state: StoredState) -> None`: atomar über eine temporäre Datei und `os.replace`
- `config.ini.example` entspricht genau der Ausgabe von Task 16 für `deploy/config.example.yaml`; Task 16 prüft das.

```ini
; BindaEMS-Watchdog – erzeugt mit „python -m bindaems.tools.watchdog_config“ aus config.yaml.
; Nicht von Hand ändern: config.yaml anpassen, neu erzeugen und mit install.sh übertragen.

[watchdog]
enabled = true
timeout_s = 90
rearm_s = 30
heartbeat_gap_s = 25
retries = 6
retry_s = 5
state_file = /data/bindaems-watchdog/state.json

; EVCS „evcs“ auf den sicheren Strom (wallboxes.evcs.safe_a)
[action:evcs_evcs]
service = com.victronenergy.evcharger
instance = 40
path = /SetCurrent
value = 6

; Min-SOC auf die USV-Reserve (battery.reserve_soc_pct)
[action:min_soc]
service = com.victronenergy.settings
path = /Settings/CGwacs/BatteryLife/MinimumSocLimit
value = 20

; Entladegrenze unbegrenzt
[action:max_discharge]
service = com.victronenergy.settings
path = /Settings/CGwacs/MaxDischargePower
value = -1
```

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/cerbo_watchdog/test_logic.py
SETTINGS = Settings(enabled=True, timeout_s=90.0, rearm_s=30.0, gap_s=25.0, retries=6, retry_s=5.0,
                    state_file="state.json", actions=())


def beats(logic: WatchdogLogic, times: Iterable[float]) -> None:
    for t in times:
        logic.heartbeat(1, t)


def armed() -> WatchdogLogic:
    logic = WatchdogLogic(SETTINGS, armed_at_start=False, now=0.0)
    beats(logic, [0, 10, 20, 30])
    logic.tick(30.0)
    return logic


def tripped() -> WatchdogLogic:
    logic = armed()
    logic.tick(120.5)
    return logic


def test_starts_waiting() -> None:
    assert WatchdogLogic(SETTINGS, armed_at_start=False, now=0.0).state == WAITING


def test_arms_after_30s_of_stable_heartbeats() -> None:
    logic = WatchdogLogic(SETTINGS, armed_at_start=False, now=0.0)
    beats(logic, [0, 10, 20])
    assert not logic.tick(29.0) and logic.state == WAITING
    beats(logic, [30])
    assert not logic.tick(30.0) and logic.state == ARMED


def test_gap_restarts_the_arming_run() -> None:
    logic = WatchdogLogic(SETTINGS, armed_at_start=False, now=0.0)
    beats(logic, [0, 10, 40])
    logic.tick(45.0)
    assert logic.state == WAITING
    beats(logic, [50, 60, 70])
    logic.tick(70.0)
    assert logic.state == ARMED


def test_trips_once_after_timeout() -> None:
    logic = armed()
    assert not logic.tick(120.0)
    assert logic.tick(120.5) and logic.state == TRIPPED
    assert not logic.tick(200.0)


def test_rearms_after_trip_with_stable_heartbeats() -> None:
    logic = tripped()
    beats(logic, [130, 140, 150])
    logic.tick(155.0)
    assert logic.state == TRIPPED
    beats(logic, [160])
    logic.tick(160.0)
    assert logic.state == ARMED


def test_release_returns_to_waiting_without_trip() -> None:
    logic = armed()
    logic.heartbeat(0, 40.0)
    assert logic.state == WAITING and not logic.tick(1000.0)


def test_restart_while_armed_trips_after_timeout() -> None:
    logic = WatchdogLogic(SETTINGS, armed_at_start=True, now=0.0)
    assert logic.state == ARMED
    assert not logic.tick(90.0)
    assert logic.tick(90.5)


def test_disabled_never_trips() -> None:
    logic = WatchdogLogic(replace(SETTINGS, enabled=False), armed_at_start=True, now=0.0)
    beats(logic, [0, 10])
    assert logic.state == DISABLED and not logic.tick(500.0)


def test_heartbeats_while_tripped_do_not_retrigger() -> None:
    logic = tripped()
    beats(logic, [130])
    assert not logic.tick(400.0) and logic.state == TRIPPED
```

```python
# tests/unit/cerbo_watchdog/test_config.py
EXAMPLE = Path("cerbo-watchdog/config.ini.example")


def test_example_parses() -> None:
    settings = load_settings(str(EXAMPLE))
    assert (settings.enabled, settings.timeout_s, settings.rearm_s, settings.gap_s,
            settings.retries, settings.retry_s) == (True, 90.0, 30.0, 25.0, 6, 5.0)
    assert [a.name for a in settings.actions] == ["evcs_evcs", "min_soc", "max_discharge"]
    assert settings.actions[0] == Action("evcs_evcs", "com.victronenergy.evcharger", "/SetCurrent", 6, instance=40)


def test_defaults_for_missing_keys() -> None:
    settings = parse_settings("[watchdog]\n")
    assert (settings.timeout_s, settings.state_file, settings.actions) == (
        90.0, "/data/bindaems-watchdog/state.json", ()
    )


def test_null_value_means_invalid() -> None:
    text = "[watchdog]\n[action:x]\nservice = a\npath = /b\nvalue = null\n"
    assert parse_settings(text).actions[0].value is None


@pytest.mark.parametrize(("text", "message"), [
    ("[sonst]\n", "config.ini: Abschnitt [watchdog] fehlt."),
    ("[watchdog]\n[action:x]\nservice = a\n", "config.ini: [action:x] braucht service, path und value."),
    ("[watchdog]\n[action:x]\nservice = a\npath = /b\nvalue = sechs\n",
     "config.ini: [action:x] value ist kein JSON-Wert: sechs"),
    ("[watchdog]\ntimeout_s = 0\n", "config.ini: timeout_s muss eine positive Zahl sein."),
    ("[watchdog]\nheartbeat_gap_s = 90\n", "config.ini: heartbeat_gap_s muss kleiner als timeout_s sein."),
])
def test_invalid_config(text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=re.escape(message)):
        parse_settings(text)
```

```python
# tests/unit/cerbo_watchdog/test_state_file.py
def test_missing_file_gives_defaults(tmp_path: Path) -> None:
    assert read_state(str(tmp_path / "state.json")) == (StoredState(), None)


def test_roundtrip(tmp_path: Path) -> None:
    path = str(tmp_path / "state.json")
    write_state(path, StoredState(armed=True, trip_count=2, last_trip_time=1791600000))
    assert read_state(path) == (StoredState(True, 2, 1791600000), None)


def test_corrupt_state_file_starts_armed(tmp_path: Path) -> None:
    (tmp_path / "state.json").write_text("kaputt")
    assert read_state(str(tmp_path / "state.json")) == (
        StoredState(armed=True), "state.json unlesbar; der Watchdog startet scharf."
    )
```

```python
# tests/unit/cerbo_watchdog/test_compat.py
SOURCES = sorted(Path("cerbo-watchdog").rglob("*.py"))


@pytest.mark.parametrize("path", SOURCES, ids=str)
def test_runs_on_python_38(path: Path) -> None:
    ast.parse(path.read_text(), filename=str(path), feature_version=(3, 8))


def test_independent_of_bindaems() -> None:
    offenders = [p for p in SOURCES if re.search(r"^\s*(from|import)\s+bindaems\b", p.read_text(), re.M)]
    assert offenders == []


def test_versions_match() -> None:
    assert bindaems_watchdog.VERSION == WATCHDOG_VERSION
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/cerbo_watchdog -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems_watchdog'`)

- [ ] **Step 3: Module und `config.ini.example` anlegen**

- [ ] **Step 4: Tests und ruff laufen lassen**

Run: `uv run pytest tests/unit/cerbo_watchdog -q && uv run ruff check cerbo-watchdog && uv run ruff format --check cerbo-watchdog`
Expected: `26 passed` (9 Logik, 8 Konfiguration, 3 Zustandsdatei, 6 Kompatibilität für 4 Dateien), ruff ohne Befund

- [ ] **Step 5: Commit**

```bash
git add cerbo-watchdog pyproject.toml tests/unit/cerbo_watchdog
git commit -m "feat(watchdog): Zustandsautomat, config.ini und Zustandsdatei des Cerbo-Watchdogs"
```

---

### Task 15: Cerbo-Watchdog – Aktionen, D-Bus-Dienst und Installation

**Files:**
- Create:
  - im Paket: `cerbo-watchdog/bindaems_watchdog/actions.py`, `cerbo-watchdog/bindaems_watchdog/service.py`
  - Skripte: `cerbo-watchdog/bindaems-watchdog.py`, `cerbo-watchdog/service/run`, `cerbo-watchdog/service/log/run`, `cerbo-watchdog/install.sh`
  - Doku: `cerbo-watchdog/README.md`
- Test: `tests/unit/cerbo_watchdog/test_actions.py`, `tests/unit/cerbo_watchdog/test_service.py`, `tests/unit/cerbo_watchdog/test_install.py`

**Interfaces:**
- Consumes: `Settings`, `Action`, `WatchdogLogic`, `StoredState`, `read_state`, `write_state`, `VERSION` (Task 14)
- Produces in `actions.py`:
  - `class ItemAccess(Protocol)`:
    - `find_service(self, service: str, instance: Optional[int]) -> Optional[str]`
    - `get_value(self, service: str, path: str) -> object`
    - `set_value(self, service: str, path: str, value: object) -> bool`
  - `class ActionRunner`:
    - `__init__(self, access: ItemAccess, actions: Sequence[Action], retries: int, retry_s: float) -> None`
    - `start(self, now: float) -> None`: alle Aktionen sofort fällig, Ergebnisse `"offen"`
    - `tick(self, now: float) -> None`
    - Properties `busy: bool`, `failures: int` und `results: Dict[str, str]`; die Werte von `results` sind `"offen"`, `"ok"`, `"unverändert"` und `"fehlgeschlagen"`
  - Ablauf je fälliger Aktion:
    1. Den Dienst bestimmen: mit `instance` über `find_service`, sonst `service`.
    2. Den Ist-Wert lesen; ist er gleich dem Sicherwert, gilt `"unverändert"`.
    3. Sonst `set_value`, dann zurücklesen und vergleichen; stimmt der Wert, gilt `"ok"`.
    4. Jede andere Lage zählt als Fehlversuch, ebenso eine Ausnahme oder ein nicht gefundener Dienst. Nach mehr als `retries` Fehlversuchen gilt `"fehlgeschlagen"` und `failures += 1`; sonst ist die Aktion nach `retry_s` erneut fällig.
  - Vergleich: Bei Sicherwert `None` passt `None` oder eine leere Liste. Zahlen (nicht `bool`) gelten bis `tolerance` als gleich, alles andere über `==`.
- Produces in `service.py`:
  - `SERVICE_NAME = "com.victronenergy.bindaems"`, `VELIB_PATH = "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python"`
  - `class WatchdogService`:
    - `__init__(self, settings, logic, runner, stored: StoredState, publish: Callable[[str, object], None], save: Callable[[StoredState], None], wall: Callable[[], float], monotonic: Callable[[], float]) -> None`
    - `values(self) -> Dict[str, object]`: aktuelle Werte aller Pfade: `/Heartbeat` (zuletzt empfangen, Start 0), `/State`, `/TripCount`, `/LastTripTime`, `/LastTripFailures`, `/TimeoutSeconds` (`timeout_s` als int), `/Version`
    - `on_heartbeat(self, path: str, value: object) -> bool`: Callback von VeDbusService. Er nimmt nur `int` an, kein `bool`, und nur Werte ≥ 0; dann ruft er `logic.heartbeat(value, monotonic())` auf und liefert `True`. Sonst liefert er `False`, und der Schreibzugriff wird abgelehnt.
    - `tick(self) -> bool`: GLib-Timer, Ergebnis immer `True`.
      - Löst `logic.tick` aus: `trip_count += 1`, `last_trip_time = int(wall())`, `runner.start(now)`, dann `publish` für `/TripCount` und `/LastTripTime`.
      - `runner.tick(now)`; ist der Runner gerade fertig geworden, folgt `publish("/LastTripFailures", runner.failures)`.
      - Hat sich `logic.state` geändert, folgen `publish("/State", state)` und `save(StoredState(armed=state == ARMED, trip_count, last_trip_time))`.
      - Jede Zustandsänderung, jede Auslösung und jedes fehlgeschlagene Ergebnis wird über `logging` protokolliert, auf Deutsch.
  - `register_paths(dbus_service: Any, service: WatchdogService) -> None`: `add_path` für alle Pfade aus `values()`. Nur `/Heartbeat` ist schreibbar (`writeable=True`, `onchangecallback=service.on_heartbeat`).
  - `class DbusItemAccess(ItemAccess)`:
    - `find_service` sucht in `bus.list_names()` nach Namen, die mit `service + "."` beginnen und deren `/DeviceInstance` der Instanz entspricht.
    - `get_value` und `set_value` nutzen `GetValue` bzw. `SetValue` an `com.victronenergy.BusItem`; `None` wird zu `dbus.Array([], signature="i")`. Ein `SetValue` mit Ergebnis 0 gilt als Erfolg.
  - `main(argv: Optional[List[str]] = None) -> int`:
    1. `argv[0]` ist der Pfad zu `config.ini`. Ein `ConfigError` beendet mit Code 2.
    2. `read_state(settings.state_file)` lesen; ein Problem wird geloggt.
    3. `VELIB_PATH` in `sys.path` aufnehmen, dann `dbus`, `dbus.mainloop.glib`, `gi.repository.GLib` und `vedbus` importieren, erst hier (mit `# type: ignore[import-not-found]`).
    4. `VeDbusService(SERVICE_NAME, bus, register=False)`; ältere velib-Versionen ohne `register` fängt `except TypeError` ab.
    5. `add_mandatory_paths(__file__, VERSION, "local", 0, 0xFFFF, "BindaEMS Watchdog", VERSION, "", 1)`, dann `register_paths(...)` und `register()` (falls vorhanden).
    6. `GLib.timeout_add(1000, service.tick)` und `GLib.MainLoop().run()`.
- `bindaems-watchdog.py`: setzt das eigene Verzeichnis an den Anfang von `sys.path` und ruft `sys.exit(main(sys.argv[1:]))` auf. Die Datei ist ausführbar und beginnt mit `#!/usr/bin/env python3`.
- `service/run` (ausführbar):

  ```sh
  #!/bin/sh
  exec 2>&1
  exec python3 /data/bindaems-watchdog/bindaems-watchdog.py /data/bindaems-watchdog/config.ini
  ```

  `service/log/run`: `exec multilog t s25000 n4 /var/log/bindaems-watchdog`
- `install.sh` (bash, `set -euo pipefail`, ausführbar):
  - **Aufrufe:**
    - `./install.sh <ziel> <config.ini>`, z. B. `./install.sh root@cerbo.lan config.ini`
    - `./install.sh --uninstall <ziel>`
    - Ohne gültige Argumente: Nutzung auf stderr, beginnend mit `Aufruf:`, Exitcode 2, kein ssh.
  - **Installation:**
    1. Per ssh prüfen, dass `python3` ≥ 3.8 und `/opt/victronenergy` vorhanden sind; sonst Abbruch mit Meldung.
    2. `bindaems-watchdog.py`, `bindaems_watchdog/` und `service/` per `scp -r` nach `/data/bindaems-watchdog/` kopieren, ebenso `config.ini` (Rechte 0644).
    3. In `/data/rc.local` einen Block zwischen `# bindaems-watchdog begin` und `# bindaems-watchdog end` mit `ln -sfn /data/bindaems-watchdog/service /service/bindaems-watchdog` setzen. Ein vorhandener Block wird ersetzt; `rc.local` wird bei Bedarf angelegt und ausführbar gemacht.
    4. Den Link sofort setzen. War der Dienst schon da, folgt `svc -t /service/bindaems-watchdog`.
    5. Nach 5 s Version und Zustand zeigen: `dbus -y com.victronenergy.bindaems /Version GetValue` und `/State GetValue`.
  - **Deinstallation:** `svc -d`, Link entfernen, Block aus `rc.local` entfernen, `/data/bindaems-watchdog` löschen.
- `README.md` (deutsch):
  - was der Watchdog tut: Zustände, Zeiten, Aktionen, Freigabe mit 0
  - Voraussetzungen: SSH-Zugang als root, Venus OS mit Python ≥ 3.8
  - `config.ini` erzeugen (Task 16), installieren, prüfen (Probe, Task 16), aktualisieren, entfernen
  - Logs unter `/var/log/bindaems-watchdog/current`

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/cerbo_watchdog/fakes.py
class FakeAccess:
    def __init__(self, values: dict[tuple[str, str], object], services: dict[tuple[str, int], str] | None = None,
                 failing_sets: int = 0) -> None:
        self.values, self.services, self.failing_sets = dict(values), services or {}, failing_sets
        self.writes: list[tuple[str, str, object]] = []

    def find_service(self, service: str, instance: int | None) -> str | None:
        return self.services.get((service, instance)) if instance is not None else service

    def get_value(self, service: str, path: str) -> object:
        return self.values.get((service, path))

    def set_value(self, service: str, path: str, value: object) -> bool:
        self.writes.append((service, path, value))
        if self.failing_sets > 0:
            self.failing_sets -= 1
            return False
        self.values[(service, path)] = value
        return True
```

```python
# tests/unit/cerbo_watchdog/test_actions.py
SETTINGS_PATH = ("com.victronenergy.settings", "/Settings/CGwacs/BatteryLife/MinimumSocLimit")
MIN_SOC = Action("min_soc", *SETTINGS_PATH, 20)


def run(runner: ActionRunner, times: Iterable[float]) -> None:
    runner.start(0.0)
    for t in times:
        runner.tick(t)


def test_skips_write_when_value_already_safe() -> None:
    access = FakeAccess({SETTINGS_PATH: 20})
    runner = ActionRunner(access, [MIN_SOC], retries=6, retry_s=5.0)
    run(runner, [0.0])
    assert (access.writes, runner.results, runner.busy) == ([], {"min_soc": "unverändert"}, False)


def test_writes_and_verifies() -> None:
    access = FakeAccess({SETTINGS_PATH: 30})
    runner = ActionRunner(access, [MIN_SOC], retries=6, retry_s=5.0)
    run(runner, [0.0])
    assert (access.writes, runner.results) == ([(*SETTINGS_PATH, 20)], {"min_soc": "ok"})


def test_retries_until_success() -> None:
    access = FakeAccess({SETTINGS_PATH: 30}, failing_sets=2)
    runner = ActionRunner(access, [MIN_SOC], retries=6, retry_s=5.0)
    run(runner, [0.0, 4.0, 5.0, 10.0])
    assert (len(access.writes), runner.results, runner.failures) == (3, {"min_soc": "ok"}, 0)


def test_counts_failures_after_retries() -> None:
    access = FakeAccess({SETTINGS_PATH: 30}, failing_sets=100)
    runner = ActionRunner(access, [MIN_SOC], retries=2, retry_s=5.0)
    run(runner, [0.0, 5.0, 10.0])
    assert (runner.results, runner.failures, runner.busy) == ({"min_soc": "fehlgeschlagen"}, 1, False)


def test_resolves_evcharger_by_instance() -> None:
    name = "com.victronenergy.evcharger.tcp_192_168_81_41"
    access = FakeAccess({(name, "/SetCurrent"): 16}, services={("com.victronenergy.evcharger", 40): name})
    action = Action("evcs_evcs", "com.victronenergy.evcharger", "/SetCurrent", 6, instance=40)
    run(ActionRunner(access, [action], retries=0, retry_s=5.0), [0.0])
    assert access.writes == [(name, "/SetCurrent", 6)]


def test_missing_service_counts_as_failure() -> None:
    action = Action("evcs_evcs", "com.victronenergy.evcharger", "/SetCurrent", 6, instance=40)
    runner = ActionRunner(FakeAccess({}), [action], retries=0, retry_s=5.0)
    run(runner, [0.0])
    assert (runner.results, runner.failures) == ({"evcs_evcs": "fehlgeschlagen"}, 1)


def test_null_value_compares_with_invalid() -> None:
    access = FakeAccess({("com.victronenergy.hub4", "/Overrides/Setpoint"): []})
    action = Action("setpoint", "com.victronenergy.hub4", "/Overrides/Setpoint", None)
    runner = ActionRunner(access, [action], retries=0, retry_s=5.0)
    run(runner, [0.0])
    assert runner.results == {"setpoint": "unverändert"}
```

```python
# tests/unit/cerbo_watchdog/test_service.py
SETTINGS = Settings(enabled=True, timeout_s=90.0, rearm_s=30.0, gap_s=25.0, retries=0, retry_s=5.0,
                    state_file="state.json", actions=(Action("min_soc", "com.victronenergy.settings",
                    "/Settings/CGwacs/BatteryLife/MinimumSocLimit", 20),))


class Env:
    def __init__(self, stored: StoredState = StoredState()) -> None:
        self.now = 0.0
        self.published: list[tuple[str, object]] = []
        self.saved: list[StoredState] = []
        access = FakeAccess({("com.victronenergy.settings", "/Settings/CGwacs/BatteryLife/MinimumSocLimit"): 30})
        self.service = WatchdogService(
            SETTINGS, WatchdogLogic(SETTINGS, stored.armed, 0.0),
            ActionRunner(access, SETTINGS.actions, SETTINGS.retries, SETTINGS.retry_s), stored,
            publish=lambda p, v: self.published.append((p, v)), save=self.saved.append,
            wall=lambda: 1791600000.0, monotonic=lambda: self.now,
        )

    def at(self, t: float, beat: bool = False) -> None:
        self.now = t
        if beat:
            self.service.on_heartbeat("/Heartbeat", 1)
        self.service.tick()


def test_heartbeat_callback_accepts_int_only() -> None:
    service = Env().service
    assert service.on_heartbeat("/Heartbeat", 5)
    assert not any(service.on_heartbeat("/Heartbeat", v) for v in ("x", True, -1, 1.5))


def test_trip_counts_runs_actions_and_publishes() -> None:
    env = Env()
    for t in (0, 10, 20, 30):
        env.at(t, beat=True)
    env.at(121)
    assert ("/State", 2) in env.published and ("/TripCount", 1) in env.published
    assert ("/LastTripTime", 1791600000) in env.published and ("/LastTripFailures", 0) in env.published
    assert env.saved[-1] == StoredState(armed=False, trip_count=1, last_trip_time=1791600000)


def test_state_saved_on_arm_and_release() -> None:
    env = Env()
    for t in (0, 10, 20, 30):
        env.at(t, beat=True)
    assert env.saved[-1].armed is True
    env.service.on_heartbeat("/Heartbeat", 0)
    env.at(31)
    assert env.saved[-1].armed is False and env.published[-1] == ("/State", 0)


class FakeDbusService:
    def __init__(self) -> None:
        self.paths: list[tuple[str, object, bool]] = []

    def add_path(self, path: str, value: object, writeable: bool = False, onchangecallback: Any = None, **_: Any) -> None:
        self.paths.append((path, value, writeable))


def test_register_paths_only_heartbeat_writeable() -> None:
    dbus_service = FakeDbusService()
    register_paths(dbus_service, Env().service)
    assert dbus_service.paths == [
        ("/Heartbeat", 0, True), ("/State", 0, False), ("/TripCount", 0, False), ("/LastTripTime", 0, False),
        ("/LastTripFailures", 0, False), ("/TimeoutSeconds", 90, False), ("/Version", "1.0.0", False),
    ]


def test_initial_values_from_stored_state() -> None:
    values = Env(StoredState(armed=True, trip_count=3, last_trip_time=5)).service.values()
    assert (values["/State"], values["/TripCount"], values["/LastTripTime"]) == (1, 3, 5)
```

```python
# tests/unit/cerbo_watchdog/test_install.py
ROOT = Path("cerbo-watchdog")
SCRIPTS = ["install.sh", "service/run", "service/log/run"]


@pytest.mark.parametrize("name", SCRIPTS)
def test_script_syntax(name: str) -> None:
    subprocess.run(["bash", "-n", str(ROOT / name)], check=True)


def test_install_without_arguments_prints_usage() -> None:
    result = subprocess.run(["bash", str(ROOT / "install.sh")], capture_output=True, text=True, check=False)
    assert result.returncode == 2 and result.stderr.startswith("Aufruf:")


@pytest.mark.parametrize("name", [*SCRIPTS, "bindaems-watchdog.py"])
def test_scripts_are_executable(name: str) -> None:
    assert os.access(ROOT / name, os.X_OK)
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/cerbo_watchdog -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'bindaems_watchdog.actions'`)

- [ ] **Step 3: Aktionen, Dienst, Skripte und README umsetzen** (Ausführrechte mit `git update-index --chmod=+x` sichern)

- [ ] **Step 4: Tests, ruff und mypy laufen lassen**

Run: `uv run pytest tests/unit/cerbo_watchdog -q && uv run ruff check cerbo-watchdog && uv run mypy cerbo-watchdog/bindaems_watchdog`
Expected: PASS (alle Tests aus den Tasks 14 und 15), ruff und mypy ohne Befund

- [ ] **Step 5: Commit**

```bash
git add cerbo-watchdog tests/unit/cerbo_watchdog
git commit -m "feat(watchdog): Aktionen mit Rücklesen, D-Bus-Dienst für Venus OS und Installationsskript"
```

---

### Task 16: Werkzeuge – `config.ini` erzeugen und Watchdog-Probe

**Files:**
- Create: `src/bindaems/tools/watchdog_config.py`, `src/bindaems/tools/watchdog_probe.py`
- Test: `tests/unit/tools/test_watchdog_config.py`, `tests/unit/tools/test_watchdog_probe.py`

**Interfaces:**
- Consumes:
  - `Config`, `load_config`, `load_secrets`
  - `MqttTransport`, `AiomqttTransport`, `KEEPALIVE_SUPPRESS`, `HEARTBEAT_PATH` (`core/adapters/victron_mqtt.py`)
  - `WATCHDOG_VERSION` (Task 12)
  - `Finding`, `render_report` (`tools/verify_checks.py`), `de` (Task 2)
- Produces in `watchdog_config.py`:
  - `render_config(cfg: Config) -> str` erzeugt genau das Format aus Task 14:
    - Zeiten aus `cfg.victron.watchdog`; `heartbeat_gap_s = 2,5 × heartbeat_s`; `rearm_s = 30`, `retries = 6`, `retry_s = 5`
    - Zahlen ohne überflüssige Nachkommastellen (`f"{v:g}"`)
    - **EVCS:** je EVCS-Wallbox ein Abschnitt `[action:evcs_<key>]` mit der evcharger-Instanz, deren Name in `victron.instances.evcharger` dem Schlüssel entspricht. Ohne Instanz steht stattdessen die Kommentarzeile `f"; EVCS „{key}“: keine evcharger-Instanz in victron.instances.evcharger – der Watchdog setzt keinen sicheren Strom"`.
    - Danach `min_soc` und `max_discharge`.
  - `main(argv: list[str] | None = None) -> int`: Argumente `--config` (Default `/config/config.yaml`) und `--out` (Default: stdout). Ein `ConfigError` gibt die Meldung auf stderr aus und endet mit Exitcode 2.
- Produces in `watchdog_probe.py`:
  - `@dataclass(frozen=True) class ProbeTimings`: `heartbeat_s`, `answer_wait_s`, `arm_wait_s`, `timeout_s`, `trip_slack_s`, `rearm_wait_s`, `release_wait_s` (alle `float`)
  - `timings_from_config(cfg: Config) -> ProbeTimings`: Heartbeat und Timeout aus `cfg.victron.watchdog`, dazu 10, 45, 30, 45 und 15 s
  - `async run_probe(transport: MqttTransport, portal: str, timings: ProbeTimings) -> list[Finding]`. Die Schritte laufen nacheinander; jeder liefert ein `Finding` mit Punkt `"17.2-5"`:

    | Titel | ok | sonst |
    |---|---|---|
    | Watchdog erreichbar | `f"Version {v}, Zustand {name}"` | fail: `"Keine Antwort von com.victronenergy.bindaems."` (Ende der Probe) |
    | Watchdog-Version | `v` | warn: `f"{v}, erwartet {WATCHDOG_VERSION}"` |
    | Scharf nach Heartbeat | `f"scharf nach {de(t)} s"` | fail: `f"nach {de(w)} s nicht scharf (Zustand {name})"` |
    | Auslösung ohne Heartbeat | `f"ausgelöst nach {de(t)} s, Auslösungen {a} → {b}, fehlgeschlagene Aktionen {f}"` | warn, wenn `f > 0`; fail: `f"nach {de(w)} s nicht ausgelöst"` |
    | Wieder scharf | `f"scharf nach {de(t)} s"` | fail wie oben |
    | Freigabe ohne Auslösung | `"Heartbeat 0: Zustand wartet"` | fail: `f"Zustand {name} statt wartet"` |

    - Ablauf:
      1. `N/<portal>/bindaems/0/#` abonnieren und `R/<portal>/keepalive` leer publizieren; dann bis `answer_wait_s` auf State und Version warten.
      2. Heartbeats ab 1 im Takt `heartbeat_s` senden, bis der Zustand 1 ist (höchstens `arm_wait_s`).
      3. Die Heartbeats stoppen und bis `timeout_s + trip_slack_s` auf Zustand 2 warten.
      4. Die Heartbeats wieder aufnehmen und bis `rearm_wait_s` auf Zustand 1 warten.
      5. Die 0 senden und bis `release_wait_s` auf Zustand 0 warten.
    - Alle 30 s wird der Keepalive mit `KEEPALIVE_SUPPRESS` gesendet.
    - Zustandsnamen: 0 wartet, 1 scharf, 2 ausgelöst, 3 deaktiviert. Zeiten werden auf ganze Sekunden gerundet; `de()` setzt das Dezimalkomma.
  - `main(argv: list[str] | None = None) -> int`:
    - Argumente: `--config` (wie oben), `--out` (Default `docs/verification/<Datum>-watchdog-probe.md`), `--yes`.
    - Reihenfolge: Konfiguration laden, dann bestätigen lassen, dann `load_secrets()`, dann verbinden. Der Abbruch braucht damit keine Secrets.
    - Ohne `--yes` zeigt sie diese Warnung und fragt `Fortfahren? [j/N]`:

      ```
      Die Probe löst den Watchdog aus. Er setzt die EVCS auf den sicheren Strom und schreibt Min-SOC und Entladegrenze, wenn sie abweichen.
      ```

      Jede Antwort außer `j` beendet mit Exitcode 1, ohne Verbindung.
    - Sonst verbindet sie sich wie der Victron-Adapter; die Portal-ID kommt aus der Konfiguration oder aus `N/+/system/0/Serial`.
    - Der Bericht entsteht mit `render_report` und `meta = {"Werkzeug": "watchdog_probe", "Datum": …}`.
    - Exitcode 0, wenn kein Schritt `fail` ist, sonst 1.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/tools/test_watchdog_config.py
EXAMPLE = Path("cerbo-watchdog/config.ini.example")


def test_example_matches_rendered_example(cfg) -> None:
    assert render_config(cfg) == EXAMPLE.read_text()


def test_rendered_config_parses(cfg) -> None:
    settings = parse_settings(render_config(cfg))
    assert [a.name for a in settings.actions] == ["evcs_evcs", "min_soc", "max_discharge"]
    assert settings.actions[0].instance == 40


def test_evcs_without_gx_instance_gets_no_action(cfg) -> None:
    instances = cfg.victron.instances.model_copy(update={"evcharger": {}})
    text = render_config(cfg.model_copy(update={"victron": cfg.victron.model_copy(update={"instances": instances})}))
    assert "[action:evcs_evcs]" not in text
    assert "; EVCS „evcs“: keine evcharger-Instanz in victron.instances.evcharger" in text


def test_main_writes_file(cfg, tmp_path: Path) -> None:
    out = tmp_path / "config.ini"
    assert main(["--config", "deploy/config.example.yaml", "--out", str(out)]) == 0
    assert out.read_text() == render_config(cfg)


def test_main_rejects_invalid_config(tmp_path: Path, capsys) -> None:
    broken = tmp_path / "config.yaml"
    broken.write_text("grid: 1\n")
    assert main(["--config", str(broken)]) == 2
    assert "Konfiguration ungültig" in capsys.readouterr().err
```

`SimulatedCerbo` in `tests/unit/tools/test_watchdog_probe.py` ist ein `MqttTransport`:
- Er enthält eine `WatchdogLogic` aus `bindaems_watchdog` mit verkürzten Zeiten (`timeout_s=0.4`, `rearm_s=0.15`, `gap_s=0.12`).
- Heartbeats auf `W/<P>/bindaems/0/Heartbeat` reicht er an die Logik weiter.
- Ein Hintergrund-Task ruft alle 10 ms `tick` auf.
- Bei jeder Änderung stellt er `N/`-Meldungen für `State`, `TripCount` und `LastTripFailures` (0) in seine Nachrichten. Auf den Keepalive antwortet er mit `Version` und `State`.

```python
FAST = ProbeTimings(heartbeat_s=0.05, answer_wait_s=0.5, arm_wait_s=1.0, timeout_s=0.4, trip_slack_s=0.5,
                    rearm_wait_s=1.0, release_wait_s=0.5)
TITLES = ["Watchdog erreichbar", "Watchdog-Version", "Scharf nach Heartbeat", "Auslösung ohne Heartbeat",
          "Wieder scharf", "Freigabe ohne Auslösung"]


async def test_probe_passes_against_simulated_watchdog() -> None:
    async with SimulatedCerbo() as cerbo:
        findings = await run_probe(cerbo, P, FAST)
    assert [f.title for f in findings] == TITLES
    assert {f.status for f in findings} == {"ok"}


async def test_probe_without_answer_stops_after_first_step() -> None:
    async with SilentTransport() as silent:
        findings = await run_probe(silent, P, FAST)
    assert findings == [Finding("17.2-5", "Watchdog erreichbar", "fail", "Keine Antwort von com.victronenergy.bindaems.")]


def test_main_aborts_without_confirmation(monkeypatch) -> None:
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    monkeypatch.setattr("bindaems.tools.watchdog_probe.AiomqttTransport", Mock(side_effect=AssertionError("keine Verbindung")))
    assert main(["--config", "deploy/config.example.yaml"]) == 1
```

`SilentTransport` nimmt alles an und liefert keine Nachrichten.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools/test_watchdog_config.py tests/unit/tools/test_watchdog_probe.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Beide Werkzeuge umsetzen**

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools/test_watchdog_config.py tests/unit/tools/test_watchdog_probe.py tests/unit/cerbo_watchdog -q`
Expected: PASS (8 neue Tests, alle Watchdog-Tests grün)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools/watchdog_config.py src/bindaems/tools/watchdog_probe.py tests/unit/tools
git commit -m "feat(tools): config.ini für den Watchdog aus config.yaml erzeugen, Watchdog-Probe"
```

---

### Task 17: Status in UI und HA

**Files:**
- Modify:
  - app: `src/bindaems/app/ha/entities.py`
  - UI: `ui/src/lib/api/schemas.ts`, `ui/src/lib/system/view.ts`, `ui/src/routes/system/+page.svelte`
  - Demo und Tests: `tests/ui_world.py`, `tests/app_helpers.py`
- Regenerate: `ui/src/lib/api/contract/*.json` (`UPDATE_UI_CONTRACT=1`)
- Test: `tests/unit/app/ha/test_entities.py`, `ui/src/lib/system/view.test.ts`, `ui/src/routes/system/page.test.ts`

**Interfaces:**
- Consumes: Health-JSON aus Task 13 (über `GET /api/system`, Feld `core.health`)
- Produces in `entities.py`:
  - `HaInputs.watchdog_state: str | None = None`: `build_inputs` liest `health["watchdog"]["state"]`, wenn vorhanden.
  - Neue Entität `watchdog_tripped` am Gerät „BindaEMS“: binary_sensor „Watchdog ausgelöst“ mit `device_class="problem"`.
    - Wert `on_off(state == "tripped")`; bei `None` oder `"unknown"` gilt `on_off(None)`.
  - `sample_inputs` bekommt `watchdog_state=None`.
- Produces in `schemas.ts`, in `CoreHealthSchema`:
  - `mode: v.picklist(['START', 'OBSERVE', 'CONTROL', 'DEGRADED', 'SAFE'])`
  - Zusätzlich: `mode_since: v.nullable(Iso)`, `mode_reasons: v.array(v.string())`
  - `intent: v.nullable(v.object({ version: v.number(), control: v.boolean(), actuators: v.record(v.string(), Mode) }))`
  - `actuators: v.array(v.object({ id, label, requested: Mode, effective: Mode, live_allowed: v.boolean(), reasons: v.array(v.string()), locked: v.array(v.string()) }))`
  - `watchdog: v.object({ state: v.picklist(['waiting', 'armed', 'tripped', 'disabled', 'unknown']), trip_count: NullableNumber, last_trip: v.nullable(Iso), last_trip_failures: NullableNumber, version: v.nullable(v.string()), expected_version: v.string(), heartbeat: v.picklist(['off', 'active', 'released']), required: v.boolean() })`
  - `inputs: v.object({ ok: v.boolean(), problems: v.array(v.string()) })`
  - Dabei ist `Mode = v.picklist(['dry_run', 'live'])`.
- Produces in `view.ts`:
  - `modeLabel(mode)`:

    | Wert | Text |
    |---|---|
    | START | „Start“ |
    | OBSERVE | „Beobachten (nur lesend)“ |
    | CONTROL | „Regeln“ |
    | DEGRADED | „Eingeschränkt“ |
    | SAFE | „Sicherer Zustand“ |

  - `watchdogStateLabel(state)`: waiting „wartet“, armed „scharf“, tripped „ausgelöst“, disabled „deaktiviert“, unknown „keine Antwort“
  - `heartbeatLabel(h)`: off „aus (kein Aktor live)“, active „aktiv“, released „freigegeben“
  - `actuatorModeLabel(m)`: dry_run „Dry-Run“, live „live“
  - `watchdogVersionText(version: string | null, expected: string): string`: `null` → „–“; gleich → `version`; sonst `${version} (erwartet ${expected})`
  - `actuatorNotes(actuator): string[]`: erst `reasons`, dann `locked`, je mit dem Präfix „gesperrt: “
- System-Seite:
  - **Karte „core“:** Unter „Betriebsart“ steht `modeLabel` mit „seit …“. Darunter stehen die `mode_reasons` als Liste. Neue Zeile „Eingangsdaten“ mit „in Ordnung“ oder den Problemen.
  - **Neue Karte „Aktoren“:** eine Tabelle in `.scroll` mit den Spalten Aktor (`label`), Angefordert, Wirksam, „live erlaubt“ (ja/nein) und Hinweise.
  - **Neue Karte „Watchdog am Cerbo“:**
    - Zustand mit `StatusDot`: armed `ok`; waiting und disabled `warn`; tripped `error`; unknown `error`, wenn `required`, sonst `unknown`
    - Heartbeat, Auslösungen, letzte Auslösung (`formatDateTime`, ohne Wert „nie“), fehlgeschlagene Aktionen, Version (`watchdogVersionText`)
- Demo-Welt:
  - `VICTRON_STATE` bekommt `watchdog.state 0`, `watchdog.version "1.0.0"`, `watchdog.trip_count 0`, `watchdog.last_trip_time 0`, `watchdog.last_trip_failures 0` und `watchdog.timeout_s 90`.
  - Damit zeigt die Demo „wartet“.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/app/ha/test_entities.py (ergänzen)
def test_watchdog_tripped_entity(cfg) -> None:
    spec = next(e for e in build_entities(cfg) if e.object_id == "watchdog_tripped")
    assert (spec.component, spec.name, spec.device_class) == ("binary_sensor", "Watchdog ausgelöst", "problem")
    assert spec.value(sample_inputs(watchdog_state="tripped")) == "ON"
    assert spec.value(sample_inputs(watchdog_state="armed")) == "OFF"
    assert spec.value(sample_inputs(watchdog_state="unknown")) == on_off(None)


def test_build_inputs_reads_watchdog_state(live, price_service, store, forecast_service) -> None:
    live.health = {**live.health, "watchdog": {"state": "tripped"}}
    assert build_inputs(live, price_service, store, forecast_service, T_APP).watchdog_state == "tripped"
    live.health = {k: v for k, v in live.health.items() if k != "watchdog"}
    assert build_inputs(live, price_service, store, forecast_service, T_APP).watchdog_state is None
```

```ts
// ui/src/lib/system/view.test.ts (ergänzen)
it('benennt Betriebsart, Watchdog und Heartbeat', () => {
	expect(modeLabel('DEGRADED')).toBe('Eingeschränkt');
	expect(watchdogStateLabel('armed')).toBe('scharf');
	expect(heartbeatLabel('released')).toBe('freigegeben');
});
it('zeigt eine abweichende Watchdog-Version mit der erwarteten', () => {
	expect(watchdogVersionText('0.9.0', '1.0.0')).toBe('0.9.0 (erwartet 1.0.0)');
	expect(watchdogVersionText('1.0.0', '1.0.0')).toBe('1.0.0');
	expect(watchdogVersionText(null, '1.0.0')).toBe('–');
});
it('sammelt Gründe und Sperren je Aktor', () => {
	expect(actuatorNotes({ reasons: ['Watchdog am Cerbo ist nicht scharf.'], locked: ['Dynamic ESS ist aktiv (Modus 1).'] } as never))
		.toEqual(['Watchdog am Cerbo ist nicht scharf.', 'gesperrt: Dynamic ESS ist aktiv (Modus 1).']);
});
```

Der Seitentest in `ui/src/routes/system/page.test.ts` nutzt die Vorlage der vorhandenen Tests. Er rendert eine Antwort mit Modus `DEGRADED`, einem Grund, einem gesperrten Victron-Aktor und dem Watchdog im Zustand `tripped` mit Version `0.9.0`. Erwartet werden:
- die Texte „Eingeschränkt“ und der Grund
- in der Karte „Watchdog am Cerbo“ die Texte „ausgelöst“ und „0.9.0 (erwartet 1.0.0)“
- in der Karte „Aktoren“ der Text „gesperrt: …“

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/app/ha -q && cd ui && pnpm test`
Expected: FAIL (neue Entität und Funktionen fehlen)

- [ ] **Step 3: HA-Entität, Schemas, Hilfsfunktionen und System-Seite umsetzen, dann die Vertragsdateien neu schreiben**

Run: `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py -q`
Expected: Die Dateien mit dem Zustand bekommen die Watchdog-Signale der Demo, `system.json` ihren Watchdog-Status („waiting“, Version 1.0.0).

- [ ] **Step 4: Alles prüfen**

Run:
```bash
uv run pytest -q
cd ui && pnpm lint && pnpm check && pnpm test && pnpm build
FONTCONFIG_FILE=$PWD/tests/e2e/fonts-ci.conf pnpm e2e
```
Expected: alles grün. Die System-Seite hat bei 360 px auch mit der Schrift der CI keinen waagrechten Überlauf; alle Bedienelemente sind mindestens 44 × 44 px groß.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/ha tests ui
git commit -m "feat(ui): Betriebszustand, Aktoren und Watchdog auf der System-Seite, HA-Sensor „Watchdog ausgelöst“"
```

---

### Task 18: Abdeckung, Importvertrag, CI und Doku

**Files:**
- Modify:
  - Projekt und CI: `pyproject.toml`, `uv.lock`, `.github/workflows/ci.yml`
  - Doku: `docs/betrieb.md`, `docs/superpowers/specs/2026-10-08-bindaems-design.md`, `docs/uebergabe.md`, `README.md`

**Interfaces:**
- Produces:
  - **Abhängigkeit:** `pytest-cov>=6.0` unter `dev` (mit `uv lock` übernehmen).
  - **Coverage-Konfiguration:** `[tool.coverage.run] source = ["bindaems"]`, `[tool.coverage.report] skip_empty = true`.
  - **import-linter-Vertrag „Safety-Schicht und Betriebszustände bleiben rein“:**
    - Typ `forbidden`, `source_modules = ["bindaems.core.safety", "bindaems.core.control"]`
    - `forbidden_modules = ["bindaems.core.adapters", "bindaems.core.api", "bindaems.core.runtime", "bindaems.core.telemetry", "bindaems.core.accounting", "bindaems.core.checks"]`
  - **CI-Job `python`:**
    - Schritt „Typprüfung“: `uv run mypy src cerbo-watchdog/bindaems_watchdog`
    - Schritt „Tests“: `uv run pytest -q --cov --cov-report=term --cov-fail-under=75`
    - Neuer Schritt „Abdeckung Safety und Regelung“: `uv run coverage report --include="src/bindaems/core/safety/*,src/bindaems/core/control/*" --fail-under=90`
  - **Doku:**
    - `docs/betrieb.md`: neue Abschnitte
      - „Betriebszustände und Freigaben“: Zustände, Gründe, Sperren, Dry-Run und live, Reihenfolge der Inbetriebnahme, Wartung nach Spec 15
      - „Watchdog am Cerbo“: `config.ini` erzeugen (`docker compose run --rm ems-core python -m bindaems.tools.watchdog_config > config.ini`), Installation mit `install.sh`, Probe (`docker compose run --rm ems-core python -m bindaems.tools.watchdog_probe`), Update, Entfernen, Logs
    - Spec, mit Verweis auf diesen Plan:
      - 5.6: Aufbau von `cerbo-watchdog/`
      - 7.2: Gate und Sperren je Teilsystem (Präzisierung 1)
      - 8.3: dringende Befehle und Rate-Limits je Art (Präzisierungen 4 und 5)
      - 8.6: Heartbeat nur bei Bedarf, Freigabe 0, `state.json`, `/LastTripFailures`, Lesen vor dem Schreiben, `config.ini` aus `config.yaml` (Präzisierungen 2 und 3)
      - 17.2, Punkt 5: Probe-Werkzeug
    - `docs/uebergabe.md`: Stand nach 2a, nächste Schritte (Watchdog installieren, Probe, Plan 2b), neue Befehle und Testzahlen
    - `README.md`: Verweis auf `cerbo-watchdog/README.md`

- [ ] **Step 1: Konfiguration und CI ändern, dann alle Prüfungen lokal laufen lassen**

Run:
```bash
uv lock && uv sync --locked --extra core --extra app --extra dev
uv run ruff format --check . && uv run ruff check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports
uv run pytest -q --cov --cov-report=term --cov-fail-under=75
uv run coverage report --include="src/bindaems/core/safety/*,src/bindaems/core/control/*" --fail-under=90
```
Expected: alles grün; lint-imports meldet 4 eingehaltene Verträge; Abdeckung gesamt ≥ 75 %, für Safety und Regelung ≥ 90 %.

- [ ] **Step 2: Doku schreiben** und prüfen, dass `tests/unit/test_docs.py` grün bleibt

Run: `uv run pytest tests/unit/test_docs.py -q`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml uv.lock .github/workflows/ci.yml docs README.md
git commit -m "build: Abdeckungsgrenzen, Importvertrag für die Safety-Schicht, Doku zu Watchdog und Freigaben"
```

---

## Abschluss

- **Gesamtreview des Branches** gegen Spec und Plan, mit den Punkten des Review Focus. Die Befunde werden behoben, wie im gewählten Ausführungs-Skill beschrieben.
- **Danach an der Anlage**, gemeinsam mit dem Betreiber:
  1. `config.ini` erzeugen und den Watchdog installieren.
  2. Die Probe laufen lassen und den Bericht nach `docs/verification/` übernehmen. Die Auslösung setzt die EVCS auf den sicheren Strom; danach den gewünschten Strom wieder einstellen.
  3. Im UI und in HA prüfen, dass der Watchdog-Status stimmt („wartet“ nach der Probe).
- **Dann folgt Plan 2b.**

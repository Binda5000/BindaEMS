# Abfahrtszeit in Home Assistant als Uhrzeit – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Entität „Abfahrtszeit“ je Fahrzeug kommt in Home Assistant als Uhrzeit (MQTT-Plattform `time`) statt als Textfeld, damit das Dashboard sie mit der normalen Uhrzeit-Eingabe direkt in der Karte bedienen kann.

**Architecture:** Nur die app (`src/bindaems/app/ha/`): `{oid}_departure` wechselt von `text` zu `time`, Discovery unter `homeassistant/time/bindaems/<oid>_departure/config` ohne `pattern`, der Zustand als `HH:MM:00`. Befehle nehmen `HH:MM` und `HH:MM:SS` an (Home Assistant sendet `HH:MM:SS`), die Absicht speichert weiter `HH:MM`. Beim Verbinden leert die app die alte, gehaltene Text-Discovery mit einer leeren Nachricht, damit Home Assistant das alte Textfeld entfernt. core, `shared`, Absicht, API, UI und Datenbank bleiben unverändert (keine Migration).

**Tech Stack:** Python 3.12, aiomqtt, pytest. Keine neuen Abhängigkeiten.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Abschnitt 12 (Tabelle der Bedienelemente, Zeile „Abfahrtszeit je Auto“, und „Je Fahrzeug“ unter Plan 4b). Home-Assistant-Doku „MQTT Time“ (`platform: time`, seit Home Assistant 2026.5): Zustand eine Uhrzeit wie `12:33:00`, Befehl im ISO-Format.

## Einordnung

- **Anlass:** Im Home-Dashboard (4b) öffnet die Abfahrtszeit als Textfeld ein eigenes Fenster oder verlangt Tippen im Format `HH:MM`. Eine `time`-Entität zeigt Home Assistant in jeder Entitäten-Karte als Uhrzeit-Eingabe direkt in der Zeile, wie ein `input_datetime` nur mit Uhrzeit.
- **Was sich für dich ändert:** Die Entitäts-ID wechselt von `text.bindaems_<auto>_abfahrtszeit` zu `time.bindaems_<auto>_abfahrtszeit`. Dashboards und Automationen mit der alten ID passe ich mit an (die Dashboard-Dateien unter `/mnt/project-files/dashboard/` nach dem Merge, außerhalb des Repos); eigene Automationen musst du selbst umstellen.
- **Voraussetzungen an der Anlage:** Home Assistant ab 2026.5 (sonst ignoriert es die neue Entität, und die Abfahrtszeit fehlt) und die ACL-Zeile `topic write homeassistant/time/bindaems/#`. Die Zeile für `text` bleibt, weil die app darüber das alte Textfeld entfernt.
- **Ausrollen:** nur `ems-app`, nach den beiden Voraussetzungen. Wann, entscheidest du beim Abschluss.

## Global Constraints

- Entitätsname bleibt „Abfahrtszeit“, `object_id` bleibt `<oid>_departure`, `unique_id` bleibt `bindaems_<oid>_departure`.
- Die Absicht speichert weiter `HH:MM` (`DEPARTURE_PATTERN` in `src/bindaems/app/control/intent.py` bleibt unverändert); Sekunden kommen nie in die Absicht.
- Alle Veröffentlichungen bleiben gehalten (`retain=True`) und laufen durch `allowed_topic`.
- Texte für den Betreiber auf Deutsch, Code-Kommentare im Stil der Umgebung.

## Review Focus

- Home Assistant sendet `06:30:00` (mit Sekunden): Der Befehl muss wirken wie `06:30` → Test in Task 2.
- Ungültige Uhrzeiten mit Sekunden (`06:30:60`, `6:30:00`, `06:30:0`, `24:00:00`) lehnt die app ab wie bisher ungültige `HH:MM` → Test in Task 2.
- Nach dem Update steht in Home Assistant noch das alte Textfeld neben der neuen Uhrzeit: Die leere Discovery auf dem alten Topic muss bei jeder Verbindung und nach dem Neustart von Home Assistant (`homeassistant/status` „online“) gehen → Test in Task 1.
- Die leere Nachricht für das alte Topic darf nicht an `allowed_topic` scheitern, und `time` muss erlaubt sein → Test in Task 1.
- Ohne bekannte Absicht zeigt die Uhrzeit `unknown`, nicht `unknown:00` → Test in Task 1.

---

### Task 1: Abfahrtszeit als `time`-Entität, altes Textfeld entfernen

**Files:**
- Modify: `src/bindaems/app/ha/entities.py` (`EntitySpec.component` ~169, `pattern` ~186, `_departure_state` ~296, `build_entities` ~1144, `discovery` ~1260–1310, neue Funktion `retired_discovery`)
- Modify: `src/bindaems/app/ha/publisher.py` (`allowed_topic` ~113, `HaPublisher.__init__`, `_publish_all` ~229)
- Modify: `src/bindaems/app/runtime.py` ~396 (`retired=` an `HaPublisher`)
- Test: `tests/unit/app/ha/test_entities.py` (~902), `tests/unit/app/ha/test_publisher.py` (~270), `tests/integration/test_ha_round_trip.py` (~122)

**Interfaces:**
- Produces: `EntitySpec.component: Literal["sensor", "binary_sensor", "select", "switch", "number", "time", "button"]`; das Feld `pattern` entfällt (einziger Nutzer war die Abfahrt).
- Produces: `retired_discovery(cfg: Config, mqtt: HaMqttConfig) -> tuple[str, ...]` in `entities.py`: je Fahrzeug `f"{mqtt.discovery_prefix}/text/{NODE_ID}/{ha_object_id(key)}_departure/config"`.
- Produces: `HaPublisher(..., retired: Sequence[str] = ())`; `_publish_all` veröffentlicht zuerst `""` gehalten auf jedes Topic aus `retired`, dann die Discovery wie bisher.

- [ ] **Step 1: Tests schreiben**

In `test_entities.py` wird `test_target_soc_number_and_departure_text` zu `test_target_soc_number_and_departure_time`:

```python
    assert "pattern" not in time
    assert (time["name"], time["command_topic"], time["state_topic"]) == (
        "Abfahrtszeit",
        "bindaems/cmd/tesla_departure",
        "bindaems/state/tesla_departure",
    )
    topic, _ = discovery(entity(cfg, "tesla_departure"), cfg.homeassistant.mqtt)
    assert topic == "homeassistant/time/bindaems/tesla_departure/config"
    # Zustand mit Sekunden, wie Home Assistant ihn für eine Uhrzeit erwartet
    assert state(cfg, "tesla_departure", inputs) == "06:30:00"
    assert state(cfg, "tesla_departure", sample_inputs(intent=None)) == "unknown"
```

Neu in `test_entities.py`:

```python
def test_retired_discovery_names_the_old_departure_text(cfg) -> None:
    assert retired_discovery(cfg, cfg.homeassistant.mqtt) == tuple(
        f"homeassistant/text/bindaems/{ha_object_id(key)}_departure/config" for key in cfg.vehicles
    )
```

In `test_publisher.py` wird `test_text_topics_are_allowed` zu `test_time_and_old_text_topics_are_allowed` (`homeassistant/time/bindaems/tesla_departure/config` und `homeassistant/text/bindaems/tesla_departure/config` erlaubt, `homeassistant/times/...` nicht). Neu, mit `publisher_env` (falls die Fixture `retired` noch nicht durchreicht, bekommt sie den Parameter):

```python
async def test_retired_discovery_is_cleared_on_connect_and_ha_restart(publisher_env) -> None:
    old = "homeassistant/text/bindaems/tesla_departure/config"
    publisher, transport = publisher_env(
        incoming=[("homeassistant/status", b"online")], retired=(old,)
    )
    await run_publisher_until(publisher, lambda: transport.count(old) == 2)
    assert all(payload == "" and retain for topic, payload, retain in transport.published if topic == old)
```

In `test_ha_round_trip.py` bekommt der Fall `{oid}_departure` `published="06:30:00"` und `shown="08:15:00"`.

- [ ] **Step 2: Tests laufen lassen, sie scheitern**

Run: `pytest tests/unit/app/ha/test_entities.py tests/unit/app/ha/test_publisher.py tests/integration/test_ha_round_trip.py -q`
Expected: FAIL (`retired_discovery` fehlt, Topic noch `text`, Zustand `06:30`)

- [ ] **Step 3: Umsetzen**

`build_entities`: `{oid}_departure` mit Komponente `"time"`, ohne `pattern`. `_departure_state` hängt `:00` an, außer bei `UNKNOWN`. `discovery`: der Zweig für `text`/`pattern` entfällt, der Docstring nennt `time` statt `text`. `allowed_topic`: Plattformliste `sensor|binary_sensor|select|switch|number|text|time|button` (`text` bleibt für das Leeren). `HaPublisher` speichert `retired` und veröffentlicht in `_publish_all` vor der Discovery `""` auf jedes Topic. `runtime.py` übergibt `retired=retired_discovery(cfg, mqtt)`.

- [ ] **Step 4: Tests laufen lassen, sie bestehen**

Run: `pytest tests/unit/app/ha tests/integration/test_ha_round_trip.py -q`
Expected: PASS. Dann die lokale Kette wie gewohnt (ruff, mypy, gesamte pytest-Suite mit Abdeckung).

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/ha/entities.py src/bindaems/app/ha/publisher.py src/bindaems/app/runtime.py tests/unit/app/ha tests/integration/test_ha_round_trip.py
git commit -m "feat(ha): Abfahrtszeit als Uhrzeit (MQTT time), altes Textfeld entfernen"
```

### Task 2: Befehl für die Abfahrt nimmt Sekunden an

**Files:**
- Modify: `src/bindaems/app/ha/commands.py:182-191` (`departure`)
- Test: `tests/unit/app/ha/test_commands.py:596-628`, `tests/integration/test_ha_round_trip.py` (~122)

**Interfaces:**
- Consumes: `DEPARTURE_PATTERN` und `SetTarget(vehicle, target_soc_pct, departure)` unverändert.
- Produces: `departure(key)` akzeptiert `HH:MM` und `HH:MM:SS` (Sekunden `00` bis `59`, werden verworfen) und gibt `SetTarget(key, None, "HH:MM")` zurück.

- [ ] **Step 1: Tests schreiben**

`test_departure_text_sets_only_the_departure` wird `test_departure_time_sets_only_the_departure` mit den Nutzlasten `("06:30", "06:30:00", "00:00:00", "23:59:59")` und den Ergebnissen `SetTarget("tesla", None, x)` für `x` in `("06:30", "06:30", "00:00", "23:59")`.

`test_departure_text_rejects_bad_time` wird `test_departure_time_rejects_bad_time` mit `("7.00", "7:00", "24:00", "06:60", "", "06:30:60", "6:30:00", "06:30:0", "24:00:00", "06:30:00:00")` und dem neuen Text:

```python
    assert rejections.texts == [
        PREFIX + f"Abfahrt „{shown}“ ist keine Uhrzeit (HH:MM)."
        for shown in (...)  # dieselben Nutzlasten
    ]
```

In `test_target_and_departure_through_the_real_service` sendet der Handler `"06:30:00"`; erwartet bleibt `departure == "06:30"`. In `test_ha_round_trip.py` sendet der Fall `{oid}_departure` die Nutzlast `"06:30:00"` (wie Home Assistant), Wirkung `departure == "06:30"`, `published` bleibt `"06:30:00"`.

- [ ] **Step 2: Tests laufen lassen, sie scheitern**

Run: `pytest tests/unit/app/ha/test_commands.py tests/integration/test_ha_round_trip.py -q`
Expected: FAIL (Abfahrt „06:30:00“ ist keine Uhrzeit)

- [ ] **Step 3: Umsetzen**

In `commands.py` ein Muster `_TIME = re.compile(rf"({DEPARTURE_PATTERN.pattern})(:[0-5]\d)?")`; `parse` nimmt bei `fullmatch` die erste Gruppe als Abfahrt, sonst `Refused` mit dem bisherigen Text. Docstring: „Nur die Abfahrt (`HH:MM`, von Home Assistant `HH:MM:SS`; die Sekunden fallen weg); das Ziel bleibt (`None`).“

- [ ] **Step 4: Tests laufen lassen, sie bestehen**

Run: `pytest tests/unit/app/ha tests/integration/test_ha_round_trip.py -q`
Expected: PASS. Dann die lokale Kette.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/ha/commands.py tests/unit/app/ha/test_commands.py tests/integration/test_ha_round_trip.py
git commit -m "feat(ha): Abfahrtszeit nimmt die Uhrzeit von Home Assistant mit Sekunden an"
```

### Task 3: Doku und Anlagenschritte

**Files:**
- Modify: `docs/betrieb.md` (Abschnitt 7 Fließtext ~450–454 und ACL-Block ~456–468; Entitätentabellen ~531 und ~575; ~2493; Ausrollschritte ~3394)
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (~1916 und ~1953)
- Modify: `docs/uebergabe.md` (Stand des Plans und offene Schritte an der Anlage)

**Interfaces:**
- Consumes: Verhalten aus Task 1 und 2 (Entitäts-ID `time.bindaems_<auto>_abfahrtszeit`, Zustand `HH:MM:00`, Befehl `HH:MM` oder `HH:MM:SS`, altes Textfeld wird geleert).

- [ ] **Step 1: Betriebshandbuch**

Abschnitt 7: ACL-Block bekommt `topic write homeassistant/time/bindaems/#` nach der `text`-Zeile; der Text nennt sieben Zeilen mehr als die Sensoren, `time` seit diesem Plan für die Abfahrtszeit (Home Assistant ab 2026.5) und `text` nur noch, damit die app das alte Textfeld entfernen kann. Tabelle ~531: „Abfahrtszeit (je Fahrzeug) | Uhrzeit | …“. Tabelle ~575: `tesla_departure` | Abfahrtszeit | time | Zustand `HH:MM:00`, Befehl `HH:MM` oder `HH:MM:SS` (Sekunden fallen weg); schreibend. ~2493 und ~3394 nennen die Uhrzeit-Entität und die `time`-Zeile.

- [ ] **Step 2: Spec**

Zeile ~1916: „Abfahrtszeit je Auto (Uhrzeit) | time | schreiben“. Unter „Je Fahrzeug“ (~1953) ein Satz: seit Plan „Abfahrt als Uhrzeit“ Plattform `time` statt `text`, ACL `topic write homeassistant/time/bindaems/#`, Home Assistant ab 2026.5.

- [ ] **Step 3: Übergabe**

Stand des Plans eintragen; unter den offenen Schritten an der Anlage in dieser Reihenfolge: Home-Assistant-Version prüfen (Einstellungen → Info, mindestens 2026.5), ACL-Zeile `topic write homeassistant/time/bindaems/#` ergänzen und das Mosquitto-Add-on neu starten, `ems-app` ziehen, in Home Assistant prüfen, dass `time.bindaems_<auto>_abfahrtszeit` die Abfahrt zeigt und das alte Textfeld weg ist, dann die neuen Dashboard-Dateien einspielen.

- [ ] **Step 4: Prüfen**

Run: `rg -n "homeassistant/text|text\.bindaems|\| text \|" docs/betrieb.md docs/superpowers/specs/2026-10-08-bindaems-design.md`
Expected: nur noch die ACL-Zeile für `text` und Sätze, die erklären, warum sie bleibt.

- [ ] **Step 5: Commit**

```bash
git add docs/betrieb.md docs/superpowers/specs/2026-10-08-bindaems-design.md docs/uebergabe.md
git commit -m "docs: Abfahrtszeit als Uhrzeit in Betriebshandbuch, Spec und Übergabe"
```

## Nach dem Merge (außerhalb des Repos)

- Dashboards unter `/mnt/project-files/dashboard/`: `home_dashboard_4b.yaml` (und das Energie-Dashboard, falls es die Abfahrt zeigt) nutzen `time.bindaems_<auto>_abfahrtszeit` in der Entitäten-Karte; die Schnellwahl-Chips rufen `time.set_value` mit `time: "06:30:00"`. `LIESMICH.md` nennt die neue ID und Home Assistant ab 2026.5.
- Die Anlagenliste (`/mnt/project-files/anlage/befehle-nach-3c.md`) führt der Thread „Befehle für die Anlage“; die Schritte aus Task 3, Step 3 gehen über den Projekt-Chat an ihn.

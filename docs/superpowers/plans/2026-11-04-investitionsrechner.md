# Investitionsrechner mit echten Tagen – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Werkzeug rechnet für ein ganzes Jahr aus Bindas echten Tagen durch, was eine Anschaffung bringt (mehr PV, größerer Akku, Wärmepumpe, Wallbox mit Phasenumschaltung, auch kombiniert): Stromkosten im Jahr, Autarkie, Monatsspitze, PV ins Auto, Wärmepumpenstrom, Ersparnis und Amortisation je Variante gegen „Heute“.

**Architecture:** Zwei Werkzeuge im Image der app, wie `export_days`/`scenario_days`. `export_invest` liest an der Anlage die Abrechnung (PV, Haus, je Wallbox, Bezug, Einspeisung je Viertelstunde), die Ladesessions und die gespeicherten Börsenpreise eines Rechenjahrs aus der Datenbank (nur lesend) und holt für alle Tage des Rechenjahrs Wetter je Dachfläche (Open-Meteo, historische Vorhersage) und fehlende Börsenpreise (Energy-Charts). Es schreibt eine JSON-Datei ohne Geheimnisse. `invest_days` rechnet daraus offline (an der Anlage oder in der Cloud) jede Variante einer YAML-Datei: Tage ohne Messung werden aus Wetter, Lastprofil und Sessions ergänzt und als „ergänzt“ ausgewiesen; je Viertelstunde laufen Haus, Wärmepumpe, Autos (PV-Überschuss mit Mindestleistung, sonst spätestmöglich) und das Akkumodell des Planers in `EIGENVERBRAUCH`; der Tarif gilt mit dem Stand eines Stichtags. Ergebnis ist ein Markdown-Bericht.

**Tech Stack:** Python 3.12, pydantic, httpx, PyYAML, pytest, Hypothesis. Keine neuen Abhängigkeiten, keine Migration, kein core, keine UI.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Abschnitte 1.4 (Nicht-Ziele: 1/3-Phasen-Umschaltung), 2 (Anlage, „weitere PV geplant“), 9 (Tarif, Leistungspreis, „Stand Plan 3b“ Szenario-Tests), 10.1 (PV-Modell), 18 (Phasenplan, Phase 6 Wärme). Ideen: `/mnt/project-files/ideen/brainstorming-2026-11.md` Nr. 5.5 und 17.1. Wärmepumpe: `/mnt/project-files/waermepumpe/auswertung.md`. Betrieb: `docs/betrieb.md`, „Planung“ (Abschnitt zu `export_days`/`scenario_days`).

## Einordnung

- **Anlass (Betreiber, 04.11.2026):** Aus dem Brainstorming vom 03.11.2026 Idee 5.5 „Investitionsrechner mit echten Tagen“ zusammen mit 17.1 „Lohnt sich eine Phasenumschaltung?“, Platz 10 der Top 10. „Erstelle mal Threads für die Features und beginne mit dem Plan.“ Vorgesehen fürs Frühjahr 2027, wenn ein Winter an Daten da ist.
- **Heute** (main 8ecbfe9): `scenario_days` vergleicht den Optimierer mit „Victron allein“ auf exportierten Tagen, kennt aber weder Wallbox-Lasten echter Tage (die Version 1 von `export_days` exportiert nur Haus und PV) noch andere Anlagen als die heutige. Die Abrechnung hat je Viertelstunde die Flüsse `pv>…`, `…>house`, `…>wb:<id>`, Bezug und Einspeisung; die Ladesessions stehen in `charging_session`; das PV-Modell (`app/forecast/pv_model.py`) rechnet jede Fläche aus der Einstrahlung. Rohdaten in InfluxDB halten nur 90 Tage, die Datenbank der app hält Abrechnung, Sessions und Preise dauerhaft.
- **Was sich für dich ändert:** nichts im Betrieb. Im Frühjahr rufst du an der Anlage `export_invest` auf (eine Zeile, Datei unter `/data`), schreibst deine Varianten in eine YAML-Datei (Vorlage im Repo) und rufst `invest_days` auf, oder du lädst die Datei ins Projekt und ich rechne sie hier. Du bekommst einen Bericht mit einer Tabelle je Variante und je Monat.
- **Ausrollen:** nur `ems-app`, mit dem nächsten gemeinsamen Pull nach den Abnahmen (frühestens 23.11.2026, Stufe 2 oder später). Keine Migration, keine ACL-Zeile, nichts an der Anlage schalten. Das Werkzeug braucht Internet (Open-Meteo, Energy-Charts), wie die app ohnehin.

## Entscheidungen (vorgeschlagen, warten auf deine Freigabe)

1. **Rechenjahr:** zwölf ganze lokale Monate, die mit `--bis JJJJ-MM` enden (Standard: der letzte abgeschlossene Monat). Ein Tag ist **echt**, wenn die Abrechnung ihn mit höchstens `MAX_FILLED` (4) unvollständigen Slots hat (wie `export_days`); sonst **ergänzt**. Ausgewiesen wird immer beides getrennt und das Jahr gesamt. *(Karte 1: ob ergänzt wird.)*
2. **Ergänzte Tage** (vor dem 18.10.2026 und Lücken): PV aus dem PV-Modell mit historischem Wetter der echten Tage dieses Datums mal Kalibrierfaktor (3); Hauslast als Median je Wochentagsgruppe (`day_type`) und lokaler Viertelstunde aus den wärmsten echten Tagen dieser Gruppe (die wärmsten 20 %, mindestens 5 Tage; hat eine Gruppe keine 5 echten Tage, alle echten Tage); Autos mit den Sessions echter Tage derselben Gruppe der Reihe nach (Session-Zeiten auf den Tag verschoben, Energie gleich; eine Kopie, die sich an derselben Wallbox mit einer vorigen überschneidet, entfällt); Börsenpreis echt für dieses Datum (Energy-Charts).
3. **Kalibrierung der PV:** Faktor `f = Σ gemessene PV / Σ modellierte PV der heutigen Flächen` über die echten Tage, je Kalendermonat mit mindestens 10 echten Tagen, sonst über alle echten Tage, ohne echte Tage 1,0; immer auf 0,5 bis 1,5 begrenzt. Der Faktor gilt auch für zusätzliche Flächen. Die Einstellungen `pv_model` (PR, Temperaturkoeffizient, NOCT) wie in der app. Zusätzliche Flächen hängen an einem eigenen Wechselrichter, wenn die Variante `wechselrichter_zusatz_w` nennt (dann sind nur sie darauf begrenzt), sonst am heutigen (dann ist die Summe auf `inverter_ac_max_w` begrenzt).
4. **Rechenart „Eigenverbrauch“:** Das Werkzeug fährt das Akkumodell des Planers (`battery_model.step`) mit `EIGENVERBRAUCH` in allen Slots, ohne Optimierer. Ein Jahr mit vielen Varianten rechnet so in Sekunden, und alle Varianten werden mit derselben Regel verglichen. Folge: Netzladen in billigen Stunden und „Akku ins Auto“ sind nicht drin; der Nutzen eines größeren Akkus ist damit eine **untere Grenze**. Der Bericht sagt das.
5. **Autos:** Je Session bleibt die gemessene Energie (an der Wallbox) gleich, nur der Zeitpunkt ist frei. Je Slot bekommt zuerst der Akku den PV-Überschuss (Rangfolge Akku → Autos, Entscheidung vom 18.10.2026), dann laden die Autos in der Reihenfolge `Config.wallbox_order` aus dem Rest, wenn er mindestens ihre Mindestleistung erreicht (bis zur Höchstleistung und zum Restbedarf). Passt der Restbedarf nicht mehr in die verbleibenden Slots der Session bei voller Leistung, lädt das Auto den Überhang sofort, aus welcher Quelle auch immer (spätestmöglich, wie „Geplant“ ohne Plan). Mindest- und Höchstleistung: `Phasen × min_a bzw. max_a × voltage_nominal_v` des Fahrzeugs (Gast und unbekannt: 3 Phasen und die Ströme der Wallbox), Höchststrom höchstens der der Wallbox. „Heute“ rechnet mit derselben Regel, damit der Vergleich fair bleibt; der Abgleich (9) zeigt, wie weit die Regel von deinem echten Laden abweicht.
6. **Phasenumschaltung (17.1):** Eine Variante nennt Wallboxen mit Umschaltung (`phasenumschaltung: [evcs]`). Für Sessions dort ist die Mindestleistung `1 × min_a × voltage_nominal_v` (bei 6 A und 230 V 1380 W); die Höchstleistung bleibt. Umschaltpausen und Zählgrenzen sind nicht drin; das Ergebnis ist eine obere Grenze für den Nutzen.
7. **Wärmepumpe:** Heizwärme im Jahr `heizwaerme_kwh` (von dir, etwa aus Energieausweis oder Heizlastberechnung des Installateurs) verteilt auf die Slots nach Heizgradstunden `max(0, heizgrenze_c − T)` (Standard 15 °C, Außentemperatur aus Open-Meteo); Warmwasser `warmwasser_kwh_tag` gleichmäßig im Fenster `warmwasser_fenster` (lokal, etwa „11:00-15:00“). Strom = Wärme / COP, COP linear zwischen den Stützpunkten `cop_heizen` bzw. `cop_warmwasser` (°C → COP, außerhalb der äußere Wert). Höchstens `max_el_w`; was darüber liegt, holt die Wärmepumpe in den nächsten Slots nach (Rückstand in kWh Strom), was am Ende des Jahres offen ist, steht als „nicht gedeckt“ im Bericht. Kein Pufferspeicher-Modell, keine Steuerung nach Preis oder PV (das wäre 6b). Optional `klima_heizen_kwh` je Monat: Strom der Klimaanlage fürs Heizen, der mit der Wärmepumpe entfällt; er wird auf den echten Tagen des Monats anteilig (echte Tage ÷ Tage im Monat) nach Heizgradstunden von der Hauslast abgezogen, nie unter 0 (was nicht abziehbar war, steht im Bericht).
8. **Tarif mit Stichtag:** Alle Slots rechnen mit den Tarifbestandteilen, die am `tarif_stand` gelten (Standard: heute), ohne deren Gültigkeitsgrenzen; Uhrzeit- und Saisonfenster (SNAP) gelten nach dem Datum des Slots. So zählt der Leistungspreis ab 2027 auch für Monate von 2026. Börsenpreis je Slot echt. Einspeisung: OeMAG-Wert aus den Einstellungen wie in der app (`feed_in_price`); fehlt er, `einspeisung_ct` des Monats aus der Variantendatei, sonst `einspeisung_ersatz_ct`; fehlt auch der, bricht der Lauf mit der Liste der Monate ab.
9. **Abgleich:** Auf den echten Tagen stehen gemessener Bezug und gemessene Einspeisung neben denen von „Heute“ im Modell. Weicht eines um mehr als 15 % ab, steht ein Hinweis über der Tabelle.
10. **Geld:** Stromkosten = Energie (Bezug × Bezugspreis brutto − Einspeisung × Einspeisepreis) + Leistungspreis je Monat auf die höchste Viertelstunde. Grundgebühren zählen nicht (gleich in allen Varianten). Ersparnis/Jahr = Strom „Heute“ − Strom Variante + `ersparnis_sonst_eur_jahr` (etwa Heizöl und Kesselwartung, von dir). Netto-Investition = `investition_eur − foerderung_eur`. Amortisation = Netto ÷ Ersparnis (bei Ersparnis ≤ 0 „nie“), Saldo über `nutzungsdauer_jahre` = Ersparnis × Jahre − Netto. Ohne Zins und Preissteigerung.
11. **Ergebnis als Markdown-Bericht**, keine Seite in der App. *(Karte 2.)*
12. **Nicht in diesem Plan:** Optimierer je Variante, Netzladen, Pufferspeicher/Steuerung der Wärmepumpe, Alterung von PV und Akku, Zins, HA-Werte aus der Zeit vor BindaEMS, eine Seite in der App.

## Global Constraints

- Texte für den Betreiber auf Deutsch, Anrede „du“; Kommentare und Docstrings deutsch im Stil der Umgebung. Zahlen im Bericht mit Dezimalkomma wie in `scenario/report.py`.
- Neue Module: `src/bindaems/tools/invest/{__init__,data,history,variants,year,heatpump,cars,model,report}.py`, CLIs `src/bindaems/tools/export_invest.py` und `src/bindaems/tools/invest_days.py`, Vorlage `deploy/invest-varianten.example.yaml`.
- Konstanten wörtlich: `MAX_FILLED` aus `export_days` (4), `CALIBRATION_MIN_DAYS = 10`, `CALIBRATION_RANGE = (0.5, 1.5)`, `PROFILE_SHARE = 0.2`, `PROFILE_MIN_DAYS = 5`, `HEATING_LIMIT_C = 15.0`, `START_SOC_PCT = 50.0`, `MATCH_TOLERANCE = 0.15`, `SPOT_CHUNK_DAYS = 28`, `HISTORICAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"`.
- Die Datenbank öffnet nur `open_database_readonly`; kein Werkzeug schreibt Einstellungen, Audit oder Archive.
- Exitcodes wie die anderen Werkzeuge: 0 Erfolg, 2 bei ungültiger Eingabe, fehlender Datenbank, Netzfehler, nicht schreibbarer Zieldatei, Rechenfehler; Meldung auf stderr, Logs nach stderr.
- Leistungen in W, Energien in kWh, Preise in ct/kWh brutto, Zeiten in UTC, lokale Tage `Europe/Vienna` über `shared/timeutil`.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy --strict` (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators), UI `pnpm check`, `pnpm lint`, `pnpm test` (unverändert). In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Tests ohne Netz (httpx `MockTransport`) und mit `core.data_dir`/`app.data_dir` unter `tmp_path`. Commits deutsch im Stil `feat(tools): …`. Kein Aktor wird live geschaltet. Keine Modellbezeichnungen im Repo.

## Review Focus

- **Zeitumstellung im Rechenjahr** (Tage mit 92 und 100 Slots, Open-Meteo in UTC, Warmwasserfenster lokal): jeder Slot genau einmal, Fenster nach lokaler Uhrzeit → Tests in Task 2 (`test_weather_covers_dst_days`) und Task 5 (`test_hot_water_window_on_dst_day`).
- **Eine Session über Mitternacht oder über das Ende des Rechenjahrs** (oder noch offen): Energie bleibt in der Session, nichts zählt doppelt, die Kopie auf ergänzte Tage verschiebt sie ganz → Tests in Task 2 (`test_session_open_ends_at_year_end`) und Task 4 (`test_copied_session_over_midnight_keeps_energy`).
- **Zwei Autos gleichzeitig mit wenig Überschuss:** das erste in `wallbox_order` bekommt den Überschuss, das zweite nur den Rest über seiner Mindestleistung; Zwangsladen beider gleichzeitig → Test in Task 6 (`test_two_cars_share_surplus_in_order`).
- **Variante ohne Unterschied zu „Heute“** (leere Änderungen): exakt dieselben Zahlen, Ersparnis 0, Amortisation „nie“ → Test in Task 7 (`test_empty_variant_equals_today`).
- **Monat ohne OeMAG-Wert und ohne Ersatz, Tarif ohne Leistungspreis-Sätze:** Abbruch mit Monatsliste bzw. Leistungspreis 0 bei ausgewiesener Spitze → Tests in Task 7 (`test_missing_feed_in_lists_months`, `test_power_price_without_rates_is_zero`).

---

### Task 1: Historisches Wetter und Börsenpreise holen

**Files:**
- Modify: `src/bindaems/app/forecast/openmeteo.py` (neben `fetch_plane`)
- Create: `src/bindaems/tools/invest/__init__.py`, `src/bindaems/tools/invest/history.py`
- Test: `tests/unit/tools/invest/test_history.py`, `tests/unit/app/forecast/test_openmeteo.py` (ergänzen)

**Interfaces:**
- Produces: `HISTORICAL_URL` und `async def fetch_plane_history(http: httpx.AsyncClient, clock: Clock, location: LatLon, plane: PvPlane, first: date, last: date) -> RawResponse` in `openmeteo.py`: dieselben `minutely_15`-Variablen wie `fetch_plane`, `start_date`/`end_date` (ISO, UTC-Tage, je einen Tag davor und danach, damit lokale Tage voll sind), `timezone=GMT`, `timeformat=unixtime`. Die Antwort parst das vorhandene `parse_plane`.
- Produces: `async def fetch_weather(http, clock, location: LatLon, planes: Sequence[PvPlane], first: date, last: date) -> dict[str, dict[datetime, WeatherSlot]]` (Schlüssel: Name der Fläche; ein Aufruf je Fläche, höchstens 92 Tage je Aufruf, Ergebnis zusammengesetzt; HTTP-Fehler über `http_error`, 5xx mit drei Versuchen wie `shared/retry.py`).
- Produces: `async def fetch_spot(http, clock, first: date, last: date) -> dict[datetime, float]` (ct/kWh netto je Slotbeginn UTC, über `EnergyChartsSource` in Stücken von `SPOT_CHUNK_DAYS`; ein Stundenwert gilt für seine vier Slots, ein 15-min-Wert für seinen Slot).
- Produces: `class HistoryError(Exception)`; jeder Fehler (HTTP, Parse, Lücke über 4 Slots je Tag und Fläche) wird zu `HistoryError` mit Quelle und Tag.

- [ ] **Step 1: Tests schreiben**

```python
def test_fetch_plane_history_params() -> None:
    # MockTransport hält die Anfrage fest
    assert request.url.host == "historical-forecast-api.open-meteo.com"
    assert params["start_date"] == "2026-03-31" and params["end_date"] == "2026-04-02"
    assert params["tilt"] == "30" and params["azimuth"] == "0"

def test_fetch_weather_splits_into_92_day_calls() -> None:  # 01.04.2026–31.03.2027 → 4 Aufrufe je Fläche

def test_weather_covers_dst_days() -> None:
    weather = run(fetch_weather(..., first=date(2026, 10, 25), last=date(2026, 10, 25)))
    assert set(local_day_slots(date(2026, 10, 25))) <= set(weather["Sued"])  # 100 Slots

def test_fetch_spot_hourly_fills_four_slots() -> None:
    spot = run(fetch_spot(...))  # Antwort mit einem Stundenwert 120 EUR/MWh um 10:00 UTC
    assert [spot[t] for t in four_slots_from_10_utc] == [12.0] * 4

def test_fetch_spot_chunks_by_28_days() -> None:  # 60 Tage → 3 Anfragen

def test_gap_over_four_slots_raises_history_error() -> None:
    with pytest.raises(HistoryError, match="Sued.*2026-11-02"):
```

- [ ] **Step 2: Tests laufen lassen, sie scheitern** — `uv run pytest tests/unit/tools/invest/test_history.py tests/unit/app/forecast/test_openmeteo.py -q` → FAIL (`ImportError`)
- [ ] **Step 3: `fetch_plane_history`, `fetch_weather`, `fetch_spot`, `HistoryError` umsetzen.** Lücken bis 4 Slots je Tag und Fläche füllt `fetch_weather` mit dem Mittel der Nachbarn (GTI und Temperatur).
- [ ] **Step 4: Tests grün, Prüfkette** — dieselbe Zeile → PASS; dann die ganze Prüfkette
- [ ] **Step 5: Commit** — `feat(tools): historisches Wetter je Fläche und Börsenpreise fürs Rechenjahr holen`

---

### Task 2: Datei des Rechenjahrs und `export_invest`

**Files:**
- Create: `src/bindaems/tools/invest/data.py`, `src/bindaems/tools/export_invest.py`
- Modify: `src/bindaems/tools/export_days.py` (`_fill` als `fill_gaps` öffentlich, für beliebige Werte je Slot)
- Test: `tests/unit/tools/invest/test_data.py`, `tests/unit/tools/test_export_invest.py`

**Interfaces:**
- Consumes: Task 1 (`fetch_weather`, `fetch_spot`, `HistoryError`); `LedgerService.slots`, `ChargingTracker.sessions`, `SessionLookup.at`, `PriceService.slots` (`PriceView.spot_net_ct`), `StoredSettings`.
- Produces in `data.py` (pydantic, `_Model`, `extra="forbid"`):
  - `InvestPlane(name: str, kwp: PositiveFinite, tilt_deg, azimuth_deg, existing: bool)`
  - `InvestWallbox(id: str, min_a: int, max_a: int)`, `InvestVehicle(key: str, phases: Literal[1, 2, 3], min_a: int, max_a: int)`
  - `InvestSlot(start: AwareDatetime, real: bool, pv_w: NonNegativeFinite | None, house_w: NonNegativeFinite | None, wallbox_w: dict[str, NonNegativeFinite], import_w: NonNegativeFinite | None, export_w: NonNegativeFinite | None, spot_net_ct: Finite, temp_c: Finite, gti_w_m2: dict[str, NonNegativeFinite])` — bei `real=False` sind `pv_w`, `house_w`, `import_w`, `export_w` `None` und `wallbox_w` leer (Validator).
  - `InvestSession(wallbox: str, vehicle: str | None, start: AwareDatetime, end: AwareDatetime, energy_kwh: NonNegativeFinite)`
  - `InvestFile(version: Literal[1], created_at, name: str, first_month: str, last_month: str, location: LatLon, planes: list[InvestPlane], inverter_ac_max_w, pv_model: PvModelSettings, battery: BatteryConfig, roundtrip_pct: float, voltage_nominal_v: float, fuse_a: float, fuse_margin_a: float, wallbox_order: list[str], wallboxes: list[InvestWallbox], vehicles: list[InvestVehicle], tariff: TariffSettings, feed_in: FeedInSettings, slots: list[InvestSlot], sessions: list[InvestSession])` — `temp_c` kommt aus dem Wetter der ersten heutigen Fläche, `roundtrip_pct` aus `planning.roundtrip_pct`. Validator: Slots lückenlos und aufsteigend über alle lokalen Tage der Monate, jede Fläche in jedem `gti_w_m2`.
- Produces in `export_invest.py`: `def export_invest(engine: Engine, cfg: Config, first_month: date, last_month: date, weather: Mapping[str, Mapping[datetime, WeatherSlot]], spot: Mapping[datetime, float], *, extra_planes: Sequence[PvPlane], clock: Clock) -> tuple[InvestFile, list[str]]` (rein bis auf die Datenbank; Zeilen je Monat: „2026-11: 30 Tage, 30 echt, 0 ergänzt“) und `main(argv)`.
- CLI: `python -m bindaems.tools.export_invest --config /config/config.yaml --bis 2027-03 --out /data/jahr.json [--varianten /data/varianten.yaml]`. `--varianten` liest nur die zusätzlichen Flächen (`pv_zusatz` aller Varianten, nach Namen eindeutig), damit ihr Wetter in der Datei steht. `main` braucht die Börsenpreise nur für Slots ohne gespeicherten Preis (`fetch_spot` über die zusammenhängenden Lücken).

- [ ] **Step 1: Tests schreiben** (Datenbank wie in `test_export_days.py`, Wetter und Preise als Dicts)

```python
def test_real_day_carries_flows_per_wallbox(app_db) -> None:
    # Slot mit pv>house 500 Wh, pv>wb:evcs 250 Wh, grid>wb:evcs 250 Wh, pv>grid 100 Wh
    slot = file.slots[index]
    assert slot.real and slot.pv_w == pytest.approx(3400) and slot.wallbox_w == {"evcs": 2000.0}
    assert slot.export_w == pytest.approx(400)

def test_day_with_five_gaps_is_not_real(app_db) -> None:
    assert not any(s.real for s in day_slots(file, DAY))

def test_session_energy_from_assigned_slots(app_db) -> None:
    assert file.sessions[0].energy_kwh == pytest.approx(0.5)

def test_session_open_ends_at_year_end(app_db) -> None:
    assert file.sessions[0].end == local_day_slots(date(2027, 3, 31))[-1] + SLOT

def test_stored_spot_wins_over_fetched(app_db) -> None:

def test_slot_without_spot_fails_with_day(app_db) -> None:
    with pytest.raises(ValueError, match="2026-11-02"):

def test_main_exit_2_without_database(tmp_path) -> None:
def test_main_writes_file_and_lines(tmp_path, monkeypatch) -> None:  # fetch_* durch Fakes ersetzt
def test_main_history_error_exit_2(tmp_path, monkeypatch, capsys) -> None:
def test_database_opened_read_only(app_db) -> None:  # wie in test_export_days
```

- [ ] **Step 2: Tests laufen lassen, sie scheitern** — `uv run pytest tests/unit/tools/test_export_invest.py tests/unit/tools/invest/test_data.py -q` → FAIL
- [ ] **Step 3: `data.py`, `fill_gaps` und `export_invest` umsetzen.** Echte Tage: Lücken bis `MAX_FILLED` je Tag über `fill_gaps` (PV, Haus, jede Wallbox, Bezug, Einspeisung). Wallbox-Energie ohne Session (`SessionLookup.at` → `None`) bleibt im Slot unter `wallbox_w` und zählt später als feste Last. Gast und unbekannt: `vehicle` `"guest"` bzw. `None`.
- [ ] **Step 4: Tests grün, `test_export_days.py` weiter grün, Prüfkette**
- [ ] **Step 5: Commit** — `feat(tools): export_invest schreibt ein Rechenjahr mit Wallboxen, Sessions, Wetter und Börsenpreisen`

---

### Task 3: Variantendatei

**Files:**
- Create: `src/bindaems/tools/invest/variants.py`, `deploy/invest-varianten.example.yaml`
- Test: `tests/unit/tools/invest/test_variants.py`

**Interfaces:**
- Produces (pydantic, `extra="forbid"`, deutsche Feldnamen wie in der YAML):
  - `CopPoint(temp_c: Finite, cop: PositiveFinite)`
  - `HeatPump(heizwaerme_kwh: NonNegativeFinite, heizgrenze_c: Finite = HEATING_LIMIT_C, warmwasser_kwh_tag: NonNegativeFinite = 0, warmwasser_fenster: str, cop_heizen: list[CopPoint] (≥ 2, Temperaturen streng steigend), cop_warmwasser: list[CopPoint] (dito), max_el_w: PositiveFinite, klima_heizen_kwh: dict[MonthKey, NonNegativeFinite] = {})` — `warmwasser_fenster` im Muster `HH:MM-HH:MM` auf Viertelstunden, Beginn ≠ Ende, über Mitternacht erlaubt.
  - `BatteryOverride(usable_kwh: PositiveFinite, max_charge_w: PositiveFinite, max_discharge_w: PositiveFinite)`
  - `Variant(name: str, investition_eur: NonNegativeFinite = 0, foerderung_eur: NonNegativeFinite = 0, nutzungsdauer_jahre: PositiveInt = 20, ersparnis_sonst_eur_jahr: Finite = 0, pv_zusatz: list[PvPlane] = [], wechselrichter_zusatz_w: PositiveFinite | None = None, akku: BatteryOverride | None = None, waermepumpe: HeatPump | None = None, phasenumschaltung: list[str] = [])`
  - `VariantsFile(tarif_stand: date | None = None, einspeisung_ct: dict[MonthKey, Finite] = {}, einspeisung_ersatz_ct: Finite | None = None, varianten: list[Variant] (≥ 1, Namen eindeutig, keiner heißt „Heute“))`
  - `def load_variants(path: Path) -> VariantsFile` (YAML; Fehler als `ValueError` mit Zeilenangabe von pydantic).
  - `def check_against(variants: VariantsFile, data: InvestFile) -> list[str]` — Fehlertexte: Fläche aus `pv_zusatz` ohne Wetter in der Datei („Fläche „Ost“ fehlt in der Datei: export_invest mit --varianten neu laufen lassen“), Wallbox aus `phasenumschaltung` unbekannt, `foerderung_eur > investition_eur`.
- Vorlage `deploy/invest-varianten.example.yaml` mit fünf Varianten und Kommentaren auf Deutsch: „PV Ost 6 kWp“, „Akku +10 kWh“, „Wallbox mit Phasenumschaltung“ (`[evcs]`), „Wärmepumpe, Warmwasser mittags“, „Wärmepumpe + PV Ost“. Werte der Wärmepumpe als deutlich markierte Platzhalter („aus dem Datenblatt der vampair ersetzen“), COP-Stützpunkte −15/−7/2/7/12 °C.

- [ ] **Step 1: Tests schreiben**

```python
def test_example_file_loads() -> None:
    variants = load_variants(Path("deploy/invest-varianten.example.yaml"))
    assert [v.name for v in variants.varianten][:3] == ["PV Ost 6 kWp", "Akku +10 kWh", "Wallbox mit Phasenumschaltung"]

def test_cop_points_must_rise() -> None:
def test_hot_water_window_over_midnight_ok() -> None:  # "22:00-02:00"
def test_variant_named_heute_rejected() -> None:
def test_check_against_reports_missing_plane_and_wallbox(invest_file) -> None:
    assert check_against(variants, invest_file) == [
        "Fläche „Ost“ fehlt in der Datei: export_invest mit --varianten neu laufen lassen",
        "Wallbox „wb2“ gibt es in der Datei nicht",
    ]
```

- [ ] **Step 2: scheitern lassen** — `uv run pytest tests/unit/tools/invest/test_variants.py -q` → FAIL
- [ ] **Step 3: `variants.py` und Vorlage umsetzen**
- [ ] **Step 4: grün, Prüfkette**
- [ ] **Step 5: Commit** — `feat(tools): Variantendatei des Investitionsrechners mit Vorlage`

---

### Task 4: Jahr vervollständigen (Kalibrierung, ergänzte Tage)

**Files:**
- Create: `src/bindaems/tools/invest/year.py`
- Test: `tests/unit/tools/invest/test_year.py`

**Interfaces:**
- Consumes: `InvestFile` (Task 2); `plane_power_w`, `combine` aus `app/forecast/pv_model.py`; `day_type` aus `app/daytype.py`.
- Produces:
  - `def calibration(data: InvestFile) -> dict[str, float]` (Schlüssel `JJJJ-MM` jedes Monats im Rechenjahr, Regeln Entscheidung 3).
  - `def plane_pv_w(data: InvestFile, plane: InvestPlane | PvPlane, slot: InvestSlot, factor: float) -> float` (ohne Wechselrichtergrenze).
  - `@dataclass(frozen=True) class YearSlot: start, real: bool, pv_w: float, house_w: float, fixed_wallbox_w: float, spot_net_ct: float, temp_c: float, measured_import_w: float | None, measured_export_w: float | None`
  - `@dataclass(frozen=True) class YearSession: wallbox: str, vehicle: str | None, first_slot: int, last_slot: int, energy_kwh: float, copied: bool` (Indizes in `YearData.slots`, `last_slot` einschließlich).
  - `@dataclass(frozen=True) class YearData: file: InvestFile, slots: list[YearSlot], sessions: list[YearSession], factors: dict[str, float], real_days: int, filled_days: int`
  - `def complete_year(data: InvestFile) -> YearData`. Echte Slots: Messwerte; `fixed_wallbox_w` = Wallbox-Leistung ohne Session. Ergänzte Slots: PV = `combine` der heutigen Flächen mal Faktor des Monats, begrenzt auf `inverter_ac_max_w`; Haus nach Entscheidung 2 (Profil `(day_type, lokale Uhrzeit)`; an Tagen mit 92/100 Slots die Uhrzeit des Slots, fehlende Uhrzeit aus der Stunde davor); Sessions nach Entscheidung 2, je Wochentagsgruppe reihum über die echten Tage in Datumsfolge. Hat das Rechenjahr keinen echten Tag, `ValueError("Kein echter Tag im Rechenjahr")`.

- [ ] **Step 1: Tests schreiben** (kleine Dateien aus einem Test-Helfer `make_file(days=…)` in `tests/unit/tools/invest/conftest.py`)

```python
def test_factor_per_month_needs_ten_days() -> None:
    # Oktober 12 echte Tage mit Ist = 0,8 · Modell, November 3 echte Tage mit Ist = 1,2 · Modell
    factors = calibration(data)
    assert factors["2026-10"] == pytest.approx(0.8)
    assert factors["2026-11"] == pytest.approx((12 * 0.8 + 3 * 1.2) / 15, rel=1e-3)  # gesamt, energiegewichtet

def test_factor_clamped() -> None:  # Ist = 3 · Modell → 1.5
def test_no_real_day_raises() -> None:
def test_filled_house_from_warmest_days() -> None:
    # 10 echte Werktage: die fünf wärmsten mit 300 W, die fünf kältesten mit 900 W.
    # max(PROFILE_MIN_DAYS, 20 % von 10) = 5 Tage → Median der fünf wärmsten = 300 W
    assert filled_slot.house_w == pytest.approx(300)

def test_filled_day_pv_from_model_times_factor() -> None:
def test_sessions_copied_round_robin_per_day_type() -> None:
def test_copied_session_over_midnight_keeps_energy() -> None:
def test_overlapping_copy_at_same_wallbox_dropped() -> None:
def test_real_days_unchanged() -> None:  # Hypothesis: echte Slots kommen 1:1 durch
```

- [ ] **Step 2: scheitern lassen** — `uv run pytest tests/unit/tools/invest/test_year.py -q` → FAIL
- [ ] **Step 3: `year.py` umsetzen**
- [ ] **Step 4: grün, Prüfkette**
- [ ] **Step 5: Commit** — `feat(tools): Rechenjahr aus echten und ergänzten Tagen mit PV-Kalibrierung`

---

### Task 5: Wärmepumpe

**Files:**
- Create: `src/bindaems/tools/invest/heatpump.py`
- Test: `tests/unit/tools/invest/test_heatpump.py`

**Interfaces:**
- Consumes: `HeatPump` (Task 3), `YearData` (Task 4).
- Produces:
  - `def cop_at(points: Sequence[CopPoint], temp_c: float) -> float` (linear, außerhalb der äußere Wert).
  - `@dataclass(frozen=True) class HeatPumpRun: el_w: list[float], heat_kwh: float, el_kwh: float, unmet_el_kwh: float, max_backlog_el_kwh: float, climate_removed_w: list[float], climate_not_removed_kwh: float` (`el_w` und `climate_removed_w` je Slot von `YearData.slots`).
  - `def run_heat_pump(year: YearData, hp: HeatPump) -> HeatPumpRun` nach Entscheidung 7. Heizgradstunden `w_t = max(0, heizgrenze_c − temp_c)`; Heizwärme je Slot `heizwaerme_kwh · w_t / Σw` (Σw = 0 → 0). Klima-Abzug: für Monat `m` mit Wert `K`: `K · echte Tage/Tage im Monat`, verteilt nach `w_t` auf die echten Slots des Monats, je Slot höchstens `house_w`.

- [ ] **Step 1: Tests schreiben**

```python
def test_cop_interpolates_and_clamps() -> None:
    points = [CopPoint(temp_c=-7, cop=2.8), CopPoint(temp_c=7, cop=4.2)]
    assert cop_at(points, 0) == pytest.approx(3.5) and cop_at(points, -20) == 2.8 and cop_at(points, 20) == 4.2

def test_space_heat_sums_to_year_value() -> None:
    assert run.heat_kwh == pytest.approx(hp.heizwaerme_kwh + 365 * hp.warmwasser_kwh_tag)

def test_no_heat_above_heating_limit() -> None:  # Slot mit 16 °C → nur Warmwasser
def test_power_capped_and_backlog_caught_up() -> None:
    assert max(run.el_w) <= hp.max_el_w and run.unmet_el_kwh == pytest.approx(0)

def test_unmet_when_cap_too_small() -> None:
def test_hot_water_window_on_dst_day() -> None:  # 25.10.2026, Fenster 01:00-03:00 → 12 Slots (02:00–03:00 doppelt)
def test_climate_removed_scaled_by_real_days_and_capped() -> None:
```

- [ ] **Step 2: scheitern lassen** — `uv run pytest tests/unit/tools/invest/test_heatpump.py -q` → FAIL
- [ ] **Step 3: `heatpump.py` umsetzen**
- [ ] **Step 4: grün, Prüfkette**
- [ ] **Step 5: Commit** — `feat(tools): Wärmepumpe aus Heizgradstunden und COP-Kurve`

---

### Task 6: Autos und Phasenumschaltung

**Files:**
- Create: `src/bindaems/tools/invest/cars.py`
- Test: `tests/unit/tools/invest/test_cars.py`

**Interfaces:**
- Consumes: `YearSession`, `InvestVehicle`, `InvestWallbox`, `voltage_nominal_v` (Tasks 2, 4).
- Produces:
  - `@dataclass(frozen=True) class CarLimits: min_w: float, max_w: float`
  - `def car_limits(data: InvestFile, session: YearSession, *, switching: bool) -> CarLimits` (Entscheidung 5 und 6).
  - `class CarFleet` mit `__init__(self, data: InvestFile, sessions: Sequence[YearSession], switching: Collection[str])` und `def power_w(self, index: int, available_w: float) -> tuple[float, float]`: für Slot `index` und den PV-Rest nach dem Akku die Summe der Ladeleistung und den davon aus PV-Rest gedeckten Teil; führt den Restbedarf je Session. `def shortfall_kwh(self) -> float` (Energie, die in keiner Session mehr Platz hatte).
  - Regel je Session im Slot (in `wallbox_order`): `forced = max(0, rest − danach · max_w · SLOT_H / 1000) · 1000 / SLOT_H`; `pv = min(available, max_w, rest·1000/SLOT_H)`, wenn `available ≥ min_w`, sonst 0; `ev = min(max(forced, pv), max_w, rest·1000/SLOT_H)`; `available −= min(available, ev)`.

- [ ] **Step 1: Tests schreiben**

```python
def test_limits_egolf_two_phases() -> None:
    assert car_limits(data, egolf_session, switching=False) == CarLimits(min_w=2760, max_w=7360)
    assert car_limits(data, egolf_session, switching=True).min_w == 1380

def test_guest_uses_wallbox_currents_three_phases() -> None:
def test_surplus_below_min_not_used_without_switching() -> None:
    fleet = CarFleet(data, [tesla_session], switching=())
    assert fleet.power_w(0, 2000) == (0.0, 0.0)
    fleet = CarFleet(data, [tesla_session], switching=("twc",))
    assert fleet.power_w(0, 2000) == (2000.0, 2000.0)

def test_forced_charge_at_end_of_session() -> None:  # 4 Slots, 11 kWh, max 11 kW → letzter Slot zwingt den Rest
def test_two_cars_share_surplus_in_order() -> None:
def test_energy_never_exceeds_session() -> None:  # Hypothesis über available_w-Folgen: Σ ev · SLOT_H = energy_kwh − shortfall
```

- [ ] **Step 2: scheitern lassen** — `uv run pytest tests/unit/tools/invest/test_cars.py -q` → FAIL
- [ ] **Step 3: `cars.py` umsetzen**
- [ ] **Step 4: grün, Prüfkette**
- [ ] **Step 5: Commit** — `feat(tools): Autos laden im Investitionsrechner aus PV-Rest, Phasenumschaltung als Variante`

---

### Task 7: Variante durchrechnen

**Files:**
- Create: `src/bindaems/tools/invest/model.py`
- Test: `tests/unit/tools/invest/test_model.py`

**Interfaces:**
- Consumes: Tasks 3–6; `battery_model.step`, `BatteryParams`, `SimSlot`, `eta`; `tariff.calc.import_price`, `feed_in_price`, `power_rate`, `power_cost_eur`.
- Produces:
  - `def tariff_as_of(tariff: TariffSettings, day: date) -> TariffSettings` (nur am Tag gültige Bestandteile, `valid_from`/`valid_until` geleert).
  - `@dataclass(frozen=True) class MonthResult: month: str, real_days: int, filled_days: int, pv_kwh, import_kwh, export_kwh, house_kwh, car_kwh, car_pv_kwh, heat_pump_kwh, energy_eur, power_eur, peak_kw, slots_over_limit: int`
  - `@dataclass(frozen=True) class VariantResult: name: str, months: list[MonthResult], autarky: float, self_use: float, heat: HeatPumpRun | None, car_shortfall_kwh: float, measured: tuple[float, float] | None, modelled_real: tuple[float, float]` mit Properties `cost_eur` (Σ energy + power), `real_part` und `filled_part` (je ein `MonthResult`-artiges Aggregat über die Slots echter bzw. ergänzter Tage).
  - `def run_variant(year: YearData, variants: VariantsFile, variant: Variant | None, *, today: date) -> VariantResult` (`None` = „Heute“). Je Slot: Hauslast (minus Klima-Abzug) + feste Wallbox + Wärmepumpe; PV + Zusatzflächen (Wechselrichter nach Entscheidung 3); PV-Rest nach Akku = `max(0, pv − last − min(max(0, pv − last), max_charge_w, Platz))`; Autos aus `CarFleet.power_w`; `step(soc, SimSlot(…, ev_w=…), BatteryIntent(), params)`. Start-SOC `START_SOC_PCT`. Preise nach Entscheidung 8, Bezugsgrenze `(fuse_a − fuse_margin_a) · voltage_nominal_v · 3`.
  - `class FeedInMissing(ValueError)` mit `months: list[str]`.
  - Autarkie `1 − Bezug ÷ Verbrauch` (Verbrauch = Haus + Autos + Wärmepumpe), Eigenverbrauch `(PV − Einspeisung) ÷ PV`, beide auf 0–1 begrenzt wie in `stats/service.py`.

- [ ] **Step 1: Tests schreiben**

```python
def test_empty_variant_equals_today(year) -> None:
    assert run_variant(year, vf, Variant(name="leer"), today=TODAY).months == run_variant(year, vf, None, today=TODAY).months

def test_more_pv_lowers_import_and_raises_export(year) -> None:
def test_bigger_battery_never_costs_more_energy(year) -> None:  # bei gleichem Tarif ohne Leistungspreis
def test_switching_moves_car_energy_to_pv(year) -> None:
    assert switched.months[0].car_pv_kwh > today.months[0].car_pv_kwh

def test_heat_pump_raises_winter_peak(year) -> None:
def test_tariff_as_of_applies_2027_power_price_to_2026(year) -> None:
def test_power_price_without_rates_is_zero(year) -> None:
def test_missing_feed_in_lists_months(year) -> None:
    with pytest.raises(FeedInMissing) as info:
    assert info.value.months == ["2026-04", "2026-05"]

def test_feed_in_order_settings_then_month_then_fallback(year) -> None:
def test_measured_vs_modelled_on_real_days(year) -> None:
def test_slots_over_limit_counted(year) -> None:
```

- [ ] **Step 2: scheitern lassen** — `uv run pytest tests/unit/tools/invest/test_model.py -q` → FAIL
- [ ] **Step 3: `model.py` umsetzen**
- [ ] **Step 4: grün, Prüfkette**
- [ ] **Step 5: Commit** — `feat(tools): Variante eines Rechenjahrs mit Akkumodell, Tarif zum Stichtag und Monatsspitzen`

---

### Task 8: Bericht, `invest_days` und Dokumentation

**Files:**
- Create: `src/bindaems/tools/invest/report.py`, `src/bindaems/tools/invest_days.py`
- Modify: `docs/betrieb.md` (neuer Abschnitt „## Investitionsrechner“ nach „## Simulator“), `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Abschnitt 18, Absatz „Stand Plan Investitionsrechner (04.11.2026)“), `docs/uebergabe.md`
- Test: `tests/unit/tools/invest/test_report.py`, `tests/unit/tools/test_invest_days.py`

**Interfaces:**
- Consumes: Tasks 2–7.
- Produces: `def render_report(year: YearData, variants: VariantsFile, results: Sequence[VariantResult], *, today: date) -> str` und `main(argv)`.
- CLI: `python -m bindaems.tools.invest_days --daten /data/jahr.json --varianten /data/varianten.yaml --out /data/invest.md`. Je Variante eine Zeile auf stdout („PV Ost 6 kWp: 1.234,56 € im Jahr, Ersparnis 321,00 €, Amortisation 14,2 Jahre“). Exit 2 bei `check_against`-Fehlern (alle Zeilen auf stderr), `FeedInMissing`, ungültigen Dateien, nicht beschreibbarem Ziel.
- Bericht, wörtliche Überschriften: `# Investitionsrechner`, `## Datenlage` (Rechenjahr, echte/ergänzte Tage, Faktoren je Monat, Tarif-Stichtag, Abgleich mit Hinweis „Das Modell weicht auf den echten Tagen um {p} % beim {Bezug|Einspeisung} ab.“ ab `MATCH_TOLERANCE`), `## Übersicht` (Tabelle: Variante, Strom €/Jahr, Ersparnis €/Jahr, Netto-Investition, Amortisation, Saldo über Nutzungsdauer, Autarkie, Eigenverbrauch, höchste Monatsspitze kW, PV ins Auto kWh, Wärmepumpe kWh / JAZ), `## Echte und ergänzte Tage` (Ersparnis je Variante getrennt), `## {Variante}` je Variante mit Monatstabelle, `## Annahmen` (Entscheidungen 4–7 und 10 in je einem Satz, darunter: „Netzladen und Akku ins Auto sind nicht gerechnet; der Nutzen eines größeren Akkus ist eine untere Grenze.“ und „Die Phasenumschaltung ist ohne Umschaltpausen gerechnet; ihr Nutzen ist eine obere Grenze.“).
- `docs/betrieb.md`: Aufruf an der Anlage (beide Befehle mit `docker compose exec ems-app python -m …` wie bei `export_days`), Datei ins Projekt laden als Alternative, was die Variantendatei braucht (Heizwärme, COP aus dem Datenblatt, Investition und Förderung), wie man den Bericht liest.

- [ ] **Step 1: Tests schreiben**

```python
def test_report_has_sections_in_order(year, results) -> None:
    text = render_report(year, vf, results, today=TODAY)
    assert [l for l in text.splitlines() if l.startswith("## ")][:4] == ["## Datenlage", "## Übersicht", "## Echte und ergänzte Tage", "## Heute"]

def test_payback_never_when_no_saving(year) -> None:  # Zelle „nie“
def test_match_hint_above_tolerance(year) -> None:
def test_numbers_with_decimal_comma(year) -> None:
def test_main_end_to_end(tmp_path) -> None:  # synthetisches Jahr aus conftest, Vorlage aus deploy/ → Exit 0, Bericht da
def test_main_check_errors_exit_2(tmp_path, capsys) -> None:
def test_main_feed_in_missing_exit_2(tmp_path, capsys) -> None:
def test_year_with_example_variants_runs_under_10_s(tmp_path) -> None:  # 365 Tage, 6 Varianten
```

- [ ] **Step 2: scheitern lassen** — `uv run pytest tests/unit/tools/invest/test_report.py tests/unit/tools/test_invest_days.py -q` → FAIL
- [ ] **Step 3: `report.py`, `invest_days.py`, Doku umsetzen**
- [ ] **Step 4: grün, ganze Prüfkette mit Abdeckung**
- [ ] **Step 5: Commit** — `feat(tools): invest_days rechnet Varianten und schreibt den Bericht` und `docs: Investitionsrechner in Betrieb, Spec und Übergabe`

---

## Abnahme

- **In der Cloud (Ende der Umsetzung):** `invest_days` auf einem synthetischen Jahr mit der Vorlage; die Zahlen jeder Variante von Hand an einem Wintertag und einem Sommertag nachgerechnet (Protokoll in `docs/verification/`).
- **An der Anlage (frühestens nach den Abnahmen, sinnvoll ab März 2027):** `export_invest --bis 2027-02` (oder `2027-03`), dann `invest_days` mit deinen Werten. Bestanden, wenn der Abgleich auf den echten Tagen höchstens 15 % abweicht und der Faktor der PV zwischen 0,7 und 1,3 liegt; sonst prüfen wir Modell oder Eingaben, bevor du Zahlen für einen Kauf nimmst.
- **Was du vor dem Lauf brauchst:** Heizwärmebedarf des Hauses in kWh/Jahr und Warmwasser je Tag (Energieausweis oder Installateur), COP-Werte der vampair aus dem Datenblatt bei deiner Vorlauftemperatur, Investition und Förderung je Variante, für PV die geplanten Flächen (Neigung, Ausrichtung, kWp).

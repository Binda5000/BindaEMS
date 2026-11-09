# Ölheizung: Wärmebedarf messen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BindaEMS zeichnet auf, wann der Brenner der Ölheizung läuft, und rechnet daraus je Tag Wärme (Brennerstunden × Nennleistung) und Öl (Brennerstunden × Liter je Stunde). Zusammen mit der Heizwärme der Klimaanlage ergibt das den Wärmebedarf des Hauses je Tag, ausgewertet gegen die Heizgradtage: „Grundbedarf je Tag“ plus „Wärme je Heizgradtag“. Das ist die gemessene Eingabe für den Investitionsrechner (Idee 5.5) und die Auslegung der Wärmepumpe (6b).

**Architecture:** Die UVR610 kennt den Brennerstatus; das C.M.I. schickt ihn als weiteren CoE-Digitalwert im selben Paket wie die Werte der Klimaanlage. Der core bekommt dafür nur eine optionale Zeile `oil_burner` in der Empfangstabelle (`CoeReceive`); der Adapter schreibt sie wie die anderen Bits als `thermal.<name>.oil_burner`, der Sampler legt sie bei Änderung in InfluxDB `status` ab. Die app rechnet jede Nacht den Vortag aus `status` (Brenner), `thermal` (Lebenszeichen und Leistung der Klimaanlage) und dem Wetterarchiv (Außentemperatur) und speichert ihn dauerhaft in SQLite (`heating_day`, Migration 0019), denn InfluxDB `raw` hält nur 90 Tage und das Wetterarchiv nur 120. Die Auswertung (lineare Regression Wärme gegen Heizgradtage je Heizsaison) ist eine reine Funktion; die Statistik zeigt zwei neue Karten. Nennleistung, Liter je Stunde, Ölpreis und die angenommene Leistungszahl der Klimaanlage stehen in den Einstellungen (UI).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic, InfluxQL (InfluxDB 1.x), SvelteKit 2 / Svelte 5, ECharts, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Abschnitte 2 (Tabelle Anlage, Klimaanlage), 11.2 (InfluxDB), 18 Phase 6 („6a: Empfangen“, „Empfangstabelle“, „Stand Plan 6a“, „Stand Plan 5c“, „Für 6b“). Idee: `/mnt/project-files/ideen/brainstorming-2026-11.md` Nr. 23.1. Nutzer der Zahlen: `docs/superpowers/plans/2026-11-04-investitionsrechner.md` (Branch `claude/project-thread-z62xwp`, noch nicht freigegeben), Entscheidung 7 und Task 5. Muster: `app/consumers/days.py` (Tage speichern und nachholen), `app/learning/thermal_data.py` (Bits aus `status` als Stufenfunktion).

## Befund

| Was | Wo heute | Folge |
|---|---|---|
| Brennerstatus der Ölheizung | in der UVR610 (Binda, 9.11.2026), nicht in BindaEMS | eine Zeile mehr in der CoE-Sendeliste des C.M.I. und in `receive` |
| Kessel | 27 kW Nennleistung, 2,9 l Öl je Stunde (Binda, 9.11.2026). Plausibel: 2,9 l/h × rund 10 kWh/l ≈ 29 kW Brennstoff, 27 kW Wärme ≈ 93 % | Wärme = Brennerstunden × 27 kWh, Öl = Brennerstunden × 2,9 l |
| Warmwasser | im Winter vom Kessel, im Sommer von der Solarthermie | der gemessene Winterbedarf enthält Heizung und Warmwasser; der Grundbedarf je Tag der Regression ist im Wesentlichen das Warmwasser (plus Bereitschaftsverluste des Kessels) |
| Klimaanlage | heizt die übrigen Räume; elektrische Leistung (F51) und Betriebsbits seit 6a in InfluxDB, je Viertelstunde lesbar mit `read_thermal_slots` | ihre Heizwärme gehört zum Bedarf des Hauses (die Wärmepumpe übernimmt sie, Spec „Für 6b“); Wärme = Strom × angenommene Leistungszahl |
| Bits in `status` | nur bei Änderung geschrieben (`core/telemetry/sampler.py`, `_status`); ein veralteter Wert schreibt nichts | ein Ausfall des C.M.I. bei laufendem Brenner sähe in `status` wie „läuft weiter“ aus → lebt die Quelle, zeigen die 10-s-Messwerte `thermal.actual_c` je Viertelstunde |
| InfluxDB `raw` | 90 Tage | ein Brenner, der im Sommer über 90 Tage aus ist, hat in `status` keinen „letzten Wert vor dem Fenster“ mehr → der gespeicherte Endzustand des Vortags ist der Anfangswert |
| Wetterarchiv | `weather_slot`, 120 Tage (`WEATHER_KEEP`) | Heizgradtage und Tagesmittel je Tag dauerhaft in `heating_day` |

## Entscheidungen

1. **Empfang über die vorhandene Empfangstabelle** der Klimaanlage: optionales Feld `oil_burner` in `CoeReceive` (wie `power_kw` und `cmd_on`), Signal `thermal.<name>.oil_burner`, Art Zustand. Nicht gewählt: ein eigener Adapter (der UDP-Port 5442 gehört schon dem Adapter der Klimaanlage) oder ein eigener Abschnitt `heating` in `config.yaml` (mehr core-Code für einen Wert, der im selben Paket ankommt). Ohne `oil_burner` ist alles hier aus: keine Tage, keine Karten, `heating: null` in der Statistik.
2. **Laufzeit aus `status` als Stufenfunktion,** wie die Betriebsbits in 5c. Anfangswert: letzter Wert vor dem Tag in `status`, sonst der gespeicherte Endzustand des Vortags (`heating_day.burner_end`), sonst unbekannt. Gezählt wird nur in Viertelstunden, in denen die Quelle lebt (mindestens ein Messwert `actual_c` in `thermal` für das Gerät). Ein Tag ist **vollständig**, wenn die bekannte Zeit mindestens 95 % der Tageslänge (23, 24 oder 25 h) erreicht.
3. **Wärme und Öl werden beim Lesen gerechnet,** nicht gespeichert: `heating_day` hält Brennerstunden, bekannte Stunden, Heizgradtage, Tagesmittel und den Strom der Klimaanlage beim Heizen. So gelten geänderte Werte in den Einstellungen sofort für alle Tage.
4. **Einstellungen in der UI** (Karte „Ölheizung“ unter Einstellungen, versioniert mit Änderungsprotokoll): Nennleistung (kW), Öl je Brennerstunde (l/h), Ölpreis (€/l, optional) und Leistungszahl der Klimaanlage beim Heizen (Standard 3,0, als Schätzung gekennzeichnet). Ab Werk sind Nennleistung und l/h leer; ohne sie zeigen die Karten die Brennerstunden und „Nennleistung fehlt“. Nach dem Ausrollen trägst du 27 kW und 2,9 l/h ein.
5. **Heizgradtage mit Heizgrenze 15 °C,** dieselbe Grenze wie im Investitionsrechner (`HEATING_LIMIT_C`): je Tag `Σ max(0, 15 − T) · ¼ h ÷ 24 h` über die Viertelstunden des Wetterarchivs, in K·d. So passt „Wärme je Heizgradtag“ eins zu eins zu dessen Verteilung nach Heizgradstunden. Fehlen mehr als 10 % der Viertelstunden, ist der Tag ohne Heizgradtage.
6. **Auswertung je Heizsaison** (1. Juli bis 30. Juni, „Heizsaison 2026/27“): Wärmebedarf je Tag `Q = Öl-Wärme + Klima-Strom × Leistungszahl`, lineare Regression `Q = Grundbedarf + Wärme je K·d × Heizgradtage` über die Tage, die vollständig sind, Heizgradtage haben und mindestens 1,0 K·d kalt waren. Ab 14 solchen Tagen gibt es ein Ergebnis mit Bestimmtheitsmaß R²; davor „noch {n} von 14 Tagen“. Wird der Grundbedarf negativ, rechnet sie ohne Grundbedarf (durch den Ursprung) und sagt das.
7. **Klimaanlage im Bedarf:** Strom beim Heizen je Tag = Summe der Viertelstunden mit Betriebsart `heizen` (aus `read_thermal_slots`) × Leistung. Viertelstunden ohne klare Betriebsart zählen nicht; ihre Energie steht als Hinweis am Tag, wenn sie 0,1 kWh übersteigt. Ohne Leistungskanal (`power_kw` fehlt) ist der Klima-Teil unbekannt, und der Bedarf ist nur die Öl-Wärme (Hinweis in der Karte).
8. **Anzeige auf der Seite Statistik,** zwei Karten nur bei eingerichtetem Brenner: „Heizung“ für den gewählten Zeitraum (Brennerstunden, Wärme aus Öl, Öl in Litern und Euro, Klimaanlage heizen als Strom und geschätzte Wärme, Heizgradtage, vollständige Tage) und „Wärmebedarf“ für die Heizsaison, in der der gewählte Tag liegt (Punktwolke Tage über Heizgradtage mit Ausgleichsgerade, Grundbedarf, Wärme je Heizgradtag, R², Zahl der Tage, Summen der Saison, Strom der Klimaanlage je Monat, CSV-Download). Heute zählt bis jetzt, wie die anderen Karten.
9. **Für den Investitionsrechner** liefert dieser Plan die Zahlen (Grundbedarf, Wärme je K·d, Klima-Strom je Monat) in der Karte und im CSV. **Vorschlag für den Plan Investitionsrechner** (dort umzusetzen, nicht hier): `export_invest` schreibt die Auswertung der letzten Heizsaison in die Jahresdatei, und `waermepumpe.heizwaerme_kwh` darf `gemessen` sein: Heizwärme je Slot = Wärme je K·d × Heizgradstunden ÷ 24, Warmwasser = Grundbedarf an Tagen mit mindestens 1 K·d (im Sommer macht es die Solarthermie), `klima_heizen_kwh` je Monat aus den gemessenen Tagen. Nach deiner Freigabe gebe ich das an den Thread „Investitionsrechner mit echten Daten“ weiter.
10. **Nachholen:** nachts um 02:45 die fehlenden Tage der letzten 3 (wie Verbraucher), beim Start die fehlenden der letzten 31, immer aufsteigend (der Endzustand des Vortags ist der Anfangswert des nächsten). Ein Tag ohne jeden Brennerwert bekommt eine Zeile mit Grund und wird nicht erneut gerechnet; ein Lesefehler schreibt nichts, der Tag kommt beim nächsten Lauf wieder dran.
11. **Ausrollen:** core und app gemeinsam mit Stufe 2 des Sammel-Pulls ab 23.11.2026 (der core muss das neue Feld kennen, sonst lehnt er `config.yaml` ab), Migration 0019 (oder die nächste freie Nummer, wenn vorher ein anderer Plan auf `main` eine belegt). Erst `main` ziehen, dann die Zeile `oil_burner` in `config.yaml`, dann das C.M.I. Nichts wird geschaltet; der Brenner wird nur gelesen.
12. **Nicht in diesem Plan:** Brennerstatus auf der System-Seite oder in HA, eine Meldung bei auffälligem Ölverbrauch, Füllstand des Tanks, ein Modell für Vorlauftemperatur oder Puffer, eine Hochrechnung auf ein ganzes Jahr in der App (das macht der Investitionsrechner mit dem Wetter seines Rechenjahrs), Änderungen am Planer.

## Was du an der Anlage tust (nach dem Ausrollen)

1. Im C.M.I. unter CoE-Ausgänge den Brennerstatus der UVR als **Digital 6** an BindaEMS (192.168.82.20, Knoten wie die anderen Werte) hinzufügen, Sendebedingung „bei Änderung“ mit kurzer Blockierzeit (etwa 10 s), Intervall wie die anderen Werte.
2. In `config.yaml` unter `thermal.devices.klima.receive`: `oil_burner: {node: 56, digital: 6}`; ems-core neu starten (Regelbetrieb vorher aus, wie bei jeder core-Änderung).
3. Mit `coe_probe listen` (core gestoppt) oder in Grafana prüfen, dass der Wert ankommt und beim Brennerstart auf 1 springt.
4. Unter Einstellungen → Ölheizung 27 kW und 2,9 l/h eintragen, optional den Ölpreis der letzten Lieferung.
5. Gegenprobe über den Winter: die gerechneten Liter mit dem Tank vergleichen (Lieferschein oder Messstab).

## Global Constraints

- Texte deutsch, Anrede „du“, Zahlen mit Komma (`ui/src/lib/format.ts`); Energie „kWh“ mit 1 Nachkommastelle, Liter mit 1, Stunden mit 1, Euro mit 2, Heizgradtage „K·d“ mit 1, R² mit 2.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy --strict` (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators), UI `pnpm check`, `pnpm lint`, `pnpm test`; E2E lokal mit `launchOptions.executablePath: /opt/pw-browsers/chromium`. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Tests, die `AppRuntime` bauen, legen `data_dir` unter `tmp_path`.
- InfluxQL nur mit `quote_ident`/`quote_str`.
- Breite 360 px ohne seitliches Scrollen.
- Commits deutsch im Stil `feat(app): …`, keine Modellbezeichnungen. Kein Aktor wird live, nichts an der Anlage geschaltet.

## Review Focus

- **Ausfall des C.M.I. bei laufendem Brenner:** Kommt eine Stunde lang kein Paket, während `status` zuletzt „an“ zeigt, zählt diese Stunde nicht als Brennerzeit, sondern als unbekannt; der Tag ist dann nicht vollständig und fällt aus der Regression. Test in Task 3.
- **Brenner seit über 90 Tagen aus** (Sommer): Ohne Wert in `status` gilt der gespeicherte Endzustand des Vortags; fehlt auch der, ist der Tag unbekannt, nicht 0 h. Test in Task 3.
- **Zeitumstellung:** Ein Tag mit 25 h (25.10.2026) und einer mit 23 h (29.03.2026) rechnen Tageslänge, Vollständigkeit und Heizgradtage über `local_day_slots`. Test in Task 3.
- **Nennleistung noch leer:** Karten zeigen Brennerstunden, Wärme und Öl als „–“ mit „Nennleistung fehlt“, die Regression sagt dasselbe; kein Absturz, keine 0 kWh. Test in Task 2 (reine Rechnung) und Task 5 (Karte).
- **Ohne `oil_burner` in `config.yaml`** (heute an der Anlage): keine Tage, `heating: null` in der Statistik, `GET /api/heating/demand` antwortet 404, die Karten fehlen, die Einstellungen zeigen die Karte „Ölheizung“ trotzdem (Eintragen vorab geht). Test in Task 4 und Task 5.

---

### Task 1: Brennerstatus empfangen (core, shared, Simulator)

**Files:**
- Modify: `src/bindaems/shared/config.py` (`CoeReceive`)
- Modify: `src/bindaems/tools/sim/model.py` (`_value`, Zustand `oil_burner_on`)
- Modify: `deploy/config.example.yaml` (Empfangstabelle, auskommentierte Zeile)
- Test: `tests/unit/shared/test_config.py`, `tests/unit/core/adapters/test_coe.py`, `tests/unit/core/telemetry/test_sampler.py`, `tests/unit/tools/sim/test_sim_model.py`

**Interfaces:**
- Produces: `CoeReceive.oil_burner: CoeChannel | None = None` (nur `digital`); Signal `thermal.<name>.oil_burner` (bool, `SignalKind.STATE`), in InfluxDB `status` mit `id=thermal.<name>.oil_burner`, Feld `value_bool`. Konstante `OIL_BURNER_SIGNAL: Final = "oil_burner"` in `shared/config.py`, die die app nutzt.

- [ ] **Step 1: Failing tests schreiben**

```python
# tests/unit/shared/test_config.py
def test_oil_burner_is_optional_and_digital() -> None:
    receive = CoeReceive.model_validate(RECEIVE | {"oil_burner": {"node": 56, "digital": 6}})
    assert receive.channels()["oil_burner"].digital == 6
    assert "oil_burner" not in CoeReceive.model_validate(RECEIVE).channels()

def test_oil_burner_must_be_digital() -> None:
    with pytest.raises(ValidationError, match="Digitalwert"):
        CoeReceive.model_validate(RECEIVE | {"oil_burner": {"node": 56, "analog": 6, "unit": 1}})

def test_oil_burner_must_not_share_a_value() -> None:
    with pytest.raises(ValidationError, match="running und oil_burner"):
        CoeReceive.model_validate(RECEIVE | {"oil_burner": {"node": 56, "digital": 5}})
```

```python
# tests/unit/core/adapters/test_coe.py
async def test_oil_burner_is_written_as_state() -> None:
    # Paket mit den Werten der Tabelle plus Digital 6 = 1
    ...
    reading = store.get("thermal.klima.oil_burner")
    assert reading.value is True and reading.kind is SignalKind.STATE
```

```python
# tests/unit/core/telemetry/test_sampler.py
def test_oil_burner_goes_to_status() -> None:
    point = signal_to_point("thermal.klima.oil_burner", Reading(True, TS, SignalKind.STATE))
    assert point == Point("status", {"id": "thermal.klima.oil_burner"}, {"value_bool": True}, TS)
```

```python
# tests/unit/tools/sim/test_sim_model.py
def test_sim_sends_oil_burner() -> None:
    # Gerät mit oil_burner; model.oil_burner_on = True → Paket enthält Digital 6 = 1
```

- [ ] **Step 2: Tests laufen lassen, sie scheitern** (`oil_burner` unbekannt, `extra="forbid"`; im Simulator `KeyError`).

- [ ] **Step 3: Umsetzen**

`oil_burner: CoeChannel | None = None` in `CoeReceive` mit Docstring „Brennerstatus der Ölheizung (nur gelesen, Plan Ölheizung)“; ein Validator lehnt `analog` für `oil_burner` ab („oil_burner muss ein Digitalwert sein.“). Der Adapter braucht keine Änderung (er schreibt jeden Kanal, Nicht-Messwerte als Zustand); der Test belegt es. Im Simulator ein Attribut `oil_burner_on: bool = False` und `"oil_burner": self.oil_burner_on` in `flags`. In `config.example.yaml` unter `running` die Zeile `# oil_burner: {node: 56, digital: 6}   # Brennerstatus der Ölheizung (Plan Ölheizung), nur gelesen`.

- [ ] **Step 4: Tests grün, Prüfkette, Commit** `feat(core): Brennerstatus der Ölheizung über CoE empfangen`

### Task 2: Einstellungen und reine Auswertung

**Files:**
- Modify: `src/bindaems/shared/settings.py` (`HeatingSettings`, Feld `RuntimeSettings.heating`)
- Create: `src/bindaems/app/heating/__init__.py`, `src/bindaems/app/heating/fit.py`
- Test: `tests/unit/shared/test_settings.py`, `tests/unit/app/heating/test_fit.py`

**Interfaces:**
- Produces:
  - `HeatingSettings(burner_kw: float | None = None (ge 1, le 100), oil_l_per_h: float | None = None (gt 0, le 20), oil_price_eur_l: float | None = None (ge 0, le 5), ac_cop: float = 3.0 (ge 1, le 8))`, alle `allow_inf_nan=False`; `RuntimeSettings.heating: HeatingSettings = Field(default_factory=HeatingSettings)`.
  - `HEATING_LIMIT_C: Final = 15.0`, `FIT_MIN_DAYS: Final = 14`, `FIT_MIN_HDD: Final = 1.0`, `COMPLETE_SHARE: Final = 0.95`.
  - `@dataclass(frozen=True) HeatDay(day: date, burner_h: float | None, known_h: float, day_h: float, burner_end: bool | None, hdd_kd: float | None, outdoor_mean_c: float | None, ac_heat_el_kwh: float | None, note: str | None)` mit Property `complete: bool` (`burner_h is not None and known_h >= COMPLETE_SHARE * day_h`).
  - `@dataclass(frozen=True) DayHeat(oil_heat_kwh: float | None, oil_l: float | None, oil_eur: float | None, ac_heat_kwh: float | None, demand_kwh: float | None)` und `def day_heat(day: HeatDay, settings: HeatingSettings, *, ac_power_known: bool) -> DayHeat`.
  - `@dataclass(frozen=True) HeatFit(per_kd: float, base_kwh_day: float, r2: float, n: int, through_origin: bool)` und `def fit_demand(days: Sequence[HeatDay], settings: HeatingSettings, *, ac_power_known: bool) -> HeatFit | str` (Text = Grund ohne Ergebnis).
  - `def season_of(day: date) -> tuple[date, date, str]` → (1.7., 30.6., `"2026/27"`).

- [ ] **Step 1: Failing tests schreiben** (`tests/unit/app/heating/test_fit.py`)

```python
S = HeatingSettings(burner_kw=27, oil_l_per_h=2.9, oil_price_eur_l=1.1, ac_cop=3.0)

def test_day_heat_from_burner_hours() -> None:
    heat = day_heat(day(burner_h=4.0, ac=2.0), S, ac_power_known=True)
    assert heat.oil_heat_kwh == pytest.approx(108.0)
    assert heat.oil_l == pytest.approx(11.6)
    assert heat.oil_eur == pytest.approx(12.76)
    assert heat.ac_heat_kwh == pytest.approx(6.0)
    assert heat.demand_kwh == pytest.approx(114.0)

def test_day_heat_without_nominal_power_is_unknown() -> None:
    heat = day_heat(day(burner_h=4.0), HeatingSettings(), ac_power_known=True)
    assert heat.oil_heat_kwh is None and heat.oil_l is None and heat.demand_kwh is None

def test_without_ac_power_demand_is_oil_only() -> None:
    assert day_heat(day(burner_h=1.0, ac=None), S, ac_power_known=False).demand_kwh == 27.0

def test_fit_recovers_base_and_slope() -> None:
    days = [day(hdd=h, burner_h=(10 + 4.5 * h) / 27, ac=0.0) for h in range(2, 18)]
    fit = fit_demand(days, S, ac_power_known=True)
    assert isinstance(fit, HeatFit)
    assert fit.base_kwh_day == pytest.approx(10.0)
    assert fit.per_kd == pytest.approx(4.5)
    assert fit.r2 == pytest.approx(1.0) and fit.n == 16 and not fit.through_origin

def test_fit_skips_incomplete_warm_and_unknown_days() -> None:
    days = [*good_days(14), day(hdd=0.5), day(hdd=None), day(known_h=20.0, day_h=24.0)]
    assert fit_demand(days, S, ac_power_known=True).n == 14

def test_fit_needs_14_days() -> None:
    assert fit_demand(good_days(13), S, ac_power_known=True) == "Noch 13 von 14 Tagen."

def test_fit_negative_base_goes_through_origin() -> None:
    days = [day(hdd=h, burner_h=max(0.0, 5 * h - 8) / 27) for h in range(2, 18)]
    fit = fit_demand(days, S, ac_power_known=True)
    assert fit.through_origin and fit.base_kwh_day == 0.0

def test_fit_without_nominal_power() -> None:
    assert fit_demand(good_days(20), HeatingSettings(), ac_power_known=True) == "Nennleistung fehlt."

def test_fit_without_spread_in_cold() -> None:
    # alle Tage gleich kalt → keine Steigung bestimmbar
    assert fit_demand([day(hdd=8.0)] * 20, S, ac_power_known=True) == "Die Tage waren alle gleich kalt."

def test_season_bounds() -> None:
    assert season_of(date(2026, 11, 9)) == (date(2026, 7, 1), date(2027, 6, 30), "2026/27")
    assert season_of(date(2027, 6, 30))[2] == "2026/27"
    assert season_of(date(2027, 7, 1))[2] == "2027/28"
```

Dazu in `tests/unit/shared/test_settings.py`: Standardwerte (`burner_kw is None`, `ac_cop == 3.0`), Grenzen (`burner_kw=0` und `ac_cop=0.5` abgelehnt), alte gespeicherte Einstellungen ohne `heating` laden mit Standardwerten.

- [ ] **Step 2: Tests laufen lassen, sie scheitern.**

- [ ] **Step 3: Umsetzen.** Regression mit `math.fsum` (keine neue Abhängigkeit): `b = Σ(x−x̄)(y−ȳ) / Σ(x−x̄)²`, `a = ȳ − b·x̄`; bei `a < 0` `b = Σxy / Σx²`, `a = 0`, `through_origin=True`; `R² = 1 − SS_res/SS_tot` (bei `SS_tot = 0` → 1,0). Ohne Streuung (`Σ(x−x̄)² < 1e-9`) der Grund „Die Tage waren alle gleich kalt.“. Ein Tag zählt, wenn `complete`, `hdd_kd is not None`, `hdd_kd >= FIT_MIN_HDD` und `demand_kwh is not None`.

- [ ] **Step 4: Tests grün, Prüfkette, Commit** `feat(app): Wärmebedarf aus Brennerstunden und Heizgradtagen rechnen`

### Task 3: Heizungstage aus InfluxDB rechnen und speichern

**Files:**
- Create: `src/bindaems/app/history/steps.py` (aus `thermal_data._Steps` und `_steps` herausgelöst)
- Modify: `src/bindaems/app/learning/thermal_data.py` (nutzt `steps.py`, Verhalten unverändert)
- Modify: `src/bindaems/app/db/schema.py` (`heating_day`)
- Create: `src/bindaems/app/db/migrations/versions/v0019_heating_day.py`
- Create: `src/bindaems/app/heating/days.py`
- Test: `tests/unit/app/history/test_steps.py`, `tests/unit/app/heating/test_days.py`, `tests/unit/app/test_db.py` (Kopfrevision 0019)

**Interfaces:**
- Consumes: `HeatDay`, `HEATING_LIMIT_C` (Task 2); `read_thermal_slots` (`app/learning/thermal_data.py`); `WeatherArchive.temperatures`; `local_day_slots`; `OIL_BURNER_SIGNAL` (Task 1).
- Produces:
  - `StatusSteps(seed: bool | None, times: list[datetime], values: list[bool])` mit `share(start, end) -> float | None` (wie bisher) und `async def read_status_steps(reader: InfluxReader, ids: Sequence[str], start: datetime, end: datetime) -> dict[str, StatusSteps]` (Schlüssel = `id`; ohne Wert vor dem Fenster `seed=None`). `thermal_data._steps` ruft sie mit den Bits der Klimaanlage auf; Fehler werfen weiter `InfluxQueryError`, `thermal_data` übersetzt wie bisher in `ThermalDataError`.
  - Tabelle `heating_day`: `day String(10) PK`, `burner_h Float null`, `known_h Float not null`, `day_h Float not null`, `burner_end Boolean null`, `hdd_kd Float null`, `outdoor_mean_c Float null`, `ac_heat_el_kwh Float null`, `note String(80) null`.
  - `class HeatingDays(engine: Engine, reader: InfluxReader, weather: WeatherArchive, device: str, clock: Clock)` mit
    - `async def compute(self, day: date, seed: bool | None) -> HeatDay` (rechnet, schreibt nichts; auch für heute bis jetzt),
    - `async def fill_missing(self, today: date, days: int) -> int` (aufsteigend, nur fehlende Tage vor heute, gibt die Zahl gespeicherter Tage zurück),
    - `def rows(self, first: date, last: date) -> list[HeatDay]` (synchron, Aufrufer nehmen `asyncio.to_thread`),
    - `async def period(self, first: date, last: date) -> list[HeatDay]` (gespeicherte Tage plus heute bis jetzt, wenn `last >= today`).

- [ ] **Step 1: Failing tests schreiben** (`tests/unit/app/heating/test_days.py`, InfluxDB über den Fake-Reader der bestehenden `thermal_data`-Tests, Wetterarchiv auf SQLite in `tmp_path`)

```python
async def test_burner_hours_from_status_steps() -> None:
    # Brenner 06:00–08:00 und 18:00–19:30 an, Quelle lebt den ganzen Tag, 24 h Wetter
    heat_day = await days.compute(DAY, seed=False)
    assert heat_day.burner_h == pytest.approx(3.5)
    assert heat_day.known_h == pytest.approx(24.0) and heat_day.complete
    assert heat_day.burner_end is False

async def test_dead_source_is_unknown_not_burning() -> None:
    # status: an ab 06:00, danach kein Wert; actual_c fehlt 07:00–08:00
    heat_day = await days.compute(DAY, seed=False)
    assert heat_day.known_h == pytest.approx(23.0)
    assert heat_day.burner_h == pytest.approx(17.0)   # 06–07 und 08–24
    assert not heat_day.complete

async def test_seed_from_stored_previous_day() -> None:
    # kein status-Wert in 90 Tagen; Vortag direkt in heating_day mit burner_end=False, TODAY = DAY + 1
    assert await days.fill_missing(TODAY, 1) == 1
    assert days.rows(DAY, DAY)[0].burner_h == 0.0

async def test_no_seed_and_no_change_is_unknown() -> None:
    heat_day = await days.compute(DAY, seed=None)
    assert heat_day.burner_h is None and heat_day.note == "Brennerstatus fehlt"

async def test_heating_degree_days_base_15() -> None:
    # 96 Viertelstunden mit 5 °C → 10 K·d; mit 20 °C → 0
    assert (await days.compute(DAY, seed=False)).hdd_kd == pytest.approx(10.0)

async def test_heating_degree_days_need_90_percent_of_slots() -> None:
    # nur 80 von 96 Viertelstunden im Wetterarchiv
    assert (await days.compute(DAY, seed=False)).hdd_kd is None

@pytest.mark.parametrize(("day", "hours"), [(date(2026, 10, 25), 25.0), (date(2026, 3, 29), 23.0)])
async def test_dst_day_length(day: date, hours: float) -> None:
    heat_day = await days.compute(day, seed=False)
    assert heat_day.day_h == hours and heat_day.complete

async def test_ac_heating_energy_counts_only_heating_slots() -> None:
    # 4 Slots heizen à 2000 W, 4 Slots kühlen à 1500 W → 2,0 kWh
    assert (await days.compute(DAY, seed=False)).ac_heat_el_kwh == pytest.approx(2.0)

async def test_fill_is_ascending_and_skips_stored_and_today() -> None: ...

async def test_read_error_writes_nothing() -> None:
    # Reader wirft InfluxQueryError → 0 gespeichert, Tag fehlt weiter
```

Für `steps.py` die bestehenden Fälle von `_Steps.share` als Tests von `StatusSteps.share` (Anteil, Anfang unbekannt, Wechsel genau an der Grenze); die Tests von `thermal_data` bleiben unverändert grün.

- [ ] **Step 2: Tests laufen lassen, sie scheitern.**

- [ ] **Step 3: Umsetzen.** Je Viertelstunde des Tags (`local_day_slots`): lebt die Quelle (eigener Query `count("actual_c")` aus `thermal` für das Gerät je 15 min, größer 0; nicht über `read_thermal_slots`, denn der lässt Slots ohne Wetter aus, und die sollen nicht als Ausfall zählen), zählt `share × ¼ h` als Brennerzeit und `¼ h` als bekannt; ein Slot mit `share is None` ist unbekannt. Anfangswert: `read_status_steps` (letzter Wert vor dem Tag), sonst der übergebene `seed`. `burner_end` ist der Zustand am Tagesende, wenn bekannt. Heizgradtage und Tagesmittel aus `WeatherArchive.temperatures` (Entscheidung 5). Klima-Strom aus `read_thermal_slots` (Entscheidung 7); fehlt dem Gerät `power_kw`, bleibt er `None`. `fill_missing` liest den Endzustand des vorigen Tags aus der Tabelle bzw. aus dem eben gerechneten Tag. Kein Brennerwert und kein Seed: Zeile mit `burner_h=None`, `known_h=0`, `note="Brennerstatus fehlt"`. Ein Fehler beim Lesen (InfluxDB oder Wetterarchiv) bricht den Lauf ab, Gespeichertes bleibt.

- [ ] **Step 4: Tests grün, Prüfkette, Commit** `feat(app): Heizungstage mit Brennerstunden und Heizgradtagen speichern`

### Task 4: Laufzeit, Statistik und API

**Files:**
- Modify: `src/bindaems/app/runtime.py` (Aufbau, Nachholen beim Start und nachts)
- Modify: `src/bindaems/app/stats/service.py` (Block `heating`)
- Create: `src/bindaems/app/heating/routes.py`; Modify: die Stelle, an der `learning/routes.py` eingehängt wird
- Test: `tests/unit/app/heating/test_routes.py`, `tests/unit/app/stats/test_service.py`, `tests/integration/test_app_runtime.py`

**Interfaces:**
- Consumes: `HeatingDays` (Task 3), `day_heat`, `fit_demand`, `season_of` (Task 2), `thermal_device(cfg)`.
- Produces:
  - `AppRuntime.heating_days: HeatingDays | None` (nur mit Gerät `coe_offset` **und** `receive.oil_burner`); beim Start `fill_missing(today, FILL_DAYS)`, nachts in `_ledger_nightly` `fill_missing(today, CONSUMER_DAYS_NIGHTLY)`, je mit Log „Heizungstage gespeichert“ und Fehler ins Log wie bei den Verbrauchern.
  - In `GET /api/stats` das Feld `heating`: `null` ohne Brenner, sonst `{"burner_h", "oil_heat_kwh", "oil_l", "oil_eur", "ac_heat_el_kwh", "ac_heat_kwh", "demand_kwh", "hdd_kd", "days", "complete_days", "first_day", "settings_missing": bool, "ac_power_known": bool}`; Summen nur über bekannte Werte (`_sum_known`), `first_day` = erster Tag mit Brennerwert im Zeitraum.
  - `GET /api/heating/demand?tag=YYYY-MM-DD` (Viewer; ohne Brenner 404 „Kein Brennerstatus eingerichtet.“): `{"season": "2026/27", "first", "last", "points": [{"day", "hdd_kd", "outdoor_mean_c", "burner_h", "oil_heat_kwh", "oil_l", "ac_heat_el_kwh", "ac_heat_kwh", "demand_kwh", "complete", "used", "note"}], "fit": {"per_kd", "base_kwh_day", "r2", "n", "through_origin"} | null, "reason": str | null, "totals": {"oil_heat_kwh", "oil_l", "oil_eur", "ac_heat_el_kwh", "demand_kwh", "hdd_kd"}, "ac_by_month": {"2026-11": kWh Strom}, "heating_limit_c": 15.0, "ac_cop": float}`.

- [ ] **Step 1: Failing tests schreiben**

```python
# tests/unit/app/stats/test_service.py
def test_heating_block_is_null_without_burner() -> None:
    assert service.period("month", DAY)["heating"] is None

def test_heating_block_sums_known_days() -> None:
    # zwei gespeicherte Tage 3,0 h und 2,0 h, ein Tag ohne Brennerwert, 27 kW, 2,9 l/h
    heating = service.period("week", DAY)["heating"]
    assert heating["burner_h"] == pytest.approx(5.0)
    assert heating["oil_heat_kwh"] == pytest.approx(135.0)
    assert heating["oil_l"] == pytest.approx(14.5)
    assert heating["days"] == 3 and heating["complete_days"] == 2

def test_heating_block_without_nominal_power() -> None:
    heating = service_without_kw.period("week", DAY)["heating"]
    assert heating["settings_missing"] and heating["oil_heat_kwh"] is None
```

```python
# tests/unit/app/heating/test_routes.py
def test_demand_404_without_burner(client_without_burner) -> None: ...
def test_demand_returns_season_points_and_fit(client) -> None:
    body = client.get("/api/heating/demand?tag=2026-12-15").json()
    assert body["season"] == "2026/27" and body["fit"]["n"] >= 14
    assert body["ac_by_month"]["2026-12"] == pytest.approx(...)
def test_demand_without_enough_days_has_reason(client) -> None:
    assert body["fit"] is None and body["reason"] == "Noch 5 von 14 Tagen."
def test_demand_requires_login(anon_client) -> None: ...
```

```python
# tests/integration/test_app_runtime.py
async def test_runtime_builds_heating_days_only_with_oil_burner(tmp_path) -> None: ...
```

- [ ] **Step 2: Tests laufen lassen, sie scheitern.**
- [ ] **Step 3: Umsetzen.** `StatsService` bekommt `heating_days: HeatingDays | None` und `ac_power_known: bool` (`device.receive.power_kw is not None`); der Block nutzt `HeatingDays.period` über `asyncio.to_thread` wie die übrigen Tageswerte bzw. den async-Pfad, den `period` heute für Verbraucher nimmt. Der Router hängt wie `learning` mit `heating_days is None` → 404.
- [ ] **Step 4: Tests grün, Prüfkette, Commit** `feat(app): Heizung in Statistik und API`

### Task 5: UI – Karten „Heizung“ und „Wärmebedarf“, Einstellungen „Ölheizung“

**Files:**
- Modify: `ui/src/lib/api/schemas.ts` (Statistik `heating`, `HeatDemand`, Einstellungen `heating`), `ui/src/lib/api/endpoints.ts`
- Create: `ui/src/lib/heating/HeatingCard.svelte`, `ui/src/lib/heating/HeatDemandCard.svelte`, `ui/src/lib/heating/view.ts` (Texte, Chart-Option, CSV)
- Create: `ui/src/lib/settings/HeatingView.svelte`, `ui/src/lib/settings/HeatingEditor.svelte`; Modify: `SettingsEditor.svelte`, `ui/src/routes/einstellungen/+page.svelte`
- Modify: `ui/src/routes/statistik/+page.svelte` (nach `PvCard`)
- Test: `ui/src/lib/heating/view.test.ts`, `HeatingCard.test.ts`, `HeatDemandCard.test.ts`, `ui/src/lib/settings/HeatingEditor.test.ts`, `ui/tests/e2e/stats.spec.ts`, `ui/tests/e2e/settings.spec.ts`

**Interfaces:**
- Consumes: `heating` aus `GET /api/stats`, `GET /api/heating/demand?tag=` (Task 4); `settings.heating` (Task 2).
- Produces: `heatDemandOption(demand: HeatDemand, palette: Palette): EChartsOption` (Punkte „verwendet“ und „nicht verwendet“ in zwei Reihen, Gerade von 0 bis zum größten K·d), `heatingCsv(demand: HeatDemand): string` (Spalten wie die Ladesessions-CSV in `ui/src/lib/stats/view.ts`, Kopf: `Tag;Brennerstunden;Wärme Öl kWh;Öl l;Klima Strom heizen kWh;Klima Wärme kWh (geschätzt);Wärmebedarf kWh;Heizgradtage K·d;Außentemperatur °C;vollständig;in Auswertung;Hinweis`, Dezimalkomma), Dateiname `waermebedarf-2026-27.csv`.

Wörtliche Texte:
- Karte **„Heizung“** (Zeitraum der Statistik): Zeilen „Brenner lief“ ({h} h), „Wärme aus Öl“ ({kWh} kWh), „Öl“ ({l} l, mit Preis „ · {€} €“), „Klimaanlage heizt“ ({kWh} kWh Strom, „≈ {kWh} kWh Wärme“), „Wärmebedarf“ ({kWh} kWh), „Heizgradtage“ ({K·d} K·d, Heizgrenze 15 °C), „Vollständige Tage“ ({n} von {m}). Ohne Nennleistung: „Nennleistung fehlt: unter Einstellungen → Ölheizung eintragen.“ Liegen Tage vor dem ersten Brennerwert: „Brennerstatus ab {Datum}.“
- Karte **„Wärmebedarf“** (Heizsaison): Titelzeile „Heizsaison {2026/27}“; mit Ergebnis „Grundbedarf {a} kWh je Tag (vor allem Warmwasser)“, „Dazu {b} kWh je Heizgradtag“, „R² {r2} aus {n} Tagen“; bei Ursprung zusätzlich „Ohne Grundbedarf gerechnet (er wäre negativ).“; ohne Ergebnis der `reason` der API. Darunter „Bisher in dieser Saison: {kWh} kWh Wärme, {l} l Öl ({€} €), {K·d} Heizgradtage“ und eine kleine Tabelle „Klimaanlage heizt, Strom je Monat“. Hinweis unter dem Diagramm: „Die Wärme aus Öl ist Brennerlaufzeit × Nennleistung; Bereitschaftsverluste des Kessels sind darin enthalten. Die Wärme der Klimaanlage ist Strom × {cop} (Schätzung).“ Knopf „CSV herunterladen“.
- Einstellungen, Karte **„Ölheizung“**: „Nennleistung“ (kW), „Öl je Brennerstunde“ (l/h), „Ölpreis“ (€/l, leer = ohne Euro), „Leistungszahl der Klimaanlage beim Heizen“ (Standard 3,0, Hilfetext „Schätzung: Wärme je kWh Strom.“). Leer gelassen = „nicht eingetragen“.

- [ ] **Step 1: Failing tests schreiben** (Vitest): Option hat zwei Punktreihen und eine Linie mit den Werten der Fit-Antwort; CSV mit Dezimalkomma und Kopf wie oben; `HeatingCard` zeigt „Nennleistung fehlt…“ bei `settings_missing`, „–“ statt 0 bei `null`; Statistik-Seite ohne `heating` zeigt keine der beiden Karten; Editor sendet `heating` mit `null` für leere Felder und lehnt 0 kW ab. E2E: Statistik mit Brenner (Fixture der API) zeigt beide Karten bei 360 px ohne seitliches Scrollen; Einstellungen speichern 27 / 2,9.
- [ ] **Step 2: Tests laufen lassen, sie scheitern.**
- [ ] **Step 3: Umsetzen** nach den bestehenden Karten (`PvCard`, `VehicleTrendCard` für ECharts mit `readPalette`) und Editoren (`ThermalPlanEditor`/`ThermalPlanView`).
- [ ] **Step 4: `pnpm check`, `pnpm lint`, `pnpm test`, E2E grün; Commit** `feat(ui): Karten Heizung und Wärmebedarf, Einstellungen Ölheizung`

### Task 6: Dokumentation und Übergabe

**Files:**
- Modify: `docs/betrieb.md` (neuer Abschnitt „Ölheizung: Wärmebedarf“ nach „Vorheizen und Vorkühlen (Phase 5c)“: C.M.I.-Zeile, `config.yaml`, Einstellungen, was die Karten zeigen, Gegenprobe am Tank)
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Phase 6: Absatz „**Stand Plan Ölheizung**“ mit Entscheidungen 1, 2, 5, 6, 7 und der Tabelle `heating_day`; Empfangstabelle: „optional Digital 6 Brennerstatus der Ölheizung“)
- Modify: `docs/uebergabe.md` (Kopfblock und Punkt 26 unter „Offen an der Anlage“ mit den fünf Schritten aus „Was du an der Anlage tust“; Migration 0019; Stufe 2)
- Test: `tests/unit/test_docs.py` (bestehende Prüfungen bleiben grün)

- [ ] **Step 1:** Texte schreiben, `pytest tests/unit/test_docs.py` grün.
- [ ] **Step 2: Gesamte Prüfkette einmal, Commit** `docs: Ölheizung Wärmebedarf in Betrieb, Spec und Übergabe`

## Offene Fragen an dich (beeinflussen nur die Auslegung, nicht den Code)

- **Ist der Brenner einstufig?** Der Plan nimmt es an (immer 27 kW, wenn er läuft). Bei einem zweistufigen Brenner wären die Zahlen eine obere Grenze; die Gegenprobe am Tank zeigt es.
- **Welcher Ausgang der UVR trägt den Brennerstatus?** Nur für deine Zeile im C.M.I.; BindaEMS braucht nur, dass er als Digital 6 (oder eine andere freie Nummer, dann in `config.yaml` anpassen) ankommt.

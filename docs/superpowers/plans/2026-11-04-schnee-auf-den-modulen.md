# Schnee auf den Modulen Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Liegt Schnee auf den Modulen, kürzt die app die PV-Prognose, bis die Messung zeigt, dass die Module wieder liefern. So plant der Planer an Schneetagen das Netzladen in der billigen Nacht ein, statt am Abend teuer zu beziehen, und die Kalibrierung lernt nichts aus Schneetagen.

**Architecture:** Nur app und UI. Open-Meteo liefert mit dem Abruf der ersten Fläche zusätzlich stündlich Schneefall und Schneehöhe (kein zusätzlicher Abruf). Ein neues reines Modul `app/forecast/snow.py` entscheidet aus Wetter, Ist-PV der Abrechnung und ungekürzter Prognose ohne eigenen Zustand, ob die Module bedeckt sind, und gibt je Slot einen Faktor. Der `ForecastService` wendet ihn auf P25/P50/P75 an und prüft ihn alle 15 min neu; der Planer bekommt die gekürzte Prognose und einen Grund je Slot. core, `config.yaml` und Datenbank bleiben unverändert.

**Tech Stack:** Python 3.12, FastAPI, pydantic, pytest; SvelteKit 2 (Svelte 5), valibot, ECharts, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, 6.8 „PV-Prognose“, 9.3 „Optimierungsmodell“ (Eingang PV), 9.6 „Erklärungen“. Idee 2.1 aus `/mnt/project-files/ideen/brainstorming-2026-11.md`.

**Basis:** Branch `claude/project-thread-a3ghby` ab `102628d` (Stand „Netzgrenze je Phase“, Branch `claude/project-thread-ambh18`). Kommt jener vor diesem Plan auf `main`, wird dieser Branch vor der Umsetzung auf `main` rebased.

## Anlass

Liegt Schnee auf den Modulen, liefert die PV fast nichts, obwohl Open-Meteo Sonne zeigt: Die Strahlungsprognose kennt Wolken, aber nicht die Schneedecke auf dem Dach. Der Planer rechnet dann mit PV, die nicht kommt, verzichtet in der Nacht auf Netzladen und bezieht am Abend zum Spitzenpreis. Das trifft genau die Wintertage, an denen Strom teuer ist. Dazu zieht jeder Schneetag die Kalibrierung (Median von Ist / roh über 30 Tage) nach unten, sodass die Prognose danach auch an schneefreien Tagen zu niedrig ist. Ziel ist, vor Mitte Dezember 2026 fertig zu sein.

## Entscheidungen

1. **Eine Kürzung für die ganze Anlage.** Die app kennt die Ist-PV nur als Summe (Victron-Zähler in der Rolle PV-Wechselrichter); Werte je Fläche aus Huawei über HA sind nicht eingerichtet (Spec 6.9, „optional“). Süd und West bekommen denselben Faktor. Je Fläche kommt erst mit Idee 11.3 (Flächen einzeln überwachen).
2. **Wetter:** `fetch_plane` fragt für die **erste** Fläche zusätzlich `hourly=snowfall,snow_depth` ab (Schneefall in cm je vorangehender Stunde, Schneehöhe am Boden in m; der Standort ist für alle Flächen gleich). Ein Abruf mit 6 statt 4 Variablen zählt bei Open-Meteo weiter als einer (≤ 10 Variablen). Fehlt der Block oder hat er andere Einheiten, gibt es keine Schneeerkennung, die Prognose läuft unverändert weiter (keine Fehlermeldung, ein Log `info`).
3. **Zustandslos:** `snow_cut` rechnet bei jedem Aufruf aus Wetter (24 h zurück, 3 Tage voraus), den vollständigen Abrechnungs-Slots der letzten 36 h und der ungekürzten P50 der aktuellen Prognose (die mit `past_days=1` den Vortag enthält). Nach einem Neustart der app kommt dasselbe heraus; es gibt nichts zu speichern (außer dem Handschalter, Entscheidung 9).
4. **Regeln** (Konstanten in `snow.py`):
   - *Messbar* ist ein vergangener Slot mit Ist-Wert und ungekürzter P50 ≥ `MEASURE_MIN_W = max(300 W, 5 % der Summe der kWp)` (an der Anlage 9,8 kWp → 490 W). Sein Verhältnis ist `r = Ist / P50 ungekürzt`.
   - *Schneefall* ist eine Stunde, in deren 12-h-Fenster (diese und die 11 davor) mindestens `SNOW_EVENT_CM = 1.0` cm fallen und die selbst mindestens `SNOW_HOUR_MIN_CM = 0.2` cm hat.
   - *Frei* gemessen: zwei aufeinanderfolgende messbare Slots mit `r ≥ FREE_RATIO = 0.6`; „frei seit“ ist das Ende des zweiten.
   - *Bedeckt* gilt jetzt, wenn (a) es seit dem letzten „frei seit“ Schneefall gab (Stunde ≤ jetzt), oder (b) ohne Schneefall im Wetter drei aufeinanderfolgende messbare Slots nach dem letzten „frei seit“ `r < COVERED_RATIO = 0.15` haben, die Lufttemperatur in diesen Slots ≤ `COLD_C = 3.0` °C ist und die letzte vergangene Schneehöhe ≥ `DEPTH_MIN_M = 0.01` m (Schnee am Boden: Fall verpasster Prognose, Neustart zwei Tage nach dem Schneefall). Ohne Schnee am Boden ist ein Ausfall kein Schnee; den finden Betriebscheck und Auffälligkeiten (Ideen 1.1, 2.4).
   - *Faktor* bei „bedeckt“: Median von `r` der letzten bis zu 4 messbaren Slots seit Beginn der Bedeckung, begrenzt auf `[COVERED_FACTOR, 1]` mit `COVERED_FACTOR = 0.1`; mit weniger als 2 solchen Slots (Nacht, früher Morgen) `COVERED_FACTOR`. So folgt die Kürzung dem Abtauen.
   - Zustand: `frei`, `vermutet` (bedeckt ohne messbaren Slot seit Beginn der Bedeckung) oder `gemessen` (bedeckt mit mindestens 2 solchen Slots).
5. **Wie weit voraus gekürzt wird** — *offen, Karte an den Betreiber; Vorschlag A:* Ist die Anlage jetzt bedeckt, gilt der Faktor für alle Slots ab jetzt bis zum Ende des **nächsten** lokalen Tages. Für künftigen Schneefall (erste Stunde `t_n` > jetzt) gilt `COVERED_FACTOR` ab `t_n` bis zum Ende des Tages nach dem Tag von `t_n`. Liegen beide Fenster übereinander, gilt der kleinere Faktor. Danach und für alle anderen Slots 1. (Alternativen: B „nur der laufende Tag und der Schneetag selbst“; C „bis zum ersten Tag mit Tauwetter laut Prognose, höchstens 3 Tage“.)
6. **Kürzung:** P50, P25 und P75 werden mit dem Faktor des Slots multipliziert, `raw_w` bleibt ungekürzt. Ins Archiv geht wie bisher die ausgegebene (gekürzte) P50: Die Güte zeigt, womit der Planer gerechnet hat. In InfluxDB stehen die gekürzten Werte.
7. **Kalibrierung ohne Ausreißertage:** `recalibrate` lässt jeden lokalen Tag aus, an dem mindestens `OUTLIER_SLOTS = 4` Paare (`h0_6`, `raw_w` ≥ Schwelle) `Ist < COVERED_RATIO · raw_w` haben, für Faktoren und Bänder. Das fasst Schneetage (auch die vor diesem Plan) und Ausfälle, ohne dass etwas gespeichert werden muss.
8. **Planer:** `SlotInput.pv_snow_pct` (Anteil der ungekürzten Prognose in %, `None` = nicht gekürzt) für jeden gekürzten Slot mit ungekürzter P50 ≥ `MEASURE_MIN_W`; neuer Grundcode `PV_SCHNEE` vor `GESCHAETZT`, Satz wörtlich: „PV gekürzt auf {pct} % der Prognose: vermutlich Schnee auf den Modulen.“ Ändert die 15-min-Prüfung den Faktor eines Slots um mehr als `CHANGE_MIN = 0.05` oder den Zustand, bekommt die Prognose eine neue `revision`; der Auslöser `pv_forecast` vergleicht künftig `revision` statt `issued_at`, der Planer rechnet also binnen 15 min neu.
9. **Handschalter** — *offen, Karte an den Betreiber; Vorschlag: ja.* Zwei Knöpfe auf der Seite „Prognosen“ (Rolle Bedienen) und in HA: „Schnee auf den Modulen“ (gilt als bedeckt ab jetzt, bis die Messung frei meldet) und „Module sind frei“ (gilt als frei gemessen ab jetzt; neuer Schneefall danach zählt wieder). Ein Knopf wirkt höchstens 48 h. Abgelegt in `data_dir/pv_snow_override.json` (atomar, `{"mode": "covered" | "free", "at": ISO}`); ein Knopf ersetzt den anderen; ohne Datei oder bei unlesbarer Datei Automatik.
10. **Schalter:** `RuntimeSettings.pv_model.snow_detection: bool = True` (Einstellungen „PV-Modell“, „Schnee erkennen“). Aus: kein Faktor, Knöpfe ohne Wirkung, die Kalibrierung lässt Ausreißertage trotzdem aus.
11. **Anzeige:** Seite „Prognosen“ zeigt Zustand, Faktor und „seit“ als Karte und die ungekürzte P50 gestrichelt im Diagramm; die Übersicht einen Hinweis der Stufe `info` („PV-Prognose gekürzt: vermutlich Schnee auf den Modulen (seit 07:15)“); der Zeitplan den Grund `PV_SCHNEE`; HA einen Sensor `sensor.bindaems_pv_schnee` (`frei`/`vermutet`/`gemessen`, Attribut `faktor_pct`). Der Status der Komponente `forecast` bleibt grün und nennt die Kürzung.
12. **Ausrollen:** ems-app (core-Image unverändert, aber nach `sammel-pull.md` nie ems-app allein), ohne Migration, ohne Änderung an `config.yaml`. Bis 22.11.2026 nichts an der Anlage (Abnahmen). Ist der Plan bis dahin auf `main`, kommt er mit Stufe 2 (ab 23.11.), sonst mit dem nächsten Sammel-Pull Anfang Dezember.

## Global Constraints

- Keine Änderung an core, Safety-Schicht, Datenbankschema, `config.yaml` und Importvertrag.
- Neue Felder sind optional mit Standard (`None`, `False` oder `True` wie angegeben); gespeicherte `PlanInputs`, Pläne, Einstellungen und Szenariodateien Version 2 laden unverändert.
- Open-Meteo: weiter ein Abruf je Fläche und Stunde, höchstens 10 Variablen je Abruf.
- Texte deutsch, wörtlich wie in den Entscheidungen; Commits deutsch im Stil `feat(app): …`, keine Modellbezeichnungen im Repo.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy` strict (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators); UI `pnpm check`, `pnpm lint`, `pnpm test`; bei API-Änderungen Vertragsdateien neu (`UPDATE_UI_CONTRACT=1`), E2E. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.

## Review Focus

- **Neustart der app in der Nacht nach Schneefall:** dieselbe Kürzung wie vorher, ohne gespeicherten Zustand (Task 3, `test_restart_after_snowfall_keeps_cut`).
- **Open-Meteo ohne `hourly`-Block oder mit anderer Einheit:** Prognose wie heute, Komponente grün, kein Faktor (Task 1 und 3, `test_parse_snow_missing_block_is_empty`, `test_refresh_without_snow_data_is_uncut`).
- **Lücke in der Abrechnung** (core weg, Slot unvollständig): Lücken sind weder frei noch bedeckt, zwei messbare Slots um eine Lücke herum zählen nicht als „aufeinanderfolgend“ (Task 2, `test_gap_breaks_consecutive_slots`).
- **Ausfall des Wechselrichters ohne Schnee** (Ist 0, kein Schneefall, Schneehöhe 0): keine Kürzung, der Planer sieht weiter die volle Prognose (Task 2, `test_zero_pv_without_snow_is_not_snow`).
- **Abtauen am Nachmittag:** Sobald zwei Slots `r ≥ 0,6` haben, ist die Kürzung für den Rest des Tages und für morgen weg, und der Planer rechnet binnen 15 min neu (Task 2 `test_two_free_slots_lift_the_cut`, Task 3 `test_snow_check_bumps_revision_and_triggers_replan`).

---

### Task 1: Schneefall und Schneehöhe von Open-Meteo

**Files:**
- Modify: `src/bindaems/app/forecast/openmeteo.py` (`fetch_plane`, neu `SnowHour`, `parse_snow`)
- Test: `tests/unit/app/forecast/test_openmeteo.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class SnowHour: hour_start: datetime; snowfall_cm: float; snow_depth_m: float | None` — `hour_start` UTC, Beginn der Stunde, auf die sich der Schneefall bezieht (Wert zum Zeitpunkt T gehört zur Stunde ab T − 1 h, wie bei den 15-min-Werten).
  - `fetch_plane(http, clock, location, plane, *, snow: bool = False) -> RawResponse`; mit `snow=True` zusätzlich `"hourly": "snowfall,snow_depth"`.
  - `parse_snow(body: str) -> list[SnowHour]`: leer, wenn `hourly` fehlt, eine Variable fehlt oder die Einheit nicht `cm` (Schneefall) bzw. `m` (Schneehöhe) ist; Einträge mit `snowfall` `None` entfallen, `snow_depth` `None` bleibt `None`. Wirft nie (auch nicht bei kaputtem `hourly`); die 15-min-Prüfung von `parse_plane` bleibt unverändert.

- [ ] **Step 1: Failing tests**
  - `test_fetch_plane_asks_for_snow_only_when_wanted`: Parameter mit `snow=True` enthalten `hourly == "snowfall,snow_depth"`, ohne nicht; `minutely_15` unverändert.
  - `test_parse_snow_reads_hourly_block`: Antwort mit `hourly.time = [T, T+3600]`, `snowfall [0.4, 1.2]`, `snow_depth [0.02, null]`, Einheiten `cm`/`m` → `[SnowHour(T−1h, 0.4, 0.02), SnowHour(T, 1.2, None)]`.
  - `test_parse_snow_missing_block_is_empty`: ohne `hourly`, mit `hourly_units.snowfall == "mm"`, mit ungleich langen Listen → jeweils `[]`.
  - Vor dem Test einmal eine echte Antwort holen (`curl` mit den Parametern der Anlage aus `config.example.yaml`), wenn das Netz es zulässt, und Feldnamen und Einheiten daran prüfen; die Fixture im Test ist von Hand gekürzt.
- [ ] **Step 2:** `pytest tests/unit/app/forecast/test_openmeteo.py -q` → neue Tests scheitern.
- [ ] **Step 3:** umsetzen.
- [ ] **Step 4:** Tests grün, Prüfkette.
- [ ] **Step 5:** Commit `feat(app): Schneefall und Schneehöhe von Open-Meteo mit der ersten Fläche`.

### Task 2: Die Erkennung (rein)

**Files:**
- Create: `src/bindaems/app/forecast/snow.py`
- Test: `tests/unit/app/forecast/test_snow.py` (neu)

**Interfaces:**
- Consumes: `SnowHour` (Task 1)
- Produces:
  - `SnowState = Literal["frei", "vermutet", "gemessen"]`
  - `@dataclass(frozen=True) class Observation: slot_start: datetime; expected_w: float; actual_w: float; temp_c: float | None`
  - `@dataclass(frozen=True) class Override: mode: Literal["covered", "free"]; at: datetime`
  - `@dataclass(frozen=True) class SnowCut: state: SnowState; factor: float; since: datetime | None; factors: Mapping[datetime, float]` — `factors` nur für Slots mit Faktor < 1; `factor` der Faktor jetzt (1 bei `frei`).
  - `NO_CUT: Final = SnowCut("frei", 1.0, None, {})`
  - `measure_min_w(total_kwp: float) -> float`
  - `snow_cut(now: datetime, slots: Sequence[datetime], weather: Sequence[SnowHour], observations: Sequence[Observation], *, total_kwp: float, override: Override | None = None) -> SnowCut` — `slots` sind die Slotbeginne der Prognose; Regeln und Fenster nach Entscheidungen 4, 5 (Vorschlag A, oder was der Betreiber wählt) und 9. Ein `Override` älter als 48 h zählt nicht. `"covered"` wirkt wie Schneefall zur Zeit `at`, `"free"` wie „frei seit `at`“.
  - Konstanten wie in Entscheidung 4 (`MEASURE_MIN_SHARE = 0.05`, `MEASURE_MIN_FLOOR_W = 300.0`, `SNOW_EVENT_CM`, `SNOW_HOUR_MIN_CM`, `SNOW_WINDOW_H = 12`, `FREE_RATIO`, `COVERED_RATIO`, `COLD_C`, `DEPTH_MIN_M`, `COVERED_FACTOR`, `RATIO_SLOTS = 4`, `OVERRIDE_MAX = timedelta(hours=48)`).

- [ ] **Step 1: Failing tests** (Anlage 9,8 kWp, also `MEASURE_MIN_W = 490`; Tage im Dezember 2026, Ortszeit Wien; Hilfen `hours(...)`, `obs(...)`):
  - `test_measure_min_w`: `measure_min_w(9.8) == 490.0`, `measure_min_w(2.0) == 300.0`.
  - `test_night_snowfall_cuts_today_and_tomorrow`: 2 cm zwischen 01:00 und 04:00, jetzt 05:00, keine Messung → `state == "vermutet"`, `factor == 0.1`, jeder Slot von 05:00 bis Ende morgen hat `0.1`, übermorgen keiner.
  - `test_measured_ratio_sets_factor`: wie oben, jetzt 10:00, messbare Slots 08:30–09:45 mit `r = 0.05, 0.3, 0.35, 0.4, 0.3` → `state == "gemessen"`, `factor == 0.35` (Median der letzten 4).
  - `test_two_free_slots_lift_the_cut`: wie oben, dann 12:00 und 12:15 mit `r = 0.7, 0.8` → jetzt 12:45: `NO_CUT`; ein einzelner Slot `r = 0.9` mit `0.4` danach hebt nicht auf.
  - `test_future_snowfall_cuts_from_its_start`: frei, 1,5 cm morgen 14:00–17:00 → Faktor 0.1 ab morgen 14:00 bis Ende übermorgen, heute keiner; `state == "frei"`.
  - `test_light_snow_is_ignored`: 0,8 cm in 12 h → `NO_CUT`.
  - `test_zero_pv_without_snow_is_not_snow`: kein Schneefall, Schneehöhe 0, fünf messbare Slots `r = 0`, −2 °C → `NO_CUT`.
  - `test_measured_snow_without_forecast_snowfall`: kein Schneefall im Wetter, Schneehöhe 0,05 m, drei Slots `r = 0.05` bei −1 °C → `state == "gemessen"`; dieselben Slots bei +6 °C → `NO_CUT`.
  - `test_gap_breaks_consecutive_slots`: zwei Slots `r = 0.8` mit einer fehlenden Viertelstunde dazwischen → bleibt bedeckt.
  - `test_slots_below_measure_min_do_not_count`: Morgen-Slots mit P50 400 W und `r = 1.0` → bleibt bedeckt.
  - `test_override_covered_and_free`: ohne Schneefall `Override("covered", 08:00)` → ab 08:00 `vermutet`; nach zwei freien Slots frei; `Override("free", 06:00)` nach Schneefall 02:00 → `NO_CUT`; neuer Schneefall 07:00 danach → wieder bedeckt; ein Override von vor 49 h → wirkungslos.
- [ ] **Step 2:** `pytest tests/unit/app/forecast/test_snow.py -q` → scheitern (Modul fehlt).
- [ ] **Step 3:** `snow.py` umsetzen; reine Funktionen, keine Uhr, kein I/O.
- [ ] **Step 4:** Tests grün, Prüfkette.
- [ ] **Step 5:** Commit `feat(app): Schnee auf den Modulen aus Wetter und Messung erkennen`.

### Task 3: Kürzung im Prognosedienst

**Files:**
- Modify: `src/bindaems/app/forecast/service.py` (`PvForecast`, `ForecastService.refresh`, neu `check_snow`, `set_snow_override`, `component_status`), `src/bindaems/app/runtime.py` (Schleife `snow-check`), `src/bindaems/shared/settings.py` (`PvModelSettings.snow_detection`), `src/bindaems/app/planning/triggers.py` (`Fingerprint.pv_issued` → `pv_revision`), `src/bindaems/app/planning/service.py` (`_watch`)
- Test: `tests/unit/app/forecast/test_service.py`, `tests/unit/app/planning/test_triggers.py`, `tests/unit/shared/test_settings.py`

**Interfaces:**
- Consumes: `fetch_plane(..., snow=True)`, `parse_snow` (Task 1), `snow_cut`, `Observation`, `Override`, `SnowCut`, `NO_CUT` (Task 2)
- Produces:
  - `PvForecast` neu: `uncut_p50_w: Mapping[datetime, float]`, `snow: SnowCut = NO_CUT`, `revision: datetime` (gleich `issued_at` bei einem Abruf). `p50_w/p25_w/p75_w` sind gekürzt.
  - `ForecastService.check_snow(ledger: SlotSource) -> bool` (synchron, Aufrufer `asyncio.to_thread`): liest Ist der letzten 36 h (`actuals`), baut `Observation` aus `uncut_p50_w` und Temperaturen, rechnet `snow_cut`, ersetzt bei Änderung nach Entscheidung 8 die Prognose (neue `revision = clock.now()`, Influx neu, Archiv nicht) und gibt `True` zurück. Merkt sich die Beobachtungen für `refresh`.
  - `ForecastService.set_snow_override(mode: Literal["covered", "free"] | None, *, actor: str) -> None`: schreibt oder löscht `pv_snow_override.json` atomar (temporäre Datei, `fsync`, `os.replace` wie `app/learning/registry.py`), dann sofort neu rechnen wie `check_snow` mit den gemerkten Beobachtungen; Eintrag im `AuditLog` (`AuditLog.record`) „PV: Schnee auf den Modulen gemeldet“ / „PV: Module als frei gemeldet“ / „PV: Schneeerkennung wieder automatisch“.
  - `ForecastService(..., override_path: Path | None = None)`; Laufzeit setzt `cfg.app.data_dir / "pv_snow_override.json"`.
  - `component_status`: bei Kürzung „PV-Prognose von HH:MM Uhr, gekürzt auf N %: vermutlich Schnee auf den Modulen“, `ok=True`, `details={"snow_state": ..., "snow_factor_pct": ..., "snow_since": ISO | None}`.
  - `PvModelSettings.snow_detection: bool = True`; aus → `NO_CUT`.
  - Laufzeit: Aufgabe `("snow-check", self._snow_loop)`, jede Viertelstunde zur Minute 4, 19, 34, 49; Ausnahmen loggen, Schleife läuft weiter.
  - `Fingerprint.pv_revision: datetime | None` statt `pv_issued`; `_watch` setzt `pv.revision`.

- [ ] **Step 1: Failing tests**
  - `test_refresh_applies_snow_cut`: Fake-Open-Meteo mit 2 cm Schneefall heute 02:00, Uhr 05:00 → `latest().p50_w[slot] == pytest.approx(0.1 * uncut_p50_w[slot])` für heute 12:00, P25 und P75 ebenso, `raw_w` ungekürzt, `snow.state == "vermutet"`.
  - `test_refresh_without_snow_data_is_uncut`: Antwort ohne `hourly` → `p50_w == uncut_p50_w`, Status grün ohne „gekürzt“.
  - `test_restart_after_snowfall_keeps_cut`: zwei Dienste mit denselben Antworten und derselben Abrechnung, der zweite frisch erzeugt → gleiche `snow` und gleiche `p50_w`.
  - `test_snow_check_bumps_revision_and_triggers_replan`: nach `refresh` liefert die Abrechnung zwei Slots mit `r = 0.8` → `check_snow` gibt `True`, `revision > issued_at`, `snow == NO_CUT`; zweiter Aufruf ohne neue Slots → `False`, `revision` gleich. In `test_triggers.py`: `changed(old, new)` mit nur anderer `pv_revision` → `["pv_forecast"]`.
  - `test_snow_detection_off`: `snow_detection=False` → `NO_CUT` trotz Schneefall.
  - `test_override_file_roundtrip`: `set_snow_override("covered", actor="admin")` → Datei mit `mode == "covered"`, `snow.state == "vermutet"`; `None` löscht die Datei; kaputte Datei → Automatik, Log `warning`.
  - `test_status_names_the_cut`: Status-Text wie in den Interfaces, `ok is True`.
  - `test_settings.py`: `RuntimeSettings.model_validate({})` hat `pv_model.snow_detection is True`; gespeicherte Einstellungen ohne das Feld laden.
- [ ] **Step 2:** `pytest tests/unit/app/forecast/test_service.py tests/unit/app/planning/test_triggers.py tests/unit/shared/test_settings.py -q` → neue Tests scheitern.
- [ ] **Step 3:** umsetzen. In `refresh` den Abruf der ersten Fläche mit `snow=True`, `parse_snow` auf dieselbe Antwort (`_fetch_plane_once` ruft für die erste Fläche zusätzlich `parse_snow` auf und gibt die Schneestunden mit zurück); die behaltene erste Fläche (`_kept`) behält auch ihre Schneestunden.
- [ ] **Step 4:** Tests grün, Prüfkette.
- [ ] **Step 5:** Commit `feat(app): PV-Prognose bei Schnee auf den Modulen gekürzt`.

### Task 4: Kalibrierung ohne Ausreißertage

**Files:**
- Modify: `src/bindaems/app/forecast/calibration.py` (neu `outlier_days`), `src/bindaems/app/forecast/service.py` (`recalibrate`)
- Test: `tests/unit/app/forecast/test_calibration.py`, `tests/unit/app/forecast/test_service.py`

**Interfaces:**
- Produces: `outlier_days(pairs: Sequence[tuple[datetime, float, float]], threshold_w: float) -> set[date]` — Paare `(slot_start, raw_w, actual_w)` aus `h0_6`; ein lokaler Tag ist Ausreißer, wenn mindestens `OUTLIER_SLOTS = 4` Paare mit `raw_w ≥ threshold_w` und `actual_w < 0.15 · raw_w` darauf fallen. `recalibrate` nimmt für Faktoren **und** Bänder alle Paare dieser Tage heraus und loggt `excluded_days`.

- [ ] **Step 1: Failing tests**
  - `test_outlier_days_finds_snow_day`: ein Tag mit 6 Paaren `actual = 0.05 · raw`, ein Tag mit 3 solchen und sonst normalen → nur der erste.
  - `test_recalibrate_ignores_snow_days`: Archiv über 20 Tage, 8 klare Tage mit `Ist = 0.9 · roh` und 12 Schneetage mit `Ist = 0.02 · roh` → Faktor für 12 Uhr klar `== pytest.approx(0.9)` (ohne den Ausschluss läge der Median bei 0.5, der unteren Grenze); die Bänder entstehen nur aus den 8 klaren Tagen.
- [ ] **Step 2:** Tests scheitern.
- [ ] **Step 3:** umsetzen.
- [ ] **Step 4:** Tests grün, Prüfkette.
- [ ] **Step 5:** Commit `fix(app): Kalibrierung der PV-Prognose lernt nicht aus Schneetagen`.

### Task 5: Grund im Plan

**Files:**
- Modify: `src/bindaems/app/planning/inputs.py` (`SlotInput.pv_snow_pct`, `assemble_inputs(..., pv_snow_pct=...)`), `src/bindaems/app/planning/service.py` (`collect`), `src/bindaems/app/planning/explain.py` (Satz, Reihenfolge vor `GESCHAETZT`), `src/bindaems/shared/plan.py` (`ReasonCode` + `"PV_SCHNEE"`), `ui/src/lib/api/schemas.ts`, `ui/src/lib/plan/model.ts` (`SUFFIX_CODES`)
- Test: `tests/unit/app/planning/test_inputs.py`, `tests/unit/app/planning/test_explain.py`, `tests/unit/app/planning/test_service.py`, `ui/src/lib/plan/model.test.ts`

**Interfaces:**
- Consumes: `PvForecast.snow`, `PvForecast.uncut_p50_w`, `measure_min_w` (Tasks 2, 3)
- Produces:
  - `SlotInput.pv_snow_pct: Finite | None = None`
  - `assemble_inputs(..., pv_snow_pct: Mapping[datetime, float] | None = None)`; `collect` übergibt `{slot: round(100 * f) for slot, f in pv.snow.factors.items() if pv.uncut_p50_w.get(slot, 0) >= measure_min_w(kwp)}`.
  - `Reason(code="PV_SCHNEE", params={"pct": n})`, Satz „PV gekürzt auf {pct} % der Prognose: vermutlich Schnee auf den Modulen.“ (`pct` ganzzahlig).

- [ ] **Step 1: Failing tests**
  - `test_explain.py::test_snow_reason_before_estimated`: Slot mit `pv_snow_pct=10` und Schätzpreis → Codes enden mit `["PV_SCHNEE", "GESCHAETZT"]`, Satz wörtlich mit „10 %“.
  - `test_inputs.py::test_stored_inputs_without_snow_load`: `SlotInput.model_validate(<dict ohne pv_snow_pct>)` → `None`.
  - `test_service.py::test_collect_passes_snow_share`: Prognose mit `snow.factors = {12:00: 0.1, 03:00: 0.1}`, ungekürzt 12:00 = 2000 W, 03:00 = 0 W → nur 12:00 mit `pv_snow_pct == 10`.
  - `model.test.ts`: `PV_SCHNEE` steht als Suffix hinter dem Hauptcode und vor `GESCHAETZT`, Text aus dem Plan.
- [ ] **Step 2:** Tests scheitern.
- [ ] **Step 3:** umsetzen.
- [ ] **Step 4:** Tests grün, Prüfkette (Python und UI).
- [ ] **Step 5:** Commit `feat(app): Zeitplan nennt die PV-Kürzung bei Schnee`.

### Task 6: Anzeige in UI und HA, Knöpfe

**Files:**
- Modify: `src/bindaems/app/forecast/routes.py` (`/api/forecast/pv`: `snow`, `uncut_p50_w` je Slot; neu `POST /api/forecast/pv/snow`), `src/bindaems/app/api/live.py` bzw. die Quelle von `system.warnings` (neu `info: list[str]`), `src/bindaems/app/ha/entities.py` (Sensor, zwei Knöpfe), `src/bindaems/app/ha/commands.py` (zwei Befehle), `ui/src/lib/api/schemas.ts`, `ui/src/routes/prognosen/+page.svelte`, `ui/src/lib/dashboard/notices.ts`, `ui/src/routes/einstellungen/+page.svelte` (Schalter „Schnee erkennen“), `tests/ui_world.py`, Vertragsdateien
- Test: `tests/unit/app/forecast/test_routes.py`, `tests/unit/app/ha/test_entities.py`, `tests/unit/app/ha/test_commands.py`, `ui/src/routes/prognosen/page.test.ts`, `ui/src/lib/dashboard/notices.test.ts`, `ui/tests/e2e/` (bestehende Spec der Seite „Prognosen“ erweitern)

**Interfaces:**
- Consumes: `PvForecast.snow`, `ForecastService.set_snow_override` (Task 3)
- Produces:
  - `GET /api/forecast/pv` zusätzlich `"snow": {"state", "factor_pct", "since", "override": "covered" | "free" | None}` und je Slot `"uncut_p50_w"`.
  - `POST /api/forecast/pv/snow` mit `{"mode": "covered" | "free" | null}`, Rolle `operator`, CSRF wie die übrigen Schreibrouten; Antwort wie `snow` oben.
  - System-Antwort `info: list[str]` (Standard `[]`), mit dem Satz aus Entscheidung 11; `noticesFrom` macht daraus Hinweise der Stufe `info`.
  - HA: Sensor `pv_schnee` (Name „PV Schnee“, Zustand `frei`/`vermutet`/`gemessen`, Attribut `faktor_pct`), Knöpfe `pv_schnee_melden` („Schnee auf den Modulen“) und `pv_module_frei` („Module sind frei“); Quelle der Änderung `ha`.
  - Entfällt der Handschalter (Karte), entfallen POST-Route, Knöpfe in UI und HA und `override_path`; Sensor, Karte und Hinweis bleiben.

- [ ] **Step 1: Failing tests**
  - `test_routes.py`: `GET` liefert `snow.state == "vermutet"`, `factor_pct == 10`, Slots mit `uncut_p50_w`; `POST` als `viewer` → 403, als `operator` mit `{"mode": "free"}` → 200 und `snow.state == "frei"`; `{"mode": "x"}` → 422.
  - `test_entities.py`: Discovery enthält Sensor und beide Knöpfe; Zustand `gemessen` mit `faktor_pct 35`.
  - `test_commands.py`: Druck auf `pv_module_frei` ruft `set_snow_override("free", actor="ha")`.
  - `notices.test.ts`: `info: ["PV-Prognose gekürzt: …"]` ergibt einen Hinweis `level: 'info'`.
  - `page.test.ts` (Prognosen): Karte „Schnee auf den Modulen“ zeigt „vermutet, 10 % der Prognose, seit 07:15“; Knöpfe nur mit Rolle Bedienen; gestrichelte Reihe „ohne Kürzung“ nur, wenn ein Slot gekürzt ist.
  - E2E: Demo-Welt mit Schneefall; Prognosen-Seite zeigt die Karte, Klick auf „Module sind frei“ zeigt „frei“.
- [ ] **Step 2:** Tests scheitern.
- [ ] **Step 3:** umsetzen; Demo-Welt (`tests/ui_world.py`) mit einem verschneiten Morgen, Vertragsdateien neu schreiben.
- [ ] **Step 4:** Tests grün, Prüfkette einschließlich `pnpm build && pnpm e2e`.
- [ ] **Step 5:** Commit `feat(app): Schnee auf den Modulen in Prognosen, Übersicht und HA`.

### Task 7: Szenario „winter-schnee“, Doku

**Files:**
- Modify: `src/bindaems/tools/scenario/data.py` (`ScenarioSlot.snowfall_cm`, `snow_depth_m`, beide optional `None`), `src/bindaems/tools/scenario/synthetic.py` (Art `winter-schnee`), `src/bindaems/tools/scenario/runner.py` (`run_scenario(..., snow: bool = False)`, in `_plan_at` die Prognose mit `snow_cut` kürzen), `src/bindaems/tools/scenario_days.py` (Option `--schnee`)
- Docs: Spec 6.8 und 9.6 (Absatz „**Stand Plan Schnee auf den Modulen** (4.11.2026)“ mit den Entscheidungen), `docs/betrieb.md` „PV-Prognose mit Kalibrierung“ und „Die Seite ‚Prognosen‘“ (Regeln in Alltagssprache, Knöpfe, Rückweg: Schalter „Schnee erkennen“ aus), `docs/uebergabe.md` (Stand, „Offen an der Anlage“: kommt mit dem nächsten Sammel-Pull, nur ems-app neu, keine Migration; erster Schneetag: Zeitplan und Prognosen ansehen)
- Test: `tests/unit/tools/scenario/test_runner.py`, `tests/unit/tools/test_scenario_days.py`

**Interfaces:**
- Consumes: `snow_cut`, `Observation`, `SnowHour` (Task 2); im Runner sind Beobachtungen die Slots vor `now` mit `expected_w = pv_forecast_w["h0_6"]` (sonst Ist) und `actual_w = pv_w`.
- Produces: Art `winter-schnee` (2 Tage): PV-Prognose wie ein klarer Wintertag, Spitze 3000 W von 08:00 bis 16:30; Ist am ersten Tag 5 % der Prognose bis 13:00, danach 100 %; zweiter Tag normal; Schneefall 3 cm am ersten Tag 00:00–04:00 (je Stunde 0,75 cm), Schneehöhe 0,05 m; Preise wie `winter-spread`; Auto wie die übrigen Arten.

- [ ] **Step 1: Failing tests**
  - `test_winter_snow_detection_saves_money`: `run_scenario(winter_schnee, snow=True)` kostet mindestens 100 ct weniger als `snow=False`, beide ohne Verstoß gegen R1/R2 und ohne Fehlmenge des Autos.
  - `test_winter_snow_plan_charges_at_night`: mit `snow=True` hat der Plan um 01:15 am ersten Tag Netzladen zwischen 00:00 und 05:00; ohne nicht.
  - `test_winter_snow_cut_lifts_after_noon`: der Plan um 13:46 hat für den Nachmittag keinen Grund `PV_SCHNEE`.
  - `test_scenario_v2_without_snow_fields_loads`: bestehende Datei ohne die Felder lädt, `snow=True` ändert dort nichts.
- [ ] **Step 2:** Tests scheitern.
- [ ] **Step 3:** umsetzen. Scheitert ein Szenario, liegt der Fehler zuerst in Erkennung oder Planer (eigener `fix(app)`-Commit mit Test); das Szenario nur ändern, wenn es physikalisch nicht zu schaffen ist.
- [ ] **Step 4:** `uv run python -m bindaems.tools.scenario_days --synthetisch winter-schnee --anzahl 2 --takt 60 --schnee --out bericht.md` → Rückgabe 0; Tests grün, Prüfkette, `tests/unit/test_docs.py` grün.
- [ ] **Step 5:** Commit `test(app): Szenario winter-schnee` und Commit `docs: Schnee auf den Modulen in Spec, Betrieb und Übergabe`.

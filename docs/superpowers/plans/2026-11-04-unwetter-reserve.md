# Unwetter-Reserve Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bei einer Sturm- oder Gewitterwarnung von GeoSphere Austria für den Standort hebt der Planer die Untergrenze des Akkus für die Dauer der Warnung auf einen eingestellten SOC an und lädt vorher dafür (PV zuerst, sonst sanft aus dem Netz), damit der Proxmox-Cluster einen Netzausfall länger übersteht. Schalter ab Werk aus; die harte Untergrenze bleibt `battery.reserve_soc_pct` in `config.yaml`.

**Architecture:** Nur die app (neue Quelle `app/storm/`, Planung, Erklärung, HA) und das UI; dazu Modelle in `shared/plan.py` und `shared/settings.py`, die der core nicht liest. Der core, die Safety-Schicht, der Victron-Min-SOC und der Watchdog bleiben unverändert: Die höhere Untergrenze wirkt nur über den Plan (`HALTEN` statt Entladen, `NETZLADEN` mit `reserve_recharge` vorher), den der core heute schon ausführt und prüft. Fällt die app aus, fällt der Akku auf die USV-Reserve zurück, also nie unter das, was heute gilt.

**Tech Stack:** Python 3.12, httpx, pydantic, PuLP/HiGHS, pytest, hypothesis; Svelte 5, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, 1 (Ziele 1 und 6), 9.3 „Optimierungsmodell“ mit „Stand Plan Netzgrenze je Phase“ (Nachladen auf die Reserve), 9.4 „Regelbasierter Ersatzplan“, 9.5 „Planungsablauf“, 9.6 „Erklärungen“. Setzt auf dem Branch des Plans Netzgrenze je Phase auf (`claude/project-thread-ambh18`, Kopf `102628d`), dessen Nachladen (`gr_r`, `reserve_recharge`, `RESERVE_RECHARGE_MAX_W`) hier mitbenutzt wird. Idee 1.6 aus `/mnt/project-files/ideen/brainstorming-2026-11.md`.

## Anlass

Netzausfälle kommen meist mit Unwetter. Heute hält der Planer den Akku nur über der USV-Reserve (20 %). Steht ein Gewitter für den Nachmittag an und der Plan entlädt den Akku am Abend vorher auf 20 %, überbrückt er einen Ausfall nur mit diesen 20 %. Eine Warnung kommt meist Stunden bis Tage vorher. Mit ihr kann der Planer im Sommer einfach PV im Akku lassen statt sie ins Auto oder ins Netz zu geben, im Winter (Sturm) sanft aus dem Netz nachladen.

## GeoSphere Warn-API (geprüft am 4.11.2026)

- `GET https://warnungen.zamg.at/wsapp/api/getWarningsForCoords?lon=…&lat=…&lang=de`, ohne Schlüssel, Antwort `200` mit GeoJSON-Feature. Doku: `https://openapi.hub.geosphere.at/warnapi/v1/openapi.json` (OpenAPI 3.0.3, „Warn API“). Abruf von hier aus funktioniert (St. Pölten: `"warnings": []`).
- `properties.location.properties`: `gemeindenr`, `name`. `properties.warnings[]` je Eintrag `{"type": "Warning", "properties": {…}}` mit `warnid` (int), `chgid` (Version der Warnung), `verlaufid`, `warntypid` (1 Sturm, 2 Regen, 3 Schnee, 4 Glatteis, 5 Gewitter, 6 Hitze, 7 Kälte), `warnstufeid` (1 gelb, 2 orange, 3 rot), `begin`/`end` als lokaler Text ohne Zone (`"27.03.2023 08:00"`, entgegen dem Schema), `text`, `auswirkungen`, `empfehlungen`, `meteotext`, `rawinfo` `{"wtype", "wlevel", "start", "end"}` mit `start`/`end` als **Unix-Sekunden in einem String** (`"1679896800"` = 27.03.2023 06:00 UTC = 08:00 Sommerzeit, passt zu `begin`).
- Fehler: `{"type": "Error", "msg": "…"}` (z. B. „Could not find municipal for coords.“ außerhalb Österreichs), bei 500 `{"type": "error", "msg": …}`.
- Warnungen reichen höchstens 4 Tage voraus und werden „im Anlassfall“ aktualisiert. Eine zurückgenommene Warnung fehlt in der nächsten Antwort.
- Nicht genutzt: `getGewitterAuto` (automatische Gewitter-Kurzfristwarnung je Gemeinde, ohne Beginn und Ende, Vorlauf zu kurz zum Laden).

## Entscheidungen

1. **Quelle:** `getWarningsForCoords` mit `site.location` aus `config.yaml`, `lang=de`, alle 15 min (`POLL = 15 min`), nur wenn der Schalter an ist; aus heißt: kein Abruf, keine Untergrenze, Status „aus“. Beginn und Ende kommen aus `rawinfo.start`/`rawinfo.end` (Unix-Sekunden), nicht aus dem Text `begin`/`end`. Eine Warnung ohne gültiges `rawinfo` oder mit `end ≤ start` wird übersprungen (Log `code=storm_warning_skipped`), der Rest gilt.
2. **Welche Warnungen:** einstellbar `planning.storm.kinds`, Standard **Sturm und Gewitter**; wählbar sind alle sieben Arten. Ab Stufe `planning.storm.min_level`, Standard **[Karte 2]**.
3. **Wie hoch:** `planning.storm.target_soc_pct`, ganze Prozent, Standard **[Karte 1]**. Wirksam ist `min(Ziel, battery.soc_max_pct)`; liegt das Ziel nicht über `battery.reserve_soc_pct`, wirkt die Funktion nicht (kein Fehler).
4. **Wann:** Die Untergrenze gilt am Ende jedes Slots, der `[Beginn − STORM_LEAD, Ende)` einer passenden Warnung schneidet, `STORM_LEAD = 1 h` (Warnungen beginnen unscharf). Mehrere Warnungen vereinigen sich; alle haben dasselbe Ziel. Was hinter dem Horizont (36 h) liegt, sieht der Planer erst, wenn es hereinrückt.
5. **Weich im Optimierer, hart bleibt die Reserve:** je Slot `t` im Fenster `s_t + short_s_t ≥ F_t` mit `F_t` = Untergrenze in kWh und `short_s_t ≥ 0`, Kosten `STORM_SHORTFALL_CT = 200` ct je kWh und Slot. So bleibt das Modell lösbar, wenn das Ziel nicht mehr zu erreichen ist (Warnung schon im Gange, Akku leer), und 1 kWh unter dem Ziel über 10 Slots kostet so viel wie 1 kWh Fehlmenge eines Autos (2000 ct). `lo = min(Reserve, SOC₀)` und `hi` bleiben, wie sie sind. An Victron geht kein neuer Wert; der Min-SOC bleibt auf der USV-Reserve, der Selbsttest `min_soc` bleibt gleich.
6. **Aufladen dafür:** neue Variable `gr_s_t` (Netz → Akku für die Unwetter-Reserve) nur in Slots vor dem Ende des letzten Fensters und nur, wenn `SOC₀ < F_max` (höchste Untergrenze im Horizont). Sie teilt sich die Obergrenze des Nachladens: `gr_r_t + gr_s_t ≤ recharge_cap_w(t)` mit höchstens `RESERVE_RECHARGE_MAX_W = 3000` W, **auch bei ausgeschaltetem „Netzladen erlaubt“**, wie das Nachladen auf die Reserve. Sie zählt wie `gr_r` zu Ladung, Bezug, Spitze, Phasengrenze und Planträgheit, kostet `import_ct` ohne Marge, setzt kein `z_t` (Regel R1) und trägt keinen Restwert (`+ end_value_ct · Σ η_ch · gr_s · Δ`). `Σ η_ch · gr_s_t · Δ ≤ F_max − max(SOC₀, Reserve)`. PV bleibt vorrangig, weil sie nichts kostet. Ausgeführt wird sie als `NETZLADEN(P, reserve_recharge=True)`; der core lässt das schon zu (Plan Netzgrenze je Phase, Task 6). 96 kWh von 20 % auf 40 % sind 19,2 kWh, mit 3 kW rund 7 h; was bis zum Beginn fehlt, fehlt (Entscheidung 5). Nach dem Fenster darf das Haus die Ladung verbrauchen.
7. **Autos während der Warnung:** Die Plan-Schwelle der Akku-Unterstützung rechnet mit der Untergrenze je Slot statt mit der Reserve: rückwärts `q_N = Reserve`, `q_t = max(f_t, d_t + q_{t+1})` mit `f_t = F_t` im Fenster, sonst Reserve, und `d_t` der Entladung des Slots in Prozentpunkten; Schwelle `min(100, ceil(q_t))`. Ohne Fenster ist das genau die heutige Formel. „Sofort aus dem Akku“ (`now_battery`) ist in Slots mit Untergrenze aus. Akku ins Auto (`bt_ev`) begrenzt der Optimierer über Entscheidung 5 von selbst.
8. **Ausfall der Quelle:** Bekannte Warnungen gelten bis zu ihrem Ende weiter, auch wenn Abrufe scheitern (höhere Reserve ist die sichere Richtung). Nur eine erfolgreiche Antwort ersetzt die Liste (eine fehlende Warnung ist zurückgenommen). Status der Komponente „Unwetterwarnungen“: ok mit „keine Warnung“ bzw. „Gewitter (orange) bis 18:00“; nicht ok mit „Warnungen seit {HH:MM} nicht abrufbar“, wenn der letzte Erfolg länger als `STALE_AFTER = 1 h` her ist; „aus“ (ok) bei ausgeschaltetem Schalter.
9. **Neuplanung:** neuer Auslöser `storm`, wenn sich die passenden Warnungen ändern (Fingerabdruck aus `(warn_id, version, begin, end)` der passenden Warnungen, sortiert) oder eine Warnung endet. Einstellungen lösen wie heute `settings` aus.
10. **Plausibilität:** neuer Befund (Art `storm`), wenn die Nachrechnung in einem Slot mit Untergrenze **entlädt** (`discharge_w ≥ HOLD_MIN_W`) und am Slotende mehr als `STORM_TOL_PCT = 0,5` Prozentpunkte unter `F_t` liegt: „Akku entlädt um {HH:MM} unter die Unwetter-Reserve“. Ein nicht erreichbares Ziel (kein Entladen) ist kein Befund.
11. **Ersatzplan:** vor jedem Fenster, wenn die Nachrechnung zu dessen Beginn unter `F` liegt: `NETZLADEN(min(3000, recharge_cap_w, Platz), reserve_recharge=True)` in den billigsten Slots vor dem Beginn (Gleichstand: früher), bis die Nachrechnung `F` erreicht oder die Slots aus sind. Im Fenster `HALTEN`, wo `EIGENVERBRAUCH` unter `F` führen würde.
12. **Anzeige:**
    - Zeitplan: Grund `UNWETTER_RESERVE` in Slots mit Untergrenze, Text „Unwetter-Reserve: {Art} ({Stufe}) bis {HH:MM} Uhr, der Akku hält mindestens {soc} %.“; vor dem Fenster beim Laden dafür `NACHLADEN_RESERVE` wie heute. Im Akku-Diagramm steigt die Reserve-Linie im Fenster auf `F`. Kopf des Zeitplans: Hinweis „Unwetterwarnung: {Art} ({Stufe}) {Beginn}–{Ende}“, wenn `Plan.storm` Warnungen hat.
    - Einstellungen „Planung“: Abschnitt „Unwetter-Reserve“ mit Schalter, Ziel-SOC, Warnstufe, Warnarten.
    - System-Seite: Komponente „Unwetterwarnungen“ (Entscheidung 8).
    - HA: Sensor „Unwetter-Reserve“ (`sensor.bindaems_unwetter_reserve`), Zustand = Untergrenze des aktuellen Slots in %, sonst `0`; Attribut `bis` (Ende des Fensters) und `warnung` (Text der Warnung).
13. **Ausrollen:** Der core bekommt keinen geänderten Code, keine Migration, keine ACL-Zeile, keine Änderung an `config.yaml`. Ausgerollt wird nach den Abnahmen (nicht vor dem 23.11.2026), mit oder nach Stufe 2, `ems-core` und `ems-app` zusammen wie immer; einschalten bis April. Rückweg: Eine Einstellungsversion mit `planning.storm` und Pläne mit Auslöser `storm` liest eine ältere app nicht; der Rückweg in `docs/betrieb.md`, Abschnitt 4, wird dafür ergänzt (wie bei `planning.peak_soft_ct_per_kw`).

## Global Constraints

- Die USV-Reserve (`battery.reserve_soc_pct`) bleibt die einzige harte Untergrenze; nichts in diesem Plan senkt sie, schreibt den Victron-Min-SOC oder ändert Safety-Schicht, core oder Watchdog (Spec 1, Ziele 1 und 6).
- Ausfall von GeoSphere oder Internet darf keinen Plan verhindern: ohne Warnungen plant der Planer wie heute.
- Neue Felder sind optional mit Standard (`None`, `[]`, Schalter aus); gespeicherte `PlanInputs`, Pläne, Einstellungen und Szenariodateien laden unverändert und rechnen wie vorher.
- Keine neue Binärvariable im Optimierer; `test_two_cars_over_36_hours_solve_in_time` besteht weiter.
- Texte deutsch, Commits deutsch im Stil `feat(app): …`, keine Modellbezeichnungen im Repo.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy` strict (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators); UI: `pnpm check`, `pnpm lint`, `pnpm test`. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`. Vor einem Fast-Forward nach `main` auch die GitHub-CI des Branch-Kopfs prüfen.

## Review Focus

- Warnung schon im Gange, SOC 18 % bei Ziel 40 %: Modell lösbar (kein `infeasible`), Nachladen ≤ 3000 W, kein Plausibilitätsbefund, kein Ersatzplan (Task 4 `test_storm_already_running_stays_feasible`, Task 5 `test_unreachable_floor_is_no_finding`).
- GeoSphere fällt aus, nachdem eine Warnung bekannt ist: Untergrenze bleibt bis zum Ende der Warnung, Status nach 1 h nicht ok; nach dem Ende ist sie weg (Task 3 `test_known_warning_survives_failed_fetch`, `test_warning_expires_at_end`).
- Warnung wird verkürzt oder zurückgenommen (gleiche `warnid`, höhere `chgid`, oder fehlt): die nächste erfolgreiche Antwort ersetzt die Liste, und der Planer plant mit `storm` neu (Task 3 `test_withdrawn_warning_is_dropped_and_replans`).
- Zeiten: Beginn 14:10 lokal (nicht auf der Viertelstunde) und eine Warnung über das Ende der Sommerzeit hinweg: der Slot 13:00 (Vorlauf) ist der erste mit Untergrenze, Slots in UTC richtig (Task 1 `test_times_come_from_rawinfo_epoch`, Task 2 `test_floor_window_across_dst_end`).
- Ziel über `soc_max_pct` oder nicht über der Reserve: geklemmt bzw. wirkungslos, kein Fehler (Task 2 `test_target_clamped_to_soc_max`, `test_target_at_reserve_has_no_effect`).

---

### Task 1: GeoSphere-Abruf und -Parser

**Files:**
- Create: `src/bindaems/app/storm/__init__.py`, `src/bindaems/app/storm/geosphere.py`
- Modify: `src/bindaems/shared/plan.py` (`StormKind`, `StormWarning`)
- Test: `tests/unit/app/storm/test_geosphere.py`, Fixtures `tests/fixtures/geosphere/warnings_two.json` (das Beispiel aus der OpenAPI-Doku: Schwechat, zwei gelbe Windwarnungen, `rawinfo.start` `1679896800`/`1679983200`), `warnings_none.json` (St. Pölten, leer), `error_municipal.json`

**Interfaces:**
- Produces (`shared/plan.py`): `StormKind = Literal["sturm", "regen", "schnee", "glatteis", "gewitter", "hitze", "kaelte"]` (in der Reihenfolge `warntypid` 1–7); `StormLevel = Literal[1, 2, 3]`; `class StormWarning(_Model)`: `warn_id: int`, `version: int`, `kind: StormKind`, `level: StormLevel`, `begin: AwareDatetime`, `end: AwareDatetime` (Validator `end > begin`), `text: str`. `STORM_KIND_LABELS: Final[dict[StormKind, str]]` („Sturm“, „Regen“, „Schnee“, „Glatteis“, „Gewitter“, „Hitze“, „Kälte“), `STORM_LEVEL_LABELS` („gelb“, „orange“, „rot“).
- Produces (`app/storm/geosphere.py`): `URL: Final = "https://warnungen.zamg.at/wsapp/api/getWarningsForCoords"`, `SOURCE: Final = "geosphere_warn"`; `class WarningsUnavailable(Exception)`; `parse_warnings(body: str) -> list[StormWarning]` (wirft `WarningsUnavailable` bei kaputtem JSON, `type` „Error“/„error“ oder fehlendem `properties.warnings`); `async def fetch_warnings(http: httpx.AsyncClient, clock: Clock, location: LatLon) -> list[StormWarning]` über `app.fetch.get_raw` (wirft `WarningsUnavailable` bei `FetchError` und Status ≠ 2xx, mit Status im Text).

- [ ] **Step 1: Failing tests**
  - `test_parses_two_warnings_from_doc_example`: zwei `StormWarning` mit `warn_id` 4149/4150, `version` 6/2, `kind == "sturm"`, `level == 1`, Text wie in der Fixture.
  - `test_times_come_from_rawinfo_epoch`: `begin == datetime(2023, 3, 27, 6, 0, tzinfo=UTC)`, `end == datetime(2023, 3, 27, 16, 0, tzinfo=UTC)`; ein Eintrag mit `begin` „01.01.1999 00:00“, aber gültigem `rawinfo` übernimmt `rawinfo`.
  - `test_skips_warning_without_valid_rawinfo`: ein Eintrag ohne `rawinfo`, einer mit `end ≤ start`, einer mit `warntypid` 9 → nur der gültige dritte bleibt, die anderen erscheinen im Log mit `code=storm_warning_skipped`.
  - `test_empty_warnings`: `warnings_none.json` → `[]`.
  - `test_error_body_raises`: `error_municipal.json`, `"not json"`, `{"type": "Feature", "properties": {}}` → `WarningsUnavailable`.
  - `test_fetch_sends_coords_and_lang`: `httpx.MockTransport` prüft `lon`, `lat`, `lang=de` und den User-Agent `BindaEMS/…`; Status 503 → `WarningsUnavailable` mit „503“.
- [ ] **Step 2:** `uv run pytest tests/unit/app/storm/test_geosphere.py -v` → FAIL (Modul fehlt).
- [ ] **Step 3:** Umsetzen. Parsen mit `json.loads` und pydantic-freien Zugriffen auf `properties.warnings[*].properties`; `StormWarning` validiert.
- [ ] **Step 4:** Tests grün, ganze Prüfkette.
- [ ] **Step 5:** Commit `feat(app): Unwetterwarnungen von GeoSphere Austria lesen`.

### Task 2: Einstellungen und Untergrenzen je Slot

**Files:**
- Modify: `src/bindaems/shared/settings.py` (`StormSettings`, `PlanningSettings.storm`)
- Create: `src/bindaems/app/storm/windows.py`
- Test: `tests/unit/app/storm/test_windows.py`, `tests/unit/shared/test_settings.py`

**Interfaces:**
- Consumes: `StormWarning`, `StormKind`, `StormLevel` (Task 1).
- Produces (`shared/settings.py`): `class StormSettings(_Model)`: `enabled: bool = False`, `target_soc_pct: Annotated[int, Field(ge=10, le=100)] = <Karte 1>`, `min_level: StormLevel = <Karte 2>`, `kinds: Annotated[list[StormKind], Field(min_length=1)] = ["sturm", "gewitter"]`; `PlanningSettings.storm: StormSettings = Field(default_factory=StormSettings)`.
- Produces (`app/storm/windows.py`): `STORM_LEAD: Final = timedelta(hours=1)`; `relevant(warnings: Sequence[StormWarning], settings: StormSettings) -> list[StormWarning]` (Art in `kinds`, `level ≥ min_level`, nach `begin` sortiert; bei ausgeschaltetem Schalter `[]`); `storm_floors(starts: Sequence[datetime], warnings: Sequence[StormWarning], settings: StormSettings, *, reserve_pct: float, soc_max_pct: float) -> list[float | None]` (je Slotbeginn die Untergrenze in % oder `None`; Entscheidungen 3 und 4); `warning_at(start: datetime, warnings: Sequence[StormWarning]) -> StormWarning | None` (die Warnung mit dem spätesten Ende, deren Fenster den Slot schneidet; für die Erklärung).

- [ ] **Step 1: Failing tests**
  - `test_floor_covers_lead_and_warning`: Warnung 14:00–16:00 lokal, Slots 12:00–17:00 → `None` bis 12:45, `40.0` von 13:00 bis 15:45, `None` ab 16:00.
  - `test_floor_window_across_dst_end`: Warnung 25.10.2026 01:30–03:30 lokal über die Umstellung; die Slots in UTC mit Untergrenze sind genau die, die `[begin − 1 h, end)` schneiden (24.10. 22:30 bis 25.10. 02:30 UTC, also 16 Slots; die doppelte Stunde zählt doppelt).
  - `test_unaligned_begin_includes_slot`: Beginn 14:10 → Slot 13:00 ist der erste (Vorlauf ab 13:10 schneidet 13:00–13:15).
  - `test_filters_kind_and_level`: Regen orange und Gewitter gelb bei `min_level=2`, `kinds=["sturm","gewitter"]` → keine Untergrenze; Gewitter orange → Untergrenze.
  - `test_switch_off_means_no_floor`: `enabled=False` → lauter `None`.
  - `test_target_clamped_to_soc_max`: Ziel 100, `soc_max_pct` 95 → `95.0`.
  - `test_target_at_reserve_has_no_effect`: Ziel 20, Reserve 20 → lauter `None`.
  - `test_overlapping_warnings_union`: 14–16 und 15–18 Uhr → durchgehend 13:00–17:45.
  - Settings: `test_storm_defaults`: `PlanningSettings()` hat `storm.enabled is False`, Ziel und Stufe wie Karte, `kinds == ["sturm", "gewitter"]`; alte Einstellungs-JSON ohne `storm` lädt; `kinds=[]` → `ValidationError`.
- [ ] **Step 2:** Tests laufen lassen → FAIL.
- [ ] **Step 3:** Umsetzen.
- [ ] **Step 4:** Tests grün, ganze Prüfkette; Vertragsdatei `ui/src/lib/api/contract/settings.json` neu schreiben (Befehl unter „Befehle“ in `docs/uebergabe.md`).
- [ ] **Step 5:** Commit `feat(app): Einstellung und Zeitfenster der Unwetter-Reserve`.

### Task 3: Dienst „Unwetterwarnungen“, Neuplanung, Status und HA

**Files:**
- Create: `src/bindaems/app/storm/service.py`
- Modify: `src/bindaems/app/runtime.py` (Dienst anlegen, `run` starten, `component_status` eintragen), `src/bindaems/app/planning/triggers.py` (`Fingerprint.storm`, `changed`), `src/bindaems/app/planning/service.py` (Fingerabdruck, `collect` gibt die Warnungen weiter), `src/bindaems/shared/plan.py` (`PlanTrigger` + `"storm"`), `src/bindaems/app/ha/entities.py` (Sensor, `HaInputs.storm_floor_pct`, `storm_until`, `storm_text`)
- Test: `tests/unit/app/storm/test_service.py`, `tests/unit/app/planning/test_triggers.py`, `tests/unit/app/planning/test_service.py`, `tests/unit/app/ha/test_entities.py`

**Interfaces:**
- Consumes: `fetch_warnings`, `WarningsUnavailable` (Task 1); `relevant` (Task 2).
- Produces: `class StormService` mit `__init__(self, cfg: Config, settings: SettingsService, http: httpx.AsyncClient, clock: Clock, fetch: Callable[..., Awaitable[list[StormWarning]]] = fetch_warnings)`, `def warnings(self) -> list[StormWarning]` (bekannt, noch nicht zu Ende, `relevant` nach den aktuellen Einstellungen), `async def refresh(self) -> None`, `async def run(self) -> None` (alle `POLL = timedelta(minutes=15)`, sofort beim Start und nach dem Einschalten), `def component_status(self) -> ComponentStatus` (Name „Unwetterwarnungen“, Texte nach Entscheidung 8), `def fingerprint(self) -> tuple[tuple[int, int, datetime, datetime], ...]`. `STALE_AFTER: Final = timedelta(hours=1)`. `Fingerprint.storm: tuple[Any, ...] = ()`; `changed` meldet `"storm"` nach `"vehicles"` und vor `"settings"`. `PlanningService` bekommt `storm: Callable[[], list[StormWarning]] | None = None`.

- [ ] **Step 1: Failing tests** (Fake-Uhr, Fake-`fetch`)
  - `test_switch_off_fetches_nothing`: Schalter aus → `refresh` ruft `fetch` nie, `warnings() == []`, Status ok „aus“.
  - `test_known_warning_survives_failed_fetch`: erst Erfolg mit Gewitter orange 14–16 Uhr, dann `WarningsUnavailable` → `warnings()` enthält sie weiter; 61 min nach dem letzten Erfolg Status nicht ok „Warnungen seit 12:00 nicht abrufbar“.
  - `test_warning_expires_at_end`: Uhr auf 16:00 → `warnings() == []`, Fingerabdruck ändert sich.
  - `test_withdrawn_warning_is_dropped_and_replans`: zweite erfolgreiche Antwort ohne die Warnung → `warnings() == []`; im Planungsdienst führt der neue Fingerabdruck zu genau einem Lauf mit `trigger == "storm"`.
  - `test_new_version_replaces_old`: gleiche `warn_id`, `version` 3 mit Ende 15:00 statt 16:00 → nur die neue gilt.
  - `test_status_texts`: keine Warnung → ok „keine Warnung“; Gewitter orange → ok „Gewitter (orange) bis 16:00“.
  - HA: `test_storm_sensor`: Slot mit Untergrenze 40 → Zustand `40`, Attribute `bis`, `warnung`; ohne → `0`.
- [ ] **Step 2:** Tests laufen lassen → FAIL.
- [ ] **Step 3:** Umsetzen; Planungsdienst liest `storm()` in `collect` und in `_watch` (Fingerabdruck), Laufzeit verbindet beides.
- [ ] **Step 4:** Tests grün, ganze Prüfkette.
- [ ] **Step 5:** Commit `feat(app): Unwetterwarnungen abrufen und neu planen, wenn sie sich ändern`.

### Task 4: Untergrenze und Aufladen im Optimierer

**Files:**
- Modify: `src/bindaems/app/planning/inputs.py` (`SlotInput.storm_floor_pct`, `PlanInputs.storm_warnings`, `assemble_inputs(..., storm_warnings=())`), `src/bindaems/app/planning/optimizer.py` (`gr_s`, Untergrenze, Konstanten), `src/bindaems/app/planning/intents.py` (`gr_s` in die Absicht), `src/bindaems/app/planning/service.py` (Warnungen an `assemble_inputs`)
- Test: `tests/unit/app/planning/test_inputs.py`, `test_optimizer.py`, `test_intents.py`

**Interfaces:**
- Consumes: `storm_floors` (Task 2), `StormService.warnings` über `PlanningService.storm` (Task 3).
- Produces: `SlotInput.storm_floor_pct: Finite | None = None`; `PlanInputs.storm_warnings: list[StormWarning] = Field(default_factory=list)` (die passenden Warnungen des Laufs); `STORM_SHORTFALL_CT: Final = 200.0`; `storm_charge_caps_w(inputs: PlanInputs) -> list[float]` (`recharge_cap_w`-Formel in Slots vor dem Ende des letzten Fensters, wenn `SOC₀ < F_max`, sonst 0); `SlotFlows.gr_s: float`; `OptimizerResult.storm_shortfall_kwh: float` (Summe der `short_s` in kWh·Slot).

- [ ] **Step 1: Failing tests** (Akku 96 kWh, Reserve 20 %, Ziel 40 %, Haus 600 W, Preise mit billigen Slots 02:00–04:00, Warnung Gewitter orange 14:00–16:00)
  - `test_assemble_sets_floors`: `assemble_inputs(..., storm_warnings=[…])` mit Schalter an → `storm_floor_pct` 40 genau in den Slots aus Task 2; Schalter aus → alle `None`, `storm_warnings == []`.
  - `test_holds_battery_for_storm_window`: SOC₀ 60 %, keine PV → Nachrechnung am Ende jedes Fenster-Slots ≥ 40 % − 0,5; ohne Warnung entlädt derselbe Lauf bis 20 %.
  - `test_storm_charge_with_switch_off`: SOC₀ 22 %, `grid_charge_allowed=False` → `NETZLADEN` mit `reserve_recharge=True`, je Slot ≤ 3000 W, zuerst die billigen Slots, SOC zu Fensterbeginn ≥ 40 % − 0,5.
  - `test_pv_fills_storm_reserve_first`: genug PV am Vormittag → kein `NETZLADEN`, aber zu Fensterbeginn ≥ 40 %.
  - `test_storm_already_running_stays_feasible`: Fenster ab Slot 0, SOC₀ 18 % → `solver_status == "optimal"`, `gr_r + gr_s ≤ 3000` je Slot, `storm_shortfall_kwh > 0`.
  - `test_no_storm_charge_after_window`: `gr_s == 0` in allen Slots ab Fensterende.
  - `test_storm_charge_respects_phase_limit`: Grenze 4370 W je Phase und Tesla voll im billigen Slot → `(gr_h + gr_b + gr_r + gr_s) / 3 + Tesla/3 ≤ 4370`.
  - `test_old_inputs_without_storm_unchanged`: gespeicherte Eingangsdaten ohne die Felder lösen mit gleichem Ziel wie vorher.
  - Intents: `test_storm_charge_becomes_flagged_netzladen`: Flüsse mit `gr_s = 2500`, `gr_b = 0` → `NETZLADEN(2500, reserve_recharge=True)`, auch bei Schalter aus, und `_drop_unprofitable` lässt es stehen.
- [ ] **Step 2:** Tests laufen lassen → FAIL.
- [ ] **Step 3:** Umsetzen nach Entscheidungen 5 und 6; die Docstrings von `optimizer.py` und `inputs.py` um den Absatz „Unwetter-Reserve“ ergänzen.
- [ ] **Step 4:** Tests grün, ganze Prüfkette, `test_two_cars_over_36_hours_solve_in_time` grün.
- [ ] **Step 5:** Commit `feat(app): Planer hält und lädt den Akku für eine Unwetterwarnung`.

### Task 5: Ersatzplan, Plausibilität, Schwellen und Erklärung

**Files:**
- Modify: `src/bindaems/app/planning/fallback.py` (`_storm`), `src/bindaems/app/planning/plausibility.py` (Befund `storm`, `STORM_TOL_PCT`), `src/bindaems/app/planning/builder.py` (`support_thresholds` mit Untergrenze, `PlanSlot.storm_reserve_pct`, `Plan.storm`), `src/bindaems/app/planning/spare.py` (`now_battery` aus), `src/bindaems/app/planning/explain.py` (`UNWETTER_RESERVE`), `src/bindaems/shared/plan.py` (`PlanSlot.storm_reserve_pct`, `PlanStorm`, `Plan.storm`, `ReasonCode`), `src/bindaems/app/planning/routes.py` (`GET /api/plan` mit beidem)
- Test: `tests/unit/app/planning/test_fallback.py`, `test_plausibility.py`, `test_builder.py`, `test_spare.py`, `test_explain.py`, `test_routes.py`

**Interfaces:**
- Consumes: `SlotInput.storm_floor_pct`, `PlanInputs.storm_warnings` (Task 4); `warning_at` (Task 2).
- Produces: `support_thresholds(slots: Sequence[PlanSlot], reserve_pct: float, floors: Sequence[float | None] = ()) -> list[tuple[float, datetime]]` (Entscheidung 7); `PlanSlot.storm_reserve_pct: Finite | None = None`; `class PlanStorm(_Model)`: `target_pct: Finite`, `warnings: list[StormWarning]`; `Plan.storm: PlanStorm | None = None` (gesetzt, sobald der Lauf passende Warnungen hat); `ReasonCode` + `"UNWETTER_RESERVE"` mit Parametern `art`, `stufe`, `bis`, `soc`.

- [ ] **Step 1: Failing tests**
  - Ersatzplan `test_fallback_charges_before_storm`: Schalter aus, SOC₀ 25 %, Fenster ab 14:00 → `NETZLADEN` mit Flag in den billigsten Slots vor 13:00, Nachrechnung zu Beginn ≥ 40 % − 0,5; `test_fallback_holds_in_window`: SOC₀ 45 % → im Fenster `HALTEN`, wo `EIGENVERBRAUCH` unter 40 % führt.
  - Plausibilität `test_discharge_below_floor_is_finding`: `EIGENVERBRAUCH`-Slot im Fenster, der von 40,2 % auf 39,0 % entlädt → Befund „Akku entlädt um 14:15 unter die Unwetter-Reserve“; `test_unreachable_floor_is_no_finding`: SOC 30 % im Fenster mit `HALTEN` → kein Befund.
  - Builder `test_support_threshold_rises_in_window`: ohne Entladung danach ist die Schwelle im Fenster 40, außerhalb 20; `test_support_threshold_without_floors_unchanged`: ohne `floors` gleich wie heute (bestehende Tests unverändert grün).
  - Spare `test_no_now_battery_in_storm_slots`: `car_battery_allowed=True`, Slot mit Untergrenze → `now_battery is False`.
  - Erklärung `test_storm_reason_text`: „Unwetter-Reserve: Gewitter (orange) bis 16:00 Uhr, der Akku hält mindestens 40 %.“; ein Nachladen-Slot davor behält `NACHLADEN_RESERVE`.
  - Plan/Route: `test_plan_carries_storm`: `Plan.storm.target_pct == 40`, Warnungen wie Eingang, `slots[i].storm_reserve_pct` wie `storm_floor_pct`; alter Plan-JSON ohne Felder lädt mit `None`.
- [ ] **Step 2:** Tests laufen lassen → FAIL.
- [ ] **Step 3:** Umsetzen nach Entscheidungen 7, 10, 11 und 12; Vertragsdatei `ui/src/lib/api/contract/plan.json` neu schreiben.
- [ ] **Step 4:** Tests grün, ganze Prüfkette, Szenario-Vergleich `scenario` auf erzeugten Tagen unverändert (keine Warnungen).
- [ ] **Step 5:** Commit `feat(app): Ersatzplan, Prüfung und Erklärung kennen die Unwetter-Reserve`.

### Task 6: UI, Doku und Übergabe

**Files:**
- Modify: `ui/src/lib/api/schemas.ts`, `ui/src/lib/settings/model.ts`, `ui/src/lib/settings/PlanningEditor.svelte`, `ui/src/lib/settings/PlanningView.svelte`, `ui/src/lib/plan/model.ts` (Grund, Kopf-Hinweis), `ui/src/lib/plan/chart.ts` (Reserve-Linie je Slot), `ui/src/lib/plan/PlanHeader.svelte`
- Test: `ui/src/lib/settings/model.test.ts`, `PlanningView.test.ts`, `ui/src/lib/plan/model.test.ts`, `chart.test.ts`, `PlanHeader.test.ts`, `ui/src/lib/api/contract.test.ts`; E2E: bestehender Zeitplan-Test um einen Plan mit `storm` im Demo-Backend ergänzt
- Docs: Spec 9.3/9.4/9.6 neuer Absatz „Stand Plan Unwetter-Reserve“ (Entscheidungen 1–13), Spec 6.x neue Quelle „GeoSphere Warn-API“; `docs/betrieb.md` „Planung“ (Abschnitt „Unwetter-Reserve“: was sie tut, Einstellungen, Status, HA-Sensor, was sie nicht tut: kein Min-SOC, kein Schutz, wenn die app steht), Abschnitt 4 Rückweg ergänzt; `docs/uebergabe.md` (Stand, Ausrollen nach Entscheidung 13, Rulings, Deferred minors)

**Interfaces:**
- Consumes: `planning.storm` (Task 2), `Plan.storm`, `PlanSlot.storm_reserve_pct`, `UNWETTER_RESERVE` (Task 5).

- [ ] **Step 1: Failing tests**
  - Einstellungen: Abschnitt „Unwetter-Reserve“ zeigt Schalter (aus), Ziel-SOC, Stufe „ab orange“, Warnarten Sturm und Gewitter angehakt; Speichern sendet `planning.storm`; Ziel unter 10 oder über 100 und keine Warnart → Fehlermeldung, kein Speichern.
  - Zeitplan: Reserve-Linie des Diagramms ist im Fenster 40, sonst `plan.reserve_pct`; Kopf zeigt „Unwetterwarnung: Gewitter (orange) 14:00–16:00“ nur mit Warnungen; Grund-Text wie Entscheidung 12.
  - Vertrag: `plan.json` und `settings.json` mit den neuen Feldern bestehen den Schema-Test; alte Antworten ohne sie auch.
- [ ] **Step 2:** `pnpm test` → FAIL.
- [ ] **Step 3:** Umsetzen; Doku schreiben (`tests/unit/test_docs.py` prüft Verweise).
- [ ] **Step 4:** `pnpm check`, `pnpm lint`, `pnpm test`, E2E (lokal mit `launchOptions.executablePath: /opt/pw-browsers/chromium`), ganze Python-Kette.
- [ ] **Step 5:** Commits `feat(ui): Unwetter-Reserve in Einstellungen und Zeitplan`, `docs: Unwetter-Reserve in Spec, Betrieb und Übergabe`.

## Nicht in diesem Plan

- Victron-Min-SOC während der Warnung anheben: würde die Reserve auch ohne app halten, aber der Selbsttest `min_soc` meldet dann eine Abweichung, unter Min-SOC − 5 % lädt Victron selbst mit voller Leistung nach (3.11. 22:35), und der Watchdog kennt nur die Reserve. Erst prüfen, wenn sich die Unwetter-Reserve im Sommer bewährt hat.
- Schnelleres Laden als 3 kW für die Reserve (auch mit Schalter): bräuchte eine Ausnahme vom Befund „rechnet sich nicht“ für normales Netzladen und mehr Netzspitze; erst mit Erfahrung aus dem ersten Sommer.
- `getGewitterAuto` (Kurzfrist-Gewitter): ohne Beginn und Ende, zu kurz zum Laden.
- USV-Reichweite in Stunden (Idee 1.5) und externes Lebenszeichen (1.3): eigene Ideen.
- Szenario-Dateien mit Warnungen für `scenario_days`: erst, wenn echte Warntage aufgezeichnet sind.

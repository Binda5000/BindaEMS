# PV je Anlage und String Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eine eigene Seite „PV-Anlagen“ zeigt die Erzeugung je PV-Anlage und je String (Leistung jetzt und Ertrag je Tag, Woche, Monat und Jahr mit Blättern, jeweils mit kWh/kWp; am Tag als Kurve, sonst als Balken). Anlagen und Strings legt der Admin in der UI an, beliebig viele, ohne Neustart; spätere PV-Systeme kommen als weitere Einträge dazu.

**Architecture:** Nur app und UI. Die Seite baut auf dem Muster der Verbraucher auf (Stammdaten in SQLite, Quelle core-Signal oder HA-Leistungssensor, Energie aus InfluxDB, je Tag gespeichert). `EnergyCache`, `HaValueCache` und die Tagesspeicherung werden dafür auf ein Protokoll `PowerSource` verallgemeinert; die PV bekommt ein eigenes Paket `app/pv/`, eigene Tabellen (Migration 0018) und eine eigene `EnergyCache`-Instanz. Der core bleibt unverändert.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic, InfluxQL (InfluxDB 1.x), SvelteKit 2 / Svelte 5, ECharts, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Tabelle Anlage „PV“, 6.2 `pvinverter`, 6.9 Huawei); Muster: `docs/betrieb.md` „Energie der Verbraucher“ und „Verbraucher aus Home Assistant“.

## Befund: Welche Daten gibt es je Anlage und je String?

| Quelle | Was | Heute im System |
|---|---|---|
| Victron `pvinverter` (VM-3P75CT vor dem Huawei) | AC-Leistung gesamt und je Phase, Zählerstand | ja: `pv.huawei.power_w`, in InfluxDB `power` mit `source=pv, id=huawei` (raw 90 Tage, `long` 1-min-Mittel unbegrenzt) |
| weitere Victron-`pvinverter` (z. B. ein zweiter Zähler, Fronius am Cerbo) | wie oben | geht sofort: Instanz unter `victron.instances.pv` eintragen → `pv.<name>.power_w`; `Derived.pv_total_w` summiert alle |
| Victron-MPPT (`solarcharger`, DC-gekoppelt) | Summe aller MPPT | nur als Summe `pv_dc.power_w` (`system/Dc/Pv/Power`, InfluxDB `source=pv, id=dc`); einzelne MPPT liest der core nicht |
| Huawei je String (PV1, PV2 …) | nur Spannung und Strom je String, **keine Leistung** | in BindaEMS nicht eingerichtet (Spec 6.9: „nur über die vorhandenen HA-Entitäten“). Die HA-Integration `huawei_solar` liefert `…_pv_1_voltage`, `…_pv_1_current` usw.; Leistung je String = U × I. **Laut Binda (8.11.2026) gibt es die Integration und die Template-Sensoren für die Leistung je String in HA schon.** |
| HA-Sensoren allgemein | jede Leistung in W/kW | über die HA-Datenbank in InfluxDB (`influxdb.ha_database`), wie bei den Verbrauchern |

Folgen für die Lösung: Anlagen kommen aus dem core (Victron), Strings aus HA. Wie die Verbraucher brauchen HA-Quellen `influxdb.ha_database`. Bestätigt von Binda am 8.11.2026: `influxdb.ha_database` ist gesetzt; PV1 = Süd (4,1 kWp), PV2 = West (5,7 kWp).

## Entscheidungen

1. **Eigene Seite „PV-Anlagen“** (`/pv`, Symbol `pv`) in der Navigation direkt vor „Verbraucher“ (erst Erzeugung, dann Verbrauch). Alle sehen sie, nur Admins ändern Einträge (wie Verbraucher).
2. **Pflege in der UI, nicht in `config.yaml`:** beliebig viele Einträge, ohne Neustart, jede Änderung im Änderungsprotokoll (Audit, Quelle `ui`).
3. **Zwei Ebenen:** Anlage (oberste Ebene) → Strings (Kinder einer Anlage). Ein String hat keine Kinder. Eine Anlage ohne Strings ist erlaubt (z. B. Balkonkraftwerk).
4. **Quellen:** core-Signal `pv.<id>.power_w` oder `pv_dc.power_w`, oder HA-Leistungssensor in W/kW. **Strings des Huawei über die vorhandenen HA-Template-Sensoren (U × I).** Nicht gewählt: U × I in BindaEMS rechnen (zwei Entitäten je Quelle, eigene Einheiten V/A, Mittel von Produkt ≠ Produkt der Mittel). Der Verlauf der Strings reicht so weit zurück, wie HA die Template-Sensoren nach InfluxDB schreibt.
5. **Victron-MPPT einzeln: nicht jetzt.** Kommt ein DC-gekoppeltes System, folgt ein kleiner eigener core-Plan (Pfade am Gerät prüfen, eigener Tag `source=pv_mppt`, damit `thermal_data._pv` und andere Summen über `source=pv` nicht doppelt zählen). Bis dahin ist `pv_dc.power_w` als eine Anlage „MPPT gesamt“ wählbar.
6. **kWp je Eintrag, optional** (0,1 bis 1000). Mit kWp zeigt die Seite kWh/kWp; das macht Strings verschiedener Größe vergleichbar (ein schwacher String fällt auf).
7. **Restzeile nur oben:** „Nicht zugeordnet“ = `pv_total_w` − Summe der Anlagen (Leistung und Energie heute; Energie aus `derived/pv_total`), nur wenn mindestens eine Anlage angelegt ist. Unter einer Anlage gibt es keine Restzeile, denn Strings messen DC, die Anlage AC (Wandlerverluste); dort steht stattdessen „Strings zusammen“ (Summe der Kinder).
8. **Tageskurve** je Eintrag in 5-min-Schritten: core-Signale aus RP `long` (1-min-Mittel, jeder Tag abrufbar), HA aus der HA-Datenbank. Tag wählbar mit Blättern, Standard heute.
9. **Ertrag je Tag gespeichert** (Tabelle `pv_source_day`, nachts 02:45 die letzten 3 Tage wie bei den Verbrauchern). Anders als dort rechnet die PV die core-Signale aus RP `long` (1-min-Mittel, unbegrenzt) statt `raw` (90 Tage); so kann der Start alle fehlenden Tage **bis zum 1. Januar des Vorjahres** nachholen (höchstens 800 Tage, einmalig, im Hintergrund). Ein Tag ohne jeden Verlauf bekommt eine Zeile mit Grund und wird nicht erneut gerechnet. Zusätzlich speichert die app je Tag „PV gesamt“ (`derived/pv_total`) unter der reservierten `source_id` 0, für „Nicht zugeordnet“ in Woche, Monat und Jahr.
10. **Zeiträume Tag, Woche, Monat, Jahr** mit Blättern und „Heute“, dieselben Grenzen wie die Statistik (`period_range`, Woche Mo–So, Wiener Zeit). Tag: Leistungskurve in 5-min-Schritten. Woche und Monat: Balken je Tag. Jahr: Balken je Monat. Die Tabelle zeigt den Ertrag im gewählten Zeitraum (beim laufenden Zeitraum bis jetzt) und kWh/kWp. Über den Balken wählst du „Anlagen“ oder „Strings“ und „kWh“ oder „kWh/kWp“. Zeitraum und Tag stehen in der Adresse (`/pv?zeitraum=monat&tag=2026-11-08`), wie in der Statistik.
11. **Statistik:** zusätzlich die Karte „PV je Anlage“ mit dem Ertrag im Zeitraum der Statistik.
12. **Keine Meldung bei String-Ausfall** in diesem Plan. Das ist Idee 11.3 („PV-Flächen einzeln überwachen“) und braucht die Prognose je Fläche; eigener Plan, sobald ein paar Wochen String-Daten da sind.
13. **Ausrollen:** nur app und UI, Migration 0018 (oder die nächste freie Nummer, falls vorher ein anderer Plan auf `main` eine belegt). Mit Stufe 2 des Sammel-Pulls ab 23.11.2026, core und app gemeinsam mit fester Version, Datenbank vorher sichern. Nichts an der Anlage wird geschaltet.

## Global Constraints

- Texte deutsch, Anrede „du“, Zahlen mit Komma (`format.ts`), Energie „kWh“ mit 1 Nachkommastelle, kWh/kWp mit 2.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy --strict` (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators), UI `pnpm check`, `pnpm lint`, `pnpm test`; E2E mit `launchOptions.executablePath: /opt/pw-browsers/chromium` lokal. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Tests, die `AppRuntime` bauen, legen `data_dir` unter `tmp_path`.
- InfluxQL nur mit `quote_ident`/`quote_str`.
- Breite 360 px ohne seitliches Scrollen (CI-Runner ohne Inter, `ui/tests/e2e/fonts-ci.conf`).
- Commits deutsch im Stil `feat(app): …`, keine Modellbezeichnungen.

## Review Focus

- **HA-Datenbank fehlt** (`influxdb.ha_database` nicht gesetzt): HA-Einträge zeigen „HA-Datenbank nicht eingerichtet“, core-Einträge laufen normal; Anlegen einer HA-Quelle ist dann mit 422 abgelehnt (wie Verbraucher `ha_enabled`). Test in Task 2 und 3.
- **Nacht und Null:** 0 W ist ein gültiger Wert (nicht „–“); kWh/kWp bei 0 kWh ist 0,00; ein Template-Sensor, der nachts `unavailable` liefert, zählt nicht als Wert, die Energie bleibt aus den vorhandenen Werten. Test in Task 3 (Baum) und Task 4 (Kurve).
- **Löschen einer Anlage mit Strings** wird mit 409 abgelehnt („zuerst die Strings löschen“); gelöschte Einträge verlieren ihre Tage. Test in Task 2.
- **Zeitumstellung:** Tageskurve und gespeicherter Tag laufen über `local_day_slots` (23/25 h). Test in Task 4 mit dem 29.03.2026 bzw. 25.10.2026.
- **Zeitraum vor den ersten Daten:** Ein Jahr, in dem die ersten Monate keine Werte haben (vor der Inbetriebnahme, vor den Template-Sensoren), zeigt dort „–“ (keine Balken), nicht 0 kWh; die Summe des Zeitraums zählt nur bekannte Tage und sagt „ab {erster Tag mit Wert}“. Test in Task 4.
- **ID-Kollision mit Verbrauchern:** Verbraucher und PV haben eigene IDs ab 1; eigene `EnergyCache`- und Tages-Instanzen, damit `window_energy` (Schlüssel `.id`) nichts vermischt. Test in Task 3 (Verbraucher 1 und PV-Quelle 1 mit verschiedenen Signalen behalten ihre eigenen Werte).

---

### Task 1: Energie und HA-Werte für beliebige Leistungsquellen

**Files:**
- Create: `src/bindaems/app/consumers/source.py`
- Modify: `src/bindaems/app/consumers/energy.py`, `values.py` (`HaRef.from_consumer` → `HaRef.from_source`, Alias bleibt nicht), `days.py`
- Modify: `src/bindaems/app/runtime.py`, `src/bindaems/app/stats/service.py`, `tests/ui_world.py` (Aufrufer umstellen)
- Test: `tests/unit/app/consumers/test_energy.py`, `test_days.py`, `test_values.py`

**Interfaces:**
- Produces:
  - `class PowerSource(Protocol)` in `consumers/source.py` mit den Attributen `id: int`, `source_kind: str`, `power_ref: str`, `power_unit: str | None` (`Consumer` erfüllt es unverändert).
  - `EnergyCache.window_energy(sources: Iterable[PowerSource], start, end)`, `energy_kwh(source: PowerSource)`, `note(source: PowerSource)`, `refresh(sources: Iterable[PowerSource])`; zusätzlich `EnergyCache.pv_total_kwh() -> float | None` (Energie von `("derived","pv_total")` im selben Abruf wie die Hauslast).
  - `HaRef.from_source(source: PowerSource) -> HaRef`.
  - `class SourceDays` in `consumers/days.py`: `__init__(engine, cache: EnergyCache, list_sources: Callable[[], Sequence[PowerSource]], table: Table, id_column: str, clock: Clock, *, total_id: int | None = None)`; Methoden wie bisher `store`, `fill_missing`, `energy`, dazu `daily(first: date, last: date) -> dict[int, dict[date, tuple[float | None, str | None]]]` (Werte je Quelle und Tag, ohne Summe). Mit `total_id` speichert `store` die Energie von `derived/pv_total` unter dieser ID mit. `ConsumerDays` wird zu `SourceDays(…, consumer_day, "consumer_id", …)`; der Name `ConsumerDays` bleibt als dünne Unterklasse mit dem alten Konstruktor.
  - `EnergyCache.__init__(reader, clock, ha_database, *, retention: str = RP_RAW)`: aus welcher RP die core-Minutenmittel kommen (die PV nutzt `RP_LONG`).
  - `CORE_POWER_RE` erkennt zusätzlich `pv_dc.power_w` → Tags `("pv", "dc")` (Funktion `core_tags(ref: str) -> tuple[str, str] | None`).

- [ ] **Step 1: Failing tests**
  - `test_energy.py::test_window_energy_accepts_any_power_source`: ein `@dataclass` mit nur `id, source_kind, power_ref, power_unit` (`"core"`, `"pv.huawei.power_w"`) bekommt kWh wie ein `Consumer`.
  - `test_energy.py::test_pv_dc_maps_to_dc_series`: `core_tags("pv_dc.power_w") == ("pv", "dc")`, `core_tags("pv.huawei.power_w") == ("pv", "huawei")`, `core_tags("battery.power_w") is None`.
  - `test_energy.py::test_pv_total_kwh_from_derived`: Antwort mit Reihe `source=derived, id=pv_total` 60 min × 1200 W → `pv_total_kwh() == 1.2`.
  - `test_energy.py::test_retention_long_reads_long`: mit `retention=RP_LONG` enthält die Abfrage `"long"."power"`, ohne Angabe `"raw"."power"`.
  - `test_days.py::test_source_days_uses_given_table`: `SourceDays` mit einer Test-Tabelle (`day`, `src_id`, `kwh`, `note`) schreibt und summiert über `src_id`.
- [ ] **Step 2:** `uv run pytest tests/unit/app/consumers -q` → neue Tests scheitern.
- [ ] **Step 3:** Umsetzen. `_core_points` holt `HOUSE` und `PV_TOTAL = ("derived", "pv_total")` mit. Bestehende Verbraucher-Tests bleiben unverändert grün (reiner Umbau).
- [ ] **Step 4:** ganze Prüfkette grün.
- [ ] **Step 5:** Commit `refactor(app): Energie und Tage für beliebige Leistungsquellen`.

### Task 2: PV-Quellen speichern (Migration 0018, Dienst)

**Files:**
- Create: `src/bindaems/app/db/migrations/versions/v0018_pv_sources.py`, `src/bindaems/app/pv/__init__.py`, `src/bindaems/app/pv/service.py`
- Modify: `src/bindaems/app/db/schema.py`
- Test: `tests/unit/app/pv/test_service.py`, `tests/unit/app/test_db.py` (`HEAD = "0018"`, neuer Test `test_migration_0018_creates_the_pv_tables_and_downgrades`)

**Interfaces:**
- Produces:
  - Tabelle `pv_source`: `id` PK, `name` String(60), `parent_id` FK `pv_source.id` ON DELETE RESTRICT nullable, `color` String(7), `source_kind` String(8), `power_ref` String(200), `power_unit` String(4) nullable, `kwp` Float nullable, `sort` Integer, `created_at`, `updated_at` (UtcDateTime); Index auf `parent_id`.
  - Tabelle `pv_source_day`: `day` String(10) PK, `source_id` Integer PK, `kwh` Float nullable, `note` String(80) nullable.
  - `PvSourceInput` (pydantic, `extra="forbid"`): `name`, `parent_id: DbId | None`, `color` `#rrggbb`, `source_kind: Literal["core","ha"]`, `power_ref`, `power_unit: Literal["W","kW"] | None`, `kwp: float | None` (0,1 ≤ kwp ≤ 1000), `sort`.
  - `@dataclass(frozen=True) PvSource` mit denselben Feldern plus `id` (erfüllt `PowerSource`).
  - `PvSourceError(message, status)`, `PvSourceService(engine, clock, audit, *, ha_enabled: bool)` mit `list()`, `get(id)`, `create(data, *, actor, source)`, `update(id, data, *, actor, source)`, `delete(id, *, actor, source)`; Audit-Objekt `pv_source`.
  - `PV_CORE_REF_RE = r"^(pv\.[a-z0-9_]+|pv_dc)\.power_w$"`.

- [ ] **Step 1: Failing tests** (`test_service.py`)
  - `test_core_ref_must_be_pv_signal`: `load.obergeschoss.power_w` → ValidationError mit „PV-Signal“; `pv.huawei.power_w` und `pv_dc.power_w` gültig.
  - `test_ha_ref_needs_unit`: `sensor.pv1_leistung` ohne Einheit → Fehler; mit `W` gültig.
  - `test_kwp_bounds`: 0.05 und 1001 → Fehler; 4.1 gültig; `None` gültig.
  - `test_string_cannot_have_children`: Anlage A, String S unter A, Eintrag unter S → `PvSourceError` 422 „Ein String kann keine Strings haben“.
  - `test_parent_must_exist`: `parent_id=99` → 404.
  - `test_delete_system_with_strings_rejected`: 409 „zuerst die Strings löschen“; ohne Strings gelöscht, seine `pv_source_day`-Zeilen auch.
  - `test_ha_source_rejected_without_ha_database`: `ha_enabled=False`, HA-Quelle → 422 „HA-Datenbank nicht eingerichtet (influxdb.ha_database)“.
  - `test_changes_are_audited`: create/update/delete schreiben je einen Audit-Eintrag mit `actor`, Quelle `ui`.
  - Migration: Upgrade bis 0018 legt beide Tabellen an, Downgrade entfernt sie, vorhandene Tabellen bleiben.
- [ ] **Step 2:** Tests scheitern.
- [ ] **Step 3:** Umsetzen nach dem Muster `consumers/service.py` (Sperre mit `threading.Lock`, `_get_parent` prüft „Elternteil hat selbst keinen Elternteil“). Ein `parent_id` auf sich selbst oder eine Anlage mit Strings unter einen anderen Elternteil zu hängen → 422.
- [ ] **Step 4:** Prüfkette grün.
- [ ] **Step 5:** Commit `feat(app): PV-Anlagen und Strings speichern (Migration 0018)`.

### Task 3: Baum mit Live-Werten, API und Laufzeit

**Files:**
- Create: `src/bindaems/app/pv/tree.py`, `src/bindaems/app/pv/routes.py`
- Modify: `src/bindaems/app/runtime.py` (Dienst, zweite `EnergyCache` `pv_energy`, `SourceDays` `pv_days`, HA-Refs der PV in `_ha_values_loop`, `pv_energy.refresh` in `_energy_loop`, `pv_days.fill_missing` nachts mit 3 Tagen neben `consumer_days`, beim Start als eigene Hintergrundaufgabe `pv-days-fill` mit `pv_fill_days(today)`, Router)
- Test: `tests/unit/app/pv/test_tree.py`, `tests/unit/app/pv/test_routes.py`, `tests/integration/test_app_pv.py`

**Interfaces:**
- Consumes: `PvSourceService`, `EnergyCache` (Task 1), `HaValueCache`, `LiveState`.
- Produces:
  - `@dataclass(frozen=True) PvNode`: `id: int | None` (`None` = „Nicht zugeordnet“), `name`, `color`, `kind: Literal["system","string","unassigned"]`, `power_w: float | None`, `kwh_today: float | None`, `kwp: float | None`, `kwh_per_kwp: float | None`, `note: str | None`, `children: list[PvNode]`, `strings_power_w: float | None`, `strings_kwh_today: float | None`.
  - `build_pv_tree(sources, power, note, energy, total_w: float | None, total_kwh: float | None) -> list[PvNode]` (Anlagen nach `sort`, dann Name; „Nicht zugeordnet“ zuletzt).
  - `pv_fill_days(today: date) -> int` in `pv/periods.py`: Tage bis zum 1. Januar des Vorjahres, höchstens 800 (`(today - date(today.year - 1, 1, 1)).days`).
  - `GET /api/pv` (viewer) → `{"total_w", "total_kwh", "tree": [...], "sources": [...], "energy_since"}`; `GET /api/pv/candidates` (admin) → `{"core": ["pv.huawei.power_w", "pv_dc.power_w", …], "ha": [...]}` (core: alle `pv.*.power_w` aus dem LiveState plus `pv_dc.power_w`; HA wie `consumers.values.candidates`); `POST /api/pv`, `PATCH /api/pv/{id}`, `DELETE /api/pv/{id}` (admin).

- [ ] **Step 1: Failing tests**
  - `test_tree.py::test_unassigned_is_total_minus_systems`: total 5000 W, Anlage 4200 W → „Nicht zugeordnet“ 800 W; Energie 10,0 − 8,4 = 1,6 kWh.
  - `test_tree.py::test_no_unassigned_without_systems`: keine Quellen → leere Liste.
  - `test_tree.py::test_unassigned_unknown_if_a_system_unknown`: eine Anlage `None` → Rest `None`.
  - `test_tree.py::test_strings_sum_without_rest_row`: Anlage 4200 W, Strings 1800 + 2550 W → `strings_power_w == 4350`, kein Kind „Nicht zugeordnet“.
  - `test_tree.py::test_kwh_per_kwp`: 8,2 kWh bei 4,1 kWp → 2,0; ohne kWp `None`; 0 kWh → 0,0.
  - `test_tree.py::test_zero_watts_is_a_value`: 0 W nachts → `power_w == 0.0`, `note is None`.
  - `test_routes.py`: viewer liest, viewer darf nicht anlegen (403), admin legt an (201), PATCH, DELETE (204, 409 bei Strings); Kandidaten nur admin.
  - `test_app_pv.py::test_pv_and_consumer_ids_do_not_mix`: Verbraucher 1 (`load.obergeschoss.power_w`) und PV-Quelle 1 (`pv.huawei.power_w`) mit verschiedenen Minutenmitteln → jede Seite zeigt ihre kWh.
  - `test_app_pv.py::test_fill_reaches_january_of_last_year`: `pv_fill_days(date(2026, 11, 8)) == 676`; ein Tag ohne Verlauf bekommt eine Zeile mit „kein Verlauf seit Mitternacht“ und zählt beim zweiten Lauf nicht als fehlend.
  - `test_app_pv.py::test_ha_without_database_shows_reason`: core-Quelle hat Werte, HA-Quelle Grund „HA-Datenbank nicht eingerichtet“.
- [ ] **Step 2:** Tests scheitern.
- [ ] **Step 3:** Umsetzen; Live-Leistung core aus `live.snapshot`, HA aus `HaValueCache` (wie `consumer_power`/`consumer_note`, verallgemeinert über `PowerSource`), `total_w` aus `live.derived()["pv_total_w"]`, `total_kwh` aus `pv_energy.pv_total_kwh()`.
- [ ] **Step 4:** Prüfkette grün.
- [ ] **Step 5:** Commit `feat(app): PV-Anlagen mit Leistung und Energie heute`.

### Task 4: Tageskurve und Ertrag je Woche, Monat und Jahr

**Files:**
- Create: `src/bindaems/app/pv/curve.py`, `src/bindaems/app/pv/periods.py`
- Modify: `src/bindaems/app/pv/routes.py`
- Test: `tests/unit/app/pv/test_curve.py`, `tests/unit/app/pv/test_periods.py`, `tests/unit/app/pv/test_routes.py`

**Interfaces:**
- Consumes: `InfluxReader`, `ha_selection`/`ha_points` (`consumers/values.py`, `energy.py`), `SourceDays.daily` (Task 1), `period_range` und `Period` (`stats/service.py`), `local_day_slots`.
- Produces:
  - `CURVE_STEP_S = 300`; `async def day_curve(reader, ha_database: str | None, sources: Sequence[PvSource], day: date) -> dict[int, list[tuple[int, float]]]` (ms, W; core aus `RP_LONG."power"` `mean("p_w")`, HA `mean("value")` × Einheit, je `GROUP BY time(5m)` `fill(none)`; der Tag ist `local_day_slots(day)[0] … local_day_slots(day+1)[0]`).
  - `GET /api/pv/day?date=YYYY-MM-DD` (viewer; Standard heute; Zukunft → 422) → `{"date", "series": [{"id", "points": [[ms, w], …], "note"}]}`; Grund „Verlauf nicht lesbar“ bzw. „HA-Datenbank nicht eingerichtet“ je Reihe.
  - `@dataclass(frozen=True) PvBucket(start: date, end: date)`; `buckets(period: Period, first: date, last: date) -> list[PvBucket]` (Woche/Monat: je Tag; Jahr: je Monat; Tag: ein Eintrag).
  - `period_energy(daily, today_kwh, buckets, today) -> PeriodEnergy` in `periods.py`: je Quelle (und `0` = PV gesamt) und Bucket die Summe der bekannten Tage oder `None`, wenn kein Tag des Buckets bekannt ist; Summe des Zeitraums über die bekannten Tage, `first_known: date | None`; Tage in der Zukunft zählen nicht; heute aus `pv_energy`.
  - `GET /api/pv/period?period=day|week|month|year&day=YYYY-MM-DD` (viewer; Standard `day` und heute; Tag in der Zukunft → 422) → `{"period", "first", "last", "buckets": [{"start", "end"}], "total": {"kwh": [...], "sum_kwh"}, "sources": [{"id", "kwh": [float | null, …], "sum_kwh", "kwh_per_kwp", "first_known", "note"}]}`.

- [ ] **Step 1: Failing tests**
  - `test_curve.py::test_core_curve_from_long_in_five_minutes`: die erzeugte Abfrage enthält `"long"."power"`, `time(300s)`, `"source"='pv'`, `"id"='huawei'`; Antwort 2 Punkte → 2 Punkte.
  - `test_curve.py::test_ha_curve_scales_kw`: HA-Entität in kW, Wert 1,8 → 1800 W.
  - `test_curve.py::test_dst_day_has_25_hours`: `day = date(2026, 10, 25)` → Fenster 25 h; `date(2026, 3, 29)` → 23 h.
  - `test_curve.py::test_unavailable_is_skipped`: HA-Zeile mit `value` `None` fehlt in den Punkten.
  - `test_periods.py::test_buckets`: Woche zum 2026-11-08 → 7 Buckets 02.–08.11.; Monat Februar 2027 → 28; Jahr 2026 → 12 Monate, März mit `start=2026-03-01`, `end=2026-03-31`.
  - `test_periods.py::test_month_today_from_cache_past_from_table`: gestern aus der Tabelle 12,3, heute aus dem Cache 4,5 → Monat endet mit `[…, 12.3, 4.5, None, …]` (Rest des Monats `None`), `sum_kwh` ist die Summe der bekannten Tage.
  - `test_periods.py::test_year_before_first_data_is_none`: Werte erst ab 2026-10-18 → Januar bis September `None`, Oktober die Summe ab 18.10., `first_known == date(2026, 10, 18)`; kWh/kWp aus `sum_kwh` ÷ kWp.
  - `test_periods.py::test_unassigned_from_total`: PV gesamt (ID 0) 30,0, Anlagen 27,5 → Rest 2,5 je Bucket; fehlt eine Anlage im Bucket → Rest `None`.
  - `test_routes.py::test_day_in_future_rejected`: morgen → 422.
- [ ] **Step 2:** Tests scheitern.
- [ ] **Step 3:** Umsetzen.
- [ ] **Step 4:** Prüfkette grün.
- [ ] **Step 5:** Commit `feat(app): PV-Ertrag je Tag, Woche, Monat und Jahr`.

### Task 5: Statistik „PV je Anlage“

**Files:**
- Modify: `src/bindaems/app/stats/service.py` (neuer Schlüssel `pv_sources` in `period`), `src/bindaems/app/runtime.py` (Übergabe von `pv_sources`, `pv_days`, `pv_energy`)
- Modify: `ui/src/lib/api/schemas.ts`, `ui/src/routes/statistik/+page.svelte`
- Create: `ui/src/lib/stats/PvCard.svelte`
- Test: `tests/unit/app/stats/test_service.py`, `ui/src/lib/stats/PvCard.test.ts`

**Interfaces:**
- Produces: `period(...)["pv_sources"]`: Liste `{"id", "name", "parent_id", "kwp", "kwh", "kwh_per_kwp", "note"}` (gleiche Regeln wie `_consumer_energy`; die gemeinsame Logik wird zu `_source_energy(sources, days, cache, first, last, today)`).

- [ ] **Step 1: Failing tests**
  - Python: Monat mit zwei gespeicherten Tagen 10,0 + 12,0 und heute 3,0 kWh, kWp 4,1 → `kwh == 25.0`, `kwh_per_kwp == 6.1` (auf 2 Stellen); ein fehlender Tag → `kwh is None` mit „Tag nicht gespeichert“.
  - Vitest: Karte „PV je Anlage“ zeigt Strings eingerückt unter ihrer Anlage, „25,0 kWh“ und „6,10 kWh/kWp“; ohne Quellen ist die Karte nicht da; `kwh: null` zeigt „–“ und klein den Grund.
- [ ] **Step 2:** Tests scheitern.
- [ ] **Step 3:** Umsetzen; Karte nach „Verbraucher“ einordnen.
- [ ] **Step 4:** Prüfkette grün.
- [ ] **Step 5:** Commit `feat(ui): Statistik zeigt die Energie je PV-Anlage und String`.

### Task 6: Seite „PV-Anlagen“

**Files:**
- Create: `ui/src/routes/pv/+page.svelte`, `ui/src/lib/pv/PvSystemCard.svelte`, `ui/src/lib/pv/PvForm.svelte`, `ui/src/lib/pv/PvDayChart.svelte`, `ui/src/lib/pv/PvPeriodChart.svelte`, `ui/src/lib/pv/tree.ts`
- Modify: `ui/src/lib/nav.ts` (Eintrag `{ href: '/pv', label: 'PV-Anlagen', icon: 'pv' }` vor „Verbraucher“), `ui/src/lib/api/schemas.ts`, `tests/ui_world.py` (PV-Quellen und Antworten für E2E), `ui/tests/e2e/pages.spec.ts`
- Test: `*.test.ts` neben den Komponenten, `ui/src/lib/nav.test.ts`, `ui/tests/e2e/pv.spec.ts`

**Interfaces:**
- Consumes: `GET /api/pv`, `/api/pv/candidates`, `/api/pv/day`, `/api/pv/period`, POST/PATCH/DELETE (Task 3, 4); `periodRange`, `periodTitle`, `shift`, `parsePeriod`, `parseDay` aus `ui/src/lib/stats/period.ts`.
- Gestaltung: **Variante B „Karte je Anlage“** (Wahl des Betreibers am 8.11.2026) aus https://claude.ai/artifact/92eEBBsDHVoYCQ4oGqrjkx, Artboards „B · Karte je Anlage“, „Tag“ und „String hinzufügen“. Monate ohne Daten im Jahr erscheinen grau hinterlegt mit „noch keine Daten (Werte ab …)“.
- Produces: Seite von oben nach unten:
  1. Zeitraum-Leiste „Tag | Woche | Monat | Jahr“ mit `‹ {Titel} ›` und „Heute“ (wie Statistik), darunter die Zeile „PV gesamt {kWh im Zeitraum} · jetzt {kW}“; ist „Nicht zugeordnet“ bekannt und ≥ 0,1 kWh oder ≥ 50 W, darunter klein „davon nicht zugeordnet {kWh}“.
  2. **Eine Karte je Anlage** (`PvSystemCard.svelte`, Reihenfolge nach `sort`, dann Name): Kopf mit Name und „{kWp} kWp · {Quelle}“ („Victron-Zähler (AC)“ bzw. „Home Assistant“), Admins mit Stift-Knopf „{Name} ändern“; drei Kennzahlen „{Zeitraum}“ (kWh), „je kWp“ und „Jetzt“ (kW); hat die Anlage Strings, darunter „Strings im Vergleich (kWh je kWp)“ mit einer Zeile je String (Farbpunkt, Name, kWp, kWh/kWp fett und kWh) und einem Balken, dessen Länge kWh/kWp relativ zum besten String der Anlage ist (bester = 100 %); Strings ohne kWp zeigen nur kWh und keinen Balken; darunter klein „Strings zusammen {kWh} (DC, vor dem Wechselrichter)“. Fehlt ein Wert: „–“ und klein der Grund. Fehlen frühe Tage im Zeitraum, klein „ab 18.10.“. Unter den Karten (Admins) „Weitere Anlage hinzufügen“ (gestrichelt; ohne Anlagen „Anlage hinzufügen“ mit einem Satz, was hier erscheint).
  3. **Formular** (Dialog wie `ConsumerForm`): Name, „Gehört zu“ (keine = Anlage, sonst Auswahl der Anlagen), Farbe, Quelle (core-Signal aus Kandidaten oder HA-Entität mit Einheit), kWp (optional, Komma erlaubt), Reihenfolge; Löschen mit Bestätigung.
  4. **Diagramm** zum Zeitraum: Tag → `PvDayChart` (eine Linie je Eintrag in seiner Farbe, Tooltip „{Zeit} · {Name} {W}“); Woche/Monat/Jahr → `PvPeriodChart` (gruppierte Balken je Tag bzw. Monat, Tooltip „{Datum} · {Name} {kWh}“). Umschalter „Anlagen | Strings“ und „kWh | kWh/kWp“ (kWh/kWp nur für Einträge mit kWp; bei „Strings“ Standard kWh/kWp). Legende als Chips (`ChartLegend`).
  Live-Aktualisierung alle 15 s für Kopf, „Jetzt“ und den laufenden Zeitraum (wie „Verbraucher“); vergangene Zeiträume beim Öffnen und Blättern.

- [ ] **Step 1: Failing tests**
  - `nav.test.ts`: „PV-Anlagen“ steht direkt vor „Verbraucher“, für viewer und admin.
  - `tree.test.ts`: `cards(tree)` liefert je Anlage ihre Strings in `sort`-Reihenfolge und `barPct` je String; `formatKwp(2)` → „2,00“.
  - `PvSystemCard.test.ts`: 0 W zeigt „0 W“, `null` zeigt „–“ und den Grund; viewer sieht keinen Stift; Süd 5,68 und West 4,88 kWh/kWp → Balken 100 % und 86 %; String ohne kWp hat keinen Balken; Anlage ohne Strings zeigt keinen Vergleich.
  - `PvForm.test.ts`: „Gehört zu“ bietet nur Anlagen an (keine Strings, nicht sich selbst); kWp „4,1“ → `4.1`; HA ohne Einheit → Fehlermeldung, kein Absenden.
  - `PvPeriodChart.test.ts`: Jahr hat 12 Kategorien „Jan“ … „Dez“; `null` ergibt eine Lücke, keinen 0-Balken; Umschalter kWh/kWp blendet Einträge ohne kWp aus und sagt das in einer Zeile („ohne kWp: …“).
  - `page.test.ts`: `?zeitraum=monat&tag=2026-11-08` lädt `/api/pv/period?period=month&day=2026-11-08`; `‹` springt zum Oktober; Tag zeigt die Kurve, Monat die Balken.
  - E2E `pv.spec.ts`: admin legt Anlage „Huawei“ (`pv.huawei.power_w`, 9,8 kWp) und String „Süd“ (HA, W, 4,1 kWp) an, Tabelle zeigt beide; Wechsel auf „Jahr“ zeigt 12 Balkengruppen; Seite bei 360 px ohne seitliches Scrollen (hell und dunkel).
- [ ] **Step 2:** Tests scheitern.
- [ ] **Step 3:** Umsetzen mit den vorhandenen Bausteinen (`EChart`, `ChartLegend`, `palette.ts`, `format.ts`, `resource.svelte.ts`).
- [ ] **Step 4:** Prüfkette grün inkl. Playwright; Screenshots 390 px hell und 1440 px dunkel nach `/mnt/project-files/plaene/pv-anlagen/`.
- [ ] **Step 5:** Commit `feat(ui): Seite PV-Anlagen mit Strings und Ertrag je Tag, Woche, Monat und Jahr`.

### Task 7: Doku

**Files:**
- Modify: `docs/betrieb.md` (neuer Abschnitt „PV-Anlagen“ nach „Verbraucher aus Home Assistant“; Seitenübersicht-Tabelle um „PV-Anlagen“; Sicherung/Wiederherstellung nennt `pv_source`, `pv_source_day`), Spec (Nachtrag „Stand Plan PV je Anlage und String“ unter 6.9 und in der UI-Seitenliste), `docs/uebergabe.md` (Stand; „Offen an der Anlage“: kommt mit Stufe 2, Migration 0018, danach Anlagen anlegen)

- [ ] **Step 1:** Doku schreiben; `docs/uebergabe.md` nennt die Schritte nach dem Ausrollen: prüfen, dass die InfluxDB-Integration von HA die Template-Sensoren der Strings schreibt (`include`), dann Anlage „Huawei“ (`pv.huawei.power_w`, 9,8 kWp) mit den Strings „Süd“ (PV1, 4,1 kWp) und „West“ (PV2, 5,7 kWp) anlegen; der erste Start rechnet die Tage bis 1.1.2025 im Hintergrund nach. `docs/betrieb.md` erklärt die Gründe („HA-Datenbank nicht eingerichtet“, „kein Wert in den letzten 24 h“, „Tag nicht gespeichert“), dass Strings DC und Anlagen AC messen (deshalb „Strings zusammen“ etwas über der Anlage liegt) und wie ein weiteres System dazukommt (Victron-`pvinverter`: Instanz in `victron.instances.pv`, core und app neu starten, dann in der UI anlegen; anderes Gerät: Leistungssensor in HA).
- [ ] **Step 2:** Prüfkette grün.
- [ ] **Step 3:** Commit `docs: PV-Anlagen und Strings in Betrieb, Spec und Übergabe`.

### Abschluss

Gesamtreview über den Branch, Fix-Welle (Critical/Important mit vorher scheiterndem Test), Minors sammeln; Abschlussnachricht mit Ergebnis und Tests, Rulings, Deferred minors und der Frage nach der Integration in `main`; Zusammenfassung `/mnt/project-files/plaene/pv-anlagen-abschluss.md`.

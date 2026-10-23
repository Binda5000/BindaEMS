# Phase 4c – Ladeübersicht je Auto: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Auf der Seite „Statistik“ sieht der Betreiber je Auto, wann und wofür es geladen hat: ein Verlauf je Tag oder Monat in kWh und €, die Ladesessions nach Auto gefiltert, ein ganzes Jahr statt 200 Sessions und ein CSV-Export.

**Architecture:** Nur app und UI; core, `shared`, HA, `config.yaml` und die Datenbank bleiben unverändert (keine Migration).
- **app:** Die Zuordnung der Wallbox-Energie zu Sessions (`app/charging/sessions.py`) rechnet einmal je Zeitraum und gruppiert zusätzlich je Wiener Tag. Die Statistik (`app/stats/service.py`) leitet daraus die Summen je Auto (wie bisher) und das neue Feld `vehicle_buckets` ab, ohne eine zweite Zuordnung. `GET /api/charging/sessions` liefert bis zu 2000 statt 200 Sessions.
- **UI:** Eine neue Karte „Laden je Auto“ mit Auswahl des Autos, Umschalter kWh/€ und Balkendiagramm; die Auswahl steht in der Adresse (`auto=`) und filtert auch die Karte „Ladesessions“. Diese zeigt zuerst 50 Zeilen, lädt mit „Weitere 50 anzeigen“ nach und bietet „CSV herunterladen“.

**Tech Stack:** wie Plan 4a (Python, FastAPI, SQLAlchemy, pytest; SvelteKit 5, Valibot, ECharts, Vitest, Playwright). Keine neuen Abhängigkeiten.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Statistik (Phase 4) und die Absätze „Stand Plan 4a“ (Ladesessions, Statistik, Seite „Statistik“). Kostenregel aus Plan 4a: PV ins Auto kostet 0 ct, Akku zum Einstandspreis (Netzanteil × Bezugspreis + Abnutzung), Netz zum Bezugspreis des Slots. Dieser Plan fügt „Stand Plan 4c“ hinzu (Task 5).

## Einordnung

- **Anlass:** Der Betreiber fragte am 23.10.2026 nach einer Übersicht je Auto mit Ladesessions und Kosten je Tag, Woche und Jahr. Plan 4a liefert die Summen je Auto im Zeitraum (Karte „Autos“) und die Liste der Sessions; es fehlen drei Dinge:
  1. Die Liste lässt sich im UI nicht nach Auto filtern (die API kann es: `vehicle=`).
  2. Höchstens 200 Sessions: Mit zwei Autos reicht das in der Jahresansicht nicht.
  3. Kein Verlauf je Auto (etwa Kosten je Monat über das Jahr oder je Tag über den Monat).
- **Was bleibt:** Kacheln, Kosten, Energie-Diagramm und die Karte „Autos“ zeigen weiter das ganze Haus bzw. alle Autos; die Auswahl des Autos wirkt nur auf „Laden je Auto“ und „Ladesessions“.
- **Laufzeit:** Die Jahresansicht der Statistik braucht nach der Fix-Welle von 4a etwa 2,5 s, und die Zuordnung über das Jahr ist der größte Teil davon. Die Statistik rechnet weiterhin genau eine Zuordnung (jetzt mit Tagesgruppen). Die Session-Liste eines Jahres rechnet ihre eigene Zuordnung über die Spanne ihrer Sessions, also ebenfalls etwa ein Jahr; beide Abfragen laufen im UI parallel.
- **Ausrollen:** nur `ems-app` (wie 4a), keine Migration. Wann, entscheidet der Betreiber (Entscheidung 7).

## Vorgeschlagene Entscheidungen (zur Freigabe)

1. **Auswahl des Autos:** Knöpfe „Alle“, die Autos aus `config.yaml` (Reihenfolge und Namen aus `GET /api/limits`), „Gast“ und „Unbekannt“. Die Auswahl steht in der Adresse als `auto=<Schlüssel>` (ohne: „Alle“) und bleibt beim Blättern und beim Wechsel des Zeitraums erhalten. Ein Schlüssel, den die Liste nicht kennt, erscheint als eigener Knopf mit dem Schlüssel als Namen.
2. **Wirkung der Auswahl:** nur auf die neue Karte „Laden je Auto“ und auf „Ladesessions“. Kacheln, „Kosten“, „Energie“ und „Autos“ bleiben für das ganze Haus.
3. **Karte „Laden je Auto“:** Balken je Tag (Woche, Monat) oder je Monat (Jahr), Umschalter „kWh“ / „€“ (Standard kWh, nicht in der Adresse). Bei „Alle“ gestapelt je Auto, bei einem Auto gestapelt nach PV, Akku und Netz (kWh) bzw. ein Balken „Kosten“ (€). Der Tooltip nennt je Reihe den Wert und bei mehr als einer Reihe die „Summe“. Bei „Tag“ gibt es kein Diagramm, die Karte sagt: „Den Verlauf zeigen Woche, Monat und Jahr.“
4. **Unbekannte Kosten:** Ist der Preis eines Tages (oder Monats) unbekannt, fehlt dort der €-Balken dieses Autos, der Tooltip zeigt „–“ und die „Summe“ „–“. Die Kostenregel aus 4a bleibt unverändert (PV 0 ct, Akku zum Einstandspreis, Netz zum Bezugspreis).
5. **Mehr Sessions:** Die Grenze steigt von 200 auf **2000** (`MAX_SESSIONS`, `SESSIONS_LIMIT`). Die Karte zeigt zuerst 50 Zeilen und darunter „{n} von {m} angezeigt“ mit dem Knopf „Weitere 50 anzeigen“. Ab 2000 steht wie bisher „Angezeigt werden die 2000 neuesten Sessions.“
6. **CSV-Export:** Ein Knopf „CSV herunterladen“ in „Ladesessions“ speichert alle geladenen Sessions der Auswahl (bis 2000), im Browser erzeugt, ohne neuen Endpunkt. Format für Excel in Österreich: UTF-8 mit BOM, Trennzeichen `;`, Dezimalkomma, Zeiten in Wiener Zeit `TT.MM.JJJJ HH:MM`. Spalten: `Beginn;Ende;Wallbox;Auto;Energie kWh;PV kWh;Akku kWh;Netz kWh;Kosten EUR`; Energie mit 3 Stellen, Kosten mit 2; eine laufende Session hat ein leeres Ende, unbekannte Kosten ein leeres Feld. Dateiname `ladesessions-{first}-{last}.csv`, mit Auswahl `ladesessions-{first}-{last}-{auto}.csv`. *Falls du keinen Export willst: Task 4, Schritte 5 bis 7 entfallen.*
7. **Ausrollen:** entscheidest du beim Abschluss. Vorschlag: mit dem nächsten Pull von `ems-app` nach dem gemeinsamen Fenster, nicht in das Fenster nach dem 25.10.2026 gedrängt.

## Global Constraints

- Deutsch, Anrede „du“ in der Doku; Commits im Stil `feat(app): …`, `feat(ui): …`, `docs: …`; keine Modellbezeichnungen im Repo.
- Vor jedem Commit: `uv run ruff format --check . && uv run ruff check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports`, `uv run pytest -q --cov --cov-fail-under=75` (≥ 75 % gesamt, ≥ 90 % safety/control/actuators); bei UI-Änderungen `cd ui && pnpm check && pnpm lint && pnpm test`.
- In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- core, `shared`, `cerbo-watchdog` und die Migrationen bleiben unverändert. Kein Aktor ist live; nichts an der Anlage schalten.
- Euro-Beträge im UI wie überall: „0,52 €“ (`formatEur`), Energie mit `formatEnergy`.
- Bedienelemente mindestens 44 × 44 px; bei 360 px keine waagrechte Scrollleiste der Seite (`ui/tests/e2e/stats.spec.ts`).
- Ändert sich eine API-Antwort: `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py`, danach `pnpm test` (prüft die Vertragsdateien gegen die Schemas).
- `{#each}` über Texte vom Server: Index oder eindeutigen Schlüssel nehmen (Stolperstein der Übergabe).

## Review Focus

1. **Tagesgrenze und Zeitumstellung:** Ein Slot gehört zum Wiener Datum seines Beginns; am 25.10.2026 (25 Stunden) und am 29.03.2026 (23 Stunden) darf kein Slot fehlen oder doppelt zählen (Task 1, Test `test_attribute_days_on_the_dst_day`).
2. **Summen gleich wie bisher:** Die Karte „Autos“ muss nach dem Umbau dieselben Werte zeigen wie vorher, auch für „Unbekannt“ (Energie ohne Session) und für eine offene Session (Task 2, `test_vehicle_totals_equal_the_sum_of_the_buckets`; die bestehenden Tests von `_vehicles` bleiben unverändert grün).
3. **Laufzeit der Jahresansicht:** genau eine Zuordnung je Statistik-Abfrage (Task 2, angepasster `test_year_view_attributes_the_sessions_only_once`); die Session-Liste mit 2000 Einträgen in der Demo-Welt unter 3 s (Task 4, Step 7 misst einmal von Hand und schreibt das Ergebnis in den Ledger).
4. **Auswahl ohne passende Daten:** Ein Auto ohne Ladung im Zeitraum zeigt „Im Zeitraum wurde kein Auto geladen.“ im Diagramm-Bereich und „Keine Ladesessions im Zeitraum.“ in der Liste, kein leeres Diagramm (Task 3, Test `zeigt ohne Ladung des Autos keinen leeren Verlauf`).
5. **CSV in Excel:** Namen mit `;` oder `"` (z. B. ein umbenannter Wallbox-Name) müssen gequotet werden; Dezimalkomma statt Punkt (Task 4, Test `quotet Felder mit Semikolon und Anführungszeichen`).

---

### Task 1: Zuordnung je Wiener Tag

**Files:**
- Modify: `src/bindaems/app/charging/sessions.py`
- Test: `tests/unit/app/charging/test_sessions.py`

**Interfaces:**
- Produces:
  - `merge_splits(splits: Iterable[FlowSplit]) -> FlowSplit` – Summe; `cost_eur` ist `None`, sobald eine Teilsumme `None` ist; leer ergibt `FlowSplit(0.0, 0.0, 0.0, 0.0, 0.0)`.
  - `attribute_days(slots: Sequence[LedgerSlot], costs: Mapping[datetime, SlotCost], sessions: Sequence[StoredSession], now: datetime) -> dict[date, dict[str, FlowSplit]]` – je Wiener Datum des Slot-Beginns und Auto-Schlüssel (`vehicle_key`, Energie ohne Session `UNKNOWN`); nur Tage und Autos mit Wallbox-Energie über 0.
  - `SessionService.by_vehicle_days(first: date, last: date) -> dict[date, dict[str, FlowSplit]]` – die Tage `first` bis `last` (Wiener Zeit), eine Abfrage der Abrechnung und eine Zuordnung.
- Unverändert: `attribute(...)`, `SessionService.by_vehicle(...)`, `sessions(...)`.

- [ ] **Step 1: Gemeinsamen Durchlauf herausziehen (Refaktor ohne Verhaltensänderung)**

`attribute` enthält die Schleife über die Slots, die je Wallbox PV, Akku und Netz summiert, die Session sucht und die Kosten rechnet. Zieh sie in einen privaten Generator `_shares(slots, costs, sessions, now) -> Iterator[tuple[LedgerSlot, StoredSession | None, float, float, float, float | None]]` (Slot, Session oder `None`, PV-, Akku-, Netz-Wh, Kosten in ct). `attribute` summiert darüber wie bisher.

Run: `uv run pytest tests/unit/app/charging tests/unit/app/stats -q`
Expected: PASS (unverändert)

- [ ] **Step 2: Failing tests schreiben**

```python
def test_merge_splits_sums_and_keeps_unknown_cost() -> None:
    a = FlowSplit(1.5, 1.0, 0.5, 0.0, 0.01)
    b = FlowSplit(2.0, 0.0, 0.0, 2.0, 0.6)
    assert merge_splits([a, b]) == FlowSplit(3.5, 1.0, 0.5, 2.0, pytest.approx(0.61))
    assert merge_splits([a, FlowSplit(1.0, 0.0, 0.0, 1.0, None)]).cost_eur is None
    assert merge_splits([]) == FlowSplit(0.0, 0.0, 0.0, 0.0, 0.0)


def test_attribute_days_splits_at_vienna_midnight() -> None:
    # 21:45Z = 23:45 Ortszeit am 9., 22:00Z = 00:00 Ortszeit am 10. (MESZ)
    late = datetime(2026, 10, 9, 21, 45, tzinfo=UTC)
    session = stored(1, late, late + SLOT * 2)
    slots = [led(late, {"pv>wb:twc": 1000.0}), led(late + SLOT, {"pv>wb:twc": 500.0})]
    days = attribute_days(slots, {}, [session], NOW_LATE)
    assert days == {
        date(2026, 10, 9): {"tesla": FlowSplit(1.0, 1.0, 0.0, 0.0, 0.0)},
        date(2026, 10, 10): {"tesla": FlowSplit(0.5, 0.5, 0.0, 0.0, 0.0)},
    }


def test_attribute_days_energy_without_session_is_unknown() -> None:
    days = attribute_days([led(T0, {"pv>wb:evcs": 300.0})], {}, [], NOW)
    assert days == {date(2026, 10, 9): {UNKNOWN: FlowSplit(0.3, 0.3, 0.0, 0.0, 0.0)}}


def test_attribute_days_cost_unknown_only_on_that_day() -> None:
    session = stored(1, T0 - timedelta(days=1), T0 + SLOT)
    slots = [
        led(T0 - timedelta(days=1), {"grid>wb:twc": 1000.0}, price=30.0),
        led(T0, {"grid>wb:twc": 1000.0}),  # ohne Bezugspreis
    ]
    days = attribute_days(slots, {}, [session], NOW)
    assert days[date(2026, 10, 8)]["tesla"].cost_eur == pytest.approx(0.30)
    assert days[date(2026, 10, 9)]["tesla"].cost_eur is None


def test_attribute_days_add_up_to_attribute() -> None:
    sessions = [stored(1, T0, T0 + SLOT * 3), stored(2, T0, T0 + SLOT * 3, "guest", "evcs")]
    slots = [
        led(T0 + SLOT * i, {"pv>wb:twc": 400.0 + i, "battery>wb:evcs": 100.0, "grid>wb:twc": 50.0}, price=25.0)
        for i in range(3)
    ]
    costs = {slot.slot_start: cost(slot.slot_start) for slot in slots}
    _, per_vehicle = attribute(slots, costs, sessions, NOW)
    days = attribute_days(slots, costs, sessions, NOW)
    for key, split in per_vehicle.items():
        merged = merge_splits(day[key] for day in days.values() if key in day)
        assert merged.kwh == pytest.approx(split.kwh)
        assert merged.cost_eur == pytest.approx(split.cost_eur)


def test_attribute_days_on_the_dst_day() -> None:
    # 25.10.2026 hat 25 Stunden: 100 Slots von 24.10. 22:00Z bis 25.10. 23:00Z
    day_start = datetime(2026, 10, 24, 22, 0, tzinfo=UTC)
    slots = [led(day_start + SLOT * i, {"pv>wb:twc": 100.0}) for i in range(100)]
    session = stored(1, day_start, day_start + SLOT * 100)
    days = attribute_days(slots, {}, [session], datetime(2026, 10, 26, 12, 0, tzinfo=UTC))
    assert list(days) == [date(2026, 10, 25)]
    assert days[date(2026, 10, 25)]["tesla"].kwh == pytest.approx(10.0)


def test_service_by_vehicle_days_covers_the_local_days(service, engine, ledger, clock) -> None:
    clock.advance(timedelta(days=2))
    add_session(engine, T0, T0 + SLOT)  # Hilfsfunktion wie in den Service-Tests dieser Datei
    add_session(engine, T0 + timedelta(days=1), T0 + timedelta(days=1) + SLOT, "guest")
    ingest(ledger, T0, {"pv>wb:twc": 1000.0})
    ingest(ledger, T0 + timedelta(days=1), {"pv>wb:twc": 500.0})
    days = service.by_vehicle_days(date(2026, 10, 9), date(2026, 10, 10))
    assert {day: list(found) for day, found in days.items()} == {
        date(2026, 10, 9): ["tesla"],
        date(2026, 10, 10): ["guest"],
    }
    assert service.by_vehicle_days(date(2026, 10, 10), date(2026, 10, 10)) == {
        date(2026, 10, 10): {"guest": FlowSplit(0.5, 0.5, 0.0, 0.0, 0.0)}
    }
```

`NOW_LATE = datetime(2026, 10, 10, 6, 0, tzinfo=UTC)`; `UTC` aus `datetime`. Fehlt in `test_sessions.py` eine Hilfsfunktion zum Anlegen gespeicherter Sessions, nimm die vorhandene der Service-Tests dieser Datei (wie in `test_service_sessions_newest_first_with_split`).

- [ ] **Step 3: Tests laufen lassen, sie scheitern**

Run: `uv run pytest tests/unit/app/charging/test_sessions.py -q -k "merge_splits or attribute_days or by_vehicle_days"`
Expected: FAIL (ImportError `merge_splits`/`attribute_days`)

- [ ] **Step 4: `merge_splits`, `attribute_days` und `SessionService.by_vehicle_days` umsetzen**

`attribute_days` summiert `_shares` in `_Sum`-Objekten je `(slot.slot_start.astimezone(LOCAL_TZ).date(), key)` und gibt nur Einträge mit `kwh > 0` zurück. `by_vehicle_days` nimmt `start, end = local_day_slots(first)[0], local_day_slots(last)[-1] + SLOT`, liest `self._tracker.sessions(start, end)`, `self._ledger.slots(start, end)` und `self._costing.costs(start, end)` und ruft `attribute_days` einmal (wie `_attribute`, das du für das Muster nimmst). Docstrings deutsch wie die Nachbarn; im Modul-Docstring einen Satz zur Tagesgruppe ergänzen.

- [ ] **Step 5: Tests laufen lassen, sie bestehen**

Run: `uv run pytest tests/unit/app/charging -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/bindaems/app/charging/sessions.py tests/unit/app/charging/test_sessions.py
git commit -m "feat(app): Ladeenergie und Kosten je Auto und Wiener Tag"
```

---

### Task 2: Statistik liefert `vehicle_buckets`

**Files:**
- Modify: `src/bindaems/app/stats/service.py` (`period`, `_vehicles`)
- Modify: `ui/src/lib/api/schemas.ts` (`StatsSchema`)
- Modify: `ui/src/lib/api/contract/stats-*.json` (neu geschrieben)
- Modify: `docs/betrieb.md` (Abschnitt 8, „Statistik je Zeitraum“: Feldliste und ein Punkt „Verlauf je Auto“)
- Test: `tests/unit/app/stats/test_service.py`, `tests/unit/app/stats/test_routes.py`

**Interfaces:**
- Consumes: `SessionService.by_vehicle_days`, `merge_splits` (Task 1).
- Produces: in der Antwort von `GET /api/stats` das Feld
  ```
  vehicle_buckets: [{ start: str, vehicles: [{ key, kwh, pv_kwh, battery_kwh, grid_kwh, cost_eur }] }]
  ```
  Bei `day` eine leere Liste; bei `week`/`month`/`year` genau ein Eintrag je Eintrag von `buckets`, mit demselben `start` (Datum `JJJJ-MM-TT` bzw. Monat `JJJJ-MM`) in derselben Reihenfolge. `vehicles` je Bucket in der Reihenfolge von `vehicles` (Konfiguration, sonstige Schlüssel, `guest`, `unknown`), nur mit `kwh > 0`; ein Bucket ohne Ladung hat `vehicles: []`. UI-Typ: `Stats['vehicle_buckets']`.

- [ ] **Step 1: Failing tests schreiben**

```python
def test_vehicle_buckets_follow_the_buckets(stats) -> None:
    assert stats.period("day", TODAY)["vehicle_buckets"] == []
    for period in ("week", "month", "year"):
        result = stats.period(period, TODAY)
        assert [b["start"] for b in result["vehicle_buckets"]] == [
            b["start"] for b in result["buckets"]
        ]


def test_vehicle_buckets_per_day_in_config_order(stats, engine, price_all) -> None:
    yesterday = T0 - timedelta(days=1)
    add_session(engine, yesterday, yesterday + SLOT, "egolf", "evcs")
    add_session(engine, T0, T0 + SLOT, "guest", "evcs")
    add_session(engine, T0, T0 + SLOT, "tesla", "twc")
    ingest(stats._ledger, yesterday, {"pv>wb:evcs": 2000.0})
    ingest(stats._ledger, T0, {"pv>wb:twc": 1000.0, "grid>wb:evcs": 500.0})
    result = stats.period("week", TODAY)
    by_start = {b["start"]: b["vehicles"] for b in result["vehicle_buckets"]}
    assert [v["key"] for v in by_start["2026-10-08"]] == ["egolf"]
    assert [v["key"] for v in by_start["2026-10-09"]] == ["tesla", "guest"]
    assert by_start["2026-10-05"] == []
    tesla = by_start["2026-10-09"][0]
    assert (tesla["kwh"], tesla["pv_kwh"], tesla["cost_eur"]) == (1.0, 1.0, 0.0)
    assert set(tesla) == {"key", "kwh", "pv_kwh", "battery_kwh", "grid_kwh", "cost_eur"}


def test_vehicle_bucket_cost_unknown_only_where_the_price_is_missing(stats, engine) -> None:
    yesterday = T0 - timedelta(days=1)
    add_session(engine, yesterday, T0 + SLOT, "tesla", "twc")
    ingest(stats._ledger, yesterday, {"pv>wb:twc": 1000.0})
    ingest(stats._ledger, T0, {"grid>wb:twc": 1000.0})  # ohne Bezugspreis
    by_start = {b["start"]: b["vehicles"] for b in stats.period("week", TODAY)["vehicle_buckets"]}
    assert by_start["2026-10-08"][0]["cost_eur"] == 0.0
    assert by_start["2026-10-09"][0]["cost_eur"] is None
    [tesla] = stats.period("week", TODAY)["vehicles"]
    assert tesla["cost_eur"] is None


def test_year_vehicle_buckets_are_months(stats, engine) -> None:
    september = T0 - timedelta(days=20)
    add_session(engine, september, september + SLOT, "tesla", "twc")
    add_session(engine, T0, T0 + SLOT, "tesla", "twc")
    ingest(stats._ledger, september, {"pv>wb:twc": 1000.0})
    ingest(stats._ledger, T0, {"pv>wb:twc": 500.0})
    by_start = {b["start"]: b["vehicles"] for b in stats.period("year", TODAY)["vehicle_buckets"]}
    assert by_start["2026-09"][0]["kwh"] == 1.0 and by_start["2026-10"][0]["kwh"] == 0.5
    assert by_start["2026-01"] == []


def test_vehicle_totals_equal_the_sum_of_the_buckets(stats, engine, price_all) -> None:
    add_session(engine, T0 - timedelta(days=3), T0 - timedelta(days=3) + SLOT, "tesla", "twc")
    add_session(engine, T0, None, "tesla", "twc")  # offen
    ingest(stats._ledger, T0 - timedelta(days=3), {"grid>wb:twc": 800.0})
    ingest(stats._ledger, T0, {"pv>wb:twc": 600.0, "battery>wb:twc": 200.0})
    ingest(stats._ledger, T0 + SLOT, {"pv>wb:evcs": 100.0})  # ohne Session
    result = stats.period("month", TODAY)
    for vehicle in result["vehicles"]:
        parts = [v for b in result["vehicle_buckets"] for v in b["vehicles"] if v["key"] == vehicle["key"]]
        assert sum(p["kwh"] for p in parts) == pytest.approx(vehicle["kwh"])
        assert sum(p["cost_eur"] for p in parts) == pytest.approx(vehicle["cost_eur"])
```

`test_year_view_attributes_the_sessions_only_once` anpassen: `attribute_days` zählen (genau 1 Aufruf) und `attribute` (0 Aufrufe) für `stats.period("year", TODAY)`.

In `tests/unit/app/stats/test_routes.py`: ein Test `test_stats_route_rounds_the_vehicle_buckets`, der eine Session mit `{"pv>wb:twc": 1234.5678}` anlegt und für `period=week` `vehicle_buckets[...]["vehicles"][0]["kwh"] == 1.235` prüft (Muster: der vorhandene Rundungstest der Route).

- [ ] **Step 2: Tests laufen lassen, sie scheitern**

Run: `uv run pytest tests/unit/app/stats -q`
Expected: FAIL (`KeyError: 'vehicle_buckets'`, `attribute` statt `attribute_days`)

- [ ] **Step 3: `StatsService` umbauen**

`_vehicles(first, last)` ruft einmal `self._sessions.by_vehicle_days(first, last)`, summiert je Auto mit `merge_splits` (für `vehicles` und `battery_to_vehicles_kwh`, Werte und Reihenfolge wie bisher) und gibt zusätzlich die Tagesaufteilung zurück. Neue Methode `_vehicle_buckets(period: Period, buckets: list[dict[str, Any]], days: Mapping[date, Mapping[str, FlowSplit]], order: list[str]) -> list[dict[str, Any]]`: für `day` `[]`, sonst je `bucket["start"]` die Tage dieses Buckets (Woche/Monat: der Tag selbst; Jahr: alle Tage mit `_month_key(day) == start`) mit `merge_splits` je Auto zusammengefasst, Reihenfolge `order`, nur `kwh > 0`. `period()` setzt `"vehicle_buckets"` direkt nach `"battery_to_vehicles_kwh"`. `by_vehicle` bleibt in `SessionService` (andere Aufrufer).

- [ ] **Step 4: Tests laufen lassen, sie bestehen**

Run: `uv run pytest tests/unit/app/stats tests/unit/app/charging -q`
Expected: PASS

- [ ] **Step 5: Vertrag und Schema nachziehen**

`StatsSchema` in `ui/src/lib/api/schemas.ts` bekommt nach `battery_to_vehicles_kwh`:

```ts
	/** Woche/Monat: je Tag, Jahr: je Monat (gleiche `start` wie `buckets`); Tag: leer */
	vehicle_buckets: v.array(
		v.object({
			start: v.string(),
			/** Reihenfolge wie `vehicles`, nur mit Energie */
			vehicles: v.array(
				v.object({
					key: v.string(),
					kwh: v.number(),
					pv_kwh: v.number(),
					battery_kwh: v.number(),
					grid_kwh: v.number(),
					cost_eur: NullableNumber
				})
			)
		})
	),
```

Run: `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py -q && uv run pytest tests/integration/test_ui_contract.py -q && cd ui && pnpm check && pnpm test`
Expected: PASS; `git diff --stat ui/src/lib/api/contract` zeigt nur `stats-*.json`.

- [ ] **Step 6: Doku Abschnitt 8**

In `docs/betrieb.md`, „Statistik je Zeitraum“: `vehicle_buckets` in die Feldliste nach `battery_to_vehicles_kwh`; ein Punkt **Verlauf je Auto:** mit dem Inhalt des Interfaces-Blocks oben (leer bei „Tag“, gleiche `start` wie `buckets`, Reihenfolge, nur Energie über 0, `cost_eur` `null` bei unbekanntem Preis in diesem Bucket).

Run: `uv run pytest tests/unit/test_docs.py -q`
Expected: PASS

- [ ] **Step 7: Volle Kette und Commit**

Run: Global Constraints (Python und UI).

```bash
git add src/bindaems/app/stats/service.py tests/unit/app/stats ui/src/lib/api/schemas.ts ui/src/lib/api/contract docs/betrieb.md
git commit -m "feat(app): Statistik mit Verlauf je Auto (vehicle_buckets)"
```

---

### Task 3: Auswahl des Autos und Karte „Laden je Auto“

**Files:**
- Create: `ui/src/lib/stats/vehicles.ts`, `ui/src/lib/stats/vehicles.test.ts`
- Create: `ui/src/lib/stats/VehicleTrendCard.svelte`, `ui/src/lib/stats/VehicleTrendCard.test.ts`
- Modify: `ui/src/lib/charts/options.ts` (`vehicleTrendOption`), `ui/src/lib/charts/options.test.ts`
- Modify: `ui/src/routes/statistik/+page.svelte`, `ui/src/routes/statistik/page.test.ts`

**Interfaces:**
- Consumes: `Stats['vehicle_buckets']` (Task 2), `Limits['vehicles']`, `vehicleLabel`, `bucketLabels` (`$lib/stats/view`), `Palette`.
- Produces:
  - `interface VehicleChoice { key: string | null; label: string }` (`null` = „Alle“)
  - `vehicleChoices(limits: Limits | undefined, selected: string | null): VehicleChoice[]` – „Alle“, die Autos aus `limits.vehicles` in ihrer Reihenfolge mit `vehicleLabel(key, name)`, „Gast“ (`guest`), „Unbekannt“ (`unknown`); ein `selected`, das darin fehlt, als letzter Eintrag mit dem Schlüssel als Namen.
  - `parseVehicle(param: string | null): string | null` – leer oder fehlend → `null`, sonst der Wert.
  - `statsHref(period: Period, day: string, vehicle: string | null): string` – `/statistik?zeitraum=…&datum=…[&auto=…]` (URL-kodiert).
  - `vehicleColor(key: string, limits: Limits | undefined, palette: Palette): string` – Auto Nr. i aus `limits.vehicles`: `palette.series[(i + 1) % palette.series.length]`; `guest`: `palette.series[0]`; sonst `palette.muted`.
  - `vehicleTrendOption(stats: Stats, vehicle: string | null, unit: 'kwh' | 'eur', colorOf: (key: string) => string, palette: Palette): EChartsCoreOption`
  - `VehicleTrendCard` Props: `{ stats: Stats; vehicle: string | null; choices: VehicleChoice[]; colorOf: (key: string) => string; palette: Palette; onselect: (key: string | null) => void }`.

- [ ] **Step 1: Failing tests für `vehicles.ts`**

```ts
it('bietet Alle, die Autos der Konfiguration, Gast und Unbekannt', () => {
	const choices = vehicleChoices(limits(), null);
	expect(choices.map((c) => c.label)).toEqual(['Alle', 'Tesla Model 3', 'e-Golf', 'Gast', 'Unbekannt']);
	expect(choices.map((c) => c.key)).toEqual([null, 'tesla', 'egolf', 'guest', 'unknown']);
});

it('zeigt einen unbekannten Schlüssel aus der Adresse als eigenen Knopf', () => {
	expect(vehicleChoices(limits(), 'old-car').at(-1)).toEqual({ key: 'old-car', label: 'old-car' });
	expect(vehicleChoices(undefined, null).map((c) => c.key)).toEqual([null, 'guest', 'unknown']);
});

it('hält die Auswahl in der Adresse', () => {
	expect(statsHref('month', '2026-10-09', null)).toBe('/statistik?zeitraum=monat&datum=2026-10-09');
	expect(statsHref('year', '2026-10-09', 'tesla')).toBe('/statistik?zeitraum=jahr&datum=2026-10-09&auto=tesla');
	expect(parseVehicle(null)).toBeNull();
	expect(parseVehicle('')).toBeNull();
	expect(parseVehicle('egolf')).toBe('egolf');
});
```

`limits()` aus `$lib/testing/fixtures` (die Demo-Konfiguration hat `tesla` „Tesla Model 3“ und `egolf` „e-Golf“; gibt es keinen Fixture-Helfer `limits`, leg ihn dort wie `stats()` aus der Vertragsdatei `limits.json` an).

- [ ] **Step 2: Failing tests für `vehicleTrendOption`** (in `options.test.ts`)

```ts
it('stapelt im Verlauf alle Autos je Tag', () => {
	const option = vehicleTrendOption(trendStats(), null, 'kwh', () => '#123456', palette);
	expect(option.series.map((s) => [s.name, s.stack, s.data])).toEqual([
		['Tesla Model 3', 'autos', [1, 0]],
		['Gast', 'autos', [0.5, 2]]
	]);
	expect(option.xAxis.data).toEqual(['Mo. 05.10.', 'Di. 06.10.']); // Beschriftung aus bucketLabels
});

it('zeigt ein Auto nach PV, Akku und Netz und in Euro als Kosten', () => {
	const kwh = vehicleTrendOption(trendStats(), 'tesla', 'kwh', () => '#123456', palette);
	expect(kwh.series.map((s) => s.name)).toEqual(['PV', 'Akku', 'Netz']);
	const eur = vehicleTrendOption(trendStats(), 'tesla', 'eur', () => '#123456', palette);
	expect(eur.series.map((s) => [s.name, s.data])).toEqual([['Kosten', [0.3, 0]]]);
});

it('lässt unbekannte Kosten als Lücke und schreibt im Tooltip „–“ und die Summe', () => {
	const option = vehicleTrendOption(trendStats({ unknownCost: true }), null, 'eur', () => '#123456', palette);
	const text = option.tooltip.formatter([
		{ name: 'Mo. 05.10.', seriesName: 'Tesla Model 3', value: null, marker: '' },
		{ name: 'Mo. 05.10.', seriesName: 'Gast', value: 0.2, marker: '' }
	]);
	expect(text).toContain('Tesla Model 3: <b>–</b>');
	expect(text).toContain('Summe: <b>–</b>');
});
```

`trendStats` baut ein `Stats` mit `fixtures.stats({ period: 'week', buckets: […2 Tage…], vehicles: [tesla, guest], vehicle_buckets: […] })`; die Zahlen im Test oben legen ihn fest (Tesla: Tag 1 1 kWh / 0,30 € bzw. `null` bei `unknownCost`, Tag 2 nichts; Gast: 0,5 und 2 kWh, 0,2 €). Die Beschriftungen nimmt der Test aus `bucketLabels('week', …)`, wenn sie anders aussehen als oben.

Werte: Modus „Alle“: je Auto aus `stats.vehicles` (Reihenfolge, Name `vehicleLabel`, Farbe `colorOf(key)`) eine Balkenreihe mit `stack: 'autos'`, Wert `kwh` bzw. `cost_eur`. Lädt das Auto in einem Bucket nicht, ist der Wert 0; nur ein unbekannter Preis gibt `null` (Lücke). Ein Auto: kWh drei gestapelte Reihen „PV“, „Akku“, „Netz“ aus `pv_kwh`/`battery_kwh`/`grid_kwh` mit `palette.pv`, `palette.importPrice`, `palette.series[0]`; € eine Reihe „Kosten“ in `colorOf(vehicle)`. Achse „kWh“ bzw. „€“ (`valueAxis`). Tooltip wie `statsTooltip`, mit `formatEnergy` bzw. `formatEur` (`null` → „–“), bei mehr als einer Reihe zusätzlich „Summe“, die „–“ ist, sobald ein Wert `null` ist.

- [ ] **Step 3: Failing tests für `VehicleTrendCard` und die Seite**

`VehicleTrendCard.test.ts`:
- `zeigt die Auswahl als Knöpfe mit aria-pressed`: Überschrift „Laden je Auto“, Gruppe `role="group"` mit `aria-label="Auto wählen"`, „Alle“ gedrückt bei `vehicle: null`; Klick auf „e-Golf“ ruft `onselect('egolf')`.
- `schaltet zwischen kWh und €`: Gruppe `aria-label="Einheit"` mit „kWh“ (gedrückt) und „€“; nach Klick auf „€“ ist „€“ gedrückt.
- `zeigt bei Tag keinen Verlauf`: `stats({ period: 'day', vehicle_buckets: [] })` → Text „Den Verlauf zeigen Woche, Monat und Jahr.“, kein `EChart`.
- `zeigt ohne Ladung des Autos keinen leeren Verlauf`: `vehicle: 'egolf'`, keine Einträge für `egolf` → „Im Zeitraum wurde kein Auto geladen.“, kein `EChart`.

`page.test.ts` (Statistik): `?zeitraum=monat&datum=2026-10-09&auto=tesla` → `api.chargingSessions` mit `('2026-10-01', '2026-10-31', 'tesla', …)`; Klick auf „Weiter“ behält `auto=tesla` in `goto`; Klick auf „Alle“ entfernt `auto`.

Run: `cd ui && pnpm test -- src/lib/stats src/lib/charts src/routes/statistik`
Expected: FAIL

- [ ] **Step 4: Umsetzen**

- `vehicles.ts` und `vehicleTrendOption` wie im Interfaces-Block.
- `VehicleTrendCard.svelte`: `Card title="Laden je Auto"`, oben die beiden Knopfgruppen (Stil wie `.switch` der Seite, aktive Knöpfe mit `aria-pressed`), darunter das Diagramm (`EChart`, `label="Laden je Auto {Titel}"`, `height="18rem"`) oder einer der beiden Texte aus Step 3. Die Einheit ist lokaler `$state('kwh')`.
- `+page.svelte`: `vehicle = $derived(parseVehicle(page.url.searchParams.get('auto')))`; `open()` nimmt `statsHref(nextPeriod, nextDay, vehicle)`, eine neue Funktion `choose(key)` ruft `goto(statsHref(period, day, key), { keepFocus: true, noScroll: true })`. Der Schlüssel der Abfrage (`key` im `$effect`) bekommt `vehicle`, und `sessions` lädt `api.chargingSessions(key.first, key.last, key.vehicle ?? undefined, o)`. Die Karte steht zwischen `VehicleCard` und `PlannedCard`; `choices = $derived(vehicleChoices(limits.data, vehicle))`, `colorOf = (key) => vehicleColor(key, limits.data, palette)`.

- [ ] **Step 5: Tests laufen lassen, sie bestehen**

Run: `cd ui && pnpm check && pnpm lint && pnpm test`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add ui/src/lib/stats ui/src/lib/charts ui/src/routes/statistik ui/src/lib/testing
git commit -m "feat(ui): Statistik mit Auswahl des Autos und Verlauf je Auto"
```

---

### Task 4: Ladesessions – 2000, nachladen, CSV

**Files:**
- Modify: `src/bindaems/app/charging/routes.py` (`MAX_SESSIONS = 2000`)
- Modify: `ui/src/lib/api/endpoints.ts` (`SESSIONS_LIMIT = 2000`)
- Modify: `ui/src/lib/stats/SessionsCard.svelte`, `ui/src/lib/stats/SessionsCard.test.ts`
- Modify: `ui/src/lib/stats/view.ts` (`sessionsCsv`), `ui/src/lib/stats/view.test.ts`
- Modify: `ui/src/routes/statistik/+page.svelte` (Dateiname an die Karte)
- Modify: `ui/tests/e2e/stats.spec.ts`
- Modify: `tests/unit/test_docs.py`, `docs/betrieb.md` (Abschnitte 8 „Ladesessions und Kosten je Auto“ und 12 „Statistik“)

**Interfaces:**
- Consumes: `vehicle`, `statsHref` und die Karte aus Task 3.
- Produces:
  - `SESSIONS_PAGE = 50` (in `SessionsCard.svelte` oder `view.ts`, exportiert für Tests)
  - `sessionsCsv(sessions: ChargingSession[]): string` – Kopfzeile und eine Zeile je Session, `\r\n`, mit BOM `﻿` am Anfang.
  - `SessionsCard` Props neu: `filename: string` (z. B. `ladesessions-2026-10-01-2026-10-31-tesla.csv`).

- [ ] **Step 1: Failing tests (Python)**

`tests/unit/app/charging/test_routes.py::test_ui_knows_the_session_limit` bleibt und prüft jetzt `2000`. In `tests/unit/test_docs.py` (Statistik-Seite) die drei Zeilen mit `200` auf `2000` ändern und die Phrasen `"Weitere 50 anzeigen"`, `"CSV herunterladen"`, `"Laden je Auto"`, `"Den Verlauf zeigen Woche, Monat und Jahr."` in die Liste aufnehmen.

Run: `uv run pytest tests/unit/test_docs.py tests/unit/app/charging/test_routes.py -q`
Expected: FAIL

- [ ] **Step 2: Grenze anheben und Doku nachziehen**

`MAX_SESSIONS = 2000`, `SESSIONS_LIMIT = 2000`. `docs/betrieb.md`: in Abschnitt 8 „höchstens 2000 Einträge“; in Abschnitt 12, „Statistik“, die Karte „Laden je Auto“ (Auswahl, `auto=` in der Adresse, kWh/€, gestapelt je Auto bzw. nach Herkunft, bei „Tag“ der Satz aus Task 3, unbekannte Kosten als Lücke mit „–“) und in „Ladesessions“: „zuerst 50, ‚{n} von {m} angezeigt‘, ‚Weitere 50 anzeigen‘; ‚CSV herunterladen‘ speichert alle geladenen Sessions der Auswahl (Spalten, Format wie Entscheidung 6)“ und „Angezeigt werden die 2000 neuesten Sessions.“

Run: `uv run pytest tests/unit/test_docs.py tests/unit/app/charging -q`
Expected: PASS erst nach Step 4 (die Phrasen müssen auch in `ui/src` stehen); bis dahin scheitert nur `test_statistics_page_in_the_guide_uses_the_texts_of_the_ui` an `source`.

- [ ] **Step 3: Failing tests (UI)**

`SessionsCard.test.ts`:
- `zeigt zuerst 50 Sessions und lädt mit „Weitere 50 anzeigen“ nach`: 120 Sessions (`chargingSession({ id })`) → 50 Zeilen, Text „50 von 120 angezeigt“; Klick → 100 Zeilen, „100 von 120 angezeigt“; Klick → 120 Zeilen, Knopf weg, Text weg.
- `weist ab SESSIONS_LIMIT auf die Grenze hin`: der bestehende Test mit `SESSIONS_LIMIT` Sessions bleibt (Text „Angezeigt werden die 2000 neuesten Sessions.“).
- `bietet „CSV herunterladen“ mit dem Dateinamen`: Knopf vorhanden, sobald Sessions da sind; nicht bei `[]` und nicht bei `undefined`. Klick ruft `URL.createObjectURL` (gemockt) mit einem `Blob` vom Typ `text/csv;charset=utf-8` und setzt am erzeugten Link `download` auf `filename`.

`view.test.ts`:

```ts
it('schreibt die Sessions als CSV für Excel', () => {
	const csv = sessionsCsv([
		chargingSession({
			start: '2026-10-09T06:00:00+00:00',
			end: '2026-10-09T07:30:00+00:00',
			wallbox_name: 'Garage',
			vehicle: 'tesla',
			vehicle_name: 'Tesla Model 3',
			kwh: 12.3456,
			pv_kwh: 10,
			battery_kwh: 2.3456,
			grid_kwh: 0,
			cost_eur: 0.4567
		}),
		chargingSession({ open: true, end: null, cost_eur: null, vehicle: 'guest', vehicle_name: '' })
	]);
	const lines = csv.split('\r\n');
	expect(csv.startsWith('﻿')).toBe(true);
	expect(lines[0]).toBe('﻿Beginn;Ende;Wallbox;Auto;Energie kWh;PV kWh;Akku kWh;Netz kWh;Kosten EUR');
	expect(lines[1]).toBe('09.10.2026 08:00;09.10.2026 09:30;Garage;Tesla Model 3;12,346;10,000;2,346;0,000;0,46');
	expect(lines[2]).toMatch(/^[\d.]+ [\d:]+;;[^;]*;Gast;/);
	expect(lines[2].endsWith(';')).toBe(true); // Kosten unbekannt: leer
});

it('quotet Felder mit Semikolon und Anführungszeichen', () => {
	const csv = sessionsCsv([chargingSession({ wallbox_name: 'Garage; "links"' })]);
	expect(csv.split('\r\n')[1]).toContain(';"Garage; ""links""";');
});
```

Zahlen ohne Tausenderpunkt (`12345,678`, nicht `12.345,678`), damit Excel sie als Zahl liest.

Run: `cd ui && pnpm test -- src/lib/stats`
Expected: FAIL

- [ ] **Step 4: `SessionsCard` umsetzen**

`let shown = $state(SESSIONS_PAGE)`, zurückgesetzt, wenn sich `sessions` ändert (`$effect` auf die Referenz); Tabelle über `rows.slice(0, shown)`; darunter, wenn `rows.length > shown`, `<p class="muted">{shown} von {rows.length} angezeigt</p>` und `<button type="button">Weitere 50 anzeigen</button>`. Der Knopf „CSV herunterladen“ steht unter der Tabelle (neben dem Nachladen), erzeugt `new Blob([sessionsCsv(sessions)], { type: 'text/csv;charset=utf-8' })`, einen temporären `<a download={filename}>` und gibt die URL nach dem Klick mit `URL.revokeObjectURL` frei. `sessionsCsv` in `view.ts`: Zeiten mit `formatDateTime` (Wiener Zeit; liefert die Funktion ein anderes Format als `TT.MM.JJJJ HH:MM`, nimm `Intl.DateTimeFormat('de-AT', { timeZone: 'Europe/Vienna', … })` und halte es im Ledger fest), Zahlen mit `toFixed(3)`/`toFixed(2)` und `.` → `,`, Auto mit `vehicleLabel`.

`+page.svelte`: `filename = $derived(\`ladesessions-${range.first}-${range.last}${vehicle ? \`-${vehicle}\` : ''}.csv\`)` an `SessionsCard`.

- [ ] **Step 5: Tests laufen lassen, sie bestehen**

Run: `cd ui && pnpm check && pnpm lint && pnpm test` und `uv run pytest tests/unit/test_docs.py tests/unit/app/charging -q`
Expected: PASS

- [ ] **Step 6: E2E erweitern**

In `ui/tests/e2e/stats.spec.ts` ein Test `Statistik filtert die Ladesessions nach Auto und zeigt den Verlauf`: Monat Oktober 2026 (`T_DEMO`), Überschrift „Laden je Auto“ sichtbar, `canvas` in der Karte sichtbar; Klick auf „Tesla Model 3“ → Adresse enthält `auto=tesla`, jede Zeile der Tabelle „Ladesessions“ enthält „Tesla Model 3“; „Zurück“ → Adresse enthält weiter `auto=tesla`; Klick auf „€“ → „€“ gedrückt; Klick auf „Alle“ → `auto` nicht mehr in der Adresse. Ein zweiter kurzer Test: `page.waitForEvent('download')` nach „CSV herunterladen“, `suggestedFilename()` ist `ladesessions-2026-10-01-2026-10-31.csv`. Der bestehende 360-px-Test deckt die neue Karte mit ab (keine Änderung nötig, aber laufen lassen).

Run: `cd ui && pnpm build && pnpm e2e`
Expected: PASS (bei Breitenfehlern in der CI: `FONTCONFIG_FILE=$PWD/tests/e2e/fonts-ci.conf pnpm e2e`)

- [ ] **Step 7: Laufzeit messen**

Demo-Backend starten (`uv run python -m tests.e2e.ui_server --port 8099 --ui-dir ui/build`, PID merken), angemeldet `GET /api/charging/sessions?from=2025-10-10&to=2026-10-09` und `GET /api/stats?period=year&date=2026-10-09` je dreimal mit `curl -w '%{time_total}'` messen, Backend über die PID beenden. Ergebnis in den Ledger. Über 3 s für die Session-Liste: als Befund melden, nicht stillschweigend die Grenze senken.

- [ ] **Step 8: Volle Kette und Commit**

Run: Global Constraints (Python und UI) und E2E.

```bash
git add src/bindaems/app/charging/routes.py ui/src ui/tests/e2e/stats.spec.ts tests/unit/test_docs.py docs/betrieb.md
git commit -m "feat(ui): Ladesessions bis 2000, nachladen und als CSV speichern"
```

---

### Task 5: Spec und Übergabe

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md`
- Modify: `docs/uebergabe.md`
- Modify: `docs/verification/abnahme-phase-4.md` (ein Prüfpunkt)

- [ ] **Step 1: Spec**

Nach dem Absatz „Stand Plan 4b“ zur Seite „Statistik“ (UI) einen Absatz **„Stand Plan 4c“** (`docs/superpowers/plans/2026-10-23-phase-4c-ladeuebersicht-je-auto.md`, Entscheidungen 1 bis 6; Bedienung: `docs/betrieb.md`, Abschnitt 12, „Statistik“): Karte „Laden je Auto“, Auswahl `auto=`, Wirkung nur auf „Laden je Auto“ und „Ladesessions“, 2000 Sessions mit Nachladen, CSV. Beim API-Absatz „Stand Plan 4a“ zur Statistik einen Satz zu `vehicle_buckets` mit Verweis auf `docs/betrieb.md`, Abschnitt 8.

- [ ] **Step 2: Abnahme-Checkliste**

In `docs/verification/abnahme-phase-4.md` einen Punkt: „Laden je Auto: Im Monat stimmt die Summe der Balken eines Autos (kWh und €) mit der Zeile in ‚Autos‘ überein; die CSV des Monats öffnet in Excel mit Zahlen in den Spalten Energie und Kosten.“

- [ ] **Step 3: Übergabe**

Kurzfassung oben: Plan 4c umgesetzt (Branch, Stand), nur app und UI, keine Migration, Ausrollen nur `ems-app` laut Entscheidung 7. Aufgeschobene Kleinigkeiten aus dem Ledger unter „Bewusst offen gelassen“, „Aufgeschobene Kleinigkeiten aus Plan 4c“.

Run: `uv run pytest tests/unit/test_docs.py -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add docs
git commit -m "docs: Plan 4c in Spec, Abnahme und Übergabe"
```

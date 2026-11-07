# Lebenszeichen und USV-Reichweite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fällt die VM, der Proxmox-Cluster, das Internet, der core oder das Netz aus, kommt binnen Minuten eine Nachricht aufs Handy, über einen externen Dienst außerhalb des Clusters (Idee 1.3). Dazu zeigen UI und Home Assistant, wie lange der Akku das Haus bei einem Netzausfall noch versorgt, und die app führt ein Protokoll der Netzausfälle (Idee 1.5).

**Architecture:** Nur app, UI und HA; der core bleibt unverändert. Ein neuer Baustein `bindaems.app.monitoring` hat drei Teile: `backup_range.py` rechnet die Reichweite (Lastannahme nur aus den Lasten an AC-Out der Multis, Formel), `outages.py` erkennt Netzausfälle am Signal `system.active_in_source` (240 = Inselbetrieb) und speichert sie in einer neuen Tabelle `grid_outage`, `heartbeat.py` pingt alle 5 min eine URL im Stil von healthchecks.io (`<url>` = in Ordnung, `<url>/fail` = Störung, Text im Body). Die URL steht als Secret `BINDAEMS_PING_URL` in der `.env`; ohne sie ist das Lebenszeichen aus. Bleibt der Ping aus (VM, Cluster, Internet weg), meldet der Dienst das selbst.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy/Alembic, httpx, pytest; Svelte 5, TypeScript, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md` (1.1 Punkt 1 „USV-Reserve“, 1.4 Nicht-Ziele, 8.5 Netzausfall/SAFE, 11.4 Flüsse, Übersicht „Akku-Kachel“, Nachtrag 30.10.2026); Ideen 1.3 und 1.5 in `/mnt/project-files/ideen/brainstorming-2026-11.md`. Der Plan schreibt die Spec in Task 8 als „Stand Plan Lebenszeichen und USV-Reichweite“ nach.

## Global Constraints

- core, `cerbo-watchdog` und alle Aktoren bleiben unverändert; nichts schaltet an der Anlage.
- Das Lebenszeichen ist keine Sicherheitsfunktion (Spec 1.4 „keine Cloud-Abhängigkeiten für sicherheitsrelevante Funktionen“): Fällt der Dienst oder das Internet aus, läuft alles andere unverändert weiter, und kein Fehler des Pings bricht einen anderen Job ab.
- Die Ping-URL ist ein Secret: nur `BINDAEMS_PING_URL`, nie in `config.yaml`, nie im Log, nie in API, HA oder UI (dort nur „eingerichtet: ja/nein“).
- Reichweite nur mit den **Lasten an AC-Out** (Hinweis des Betreibers vom 4.11.2026: nur AC-Out ist USV-versorgt; Lasten an AC-In, Wallboxen und alles andere fallen bei Netzausfall weg). Quelle ist das Victron-Signal `consumption_output.l1..l3.power_w` (`Ac/ConsumptionOnOutput`), nie die Hauslast der Abrechnung.
- Reichweite immer **ohne PV** gerechnet und so beschriftet; nie negativ, nie NaN, nie unendlich; ohne Grundlage keine Zahl, sondern „zu wenig Daten“.
- Texte deutsch, Zahlen mit Komma, `NBSP` vor Einheiten (wie `energyText`).
- Neue Felder in `config.yaml` haben Standardwerte; sie dürfen erst in die `config.yaml` der Anlage, wenn core und app aus diesem Stand laufen (der core liest dieselbe Datei).
- Migration: die nächste freie Nummer auf `main` bei Beginn von Task 3; „Betrieb im Blick“ bringt `0016` (`v0016_ops.py`), dieser Plan dann `0017` mit `down_revision = "0016"`.
- Ausrollen: nur mit einem Sammel-Pull (core + app gemeinsam, nie `ems-app` allein), frühestens mit Stufe 2 ab 23.11.2026; bis 22.11. laufen Abnahmen.
- Prüfkette vor jedem Commit wie in den Projektregeln (ruff, mypy strict, lint-imports, pytest mit Abdeckung ≥ 75 % gesamt, UI check/lint/test).

## Entscheidungen

Freigegeben vom Betreiber am 7.11.2026 („Bitte starten“). Die Karten: 5 hat er am 4.11. gewählt, 6 gilt mit der Freigabe in der empfohlenen Form; 7 bleibt bei 5 %, bis er seinen Wert nennt.

1. **Wer pingt: die app** (nicht der core, nicht der Cerbo). Die app läuft in der VM im Cluster: Fehlt ihr Ping, ist die VM, der Cluster oder das Internet weg. Der core bleibt frei von Internet-Abhängigkeiten, der Cerbo-Watchdog unverändert. Ein Cron am Cerbo würde einen VM-Ausfall nicht sehen.
2. **Dienst: healthchecks.io-Protokoll** (gratis bis 20 Checks; auch selbst gehostet möglich). Erfolg = `POST <url>`, Störung = `POST <url>/fail`, je mit einer Textzeile als Body (healthchecks zeigt sie im Verlauf und in vielen Benachrichtigungen). Einrichtung beim Dienst: Periode 5 min, Karenz 10 min, Benachrichtigung z. B. über Telegram, ntfy oder Pushover. Das ist dein Schritt an der Anlage (Task 8 schreibt die Anleitung).
3. **Takt 5 min** (`app.ping_interval_s`, Standard 300, 60–3600), erster Ping 30 s nach dem Start der app.
4. **Wann „Störung“ (`/fail`):** core seit mindestens 120 s nicht erreichbar; Netzausfall (Inselbetrieb); core im Zustand SAFE (veraltete Cerbo-Daten u. a.). OBSERVE und DEGRADED gelten als in Ordnung (OBSERVE ist normal bei Regelbetrieb aus); der Text nennt die Betriebsart. Mehr prüft der Betriebscheck (Idee 1.1), nicht dieses Lebenszeichen.
5. **(entschieden 4.11.) Netzausfall meldet das Lebenszeichen als Störung**, mit SOC und Reichweite im Text („Netzausfall seit 14:03 · SOC 62 % · reicht ≈ 18 h ohne PV“); kehrt das Netz zurück, kommt mit dem nächsten Ping „wieder in Ordnung“. Ist der Router nicht an der USV, bleibt der Ping ganz aus, und der Dienst meldet das nach der Karenz ebenfalls.
6. **(mit der Freigabe 7.11.) Lastannahme der Reichweite: mittlere Last an AC-Out der letzten 7 vollen Tage** (24 h). Quelle: InfluxDB, Retention `long`, Messung `power` mit `source='load'`, `id='consumption_output'`, Phasen L1–L3, je Viertelstunde gemittelt und über die Phasen summiert (eine Viertelstunde zählt nur mit allen drei Phasen). Die Nacht-Grundlast an AC-Out (00:00–05:00 der letzten 7 Nächte) rechnet die app trotzdem und zeigt sie als zweite Zahl („nachts bis ≈ 40 h“) in API, HA-Attributen und der Karte auf der Seite System. Begründung: Was an AC-Out hängt, läuft bei einem Ausfall tagsüber wie sonst weiter; nur mit der Nachtlast wäre die Zahl zu optimistisch.
7. **Untergrenze im Inselbetrieb:** neues Feld `battery.outage_floor_soc_pct` (Standard 5 %, Bereich 0 bis `reserve_soc_pct`). Victron ESS entlädt bei Netzausfall unter den Min-SOC, bis das BMS abschaltet; diese Schwelle trägst du für deinen Akku ein (Frage in der Freigabe).
8. **Formel:** `η = √(planning.roundtrip_pct / 100)` (Entladeseite, Standard 88 % → 0,938). `kWh(soc) = max(0, soc − Untergrenze) / 100 × usable_kwh × η`. Reichweite `h = kWh × 1000 / Last_W`. Zwei Zahlen: **jetzt** (aktueller SOC) und **ab Reserve** (`reserve_soc_pct`, also was die Reserve allein trägt). Last unter 50 W gilt als unplausibel → keine Zahl.
9. **Während eines Netzausfalls** rechnet die Reichweite mit der mittleren Last an AC-Out (Summe `consumption_output.l1..l3.power_w` aus dem Live-Stand) seit Beginn des Ausfalls, sobald 15 min erfasst sind; vorher mit der Lastannahme.
10. **Anzeige:** unter 10 h mit einer Nachkommastelle („≈ 7,5 h“), sonst ganze Stunden („≈ 31 h“); HA-Sensor in h mit einer Nachkommastelle.
11. **Protokoll der Netzausfälle:** ab dem Ausrollen, kein Nachtragen aus der Vergangenheit (`active_in_source` steht nicht in InfluxDB). Erfasst wird jeder Ausfall, auch kurze. Je Ausfall: Beginn, Ende, Dauer, SOC zu Beginn, am Ende und das Minimum, mittlere Last an AC-Out und Energie an AC-Out. Ein Neustart der app während eines Ausfalls setzt die offene Zeile fort; ist das Netz beim Start schon zurück, schließt sie mit „Ende geschätzt“ (Zeit des Starts).
12. **Orte:** Akku-Kachel der Übersicht bekommt eine Zeile „Netzausfall: ≈ 31 h, ab Reserve ≈ 14 h“ (während eines Ausfalls hervorgehoben: „Netzausfall seit 14:03 · reicht noch ≈ 18 h“). Seite System: Karte „USV und Netzausfälle“ (Lastannahme, Nachtlast, Untergrenze, Lebenszeichen, letzte 20 Ausfälle). Das Lebenszeichen erscheint zusätzlich als Baustein in der Liste der Komponenten. HA: Sensoren `usv_reichweite`, `usv_reichweite_reserve`, Binärsensor `netzausfall`.

## Review Focus

1. **Internet weg, VM läuft:** jeder Ping scheitert (Timeout, DNS, Proxy). Erwartet: kein Absturz, kein Neustart anderer Jobs, eine Logzeile je Wechsel (nicht je Versuch), Komponente „Lebenszeichen nicht gesendet seit 14:05“, nächster Versuch im Takt. Test in Task 4.
2. **Neustart der app während eines Netzausfalls** und Neustart nach dem Ende: keine zweite offene Zeile, kein Ausfall ohne Ende. Test in Task 3.
3. **SOC ungültig oder veraltet, core getrennt:** keine Reichweite (`None`), kein „0 h“; das Lebenszeichen meldet erst nach 120 s „core nicht erreichbar“. Tests in Task 2 und Task 4.
4. **InfluxDB nicht erreichbar, neue Anlage oder Lücken bei `consumption_output`:** weniger als 192 vollständige Viertelstunden (2 Tage, alle drei Phasen) → keine Lastannahme, Text „zu wenig Daten“, keine Division durch null; scheitert nur die Abfrage, gilt die letzte gültige Lastannahme bis 48 h weiter. Test in Task 2.
5. **Secret-Leck:** die URL (mit UUID) erscheint weder im Log noch in `/api/backup`, `/api/system` oder HA, auch nicht in einer Fehlermeldung von httpx. Test in Task 4.

---

### Task 1: Konfiguration und Secret

**Files:**
- Modify: `src/bindaems/shared/config.py` (`BatteryConfig`, `AppConfig`, `Secrets`)
- Modify: `deploy/config.example.yaml`, `deploy/.env.example`
- Test: `tests/unit/shared/test_config.py` (bestehend; neue Tests anhängen)

**Interfaces:**
- Produces: `BatteryConfig.outage_floor_soc_pct: float = 5.0`, `AppConfig.ping_interval_s: float = 300.0`, `Secrets.ping_url: SecretStr | None = None`

- [ ] **Step 1: Failing tests**
  - `test_outage_floor_defaults_to_5` → `Config` aus der Beispielkonfiguration ohne das Feld hat `battery.outage_floor_soc_pct == 5.0`.
  - `test_outage_floor_above_reserve_rejected` → `reserve_soc_pct: 20`, `outage_floor_soc_pct: 25` → `ValidationError` mit „outage_floor_soc_pct darf nicht über reserve_soc_pct liegen“.
  - `test_ping_interval_bounds` → 59 und 3601 abgelehnt, 60 und 3600 angenommen.
  - `test_ping_url_must_be_https` → `BINDAEMS_PING_URL=http://x` abgelehnt („ping_url muss mit https:// beginnen“), `https://hc-ping.com/abc` angenommen, leer = `None`.
- [ ] **Step 2:** `pytest tests/unit/shared/test_config.py -k "outage_floor or ping"` → FAIL (Felder fehlen).
- [ ] **Step 3:** Felder ergänzen: `outage_floor_soc_pct: Annotated[float, Field(ge=0, le=100)] = 5.0` mit Prüfung im bestehenden `model_validator` (`≤ reserve_soc_pct`); `ping_interval_s: Annotated[float, Field(ge=60, le=3600)] = 300.0`; `ping_url` mit `field_validator`. In `config.example.yaml` beide Felder auskommentiert mit Erklärung (Untergrenze: „SOC, bei dem das BMS im Inselbetrieb abschaltet“), in `.env.example` `BINDAEMS_PING_URL=` mit Kommentar „healthchecks.io-Ping-URL, leer = Lebenszeichen aus“.
- [ ] **Step 4:** Tests grün; `tests/unit/test_deploy_files.py` grün (prüft die Beispieldateien).
- [ ] **Step 5:** Commit `feat(shared): Untergrenze im Inselbetrieb, Ping-Takt und Ping-URL`

---

### Task 2: Reichweite rechnen

**Files:**
- Create: `src/bindaems/app/monitoring/__init__.py`, `src/bindaems/app/monitoring/backup_range.py`
- Test: `tests/unit/app/monitoring/__init__.py`, `tests/unit/app/monitoring/test_backup_range.py`

**Interfaces:**
- Consumes: `InfluxReader.query` und `RP_LONG`, `quote_ident`/`quote_str` aus `bindaems.app.history.influx` (Muster: `build_query` in `history/series.py`), `LOCAL_TZ`
- Produces:
  - `@dataclass(frozen=True) class LoadBasis: day_w: float | None; night_w: float | None; day_slots: int; night_slots: int; first_day: date; last_day: date`
  - `def ac_out_query(start: datetime, end: datetime) -> str` — `SELECT mean("p_w") FROM "long"."power" WHERE "source"='load' AND "id"='consumption_output' AND time … GROUP BY time(15m),"phase" fill(none)`
  - `def slot_loads(series: Sequence[Series]) -> dict[datetime, float]` — je Viertelstunde die Summe der drei Phasen; Viertelstunden ohne alle drei Phasen fallen weg
  - `def load_basis(loads: Mapping[datetime, float], today: date) -> LoadBasis` — Tage `today−7 … today−1` (lokal), Tag = alle Viertelstunden, Nacht = mit lokalem Beginn 00:00–04:45; `day_w` nur ab `MIN_DAY_SLOTS = 192`, `night_w` ab `MIN_NIGHT_SLOTS = 40`, sonst `None`.
  - `@dataclass(frozen=True) class BackupRange: now_h: float | None; reserve_h: float | None; load_w: float | None; load_source: Literal["tag", "ausfall"] | None; night_h: float | None`
  - `def backup_range(*, soc_pct: float | None, reserve_pct: float, floor_pct: float, usable_kwh: float, roundtrip_pct: float, basis: LoadBasis | None, outage_load_w: float | None) -> BackupRange` (`basis=None` und kein `outage_load_w` → alle Stunden `None`)
  - `def hours_text(hours: float | None) -> str` — „≈ 7,5 h“, „≈ 31 h“, `None` → „zu wenig Daten“
  - `class BackupRangeService(influx: InfluxReader, clock)` mit `async refresh() -> None` (Abfrage über 7 Tage, dann `load_basis`; bei `InfluxQueryError` bleibt die letzte Grundlage bis 48 h), `basis() -> LoadBasis | None`, `async run() -> None` (beim Start und täglich 00:20 lokal, über `next_local` aus `bindaems.app.schedule`).

- [ ] **Step 1: Failing tests** (`test_backup_range.py`, `Series` als Fake mit Spalten `time`, `mean` und Tag `phase`):
  - `test_query_targets_ac_out` → `ac_out_query` enthält `'consumption_output'`, `"long"."power"`, `GROUP BY time(15m)` und keine Hauslast.
  - `test_slot_needs_three_phases` → L1 und L2 je 200 W, L3 fehlt → Viertelstunde fällt weg; mit L3 100 W → 500 W.
  - `test_day_and_night_mean` → 7 Tage, Last an AC-Out tagsüber 800 W, nachts 400 W → `night_w == 400`, `day_w == pytest.approx((20*400 + 76*800)/96)`.
  - `test_too_few_slots_gives_none` → 191 Viertelstunden → `day_w is None`.
  - `test_influx_error_keeps_last_basis` → erste Abfrage gut, zweite wirft `InfluxQueryError` → `basis()` unverändert; 49 h nach der letzten guten → `None`.
  - `test_today_is_excluded` → Viertelstunden von heute ändern das Ergebnis nicht.
  - `test_formula` → SOC 60, Reserve 20, Untergrenze 5, 96 kWh, 88 %, `day_w=700`: `now_h == pytest.approx(0.55*96*sqrt(0.88)*1000/700)`, `reserve_h == pytest.approx(0.15*96*sqrt(0.88)*1000/700)`.
  - `test_soc_below_floor_is_zero` → SOC 3, Untergrenze 5 → `now_h == 0.0`.
  - `test_soc_unknown` → `soc_pct=None` → `now_h is None`, `reserve_h` trotzdem gesetzt.
  - `test_implausible_load` → `day_w=30` → beide `None`.
  - `test_outage_load_wins` → `outage_load_w=1200` → `load_w == 1200`, `load_source == "ausfall"`.
  - `test_night_hours` → `night_h` mit `night_w` gerechnet, `None` ohne.
  - `test_hours_text` → 7.46 → „≈ 7,5 h“, 31.4 → „≈ 31 h“, 0 → „≈ 0 h“, `None` → „zu wenig Daten“ (mit `NBSP` vor „h“).
- [ ] **Step 2:** `pytest tests/unit/app/monitoring/test_backup_range.py` → FAIL (Modul fehlt).
- [ ] **Step 3:** `backup_range.py` umsetzen (Formel nach Entscheidung 8, Vorrang der Ausfall-Last nach Entscheidung 9). Den Wiener Kalender über `LOCAL_TZ` wie in `forecast/load.py`; Zeitumstellung: Tagesgrenzen lokal, Slots aus `slots(start_utc, end_utc)`.
- [ ] **Step 4:** Tests grün.
- [ ] **Step 5:** Commit `feat(app): Reichweite des Akkus bei Netzausfall`

---

### Task 3: Netzausfälle erkennen und speichern

**Files:**
- Create: `src/bindaems/app/db/migrations/versions/v0017_grid_outage.py`
- Modify: `src/bindaems/app/db/schema.py` (Tabelle `grid_outage`)
- Create: `src/bindaems/app/monitoring/outages.py`
- Test: `tests/unit/app/monitoring/test_outages.py`, `tests/unit/app/test_db.py` (Migration auf/ab)

**Interfaces:**
- Consumes: `LiveState` (`connected`, `value("system.active_in_source")`, `value("battery.soc_pct")`, `value("consumption_output.l{1,2,3}.power_w")`), `Clock`
- Produces:
  - Tabelle `grid_outage`: `id` Integer PK, `start` UtcDateTime, `end` UtcDateTime NULL, `end_estimated` Boolean, `soc_start` Float NULL, `soc_end` Float NULL, `soc_min` Float NULL, `load_wh` Float (Energie an AC-Out), `covered_s` Float (Zeit mit Lastwert aller drei Phasen), Index auf `start`.
  - `ISLAND_SOURCE = 240` (eigene Konstante in der app; `app` importiert den core nicht)
  - `@dataclass(frozen=True) class Outage: id: int; start: datetime; end: datetime | None; end_estimated: bool; soc_start: float | None; soc_end: float | None; soc_min: float | None; load_wh: float; covered_s: float` mit `mean_w -> float | None` (`load_wh × 3600 / covered_s`, `None` unter 900 s)
  - `class OutageTracker(engine, live, clock, *, poll_s: float = 10.0)`: `async run()`, `def poll() -> None` (ein Schritt, für Tests), `current() -> Outage | None`, `recent(limit: int = 20) -> list[Outage]`.

- [ ] **Step 1: Failing tests** (Fake-`LiveState` und -Uhr, SQLite in `tmp_path`):
  - `test_island_starts_outage` → Quelle 240, SOC 62 → eine offene Zeile, `soc_start == 62`.
  - `test_return_closes_outage` → danach Quelle 1 (Netz) → `end` = Zeit der Beobachtung, `soc_end`, `end_estimated is False`.
  - `test_unknown_source_changes_nothing` → `value(...)` ist `None` (getrennt oder veraltet) mitten im Ausfall → Zeile bleibt offen, keine neue.
  - `test_ac_out_energy_and_min_soc` → 3 Polls à 10 s mit L1/L2/L3 = 500/400/300 W, SOC 62/61/60 → `load_wh == pytest.approx(1200*20/3600)`, `soc_min == 60`; ein Abstand über 30 s zählt höchstens 30 s; fehlt eine Phase, zählt der Abschnitt nicht (`covered_s` wächst nicht).
  - `test_restart_during_outage_continues` → offene Zeile in der DB, neuer Tracker, Quelle 240 → dieselbe `id`, keine zweite Zeile.
  - `test_restart_after_return_closes_estimated` → offene Zeile, neuer Tracker, Quelle 1 → `end` = erste Beobachtung, `end_estimated is True`.
  - `test_recent_newest_first` → 25 Ausfälle → `recent()` liefert 20, neuester zuerst.
  - In `test_db.py`: `test_migration_0017_creates_grid_outage` (auf und ab, Tabelle danach da bzw. weg); `test_migrations_match_schema` bleibt grün.
- [ ] **Step 2:** Tests → FAIL.
- [ ] **Step 3:** Umsetzen. Speichern: Beginn und Ende sofort, laufende Werte höchstens alle 60 s (`load_wh`, `covered_s`, `soc_min`). DB-Zugriffe über `asyncio.to_thread` wie in den anderen Diensten.
- [ ] **Step 4:** Tests grün, Migration in `tests/unit/app/test_db.py` grün.
- [ ] **Step 5:** Commit `feat(app): Protokoll der Netzausfälle`

---

### Task 4: Lebenszeichen an den externen Dienst

**Files:**
- Create: `src/bindaems/app/monitoring/heartbeat.py`
- Test: `tests/unit/app/monitoring/test_heartbeat.py`

**Interfaces:**
- Consumes: `LiveState` (`connected`, `health["mode"]`, `health["mode_reasons"]`, `value("battery.soc_pct")`), `OutageTracker.current()`, `BackupRange`/`hours_text` (Task 2), `Secrets.ping_url`, `AppConfig.ping_interval_s`, `ComponentStatus`
- Produces:
  - `@dataclass(frozen=True) class Verdict: ok: bool; text: str`
  - `def verdict(*, connected: bool, down_since: datetime | None, now: datetime, mode: str | None, reasons: Sequence[str], outage: Outage | None, soc_pct: float | None, range_now: float | None) -> Verdict`
  - `class Heartbeat(url: SecretStr | None, http: httpx.AsyncClient, clock: Clock, *, interval_s: float, inputs: Callable[[], Verdict])`: `async run()`, `async send_once() -> bool`, `component_status() -> ComponentStatus` (Name „Lebenszeichen“).

- [ ] **Step 1: Failing tests**
  - `test_ok_text` → verbunden, CONTROL, SOC 55, 31.2 h → `Verdict(True, "in Ordnung · CONTROL · SOC 55 % · USV ≈ 31 h")`.
  - `test_core_down_grace` → getrennt seit 119 s → ok; seit 120 s → `ok is False`, Text beginnt „core nicht erreichbar seit 14:03“ (lokale Zeit).
  - `test_outage_fails` → offener Ausfall seit 14:03, SOC 62, 18 h → `Verdict(False, "Netzausfall seit 14:03 · SOC 62 % · reicht ≈ 18 h ohne PV")`.
  - `test_safe_fails` → `mode="SAFE"`, Gründe `["Cerbo-Daten veraltet."]` → `ok is False`, Text „Sicherer Zustand: Cerbo-Daten veraltet.“.
  - `test_observe_is_ok` → `mode="OBSERVE"` → `ok is True`.
  - `test_posts_to_url_and_fail` (`httpx.MockTransport`) → ok → `POST https://hc-ping.com/abc` mit Body = Text; Störung → `POST https://hc-ping.com/abc/fail`; Timeout 10 s.
  - `test_send_failure_is_contained` → Transport wirft `httpx.ConnectError` dreimal → `send_once()` liefert `False`, kein Fehler nach außen, `component_status().ok is False`, Meldung „nicht gesendet seit 14:05“; eine Logzeile beim Wechsel, keine beim zweiten Fehlschlag; nach Erfolg `ok is True`.
  - `test_url_never_logged` → mit `structlog.testing.capture_logs()` und einem `ConnectError`, dessen Text die URL enthält → kein Logeintrag und kein `component_status().message`/`details` enthält `abc`.
  - `test_without_url_is_off` → `url=None` → `run()` endet nicht mit Fehler und sendet nichts; `component_status()` = ok, „aus (keine Ping-URL)“.
- [ ] **Step 2:** Tests → FAIL.
- [ ] **Step 3:** Umsetzen. `down_since` merkt sich der Heartbeat selbst (erster Poll mit `connected False`). Ohne URL schläft `run()` dauerhaft (kein Neustart-Kreislauf in `_supervise`). Beim Senden nur `type(exc).__name__` loggen, nie `str(exc)`. Erster Ping 30 s nach dem Start.
- [ ] **Step 4:** Tests grün.
- [ ] **Step 5:** Commit `feat(app): Lebenszeichen an einen externen Dienst`

---

### Task 5: Einbinden in die app und API `/api/backup`

**Files:**
- Modify: `src/bindaems/app/runtime.py` (Dienste bauen, Jobs `backup-range`, `outages`, `heartbeat`, Statusquelle)
- Create: `src/bindaems/app/monitoring/routes.py`
- Test: `tests/unit/app/monitoring/test_routes.py`, `tests/integration/test_app_runtime.py` (Jobs vorhanden)

**Interfaces:**
- Consumes: Tasks 2–4; `guard.require("viewer")` wie in `forecast/routes.py`; aktuelle Einstellungen für `planning.roundtrip_pct`.
- Produces:
  - `AppRuntime.backup_range() -> BackupRange`: eine Stelle für API, HA und Heartbeat (SOC aus `live`, Limits aus `config.yaml`, `roundtrip_pct` aus den aktuellen Einstellungen, Ausfall-Last aus `OutageTracker.current().mean_w`)
  - `GET /api/backup` (Viewer) →
    ```json
    {"now_h": 31.2, "reserve_h": 14.1, "night_h": 40.3, "load_w": 712.0, "load_source": "tag",
     "floor_soc_pct": 5.0, "basis": {"first_day": "2026-10-28", "last_day": "2026-11-03", "day_slots": 672, "night_slots": 140, "night_w": 551.0},
     "outage": null | {"start": "...", "soc_start": 62.0, "mean_w": 1180.0},
     "outages": [{"start": "...", "end": "...", "end_estimated": false, "duration_s": 840, "soc_start": 62.0, "soc_end": 60.5, "soc_min": 60.5, "mean_w": 1180.0, "load_kwh": 0.28}],
     "ping": {"configured": true, "ok": true, "last_sent": "...", "text": "in Ordnung · …"}}
    ```

- [ ] **Step 1: Failing tests**
  - `test_backup_requires_login` → ohne Sitzung 401.
  - `test_backup_payload` → Felder und Typen wie oben; ohne Lastannahme `now_h`, `reserve_h`, `load_w` = `null`.
  - `test_backup_has_no_url` → Antworttext enthält die Ping-URL nicht.
  - `test_runtime_jobs` (Integration) → Jobs `backup-range`, `outages`, `heartbeat` laufen unter `_supervise`; `/api/system` nennt die Komponente „Lebenszeichen“.
- [ ] **Step 2:** Tests → FAIL.
- [ ] **Step 3:** Umsetzen; `BackupRangeService` bekommt den vorhandenen `AppRuntime.reader` (InfluxDB). Heartbeat bekommt `inputs` als Closure über `live`, `outages.current()` und `backup_range()`. Tests mit `CoreRuntime`-Daten nur unter `tmp_path` (Lehre 19.10.).
- [ ] **Step 4:** Tests grün.
- [ ] **Step 5:** Commit `feat(app): USV-Reichweite und Netzausfälle in der API`

---

### Task 6: Home Assistant

**Files:**
- Modify: `src/bindaems/app/ha/entities.py` (`HaInputs`, `build_entities`, `build_inputs`)
- Modify: Aufrufer von `build_inputs` in `src/bindaems/app/runtime.py`
- Test: `tests/unit/app/ha/test_entities.py`

**Interfaces:**
- Consumes: `AppRuntime.backup_range()`, `OutageTracker.current()`
- Produces: `HaInputs.backup: BackupRange | None = None`, `HaInputs.outage_since: datetime | None = None`; Entitäten
  - `usv_reichweite` sensor „USV-Reichweite“, unit `h`, `device_class="duration"`, `state_class="measurement"`, Wert mit 1 Nachkommastelle, Attribute `reserve_h`, `night_h`, `load_w`, `load_source`, `floor_soc_pct`, `ohne_pv: true`
  - `usv_reichweite_reserve` sensor „USV-Reichweite ab Reserve“, wie oben ohne Attribute
  - `netzausfall` binary_sensor „Netzausfall“, `device_class="problem"`, Attribut `seit`

- [ ] **Step 1: Failing tests** → Discovery enthält die drei Entitäten mit Einheit und Klasse; `backup=None` oder `now_h=None` → Zustand `UNKNOWN` („None“); offener Ausfall → `netzausfall` „ON“ mit `seit`; kein Ausfall → „OFF“.
- [ ] **Step 2:** Tests → FAIL.
- [ ] **Step 3:** Umsetzen, Muster wie `battery_soc`.
- [ ] **Step 4:** Tests grün, `tests/integration/test_ha_round_trip.py` grün.
- [ ] **Step 5:** Commit `feat(app): USV-Reichweite und Netzausfall in Home Assistant`

---

### Task 7: UI – Akku-Kachel und Karte auf der Seite System

**Files:**
- Modify: `ui/src/lib/api/schemas.ts` (`BackupSchema`), `ui/src/lib/api/endpoints.ts` (`backup`), `ui/src/lib/api/contract.test.ts`
- Modify: `ui/src/lib/dashboard/battery.ts`, `ui/src/lib/dashboard/BatteryTile.svelte`, `ui/src/routes/+page.svelte`
- Create: `ui/src/lib/system/BackupCard.svelte`, `ui/src/lib/system/backup.ts`
- Modify: `ui/src/routes/system/+page.svelte`
- Test: `ui/src/lib/dashboard/battery.test.ts`, `ui/src/lib/dashboard/BatteryTile.test.ts`, `ui/src/lib/system/BackupCard.test.ts`, `ui/src/lib/system/backup.test.ts`, `ui/tests/e2e/pages.spec.ts`

**Interfaces:**
- Consumes: `GET /api/backup` (Task 5)
- Produces:
  - `export function hoursText(h: number | null): string | null` in `backup.ts` (gleiche Regeln wie `hours_text`, Entscheidung 10; `null` → `null`)
  - `export function backupLine(b: Backup | null | undefined, nowMs: number): { text: string; outage: boolean } | null` in `battery.ts`: „Netzausfall: ≈ 31 h, ab Reserve ≈ 14 h“; bei offenem Ausfall „Netzausfall seit 14:03 · reicht noch ≈ 18 h“ (`outage: true`); ohne `now_h` und ohne Ausfall `null`.
  - `BackupCard` Props `{ data: Backup | undefined; error: ApiError | null }`

- [ ] **Step 1: Failing tests**
  - `backup.test.ts`: 7.46 → „≈ 7,5 h“, 31.4 → „≈ 31 h“ (mit NBSP), `null` → `null`.
  - `battery.test.ts` (`describe('backupLine')`): normal, nur `now_h` ohne `reserve_h` („Netzausfall: ≈ 31 h“), offener Ausfall, `null`-Daten.
  - `BatteryTile.test.ts`: Zeile sichtbar unter der Planzeile; im Ausfall mit Klasse `outage`; ohne Daten keine Zeile.
  - `BackupCard.test.ts`: zeigt Lastannahme „≈ 712 W (Mittel 28.10.–3.11.)“, „nachts ≈ 551 W“, Untergrenze 5 %, Lebenszeichen „eingerichtet, zuletzt 14:05: in Ordnung“ bzw. „nicht eingerichtet“, Tabelle der Ausfälle (Beginn, Dauer „14 min“, SOC „62 → 60,5 % (min 60,5 %)“, Last „≈ 1,18 kW“, „Ende geschätzt“ als Zusatz); leere Liste „Noch kein Netzausfall erfasst.“
  - `contract.test.ts`: `backup` gegen `BackupSchema`.
  - E2E `pages.spec.ts`: Übersicht bei 360 px – Zeile in der Akku-Kachel ohne horizontales Scrollen; Seite System zeigt „USV und Netzausfälle“.
- [ ] **Step 2:** `cd ui && pnpm vitest run src/lib/dashboard src/lib/system` → FAIL.
- [ ] **Step 3:** Umsetzen; `+page.svelte` (Übersicht) und System holen `api.backup` alle 60 s. Die Kachel zeigt die Zeile als eigene `<p>` unter der Planzeile (nicht in `.side`, damit 360 px reichen).
- [ ] **Step 4:** `pnpm check`, `pnpm lint`, `pnpm test`, E2E (`pnpm exec playwright test`, lokal mit `launchOptions.executablePath` `/opt/pw-browsers/chromium`).
- [ ] **Step 5:** Commit `feat(ui): USV-Reichweite in der Akku-Kachel und Netzausfälle auf der Seite System`

---

### Task 8: Doku, Anleitung und Abschluss

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Nachtrag „Stand Plan Lebenszeichen und USV-Reichweite“ bei Akku-Kachel und bei HA-Entitäten; 1.4 bleibt: das Lebenszeichen fährt nichts herunter)
- Modify: `docs/betrieb.md` (Abschnitt „Lebenszeichen einrichten“: Konto bei healthchecks.io, Check „BindaEMS“ mit Periode 5 min und Karenz 10 min, Benachrichtigung aufs Handy, URL in `.env` als `BINDAEMS_PING_URL`, `docker compose up -d ems-app` nach dem Sammel-Pull, Test: app stoppen → Meldung nach ≤ 15 min; `battery.outage_floor_soc_pct` prüfen; vor dem Ausrollen auf der Seite System in der Signaltabelle nachsehen, dass `consumption_output.l1..l3.power_w` Werte haben, sonst bleibt die Reichweite „zu wenig Daten“)
- Modify: `docs/uebergabe.md` (Stand, „Offen an der Anlage“: mit dem Sammel-Pull, nicht vor dem 23.11.)
- Test: `tests/unit/test_docs.py` (bestehend)

- [ ] **Step 1:** Doku schreiben; `pytest tests/unit/test_docs.py` grün.
- [ ] **Step 2:** Ganze Prüfkette (Python und UI), Abdeckung ≥ 75 % gesamt.
- [ ] **Step 3:** Commit `docs: Lebenszeichen und USV-Reichweite`
- [ ] **Step 4:** Gesamtreview über den Branch, Fix-Welle für Critical/Important (je Fix ein vorher scheiternder Test), Minors gesammelt; Abschlussbericht mit Ergebnis, Tests, Rulings, Deferred minors und der Frage nach der Integration.

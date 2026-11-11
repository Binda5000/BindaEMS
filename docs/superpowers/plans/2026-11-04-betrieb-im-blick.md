# Betrieb im Blick – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eine neue Seite „Betrieb“ zeigt auf einen Blick, ob die Anlage in Ordnung ist (Betriebscheck), welche Auffälligkeiten die app in den letzten Tagen selbst gefunden hat (mit Tagesbericht in Home Assistant) und wie alle Schalter stehen, seit wann und wer sie gestellt hat.

**Architecture:** Nur app und UI, der core bleibt unverändert. Ein neues Paket `app/ops/` mit drei reinen Bausteinen (`check.py`, `findings.py`, `switches.py`) und dem Text des Tagesberichts (`report.py`); dazu `service.py` (Daten sammeln, nachts rechnen, ablegen, Bericht senden) und `routes.py`. Der Betriebscheck liest Health, die Bausteine der app und die Entscheidungen der letzten Stunde. Die Auffälligkeiten rechnen nachts aus Abrechnung, Plänen, Entscheidungen, geplanten Ladungen, Zählerabgleich und dem Akku-SOC aus InfluxDB und liegen in einer neuen Tabelle (Migration `0016`). Die Schalter kommen aus Einstellungen, Absicht und Health, „seit wann und wer“ aus dem Änderungsprotokoll.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy Core, Alembic, pytest; SvelteKit 2 (Svelte 5), valibot, Vitest, Playwright. Keine neuen Abhängigkeiten.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Abschnitte 7.10 (Entscheidungslog), 11.4 (Abrechnung), 12 (Web-UI), 13 (Home Assistant), 14 (Änderungsprotokoll); Ideen 1.1, 2.4 und 15.2 aus `/mnt/project-files/ideen/brainstorming-2026-11.md` sowie D („HA-Tagesbericht“) aus dem Doc „BindaEMS: Was offen ist und Vorschläge“.

## Einordnung

- **Anlass (Betreiber, 4.11.2026):** Bündel „Betrieb im Blick“ aus der Top 10 des Brainstormings: Rang 1 (1.1 Betriebscheck im UI), Rang 3 (2.4 Auffälligkeiten automatisch finden, mit D Tagesbericht) und Rang 9 (15.2 Schalter-Übersicht).
- **Heute:** Ob alles läuft, prüfst du mit rund zehn `core … | jq`-Befehlen aus B2 und B6 der Anlagenliste. Den Netzbezug von rund 9 kW am 3.11. um 22:35 (Victron lud den Akku unter der Reserve selbst nach) hast du selbst im Diagramm entdeckt. Die Schalter liegen auf vier Seiten (System, Zeitplan, Laden, Einstellungen) und in Home Assistant; wann und von wem einer gestellt wurde, steht nur im Protokoll.
- **Was sich für dich ändert:** In der Navigation steht nach „System“ die Seite **Betrieb** mit drei Karten:
  1. **Alles in Ordnung?** Grün, gelb oder rot, darunter jede Prüfung mit einem Satz Begründung und, wo es passt, einem Link (etwa zu den Entscheidungen der letzten Stunde).
  2. **Auffälligkeiten** der letzten 14 Tage, neueste zuerst, je Treffer Zeit, Satz und Link zu den Entscheidungen dieses Tages; oben der Tagesbericht von gestern.
  3. **Schalter:** jeder Schalter mit Stand, „ab Werk“, seit wann, wer (Benutzer, HA oder System) und einem Link zur Seite, auf der du ihn stellst. Abweichungen von „ab Werk“ sind markiert.
  Auf der Seite **System** steht ganz oben eine Zeile „Betriebscheck: alles in Ordnung“ (bzw. die Zahl der Befunde) mit Link. In Home Assistant gibt es den Sensor „Betriebscheck“ (`ok`, `warnung`, `fehler`) und jeden Morgen den **Tagesbericht** als Benachrichtigung.
- **Ausrollen:** mit Stufe 2 des Sammel-Pulls (ab 23.11.2026, `anlage/sammel-pull.md`), nicht vorher: Bis 22.11. laufen die Abnahmen, an der Anlage ändert sich nichts. Die app bringt Migration `0016`; der core ist unverändert. **Rückweg:** Vor dem Pull die Datenbank sichern wie im Sammel-Pull (`cp /data/bindaems.sqlite3 /backup/vor-betrieb-im-blick.sqlite3`); zurück geht es mit dieser Sicherung oder `alembic downgrade` auf `0015`. Eine ältere app kennt den Einstellungs-Schlüssel `ops` nicht und fiele beim Lesen auf die Standardwerte zurück (Tarif!); deshalb vor dem Rückweg die Einstellungen als YAML exportieren, `ops` löschen und nach dem Rückweg importieren (Betriebshandbuch, wie beim Standard-Lademodus).
- **Parallel:** `main` ist `8ecbfe9`. Der Branch „Netzgrenze je Phase“ (`claude/project-thread-ambh18`, freigegeben, noch nicht auf `main`) ändert `shared/settings.py`, `ui/src/lib/api/schemas.ts` und `ui/src/lib/settings/model.ts`; wer zuerst nach `main` kommt, der andere zieht nach (keine Migration auf dem anderen Branch). Kommt er zuerst, nimmt Task 6 dessen Schalter „Nachladen auf die Reserve“ nicht auf, weil er keiner ist (immer an), und den gedachten Satz der Monatsspitze (`peak_soft_ct_per_kw`, Zahl, 0 = aus) als Schalter „Monatsspitze meiden“ (an, solange > 0).

## Entscheidungen

Vorschläge; 5 und 6 hast du am 4.11.2026 entschieden, der Rest gilt, wenn du nichts dagegen sagst.

1. **Eigene Seite „Betrieb“** statt einer weiteren Karte auf „System“: System ist schon lang (Signale, Prognosen, Bedienung), und die Schalter-Übersicht braucht ohnehin eine eigene Seite. System verweist oben mit einer Zeile darauf. Sichtbar für alle Rollen (viewer); stellen kannst du die Schalter weiter nur dort, wo sie heute sind (die Übersicht verlinkt).
2. **Betriebscheck aus vorhandenen Daten, ohne Schwellen in den Einstellungen.** Die Schwellen sind Konstanten aus B2/B6 (Tabelle in Task 1). Ein Aktor, den du bewusst im Dry-Run hast (heute die Klimaanlage), ist grün mit dem Satz „Dry-Run (so gewünscht)“; gelb oder rot wird nur, was anders läuft als gewünscht.
3. **Auffälligkeiten nachts für den Vortag** (03:05 Uhr, nach dem Zählerabgleich um 02:45 und vor dem Training um 03:15), beim Start für die letzten 7 Tage, die noch fehlen. Kein Live-Alarm: Was sofort zählt, melden schon die Alarme.
4. **Sieben Regeln** (Task 3): Akku lädt aus dem Netz ohne Plan (mit dem Hinweis „vermutlich Victron-Nachladen“, wenn der SOC dabei unter der Reserve war), Akku unter der Reserve, Monatsspitze gestiegen, Netzbezug weicht vom Plan ab, Auto hat Ziel verfehlt, Befehle gescheitert, Zählerabgleich markiert. Nicht dabei: eine feste kW-Schwelle je Viertelstunde (ein Tesla mit 11 kW wäre jeden Abend ein Treffer; der finanziell wichtige Fall ist die neue Monatsspitze, und der 9-kW-Fall vom 3.11. fällt unter „ohne Plan“).
5. **Tagesbericht (entschieden vom Betreiber am 4.11.2026: „Täglich um 07:30“):** jeden Morgen um 07:30 Uhr, auch an ruhigen Tagen (dann ein Satz „Keine Auffälligkeiten“), als persistente Meldung in HA und über den eingestellten `notify`-Dienst aufs Handy. Umstellbar unter Einstellungen → „Tagesbericht“ auf „nur bei Auffälligkeiten“ oder „aus“, Uhrzeit wählbar. Ohne Home Assistant steht der Bericht nur auf der Seite Betrieb.
6. **Ersparnis im Tagesbericht (entschieden vom Betreiber am 4.11.2026: „Echte Rechnung später“):** Kosten und Ersparnis von gestern wie in der Statistik (gegen „ohne PV und Akku“). Eine gemessene Ersparnis gegen „Victron allein“ gibt es in der app nicht (das rechnet `scenario_days` für die Abnahme am 17.11.); sie kommt als eigener kleiner Plan nach der Abnahme am 17.11.
7. **Schalter-Übersicht nur lesend**, Katalog in Task 6. „Seit wann und wer“ ist der jüngste passende Eintrag im Änderungsprotokoll; ohne Eintrag steht „seit Einrichtung“. Nicht in der Liste: Lademodus, Ziel und Abfahrt je Auto (das sind Bedienungen, keine Schalter), Werte aus `config.yaml` außer `live_allowed` (das steht beim Aktor als „in config.yaml gesperrt“).
8. **Aufbewahrung:** Auffälligkeiten 400 Tage (für den Jahresrückblick), Bericht-Tage ebenso; Löschen in der bestehenden Wartung.

## Global Constraints

- Texte für den Betreiber deutsch, Anrede „du“; Kommentare und Docstrings deutsch im Stil der Umgebung. Zahlen deutsch formatiert (Komma, geschütztes Leerzeichen vor Einheit wie in `ui/src/lib/format.ts`; in Python `format_number` aus dem bestehenden Text der Benachrichtigungen bzw. `f"{x:.1f}".replace(".", ",")`).
- Paket `bindaems.app.ops`; Routen unter `/api/ops/…`; UI-Route `/betrieb`, Komponenten unter `ui/src/lib/ops/`.
- Stufen `ok`, `warn`, `error`, `unknown` (Betriebscheck) und `info`, `warn` (Auffälligkeiten); die UI zeigt sie mit dem bestehenden `StatusDot`.
- Der core bleibt unverändert (keine Datei unter `src/bindaems/core`, keine Änderung an Health). Kein Aktor wird live geschaltet; nichts an der Anlage.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy --strict` (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators), UI `pnpm check`, `pnpm lint`, `pnpm test`. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Commits deutsch im Stil `feat(app): …`, `feat(ui): …`, `docs: …`. Keine Modellbezeichnungen im Repo.

## Review Focus

- **core weg oder Health alt (`live.health is None`, `live.connected` false):** Der Betriebscheck zeigt genau einen roten Punkt „core nicht erreichbar“ und für die übrigen Prüfungen „unbekannt“, keine Ausnahme, kein Grün → Test in Task 1 (`test_core_down_is_one_error_rest_unknown`).
- **Ein älterer core ohne einzelne Health-Felder** (`plan`, `battery`, `vehicles[].commands_hour`): die Prüfung ist „unbekannt“, Health bleibt gültig → Test in Task 1 (`test_missing_fields_are_unknown`).
- **Lücken in den Daten des Vortags** (app war aus, InfluxDB nicht erreichbar, Slot ohne Plan): Regeln, denen Daten fehlen, laufen nicht und erzeugen einen `info`-Treffer „nicht geprüft: …“ statt eines falschen Treffers; ein Slot ohne Plan zählt für „ohne Plan“ nur, wenn der Tag sonst Pläne hat → Tests in Task 3 und Task 4.
- **Zeitumstellung (25-h- und 23-h-Tag):** Tagesgrenzen über `local_day_slots`, der Nachtlauf über `next_local`; der Bericht kommt genau einmal je Tag, auch nach einem Neustart → Tests in Task 3 (`test_rules_on_dst_day_cover_100_slots`) und Task 5 (`test_report_sent_once_per_day_across_restart`).
- **Viele Treffer an einem Tag** (Victron lädt eine Stunde in Schüben nach): aufeinanderfolgende Slots derselben Regel werden zu einem Treffer zusammengefasst, der Bericht nennt höchstens drei und „und N weitere“, HA bekommt höchstens eine Nachricht je Tag → Tests in Task 3 und Task 5.

---

### Task 1: Betriebscheck (rein)

**Files:**
- Create: `src/bindaems/app/ops/__init__.py`, `src/bindaems/app/ops/check.py`
- Test: `tests/unit/app/ops/test_check.py`

**Interfaces:**
- Produces:
  - `Level = Literal["ok", "warn", "error", "unknown"]`
  - `@dataclass(frozen=True) class CheckItem: id: str; label: str; level: Level; text: str; link: str | None = None`
  - `@dataclass(frozen=True) class OpsCheck: level: Level; items: tuple[CheckItem, ...]; checked_at: datetime` (Gesamtstufe = schlechteste über `error > warn > unknown > ok`; nur `unknown` und `ok` → `unknown`, wenn der core weg ist, sonst `ok`)
  - `@dataclass(frozen=True) class DecisionRates: per_actuator_hour: Mapping[str, int]` (Entscheidungen der letzten 60 min je Aktor, ohne Dry-Run nicht gefiltert)
  - `def operations_check(*, connected: bool, health: Mapping[str, Any] | None, components: Sequence[ComponentStatus], rates: DecisionRates | None, now: datetime) -> OpsCheck`
  - `def check_json(check: OpsCheck) -> dict[str, Any]`

Prüfungen in dieser Reihenfolge (IDs fest, Texte Vorschlag):

| id | Quelle | ok | warn | error |
|---|---|---|---|---|
| `core` | `connected` | „core verbunden“ | – | „core nicht erreichbar“ (dann alle weiteren core-Prüfungen `unknown`, Text „unbekannt, core nicht erreichbar“) |
| `mode` | `mode`, `mode_reasons`, `intent.control` | `CONTROL` | `OBSERVE` mit „Regelbetrieb ist aus“; `DEGRADED` mit den Gründen | `SAFE` mit den Gründen; `START` → `unknown` |
| `actuator.<id>` je Aktor | `actuators[]` | `effective == requested`; Text „live“ bzw. „Dry-Run (so gewünscht)“ | – | `requested == "live"` und `effective != "live"` (Gründe aus `reasons`); `locked` nicht leer (Gründe) |
| `watchdog` | `watchdog` | `required` false („nicht nötig“) oder `armed` und `state == "armed"` | `version != expected_version` | `required` und nicht scharf; `state == "tripped"` |
| `alarms` | `alarms` | keine | Schwere `warning` (Anzahl und erste Meldung) | Schwere `error` |
| `assignment` | `wallboxes[]` | keine `unclear` | `assignment == "unclear"` (Name der Wallbox), Link `/laden` | – |
| `tesla_commands` | `vehicles[].commands_hour` | ≤ 16 je Fahrzeug | > 16 („Reserve greift“) | – |
| `evcs_rate` | `rates["wallbox.evcs"]` | ≤ 20 je Stunde | > 20 („Hysterese prüfen“), Link `/entscheidungen?range=hour&actuator=wallbox.evcs` | – |
| `victron_rate` | `rates["victron"]` | ≤ 60 je Stunde | > 60 („Totband prüfen“), Link analog | – |
| `plan` | `plan.state` | `valid` | `expired`, `none` | – |
| `battery` | `battery.state` | `ok`, `idle` | `unknown` | `locked` |
| `inputs` | `inputs` | `ok` | Probleme (erste zwei) | – |
| `adapters` | `adapters[]` | alle `connected` | getrennte Adapter (Namen) | – |
| `selfcheck` | `selfcheck[]` | alle `ok` | `warn` | `fail` |
| `app` | `components` | alle `ok` | Bausteine mit `ok` false (Namen) | – |

Fehlt ein Feld in Health (älterer core), ist die Prüfung `unknown` mit „vom core nicht gemeldet“. Konstanten `TESLA_COMMANDS_WARN = 16`, `EVCS_PER_HOUR_WARN = 20`, `VICTRON_PER_HOUR_WARN = 60` mit Kommentar auf B2/B6.

- [ ] **Step 1: Tests schreiben** – `test_all_good_is_ok` (Health wie an der Anlage nach Victron live: CONTROL, EVCS/Tesla/Victron live, Klima Dry-Run gewünscht, Watchdog scharf → Gesamt `ok`, Klima-Zeile „Dry-Run (so gewünscht)“), `test_core_down_is_one_error_rest_unknown`, `test_missing_fields_are_unknown`, `test_live_requested_but_dry_run_is_error_with_reason` (EVCS `requested=live`, `effective=dry_run`, Grund „Watchdog am Cerbo ist nicht scharf.“ → `error`, Text enthält den Grund), `test_locked_actuator_is_error`, `test_watchdog_needed_but_not_armed_is_error`, `test_watchdog_not_needed_is_ok`, `test_observe_without_control_is_warn`, `test_error_alarm_beats_warning`, `test_unclear_assignment_is_warn_with_link`, `test_tesla_commands_over_16_is_warn`, `test_evcs_rate_over_20_is_warn` (21 → `warn`, 20 → `ok`), `test_victron_rate_over_60_is_warn`, `test_rates_unknown_without_store` (`rates=None` → beide `unknown`), `test_expired_plan_is_warn`, `test_component_not_ok_is_warn`, `test_overall_level_is_worst`.
- [ ] **Step 2: Tests laufen lassen, sie scheitern** – `uv run pytest tests/unit/app/ops/test_check.py -v` → FAIL (Modul fehlt).
- [ ] **Step 3: `check.py` umsetzen** – je Prüfung eine kleine Funktion `_mode(health) -> CheckItem` usw.; Modul-Docstring mit der Tabelle in Kurzform.
- [ ] **Step 4: Tests grün** – derselbe Befehl → PASS.
- [ ] **Step 5: Commit** – `feat(app): Betriebscheck aus Health, Bausteinen und Entscheidungsraten`

### Task 2: Route des Betriebschecks und Zählen der Entscheidungen

**Files:**
- Modify: `src/bindaems/app/control/decisions.py` (`DecisionStore.counts`, `DecisionStore.failures`)
- Create: `src/bindaems/app/ops/routes.py`
- Modify: `src/bindaems/app/runtime.py` (Router einhängen, Quellen der Bausteine übergeben wie bei `live_router`)
- Test: `tests/unit/app/control/test_decisions.py`, `tests/unit/app/ops/test_routes.py`

**Interfaces:**
- Consumes: `operations_check`, `check_json`, `DecisionRates` (Task 1); `LiveState`, `StatusSource`, `Guard`.
- Produces:
  - `DecisionStore.counts(since: datetime, until: datetime | None = None) -> dict[str, int]` (Anzahl je Aktor, Dry-Run eingeschlossen; eine `GROUP BY`-Abfrage)
  - `DecisionStore.failures(since: datetime, until: datetime) -> dict[str, int]` (je Aktor die Entscheidungen mit `dry_run` false und `result` in `error`, `timeout`, `not_sent`) – für Task 4
  - `def ops_router(*, live: LiveState, guard: Guard, sources: Sequence[StatusSource], decisions: DecisionStore, findings: "FindingsService", switches: "SwitchesSource", clock: Clock) -> APIRouter` mit Präfix `/api/ops`; in diesem Task nur `GET /api/ops/check` (viewer) → `check_json(...)`. Task 4 und 6 ergänzen ihre Routen im selben Router (die Parameter `findings` und `switches` kommen erst dort dazu).

- [ ] **Step 1: Tests schreiben** – `test_counts_per_actuator_since` (drei EVCS, eine Victron, eine vor `since` → `{"wallbox.evcs": 3, "victron": 1}`), `test_failures_ignore_dry_run_and_ok`, `test_check_route_requires_login` (401 ohne Sitzung), `test_check_route_returns_level_and_items` (Fake-`LiveState` mit Health aus `tests/ui_world.py` → 200, `level`, `items[].id` enthält `core`, `mode`, `watchdog`), `test_check_route_without_core` (`connected` false → `level == "error"`).
- [ ] **Step 2: Tests laufen lassen, sie scheitern** – `uv run pytest tests/unit/app/control/test_decisions.py tests/unit/app/ops/test_routes.py -v` → FAIL.
- [ ] **Step 3: Umsetzen** – `counts` und `failures` mit `select(actuator, func.count()).group_by(actuator)`; Route liest `decisions.counts(now - 1 h)` in einem Arbeitsthread (`run_in_threadpool`), bei einer Datenbankausnahme `rates=None`.
- [ ] **Step 4: Verdrahten** – in `runtime.py` neben `live_router`; `/api/ops` in der Liste der Routen, die das bestehende Routen-Inventar prüft (falls ein Test sie aufzählt, dort ergänzen).
- [ ] **Step 5: Tests grün** – `uv run pytest tests/unit/app tests/integration -q` → PASS.
- [ ] **Step 6: Commit** – `feat(app): Route für den Betriebscheck und Entscheidungen je Aktor zählen`

### Task 3: Regeln der Auffälligkeiten (rein)

**Files:**
- Create: `src/bindaems/app/ops/findings.py`
- Test: `tests/unit/app/ops/test_findings.py`

**Interfaces:**
- Consumes: `LedgerSlot` (`app/ledger/service.py`), `in_force` (`app/planning/review.py`), `Plan`, `PlanSlot` (`shared/plan.py`), `local_day_slots`, `SLOT`.
- Produces:
  - `Rule = Literal["grid_charge_unplanned", "below_reserve", "month_peak", "plan_deviation", "target_missed", "command_failures", "counter_check", "not_checked"]`
  - `@dataclass(frozen=True) class Finding: day: date; rule: Rule; level: Literal["info", "warn"]; start: datetime | None; end: datetime | None; text: str; link: str | None; data: Mapping[str, Any]`
  - `@dataclass(frozen=True) class MissedTarget: vehicle_name: str; target_soc_pct: float; end_soc_pct: float | None; departure_at: datetime`
  - `@dataclass(frozen=True) class DayInputs: day: date; slots: Sequence[LedgerSlot]; plans: Sequence[Plan]; soc_min: Mapping[datetime, float] | None; reserve_soc_pct: float; previous_peak_kw: float | None; failures: Mapping[str, int]; actuator_labels: Mapping[str, str]; missed: Sequence[MissedTarget]; flagged_checks: Sequence[tuple[str, float | None]]` (`soc_min` je Slotbeginn der kleinste SOC, `None` = InfluxDB nicht erreichbar; `flagged_checks` = Signal und Abweichung in %)
  - `def find(inputs: DayInputs) -> list[Finding]` (nach `start`, Tagesregeln ohne Zeit am Ende)
  - Konstanten: `GRID_CHARGE_MIN_WH = 250.0` (1 kW im Mittel einer Viertelstunde), `RESERVE_MARGIN_PCT = 2.0`, `VICTRON_RECHARGE_HINT_PCT = 3.0`, `PEAK_RISE_MIN_KW = 0.5`, `DEVIATION_MIN_KWH = 3.0`, `DEVIATION_MIN_SHARE = 0.25`, `MAX_GAP_SLOTS = 1` (Slots derselben Regel mit höchstens einem Slot dazwischen werden ein Treffer)

Regeln (Texte Vorschlag, Zeiten lokal `HH:MM`):
- **`grid_charge_unplanned` (warn):** Slot mit `flows_wh["grid>battery"] ≥ GRID_CHARGE_MIN_WH`, für den der damals gültige Plan `charge_grid_w == 0` hatte oder kein Plan galt (letzteres nur, wenn der Tag sonst mindestens einen Plan hat). Zusammengefasst über Folgen. Text: „Akku lud 22:30–23:00 mit bis zu 9,1 kW aus dem Netz (2,3 kWh), der Plan sah kein Netzladen vor.“ War `soc_min` in der Folge ≤ `reserve − VICTRON_RECHARGE_HINT_PCT`: Zusatz „Vermutlich lud Victron selbst nach (SOC 14 %, Reserve 20 %).“ Link `/entscheidungen?range=<Tag>&actuator=victron`. `data`: `kwh`, `max_kw`, `soc_min`.
- **`below_reserve` (warn):** Folge von Slots mit `soc_min < reserve − RESERVE_MARGIN_PCT`. Text: „Akku war 22:15–22:45 unter der Reserve (tiefster Stand 14 %, Reserve 20 %).“ Ohne `soc_min` → ein `not_checked`-Treffer „Akku-SOC nicht geprüft: InfluxDB nicht erreichbar.“
- **`month_peak` (warn):** höchster Slot des Tages (`import_wh · 4 / 1000`) liegt um ≥ `PEAK_RISE_MIN_KW` über `previous_peak_kw` (`None` am Monatsersten oder ohne frühere Slots → keine Regel). Text mit der Aufteilung des Bezugs nach Ziel aus `flows_wh` (`grid>house`, `grid>battery`, `grid>wallbox.*`): „Neue Monatsspitze 9,1 kW um 22:30 (vorher 6,4 kW): Akku 8,2 kW, Haus 0,9 kW.“
- **`plan_deviation` (info):** über Slots mit Plan und Abrechnung: `|Ist − Plan|` des Netzbezugs ≥ `DEVIATION_MIN_KWH` und ≥ `DEVIATION_MIN_SHARE · Plan`. Text: „Netzbezug 14,2 kWh statt geplant 9,8 kWh (+4,4 kWh).“ Link `/zeitplan`.
- **`target_missed` (warn):** je `MissedTarget`: „Tesla hat das Ziel 80 % um 07:00 verfehlt (erreicht: 71 %).“ Link `/laden`.
- **`command_failures` (warn):** je Aktor mit `failures > 0`: „3 Befehle an Wallbox EVCS gescheitert.“ Link `/entscheidungen?range=<Tag>&actuator=<id>&result=error`.
- **`counter_check` (info):** je markiertem Signal: „Abrechnung und Zähler weichen bei Netzbezug um 2,4 % ab.“ Link `/statistik`.

- [ ] **Step 1: Tests schreiben** – `test_unplanned_grid_charge_like_3_november` (Slots 22:30 und 22:45 mit je 2,2 kWh `grid>battery`, Plan `charge_grid_w = 0`, SOC 14 % bei Reserve 20 % → ein Treffer 22:30–23:00 mit „Vermutlich lud Victron selbst nach“), `test_planned_grid_charge_is_no_finding`, `test_slot_without_plan_counts_only_if_day_has_plans`, `test_small_grid_charge_below_1_kw_is_ignored`, `test_runs_with_one_gap_slot_merge`, `test_below_reserve_with_margin` (Reserve 20 %, 18,5 % → kein Treffer; 17,9 % → Treffer), `test_soc_unknown_gives_not_checked`, `test_month_peak_rise_with_split`, `test_no_month_peak_rule_on_first_of_month`, `test_plan_deviation_needs_both_thresholds` (2,9 kWh → nichts; 4 kWh bei 20 kWh Plan → nichts; 4 kWh bei 10 kWh → info), `test_missed_target_text`, `test_command_failures_per_actuator_with_label`, `test_flagged_counter_check`, `test_rules_on_dst_day_cover_100_slots` (25.10.2026), `test_findings_sorted_by_start_then_day_rules`.
- [ ] **Step 2: Tests laufen lassen, sie scheitern** – `uv run pytest tests/unit/app/ops/test_findings.py -v` → FAIL.
- [ ] **Step 3: `findings.py` umsetzen** – je Regel eine Funktion `_grid_charge(inputs) -> list[Finding]`; die Folgen über eine gemeinsame Hilfsfunktion `_runs(starts: Sequence[datetime]) -> list[tuple[datetime, datetime]]`.
- [ ] **Step 4: Tests grün** – derselbe Befehl → PASS.
- [ ] **Step 5: Commit** – `feat(app): Regeln für Auffälligkeiten eines Tages`

### Task 4: Auffälligkeiten sammeln, ablegen und abfragen

**Files:**
- Create: `src/bindaems/app/db/migrations/versions/v0016_ops.py`
- Modify: `src/bindaems/app/db/schema.py` (`ops_finding`, `ops_day`)
- Modify: `src/bindaems/app/charging/planned.py` (`PlannedCharges.missed`)
- Create: `src/bindaems/app/ops/service.py`
- Modify: `src/bindaems/app/ops/routes.py` (`GET /api/ops/findings`)
- Modify: `src/bindaems/app/runtime.py` (Dienst bauen, Job `ops-nightly`, Löschen in `_maintenance_loop`)
- Test: `tests/unit/app/test_db.py` (Migration auf und ab), `tests/unit/app/charging/test_planned.py`, `tests/unit/app/ops/test_service.py`, `tests/unit/app/ops/test_routes.py`

**Interfaces:**
- Consumes: `find`, `DayInputs`, `Finding`, `MissedTarget` (Task 3); `DecisionStore.failures` (Task 2); `LedgerService.slots`, `LedgerService.month_peak`; `PlanStore.plans_between`; `InfluxReader.query`, `build_catalog`, `build_query`, `RP_LONG`/`RP_RAW`; `ledger_check` (Tabelle, `flagged`); `cfg.battery.reserve_soc_pct`; `next_local`.
- Produces:
  - Tabelle `ops_finding`: `id` (PK), `day` `String(10)`, `rule` `String(32)`, `level` `String(8)`, `start` `UtcDateTime` nullable, `end` nullable, `text` `Text`, `link` `String(200)` nullable, `data` `JSON`, Index `ix_ops_finding_day`.
  - Tabelle `ops_day`: `day` `String(10)` PK, `computed_at` `UtcDateTime`, `report_sent_at` `UtcDateTime` nullable.
  - `PlannedCharges.missed(day: date) -> list[MissedTarget]` (Abfahrt am lokalen Tag, `outcome == "missed"`, Name aus `cfg.vehicles`)
  - `class FindingsService: __init__(self, engine, clock, cfg, ledger, plans, decisions, planned, reader: InfluxReader | None, labels: Callable[[], Mapping[str, str]])`
    - `async def run_day(self, day: date) -> list[Finding]` (sammelt `DayInputs`, ruft `find`, ersetzt die Zeilen des Tages in einer Transaktion, setzt `ops_day.computed_at`; `report_sent_at` bleibt)
    - `async def run(self) -> None` (beim Start fehlende Tage der letzten 7 bis gestern, dann täglich um `OPS_NIGHTLY_AT = (3, 5)` den Vortag)
    - `def recent(self, days: int) -> list[Finding]` (neueste zuerst)
    - `def day(self, day: date) -> list[Finding] | None` (`None`, solange der Tag nicht gerechnet ist)
    - `def cleanup(self, keep_days: int = 400) -> int`
  - `GET /api/ops/findings?days=14` (viewer, `days` 1 bis 60) → `{"days": [{"day": "2026-11-03", "computed": true, "findings": [...]}, ...], "report": <Task 5, bis dahin null>}`

SOC aus InfluxDB: Reihe `soc.battery` des Katalogs, `GROUP BY time(900s)` mit `min` statt `mean` (eigene kleine Abfrage nach dem Muster von `build_query`, kein Eingriff in `build_query`); `InfluxQueryError` oder kein Reader → `soc_min=None`. `previous_peak_kw`: Monatsspitze der Slots vom Monatsersten bis vor `day` (neue Methode `LedgerService.month_peak_before(day: date) -> MonthPeak | None`, gleiche Abfrage wie `month_peak` mit Obergrenze `day`).

- [ ] **Step 1: Tests schreiben** – `test_migration_0016_up_and_down`, `test_missed_lists_outcome_missed_of_day`, `test_run_day_stores_and_replaces` (zweiter Lauf desselben Tages ersetzt, verdoppelt nicht), `test_run_day_without_influx_gives_not_checked`, `test_run_at_start_fills_missing_days_only` (Tage mit `ops_day` werden übersprungen), `test_nightly_runs_at_0305_local` (Uhr-Fake, Zeitumstellung 25.10.), `test_findings_route_groups_by_day` (Tag ohne Lauf → `computed` false, `findings` leer), `test_findings_route_rejects_days_over_60` (422), `test_cleanup_keeps_400_days`.
- [ ] **Step 2: Tests laufen lassen, sie scheitern** – `uv run pytest tests/unit/app/test_db.py tests/unit/app/charging/test_planned.py tests/unit/app/ops -v` → FAIL.
- [ ] **Step 3: Migration und Schema** (Muster `v0015_thermal_model.py`).
- [ ] **Step 4: `missed`, `month_peak_before`, `FindingsService` und Route umsetzen.**
- [ ] **Step 5: Verdrahten** – Job `("ops-nightly", self.findings.run)` in der Liste der `_supervise`-Jobs; `cleanup` in `_maintenance_loop`.
- [ ] **Step 6: Integrationstest** – `tests/integration/test_app_ops.py::test_findings_after_unplanned_grid_charge`: app mit Fake-core, Abrechnung mit zwei Slots `grid>battery` und einem Plan ohne Netzladen, `run_day` → `GET /api/ops/findings` enthält `grid_charge_unplanned`.
- [ ] **Step 7: Prüfkette** – `uv run pytest tests/unit/app tests/integration -q`, `uv run mypy --strict src`, `uv run lint-imports` → grün.
- [ ] **Step 8: Commit** – `feat(app): Auffälligkeiten nachts rechnen und ablegen (Migration 0016)`

### Task 5: Tagesbericht in Home Assistant

**Files:**
- Modify: `src/bindaems/shared/settings.py` (`OpsSettings`, `RuntimeSettings.ops`)
- Create: `src/bindaems/app/ops/report.py`
- Modify: `src/bindaems/app/ha/notify.py` (`HaNotifier.report`)
- Modify: `src/bindaems/app/ops/service.py` (`ReportSender`), `src/bindaems/app/ops/routes.py` (Feld `report`), `src/bindaems/app/runtime.py` (Job `ops-report`)
- Modify: `ui/src/lib/api/contract/settings.json` (falls der Vertragstest es verlangt)
- Test: `tests/unit/shared/test_settings.py`, `tests/unit/app/ops/test_report.py`, `tests/unit/app/ha/test_notify.py`, `tests/unit/app/ops/test_service.py`

**Interfaces:**
- Consumes: `Finding` (Task 3), `FindingsService.day` (Task 4), `review` (`app/planning/review.py`), `StatsService.period("day", day)` (`money.net_eur`, `money.savings_no_plant_eur`, `energy.import_kwh`), `LedgerService.month_peak`, `OpsCheck` (Task 1), `HaNotifier`.
- Produces:
  - `class OpsSettings(_Model): report: Literal["daily", "findings", "off"] = "daily"; report_at: Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")] = "07:30"`; `RuntimeSettings.ops: OpsSettings = Field(default_factory=OpsSettings)` (Entscheidung 5)
  - `@dataclass(frozen=True) class DaySummary: import_kwh: float | None; planned_import_kwh: float | None; grid_charge_kwh: float | None; planned_grid_charge_kwh: float | None; net_eur: float | None; savings_eur: float | None; month_peak_kw: float | None`
  - `def report_text(day: date, summary: DaySummary, findings: Sequence[Finding] | None, check: OpsCheck | None) -> str` – höchstens drei Treffer (`warn` vor `info`), dann „und N weitere unter Betrieb“; `findings is None` → „Auffälligkeiten noch nicht gerechnet.“; ohne Treffer „Keine Auffälligkeiten.“; Schluss „Betriebscheck jetzt: alles in Ordnung.“ bzw. „… 2 Befunde.“ Fehlende Zahlen entfallen samt Satzteil.
  - `HaNotifier.report(text: str) -> bool` – `notification_id` `bindaems_tagesbericht` (ersetzt den Vortag), Titel „BindaEMS Tagesbericht“, danach `notify` wie `_raise`; zählt ins Budget je Minute.
  - `class ReportSender: __init__(self, engine, clock, settings, findings, summary: Callable[[date], DaySummary], check: Callable[[], OpsCheck], notifier: HaNotifier | None)`; `async def run(self) -> None` (jede Minute: ist es nach `report_at` lokal, ist `ops_day(gestern).report_sent_at` leer und `report != "off"` und bei `"findings"` mindestens ein Treffer `warn` → senden und `report_sent_at` setzen; scheitert das Senden, nächster Versuch nach 15 min, längstens bis 23:59)
  - `GET /api/ops/findings` → `report`: `{"day": "<gestern>", "text": report_text(...)}` (auch ohne Home Assistant)

Hinweis HA: Der Blueprint `bindaems_benachrichtigungen.yaml` (Projektordner `ha-benachrichtigungen/`) leitet jede persistente Meldung `bindaems_*` als Warnung aufs Handy weiter; ist zusätzlich `notify_service` gesetzt, käme der Bericht doppelt. Task 8 ergänzt das LIESMICH des Blueprints: `bindaems_tagesbericht` bekommt den Titel „Tagesbericht“ statt „Warnung“ (Bedingung auf die `notification_id`).

Beispiel (Erwartung im Test, Zahlen erfunden): „Montag 3.11.: Netzbezug 14,2 kWh (geplant 9,8), davon Netzladen 4,4 kWh (geplant 0,0). Kosten 3,12 €, Ersparnis 1,85 €. Monatsspitze 9,1 kW. 2 Auffälligkeiten: Akku lud 22:30–23:00 mit bis zu 9,1 kW aus dem Netz … Neue Monatsspitze 9,1 kW um 22:30 … Betriebscheck jetzt: alles in Ordnung.“

- [ ] **Step 1: Tests schreiben** – `test_ops_settings_defaults` und `test_ops_settings_reject_bad_time`, `test_stored_settings_without_ops_load`; `test_report_text_like_3_november`, `test_report_text_quiet_day`, `test_report_text_not_computed`, `test_report_text_caps_at_three` (fünf Treffer → drei und „und 2 weitere unter Betrieb“), `test_report_text_leaves_out_unknown_numbers`; `test_ha_report_replaces_notification_and_notifies`, `test_ha_report_respects_minute_budget`; `test_report_sent_after_report_at_once`, `test_report_sent_once_per_day_across_restart` (zweiter `ReportSender` mit derselben DB sendet nicht), `test_findings_mode_skips_quiet_day`, `test_off_sends_nothing`, `test_failed_send_retries_after_15_min`.
- [ ] **Step 2: Tests laufen lassen, sie scheitern** – `uv run pytest tests/unit/shared/test_settings.py tests/unit/app/ops tests/unit/app/ha/test_notify.py -v` → FAIL.
- [ ] **Step 3: Einstellung, `report.py`, `HaNotifier.report`, `ReportSender` umsetzen.**
- [ ] **Step 4: Verdrahten** – `DaySummary` aus `review(...)["totals"]` (geplant/Ist `import_kwh`, `grid_charge_kwh`), `StatsService.period("day", day)` und `LedgerService.month_peak(day)`; Job `("ops-report", self.report_sender.run)`.
- [ ] **Step 5: Tests und Vertragstests grün** – `uv run pytest tests/unit tests/integration -q` → PASS; ist der API-Vertrag veraltet, mit dem Werkzeug neu schreiben, das der scheiternde Test nennt.
- [ ] **Step 6: Commit** – `feat(app): Tagesbericht mit Auffälligkeiten in Home Assistant`

### Task 6: Schalter-Übersicht

**Files:**
- Modify: `src/bindaems/app/audit.py` (`AuditLog.latest_of`)
- Create: `src/bindaems/app/ops/switches.py`
- Modify: `src/bindaems/app/ops/routes.py` (`GET /api/ops/switches`)
- Test: `tests/unit/app/test_audit.py`, `tests/unit/app/ops/test_switches.py`, `tests/unit/app/ops/test_routes.py`

**Interfaces:**
- Consumes: `RuntimeSettings` (inkl. `ops` aus Task 5), `Intent` (über `LiveState.health["intent"]` bzw. `IntentService.cached()`), `cfg.vehicles`, `cfg.wallboxes`, Health `actuators[]`, `AuditEntry`.
- Produces:
  - `AuditLog.latest_of(actions: Collection[str], limit: int = 2000) -> list[AuditEntry]` (neueste zuerst, nur diese Aktionen)
  - `@dataclass(frozen=True) class Switch: id: str; label: str; group: str; value: str; factory: str; changed: bool; since: datetime | None; actor: str | None; source: str | None; link: str`
  - `def switches(*, settings: RuntimeSettings, intent: Intent | None, health: Mapping[str, Any] | None, cfg: Config, audit: Sequence[AuditEntry], wallbox_names: Mapping[str, str]) -> list[Switch]`
  - `GET /api/ops/switches` (viewer) → `{"switches": [...]}`

Katalog (Gruppe, Stand, ab Werk, Treffer im Protokoll, Link):

| id | Gruppe | Stand aus | ab Werk | Protokoll | Link |
|---|---|---|---|---|---|
| `control` | Regelung | `intent.control` | aus | `intent.control` | `/system` |
| `actuator.<id>` je Aktor | Regelung | Health `requested` (`live`/`dry_run`/…), mit „in config.yaml gesperrt“, wenn `live_allowed` false | Dry-Run | `intent.actuators`, `target` enthält `<id>` | `/system` |
| `grid_charge` | Akku | `planning.grid_charge_allowed` | aus | `settings.update` mit Pfad `planning.grid_charge_allowed` | `/zeitplan` |
| `car_battery` | Akku | `planning.car_battery_allowed` | aus | Pfad `planning.car_battery_allowed` | `/einstellungen` |
| `support` | Akku | `intent.support.allowed` und `soc_pct` („an ab 70 %“) | an, Schwelle aus `config.yaml` | `control.support` | `/laden` |
| `load_model` | Prognosen | `learning.load_model_enabled` | an | Pfad `learning.load_model_enabled` | `/prognosen` |
| `thermal_plan` | Klima | `learning.thermal.enabled` | aus | Pfad `learning.thermal.enabled` | `/zeitplan` |
| `automation.<key>` je Auto | Autos | `learning.driving.automation[key]` | aus | `control.automation` mit `target == key`, sonst Pfad `learning.driving.automation.<key>` | `/laden` |
| `default_mode.<key>` je Ladestation | Autos | `wallbox_default_modes[key]` oder „Unverändert“ | Unverändert | Pfad `wallbox_default_modes.<key>` | `/einstellungen` |
| `report` | Benachrichtigungen | `ops.report` („täglich 07:30“, „nur bei Auffälligkeiten“, „aus“) | täglich 07:30 | Pfade `ops.report`, `ops.report_at` | `/einstellungen` |

„Seit wann und wer“: der jüngste passende Eintrag; beim Pfad zählt ein Eintrag von `settings.update`, dessen `details.changes[].path` den Pfad trifft (Präfix bei Blättern unter dem Pfad). Ohne Treffer `since`, `actor`, `source` = `None` (UI: „seit Einrichtung“). Fehlt die Absicht (core weg), steht der Stand der Absicht-Schalter auf „unbekannt“ und `changed` auf false. Labels der Aktoren aus Health `label`, Namen der Ladestationen über `wallbox_names`.

- [ ] **Step 1: Tests schreiben** – `test_latest_of_filters_actions_newest_first`; `test_all_switches_listed_in_catalog_order` (zwei Autos, zwei Ladestationen, vier Aktoren → erwartete IDs in der Reihenfolge der Tabelle), `test_factory_values_not_changed`, `test_grid_charge_since_and_actor_from_settings_update` (zwei Einstellungsänderungen, nur die zweite ändert `planning.grid_charge_allowed` → deren Zeit und Benutzer), `test_automation_from_control_automation_entry`, `test_actuator_entry_matches_target_list` (`target "victron,wallbox.evcs"` trifft beide), `test_actuator_live_not_allowed_shows_config_lock`, `test_intent_unknown_without_core`, `test_switches_route_requires_login`.
- [ ] **Step 2: Tests laufen lassen, sie scheitern** – `uv run pytest tests/unit/app/test_audit.py tests/unit/app/ops -v` → FAIL.
- [ ] **Step 3: `latest_of`, `switches.py` und Route umsetzen.**
- [ ] **Step 4: Tests grün** – `uv run pytest tests/unit/app -q` → PASS.
- [ ] **Step 5: Commit** – `feat(app): Schalter-Übersicht mit Stand, ab Werk und letzter Änderung`

### Task 7: Seite „Betrieb“ im UI

**Files:**
- Modify: `ui/src/lib/api/schemas.ts` (`OpsCheckSchema`, `OpsFindingsSchema`, `OpsSwitchesSchema`; `ops` in den Einstellungen als `v.optional(..., {report: 'daily', report_at: '07:30'})`)
- Modify: `ui/src/lib/api/endpoints.ts` (`opsCheck`, `opsFindings(days)`, `opsSwitches`)
- Create: `ui/src/lib/ops/view.ts` (Texte, Sortierung, Gruppierung), `ui/src/lib/ops/CheckCard.svelte`, `ui/src/lib/ops/FindingsCard.svelte`, `ui/src/lib/ops/SwitchesCard.svelte`
- Create: `ui/src/routes/betrieb/+page.svelte`, `ui/src/routes/betrieb/page.test.ts`
- Modify: `ui/src/lib/nav.ts` (nach „System“: `{ href: '/betrieb', label: 'Betrieb', icon: 'check' }`, neues Icon im bestehenden Icon-Satz), `ui/src/routes/system/+page.svelte` (Zeile oben mit Link)
- Modify: `ui/src/lib/settings/model.ts`, `ui/src/lib/settings/SettingsEditor.svelte` (Abschnitt „Tagesbericht“: Auswahl „Täglich“, „Nur bei Auffälligkeiten“, „Aus“ und Uhrzeit)
- Modify: `tests/ui_world.py` und `ui/src/lib/api/contract/*.json` (Vertragsdateien `ops-check.json`, `ops-findings.json`, `ops-switches.json`)
- Test: `ui/src/lib/ops/view.test.ts`, `ui/src/lib/ops/*.test.ts`, `ui/src/lib/nav.test.ts`, `ui/src/lib/settings/model.test.ts`, `ui/e2e/betrieb.spec.ts`

**Interfaces:**
- Consumes: die drei Routen aus Task 2, 4, 6 und die Einstellung aus Task 5.
- Produces: Route `/betrieb`; `view.ts`: `levelLabel(level) -> string` („in Ordnung“, „Warnung“, „Fehler“, „unbekannt“), `checkHeadline(check) -> string` („Alles in Ordnung“ / „2 Warnungen, 1 Fehler“), `groupSwitches(list) -> {group: string; items: Switch[]}[]`, `sinceText(switch, now) -> string` („seit 3.11. 19:45 · Binda (UI)“ bzw. „seit Einrichtung“).

Darstellung: Karte 1 klappt grüne Zeilen ein („12 Prüfungen in Ordnung“ aufklappbar), gelbe und rote stehen offen. Karte 2 zeigt oben den Bericht von gestern, darunter je Tag die Treffer, Tage ohne Treffer als eine Zeile „keine“. Karte 3 als Tabelle je Gruppe, am Handy (390 px) als Liste; geänderte Schalter mit Markierung „geändert“. Aktualisierung: Check alle 15 s, Auffälligkeiten und Schalter alle 5 min (`Resource` wie auf System).

- [ ] **Step 1: Tests schreiben** – Vitest: `levelLabel`, `checkHeadline`, `groupSwitches`, `sinceText`; Komponenten: `CheckCard` zeigt Fehler offen und grüne eingeklappt, `FindingsCard` zeigt Bericht und „keine“-Tage, `SwitchesCard` markiert geänderte; `nav` enthält „Betrieb“ nach „System“; Einstellungen: Hin- und Rückweg von `ops`. Vertragstest für die drei neuen Antworten. E2E `betrieb.spec.ts`: Seite lädt in der Demo-Welt, alle drei Karten sichtbar, Link einer Auffälligkeit führt auf `/entscheidungen` mit gesetztem Filter.
- [ ] **Step 2: Tests laufen lassen, sie scheitern** – `cd ui && pnpm test -- --run src/lib/ops` → FAIL.
- [ ] **Step 3: Schemas, Endpunkte, Demo-Welt und Vertragsdateien** (Vertrag mit dem bestehenden Werkzeug neu schreiben, siehe `docs/uebergabe.md`, „Befehle“).
- [ ] **Step 4: Komponenten, Seite, Navigation, System-Zeile, Einstellungen umsetzen.**
- [ ] **Step 5: UI-Prüfkette** – `pnpm check`, `pnpm lint`, `pnpm test`, E2E (`pnpm exec playwright test e2e/betrieb.spec.ts`; lokal mit `launchOptions.executablePath` `/opt/pw-browsers/chromium`) → grün. Bildschirmfotos 390 px hell und 1440 px dunkel nach `/mnt/project-files/plaene/betrieb-im-blick/`.
- [ ] **Step 6: Commit** – `feat(ui): Seite Betrieb mit Betriebscheck, Auffälligkeiten und Schaltern`

### Task 8: Sensor in Home Assistant und Doku

**Files:**
- Modify: `src/bindaems/app/ha/entities.py` (`HaInputs.ops_level`, `ops_text`; Sensor `ops_check`, Name „Betriebscheck“, Zustand `ok`/`warnung`/`fehler`/`unbekannt`, Attribut `befunde` mit den Texten der gelben und roten Prüfungen)
- Modify: `src/bindaems/app/runtime.py` (`_build_ha_inputs` ruft den Betriebscheck mit den Zählern der letzten Stunde)
- Modify: `docs/betrieb.md` (neuer Abschnitt „Betrieb: Check, Auffälligkeiten, Schalter“ unter 12. Web-UI; unter „Benachrichtigungen“ der Tagesbericht; Rückweg mit `ops`), `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absatz „Stand Plan Betrieb im Blick“ in 12 und 13), `docs/uebergabe.md` (Aufbau: `app/ops/`; Stand; Migration `0016`)
- Modify: `/mnt/project-files/anlage/befehle-nach-3c.md` erst nach dem Ausrollen (B2/B6: „im UI unter Betrieb“ statt der `jq`-Befehle; die Befehle bleiben als Rückfall)
- Test: `tests/unit/app/ha/test_entities.py`, `tests/unit/test_docs.py`

**Interfaces:**
- Consumes: `operations_check`, `OpsCheck` (Task 1), `DecisionStore.counts` (Task 2).
- Produces: Entität `sensor.bindaems_ops_check` über MQTT Discovery; `test_docs.py` prüft, dass `docs/betrieb.md` „Tagesbericht“, „Betriebscheck“ und „Schalter“ nennt.

- [ ] **Step 1: Tests schreiben** – `test_ops_check_sensor_states` (je Gesamtstufe der Zustand), `test_ops_check_attribute_lists_findings`, `test_ops_check_unknown_without_core`, `test_betrieb_doc_names_ops_page`.
- [ ] **Step 2: Tests laufen lassen, sie scheitern** – `uv run pytest tests/unit/app/ha/test_entities.py tests/unit/test_docs.py -v` → FAIL.
- [ ] **Step 3: Sensor umsetzen und verdrahten.**
- [ ] **Step 4: Doku nachführen** (Betriebshandbuch, Spec-Absatz, Übergabe).
- [ ] **Step 5: Ganze Prüfkette** – Python und UI wie in den Global Constraints, dazu E2E → grün.
- [ ] **Step 6: Commit** – `feat(app): Betriebscheck als Sensor in Home Assistant` und `docs: Betrieb im Blick in Betriebshandbuch, Spec und Übergabe`

---

## Abnahme

1. **In der Demo-Welt (vor dem Ausrollen):** Seite Betrieb zeigt alle drei Karten; Betriebscheck grün mit Klima „Dry-Run (so gewünscht)“; ein künstlicher Tag mit Netzladen ohne Plan erzeugt den Treffer und den Tagesbericht; die Schalter zeigen „ab Werk“.
2. **An der Anlage (nach Stufe 2, ab 23.11.):** Betriebscheck gegen die `jq`-Befehle aus B6 einmal nebeneinander: gleiche Aussage. Am Morgen danach kommt der Tagesbericht aufs Handy. Die Auffälligkeiten des 3.11. lassen sich nachträglich nicht mehr rechnen (Pläne werden 30 Tage gehalten, der Lauf beim Start rechnet nur 7 Tage); als Nachweis zählt der erste echte Treffer oder ein ruhiger Bericht.
3. **Schalter:** jeden Schalter einmal mit der Seite vergleichen, auf der er gestellt wird; „seit“ und „wer“ stimmen mit dem Protokoll.

## Nicht in diesem Plan

- Live-Alarm für Auffälligkeiten (die Alarme des core decken den Moment ab).
- Schwellen in den Einstellungen (erst, wenn die Konstanten an der Anlage stören).
- Ersparnis gegen „Victron allein“ aus echten Tagen (Entscheidung 6).
- Schalter direkt auf der Seite Betrieb stellen (die Seite verlinkt; Stellen bleibt an einer Stelle).
- Lebenszeichen außerhalb des Clusters (1.3) und USV-Reichweite (1.5): eigener kleiner Plan (Rang 7).

# Diagramme und Plantexte Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Diagramme im UI überlappen am Handy nicht mehr, die Erklärungen im Zeitplan und in „Warum?“ sagen, was wirklich passiert, und der Planer plant für ein Auto, das der core als „Ziel erreicht“ hält, keinen Restbedarf mehr.

**Architecture:** Grafik nur in den ECharts-Optionen (`ui/src/lib/charts/options.ts`, `ui/src/lib/plan/chart.ts`). Texte in `src/bindaems/app/planning/explain.py` (Erklärungen je Slot) und in `ui/src/lib/decisions/WhyCard.svelte` (Entscheidungen). Zwei neue, optionale Felder im gespeicherten Plan: `Plan.soc_start_pct` (Start-SOC des Laufs) und `PlanVehicle.reached` (der core meldet „Ziel erreicht“); ältere Pläne laden mit `None` bzw. `False`. Nur app und UI, kein core, keine Migration.

**Tech Stack:** Python 3.13 (pydantic, FastAPI), SvelteKit + valibot + ECharts, pytest, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Abschnitt Erklärungen/Texte, Präzisierung 12 von Plan 3b, Präzisierung 13 von Plan 4b) und die freigegebene Fix-Liste `/mnt/project-files/plaene/diagramme-plantexte-fixliste.md` (Freigabe des Betreibers 29.10.2026: G1–G4, T1–T6 und R1; R2 nicht).

## Global Constraints

- Deutsch in Code-Kommentaren, Texten und Commits; Commits im Stil `fix(app): …`, `fix(ui): …`, `docs: …`. Keine Modellbezeichnungen im Repo.
- Prüfkette vor jedem Commit: `uv run ruff format --check`, `uv run ruff check`, `uv run mypy` (strict, src + cerbo-watchdog), `uv run lint-imports`, betroffene pytest-Dateien; UI: `pnpm check`, `pnpm lint`, `pnpm test` (Vitest). In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Zahlenformat der Sätze wie bisher (`_de`: Dezimalkomma, `%` ganzzahlig, kW/ct eine Nachkommastelle), Uhrzeiten lokal `HH:MM`.
- Gespeicherte Pläne in der DB müssen weiter laden und ihre Texte bilden: neue Parameter in alten Gründen fehlen.
- Kein Aktor wird geschaltet; der core ändert sich nicht.
- Die Tabelle der Codes in `docs/betrieb.md` („Planung“) ist durch `tests/unit/test_docs.py` an `explain.sentence` gebunden: jede Satzänderung dort mitziehen.
- Vertragsdateien des UI neu schreiben mit `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py`.

## Review Focus

1. Alter Plan aus der DB mit `AUTO_WARTET_AUF_PV` ohne `um`: der Satz fällt auf den alten Wortlaut zurück, kein `KeyError` beim `GET /api/plan` (Test in Task 4).
2. Ein Auto meldet „erreicht“, fällt aber im nächsten Zyklus unter Ziel − 3 pp: der nächste Planlauf (spätestens nach 15 min) plant wieder den Bedarf, weil Health dann nicht mehr `reached` meldet (Test in Task 5: ohne `reached` voller Bedarf).
3. Legende mit einer einzigen Reihe (Diagramm „Autos“ mit einem Auto) und Diagramme ohne „jetzt“ (Horizont liegt nicht um jetzt): keine Fehler, kein `markLine` (Tests in Task 1).
4. Entscheidungstext, der **nicht** mit dem Anzeigenamen beginnt (Name in der Konfiguration geändert, alter Eintrag): „Name · Text“ wie bisher (Test in Task 3).
5. Plan ohne `soc_start_pct` (alter Plan) im Zeitplan: erster Block wie bisher mit dem SOC am Ende seines ersten Slots (Test in Task 6).

---

### Task 1: Legende, „jetzt“ und Tooltip in allen Diagrammen (G1–G3)

**Files:**
- Modify: `ui/src/lib/charts/options.ts` (`baseOption`, `priceForecastOption`, alle `tooltip`-Blöcke der Datei)
- Modify: `ui/src/lib/plan/chart.ts` (`nowLine` entfällt, alle drei Optionen)
- Test: `ui/src/lib/charts/options.test.ts`, `ui/src/lib/plan/chart.test.ts`

**Interfaces:**
- Produces: `export function nowMarkLine(now: number | null, palette: Palette)` in `options.ts` (ersetzt `nowLine` in `chart.ts` und den Block in `priceForecastOption`); `undefined` bei `now === null`, sonst `{ symbol: 'none', silent: true, lineStyle: { color: palette.muted, type: 'solid' }, label: { formatter: 'jetzt', color: palette.muted, position: 'insideEndTop' }, data: [{ xAxis: now }] }`.
- Produces: `baseOption(palette).legend` hat `type: 'scroll'`; `baseOption(palette).tooltip` ist `{ confine: true }`.

- [ ] **Step 1: Failing tests**

In `options.test.ts`:
```ts
it('legt die Legende in eine Zeile und hält Tooltips im Diagramm', () => {
	const option = baseOption(palette) as { legend: { type: string }; tooltip: { confine: boolean } };
	expect(option.legend.type).toBe('scroll');
	expect(option.tooltip.confine).toBe(true);
});
it('setzt „jetzt“ senkrecht an die Linie innerhalb des Diagramms', () => {
	expect(nowMarkLine(1000, palette)?.label).toMatchObject({ formatter: 'jetzt', position: 'insideEndTop' });
	expect(nowMarkLine(null, palette)).toBeUndefined();
});
```
Dazu für jede Option der Datei, die einen `tooltip` setzt (`priceForecastOption`, `historyOption`, `statsEnergyOption`, `vehicleTrendOption`, `qualityDailyOption`, `presenceOption`): `expect(option.tooltip.confine).toBe(true)` und `expect(option.legend.type).toBe('scroll')` (wo die Option `legend` überschreibt, muss `type` erhalten bleiben). In `chart.test.ts` dasselbe für `planPriceOption`, `planSocOption`, `planVehiclesOption`, und: mit `now` außerhalb des Horizonts hat keine Reihe ein `markLine`.

- [ ] **Step 2:** `cd ui && pnpm vitest run src/lib/charts/options.test.ts src/lib/plan/chart.test.ts` → FAIL (`legend.type` undefined, `nowMarkLine` nicht exportiert).

- [ ] **Step 3: Implementieren.** `baseOption` bekommt `legend: { type: 'scroll', top: 0, textStyle: …, pageTextStyle: { color: palette.muted }, pageIconColor: palette.text, pageIconInactiveColor: palette.muted }` und `tooltip: { confine: true }`. Jede Option, die `tooltip` setzt, spreizt den Basis-Tooltip (`tooltip: { ...base.tooltip, trigger: 'axis', formatter: … }`). `nowMarkLine` ersetzt beide bisherigen Fassungen.

- [ ] **Step 4:** Tests aus Step 2 grün, dann `pnpm check && pnpm lint && pnpm test`.

- [ ] **Step 5: Commit** `fix(ui): Legende in einer Zeile, „jetzt“ an der Linie, Tooltips im Diagramm`

---

### Task 2: Akku-Diagramm ohne übereinanderliegende Beschriftungen (G4)

**Files:**
- Modify: `ui/src/lib/plan/chart.ts` (`planSocOption`)
- Test: `ui/src/lib/plan/chart.test.ts`

**Interfaces:**
- Consumes: `nowMarkLine` (Task 1), `blockLabel`-Wortlaut aus `ui/src/lib/plan/model.ts` („Netzladen 6,7 kW“, „Halten“, „Eigenverbrauch“; `INTENT_LABELS`, `formatNumberMax`).
- Produces: `export function intentAt(slots: PlanSlot[], time: number): string | null` in `chart.ts`: Beschriftung der Absicht des Slots, der `time` enthält (`start ≤ time < start + 15 min`), sonst `null`.

- [ ] **Step 1: Failing tests** in `chart.test.ts`:
	- `planSocOption`: kein Eintrag in `series[0].markArea.data` hat ein sichtbares Label (`label.show === false` am ersten Punkt jedes Paars).
	- Legende: `legend.data` ist `['Geplanter SOC', 'Reserve', 'Netzladen', 'Halten']`, wenn der Plan beide Absichten hat; ohne `HALTEN`-Slot fehlt `'Halten'`. Die Legendeneinträge gehören zu zwei Reihen ohne Daten (`data: []`) mit `itemStyle.color` `palette.importPrice` bzw. `palette.pv`.
	- `intentAt`: Slot `NETZLADEN` mit `grid_charge_w: 6700` → `'Netzladen 6,7 kW'`; `HALTEN` → `'Halten'`; Zeit hinter dem letzten Slot → `null`.
	- Tooltip: der Formatter von `planSocOption` liefert für einen Zeitpunkt in einem Netzladen-Slot eine Zeile `Akku: <b>Netzladen 6,7 kW</b>` nach den Werten.

- [ ] **Step 2:** `pnpm vitest run src/lib/plan/chart.test.ts` → FAIL.

- [ ] **Step 3: Implementieren.** Flächen behalten Farbe und Deckkraft, `label: { show: false }`. Zwei Legendenreihen `{ id: 'legend_netzladen', name: 'Netzladen', type: 'line', data: [], itemStyle, lineStyle: { width: 0 }, areaStyle }` (für `Halten` entsprechend), nur wenn die Absicht im Plan vorkommt. Klick auf die Legende blendet die Fläche nicht aus (sie hängt an der SOC-Reihe); das ist gewollt. Tooltip: `axisTooltip({ 'Geplanter SOC': socFormat, Reserve: socFormat, Netzladen: null, Halten: null })` und dahinter `Akku: <b>${escape(intentAt(...))}</b>`, wenn nicht `null`. `intentAt` nutzt die Beschriftung aus `model.ts` (gemeinsame Funktion `intentLabel(intent, gridChargeW)` dort exportieren und in `blockLabel` verwenden, statt sie doppelt zu schreiben).

- [ ] **Step 4:** grün, dann `pnpm check && pnpm lint && pnpm test`.

- [ ] **Step 5: Commit** `fix(ui): Akku-Diagramm mit Legende und Tooltip statt Beschriftung je Fenster`

---

### Task 3: „Warum?“ ohne doppelten Namen (T1)

**Files:**
- Modify: `ui/src/lib/decisions/view.ts`, `ui/src/lib/decisions/WhyCard.svelte`
- Test: `ui/src/lib/decisions/view.test.ts`, `ui/src/lib/decisions/WhyCard.test.ts`

**Interfaces:**
- Produces: `export function splitName(text: string, name: string): string | null` in `view.ts`: der Rest des Textes ohne den Namen am Anfang, wenn `text` mit `name` beginnt und danach `' '`, `'('` oder `':'` folgt (der Rest ohne führendes Leerzeichen); sonst `null`.

- [ ] **Step 1: Failing tests**
	- `splitName('Victron (ESS) (Plan: Netzladen): Netz-Sollwert 6070 W – Dry-Run, nicht gesendet.', 'Victron (ESS)')` → `'(Plan: Netzladen): Netz-Sollwert 6070 W – Dry-Run, nicht gesendet.'`
	- `splitName('Victron (ESS): Netz-Sollwert 0 W – gesendet.', 'Victron (ESS)')` → `': Netz-Sollwert 0 W – gesendet.'`
	- `splitName('EVCS alt: Laden aus.', 'EVCS „evcs“')` → `null`; `splitName('Victron2: x', 'Victron')` → `null`.
	- `WhyCard`: mit Text, der mit dem Namen beginnt, steht der Name genau einmal im Eintrag (als Link), kein `·`; der Eintrag liest sich „Victron (ESS) (Plan: Netzladen): Netz-Sollwert …“ (Link, ein Leerzeichen, Rest; vor `:` kein Leerzeichen). Ohne passenden Anfang bleibt „Name · Text“.

- [ ] **Step 2:** `pnpm vitest run src/lib/decisions` → FAIL.

- [ ] **Step 3: Implementieren** in `WhyCard.svelte` mit `splitName(decision.text, names[decision.actuator] ?? decision.actuator)`. `DecisionTable` bleibt (eigene Spalte „Aktor“).

- [ ] **Step 4:** grün, `pnpm check && pnpm lint && pnpm test`.

- [ ] **Step 5: Commit** `fix(ui): „Warum?“ nennt den Aktor nur einmal`

---

### Task 4: Erklärungen im Zeitplan (T4, T5, T6)

**Files:**
- Modify: `src/bindaems/app/planning/explain.py`, `src/bindaems/shared/plan.py` (`ReasonCode` um `AKKU_AN_UNTERGRENZE`)
- Modify: `ui/src/lib/api/schemas.ts` (`PlanReasonCodeSchema` um `'AKKU_AN_UNTERGRENZE'`)
- Modify: `docs/betrieb.md` (Tabelle der Codes, Abschnitt „Planung“), `tests/unit/test_docs.py` (Beispiel und Reihenfolge)
- Test: `tests/unit/app/planning/test_explain.py`

**Interfaces:**
- Consumes: `spread_pays`, `USED_ABOVE_W`, `EMPTY_TOL_KWH`, `MENTION_W` aus `plausibility.py`; `params_of` aus `battery_model.py`.
- Produces: Code `AKKU_AN_UNTERGRENZE` mit `{"soc": float}`; `AUTO_WARTET_AUF_PV` mit `{"name": str, "um": "HH:MM"}`.

Entscheidungen:
- **T4:** `NETZLADEN_GUENSTIG` nennt den **ersten** späteren Slot mit Entladung über `USED_ABOVE_W`, für den `spread_pays(inputs.planning, jetzt, später)` gilt (neue private Funktion `_first_paying_discharge` in `explain.py`); gibt es keinen, gilt der bisherige Rückfall (teuerster späterer Slot). `pays_off` und damit Plausibilität und Absichten bleiben unverändert.
- **T5:** neuer Hauptcode `AKKU_AN_UNTERGRENZE` vor `EIGENVERBRAUCH`: Absicht `EIGENVERBRAUCH`, der Akku entlädt weniger als `MENTION_W`, das Haus bezieht mindestens `MENTION_W` aus dem Netz (`import_w − ev_grid_w ≥ MENTION_W`) und der SOC am Slotbeginn liegt höchstens `EMPTY_TOL_KWH` über `lo_kwh`. `soc` ist `lo_kwh` in % der Kapazität. Satz: „Akku steht an der Untergrenze ({soc} %), das Haus bezieht aus dem Netz.“
- **T6:** `AUTO_WARTET_AUF_PV` nur im ersten Slot jeder zusammenhängenden Folge, in der die bisherige Bedingung gilt; `um` ist der Beginn des ersten späteren Slots im Fenster mit `from_pv ≥ MENTION_W`. Satz: „{name} wartet bis {um} Uhr auf PV-Überschuss (Regel R).“ Fehlt `um` (alter Plan), bleibt der alte Satz „{name} wartet auf PV-Überschuss (Regel R).“

- [ ] **Step 1: Failing tests** in `test_explain.py`:
	- `test_grid_charge_names_the_first_paying_discharge`: Preise `[20, 40, 40, 60]`, Absicht Netzladen in Slot 0, Entladung in Slot 1–3 → Satz von Slot 0 nennt „um {Uhrzeit von Slot 1} Uhr 40,0 ct“ (nicht 60,0).
	- `test_battery_at_floor_says_so`: Akku startet an der Reserve, Last 1000 W, keine PV, Absicht `EIGENVERBRAUCH` → Code `AKKU_AN_UNTERGRENZE`, Text „Akku steht an der Untergrenze (20 %), das Haus bezieht aus dem Netz.“; derselbe Slot mit SOC 50 % → `EIGENVERBRAUCH` bzw. `PV_UEBERSCHUSS_ERWARTET` wie bisher.
	- `test_car_waits_for_pv_when_rule_r2_binds` anpassen: Slot 0 endet auf „Tesla Model 3 wartet bis {HH:MM von Slot 2} Uhr auf PV-Überschuss (Regel R).“, Slot 1 hat **keinen** `AUTO_WARTET_AUF_PV` mehr.
	- `test_expected_car_waits_for_pv_only_after_arrival`: `waits` mit vier Slots, Ankunft Slot 2, PV in Slot 3 bleibt `[False, False, True, False]`.
	- `test_waiting_run_restarts_after_charging`: Wartefolge, ein Slot PV-Laden, wieder Wartefolge, dann PV → zwei Nennungen, je am Beginn der Folge.
	- `test_old_waiting_reason_without_time_keeps_its_sentence`: `sentence(Reason(code="AUTO_WARTET_AUF_PV", params={"name": "e-Golf"}))` → „e-Golf wartet auf PV-Überschuss (Regel R).“
	- Die Tabelle `SENTENCES`/Beispiele oben in `test_explain.py` (Zeilen um 55 und 129) und in `test_docs.py` bekommen `um: "09:30"` bei `AUTO_WARTET_AUF_PV` und das Beispiel `AKKU_AN_UNTERGRENZE: {"soc": 20.0}`; die Reihenfolge in `test_docs.py` setzt `AKKU_AN_UNTERGRENZE` zwischen `PV_UEBERSCHUSS_ERWARTET` und `EIGENVERBRAUCH`.

- [ ] **Step 2:** `uv run pytest tests/unit/app/planning/test_explain.py tests/unit/test_docs.py -q` → FAIL.

- [ ] **Step 3: Implementieren** nach den Entscheidungen oben; Docstring von `_car_reasons` und `_main_reason` nachführen. `docs/betrieb.md`: Zeilen `NETZLADEN_GUENSTIG` („um“ ist der erste spätere Slot mit Entladung, für den sich das Laden auszahlt), `AUTO_WARTET_AUF_PV` (nur im ersten Slot der Wartezeit, „bis“ ist der erste Slot mit PV-Laden) und neue Zeile `AKKU_AN_UNTERGRENZE`. UI-Schema um den Code ergänzen; `ui/src/lib/api/contract.test.ts` nur anpassen, wenn er die Liste der Codes prüft.

- [ ] **Step 4:** `uv run pytest tests/unit/app/planning tests/unit/test_docs.py tests/unit/shared/test_plan.py -q` grün; `uv run pytest tests/integration -q -k "plan or scenario"` grün; UI `pnpm check && pnpm test`.

- [ ] **Step 5: Commit** `fix(app): Erklärungen nennen den nächsten Entladeblock, die Untergrenze und das Warten einmal`

---

### Task 5: Planer übernimmt „Ziel erreicht“ des core (R1)

**Files:**
- Modify: `src/bindaems/app/planning/vehicles.py` (`_car_input`, `planned_vehicles`), `src/bindaems/app/planning/inputs.py` (`PlanVehicleInput.reached: bool = False`), `src/bindaems/shared/plan.py` (`PlanVehicle.reached: bool = False`), `src/bindaems/app/planning/builder.py` (`vehicle_summary` reicht `reached` durch), `src/bindaems/app/planning/routes.py` (`vehicle_json` mit `"reached"`)
- Test: `tests/unit/app/planning/test_vehicles.py`, `tests/unit/app/planning/test_builder.py`, `tests/unit/app/planning/test_routes.py`

**Interfaces:**
- Consumes: Health je Auto `planned.state` (`"reached"`, siehe `bindaems.app.charging.planned.REACHED_STATE`).
- Produces: `_car_input(..., reached: bool = False)`; mit `reached` ist `need_kwh = horizon_need_kwh = grid_allowed_kwh = 0` und `reached=True` im Eingangsdatum. `PlanVehicle.reached` (Default `False`, alte Pläne laden). API `vehicles[].reached: bool`.

Entscheidung: Nur Health des core entscheidet „erreicht“ (Hysterese `TARGET_RESTART_PP` im core); die app rechnet keine eigene Hysterese. Erwartete Autos (`expected_vehicles`) bleiben unverändert.

- [ ] **Step 1: Failing tests**
	- `test_vehicles.py::test_reached_car_has_no_need`: Health `planned_health(vehicle_health("tesla", soc_pct=49.0, planned={…, "state": "reached", "target_soc_pct": 50.0}))` → ein Auto mit `need_kwh == 0`, `horizon_need_kwh == 0`, `grid_allowed_kwh == 0`, `reached is True`, `soc_pct == 49.0`, `target_soc_pct == 50.0`. Dasselbe mit `"state": "plan"` → Bedarf > 0, `reached is False`. (Helfer `vehicle_health`/`planned_health` aus `tests/plan_helpers.py`; fehlt dort ein Parameter für `state`, ihn ergänzen.)
	- `test_builder.py::test_reached_car_is_summarised_as_reached`: Auto mit `reached=True` und Bedarf 0 → `PlanVehicle.reached is True`, `reachable is True`, `finish_at is None`, kein `AUTO_WARTET_AUF_PV` und kein `ZIEL_SOC_ABFAHRT` in den Slots.
	- `test_store.py`/`test_plan.py`: ein gespeicherter Plan ohne `reached` lädt mit `False`.
	- `test_routes.py`: `vehicle_json` enthält `"reached"`.

- [ ] **Step 2:** `uv run pytest tests/unit/app/planning/test_vehicles.py tests/unit/app/planning/test_builder.py tests/unit/app/planning/test_routes.py -q` → FAIL.

- [ ] **Step 3: Implementieren.** In `planned_vehicles`: `reached = planned.get("state") == "reached"` an `_car_input`. `grid_allowed` verteilt bei Bedarf 0 nichts an das Auto (prüfen, dass es dann für Autos niedrigerer Priorität bleibt).

- [ ] **Step 4:** `uv run pytest tests/unit/app/planning tests/unit/shared -q` und `uv run pytest tests/integration -q -k "plan or ui_contract or scenario"` grün (Vertrag in Task 6).

- [ ] **Step 5: Commit** `fix(app): Planer plant kein Restladen für ein Auto, das der core als erreicht hält`

---

### Task 6: Start-SOC im Plan und Anzeige im Zeitplan (T2, T3, R1 im UI)

**Files:**
- Modify: `src/bindaems/shared/plan.py` (`Plan.soc_start_pct: Finite | None = None`), `src/bindaems/app/planning/builder.py` (setzt `inputs.battery.soc_pct`), `src/bindaems/app/planning/routes.py` (`plan_json` mit `"soc_start_pct"`)
- Modify: `ui/src/lib/api/schemas.ts` (`PlanSchema.soc_start_pct: v.optional(v.nullable(v.number()), null)`, `PlanVehicleSchema.reached: v.optional(v.boolean(), false)`), Vertragsdateien `ui/src/lib/api/contract/plan.json` (neu geschrieben)
- Modify: `ui/src/lib/plan/model.ts` (`planBlocks`, neue `socRange`), `ui/src/lib/plan/PlanBlocks.svelte`, `ui/src/routes/zeitplan/+page.svelte`, `ui/src/lib/charging/view.ts` (`plannedText`)
- Test: `tests/unit/app/planning/test_builder.py`, `tests/unit/app/planning/test_routes.py`, `ui/src/lib/plan/model.test.ts`, `ui/src/lib/plan/PlanBlocks.test.ts`, `ui/src/lib/charging/view.test.ts`

**Interfaces:**
- Consumes: `PlanVehicle.reached` (Task 5).
- Produces: `planBlocks(slots: PlanSlot[], vehicles: PlanVehicle[] = [], socStartPct: number | null = null): PlanBlock[]` — `socFrom` des ersten Blocks ist `socStartPct ?? slots[0].soc_pct`. `export function socRange(from: number, to: number): string` in `model.ts`: `'19 → 20'` ganzzahlig; sind beide gerundet gleich und unterscheiden sich um mindestens 0,05, mit einer Nachkommastelle (`'19,2 → 19,9'`); sonst ganzzahlig (`'17 → 17'`).

- [ ] **Step 1: Failing tests**
	- `test_builder.py`: `build(...)` setzt `plan.soc_start_pct == inputs.battery.soc_pct`; alter Plan ohne Feld lädt mit `None`.
	- `test_routes.py`: `plan_json` enthält `"soc_start_pct"`.
	- `model.test.ts`: `planBlocks(slots, [], 17.4)[0].socFrom === 17.4`; ohne dritten Parameter wie bisher; `socRange(19.2, 19.9) === '19,2 → 19,9'`, `socRange(19, 20.4) === '19 → 20'`, `socRange(17, 17) === '17 → 17'`, `socRange(17.01, 17.03) === '17 → 17'`.
	- `PlanBlocks.test.ts`: Zeile zeigt `SOC 19,2 → 19,9 %`.
	- `view.test.ts` (`plannedLine`): Plan-Auto mit `reached: true`, `soc_start_pct: 49`, `target_soc_pct: 50`, `finish_at: null` → Text „Ziel erreicht (49 %).“

- [ ] **Step 2:** pytest- und Vitest-Dateien → FAIL.

- [ ] **Step 3: Implementieren.** `plannedText`: Bedingung für „Ziel erreicht“ aus dem Plan wird `!car.soc_assumed && (car.reached || car.soc_start_pct >= car.target_soc_pct)`. Zeitplan-Seite übergibt `current?.soc_start_pct ?? null`. Vertrag neu schreiben (`UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py`), Diff der JSON-Dateien prüfen: nur die neuen Felder.

- [ ] **Step 4:** `uv run pytest tests/unit/app/planning tests/integration/test_ui_contract.py -q`, UI `pnpm check && pnpm lint && pnpm test` grün.

- [ ] **Step 5: Commit** `fix(ui): Zeitplan zeigt den Start-SOC und kleine SOC-Änderungen, Plan-Auto „Ziel erreicht“`

---

### Task 7: Doku, E2E und Screenshots

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absatz „Stand Plan Diagramme und Plantexte (29.10.2026)“ hinter dem Abschnitt der Erklärungen: T4–T6, R1, die neuen Felder), `docs/betrieb.md` (Zeitplan: Akku-Diagramm mit Legende statt Beschriftung; „Warum?“), `docs/uebergabe.md` (Stand, Offen an der Anlage: kommt mit dem nächsten Sammel-Pull, nur app/UI)
- Test: `ui/e2e/*.spec.ts` (nur anpassen, wo sie alte Texte prüfen)

- [ ] **Step 1:** `cd ui && pnpm exec playwright test` (lokal mit `launchOptions.executablePath: '/opt/pw-browsers/chromium'`), Fehler nur dort beheben, wo E2E alte Texte/Beschriftungen erwarten.
- [ ] **Step 2:** Screenshots der Übersicht und des Zeitplans in 390 px Breite (dunkel) aus der Demo-Welt nach `/mnt/project-files/plaene/diagramme-plantexte/`.
- [ ] **Step 3:** Doku nachführen; `uv run pytest tests/unit/test_docs.py -q` grün.
- [ ] **Step 4:** volle Prüfkette (pytest mit Abdeckung in Gruppen und `coverage combine`, ≥ 75 % gesamt, ≥ 90 % safety/control/actuators).
- [ ] **Step 5: Commit** `docs: Diagramme und Plantexte in Spec, Betrieb und Übergabe`

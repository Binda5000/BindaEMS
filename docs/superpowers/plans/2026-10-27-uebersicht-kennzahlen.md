# Übersicht neu: Kennzahlen-Leiste (Variante B) – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Übersicht (`/`) wird aufgeräumt: oben eine Leiste mit Kennzahlen, darin der Hausakku doppelt breit und gut sichtbar; Hinweise schrumpfen auf eine Zeile, Energiefluss und „Verbraucher“ werden zu zwei Balken „Woher – Wohin“.

**Architecture:** Nur UI (`ui/`); core, app, `shared`, HA, Datenbank und API bleiben unverändert (keine Migration, keine neue Abfrage am Server). Die Seite nutzt nur vorhandene Endpunkte: `/api/system`, `/api/limits`, `/api/consumers`, `/api/prices/now`, `/api/prices`, `/api/forecast/pv`, `/api/forecast/load`, `/api/decisions/latest` wie bisher, neu `/api/plan`, `/api/stats?period=day` und `/api/history` (alle ab „Lesen“). Die Rechenlogik steht in reinen TS-Modulen unter `ui/src/lib/dashboard/` (mit Vitest), die Svelte-Komponenten zeigen nur an.

**Tech Stack:** SvelteKit 5, Valibot, ECharts, Vitest + Testing Library, Playwright. Keine neuen Abhängigkeiten.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Abschnitt 13 (Seiten, Zeile „Dashboard“) und „Warum?“ (Plan 2d). Entwurf: Variante B der Entwürfe vom 27.10.2026 (Canvas „BindaEMS Übersicht – Entwürfe“), vom Betreiber gewählt. Task 6 ergänzt „Stand Plan Übersicht B“.

## Einordnung

- **Anlass:** Der Betreiber findet die Übersicht unübersichtlich (sieben Karten, fünf Hinweise als lange Liste, „Warum?“ mit vier langen Absätzen) und möchte den Ladestand des Hausakkus prominenter. Von drei Entwürfen wählte er am 27.10.2026 Variante B „Kennzahlen-Leiste“.
- **Was wegfällt:** die Karten „Energiefluss“ (SVG), „Ladestände“, „Strompreis“, „Hinweise“ (Liste) und „Verbraucher“ in ihrer bisherigen Form. Ihre Inhalte stehen in den neuen Teilen (Tabelle unten). Die Komponenten `EnergyFlow`, `SocList`, `ConsumerSummary`, `PriceNow` und `Notices` werden nur auf der Übersicht benutzt und entfallen samt Tests; `flowBranches` entfällt, `flowConsumers` und `wallboxLabels` bleiben.
- **Ausrollen:** nur `ems-app` (das UI liegt im Image der app), keine Migration. Wann, entscheidest du beim Abschluss.

| bisher | neu |
|---|---|
| Hinweise (Karte, Liste) | Hinweis-Leiste ganz oben, aufklappbar |
| Ladestände: Akku | Akku-Kachel (doppelt breit) |
| Ladestände: Autos | Karte „Autos“ mit Ring je Auto |
| Strompreis | Preis-Kachel (jetzt, nächste 3 h als Mini-Balken, günstigste Viertelstunde) |
| Energiefluss + Verbraucher | Karte „Woher kommt der Strom – wohin geht er?“ |
| Diagramm „Preise und PV heute/morgen“ | dasselbe Diagramm plus Akku-SOC (gemessen heute) und Akku-Plan |
| Warum? (Absätze) | „Warum?“ kompakt, eine Zeile je Aktor |

## Vorgeschlagene Entscheidungen (zur Freigabe)

1. **Reihenfolge**, auf Desktop wie Handy: Hinweis-Leiste → Kachelreihe (Akku, PV, Haus, Netz, Preis) → „Woher – Wohin“ und „Autos“ (Desktop nebeneinander) → Diagramm → „Warum?“.
2. **Hinweis-Leiste:** ohne Hinweise unsichtbar. Sonst eine Zeile „{n} Hinweis(e) · {die ersten zwei Texte, mit „ · “ getrennt}“ und der Knopf „Anzeigen“; die Farbe richtet sich nach dem schwersten Hinweis (Fehler rot, Warnung gelb, Info grau). „Anzeigen“ klappt die volle Liste darunter auf (wie heute die Karte), „Ausblenden“ wieder zu. Der Zustand gilt nur bis zum Neuladen.
3. **Akku-Kachel:** großer SOC in % und ein Batteriesymbol mit Füllstand und Strich bei der Reserve. Daneben „≈ {kWh} von {nutzbar} kWh“ (SOC × `usable_kwh`), die Leistung „lädt 1,00 kW“ / „entlädt 0,80 kW“ / „ruht“ (Schwelle 20 W wie der Energiefluss), „Reserve 20 %“ (`reserve_soc_pct`), „Netzladen erlaubt“ / „Netzladen aus“ (`grid_charge_allowed` aus `/api/plan`) und eine Zeile zum Plan: „Plan: steigt bis 90 % um 15:00“, „Plan: sinkt bis 25 % um 06:00“ oder „Plan: hält 55 %“; ohne gültigen Plan fehlt die Zeile. Fehlt der SOC, steht „–“ und „kein Messwert“, ist er veraltet, „veraltet“ (gedämpft, wie heute). Liegt der SOC an oder unter der Reserve, wird die Füllung gelb.
4. **PV, Haus, Netz:** je eine Kachel mit der Leistung jetzt, einer Mini-Linie der letzten 2 h (aus `/api/history`, ohne Achsen) und dem Tageswert aus `/api/stats?period=day`: PV „heute {pv_kwh}“, Haus „heute {house_kwh}“, Netz „heute ↓ {import_kwh} · ↑ {export_kwh}“. Die Netz-Kachel heißt je nach Richtung „Netz · Bezug“ oder „Netz · Einspeisung“, „Netz“ wenn ruhend oder unbekannt.
5. **Preis-Kachel:** Bezugspreis jetzt, „Einspeisung {ct}“, die nächsten 3 h als Mini-Balken in drei Stufen (wie heute, ohne Antippen) und „am günstigsten um {HH:MM}“ bzw. „jetzt am günstigsten“. Die Marken „Ersatzquelle“ und „Tarif unvollständig“ bleiben als kleine Marken. Den Preis je Viertelstunde zeigt weiterhin das Diagramm darunter (Tooltip).
6. **„Woher – Wohin“:** zwei waagrechte Balken. Quellen: PV, Netzbezug, Akku entlädt. Verbrauch: Haus, Akku lädt, je Wallbox, Einspeisung. Jeder Balken ist auf seine eigene Summe skaliert (Messwerte gehen nicht genau auf), Beschriftung im Segment nur ab 15 % Breite, darunter immer die volle Liste mit Werten. Darunter „Im Haus: Küche 230 W · Obergeschoss 600 W · Sonstiges 342 W“ (die bisherigen Verbraucher der ersten Ebene, höchstens 6 wie im Fluss) und der Verweis „Verbraucher im Detail“ (`/verbraucher`).
7. **Autos:** je Auto aus `config.yaml` eine Kachel mit SOC-Ring, Name und Stand: „{Wallbox} · lädt {kW}“, „{Wallbox} · angesteckt“ oder „nicht angesteckt“, darunter der Lademodus („PV-Überschuss“ usw. wie auf „Laden“) und „SOC veraltet“ bzw. „kein SOC“. Steht ein Gast an einer Wallbox, kommt eine Kachel „Gast · {Wallbox}“ ohne Ring dazu. Verweis „Zum Laden“ (`/laden`).
8. **Diagramm** heißt „Preise, PV und Akku heute/morgen“ und bekommt zwei Reihen auf einer dritten, unsichtbaren Achse 0–100 %: „Akku-SOC“ (gemessen, heute bis jetzt, durchgezogen) und „Akku-Plan“ (gestrichelt, nur bei gültigem Plan). Der Tooltip zeigt „55 %“.
9. **„Warum?“** bleibt (Spec 13): je Aktor eine Zeile „**Name** · Text“ mit dem Alter rechts. Das Alter heißt künftig überall „vor 45 s“, „vor 12 min“, „vor 3 h“, „vor 2 Tagen“ (ab 120 s Minuten, ab 120 min Stunden, ab 48 h Tage); das betrifft auch die Seite „System“.
10. **Abfragen:** Plan alle 60 s, Verlauf (PV, Haus, Netz, Akku-SOC von heute 0 Uhr bis jetzt) alle 120 s, Tageswerte alle 300 s; die übrigen wie bisher. Scheitert eine dieser Abfragen, fehlt nur ihr Teil (Mini-Linie, Tageswert, Planzeile), die Kachel bleibt.

## Global Constraints

- Deutsch, Anrede „du“ in der Doku; Commits im Stil `feat(ui): …`, `refactor(ui): …`, `test(ui): …`, `docs: …`; keine Modellbezeichnungen im Repo.
- Nur `ui/` und Doku ändern; core, app, `shared`, `cerbo-watchdog`, Migrationen und API-Antworten bleiben unverändert. Kein Aktor ist live; nichts an der Anlage schalten.
- Vor jedem Commit: `cd ui && pnpm check && pnpm lint && pnpm test`; vor dem Abschluss zusätzlich `pnpm build && pnpm e2e` (Playwright mit `/opt/pw-browsers/chromium`, siehe Übergabe) und die Python-Kette einmal ganz: `uv run ruff format --check . && uv run ruff check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports && uv run pytest -q --cov --cov-fail-under=75` (wegen `tests/unit/test_docs.py`). In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Zahlen und Einheiten nur über `$lib/format` (`formatPower`, `formatEnergy`, `formatSoc`, `formatCt`, `formatTime`); fehlende Werte als „–“, nie als 0.
- Farben nur über die Variablen aus `app.css` (`--battery`, `--pv`, `--house`, `--grid`, `--wallbox`, `--warn`, `--error` …); hell und dunkel müssen lesbar sein.
- Bedienelemente mindestens 44 × 44 px; bei 360 px keine waagrechte Scrollleiste (`ui/tests/e2e/pages.spec.ts` prüft `/`).
- Svelte-Komponenten bekommen fertige Werte aus den Modulen; Logik mit Verzweigungen gehört in die `.ts`-Module und deren Tests.
- `{#each}` über Texte vom Server: eindeutigen Schlüssel oder Index nehmen.

## Review Focus

1. **Fehlende oder veraltete Werte:** SOC `invalid`, Derived-Werte `null`, keine Fahrzeuge in `limits`, Live-Verbindung veraltet – jede Kachel zeigt „–“ bzw. „veraltet“ statt 0 % oder 0 W (Task 1 `batteryView ohne SOC`, Task 2 `balance ohne Werte`, Task 3 Komponententests).
2. **Plan, der nicht mehr gilt oder vor jetzt endet:** `plan.valid === false`, `plan === null` oder keine Slots nach jetzt → keine Planzeile, keine Plan-Reihe im Diagramm (Task 1 `planOutlook ohne künftige Slots`, Task 4).
3. **Schmale Bildschirme:** bei 360 px bricht die Kachelreihe auf eine Spalte um, die Akku-Kachel nimmt die volle Breite, ohne eine zweite Spalte zu erzwingen (Task 5, E2E 360 px).
4. **Einspeisung und entladender Akku gleichzeitig:** Netz negativ und Akku negativ – Akku steht bei den Quellen, Einspeisung beim Verbrauch, keine negativen Segmentbreiten (Task 2 `balance mit Einspeisung und Entladen`).
5. **Gast an der Wallbox und unbekannter Wallbox-Schlüssel:** Gast-Kachel ohne Ring; eine Wallbox ohne Eintrag in `limits` erscheint mit ihrem Schlüssel (Task 2 `vehicleCards mit Gast`).

---

### Task 1: Akku-Modell (`battery.ts`)

**Files:**
- Create: `ui/src/lib/dashboard/battery.ts`
- Test: `ui/src/lib/dashboard/battery.test.ts`

**Interfaces:**
- Consumes: `CoreState`, `Limits`, `PlanResponse`, `PlanSlot` aus `$lib/api/schemas`; `IDLE_W` aus `./flow`.
- Produces:
  ```ts
  export type BatteryDirection = 'charge' | 'discharge' | 'idle' | 'unknown';
  export interface PlanOutlook { trend: 'up' | 'down' | 'hold'; socPct: number; at: string /* ISO, Ende des Slots */ }
  export interface BatteryView {
    pct: number | null; quality: 'ok' | 'stale' | 'missing';
    kwh: number | null; usableKwh: number | null; reservePct: number | null;
    powerW: number | null; direction: BatteryDirection;
    atOrBelowReserve: boolean; gridChargeAllowed: boolean | null; outlook: PlanOutlook | null;
  }
  export function planOutlook(slots: PlanSlot[], nowMs: number, tolerancePct?: number): PlanOutlook | null;
  export function batteryView(state: CoreState | null | undefined, limits: Limits | null | undefined, plan: PlanResponse | null | undefined, nowMs: number): BatteryView;
  export function outlookText(outlook: PlanOutlook): string; // „steigt bis 90 % um 15:00“ …
  ```

- [ ] **Step 1: Tests schreiben** (`battery.test.ts`, Vitest, Fixtures minimal von Hand):
  - `batteryView mit SOC 55 und 30 kWh` → `pct 55`, `quality 'ok'`, `kwh 16.5`, `usableKwh 30`, `reservePct 20`, `atOrBelowReserve false`.
  - `batteryView ohne SOC` (Signal fehlt bzw. `q: 'invalid'`) → `pct null`, `quality 'missing'`, `kwh null`; veraltetes Signal → `quality 'stale'`, `pct` gesetzt.
  - Richtung: `battery_w` 1000 → `'charge'`, −800 → `'discharge'`, 15 → `'idle'`, `null` → `'unknown'`; `powerW` ist der Betrag.
  - `atOrBelowReserve` bei SOC 20 und Reserve 20 → `true`.
  - `gridChargeAllowed` aus `plan.grid_charge_allowed`; ohne Antwort `null`.
  - `planOutlook`: Slots ab jetzt 55, 62, 75, 90, 90, 86 → `{ trend: 'up', socPct: 90, at: Ende des Slots mit 90 }` (erster Slot, der das Maximum des steigenden Laufs erreicht); fallend 55, 50, 40, 25, 26 → `down`, 25; alle innerhalb ±1 % → `hold` mit dem SOC des ersten Slots; `planOutlook ohne künftige Slots` → `null`. Vergangene Slots (Ende ≤ jetzt) zählen nicht. Toleranz Standard 1 %: Schwankungen ≤ 1 % gegen die Richtung beenden den Lauf nicht.
  - `batteryView` liefert `outlook null`, wenn `plan.plan` `null` oder `plan.plan.valid` falsch ist.
  - `outlookText`: „steigt bis 90 % um 15:00“, „sinkt bis 25 % um 06:00“, „hält 55 %“ (Zeit in Wiener Zeit über `formatTime`).
- [ ] **Step 2:** `cd ui && pnpm test -- battery` → FAIL (Modul fehlt).
- [ ] **Step 3:** `battery.ts` umsetzen. Der Lauf beginnt beim ersten Slot nach jetzt; Richtung = Vorzeichen der ersten Änderung über der Toleranz; er endet vor dem ersten Slot, der um mehr als die Toleranz gegen die Richtung geht. `at` = Start + 15 min des Slots mit dem Extremwert.
- [ ] **Step 4:** `pnpm test -- battery` → PASS; `pnpm check && pnpm lint`.
- [ ] **Step 5:** Commit `feat(ui): Akku-Modell für die Übersicht mit Ausblick aus dem Plan`.

### Task 2: Modelle für Kacheln, „Woher – Wohin“ und Autos

**Files:**
- Create: `ui/src/lib/dashboard/tiles.ts`, `ui/src/lib/dashboard/balance.ts`, `ui/src/lib/dashboard/vehicles.ts`
- Modify: `ui/src/lib/dashboard/flow.ts` (entfernt `flowBranches`, `FlowBranch`, `FlowDirection`; `IDLE_W`, `wallboxLabels`, `flowConsumers` bleiben), `ui/src/lib/dashboard/prices.ts` (neu `cheapestNext`)
- Test: `tiles.test.ts`, `balance.test.ts`, `vehicles.test.ts`, `prices.test.ts`, `flow.test.ts` (Tests von `flowBranches` entfallen)

**Interfaces:**
- Consumes: `Derived`, `CoreState`, `Limits`, `VehicleHealth`, `WallboxHealth`, `HistoryResponse`, `Stats` aus `$lib/api/schemas`; `modeLabel` aus `$lib/charging/view`; `PriceSummary` aus `./prices`.
- Produces:
  ```ts
  // tiles.ts
  export const SPARK_WINDOW_MS = 2 * 60 * 60 * 1000;
  export const HISTORY_SERIES = ['pv', 'house', 'grid', 'soc.battery'] as const;
  export function sparkPoints(history: HistoryResponse | null | undefined, series: string, nowMs: number): [number, number][]; // nur die letzten 2 h
  export function gridLabel(gridW: number | null | undefined): 'Netz · Bezug' | 'Netz · Einspeisung' | 'Netz';
  export interface DayEnergy { pvKwh: number | null; houseKwh: number | null; importKwh: number | null; exportKwh: number | null }
  export function dayEnergy(stats: Stats | null | undefined): DayEnergy;
  // prices.ts
  export function cheapestNext(summary: PriceSummary): { start: string; ct: number; now: boolean } | null;
  // balance.ts
  export type BalanceKind = 'pv' | 'grid' | 'battery' | 'house' | 'wallbox' | 'export';
  export interface BalanceSegment { id: string; label: string; kind: BalanceKind; powerW: number; share: number /* 0–1 */ }
  export interface Balance { sources: BalanceSegment[]; sinks: BalanceSegment[]; sourcesW: number | null; sinksW: number | null }
  export function balance(derived: Derived | null | undefined, wallboxes: Record<string, string>): Balance;
  // vehicles.ts
  export interface VehicleCard { id: string; name: string; socPct: number | null; socQuality: 'ok' | 'stale' | 'missing'; guest: boolean; status: string; mode: string | null }
  export function vehicleCards(state: CoreState | null | undefined, limits: Limits | null | undefined, health: { vehicles: VehicleHealth[]; wallboxes: WallboxHealth[] } | null | undefined, wallboxes: Record<string, string>): VehicleCard[];
  ```

- [ ] **Step 1: Tests schreiben:**
  - `sparkPoints` liefert nur Punkte der letzten 2 h, sortiert; fehlende Reihe → `[]`.
  - `gridLabel(380)` „Netz · Bezug“, `gridLabel(-900)` „Netz · Einspeisung“, `gridLabel(10)` und `gridLabel(null)` „Netz“.
  - `dayEnergy(null)` → alle `null`; mit Stats die vier Felder aus `stats.energy`.
  - `cheapestNext`: niedrigster Preis der nächsten 3 h, bei Gleichstand der früheste; `now: true`, wenn es der erste Slot ist; leere Liste → `null`.
  - `balance` mit PV 3230, Netz 380, Akku +1000, Haus 1230, Wallbox garage 1380, evcs 0 → Quellen PV und Netz (Summe 3610, Anteile 0,895/0,105), Verbrauch Haus, Akku, Garage (Wallbox mit ≤ 20 W fehlt), keine Einspeisung.
  - `balance mit Einspeisung und Entladen` (Netz −900, Akku −500, PV 0, Haus 400): Quellen Akku, Verbrauch Haus und Einspeisung; alle `share` ≥ 0 und je Balken Summe 1.
  - `balance ohne Werte` (`derived` `null`) → leere Listen, Summen `null`.
  - `vehicleCards`: Tesla mit Wallbox „garage“, `plugged`/`charging` true, 1380 W → `status` „Garage · lädt 1,38 kW“, `mode` „Min+PV“ (aus `modeLabel`); e-Golf ohne Wallbox → „nicht angesteckt“; angesteckt ohne Laden → „{Wallbox} · angesteckt“; veralteter SOC → `socQuality 'stale'`.
  - `vehicleCards mit Gast`: Wallbox `occupant: 'guest'` → zusätzliche Karte `guest: true`, `name` „Gast“, `status` „{Wallbox} · lädt …“, `socPct null`; Wallbox-Schlüssel ohne Namen erscheint mit dem Schlüssel.
- [ ] **Step 2:** `pnpm test -- tiles balance vehicles prices flow` → FAIL.
- [ ] **Step 3:** Module umsetzen; `flowBranches` und seine Typen aus `flow.ts` entfernen (nur `EnergyFlow` nutzt sie; die Komponente fällt in Task 5 weg – bis dahin bleibt `flowBranches` drin, wenn `pnpm check` sonst scheitert, und Task 5 entfernt es).
- [ ] **Step 4:** `pnpm test && pnpm check && pnpm lint` → PASS.
- [ ] **Step 5:** Commit `feat(ui): Modelle für Kacheln, Woher–Wohin und Autos der Übersicht`.

### Task 3: Komponenten der Übersicht

**Files:**
- Create in `ui/src/lib/dashboard/`: `NoticeBar.svelte`, `BatteryTile.svelte`, `KpiTile.svelte`, `Sparkline.svelte`, `PriceTile.svelte`, `BalanceCard.svelte`, `VehicleTiles.svelte`
- Modify: `ui/src/lib/decisions/WhyCard.svelte` (kompakt), `ui/src/lib/system/view.ts` (`ageText`)
- Test: je Komponente `*.test.ts` daneben; `ui/src/lib/system/view.test.ts`, vorhandene Tests von `WhyCard`/System-Seite anpassen

**Interfaces:**
- Consumes: Task 1 (`BatteryView`, `outlookText`), Task 2 (`Balance`, `VehicleCard`, `DayEnergy`, `gridLabel`, `sparkPoints`, `cheapestNext`), `Notice` aus `./notices`, `priceSummary` aus `./prices`.
- Produces (Props):
  - `NoticeBar { notices: Notice[] }` – nichts bei leerer Liste; `<button aria-expanded>` „Anzeigen“/„Ausblenden“; Klasse nach schwerstem Level.
  - `BatteryTile { view: BatteryView; stale: boolean }` – Batteriesymbol `role="img"` mit `aria-label` „Hausakku 55 %, Reserve 20 %“; `data-testid="battery-soc"` am großen Wert.
  - `KpiTile { label: string; value: string; detail?: string; color: string /* CSS-Variable */; points?: [number, number][]; stale?: boolean }` mit `data-testid` vom Aufrufer (`kpi-pv`, `kpi-house`, `kpi-grid`).
  - `Sparkline { points: [number, number][]; color: string }` – SVG `aria-hidden`, Polyline auf 100 × 24 skaliert; < 2 Punkte → nichts.
  - `PriceTile { data: PricesNow | undefined; error: ApiError | null }` – `data-testid="price-now"` bleibt am Preis.
  - `BalanceCard { balance: Balance; consumers: FlowConsumer[]; stale: boolean; error: ApiError | null }` – Titel „Woher kommt der Strom – wohin geht er?“.
  - `VehicleTiles { cards: VehicleCard[]; stale: boolean; error: ApiError | null }` – Titel „Autos“; ohne Autos „Keine Autos eingetragen.“
  - `WhyCard` behält seine Props.
  - `ageText(ageS: number | null): string` – Stufen aus Entscheidung 9.

- [ ] **Step 1: Tests schreiben** (Testing Library):
  - `NoticeBar`: leere Liste rendert nichts; drei Hinweise zeigen „3 Hinweise“ und die ersten zwei Texte; Klick auf „Anzeigen“ zeigt alle drei, `aria-expanded="true"`; ein Hinweis → „1 Hinweis“.
  - `BatteryTile`: zeigt „55 %“, „lädt 1,00 kW“, „Reserve 20 %“, „Netzladen aus“, „Plan: steigt bis 90 % um 15:00“; ohne SOC „–“ und „kein Messwert“; `outlook null` → kein Text „Plan:“.
  - `PriceTile`: Preis, „Einspeisung …“, „am günstigsten um …“; Marke „Tarif unvollständig“ bei `missing`; Fehler als Notice.
  - `BalanceCard`: Segmentbeschriftung nur ab 15 %, die Liste nennt alle Segmente mit Leistung; „Im Haus: …“; Verweis auf `/verbraucher`.
  - `VehicleTiles`: Ring mit „70“, „SOC veraltet“, Gast-Kachel ohne Ring.
  - `ageText`: 45 → „vor 45 s“, 720 → „vor 12 min“, 7200 → „vor 2 h“, 10 800 → „vor 3 h“, 172 800 → „vor 2 Tagen“, `null` → „–“.
  - `WhyCard`: Name als Verweis auf `/entscheidungen?actuator=…` und Text in derselben Zeile (`li` enthält beide).
- [ ] **Step 2:** `pnpm test -- dashboard decisions system` → FAIL.
- [ ] **Step 3:** Komponenten umsetzen; Kacheln nutzen `Card`-Optik (Variablen `--surface`, `--border`, `--radius`). Die Akku-Kachel bekommt einen 2 px Rahmen in `--battery`. Füllung des Batteriesymbols `--battery`, bei `atOrBelowReserve` `--warn`; gedämpft (`opacity: 0.6`) bei `stale` oder `quality 'stale'`.
- [ ] **Step 4:** `pnpm test && pnpm check && pnpm lint` → PASS.
- [ ] **Step 5:** Commit `feat(ui): Kacheln, Hinweis-Leiste und Woher–Wohin für die Übersicht`.

### Task 4: Diagramm mit Akku-SOC und Akku-Plan

**Files:**
- Modify: `ui/src/lib/charts/options.ts` (`priceForecastOption`)
- Test: `ui/src/lib/charts/options.test.ts`

**Interfaces:**
- Consumes: `Plan` aus `$lib/api/schemas`.
- Produces: `priceForecastOption(prices, forecast, load, nowIso, palette, battery?: { measured: [number, number][]; plan: Plan | null })` – neue Reihen `id: 'soc'` (Name „Akku-SOC“) und `id: 'soc_plan'` („Akku-Plan“, gestrichelt) auf `yAxisIndex: 2` (`min 0`, `max 100`, `show: false`), Farbe `palette.text` (das Blau `--battery` trägt im Diagramm schon der Bezugspreis), „Akku-SOC“ durchgezogen 2 px, „Akku-Plan“ gestrichelt. Plan-Punkte wie auf „Zeitplan“: `[start + 15 min, soc_pct]`, nur Slots ab jetzt und nur bei `plan.valid`. Tooltip: `formatSoc`.

- [ ] **Step 1: Tests:** ohne `battery` bleiben Reihen und Achsen wie bisher (bestehende Tests grün); mit Messwerten und gültigem Plan gibt es beide Reihen auf Achse 2; `valid: false` → keine `soc_plan`; Legende enthält „Akku-SOC“ und „Akku-Plan“.
- [ ] **Step 2:** `pnpm test -- options` → FAIL.
- [ ] **Step 3:** umsetzen; die Palette bleibt unverändert.
- [ ] **Step 4:** `pnpm test && pnpm check && pnpm lint` → PASS.
- [ ] **Step 5:** Commit `feat(ui): Diagramm der Übersicht zeigt Akku-SOC und Akku-Plan`.

### Task 5: Seite zusammensetzen, alte Teile entfernen, E2E

**Files:**
- Modify: `ui/src/routes/+page.svelte`
- Delete: `ui/src/lib/dashboard/EnergyFlow.svelte`, `SocList.svelte`, `ConsumerSummary.svelte`, `PriceNow.svelte`, `Notices.svelte` und ihre `*.test.ts`; `socs.ts`/`socs.test.ts`, falls nichts mehr sie nutzt; `flowBranches` in `flow.ts`, falls noch da
- Modify: `ui/tests/e2e/login.spec.ts`, `ui/tests/e2e/pages.spec.ts`
- Test: `ui/src/routes/layout-dashboard.test.ts` (bestehender Seitentest der Übersicht: Mocks für `plan`, `history`, `stats` ergänzen, Erwartungen auf die neuen Teile umstellen; Fixtures in `$lib/testing/fixtures`)

**Interfaces:**
- Consumes: alles aus Task 1–4.
- Produces: die Seite. Neue Ressourcen: `plan` (`api.plan`, 60 s), `history` (`api.history([...HISTORY_SERIES], { from: localDayRange(todayVienna()).from, to: jetzt }, o)`, 120 s), `day` (`api.stats('day', undefined, o)`, 300 s).

- [ ] **Step 1: Seitentest und E2E anpassen (scheitern zuerst):** `layout-dashboard.test.ts`: die Seite zeigt Akku-Kachel, die Kacheln `kpi-pv`/`kpi-house`/`kpi-grid`, „Woher kommt der Strom – wohin geht er?“, „Autos“ und „Warum?“; scheitert `api.plan`, fehlt nur die Planzeile, scheitert `api.history`, fehlen nur die Mini-Linien. E2E: `login.spec.ts` prüft statt `flow-pv` `getByTestId('kpi-pv')` enthält „kW“ und `getByTestId('battery-soc')` enthält „%“. `pages.spec.ts`: der Diagrammtest sucht die Karte „Preise, PV und Akku heute/morgen“. Neuer Test „Übersicht: Hinweise aufklappen“: Leiste sichtbar, Klick auf „Anzeigen“ zeigt „MQTT noch nicht verbunden“.
- [ ] **Step 2:** `+page.svelte` neu aufbauen: `NoticeBar`; Kachelraster `grid-template-columns: repeat(auto-fit, minmax(min(100%, 10rem), 1fr))`, die Akku-Kachel `grid-column: 1 / -1` unter 40rem und `span 2` ab 40rem; darunter zwei Spalten ab 60rem (`BalanceCard`, `VehicleTiles`), dann Diagramm (breit), dann `WhyCard` (breit). Die alten Ressourcen bleiben (system, consumers, limits, pricesNow, decisions, prices, forecast, load).
- [ ] **Step 3:** alte Komponenten und ungenutzte Exporte löschen; `pnpm check` meldet keine toten Importe.
- [ ] **Step 4:** `pnpm check && pnpm lint && pnpm test && pnpm build && pnpm e2e` → PASS (alle Seiten bei 360 px ohne waagrechtes Scrollen).
- [ ] **Step 5:** Bildschirmfotos der Demo bei 1440 px hell und dunkel und bei 390 px in den Ledger legen (nicht ins Repo) und mit dem Entwurf B vergleichen.
- [ ] **Step 6:** Commit `feat(ui): Übersicht als Kennzahlen-Leiste mit prominentem Hausakku`.

### Task 6: Doku

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absatz „Stand Plan Übersicht B“ nach der Seitentabelle in Abschnitt 13), `docs/uebergabe.md` (UI-Abschnitt, Stand, „Offen an der Anlage“: nur `ems-app` ziehen), `README.md` nur falls es die Karten der Übersicht beschreibt
- Test: `uv run pytest tests/unit/test_docs.py -q`

- [ ] **Step 1:** „Stand Plan Übersicht B“: die Dashboard-Zeile gilt mit diesen Änderungen: Energiefluss als Balken „Woher – Wohin“ statt SVG-Diagramm; Akku-Kachel mit kWh, Reserve, Netzladen und Planausblick; Kacheln PV, Haus, Netz mit 2-h-Linie und Tageswert; Preis jetzt und nächste 3 h als Kachel; Hinweise als aufklappbare Leiste; Diagramm mit Akku-SOC und Akku-Plan; „Warum?“ kompakt; Alter in s, min, h, Tagen.
- [ ] **Step 2:** `docs/uebergabe.md` nachführen (Stand, Plan, Tests, Ausrollen nur `ems-app`).
- [ ] **Step 3:** `uv run pytest tests/unit/test_docs.py -q` → PASS; volle Python-Kette einmal.
- [ ] **Step 4:** Commit `docs: Stand Plan Übersicht B in Spec und Übergabe`.

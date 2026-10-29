# Verständlichkeit und Design A–H Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die acht freigegebenen Verbesserungen A–H aus der Handy-Durchsicht vom 29.10.2026 so umsetzen,
wie sie die Entwürfe zeigen.

**Architecture:** Fast nur UI (SvelteKit, `ui/src`). Breite Tabellen bekommen eine gemeinsame
Karten-Darstellung am Handy (CSS in `app.css` plus `data-label` je Zelle). Diagramm-Legenden zeichnet
`EChart.svelte` selbst als HTML-Chips unter dem Diagramm; die ECharts-Legende bleibt unsichtbar und
schaltet nur noch Reihen. Eine Zeile Python: die Tarif-Hinweise werden zu einem zusammengefasst.

**Tech Stack:** SvelteKit 2 / Svelte 5 (Runes), TypeScript, ECharts 5, Vitest + Testing Library,
Playwright (E2E gegen `tests/e2e/ui_server.py`), Python 3.12 (`shared/settings.py`).

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md` (verbindlich, v. a. 9.x UI) und die
freigegebenen Entwürfe: Design-Fläche https://claude.ai/artifact/8BEH9zWV1j8UE4HMtRsrYr,
Liste `/mnt/project-files/plaene/ui-verbesserungen/vorschlaege.md`, Belegbilder im selben Ordner.

## Global Constraints

- Nur `ems-app` (UI und app-Texte); kein core, keine Migration, keine neuen API-Felder.
- Farben nur über die Tokens in `ui/src/app.css` (`--accent`, `--warn`, `--muted`, `--border`, …), hell und dunkel.
- Tippflächen mindestens 44 px (`min-height: 2.75rem`), Icon-Knöpfe mit `aria-label`.
- Handy-Breite 390 px (Tests auch 360 px): keine waagrechte Seiten-Scrollleiste, keine abgeschnittene Tabelle.
- Deutsch, „du“-Ton in Texten, deutsche Zahlenformate über `$lib/format`.
- Bestehende Testnamen/Rollen nur ändern, wenn der Entwurf den Text ändert; dann Tests mitziehen.
- Vor jedem Commit: `pnpm check`, `pnpm lint`, `pnpm test` (in `ui/`); bei Python-Änderung ruff format/check,
  mypy strict, pytest der betroffenen Dateien (`export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS` vor uv).
- Commits deutsch im Stil `feat(ui): …` / `fix(app): …`, keine Modellnamen im Repo.

## Review Focus

1. Tabellen mit sehr langen Werten (Tarif-Bestandteil „Netznutzungsentgelt Arbeitspreis (Netzebene 7)“,
   Zeitfenster mit mehreren Fenstern) brechen in der Karte um, statt die Karte zu verbreitern (Task 1, E2E 360 px).
2. Legenden-Chips: Ausblenden einer Reihe bleibt erhalten, wenn das Diagramm minütlich neue Daten bekommt
   (Task 5, Test „bleibt ausgeblendet nach neuer option“).
3. „Warum?“-Texte, die nicht dem Muster `Name (Gründe): Befehl – Ergebnis.` folgen (fremde Änderung,
   Safety-Befunde dahinter), zeigen den ganzen Text statt eines leeren Satzes (Task 6).
4. Fahrzeug ohne gemessenen Ladestand (nicht nur veraltet) bekommt weiter „Ladestand unbekannt, gerechnet ab 0 %.“ (Task 2).
5. Komponenten-Meldung, die nicht mit dem Namen beginnt, bleibt unverändert (Task 4).

---

### Task 1: Tabellen am Handy als Karten (A, Teil 1) und ein Tarif-Hinweis

**Files:**
- Modify: `ui/src/app.css` (neuer Block `.stack`)
- Modify: `ui/src/lib/settings/TariffView.svelte`, `ui/src/lib/settings/LimitsView.svelte`
- Modify: `ui/src/lib/stats/SessionsCard.svelte`, `ui/src/lib/stats/VehicleCard.svelte`,
  `ui/src/lib/stats/PeaksCard.svelte`, `ui/src/lib/stats/ChecksCard.svelte`, `ui/src/lib/stats/view.ts`
- Create: `ui/src/lib/components/SplitBar.svelte`
- Modify: `src/bindaems/shared/settings.py:318-327` (`settings_warnings`)
- Test: `ui/src/lib/stats/view.test.ts`, neue `ui/src/lib/components/SplitBar.test.ts`,
  `ui/src/lib/settings/TariffView.test.ts` (neu, falls nicht vorhanden), `tests/unit/shared/test_settings.py`,
  `tests/unit/shared/test_settings_tariff.py`, `ui/tests/e2e/pages.spec.ts`

**Interfaces:**
- Produces: CSS-Klasse `stack` für `<table>`; jede `<td>` außer der ersten trägt `data-label="<Spaltenkopf>"`.
  Unter `@media (max-width: 40rem)`: `thead` visuell versteckt (wie `.sr-only`), `tr` als Block mit
  Trennlinie, erste Zelle fett als Titel ohne Beschriftung, übrige Zellen als Zeile
  `display:flex; justify-content:space-between; gap` mit `::before { content: attr(data-label); color: var(--muted) }`,
  Werte dürfen umbrechen (`white-space: normal; overflow-wrap: anywhere; text-align: right`).
  `.scroll.wide` behält am Desktop seine Regeln, am Handy gilt `min-width: 0` für `table.stack`.
- Produces: `SplitBar.svelte` mit Props `{ pv: number; battery: number; grid: number }` (kWh): Balken 8 px
  hoch, Anteile in `--pv`, `--battery`, `--grid`, `role="img"` und `aria-label="PV x %, Akku y %, Netz z %"`
  (ganze Prozent, Summe 0 → leerer Balken mit `aria-label="keine Energie"`).
- Produces: `SessionRow` erhält zusätzlich `pv_kwh`, `battery_kwh`, `grid_kwh: number` (für SplitBar);
  `split` bleibt als Text.

- [ ] **Step 1: Failing tests**
  - `test_settings.py`: die Liste der Hinweise mit sechs fehlenden Bestandteilen enthält genau
    `"6 Tarifbestandteile haben noch keinen Wert: „Netznutzungsentgelt Arbeitspreis (Netzebene 7)“, „Netzverlustentgelt“, „Elektrizitätsabgabe“, „Erneuerbaren-Förderbeitrag“, „Leistungspreis (ElWG)“, „Grundgebühr Netz“."`
    statt der sechs Einzelsätze; bei genau einem fehlenden bleibt der alte Satz
    `"Tarifbestandteil „Grundgebühr Netz“ hat noch keinen Wert."` (`test_settings_tariff.py` anpassen).
  - `SplitBar.test.ts`: `pv=8.1, battery=2, grid=2.5` → `aria-label` `"PV 64 %, Akku 16 %, Netz 20 %"`, drei Segmente mit Breiten `64.29%`, `15.87%`, `19.84%` (zwei Nachkommastellen); alles 0 → `"keine Energie"`.
  - `view.test.ts`: `sessionRows` liefert `pv_kwh`, `battery_kwh`, `grid_kwh` unverändert.
  - `TariffView.test.ts`: Tabelle hat Klasse `stack`; die Zelle „Wert netto“ eines Bestandteils ohne Wert zeigt `kein Wert` (statt `fehlt`); jede `td` außer der ersten hat `data-label` gleich ihrem Spaltenkopf.
  - `pages.spec.ts` neuer Test `'Tabellen passen bei 360 px in ihre Karte'`: Viewport 360×740, als admin
    `/einstellungen` und `/statistik` (Zeitraum Monat), für jedes `.scroll`-Element
    `scrollWidth - clientWidth <= 1`.
- [ ] **Step 2: Tests laufen lassen, sie scheitern** (`pnpm test -- SplitBar view TariffView`, `uv run pytest tests/unit/shared/test_settings.py tests/unit/shared/test_settings_tariff.py`)
- [ ] **Step 3: Umsetzen**
  - `settings_warnings`: fehlende Namen sammeln; 1 → alter Satz, ≥ 2 → `f"{n} Tarifbestandteile haben noch keinen Wert: {', '.join(f'„{name}“' for name in names)}."`.
  - `.stack`-CSS in `app.css`; Klasse `stack` und `data-label` an den Tabellen in TariffView (eine), LimitsView (beide), SessionsCard, stats/VehicleCard, PeaksCard, ChecksCard.
  - TariffView: „fehlt“ → `<span class="missing">kein Wert</span>` als Etikett (Hintergrund `--warn`, Text `--bg`, `border-radius: 999px`, `padding: 0 0.5rem`).
  - SessionsCard: Zelle „PV / Akku / Netz“ zeigt `<SplitBar>` über dem Text `row.split`.
- [ ] **Step 4: Tests grün** (gleiche Befehle, dazu `pnpm check && pnpm lint`)
- [ ] **Step 5: Commit** `feat(ui): breite Tabellen am Handy als Karten, ein Tarif-Hinweis`

### Task 2: Tabellen am Handy (A, Teil 2): Backtest, Tagesbilanz, Fahrverhalten

**Files:**
- Modify: `ui/src/lib/forecast/BacktestTable.svelte`, `ui/src/lib/history/BalanceTable.svelte`,
  `ui/src/lib/forecast/DrivingCard.svelte`, `ui/src/lib/forecast/view.ts`
- Test: `ui/src/lib/forecast/view.test.ts`, `ui/src/lib/forecast/DrivingCard.test.ts`,
  `ui/src/lib/history/BalanceTable.test.ts`, `ui/tests/e2e/pages.spec.ts`

**Interfaces:**
- Consumes: `.stack`/`data-label` aus Task 1.
- Produces: `groupDays<T>(days: readonly T[], label: (day: T) => string, cells: (day: T) => string[]): { label: string; cells: string[] }[]`
  in `forecast/view.ts`: aufeinanderfolgende Tage mit gleichen `cells` werden eine Zeile. Beschriftung:
  ein Tag → sein Label; zwei → `"Sa, So"` (Labels mit `", "`); drei oder mehr → `"Mo bis Fr"` (erstes und letztes mit `" bis "`).

- [ ] **Step 1: Failing tests**
  - `view.test.ts`: fünf gleiche Werktage + zwei gleiche Wochenendtage → zwei Zeilen `"Mo bis Fr"`, `"Sa, So"`; Mo und Di verschieden → je eine Zeile; leere Liste → `[]`.
  - `DrivingCard.test.ts`: mit dem Demo-Muster (Mo–Fr gleich) zeigt die Tabelle eine Zeile „Mo bis Fr“; Knopf `Tage einzeln` (aria-pressed) zeigt wieder sieben Zeilen.
  - `BalanceTable.test.ts`: Tabelle hat Klasse `stack`, `td` mit `data-label` wie die Spaltenköpfe.
  - `pages.spec.ts` (Test aus Task 1 erweitern): auch `/prognosen` und `/verlauf` bei 360 px ohne überlaufende `.scroll`.
- [ ] **Step 2: Tests scheitern**
- [ ] **Step 3: Umsetzen** — `stack`/`data-label` an Backtest-, Tagesbilanz- und Fahrverhalten-Tabelle; DrivingCard gruppiert per `groupDays` (Zellen ohne die Tagesspalte), Umschalter „Tage einzeln“ unter der Tabelle (lokaler `$state`, Standard: gruppiert).
- [ ] **Step 4: Tests grün**
- [ ] **Step 5: Commit** `feat(ui): Prognose-, Bilanz- und Fahrverhalten-Tabellen am Handy als Karten`

### Task 3: Laden-Seite (B)

**Files:**
- Modify: `ui/src/lib/charging/VehicleCard.svelte`, `ui/src/lib/charging/view.ts`, `ui/src/lib/charging/PriorityCard.svelte`
- Test: `ui/src/lib/charging/VehicleCard.test.ts`, `ui/src/lib/charging/view.test.ts`, Prioritäts-Tests (wo vorhanden), `ui/tests/e2e/control.spec.ts`

**Interfaces:**
- Produces in `charging/view.ts`:
  - `vehicleStatus(vehicle: VehicleHealth): { text: string; tone: 'ok' | 'muted' }` — lädt → `{ "lädt", ok }`; angesteckt → `{ "angesteckt", muted }`; nicht angesteckt → `{ "nicht angesteckt", muted }`; `plugged === null` → `{ "Stand unbekannt", muted }`.
  - `staleSocNote(pct: number): string` → `"Der letzte Ladestand (70 %) ist zu alt. Der Plan rechnet deshalb vorsichtig ab 0 %."` (Zahlen über `formatSoc`).
  - `SOC_ASSUMED_NOTE` = der bisherige Satz `"Ladestand unbekannt, gerechnet ab 0 %."` als exportierte Konstante (von `plannedLine` genutzt).

Aufbau der Karte (von oben), wie Entwurf B:
1. Kopf: Name (Card-Titel), darunter `"{Wallbox} ({Zuordnung}) · {Modus}"` gedämpft; rechts Etikett `vehicleStatus`.
2. Ladestand groß (`1.75rem`, fett) mit Etikett „veraltet“ (Rand `--warn`) bzw. „geschätzt“; rechts „Ziel X %“ bei Modus Geplant; Balken mit Ziel-Marke (`role="img"`, `aria-label="Ladestand X %, Ziel Y %"`). Ist der Ladestand veraltet und der Plan-Zusatz `SOC_ASSUMED_NOTE` vorhanden, ersetzt `staleSocNote(pct)` diesen Zusatz.
3. Planbox (Hintergrund `--surface` heller/Rand): `planned.text`, übrige Zusätze, Sofort-Limit-Satz, Session-Zeile.
4. „Laden einstellen“ (nur `operate`): Lademodus + Boost (+ NowLimitSelect), bei Geplant Ziel in % und Abfahrt nebeneinander, Knopf **„Ziel speichern“** (Hauptknopf, volle Breite), Boost-Kosten.
5. „Vorschlag aus dem Fahrverhalten“: Vorschlagstext, Knopf **„Vorschlag übernehmen“**, darunter der Automatik-Schalter mit Text.
6. `<details>` „Details“: Angesteckt, Lädt, Grenze, Letzter Grund, Befehle letzte Stunde (`.facts`).
PriorityCard: Text-Knöpfe „nach oben/nach unten“ werden 44×44-Icon-Knöpfe (Pfeil-SVG, `aria-label` unverändert).

- [ ] **Step 1: Failing tests**
  - `view.test.ts`: `vehicleStatus` für die vier Fälle; `staleSocNote(70)` exakt wie oben.
  - `VehicleCard.test.ts`: Knöpfe `Ziel speichern` und `Vorschlag übernehmen` (kein Knopf „Übernehmen“ mehr); „Grenze“ steht in einem geschlossenen `details`; veralteter SOC 70 % mit Modus Geplant und Zusatz „assumed“ zeigt `staleSocNote(70)` und nicht „Ladestand unbekannt …“; ohne SOC (`soc` undefined) bleibt „Ladestand unbekannt, gerechnet ab 0 %.“.
  - E2E `control.spec.ts`: bestehende Klicks auf „Übernehmen“ auf die neuen Namen umstellen (sie scheitern vorher).
- [ ] **Step 2: Tests scheitern**
- [ ] **Step 3: Umsetzen** (Props der Karte bleiben gleich; nur Aufbau und Texte)
- [ ] **Step 4: Tests grün** (inkl. `pnpm build && pnpm exec playwright test control` mit lokaler Chromium-Konfiguration)
- [ ] **Step 5: Commit** `feat(ui): Laden-Karte mit Wichtigem oben und eindeutigen Knöpfen`

### Task 4: Prognosegüte und Merkmale (C)

**Files:**
- Modify: `ui/src/lib/forecast/QualityTable.svelte`, `ui/src/lib/forecast/FeatureTable.svelte`, `ui/src/lib/forecast/view.ts`
- Test: `ui/src/lib/forecast/view.test.ts`, `ui/src/lib/forecast/QualityTable.test.ts` (neu), `ui/tests/e2e/prognosen.spec.ts`

**Interfaces:**
- Produces in `forecast/view.ts`:
  - `qualitySummary(rows: readonly QualityRow[]): string | null` — über die Vorläufe, die Profil und Modell beide haben: `x = round((1 − Σ(mae_model·n_model) / Σ(mae_profile·n_profile))·100)`. `x > 0` → `"Das Lastmodell lag im Mittel {x} % näher am echten Verbrauch als das Profil."`; `x < 0` → `"Das Profil lag im Mittel {|x|} % näher am echten Verbrauch als das Lastmodell."`; `x = 0` → `"Lastmodell und Profil lagen gleich nah am echten Verbrauch."`; ohne Paar `null`.
  - `compareRows(rows): { horizon: string; profile: string; model: string; used: string }[]` je Vorlauf in Reihenfolge `HORIZON_ORDER`, Werte `watts(mae)` oder `DASH`.
  - `pvRows(rows): string[]` — je PV-Zeile `"PV-Prognose, {Vorlauf}: {mae} ({n} Viertelstunden)"`.
  - `HORIZON_LABELS` werden `"bis 6 h"`, `"6 bis 24 h"`, `"über 24 h"`.
  - `featureLabel(label: string): string` — beginnt `label` mit `sensor.`: Teil nach dem Punkt, `_` → Leerzeichen, erster Buchstabe groß, Zusatz `" (Sensor)"` (`sensor.wohnzimmer_temperatur` → `"Wohnzimmer temperatur (Sensor)"`); sonst unverändert.

Karte „Prognosegüte“ (Entwurf C): Zeitraum als Segment-Leiste (4 gleich breite Knöpfe in einer Zeile,
aktiver gefüllt mit `--accent`), Satz `qualitySummary`, Tabelle `Vorlauf | Profil | Modell | im Plan`
(`compareRows`), Fußnote „Mittlere Abweichung je Viertelstunde, kleiner ist besser.“, PV-Zeilen, dann
`<details>` „Alle Werte“ mit der bisherigen Tabelle (Spalte „Slots“ heißt „Viertelstunden“, Klasse `stack`).
Karte „Merkmale“ heißt „Was das Modell nutzt“: Raster Name | Balken | Anteil, die ersten 5, Knopf
„alle {n} Merkmale zeigen“; ungenutzte Sensoren mit `featureLabel(entity_id)`.

- [ ] **Step 1: Failing tests** — `qualitySummary` mit den Demo-Zahlen (Profil 115/142/169 W, Modell 82/101/120 W, je n=96) → `"Das Lastmodell lag im Mittel 29 % näher am echten Verbrauch als das Profil."`; negatives und gleiches Beispiel; ohne Modellzeilen `null`. `compareRows` Reihenfolge und Strich bei fehlendem Wert. `featureLabel` beide Fälle. `QualityTable.test.ts`: Satz sichtbar, Spaltenköpfe `Vorlauf`, `Profil`, `Modell`, `im Plan`, `details` „Alle Werte“ geschlossen. E2E `prognosen.spec.ts` auf die neuen Texte.
- [ ] **Step 2: Tests scheitern**
- [ ] **Step 3: Umsetzen**
- [ ] **Step 4: Tests grün**
- [ ] **Step 5: Commit** `feat(ui): Prognosegüte in einem Satz und drei Spalten, lesbare Merkmale`

### Task 5: Legenden als Chips unter dem Diagramm (E)

**Files:**
- Create: `ui/src/lib/charts/legend.ts`, `ui/src/lib/charts/ChartLegend.svelte`
- Modify: `ui/src/lib/charts/EChart.svelte`, `ui/src/lib/charts/options.ts` (`baseOption`, `priceForecastOption`, Grid-Abstände), `ui/src/lib/plan/chart.ts` (`LEGEND_TWO_ROWS_TOP` entfällt)
- Test: `ui/src/lib/charts/legend.test.ts` (neu), `ui/src/lib/charts/EChart.test.ts`, `options.test.ts`, `plan/chart.test.ts`, `ui/src/routes/layout-dashboard.test.ts`

**Interfaces:**
- Produces: `interface LegendItem { name: string; color: string; mark: 'line' | 'dashed' | 'area' | 'rect' }`;
  `legendItems(option: EChartsCoreOption): LegendItem[]` — ohne `legend` im option: `[]`. Reihenfolge und
  Auswahl aus `legend.data` (Strings oder `{ name, icon }`), sonst alle Reihen mit `name`. Farbe:
  `lineStyle.color ?? itemStyle.color ?? areaStyle.color`; `mark`: `icon: 'rect'` oder Balken → `'rect'`,
  nur Fläche (`lineStyle.width === 0`) → `'area'`, `lineStyle.type === 'dashed'` → `'dashed'`, sonst `'line'`.
- Produces: `withSelection(option, hidden: ReadonlySet<string>): EChartsCoreOption` — setzt `legend.selected` für alle Items (`false` für ausgeblendete).
- `ChartLegend.svelte` Props `{ items: LegendItem[]; hidden: ReadonlySet<string>; onToggle: (name: string) => void }`:
  `role="group" aria-label="Reihen ein- und ausblenden"`, je Item ein `<button aria-pressed>` (min-height 2.25rem,
  Pille, Marke vor dem Namen), ausgeblendete gedämpft und durchgestrichen.
- `EChart.svelte`: hält `hidden = $state(new Set<string>())`, rendert `<ChartLegend>` unter dem Diagramm, wenn `legendItems(option).length > 0`; der `$effect` setzt `withSelection(prepared, hidden)`.
- `baseOption`: `legend: { show: false }`, `grid.top: 28` (Platz für Achsentitel). `priceForecastOption`
  Reihennamen: `Preis`, `Einspeisung`, `PV-Spanne`, `PV`, `Haus`, `Akku`, `Akku-Plan` (Tooltip-Schlüssel mitziehen,
  `legend.data` ohne `PV P25`). Akku-Diagramm im Zeitplan: `grid.top` wie alle.

- [ ] **Step 1: Failing tests** — `legend.test.ts`: Übersicht-Option (Fixture aus `options.test.ts`) → Namen `['Preis','Einspeisung','PV-Spanne','PV','Haus','Akku','Akku-Plan']`, Marken `line,dashed,area,line,dashed,line,dashed`; Plan-SOC-Option enthält `Netzladen`/`Halten` mit `rect`; `withSelection` setzt `selected.PV === false`. `EChart.test.ts`: Chips erscheinen, Klick setzt `aria-pressed="false"` und ruft `setOption` mit `legend.selected.PV === false`; nach einer neuen `option` bleibt `PV` ausgeblendet. `options.test.ts`: `baseOption().legend.show === false`.
- [ ] **Step 2: Tests scheitern**
- [ ] **Step 3: Umsetzen**
- [ ] **Step 4: Tests grün**
- [ ] **Step 5: Commit** `feat(ui): Legenden als Chips unter den Diagrammen`

### Task 6: „Warum?“ als kompakte Zeilen (F)

**Files:**
- Modify: `ui/src/lib/decisions/WhyCard.svelte`, `ui/src/lib/decisions/view.ts`
- Test: `ui/src/lib/decisions/view.test.ts`, `ui/src/lib/decisions/WhyCard.test.ts`, `ui/src/routes/layout-dashboard.test.ts`

**Interfaces:**
- Produces: `whyBody(text: string, name: string): string` — Name am Anfang weg (`splitName`), dann Muster
  `({Gründe}): {Befehl} – {Ergebnis}.{ Befunde}` (Format von `decision_text` in `shared/decisions.py`) →
  `"{Gründe}: {Befehl}"` bzw. ohne Gründe `"{Befehl}"`; Befunde hängen mit `" · "` an. Passt das Muster
  nicht, bleibt der Rest nach dem Namen (oder der ganze Text) unverändert.
- Zeile: ganze Zeile ein Link (`display:flex; flex-direction:column; min-height: 2.75rem`), oben Name fett
  und rechts Alter, darunter `whyBody`, darunter Etikett `resultLabel(decision.result)` (Ton `--warn` bei
  `deferred`, `rejected`, `error`, `timeout`, `not_sent`, `foreign`; sonst gedämpft). Trennlinien statt Abstand.

- [ ] **Step 1: Failing tests** — `whyBody("Victron (ESS) (Überschuss): Netz-Sollwert 50 W – Dry-Run, nicht gesendet.", "Victron (ESS)") === "Überschuss: Netz-Sollwert 50 W"`; `whyBody("EVCS „evcs“: Strom 8 A – Dry-Run, nicht gesendet.", "EVCS „evcs“") === "Strom 8 A"`; `whyBody("Tesla Model 3 (Min+PV): Strom 20 A – abgelehnt. Grenze 16 A.", "Tesla Model 3") === "Min+PV: Strom 20 A · Grenze 16 A."`; `whyBody("Klimaanlage wurde von außen geändert", "Klimaanlage") === "wurde von außen geändert"`. `WhyCard.test.ts`: je Entscheidung ein Link mit Name und Text, Etikett „Dry-Run“.
- [ ] **Step 2: Tests scheitern**
- [ ] **Step 3: Umsetzen**
- [ ] **Step 4: Tests grün**
- [ ] **Step 5: Commit** `feat(ui): Warum-Karte als kompakte, tippbare Zeilen`

### Task 7: System-Komponenten (D) und Anzeigenamen (G)

**Files:**
- Modify: `ui/src/lib/system/view.ts`, `ui/src/routes/system/+page.svelte`, `ui/src/lib/history/balance.ts`,
  `ui/src/lib/history/BalanceTable.svelte` (Namen durchreichen), `ui/src/routes/verlauf/+page.svelte`,
  `ui/src/routes/+page.svelte` (Namen für „Warum?“), `ui/src/lib/wallboxes.ts`
- Test: `ui/src/lib/system/view.test.ts`, `ui/src/lib/history/balance.test.ts`, `ui/src/lib/wallboxes.test.ts` (neu oder vorhanden), Seiten-Tests System/Verlauf

**Interfaces:**
- `COMPONENTS` ergänzt: `'load-model': 'Lastmodell'`, `driving: 'Fahrverhalten'`, `thermal_model: 'Wärmemodell'`, `planning: 'Planer'`.
- `componentMessage(label: string, message: string): string` — beginnt `message` mit `label + " "` oder `label + ": "`, wird dieser Anfang entfernt; sonst unverändert.
- `sortComponents<T extends { ok: boolean }>(list: readonly T[]): T[]` — gestörte zuerst, sonst Reihenfolge des Servers.
- Kopfzeile der Karte: `"{ok} von {n} in Ordnung"`.
- `wallboxDisplay(key: string, names: Record<string, string>): string` in `wallboxes.ts` → `names[key] ?? key`.
- `actuatorDisplay(id: string, label: string, names: Record<string, string>): string` → für `wallbox.<key>` `wallboxDisplay(key, names)`, sonst `label`.
- `selfcheckLabel(id, names)`: `evcs_mode.<key>` → `"EVCS-Modus: {wallboxDisplay(key, names)}"`.
- `counterLabel(key, names)`: `wallbox.<key>.total_kwh` → `"Wallbox {wallboxDisplay(key, names)}"`.
- Verlauf: Reihen mit Id `wallbox.<key>` zeigen `wallboxDisplay(key, names)` (Namen aus `wallboxLabels(limits)`).

- [ ] **Step 1: Failing tests** — `componentLabel('thermal_model') === 'Wärmemodell'` (und die drei anderen); `componentMessage('Preise','Preise bis 09.10. 23:45') === 'bis 09.10. 23:45'`, `componentMessage('InfluxDB','InfluxDB: alles übertragen') === 'alles übertragen'`, `componentMessage('Planer','Plan von 10:00 Uhr')` unverändert; `sortComponents` stellt `ok:false` nach vorn; `actuatorDisplay('wallbox.evcs','EVCS „evcs“',{evcs:'EVCS'}) === 'EVCS'`; `selfcheckLabel('evcs_mode.evcs',{evcs:'EVCS'}) === 'EVCS-Modus: EVCS'`; `counterLabel('wallbox.evcs.total_kwh',{evcs:'EVCS'}) === 'Wallbox EVCS'`; System-Seite zeigt „10 von 11 in Ordnung“ mit Demo-Daten.
- [ ] **Step 2: Tests scheitern**
- [ ] **Step 3: Umsetzen** — Komponentenliste: eine Zeile je Komponente (Punkt, Name, Meldung rechts gedämpft, bei Störung `--error`), Zeilenhöhe ≥ 2.75rem.
- [ ] **Step 4: Tests grün**
- [ ] **Step 5: Commit** `feat(ui): deutsche Komponentennamen und Wallbox-Namen statt Schlüssel`

### Task 8: Statistik-Kopf und Kacheln (H)

**Files:**
- Modify: `ui/src/routes/statistik/+page.svelte`, `ui/src/lib/stats/StatTiles.svelte`, `ui/src/lib/stats/view.ts` (`tiles`)
- Test: `ui/src/routes/statistik/page.test.ts`, `ui/src/lib/stats/view.test.ts`, `ui/tests/e2e/stats.spec.ts`

**Interfaces:**
- `tiles(stats)`: Label „Ersparnis*“ ohne Untertitel, „Einspeiseerlös“ → „Erlös“; neues Feld je Tile `size: 'large' | 'small'` (Autarkie, Eigenverbrauch `large`; Kosten, Ersparnis, Erlös `small`).
- `StatTiles`: Raster mit 6 Spalten, `large` über 3, `small` über 2 Spalten; ab `48rem` eine Zeile mit 5 gleichen Spalten; darunter Fußnote `"* gegenüber Fixtarif ohne Anlage. „Erlös“ ist der Einspeiseerlös."`.
- Kopf: Zeitraum als Segment-Leiste (4 gleich breite Knöpfe, wie Task 4); Zeile 2: `←` (44×44, `aria-label="Zurück"`), Titel (mittig), `→` (`aria-label="Weiter"`), `Heute` — alles in einer Zeile.

- [ ] **Step 1: Failing tests** — `tiles` Labels/size; `page.test.ts`: Knöpfe Zurück, Weiter, Heute haben denselben Elternknoten wie die Überschrift; Fußnote sichtbar. E2E `stats.spec.ts`: bei 390 px liegen `Heute` und der Titel auf gleicher Höhe (Bounding-Box-Mitte ±8 px).
- [ ] **Step 2: Tests scheitern**
- [ ] **Step 3: Umsetzen**
- [ ] **Step 4: Tests grün**
- [ ] **Step 5: Commit** `feat(ui): Statistik-Kopf in zwei Zeilen und Kacheln 2 + 3`

### Task 9: Doku, Übergabe, Vertragsprüfung

**Files:**
- Modify: `docs/betrieb.md` (UI-Abschnitte zu Laden, Prognosen, System, Statistik, Diagrammen; Tarif-Hinweis),
  Spec 9.6 Absatz „Stand Verständlichkeit und Design (29.10.2026)“, `docs/uebergabe.md` (Status; Punkt 18 „nur ems-app mit dem nächsten Sammel-Pull“ um diese Welle ergänzen)
- Test: `tests/test_docs.py` (läuft mit), volle Kette

- [ ] **Step 1:** Doku-Stellen mit den neuen Texten nachziehen (Knopfnamen „Ziel speichern“, „Vorschlag übernehmen“; Legenden-Chips; Tarif-Hinweis-Satz).
- [ ] **Step 2:** Volle Kette: ruff, mypy strict (src + cerbo-watchdog), lint-imports, pytest mit Abdeckung, `pnpm check/lint/test`, `pnpm build` + E2E (lokale Playwright-Konfiguration mit `/opt/pw-browsers/chromium`, nicht committen).
- [ ] **Step 3: Commit** `docs: Verständlichkeit und Design in Betrieb, Spec und Übergabe`

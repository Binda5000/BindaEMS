---
title: "Diagramm-Legenden als Chips unter dem Diagramm, Anzeigenamen der Wallboxen überall"
author: Claude
date: 2026-10-29
branch: claude/project-thread-ja1izu
cost: { minutes: 1, tokens: 2198 }
id: l-a121ab3a
scope: repo
status: active
replaces: l-a5574601
src: agent
touches:
  - ui/src/lib/charts/legend.ts
  - ui/src/lib/charts/ChartLegend.svelte
  - ui/src/lib/charts/EChart.svelte
  - ui/src/lib/charts/options.ts
  - ui/src/lib/plan/chart.ts
  - ui/src/lib/wallboxes.ts
  - ui/src/lib/stats/StatTiles.svelte
  - src/bindaems/app/planning/vehicles.py
---

## Decided
- Binda 29.10.2026 (Entwürfe A–H, „Ja, so umsetzen“): Legenden sind HTML-Chips unter jedem Diagramm (`ChartLegend.svelte`, `legendItems`/`withSelection` in `ui/src/lib/charts/legend.ts`). ECharts-Legende bleibt `show: false` in `baseOption`; `EChart.svelte` hält die ausgeblendeten Namen und setzt `legend.selected` bei jeder neuen Option, damit Ausgeblendetes bei Live-Updates ausgeblendet bleibt. Ersetzt die einzeilig blätternde ECharts-Legende vom selben Tag.
- Chips sind 44 px hoch (globale Tippflächen-Regel geht vor dem Planwert 2.25rem). Gleichnamige Reihen ergeben einen Chip, wie in ECharts.
- Akku-Diagramm im Zeitplan: die markArea für Netzladen und Halten hängt an der jeweiligen Platzhalter-Reihe `legend_*` (nicht an der SOC-Reihe), sonst wäre der Chip ein toter Schalter. ECharts zeichnet die markArea auch bei leerer `data`.
- Wallbox-Anzeigename aus den Einstellungen überall statt `EVCS „evcs“` (`actuatorDisplay`/`wallboxDisplay` in `ui/src/lib/wallboxes.ts`); unbekannter Schlüssel → Bezeichnung des core. In Live-/Dry-Run-Dialog bei gleichen Namen die core-Bezeichnung mit Schlüssel (`uniqueDisplays`).
- Weiter gültig: Planer übernimmt Health `planned.state == "reached"` als Bedarf 0 (`planning/vehicles.py`); Tooltips `confine: true` über `...base.tooltip`; Tooltip im Akku-Diagramm nennt den Slot mit Spanne.

## Tried and ruled out
- markLine-Label `insideEndTop` für die „jetzt“-Linie: liegt links der Linie auf den Achsenwerten; `insideEndBottom` liegt rechts.
- Feste 19 px für Geldwerte in den kleinen Statistik-Kacheln: „1.234,56 €“ passt bei 360 px nicht (Innenbreite ~75 px). Jetzt `min(19px, 100cqi / (Zeichen · 0,56))` mit Container-Query.

## Watch out
- `--cap` der kleinen Kacheln nur per `var(--cap, 1.1875rem)` setzen: eine spätere Regel gleicher Spezifität überschreibt sonst die Media-Query ab 48rem.
- Neue Plan-Felder (`Plan.soc_start_pct`, `PlanVehicle.reached`) machen gespeicherte Pläne für ältere ems-app-Images unlesbar (`extra="forbid"`).
- Lokale E2E brauchen eine temporäre Playwright-Konfiguration mit `launchOptions.executablePath: '/opt/pw-browsers/chromium'` (nicht committen). Eine Warteschleife `while pgrep -f "playwright test …"` findet sich selbst und endet nie.

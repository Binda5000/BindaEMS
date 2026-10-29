---
title: ECharts-„jetzt“ rechts der Linie und Planer übernimmt „erreicht“ des core
author: Claude
date: 2026-10-29
branch: claude/project-thread-ja1izu
cost: { minutes: 116, tokens: 335622 }
id: l-a5574601
scope: repo
status: active
src: agent
touches:
  - ui/src/lib/plan/chart.test.ts
  - ui/src/lib/plan/chart.ts
  - docs/betrieb.md
  - src/bindaems/app/planning/explain.py
  - tests/unit/app/planning/test_explain.py
  - docs/uebergabe.md
  - ui/src/lib/charts/options.test.ts
  - ui/src/lib/charts/options.ts
  - docs/superpowers/specs/2026-10-08-bindaems-design.md
  - src/bindaems/app/planning/builder.py
---

## Decided
- Binda 29.10.2026 (Fix-Liste G1–G4, T1–T6, R1; R2 nicht): Diagramm-Legenden einzeilig blätternd (`type: 'scroll'` in `baseOption`), nur das Akku-Diagramm (`planSocOption`) bricht um (`plain`, grid.top 76, Flächen mit `icon: 'rect'`). Alle Tooltips `confine: true` über `...base.tooltip`.
- Der Planer (`planning/vehicles.py` `planned_vehicles`) übernimmt Health `planned.state == "reached"` als Bedarf 0 (`PlanVehicle.reached`); die app rechnet keine eigene Hysterese. Vorher plante er bei 49 %/50 % weiter 1 %, während der core „Ziel-SOC erreicht“ hält (bis Ziel − `TARGET_RESTART_PP` 3 pp).
- Tooltip im Akku-Diagramm nennt den Slot, der am SOC-Punkt endet, mit Spanne („Akku 14:15–14:30: Netzladen 6,7 kW“), weil der Axis-Tooltip auf Slot-Enden einrastet.

## Tried and ruled out
- markLine-Label `insideEndTop` für die senkrechte „jetzt“-Linie: liegt links der Linie (ECharts Line.js, textVerticalAlign bottom, gedreht) und damit im Zeitplan auf den Achsenwerten, weil die Zeitachse dort beim Slot von jetzt beginnt. `insideEndBottom` liegt rechts im Diagramm.

## Watch out
- Neue Felder im Plan (`Plan.soc_start_pct`, `PlanVehicle.reached`) machen gespeicherte Pläne für ältere ems-app-Images unlesbar (`extra="forbid"`): beim Rollback 30 Tage Lücke in Verlauf/„Plan gegen Ist“, und `previous` ist der letzte Plan vor dem Update (docs/betrieb.md Abschnitt 4).
- `vehicles_fingerprint` enthält `planned.state` nicht: Wechsel erreicht/nicht erreicht kommt erst mit dem Viertelstunden-Lauf in den Plan.
- Lokale E2E brauchen eine temporäre Playwright-Konfiguration mit `launchOptions.executablePath: '/opt/pw-browsers/chromium'` (nicht committen).

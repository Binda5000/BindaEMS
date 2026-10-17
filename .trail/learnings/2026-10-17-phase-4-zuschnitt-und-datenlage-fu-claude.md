---
title: Phase 4 Zuschnitt und Datenlage für die Statistik
author: Claude
date: 2026-10-17
branch: claude/phase-4-plan-whnbh6
cost: { minutes: 1, tokens: 1502 }
touches:
  - docs/superpowers/plans/2026-10-17-phase-4a-statistik.md
  - src/bindaems/app/ledger/service.py
  - src/bindaems/core/accounting/flows.py
---

## Decided
- Phase 4 geteilt: 4a Statistik (nur app/UI/deploy, core und shared unverändert, Ausrollen nur `ems-app`), 4b Geplantes Laden (core + Optimierer, nach Abnahme Phase 3). Betreiber hat am 17.10.2026 „Statistik zuerst“ gewählt.
- Ladesessions aus dem Steckzustand im State (EVCS `wallbox.<id>.status != 0`, TWC `vehicle_connected`), Fahrzeug aus Health `wallboxes[].occupant` (gilt nur, solange der core regelt).

## Tried and ruled out
- Sessions nur aus Health: nach `idle` (Regelbetrieb aus) sind `occupant`/`assignment` leer, Sessions gingen verloren.
- Sessions aus Wallbox-Energie der Abrechnung: trennt jede PV-Pause, kennt keine Steckzeiten.

## Watch out
- `slot_flows` und Abrechnung sind je Wallbox (`wb:<id>`), nicht je Auto; Kosten je Auto brauchen die Session-Zuordnung.
- Zählerstände stehen nicht in der Influx-Messung `flows`: per Backfill nachgetragene Slots haben keine `counters`. Für Abgleich/Lücken `energy` (raw 90 Tage, long 1-min-last) lesen.
- Es gibt keinen Akku-Zähler; PV-Zähler fehlen für DC-gekoppelte PV (`pv_dc`).
- `allocate` liegt im core; die app darf ihn nicht importieren (lint-imports), Tests dürfen beide vergleichen.
- Dev-Abhängigkeiten: `uv sync --extra core --extra app --extra dev`, sonst fehlt pytest im venv.

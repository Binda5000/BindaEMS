---
title: "Plan 4b Geplantes Laden: Datenlücken und Fallen"
author: Claude
date: 2026-10-18
branch: claude/phase-4b-plan-agf1qj
cost: { minutes: 1, tokens: 5381 }
touches:
  - docs/superpowers/plans/2026-10-18-phase-4b-geplantes-laden.md
---

## Decided
- Plan 4b liegt in docs/superpowers/plans/2026-10-18-phase-4b-geplantes-laden.md (19 Tasks, 17 Entscheidungen zur Freigabe); Umsetzung erst nach Freigabe und nach der Abnahme von Phase 3.
- e-Golf-Ladestand: `soc_entity` wird vom core abonniert (Signal `vehicle.<k>.soc_pct`), dazu Schätzung aus Anker + Wallbox-Zählerenergie × η / nutzbare kWh; ohne Wert gilt „angenommen 0 %“.
- Tesla-Ladelimit setzen nur mit `charge_limit_sync` (Standard aus); sonst wirksames Ziel = min(Ziel, Limit).
- Geplante Ladung gilt in der Statistik nur, wenn zu Beginn machbar; „früh abgefahren“ und „unbekannt“ zählen nicht.

## Tried and ruled out
- Akku-Unterstützung für „Geplant“: abgelehnt, der Plan entscheidet über Netzladung des Autos, der Akku gehört dem Haus.

## Watch out
- `VehicleObs` hat heute weder Ladestand noch Ladelimit; EVCS liefert keine Session-Energie, nur `wallbox.<k>.total_kwh`.
- Das Gast-Select nutzt dieselben `MODE_LABELS`: „Geplant“ darf dort nicht auftauchen.
- peak_guard `_LEVELS` und Akku `SUPPORT_MODES` müssen den neuen Modus bewusst einordnen.
- Gespeicherte Absichten und Pläne ohne die neuen Felder müssen mit Standardwerten laden.
- Nächste Migration ist 0012 (0011 = cost_basis).

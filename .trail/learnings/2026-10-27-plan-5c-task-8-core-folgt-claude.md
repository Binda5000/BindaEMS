---
title: "Plan 5c Task 8: core folgt dem Planwert der Klimaanlage"
author: Claude
date: 2026-10-27
branch: claude/project-thread-x8izsc
cost: { minutes: 146, tokens: 291367 }
id: l-22b7380c
scope: repo
status: active
src: agent
touches:
  - "src/bindaems/core/control/thermal.py#ThermalStrategy"
  - "src/bindaems/core/control/regulation.py#Regulation"
  - src/bindaems/tools/sim/scenarios.py
---

## Decided
- „Zurück bei Netzbezug“ (Regel 6) nimmt mit Planwert nur Stufen über dem Planwert zurück. Nachts hält der Akku, das Haus bezieht 2–3 kW aus dem Netz; mit `import_above_w + thermal_grid_w` gegen den ganzen Netzbezug holte Regel 6 jedes geplante Vorheizen nach 5 min zurück (der core kennt den geplanten Bezug des Hauses nicht). Entscheidung 9 nennt Regel 6 auch nicht unter den Regeln „vor dem Planwert“.
- Planwert nach unten (`_rule_plan_down`) steht vor Autos und Akku: Fällt der Plan von +3 auf −2, griff sonst zuerst die SOC-Regel (Plan ≤ 0 → 6a-Schwelle) mit nur einer Stufe (`thermal_battery` in klima-plan-vorheizen).
- Über 0 hinweg geht der Wunsch erst auf 0, das Negative im nächsten Zyklus: Die Schrittregel (`safety/offset.py`) lehnt +1 → −0,5 ab, und eine Ablehnung setzt die Strategie zurück.
- Stufen aus dem Überschuss über dem Plan merkt `ThermalStrategy._above`; sonst zöge der Plan sie sofort zurück und der Überschuss pendelte.
- `thermal_plan` hat `{k}` im Text; `decision_text` setzt den Versatz der Entscheidung ein (der Planwert selbst ist nur in `inputs.planned_steps`: die Aufrufer im core dürfen sich nicht ändern).

## Watch out
- `ActiveSlot` (plan_exec.py) trägt `thermal_steps` nicht; regulation holt den `IntentPlanSlot` selbst (`plan.slot_at`), nur wenn `active_slot` einen gültigen Slot fand.
- Victron im Dry-Run: Der ESS deckt jede Last aus dem Akku; Szenarien mit Netzbezug für den Plan brauchen „Nur Laden“ (`charger_only`).

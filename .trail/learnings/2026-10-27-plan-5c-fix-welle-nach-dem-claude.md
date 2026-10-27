---
title: Plan 5c Fix-Welle nach dem Gesamtreview
author: Claude
date: 2026-10-27
branch: claude/project-thread-x8izsc
cost: { minutes: 75, tokens: 472428 }
id: l-49e6c80b
scope: repo
status: active
src: agent
touches:
  - "src/bindaems/app/learning/thermal_service.py#ThermalModelService"
  - src/bindaems/app/planning/service.py
  - "src/bindaems/app/planning/routes.py#thermal_json"
  - "src/bindaems/core/control/thermal.py#ThermalStrategy"
---

## Decided
- `ThermalModelService._compute`: Ein gelungenes Lesen ohne eine Viertelstunde legt nichts ab, ruft `on_change` nicht und setzt `_empty` (Komponente „Keine Daten der Klimaanlage im Fenster, das letzte Wärmemodell gilt“, ohne Punkt wie alle Komponentensätze). Damit gilt die Entscheidung aus Task 5 („auch ein Lauf ohne Daten ist ein Stand“) nicht mehr: ein leeres InfluxDB-Fenster überschrieb sonst ein taugliches Modell mit `no_data`. Ein Lauf mit Slots, aber ohne taugliche Tage, bleibt ein Stand.
- `load_stored` ruft `on_change`, wenn es Stände fand: `PlanningService.thermal_model_changed` setzt nur ein Flag, das ist vor dem Start der Planung harmlos.
- `GET /api/plan`: `thermal.active` = gespeichert UND `learning.thermal.enabled` von jetzt (`thermal_json(..., enabled=)`); der gespeicherte Plan bleibt unverändert, `reason` auch (die UI zeigt dann die Vorschau).
- Zweiter Planungsschritt: `asyncio.wait_for(asyncio.to_thread(...), THERMAL_STAGE_TIMEOUT_S)` (HiGHS-Limit 5 s + 5 s). Der Thread läuft nach dem Timeout weiter, er lässt sich nicht abbrechen; das ist bewusst hingenommen (nur die Planung wartet nicht mehr).
- core: Am Ende des Planwerts geht ein Wunsch unter `-less_max_steps` mit Hinweis „weniger“ sofort auf `-less_max_steps` (`thermal_plan`); ohne Hinweis übernimmt `_rule_less_end` (0) im selben Zyklus, darum klemmt die neue Regel nur mit Hinweis.

## Watch out
- `test_close_cancels_the_run_and_waits_for_the_thread` war wackelig: `until(len(_threads) == 1)` sah manchmal noch den Rechen-Thread kurz vor seinem Ende. Jetzt wartet der Test auf ein Event im Schreib-Thread.

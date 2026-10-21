---
title: "Plan 5b Task 8: Automatik und Übernahme"
author: Claude
date: 2026-10-20
branch: claude/phase-5b-plan-xo03ty
cost: { minutes: 60, tokens: 570680 }
touches:
  - src/bindaems/app/learning/driving_auto.py
  - src/bindaems/app/charging/planned.py
  - src/bindaems/app/runtime.py
---

## Decided
- `DrivingAutomation` (app/learning/driving_auto.py) liest Modus und Werte aus `IntentService.current()` (frischer Stand vom core), Vertrauen aus `live.health` (`health_trusted`); offene Zeilen über `PlannedCharges.open_charges()` (Mutex gegen `record` im anderen Thread; vor dem ersten Health aus der Datenbank) mit `opened_at`, das sich beim Korrigieren eines angenommenen Beginns nicht ändert (Ruling T8-13).
- Abfahrt und Ziel getrennt nach Konfidenz ab „mittel“; was nicht übernommen wird, geht als `None` in `SetTarget`, damit eine gleichzeitige Bedienung des anderen Werts nicht überschrieben wird.
- Übernahme in `planned_charge` misst ab dem Setzen (`auto_set_at`, Ruling T8-10), nicht ab dem Health, das es zeigt: sonst endet ein Setzen kurz vor 10 min als „geändert“.
- Senden nur mit `precondition` unter der Sperre von `IntentService.apply` (Zähler der Bedienungen, Ruling T8-11); sonst überschreibt die Automatik eine Bedienung zwischen Prüfung und Senden.
- `auto_set_at` gilt nur ohne spätere Bedienung: `on_manual_target` löscht das Merkzeichen, auch bei ausgeschalteter Automatik. Sonst würde eine Bedienung in den 2 min nach dem Setzen als Übernahme statt „geändert“ gezählt.
- Text `applied` ohne „(gesetzt …)“, wenn die Automatik nie gesetzt hat (Werte stimmten schon).

## Watch out
- `on_manual_target` läuft unter der Sperre von `IntentService.apply`: nur Speicher ändern, Ablage in `driving_automation` erst in `tick()` (der Rückruf stößt ihn an).
- `set_enabled(on)` wartet auf eine Prüfung (`tick`), kann also so lange dauern wie ein Senden an den core.

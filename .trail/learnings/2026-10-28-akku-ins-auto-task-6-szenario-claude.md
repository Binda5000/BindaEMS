---
title: "Akku ins Auto Task 6: Szenario-Runner"
author: Claude
date: 2026-10-28
branch: claude/project-thread-c3k9h7
cost: { minutes: 22, tokens: 92562 }
id: l-618b4154
scope: repo
status: active
src: agent
touches:
  - src/bindaems/tools/scenario/runner.py
  - src/bindaems/tools/scenario/report.py
  - tests/integration/test_planning_scenarios.py
---

## Decided
- Runner (src/bindaems/tools/scenario/runner.py): Akkuanteil je Auto im angewandten Slot = min(PlanSlot.ev_battery_w[key], tatsächliche Ladeleistung nach _charge), Summe als SimSlot.ev_battery_w in step; das Akkumodell gibt ihn nur bei EIGENVERBRAUCH und nach dem Haus. Ohne gültigen Plan, der den Ansteckvorgang nennt: 0.
- car_grid_kwh (Regel R2 im Runner) zieht den Akkuanteil nach der PV ab, wie der Optimierer (R2 zählt bt_ev nicht). Ohne das meldet ein Plan, der das Auto aus dem Akku lädt, R2-Verstöße, die es nicht gibt.
- Nachrechnung je Plan (_plan_at, für Regel R) rechnet mit sim_slots(inputs, ev_w, ev_battery_w) wie plan_once.
- Verschleiß wear_ct je kWh Akku → Auto steht in DayResult.optimizer_ct und ScenarioResult.optimizer_ct (Entscheidung 10); die Spalte „Akku → Auto (kWh)“ im Bericht nur mit Autos und car_battery_allowed, damit alte Berichte gleich bleiben.

## Watch out
- Der Runner bildet die core-Regel „nur solange SOC > soc_end_pct des Slots“ (Entscheidung 4) nicht nach; braucht das Haus mehr als geplant, gibt das Modell dem Auto trotzdem den Akkuanteil (begrenzt durch Reserve und Entladegrenze). Szenarien damit eher pessimistisch für den Abend.
- Gemessen 28.10.2026, step_slots=4: sommer-sonnig 16,0 kWh Akku → Auto, bereinigt −2,65 € statt −0,24 €; winter-spread 0 kWh, unverändert 9,33 €. Datei tests/integration/test_planning_scenarios.py 129 s (Budget 150 s).

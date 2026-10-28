---
title: "Akku ins Auto Task 8: Sim-Szenarien und ihre Grenzen"
author: Claude
date: 2026-10-28
branch: claude/project-thread-c3k9h7
cost: { minutes: 29, tokens: 140699 }
id: l-03f610d2
scope: repo
status: active
src: agent
touches:
  - src/bindaems/tools/sim/scenarios.py
  - docs/betrieb.md
---

## Decided
- akku-uebrig-sofort: Tesla capped at 10 A via now_max_a. At 16 A (11 kW) rule 2 caps the override at max_grid_charge_setpoint_w (8 kW) once Spare locks, so the battery keeps feeding ~3 kW into the car and "grid >= car - 300 W" cannot hold (same cap akku-sofort checks).
- akku-uebrig-geplant: plan names 2760 W (6 A on two phases), not 2800 W. The core rounds the Geplant current up (charge_points.wanted_a, ceil(plan_w / (U*n))), 2800 W -> 7 A = 3220 W, while BatteryController gives at most ev_battery_w (decision 4): 420 W would come from the grid, above the 300 W tolerance.
- The third check of akku-uebrig-sofort reads Health battery.spare/spare_w, not the decision log.

## Watch out
- battery_spare only appears in the decision log as the reason of an override for a remainder (or when an existing override is lifted while spare_w > 0). When the battery covers a Sofort car's whole deficit there is no command, hence no decision (like Boost in boost-abend). A Geplant car whose plan power is not a whole-amp step does log battery_spare (override for the rounding remainder).
- Health battery.support reads "on" in akku-uebrig-sofort although no car is in PV/Min+PV; irrelevant there, but don't use it as evidence of support.

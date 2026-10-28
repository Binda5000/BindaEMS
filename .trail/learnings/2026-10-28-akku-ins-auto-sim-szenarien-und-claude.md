---
title: "Akku ins Auto: Sim-Szenarien und ihre Grenzen nach der Fix-Welle"
author: Claude
date: 2026-10-28
branch: claude/project-thread-c3k9h7
cost: { minutes: 97, tokens: 295903 }
id: l-5dc112da
scope: repo
status: active
replaces: l-03f610d2
src: agent
touches:
  - src/bindaems/tools/sim/scenarios.py
  - src/bindaems/core/control/battery.py
  - docs/betrieb.md
---

## Decided
- akku-uebrig-sofort: Tesla capped at 10 A via now_max_a. At 16 A (11 kW) rule 2 caps the override at max_grid_charge_setpoint_w (8 kW) once Spare locks, so the battery keeps feeding ~3 kW into the car and "grid >= car - 300 W" cannot hold (same cap akku-sofort checks).
- akku-uebrig-geplant names 2800 W again (fix wave F7, operator decision 28.10.2026, card "Stufe aus dem Akku"). The core still rounds Geplant up to whole amps (charge_points.wanted_a: 2800 W -> 7 A = 3220 W on the e-Golf), but allowed() now gives min(deficit, ev_battery_w + step_w) for a share > 0, step_w = U x phases of the charge point (CarView.step_w -> BatteryCar.step_w; 0 = unknown, fails closed). Before F7 the scenario used 2760 W (6 A) because 420 W came from the grid.
- With 3220 W from the battery the SOC drops ~3.6 points per 15-min slot, so the planned SOC ends moved from 56/53/52 to 55/52/51. With the old ends the share ended at 1769 s in slot 2 and came back in slot 3 (SOC > 52), failing check (a)/(b) on timing, not on grid. Tolerances unchanged.
- The third check of akku-uebrig-sofort reads Health battery.spare/spare_w, not the decision log.

## Watch out
- battery_spare only appears in the decision log as the reason of an override for a remainder (or when an existing override is lifted while spare_w > 0). When the battery covers a Sofort car's whole deficit there is no command, hence no decision (like Boost in boost-abend). Since F7 a Geplant car fully planned from the battery logs nothing either; only a deficit beyond share + one step does.
- If you change the load or the car power in akku-uebrig-geplant, re-fit PLANNED_SOC_END_PCT so the share still ends in slot 3 (PLANNED_SHARE_SLOT).
- Health battery.support reads "on" in akku-uebrig-sofort although no car is in PV/Min+PV; irrelevant there, but don't use it as evidence of support.

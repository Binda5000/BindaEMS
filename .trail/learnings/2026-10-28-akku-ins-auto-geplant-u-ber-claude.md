---
title: "Akku ins Auto: Geplant über den Optimierer, Sofort über die Plan-Schwelle"
author: Claude
date: 2026-10-28
branch: claude/project-thread-c3k9h7
cost: { minutes: 10, tokens: 464638 }
id: l-d500065d
scope: repo
status: active
src: agent
touches:
  - docs/superpowers/plans/2026-10-28-akku-ins-auto-wenn-uebrig.md
  - src/bindaems/app/planning/optimizer.py
---

## Decided
- Binda 28.10.2026 approved docs/superpowers/plans/2026-10-28-akku-ins-auto-wenn-uebrig.md: cars in Geplant/Sofort may draw from the house battery only when the energy is spare (house doesn't need it until the next PV refill) and battery power is cheaper than grid power in the slot. Switch planning.car_battery_allowed, default off.
- Geplant: MILP flow bt_ev (src/bindaems/app/planning/optimizer.py) costs wear_ct + margin_ct, blocked while R1's c_t says grid energy may be in the battery (else grid → battery → car bypasses R2 and loses ~12 %). Core follows plan's ev_battery_w only while SOC > the slot's soc_end_pct.
- Sofort: not in the MILP (unknown load). App flags slots (now_battery) where import_ct − wear − margin ≥ v/η_dis, v = feed-in of the first later slot with full battery and export, else end_value_ct. Core gives Sofort its whole deficit while SOC > the slot's plan threshold support_soc_pct (the SOC the plan still needs, R_t in builder.support_thresholds), 5-pt hysteresis.

## Tried and ruled out
- A fixed SOC threshold ("genug im Akku") for Sofort/Geplant: in winter it moves evening house energy into the car and the house buys it back later; the plan threshold already encodes "needed until next refill".

## Watch out
- builder.support_thresholds uses soc_start − soc_end per slot (negative when charging), so it means "needed until the next refill", not "needed until horizon end".
- IntentPlanSlot is extra="forbid": the new fields need core and app rolled out together.

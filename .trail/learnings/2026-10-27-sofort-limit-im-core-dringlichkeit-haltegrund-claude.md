---
title: "Sofort-Limit im core: Dringlichkeit, Haltegrund, JSON-Form"
author: Claude
date: 2026-10-27
branch: claude/project-thread-wi9jx5
cost: { minutes: 3, tokens: 27947 }
id: l-77bb3a0d
scope: repo
status: active
src: agent
touches:
  - "src/bindaems/core/control/charging.py#_urgent"
  - "src/bindaems/core/control/charging.py#_hold"
  - "src/bindaems/core/control/charge_points.py#now_capped"
  - "src/bindaems/shared/control.py#VehicleIntent"
  - ui/src/lib/api/contract/control.json
---

## Decided
- The cap replaces `ChargePoint.max_a` right after `charge_points()`, but `_urgent` must still judge with the uncapped `max_a` (`_Situation.uncapped_a`): with the capped point, a drop from 16 A to the limit at the fuse target (`PHASE_LIMIT`) counted as "what the fuse demands" and went out urgent, past the safety layer's min intervals. A lowering for the limit is never urgent; a guard below it still is.
- `_hold` (unknown plug state, unclear assignment) also lowers to the limit, with reason `now_limit` when the limit, not a guard, bounds it.

## Watch out
- `now_max_a` is dumped as `null` in every intent JSON: `tests/helpers.py` INTENT_CHARGING_DEFAULTS, intent-store tests and the UI contract files (control, control-reply, control-support, system; regenerate with `UPDATE_UI_CONTRACT=1`) changed with Task 1.
- A car in "Sofort" never counts in `unmet_surplus_w` (`_UNMET_MODES` is PV and Min+PV only), so the cap does not change it; the plan's test for that pins existing behaviour, it was not red.

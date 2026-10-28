---
title: "Vorklimatisieren Task 3: Regel K2 und ihre Mindestpause nach Überlast"
author: Claude
date: 2026-10-28
branch: claude/project-thread-xqrmsn
cost: { minutes: 19, tokens: 28875 }
id: l-c018eff7
scope: repo
status: active
src: agent
touches:
  - "src/bindaems/core/control/charging.py#keep_on_complete"
  - "src/bindaems/core/control/charging.py#_paused"
  - "src/bindaems/core/control/charging.py#_regulate"
  - tests/unit/core/control/test_charging.py
---

## Decided
K2 (keep_on_complete, module function in charging.py) sits in ChargeController.step in the else branch of K1, so K1 wins when both apply (a Tesla at the EVCS). When it holds: decision = ModeDecision(True, None), hold = KEEP_ON_COMPLETE, which is in _HOLD_STARTS, so a stopped EVCS gets current min_a then charging=True (only when carries and no peak start lock). Surplus participation is unchanged: can_charge still excludes a complete car, its draw is load.
The overload pause needed controller state: ModeMachine.force_stop sets a min_off_s pause, but rule 3 of the machine (complete -> off) answers before the pause is ever read, and K2 overrides the machine verdict anyway. Without own state, an EVCS_STOP at 6 s restarts at 67 s (when the overload clears) instead of 606 s. So _regulate records self._forced[key] = now next to force_stop, and _paused(key, now, vehicle) blocks K2 for control.min_off_s; it is called for every regulated car each cycle so unplugging (plugged False) clears it like the machine does. A mode change does not clear it (conservative; the machine would). ModeMachine stays untouched as the plan requires.

## Watch out
Tests for K2: the e-Golf without Tessie is the EVCS default vehicle; a guest at the EVCS is homes={'egolf': False}. evcs(complete=True) makes VehicleState.complete true for the whole plug session. With the default grid() there is surplus; use NO_SHARE (grid_w=0) for 'PV ohne Überschuss'.

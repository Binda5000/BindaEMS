---
title: "Vorklimatisieren Task 2: Regel K1 im ChargeController"
author: Claude
date: 2026-10-28
branch: claude/project-thread-xqrmsn
cost: { minutes: 1, tokens: 11583 }
id: l-1bbb2f5a
scope: repo
status: active
src: agent
touches:
  - "src/bindaems/core/control/charging.py#_regulate"
  - "src/bindaems/core/control/charging_tesla.py#climate_hold"
  - tests/unit/core/control/test_charging_tesla.py
---

## Decided
K1 sits in ChargeController.step right after _decide: climate_hold() true -> decision becomes ModeDecision(True, None), _Situation.hold = CLIMATE, AgainstMode.clear(key) every hold cycle and once when the key leaves self._climate. _regulate: after the overload branches, hold -> point.min_a with reasons (*head, hold), never urgent (_urgent skipped). may_start is false unless hold is in _HOLD_STARTS (empty for climate), which blocks both charging=True and the wake start for a Tesla that is only wakeable. A second hold rule that may start (keep_on_complete) adds its code to _HOLD_STARTS.

## Watch out
_self_start requires tessie.reachable, so an unreachable Tessie gets no self-start stop at all; a test of 'K1 needs a reachable Tessie' can only compare against the run without climate_on, not expect a stop after 120 s.
In self_start_run, quiet_until=0 makes the first cycle resume a charging Tesla (PV keeps it on until the surplus timers stop it at 300 s); for a self-start retry near 120 s use quiet_until=1 (retry at 121, next at 721).

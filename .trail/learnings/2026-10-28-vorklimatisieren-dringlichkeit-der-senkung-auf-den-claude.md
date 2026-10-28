---
title: "Vorklimatisieren: Dringlichkeit der Senkung auf den Haltestrom"
author: Claude
date: 2026-10-28
branch: claude/project-thread-xqrmsn
cost: { minutes: 11, tokens: 28875 }
id: l-8ba3f028
scope: repo
status: active
replaces: l-1bbb2f5a
src: agent
touches:
  - "src/bindaems/core/control/charging.py#_regulate"
  - "src/bindaems/core/control/charging.py#_urgent"
  - tests/unit/core/control/test_charging_tesla.py
---

## Decided
The cut to the hold current (_Situation.hold, rules K1 climate and K2 keep_on_complete) is urgent when the fuse alone demands it, like the normal branch (Präzisierung 11): _regulate calls _urgent(situation, point, point.min_a, present_a, point.max_a). The wish is point.max_a, as in _hold, not situation.wanted: a held car cannot charge (complete, or its mode does not want to), so it is not in can_charge and cycle.wanted is 0. With wanted 0 and the fuse as target (PHASE_LIMIT), _current_a(point, 0, limit) is min_a < present, so every hold cut would be urgent even with ample free current. This corrects "never urgent" in the Task 2 learning; the rest of it holds: K1 sits in step right after _decide, hold -> point.min_a with (*head, hold), may_start only for codes in _HOLD_STARTS.

## Watch out
In PV, Min+PV and Planned without firm demand the guard target is the import limit (phase_target), so _urgent is false there unless a peak cap with fuse_a; a test for a fuse-urgent hold needs Sofort/Boost. A held car in Sofort exists when it is complete in its plug session (sticky VehicleState.complete). To make the fuse demand a cut without the OverloadGuard (MINIMUM after 3 s over fuse_target_a), lower the free current via the battery feed: grid(l1=20, feed=(20,0,0)) gives 33 - (20 + 20 - 16) = 9 A free while the grid import stays at 20 A.
Carried over from the Task 2 learning (K1): _self_start requires tessie.reachable, so an unreachable Tessie gets no self-start stop at all; a test of 'K1 needs a reachable Tessie' can only compare against the run without climate_on. In self_start_run, quiet_until=0 makes the first cycle resume a charging Tesla; for a self-start retry near 120 s use quiet_until=1 (retry at 121, next at 721).

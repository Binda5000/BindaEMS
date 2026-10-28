---
title: "Vorklimatisieren Fix-Welle: Halteregeln ruhen unter Überlast-Pause und 15-min-Grenze"
author: Claude
date: 2026-10-28
branch: claude/project-thread-xqrmsn
cost: { minutes: 93, tokens: 88320 }
id: l-126a5f87
scope: repo
status: active
replaces: l-c018eff7
src: agent
touches:
  - "src/bindaems/core/control/charging.py#_paused"
  - "src/bindaems/core/control/charging.py#step"
  - "src/bindaems/core/control/charging.py#_unmet"
  - "src/bindaems/core/control/charging.py#_decide"
  - tests/unit/core/control/test_charging.py
  - tests/unit/core/control/test_charging_tesla.py
---

## Decided
K1 (climate) and K2 (keep_on_complete) in ChargeController.step both run only when holds = not _paused(...) and not short, where short = cause is GuardCause.PEAK_GUARD and free_a < point.min_a (free_a after the 15-min cap). Why: peak_guard assumes it can throttle a car to 0 W, so a hold at min_a would defeat it; K1 then falls back to the self-start stop, K2 to 'Laden aus' as for 'geladen' (final review I1).
The overload pause of the hold rules lives in the controller, not the ModeMachine (rule 3 'complete -> off' answers before the machine's pause is read, and the holds override the verdict). self._forced maps key -> (stopped_at, mode). It must end exactly like the machine's pause (Präzisierung 8): mode change (_paused compares mode), unplugging (plugged False in _paused), a new or remembered session (_decide pops it on new_session or reached; the regulation may never see the unplug when the point was unregulated meanwhile) and idle() (take-over). A K1 car is also blocked by the pause (M1); otherwise it gets 5 A again as soon as the overload level clears.
_unmet treats a key in self._climate as commanded 0 A (like 'Laden aus'): after a new session or a level escalation the last own charging value is forgotten, the Tesla reports charging, and it would count as following 5 A with ~7.6 kW unmet wish, pushing the AC back (M2).

## Watch out
A plain 'back' (point unregulated for a while without idle) still keeps the hold pause, while the machine would take over and drop its own pause; the ruling only asked for idle(). The sim check _no_stop_between has an exclusive end (begin <= ts < end).

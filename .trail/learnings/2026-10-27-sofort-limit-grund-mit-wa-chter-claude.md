---
title: "Sofort-Limit: Grund mit Wächter zwischen Limit und Wunsch"
author: Claude
date: 2026-10-27
branch: claude/project-thread-wi9jx5
cost: { minutes: 97, tokens: 166649 }
id: l-5dbe9072
scope: repo
status: active
src: agent
touches:
  - "src/bindaems/core/control/charging.py#_why"
  - "src/bindaems/core/control/charging.py#_limit_reasons"
  - "src/bindaems/core/control/charging.py#_hold"
---

## Decided
- Controller ruling in the fix wave after the final review: when the Sofort limit bounds the commanded value and a guard lies between the limit and the uncapped max (limit 10 A, phase guard 12 A, present 16 A), the reasons are (`mode_now`, `now_limit`, <guard cause>) on the EVCS and the Tesla alike, in `_why` and `_hold` (`_limit_reasons`). Value and urgency stay: the lowering is urgent because the guard alone would have lowered below present.
- Before, the EVCS said only `now_limit` (wanted equals the capped max, so `limit < wanted` never fired) while the Tesla at the Wall Connector said only `phase_limit` (its window average still held the old 16 A, so `limit < wanted` fired although the value was bounded by the capped `max_a`). `_why` therefore needs the capped `max_a`: "the limit bounds the value" is `min(wanted, limit) >= max_a`, not `limit >= wanted`.

## Watch out
- With unknown free current (limit = `min_a`) and a limit at the point minimum, the guard cause now follows `now_limit`; that matches what the uncapped car would say.

---
title: "Sofort-Limit: Strom in A je Auto und Gast, harte Grenze, Boost bleibt voll"
author: Claude
date: 2026-10-27
branch: claude/project-thread-wi9jx5
cost: { minutes: 11, tokens: 208344 }
id: l-36187950
scope: repo
status: active
src: agent
touches:
  - docs/superpowers/plans/2026-10-27-sofort-ladelimit.md
---

## Decided
- Binda 27.10.2026 approved plan docs/superpowers/plans/2026-10-27-sofort-ladelimit.md with one change: limit also for guests (`GuestIntent.now_max_a`), not only own cars.
- Unit is whole amps per phase (`now_max_a`, None = full power), not kW: wallboxes switch whole A and the same kW means different A for Tesla (3 phases) and e-Golf (2 phases). UI shows kW next to each A.
- Limit is persistent per car (like target SOC), applies only in "Sofort"; Boost stays full power, "Geplant" untouched (latest start assumes full power).
- Hard cap even with PV surplus; the rest goes to other cars/AC/battery. "Limit plus PV" would be Min+PV with its own minimum, not this feature.
- Core caps `ChargePoint.max_a` (now_capped) right after `charge_points()` for regulated points in Effective.NOW, so surplus sharing, `_hold`, `_unmet` (AC gets the rest) all see the capped value.

## Watch out
- `Intent` models use extra="forbid": the new field means core and app must roll out together; rollback of the core needs `now_max_a` removed from intent.json (null is still dumped).
- Guest at the Tesla Wall Connector has no actuator; the guest limit only acts at the EVCS. Guest range = EVCS min_a..max_a.

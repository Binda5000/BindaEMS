---
title: "Vorklimatisieren: Selbststart-Regel stoppt den klimatisierenden Tesla, EVCS geht nach „geladen“ aus"
author: Claude
date: 2026-10-28
branch: claude/project-thread-xqrmsn
cost: { minutes: 9, tokens: 81909 }
id: l-652c3d9b
scope: repo
status: active
src: agent
touches:
  - docs/superpowers/plans/2026-10-28-vorklimatisieren-ueber-wallbox.md
---

## Decided
Binda 28.10.2026 approved plan docs/superpowers/plans/2026-10-28-vorklimatisieren-ueber-wallbox.md (11 decisions as proposed). Rule K1: Tesla with Tessie climate on (is_climate_on or is_preconditioning) and charging → no stop, min_a, reason `climate`, no own "Laden an" (only tolerate; evcc-style active start was offered and not chosen). Rule K2: car at the EVCS that is `complete` in its session → EVCS stays on at min_a, reason `keep_on_complete`. Both after the ModeMachine verdict in ChargeController.step; not in mode Off, not for guests, switch vehicles.<key>.climate_via_wallbox (default true).

## Watch out
Cause (from code, plant log not yet confirmed): `_switch_off` only sends when the last own value changes, so the repeated stop of a self-charging Tesla comes from `_self_start` (control.tesla_against_s 120 s, retry 600 s) in core/control/charging.py. If Tessie did not report "Charging" while climatising, BindaEMS would send nothing at all.
`plan.reached` in "Geplant" is not "full": treating it like `complete` for K2 would keep the e-Golf charging at min_a past its target.
After K1 ends, clear AgainstMode for the key, otherwise a self-start retry shortly before can delay the stop by up to 600 s.

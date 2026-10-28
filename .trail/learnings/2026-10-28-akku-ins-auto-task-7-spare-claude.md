---
title: "Akku ins Auto Task 7: Spare im core startet frei, Health aus assess"
author: Claude
date: 2026-10-28
branch: claude/project-thread-c3k9h7
cost: { minutes: 49, tokens: 164839 }
id: l-6d9125e6
scope: repo
status: active
src: agent
touches:
  - src/bindaems/core/control/battery.py
  - src/bindaems/core/control/plan_exec.py
  - src/bindaems/core/control/regulation.py
  - docs/betrieb.md
  - ui/src/lib/api/contract/system.json
---

## Decided
- `Spare` (src/bindaems/core/control/battery.py) starts unlocked and `reset()` unlocks again, unlike `Support`, which starts locked until the SOC reaches the threshold. The brief's test wants a fresh controller at SOC 72 % with plan threshold 70 % to give "on"; locking only happens when the SOC reaches the threshold (≤), and it frees at threshold + `support_hysteresis_pct` (≥).
- The SOC state of `Spare` follows every slot that has a `support_soc_pct`, even with `now_battery` false or under HALTEN/NETZLADEN, the same way `Support` tracks SOC whatever the reason is. A slot without a plan threshold gives "soc", never "on".
- Health `battery.spare`/`spare_w` are stored in `assess` (like `support_soc_pct`), not in `decide` like `support`. `spare` is known even when PV is unknown; `spare_w` is None without a need.
- The Geplant share and Sofort spare do not count against `support_max_w`. `BatteryNeed.spare_w` (sum of Geplant + Sofort shares) drives the `battery_spare` reason, which comes before `battery_support`.

## Watch out
- Adding a key to `BatteryController.health()` breaks `tests/unit/test_docs.py::test_json_examples_of_the_battery_section_are_valid` (it compares the docs JSON example keys with `health()`) and the system contract in `tests/integration/test_ui_contract.py`. Update docs/betrieb.md "Akku und Spitzen" → Health and regenerate with `UPDATE_UI_CONTRACT=1`.
- `ui/src/lib/api/contract/settings.json` was already out of date since Task 1 (`car_battery_allowed`). It is left for Task 9, which owns the contract files.

---
title: "Vorklimatisieren Task 4: Szenarien tesla-vorklimatisieren und egolf-voll-bleibt-an"
author: Claude
date: 2026-10-28
branch: claude/project-thread-xqrmsn
cost: { minutes: 40, tokens: 8436 }
id: l-72cabfce
scope: repo
status: active
src: agent
touches:
  - "src/bindaems/tools/sim/scenarios.py#_tesla_vorklimatisieren"
  - "src/bindaems/tools/sim/scenarios.py#_egolf_voll_bleibt_an"
  - "src/bindaems/tools/sim/scenarios.py#_current_at_most"
  - tests/unit/test_docs.py
---

## Decided
The two scenarios are appended at the END of SCENARIOS (seeds 44 and 45), not after tesla-selbststart: the index slices in tests/integration/test_sim_scenarios.py ([23:28] PLAN etc.) and test_sim_battery_checks.py stay valid; only the KLIMA slice [-9:] became [-11:-2]. The current check _current_at_most reads the requested current from the decision log, not the car samples: the value of the last non-held CURRENT decision before 7 min plus all later ones until 25 min, so a missing command before 7 min fails (the car would keep its 10 A). _evcs_on_when_complete derives 'geladen' from the first sample with CarSample.complete (EVCS status 3 follows at once), so _evcs_off_from could not be reused (it is the opposite check and takes a fixed begin). _setup got egolf_soc_pct for the e-Golf at 98 %; it is complete after ~12 min (second ~734) in MORNING with PV, well inside 30 min.

## Watch out
tests/unit/test_docs.py::test_simulator_section_matches_the_tool ties the scenario table in docs/betrieb.md (Simulator) to list(SCENARIOS) in order and counts the number word (now fuenfundvierzig, three times): a new scenario needs its table row and the word updated, or the docs test fails, not just the integration list. RED check without the core rules (git checkout fb3f982d -- charging.py charging_tesla.py charge_modes.py decisions.py): both scenarios fail with the expected findings (Laden aus bei 424 s; EVCS Laden aus bei 734 s, letzter Strom 11 A).

---
title: "Volle pytest-Suite mit Abdeckung dauert in der Cloud über 30 min: in Gruppen teilen und coverage combine"
author: Claude
date: 2026-10-28
branch: claude/project-thread-xqrmsn
cost: { minutes: 42, tokens: 236987 }
id: l-f1e57118
scope: repo
status: active
src: agent
touches:
  - deploy/config.example.yaml
  - src/bindaems/core/adapters/tessie.py
  - src/bindaems/core/control/charge_inputs.py
  - src/bindaems/shared/config.py
  - tests/fixtures/tessie_state.json
  - tests/unit/core/adapters/test_tessie.py
  - tests/unit/core/control/test_charge_inputs.py
  - tests/unit/shared/test_config.py
  - docs/superpowers/plans/2026-10-28-vorklimatisieren-ueber-wallbox.md
  - docs/uebergabe.md
---

## Watch out
In the cloud container `uv run pytest -q --cov` in one go took over 30 min on 28.10.2026 (9139 tests; tests/integration alone ~27 min) and a background subagent run was killed by its time limit. Equivalent and faster: four parallel pytest processes with separate coverage files (`COVERAGE_FILE=.coverage.<n>`), groups tests/integration | tests/property + tests/e2e + tests/unit/tools + tests/unit/shared + tests/unit/cerbo_watchdog + top-level | tests/unit/app | tests/unit/core, then `uv run coverage combine` and the two `coverage report` gates from docs/uebergabe.md „Befehle“ (≥ 75 % total, ≥ 90 % safety/control/actuators).
Also: a fresh container needs `uv sync --all-extras --all-groups` (pytest is in the `dev` extra); `uv sync --all-groups` alone leaves `uv run pytest` falling back to a global pytest without bindaems.

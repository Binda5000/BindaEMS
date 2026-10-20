---
title: "Plan 3d: Regel R1 nur bis der Akku leer ist"
author: Claude
date: 2026-10-20
branch: claude/project-thread-707vv8
cost: { minutes: 110, tokens: 227393 }
touches:
  - docs/betrieb.md
  - docs/superpowers/specs/2026-10-08-bindaems-design.md
  - docs/uebergabe.md
  - src/bindaems/tools/scenario/runner.py
  - src/bindaems/app/planning/plausibility.py
  - tests/unit/app/planning/test_plausibility.py
  - tests/unit/test_docs.py
  - tests/unit/app/planning/test_optimizer.py
  - src/bindaems/app/planning/fallback.py
  - tests/unit/app/planning/test_fallback.py
---

## Decided
- Binda 20.10.2026: R1 soll nur verhindern, den Akku mit Netzstrom zu füllen, wenn noch genug PV kommt. Umsetzung: Sperre vom NETZLADEN-Slot bis zum Ende des ersten Slots mit SOC ≤ lo + Toleranz (Optimierer 0,05 kWh `R1_EMPTY_TOL_KWH`, Nachrechnung 0,1 kWh `EMPTY_TOL_KWH`), sonst bis Horizontende.
- Optimierer: Binär e_t (leer), stetiges c_t (Netzenergie im Akku): c_t ≥ z_t, c_t ≥ c_{t−1} − e_{t−1}, pv_x_t ≤ PV_t·(1 − c_t). Lösezeit und Status in den Szenarien unverändert.
- „Leer“ ist das richtige Kriterium: Mit und ohne Netzladen laufen die SOC-Verläufe erst an der Reserve zusammen.

## Tried and ruled out
- Kürzung in `_keep_rule_r1` um die Einspeisung im Fenster: unter der neuen Regel ist das oft PV über der Ladeleistung und kürzt viel zu viel. Jetzt höchstens die Ladung über lo + 0,1 kWh vor der ersten Einspeisung, geteilt durch η_ch.

## Watch out
- Szenario-Werkzeug: Jeder Plan hat sein eigenes lo = min(Reserve, SOC zur Planzeit). Die Verstoßprüfung muss das lo des Plans nehmen, nicht das des Laufs (sonst falsche Verstöße bei Start unter der Reserve).
- Der Optimierer landet genau auf lo + 0,05 (+1e-16): Tests brauchen ein Epsilon an der Leer-Grenze, sonst flackert der Property-Test.
- Ersatzplan: nach jedem Versuch R1 für alle NETZLADEN-Slots prüfen; ein früherer Slot kann den Leerstand vor einem schon angenommenen anheben.

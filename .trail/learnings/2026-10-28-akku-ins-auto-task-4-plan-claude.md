---
title: "Akku ins Auto Task 4: Plan, Plausibilität und Sofort-Flag"
author: Claude
date: 2026-10-28
branch: claude/project-thread-c3k9h7
cost: { minutes: 4, tokens: 15049 }
id: l-1041c509
scope: repo
status: active
src: agent
touches:
  - src/bindaems/app/planning/spare.py
  - src/bindaems/app/planning/plausibility.py
  - src/bindaems/app/planning/intents.py
  - src/bindaems/app/planning/builder.py
  - src/bindaems/app/planning/service.py
---

## Decided
- check_plan (src/bindaems/app/planning/plausibility.py) liest den Akkuanteil aus result (ev_battery_power_w), nicht aus einem eigenen Parameter: Die Flüsse-Prüfungen brauchen ihn ohnehin. Die Leistung je Auto (Grenzen min_w/max_w) und die Entladegrenze zählen bt_ev mit; vorher meldete ein Auto mit 1 kW Netz + 3 kW Akku „außerhalb seiner Grenzen“, weil nur pv + grid zählte.
- „Netzstrom im Akku“ ist grid_in_battery(intents, steps, lo_kwh) in plausibility.py (Fenster charged_window_end je NETZLADEN-Slot); spare.now_battery_flags und check_plan rechnen beide damit, an der Nachrechnung, nicht am c_t des Optimierers.
- builder._guard_steps (Budget ohne erwartete Autos) behält den Akkuanteil der angesteckten Autos; sonst stiege das Budget um ihre ganze Leistung.
- baseline_cost_ct bleibt ohne Akkuanteil (Entscheidung 10).

## Watch out
- derive_intents macht einen Slot nur ab HOLD_MIN_W (100 W) Akkuanteil zu EIGENVERBRAUCH; ein Slot mit 1–100 W bt_ev und kaum Hausentladung würde HALTEN und check_plan meldete „bei Halten oder Netzladen“ (Ersatzplan). Praktisch kommt das nicht vor: bt_ev kostet Verschleiß plus Marge, bt_h nur Verschleiß, der Optimierer deckt also zuerst das Haus. Probe: 400 zufällige Horizonte aus tests/property/test_plan_plausible.py mit Schalter an, 64 mit Akku ins Auto, alle plausibel.

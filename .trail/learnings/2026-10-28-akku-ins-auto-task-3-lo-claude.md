---
title: "Akku ins Auto Task 3: Lösezeit der R1-Sperre für bt_ev"
author: Claude
date: 2026-10-28
branch: claude/project-thread-c3k9h7
cost: { minutes: 16, tokens: 10736 }
id: l-298d0a57
scope: repo
status: active
src: agent
touches:
  - src/bindaems/app/planning/optimizer.py
  - tests/unit/app/planning/test_optimizer.py
  - tests/property/test_optimizer.py
---

## Decided
- Optimierer (src/bindaems/app/planning/optimizer.py): bt_ev je Auto und Slot nur mit planning.car_battery_allowed; Sperre r1_auto_t: Σ bt_ev ≤ Σ max_w · (1 − c_t), dasselbe c_t wie Regel R1 (Entscheidung 2, wörtlich). Mit Schalter aus keine neuen Variablen oder Bedingungen, Lösezeit wie vorher.
- Zusätzlich zur Vorgabe: auto_netz_t zählt auch bt_ev (Lädt ein Auto aus dem Akku, geht keine PV in Akku oder Netz). Ohne das entlädt der Plan bei hoher Einspeisung und Restwert 0 den Akku ins Auto und speist die PV ein, also Akku ins Netz; ESS kann das physisch nicht (PV deckt zuerst das Auto). Test test_battery_to_car_never_while_pv_goes_to_grid.
- Trägheit: net = pv_b + gr_b − bt_h − Σ bt_ev, weil die Nachrechnung (Task 2) bt_ev in discharge_w führt und prev_battery_w daraus kommt.

## Tried and ruled out
- Die Sperre macht das MILP mit Netzladen an schwer: zufällige 96/144-Slot-Fälle mit Preisen 5–40 ct je Slot 0,2 s → 13 s bzw. Zeitlimit 20 s. Ursache ist die schwache LP-Relaxation (z, e gebrochen, c fällt). Ohne r1_auto unter 0,5 s.
- c_t binär statt stetig: schlechter (zwei Fälle am Zeitlimit).
- Energie-Schnitt g_t (Netzenergie, Haus verbraucht sie zuerst; Auto nur soc − lo − g): als Zusatz gemischt (13→7,5 s, ein anderer Fall 0,9→2,4 s), als Ersatz schnell, aber erlaubt Netz → Haus und PV → Auto bei Netzenergie im Akku, also den R2-Umweg, den Entscheidung 2 verbietet.
- Lazy Constraints (erst ohne Sperre lösen, verletzte Slots nachtragen): viel langsamer, die Verletzungen wandern von Runde zu Runde.

## Watch out
- Realistische Szenarien (synthetic, stündlich neu geplant): sommer-sonnig, winter-spread, wechselhaft praktisch unverändert; winter-leistung 87 s → 112 s Summe, Läufe am Zeitlimit 4 → 5 (schon vorher am Zeitlimit wegen Leistungspreis).

---
title: "Plan 5c Task 4: Grundleistung mit Regelabstand, Phasen als Betrag"
author: Claude
date: 2026-10-27
branch: claude/project-thread-x8izsc
cost: { minutes: 40, tokens: 63848 }
id: l-8b6198e7
scope: repo
status: active
replaces: l-70c438d0
src: agent
touches:
  - src/bindaems/app/learning/thermal_model.py
  - tests/unit/app/learning/test_thermal_model.py
---

## Decided
- `thermal_model.py` `_fit_base`: Die Grundleistung hat einen vierten Regressor, den Regelabstand (Heizen Soll ohne Versatz − Ist, Kühlen Ist − Soll): `p_w ≈ u·ΔT + s·pv_kw + k·Abstand + b`; `P₀` und `base_power_w` rechnen mit Abstand 0, `ModeModel.gap_w_per_k` hält `k`. Grund: Mit „Ist–Soll höchstens 0,5 K“ fällt das Nachheizen nach jeder Auskühl-Phase (rund `C · 0,5 K`) in die Anpassung; ohne den Abstand lag im Testhaus `s` bei −114 statt −60 W/kW und `b` bei 328 statt 40 W, mit ihm liegen u, s, b in 3 %.
- PV oder Abstand ohne Streuung (unter `CONSTANT_TOL`) fallen aus der Anpassung, `s` bzw. `k` bleiben 0; Gleitkomma-Reste ohne Toleranz machten die Matrix rangarm und das Modell leer.
- `free_float_episodes` nimmt den Betrag |innen − außen| ≥ 3 K und `ln |innen − außen|`, für beide Betriebsarten gleich (Ruling, Plan wörtlich); die Richtung zählt nicht.
- Versatz-Ereignisse: Wert des Slots nach dem Sprung (Viertelstundenmittel), 30 min gehalten; Energie geteilt durch `Versatz · Richtung` (Heizen +1, Kühlen −1), damit auch Absenken einen positiven Speicher ergibt. `u ≤ 0` ist `poor_fit`; nicht abklingende Phase `tau_h = inf`, Modell dann `tau_h = None`.

## Watch out
- Testhaus: Auskühlen abends 18–22 Uhr (ohne PV, Außentemperatur dann konstant), Regelung Sollleistung + 3000 W/K · Abstand; mit Regelung ohne Nachlauf und Pause 0–4 Uhr lag R² unter 0,5.

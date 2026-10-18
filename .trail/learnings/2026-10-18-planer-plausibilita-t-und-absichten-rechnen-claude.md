---
title: "Planer: Plausibilität und Absichten rechnen Netzladen gleich"
author: Claude
date: 2026-10-18
branch: claude/phase-4-plan-whnbh6
cost: { minutes: 60, tokens: 231480 }
touches:
  - src/bindaems/app/planning/plausibility.py
  - src/bindaems/app/planning/intents.py
  - tests/property/test_plan_plausible.py
  - docs/superpowers/specs/2026-10-08-bindaems-design.md
---

## Decided
- Betreiber 18.10.2026: Optionen B und C. Spread-Regel zählt spätere Entladung > 1 W (USED_ABOVE_W); 100 W nur noch für Erklärungen (MENTION_W, vorher USEFUL_W).
- derive_intents streicht NETZLADEN, das an der Nachrechnung nicht rechnet (_drop_unprofitable, je Runde der letzte), mit grid_charge_pays aus plausibility.py; Slot bekommt die Absicht ohne Netzladen.
- check_plan bleibt streng; im Betrieb prüft plan_once nur Optimierer-Absichten (Ersatzplan läuft nicht durch check_plan).
## Watch out
- Hypothesis löscht ein gespeichertes Beispiel, sobald es besteht: Property-Tests mit HYPOTHESIS_STORAGE_DIRECTORY auf einer Kopie von .hypothesis/ laufen lassen, wenn das Beispiel bleiben soll.
- Synthetische Flüsse mit flachem Preis und Netzladen fallen jetzt in den Absichten weg; Tests brauchen Spread oder Restwert.

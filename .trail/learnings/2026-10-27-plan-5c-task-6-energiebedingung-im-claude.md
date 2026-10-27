---
title: "Plan 5c Task 6: Energiebedingung im LP der Verschiebung"
author: Claude
date: 2026-10-27
branch: claude/project-thread-x8izsc
cost: { minutes: 65, tokens: 192872 }
id: l-2cb38893
scope: repo
status: active
src: agent
---

## Decided
- `thermal_shift._solve` hat über Präzisierung 6 hinaus die Energiebedingung `S_{−1} + Σ (up − dn) · Δ / 1000 ≥ 0`. Grund: Mit der Rekursion `S_t = a · S_{t−1} + …` führt `a` ein abgesenktes Haus (`S < 0`) ohne Strom zum Soll zurück; das wörtliche LP senkte deshalb schon bei gleichem Preis den ganzen Horizont um `down_k` ab (Testhaus 96 Slots zu 20 ct: 21 ct „Ersparnis“, mit C 3 kWh/K und 30 ct 47 ct) und widersprach `test_flat_price_is_not_worth` und `test_shift_energy_balance_within_losses` (Σ thermal_w·Δ ≥ 0). Mit der Bedingung ist der Fall „lohnt nicht“, das Ergebnis der Vorheiz-Szenarien bleibt gleich.
- `steps` rundet genau dazwischen Richtung 0 wie `core/safety/ranges.py` `_check_offset`, nach `round(x, 6)` gegen Reste des Solvers.

## Watch out
- Auch mit der Bedingung bleibt das Haus nach dem teuren Abschnitt bis kurz vor Horizontende abgesenkt, wenn die Preise danach gleich sind: späteres Nachheizen ist wegen `a` billiger. Wer früheres Zurückkehren will, braucht einen Komfortterm.
- Mehr- und Minderverbrauch im selben Slot heben sich in `S` auf: Ein Plan, der in einem Slot zugleich einspeist und teurer bezieht (oder aus dem Akku über dem Einspeisepreis entlädt), ergäbe eine Scheinersparnis.

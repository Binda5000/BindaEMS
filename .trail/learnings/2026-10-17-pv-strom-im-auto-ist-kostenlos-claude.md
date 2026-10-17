---
title: PV-Strom im Auto ist kostenlos
author: Claude
date: 2026-10-17
branch: claude/phase-4-plan-whnbh6
cost: { minutes: 4, tokens: 32889 }
touches:
  - docs/superpowers/plans/2026-10-17-phase-4a-statistik.md
---

## Decided
- Betreiber (17.10.2026): PV-Strom, der ins Auto geht, direkt oder über den Akku, kostet 0 ct. Abweichend von Spec 11.4 (dort OeMAG-Wert). Einstandspreis des Akkus = Netzanteil zum Bezugspreis + Verschleiß; Kosten je Auto = Netz × Bezugspreis + Akku × Einstandspreis.

## Tried and ruled out
- PV-Ladung mit dem OeMAG-Wert bewerten (Spec 11.4): vom Betreiber abgelehnt.

## Watch out
- Einspeiseerlös und Ersparnis der Tagesbilanz bleiben mit OeMAG; die Regel gilt nur für Kosten je Auto und den Einstandspreis.

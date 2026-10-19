---
title: "Plan 4b umgesetzt: Freigabe und Fallen der Fix-Welle"
author: Claude
date: 2026-10-19
branch: claude/phase-4b-plan-agf1qj
cost: { minutes: 225, tokens: 555726 }
touches:
  - docs/superpowers/plans/2026-10-18-phase-4b-geplantes-laden.md
  - "src/bindaems/shared/plan.py#intent_plan"
  - "src/bindaems/core/control/charging.py#_slot_w"
  - "src/bindaems/app/planning/service.py#collect"
  - "src/bindaems/app/planning/builder.py#vehicle_summary"
  - "src/bindaems/app/charging/planned.py#_session_ended"
---

## Decided
- Plan 4b „Geplantes Laden“ ist zur Umsetzung freigegeben (Betreiber, 18.10.2026: „passt so, du kannst beginnen“) und auf claude/phase-4b-plan-agf1qj umgesetzt. Nur das Ausrollen an der Anlage wartet auf die Abnahme der Phase 3; dieser Eintrag ersetzt „Umsetzung erst nach der Abnahme von Phase 3“ aus dem Learning vom 18.10.
- Ruling R22: `intent_plan` schreibt jedes Auto aus `plan.vehicles` in jeden Slot vor seiner Abfahrt, auch mit 0 W. Der core liest `active.vehicles.get(key)`: Ein Auto, das der gültige Slot nicht nennt, hat keinen Slot (`None`) und folgt der spätesten Startzeit. Pläne aus 4a nennen kein Auto.
- Ruling R23: Lief ein Plan, während Health die Absicht noch nicht bestätigt hatte (`intent_version` < Version), und steht ein Auto in „Geplant“, fordert der Planungsdienst „vehicles“ an, sobald Health sie bestätigt.
- Ruling R20/R24: Abfahrt hinter dem Horizont: `finish_at` ist leer, solange der Plan den Bedarf nicht im Horizont lädt; `expected_soc_pct` gilt an der Abfahrt (plus volle Leistung nach dem Horizont).

## Tried and ruled out
- `active.vehicles.get(key, 0)` im core: macht „Plan kennt das Auto nicht“ zu „0 W, nur PV“, bis der Plan abläuft (bis 45 min), entgegen Entscheidung 4.
- Den Fingerabdruck der Autos im untrusted Fenster neu zu rechnen: lässt den Auslöser „vehicles“ bei jedem Neustart des core flattern; deshalb das Merkflag in `collect`.

## Watch out
- Nach jeder neuen Absicht (gesendeter Plan, Bedienung) regelt der core bis zum nächsten Health (bis 10 s) mit der alten; alles, was Health nur mit `_trusted` liest, sieht in diesem Fenster keine Autos.
- Die Zählung `planned_charge` an einer Session-Lücke: Schließt der Tracker wegen einer Lücke (Ende mehr als `SESSION_GAP` vor jetzt), ist „abgesteckt“ unbekannt; nie `early`, und `reached_at` gilt zuerst.
- Teil-Commits mit `git apply --cached --unidiff-zero` setzen reine Einfügungen an falsche Stellen; besser den Inhalt für den Index aus HEAD plus genau der Änderung bauen.

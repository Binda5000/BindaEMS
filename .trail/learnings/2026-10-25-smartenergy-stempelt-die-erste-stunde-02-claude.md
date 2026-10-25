---
title: "smartENERGY stempelt die erste Stunde 02:00 an der Winterzeit-Rückstellung mit +01:00"
author: Claude
date: 2026-10-25
branch: claude/project-thread-smtcnd
cost: { minutes: 10, tokens: 107695 }
id: l-bc2a4a32
scope: repo
status: active
src: agent
touches:
  - docs/uebergabe.md
  - src/bindaems/app/prices/sources.py
---

## Decided
Am 25.10.2026 lieferte die smartENERGY-API 100 Einträge in richtiger Reihenfolge, aber beide Stunden 02:00–02:45 mit +01:00 (die erste ist Sommerzeit, +02:00). Abgleich mit Energy-Charts (Stundenmittel × 1,2) bestätigt: erste Stunde = 00:00 UTC, zweite = 01:00 UTC. Fix in `SmartEnergySource.parse` (`_fold_ambiguous` in src/bindaems/app/prices/sources.py): eine mehrdeutige Wiener Ortszeit, die genau zweimal kommt, bekommt nach Reihenfolge fold=0/fold=1. Einzelne oder mehr als zwei Vorkommen bleiben wie angegeben, die Plausibilitätsprüfung meldet sie.

## Tried and ruled out
Fehler in der Plausibilitätsprüfung (checks.py) vermutet: rechnet schon in UTC mit 92/96/100 Slots, war richtig. Energy-Charts und aWATTar nutzen Unix-Zeit und sind nicht betroffen.

## Watch out
Den Frühjahrstag (23 h) von smartENERGY hat noch niemand aufgezeichnet; Ende März 2027 die Antwort prüfen und als Fixture ablegen.

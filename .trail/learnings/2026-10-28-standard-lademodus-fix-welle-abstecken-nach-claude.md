---
title: "Standard-Lademodus Fix-Welle: Abstecken nach Lücke bei unbekanntem Steckzustand"
author: Claude
date: 2026-10-28
branch: claude/project-thread-7dml9y
cost: { minutes: 40, tokens: 108580 }
id: l-3bb9d4f0
scope: repo
status: active
src: agent
touches:
  - "src/bindaems/app/charging/tracker.py#ChargingTracker.record"
  - "src/bindaems/app/charging/default_mode.py#DefaultModes._try"
---

## Watch out
- Die erste Beobachtung des `ChargingTracker` nach App-Start oder Wiederverbinden zum core hat fast immer `connected=None`, weil `core_link._health_loop` die Health-Handler auch ohne verbundenen Stream ruft und `LiveState.value` dann `None` liefert. Eine Regel, die nur auf `connected is False` in derselben Beobachtung schaut, verpasst deshalb das Abstecken während eines Ausfalls. Darum merkt sich `record` nach einer Lücke (`SESSION_GAP`) bei unbekanntem oder fehlendem Ladepunkt `(Fahrzeug, last_seen)` in `_gap_closed` und meldet beim ersten `False`; `True` verwirft es.

## Decided
- `_gap_closed` wird nicht wie `_open`/`_assigned` im Fehlerpfad von `record` geleert, sondern transaktional geführt (Kopie, Übernahme erst nach erfolgreichem Commit), weil es die gemerkten Abstecken nicht in der Datenbank gibt: Leeren nach einem Fehler würde ein schon geschlossenes, noch nicht gemeldetes Abstecken verlieren.
- `DefaultModes._try`: Scheitert ein Versuch erst beim Senden (`CoreDown`, `ConflictAgain`), wird der gelesene Modus der neue Maßstab `before`, weil sonst ein veralteter oder leerer Cache beim Abstecken den Wunsch fälschlich als „bedient“ verwirft bzw. eine spätere Bedienung nicht schützt.

---
title: "Plan 5c Task 11: UI für Wärmemodell, Vorschau und Planwert"
author: Claude
date: 2026-10-27
branch: claude/project-thread-x8izsc
cost: { minutes: 178, tokens: 323558 }
id: l-3512302b
scope: repo
status: active
src: agent
touches:
  - tests/e2e/ui_server.py
  - tests/ui_world.py
  - ui/src/lib/system/thermal.ts
---

## Decided
- Vorzeichen in der Anzeige: Plan und core rechnen in Stufen (positiv mehr Leistung); Spalte „Klima“ im Zeitplan und Zeile „Plan“ der Karte „Klimaanlage“ zeigen den Versatz des Sollwerts wie der HA-Sensor „Geplanter Versatz“: Heizen +Stufen × 0,5 K, Kühlen −Stufen × 0,5 K (`plannedOffsetK` in `ui/src/lib/learning/thermal.ts`). 0 und `null` sind in der Spalte „–“.
- „bis HH:MM“ der Zeile „Plan“ kommt aus dem Plan der Absicht (`GET /api/control` → `intent.plan`, den die System-Seite schon alle 15 s lädt), nicht aus `GET /api/plan`: Health `planned` folgt dem Plan, den der core hat; der neueste Plan kann noch nicht beim core sein. Ende = letzter Slot gleicher `thermal_steps` ab dem laufenden, höchstens `valid_until`; ohne Plan nur der Versatz.
- Der Zeitplan hat keine Slot-Tabelle; „Spalte Klima“ ist eine eigene Karte „Vorheizen und Vorkühlen“ (Zeile über einer Tabelle Zeit/Klima mit Zeiträumen gleicher Verschiebung). „Ablauf“ und Chart bleiben unverändert.

## Watch out
- Das Demo-Backend (`tests/e2e/ui_server.py`) plant beim Start sofort neu (`planning.run()` fordert „start“ an, dazu ein wartender Auslöser „settings“ aus dem Aufbau der Welt). Ohne `demo_thermal_stage()` ersetzt dieser Lauf die Vorschau des ersten Plans durch Grund „off“ (Betriebsart „unbekannt“) – `plan.json` (aus `build_world` ohne `run`) zeigt die Vorschau trotzdem, darum fiel es in Task 9 nicht auf.
- valibot `v.optional(x, default)` macht das Feld im Ausgabetyp Pflicht: Testdaten, die `PlanSlot` oder `ThermalHealth` von Hand bauen (`lib/testing/plan.ts`, `ThermalCard.test.ts`), brauchen die neuen Felder.

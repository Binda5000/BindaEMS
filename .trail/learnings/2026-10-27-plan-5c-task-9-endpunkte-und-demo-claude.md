---
title: "Plan 5c Task 9: Endpunkte, Demo-Vorschau und Vertragsdateien"
author: Claude
date: 2026-10-27
branch: claude/project-thread-x8izsc
cost: { minutes: 90, tokens: 300000 }
id: l-5c09a1b2
scope: repo
status: active
src: agent
touches:
  - src/bindaems/app/learning/routes.py
  - src/bindaems/app/planning/routes.py
  - tests/ui_world.py
---

## Decided
- `learning_router` bekommt `thermal: ThermalModelService | None` und `settings`; `None` heißt keine Klimaanlage der Art `coe_offset` und beantwortet GET und POST mit 404 „Keine Klimaanlage eingerichtet.“. `enabled` kommt aus `learning.thermal`, nicht aus dem Dienst (`status()` kennt den Schalter nicht).
- `GET /api/plan`: je Slot `thermal_w`, `thermal_steps`, `thermal_grid_w`; im Plan `thermal` (`null` ohne Klimaanlage) mit `saving_ct` auf 0,1 gerundet.
- Handbuch: eigener Abschnitt „Vorheizen und Vorkühlen (Phase 5c)“ vor „Planung“; das Ausrollen kommt in Task 12 dazu.

## Watch out
- Die Preise der Demo-Welt sind zu eng für eine echte Verschiebung (jede Rechnung ergibt `not_worth`: Budget der Slots mit Netzbezug ausgeschöpft, am Abend deckt der Akku). Die Demo-Vorschau kommt darum aus einer festen Verschiebung (`_demo_shift` als `shift_fn` von `apply_thermal`, per `patch` auf `bindaems.app.planning.service.apply_thermal`); Prüfung, Slots und `Plan.thermal` laufen echt.
- `apply_thermal` bindet `shift_fn=shift` als Standardargument: Ein Patch auf `thermal_stage.shift` wirkt nicht.
- Die Betriebsart ist in der Demo „unbekannt“ (Anlage nicht verbunden): `_heating` ersetzt `live.health` nur, solange der Plan entsteht, damit `system.json` unverändert „unbekannt“ zeigt.

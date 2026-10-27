---
title: "Plan 5c Task 10: Schalter und Sensor für Vorheizen und Vorkühlen in Home Assistant"
author: Claude
date: 2026-10-27
branch: claude/project-thread-x8izsc
cost: { minutes: 60, tokens: 250000 }
id: l-5c10a3c4
scope: repo
status: active
src: agent
touches:
  - src/bindaems/app/ha/entities.py
  - src/bindaems/app/ha/commands.py
  - src/bindaems/app/planning/service.py
  - src/bindaems/shared/config.py
  - tests/integration/test_ha_round_trip.py
---

## Decided
- Der Schalter `thermal_plan` geht den Weg von „Netzladen erlaubt“: `PlanningService.set_thermal_plan` schreibt eine neue Einstellungsversion (Kommentar „Vorheizen und Vorkühlen ein/aus“, Eintrag `settings.update`), der Listener löst die Neuplanung aus. Es gibt keinen eigenen Audit-Eintrag wie `control.automation` bei 5b: Der Schalter ist global und lebt in den Einstellungen, nicht in einem Dienst.
- Schalter und Sensor gibt es nur mit einer Klimaanlage (`cfg.thermal.devices`); `ha_object_ids` nimmt `thermal_plan` darum nur dann auf (`HA_THERMAL_PLAN_ID`), und der Befehl fehlt in der Tabelle des Handlers.
- `planned_offset` rechnet ohne `offset_for`: Stufen des laufenden Slots (`plan_now`) mal `STEP_K`, beim Kühlen mit umgekehrtem Vorzeichen, nur bei `plan.thermal.active`, eingeschaltetem Schalter und passendem `plan.thermal.device`; sonst 0. Das Vorzeichen kommt aus den ganzen Stufen, damit nie „-0.0“ erscheint.
- Der Name heißt „Klima Geplanter Versatz“ (Präfix wie die anderen Sensoren am Hauptgerät).

## Watch out
- `HaInputs.thermal_plan_enabled` füllt `_build_ha_inputs` aus den Einstellungen; `_current_ha_inputs` liest sie nach einem Befehl neu, sonst zeigt der Schalter bis zum nächsten 5-s-Takt den alten Stand.
- Der Rundlauf-Test meldet sich für die API-Änderung als Admin an (`PUT /api/settings` verlangt Admin, nicht „Bedienen“); der Client trägt nur eine Sitzung, darum wechselt der Test den Benutzer nur bei Bedarf.
- `test_acceptance_checklist_of_phase_4_covers_the_planned_part` verlangt in `docs/verification/abnahme-phase-4.md` eine Zeile je Bedienelement des Beispiels.

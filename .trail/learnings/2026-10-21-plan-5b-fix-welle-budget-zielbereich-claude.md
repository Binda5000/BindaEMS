---
title: "Plan 5b Fix-Welle: Budget, Zielbereich, Abfahrtstag"
author: Claude
date: 2026-10-21
branch: claude/phase-5b-plan-xo03ty
cost: { minutes: 226, tokens: 479397 }
touches:
  - "src/bindaems/app/planning/builder.py#_guard_steps"
  - "src/bindaems/app/planning/optimizer.py#_known_peaks_kw"
  - "src/bindaems/app/learning/driving_profile.py#with_settings"
  - "src/bindaems/app/learning/driving_service.py#_on_settings"
  - "src/bindaems/app/planning/vehicles.py#_occupied_wallboxes"
  - "ui/src/lib/format.ts#departureText"
---

## Decided
- Ruling F-1: budget_kw (geht als Wächter-Budget an den core) rechnet ohne erwartete Autos, an zwei Stellen: builder._guard_steps (Nachrechnung ohne ihre Ladeleistung) und optimizer.peaks_kw (mit erwarteten Autos max(peak0, Bezug ohne deren Netzladen) statt peak0+d1+d2). Beide sind nötig, jede allein lässt den Test test_plan_once_budget_is_not_raised_by_an_expected_car rot.
- Ruling F-2: Ziel und target_clamped entstehen bei jeder Verwendung neu aus consumption_p80_pp (driving_profile.with_settings); die einzige Schleuse ist DrivingService.profile/profiles/status, über die Automatik (runtime: profiles=driving.profile), HA und API lesen. Settings.on_change startet einen Lauf, wenn sich learning.driving außer automation ändert; läuft schon einer, folgt einer danach (_again).
- Ruling F-4: kein erwartetes Auto, wenn health.wallboxes die default_wallbox mit occupant (auch guest) oder einem Vorgang mit unklarer Zuordnung nennt oder ein anderes Auto dort plugged True ist.
- T10: PlanVehicleInput validiert expected == (arrival_at is not None); model_copy umgeht das (Tests mit model_copy setzen beide).
- UI (F-3): departureText nennt den Tag, wenn die Absicht departure_on ≠ heute hat; ohne Absicht (Zeitplan) ab übermorgen.

## Watch out
- Der Planungs-Test, der nach until(len(calls)==2) current() prüft, wackelt unter Last (Plan wird nach dem Solver im Thread gespeichert): auf current().trigger warten.
- pkill -f 'while True' trifft auch die eigene Shell; Lastprozesse per PID beenden.
- Eine geänderte Uhrzeit im Feld „Von Hand“ löscht departure_on (app: departure ohne departure_on → täglich).

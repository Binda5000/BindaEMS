---
title: "Plan 5b Fahrverhalten: Datenlage und Fallen"
author: Claude
date: 2026-10-20
branch: claude/phase-5b-plan-xo03ty
cost: { minutes: 25, tokens: 1388 }
touches:
  - docs/superpowers/plans/2026-10-20-phase-5b-fahrverhalten.md
  - src/bindaems/app/charging/planned.py
  - src/bindaems/shared/control.py
---

## Decided
- Plan 5b liegt in docs/superpowers/plans/2026-10-20-phase-5b-fahrverhalten.md (14 Tasks, 17 Entscheidungen zur Freigabe, offen vor allem 1, 8, 9, 11, 12). Nichts implementieren vor Bindas Freigabe.
- Fahrverhalten mit empirischen Perzentilen statt LightGBM (höchstens 8 Werte je Wochentag in 8 Wochen).
- Vorschlag: Abfahrt an einem Datum (`VehicleIntent.departure_on`), sonst kann die Automatik Tage ohne Fahrt (Wochenende) nicht überspringen; einzige core-Änderung ist der Aufruf von `next_departure` in core/control/planned.py.
- Erwartete Autos (nicht angesteckt, „Geplant“, Anwesenheitsprognose ≥ 50 %) nur im Plan; `intent_plan` lässt sie weg.

## Tried and ruled out
- Akku-Unterstützung für PV-Autos im MILP: Plan-Schwelle darf heute nur anheben; Optimierung müsste senken dürfen (Regel aus 3c ändern), Gewinn klein. Als Entscheidung 12 „entfällt“ vorgeschlagen.

## Watch out
- Jede Änderung von Ziel/Abfahrt vor der Abfahrt beendet eine Zeile in `planned_charge` als `changed` (4b R16). Bleibt ein Auto über die Abfahrt stecken, beginnt sofort die Zeile des nächsten Tages, bevor eine Automatik reagieren kann; darum die Übernahme in den ersten 10 min einer Zeile.
- Anwesenheit: Tessie `vehicle.<k>.at_home` steht nur in InfluxDB `raw` (status, 90 Tage, keine CQ). HA schreibt device_tracker `home`=1/`not_home`=0 in `value`, eine Zone aber nur als Text im Feld `state` – beide Felder lesen.
- Health nennt keine Anwesenheit (`at_home`), nur `plugged`; `charging_session` gibt es erst seit 4a (18.10.2026).

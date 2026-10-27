---
title: "Plan 5c Task 7: Verschiebung im Plan, Absicht und Auslöser"
author: Claude
date: 2026-10-27
branch: claude/project-thread-x8izsc
cost: { minutes: 125, tokens: 277416 }
id: l-912efd9e
scope: repo
status: active
src: agent
touches:
  - src/bindaems/app/planning/service.py
  - src/bindaems/shared/plan.py
  - tests/unit/app/planning/test_service.py
---

## Decided
- Der zweite Schritt läuft im Dienst nach `plan_once` (`thermal_stage.apply_thermal`, im Thread), nicht in `plan_once`: Die Planungsszenarien rufen `plan_once` und bleiben so unverändert. Auch ein Ersatzplan bekommt die Verschiebung.
- `PlanSlot.thermal_grid_w` ist zusätzlich zu Präzisierung 7 nötig: `intent_plan` braucht `ShiftedSlot.grid_w` und liest nur den Plan.
- `KLIMA_MEHR`/`KLIMA_WENIGER` stehen nur in Slots einer aktiven Verschiebung; in der Vorschau tragen die Slots `thermal_steps`, die Sätze aber nicht (sonst sagten UI und HA "Vorheizen" bei Schalter aus). `k` hat das Vorzeichen der Stufen, der Satz nennt den Betrag.
- Auslöser `thermal`: verglichen wird mit der Betriebsart, mit der der letzte Plan rechnete (`_with_thermal` merkt sie und löscht das Flag "neues Modell"); ohne Verbindung zum core kein Vergleich. Der Schalter `learning.thermal` zählt in `settings_relevant`.

## Watch out
- Im Testhaus der Planungs-Fixtures (30 kWh Akku bei 50 %) deckt der Akku den teuren Abend: Absenken spart dann nur den Restwert (`dn_b`), Vorheizen aus dem Netz sperrt die Entladung, die Verschiebung ist `not_worth`. Tests mit `ok` brauchen einen kleinen Akku an der Reserve (Fixture `small_house`).
- Die Absicht trägt nur die Slots vor `valid_until` (3–4), nicht 8.
- In Tests auf den gespeicherten Plan warten (`plan.id`), nicht auf den Solver-Aufruf: Die Verschiebung rechnet danach im Thread, und ein `thermal_model_changed` vor ihrem Beginn löscht sie wieder.

---
title: "Nacharbeit 5a: Wechsel des Lastmodells, Rollback, Demo-Preise"
author: Claude
date: 2026-10-20
branch: claude/phase-5-plan-s7xe0n
cost: { minutes: 171, tokens: 1250357 }
touches:
  - src/bindaems/app/forecast/load.py
  - src/bindaems/app/learning/service.py
  - src/bindaems/app/db/migrations/versions/v0013_learning.py
  - tests/ui_world.py
  - ui/tests/e2e/settings.spec.ts
---

## Decided
- Ändert sich `LoadModelService.active()` (Schalter, Bewertung, Laden beim Start), meldet der Dienst es (`on_active_change`); die Laufzeit hängt `LoadForecastService.request_refresh` an. Die Ausgabe außer der Reihe macht die Schleife `run` selbst (Event + `_wait`), damit nie zwei `refresh()` zugleich laufen; der Planer sieht sie wie jede Ausgabe über `load_issued`.
- Rollback vor 5a ohne Datenverlust: `command.downgrade(config, "0012")` mit dem 5a-Image; der Downgrade von 0013 entfernt auch `learning` aus `settings_version.data` (json_remove).

## Tried and ruled out
- Nur `alembic downgrade` reicht nicht: Die app vor 5a lehnt jede seit dem Ausrollen gespeicherte Einstellungsversion ab (`extra="forbid"`, Abschnitt `learning`) und nimmt Standardwerte. Geprüft mit einem Worktree auf e0a6e50 und PYTHONPATH auf dessen `src`.

## Watch out
- Die Demo-Welt ist in den E2E-Tests geteilt. „Neu bewerten“ (`reprice`, only_known=False) setzt den Preis jedes Slots ohne Eintrag im Preisspeicher auf leer; darum legt `tests/ui_world.py` die Börsenpreise des Verlaufs in `price_slot` (aus dem linearen Tarif zurückgerechnet), und E2E-Tests mit Datumsvorschlag brauchen `page.clock.setFixedTime(T_DEMO)`.
- `HaSensorReader.candidates()` wirft nie, sondern liefert `([], Grund)`; wer daraus „bekannt“ ableitet, muss den Grund prüfen.

---
title: "Akku ins Auto Task 10: Rückweg ohne Migration"
author: Claude
date: 2026-10-28
branch: claude/project-thread-c3k9h7
cost: { minutes: 68, tokens: 194508 }
id: l-45b52bd4
scope: repo
status: active
src: agent
touches:
  - docs/betrieb.md
  - docs/uebergabe.md
  - src/bindaems/app/settings/service.py
  - src/bindaems/core/control/intent.py
---

## Decided
- Rollback vor Akku ins Auto (docs/betrieb.md, Abschnitt 4): drei Stellen tragen die neuen Felder auch mit Schalter aus. `intent.json`: `IntentStore._save` schreibt `intent.model_dump(mode="json")`, also `ev_battery_w: {}` und `now_battery: false` in jedem Plan-Slot; ein core vor dem Plan (`extra="forbid"`) liest die Datei nicht. Einstellungen: `SettingsService.update` speichert `model_dump`, jede Version nach dem Ausrollen hat `planning.car_battery_allowed`; eine ältere app lehnt sie in `_parse_stored` ab und nimmt die Standardwerte aller Einstellungen (Tarif weg). Pläne: `plan_row_unreadable` für 30 Tage.
- Abhilfe ohne Migration: Felder aus `intent.json` per Python im Image entfernen, in SQLite `UPDATE settings_version SET data = json_remove(data, '$.planning.car_battery_allowed') WHERE json_type(...) IS NOT NULL` (wie der Downgrade von `0013`); geprüft an einer frisch migrierten Datenbank.

## Tried and ruled out
- „Warten, bis der Plan abläuft“ als Rückweg für `intent.json`: Ein abgelaufener Plan bleibt mit den Feldern in der Datei, bis eine neue Absicht ihn ersetzt; der ältere core liest die Datei trotzdem nicht.
- Nach dem Rollback die Einstellungen in der älteren app neu speichern: speichert die Standardwerte als neue Version (`update` mit `_invalid_stored`).

## Watch out
- `ZIEL_SOC_ABFAHRT` hat als `kw` die ganze Ladeleistung des Autos (`watts`), nicht den Netzanteil; das Betriebshandbuch sagte „Netzleistung“.
- `origin/main` war beim Schreiben schon bei `779dd47` (Standard-Lademodus, nutzt in docs/uebergabe.md Punkt 15 und Nächste Schritte 10): beim Rebase die Nummern dieses Plans auf 16 und 11 schieben.

---
title: "Akku ins Auto: Rückweg ohne Migration über intent.json-Skript und YAML-Export der Einstellungen"
author: Claude
date: 2026-10-28
branch: claude/project-thread-c3k9h7
cost: { minutes: 75, tokens: 239474 }
id: l-b8dc1789
scope: repo
status: active
replaces: l-45b52bd4
src: agent
touches:
  - docs/betrieb.md
  - docs/uebergabe.md
  - src/bindaems/app/settings/service.py
  - src/bindaems/core/control/intent.py
---

## Decided
- Rollback vor Akku ins Auto (docs/betrieb.md, Abschnitt 4): drei Stellen tragen die neuen Felder auch mit Schalter aus. `intent.json`: `IntentStore._save` schreibt `intent.model_dump(mode="json")`, also `ev_battery_w: {}` und `now_battery: false` in jedem Plan-Slot; ein core vor dem Plan (`extra="forbid"`) liest die Datei nicht. Einstellungen: `SettingsService.update` speichert `model_dump`, jede Version nach dem Ausrollen hat `planning.car_battery_allowed`; eine ältere app lehnt sie in `_parse_stored` ab und nimmt die Standardwerte aller Einstellungen (Tarif weg). Pläne: `plan_row_unreadable` für 30 Tage, harmlos.
- Abhilfe ohne Migration: Felder aus `intent.json` per Python im Image entfernen. Die Einstellungen folgen der Konvention von main (Learning l-e688fbe6, Standard-Lademodus): vor dem Rollback als YAML exportieren, die Zeile `car_battery_allowed:` unter `planning:` löschen, nach dem Rollback importieren. Geht man hinter beide Pläne zurück, beide Schlüssel löschen (`wallbox_default_modes` und `planning.car_battery_allowed`). Das SQL mit `json_remove` steht nur als Notbehelf ohne Export im Betriebshandbuch.

## Tried and ruled out
- „Warten, bis der Plan abläuft“ als Rückweg für `intent.json`: Ein abgelaufener Plan bleibt mit den Feldern in der Datei, bis eine neue Absicht ihn ersetzt; der ältere core liest die Datei trotzdem nicht.
- Nach dem Rollback die Einstellungen in der älteren app neu speichern: speichert die Standardwerte als neue Version (`update` mit `_invalid_stored`).
- SQL `json_remove` als regulärer Weg für die Einstellungen: zweite Konvention neben Export/Import; nur Notbehelf.

## Watch out
- `ZIEL_SOC_ABFAHRT` hat als `kw` die ganze Ladeleistung des Autos (`watts`), nicht den Netzanteil; das Betriebshandbuch sagte „Netzleistung“.

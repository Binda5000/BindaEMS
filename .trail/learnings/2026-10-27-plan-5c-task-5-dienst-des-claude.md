---
title: "Plan 5c Task 5: Dienst des Wärmemodells"
author: Claude
date: 2026-10-27
branch: claude/project-thread-x8izsc
cost: { minutes: 52, tokens: 162324 }
id: l-9c78e3fd
scope: repo
status: active
src: agent
touches:
  - src/bindaems/app/runtime.py
  - ui/src/lib/api/contract/system.json
---

## Decided
- `ThermalModelService` wartet beim Start wie das Lastmodell auf die Nachholung des Wetterarchivs (`ready=self._weather_backfilled.wait`, höchstens 600 s), nicht wie das Fahrverhalten sofort: `read_thermal_slots` lässt jeden Slot ohne Außentemperatur weg, ein Lauf vor der Nachholung hätte nach einer Pause zu wenig Tage und legte ein untaugliches Modell als neuen Stand ab.
- Auch ein Lauf ohne Daten oder mit untauglichem Modell ist ein Stand (Zeilen mit `usable=False`) und ruft `on_change`; nur Lese- oder Rechenfehler behalten den alten. `status()` ist frei von Einstellungen, `enabled` ergänzt der Endpunkt (Task 9).
- `params` ist `asdict(ModeModel)` mit `inf`/`nan` als `null` (die API lehnt sie ab); das Profil liegt als Listen `[Gruppe, Viertelstunde, Wert]`, weil JSON keine Tupel-Schlüssel kennt.

## Watch out
- Eine neue Komponente ändert `ui/src/lib/api/contract/system.json`: `tests/integration/test_ui_contract.py` scheitert mit `['system']`, bis die Datei mit `UPDATE_UI_CONTRACT=1` neu geschrieben ist; die Unit-Suite merkt es nicht.

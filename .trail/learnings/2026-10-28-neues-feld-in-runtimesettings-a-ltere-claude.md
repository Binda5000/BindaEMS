---
title: "Neues Feld in RuntimeSettings: ältere app fällt beim Rollback auf Standardwerte zurück"
author: Claude
date: 2026-10-28
branch: claude/project-thread-7dml9y
cost: { minutes: 6, tokens: 64209 }
id: l-e688fbe6
scope: repo
status: active
src: agent
touches:
  - docs/superpowers/plans/2026-10-28-standard-lademodus-je-ladepunkt.md
---

## Decided
Plan docs/superpowers/plans/2026-10-28-standard-lademodus-je-ladepunkt.md: der Standard-Lademodus je Ladepunkt läuft nur in der app (ChargingTracker meldet das Abstecken, ein Dienst setzt SetMode/SetGuestMode mit Quelle system). Der core bleibt gleich, weil der Modus dem Fahrzeug gehört und die app das Fahrzeug der Session schon kennt. Binda wollte den Plan am 28.10.2026 noch ändern (noch nicht freigegeben).

## Watch out
Jedes neue Feld in RuntimeSettings (shared/settings.py, extra="forbid") lässt eine ältere app beim Rollback die gespeicherten Einstellungen verwerfen: _parse_stored in app/settings/service.py fällt dann auf die Standardwerte zurück (Tarif weg). Der Rollback muss die Einstellungen als YAML exportieren, den neuen Schlüssel löschen und danach importieren.

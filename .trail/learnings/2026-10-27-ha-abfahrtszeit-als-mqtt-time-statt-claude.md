---
title: HA-Abfahrtszeit als MQTT time statt text
author: Claude
date: 2026-10-27
branch: claude/project-thread-4xp3qu
cost: { minutes: 33, tokens: 361194 }
touches:
  - docs/betrieb.md
  - docs/verification/abnahme-phase-4.md
  - tests/integration/test_app_runtime.py
  - tests/unit/test_docs.py
  - docs/superpowers/specs/2026-10-08-bindaems-design.md
  - docs/uebergabe.md
  - src/bindaems/app/ha/commands.py
  - tests/integration/test_ha_round_trip.py
  - tests/unit/app/ha/test_commands.py
  - tests/unit/app/ha/test_entities.py
---

## Decided
- Binda 27.10.2026: Abfahrtszeit je Auto in HA als MQTT `time` statt `text` (Plan docs/superpowers/plans/2026-10-27-ha-abfahrt-als-uhrzeit.md), damit die Entities-Karte eine Uhrzeit-Auswahl in der Zeile zeigt. Zustand `HH:MM:00`, Befehl `HH:MM` oder `HH:MM:SS` (Sekunden fallen weg), die Absicht bleibt `HH:MM`.
- Altes Textfeld: `retired_discovery` (entities.py) + `HaPublisher(retired=...)` senden bei jeder Verbindung und HA-Birth eine leere gehaltene Discovery an homeassistant/text/bindaems/<oid>_departure/config. Darum bleibt die ACL-Zeile für `text`; neu nötig `topic write homeassistant/time/bindaems/#`.

## Tried and ruled out
- Eine HACS-Uhrzeitkarte für das Textfeld: gibt es nur für input_datetime.

## Watch out
- MQTT `time` gibt es erst ab Home Assistant 2026.5; ältere ignorieren die Discovery, die Abfahrt fehlt dann.
- HA sendet den Befehl als `value.isoformat()` (HH:MM:SS); HA parst den Zustand mit dateutil, UNKNOWN ("None") wird unknown.
- Dashboard-YAML: `06:00:00` ungequotet ist in YAML 1.1 eine Sexagesimalzahl, immer quoten.
- Branch parallel zu 5c (x8izsc): Änderungen lokal halten, nichts an Dateienden anhängen, betrieb.md Abschnitt 4 und uebergabe-Kopf meiden.

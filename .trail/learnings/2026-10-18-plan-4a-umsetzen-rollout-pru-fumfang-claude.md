---
title: "Plan 4a umsetzen: Rollout, Prüfumfang, Fallen"
author: Claude
date: 2026-10-18
branch: claude/phase-4-plan-whnbh6
cost: { minutes: 112, tokens: 200614 }
touches:
  - docs/uebergabe.md
  - src/bindaems/app/ledger/service.py
  - tests/unit/app/ledger/test_service.py
  - src/bindaems/app/charging/sessions.py
  - src/bindaems/app/stats/service.py
  - tests/property/test_session_attribution.py
  - tests/unit/app/charging/test_sessions.py
  - tests/unit/app/stats/test_service.py
  - docs/betrieb.md
  - docs/superpowers/specs/2026-10-08-bindaems-design.md
---

## Decided
- 4a ändert nur app/UI/deploy. Ausrollen an der Anlage: `cd /opt/bindaems && docker compose pull ems-app && docker compose up -d --no-deps ems-app` (erst wenn 4a auf main und das Image veröffentlicht ist). `up -d --build ems-app` aus /opt/bindaems kann nicht bauen (Build-Kontext `..`) und könnte ohne `--no-deps` den core neu erzeugen.
- HA: Ersparnis-Sensoren mit Einheit "EUR" (ISO 4217 für monetary), Ladeenergie ohne state_class (energy + measurement warnt in HA).
- Kennzahlen eines Zeitraums (Autarkie, Eigenverbrauch) sind nur bei Nenner 0 unbekannt; Geldbeträge werden unbekannt, sobald ein Tag unbekannt ist.
- UI-only-Tasks brauchen keinen Python-Lauf; die volle Kette (pytest --cov mit Simulator, ~27 min) läuft einmal am Ende.

## Tried and ruled out
- Session-Zuordnung je Slot über alle Sessions: Jahresansicht 15 s. Jetzt Index je Wallbox mit bisect (`SessionLookup`) und `count_by_vehicle` ohne Zuordnung.

## Watch out
- `trail check` meldet für 4a viele Treffer gegen die Lernnotiz „Sessions aus Wallbox-Energie“: Wortgleichheit, die Sessions kommen aus dem Steckzustand.
- Ein ersetzter Slot muss weggefallene Flüsse in Influx auf 0 schreiben, sonst zählt Grafana Reste mit.
- Der Startlauf des Nachtjobs wartet auf den ersten Nachtrag der Abrechnung (höchstens 600 s).

---
title: "smartENERGY veröffentlicht den Folgetag um 17 Uhr; Abruf 17–19 Uhr alle 5 min, Frist 18 Uhr"
author: Claude
date: 2026-10-25
branch: claude/project-thread-5sj2u0
cost: { minutes: 17, tokens: 207265 }
id: l-fb3ddeaa
scope: repo
status: active
src: agent
touches:
  - src/bindaems/app/prices/pipeline.py
  - src/bindaems/app/prices/scheduler.py
  - tests/unit/app/prices/test_scheduler.py
---

## Decided
Betreiber 25.10.2026: smartENERGY (API-Infos) liefert den Folgetag erst um 17 Uhr. Der alte Abrufplan (12:30–16:00 alle 15 min) lief komplett davor ins Leere, und die Frist 16 Uhr (`TOMORROW_EXPECTED_FROM` in src/bindaems/app/prices/pipeline.py, auch HA „Preise fehlen“ in app/ha/entities.py) meldete jeden Tag eine Stunde lang Fehler. Neu: src/bindaems/app/prices/scheduler.py fragt 17:00–19:00 alle 5 min nach, bis morgen von smartENERGY vollständig ist; Frist 18 Uhr. Energy-Charts füllt den Folgetag vor der Frist weiter still als Ersatz (gleicher Börsenpreis, der Planer sieht morgen früher).

## Tried and ruled out
Ersatzquelle vor der Frist abschalten: bringt nichts, die Werte sind dieselben EPEX-Preise, und der Planer plant beim Wechsel auf primary ohnehin neu (Fingerabdruck `primary`).

## Watch out
Energy-Charts hatte am 25.10. (Sonntag) um 14:25 den Folgetag auch noch nicht, aWATTar schon. In tests/unit/app/prices/test_scheduler.py: der stündliche Abruf um :02 gilt auch im dichten Fenster, Erwartungen also nicht auf 17:01 → 17:05 setzen.

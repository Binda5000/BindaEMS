---
title: "Open-Meteo-Fehler an der Anlage sind kurze 503, nicht das Rate-Limit"
author: Claude
date: 2026-10-26
branch: claude/project-thread-6hwhy3
cost: { minutes: 17, tokens: 78977 }
id: l-adba1196
scope: repo
status: active
src: agent
---

## Decided
- Binda meldete am 25.10.2026 „Open-Meteo-Limit überschritten“. Die Logs der app an der Anlage (18.–26.10.2026, 2 Flächen Sued/West, Glasfaser mit fixer IP) zeigen kein einziges HTTP 429, nur 5 einzelne HTTP 503 am 18. und 19.10.; die Wiederholung nach 10 min klappte jedes Mal.
- BindaEMS braucht etwa 25 × N Aufrufe am Tag (N = Flächen; stündlich :07 plus Start), die Temperatur-Historie (92 Tage) zählt einmalig etwa 6,6 Aufrufe. Free-Limit 10.000/Tag, 5.000/h, 600/min, gezählt je öffentlicher IP.
- Fix (Binda, Karte „Kleiner Fix“ 26.10.2026): je Fläche bei 5xx/Netzfehler nach 30 s bis zu zweimal neu, geholte Flächen bis 30 min behalten, `reason` der Antwort in Status/Log (`http_error` in app/forecast/openmeteo.py, `_fetch_plane` in app/forecast/service.py).

## Tried and ruled out
- Backoff für 429 (Tages-/Stundenlimit bis Mitternacht UTC warten): nicht gebaut, weil nie ein 429 auftrat.
- Flächen in einer Anfrage bündeln: Open-Meteo zählt je Ort trotzdem, Listen für tilt/azimuth sind nicht dokumentiert.
- Status gelb statt rot, solange die alte Prognose 24 h abdeckt: Binda wollte das nicht.

## Watch out
- Meldet jemand wieder „Limit“: erst die Logzeile prüfen (`docker compose logs ems-app | grep PV-Prognose`); seit dem Fix steht der Grund von Open-Meteo im Text.
- Tests in tests/unit/app/forecast nutzen standardmäßig `no_sleep`; mit echtem asyncio.sleep kosten 503-Tests 60 s.

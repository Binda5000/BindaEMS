---
title: "Phase 5 Zuschnitt und Plan 5a Lastmodell"
author: Claude
date: 2026-10-20
branch: claude/phase-5-plan-s7xe0n
cost: { minutes: 25, tokens: 140000 }
touches:
  - docs/superpowers/plans/2026-10-20-phase-5a-lastmodell.md
---

## Decided
- Vorschlag (wartet auf Freigabe): Phase 5 in 5a Lastmodell + Prognosegüte (nur app/UI), 5b Fahrverhalten (Vorschläge, Automatik, Anwesenheitsprognose, Akku-Unterstützung PV-Autos im MILP aus 4b), 5c Vorheizen/Vorkühlen (aus 6a, braucht Daten der Klimaanlage).
- Plan 5a: Schattenbetrieb (`load_profile`, `load_model` im Archiv, `load` = aktiv), Aktivierung ≥ 10 % über 14 Tage aus Schatten ab 10 Tagen, sonst aus dem Backtest.

## Tried and ruled out
- Ein Merkmalssatz je Ziel-Slot ohne Ausgabezeitpunkt: Lags und Sensoren wären je Vorlauf unterschiedlich bekannt (Leck); deshalb Zeilen je (Ausgabe 00/06/12/18, Ziel-Slot) mit `lead_h`.

## Watch out
- LightGBM 4.x (PyPI-Wheel) braucht `libgomp.so.1`; `python:3.12-slim` hat es nicht → `libgomp1` in `deploy/Dockerfile.app`. LightGBM bringt numpy und scipy (~130 MB) mit und hat `py.typed`.
- `ha_selection` kennt nur die Einheiten-Messungen `W`/`kW`; für °C-Sensoren erweitern.
- `forecast_record.kind` ist `String(8)`; `load_profile`/`load_model` sind länger.
- Open-Meteo `past_days` (bis 92) liefert die Temperatur-Historie für die Nachholung.

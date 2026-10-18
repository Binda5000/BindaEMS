---
title: "Nacharbeit 4a: Abrechnung, InfluxDB-Leser, Planer-Plausibilität"
author: Claude
date: 2026-10-18
branch: claude/phase-4-plan-whnbh6
cost: { minutes: 5, tokens: 39706 }
touches:
  - docs/uebergabe.md
  - docs/betrieb.md
  - docs/superpowers/specs/2026-10-08-bindaems-design.md
  - ui/src/lib/stats/ChecksCard.svelte
  - ui/src/lib/stats/ChecksCard.test.ts
  - ui/src/lib/stats/view.test.ts
  - ui/src/lib/stats/view.ts
  - ui/src/routes/statistik/+page.svelte
  - ui/src/routes/statistik/page.test.ts
  - src/bindaems/app/charging/tracker.py
---

## Decided
- Zählerstand um Mitternacht kommt aus "raw"."energy" (Lesezeitpunkte), nicht aus "long": die CQ cq_energy_1m stempelt am Minutenanfang, eine Interpolation auf long ist wirkungslos.
- InfluxReader schickt Abfragen per POST (lange Mehrfachabfragen), lässt aber nur SELECT/SHOW ohne INTO durch, weil POST die Nur-Lesen-Sperre von InfluxDB 1.x aufhebt.
- Einstandspreis unbekannt, solange der unbepreiste Anteil im Akkubestand ≥ 1 % ist (anteilig geführt, Spalte ledger_cost.unpriced_kwh). Der Buchungsbestand erreicht praktisch nie 0.
- Nachführung des Einstandspreises im eigenen Job; Fehlerpfad markiert dirty ohne Listener, danach 60 s Pause (sonst enge Schleife).
## Tried and ruled out
- "Bekannt, sobald der Akku leer war": tritt mit Wandlungsverlusten praktisch nie ein.
## Watch out
- tests/property/test_plan_plausible.py kann scheitern: Optimierer entlädt je Slot knapp unter 100 W, die Plausibilität zählt erst ab 100 W (Spec 9.5 widerspricht sich). Offen bis zur Entscheidung des Betreibers; nicht still den Test lockern.
- Test-Fakes für InfluxDB müssen die echten Stempel abbilden (long = Minutenanfang), sonst verdecken sie Fehler.

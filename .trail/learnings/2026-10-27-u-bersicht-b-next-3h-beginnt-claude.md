---
title: "Übersicht B: next_3h beginnt bei jetzt+15 min, Teilsummen und unbekannter Auto-Stand zeigen „–“"
author: Claude
date: 2026-10-27
branch: claude/project-thread-81quvk
cost: { minutes: 101, tokens: 442141 }
id: l-0d4ab3e9
scope: repo
status: active
src: agent
touches:
  - docs/betrieb.md
  - docs/superpowers/specs/2026-10-08-bindaems-design.md
  - docs/uebergabe.md
  - ui/src/lib/dashboard/NoticeBar.svelte
  - ui/src/lib/dashboard/NoticeBar.test.ts
  - ui/tests/e2e/pages.spec.ts
  - ui/src/lib/dashboard/tiles.test.ts
  - ui/src/lib/dashboard/tiles.ts
  - ui/src/lib/dashboard/BatteryTile.svelte
  - ui/src/lib/dashboard/BatteryTile.test.ts
---

## Decided
- Binda wählte am 27.10.2026 aus drei Entwürfen (A Akku zuerst, B Kennzahlen-Leiste, C Fluss mit Akku) Variante B für die Übersicht (`ui/src/routes/+page.svelte`). Das SVG-Energieflussbild wurde durch die Balken „Woher – Wohin“ (`ui/src/lib/dashboard/balance.ts`) ersetzt; EnergyFlow, SocList, ConsumerSummary, PriceNow und Notices sind gelöscht.
- Logik der Kacheln steckt in reinen Modulen (`battery.ts`, `tiles.ts`, `balance.ts`, `vehicles.ts`, `prices.ts`), die Svelte-Komponenten zeigen nur an.
- Akku-SOC und Akku-Plan im Übersicht-Diagramm in `palette.text`, weil `--battery` (Blau) dort schon der Bezugspreis ist.

## Watch out
- `GET /api/prices/now`: `next_3h` enthält den laufenden Slot NICHT (beginnt bei jetzt + 15 min, `app/prices/routes.py`). „Jetzt“ ist `now.import_gross_ct`. UI-Fixtures, die `next_3h` mit dem jetzigen Slot beginnen, verdecken genau diesen Fehler (passierte in `cheapestNext`).
- Im core sind `pv_total_w`, `consumption_w` und `house_load_w` gemeinsam null, sobald `pv_ac_w` fehlt. Summen über Zweige müssen dann „–“ zeigen, nicht die Teilsumme oder 0 W.
- `system.core.health` ist null bei getrenntem core oder vor der ersten Antwort: Fahrzeugstatus dann „Stand unbekannt“, nie „nicht angesteckt“.
- Demo-Uhr steht auf 09.10.2026, Browser auf echtem Datum: im Demo-Screenshot fehlt die Planzeile, die x-Achse des Diagramms spannt Wochen, „Warum?“ sagt „vor 18 Tagen“ – nur Demo.

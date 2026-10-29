---
title: "GitHub-CI rendert E2E ohne Inter mit DejaVu Sans: Breiten-Tests am Handy lokal mit FONTCONFIG nachstellen"
author: Claude
date: 2026-10-29
branch: claude/project-thread-ja1izu
cost: { minutes: 16, tokens: 118603 }
id: l-be4288df
scope: repo
status: active
src: agent
touches:
  - ui/src/lib/stats/StatTiles.svelte
  - ui/src/lib/forecast/QualityTable.svelte
  - ui/tests/e2e/pages.spec.ts
  - ui/tests/e2e/stats.spec.ts
---

## Watch out
- Der Cloud-Container hat die Schrift Inter, der GitHub-Runner nicht: dort wird `system-ui` zu DejaVu Sans, deutlich breiter. 360-px-Tests (pages.spec „Tabellen passen bei 360 px in ihre Karte“, stats.spec „Geldwerte … in einer Zeile“) waren am 29.10.2026 lokal grün und in der CI rot (Prognosegüte-Tabelle 14 px zu breit, „1.234,56 €“ lief über).
- Lokal nachstellen: eine fontconfig-Datei, die `/etc/fonts/fonts.conf` einbindet und Inter per `<selectfont><rejectfont>` ausschließt, und Playwright mit `FONTCONFIG_FILE=<datei>` starten.

## Decided
- Kleine Geldkacheln: CSS-Schätzung (`100cqi / (Zeichen · 0,56)`) als erster Wurf, danach misst die Action `fitWidth` (`ui/src/lib/stats/fit.ts`) nach und verkleinert, bis `scrollWidth <= clientWidth`. Eine reine Zeichen-Schätzung passt nicht für jede Systemschrift.
- Vergleichstabelle der Prognosegüte (`.compare` in QualityTable.svelte): schmalere Zellränder und umbrechende Spaltenköpfe statt Karten, weil der Entwurf vier Spalten zeigt.

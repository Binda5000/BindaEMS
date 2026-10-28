---
title: Kleinigkeiten 5c vor der Integration
author: Claude
date: 2026-10-28
branch: claude/project-thread-x8izsc
cost: { minutes: 291, tokens: 794268 }
id: l-d69856d7
scope: repo
status: active
src: agent
touches:
  - "src/bindaems/app/planning/service.py#PlanningService"
  - src/bindaems/app/learning/thermal_data.py
  - src/bindaems/app/learning/thermal_model.py
  - ui/src/lib/resource.svelte.ts
  - ui/src/lib/system/thermal.ts
---

## Decided
- K8 (Auslöser „Betriebsart geändert“): Ursache der Wiederholung war, dass `_thermal_seen` und `_thermal_model_changed` nur in `_with_thermal` gesetzt wurden, also nur mit einem fertigen Plan. Ergab der Lauf `PlanningUnavailable`, blieb der Wechsel fällig und löste alle 5 min erneut aus. Jetzt setzt `_thermal_due` beides beim Auslösen.
- K2: `Resource` hat die Option `giveUp` (Fehler, nach denen weder Takt noch Rückkehr in den Tab von selbst abfragen; ein `refresh()` auf Bedienung lädt weiter). Die Seite Prognosen nimmt sie für 404 der Karte „Wärmemodell“ (404 gibt es nur ohne Klimaanlage, `thermal is None`).
- K4: Eine Viertelstunde ohne vollständige PV-Reihen hat `pv_w` None; `thermal_model` lässt solche Slots in Anpassung, Auskühl-Phasen und Ereignissen aus. Die Menge der Reihen sind alle `id` der Antwort der Abfrage (eine Anlage, deren Reihe nur zeitweise Daten hat, verliert damit Slots).
- K6: `no_data` gilt für eine Betriebsart ohne einen Slot in dieser Betriebsart, auch wenn es Slots anderer gibt; der Text bleibt „Noch keine Daten der Klimaanlage.“.

## Watch out
- `formatNumber` (de-AT) liefert ein ASCII-Minus. Wer einen Versatz mit echtem Minus (U+2212) will, nimmt `formatKelvinShift` oder setzt das Zeichen selbst und formatiert den Betrag.

---
title: "Plan 6a Klimaanlage: Entwurfsentscheidungen"
author: Claude
date: 2026-10-19
branch: claude/project-thread-79ieqs
cost: { minutes: 11, tokens: 115280 }
touches:
  - docs/superpowers/plans/2026-10-19-phase-6a-klimaanlage.md
  - docs/superpowers/specs/2026-10-08-bindaems-design.md
  - docs/uebergabe.md
  - tests/unit/app/planning/test_service.py
  - ui/tests/e2e/control.spec.ts
  - docs/betrieb.md
  - tests/unit/test_docs.py
  - ui/src/lib/charging/view.test.ts
  - ui/src/lib/charging/view.ts
  - ui/src/routes/laden/+page.svelte
---

## Decided
- Plan 6a liegt in docs/superpowers/plans/2026-10-19-phase-6a-klimaanlage.md (13 Tasks, 16 Entscheidungen), wartet auf Freigabe.
- Rest-Überschuss = gemessene Einspeisung über 10 min; Vorrang von Akku (SOC-Schwelle aus `soc_end_pct` des Slots) und Autos (`unmet_surplus_w`) zusätzlich als Rückschritt-Regeln.
- Der CoE-Adapter hält den Versatz und sendet ihn nur mit `permit` der letzten 10 s, sonst 0; Start sendet 0. Dry-Run sendet so zyklisch 0.
- Schrittregel in der Safety-Schicht (`safety/offset.py`): Betrag nur +1 Stufe je 600 s, Vorzeichenwechsel nur über 0, Verringern immer.

## Tried and ruled out
- Zyklisches Neusenden über den Ausführer (`Command.refresh`): gilt nur für GRID_SETPOINT, Totband blockiert unverändertes Senden; deshalb im Adapter.
- DEGRADED bei fehlenden Klima-Werten: würde den Akku wegen der Klimaanlage stoppen; die Strategie setzt selbst 0.

## Watch out
- Im Dry-Run bleibt das Echo 0: die Schrittregel muss dort gegen den zuletzt entschiedenen Wert prüfen.
- `needsEvccConfirmation` im UI fragt bei jedem Aktor außer victron nach evcc; für `thermal.*` einschränken.
- `tests/unit/test_deploy_files.py` verbietet heute Ports am core; 6a braucht `5442:5442/udp`.
- Skalierung der Leistung (Einheit, Teiler) ist unbestätigt; erst `coe_probe listen` an der Anlage.

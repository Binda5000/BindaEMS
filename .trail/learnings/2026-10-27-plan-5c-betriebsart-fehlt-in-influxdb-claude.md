---
title: "Plan 5c: Betriebsart fehlt in InfluxDB, Dry-Run liefert nur passive Daten"
author: Claude
date: 2026-10-27
branch: claude/project-thread-x8izsc
cost: { minutes: 11, tokens: 240275 }
id: l-159a2bb5
scope: repo
status: active
src: agent
touches:
  - docs/superpowers/plans/2026-10-27-phase-5c-vorheizen-vorkuehlen.md
---

## Decided
- Plan 5c (docs/superpowers/plans/2026-10-27-phase-5c-vorheizen-vorkuehlen.md, wartet auf Freigabe): Grey-Box-Speicher je Betriebsart (Grundleistung u·ΔT, Zeitkonstante τ aus Auskühl-Phasen, Speicher C = τ·u), zweiter Planungsschritt als LP nach dem MILP für Akku und Autos (Optimierer bleibt unverändert, Rangfolge Akku → Autos → Klima durch Aufbau), Planwert `thermal_steps`/`thermal_grid_w` je Slot an den core. Schalter ab Werk aus, Vorschau immer.

## Watch out
- `core/telemetry/sampler.py` schreibt für die Klimaanlage nur actual_c, setpoint_c, offset_k, offset_sent_k und die Leistung; running/heating/cooling/venting fehlen in InfluxDB (`_float` lehnt bool ab). Ohne sie kein Heizmodell; Task 1 des Plans schließt das und lässt sich vorziehen.
- Im Dry-Run ist der Versatz immer 0: Die Reaktion auf einen Versatz zeigt sich erst im Live-Betrieb von 6a; bis dahin nur Grundleistung und τ aus passiven Daten.
- Extra-Last in einem Slot mit EIGENVERBRAUCH zieht der Akku, nicht das Netz: Vorheizen aus dem Netz nur in Slots ohne geplante Entladung, und im core ein Akku-Wächter.

---
title: "Betriebsbits der Klimaanlage stehen in InfluxDB status, nicht in thermal"
author: Claude
date: 2026-10-27
branch: claude/project-thread-x8izsc
cost: { minutes: 14, tokens: 311774 }
id: l-b65df27b
scope: repo
status: active
replaces: l-159a2bb5
src: agent
touches:
  - docs/superpowers/plans/2026-10-27-phase-5c-vorheizen-vorkuehlen.md
  - docs/uebergabe.md
---

## Decided
- Plan 5c Task 1 (Betriebsart aufzeichnen) entfällt: Die Bits running/heating/cooling/venting/cmd_on sind im CoE-Adapter `SignalKind.STATE` und gehen schon seit 6a bei jeder Änderung nach InfluxDB `status` (Tag `id` = `thermal.<name>.<bit>`, Feld `value_bool`, `core/telemetry/sampler.py` `_status`/`cadence_for`). Health nennt `mode`, `running`, `actual_c`, `base_setpoint_c` schon (`core/runtime.py` `_thermal_json`).
- Lesen für das Wärmemodell (Task 3, `app/learning/thermal_data.py`): letzter Wert vor dem Fenster plus Änderungen als Stufenfunktion, Zeitanteil je Viertelstunde.

## Tried and ruled out
- Die Bits zusätzlich als 0/1 im 10-s-Takt in `thermal` schreiben (eigenes `flag`-Feld in `_Rule`, Ausnahme in `cadence_for`): verlegt Daten, die seit 26.10.2026 in `status` liegen, bricht `test_thermal_states_and_wish_go_to_status` und `test_grafana`, und bringt nichts.

## Watch out
- Nur in `RULES` des Samplers zu suchen übersieht alles, was als Zustand nach `status` geht.
- Extra-Last in einem Slot mit EIGENVERBRAUCH zieht der Akku, nicht das Netz: Vorheizen aus dem Netz nur in Slots ohne geplante Entladung, im core ein Akku-Wächter. Im Dry-Run ist der Versatz immer 0; die Reaktion auf einen Versatz zeigt erst der Live-Betrieb von 6a.

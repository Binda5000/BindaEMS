---
title: "Vorrang der Autos: nur freigebbare Leistung der Klimaanlage"
author: Claude
date: 2026-10-19
branch: claude/project-thread-79ieqs
cost: { minutes: 40 }
touches:
  - "src/bindaems/core/control/regulation.py#_Thermal"
  - "src/bindaems/core/control/charging.py#ChargeController"
---

Im Vorrang der Autos (R23 c) darf für ein Auto bei „Laden aus“ nur die Leistung der Klimaanlage zählen, die die Strategie freigeben kann: über ihrer Leistung bei Versatz 0 und nur bei Wunsch über 0 (_Thermal.freeable_w). Die ganze Leistung zu zählen ließ die Klimaanlage beim Heizen (1,5 kW Grundlast) einem e-Golf weichen, der mit dem Überschuss nie starten konnte (Sim-Abwandlung test_klima_base_load_*). Die Grundlast lernt _Thermal.learn erst nach 300 s mit Versatz 0 (die Leistung folgt träge) und gleitend; ohne gelernte Leistung gilt die ganze (konservativ für die Autos). Dazu Hysterese auf die Startleistung: ein zählendes Auto zählt bis eine 1-A-Stufe darunter; unmet_surplus_w führt sie und wird deshalb einmal je Zyklus gerufen.

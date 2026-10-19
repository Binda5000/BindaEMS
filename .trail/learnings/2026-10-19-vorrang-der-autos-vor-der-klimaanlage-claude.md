---
title: "Vorrang der Autos vor der Klimaanlage: Hysterese"
author: Claude
date: 2026-10-19
branch: claude/project-thread-79ieqs
cost: { minutes: 93, tokens: 171537 }
touches:
  - "src/bindaems/core/control/charging.py#_Follow"
  - "src/bindaems/core/control/charging.py#ChargeController"
---

Eine reine Haltezeit (FOLLOW_HOLD_S) auf dem Urteil folgt/selbst begrenzt stoppt das Flackern Klimaanlage gegen Laderegelung nicht: Nimmt die Klimaanlage den Überschuss, senkt die Laderegelung den Strom (8 → 7 A), und ein bei 6 A abregelndes Auto folgt dann scheinbar wieder (eine Stufe Toleranz); die Schleife läuft mit ~20 min Periode weiter (Sim-Abwandlung test_klima_self_limiting_*). Deshalb merkt _Follow den Strom, bei dem sich das Auto begrenzte (limit_a), und es folgt erst wieder, wenn es bis auf eine Stufe diesen Strom zieht; „Laden aus“ (0 A) gilt für sich. Der Anlauf (RAMP_UP_S, ab note_verdict eines Starts/Anstiegs) deckt den Zyklus, in dem der Aktor nach dem Start noch „aus“ meldet (_commanded_a gibt dann None).

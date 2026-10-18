---
title: "Phase 6 Wärme und Kälte: Klimaanlage (UVR/CoE) und Wärmepumpe (Solarfocus)"
author: Claude
date: 2026-10-18
branch: claude/project-thread-79ieqs
cost: { minutes: 12, tokens: 72063 }
touches:
  - docs/superpowers/specs/2026-10-08-bindaems-design.md
---

## Decided
- Phase 6 in der Spec (Abschnitt 18, nach Phase 5 eingefügt): 6a Klimaanlage direkt nach 4b und vor Phase 5; 6b Wärmepumpe Solarfocus frühestens Herbst 2027, nach Phase 5. Nummer ist nicht die Reihenfolge.
- Gemeinsames Modell `thermal.devices` mit Leistungswunsch −n…+n; der Aktor übersetzt (CoE-Sollwertversatz bzw. SG-Ready/Sollleistung).
- Rangfolge PV-Überschuss: Akku (Plan-SOC) → Autos → Wärmeerzeuger → Einspeisung (Betreiber 18.10.2026).
- Bestätigung des Versatzes über ein Echo der UVR (Betreiber 18.10.2026); zurück auf 0 immer sofort, Rate-Limit nur weg von 0.

## Tried and ruled out
- Klima-Strategie mit rohem PV-Überschuss: würde mit Akku/Autos um dieselben Watt regeln.
- HA-Leistungswerte für die Regelung: bis 30 min alt; nur für die Statistik.
- PV-Überschuss-Logik der Wärmepumpe selbst (Register 33407–33409): regelt gegen BindaEMS.

## Watch out
- Die UVR hat einen Timeout (Versatz → 0), die Solarfocus-Register nicht: 6b braucht Rückfall und Schreibbudget.
- Wärmepumpe nicht an den AC-Out der MultiPlus (USV-Reserve), nicht hinter einen eigenen Zähler.
- ems-core braucht für 6a `5442/udp`; heute veröffentlicht er keine Ports.
- Offen beim Betreiber: CoE-Empfangstabelle, IPs, Timeout-Zeit, Echo und F51-Leistung als CAN-Ausgänge. Details: /mnt/project-files/waermepumpe/.

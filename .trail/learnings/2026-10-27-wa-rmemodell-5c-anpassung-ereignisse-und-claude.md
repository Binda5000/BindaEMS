---
title: "Wärmemodell 5c: Anpassung, Ereignisse und Testhaus"
author: Claude
date: 2026-10-27
branch: claude/project-thread-x8izsc
cost: { minutes: 26, tokens: 40976 }
id: l-70c438d0
scope: repo
status: active
src: agent
---

## Decided
- `thermal_model.py`: Versatz-Ereignisse nehmen den Wert des Slots nach dem Sprung (Viertelstundenmittel: der Sprung-Slot ist meist ein Teilwert) und teilen die Energie durch `Versatz · Richtung` (Heizen +1, Kühlen −1), nicht durch den Betrag: so ergibt auch ein Absenken einen positiven Speicher je K. Ein Ereignis, das vor dem Einschwingen den Versatz ändert oder Daten verliert, zählt nicht; nach 3 h ohne Einschwingen zählt es (Deckel).
- `u ≤ 0` ist `poor_fit` (kein Heizen/Kühlen, `C` wäre negativ); ist die PV in den Anpassungs-Slots konstant, bleibt `s = 0`; ist `ΔT` konstant, gibt es keine Anpassung. Eine Phase mit wachsendem Abstand hat `tau_h = inf`, das Modell dann `tau_h = None` und `implausible_tau`.

## Watch out
- Mit „Ist–Soll höchstens 0,5 K“ fällt das Nachheizen nach einer Auskühl-Phase (rund `C · 0,5 K` je Phase) in die Anpassung. Im Testhaus verzerrt das `s` (−114 statt −60 W/kW), weil die Mehrleistung zu Zeiten ohne PV liegt; `u` bleibt in 3 %. Darum prüfen die Tests für `s` nur das Vorzeichen.
- Testhaus: Auskühlen abends 18–22 Uhr (ohne PV, Außentemperatur dann konstant); mit nächtlicher Pause und Regelung ohne Nachlauf lag R² unter 0,5, weil der eine Slot mit voller Leistung am Ende des Nachheizens die Streuung beherrscht.

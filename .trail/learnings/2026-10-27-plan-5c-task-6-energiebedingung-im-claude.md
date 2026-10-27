---
title: "Plan 5c Task 6: Energiebedingung im LP der Verschiebung"
author: Claude
date: 2026-10-27
branch: claude/project-thread-x8izsc
cost: { minutes: 65, tokens: 192872 }
id: l-2cb38893
scope: repo
status: active
src: agent
---

## Decided
- `thermal_shift` hat über Präzisierung 6 hinaus die Energiebedingung `E = Σ (up − dn) · Δ / 1000 ≥ E_min` (Ruling Fix-Runde 2, ersetzt `S_{−1} + E ≥ 0` und `E ≥ −max(0, S_{−1})`); `E_min` ist die kleinste Mehrenergie im LP der Grundlinie (eigenes LP, Ziel `min E`), ohne Abweichung jetzt 0 und nicht gerechnet. Grund: Mit der Rekursion `S_t = a · S_{t−1} + …` führt `a` ein abgesenktes Haus (`S < 0`) ohne Strom zum Soll zurück; das wörtliche LP senkte deshalb schon bei gleichem Preis den ganzen Horizont um `down_k` ab (Testhaus 96 Slots zu 20 ct: 21 ct „Ersparnis“, mit C 3 kWh/K und 30 ct 47 ct). Die Zwischenform `E ≥ −max(0, S_{−1})` ließ bei einem Start neben dem Soll tiefer absenken (Start −0,25 K, 48 Slots zu 20 ct: 3,72 ct, „ok“).
- Ersparnis gegen eine Grundlinie (Ruling Fix-Runde 1): dasselbe LP mit dem Band `[min(0, S_{−1}), max(0, S_{−1})]`; `saving_ct = Grundlinie − Kosten`. Ohne sie zahlte beim Neuplanen jede Viertelstunde die Verschiebung den Rückweg aus einem Defizit, das ohnehin zurückmuss, und brach die Absenkung mitten im teuren Block ab (Neuplanung ab Slot 31 des Vorheiz-Falls: −4,12 ct, „lohnt nicht“).
- Die erste Form `S_{−1} + E ≥ 0` macht die Grundlinie bei `S_{−1} < 0` immer unlösbar: `S_{−1} + E = S_end + (1 − a) · Σ S_{t−1}`, und mit `S ≤ 0` ist das negativ.
- In Slots mit Einspeisung sind `dn_g` und `dn_b` 0 (Ruling): sonst hoben sich `up_x` und `dn_g`/`dn_b` im selben Slot auf und buchten eine Scheinersparnis.
- Fix-Runde 3: `saving_ct` rechnet Hauptlauf und Grundlinie zu echten Preisen ohne Marge (Ruling); die Marge bleibt Hürde in den Zielen. Untergrenze des Hauptlaufs ist die Mehrenergie der gelösten Grundlinie (`≥ E_min`), nicht `E_min`: Mit Marge 20 hält die Grundlinie ein Defizit nicht mit Minderverbrauch und braucht mehr als `E_min`; mit `E ≥ E_min` buchte der Hauptlauf tieferes Absenken zu echten Preisen als Ersparnis (Start −0,5 K, down_k 1, 96 Slots zu 20 ct: 3,73 ct „ok“). Damit ist `E_min` nur noch Untergrenze der Grundlinie, dort ohne Wirkung.
- `steps` rundet genau dazwischen Richtung 0 wie `core/safety/ranges.py` `_check_offset`, nach `round(x, 6)` gegen Reste des Solvers.

## Watch out
- Auch mit der Bedingung bleibt das Haus nach dem teuren Abschnitt bis kurz vor Horizontende abgesenkt, wenn die Preise danach gleich sind: späteres Nachheizen ist wegen `a` billiger. Wer früheres Zurückkehren will, braucht einen Komfortterm.
- Einspeisung zu 5 ct bei 20 ct Bezug ist kein „gleicher Preis“: Minderverbrauch am Bezug vor einem PV-Slot und Nachheizen aus der Einspeisung ist eine echte Verschiebung. Ein Test auf die Scheinersparnis braucht deshalb einen Horizont nur aus Slots mit Einspeisung.

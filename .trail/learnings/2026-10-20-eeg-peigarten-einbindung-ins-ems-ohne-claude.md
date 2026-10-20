---
title: "EEG Peigarten: Einbindung ins EMS ohne Codeänderung"
author: Claude
date: 2026-10-20
branch: claude/project-thread-707vv8
cost: { minutes: 18, tokens: 127531 }
---

## Decided
- EEG Peigarten (regional, UW Peigarten Netz NÖ): Einspeisung 9,00 ct 06–20 / 10,50 ct 20–06 netto, Bezug 11,50 ct ohne USt., Gebührenersparnis ca. 3,18 ct netto. Ergebnis in /mnt/project-files/eeg-peigarten/eeg-peigarten-im-ems.md.
- Ohne Code nur: Einspeisebestandteil im Tarif-Editor mit Wert Anteil × (EEG-Preis − OeMAG), gültig ab EEG-Start, Anteil je Quartal aus der Abrechnung nachstellen.
- Binda will tagsüber aus der EEG in Akku/Autos laden: braucht eigene Phase (EEG-Überschussprognose aus eigenen 15-min-Werten gegen PV-Prognose, Bezug in zwei Quellen im Optimierer, R1 für EEG-Strom lockern).

## Tried and ruled out
- EEG-Bezug als fester Mittelwert im Tarif: täuscht dem Planer billigen Strom auch ohne EEG-Überschuss vor.
- Vergleich EEG-Bezug netto gegen Börse netto war falsch: auf den EEG-Preis kommt keine USt. EEG ist billiger, sobald Börse + Aufschlag über ca. 6,4 ct netto liegen.

## Watch out
- Regel R1 (Spec, Optimierer) verbietet Netzladen, solange später PV eingespeist wird; der Optimierer plant nur PV-Einspeisung (pv_x), nie Akku ins Netz.
- Einspeisebestandteile sind ein fester Aufschlag auf den OeMAG-Monatswert und folgen ihm nicht.

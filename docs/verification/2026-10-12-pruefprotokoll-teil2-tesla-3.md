# Prüfprotokoll Teil 2 – Tesla – 2026-10-12

| Angabe | Wert |
|---|---|
| Werkzeug | verify_part2 |
| Datum | 2026-10-12 |
| Punkt | tesla |
| Gerät | Tesla Model 3 |
| Venus OS | v3.80 |

| Punkt | Prüfung | Ergebnis | Details |
|---|---|---|---|
| 17.2-7 | Tesla bereit | OK | SOC 70 %, Ladestrom 5 A, Charging |
| 17.2-7 | Ladestrom 10 A | OK | bestätigt nach 30,4 s, Wall Connector 4 s |
| 17.2-7 | Verhalten bei 5 A | OK | bestätigt nach 60,7 s, Wall Connector 34,4 s. Erster Befehl: Fahrzeug übernahm 5 A nicht (Tessie meldet 10 A); wiederholt nach 30,3 s. |
| 17.2-7 | Laden aus und an | OK | aus nach 2,5 s (Schütz offen nach 4 s), an nach 2,5 s |

## Bewertung vom 12.10.2026

- **Bestanden.** Dritter Lauf des Tages, mit den Images, in denen der Tesla-Aktor den Strom im
  Tessie-Cache prüft und das Werkzeug einen nicht übernommenen Befehl einmal wiederholt (Nachtrag
  zu `2026-10-12-pruefprotokoll-teil2-tesla-2.md`). Zusammen mit dem Schlaf-Test
  (`2026-10-12-pruefprotokoll-teil2-tesla-schlafend.md`) ist Punkt 17.2-7 bestanden.
- **Ausgangswert 5 A:** Das Auto stand noch auf dem Strom aus dem Versuch.
- **Ladestrom 10 A (5 → 10 A):** beim ersten Befehl übernommen. Der Wall Connector war nach 4 s
  bei 10 A. Bestätigt war der Strom nach 30,4 s, mit der ersten Meldung von Tessie, die die
  Prüfung wertet.
- **Verhalten bei 5 A (10 → 5 A):** Der erste Befehl wirkte nicht. Tessie meldete nach 30 s
  weiter 10 A, und auch der Wall Connector blieb bei 10 A; es hinkte also nicht nur der Cache.
  Das Werkzeug wiederholte nach 30,3 s. Rund 4 s danach (34,4 s ab dem ersten Senden) war der
  Wall Connector bei 5 A, bestätigt nach 60,7 s. Das ist der Fall aus dem Versuch: Die Prüfung
  im Cache erkennt ihn, die Wiederholung wirkt.
- **Wann der erste Befehl verloren geht:** Ein erster Befehl „auf 5 A“ wirkte in allen vier
  Fällen nicht: 10 → 5 A in den Läufen 1 bis 3, 6 → 5 A im Versuch. Wo ein zweiter folgte (im
  Versuch und in diesem Lauf), wirkte er. „Von 5 A weg“ ist uneinheitlich: 5 → 6 A wurde im
  Versuch nicht übernommen, 5 → 10 A in diesem Lauf beim ersten Mal. Die Prüfung im Cache deckt
  beide Fälle ab.
- **Prüfung im Cache:** In beiden Schritten zeigte die erste gewertete Meldung, was der Wall
  Connector maß; eine Fehlmeldung durch einen hinkenden Cache gab es nicht. Diese Meldung kam
  aber erst rund 30 s nach dem Senden (Abfrageabstand des Adapters beim Laden). Ob Tessies Cache
  den neuen Strom schon nach 10 s zeigt (`SETTLE_S`), lässt sich daraus nicht ablesen.
- **Dauer:** Bestätigt ist ein Strom jetzt nach rund 30 s, mit Wiederholung nach rund 60 s, statt
  nach 2,5 s wie vor der Prüfung im Cache.
- **Kein Wiederherstellen:** Der Ausgangswert war 5 A, und nach dem letzten Strom-Schritt stand
  wieder 5 A. Das Werkzeug schreibt dann nichts. Das Auto steht deshalb weiter auf 5 A; den
  gewünschten Strom stellt der Betreiber in der Tesla-App ein.
- **Laden aus und an:** wirkt; der Schütz des Wall Connectors öffnete nach 4 s.

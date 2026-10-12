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
| 17.2-7 | Tesla bereit | OK | SOC 69 %, Ladestrom 13 A, Charging |
| 17.2-7 | Ladestrom 10 A | OK | Tessie 2,5 s, Wall Connector 4 s |
| 17.2-7 | Verhalten bei 5 A | WARNUNG | Tessie 0,5 s; Wall Connector nach 90 s nicht bei 5 A (Ist 10,1 A) |
| 17.2-7 | Wiederherstellen: Strom 13 A | OK | Wiederhergestellt nach 2,5 s |
| 17.2-7 | Laden aus und an | OK | aus nach 2,5 s (Schütz offen nach 4 s), an nach 2,5 s |

## Bewertung vom 12.10.2026

- **Wiederholung des Laufs vom selben Tag** (`2026-10-12-pruefprotokoll-teil2-tesla.md`), diesmal
  mit 13 A als Ausgangswert, damit „Ladestrom 10 A“ eine messbare Änderung ist.
- **Bestanden:** Das Auto folgt einem Strombefehl über Tessie: 13 A → 10 A, von Tessie nach 2,5 s
  bestätigt, am Wall Connector nach 4 s. Das Wiederherstellen auf 13 A und „Laden aus und an“
  wirken ebenfalls.
- **Nicht bestanden: 5 A.** Der Strom am Wall Connector blieb nach 90 s bei 10,1 A. Tessie
  bestätigte den Befehl nach 0,5 s, auffällig schneller als die wirksamen Befehle (2,5 s). In der
  Tesla-App lässt sich 5 A einstellen, und das Auto folgt (Betreiber). Das Auto kann 5 A also; der
  Befehl über Tessie wirkt bei 5 A nicht. Ursache offen, zu klären vor der Live-Schaltung des
  Tesla.
- **Offen:** die Ursache für 5 A über Tessie und der Schlaf-Test (`--schlafend`).

## Nachtrag vom 12.10.2026: Versuch mit `set_charging_amps` über Tessie

Direkt über die Tessie-API (`wait_for_completion=true`), während das Auto lud, je Befehl 30 s
Abstand; der Zustand jeweils live von Tessie:

| Befehl | Antwort von Tessie | Strom 30 s danach |
|---|---|---|
| 6 A (Auto stand auf 6 A) | `result: true` nach 2,5 s | 6 A |
| 5 A | `result: true` nach 0,2 s | 6 A, nicht übernommen |
| 5 A, zweites Mal | `result: true` nach 1,2 s | 5 A |
| 6 A | `result: true` nach 0,2 s | 5 A, nicht übernommen |

- **Befund:** Ein Befehl, der den Strom auf 5 A setzt oder von 5 A weggeht, wirkt erst beim
  zweiten Mal. Tessie bestätigt den ersten trotzdem, auffällig schnell. Wechsel zwischen 10 und
  13 A wirkten in beiden Läufen beim ersten Mal. `result: true` heißt also nicht sicher, dass das
  Auto den Wert übernommen hat.
- **Folge:** Das Auto blieb nach dem Versuch auf 5 A stehen; der Betreiber stellt den Strom in der
  App zurück.
- **Umgesetzt am 12.10.2026:** Der Tesla-Aktor prüft den Strom nach der Bestätigung durch Tessie
  im Tessie-Cache, mit der ersten Meldung ab 10 s und bis 40 s danach. Zeigt sie den alten Wert,
  meldet er „nicht übernommen“. Dann geht der Befehl ein zweites Mal hinaus: im core als nächster
  Versuch des sicheren Zustands durch die Safety-Schicht, im Werkzeug als Wiederholung („Befehl
  wiederholt“ im Befund). `min_a` kann deshalb bei 5 A bleiben. Ein weiterer Lauf des Punkts
  `tesla` mit den neuen Images soll zeigen, dass „Verhalten bei 5 A“ jetzt wirkt.

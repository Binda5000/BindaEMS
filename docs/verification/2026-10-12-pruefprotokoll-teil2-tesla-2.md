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

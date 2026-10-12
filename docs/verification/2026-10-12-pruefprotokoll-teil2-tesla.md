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
| 17.2-7 | Tesla bereit | OK | SOC 68 %, Ladestrom 10 A, Charging |
| 17.2-7 | Ladestrom 10 A | OK | Tessie 0,5 s; Wirkung nicht messbar (Strom am Wall Connector schon bei 10 A) |
| 17.2-7 | Verhalten bei 5 A | WARNUNG | Tessie 0,5 s; Wall Connector nach 90 s nicht bei 5 A (Ist 10,1 A) |
| 17.2-7 | Wiederherstellen: Strom 10 A | OK | Wiederhergestellt nach 0,5 s |
| 17.2-7 | Laden aus und an | OK | aus nach 1,5 s (Schütz offen nach 2 s), an nach 2,5 s |

## Bewertung vom 12.10.2026

- **Teilweise bestanden.**
- **Vorläufe am selben Tag:** Ein erster Lauf endete bei „Tesla bereit“ mit „Fahrzeug nicht zu
  Hause.“, obwohl das Auto zu Hause stand. Weitere Läufe endeten mit „Fahrzeug schläft oder ist
  offline; es wird nicht geweckt.“, bis der Betreiber das Laden in der Tesla-App startete und das
  Auto damit weckte. Dieser Bericht ist der Lauf danach; er hat die früheren Berichte
  überschrieben. Ursache für „nicht zu Hause“ war eine falsche `site.location` in der
  `config.yaml`; der Betreiber hat sie vor diesem Lauf korrigiert. Die PV-Prognose nutzt dieselbe
  Position.
- **Nicht geweckt:** Das Werkzeug hat das schlafende Auto nicht geweckt, wie vorgesehen.
- **Befehle über Tessie:** Ladestrom und „Laden aus und an“ bestätigte Tessie nach 0,5 s.
- **Laden aus und an:** wirkt. Der Schütz des Wall Connectors öffnete nach 2 s, das Laden ging
  nach 2,5 s wieder an.
- **Nicht belegt: Folgt das Auto einem neuen Ladestrom?**
  - Bei 10 A lag der Strom schon an; die Wirkung war nicht messbar.
  - Bei 5 A, dem Mindeststrom laut `config.yaml`, blieb der Strom am Wall Connector nach 90 s bei
    10,1 A, obwohl Tessie den Befehl bestätigte (WARNUNG).
  - Ursache offen: Entweder nimmt das Auto unterhalb eines eigenen Mindeststroms keine Änderung
    an, oder es setzt Strombefehle nicht um.
- **Offen:** Wiederholung mit messbarem Stromwechsel und der Schlaf-Test. Die Wiederholung folgte
  am selben Tag (`2026-10-12-pruefprotokoll-teil2-tesla-2.md`).

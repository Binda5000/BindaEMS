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
| 17.2-7 | Schlafendes Auto wird nicht geweckt | OK | 10 min ohne Wecken, Befehle verweigert |

## Bewertung vom 12.10.2026

- **Bestanden.** Aufruf mit `--punkt tesla --schlafend`: Das Werkzeug sendet dabei nichts. Tessie
  meldete zu Beginn und am Ende frisch, dass das Auto schläft. Dazwischen verweigerte der Aktor 10
  min lang jeden Befehl mit „Fahrzeug schläft oder ist offline; es wird nicht geweckt.“, und das
  Auto blieb im Schlaf.
- Zusammen mit dem dritten Lauf des Punkts (`2026-10-12-pruefprotokoll-teil2-tesla-3.md`) ist
  17.2-7 bestanden.

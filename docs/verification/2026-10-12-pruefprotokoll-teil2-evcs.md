# Prüfprotokoll Teil 2 – EVCS – 2026-10-12

| Angabe | Wert |
|---|---|
| Werkzeug | verify_part2 |
| Datum | 2026-10-12 |
| Punkt | evcs |
| Gerät | EVCS „evcs“ |
| Venus OS | v3.80 |
| EVCS-Firmware | v2.09 |

| Punkt | Prüfung | Ergebnis | Details |
|---|---|---|---|
| 17.2-6 | EVCS bereit | OK | Instanz 40, manueller Modus, Sollstrom 14 A, Laden an |
| 17.2-6 | Strom 6 A | OK | bestätigt nach 1 s |
| 17.2-6 | Strom 10 A | OK | bestätigt nach 1 s |
| 17.2-6 | Strom 16 A | OK | bestätigt nach 1,5 s |
| 17.2-6 | Wiederherstellen: Strom 14 A | OK | Wiederhergestellt nach 1 s |
| 17.2-6 | Laden aus und an | OK | aus nach 0,5 s, an nach 1 s |
| 17.2-6 | Fremde Änderung | OK | erkannt nach 3 Prüfungen (8 A statt 14 A) |
| 17.2-6 | Wiederherstellen: Strom 14 A | OK | Wiederhergestellt nach 1 s |
| 17.2-6 | Verbindung unterbrochen | INFO | übersprungen |
| 17.2-5 | Watchdog erreichbar | OK | Version 1.1.0, Zustand wartet |
| 17.2-5 | Watchdog-Version | OK | 1.1.0 |
| 17.2-5 | Scharf nach Heartbeat | OK | scharf nach 30 s |
| 17.2-5 | Auslösung ohne Heartbeat | OK | ausgelöst nach 91 s, Auslösungen 2 → 3, fehlgeschlagene Aktionen 0 |
| 17.2-5 | Wieder scharf | OK | scharf nach 31 s |
| 17.2-5 | Freigabe ohne Auslösung | OK | Heartbeat 0: Zustand wartet |
| 17.2-5 | Sicherer Strom über den Watchdog | OK | Watchdog setzte 6 A (vorher 10 A) |
| 17.2-6 | Wiederherstellen: Strom 14 A | OK | Wiederhergestellt nach 0,5 s |

## Bewertung vom 12.10.2026

- **Bestanden, mit einer Lücke.**
- **Schreibweg bestätigt (17.2-6):** Der Sollstrom über den GX-Dienst `evcharger` des Cerbo
  (Instanz 40) kam bei 6, 10 und 16 A nach 1 bis 1,5 s zurück, „Laden aus und an“ nach 0,5 s
  bzw. 1 s. Damit ist der Schreibweg nach der Entscheidung des Betreibers vom 11.10.2026 an der
  Anlage bestätigt.
- **Fremde Änderung:** Ein von Hand gesetzter Strom (8 A statt 14 A) wurde nach 3 Prüfungen
  erkannt, wie vorgesehen (3 Treffer in Folge).
- **Watchdog mit verstelltem Strom (17.2-5):** Auslösung ohne fehlgeschlagene Aktion; der Watchdog
  setzte den sicheren Strom 6 A (vorher 10 A), danach Freigabe mit der 0.
- **Wiederherstellen:** Jeder Block endete wieder mit dem vorgefundenen Sollstrom 14 A.
- **Lücke:** „Verbindung unterbrochen“ ist übersprungen (Rückfrage verneint). Wie die EVCS und
  das Werkzeug einen Verbindungsabbruch überstehen, ist an der Anlage noch nicht belegt. Bei
  Gelegenheit den Punkt `evcs` wiederholen und diesen Schritt mit `j` ausführen.

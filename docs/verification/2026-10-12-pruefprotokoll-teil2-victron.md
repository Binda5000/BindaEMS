# Prüfprotokoll Teil 2 – Victron – 2026-10-12

| Angabe | Wert |
|---|---|
| Werkzeug | verify_part2 |
| Datum | 2026-10-12 |
| Punkt | victron |
| Gerät | Victron (ESS) |
| Venus OS | v3.80 |

| Punkt | Prüfung | Ergebnis | Details |
|---|---|---|---|
| 17.2-1 | Victron bereit | OK | hub4-Overrides vorher: feedinexcess = 0, forcecharge = 0, maxchargepower = null, maxdischargepower = null, setpoint = null; Entladegrenze vorher: unbegrenzt |
| 17.2-1 | Netz-Sollwert-Override | OK | 1000 W nach 0,5 s bestätigt; Netz vorher 16 W, mit Override 810 W; aufgehoben nach 0,5 s, danach 282 W |
| 17.2-1 | Override ohne Timeout | INFO | nach 5 min verschwunden |
| 17.2-1 | Wiederherstellen: kein Netz-Sollwert-Override | OK | Wiederhergestellt nach 0 s |
| 17.2-1 | Override nach Neustart des Cerbo | OK | nach dem Neustart aufgehoben |
| 17.2-1 | Wiederherstellen: kein Netz-Sollwert-Override | OK | Wiederhergestellt nach 0 s |
| 17.2-1 | Zusammenspiel mit dem persistenten Sollwert | INFO | AcPowerSetPoint 10 W blieb unverändert |
| 17.2-2 | Override für die Entladegrenze | INFO | ess.override.maxdischargepower existiert |
| 17.2-2 | Entladesperre | OK | Akku entlädt höchstens 0 W |
| 17.2-2 | Wiederherstellen: Entladegrenze unbegrenzt | OK | Wiederhergestellt nach 0,5 s |
| 17.2-3 | Netzladen | OK | Akku lädt mit 945 W |
| 17.2-4 | Kein Akku ins Auto | INFO | übersprungen: kein Laden an der EVCS |
| 17.2-8 | Importlimit | INFO | höchster Netzstrom 4,9 A (L2), Akku −1139 W |

## Bewertung vom 12.10.2026

- **Ausgeführt mit vorübergehend abgeschaltetem Dynamic ESS** (Entscheidung des Betreibers vom
  12.10.2026; im Betrieb bleibt DESS an, bis Victron live geht).
- **Ruhezustand geklärt (17.2-1):** Ohne Override meldet der Cerbo `setpoint = null`. Die
  vorläufige Annahme aus Plan 2b (Präzisierung 1), dass `null` „kein Override“ bedeutet, ist
  damit bestätigt.
- **Netz-Sollwert-Override bestätigt:** 1000 W kamen nach 0,5 s zurück, das Netz ging von 16 W auf
  810 W. Das Aufheben mit `null` kam ebenfalls nach 0,5 s zurück. Nach einem Neustart des Cerbo
  ist der Override weg, und der persistente Sollwert (`AcPowerSetPoint` 10 W) bleibt unberührt.
- **Neu: Der Override hat einen Timeout.** Ohne Auffrischen war er nach 5 min verschwunden; Spec
  3.1 ging von „kein Timeout“ aus. Folge für 2c: Ein gewollter Override muss vor Ablauf
  aufgefrischt werden. Für die Sicherheit ist das günstig: Ein Override eines ausgefallenen core
  verschwindet spätestens nach 5 min auch ohne Watchdog.
- **Entladegrenze (17.2-2):** Die Entladesperre über die persistente Entladegrenze wirkt
  (höchstens 0 W), das Wiederherstellen auf „unbegrenzt“ auch. Es gibt außerdem einen flüchtigen
  Override `ess.override.maxdischargepower`; ob er die persistente Einstellung in 2c ersetzen kann
  (schont den Flash), ist noch zu prüfen.
- **Netzladen (17.2-3):** wirkt, der Akku lud mit 945 W bei einem Override von 2000 W.
- **Offen:**
  - 17.2-4 „Kein Akku ins Auto“ ist übersprungen, weil an der EVCS nichts lud. Wiederholen,
    während ein Auto an der EVCS lädt.
  - 17.2-8 Importlimit: Das Limit von 20 A je Phase wurde nicht erreicht (höchster Netzstrom
    4,9 A, der Akku deckte die Last). Der Punkt ist damit nicht belegt.

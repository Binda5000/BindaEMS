# Prüfprotokoll Teil 2 – Watchdog-Probe – 2026-10-12

| Angabe | Wert |
|---|---|
| Werkzeug | watchdog_probe |
| Datum | 2026-10-12 |

| Punkt | Prüfung | Ergebnis | Details |
|---|---|---|---|
| 17.2-5 | Watchdog erreichbar | OK | Version 1.1.0, Zustand wartet |
| 17.2-5 | Watchdog-Version | OK | 1.1.0 |
| 17.2-5 | Scharf nach Heartbeat | OK | scharf nach 31 s |
| 17.2-5 | Auslösung ohne Heartbeat | OK | ausgelöst nach 91 s, Auslösungen 1 → 2, fehlgeschlagene Aktionen 0 |
| 17.2-5 | Wieder scharf | OK | scharf nach 30 s |
| 17.2-5 | Freigabe ohne Auslösung | OK | Heartbeat 0: Zustand wartet |

## Bewertung vom 12.10.2026

- **Bestanden:** Alle Schritte sind OK, jetzt mit Watchdog 1.1.0 (Update mit `install.sh` am
  selben Tag). Die Zeiten passen wie am 11.10.2026 zur `config.ini`: scharf nach 31 s
  (`rearm_s` = 30), ausgelöst nach 91 s ohne Heartbeat (`timeout_s` = 90), wieder scharf nach
  30 s, Freigabe mit der 0.
- **Fehlgeschlagene Aktionen 0:** Seit 1.1.0 liest die Probe die Zahl erst nach „Wieder scharf“;
  sie ist also der Endstand des Aktionslaufs.
- **Auslösungen 1 → 2:** Der Zähler der Probe vom 11.10.2026 hat das Update überstanden;
  `install.sh` behält `state.json`.
- **Nicht im Bericht:** Ob jede Aktion „ok“ oder „unverändert“ war, steht nur im Log des Cerbo
  (Zeile „Aktionen abgeschlossen: …“). Diese Zeile liegt dem Protokoll nicht bei.

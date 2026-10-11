# Prüfprotokoll Teil 2 – Watchdog-Probe – 2026-10-11

| Angabe | Wert |
|---|---|
| Werkzeug | watchdog_probe |
| Datum | 2026-10-11 |

| Punkt | Prüfung | Ergebnis | Details |
|---|---|---|---|
| 17.2-5 | Watchdog erreichbar | OK | Version 1.0.0, Zustand wartet |
| 17.2-5 | Watchdog-Version | OK | 1.0.0 |
| 17.2-5 | Scharf nach Heartbeat | OK | scharf nach 30 s |
| 17.2-5 | Auslösung ohne Heartbeat | OK | ausgelöst nach 91 s, Auslösungen 0 → 1, fehlgeschlagene Aktionen 0 |
| 17.2-5 | Wieder scharf | OK | scharf nach 31 s |
| 17.2-5 | Freigabe ohne Auslösung | OK | Heartbeat 0: Zustand wartet |

## Bewertung vom 11.10.2026

- **Bestanden:** Alle Schritte sind OK. Die Zeiten passen zur `config.ini`: scharf nach 30 s
  stabilem Heartbeat (`rearm_s` = 30), ausgelöst nach 91 s ohne Heartbeat (`timeout_s` = 90).
  Die Auslösung lief ohne fehlgeschlagene Aktion, danach wurde der Watchdog wieder scharf und mit
  der 0 freigegeben.
- **Stand:** Watchdog 1.0.0 auf Venus OS v3.80 (Firmwarestände siehe Prüfprotokoll Teil 1 vom
  selben Tag).
- **Nicht im Bericht:** Ob jede Aktion „ok“ oder „unverändert“ war, steht nur im Log des Cerbo
  (Zeile „Aktionen abgeschlossen: …“). Diese Zeile liegt dem Protokoll nicht bei.
- Die Probe belegt die Kette vom EMS bis zu den Aktionen, die Auslösung, das erneute Scharfwerden
  und die Freigabe. Die übrigen Punkte von Teil 2 (Spec 17.2) folgen mit Plan 2b.

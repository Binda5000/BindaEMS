# Prüfprotokolle

Hier liegen die Prüfprotokolle an der Anlage (Spec 17). Jedes Protokoll hat Datum,
Firmwarestände sowie erwartetes und tatsächliches Verhalten.

## Teil 1 – nur lesend (Phase 1)

Das Werkzeug `bindaems.tools.verify_part1` liest ausschließlich. Am Cerbo publiziert es nur
den MQTT-Keepalive. Es schreibt keine Einstellungen und weckt kein Fahrzeug.

| Quelle | Was gelesen wird |
|---|---|
| Cerbo GX (MQTT) | alle Topics `N/<portal>/#` für die angegebene Dauer, alle 5 s eine Probe |
| EVCS NS (Modbus) | Register 5000–5199; Blöcke mit Lücken werden einzeln gelesen |
| Wall Connector | `/api/1/vitals`, `/api/1/lifetime`, `/api/1/version` |
| Tessie | `GET /{vin}/state?use_cache=true` |
| Home Assistant | `GET /api/states/<entity_id>` für jede konfigurierte Entität |
| InfluxDB | `SHOW DATABASES`, Retention Policies von `bindaems`, Messungen der HA-Datenbank |

### Aufruf

Der Aufruf erfolgt auf der VM im Container, mit denselben Secrets wie der core (`.env`):

```bash
cd /opt/bindaems
mkdir -p verification && sudo chown 10001 verification
docker compose run --rm -v "$PWD/verification:/out" --entrypoint python ems-core \
  -m bindaems.tools.verify_part1 --config /config/config.yaml --duration 120 --out /out
```

Lokal aus dem Repository geht es mit:
`uv run python -m bindaems.tools.verify_part1 --config deploy/config.yaml --out docs/verification/`.

### Ergebnis

- `YYYY-MM-DD-pruefprotokoll-teil1.md` enthält den Bericht mit der Tabelle
  `Punkt | Prüfung | Ergebnis | Details`. Die Punkte entsprechen Spec 17.1, die Ergebnisse
  lauten OK, WARNUNG, FEHLER oder INFO.
- `YYYY-MM-DD-pruefprotokoll-teil1-rohdaten.json` enthält alle Rohdaten.
  - Token und Passwörter werden nie ausgegeben.
  - Koordinaten (`latitude`/`longitude`) und die VIN werden geschwärzt.
  - Vom Cerbo werden nur die benötigten Teilbäume gespeichert (Messwerte, ESS-, DESS- und
    DVCC-Einstellungen, Firmware); andere Einstellungen wie die BLE-PIN nicht.
  - Trotzdem enthalten die Rohdaten Portal-ID, Seriennummern und alle Messungen der
    HA-Datenbank. Solange das Repository öffentlich ist, wird nur der Bericht eingecheckt.
- Ist eine Quelle nicht erreichbar, steht im Bericht ein FEHLER mit der Meldung. Die übrigen
  Prüfungen laufen trotzdem.

### Danach

1. Abweichungen gegenüber `config.yaml` korrigieren, z. B. Instanzen unter
   `victron.instances` oder die erwarteten Werte unter `victron.expected`.
2. Warnungen bewerten. Zwei Beispiele:
   - Die Vorzeichenkonvention des Netzstroms lässt sich nur bei Einspeisung über 100 W
     bestimmen.
   - Ein aktiver Ladeplan im Fahrzeug muss aus.
   - „EVCS-Registerabbild leer“: Unter `host`, `port` und `unit_id` antwortet etwas, aber
     nicht die EVCS. Der Bericht nennt den Weg, über den der Cerbo die Wallbox liest. Der Cerbo
     findet sie per mDNS und nutzt den dort angekündigten Port; ihn zeigt der Cerbo unter den
     gespeicherten Modbus-TCP-Geräten. `host` und `port` dorthin stellen und Teil 1
     wiederholen.
3. Den Bericht mit Datum einchecken.

### Manuell zu prüfen

- **NTP am Cerbo (17.1-12):** Unter Einstellungen → Allgemein → Datum & Uhrzeit muss die Zeit
  synchron sein. Das Werkzeug prüft die Uhrzeit nur bei HA und InfluxDB, über deren HTTP-Header
  `Date` mit einer Auflösung von 1 s.
- **Sicherheitsprofil und Modbus-Dienstliste (17.1-3):** am Cerbo ablesen und im Bericht
  ergänzen.
- **Preise (17.1-13):** eigenes Protokoll, siehe Abschnitt „Preise“.

### Wiederholung

Nach jedem Update von Venus OS, der EVCS-Firmware oder der Wall-Connector-Firmware wiederholst
du Teil 1. Die betroffenen Punkte vergleichst du mit dem letzten Protokoll.

## Preise (17.1-13)

Der Befehl `verify-prices` der app ruft smartENERGY sowie die Referenzquellen Energy-Charts und
aWATTar für heute und morgen ab. Er braucht keine Konfiguration und schreibt nur Dateien in das
Zielverzeichnis.

### Aufruf

```bash
cd /opt/bindaems
mkdir -p verification && sudo chown 10001 verification
docker compose run --rm -v "$PWD/verification:/out" ems-app verify-prices --out /out
```

Lokal aus dem Repository geht es mit
`uv run python -m bindaems.app verify-prices --out docs/verification/`.

### Ergebnis

- `pruefprotokoll-preise-JJJJ-MM-TT.md` enthält:
  - Format und Intervall von smartENERGY und die Slots je Tag,
  - das Raster (Stundenwerte im 15-min-Raster oder echte 15-min-Werte),
  - die Befunde der Strukturprüfung,
  - je Referenz die Verfügbarkeit und das Ergebnis der Brutto/Netto-Erkennung.
- `preise-<quelle>-JJJJ-MM-TT.json` sind die Rohantworten.
- „Ergebnis: bestanden“ heißt: smartENERGY ohne Befund, mindestens eine Referenz verfügbar und
  Brutto/Netto erkannt. Der Exitcode ist dann 0, sonst 1.

Das Protokoll übernimmst du mit Datum nach `docs/verification/`.

## Teil 2 – Watchdog-Probe (17.2-5)

Teil 2 (Spec 17.2) schreibt auf Geräte und läuft gemeinsam mit dem Betreiber. Plan 2a liefert dafür
die Watchdog-Probe; die übrigen Punkte folgen mit Plan 2b. Das Werkzeug
`bindaems.tools.watchdog_probe` spricht MQTT mit dem Cerbo. Es schreibt dort nur den Heartbeat des
Watchdogs (`W/<portal>/bindaems/0/Heartbeat`) und sendet den Keepalive.

**Die Probe löst den Watchdog aus:** Er setzt die EVCS auf den sicheren Strom und schreibt Min-SOC
und Entladegrenze, wenn sie abweichen. Darum fragt das Werkzeug vorher nach; `--yes` überspringt die
Rückfrage.

| Schritt | Was geprüft wird |
|---|---|
| Watchdog erreichbar | Der Watchdog antwortet über MQTT mit Version und Zustand. |
| Watchdog-Version | Die Version ist die, die der core erwartet. |
| Scharf nach Heartbeat | Heartbeats machen ihn scharf. |
| Auslösung ohne Heartbeat | Bleiben sie aus, löst er nach dem Timeout aus. Die Probe wartet dann 45 s ohne Heartbeat, bis der Aktionslauf zu Ende ist, und nennt die Zahl der fehlgeschlagenen Aktionen. |
| Wieder scharf | Mit stabilem Heartbeat wird er nach der Auslösung wieder scharf. |

Prüfe nach jeder Probe auch das Log des Cerbo (Betriebshandbuch 14, „Probe“ und „Logs“): Die Zeile
„Aktionen abgeschlossen: …“ nennt das Ergebnis jeder Aktion, auch wenn der Bericht 0 fehlgeschlagene
Aktionen nennt.
| Freigabe ohne Auslösung | Der Heartbeat 0 bringt ihn auf „wartet“, ohne dass er auslöst. |

### Aufruf

Alle Hinweise (Rückfrage, Abbruch, Voraussetzungen) stehen im Betriebshandbuch, Abschnitt 14. Kurz:

```bash
cd /opt/bindaems
mkdir -p verification && sudo chown 10001 verification
docker compose run --rm -v "$PWD/verification:/out" --entrypoint python ems-core \
  -m bindaems.tools.watchdog_probe --config /config/config.yaml --out /out/<Datum>-watchdog-probe.md
```

Lokal aus dem Repository geht es mit
`uv run python -m bindaems.tools.watchdog_probe --config deploy/config.yaml`. Ohne `--out` liegt
der Bericht dann unter `docs/verification/<Datum>-watchdog-probe.md`.

### Ergebnis

- `JJJJ-MM-TT-watchdog-probe.md` enthält den Bericht mit der Tabelle
  `Punkt | Prüfung | Ergebnis | Details`. Der Punkt ist immer 17.2-5, die Ergebnisse lauten OK,
  WARNUNG oder FEHLER.
- Exitcode 0: Kein Schritt ist FEHLER. 1: Ein Schritt ist FEHLER, der Bericht ließ sich nicht
  schreiben oder die Rückfrage wurde verneint. 2: Konfiguration oder Secrets ungültig. 130: Strg+C;
  die Probe gibt den Watchdog vorher frei.
- Die Probe stellt vorher keine Werte um. Sie belegt die Kette vom EMS bis zu den Aktionen, die
  Auslösung, das erneute Scharfwerden und die Freigabe; verstellte Werte kommen mit Plan 2b.

Den Bericht übernimmst du mit Datum nach `docs/verification/`. Nach jedem Update von Venus OS oder
der EVCS-Firmware wiederholst du die Probe.

## Abnahme Phase 1

Die Checkliste für die Abnahme der Phase 1 (Spec 18) steht in
[`abnahme-phase-1.md`](abnahme-phase-1.md): 7 Tage lückenlose Aufzeichnung, Tages-Energiebilanz
innerhalb ±2 % von VRM, Preise und Prognose sichtbar und nachweislich keine Schreibpfade zu
Geräten. Für die Abnahme kopierst du sie mit Datum, füllst sie an der Anlage aus und checkst sie
hier ein.

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
  - So können beide Dateien in dieses Verzeichnis eingecheckt werden.
- Ist eine Quelle nicht erreichbar, steht im Bericht ein FEHLER mit der Meldung. Die übrigen
  Prüfungen laufen trotzdem.

### Danach

1. Abweichungen gegenüber `config.yaml` korrigieren, z. B. Instanzen unter
   `victron.instances` oder die erwarteten Werte unter `victron.expected`.
2. Warnungen bewerten. Zwei Beispiele:
   - Die Vorzeichenkonvention des Netzstroms lässt sich nur bei Einspeisung über 100 W
     bestimmen.
   - Ein aktiver Ladeplan im Fahrzeug muss aus.
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

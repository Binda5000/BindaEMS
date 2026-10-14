# Betrieb von BindaEMS

Stand: Phase 2c-1 (Laderegelung). Auf der VM laufen zwei Dienste. Der core **regelt** seit Plan
2c-1 die beiden Ladepunkte (EVCS und Tesla), **sendet aber nichts**, solange ihre Aktoren im Dry-Run
sind: Er protokolliert dann nur, was er täte, und evcc regelt weiter (Abschnitt „Laderegelung“ am
Ende). Die Befehle an Victron (Akku) folgen mit Plan 2c-2, die Bedienung in UI und HA mit Plan 2d.

- `ems-core` liest Cerbo GX, EVCS, Wall Connector, Tessie und Home Assistant und schreibt
  Telemetrie nach InfluxDB. Er kennt Betriebszustände, Freigaben und Sperren (Abschnitt 13), hat
  Aktoren für Victron, EVCS und Tesla und regelt die beiden Ladepunkte. Ohne Absicht der Bedienung
  bleibt er im Zustand **OBSERVE**: nur lesend. Auf ein Gerät schreibt er nur für einen live
  geschalteten Aktor: den Heartbeat an den eigenen Watchdog (Abschnitt 14), solange er regelt, die
  Sicherwerte im sicheren Zustand und bei der Übergabe am Ende des Live-Betriebs (Abschnitt 13) und
  die Befehle der Laderegelung an EVCS und Tesla. Einen live gewünschten Aktor gibt es erst nach
  Plan 2d. Das Entscheidungslog hält fest, was er an den Aktoren tut: gesendete Befehle, im Dry-Run
  die Urteile mit einem Wert und die Ablehnungen der Safety-Schicht (Abschnitt 13).
- `ems-app` meldet Benutzer an und liefert das Web-UI samt API aus (Abschnitt 12). Sie holt
  Preise und die PV-Prognose, rechnet jede Viertelstunde ab und meldet lesende Sensoren per
  MQTT Discovery an Home Assistant.

Dazu kommt der **Watchdog** auf dem Cerbo, den du einmal installierst (Abschnitt 14). Er ist der
Totmann-Schalter der Anlage und greift nur ein, wenn ein live geschalteter core ausfällt.

Am Ende des Handbuchs stehen die Abschnitte „Laderegelung“ (Lademodi, Absicht, Dry-Run-Vergleich mit
evcc, Umstieg, Hinweise für die Anlage) und „Simulator“ (die Szenarien der CI von Hand laufen
lassen).

## 1. Voraussetzungen der VM

- Debian (aktuelle stabile Version) mit Docker Engine und Compose-Plugin.
- Richtwert: 2 vCPU, 4 GB RAM, 20 GB Platte.
- **NTP muss aktiv sein**, denn die Viertelstunden-Slots hängen an der Uhrzeit.
  Prüfen mit `timedatectl` (Zeile `System clock synchronized: yes`).
- Netzzugang:
  - im LAN: Cerbo GX (MQTT 8883), EVCS (Modbus TCP 502), Wall Connector (HTTP 80),
    InfluxDB (8086), Home Assistant (8123)
  - Die Adresse der VM muss auf der IP-Whitelist der EVCS stehen. Sonst nimmt die EVCS die
    Verbindung zwar an, liefert aber nur Nullen.
  - im LAN außerdem: Mosquitto von Home Assistant (1883 bzw. 8883) für die Sensoren der app
  - ins Internet: Tessie (`api.tessie.com`), GHCR (`ghcr.io`) für Images sowie für die app
    `apis.smartenergy.at`, `api.energy-charts.info`, `api.awattar.at` und `api.open-meteo.com`
    (nur lesende `GET`-Abfragen)

### Docker unter Debian installieren

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/debian/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
https://download.docker.com/linux/debian $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
  | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

## 2. Verzeichnis `/opt/bindaems`

```bash
sudo mkdir -p /opt/bindaems && cd /opt/bindaems
# aus dem Repository (Ordner deploy/) übernehmen:
sudo cp <repo>/deploy/docker-compose.yml .
sudo cp <repo>/deploy/config.example.yaml config.yaml   # anschließend anpassen
sudo cp <repo>/deploy/.env.example .env
sudo chmod 600 .env                                     # Secrets nur für root lesbar
```

- `config.yaml` enthält keine Geheimnisse. Sie wird nur lesend in den Container eingebunden.
  Fehler meldet der core beim Start mit „Konfiguration ungültig: …“ und beendet sich mit Code 2.
- In `.env` stehen die Secrets (`BINDAEMS_*`):
  - `BINDAEMS_INTERNAL_TOKEN` ist Pflicht und hat mindestens 32 Zeichen
    (z. B. `openssl rand -hex 32`).
  - MQTT-Passwort, Tessie-Token, HA-Token, InfluxDB-Passwort und das Passwort für den
    Mosquitto von HA (`BINDAEMS_HA_MQTT_PASSWORD`) trägst du nur ein, wenn sie genutzt werden.
    Leere Einträge gelten als nicht gesetzt.
  - Ohne Tessie-Token wird der Tesla weder gelesen noch gesteuert, und `verify_part2 --punkt tesla`
    endet mit dem Exitcode 2. Das Log des core meldet das.
- Zugriff auf die Images in GHCR mit einem Lese-Token (`read:packages`):
  `echo <token> | docker login ghcr.io -u <github-benutzer> --password-stdin`

## 3. InfluxDB einrichten

Das Skript `deploy/influxdb-setup.sh` richtet Folgendes ein und darf beliebig oft laufen:

- Datenbank `bindaems`
- Retention Policies `raw` (90 Tage, Standard) und `long` (unbegrenzt)
- Continuous Queries:
  - 1-Minuten-Mittel für `power` und `soc`
  - letzter Zählerstand je Minute für `energy`
  - Viertelstunden-Summen für `flows`

```bash
INFLUX_URL=http://influx.lan:8086 INFLUX_ADMIN_USER=admin INFLUX_ADMIN_PASSWORD='…' \
  ./influxdb-setup.sh
```

Den Benutzer für BindaEMS legst du einmalig an. Das Passwort ist `BINDAEMS_INFLUX_PASSWORD`
aus `.env`. Den Lesezugriff auf die HA-Datenbank braucht die app für Verbraucher aus
HA-Entitäten (`influxdb.ha_database`), später auch für die Lernmodelle. Die HA-Datenbank heißt
so, wie es in der InfluxDB-Konfiguration von HA unter `database:` steht; ohne Angabe
`home_assistant`. Die app schreibt Preise, PV-Prognose und Abrechnung (`price`, `forecast`,
`ledger`) direkt in `long`.

```sql
CREATE USER "bindaems" WITH PASSWORD '…'
GRANT ALL ON "bindaems" TO "bindaems"
GRANT READ ON "homeassistant" TO "bindaems"
```

**Ausfall von InfluxDB:** Der core puffert die Telemetrie auf Platte, im Volume `core-data`
unter `/data/spool`. Der Puffer fasst höchstens 500 MB bzw. 7 Tage. Ist InfluxDB wieder
erreichbar, sendet der core den Puffer nach. Die Continuous Queries erfassen nachgesendete
Daten nicht mehr. Bei Bedarf füllst du `long` für den Ausfallzeitraum von Hand nach, z. B.:

```sql
SELECT mean(p_w) AS p_w, mean(i_a) AS i_a, mean(u_v) AS u_v
INTO "bindaems"."long"."power" FROM "bindaems"."raw"."power"
WHERE time >= '2026-10-08T08:00:00Z' AND time < '2026-10-08T12:00:00Z'
GROUP BY time(1m), *
```

## 4. Bedienung

| Aufgabe | Befehl (in `/opt/bindaems`) |
|---|---|
| Starten | `docker compose up -d` |
| Zustand und Healthcheck | `docker compose ps` (Spalte STATUS: `healthy`) |
| Logs (JSON) | `docker compose logs -f ems-core` bzw. `… ems-app` |
| Update | `docker compose pull && docker compose up -d` |
| Rollback | in `.env` `BINDAEMS_VERSION=<tag>` setzen, dann `docker compose up -d` |
| Stoppen | `docker compose down` (die Volumes `core-data`, `app-data` und `backup` bleiben) |

- **Tags:**
  - `latest` ist der Stand von `main`.
  - Zusätzlich gibt es je Commit `sha-<kurz>` und je Release `vX.Y.Z`.
- **Geordnetes Herunterfahren:**
  - `docker compose stop` sendet SIGTERM. Der core schreibt dann die laufende Viertelstunde
    und die Warteschlange und beendet sich mit Code 0. Die app schreibt ihre Warteschlange für
    InfluxDB, schließt alle Verbindungen und beendet sich ebenfalls mit Code 0.
  - Der core gibt den Watchdog dabei **nicht** frei. Ist ein Aktor live und dauert ein Neustart
    samt 30 s frischer Messwerte länger als 90 s, löst der Watchdog aus (Abschnitt 14).
- **Logs:**
  - JSON auf stdout. Docker rotiert sie (5 × 10 MB).
- **Healthcheck:**
  - Der core fragt alle 30 s `/v1/health` der internen API ab, die app `/health`.
  - Wurde `core_api.port` bzw. `app.port` in `config.yaml` geändert, muss
    `BINDAEMS_CORE_PORT` bzw. `BINDAEMS_APP_PORT` in `.env` denselben Wert haben.
- **Diagnose:**
  - Die interne API ist absichtlich nicht nach außen veröffentlicht.
  - Zur Fehlersuche kannst du in `docker-compose.yml` die Zeile `127.0.0.1:8081:8081`
    kurzzeitig einkommentieren. Danach fragst du so ab:
    `curl -H "Authorization: Bearer $BINDAEMS_INTERNAL_TOKEN" http://127.0.0.1:8081/v1/health`.
  - `/v1/health` nennt auch Betriebszustand mit Gründen, Aktoren (steuerbar, offene
    Schreibvorgänge, ob sie Werte des EMS halten), Watchdog und je Fahrzeug den Stand der
    Laderegelung (`vehicles`, Abschnitt „Laderegelung“), `/v1/intent` die gültige Absicht und
    `/v1/decisions` das Entscheidungslog (Abschnitt 13).
- **Lokaler Build:**
  - Statt GHCR geht das auch mit einem Checkout des Repositorys.
  - Im Ordner `deploy/` mit `docker compose build` bauen, dann `docker compose up -d`.

## 5. ems-app: erster Start und erster Admin

`docker compose up -d` startet beide Dienste. Beim ersten Start legt die app ihre Datenbank
`/data/bindaems.sqlite3` im Volume `app-data` an. Danach legst du den ersten Admin an; das
Passwort wird zweimal abgefragt (mindestens 10 Zeichen):

```bash
docker compose run --rm ems-app create-admin --username <name>
```

Ein vergessenes Passwort setzt `docker compose run --rm ems-app set-password --username <name>`
neu. Weitere Benutzer legt ein Admin über die API bzw. später im UI an. Die Rollen sind
`admin` (alles), `operator` (Bedienen; in Phase 1b wie Lesen) und `viewer` (Lesen).
Zwei-Faktor-Anmeldung (TOTP) richtet jeder Benutzer selbst ein, bestätigt mit seinem Passwort;
solange ein Admin sie nicht hat, zeigt `/api/system` eine Warnung.

Ist das TOTP-Gerät verloren, schaltet dieser Befehl die Zwei-Faktor-Anmeldung für das Konto ab:

```bash
docker compose run --rm ems-app reset-totp --username <name>
```

Danach meldet sich die Person nur mit dem Passwort an und richtet TOTP neu ein. Beide Befehle
stehen im Änderungsprotokoll (`/api/audit`, Quelle `cli`).

Falsche Passwörter zählen überall gleich: bei der Anmeldung, beim Passwortwechsel und beim
Einrichten oder Abschalten von TOTP. Nach 5 Fehlversuchen innerhalb von 15 min ist das Konto
15 min gesperrt.

## 6. Reverse Proxy

Die app ist nur über den Reverse Proxy (HTTPS) erreichbar. Sie liefert unter `/` auch die
Web-UI aus; die kommt fertig gebaut mit dem Image (`/app/ui`, Einstellung `app.ui_dir`). Der
Proxy läuft außerhalb dieser VM und leitet auf `<vm>:8080` weiter. Er muss:

- den ursprünglichen `Host`-Header samt Port weitergeben (der Live-WebSocket prüft die Herkunft
  dagegen; nginx: `$http_host`, nicht `$host`),
- `X-Forwarded-For` und `X-Forwarded-Proto` setzen,
- für `/api/live` das WebSocket-Upgrade durchreichen (lange Lesezeit, z. B. 1 h).

Beispiel für nginx:

```nginx
location / {
    proxy_pass http://<vm>:8080;
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    gzip on;
    gzip_types text/css application/javascript image/svg+xml;
}
location /api/live {
    proxy_pass http://<vm>:8080;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $http_host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 1h;
}
```

`gzip` verkleinert die Dateien der Web-UI (Skripte, Stile); die API antwortet ohnehin klein.

In `config.yaml`:

- `app.trusted_proxies` nennt die Adresse des Proxys. Nur von dort übernimmt die app
  `X-Forwarded-*`; das Log der Anmeldungen zeigt so die echte Client-Adresse.
- `app.cookie_secure: true` (Standard) schickt die Sitzungs-Cookies nur über HTTPS. `false`
  ist nur für einen Test im LAN ohne Proxy gedacht.

Port 8080 soll nur der Proxy erreichen. Docker umgeht `ufw`; filtere daher in der
Proxmox-Firewall der VM oder in der iptables-Kette `DOCKER-USER`. Läuft der Proxy doch als
Container auf dieser VM, entfernst du `ports` beim Dienst `ems-app` und hängst den Proxy an
das Netz `internal`; er erreicht die app dann unter `http://ems-app:8080`.

## 7. Home Assistant über MQTT

Die app meldet ihre Sensoren per MQTT Discovery am Mosquitto von Home Assistant an
(`homeassistant.mqtt` in `config.yaml`). Sie liest nur `homeassistant/status` und sendet keine
Befehle. Lege dafür einen eigenen Benutzer `bindaems` an; sein Passwort steht in
`BINDAEMS_HA_MQTT_PASSWORD`. Die ACL beschränkt ihn auf das Nötige (beim Mosquitto-Add-on über
`customize` und eine ACL-Datei unter `/share/mosquitto`):

```text
user bindaems
topic write homeassistant/sensor/bindaems/#
topic write homeassistant/binary_sensor/bindaems/#
topic write bindaems/#
topic read homeassistant/status
```

Entitäten (Gerät „BindaEMS“, sofern nicht anders angegeben):

| Entität | Einheit | Bedeutung |
|---|---|---|
| Bezugspreis | ct/kWh | Preis der laufenden Viertelstunde, brutto; Attribut `prices` mit den nächsten 36 h |
| Einspeisepreis | ct/kWh | OeMAG-Wert des Monats |
| PV-Prognose heute / morgen | kWh | P50 aus Open-Meteo und PV-Modell |
| Netzleistung, PV-Leistung, Akkuleistung, Hausverbrauch | W | Werte des core |
| Akku-SOC | % | |
| Betriebsart | – | Betriebszustand des core: `START`, `OBSERVE`, `CONTROL`, `DEGRADED` oder `SAFE` (Abschnitt 13), Diagnose |
| core verbunden | an/aus | Diagnose |
| Mitregelndes System | Problem | ein anderes System regelt mit (z. B. Dynamic ESS) |
| Watchdog ausgelöst | Problem | der Watchdog am Cerbo hat ausgelöst (Abschnitt 14); unbekannt, solange er nicht antwortet |
| Preise fehlen | Problem | kein Preis für jetzt oder nach 16 Uhr keiner für morgen |
| Gerät offline | Problem | ein Adapter des core ist getrennt; Attribut `adapters` |
| SOC (Gerät „BindaEMS <Fahrzeug>“) | % | je Fahrzeug |
| Ladeleistung (Gerät „BindaEMS EVCS“ / „BindaEMS Wall Connector“) | W | je Wallbox |

Fährt die app geordnet herunter, meldet sie sich selbst ab; bei einem Absturz oder
Verbindungsabbruch setzt der Broker alle Entitäten über den Last Will auf „nicht verfügbar“.

## 8. Tarif und Abrechnung

Nach der Inbetriebnahme trägst du in den Laufzeit-Einstellungen nach, was die Spec bewusst
offen lässt: Netznutzungsentgelt (Arbeitspreis), Netzverlustentgelt, Elektrizitätsabgabe,
Erneuerbaren-Förderbeitrag (alle netto in ct/kWh) und jeden Monat den OeMAG-Marktpreis. Bis
dahin zählen fehlende Werte als 0, und die Übersicht und die Seite „System“ zeigen Warnungen.
Jede Änderung ist eine neue Version im Änderungsprotokoll.

Im UI (Admin) unter **Einstellungen**:

- **Bearbeiten**: je fester Tarifbestandteil ein Feld „… (ct/kWh netto)“ (Komma oder Punkt,
  leer = noch kein Wert), die OeMAG-Monatswerte (Monat und ct/kWh, Zeilen hinzufügen oder
  entfernen), Brutto/Netto der Börsenpreise und die Referenzquelle, dazu ein Kommentar.
  **Speichern** legt eine neue Version an. Hat inzwischen jemand anderes gespeichert, bleiben
  deine Eingaben stehen; „Neu laden (Eingaben verwerfen)“ holt den neuen Stand.
- **Abrechnung neu bewerten**: Die Abrechnung speichert je Viertelstunde den Bezugspreis zum
  Slotzeitpunkt. Nach einer Änderung der Tarifwerte erscheint das Formular mit dem Zeitraum
  vom Monatsbeginn bis heute; **Neu bewerten** rechnet die gewählten Tage mit dem aktuellen
  Tarif neu.
- **Export und Import**: „Exportieren (YAML)“ lädt die gültigen Einstellungen herunter;
  „YAML-Datei importieren“ übernimmt eine geprüfte Datei als neue Version. Fehler nennen die
  betroffenen Felder. Zeitfenster der Tarifbestandteile (z. B. Sommer-Faktor) änderst du so.
- **Versionen** listet alle Stände mit Zeit, Person, Quelle und Kommentar.

Der Einspeiseerlös wird immer erst beim Lesen mit dem eingetragenen OeMAG-Wert berechnet; dafür
ist keine Neubewertung nötig.

### Alternative für Skripte (curl)

Dieselben Schritte über die API, z. B. für eine Automatisierung:

```bash
APP=https://ems.example.lan
curl -c jar -H 'Content-Type: application/json' \
  -d '{"username": "<name>", "password": "<passwort>"}' "$APP/api/auth/login"
CSRF=$(awk '$6 == "bindaems_csrf" {print $7}' jar)
# Abrechnung neu bewerten
curl -b jar -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -d '{"from": "2026-10-01", "to": "2026-10-31"}' "$APP/api/ledger/reprice"
# Einstellungen exportieren und importieren
curl -b jar "$APP/api/settings/export" -o einstellungen.yaml
python3 -c 'import json, sys; print(json.dumps({"yaml": open(sys.argv[1]).read()}))' \
  einstellungen.yaml > import.json
curl -b jar -H "X-CSRF-Token: $CSRF" -H 'Content-Type: application/json' \
  -d @import.json "$APP/api/settings/import"
```

## 9. Sicherung und Wiederherstellung

Die app sichert ihre Datenbank jede Nacht um 03:15 Uhr im laufenden Betrieb in das Volume
`backup` (`/backup/bindaems-JJJJMMTT-HHMMSS.sqlite3`) und behält 14 Sicherungen. Gegen den
Verlust der VM helfen sie nur zusammen mit der Proxmox-Replikation oder einer Kopie außerhalb
der VM.

Wiederherstellen:

```bash
docker compose stop ems-app
docker compose run --rm --entrypoint sh ems-app -c \
  'cp /backup/bindaems-20261009-031500.sqlite3 /data/bindaems.sqlite3 \
   && rm -f /data/bindaems.sqlite3-wal /data/bindaems.sqlite3-shm'
docker compose start ems-app
```

Die WAL-Dateien müssen weg, sonst spielt SQLite alte Änderungen in die zurückgeholte Datei.
Daten seit der Sicherung (z. B. Preise, Abrechnung) holt die app beim Start zum Teil wieder:
Preise neu von den Quellen, Viertelstunden der letzten 7 Tage aus InfluxDB.

## 10. Proxmox-HA

- Die VM ist eine HA-Ressource mit ZFS-Replikation alle 1–5 Minuten.
- **Bei einem Failover** startet die VM auf dem anderen Knoten aus der letzten Replikation.
  Telemetrie seit dieser Replikation, also Spool und laufende Viertelstunde, kann fehlen.
  Sie steht dann in InfluxDB, sofern InfluxDB erreichbar war.
- Der core startet immer in START und durchläuft OBSERVE, auch mit gespeicherter Absicht
  (Abschnitt 13).
- Ist ein Victron- oder EVCS-Aktor live, schützt der Watchdog auf dem Cerbo die Anlage während
  des Failovers (Abschnitt 14):
  - Ist der core samt 30 s frischer Messwerte (und bestätigter Phasen) binnen 90 s zurück, löst der
    Watchdog nicht aus: Der core sendet den ersten Heartbeat erst, wenn das Gate bis auf den
    Watchdog offen ist (Abschnitt 13).
  - Sonst löst er genau einmal aus und wird nach 30 s stabilem Heartbeat wieder scharf.
- Nach einem Failover prüfst du die Uhrzeit (`timedatectl`) und `docker compose ps`.

## 11. Prüfprotokolle

### Teil 1

Das Werkzeug liest alle relevanten Werte vom Cerbo und von den Geräten und gleicht sie mit
`config.yaml` ab. Das umfasst Instanzen, ESS-Einstellungen, Ladefenster und den Registerdump
der EVCS. Es schreibt das Ergebnis als Prüfprotokoll. Wiederhole es nach jedem
Firmware-Update von Cerbo, EVCS oder Wall Connector.

```bash
cd /opt/bindaems
mkdir -p verification && sudo chown 10001 verification   # der Container läuft als UID 10001
docker compose run --rm -v "$PWD/verification:/out" --entrypoint python ems-core \
  -m bindaems.tools.verify_part1 --config /config/config.yaml --duration 120 --out /out
```

Das Protokoll liegt danach in `/opt/bindaems/verification/`. Abweichungen gegenüber
`config.yaml` (Instanzen, `expected`-Werte) korrigierst du anschließend. Details stehen in
`docs/verification/README.md`.

### Prüfprotokoll Teil 2

Teil 2 (Spec 17.2) **schreibt auf die Geräte** und läuft gemeinsam mit dem Betreiber. Das Werkzeug
`verify_part2` führt die Punkte aus. Es nutzt dieselben Adapter und Aktoren wie der core, prüft
jeden Wert mit den Regeln 1–3 der Safety-Schicht und fragt vor jedem schreibenden Schritt nach.
Der core läuft währenddessen im Dry-Run weiter und stört nicht, solange er in diesem Lauf nie für
den Watchdog zuständig war (Abschnitt 14); sonst starte ihn vorher neu. Die **Watchdog-Probe**
(17.2-5, Abschnitt 14) gibt es auch einzeln; der Punkt `evcs` führt sie mit aus.

| `--punkt` | Spec | Was das Werkzeug schreibt |
|---|---|---|
| `evcs` | 17.2-6 und 17.2-5 | Ladestrom der EVCS (Mindeststrom, 10 A, Höchststrom), Laden aus und an; dann die Watchdog-Probe mit verstelltem Strom |
| `tesla` | 17.2-7 | über Tessie Ladestrom und Laden aus und an; `--schlafend` sendet nichts und prüft nur, dass ein schlafendes Auto nicht geweckt wird |
| `victron` | 17.2-1 bis 17.2-4 und 17.2-8 | Netz-Sollwert-Override, Entladegrenze 0 W, Netzladen und „Kein Akku ins Auto“; das Importlimit wird nur beobachtet |

Die Schritte je Punkt und das Format des Berichts stehen in `docs/verification/README.md`.
Die Reihenfolge: **EVCS und Tesla jetzt**, gemeinsam mit dem Betreiber, **Victron nur mit
abgeschaltetem Dynamic ESS** (für den Test genügt es, DESS vorübergehend abzuschalten).

Der Aufruf gilt je Punkt und folgt dem der Probe:

```bash
cd /opt/bindaems
mkdir -p verification && sudo chown 10001 verification   # der Container läuft als UID 10001
docker compose run --rm -v "$PWD/verification:/out" --entrypoint python ems-core \
  -m bindaems.tools.verify_part2 --config /config/config.yaml --punkt evcs \
  --out /out/<Datum>-pruefprotokoll-teil2-evcs.md
```

- Für die anderen Punkte setzt du `--punkt tesla` bzw. `--punkt victron` und den Namen im
  Dateinamen ein. `--wallbox <Name>` wählt bei `evcs` die EVCS und `--fahrzeug <Name>` bei `tesla`
  das Fahrzeug, wenn es mehrere gibt (Standard: das erste).
- Den **Schlaf-Test des Tesla** rufst du mit `--punkt tesla --schlafend` auf und gibst ihm mit
  `--out` einen eigenen Namen. Heißt die Datei wie der Bericht des Tesla-Punkts, überschreibt er
  ihn:

  ```bash
  docker compose run --rm -v "$PWD/verification:/out" --entrypoint python ems-core \
    -m bindaems.tools.verify_part2 --config /config/config.yaml --punkt tesla --schlafend \
    --out /out/<Datum>-pruefprotokoll-teil2-tesla-schlafend.md
  ```
- Wie bei der Probe braucht `docker compose run` ein Terminal, deshalb steht hier kein `-T`. Setze
  für `<Datum>` das Datum (JJJJ-MM-TT) ein. Ohne `--out` ginge der Bericht mit `--rm` verloren.
- Starte das Werkzeug in `tmux` oder `screen` (siehe „Abbruch mit Strg+C“).

**Voraussetzungen**

- core und app laufen mit den Images von Plan 2b oder neuer (Abschnitt 4). Der Watchdog am Cerbo
  ist auf 1.1.0 (Abschnitt 14), denn der Punkt `evcs` führt die Watchdog-Probe aus.
- `live_allowed` brauchst du nicht: Das Werkzeug schreibt unabhängig davon und fragt dich bei jedem
  Schritt. `live_allowed: true` setzt du erst für Aktoren, die bestanden haben.
- `evcs`: Die EVCS steht im manuellen Modus und hat ihre Instanz unter
  `victron.instances.evcharger`. Ein angestecktes Fahrzeug lädt während des Tests mit
  wechselndem Strom.
- `tesla`: `BINDAEMS_TESSIE_TOKEN` steht in der `.env`; ohne endet das Werkzeug mit dem
  Exitcode 2. Das Auto ist wach, zu Hause und angesteckt; wecke es vorher, etwa indem du in der
  Tesla-App das Laden startest. Das Werkzeug weckt es nie und sendet sonst nichts. Für
  `--schlafend` muss das Auto dagegen schon schlafen, und Tessie muss sich zu Beginn und am Ende
  des Tests frisch melden. Ohne frische Meldung von Tessie ist der Schlaf-Test FEHLER „nicht
  beobachtbar: …“ mit dem Grund (Tessie nicht erreichbar, Wert veraltet).
- `victron`: Dynamic ESS ist aus (siehe „Victron und Dynamic ESS“), und die Entladegrenze steht
  nicht auf 0 W. Eine Entladegrenze von 0 W (Entladen gesperrt) hinterlässt etwa ein abgebrochener
  Lauf; der Teil verweigert dann ohne zu schreiben, denn er stellte sie am Ende als Ausgangswert
  wieder her. Setze sie vorher zurück. Geht Dynamic ESS mitten im Lauf an, schreibt der Teil nichts
  mehr und stellt die vorgefundenen Werte wieder her.

**Rückfragen**

- Vor dem Start nennt das Werkzeug, was es schreibt, und fragt `Fortfahren? [j/N]`. Vor jedem
  weiteren schreibenden Schritt fragt es erneut. Nur `j` gilt als Ja.
- **Führe es ohne `--yes` aus.** Mit `--yes` beantwortet es alle Rückfragen mit Ja und überspringt
  die Schritte, die einen Handgriff von dir brauchen (EVCS: fremde Änderung, Netzwerkkabel ziehen;
  Victron: Neustart des Cerbo, Last für das Importlimit). Der Bericht vermerkt das unter
  „Rückfragen“.
- Beantworte Rückfragen zu schreibenden Schritten zügig. Solange eine Rückfrage offen ist, stehen
  die Verbindungen still; nach einer langen Pause verbinden sie sich einmal neu. Ein Schritt kann
  dann „nicht bestätigt“ melden. Das Werkzeug stellt trotzdem wieder her; wiederhole danach den
  Lauf.
- Victron, Schritt „Override nach Neustart des Cerbo“: Beantworte erst die Rückfrage und starte
  dann den Cerbo neu. Eine lange offene Rückfrage kann einen Verbindungsaufbau auslösen, der wie
  ein Neustart aussieht.
- Tesla: Nach einer Rückfrage wartet das Werkzeug auf eine neue Meldung von Tessie, bevor es
  sendet. Kommt keine, endet der Schritt mit FEHLER, und es geht nichts hinaus.
- Tesla: Das Werkzeug wendet die Rate-Limits der Safety-Schicht nicht an, denn du bestätigst jeden
  Schritt. Zwei vollständige Läufe binnen einer Stunde können deshalb mehr als 6 Start/Stopp-Befehle
  ergeben (die Grenze der Regelung, Spec 6.5).
- Tesla: Bestätigt ist ein Strom erst, wenn auch der Tessie-Cache ihn zeigt. Der Aktor wertet die
  erste Meldung ab 10 s nach der Bestätigung durch Tessie, höchstens bis 75 s danach (die Frist je
  Befehl beträgt 85 s ab dem Senden; das Werkzeug wartet so lange); kommt keine, gilt die
  Bestätigung von Tessie. Das Fenster ist so lang, weil Tessie ein angestecktes, noch nicht ladendes
  Auto nur alle 60 s abfragt. Zeigt die Meldung noch den alten Wert, sendet das Werkzeug den
  Befehl einmal erneut; an der Anlage wirkt ein Befehl auf oder von 5 A erst beim zweiten Mal. Der
  Befund nennt das als „Erster Befehl: Fahrzeug übernahm … A nicht (Tessie meldet … A);
  wiederholt nach … s.“ „bestätigt nach … s“ schließt die Prüfung ein, die Zeit am Wall Connector
  zählt ab dem ersten Senden. Ohne Bestätigung nennt der Befund, ob der Wall Connector den
  Sollstrom erreichte: Dann hinkt nur der Cache, sonst hat das Auto den Strom nicht übernommen.

**Wiederherstellen**

- Vor dem ersten Schreiben merkt sich das Werkzeug jeden vorgefundenen Wert. Nach jedem Schritt
  (die Ströme gelten als einer), bei Abbruch und am Ende stellt es diese Werte wieder her. Die
  Zeilen „Wiederherstellen: …“ stehen im Bericht hinter ihrem Schritt. Gelang es nicht, ist die
  Zeile ein FEHLER; dann prüfst du den Wert von Hand.
- Ein vorgefundener Wert außerhalb der Grenzen (Regeln 1–3, etwa ein EVCS-Strom über `max_a`)
  wird nicht überschrieben, denn er ließe sich nicht wiederherstellen: Der erste Schreibversuch für
  diesen Wert endet mit „nicht gesendet: Ausgangswert … außerhalb der Grenzen …“, auch wenn der
  Wert erst spät gemeldet wird. Stelle ihn vorher richtig.
- Entladegrenze und Min-SOC sind gespeicherte Einstellungen am Cerbo: Das Werkzeug schreibt
  höchstens vier persistente Werte je Lauf, das Wiederherstellen eingeschlossen. Steht danach noch
  ein Wert falsch, meldet es das; stelle ihn von Hand zurück.
- EVCS, Schritt „Sicherer Strom über den Watchdog“ (17.2-5): Die Auslösung setzt laut `config.ini`
  auch Min-SOC und Entladegrenze auf ihre Sicherwerte, und mit `victron.live_allowed` hebt sie den
  Netz-Sollwert-Override auf. Das Werkzeug stellt diese Werte nicht wieder her; es sind ohnehin die
  Sicherwerte. Prüfe sie danach wie nach der Probe (Abschnitt 14).

**Abbruch mit Strg+C**

- Strg+C bricht den Lauf ab. Nach dem Start stellt das Werkzeug die vorgefundenen Werte wieder her,
  schreibt den Bericht und endet mit dem Exitcode 130. Strg+C bei der Startfrage endet ebenfalls mit
  130, aber ohne Bericht; das Werkzeug hat dann noch nichts geschrieben.
- Nur Strg+C führt zum Wiederherstellen. Bricht die SSH-Verbindung ab (SIGHUP) oder endet das
  Werkzeug durch `docker stop` (SIGTERM), beendet es sich ohne Wiederherstellen; darum startest du
  es in `tmux` oder `screen`. Prüfe nach einem solchen Ende alle Werte von Hand.
- Endet das Werkzeug so im Schritt „Sicherer Strom über den Watchdog“ (Punkt `evcs`), kann der
  Watchdog scharf bleiben und nach 90 s auslösen oder ausgelöst bleiben, denn ein core, der in
  diesem Lauf nie für den Watchdog zuständig war, gibt ihn nicht frei. Gib ihn dann am Cerbo frei
  und prüfe `/State`, wie bei einer abgebrochenen Probe (Abschnitt 14, „Probe“).
- Strg+C während einer Rückfrage wirkt erst nach Enter oder mit einem zweiten Strg+C; danach
  stellt das Werkzeug wieder her. Ein weiteres Strg+C bricht auch das Wiederherstellen ab. Der
  Bericht nennt es („mit Strg+C abgebrochen; die Werte bitte von Hand prüfen“); dann prüfst du die
  Werte von Hand.

**Victron und Dynamic ESS**

- Dynamic ESS nullt fremde Overrides (Spec 3.2) und bleibt im Betrieb an, bis Victron live geht
  (Entscheidung vom 10.10.2026). Für den Punkt `victron` schaltest du es vorübergehend ab
  (Entscheidung vom 12.10.2026) und danach wieder ein. Den Modus liest und setzt du am Cerbo:

  ```bash
  ssh root@<cerbo> dbus -y com.victronenergy.settings /Settings/DynamicEss/Mode GetValue   # notieren
  ssh root@<cerbo> dbus -y com.victronenergy.settings /Settings/DynamicEss/Mode SetValue 0
  # … Test …
  ssh root@<cerbo> dbus -y com.victronenergy.settings /Settings/DynamicEss/Mode SetValue <notierter Wert>
  ```

  Ist DESS an, endet der Punkt mit FEHLER „Dynamic ESS ist aktiv (Modus …); die Victron-Punkte
  erst nach dem Abschalten von DESS.“, ohne zu schreiben.
- Er verweigert ebenso, wenn beim Start ein Netz-Sollwert-Override steht, auch 0 W:
  „Netz-Sollwert-Override aktiv (… W); ein anderes System regelt – vor dem Test aufheben.“ Ohne
  vorgefundenen Override ist er am Ende wieder aufgehoben.
- „Victron bereit“ wartet bis zur Bestätigungsfrist auf die Werte von Override und Entladegrenze.
  Meldet der Cerbo `ess.override.setpoint` im Ruhezustand gar nicht, enden die Override-Schritte mit
  „Ausgangswert unbekannt“, ohne zu schreiben. Im Lauf vom 12.10.2026 meldete der Cerbo im
  Ruhezustand `null`, und `null` hob einen Override auf. Meldet er im Ruhezustand einen
  Zahlenwert, verweigert das Werkzeug wie oben; dann muss der Ruhezustand neu bewertet werden.
- Nennt „Victron bereit“ die Entladegrenze als „unbekannt“, brich mit Strg+C ab und starte den
  Punkt neu: Meldet der Cerbo sie erst später, gälte sonst eine von einem abgebrochenen Lauf
  hinterlassene Entladegrenze von 0 W als Ausgangswert und bliebe am Ende stehen.

**Bericht**

- Ohne `--out` heißt der Bericht `<Datum>-pruefprotokoll-teil2-<punkt>.md` mit `evcs`, `tesla` oder
  `victron`, beim Schlaf-Test des Tesla (`--schlafend`)
  `<Datum>-pruefprotokoll-teil2-tesla-schlafend.md`. Im Container gibst du den Namen mit `--out`
  vor (siehe Aufruf) und wählst für den Schlaf-Test einen anderen als für den Tesla-Punkt. Der
  Bericht liegt in `/opt/bindaems/verification/`; übernimm ihn mit Datum nach `docs/verification/`.
- Der Bericht hat je Schritt eine Zeile unter dem Punkt 17.2-x mit OK, WARNUNG, FEHLER oder INFO.
- Exitcode: 0, wenn kein Schritt FEHLER ist. 1 bei einem FEHLER, einer verneinten Rückfrage vor
  dem Start oder wenn sich der Bericht nicht schreiben ließ. 2 bei ungültiger Konfiguration,
  ungültigen Secrets oder Aufruf, auch bei `--punkt tesla` ohne Tessie-Token. 130 nach Strg+C, bei
  der Startfrage ohne Bericht.
- Wiederhole die betroffenen Punkte nach jedem Update von Venus OS, der EVCS-Firmware oder der
  Wall-Connector-Firmware (Spec 17).

### Preise

Das Prüfprotokoll „Preise“ (Spec 17.1-13) ruft smartENERGY sowie Energy-Charts und aWATTar ab
und hält Format, Intervall, Verfügbarkeit der Referenzquellen und das Ergebnis der
Brutto/Netto-Erkennung fest. Es braucht keine Konfiguration:

```bash
cd /opt/bindaems
mkdir -p verification && sudo chown 10001 verification
docker compose run --rm -v "$PWD/verification:/out" ems-app verify-prices --out /out
```

Exitcode 0 heißt bestanden. Das Protokoll und die Rohantworten übernimmst du nach
`docs/verification/` (Details dort im Abschnitt „Preise“).

## 12. Web-UI

Die app liefert die Bedienoberfläche unter derselben Adresse aus wie ihre API, also über den
Reverse Proxy aus Abschnitt 6, z. B. `https://ems.example.lan`. Das UI braucht kein Internet;
alle Daten kommen von der app.

### Anmeldung und Rollen

- Den ersten Admin legst du über die Befehlszeile an (Abschnitt 5), alle weiteren Benutzer ein
  Admin unter **Benutzer**.
- **Lesen** sieht alles, ändert nichts. **Bedienen** entspricht in Phase 1 dem Lesen; ab Phase 2
  steuert diese Rolle das Laden. **Admin** ändert zusätzlich Einstellungen, Verbraucher und
  Benutzer und sieht das Änderungsprotokoll.
- „Angemeldet bleiben“ hält die Sitzung 30 Tage, aber nur für Bedienen und Lesen.
- Nach 5 Fehlversuchen innerhalb von 15 min ist die Anmeldung 15 min gesperrt; die Meldung nennt
  die Uhrzeit, ab der es wieder geht.
- Meldet die Anmeldeseite „… der Browser hat das Sitzungs-Cookie verworfen“, ist das UI über
  `http://` geöffnet (etwa direkt `http://<vm>:8080`). Das Sitzungs-Cookie gilt nur über HTTPS
  (`app.cookie_secure: true`). Öffne das UI über den Reverse Proxy. Nur für einen Test im LAN
  ohne HTTPS setzt du `cookie_secure: false` und startest die app neu
  (`docker compose restart ems-app`); Passwort und Sitzung gehen dann unverschlüsselt durchs LAN.

### Zwei-Faktor-Anmeldung

Unter **Konto** → „Zwei-Faktor-Anmeldung (TOTP)“ → „Einrichten“ bestätigst du dein Passwort,
scannst den QR-Code mit einer Authenticator-App (oder tippst das angezeigte Geheimnis ab), gibst
den sechsstelligen Bestätigungscode ein und wählst „Aktivieren“. Ab dann fragt die Anmeldung
nach Passwort und Bestätigungscode. Für Admins ist das dringend empfohlen; „System“ und die
Übersicht weisen auf Admins ohne TOTP hin. Ist das Telefon verloren, hilft `reset-totp`
(Abschnitt 5).

### Sitzung und Leerlauf

- Eine Sitzung ohne „angemeldet bleiben“ endet nach 30 min ohne Bedienung. Bedienung ist jedes
  Tippen, Klicken oder Antippen im UI, auch in einem noch nicht gespeicherten Formular. Das gilt
  für Admins immer, auch wenn die Übersicht offen bleibt: automatische Abfragen und die
  Live-Anzeige zählen nicht als Bedienung.
- Ist die Sitzung abgelaufen, führt das UI zur Anmeldung („Sitzung abgelaufen – bitte neu
  anmelden.“) und danach zurück zur vorherigen Seite.
- Ein Passwortwechsel unter **Konto** meldet alle anderen Geräte ab. Ändert ein Admin Rolle oder
  Passwort eines Benutzers, sind dessen Sitzungen beendet.

### App installieren

Das UI lässt sich wie eine App mit eigenem Symbol und Fenster einrichten (es bleibt eine
Webseite und braucht die Verbindung zur app; HTTPS ist Voraussetzung):

- **Android (Chrome):** Menü ⋮ → „App installieren“ (je nach Version „Zum Startbildschirm
  hinzufügen“).
- **iPhone und iPad (Safari):** Teilen → „Zum Home-Bildschirm“.
- **Desktop (Chrome, Edge):** Symbol „App installieren“ in der Adressleiste.

### Hell und dunkel

Der Knopf mit dem Halbkreis in der Kopfzeile wechselt zwischen System, Hell und Dunkel. Die Wahl
gilt für diesen Browser.

### Seiten

| Seite | Inhalt |
|---|---|
| Übersicht | Energiefluss live mit den Verbrauchern unter dem Haus, Strompreis jetzt und die nächsten 3 h (Balken überfahren, antippen oder mit den Pfeiltasten wählen zeigt den Preis der Viertelstunde), Ladestände, Preise und PV-Prognose für heute und morgen, Hinweise, Verbraucher mit Leistung und Energie seit Mitternacht |
| Verlauf | Diagramm mit bis zu 8 Reihen für frei wählbare Tage (höchstens 400), Tagesbilanz mit Abdeckung (Viertelstunden und Anteil der aufgezeichneten Zeit; am laufenden Tag „bis jetzt“, also nur die vergangenen Viertelstunden), Kosten, Erlös, Autarkie und den Tageswerten der Zähler (Differenz über den Tag) für den Abgleich mit VRM |
| Verbraucher | Verbraucherbaum mit „Sonstiges“, Leistung und Energie seit Mitternacht („Heute“); Admins legen Verbraucher an, ändern und löschen sie |
| System | Komponenten, Hinweise, core (Betriebsart mit Beginn und Gründen, Eingangsdaten, Adapter, Selbstprüfung, Alarme), Aktoren (angefordert, wirksam, Freigabe, Hinweise und Sperren), Watchdog am Cerbo (Abschnitt 14), Preise (Admins: „Preise jetzt abrufen“), PV-Prognose, alle Signale mit Suche |
| Einstellungen | Tarif, OeMAG-Werte, Preise, PV-Modell, Namen der Ladestationen und harte Grenzen; Admins bearbeiten, bewerten neu und exportieren oder importieren (Abschnitt 8) |
| Benutzer | nur Admins: anlegen, Rolle ändern, Passwort setzen, löschen |
| Protokoll | nur Admins: Änderungsprotokoll nach Bereich |
| Konto | eigenes Passwort, Zwei-Faktor-Anmeldung, Abmelden |

### Namen der Ladestationen

Unter **Einstellungen** → „Bearbeiten“ → „Ladestationen“ gibt ein Admin jeder Wallbox einen
eigenen Namen (höchstens 40 Zeichen). Er erscheint im Energiefluss, im Verlauf und bei den
harten Grenzen. Ein leeres Feld heißt: der Typname („EVCS“, „Wall Connector“). Der Schlüssel aus
`config.yaml` (z. B. `evcs`) bleibt unverändert.

### Energie der Verbraucher

Die Spalte „Heute“ zeigt die Energie seit Mitternacht (Wien). Die app rechnet sie jede Minute
aus dem Leistungsverlauf in InfluxDB:

- **core-Signale** `load.…`, `pv.…` und `wallbox.….power_w` sowie das Haus aus den
  Minutenmitteln der Messung `power`. Fehlen Minuten (core oder InfluxDB aus), zählen sie nicht.
- **HA-Entitäten** aus der HA-Datenbank. Ein Wert gilt bis zur nächsten Änderung; der letzte
  Wert vor Mitternacht (höchstens 7 Tage zurück) gilt ab Mitternacht.
- „Sonstiges“ ist die Energie des Elternelements minus die seiner Unterverbraucher.

Fehlt die Energie, steht der Grund unter dem Namen („Energie: …“):

| Grund | Bedeutung |
|---|---|
| kein Verlauf seit Mitternacht | InfluxDB hat für heute keine Werte dieses Signals. |
| Signal wird nicht aufgezeichnet | Der core schreibt dieses Signal nicht nach InfluxDB, oder die HA-Datenbank ist nicht eingerichtet. |
| Verlauf nicht lesbar | InfluxDB lehnt die Abfrage ab oder ist nicht erreichbar (Log der app). |

### Verbraucher aus Home Assistant

Ein Verbraucher liest seine Leistung aus einem core-Signal oder aus einer HA-Entität.

- **HA-Entität:** Die app fragt Home Assistant nicht selbst. Sie liest alle 15 s den letzten
  Wert der letzten 24 h aus der HA-Datenbank in InfluxDB (`influxdb.ha_database`, derselbe
  Server wie `influxdb.url`, Lesezugriff aus Abschnitt 3).
  - Beide Schemata der HA-Integration gehen: Messung je Einheit (`W`, `kW`; HA-Standard) und
    Messung je Entität (`measurement_attr: entity_id`). Im zweiten Fall steht die Einheit im
    Feld `unit_of_measurement_str`.
  - Die Vorschläge im Formular zeigen Sensoren in W oder kW. Bei Messung je Entität sind es
    die, die in den letzten 30 Tagen etwas geschrieben haben. Andere Entitäten trägst du von
    Hand ein und wählst die Einheit.
- **core-Signal:** Für Live-Werte ohne InfluxDB trägst du die Entität in `config.yaml` unter
  `homeassistant.entities` ein, z. B. `load.serverschrank.power_w: sensor.serverschrank_power`.
  Der Sensor muss in W messen; der core rechnet nicht um. Danach
  `docker compose restart ems-core ems-app`.

Fehlt ein Wert, steht der Grund unter dem Namen:

| Grund | Bedeutung und Abhilfe |
|---|---|
| kein Wert in den letzten 24 h | HA schreibt nur bei einer Änderung, z. B. ist das Gerät seit gestern aus. Oder HA schreibt die Entität gar nicht nach InfluxDB (`include`/`exclude` in der InfluxDB-Konfiguration von HA). |
| HA meldet kWh statt W | Die Entität hat eine andere Einheit als eingestellt, etwa ein Energiezähler statt eines Leistungssensors. Einheit oder Entität korrigieren. |
| HA-Datenbank nicht lesbar | InfluxDB lehnt die Abfrage ab. Meist fehlt `GRANT READ` (Abschnitt 3), oder der Datenbankname stimmt nicht. Die genaue Meldung steht im Formular unter „HA-Entität“ und im Log der app. |
| HA-Datenbank nicht eingerichtet | `influxdb.ha_database` fehlt in `config.yaml`. |

Log, Datenbank und Schema prüfst du so (InfluxQL im influx-CLI oder in Grafana → Explore, als
Admin):

```bash
docker compose logs --since 30m ems-app | grep "HA-"
```

```sql
SHOW DATABASES
SHOW GRANTS FOR "bindaems"
SHOW SERIES ON "homeassistant" WHERE "entity_id" = 'serverschrank_power'
```

`entity_id` steht dort ohne `sensor.`. Die Zeile von `SHOW SERIES` beginnt mit der Einheit
(`W,…`) oder mit der Entität (`sensor.serverschrank_power,…`); beides liest die app. Beginnt
sie mit etwas anderem, etwa `state,…` bei `override_measurement`, findet die app den Sensor
nicht. Dann nimm den Weg über das core-Signal.

### Live-Anzeige

Das Kennzeichen oben rechts zeigt den Zustand der Live-Verbindung:

- **live:** Die Werte kommen laufend, etwa jede Sekunde.
- **veraltet:** Seit über 5 s kamen keine neuen Werte. Energiefluss und Ladestände zeigen die
  letzten Werte gedämpft.
- **core getrennt:** Die app erreicht den core nicht. Prüfe `docker compose ps` und
  `docker compose logs --tail 100 ems-core` sowie unter „System“ die Komponente core.
- **verbinde …:** Das UI baut die Verbindung neu auf (nach 1, 2, 5 und 10 s, danach alle 30 s).
  Hält das an, prüfe Netz und Reverse Proxy, vor allem das WebSocket-Upgrade für `/api/live`
  (Abschnitt 6).
- **abgemeldet:** Die Sitzung ist abgelaufen.

Fehlende Werte erscheinen als „–“, nie als 0.

### Demo-Backend

Das Demo-Backend aus dem README (feste Messwerte, eingefrorene Uhr, bekannte Passwörter) dient
nur der Entwicklung des UI. Es läuft nie auf der Anlage und nie im Netz.

## 13. Betriebszustände und Freigaben

Der core arbeitet mit fünf Betriebszuständen (Spec 7.2). Du siehst sie im UI unter **System**
(Karte „core“, Zeile „Betriebsart“, mit Beginn und Gründen), in Home Assistant als Sensor
„Betriebsart“ und im Log (Meldung „Betriebszustand gewechselt“).

| Zustand | Anzeige im UI | Bedeutung |
|---|---|---|
| `START` | Start | Der core lädt Konfiguration und gespeicherte Absicht und verbindet die Adapter. Der erste Zyklus führt immer nach OBSERVE. |
| `OBSERVE` | Beobachten (nur lesend) | Der core liest und prüft, regelt aber nichts. Die Gründe sagen, was noch fehlt. |
| `CONTROL` | Regeln | Das Gate ist offen. Jeder Aktor arbeitet in seiner Betriebsart, im Dry-Run oder live. |
| `DEGRADED` | Eingeschränkt | Wie CONTROL, aber mit Einschränkung: ein Teilsystem ist gesperrt, ein Gerät ist nicht erreichbar oder der Watchdog fehlt; auch bei ausgeschaltetem Regelbetrieb, solange die Übergabe der Aktoren läuft. Die Gründe nennen es. |
| `SAFE` | Sicherer Zustand | Kritische Daten fehlen, die Phasen stimmen nicht oder das Netz ist ausgefallen. Der core setzt die Sicherwerte der Aktoren, und es gibt einen Alarm „Sicherer Zustand: …“. |

Befehle entstehen an diesen Stellen:

- **Heartbeat** an den Watchdog (Abschnitt 14), nur solange der core regelt.
- **Sicherer Zustand:** In SAFE bekommt jeder nicht gesperrte Aktor seine Sicherwerte: Victron den
  aufgehobenen Netz-Sollwert-Override, die Entladegrenze „unbegrenzt“ und den Min-SOC auf der
  USV-Reserve, die EVCS ihren sicheren Strom (`safe_a`) und der Tesla seinen Mindeststrom. Ein
  Tesla, der laut Tessie nicht lädt (er schläft, ist nicht zu Hause oder nicht angesteckt), bekommt
  nichts: Für ihn gäbe es nur Versuche, die nicht gesendet werden, samt Alarm. Bei Netzausfall
  (Inselbetrieb) kommt für EVCS und Tesla „Laden aus“ dazu. Ein Strom, der schon unter dem Sicherwert
  liegt, bleibt stehen, auch bei der Übergabe: Der sichere Zustand erhöht nie einen Strom.
- **Übergabe** am Ende des Live-Betriebs (unten).
- **Laderegelung** (seit Plan 2c-1, Abschnitt „Laderegelung“): Strom und „Laden an/aus“ der EVCS und
  des Tesla. Sie regelt nur in CONTROL und DEGRADED bei eingeschaltetem Regelbetrieb.

Jeder Befehl geht durch die Safety-Schicht. Einem Aktor, der nicht live ist, sendet der core nichts;
sein Befehl steht dann als „Dry-Run“ im Entscheidungslog. In CONTROL schreibt der core sonst
nichts. Die Laderegelung schreibt nie auf Victron; die Befehle an den Akku folgen mit Plan 2c-2.

Der sichere Zustand und die Übergabe gehen der Regelung vor: Ein Aktor, den der core gerade
übergibt, bekommt von ihr nichts, und in jedem Zyklus führt der core erst deren Befehle aus, dann die
der Regelung.

### Absicht und Regelbetrieb

Die Bedienung legt in einer **Absicht** fest, ob der core regeln soll („Regelbetrieb“), in welcher
Betriebsart jeder Aktor läuft und, seit Plan 2c-1, in welchem Lademodus jedes Fahrzeug lädt. Die app
sendet sie ab Plan 2d; bis dahin sendest du sie selbst (`PUT /v1/intent`, Abschnitt „Laderegelung“).
Ohne Absicht steht der core in OBSERVE mit dem Grund „Regelbetrieb ist aus.“, alle Aktoren laufen im
Dry-Run, und ein installierter Watchdog wartet.

- Der core speichert die zuletzt angenommene Absicht in `/data/intent.json` (Volume
  `core-data`) und nutzt sie nach einem Neustart weiter. Er durchläuft trotzdem immer OBSERVE
  und das Gate.
- Ist die Datei unlesbar, startet der core ohne Absicht, also mit ausgeschaltetem Regelbetrieb.
  Das Log warnt, und unter **System** steht der Alarm „Gespeicherte Absicht unlesbar; der core
  startet ohne Absicht (Regelbetrieb aus).“
- Eine Absicht gibt nur frei, was `config.yaml` erlaubt: Ein Aktor mit `live`, aber ohne
  `live_allowed`, bleibt im Dry-Run.

### Von OBSERVE nach CONTROL (Gate)

Der core geht nur nach CONTROL (oder DEGRADED), wenn alle Bedingungen gelten, die den ganzen core
betreffen:

1. Die Absicht verlangt den Regelbetrieb.
2. Die kritischen Messwerte sind seit 30 s lückenlos aktuell: Netzleistung und AC-Eingang der
   Multis je Phase sowie der Akku-SOC.
3. Die 3 Phasen mit je einem Gerät sind bestätigt.
4. Der Watchdog ist für diesen core scharf, sobald ein Victron- oder EVCS-Aktor live gewünscht
   und freigegeben ist (Abschnitt 14). Das heißt: Er zeigt einen der zuletzt gesendeten Zähler des
   core und meldet das Timeout aus `victron.watchdog.timeout_s`.
5. Es liegt kein Grund für SAFE vor.

Den Watchdog schärft der core selbst: Sind alle übrigen Bedingungen erfüllt und meldet der
Watchdog seinen Zustand und das erwartete Timeout, beginnt der core in OBSERVE mit den
Heartbeats. Meldet der Watchdog nichts oder ein anderes Timeout, bleibt der Heartbeat aus; ein
scharfer Watchdog löst dann aus. Die Alarme „Watchdog-Timeout …“ und „Watchdog am Cerbo antwortet
nicht“ nennen die Abhilfe (Abschnitt 14).

Was nur ein Teilsystem betrifft, sperrt dieses (nächster Abschnitt), statt den ganzen core zu
stoppen. Im laufenden Betrieb führen veraltete Cerbo-Daten, ein Netzausfall und nicht
bestätigte Phasen nach SAFE. Ein fehlender Watchdog, nicht erreichbare Geräte, Sperren und eine
laufende Übergabe bei ausgeschaltetem Regelbetrieb führen nach DEGRADED.

Die Gründe stehen im UI unter der Betriebsart:

| Grund | Bedeutung und Abhilfe |
|---|---|
| Regelbetrieb ist aus. | Die Absicht verlangt keinen Regelbetrieb, oder es gibt noch keine. Ohne eine Absicht per `PUT /v1/intent` ist das bis Plan 2d der Normalfall. |
| Kritische Messwerte noch nicht 30 s aktuell. | Nach dem Start oder einer Störung brauchen die Messwerte 30 s. Hält es an, prüfe die Selbstprüfung „Datenfrische“ und den Adapter `victron`. |
| 3 Phasen mit je einem Gerät nicht bestätigt. | Der Cerbo meldet nicht die erwartete Phasenzahl. Siehe Selbstprüfung „Phasen (vebus)“. |
| Watchdog am Cerbo ist nicht scharf. | Ein Victron- oder EVCS-Aktor ist live gewünscht, der Watchdog aber nicht für diesen core scharf: Er ist nicht scharf, zeigt keinen Zähler des core oder meldet ein anderes Timeout (Abschnitt 14). |
| Cerbo-Daten veraltet: … | Ein kritischer Messwert fehlt oder ist nicht aktuell; der Grund nennt die Signale. |
| Netzausfall (Inselbetrieb). | Der Cerbo meldet Inselbetrieb. |
| EVCS „…“ nicht erreichbar. Wall Connector „…“ nicht erreichbar. Tessie für „…“ nicht erreichbar. | Die Quelle ist getrennt; den Adapter zeigt die Karte „core“. |
| … gesperrt: … | Ein Teilsystem ist gesperrt (nächster Abschnitt). |
| Regelbetrieb wird beendet: Übergabe der Aktoren läuft. | Der Regelbetrieb ist aus, aber ein Aktor hält noch Werte des EMS. Der core setzt seine Sicherwerte (siehe „Übergabe“) und geht danach nach OBSERVE. Hält es an, prüfe die Alarme „Sicherwert … nicht bestätigt“ (Abschnitt 14). |

### Sperren

Stimmt bei einem Teilsystem etwas nicht oder lässt es sich nicht prüfen, sperrt der core das
Schreiben dorthin (Spec 7.9). Der Rest läuft weiter: Im Regelbetrieb steht der core dann in
DEGRADED, und die Sperre ist der Grund. Das UI zeigt sie unter **System** → „Aktoren“ in der
Spalte „Hinweise“ als „gesperrt: …“.

| Teilsystem | Gesperrt, wenn |
|---|---|
| Victron (ESS) | Dynamic ESS aktiv, ein Victron-Ladefenster aktiv, ESS-Modus oder BatteryLife-Zustand weichen von den erwarteten Werten ab, der Min-SOC liegt unter der USV-Reserve, einer dieser Werte ist nicht lesbar, oder ein anderes System hat den Netz-Sollwert-Override geändert |
| EVCS | die EVCS ist nicht im manuellen Modus, ihr Modus ist nicht lesbar, oder ein anderes System hat ihren Ladestrom geändert |
| Fahrzeug mit Tessie | im Fahrzeug ist ein Ladeplan aktiv, oder ein anderes System hat den Ladestrom geändert |

- Ein Min-SOC über der USV-Reserve sperrt nicht; die Selbstprüfung warnt nur.
- Eine **fremde Änderung** erkennt der core nur bei einem Wert, den er selbst bestätigt hat
  (EVCS-Strom, Netz-Sollwert-Override, Tesla-Ladestrom), und nur bei Aktoren, die Werte des EMS
  halten. Weicht der Wert bei 3 Prüfungen im Abstand von 10 s hintereinander ab und ist kein
  eigener Schreibvorgang offen, gibt es den Alarm „… von außen geändert (… statt …).“
  (Abschnitt 14, „Meldungen des core“), und der Aktor ist gesperrt. Einen eigenen Eintrag im
  Entscheidungslog gibt es dafür noch nicht (Plan 2d). Beim Tesla zählt ein bestätigter Strom erst,
  wenn der Tessie-Cache ihn zeigt, denn der Cache hinkt dem Fahrzeug hinterher.
- Eine Sperre löst sich von selbst, wenn die Ursache 60 s lang weg war (`safety.lock_release_s`
  in `config.yaml`). Kehrt die Ursache zurück, beginnt die Wartezeit von vorn. Der Alarm nach einer
  fremden Änderung besteht `safety.lock_release_s` ab der letzten Erkennung; danach gilt die Frist
  wie bei jeder Sperre.
- Eine Freigabe per Hand („Quittierung“) gibt es erst mit der Bedienung in Plan 2d.
- Solange Dynamic ESS an ist, bleibt Victron gesperrt. Die EVCS darf trotzdem regeln, denn ihre
  Regelung braucht nur die Netzmessung und den Phasenwächter. So kann der Dry-Run auch vor der
  Live-Schaltung von Victron laufen (Entscheidung des Betreibers vom 10.10.2026).

### Dry-Run und live

Aktoren sind `victron`, `wallbox.<name>` für jede EVCS und `vehicle.<name>` für jedes Fahrzeug
mit Tessie. Der Wall Connector und der e-Golf sind keine Aktoren. Jeder Aktor hat eine
Betriebsart: **Dry-Run** (die Safety-Schicht prüft jeden Befehl, der core protokolliert, was er
senden würde, und sendet nichts) oder **live**.

Ein Aktor läuft nur live, wenn alle vier Bedingungen gelten (Spec 8.7):

1. `live_allowed: true` für den Aktor in `config.yaml`. Der Standard ist `false`. Es steht unter
   `victron`, bei jeder EVCS unter `wallboxes.<name>` und bei jedem Fahrzeug unter
   `vehicles.<name>`; bei einem Fahrzeug verlangt es den Abschnitt `tessie`, bei einer EVCS ihren
   Eintrag unter `victron.instances.evcharger` (sonst setzt der Watchdog für sie keinen sicheren
   Strom). Fehlt das, lehnen core und app die `config.yaml` beim Start mit diesem Grund ab.
2. Das Prüfprotokoll Teil 2 ist für diesen Aktor bestanden.
3. Ein Admin schaltet ihn im UI live, mit Bestätigung (ab Plan 2d).
4. Bei Victron und EVCS ist der Watchdog scharf.

Der core prüft die Punkte 1 und 4 selbst. Punkt 2 stellst du sicher, bevor du `live_allowed`
setzt. Das UI zeigt unter **System** → „Aktoren“ je Aktor „Angefordert“, „Wirksam“ und „live
erlaubt“. Wirksam live ist ein Aktor nur im Regelbetrieb. Weicht „Wirksam“ ab, nennen die
„Hinweise“ den Grund: „In config.yaml nicht für den Live-Betrieb freigegeben (live_allowed).“,
„Regelbetrieb ist aus.“ oder „Watchdog am Cerbo ist nicht scharf.“ Während einer Übergabe (nächster
Abschnitt) steht dort „Übergabe: Sicherwerte werden gesetzt.“, auch bei ausgeschaltetem
Regelbetrieb: Dann ist nur der übergebene Aktor wirksam live, die übrigen live angeforderten zeigen
Dry-Run mit „Regelbetrieb ist aus.“

**Live-Freigabe zurücknehmen:** Bevor du bei einem Victron- oder EVCS-Aktor `live_allowed` wieder
auf `false` setzt oder ihn auf anderem Weg aus der `config.yaml` nimmst (etwa eine EVCS entfernst),
stellst du ihn in der Absicht auf Dry-Run (ab Plan 2d in der Bedienung, bis dahin mit
`PUT /v1/intent`). Der core übergibt ihn dann (nächster Abschnitt): Er setzt die Sicherwerte und
sendet erst danach die Freigabe 0. Warte, bis der Aktor unter **System** → „Aktoren“ als „Wirksam“
„Dry-Run“ zeigt, ohne den Hinweis „Übergabe: Sicherwerte werden gesetzt.“ oder „Watchdog am Cerbo
ist nicht scharf.“, und kein Alarm „… Sicherwert für … nicht bestätigt.“ mehr ansteht. Der zweite
Hinweis erscheint, wenn der Watchdog während der Übergabe ausgelöst hat: Sie kann dann noch offen
sein, auch wenn „Wirksam“ nicht mehr „live“ zeigt. Der Watchdog zeigt erst „wartet“, wenn kein
anderer Victron- oder EVCS-Aktor mehr live bleibt: Hält noch einer Werte des EMS, sendet der core
keine 0. Änderst du zuerst die `config.yaml`, sieht der core den Aktor nach dem Neustart nicht mehr
als live angefordert, übergibt ihn nicht und sendet keine 0. Ein scharfer Watchdog löst dann 90 s
nach dem letzten Heartbeat aus.

### Übergabe am Ende des Live-Betriebs

Ein Aktor **hält Werte des EMS**, sobald der core ihm einen Wert gesendet hat. Nach einem Neustart
gilt jeder Aktor so, den die gespeicherte Absicht live anfordert und `config.yaml` freigibt, denn
sein Gerät kann noch Werte vom letzten Lauf halten.

- Stellst du einen solchen Aktor auf Dry-Run oder schaltest den Regelbetrieb aus, **übergibt** der
  core ihn: Er setzt seine Sicherwerte (die Entscheidungen nennen den Grund „Übergabe“) und liest
  sie zurück. Erst dann gilt er als übergeben, und erst dann gibt der core den Watchdog frei
  (Freigabe 0), sofern kein Victron- oder EVCS-Aktor mehr Werte des EMS hält. Während der Übergabe
  zeigt der Aktor „Wirksam: live“ mit dem Hinweis „Übergabe: Sicherwerte werden gesetzt.“
- Bei ausgeschaltetem Regelbetrieb bleibt der core bis dahin in DEGRADED (bei Gründen für SAFE in
  SAFE) mit dem Grund „Regelbetrieb wird beendet: Übergabe der Aktoren läuft.“ und geht erst danach
  nach OBSERVE. Aus SAFE heraus bleibt er in SAFE, bis die Übergabe fertig ist.
- Der core versucht es je Wert höchstens 3-mal im Abstand von mindestens 10 s. Scheitert die
  Übergabe, gibt es den Alarm „… Sicherwert für … nicht bestätigt.“ und keinen Heartbeat mehr. Bei
  Victron und EVCS löst dann der Watchdog aus und setzt die Sicherwerte; sobald der core sie
  zurückliest, gilt die Übergabe als erledigt, und er gibt frei.
- Beim Tesla gibt es keinen Watchdog. Eine gescheiterte Übergabe bleibt offen: Bei ausgeschaltetem
  Regelbetrieb steht der core in DEGRADED mit dem Übergabe-Grund, und der Alarm bleibt, bis der
  Strom des Fahrzeugs den Sicherwert (`vehicles.<name>.min_a`) zeigt oder der Tesla wieder live
  gewünscht ist. Stelle den Ladestrom dann von Hand auf den Mindeststrom.
- Kann der core nicht schreiben, etwa weil er nach einem Neustart in OBSERVE steht, übergibt er
  nicht und gibt den Watchdog nicht frei. Der Watchdog löst aus, weil der Heartbeat ausbleibt; danach
  geht es weiter wie oben.
- Gesperrte Aktoren übergibt der core nicht. Regelt ein anderes System den Aktor (ein
  mitregelndes System, eine fremde Änderung oder ein Prüfpunkt, der fehlschlägt), hält er sofort
  keine Werte des EMS mehr. Sperrt ihn nur ein Prüfpunkt, der nicht lesbar ist, etwa im ersten
  Zyklus nach dem Start, behält er sie: Seine Übergabe scheitert dann an der Sperre, und der
  Watchdog übernimmt.

### Entscheidungslog

Der core hält jede Entscheidung zu einem Befehl fest (Spec 7.10):

- jeden gesendeten Befehl mit seinem Ergebnis („gesendet und bestätigt“, „Fehler“, „nicht
  bestätigt“ oder „nicht gesendet“),
- im Dry-Run jedes Urteil, das einen Wert ergäbe („Dry-Run, nicht gesendet“),
- jede Ablehnung der Safety-Schicht (dieselbe Ablehnung höchstens alle 60 s),
- seit Plan 2c-1 jeden Befehl, den ein Stundenlimit des Tesla zurückstellt („zurückgestellt“,
  Ergebnis `deferred`, mit dem Befund `rate.per_hour` oder `rate.start_stop`; dieselbe
  Zurückstellung höchstens alle 60 s). Andere Zurückstellungen, etwa wegen der 90-s-Regel, und
  unveränderte Werte erscheinen nicht.

„Dieselbe“ heißt: gleicher Aktor, gleiche Art, gleicher angeforderter Wert und gleiche Befunde. Ein
zurückgestellter Stopp kurz nach einem zurückgestellten Start erscheint also beide Male.

Ein Eintrag lautet etwa: `EVCS „evcs“ (sicherer Zustand): Strom 6 A – gesendet und bestätigt.`
Die Laderegelung nennt ihre Gründe in Klammern, etwa
`EVCS „evcs“ (PV-Überschuss, Überschuss): Strom 8 A – Dry-Run, nicht gesendet.` (alle Gründe und
Eingaben: Abschnitt „Laderegelung“). Eine erkannte fremde Änderung steht noch nicht darin, nur als
Alarm; der Eintrag kommt mit Plan 2d.

- **Auf der Platte:** `/data/decisions/` im Volume `core-data`, eine Datei je Tag
  (`decisions-JJJJ-MM-TT.jsonl`, UTC-Datum) mit einer JSON-Zeile je Entscheidung. Der core behält
  höchstens 7 Tage und 50 MB und räumt beim Start und danach stündlich auf. Ein Schreibfehler
  unterbricht den Zyklus nicht; er steht im Log.
- **Als Stream:** Die interne API sendet jede Entscheidung sofort als Meldung `decision`.
- **Abruf:** `GET /v1/decisions` liefert die Entscheidungen nach einem Zeitpunkt (Zugang wie in
  Abschnitt 4, „Diagnose“). `since` ist Pflicht und braucht eine Zeitzone; ein `+` in der Zeitzone
  steht im Query als `%2B`. `limit` hat den Standard 1000 und höchstens 5000.

  ```bash
  curl -H "Authorization: Bearer $BINDAEMS_INTERNAL_TOKEN" \
    "http://127.0.0.1:8081/v1/decisions?since=2026-10-12T00:00:00%2B00:00&limit=100"
  ```

  Die Antwort ist `{"decisions": […], "more": true|false}`. Entscheidungen gleicher Zeit (die eines
  Zyklus) bleiben beisammen, deshalb kann sie mehr als `limit` enthalten. `more` sagt, ob weitere
  folgen: Nimm dann das `ts` der letzten gelieferten als neues `since`. Die Entscheidungen des
  laufenden Zyklus erscheinen erst nach seinem Ende.
- Die app speichert Entscheidungen erst ab Plan 2d. Bis dahin sind die Dateien die einzige Quelle.
- Im Volume `core-data` legt der core außerdem ab: `intent.json`, `write_budget.json`
  (Schreibbudget für persistente Einstellungen am Cerbo), `rate_limits.json` (die Stundenfenster
  des Tesla; sie überstehen einen Neustart) und `spool/` (Telemetrie, Abschnitt 3).

### Reihenfolge der Inbetriebnahme

1. **Watchdog** installieren und die Probe bestehen (Abschnitt 14).
2. **Prüfprotokoll Teil 2** gemeinsam durchführen (Abschnitt 11): EVCS und Tesla zuerst, Victron
   mit vorübergehend abgeschaltetem Dynamic ESS. `live_allowed: true` setzt du nur für Aktoren,
   die bestanden haben.
3. **Dry-Run neben evcc:** Er beginnt mit den Images von Plan 2c-1 und einer Absicht per
   `PUT /v1/intent` (Abschnitt „Laderegelung“). Er dauert 2 Wochen, und die Entscheidungen werden
   gemeinsam gesichtet (Spec 18). Mit der Bedienung in Plan 2d wird er bequemer.
4. **Live-Schaltung:** EVCS und Tesla gemeinsam nach Plan 2d, mit abgeschaltetem evcc (Abschnitt
   „Laderegelung“, „Umstieg von evcc“). Victron folgt nach Plan 2c-2 (Spec 8.7).
5. Vor der Live-Schaltung von Victron schaltest du Dynamic ESS ab (Entscheidung vom 10.10.2026);
   danach läuft der Victron-Teil von Teil 2.

### Wartung

Für ein Update des core gilt der Ablauf aus Spec 15:

1. Regelbetrieb auf OBSERVE schalten (ab Plan 2d in der Bedienung; bis dahin mit `PUT /v1/intent`
   und `"control": false`, ohne Absicht steht der core ohnehin in OBSERVE).
2. Update einspielen: `docker compose pull && docker compose up -d` (Abschnitt 4).
3. Selbstprüfung abwarten: Die kritischen Messwerte müssen wieder 30 s aktuell sein, und unter
   **System** zeigt die Selbstprüfung keinen Fehler.
4. Zurück auf CONTROL schalten.

Ist ein Victron- oder EVCS-Aktor live, beachte den Watchdog (Abschnitt 14):

- Der Heartbeat hängt am Regelbetrieb. Schaltest du ihn aus, übergibt der core die Aktoren, die
  Werte des EMS halten (siehe „Übergabe“): Er setzt ihre Sicherwerte, sendet danach einmal die
  Freigabe 0 und keine Heartbeats mehr.
- Ein Neustart des core ist samt 30 s frischer Messwerte (und bestätigter Phasen) meist binnen
  90 s erledigt: Dann löst der Watchdog nicht aus. Dem Neustart selbst bleiben also nur etwa 60 s,
  denn der erste Heartbeat geht erst hinaus, wenn das Gate bis auf den Watchdog offen ist
  (Abschnitt 13, „Gate“). Dauert die Wartung länger, schalte den Regelbetrieb vorher aus (oder
  stelle die Aktoren auf Dry-Run) und warte, bis die Übergabe fertig ist. Der Watchdog wartet
  dann, ohne auszulösen.
- Beim Herunterfahren sendet der core keine Freigabe.

## 14. Watchdog am Cerbo

Der Watchdog ist der Totmann-Schalter der Anlage (Spec 8.6): ein kleiner Python-Dienst auf dem
Cerbo (Venus OS). Solange der core regelt, also ein Victron- oder EVCS-Aktor im Regelbetrieb
live gewünscht ist oder gerade übergeben wird, schickt der core alle 10 s einen Heartbeat. Bleibt
er 90 s aus, weil VM, Netz oder core ausgefallen sind, stellt der Watchdog selbst den sicheren
Zustand her:

- die EVCS auf den sicheren Strom (`wallboxes.<name>.safe_a`),
- den Min-SOC auf die USV-Reserve (`battery.reserve_soc_pct`),
- die Entladegrenze auf unbegrenzt,
- mit `victron.live_allowed` auch den Netz-Sollwert-Override des EMS: Er hebt ihn auf.

Zustände, Zeiten und D-Bus-Pfade stehen in `cerbo-watchdog/README.md`. Wichtig für den Betrieb:

- Ohne Bedarf schickt der core keinen Heartbeat. Der Watchdog wartet dann und greift nie ein.
  Bedarf besteht, solange ein Victron- oder EVCS-Aktor im Regelbetrieb live gewünscht ist oder
  übergeben wird; bis zur Live-Schaltung nach Plan 2d gibt es ihn nicht.
- Den Watchdog wertet der core nur als scharf, wenn er einen der zuletzt gesendeten Zähler des
  core zeigt und das Timeout aus `victron.watchdog.timeout_s` meldet. Hat eine Probe oder ein
  früherer Lauf ihn scharf gemacht, schützt er diesen core noch nicht. Der core schärft ihn selbst
  (Abschnitt 13, „Gate“).
- Die Freigabe 0 sendet der core erst, wenn kein Victron- oder EVCS-Aktor mehr Werte des EMS hält
  (Abschnitt 13, „Übergabe“) **und für keinen von ihnen ein Schreibvorgang offen ist**, gleich ob
  ihn die Sicherwerte oder die Laderegelung gesendet haben: Ein Wert, der nach der Freigabe noch
  ankäme, stünde sonst ohne Watchdog. Außerdem sendet er sie nur, wenn er in diesem Lauf für den
  Watchdog zuständig war: ab dem ersten Bedarf oder dem ersten Victron- oder EVCS-Aktor, der Werte
  des EMS hält, bis zum nächsten Neustart. Eine Probe oder `verify_part2`, die den Watchdog selbst
  schärfen und auslösen, stört deshalb nur einen core nicht, der nie zuständig war; war er es,
  starte ihn vorher neu. Ging die 0 verloren, sendet er sie nach dem Doppelten des Heartbeat-Takts
  erneut, außer der Watchdog zeigt einen fremden Zähler. Steht der Watchdog 60 s lang ohne Bedarf auf
  „ausgelöst“, räumt er mit der 0 auf.
- **Bestätigt** ist die Freigabe erst, wenn der Watchdog das Echo 0 meldet (in jedem Zustand) oder
  „wartet“ meldet und als Echo keinen der eigenen Zähler zeigt. Erst dann vergisst der core seine
  Zähler. „Wartet“ allein genügt nicht: Der Watchdog zeigt es vor dem Scharfschalten auch, wenn die 0
  verloren ging; dann steht noch ein eigener Zähler im Echo, und der core muss die 0 erneut senden
  können.
- **Startwert je Lauf:** Der erste Zähler eines Laufs ist `1_000_000 + Unix-Sekunden mod
  1_000_000_000` (am 12.10.2026 um 12:00 UTC ist das `792806400`). Probe und `verify_part2` zählen
  dagegen 1, 2, 3, …. So gilt der Zähler eines früheren Laufs oder eines Werkzeugs nie als Zähler
  dieses core. Liest du `/Heartbeat` am Cerbo, ist eine Zahl in dieser Größenordnung der Zähler des
  core, eine kleine stammt von einer Probe oder einem Werkzeug.
- Heartbeat-Takt und Timeout kommen aus `victron.watchdog` in `config.yaml`.
- Mit `victron.live_allowed` enthält die `config.ini` die Aktion `override_setpoint` (seit
  Watchdog 1.1.0): Bei einer Auslösung hebt der Watchdog den Netz-Sollwert-Override des EMS auf
  (`/Overrides/Setpoint` am Dienst `com.victronenergy.hub4`). Ohne die Freigabe von Victron bleibt
  er unberührt, denn dann nutzt Dynamic ESS den Override.
- Jede Aktion liest zuerst und schreibt nur, wenn der Wert vom Sicherwert abweicht. Danach liest
  sie zurück und wiederholt bei Fehlern (6-mal im Abstand von 5 s).
- Was der Watchdog bei einer Auslösung setzt, gilt nicht als fremde Änderung: Meldet er „ausgelöst“,
  vergisst der core die Erwartungen der Erkennung für Victron und EVCS (Abschnitt 13, „Sperren“).
- Nach einer Auslösung wird der Watchdog wieder scharf, sobald der Heartbeat 30 s lang stabil
  läuft.
- Nach einem Neustart des Cerbo startet ein scharfer oder ausgelöster Watchdog (seit 1.1.0) wieder
  scharf (`/data/bindaems-watchdog/state.json`), und der core hat 90 s, sich zu melden. Eine
  unlesbare Datei gilt als scharf.
- Nimmst du einem Victron- oder EVCS-Aktor `live_allowed`, stelle ihn vorher in der Absicht auf
  Dry-Run (Abschnitt 13, „Live-Freigabe zurücknehmen“). Sonst löst ein scharfer Watchdog 90 s
  nach dem letzten Heartbeat aus, also kurz nach dem Neustart des core.

### Voraussetzungen

- core und app laufen mit den Images von Plan 2b oder neuer, beide aus demselben Stand. Rolle sie
  zuerst aus: `docker compose pull && docker compose up -d` (Abschnitt 4). Sonst fehlen im Image des
  core die Werkzeuge `bindaems.tools.watchdog_config`, `bindaems.tools.watchdog_probe` und
  `bindaems.tools.verify_part2`, und die System-Seite kann den Status des Watchdogs nicht zeigen.
- Venus OS mit Python ≥ 3.8 (`install.sh` prüft das) und SSH-Zugang als `root` zum Cerbo
  (Root-Passwort setzen und SSH einschalten, siehe die Victron-Anleitung „Root Access“). Am
  besten hinterlegst du einen SSH-Schlüssel (`ssh-copy-id root@cerbo.lan`), sonst fragt
  `install.sh` mehrmals nach dem Passwort.
- Ein Rechner mit `bash`, `ssh`, `tar` und einem Checkout des Repositorys, z. B. die VM. Unter
  `/opt/bindaems` liegen nur die Dateien aus `deploy/`; den Ordner `cerbo-watchdog/` brauchst
  du zusätzlich.
- In `config.yaml` steht unter `victron.instances.evcharger` die Instanz der EVCS (laut
  Prüfprotokoll Teil 1). Sonst setzt der Watchdog für sie keinen sicheren Strom. Mit
  `live_allowed: true` für die EVCS ist der Eintrag Pflicht; ohne ihn lehnen core und app die
  `config.yaml` ab.

### config.ini erzeugen

Die `config.ini` des Watchdogs entsteht aus `config.yaml`. So stehen Zeiten, USV-Reserve und
sicherer EVCS-Strom nur an einer Stelle. Ändere die Datei nie von Hand.

```bash
cd /opt/bindaems
docker compose run --rm -T --entrypoint python ems-core \
  -m bindaems.tools.watchdog_config --config /config/config.yaml > config.ini.new \
  && mv config.ini.new config.ini
```

- `--entrypoint python`, denn der ENTRYPOINT des Images ist `python -m bindaems.core`. `-T`
  schaltet das Terminal ab, weil die Ausgabe mit `>` in die Datei geht.
- Die Ausgabe geht zuerst nach `config.ini.new`. Nur wenn das Werkzeug ohne Fehler endet, ersetzt
  `mv` die bisherige `config.ini`. Mit `> config.ini` bliebe nach einem Fehler eine leere Datei
  zurück.
- Das Werkzeug meldet auf der Fehlerausgabe, was die Datei nicht leisten kann:
  - Eine EVCS ohne GX-Instanz in `victron.instances.evcharger` ergibt eine Warnung (und einen
    Kommentar in der Datei): Der Watchdog setzt für sie keinen sicheren Strom.
  - Ist 2,5 × `victron.watchdog.heartbeat_s` nicht kleiner als `timeout_s`, bricht es mit dem
    Exitcode 2 ab: Der Watchdog lehnte eine solche Datei beim Start ab.
- Mit `victron.live_allowed` enthält die Datei die Aktion `override_setpoint` (seit Watchdog
  1.1.0); ohne steht an ihrer Stelle eine Kommentarzeile.
- Sieh dir die Datei an: Sie nennt je Aktion Dienst, Pfad und Sicherwert.

### Installieren

```bash
cd <repo>/cerbo-watchdog
./install.sh root@cerbo.lan /opt/bindaems/config.ini
```

Das Skript prüft das Gerät, überträgt Programm und `config.ini` mit `tar` über ssh nach
`/data/bindaems-watchdog/` (das übersteht Firmware-Updates) und hängt den Dienst unter die
Dienstüberwachung von Venus OS: sofort und, über einen Block in `/data/rc.local`, bei jedem
Start. Außerhalb dieses Ordners, des Links `/service/bindaems-watchdog` und seines Blocks in
`/data/rc.local` ändert es nichts. Am Ende zeigt es Version und Zustand: Nach der Erstinstallation
„Zustand: 0“, der Watchdog wartet.

Das Skript bricht ab, bevor es auf dem Cerbo etwas ändert, wenn

- die `config.ini` leer ist oder keinen Abschnitt `[watchdog]` hat (Exitcode 2, noch ohne ssh),
- `/data/rc.local` eine `exit`-Zeile ohne Einrückung enthält: Der Block des Watchdogs stünde
  dahinter und liefe beim Start nie. Entferne die Zeile und rufe das Skript erneut auf.

Die Dateien kommen zuerst nach `/data/bindaems-watchdog/neu/`. Dort prüft das neue Programm die
neue `config.ini`. Lehnt es sie ab, bricht das Skript mit der Meldung des Watchdogs ab, und ein
laufender Watchdog bleibt, wie er ist.

Das UI zeigt den Zustand ebenfalls: Unter **System** nennt die Karte „Watchdog am Cerbo“ den
Zustand „wartet“, den Heartbeat „aus (kein Aktor live)“ und die Version. In Home Assistant steht
„Watchdog ausgelöst“ auf „aus“. Vor der Installation steht der Zustand auf „keine Antwort“. Am
Cerbo liest du ihn direkt:

```bash
ssh root@cerbo.lan dbus -y com.victronenergy.bindaems /State GetValue
```

Die Werte sind 0 wartet, 1 scharf, 2 ausgelöst und 3 deaktiviert.

### Probe

Die Probe ist Punkt 5 des Prüfprotokolls Teil 2 (Spec 17.2). Sie prüft die ganze Kette vom EMS
bis zu den Aktionen auf dem Cerbo; der Punkt `evcs` von `verify_part2` (Abschnitt 11) führt sie
mit verstelltem EVCS-Strom aus. Sie

1. fragt den Watchdog nach Version und Zustand,
2. macht ihn mit Heartbeats scharf,
3. lässt die Heartbeats ausbleiben, bis er auslöst,
4. wartet 45 s ohne Heartbeat, bis seine Aktionen mit allen Wiederholungen durch sind, und nimmt
   die Heartbeats dann wieder auf, bis er wieder scharf ist,
5. und gibt ihn mit der 0 frei.

**Die Probe löst den Watchdog aus:** Er setzt die EVCS auf den sicheren Strom und schreibt
Min-SOC und Entladegrenze, wenn sie abweichen; mit `victron.live_allowed` hebt er auch den
Netz-Sollwert-Override auf. Wähle einen Zeitpunkt, zu dem das passt. Prüfe danach die Werte und
stelle sie wieder ein, wie du sie haben willst: den EVCS-Strom, den Min-SOC (jetzt die
USV-Reserve) und die Entladegrenze (jetzt unbegrenzt), mit `victron.live_allowed` auch den
Override.

```bash
cd /opt/bindaems
mkdir -p verification && sudo chown 10001 verification   # der Container läuft als UID 10001
docker compose run --rm -v "$PWD/verification:/out" --entrypoint python ems-core \
  -m bindaems.tools.watchdog_probe --config /config/config.yaml \
  --out /out/<Datum>-watchdog-probe.md
```

- Die Probe braucht einige Minuten und fragt vorher `Fortfahren? [j/N]`. Nur `j` startet sie.
  Dafür braucht `docker compose run` ein Terminal, deshalb steht hier **kein** `-T`. Mit `--yes`
  entfällt die Rückfrage.
- Der Bericht liegt danach auf dem Host in `/opt/bindaems/verification/`; im Container ginge er
  mit `--rm` verloren. Setze für `<Datum>` das Datum (JJJJ-MM-TT) ein und übernimm den Bericht
  nach `docs/verification/`.
- Der Bericht hat je Schritt eine Zeile mit OK, WARNUNG oder FEHLER. Der Exitcode ist 0, wenn
  kein Schritt FEHLER ist, und sonst 1. Bei ungültiger Konfiguration oder ungültigen Secrets ist er
  2.
- Die Zeile „Auslösung ohne Heartbeat“ nennt die Zahl der fehlgeschlagenen Aktionen. Die Probe
  liest sie erst nach „Wieder scharf“, denn erst dann ist sie der Endstand. Sieht die Probe „Wieder
  scharf“ nicht, ist die Zeile eine Warnung mit dem Zusatz „nicht wieder scharf; Zahl noch nicht der
  Endstand“ (bei einem Abbruch in der Pause „Abbruch vor dem Ende des Aktionslaufs“), auch bei 0
  fehlgeschlagenen Aktionen. Prüfe nach **jeder** Probe zusätzlich das Log des Cerbo (unten,
  „Logs“), auch wenn die Zahl 0 ist:

  ```bash
  ssh root@cerbo.lan 'grep Aktion /var/log/bindaems-watchdog/current | tai64nlocal | tail -n 20'
  ```

  Die Zeile „Aktionen abgeschlossen: …“ nennt das Ergebnis jeder Aktion (`ok`, `unverändert` oder
  `fehlgeschlagen`). Steht dort „Aktionen abgebrochen …“, endete der Lauf vorzeitig: Eine schon
  gescheiterte Aktion zählt dann als fehlgeschlagen, eine mit „offen“ wurde nie versucht.
- Die Probe verstellt vorher nichts: Stehen die Werte schon auf dem Sicherwert, schreibt der
  Watchdog nichts. Willst du das Zurücksetzen selbst sehen, nimm den Punkt `evcs` von
  `verify_part2` (Abschnitt 11): Er verstellt den EVCS-Strom vor der Probe selbst und prüft
  danach, dass er wieder auf dem sicheren Strom steht. Von Hand geht es auch.
- Lass die Probe nie laufen, während ein live geschalteter core Heartbeats sendet: Ihre
  abschließende 0 gibt den Watchdog frei, und ihre Auslösung setzt die Sicherwerte. Das wird mit
  der Live-Schaltung relevant (nach Plan 2d). Im Dry-Run stört der core die Probe nicht: Er sendet
  keine Heartbeats und gibt einen Watchdog nicht frei, für den er in diesem Lauf nie zuständig war.
- Mit Strg+C brichst du die Probe ab. Sie gibt den Watchdog dann vorher mit dem Heartbeat 0 frei
  und endet mit dem Exitcode 130, ohne Bericht. `docker stop` oder SIGTERM gibt ihn **nicht**
  frei. Gelingt die Freigabe nach einem Abbruch nicht, warnt die Probe auf der Konsole: Der
  Watchdog kann dann scharf bleiben und später auslösen oder ausgelöst bleiben. Starte die Probe
  erneut (sie endet mit der Freigabe) oder gib ihn am Cerbo frei:
  `dbus -y com.victronenergy.bindaems /Heartbeat SetValue 0`. Prüfe danach, dass `/State` 0 ist:
  `dbus -y com.victronenergy.bindaems /State GetValue`.
- Die Probe vergleicht die installierte `config.ini` nicht mit `config.yaml`. Änderst du
  `victron.watchdog`, die USV-Reserve oder einen sicheren Strom, erzeuge die `config.ini` neu und
  übertrage sie mit `install.sh`.

Danach prüfst du im UI und in HA, dass der Watchdog stimmt: Der Zustand steht auf „wartet“. Die
Karte „Watchdog am Cerbo“ zeigt eine Auslösung mehr, die Zeit der Probe und 0 fehlgeschlagene
Aktionen. Der Sensor „Watchdog ausgelöst“ steht auf „aus“.

### Aktualisieren

Rolle zuerst die neuen Images aus: `docker compose pull && docker compose up -d` (Abschnitt 4).
Das Werkzeug, das die `config.ini` erzeugt, steckt im Image des core, und erst das Image von
Plan 2b kennt die Override-Aktion. Erzeuge die `config.ini` dann neu, falls sich `config.yaml`
geändert hat, und rufe `install.sh` aus einem Checkout desselben Stands wie bei der Installation
auf. Das Skript ersetzt Programm, `config.ini` und den Block in `/data/rc.local` und startet den
Dienst neu. `state.json` bleibt: Ein scharfer Watchdog startet wieder scharf.

**Von 1.0.0 auf 1.1.0** (Plan 2b):

1. Images ausrollen (siehe oben).
2. `config.ini` neu erzeugen (Abschnitt „config.ini erzeugen“). Sie enthält jetzt eine Kommentarzeile
   zur Override-Aktion, mit `victron.live_allowed` die Aktion selbst.
3. `install.sh` aufrufen (Abschnitt „Installieren“).
4. Die Probe wiederholen. Sie liest die Zahl der fehlgeschlagenen Aktionen jetzt erst nach „Wieder
   scharf“.

Bis dahin meldet der core „Watchdog-Version 1.0.0 am Cerbo, erwartet 1.1.0; bitte mit install.sh
aktualisieren.“ Neu in 1.1.0: Ein ausgelöster Watchdog übersteht einen Neustart des Cerbo als
„scharf“, `state.json` wird samt Verzeichnis gesichert, und die Override-Aktion kommt dazu.

Meldet der core „Watchdog-Version … am Cerbo, erwartet …“, gehört der Watchdog zu einem anderen
Stand als der core. Aktualisiere ihn dann mit `install.sh` aus dem Checkout desselben Stands.

### Entfernen

```bash
./install.sh --uninstall root@cerbo.lan
```

Das hält den Dienst an und entfernt den Link, den Block in `/data/rc.local` und das Verzeichnis
`/data/bindaems-watchdog/` samt `state.json`. Ohne Watchdog gehen Victron und EVCS nicht live
(Spec 8.7).

### Logs

Der Dienst schreibt nach `/var/log/bindaems-watchdog/current`. Das Log liegt im flüchtigen
Speicher: Nach einem Neustart des Cerbo ist es leer.

```bash
ssh root@cerbo.lan 'tail -n 50 /var/log/bindaems-watchdog/current | tai64nlocal'
```

Dort stehen der Start mit Version, Aktionen und Startzustand, jeder Zustandswechsel, jede
Auslösung mit dem Ergebnis jeder Aktion und abgelehnte Heartbeats. Ist die `config.ini`
ungültig, steht der Fehler im Log, und der Dienst versucht es erst nach 60 s erneut.

### Meldungen des core

Diese Alarme zeigt das UI unter **System** → „core“ → „Alarme“:

| Alarm | Bedeutung und Abhilfe |
|---|---|
| Der Watchdog am Cerbo hat ausgelöst und den sicheren Zustand hergestellt. | Der Heartbeat blieb 90 s aus, oder die Probe lief. Suche im Log des core, warum er ausfiel. Nach 30 s stabilem Heartbeat ist der Watchdog wieder scharf; besteht kein Bedarf, räumt der core ihn nach 60 s mit der Freigabe 0 auf (sofern er für den Watchdog zuständig war; sonst räumst du ihn von Hand auf, siehe unter der Tabelle). Nennt der Alarm fehlgeschlagene Aktionen, prüfe EVCS-Strom, Min-SOC und Entladegrenze von Hand und lies das Log des Cerbo. |
| Watchdog am Cerbo antwortet nicht; Victron und EVCS bleiben im Dry-Run. | Nur wenn der Betrieb den Watchdog braucht (Victron oder EVCS im Regelbetrieb live gewünscht oder in der Übergabe). Prüfe, ob der Dienst auf dem Cerbo läuft (`/State` lesen, Log) und ob der core den Cerbo über MQTT erreicht. |
| Watchdog am Cerbo ist deaktiviert; Victron und EVCS bleiben im Dry-Run. | Nur wenn der Betrieb den Watchdog braucht. In der `config.ini` steht `enabled = false`. Erzeuge sie neu und übertrage sie. |
| Watchdog-Version … am Cerbo, erwartet …; bitte mit install.sh aktualisieren. | Siehe „Aktualisieren“. |
| Watchdog-Timeout … s am Cerbo, erwartet … s; config.ini neu erzeugen und mit install.sh übertragen. | Die `config.ini` am Cerbo passt nicht zu `victron.watchdog.timeout_s`. Der core schärft den Watchdog dann nicht (Abschnitt 13, „Gate“); ein scharfer Watchdog löst aus. Erzeuge die `config.ini` neu und übertrage sie. |
| … Sicherwert für „…“ nicht bestätigt. (bei Victron und EVCS mit dem Zusatz „Der Watchdog am Cerbo übernimmt.“) | Der Aktor hat seine Sicherwerte nach 3 Versuchen nicht angenommen, im sicheren Zustand oder bei der Übergabe. Victron und EVCS setzt dann der Watchdog. Prüfe das Gerät (Cerbo verbunden, EVCS im manuellen Modus, Tessie erreichbar) und das Entscheidungslog. Der Alarm endet, wenn der Wert stimmt oder der Aktor kein Ziel mehr ist. Ein Tesla, der bei der Übergabe schläft, bekommt den Alarm, bis er aufwacht (Abschnitt „Laderegelung“, Hinweise für die Anlage). |
| … ist nach 3 Fehlern in 15 min bis HH:MM nicht steuerbar. | Nur Tesla: Tessie hat 3 Befehle abgelehnt oder nicht beantwortet. Bis zur Uhrzeit (Wiener Zeit) sendet der core nichts an das Fahrzeug. Prüfe das Tessie-Konto und das Log des core. |
| … von außen geändert (… statt …). | Ein anderes System hat Strom oder Netz-Sollwert-Override geändert, den der core bestätigt hatte (Abschnitt 13, „Sperren“). Der Aktor ist gesperrt. Suche das System (App, Automation, Zeitplan) und schalte es ab. |
| Gespeicherte Schreibzeiten liegen bis zu … min in der Zukunft; persistente Schreibzugriffe bleiben bis dahin begrenzt. | Die Uhr lief zurück (NTP-Sprung, Failover). Das Schreibbudget für persistente Einstellungen zählt die Einträge in der Zukunft weiter mit; die Begrenzung endet, wenn die Uhr aufgeholt hat. Prüfe `timedatectl`. |

**„Ausgelöst“ von Hand aufräumen:** Seit 1.1.0 übersteht „ausgelöst“ einen Neustart des Cerbo
(als „scharf“). Räumt kein zuständiger core den Watchdog auf, etwa nach einer abgebrochenen Probe
oder einem Lauf von `verify_part2`, während der core nie für ihn zuständig war, löst er deshalb nach
jedem Neustart des Cerbo erneut aus: Er setzt EVCS-Strom, Min-SOC und Entladegrenze wieder auf die
Sicherwerte und hebt mit `victron.live_allowed` den Override auf. Gib ihn dann von Hand frei wie bei
einer abgebrochenen Probe (Abschnitt „Probe“), aber nur, solange der core keine Heartbeats sendet
(Karte „Watchdog am Cerbo“: Heartbeat nicht „aktiv“), und prüfe danach `/State`:

```bash
ssh root@cerbo.lan 'dbus -y com.victronenergy.bindaems /Heartbeat SetValue 0'
ssh root@cerbo.lan 'dbus -y com.victronenergy.bindaems /State GetValue'   # 0 = wartet
```

Zeigt `/State` danach nicht 0, wiederhole die Freigabe und lies das Log des Cerbo (oben, „Logs“).
Stelle die Werte, die die Auslösungen gesetzt haben, danach wieder ein, wie du sie haben willst.

Die Alarme der Safety-Schicht, des sicheren Zustands, der Aktoren und der fremden Änderungen
tragen die monotone Zeit des core als „seit“, die übrigen die Wanduhr. Nach einem NTP-Sprung können
sie bis zum nächsten Neustart des core voneinander abweichen.

**Selbstprüfung:** Die Punkte der Selbstprüfung stehen im UI unter **System** → „core“ →
„Selbstprüfung“. Seit Plan 2c-1 gibt es zwei neue. Sie **warnen nur und sperren nichts**: Der
Phasenwächter bleibt auch ohne Importlimit sicher, denn er rechnet ohne die Hilfe des Akkus
(Abschnitt „Laderegelung“).

| Prüfpunkt im UI | Meldung | Status | Bedeutung und Abhilfe |
|---|---|---|---|
| Multi-Modus (`multi_mode`) | Multi auf „Ein“. | ok | |
| | Multi nicht auf „Ein“ (Modus …); das Importlimit wirkt nicht. | Warnung | Der Multi steht nicht auf „Ein“ (`vebus.mode` ist nicht 3), etwa auf „Nur Laden“. Dann wirkt Peak Shaving nicht: Am 12.10.2026 stieg L3 so auf 33,3 A, nahe an der Sicherung. Stelle den Multi am Cerbo auf „Ein“. |
| | Multi-Modus nicht verfügbar. | unbekannt | Der Cerbo meldet `vebus.mode` nicht (Adapter `victron`, Selbstprüfung „Datenfrische“ prüfen). |
| Importlimit (`import_limit`) | Importlimit am Cerbo … A wie konfiguriert. | ok | Der Wert am Cerbo (`settings.cgwacs.acinputlimit`) entspricht `grid.import_limit_a`, mit höchstens 0,5 A Abweichung. |
| | Importlimit am Cerbo … A, konfiguriert … A. | Warnung | Cerbo und `config.yaml` stimmen nicht überein. Passe das Importlimit am Cerbo oder `grid.import_limit_a` an; der Phasenwächter richtet PV-Überschuss und Min+PV am konfigurierten Wert aus. |
| | Importlimit am Cerbo aus. | Warnung | Peak Shaving ist am Cerbo abgeschaltet (Wert −1). Stelle am Cerbo das Importlimit je Phase mit „immer“ auf den Wert von `grid.import_limit_a` (Standard 20 A). |
| | Importlimit am Cerbo nicht verfügbar. | Warnung | Der Wert fehlt; prüfe den Adapter `victron`. |

## Laderegelung

Seit Plan 2c-1 regelt der core die beiden Ladepunkte selbst: die EVCS mit dem e-Golf und den Wall
Connector mit dem Tesla. Er schreibt dabei nur Strom und „Laden an/aus“ der EVCS und des Tesla (den
Tesla über Tessie), nie auf Victron; die Befehle an den Akku folgen mit Plan 2c-2. Jeder Befehl geht
durch die Safety-Schicht (Abschnitt 13). **Solange ein Aktor im Dry-Run ist, sendet der core nichts**
und protokolliert nur, was er täte. Live gehen EVCS und Tesla erst nach Plan 2d, gemeinsam und mit
abgeschaltetem evcc (unten, „Umstieg von evcc“).

### Wann der core regelt

- Nur in **CONTROL** und **DEGRADED** und nur bei eingeschaltetem Regelbetrieb (`"control": true` in
  der Absicht). In START, OBSERVE und SAFE, ohne Absicht und bei ausgeschaltetem Regelbetrieb ruht
  die Regelung, auch während der Übergabe nach dem Ausschalten des Regelbetriebs.
- Die Regelung sendet nur **Änderungen**: einen Wert erst, wenn er sich vom letzten eigenen
  unterscheidet. Im Dry-Run entsteht so nicht alle 5 s eine Entscheidung, obwohl evcc andere Werte
  einstellt.
- Ein gesperrter Aktor schreibt nicht (Abschnitt 13, „Sperren“). Ein Tesla, der schläft, nicht zu
  Hause oder nicht angesteckt ist, bekommt weder Strom noch Stopp. Ein schlafendes Auto, das zu
  Hause angesteckt ist, erreicht nur ein Start mit Weckerlaubnis (unten, „Tesla“).

### Lademodi

Jedes Fahrzeug hat einen Lademodus. Ohne Eintrag in der Absicht lädt es im **PV-Überschuss**.

| Modus | Wert | Verhalten | Grenze je Phase |
|---|---|---|---|
| PV-Überschuss | `pv` | Startet, wenn der Überschuss den Mindeststrom des Autos `control.start_after_s` (120 s) lang trägt, und stoppt, wenn er `control.stop_after_s` (300 s) lang darunter liegt. Dazwischen lädt das Auto am Mindeststrom weiter; die Lücke deckt der Akku über den Eigenverbrauch von ESS. | Importlimit minus Abstand: 20 − 1 = 19 A |
| Min+PV | `min_pv` | Lädt immer mindestens mit dem Mindeststrom, darüber wie PV-Überschuss. Der Mindeststrom geht dem Importlimit vor, nicht der Sicherung. | wie PV-Überschuss; für den Mindeststrom die Sicherung |
| Sofort | `now` | Voller Strom innerhalb der Grenze, auch aus dem Akku: unter dem Importlimit über den Eigenverbrauch von ESS, darüber über Peak Shaving. | Sicherung minus Abstand: 35 − 2 = 33 A |
| Aus | `off` | Stoppt das Laden und hält es gestoppt. | – |
| Boost | `boost_until` | Wie Sofort bis zu diesem Zeitpunkt oder bis zum Abstecken; danach gilt wieder der eingestellte Modus. | wie Sofort |

Die Zahlen gelten für die Beispielkonfiguration (`grid.fuse_a` 35 A, `grid.fuse_margin_a` 2 A,
`grid.import_limit_a` 20 A, `control.import_margin_a` 1 A); alle Werte stehen in `config.yaml`.

- Liegt der SOC unter USV-Reserve + `control.reserve_guard_pct` (5 %) oder ist er unbekannt, richten
  sich auch Sofort und Boost am Importlimit aus (Grund „Schutz der USV-Reserve“): Peak Shaving soll
  den Akku nicht unter die Reserve ziehen.
- Ein **Moduswechsel** wirkt sofort, ohne Startzeit, Stoppzeit und Mindestzeiten.
- Nach einem Start lädt ein Auto mindestens `control.min_on_s` (600 s), nach einem Stopp startet es
  frühestens nach `control.min_off_s` (600 s) wieder. Das sind höchstens 3 Start/Stopp-Zyklen je Auto
  und Stunde.
- Ein Auto startet nur, wenn die Grenze des Phasenwächters für es mindestens seinen Mindeststrom
  trägt (in Min+PV die Grenze an der Sicherung). Ist sie kleiner oder unbekannt, bleibt es aus; ein
  Auto, das schon lädt, geht auf den Mindeststrom.
- Es gibt **kein Ziel-SOC**: Die Autos stoppen an ihrem eigenen Ladelimit, der e-Golf lädt bis voll.
  Meldet ein Auto „geladen“, startet die Regelung es erst nach dem Abstecken wieder.
- **Abgesteckt:** Die Regelung will „Laden aus“; die EVCS bekommt das einmal. Bei einem **neuen
  Ansteckvorgang** beginnen PV-Überschuss und Aus mit „Laden aus“, auch wenn das Auto selbst
  gestartet hat. Sofort, Boost und Min+PV starten sofort.
- **Neustart des core, während ein Auto lädt:** Es gibt keinen sofortigen Stopp oder Start. Ein Auto
  im PV-Überschuss lädt mit Überschuss weiter; ohne Überschuss stoppt es frühestens nach
  `stop_after_s` ab dem ersten Regelzyklus. Das gilt auch nach jeder Pause der Regelung (SAFE,
  OBSERVE, Regelbetrieb aus).
- **Überschuss:** Er ergibt sich aus der Ladeleistung der Autos, dem Netzbezug und dem Akku, abzüglich
  `control.surplus_margin_w` (100 W). Entlädt der Akku, zählt das immer gegen den Überschuss: Der
  Anteil eines PV-Autos rechnet nie mit Leistung aus dem Akku. Aus dem Akku lädt es trotzdem, wenn
  es dem Überschuss nicht sofort folgt: in den Stopp-Minuten am Mindeststrom (oben), in Übergängen,
  bis die Regelung nachgezogen hat, und beim Tesla unter schnellen Wolken („Hinweise für die
  Anlage“, unten). Lädt der Akku, zählt seine Leistung nur, wenn die Autos Vorrang haben
  (`surplus_priority`, unten). Fehlt ein Messwert, startet kein PV-Auto.
- **Verteilung:** Die Autos kommen in der Reihenfolge `priority` dran, und jedes bekommt bis zu
  seinem Höchststrom, bevor das nächste etwas sieht. Mit höchstens 10 kW PV laden zwei PV-Autos
  deshalb nacheinander, nicht gleichzeitig: erst der Tesla (bis zu 11 kW), dann der e-Golf. Sofort
  und Boost ziehen ihren vollen Strom vom Überschuss ab, bevor die PV-Autos ihren Anteil bekommen.
- **EVCS mit Hysterese** (`control.evcs_hysteresis_a`, 0,5 A): In PV-Überschuss und Min+PV sinkt
  der Strom der EVCS sofort mit dem Anteil, steigt aber erst, wenn der Anteil den zuletzt
  angeforderten Strom um 1 A plus die Hysterese übersteigt, und dann auf den abgerundeten Anteil
  minus die Hysterese. Lädt der e-Golf mit 8 A, geht er bei einem Anteil von 9,5 A auf 9 A, bei
  7,9 A sofort auf 7 A. So folgt die EVCS nicht dem Rauschen der Hauslast (im Simulator in `sonnig`
  3 statt 77 Strombefehle in knapp einer Stunde). Was sie dabei liegen lässt, lädt der Hausakku.
  Ohne eigenen Wert (nach einem Neustart, einer Pause, einem Wert des Keepers oder dem Wechsel
  zwischen Dry-Run und live) rundet die Regelung einmal nur ab. `0` schaltet die Hysterese ab; der
  Tesla folgt ohnehin seinem gleitenden Mittel (unten, „Tesla“).

### Phasenwächter und Überlast

- Der Wächter rechnet je Phase die **Last ohne die eigenen Wallboxen und ohne die Hilfe des Akkus**:
  Netzstrom plus der Strom, den der Multi aus dem Akku liefert, minus der gemessene Strom der
  geregelten Wallboxen. So bleibt die Sicherung auch dann geschützt, wenn der Akku plötzlich nicht
  mehr liefert. Was das Ziel der Phase (Tabelle oben) darüber frei lässt, teilt er den Autos in der
  Reihenfolge `priority` zu.
- Fehlt der Messwert einer Phase, gilt sie als voll: Ein ladendes Auto bekommt höchstens seinen
  Mindeststrom, ein Start unterbleibt. Fällt eine Wallbox aus, rechnet der Wächter sie mit einem
  Zuschlag als Last (EVCS mit `safe_a`, ein Tesla ohne Strom von Wall Connector und Tessie mit
  `max_a`, jeweils auf allen Phasen).
- **Überlast**, gemessen am echten Netzstrom und in allen Modi: Liegt eine Phase 3 s lang über 33 A,
  gehen EVCS und Tesla auf den Mindeststrom; hält das weitere 3 s an, stoppt die EVCS. Liegt eine
  Phase 10 s lang über der Sicherung (35 A), stoppt alles Laden. Aufgehoben wird die Stufe, wenn
  alle Phasen 60 s lang höchstens bei 33 A liegen. Nach einem Stopp wegen Überlast gilt die
  Mindestpause. Überlast startet und weckt nichts, und sie stoppt auch ein Auto, das selbst
  gestartet hat.
- **Dringend** sind nur die Befehle wegen Überlast und Senkungen unter den Ist-Strom, wenn das Ziel
  die Sicherung ist (Sofort und Boost ohne Schutz der Reserve): Sie umgehen die Mindestabstände, die
  Stundenlimits und die Totbänder der Safety-Schicht; nur einen gleichen Wert sendet auch ein
  dringender Befehl nicht. Alle anderen Befehle sind normal, auch Senkungen am Importlimit (die
  fängt Peak Shaving ab). **Jeder erneute Versuch nach „nicht übernommen“ ist normal und nie
  dringend**, auch der einer Senkung wegen Überlast; er wartet auf die 90-s-Regel. Ein Befehl wegen
  Überlast, der neu entsteht, weil die Stufe stieg, ist kein Neuversuch und bleibt dringend,
  höchstens einmal je Stufe.
- Regel 4 der Safety-Schicht prüft jeden Befehl weiterhin unabhängig vom Wächter.

### Absicht per PUT /v1/intent

Bis zur Bedienung in Plan 2d setzt du Lademodi per `PUT /v1/intent` an die interne API des core. Den
Zugang richtest du ein wie in Abschnitt 4, „Diagnose“ (Port 8081 kurzzeitig veröffentlichen);
`$BINDAEMS_INTERNAL_TOKEN` ist der Wert aus der `.env`. Die Absicht ist **vollständig**: Sie ersetzt
die bisherige, und was fehlt, gilt als Standard. `GET /v1/intent` zeigt die gültige.

```json
{
  "version": 12,
  "control": true,
  "vehicles": {
    "tesla": {"mode": "min_pv"},
    "egolf": {"mode": "pv", "boost_until": "2026-10-14T20:00:00+02:00"}
  },
  "priority": ["tesla", "egolf"],
  "surplus_priority": {"kind": "battery_until_soc", "soc_pct": 30}
}
```

```bash
curl -X PUT -H "Authorization: Bearer $BINDAEMS_INTERNAL_TOKEN" -H "Content-Type: application/json" \
  --data @intent.json http://127.0.0.1:8081/v1/intent
```

Im Beispiel lädt der Tesla in Min+PV und der e-Golf bis 20 Uhr im Boost (wie Sofort), danach
wieder im PV-Überschuss.

| Feld | Werte | Standard |
|---|---|---|
| `version` | ganze Zahl ab 1, streng steigend | – |
| `control` | `true` schaltet den Regelbetrieb ein | `false` |
| `actuators` | je Aktor-ID (`wallbox.evcs`, `vehicle.tesla`, …) `dry_run` oder `live` | Dry-Run |
| `vehicles.<Schlüssel>.mode` | `pv`, `min_pv`, `now`, `off` | `pv` |
| `vehicles.<Schlüssel>.boost_until` | Zeitpunkt mit Zeitzone, höchstens 12 h nach dem Senden | kein Boost |
| `vehicles.<Schlüssel>.wallbox` | Schlüssel einer Wallbox; übersteuert die Zuordnung (unten) | keine Übersteuerung |
| `priority` | alle Fahrzeug-Schlüssel genau einmal, in der Reihenfolge der Zuteilung | Reihenfolge unter `vehicles` in `config.yaml` |
| `surplus_priority` | `kind`: `cars_first`, `battery_first` oder `battery_until_soc` (dann mit `soc_pct`) | `battery_until_soc`, 30 % |

- Die Schlüssel der Fahrzeuge und Wallboxen sind die aus `config.yaml` (im Beispiel `tesla`, `egolf`
  und `evcs`, `twc`).
- `surplus_priority` sagt, wer den PV-Überschuss zuerst bekommt: `cars_first` die Autos (die
  Ladeleistung des Akkus zählt zum Überschuss), `battery_first` zuerst der Akku, `battery_until_soc`
  die Autos, sobald der Akku den SOC `soc_pct` erreicht hat.
- **Version:** Sie muss über der gültigen liegen. Dieselbe Version mit gleichem Inhalt gilt als
  Wiederholung. Bis zu Plan 2d genügt eine kleine, steigende Zahl; die app nimmt später Millisekunden
  seit 1970, was größer bleibt.
- **Antwort** (`{"result": …, "version": …, "details": […]}`):
  - `accepted`: angenommen.
  - `clamped`: angenommen, aber geklemmt. `details` nennt es: „{Name}: Boost auf 12 h begrenzt, bis
    {HH:MM}.“ (Wiener Zeit) oder, dass ein Aktor ohne `live_allowed` im Dry-Run bleibt.
  - `rejected` mit Status 422, mit je einem Satz in `details`: ein unbekanntes Fahrzeug
    (`Unbekanntes Fahrzeug „x“.`), eine unbekannte Wallbox (`Unbekannte Wallbox „w“ für „v“.`), eine
    Wallbox bei mehreren Fahrzeugen (`Wallbox „w“ ist mehreren Fahrzeugen zugeordnet.`), eine
    Priorität, die nicht jedes Fahrzeug genau einmal nennt (`Die Priorität muss jedes Fahrzeug genau
    einmal nennen.`) oder ein unbekannter Aktor.
  - `rejected` mit Status 409: Die Version ist veraltet oder schon mit anderem Inhalt angenommen.
- **Boost und Übersteuerung** gelten bis zum Ende des Ansteckvorgangs der Wallbox, an der das
  Fahrzeug laut Absicht hängt: Meldet sie ein Fahrzeug, am laufenden Vorgang, sonst am nächsten.
  Danach gelten sie erst wieder mit einer neuen Version. **Eine neue Version bindet jedes Fahrzeug
  neu, das `boost_until` oder `wallbox` nennt**, auch wenn der Boost oder die Übersteuerung beim
  Abstecken schon endete. Nimm solche Einträge aus der nächsten Version, wenn sie nicht wieder
  gelten sollen. Nach einem Neustart des core springt ein Boost vom Vortag nicht beim nächsten
  Anstecken an.
- Der core speichert die gültige Absicht (`intent.json`) und nutzt sie nach einem Neustart weiter.
  Eine gespeicherte Absicht aus Plan 2b lädt mit den Standardwerten.
- **Regelbetrieb aus:** `{"version": …, "control": false}`. Der core übergibt die Aktoren und geht
  dann nach OBSERVE (Abschnitt 13).

Für den **Dry-Run** genügt `{"version": …, "control": true}` mit einer Version über der gültigen:
Alle Aktoren bleiben im Dry-Run, und alle Fahrzeuge laden im PV-Überschuss, wie heute mit evcc.

### Zuordnung Auto und Wallbox

- **Standard:** Jedes Fahrzeug hängt an seiner `default_wallbox` (Tesla am Wall Connector, e-Golf an
  der EVCS).
- **Unklar** wird die Zuordnung, wenn die Indizien 60 s lang widersprechen: Der Tesla meldet über
  Tessie „zu Hause“ und „angesteckt“, der Wall Connector meldet kein Fahrzeug, die EVCS aber eines
  (Bild „Tesla an der EVCS“). Dann pausieren die Tesla-Befehle, und die EVCS behält ihren Strom;
  nur der Phasenwächter senkt ihn. Start und Stopp nach Modus entfallen. Der Hinweis steht in
  `GET /v1/health` (`vehicles`, Feld `assignment`: `unclear`) und im Entscheidungslog (Grund
  „Zuordnung unklar“). Unbekannte Werte widersprechen nie.
- **Übersteuerung:** Steht der Tesla an der EVCS, setzt du `"wallbox": "evcs"` bei `tesla`. Das gilt
  bis zum Abstecken und hebt „unklar“ sofort auf. Der core setzt den Tesla-Strom dann einmal auf
  `vehicles.tesla.max_a` und regelt nur noch die EVCS. Der e-Golf hat dann keinen Ladepunkt.

```json
{"version": 13, "control": true, "vehicles": {"tesla": {"mode": "now", "wallbox": "evcs"}}}
```

- Die vollständige Erkennung mit Hypothesen und Zeitkorrelation (Spec 7.8) kommt mit Plan 2c-2.

### Tesla

- **Strom:** Der Tesla folgt dem Mittel des Wunschstroms über `control.tesla_average_s` (90 s) und dem
  niedrigsten freien Wert der letzten `control.tesla_caution_s` (60 s), nimmt davon den kleineren
  Wert und rundet ab. Die Safety-Schicht lässt eine Änderung ab 2 A Unterschied und höchstens alle
  90 s zu. Die schnellen Schwankungen fängt die EVCS ab.
- **Stundenlimit:** Der Tesla bekommt höchstens 20 Befehle je Stunde, davon höchstens 6 Start/Stopp
  (`vehicles.<name>.commands`: `per_hour`, `start_stop_per_hour`), gezählt in einem gleitenden
  Fenster, das einen Neustart übersteht. Unter schnellen Wolken kann das Budget aufgebraucht sein
  (im Simulator in `wolkig` nach 40 min): Weitere Befehle, auch ein Stopp oder der Befehl eines
  Moduswechsels, werden dann zurückgestellt, bis ein Platz frei wird. Das Entscheidungslog zeigt sie
  als „zurückgestellt“ (Befund `rate.per_hour` bzw. `rate.start_stop`, dieselbe Zurückstellung
  höchstens einmal je Minute). Nur dringende Befehle umgehen das Limit („Phasenwächter und
  Überlast“, oben).
- **5 A:** Über Tessie wirkt ein Befehl auf oder von 5 A erst beim zweiten Mal. Der Tesla-Aktor prüft
  den Strom deshalb im Tessie-Cache (10 bis 75 s nach der Bestätigung, Frist 85 s) und meldet sonst
  „nicht übernommen“. Die Regelung fordert denselben Wert dann erneut an (Grund `not_taken_retry`),
  als normalen Befehl, der auf die 90-s-Regel wartet. Ein Wechsel auf oder von 5 A dauert dadurch
  etwa 2 min länger.
- **Wecken** (Entscheidung des Betreibers vom 12.10.2026): Der core weckt den Tesla nur für einen
  gewollten Start. Der Start trägt eine Weckerlaubnis; Tessie weckt das Auto dabei selbst. Das
  Kontingent steht je Fahrzeug in `config.yaml` unter `wake`: mindestens `interval_s` (1800 s)
  zwischen zwei Weckstarts und höchstens `per_day` (6) je Kalendertag in Wiener Zeit. Der Dry-Run
  zählt mit, damit der Vergleich mit evcc ehrlich bleibt. Die Entscheidung trägt den Grund „Wecken“,
  `GET /v1/health` zeigt `wakes_today`. Das Auto muss laut Tessie zu Hause und angesteckt sein.
- Ohne `BINDAEMS_TESSIE_TOKEN` ist der Tesla nie erreichbar; die EVCS regelt dann allein.

### Dry-Run-Vergleich mit evcc

Im Dry-Run neben evcc stellt evcc die Ladepunkte, und BindaEMS entscheidet daneben. Jede Entscheidung
hält fest, was BindaEMS täte **und** was gerade eingestellt ist, also den Wert von evcc. So
vergleichst du zwei Wochen lang, ohne dass etwas auf die Geräte geschrieben wird.

1. Images von Plan 2c-1 ausrollen (Abschnitt 4); `live_allowed` bleibt `false`.
2. Eine Absicht senden: `{"version": …, "control": true}` (oben). Nach 30 s frischen Messwerten geht
   der core nach CONTROL, alle Aktoren bleiben im Dry-Run.
3. Entscheidungen lesen (Zugang wie in Abschnitt 13, „Entscheidungslog“). Das Beispiel listet mit
   `jq` je Entscheidung eines Aktors im Dry-Run Zeit, Aktor, Art, Ergebnis (`dry_run`, `deferred`
   für „zurückgestellt“ oder `rejected`), den Wert von BindaEMS und den eingestellten Wert von evcc:

   ```bash
   SINCE=$(date -u -d '1 day ago' +%Y-%m-%dT%H:%M:%S)%2B00:00
   curl -s -H "Authorization: Bearer $BINDAEMS_INTERNAL_TOKEN" \
     "http://127.0.0.1:8081/v1/decisions?since=$SINCE&limit=5000" \
     | jq -r '.decisions[] | select(.dry_run) | [.ts, .actuator, .kind, .result, .requested,
         (if .kind == "charging" then .inputs.actual_charging else .inputs.actual_current_a end),
         (.codes | join(","))] | @tsv'
   ```

Ein Eintrag im Rohformat (hier aus dem Simulator):

```json
{
  "id": "5affd9dcf2ab406fb896f1e5500505b5",
  "ts": "2026-06-21T08:00:30+00:00",
  "actuator": "vehicle.tesla",
  "kind": "current",
  "requested": 9.0,
  "applied": 9.0,
  "dry_run": true,
  "outcome": "accepted",
  "result": "dry_run",
  "codes": ["mode_pv", "pv_surplus"],
  "text": "Tesla Model 3 (PV-Überschuss, Überschuss): Strom 9 A – Dry-Run, nicht gesendet.",
  "inputs": {"soc_pct": 59.9, "battery_w": -3809.1, "grid_l1_a": 5.1, "grid_l2_a": 4.8,
             "grid_l3_a": -9.9, "surplus_w": 6440.9, "limit_a": 28.4,
             "actual_current_a": 5.0, "actual_charging": 1.0}
}
```

- `requested` ist der Wert, den BindaEMS einstellen würde; `inputs.actual_current_a` und
  `inputs.actual_charging` sind im Dry-Run die Werte, die evcc eingestellt hat. Im Beispiel würde
  BindaEMS dem Tesla 9 A geben, evcc hat 5 A eingestellt.
- Den **Modus** tragen die Gründe (`mode_*`, `boost`), denn `inputs` enthält nur Zahlen.
- Es erscheinen nur **Änderungen**, Ablehnungen der Safety-Schicht und Zurückstellungen wegen des
  Stundenlimits des Tesla (dieselbe Ablehnung bzw. Zurückstellung höchstens alle 60 s). Eine
  Zurückstellung ist keine Entscheidung, die BindaEMS ausführte: Sie zeigt, dass der Vergleich beim
  Tesla dort abbricht, bis das Budget wieder Platz hat (im Simulator in `dry-run` 14 Einträge je
  Stunde neben 22 Dry-Run-Entscheidungen). Die EVCS folgt dem Überschuss in ganzen Ampere mit
  Hysterese (oben, „Lademodi“): Im Simulator waren es bei ruhiger Sonne (`sonnig`) 3 Strombefehle
  in knapp einer Stunde PV-Laden, unter wechselnden Wolken (`wolkig`) rund 40 je Stunde.

| Grund (`codes`) | Text im Log | Bedeutung |
|---|---|---|
| `mode_pv`, `mode_min_pv`, `mode_now`, `mode_off` | PV-Überschuss, Min+PV, Sofort, Aus | der Lademodus des Fahrzeugs |
| `boost` | Boost | Ein Boost gilt (er steht an Stelle des Modus). |
| `pv_surplus` | Überschuss | Der Wert kommt aus dem Anteil am Überschuss, oder die Zeiten des PV-Überschusses haben einen Start oder Stopp ergeben. |
| `phase_limit` | Phasengrenze | Der Wächter kappt am Ziel der Sicherung (Sofort, Boost). |
| `import_limit` | Importlimit | Der Wächter kappt am Importlimit (PV-Überschuss, Min+PV). |
| `reserve_guard` | Schutz der USV-Reserve | Sofort oder Boost richtet sich wegen eines niedrigen SOC am Importlimit aus. |
| `overload` | Überlast | Befehl wegen Überlast (Mindeststrom oder Stopp). |
| `wake` | Wecken | Start mit Weckerlaubnis an ein schlafendes Auto. |
| `not_taken_retry` | erneut nach „nicht übernommen“ | Der Tesla hat den Strom nicht übernommen; die Regelung fordert ihn erneut an. |
| `assignment_unclear` | Zuordnung unklar | Die Zuordnung Auto–Wallbox ist unklar (oben). |
| `resume_after_outage` | Wiederaufnahme nach Ausfall | Der erste Start nach einer Pause der Regelung (SAFE, Inselbetrieb, Regelbetrieb aus). |

Nach den Gründen der Regelung stehen in `codes` die Befunde der Safety-Schicht (etwa
`urgent.bypass`, bei „zurückgestellt“ `rate.per_hour` oder `rate.start_stop`). Die **Eingaben**
(`inputs`) einer Entscheidung der Regelung sind:

| Eingabe | Bedeutung |
|---|---|
| `soc_pct`, `battery_w` | Akku-SOC in % und Akkuleistung in W (positiv beim Laden) |
| `grid_l1_a` bis `grid_l3_a` | Netzbezug je Phase in A (positiv bei Bezug) |
| `surplus_w` | der verfügbare PV-Überschuss in W (`P_verf`) |
| `limit_a` | der freie Strom, den der Phasenwächter dem Ladepunkt lässt, in A |
| `actual_current_a` | der Ist-Sollstrom des Aktors in A (im Dry-Run: der von evcc) |
| `actual_charging` | der Ist-Zustand: 1 = lädt, 0 = lädt nicht (im Dry-Run: der von evcc) |

`null` heißt unbekannt. `GET /v1/health` zeigt je Fahrzeug (`vehicles`, in der Reihenfolge von
`config.yaml`) Modus, Boost bis, Wallbox, Zuordnung, Grenze, angesteckt, lädt, Weckvorgänge heute
und den letzten Grund.

### Umstieg von evcc

Heute regelt evcc beide Ladepunkte, nicht den Victron; BindaEMS löst es ab. Die Live-Schaltung folgt
nach Plan 2d, mit dem Live-Schalter im UI (Spec 8.7), und zwar so:

1. Der Dry-Run lief 2 Wochen, und die Entscheidungen sind gemeinsam gesichtet (Spec 18).
2. Das Prüfprotokoll Teil 2 ist für EVCS und Tesla bestanden, bei der EVCS einschließlich des
   Schritts „Verbindung unterbrochen“, und der Watchdog ist scharf (Abschnitt 14).
3. `live_allowed: true` steht für `wallboxes.evcs` und `vehicles.tesla` (Abschnitt 13).
4. **Beide Ladepunkte gehen gemeinsam live, und evcc wird ganz abgeschaltet,** nicht nur einer.
   Beide Autos teilen sich Überschuss und Phasenwächter; ein Regler für das eine Auto und evcc für
   das andere würden um denselben Überschuss konkurrieren. Schreibt evcc nach der Live-Schaltung
   weiter, erkennt der core die fremde Änderung und sperrt den Aktor nach 3 Prüfungen im Abstand von
   10 s (Abschnitt 13, „Sperren“).
5. Victron bleibt im Dry-Run, bis Plan 2c-2 da ist und Dynamic ESS aus ist.

Zurück zu evcc: beide Aktoren in der Absicht auf Dry-Run stellen, die Übergabe abwarten (Abschnitt
13) und erst dann evcc wieder starten.

### Hinweise für die Anlage

- **Ein schlafender Tesla, den der core gerade übergibt,** hält die Übergabe offen, bis er aufwacht.
  Solange kann der Alarm „… Sicherwert für … nicht bestätigt.“ (`actuator.failed.vehicle.tesla`)
  anstehen (Abschnitt 14, „Meldungen des core“).
- **Schnell wechselnde Wolken:** Dann deckt der Hausakku einen spürbaren Teil der PV-Ladung des
  Tesla. Der Tesla darf seinen Strom nur alle 90 s ändern, und ein Wechsel auf oder von 5 A braucht
  zwei Befehle; das nimmt das Design in Kauf (Design 6.5). Im Simulator (`wolkig`, Wolken im Wechsel
  alle 30 bis 180 s) kamen so rund 40 % der Ladeenergie des Tesla aus dem Akku. Das ist der rohe
  Anteil: Die Kennzahl „Akku-Energie in PV-Autos“ nimmt die Übergänge aus und zeigt dort 0 %, denn
  jede dieser Entladungen fällt in einen Übergang. Den rohen Anteil aller PV-Autos (dort 18 %)
  nennt der Bericht als Informationszahl (Abschnitt „Simulator“). Der e-Golf folgt den Wolken
  binnen Sekunden.

**Vor der Live-Schaltung von EVCS und Tesla (nach 2d)** sind diese Punkte zu entscheiden oder
umzusetzen, je mit Befund und möglicher Abhilfe (ausführlicher in `docs/uebergabe.md`):

1. **Stundenbudget des Tesla:** Unter Wolken verbraucht die Regelung die 20 Befehle je Stunde;
   danach werden auch ein Stopp und ein Moduswechsel zurückgestellt (im Log „zurückgestellt“); in
   einer Probe im Simulator wirkte „Aus“ erst nach 19 min. Abhilfe: ein Budget in der Regelung
   (Plätze für Start/Stopp freihalten, Stromänderungen in PV sparen) und/oder den Stopp nach einem
   Moduswechsel dringend machen.
2. **Selbststart im laufenden Vorgang:** Startet das Auto selbst (etwa aus der Tesla-App),
   korrigiert die Regelung das nicht, denn sie sendet nur Änderungen; „Aus“ hält es nicht gestoppt,
   PV stoppt es nicht, nur die Überlast-Stufen greifen. Abhilfe: „Laden aus“ erneut senden, wenn das
   Auto länger als eine Frist gegen den Modus lädt, im Start/Stopp-Budget.
3. **Ansteckerkennung des Tesla:** Tessie fragt bei abgestecktem Auto nur alle 600 s ab; ein Tesla,
   der beim Anstecken selbst zu laden beginnt, lädt bis zu 10 min ungeregelt. Abhilfe im Adapter:
   schneller abfragen, solange das Auto zu Hause oder online ist oder der Wall Connector ein neues
   Fahrzeug meldet.
4. **Ausnahme in der Regelung:** Wirft die Regelung in jedem Zyklus, laufen auch Keeper, Stream und
   Alarme nicht. Abhilfe: die Ausnahme fangen, die Regelung ruhen lassen, Alarm, Heartbeat
   zurückhalten, den Keeper weiter ausführen.
5. **5 A beim Tesla:** Befehle auf oder von 5 A gehen über Tessie beim ersten Mal verloren und
   kosten einen zweiten Befehl. Abhilfe: `vehicles.tesla.min_a: 6` (Design 7.2 legt 5 A fest;
   Entscheidung des Betreibers).
6. **Simulator:** Kein PV-Szenario lässt das Importziel binden; „Längste Zeit über dem Ziel in
   PV-Modi“ prüft darum wenig. Abhilfe: ein PV-Szenario mit schwerer Last auf einer Phase.
7. **Technische Schulden vor 2c-2:** `core/control/charging.py` und `core/runtime.py` sind groß, und
   einige Hilfsfunktionen gibt es doppelt. Abhilfe: aufteilen und zusammenführen, bevor 2c-2
   dazukommt.
8. **Sicherer Zustand bei Netzausfall:** Der core sendet dem Tesla „Laden aus“ alle 10 s erneut, bis
   der Tessie-Cache es zeigt; beim Laden dauert das bis zu 30 s und kostet bis zu 3 der 6
   Start/Stopp-Befehle je Stunde und alle 3 Versuche des Vorfalls. Abhilfe: die Zahl der Versuche,
   ihren Abstand oder `min_a` prüfen (Spec 8.5).

**Im Dry-Run an der Anlage prüfen:**

- welcher EVCS-Status bei angestecktem Auto auf „Laden aus“ (`StartStop` = 0) folgt: Ist es 3
  („geladen“), bliebe ein Auto in PV nach dem ersten Stopp stehen, bis es abgesteckt wird;
- was Tessie für `charger_phases` meldet;
- ob „unklar“ fälschlich erscheint, wenn der Tesla den Wall Connector verlässt, während der e-Golf
  an der EVCS hängt (Tessie meldet `plugged` bis zu 60 s veraltet);
- wie oft die EVCS ihren Strom ändert: Folgt sie bei ruhiger Sonne trotz der Hysterese noch dem
  Rauschen (deutlich mehr Strom-Entscheidungen an `wallbox.evcs` als im Simulator, etwa über 10 je
  Stunde), hebe `control.evcs_hysteresis_a` an (oben, „Lademodi“).

## Simulator

Der Anlagensimulator `bindaems.tools.sim` lässt die echte Laufzeit des core mit ihren echten Aktoren
gegen ein Modell der Anlage laufen, in simulierter Zeit (ein Zyklus je Sekunde). Das Modell hat ein
Netz mit Sicherung, PV, Hauslast, den Akku mit ESS und Peak Shaving, die EVCS mit dem e-Golf, den Wall
Connector mit dem Tesla und Tessie, Ausfälle und einen fremden Regler wie evcc. Gefälscht sind nur die
Geräte: Parser der Adapter, Aktoren, Safety-Schicht und Laderegelung sind die echten, und das Modell
zeigt sich so, wie die Geräte und Adapter es tun. Der Simulator verbindet sich mit nichts und schreibt
nichts auf die Geräte; er braucht weder Tokens noch Netz. Die Szenarien sind deterministisch.

In der CI läuft jedes der zehn Szenarien bei jedem Push (`tests/integration/test_sim_scenarios.py`).
Von Hand lässt du eines laufen, etwa um nach einer Änderung von `grid` oder `control` in `config.yaml`
zu sehen, ob die Regelung noch hält.

### Aufruf

Im Checkout, mit der Beispielkonfiguration `deploy/config.example.yaml`, aus dem Wurzelverzeichnis
des Checkouts (der Standardpfad der Konfiguration ist relativ; anderswo endet der Aufruf mit
„Datei nicht gefunden“ und Rückgabe 2):

```bash
uv run python -m bindaems.tools.sim --szenario wolkig --out bericht.md
```

Auf der VM im Image des core. Dort gibt es `deploy/config.example.yaml` nicht, du gibst deshalb deine
`config.yaml` an:

```bash
cd /opt/bindaems
mkdir -p verification && sudo chown 10001 verification   # der Container läuft als UID 10001
docker compose run --rm -v "$PWD/verification:/out" --entrypoint python ems-core \
  -m bindaems.tools.sim --szenario wolkig --config /config/config.yaml \
  --out /out/<Datum>-simulation-wolkig.md
```

- `--szenario NAME` (Pflicht): eines der zehn Szenarien unten.
- `--out PFAD` (Pflicht): der Bericht als Markdown. Fehlende Ordner legt das Werkzeug an.
- `--config PFAD`: die Konfiguration, aus der Grenzen, Fahrzeuge und Wallboxen kommen; ohne Angabe
  `deploy/config.example.yaml`, relativ zum aktuellen Verzeichnis. Die Szenarien brauchen die
  Wallboxen `evcs` (EVCS) und `twc` (Wall Connector) und die Fahrzeuge `tesla` (mit Tessie) und
  `egolf`.
- `--stunden H`: Dauer in Stunden statt der des Szenarios, **höchstens diese**; ein größerer Wert
  lässt das Szenario ganz laufen, denn seine Ereignisse und Prüfungen enden mit seiner Dauer. Ein
  kürzerer Lauf ist nur ein Ausschnitt: Prüfungen des Szenarios, die das Ende oder eine Dauer
  prüfen, können dann scheitern, ohne dass die Regelung fehlt (etwa `sonnig --stunden 0.05`:
  „„tesla“ erreicht „Complete“ nicht.“, Rückgabe 1).
- Jeder Lauf bekommt ein **frisches temporäres Verzeichnis** für die Ablage des core (Absicht,
  Rate-Limits, Entscheidungen). Er berührt `/data` nie und sieht nichts von früheren Läufen.
- Ein Szenario braucht für 1 bis 2 h simulierte Zeit etwa 5 bis 20 s Rechenzeit.

Die Standardausgabe zeigt je Kennzahl eine Zeile, dann je Informationszahl eine (ohne Grenze), dann
je Prüfung des Szenarios eine und am Ende den Pfad des Berichts. Ein Ausschnitt aus `tesla-5a`:

```text
Höchster Phasenstrom: 0,28 A (Grenze 35 A) – OK
…
Wiederholte Dry-Run-Entscheidungen: 0 (Grenze 0) – OK
Akku-Energie in PV-Autos, roh (mit Übergängen): 46,47 % (zur Information)
Zurückstellungen wegen des Stundenlimits an „tesla“: 0 (zur Information)
Szenario-Prüfung: OK – Eine Entscheidung nennt „not_taken_retry“.
Szenario-Prüfung: OK – „tesla“ lädt am Ende mit 5 A.
Bericht: bericht.md
```

Schlägt etwas fehl, steht statt OK `FEHLER`, bei einer Prüfung des Szenarios mit „Befund: …“.

**Rückgabewert:**
- **0**, wenn alle Kennzahlen und Prüfungen bestehen.
- **1**, wenn eine Kennzahl oder eine Prüfung des Szenarios fehlschlägt.
- **2** bei einem unbekannten Szenario, einer fehlenden oder ungültigen Konfiguration, einer
  Konfiguration ohne die Geräte der Szenarien (siehe `--config`), einem ungültigen `--stunden` oder
  einem Bericht, der sich nicht schreiben lässt.

### Bericht

Der Bericht ist eine Markdown-Datei:

- Überschrift mit dem Titel des Szenarios und eine Tabelle „Angabe | Wert“ mit Szenario, simulierter
  Zeit (Wiener Zeit) und Konfiguration.
- **Kennzahlen:** eine Tabelle „Kennzahl | Wert | Grenze | Ergebnis“, das Ergebnis ist OK oder FEHLER.
- **Informationszahlen:** eine Tabelle „Zahl | Wert“ mit Zahlen ohne Grenze, die die Kennzahlen
  einordnen (unten, „Kennzahlen“).
- **Szenario-Prüfungen:** eine Tabelle „Ergebnis | Prüfung | Befund“ mit einer Zeile je Prüfung. Der
  Befund nennt bei FEHLER die Verstöße, sonst „–“.
- **Entscheidungen:** die Zahl der Entscheidungen des core je Aktor, Art und Ergebnis, danach je
  Grund (die Gründe aus dem Abschnitt „Laderegelung“).

### Kennzahlen

Jedes Szenario misst dieselben Kennzahlen. Die Grenzen kommen aus der Konfiguration; die Zahlen unten
gelten für die Beispielkonfiguration.

| Kennzahl | Grenze | Gemessen |
|---|---|---|
| Höchster Phasenstrom | ≤ 35 A (`grid.fuse_a`) | der größte Netzstrom einer Phase |
| Längste Zeit über 33 A | ≤ 10 s | die längste Folge von Sekunden mit einer Phase über `fuse_a − fuse_margin_a` |
| Längste Zeit über dem Ziel in PV-Modi | ≤ 10 s | die längste Folge von Sekunden, in der auf einer Phase die Last ohne Akku-Hilfe (Netzstrom plus der Strom, den der Multi auf ihr aus dem Akku liefert) über dem Ziel am Importlimit (19 A) liegt, mit 0,5 A Toleranz. Es zählen nur Sekunden, in denen alle ladenden Autos im PV-Überschuss oder in Min+PV stehen, die Regelung ihre Ladepunkte live steuert und keines mit weniger als Mindeststrom + 0,5 A lädt (Stopp-Minuten, Mindeststrom von Min+PV). |
| … Tesla lädt | ≤ 200 s | dieselbe Messung für Folgen, in denen der Tesla lädt. Ein Wechsel auf oder von 5 A braucht zwei Strombefehle im Abstand von 90 s, dazu Antwort und Prüfung im Cache: `2 × commands.current_interval_s + 20`. |
| Akku-Energie in PV-Autos | ≤ 5 % | der Anteil der Akku-Energie an der Energie der PV-Autos, in denselben Sekunden. **Übergänge zählen nicht:** die ersten 10 s einer ununterbrochenen Folge mit Entladung, lädt der Tesla, die ersten 200 s. |
| Starts je Auto und Stunde | ≤ 4 | Wechsel von „lädt nicht“ zu „lädt“ im Modell, in einem gleitenden Fenster von einer Stunde |
| Kürzester Abstand der Tesla-Strombefehle | ≥ 90 s | der Abstand am Fahrzeug (Zeitpunkt des Sendens) zwischen Entscheidungen über den Tesla-Strom mit Ergebnis ok oder Dry-Run, ohne dringende Befehle; „–“, wenn weniger als zwei zählen |
| Weckvorgänge je Tag | ≤ `wake.per_day` | Entscheidungen mit dem Grund „Wecken“ und einem Wert, je Kalendertag in Wiener Zeit |
| Kürzester Abstand der Weckvorgänge | ≥ `wake.interval_s` | der Abstand zweier solcher Entscheidungen |
| Wiederholte Dry-Run-Entscheidungen | 0 | Dry-Run-Entscheidungen, deren Wert dem vorigen eigenen Wert der Regelung für Aktor und Art gleicht |

- Die beiden PV-Kennzahlen, „Längste Zeit über dem Ziel in PV-Modi“ (beide Zeilen) und „Akku-Energie
  in PV-Autos“, zählen nur Autos, deren Ladepunkt die Regelung **live** steuert. Im Dry-Run stellt
  ein anderer Regler die Ladepunkte, und gemessen werden soll die Regelung.
- **Informationszahlen** ohne Grenze stehen unter der Tabelle der Kennzahlen:
  - „Akku-Energie in PV-Autos, roh (mit Übergängen)“: derselbe Anteil in denselben Sekunden, aber
    ohne die Ausnahme der Übergänge. Eine Kennzahl von 0 % kann ganz aus Übergängen bestehen: In
    `wolkig` ist der rohe Anteil 18 %, in `tesla-5a` 46 % (dort zählen nur die Sekunden mit 10 A vor
    dem Wechsel auf 5 A; am Mindeststrom zählt keine Sekunde).
  - „Zurückstellungen wegen des Stundenlimits an …“, je Fahrzeug mit Tessie (in der
    Beispielkonfiguration `tesla`): die Entscheidungen „zurückgestellt“ (Abschnitt 13,
    „Entscheidungslog“), also höchstens eine je Minute, Art und Befund. In `wolkig` sind es 12, in
    `dry-run` 14.
- Als „eigener Wert“ der Regelung zählt auch ein Wert, den die Safety-Schicht mit „unverändert“
  beantwortete. Vergisst die Regelung ihre Werte (Pause, Wechsel der Betriebsart), beginnt die
  Zählung neu.
- Die Lastsprünge der Szenarien sind so bemessen, dass ein korrekt regelnder core 35 A halten kann.

### Szenarien

Alle Szenarien starten mit angesteckten, nicht ladenden Autos und einem wachen Tesla, soweit die
Tabelle nichts anderes sagt. Der Regelbetrieb ist an, die Aktoren der Regelung sind live (außer in
`dry-run`).

| Name | Titel, Dauer | Anlage und Ereignisse | Prüfung des Szenarios |
|---|---|---|---|
| `sonnig` | Sonniger Tag, beide Autos im PV-Modus, 2 h | 21.06.2026 ab 10:00, PV bis 9 kW, Akku 60 %, Tesla nahe an seinem Ladelimit | Erst lädt der Tesla bis „Complete“ (mindestens 45 min), danach der e-Golf (mindestens 45 min); höchstens 10 Strombefehle an die EVCS je Stunde, in der der e-Golf lädt (Hysterese, Abschnitt „Laderegelung“). |
| `wolkig` | Wolkiger Tag, beide Autos im PV-Modus, 2 h | wie `sonnig`, Sonne und Wolke im Wechsel alle 30 bis 180 s | Erst lädt der Tesla bis „Complete“, danach der e-Golf, je mindestens 20 min. |
| `abend-sofort` | Abends beide Autos auf Sofort, mit Lastspitze, 1 h | 12.10.2026 ab 18:00, keine PV, Akku 60 %, L1 +2,5 kW von 20 bis 25 min | Der Strom der EVCS liegt während der Spitze unter dem davor. |
| `reserve` | Sofort knapp über der USV-Reserve, 1 h | Akku 23 %, dauerhaft 1 kW mehr Last auf L1, nur der e-Golf | Eine Entscheidung nennt „reserve_guard“; unter der Reserve-Absicherung (SOC unter 25 %) liegt keine Phase länger als 10 s mit der Last ohne Akku-Hilfe über dem Importziel (19 A, mit 0,5 A Toleranz), solange ein Auto live über seinem Mindeststrom lädt; der SOC sinkt nie unter 20 % (eine Invariante des Modells, das am Netz nie unter die Reserve entlädt: Sie prüft das Modell, nicht die Regelung). |
| `nur-laden` | Multi auf „Nur Laden“ mit Sofort, 1 h | L1 +3 kW als Rampe von 20 bis 40 min | Die Selbstprüfung `multi_mode` warnt. |
| `tesla-5a` | Tesla auf 5 A mit verlorenem erstem Befehl, 1 h | PV konstant 4,6 kW, Tesla angesteckt mit 10 A, e-Golf abgesteckt | Eine Entscheidung nennt „not_taken_retry“; der Tesla lädt am Ende mit 5 A. |
| `tesla-schlaeft` | Schlafender Tesla im PV-Modus, 1 h | 21.06.2026 ab 09:00, PV steigt in 30 min auf 6 kW | Genau ein Weckvorgang; der Tesla lädt am Ende. |
| `ausfaelle` | Ausfälle einzeln, Netzausfall mit Wiederaufnahme, 1,5 h | beide Sofort; Ausfälle: EVCS 10–15 min, Wall Connector 20–25, Tessie 30–35, Cerbo 40–41, Netz 50–53 | SAFE in den Fenstern von Cerbo und Netz; ab 60 min laden beide; eine Entscheidung nennt „resume_after_outage“. |
| `zuordnung` | Zuordnung unklar und Übersteuerung, 1 h | Tesla an der EVCS; Übersteuerung `tesla: evcs` (Version 2) bei 10 min; Abstecken bei 40 min | Die Gesundheit zeigt „unklar“ vor 10 min; genau eine Entscheidung „Strom 16 A“ an den Tesla; ab 41 min lädt die EVCS nicht. |
| `dry-run` | Dry-Run neben einem fremden Regler, 1 h | wie `wolkig`, fremder Regler an, alle Aktoren im Dry-Run | keine wiederholte Dry-Run-Entscheidung; mindestens eine Dry-Run-Entscheidung an EVCS und Tesla; kein Alarm „competitor.foreign.*“ |

Die Verteilung ist gierig nach Priorität (Abschnitt „Laderegelung“): Mit höchstens 10 kW PV laden die
Autos in `sonnig` und `wolkig` nacheinander, darum steht der Tesla dort nahe an seinem Ladelimit.

### Was das Modell nicht leistet

Der Simulator zeigt, ob die Regelung bei bestimmten Lasten, Wolken und Ausfällen die Grenzen hält. Die
Zeiten von Tessie (Antwort nach 0,2 bis 2,5 s, Abfragen alle 30, 60 oder 600 s), der verlorene erste
5-A-Befehl und die Ladekurven der Autos sind Annahmen aus dem Prüfprotokoll Teil 2 und der Spec. Ein
Tessie-Ausfall trennt wie beim echten Adapter die Quelle nicht: Die Werte veralten, Befehle scheitern
mit HTTP 503, und die Abfragen laufen im Backoff des Adapters (60 s, verdoppelt bis 900 s). Ein grüner
Simulator ersetzt weder das Prüfprotokoll Teil 2 noch den Dry-Run an der Anlage.

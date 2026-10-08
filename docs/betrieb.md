# Betrieb von BindaEMS

Stand: Phase 1a. Es läuft nur der Dienst `ems-core` im Modus **OBSERVE**: Er liest Cerbo GX,
EVCS, Wall Connector, Tessie und Home Assistant, schreibt Telemetrie nach InfluxDB und steuert
**nichts**. Die App mit Weboberfläche folgt in Phase 1b.

## 1. Voraussetzungen der VM

- Debian (aktuelle stabile Version) mit Docker Engine und Compose-Plugin.
- Richtwert: 2 vCPU, 4 GB RAM, 20 GB Platte.
- **NTP muss aktiv sein**, denn die Viertelstunden-Slots hängen an der Uhrzeit.
  Prüfen mit `timedatectl` (Zeile `System clock synchronized: yes`).
- Netzzugang:
  - im LAN: Cerbo GX (MQTT 8883), EVCS (Modbus TCP 502), Wall Connector (HTTP 80),
    InfluxDB (8086), Home Assistant (8123)
  - ins Internet: Tessie (`api.tessie.com`) und GHCR (`ghcr.io`) für Images

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
  - MQTT-Passwort, Tessie-Token, HA-Token und InfluxDB-Passwort trägst du nur ein, wenn sie
    genutzt werden. Leere Einträge gelten als nicht gesetzt.
  - Ohne Tessie-Token wird der Tesla nicht gelesen. Das Log meldet das.
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
aus `.env`. Der Lesezugriff auf die HA-Datenbank wird ab Phase 1b für die Lernmodelle gebraucht.

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
| Logs (JSON) | `docker compose logs -f ems-core` |
| Update | `docker compose pull && docker compose up -d` |
| Rollback | in `.env` `BINDAEMS_VERSION=<tag>` setzen, dann `docker compose up -d` |
| Stoppen | `docker compose down` (das Volume `core-data` bleibt erhalten) |

- **Tags:**
  - `latest` ist der Stand von `main`.
  - Zusätzlich gibt es je Commit `sha-<kurz>` und je Release `vX.Y.Z`.
- **Geordnetes Herunterfahren:**
  - `docker compose stop` sendet SIGTERM. Der core schreibt dann die laufende Viertelstunde
    und die Warteschlange und beendet sich mit Code 0.
- **Logs:**
  - JSON auf stdout. Docker rotiert sie (5 × 10 MB).
- **Healthcheck:**
  - Er fragt alle 30 s `/v1/health` der internen API ab.
  - Wurde `core_api.port` in `config.yaml` geändert, muss `BINDAEMS_CORE_PORT` in `.env`
    denselben Wert haben.
- **Diagnose:**
  - Die interne API ist absichtlich nicht nach außen veröffentlicht.
  - Zur Fehlersuche kannst du in `docker-compose.yml` die Zeile `127.0.0.1:8081:8081`
    kurzzeitig einkommentieren. Danach fragst du so ab:
    `curl -H "Authorization: Bearer $BINDAEMS_INTERNAL_TOKEN" http://127.0.0.1:8081/v1/health`.
- **Lokaler Build:**
  - Statt GHCR geht das auch mit einem Checkout des Repositorys.
  - Im Ordner `deploy/` mit `docker compose build` bauen, dann `docker compose up -d`.

## 5. Proxmox-HA

- Die VM ist eine HA-Ressource mit ZFS-Replikation alle 1–5 Minuten.
- **Bei einem Failover** startet die VM auf dem anderen Knoten aus der letzten Replikation.
  Telemetrie seit dieser Replikation, also Spool und laufende Viertelstunde, kann fehlen.
  Sie steht dann in InfluxDB, sofern InfluxDB erreichbar war.
- Der core startet immer im Modus OBSERVE. Ab Phase 2 schützt der Watchdog auf dem Cerbo die
  Victron-Steuerung während eines Failovers.
- Nach einem Failover prüfst du die Uhrzeit (`timedatectl`) und `docker compose ps`.

## 6. Prüfprotokoll Teil 1

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

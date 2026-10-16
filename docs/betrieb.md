# Betrieb von BindaEMS

Stand: Phase 2d (Bedienung in UI und HA). Auf der VM laufen zwei Dienste. Der core **regelt** seit Plan
2c-1 die beiden Ladepunkte (EVCS und Tesla), seit Plan 2c-2a den Akku (Override an Victron) und die
Spitzen (15-min-Wächter) und **erkennt** seit Plan 2c-2b selbst, welches Auto an welcher Wallbox
steht, auch ein Gast. Er **sendet aber nichts**, solange ihre Aktoren im Dry-Run sind: Er
protokolliert dann nur, was er täte, und evcc regelt weiter (Abschnitte „Laderegelung“ und „Akku und
Spitzen“ am Ende). Seit Plan 2d bedienst du den Regelbetrieb, die Lademodi, den Boost und die
Zuordnung im UI (Seiten „Laden“ und „System“) und in Home Assistant; die Entscheidungen stehen im UI
auf der Seite „Entscheidungen“ und in Grafana.

- `ems-core` liest Cerbo GX, EVCS, Wall Connector, Tessie und Home Assistant und schreibt
  Telemetrie nach InfluxDB. Er kennt Betriebszustände, Freigaben und Sperren (Abschnitt 13), hat
  Aktoren für Victron, EVCS und Tesla und regelt die beiden Ladepunkte, den Akku und die Spitzen.
  Ohne Absicht der Bedienung bleibt er im Zustand **OBSERVE**: nur lesend. Auf ein Gerät schreibt er
  nur für einen live geschalteten Aktor: den Heartbeat an den eigenen Watchdog (Abschnitt 14),
  solange er regelt, die Sicherwerte im sicheren Zustand und bei der Übergabe am Ende des
  Live-Betriebs (Abschnitt 13) und die Befehle der Regelung an EVCS, Tesla und Victron. Live ist
  bisher kein Aktor: EVCS und Tesla gehen mit dem Live-Schalter im UI gemeinsam live (Abschnitt
  „Laderegelung“, „Umstieg von evcc“), Victron nach den Schritten im
  Abschnitt „Akku und Spitzen“. Das Entscheidungslog hält fest, was er an den Aktoren tut: gesendete
  Befehle, im Dry-Run die Urteile mit einem Wert und die Ablehnungen der Safety-Schicht
  (Abschnitt 13).
- `ems-app` meldet Benutzer an und liefert das Web-UI samt API aus (Abschnitt 12). Sie holt
  Preise und die PV-Prognose, rechnet jede Viertelstunde ab und sendet seit Plan 2d die Absicht an
  den core: Regelbetrieb, Lademodi, Boost, Zuordnung und der Live-Schalter (Abschnitt 13). Sie
  speichert das Entscheidungslog, meldet Sensoren und Bedienelemente per MQTT Discovery an Home
  Assistant und meldet Alarme dort als Benachrichtigung (Abschnitt 7).

Dazu kommt der **Watchdog** auf dem Cerbo, den du einmal installierst (Abschnitt 14). Er ist der
Totmann-Schalter der Anlage und greift nur ein, wenn ein live geschalteter core ausfällt.

Am Ende des Handbuchs stehen die Abschnitte „Laderegelung“ (Lademodi, Absicht, Zuordnung Auto und
Wallbox, Dry-Run-Vergleich mit evcc, Umstieg, Hinweise für die Anlage), „Akku und Spitzen“
(Akku-Sollwert, 15-min-Wächter, Health, Victron in den Dry-Run und live, Alarme) und „Simulator“ (die
Szenarien der CI von Hand laufen lassen).

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
    Schreibvorgänge, ob sie Werte des EMS halten), Watchdog, je Fahrzeug den Stand der
    Laderegelung (`vehicles`) und je Wallbox, wer dort steht (`wallboxes`; beide Abschnitt
    „Laderegelung“, „Zuordnung Auto und Wallbox“) sowie den Akku-Sollwert (`battery`) und den
    15-min-Wächter (`peak`, Abschnitt „Akku und Spitzen“), `/v1/intent` die gültige Absicht und
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
(`homeassistant.mqtt` in `config.yaml`). Sie liest `homeassistant/status` und seit Plan 2d die
Befehle der Bedienelemente unter `bindaems/cmd/`. Lege dafür einen eigenen Benutzer `bindaems` an;
sein Passwort steht in `BINDAEMS_HA_MQTT_PASSWORD`. Die ACL beschränkt ihn auf das Nötige (beim
Mosquitto-Add-on über `customize` und eine ACL-Datei unter `/share/mosquitto`). Die Bedienelemente
brauchen dafür drei Zeilen mehr als die Sensoren: Schreibrechte für `select`, `switch` und `button`
und das Leserecht auf `bindaems/cmd/#`:

```text
user bindaems
topic write homeassistant/sensor/bindaems/#
topic write homeassistant/binary_sensor/bindaems/#
topic write homeassistant/select/bindaems/#
topic write homeassistant/switch/bindaems/#
topic write homeassistant/button/bindaems/#
topic write bindaems/#
topic read homeassistant/status
topic read bindaems/cmd/#
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
| Zuordnung unklar | Problem | eine Wallbox oder ein Fahrzeug zeigt `unclear` (Abschnitt „Laderegelung“, „Zuordnung Auto und Wallbox“) |
| SOC (Gerät „BindaEMS <Fahrzeug>“) | % | je Fahrzeug |
| Wallbox (Gerät „BindaEMS <Fahrzeug>“) | – | je Fahrzeug der Name der Wallbox, an der es steht (eigener Name oder „EVCS“ / „Wall Connector“), sonst „keine“ |
| Ladeleistung (Gerät „BindaEMS EVCS“ / „BindaEMS Wall Connector“) | W | je Wallbox |
| Fahrzeug (Gerät der Wallbox) | – | je Wallbox der Name des Fahrzeugs, „Gast“ oder „keins“ |

Fährt die app geordnet herunter, meldet sie sich selbst ab; bei einem Absturz oder
Verbindungsabbruch setzt der Broker alle Entitäten über den Last Will auf „nicht verfügbar“.

### Bedienelemente und Befehle

Seit Plan 2d gibt es Bedienelemente. Home Assistant hat dabei die Rechte der Rolle „Bedienen“
(Abschnitt 12), aber keinen Benutzer in der app: Du legst dafür nichts an. Im Änderungsprotokoll
steht die Quelle „ha“.

| Entität | Plattform | Wirkung |
|---|---|---|
| Lademodus (je Fahrzeug) | Auswahl | PV-Überschuss, Min+PV, Sofort oder Aus; „Boost“ gibt es hier nicht |
| Boost (je Fahrzeug) | Taste | Boost bis zum Abstecken, höchstens 12 h |
| Boost beenden (je Fahrzeug) | Taste | beendet den Boost |
| Zuordnung (je Wallbox) | Auswahl | „Automatisch“ oder der Name eines Fahrzeugs; „Automatisch“ hebt jede Übersteuerung dieser Wallbox auf (Abschnitt „Laderegelung“, „Zuordnung Auto und Wallbox“) |
| Regelbetrieb | Schalter | schaltet den Regelbetrieb ein oder aus |
| Gast-Lademodus | Auswahl | der Modus für einen Gast an jeder Wallbox |

Die Bedienelemente stehen am Gerät des Fahrzeugs bzw. der Wallbox, Regelbetrieb und Gast-Lademodus am
Gerät „BindaEMS“. Live-Schaltung und Vorrang gibt es in Home Assistant nicht; sie bleiben im UI.

- **Befehle** kommen über `bindaems/cmd/<Objekt>`. Eine gehaltene (retained) Nachricht auf diesen
  Topics ignoriert die app: Sie käme sonst bei jeder Verbindung wieder.
- **Höchstens ein Befehl je Sekunde und Entität** wirkt; was dazwischen kommt, wird verworfen. So
  richtet eine Automation mit Dauerfeuer nichts an.
- **Nach jedem Befehl veröffentlicht die app den Zustand der Entität neu**, auch nach einem
  verworfenen oder abgelehnten Befehl. Home Assistant zeigt so immer, was gilt.
- **Ablehnungen:** Ein ungültiger Wert (eine Option, die es nicht gibt) oder ein Befehl, den der core
  ablehnt oder der nicht ankommt, ändert nichts. Home Assistant zeigt dann die persistente Meldung
  `bindaems_ha_command` mit „Befehl aus Home Assistant abgelehnt: …“; eine neue Ablehnung ersetzt die
  alte. Ist der core nicht erreichbar, lautet der Grund „core nicht erreichbar.“.
- Die Auswahl „Zuordnung“ zeigt dieselbe Übersteuerung wie das UI: Eine beendete (etwa nach dem
  Abstecken) zeigt sie als „Automatisch“, eine wartende mit dem Namen des Fahrzeugs.
- Die Namen in der Auswahl „Zuordnung“ sind die Namen der Fahrzeuge aus `config.yaml`. Die
  Konfiguration lehnt deshalb einen leeren Namen, einen mit Leerzeichen am Rand, einen doppelten (Groß-
  und Kleinschreibung zählt nicht) und den Namen „Automatisch“ ab. Sie lehnt auch Schlüssel von
  Fahrzeugen und Wallboxen ab, deren Objekt-ID in Home Assistant mit einer anderen Entität
  zusammenfiele. Nach dem Ausrollen von Plan 2d prüfst du deine `config.yaml` darauf: Der core und die
  app melden den Grund beim Start.

### Benachrichtigungen

Die app meldet Alarme und abgelehnte Befehle in Home Assistant. Dafür braucht sie dort einen Token
(`BINDAEMS_HA_TOKEN` in der `.env`, derselbe wie für den core) und den Abschnitt `homeassistant` mit
`url`. Die Meldung läuft über die WebSocket-API von Home Assistant. Der core ruft weiter nie Dienste in
Home Assistant auf.

```yaml
homeassistant:
  url: http://homeassistant.lan:8123
  notify_service: notify.mobile_app_handy   # optional
```

- Jeder **Alarm mit der Schwere „Fehler“**, der 30 s ohne Unterbrechung besteht, wird einmal gemeldet:
  als persistente Meldung mit der Kennung `bindaems_<Alarm-ID>` (die Punkte der ID werden zu `_`, etwa
  `bindaems_competitor_foreign_wallbox_evcs`) und dem Titel „BindaEMS“, und, wenn `notify_service`
  (`notify.*`) eingestellt ist, zusätzlich über diesen Dienst, etwa als Push auf das Handy. Verschwindet
  der Alarm, entfernt die app die persistente Meldung wieder.
- **Abgelehnte Befehle** aus Home Assistant erscheinen als persistente Meldung `bindaems_ha_command`
  (oben).
- Höchstens **10 Meldungen je Minute** über alle Alarme und Ablehnungen zusammen, davon höchstens
  **4 für Ablehnungen**: Den Alarmen bleiben so immer mindestens 6, auch wenn eine Automation eine Flut
  ungültiger Befehle sendet. Eine Ablehnung über dem Budget steht nur im Log der app.
- Ein fehlgeschlagener Aufruf stört nichts; er steht im Log (`Dienstaufruf an Home Assistant
  fehlgeschlagen`). Ein Alarm gilt erst als gemeldet, wenn die persistente Meldung angelegt ist: Über
  dem Budget oder während Home Assistant neu startet versucht es die app erneut, solange der Alarm
  besteht, je Alarm höchstens einmal je Minute. Der `notify`-Dienst folgt erst auf die angelegte
  Meldung, einmal. Auch das Entfernen wiederholt die app so, bis es gelingt.
- **Ohne `BINDAEMS_HA_TOKEN`** (oder ohne den Abschnitt `homeassistant`) sendet die app nichts. Die
  Komponente `ha-notify` unter **System** zeigt dann „Benachrichtigungen aus: BINDAEMS_HA_TOKEN
  fehlt.“.

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

- Dynamic ESS nullt fremde Overrides (Spec 3.2). Es bleibt an, bis der Dry-Run des Victron-Teils
  beginnt; für ihn und für die Live-Schaltung von Victron ist es aus (Entscheidungen vom 10.10. und
  14.10.2026; Abschnitt „Akku und Spitzen“, „Victron in den Dry-Run und live“). Für den Punkt
  `victron` schaltest du es vorübergehend ab (Entscheidung vom 12.10.2026) und danach wieder ein,
  sofern der Dry-Run des Victron-Teils nicht läuft. Den Modus liest und setzt du am Cerbo:

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
- **Lesen** sieht alles, ändert nichts. **Bedienen** (seit Plan 2d) schaltet den Regelbetrieb ein
  und aus und ändert Lademodus, Boost, Zuordnung, Gast-Modus und Vorrang; die Bedienelemente dafür
  erscheinen erst ab dieser Rolle, und die Navigation zeigt „Lesen“ und „Bedienen“ dieselben
  Seiten. **Admin** darf zusätzlich Aktoren live oder auf Dry-Run stellen, Sperren quittieren,
  Einstellungen, Verbraucher und Benutzer ändern und sieht das Änderungsprotokoll. Home Assistant
  hat die Rechte von „Bedienen“ (Abschnitt 7).
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
| Übersicht | Energiefluss live mit den Verbrauchern unter dem Haus, Strompreis jetzt und die nächsten 3 h (Balken überfahren, antippen oder mit den Pfeiltasten wählen zeigt den Preis der Viertelstunde), Ladestände, Preise und PV-Prognose für heute und morgen, Hinweise, Verbraucher mit Leistung und Energie seit Mitternacht, „Warum?“ (unten) |
| Laden | je Fahrzeug und je Wallbox eine Karte, dazu „Gast“ und „Vorrang“; ab „Bedienen“ mit den Bedienelementen (unten, „Laden“) |
| Verlauf | Diagramm mit bis zu 8 Reihen für frei wählbare Tage (höchstens 400), Tagesbilanz mit Abdeckung (Viertelstunden und Anteil der aufgezeichneten Zeit; am laufenden Tag „bis jetzt“, also nur die vergangenen Viertelstunden), Kosten, Erlös, Autarkie und den Tageswerten der Zähler (Differenz über den Tag) für den Abgleich mit VRM |
| Verbraucher | Verbraucherbaum mit „Sonstiges“, Leistung und Energie seit Mitternacht („Heute“); Admins legen Verbraucher an, ändern und löschen sie |
| System | Komponenten, Hinweise, core (Betriebsart mit Beginn und Gründen, Eingangsdaten, Adapter, Selbstprüfung, Alarme), Bedienung (Regelbetrieb; Admins: „Live schalten…“ und „Auf Dry-Run…“, Abschnitt 13), Aktoren (angefordert, wirksam, Freigabe, Hinweise und Sperren; Admins: „Quittieren“), Watchdog am Cerbo (Abschnitt 14), Preise (Admins: „Preise jetzt abrufen“), PV-Prognose, alle Signale mit Suche |
| Entscheidungen | das Entscheidungslog mit Filtern (unten, „Entscheidungen und Warum?“) |
| Einstellungen | Tarif, OeMAG-Werte, Preise, PV-Modell, Namen der Ladestationen und harte Grenzen; Admins bearbeiten, bewerten neu und exportieren oder importieren (Abschnitt 8) |
| Benutzer | nur Admins: anlegen, Rolle ändern, Passwort setzen, löschen |
| Protokoll | nur Admins: Änderungsprotokoll nach Bereich |
| Konto | eigenes Passwort, Zwei-Faktor-Anmeldung, Abmelden |

### Laden

Die Seite **Laden** zeigt den Stand der Laderegelung und bedient sie. Sie liest `GET /v1/health` des core
und die gültige Absicht; Änderungen schickt die app als neue Absicht (Abschnitt 13, „Absicht und
Regelbetrieb“). Bis Health eine Änderung zeigt, steht auf der Seite die gesendete Absicht.

- **Hinweis oben:** Ist der Regelbetrieb aus, steht dort „Regelbetrieb ist aus. Der core regelt nichts;
  Modi und Boost wirken erst mit Regelbetrieb.“ Ab „Bedienen“ hat der Hinweis die Taste „Regelbetrieb
  einschalten“.
- **Fahrzeug:** Ladestand, Lademodus, Wallbox und Art der Zuordnung, angesteckt, lädt, die Grenze des
  Phasenwächters, der letzte Grund und, bei einem Fahrzeug mit Tessie, die Befehle der letzten Stunde
  (`commands_hour`, gegen das Stundenlimit von 20). Der Lademodus ist eine Auswahl: PV-Überschuss,
  Min+PV, Sofort oder Aus. Die Taste „Boost“ setzt einen Boost bis zum Abstecken, höchstens 12 h;
  danach steht dort „Boost beenden“ und, solange er gilt, „bis HH:MM“. Gilt ein Boost schon, wartet
  aber auf einen Ansteckvorgang, der noch nicht begonnen hat, steht daneben „wartet auf Anstecken“. Die
  Dauer wählst du nicht.
- **Wallbox:** wer dort steht („kein Fahrzeug“, wenn kein Vorgang läuft, sonst das Fahrzeug oder
  „Gast“), die Art der Zuordnung (erkannt, angenommen, Übersteuerung, unklar) und die Indizien als
  Sätze („Tessie: zu Hause und angesteckt“, „Strom passt nicht zu Tessie“, …), je nachdem, ob sie das
  Fahrzeug stützen oder ausschließen. Die Auswahl „Zuordnung“ kennt „Automatisch“, das jede
  Übersteuerung dieser Wallbox aufhebt, und die Fahrzeuge; eine Wallbox als leer zu übersteuern geht
  nicht.
- **Gast:** der Lademodus für einen Gast an jeder Wallbox (Abschnitt „Laderegelung“, „Zuordnung Auto
  und Wallbox“).
- **Vorrang:** die Reihenfolge der Fahrzeuge bei der Zuteilung („nach oben“, „nach unten“) und der
  Vorrang beim Überschuss: „Autos zuerst“, „Akku zuerst“ oder „Akku bis {SOC} %, dann Autos“.

Ein Befehl, den der core ablehnt (422), erscheint mit den Sätzen des core an der Karte; ist der core
nicht erreichbar, steht dort „core nicht erreichbar.“.

### Entscheidungen und „Warum?“

Die Seite **Entscheidungen** zeigt das Entscheidungslog der app (Abschnitt 13, „Entscheidungslog“):
neueste zuerst, 100 je Seite, mit „Ältere laden“ für die nächsten. Die Spalten sind „Zeit“, „Aktor“,
„Entscheidung“ (der Satz des core) und „Ergebnis“ (bestätigt, Fehler, nicht bestätigt, nicht
gesendet, Dry-Run, abgelehnt, zurückgestellt, von außen geändert). Filter:

- **Zeitraum:** letzte Stunde, heute, 7 Tage oder ein eigenes Datum
- **Aktor** und **Ergebnis**
- **Dry-Run zeigen** (standardmäßig an): Ohne das Häkchen blendet die Seite die Entscheidungen von
  Aktoren im Dry-Run aus
- **Suche im Text**, ohne auf Groß- und Kleinschreibung zu achten

Ohne Treffer steht dort „Keine Entscheidungen im Zeitraum.“. Die Seite sehen alle Rollen.

**Warum?** steht auf der **Übersicht**: je Aktor die jüngste Entscheidung der letzten 24 h mit ihrem
Alter und ein Verweis „Alle Entscheidungen“; ein Klick auf den Aktor öffnet die Seite mit diesem
Filter. Gab es nichts, steht dort „Keine Entscheidungen in den letzten 24 h.“. Die Begründung des
Plan-Slots kommt mit Phase 3.

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
nur der Entwicklung des UI. Es läuft nie auf der Anlage und nie im Netz. Sein core meldet
„Eingeschränkt“ (DEGRADED): Das gehört zur Demo-Welt und sagt nichts über die Anlage. Der Benutzer
`bedienen` hat dort die Rolle „Bedienen“.

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
- **Akku-Sollwert** (seit Plan 2c-2a, Abschnitt „Akku und Spitzen“): der Netz-Sollwert-Override von
  Victron, auch sein Auffrischen. Er gilt unter denselben Bedingungen wie die Laderegelung. Entladegrenze
  und Min-SOC schreibt die Regelung nie.

Jeder Befehl geht durch die Safety-Schicht. Einem Aktor, der nicht live ist, sendet der core nichts;
sein Befehl steht dann als „Dry-Run“ im Entscheidungslog. In CONTROL schreibt der core sonst
nichts.

Der sichere Zustand und die Übergabe gehen der Regelung vor: Ein Aktor, den der core gerade
übergibt, bekommt von ihr nichts, und in jedem Zyklus führt der core erst deren Befehle aus, dann die
der Regelung.

### Absicht und Regelbetrieb

Die Bedienung legt in einer **Absicht** fest, ob der core regeln soll („Regelbetrieb“), in welcher
Betriebsart jeder Aktor läuft und, seit Plan 2c-1, in welchem Lademodus jedes Fahrzeug lädt. Die app
sendet sie seit Plan 2d; `PUT /v1/intent` bleibt der Weg ohne app und für Notfälle (Abschnitt
„Laderegelung“).
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
- **Die app leitet jede Bedienung aus der gültigen Absicht ab:** Sie holt sie mit `GET /v1/intent`,
  ändert genau das Bediente und sendet das Ganze mit `PUT /v1/intent`. Ohne gültige Absicht beginnt sie
  bei der Basis: Regelbetrieb aus, alle Aktoren im Dry-Run, alle Fahrzeuge im PV-Überschuss. Die
  **Version** ist das Größere aus den Millisekunden seit 1970 (Uhr der app) und der gültigen Version + 1,
  damit auch eine aus der Sicherung wiederhergestellte app neuere Versionen erzeugt. Jede Änderung steht
  im Änderungsprotokoll (Quelle `ui` oder `ha`; Aktionen `intent.control`, `intent.mode`,
  `intent.boost`, `intent.assignment`, `intent.guest_mode`, `intent.priority`, `intent.actuators`).
- **`PUT /v1/intent` per `curl` mischt sich über die Version korrekt ein.** Die app führt Änderungen
  nacheinander aus. Antwortet der core mit 409, weil die Version veraltet ist (jemand hat dazwischen eine
  Absicht per `curl` gesendet), holt sie die gültige Absicht neu und wendet die Änderung genau einmal
  neu an; erst ein zweites 409 geht als „Die Absicht wurde gleichzeitig geändert. Bitte erneut
  versuchen.“ an den Bediener. Eine Absicht per `curl` braucht danach eine Version über der gültigen: Die
  app nimmt Millisekunden, also nimm die Version aus `GET /v1/intent` plus 1 (Abschnitt „Laderegelung“).
  Sende eine Absicht per `curl` immer vollständig: Was fehlt, gilt als Standard, auch Lademodi.
- **Ein abgelaufener Boost und eine beendete Übersteuerung gehen nie zurück an den core.** Eine neue
  Version bindet jedes Fahrzeug neu, das `boost_until` oder `wallbox` nennt; die app lässt beides beim
  Ableiten weg, wo es nicht mehr gilt. Sie fragt dafür Health: Ein Fahrzeug behält `boost_until` nur,
  wenn Health für es einen Boost zeigt (`vehicles[].boost_until`) oder er auf seinen Ansteckvorgang
  wartet (`boost_pending`), und `wallbox` nur, wenn Health `assignment: "override"` an genau dieser
  Wallbox zeigt oder die Übersteuerung wartet (`override_pending`). Ein `boost_until` in der
  Vergangenheit entfällt immer. Health gilt der app nur, wenn seine `intent_version` mindestens die
  gültige Version erreicht; sonst, bei ausgeschaltetem Regelbetrieb und bei einem Fahrzeug, dessen
  Lademodus Health als `null` zeigt, gilt der Stand als unbekannt: Ein Boost in der Zukunft und die
  Übersteuerung bleiben, ein abgelaufener Boost entfällt. Das gilt auch für das Fahrzeug, das die
  Bedienung gerade ändert, vor der Änderung. Dazu merkt sich der core je Fahrzeug den `boost_until`
  eines Boosts, dessen Ansteckvorgang geendet hat: Eine neue Version mit demselben `boost_until` (etwa
  aus einem Health, das das Abstecken noch nicht zeigt, oder per `curl`) bindet ihn nicht wieder; nur ein
  anderer `boost_until`, also ein neuer Boost, bindet. So springt ein alter Boost beim nächsten
  Anstecken nicht an, auch wenn dazwischen jemand einen Modus geändert hat.
  Die Felder von Health stehen im Abschnitt „Laderegelung“, „Health für die Bedienung“.
- **Regelbetrieb** schaltest du im UI mit dem Schalter „Regelbetrieb“ (Karte „Bedienung“ unter
  **System**, ab „Bedienen“; auf der Seite „Laden“ mit der Taste „Regelbetrieb einschalten“) oder in
  Home Assistant mit dem Schalter „Regelbetrieb“.

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
| Regelbetrieb ist aus. | Die Absicht verlangt keinen Regelbetrieb, oder es gibt noch keine. Schalte ihn im UI (Karte „Bedienung“ unter **System**, Seite „Laden“) oder in Home Assistant ein; ohne app mit `PUT /v1/intent`. |
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
  (Abschnitt 14, „Meldungen des core“), und der Aktor ist gesperrt. Dazu kommt ein Eintrag im
  Entscheidungslog (unten): Ergebnis „von außen geändert“. Beim Tesla zählt ein bestätigter Strom erst,
  wenn der Tessie-Cache ihn zeigt, denn der Cache hinkt dem Fahrzeug hinterher.
- Eine Sperre löst sich von selbst, wenn die Ursache 60 s lang weg war (`safety.lock_release_s`
  in `config.yaml`). Kehrt die Ursache zurück, beginnt die Wartezeit von vorn. Der Alarm nach einer
  fremden Änderung besteht `safety.lock_release_s` ab der letzten Erkennung; danach gilt die Frist
  wie bei jeder Sperre.
- **Quittierung (Admin):** Statt zu warten gibt ein Admin eine Sperre per Hand frei. Unter **System** →
  „Aktoren“ steht in der Spalte „Hinweise“ bei einer Sperre die Taste „Quittieren“. Quittierbar ist eine
  Sperre, deren Ursache fehlt (die Wartezeit `safety.lock_release_s` läuft noch) oder deren einzige
  Ursache der Alarm einer fremden Änderung ist. Der core antwortet sofort aus dem Stand des letzten
  Zyklus („Quittiert. Die Sperre von {Name} löst sich im nächsten Zyklus.“) und wendet die Quittierung im
  nächsten Zyklus an: Er gibt die Sperre frei und lässt den Alarm der fremden Änderung fallen; die
  Erkennung erwartet den Wert, den das Gerät jetzt hat, als neuen. Erkennt der Zyklus, der die
  Quittierung anwendet, eine neue fremde Änderung, bleiben deren Alarm und Sperre. Eine neue Änderung,
  die schon vor deinem Klick erkannt war, aber noch nicht auf deiner Seite stand, quittierst du mit;
  sieh darum nach dem Quittieren auf die Seite. Besteht eine andere Ursache (Dynamic
  ESS aktiv, EVCS nicht manuell, Ladeplan aktiv, ein Prüfpunkt nicht lesbar), lehnt der core mit 409 ab
  und nennt sie („Ursache besteht weiter: …“); ohne Sperre sagt er „{Name} ist nicht gesperrt.“. Schreibt
  der fremde Regler weiter (etwa evcc), sperrt die Erkennung nach 3 Prüfungen erneut: Schalte ihn vorher
  ab. Die Quittierung steht im Änderungsprotokoll (`lock.acknowledge`). Am core ist es
  `POST /v1/locks/{Aktor}/ack`, in der app `POST /api/control/locks/{Aktor}/ack`.
- Solange Dynamic ESS an ist, bleibt Victron gesperrt. Die EVCS darf trotzdem regeln, denn ihre
  Regelung braucht nur die Netzmessung und den Phasenwächter. So kann der Dry-Run auch vor der
  Live-Schaltung von Victron laufen (Entscheidung des Betreibers vom 10.10.2026). Der Akku-Sollwert
  sendet an einen gesperrten Victron nichts, auch im Dry-Run nicht (Abschnitt „Akku und Spitzen“);
  sein Dry-Run braucht DESS aus.

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
3. Ein Admin schaltet ihn im UI live, mit Passwort und Bestätigung (Live-Schalter, unten).
4. Bei Victron und EVCS ist der Watchdog scharf.

Der core prüft die Punkte 1 und 4 selbst. Punkt 2 stellst du sicher, bevor du `live_allowed`
setzt. Das UI zeigt unter **System** → „Aktoren“ je Aktor „Angefordert“, „Wirksam“ und „live
erlaubt“. Wirksam live ist ein Aktor nur im Regelbetrieb. Weicht „Wirksam“ ab, nennen die
„Hinweise“ den Grund: „In config.yaml nicht für den Live-Betrieb freigegeben (live_allowed).“,
„Regelbetrieb ist aus.“ oder „Watchdog am Cerbo ist nicht scharf.“ Während einer Übergabe (nächster
Abschnitt) steht dort „Übergabe: Sicherwerte werden gesetzt.“, auch bei ausgeschaltetem
Regelbetrieb: Dann ist nur der übergebene Aktor wirksam live, die übrigen live angeforderten zeigen
Dry-Run mit „Regelbetrieb ist aus.“

**Live-Schalter (nur Admin):** Die Karte „Bedienung“ unter **System** hat zwei Tasten.

- **„Live schalten…“** öffnet den Dialog „Live schalten“. Er listet die Aktoren und je Aktor die
  Bedingungen mit „ja“ oder „nein“: „live erlaubt“ (`live_allowed`, Bedingung 1), „Watchdog scharf“ (nur
  Victron und EVCS, Bedingung 4), „Regelbetrieb an“ und „nicht gesperrt“. Wählen lässt sich nur ein
  Aktor, der „live erlaubt“ ist; die übrigen Bedingungen sind Hinweise, denn der core wartet von selbst,
  bis sie gelten (bis dahin ist „Wirksam“ Dry-Run). Du bestätigst „Prüfprotokoll Teil 2 für die
  gewählten Aktoren ist bestanden.“ (Bedingung 2; ein Merkmal dafür gibt es nicht) und gibst dein
  Passwort erneut ein. Bei jedem Aktor außer Victron, also bei EVCS und Tesla, kommt „evcc ist ganz
  abgeschaltet.“ dazu; der Dialog erinnert: „EVCS und Tesla gehen gemeinsam live.“ Wählst du die Aktoren
  neu, entfallen die Bestätigungen und du setzt sie neu. Mehrere Aktoren gehen in einem Zug live.
- **„Auf Dry-Run…“** öffnet den Dialog „Auf Dry-Run stellen“ („Der core übergibt die gewählten Aktoren
  und setzt ihre Sicherwerte.“). Er listet die Aktoren, die live angefordert sind, alle vorgewählt; die
  Taste „Auf Dry-Run“ genügt als Bestätigung, ein Passwort braucht es nicht. Die Übergabe läuft wie unten
  beschrieben.

Ein falsches Passwort lehnt die app mit „Passwort falsch.“ ab (403), eine fehlende Bestätigung mit
„Bitte bestätige Prüfprotokoll Teil 2.“ (422); die Absicht bleibt dann unverändert. Gibt `config.yaml`
einen Aktor nicht frei, klemmt der core und der Aktor bleibt im Dry-Run: Die Karte zeigt den Hinweis des
core. Passwort und Bestätigung verlangt die app nur für Aktoren, die neu auf live wechseln. Das prüft
sie bei jedem Versuch noch einmal gegen die gültige Absicht, auch nach einem 409: Hat jemand einen Aktor
inzwischen auf Dry-Run gestellt, geht er ohne Passwort und Bestätigung nicht wieder live (dieselben
Antworten, 403 bzw. 422). Eine leere Auswahl lehnt die app mit „Keine Aktoren gewählt.“ ab (422). Die
Änderung steht im Änderungsprotokoll (`intent.actuators`, mit den Aktor-IDs). Home Assistant hat den
Live-Schalter nicht.

**Live-Freigabe zurücknehmen:** Bevor du bei einem Victron- oder EVCS-Aktor `live_allowed` wieder
auf `false` setzt oder ihn auf anderem Weg aus der `config.yaml` nimmst (etwa eine EVCS entfernst),
stellst du ihn im UI auf Dry-Run („Auf Dry-Run…“; ohne app mit `PUT /v1/intent`). Der core übergibt ihn dann (nächster Abschnitt): Er setzt die Sicherwerte und
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
- seit Plan 2d jede erkannte fremde Änderung eines Aktors („von außen geändert“, Ergebnis `foreign`):
  Sie ist kein Befehl des EMS, sondern der Befund, dass ein anderes System den Wert am Gerät
  geändert hat. `requested` ist der erwartete Wert, `applied` der gefundene, `dry_run` ist falsch.
- Nicht im Log steht seit Plan 2c-2a ein **Auffrischen** des Netz-Sollwert-Overrides, das gelingt
  oder im Dry-Run ein Urteil ergibt (Abschnitt „Akku und Spitzen“, „Auffrischen“). Abgelehnt,
  zurückgestellt oder gescheitert steht es dort wie jeder Befehl.

„Dieselbe“ heißt: gleicher Aktor, gleiche Art, gleicher angeforderter Wert und gleiche Befunde. Ein
zurückgestellter Stopp kurz nach einem zurückgestellten Start erscheint also beide Male.

Ein Eintrag lautet etwa: `EVCS „evcs“ (sicherer Zustand): Strom 6 A – gesendet und bestätigt.`
Die Laderegelung nennt ihre Gründe in Klammern, etwa
`EVCS „evcs“ (PV-Überschuss, Überschuss): Strom 8 A – Dry-Run, nicht gesendet.` (alle Gründe und
Eingaben: Abschnitt „Laderegelung“). Eine erkannte fremde Änderung lautet etwa
`EVCS „evcs“ (Änderung von außen): Strom 10 A – von außen geändert: erwartet 8 A.`, ein erneutes
„Laden aus“ nach einem Selbststart des Tesla
`Tesla Model 3 (Aus, Selbststart): Laden aus – Dry-Run, nicht gesendet.`

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
- **In der app (seit Plan 2d):** Die app speichert die Entscheidungen in SQLite (Tabelle `decision`,
  Migration `0007`; sie läuft beim Start der app). Die Seite „Entscheidungen“ und „Warum?“ lesen sie
  (Abschnitt 12).
  - **Stream:** Jede Meldung `decision` landet sofort in einem Puffer, den die app jede Sekunde in
    einem Zug schreibt.
  - **Rückstand:** Nach jedem Verbindungsaufbau zum core holt sie die Entscheidungen mit
    `GET /v1/decisions` ab dem jüngsten `ts` der Tabelle minus 10 min (ohne Eintrag ab jetzt minus
    7 Tage), seitenweise. Doppelte `id` werden übergangen. So bleibt nach einem Neustart des core oder
    der app keine Lücke und kein Doppelter, und ein Rücksprung der Wanduhr bis 10 min geht nicht
    verloren. Quelle des Rückstands sind die Dateien des core (7 Tage): War die app länger weg,
    fehlen die älteren.
  - **Aufbewahrung:** 180 Tage (`app.decisions_keep_days` in `config.yaml`, 1 bis 3650). Die Wartung
    der app räumt Ältere auf.
  - **Grafana:** Je neu gespeicherter Entscheidung schreibt die app einen Punkt `decision` nach InfluxDB
    (Retention Policy `raw`, 90 Tage), mit den Tags `actuator`, `kind` (Befehlsart), `result` und
    `dry_run` (`"true"` oder `"false"`), den Feldern `text` und `codes` (mit „,“ verbunden) und dem
    Zeitpunkt der Entscheidung.
    In Grafana legst du an der InfluxDB-Datenquelle eine Annotation an, etwa für gesendete (nicht
    Dry-Run-)Entscheidungen:

    ```sql
    SELECT "text" FROM "raw"."decision" WHERE $timeFilter AND "dry_run" = 'false'
    ```

    Mit `"result"` oder `"actuator"` als Tag der Annotation filterst du weiter. Ohne die Bedingung
    `"dry_run" = 'false'` markiert Grafana auch jedes Dry-Run-Urteil.
- Im Volume `core-data` legt der core außerdem ab: `intent.json`, `write_budget.json`
  (Schreibbudget für persistente Einstellungen am Cerbo), `rate_limits.json` (die Stundenfenster
  des Tesla; sie überstehen einen Neustart) und `spool/` (Telemetrie, Abschnitt 3).

### Reihenfolge der Inbetriebnahme

1. **Watchdog** installieren und die Probe bestehen (Abschnitt 14).
2. **Prüfprotokoll Teil 2** gemeinsam durchführen (Abschnitt 11): EVCS und Tesla zuerst, Victron
   mit vorübergehend abgeschaltetem Dynamic ESS. `live_allowed: true` setzt du nur für Aktoren,
   die bestanden haben.
3. **Dry-Run neben evcc:** Er beginnt mit den Images von Plan 2d und dem Regelbetrieb aus dem UI
   (Schalter „Regelbetrieb“, alle Aktoren bleiben im Dry-Run; ohne app eine Absicht per
   `PUT /v1/intent`, Abschnitt „Laderegelung“). Er dauert 2 Wochen, und die Entscheidungen werden
   gemeinsam gesichtet (Spec 18), auf der Seite „Entscheidungen“ und in Grafana.
4. **Live-Schaltung:** EVCS und Tesla gemeinsam mit dem Live-Schalter im UI, mit abgeschaltetem evcc
   (Abschnitt „Laderegelung“, „Umstieg von evcc“). Victron geht seit Plan 2c-2a getrennt davon live, sobald
   Dynamic ESS aus ist (Spec 8.7; Abschnitt „Akku und Spitzen“, „Victron in den Dry-Run und live“).
5. Für den Dry-Run des Victron-Teils und seine Live-Schaltung schaltest du Dynamic ESS ab
   (Entscheidungen vom 10.10. und 14.10.2026); danach läuft der Victron-Teil von Teil 2.

### Wartung

Für ein Update des core gilt der Ablauf aus Spec 15:

1. Regelbetrieb auf OBSERVE schalten (im UI mit dem Schalter „Regelbetrieb“, in Home Assistant mit dem
   gleichnamigen Schalter; ohne app mit `PUT /v1/intent` und `"control": false`, ohne Absicht steht der
   core ohnehin in OBSERVE).
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
  übergeben wird; bis zur Live-Schaltung von Victron (Abschnitt „Akku und Spitzen“) und von EVCS und
  Tesla (Live-Schalter, Abschnitt 13) gibt es ihn nicht.
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
  der Live-Schaltung relevant (Victron nach Plan 2c-2a, EVCS und Tesla mit dem Live-Schalter). Im Dry-Run
  stört der core die Probe nicht: Er sendet keine Heartbeats und gibt einen Watchdog nicht frei,
  für den er in diesem Lauf nie zuständig war.
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
| Regelung ausgesetzt: Ausnahme … im Regelzyklus. | Stufe Fehler. Ein Aufruf der Regelung hat eine Ausnahme geworfen, das ist ein Fehler im core. Die Regelung sendet in dem Zyklus nichts, der Heartbeat bleibt aus; Keeper, Stream, Alarme und Entscheidungslog laufen weiter. Sichere das Log und melde den Fehler (Abschnitt „Akku und Spitzen“, „Alarme“). |
| 15-min-Ziel überschritten: … kW in der Viertelstunde bis HH:MM. | Stufe Warnung. Die Hochrechnung lag 60 s über `control.peak_target_kw`, und Drosselung und Akku haben es nicht gehalten. Der Alarm endet mit der Viertelstunde (Abschnitt „Akku und Spitzen“, „Alarme“). |

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
Tesla über Tessie). Der Akku und die Spitzen folgen im Abschnitt „Akku und Spitzen“ (seit Plan
2c-2a). Welches Auto an welcher Wallbox steht, erkennt der core seit Plan 2c-2b selbst (unten,
„Zuordnung Auto und Wallbox“). Jeder Befehl geht durch die Safety-Schicht (Abschnitt 13). **Solange
ein Aktor im Dry-Run ist, sendet der core nichts** und protokolliert nur, was er täte. Live gehen EVCS und Tesla
gemeinsam mit dem Live-Schalter im UI und mit abgeschaltetem evcc (unten, „Umstieg von evcc“). Die
Bedienung (Regelbetrieb, Lademodi, Boost, Zuordnung) liegt seit Plan 2d in der app (Abschnitt 12,
„Laden“) und in Home Assistant (Abschnitt 7).

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
| PV-Überschuss | `pv` | Startet, wenn der Überschuss den Mindeststrom des Autos `control.start_after_s` (120 s) lang trägt, und stoppt, wenn er `control.stop_after_s` (300 s) lang darunter liegt. Dazwischen lädt das Auto am Mindeststrom weiter; die Lücke deckt der Akku über den Eigenverbrauch von ESS (ohne Akku-Sollwert). | Importlimit minus Abstand: 20 − 1 = 19 A |
| Min+PV | `min_pv` | Lädt immer mindestens mit dem Mindeststrom, darüber wie PV-Überschuss. Der Mindeststrom geht dem Importlimit vor, nicht der Sicherung. | wie PV-Überschuss; für den Mindeststrom die Sicherung |
| Sofort | `now` | Voller Strom innerhalb der Grenze, auch aus dem Akku: unter dem Importlimit über den Eigenverbrauch von ESS (ohne Akku-Sollwert; mit ihm liefert der Akku nur, was der Override nicht trägt), darüber über Peak Shaving. | Sicherung minus Abstand: 35 − 2 = 33 A |
| Aus | `off` | Stoppt das Laden und hält es gestoppt. | – |
| Boost | `boost_until` | Wie Sofort bis zu diesem Zeitpunkt oder bis zum Abstecken; danach gilt wieder der eingestellte Modus. | wie Sofort |

Die Zahlen gelten für die Beispielkonfiguration (`grid.fuse_a` 35 A, `grid.fuse_margin_a` 2 A,
`grid.import_limit_a` 20 A, `control.import_margin_a` 1 A); alle Werte stehen in `config.yaml`.

Was die Tabelle und die Punkte darunter über die Herkunft der Energie sagen (Akku oder Netz), gilt,
solange Victron im Dry-Run ist, also ohne Akku-Sollwert. Mit Victron live bestimmt der Akku-Sollwert,
wann der Akku liefert (Abschnitt „Akku und Spitzen“).

- Liegt der SOC unter USV-Reserve + `control.reserve_guard_pct` (5 %) oder ist er unbekannt, richten
  sich auch Sofort und Boost am Importlimit aus (Grund „Schutz der USV-Reserve“): Peak Shaving soll
  den Akku nicht unter die Reserve ziehen.
- **Boost** darf auch aus dem Akku laden und gehört nicht zum Ziel des 15-min-Wächters (Abschnitt
  „Akku und Spitzen“).
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
  Anteil eines PV-Autos rechnet nie mit Leistung aus dem Akku. Ohne Akku-Sollwert lädt es trotzdem
  aus dem Akku, wenn es dem Überschuss nicht sofort folgt: in den Stopp-Minuten am Mindeststrom
  (oben), in Übergängen, bis die Regelung nachgezogen hat, und beim Tesla unter schnellen Wolken
  („Hinweise für die Anlage“, unten). Mit Victron live verschiebt der Akku-Sollwert diesen
  Fehlbetrag aufs Netz, solange die Unterstützung nicht gilt (Abschnitt „Akku und Spitzen“). Lädt
  der Akku, zählt seine Leistung nur, wenn die Autos Vorrang haben (`surplus_priority`, unten).
  Fehlt ein Messwert, startet kein PV-Auto.
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
  Zuschlag als Last, jeweils auf allen Phasen: die EVCS mit `safe_a`, und jedes Auto am Wall
  Connector, dessen Strom weder der Wall Connector noch Tessie liefert (der Tesla ebenso wie der
  e-Golf oder ein Gast), mit dem `max_a` seines Ladepunkts (beim Gast dem des Wall Connectors).
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
  fängt Peak Shaving ab) und wegen einer Grenze des 15-min-Wächters (Abschnitt „Akku und
  Spitzen“). **Jeder erneute Versuch nach „nicht übernommen“ ist normal und nie
  dringend**, auch der einer Senkung wegen Überlast; er wartet auf die 90-s-Regel. Ein Befehl wegen
  Überlast, der neu entsteht, weil die Stufe stieg, ist kein Neuversuch und bleibt dringend,
  höchstens einmal je Stufe.
- Regel 4 der Safety-Schicht prüft jeden Befehl weiterhin unabhängig vom Wächter.

### Absicht per PUT /v1/intent

Die app sendet die Absicht für dich (Abschnitt 13, „Absicht und Regelbetrieb“). `PUT /v1/intent` ist
der Weg ohne app und für Notfälle, etwa wenn die app nicht läuft: Dann setzt du Lademodi selbst an die
interne API des core. Den
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
| `guest.mode` | `pv`, `min_pv`, `now`, `off`: der Modus eines Gastes, also eines Autos, das nicht in `config.yaml` steht (Abschnitt „Zuordnung Auto und Wallbox“); kein Boost, keine Übersteuerung | `pv` |

- Die Schlüssel der Fahrzeuge und Wallboxen sind die aus `config.yaml` (im Beispiel `tesla`, `egolf`
  und `evcs`, `twc`).
- `surplus_priority` sagt, wer den PV-Überschuss zuerst bekommt: `cars_first` die Autos (die
  Ladeleistung des Akkus zählt zum Überschuss), `battery_first` zuerst der Akku, `battery_until_soc`
  die Autos, sobald der Akku den SOC `soc_pct` erreicht hat.
- `guest.mode` ist der Modus eines **Gastes**, also eines Autos, das nicht in `config.yaml` steht.
  Boost und Übersteuerung der Wallbox gibt es für einen Gast nicht; unter `vehicles` lehnt der core
  ihn als unbekanntes Fahrzeug ab (422). Wer ein Gast ist, erkennt der core selbst. An einer EVCS
  regelt er den Gast in diesem Modus, am Wall Connector zählt der Modus für den Akku-Sollwert
  (Abschnitt „Zuordnung Auto und Wallbox“ und „Akku und Spitzen“). Die Auswahl gibt es im UI (Seite
  „Laden“, Karte „Gast“) und in Home Assistant („Gast-Lademodus“); ohne app setzt du den Modus hier.
- **Version:** Sie muss über der gültigen liegen. Dieselbe Version mit gleichem Inhalt gilt als
  Wiederholung. Die app nimmt Millisekunden seit 1970 (Abschnitt 13). Hat sie schon eine Absicht
  gesendet, nimm für eine Absicht per `curl` die Version aus `GET /v1/intent` plus 1; eine kleine Zahl
  wie im Beispiel lehnt der core dann mit 409 ab.
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
- **Boost und Übersteuerung** gelten bis zum Ende des Ansteckvorgangs, an den der core sie bindet:
  - Eine **Übersteuerung** (auch mit Boost im selben Eintrag) bindet an die Wallbox, die du nennst:
    Meldet sie ein Fahrzeug, an den laufenden Vorgang, sonst an den nächsten.
  - Ein **Boost ohne Übersteuerung** bindet an den Vorgang der Wallbox, an der das Fahrzeug beim
    Setzen steht, erkannt oder angenommen (Health, `vehicles[].wallbox`; Abschnitt „Zuordnung Auto
    und Wallbox“). Steht es an keiner Wallbox mit Vorgang, bindet er an seine Standard-Wallbox
    (`default_wallbox`): Meldet sie ein Fahrzeug, an den laufenden Vorgang, sonst an den nächsten.
    So gilt ein Boost für den Tesla, den der core an der EVCS erkannt hat, dort und springt nicht
    beim nächsten Anstecken am Wall Connector an.
  - **Grenze:** Setzt du einen Boost für den e-Golf, während der erkannte Tesla an der EVCS steht,
    bindet er an den Vorgang des Tesla und endet mit dessen Abstecken (wie bisher, wenn der Tesla
    an die EVCS übersteuert ist). Steckt danach der e-Golf an, braucht sein Boost eine neue Version.

  Nach dem Ende ihres Vorgangs gelten sie erst wieder mit einer neuen Version. **Eine neue Version
  bindet jedes Fahrzeug neu, das `boost_until` oder `wallbox` nennt**, auch wenn der Boost oder die
  Übersteuerung beim Abstecken schon endete. Nimm solche Einträge aus der nächsten Version, wenn sie
  nicht wieder gelten sollen; die app tut das beim Ableiten selbst (Abschnitt 13, „Absicht und
  Regelbetrieb“). Nach einem Neustart des core springt ein Boost vom Vortag nicht beim
  nächsten Anstecken an.
- Der core speichert die gültige Absicht (`intent.json`) und nutzt sie nach einem Neustart weiter.
  Eine gespeicherte Absicht aus Plan 2b lädt mit den Standardwerten.
- **Regelbetrieb aus:** `{"version": …, "control": false}`. Der core übergibt die Aktoren und geht
  dann nach OBSERVE (Abschnitt 13).

Für den **Dry-Run** genügt `{"version": …, "control": true}` mit einer Version über der gültigen:
Alle Aktoren bleiben im Dry-Run, und alle Fahrzeuge laden im PV-Überschuss, wie heute mit evcc.

### Zuordnung Auto und Wallbox

Seit Plan 2c-2b bestimmt der core selbst, wer an einer Wallbox steht. Das gilt für jede Wallbox, die
ein Fahrzeug meldet (ein laufender Ansteckvorgang): Dort steht ein Fahrzeug aus `config.yaml` oder
ein **Gast**, also ein Auto, das `config.yaml` nicht kennt. Ein Fahrzeug steht höchstens an einer
Wallbox, und ohne Vorgang steht dort niemand. Boost, Übersteuerung und „geladen“ hängen wie bisher
an einem Ansteckvorgang. Einen Boost ohne Übersteuerung bindet der core jetzt an den Vorgang der
Wallbox, an der das Fahrzeug beim Setzen steht, erkannt oder angenommen; steht es an keiner, wie
bisher an seine Standard-Wallbox (Abschnitt „Absicht per PUT /v1/intent“, mit der Grenze für den
e-Golf). Die Erkennung schreibt nichts auf die Geräte: Sie bestimmt nur, welches Auto die
Regelung an welcher Wallbox sieht. Die Szenarien `zuordnung`, `tesla-evcs`, `egolf-twc` und
`gast-evcs` spielen die typischen Bilder durch (Abschnitt „Simulator“).

**Wer steht wo.** Je Zyklus, solange die Zuordnung nicht unklar ist, in dieser Reihenfolge:

1. **Übersteuerung** (`override`): Ein Fahrzeug mit gültiger Übersteuerung steht an der Wallbox,
   die du in der Absicht nennst (unten).
2. **Erkannt** (`detected`): An jeder anderen Wallbox mit Vorgang steht das Fahrzeug oder der Gast,
   den der core dort erkannt hat (siehe „Entscheidung“).
3. **Angenommen** (`default`): An jeder übrigen Wallbox mit Vorgang steht ihr Standardfahrzeug, das
   erste Fahrzeug in `config.yaml` mit dieser `default_wallbox` (Tesla am Wall Connector, e-Golf an
   der EVCS), wenn es dort nicht ausgeschlossen ist und noch an keiner Wallbox steht. Sonst steht
   dort ein **Gast, angenommen**. Das gilt, solange die Indizien für eine Erkennung nicht reichen.
   Das Standardfahrzeug an seiner Standard-Wallbox regelt der core angenommen wie erkannt.
4. **Sonst:** Ein Fahrzeug, das nirgends steht, hängt an seiner Standard-Wallbox, wenn dort kein
   Vorgang läuft und keine Übersteuerung sie belegt (`default`; angesteckt ist es dort nicht).
   Sonst hat es keine Wallbox.

Widersprechen sich die Indizien, ist die Zuordnung **unklar** (`unclear`, siehe „Entscheidung“).

**Indizien.** Der core wertet je Wallbox mit Vorgang und je Fahrzeug aus `config.yaml` sechs Indizien
aus. Jedes zählt für sich: Es **stützt** das Fahrzeug an der Wallbox, **schließt es dort aus** oder
sagt nichts. Unbekannte Werte (Tessie oder Home Assistant nicht erreichbar, `unknown`) sprechen für
nichts und schließen nichts aus. Health nennt die Indizien je Wallbox (unten).

- **Tessie** (`tessie`): Meldet Tessie das Fahrzeug zu Hause und angesteckt, stützt das Indiz es an
  einer Wallbox mit Vorgang, wenn Stromvergleich oder Phasen-Signatur es an jeder anderen Wallbox
  mit Vorgang ausschließen (Elimination, unten); läuft nur ein Vorgang, immer. Nach dem Ende eines
  Vorgangs an einer anderen Wallbox stützt es erst wieder mit einem Wert, den Tessie mindestens
  60 s danach abgefragt hat (unten, „Tessie nach einer Abfahrt“). Meldet Tessie sicher, dass es
  nicht angesteckt oder nicht zu Hause ist, schließt es das Fahrzeug an jeder Wallbox mit Vorgang
  sofort aus.
- **Stromvergleich** (`current`): Laden die Wallbox und das Auto laut Tessie (mindestens 1 A, und
  Tessie meldet die Phasenzahl aus `config.yaml`), vergleicht der core die Ströme. Sie passen, wenn
  die Wallbox auf genau so vielen Netzphasen mindestens 1 A misst, wie das Auto Phasen hat, und
  jeder dieser Ströme höchstens 1 A vom Strom des Autos abweicht. Passen sie 60 s ohne
  Unterbrechung, stützt das Indiz das Fahrzeug an dieser Wallbox; passen sie 60 s nicht, schließt es
  das Fahrzeug dort aus, gezählt erst ab einem Tessie-Wert, der mindestens 60 s nach der letzten
  Änderung der Ströme der Wallbox abgefragt wurde (unten, „Stromvergleich nach einer Änderung“).
  Unter 1 A lädt das Auto hier nicht: Tessie meldet „Charging“ schon, bevor Strom fließt.
- **Zeitliche Korrelation** (`timing`): Das Fenster, in dem das Auto angesteckt wurde, reicht nach den
  Zeitstempeln von Tessies Werten von der letzten Meldung „nicht angesteckt“ (60 s früher gezählt)
  bis zur ersten Meldung „angesteckt“ danach. Begann der Vorgang der Wallbox in diesem Fenster und
  in derselben Zeit an keiner anderen Wallbox ein Vorgang, stützt das Indiz das Fahrzeug an dieser
  Wallbox. Es schließt nie aus.
- **Phasen-Signatur** (`phases`): Lädt die Wallbox und misst sie 60 s ohne Unterbrechung auf
  derselben Zahl n von Netzphasen mindestens 1 A, stützt das Indiz jedes Fahrzeug mit `phases: n` in
  `config.yaml` und schließt jedes andere dort aus.
- **Home Assistant** (`home`): Meldet `home_entity` des Fahrzeugs `home`, stützt das Indiz es an
  einer Wallbox mit Vorgang, ebenfalls nur nach der Elimination. Jeder andere Zustand schließt es an
  jeder Wallbox mit Vorgang aus; `unknown` und `unavailable` sagen nichts.
- **Ausschluss** (`elsewhere`): Meldet Tessie das Fahrzeug zu Hause und angesteckt, und meldet jede
  andere Wallbox sicher kein Fahrzeug, stützt das Indiz das Fahrzeug an dieser Wallbox: Es muss
  irgendwo stecken.

`tessie`, `current`, `timing` und `elsewhere` gibt es nur für ein Fahrzeug mit `tessie` in
`config.yaml` (der Tesla), `home` nur für eines mit `home_entity`, die Phasen-Signatur für jedes.
Fehlt eine Bedingung von Stromvergleich oder Phasen-Signatur einen Zyklus lang, beginnen ihre 60 s
neu. Einen Gast stützt kein Indiz: Der core erkennt ihn daran, dass alle Fahrzeuge dort ausgeschlossen
sind.

„Zu Hause und angesteckt“ und „zu Hause“ sagen nicht, an welcher Wallbox das Fahrzeug steht. Stecken
an beiden Wallboxen Autos (zwei Vorgänge), gilt darum die Elimination: `tessie` und `home` stützen ein
Fahrzeug an einer Wallbox nur, wenn Stromvergleich oder Phasen-Signatur es an der anderen Wallbox
ausschließen; ausschließen tun beide weiter an jeder Wallbox. Lädt etwa der e-Golf am Wall Connector
auf zwei Phasen und der Tesla an der EVCS auf drei, schließen die Phasen jeden an der Wallbox des
anderen aus, und der core erkennt beide. Lädt an der anderen Wallbox nichts, stützen `tessie` und
`home` nicht; stützten sie dann, hielte der core einen dreiphasigen Gast an der EVCS für den Tesla,
der nicht ladend am Wall Connector steckt, und einen zweiphasigen Gast am Wall Connector für den
e-Golf, der „geladen“ an der EVCS steckt. Für die Elimination zählen nur Strom und Phasen; steht ein
anderes Fahrzeug laut Übersteuerung an der anderen Wallbox, zählt das nicht (ein übersteuertes
Fahrzeug ist an jeder anderen Wallbox ohnehin ausgeschlossen). Ist etwa der e-Golf an den Wall
Connector übersteuert und steht der Tesla an der EVCS, ohne zu laden, bleibt an der EVCS „Gast,
angenommen“ mit dem Modus des Gastes: Lädt der e-Golf nicht zweiphasig, stützen den Tesla dort weder
`tessie` noch `elsewhere`; lädt er zweiphasig, stützt `tessie` den Tesla an der EVCS, doch allein
reicht das nicht. Übersteuere dann auch den Tesla. Das weicht von Design 2c-2 (7.2) ab und hat einen Preis:
Steckt an der anderen Wallbox ein Auto an, fällt eine Erkennung, die auf Tessie oder Home Assistant
beruht, auf „angenommen“ zurück, bis die andere Wallbox das Fahrzeug ausschließt (60 s Laden dort)
und es danach 60 s Kandidat war, also mindestens 2 min, und so lange, wie dort nichts lädt. Ein
e-Golf, den der core allein am Wall Connector erkannt hat, steht so lange angenommen an der EVCS
oder nirgends, und am Wall Connector steht „Gast, angenommen“. Steht ein Fahrzeug nicht an seiner
Standard-Wallbox und zeigt Health es nicht dort, wo es steht, setzt du eine Übersteuerung (unten).

- **Tessie meldet verspätet.** Tessie fragt ein abgestecktes Auto zu Hause alle 60 s ab, eines unterwegs
  nur alle 600 s, und erfährt vom Anstecken mit Verzug: Ein Wert aus einer Abfrage vor oder kurz nach dem Beginn eines Vorgangs sagt
  über diesen Vorgang nichts. Darum zählen `tessie`, `current` und `elsewhere` an einer Wallbox nur
  mit Werten, die mindestens 60 s nach dem Beginn ihres Vorgangs abgefragt wurden (lief der Vorgang
  schon beim Start des core, zählen sie immer). So bleibt der Tesla, der am Wall Connector ankommt,
  dort „angenommen“ und geregelt wie bisher, bis Tessie ihn neu abgefragt hat. Ohne diese Regel
  schlösse ihn sein letzter Wert „nicht angesteckt“ für bis zu 10 min aus, und er lüde als
  angenommener Gast ungeregelt mit dem Höchststrom des Wall Connectors. Die zeitliche Korrelation
  misst dagegen gerade das Intervall zwischen zwei Meldungen von Tessie.
- **Tessie nach einer Abfahrt.** Dasselbe gilt, wenn ein Vorgang endet: Meldet eine Wallbox kein
  Fahrzeug mehr (oder beginnt an ihr ein neuer Vorgang), stützen `tessie` und `elsewhere` ein
  Fahrzeug an einer anderen Wallbox erst wieder mit einem Wert, den Tessie mindestens 60 s nach
  diesem Ende abgefragt hat. Ein Ausschluss durch Tessie gilt sofort; `current`, `timing` und
  `home` (Home Assistant meldet jede Änderung sofort) bleiben, wie sie sind. Grund: Fährt der
  Tesla vom Wall Connector weg, meldet Tessie ihn bis zur nächsten Abfrage noch „zu Hause und
  angesteckt“ (alle 60 s, solange er angesteckt nicht lädt). Sonst hielte der core ihn an der EVCS,
  an der der e-Golf steckt und nicht lädt, nach 60 s für erkannt: Der Tesla bekäme `max_a`, und die
  EVCS lüde kurz in seinem Modus. Preis: Eine Erkennung, die auf `tessie` und `elsewhere` beruht,
  kommt nach dem Ende eines Vorgangs an der anderen Wallbox erst 60 s nach der ersten Abfrage, die
  mindestens 60 s nach dem Ende liegt; bis dahin bleibt das Fahrzeug angenommen. Das unterbricht
  auch eine laufende Erkennung: Ist der Tesla nicht ladend an der EVCS über `tessie` und `timing`
  erkannt und verlässt ein zweiphasiger Gast den Wall Connector, zeigt die EVCS rund 2 min lang
  „angenommen“ (e-Golf oder Gast), dann ist der Tesla neu erkannt, mit einem neuen
  `assignment_detected`.
- **Stromvergleich nach einer Änderung.** Meldet Tessie eine andere Phasenzahl als `phases` in
  `config.yaml`, gibt es kein `current`: Der Wert ist unplausibel und zählt als unbekannt. „Passt
  nicht“ zählt an einer Wallbox erst mit einem Wert, den Tessie mindestens 60 s nach der letzten
  Änderung der Ströme dieser Wallbox abgefragt hat; erst dann beginnen die 60 s, nach denen
  `current` ausschließt. Eine Änderung ist: Eine Phase weicht um mehr als 1 A vom vorigen Zyklus
  ab, eine Phase beginnt oder endet zu laden, das Laden beginnt oder endet, die Ströme werden
  bekannt oder unbekannt, oder der core sieht die Wallbox zum ersten Mal (auch nach einer Pause der
  Regelung). „Passt“ zählt wie bisher sofort. Grund: Tessies Ist-Strom hinkt dem Wall Connector
  60 s und mehr nach, und welche Phasenzahl Tessie beim dreiphasigen Laden meldet, ist an der
  Anlage nicht geprüft. Sonst schloss der Stromvergleich den Tesla an seinem eigenen Wall Connector
  aus, wenn Tessie eine Stromänderung der Regelung erst nach 60 s meldete oder eine falsche
  Phasenzahl: Dort stand ein Gast, 60 s später war die Zuordnung unklar, und die Tesla-Befehle
  pausierten. Preis: Ein Fahrzeug, das nur `current` an einer
  Wallbox ausschließt, ist dort frühestens 60 s nach der ersten Abfrage ausgeschlossen, die 60 s
  nach der letzten Stromänderung liegt, und nie, solange sich der Strom öfter ändert. Ein Gast, den
  nur der Strom vom Tesla trennt, bleibt dann „Gast, angenommen“; die Regelung ist dieselbe.
- **Auto nicht zu Hause.** Sieht Tessie ein Auto nicht zu Hause, gibt es für dieses Auto weder
  `current` noch `timing`: Es lädt woanders, und `tessie` schließt es an jeder Wallbox ohnehin aus.
  Auch `elsewhere` verlangt „zu Hause und angesteckt“, nicht schon „angesteckt“. Sonst stützten ein
  Tesla an einer fremden Ladesäule und ein dreiphasiger Gast bei dir mit demselben Strom den Tesla an
  deiner Wallbox, während Tessie ihn ausschlösse: ein falscher Widerspruch.

**Entscheidung.** Je Wallbox mit Vorgang und je Fahrzeug:

- **Kandidat** ist ein Fahrzeug an einer Wallbox mit mindestens 2 stützenden Indizien und keinem,
  das es ausschließt.
- **Erkannt** ist es dort, wenn es 60 s ohne Unterbrechung Kandidat ist und an keiner anderen
  Wallbox Kandidat ist. Sind an einer Wallbox zwei Fahrzeuge erkannt, gilt keins, und die Wallbox
  fällt auf „angenommen“ zurück.
- **Ausgeschlossen** ist ein Fahrzeug an einer Wallbox, wenn ein Indiz es dort ausschließt oder es
  laut Übersteuerung an einer anderen Wallbox steht. Ein **Gast** ist an einer Wallbox erkannt, wenn
  dort jedes Fahrzeug aus `config.yaml` 60 s ohne Unterbrechung ausgeschlossen ist, etwa weil der
  e-Golf laut Home Assistant nicht zu Hause und der Tesla laut Tessie nicht angesteckt ist oder
  weil die Phasen-Signatur zu keinem Fahrzeug passt.
- Die 60 s von „erkannt“ und „Gast“ beginnen neu, sobald ihre Bedingung einen Zyklus lang fehlt, und
  enden mit dem Vorgang. Sie zählen nur beobachtete Zeit, wie die 60 s von `current` und `phases`:
  Ruhte die Regelung zwischen zwei Zyklen länger als 60 s (etwa in OBSERVE oder SAFE), beginnen sie
  alle neu. Die Zeiten von „unklar“ zählen weiter wie bisher.
- **Übersteuerung geht vor:** An der Wallbox einer Übersteuerung entscheidet die Erkennung nicht
  (kein Fahrzeug, kein Gast), und das übersteuerte Fahrzeug ist an jeder anderen Wallbox
  ausgeschlossen. Health zeigt die Indizien einer übersteuerten Wallbox trotzdem.
- **Widerspruch** ist: Ein Fahrzeug ist an zwei Wallboxen Kandidat, oder es ist nirgends Kandidat und
  hat an einer Wallbox 2 stützende Indizien und eins, das es ausschließt. Ein Fahrzeug, das an einer
  Wallbox Kandidat ist und an der anderen 2 Stützen und einen Ausschluss hat, widerspricht sich
  nicht: Ein dreiphasiger Gast an der EVCS neben dem ladenden Tesla am Wall Connector machte die
  Zuordnung sonst unklar, sobald zeitliche Korrelation und Phasen-Signatur den Tesla auch an der
  EVCS stützen, obwohl der Stromvergleich beide trennt.
- **Unklar** wird die Zuordnung, wenn ein Widerspruch 60 s ohne Unterbrechung besteht, und wieder
  klar nach 60 s ohne Widerspruch. Eine aktive Übersteuerung hebt „unklar“ sofort auf. Solange sie
  unklar ist, gilt alles wie vor der Erkennung: Jedes Fahrzeug ohne Übersteuerung steht an seiner
  Standard-Wallbox, sofern keine Übersteuerung sie belegt, und es steht kein Gast an einer Wallbox.
  Dann pausieren die Tesla-Befehle, und die EVCS behält ihren Strom; nur der Phasenwächter senkt
  ihn. Start und Stopp nach Modus entfallen. Der Hinweis steht in `GET /v1/health` und im
  Entscheidungslog (Grund „Zuordnung unklar“).

**Folgen für die Regelung.** Für einen Gast gilt das Gleiche, ob er erkannt ist oder „Gast,
angenommen“:

| Wer steht wo | Die Regelung |
|---|---|
| Tesla an der EVCS (erkannt oder übersteuert) | Mit jeder Ankunft an der EVCS bekommt der Tesla einmal `vehicles.tesla.max_a`, sobald er steuerbar und die EVCS geregelt ist; danach regelt die EVCS mit seinen Phasen und Grenzen in seinem Modus. Wechselt an derselben EVCS nur die Art (erkannt oder übersteuert), ist das keine Ankunft. |
| e-Golf am Wall Connector | Dafür gibt es keinen Aktor: Der e-Golf ist Last für Phasenwächter und 15-min-Wächter, für den Akku-Sollwert zählt sein Modus. Der Tesla-Aktor bekommt keine Befehle. |
| Gast an der EVCS | Die EVCS regelt wie für jedes Fahrzeug, im Modus `guest.mode`, mit `min_a` und `max_a` der EVCS und auf allen Phasen ihrer `phase_map`. In der Priorität steht der Gast hinter den Fahrzeugen; der 15-min-Wächter kann ihn drosseln. |
| Gast am Wall Connector | Last wie der e-Golf dort; für den Akku-Sollwert zählt `guest.mode`. Fällt der Wall Connector aus, rechnet der Phasenwächter mit dessen `max_a` als Zuschlag. |

**Grund `assignment_detected`.** Ab einer neuen Erkennung tragen die Befehle des Autos zuletzt den
Grund `assignment_detected` („Zuordnung erkannt“), bis zum ersten Zyklus mit Befehlen für das Auto,
der Höchststrom an den Tesla eingeschlossen. Neu erkannt ist ein Auto, das an seiner Wallbox erkannt
ist, wenn dort im vorigen Zyklus ein anderes Auto oder keins stand; ein angenommenes Auto, das die
Erkennung bestätigt, ist nicht neu erkannt. Ist das Auto vorher nicht mehr erkannt, entfällt der
Grund. Ein zurückgestellter erster Befehl kommt ohne den Grund wieder, und war der Tesla bei der
Erkennung nicht steuerbar, trägt sein späteres `max_a` ihn nicht. Im Entscheidungslog steht etwa:
`Tesla Model 3 (Sofort, Zuordnung erkannt): Strom 16 A – gesendet und bestätigt.`

**`home_entity` einrichten.** Home Assistant sagt dem core, ob ein Fahrzeug zu Hause ist (Indiz
`home`). Das ist vor allem für den e-Golf nötig, den Tessie nicht kennt; Home Assistant liefert von
ihm außer dem SOC nur „zu Hause“. Trage dazu in `config.yaml` beim Fahrzeug einen `device_tracker`
ein; der Zustand `home` heißt zu Hause:

```yaml
vehicles:
  egolf:
    # ...
    home_entity: device_tracker.egolf
```

- `home_entity` verlangt den Abschnitt `homeassistant` in `config.yaml`, sonst lehnt der core die
  Konfiguration ab: `vehicles.egolf.home_entity verlangt einen homeassistant-Abschnitt`. Der core
  liest Home Assistant mit `BINDAEMS_HA_TOKEN` aus der `.env`; ohne das Token bleibt der Wert
  unbekannt.
- Der core abonniert die Entität selbst und nur lesend (Signal `vehicle.<Schlüssel>.home_state`). Du
  trägst sie nicht zusätzlich unter `homeassistant.entities` ein. Nach einer Änderung startest du den
  core neu (Abschnitt 13, „Wartung“).
- Jeder Zustand außer `home` (`not_home`, eine Zone) heißt nicht zu Hause. `unknown`, `unavailable`
  und eine unterbrochene Verbindung zu Home Assistant heißen unbekannt: Das Indiz sagt dann nichts.
  Home Assistant meldet jede Änderung, ein alter Wert gilt nicht als veraltet (anders als bei
  Tessie, oben).
- Der `device_tracker` sollte das Ankommen binnen einer Minute melden. **Meldet er es spät,** gilt
  der e-Golf bis dahin als nicht zu Hause, also an der EVCS als ausgeschlossen: Dort steht ein
  **Gast, angenommen**, und die EVCS lädt im Modus des Gastes (`guest.mode`, Standard
  PV-Überschuss), nicht im Modus des e-Golf. Dasselbe gilt, solange ein Tracker zu Hause kurz
  `not_home` meldet.

**Gast.** Je Wallbox gibt es einen Gast, im Health `guest` mit dem Namen „Gast“; so steht ein Gast
nie an zwei Wallboxen. Der Gast hat keinen Eintrag in `config.yaml` und keine Tessie-Werte. Ein
Fahrzeug in `config.yaml` darf darum nicht `guest` heißen und nicht mit `guest@` beginnen; sonst
lehnt der core die Konfiguration ab (`vehicles.guest: Der Schlüssel ist dem Gast der Zuordnung
vorbehalten …`).

- **Modus:** Der Gast lädt im Modus `guest.mode` der Absicht, Standard PV-Überschuss; Boost und
  Übersteuerung gibt es für ihn nicht. Du wählst den Modus im UI (Seite „Laden“, Karte „Gast“) oder
  in Home Assistant („Gast-Lademodus“). Ohne app setzt du ihn per `PUT /v1/intent` (Zugang und
  Aufruf wie oben); die Absicht ist vollständig, nimm also die übrigen Einträge mit.

```json
{
  "version": 14,
  "control": true,
  "vehicles": {"tesla": {"mode": "min_pv"}, "egolf": {"mode": "pv"}},
  "guest": {"mode": "now"}
}
```

- **Wo:** An einer EVCS regelt der core den Gast wie jedes Fahrzeug (Tabelle oben). Am Wall
  Connector kann er ihn nicht regeln: Dort ist der Gast Last, und `guest.mode` zählt nur für den
  Akku-Sollwert.
- **Grenze:** Erkannt ist ein Gast erst, wenn an seiner Wallbox jedes Fahrzeug aus `config.yaml`
  ausgeschlossen ist. Bis dahin zeigt Health dort „Gast, angenommen“ oder das Standardfahrzeug.
  - **Neben einem zweiten Auto:** Ein Fahrzeug, das zu Hause angesteckt ist, schließen Tessie und
    Home Assistant nicht aus; an der Wallbox des Gastes tun das nur Strom oder Phasen. Ein
    dreiphasiger Gast an der EVCS neben dem Tesla, der am Wall Connector steckt und nicht lädt,
    bleibt so „Gast, angenommen“; lädt der Tesla mit einem anderen Strom, ist der Gast nach rund
    2 min erkannt, solange sich der Strom an der EVCS nicht ändert. Ändert die Regelung ihn oft
    (etwa alle 45 s), bleibt dort „Gast, angenommen“ (Stromvergleich nach einer Änderung, oben).
  - **Ein Gast mit zwei Phasen** sieht für den core aus wie der e-Golf, solange dieser laut Home
    Assistant zu Hause ist, denn der e-Golf meldet kein „angesteckt“. Steckt der Gast allein an einer
    Wallbox, ist er als e-Golf erkannt, auch wenn der e-Golf gar nicht angesteckt ist. Neben einem
    zweiten Auto gilt das ebenso, sobald dieses auf einer anderen Zahl von Phasen lädt und so den
    e-Golf an seiner Wallbox ausschließt (Elimination, oben). Sonst bleiben die Wallboxen
    angenommen; steckt etwa der Gast am Wall Connector und der e-Golf an der EVCS, steht an der
    EVCS der e-Golf angenommen und am Wall Connector ein Gast, angenommen.

**Übersteuerung (wie bisher).** Steht ein Fahrzeug an einer anderen Wallbox, als der core
annimmt, oder ist die Zuordnung unklar, setzt du `"wallbox"` bei dem Fahrzeug, etwa
`"wallbox": "evcs"` bei `tesla`. Das gilt bis zum Abstecken, geht der Erkennung vor und hebt „unklar“
sofort auf. Steht der Tesla so an der EVCS, setzt der core den Tesla-Strom einmal auf
`vehicles.tesla.max_a` und regelt nur noch die EVCS. Der e-Golf hat dann keinen Ladepunkt.

```json
{"version": 13, "control": true, "vehicles": {"tesla": {"mode": "now", "wallbox": "evcs"}}}
```

**Health.** `GET /v1/health` (Zugang wie in Abschnitt 4, „Diagnose“) nennt in `wallboxes` je
Wallbox, in der Reihenfolge von `config.yaml`, wer dort im letzten Zyklus stand und warum. Ein
Ausschnitt aus dem Simulator (`zuordnung`, 190 s nach Beginn): Der Tesla steckt an einer Steckdose,
an keiner Wallbox, und der e-Golf lädt an der EVCS auf zwei Phasen.

```json
{
  "wallboxes": [
    {
      "id": "evcs",
      "occupant": "egolf",
      "name": "e-Golf",
      "assignment": "unclear",
      "indicators": [
        {"vehicle": "tesla", "clue": "tessie", "supports": true},
        {"vehicle": "tesla", "clue": "phases", "supports": false},
        {"vehicle": "tesla", "clue": "elsewhere", "supports": true},
        {"vehicle": "egolf", "clue": "phases", "supports": true},
        {"vehicle": "egolf", "clue": "home", "supports": true}
      ]
    },
    {
      "id": "twc",
      "occupant": null,
      "name": null,
      "assignment": null,
      "indicators": []
    }
  ]
}
```

Tessie und der Ausschluss stützen den Tesla an der EVCS, die Phasen-Signatur (zwei Phasen) schließt
ihn dort aus: Er ist nirgends Kandidat und hat dort 2 Stützen und einen Ausschluss, das ist ein
Widerspruch. Er dauerte 60 s ohne Unterbrechung, darum ist die Zuordnung unklar, und an der EVCS
steht der e-Golf als Standardfahrzeug. Am Wall Connector läuft kein Vorgang.

| Feld | Bedeutung |
|---|---|
| `wallboxes[].id` | der Schlüssel der Wallbox in `config.yaml` |
| `wallboxes[].occupant` | der Schlüssel des Fahrzeugs in `config.yaml`, `guest` für einen Gast, `null` ohne Vorgang. Auch `null`, solange die Zuordnung unklar ist und an der Wallbox kein Fahrzeug aus `config.yaml` steht (`assignment` ist dann `unclear`). |
| `wallboxes[].name` | der Name des Fahrzeugs (`name` in `config.yaml`), `Gast` oder `null` |
| `wallboxes[].assignment` | `detected` (erkannt), `default` (angenommen), `override` oder `unclear`; `null` ohne Vorgang |
| `wallboxes[].indicators` | die Indizien des letzten Zyklus an dieser Wallbox, je `vehicle` (Schlüssel aus `config.yaml`), `clue` (`tessie`, `current`, `timing`, `phases`, `home` oder `elsewhere`) und `supports` (`true`: stützt das Fahrzeug an der Wallbox, `false`: schließt es dort aus). Die Fahrzeuge stehen wie in `config.yaml`, je Fahrzeug die Indizien in der Reihenfolge von „Indizien“ oben. Stromvergleich und Phasen-Signatur erscheinen erst nach ihren 60 s. Leer ohne Vorgang. |
| `vehicles[].assignment` | `detected`, `default`, `override` oder `unclear`; `null` vor dem ersten Zyklus und nach einer Pause der Regelung. `vehicles` nennt nur die Fahrzeuge aus `config.yaml`; einen Gast zeigt `wallboxes`. |

Vor dem ersten Zyklus und nach einer Pause der Regelung (OBSERVE, Regelbetrieb aus, SAFE) sind
`occupant`, `name` und `assignment` `null`, und `indicators` ist leer.

**Inbetriebnahme.** Die Erkennung läuft im Dry-Run mit (Design 2c-2, 11.1, Punkt 3): Sie liest nur.
Die Befehle, die aus ihr folgen (der Höchststrom an einen Tesla an der EVCS, die Regelung eines
Gastes), stehen als Dry-Run-Entscheidungen im Log, mit dem Grund „Zuordnung erkannt“. Health zeigt,
wer wo steht, solange der core regelt (Abschnitt „Wann der core regelt“: CONTROL oder DEGRADED und
`"control": true` in der Absicht); sonst ruht die Regelung, und `wallboxes` zeigt niemanden. Prüfe in
den ersten Tagen:

1. **Steht an jeder Wallbox mit Auto das richtige?** (`wallboxes[].occupant`). Ein Fahrzeug an seiner
   Standard-Wallbox zeigt „angenommen“, bis genug Indizien reichen: Der e-Golf braucht neben `home`
   die Phasen-Signatur, also 60 s Laden und danach 60 s Kandidatur. Ein Auto, das nicht lädt, bleibt
   „angenommen“, wenn keine zwei Indizien es stützen. Stecken an beiden Wallboxen Autos, stützen
   Tessie und Home Assistant erst, wenn Strom oder Phasen das Fahrzeug an der anderen Wallbox
   ausschließen (Elimination, oben); bis dahin bleibt es „angenommen“. Das ändert die Regelung nicht.
2. **Bleibt „unklar“ aus?** Zeigt Health `unclear`, lies `indicators`: Welches Fahrzeug hat 2
   stützende Indizien und eins, das es ausschließt? Bis die Ursache klar ist, hilft eine
   Übersteuerung (oben).
3. **Wie schnell meldet `home_entity` das Ankommen des e-Golf?** Steht nach dem Anstecken an der
   EVCS zuerst ein Gast, meldet der Tracker zu spät (oben).
4. **Tesla an der EVCS, e-Golf am Wall Connector, Gast:** Steckt eines davon an, prüfst du, ob Health
   es erkennt. Rechne mit einigen Minuten: Die Erkennung braucht 60 s ohne Unterbrechung, und Tessie
   meldet ein neu angestecktes Auto bis zu 10 min verspätet. Steckt an der anderen Wallbox ein
   weiteres Auto, das nicht lädt, zeigt Health den Gast oft als „Gast, angenommen“ statt erkannt,
   und den e-Golf am Wall Connector erkennt der core erst, wenn die EVCS auf einer anderen Zahl von
   Phasen lädt (Elimination oben, Grenze unter „Gast“).

### Tesla

- **Strom:** Der Tesla folgt dem Mittel des Wunschstroms über `control.tesla_average_s` (90 s) und dem
  niedrigsten freien Wert der letzten `control.tesla_caution_s` (60 s), nimmt davon den kleineren
  Wert und rundet ab. Die Safety-Schicht lässt eine Änderung ab 2 A Unterschied und höchstens alle
  90 s zu. Die schnellen Schwankungen fängt die EVCS ab.
- **Stundenlimit:** Der Tesla bekommt höchstens 20 Befehle je Stunde, davon höchstens 6 Start/Stopp
  (`vehicles.<name>.commands`: `per_hour`, `start_stop_per_hour`), gezählt in einem gleitenden
  Fenster, das einen Neustart übersteht. Unter schnellen Wolken kann das Budget aufgebraucht sein
  (der Simulator zeigt es in `wolkig` bis zur Reserve unten): Weitere Befehle, auch ein Start oder
  der Befehl eines Moduswechsels, werden dann zurückgestellt, bis ein Platz frei wird. Das
  Entscheidungslog zeigt sie als „zurückgestellt“ (Befund `rate.per_hour` bzw. `rate.start_stop`,
  dieselbe Zurückstellung höchstens einmal je Minute). Nur dringende Befehle umgehen das Limit
  („Phasenwächter und Überlast“, oben). Damit es nicht so weit kommt, gilt seit Plan 2d die Reserve
  (nächster Punkt).
- **Reserve** (seit Plan 2d): Die Regelung hält `control.tesla_reserve_commands` (4) Befehle je Stunde
  für den Tesla frei. Ist das Stundenbudget bis auf diese Reserve verbraucht (mit `per_hour` 20 bei
  16 Befehlen), sendet sie ihm einen Strombefehl nur noch, wenn er den Strom senkt und der Ist-Strom
  über der Grenze des Phasenwächters liegt. Starts, Stopps, der Höchststrom „Tesla an der EVCS“ und
  dringende Befehle sind davon frei; die Limits der Safety-Schicht gelten weiter. Ein
  zurückgehaltener Strombefehl steht nicht im Log, wie bei der 90-s-Regel. Die Befehle der letzten
  Stunde zeigt die Seite „Laden“ (`vehicles[].commands_hour`). Der Wert muss unter `per_hour`
  jedes Fahrzeugs mit Tessie liegen, sonst lehnt der core die Konfiguration ab.
- **„Aus“ wirkt sofort** (seit Plan 2d): Ein Stopp, weil der Modus „Aus“ gilt, ist dringend
  (Befund `urgent.bypass`): Er umgeht die Mindestabstände und die Stundenlimits, auch das
  Start/Stopp-Limit. Das gilt für den Tesla und die EVCS. Vorher hätte ein aufgebrauchtes Budget den
  Stopp zurückgestellt (in einer Probe im Simulator wirkte „Aus“ erst nach 19 min).
- **Selbststart** (seit Plan 2d): Lädt ein Fahrzeug mit Tessie laut Tessie (mindestens 1 A) an einem
  Ladepunkt, den die Regelung regelt, `control.tesla_against_s` (120 s) ohne Unterbrechung gegen
  seinen Modus (der Modus ergibt „nicht laden“) und ist die Zuordnung klar, sendet die Regelung „Laden
  aus“ erneut, mit dem Grund `self_start` („Selbststart“). Das geschieht je Fahrzeug höchstens alle
  `control.tesla_against_retry_s` (600 s). Der Befehl zählt im Start/Stopp-Budget, außer im Modus
  „Aus“ (dort ist er dringend). Startet das Auto also selbst, etwa aus der Tesla-App, stoppt die
  Regelung es binnen rund 3,5 min wieder (Szenario `tesla-selbststart`).
- **Ansteckerkennung** (seit Plan 2d): Tessie fragt ein Auto alle 30 s ab, solange es lädt, alle 60 s,
  solange es angesteckt oder zu Hause ist, sonst alle 600 s. Erscheint an einer Wallbox ein
  Fahrzeug, fragt der core sofort nach, frühestens 10 s nach der letzten Abfrage. Ein Tesla, der beim
  Anstecken selbst zu laden beginnt, läuft so nicht mehr bis zu 10 min ungeregelt.
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

1. Images von Plan 2d ausrollen (Abschnitt 4); `live_allowed` bleibt `false`.
2. Den Regelbetrieb einschalten: im UI (Seite „Laden“ oder Karte „Bedienung“ unter **System**) oder in
   Home Assistant; ohne app mit einer Absicht `{"version": …, "control": true}` (oben). Nach 30 s
   frischen Messwerten geht der core nach CONTROL, alle Aktoren bleiben im Dry-Run.
3. Entscheidungen lesen: im UI auf der Seite „Entscheidungen“ (Filter nach Aktor, Ergebnis und Text) oder
   über die Dateien bzw. `GET /v1/decisions` (Zugang wie in Abschnitt 13, „Entscheidungslog“). Das
   Beispiel listet mit
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
  Tesla dort abbricht, bis das Budget wieder Platz hat. Seit der Reserve (oben, „Tesla“) kommt es kaum
  noch dazu: Im Simulator in `dry-run` waren es 16 Strom-Entscheidungen an den Tesla in der Stunde und
  keine Zurückstellung, denn die Regelung hielt die weiteren zurück, ohne Eintrag im Log. Die EVCS
  folgt dem Überschuss in ganzen Ampere mit
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
| `assignment_detected` | Zuordnung erkannt | Der Befehl folgt aus einer neuen Erkennung: Der core hat ein Auto an seiner Wallbox erkannt, etwa den Höchststrom an den Tesla an der EVCS (oben). |
| `resume_after_outage` | Wiederaufnahme nach Ausfall | Der erste Start nach einer Pause der Regelung (SAFE, Inselbetrieb, Regelbetrieb aus). |
| `self_start` | Selbststart | Ein Fahrzeug mit Tessie lädt `control.tesla_against_s` lang gegen seinen Modus; die Regelung sendet „Laden aus“ erneut (Tesla, oben). |
| `foreign_change` | Änderung von außen | Nur bei einer Entscheidung mit dem Ergebnis „von außen geändert“ (Abschnitt 13, „Sperren“): Ein anderes System hat den Wert am Gerät geändert. |

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
und den letzten Grund. Je Wallbox (`wallboxes`) zeigt es, wer dort steht, die Art der Zuordnung und
die Indizien (oben, „Zuordnung Auto und Wallbox“).

### Health für die Bedienung

Neben den Feldern oben stehen in `GET /v1/health` diese, die die Bedienung braucht:

| Feld | Bedeutung |
|---|---|
| `intent_version` | die Version der Absicht, mit der die Regelung im letzten Zyklus regelte; `null`, solange sie ruht. Die app bereinigt die Absicht (Abschnitt 13, „Absicht und Regelbetrieb“) nur nach einem Health, dessen `intent_version` mindestens die gültige Version erreicht; sonst gilt der Stand als unbekannt. |
| `vehicles[].boost_pending` | `true`: Ein Boost der Absicht gilt, wartet aber auf einen Ansteckvorgang, der noch nicht begonnen hat. `boost_until` zeigt nur einen aktiven Boost. Die Seite „Laden“ zeigt „wartet auf Anstecken“. |
| `vehicles[].override_pending` | dasselbe für die Übersteuerung der Wallbox: Sie gilt, wartet aber auf einen Ansteckvorgang. `assignment` zeigt `override` erst, wenn sie wirkt. |
| `vehicles[].commands_hour` | die Befehle der letzten Stunde im gleitenden Fenster der Safety-Schicht, nur für Fahrzeuge mit Tessie, sonst `null` |
| `watchdog.armed` | der Watchdog am Cerbo ist für diesen core scharf (Abschnitt 13, „Gate“) |
| `actuators[].watchdog_needed` | `true` bei Victron und EVCS: Diese Aktoren brauchen den scharfen Watchdog, um live zu gehen |

Der Live-Dialog (Abschnitt 13, „Dry-Run und live“) zeigt „Watchdog scharf“ aus `watchdog.armed` und
`watchdog_needed`, „live erlaubt“ aus `actuators[].live_allowed`.

### Umstieg von evcc

Heute regelt evcc beide Ladepunkte, nicht den Victron; BindaEMS löst es ab. Die Live-Schaltung geht
mit dem Live-Schalter im UI (Spec 8.7), und zwar so:

1. Der Dry-Run lief 2 Wochen, und die Entscheidungen sind gemeinsam gesichtet (Spec 18).
2. Das Prüfprotokoll Teil 2 ist für EVCS und Tesla bestanden, bei der EVCS einschließlich des
   Schritts „Verbindung unterbrochen“, und der Watchdog ist scharf (Abschnitt 14).
3. `live_allowed: true` steht für `wallboxes.evcs` und `vehicles.tesla` (Abschnitt 13).
4. **Beide Ladepunkte gehen gemeinsam live, und evcc wird ganz abgeschaltet,** nicht nur einer.
   Beide Autos teilen sich Überschuss und Phasenwächter; ein Regler für das eine Auto und evcc für
   das andere würden um denselben Überschuss konkurrieren. Schreibt evcc nach der Live-Schaltung
   weiter, erkennt der core die fremde Änderung und sperrt den Aktor nach 3 Prüfungen im Abstand von
   10 s (Abschnitt 13, „Sperren“). Im Dialog „Live schalten“ (**System** → „Bedienung“, als Admin)
   wählst du `wallbox.evcs` und `vehicle.tesla` in einem Zug, bestätigst Prüfprotokoll Teil 2 und
   „evcc ist ganz abgeschaltet.“ und gibst dein Passwort ein (Abschnitt 13, „Dry-Run und live“).
5. Victron geht getrennt davon live, sobald Dynamic ESS aus ist und die Schritte im Abschnitt „Akku
   und Spitzen“ („Victron in den Dry-Run und live“) erledigt sind. Der Akku-Sollwert rechnet auch
   neben evcc mit den Modi aus der Absicht von BindaEMS.

Zurück zu evcc: beide Aktoren im UI mit „Auf Dry-Run…“ auf Dry-Run stellen, die Übergabe abwarten
(Abschnitt 13) und erst dann evcc wieder starten.

### Hinweise für die Anlage

- **Ein schlafender Tesla, den der core gerade übergibt,** hält die Übergabe offen, bis er aufwacht.
  Solange kann der Alarm „… Sicherwert für … nicht bestätigt.“ (`actuator.failed.vehicle.tesla`)
  anstehen (Abschnitt 14, „Meldungen des core“).
- **Schnell wechselnde Wolken:** Ohne Akku-Sollwert (Victron im Dry-Run) deckt dann der Hausakku
  einen spürbaren Teil der PV-Ladung des Tesla. Der Tesla darf seinen Strom nur alle 90 s ändern,
  und ein Wechsel auf oder von 5 A braucht zwei Befehle; das nimmt das Design in Kauf (Design 6.5).
  Im Simulator (`wolkig`, Victron im Dry-Run, Wolken im Wechsel alle 30 bis 180 s) kamen so rund
  40 % der Ladeenergie des Tesla aus dem Akku. Das ist der rohe Anteil: Die Kennzahl „Akku-Energie
  in PV-Autos“ nimmt die Übergänge aus und zeigt dort 0 %, denn jede dieser Entladungen fällt in
  einen Übergang. Den rohen Anteil aller PV-Autos (dort 18 %) nennt der Bericht als Informationszahl
  (Abschnitt „Simulator“). Der e-Golf folgt den Wolken binnen Sekunden. Mit Victron live verschiebt
  der Akku-Sollwert den Fehlbetrag des Tesla aufs Netz, solange die Unterstützung nicht gilt
  (Abschnitt „Akku und Spitzen“).

**Vor der Live-Schaltung von EVCS und Tesla** waren diese Punkte zu entscheiden oder umzusetzen; die
Punkte 1 bis 3 hat Plan 2d umgesetzt, 5 und 8 mit einer Entscheidung abgeschlossen (je mit Befund und
Abhilfe; ausführlicher in `docs/uebergabe.md`):

1. **Stundenbudget des Tesla** (erledigt mit Plan 2d): Unter Wolken verbrauchte die Regelung die 20
   Befehle je Stunde; danach wurden auch ein Stopp und ein Moduswechsel zurückgestellt (in einer
   Probe im Simulator wirkte „Aus“ erst nach 19 min). Jetzt hält die Regelung 4 Befehle frei
   (`control.tesla_reserve_commands`), und der Stopp in „Aus“ ist dringend (oben, „Tesla“).
2. **Selbststart im laufenden Vorgang** (erledigt mit Plan 2d): Startet das Auto selbst (etwa aus
   der Tesla-App), sendet die Regelung nach 120 s „Laden aus“ erneut, höchstens alle 600 s (oben,
   „Tesla“; Szenario `tesla-selbststart`).
3. **Ansteckerkennung des Tesla** (erledigt mit Plan 2d): Tessie fragt ein abgestecktes Auto alle
   60 s ab, solange es zu Hause ist, und sofort, wenn an einer Wallbox ein Fahrzeug erscheint (oben,
   „Tesla“).
4. **Ausnahme in der Regelung** (erledigt mit Plan 2c-2a): Die Verdrahtung der Regelung fängt die
   Ausnahme und lässt die Regelung ruhen; Keeper, Stream, Alarme und Entscheidungslog laufen weiter,
   der Heartbeat bleibt aus, und der Alarm `control.exception` steht (Abschnitt „Akku und Spitzen“,
   „Alarme“).
5. **5 A beim Tesla:** Befehle auf oder von 5 A gehen über Tessie beim ersten Mal verloren und
   kosten einen zweiten Befehl. Seit Plan 2c-2a kommt hinzu: Startet der Tesla nachts in Min+PV mit
   16 A, liefert der Akku 90 s lang bis zu 5,8 kW in die Autos (Abschnitt „Akku und Spitzen“,
   „Hinweise für die Anlage“). Entscheidung (Plan 2d): Der Code bleibt bei 5 A (Design 7.2). Die
   Empfehlung für die `config.yaml` der Anlage ist `vehicles.tesla.min_a: 6`; das entscheidet der
   Betreiber dort.
6. **Simulator** (erledigt mit Plan 2c-2a): Das Szenario `schieflast-pv` lässt das Importziel
   binden (Abschnitt „Simulator“).
7. **Technische Schulden vor 2c-2** (erledigt mit Plan 2c-2a): Ladepunkte, Verteilung, Tesla-Teil
   und die Merker der letzten eigenen Werte liegen in eigenen Modulen von `core/control`, die
   Verdrahtung der Regelung in `core/regulation_link.py`, und die doppelten Hilfen sind
   zusammengeführt (Zeilenzahlen: `docs/uebergabe.md`).
8. **Sicherer Zustand bei Netzausfall:** Der core sendet dem Tesla „Laden aus“ alle 10 s erneut, bis
   der Tessie-Cache es zeigt; beim Laden dauert das bis zu 30 s und kostet bis zu 3 der 6
   Start/Stopp-Befehle je Stunde und alle 3 Versuche des Vorfalls. Entscheidung (Plan 2d):
   unverändert. Ein Netzausfall ist selten, und die dringenden Befehle des sicheren Zustands umgehen
   die Limits der Safety-Schicht schon (Spec 8.5).

**Im Dry-Run an der Anlage prüfen:**

- welcher EVCS-Status bei angestecktem Auto auf „Laden aus“ (`StartStop` = 0) folgt: Ist es 3
  („geladen“), bliebe ein Auto in PV nach dem ersten Stopp stehen, bis es abgesteckt wird;
- welche Phasenzahl Tessie (`charger_phases`) meldet, während der Tesla dreiphasig lädt: Weicht
  sie von `vehicles.tesla.phases` ab, gibt es für den Tesla keinen Stromvergleich (`current`);
- ob ein Tesla, der den Wall Connector verlässt, während der e-Golf an der EVCS steckt und nicht
  lädt, dort nie erkannt wird: Die EVCS bleibt „e-Golf, angenommen“, der Tesla wird nicht erkannt,
  und nach ≤ 60 s schließt Tessie ihn aus (`tessie` mit `supports: false`; Tessie meldet
  `plugged` bis zu 60 s veraltet). Die Indizien stehen in `GET /v1/health` unter `wallboxes`;
- ob der Tesla, der am Wall Connector lädt, dort richtig bleibt, wenn die Regelung seinen Strom
  ändert: Er erscheint dort nie als Gast (`guest`), die Zuordnung nie als `unclear`, und `current`
  schließt ihn dort nie aus (`supports: false`);
- ob ein Gast an der EVCS nach Stromänderungen erkannt bleibt und nicht zum e-Golf wechselt: Der
  core leitet die Phasenzahl der EVCS aus Leistung und Strom ab, die nicht gleichzeitig gemessen
  werden, und schon ein abweichender Zyklus startet die 60 s der Phasen-Signatur (`phases`) neu;
- ob die Reserve und der Selbststart des Tesla an der Anlage greifen: Zeigt die Seite „Laden“ bei
  `commands_hour` über 16 Befehle je Stunde (mit `per_hour` 20), hält die Reserve Strombefehle zurück,
  und ein Tesla, der selbst startet, bekommt nach rund 3,5 min eine Entscheidung mit dem Grund
  „Selbststart“;
- ob die Zuordnung stimmt: wer laut `wallboxes` an jeder Wallbox steht, wann ein Tesla an der EVCS,
  ein e-Golf am Wall Connector oder ein Gast erkannt wird und wie schnell `home_entity` das Ankommen
  des e-Golf meldet (oben, „Zuordnung Auto und Wallbox“, „Inbetriebnahme“);
- wie oft die EVCS ihren Strom ändert: Folgt sie bei ruhiger Sonne trotz der Hysterese noch dem
  Rauschen (deutlich mehr Strom-Entscheidungen an `wallbox.evcs` als im Simulator, etwa über 10 je
  Stunde), hebe `control.evcs_hysteresis_a` an (oben, „Lademodi“).

Die drei Prüfungen zur Zuordnung (Abfahrt des Tesla, Tesla am Wall Connector, Gast an der EVCS)
sind Voraussetzung, bevor EVCS und Tesla mit dem Live-Schalter live gehen: Ein Befund dort heißt, dass die
Regelung das falsche Auto oder den Tesla gar nicht regelt.

## Akku und Spitzen

Seit Plan 2c-2a regelt der core auch den **Akku** und die **Spitzen**. Er setzt den
Netz-Sollwert-Override von Victron (`/Overrides/Setpoint` am Dienst `com.victronenergy.hub4`, kurz
„Override“) so, dass der Hausakku die Autos nur nach festen Regeln versorgt (Akku-Sollwert), und hält
auf Wunsch ein Ziel für den Netzbezug je Viertelstunde (15-min-Wächter). An Victron schreibt er dabei
nur den Override; Entladegrenze und Min-SOC setzt weiter nur der sichere Zustand und die Übergabe
(Abschnitt 13). Jeder Befehl geht durch die Safety-Schicht. **Solange Victron im Dry-Run ist, sendet
der core nichts** und protokolliert nur, was er täte. Live geht Victron erst nach den Schritten unter
„Victron in den Dry-Run und live“ (unten) und nur mit abgeschaltetem Dynamic ESS (DESS).

Die Einstellungen stehen in `config.yaml` unter `control` und haben Standardwerte
(`deploy/config.example.yaml`):

| Schlüssel | Standard | Bedeutung |
|---|---|---|
| `control.support_soc_pct` | 70 | Ab diesem SOC in % stützt der Akku Autos in PV-Überschuss und Min+PV. |
| `control.support_hysteresis_pct` | 5 | Gesperrt wird diese Unterstützung erst unter der Schwelle minus diesem Wert. |
| `control.support_max_w` | 3000 | Höchstens so viel Akkuleistung in W für diese Autos zusammen. |
| `control.support_max_s` | 900 | Längste Überbrückung am Stück in s. |
| `control.setpoint_deadband_w` | 200 | Totband des Overrides in W: der Abstand, ab dem ein Override entsteht oder sich ändert (unten). |
| `control.override_refresh_s` | 240 | So oft in s frischt der core einen aktiven Override auf; höchstens 280, denn der Cerbo verwirft ihn nach 300 s. |
| `control.peak_target_kw` | keiner | Ziel des 15-min-Wächters in kW; ohne Wert ruht er. |
| `control.peak_margin_kw` | 0,3 | Marge der Hochrechnung des Wächters in kW. |
| `control.reserve_guard_pct` | 5 | Abstand über der USV-Reserve in Prozentpunkten SOC. Darunter richten sich auch Sofort und Boost am Importlimit aus (Abschnitt „Laderegelung“), und der Wächter gibt dem Akku kein Budget. |
| `control.tesla_reserve_commands` | 4 | So viele Befehle je Stunde hält die Regelung für den Tesla frei; ist das Stundenbudget bis auf diese Reserve verbraucht, ändert sie seinen Strom nur noch, wenn der Phasenwächter es verlangt (Abschnitt „Laderegelung“, „Tesla“). Ganze Zahl, kleiner als `commands.per_hour` jedes Fahrzeugs mit Tessie. |
| `control.tesla_against_s` | 120 | So lange in s lädt ein Fahrzeug mit Tessie ohne Unterbrechung gegen seinen Modus, bevor die Regelung „Laden aus“ erneut sendet (Selbststart). |
| `control.tesla_against_retry_s` | 600 | Frühestens so viele s später folgt je Fahrzeug der nächste Versuch, solange es gegen den Modus lädt. |

Negative, unendliche und ungültige Werte lehnt der core beim Start ab; `support_soc_pct` liegt
zwischen 0 und 100, `setpoint_deadband_w` und `peak_target_kw` über 0 (ohne Totband entstünde auch
ohne gewollten Netzbezug ein Override), `override_refresh_s` über 0 und höchstens 280. Nach einer
Änderung startest du den core neu (Abschnitt 13, „Wartung“). **Keines dieser Felder verlangt eine
neue `config.ini` des Watchdogs** (Abschnitt 14): Er kennt nur USV-Reserve, sichere Ströme und Zeiten.
Anders ist es mit `victron.live_allowed` (unten, „Victron in den Dry-Run und live“).

### Akku-Sollwert

Der Override ist der Netzbezug, den der core haben will. ESS regelt den Netzbezug auf ihn, und der
Akku gleicht alles Übrige aus. Ohne Override gilt der Eigenverbrauch: Der Akku deckt Haus und Autos,
so weit er darf.

- **Grundsatz:** Wo der Akku nicht ins Auto laden soll, verschiebt der core den Teil der
  Autoleistung, den sonst der Akku lieferte, aufs Netz: den **Fehlbetrag**. Er setzt den Override
  nicht auf die ganze Ladeleistung der Autos, denn das holte auch den Teil aus dem Netz, den die PV
  deckt, und der Akku lüde dafür: Bei 6 kW PV, 1 kW Haus und 5 kW Auto gäbe ein Override von 5 kW
  5 kW Netzbezug, und der Akku lüde 5 kW. Der Override ist der Basis-Sollwert
  (`ess.grid_setpoint_w`) plus der gewollte Netzbezug, auf 10 W gerundet; ist der Basis-Sollwert
  unbekannt, kennt der core auch das Ziel nicht.
- **Fehlbetrag:** Die PV deckt zuerst die Hauslast (ohne Wallboxen), dann die Autos in der Reihenfolge
  `priority`, wie die Abrechnung. Was einem Auto danach fehlt, ist sein Fehlbetrag: die Leistung, die
  es aus Akku oder Netz bezieht. Gemessen wird an der Wallbox; fehlt ihr Wert bei einem Fahrzeug mit
  Tessie, rechnet der core ihn aus dem Ist-Strom, den Phasen und der Nennspannung. Der **gewollte
  Netzbezug** ist die Summe über die Autos von Fehlbetrag minus erlaubtem Akku-Anteil, nie unter 0;
  die Hauslast deckt der Akku immer.
- **Welche Autos zählen:** erst die Autos mit Ladepunkt nach `priority`, erst die Fahrzeuge, dahinter
  die Gäste (Abschnitt „Zuordnung Auto und Wallbox“), dann jede Wallbox ohne Ladepunkt, an der
  niemand steht, in der Reihenfolge von `config.yaml`. Es zählt der geltende Modus aus der Absicht
  von BindaEMS (mit Boost), auch solange evcc die Ladepunkte regelt. Für einen Gast und für eine
  Wallbox, an der niemand steht, gilt der Modus des **Gastes** (`guest.mode`, Abschnitt „Absicht per
  PUT /v1/intent“).

Den **erlaubten Akku-Anteil** bestimmt der Modus des Autos:

| Modus | Aus dem Akku erlaubt |
|---|---|
| PV-Überschuss, Min+PV | Wenn die Unterstützung gilt (unten): für diese Autos zusammen höchstens `support_max_w`, verteilt in der Reihenfolge `priority`. Sonst nichts. |
| Sofort, Aus | Nichts. Sofort lädt aus Netz und PV mit vollem Strom. |
| Boost | Alles, ohne SOC-Schwelle und ohne die Grenzen von Leistung und Dauer. Boost begrenzt nur der Schutz der USV-Reserve (Abschnitt „Lademodi“). |

- **Sofort** bleibt beim vollen Strom. Regel 2 der Safety-Schicht begrenzt den Override auf
  `victron.max_grid_charge_setpoint_w` (8000 W), die Absicherung und das Budget des Wächters; was
  darüber hinausgeht, liefert der Akku. Laden zwei Autos in Sofort zusammen mehr als 8 kW, kommt der
  Rest aus dem Akku (im Simulator, `akku-sofort`: 8 kW Netzbezug). Wer das nicht will, hebt
  `max_grid_charge_setpoint_w` bis zum Importlimit an. Über dem Importlimit (20 A je Phase) liefert
  Victron ohnehin aus dem Akku (Peak Shaving). Mit Budget für den Akku zählt der 15-min-Wächter den
  gewollten Netzbezug deshalb nur bis zu dieser festen Grenze (unten, „Hochrechnung und Senkung“):
  Was der Akku darüber ohnehin liefert, drosselt er nicht, und liegt `P_zul` darunter, senkt er die
  Autos nur einmal um den Unterschied. Ohne Budget (SOC nicht über USV-Reserve +
  `reserve_guard_pct` oder unbekannt) hält Regel 2 den Override ebenfalls bei der festen Grenze:
  Liegt `P_zul` darunter, drosselt der Wächter die Autos um alles, was über `P_zul` liegt, sonst
  gar nicht.
- **Unterstützung** heißt: Der Akku liefert Autos in PV-Überschuss und Min+PV ihren Fehlbetrag, bis
  zu `support_max_w` (3 kW). Sie gilt, wenn
  - ein Auto in einem dieser Modi steht (sonst zeigt Health `mode`),
  - der SOC `support_soc_pct` (70 %) erreicht hat und seitdem nicht unter `support_soc_pct −
    support_hysteresis_pct` (65 %) gefallen ist (sonst `soc`); ein unbekannter SOC sperrt sie im
    Zyklus, ändert den Stand der Hysterese aber nicht, und
  - die Überbrückung noch keine `support_max_s` (900 s) dauert (sonst `time`).
- **Überbrückung:** Sie beginnt mit dem ersten Zyklus, in dem die Autos in PV-Überschuss und Min+PV
  zusammen mindestens 100 W Fehlbetrag haben (die Hälfte des Totbands), und endet nach 60 s ohne.
  Nach `support_max_s` ist die Unterstützung gesperrt, bis sie endet. In PV-Überschuss senkt die
  Laderegelung dann ohnehin auf den echten Überschuss, in Min+PV kommt der Mindeststrom aus dem Netz.
- **Unter der Schwelle** (SOC unter 70 %) holt der core den Fehlbetrag aus dem Netz: Der Akku bleibt
  aus dem Auto heraus, etwa in den Lücken unter Wolken (im Simulator, `akku-wolkig`: 0,06 % der Energie
  des e-Golf kommen aus dem Akku).
- **Nach einer Pause** der Regelung (Regelbetrieb aus, SAFE, OBSERVE) beginnt der Stand neu: Der SOC
  muss `support_soc_pct` wieder erreichen.
- **Totband** (`control.setpoint_deadband_w`, 200 W): Ein Override entsteht erst, wenn der gewollte
  Netzbezug mindestens 200 W erreicht, und fällt weg („kein Override“), sobald er unter die Hälfte
  (100 W) sinkt. Dazwischen ändert er sich erst, wenn der neue Wert mindestens 200 W vom letzten
  eigenen abweicht. Die Safety-Schicht verlangt nur 50 W Unterschied; die 200 W halten das Rauschen
  der Hauslast vom Override fern.
- **Auffrischen** (`control.override_refresh_s`, 240 s): Der Cerbo verwirft einen Override nach 300 s
  ohne Schreiben. Der core sendet einen aktiven Override deshalb alle 240 s erneut, auch mit gleichem
  Wert, gezählt ab dem letzten Urteil mit Wert.
  - Die Safety-Schicht lässt das Auffrischen trotz gleichen Werts durch: Weder „unverändert“ noch das
    Totband von 50 W gelten für es. Die Regeln 2 und 4 ändern den Override von Zyklus zu Zyklus
    leicht, und ein Auffrischen, das der Cerbo um weniger als 50 W anders hält, bliebe sonst
    „unverändert“, und der Override verfiele. Sperren, Betriebszustand, Betriebsart, die Regeln 1 bis 4
    und der Mindestabstand von 2 s gelten wie für jeden Befehl. Ein Auffrischen ist nie dringend.
  - Ein Auffrischen steht **nicht im Entscheidungslog**, wenn es gelingt (`ok`) oder im Dry-Run ein
    Urteil ergibt. Abgelehnt, zurückgestellt oder gescheitert steht es dort wie jeder Befehl. Das Log
    zeigt so nur Änderungen des Overrides: Unter schnell wechselnden Wolken sind es im Simulator
    (`akku-wolkig`) rund 67 Entscheidungen je Stunde (zwei bis drei je Wolke), bei gleichbleibender
    Last wenige (`override-stunde`: höchstens 5 in einer Stunde).
  - Ein zurückgestelltes oder blockiertes Auffrischen folgt im nächsten möglichen Zyklus. Scheitert
    das Schreiben, sendet der core den Wert danach neu, als normalen Befehl.
  - Die Erkennung fremder Änderungen (Abschnitt 13, „Sperren“) erwartet einen Override nur 300 s
    nach der Bestätigung; jedes Auffrischen erneuert die Erwartung. Verfällt der Override gewollt
    (nächste Punkte), ist das keine fremde Änderung.
- **Victron gesperrt oder nicht steuerbar** (Dynamic ESS an, ein anderes System regelt, der Cerbo ist
  nicht verbunden; Abschnitt 13, „Sperren“): Der Akku-Sollwert sendet nichts, auch im Dry-Run nicht,
  sonst stünde neben DESS jede Sekunde eine Ablehnung im Log, und vergisst seine Werte. Bedarf,
  Unterstützung und Ziel rechnet er für Health weiter (`battery.state` `locked`). Ein vorhandener
  Override verfällt nach 300 s von selbst; das ist der gewollte Rückfall auf den Eigenverbrauch. Nach
  der Sperre beginnt der Akku-Sollwert ohne eigenen Wert. Ein offener Schreibvorgang für den Override
  oder eine Übergabe von Victron hält nur den Befehl dieses Zyklus zurück.
- **Fehlende Werte:** Fehlt die PV, die Hauslast oder die Leistung eines Autos, von dem nicht sicher
  ist, dass es nicht lädt, ist der gewollte Netzbezug unbekannt (ein Auto, das sicher nicht lädt,
  zählt ohne Messwert mit 0 W). Dann geht kein Befehl hinaus, auch kein Auffrischen (`battery.state`
  `unknown`), und ein Override verfällt nach 300 s, wenn das anhält. Kommen die Werte zurück, folgt
  der Override ohne Sprung über „kein Override“.
- **Die Regelung ruht** (START, OBSERVE, SAFE und bei ausgeschaltetem Regelbetrieb, auch während der
  Übergabe danach): Der Akku-Sollwert sendet nichts. Sicherer Zustand und Übergabe bleiben beim Keeper,
  der den Override aufhebt (Abschnitt 13); ist der core ausgefallen, tut es der Watchdog am Cerbo
  (Abschnitt 14).

### 15-min-Wächter

Der Wächter misst den Netzbezug jeder Viertelstunde (:00, :15, :30, :45, Wiener Zeit), rechnet ihn
hoch und hält ein Ziel, wenn eins eingestellt ist (Spec 7.4). Er verhindert keine Spitze um jeden
Preis: Er drosselt die Wallboxen und lässt den Akku überbrücken, und was beides nicht hält, meldet er.

- **Ziel einstellen und abschalten:** Das Ziel ist `control.peak_target_kw`. Ohne Wert (Standard)
  ruht der Wächter ganz (Health `peak.state` `off`). Zum Einschalten trägst du das Ziel in
  `config.yaml` ein und startest den core neu; zum Abschalten nimmst du die Zeile wieder heraus und
  startest neu.

  ```yaml
  control:
    peak_target_kw: 8   # Ziel des 15-min-Wächters in kW; ohne Wert ruht er
  ```

  Das Ziel ist bis Phase 3 das Budget `B`; die Monatsspitze folgt mit dem Leistungspreis (Spec 7.4).
  Zulässig im Rest der Viertelstunde ist die mittlere Leistung
  `P_zul = (B · 0,25 h − E_bisher) / t_rest − Marge` mit der Marge `control.peak_margin_kw`
  (0,3 kW); bei einem Ziel von 8 kW sind das zu Beginn 7,7 kW.
- **Messung:** `E_bisher` ist der Netzbezug der laufenden Viertelstunde bisher aus derselben
  Integration wie die Viertelstunden-Bilanz der Abrechnung (`core/accounting`): die Summe der Flüsse
  aus dem Netz in Haus, Autos und Akku. Der Wächter rechnet ganz auf der Wanduhr, wie die Abrechnung.
  Boost zählt außerhalb des Ziels (unten): Den Netzanteil der Wallboxen in Boost integriert der
  Wächter selbst und zieht ihn ab. Diesen Anteil rechnet er mit PV und Akku zuerst für die Wallboxen
  in Boost, dann für die übrigen in der Reihenfolge von `config.yaml`, denn so lädt der
  Akku-Sollwert: Boost darf aus dem Akku laden, der Fehlbetrag der anderen Autos geht ans Netz. Die
  Abrechnung verteilt PV und Akku dagegen nur in der Reihenfolge von `config.yaml` (erst die EVCS);
  mit ihrer Verteilung zöge der Wächter etwa den Netzbezug des e-Golf in Sofort als Boost des Tesla
  ab und überschritte das Ziel still. Im Simulator (Tesla in Boost, e-Golf in Sofort, Ziel 5 kW)
  bleibt so jede Viertelstunde bei 1,20 kWh; mit der Verteilung der Abrechnung lag sie vor dem
  Gesamtreview unbemerkt bei 1,51 kWh. Die Summe des Netzbezugs ist in beiden Rechnungen dieselbe.
  - **Bekannte Grenze:** Kurze Lücken, die die Abrechnung nicht überbrückt, fehlen in `E_bisher`. Die
    Abrechnung integriert nach dem Halteprinzip und überbrückt höchstens 5 s (`HOLD_MAX_S`, dieselbe
    Grenze gilt für die Energie der Autos in Boost, die der Wächter abzieht); ein längeres Intervall
    und ein Zyklus ohne Bilanz zählen nicht. Der Wächter meldet eine Lücke erst ab 10 s (nächster
    Punkt). Ein Ausfall von 5 bis 10 s lässt `P_zul` in dieser Viertelstunde deshalb etwas zu hoch
    ausfallen.
- **Lückenhaft** (Health `gap`) ist eine Viertelstunde bis zu ihrem Ende, wenn
  - zwischen zwei Zyklen mit bekannter Leistung mehr als 10 s liegen,
  - der erste Zyklus mit bekannter Leistung später als 10 s nach ihrem Beginn kommt, etwa nach dem
    Start des core mitten in der Viertelstunde,
  - die Regelung ruhte (OBSERVE, SAFE, Regelbetrieb aus) oder
  - die Wanduhr innerhalb der Viertelstunde zurückspringt.

  Der Wächter ruht dann: Er drosselt nicht nach einer halben Messung, und es gibt kein Budget für den
  Akku und die Safety-Schicht. Die nächste Viertelstunde beginnt normal.
- **Hochrechnung und Senkung:** Das Mittel ist der Netzbezug der letzten 30 s ohne Boost. Die
  Hochrechnung ist `(E_bisher + Mittel · t_rest) / 0,25 h`, mit dem Rest der Viertelstunde `t_rest`
  (mindestens 1 s). Die **verlangte Senkung** ist `Bedarf − P_zul`, in Health mindestens 0. Der
  Bedarf ist das größere von Mittel und dem Netzbezug, den die Autos über den Override wollen, aus
  dem Basis-Sollwert und `W`, dem gewollten Netzbezug der Autos vor dem Wächter. `W` ist nötig, weil
  Regel 2 mit Victron live den Override auf `P_zul` begrenzt: ESS hielte den Netzbezug dann immer
  unter `P_zul`, ganz aus dem Akku, und die Wallboxen würden nie gedrosselt. Mit `W` drosselt der
  Wächter, sobald die Autos mehr Netz wollen, als das Budget trägt; das Mittel fängt den Rest, etwa
  einen Akku am Min-SOC. Wie `W` zählt, hängt am Budget für den Akku (Punkt 2 unten):
  - **Mit Budget** zählt `Basis + W` höchstens bis zur festen Grenze `C` von Regel 2, dem kleinsten
    Wert aus `max_grid_charge_setpoint_w`, Absicherung und `grid.max_import_kw` (ohne das Budget).
    Mehr Netzbezug setzt kein Override durch; was die Autos darüber wollen, liefert der Akku wie ohne
    Ziel (oben, „Sofort“). Dabei rechnet der Wächter mit dem Netzbezug ohne seine eigenen Senkungen:
    Für den Bedarf zählt `min(Basis + W + S, C) − S`, mit der Summe `S` der Senkungen der Grenzen,
    die er gerade hält. So senkt er insgesamt nur um `min(Basis + W ohne Senkung, C) − P_zul`, und
    das einmal. Laden etwa Tesla und e-Golf in Sofort mit zusammen 18 kW bei einem Ziel von 8 kW
    (`P_zul` 7,7 kW), senkt er den e-Golf um 300 W und dann nicht weiter; was über 7,7 kW liegt,
    liefert der Akku. Bei einem Ziel von 10 kW (`P_zul` 9,7 kW) bleibt der Netzbezug bei 8 kW, und
    der Wächter drosselt gar nicht.
  - **Ohne Budget** (SOC nicht über USV-Reserve + `reserve_guard_pct` oder unbekannt) hält Regel 2
    den Override bei `C`, und der Netzbezug folgt ihm bis dorthin: Er liegt bei höchstens
    `min(Basis + W, C)`. Solange `P_zul` unter `C` liegt, zählt `Basis + W` ganz, denn unter
    `P_zul` bringt den Netzbezug dann nur die ganze Senkung: Der Wächter senkt die Autos in einem
    Schritt um alles, was `Basis + W` über `P_zul` liegt, in der Reihenfolge unten und höchstens
    bis zu ihrem Boden. Liegt `P_zul` bei `C` oder darüber, zählt `min(Basis + W, C)`, und der
    Wächter senkt nichts: Was die Autos über `C` wollen, liefert der Akku wie ohne Ziel, und ein
    Akku an seiner Reserve zeigt sich im Mittel. Laden etwa Tesla und e-Golf in Sofort mit zusammen
    18 kW bei einem Ziel von 10 kW (`P_zul` 9,7 kW), drosselt er auch ohne Budget nicht.
  - **Nur das Mittel,** solange Victron gesperrt (etwa durch DESS) oder nicht steuerbar ist: Dann
    setzt kein Override `Basis + W` durch, und der Wächter zählt nur, was das Netz wirklich liefert.
    Ebenso, wenn `W` oder der Basis-Sollwert unbekannt ist.

Die Reaktion folgt der Reihenfolge von Spec 7.4:

1. **Wallboxen drosseln.**
   - Der Wächter senkt den Netzanteil der Autos, ihren Beitrag zu `W`, in umgekehrter Priorität der
     Modi: erst Autos in PV-Überschuss bis 0 W (sie stoppen dann nach den Regeln ihres Modus), dann
     Min+PV und zuletzt Sofort, beide höchstens bis zur Mindestleistung des Ladepunkts
     (`min_a × U × Phasen`); innerhalb einer Stufe zuerst das Auto mit der niedrigsten Priorität.
     Ein Auto ohne Netzanteil, dessen Leistung die PV deckt, wird nicht gedrosselt.
   - Das ergibt je Auto eine **Leistungsgrenze** in W. Die Laderegelung rechnet sie in Ampere um
     (`W / (U · Phasen des Ladepunkts)`) und behandelt sie wie die Grenze des Phasenwächters: Liegt
     sie unter dem freien Strom, gilt sie, und der Grund der Entscheidung ist `peak_guard`. Sie wirkt
     frühestens im nächsten Zyklus. Eine Senkung wegen dieser Grenze ist nie dringend; verlangt die
     Sicherung allein dieselbe Senkung (Sofort oder Boost ohne Reserve-Schutz), bleibt sie es.
   - Eine **neue Drosselung** kommt nur bei einer Senkung über 0 und frühestens 30 s nach der
     letzten Änderung einer Grenze (die erste einer Viertelstunde sofort). Gedrosselt wird um die
     Senkung abzüglich dessen, was gedrosselte Autos ihrer Grenze noch nicht gefolgt sind (ein Tesla
     folgt erst nach seiner 90-s-Regel).
   - Je Auto setzt die Drosselung bei seiner **Leistung** an, mit Grenze beim kleineren von Grenze
     und Leistung (ohne bekannte Leistung bei der Grenze). Eine Grenze weit über der Leistung schluckt
     so keine Senkung: Hat der Wächter etwa den Tesla in Min+PV bei 16 A gedrosselt und lädt er ohne
     PV inzwischen mit 5 A, an seinem Boden, geht die nächste Senkung an den e-Golf, statt nur die
     Grenze des Tesla zu senken, die ohnehin nicht bindet.
   - **Aufgehoben** wird eine Grenze, wenn `P_zul` den Bedarf (oben, „Hochrechnung und Senkung“)
     um mindestens 1 kW plus die Senkung dieses Autos übersteigt, frühestens 30 s nach der letzten
     Änderung und je Zyklus höchstens eine, in umgekehrter Reihenfolge (Sofort vor Min+PV vor
     PV-Überschuss, innerhalb einer Stufe die höchste Priorität zuerst). Wollen die Autos mit Budget
     mehr als die feste Grenze von Regel 2, hält der Override den Netzbezug bei ihr, sobald `P_zul`
     über ihr liegt, und das Mittel zählt mit ihr: Eine Grenze fällt dann, wenn `P_zul` die feste
     Grenze um 1 kW plus die Senkung dieses Autos übersteigt (im Simulator, `spitze-15min`, bei
     rund 9,3 kW). So flattert der Tesla nicht und spart Tessie-Befehle. Mit dem Ende der
     Viertelstunde fallen alle Grenzen weg.
   - Gedrosselt wird nur ein Auto an einem Ladepunkt, den die Laderegelung regelt; ein ungeregeltes
     Auto in Boost zählt nur für den Boost-Anteil. Verlässt ein gedrosseltes Auto die Liste, wechselt
     es den Modus oder geht es in Boost oder Aus, fällt seine Grenze still weg, ohne neue Wartezeit.
   - Ein Totband gibt es bei der Drosselung nicht (unten, „Hinweise für die Anlage“).
2. **Akku stärker entladen.** Der Akku-Sollwert bekommt `P_zul` als Budget und setzt den gewollten
   Netzbezug auf höchstens dieses Budget: `W' = max(0, min(W, Budget − Basis))`, mit dem Budget auf
   10 W abgerundet. Der Override liegt so nie über dem Budget und bleibt auf dem Raster von 10 W. Ist
   das Budget aufgebraucht (`P_zul` unter 0), gibt es keinen Override, und der Akku deckt die Autos,
   bis die Wallboxen gefolgt sind. Er überbrückt so genau, was die Wallboxen nicht oder noch nicht
   bringen, etwa bis ein gedrosselter Tesla seiner Grenze folgt, und liefert wie ohne Ziel, was die
   Autos über der festen Grenze von Regel 2 wollen; die Schwelle der Unterstützung gilt dafür nicht.
   Das Budget geht aber nur an den Akku (und an Regel 2), solange der SOC über USV-Reserve +
   `control.reserve_guard_pct` (5 %) liegt. Sonst und bei unbekanntem SOC gibt es keins, denn
   Regel 2 entlüde den Akku sonst bis zum Min-SOC; dann bleiben die Wallboxen, die der Wächter um
   die ganze Senkung drosselt, solange `P_zul` unter der festen Grenze von Regel 2 liegt (oben,
   „Hochrechnung und Senkung“), sonst gilt Punkt 3.
3. **Sonst** nimmt der Wächter die Überschreitung hin: Liegt die Hochrechnung 60 s lang ohne
   Unterbrechung über dem Ziel, ist die Viertelstunde **überschritten** (Health `exceeded`) bis zu
   ihrem Ende, und der Alarm `peak.exceeded` (unten) meldet es. Gezählt wird nur in Zyklen, die
   rechnen; ein Zyklus ohne Rechnung unterbricht die 60 s.

- **Die letzten 120 s:** Ab 120 s vor dem Ende einer Viertelstunde startet kein Auto in PV-Überschuss,
  Min+PV oder Sofort, dessen Mindestleistung über `P_zul − Mittel` liegt. Die Laderegelung behandelt
  das wie eine Grenze, die den Mindeststrom nicht trägt; der Start wartet auf die nächste
  Viertelstunde. Ein Auto, das lädt, betrifft das nicht.
- **Boost** hebt das Ziel für sein Auto auf (bis zum Abstecken, höchstens 12 h): Der Netzanteil seiner
  Wallbox (mit PV und Akku zuerst für Boost, oben, „Messung“) zählt weder in die Energie bisher noch
  ins Mittel, und der Wächter drosselt das Auto nie.
  Der Zähler am Netz und die Viertelstunde der Abrechnung sehen den Boost trotzdem. Die geschätzten
  Mehrkosten zeigt erst das UI mit dem Leistungspreis (Phase 3).
- **Im Dry-Run neben evcc** misst der Wächter den Netzbezug, den evcc verursacht. Seine Drosselungen
  stehen als Dry-Run-Entscheidungen der Wallboxen im Log (Grund `peak_guard`), und `peak.exceeded`
  zeigt, wie oft das Ziel unter evcc überschritten würde. Die Autos folgen den Grenzen dabei nicht:
  Die Entscheidungen zeigen, was der Wächter täte, nicht, wie die Viertelstunde dann verliefe.

### Health

`GET /v1/health` (Zugang wie in Abschnitt 4, „Diagnose“) zeigt neben `vehicles` den Stand des
Akku-Sollwerts (`battery`) und des 15-min-Wächters (`peak`) aus dem letzten Zyklus. Ein Ausschnitt
(aus dem Simulator, `spitze-15min` mit Ziel 8 kW, 10 s nach Beginn der zweiten Viertelstunde,
gerundet; die Senkung kommt dort aus dem Mittel der letzten 30 s, denn bis 18:15 lag der Netzbezug
bei der festen Grenze von 8 kW):

```json
{
  "battery": {
    "state": "ok",
    "target_w": 7700.0,
    "override_w": 7700.0,
    "support": "soc",
    "bridging_s": 906.0,
    "refresh_age_s": 10.0
  },
  "peak": {
    "state": "active",
    "target_kw": 8.0,
    "energy_kwh": 0.021,
    "projected_kw": 7.9,
    "budget_kw": 7.7,
    "reduction_kw": 0.2,
    "quarter_end": "2026-10-12T16:30:00+00:00"
  }
}
```

Was fehlt, ist `null`. Das UI zeigt diese Werte nicht.

| Feld | Bedeutung |
|---|---|
| `battery.state` | `ok`: Das Ziel ist bekannt, der Akku-Sollwert rechnet. `unknown`: Das Ziel ist unbekannt, weil ein Wert fehlt (PV, Hauslast, Leistung eines ladenden Autos, Basis-Sollwert); es geht kein Befehl hinaus. `locked`: Victron ist gesperrt oder nicht steuerbar, es geht kein Befehl hinaus. `idle`: Die Regelung ruht, oder es gab noch keinen Zyklus. |
| `battery.target_w` | der gewollte Netzbezug nach dem Wächter (`W'`) in W, ohne den Basis-Sollwert; `null`, wenn er unbekannt ist |
| `battery.override_w` | der letzte eigene Override in W, mit dem Basis-Sollwert (`override_w` ≈ Basis + `target_w`); `null` ohne einen und für „kein Override“. Innerhalb des Totbands weicht er davon ab. Es ist der angeforderte Wert, vor den Regeln 2 und 4 der Safety-Schicht: Der Cerbo kann weniger halten, etwa 8000 W, wenn 17 940 W angefordert waren. Was er hält, zeigt `inputs.actual_setpoint_w` der Entscheidungen an `victron`. |
| `battery.support` | Stand der Unterstützung: `on` (gilt), `mode` (kein Auto in PV-Überschuss oder Min+PV), `soc` (SOC unter der Schwelle oder unbekannt), `time` (Überbrückung länger als `support_max_s`). Gilt sie nicht, steht der erste zutreffende Grund in der Reihenfolge `mode`, `soc`, `time`. |
| `battery.bridging_s` | Sekunden der laufenden Überbrückung; `null` ohne |
| `battery.refresh_age_s` | Sekunden vom letzten Urteil mit Wert über den eigenen Override bis zum letzten Zyklus; er steigt bis zum Auffrischen und beginnt dann neu. `null` ohne Override. |
| `peak.state` | `off`: kein Ziel. `gap`: Die Viertelstunde ist lückenhaft, oder die Regelung ruht; auch vor dem ersten Zyklus. `active`: Der Wächter rechnet. `exceeded`: Die Viertelstunde ist überschritten (auch während einer Lücke). |
| `peak.target_kw` | das Ziel in kW; `null` ohne |
| `peak.energy_kwh` | `E_bisher` ohne Boost in kWh |
| `peak.projected_kw` | die Hochrechnung der Viertelstunde in kW |
| `peak.budget_kw` | `P_zul` in kW, auch unter 0 und auch dann, wenn der SOC dem Akku kein Budget erlaubt |
| `peak.reduction_kw` | die verlangte Senkung in kW, mindestens 0 |
| `peak.quarter_end` | das Ende der Viertelstunde des letzten Zyklus, ISO-Zeit in UTC |

`peak.energy_kwh`, `peak.projected_kw`, `peak.budget_kw` und `peak.reduction_kw` gibt es nur nach einem
Zyklus, der rechnet; die Hochrechnung und die Senkung brauchen außerdem das Mittel der letzten 30 s.
Ruht die Regelung (OBSERVE, Regelbetrieb aus), zeigt `peak` weiter den letzten Zustand ohne Werte
(`gap`, bei einer überschrittenen Viertelstunde `exceeded`); `quarter_end` nennt die Viertelstunde, auf
die er sich bezieht. `battery` steht dann auf `idle`.

### Gründe

Das Entscheidungslog (Abschnitt 13) nennt bei Befehlen des Akku-Sollwerts und des Wächters diese
Gründe in `codes`, vor den Befunden der Safety-Schicht:

| Grund (`codes`) | Text im Log | Bedeutung |
|---|---|---|
| `battery_comp` | Akku nicht ins Auto | Der Override verschiebt den Fehlbetrag der Autos aufs Netz. |
| `battery_support` | Akku-Unterstützung | Nur der Teil über dem erlaubten Akku-Anteil geht aufs Netz. |
| `peak_guard` | 15-min-Wächter | Der Befehl folgt aus dem 15-min-Wächter: Beim Override hat sein Budget den gewollten Netzbezug gesenkt, bei einer Wallbox gilt seine Leistungsgrenze an Stelle der des Phasenwächters. |

- `battery_support` steht, wenn der Akku zusammen etwas liefern darf, sonst `battery_comp`; dahinter
  folgt `peak_guard`, wenn das Budget des Wächters den gewollten Netzbezug gesenkt hat.
- Ein Beispiel: `Victron (ESS) (Akku nicht ins Auto): Netz-Sollwert 2340 W – Dry-Run, nicht
  gesendet.` Hebt der Akku-Sollwert den Override auf, steht dort „Netz-Sollwert-Override aufheben“.
- Zu den Eingaben einer Entscheidung des Overrides gehören die der Laderegelung (`soc_pct`,
  `battery_w`, `grid_l1_a` bis `grid_l3_a`) und diese in W: `pv_w` und `house_w` (PV und Hauslast),
  `cars_w` (Ladeleistung der Autos), `deficit_w` (Fehlbetrag zusammen), `allowed_w` (erlaubter
  Akku-Anteil zusammen), `target_w` (der gewollte Netzbezug nach dem Wächter) und `actual_setpoint_w`
  (der Override laut Cerbo; im Dry-Run unter ESS „kein Override“, also `null`).

Ein Eintrag im Rohformat (hier aus dem Simulator, gerundet):

```json
{
  "id": "2f96611b39534a979f8d77bffa04e2bf",
  "ts": "2026-10-12T16:00:05+00:00",
  "actuator": "victron",
  "kind": "grid_setpoint",
  "requested": 7720.0,
  "applied": 7720.0,
  "dry_run": false,
  "outcome": "accepted",
  "result": "ok",
  "codes": ["battery_comp", "peak_guard"],
  "text": "Victron (ESS) (Akku nicht ins Auto, 15-min-Wächter): Netz-Sollwert 7720 W – gesendet und bestätigt.",
  "inputs": {"soc_pct": 59.9, "battery_w": -4290.7, "grid_l1_a": 16.0, "grid_l2_a": 16.0,
             "grid_l3_a": 0.1, "pv_w": 0.0, "house_w": 840.7, "cars_w": 10810.0,
             "deficit_w": 10810.0, "allowed_w": 0.0, "target_w": 7720.0,
             "actual_setpoint_w": 7360.0}
}
```

Im Beispiel haben die Autos 10,8 kW Fehlbetrag, und der Akku darf nichts liefern (SOC unter 70 %). Das
Budget des Wächters liegt bei 7,72 kW und senkt den gewollten Netzbezug auf 7720 W (`target_w`); der
Override geht von 7360 W auf diesen Wert. Liegt der gewollte Netzbezug über
`victron.max_grid_charge_setpoint_w`, kappt Regel 2 ihn: Dann steht `range.ceiling` hinter den Gründen
und `applied` unter `requested`.

### Victron in den Dry-Run und live

1. **Images ausrollen** (`docker compose pull && docker compose up -d`, Abschnitt 4). Die neuen Felder
   haben Standardwerte (`deploy/config.example.yaml`); der Watchdog bleibt unverändert (1.1.0), und
   keines der neuen Felder verlangt eine neue `config.ini`. Sende eine Absicht mit `"control": true`
   (Abschnitt „Laderegelung“): Victron bleibt im Dry-Run, solange sie ihn nicht live verlangt.
2. **DESS aus für den Dry-Run.** Solange DESS an ist, ist Victron gesperrt: Der Akku-Sollwert sendet
   auch im Dry-Run nichts, und Health zeigt `battery.state` `locked`. Der Dry-Run des Victron-Teils
   braucht deshalb DESS aus; das Fenster dafür bestimmst du. Bis Phase 3 läuft der Akku dann ohne
   Preisoptimierung (Eigenverbrauch und die Regeln dieses Abschnitts). Modus lesen, abschalten und
   zurücksetzen wie in Abschnitt 11, „Victron und Dynamic ESS“.
3. **Dry-Run auswerten.** Die Entscheidungen an `victron` (Art `grid_setpoint`) zeigen in `requested`,
   was BindaEMS einstellen würde, und in `inputs.actual_setpoint_w` den Override am Cerbo (unter ESS
   im Dry-Run `null`). `SINCE` ist wie in „Dry-Run-Vergleich mit evcc“ gesetzt:

   ```bash
   curl -s -H "Authorization: Bearer $BINDAEMS_INTERNAL_TOKEN" \
     "http://127.0.0.1:8081/v1/decisions?since=$SINCE&limit=5000" \
     | jq -r '.decisions[] | select(.actuator == "victron" and .dry_run) | [.ts, .result, .requested,
         .inputs.actual_setpoint_w, .inputs.deficit_w, .inputs.allowed_w, (.codes | join(","))] | @tsv'
   ```

   Prüfe die Punkte unter „Hinweise für die Anlage“ (unten).
4. **Prüfprotokoll Teil 2, Victron 17.2-4 „Kein Akku ins Auto“** bestehen (Abschnitt 11). Es prüft
   genau den Mechanismus dieses Abschnitts und ist noch offen. Der Punkt `victron` verlangt DESS aus,
   den Multi auf „Ein“ und ein Auto, das an der EVCS lädt (über 1000 W).
5. **`victron.live_allowed: true`** in `config.yaml` (Abschnitt 13, „Dry-Run und live“), den core neu
   starten, dann die `config.ini` neu erzeugen und mit `install.sh` übertragen (Abschnitt 14,
   „config.ini erzeugen“ und „Installieren“). Die Freigabe allein schaltet Victron nicht live, das
   tut erst die Absicht (Schritt 7). Mit ihr enthält die `config.ini` aber die Aktion, die bei einer
   Auslösung den Override aufhebt, und die Probe in Schritt 6 prüft sie nur dann. Design 2c-2 (11.1)
   nennt die Freigabe nach der Probe; die Reihenfolge hier folgt dieser Abhängigkeit.
6. **Watchdog-Probe mit vorher verstellten Victron-Werten** (Abschnitt 14, „Probe“). Die Probe
   verstellt nichts selbst, und der Watchdog schreibt nur, was vom Sicherwert abweicht. Stelle
   deshalb Min-SOC, Entladegrenze und Override vorher von Hand auf Werte neben dem Sicherwert (Min-SOC
   etwas über die USV-Reserve, die Entladegrenze auf einen endlichen Wert, den Override auf eine
   Zahl), notiere die vorgefundenen Werte und prüfe nach der Probe, dass die Auslösung sie
   zurückgesetzt hat: Min-SOC = USV-Reserve, Entladegrenze −1 (unbegrenzt), Override aufgehoben. Die
   Pfade am Cerbo sind `com.victronenergy.settings` mit `/Settings/CGwacs/BatteryLife/MinimumSocLimit`
   und `/Settings/CGwacs/MaxDischargePower` sowie `com.victronenergy.hub4` mit
   `/Overrides/Setpoint` (`dbus -y … GetValue` und `SetValue`, wie in Abschnitt 11). Danach stellst
   du die Werte ein, wie du sie haben willst.
7. **Victron live schalten:** im UI mit dem Dialog „Live schalten“ (**System** → „Bedienung“, als Admin;
   wähle nur Victron, die Bestätigung „evcc ist ganz abgeschaltet.“ entfällt dafür). Ohne app verlangst
   du es in der Absicht per `PUT /v1/intent`. Sie ist vollständig (Abschnitt „Absicht per
   PUT /v1/intent“); dieses Beispiel lässt EVCS und Tesla im Dry-Run:

   ```json
   {"version": 14, "control": true, "actuators": {"victron": "live"}}
   ```

   Live läuft Victron nur mit scharfem Watchdog und ohne Sperre, wie jeder Aktor; den Watchdog
   schärft der core selbst (Abschnitt 13, „Gate“).
8. **Danach:** DESS bleibt aus. Schaltest du es wieder ein, sperrt sich Victron, und der Override
   verfällt nach 300 s. Zurück in den Dry-Run geht es wie in Abschnitt 13, „Live-Freigabe
   zurücknehmen“: Der core übergibt Victron und hebt den Override auf.

### Alarme

Beide Alarme zeigt das UI unter **System** → „core“ → „Alarme“ (Abschnitt 14, „Meldungen des core“).

| Alarm | Stufe | Bedeutung und Abhilfe |
|---|---|---|
| `control.exception`: Regelung ausgesetzt: Ausnahme {Typ} im Regelzyklus. | Fehler | Ein Aufruf der Regelung hat eine Ausnahme geworfen; {Typ} ist ihre Art. Das ist ein Fehler im core, kein Bedienfehler. Das Log (`docker compose logs ems-core`) nennt die erste Ausnahme einer Folge mit Traceback: Sichere es und melde den Fehler. |
| `peak.exceeded`: 15-min-Ziel überschritten: {x} kW in der Viertelstunde bis {HH:MM}. | Warnung | Die Hochrechnung lag 60 s lang über dem Ziel, und weder die Drosselung der Wallboxen noch der Akku halten es. Meist ist es kein Fehler: Prüfe, was den Netzbezug getrieben hat, und hebe das Ziel an oder schalte den Wächter ab. |

- **`control.exception`:**
  - Die Regelung sendet in einem gescheiterten Zyklus nichts, und der Heartbeat bleibt aus. Keeper
    (sicherer Zustand, Übergabe), Stream, Alarme und Entscheidungslog laufen weiter. Ein scharfer
    Watchdog löst nach 90 s ohne Heartbeat aus und setzt die Sicherwerte.
  - Der Alarm steht, solange der letzte Zyklus mit Regelung scheiterte: Er endet erst mit einem
    Zyklus, in dem die Regelung ohne Ausnahme lief, auch wenn sie zwischendurch ruht (etwa nach dem
    Ausschalten des Regelbetriebs oder in SAFE). Der Zeitpunkt „seit“ ist der erste gescheiterte
    Zyklus der Folge. Während einer Folge gescheiterter Zyklen kann ein älterer Alarm `peak.exceeded`
    danebenstehen.
  - Ein Neustart des core räumt den Alarm. Wirft die Regelung danach wieder, schalte den Regelbetrieb
    aus (Abschnitt „Absicht per PUT /v1/intent“), bis der Fehler behoben ist: Der core übergibt die
    Aktoren dann mit den Sicherwerten.
- **`peak.exceeded`:**
  - {x} ist die Hochrechnung in diesem Moment auf 0,1 kW, {HH:MM} das Ende der Viertelstunde (Wiener
    Zeit). Text und Zeitpunkt bleiben bis zum Ende der Viertelstunde fest, und der Alarm endet mit
    ihr, auch wenn die Regelung ruht; Health zeigt `peak` dann weiter mit `quarter_end` der alten
    Viertelstunde.
  - Häufige Ursachen: eine große Last im Haus, ein Akku an der Schwelle (SOC nicht über
    USV-Reserve + `reserve_guard_pct`, dann gibt es kein Budget für ihn) oder ein Ziel unter dem
    Grundbedarf des Hauses. Ein Auto in Boost zählt nicht mit, ungeregelte Autos drosselt der Wächter
    nicht.

### Hinweise für die Anlage

- **Der Tesla startet mit 16 A:** Nachts in Min+PV (keine PV, SOC über 70 %) liefert der Akku bis zu
  3 kW in die Autos (`support_max_w`). Startet der Tesla mit seinem alten Strom von 16 A, lädt er 90 s
  mit voller Leistung, bis der Befehl auf 5 A wirkt (die 5-A-Eigenheit, Abschnitt „Laderegelung“,
  „Tesla“): Regel 2 begrenzt den Override auf 8 kW, und der Akku liefert den Rest, im Simulator bis zu
  5,8 kW in die Autos für 90 s. `akku-minpv` startet den Tesla darum mit 5 A (Abschnitt
  „Simulator“). `vehicles.tesla.min_a: 6` vermiede die 5-A-Eigenheit und damit diese 90 s.
- **Im Dry-Run an der Anlage prüfen:**
  - wie viele Entscheidungen an `victron` je Stunde entstehen: bei ruhiger Last wenige, unter
    wechselnden Wolken rund 70 (Simulator). Deutlich mehr heißt, dass das Rauschen der Hauslast
    größer ist als das Totband; hebe `control.setpoint_deadband_w` an.
  - ob der Wächter bei kleinen Senkungen nachdrosselt (Entscheidungen mit `peak_guard` in kurzer
    Folge an derselben Wallbox). Die Drosselung hat kein Totband: Nach einer Drosselung klingt das
    alte 30-s-Mittel genau mit der Wartezeit von 30 s ab, und das Modell des Simulators hat kein
    Netzrauschen. An der Anlage könnten kleine Nachdrosselungen und eine verzögerte Aufhebung die
    Folge sein; die Marge `peak_margin_kw` fängt sie ab. Im Simulator gibt es eine solche
    Nachdrosselung, seit die Drosselung bei der echten Leistung ansetzt: In der Abwandlung von
    `spitze-15min` mit dem Tesla in Boost und Ziel 5 kW liegt der Netzbezug 3 s lang über dem
    Override, bis der e-Golf seiner ersten Grenze folgt. 30 s später verlangt das Mittel daraus
    noch 93 W, und der e-Golf geht von 10 auf 9 A; vorher fiel diese Senkung in den Abstand
    zwischen seiner Grenze und dem Strom in ganzen Ampere. Stufen sind dabei nicht gewollt: Wollen
    die Autos mehr als die feste Grenze von Regel 2 und liegt `P_zul` darunter, senkt der Wächter
    mit Budget einmal um den Unterschied (bei 8 kW Ziel und Grenze rund 300 W) und rechnet diese
    Senkung danach mit (oben, „Hochrechnung und Senkung“). Im Simulator (`spitze-15min`) geht der
    e-Golf so in der zweiten und dritten Viertelstunde einmal von 16 auf 15 A, bis `P_zul` die
    Grenze um 1 kW plus die Senkung übersteigt (rund 9,3 kW); in der ersten trifft die Senkung den
    Tesla, der dort noch mit 16 A lädt.
  - ob `battery.state` `unknown` erscheint (ein Messwert fehlt) oder `locked` (DESS an, ein anderes
    System regelt).

## Simulator

Der Anlagensimulator `bindaems.tools.sim` lässt die echte Laufzeit des core mit ihren echten Aktoren
gegen ein Modell der Anlage laufen, in simulierter Zeit (ein Zyklus je Sekunde). Das Modell hat ein
Netz mit Sicherung, PV, Hauslast, den Akku mit ESS, Peak Shaving und dem Override des Netz-Sollwerts
(er verfällt 300 s nach dem letzten Schreiben; mit Dynamic ESS verwirft der Cerbo ihn), die EVCS mit
dem e-Golf, den Wall Connector mit dem Tesla und Tessie, Ausfälle und einen fremden Regler wie evcc.
Für die Zuordnung kommen Home Assistant dazu, das den e-Golf zu Hause oder weg meldet, ein Gast ohne
Tessie, der auf 1 bis 3 Phasen lädt, und ein Tesla, der woanders angesteckt ist: Tessie meldet ihn
zu Hause und angesteckt, er steckt aber an keiner Wallbox. Jedes Auto lässt sich an jede Wallbox
stecken. Gefälscht sind nur die Geräte: Parser der Adapter, Aktoren, Safety-Schicht und Regelung
sind die echten, und das Modell zeigt sich so, wie die Geräte und Adapter es tun. Der Simulator
verbindet sich mit nichts und schreibt nichts auf die Geräte; er braucht weder Tokens noch Netz. Die
Szenarien sind deterministisch.

In der CI läuft jedes der dreiundzwanzig Szenarien bei jedem Push
(`tests/integration/test_sim_scenarios.py`), dazu eine Abwandlung von `spitze-15min` (Tesla in
Boost, e-Golf in Sofort, Ziel 5 kW), die den Boost-Anteil des Wächters prüft (oben, „Messung“).
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

- `--szenario NAME` (Pflicht): eines der dreiundzwanzig Szenarien unten.
- `--out PFAD` (Pflicht): der Bericht als Markdown. Fehlende Ordner legt das Werkzeug an.
- `--config PFAD`: die Konfiguration, aus der Grenzen, Fahrzeuge und Wallboxen kommen; ohne Angabe
  `deploy/config.example.yaml`, relativ zum aktuellen Verzeichnis. Die Szenarien brauchen die
  Wallboxen `evcs` (EVCS) und `twc` (Wall Connector) und die Fahrzeuge `tesla` (mit Tessie) und
  `egolf`. `egolf-twc` und `gast-evcs` bestehen nur mit `home_entity` beim e-Golf: Ohne Home
  Assistant erkennt der core den e-Golf am Wall Connector nicht und schließt ihn an der EVCS nicht
  aus.
- `--stunden H`: Dauer in Stunden statt der des Szenarios, **höchstens diese**; ein größerer Wert
  lässt das Szenario ganz laufen, denn seine Ereignisse und Prüfungen enden mit seiner Dauer. Ein
  kürzerer Lauf ist nur ein Ausschnitt: Prüfungen des Szenarios, die das Ende oder eine Dauer
  prüfen, können dann scheitern, ohne dass die Regelung fehlt (etwa `sonnig --stunden 0.05`:
  „„tesla“ erreicht „Complete“ nicht.“, Rückgabe 1).
- Jeder Lauf bekommt ein **frisches temporäres Verzeichnis** für die Ablage des core (Absicht,
  Rate-Limits, Entscheidungen). Er berührt `/data` nie und sieht nichts von früheren Läufen.
- Ein Szenario braucht für 30 min bis 2 h simulierte Zeit etwa 3 bis 13 s Rechenzeit; alle
  dreiundzwanzig zusammen rund 3 min.

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
    „Entscheidungslog“), also höchstens eine je Minute, Art und Befund. Seit der Reserve für
    Tesla-Befehle sind es in `wolkig` und `dry-run` 0.
- Als „eigener Wert“ der Regelung zählt auch ein Wert, den die Safety-Schicht mit „unverändert“
  beantwortete. Vergisst die Regelung ihre Werte (Pause, Wechsel der Betriebsart), beginnt die
  Zählung neu.
- Die Lastsprünge der Szenarien sind so bemessen, dass ein korrekt regelnder core 35 A halten kann.

### Szenarien

Alle Szenarien starten mit angesteckten, nicht ladenden Autos an ihren Standard-Wallboxen und einem
wachen Tesla, soweit die Tabelle nichts anderes sagt; der Gast steht abgesteckt, und Home Assistant
meldet den e-Golf zu Hause. Der Regelbetrieb ist an, EVCS und Tesla sind live (außer in `dry-run`).
Victron (der Akku-Sollwert) entscheidet in den ersten zehn Szenarien im Dry-Run mit; in den acht für
Akku und Spitzen, ab `akku-wolkig`, ist er live, in den drei für die Zuordnung, ab `tesla-evcs`,
wieder im Dry-Run, ebenso in den zwei für „Aus“ und Selbststart des Tesla, ab `tesla-aus`.

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
| `zuordnung` | Zuordnung unklar und Übersteuerung, 1 h | Tesla woanders angesteckt (Tessie: zu Hause und angesteckt, an keiner Wallbox); e-Golf in Sofort an der EVCS, die schon vor dem Start des core lädt; Übersteuerung `egolf: evcs` (Version 2) bei 10 min; Abstecken des e-Golf bei 40 min | Die Gesundheit zeigt den e-Golf vor 10 min „unklar“ (ein echter Widerspruch: Tessie und der Ausschluss stützen den Tesla an der EVCS, die Phasen-Signatur des e-Golf schließt ihn aus); ab 11 min zeigt sie kein Fahrzeug „unklar“; ab 41 min lädt die EVCS nicht. |
| `dry-run` | Dry-Run neben einem fremden Regler, 1 h | wie `wolkig`, fremder Regler an, alle Aktoren im Dry-Run | keine wiederholte Dry-Run-Entscheidung; mindestens eine Dry-Run-Entscheidung an EVCS und Tesla; kein Alarm „competitor.foreign.*“ |
| `akku-wolkig` | PV unter Wolken, SOC 60 %, 1 h | 21.06.2026 ab 10:00, Wolken wie `wolkig`, Akku 60 %, nur der e-Golf, in PV | Höchstens 5 % der Energie des e-Golf kommen aus dem Akku (ohne Übergänge); eine Entscheidung an `victron` nennt „battery_comp“. |
| `akku-minpv` | Min+PV ohne Sonne, SOC 80 %, 30 min | 12.10.2026 ab 18:00, keine PV, Akku 80 %, beide in Min+PV, der Tesla zu Beginn auf 5 A | Der Akku liefert in die Autos (Entladung − Haus) ab 10 s nach dem ersten Laden bis 890 s danach höchstens 3200 W (`support_max_w` von 3000 W plus das Totband des Overrides von 200 W); nach `support_max_s` (900 s), ab 930 s nach dem ersten Laden, höchstens 200 W (das Totband). |
| `akku-sofort` | Sofort am Abend, beide Autos, 30 min | Akku 60 %, beide in Sofort | Ab 2 min liegt der Netzbezug in jeder Sekunde, in der Autos und Haus mehr als 8300 W ziehen, zwischen 7800 und 8200 W: Der Akku liefert nur den Teil über `max_grid_charge_setpoint_w` (8 kW). |
| `boost-abend` | Boost am Abend, 45 min | Akku 35 %, nur der e-Golf, in Boost bis zum Ende | Keine Entscheidung an `victron` mit einer Zahl; solange der SOC über 25 % liegt, ab 2 min Netzbezug höchstens 300 W; der SOC sinkt nie unter 20 % (Invariante des Modells). |
| `override-stunde` | Eine Stunde mit aktivem Override, 1 h | Akku 50 %, nur der e-Golf, in Sofort | Ab 2 min gilt in jeder Sekunde ein Override, zuletzt vor höchstens 245 s geschrieben (Auffrischen alle 240 s); kein Alarm „competitor.foreign.*“; höchstens 5 Entscheidungen an `victron` (das Auffrischen steht nicht im Log). |
| `dess-an` | Dynamic ESS an, 30 min | Akku 60 %, Dynamic ESS an, nur der e-Golf, in Sofort | Keine Entscheidung an `victron`; ab 1 min zeigt die Gesundheit `battery.state` „locked“; der e-Golf lädt am Ende. |
| `spitze-15min` | 15-min-Ziel 8 kW, 75 min | 12.10.2026 ab 17:59, `control.peak_target_kw` 8, Akku 60 %, Tesla in Min+PV, e-Golf in Sofort, ab 18:45 in Boost bis zum Ende | Netzbezug der Viertelstunden ab 18:00, 18:15 und 18:30 je höchstens 2,02 kWh; vor 18:45 nennt eine Entscheidung „peak_guard“, ab 18:45 keine an `wallbox.evcs`; bei der ersten Drosselung des e-Golf in Sofort lädt kein Auto in PV-Überschuss oder Min+PV über Mindeststrom + 0,5 A (belegt im Simulator wenig, unten). |
| `schieflast-pv` | PV mit schwerer Last auf L1, 1 h | 21.06.2026 ab 10:00, PV konstant 12 kW, Akku 60 %, nur der e-Golf, in PV; ab 10 min 5,5 kW mehr Last auf L1 | Eine Entscheidung an `wallbox.evcs` nennt „import_limit“: Das Importziel bindet den e-Golf zwischen Mindest- und Höchststrom. |
| `tesla-evcs` | Tesla an der EVCS ohne Übersteuerung, 30 min | 12.10.2026 ab 18:00, keine PV, Akku 60 %, Tesla an der EVCS mit 10 A im Auto, in Sofort; e-Golf abgesteckt | Die Gesundheit zeigt den Tesla vor 3 min „erkannt“; genau eine Entscheidung „Strom 16 A“ an den Tesla (sein `max_a`); er lädt am Ende mit 16 A: Die EVCS regelt in seinem Modus. |
| `egolf-twc` | e-Golf am Wall Connector, 30 min | 12.10.2026 ab 18:00, keine PV, Akku 60 %, e-Golf am Wall Connector, in PV; Tesla abgesteckt | Ab 4 min zeigt die Gesundheit am Wall Connector den e-Golf, erkannt; keine Entscheidung an `vehicle.tesla`; der e-Golf lädt am Ende mit 16 A, als Last, die die Regelung nicht steuert. |
| `gast-evcs` | Gast an der EVCS, e-Golf weg, 30 min | 12.10.2026 ab 18:00, keine PV, Akku 60 %, Gast mit 3 Phasen an der EVCS, Modus des Gastes Sofort; e-Golf abgesteckt und laut Home Assistant weg; Tesla abgesteckt | Ab 3 min zeigt die Gesundheit an der EVCS den Gast (`guest`), erkannt; er lädt am Ende mit 16 A im Modus des Gastes. |
| `tesla-aus` | Tesla wird unter Wolken auf Aus gestellt, 1 h | wie `wolkig` (21.06.2026 ab 10:00, Wolken, Akku 60 %, beide in PV), der Tesla mit 45 % SOC; bei 45 min wechselt der Tesla auf Aus (Version 2 der Absicht) | Der Tesla bekommt binnen 10 s nach 45 min „Laden aus“ („Aus“ wirkt sofort); kein zurückgestellter Befehl „charging“ an `vehicle.tesla`. Das Szenario belegt nicht, dass der dringende Stopp das Stundenlimit umgeht: Die Reserve (`control.tesla_reserve_commands`, Abschnitt „Laderegelung“, „Tesla“) hält das Stundenbudget unter den Wolken offen, es ist nie ausgeschöpft (Zurückstellungen wegen des Stundenlimits: 0). Das Umgehen prüft der Test `test_off_stops_urgently` der Regelung. |
| `tesla-selbststart` | Tesla startet selbst im Modus Aus, 30 min | 12.10.2026 ab 18:00, keine PV, Akku 60 %, Tesla am Wall Connector mit 50 % SOC, in Aus, gestoppt; e-Golf abgesteckt; bei 5 min startet der Tesla selbst („Laden an“ im Auto, etwa aus der App) | Der Tesla bekommt binnen 210 s nach 5 min „Laden aus“ (120 s gegen den Modus, `control.tesla_against_s`, plus die Abfrage von Tessie alle 60 s, solange er angesteckt ist, plus der Verzug des Befehls); ab 9 min lädt er nicht mehr. |

Die Verteilung ist gierig nach Priorität (Abschnitt „Laderegelung“): Mit höchstens 10 kW PV laden die
Autos in `sonnig` und `wolkig` nacheinander, darum steht der Tesla dort nahe an seinem Ladelimit.

Der Aufbau von zwei Szenarien für Akku und Spitzen ist angepasst, weil sich ihre Prüfung im Modell
sonst nicht erfüllen ließe:

- In `akku-minpv` steht der Tesla zu Beginn auf 5 A. Startet er mit seinem alten Strom von 16 A,
  lädt er nach dem Start 90 s mit 16 A, bis der zweite Befehl auf 5 A wirkt (der erste geht über
  Tessie verloren). Der Akku liefert dann bis zu 5,8 kW in die Autos, denn der Override ist auf
  `max_grid_charge_setpoint_w` (8 kW) begrenzt.
- In `schieflast-pv` kommt die Last auf L1 erst nach 10 min, wenn der e-Golf schon lädt. Liegt sie von
  Anfang an, startet er nicht: Der Phasenwächter zählt die Ladeleistung des Akkus zur Last der Phase
  und lässt auf L1 nur rund 3 A frei, weniger als den Mindeststrom.

`spitze-15min` startet den Tesla wie im Plan mit 16 A und besteht damit alle Prüfungen. Die Prüfung
der Reihenfolge belegt dort trotzdem wenig. In der ersten Viertelstunde senkt der Wächter den Tesla
in Min+PV, der noch mit 16 A lädt, um rund 270 W; diese Grenze bindet nicht, denn ohne PV geht er
ohnehin auf 5 A, und sie steht nicht im Log. Danach deckt die gemerkte Senkung den Unterschied zur
festen Grenze von Regel 2 (oben, „Hochrechnung und Senkung“), und den e-Golf in Sofort drosselt der
Wächter erst ab 18:15, wenn der Tesla mit 5 A lädt. Auch ein Wächter, der Sofort vor Min+PV
drosselte, bestünde die Prüfung: Seine Senkung des e-Golf um 1 A fiele um 18:00 mit der des
Phasenwächters zusammen, der den e-Golf neben dem Tesla mit 16 A ebenfalls auf 15 A begrenzt, und
die Entscheidung nennte dann `phase_limit`, nicht `peak_guard`. Die Reihenfolge der Drosselung
prüfen `tests/unit/core/control/test_peak_throttle.py` und `tests/property/test_peak_guard.py`.

In den Szenarien für die Zuordnung zeigt `GET /v1/health`, wer wo steht (`vehicles` und
`wallboxes`). Nach dem Start beobachtet der core rund 60 s; erst dann regelt er, und die Erkennung
beginnt, die 60 s ohne Unterbrechung braucht. So ist im Simulator der Tesla in `tesla-evcs` nach
2 min erkannt, ebenso der Gast in `gast-evcs`, der e-Golf in `egolf-twc` nach 3 min, denn die
Phasen-Signatur braucht davor 60 s Laden; bis dahin steht am Wall Connector ein angenommener Gast.
`zuordnung` ist ab 3 min unklar: erst 60 s Phasen-Signatur, dann 60 s Widerspruch. `tesla-evcs`
prüft „erkannt“ darum vor 3 min, nicht binnen 2 min wie im Design, dessen 2 min ab dem Beginn der
Regelung zählen. In `zuordnung` lädt die EVCS den e-Golf schon vor dem Start des core: So schließt
die Phasen-Signatur den Tesla an der EVCS im selben Zyklus aus, in dem er dort sonst erkannt wäre,
denn Tessie und der Ausschluss stützen ihn von Anfang an.

### Was das Modell nicht leistet

Der Simulator zeigt, ob die Regelung bei bestimmten Lasten, Wolken und Ausfällen die Grenzen hält. Die
Zeiten von Tessie (Antwort nach 0,2 bis 2,5 s, Abfragen alle 30, 60 oder 600 s), der verlorene erste
5-A-Befehl und die Ladekurven der Autos sind Annahmen aus dem Prüfprotokoll Teil 2 und der Spec. Ein
Tessie-Ausfall trennt wie beim echten Adapter die Quelle nicht: Die Werte veralten, Befehle scheitern
mit HTTP 503, und die Abfragen laufen im Backoff des Adapters (60 s, verdoppelt bis 900 s). Ein grüner
Simulator ersetzt weder das Prüfprotokoll Teil 2 noch den Dry-Run an der Anlage.

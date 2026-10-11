# Betrieb von BindaEMS

Stand: Phase 2a (Sicherheitsbasis). Auf der VM laufen zwei Dienste, beide steuern **nichts**:

- `ems-core` liest Cerbo GX, EVCS, Wall Connector, Tessie und Home Assistant und schreibt
  Telemetrie nach InfluxDB. Er kennt Betriebszustände, Freigaben und Sperren (Abschnitt 13).
  Ohne Absicht der Bedienung bleibt er im Zustand **OBSERVE**: nur lesend. Auf ein Gerät schreibt
  er höchstens den Heartbeat an den eigenen Watchdog (Abschnitt 14), und den nur, solange ein
  Victron- oder EVCS-Aktor live angefordert ist. Einen live angeforderten Aktor gibt es erst mit
  der Bedienung in Plan 2d.
- `ems-app` meldet Benutzer an und liefert das Web-UI samt API aus (Abschnitt 12). Sie holt
  Preise und die PV-Prognose, rechnet jede Viertelstunde ab und meldet lesende Sensoren per
  MQTT Discovery an Home Assistant.

Dazu kommt der **Watchdog** auf dem Cerbo, den du einmal installierst (Abschnitt 14). Er ist der
Totmann-Schalter der Anlage und greift nur ein, wenn ein live geschalteter core ausfällt.

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
    länger als 90 s, löst der Watchdog aus (Abschnitt 14).
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
  - `/v1/health` nennt auch Betriebszustand mit Gründen, Aktoren und Watchdog, `/v1/intent` die
    gültige Absicht (Abschnitt 13).
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
  - Ist der core binnen 90 s zurück, löst der Watchdog nicht aus.
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

### Teil 2

Teil 2 (Spec 17.2) schreibt auf Geräte und läuft gemeinsam mit dem Betreiber. Plan 2a liefert
dafür die **Watchdog-Probe** (Punkt 5); ihr Aufruf steht in Abschnitt 14. Die übrigen Punkte
folgen mit Plan 2b.

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
| `DEGRADED` | Eingeschränkt | Wie CONTROL, aber mit Einschränkung: ein Teilsystem ist gesperrt, ein Gerät ist nicht erreichbar oder der Watchdog fehlt. Die Gründe nennen es. |
| `SAFE` | Sicherer Zustand | Kritische Daten fehlen, die Phasen stimmen nicht oder das Netz ist ausgefallen. Dazu gibt es einen Alarm „Sicherer Zustand: …“. |

Plan 2a hat noch keine Aktoren (sie kommen mit Plan 2b). Auch in CONTROL schreibt der core
deshalb nichts außer dem Heartbeat an den Watchdog, und SAFE stellt noch nichts her, es zeigt
den Zustand an.

### Absicht und Regelbetrieb

Die Bedienung legt in einer **Absicht** fest, ob der core regeln soll („Regelbetrieb“) und in
welcher Betriebsart jeder Aktor läuft. Die app sendet sie ab Plan 2d. Bis dahin gibt es keine
Absicht: Der core steht in OBSERVE mit dem Grund „Regelbetrieb ist aus.“, alle Aktoren laufen im
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
4. Der Watchdog ist scharf, sobald ein Victron- oder EVCS-Aktor live angefordert und freigegeben
   ist (Abschnitt 14).
5. Es liegt kein Grund für SAFE vor.

Was nur ein Teilsystem betrifft, sperrt dieses (nächster Abschnitt), statt den ganzen core zu
stoppen. Im laufenden Betrieb führen veraltete Cerbo-Daten, ein Netzausfall und nicht
bestätigte Phasen nach SAFE. Ein fehlender Watchdog, nicht erreichbare Geräte und Sperren
führen nach DEGRADED.

Die Gründe stehen im UI unter der Betriebsart:

| Grund | Bedeutung und Abhilfe |
|---|---|
| Regelbetrieb ist aus. | Die Absicht verlangt keinen Regelbetrieb, oder es gibt noch keine. Bis Plan 2d ist das der Normalfall. |
| Kritische Messwerte noch nicht 30 s aktuell. | Nach dem Start oder einer Störung brauchen die Messwerte 30 s. Hält es an, prüfe die Selbstprüfung „Datenfrische“ und den Adapter `victron`. |
| 3 Phasen mit je einem Gerät nicht bestätigt. | Der Cerbo meldet nicht die erwartete Phasenzahl. Siehe Selbstprüfung „Phasen (vebus)“. |
| Watchdog am Cerbo ist nicht scharf. | Ein Victron- oder EVCS-Aktor ist live angefordert, der Watchdog aber nicht scharf (Abschnitt 14). |
| Cerbo-Daten veraltet: … | Ein kritischer Messwert fehlt oder ist nicht aktuell; der Grund nennt die Signale. |
| Netzausfall (Inselbetrieb). | Der Cerbo meldet Inselbetrieb. |
| EVCS „…“ nicht erreichbar. Wall Connector „…“ nicht erreichbar. Tessie für „…“ nicht erreichbar. | Die Quelle ist getrennt; den Adapter zeigt die Karte „core“. |
| … gesperrt: … | Ein Teilsystem ist gesperrt (nächster Abschnitt). |

### Sperren

Stimmt bei einem Teilsystem etwas nicht oder lässt es sich nicht prüfen, sperrt der core das
Schreiben dorthin (Spec 7.9). Der Rest läuft weiter: Im Regelbetrieb steht der core dann in
DEGRADED, und die Sperre ist der Grund. Das UI zeigt sie unter **System** → „Aktoren“ in der
Spalte „Hinweise“ als „gesperrt: …“.

| Teilsystem | Gesperrt, wenn |
|---|---|
| Victron (ESS) | Dynamic ESS aktiv, ein Victron-Ladefenster aktiv, ESS-Modus oder BatteryLife-Zustand weichen von den erwarteten Werten ab, der Min-SOC liegt unter der USV-Reserve, oder einer dieser Werte ist nicht lesbar |
| EVCS | die EVCS ist nicht im manuellen Modus, oder ihr Modus ist nicht lesbar |
| Fahrzeug mit Tessie | im Fahrzeug ist ein Ladeplan aktiv |

- Ein Min-SOC über der USV-Reserve sperrt nicht; die Selbstprüfung warnt nur.
- Eine Sperre löst sich von selbst, wenn die Ursache 60 s lang weg war (`safety.lock_release_s`
  in `config.yaml`). Kehrt die Ursache zurück, beginnt die Wartezeit von vorn.
- Eine Freigabe per Hand („Quittierung“) gibt es erst mit der Bedienung in Plan 2d.
- Solange Dynamic ESS an ist, bleibt Victron gesperrt. Die EVCS darf trotzdem regeln, denn ihre
  Regelung braucht nur die Netzmessung und den Phasenwächter. So kann der Dry-Run auch vor der
  Live-Schaltung von Victron laufen (Entscheidung des Betreibers vom 10.10.2026).

### Dry-Run und live

Aktoren sind `victron`, `wallbox.<name>` für jede EVCS und `vehicle.<name>` für jedes Fahrzeug
mit Tessie. Der Wall Connector und der e-Golf sind keine Aktoren. Jeder Aktor hat eine
Betriebsart: **Dry-Run** (die Safety-Schicht prüft und protokolliert jeden Befehl, sendet aber
nichts) oder **live**.

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
erlaubt“. Weicht „Wirksam“ ab, nennen die „Hinweise“ den Grund: „In config.yaml nicht für den
Live-Betrieb freigegeben (live_allowed).“ oder „Watchdog am Cerbo ist nicht scharf.“

**Live-Freigabe zurücknehmen:** Bevor du bei einem Victron- oder EVCS-Aktor `live_allowed` wieder
auf `false` setzt oder ihn auf anderem Weg aus der `config.yaml` nimmst (etwa eine EVCS entfernst),
stellst du ihn in der Absicht auf Dry-Run (ab Plan 2d in der Bedienung, bis dahin mit
`PUT /v1/intent`). Warte, bis der Watchdog „wartet“ zeigt: Der core hat dann einmal die Freigabe 0
gesendet. Änderst du zuerst die `config.yaml`, sieht der core den Aktor nach dem Neustart nicht
mehr als live angefordert und sendet keine 0. Ein scharfer Watchdog löst dann 90 s nach dem
letzten Heartbeat aus.

### Reihenfolge der Inbetriebnahme

1. **Watchdog** installieren und die Probe bestehen (Abschnitt 14, Plan 2a).
2. **Prüfprotokoll Teil 2** gemeinsam durchführen. `live_allowed: true` setzt du nur für Aktoren,
   die bestanden haben (ab Plan 2b).
3. **Dry-Run:** Mit der Bedienung in Plan 2d beginnt er. Er dauert 2 Wochen, und die
   Entscheidungen werden gemeinsam gesichtet (Spec 18).
4. **Live-Schaltung** je Aktor in dieser Reihenfolge (Spec 8.7): EVCS, dann Victron-Setpoint,
   dann Tesla.
5. Vor der Live-Schaltung von Victron schaltest du Dynamic ESS ab (Entscheidung vom 10.10.2026).

### Wartung

Für ein Update des core gilt der Ablauf aus Spec 15:

1. Regelbetrieb auf OBSERVE schalten (ab Plan 2d in der Bedienung; vorher steht der core ohnehin
   in OBSERVE).
2. Update einspielen: `docker compose pull && docker compose up -d` (Abschnitt 4).
3. Selbstprüfung abwarten: Die kritischen Messwerte müssen wieder 30 s aktuell sein, und unter
   **System** zeigt die Selbstprüfung keinen Fehler.
4. Zurück auf CONTROL schalten.

Ist ein Victron- oder EVCS-Aktor live, beachte den Watchdog (Abschnitt 14):

- Der Heartbeat hängt an der Betriebsart der Aktoren, nicht am Regelbetrieb. Auch mit
  ausgeschaltetem Regelbetrieb sendet der core weiter Heartbeats, solange ein solcher Aktor live
  angefordert ist.
- Ein Neustart des core dauert meist kürzer als die 90 s: Dann löst der Watchdog nicht aus.
  Dauert die Wartung länger, stelle die Aktoren vorher auf Dry-Run. Der core sendet dann einmal
  die Freigabe 0, und der Watchdog wartet, ohne auszulösen.
- Beim Herunterfahren sendet der core keine Freigabe.

## 14. Watchdog am Cerbo

Der Watchdog ist der Totmann-Schalter der Anlage (Spec 8.6): ein kleiner Python-Dienst auf dem
Cerbo (Venus OS). Solange ein Victron- oder EVCS-Aktor live angefordert ist, schickt der core
alle 10 s einen Heartbeat. Bleibt er 90 s aus, weil VM, Netz oder core ausgefallen sind, stellt
der Watchdog selbst den sicheren Zustand her:

- die EVCS auf den sicheren Strom (`wallboxes.<name>.safe_a`),
- den Min-SOC auf die USV-Reserve (`battery.reserve_soc_pct`),
- die Entladegrenze auf unbegrenzt.

Zustände, Zeiten und D-Bus-Pfade stehen in `cerbo-watchdog/README.md`. Wichtig für den Betrieb:

- Ohne live angeforderten Aktor schickt der core keinen Heartbeat. Der Watchdog wartet dann und
  greift nie ein. Das gilt in Plan 2a immer.
- Heartbeat-Takt und Timeout kommen aus `victron.watchdog` in `config.yaml`.
- Jede Aktion liest zuerst und schreibt nur, wenn der Wert vom Sicherwert abweicht. Danach liest
  sie zurück und wiederholt bei Fehlern (6-mal im Abstand von 5 s).
- Nach einer Auslösung wird der Watchdog wieder scharf, sobald der Heartbeat 30 s lang stabil
  läuft.
- Nach einem Neustart des Cerbo startet ein scharfer Watchdog wieder scharf
  (`/data/bindaems-watchdog/state.json`), und der core hat 90 s, sich zu melden. Eine unlesbare
  Datei gilt als scharf.
- Nimmst du einem Victron- oder EVCS-Aktor `live_allowed`, stelle ihn vorher in der Absicht auf
  Dry-Run (Abschnitt 13, „Live-Freigabe zurücknehmen“). Sonst löst ein scharfer Watchdog 90 s
  nach dem letzten Heartbeat aus, also kurz nach dem Neustart des core.

### Voraussetzungen

- core und app laufen mit den Images von Plan 2a, beide aus demselben Stand. Rolle sie zuerst aus:
  `docker compose pull && docker compose up -d` (Abschnitt 4). Sonst fehlen im Image des core die
  Werkzeuge `bindaems.tools.watchdog_config` und `bindaems.tools.watchdog_probe`, und die
  System-Seite kann den Status des Watchdogs nicht zeigen.
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
bis zu den Aktionen auf dem Cerbo. Sie

1. fragt den Watchdog nach Version und Zustand,
2. macht ihn mit Heartbeats scharf,
3. lässt die Heartbeats ausbleiben, bis er auslöst,
4. wartet 45 s ohne Heartbeat, bis seine Aktionen mit allen Wiederholungen durch sind, und nimmt
   die Heartbeats dann wieder auf, bis er wieder scharf ist,
5. und gibt ihn mit der 0 frei.

**Die Probe löst den Watchdog aus:** Er setzt die EVCS auf den sicheren Strom und schreibt
Min-SOC und Entladegrenze, wenn sie abweichen. Wähle einen Zeitpunkt, zu dem das passt. Prüfe
danach alle drei Werte und stelle sie wieder ein, wie du sie haben willst: den EVCS-Strom, den
Min-SOC (jetzt die USV-Reserve) und die Entladegrenze (jetzt unbegrenzt).

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
- Die Zeile „Auslösung ohne Heartbeat“ nennt die Zahl der fehlgeschlagenen Aktionen. Prüfe nach
  **jeder** Probe zusätzlich das Log des Cerbo (unten, „Logs“), auch wenn die Zahl 0 ist:

  ```bash
  ssh root@cerbo.lan 'grep Aktion /var/log/bindaems-watchdog/current | tai64nlocal | tail -n 20'
  ```

  Die Zeile „Aktionen abgeschlossen: …“ nennt das Ergebnis jeder Aktion (`ok`, `unverändert` oder
  `fehlgeschlagen`). Steht dort „Aktionen abgebrochen …“, endete der Lauf vorzeitig: Eine schon
  gescheiterte Aktion zählt dann als fehlgeschlagen, eine mit „offen“ wurde nie versucht.
- Die Probe verstellt vorher nichts: Stehen die Werte schon auf dem Sicherwert, schreibt der
  Watchdog nichts. Willst du das Zurücksetzen selbst sehen, stelle vor der Probe den EVCS-Strom
  von Hand auf einen anderen Wert und prüfe danach, dass er wieder auf dem sicheren Strom steht.
- Lass die Probe nie laufen, während ein live geschalteter core Heartbeats sendet: Ihre
  abschließende 0 gibt den Watchdog frei. Das ist erst ab Plan 2b relevant.
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

Erzeuge die `config.ini` neu, falls sich `config.yaml` geändert hat, und rufe `install.sh` wie bei
der Installation auf. Das Skript ersetzt Programm, `config.ini` und den Block in `/data/rc.local`
und startet den Dienst neu. `state.json` bleibt: Ein scharfer Watchdog startet wieder scharf.

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
| Der Watchdog am Cerbo hat ausgelöst und den sicheren Zustand hergestellt. | Der Heartbeat blieb 90 s aus, oder die Probe lief. Suche im Log des core, warum er ausfiel. Nach 30 s stabilem Heartbeat ist der Watchdog wieder scharf. Nennt der Alarm fehlgeschlagene Aktionen, prüfe EVCS-Strom, Min-SOC und Entladegrenze von Hand und lies das Log des Cerbo. |
| Watchdog am Cerbo antwortet nicht; Victron und EVCS bleiben im Dry-Run. | Nur bei einem live angeforderten Aktor. Prüfe, ob der Dienst auf dem Cerbo läuft (`/State` lesen, Log) und ob der core den Cerbo über MQTT erreicht. |
| Watchdog am Cerbo ist deaktiviert; Victron und EVCS bleiben im Dry-Run. | Nur bei einem live angeforderten Aktor. In der `config.ini` steht `enabled = false`. Erzeuge sie neu und übertrage sie. |
| Watchdog-Version … am Cerbo, erwartet …; bitte mit install.sh aktualisieren. | Siehe „Aktualisieren“. |

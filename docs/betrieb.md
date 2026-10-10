# Betrieb von BindaEMS

Stand: Phase 1c. Es laufen zwei Dienste, beide steuern **nichts**:

- `ems-core` im Modus **OBSERVE** liest Cerbo GX, EVCS, Wall Connector, Tessie und Home
  Assistant und schreibt Telemetrie nach InfluxDB.
- `ems-app` meldet Benutzer an und liefert das Web-UI samt API aus (Abschnitt 12). Sie holt
  Preise und die PV-Prognose, rechnet jede Viertelstunde ab und meldet lesende Sensoren per
  MQTT Discovery an Home Assistant.

## 1. Voraussetzungen der VM

- Debian (aktuelle stabile Version) mit Docker Engine und Compose-Plugin.
- Richtwert: 2 vCPU, 4 GB RAM, 20 GB Platte.
- **NTP muss aktiv sein**, denn die Viertelstunden-Slots hängen an der Uhrzeit.
  Prüfen mit `timedatectl` (Zeile `System clock synchronized: yes`).
- Netzzugang:
  - im LAN: Cerbo GX (MQTT 8883), EVCS (Modbus TCP 502), Wall Connector (HTTP 80),
    InfluxDB (8086), Home Assistant (8123)
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
HA-Entitäten (`influxdb.ha_database`), später auch für die Lernmodelle. Die app schreibt
Preise, PV-Prognose und Abrechnung (`price`, `forecast`, `ledger`) direkt in `long`.

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
| Betriebsart | – | Modus des core (`OBSERVE`), Diagnose |
| core verbunden | an/aus | Diagnose |
| Mitregelndes System | Problem | ein anderes System regelt mit (z. B. Dynamic ESS) |
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
- Der core startet immer im Modus OBSERVE. Ab Phase 2 schützt der Watchdog auf dem Cerbo die
  Victron-Steuerung während eines Failovers.
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
| Übersicht | Energiefluss live, Strompreis jetzt und die nächsten 3 h, Ladestände, Preise und PV-Prognose für heute und morgen, Hinweise, Verbraucher |
| Verlauf | Diagramm mit bis zu 8 Reihen für frei wählbare Tage (höchstens 400), Tagesbilanz mit Abdeckung (Viertelstunden und Anteil der aufgezeichneten Zeit), Kosten, Erlös, Autarkie und den Tageswerten der Zähler (Differenz über den Tag) für den Abgleich mit VRM |
| Verbraucher | Verbraucherbaum mit „Sonstiges“; Admins legen Verbraucher an, ändern und löschen sie |
| System | Komponenten, Hinweise, core (Adapter, Selbstprüfung, Alarme), Preise (Admins: „Preise jetzt abrufen“), PV-Prognose, alle Signale mit Suche |
| Einstellungen | Tarif, OeMAG-Werte, Preise, PV-Modell und harte Grenzen; Admins bearbeiten, bewerten neu und exportieren oder importieren (Abschnitt 8) |
| Benutzer | nur Admins: anlegen, Rolle ändern, Passwort setzen, löschen |
| Protokoll | nur Admins: Änderungsprotokoll nach Bereich |
| Konto | eigenes Passwort, Zwei-Faktor-Anmeldung, Abmelden |

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

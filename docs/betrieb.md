# Betrieb von BindaEMS

Stand: Plan 5a (Lastmodell) und Plan 6a (Klimaanlage) auf dem Stand von Plan 4b (Geplantes Laden), Plan 4a (Statistik), 3c (Planausführung), 3b (Optimierer und Zeitplan), 3a (Tarif und Prognosen) und Phase 2d (Bedienung in UI und HA). Auf der VM laufen zwei Dienste. Der core **regelt** seit Plan
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
  den core: Regelbetrieb, Lademodi, Boost, Zuordnung und der Live-Schalter (Abschnitt 13). Seit
  Plan 3b plant sie außerdem den Akku für bis zu 36 h und zeigt den Plan samt dem Stand seiner
  Ausführung im core (Seite „Zeitplan“, Abschnitt „Planung“). Sie speichert das
  Entscheidungslog, meldet Sensoren und Bedienelemente per MQTT Discovery an Home Assistant und
  meldet Alarme dort als Benachrichtigung (Abschnitt 7). Seit Plan 4a zeichnet sie außerdem die
  Ladesessions auf, rechnet den Einstandspreis des Akkus und die Kosten je Auto, gleicht die
  Abrechnung jede Nacht mit den Zählern ab, rechnet Lücken nach und zeigt das alles auf der Seite
  „Statistik“ (Abschnitt 8 und 12). Das alles liest nur; Plan 4a ändert den core nicht.
- Seit Plan 4b lädt ein Auto im Modus **„Geplant“** bis zur Abfahrt auf seinen Ziel-SOC: Die app
  plant die angesteckten Autos in diesem Modus gemeinsam mit dem Akku, und der core folgt der
  Ladeleistung des Plans, ohne Plan der spätesten Startzeit, und stoppt am Ziel (Abschnitte
  „Laderegelung“, „Geplant“, und „Planung“, „Autos im Plan“). Ziel-SOC und Abfahrt bedienst du auf
  der Seite „Laden“ und in Home Assistant (Abschnitt 7, „Geplantes Laden“). Plan 4b ändert core und
  app; beide rollen gemeinsam aus (Abschnitt „Laderegelung“, „Geplantes Laden ausrollen (Plan 4b)“).
- Seit Plan 6a führt der core auch die **Klimaanlage** (UVR610 über das C.M.I.): Er liest ihre Werte
  per CoE (UDP 5442) und schickt ihr einen Sollwertversatz von höchstens ±3 K, wenn PV-Überschuss
  bleibt, den Akku und Autos nicht brauchen. Der Aktor `thermal.<name>` startet im Dry-Run und
  sendet dann nur 0. `ems-core` veröffentlicht dafür als einziger Dienst einen Port, `5442/udp`;
  Routing und Firewall zwischen den Subnetzen sind ein Schritt des Betreibers (Abschnitt
  „Klimaanlage (Phase 6a)“). Plan 6a ändert core, app, UI und Deploy.
- Seit Plan 5a lernt die app die Hauslast zusätzlich mit einem **Modell** (LightGBM). Es nutzt
  Kalender, Temperatur, die Last der Vortage und wenige Sensoren aus Home Assistant, wird jede Nacht
  neu angelernt und gilt für die Planung nur, solange es in den letzten 14 Tagen mindestens 10 %
  genauer ist als das Profil; sonst gilt das Profil. Die Seite **Prognosen** zeigt Verfahren,
  Prognosegüte, Backtest und Merkmale (Abschnitt „Lastmodell (Phase 5a)“). Plan 5a ändert nur die
  app und das UI, der core bleibt gleich, und er schaltet nichts an der Anlage.

Dazu kommt der **Watchdog** auf dem Cerbo, den du einmal installierst (Abschnitt 14). Er ist der
Totmann-Schalter der Anlage und greift nur ein, wenn ein live geschalteter core ausfällt.

Am Ende des Handbuchs stehen die Abschnitte „Laderegelung“ (Lademodi, „Geplant“, Absicht, Zuordnung
Auto und Wallbox, Dry-Run-Vergleich mit evcc, Umstieg, Hinweise für die Anlage, Ausrollen von Plan
4b), „Akku und Spitzen“ (Akku-Sollwert, 15-min-Wächter, Health, Victron in den Dry-Run und live,
Alarme), „Prognosen“ (PV-Prognose mit Kalibrierung und Band, Lastprognose, Prognosegüte,
Schätzpreise, nächtlicher Lauf), „Lastmodell (Phase 5a)“ (Ablauf, Mindestdaten, Aktivierung, Rückfall,
Sensorauswahl, Seite „Prognosen“, Ausrollen), „Planung“ (Zeitplan, Schalter „Netzladen erlaubt“, Autos im Plan,
Auslöser, Ersatzplan, Erklärungen, Vergleich mit „Victron allein“), „Klimaanlage (Phase 6a)“ (Netz
und Firewall, Konfiguration, Empfang prüfen, Prüfprotokoll, Dry-Run, live, Gründe, Alarme,
Fehlersuche) und „Simulator“ (die Szenarien der CI von Hand laufen lassen).

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

### Grafana

`deploy/grafana/bindaems.json` ist ein Startdashboard für die Datenbank `bindaems`. Es enthält keine
Zugangsdaten und keine Daten der Anlage; die Datenquelle wählst du beim Import.

1. In Grafana eine Datenquelle vom Typ InfluxDB anlegen (Connections → Data sources): Abfragesprache
   InfluxQL, die URL der InfluxDB, Datenbank `bindaems`. Für Grafana genügt ein Benutzer mit
   Lesezugriff, z. B. `CREATE USER "grafana" WITH PASSWORD '…'` und
   `GRANT READ ON "bindaems" TO "grafana"`.
2. Dashboards → New → Import, die Datei hochladen und bei der Eingabe `DS_BINDAEMS` die Datenquelle
   aus Schritt 1 wählen. Ein erneuter Import mit derselben Datei ersetzt das Dashboard.

Das Dashboard zeigt fünf Zeilen. Der Zeitraum oben gilt für alle Panels außer der letzten Zeile;
die Zeitzone ist `Europe/Vienna`.

- „Leistung“: die mittlere Leistung (Summe der Phasen) von Netz, PV gesamt, Haus (Verbrauch), Akku
  und Wallboxen aus der Retention Policy `raw`; PV gesamt und Haus sind die abgeleiteten Werte des
  core (`pv_total`, `house_load`), einzeln gemessene Lasten kommen als „Einzellast“ dazu. Die PV
  je Wechselrichter fehlt absichtlich, sie würde doppelt zählen.
- „Ladestand“: der Ladestand des Akkus und der Fahrzeuge aus `raw`.
- „Klimaanlage“: Ist- und Solltemperatur des Raums in °C, der gesendete Versatz und das Echo der
  Regelung in K sowie die Leistung der Klimaanlage in Watt aus `raw`. Der gesendete Versatz wird
  nur bei Änderung geschrieben; das Panel führt ihn bis zur nächsten Änderung fort. Der Wunsch
  steht nicht in InfluxDB, sondern im Entscheidungslog.
- „Preise“: Bezugspreis brutto und Einspeisevergütung in ct/kWh je Viertelstunde aus `long`. Die
  Preise von morgen erscheinen, sobald sie geholt sind und der Zeitraum bis morgen reicht.
- „Abrechnung je Tag“: kWh sowie Kosten und Erlös in Euro je Fluss und Tag aus `long`, immer die
  letzten 13 ganzen Tage und heute bis jetzt (Zeitbereich des Panels `now-13d/d`, der letzte Balken
  wächst über den Tag). Der Kopf des Panels zeigt dazu „Last 13 days“, obwohl 14 Balken stehen (13
  ganze Tage und heute). Die Tage laufen in Wiener Zeit (`tz('Europe/Vienna')`), auch an den Tagen
  der Zeitumstellung. Eine Viertelstunde ohne Preis zählt dort 0 €; verliert sie ihren Preis beim
  neu Bewerten, überschreibt die app den alten Euro-Wert mit 0.

Die Entscheidungen des Reglers erscheinen als Markierungen („Entscheidungen“) in allen Panels: mit
dem Text, der Wallbox oder dem Akku und dem Ergebnis. Sie stehen nur in `raw`, also 90 Tage lang.
Leistung und Ladestand zeigen ebenfalls höchstens 90 Tage; ältere Daten liegen als
Minutenmittel in `long` und lassen sich in Grafana mit der Retention Policy `long` anstelle von
`raw` abfragen (Panel bearbeiten).

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
- **Update mit Plan 4a (Statistik):** Plan 4a ändert nur die app. Du ziehst und startest nur `ems-app`
  (`docker compose pull ems-app && docker compose up -d --no-deps ems-app`); der core läuft weiter, und
  der Regelbetrieb bleibt an. Die Schritte stehen am Ende von Abschnitt 8, „Statistik ausrollen
  (Plan 4a)“.
- **Update mit Plan 4b (Geplantes Laden):** Plan 4b ändert core und app; beide rollen gemeinsam aus,
  mit ausgeschaltetem Regelbetrieb während des Wechsels. Die Schritte stehen im Abschnitt
  „Laderegelung“, „Geplantes Laden ausrollen (Plan 4b)“.
- **Update mit Plan 5a (Lastmodell):** Plan 5a ändert nur die app. Du ziehst und startest nur
  `ems-app` (`docker compose pull ems-app && docker compose up -d ems-app`); der core läuft weiter, und
  der Regelbetrieb bleibt an. Ausgerollt wird mit oder nach 4b und 6a; die Schritte stehen im
  Abschnitt „Lastmodell (Phase 5a)“, „Lastmodell ausrollen (Plan 5a)“.
- **Update mit Plan 5b (Fahrverhalten):** Plan 5b ändert core und app; beide rollen gemeinsam aus, mit
  ausgeschaltetem Regelbetrieb während des Wechsels, zusammen mit 3d, 4b, 5a und 6a. Die Schritte
  stehen im Abschnitt „Fahrverhalten (Phase 5b)“, „Fahrverhalten ausrollen (Plan 5b)“.
- **Update mit dem Sofort-Limit:** Der Plan Sofort-Limit ändert core und app; beide rollen
  **gemeinsam** aus, mit dem nächsten gemeinsamen Pull (`docker compose pull && docker compose up -d`).
  Die Absicht hat ein neues Feld, ein alter core lehnt es ab. Es gibt keine Migration, und die ACL des
  Mosquitto-Benutzers `bindaems` bleibt unverändert (die Zahlen sind die Plattform `number`, die schon
  freigegeben ist). Home Assistant bekommt je Fahrzeug die Zahl „Sofort-Höchststrom“ und, mit einer
  EVCS, „Gast-Sofort-Höchststrom“. Alle Autos laden danach mit „Volle Leistung“, bis du ein Limit
  wählst. Den Rückweg beschreibt der nächste Punkt.
- **Rollback auf einen Stand vor dem Sofort-Limit:** Die Absicht des core hat das neue Feld
  `now_max_a`, und ein älterer core lehnt unbekannte Felder ab (`extra="forbid"`). Das Feld steht in
  `intent.json` (unter `core.data_dir`, im Volume `core-data` `/data/intent.json`; die Absicht steht
  dort unter dem Schlüssel `intent`) bei **jedem** Eintrag unter `vehicles` und unter `guest`, auch
  mit dem Wert `null`: „Volle Leistung“ vor dem Rollback zu wählen reicht also nicht. Ein älterer
  core liest die Datei sonst nicht, startet ohne Absicht (Regelbetrieb aus) und meldet den Alarm
  `intent.file`, wie beim Rollback vor Plan 5b (unten). Core und app gehen gemeinsam zurück, auf
  dasselbe Tag. Schalte vorher den **Regelbetrieb aus** (System → „Bedienung“) und entferne die
  Schlüssel, bevor du das Tag änderst:

  ```bash
  cd /opt/bindaems
  docker compose stop ems-core
  docker compose run --rm --no-deps --entrypoint python ems-core -c '
  import json
  from pathlib import Path
  path = Path("/data/intent.json")
  document = json.loads(path.read_text())
  intent = document["intent"]
  for entry in [*intent.get("vehicles", {}).values(), intent.get("guest", {})]:
      entry.pop("now_max_a", None)
  path.write_text(json.dumps(document))
  print("now_max_a entfernt")'
  ```

  Danach in `.env` `BINDAEMS_VERSION=<tag>` setzen und `docker compose up -d` (core und app), und
  erst danach den Regelbetrieb wieder einschalten. Gehst du ohne diesen Schritt zurück, setzt du die
  Absicht danach neu (Bedienung oder `PUT /v1/intent`). Die Datenbank der app ist nicht betroffen
  (keine Migration). In Home Assistant bleiben die Zahlen „Sofort-Höchststrom“ und
  „Gast-Sofort-Höchststrom“ als nicht mehr gepflegte Entitäten stehen, bis du sie dort entfernst.
- **Rollback auf einen Stand vor Plan 5b:** Die Migration `0014` der app ist beim Start gelaufen; eine
  ältere app kennt sie nicht und startet mit dieser Datenbank nicht. Core und app gehen gemeinsam
  zurück, auf dasselbe Tag, mit **Regelbetrieb aus** (System → „Bedienung“) vor dem Wechsel und erst
  danach wieder an. Es gibt zwei Wege für die Datenbank:
  - **Datenbank zurückführen (empfohlen, die übrigen Daten bleiben):** Noch mit dem Image von Plan 5b,
    also bevor du das Tag änderst, setzt du die Datenbank auf den Stand `0013`:

    ```bash
    cd /opt/bindaems
    docker compose stop ems-app
    docker compose run --rm --no-deps --entrypoint python ems-app -c '
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from bindaems.app.db.engine import MIGRATIONS_DIR, open_database
    engine = open_database(Path("/data/bindaems.sqlite3"))
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0013")
    engine.dispose()
    print("Datenbank auf Stand 0013")'
    ```

    Danach in `.env` `BINDAEMS_VERSION=<tag>` setzen und `docker compose up -d` (core und app). Verloren
    gehen nur die Daten von Plan 5b: die Profile (`driving_profile`), der Stand der Automatik
    (`driving_automation`) und der Abschnitt `learning.driving` der Einstellungen (Puffer, Bereich,
    Automatik, Entitäten); alles andere bleibt.
  - **Kopie vor dem Ausrollen einspielen** (`/backup/vor-plan-5b.sqlite3`, Wiederherstellen wie in
    Abschnitt 9): Das setzt die **ganze** Datenbank der app auf den Stand vor dem Ausrollen zurück, mit
    den Folgen wie beim Rollback vor Plan 5a. Diesen Weg nimmst du nur, wenn das Zurückführen scheitert.
  - Ein core vor Plan 5b liest die Absicht eines core ab 5b nicht, sobald sie einen Eintrag unter
    `vehicles` hat (jeder trägt jetzt `departure_on`; die Modelle verbieten unbekannte Felder). Er
    startet dann ohne Absicht mit dem Alarm `intent.file`, wie beim Rollback vor Plan 4b. Setze die
    Absicht danach neu (Bedienung oder `PUT /v1/intent`).
- **Rollback auf einen Stand vor Plan 5a:** Die Migration `0013` der app ist beim Start gelaufen; eine
  ältere app kennt sie nicht und startet mit dieser Datenbank nicht. Der core ist nicht betroffen. Es
  gibt zwei Wege:
  - **Datenbank zurückführen (empfohlen, die übrigen Daten bleiben):** Noch mit dem Image von Plan 5a,
    also bevor du das Tag änderst, setzt du die Datenbank auf den Stand `0012`:

    ```bash
    cd /opt/bindaems
    docker compose stop ems-app
    docker compose run --rm --no-deps --entrypoint python ems-app -c '
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from bindaems.app.db.engine import MIGRATIONS_DIR, open_database
    engine = open_database(Path("/data/bindaems.sqlite3"))
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, "0012")
    engine.dispose()
    print("Datenbank auf Stand 0012")'
    ```

    Danach in `.env` `BINDAEMS_VERSION=<tag>` setzen und `docker compose up -d ems-app`. Verloren
    gehen nur die Daten von Plan 5a: die Registry der Modelle, das Wetterarchiv, die Schatten im
    Archiv der Prognosen (`load_profile`, `load_model`) samt ihrer Güte und der Abschnitt
    „Lastmodell“ der Einstellungen (Schalter und Sensoren); alle anderen Einstellungen bleiben, die
    ältere app liest sie ohne Warnung. Abrechnung, Statistik, Ladesessions, Belegung, geplante
    Ladungen, Pläne, Benutzer und das Audit-Log bleiben. Die Dateien unter `/data/models/load/`
    darfst du löschen. Der Befehl braucht das Image mit Plan 5a; das ältere Image kennt die Migration
    `0013` nicht und kann sie nicht zurücknehmen.
  - **Kopie vor dem Ausrollen einspielen** (`/backup/vor-plan-5a.sqlite3`, Wiederherstellen wie in
    Abschnitt 9): Das setzt die **ganze** Datenbank der app auf den Stand vor dem Ausrollen zurück.
    Was die app beim Start nachholt, steht in Abschnitt 9: Preise neu von den Quellen, die
    Viertelstunden der Abrechnung nur der letzten 7 Tage aus InfluxDB, die Statistik, soweit sie sich
    rechnen lässt. Verloren geht alles andere seit dem Ausrollen: die Abrechnung davor (nach den 28
    Tagen der Abnahme also drei Wochen), Ladesessions und Belegung, die geplanten Ladungen, die Pläne
    und das Archiv der Prognosen, die Versionen der Einstellungen (es gelten wieder die Einstellungen
    vom Ausrollen), Änderungen an Benutzern und Verbrauchern, das Audit-Log sowie Modelle und
    Wetterarchiv. Diesen Weg nimmst du nur, wenn das Zurückführen scheitert.
- **Rollback auf einen Stand vor Plan 4b:**
  - Die Migration `0012` der app ist beim Start gelaufen; eine ältere app startet mit dieser
    Datenbank nicht. Spiele die Kopie ein, die du vor dem Ausrollen gemacht hast
    (`/backup/vor-plan-4b.sqlite3`, Wiederherstellen wie in Abschnitt 9). Die geplanten Ladungen und
    die Statistik seit dem Ausrollen gehen dabei verloren.
  - Ein core vor Plan 4b kann eine `intent.json`, die ein core ab Plan 4b geschrieben hat, nicht
    lesen, sobald sie einen Eintrag unter `vehicles` oder einen Plan hat (jeder Eintrag trägt
    `target_soc_pct` und `departure`, jeder Slot `vehicles`; der ältere core lehnt unbekannte Felder
    ab). Er startet dann ohne Absicht mit dem Alarm `intent.file`, wie beim Rollback vor Plan 3c
    (unten): Er regelt nichts, und nichts geht live. Rolle core und app gemeinsam zurück (dasselbe
    Tag), schalte den Regelbetrieb wieder ein und stelle Lademodi, Gast-Modus und Priorität neu ein.
- **Rollback auf einen Stand vor Plan 4a:** Die Migrationen `0010` und `0011` der app sind beim
  Start gelaufen. Eine ältere app kennt diese Versionen nicht und startet mit dieser Datenbank
  nicht. Spiele die Kopie ein, die du vor dem Ausrollen gemacht hast (`/backup/vor-plan-4a.sqlite3`,
  Schritt 1 in Abschnitt 8, „Statistik ausrollen (Plan 4a)“; Wiederherstellen wie in Abschnitt 9).
  Die nächtlichen Sicherungen helfen nur am Tag des Ausrollens, vor 04:00 Uhr: Schon die erste
  Sicherung danach enthält `0010` und `0011`. Die Statistik seit dem Ausrollen geht dabei
  verloren. Der core ist nicht betroffen.
- **Rollback auf einen Stand vor Plan 3c:**
  - Ein core vor Plan 3c kann keine `intent.json` lesen, die ein core ab Plan 3c geschrieben hat (sie
    hat immer die Felder `plan` und `support`, und der ältere core lehnt unbekannte Felder ab). Er startet dann ohne Absicht und meldet den Alarm
    `intent.file` („Gespeicherte Absicht unlesbar; der core startet ohne Absicht (Regelbetrieb
    aus).“): Er regelt nichts, und nichts geht live. Das ist die sichere Richtung, aber Lademodi,
    Gast-Modus, Priorität, die Modi der Aktoren, Boost und Übersteuerung fehlen.
  - Rolle core und app immer gemeinsam zurück (dasselbe Tag). Schalte danach im UI den Regelbetrieb
    wieder ein und stelle Lademodi, Gast-Modus und Priorität neu ein; die erste angenommene Absicht
    beendet den Alarm und überschreibt die Datei. Notiere dir die Einstellungen vorher (Seite
    „Laden“).
  - Gehst du ohne neue Bedienung wieder auf den Stand mit Plan 3c, liest der core die alte Datei
    wieder: Der ältere core liest sie nur und schreibt sie erst mit einer angenommenen Absicht neu.
- **Geordnetes Herunterfahren:**
  - `docker compose stop` sendet SIGTERM. Der core schreibt dann die laufende Viertelstunde
    und die Warteschlange und beendet sich mit Code 0. Die app schreibt ihre Warteschlange für
    InfluxDB, schließt alle Verbindungen und beendet sich ebenfalls mit Code 0.
  - Der core gibt den Watchdog dabei **nicht** frei. Ist ein Aktor live und dauert ein Neustart
    samt 30 s frischer Messwerte länger als 90 s, löst der Watchdog aus (Abschnitt 14). Das gilt
    auch für die Werkzeuge `coe_probe listen` und `verify_part2 --punkt thermal`, die den core
    für Minuten stoppen (Abschnitt „Klimaanlage (Phase 6a)“, „Reihenfolge an der Anlage“).
- **Logs:**
  - JSON auf stdout. Docker rotiert sie (5 × 10 MB).
- **Healthcheck:**
  - Der core fragt alle 30 s `/v1/health` der internen API ab, die app `/health`.
  - Wurde `core_api.port` bzw. `app.port` in `config.yaml` geändert, muss
    `BINDAEMS_CORE_PORT` bzw. `BINDAEMS_APP_PORT` in `.env` denselben Wert haben.
- **Diagnose:**
  - Die interne API ist absichtlich nicht nach außen veröffentlicht. Der einzige Port, den
    `ems-core` veröffentlicht, ist `5442/udp` für CoE (Abschnitt „Klimaanlage (Phase 6a)“);
    `ems-app` hat Port 8080.
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
brauchen dafür sieben Zeilen mehr als die Sensoren: Schreibrechte für `select`, `switch`, `number` (seit
Plan 3c, die Unterstützungsschwelle), `time` (seit Plan „Abfahrt als Uhrzeit“, die Abfahrtszeit; dafür
brauchst du Home Assistant ab 2026.5), `text`, `button` und das Leserecht auf `bindaems/cmd/#`. Die
Zeile für `text` bleibt, obwohl die Abfahrtszeit kein Textfeld mehr ist: Die app veröffentlicht bei
jedem MQTT-Verbinden und beim Neustart von Home Assistant eine leere Konfiguration für das alte
Textfeld, damit Home Assistant es entfernt, und dafür braucht sie das Schreibrecht. Ohne die Zeile für
`time` fehlt die Entität „Abfahrtszeit“ in Home Assistant. Gehst du mit `ems-app` auf einen Stand vor
diesem Plan zurück, bleibt die gehaltene Discovery der Uhrzeit stehen; lösche sie dann mit
`mosquitto_pub -r -n -t homeassistant/time/bindaems/<objekt-id>_departure/config` je Fahrzeug.

```text
user bindaems
topic write homeassistant/sensor/bindaems/#
topic write homeassistant/binary_sensor/bindaems/#
topic write homeassistant/select/bindaems/#
topic write homeassistant/switch/bindaems/#
topic write homeassistant/number/bindaems/#
topic write homeassistant/time/bindaems/#
topic write homeassistant/text/bindaems/#
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
| Monatsspitze | kW | höchster Netzbezug einer Viertelstunde im laufenden Monat (Abschnitt 8); Attribut `slot` mit dem Beginn dieser Viertelstunde (ISO-Zeit). „Unbekannt“, solange der Monat keine Abrechnung hat |
| Akku-Plan jetzt | – | Absicht des Plans für die laufende Viertelstunde (Eigenverbrauch, Halten, Netzladen); Attribute `reason` (der Satz der Erklärung), `codes`, `planned_soc_pct`, `grid_charge_w`, `start`, `source`. „Unbekannt“ ohne gültigen Plan (Abschnitt „Planung“) |
| Akku-Plan nächster Slot | – | wie oben für die nächste Viertelstunde |
| Geplanter SOC | % | SOC am Ende der laufenden Viertelstunde laut Plan; Attribut `hourly` mit dem SOC je volle Stunde |
| Budget | kW | die vom Plan akzeptierte Monatsspitze der laufenden Viertelstunde; nur mit Leistungspreis |
| Ersparnis heute | EUR | Ersparnis von heute gegenüber einem Fixtarif ohne Anlage (zwei Nachkommastellen, Geräteklasse „Geldbetrag“); Attribut `same_import_eur` mit der Ersparnis bei gleichem Netzbezug. „Unbekannt“, solange ein Preis des Tages fehlt |
| Ersparnis Monat | EUR | wie oben für den Monat bis heute |
| Autarkie heute | % | Anteil des Verbrauchs von heute, der nicht aus dem Netz kam (ganze Zahl); Attribut `month_pct` mit der Autarkie des Monats. „Unbekannt“, solange heute noch nichts verbraucht wurde |
| Akku-SOC | % | |
| Betriebsart | – | Betriebszustand des core: `START`, `OBSERVE`, `CONTROL`, `DEGRADED` oder `SAFE` (Abschnitt 13), Diagnose |
| core verbunden | an/aus | Diagnose |
| Mitregelndes System | Problem | ein anderes System regelt mit (z. B. Dynamic ESS) |
| Watchdog ausgelöst | Problem | der Watchdog am Cerbo hat ausgelöst (Abschnitt 14); unbekannt, solange er nicht antwortet |
| Preise fehlen | Problem | kein Preis für jetzt oder ab 18 Uhr keiner für morgen (smartENERGY veröffentlicht um 17 Uhr, die app fragt von 17 bis 19 Uhr alle 5 min nach) |
| Gerät offline | Problem | ein Adapter des core ist getrennt; Attribut `adapters` |
| Zuordnung unklar | Problem | eine Wallbox oder ein Fahrzeug zeigt `unclear` (Abschnitt „Laderegelung“, „Zuordnung Auto und Wallbox“) |
| Ziel-SOC unerreichbar | Problem | ein Auto im Modus „Geplant“ erreicht sein Ziel bis zur Abfahrt nicht (Unterabschnitt „Geplantes Laden“); Attribut `vehicles` mit den Namen |
| SOC (Gerät „BindaEMS <Fahrzeug>“) | % | je Fahrzeug |
| Wallbox (Gerät „BindaEMS <Fahrzeug>“) | – | je Fahrzeug der Name der Wallbox, an der es steht (eigener Name oder „EVCS“ / „Wall Connector“), sonst „keine“ |
| Fertig um, Ziel erreichbar (Gerät „BindaEMS <Fahrzeug>“) | – | je Fahrzeug im Modus „Geplant“ (Unterabschnitt „Geplantes Laden“) |
| Vorschlag Abfahrt, Vorschlag Ziel-SOC, Nächste Abfahrt (Gerät „BindaEMS <Fahrzeug>“) | –, %, – | je Fahrzeug der gelernte Vorschlag und die nächste Abfahrt (Abschnitt „Fahrverhalten (Phase 5b)“, „Home Assistant“); „Unbekannt“ ohne Vorschlag |
| Ladeenergie (Gerät „BindaEMS <Fahrzeug>“) | kWh | je Fahrzeug die Energie der laufenden Ladesession, sonst der letzten (eine Nachkommastelle, Geräteklasse „Energie“, ohne Zustandsklasse); Attribute `start` und `end` (Ortszeit, `end` ist `null`, solange die Session läuft), `pv_kwh`, `battery_kwh`, `grid_kwh` (Herkunft der Energie), `cost_eur` (`null`, wenn ein Preis fehlt oder der Einstandspreis des Akkus unbekannt ist) und `month_kwh` (Ladeenergie des Fahrzeugs im Monat). „Unbekannt“ ohne laufende Session und ohne beendete der letzten 90 Tage (eine laufende zählt, egal wie lange sie schon läuft) |
| Klima Versatz (`thermal_<Schlüssel>_offset`) | K | je Klimaanlage (Abschnitt „Klimaanlage (Phase 6a)“) der Versatz, den die UVR anwendet (das Echo), ersatzweise der zuletzt gesendete; eine Nachkommastelle. „Klima“ steht für den Namen der Klimaanlage (`label`): bei einem eigenen Namen steht er ganz davor, etwa „Büro Versatz“ |
| Klima Betriebsart (`thermal_<Schlüssel>_mode`) | – | `heizen`, `kuehlen`, `lueften`, `aus` oder `unbekannt` |
| Klima Leistung (`thermal_<Schlüssel>_power`) | W | elektrische Leistung der Klimaanlage laut UVR; „unbekannt“ ohne Wert |
| Klima Wunsch (`thermal_<Schlüssel>_wish`) | – | der Leistungswunsch der Strategie in ganzen Stufen (positiv: mehr Leistung, negativ: weniger); „unbekannt“, solange die Regelung ruht |
| Ladeleistung (Gerät „BindaEMS EVCS“ / „BindaEMS Wall Connector“) | W | je Wallbox |
| Fahrzeug (Gerät der Wallbox) | – | je Wallbox der Name des Fahrzeugs, „Gast“ oder „keins“ |

Die Sensoren zu Ersparnis, Autarkie und Ladeenergie sind nur lesend. Die app rechnet sie nicht im
5-s-Takt der übrigen Werte, sondern bei jeder neuen Viertelstunde der Abrechnung und spätestens alle
15 Minuten neu; scheitert die Rechnung, behalten die Sensoren ihren letzten Wert. Nur über Mitternacht
hinweg nicht: Dann zeigen alle Statistik-Sensoren (auch „Ersparnis Monat“ und das Attribut `month_pct`)
„unbekannt“ statt den Wert von gestern.
Die Zahlen sind dieselben wie in der Statistik je Zeitraum (Abschnitt „Statistik je Zeitraum“,
`GET /api/stats`). „Ladeenergie“ hat keine Zustandsklasse, denn sie gilt je Session und ist kein
Zählerstand; Home Assistant führt dafür keine Langzeitstatistik. Welche Session „laufend oder letzte“
ist, fragt die app je Fahrzeug zuerst nach der neuesten offenen Session (egal wie lange sie schon
läuft) und nur ohne sie nach der neuesten beendeten der letzten 90 Tage.

Fährt die app geordnet herunter, meldet sie sich selbst ab; bei einem Absturz oder
Verbindungsabbruch setzt der Broker alle Entitäten über den Last Will auf „nicht verfügbar“.

### Bedienelemente und Befehle

Seit Plan 2d gibt es Bedienelemente. Home Assistant hat dabei die Rechte der Rolle „Bedienen“
(Abschnitt 12), aber keinen Benutzer in der app: Du legst dafür nichts an. Im Änderungsprotokoll
steht die Quelle „ha“.

| Entität | Plattform | Wirkung |
|---|---|---|
| Lademodus (je Fahrzeug) | Auswahl | PV-Überschuss, Min+PV, Sofort, Aus oder Geplant; „Boost“ gibt es hier nicht |
| Ziel-SOC (je Fahrzeug) | Zahl | das Ziel für „Geplant“ in %, eine ganze Zahl von 20 bis 100; die Abfahrt bleibt |
| Sofort-Höchststrom (je Fahrzeug) | Zahl | der höchste Ladestrom in „Sofort“ in A je Phase, eine ganze Zahl von `min_a` bis `max_a` des Fahrzeugs; der Höchststrom des Fahrzeugs heißt „volle Leistung“. Das Limit bleibt über Abstecken und Neustart stehen |
| Abfahrtszeit (je Fahrzeug) | Uhrzeit | die tägliche Abfahrt für „Geplant“, Uhrzeit von 00:00 bis 23:59 (Sekunden fallen weg); das Ziel bleibt |
| Boost (je Fahrzeug) | Taste | Boost bis zum Abstecken, höchstens 12 h |
| Boost beenden (je Fahrzeug) | Taste | beendet den Boost |
| Automatik Ziel und Abfahrt (je Fahrzeug) | Schalter | schaltet die Automatik, die Ziel und Abfahrt von „Geplant“ nach dem Vorschlag setzt (Abschnitt „Fahrverhalten (Phase 5b)“); ab Werk aus. Eine Änderung ist eine neue Version der Einstellungen und steht im Änderungsprotokoll als `control.automation` mit der Quelle „ha“ |
| Zuordnung (je Wallbox) | Auswahl | „Automatisch“ oder der Name eines Fahrzeugs; „Automatisch“ hebt jede Übersteuerung dieser Wallbox auf (Abschnitt „Laderegelung“, „Zuordnung Auto und Wallbox“) |
| Regelbetrieb | Schalter | schaltet den Regelbetrieb ein oder aus |
| Gast-Lademodus | Auswahl | der Modus für einen Gast an jeder Wallbox; ohne „Geplant“, denn für einen Gast gibt es weder Ladestand noch Abfahrt |
| Gast-Sofort-Höchststrom | Zahl | wie der Sofort-Höchststrom, für jeden Gast an der EVCS: eine ganze Zahl vom kleinsten `min_a` bis zum größten `max_a` aller EVCS; die Zahl gibt es nur mit einer EVCS in `config.yaml` |
| Netzladen erlaubt | Schalter | die Einstellung „Netzladen erlaubt“ (Abschnitt „Planung“); jede Änderung ist eine neue Version der Einstellungen und löst eine Neuplanung aus |
| Akku-Unterstützung erlaubt | Schalter | erlaubt oder sperrt die Unterstützung der Autos aus dem Akku (Abschnitt „Akku und Spitzen“, „Akku-Unterstützung bedienen“) |
| Unterstützungsschwelle | Zahl | die Schwelle in %, von der USV-Reserve bis 100 (Abschnitt „Akku und Spitzen“, „Akku-Unterstützung bedienen“) |
| Neu planen | Taste | löst eine Neuplanung aus |

Die Bedienelemente stehen am Gerät des Fahrzeugs bzw. der Wallbox, Regelbetrieb, Gast-Lademodus,
Gast-Sofort-Höchststrom, Netzladen erlaubt, Akku-Unterstützung erlaubt, Unterstützungsschwelle und Neu planen am Gerät „BindaEMS“. Live-Schaltung und Vorrang gibt es in Home Assistant nicht; sie bleiben im UI.

- **Befehle** kommen über `bindaems/cmd/<Objekt>`. Eine gehaltene (retained) Nachricht auf diesen
  Topics ignoriert die app: Sie käme sonst bei jeder Verbindung wieder.
- **Höchstens ein Befehl je Sekunde und Entität** wirkt; was dazwischen kommt, wird verworfen. So
  richtet eine Automation mit Dauerfeuer nichts an.
- **Nach jedem Befehl veröffentlicht die app den Zustand der Entität neu**, auch nach einem
  verworfenen oder abgelehnten Befehl. Home Assistant zeigt so immer, was gilt.
- **Ablehnungen:** Ein ungültiger Wert (eine Option, die es nicht gibt, ein Ziel-SOC außerhalb von
  20–100 %, ein Sofort-Höchststrom außerhalb des Strombereichs oder eine Abfahrt, die keine Uhrzeit `HH:MM` ist) oder ein Befehl, den der core ablehnt oder
  der nicht ankommt, ändert nichts. Home Assistant zeigt dann die persistente Meldung
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

### Geplantes Laden

Seit Plan 4b hat jedes Fahrzeug vier Entitäten für den Modus „Geplant“. Die Objekt-IDs stehen hier
für den Schlüssel `tesla` der Beispielkonfiguration; bei einem anderen Fahrzeug steht dessen Schlüssel
vorn. Die Warnung „Ziel-SOC unerreichbar“ gilt für alle Fahrzeuge.

| Objekt-ID | Name | Plattform | Wert |
|---|---|---|---|
| `tesla_target_soc` | Ziel-SOC | number | das Ziel der Absicht in % (20 bis 100, Schritt 1); schreibend |
| `tesla_departure` | Abfahrtszeit | time | die tägliche Abfahrt der Absicht; Zustand `HH:MM:00`, Befehl `HH:MM` oder `HH:MM:SS` (die Sekunden fallen weg); schreibend |
| `tesla_done_by` | Fertig um | sensor | wann das Auto voraussichtlich am Ziel ist (Zeitstempel) |
| `tesla_target_reachable` | Ziel erreichbar | sensor | „ja“ oder „nein“ (Geräteklasse „enum“); Attribute `expected_soc_pct` (Ladestand an der Abfahrt, eine Nachkommastelle), `departure_at` (die nächste Abfahrt, Ortszeit) und `soc_source` (`measured`, `estimated` oder `assumed`) |
| `problem_target_unreachable` | Ziel-SOC unerreichbar | binary_sensor | an, wenn ein Auto im Modus „Geplant“ sein Ziel nicht erreicht; Attribut `vehicles` mit den Namen dieser Autos |

- Ziel-SOC und Abfahrtszeit zeigen die Absicht, auch in den anderen Modi: Sie bleiben stehen, wenn du
  auf „Geplant“ umschaltest. Ohne bekannte Absicht sind sie „unbekannt“.
- **Mit Plan:** Nennt der gültige Plan das Auto, ist „Fertig um“ das Ende der letzten Viertelstunde, in
  der der Plan es lädt, und „Ziel erreichbar“ ist „ja“, wenn die Fehlmenge des Plans höchstens 0,1 kWh
  ist; `expected_soc_pct` ist der Ladestand an der Abfahrt laut Plan. Liegt die Abfahrt hinter dem
  Horizont (etwa am Nachmittag, bevor die Preise für morgen da sind) und lädt der Plan den Bedarf
  nicht schon im Horizont, ist „Fertig um“ „unbekannt“: Den Rest lädt der core erst danach.
- **Ohne Plan** für das Auto (kein gültiger Plan, oder es wurde nach dem Plan angesteckt) rechnet die
  app aus der spätesten Startzeit, die der core in Health meldet: Ab ihr lädt das Auto mit voller
  Leistung an seiner Wallbox, bis der Bedarf geladen ist; ist sie schon erreicht, ab jetzt. Reicht die
  Zeit bis zur Abfahrt nicht, ist das Ziel nicht erreichbar, und `expected_soc_pct` ist der Ladestand,
  den die volle Leistung bis zur Abfahrt bringt. „Fertig um“ ist dabei auf die volle Minute
  aufgerundet, so ändert es sich nicht mit jeder Sekunde.
- **Am Ziel** (oder ohne Bedarf) ist „Fertig um“ „unbekannt“ und „Ziel erreichbar“ „ja“.
- „Fertig um“ und „Ziel erreichbar“ sind „unbekannt“, solange das Fahrzeug nicht in „Geplant“ steht
  oder abgesteckt ist. „Ziel-SOC unerreichbar“ ist dann aus; ohne core und ohne Plan ist sie
  „unbekannt“.
- Ein **angenommener Ladestand** (`soc_source` `assumed`, Abschnitt „Laderegelung“) rechnet mit 0 %:
  „Ziel erreichbar“ gilt dann für ein leeres Auto.

### Benachrichtigungen

Die app meldet Alarme, Warnungen und abgelehnte Befehle in Home Assistant. Dafür braucht sie dort einen Token
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
- Jede **Warnung** (die `binary_sensor` mit der Geräteklasse `problem`, oben), die 30 s ohne
  Unterbrechung besteht, meldet sich ebenso einmal, als persistente Meldung mit der Kennung
  `bindaems_warning_<Objekt-ID>` und dem Titel „BindaEMS“ und über `notify_service`; verschwindet die
  Warnung, entfernt die app die Meldung:

  | Warnung | Kennung | Text |
  |---|---|---|
  | Mitregelndes System | `bindaems_warning_problem_competitor` | „Mitregelndes System erkannt.“ |
  | Preise fehlen | `bindaems_warning_problem_prices` | „Preise fehlen.“ |
  | Gerät offline | `bindaems_warning_problem_device_offline` | „Gerät offline: {Namen}.“ (die Adapter) |
  | Zuordnung unklar | `bindaems_warning_problem_assignment_unclear` | „Zuordnung Auto und Wallbox unklar.“ |
  | Ziel-SOC unerreichbar (je Fahrzeug) | `bindaems_warning_<Fahrzeug>_target` | „Ziel-SOC unerreichbar: {Name} erreicht bis {HH:MM} voraussichtlich nur {soc} %.“ |

  - „Watchdog ausgelöst“ meldet sich nicht zusätzlich: Das tut schon sein Alarm.
  - „Mitregelndes System“ schweigt, solange ein Alarm `competitor.*` der Schwere „Fehler“ besteht
    oder gemeldet ist, auch in den 30 s, bevor der Alarm selbst gemeldet wird; so kommt auf dem Handy
    nur die Meldung des Alarms. Steht ihre Meldung schon, entfernt die app sie, sobald der Alarm
    besteht. Danach zählt sie neu.
  - „Ziel-SOC unerreichbar“ meldet sich je Fahrzeug in „Geplant“ nach derselben Rechnung wie „Ziel
    erreichbar“ (oben): {HH:MM} ist die Abfahrt, {soc} der erwartete Ladestand an der Abfahrt. Bleibt
    das Ziel über viele neue Pläne unerreichbar, bleibt es bei der einen Meldung (mit dem Text vom
    Zeitpunkt der Meldung); wird es erreichbar oder verlässt das Auto „Geplant“, verschwindet sie,
    und wird es danach wieder unerreichbar, kommt eine neue. (Ohne erwarteten Ladestand meldet sie
    sich nicht. Das ist nur ein Schutz gegen ein unvollständiges Health: Der core nennt in „Geplant“
    immer einen Ladestand, notfalls angenommen 0 %.)
  - Ist eine Warnung „unbekannt“ (etwa ohne core), zählt sie nicht weiter, eine stehende Meldung
    bleibt aber stehen.
- **Abgelehnte Befehle** aus Home Assistant erscheinen als persistente Meldung `bindaems_ha_command`
  (oben).
- Höchstens **10 Meldungen je Minute** über alle Alarme, Warnungen und Ablehnungen zusammen, davon
  höchstens **4 für Ablehnungen**: Den Alarmen und Warnungen bleiben so immer mindestens 6, auch wenn
  eine Automation eine Flut ungültiger Befehle sendet. Eine Ablehnung über dem Budget steht nur im Log
  der app; eine Warnung über dem Budget folgt wie ein Alarm, sobald es frei ist.
- Ein fehlgeschlagener Aufruf stört nichts; er steht im Log (`Dienstaufruf an Home Assistant
  fehlgeschlagen`). Ein Alarm gilt erst als gemeldet, wenn die persistente Meldung angelegt ist: Über
  dem Budget oder während Home Assistant neu startet versucht es die app erneut, solange der Alarm
  (oder die Warnung) besteht, je Alarm höchstens einmal je Minute. Der `notify`-Dienst folgt erst auf
  die angelegte Meldung, einmal. Auch das Entfernen wiederholt die app so, bis es gelingt.
- **Ohne `BINDAEMS_HA_TOKEN`** (oder ohne den Abschnitt `homeassistant`) sendet die app nichts. Die
  Komponente `ha-notify` unter **System** zeigt dann „Benachrichtigungen aus: BINDAEMS_HA_TOKEN
  fehlt.“.

## 8. Tarif und Abrechnung

Nach der Inbetriebnahme trägst du in den Laufzeit-Einstellungen nach, was die Spec bewusst
offen lässt: Netznutzungsentgelt (Arbeitspreis), Netzverlustentgelt, Elektrizitätsabgabe,
Erneuerbaren-Förderbeitrag (alle netto in ct/kWh), den Leistungspreis, die monatlichen Gebühren
und jeden Monat den OeMAG-Marktpreis. Bis dahin zählen fehlende Werte als 0, und die Übersicht
und die Seite „System“ zeigen Warnungen. Jede Änderung ist eine neue Version im
Änderungsprotokoll.

Der Tarif besteht aus Bestandteilen. Ihre **Art** bestimmt, was sie tun:

| Art im Editor | gilt für | Bedeutung |
|---|---|---|
| Energie (ct/kWh) | Bezug | Teil des Bezugspreises je Viertelstunde: Börsenpreis, Lieferantenaufschlag, Netznutzung, Abgaben. Wert netto in ct/kWh, optional mit Zeitfenstern |
| Leistungspreis (€/kW/Jahr) | Monatsspitze | Preis der höchsten Viertelstunde des Monats (unten) |
| Monatliche Gebühr (€/Monat) | Kosten | Grundgebühren; nur für Statistik und Kostenanzeige, nie im Bezugspreis |
| Einspeisung (ct/kWh) | Einspeisung | Zu- oder Abschlag auf den OeMAG-Monatswert, netto, darf negativ sein (etwa ein Entgelt). Standardmäßig ohne USt. |

Aus den Bestandteilen rechnet die app:

- **Bezugspreis je Viertelstunde:** die Summe der Energiebestandteile, die an dem Tag und in dem
  Zeitfenster gelten, mit USt. bei den Bestandteilen, die „mit USt.“ haben. Fehlt ein Wert,
  zählt er als 0 und die Übersicht warnt.
- **Einspeisepreis je Viertelstunde:** der OeMAG-Monatswert plus die gültigen
  Einspeisebestandteile, mit USt., wo „mit USt.“ gesetzt ist. Gilt für einen Monat noch kein
  eigener Wert, nimmt die app den jüngsten eingetragenen Monat davor. Ohne einen OeMAG-Wert ist der
  Einspeisepreis unbekannt.
- **Leistungspreis (ElWG):** Nach dem ElWG zahlst du ab 1. 1. 2027 für die höchste
  15-min-Leistung des Monats. Der Bestandteil „Leistungspreis (ElWG)“ ist vorbereitet: Stufe
  10 kW, Mindestverrechnung 2 kW, gültig ab 1. 1. 2027, aber **ohne Sätze**. Die Sätze in
  €/kW/Jahr netto (bis und über der Stufe; leer = über der Stufe gilt der Satz bis zur Stufe)
  trägst du ein, sobald die SNE-T-V sie nennt. Verrechnet wird
  `max(Monatsspitze, Mindestverrechnung)`; die Kosten eines Monats sind
  `(Satz bis zur Stufe · min(kW, Stufe) + Satz über der Stufe · max(0, kW − Stufe)) / 12`, mit
  USt. Es gilt der Leistungspreis, der am Tag gültig ist; zwei Leistungspreise dürfen sich zeitlich
  nicht überschneiden („Leistungspreise dürfen sich zeitlich nicht überschneiden“). Vor dem
  Gültigkeitsdatum gibt es keinen Leistungspreis, ohne Satz zählt er 0 mit Warnung.
- **Gebühren eines Monats:** die Summe der am Tag gültigen monatlichen Gebühren, brutto.
- **Monatsspitze:** die höchste Viertelstunde des Netzbezugs im Monat aus der Abrechnung
  (Netzbezug der Viertelstunde in Wh · 4 / 1000 = kW), mit ihrem Beginn. Boost zählt mit, denn der
  Zähler sieht ihn. Home Assistant bekommt sie als Sensor „Monatsspitze“ (Abschnitt 7). Es gibt
  dafür keine eigene Tabelle; die Abfrage über die Abrechnung läuft jedes Mal und kann nicht
  auseinanderlaufen. Seit Plan 3b fließt der Leistungspreis in die Planung ein: in das Ziel des
  Optimierers, in die erwarteten Kosten des Plans und in sein Budget (Abschnitt „Planung“). Seit
  Plan 3c hält der 15-min-Wächter das Budget des Plans (Abschnitt „15-min-Wächter“), und die Seite
  „Laden“ zeigt, was ein Boost höchstens an Leistungspreis kostet (Abschnitt „Laderegelung“).

Die **Warnungen** auf der Übersicht und der Seite „System“ nennen, was fehlt:

- `Tarifbestandteil „<Name>“ hat noch keinen Wert.` (für jede Art; beim Leistungspreis, wenn der
  Satz bis zur Stufe fehlt)
- `Kein Leistungspreis eingetragen; nach ElWG gilt er ab 1. 1. 2027.` (kein Bestandteil der Art
  Leistungspreis)
- `Noch kein OeMAG-Monatswert eingetragen.`

Einstellungen, die vor Plan 3a gespeichert wurden, haben nur Energiebestandteile. Sie laden
unverändert; die Warnung nennt den fehlenden Leistungspreis, und im Editor legst du ihn mit
**Bestandteil hinzufügen** (Art „Leistungspreis“) an. Ein YAML-Export aus der Zeit davor lässt sich
ebenso importieren.

Im UI (Admin) unter **Einstellungen**:

- **Bearbeiten**: öffnet den Editor für den ganzen Tarif. Je Bestandteil (Börsenpreis,
  Aufschlag, Netzentgelte, Abgaben, Leistungspreis, Gebühren, Einspeisung) gibt es eine Gruppe
  mit dem Namen, dem Wert netto (Komma oder Punkt, leer = noch kein Wert), „mit USt.“ und der
  Gültigkeit („gültig ab“ und „gültig bis“, leer = ohne Grenze). Der Börsenpreis hat keinen
  Wert, er „kommt aus der Preisquelle“. Der Leistungspreis hat zusätzlich die Preise bis zur
  und über der Stufe, die Stufe (kW) und die Mindestverrechnung (kW).
  - **Zeitfenster** (bei Energie und Einspeisung, z. B. der SNAP-Sommerfaktor): mit
    **Zeitfenster hinzufügen** legst du eines an, mit **Zeitfenster entfernen** löschst du es.
    Du wählst Monate und Wochentage mit den Umschaltern, „von“ und „bis“ in Viertelstunden
    („24:00“ ist das Tagesende) und entweder einen Faktor auf den Wert des Bestandteils oder
    einen festen Wert in ct/kWh netto.
  - **Hinzufügen und entfernen**: Unter „Art“ wählst du Energie, Leistungspreis, Monatliche
    Gebühr oder Einspeisung und legst mit **Bestandteil hinzufügen** einen neuen an; er trägt
    zunächst einen Namen nach der Art, den du änderst. Seine interne ID entsteht beim Anlegen
    aus dem Namen und bleibt danach fest, auch wenn du ihn umbenennst. **Bestandteil entfernen**
    löscht ihn; der Börsenpreis lässt sich nicht entfernen.
  - Dazu die **USt. (%)** und der **Fixtarif zum Vergleich** (ct/kWh brutto), außerdem die
    OeMAG-Monatswerte (Monat und ct/kWh, Zeilen hinzufügen oder entfernen), Brutto/Netto der
    Börsenpreise, die Referenzquelle, die Namen der Ladestationen und ein Kommentar.

  **Speichern** prüft die Eingaben und legt eine neue Version an. Fehler stehen am Feld, am
  Zeitfenster oder am Bestandteil (z. B. „Ende muss nach dem Beginn liegen“ oder dass sich
  zwei Leistungspreise zeitlich überschneiden). Hat inzwischen jemand anderes gespeichert,
  bleiben deine Eingaben stehen; „Neu laden (Eingaben verwerfen)“ holt den neuen Stand.
- **Abrechnung neu bewerten**: Die Abrechnung speichert je Viertelstunde den Bezugspreis zum
  Slotzeitpunkt. Nach einer Änderung der Tarifwerte erscheint das Formular mit dem Zeitraum
  vom Monatsbeginn bis heute; **Neu bewerten** rechnet die gewählten Tage mit dem aktuellen
  Tarif neu.
- **Export und Import**: „Exportieren (YAML)“ lädt die gültigen Einstellungen herunter;
  „YAML-Datei importieren“ übernimmt eine geprüfte Datei als neue Version. Fehler nennen die
  betroffenen Felder.
- **Versionen** listet alle Stände mit Zeit, Person, Quelle und Kommentar.

Der Einspeiseerlös wird immer erst beim Lesen berechnet, je Viertelstunde mit deren
Einspeisepreis (OeMAG-Wert plus die in dieser Viertelstunde gültigen Einspeisebestandteile);
dafür ist keine Neubewertung nötig.

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

### Einstandspreis des Akkus

Die app führt für den Akku einen gleitenden Mittelwert, den **Einstandspreis**: Was kostet eine kWh,
die jetzt im Akku steckt? Er ist keine eigene Anzeige, sondern die Grundlage für „Akku → Auto“ und die
Kosten je Auto (nächster Unterabschnitt). Er entsteht aus der Abrechnung, Viertelstunde für
Viertelstunde in zeitlicher Reihenfolge, und kann nicht auseinanderlaufen: Ändert sich ein Slot oder
ein Preis, rechnet die app ab dem frühesten geänderten Slot neu. Das gilt auch über einen Neustart
oder Absturz hinweg: Beim Start sucht die app den frühesten Slot, dessen Rechnung nicht mehr zu ihm,
zum Verschleiß oder zu `battery.usable_kwh` passt, und rechnet ab dort neu. Scheitert die Rechnung
(Log „Einstandspreis des Akkus nicht berechenbar“), versucht die app es nach einer Minute wieder.

- **PV-Ladung kostet 0 ct,** Netzladung den Bezugspreis der Viertelstunde (Entscheidung des Betreibers
  vom 17.10.2026, anders als Spec 11.4: PV-Strom zählt nicht mit dem OeMAG-Wert). Der Mittelwert ist
  damit der Netzanteil im Akku zum Bezugspreis. Der entgangene Einspeiseerlös erscheint nirgends in
  den Kosten je Auto; die Tagesbilanz (Einspeiseerlös, Ersparnis) bleibt davon unberührt.
- **Inhalt:** Der gerechnete Inhalt ist auf `battery.usable_kwh` begrenzt, damit Wandlungsverluste nicht
  endlos Energie ansammeln.
- **Entladung:** Sie kostet den Mittelwert zu Beginn der Viertelstunde plus den Verschleiß
  (`planning.wear_ct`, Standard 2 ct/kWh, Einstellungen „Planung“). Bei leerem Inhalt gilt nur der
  Verschleiß. Ändert sich `planning.wear_ct`, rechnet die app alles neu.
- **Fehlt der Bezugspreis** einer Netzladung, führt die app diese Energie als Anteil ohne Preis im
  Inhalt mit: Eine Entladung und das Begrenzen auf `battery.usable_kwh` nehmen ihn anteilig heraus,
  neue PV- oder bepreiste Ladung verdünnt ihn. Der Einstandspreis ist unbekannt, bis der Preis da ist
  oder der Anteil ohne Preis unter 1 % des Akkubestands fällt (Akkubestand: der gerechnete Inhalt am
  Ende der Viertelstunde davor, nicht der SOC); darunter gilt der Mittelwert der bepreisten Energie.
  Akku → Auto, die Kosten der Session und die Kosten je Auto sind dann unbekannt, wie die
  Tageskosten bei fehlendem Preis: „Statistik“ zeigt dort „–“, „Laden“ lässt die Kosten der letzten
  Session weg, und API und Home Assistant liefern `cost_eur` als `null`. Ein nachgetragener Preis
  (Abrechnung neu bewerten, siehe oben) macht ihn ab dort neu gerechnet wieder bekannt.
- **Beim ersten Start mit Plan 4a** rechnet die app einmal über alle gespeicherten Viertelstunden;
  das kann bei viel Abrechnung etwas dauern. Beendest du die app währenddessen, hört die Rechnung
  nach dem laufenden Abschnitt von 7 Tagen auf (das Beenden wartet darauf höchstens 5 s), und der
  nächste Start rechnet ab dort weiter.

### Ladesessions und Kosten je Auto

`GET /api/charging/sessions?from=JJJJ-MM-TT&to=JJJJ-MM-TT[&vehicle=<Schlüssel>]` (ab „Lesen“) liefert die
Ladesessions, die die Tage (Wiener Zeit) überdecken, neueste zuerst: höchstens 400 Tage (sonst 422) und
höchstens 2000 Einträge. `vehicle` ist der Schlüssel aus `config.yaml`, `guest` („Gast“) oder `unknown`
(„Unbekannt“, kein Fahrzeug erkannt). Je Eintrag: `id`, `wallbox` und `wallbox_name`, `vehicle` und
`vehicle_name`, `start`, `end` (`null` und `open: true`, solange die Session läuft), die Energie
`kwh` mit den Anteilen `pv_kwh`, `battery_kwh`, `grid_kwh` und `cost_eur`.

- **Wie eine Session entsteht:** Eine Session ist die Zeit, in der an einer Wallbox ein Auto steckt. Den
  Steckzustand liest die app aus dem State des core (EVCS: `status` ungleich 0, Wall Connector:
  `vehicle_connected`), auch bei Regelbetrieb aus. Welches Auto es ist, kennt sie aus der Belegung in
  `GET /v1/health` (`wallboxes[].occupant`, alle 10 s): Es ist das Auto mit der längsten Zeit in der
  Session, ein Gast ist „Gast“, ohne Beobachtung „Unbekannt“. Hat während der Session kein Auto Zeit
  (z. B. bei einer sehr kurzen Session, deren Insasse erst in der schließenden Beobachtung steht),
  gilt die letzte nicht leere Belegung zwischen Beginn und Ende. Ohne Regelbetrieb ruht die Regelung,
  und `wallboxes` zeigt niemanden: Dann heißen die Sessions „Unbekannt“. Eine Pause der Beobachtung
  (core oder app aus) trennt eine Session nicht, wenn sie höchstens 30 Minuten dauert und das Auto
  noch steckt; sonst endet die Session bei der letzten Beobachtung.
- **Zuordnung:** Jede Viertelstunde gehört an einer Wallbox zu der Session, die sie am längsten überdeckt
  (bei Gleichstand der späteren). Energie ohne Session zählt als „Unbekannt“. Eine laufende Session
  endet für die Zuordnung bei „jetzt“.
- **Kosten:** PV-Strom ins Auto kostet 0 ct, Akku-Strom den Einstandspreis des Akkus plus Verschleiß,
  Netzstrom den Bezugspreis der Viertelstunde. Fehlt der Bezugspreis bei einem Netzanteil oder ist der
  Einstandspreis bei einem Akkuanteil unbekannt (siehe oben), ist `cost_eur` `null` (unbekannt), nie 0;
  nach dem Nachtragen des Preises stimmt der Wert wieder.
  Der entgangene Einspeiseerlös gehört nicht dazu.

```bash
curl -b jar "$APP/api/charging/sessions?from=2026-10-01&to=2026-10-31&vehicle=guest"
```

### Lücken nachrechnen

Fehlen Viertelstunden in der Abrechnung (die app war aus, die Verbindung zum core riss ab, InfluxDB
fehlte beim Nachtragen), rechnet die app sie aus den Zählerständen nach. Das geschieht jede Nacht
um 02:45 Uhr (Wiener Zeit) für die letzten 7 Tage und beim Start, jeweils vor dem Abgleich mit den
Zählern, und beim Start erst nach dem ersten Nachtrag der Abrechnung (höchstens 10 Minuten lang).

- **Rechnung:** Je zusammenhängender Lücke liest die app Netzbezug, Einspeisung, PV und die
  Wallboxen aus den Rohdaten von InfluxDB (`energy` in `raw`) am Rand davor (letzter Wert in den
  15 Minuten bis zum Beginn) und am Rand danach (erster Wert in den 15 Minuten ab dem Ende), dazu
  den Ladezustand des Akkus. Die Energie verteilt sich gleichmäßig auf die Viertelstunden der
  Lücke. Der Akku folgt aus der Änderung des Ladezustands mal der nutzbaren Kapazität (ohne
  Ladezustand ändert er sich nicht), das Haus ist der Rest der Bilanz; ein negativer Rest gilt als
  PV ohne Zähler (DC-gekoppelt). PV und Akku decken zuerst das Haus, dann die Wallboxen in der
  Reihenfolge der Konfiguration, der Rest geht ins Netz. Fehlende PV- oder Wallbox-Zähler zählen 0.
- **Kennzeichnung:** Geschätzte Viertelstunden tragen die Herkunft `estimate` und die Abdeckung
  0 s; die Tagesbilanz (`estimated_slots`) zeigt, wie viele es sind. Der Einstandspreis des Akkus
  und der Abgleich rechnen mit ihnen.
- **Echte Daten zuerst:** Eine Schätzung ersetzt nie eine vorhandene Viertelstunde. Kommt später
  eine echte (z. B. aus dem Nachtrag von InfluxDB), ersetzt sie die Schätzung. Ein zweiter Lauf
  ändert nichts.
- **Wann eine Lücke leer bleibt:** Fehlt der Zählerstand von Netzbezug oder Einspeisung an einem
  der beiden Ränder oder ist ein Zähler zurückgesetzt, bleibt die Lücke leer (der Grund steht im
  Log, Ereignis „Lücke bleibt leer“). Ein Ausfall von InfluxDB ergibt eine Warnung; die nächste
  Nacht versucht es erneut.
- **Nur die letzten 7 Tage:** Die Nachrechnung sieht immer nur das Fenster der letzten 7 Tage bis
  gestern. Dauerte ein Ausfall länger, bleibt der Teil davor leer, und der Teil im Fenster wird
  geschätzt, wenn an beiden Rändern Zählerstände da sind. Eine einzelne Lücke über 7 Tage rechnet die
  app nicht (Warnung „Lücke zu lang für eine Nachrechnung“).
- **Nur, was der Nachtrag schon gelesen hat:** Die app schätzt nur in dem Zeitraum, den der Nachtrag
  der Abrechnung lückenlos aus InfluxDB gelesen hat, damit keine Schätzung entsteht, wo echte Daten
  liegen. Der erste Nachtrag nach dem Start liest deshalb ab Mitternacht vor 7 Tagen, danach stündlich
  die letzten 6 Stunden. Sind stündliche Nachträge gescheitert, beginnt der nächste dort, wo der
  gelesene Zeitraum endet (höchstens Mitternacht vor 7 Tagen), so bleibt keine Stunde dazwischen
  ungelesen. Gelang noch kein Nachtrag (InfluxDB nicht erreichbar), schätzt die app in dieser Nacht
  nichts (Log „Nachrechnung der Lücken wartet auf den ersten Nachtrag der Abrechnung“).

### Abgleich mit den Zählern

Jede Nacht um 02:45 Uhr (Wiener Zeit) vergleicht die app die Abrechnung der letzten 7 vollständigen
Tage mit den Zählerständen aus InfluxDB, beim Start die der letzten 35. Je Tag und Größe steht die
Summe der Abrechnung (Schätzungen eingeschlossen) neben der Differenz der Zählerstände um
Mitternacht (`energy` in `raw`, dort trägt jeder Wert seinen Lesezeitpunkt; `raw` hält 90 Tage).
Der core schreibt die Zähler nur jede Minute; den Stand um Mitternacht rechnet die app deshalb
zwischen dem letzten Wert in der halben Stunde davor und dem ersten in der halben Stunde danach
(linear). Fehlt einer der beiden, gilt der andere, wenn er höchstens 2 Minuten von Mitternacht
entfernt ist, sonst fehlt der Zählerstand. Alle Mitternächte eines Laufs gehen in einer Anfrage an
InfluxDB; die app schickt Abfragen per POST, damit auch ein Proxy mit kurzer URL-Grenze sie
durchlässt. Per POST sperrt InfluxDB schreibende Anweisungen nicht, darum schickt die app nur
Anweisungen, die mit `SELECT` oder `SHOW` beginnen und kein `INTO` enthalten. Die Größen sind
`import` (Netzbezug), `export` (Einspeisung), `pv` und `wb:<Schlüssel>` je Wallbox.

`GET /api/ledger/checks?from=JJJJ-MM-TT&to=JJJJ-MM-TT` (ab „Lesen“) liefert die gespeicherten
Prüfungen, höchstens 400 Tage (sonst 422): `days` mit `day`, `signal`, `ledger_kwh`, `counter_kwh`,
`deviation_pct`, `flagged` und `note` je Tag und Größe, dazu `totals` mit den Summen je Größe
über den Zeitraum.

- **Markierung:** `flagged` ist wahr bei mehr als 1 % und mehr als 0,1 kWh Abweichung. Genau 1 % oder
  genau 0,1 kWh markiert die app nicht (sie rundet vor dem Vergleich auf 6 Nachkommastellen). `pv`
  wird gezeigt, aber nie markiert, denn DC-gekoppelte PV hat keinen Zähler.
- **Gründe (`note`):** „Zählerstand fehlt“ (kein Wert vor oder nach Mitternacht), „Zähler
  zurückgesetzt“ (der Zähler sinkt, z. B. nach einem Tausch) und „Zähler zeigt 0 kWh“. Dann gibt es
  keine Markierung; ohne Zählerwert sind `counter_kwh` und `deviation_pct` `null`, bei 0 kWh nur
  `deviation_pct`.
- **Summen:** `totals` zählt nur Tage mit Zählerwert; ohne einen solchen ist `counter_kwh` `null`.
- **Fehler:** Ist InfluxDB nicht erreichbar, steht eine Warnung im Log und die alten Zeilen bleiben;
  die nächste Nacht versucht es erneut.

```bash
curl -b jar "$APP/api/ledger/checks?from=2026-10-01&to=2026-10-07"
```

### Statistik je Zeitraum

`GET /api/stats?period=day|week|month|year[&date=JJJJ-MM-TT]` (ab „Lesen“) liefert die Statistik des
Zeitraums, der `date` (Wiener Zeit, Standard: heute) enthält; ein anderes `period` gibt 422. Die Woche
läuft von Montag bis Sonntag (ISO). Die Antwort enthält `period`, `first`, `last`, `complete`,
`coverage`, `estimated_slots`, `energy`, `money`, `ratios`, `buckets`, `vehicles`,
`battery_to_vehicles_kwh`, `vehicle_buckets`, `planned`, `consumers`, `month_peaks` und `checks`; Zahlen
sind auf 3 Stellen gerundet.

- **Kennzahlen:** Sie entstehen aus den Summen der Tage, nicht als Mittel der Tageskennzahlen:
  `autarky` ist `1 − Bezug ÷ Verbrauch`, `self_consumption` ist `(PV − Einspeisung) ÷ PV`, die
  Ersparnis (`savings_no_plant_eur`, `savings_same_import_eur`) folgt den Formeln der Tagesbilanz und
  bleibt ohne Grundgebühren und Leistungspreis. Ein Verhältnis ist `null`, wenn sein Nenner 0 ist
  (z. B. in der Zukunft); ein laufender Zeitraum zählt die Tage, die noch nicht begonnen haben,
  als 0, die Verhältnisse gelten dann „bis jetzt“. Ein Geldbetrag ist `null`, sobald ein Tag ihn
  nicht kennt.
- **Buckets:** `buckets` zeigt bei „Tag“ eine Stunde je Eintrag (23, 24 oder 25 Einträge an Tagen der
  Zeitumstellung, `start` mit Zone), bei „Woche“ und „Monat“ einen Tag je Eintrag, bei „Jahr“ einen
  Monat (`JJJJ-MM`).
- **Kosten:** `net_eur` ist Bezug minus Einspeiseerlös. `fees_eur` verteilt die Grundgebühren anteilig
  je Tag (Monatsgebühr ÷ Tage des Monats) bis einschließlich heute. `power_eur` (und
  `month_peaks[].power_eur`) gibt es nur bei „Monat“ und „Jahr“ und nur für Monate mit Abrechnungsdaten,
  in denen ein Leistungspreis mit Satz gilt, aus der Monatsspitze (`month_peaks`); sonst `null`.
  `total_eur` ist `net_eur + fees_eur + power_eur` und `null`, wenn `net_eur` unbekannt ist.
- **Autos:** `vehicles` nennt die Autos mit Energie über 0 in der Reihenfolge der `config.yaml`, dann
  `guest` („Gast“) und `unknown` („Unbekannt“); `sessions` zählt die Ladesessions, die den Zeitraum
  überdecken. `battery_to_vehicles_kwh` ist der Akku-Strom, der ins Auto ging.
- **Verlauf je Auto:** `vehicle_buckets` hat bei „Tag“ keine Einträge; bei „Woche“, „Monat“ und „Jahr“
  genau einen Eintrag je Eintrag von `buckets`, mit demselben `start` (Datum `JJJJ-MM-TT` bzw. Monat
  `JJJJ-MM`) in derselben Reihenfolge. `vehicles` darin (`key`, `kwh`, `pv_kwh`, `battery_kwh`,
  `grid_kwh`, `cost_eur`) folgt der Reihenfolge von `vehicles` und nennt nur Autos mit Energie über 0;
  ein Bucket ohne Ladung hat `vehicles: []`. `cost_eur` ist `null`, wenn in diesem Bucket ein Preis
  fehlt.
- **Geplantes Laden (Abnahme „Ziel-SOC“):** `planned` zählt die geplanten Ladungen mit Abfahrt im
  Zeitraum: `reached` von `feasible` machbaren erreicht, `missed` machbar und verfehlt, `infeasible`
  nicht machbar, `early` vor der Abfahrt abgesteckt, `unknown` ohne Ladestand, `changed` vor der
  Abfahrt geändert oder verlassen; offene zählen nicht. Die Abnahme verlangt mindestens 95 % der
  machbaren Ladungen erreicht; `infeasible`, `early`, `unknown` und `changed` zählen nicht in diese
  Quote, stehen aber daneben. Die app legt dafür die Tabelle `planned_charge` an (Migration `0012`,
  sie läuft beim Start); sie löscht keine Zeile. Die Regeln:
  - **Beginn:** Eine Zeile entsteht je Session in „Geplant“ und Abfahrt, sobald das angesteckte Auto
    für diese Abfahrt einen Bedarf hat (im Health ist `planned.state` nicht `reached` und
    `need_kwh` über 0). Ein Auto, das über dem Ziel angesteckt wird oder über die Abfahrt hinaus am
    Ziel steckt, zählt nirgends. Ist der Ladestand zu Beginn angenommen, ersetzt ihn der erste
    bekannte; Beginn und Machbarkeit gelten dann ab diesem Zeitpunkt.
  - **Machbar** heißt: Zu Beginn reichte volle Leistung bis zur Abfahrt, und das Ladelimit im Auto
    liegt nicht unter dem Ziel (ohne `charge_limit_sync`). Sinkt das Limit später unter das Ziel,
    bevor es erreicht ist, wird die Ladung nicht machbar; eine erreichte bleibt machbar.
  - **Erreicht** heißt: Der Ladestand des letzten Health vor der Abfahrt war mindestens Ziel − 1
    Prozentpunkt, oder ein bekannter Ladestand war schon vorher am Ziel. Ein angenommener Ladestand
    zählt nie als erreicht. Ist der letzte Ladestand vor der Abfahrt angenommen oder älter als
    15 min, ist das Ergebnis `unknown`.
  - **Geändert** (`changed`): Abfahrt oder Ziel ändern sich vor der Abfahrt (für die neue kann eine
    neue Zeile beginnen), oder das Auto wechselt auf PV, Min+PV oder Aus. Sofort und Boost lassen
    die Ladung offen; sie wird an der Abfahrt beurteilt. Ein unbekannter Modus (etwa während der core
    neu startet) lässt sie ebenfalls offen.
  - **Lücke ohne Health:** Schließt die app eine Session nach mehr als 30 min ohne Health (oder mit
    unbekanntem Steckzustand) und beginnt für dasselbe Auto an derselben Wallbox eine neue, läuft die
    geplante Ladung darin weiter (Beginn und Machbarkeit bleiben). Lag die Abfahrt in der Lücke, ist
    sie `unknown`. Beginnt keine neue Session, bleibt die Ladung offen, bis eine beginnt (dann läuft
    sie darin weiter) oder die Abfahrt vorbei ist (dann `unknown`): Ob das Auto in der Lücke
    abgesteckt wurde, weiß die app nicht, darum gilt das nie als `early`. War das Ziel vor der Lücke
    erreicht, bleibt die Ladung `reached`; läuft sie in einer neuen Session weiter, bleibt es eine
    Ladung, auch wenn danach wieder Bedarf da ist.
- **Verbraucher:** `consumers` summiert die gespeicherten Tage bis gestern und für heute den
  laufenden Wert; fehlt ein Tag (älter als 31 Tage oder noch nicht gespeichert), ist `kwh` `null`
  und `note` nennt den Grund.

```bash
curl -b jar "$APP/api/stats?period=month&date=2026-10-09"
```

### Statistik ausrollen (Plan 4a)

Plan 4a ändert nur die app. Das Image des core bleibt gleich: Der core läuft weiter, der Regelbetrieb
bleibt an, und die Aktoren behalten ihre Betriebsart. Es ist keine neue Konfiguration und keine neue
`config.ini` nötig; die HA-Sensoren der Statistik sind `sensor`-Entitäten und brauchen keine neue
ACL. Die Statistik liest nur und schreibt nichts auf Geräte. Sobald Plan 4a auf `main` ist und das
Image der app veröffentlicht ist, ziehst du nur die app und startest nur sie, ohne ihre Abhängigkeit
`ems-core`:

```bash
cd /opt/bindaems
docker compose pull ems-app && docker compose up -d --no-deps ems-app
```

`--no-deps` hält Compose davon ab, den core mit anzufassen. Als Alternative baust du die app lokal: im
`deploy/`-Ordner eines Checkouts (mit `.env` und `config.yaml` dort; der Build-Kontext ist das
Repository, nicht `/opt/bindaems`) mit `docker compose up -d --build --no-deps ems-app`.

1. **Vorher sichern:** Die Sicherung der Nacht reicht als Rückfall nur bis zum Ausrollen, denn schon die
   erste Sicherung danach enthält die Migration `0010`. Kopiere deshalb vor dem Ausrollen die Datenbank
   unter einem Namen, den die Rotation nicht löscht (Abschnitt 9 beschreibt das Volume `backup`):
   `docker compose stop ems-app`, dann
   `docker compose run --rm --no-deps --entrypoint sh ems-app -c 'cp /data/bindaems.sqlite3 /backup/vor-plan-4a.sqlite3'`.
   Der Rollback steht in Abschnitt 4, „Rollback
   auf einen Stand vor Plan 4a“.
2. **Ausrollen:** Der Befehl oben. Währenddessen fehlt das UI kurz; der core regelt weiter, und die
   app holt die Abrechnung der letzten 7 Tage danach aus InfluxDB nach.
3. **Migration:** Die Migrationen `0010` und `0011` laufen beim Start der app von selbst. `0010` legt
   die Tabellen `assignment_log`, `charging_session`, `ledger_cost`, `ledger_check` und
   `consumer_day` an und ändert keine vorhandene; `0011` passt `ledger_cost` an.
4. **Erste Werte prüfen:** Öffne die Seite **Statistik** (Abschnitt 12) und stelle „Monat“ ein.
   - **Abgleich:** Beim Start gleicht die app die letzten 35 Tage mit den Zählern ab und wartet dafür
     auf den ersten Nachtrag der Abrechnung (höchstens 10 Minuten). Die Karte „Abgleich mit den
     Zählern“ füllt sich danach; eine Weile steht dort „Noch kein Tag abgeglichen.“ Markierte Tage
     suchst du nach ihrer Ursache (Lücke, Zählertausch, fehlender Zählerstand), siehe „Abgleich mit
     den Zählern“ oben.
   - **Ladesessions:** Sie beginnen erst mit dem Ausrollen; für die Zeit davor kennt die app nicht, wer
     an welcher Wallbox stand. Die Ladeenergie davor erscheint in „Autos“ als „Unbekannt“. Steckt jetzt
     ein Auto, zeigt die Karte „Ladesessions“ eine Session mit „läuft“ und die Seite **Laden** die Zeile
     „Session seit …“.
   - **Einstandspreis:** Er entsteht beim ersten Start aus der gesamten gespeicherten Abrechnung; die
     Kosten je Auto sind erst danach da.
   - **Verbraucher:** Die Energie je Verbraucher der letzten 31 Tage holt die app beim Start aus
     InfluxDB (eine Abfrage je Tag); ältere Tage bleiben leer.
5. **Tarif nachtragen:** OeMAG-Werte und monatliche Gebühren unter **Einstellungen** → „Bearbeiten“
   eintragen, soweit sie fehlen (Abschnitt 8, Anfang). Ohne OeMAG-Wert sind Einspeiseerlös, Kosten und
   Ersparnis „–“ (unbekannt), ohne Grundgebühr zählt die Kostenzeile „Grundgebühren“ 0 €. Danach die
   Abrechnung der betroffenen Tage neu bewerten; Einstandspreis und Kosten je Auto ziehen von selbst
   nach.
6. **Grafana-Dashboard importieren (optional):** Abschnitt 3, „Grafana“.

Die Abnahme dieses Teils der Phase 4 steht in `docs/verification/abnahme-phase-4.md`: 30 Tage ab dem
Ausrollen.

## 9. Sicherung und Wiederherstellung

Die app sichert ihre Datenbank jede Nacht um 04:00 Uhr im laufenden Betrieb in das Volume
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
Preise neu von den Quellen, Viertelstunden der letzten 7 Tage aus InfluxDB. Die Statistik holt
die app ebenfalls nach, soweit sie sich rechnen lässt: Einstandspreis, Abgleich (35 Tage) und
Energie je Verbraucher (31 Tage). Ladesessions und Belegung seit der Sicherung gehen verloren, denn
sie kommen nur aus dem Betrieb der app. Die Modelle des Lastmodells liegen als Dateien unter
`/data/models/load/` und brauchen keine Sicherung, weil die app jede Nacht neu trainiert
(Abschnitt „Lastmodell (Phase 5a)“).

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
| `thermal` | 17.2-9 | über CoE einen Sollwertversatz von +0,5 K und 0 an die UVR der Klimaanlage und den Timeout der UVR; `--geraet <Name>` wählt die Klimaanlage. Der core muss gestoppt sein (derselbe UDP-Port), Secrets braucht der Punkt nicht (Abschnitt „Klimaanlage (Phase 6a)“) |

Die Schritte je Punkt und das Format des Berichts stehen in `docs/verification/README.md`.
Die Reihenfolge: **EVCS und Tesla jetzt**, gemeinsam mit dem Betreiber, **Victron nur mit
abgeschaltetem Dynamic ESS** (für den Test genügt es, DESS vorübergehend abzuschalten).
Der Punkt `thermal` (Plan 6a) läuft nur mit **gestopptem core** und mit `--service-ports` (derselbe
UDP-Port); sein Aufruf steht im Abschnitt „Klimaanlage (Phase 6a)“, „Prüfprotokoll Teil 2, Punkt
thermal“.

Der Aufruf gilt je Punkt und folgt dem der Probe:

```bash
cd /opt/bindaems
mkdir -p verification && sudo chown 10001 verification   # der Container läuft als UID 10001
docker compose run --rm -v "$PWD/verification:/out" --entrypoint python ems-core \
  -m bindaems.tools.verify_part2 --config /config/config.yaml --punkt evcs \
  --out /out/<Datum>-pruefprotokoll-teil2-evcs.md
```

- Für die anderen Punkte setzt du `--punkt tesla` bzw. `--punkt victron` und den Namen im Dateinamen
  ein (`thermal` siehe oben). `--wallbox <Name>` wählt bei `evcs` die EVCS und `--fahrzeug <Name>`
  bei `tesla` das Fahrzeug, wenn es mehrere gibt (Standard: das erste).
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
  und aus und ändert Lademodus, Boost, Zuordnung, Gast-Modus und Vorrang und schaltet „Netzladen
  erlaubt“ und „Neu planen“ im Zeitplan; die Bedienelemente dafür
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
| Übersicht | von oben nach unten: die Hinweis-Leiste (ohne Hinweise unsichtbar; eine Zeile, eine lange Vorschau endet mit „…“; „Anzeigen“ klappt die Liste auf, dann entfällt die Vorschau), die Kachelreihe mit dem Hausakku (doppelt breit: SOC, kWh, Reserve, Netzladen, Planausblick „Plan: sinkt bis 25 % um 06:00“, nach Mitternacht „morgen um 06:00“), PV, Haus und Netz (je mit 2-h-Linie und Tageswert) und dem Strompreis (jetzt, nächste 3 h, günstigste Viertelstunde), die Karte „Woher kommt der Strom – wohin geht er?“ (zwei Balken, darunter die Verbraucher im Haus; ist ein Zweig unbekannt, steht er als „PV –“ in der Liste und die Summe der Seite als „–“) neben „Autos“ (je Auto „{Wallbox} · lädt …“, „{Wallbox} · angesteckt“, „nicht angesteckt“ oder „Stand unbekannt“, wenn der Bericht des core fehlt oder der Steckerstatus unbekannt ist), das Diagramm „Preise, PV und Akku heute/morgen“ (Preis je Viertelstunde im Tooltip, Akku-SOC und Akku-Plan) und „Warum?“ (unten). Das Alter steht als „vor 45 s“, „vor 12 min“, „vor 3 h“ oder „vor 2 Tagen“. Scheitert die Abfrage des Plans, des Verlaufs oder der Tageswerte, fehlt nur ihr Teil (Planzeile, Linie, Tageswert); das gilt bis zur ersten erfolgreichen Antwort, danach bleiben bei einem gescheiterten Neuabruf die letzten Werte stehen |
| Laden | je Fahrzeug und je Wallbox eine Karte, dazu „Gast“, „Akku-Unterstützung“ und „Vorrang“; in „Geplant“ mit Ziel-SOC, Abfahrt und „Fertig um“; ab „Bedienen“ mit den Bedienelementen (unten, „Laden“) |
| Zeitplan | der Akkuplan für bis zu 36 h: Kopf mit dem Stand der Ausführung, Quelle, Gültigkeit, Kosten gegenüber „Victron allein“ und je geplantem Auto einer Zeile wie auf „Laden“, Diagramme „Preise und Prognosen“, „Akku“ und, mit geplanten Autos, „Autos“, „Ablauf“ in Blöcken mit Satz; ab „Bedienen“ mit „Netzladen erlaubt“ und „Neu planen“ (Abschnitt „Planung“) |
| Prognosen | die Lastprognose mit Verfahren (Profil oder Modell), Grund und Taste „Jetzt neu trainieren“, die Prognosegüte von PV und Last (Profil und Modell) mit Zeitraum 7 bis 60 Tage, „Fehler je Tag“, „Backtest“ und „Merkmale“ (Abschnitt „Lastmodell (Phase 5a)“) |
| Verlauf | Diagramm mit bis zu 8 Reihen für frei wählbare Tage (höchstens 400), Tagesbilanz mit Abdeckung (Viertelstunden und Anteil der aufgezeichneten Zeit; am laufenden Tag „bis jetzt“, also nur die vergangenen Viertelstunden), Kosten, Erlös, Autarkie und den Tageswerten der Zähler (Differenz über den Tag) für den Abgleich mit VRM |
| Statistik | Tag, Woche, Monat oder Jahr mit Blättern: Autarkie, Eigenverbrauch, Kosten, Ersparnis und Einspeiseerlös, Diagramm „Energie“ und die Karten „Kosten“, „Autos“, „Geplantes Laden“, „Ladesessions“, „Verbraucher“, „Monatsspitzen“ und „Abgleich mit den Zählern“ (unten, „Statistik“) |
| Verbraucher | Verbraucherbaum mit „Sonstiges“, Leistung und Energie seit Mitternacht („Heute“); Admins legen Verbraucher an, ändern und löschen sie |
| System | Komponenten, Hinweise, core (Betriebsart mit Beginn und Gründen, Eingangsdaten, Adapter, Selbstprüfung, Alarme), Bedienung (Regelbetrieb; Admins: „Live schalten…“ und „Auf Dry-Run…“, Abschnitt 13), Aktoren (angefordert, wirksam, Freigabe, Hinweise und Sperren; Admins: „Quittieren“), Watchdog am Cerbo (Abschnitt 14), Preise (Admins: „Preise jetzt abrufen“), PV-Prognose, alle Signale mit Suche |
| Entscheidungen | das Entscheidungslog mit Filtern (unten, „Entscheidungen und Warum?“) |
| Einstellungen | Tarif, OeMAG-Werte, Preise, PV-Modell, Planung, Lastmodell (Schalter und Sensoren aus Home Assistant), Namen der Ladestationen und harte Grenzen; Admins bearbeiten, bewerten neu und exportieren oder importieren (Abschnitt 8) |
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
  Phasenwächters, der letzte Grund, die Session (unten) und, bei einem Fahrzeug mit Tessie, die
  Befehle der letzten Stunde (`commands_hour`, gegen das Stundenlimit von 20). Schätzt der core den
  Ladestand aus dem Zähler der Wallbox (Abschnitt „Laderegelung“), steht dieser Wert mit „geschätzt“
  da. Der Lademodus ist eine Auswahl: PV-Überschuss, Min+PV, Sofort, Aus oder Geplant. Die Taste „Boost“ setzt einen Boost bis zum Abstecken, höchstens 12 h;
  danach steht dort „Boost beenden“ und, solange er gilt, „bis HH:MM“. Gilt ein Boost schon, wartet
  aber auf einen Ansteckvorgang, der noch nicht begonnen hat, steht daneben „wartet auf Anstecken“. Die
  Dauer wählst du nicht. Ab „Bedienen“ steht unter den Bedienelementen, was ein Boost kosten kann:
  „Boost kann bis zu {€} € Leistungspreis im Monat kosten ({kW} kW mehr Spitze).“ (Abschnitt
  „Laderegelung“, „Lademodi“, Boost).
- **Höchststrom:** Steht das Fahrzeug auf „Sofort“, hat die Karte ab „Bedienen“ die Auswahl
  „Höchststrom“ (Plan Sofort-Limit). Die erste Wahl ist „Volle Leistung“ mit dem Höchststrom des
  Fahrzeugs und seinen kW („Volle Leistung (16 A · 11,0 kW)“), darunter jeder Strom bis zum
  Mindeststrom, jeweils mit den kW (Ampere · `grid.voltage_nominal_v` · Phasen des Fahrzeugs, etwa
  beim Tesla mit 3 Phasen „5 A · 3,5 kW“ bis „16 A · 11,0 kW“, beim e-Golf mit 2 Phasen „6 A · 2,8 kW“
  bis „16 A · 7,4 kW“). Die Wahl schickt `PUT /api/control/vehicles/{key}/now-limit`, gilt sofort und
  bleibt über Abstecken, Neustart und Moduswechsel stehen, bis du sie änderst. „Lesen“ sieht statt der
  Auswahl den Satz „Sofort mit höchstens {n} A ({kW} kW)“, und nur bei einem Limit. In anderen Modi
  steht die Auswahl nicht da. Läuft in „Sofort“ ein Boost, bleibt sie sichtbar, das Limit ruht aber:
  Boost lädt mit voller Leistung, und „Geplant“ rechnet mit voller Leistung (Abschnitt „Laderegelung“, „Lademodi“, „Sofort-Limit“).
- **Geplant:** Steht das Fahrzeug auf „Geplant“, hat die Karte ab „Bedienen“ die Felder „Ziel-SOC“ (%,
  ganze Zahl von 20 bis 100) und „Abfahrt“ (tägliche Uhrzeit) mit der Taste „Übernehmen“; sie schickt
  nur, was sich geändert hat (`PUT /api/control/vehicles/{key}/target`). Ein ungültiger Wert steht mit
  dem Satz der app an der Karte („Ziel-SOC „{Wert}“ liegt außerhalb von 20–100 %.“, „Abfahrt „{Wert}“
  ist keine Uhrzeit (HH:MM).“). Darunter steht, auch für „Lesen“, die erste zutreffende Zeile, mit
  denselben Werten wie „Fertig um“ und „Ziel erreichbar“ in Home Assistant (Abschnitt „Geplantes
  Laden“):
  - „Ziel erreicht ({soc} %).“, wenn Health das Ziel mit bekanntem Ladestand erreicht meldet;
  - „Fertig um {HH:MM} · Ziel {soc} % bis {HH:MM}“, wenn der gültige Plan das Auto lädt und das Ziel
    erreicht;
  - „Ziel {soc} % bis {HH:MM} nicht erreichbar, voraussichtlich {x} %.“, wenn der Plan es verfehlt;
  - „Kein Plan · lädt spätestens ab {HH:MM}“ ohne gültigen Plan für das Auto, mit der spätesten
    Startzeit aus Health.

  Nennt die Absicht einen Abfahrtstag, der nicht heute ist (Plan 5b, etwa von der Automatik), steht
  vor der Uhrzeit der Abfahrt Wochentag und Datum („Ziel 50 % bis Mo 23.11. 07:15“), und neben dem
  Feld „Abfahrt“ steht „am Mo 23.11.“. Änderst du die Uhrzeit von Hand, gilt sie danach täglich (der
  Hinweis verschwindet).

  Dazu, wenn es zutrifft: „Ladestand geschätzt“, „Ladestand unbekannt, gerechnet ab 0 %.“ (angenommen,
  nie „gemessen“) und „Ladelimit im Auto {x} % liegt unter dem Ziel.“ (ohne `charge_limit_sync`). Das
  Ziel ist das wirksame, bei einem Ladelimit darunter also das Limit. Abgesteckt oder in einem anderen
  Modus steht keine Zeile da; scheitert die Abfrage des Plans, fehlen die Zeilen mit Plan und ohne Plan.
  Die Seite fragt den Plan jede Minute ab und nach jeder Bedienung (auch „Übernehmen“) nach 2 s
  zusammen mit Health. Ein neues Ziel oder eine neue Abfahrt plant die app erst, wenn Health die
  Absicht bestätigt (Abschnitt „Planung“, Anlass `vehicles`). Meldet Health für ein Auto ein anderes
  Ziel oder eine andere Abfahrt als der Plan, fehlen die Zeilen mit Plan und ohne Plan (die Zusätze
  bleiben); dann fragt die Seite den Plan alle 10 s neu, bis er passt.
- **Session:** Eine Zeile unter den Fakten des Fahrzeugs, aus den Ladesessions der letzten 30 Tage
  (Abschnitt 8, „Ladesessions und Kosten je Auto“; die Seite fragt sie jede Minute ab). Läuft eine,
  steht dort „Session seit {HH:MM} · {kWh} kWh (PV {a} · Akku {b} · Netz {c})“. Sonst steht dort die
  letzte, „Letzte Session {TT.MM.} {HH:MM}–{HH:MM} · {kWh} kWh · {€} €“, die Kosten nur, wenn sie
  bekannt sind. Endet eine Session an einem anderen Tag als dem Beginn, steht beim Ende das Datum
  („18:00–09.10. 01:15“). Eine laufende Session steht auch dann da, wenn sie vor mehr als 30 Tagen
  begann. Ohne laufende Session und ohne Session in den letzten 30 Tagen sowie bei einem Fehler der
  Abfrage steht keine Zeile da.
- **Wallbox:** wer dort steht („kein Fahrzeug“, wenn kein Vorgang läuft, sonst das Fahrzeug oder
  „Gast“), die Art der Zuordnung (erkannt, angenommen, Übersteuerung, unklar) und die Indizien als
  Sätze („Tessie: zu Hause und angesteckt“, „Strom passt nicht zu Tessie“, …), je nachdem, ob sie das
  Fahrzeug stützen oder ausschließen. Die Auswahl „Zuordnung“ kennt „Automatisch“, das jede
  Übersteuerung dieser Wallbox aufhebt, und die Fahrzeuge; eine Wallbox als leer zu übersteuern geht
  nicht.
- **Gast:** der Lademodus für einen Gast an jeder Wallbox (Abschnitt „Laderegelung“, „Zuordnung Auto
  und Wallbox“). Steht der Gast auf „Sofort“, hat die Karte ab „Bedienen“ dieselbe Auswahl
  „Höchststrom“ für jeden Gast (`PUT /api/control/guest/now-limit`), im Strombereich der EVCS (kleinstes
  `min_a` bis größtes `max_a` aller EVCS; auf 3 Phasen etwa „6 A · 4,1 kW“ bis „16 A · 11,0 kW“).
  Die Auswahl fehlt ohne EVCS in `config.yaml`: Am Wall Connector hat ein Gast keinen Aktor und bleibt
  Last wie bisher.
- **Vorrang:** die Reihenfolge der Fahrzeuge bei der Zuteilung („nach oben“, „nach unten“) und der
  Vorrang beim Überschuss: „Autos zuerst“, „Akku zuerst“ oder „Akku bis {SOC} %, dann Autos“.

Ein Befehl, den der core ablehnt (422), erscheint mit den Sätzen des core an der Karte; ist der core
nicht erreichbar, steht dort „core nicht erreichbar.“.

### Zeitplan

Die Seite **Zeitplan** (Navigation nach „Laden“) zeigt den Plan, den die app für den Akku gerechnet hat,
und, in der obersten Zeile, ob der core ihn ausführt (Abschnitt „Planung“, „Stand der Ausführung“).
Darunter vergleicht die Karte „Plan gegen Ist (heute)“ den Plan mit der Abrechnung. Ab „Bedienen“
schaltest du dort „Netzladen erlaubt“ und löst „Neu planen“ aus; „Lesen“ sieht dieselbe Seite ohne
die Bedienelemente. Aufbau, Auslöser, Ersatzplan und die Sätze der Erklärung stehen im Abschnitt
„Planung“ am Ende dieses Handbuchs.

Lädt der Plan Autos im Modus „Geplant“, nennt der Kopf je Auto die Zeile wie auf „Laden“ („Fertig um
{HH:MM} · Ziel {soc} % bis {HH:MM}“ oder „… nicht erreichbar, voraussichtlich {x} %.“, dazu die Quelle
des Ladestands; liegt die Abfahrt nicht am nächsten Tag, an dem die Uhrzeit kommt (heute, wenn sie heute noch bevorsteht, sonst morgen), mit Wochentag und Datum wie auf der Fahrzeugkarte, etwa „bis Mo 23.11. 07:15“), und
das Diagramm „Autos“ zeigt je Auto eine Reihe mit seinem Namen: die geplante
Ladeleistung in kW (ohne Laden 0 kW).

### Statistik

Die Seite **Statistik** (Navigation nach „Verlauf“) zeigt die Abrechnung für einen Zeitraum. Alle
Rollen sehen sie, sie hat keine Bedienelemente. Die Daten kommen aus `GET /api/stats`, den Ladesessions
und dem Abgleich (Abschnitt 8).

- **Zeitraum:** „Tag“, „Woche“ (Montag bis Sonntag), „Monat“ und „Jahr“. Mit den Pfeilen „Zurück“ und
  „Weiter“ blätterst du, „Heute“ springt zum laufenden Zeitraum. „Weiter“ und „Heute“ sind im laufenden
  Zeitraum gesperrt, wo sie nichts bewirken. Die Überschrift nennt den Zeitraum: „Fr., 09.10.2026“, „KW 41 ·
  05.10.–11.10.2026“, „Oktober 2026“ oder „2026“. Zeitraum und Datum stehen in der Adresse
  (`/statistik?zeitraum=monat&datum=2026-10-09`), du kannst sie als Lesezeichen setzen. Beim Blättern
  bleiben die Werte des vorigen Zeitraums mit ihren Hinweisen und Ladesessions gedimmt stehen, bis
  die neuen da sind. Scheitert die Abfrage, steht die Meldung statt der Werte. „Lädt …“ steht nur,
  solange keine Werte zu zeigen sind: beim ersten Öffnen und beim Blättern nach einem Fehler.
- **Hinweise im Kopf:** „Abdeckung {pct} %“ (Anteil der Zeit mit aufgezeichneter Abrechnung), bei
  geschätzten Viertelstunden „{n} Viertelstunden geschätzt“ (Abschnitt 8, „Lücken nachrechnen“) und bei
  einem laufenden Zeitraum „bis jetzt“.
- **Kacheln:** „Autarkie“, „Eigenverbrauch“, „Kosten“ (mit Grundgebühren und, in Monat und Jahr, mit
  Leistungspreis), „Ersparnis“ (Untertitel „gegenüber Fixtarif ohne Anlage“, ohne Grundgebühren und
  Leistungspreis, denn die fielen auch ohne Anlage an) und „Einspeiseerlös“. Was nicht feststeht, weil
  etwa ein Preis fehlt, steht als „–“.
- **Diagramm „Energie“:** PV, Bezug und Einspeisung als Balken, Verbrauch als Linie; je Stunde (Tag, mit
  23 oder 25 Stunden an der Zeitumstellung), je Tag (Woche, Monat) oder je Monat (Jahr).
- **„Kosten“:** die Zeilen „Bezug“, „Einspeiseerlös“, „Netto“, „Grundgebühren“, „Leistungspreis“ (nur
  Monat und Jahr, und nur mit Leistungspreis mit Satz) und „Summe“, darunter „Ersparnis ohne Anlage“ und
  „Ersparnis bei gleichem Netzbezug“.
- **„Autos“:** je Auto Energie, die Herkunft PV, Akku und Netz in kWh, Kosten, ct/kWh und die Zahl der
  Sessions, dazu die Zeile „Akku → Autos“. Gast und ohne erkanntes Auto erscheinen als „Gast“ und
  „Unbekannt“. Ohne Ladung: „Im Zeitraum wurde kein Auto geladen.“ PV-Strom ins Auto kostet 0 ct
  (Abschnitt 8, „Einstandspreis des Akkus“).
- **„Laden je Auto“:** wählst du oben ein Auto („Alle“ oder eines der Liste, auch „Gast“ und „Unbekannt“),
  zeigt das Diagramm es allein und die Karte „Ladesessions“ nur dessen Sessions. Die Wahl steht in der
  Adresse (`/statistik?zeitraum=monat&datum=2026-10-09&auto=tesla`) und bleibt beim Blättern erhalten;
  „Alle“ entfernt `auto=`. Mit „kWh“ oder „€“ wählst du die Einheit. In Woche, Monat und Jahr steht je Tag
  bzw. Monat ein gestapelter Balken, bei „Alle“ je Auto, bei einem Auto nach Herkunft (PV, Akku, Netz).
  Bei „Tag“ steht stattdessen „Den Verlauf zeigen Woche, Monat und Jahr.“ Unbekannte Kosten sind eine
  Lücke im Balken, im Hinweis beim Darüberfahren steht „–“. Hat im Zeitraum kein Auto geladen, steht
  „Im Zeitraum wurde kein Auto geladen.“
- **„Geplantes Laden“:** die Abnahme „Ziel-SOC“ (Abschnitt 8, „Statistik je Zeitraum“): „{a} von {b}
  machbaren Ladungen erreicht ({pct} %)“, abgerundet (94,6 % stehen als 94 %), darunter „Nicht machbar:
  {n} · Vor der Abfahrt abgesteckt: {n} · Ohne Ladestand: {n}“ und „Vor der Abfahrt geändert oder
  verlassen: {n}“; diese zählen nicht in die Quote. Ohne machbare Ladung steht „(– %)“. Leer: „Im
  Zeitraum gab es keine geplante Ladung.“
- **„Ladesessions“:** Beginn, Ende (eine laufende Session: „läuft“), Wallbox, Auto, Energie, „PV / Akku /
  Netz“ und Kosten, neueste zuerst. Zuerst stehen 50; darunter sagt „{n} von {m} angezeigt“, wie viele
  es sind, und „Weitere 50 anzeigen“ zeigt die nächsten 50. „CSV herunterladen“ speichert alle
  geladenen Sessions der Auswahl (nicht nur die angezeigten) als Datei
  `ladesessions-{erster Tag}-{letzter Tag}[-{Auto}].csv` für Excel: Semikolon als Trenner, UTF-8 mit
  BOM, Zeilenende `\r\n`, Kopfzeile
  `Beginn;Ende;Wallbox;Auto;Energie kWh;PV kWh;Akku kWh;Netz kWh;Kosten EUR`, Zeiten in Wiener Zeit als
  `09.10.2026 08:00` (bei einer laufenden Session bleibt das Ende leer), Energie mit drei und Kosten mit
  zwei Nachkommastellen und Komma, ohne Tausenderpunkt; unbekannte Kosten bleiben leer.
  Sind es 2000 oder mehr, steht darunter „Angezeigt werden die 2000 neuesten Sessions.“ Leer: „Keine
  Ladesessions im Zeitraum.“
- **„Verbraucher“:** die Energie je Verbraucher im Zeitraum. Fehlt sie, steht „–“ und darunter klein
  der Grund. Zu den Tagen siehe „Energie der Verbraucher“ unten.
- **„Monatsspitzen“:** nur in Monat und Jahr: je Monat die höchste Viertelstunde des Netzbezugs mit
  Zeitpunkt und, mit Leistungspreis, dessen Kosten. Gibt es keine, fehlt die Karte.
- **„Abgleich mit den Zählern“:** je Größe (Netzbezug, Einspeisung, „PV (ohne Markierung)“, jede
  Wallbox) die Summe der Abrechnung, die der Zähler und die Abweichung über die geprüften Tage mit
  Zählerwert, dazu „Mehr als 1 % Abweichung am {Tage}.“ für die markierten Tage und „Noch kein Tag
  abgeglichen.“ ohne einen geprüften Tag. Eine Größe, die an keinem geprüften Tag einen Zählerwert
  hat, fehlt in der Tabelle; darunter steht dann gedämpft „Ohne Zählerwerte: {Größen}.“, z. B.
  „Ohne Zählerwerte: Wallbox EVCS.“ Den Grund je Tag nennt `GET /api/ledger/checks` (z. B.
  „Zählerstand fehlt“). Bleibt so keine Größe übrig und ist kein Tag markiert, steht dort nur „Keine
  Zählerwerte für diesen Zeitraum.“ Wie du eine Abweichung deutest, steht in Abschnitt 8, „Abgleich
  mit den Zählern“.

Auf dem Handy scrollen die breiten Tabellen in ihrer Karte; die Seite selbst scrollt nicht seitlich.

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
Plan-Slots zeigt „Warum?“ noch nicht; sie steht auf der Seite „Zeitplan“ („Ablauf“).

### Namen der Ladestationen

Unter **Einstellungen** → „Bearbeiten“ → „Ladestationen“ gibt ein Admin jeder Wallbox einen
eigenen Namen (höchstens 40 Zeichen). Er erscheint in „Woher – Wohin“, im Verlauf und bei den
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
- **Je Tag gespeichert:** Seit Plan 4a speichert die app die Energie jedes Verbrauchers auch je Tag, für
  die Statistik. Sie rechnet dafür wie für „Heute“. Beim Start holt sie die fehlenden Tage der letzten
  31 nach, jede Nacht um 02:45 Uhr die der letzten 3; scheitert InfluxDB in einer Nacht, geht der Tag
  also nicht verloren. Ältere Tage holt die app nicht nach; sie stehen als „Tag nicht gespeichert“
  leer da. Ist der Verlauf eines Verbrauchers gerade nicht lesbar, behält er seine alte Zeile; ein neu
  angelegter Verbraucher füllt die letzten 31 Tage beim nächsten Start, und löscht du ihn, verschwinden
  auch seine Tage.

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
- **veraltet:** Seit über 5 s kamen keine neuen Werte. Kacheln, „Woher – Wohin“ und „Autos“ zeigen die
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
`bedienen` hat dort die Rolle „Bedienen“. Der Tesla steckt dort am Wall Connector und lädt „Geplant“
(Ziel 60 %, Abfahrt 17:30, Ladestand angenommen); der e-Golf steckt an der EVCS mit einem geschätzten
Ladestand.

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
  `intent.target`, `intent.now_limit`, `intent.boost`, `intent.assignment`, `intent.guest_mode`,
  `intent.guest_now_limit`, `intent.priority`, `intent.actuators`, `control.support`).
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

Aktoren sind `victron`, `wallbox.<name>` für jede EVCS, `vehicle.<name>` für jedes Fahrzeug
mit Tessie und seit Plan 6a `thermal.<name>` für die Klimaanlage. Der Wall Connector und der e-Golf
sind keine Aktoren. Jeder Aktor hat eine
Betriebsart: **Dry-Run** (die Safety-Schicht prüft jeden Befehl, der core protokolliert, was er
senden würde, und sendet nichts) oder **live**.

Ein Aktor läuft nur live, wenn alle vier Bedingungen gelten (Spec 8.7):

1. `live_allowed: true` für den Aktor in `config.yaml`. Der Standard ist `false`. Es steht unter
   `victron`, bei jeder EVCS unter `wallboxes.<name>`, bei jedem Fahrzeug unter `vehicles.<name>`
   und bei der Klimaanlage unter `thermal.devices.<name>`; bei einem Fahrzeug verlangt es den
   Abschnitt `tessie`, bei einer EVCS ihren Eintrag unter `victron.instances.evcharger` (sonst setzt
   der Watchdog für sie keinen sicheren Strom). Fehlt das, lehnen core und app die `config.yaml`
   beim Start mit diesem Grund ab.
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
6. **Klimaanlage (Plan 6a):** Netz für UDP 5442 freigeben, `coe_probe listen`, Prüfprotokoll Teil 2
   Punkt `thermal`, Dry-Run mindestens 7 Tage, dann live (Abschnitt „Klimaanlage (Phase 6a)“). Sie
   ist von EVCS, Tesla und Victron unabhängig und braucht keinen Watchdog.

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
  und endet mit dem Exitcode 130, ohne Bericht. `docker stop` (SIGTERM) und SIGHUP behandelt sie
  ebenso, nur mit dem Exitcode 143; die Freigabe dauert höchstens 2 s und passt in die 10 s von
  `docker stop`. Ein SIGKILL, auch das von `docker stop` nach 10 s, gibt ihn **nicht** frei. Gelingt die Freigabe nach einem Abbruch nicht, warnt die Probe auf der Konsole: Der
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
| 15-min-Ziel überschritten: … kW in der Viertelstunde bis HH:MM. | Stufe Warnung. Die Hochrechnung lag 60 s über dem Ziel (dem Budget des gültigen Plan-Slots, sonst `control.peak_target_kw`), und Drosselung und Akku haben es nicht gehalten. Der Alarm endet mit der Viertelstunde (Abschnitt „Akku und Spitzen“, „Alarme“). |
| Regelung der Klimaanlage ausgesetzt: Ausnahme … im Regelzyklus. | Stufe Warnung (`control.thermal_exception`). Die Strategie der Klimaanlage hat eine Ausnahme geworfen. Der core regelt Akku und Autos weiter, die Klimaanlage bleibt bei Versatz 0. Sichere das Log und melde den Fehler (Abschnitt „Klimaanlage (Phase 6a)“, „Alarme und Meldungen“). |
| … Das Echo der UVR zeigt seit mehr als 120 s nicht den gesendeten Versatz (…). | Stufe Warnung (`thermal.echo_mismatch.<name>`). Die UVR bestätigt den gesendeten Versatz nicht. Prüfe Empfangstabelle, `own_node` und `own_output` und die Visu der UVR (Abschnitt „Klimaanlage (Phase 6a)“, „Alarme und Meldungen“). |

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
„Laden“) und in Home Assistant (Abschnitt 7). Seit Plan 4b gibt es den Modus „Geplant“ mit Ziel-SOC
und Abfahrt (unten, „Geplant“); dafür kennt der core den Ladestand der Autos und, mit
`charge_limit_sync`, schreibt er das Ladelimit des Tesla.

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
| Geplant | `planned` | Lädt bis zur Abfahrt auf den Ziel-SOC: mit der Ladeleistung des Plans und zusätzlichem PV-Überschuss, ohne Plan ab der spätesten Startzeit mit voller Leistung, davor nur PV-Überschuss; am Ziel stoppt es (unten, „Geplant“). Nicht für einen Gast. | mit festem Bedarf (Plan-Leistung oder späteste Startzeit) wie Sofort, sonst wie PV-Überschuss |
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
  „Akku und Spitzen“). Er kann den Leistungspreis des Monats erhöhen: Die Seite „Laden“ zeigt ab
  „Bedienen“ „bis zu {€} € Leistungspreis im Monat“. Gerechnet wird der Leistungspreis (brutto) für
  die Monatsspitze der Abrechnung, mindestens die Mindestleistung des Tarifs, plus die volle
  Ladeleistung des Autos (`max_a` · Phasen · `grid.voltage_nominal_v`) abzüglich des Leistungspreises
  für die Monatsspitze allein, mit dem Satz des aktuellen Monats (Wiener Zeit). Das ist eine Obergrenze:
  Sie gilt, wenn der Boost die Spitze wirklich um die volle Ladeleistung hebt. Gilt im Monat kein
  Leistungspreis mit Satz, fehlt der Hinweis. Die Zahlen liefert `GET /api/control/boost-cost`
  (ab „Lesen“).
- **Sofort-Limit** (Plan Sofort-Limit): In „Sofort“ lädt ein Auto höchstens mit dem Strom, den du
  bei „Höchststrom“ gewählt hast (Seite „Laden“, oder in Home Assistant die Zahl „Sofort-Höchststrom“;
  für Gäste „Gast-Sofort-Höchststrom“). Der Strom ist eine ganze Zahl in A je Phase, vom Mindeststrom
  bis zum Höchststrom des Fahrzeugs; „volle Leistung“ ist der Höchststrom des Fahrzeugs und wird als
  „kein Limit“ gespeichert, sodass das Auto einem später höheren `max_a` in `config.yaml` folgt. Das
  Limit gilt je Auto (und einmal für Gäste) dauerhaft und nur in „Sofort“; Boost lädt weiter mit
  voller Leistung, „Geplant“ rechnet mit voller Leistung, PV-Überschuss und Min+PV begrenzt der
  Überschuss. Es ist eine harte Grenze: Das Auto lädt nie darüber, auch wenn mehr PV-Überschuss da
  wäre; der Rest geht an andere PV-Autos, die Klimaanlage, den Akku oder ins Netz. Unter den
  Mindeststrom der Wallbox geht es nie (ein Limit darunter lädt mit dem Mindeststrom), und die
  Wächter kappen weiter darunter. Ändert sich das Limit während des Ladens, folgt der Strom im
  nächsten Regelzyklus (beim Tesla im Rahmen der Befehlslimits von Tessie). Kappt das Limit, trägt der
  Befehl nach „Sofort“ den Grund „Sofort-Limit“ (`now_limit`); kappt ein Wächter darunter, steht
  dessen Grund. Liegt ein Wächter zwischen Limit und vollem Strom (etwa Limit 10 A, Phasenwächter
  12 A), stehen beide: „Sofort-Limit“, danach der Grund des Wächters. Ein Gast wird nur an der EVCS
  begrenzt.
- Ein **Moduswechsel** wirkt sofort, ohne Startzeit, Stoppzeit und Mindestzeiten.
- Nach einem Start lädt ein Auto mindestens `control.min_on_s` (600 s), nach einem Stopp startet es
  frühestens nach `control.min_off_s` (600 s) wieder. Das sind höchstens 3 Start/Stopp-Zyklen je Auto
  und Stunde.
- Ein Auto startet nur, wenn die Grenze des Phasenwächters für es mindestens seinen Mindeststrom
  trägt (in Min+PV die Grenze an der Sicherung). Ist sie kleiner oder unbekannt, bleibt es aus; ein
  Auto, das schon lädt, geht auf den Mindeststrom.
- Einen **Ziel-SOC** gibt es nur im Modus „Geplant“ (unten). In den anderen Modi stoppen die Autos an
  ihrem eigenen Ladelimit, der e-Golf lädt bis voll.
  Meldet ein Auto „geladen“, startet die Regelung es erst nach dem Abstecken wieder, oder wenn Tessie
  danach ein anderes Ladelimit meldet (etwa in der Tesla-App angehoben): „Geladen“ gilt je
  Ansteckvorgang und Ladelimit, für das Limit, bei dem es gemeldet wurde, auch wenn danach noch ein
  alter Bericht kommt. Das gilt seit Plan 4b in jedem Modus.
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

### Geplant

Seit Plan 4b lädt ein Fahrzeug im Modus **„Geplant“** (`planned`) bis zu seiner Abfahrt auf seinen
Ziel-SOC, so günstig, wie der Plan der app es vorsieht (Abschnitt „Planung“, „Autos im Plan“). Du
stellst den Modus, das Ziel und die Abfahrt auf der Seite „Laden“ (Abschnitt 12) oder in Home
Assistant (Abschnitt 7) ein; ohne app über `PUT /v1/intent` (unten). Ein Gast kann nicht „Geplant“
laden: Für ihn gibt es weder Ladestand noch Abfahrt.

- **Ziel und Abfahrt** stehen je Fahrzeug in der Absicht: `target_soc_pct` (ganze Zahl von 20 bis
  100, Standard 80) und `departure` (tägliche Uhrzeit `HH:MM`, Wiener Zeit, Standard `07:00`). Beide
  bleiben in den anderen Modi stehen; beim Umschalten auf „Geplant“ musst du nichts nachtragen. Es
  gilt die nächste Abfahrt nach jetzt: Ist sie vorbei und das Auto steckt noch, gilt die des nächsten
  Tages. Gibt es die Uhrzeit an einem Tag nicht (Beginn der Sommerzeit, 02:30), gilt 03:00; gibt es
  sie zweimal (Ende der Sommerzeit), das erste Vorkommen.
- **Abfahrt an einem Tag:** Optional nennt die Absicht mit `departure_on` (`YYYY-MM-DD`, lokales
  Datum) einen Tag. Dann gilt die Abfahrt an diesem Tag, solange der Zeitpunkt in der Zukunft liegt;
  danach gilt `departure` wieder täglich. Wer über `PUT /api/control/vehicles/{key}/target` eine
  Abfahrtszeit setzt, ersetzt den Tag: ohne `departure_on` im Aufruf wird er gelöscht. Ein Aufruf ohne
  Abfahrtszeit lässt ihn stehen, und `departure_on` ohne Abfahrtszeit lehnt die app ab („Ein
  Abfahrtstag verlangt eine Abfahrtszeit.“). Im Änderungsprotokoll steht er mit Wochentag: „Ziel 80 %,
  Abfahrt Mo 23.11. 07:15“. Die Home-Assistant-Entität „Abfahrtszeit“ (`time`) sendet nur die Uhrzeit
  und löscht den Tag also.
- **Ladestand** (Health `soc_pct` mit `soc_source`):
  - `measured` (gemessen): beim Tesla aus Tessie, höchstens 900 s alt; beim e-Golf aus Home Assistant
    (`soc_entity`; die Einstellung steht unten in „Zuordnung Auto und Wallbox“).
  - `estimated` (geschätzt): der letzte Messwert plus die Energie, die der Zähler der Wallbox seither
    gezählt hat, mal Wirkungsgrad, geteilt durch `usable_kwh`, höchstens 100 %. Der Messwert zählt nur,
    wenn er höchstens 60 min vor dem Anstecken oder danach gelesen wurde; sonst kann das Auto seither
    gefahren sein. Lag die Messung vor dem Anstecken oder fehlte der Zählerstand, rechnet die
    Schätzung ab dem ersten Zählerstand dieses Ansteckvorgangs: Was die Wallbox vorher zählte, ging in
    ein anderes Auto. Ein Zähler, der sinkt (Tausch), macht den Messwert als Grundlage ungültig.
    Home Assistant meldet den Ladestand des e-Golf oft nur beim Parken; derselbe Wert mit demselben
    Zeitstempel ist keine neue Messung, und während der e-Golf lädt, ist sein Ladestand geschätzt.
    Ein Wert von Tessie ist bei jeder Abfrage eine neue Messung.
  - `assumed` (angenommen): weder gemessen noch schätzbar. „Geplant“ rechnet dann mit 0 %: Es lädt
    früh genug, erkennt das Ziel aber nicht und lädt, bis das Auto selbst aufhört. UI und Home
    Assistant sagen „angenommen“ bzw. „Ladestand unbekannt, gerechnet ab 0 %.“, nie „gemessen“.
  - Ein Ansteckvorgang, der vor dem Start des core begann, beginnt für den core mit seinem ersten
    Zyklus.
- **Wirksames Ziel und Bedarf:** Ohne `charge_limit_sync` und mit einem bekannten Ladelimit im Auto
  unter dem Ziel ist das Limit das Ziel (UI und Home Assistant: „Ladelimit im Auto {x} % liegt unter
  dem Ziel.“). Der Bedarf ist `(Ziel − Ladestand) / 100 · usable_kwh / Wirkungsgrad`, die Energie aus
  der Wallbox (`charge_efficiency_pct`, unten).
- **Stand** (Health `planned.state`), je Zyklus der erste zutreffende:
  - `reached`: Der Ladestand ist bekannt (gemessen oder geschätzt) und mindestens am Ziel. Die
    Regelung stoppt das Laden wie bei „geladen“ (einmal „Laden aus“, Grund `target_reached`) und lädt
    über dem Ziel auch keinen PV-Überschuss. Sie lädt erst wieder, wenn der Ladestand 3 Prozentpunkte
    unter das Ziel fällt, das Ziel steigt oder ein neuer Ansteckvorgang beginnt. Mit einem
    angenommenen Ladestand gibt es kein „Ziel erreicht“.
  - `plan`: Der gültige Slot des Plans nennt das Auto mit einer Ladeleistung über 0. Diese Leistung ist
    ein **fester Bedarf** (wie Sofort, aber mit der Leistung des Plans); zusätzlicher PV-Überschuss
    kommt obendrauf. Grund `plan_power`.
  - `due`: Es gibt keinen gültigen Slot für das Auto (kein Plan, abgelaufen, oder der gültige Slot
    nennt das Auto nicht, etwa weil es nach dem Plan angesteckt wurde) und die **späteste Startzeit**
    ist erreicht: volle Leistung bis zum Ziel, Grund
    `latest_start`. Einmal erreicht, bleibt das bis zum Ziel oder bis zum Abstecken, auch wenn danach
    wieder ein Plan gilt, der das Auto mit 0 W nennt; nur ein Slot mit Leistung über 0 geht vor.
  - `surplus`: sonst, auch bei einem gültigen Slot mit 0 W: nur PV-Überschuss, mit den Zeiten des
    PV-Modus (Start nach 120 s, Stopp nach 300 s, Mindestlaufzeit und Mindestpause).
- **Späteste Startzeit:** `Abfahrt − Bedarf / volle Leistung · 1,1 − 15 min`. Die volle Leistung ist
  `min(max_a des Fahrzeugs, max_a der Wallbox) · grid.voltage_nominal_v · Phasen des Fahrzeugs`. Der
  Wirkungsgrad steckt schon im Bedarf und kommt nicht ein zweites Mal dazu. Beispiel: 22,08 kWh Bedarf
  bei 11,04 kW (16 A, 3 Phasen) dauern 2 h, mit Zuschlag 2 h 12 min; bei Abfahrt 07:00 ist die späteste
  Startzeit 04:33. Ohne Bedarf gibt es keine.
- **Fester Bedarf wie Sofort:** Mit festem Bedarf (`plan` oder `due`) startet das Auto sofort, auch kurz
  nach einem Stopp aus dem PV-Überschuss (dessen Pause verfällt wie bei einem Wechsel auf Sofort; nur
  die Mindestpause nach einem Stopp wegen Überlast bleibt). Das Ziel des Phasenwächters ist dann die
  Sicherung, unter USV-Reserve + `control.reserve_guard_pct` das Importlimit, wie bei Sofort. Der
  Strom ist der höhere aus der Plan-Leistung (aufgerundet auf ganze Ampere) und dem Anteil am
  Überschuss, zwischen Mindest- und Höchststrom; der feste Bedarf geht vom Überschuss ab, bevor die
  PV-Autos ihren Anteil bekommen. Ohne festen Bedarf gilt „Geplant“ wie PV-Überschuss, auch für das
  Importlimit und beim neuen Ansteckvorgang („Laden aus“ zu Beginn).
- **Plan läuft mitten im Laden ab** (etwa weil die app steht): Ist die späteste Startzeit erreicht,
  lädt das Auto ohne Stopp mit voller Leistung weiter; sonst gilt es wie PV-Überschuss mit dessen
  Zeiten, ohne sofortigen Stopp in der Mindestlaufzeit.
- **Akku:** „Geplant“ bekommt keine Akku-Unterstützung; was dem Auto fehlt, kommt wie bei Sofort aus
  dem Netz, denn über das Netzladen des Autos entscheidet der Plan (Abschnitt „Akku und Spitzen“).
- **15-min-Wächter:** Er drosselt „Geplant“ nach PV-Überschuss und Min+PV und vor Sofort, höchstens
  bis zur Mindestleistung des Autos (Abschnitt „Akku und Spitzen“).
- **Ladelimit des Tesla:** Mit `charge_limit_sync` setzt der core es in „Geplant“ an das Ziel (unten,
  „Tesla“).
- **Neue Gründe** im Entscheidungslog: `mode_planned` („Geplant“), `plan_power`, `latest_start`,
  `target_reached` und `charge_limit` (unten, „Dry-Run-Vergleich mit evcc“, Tabelle der Gründe).

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
  dringender Befehl nicht. In „Geplant“ mit festem Bedarf (oben, „Geplant“) ist nur eine Senkung
  dringend, die schon die Sicherung allein verlangt: Die Leistung des Plans liegt oft unter dem
  Ist-Strom, und eine Senkung auf sie ist normal; sie wartet auf Mindestabstand, Stundenlimit und
  Reserve des Tesla. Alle anderen Befehle sind normal, auch Senkungen am Importlimit (die
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
wieder im PV-Überschuss. Für „Geplant“ hieße der Eintrag des Tesla etwa
`{"mode": "planned", "target_soc_pct": 80, "departure": "07:00"}`.

| Feld | Werte | Standard |
|---|---|---|
| `version` | ganze Zahl ab 1, streng steigend | – |
| `control` | `true` schaltet den Regelbetrieb ein | `false` |
| `actuators` | je Aktor-ID (`wallbox.evcs`, `vehicle.tesla`, …) `dry_run` oder `live` | Dry-Run |
| `vehicles.<Schlüssel>.mode` | `pv`, `min_pv`, `now`, `off`, `planned` (seit Plan 4b, oben „Geplant“) | `pv` |
| `vehicles.<Schlüssel>.target_soc_pct` | Ziel-SOC für „Geplant“, ganze Zahl von 20 bis 100 | 80 |
| `vehicles.<Schlüssel>.departure` | tägliche Abfahrt für „Geplant“, `HH:MM` (Wiener Zeit) | `07:00` |
| `vehicles.<Schlüssel>.departure_on` | optionaler Tag der Abfahrt für „Geplant“, `YYYY-MM-DD` (lokales Datum); ist der Zeitpunkt vorbei, gilt `departure` täglich | kein Tag |
| `vehicles.<Schlüssel>.boost_until` | Zeitpunkt mit Zeitzone, höchstens 12 h nach dem Senden | kein Boost |
| `vehicles.<Schlüssel>.wallbox` | Schlüssel einer Wallbox; übersteuert die Zuordnung (unten) | keine Übersteuerung |
| `priority` | alle Fahrzeug-Schlüssel genau einmal, in der Reihenfolge der Zuteilung | Reihenfolge unter `vehicles` in `config.yaml` |
| `surplus_priority` | `kind`: `cars_first`, `battery_first` oder `battery_until_soc` (dann mit `soc_pct`) | `battery_until_soc`, 30 % |
| `guest.mode` | `pv`, `min_pv`, `now`, `off`: der Modus eines Gastes, also eines Autos, das nicht in `config.yaml` steht (Abschnitt „Zuordnung Auto und Wallbox“); kein Boost, keine Übersteuerung, kein `planned` | `pv` |
| `support` | `allowed` (`true` oder `false`) und `soc_pct` (Unterstützungsschwelle in %, von der USV-Reserve bis 100, oder `null` für `control.support_soc_pct`; Abschnitt „Akku und Spitzen“, „Akku-Unterstützung bedienen“) | erlaubt, Schwelle aus der Konfiguration |
| `plan` | der Plan der app (Abschnitt „Planung“, „Übergabe an den core“): `id`, `created_at`, `valid_until`, `grid_charge_allowed` und 1 bis 8 `slots` mit `start`, `intent` (`kind` und `grid_charge_w`), `budget_kw`, `import_ct`, `support_soc_pct` und seit Plan 4b `vehicles` (Ladeleistung je Fahrzeug-Schlüssel in ganzen W; jedes Auto des Plans vor seiner Abfahrt, auch mit 0 W; ein Auto, das der Slot nicht nennt, lädt nach der spätesten Startzeit). Die app schickt ihn selbst; per `curl` übernimmst du ihn aus `GET /v1/intent`. | kein Plan |

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
    {HH:MM}.“ (Wiener Zeit), dass ein Aktor ohne `live_allowed` im Dry-Run bleibt, „Plan bis {HH:MM}
    begrenzt.“ (`valid_until` höchstens 45 min nach der Annahme, Wiener Zeit) oder „Netzladen auf {W} W
    begrenzt (maximaler Netzladesetpoint).“ (`victron.max_grid_charge_setpoint_w`) oder
    „Unterstützungsschwelle 15 % auf die USV-Reserve (20 %) angehoben.“ (`support.soc_pct` unter
    `battery.reserve_soc_pct`, etwa nachdem die Reserve in `config.yaml` stieg; so scheitert keine
    Bedienung, auch nicht „Regelbetrieb aus“, an einer alten Schwelle) oder „Plan für {Name} auf {W}
    W begrenzt.“ (die Ladeleistung eines Fahrzeugs im Plan über `max_a` · `grid.voltage_nominal_v` ·
    Phasen). Ein Plan, der schon abgelaufen ist, gilt als angenommen und wirkt nicht.
  - `rejected` mit Status 422, mit je einem Satz in `details`: ein unbekanntes Fahrzeug
    (`Unbekanntes Fahrzeug „x“.`), eine unbekannte Wallbox (`Unbekannte Wallbox „w“ für „v“.`), eine
    Wallbox bei mehreren Fahrzeugen (`Wallbox „w“ ist mehreren Fahrzeugen zugeordnet.`), eine
    Priorität, die nicht jedes Fahrzeug genau einmal nennt (`Die Priorität muss jedes Fahrzeug genau
    einmal nennen.`), ein unbekannter Aktor oder ein Plan, der nicht passt: `Plan: Slots nicht im Viertelstundenraster oder nicht lückenlos.`, `Plan: zu viele Slots
    (höchstens 8).`, `Plan: Gültigkeit passt nicht zu den Slots.` (es muss `slots[0].start ≤
    created_at < valid_until ≤` Ende des letzten Slots gelten, und höchstens 45 min zwischen
    `created_at` und `valid_until` liegen), `Plan: Netzladen, obwohl ausgeschaltet.`, `Plan nennt
    unbekanntes Fahrzeug „x“.` oder `Plan hat negative Ladeleistung für „x“.`; dazu seit Plan 4b `Ein
    Gast kann nicht „Geplant“ laden.` für `guest.mode` `planned`
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

  Seit Plan 3c ist auch **jeder neue Plan eine neue Version** (etwa alle 15 min) und bindet neu. Ein
  Boost, der auf den nächsten Vorgang der Standard-Wallbox wartet, geht so mit dem nächsten Plan an
  den Vorgang, an dem der core das Fahrzeug inzwischen erkennt oder annimmt; vorher geschah das erst
  mit der nächsten Bedienung. Einen Plan sendet die app nur, wenn der core mit der gültigen Absicht
  regelt (Abschnitt „Planung“, „Übergabe an den core“); so bindet ein Plan nach einem Neustart des
  core keinen wiederhergestellten oder verbrauchten Boost an das nächste Anstecken.
- Der core speichert die gültige Absicht (`intent.json`) und nutzt sie nach einem Neustart weiter.
  Eine gespeicherte Absicht aus Plan 2b lädt mit den Standardwerten, eine ohne Ziel und Abfahrt
  (vor Plan 4b) mit 80 % und 07:00.
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

**Ladestand, Wirkungsgrad und Ladelimit für „Geplant“.** Drei Schlüssel je Fahrzeug in `config.yaml`
gehören zum Modus „Geplant“ (oben, „Geplant“); `charge_efficiency_pct` und `charge_limit_sync` sind
neu mit Plan 4b und haben einen Standard, du musst also nichts eintragen:

```yaml
vehicles:
  egolf:
    # ...
    soc_entity: sensor.egolf_soc
    charge_efficiency_pct: 90      # optional, 50 bis 100, Standard 90
  tesla:
    # ...
    charge_limit_sync: false       # optional, Standard aus, verlangt den Abschnitt tessie
```

- `soc_entity` (Ladestand in Home Assistant) abonniert der core selbst und nur lesend (Signal
  `vehicle.<Schlüssel>.soc_pct`), wie `home_entity`. Du trägst ihn nicht zusätzlich unter
  `homeassistant.entities` ein. Steht dort doch ein Eintrag mit demselben Signal und einer anderen
  Entität, lehnt der core die Konfiguration ab: `vehicle.egolf.soc_pct ist in
  homeassistant.entities anders belegt als soc_entity`. Der Ladestand des Tesla kommt aus Tessie.
- `charge_efficiency_pct` ist der Wirkungsgrad des Ladens (Netz bis Akku des Autos): Der Bedarf eines
  Autos ist `(Ziel − Ladestand) / 100 · usable_kwh / Wirkungsgrad`. Planer und core rechnen
  gleich.
- `charge_limit_sync` erlaubt dem core, das Ladelimit des Tesla in „Geplant“ an das Ziel
  anzugleichen (unten, „Tesla“). Ohne `tessie` lehnt der core die Konfiguration ab: `charge_limit_sync verlangt
  einen tessie-Abschnitt`. Ohne den Schlüssel bleibt das Limit, wie es im Auto steht; liegt es unter
  dem Ziel, rechnen core und Planer mit dem Limit als Ziel.

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
- **Ladelimit** (seit Plan 4b, nur mit `charge_limit_sync`): Im Modus „Geplant“ setzt der core das
  Ladelimit des Tesla auf `max(Ziel, 50)` % (Tessie `set_charge_limit`, im Log „Ladelimit 80 %“,
  Grund `charge_limit`), einmal je Ansteckvorgang und nach jeder Änderung des Ziels, nur an ein
  waches Auto (er weckt dafür nicht). Danach stoppt der Tesla selbst am Ziel; das alte Limit stellt
  der core nicht zurück. Meldete der Tesla „Complete“ am alten, niedrigeren Limit, lädt er bis zum
  neuen, sobald Tessie es meldet: „Geladen“ gehört zum Limit, bei dem es entstand, auch wenn danach
  noch ein alter Bericht kommt. Ist er auch am neuen voll, meldet Tessie wieder „Complete“. Die Safety-Schicht lässt ganze Prozent von 50 bis 100 zu (darunter
  abgelehnt, darüber auf 100 begrenzt), höchstens alle 600 s; der Befehl zählt im Stundenbudget
  und ist nie dringend. Wie beim Strom prüft der Aktor das Limit im Tessie-Cache und meldet sonst
  „nicht übernommen“. Ändert jemand das Limit danach in der Tesla-App, ist das keine fremde
  Änderung: Der core lässt es bis zum nächsten Ansteckvorgang oder Ziel stehen. Ist das
  Stundenbudget bis auf die Reserve verbraucht, wartet der Befehl.
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
| `mode_pv`, `mode_min_pv`, `mode_now`, `mode_off`, `mode_planned` | PV-Überschuss, Min+PV, Sofort, Aus, Geplant | der Lademodus des Fahrzeugs |
| `boost` | Boost | Ein Boost gilt (er steht an Stelle des Modus). |
| `pv_surplus` | Überschuss | Der Wert kommt aus dem Anteil am Überschuss, oder die Zeiten des PV-Überschusses haben einen Start oder Stopp ergeben. |
| `now_limit` | Sofort-Limit | Das Sofort-Limit (Höchststrom des Autos, „Laderegelung“, „Lademodi“) senkt den Strom in „Sofort“; kappt ein Wächter darunter, steht dessen Grund; liegt ein Wächter zwischen Limit und vollem Strom, steht sein Grund danach. |
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
| `plan_power` | Plan: Laden | Im Modus „Geplant“ folgt der Strom der Ladeleistung, die der Plan für das Auto im laufenden Slot nennt. |
| `latest_start` | Späteste Startzeit | Im Modus „Geplant“ gibt es keinen gültigen Plan-Slot und die späteste Startzeit ist erreicht: Das Auto lädt mit voller Leistung bis zum Ziel. |
| `target_reached` | Ziel-SOC erreicht | Im Modus „Geplant“ hat das Auto den Ziel-SOC erreicht; die Regelung stoppt das Laden. |
| `charge_limit` | Ladelimit | Der Befehl setzt das Ladelimit des Tesla auf das Ziel (nur mit `charge_limit_sync`). |
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
| `plan_slot_ts` | nur mit gültigem Slot des Plans: Beginn des Slots in Sekunden seit 1970 (UTC) |
| `price_ct` | nur mit gültigem Slot, dessen Plan den Bezugspreis nennt: der Preis in ct/kWh |
| `budget_kw` | nur mit gültigem Slot und gesetztem Ziel des 15-min-Wächters: das Ziel in kW (Budget des Slots, sonst `control.peak_target_kw`) |

`null` heißt unbekannt. Die drei Eingaben des Plans tragen alle Entscheidungen der Regelung
(Ladepunkte und Override), solange ein Slot gilt; ohne gültigen Slot fehlen sie. Sicherer Zustand,
Übergabe und erkannte fremde Änderungen tragen sie nicht. `GET /v1/health` zeigt je Fahrzeug
(`vehicles`, in der Reihenfolge von `config.yaml`) Modus, Boost bis, Wallbox, Zuordnung, Grenze,
angesteckt, lädt, Weckvorgänge heute und den letzten Grund, seit Plan 4b dazu Ladestand, Ladelimit
und den Stand von „Geplant“ (unten, „Health für die Bedienung“). Je Wallbox (`wallboxes`) zeigt es, wer
dort steht, die Art der Zuordnung und die Indizien (oben, „Zuordnung Auto und Wallbox“).

### Health für die Bedienung

Neben den Feldern oben stehen in `GET /v1/health` diese, die die Bedienung braucht:

| Feld | Bedeutung |
|---|---|
| `intent_version` | die Version der Absicht, mit der die Regelung im letzten Zyklus regelte; `null`, solange sie ruht. Die app bereinigt die Absicht (Abschnitt 13, „Absicht und Regelbetrieb“) nur nach einem Health, dessen `intent_version` mindestens die gültige Version erreicht; sonst gilt der Stand als unbekannt. |
| `vehicles[].boost_pending` | `true`: Ein Boost der Absicht gilt, wartet aber auf einen Ansteckvorgang, der noch nicht begonnen hat. `boost_until` zeigt nur einen aktiven Boost. Die Seite „Laden“ zeigt „wartet auf Anstecken“. |
| `vehicles[].override_pending` | dasselbe für die Übersteuerung der Wallbox: Sie gilt, wartet aber auf einen Ansteckvorgang. `assignment` zeigt `override` erst, wenn sie wirkt. |
| `vehicles[].commands_hour` | die Befehle der letzten Stunde im gleitenden Fenster der Safety-Schicht, nur für Fahrzeuge mit Tessie, sonst `null` |
| `vehicles[].soc_pct` | der Ladestand des Fahrzeugs in %, mit dem die Regelung rechnet (seit Plan 4b, oben „Geplant“); `null` ohne Regelzyklus |
| `vehicles[].soc_source` | `measured` (gemessen), `estimated` (aus dem Zähler der Wallbox geschätzt) oder `assumed` (angenommen, dann ist `soc_pct` 0); `null` ohne Regelzyklus |
| `vehicles[].charge_limit_pct` | das Ladelimit im Auto in %, nur mit Tessie, sonst `null` |
| `vehicles[].planned` | nur in „Geplant“ an einer Wallbox, sonst `null`: `state` (`plan`, `surplus`, `due`, `reached`), `target_soc_pct` (das wirksame Ziel), `departure_at` (die nächste Abfahrt, UTC), `need_kwh` (Bedarf aus der Wallbox), `latest_start` (späteste Startzeit, UTC; `null` ohne Bedarf) und `plan_w` (die Leistung des gültigen Slots für das Auto; `null` ohne gültigen Slot und wenn der Slot das Auto nicht nennt). Die app nimmt daraus die geplanten Autos (Abschnitt „Planung“, „Autos im Plan“) und „Fertig um“ ohne Plan. |
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
- **Wall Connector bei offenem Schütz:** Die Klemmen messen dann nur Rauschen (im Prüfprotokoll
  17.1-9 rund 1,5 W bei 0,2 A und 7,4 V). Meldet der Wall Connector ausdrücklich `contactor_closed`
  `false`, setzt der core seine Leistung auf 0 W; Phasenströme und
  -spannungen bleiben die Messung, der Phasenwächter sieht sie weiter. Fehlt das Feld, rechnet der
  core wie bisher Strom mal Spannung. So zeigen Anzeige und Statistik im Leerlauf 0 W statt 1 W.
- **„Complete“ ohne Ladelimit (EVCS, Tesla):** Meldet die EVCS „geladen“, ohne dass das Ladelimit
  bekannt ist, behält der core das Limit, das Tessie im selben Ansteckvorgang schon gemeldet hat. Ein
  später höheres Limit für „Geplant“ hebt „geladen“ also weiterhin auf.

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

### Geplantes Laden ausrollen (Plan 4b)

Plan 4b ändert den core und die app; sie rollen **gemeinsam** aus (dieselben Image-Tags), denn die
Absicht ändert ihre Form. Ausgerollt wird im gemeinsamen Fenster nach dem 25.10.2026 (Entscheidung des Betreibers vom
22.10.2026, statt nach der Abnahme der Phase 3; Victron danach noch mindestens eine Woche im Dry-Run,
die 30 echten Tage laufen weiter) und erst, wenn Plan 4b auf `main` ist und die Images veröffentlicht sind; einen lokalen Build
machst du wie in Abschnitt 4 („Lokaler Build“). **Nichts geht dabei von selbst live:** Jeder Aktor
behält seine Betriebsart, und ein Fahrzeug lädt erst „Geplant“, wenn du es darauf stellst. Alle
anderen Modi verhalten sich wie vorher. Der Watchdog bleibt unverändert, eine neue `config.ini` ist
nicht nötig.

1. **ACL ergänzen:** In der ACL des Mosquitto-Benutzers `bindaems` die Zeile
   `topic write homeassistant/text/bindaems/#` eintragen (Abschnitt 7) und das Add-on neu starten. Ohne
   sie fehlt in Home Assistant die Entität „Abfahrtszeit“; alles andere geht. Seit Plan „Abfahrt als
   Uhrzeit“ ist die Abfahrtszeit eine `time`-Entität und braucht zusätzlich die Zeile
   `topic write homeassistant/time/bindaems/#` (Home Assistant ab 2026.5); die Zeile für `text` bleibt.
2. **Regelbetrieb aus:** im UI unter **System** → „Bedienung“ oder in Home Assistant. Der core übergibt
   die Aktoren und geht nach OBSERVE (Abschnitt 13); warte, bis die Übergabe fertig ist. Notiere dir die
   Einstellungen der Seite „Laden“ (für einen Rollback, Abschnitt 4).
3. **Datenbank sichern:** Die nächtliche Sicherung reicht als Rückfall nur bis zum Ausrollen. Kopiere die
   Datenbank der app unter einem Namen, den die Rotation nicht löscht: `docker compose stop ems-app`,
   dann
   `docker compose run --rm --no-deps --entrypoint sh ems-app -c 'cp /data/bindaems.sqlite3 /backup/vor-plan-4b.sqlite3'`.
4. **Ausrollen:** `cd /opt/bindaems && docker compose pull && docker compose up -d`. core und app
   starten neu. Die Migration `0012` (Tabelle `planned_charge`) läuft beim Start der app von selbst und
   ändert keine vorhandene Tabelle. Die gespeicherte Absicht lädt mit Ziel 80 % und Abfahrt 07:00 für
   jedes Fahrzeug, der gespeicherte Plan ohne Ladeleistung der Autos.
5. **`soc_entity` prüfen:** Den Ladestand des e-Golf abonniert der core jetzt selbst über
   `vehicles.egolf.soc_entity` (Abschnitt „Zuordnung Auto und Wallbox“). Den manuellen Eintrag
   `vehicle.egolf.soc_pct` unter `homeassistant.entities` kannst du löschen; steht dort eine andere
   Entität als in `soc_entity`, startet der core nicht und nennt den Grund. Nach einer Änderung der
   `config.yaml` startest du core und app neu (Abschnitt 13, „Wartung“).
6. **Optional `charge_limit_sync`:** Soll der core das Ladelimit des Tesla in „Geplant“ an das Ziel
   angleichen, trägst du unter `vehicles.tesla` `charge_limit_sync: true` ein (oben, „Tesla“). Ohne den
   Schlüssel bleibt das Limit, wie es im Auto steht.
7. **Regelbetrieb an:** wieder unter **System** → „Bedienung“. Auf der Seite **Laden** steht beim Fahrzeug
   der Ladestand (beim e-Golf oft „geschätzt“), die Auswahl des Lademodus kennt „Geplant“; in Home
   Assistant gibt es je Fahrzeug „Ziel-SOC“, „Abfahrtszeit“, „Fertig um“ und „Ziel erreichbar“ und am
   Gerät „BindaEMS“ die Warnung „Ziel-SOC unerreichbar“.
8. **Einen Abend „Geplant“ im Dry-Run beobachten:** Ein Auto anstecken, auf „Geplant“ stellen und Ziel
   und Abfahrt setzen (etwa 80 %, 07:00). Dann zeigen
   - die Seite **Zeitplan** das Diagramm „Autos“ mit der geplanten Ladeleistung und im Kopf „Fertig um
     {HH:MM} · Ziel {soc} % bis {HH:MM}“, der Anlass des Plans ist „Fahrzeug geändert“;
   - die Seite **Laden** dieselbe Zeile an der Fahrzeugkarte;
   - die **Entscheidungen** mit „Geplant“ und „Plan: Laden“ in den Slots, in denen der Plan lädt (im
     Dry-Run „nicht gesendet“), ohne Plan „Späteste Startzeit“ und am Ziel „Ziel-SOC erreicht“;
   - am nächsten Tag die Karte „Geplantes Laden“ der **Statistik** die Ladung.

   Im Dry-Run regelt weiter evcc: Ob das Auto am Morgen das Ziel hat, hängt dann an evcc, nicht am Plan.
   Ein Befund ist, wenn der Plan oder die Entscheidungen nicht zu Ziel, Abfahrt und Preisen passen.

Die Abnahme der Phase 4 prüft danach „Ziel-SOC“ und „HA in beide Richtungen“
(`docs/verification/abnahme-phase-4.md`, Abschnitte 4 und 5).

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
| `control.peak_target_kw` | keiner | Ziel des 15-min-Wächters in kW; ein Budget des Plans ersetzt es, ohne beides ruht er. |
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
| Sofort, Geplant, Aus | Nichts. Sofort lädt aus Netz und PV mit vollem Strom; was einem Auto in Geplant fehlt, kommt aus dem Netz, denn über das Netzladen des Autos entscheidet der Plan. |
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
  - „Akku-Unterstützung erlaubt“ in der Absicht an ist (`support.allowed`, sonst `off`),
  - der gültige Slot des Plans nicht `HALTEN` oder `NETZLADEN` will (sonst `plan`): Dann gibt der
    Akku keinem Auto etwas, auch nicht in Boost, und jedes Auto lädt ganz aus dem Netz,
  - der SOC die wirksame Schwelle erreicht hat und seitdem nicht unter Schwelle minus
    `support_hysteresis_pct` (5 %) gefallen ist (sonst `soc`); ein unbekannter SOC sperrt sie im
    Zyklus, ändert den Stand der Hysterese aber nicht, und
  - die Überbrückung noch keine `support_max_s` (900 s) dauert (sonst `time`).
- **Wirksame Schwelle:** die Unterstützungsschwelle der Absicht (`support.soc_pct`), ohne Angabe
  `support_soc_pct` (70 %); liegt die Plan-Schwelle des gültigen Slots höher, gilt sie. Die
  Hysterese folgt der Schwelle jedes Zyklus: Steigt sie, gilt das neue Band sofort. Health zeigt sie
  als `battery.support_soc_pct`, ihre Quelle als `battery.support_source`.
- **Überbrückung:** Sie beginnt mit dem ersten Zyklus, in dem die Autos in PV-Überschuss und Min+PV
  zusammen mindestens 100 W Fehlbetrag haben (die Hälfte des Totbands), und endet nach 60 s ohne.
  Nach `support_max_s` ist die Unterstützung gesperrt, bis sie endet. In PV-Überschuss senkt die
  Laderegelung dann ohnehin auf den echten Überschuss, in Min+PV kommt der Mindeststrom aus dem Netz.
- **Unter der Schwelle** (SOC unter der wirksamen Schwelle, ohne Plan und Einstellung 70 %) holt der
  core den Fehlbetrag aus dem Netz: Der Akku bleibt aus dem Auto heraus, etwa in den Lücken unter
  Wolken (im Simulator, `akku-wolkig`: 0,06 % der Energie des e-Golf kommen aus dem Akku).
- **Nach einer Pause** der Regelung (Regelbetrieb aus, SAFE, OBSERVE) beginnt der Stand neu: Der SOC
  muss die wirksame Schwelle wieder erreichen.
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
- **Absicht des Plans** (seit Plan 3c): Gilt ein Slot des Plans (Abschnitt „Planung“), bestimmt seine
  Absicht für den Akku den gewollten Netzbezug über dem Basis-Sollwert:
  - **`EIGENVERBRAUCH`:** wie ohne Plan, die Regeln dieses Abschnitts.
  - **`HALTEN`:** Hauslast plus Autos minus PV minus Basis-Sollwert, nie unter 0. Der Akku gibt
    nichts ab, auch nicht an ein Auto in Boost: Boost lädt dann aus dem Netz. Ein PV-Überschuss lädt
    den Akku weiter.
  - **`NETZLADEN`:** dasselbe plus die Netzladeleistung des Slots.

  Budget des Wächters, Raster von 10 W, Totband, Auffrischen, Sperren und Regel 2 gelten für alle
  drei wie oben. Wegen des Totbands (`control.setpoint_deadband_w`, 200 W) folgt der Override dem
  Bedarf nicht genau: Ohne laufenden Override entsteht unter 200 W gewolltem Netzbezug keiner, ein
  laufender fällt erst unter 100 W weg und ändert sich erst ab 200 W Abstand. So entlädt der Akku
  auch bei `HALTEN` etwas, höchstens rund 0,05 kWh je Viertelstunde (200 W für 15 min). Wollen Haus
  und Autos bei `HALTEN` mehr als `victron.max_grid_charge_setpoint_w` (8000 W), etwa ein Auto in
  Sofort mit 11 kW, kappt Regel 2 den Override, und den Rest liefert der Akku. Ohne Basis-Sollwert ist
  das Ziel bei `HALTEN` und `NETZLADEN` unbekannt.

### 15-min-Wächter

Der Wächter misst den Netzbezug jeder Viertelstunde (:00, :15, :30, :45, Wiener Zeit), rechnet ihn
hoch und hält ein Ziel, wenn eins eingestellt ist (Spec 7.4). Er verhindert keine Spitze um jeden
Preis: Er drosselt die Wallboxen und lässt den Akku überbrücken, und was beides nicht hält, meldet er.

- **Ziel einstellen und abschalten:** Das Ziel ist `control.peak_target_kw`, solange der gültige Slot
  des Plans kein Budget nennt (unten). Ohne beides (Standard ohne Leistungspreis) ruht der Wächter
  ganz (Health `peak.state` `off`). Zum Einschalten trägst du das Ziel in `config.yaml` ein und
  startest den core neu; nimmst du die Zeile wieder heraus und startest neu, ruht er nur, solange
  kein Plan ein Budget bringt. Gilt im Monat ein Leistungspreis mit Satz, bringt jeder Plan eines,
  und der Wächter hält es, solange ein Plan gilt.

  ```yaml
  control:
    peak_target_kw: 8   # Ziel des 15-min-Wächters in kW; ohne Wert gilt nur das Budget eines Plans
  ```

  Das Ziel ist das Budget `B`. Gilt ein Plan, ersetzt das Budget seines Slots das Ziel aus der
  Konfiguration: Der Wächter nimmt in jedem Zyklus das Budget des gültigen Slots, wenn der Plan eins
  nennt, sonst `control.peak_target_kw`. Auch ohne dieses Ziel hält er das Budget eines Slots; ohne
  beides ruht er. Health `peak.target_kw` und der Stand `off` folgen dem Ziel des letzten Zyklus,
  und `P_zul` ändert sich mit dem Ziel auch mitten in einer Viertelstunde. Das Budget des Plans ist
  die Monatsspitze, die der Plan akzeptiert (Abschnitt „Planung“, „Budget“); eine Überschreitung
  nimmt der Wächter hin und warnt, und der nächste Plan sieht die neue Monatsspitze (Auslöser
  `month_peak`) und hebt das Budget. Läuft ein Plan, der allein das Ziel brachte (ohne
  `control.peak_target_kw`), mitten in einer Viertelstunde ab, ruht der Wächter sofort: Seine Grenzen
  und ein Alarm `peak.exceeded` fallen weg, und die Autos laden im Rest der Viertelstunde ohne Ziel.
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
  dem Basis-Sollwert und `W`, dem gewollten Netzbezug der Autos vor dem Wächter (bei `HALTEN` und
  `NETZLADEN` der des Plans, oben unter „Absicht des Plans“). `W` ist nötig, weil
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
     Min+PV, dann Geplant und zuletzt Sofort, diese drei höchstens bis zur Mindestleistung des
     Ladepunkts (`min_a × U × Phasen`); innerhalb einer Stufe zuerst das Auto mit der niedrigsten
     Priorität. Ein Auto ohne Netzanteil, dessen Leistung die PV deckt, wird nicht gedrosselt.
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
     Änderung und je Zyklus höchstens eine, in umgekehrter Reihenfolge (Sofort vor Geplant vor
     Min+PV vor PV-Überschuss, innerhalb einer Stufe die höchste Priorität zuerst). Wollen die Autos
     mit Budget mehr als die feste Grenze von Regel 2, hält der Override den Netzbezug bei ihr,
     sobald `P_zul` über ihr liegt, und das Mittel zählt mit ihr: Eine Grenze fällt dann, wenn
     `P_zul` die feste Grenze um 1 kW plus die Senkung dieses Autos übersteigt (im Simulator,
     `spitze-15min`, bei rund 9,3 kW). So flattert der Tesla nicht und spart Tessie-Befehle. Mit
     dem Ende der Viertelstunde fallen alle Grenzen weg.
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
  Min+PV, Geplant oder Sofort, dessen Mindestleistung über `P_zul − Mittel` liegt. Die Laderegelung
  behandelt das wie eine Grenze, die den Mindeststrom nicht trägt; der Start wartet auf die nächste
  Viertelstunde. Ein Auto, das lädt, betrifft das nicht.
- **Boost** hebt das Ziel für sein Auto auf (bis zum Abstecken, höchstens 12 h): Der Netzanteil seiner
  Wallbox (mit PV und Akku zuerst für Boost, oben, „Messung“) zählt weder in die Energie bisher noch
  ins Mittel, und der Wächter drosselt das Auto nie.
  Der Zähler am Netz und die Viertelstunde der Abrechnung sehen den Boost trotzdem. Was er höchstens
  an Leistungspreis kostet, zeigt die Seite „Laden“ (Abschnitt „Laderegelung“, „Lademodi“, Boost).
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
    "refresh_age_s": 10.0,
    "support_soc_pct": 70.0,
    "support_source": "config"
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
| `battery.support` | Stand der Unterstützung: `on` (gilt), `mode` (kein Auto in PV-Überschuss oder Min+PV), `off` („Akku-Unterstützung erlaubt“ ist aus), `plan` (der Plan will `HALTEN` oder `NETZLADEN`), `soc` (SOC unter der wirksamen Schwelle oder unbekannt), `time` (Überbrückung länger als `support_max_s`). Gilt sie nicht, steht der erste zutreffende Grund in der Reihenfolge `mode`, `off`, `plan`, `soc`, `time`. |
| `battery.bridging_s` | Sekunden der laufenden Überbrückung; `null` ohne |
| `battery.support_soc_pct` | die wirksame Schwelle der Unterstützung in % (oben, „Wirksame Schwelle“); `null` ohne Zyklus |
| `battery.support_source` | woher sie kommt: `config` (`support_soc_pct`), `intent` (Unterstützungsschwelle der Absicht), `plan` (Plan-Schwelle des gültigen Slots, höher als die eigene); `null` ohne Zyklus |
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

Seit Plan 3c zeigt `GET /v1/health` unter `plan` den Plan der Absicht (Abschnitt „Planung“, „Übergabe
an den core“); was fehlt, ist `null`:

| Feld | Bedeutung |
|---|---|
| `plan.id` | die Nummer des Plans; `null` ohne Plan |
| `plan.state` | `none`: kein Plan in der Absicht. `valid`: Der Plan gilt auf der Uhr des core. `expired`: Er gilt nicht (abgelaufen oder, bei einer Abweichung der Uhren, noch nicht gültig) und wirkt nicht. |
| `plan.valid_until` | bis wann der Plan gilt, ISO-Zeit in UTC |
| `plan.slot` | Beginn des Slots, der jetzt bindet, ISO-Zeit in UTC; `null`, wenn keiner bindet, auch wenn die Regelung ruht |
| `plan.intent` | seine Absicht: `EIGENVERBRAUCH`, `HALTEN` oder `NETZLADEN` |
| `plan.grid_charge_w` | seine Netzladeleistung in W (0 außer bei `NETZLADEN`) |
| `plan.budget_kw` | sein Budget in kW; `null`, wenn der Plan keins nennt |
| `plan.support_soc_pct` | seine Plan-Schwelle in % (oben, „Wirksame Schwelle“) |
| `plan.price_ct` | sein Bezugspreis in ct/kWh |

### Akku-Unterstützung bedienen

„Akku-Unterstützung erlaubt“ und die Unterstützungsschwelle gehören zur Absicht wie der Gast-Modus.
Im UI (Seite „Laden“, Karte „Akku-Unterstützung“ mit Schalter, Zahlfeld und „Übernehmen“; ein leeres
Zahlfeld heißt „Schwelle der Konfiguration“), in Home Assistant und per API stellen das „Bedienen“ und
Admin ein. Die Karte nennt dazu die wirksame Schwelle und ihre Quelle („Wirksam ab 82 % (Plan).“ mit
„Einstellung“, „Konfiguration“ oder „Plan“); Lesende sehen die Werte ohne Bedienung. Eine
Änderung behält den Plan und steht im Änderungsprotokoll mit der Aktion `control.support`, dem Ziel
„Akku-Unterstützung“ und dem Wert als Text: „erlaubt, ab 60 %“, „erlaubt, Schwelle aus der
Konfiguration“ oder „gesperrt“. Standard ist erlaubt mit `control.support_soc_pct` (70 %).

Die Schwelle liegt zwischen der USV-Reserve (`battery.reserve_soc_pct`) und 100 %. Darunter lehnt die
app die Änderung ab („Unterstützungsschwelle 15 % liegt unter der USV-Reserve (20 %).“). Der core hebt
eine Schwelle unter der Reserve auf die Reserve und nennt das als Hinweis („Unterstützungsschwelle
15 % auf die USV-Reserve (20 %) angehoben.“), statt die Absicht abzulehnen: etwa eine Absicht per
`curl` oder eine gespeicherte Schwelle, nachdem die Reserve in `config.yaml` gestiegen ist. So geht
jede andere Bedienung weiter durch. Die Planung rechnet mit der Schwelle der Absicht, sobald sie
gilt.

| Objekt-ID | Name | Plattform | Wert |
|---|---|---|---|
| `battery_support` | Akku-Unterstützung erlaubt | switch | `ON`, wenn die Unterstützung erlaubt ist; ein Befehl ändert nur die Erlaubnis, die Schwelle bleibt; schreibend |
| `support_soc` | Unterstützungsschwelle | number | die Schwelle in %: der Wert der Absicht, ohne Angabe `control.support_soc_pct`; Eingabefeld von der USV-Reserve (20 im Beispiel) bis 100 in Schritten von 1; schreibend |

Ein Wert außerhalb des Bereichs oder keine Zahl wird abgelehnt und ändert nichts; Home Assistant meldet
dann „Unterstützungsschwelle „150“ liegt außerhalb von 20–100 %.“. Die Zahl behält den Schalter, wie
der Schalter die Zahl behält.

Per API (ab „Bedienen“, Anmeldung und `X-CSRF-Token` wie in Abschnitt 8): `PUT /api/control/support`
mit `{"allowed": true, "soc_pct": 60}`; `soc_pct` `null` heißt „Schwelle aus der Konfiguration“. Die
Antwort ist die der übrigen Bedienungen (`result`, `version`, `details`, `intent`).

### Gründe

Das Entscheidungslog (Abschnitt 13) nennt bei Befehlen des Akku-Sollwerts und des Wächters diese
Gründe in `codes`, vor den Befunden der Safety-Schicht:

| Grund (`codes`) | Text im Log | Bedeutung |
|---|---|---|
| `battery_comp` | Akku nicht ins Auto | Der Override verschiebt den Fehlbetrag der Autos aufs Netz. |
| `battery_support` | Akku-Unterstützung | Nur der Teil über dem erlaubten Akku-Anteil geht aufs Netz. |
| `peak_guard` | 15-min-Wächter | Der Befehl folgt aus dem 15-min-Wächter: Beim Override hat sein Budget den gewollten Netzbezug gesenkt, bei einer Wallbox gilt seine Leistungsgrenze an Stelle der des Phasenwächters. |
| `plan_hold` | Plan: Halten | Der Slot des Plans will `HALTEN`: Haus und Autos kommen aus dem Netz, soweit die PV sie nicht deckt. |
| `plan_grid_charge` | Plan: Netzladen | Der Slot des Plans will `NETZLADEN`: dazu die Netzladeleistung des Slots aus dem Netz. |

- `plan_hold` steht bei `HALTEN`, `plan_grid_charge` bei `NETZLADEN`. Sonst steht
  `battery_support`, wenn der Akku zusammen etwas liefern darf, und `battery_comp`. Dahinter folgt
  `peak_guard`, wenn das Budget des Wächters den gewollten Netzbezug gesenkt hat.
- Ein Beispiel: `Victron (ESS) (Akku nicht ins Auto): Netz-Sollwert 2340 W – Dry-Run, nicht
  gesendet.` Hebt der Akku-Sollwert den Override auf, steht dort „Netz-Sollwert-Override aufheben“.
- Zu den Eingaben einer Entscheidung des Overrides gehören die der Laderegelung (`soc_pct`,
  `battery_w`, `grid_l1_a` bis `grid_l3_a`) und diese in W: `pv_w` und `house_w` (PV und Hauslast),
  `cars_w` (Ladeleistung der Autos), `deficit_w` (Fehlbetrag zusammen), `allowed_w` (erlaubter
  Akku-Anteil zusammen), `target_w` (der gewollte Netzbezug nach dem Wächter) und `actual_setpoint_w`
  (der Override laut Cerbo; im Dry-Run unter ESS „kein Override“, also `null`). Gilt ein Slot des
  Plans, kommt `plan_grid_charge_w` dazu (die Netzladeleistung bei `NETZLADEN`, sonst 0), außerdem
  `plan_slot_ts`, `price_ct` und `budget_kw` wie in der Tabelle oben.

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

Seit Plan 3c führt der core im Regelbetrieb auch den Plan der app aus (oben, „Absicht des Plans“). Vor
der Live-Schaltung läuft die Planausführung **2 Wochen im Dry-Run**: Der core rechnet den Plan mit
und protokolliert, was er an Victron senden würde, ohne etwas zu senden. Die Schritte:

1. **Images ausrollen** (`docker compose pull && docker compose up -d`, Abschnitt 4): core und app
   starten neu, der core mit der gespeicherten Absicht (er durchläuft OBSERVE und das Gate). Die neuen
   Felder haben Standardwerte (`deploy/config.example.yaml`); der Watchdog bleibt unverändert (1.1.0),
   und keines der neuen Felder verlangt eine neue `config.ini`. Schalte den Regelbetrieb ein (Karte
   „Bedienung“ unter **System** oder Seite „Laden“; ohne app eine Absicht mit `"control": true`,
   Abschnitt „Laderegelung“): Victron bleibt im Dry-Run, solange die Absicht ihn nicht live verlangt.
2. **DESS aus für den Dry-Run,** für die ganzen 2 Wochen. Solange DESS an ist, ist Victron gesperrt:
   Der Akku-Sollwert sendet auch im Dry-Run nichts, Health zeigt `battery.state` `locked`, und der
   Stand der Ausführung ist „Victron ist gesperrt“. Der Dry-Run des Victron-Teils braucht deshalb
   DESS aus. Der Akku läuft in dieser Zeit ohne Preisoptimierung (Eigenverbrauch des ESS), denn im
   Dry-Run stellt auch der Plan nichts. Modus lesen, abschalten und zurücksetzen wie in Abschnitt 11,
   „Victron und Dynamic ESS“.
3. **Prüfen, dass der Plan beim core liegt:** Unter **System** meldet die Komponente `planning` „Plan
   von HH:MM Uhr“, der core steht in `CONTROL` (oder `DEGRADED`), und Victron ist im Dry-Run ohne
   Sperre. Die oberste Zeile der Seite **Zeitplan** lautet „Dry-Run: Der core rechnet den Plan mit und
   sendet nichts an Victron.“ (Abschnitt „Planung“, „Stand der Ausführung“); ein anderer Satz nennt,
   was fehlt. Im core zeigt Health den Plan (`plan.state` `valid`, `plan.intent` und `plan.slot` des
   laufenden Slots, oben unter „Health“).
4. **2 Wochen beobachten** (Abfrage unten):
   - die Entscheidungen an `victron` mit `plan_hold` („Plan: Halten“) und `plan_grid_charge` („Plan:
     Netzladen“; nur mit eingeschaltetem „Netzladen erlaubt“): Stimmen Zeit und Absicht mit dem
     „Ablauf“ des Plans überein, und liegt `requested` bei `HALTEN` nahe Hauslast plus Autos minus PV
     und bei `NETZLADEN` die Netzladeleistung darüber?
   - ihre Zahl je Stunde: Bei ruhiger Last sind es wenige, im Simulator (`plan-halten`) höchstens 40
     je Stunde; deutlich mehr heißt, dass das Rauschen der Hauslast größer ist als das Totband
     (unten, „Hinweise für die Anlage“).
   - die Karte „Plan gegen Ist (heute)“ auf der Seite **Zeitplan** (Abschnitt „Planung“, „Plan gegen
     Ist“): Im Dry-Run zeigt sie, wie gut Prognosen und Akkumodell passen.
   - die Punkte unter „Hinweise für die Anlage“ (unten), auch ohne Plan.
5. **Danach Victron live schalten,** mit den vier Bedingungen aus Spec 8.7: `victron.live_allowed`
   (Schritt 7), Prüfprotokoll Teil 2 bestanden (Schritt 6), ein scharfer Watchdog (die Probe in
   Schritt 8; scharf schaltet ihn der core selbst) und die Umschaltung durch einen Admin im UI mit
   Bestätigung (Schritt 9). Ab dann stellt der Plan den Akku; der Stand der Ausführung lautet „Der
   core führt den Plan aus.“

Die Entscheidungen an `victron` (Art `grid_setpoint`) zeigen in `requested`, was BindaEMS einstellen
würde, und in `inputs.actual_setpoint_w` den Override am Cerbo (unter ESS im Dry-Run `null`). Mit
gültigem Slot tragen sie `plan_slot_ts`, `plan_grid_charge_w`, `price_ct` und `budget_kw` (oben,
„Gründe“). `SINCE` ist wie in „Dry-Run-Vergleich mit evcc“ gesetzt:

```bash
curl -s -H "Authorization: Bearer $BINDAEMS_INTERNAL_TOKEN" \
  "http://127.0.0.1:8081/v1/decisions?since=$SINCE&limit=5000" \
  | jq -r '.decisions[] | select(.actuator == "victron" and .dry_run) | [.ts, .result, .requested,
      .inputs.actual_setpoint_w, .inputs.deficit_w, .inputs.allowed_w, (.codes | join(","))] | @tsv'
```

Die Zahl der Entscheidungen des Plans je Stunde (UTC) zählt diese Abfrage; `more` in der Antwort
sagt, ob weitere folgen (Abschnitt 13, „Entscheidungslog“):

```bash
curl -s -H "Authorization: Bearer $BINDAEMS_INTERNAL_TOKEN" \
  "http://127.0.0.1:8081/v1/decisions?since=$SINCE&limit=5000" \
  | jq -r '.decisions[] | select(.actuator == "victron"
      and any(.codes[]; . == "plan_hold" or . == "plan_grid_charge")) | .ts[0:13]' \
  | sort | uniq -c
```

Die Live-Schaltung danach:

6. **Prüfprotokoll Teil 2, Victron 17.2-4 „Kein Akku ins Auto“** bestehen (Abschnitt 11). Es prüft
   genau den Mechanismus dieses Abschnitts und ist noch offen. Der Punkt `victron` verlangt DESS aus,
   den Multi auf „Ein“ und ein Auto, das an der EVCS lädt (über 1000 W).
7. **`victron.live_allowed: true`** in `config.yaml` (Abschnitt 13, „Dry-Run und live“), den core neu
   starten, dann die `config.ini` neu erzeugen und mit `install.sh` übertragen (Abschnitt 14,
   „config.ini erzeugen“ und „Installieren“). Die Freigabe allein schaltet Victron nicht live, das
   tut erst die Absicht (Schritt 9). Mit ihr enthält die `config.ini` aber die Aktion, die bei einer
   Auslösung den Override aufhebt, und die Probe in Schritt 8 prüft sie nur dann. Design 2c-2 (11.1)
   nennt die Freigabe nach der Probe; die Reihenfolge hier folgt dieser Abhängigkeit.
8. **Watchdog-Probe mit vorher verstellten Victron-Werten** (Abschnitt 14, „Probe“). Die Probe
   verstellt nichts selbst, und der Watchdog schreibt nur, was vom Sicherwert abweicht. Stelle
   deshalb Min-SOC, Entladegrenze und Override vorher von Hand auf Werte neben dem Sicherwert (Min-SOC
   etwas über die USV-Reserve, die Entladegrenze auf einen endlichen Wert, den Override auf eine
   Zahl), notiere die vorgefundenen Werte und prüfe nach der Probe, dass die Auslösung sie
   zurückgesetzt hat: Min-SOC = USV-Reserve, Entladegrenze −1 (unbegrenzt), Override aufgehoben. Die
   Pfade am Cerbo sind `com.victronenergy.settings` mit `/Settings/CGwacs/BatteryLife/MinimumSocLimit`
   und `/Settings/CGwacs/MaxDischargePower` sowie `com.victronenergy.hub4` mit
   `/Overrides/Setpoint` (`dbus -y … GetValue` und `SetValue`, wie in Abschnitt 11). Danach stellst
   du die Werte ein, wie du sie haben willst.
9. **Victron live schalten:** im UI mit dem Dialog „Live schalten“ (**System** → „Bedienung“, als Admin;
   wähle nur Victron, die Bestätigung „evcc ist ganz abgeschaltet.“ entfällt dafür). Ohne app verlangst
   du es in der Absicht per `PUT /v1/intent`. Sie ist vollständig (Abschnitt „Absicht per
   PUT /v1/intent“); dieses Beispiel lässt EVCS und Tesla im Dry-Run:

   ```json
   {"version": 14, "control": true, "actuators": {"victron": "live"}}
   ```

   Live läuft Victron nur mit scharfem Watchdog und ohne Sperre, wie jeder Aktor; den Watchdog
   schärft der core selbst (Abschnitt 13, „Gate“).
10. **Danach:** DESS bleibt aus. Schaltest du es wieder ein, sperrt sich Victron, und der Override
   verfällt nach 300 s. Der Plan wirkt dann nicht mehr; der Stand der Ausführung lautet „Victron ist
   gesperrt“. Zurück in den Dry-Run geht es wie in Abschnitt 13, „Live-Freigabe
   zurücknehmen“: Der core übergibt Victron und hebt den Override auf.

### Alarme

Beide Alarme zeigt das UI unter **System** → „core“ → „Alarme“ (Abschnitt 14, „Meldungen des core“).

| Alarm | Stufe | Bedeutung und Abhilfe |
|---|---|---|
| `control.exception`: Regelung ausgesetzt: Ausnahme {Typ} im Regelzyklus. | Fehler | Ein Aufruf der Regelung hat eine Ausnahme geworfen; {Typ} ist ihre Art. Das ist ein Fehler im core, kein Bedienfehler. Das Log (`docker compose logs ems-core`) nennt die erste Ausnahme einer Folge mit Traceback: Sichere es und melde den Fehler. |
| `peak.exceeded`: 15-min-Ziel überschritten: {x} kW in der Viertelstunde bis {HH:MM}. | Warnung | Die Hochrechnung lag 60 s lang über dem Ziel, und weder die Drosselung der Wallboxen noch der Akku halten es. Meist ist es kein Fehler: Prüfe, was den Netzbezug getrieben hat. Kam das Ziel aus `control.peak_target_kw`, hebe es an oder nimm es heraus; das Budget eines Plans hebt der nächste Plan von selbst (Auslöser `month_peak`). |

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

## Prognosen

Seit Plan 3a rechnet die app zwei Prognosen, bewertet sie und schätzt fehlende Preise. Das schaltet
nichts an der Anlage: Die Prognosen sind Anzeige und Eingangsdaten für die Planung (Plan 3b). Sie
liegen im Speicher der app, jede Ausgabe geht zusätzlich in ein Archiv in SQLite
(`forecast_record`) und nach InfluxDB (Messung `forecast`, Tags `kind` = `pv` oder `load` und
`quantile` = `p25`, `p50`, `p75`, Feld `kw`, Retention-Policy `long`). Die Prognosegüte steht in
`forecast_metric`. Beide Tabellen legt die Migration `0008` beim Start der app an.

### PV-Prognose mit Kalibrierung

Die PV-Prognose holt die app von Open-Meteo, je Fläche, und rechnet sie mit dem PV-Modell in
Leistung um: stündlich zur Minute 7, beim Start sofort, nach einem Fehler spätestens nach 10 min.
Ist Open-Meteo kurz nicht verfügbar (HTTP 5xx, kein Netz), holt die app dieselbe Fläche nach 30 s
noch bis zu zweimal; Flächen, die schon geklappt haben, behält sie bis zu 30 min, die Wiederholung
holt nur die fehlenden. Ein Limit (HTTP 429) wiederholt sie nicht sofort. Den Grund, den Open-Meteo
mitschickt (z. B. „Daily API request limit exceeded“), zeigt die Karte „System“ bei der
Komponente „forecast“.
Sie unterscheidet nach der **Klarheit** des Himmels: `k = 1 − diffuse / globale Strahlung`;
ab 0,6 ist es `clear`, ab 0,3 `mixed`, sonst `overcast`. Unter 50 W/m² global (Nacht, Dämmerung)
gibt es keine Klasse.

- **Kalibrierung:** Die app vergleicht die Prognose mit dem, was die PV wirklich geliefert hat
  (Abrechnung, nur vollständige Viertelstunden). Je lokaler Stunde und Klarheitsklasse gilt der
  Median von `Ist / Prognose` über die letzten 30 Tage, jeweils für die Ausgaben bis 6 h vor dem
  Slot. Mindestens 8 Paare je Stunde und Klasse; sonst gilt die Stunde über alle Klassen
  (mindestens 8), sonst der Faktor 1,0. Jeder Faktor liegt zwischen 0,5 und 1,5. Paare mit
  weniger als `max(100 W, 2 % der Summe der kWp)` Prognose zählen nicht, denn in der Dämmerung
  verfälscht das Verhältnis den Faktor. Die Kalibrierung gilt für P50, begrenzt auf 0 bis
  `pv.inverter_ac_max_w`.
- **Band P25 bis P75:** Je Vorlaufklasse (bis 6 h, 6 bis 24 h, über 24 h) und Klarheitsklasse
  bestimmt die app aus den Abweichungen der letzten 30 Tage das 25. und 75. Perzentil von
  `Ist / P50` (mindestens 20 Paare; sonst über die Vorlaufklasse allein, sonst die Faktoren 0,7
  und 1,2). Daraus entstehen P25 und P75; es gilt immer P25 ≤ P50 ≤ P75, und P75 geht nie über
  `pv.inverter_ac_max_w`.
- **Wann:** Faktoren und Band werden beim Start und jede Nacht um 02:30 Uhr neu gerechnet. Die
  Karte „System“ zeigt bei der Komponente „forecast“ „PV-Prognose von HH:MM Uhr“, nach der
  Kalibrierung mit dem Zusatz „, kalibriert aus n Viertelstunden“.
- **Start:** Die erste PV-Prognose nach dem Start wartet bis zu 60 s auf die erste Kalibrierung.
  Sonst läge die erste Ausgabe mit dem neutralen Faktor 1,0 im Archiv und verfälschte später die
  Kalibrierung. Dauert die Kalibrierung länger, beginnt der Abruf ohne sie (Warnung im Log).

### Lastprognose

Die Lastprognose v1 ist ein **Profil der Hauslast**, das ist der Gesamtverbrauch ohne beide
Wallboxen und ohne Akkuladung:

- **Profil:** je Tagestyp und lokaler Uhrzeit (Viertelstunde) der gewichtete Median der letzten
  56 Tage. Das Gewicht einer Stichprobe halbiert sich alle 14 Tage. Der Tagestyp ist Werktag,
  Samstag oder Sonntag; Sonntage und die bundesweiten Feiertage in Österreich zählen als
  Sonntag. Maßgeblich ist das lokale Datum (Europe/Vienna). Fehlen für einen Tagestyp und eine
  Uhrzeit mindestens 3 Stichproben, gilt dieselbe Uhrzeit über alle Tagestypen, dann der Median
  aller Stichproben. Ohne Stichprobe gibt es keine Prognose.
- **Ausgabe:** stündlich zur Minute 9 für die Viertelstunden vom laufenden Slot bis zum Ende des
  übernächsten Tages; außer der Reihe gleich, wenn das Lastmodell aktiv wird oder nicht mehr
  (Abschnitt „Lastmodell (Phase 5a)“, „Aktivierung“). Das Profil baut die app beim Start und beim ersten Lauf eines neuen Tages
  neu. Bei einer Zeitumstellung bleibt die Zuordnung über die lokale Uhrzeit; die doppelte Stunde
  im Oktober hat in beiden Durchläufen den Wert des Profils.
- **Quellen:** Die Hauslast kommt aus der Abrechnung (`ledger_slot`: Flüsse `*>house`), nur aus
  vollständigen Viertelstunden. Sie beginnt mit Phase 1b; das Profil hat die vollen 56 Tage erst
  nach und nach. Für Zeiten ohne vollständigen Abrechnungs-Slot kann die app die
  **HA-Datenbank** lesen, wenn du in `config.yaml` die Entität der Hauslast nennst:

```yaml
homeassistant:
  load_history:
    entity: sensor.hausverbrauch          # Leistung des Hauses in W oder kW
    subtract: [sensor.wallbox_leistung]   # Leistungen, die nicht zur Hauslast zählen
influxdb:
  ha_database: homeassistant              # verlangt: wie bei den Verbrauchern aus HA
```

  `entity` ist die Gesamtlast des Hauses; unter `subtract` stehen die Entitäten, die davon
  abzuziehen sind (hier die beiden Wallboxen, sonst zählte das Autoladen als Hauslast). Die app
  bildet daraus je Viertelstunde das Mittel der Entität (aus Minutenmitteln) minus die Mittel der
  abzuziehenden Entitäten, mindestens 0. Fehlt der Wert einer abzuziehenden Entität, fällt die
  Viertelstunde weg. Ohne `influxdb.ha_database` lehnt die app die Konfiguration beim Start ab.
  Beide Schemata der HA-Integration gehen wie bei den Verbrauchern (Abschnitt 12, „Verbraucher aus
  Home Assistant“). Nach einer Änderung startest du die app neu.

Seit Plan 5a kann statt des Profils ein Modell die Lastprognose liefern (Abschnitt „Lastmodell
(Phase 5a)“); das Profil bleibt der Rückfall und die Vergleichsgröße.

Die Seite **System** zeigt die Karte „Lastprognose“ mit „Stand“ (Zeit der letzten Ausgabe),
„Datenbasis“ („n Viertelstunden aus BindaEMS, m aus Home Assistant“) und der Energie von heute,
morgen und übermorgen in kWh. Die Komponente „load-forecast“ meldet „Lastprognose von HH:MM Uhr“,
„Noch keine Daten für die Lastprognose“ (neue Installation ohne Abrechnung) oder
„Lastprognose fehlgeschlagen: …“. Die Datenbasis gilt für das zuletzt gebaute Profil.

### Prognosegüte

Die app vergleicht jede archivierte Prognose mit dem Ist-Wert aus der Abrechnung (PV: Summe der
Flüsse `pv>*`, Hauslast: Summe der Flüsse `*>house`; nur vollständige Viertelstunden). Je Slot und
Vorlaufklasse bleibt die Ausgabe, deren Vorlauf der Klassenmitte am nächsten liegt (3 h, 15 h,
30 h), so vergleichen die Kennzahlen typische Vorläufe und nicht nur die letzte Ausgabe vor dem
Slot. Bei PV zählen nur Viertelstunden, in denen Prognose oder Ist die Schwelle erreichen, sonst
verwässern die Nachtstunden den Fehler.

Je Art (PV, Last, Last Profil, Last Modell) und Vorlaufklasse (bis 6 h, 6 bis 24 h, über 24 h) zeigt die Karte
**Prognosegüte** auf der Seite „System“ für die letzten 14 Tage:

| Spalte | Bedeutung |
|---|---|
| Werte | Zahl der verglichenen Viertelstunden |
| MAE | mittlerer absoluter Fehler in W: Summe der Beträge der Fehler geteilt durch n |
| Abweichung | Bias in W: Summe der Fehler geteilt durch n; positiv heißt, die Prognose lag im Mittel zu hoch |
| MAE relativ | Summe der Beträge der Fehler geteilt durch die Summe der Ist-Werte, in %; ohne Ist „–“ |

Fehler = Prognose (P50) − Ist. Ohne Daten steht „Noch keine Kennzahlen.“ in der Karte. Dieselben
Zahlen liefert die API, eingeloggt und mit beliebiger Rolle: `GET /api/forecast/quality?days=14`
(`days` von 1 bis 60); die Zeilen gibt es für PV, die ausgegebene Last (`load`) und die beiden
Schatten `load_profile` und `load_model`. Die Aufrufe stehen im Unterabschnitt „API für Lastmodell
und Prognosegüte“ unten. Die Seite **Prognosen** zeigt dieselben Zahlen für 7, 14, 28 oder 60 Tage
(Abschnitt „Lastmodell (Phase 5a)“).

### API für Lastmodell und Prognosegüte

Für Skripte, mit Sitzung und Rolle wie das UI:

- `GET /api/forecast/load` (ab „Lesen“): die Lastprognose; `method` nennt das Verfahren der letzten
  Ausgabe, `profile` oder `model` (ohne Ausgabe `profile`).
- `GET /api/forecast/quality?days=14` (ab „Lesen“): Kennzahlen je Art (`pv`, `load`, `load_profile`,
  `load_model`) und Vorlaufklasse. `improvement_pct` steht nur bei `load_model` und sagt, wie viel
  Prozent genauer das Modell als das Profil derselben Vorlaufklasse war; sonst ist es `null`.
- `GET /api/forecast/quality/daily?days=28` (ab „Lesen“, `days` von 1 bis 60): der MAE in W je
  lokalem Tag und Art (`date`, `kind`, `n`, `mae_w`), die Vorlaufklassen zusammen, nach Datum und
  Art sortiert.
- `GET /api/learning/load` (ab „Lesen“): Zustand des Lastmodells mit `enabled`, `method`,
  `activation` (aktiv ja oder nein, Grundlage, MAE von Modell und Profil, `improvement_pct`,
  Grund und Satz), dem geladenen `model` mit den Anteilen der Merkmale, dem `backtest`, dem
  letzten `training` und den gewählten `sensors` (`in_model`: das Modell nutzt ihre Werte).
  `model` und `backtest` sind leer, solange es kein Modell gibt.
- `POST /api/learning/load/train` (nur Admin, Antwort 202 mit `{"started": true}`): trainiert
  sofort neu, wie die Taste „Jetzt neu trainieren“. Läuft schon ein Training, antwortet die API mit
  409 „Training läuft schon.“.
- `GET /api/learning/candidates` (nur Admin): Entitäten der HA-Datenbank mit Zahlenwert in den
  letzten 30 Tagen (`entities` mit `entity_id` und `unit`) zur Auswahl der Sensoren; `error` nennt,
  warum die Liste fehlt, etwa „HA-Datenbank nicht eingerichtet (influxdb.ha_database)“.

Anmeldung und `X-CSRF-Token` für den schreibenden Aufruf wie in Abschnitt 8, „Alternative für
Skripte (curl)“, etwa:

```bash
curl -b jar -H "X-CSRF-Token: $CSRF" -X POST "$APP/api/learning/load/train"
```

### Nächtlicher Lauf

Jede Nacht um 02:30 Uhr (lokal) und beim Start der app:

1. **Prognosegüte** für gestern und vorgestern neu rechnen (die Abrechnung holt Lücken aus
   InfluxDB nach, daher die zwei Tage); beim Start für heute, gestern und vorgestern.
2. **PV neu kalibrieren** (Faktoren und Band).
3. **Archiv aufräumen:** Prognosen, die älter als 60 Tage sind, werden gelöscht. Die Kennzahlen je
   Tag bleiben.

Ein Fehler in einem Schritt steht im Log der app (`docker compose logs ems-app`) und hält die
anderen nicht auf.

### Schätzpreise

Für eine Viertelstunde ohne gespeicherten Börsenpreis schätzt die app den Preis, damit die Planung
(Plan 3b) über das Ende der bekannten Preise hinaus rechnen kann. Der geschätzte Börsenpreis
(netto) ist der Median der gespeicherten Preise derselben Uhrzeit und desselben Tagestyps in den
28 Tagen davor, mindestens 2 Werte; sonst derselbe Median über alle Tagestypen; sonst gibt es
keinen Schätzpreis. Darauf kommen die Tarifbestandteile wie bei echten Preisen. Die Schätzpreise
sind als „geschätzt“ gekennzeichnet (Herkunft `estimated`) und stehen nur für die Planung bereit;
Übersicht, Abrechnung und der Sensor „Bezugspreis“ zeigen weiter nur echte Preise.

### Was du im UI siehst

- **Übersicht**, Diagramm „Preise, PV und Akku heute/morgen“ (bis Plan Übersicht B „Preise und PV heute/morgen“): die Reihe „PV-Prognose“ (P50) mit dem
  Band „PV P25–P75“ und die „Lastprognose“ als Linie. Scheitert die Abfrage einer Prognose,
  steht der Fehler über dem Diagramm, und die übrigen Reihen bleiben.
- **System**: die Karten „Lastprognose“ und „Prognosegüte“ (oben, mit dem Verweis „Mehr unter
  Prognosen“) und bei den Komponenten „forecast“, „load-forecast“ und „load-model“ der Stand der
  Prognosen und des Lastmodells.
- **Prognosen**: Verfahren der Lastprognose, Prognosegüte, Fehler je Tag, Backtest und Merkmale des
  Modells (Abschnitt „Lastmodell (Phase 5a)“).

### An der Anlage prüfen

Plan 3a ist ohne die echte Anlage getestet; zwei Dinge zeigen sich erst im Betrieb:

- **Abfrage der Hauslast aus Home Assistant:** Die InfluxQL-Abfrage (Minutenmittel mit
  `fill(previous)` auf dem Einheitenfeld, über bis zu 56 Tage in einer Abfrage) ist nur gegen
  nachgebaute Antworten getestet. Sieh auf der Seite „System“ nach, ob die Karte „Lastprognose“
  bei „Datenbasis“ Viertelstunden „aus Home Assistant“ nennt (die Zahl ist größer als 0, sobald
  `load_history` gesetzt ist und die Abrechnung Lücken hat), und ob im Log keine Warnung
  „Lastverlauf aus Home Assistant nicht lesbar“ oder zur Einheit der Entität steht:
  `docker compose logs --since 1h ems-app | grep -i "lastverlauf\|lastprofil"`.
- **Kalibrierung der PV:** Sie braucht etwa eine Woche Archiv, bevor die Faktoren von 1,0
  abweichen: In den ersten Tagen fehlen je Stunde und Klarheitsklasse die 8 Paare, und das Band
  steht auf 0,7 bis 1,2. Die Komponente „forecast“ nennt „kalibriert aus n Viertelstunden“, sobald
  es Faktoren gibt.

## Lastmodell (Phase 5a)

Seit Plan 5a lernt die app die Hauslast zusätzlich mit einem **Modell** (LightGBM) aus der
Historie, dem Kalender, der Temperatur und, wenn du es willst, einigen Sensoren aus Home Assistant.
Das Modell ist eine Alternative zum Profil der Lastprognose (Abschnitt „Prognosen“, „Lastprognose“):
Die Planung nutzt es nur, solange es nachweislich genauer ist, sonst gilt das **Profil**. Der core
ändert sich dabei nicht. **5a schaltet nichts an der Anlage:** Das Modell ändert nur die Eingabe
des Planers, und jeder Wert liegt zwischen 0 und dem 1,5-Fachen des höchsten Viertelstundenwerts
der Trainingsdaten (Spec 10.5).

### Ablauf

1. **Temperatur sammeln:** Jede PV-Prognose schreibt die Lufttemperatur der Viertelstunden bis 6 h
   nach ihrer Ausgabe in die Tabelle `weather_slot` (auch die des Vortags); eine spätere Ausgabe
   ersetzt eine frühere. Ist das Archiv beim Start jünger als 56 Tage, holt die app einmal 92 Tage
   Temperatur von Open-Meteo nach (nur `temperature_2m`, am Standort). Das Log nennt es mit
   „Temperatur-Historie nachgeholt“; scheitert der Abruf, warnt die app, und der nächste Start
   versucht es erneut.
   Temperaturen, die älter als 120 Tage sind, löscht der Nachtlauf um 02:30 Uhr (das Training
   braucht 112 Tage).
2. **Trainieren:** Jede Nacht um 03:15 Uhr (Ortszeit), beim Start, wenn es noch keine brauchbare
   Version gibt, und mit der Taste „Jetzt neu trainieren“ (nur Admin). Das erste Training nach dem
   Start wartet auf die Nachholung der Temperatur, höchstens 10 min. Jedes Training legt eine neue
   **Version** an.
3. **Backtest:** Mit jedem Training rechnet die app einen Rückblick (unten).
4. **Aktivieren:** Danach entscheidet die Regel (unten), ob die Planung das Modell oder das Profil
   nutzt. Die Regel läuft auch, wenn das Training scheitert oder nicht genug Daten hat; dann bleibt
   die bisherige Version.
5. **Ausgeben:** Bei jeder stündlichen Ausgabe der Lastprognose (Minute 9) rechnen Profil und Modell,
   beide Ergebnisse gehen in das Archiv, und als Lastprognose geht das aktive Verfahren hinaus.
   Wechselt das Verfahren (Schalter, Bewertung), folgt gleich eine Ausgabe außer der Reihe.

### Mindestdaten und Nachtlauf

- **Fenster:** Das Training nutzt die letzten 56 Tage der Hauslast, dieselbe Historie wie das Profil
  (Abrechnung, nur vollständige Viertelstunden; die HA-Historie der Hauslast nur mit
  `homeassistant.load_history`).
- **Mindestdaten:** Trainiert wird erst, wenn die 56 Tage mindestens **28 Tage** mit je mindestens
  80 von 96 vollständigen Viertelstunden enthalten. Davor bleibt das Profil, und die Seite
  „Prognosen“ nennt „Zu wenig Daten: {n} von 28 Tagen.“. Ein Tag mit weniger als 80 Viertelstunden
  (Ausfall der app, Lücken in der Abrechnung) zählt nicht mit. Die Abrechnung läuft seit Phase 1b;
  beim Ausrollen reicht das.
- **Merkmale:** Vorlauf in Stunden (ein Modell für alle Vorläufe bis 72 h), Uhrzeit, Wochentag,
  Tagestyp (Werktag, Samstag, Sonn- und Feiertag), Feiertag, Monat, die Temperatur der Viertelstunde
  und das Tagesmittel, die Last zur selben Uhrzeit vor einem Tag und vor einer Woche, die mittlere
  Last der letzten Stunde vor der Ausgabe, das Profil selbst und je gewähltem Sensor sein Wert vor
  der Ausgabe. Ein Wert, den die app zum Zeitpunkt der Ausgabe nicht kannte, fließt nie ein: Die Last
  von gestern gilt nur, wenn sie vor der Ausgabe vollständig war, und ein Sensor gilt für den ganzen
  Vorlauf mit dem Wert vor der Ausgabe. Fehlt ein Wert, bleibt das Merkmal leer.
- **Training:** Ausgaben um 00:00, 06:00, 12:00 und 18:00 Uhr (Ortszeit), je Ausgabe alle
  Viertelstunden bis 72 h danach, die einen Ist-Wert haben (bei 56 Tagen rund 60 000 Zeilen). Das
  Modell schätzt den Median wie das Profil; die Parameter sind fest, es gibt keine Suche. Training und
  Backtest laufen in einem eigenen Thread; dauern sie länger als 10 min, gilt der Lauf als gescheitert
  („Training fehlgeschlagen: Zeitlimit von 600 s überschritten“).
- **Temperatur:** Im Training gilt die archivierte Temperatur (nahe am Ist), in der Vorhersage die
  aktuelle Prognose. Das macht den Backtest etwas zu günstig; der Schattenbetrieb misst ehrlich.
- **Versionen:** Behalten werden die 14 neuesten Versionen und die aktive.

### Backtest

Der Rückblick prüft, wie gut das Verfahren in den letzten Wochen gewesen wäre. Startpunkte sind die
lokalen Mitternächte 7, 14, … Tage vor heute. Je Startpunkt lernt ein Modell nur mit Daten davor
(höchstens 56 Tage) und sagt die Ausgaben der folgenden 7 Tage voraus; verglichen wird es mit dem
Profil, das an dem Tag gegolten hätte. So viele Wochen, wie die Daten nach den ersten 14 Tagen
erlauben, höchstens 8, mindestens 2 (also ab 28 Tagen Daten). Das Ergebnis steht je Woche und
Vorlaufklasse (0–6 h, 6–24 h, über 24 h) auf der Seite „Prognosen“.

### Aktivierung

Das Modell ist **aktiv**, wenn sein mittlerer absoluter Fehler (MAE) über die letzten 14 vollen Tage
**mindestens 10 %** unter dem des Profils liegt (`1 − MAE Modell / MAE Profil`), über alle
Vorlaufklassen zusammen. Heute zählt nicht mit. Die Regel läuft jede Nacht nach dem Training, ohne
Hysterese.

- **Grundlage:** der **Schattenbetrieb** (die archivierten Ausgaben von Modell und Profil gegen das
  Ist), sobald er an mindestens 10 der 14 Tage Werte hat; vorher die letzten 14 Tage des Backtests.
  So kann das Modell schon kurz nach dem Ausrollen aktiv sein, wenn es im Backtest klar besser ist,
  und der Betrieb entscheidet, sobald er genug gesehen hat. Nach einem Neustart der app gilt die
  gespeicherte Entscheidung bis zur nächsten Bewertung; der Backtest fehlt bis zum nächsten Training,
  bis dahin entscheidet nur der Schattenbetrieb.
- **Profil gilt** ohne brauchbares Modell, mit ausgeschaltetem Schalter „Lastmodell verwenden“
  (Standard an) und bei jedem Fehler. Der Schalter wirkt sofort: Ausschalten gibt gleich eine neue
  Lastprognose mit dem Profil aus, Einschalten bewertet gleich neu und gibt mit dem Modell aus, wenn
  die Regel es aktiviert. Das Modell rechnet im Schatten weiter.
- **Wechsel des Verfahrens:** Ändert sich, ob das Modell gilt (Schalter, Bewertung nach dem Training,
  beim Start geladene Version), gibt die Lastprognose gleich neu aus, nicht erst zur Minute 9; die
  neue Ausgabe löst wie jede andere die Planung aus.
- **Die Gründe**, wie die Seite sie nennt:

| Grund | Satz |
|---|---|
| `better` | „Modell aktiv: in den letzten 14 Tagen {x} % genauer als das Profil (im Schattenbetrieb).“ bzw. „(im Backtest)“ |
| `not_better` | „Profil aktiv: Das Modell ist nur {x} % genauer, nötig sind 10 %.“ oder bei negativem Wert „Profil aktiv: Das Modell ist {x} % ungenauer als das Profil.“ |
| `too_few_days` | „Profil aktiv: Für den Vergleich fehlen noch Tage ({n} von 10).“ |
| `disabled` | „Profil aktiv: Das Lastmodell ist in den Einstellungen ausgeschaltet.“ |
| `no_model` | „Profil aktiv: Es gibt noch kein brauchbares Modell.“ |

  Ein knapp verfehltes Ergebnis rundet der Satz ab: 9,6 % heißt „nur 9 %“, damit nie „nur 10 %,
  nötig sind 10 %“ dasteht.

### Schattenbetrieb und Ausgabe

- Jede stündliche Ausgabe rechnet Profil und Modell und schreibt beide ins Archiv (`load_profile` und
  `load_model`); als `load` (für den Planer, InfluxDB und die bisherige Güte) geht das aktive
  Verfahren. Daraus entsteht die Prognosegüte je Verfahren.
- Fehlt dem Modell für einzelne Viertelstunden ein Wert, nimmt die Ausgabe dort das Profil. Fehlt er
  für mehr als 10 % der Viertelstunden, oder wirft die Vorhersage einen Fehler, gilt für diese
  Ausgabe ganz das Profil (Warnung im Log).

### Rückfall

| Fall | Folge |
|---|---|
| Training scheitert (auch bei voller Platte, die Datei wird dann gar nicht angelegt) oder hat zu wenig Daten | Die bisherige Version bleibt; die Aktivierung wird trotzdem neu bewertet. Die Seite nennt den Grund unter „Lastprognose“. |
| Modelldatei fehlt oder ist nicht lesbar (etwa nach einer Rücksicherung der Datenbank) | Die Version gilt als unbrauchbar (Warnung „Lastmodell nicht lesbar, die Version wird nicht mehr verwendet“), die Planung nutzt das Profil, und das nächste Training legt eine neue Version an. |
| Schalter „Lastmodell verwenden“ aus | Sofort Profil: Die Lastprognose gibt gleich neu aus, und die Planung rechnet damit. |
| Sensor in HA fehlt, ist umbenannt oder nicht numerisch | Das Merkmal ist leer, das Training läuft; die Karte „Merkmale“ zeigt den Sensor als „nicht im Modell“. |
| Auswahl der Sensoren ändert sich | Jedes Modell rechnet mit seiner eigenen Merkmalsliste: Ein entfernter Sensor ist leer, ein neuer zählt erst ab dem nächsten Training. |

Die Komponente `load-model` unter **System** → „Komponenten“ nennt den Zustand: „Lastmodell aktiv“,
„Profil aktiv, Modell bereit“, „Noch kein Modell“ (ein normaler Zustand bis zum ersten gelungenen
Training, etwa mit zu wenig Daten; Status in Ordnung) oder „Lastmodell fehlgeschlagen: {Fehler}“
(Status nicht in Ordnung).

### Sensoren aus Home Assistant wählen

Unter **Einstellungen** → „Lastmodell“ (Admins bearbeiten, alle anderen sehen die Auswahl):

- **Lastmodell verwenden:** Aus: Die Planung nutzt immer das Profil. Das Modell rechnet weiter mit.
- **Zusätzliche Sensoren aus Home Assistant (höchstens 8):** die Liste der Entitäten der
  HA-Datenbank, die in den letzten 30 Tagen einen Zahlenwert hatten (Zahl, oder ein Zustand, den HA
  als Zahl schreibt, etwa an/aus). Zum Beispiel Anwesenheit (`person.…`) oder die gemessene
  Außentemperatur. Wirkt ab dem nächsten Training. Ohne HA-Datenbank steht dort „HA-Datenbank nicht
  eingerichtet (influxdb.ha_database)“.
- Der Wert eines Sensors ist das Mittel der letzten vollständigen Viertelstunde vor der Ausgabe und
  gilt für den ganzen Vorlauf; für die Zukunft kennt BindaEMS keine Sensorwerte. Ein gewählter Sensor,
  den die Liste nicht kennt, steht dort mit „nicht in HA gefunden“.
- Das Modell hat nach dem Training für jedes Merkmal einen Anteil am Gewinn (Seite „Prognosen“, Karte
  „Merkmale“). Ein gewählter Sensor ohne Anteil (er hatte keine Werte oder keinen Einfluss) steht
  dort unter der Tabelle als „{Entität}: nicht im Modell“.

### Die Seite „Prognosen“

Sie steht in der Navigation nach „Zeitplan“ und ist für alle Rollen sichtbar; nur die Taste ist für
Admins. Zeitraum der Güte und des Verlaufs: „7 Tage“, „14 Tage“, „28 Tage“ oder „60 Tage“.

| Karte | Inhalt |
|---|---|
| Lastprognose | „Verfahren: Profil“ bzw. „Verfahren: Modell“, der Grund der Aktivierung (Tabelle oben), der Zustand von `load-model`, „Modell vom {TT.MM.JJJJ HH:MM}“, „Daten {TT.MM.}–{TT.MM.JJJJ} · {n} Viertelstunden“, das Ergebnis des letzten Trainings („Trainiert am {TT.MM. HH:MM} mit {n} Viertelstunden.“, „Zu wenig Daten: {n} von 28 Tagen.“ oder „Training fehlgeschlagen: {Fehler}“) und die Taste „Jetzt neu trainieren“ (während des Laufs „Training läuft …“). Ohne Modell: „Noch kein Modell trainiert.“ |
| Prognosegüte | Spalten „Art“, „Vorlauf“, „Slots“, „MAE“, „Bias“, „rel. MAE“ und „besser als Profil“; Arten „PV“, „Last (verwendet)“, „Last Profil“ und „Last Modell“; Vorlauf „0–6 h“, „6–24 h“, „über 24 h“. „besser als Profil“ steht nur bei „Last Modell“. Leer: „Noch keine Werte im Zeitraum.“ |
| Fehler je Tag | MAE je Tag als Linien „Profil“ und „Modell“, Achse „MAE (W)“ |
| Backtest | Spalten „Woche ab“, „Vorlauf“, „Slots“, „Modell“, „Profil“ und „besser“, dazu die Zeile „Gesamt“. Leer: „Noch kein Backtest (braucht mindestens 28 Tage Daten).“ |
| Merkmale | Spalten „Merkmal“ und „Anteil“: der Anteil jedes Merkmals am Gewinn des Modells, absteigend (Vorlauf, Uhrzeit, Temperatur, … bzw. die Entität eines Sensors) |

Die Karte „Prognosegüte“ auf der Seite „System“ bleibt und verweist mit „Mehr unter Prognosen“ auf
die Seite. Die Aufrufe dahinter stehen im Abschnitt „Prognosen“, „API für Lastmodell und
Prognosegüte“.

### Modelle auf der Platte, keine Sicherung nötig

Die Modelle liegen als Textdateien `load-<Nummer>.txt` unter `/data/models/load/` im Volume
`app-data`; die Registry (`model_registry`) steht in der Datenbank der app. **Eine Sicherung ist nicht
nötig:** Jede Nacht entsteht eine neue Version, und ein verlorenes Modell ist spätestens am
nächsten Morgen ersetzt (oder gleich mit „Jetzt neu trainieren“). Nach einer Rücksicherung der
Datenbank kann die Registry Versionen kennen, deren Datei fehlt; die app erkennt das, stuft sie als
unbrauchbar ein (siehe „Rückfall“), und das nächste Training behebt es. Das Wetterarchiv ist Teil der
Datenbank; ist es nach einer Rücksicherung jünger als 56 Tage, holt die app beim Start die
Temperatur-Historie wieder nach.

Das Image von `ems-app` ist mit Plan 5a rund 130 MB größer: LightGBM bringt `numpy` und `scipy` mit,
und es braucht `libgomp1` im Image (das Dockerfile installiert es). Die CI baut das Image, damit
das auffällt; wer lokal baut (Abschnitt 4, „Lokaler Build“), braucht nichts Besonderes.

### Lastmodell ausrollen (Plan 5a)

Plan 5a ändert nur die app, `shared/settings.py` und das UI; der core und sein Image bleiben
gleich. Ausgerollt wird **mit oder nach dem gemeinsamen Ausrollen von 4b und 6a** und damit im
gemeinsamen Fenster nach dem 25.10.2026 (Entscheidung des Betreibers vom 22.10.2026). Bis dahin ziehst du keine Images (`docker compose pull`), denn `latest`
enthält Plan 5a, sobald er auf `main` ist.

**Was sich an der Anlage ändert:** Plan 5a schaltet nichts an der Anlage; kein Aktor geht live, und
der core bleibt, wie er ist. Er ändert nur die Eingabe des Planers, die Lastprognose. **Das Modell
kann aber gleich nach dem ersten Training aktiv werden:** Der Schalter „Lastmodell verwenden“ steht
ab Werk auf an, und hat die Abrechnung schon 28 Tage mit Daten, bewertet die app die Aktivierung
nach dem ersten Training (wenige Minuten nach dem Start) aus dem Backtest. Ist das Modell dort
mindestens 10 % genauer, plant der Planer ab dann mit der Lastprognose des Modells. Der Backtest ist
in den ersten Wochen zu günstig für das Modell: Training und Backtest rechnen mit der archivierten
Temperatur, die fast der gemessenen entspricht, im Betrieb kennt das Modell nur die Prognose; und die
Profile des Backtests haben wenig Historie, was das Profil dort schwächer macht. Erst ab 10 Tagen
Schattenbetrieb entscheidet der Betrieb selbst.

**Reiner Schattenbetrieb (wahlweise):** Willst du zuerst nur beobachten, schaltest du gleich nach
dem Start unter **Einstellungen** → „Lastmodell“ den Schalter „Lastmodell verwenden“ aus (Admin).
Dann gilt immer das Profil; das Modell rechnet trotzdem jede Stunde im Schatten mit, und die Seite
„Prognosen“ zeigt seine Güte. War es schon aktiv, gibt die Lastprognose gleich wieder mit dem Profil
aus. Einschalten geht später an derselben Stelle: Die app bewertet sofort neu und nimmt das Modell
nur, wenn die Regel (oben, „Aktivierung“) es aktiviert; nach mindestens 10 Tagen Schattenbetrieb
entscheidet dabei der Schattenbetrieb, nicht der Backtest.

1. **Datenbank sichern** (empfohlen, wie bei 4a und 4b): `docker compose stop ems-app`, dann
   `docker compose run --rm --no-deps --entrypoint sh ems-app -c 'cp /data/bindaems.sqlite3 /backup/vor-plan-5a.sqlite3'`.
   Eine ältere app startet mit der Migration `0013` nicht (Abschnitt 4, „Rollback auf einen Stand vor
   Plan 5a“).
2. **Ausrollen:** nach (oder mit) 4b und 6a nur die app:
   `cd /opt/bindaems && docker compose pull ems-app && docker compose up -d ems-app`. Der core
   bleibt und der Regelbetrieb bleibt an. Rollst du 4b und 6a gleichzeitig aus, genügt deren
   `docker compose pull && docker compose up -d`; ist 5a dann schon auf `main`, bringt dieser Pull 5a
   und die Migration `0013` mit, die Sicherung aus Schritt 1 gehört dann davor.
3. **Beim Start** läuft die Migration `0013` (Tabellen `weather_slot` und `model_registry`; die Spalten
   `kind` von `forecast_record` und `forecast_metric` werden auf 16 Zeichen verlängert), die app holt
   die Temperatur-Historie einmal nach, und das **erste Training beginnt gleich danach**. Hat die
   Abrechnung schon 28 Tage mit Daten, entsteht die erste Version; sonst steht auf der Seite „Prognosen“
   „Zu wenig Daten: {n} von 28 Tagen.“, und die nächste Nacht versucht es wieder.
4. **Sensoren wählen** (optional): unter **Einstellungen** → „Lastmodell“ bis zu 8 Entitäten (zum
   Beispiel Anwesenheit), dann auf der Seite **Prognosen** „Jetzt neu trainieren“.
5. **Beobachten:** Gleich nach dem ersten Training zeigt die Karte „Lastprognose“ auf der Seite
   „Prognosen“, welches Verfahren gilt und warum. Steht dort „Modell aktiv: … (im Backtest)“, plant
   der Planer schon mit dem Modell; willst du zuerst einen reinen Schattenbetrieb, schalte jetzt
   „Lastmodell verwenden“ aus (oben, „Reiner Schattenbetrieb“).

Die Abnahme der Phase 5, Teil Lastmodell, steht in `docs/verification/abnahme-phase-5a.md`: 28 Tage
ab dem Ausrollen.

### Abnahme

Bestanden ist sie, wenn auf der Seite „Prognosen“ mit dem Zeitraum „28 Tage“ das Modell mindestens
10 % niedrigeren MAE hat als das Profil, **über alle Vorlaufklassen zusammen** (nach Viertelstunden
gewichtet, so wie die Regel der Aktivierung sie zusammenfasst), **oder** das Profil in dieser Zeit
automatisch aktiv war, sobald und solange das Modell es nicht schaffte. Die Spalte „besser als
Profil“ der Zeilen „Last Modell“ steht je Vorlaufklasse und entscheidet allein nicht; die Zahl für
die 28 Tage liefert der Abruf mit `jq` in der Checkliste (Abschnitt 3, `improvement_pct`). Der Satz
der Aktivierung in der Karte „Lastprognose“ nennt dieselbe zusammengefasste Verbesserung, aber für
die letzten 14 Tage; ihn trägst du wöchentlich ein (Checkliste, Abschnitt 2).
Dazu kommen jede Nacht ein Training (Registry), keine Fehler in `load-model` und der Rückfall bei
fehlender Modelldatei, an der Anlage beobachtet. Die Checkliste führt die Schritte auf.

## Fahrverhalten (Phase 5b)

Seit Plan 5b lernt die app aus den Fahrdaten jedes Autos, **wann es abfährt, mit wie viel Ladestand
es zurückkommt und wann es angesteckt ist**. Daraus entstehen ein **Vorschlag** für Ziel-SOC und
Abfahrt, eine **Anwesenheitsprognose** für die nächsten 48 Stunden und, wenn du es willst, eine
**Automatik**, die Ziel und Abfahrt von „Geplant“ selbst setzt. **Unbekannt bleibt unbekannt:** Ohne
Daten gibt es keinen Vorschlag, keine Prognose und kein erwartetes Auto, nie einen Ersatzwert.
Gelernte Werte erreichen die Safety-Schicht nie und ändern keine harte Grenze (Spec 10.5); die
Automatik ist ab Werk **aus**, und kein Aktor wird dafür live geschaltet.

### Ablauf und Komponente

- **Berechnung:** Jede Nacht um **03:40 Uhr** (Ortszeit, nach dem Lastmodell um 03:15), beim Start
  und mit der Taste „Jetzt neu berechnen“ (nur Admin) liest die app je konfiguriertem Auto die
  Fahrdaten der letzten 56 Tage und legt ein Profil in der Tabelle `driving_profile` ab (behalten
  werden 70 Tage). Während eines Laufs steht auf der Taste „Berechnung läuft …“; ein zweiter Start
  ist nicht möglich. Scheitert das Lesen oder Rechnen für ein Auto, gilt dessen letzter Stand weiter.
- **Quelle der Anwesenheit** (je Auto die erste verfügbare): die Entität
  `learning.driving.presence_entities` (HA-Datenbank), `home_entity` des Autos, bei einem
  Tessie-Auto der Status `at_home`, sonst nur die Ansteckvorgänge. Das Profil nennt die Quelle:
  `ha`, `home_entity`, `tessie` oder `sessions`. Fehlt dem Auto ein Stand, steht dort `null`.
- **Profil:** Je Wochentag (Gruppen „Mo–Fr“, „Sa“, „So/Feiertag“, wenn ein Wochentag weniger als 3
  Tage hat) Abfahrt (P20, auf die Viertelstunde abgerundet), Ankunft, Ladestand bei der Ankunft,
  Verbrauch (P80) und daraus das Ziel: Reserve (`return_reserve_pct`) plus Verbrauch, auf 5 %
  aufgerundet und zwischen `target_min_pct` und `target_max_pct` begrenzt. Dazu die **Konfidenz**
  („hoch“, „mittel“, „niedrig“) und je Viertelstunde die Wahrscheinlichkeit, dass das Auto angesteckt
  ist.
- **Einstellungen** (`learning.driving`): `return_reserve_pct` (20), `target_min_pct` (50),
  `target_max_pct` (90), `automation` (Schalter je Auto, ab Werk leer, also aus) und
  `presence_entities` (je Auto eine HA-Entität). Einträge für Autos, die `config.yaml` nicht kennt,
  übergeht die app. **Änderungen wirken sofort:** Das Ziel jedes Wochentags rechnet die app bei jeder
  Verwendung (Vorschlag, Automatik, Karte „Fahrverhalten“, Home Assistant) aus dem abgelegten
  Verbrauch (P80) mit dem aktuellen Puffer und Ziel-SOC-Bereich neu, mit „(begrenzt)“ dazu. Jede
  Änderung von `learning.driving` außer den Schaltern der Automatik startet außerdem gleich einen
  neuen Lauf (etwa für eine geänderte Anwesenheit aus Home Assistant), nicht erst um 03:40 Uhr; läuft
  schon einer, folgt der neue danach.
- **Komponente `driving`** unter **System** → „Komponenten“: „Fahrverhalten aktuell“, „Noch keine
  Fahrdaten“ (normal, bis das erste Profil Tage hat; Status in Ordnung) oder „Fahrverhalten
  fehlgeschlagen: {Fehler}“ (Status nicht in Ordnung).

### Die Automatik für Ziel und Abfahrt

Je Auto gibt es einen Schalter „Ziel und Abfahrt automatisch“ (`learning.driving.automation`, ab
Werk **aus**). Ist er an, prüft die app jede Minute und nach jedem Lauf des Fahrverhaltens der
Reihe nach: Health vertrauenswürdig, das Auto steht auf „Geplant“, keine geplante Ladung läuft, die
Pause ist vorbei und der nächste Vorschlag hat für Abfahrt oder Ziel mindestens die Konfidenz
„mittel“. Weichen die Werte von der Absicht ab, setzt sie Ziel und Abfahrt (mit dem Tag der Fahrt)
mit dem Urheber „Automatik“; den Lademodus ändert sie nie.

**Pause bei Bedienung von Hand:** Ändert jemand Ziel oder Abfahrt eines Autos mit Automatik von
Hand (also nicht durch die Automatik selbst), pausiert die Automatik bis zu dieser Abfahrt (ohne
Abfahrt bis Mitternacht). Das Einschalten des Schalters beendet eine Pause. Bis zur ersten
Prüfung nach dem Start steht ein eingeschaltetes Auto auf `pending`. Die Zustände und ihre
Sätze (`automation.state` und `automation.text`):

| Zustand | Satz |
|---|---|
| `off` | „Automatik aus.“ |
| `pending` | „Automatik prüft in Kürze.“ |
| `inactive` | „Automatik wartet: Das Auto steht nicht auf „Geplant“.“ |
| `waiting` | „Automatik wartet: Eine geplante Ladung läuft bis {Tag} {HH:MM}.“ |
| `paused` | „Automatik pausiert bis {Tag} {HH:MM}, weil Ziel oder Abfahrt von Hand geändert wurden.“ |
| `uncertain` | „Automatik wartet: Der Vorschlag ist noch zu unsicher.“ |
| `applied` | „Automatik: {Tag} {HH:MM}, Ziel {x} %.“ bzw. mit „(gesetzt {TT.MM. HH:MM})“ |
| `error` | „Automatik fehlgeschlagen: {Fehler}“ (nächster Versuch in einer Minute) |

Das Schalten steht im Änderungsprotokoll als Aktion `control.automation` („Automatik an“ bzw.
„Automatik aus“, Urheber ist der Benutzer); die Änderung von Ziel und Abfahrt durch die Automatik
steht dort mit dem Urheber „Automatik“.

### Home Assistant

Seit Plan 5b hat jedes Fahrzeug vier Entitäten am Gerät „BindaEMS <Fahrzeug>“. Die Objekt-IDs stehen
hier für den Schlüssel `tesla` der Beispielkonfiguration; bei einem anderen Fahrzeug steht dessen
Schlüssel vorn.

| Objekt-ID | Name | Plattform | Wert |
|---|---|---|---|
| `tesla_auto_target` | Automatik Ziel und Abfahrt | switch | „an“, wenn die Automatik für das Auto an ist (`learning.driving.automation`); schreibend, mit den Rechten von „Bedienen“ (Quelle „ha“, Urheber `home-assistant`) |
| `tesla_suggested_departure` | Vorschlag Abfahrt | sensor | die nächste erwartete Abfahrt als Zeitstempel (Geräteklasse „timestamp“, UTC); Attribute `confidence` und `basis` |
| `tesla_suggested_target` | Vorschlag Ziel-SOC | sensor | das vorgeschlagene Ziel in % für diese Fahrt; Attribute `confidence` und `basis` |
| `tesla_next_departure` | Nächste Abfahrt | sensor | die nächste Abfahrt aus Health (`planned.departure_at`) als Zeitstempel; ohne Attribute |

- **Vorschlag:** „Vorschlag Abfahrt“ und „Vorschlag Ziel-SOC“ stammen aus derselben nächsten
  erwarteten Fahrt wie `next_trip` in der API: Tag und Uhrzeit sind lokal (Wiener Zeit), Home
  Assistant bekommt den Zeitpunkt in UTC. Das Attribut `confidence` ist „hoch“, „mittel“ oder
  „niedrig“ (für die Abfahrt bzw. für das Ziel), `basis` ist „Wochentag“ oder, wenn der Wochentag zu
  wenige Tage hat, „Gruppe Mo–Fr“, „Gruppe Sa“ oder „Gruppe So/Feiertag“; ein Feiertag zählt zu
  „So/Feiertag“.
- **Unbekannt bleibt unbekannt:** Ohne Profil, ohne Fahrt in den nächsten acht Tagen (oder ohne
  Ziel) sind der Sensor „unbekannt“ und beide Attribute `null`; nie steht ein Ersatzwert da.
  „Nächste Abfahrt“ ist „unbekannt“, solange Health keine Abfahrt nennt: wenn das Auto nicht in
  „Geplant“ steht, aber auch, solange ein Auto in „Geplant“ nicht angesteckt ist, denn der core meldet
  den Stand von „Geplant“ nur für ein Auto an einer Wallbox. Eine Abfahrt an einem Tag, die die
  Automatik für ein Auto unterwegs setzt, siehst du in Home Assistant bis zum Anstecken nur als
  „Vorschlag Abfahrt“.
- **Schalten:** Nach einem Befehl veröffentlicht die app den Zustand des Schalters gleich neu. Das
  Einschalten prüft die Automatik sofort und beendet eine Pause (siehe oben). Wie für alle Befehle
  gilt höchstens ein Befehl je Sekunde und Entität (Abschnitt 7, „Bedienelemente und Befehle“).
- **Zeiten:** Alle Zeitstempel der Entitäten dieses Abschnitts sind ISO 8601 in UTC.

### API für das Fahrverhalten

- `GET /api/learning/driving` (ab „Lesen“): `computed_at`, `running`, `status` (`ok`, `text`) und
  `vehicles`. Je Auto: `key`, `name`, `source`, `data_from`, `data_to`, `days`, `weekdays` (sieben
  Einträge mit `weekday`, `basis`, `days`, `trips`, `trip_pct`, `departure`, `departure_p50`,
  `departure_p80`, `departure_confidence`, `arrival_p50`, `arrival_soc_p50`, `consumption_p50_pp`,
  `consumption_p80_pp`, `target_soc_pct`, `target_clamped`, `target_confidence`), `next_trip`,
  `presence`, `quality` (Trefferquote der letzten 28 Tage) und `automation` (`enabled`, `state`,
  `text`, `paused_until`, `last_set_at`). Alle Zeitstempel sind ISO 8601 in UTC, Werte der Form
  `HH:MM` sind Ortszeit. `presence` nennt die nächsten 48 Stunden als Viertelstunden `{"start": …, "p": 0.88}` ab dem Beginn der laufenden
  Viertelstunde, `p` auf zwei Stellen gerundet; ohne Profil ist die Liste leer. Ein Auto ohne Stand
  fehlt nicht: `source`, `data_from` und `data_to` sind `null`, `days` ist 0 und `weekdays` leer.
  `next_trip`, die Trefferquoten und die Werte je Wochentag können `null` sein.
- `POST /api/learning/driving/recompute` (nur Admin, Antwort 202 `{"started": true}`): rechnet im
  Hintergrund neu. Läuft schon eine Berechnung, antwortet die app mit 409 „Berechnung läuft schon.“.
- `PUT /api/control/vehicles/{key}/automation` (ab „Bedienen“) mit `{"on": true}`: schaltet die
  Automatik des Autos, schreibt `control.automation` ins Änderungsprotokoll und antwortet mit 200 und
  dem neuen `automation`-Objekt. Ein unbekanntes Auto beantwortet die app mit 404.
- `GET /api/plan` nennt je Auto zusätzlich `expected` (das Auto ist noch nicht angesteckt, die
  Anwesenheitsprognose erwartet es) und `arrival_at` (erwartete Ankunft, sonst `null`); der Grundcode
  `AUTO_ERWARTET` steht im Abschnitt „Planung“.

### Fahrverhalten ausrollen (Plan 5b)

Plan 5b ändert **den core** (eine Zeile in `core/control/planned.py`: die Abfahrt an einem Datum,
`departure_on`), die app, `shared` und das UI. Ausgerollt wird **gemeinsam mit 3d, 4b, 5a und 6a** und
damit im gemeinsamen Fenster nach dem 25.10.2026 (Entscheidung des Betreibers vom 22.10.2026, statt
nach der Abnahme der Phase 3); bis dahin ziehst du keine Images (`docker compose pull`), denn
`latest` enthält Plan 5b, sobald er auf `main` ist. **Nichts geht von selbst live:** Die Automatik ist
aus, bis du sie einschaltest, erwartete Autos ändern nur den Plan, und gelernte Werte erreichen die
Safety-Schicht nie (Spec 10.5).

1. **Regelbetrieb aus:** im UI unter **System** → „Bedienung“ oder in Home Assistant. Der core übergibt
   die Aktoren und geht nach OBSERVE (Abschnitt 13); warte, bis die Übergabe fertig ist. Das muss
   **vor** dem Stoppen der app geschehen, denn ohne app erreichst du den Schalter nicht. Der core hat
   sich für `departure_on` geändert: Jede Absicht mit einem Eintrag unter `vehicles` trägt jetzt dieses
   Feld, und ein core vor 5b liest sie nicht. Core und app müssen deshalb gemeinsam wechseln.
2. **Datenbank sichern:** `docker compose stop ems-app`, dann
   `docker compose run --rm --no-deps --entrypoint sh ems-app -c 'cp /data/bindaems.sqlite3 /backup/vor-plan-5b.sqlite3'`.
   Die Migration `0014` legt `driving_profile` und `driving_automation` an; eine ältere app startet
   danach nicht (Abschnitt 4, „Rollback auf einen Stand vor Plan 5b“).
3. **core und app gemeinsam ziehen:** `cd /opt/bindaems && docker compose pull && docker compose up -d`
   (dieselben Image-Tags). Wo 3d, 4b, 5a und 6a im selben Zug ausgerollt werden, ist das derselbe Pull.
   Die Migration `0014` läuft beim Start der app von selbst und ändert keine vorhandene Tabelle.
4. **Regelbetrieb an:** wieder unter **System** → „Bedienung“, erst wenn **System** core und app mit
   demselben Stand zeigt.
5. **Anwesenheit eintragen:** Unter **Einstellungen** → „Fahrverhalten“ trägst du beim Tesla unter
   „Anwesenheit aus Home Assistant“ den `device_tracker.…` ein, den Home Assistant für ihn hat. Der
   Eintrag wirkt **nur mit `influxdb.ha_database`** in der `config.yaml`; ohne sie schreibt die app eine
   Warnung („Die Anwesenheit aus Home Assistant braucht eine HA-Datenbank. Die nächste Quelle gilt.“) und
   nimmt die nächste Quelle. Die HA-Datenbank reicht meist weiter zurück als Tessie, die Vorschläge stehen
   dann früher auf breiterer Grundlage. Leer lässt die app Tessie oder `home_entity` gelten (Abschnitt
   „Ablauf und Komponente“). Puffer und Ziel-SOC-Bereich lässt du zunächst bei den Standardwerten.
6. **Prüfen:** Auf **System** → „Komponenten“ meldet die Komponente `driving` nach dem Start
   „Fahrverhalten aktuell“ oder „Noch keine Fahrdaten“; der erste Nachtlauf ist um 03:40 Uhr. Willst du
   nicht warten, drückst du als Admin auf **Prognosen** → „Jetzt neu berechnen“. Die Karte
   „Fahrverhalten“ zeigt je Auto die Quelle, die Tage und die Konfidenz.
7. **Automatik bewusst einschalten:** Je Auto erst, wenn die Vorschläge auf „Prognosen“ mindestens die
   Konfidenz „mittel“ haben und du sie für richtig hältst: **Laden** → Fahrzeugkarte → „Ziel und Abfahrt
   automatisch“ (oder in Home Assistant „Automatik Ziel und Abfahrt“). Bis dahin setzt nur du Ziel und
   Abfahrt. Von Hand geänderte Werte gehen immer vor (Pause der Automatik, oben).
8. **Abnahme:** 28 Tage ab dem Ausrollen, Checkliste `docs/verification/abnahme-phase-5b.md`.

### Abnahme

Bestanden ist sie nach 28 Tagen ab dem Ausrollen, wenn (1) für jedes Auto mit Daten Vorschläge mit
Konfidenz auf „Prognosen“, „Laden“ und in Home Assistant erscheinen, (2) für jedes Auto mit
mindestens 10 bewerteten Tagen die Trefferquote der Abfahrt und die des Ziels je mindestens 80 %
betragen (`quality` in `GET /api/learning/driving`, Karte „Fahrverhalten“), (3) mit eingeschalteter
Automatik keine Zeile „geändert“ durch die Automatik in „Geplante Ladungen“ entsteht und sich während
einer laufenden geplanten Ladung nichts ändert und (4) ein erwartetes Auto im Zeitplan steht und beim
Anstecken durch das echte ersetzt wird (einmal an der Anlage beobachtet). Die Checkliste führt die
Schritte auf.

## Planung

Seit Plan 3b plant die app den Hausakku für bis zu 36 h. Seit Plan 3c übergibt sie den Plan in der
Absicht an den core (unten, „Übergabe an den core“), und der core führt ihn aus, solange der
Regelbetrieb an ist, in der Betriebsart, die Victron hat: im Dry-Run rechnet er ihn nur mit, live
stellt er den Akku danach (Abschnitt „Akku und Spitzen“, „Absicht des Plans“). Ob das gerade so
ist, sagt der **Stand der Ausführung** (unten). Pläne entstehen etwa alle 15 min; jeder wird mit
seinen Eingangsdaten in SQLite gespeichert (`plan` und `plan_inputs`, die Migration `0009` legt
beide Tabellen beim Start der app an). Seit Plan 4b stehen die angesteckten Autos im Modus „Geplant“
mit im Plan (unten, „Autos im Plan“).

### Die Seite „Zeitplan“

Die Seite **Zeitplan** steht in der Navigation nach „Laden“ und zeigt den letzten Plan:

- **Kopf:** zuerst der Satz zum Stand der Ausführung (unten; ohne gültigen Plan steht keiner), dann
  „Plan von HH:MM Uhr“, die Quelle („Optimierer“ oder „Ersatzplan“, beim Ersatzplan mit
  „Grund: …“), „gültig bis HH:MM Uhr“ oder „Plan abgelaufen“, „Anlass“ (der Auslöser, unten), die
  „Erwarteten Kosten“ des Plans und, daneben, was „Victron allein“ im selben Zeitraum kostet. Mit
  einem Leistungspreis kommt das „Budget“ dazu (unten). Unter „Auffälligkeiten“ stehen die Befunde
  der Prüfung eines unplausiblen Plans. Ohne Plan steht „Noch kein Plan.“ und der Status.
- **Bedienung:** Ab „Bedienen“ gibt es den Schalter „Netzladen erlaubt“ und die Taste „Neu planen“;
  „Lesen“ sieht den Stand des Schalters als „ja“ oder „nein“. Während eines Laufs steht „Plant …“.
  Nach dem Schalten wartet die Seite auf den Plan, den die geänderte Einstellung auslöst (alle 2 s
  nachgefragt, höchstens 40 s), und zeigt bis dahin „Plant …“.
- **Gültigkeit:** Ob ein Plan noch gilt, sagt allein die app (`valid` in der Antwort). Die Uhr des
  Browsers zählt nicht: Eine falsche Uhr am Handy zeigt so nie dauerhaft „Plan abgelaufen“.
- **Diagramme:** „Preise und Prognosen“ mit den Reihen „Bezugspreis“, „Bezugspreis geschätzt“,
  „PV-Prognose“ und „Lastprognose“; „Akku“ mit dem „Geplanten SOC“, der „Reserve“ und den Fenstern
  „Netzladen“ und „Halten“.
- **Ablauf:** Aufeinanderfolgende Slots mit gleicher Absicht, gleicher Netzladeleistung und gleichem
  Hauptcode bilden einen Block: „HH:MM–HH:MM“, die Absicht („Eigenverbrauch“, „Halten“,
  „Netzladen {kW} kW“), „SOC a → b %“ und der Satz der Erklärung (unten).
- **Plan gegen Ist (heute):** die vergangenen Viertelstunden des Tages, Plan neben Abrechnung
  (unten, „Plan gegen Ist“).

### Wie ein Plan entsteht

Jeder Lauf friert zuerst die Eingangsdaten ein, löst dann das Modell und prüft das Ergebnis:

1. **Eingangsdaten:** Der Horizont beginnt mit dem aktuellen Slot. Er reicht über die bekannten
   Preise (Herkunft `primary` oder `fallback`, ohne Lücke) und höchstens 36 h (144 Slots). Sind
   weniger als 16 Slots (4 h) bekannt, ergänzen Schätzpreise (Abschnitt „Prognosen“) lückenlos bis
   16 Slots; sie sind als „geschätzt“ gekennzeichnet. Der Horizont endet vor dem ersten Slot ohne
   PV- oder Lastprognose. Hat er weniger als 4 Slots oder fehlt der Preis des aktuellen Slots, gibt
   es keinen Plan. Ohne Einspeisepreis (kein OeMAG-Wert) rechnet das Modell mit 0 ct. Dazu kommen
   der SOC und die Grenzen des Akkus, die Monatsspitze, die Einstellungen „Planung“ und die
   geplanten Autos aus Health (unten, „Autos im Plan“). Der
   **Restwert** der Energie im Akku am Horizontende ist der Median der Bezugspreise der folgenden
   24 h mal dem Wirkungsgrad der Entladung, abzüglich Verschleiß und mal dem Vorsichtsfaktor; ohne
   Preise dort 0.
2. **Optimierer:** ein gemischt-ganzzahliges lineares Modell, gelöst mit HiGHS über PuLP, **in einem
   eigenen Kindprozess** je Lauf. HiGHS hat ein Zeitlimit von 20 s und ein MIP-Gap von 0,5 %; nach
   30 s beendet die app den Prozess. Das Modell minimiert die Kosten aus Bezug und Einspeisung, den
   Verschleiß der Entladung und die Marge beim Netzladen, bewertet den Restwert und bestraft in den
   ersten vier Slots jede Abweichung der Akkuleistung vom vorigen Plan (**Planträgheit**). Die PV
   deckt zuerst das Haus (der Anteil ist `min(PV, Last)`), erst der Rest lädt den Akku oder geht ins
   Netz. Aus dem Akku wird nie ins Netz eingespeist. Mit einem Leistungspreis im Monat (Abschnitt 8)
   gehört die Monatsspitze ins Ziel; ohne Satz bleibt sie draußen.
3. **Absichten und Nachrechnung:** Aus den Flüssen des Modells wird je Slot eine Absicht, und
   ein Akkumodell im Viertelstundenraster rechnet SOC, Flüsse und Kosten nach, so wie der core die
   Absichten später umsetzt. `Netzladen` wird es, wenn das Modell mindestens 100 W aus dem Netz in den
   Akku lädt (die Leistung auf 100 W gerundet). `Halten` wird es, wenn der Eigenverbrauch mindestens
   100 W entladen würde und das Modell weniger als die Hälfte davon entlädt. Sonst bleibt es
   `Eigenverbrauch`. Ein `Netzladen`, das sich an der Nachrechnung nicht rechnet (Spread-Regel
   unten), streicht die app wieder; der Slot wird dann `Halten` oder `Eigenverbrauch` nach derselben
   Regel. Die Zahlen des Plans (SOC, Kosten, Budget) stammen aus der Nachrechnung.
4. **Prüfung:** Ein unplausibler Plan wird verworfen, und es gilt der Ersatzplan.

**Regel R** („nicht einspeisen und danach billig kaufen“) ist im Optimierer eine harte Bedingung
(seit Plan 3d nur, bis der Akku wieder leer ist): Lädt er in einem Slot aus dem Netz, speist die PV
laut Prognose nicht ein, bevor der Akku wieder leer ist (SOC am Slotende höchstens Reserve plus
0,05 kWh), denn sonst füllte die PV den Akku ohnehin. Ist der Akku vor der nächsten Einspeisung
verbraucht, darf er laden: Ist es heute trüb und morgen sonnig, lädt er heute, was bis morgen
verbraucht ist, aber nicht mehr. Die Prüfung rechnet Regel R an der Nachrechnung nach, vom
`Netzladen`-Slot bis zum Ende des ersten Slots mit SOC höchstens Reserve plus 0,1 kWh; die 0,1 kWh
Einspeisung, die sie in diesem Fenster zulässt, sind nur ihre Toleranz. Weil die Absichten gerundet
sind, kann die Nachrechnung mehr im Akku haben als das Modell; würde sie deshalb nach einem
`Netzladen`-Slot einspeisen, bevor der Akku leer ist, kürzt die app das Netzladen des letzten
betroffenen Slots um diese Energie, höchstens aber um die Ladung, die vor der ersten Einspeisung
noch über Reserve plus 0,1 kWh liegt (auf 100 W aufgerundet, unter 100 W wird daraus `Halten`).
Die Regel wirkt erst, wenn Victron live ist und „Netzladen erlaubt“ an ist.

Die **Spread-Regel** ist im Optimierer keine Bedingung: Marge, Verschleiß und Wirkungsgrad stehen
als Kosten im Ziel. Die Prüfung kontrolliert sie danach je `Netzladen`-Slot. Er rechnet sich, wenn
ein späterer Slot mit Entladung (mehr als 1 W) `η_rt · (π_später − w) − π_jetzt ≥ m` erfüllt
(Roundtrip-Wirkungsgrad `η_rt`, Verschleiß `w`, Marge `m`; je kWh aus dem Netz kommen `η_rt` kWh
zurück, und der Verschleiß fällt wie im Optimierer auf diese Entladung). Er rechnet sich auch, wenn
er **für die Zeit nach dem Horizont** lädt: Die geladene Energie ist am Horizontende noch im Akku
(der End-SOC der Nachrechnung liegt mindestens um sie über der Reserve), und
`η_ch · Restwert − π_jetzt ≥ m` (Wirkungsgrad des Ladens `η_ch`; der Restwert enthält den
Verschleiß schon). So lädt der Optimierer billige Stunden kurz vor dem Horizontende für den Abend
danach. Mit einem Leistungspreis im Horizont entfällt die Spread-Regel, denn dann darf Netzladen
auch einer Spitze ausweichen.

Seit dem 18.10.2026 rechnen Absichten und Prüfung die Spread-Regel gleich (entschieden vom
Betreiber): Auch kleine spätere Entladungen zählen, denn der Optimierer verteilt die geladene
Energie manchmal auf mehrere teure Slots mit je unter 100 W Fehlbetrag. Und ein `Netzladen`, das
sich an der Nachrechnung nicht rechnet, streichen schon die Absichten. Das passiert, wenn ein
billiger Slot nach dem Laden wegen der Rundung `Eigenverbrauch` wird und die Energie gleich wieder
verbraucht. So verwirft die Prüfung keinen Plan des Optimierers mehr wegen der Spread-Regel. Sie
prüft die Regel aber weiter: Gelangt doch ein `Netzladen`, das sich nicht rechnet, in die Absichten,
gilt der Ersatzplan.

Die Regel ist nicht mengenbezogen: Schon eine lohnende spätere Entladung über 1 W rechtfertigt jede
Ladeleistung. Ein `Netzladen`, das fast ganz sofort verbraucht wird und nur einen kleinen Rest für
später lässt, bleibt deshalb bestehen; wirtschaftlich ist das etwa ±0.

### Autos im Plan

Seit Plan 4b plant die app die Autos im Modus „Geplant“ (Abschnitt „Laderegelung“, „Geplant“)
gemeinsam mit dem Akku. Der core folgt der Ladeleistung, die der Plan für ein Auto im laufenden Slot
nennt.

- **Welche Autos:** jedes Fahrzeug, das Health in „Geplant“ angesteckt an einer Wallbox der
  Konfiguration zeigt (`mode` `planned`, `plugged`, dazu `planned`), wenn die app Health traut
  (`intent_version` erreicht die Version der gültigen Absicht) und die Abfahrt nicht vor dem Ende der
  laufenden Viertelstunde liegt. Ladestand und seine Quelle, Ladelimit, wirksames Ziel und Abfahrt
  kommen aus Health, Wirkungsgrad, Kapazität und Ladeleistungen aus `config.yaml`. Ein Auto, das
  noch nicht steckt, plant die app als erwartetes Auto (nächster Punkt) oder, ohne Prognose, erst nach
  dem Anstecken (Auslöser `vehicles`, unten). Autos in den anderen Modi bleiben wie bisher außerhalb.
- **Erwartete Autos** (seit Plan 5b, Abschnitt „Fahrverhalten (Phase 5b)“): Ein Auto in „Geplant“,
  das Health als nicht angesteckt zeigt (`plugged` genau `false`), kommt in den Plan, wenn es eine
  `default_wallbox` hat, die laut Health frei ist (kein anderes angestecktes Auto, kein Gast, kein
  Vorgang mit unklarer Zuordnung), ein Profil mit gelerntem Ladestand bei der Ankunft da ist und die
  Anwesenheitsprognose für eine Viertelstunde vor seiner Abfahrt mindestens 50 % sagt. Seine
  Ladeleistung ist erst ab dieser erwarteten Ankunft (`arrival_at`) bis zur Abfahrt frei; Regel R2
  zählt die PV ebenfalls erst ab dann. Der Ladestand ist der gelernte Median der Ankunft des
  Wochentags (nie „angenommen“). **Der core bekommt für ein erwartetes Auto nichts:** `intent_plan`
  lässt es weg, das Budget des Wächters (`budget_kw`) rechnet ohne seine Ladeleistung (eine Prognose
  hebt die akzeptierte Monatsspitze nicht an), und der Ersatzplan, die Warnungen („Ziel-SOC unerreichbar“), „Fertig um“ in Home
  Assistant und die Zählung der geplanten Ladungen übergehen es. Steckt das Auto an, plant der Auslöser
  `vehicles` neu mit dem echten Ladestand; ändert sich die Menge der erwarteten Autos (Ankunft,
  Abfahrt, Ziel), löst das ebenfalls aus. Auf der Seite „Zeitplan“ steht es gestrichelt mit „{Name}
  erwartet ab {HH:MM}“.
- **Im Modell** hat jedes Auto je Slot bis zur Abfahrt eine Ladeleistung von 0 oder zwischen seiner
  kleinsten (`min_a` · `grid.voltage_nominal_v` · Phasen) und seiner vollen Leistung (Abschnitt
  „Laderegelung“, „Geplant“), aus PV und Netz, nie aus dem Akku. Die PV deckt zuerst das Haus, dann
  die Autos, dann den Akku: Solange ein Auto aus dem Netz lädt, lädt die PV den Akku nicht und speist
  nicht ein; sonst könnte der Plan Netzstrom über das Auto in den Akku schieben, an Regel R und
  „Netzladen erlaubt“ vorbei. Haus, Akku und Autos zusammen beziehen höchstens die Grenze des
  Netzbezugs. Die Netzenergie der Autos kostet den Bezugspreis, PV-Strom ins Auto nichts. Die
  Planträgheit gilt in den ersten vier Slots auch für die Ladeleistung jedes Autos. Ein Auto lädt
  höchstens seinen Bedarf (oder eine Viertelstunde mit der kleinsten Leistung, wenn das mehr ist).
- **Bedarf und Fehlmenge:** Der Bedarf ist der aus Health, an der Wallbox gerechnet (der Wirkungsgrad
  steckt darin). Liegt die Abfahrt hinter dem Horizont, muss im Horizont nur der Teil geladen werden,
  der danach mit voller Leistung nicht mehr ginge: `max(0, Bedarf − volle Leistung · Stunden zwischen
  Horizontende und Abfahrt)`. Was der Plan nicht laden kann, ist **Fehlmenge**, bestraft mit 2000 ct
  je kWh; so hat das Modell auch bei einem unerreichbaren Ziel eine Lösung, und der Plan sagt
  „nicht erreichbar“.
- **Regel R2** (Regel R für die Autos): Ein Auto darf bis zur Abfahrt nur so viel aus dem Netz laden,
  wie der PV-Überschuss nach der vorsichtigen Prognose P25 (`max(0, P25 − Last)`) in den Slots vor
  seiner Abfahrt nicht deckt. Den Überschuss bekommen die Autos in der Reihenfolge `priority`, je Slot
  höchstens, was ein Auto mit voller Leistung laden kann, jedes von den letzten Slots vor seiner
  Abfahrt rückwärts (so bleibt früher Überschuss für ein Auto, das früher abfährt). Mehr Netzenergie
  kostet 200 ct je kWh. Die Rangfolge ist damit: Ziel vor Regel R vor Kosten. Sofort, Boost und
  Min+PV stehen nicht im Modell und unterliegen R2 nicht.
- **Nachrechnung:** wie der core: PV zuerst ins Haus, dann in die Autos, dann in den Akku; der Akku
  entlädt nur für das Haus, was den Autos fehlt, kommt aus dem Netz.
- **Je Auto** fasst der Plan zusammen (`plan.vehicles`, unten „API“): Ziel, Abfahrt, Ladestand zu
  Beginn und ob er angenommen ist, Ladelimit, Bedarf, geplante Energie (an der Wallbox), Fehlmenge,
  den erwarteten Ladestand an der Abfahrt (`Ladestand + geplante Energie · Wirkungsgrad /
  usable_kwh · 100`, höchstens 100 %), „Fertig um“ (Ende der letzten Viertelstunde, in der der Plan das
  Auto mit mindestens 1 W lädt; ohne Laden leer) und „erreichbar“ (Fehlmenge höchstens 0,1 kWh). UI
  und Home Assistant zeigen daraus „Fertig um“ und „Ziel erreichbar“ (Abschnitte 7 und 12).
- **Abfahrt hinter dem Horizont:** Der erwartete Ladestand zählt dazu, was der core nach dem Horizont
  mit voller Leistung bis zur Abfahrt noch lädt (höchstens den Rest des Bedarfs), denn er gilt an der
  Abfahrt; deckt das den Bedarf, ist er das Ziel. „Fertig um“ ist leer, solange der Plan den Bedarf
  nicht schon im Horizont lädt.
- **An den core** geht je Slot die Ladeleistung je Auto in ganzen Watt (`vehicles` im Slot): jedes
  Auto des Plans in jedem Slot vor seiner Abfahrt, auch mit 0 W. Ein Auto, das ein gültiger Slot
  nicht nennt, kennt der Plan nicht; der core lädt es nach der spätesten Startzeit. Der core lehnt
  negative Werte und unbekannte Fahrzeuge ab und begrenzt auf die Höchstleistung des Fahrzeugs
  (Abschnitt „Laderegelung“, „Absicht per PUT /v1/intent“).
- **Prüfung:** Die Bilanz zählt die Autos mit; je Auto ist die Leistung 0 oder zwischen kleinster und
  voller Leistung (1 W Toleranz), und nach der Abfahrt lädt kein Auto (Befunde unten, „Ersatzplan“).

### Netzladen erlaubt

Der Schalter **Netzladen erlaubt** ist standardmäßig aus. Aus heißt: Kein Plan enthält `Netzladen`;
wo es sich gelohnt hätte, sagt der Satz „Netzladen würde sich lohnen … ist aber ausgeschaltet.“
Umschalten kannst du an drei Stellen, mit denselben Folgen:

- im UI auf der Seite **Zeitplan** (ab „Bedienen“),
- in Home Assistant mit dem Schalter „Netzladen erlaubt“ (Quelle `ha`),
- im Editor der **Einstellungen** unter „Planung“ (Admins).

Jedes Umschalten ist eine neue Version der Einstellungen mit dem Kommentar „Netzladen ein“ bzw.
„Netzladen aus“ im Änderungsprotokoll und löst eine Neuplanung aus. Hat der Schalter schon den Wert,
entsteht keine neue Version. Der Plan geht danach neu an den core; bis er dort ist, steht der Stand
„Der Plan ist noch nicht beim core.“ (unten, „Stand der Ausführung“). Beim Ausschalten geht sofort
derselbe Plan ohne `Netzladen` an den core, diese Slots werden `Eigenverbrauch`; der neue Plan
folgt (unten, „Übergabe an den core“). Regelt der core in dem Moment nicht (Regelbetrieb aus,
Neustart), wartet die Übergabe, bis er wieder regelt; ein gespeicherter `Netzladen`-Slot kann dann
noch etwa 11 s laufen (eine Abfrage der Health und ein Regelzyklus).

### Übergabe an den core

Die app schickt den Plan als Teil der Absicht (Abschnitt 13, „Absicht und Regelbetrieb“), mit einer
neuen Version, aber ohne Eintrag im Änderungsprotokoll: Bei rund 100 Plänen am Tag ist das Archiv der
Pläne der Nachweis (Akteur `planung`, Quelle `system`). Eine Bedienung behält den Plan der gültigen
Absicht, und ein neuer Plan wird wie eine Bedienung aus der gültigen Absicht abgeleitet (Boost und
Übersteuerung nach den Regeln aus Abschnitt 13).

- **Was hinausgeht:** nur der bindende Teil des neuesten gültigen Plans, die Slots vor seinem
  `valid_until` (höchstens 8), je Slot Absicht, Budget, Bezugspreis und Plan-Schwelle, dazu `id`,
  `created_at`, `valid_until` und der Stand von „Netzladen erlaubt“. Ist der Schalter aus, werden
  `Netzladen`-Slots zu `Eigenverbrauch`.
- **Wann:** beim Start, nach jedem Planungslauf, nach jeder Verbindung zum core, nach jeder Abfrage
  von Health (alle 10 s), nach jeder Änderung der Einstellungen und sonst alle 30 s, solange der core
  diesen Plan nicht hat. Er hat ihn, wenn `intent.plan` in seinem Health dieselbe `id` und denselben
  Stand von „Netzladen erlaubt“ trägt. Ohne gültigen Plan schickt die app nichts.
- **Nur, wenn der core mit der Absicht regelt:** Die app sendet erst, wenn Health zeigt, dass der
  letzte Regelzyklus mit der gültigen Absicht oder einer neueren regelte (`intent_version` nicht
  kleiner als `intent.version`). Ohne Health, nach einem Neustart des core bis zu seinem ersten
  regelnden Zyklus, in `OBSERVE` oder `SAFE` und bei Regelbetrieb aus wartet der Plan; das Log
  nennt es einmal („Plan wartet auf Health des core“, „Plan wartet, bis der core mit der Absicht
  regelt“). Grund: Jede neue Version bindet Boost und Übersteuerung im core neu (Abschnitt
  „Laderegelung“, „Absicht per PUT /v1/intent“); auf einem Stand ohne Regelung bände sie nach einem
  Neustart einen wiederhergestellten oder schon verbrauchten Boost an das nächste Anstecken. Ein
  core, der so nicht regelt, führt ohnehin keinen Plan aus. Bricht die Verbindung ab, verwirft die
  app das alte Health; der Plan folgt dem ersten passenden Health nach der Verbindung, binnen einer
  Abfrage.
- **Prüfung im core** (Abschnitt „Laderegelung“, „Absicht per PUT /v1/intent“): Er lehnt einen Plan
  mit Lücken, falschem Raster, mehr als 8 Slots, unpassender Gültigkeit oder `Netzladen` trotz
  ausgeschaltetem Schalter ab (422) und klemmt `valid_until` auf 45 min nach der Annahme und die
  Netzladeleistung auf `victron.max_grid_charge_setpoint_w`. Einen Plan, der schon abgelaufen ist,
  nimmt er an; er wirkt nicht. Ob ein Plan gilt, prüft der core auf seiner eigenen Uhr; ohne
  gültigen Slot regelt er den Eigenverbrauch.
- **Log:** `docker compose logs --since 1h ems-app | grep "Plan an den core übergeben"` zeigt jede
  Übergabe mit `plan`, `result` und `version`; „Plan vom core abgelehnt“ nennt die Gründe, „Plan
  nicht übergeben: core nicht erreichbar“ einen Versuch ohne Verbindung. Der nächste Anstoß
  versucht es neu.

### Stand der Ausführung

Die Zeile oben im Kopf der Seite „Zeitplan“ sagt, was der core mit dem gültigen Plan macht. Die app
leitet den Stand aus dem Health des core ab; der erste zutreffende der Liste gilt:

| Stand | Satz | Wann |
|---|---|---|
| `none` | (keiner) | es gibt keinen gültigen Plan |
| `unreachable` | Der core ist nicht erreichbar; er führt den Plan nicht aus. | die app ist nicht mit dem core verbunden |
| `pending` | Der Plan ist noch nicht beim core. | die app hat noch kein Health des core (etwa direkt nach der Verbindung) |
| `off` | Regelbetrieb ist aus: Der core führt den Plan nicht aus. | der Schalter „Regelbetrieb“ steht auf aus, oder der core hat keine Absicht |
| `idle` | Der core regelt gerade nicht ({Betriebszustand}). | der core steht weder in `CONTROL` noch in `DEGRADED`, sondern etwa in `START`, `OBSERVE` oder `SAFE` |
| `pending` | Der Plan ist noch nicht beim core. | der Plan der Absicht im core hat nicht dieselbe `id` und denselben Stand von „Netzladen erlaubt“ wie der Plan der app (zum Beispiel direkt nach einer Neuplanung oder dem Umschalten; die app sendet ihn gleich, sobald der core mit der Absicht regelt), oder er gilt beim core nicht (Health `plan.state` nicht `valid`, etwa bei einer Abweichung der Uhren) |
| `locked` | Victron ist gesperrt: Der core führt den Plan nicht aus. | Victron hat Sperrgründe (Abschnitt „Akku und Spitzen“) |
| `dry_run` | Dry-Run: Der core rechnet den Plan mit und sendet nichts an Victron. | die wirksame Betriebsart von Victron ist nicht „live“ |
| `live` | Der core führt den Plan aus. | alles trifft zu |

`{Betriebszustand}` ist der Zustand des core, etwa `SAFE`. Außer bei `live` ist die Zeile in der
Warnfarbe. In Home Assistant steht der Schlüssel der ersten Spalte im Attribut `execution` der
Plan-Sensoren (Abschnitt „Home Assistant“). Die Anzeige schaltet nichts: Der Plan wirkt nie ohne
Regelbetrieb und nie in einer anderen Betriebsart, als Victron hat (Abschnitt „Akku und Spitzen“).

### Einstellungen „Planung“

Die Gruppe **Planung** in den Einstellungen (Admins bearbeiten) enthält:

| Einstellung | Feld | Standard | Bedeutung |
|---|---|---|---|
| `planning.grid_charge_allowed` | Netzladen erlaubt | aus | der Schalter oben |
| `planning.margin_ct` | Marge (ct/kWh) | 2,0 | Mindestgewinn je kWh, damit sich Netzladen lohnt; geht als Kosten ins Modell |
| `planning.roundtrip_pct` | Roundtrip-Wirkungsgrad (%) | 88 | Laden und Entladen zusammen; je Richtung die Wurzel daraus |
| `planning.wear_ct` | Verschleiß (ct/kWh) | 2,0 | Kosten je entladener kWh |
| `planning.end_value_factor` | Vorsichtsfaktor Restwert | 0,9 | bewertet die Energie im Akku am Horizontende (oben) |
| `planning.inertia_ct` | Planträgheit (ct/kWh) | 0,5 | Kosten je kWh Abweichung der Akkuleistung vom vorigen Plan, in den ersten vier Slots |

Das Feld prüft: „Zahl ab 0, z. B. 2,0“ (Marge, Verschleiß, Planträgheit), „Zahl von 50 bis 100“
(Roundtrip) und „Zahl von 0 bis 1, z. B. 0,9“ (Vorsichtsfaktor). Gespeicherte Einstellungen ohne
Gruppe „Planung“ (aus der Zeit vor Plan 3b) laden mit den Standardwerten. Eine Änderung von
Planung, Tarif oder Einspeisung löst eine Neuplanung aus, eine andere Einstellung nicht.

### Auslöser

Die app plant beim Start und danach auf diese Auslöser; die Seite zeigt sie als „Anlass“:

| Auslöser | Anzeige | Wann |
|---|---|---|
| `start` | Start | beim Start der app |
| `slot` | Viertelstunde | eine Minute nach jedem Slotbeginn (HH:01, HH:16, HH:31, HH:46) |
| `prices` | neue Preise | ein neuer letzter bekannter Preis-Slot oder mehr `primary`-Slots in den nächsten 48 h; weniger lösen nicht aus, denn das Fenster verliert mit jeder Viertelstunde einen |
| `pv_forecast` | neue PV-Prognose | die PV-Prognose hat eine neue Ausgabe |
| `load_forecast` | neue Lastprognose | die Lastprognose hat eine neue Ausgabe |
| `soc` | SOC weicht ab | der SOC liegt mehr als 5 Prozentpunkte neben dem Plan (linear im Slot gerechnet); nach einem Plan mit diesem Anlass im selben Slot nicht erneut |
| `month_peak` | neue Monatsspitze | die Monatsspitze liegt mehr als 0,05 kW über der, die der Plan kennt |
| `settings` | Einstellungen geändert | Planung, Tarif oder Einspeisung wurden geändert, oder die wirksame Unterstützungsschwelle (Einstellung, Bedienung, Home Assistant) |
| `manual` | Neu planen | die Taste im UI oder in Home Assistant, oder `POST /api/plan/replan` |
| `vehicles` | Fahrzeug geändert | bei einem Auto im Modus „Geplant“ ändern sich Ein- oder Ausstecken, Wallbox, Ziel, Abfahrt, „Ladestand angenommen“ oder das Ladelimit des Tesla, oder ein Auto kommt in „Geplant“ oder verlässt den Modus (ändern sich nur das Ladelimit oder die Quelle des Ladestands, plant die app höchstens alle 5 min neu; Ziel, Abfahrt und die Menge der geplanten Autos lösen sofort aus); außerdem, sobald Health eine neue Absicht bestätigt, wenn der letzte Lauf davor ein Auto in „Geplant“ nicht sehen konnte |

Nach jeder neuen Absicht (ein gesendeter Plan, eine Bedienung) regelt der core ab seinem nächsten
Zyklus mit ihr. Die app sieht das erst im nächsten Health, das sie abfragt (`intent_version`, bis zu
10 s später); bis dahin traut sie der Absicht nicht, und ein Lauf in diesem Fenster plant ohne die Autos
in „Geplant“. Bestätigt Health die Absicht, plant die app mit `vehicles` neu, auch wenn sich bei den
Autos nichts geändert hat.

Preise, Prognosen, SOC und Monatsspitze prüft die app alle 30 s. Es läuft immer nur ein Lauf; was
währenddessen eintrifft, ergibt genau einen weiteren. Ein Plan **gilt 45 min**. Fehlen Eingangsdaten,
entsteht kein neuer Plan, nur ein Status; der letzte Plan bleibt bis zu seinem Ende sichtbar.

Die Komponente `planning` auf der Seite **System** meldet:

| Status | Bedeutung |
|---|---|
| „Plan von HH:MM Uhr“ | ein gültiger Plan des Optimierers |
| „Ersatzplan von HH:MM Uhr: {Grund}“ | der Plan ist ein Ersatzplan (unten) |
| „Plan abgelaufen seit HH:MM Uhr“ | der letzte Plan gilt nicht mehr, ein neuer fehlt |
| „Kein Plan: {Grund}“ | „kein SOC vom core“, „kein Preis für den aktuellen Slot“, „keine PV-Prognose“, „keine Lastprognose“ oder „zu wenig Daten für einen Plan“ |
| „Noch kein Plan“ | vor dem ersten Lauf |

### Ersatzplan

Scheitert der Solver oder besteht der Plan die Prüfung nicht, gilt ein **regelbasierter Ersatzplan**.
Die Seite und die Komponente nennen den Grund:

| Grund | Anlass |
|---|---|
| „Zeitlimit ohne Lösung“ | HiGHS fand in 20 s keine Lösung (mit einer Lösung gilt das Ergebnis) |
| „Solver ohne Lösung (infeasible)“ | das Modell hat keine Lösung |
| „Solver nach 30 s abgebrochen“ | der Kindprozess antwortete nicht; die app hat ihn beendet |
| „Solver fehlgeschlagen: …“ | der Kindprozess meldete einen Fehler (der Fehler steht dahinter) |
| „Plan unplausibel: {Befund}“ | die Prüfung fand einen Fehler; der erste Befund steht im Grund, alle unter „Auffälligkeiten“ |

Die Prüfung findet diese Befunde, jeweils mit der Uhrzeit des Slots (je Art der erste, in der
Reihenfolge der Slots; der früheste ist der „erste Befund“): „Bilanz verletzt um HH:MM“,
„SOC außerhalb der Grenzen um HH:MM“, „Leistung über der Grenze um HH:MM“, „Netzladen um HH:MM,
obwohl ausgeschaltet“, „Regel R verletzt: Netzladen um HH:MM, danach {kWh} kWh Einspeisung,
bevor der Akku leer ist“ und „Netzladen um HH:MM rechnet sich nicht“, mit Autos (seit Plan 4b)
dazu „Auto {Name} lädt außerhalb seiner Grenzen um HH:MM“ und „Auto {Name} lädt nach der
Abfahrt“, mit erwarteten Autos (seit Plan 5b) „Auto {Name} lädt vor der erwarteten Ankunft um HH:MM“
(die Uhrzeit ist die erwartete Ankunft).

Der Ersatzplan plant zuerst die Autos im Modus „Geplant“, in der Reihenfolge `priority` (seit Plan
4b):

- **PV zuerst:** Ab dem aktuellen Slot lädt jedes Auto den erwarteten PV-Überschuss (P50 minus Last),
  den die Autos davor übrig lassen, bis der Bedarf gedeckt ist; ein Slot zählt nur, wenn der
  Überschuss die kleinste Leistung des Autos erreicht.
- **Dann die günstigsten Slots** vor der Abfahrt (bei gleichem Preis der frühere) mit voller Leistung,
  bis der Bedarf im Horizont gedeckt ist; der letzte lädt nur den Rest, mindestens die kleinste
  Leistung. Je Slot laden alle Autos zusammen höchstens die Grenze des Netzbezugs plus PV minus Haus.
  Was nicht passt, bleibt Fehlmenge.
- Regel R2 hält das meist von selbst ein, denn der Überschuss nach P50 ist mindestens der nach P25.
  Ausnahme: Einen Überschuss unter der kleinsten Leistung des Autos zählt Regel R2 als PV für das
  Auto, der Ersatzplan lädt dort aber nicht. Bei vielen solchen Slots kann er deshalb mehr aus dem
  Netz laden, als Regel R2 erlaubt; das ist ein bekannter offener Punkt.

Den Akku rechnet der Ersatzplan mit diesen Autos so:

- **Grundlage:** `Eigenverbrauch` in allen Slots, nachgerechnet.
- **Teuer** sind die Slots des heutigen Tages ab dem aktuellen Slot, deren Bezugspreis das 80. Perzentil
  dieser Slots erreicht. Bei einem einheitlichen Preis gibt es keinen. Der SOC **reicht nur knapp**,
  wenn die Nachrechnung in einem teuren Slot mehr als 100 W aus dem Netz bezieht, weil der Akku leer
  ist.
- **Halten:** Der Plan hält den Akku in dem nicht teuren, entladenden Slot mit dem niedrigsten Preis vor
  dem ersten knappen teuren Slot zurück und rechnet neu, bis der teure Slot gedeckt ist oder kein
  Kandidat bleibt. Dann folgt der nächste knappe teure Slot.
- **Netzladen:** nur bei eingeschaltetem Schalter, nur gegen den ersten teuren Slot, der trotz
  Halten knapp bleibt, und nur in einem billigen Slot davor, für den die Spread-Regel mit dem Preis
  des teuren Slots und Regel R (Einspeisung nur, bis der Akku leer ist) gelten; Regel R hält dabei
  auch jedes schon geplante Netzladen. Die Leistung ist die höchste volle 100 W unter der Grenze
  des Slots; neben Haus und Autos bleibt dabei die Grenze des Netzbezugs frei.
- **Budget:** wie beim Optimierer (unten, „Budget“), ohne die Spitze des Optimierers.

### Erklärungen

Jeder Slot trägt **Grundcodes**; das UI und die Sensoren in Home Assistant zeigen daraus deutsche
Sätze, mehrere durch ein Leerzeichen getrennt. In einem Ersatzplan steht `ERSATZPLAN` an erster
Stelle, bei einem Schätzpreis `GESCHAETZT` an letzter. Zahlen haben ein Dezimalkomma (`kW`, `ct`
und `kWh` mit einer Nachkommastelle, `%` ganzzahlig), Uhrzeiten sind lokal. Der Hauptcode eines
Slots folgt der ersten passenden Regel, in dieser Reihenfolge der Prüfung (`GESCHAETZT` und
`ERSATZPLAN` am Ende sind Zusätze, keine Hauptcodes); die Beispiele zeigen den Satz mit erfundenen
Zahlen:

| Code | Beispiel | Wann |
|---|---|---|
| `NETZLADEN_GUENSTIG` | Akku lädt 4,0 kW aus dem Netz: jetzt 12,3 ct, um 18:00 Uhr 31,5 ct. | Absicht `Netzladen`; „um“ ist der spätere Slot, in dem sich das Laden auszahlt |
| `HALTEN_FUER_SPITZE` | Akku hält 64 %, weil 18:00–20:00 Uhr 38,0 ct erwartet werden. | Absicht `Halten`; später folgt ein Block teurerer Slots mit Entladung |
| `BUDGET_SPITZE` | Akku hält 55 %, damit die Monatsspitze unter 6,5 kW bleibt. | Absicht `Halten`; später würde ohne den Akku das Budget überschritten |
| `HALTEN_RESTWERT` | Akku hält 40 % für die Zeit nach 10:00 Uhr. | Absicht `Halten` aus keinem der beiden Gründe; „nach“ ist das Horizontende |
| `PV_LADEN` | Akku lädt 3,2 kW aus PV-Überschuss. | Absicht `Eigenverbrauch`, der Akku lädt mit mindestens 100 W aus der PV |
| `REGEL_R_BLOCKIERT_NETZLADEN` | Kein Netzladen: Ab 17:00 Uhr speist die PV laut Prognose 8,4 kWh ein, bevor der Akku leer ist (Regel R). | Netzladen würde sich lohnen und ist erlaubt, aber die PV speist mehr als 0,1 kWh ein, bevor der Akku leer ist |
| `NETZLADEN_AUS` | Netzladen würde sich lohnen (jetzt 10,0 ct, um 18:00 Uhr 30,0 ct), ist aber ausgeschaltet. | Netzladen würde sich lohnen, der Schalter ist aus |
| `PV_UEBERSCHUSS_ERWARTET` | Akku deckt den Verbrauch; ab 09:30 Uhr lädt ihn die PV wieder. | Absicht `Eigenverbrauch`, der Akku entlädt, und später lädt die PV ihn |
| `EIGENVERBRAUCH` | Akku deckt den Eigenverbrauch. | sonst |
| `AUTO_ERWARTET` | Tesla Model 3 wird um 17:30 Uhr mit etwa 41 % erwartet (Anwesenheitsprognose). | Zusatz im Slot der erwarteten Ankunft, vor den übrigen Codes der Autos (seit Plan 5b): ein Auto in „Geplant“ steckt noch nicht, die Anwesenheitsprognose erwartet es ab diesem Slot mit mindestens 50 %; „etwa“ ist der gelernte Ladestand bei der Ankunft. Das Auto steht nur im Plan, der core bekommt dafür nichts |
| `ZIEL_SOC_ABFAHRT` | Tesla Model 3 lädt 7,0 kW, damit es um 07:00 Uhr 80 % hat. | Zusatz zum Hauptcode, je Auto in der Reihenfolge `priority`: ein Auto im Modus „Geplant“ lädt in diesem Slot mindestens 100 W aus dem Netz; „kW“ ist die Netzleistung, „um“ die Abfahrt |
| `AUTO_WARTET_AUF_PV` | e-Golf wartet auf PV-Überschuss (Regel R). | Zusatz: ein Auto im Modus „Geplant“ lädt in diesem Slot vor der Abfahrt nicht, weil Regel R2 das Netzladen begrenzt und ein späterer Slot es aus PV lädt |
| `ZIEL_SOC_UNERREICHBAR` | e-Golf erreicht bis 06:30 Uhr voraussichtlich nur 62 %. | Zusatz im ersten Slot: der Plan erreicht das Ziel eines Autos im Modus „Geplant“ nicht (Fehlmenge über 0,1 kWh); „nur“ ist der erwartete Ladestand an der Abfahrt; nicht für erwartete Autos |
| `UNTERSTUETZUNG_RESERVIERT` | Akku-Unterstützung fürs Auto erst ab 70 %, weil der Plan den Akku bis 18:00 Uhr braucht. | Absicht `Eigenverbrauch`; die Schwelle, die der Plan für spätere Entladungen braucht, liegt über der Unterstützungsschwelle; „bis“ ist der Beginn des ersten späteren Slots ohne diesen Bedarf |
| `GESCHAETZT` | Preis geschätzt. | der Preis des Slots ist ein Schätzpreis |
| `ERSATZPLAN` | Ersatzplan. | der Plan ist ein Ersatzplan |

**Lohnt sich** heißt: Ein späterer Slot im Horizont bezieht in der Nachrechnung mindestens 100 W aus
dem Netz und erfüllt die Spread-Regel mit dem Preis des Slots.

**Plan-Schwelle** (`support_soc_pct` am Slot, auch in `GET /api/plan`): der SOC, den der Plan für seine
späteren Entladungen noch braucht, zuzüglich der USV-Reserve und auf ganze Prozent aufgerundet,
höchstens 100 %. Sie wird rückwärts über den Horizont gerechnet: Der Bedarf eines Slots ist seine
Entladung in Prozentpunkten plus der Bedarf des nächsten Slots, mindestens 0; ein Slot, in dem die PV
den Akku wieder lädt, gibt den Bedarf davor frei. Liegt sie in einem `Eigenverbrauch`-Slot über der
Unterstützungsschwelle, mit der der Plan gerechnet wurde (Standard `control.support_soc_pct`),
steht dort `UNTERSTUETZUNG_RESERVIERT`.

### Budget

Mit einem Leistungspreis im Tarif (Abschnitt 8) hat der Plan je Monat ein **Budget**: die Monatsspitze,
die er akzeptiert. Es ist, auf 0,1 kW aufgerundet, das Größte aus

- der bisherigen Monatsspitze, mindestens der Mindestverrechnung (`minimum_kw`),
- der Spitze des Optimierers für den Monat (nur beim Optimierer, nicht im Ersatzplan),
- dem höchsten Netzbezug der Nachrechnung im Monat (so liegt das Budget nie unter dem Bezug, den
  der Plan selbst vorsieht, auch nicht bei `HALTEN` oder `Netzladen` im Ersatzplan) und
- `control.peak_target_kw`, wenn gesetzt.

Ist das 0 kW (am Monatsanfang mit `minimum_kw` 0 und ohne Bezug im Plan), gilt der kleinste
Ladestrom des größten Autos: das Größte aus `min_a · phases · grid.voltage_nominal_v` über die
Fahrzeuge in `config.yaml` (im Beispiel 3,5 kW für den Tesla). Darunter könnte der Wächter ein
ladendes Auto nur bis zu seinem Mindeststrom drosseln und alarmieren. Willst du eine andere
Untergrenze, setze `control.peak_target_kw` oder `minimum_kw` im Tarif. Ohne Leistungspreis ist das
Budget leer. Seit Plan 3c geht es mit dem Plan an den core, und der 15-min-Wächter hält es als Ziel,
solange der Slot gilt (Abschnitt „15-min-Wächter“).

### Plan gegen Ist

Die Karte **Plan gegen Ist (heute)** auf der Seite „Zeitplan“ vergleicht die vergangenen
Viertelstunden des Tages (Wiener Zeit) mit der Abrechnung. Je Viertelstunde zählt der Slot des Plans,
der damals galt: der neueste Plan, der vor ihrem Ende entstand, bei ihrem Beginn noch galt und sie
enthält.

- **Diagramm:** „Geplanter SOC“ (am Ende jeder Viertelstunde, aus dem Plan) und „Ist-SOC“ (aus dem
  Verlauf, `soc.battery`).
- **Tabelle:** „Netzbezug“, „Einspeisung“ und „Netzladen“ in kWh und „Bezugskosten“ in €, je mit
  „Plan“, „Ist“ und „Differenz“ (Ist minus Plan). Summiert werden nur Viertelstunden mit Plan und
  Abrechnung, die Bezugskosten nur über die mit bekanntem Bezugspreis (Bezug mal Bezugspreis, ohne
  Einspeisung und Leistungspreis). „Netzladen“ ist auf der Ist-Seite der Fluss vom Netz in den Akku.
- **Leer:** „Für heute gibt es noch keine Viertelstunde mit Plan und Ist.“, etwa kurz nach
  Mitternacht oder bevor der erste Plan entstand. Ohne Antwort steht „Plan gegen Ist ist nicht
  verfügbar.“

Plan und Ist weichen auch ohne Fehler ab: Die Prognosen treffen nie genau, und solange Victron im
Dry-Run ist, regelt der Akku nicht nach dem Plan. Im Dry-Run zeigt die Karte deshalb, wie gut die
Prognosen und das Akkumodell passen, live dazu, wie gut der core den Plan umsetzt.

### Home Assistant

Zum Plan meldet die app diese Entitäten am Gerät „BindaEMS“ (Abschnitt 7). Die Sensoren zeigen den
Plan nur, solange er gilt; sonst sind sie „unbekannt“. Die vier Sensoren `battery_plan_now`,
`battery_plan_next`, `planned_soc` und `budget` tragen immer das Attribut `execution`, den Stand der
Ausführung (oben, „Stand der Ausführung“) als Schlüssel: `none`, `unreachable`, `pending`, `off`,
`idle`, `locked`, `dry_run` oder `live`. Automationen sollen sich nur auf den Plan verlassen, wenn
`execution` den Wert `live` hat. Ist der Stand der Einstellung „Netzladen erlaubt“ noch unbekannt,
steht `execution` auf `null`.

| Objekt-ID | Name | Plattform | Wert |
|---|---|---|---|
| `battery_plan_now` | Akku-Plan jetzt | sensor | die Absicht des laufenden Slots („Eigenverbrauch“, „Halten“, „Netzladen“); Attribute `reason` (der Satz), `codes`, `planned_soc_pct`, `grid_charge_w`, `start`, `source` |
| `battery_plan_next` | Akku-Plan nächster Slot | sensor | wie oben für den Slot danach; am Ende des Plans „unbekannt“ |
| `planned_soc` | Geplanter SOC | sensor | SOC am Ende des laufenden Slots in %; Attribut `hourly` mit `[{start, soc_pct}]` je volle Stunde des Plans |
| `budget` | Budget | sensor | das Budget des laufenden Slots in kW mit einer Nachkommastelle; ohne Leistungspreis „unbekannt“ |
| `grid_charge` | Netzladen erlaubt | switch | die Einstellung (nicht die des Plans); schreibend |
| `replan` | Neu planen | button | löst eine Neuplanung aus (Anlass „Neu planen“) |

Schalter und Taste haben die Rechte von „Bedienen“.

### API

Für Skripte, mit Sitzung und Rolle wie das UI:

- `GET /api/plan` (ab „Lesen“): der Status der Komponente, `grid_charge_allowed` und der letzte Plan
  mit `valid`, Kosten in Euro, Auffälligkeiten und allen Slots samt Satz (`text`) und Codes. Ohne
  Plan ist `plan` leer. Je Slot nennt `ev_w` die geplante Ladeleistung je Auto (Schlüssel aus
  `config.yaml`, in W, nur Autos mit Leistung über 0); `plan.vehicles` fasst jedes geplante Auto
  zusammen (Ziel, Abfahrt, Ladestand zu Beginn und „angenommen“, geplante Energie, erwarteter
  Ladestand, `finish_at` und `reachable`), leer ohne Auto im Modus „Geplant“.
- `GET /api/plan/review?day=JJJJ-MM-TT` (ab „Lesen“, ohne `day` heute): Plan gegen Ist je
  vergangener Viertelstunde des Tages (`slots` mit `planned` und `actual`, `null` ohne Plan bzw.
  ohne Abrechnung) und die Summen (`totals`). Tage vor den letzten 30 und künftige Tage ergeben 422.
- `POST /api/plan/replan` (ab „Bedienen“, Antwort 202): fordert einen Lauf an.
- `PUT /api/plan/grid-charge` mit `{"on": true}` (ab „Bedienen“): schaltet „Netzladen erlaubt“.

Anmeldung und `X-CSRF-Token` für die schreibenden Aufrufe wie in Abschnitt 8, „Alternative für Skripte (curl)“, etwa:

```bash
curl -b jar -H "X-CSRF-Token: $CSRF" -X POST "$APP/api/plan/replan"
```

### Aufbewahrung

Die stündliche Wartung der app räumt auf: Nach 7 Tagen löscht sie die Eingangsdaten eines Plans und
kürzt seine Slots auf die vor seinem `valid_until` (das, was er tatsächlich bestimmt hat); nach 30
Tagen löscht sie den Plan. Ein Plan hat mit seinen Eingangsdaten rund 80 KB. Bei 100 bis 200 Läufen
am Tag belegen die Pläne der 7 Tage mit Eingangsdaten damit rund 60 bis 115 MB; gekürzte Pläne danach
nur noch wenige KB. SQLite gibt gelöschten Platz nicht von selbst an das Dateisystem zurück: Die Datei
wächst bis zu diesem Stand und bleibt dann etwa so groß, den frei gewordenen Platz nutzt sie wieder.

### Szenarien: Vergleich mit „Victron allein“

Die Abnahme der Phase 3 verlangt über mindestens 30 Tage niedrigere Kosten als „Victron allein“, ohne
Verletzung von Regel R. **Victron allein** ist der Eigenverbrauch des ESS ohne Netzladen und ohne
Halten, auf denselben Tagen mit demselben Start-SOC gerechnet. Zwei Werkzeuge rechnen das nach, mit
denselben Bausteinen wie der Planungsdienst (Optimierer, Ersatzplan, Akkumodell) und einem
Viertelstundenraster. Sie schalten nichts an der Anlage.

**Erzeugte Tage in der CI.** Das Repository ist öffentlich, die CI rechnet deshalb nur mit erzeugten
Tagen (`tests/integration/test_planning_scenarios.py`, Beispieltarif mit erfundenen Werten), je zwei
Tage, stündlich neu geplant, einmal mit und einmal ohne geplantes Auto:

| Art | Inhalt | ohne Auto | mit Auto |
|---|---|---|---|
| `sommer-sonnig` | PV-Spitze 9000 W, Börsenpreis mittags 2 ct | gleich „Victron allein“ | rund 2,64 € günstiger |
| `winter-spread` | PV-Spitze 800 W, Börsenpreis 7 ct von 0 bis 5 Uhr und 32 ct von 17 bis 20 Uhr | rund 1,07 € günstiger | rund 7,95 € günstiger |
| `winter-leistung` | wie `winter-spread`, dazu ein Leistungspreis (Beispielwerte) ab 1. 1. 2027 | rund 1,07 € günstiger | rund 38,83 € günstiger |
| `wechselhaft` | PV-Spitze 1500 W mit wechselnden Wolken, Börsenpreis 22 ct von 17 bis 21 Uhr | rund 0,25 € günstiger | rund 5,37 € günstiger |

**Das Auto** (erfundene Werte) steckt am ersten Abend ab 18:00 mit 40 % und fährt am Morgen danach
um 07:00 mit Ziel 80 % ab: 60 kWh nutzbar, 11 kW voll, 4140 W mindestens, Wirkungsgrad 90 %. Die
Datei hat dafür Version 2 mit dem Auto und je Slot der PV-Prognose P25 (für Regel R2); eine Datei
der Version 1 (so schreibt sie `export_days`) läuft ohne Auto. Gerechnet wird nur ein Ansteckvorgang,
dessen Abfahrt in den Daten liegt, bei zwei Tagen also der erste Abend.

- **Im Plan** steht das Auto, solange es steckt, wie ein Auto in „Geplant“ im Planungsdienst; beim
  Anstecken wird sofort neu geplant. Es lädt mit der Ladeleistung des Plans, höchstens bis zum Ziel.
- **„Victron allein“** lädt es ab dem Anstecken mit voller Leistung, bis das Ziel erreicht ist.
- **Ziel erreicht** heißt: Ladestand an der Abfahrt mindestens Ziel − 1 Prozentpunkt.
- **Regel R2 verletzt** ist ein Plan, in dem die Netzenergie ins Auto die nach Regel R2 erlaubte um
  mehr als 0,1 kWh übersteigt.

Der größte Teil des Vorteils mit Auto ist das Verschieben des Ladens vom teuren Abend in die Nacht,
bei `winter-leistung` dazu die niedrigere Spitze im Leistungspreis eines ganzen Monats.

Im Sommer ist der Optimierer gleich „Victron allein“: Die PV füllt den Akku, und „Victron allein“
bezieht nie. Die Wintertage haben nur 800 W PV-Spitze, `wechselhaft` nur 1500 W; mit mehr PV (2000 W
im Winter, bis 1600 W bei `wechselhaft`) bezöge „Victron allein“ zur Spitze nie oder kaum, und es gäbe
keinen Vorteil zu messen.

**Echte Tage der Anlage.** Zuerst exportiert `export_days` Tage aus der Datenbank der app in eine
Datei, dann rechnet `scenario_days` sie. Beide laufen im Image der app; die Dateien liegen unter
`/data` (im Volume der app) und gehören nie ins Repository, denn sie enthalten die Daten der Anlage:

```bash
cd /opt/bindaems
docker compose run --rm --entrypoint python ems-app \
  -m bindaems.tools.export_days --config /config/config.yaml \
  --von 2026-11-01 --bis 2026-11-30 --out /data/tage.json
docker compose run --rm --entrypoint python ems-app \
  -m bindaems.tools.scenario_days --tage /data/tage.json --out /data/bericht.md
docker compose run --rm -T --entrypoint cat ems-app /data/bericht.md
```

- **`export_days`:**
  - `--von` und `--bis` (Pflicht, lokale Tage `JJJJ-MM-TT`, beide eingeschlossen) und `--out` (Pflicht).
  - `--config` ist die Konfiguration (Standard `/config/config.yaml`), `--start-soc` der SOC zu Beginn
    in % (Standard 50) und `--ohne-prognose` trägt die Ist-Werte als Prognose ein.
  - Je Tag gehen Ist-Werte aus der Abrechnung, Preise mit den aktuellen Einstellungen und die
    Prognosen je Horizontklasse aus dem Archiv in die Datei. Ein unvollständiger Slot wird als Mittel
    seiner Nachbarn ergänzt, höchstens vier je Tag. Ein Tag **ohne Preise** oder mit mehr Lücken wird
    übersprungen und mit dem Grund aufgelistet (`skipped_days` in der Datei und eine Zeile auf der
    Standardausgabe).
  - Die **Monatsspitze** vor dem ersten Tag ist der höchste Netzbezug der Slots dieses Monats vor dem
    ersten Tag aus der Abrechnung. Ein übersprungener Tag lässt eine Lücke: Der Lauf rechnet die Tage der
    Datei hintereinander, und der höchste Bezug des fehlenden Tages fehlt im Leistungspreis.
  - Die Datenbank wird nur lesend geöffnet (`mode=ro`) und die Einstellungen direkt gelesen: Das
    Werkzeug legt weder eine Einstellungsversion noch einen Eintrag im Protokoll an. Ein relatives
    `app.data_dir` der Konfiguration gilt relativ zur Konfigurationsdatei, nicht zum
    Arbeitsverzeichnis. Rückgabewert 2 bei ungültiger Eingabe oder Konfiguration, fehlender
    Datenbank, nicht schreibbarer Zieldatei und wenn kein Tag exportierbar ist.
  - Die Logs der Werkzeuge (`export_days`, `scenario_days`, `bindaems.tools.sim`) gehen nach stderr:
    Wer die Standardausgabe in eine Datei oder Pipe leitet, bekommt nur die Zeilen des Werkzeugs.
- **`scenario_days`:**
  - Die Tage kommen aus `--tage DATEI` (von `export_days`) oder erzeugt aus `--synthetisch ART` (die
    Arten oben) mit `--anzahl N` Tagen. Der Bericht geht als Markdown nach `--out`. Das Verzeichnis
    muss existieren und beschreibbar sein; das Werkzeug prüft es vor dem Lauf und endet sonst sofort
    mit 2.
  - `--takt 15` (Standard) plant jede Viertelstunde neu wie der Dienst, `--takt 60` stündlich und viermal
    so schnell. Für die Abnahme zählt `--takt 15`; 30 Tage brauchen einige Minuten.
  - `--netzladen an|aus|einstellung` ersetzt den Schalter „Netzladen erlaubt“ der Datei;
    `einstellung` (Standard) lässt ihn, wie er beim Export stand.
  - **Rückgabewert:** 0 bei „Bestanden“, 1 bei „Nicht bestanden“, 2 bei ungültiger Eingabe und bei
    einem Fehler der Rechnung („Rechnung gescheitert: …“ auf stderr, kein Bericht).

Der Bericht „Szenario-Vergleich mit Victron allein“ hat je Tag eine Zeile mit den Spalten „Tag“,
„Victron allein (€)“, „Optimierer (€)“, „Differenz (€)“, „Netzladen (kWh)“, „Halten (Slots)“,
„Regel R“ und „Ersatzpläne“, mit Autos dazu „Ziele erreicht“ („a von b“ am Tag der Abfahrt) und
„Regel R2“, dann diese Zeilen:

- „Gesamt“, einschließlich Leistungspreis (die Tageszeilen enthalten nur Energiekosten),
- „Optimierer bereinigt um den End-SOC“: die Kosten des Optimierers, abzüglich des Werts, den er am
  Ende mehr (oder weniger) im Akku hat als „Victron allein“; **diese Zahl entscheidet**,
- „Leistungspreis“,
- „Netzladen vor tatsächlicher Einspeisung“: die netzgeladene Energie in Slots, nach denen
  tatsächlich mehr als 0,1 kWh eingespeist wurde, bevor der Akku leer war (über den ganzen Lauf,
  seit Plan 3d nicht mehr je Tag); eine Informationszahl ohne Grenze, denn Prognosefehler machen
  sie unvermeidbar,
- „Takt: n min“.

Hat `export_days` Tage übersprungen (`skipped_days`), steht direkt unter der Tabelle die Zeile
„Übersprungene Tage: …“ mit ihren Daten; die Gründe nennt die Ausgabe von `export_days`.

Die Differenz ist Optimierer minus „Victron allein“; negativ heißt günstiger. Bei weniger als 30 Tagen
steht der Hinweis „Weniger als 30 Tage: Für die Abnahme der Phase 3 zählen mindestens 30.“ Das Ergebnis
ist **„Bestanden“**, wenn die bereinigten Kosten höchstens die von „Victron allein“ sind und Regel R
nie verletzt wurde, mit Autos zudem jedes Ziel erreicht und Regel R2 nie verletzt, sonst „Nicht
bestanden“ mit den Gründen: „Bereinigte Kosten über Victron allein“, „Regel R verletzt ({n})“, mit
Autos „Ziel-SOC verfehlt ({a} von {b})“ und „Regel R2 verletzt ({n})“. Ein Verstoß gegen Regel R ist ein
ausgeführter `Netzladen`-Slot, nach dem der Plan dieses Zeitpunkts mehr als 0,1 kWh Einspeisung
erwartet, bevor der Akku leer ist.

Die Prognosen im Archiv (60 Tage Aufbewahrung, Abschnitt „Prognosen“) füllen sich erst nach dem
Ausrollen von Plan 3a; für ältere Tage fehlt die Prognose, und der Lauf nimmt den Ist-Wert.

### An der Anlage prüfen

Die Pläne 3b und 3c sind ohne die echte Anlage getestet; das zeigt sich erst im Betrieb:

- **Der Solver im Image:** Öffne **System** und sieh nach, ob die Komponente `planning` „Plan von
  HH:MM Uhr“ meldet und nicht „Ersatzplan von … : Solver fehlgeschlagen: …“. Das Log nennt die
  Rechenzeit des Solvers (`solve_ms`) je Lauf:
  `docker compose logs --since 1h ems-app | grep "Plan erstellt"`. Üblich sind Sekunden oder weniger; nahe
  20 s stößt das Zeitlimit an.
- **Plausibilität des Plans:** Vergleiche auf der Seite **Zeitplan** die Preise und Prognosen mit denen
  der Übersicht und den Plan („Ablauf“) mit dem, was du bei diesen Preisen erwartest. Ein Ersatzplan
  oder „Auffälligkeiten“ über Stunden sind ein Befund.
- **Regel R:** Bei eingeschaltetem „Netzladen erlaubt“ darf kein Block „Netzladen“ bis in einen
  Zeitraum reichen, in dem laut PV-Prognose eingespeist wird, ohne dass der Akku dazwischen auf
  der Reserve ankommt (SOC-Verlauf im Plan prüfen: ist er vor der Einspeisung bei der Reserve,
  ist Netzladen davor in Ordnung). Der Satz „Kein Netzladen: … (Regel R)“ zeigt, dass die Regel
  gegriffen hat.
- **Auslöser:** Die Zeit im Kopf („Plan von …“) springt jede Viertelstunde, eine Minute nach Slotbeginn.
  Weicht der SOC um mehr als 5 Prozentpunkte vom Plan ab (etwa durch einen großen Verbraucher),
  folgt ein Plan mit dem Anlass „SOC weicht ab“.
- **Der Vergleich über 30 Tage:** Exportiere die Tage, sobald das Archiv der Prognosen gefüllt ist, und
  lass den Bericht mit `--takt 15` laufen (oben). „Bestanden“ über mindestens 30 Tage ist die
  Voraussetzung für die Abnahme der Phase 3, zusammen mit den 2 Wochen Dry-Run der Planausführung
  (Abschnitt „Akku und Spitzen“, „Victron in den Dry-Run und live“).
- **Autos im Plan** (Plan 4b): Steckt ein Auto in „Geplant“, folgt ein Plan mit dem Anlass „Fahrzeug
  geändert“, und das Diagramm „Autos“ zeigt seine Ladeleistung. Mit Autos rechnet der Solver länger: In
  den Szenarien der CI erreichte er mit Auto und Leistungspreis (`winter-leistung`) einige Male das
  Zeitlimit von 20 s, mit einer Lösung, die dann gilt. Nähert sich `solve_ms` an der Anlage oft 20 s,
  ist das ein Befund. Ein Ersatzplan mit Auto lädt es zuerst aus der erwarteten PV, dann in den
  billigsten Slots (oben, „Ersatzplan“).
- **Planausführung:** Im Dry-Run zeigen die oberste Zeile der Seite **Zeitplan** („Dry-Run: …“), die
  Karte „Plan gegen Ist (heute)“ und die Entscheidungen mit `plan_hold` und `plan_grid_charge`, ob
  der core den Plan so umsetzen würde, wie er gerechnet ist (Abschnitt „Akku und Spitzen“, „Victron
  in den Dry-Run und live“, Schritte 3 und 4).

## Klimaanlage (Phase 6a)

Seit Plan 6a nimmt die Klimaanlage (sie kühlt und heizt) PV-Überschuss auf, den Akku und Autos nicht
brauchen, und hält sich bei teurem Strom und drohenden Spitzen zurück. Der core liest vom C.M.I.
über CoE V2 (UDP) Ist, Soll, Betriebsart, Rückmeldung, Leistung und das Echo des Versatzes und
schickt der UVR610 einen **Sollwertversatz** von −3 bis +3 K in Schritten von 0,5 K, alle 30 s.
Die Komfortregelung bleibt in der UVR und der Klimaanlage; BindaEMS verschiebt nur den Sollwert:
beim Kühlen „mehr“ = negativer Versatz, beim Heizen „mehr“ = positiver Versatz.

**Der Aktor** heißt `thermal.<name>` (im Beispiel `thermal.klima`) und startet wie jeder Aktor im
Dry-Run (Abschnitt 13, „Dry-Run und live“): Er sendet dann zyklisch **0** und protokolliert im
Entscheidungslog, welchen Versatz er senden würde. Live geht er erst nach dem Prüfprotokoll Teil 2,
Punkt `thermal`, mit `live_allowed: true` und dem Live-Schalter eines Admins (Spec 8.7). Nichts
davon geschieht von selbst.

**Die Anlage** (Stand 18.10.2026):

| | |
|---|---|
| C.M.I. | `192.168.81.92`, CAN-Knoten 56; sendet CoE an BindaEMS |
| BindaEMS | `192.168.82.20`, UDP-Port 5442; sendet als CAN-Knoten 60, Ausgang 1 (in der UVR der CAN-Eingang 15 „EMS Sollwertversatz“) |
| Versatz | −3 … +3 K, Stufe 0,5 K; ein Schritt weg von 0 frühestens alle 10 min, zurück auf 0 sofort |
| Timeout der UVR | 5 min: Ohne Paket setzt sie den Versatz selbst auf 0. Das ist der sichere Zustand, einen Watchdog wie am Cerbo braucht es nicht |

### Rangfolge und Strategie

Der Überschuss geht an **Akku → Autos → Klimaanlage → Einspeisung**. Die Klimaanlage bekommt nur,
was die Regelung nach Akku und Autos noch einspeisen würde. Die Strategie im core ist regelbasiert
und rechnet einen Leistungswunsch in ganzen Stufen (positiv: mehr Leistung, negativ: weniger); erst
der Aktor macht daraus das Vorzeichen je Betriebsart. Je Zyklus gewinnt die erste Regel, die greift:

1. **Sofort 0** bei veralteten Werten, nicht steuerbarem Aktor (auch ohne Echo der UVR, unten bei
   „Live schalten“), Zustand DEGRADED, Betriebsart `aus`, `lueften` oder `unbekannt` und bei einem
   Wechsel der Betriebsart.
2. **Spitze oder Phasenlast:** Droht dem 15-min-Wächter eine Überschreitung oder der Phasenwächter
   greift, fällt ein positiver Wunsch sofort auf 0.
3. **Autos haben Vorrang:** Ein Auto in „PV-Überschuss“ oder „Min+PV“, das mehr aufnehmen könnte
   (offener Wunsch ab 230 W, eine Stufe von 1 A), drängt die Klimaanlage je 10 min um eine Stufe
   zurück. Damit sich Klimaanlage und Laderegelung nicht gegenseitig aufschaukeln, gilt für die
   Autos:
   - **Anlauf:** 90 s nach einem eigenen Start oder höheren Strom folgt das Auto seinem Befehl; die
     Klimaanlage geht dann keine Stufe hoch.
   - **Hysterese:** Das Urteil „folgt“ oder „begrenzt sich selbst“ wechselt erst nach 120 s. Ein
     Auto, das sich selbst begrenzt, zählt erst wieder, wenn es bis auf eine Stufe den Strom zieht,
     bei dem es sich begrenzte.
   - **Auto bei „Laden aus“:** Es hat nur Vorrang, wenn Einspeisung plus die Leistung, die die
     Klimaanlage freigeben kann, seine Startleistung erreichen; sonst nimmt die Klimaanlage den
     Überschuss. Freigeben kann sie nur, was sie über ihrer Leistung bei Versatz 0 aufnimmt, und
     nur bei positivem Wunsch: Die Grundlast beim Heizen oder Kühlen bekäme ein Auto nie. Die
     Leistung bei Versatz 0 lernt der core, wenn der Versatz 5 min lang 0 war; bis dahin zählt die
     ganze Leistung. Die gelernte Grundlast und die freigebbare Leistung zeigt die Karte
     „Klimaanlage“ auf „System“ („Grundlast … W · freigebbar … W“, „–“ ohne Wert und ohne Verbindung), damit du im
     Dry-Run siehst, ob die Grundlast gelernt wird. Hat ein Auto einmal gezählt, zählt es weiter, solange der Überschuss um
     höchstens eine Stufe (1 A) unter seiner Startleistung liegt (Hysterese).
4. **Der Akku hat Vorrang:** Liegt der SOC mehr als 5 Prozentpunkte unter der Schwelle (der geplante
   SOC am Ende des Slots, ohne Plan `strategy.battery_soc_pct`, Standard 90 %), geht ein positiver
   Wunsch je 10 min eine Stufe zurück. Mit dem Hinweis „mehr“ des Plans entfällt die Schwelle.
5. **Mehr:** Einspeisung ab `strategy.more_above_w` (800 W) 10 min ohne Unterbrechung, dann eine
   Stufe mehr, höchstens bis `max_offset_k`.
6. **Zurück:** Netzbezug ab `strategy.import_above_w` (300 W) 5 min ohne Unterbrechung bei positivem
   Wunsch, dann eine Stufe zurück.
7. **Weniger:** Der Plan gibt dem Slot den Hinweis „weniger“ (die teuersten 20 % der Bezugspreise,
   keine geplante Einspeisung) und die Einspeisung liegt unter 100 W: je 10 min eine Stufe weniger,
   bis `strategy.less_max_steps` (2, also höchstens 1 K). Endet der Hinweis, geht der Wunsch sofort
   auf 0; die Einspeisung muss danach wieder die volle Wartezeit von Regel 5 anstehen, bevor der
   Wunsch eine Stufe höher geht (er springt nicht von −2 über 0 gleich auf +1).
8. **Sonst** bleibt der Wunsch stehen (Totband gegen Wolken).

Neue positive Stufen gibt es nur, wenn kein Druck besteht, kein Auto mehr aufnehmen könnte und der
SOC die Schwelle erreicht. Jede Erhöhung des Betrags braucht 10 min seit der letzten
(`step_interval_s`); Verringerungen gehen sofort. Den Hinweis je Slot (`mehr`, `neutral`, `weniger`)
rechnet die app aus Preis und Plan; der core entscheidet mit den Echtzeitwerten. Vorheizen und
Vorkühlen über Stunden plant 6a nicht. Die Szenarien `klima-*` des Simulators (Abschnitt
„Simulator“) spielen jede dieser Regeln durch.

### Netz und Firewall

C.M.I. (192.168.81.0/24) und die VM (192.168.82.0/24) liegen in verschiedenen Subnetzen. **Routing
und Firewall für UDP 5442 in beide Richtungen sind ein Schritt des Betreibers**, BindaEMS ändert
daran nichts:

- 192.168.81.92 → 192.168.82.20, UDP 5442: die Werte des C.M.I.
- 192.168.82.20 → 192.168.81.92, UDP 5442: der Versatz. Der core sendet aus dem Container; Docker
  setzt dabei die Adresse des Containers auf die der VM um (Masquerade). Das ist das **erwartete**
  Verhalten, aber an der Anlage nicht geprüft: Bestätige, dass der Versatz beim C.M.I. mit dem
  Absender `192.168.82.20` ankommt (Sicht des C.M.I. auf den CAN-Knoten 60, oder das Echo im Punkt
  `thermal`, unten). Kommt er von einer anderen Adresse, ist das ein Befund für Docker oder die
  Firewall.

`docker-compose.yml` veröffentlicht dafür am Dienst `ems-core` genau diesen Port (`5442:5442/udp`);
sonst hat der core keinen Port. **Die Compose-Datei in `/opt/bindaems` ist deine Kopie aus der
Installation** (Abschnitt 2); `docker compose pull` ändert sie nicht. Übernimm darum die neue Datei
aus `deploy/` (Schritt 2 der Reihenfolge unten), sonst kommt kein Paket des C.M.I. an, und auch
`coe_probe` mit `--service-ports` hört ins Leere. Prüfe auf der VM, dass `docker compose ps` ihn zeigt (`5442/udp`)
und die Firewall der VM (Proxmox-Firewall oder `DOCKER-USER`, wie bei Port 8080 in Abschnitt 6)
nur das C.M.I. zulässt. Der core verwirft ohnehin jedes Paket, das nicht von `cmi_host` kommt.
Das C.M.I. sendet CoE an `192.168.82.20` (Betreiber, erledigt); in der UVR sind die CAN-Ausgänge
für Echo (A12) und Leistung (A13, aus F51) eingerichtet.

**Voraussetzung am C.M.I. (Betreiber):** Das C.M.I. muss Pakete von `192.168.82.20` annehmen und
sie als Werte des CAN-Knotens 60 (Ausgang 1) an die UVR weitergeben (dort der CAN-Eingang 15 „EMS
Sollwertversatz“). Trage die Adresse der VM im C.M.I. ein, wenn es eine Liste erlaubter Absender
führt. BindaEMS ändert daran nichts. Ob es klappt, zeigt der Punkt `thermal` des Prüfprotokolls
Teil 2 (das Echo folgt dem Versatz).

### config.yaml

Der Abschnitt `thermal` ist optional. Ohne ihn gibt es keine Klimaanlage, und der core öffnet den
Port nicht. `deploy/config.example.yaml` hat das Beispiel `klima`; der Name ist der Schlüssel des
Geräts und endet als Aktor `thermal.klima`:

```yaml
thermal:
  devices:
    klima:
      kind: coe_offset
      label: Klimaanlage
      cmi_host: 192.168.81.92
      port: 5442
      listen_port: 5442
      own_node: 60
      own_output: 1
      max_offset_k: 3.0
      step_k: 0.5
      step_interval_s: 600
      send_interval_s: 30
      stale_after_s: 180
      receive:
        actual_c: {node: 56, analog: 1, unit: 1}
        setpoint_c: {node: 56, analog: 2, unit: 1}
        offset_active_k: {node: 56, analog: 3, unit: 1}
        power_kw: {node: 56, analog: 4, unit: 10, scale: 100}
        cmd_on: {node: 56, digital: 1}
        cooling: {node: 56, digital: 2}
        venting: {node: 56, digital: 3}
        heating: {node: 56, digital: 4}
        running: {node: 56, digital: 5}
      live_allowed: false
```

| Schlüssel | Bedeutung |
|---|---|
| `cmi_host`, `port` | Adresse und CoE-Port des C.M.I.; nur Pakete von `cmi_host` zählen, hierhin gehen die Versätze |
| `listen_port` | UDP-Port am core. Ändere ihn nur zusammen mit `ports` in `docker-compose.yml` |
| `own_node`, `own_output` | Knoten und Analogausgang, unter denen BindaEMS sendet. `own_node` darf kein Knoten der Empfangstabelle sein (`config.yaml` wird sonst abgelehnt) |
| `max_offset_k`, `step_k`, `step_interval_s` | harte Grenzen der Safety-Schicht (Spec 8.2): höchstens 3 K, ein Vielfaches von `step_k`; `step_k` ist fest 0,5 K; frühestens `step_interval_s` (mindestens 60 s) zwischen zwei Erhöhungen |
| `send_interval_s` | Takt des Sendens, auch unverändert (5 bis 60 s); die UVR fällt nach 5 min ohne Paket auf 0 |
| `stale_after_s` | So lange ohne gültiges Paket ist die Quelle „nicht verbunden“ (60 bis 290 s, Standard 180 s) |
| `receive` | Empfangstabelle: welcher Wert des C.M.I. (Knoten, Analog- oder Digitalausgang, Einheit) welche Größe ist. Pflicht sind `actual_c`, `setpoint_c`, `offset_active_k`, `cooling`, `venting`, `heating` und `running`; `power_kw` und `cmd_on` sind optional |
| `strategy` | Schwellen der Strategie (alle optional): `more_above_w`, `import_above_w`, `battery_soc_pct`, `less_max_steps` |
| `live_allowed` | Freigabe für den Live-Betrieb, erst nach dem Prüfprotokoll Teil 2, Punkt `thermal` |

Analoge Werte der Einheit 1 (°C) und 7 (K) kommen in Zehnteln. Für jede andere Einheit ist `scale`
(ein Teiler) Pflicht: `unit: 10, scale: 100` heißt „Rohwert 123 = 1,23 kW“. Ein Paket mit einer
anderen Einheit oder Art als in der Tabelle macht den Wert ungültig und zählt in Health
(`unit_mismatch`). Höchstens ein Gerät der Art `coe_offset` ist erlaubt. Der Standardname
„Klimaanlage“ (`label`) heißt in Home Assistant kurz „Klima“; ein eigener Name steht ganz davor.

### Reihenfolge an der Anlage

1. **Netz freigeben** (oben, „Netz und Firewall“).
2. **Compose-Datei und Images übernehmen,** zuerst noch ohne Abschnitt `thermal` in `config.yaml`:
   Vergleiche deine Datei mit der neuen (`diff /opt/bindaems/docker-compose.yml
   <repo>/deploy/docker-compose.yml`), übernimm eigene Änderungen (etwa die Zeile
   `127.0.0.1:8081:8081` aus Abschnitt 6) in die neue und kopiere sie nach `/opt/bindaems`. Dann
   rollst du core und app gemeinsam aus wie in „Laderegelung“, „Geplantes Laden ausrollen (Plan 4b)“
   (Regelbetrieb aus, Datenbank sichern, `docker compose pull && docker compose up -d`). Ohne
   Abschnitt `thermal` läuft der core wie vorher, ohne Klimaanlage. `docker compose ps` zeigt am
   core jetzt `5442/udp`. Die Werkzeuge der nächsten Schritte kommen erst mit diesen Images.
3. **Tabelle prüfen:** core stoppen, die Pakete mit `coe_probe listen` sehen, Knotennummer des
   C.M.I. und die Einheit der Leistung notieren und den Abschnitt `thermal` in `config.yaml`
   eintragen (unten, „Empfang prüfen“).
4. **Prüfprotokoll Teil 2, Punkt `thermal`** durchführen und den Bericht einchecken (unten).
5. **core starten** (`docker compose up -d ems-core`). Der Aktor der Klimaanlage startet im
   Dry-Run.
6. **Dry-Run beobachten**, mindestens 7 Tage (unten).
7. **Live schalten** (unten) und die Abnahme der Phase 6a durchführen
   (`docs/verification/abnahme-phase-6a.md`).

Plan 6a rollt mit 4b im gemeinsamen Fenster nach dem 25.10.2026 aus (Entscheidung des Betreibers vom 22.10.2026).

**Achtung, Watchdog:** `coe_probe listen` und der Punkt `thermal` verlangen einen **gestoppten
core**. Das dauert Minuten: bei `coe_probe` so lange wie `--sekunden`, beim Timeout-Schritt des
Punkts `thermal` über 6 min. Ist währenddessen EVCS oder Victron live (der Watchdog am Cerbo
scharf), löst der Watchdog nach 90 s ohne Heartbeat aus und setzt die Sicherwerte (Abschnitt 4,
„Geordnetes Herunterfahren“, und Abschnitt 14). Mach diese Schritte darum nur, solange **kein Aktor
live** ist: vor der Live-Schaltung von EVCS, Tesla und Victron oder nachdem du sie im UI auf Dry-Run
gestellt hast (Abschnitt 13, „Dry-Run und live“). Sonst plane die Auslösung ein und gib den Watchdog
danach von Hand frei (Abschnitt 14, „Meldungen des core“, „‚Ausgelöst‘ von Hand aufräumen“). Der
Aktor der Klimaanlage selbst braucht keinen Watchdog.

### Empfang prüfen

`coe_probe listen` hört am CoE-Port mit und zeigt je Wert eines Pakets eine Zeile. Es **sendet
nie**. Der core muss dafür gestoppt sein, denn beide binden denselben Port (Warnung zum Watchdog
oben, „Reihenfolge an der Anlage“). Außerhalb des Containers (`python -m bindaems.tools.coe_probe`
auf dem Host) sagt das Werkzeug, wenn der Port belegt ist, und endet mit Exitcode 2. Im Container
sieht es den core nicht: Läuft er noch, scheitert schon `docker compose run` am belegten Port.

```bash
cd /opt/bindaems
docker compose stop ems-core
docker compose run --rm --service-ports --entrypoint python ems-core \
  -m bindaems.tools.coe_probe listen --sekunden 120
```

Danach startest du den core wieder, wenn er vorher lief (`docker compose up -d ems-core`). Vor
dem Ausrollen von Plan 6a startet ihn erst Schritt 5 der Reihenfolge.

`--service-ports` ist nötig, denn `docker compose run` veröffentlicht sonst keine Ports und die
Pakete des C.M.I. kämen nicht an. Ohne `--sekunden` hört das Werkzeug bis Strg+C; `--port` wählt
einen anderen Port als 5442, den `docker-compose.yml` aber nicht veröffentlicht (im Container kämen
dort keine Pakete an). Eine Zeile lautet etwa `14:05:09 von 192.168.81.92: Knoten 56, analog 4,
Einheit 10, Rohwert 120`; ein Paket, das kein gültiges CoE-V2-Paket ist, ergibt eine Zeile
„ungültiges Paket“ mit dem Grund. Prüfe:

- **Alle Werte der Empfangstabelle kommen an** und der Absender ist `192.168.81.92`. Das C.M.I.
  sendet bei Änderung und zyklisch.
- **Jeder Wert kommt spätestens alle `stale_after_s` (180 s) neu**, auch wenn er sich nicht ändert:
  Der core lässt jeden Wert für sich veralten, nicht nur die ganze Quelle. Stelle an den
  CAN-Ausgängen des C.M.I. eine Intervallzeit deutlich kürzer als `stale_after_s` ein (etwa 1 min).
  Sonst veraltet ein ruhiger Wert wie das Echo, obwohl Pakete ankommen: Der Aktor ist dann nicht
  steuerbar, und der Alarm `thermal.echo_mismatch.<name>` kommt. Hör mit `--sekunden` über
  `stale_after_s` mit und prüfe, dass jeder Wert in diesem Fenster mehrmals erscheint.
- **Die Knotennummer** ist 56. Steht eine andere im Paket, ändere `node` in `receive`.
- **Die Einheit der Leistung** (Analog 4, F51): Der Plan nimmt Einheit 10 (kW) mit zwei
  Nachkommastellen an, also `scale: 100`. Zeigt die Zeile eine andere Einheit oder einen anderen
  Rohwert (etwa Watt), passe `unit` und `scale` an, bis `Rohwert / scale` die Leistung in kW ergibt.
- **Das Echo** (Analog 3) folgt dem Versatz, den du in der UVR siehst (nur mit gesendetem Versatz,
  also im Prüfprotokoll Teil 2).

### Prüfprotokoll Teil 2, Punkt thermal

Der Punkt `thermal` (Spec 17.2-9) **sendet** der Klimaanlage einen Versatz und läuft darum nur
gemeinsam mit dem Betreiber. Das Werkzeug sendet nur auf ausdrücklichen Aufruf und nach einer
Bestätigung. Es nutzt denselben Adapter und Aktor wie der core, prüft den Versatz mit den Regeln
der Safety-Schicht und braucht keine Secrets. Der core bleibt dabei gestoppt (derselbe Port, mit
der Warnung zum Watchdog oben: nur, solange kein Aktor live ist). Dann:

```bash
cd /opt/bindaems
mkdir -p verification && sudo chown 10001 verification   # der Container läuft als UID 10001
docker compose stop ems-core
docker compose run --rm --service-ports -v "$PWD/verification:/out" --entrypoint python ems-core \
  -m bindaems.tools.verify_part2 --config /config/config.yaml --punkt thermal \
  --out /out/<Datum>-pruefprotokoll-teil2-thermal.md
```

Danach startest du den core wieder, wenn er vorher lief (`docker compose up -d ems-core`).

`--geraet <Name>` wählt die Klimaanlage, wenn es mehrere gibt (Standard: die erste unter
`thermal.devices`). Die Schritte, je eine Zeile unter Punkt 17.2-9 im Bericht:

1. **Werte der Empfangstabelle:** Alle Werte kommen binnen `stale_after_s` an. Sonst endet der
   Punkt, ohne etwas zu senden, und der Befund nennt, was fehlt (auch Pakete mit falscher Einheit
   oder von fremder Adresse). Das C.M.I. muss jeden Wert zyklisch binnen `stale_after_s` neu
   senden (oben, „Empfang prüfen“); fehlt ein Wert, der sich selten ändert, prüfe zuerst die
   Intervallzeit seines CAN-Ausgangs.
2. **Versatz +0,5 K:** Das Werkzeug sendet ihn, das Echo der UVR zeigt ihn binnen 60 s. Dann fragt
   es: „Zeigt die Visu der UVR „EMS“ +0,5 K?“ Sieh dazu auf die Visu der UVR.
3. **Versatz 0:** gesendet, das Echo zeigt 0.
4. **Timeout der UVR** (mit Rückfrage): Das Werkzeug sendet +0,5 K und danach bis zu 5 min 30 s
   nichts mehr; das Echo muss von selbst auf 0 fallen. Das belegt den sicheren Zustand. Hat
   Schritt 2 nicht bestanden (kein Echo oder die Visu zeigt den Versatz nicht), überspringt das
   Werkzeug diesen Schritt mit INFO „übersprungen, weil Schritt 2 nicht bestanden hat“.

Am Ende, nach einem Fehler und nach Strg+C sendet das Werkzeug 0 und wartet auf das Echo 0.
`--yes` beantwortet alle Rückfragen mit Ja und überspringt dann den Timeout und die Rückfrage zur
Visu (niemand sieht auf die Visu); für das Protokoll läufst du **ohne** `--yes`. Exitcodes und
Format des Berichts wie bei den anderen Punkten (Abschnitt 11, `docs/verification/README.md`).
Ist der Port auf der VM schon belegt, weil der core läuft, bricht `docker compose run` ab; stoppe
ihn dann wie oben.

Der Punkt ist **bestanden**, wenn alle Zeilen OK sind. Nach jeder Änderung an der UVR-Programmierung
oder an der CAN-Verbindung des C.M.I. wiederholst du ihn.

### Dry-Run beobachten

Mit laufendem core, Regelbetrieb an und dem Aktor im Dry-Run ändert sich an der Klimaanlage nichts:
Der Adapter sendet alle 30 s **0**, und das Echo bleibt 0. Beobachte mindestens **7 Tage**:

- **System** → „Klimaanlage“ zeigt „Werte kommen an“, Betriebsart, Ist, Soll ohne Versatz, Versatz,
  Wunsch, Leistung, die Zeile „Grundlast … W · freigebbar … W“, den letzten Empfang und den Grund.
  Dort steht auch „Fremde Pakete“,
  „Fehlerhafte Pakete“ und „Werte mit falscher Einheit“, wenn welche ankamen.
- **Entscheidungen** (und Grafana, Panel „Klimaanlage“) zeigen, welchen Versatz der core im
  Live-Betrieb senden würde („Dry-Run, nicht gesendet“), mit den Gründen. Kühlen und Heizen, Wechsel
  der Betriebsart, Wolken, Autos und ein teurer Abend sollten sich darin wiederfinden.
- **Kein Alarm** `thermal.echo_mismatch.<name>` und kein `control.thermal_exception` (unten).
  Fehlt das Echo, kommt der Alarm auch im Dry-Run, ohne dass je ein Versatz gesendet wurde.
- Das Echo bleibt im Dry-Run 0; das Log zeigt den gewünschten Versatz. Beides gehört zusammen: Würde
  das Echo etwas anderes als 0 zeigen, wäre das ein Befund (Alarm `thermal.echo_mismatch.<name>`).

Auswertung der Entscheidungen im Dry-Run (`SINCE` wie in „Dry-Run-Vergleich mit evcc“):

```bash
curl -s -H "Authorization: Bearer $BINDAEMS_INTERNAL_TOKEN" \
  "http://127.0.0.1:8081/v1/decisions?since=$SINCE&limit=5000" \
  | jq -r '.decisions[] | select(.actuator == "thermal.klima") | [.ts, .dry_run, .result,
      .requested, (.codes | join(",")), .inputs.thermal_steps, .inputs.export_w, .inputs.soc_pct]
      | @tsv'
```

Zugang zur internen API wie in Abschnitt 4, „Diagnose“ (Port 8081 kurzzeitig veröffentlichen). Prüfe
dabei, ob der Wunsch nicht flackert (mehr als eine Stufe je 10 min weg von 0) und ob die Gründe zu
dem passen, was an dem Tag geschah.

### Live schalten

Voraussetzungen (Spec 8.7): Der Dry-Run lief 7 Tage ohne Fehler, und das Prüfprotokoll Teil 2,
Punkt `thermal`, ist bestanden. Dann:

1. `live_allowed: true` unter `thermal.devices.klima` in `config.yaml`, den core neu starten.
2. Im UI als Admin **System** → „Bedienung“ → „Live schalten…“, `Klimaanlage` wählen, Passwort
   eingeben und „Prüfprotokoll Teil 2 für die gewählten Aktoren ist bestanden.“ bestätigen. Die
   Bestätigung „evcc ist ganz abgeschaltet.“ entfällt, denn die Klimaanlage hängt nicht an evcc. Der
   Aktor braucht auch keinen Watchdog: Die UVR setzt den Versatz nach 5 min selbst auf 0.
3. Der core sendet jetzt den Versatz, aber **nur im Regelbetrieb** und solange er ihn in den letzten
   10 s freigegeben hat. Freigegeben wird nur ein Versatz ungleich 0, den die Regelung in diesem
   Zyklus entschieden hat; 0 geht immer hinaus und wird nie als „unverändert“ verworfen. Die
   Freigabe läuft nach 10 s ab, dann sendet der Adapter 0. Wirft der Zyklus eine Ausnahme, steht
   der Versatz darum nach höchstens 10 s auf 0. Hängt der ganze Prozess, steht auch der Adapter
   (er läuft im selben Prozess) und sendet nichts mehr; dann setzt die UVR den Versatz nach 5 min
   selbst auf 0. Das Echo bestätigt jeden Versatz (Frist 60 s).
   **Ohne Echo** (Analog 3 veraltet oder nie angekommen) ist der Aktor nicht steuerbar, auch wenn
   die übrigen Werte ankommen: Die Strategie wünscht 0 (`thermal_no_echo`), die Karte „Aktoren“ nennt
   „Kein Echo des Versatzes von der UVR.“, und nach 120 s kommt der Alarm
   `thermal.echo_mismatch.<name>`.
4. Zurück: im UI „Auf Dry-Run…“. Der core übergibt den Aktor und sendet 0. Die Übergabe endet erst,
   wenn das Echo die 0 bestätigt. Fehlt das Echo (das C.M.I. ist weg), bleibt der Aktor in
   „Übergabe“ und, mit Regelbetrieb aus, der Zustand DEGRADED statt OBSERVE, bis das Echo
   zurückkehrt. Das ist sicher: Der Adapter sendet 0, und die UVR fällt ohne Paket nach 5 min
   ohnehin auf 0.

Die Safety-Schicht klemmt jeden Versatz auf ±`max_offset_k`, rundet auf 0,5 K und lässt höchstens
eine Stufe je 10 min weg von 0 zu (Befund `offset.step`); 0 geht immer sofort. Das Echo der UVR ist
die Rückmeldung: Ein Versatz gilt als bestätigt, wenn das Echo ihn auf 0,05 K zeigt.

### Gründe

Das Entscheidungslog nennt bei Befehlen an die Klimaanlage in `codes` diese Gründe (Text im Log und
im UI, „Warum?“). Der Grund beschreibt den Wunsch der Strategie, nicht jeden gesendeten Wert.

| Grund (`codes`) | Text | Bedeutung |
|---|---|---|
| `thermal_more` | PV-Überschuss in die Klimaanlage | Die Einspeisung lag 10 min über der Schwelle: eine Stufe mehr Leistung. |
| `thermal_back` | Netzbezug, Klimaanlage eine Stufe zurück | Bei positivem Wunsch lag der Netzbezug 5 min über der Schwelle. |
| `thermal_cars` | Autos haben Vorrang | Ein Auto im Modus PV oder Min+PV könnte mehr aufnehmen: eine Stufe zurück. Die Klimaanlage hält sich im Anlauf eines Autos (90 s), bei einem Auto, das sich selbst begrenzt, und bei einem Auto ohne erreichbare Startleistung zurück, wie oben unter „Rangfolge und Strategie“. |
| `thermal_battery` | Akku hat Vorrang | Der SOC liegt unter der Schwelle: eine Stufe zurück. |
| `thermal_less` | Teurer Strom, Klimaanlage zurückhaltend | Der Plan gibt den Hinweis „weniger“: eine Stufe weniger, höchstens `less_max_steps`; auch beim Ende des Hinweises (Wunsch zurück auf 0). |
| `thermal_peak` | Spitze droht, Klimaanlage nicht mehr | Der 15-min-Wächter oder der Phasenwächter macht Druck: Versatz sofort auf 0. |
| `thermal_mode` | Betriebsart gewechselt, Versatz 0 | Die Klimaanlage wechselte zwischen Kühlen und Heizen: sofort 0. |
| `thermal_stale` | Keine Werte der Klimaanlage, Versatz 0 | Die Werte sind veraltet (länger als `stale_after_s` kein Paket): sofort 0. Solange die Quelle fehlt, erzeugt die Regelung keinen Befehl; die 0 geht vom Adapter hinaus. Im Log steht der Eintrag erst, wenn die Werte zurückkehren und der Versatz vorher nicht 0 war. |
| `thermal_no_echo` | Klimaanlage nicht steuerbar (kein Echo oder gesperrt), Versatz 0 | Die Werte kommen an, aber der Aktor ist nicht steuerbar: Das Echo des Versatzes (Analog 3) fehlt oder ist veraltet, oder die Klimaanlage ist gesperrt. Sofort 0. |
| `thermal_off` | Klimaanlage aus oder lüftet, Versatz 0 | Betriebsart `aus`, `lueften` oder `unbekannt`, oder der Zustand DEGRADED: Versatz 0. Auch der Anfangsgrund nach einem Start: Der erste Eintrag nach dem Start ist meist eine 0 mit diesem Grund. |

Ein Eintrag lautet etwa
`Klimaanlage (PV-Überschuss in die Klimaanlage): Sollwertversatz −0,5 K – gesendet und bestätigt.`
Zu den Eingaben gehören `thermal_steps` (der Wunsch in Stufen), `export_w` (Einspeisung) und
`soc_pct` (Akku).

### Alarme und Meldungen

Diese Alarme zeigt das UI unter **System** → „core“ → „Alarme“ (Abschnitt 14, „Meldungen des core“).

| Alarm | Stufe | Bedeutung und Abhilfe |
|---|---|---|
| `control.thermal_exception`: Regelung der Klimaanlage ausgesetzt: Ausnahme {Typ} im Regelzyklus. | Warnung | Die Strategie der Klimaanlage hat eine Ausnahme geworfen; {Typ} ist ihre Art. Der core regelt Akku und Autos weiter, die Klimaanlage bleibt bei Versatz 0 (der Adapter sendet 0). Das ist ein Fehler im core: Das Log (`docker compose logs ems-core`) nennt die erste Ausnahme einer Folge mit Traceback; sichere es und melde den Fehler. Der Alarm endet mit einem Zyklus, in dem die Strategie ohne Ausnahme lief; ein Neustart räumt ihn. |
| `thermal.echo_mismatch.<name>`: {Name}: Das Echo der UVR zeigt seit mehr als 120 s nicht den gesendeten Versatz (gesendet …, Echo … oder kein Echo). | Warnung | Das Echo (Analog 3) weicht länger als 120 s vom Versatz ab, den der Adapter sendet; er gilt nach dem ersten gesendeten Versatz auch im Dry-Run. Prüfe: Kommt das Echo (`coe_probe listen`)? Zeigt die UVR den Versatz am CAN-Eingang 15 (Visu „EMS“)? Stimmen `own_node` und `own_output` mit der UVR überein? Ist das Echo in `receive` richtig eingetragen? |
| `actuator.failed.thermal.<name>`: {Name}: Sicherwert für „setpoint_offset“ nicht bestätigt. | Fehler | Wie bei jedem Aktor: Das Echo bestätigte die 0 des sicheren Zustands oder der Übergabe nach 3 Versuchen nicht. Meist besteht keine Verbindung (siehe unten); die UVR fällt ohnehin nach 5 min ohne Paket auf 0. |

Dazu meldet die Karte **System** → „Aktoren“ als Hinweis „Keine Werte der Klimaanlage.“, solange die
Quelle nichts liefert, und „Kein Echo des Versatzes von der UVR.“, solange Werte ankommen, aber das
Echo fehlt; in beiden Fällen ist die Klimaanlage nicht steuerbar. Nimmt der Adapter einen Versatz
nicht an, weil sein Port nicht offen ist, steht im Entscheidungslog „nicht gesendet“ mit
„CoE-Verbindung: Versatz nicht angenommen.“ Die Home-Assistant-Warnung „Gerät offline“ nennt die
Klimaanlage, wenn der Adapter getrennt ist.

### Was bei „Keine Werte der Klimaanlage“ zu tun ist

Die Karte „Klimaanlage“ zeigt dann „Keine Werte“ statt „Werte kommen an“, und der Grund lautet
„Keine Werte der Klimaanlage, Versatz 0“ (`thermal_stale`). Der Adapter hat seit `stale_after_s`
(180 s) kein gültiges Paket vom C.M.I. bekommen. Der core sendet dann nur 0, und die UVR fällt
spätestens nach 5 min von selbst auf 0 zurück: Es besteht keine Gefahr, aber die Klimaanlage
bekommt keinen PV-Überschuss mehr. Prüfe der Reihe nach:

1. **Zahlen der Karte:** „Letzter Empfang“ nennt, wann das letzte gültige Paket ankam. „Fremde
   Pakete“ (Absender nicht `cmi_host`, oder kein Wert der Empfangstabelle) und „Fehlerhafte Pakete“
   zeigen, ob Pakete ankommen, aber nicht gelten; „Werte mit falscher Einheit“ zählt Werte, deren
   Einheit oder Art nicht zur Empfangstabelle passt. Health: `GET /v1/health`, `thermal.<name>`
   (`connected`, `last_packet_at`, `foreign_packets`, `bad_packets`, `unit_mismatch`).
2. **Absender und Port:** Sendet das C.M.I. an `192.168.82.20`, Port 5442? Ein anderer Absender als
   `cmi_host` (etwa eine NAT-Adresse hinter einer Firewall) wird verworfen; setze `cmi_host` auf die
   Adresse, die tatsächlich ankommt (`coe_probe listen`).
3. **Netz:** Routing und Firewall für UDP 5442 zwischen 192.168.81.0/24 und 192.168.82.0/24, beide
   Richtungen (oben). `docker compose ps` zeigt `5442/udp` beim Dienst `ems-core`.
4. **Empfangstabelle:** Mit gestopptem core `coe_probe listen`: Knoten, Ausgang und Einheit müssen
   zu `receive` passen. Ein Paket mit anderer Einheit macht den Wert ungültig (`unit_mismatch`).
5. **Das C.M.I. und die UVR:** Die CAN-Ausgänge für Ist (A9), Soll (A11), Echo (A12), Leistung (A13)
   und die Zustände sind in der UVR eingerichtet und stehen im Sendeschema des C.M.I.
6. **Stimmt alles, steht die Karte kurz nach dem Start auf „Keine Werte“,** bis das erste Paket da
   ist. Das C.M.I. sendet bei Änderung und zyklisch; bleibt ein Wert lange gleich, kann bis zum
   nächsten zyklischen Paket Zeit vergehen.

Hat das C.M.I. nach dem Ausfall wieder Verbindung, regelt der core von selbst weiter: Die
Strategie beginnt bei Wunsch 0 und steigert in Stufen von 10 min.

Was du während des Ausfalls siehst: Die Karte zeigt „Keine Werte“ und den Grund. Das
Entscheidungslog bleibt still, denn solange die Quelle fehlt, erzeugt die Regelung keinen Befehl;
der Adapter sendet die 0 von selbst, sobald er die Quelle als nicht verbunden führt (erwartet rund
180 s plus Überwachungstakt von 10 s nach dem letzten Paket, die Freigabe läuft nach 10 s ab). Ein
Eintrag `thermal_stale` oder `thermal_no_echo` mit Versatz 0 folgt erst nach der Rückkehr der
Werte, und nur, wenn der Versatz vor dem Ausfall nicht 0 war (`thermal_no_echo`, wenn das Echo erst
in einem späteren Paket kommt als die übrigen Werte). InfluxDB hat in dieser Zeit eine Lücke bei `offset_k` und
`offset_sent_k` (der Sampler schreibt nur Werte mit Qualität OK). Die Zeiten sind Erwartungen aus
dem Code, an der Anlage zu bestätigen.

### Anzeige

- **UI:** Die Karte „Klimaanlage“ unter **System** (Zeilen: Betriebsart, Läuft, Ist, Soll ohne
  Versatz, Versatz mit „bestätigt“ oder dem Echo, Wunsch in Stufen, Leistung, „Grundlast … W ·
  freigebbar … W“, Letzter Empfang, Grund). „Warum?“ nennt die Gründe oben.
- **Home Assistant** (Abschnitt 7): je Gerät vier Sensoren am Gerät „BindaEMS“.
- **InfluxDB und Grafana:** Messung `thermal` (Tag `id`) mit `actual_c`, `setpoint_c`, `offset_k`
  (das Echo) und `offset_sent_k`, die Leistung in `power` mit `source=thermal`; das Panel
  „Klimaanlage“ im Dashboard (Abschnitt 3, „Grafana“). Der Wunsch steht im Entscheidungslog.
- **Health:** `GET /v1/health`, `thermal.<name>`: `label`, `connected`, `last_packet_at`,
  `foreign_packets`, `bad_packets`, `mode`, `running`, `actual_c`, `setpoint_c`, `base_setpoint_c`
  (Soll minus Versatz), `offset_sent_k`, `offset_active_k`, `power_w`, `wish`, `reason`, `hint`, `base_w`
  (die gelernte Leistung bei Versatz 0) und `spare_w` (die zuletzt freigebbare Leistung), beide in
  ganzen W. Die Karte „Klimaanlage“ auf „System“ zeigt sie als „Grundlast … W · freigebbar … W“.
- **Statistik:** Die Klimaanlage bleibt in der Abrechnung Hauslast. Als Verbraucher in der Statistik
  legst du sie über ihre Home-Assistant-Entität an (Abschnitt 12, „Verbraucher aus Home Assistant“).

### Abnahme

Die Abnahme der Phase 6a (Spec 18) steht in `docs/verification/abnahme-phase-6a.md`: alle Werte der
Empfangstabelle, der Timeout der UVR, 7 Tage Dry-Run ohne Fehler und im Live-Betrieb der Versatz
innerhalb ±3 K, höchstens eine Stufe je 10 min weg von 0, spätestens einen Zyklus nach einem Wechsel
der Betriebsart oder bei veralteten Werten auf 0 und jeder Versatz durch das Echo bestätigt.

## Simulator

Der Anlagensimulator `bindaems.tools.sim` lässt die echte Laufzeit des core mit ihren echten Aktoren
gegen ein Modell der Anlage laufen, in simulierter Zeit (ein Zyklus je Sekunde). Das Modell hat ein
Netz mit Sicherung, PV, Hauslast, den Akku mit ESS, Peak Shaving und dem Override des Netz-Sollwerts
(er verfällt 300 s nach dem letzten Schreiben; mit Dynamic ESS verwirft der Cerbo ihn), die EVCS mit
dem e-Golf, den Wall Connector mit dem Tesla und Tessie, Ausfälle und einen fremden Regler wie evcc.
Für die Zuordnung kommen Home Assistant dazu, das den e-Golf zu Hause oder weg meldet, ein Gast ohne
Tessie, der auf 1 bis 3 Phasen lädt, und ein Tesla, der woanders angesteckt ist: Tessie meldet ihn
zu Hause und angesteckt, er steckt aber an keiner Wallbox. Jedes Auto lässt sich an jede Wallbox
stecken. Für „Geplant“ meldet Home Assistant den Ladestand des e-Golf (`soc_entity`), die EVCS
zählt ihre Energie wie der GX (`Ac/Energy/Forward`), und Tessie nimmt `set_charge_limit` an. Tessie
und Home Assistant melden den Ladestand abgerundet auf ganze Prozent, Home Assistant nur, wenn er
sich ändert; ein Szenario kann den Ladestand des e-Golf auch ganz unbekannt lassen. Gefälscht sind nur die Geräte: Parser der Adapter, Aktoren, Safety-Schicht und Regelung
sind die echten, und das Modell zeigt sich so, wie die Geräte und Adapter es tun. Der Simulator
verbindet sich mit nichts und schreibt nichts auf die Geräte; er braucht weder Tokens noch Netz. Die
Szenarien sind deterministisch. Der Akku rechnet in allen Szenarien mit 88 % Roundtrip wie die
Planung: Laden speichert `√0,88` der Energie, Entladen zieht `1/√0,88` davon aus dem Akku, auch an
der Grenze zur USV-Reserve.

In der CI läuft jedes der vierzig Szenarien bei jedem Push
(`tests/integration/test_sim_scenarios.py`), dazu eine Abwandlung von `spitze-15min` (Tesla in
Boost, e-Golf in Sofort, Ziel 5 kW), die den Boost-Anteil des Wächters prüft (oben, „Messung“), und
zwei von `klima-auto-vorrang` (unten, nach der Tabelle).
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

- `--szenario NAME` (Pflicht): eines der vierzig Szenarien unten.
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
- Ein Szenario braucht für 30 min bis 2 h simulierte Zeit etwa 3 bis 18 s Rechenzeit; alle
  vierzig zusammen rund 5 min.

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
wieder im Dry-Run, ebenso in den zwei für „Aus“ und Selbststart des Tesla, ab `tesla-aus`. In den
fünf zur Planausführung, ab `plan-halten`, ist er live, ebenso in den fünf für „Geplant“, ab
`geplant-plan`. Ihre Pläne kommen wie von der app als neue Version der Absicht, jeweils mit der
nächsten Versionsnummer; die `id` des Plans ist die Sekunde des Laufs, in der er kommt. In den
Szenarien für „Geplant“ kommen Ziel und Abfahrt zu Beginn als Version 2 der Absicht. In den sechs
für die Klimaanlage, ab `klima-pv-kuehlen`, entscheidet Victron im Dry-Run mit; dort steckt zu
Beginn kein Auto an, und die Klimaanlage ist live.

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
| `plan-halten` | Halten am Abend, 1 h | 12.10.2026 ab 18:00, keine PV, Akku 60 %, nur der e-Golf, in PV (er lädt ohne PV nicht); zu Beginn und alle 15 min ein Plan mit `HALTEN` über 4 Slots, gültig 45 min; L1 +2 kW von 20 bis 25 min | Ab 2 min entlädt der Akku im Mittel jeder Minute höchstens 300 W; der SOC fällt höchstens um 1,5 Prozentpunkte; höchstens 40 Entscheidungen an `victron` je Stunde (das Totband hält sie klein). |
| `plan-netzladen` | Netzladen in der Nacht, 30 min | 13.10.2026 ab 02:00, keine PV, Akku 30 %, nur der e-Golf, in PV; ein Plan mit `NETZLADEN` 3000 W über 2 Slots, gültig 30 min | Ab 2 min liegt der Netzbezug in jeder Sekunde bei Haus + 3000 W, auf ± 300 W; der SOC steigt um mindestens 90 % von 3 kW · 0,5 h · √0,88, geteilt durch die nutzbare Kapazität des Akkus (bei 30 kWh 4,22 Prozentpunkte). |
| `plan-abgelaufen` | Plan läuft ab, 30 min | 12.10.2026 ab 18:00, keine PV, Akku 60 %, nur der e-Golf, in PV; ein Plan mit `HALTEN` über 2 Slots, gültig 20 min, danach keiner | Von 2 bis 20 min entlädt der Akku im Mittel jeder Minute höchstens 300 W; ab 26 min (der Override verfällt 300 s nach dem letzten Schreiben, dazu 60 s) gilt kein Override, und der Akku trägt das Haus (Akkuleistung höchstens −Haus + 300 W). |
| `plan-budget` | Netzladen unter dem Budget, 47 min | 12.10.2026 ab 17:59, keine PV, Akku 60 %, nur der e-Golf, in PV; zu Beginn und zu jeder Viertelstunde ein Plan mit `NETZLADEN` 6000 W und `budget_kw` 4,0 über 4 Slots, gültig 45 min | Netzbezug der Viertelstunden ab 18:00, 18:15 und 18:30 je zwischen 0,9 und 1,02 kWh (ohne Plan trüge der Akku das Haus, und der Netzbezug bliebe nahe 0); Entscheidungen an `victron` nennen „plan_grid_charge“ und „peak_guard“: Das Budget des Slots ersetzt `control.peak_target_kw`, und der Wächter senkt das Netzladen. |
| `plan-boost` | Boost während Halten, 1 h | 12.10.2026 ab 18:00, keine PV, Akku 60 %, nur der e-Golf, in PV, ab 1 min in Boost bis zum Ende (Version 3 der Absicht); Pläne mit `HALTEN` wie in `plan-halten` | Ab 1 min lädt der e-Golf in mindestens 90 % der Sekunden in Boost: Jeder neue Plan behält den Boost; höchstens 5 % seiner Energie kommen aus dem Akku (ohne Übergänge), denn unter `HALTEN` lädt Boost aus dem Netz. |
| `geplant-plan` | Geplant mit Plan, der Plan läuft ab, 1 h | 12.10.2026 ab 18:00, keine PV, Akku 60 %, nur der Tesla mit 60 %, in Geplant, Ziel 70 %, Abfahrt 19:05; zu Beginn ein Plan mit `EIGENVERBRAUCH` über 4 Slots, der den Tesla mit 6900 W nennt, gültig 30 min, danach keiner | Vom ersten Laden bis zum Stopp am Ziel lädt der Tesla in jeder Sekunde mit mindestens 9 A (erst die 6900 W des Plans, nach seinem Ablauf ab der spätesten Startzeit mit voller Leistung, ohne Pause); vom ersten Strombefehl bis 1800 s, solange der Plan gilt, mit 10 A ± 0,5 A, nicht mit voller Leistung (ab 3 s nach Strombefehl und erstem Laden: Tessie führt Befehle erst mit ihrer Antwortzeit aus, und der Tesla startet kurz mit dem Strom, der noch im Auto steht); er bekommt „Laden aus“ mit „target_reached“ bei mindestens 70 % und lädt danach nicht mehr. |
| `geplant-spaet` | Geplant ohne Plan, Start zur spätesten Startzeit, 2 h | 12.10.2026 ab 18:00, keine PV, Akku 60 %, nur der e-Golf mit gemessenen 50 %, in Geplant, Ziel 70 %, Abfahrt 20:00, kein Plan | Der e-Golf lädt erst ab der spätesten Startzeit, die die Gesundheit vor seinem ersten Laden nennt (gegen 18:28); die EVCS bekommt „Laden aus“ mit „target_reached“ bei mindestens 70 %, danach lädt er nicht mehr; er erreicht 70 % vor der Abfahrt. |
| `geplant-pv` | Geplant mit PV-Überschuss, 2 h | 21.06.2026 ab 10:00, PV wie `sonnig`, Akku 60 %, nur der Tesla mit 60 %, in Geplant, Ziel 70 %, Abfahrt 07:00 (am nächsten Morgen); zu Beginn und alle 15 min ein Plan mit `EIGENVERBRAUCH`, der den Tesla mit 0 W nennt | Höchstens 5 % der Energie des Tesla kommen aus dem Akku (ohne Übergänge): Er lädt nur aus dem Überschuss, wie ein PV-Auto; er bekommt „Laden aus“ mit „target_reached“ bei mindestens 70 % und lädt danach nicht mehr, obwohl die Sonne weiter scheint. |
| `geplant-unbekannt` | Geplant ohne Ladestand, 2 h | wie `geplant-spaet`, aber Home Assistant meldet keinen Ladestand des e-Golf; Abfahrt 23:15 | Jede Gesundheit, die einen Ladestand des e-Golf nennt, nennt ihn „angenommen“ mit 0 %; er lädt erst ab der spätesten Startzeit für 0 % (gegen 18:31) und beginnt binnen 30 s danach; keine Entscheidung nennt „target_reached“. |
| `tesla-ladelimit` | Ladelimit des Tesla an das Ziel angleichen, 1 h | 12.10.2026 ab 18:00, keine PV, Akku 60 %, nur der Tesla mit 65 % und dem Ladelimit 60 % im Auto, also „Complete“, `charge_limit_sync` an, in Geplant, Ziel 75 %, Abfahrt 19:00 (die späteste Startzeit ist schon vorbei) | Genau eine Entscheidung „Ladelimit 75 %“ an den Tesla, keine andere über das Ladelimit; am Ende zeigt die Gesundheit das Ladelimit 75 %; nach dem Laden meldet der Tesla „Complete“ bei 75 %. |
| `sofort-limit` | Sofort mit Ladelimit, 1 h | 15.07.2026 ab 12:00, sonniger Tag, Akku 60 %, der e-Golf an der EVCS in Sofort mit dem Höchststrom 8 A (`now_max_a`), der Tesla am Wall Connector mit 50 % SOC in PV | Kein Strombefehl an die EVCS über 8 A, und der e-Golf lädt nie mit mehr; der Tesla lädt in mindestens der halben Zeit aus dem Überschuss. |
| `klima-pv-kuehlen` | Klimaanlage nimmt PV-Überschuss beim Kühlen, 1 h | 15.07.2026 ab 12:00, PV konstant, Akku 100 %, die Klimaanlage kühlt mit 600 W, ohne Versatz 1,5 kW Einspeisung | Der Versatz an der UVR wechselt nur in Schritten von −0,5 K im Abstand von mindestens 600 s, mindestens dreimal; er liegt zwischen −3 K und 0 K und geht nur in Richtung „mehr“; jede Entscheidung an die Klimaanlage nennt `thermal_more`; die Einspeisung liegt in den letzten 10 min im Mittel unter 800 W. |
| `klima-auto-vorrang` | Klimaanlage weicht einem Auto im Modus PV, 80 min | wie `klima-pv-kuehlen`, aber ohne Versatz 4 kW Einspeisung; ab 40 min steckt der e-Golf in PV an der EVCS | Bis 40 min mindestens drei Schritte von −0,5 K; danach geht der Versatz nur in Schritten von 0,5 K zurück, jede Entscheidung dazu nennt `thermal_cars`, und er wird nie größer; am Ende wendet die UVR 0 K an, der e-Golf lädt, und die Einspeisung liegt in den letzten 10 min im Mittel unter 800 W. |
| `klima-moduswechsel` | Betriebsart der Klimaanlage wechselt, 50 min | wie `klima-pv-kuehlen`; bei 2115 s, nach drei Stufen, wechselt die Klimaanlage auf Heizen | Vor dem Wechsel drei Schritte bis −1,5 K; in der Sekunde des Wechsels bekommt die UVR 0 K, und die Regelung wünscht 0 Stufen mit `thermal_mode`; kein Versatz an der UVR gegen „mehr“ der Betriebsart, keine Entscheidung vor dem Wechsel über 0 K und danach unter 0 K. |
| `klima-paketverlust` | Pakete der Klimaanlage bleiben aus, 35 min | wie `klima-pv-kuehlen`; ab 25 min sendet das C.M.I. nichts mehr | Vor 25 min −1 K; die erste 0 K an der UVR kommt 180 s nach dem letzten Paket (höchstens 10 s später, im Takt der Überwachung des Adapters), die Regelung wünscht dort 0 Stufen mit `thermal_stale`, danach bekommt die UVR nur 0 K. |
| `klima-spitze` | Spitze droht beim Heizen, 40 min | 15.12.2026 ab 11:00, PV konstant, Multi auf „Nur Laden“, die Klimaanlage heizt mit 1500 W, ohne Versatz 1,6 kW Einspeisung, Ziel des 15-min-Wächters 5 kW; ab 30 min zieht das Haus 8 kW mehr | Vor 30 min +1 K; in der ersten Sekunde, in der der Wächter Druck macht, bekommt die UVR 0 K, und die Regelung wünscht 0 Stufen mit `thermal_peak`; danach bekommt die UVR nur 0 K. |
| `klima-teuer-heizen` | Teurer Strom beim Heizen, 1 h | 15.12.2026 ab 18:00, keine PV, Akku 60 %, die Klimaanlage heizt mit 1500 W; zu Beginn und alle 15 min ein Plan mit `EIGENVERBRAUCH` und dem Hinweis „weniger“ für die Klimaanlage | Genau zwei Schritte von −0,5 K im Abstand von mindestens 600 s; der Versatz liegt zwischen −1 K und 0 K; jede Entscheidung an die Klimaanlage nennt `thermal_less`. |

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

In `geplant-plan` gilt der Plan 30 min statt der 45 min, die die app einem Plan gibt. Grund ist der
Abstand zum Ende des einstündigen Laufs: Mit 45 min Plan erreicht der Tesla 70 % erst bei rund
3530 s und stoppt bei rund 3545 s, knapp eine Minute vor dem Ende, und die Prüfung „lädt danach
nicht mehr“ sähe kaum etwas. Mit 30 min stoppt er bei rund 3215 s, gut sechs Minuten vor dem Ende,
und das Szenario zeigt zudem, dass er nach dem Ablauf des Plans ohne Pause mit voller Leistung
weiterlädt. Stopp und Strombefehle an den Tesla prüfen die Szenarien in der Sekunde, in der der
Befehl Tessie erreicht, nicht in der seines späteren Ergebnisses; an der EVCS gilt die Sekunde der
Entscheidung.

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

In den Szenarien für die Klimaanlage hängt die Klimaanlage des Modells am echten CoE-Adapter des
core, über einen Port im Prozess statt über UDP. Die UVR im Modell wendet jeden Versatz sofort an,
meldet ihn als Echo zurück und fällt nach 5 min ohne Paket auf 0 K zurück; das C.M.I. schickt alle
10 s die Werte der Empfangstabelle, nach einem Versatz und einem Wechsel der Betriebsart sofort.
Die Leistung folgt mit 300 s erster Ordnung der Grundleistung plus 400 W je Kelvin in Richtung
„mehr“, höchstens 3 kW. Das Haus rauscht dort nur mit ± 20 W, und die PV ist konstant: So hängt
jede Stufe an der Regel und nicht am Zufall. Eine Entscheidung an die Klimaanlage trägt die Zeit
ihrer Bestätigung durch das Echo, meist eine Sekunde nach dem Paket an die UVR; die Prüfungen lesen
darum, was die UVR bekam. Abweichungen von der Tabelle des Plans:

- `klima-auto-vorrang` speist ohne Versatz 4 kW statt 1,5 kW ein. Mit 1,5 kW startet der e-Golf
  nie (6 A auf zwei Phasen sind 2760 W): Einspeisung und Klimaanlage erreichen seine
  Startleistung nicht, er zählt nicht als offener Wunsch, und die Klimaanlage nimmt den
  Überschuss. Eine Abwandlung in der CI hält das fest.
- `klima-spitze` steht auf „Nur Laden“, damit der Akku die Last nicht deckt, und die Last kommt
  zu Beginn einer Viertelstunde, denn der 15-min-Wächter rechnet je Viertelstunde.

Die zweite Abwandlung lässt den e-Golf nach 65 min nur noch 6 A nehmen (er regelt ab). Bei 8 A
begrenzt er sich nach der Haltezeit selbst, und die Klimaanlage nimmt den Überschuss; senkt die
Laderegelung darauf auf 7 A, bleibt er selbst begrenzt, und die Klimaanlage flackert nicht. Ein
Auto, das die Regelung gerade startet, gilt im Anlauf als seinem Befehl folgend: Die Klimaanlage
geht beim Start des e-Golf keine Stufe hoch (Ruling R23).

### Was das Modell nicht leistet

Der Simulator zeigt, ob die Regelung bei bestimmten Lasten, Wolken und Ausfällen die Grenzen hält. Die
Zeiten von Tessie (Antwort nach 0,2 bis 2,5 s, Abfragen alle 30, 60 oder 600 s), der verlorene erste
5-A-Befehl und die Ladekurven der Autos sind Annahmen aus dem Prüfprotokoll Teil 2 und der Spec. Ein
Tessie-Ausfall trennt wie beim echten Adapter die Quelle nicht: Die Werte veralten, Befehle scheitern
mit HTTP 503, und die Abfragen laufen im Backoff des Adapters (60 s, verdoppelt bis 900 s). Ein grüner
Simulator ersetzt weder das Prüfprotokoll Teil 2 noch den Dry-Run an der Anlage.

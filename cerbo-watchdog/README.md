# BindaEMS-Watchdog für den Cerbo GX

Der Totmann-Schalter der Anlage (Spec 8.6): ein kleiner Python-Dienst auf dem Victron Cerbo GX
(Venus OS). Solange das EMS Victron oder die EVCS live regelt, schickt es alle 10 s einen
Heartbeat. Bleibt er aus, weil die VM, das Netz oder der core ausgefallen ist, stellt der Watchdog
selbst den sicheren Zustand her.

## Was der Watchdog tut

### Zustände

| `/State` | Zustand | Bedeutung |
|---|---|---|
| 0 | wartet | wartet auf einen stabilen Heartbeat; greift nie ein |
| 1 | scharf | löst aus, wenn der Heartbeat `timeout_s` lang ausbleibt |
| 2 | ausgelöst | führt die Aktionen aus; wird wieder scharf, sobald der Heartbeat `rearm_s` lang stabil läuft |
| 3 | deaktiviert | `enabled = false` in der `config.ini`; ignoriert alles |

### Zeiten

Die Zeiten stehen im Abschnitt `[watchdog]` der `config.ini`:

- `timeout_s` (90 s): so lange darf der Heartbeat ausbleiben, solange der Watchdog scharf ist
- `rearm_s` (30 s): so lange müssen die Heartbeats lückenlos laufen, bis er scharf wird
- `heartbeat_gap_s` (25 s): der größte Abstand zweier Heartbeats, der noch als lückenlos gilt
- `retries` (6) und `retry_s` (5 s): Wiederholungen einer gescheiterten Aktion und ihr Abstand

### Heartbeat und Freigabe mit 0

- Das EMS schreibt einen Zähler (1, 2, 3, …) per MQTT auf `W/<portal>/bindaems/0/Heartbeat`; auf
  dem Cerbo ist das der D-Bus-Pfad `/Heartbeat`. Angenommen werden nur ganze Zahlen ab 0.
- Die **0 gibt den Watchdog frei:** Er geht ohne Auslösung zurück auf „wartet“. Das EMS sendet
  sie, sobald kein Victron- oder EVCS-Aktor mehr live angefordert ist.
- Ob der Watchdog scharf ist, steht in `/data/bindaems-watchdog/state.json`. Nach einem Neustart
  des Cerbo startet er wieder scharf, und das EMS hat `timeout_s`, sich zu melden. Lässt sich die
  Datei nicht schreiben (z. B. `/data` voll), versucht er es jede Sekunde erneut.

### Aktionen bei der Auslösung

Je Abschnitt `[action:<name>]` der `config.ini` bringt der Watchdog einen D-Bus-Pfad auf seinen
Sicherwert:

- EVCS auf den sicheren Strom (`/SetCurrent` des evcharger-Dienstes mit der eingetragenen Instanz)
- Min-SOC auf die USV-Reserve
- Entladegrenze unbegrenzt (`-1`)

Jede Aktion liest zuerst und schreibt nur, wenn der Wert abweicht; danach liest sie zurück und
vergleicht. Zahlen gelten bis `tolerance` (Standard 0,5) als gleich. Ein gescheiterter Versuch
folgt nach `retry_s` erneut, höchstens `retries` Mal. Die Aktionen laufen nur, solange der
Watchdog ausgelöst ist: Wird er mit 0 freigegeben oder wieder scharf, endet der Lauf sofort, und
noch offene Aktionen bleiben offen. `/LastTripFailures` steht mit jeder Auslösung auf 0 und zählt
am Ende des Laufs die fehlgeschlagenen Aktionen. Solange der Watchdog wartet oder scharf ist,
schreibt er nichts.

### D-Bus-Dienst `com.victronenergy.bindaems` (DeviceInstance 0)

| Pfad | Inhalt |
|---|---|
| `/Heartbeat` | beschreibbar; der zuletzt angenommene Wert, nach dem Start ungültig |
| `/State` | 0 wartet, 1 scharf, 2 ausgelöst, 3 deaktiviert |
| `/TripCount` | Zahl der Auslösungen |
| `/LastTripTime` | letzte Auslösung in Unix-Sekunden, 0 = nie |
| `/LastTripFailures` | fehlgeschlagene Aktionen der letzten Auslösung; 0, bis ihr Lauf endet |
| `/TimeoutSeconds` | `timeout_s` |
| `/Version` | Version des Watchdogs; das EMS warnt, wenn sie nicht zu seiner passt |

## Voraussetzungen

- Venus OS mit Python 3.8 oder neuer; `install.sh` prüft das vor der Installation.
- SSH-Zugang als `root` zum Cerbo (Root-Passwort setzen und SSH einschalten, siehe die
  Victron-Anleitung „Root Access“). Hinterlege am besten einen SSH-Schlüssel
  (`ssh-copy-id root@cerbo.lan`); sonst fragt `install.sh` mehrmals nach dem Passwort.
- Ein Rechner mit `bash`, `ssh`, `tar` und diesem Repository, z. B. die VM des EMS.

## `config.ini` erzeugen

Die Datei entsteht aus der `config.yaml` des EMS. Ändere sie nicht von Hand, sondern die
`config.yaml`, und erzeuge sie neu:

```bash
cd /opt/bindaems
docker compose run --rm -T --entrypoint python ems-core \
  -m bindaems.tools.watchdog_config --config /config/config.yaml > config.ini
```

Das Format zeigt `config.ini.example`. Das Werkzeug meldet auf der Fehlerausgabe, was die Datei
nicht leisten kann:

- Eine EVCS ohne GX-Instanz in `victron.instances.evcharger` ergibt eine Warnung (und einen
  Kommentar in der Datei): Der Watchdog setzt für sie keinen sicheren Strom.
- Ist 2,5 × `victron.watchdog.heartbeat_s` nicht kleiner als `timeout_s`, bricht es mit dem
  Exitcode 2 ab: Der Watchdog lehnte eine solche Datei beim Start ab.

## Installieren

```bash
cd cerbo-watchdog   # im Repository
./install.sh root@cerbo.lan /opt/bindaems/config.ini
```

Das Skript

1. prüft per ssh, ob das Gerät passt (Python ≥ 3.8, Venus OS, nötige Module),
2. überträgt Programm und `config.ini` mit `tar` über ssh nach `/data/bindaems-watchdog/`
   (ohne SFTP, das Venus OS nicht immer anbietet); das Verzeichnis übersteht Firmware-Updates,
3. trägt in `/data/rc.local` den Block zwischen `# bindaems-watchdog begin` und
   `# bindaems-watchdog end` ein, der den Dienst bei jedem Start einhängt,
4. hängt den Dienst sofort unter die Dienstüberwachung von Venus OS (`/service/bindaems-watchdog`)
5. und zeigt nach ein paar Sekunden Version und Zustand.

Nach der Erstinstallation meldet er „Zustand: 0“ (wartet). Scharf wird er erst, wenn das EMS
Heartbeats schickt. Außerhalb von `/data/bindaems-watchdog/`, dem Link und seinem Block in
`/data/rc.local` ändert das Skript nichts.

## Prüfen

Den Stand liest du jederzeit direkt am Cerbo:

```bash
ssh root@cerbo.lan dbus -y com.victronenergy.bindaems /State GetValue
```

Ob die Kette vom EMS bis zu den Aktionen funktioniert, zeigt die Watchdog-Probe (Prüfprotokoll
Teil 2, Spec 17.2 Punkt 5). **Die Probe löst den Watchdog aus:** Er setzt die EVCS auf den sicheren
Strom und schreibt Min-SOC und Entladegrenze, wenn sie abweichen. Sie fragt deshalb vorher nach.

```bash
cd /opt/bindaems
mkdir -p verification && sudo chown 10001 verification   # der Container läuft als UID 10001
docker compose run --rm -v "$PWD/verification:/out" --entrypoint python ems-core \
  -m bindaems.tools.watchdog_probe --config /config/config.yaml \
  --out /out/<datum>-watchdog-probe.md
```

Mit Strg+C brichst du die Probe ab: Sie gibt den Watchdog vorher mit dem Heartbeat 0 frei und endet
mit dem Exitcode 130, ohne Bericht. Dasselbe versucht sie, wenn sie aus anderem Grund endet,
etwa bei Verbindungsverlust; dann bleiben die bis dahin gewonnenen Befunde im Bericht. Gelingt die
Freigabe nicht, steht das im Bericht und auf der Konsole: Der Watchdog kann dann scharf bleiben
und später auslösen oder ausgelöst bleiben. Zurück geht es, indem du die Probe erneut startest
(sie endet mit der Freigabe) oder am Cerbo
`dbus -y com.victronenergy.bindaems /Heartbeat SetValue 0` ausführst.

## Aktualisieren

Erzeuge die `config.ini` neu, falls sich die `config.yaml` geändert hat, und rufe `install.sh`
wie bei der Installation auf. Das Skript ersetzt Programm, `config.ini` und den Block in
`/data/rc.local` und startet den Dienst neu. `state.json` bleibt: Ein scharfer Watchdog startet
wieder scharf.

## Entfernen

```bash
./install.sh --uninstall root@cerbo.lan
```

Das hält den Dienst an und entfernt den Link `/service/bindaems-watchdog`, den Block in
`/data/rc.local` und das Verzeichnis `/data/bindaems-watchdog/` samt `state.json`. Ohne Watchdog
gehen Victron und EVCS nicht live (Spec 8.7).

## Logs

Der Dienst schreibt nach `/var/log/bindaems-watchdog/current` (multilog, flüchtig: nach einem
Neustart des Cerbo ist das Log leer):

```bash
ssh root@cerbo.lan 'tail -n 50 /var/log/bindaems-watchdog/current | tai64nlocal'
```

Dort stehen der Start mit Version, Aktionen und Startzustand, jeder Zustandswechsel, jede
Auslösung mit dem Ergebnis jeder Aktion und abgelehnte Heartbeats.

## Aufbau

| Datei | Inhalt |
|---|---|
| `bindaems-watchdog.py` | Startskript |
| `bindaems_watchdog/` | das Paket: `config`, `logic`, `state_file`, `actions`, `service` |
| `service/run`, `service/log/run` | Dienst und Log für daemontools |
| `install.sh` | Installation, Update und Entfernen per ssh |
| `config.ini.example` | Beispiel der erzeugten `config.ini` |

Der Watchdog braucht nur Python ≥ 3.8 und die D-Bus-Bibliotheken des Geräts (`dbus`, `GLib`,
`vedbus`); er importiert nichts aus `bindaems`. Die Tests laufen im Hauptprojekt ohne D-Bus:
`uv run pytest tests/unit/cerbo_watchdog`.

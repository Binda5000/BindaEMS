# BindaEMS-Watchdog für den Cerbo GX

Der Totmann-Schalter der Anlage (Spec 8.6): ein kleiner Python-Dienst auf dem Victron Cerbo GX
(Venus OS). Solange das EMS Victron oder die EVCS live regelt, schickt es alle 10 s einen
Heartbeat. Bleibt er aus, weil die VM, das Netz oder der core ausgefallen ist, stellt der Watchdog
selbst den sicheren Zustand her.

Die aktuelle Version ist **1.1.0**; das EMS erwartet genau sie (`/Version`). Läuft noch eine andere,
meldet es „bitte mit install.sh aktualisieren“ (siehe „Aktualisieren“).

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
- Ob der Watchdog scharf oder ausgelöst ist, steht in `/data/bindaems-watchdog/state.json`; beides
  gilt dort als „scharf“. Nach einem Neustart des Cerbo startet er deshalb scharf, und das EMS hat
  `timeout_s`, sich zu melden. Bleibt der Heartbeat danach aus, löst er erneut aus: Die Aktionen
  lesen zuerst und schreiben nur, was abweicht. Die Datei wird samt Verzeichnis auf die Platte
  gesichert (`fsync`); scheitert nur das Sichern des Verzeichnisses, ist das kein Fehler, denn der
  Inhalt steht schon. Lässt sich die Datei nicht schreiben (z. B. `/data` voll), versucht er es
  jede Sekunde erneut.

### Aktionen bei der Auslösung

Je Abschnitt `[action:<name>]` der `config.ini` bringt der Watchdog einen D-Bus-Pfad auf seinen
Sicherwert:

- EVCS auf den sicheren Strom (`/SetCurrent` des evcharger-Dienstes mit der eingetragenen Instanz)
- Min-SOC auf die USV-Reserve
- Entladegrenze unbegrenzt (`-1`)
- Netz-Sollwert-Override des EMS aufheben (`/Overrides/Setpoint` des Dienstes
  `com.victronenergy.hub4`, Wert `null`): nur mit `victron.live_allowed`. Ohne die Freigabe von
  Victron gehört der Override nicht dem EMS, denn Dynamic ESS nutzt ihn; der Watchdog lässt ihn
  dann unberührt. `null` steht für „ungültig“ (auf D-Bus ein leeres Array): Der Watchdog schreibt
  es so, und ein Override, der schon ungültig ist, gilt als erledigt, ohne Schreiben.

Jede Aktion liest zuerst und schreibt nur, wenn der Wert abweicht; danach liest sie zurück und
vergleicht. Zahlen gelten bis `tolerance` (Standard 0,5) als gleich. Ein gescheiterter Versuch
folgt nach `retry_s` erneut, höchstens `retries` Mal. Die Aktionen laufen nur, solange der
Watchdog ausgelöst ist: Wird er mit 0 freigegeben oder wieder scharf, endet der Lauf sofort. Eine
offene Aktion, die schon gescheitert ist, zählt dann als fehlgeschlagen; eine noch nie versuchte
bleibt offen. `/LastTripFailures` steht mit jeder Auslösung auf 0 und zählt am Ende des Laufs die
fehlgeschlagenen Aktionen. Solange der Watchdog wartet oder scharf ist, schreibt er nichts.

### D-Bus-Dienst `com.victronenergy.bindaems` (DeviceInstance 0)

| Pfad | Inhalt |
|---|---|
| `/Heartbeat` | beschreibbar; der zuletzt angenommene Wert, nach dem Start ungültig |
| `/State` | 0 wartet, 1 scharf, 2 ausgelöst, 3 deaktiviert |
| `/TripCount` | Zahl der Auslösungen |
| `/LastTripTime` | letzte Auslösung in Unix-Sekunden, 0 = nie |
| `/LastTripFailures` | fehlgeschlagene Aktionen der letzten Auslösung; 0, bis ihr Lauf endet |
| `/TimeoutSeconds` | `timeout_s` |
| `/Version` | Version des Watchdogs (1.1.0); das EMS warnt, wenn sie nicht zu seiner passt |

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
  -m bindaems.tools.watchdog_config --config /config/config.yaml > config.ini.new \
  && mv config.ini.new config.ini
```

Erst wenn das Werkzeug ohne Fehler endet, ersetzt `mv` die bisherige Datei; mit `> config.ini`
bliebe nach einem Fehler eine leere Datei zurück. Das Format zeigt `config.ini.example`. Mit
`victron.live_allowed` hängt das Werkzeug die Aktion `override_setpoint` an (seit Version 1.1.0);
ohne steht an ihrer Stelle eine Kommentarzeile. Das Werkzeug meldet auf der Fehlerausgabe, was die
Datei nicht leisten kann:

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

1. lehnt eine leere `config.ini` oder eine ohne Abschnitt `[watchdog]` ab, noch bevor es das Gerät
   anspricht (Exitcode 2),
2. prüft per ssh, ob das Gerät passt (Python ≥ 3.8, Venus OS, nötige Module), und bricht ab, wenn
   `/data/rc.local` eine `exit`-Zeile ohne Einrückung enthält (der Block stünde dahinter und
   liefe beim Start nie),
3. überträgt Programm und `config.ini` mit `tar` über ssh nach `/data/bindaems-watchdog/neu/`
   (ohne SFTP, das Venus OS nicht immer anbietet),
4. prüft dort die neue `config.ini` mit dem neuen Programm: Lehnt es sie ab, bricht das Skript mit
   der Meldung ab, und ein laufender Watchdog bleibt, wie er ist,
5. übernimmt die Dateien nach `/data/bindaems-watchdog/` (das Verzeichnis übersteht
   Firmware-Updates; `state.json` bleibt),
6. trägt in `/data/rc.local` den Block zwischen `# bindaems-watchdog begin` und
   `# bindaems-watchdog end` ein, der den Dienst bei jedem Start einhängt,
7. hängt den Dienst sofort unter die Dienstüberwachung von Venus OS (`/service/bindaems-watchdog`)
8. und zeigt nach ein paar Sekunden Version und Zustand.

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
Strom und schreibt Min-SOC und Entladegrenze, wenn sie abweichen; mit `victron.live_allowed` hebt
er auch den Netz-Sollwert-Override auf. Sie fragt deshalb vorher nach. Nach der Auslösung wartet
sie 45 s ohne Heartbeat, damit der Aktionslauf mit allen Wiederholungen zu Ende kommt. Die Zahl
der fehlgeschlagenen Aktionen (`/LastTripFailures`) liest sie erst, wenn der Watchdog wieder scharf
ist: Dann ist der Lauf zu Ende, und der Wert ist der Endstand. Die Zeile „Auslösung ohne Heartbeat“
steht im Bericht trotzdem vor „Wieder scharf“. Wird der Watchdog nicht wieder scharf oder endet die
Probe vorher, kennt sie den Endstand nicht: Die Zeile ist dann eine Warnung, auch bei 0
fehlgeschlagenen Aktionen, mit dem Zusatz „nicht wieder scharf; Zahl noch nicht der Endstand“ (bei
einem Abbruch in der Pause „Abbruch vor dem Ende des Aktionslaufs“).

```bash
cd /opt/bindaems
mkdir -p verification && sudo chown 10001 verification   # der Container läuft als UID 10001
docker compose run --rm -v "$PWD/verification:/out" --entrypoint python ems-core \
  -m bindaems.tools.watchdog_probe --config /config/config.yaml \
  --out /out/<datum>-watchdog-probe.md
```

Prüfe nach jeder Probe das Log des Cerbo (unten, „Logs“), auch wenn der Bericht 0 fehlgeschlagene
Aktionen nennt: „Aktionen abgeschlossen: …“ nennt das Ergebnis jeder Aktion, „Aktionen
abgebrochen …“ einen Lauf, der vorzeitig endete.

Mit Strg+C brichst du die Probe ab: Sie gibt den Watchdog vorher mit dem Heartbeat 0 frei und endet
mit dem Exitcode 130, ohne Bericht. Dasselbe versucht sie, wenn sie aus anderem Grund endet,
etwa bei Verbindungsverlust; dann bleiben die bis dahin gewonnenen Befunde im Bericht. Gelingt die
Freigabe nicht, steht das im Bericht und auf der Konsole: Der Watchdog kann dann scharf bleiben
und später auslösen oder ausgelöst bleiben. Zurück geht es, indem du die Probe erneut startest
(sie endet mit der Freigabe) oder am Cerbo
`dbus -y com.victronenergy.bindaems /Heartbeat SetValue 0` ausführst. Prüfe danach, dass `/State`
0 ist: `dbus -y com.victronenergy.bindaems /State GetValue`.

Nach der Probe stehen die EVCS auf dem sicheren Strom, der Min-SOC auf der USV-Reserve und die
Entladegrenze auf unbegrenzt, sofern sie vorher abwichen; mit `victron.live_allowed` ist auch der
Netz-Sollwert-Override aufgehoben. Prüfe alle und stelle sie wieder ein, wie du sie haben willst.

## Aktualisieren

Erzeuge die `config.ini` neu, falls sich die `config.yaml` geändert hat, und rufe `install.sh`
wie bei der Installation auf. Das Skript ersetzt Programm, `config.ini` und den Block in
`/data/rc.local` und startet den Dienst neu. `state.json` bleibt: Ein scharfer oder ausgelöster
Watchdog startet wieder scharf.

### Von 1.0.0 auf 1.1.0

Neu in 1.1.0:

- Ein ausgelöster Watchdog übersteht einen Neustart des Cerbo als „scharf“ (siehe „Heartbeat und
  Freigabe mit 0“).
- `state.json` wird samt Verzeichnis auf die Platte gesichert.
- Mit `victron.live_allowed` hebt der Watchdog bei einer Auslösung den Netz-Sollwert-Override auf
  (siehe „Aktionen bei der Auslösung“).
- Die Watchdog-Probe liest die Zahl der fehlgeschlagenen Aktionen erst nach „Wieder scharf“.

Erzeuge die `config.ini` neu (sie enthält die neue Aktion, sobald `victron.live_allowed` gilt) und
rufe `install.sh` auf. Bis dahin meldet das EMS: „Watchdog-Version 1.0.0 am Cerbo, erwartet 1.1.0;
bitte mit install.sh aktualisieren.“

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

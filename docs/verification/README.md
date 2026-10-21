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
  - Der Bericht nennt Seriennummer und Namen der EVCS sowie die Namen der HA-Messungen nicht;
    sie stehen nur in den Rohdaten.
- Ist eine Quelle nicht erreichbar, steht im Bericht ein FEHLER mit der Meldung. Die übrigen
  Prüfungen laufen trotzdem.

### Danach

1. Abweichungen gegenüber `config.yaml` korrigieren, z. B. Instanzen unter
   `victron.instances` oder die erwarteten Werte unter `victron.expected`.
2. Warnungen bewerten. Zwei Beispiele:
   - Die Vorzeichenkonvention des Netzstroms lässt sich nur bei Einspeisung über 100 W
     bestimmen.
   - Ein aktiver Ladeplan im Fahrzeug muss aus.
   - „EVCS-Registerabbild leer“: Die EVCS antwortet über Modbus TCP nur Adressen auf ihrer
     IP-Whitelist. Andere Adressen bekommen trotzdem eine Verbindung, aber nur Nullen. Zuerst
     die Adresse der VM in den Einstellungen der EVCS auf die Whitelist setzen und Teil 1
     wiederholen. Hilft das nicht, `host`, `port` und `unit_id` mit dem Weg vergleichen, über
     den der Cerbo die Wallbox liest; ihn nennt der Bericht.
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

## Teil 2 – schreibend (17.2)

Teil 2 (Spec 17.2) schreibt auf Geräte und läuft gemeinsam mit dem Betreiber. Das Werkzeug
`bindaems.tools.verify_part2` führt die Punkte `evcs`, `tesla`, `victron` und `thermal` aus. Es nutzt
dieselben Adapter und Aktoren wie der core, prüft jeden Wert mit den Regeln 1–3 der Safety-Schicht
und fragt vor jedem schreibenden Schritt nach. Nach jedem Schritt und bei Abbruch stellt es die
vorgefundenen Werte wieder her. Bedienung, Rückfragen, Abbruch und Voraussetzungen stehen im
Betriebshandbuch, Abschnitt 11. Die Watchdog-Probe (17.2-5) steht im nächsten Abschnitt; der
Punkt `evcs` führt sie mit aus.

**Reihenfolge:** EVCS und Tesla jetzt, gemeinsam mit dem Betreiber. Victron nur mit
abgeschaltetem Dynamic ESS; für den Test genügt es, DESS vorübergehend abzuschalten
(Betriebshandbuch 11). Solange DESS an ist, verweigert der Punkt, ohne zu schreiben. Der Punkt
`thermal` (17.2-9, Klimaanlage) kommt mit Plan 6a und läuft nur mit gestopptem core.

### Aufruf

Der Aufruf erfolgt auf der VM im Container, mit denselben Secrets wie der core (`.env`):

```bash
cd /opt/bindaems
mkdir -p verification && sudo chown 10001 verification
docker compose run --rm -v "$PWD/verification:/out" --entrypoint python ems-core \
  -m bindaems.tools.verify_part2 --config /config/config.yaml --punkt evcs \
  --out /out/<Datum>-pruefprotokoll-teil2-evcs.md
```

Lokal aus dem Repository geht es mit
`uv run python -m bindaems.tools.verify_part2 --config deploy/config.yaml --punkt evcs`. Ohne
`--out` liegt der Bericht dann unter `docs/verification/<Datum>-pruefprotokoll-teil2-<punkt>.md`.

Der Schlaf-Test des Tesla (`--punkt tesla --schlafend`) bekommt im Container mit `--out` einen
eigenen Namen, sonst überschreibt er den Bericht des Tesla-Punkts desselben Tages:

```bash
docker compose run --rm -v "$PWD/verification:/out" --entrypoint python ems-core \
  -m bindaems.tools.verify_part2 --config /config/config.yaml --punkt tesla --schlafend \
  --out /out/<Datum>-pruefprotokoll-teil2-tesla-schlafend.md
```

| Option | Bedeutung |
|---|---|
| `--punkt evcs`, `tesla`, `victron` oder `thermal` | Pflicht: welcher Punkt läuft |
| `--config` | Pfad der `config.yaml` (Pflicht) |
| `--wallbox <Name>` | nur bei `evcs`: die EVCS (Standard: die erste unter `wallboxes`) |
| `--fahrzeug <Name>` | nur bei `tesla`: das Fahrzeug (Standard: das erste mit `tessie`) |
| `--geraet <Name>` | nur bei `thermal`: die Klimaanlage (Standard: das erste Gerät unter `thermal.devices`) |
| `--schlafend` | nur bei `tesla`: prüft, ohne etwas zu senden, dass ein schlafendes Auto nicht geweckt wird |
| `--out <Datei>` | der Bericht (Standard siehe oben) |
| `--yes` | beantwortet alle Rückfragen mit Ja und überspringt Schritte mit Handgriff; nicht empfohlen |

### Punkte und Schritte

Jeder Schritt ist eine Zeile im Bericht. Vor jedem Schritt, der schreibt, fragt das Werkzeug nach.

- **`evcs`** (17.2-6, dazu 17.2-5):
  - „EVCS bereit“: steuerbar (Instanz, Cerbo verbunden, manueller Modus), Sollstrom bekannt und
    innerhalb der Grenzen. Sonst endet der Punkt ohne zu schreiben.
  - „Strom N A“ für den Mindeststrom, 10 A (falls dazwischen) und den Höchststrom aus
    `wallboxes`, je mit Rücklesen und der Zeit bis zur Bestätigung.
  - „Laden aus und an“: Bei ladendem Fahrzeug auch die Zeit, bis die Leistung unter 100 W fällt.
  - „Fremde Änderung“: Du änderst den Strom von Hand; die Erkennung des core (3 Treffer in Folge)
    muss ihn bemerken. Entfällt mit `--yes`.
  - „Verbindung unterbrochen“: Du ziehst das Netzwerkkabel der EVCS 60 s ab; der Befund nennt den
    Strom danach. Entfällt mit `--yes`.
  - „Sicherer Strom über den Watchdog“ (17.2-5): Das Werkzeug stellt den sicheren Strom plus 4 A
    ein und führt die Watchdog-Probe aus; danach muss die EVCS den sicheren Strom haben. Die
    Zeilen der Probe stehen vor diesem Schritt.
- **`tesla`** (17.2-7): Die Befehle gehen über Tessie, die Wirkung misst das Werkzeug am Wall
  Connector. Es sendet nur an ein waches Auto, das zu Hause angesteckt ist, und weckt es nie.
  - „Tesla bereit“: steuerbar, Ladestrom bekannt und innerhalb der Grenzen.
  - „Ladestrom 10 A“ (falls zwischen Mindest- und Höchststrom) und „Verhalten bei N A“ mit dem
    Mindeststrom `min_a` des Fahrzeugs: Tessie bestätigt den Befehl, dann folgt der Strom am
    Wall Connector.
  - „Laden aus und an“: Der Schütz des Wall Connectors öffnet; danach geht das Laden wieder an.
  - Mit `--schlafend` läuft nur „Schlafendes Auto wird nicht geweckt“: Das Auto muss schon schlafen;
    10 min lang prüft das Werkzeug, dass der Aktor Befehle verweigert, und sendet nichts. OK ist der
    Schritt nur, wenn Tessie zu Beginn meldet, dass das Auto schläft, und sich am Ende frisch
    meldet, ohne dass es aufgewacht ist. Ist das Auto zu Beginn wach oder sein Zustand unbekannt,
    ist der Schritt FEHLER. Fehlt eine Meldung von Tessie, ist er FEHLER „nicht beobachtbar: …“
    mit dem Grund (Tessie nicht erreichbar, Wert veraltet). Wacht das Auto während der Prüfung
    auf, ist er WARNUNG „Fahrzeug wurde nach … min wach“.
- **`victron`** (17.2-1 bis 17.2-4 und 17.2-8): verweigert, solange Dynamic ESS aktiv ist oder ein
  Netz-Sollwert-Override steht. Vor jedem schreibenden Schritt prüft es Dynamic ESS erneut: Geht
  DESS mitten im Lauf an, ist der Schritt FEHLER, und das Werkzeug schreibt nichts mehr.
  - „Victron bereit“ (17.2-1): Cerbo verbunden, DESS aus, kein Override, Entladegrenze nicht auf
    0 W (die hinterlässt etwa ein abgebrochener Lauf; vor dem Test zurücksetzen). Der Befund nennt
    die vorgefundene Entladegrenze.
  - „Netz-Sollwert-Override“: Override setzen, Wirkung am Netz, mit `null` aufheben. Ging das
    Aufheben gar nicht hinaus, nennt der Befund den Sendefehler; über `null` sagt er dann nichts.
  - „Override ohne Timeout“: Der Override bleibt 10 min stehen, ohne dass das Werkzeug ihn auffrischt.
  - „Override nach Neustart des Cerbo“: Du startest den Cerbo neu. Entfällt mit `--yes`.
  - „Zusammenspiel mit dem persistenten Sollwert“: `AcPowerSetPoint` vor und nach den Schritten;
    das Werkzeug schreibt ihn nie.
  - „Override für die Entladegrenze“ und „Entladesperre“ (17.2-2): Die Entladegrenze geht auf 0 W,
    der Akku darf danach nicht entladen. Sie ist eine gespeicherte Einstellung.
  - „Netzladen“ (17.2-3): Override auf 2000 W (höchstens die Obergrenze für den Netz-Sollwert), der
    Akku lädt aus dem Netz.
  - „Kein Akku ins Auto“ (17.2-4): nur bei ladender EVCS (über 1000 W); der Override entspricht
    ihrer Leistung, genau dem Wert, den die Rückfrage nennt. Der Akku soll nicht entladen.
  - „Importlimit“ (17.2-8): Du schaltest eine Last ein, die eine Phase über 20 A bringen würde; das
    Werkzeug beobachtet nur den höchsten Netzstrom. Entfällt mit `--yes`.

- **`thermal`** (17.2-9, Plan 6a): Die Klimaanlage an der UVR610, Versatz per CoE. Das Werkzeug nutzt
  denselben CoE-Adapter und Aktor wie der core und bindet denselben UDP-Port (5442, Standard): Der
  core ist gestoppt, und im Container steht `--service-ports` am `docker compose run`, sonst kämen
  keine Pakete an. **Watchdog:** Der gestoppte core sendet keinen Heartbeat. Ist EVCS oder Victron
  live, löst der Watchdog am Cerbo nach 90 s aus; führe den Punkt darum nur aus, solange kein Aktor
  live ist (Betriebshandbuch, „Klimaanlage (Phase 6a)“, „Reihenfolge an der Anlage“, und Abschnitt
  4, „Geordnetes Herunterfahren“). Danach startest du den core wieder, wenn er vorher lief. Das
  Werkzeug braucht keine Secrets und fragt den Cerbo nicht. Es sendet nur auf ausdrücklichen Aufruf
  und nach der Bestätigung vor dem Start; mit `coe_probe listen` (hört nur mit, sendet nie)
  prüfst du vorher den Empfang (Betriebshandbuch, „Klimaanlage (Phase 6a)“).
  - „Werte der Empfangstabelle“: Alle Werte kommen binnen `stale_after_s` des Geräts an; sonst endet
    der Punkt, ohne zu senden, und der Befund nennt die fehlenden Werte.
  - „Versatz +0,5 K“: gesendet, das Echo der UVR zeigt ihn binnen 60 s; danach die Rückfrage „Zeigt
    die Visu der UVR „EMS“ +0,5 K?“. Entfällt mit `--yes`.
  - „Versatz 0“: gesendet, das Echo zeigt 0.
  - „Timeout der UVR“: Das Werkzeug sendet +0,5 K und danach bis zu 5 min 30 s nichts mehr; das
    Echo muss von selbst auf 0 fallen. Mit Rückfrage; entfällt mit `--yes` und, mit INFO
    „übersprungen, weil Schritt 2 nicht bestanden hat“, wenn „Versatz +0,5 K“ nicht bestanden hat.
  - „Am Ende: Versatz 0“: Am Ende, nach einem Fehler und nach Strg+C sendet es 0 und wartet auf das
    Echo 0, statt Werte wiederherzustellen.

### Ergebnis

- `JJJJ-MM-TT-pruefprotokoll-teil2-<punkt>.md` enthält den Bericht. Oben stehen die Angaben
  (Werkzeug, Datum, Punkt, Gerät; dazu Firmware der EVCS und Venus OS, soweit gemeldet, und bei
  `--yes` der Vermerk unter „Rückfragen“). Darunter folgt die Tabelle
  `Punkt | Prüfung | Ergebnis | Details` mit den Punkten 17.2-x; die Ergebnisse lauten OK, WARNUNG,
  FEHLER oder INFO.
- Ohne `--out` heißt die Datei mit `--schlafend` `JJJJ-MM-TT-pruefprotokoll-teil2-tesla-schlafend.md`,
  damit sie den Bericht des Punkts nicht überschreibt. Mit `--out`, wie im Aufruf im Container, gilt
  der Name, den du vorgibst: Gib dem Schlaf-Test dann einen eigenen.
- Die Zeilen „Wiederherstellen: …“ nennen jeden Wert, den das Werkzeug zurückgesetzt hat. Ein
  FEHLER dort heißt: Den Wert von Hand prüfen.
- Exitcode 0: Kein Schritt ist FEHLER. 1: ein Schritt ist FEHLER, die Rückfrage vor dem Start wurde
  verneint oder der Bericht ließ sich nicht schreiben (er steht dann auf der Konsole). 2:
  Konfiguration, Secrets oder Aufruf ungültig, auch `--punkt tesla` ohne `BINDAEMS_TESSIE_TOKEN`.
  130: Strg+C. Nach dem Start sind die Werte dann wiederhergestellt, soweit es ging, und der
  Bericht ist geschrieben; bei der Startfrage endet das Werkzeug ohne Bericht und hat noch nichts
  geschrieben.

Den Bericht übernimmst du mit Datum nach `docs/verification/`. Nach jedem Update von Venus OS, der
EVCS-Firmware oder der Wall-Connector-Firmware wiederholst du die betroffenen Punkte.

## Watchdog-Probe (17.2-5)

Die Watchdog-Probe ist Punkt 5 von Teil 2. Sie läuft einzeln mit `bindaems.tools.watchdog_probe`
oder als Teil des Punkts `evcs` von `verify_part2`, dort mit verstelltem EVCS-Strom. Das Werkzeug
spricht MQTT mit dem Cerbo. Es schreibt dort nur den Heartbeat des Watchdogs
(`W/<portal>/bindaems/0/Heartbeat`) und sendet den Keepalive.

**Die Probe löst den Watchdog aus:** Er setzt die EVCS auf den sicheren Strom und schreibt Min-SOC
und Entladegrenze, wenn sie abweichen; mit `victron.live_allowed` hebt er auch den
Netz-Sollwert-Override auf. Darum fragt das Werkzeug vorher nach; `--yes` überspringt die
Rückfrage.

| Schritt | Was geprüft wird |
|---|---|
| Watchdog erreichbar | Der Watchdog antwortet über MQTT mit Version und Zustand. |
| Watchdog-Version | Die Version ist die, die der core erwartet. |
| Scharf nach Heartbeat | Heartbeats machen ihn scharf. |
| Auslösung ohne Heartbeat | Bleiben sie aus, löst er nach dem Timeout aus. Die Probe wartet dann 45 s ohne Heartbeat, bis der Aktionslauf zu Ende ist, und nennt die Zahl der fehlgeschlagenen Aktionen. Die Zahl liest sie erst nach „Wieder scharf“, denn dann ist sie der Endstand; sieht die Probe es nicht, ist die Zeile eine Warnung mit Zusatz. Die Zeile steht trotzdem vor „Wieder scharf“. |
| Wieder scharf | Mit stabilem Heartbeat wird er nach der Auslösung wieder scharf. |
| Freigabe ohne Auslösung | Der Heartbeat 0 bringt ihn auf „wartet“, ohne dass er auslöst. |

Prüfe nach jeder Probe auch das Log des Cerbo (Betriebshandbuch 14, „Probe“ und „Logs“): Die Zeile
„Aktionen abgeschlossen: …“ nennt das Ergebnis jeder Aktion, auch wenn der Bericht 0 fehlgeschlagene
Aktionen nennt.

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
  Auslösung, das erneute Scharfwerden und die Freigabe; verstellte Werte bringt der Punkt `evcs`
  von `verify_part2` ins Spiel.

Den Bericht übernimmst du mit Datum nach `docs/verification/`. Nach jedem Update von Venus OS oder
der EVCS-Firmware wiederholst du die Probe.

## Abnahme Phase 1

Die Checkliste für die Abnahme der Phase 1 (Spec 18) steht in
[`abnahme-phase-1.md`](abnahme-phase-1.md): 7 Tage lückenlose Aufzeichnung, Tages-Energiebilanz
innerhalb ±2 % von VRM, Preise und Prognose sichtbar und nachweislich keine Schreibpfade zu
Geräten. Für die Abnahme kopierst du sie mit Datum, füllst sie an der Anlage aus und checkst sie
hier ein.

## Abnahme Phase 5a

Die Checkliste für die Abnahme des Lastmodells (Phase 5a, Spec 18) steht in
[`abnahme-phase-5a.md`](abnahme-phase-5a.md): 28 Tage ab dem Ausrollen, auf der Seite „Prognosen“
(Zeitraum „28 Tage“) hat „Last Modell“ mindestens 10 % niedrigeren MAE als „Last Profil“, oder das
Profil war automatisch aktiv, solange das Modell es nicht schaffte; Verfahren und Grund stehen in der
Karte „Lastprognose“. Dazu kommen jede Nacht eine neue Version in der Registry, keine Fehler in der
Komponente `load-model` und der Rückfall auf das Profil bei fehlender Modelldatei, an der Anlage
beobachtet. Für die Abnahme kopierst du sie mit Datum, füllst sie an der Anlage aus und checkst sie
hier ein. Das Fahrverhalten (Plan 5b) hat eine eigene Abnahme.

## Abnahme Phase 5b

Die Checkliste für die Abnahme des Fahrverhaltens (Phase 5b, Spec 18) steht in
[`abnahme-phase-5b.md`](abnahme-phase-5b.md): 28 Tage ab dem Ausrollen; für jedes Auto mit Daten
erscheinen Vorschläge mit Konfidenz auf „Prognosen“, „Laden“ und in Home Assistant; für jedes Auto mit
mindestens 10 bewerteten Tagen sind die Trefferquoten der Abfahrt und des Ziels je mindestens 80 %
(`GET /api/learning/driving`, `quality`); mit eingeschalteter Automatik entsteht keine Zeile „geändert“
durch die Automatik in den geplanten Ladungen und keine Änderung während einer laufenden geplanten
Ladung; und ein erwartetes Auto steht im Zeitplan und wird beim Anstecken durch das echte ersetzt,
einmal an der Anlage beobachtet. Für die Abnahme kopierst du sie mit Datum, füllst sie an der Anlage
aus und checkst sie hier ein.

## Abnahme Phase 6a

Die Checkliste für die Abnahme der Phase 6a (Klimaanlage, Spec 18) steht in
[`abnahme-phase-6a.md`](abnahme-phase-6a.md): alle Werte der Empfangstabelle kommen an, der Versatz
fällt 5 min nach dem letzten Paket auf 0, 7 Tage Dry-Run ohne Fehler und im Live-Betrieb der Versatz
nie außerhalb ±3 K, höchstens eine Stufe je 10 min weg von 0, spätestens einen Zyklus nach einem
Wechsel der Betriebsart oder bei veralteten Werten auf 0 und jeder Versatz durch das Echo bestätigt.
Für die Abnahme kopierst du sie mit Datum, füllst sie an der Anlage aus und checkst sie hier ein.

## Abnahme Phase 4

Die Checkliste für die Abnahme der Phase 4 steht in [`abnahme-phase-4.md`](abnahme-phase-4.md). Der
Teil Statistik (Plan 4a) prüft die Abrechnung: 30 Tage ab dem Ausrollen, Netzbezug und Einspeisung im
Abgleich mit den Zählern (`GET /api/ledger/checks` oder die Karte „Abgleich mit den Zählern“ auf der
Seite „Statistik“) innerhalb ±1 %, jeder markierte Tag mit Ursache, einzelne Tage gegen den Zähler des
Netzbetreibers. Der Teil Geplantes Laden (Plan 4b) prüft den Ziel-SOC (mindestens 95 % der machbaren
geplanten Ladungen erreicht, `GET /api/stats?period=month`, Teil `planned`, mit EVCS und Tesla live)
und jedes Bedienelement in Home Assistant in beide Richtungen. Für die Abnahme kopierst
du die Checkliste mit Datum, füllst sie an der Anlage aus und checkst sie hier ein.

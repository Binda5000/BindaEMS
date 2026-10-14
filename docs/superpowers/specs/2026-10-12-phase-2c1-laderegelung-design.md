# Phase 2c-1 – Laderegelung: Design

**Stand: umgesetzt mit Plan 2c-1** (14.10.2026). Der Plan
`docs/superpowers/plans/2026-10-12-phase-2c1-laderegelung.md` setzt dieses Design um; seine
Präzisierungen gehen diesem Dokument vor, wo sie abweichen. Die Spec enthält dazu die Absätze „Stand
Plan 2c-1“ (5.3, 6.5, 7.1 bis 7.3, 7.5, 7.8, 7.9, 8.4 bis 8.6, 16, 18). Beim Bau haben sich diese
Punkte geändert oder sind dazugekommen:

- **Dringend (4.4, 5.4):** Jeder erneute Versuch nach „nicht übernommen“ ist ein normaler Befehl, nie
  dringend, auch der einer Senkung wegen Überlast oder an der Sicherung; er wartet auf die 90-s-Regel
  (7.2). Ein Befehl wegen Überlast, der neu entsteht, weil die Stufe stieg, ist kein Neuversuch und
  bleibt dringend, höchstens einmal je Stufe. Dringend sind sonst nur die Reaktionen auf Überlast und
  Senkungen unter den Ist-Strom, wenn das Ziel die Sicherung ist (Sofort oder Boost ohne
  Reserve-Schutz); Senkungen am Importlimit sind normal.
- **Start (5.3, 5.5):** „Laden an“, auch der Weckstart, folgt nur, solange die Grenze des Wächters für
  das Auto mindestens seinen Mindeststrom beträgt. Ist sie kleiner oder unbekannt, startet das Auto
  nicht; in Min+PV zählt die Grenze an der Sicherung (6.3). Ein Auto, das schon lädt, geht auf den
  Mindeststrom.
- **Reihenfolge im Zyklus (4.2):** Die Laufzeit berechnet die Befehle der Regelung vor denen des
  Keepers. Ausgeführt werden erst die des Keepers, dann die der Regelung, durch einen gemeinsamen
  `SafetyContext`. Die Regelung vergisst ihre letzten eigenen Werte für einen Aktor, wenn seine
  wirksame Betriebsart wechselt (Dry-Run ↔ live), der Keeper ihm einen Wert übergibt oder ein anderes
  System ihn übernimmt, und nach einer Pause für alle Aktoren.
- **Freigabe und Heartbeat (4.5, 10):** Die Freigabe 0 gilt als bestätigt, wenn der Watchdog in jedem
  Zustand das Echo 0 meldet oder „wartet“ mit einem Echo meldet, das keiner der eigenen Zähler ist;
  erst dann vergisst der core seine Zähler. Der Zähler beginnt je Lauf bei
  `1_000_000 + Unix-Sekunden mod 1_000_000_000`.
- **Sicherer Zustand (10):** SAFE zielt nicht auf einen Tesla, bei dem Tessie `online`, `at_home` oder
  `plugged` mit Qualität OK als `False` meldet. Eine Auslösung des Watchdogs lässt den core die
  Erwartungen der Erkennung fremder Änderungen für Victron und EVCS vergessen.
- **Prüfung des Tesla-Stroms (7.2):** Das Fenster im Tessie-Cache reicht von 10 bis 75 s nach der
  Bestätigung, die Frist des Aktors beträgt 85 s. Die Startfolge sendet den Strom, während das Auto
  angesteckt ist, aber noch nicht lädt; Tessie fragt dann nur alle 60 s ab. Annahme: Tessie bestätigt
  binnen etwa 5 s.
- **Selbstprüfung (11):** Die Prüfpunkte `multi_mode` (UI: „Multi-Modus“) und `import_limit` (UI:
  „Importlimit“) warnen nur und sperren nichts.
- **Entscheidungslog (12):** Den Modus tragen die Gründe (`mode_*`, `boost`), denn `inputs` enthält nur
  Zahlen. Neu je Befehl sind `surplus_w`, `limit_a`, `actual_current_a` und `actual_charging`
  (1 = lädt, 0 = nicht); im Dry-Run sind die Ist-Werte die von evcc. Stellt ein Stundenlimit des
  Tesla einen Befehl zurück (`rate.per_hour`, `rate.start_stop`), steht das als Entscheidung mit dem
  neuen Ergebnis `deferred` („zurückgestellt“) im Log, dieselbe höchstens alle 60 s (Fix-Welle nach
  dem Gesamtreview); sonst bräche der Dry-Run-Vergleich beim Tesla stumm ab.
- **Simulator (13):** Er nutzt die echten Aktoren statt gefälschter, die Geräte dahinter sind
  nachgebildet. Die Übergänge, die die Kennzahl der Akku-Energie in PV-Autos ausnimmt, dauern 10 s,
  beim ladenden Tesla 200 s (`2 × commands.current_interval_s + 20`); dieselben 200 s ersetzen die
  100 s aus Präzisierung 16. Mit höchstens 10 kW PV laden zwei PV-Autos nacheinander (6.2). Die
  PV-Kennzahlen zählen nur Autos, deren Ladepunkt die Regelung live steuert, und im Dry-Run zählt eine
  Entscheidung nur als wiederholt, wenn ihr Wert dem vorigen eigenen Wert der Regelung gleicht
  (`UNCHANGED` eingeschlossen). Zur Information ohne Grenze nennt der Bericht den rohen Akku-Anteil
  (mit den Übergängen) und die Entscheidungen „zurückgestellt“ je Fahrzeug mit Tessie. In `reserve`
  prüft eine eigene Prüfung, dass die Regelung unter der Reserve-Absicherung das Importziel hält;
  „SOC nie unter 20 %“ ist nur eine Invariante des Modells.
- **EVCS-Hysterese (6.5, nach dem Gesamtreview):** Die EVCS folgt dem Überschuss nicht mehr jede
  Sekunde. Ihr Strom sinkt sofort mit dem Anteil, steigt aber erst, wenn der Anteil den zuletzt
  angeforderten Strom um 1 A plus `control.evcs_hysteresis_a` (0,5 A) übersteigt (Entscheidung des
  Betreibers vom 14.10.2026). Sonst folgte der Strom dem Rauschen der Hauslast: im Simulator 77 bzw.
  101 Strombefehle in `sonnig` bzw. `wolkig`, mit der Hysterese 3 bzw. 44. `sonnig` prüft das.

Entwurf vom 12.10.2026, im Brainstorming mit dem Betreiber abgestimmt. Grundlage ist die Spec
`docs/superpowers/specs/2026-10-08-bindaems-design.md`, vor allem 1.2, 1.3, 7.1–7.5, 7.8, 8.3, 8.4
und 18. Verweise wie „Spec 7.3“ beziehen sich auf sie. Dieses Dokument präzisiert und ändert sie
für Plan 2c-1; wo es abweicht, gilt es (Abschnitt 14). Die Vorgänger sind die Pläne 2a und 2b
(`docs/superpowers/plans/2026-10-10-phase-2a-sicherheitsbasis.md`,
`docs/superpowers/plans/2026-10-11-phase-2b-aktoren.md`) samt Entscheidungsdateien, alle umgesetzt.

## 1. Ziel und Abgrenzung

Plan 2a hat Phase 2 in vier Pläne geteilt: 2a Sicherheitsbasis, 2b Aktoren, 2c Regelung, 2d
Bedienung. Der Betreiber hat 2c geteilt (Entscheidung 1).

**2c-1 „Laderegelung“** (dieses Dokument): Der core regelt erstmals selbst, und zwar die beiden
Ladepunkte.

- Lademodi je Auto: PV-Überschuss (Standard), Min+PV, Sofort, Aus, dazu Boost.
- Phasenwächter mit Grenzen je Modus.
- Tesla: Weckregeln, 90-s-Regel, erneutes Anfordern nach „nicht übernommen“.
- Zuordnung Auto ↔ Wallbox: Standard, Übersteuerung in der Absicht, Hinweis bei Widerspruch.
- Degradationsstufen für die Ladepunkte, die geparkten Befunde aus 2b, Selbstprüfung des
  Multi-Modus.
- Anlagensimulator `tools/sim` mit Szenarien in der CI.
- Im Dry-Run halten die Entscheidungen den tatsächlich eingestellten Wert fest, also den von evcc.

**2c-2 „Akku und Spitzen“** (eigener Plan, danach): Befehle der Regelung an Victron
(Akku-Unterstützung und Setpoint-Kompensation, Spec 7.6 und 7.7, Auffrischen des Overrides vor
seinem Timeout von 5 min), 15-min-Wächter (Spec 7.4), volle Zuordnungserkennung (Spec 7.8).

**Nicht in 2c:** Bedienung in UI und HA (2d), Optimierer, Plan und Netzladen (Phase 3), Modus
„Geplant“ und Ziel-SOC (Phase 4).

## 2. Entscheidungen des Betreibers (12.10.2026)

1. **Aufteilung:** 2c wird in 2c-1 und 2c-2 geteilt, 2c-1 zuerst.
2. **Grenzen des Phasenwächters je Modus:** PV-Überschuss und Min+PV richten sich am
   Victron-Importlimit aus (20 A je Phase), Sofort und Boost an der Sicherung (35 A − 2 A). In
   Sofort und Boost darf der Akku ins Auto laden: unter 20 A über den Eigenverbrauch von ESS,
   darüber über Peak Shaving. Das ist gewollt.
3. **Tesla wecken:** nur zum Starten, wenn der Modus laden will, gedrosselt (Abschnitt 7).
4. **Standard-Lademodus** ohne Angabe in der Absicht: PV-Überschuss. So lädt die Anlage auch
   heute mit evcc.
5. **Umstieg von evcc:** Heute regelt evcc beide Ladepunkte, nicht den Victron; BindaEMS soll es
   ablösen. Beide Ladepunkte gehen gemeinsam live, evcc wird dann ganz abgeschaltet.
6. **Bauweise:** ein zyklischer, rein rechnender Regler in `core/control` und ein Simulator im
   selben Prozess mit simulierter Zeit.

## 3. Absicht und Konfiguration

### 3.1 Absicht

Die Absicht (`shared/control.py`, `PUT /v1/intent`) bekommt neue Felder. Bis 2d setzt der Betreiber
sie wie heute die Betriebsarten per `PUT /v1/intent`. Version, Regelbetrieb und Betriebsarten
bleiben, wie sie sind.

| Feld | Werte | Standard |
|---|---|---|
| `vehicles.<name>.mode` | `pv`, `min_pv`, `now`, `off` | `pv` |
| `vehicles.<name>.boost_until` | Zeitpunkt mit Zeitzone, höchstens 12 h nach dem Empfang der Absicht | kein Boost |
| `vehicles.<name>.wallbox` | Schlüssel einer Wallbox; übersteuert die Zuordnung | keine Übersteuerung |
| `priority` | Liste der Fahrzeug-Schlüssel, vollständig und ohne Dopplung | Reihenfolge unter `vehicles` in `config.yaml` |
| `surplus_priority` | `cars_first`, `battery_first` oder `battery_until_soc` mit `soc_pct` | `battery_until_soc`, 30 % (Spec 7.5) |

- Unbekannte Fahrzeuge oder Wallboxen lehnt der core ab (Status 422), wie unbekannte Aktoren heute.
- Ein `boost_until` mehr als 12 h in der Zukunft wird auf 12 h geklemmt; das Ergebnis meldet das
  wie andere Klemmungen der Absicht.
- **Boost und Übersteuerung enden beim Abstecken:** Der core merkt sich je Fahrzeug den
  Ansteckvorgang, in dem er sie angenommen hat. Steckt das Auto ab, gelten sie nicht mehr, auch
  wenn die Absicht sie noch nennt. Erst eine neue Absicht mit neuer Version setzt sie wieder.

### 3.2 Konfiguration

Neue Felder in `config.yaml` mit Standardwerten:

| Feld | Standard | Bedeutung |
|---|---|---|
| `grid.import_limit_a` | 20 | Victron-Importlimit je Phase (Peak Shaving, Spec 8.1) |
| `control.import_margin_a` | 1 | Abstand zum Importlimit für PV-Überschuss und Min+PV |
| `control.surplus_margin_w` | 100 | Marge im Überschuss (Spec 7.5) |
| `control.start_after_s` | 120 | so lange muss der Überschuss für einen Start reichen |
| `control.stop_after_s` | 300 | so lange darunter bis zum Stopp |
| `control.min_on_s` | 600 | Mindestlaufzeit nach einem Start |
| `control.min_off_s` | 600 | Mindestpause nach einem Stopp |
| `control.reserve_guard_pct` | 5 | über der USV-Reserve: darunter richten sich auch Sofort und Boost am Importlimit aus |
| `control.tesla_average_s` | 90 | gleitendes Mittel für den Tesla-Strom |
| `control.tesla_caution_s` | 60 | Fenster der vorsichtigen Zuteilung an den Tesla (Spec 7.3) |
| `control.evcs_hysteresis_a` | 0,5 | Hysterese der EVCS beim Erhöhen in A (Abschnitt 6.5; seit dem 14.10.2026) |
| `vehicles.<name>.wake.interval_s` | 1800 | Mindestabstand zweier Weckvorgänge |
| `vehicles.<name>.wake.per_day` | 6 | Weckvorgänge je Kalendertag (Wiener Zeit) |

- `grid.import_limit_a` muss unter `grid.fuse_a − grid.fuse_margin_a` liegen; sonst lehnen core
  und app die Konfiguration ab.
- Die Werte sind Ebene 0 bzw. Parameter der Regelung; die Safety-Schicht prüft weiter gegen
  `grid.fuse_a` und `grid.fuse_margin_a` (Spec 8.3, Regel 4).

## 4. Regelzyklus und Einbindung in die Laufzeit

### 4.1 Bausteine

Neu und rein rechnend in `core/control`, ohne I/O, mit der Zeit als Argument:

- `phase_guard.py`: der Phasenwächter (Abschnitt 5).
- `charging.py`: die Laderegelung samt Überschussrechnung (Abschnitte 6 bis 8).

Den Zustand hält die Laderegelung selbst und ausdrücklich: Start- und Stopp-Timer, Mindestlauf-
und Pausenzeiten, die Fenster für das gleitende Mittel und die vorsichtige Zuteilung, die letzten
eigenen Befehle je Aktor und Art, Ansteckvorgänge, Boost und Übersteuerung sowie die Weckzähler.
Der Zustand lebt nur im Speicher; nach einem Neustart beginnt er leer. Die Weckzähler übersteht ein
Neustart nicht; das ist vertretbar, weil die Safety-Schicht die Befehle je Stunde weiter begrenzt
(ihre Stundenfenster überstehen den Neustart, `rate_limits.json`).

### 4.2 Reihenfolge im Zyklus

Die Schritte aus Spec 7.1 gelten so:

1. Rücklesen, offene Schreibvorgänge abfragen, Betriebszustand (wie in 2b).
2. Sicherer Zustand und Übergabe (`SafeStateKeeper`, wie in 2b).
3. Laderegelung: Phasenwächter, Überschuss, Modi, Verteilung, Befehle.
4. Heartbeat (wie in 2b).

Der sichere Zustand und die Übergabe haben Vorrang: Ein Aktor, der in diesem Zyklus Ziel des
Keepers ist, bekommt keinen Befehl der Regelung. Ein Aktor mit offenem Schreibvorgang einer Art
bekommt für diese Art keinen neuen Befehl; der Ausführer würde ihn ohnehin nicht betrachten.

### 4.3 Wann die Regelung regelt

- Nur in CONTROL und DEGRADED, und nur bei eingeschaltetem Regelbetrieb.
- Nie in START, OBSERVE und SAFE, nie bei ausgeschaltetem Regelbetrieb, auch nicht in DEGRADED
  oder SAFE mit dem Übergabe-Grund (Spec 7.2, Stand Plan 2b).
- Einen gesperrten Aktor lehnt die Safety-Schicht ab (Regel 8); die Regelung muss ihn nicht
  aussparen, die Ablehnung steht höchstens alle 60 s im Entscheidungslog.

### 4.4 Befehle

- Jeder Befehl geht durch `CommandExecutor.execute`, die Safety-Schicht urteilt genau einmal.
- **Dringend** (`urgent=True`) sind nur Senkungen und Stopps wegen Überlast (Abschnitt 5.4); sie
  gehen nachweislich Richtung Sicherheit und umgehen Mindestabstände, Stundenlimits und Totbänder
  (Spec 8.3, Stand Plan 2a). Alle anderen Befehle sind normal.
- **Nur Änderungen:** Die Regelung schickt einen Wert nur, wenn er sich gegenüber ihrem letzten
  eigenen Befehl dieser Art ändert. Denselben Wert schickt sie erneut nur nach einem Ergebnis
  `ERROR` (auch „nicht übernommen“), `TIMEOUT` oder `NOT_SENT`. Im Dry-Run zählt die letzte
  Dry-Run-Entscheidung als letzter Befehl. Ohne diese Regel entstünde im Dry-Run alle 5 s eine
  Entscheidung, weil evcc andere Werte einstellt.
- Ein Befehl, den die Safety-Schicht zurückstellt (`DEFERRED`), etwa wegen der 90-s-Regel, gilt
  nicht als gesendet; die Regelung bietet ihn im nächsten Zyklus wieder an.
- An einen Aktor, der nicht steuerbar ist (`capabilities`), schickt die Regelung nichts, außer dem
  Start mit Weckerlaubnis (Abschnitt 7.1). Nach `NOT_SENT` bietet sie den Wert erst wieder an,
  wenn der Aktor steuerbar ist.
- Ein live gesendeter Befehl macht den Aktor „owned“ (`ownership.note_sent`), wie in 2b die
  Sicherwerte.

### 4.5 Übergabe und Freigabe

- Die Übergabe am Ende des Live-Betriebs bleibt wie in 2b: Sicherwerte durch den Keeper.
- **Die Freigabe 0 des Watchdogs** sendet der core erst, wenn für keinen Victron- oder EVCS-Aktor
  mehr ein Schreibvorgang offen ist (geparkter Befund aus 2b). Sonst könnte ein Wert der Regelung
  nach der Freigabe ankommen und stünde ohne Watchdog.

## 5. Phasenwächter

### 5.1 Größen je Phase

Für jede Phase p, aus den abgeleiteten Größen (`core/state/derived.py`):

- `I_netz_p`: gemessener Netzstrom, positiv bei Bezug (`phase_import_a`).
- `I_akku_p = max(0, P_ac_out_p − P_ac_in_p) / U_p`: was der Multi auf dieser Phase gerade aus dem
  Akku liefert (`vebus.l<n>.ac_out_power_w`, `vebus.l<n>.ac_in_power_w`).
- `I_wb_p`: Strom der eigenen Wallboxen, über `phase_map` auf die Netzphasen gelegt
  (`wallbox_phase_a`).
- `I_last_p = I_netz_p + I_akku_p − I_wb_p`: die Last ohne eigene Wallboxen, so wie sie am Netz
  läge, wenn der Akku nicht mithilft.

Mit `I_last_p` bleibt die Sicherung auch dann geschützt, wenn der Akku plötzlich nicht mehr liefert,
etwa an der Reserve oder wegen einer BMS-Grenze. Ohne `I_akku_p` sähe der Wächter im
Eigenverbrauch fast 0 A am Netz und gäbe zu viel frei.

### 5.2 Ziel je Modus

| Modus | Ziel je Phase |
|---|---|
| PV-Überschuss, Min+PV | `grid.import_limit_a − control.import_margin_a` (20 − 1 = 19 A) |
| Sofort, Boost | `grid.fuse_a − grid.fuse_margin_a` (35 − 2 = 33 A) |
| Sofort, Boost bei SOC unter USV-Reserve + `control.reserve_guard_pct` | wie PV-Überschuss |

In Sofort und Boost lädt das Auto damit auch aus dem Akku (Entscheidung 2). Die Absicherung über
der Reserve verhindert, dass Peak Shaving den Akku unter die USV-Reserve zieht (Spec 1.2,
Randbedingung 1). Ist der SOC unbekannt, gilt sie als erreicht.

### 5.3 Verteilung

- Frei für die Wallbox w ist das Minimum über ihre Phasen von
  `Ziel_p(Modus von w) − I_last_p − Zuteilung an Autos mit höherer Priorität auf p`.
- Die Autos kommen in der Reihenfolge `priority` dran.
- Der EVCS folgt die Grenze jede Sekunde, unter den Mindestabständen der Safety-Schicht.
- Der Tesla bekommt vorsichtig den niedrigsten freien Wert der letzten
  `control.tesla_caution_s` Sekunden (Spec 7.3).
- Liegt die Grenze unter dem Mindeststrom eines Autos, startet es nicht. Lädt es schon, geht es
  auf den Mindeststrom; in Min+PV bleibt es dort (Abschnitt 6.3), in PV-Überschuss stoppt es nach
  `control.stop_after_s`, in Sofort und Boost greifen bei Überlast die Regeln aus Abschnitt 5.4.

### 5.4 Reaktion auf Überlast

In allen Modi, gemessen am echten Netzstrom `I_netz_p` (Spec 7.3):

- Überschreitet die Summe aus Last und Zuteilung das Ziel, senkt die Regelung im nächsten Zyklus
  (dringend, wenn das Ziel die Sicherung ist; sonst normal).
- Liegt eine Phase 3 s lang über `grid.fuse_a − grid.fuse_margin_a`: EVCS und Tesla auf
  Mindeststrom. Hält das weitere 3 s an, stoppt die EVCS.
- Liegt eine Phase 10 s lang über `grid.fuse_a`: Alles Laden stoppt.
- Diese drei Reaktionen sind dringend und gelten, bis die Phase 60 s lang wieder unter dem Ziel
  liegt.

### 5.5 Fehlende Werte

Fehlt für eine Phase `I_netz_p`, `I_akku_p` oder der Strom einer eigenen Wallbox auf ihr, gilt sie
als voll: Ein ladendes Auto bekommt höchstens seinen Mindeststrom, ein Start unterbleibt. Wie es
bei längerem Ausfall weitergeht, sagen die Degradationsstufen (Abschnitt 9).

### 5.6 Letzte Instanz

Regel 4 der Safety-Schicht (Phasenvorhersage gegen `grid.fuse_a − grid.fuse_margin_a`) prüft jeden
Befehl weiterhin unabhängig vom Phasenwächter.

## 6. Lademodi, Überschuss und Verteilung

### 6.1 Überschuss

`P_verf = P_autos − P_netz + P_akku_anrechenbar − control.surplus_margin_w`

- `P_autos`: Ladeleistung aller Autos jetzt.
- `P_netz`: Netzleistung, positiv bei Bezug.
- `P_akku_anrechenbar`:
  - Entlädt der Akku, zählt seine Leistung immer, und zwar negativ. So rechnet der Anteil eines
    PV-Autos nie mit Leistung aus dem Akku, auch nicht, wenn Dynamic ESS gerade ins Netz verkauft.
    Aus dem Akku lädt es trotzdem, wo es dem Überschuss nicht sofort folgt: in den Stopp-Minuten am
    Mindeststrom (6.3), in Übergängen und beim Tesla unter schnellen Wolken (6.5).
  - Lädt der Akku, zählt seine Leistung nur, wenn die Autos Vorrang haben: bei `cars_first`
    immer, bei `battery_first` nie, bei `battery_until_soc` ab diesem SOC.
- Fehlt eine dieser Größen, ist der Überschuss unbekannt: PV-Autos starten nicht, und ein
  ladendes PV-Auto geht auf seinen Mindeststrom, bis die Stopp-Zeit abläuft.

### 6.2 Verteilung

1. Autos in Sofort und Boost bekommen ihren vollen Strom innerhalb des Phasenwächters.
2. Den verbleibenden Überschuss bekommen die Autos in PV-Überschuss und Min+PV in der Reihenfolge
   `priority`, jedes bis zu seinem Höchststrom.
3. Strom = abgerundet Leistung / (`grid.voltage_nominal_v` · Phasen des Autos), begrenzt auf
   Mindest- und Höchststrom.
   - Tesla, 3 Phasen: 1 A ≈ 690 W, Mindeststrom 5 A ≈ 3,5 kW.
   - e-Golf, 2 Phasen: 1 A ≈ 460 W, Mindeststrom 6 A ≈ 2,8 kW.
4. Danach gilt die Grenze des Phasenwächters (Abschnitt 5.3).

### 6.3 Modi

- **PV-Überschuss:**
  - Start, wenn der Anteil des Autos `control.start_after_s` lang seinen Mindeststrom trägt.
  - Stopp, wenn er `control.stop_after_s` lang darunter liegt. Bis dahin lädt das Auto mit
    Mindeststrom weiter; die Lücke deckt der Akku über den Eigenverbrauch von ESS, bis 2c-2 die
    Akku-Unterstützung regelt.
- **Min+PV:** immer mindestens der Mindeststrom, darüber wie PV-Überschuss. Der Mindeststrom geht
  dem Importlimit vor, nicht aber der Sicherung (Abschnitt 5.4).
- **Sofort:** voller Strom innerhalb der Grenze aus Abschnitt 5.
- **Aus:** Laden stoppen und gestoppt halten.
- **Boost:** wie Sofort bis `boost_until` oder zum Abstecken, danach wieder der eingestellte Modus.
- **Moduswechsel** wirkt sofort, ohne Start- und Stopp-Timer und ohne Mindestlauf- und
  Pausenzeit: „Sofort“ startet gleich, „Aus“ stoppt gleich.
- **Kein Ziel-SOC in 2c-1:** Die Autos stoppen an ihrem eigenen Ladelimit, der e-Golf lädt bis
  voll. Meldet ein Auto „Complete“ bzw. lädt eine Wallbox bei angestecktem Auto nicht mehr, startet
  die Regelung nicht erneut, bis es abgesteckt war.

### 6.4 Gegen Flattern

- Nach einem Start lädt ein Auto mindestens `control.min_on_s`, nach einem Stopp startet es
  frühestens nach `control.min_off_s` wieder. Überlast (Abschnitt 5.4) und Moduswechsel heben das
  auf.
- Mit 10 min je Richtung sind das höchstens 3 Start/Stopp-Zyklen je Auto und Stunde: innerhalb der
  Abnahmegrenze von höchstens 4 (Spec 1.3) und im Limit des Tesla von 6 Start/Stopp-Befehlen je
  Stunde (Spec 6.5).

### 6.5 Stromänderungen

- **EVCS:** folgt jede Sekunde; die Safety-Schicht verlangt 1 A Totband und 5 s Abstand.
  **Geändert am 14.10.2026 (Hysterese):** In PV-Überschuss und Min+PV hält die Verteilung den
  zuletzt angeforderten Strom `S`. Liegt der abgerundete Anteil darunter, sinkt der Strom sofort
  auf ihn. Erhöht wird erst ab einem Anteil von `S + 1 A + control.evcs_hysteresis_a`, und dann auf
  den abgerundeten Anteil minus `control.evcs_hysteresis_a`; dazwischen bleibt `S`. Ohne eigenen
  Wert (nach dem Start, einer Pause, einem Wert des Keepers, einer fremden Übernahme oder dem
  Wechsel zwischen Dry-Run und live) wird nur abgerundet. Folgende Autos bekommen den Rest nach dem
  gehaltenen Strom. Ganz ruhig bleibt der Strom, solange das Rauschen des Anteils von Spitze zu
  Spitze unter der Hysterese liegt; im Simulator (Rauschen ±0,33 A beim e-Golf) blieben mit 0,5 A in
  `sonnig` 3 Strombefehle in knapp einer Stunde. Der e-Golf bekommt dafür etwas weniger (dort 1 bis
  1,5 % seiner Ladeenergie); das lädt der Hausakku.
- **Tesla:** folgt dem gleitenden Mittel über `control.tesla_average_s`, ab 2 A Unterschied und
  höchstens alle 90 s (`vehicles.<name>.commands`). Die schnellen Schwankungen fangen die EVCS bzw.
  der Akku ab (Spec 7.5).

## 7. Tesla

### 7.1 Wecken (Entscheidung 3)

- Geweckt wird nur für einen Start, wenn der Modus laden will:
  - Sofort und Boost: nach dem Anstecken, nach einem Moduswechsel oder nach dem Ende eines
    Inselbetriebs.
  - PV-Überschuss und Min+PV: wenn der Überschuss `control.start_after_s` lang reicht bzw. Min+PV
    laden soll.
- Höchstens alle `wake.interval_s` und höchstens `wake.per_day`-mal je Kalendertag; die
  Entscheidung trägt dann den Grund `wake`.
- Der Startbefehl trägt eine ausdrückliche Weckerlaubnis (`Command.wake = True`). Nur damit nimmt
  der Tesla-Aktor einen Start an ein Fahrzeug an, das laut Tessie schläft, und nur, wenn die
  letzten Werte von Tessie mit Qualität OK „zu Hause“ und „angesteckt“ zeigen. `capabilities`
  bleibt unverändert; die Prüfung für den Start mit Weckerlaubnis bekommt der Ausführer über eine
  eigene Methode des Aktors.
- Stromänderungen, Stopps und alle Befehle ohne Weckerlaubnis gehen wie bisher nur an ein wache
  Auto (Spec 6.5).

### 7.2 Strom

- Gleitendes Mittel und 90-s-Regel wie in Abschnitt 6.5.
- **Nach „nicht übernommen“** (`NOT_TAKEN`, Prüfprotokoll Teil 2 vom 12.10.2026) fordert die
  Regelung denselben Wert erneut an, als normalen Befehl. Er wartet auf die 90-s-Regel; eine
  Ausnahme gibt es nicht. Ein Wechsel auf oder von 5 A dauert dadurch etwa 2 min länger.
- `vehicles.tesla.min_a` bleibt 5 A.

### 7.3 Start und Stopp

- Die Mindestlauf- und Pausenzeiten (Abschnitt 6.4) halten das Limit von 6 Start/Stopp-Befehlen je
  Stunde ein.
- Erreicht das Auto sein eigenes Ladelimit („Complete“), startet die Regelung nicht mehr, bis es
  abgesteckt war.

## 8. Zuordnung Auto ↔ Wallbox

Vereinfacht gegenüber Spec 7.8; die volle Erkennung mit Hypothesen und Zeitkorrelation kommt mit
2c-2.

- **Standard:** jedes Fahrzeug an seiner `default_wallbox` (Tesla am Wall Connector, e-Golf an der
  EVCS).
- **Prüfung:** Die Regelung vergleicht Wall Connector („Fahrzeug verbunden“, Ströme), Tessie
  („angesteckt“, „lädt“) und den Status der EVCS. Die Zuordnung ist **unklar**, wenn die Indizien
  60 s lang widersprechen, etwa wenn der Tesla lädt, der Wall Connector aber kein Fahrzeug meldet
  und die EVCS lädt. Dann:
  - Hinweis in Health und im Entscheidungslog (Grund `assignment_unclear`).
  - Tesla-Befehle pausieren.
  - Die EVCS behält ihren Strom; nur der Phasenwächter senkt ihn. Start und Stopp nach Modus
    entfallen.
- **Übersteuerung:** `vehicles.<name>.wallbox` in der Absicht gilt bis zum Abstecken und hebt
  „unklar“ auf.
- **Tesla an der EVCS** (laut Übersteuerung): Der core setzt den Tesla-Strom einmal auf
  `vehicles.tesla.max_a`, sobald das Auto wach ist; danach regelt nur die EVCS (Spec 6.5).

## 9. Ausfälle und Degradation

Für die Ladepunkte (Spec 8.4):

| Ausfall | Reaktion |
|---|---|
| EVCS nicht erreichbar | Der Phasenwächter rechnet sie als Last mit `safe_a` auf allen Phasen ihres `phase_map`; der Tesla wird vorsichtig zugeteilt; an die EVCS geht nichts. |
| Wall Connector nicht erreichbar | Den Ladestrom des Tesla nimmt der Wächter aus Tessie (Ist-Strom und Phasen), sonst gilt der Tesla als Last mit `max_a` auf allen Phasen. |
| Tessie nicht erreichbar | Der Tesla ist nicht steuerbar; sein gemessener Strom zählt als Last, die EVCS gleicht aus. |
| Cerbo-Daten veraltet, Netzausfall | wie in 2b: SAFE, der sichere Zustand senkt bzw. stoppt. Danach nimmt die Regelung das Laden nach Modus wieder auf (Sofort sofort, PV nach `start_after_s`), der Tesla wird nach den Weckregeln geweckt. |
| Ein anderer Regler schreibt (etwa evcc nach der Live-Schaltung) | Die Erkennung fremder Änderungen sperrt den Aktor nach 3 Treffern (Spec 7.9). |

## 10. Geparkte Befunde aus 2b

Plan 2c-1 erledigt:

1. Freigabe 0 des Watchdogs erst ohne offenen Schreibvorgang (Abschnitt 4.5).
2. Das Aufräumen der Entscheidungsdateien läuft auch nach einem gescheiterten Zyklus: im
   `finally`-Zweig von `cycle_once` nach dem Flush.
3. **Heartbeat-Zähler:** Nach einer bestätigten Freigabe vergisst der core seine zuletzt gesendeten
   Zähler. Jeder Lauf beginnt mit einem Startwert, der sich von dem früherer Läufe und von Probe
   und Teil-2-Werkzeug (1, 2, 3, …) unterscheidet. Das muss vor der ersten Live-Schaltung
   erledigt sein.
4. **SAFE mit schlafendem oder abwesendem Tesla:** Der sichere Zustand überspringt einen Tesla, der
   laut Tessie schläft, nicht zu Hause oder nicht angesteckt ist; er lädt dann nicht. Damit
   entfallen die NOT_SENT-Versuche, der Alarm `actuator.failed` und die Einträge im Rate-Limiter.
5. **Auslösung des Watchdogs:** Meldet der Watchdog „ausgelöst“, vergisst der core die Erwartungen
   der Erkennung fremder Änderungen für EVCS und Victron. Was der Watchdog setzt, gilt nicht als
   fremde Änderung.
6. **Laden nach einem Inselbetrieb:** Die Regelung nimmt es nach Modus wieder auf (Abschnitt 9).
7. **EVCS-Aktion des Watchdogs:** bleibt, wie sie ist (schreibt den Sicherstrom, wenn er
   abweicht). An der Anlage gilt `min_a` = `safe_a` = 6 A; ein geregelter Strom liegt also nie
   unter dem Sicherstrom, und ein Update des Watchdogs ist nicht nötig.

Nicht in 2c-1: der Abgleich der Entscheidungen bei Rücksprüngen der Wanduhr (2d) und das spät
gemeldete Ausgangswerte im Teil-2-Werkzeug (bleibt geparkt).

## 11. Selbstprüfung und Health

- **Multi-Modus:** Steht der Multi nicht auf „Ein“ (`vebus.mode` ≠ 3), gibt es eine Warnung: Das
  Importlimit wirkt dann nicht (Vorlauf zum zweiten Lauf von Teil 2 Victron am 12.10.2026: 33,3 A
  auf L3 mit „Nur Laden“, festgehalten in `docs/uebergabe.md`; das Prüfprotokoll enthält nur den
  ersten Lauf). Der Phasenwächter bleibt trotzdem sicher, weil er ohne die Hilfe des Akkus rechnet
  (Abschnitt 5.1).
- **Importlimit:** Der core liest das Importlimit des Cerbo
  (`settings/0/Settings/CGwacs/AcInputLimit`, in Teil 1 am 10. und 11.10.2026 mit 20 A gemeldet),
  vergleicht es mit `grid.import_limit_a` und warnt bei Abweichung oder wenn es fehlt.
- **Health** zeigt je Fahrzeug: Modus (mit Boost bis), Zuordnung (Standard, übersteuert, unklar),
  Grenze des Phasenwächters, angesteckt, lädt, Weckvorgänge heute und den letzten Grund der
  Regelung.

## 12. Entscheidungslog und Dry-Run-Vergleich

- **Gründe** der Regelung (Codes): `mode_pv`, `mode_min_pv`, `mode_now`, `mode_off`, `boost`,
  `pv_surplus`, `phase_limit`, `import_limit`, `reserve_guard`, `overload`, `wake`,
  `not_taken_retry`, `assignment_unclear`, `resume_after_outage`.
- **Eingaben** zusätzlich zu 2b (SOC, Akkuleistung, Netzstrom je Phase): Überschuss, Modus, Grenze
  des Phasenwächters und der tatsächlich eingestellte Wert des Aktors (Sollstrom bzw. Laden an oder
  aus).
- Im Dry-Run ist der eingestellte Wert der von evcc. So vergleicht der Betreiber zwei Wochen lang
  direkt, was BindaEMS täte, mit dem, was evcc tut.

## 13. Simulator und Tests

### 13.1 Simulator `tools/sim`

Im selben Prozess, mit simulierter Zeit (`ManualClock`). Die echte Laufzeit läuft ohne Adapter,
mit Safety-Schicht, Ausführer und Regelung. Ein Anlagenmodell füllt in jedem Zyklus den
Zustandsspeicher; gefälschte Aktoren wirken mit realistischen Verzögerungen auf das Modell.

Das Modell enthält:

- **Netz je Phase** mit Sicherung 35 A; jede Überschreitung wird festgehalten.
- **PV:** sonnig, wolkig mit schnellen Wechseln, Morgenrampe.
- **Hauslast:** Grundlast und Spitzen auf einzelnen Phasen.
- **Akku mit ESS:** Eigenverbrauch bei Sollwert 0, Entladegrenze, USV-Reserve, Peak Shaving bei
  20 A je Phase (Ausgleich über die Phasen wie bei Victron), Multi wahlweise auf „Nur Laden“.
- **EVCS mit e-Golf:** 2 Phasen, folgt dem Sollstrom in 1 bis 2 s.
- **Tesla mit Wall Connector:** 3 Phasen; Antwort von Tessie nach 0,2 bis 2,5 s; Cache-Abfrage
  alle 30 s; ein erster Befehl auf 5 A geht verloren; Schlafen nach 15 min ohne Laden, Wecken bei
  einem Befehl nach etwa 30 s.
- **Ausfälle:** EVCS, Wall Connector, Tessie, Cerbo-Daten, Netzausfall mit Rückkehr.
- **Fremder Regler** wie evcc, der Sollströme verstellt.

### 13.2 Szenarien in der CI

1. Sonniger Tag, beide Autos im PV-Modus.
2. Wolkiger Tag, beide Autos im PV-Modus.
3. Abends beide Autos auf Sofort, dazu eine Lastspitze auf einer Phase.
4. Sofort knapp über der USV-Reserve.
5. Multi auf „Nur Laden“ mit Sofort.
6. Tesla auf 5 A mit verlorenem ersten Befehl.
7. Schlafender Tesla im PV-Modus.
8. Jeder Ausfall einzeln; Netzausfall mit Wiederaufnahme des Ladens.
9. Zuordnung unklar (Tesla an der EVCS) und Übersteuerung.
10. Dry-Run neben einem fremden Regler.

Geprüft wird in jedem Szenario:

- Kein Phasenstrom über 35 A; über 33 A nur so kurz, wie Abschnitt 5.4 es erlaubt.
- In PV-Überschuss und Min+PV liegen Last ohne Akku-Hilfe und Zuteilung (Abschnitt 5.1) höchstens
  beim Ziel, abgesehen von Übergängen bis 10 s, den Stopp-Minuten und dem Mindeststrom von Min+PV.
  Außerhalb dieser Fälle fließt keine Akku-Energie in ein Auto in diesen Modi.
- Höchstens 4 Start/Stopp-Zyklen je Auto und Stunde.
- Tesla-Strombefehle mindestens 90 s auseinander; Weckvorgänge im Kontingent.
- Im Dry-Run neben dem fremden Regler höchstens eine Entscheidung je eigener Änderung.

### 13.3 Weitere Tests

- Unit-Tests der reinen Bausteine mit genauen Werten.
- Property-Tests (hypothesis) für den Phasenwächter: Keine Zuteilung überschreitet je das Ziel,
  egal welche Eingaben.
- Die bestehenden Abdeckungsgrenzen gelten weiter (≥ 75 % insgesamt, ≥ 90 % für `core/safety`,
  `core/control`, `core/actuators`).

### 13.4 Aufruf von Hand

`python -m bindaems.tools.sim --szenario wolkig --out bericht.md` lässt ein Szenario laufen. Der
Bericht nennt die Kennzahlen aus Abschnitt 13.2 und fasst die Entscheidungen zusammen.

## 14. Abnahme, Inbetriebnahme und Abweichungen von der Spec

### 14.1 Abnahme von 2c-1

- Alle Szenarien sind in der CI grün.
- Danach beginnt an der Anlage der Dry-Run neben evcc, während 2c-2 und 2d entstehen. Die
  Entscheidungen liest der Betreiber bis 2d über die Dateien bzw. `GET /v1/decisions`.
- Live gehen beide Ladepunkte gemeinsam erst nach 2d, mit dem Live-Schalter im UI (Spec 8.7) und
  abgeschaltetem evcc. Das Betriebshandbuch beschreibt den Umstieg.

### 14.2 Abweichungen von der Spec

- **Spec 18, Reihenfolge der Live-Schaltung:** EVCS und Tesla gehen gemeinsam live (Entscheidung
  5); Victron folgt nach 2c-2, wenn Dynamic ESS aus ist.
- **Spec 7.3:** Das Ziel des Phasenwächters hängt vom Modus ab (Abschnitt 5.2), und die Last
  rechnet ohne die Hilfe des Akkus (Abschnitt 5.1).
- **Spec 7.5:** Ziel-SOC erst mit „Geplant“ (Phase 4). Neu sind Mindestlauf- und Pausenzeiten
  (Abschnitt 6.4) für das Erfolgskriterium aus Spec 1.3.
- **Spec 6.5:** Neu sind Weckregeln für einen Start (Abschnitt 7.1).
- **Spec 7.8:** in 2c-1 vereinfacht (Abschnitt 8), vollständig in 2c-2.
- **Spec 8.6:** Die EVCS-Aktion des Watchdogs bleibt unverändert (Abschnitt 10, Punkt 7).

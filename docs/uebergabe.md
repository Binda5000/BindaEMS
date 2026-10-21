# Übergabe an die nächste Session

Stand: 21.10.2026. Plan 2c-1 (Laderegelung) ist umgesetzt, je Task und im Gesamtreview geprüft
(„mit Korrekturen“, keine kritischen Befunde), und die Fix-Welle danach ist umgesetzt. Er ist auf
`main` (Stand `18c575a`, wie die Branches `ccr-e451cb4c-qeg1tm` und `ccr-9778345e-t3x44k` zu Beginn
der folgenden Sitzung). Danach sind die EVCS-Hysterese (Freigabe des Betreibers vom 14.10.2026) und
die kleinen Nacharbeiten aus dem Gesamtreview dazugekommen; beides ist per Fast-Forward auf `main`
(`47a09a7`, mit Zustimmung des Betreibers vom 14.10.2026). Das Design für 2c-2 hat der Betreiber am
14.10.2026 freigegeben (`docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md`).
**Plan 2c-2a (Akku und Spitzen) ist umgesetzt, je Task und im Gesamtreview geprüft, und die
Fix-Welle danach ist umgesetzt** (`docs/superpowers/plans/2026-10-14-phase-2c2a-akku-spitzen.md`).
**Plan 2c-2b (Zuordnungserkennung) ist umgesetzt, je Task und im Gesamtreview geprüft, und die
Fix-Welle danach ist umgesetzt** (`docs/superpowers/plans/2026-10-15-phase-2c2b-zuordnung.md`).
Beide Pläne sind per Fast-Forward auf `main` (`31721a6`, mit Zustimmung des Betreibers vom
15.10.2026, Tests auf `main` grün); der Arbeitsbranch `ccr-9778345e-t3x44k` ist danach gelöscht, eine
neue Sitzung beginnt auf `main`. Die drei kleinen Doku-Korrekturen aus 2c-2b (Ende von „Bewusst
offen gelassen“, „Doku-Korrekturen aus dem Re-Review der Fix-Welle von 2c-2b“) sind seit dem
15.10.2026 umgesetzt (Branch `claude/project-thread-57rkit`).
**Plan 2d (Bedienung in UI und HA) ist umgesetzt, im Gesamtreview geprüft, und die Fix-Welle danach
ist umgesetzt** (`docs/superpowers/plans/2026-10-15-phase-2d-bedienung.md`, 18 Tasks). Er ist per
Fast-Forward auf `main` (`0198900`, mit Zustimmung des Betreibers vom 16.10.2026, alle Prüfungen auf
`main` grün: 5762 Python-Tests, 351 Vitest, 14 E2E, Abdeckung 97,5 %).
Der Betreiber hat **Phase 3 am 16.10.2026 in drei Pläne geteilt**: 3a Tarif und Prognosen (nur app),
3b Optimierer und Zeitplan (Pläne als Vorschau, Szenario-Tests), 3c Planausführung im core (danach
2 Wochen Dry-Run, dann geht Victron live); die Vorab-Entscheidungen für 3b und 3c stehen in der
Einordnung des Plans 3a.
**Plan 3a (Tarif und Prognosen) ist umgesetzt, je Task und im Gesamtreview geprüft, und die Fix-Welle
danach ist umgesetzt** (`docs/superpowers/plans/2026-10-16-phase-3a-tarif-prognosen.md`, 14 Tasks). Er
ist auf `main` (bis `e16bff5`).
**Plan 3b (Optimierer und Zeitplan) ist umgesetzt:** alle 17 Tasks je Task geprüft, dann der
Gesamtreview („mit Korrekturen“) und eine Fix-Welle, danach ein sauberes Re-Review
(`docs/superpowers/plans/2026-10-16-phase-3b-optimierer-zeitplan.md`, Branch
`claude/project-thread-syy5yu`; 6274 Python-Tests, 454 Vitest, 21 E2E, Abdeckung 97,74 %).
**Er ist auf `main`** (am 17.10.2026 mit deiner Zustimmung auf die Trail-Commits `9f6a899` und
`d484d95` rebased und per Fast-Forward übernommen, alle Prüfungen grün auf dem Ergebnis). In 3b waren
die Pläne nur eine Vorschau; der core bekam sie nicht.
**Plan 3c (Planausführung im core) ist umgesetzt, je Task und im Gesamtreview geprüft, und die
Fix-Welle danach ist umgesetzt** (`docs/superpowers/plans/2026-10-17-phase-3c-planausfuehrung.md`, 17
Tasks, freigegeben vom Betreiber am 17.10.2026 mit allen 14 Entscheidungen; Branch
`claude/project-thread-syy5yu`, ab `20c338d`) und ist auf `main` (`9621dc7` und dieser Commit, am 17.10.2026
mit Zustimmung des Betreibers per Fast-Forward übernommen, alle Prüfungen grün). Die app übergibt den
Plan in der Absicht, und der core führt ihn im Regelbetrieb aus, in der Betriebsart, die Victron hat:
**Solange Victron im Dry-Run ist, rechnet er ihn nur mit und sendet nichts.** Nichts geht von selbst
live.
An der Anlage ist `fb23bd0` (bis 3c und 4a) seit dem 18.10.2026 ausgerollt, Tarif und Planung
eingerichtet; der Betreiber hat Wallbox und Tesla am 17.10.2026 live geschaltet und evcc abgeschaltet,
Victron ist im Dry-Run (DESS an). Die Zuordnung der Autos (Inbetriebnahme Punkt 3, Design 2c-2, 11.1)
hat der Betreiber am 19.10.2026 im Live-Betrieb geprüft, ohne Befund. Als Nächstes folgen an der
Anlage die **2 Wochen Dry-Run der Planausführung** (DESS dafür aus) und die **30 echten
Tage** durch `export_days` und `scenario_days`; danach geht Victron live (siehe „Offen an der
Anlage“).
**Plan 4a (Statistik, der erste Teil der Phase 4) ist umgesetzt und je Task geprüft**
(`docs/superpowers/plans/2026-10-17-phase-4a-statistik.md`, 16 Tasks, freigegeben vom Betreiber am
17.10.2026 mit allen 13 Entscheidungen und der Änderung, dass PV-Strom ins Auto 0 ct kostet): auf dem
Arbeitszweig `claude/phase-4-plan-whnbh6` und **ist auf `main`** (am 18.10.2026 mit Zustimmung des
Betreibers per Fast-Forward übernommen, alle Prüfungen grün). Gesamtreview, Fix-Welle und die
Nacharbeit vom 18.10.2026 sind abgeschlossen. Der Property-Test des Planers aus Plan 3b, der selten
scheiterte, besteht seit der Festlegung des Betreibers vom 18.10.2026 (siehe „Bewusst offen
gelassen“, „Planer: Plausibilität gegen Optimierer“).
Plan 4a ändert nur die app und das UI (der core bleibt gleich) und braucht an der Anlage nur
den Neubau von `ems-app`: Sessions, Einstandspreis, Kosten je Auto, Abgleich mit den Zählern,
Nachrechnung von Lücken, die Seite „Statistik“, Statistik-Sensoren in HA und ein Grafana-Dashboard.
Er schaltet nichts an der Anlage.
**Plan 4b (Geplantes Laden, der zweite Teil der Phase 4) ist umgesetzt, je Task und im Gesamtreview geprüft, und die Fix-Welle danach ist umgesetzt**
(`docs/superpowers/plans/2026-10-18-phase-4b-geplantes-laden.md`, 19 Tasks, freigegeben vom Betreiber am 18.10.2026 mit allen 17 Entscheidungen: „passt so, du
kannst beginnen, deployment führe ich erst nach der abnahme von phase 3 durch“): auf dem Arbeitszweig
`claude/phase-4b-plan-agf1qj` (ab `fb23bd0`), **seit dem 19.10.2026 auf `main` (`7dabc56`), nicht ausgerollt**. Der Gesamtreview
(19.10.2026) hat eine Fix-Welle mit 12 Punkten und einen kurzen Nachlauf ausgelöst; das Re-Review ist
sauber, es ist kein kritischer und kein wichtiger Befund offen (siehe „Phase 4b“ unten). Plan 4b ändert core, `shared`, app und UI. **Ausgerollt wird
erst nach der Abnahme der Phase 3, und das ist ein Schritt des Betreibers;** nichts geht dabei von
selbst live: „Geplant“ wirkt nur mit Regelbetrieb, in der Betriebsart der Aktoren und erst, wenn
du ein Fahrzeug darauf stellst.
**Plan 6a (Klimaanlage, der erste Teil der Phase 6 „Wärme und Kälte“) ist umgesetzt, je Task und im
Gesamtreview geprüft, und die Fix-Welle danach ist umgesetzt** (`docs/superpowers/plans/2026-10-19-phase-6a-klimaanlage.md`,
13 Tasks und Task 12b, freigegeben vom Betreiber am 19.10.2026: „Passt so bitte umsetzen“): auf dem Branch
`claude/project-thread-79ieqs`, **seit dem 20.10.2026 auf `main` (dieser Commit, per Fast-Forward mit Zustimmung des
Betreibers), nicht ausgerollt**; `latest` enthält damit 6a. Das Re-Review nach der Fix-Welle
sagt „bereit für `main`“, kein kritischer und kein wichtiger Befund ist offen (siehe „Phase 6a“ unten;
Festlegungen und offene Kleinigkeiten in `docs/superpowers/plans/2026-10-19-phase-6a-entscheidungen.md`).
**Ausgerollt wird
mit 4b nach der Abnahme der Phase 3,** und vorher muss der Betreiber Routing und Firewall für UDP 5442
zwischen 192.168.81.0/24 und 192.168.82.0/24 öffnen. Nichts geht dabei von selbst live: Die
Klimaanlage folgt der Betriebsart ihres Aktors (`thermal.klima`), im Dry-Run sendet der core 0.
**Plan 3d (Regel R1 nur, bis der Akku leer ist) ist umgesetzt, je Task und im Gesamtreview geprüft,
und die Fix-Welle danach ist umgesetzt** (`docs/superpowers/plans/2026-10-20-phase-3d-regel-r1.md`, 4 Tasks,
freigegeben vom Betreiber am 20.10.2026 mit den Entscheidungen 1 bis 6): seit dem 20.10.2026 **auf `main`**
(zuvor Branch `claude/project-thread-707vv8`), **nicht ausgerollt**. Er ändert nur die app
(Planung) und das Szenario-Werkzeug; core, `shared`, UI, HA und `config.yaml` bleiben gleich (siehe
„Plan 3d“ unter „Kurzfassung“). **Ausgerollt wird nach Entscheidung 6 des Plans, wann der Betreiber
es beschließt;** Vorschlag: zusammen mit 4b und 6a (Punkt 9 unter „Offen an der Anlage“). Wirken kann
die Regel erst, wenn Victron live ist.

**Plan 5a (Lastmodell, der erste Teil der Phase 5 „Lernen“) ist umgesetzt und je Task geprüft**
(`docs/superpowers/plans/2026-10-20-phase-5a-lastmodell.md`, 14 Tasks, freigegeben vom Betreiber am 20.10.2026 mit allen 17 Entscheidungen: „Ja
passt“): auf dem Branch `claude/phase-5-plan-s7xe0n` (ab `ee6ac9b`), **noch nicht auf `main` (auf dem Stand von `main` mit Plan 3d); die
Integration braucht die Zustimmung des Betreibers**, und nicht ausgerollt. Jeder Task hat ein sauberes
Review, fünf davon nach einer oder zwei Fix-Runden; der Gesamtreview über den Branch ist gemacht, seine
Fix-Welle umgesetzt und nachgeprüft. Plan 5a ändert nur die app, das UI,
`shared/settings.py` und `deploy/Dockerfile.app` (der core und sein Image bleiben gleich) und braucht
an der Anlage nur den Neubau von `ems-app`, **mit oder nach dem gemeinsamen Ausrollen von 4b und 6a**,
also nach der Abnahme der Phase 3. Er schaltet nichts an der Anlage: Das Modell ändert nur die Eingabe
des Planers und gilt nur, solange es in den letzten 14 Tagen mindestens 10 % genauer ist als das
Profil.

**Plan 5b (Fahrverhalten, der zweite Teil der Phase 5) ist umgesetzt und je Task geprüft**
(`docs/superpowers/plans/2026-10-20-phase-5b-fahrverhalten.md`, 14 Tasks, freigegeben vom Betreiber am 20.10.2026 mit allen 17 Entscheidungen:
„Ja so umsetzen“; in Home Assistant gibt es einen `device_tracker` des Tesla): auf dem Branch
`claude/phase-5b-plan-xo03ty` (auf dem Stand von Plan 5a), Gesamtreview, Fix-Welle und Re-Review
erledigt, **seit dem 21.10.2026 per Fast-Forward auf `main` (`d29d4e2`, Zustimmung des Betreibers); `latest`
enthält damit 5b.** Nicht ausgerollt. Anders als 5a ändert 5b auch den **core** (eine Zeile in `core/control/planned.py`: die
Abfahrt an einem Datum, `departure_on`), `shared` und die Datenbank der app (Migration `0014`); core und
app rollen gemeinsam aus, mit ausgeschaltetem Regelbetrieb, **zusammen mit 3d, 4b, 5a und 6a und damit
nach der Abnahme der Phase 3**. Nichts geht von selbst live: Die Automatik ist aus, bis du sie
einschaltest, erwartete Autos ändern nur den Plan, und gelernte Werte erreichen die Safety-Schicht nie.
Als Nächstes folgt 5c (Vorheizen und Vorkühlen, erst mit Daten aus 6a).

`main` ist seit dem 12.10.2026 auch der Standard-Branch auf GitHub.

## Kurzfassung

- **Phase 1 ist umgesetzt:** 1a (core, lesend), 1b (app-Backend), 1c (Web-UI) samt Gesamtreview,
  Fix-Durchgang und allen zurückgestellten Kleinigkeiten.
- **HA-Verbraucher (nach Phase 1):** Die app liest beide InfluxDB-Schemata der HA-Integration
  (Messung je Einheit oder je Entität). Fehlt ein Wert, nennen Liste und Formular den Grund.
- **Phase 2a (Sicherheitsbasis) ist umgesetzt** (Plan: `docs/superpowers/plans/2026-10-10-phase-2a-sicherheitsbasis.md`):
  - Safety-Schicht mit allen 8 Regeln aus Spec 8.3 und Property-Tests (`core/safety`)
  - Betriebszustände START, OBSERVE, CONTROL, DEGRADED und SAFE, Absicht mit Freigaben je Aktor
    (`core/control`), Sperren für Teilsysteme bei mitregelnden Systemen
  - Cerbo-Watchdog (`cerbo-watchdog/`) mit Installationsskript, dazu die Werkzeuge
    `watchdog_config` (erzeugt die `config.ini`) und `watchdog_probe` (Prüfprotokoll Teil 2, Punkt 5)
  - Status in UI (System-Seite) und HA (Sensor „Watchdog ausgelöst“)
  - Abdeckungsgrenzen und ein Importvertrag in der CI
  - An der Anlage installiert; die Watchdog-Probe ist bestanden (11.10.2026).
- **Phase 2b (Aktoren) ist umgesetzt und je Task geprüft** (Plan: `docs/superpowers/plans/2026-10-11-phase-2b-aktoren.md`,
  18 Tasks; Entscheidung des Betreibers vom 11.10.2026: subagent-driven):
  - Aktoren in `core/actuators`: EVCS (schreibt über den GX-Dienst `evcharger` des Cerbo, liest
    weiter per Modbus), Victron (Netz-Sollwert-Override, Entladegrenze und Min-SOC per MQTT) und
    Tesla (Befehle über Tessie im Hintergrund; geweckt wird das Auto seit 2c-1 nur durch einen
    Start mit Weckerlaubnis). Jeder Befehl geht durch den Ausführer (`core/control`), die
    Safety-Schicht und das Rücklesen; im Dry-Run wird er nur protokolliert.
  - sicherer Zustand und Übergabe am Ende des Live-Betriebs: erst die Sicherwerte, dann die Freigabe
    des Watchdogs
  - Heartbeat nur, solange der core regelt, und nur aus einem fehlerfreien Zyklus. Der core schärft
    den Watchdog selbst und wertet ihn nur mit eigenem Zähler und passendem Timeout als scharf. Das
    löst den Blocker „core hängt in OBSERVE“ aus dem Gesamtreview von 2a.
  - Erkennung fremder Änderungen (EVCS-Strom, Victron-Override, Tesla-Strom): Alarm und Sperre
  - Entscheidungslog im core: Spool unter `/data/decisions/`, Stream `decision`,
    `GET /v1/decisions`
  - Cerbo-Watchdog 1.1.0: „ausgelöst“ übersteht einen Neustart des Cerbo, Override-Aktion mit
    `victron.live_allowed`, `fsync` des Verzeichnisses
  - Werkzeug `verify_part2` für das Prüfprotokoll Teil 2 (EVCS, Tesla, Victron)
  - `core/actuators` gehört zum Importvertrag (die Safety-Schicht importiert es nicht) und zur
    Abdeckungsgrenze von 90 %
  - **Stand 2b:** Befehle entstanden nur aus dem sicheren Zustand und der Übergabe; die Regelung
    kam mit 2c-1, die Bedienung kommt mit 2d. Die Punkte aus „Vor einer Live-Schaltung in 2b zu
    klären“ (Gesamtreview 2a) sind erledigt.
  - **Gesamtreview über den ganzen Branch:** „bereit mit Korrekturen“, keine kritischen Befunde.
    Der Fix-Durchgang hat die vier wichtigen Befunde behoben, jeder mit einem Test, der vorher
    scheiterte:
    - Eine Sperre, die nur besteht, weil ein Prüfpunkt nicht lesbar ist (etwa im ersten Zyklus
      nach dem Start), verwirft die Werte des EMS nicht mehr; sonst ging die Übergabe des letzten
      Laufs verloren.
    - Ein Strom unter dem Sicherwert gilt als erreicht; der sichere Zustand erhöht nie einen
      Strom.
    - Wirksam live ist ein Aktor nur im Regelbetrieb, außer während seiner Übergabe.
    - Ein wirkungsloser Sicherheitstest prüft jetzt etwas.

    Dazu kamen Punkte im Werkzeug für Teil 2 (Schlaf-Test nur mit frischer Beobachtung, Victron:
    DESS vor jedem Schreiben, vorgefundene Entladegrenze von 0 W). Das Re-Review hat alles
    bestätigt. Entscheidungen, geparkte Befunde und aufgeschobene Kleinigkeiten:
    `docs/superpowers/plans/2026-10-11-phase-2b-entscheidungen.md`.
- **Phase 2c-1 (Laderegelung) ist umgesetzt, je Task und im Gesamtreview geprüft** (Plan:
  `docs/superpowers/plans/2026-10-12-phase-2c1-laderegelung.md`, Design:
  `docs/superpowers/specs/2026-10-12-phase-2c1-laderegelung-design.md`, 16 Tasks; der Betreiber hat 2c
  am 12.10.2026 in 2c-1 und 2c-2 geteilt, Ausführung subagent-driven):
  - Der core regelt die beiden Ladepunkte (EVCS und Tesla) selbst. Die Bausteine liegen rein in
    `core/control` (Aufbau unten):
    - Lademodi je Auto: PV-Überschuss (Standard), Min+PV, Sofort, Aus und Boost. Die Absicht bekommt
      `vehicles`, `priority` und `surplus_priority`; Ungültiges lehnt der core mit `invalid` ab.
    - Phasenwächter mit Zielen je Modus, Last ohne Akku-Hilfe und Überlast-Stufen
    - Tesla: gleitendes Mittel und 90-s-Regel, Weckstart mit Erlaubnis und Kontingent, erneutes
      Anfordern nach „nicht übernommen“ (nie dringend)
    - vereinfachte Zuordnung Auto ↔ Wallbox mit Übersteuerung und „unklar“ (die volle Erkennung
      folgt mit 2c-2), Degradation der Ladepunkte und Wiederaufnahme nach einem Inselbetrieb
  - Die geparkten Befunde aus 2b sind erledigt: Freigabe 0 erst ohne offenen Schreibvorgang,
    Aufräumen des Entscheidungslogs auch nach einem gescheiterten Zyklus, Heartbeat-Startwert je
    Lauf mit bestätigter Freigabe, SAFE ohne einen Tesla, der nicht lädt, und eine Auslösung des
    Watchdogs lässt die Erwartungen der Erkennung fremder Änderungen vergessen.
  - Selbstprüfung `multi_mode` und `import_limit`; Health mit `vehicles`; die Entscheidungen tragen
    Überschuss, Grenze des Wächters und Ist-Werte (im Dry-Run die von evcc).
  - Der Anlagensimulator `bindaems.tools.sim` lässt die echte Laufzeit mit echten Aktoren in
    simulierter Zeit gegen ein Modell der Anlage laufen. Zehn Szenarien sind in der CI grün
    (Betriebshandbuch, „Simulator“).
  - **Beim Bau geändert:** Das Prüffenster des Tesla-Stroms im Tessie-Cache reicht jetzt von 10 bis
    75 s nach der Bestätigung (Frist 85 s, vorher 10 bis 50 s und 75 s). Der Simulator zeigte, dass
    das alte Fenster die 60-s-Abfrage eines angesteckten, noch nicht ladenden Autos nur mit etwa zwei
    Dritteln Wahrscheinlichkeit fing, ein verlorener 5-A-Befehl blieb unbemerkt. Die übrigen
    Änderungen gegenüber Plan und Design stehen im Kopf des Designs und in den Absätzen „Stand
    Plan 2c-1“ der Spec.
  - **Fix-Welle nach dem Gesamtreview:** Stellt ein Stundenlimit des Tesla einen Befehl zurück,
    steht das jetzt als „zurückgestellt“ im Entscheidungslog (höchstens einmal je Minute); vorher
    brach der Dry-Run-Vergleich dort stumm ab. Der Simulator nennt den rohen Akku-Anteil und die
    Zurückstellungen als Informationszahlen und prüft in `reserve` die Reserve-Absicherung der
    Regelung; „SOC nie unter 20 %“ ist als Invariante des Modells gekennzeichnet. Dazu ein Test zur
    Dringlichkeit beim Steigen der Überlast-Stufe, Korrekturen der Doku und die Liste „Vor der
    Live-Schaltung von EVCS und Tesla“ (siehe „Offen an der Anlage“).
  - **EVCS-Hysterese (danach, Design 6.5):** Der EVCS-Strom sinkt sofort mit dem Anteil am
    Überschuss, steigt aber erst, wenn der Anteil den zuletzt angeforderten Strom um 1 A plus
    `control.evcs_hysteresis_a` (0,5 A) übersteigt, und dann auf den abgerundeten Anteil minus die
    Hysterese. Im Simulator sinken die EVCS-Strombefehle in `sonnig` von 77 auf 3 (knapp eine Stunde
    PV-Laden) und in `wolkig` von 101 auf 44 (dort folgt der e-Golf den Wolken); der e-Golf bekommt
    dafür 1 bis 1,5 % weniger Energie, die der Hausakku lädt. `sonnig` prüft höchstens 10 Befehle je
    Stunde. Gegenüber dem Vorschlag aus dem Gesamtreview (0,3 A oder nach 30 s) hat der Betreiber
    nach einer Messung 0,5 A ohne den 30-s-Pfad freigegeben: 0,3 A liegt unter dem Rauschen des
    Simulators (±0,33 A beim e-Golf), und der 30-s-Pfad brachte mehr Flattern statt weniger.
  - **Noch nicht (Stand nach 2c-1):** Befehle an Victron und der 15-min-Wächter (kamen mit 2c-2a),
    die Bedienung (2d). Live ist weiterhin kein Aktor.
- **Phase 2c-2a (Akku und Spitzen) ist umgesetzt, je Task und im Gesamtreview geprüft** (Plan:
  `docs/superpowers/plans/2026-10-14-phase-2c2a-akku-spitzen.md`, Design:
  `docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md`, 14 Tasks; der Betreiber hat 2c-2
  im Design vom 14.10.2026 (Entscheidung 7) in 2c-2a und 2c-2b geteilt, Ausführung subagent-driven):
  - **Umbau vorab, ohne Verhaltensänderung:** Die Laderegelung ist aufgeteilt (`charge_points.py`,
    `charging_tesla.py`, `last_values.py`, `timing.py`), der Regelungs-Kern `Regulation`
    (`core/control/regulation.py`) ist die einzige Schnittstelle der Laufzeit, und seine Verdrahtung
    liegt in `core/regulation_link.py`. Eine Ausnahme in der Regelung setzt nur sie aus: Keeper,
    Stream, Alarme und Entscheidungslog laufen weiter, der Heartbeat bleibt aus, und der Alarm
    `control.exception` steht, bis die Regelung wieder ohne Ausnahme gelaufen ist, auch nach dem
    Abschalten des Regelbetriebs. Das erledigt Punkt 4 der Liste „Vor der Live-Schaltung“.
  - **Akku-Sollwert** (`core/control/battery.py`): Der core setzt den Override des Netz-Sollwerts auf
    den **Fehlbetrag** der Autos (was ihnen nach PV und Haus fehlt, abzüglich des erlaubten
    Akku-Anteils), damit der Akku nicht ins Auto lädt. Aus dem Akku laden dürfen nur Autos in der
    Unterstützung (PV-Überschuss und Min+PV ab SOC 70 %, Hysterese 5 %, höchstens 3 kW und 15 min am
    Stück) und im Boost (ohne diese Grenzen). Dazu gehören das Totband von 200 W, das Auffrischen alle
    240 s (der Cerbo verwirft den Override nach 300 s), die Sperren (Victron gesperrt oder nicht
    steuerbar: kein Befehl, auch im Dry-Run nicht) und Health `battery`.
  - **15-min-Wächter** (`core/control/peak_guard.py`): Er misst die Viertelstunde aus der Abrechnung,
    rechnet hoch, drosselt die Wallboxen (erst PV-Überschuss, dann Min+PV, zuletzt Sofort), lässt den
    Akku überbrücken (Budget `P_zul` für den Override, nur über USV-Reserve + `control.reserve_guard_pct`)
    und warnt sonst (`peak.exceeded`). Das Ziel `control.peak_target_kw` ist optional, ohne Wert ruht er
    (seit Plan 3c nur, solange auch kein Plan ein Budget bringt).
    Health `peak`.
  - **Safety-Schicht, Absicht und Log:** Ein Auffrischen (`Command.refresh`) lässt das Totband von
    Regel 5 aus, ist nie dringend und steht bei Erfolg nicht im Entscheidungslog. Neue Gründe
    `battery_comp`, `battery_support` und `peak_guard`; neue Absicht `guest.mode` (Modus eines Gastes,
    bis 2c-2b nur für den Akku-Sollwert).
  - **Simulator:** Das Modell kennt den Override (ESS folgt ihm, er verfällt nach 300 s, DESS verwirft
    ihn). Acht Szenarien kamen dazu; alle 18 sind in der CI grün (Betriebshandbuch, „Simulator“). Die
    Regelung brauchte keinen Fix. Zwei Szenarien haben einen angepassten Aufbau (unten,
    „Bewusst offen gelassen“).
  - **Gesamtreview, Fix-Welle und Nachrunde:** Der Gesamtreview fand zwei wichtige Lücken im
    15-min-Wächter (gewollter Netzbezug über der festen Grenze von Regel 2, Boost-Anteil aus der
    Verteilung der Abrechnung) und zwei Kleinigkeiten; die Fix-Welle hat sie behoben, eine
    Nachrunde danach zwei Restpunkte der Drosselung (alles unter „Bewusst offen gelassen“).
  - **Entschieden und aufgeschoben:** Die Festlegungen und die aufgeschobenen Kleinigkeiten stehen
    unter „Bewusst offen gelassen“.
  - **Noch nicht (Stand nach 2c-2a):** die volle Zuordnungserkennung (kam mit 2c-2b), die Bedienung
    (2d) und die Planausführung (Phase 3). Live ist weiterhin kein Aktor.
- **Phase 2c-2b (Zuordnungserkennung) ist umgesetzt, je Task und im Gesamtreview geprüft** (Plan:
  `docs/superpowers/plans/2026-10-15-phase-2c2b-zuordnung.md`, Design:
  `docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md`, Abschnitt 7, 9 Tasks;
  Ausführung subagent-driven):
  - **Erkennung** (rein, in `core/control`): `clues.py` wertet je Wallbox mit Ansteckvorgang und je
    Fahrzeug sechs Indizien aus (Tessie, Stromvergleich, zeitliche Korrelation, Phasen-Signatur, Home
    Assistant, Ausschluss), `detection.py` entscheidet daraus (Kandidat, erkannt, Gast, Widerspruch,
    je 60 s ohne Unterbrechung), `fleet.py` führt die Autos der Laderegelung (die Fahrzeuge und je
    Wallbox ein Gast mit dem Schlüssel `guest@<Wallbox>`), und `assignment.py` sagt je Zyklus, wer wo
    steht: Übersteuerung, erkannt, angenommen (auch „Gast, angenommen“) oder, nach einem Widerspruch
    von 60 s, unklar.
  - **Regelung:** Ein Fahrzeug mit Tessie, das an einer EVCS erkannt ist, regelt die Laderegelung wie
    mit Übersteuerung (einmal `max_a`, dann regelt die EVCS in seinem Modus). Der e-Golf am Wall
    Connector ist Last. Ein Gast an der EVCS wird im Modus `guest.mode` geregelt, am Wall Connector
    ist er Last. Neuer Grund `assignment_detected` („Zuordnung erkannt“).
  - **Eingaben und Health:** `vehicles.<name>.home_entity` (ein `device_tracker` in Home Assistant, den
    der core selbst abonniert), die Zeitstempel der Tessie-Werte (`VehicleObs.plugged_at`) und der
    Beginn jedes Ansteckvorgangs. `GET /v1/health` hat `wallboxes` (je Wallbox `id`, `occupant`,
    `name`, `assignment` und `indicators`); `vehicles[].assignment` kennt `detected`.
  - **Simulator:** Home Assistant (der e-Golf zu Hause oder weg), ein Gast ohne Tessie mit 1 bis 3
    Phasen, ein Tesla, der woanders angesteckt ist, und jedes Auto an jeder Wallbox. Drei Szenarien
    kamen dazu (`tesla-evcs`, `egolf-twc`, `gast-evcs`), `zuordnung` prüft einen echten Widerspruch;
    alle 21 laufen grün in `tests/integration/test_sim_scenarios.py` (Betriebshandbuch,
    „Simulator“). Keines scheiterte an Erkennung oder Regelung.
  - **Beim Bau geändert:** Tessie-Werte zählen an einer Wallbox erst eine Minute nach dem Beginn des
    Vorgangs, der Widerspruch ist enger gefasst als im Design (7.3), der Ausschluss verlangt „zu
    Hause und angesteckt“, Stromvergleich und zeitliche Korrelation sagen über ein Auto, das Tessie
    nicht zu Hause sieht, nichts, Tessie und Home Assistant stützen bei mehreren Vorgängen nur nach
    einer Elimination (Strom oder Phasen schließen das Fahrzeug an jeder anderen Wallbox aus), und
    ein Boost ohne Übersteuerung bindet an den Vorgang, an dem das Fahrzeug steht (Kopf des Designs
    2c-2; Absatz „Stand Plan 2c-2b“ der Spec, 7.8). Die Festlegungen und die offenen Punkte stehen
    unter „Bewusst offen gelassen“.
  - **Gesamtreview und Fix-Welle:** Der Gesamtreview („mit Korrekturen“, keine kritischen Befunde)
    fand zwei wichtige Lücken: Ein Tesla, der den Wall Connector verließ, galt mit Tessies
    veraltetem „angesteckt“ an der EVCS als erkannt und bekam `max_a`, und der Stromvergleich konnte
    den Tesla an seinem eigenen Wall Connector ausschließen (dann „Gast“, danach „unklar“). Seit der
    Fix-Welle stützen Tessie und Ausschluss nach dem Ende eines Vorgangs erst mit einem Wert, der
    mindestens 60 s danach abgefragt wurde; der Stromvergleich wertet eine Phasenzahl von Tessie,
    die nicht zur Konfiguration passt, als unbekannt, und „passt nicht“ zählt erst mit einem Wert,
    der mindestens 60 s nach der letzten Stromänderung der Wallbox abgefragt wurde; nach einer Pause
    der Regelung von mehr als 60 s beginnen die 60 s der Erkennung neu; die Konfiguration lehnt
    Fahrzeug-Schlüssel `guest` und `guest@…` ab. Dazu kamen drei Prüfungen im Dry-Run, die vor der
    Live-Schaltung von EVCS und Tesla bestanden sein müssen (Betriebshandbuch, „Hinweise für die
    Anlage“).
  - **Noch nicht:** die Bedienung in UI und HA, auch die Auswahl des Gast-Modus (2d), und die
    Planausführung (Phase 3). Live ist weiterhin kein Aktor.
- **Phase 2d (Bedienung in UI und HA) ist umgesetzt und im Gesamtreview geprüft** (Plan: `docs/superpowers/plans/2026-10-15-phase-2d-bedienung.md`, 18 Tasks;
  Ausführung subagent-driven; auf `main` seit `0198900`, 16.10.2026):
  - **app, neues Paket `bindaems.app.control`:**
    - `intent.py`: der `IntentService` leitet jede Bedienung aus der gültigen Absicht ab und sendet sie
      an den core. Die Version ist das Größere aus Millisekunden seit 1970 und gültiger Version + 1; bei
      409 wendet er die Änderung einmal neu an. Boost und Übersteuerung schickt er nur zurück, wo
      Health sie zeigt.
    - `decisions.py`: der `DecisionStore` (SQLite, Migration `0007`) und der `DecisionCollector` (Stream,
      Rückstand, Aufräumen nach 180 Tagen, Markierungen für Grafana).
    - `routes.py`: `/api/control/…` und `/api/decisions…`. „Bedienen“ ändert Regelbetrieb, Lademodi,
      Boost, Zuordnung, Gast-Modus und Vorrang; nur „Admin“ schaltet live oder auf Dry-Run und quittiert
      Sperren.
  - **core:** eine fremde Änderung wird eine Entscheidung (Ergebnis `foreign`), Quittierung von Sperren
    (`POST /v1/locks/{actuator}/ack`), und Health nennt `intent_version`, `boost_pending`,
    `override_pending`, `watchdog.armed`, `actuators[].watchdog_needed` und `vehicles[].commands_hour`.
  - **Tesla vor der Live-Schaltung:** „Aus“ stoppt sofort (dringend), 4 Befehle je Stunde bleiben frei
    (`control.tesla_reserve_commands`), ein Selbststart wird nach 120 s erneut gestoppt
    (`control.tesla_against_s`, `control.tesla_against_retry_s`, Grund `self_start`), und Tessie fragt
    ein abgestecktes Auto zu Hause alle 60 s ab und sofort bei einem neuen Vorgang. Der Simulator hat
    dafür `tesla-aus` und `tesla-selbststart`: jetzt 23 Szenarien, alle grün.
  - **UI:** die Seiten „Laden“ und „Entscheidungen“, „Warum?“ auf der Übersicht, auf der System-Seite die
    Karte „Bedienung“ (Regelbetrieb, Live-Dialog mit Passwort und Bestätigungen, „Auf Dry-Run“) und die
    Taste „Quittieren“. Die Rolle „Bedienen“ hat damit erstmals eigene Rechte.
  - **HA:** Auswahl „Lademodus“, Tasten „Boost“ und „Boost beenden“, Sensor „Wallbox“ je Fahrzeug;
    Auswahl „Zuordnung“ und Sensor „Fahrzeug“ je Wallbox; Schalter „Regelbetrieb“, Auswahl
    „Gast-Lademodus“ und „Zuordnung unklar“. Befehle laufen über `bindaems/cmd/…`. Benachrichtigungen
    bei Fehler-Alarmen und abgelehnten Befehlen.
  - **Doku und Tests:** Betriebshandbuch (7, 12, 13, „Laderegelung“), Spec („Stand Plan 2d“ in 5.3, 7.5,
    7.9, 7.10, 8.7, 11.3, 12, 13, 16 und 18) und `tests/unit/test_docs.py`. Das Demo-Backend hat den
    Benutzer `bedienen`; sein core meldet DEGRADED (die Demo-Welt).
  - **Gesamtreview und Fix-Welle:** Die Fix-Welle hat behoben: die erneute Prüfung der
    Live-Schaltung je Versuch (auch bei leerer Auswahl), die Quittierung neben einer im selben Zyklus
    neu erkannten fremden Änderung, den verbrauchten Boost im core (er bindet mit keiner neuen Version
    wieder), Lademodus `null` in Health als unbekannter Stand, die HA-Auswahl „Zuordnung“ wie im UI,
    den Tag `kind` der Grafana-Markierungen und das Meldebudget mit eigenem Anteil für Ablehnungen.
  - **Entschieden und aufgeschoben:** Die Festlegungen und die aufgeschobenen Kleinigkeiten stehen
    unter „Bewusst offen gelassen“.
  - **Noch nicht:** Ziel-SOC, Abfahrt und der Modus „Geplant“ (Phase 4), die Planausführung (Phase 3),
    die Mehrkostenanzeige beim Boost, die Schalter „Netzladen erlaubt“ und „Akku-Unterstützung erlaubt“,
    die Zahl „Unterstützungsschwelle“, die Taste „neu planen“ und die HA-Sensoren Ladeenergie,
    Fertig-um und Ziel erreichbar. Live ist weiterhin kein Aktor.
- **Phase 3 ist in drei Pläne geteilt** (Entscheidung des Betreibers vom 16.10.2026; Einordnung und
  Vorab-Entscheidungen im Plan 3a): **3a** Tarif und Prognosen (nur app, `core` unverändert), **3b**
  Optimierer und Zeitplan (Pläne als Vorschau, der core bekommt sie noch nicht; Szenario-Tests über
  historische Tage), **3c** Planausführung im core (danach 2 Wochen Dry-Run, dann geht Victron live).
  Für 3b und 3c gilt vorab: „Victron allein“ ist der Eigenverbrauch des ESS ohne Netzladen und ohne
  Halten; die Szenario-Tests rechnen im Viertelstundenraster mit einem Akkumodell (die CI mit erzeugten
  Tagen, die 30 echten Tage laufen an der Anlage); Regel R2 kommt erst mit Phase 4; `HALTEN` setzt der
  core über den Netz-Sollwert-Override um (Sollwert = Last − PV, nicht unter 0).
- **Phase 3a (Tarif und Prognosen) ist umgesetzt, je Task und im Gesamtreview geprüft** (Plan:
  `docs/superpowers/plans/2026-10-16-phase-3a-tarif-prognosen.md`, 14 Tasks; Ausführung
  subagent-driven; auf `main`, bis `e16bff5`):
  - **Tarif** (`shared/settings.py`, `app/tariff/calc.py`): vier Arten von Bestandteilen. Neu sind
    der **Leistungspreis** (€/kW/Jahr, Stufe nach ElWG bei 10 kW, Mindestverrechnung 2 kW, gültig ab
    1.1.2027), Monatsgebühren und Einspeisebestandteile mit Zeitfenstern. Die **Monatsspitze** rechnet
    die app aus dem Ledger; in HA gibt es den Sensor „Monatsspitze“. Leistungspreis und Gebühren
    fließen erst ab 3b und 3c in etwas ein. Der Tarif-Editor im UI (Einstellungen) ändert auch
    Zeitfenster und Gültigkeit.
  - **Tagestypen und Schätzpreise:** Werktag, Samstag, Sonn- und Feiertag (österreichische Feiertage);
    der geschätzte Spotpreis ist der Median der letzten 28 Tage je Viertelstunde und Tagestyp, daraus
    entstehen die Planungspreise (`PriceService.planning`, Herkunft `primary`, `fallback` oder
    `estimated`).
  - **Prognosen** (`app/forecast/`): ein Archiv (Migration `0008`, Tabellen `forecast_record` und
    `forecast_metric`), die PV-Kalibrierung je Stunde und Klarheit mit dem Band P25/P75, die
    Lastprognose (gewichteter Median über 56 Tage, Halbwertszeit 14 Tage) aus dem Ledger und optional
    der HA-Historie (`homeassistant.load_history`, braucht `influxdb.ha_database`), die Prognosegüte
    (MAE, Bias, relativer MAE) und ein Nachtlauf um 02:30.
  - **API und UI:** `/api/forecast/pv`, `/api/forecast/load` und `/api/forecast/quality`; im UI der
    Tarif-Editor, das PV-Band im Diagramm der Übersicht und die Karten „Lastprognose“ und
    „Prognosegüte“ auf der System-Seite. Betrieb: `docs/betrieb.md`, „Prognosen“; Spec: die Absätze
    „Stand Plan 3a“.
  - **Ledger-Korrektur:** Der Tageserlös der Einspeisung rechnet je Viertelstunde mit deren
    Einspeisepreis (Fix-Welle).
  - **Gesamtreview und Fix-Welle:** Der Gesamtreview („mit Korrekturen“) fand keinen kritischen und
    einen wichtigen Befund (der Tageserlös ignorierte die Zeitfenster der Einspeisung); die Fix-Welle hat
    ihn behoben, dazu drei kleine Punkte im UI („Heute (ab jetzt)“ in der Lastprognose, Stufe und
    Mindestverrechnung in der Tarifansicht, die ElWG-Vorlage mit Beginn 1.1.2027). Das Re-Review war
    sauber.
  - **Entschieden und aufgeschoben:** Die Festlegungen und die aufgeschobenen Kleinigkeiten stehen
    unter „Bewusst offen gelassen“ („Entschieden in Plan 3a“).
  - **Noch nicht (Stand nach 3a):** der Optimierer, der Zeitplan und die Szenario-Tests (kamen mit 3b),
    die Planausführung im core (3c), die Kostenanzeige zu Leistungspreis und Gebühren und eine eigene
    Seite zur Prognosegüte (Phase 5). Live ist weiterhin kein Aktor, und 3a schaltet nichts an der
    Anlage.
- **Phase 3b (Optimierer und Zeitplan) ist umgesetzt, je Task und im Gesamtreview geprüft** (Plan:
  `docs/superpowers/plans/2026-10-16-phase-3b-optimierer-zeitplan.md`, 17 Tasks; Ausführung
  subagent-driven; Branch `claude/project-thread-syy5yu`; seit 17.10.2026 auf `main`, rebased auf die
  Trail-Commits `9f6a899` und `d484d95`):
  - **Plan-Typen** (`shared/plan.py`): Akku-Absichten `EIGENVERBRAUCH`, `HALTEN` und `NETZLADEN(P)`,
    Plan mit Kopf und Slots (Quelle `optimizer` oder `fallback`, Gültigkeit, Grundcodes). Der core liest
    sie erst mit 3c.
  - **Optimierer** (`app/planning/optimizer.py`): ein MILP mit PuLP und HiGHS nur für den Akku, mit
    Regel R1, Marge, Verschleiß, Leistungspreis und Planträgheit. Jeder Lauf rechnet in einem eigenen
    Kindprozess (`spawn`, `process.py`) mit 30 s Limit (HiGHS selbst 20 s).
  - **Absichten und Prüfung:** `intents.py` leitet je Slot eine Absicht aus den Flüssen ab,
    `battery_model.py` rechnet sie im Viertelstundenraster nach (SOC, Flüsse, Kosten, Spitze, Budget),
    `plausibility.py` prüft das Ergebnis. **Ersatzplan** (`fallback.py`, regelbasiert) bei einem Fehler,
    einer Zeitüberschreitung ohne Lösung oder einem unplausiblen Plan. **Erklärungen** (`explain.py`):
    ein Grundcode mit Parametern je Slot, der Satz entsteht beim Lesen.
  - **Dienst und Auslöser** (`service.py`, `triggers.py`): ein Lauf jede Viertelstunde, eine Minute nach
    Slotbeginn, dazu neue Preise, neue Prognosen, SOC mehr als 5 Prozentpunkte vom Plan weg, neue
    Monatsspitze, geänderte Einstellungen und „Neu planen“; ein Lauf zur Zeit, Gültigkeit 45 min.
  - **Speicher** (`store.py`, Migration `0009`: `plan` und `plan_inputs`): Pläne 30 Tage, Eingangsdaten
    7 Tage; danach werden die Slots eines Plans auf die gekürzt, die er tatsächlich bestimmt hat.
  - **API, HA, UI:** `GET /api/plan`, `POST /api/plan/replan`, `PUT /api/plan/grid-charge`; in HA die
    Sensoren „Akku-Plan jetzt“, „Akku-Plan nächster Slot“, „Geplanter SOC“ und „Budget“, der Schalter
    „Netzladen erlaubt“ und die Taste „Neu planen“; im UI die Seite „Zeitplan“ (Preise und Prognosen,
    geplanter SOC, Ablauf in Blöcken mit Satz) und die Einstellungen „Planung“ (Marge, Roundtrip,
    Verschleiß, Restwertfaktor, Trägheit, Schalter Netzladen).
  - **Werkzeuge:** `export_days` (echte Tage aus der Datenbank der app in eine Datei) und
    `scenario_days` (Vergleich mit „Victron allein“, Rückgabe 0 = bestanden). In der CI laufen vier
    erzeugte Tagesarten (`tests/integration/test_planning_scenarios.py`). Betriebshandbuch, „Planung“.
  - **Nur Vorschau:** Der Plan wird berechnet, gespeichert und angezeigt, aber nichts stellt den Akku
    danach, und der core bekommt ihn nicht. Das kommt mit 3c. Die Seite „Zeitplan“ zeigt immer die
    Zeile „Vorschau – der core führt den Plan noch nicht aus.“, die vier HA-Sensoren tragen das Attribut
    `preview: true`. „Netzladen erlaubt“ (Standard aus) ändert deshalb noch nichts an der Anlage.
  - **Szenarien** (erzeugte Tage, je 2 Tage, stündlich neu geplant; Differenz = Optimierer minus
    „Victron allein“, negativ heißt günstiger):

    | Tag | Victron allein (€) | Optimierer (€) | Differenz bereinigt (€) | Netzladen (kWh) | Halten (Slots) | Regel R | Ersatzpläne / Pläne |
    |---|---|---|---|---|---|---|---|
    | sommer-sonnig | −6,39 | −6,39 | 0,00 | 0,0 | 0 | 0 | 0 / 48 |
    | winter-spread | 4,97 | 3,90 | −1,07 | 1,2 | 87 | 0 | 0 / 48 |
    | winter-leistung | 11,33 | 10,26 | −1,07 | 1,3 | 85 | 0 | 0 / 48 |
    | wechselhaft | 1,84 | 1,60 | −0,25 | 0,3 | 54 | 0 | 0 / 48 |

    Bereinigt heißt um den End-SOC; bei allen vier Tagen sind die bereinigten Kosten gleich den
    rohen. Im Sommer füllt die PV den Akku, dort ist der Optimierer gleich „Victron allein“.
  - **Gesamtreview und Fix-Welle:** Der Gesamtreview („mit Korrekturen“) fand zwei wichtige Befunde.
    Erstens verwarf die Plausibilität Netzladen für den Restwert am Horizontende zu Unrecht (die
    Spread-Regel kannte das Horizontende nicht und rechnete den Verschleiß ohne den Wirkungsgrad).
    Zweitens verschleppten die Werkzeuge ihre Logging-Konfiguration in andere Tests (reihenfolge-
    abhängiger Fehler). Die Fix-Welle (12 Commits, `a30d832` bis `b852fcc`) hat beide behoben, jeden
    mit einem Test, der vorher scheiterte, und einen zweiten Fund des neuen Property-Tests dazu
    (gerundete Absichten ließen Regel R in der Nachrechnung reißen). Dazu kamen die Vorschau-
    Kennzeichnung in UI und HA, die Prüfung des Zielverzeichnisses vor dem Lauf, die Zeile
    „Übersprungene Tage“ im Bericht, ein gesperrtes „Neu planen“ während des Schaltvorgangs, der
    wechselhafte Tag mit 1500 W PV-Spitze und Nachzüge in Betriebshandbuch und Spec. Das Re-Review
    war sauber (bereit zur Integration).
  - **Entschieden und aufgeschoben:** Die Festlegungen und die aufgeschobenen Kleinigkeiten stehen
    unter „Bewusst offen gelassen“ („Entschieden in Plan 3b“).
  - **Noch nicht:** die Planausführung im core mit „Plan vs. Ist“ und dem Budget des 15-min-Wächters
    (3c), Regel R2 und die Autos in der Planung samt ihren Auslösern (Phase 4), die Kostenanzeige zu
    gewählten Plänen und die Prognosegüte als eigene Seite (Phase 5). Live ist weiterhin kein Aktor, und
    3b schaltet nichts an der Anlage.
- **Phase 3c (Planausführung im core) ist umgesetzt, je Task und im Gesamtreview geprüft** (Plan:
  `docs/superpowers/plans/2026-10-17-phase-3c-planausfuehrung.md`, 17 Tasks; Ausführung
  subagent-driven; Branch `claude/project-thread-syy5yu`, ab `20c338d`, mit der Fix-Welle nach dem
  Gesamtreview):
  - **Plan in der Absicht** (`shared/plan.py`, `IntentPlan`): die app schickt nur den bindenden Teil
    (die Slots vor `valid_until`, höchstens 8, je Slot Absicht, Budget, Preis und Plan-Schwelle) als
    neue Version der Absicht, ohne Eintrag im Änderungsprotokoll (`app/planning/sender.py`: nach jedem
    Planungslauf, jeder Verbindung, jeder Abfrage von Health, jeder Änderung der Einstellungen und
    alle 30 s, solange der core ihn nicht hat; nur, wenn der core mit der gültigen Absicht regelt). Der core prüft und klemmt ihn (`core/control/intent.py`:
    422 für Lücken, Raster, Gültigkeit und Netzladen trotz Schalter aus; `valid_until` höchstens 45 min
    nach der Annahme, Netzladen höchstens `victron.max_grid_charge_setpoint_w`) und prüft die
    Gültigkeit auf seiner eigenen Uhr (`core/control/plan_exec.py`, Health `plan`).
  - **Ausführung** (`core/control/battery.py`): `HALTEN` und `NETZLADEN(P)` über den vorhandenen
    Netz-Sollwert-Override (gewollter Netzbezug `max(0, Hauslast + Autos − PV − Basis)`, bei
    `NETZLADEN` plus `P`), Boost lädt dann aus dem Netz; ohne gültigen Slot `EIGENVERBRAUCH`. Budget
    des 15-min-Wächters aus dem Slot (`core/control/peak_guard.py`, `guard_target_kw`), sonst
    `control.peak_target_kw`. Kein neuer Schreibweg an Victron, die Safety-Schicht ist unverändert.
  - **Akku-Unterstützung:** wirksame Schwelle = Maximum aus Unterstützungsschwelle der Absicht (Standard
    `control.support_soc_pct`) und Plan-Schwelle des Slots (app: `support_thresholds`, Grundcode
    `UNTERSTUETZUNG_RESERVIERT`); Schalter „Akku-Unterstützung erlaubt“ und Zahl „Unterstützungsschwelle“
    in der Absicht, im UI (Karte „Akku-Unterstützung“ auf „Laden“), in HA (`battery_support`,
    `support_soc`) und per `PUT /api/control/support`, mit Aktion `control.support` im
    Änderungsprotokoll.
  - **Entscheidungslog:** Gründe `plan_hold` („Plan: Halten“) und `plan_grid_charge` („Plan:
    Netzladen“), Eingaben `plan_slot_ts`, `price_ct`, `budget_kw` und an Victron `plan_grid_charge_w`.
  - **Stand der Ausführung statt „Vorschau“:** sieben Stände (core nicht erreichbar, noch nicht beim
    core, Regelbetrieb aus, core regelt nicht, Victron gesperrt, Dry-Run, ausgeführt) oben auf der Seite
    „Zeitplan“ und als Attribut `execution` der vier Plan-Sensoren in HA (statt `preview: true`).
  - **„Plan gegen Ist (heute)“:** Karte im Zeitplan, `GET /api/plan/review` (je vergangener
    Viertelstunde der Slot des damals gültigen Plans gegen die Abrechnung, SOC aus dem Verlauf).
  - **Mehrkosten beim Boost:** „Boost kann bis zu {€} € Leistungspreis im Monat kosten ({kW} kW mehr
    Spitze).“ auf der Seite „Laden“ (ab „Bedienen“), nur mit Leistungspreis mit Satz im Monat
    (`GET /api/control/boost-cost`). Das Demo-Backend hat dafür einen erfundenen Leistungspreis.
  - **Simulator:** alle Szenarien mit 88 % Roundtrip (das Modell ohne Angabe verlustfrei), fünf
    Plan-Szenarien dazu (`plan-halten`, `plan-netzladen`, `plan-abgelaufen`, `plan-budget`,
    `plan-boost`, jetzt 28).
  - **Doku:** Betriebshandbuch („Akku und Spitzen“ mit „Absicht des Plans“, Health `plan` und den
    Schritten „Victron in den Dry-Run und live“; „Planung“ mit „Übergabe an den core“, „Stand der
    Ausführung“ und „Plan gegen Ist“; „Laderegelung“, „Absicht per PUT /v1/intent“), Spec („Stand Plan
    3c“ in 5.3, 7.4, 7.6, 7.7, 7.10, 12, 13, 16 und 18).
  - **Gesamtreview und Fix-Welle:** Der Gesamtreview („mit Korrekturen“, keine kritischen Befunde) fand
    zwei wichtige Befunde, die die Fix-Welle behoben hat: Der Sender schickt einen Plan nur, wenn der
    core mit der gültigen Absicht regelt, verwirft Health bei der Trennung und gleicht nach jeder
    Abfrage von Health ab (sonst band ein Plan nach einem Neustart des core einen wiederhergestellten
    oder verbrauchten Boost an das nächste Anstecken); das Budget des Plans liegt nie unter seinem
    eigenen Bezug und am Monatsanfang nicht bei 0 kW. Dazu: Der Stand der Ausführung nennt
    „Regelbetrieb aus“ und „regelt nicht“ vor „noch nicht beim core“ und „ausgeführt“ nur mit
    `plan.state` `valid`; der core hebt eine Unterstützungsschwelle unter der USV-Reserve mit Hinweis,
    statt die Absicht abzulehnen; das Betriebshandbuch nennt den Rollback vor 3c und das neue Binden
    mit jedem Plan.
  - **Entschieden und aufgeschoben:** Die Festlegungen und die aufgeschobenen Kleinigkeiten stehen
    unter „Bewusst offen gelassen“ („Entschieden in Plan 3c“), dazu, was nach dem Gesamtreview offen
    ist.
  - **Noch nicht:** Regel R2 und die Autos im Planer (Phase 4), die Begründung des Plan-Slots in
    „Warum?“, „Plan gegen Ist“ für andere Tage als heute im UI, ein Totband in der Drosselung des
    Wächters. Live ist weiterhin kein Aktor, und 3c schaltet nichts an der Anlage.
- **Phase 4a (Statistik) ist umgesetzt und je Task geprüft** (Plan:
  `docs/superpowers/plans/2026-10-17-phase-4a-statistik.md`, 16 Tasks; Ausführung subagent-driven;
  Branch `claude/phase-4-plan-whnbh6`, ab `7dcb3d2`, seit dem 18.10.2026 auf `main`; Gesamtreview,
  Fix-Welle und Nacharbeit vom 18.10.2026 sind abgeschlossen).
  Nur app, UI und `deploy/`; die Dateien unter `src/bindaems/core/`, `src/bindaems/shared/` und
  `cerbo-watchdog/` sind unverändert:
  - **Migration `0010`** (`v0010_statistics.py`): `assignment_log`, `charging_session`, `ledger_cost`,
    `ledger_check` und `consumer_day`; die Herkunft eines Slots kennt `estimate`, `DaySummary` hat
    `estimated_slots`. `0011` (`v0011_cost_basis.py`, mit der Nacharbeit) passt `ledger_cost` an und
    leert die Tabelle; sie wird beim Start neu gerechnet.
  - **Ladesessions** (`app/charging/`): `plug.py` liest den Steckzustand aus dem State des core,
    `tracker.py` führt Belegung und Sessions (eine Pause bis 30 min trennt nicht), `sessions.py` rechnet
    Energie und Kosten je Session und je Auto; `GET /api/charging/sessions`.
  - **Einstandspreis des Akkus** (`app/ledger/costing.py`): PV-Ladung 0 ct, Netzladung zum Bezugspreis,
    Entladung mit dem Mittelwert plus Verschleiß; die Kosten je Auto folgen daraus. Fehlt der
    Bezugspreis einer Netzladung, ist der Einstandspreis „unbekannt“, solange dieser Anteil mindestens
    1 % des Akkubestands ist.
  - **Abgleich und Nachrechnung** (`app/ledger/counters.py`, `checks.py`, `estimate.py`): nachts um
    02:45 Uhr zuerst die Nachrechnung der Lücken der letzten 7 Tage aus den Zählerständen, dann der
    Abgleich der Abrechnung mit den Zählern (7 Tage, beim Start 35), danach die Energie je Verbraucher und
    Tag; `GET /api/ledger/checks`.
  - **Statistik und API** (`app/stats/`): `GET /api/stats?period=…` für Tag, Woche, Monat und Jahr mit
    Kennzahlen, Kosten (Grundgebühren und Leistungspreis), Autos, Verbrauchern, Monatsspitzen und
    Abgleich.
  - **Home Assistant:** `savings_today`, `savings_month`, `autarky_today` und je Fahrzeug
    `<v>_charge_energy`, alle 15 Minuten und bei jedem neuen Slot neu gerechnet.
  - **UI:** die Seite „Statistik“ (Navigation nach „Verlauf“) mit Kacheln, Diagramm „Energie“ und den
    Karten „Kosten“, „Autos“, „Ladesessions“, „Verbraucher“, „Monatsspitzen“ und „Abgleich mit den
    Zählern“; auf „Laden“ die Zeile der laufenden oder letzten Session in der Fahrzeugkarte. Die
    Demo-Welt hat 14 erfundene Tage dazu (`tests/ui_history.py`, `tests/ui_world.py`).
  - **Grafana:** `deploy/grafana/bindaems.json` (Leistung, Ladestand, Preise, Abrechnung je Tag, die
    Entscheidungen als Markierungen).
  - **Doku:** Betriebshandbuch (Abschnitt 8: „Einstandspreis des Akkus“, „Ladesessions und Kosten je
    Auto“, „Lücken nachrechnen“, „Abgleich mit den Zählern“, „Statistik je Zeitraum“, „Statistik
    ausrollen (Plan 4a)“; Abschnitt 3 „Grafana“; Abschnitt 7 die Sensoren; Abschnitt 12 die Seite
    „Statistik“), Spec („Stand Plan 4a“ in 11.1, 11.3, 11.4, 12, 13, 15 und 18) und die Checkliste
    `docs/verification/abnahme-phase-4.md`.
  - **Entschieden und aufgeschoben:** Die Entscheidungen, die Festlegungen der Umsetzung und die
    aufgeschobenen Kleinigkeiten stehen unter „Bewusst offen gelassen“ („Entschieden in Plan 4a“).
  - **4b** (der Modus „Geplant“ mit Ziel-SOC und Abfahrt, die Autos im Planer, Regel R2, die späteste
    Startzeit, das Tesla-Ladelimit, HA vollständig und die Abnahmepunkte „HA in beide Richtungen“ und
    „Ziel-SOC“) ist inzwischen auf dem Arbeitszweig umgesetzt (nächster Punkt). 4a schaltet nichts an
    der Anlage.
- **Phase 4b (Geplantes Laden) ist umgesetzt, je Task und im Gesamtreview geprüft, die Fix-Welle ist umgesetzt** (Plan:
  `docs/superpowers/plans/2026-10-18-phase-4b-geplantes-laden.md`, 19 Tasks; Ausführung
  subagent-driven; Branch `claude/phase-4b-plan-agf1qj`, ab `fb23bd0`; **seit dem 19.10.2026 auf
  `main`, nicht ausgerollt**; Gesamtreview am 19.10.2026, Fix-Welle und Re-Review abgeschlossen). core, `shared`, app, UI und Simulator:
  - **Absicht** (`shared/control.py`, `shared/plan.py`): `ChargeMode.PLANNED`, je Fahrzeug
    `target_soc_pct` (Standard 80) und `departure` (`HH:MM`, Standard `07:00`), je Plan-Slot
    `vehicles` (W je Auto); der core lehnt „Geplant“ für einen Gast ab und klemmt die Plan-Leistung auf
    die Höchstleistung des Fahrzeugs.
  - **core** (`core/control/vehicle_soc.py`, `planned.py`, `charging.py`, `charging_tesla.py`):
    Ladestand gemessen, geschätzt oder angenommen; Bedarf, späteste Startzeit und die Stände `plan`,
    `surplus`, `due`, `reached`; „Geplant“ mit festem Bedarf wie Sofort, Stopp am Ziel, keine
    Akku-Unterstützung; Tesla-Ladelimit (`charge_limit_sync`); „geladen“ je Ladelimit; Health mit
    `soc_pct`, `soc_source`, `charge_limit_pct` und `planned`.
  - **Planer** (`app/planning/vehicles.py`, `optimizer.py`, `fallback.py`, `explain.py`): die Autos in
    „Geplant“ im MILP mit Fehlmenge (2000 ct/kWh) und Regel R2 (P25, 200 ct/kWh), Ersatzplan für
    Autos, Auslöser „Fahrzeug geändert“, drei neue Grundcodes, Plausibilität mit Autos.
  - **app:** `PUT /api/control/vehicles/{key}/target`, Zählung der geplanten Ladungen (Migration
    `0012`, Tabelle `planned_charge`) und `planned` in `GET /api/stats`; HA vollständig (Ziel-SOC,
    Abfahrtszeit als `text`, „Fertig um“, „Ziel erreichbar“, „Ziel-SOC unerreichbar“) und
    Benachrichtigungen für alle Warnungen.
  - **UI:** Ziel-SOC, Abfahrt und „Fertig um“ auf „Laden“, Diagramm „Autos“ im Zeitplan, Karte
    „Geplantes Laden“ in der Statistik.
  - **Simulator und Szenarien:** fünf Szenarien dazu (jetzt 33), Planungsszenarien mit einem Auto.
  - **Doku:** Betriebshandbuch („Laderegelung“, „Geplant“; „Geplantes Laden ausrollen (Plan 4b)“;
    „Planung“, „Autos im Plan“; Abschnitte 7, 8 und 12), Spec („Stand Plan 4b“ in 5.3, 6.5, 7.5, 7.6,
    7.10, 9.3 bis 9.6, 11.1, 11.3, 12, 13, 16 und 18) und die Checkliste
    `docs/verification/abnahme-phase-4.md` (Abschnitte 4 „Ziel-SOC“ und 5 „HA in beide Richtungen“).
  - **Gesamtreview und Fix-Welle (19.10.2026):** Der Gesamtreview ist durch, die Fix-Welle (12 Punkte)
    und ein kurzer Nachlauf sind umgesetzt, das Re-Review ist sauber (kein kritischer, kein wichtiger
    Befund offen). Behoben sind: „Fertig um“ und der erwartete Ladestand, wenn die Abfahrt hinter dem
    Horizont des Plans liegt; ein Auto, das der gültige Plan nicht nennt, lädt jetzt nach der
    spätesten Startzeit (der Plan nennt jedes geplante Auto in jedem Slot vor seiner Abfahrt, 0 W
    ausdrücklich); ein Plan, der entsteht, solange Health die Absicht noch nicht bestätigt hat, wird für
    die Autos neu gerechnet; „Fertig um“ ist auf die Minute gerundet; die Benachrichtigung „Mitregelndes
    System“ geht nicht mehr doppelt hinaus; die Zählung der geplanten Ladungen läuft über Lücken ohne
    Health; die Seite „Laden“ zeigt „Fertig um“ erst, wenn der Plan zu Ziel und Abfahrt passt.
  - **Offen (klein):**
    - Der Ersatzplan kann die Regel R2 bei kleinem PV-Überschuss unter der Mindestleistung des Autos
      überschreiten (Spec 18, „Stand Plan 4b“, „Offen“).
    - Die Lösezeit mit einem Auto in den Wintermonaten mit Leistungspreis hat im Dev-Container die
      Grenze von 20 s erreicht; an der Anlagenmaschine messen.
    - Einmal in HA prüfen, dass die Text-Entität „Abfahrtszeit“ „unbekannt“ zeigt (nicht den Text
      „None“), solange keine Absicht bekannt ist.
    - `horizon_need` hat keinen Puffer: Liegt die Abfahrt knapp hinter dem Ende des Horizonts, gibt es
      keinen Spielraum.
    - Die gesammelten Kleinigkeiten stehen in der Abschlusszusammenfassung
      `/mnt/project-files/plaene/phase-4b-abschluss.md` (außerhalb des Repos).
- **Phase 6a (Klimaanlage) ist umgesetzt, je Task und im Gesamtreview geprüft, die Fix-Welle ist umgesetzt**
  (Plan: `docs/superpowers/plans/2026-10-19-phase-6a-klimaanlage.md`, 13 Tasks und Task 12b; Ausführung
  subagent-driven; Branch `claude/project-thread-79ieqs`, seit dem 20.10.2026 **auf `main`, nicht
  ausgerollt**). Der core verschiebt den Sollwert der Klimaanlage (TA UVR610) um ±3 K in Stufen von
  0,5 K über CoE V2 (UDP 5442, als CAN-Knoten 60, Ausgang 1, an das C.M.I. 192.168.81.92):
  - **CoE** (`shared/coe.py`, `core/adapters/coe.py`): Codec mit Prüfvektor, Empfang der Tabelle aus
    `config.yaml` (Ist, Soll, Echo, Leistung, Betriebsart), Senden alle 30 s nur mit gültiger Freigabe
    (10 s), sonst 0; 0 beim Start und am Ende jeder Sitzung. Die UVR fällt nach 5 min ohne Paket selbst
    auf 0 zurück.
  - **Safety** (`core/safety/offset.py`, `limits.py`): Familie `thermal`, Bereich ±3 K, Raster 0,5 K,
    höchstens eine Stufe je 10 min weg von 0, zurück auf 0 immer sofort.
  - **Aktor und Regelung** (`core/actuators/thermal.py`, `core/control/thermal.py`, `regulation.py`,
    `regulation_link.py`): Bestätigung über das Echo, Alarm `thermal.echo_mismatch.<name>`; Strategie mit
    Rangfolge Akku → Autos → Klima → Einspeisung, Hinweis „teurer Strom“ aus dem Plan, Wächter der
    Spitze; eigener geschützter Aufruf mit Alarm `control.thermal_exception`. Die Freigabe gilt nur in
    einem Zyklus mit entschiedenem Versatz ≠ 0; ohne Echo ist die Klimaanlage nicht steuerbar.
  - **Vorrang der Autos** (`charging.py`): „will mehr“ nur, wenn das Auto seinem Befehl folgt, mit
    Anlaufzeit 90 s, Hysterese und nur mit der Leistung, die die Klimaanlage freigeben kann.
  - **Anzeige:** Karte „Klimaanlage“ auf „System“, HA-Sensoren `thermal_<key>_offset/mode/power/wish`,
    Influx-Messung `thermal`, Grafana-Zeile „Klimaanlage“, neun Gründe im Entscheidungslog.
  - **Werkzeuge:** `coe_probe listen`, `verify_part2 --punkt thermal --geraet klima` (Spec 17.2, Punkt
    9); sechs Szenarien `klima-*` im Simulator.
  - **Deploy und Doku:** `ems-core` veröffentlicht `5442:5442/udp`; Betriebshandbuch „Klimaanlage
    (Phase 6a)“, Checkliste `docs/verification/abnahme-phase-6a.md`, Spec „Stand Plan 6a“ in 8.2,
    8.7, 11.2, 12, 17.2 und 18.
  - **Offen (klein):** Übergabe ohne Echo bleibt in „Übergabe“ (Festlegung R32, sicher); die
    Schätzung der Grundlast der Klimaanlage ist im Dry-Run nicht sichtbar; an der Anlage zu bestätigen:
    Absender der Pakete, Knotennummer und Einheit der Leistung, die Zeiten. Alles weitere steht in
    `docs/superpowers/plans/2026-10-19-phase-6a-entscheidungen.md`.
- **Plan 3d (Regel R1 nur, bis der Akku leer ist) ist umgesetzt, je Task und im Gesamtreview geprüft,
  und die Fix-Welle danach ist umgesetzt**
  (Plan: `docs/superpowers/plans/2026-10-20-phase-3d-regel-r1.md`, 4 Tasks, Ausführung subagent-driven;
  Branch `claude/project-thread-707vv8`, seit dem 20.10.2026 auf `main`, nicht ausgerollt). Anlass war die
  Klarstellung des Betreibers vom 20.10.2026: R1 soll verhindern, dass Netzstrom den Akku füllt, den
  die PV ohnehin gefüllt hätte; ist der Akku vor der nächsten Einspeisung leer (heute trüb, morgen
  sonnig), darf er aus dem Netz laden. Nur app und Szenario-Werkzeug:
  - **Nachrechnung** (`app/planning/plausibility.py`): `EMPTY_TOL_KWH = 0,1`, `charged_window_end` und
    `export_while_charged_kwh` (Fenster vom `NETZLADEN`-Slot bis zum Ende des ersten Slots mit SOC ≤
    Reserve + 0,1 kWh); `export_after_kwh` ist gelöscht. Prüfung, Kürzen der Absichten (`intents.py`),
    Ersatzplan, Erklärung und Szenario rechnen damit.
  - **Optimierer** (`app/planning/optimizer.py`): `R1_EMPTY_TOL_KWH = 0,05`; Binärvariable `e_t`
    (`leer_t`), stetige `c_t` (`netz_im_akku_t`, `netz_im_akku_z_t`) und `r1_t: pv_x_t ≤ PV_t · (1 − c_t)`,
    erst ab dem ersten Slot mit `z_t`. Die Lösezeit blieb gleich (längstes Planungsszenario 90,5 s auf
    88,8 s, Gesamtzeit der Tests); Zeitlimit 20 s und `MIP_GAP` 0,005 unverändert.
  - **Erklärung** (`explain.py`): „Kein Netzladen: Ab {um} Uhr speist die PV laut Prognose {kwh} kWh ein,
    bevor der Akku leer ist (Regel R).“; `um` ist der erste Einspeise-Slot im Fenster, Code und Parameter
    bleiben. Die Zahl „Netzladen vor tatsächlicher Einspeisung“ im Szenario-Bericht gilt jetzt über den
    ganzen Lauf statt je Tag.
  - **Doku:** Spec „Stand Plan 3d (20.10.2026)“ in 9.3 (mit den Verweisen in 9.4, 9.5, Präzisierung 19
    und der Regel-R-Fassung von 3b), Betriebshandbuch („Planung“, Absatz „Regel R“, Prüfpunkt „Regel R“
    unter „An der Anlage prüfen“, Tabelle der Erklärungen, Szenario-Bericht).
  - **Rulings der Umsetzung:** `export_after_kwh` blieb bis Task 3, weil `explain.py` und das Szenario-
    Werkzeug sie bis dahin nutzten; Task 1 stellte die Verstoß-Prüfung des Szenario-Werkzeugs
    (`broken` in `tools/scenario/runner.py`) schon auf die neue Regel um, damit der Integrationslauf
    von Task 2 nach ihr urteilt; der Test des Ersatzplans nutzt Last 0 in billigen Slots 0 bis 7 und PV ab
    Slot 9 (bei 2 kW Last ist die Ladung vor der PV immer verbraucht, das Szenario des Plans sperrt nie);
    ein Test des Optimierers lässt Netzladen bis zur Toleranz von 0,05 kWh zu (`gr_b < 1 oder
    soc_end ≤ lo + R1_EMPTY_TOL_KWH`, Kosten falls falsch: höchstens 0,05 kWh Netzenergie je Fenster vor
    der PV); Commits der Teilagenten tragen den Co-Authored-By des Modells, das sie schrieb.
  - **Aufgeschobene Kleinigkeiten:** Task 1: die Änderung von `broken` im Szenario-Werkzeug hat keinen
    eigenen Test (Task 3 deckt sie mit `r1_violations == 0`); ein Test `test_export_after_includes_the_slot`
    entfiel mit `export_after_kwh`; `EMPTY_TOL_KWH` könnte `= R1_TOL_KWH` sein; ein Netzladen bis rund
    0,1 kWh endet innerhalb der Toleranz und das Fenster ist ein Slot (stimmig mit der Toleranz); der Text
    des Befunds „danach {kWh} kWh Einspeisung“ blieb. Task 2: Test 3 hat keine PV und beweist nur die
    Lösbarkeit mit `lo = soc0`; `e` des letzten Slots wird angelegt und nicht gebraucht; der Docstring
    sagt „Reserve + Toleranz“, `lo` ist `min(Reserve, soc0)`. Task 3: `_charge_before_export_kwh`
    durchsucht den ganzen Lauf je Ladeslot (im schlimmsten Fall quadratisch, bei rund 2880 Slots für 30
    Tage harmlos); kein Test hält einen Fall fest, in dem die alte Tagesgrenze zählte.
  - **Fix-Welle nach dem Gesamtreview** (20.10.2026; Befunde in
    `.superpowers/sdd/2026-10-20-phase-3d-regel-r1/final-fixes.md`, ein wichtiger, sonst Kleinigkeiten;
    jeder Code-Fix mit einem Test, der vorher scheiterte):
    1. *Wichtig:* Das Szenario-Werkzeug prüfte Regel R mit der Reserve des Laufs (`min(Reserve,
       Start-SOC)`), die Nachrechnung jedes Plans rechnet aber mit dessen eigener (`min(Reserve, SOC zur
       Planzeit)`). Startete ein Lauf unter der Reserve, zählte ein legitimes Netzladen als Verstoß
       („Nicht bestanden“). `_Planned` trägt jetzt `lo_kwh` des Plans.
    2. `_keep_rule_r1` kürzte um die ganze Einspeisung im Fenster, unter der neuen Regel oft PV über der
       Ladeleistung (2 kW Netzladen wurde ganz zu `HALTEN`). Gekürzt wird jetzt höchstens um die Ladung,
       die vor der ersten Einspeisung im Fenster noch über Reserve + `EMPTY_TOL_KWH` liegt (durch `η_ch`),
       auf 100 W aufgerundet, mindestens 100 W je Runde (die Schleife endet).
    3. Der Ersatzplan prüft R1 nach jedem Kandidaten für jedes `NETZLADEN`, nicht nur für den Kandidaten:
       Ein früherer Slot konnte den Akku vor einem schon angenommenen über die Leer-Grenze heben.
    4. Kontrollfall zum Optimierer-Test von R1: dieselben Eingaben ohne wirksame Sperre laden über die
       Leer-Grenze hinaus aus dem Netz (die Sperre kommt also von R1).
    5. Der Befund lautet jetzt „Regel R verletzt: Netzladen um HH:MM, danach {kWh} kWh Einspeisung,
       bevor der Akku leer ist“ (Test des Wortlauts im Betriebshandbuch).
    6. Spec 9.6 (Erklärungsregel `REGEL_R_BLOCKIERT_NETZLADEN`, „bevor der Akku leer ist“), 9.4 und
       Präzisierung zur Kürzung nachgezogen; überlange Zeilen im Betriebshandbuch umgebrochen.
    Damit sind aus den aufgeschobenen Kleinigkeiten der Text des Befunds und der fehlende Test der
    Szenario-Prüfung erledigt. Voller Lauf nach der Fix-Welle: 7949 Python-Tests (4188 `core`, Kopf
    und Dokumentation, 3382 app/`shared`/Watchdog/Werkzeuge, 333 Integration, 46 Property), alle
    grün; ruff, mypy und import-linter sauber; Abdeckung 98,04 % insgesamt und 99,94 % für
    `core/safety`, `core/control` und `core/actuators`. Die UI ist unverändert (kein Vitest-Lauf).
  - **Tests auf dem Branch von Plan 3d** (20.10.2026): 7944 Python-Tests im vollen Lauf (in vier Teilen
    parallel mit gemeinsamer Abdeckung gemessen: 4141 `core` und Kopf, 3378 app/`shared`/Watchdog/Werkzeuge,
    46 Dokumentation, 333 Integration, 46 Property), alle grün; Vitest 696; ruff, mypy und import-linter
    (4 Verträge) sauber; Abdeckung 98,03 % insgesamt und 99,94 % für `core/safety`, `core/control` und
    `core/actuators` zusammen. Die Planungsszenarien (Integration) dauerten 38 Minuten bei vier
    parallelen Läufen.
- **Phase 5a (Lastmodell) ist umgesetzt und je Task geprüft**
  (Plan: `docs/superpowers/plans/2026-10-20-phase-5a-lastmodell.md`, 14 Tasks; Ausführung subagent-driven; Branch `claude/phase-5-plan-s7xe0n`, **noch nicht
  auf `main`, nicht ausgerollt**). Die app lernt die Hauslast zusätzlich mit LightGBM; das Profil der
  Lastprognose (3a) bleibt Rückfall und Vergleichsgröße:
  - **Daten:** Wetterarchiv `weather_slot` (jede PV-Prognose schreibt die Temperatur bis 6 h nach der
    Ausgabe; beim Start einmalig 92 Tage von Open-Meteo nachgeholt, wenn das Archiv jünger als 56 Tage
    ist), bis zu 8 HA-Sensoren als Merkmale (`app/learning/ha_sensors.py`), Merkmalsbasis
    (`features.py`: Kalender, Vorlauf, Temperatur, Last vor 1 und 7 Tagen, letzte Stunde, Profil).
  - **Modell:** LightGBM mit festen Parametern, Median als Ziel, ein Modell für alle Vorläufe bis 72 h;
    nachts um 03:15 Uhr, beim Start ohne Version und per Taste „Jetzt neu trainieren“ (Admin); erst ab
    28 Tagen mit je 80 von 96 Viertelstunden. Versionen in `model_registry` und als Dateien unter
    `/data/models/load/` (14 neueste und die aktive).
  - **Backtest und Aktivierung:** rollierend über 2 bis 8 Wochen; aktiv ab 10 % niedrigerem MAE über die
    letzten 14 Tage, Grundlage Schattenbetrieb (ab 10 Tagen mit Werten), sonst Backtest; sonst das
    Profil, ebenso ohne Modell, bei ausgeschaltetem Schalter und bei jedem Fehler. Jede stündliche Ausgabe
    schreibt Profil und Modell ins Archiv (`load_profile`, `load_model`); `load` ist das aktive Verfahren.
  - **UI:** die Seite „Prognosen“ (Lastprognose mit Taste, Prognosegüte, Fehler je Tag, Backtest,
    Merkmale), der Abschnitt „Lastmodell“ unter „Einstellungen“, die Komponente `load-model`, der
    Verweis „Mehr unter Prognosen“ auf „System“. API: `GET /api/learning/load`,
    `POST /api/learning/load/train`, `GET /api/learning/candidates`, `GET /api/forecast/quality/daily`.
  - **Deploy und Doku:** Migration `0013`, `lightgbm` und `numpy` im Extra `app`, `libgomp1` im Image
    (das Image von `ems-app` wird rund 130 MB größer); Betriebshandbuch „Lastmodell (Phase 5a)“, Checkliste
    `docs/verification/abnahme-phase-5a.md`, Spec „Stand Plan 5a“ in 5.2, 10.2, 10.3, 11.3, 13 und 18.
  - **Offen (klein):** die Liste unter „Bewusst offen gelassen“, „Aufgeschobene Kleinigkeiten aus Plan 5a“;
    an der Anlage das Ausrollen und die 28 Tage der Abnahme (Punkt 10 unter „Offen an der Anlage“).
- **Phase 5b (Fahrverhalten) ist umgesetzt, je Task und im Gesamtreview geprüft**
  (Plan: `docs/superpowers/plans/2026-10-20-phase-5b-fahrverhalten.md`, 14 Tasks; Ausführung subagent-driven; Branch `claude/phase-5b-plan-xo03ty`, **seit dem
  21.10.2026 auf `main` (Fast-Forward mit Zustimmung des Betreibers), Gesamtreview und Fix-Welle erledigt, nicht ausgerollt**). Die app lernt je Auto, wann es abfährt, mit welchem
  Ladestand es zurückkommt und wann es angesteckt ist:
  - **Lernen:** empirische Perzentile über 56 Tage statt LightGBM (`app/learning/driving_*.py`): Daten aus
    Anwesenheit (HA-Entität, `home_entity`, Tessie, sonst Ansteckvorgänge), Tage, Profil je Wochentag mit
    Gruppen „Mo–Fr“, „Sa“, „So/Feiertag“, Vorschläge (Abfahrt P20, Ziel aus Puffer plus Verbrauch P80,
    geklemmt) mit Konfidenz, Anwesenheitsprognose, Trefferquote über 28 Tage. Nachts um 03:40 Uhr, beim
    Start und per Taste „Jetzt neu berechnen“ (Admin); Tabelle `driving_profile` (70 Tage).
  - **Automatik** (je Auto, ab Werk aus): setzt Ziel und Abfahrt (mit Tag, `departure_on`) von „Geplant“ nach
    dem Vorschlag, wenn die Konfidenz mindestens „mittel“ ist; jede Minute, nie während einer geplanten Ladung
    (außer in den ersten 10 min einer Zeile, `AUTO_ADOPT`); eine Änderung von Hand pausiert sie bis zur Abfahrt.
  - **Erwartete Autos:** ein Auto in „Geplant“, das nicht steckt und laut Anwesenheitsprognose (mindestens 50 %)
    kommt, steht ab der erwarteten Ankunft im Optimierer (Grundcode `AUTO_ERWARTET`); der core bekommt nichts.
  - **UI und HA:** Karten „Fahrverhalten“ und „Anwesenheit“ auf „Prognosen“, Vorschlag und Schalter auf „Laden“,
    erwartete Autos auf „Zeitplan“, Abschnitt „Fahrverhalten“ unter „Einstellungen“, Komponente `driving`;
    in HA je Fahrzeug `auto_target`, `suggested_departure`, `suggested_target`, `next_departure`. API:
    `GET /api/learning/driving`, `POST /api/learning/driving/recompute`,
    `PUT /api/control/vehicles/{key}/automation`.
  - **Deploy und Doku:** Migration `0014`; Betriebshandbuch „Fahrverhalten (Phase 5b)“, „Fahrverhalten ausrollen
    (Plan 5b)“, Checkliste `docs/verification/abnahme-phase-5b.md`, Spec „Stand Plan 5b“ in 5.3, 7.5, 7.6, 9.3,
    10.4, 11.3, 12, 13 und 18. Der core ändert sich nur in `core/control/planned.py`.
  - **Geprüft:** Gesamtreview, Fix-Welle (Budget des Wächters ohne erwartete Autos, Zielbereich wirkt sofort,
    Abfahrtstag in der UI, kein erwartetes Auto auf belegter Wallbox) und Re-Review; die Liste
    „Deferred minors 5b“ steht unter „Bewusst offen gelassen“.
  - **Offen:** an der Anlage das Ausrollen und die 28 Tage der Abnahme (Punkt 11 unter „Offen an der
    Anlage“).
- **Images:** `ghcr.io/binda5000/bindaems-core:latest` und `ghcr.io/binda5000/bindaems-app:latest`.
  Sie sind öffentlich, und die CI setzt `latest` bei jedem Push auf `main`. core und app werden
  immer gemeinsam aus demselben Stand ausgerollt (gleiche Compose-Datei, gleiche Image-Tags): Die
  UI-Schemas sind streng, eine abweichende Version zeigt auf der System-Seite nur „Unerwartete
  Antwort vom Server“.
- **Tests auf dem Branch von Plan 5a** (20.10.2026): voller Lauf mit `--cov` 8178 Tests in 49 Minuten,
  davon einer rot (siehe unten, behoben), dazu 2 neue Tests der Doku, 754 UI-Tests (Vitest) und 33 E2E-Tests (Playwright) grün; ruff, mypy
  und import-linter (4 Verträge) sauber; Abdeckung 98,14 % insgesamt, 99,94 % für `core/safety`,
  `core/control` und `core/actuators` und 99 % für `app/learning`. Ein Test des Lerndienstes (Zeitlimit des
  Trainings, 50 ms) schlug im vollen Lauf fehl, weil er unter `--cov` zu früh prüfte; er ist behoben
  (`8d05498`) und lief danach 30-mal mit Abdeckung grün. `git diff main --stat` für `core`,
  `cerbo-watchdog`, `shared/control.py`, `shared/plan.py` und `shared/config.py` ist leer.
- **Tests auf dem Branch von Plan 6a** (19.10.2026): nach der Fix-Welle 7934 Python-Tests im vollen
  Lauf, Vitest 696, ruff, mypy und import-linter (4 Verträge) sauber, Abdeckung des core 99 %
  (`core/safety` und `core/control` 99,9 %, `core/actuators` 100 %); der volle Lauf mit `--cov` vor der
  Fix-Welle: 7907 Tests, 98,04 % insgesamt, 99 % für die Grenze, 32 Minuten.
- **Tests auf dem Branch von Plan 4b** (am Ende der Fix-Welle, 19.10.2026): 7387 Python-Tests im
  vollen Lauf (mit den 33 Szenarien des Simulators und `--cov`), 678 UI-Tests (Vitest) und 28 E2E-Tests
  (Playwright), alle grün; ruff, mypy und import-linter sauber; Abdeckung 97,98 % insgesamt und 99 %
  für `core/safety`, `core/control` und `core/actuators` zusammen (am Ende von Task 19 waren es 7371,
  675 und 97,96 %); der volle Lauf von pytest mit `--cov` dauerte 31 Minuten (die
  Planungsszenarien mit Auto sind langsamer, Spec 16, „Stand Plan 4b“).
  Stand auf `main` nach Plan 4a: 6939 Python-Tests im vollen Lauf nach der Nacharbeit zu Plan 4a und der Festlegung zum
  Planer vom 18.10.2026 (mit den Szenarien des Simulators und `--cov`), alle grün, davon
  41 Property-Tests mit Hypothesis (6 für die Safety-Schicht, 6 für den Phasenwächter, 13 für den
  Akku-Sollwert, 7 für den 15-min-Wächter, 8 sonstige; Plan 4a brachte vier: die Wasserfall-Regel
  der Nachrechnung gegen die des core (zwei Tests), der Steckzustand der app gegen den des core und
  die Zuordnung der Slots zu Sessions gegen die einfache Rechnung), und 638 UI-Tests (Vitest) sowie
  27 E2E-Tests (Playwright).
  Abdeckung 98 % insgesamt und 98 bis 100 % für `core/safety`, `core/control` und `core/actuators`;
  der volle Lauf von pytest mit `--cov` dauerte 22 Minuten.
  Stand am Ende von Plan 4a, vor der Nacharbeit: 6827, 619 und 27.
  Stand davor, nach Plan 3c auf `main`: 6572, 515 und 24, Abdeckung 97,80 % insgesamt und 99 bis 100 %
  für `core/safety`, `core/control` und `core/actuators`. Der volle Lauf von pytest dauert rund
  23 Minuten.
  Geprüft wird mit ruff, mypy strict (auch für den Watchdog), import-linter (4 Verträge), pytest
  mit Abdeckungsgrenzen (≥ 75 % insgesamt, ≥ 90 % für `core/safety`, `core/control` und
  `core/actuators`), Vitest mit svelte-check, Prettier und ESLint; dazu E2E mit Playwright.
- **Offen:**
  - an der Anlage: Plan 3d (Regel R1 bis der Akku leer ist) ausrollen, Zeitpunkt entscheidet der
    Betreiber (Entscheidung 6); Vorschlag: mit 4b und 6a (Punkt 9 unter „Offen an der Anlage“)
  - an der Anlage: Plan 5b (core und app gemeinsam, Regelbetrieb aus) mit 3d, 4b, 5a und 6a ausrollen
    (Punkt 11 unter „Offen an der Anlage“); danach die Abnahme 5b (28 Tage,
    `docs/verification/abnahme-phase-5b.md`)
  - an der Anlage: Plan 5a (nur `ems-app`) mit oder nach 4b und 6a ausrollen (Punkt 10 unter „Offen an
    der Anlage“); danach die Abnahme 5a (28 Tage, `docs/verification/abnahme-phase-5a.md`)
  - an der Anlage: Plan 6a mit 4b nach der Abnahme der Phase 3 ausrollen (Punkt 8 unter „Offen an der
    Anlage“), vorher Routing und Firewall für UDP 5442; danach die Abnahme 6a (7 Tage Dry-Run, dann live)
  - an der Anlage: Plan 4b ausrollen, erst nach der Abnahme der Phase 3 und als Schritt des Betreibers
    (Punkt 7 unter „Offen an der Anlage“); danach die Abnahme der Phase 4 (30 Tage Statistik, „Ziel-SOC“
    ≥ 95 %, „HA in beide Richtungen“)
  - an der Anlage: Plan 4a ausrollen (nur `ems-app`, Punkt 6 unter „Offen an der Anlage“) und die 30 Tage
    der Abnahme beginnen
  - an der Anlage: Images von 2d, 3a, 3b und 3c ausrollen (Punkte „Zu 3a gehört“, „Zu 3b gehört“ und
    „Zu 3c gehört“ unter „Offen an der Anlage“), die 30 echten Tage durch die Werkzeuge rechnen (Punkt
    5 dort), Dry-Run neben evcc beginnen und dabei die Inbetriebnahme Punkt 3 (die Erkennung läuft im
    Dry-Run mit, Health zeigt, wer wo steht), der Rest von Teil 2 (EVCS-Verbindungsabbruch, Victron
    17.2-4), 2 Wochen Dry-Run der Planausführung mit DESS aus, dann Victron live (Punkt 4 dort)
  - vor der Live-Schaltung von EVCS und Tesla: die Liste „Vor der Live-Schaltung“ (siehe
    „Offen an der Anlage“)
  - die Abnahme der Phase 1 an der Anlage
  - kleine Punkte an der Anlage (siehe „Stand der Installation“): manuelle Punkte von Teil 1 und
    die Log-Zeile der Probe

## Arbeitsweise mit dem Betreiber

- Sprache Deutsch, Anrede „du“.
- **Ablauf je Phase (Spec 18):** Implementierungsplan, Freigabe, Umsetzung, Abnahme.
  Implementiert wird erst nach der Freigabe.
- **Werkzeuge:**
  - Pläne entstehen mit `superpowers:writing-plans` unter `docs/superpowers/plans/`.
  - Phase 1 lief inline („Native“, `superpowers:executing-plans`): Arbeitsliste der Umsetzung mit
    Festlegungen, am Ende ein frischer Gesamtreviewer.
  - Plan 2a lief mit `superpowers:subagent-driven-development`: je Task ein Implementierer und ein
    Reviewer, eine Arbeitsliste der Umsetzung mit Festlegungen und aufgeschobenen Kleinigkeiten, am
    Ende der Gesamtreview.
  - Plan 2b lief ebenso subagent-driven (Entscheidung des Betreibers vom 11.10.2026).
  - Plan 2c-1 lief ebenso (Entscheidung des Betreibers vom 12.10.2026). Für die Szenarien des
    Simulators galt: Scheitert eines, liegt der Fehler zuerst in der Regelung (eigener
    `fix(core)`-Commit mit Test); ein Szenario ändert man nur, wenn es physikalisch nicht zu
    schaffen ist.
  - Plan 2c-2a lief ebenso (subagent-driven): je Task ein Implementierer und ein Reviewer, eine
    Arbeitsliste der Umsetzung mit Festlegungen und aufgeschobenen Kleinigkeiten (ihr Inhalt steht
    unten unter „Bewusst offen gelassen“). Von den acht neuen Szenarien des Simulators scheiterten
    drei in der ersten Fassung; bei keinem lag der Fehler in der Regelung, und die drei wurden im
    Aufbau angepasst (Betriebshandbuch, „Simulator“). `spitze-15min` startet den Tesla seit der
    Nachrunde wieder wie geplant.
  - Plan 2c-2b lief ebenso (subagent-driven): je Task ein Implementierer und ein Reviewer, eine
    Arbeitsliste der Umsetzung mit Festlegungen und aufgeschobenen Kleinigkeiten (ihr Inhalt steht
    unten unter „Bewusst offen gelassen“). Der Review von Task 2 fand, dass der Ausschluss den Ort
    des Autos ignorierte; die Fix-Runde (mit Test) hat das behoben, und die Festlegungen zu
    Stromvergleich und zeitlicher Korrelation kamen danach dazu. In den drei neuen Szenarien und im
    umgebauten `zuordnung` lag kein Fehler in der Erkennung oder Regelung.
  - Plan 3a lief ebenso (subagent-driven, 14 Tasks): je Task ein Implementierer und ein Reviewer,
    eine Arbeitsliste der Umsetzung mit Festlegungen und aufgeschobenen Kleinigkeiten (ihr Inhalt
    steht unten unter „Bewusst offen gelassen“). Jede Task ließ das ganze Repository grün (auch den
    UI-Vertragstest), darum weitete Task 1 schon das UI-Schema der Tarifbestandteile; der
    Gesamtreview („mit Korrekturen“) fand einen wichtigen Befund, den die Fix-Welle behob.
  - Plan 3b lief ebenso (subagent-driven, 17 Tasks): je Task ein Implementierer und ein Reviewer,
    eine Arbeitsliste der Umsetzung mit Festlegungen und aufgeschobenen Kleinigkeiten (ihr Inhalt
    steht unten unter „Bewusst offen gelassen“). Der Review von Task 4 fand einen Defekt im Plan
    (freies `pv_h` im Modell); er wurde mit Test behoben und im Plan nachgetragen. Der Gesamtreview
    („mit Korrekturen“) fand zwei wichtige Befunde, die Fix-Welle behob sie, und das Re-Review war
    sauber. Die Trailer der Commits folgen ab Task 2 der Attributionszeile des jeweiligen Autors
    (falls das nicht gewollt ist: Trailer vor dem Merge umschreiben).
  - Befunde werden nach ihrer Wirkung neu bewertet. Critical und Important kommen in einen
    Fix-Durchgang, jeder mit einem Test, der vorher scheitert. Minors werden gesammelt und auf
    Wunsch erledigt („bitte die kleinigkeiten erledigen“).
- **Abschlussnachricht einer Phase:**
  - Ergebnis und Tests
  - „Festlegungen“ (Entscheidungen mit Kosten, falls falsch)
  - „Deferred minors“
  - Frage nach der Integration (merge, PR oder so lassen)
- **Git:**
  - Arbeitsbranch ist der Branch, den die Sitzung vorgibt (Plan 2b und 2c-1: `ccr-e451cb4c-qeg1tm`,
    EVCS-Hysterese, Plan 2c-2a und 2c-2b: `ccr-9778345e-t3x44k`, beide nach der Integration
    gelöscht; Plan 2d: `claude/project-thread-o6klxp`, auf `main`; Plan 3a und 3b:
    `claude/project-thread-syy5yu`, 3a auf `main`, 3b noch nicht integriert); der Standard-Branch auf
    GitHub ist `main`.
  - Nach `main` nur mit Zustimmung; bisher per Fast-Forward-Merge, Tests auf `main`, dann Push.
  - Pull Requests nur auf ausdrücklichen Wunsch.
  - Commit-Nachrichten auf Deutsch im Stil `fix(ui): …`, mit den Attributionszeilen, die die
    Sitzung vorgibt.
  - Keine Modellbezeichnungen in Dateien des Repositorys.
- **Secrets** nur über `BINDAEMS_*`-Umgebungsvariablen; `.env` wird nie committet.

## Wichtige Dokumente

| Dokument | Inhalt |
|---|---|
| `docs/superpowers/specs/2026-10-08-bindaems-design.md` | Spec, verbindlich. Wichtig: 7 Regelung, 8 Sicherheit, 13 Web-UI, 14 Login, 17 Prüfprotokoll, 18 Phasenplan, 19 offene Punkte. Die Absätze „Stand Plan 2a/2b/2c-1/2c-2a/2c-2b/2d/3a/3b/3c/3d/4a/4b/6a/5a“ gehen dem älteren Text davor vor |
| `docs/superpowers/plans/2026-10-08-phase-1a-fundament-datenerfassung.md` | Plan 1a (core) |
| `docs/superpowers/plans/2026-10-09-phase-1b-app-backend.md` | Plan 1b (app) |
| `docs/superpowers/plans/2026-10-09-phase-1c-web-ui.md` | Plan 1c (UI), mit Anhang A zu den API-Formen |
| `docs/superpowers/plans/2026-10-10-phase-2a-sicherheitsbasis.md` | Plan 2a (Sicherheitsbasis), mit dem Zuschnitt der Phase 2 in 2a–2d und den Präzisierungen gegenüber der Spec |
| `docs/superpowers/plans/2026-10-10-phase-2a-entscheidungen.md` | Entscheidungen, geparkte Befunde und aufgeschobene Kleinigkeiten aus der Umsetzung von Plan 2a |
| `docs/superpowers/plans/2026-10-11-phase-2b-aktoren.md` | Plan 2b (Aktoren), 18 Tasks; Entscheidungen des Betreibers vom 11.10.2026, Präzisierungen, Texte |
| `docs/superpowers/plans/2026-10-11-phase-2b-entscheidungen.md` | Entscheidungen, geparkte Befunde und aufgeschobene Kleinigkeiten aus der Umsetzung von Plan 2b |
| `docs/superpowers/specs/2026-10-12-phase-2c1-laderegelung-design.md` | Design 2c-1 (Laderegelung), umgesetzt; der Kopf nennt, was sich beim Bau geändert hat |
| `docs/superpowers/plans/2026-10-12-phase-2c1-laderegelung.md` | Plan 2c-1, 16 Tasks; Präzisierungen gegenüber dem Design, Texte, Szenarien des Simulators |
| `docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md` | Design 2c-2 (Akku-Sollwert, 15-min-Wächter, Zuordnungserkennung), Entscheidungen des Betreibers vom 14.10.2026; 2c-2a und 2c-2b umgesetzt; der Kopf nennt, was sich beim Bau geändert hat |
| `docs/superpowers/plans/2026-10-14-phase-2c2a-akku-spitzen.md` | Plan 2c-2a, 14 Tasks, umgesetzt: Umbau, Akku-Sollwert, 15-min-Wächter, Simulator; Präzisierungen gegenüber dem Design, Texte, Szenarien |
| `docs/superpowers/plans/2026-10-15-phase-2c2b-zuordnung.md` | Plan 2c-2b, 9 Tasks, umgesetzt: Zuordnungserkennung, Gast, Health je Wallbox, Simulator; Präzisierungen gegenüber dem Design, Texte, Szenarien |
| `docs/betrieb.md` | Betriebshandbuch: Installation, Proxy (6), HA (7), Tarif (8, seit 3a mit Leistungspreis, Gebühren und Einspeisung), Sicherung (9), Prüfprotokolle einschließlich Teil 2 (11), Web-UI mit den Seiten „Laden“ und „Entscheidungen“ (12), Betriebszustände, Absicht, Live-Schalter, Übergabe und Entscheidungslog (13), Watchdog am Cerbo (14); seit 4a in Abschnitt 8 „Einstandspreis des Akkus“, „Ladesessions und Kosten je Auto“, „Lücken nachrechnen“, „Abgleich mit den Zählern“, „Statistik je Zeitraum“ und „Statistik ausrollen (Plan 4a)“, in 12 die Seite „Statistik“ und die Session auf „Laden“, in 3 „Grafana“; am Ende „Laderegelung“ (Modi, Absicht, Zuordnung Auto und Wallbox, Dry-Run-Vergleich mit evcc, Umstieg), „Akku und Spitzen“ (Akku-Sollwert mit der Absicht des Plans, 15-min-Wächter, Health samt `plan`, Akku-Unterstützung bedienen, Victron in den Dry-Run und live mit den 2 Wochen Planausführung, Alarme), „Simulator“ „Prognosen“ (seit 3a: PV-Kalibrierung, Lastprognose, Prognosegüte, Nachtlauf, Schätzpreise, „An der Anlage prüfen“) und „Planung“ (seit 3b: Seite „Zeitplan“, Ablauf, Netzladen erlaubt, Einstellungen, Auslöser, Ersatzplan, Erklärungen, Budget, HA, API, Aufbewahrung, Szenarien mit `export_days` und `scenario_days`, „An der Anlage prüfen“; seit 3c: Übergabe an den core, Stand der Ausführung, Plan gegen Ist; seit 4b: Autos im Plan); seit 4b in „Laderegelung“ der Modus „Geplant“ und „Geplantes Laden ausrollen (Plan 4b)“, in 7 „Geplantes Laden“ und alle Warnungen als Benachrichtigung, in 8 die Zählung der geplanten Ladungen |
| `docs/superpowers/plans/2026-10-15-phase-2d-bedienung.md` | Plan 2d, 18 Tasks, umgesetzt, auf `main`: Bedienung in UI und HA, Entscheidungslog in SQLite, Tesla vor der Live-Schaltung; Präzisierungen gegenüber der Spec, Texte |
| `docs/superpowers/plans/2026-10-16-phase-3a-tarif-prognosen.md` | Plan 3a, 14 Tasks, umgesetzt, auf `main`: Tarifmodell mit Editor, Monatsspitze, Schätzpreise, Lastprognose, PV-Kalibrierung, Prognosegüte; die Einordnung teilt Phase 3 in 3a, 3b und 3c und nennt die Vorab-Entscheidungen für 3b und 3c |
| `docs/superpowers/plans/2026-10-16-phase-3b-optimierer-zeitplan.md` | Plan 3b, 17 Tasks, umgesetzt und auf `main`: Optimierer, Ersatzplan, Plausibilität, Erklärungen, Planungsdienst mit Speicher, Seite „Zeitplan“, HA, Szenario-Werkzeuge; die 15 Entscheidungen des Betreibers vom 16.10.2026, Präzisierungen gegenüber der Spec, Texte |
| `docs/superpowers/plans/2026-10-17-phase-3c-planausfuehrung.md` | Plan 3c, 17 Tasks, umgesetzt auf dem Branch (Gesamtreview und Fix-Welle erledigt): Plan in der Absicht, Prüfung im core, `HALTEN` und `NETZLADEN` über den Override, Budget des Wächters, Plan-Schwelle und Schalter der Akku-Unterstützung, Übergabe, Stand der Ausführung, „Plan gegen Ist“, Mehrkosten beim Boost, Simulator mit Wirkungsgrad; die 14 Entscheidungen des Betreibers vom 17.10.2026, Präzisierungen gegenüber der Spec, Texte |
| `docs/superpowers/plans/2026-10-17-phase-4a-statistik.md` | Plan 4a, 16 Tasks, umgesetzt auf dem Arbeitszweig (Gesamtreview und Fix-Welle abgeschlossen): Ladesessions, Einstandspreis, Kosten je Auto, Abgleich, Nachrechnung, Verbraucher je Tag, Seite „Statistik“, HA-Sensoren, Grafana; die 13 Entscheidungen des Betreibers vom 17.10.2026 (PV-Strom ins Auto kostet 0 ct), Präzisierungen gegenüber der Spec, Texte |
| `docs/superpowers/plans/2026-10-18-phase-4b-geplantes-laden.md` | Plan 4b, 19 Tasks, umgesetzt, seit dem 19.10.2026 auf `main` (nicht ausgerollt): Modus „Geplant“ mit Ziel-SOC und Abfahrt, Ladestand der Autos, späteste Startzeit, Tesla-Ladelimit, die Autos im Optimierer mit Regel R2, Ersatzplan für Autos, HA vollständig, Benachrichtigungen komplett, Zählung für die Abnahme „Ziel-SOC“, Simulator- und Planungsszenarien; die 17 Entscheidungen des Betreibers vom 18.10.2026, Präzisierungen gegenüber der Spec, Texte |
| `docs/superpowers/plans/2026-10-19-phase-6a-klimaanlage.md` | Plan 6a, 13 Tasks, umgesetzt, seit dem 20.10.2026 auf `main` (nicht ausgerollt): CoE-Codec und Adapter, Safety-Familie `thermal`, Aktor mit Echo, Strategie mit Rangfolge Akku → Autos → Klima, Hinweis aus dem Plan, UI, HA, Telemetrie, Werkzeuge, Simulator; die 16 Entscheidungen vom 19.10.2026 |
| `docs/superpowers/plans/2026-10-20-phase-3d-regel-r1.md` | Plan 3d, 4 Tasks, umgesetzt auf dem Branch `claude/project-thread-707vv8` (nicht auf `main`, nicht ausgerollt): Regel R1 sperrt Netzladen nur, solange der Akku nicht leer ist (Nachrechnung, Optimierer, Erklärung, Szenario-Werkzeug); Einordnung, die sechs Entscheidungen des Betreibers vom 20.10.2026, Review-Schwerpunkte |
| `docs/superpowers/plans/2026-10-19-phase-6a-entscheidungen.md` | Festlegungen R1 bis R32 der Umsetzung von Plan 6a (je mit Kosten, falls falsch) und die offenen Kleinigkeiten |
| `docs/superpowers/plans/2026-10-20-phase-5a-lastmodell.md` | Plan 5a, 14 Tasks, umgesetzt auf dem Branch `claude/phase-5-plan-s7xe0n` (noch nicht auf `main`, nicht ausgerollt): Wetterarchiv, HA-Sensoren, Merkmale, LightGBM, Backtest, Registry, Aktivierungsregel, Schattenbetrieb, Seite „Prognosen“, Einstellungen „Lastmodell“; die Einordnung teilt Phase 5 in 5a, 5b und 5c; die 17 Entscheidungen des Betreibers vom 20.10.2026, Präzisierungen, Texte |
| `docs/superpowers/plans/2026-10-20-phase-5b-fahrverhalten.md` | Plan 5b, 14 Tasks, umgesetzt auf dem Branch `claude/phase-5b-plan-xo03ty`, seit dem 21.10.2026 auf `main` (Gesamtreview und Fix-Welle erledigt, nicht ausgerollt): Fahrverhalten mit Vorschlägen, Automatik für „Geplant“, Abfahrt an einem Datum und erwartete Autos im Planer; 17 Entscheidungen, vom Betreiber am 20.10.2026 freigegeben |
| `cerbo-watchdog/README.md` | Watchdog: Zustände, Zeiten, D-Bus-Pfade, Installation und Probe |
| `docs/verification/abnahme-phase-1.md` | Checkliste für die Abnahme der Phase 1 |
| `docs/verification/abnahme-phase-4.md` | Checkliste für die Abnahme der Phase 4: Teil Statistik (Plan 4a, 30 Tage ab dem Ausrollen, Abgleich innerhalb ±1 %), „Ziel-SOC“ (mindestens 95 % der machbaren geplanten Ladungen, `GET /api/stats`) und „HA in beide Richtungen“ (Tabelle aller Bedienelemente) aus Plan 4b |
| `docs/verification/abnahme-phase-6a.md` | Checkliste für die Abnahme 6a: Empfangstabelle, Timeout, 7 Tage Dry-Run, im Live-Betrieb Bereich, Schrittregel, Rückkehr auf 0 und Echo; Abfragen auf Entscheidungslog und Influx |
| `docs/verification/abnahme-phase-5a.md` | Checkliste für die Abnahme 5a (Lastmodell): 28 Tage ab dem Ausrollen, Prognosegüte (Modell mindestens 10 % besser oder Profil automatisch aktiv), Verfahren und Grund in der Karte „Lastprognose“, Registry (jede Nacht eine Version), `load-model` ohne Fehler, Rückfall bei fehlender Modelldatei |
| `docs/verification/abnahme-phase-5b.md` | Checkliste für die Abnahme 5b (Fahrverhalten): 28 Tage ab dem Ausrollen, Vorschläge mit Konfidenz auf „Prognosen“, „Laden“ und in HA, Trefferquote je mindestens 80 % bei mindestens 10 bewerteten Tagen, keine Zeile „geändert“ durch die Automatik, erwartetes Auto im Zeitplan und beim Anstecken ersetzt |
| `docs/verification/README.md` | Prüfprotokoll Teil 1, Preise, Teil 2 (`verify_part2`) und Watchdog-Probe: Aufruf und Ergebnis; Verweise auf die Abnahmen der Phasen 1, 4, 5a, 5b und 6a; seit 6a der Punkt `thermal` und `coe_probe` |
| `README.md` | Entwicklung, Web-UI, Demo-Backend |

## Aufbau

- **`src/bindaems/core`**
  - liest die Geräte (Victron über MQTT, EVCS über Modbus, Wall Connector, Tessie,
    Home Assistant)
  - Zustandsspeicher, abgeleitete Größen, Plausibilität, Flüsse je Viertelstunde
  - InfluxDB-Schreiber mit Spool, interne API mit Stream
  - Pakete für Sicherheit und Aktoren (seit 2a und 2b):
    - `safety/`: harte Grenzen, die Regeln 1–7, Rate-Limits, Schreibbudget, Eingangsprüfung,
      Sperren, wirksame Betriebsarten (Regel 8) und die `SafetyLayer`. Sie ist rein.
    - `control/`: Betriebszustände (`operating.py`) und Absicht (`intent.py`, Datei `intent.json`);
      seit 2b die Aktor-Schnittstelle und der Ausführer (`actuator.py`, `executor.py`), der sichere
      Zustand (`safe_state.py`), die Übergabe (`ownership.py`), die Erkennung fremder Änderungen
      (`foreign.py`) und der Aufbau des `SafetyContext` (`context.py`); seit 2c-1 die Laderegelung
      und seit 2c-2a der Akku-Sollwert und der 15-min-Wächter (nächste Punkte). Auch rein.
    - Die Laderegelung in `control/` (seit 2c-1):
      - `phase_guard.py`: Phasenwächter (Ziele je Modus, Last, Verteilung, Überlast)
      - `surplus.py`: Überschuss und seine Verteilung auf die Autos
      - `charge_modes.py`: Lademodi mit Start-, Stopp- und Mindestzeiten
      - `charge_inputs.py`: Eingaben aus Snapshot und abgeleiteten Größen (seit 2c-2b auch `homes`
        aus Home Assistant und `plugged_at`, die Wanduhr der Tessie-Abfrage)
      - `assignment.py`: Zuordnung (wer steht wo, seit 2c-2b mit der Erkennung), Ansteckvorgänge, Boost
        und Übersteuerung
      - `tesla_policy.py`, `charging_tesla.py`: gleitendes Mittel, Weckkontingent, Tesla-Besonderheiten
      - `charging.py`: der `ChargeController` verbindet die Bausteine je Zyklus
      - seit 2c-2a aus `charging.py` ausgelagert: `charge_points.py` (Ladepunkte und Verteilung),
        `charging_tesla.py` (Tesla-Teil), `last_values.py` (Merker der letzten eigenen Werte, die
        Lade- und Akku-Regelung teilen) und `timing.py` (`held_since`, `lasted`)
    - Zuordnungserkennung in `control/` (seit 2c-2b, rein):
      - `clues.py`: die sechs Indizien (`Clue`, `Evidence`, `ClueTracker`) und `home_and_plugged`
      - `detection.py`: die Entscheidung (`Detector`, `Detection`): Kandidat, erkannt, Gast, Widerspruch
      - `fleet.py`: die Autos der Laderegelung, die Fahrzeuge und je Wallbox ein Gast (`fleet`,
        `guest_key`, `car_spec`)
      - `home_signal` und `ha_entities` liegen in `shared/config.py`, denn Laufzeit, Regelung und
        Simulator brauchen sie
    - Akku und Spitzen in `control/` (seit 2c-2a):
      - `regulation.py`: der Regelungs-Kern `Regulation`, die einzige Schnittstelle der Laufzeit zur
        Regelung (Reihenfolge im Zyklus: Spec 7.1, Stand Plan 2c-2a)
      - `battery.py`: Fehlbetrag, Unterstützung, erlaubter Akku-Anteil und der `BatteryController`
        (Override, Totband, Auffrischen, Sperren)
      - `peak_guard.py`: der 15-min-Wächter (Messung, Budget, Drosselung, Start-Sperre, Zustand)
    - `actuators/`: die konkreten Aktoren für EVCS (GX-Dienst `evcharger`), Victron (MQTT) und
      Tesla (Tessie)
    - `persistence/`: atomare JSON-Dateien (mit `fsync` des Verzeichnisses) und das Entscheidungslog
      (`decision_spool.py`)
    - `watchdog_client/`: Watchdog-Status lesen und Heartbeat senden
  - `regulation_link.py` (neben `runtime.py`, seit 2c-2a): die Verdrahtung der Regelung. Sie baut die
    Eingaben, gibt Urteile und Ergebnisse an den Kern zurück, legt Health und Alarme ab und fängt jede
    Ausnahme des Kerns. Sie liegt außerhalb von `core/control` und darf `core.accounting` importieren
    (die Viertelstunde des Wächters).
  - Auf Geräte schreibt der core den Heartbeat, die Sicherwerte im sicheren Zustand und die der
    Übergabe am Ende des Live-Betriebs, seit 2c-1 die Befehle der Laderegelung an EVCS und Tesla und
    seit 2c-2a den Netz-Sollwert-Override an Victron (nur diesen; Entladegrenze und Min-SOC schreibt
    nur der Keeper). Jeder Befehl geht durch die `SafetyLayer`.
- **`src/bindaems/app`**
  - FastAPI mit SQLite (SQLAlchemy Core)
  - Login, Sitzungen, TOTP, CSRF, Rollen
  - Einstellungen mit Versionen
  - Preise (smartENERGY und Referenzquelle, Brutto/Netto-Erkennung)
  - PV-Prognose (Open-Meteo), Abrechnung, Verbraucher, Verlauf aus InfluxDB
  - seit 3a: Tarifrechnung (`tariff/calc.py`), Tagestyp mit Feiertagen (`daytype.py`), Schätzpreise
    (`prices/estimate.py`) und die Prognosen (`forecast/`: Archiv, Kalibrierung, Lastprognose, Güte,
    Routen)
  - seit 3b: die Planung (`planning/`). Rein sind `inputs.py` (Eingangsdaten, Horizont, Restwert,
    Leistungspreis je Monat), `battery_model.py` (Akkumodell im Viertelstundenraster), `optimizer.py`
    (MILP), `intents.py` (Absichten aus den Flüssen), `plausibility.py`, `fallback.py` (Ersatzplan),
    `explain.py` (Grundcodes und Sätze), `builder.py` (Plan aus den Absichten: Nachrechnung, Budget,
    Vergleich mit „Victron allein“) und `triggers.py` (Auslöser). Dazu `store.py` (SQLite),
    `process.py` (Lösen im Kindprozess), `service.py` (der Planungsdienst) und `routes.py`
  - HA MQTT Discovery mit Bedienelementen (`ha/entities.py`, `ha/publisher.py`), den Befehlen aus
    Home Assistant (`ha/commands.py`, seit 2d) und den Benachrichtigungen (`ha/notify.py`, seit 2d)
  - die Bedienung (`control/`, seit 2d): `intent.py` (Absicht aus der app), `decisions.py`
    (Entscheidungslog in SQLite und Markierungen für Grafana), `routes.py`
  - seit 5a: das Lastmodell (`learning/`): `weather.py` (Wetterarchiv), `ha_sensors.py` (Sensoren aus
    der HA-Datenbank), `features.py` (Merkmale und Trainingssatz), `model.py` (LightGBM), `backtest.py`,
    `activation.py` (Aktivierungsregel), rein; dazu `registry.py` (Versionen in SQLite und als
    Dateien), `service.py` (der Lerndienst: Training, Bewertung, Vorhersage, Status) und `routes.py`.
    Die Lastprognose (`forecast/load.py`) gibt Profil und Modell aus und wählt das aktive Verfahren.
  - seit 5b: das Fahrverhalten im selben Paket: `driving_data.py` (Anwesenheit, Ansteckvorgänge und Ladestand
    lesen), `driving_days.py` (Tage), `driving_profile.py` (Profil, Vorschläge, Anwesenheitsprognose),
    `driving_quality.py` (Trefferquote), alle rein bis auf das Lesen; dazu `driving_service.py` (Berechnung,
    Ablage in `driving_profile`, Status, Komponente `driving`) und `driving_auto.py` (die Automatik). Erwartete
    Autos baut `planning/vehicles.py` (`expected_vehicles`).
  - liefert das UI unter `/` aus
- **`src/bindaems/shared`:** Konfiguration, Domänentypen (auch `control.py`: Befehle, Betriebsarten,
  Absicht; `decisions.py`: Entscheidung und ihr deutscher Text; seit 3b `plan.py`: Plan, Slot,
  Akku-Absicht, Grundcodes), Zeit (`AnchoredClock`), Influx-Client.
  **`src/bindaems/tools`:** Prüfprotokoll Teil 1, `watchdog_config`, `watchdog_probe`,
  `verify_part2` (mit dem Paket `part2/`), der Anlagensimulator `sim/` (Modell, Fahrzeuge,
  Lauf, Kennzahlen, Szenarien, Bericht) und seit 3b `export_days`, `scenario_days` und das Paket
  `scenario/` (Tagesdaten, erzeugte Tage, Lauf, Bericht). Die Werkzeuge der Planung importieren die
  Bausteine der app (der Importvertrag verbietet nur core ↔ app/shared).
- **`cerbo-watchdog/`**
  - Python-Dienst für Venus OS (Python ≥ 3.8, nur Standardbibliothek und die Module des Geräts)
  - die Logik (`bindaems_watchdog/logic.py`) ist rein und in der CI getestet, die D-Bus-Anbindung
    (`service.py`) ist dünn
  - `install.sh` installiert, aktualisiert und entfernt per ssh
- **`ui/`**
  - SvelteKit 2 als SPA (Svelte 5 Runes), valibot-Schemas für jede Antwort, ECharts
  - Tests mit Vitest und Playwright
  - Die Vertragsdateien `ui/src/lib/api/contract/*.json` kommen aus der Demo-Welt
    (`tests/ui_world.py`).
- **`deploy/` und CI:**
  - `deploy/`: Dockerfiles, `docker-compose.yml`, `config.example.yaml`, `influxdb-setup.sh`
  - CI `.github/workflows/ci.yml` mit den Jobs `python`, `ui`, `e2e` und `docker`
  - Der Job `python` prüft zusätzlich mypy für das Watchdog-Paket, die Abdeckung (≥ 75 %
    insgesamt, ≥ 90 % für `core/safety`, `core/control` und `core/actuators`) und den
    Importvertrag, der Safety-Schicht und Betriebszustände rein hält.
  - Veröffentlicht wird von `main` und von Tags.

## Befehle

In dieser Cloud-Umgebung brauchen alle `uv`-Aufrufe vorher:
`export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.

```bash
# Python: Format, Lint, Typen (auch das Watchdog-Paket), Importgrenzen, Tests mit Abdeckung
uv run ruff format . && uv run ruff check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports
uv run pytest -q --cov --cov-report=term --cov-fail-under=75
uv run coverage report --include="src/bindaems/core/safety/*,src/bindaems/core/control/*,src/bindaems/core/actuators/*" --fail-under=90

# nur der Cerbo-Watchdog (läuft ohne D-Bus)
uv run pytest tests/unit/cerbo_watchdog -q

# Anlagensimulator: ein Szenario von Hand (Betriebshandbuch, „Simulator“); Rückgabe 0 = alles grün
uv run python -m bindaems.tools.sim --szenario wolkig --out bericht.md

# Szenario-Vergleich mit „Victron allein“ auf erzeugten Tagen (Betriebshandbuch, „Planung“);
# Rückgabe 0 = bestanden; echte Tage: erst export_days, siehe „Offen an der Anlage“
uv run python -m bindaems.tools.scenario_days --synthetisch winter-spread --anzahl 2 --takt 60 --out bericht.md

# UI
cd ui && pnpm check && pnpm lint && pnpm test

# E2E (Playwright startet das Demo-Backend auf 8099 selbst)
cd ui && pnpm build && pnpm e2e

# Vertragsdateien neu schreiben, wenn sich API-Antworten ändern
UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py

# Demo-Backend mit gebautem UI
uv run python -m tests.e2e.ui_server --port 8099 --ui-dir ui/build
```

Demo-Zugänge: `admin`, `bedienen` (Bedienen), `gast` (Lesen) und `sicher` (Admin mit TOTP, Geheimnis
`JBSWY3DPEHPK3PXP`). Das Passwort ist jeweils `demo-passwort-1`.

## Stolpersteine

- **Demo-Uhr:** steht fest auf dem 09.10.2026, 10:00 in Wien, der Browser läuft auf dem echten
  Datum. Deshalb zeigt das Übersicht-Diagramm in der Demo Preise vom 09.10. neben der PV der
  echten Tage. Das betrifft nur die Demo.
- **Demo-Backend beenden:** nicht mit `pkill -f`, das trifft die eigene Shell. Die PID merken
  und gezielt beenden.
- **ECharts in Node (SSR):** endet nicht von selbst; danach `chart.dispose(); process.exit(0)`.
- **Standard-Branch:** Auf GitHub ist das seit dem 12.10.2026 `main`; der frühere Standard-Branch
  `claude/ems-haus-akku-pv-93c0s6` ist vollständig in `main` enthalten. Die CI hängt `latest`
  ausdrücklich an `refs/heads/main`.
- **Cookies:** Die Sitzungs-Cookies tragen „Secure“. Über `http://` klappt die Anmeldung nur mit
  `app.cookie_secure: false`, und das ist nur zum Testen gedacht. Die Anmeldeseite meldet das
  verworfene Cookie.
- **`{#each}` über Texte vom Server:** immer den Index als Schlüssel nehmen. Svelte bricht bei
  doppelten Schlüsseln ab, auch im Produktions-Build.
- **Einstellungen:** Die Schemas sind `looseObject`, weil das UI die Einstellungen vollständig
  zurückschickt. Unbekannte Felder dürfen nicht verloren gehen.
- **Sitzung:**
  - Automatische Abfragen senden `X-Bindaems-Background: 1` und verlängern die Sitzung nicht.
  - Bedienung (Tasten, Antippen) verlängert sie höchstens einmal je Minute.
- **Zeitachsen:** ECharts kennt keine Zeitzonen. Die Marken kommen aus `viennaTicks`
  (`ui/src/lib/time.ts`).
- **Bedienelemente:** mindestens 44 × 44 px; ein E2E-Test misst alle Seiten bei 360 px.
- **Schriften in der CI:** Dort wird `system-ui` zu DejaVu Sans, die deutlich breiter läuft als
  die lokale Schrift. Breitenfehler bei 360 px stellt man so nach (der Pfad muss absolut sein):
  `cd ui && FONTCONFIG_FILE=$PWD/tests/e2e/fonts-ci.conf pnpm e2e`.
- **Watchdog-Paket (Phase 2a):**
  - Es muss unter Python 3.8 laufen (ruff mit `py38` und ein `ast`-Test) und darf nichts aus
    `bindaems` importieren. Die Version steht zweimal: `WATCHDOG_VERSION` in
    `core/watchdog_client/status.py` und `VERSION` in `cerbo-watchdog/bindaems_watchdog/__init__.py`.
    `tests/unit/cerbo_watchdog/test_compat.py` prüft, dass sie gleich sind.
  - Das D-Bus-Gerüst läuft nur auf einem Venus-OS-Gerät. Die Python-Tests prüfen Logik,
    Konfiguration, Zustandsdatei, Aktionen und den Dienst mit Fakes und `install.sh` gegen ein
    nachgebildetes Gerät (Attrappen für ssh, svc und dbus). Der Schritt „Shellcheck“ im Job
    `docker` der CI prüft `install.sh`, `service/run` und `service/log/run` zusammen mit
    `deploy/influxdb-setup.sh`.
  - Jede Änderung an `victron.watchdog`, der USV-Reserve oder einem sicheren Strom in
    `config.yaml` verlangt eine neue `config.ini` auf dem Cerbo (Betriebshandbuch 14). Dazu zählt
    `victron.live_allowed`, denn mit ihm enthält die Datei die Aktion `override_setpoint`. Die Felder
    unter `control` aus 2c-2a gehören nicht dazu.
- **Neue Pakete unter `src/bindaems`** brauchen eine `__init__.py`; sonst sieht import-linter sie
  nicht (`tests/unit/test_layout.py`).
- **Gates für die Safety-Schicht:** `core/safety` und `core/control` dürfen nichts aus
  `core.actuators`, `core.adapters`, `core.api`, `core.runtime`, `core.telemetry`,
  `core.accounting` und `core.checks` importieren, auch nicht über Umwege (import-linter-Vertrag).
  Das gilt auch für `battery.py`, `peak_guard.py`, `regulation.py`, `clues.py`, `detection.py` und
  `fleet.py`. `core/regulation_link.py` liegt
  außerhalb von `core/control`; dort sind diese Importe erlaubt (es nutzt `core.accounting`).
  Der Vertrag prüft nur die Importe. Konvention, nicht geprüft: Zeit kommt als Argument, Dateien nur
  über `core.persistence.files`. Aktoren erreichen die Schicht nur über das Protokoll `Actuator`,
  das Entscheidungslog über `DecisionSink`.
- **Zwei Uhren (2b):** Fristen, Rate-Limits, Schreibbudget, sicherer Zustand, Erkennung fremder
  Änderungen, Heartbeat und Tesla-Fehler laufen auf der `AnchoredClock` (Wanduhr beim Start plus
  monotone Zeit). Messwerte, Viertelstunden und die Zeitpunkte der Entscheidungen bleiben bei der
  Wanduhr. Darum tragen Alarme der Safety-Schicht, des sicheren Zustands, der Aktoren und der
  fremden Änderungen eine andere Zeit als „seit“ als die übrigen (Betriebshandbuch 14). Die
  Laderegelung (2c-1) hält es ebenso: Ihre Zeiten (Timer, Fenster, Überlast, Weckabstand) laufen auf
  der Uhr der Safety-Schicht (`ChargeInputs.now`), `boost_until` und der Kalendertag des
  Weckkontingents (Wiener Zeit) nach der Wanduhr (`ChargeInputs.wall_now`). Der Akku-Sollwert
  (Überbrückung, Auffrischen) läuft ebenso auf der Uhr der Safety-Schicht, der 15-min-Wächter auf der
  Wanduhr, wie die Viertelstunden der Abrechnung; Tests müssen beide Uhren angeben. Die Erkennung
  (2c-2b) hält es wie „unklar“: Die 60 s der Indizien und der Entscheidung laufen auf der Uhr der
  Safety-Schicht, die zeitliche Korrelation, der Beginn der Vorgänge und die Ausnahme für Tessie-Werte
  auf der Wanduhr, denn von ihr kommen die Zeitstempel der Signale.
- **Tests mit respx und Tessie (2b):** Eine Anfrage ohne passende Route fängt der Tesla-Aktor als
  Fehler ab, und respx zeichnet sie nicht auf. Wer prüft, dass nichts gesendet wird, braucht eine
  Catch-all-Route und zählt deren Aufrufe.
- **Tests der Laderegelung (2c-1):** Fehlen die Ströme von EVCS und Wall Connector, gilt jede Phase
  als voll, und kein Auto startet (Design 5.5). Tests müssen die Ströme darum angeben, auch 0 A; nur
  ein fehlender Wert macht eine Phase voll. Die gemeinsamen Vorgaben stehen in
  `tests/unit/core/control/charge_helpers.py`. Die Regelung sendet nur Änderungen; wer einem Aktor
  Befehle gibt, ohne dass sie es sieht, ruft `ChargeController.forget` auf (die Laufzeit tut das für
  Keeper, Übergabe, fremde Systeme und den Wechsel der Betriebsart).
- **Tests mit der EVCS-Hysterese:** Die EVCS hält ihren zuletzt angeforderten Strom, bis der Anteil
  ihn um 1 A plus `control.evcs_hysteresis_a` (0,5 A) übersteigt. Ein Test, der eine Erhöhung an
  der EVCS erwartet, braucht also einen Anteil ab Sollwert + 1,5 A oder setzt die Hysterese auf 0
  (`with_control` in `tests/unit/core/control/test_charging.py`). Senkungen wirken sofort.
- **Tests der Erkennung (2c-2b):** Die Erkennung braucht 60 s ohne Unterbrechung, und die Phasen-Signatur
  und der Stromvergleich brauchen vorher 60 s Laden: Ein Test, der „erkannt“ erwartet, läuft mehr als
  120 s. Das Bild „Tesla an der EVCS“ (Tessie: zu Hause und angesteckt, Wall Connector ohne Fahrzeug,
  EVCS mit Fahrzeug) ist ein echter Widerspruch nur, wenn die EVCS auf zwei Phasen lädt:
  `CONTRADICTION` in `tests/unit/core/control/charge_helpers.py`, unklar ab `UNCLEAR_FROM_S` (120 s).
  Ein e-Golf an einer EVCS, die auf drei Phasen lädt, ergibt widersprüchliche Eingaben (die
  Phasen-Signatur schließt ihn aus). Tessie-Werte brauchen `plugged_at` (die Wanduhr der Abfrage), und
  Home Assistant kommt über `homes`; ohne Angabe sagt es über kein Fahrzeug etwas. Laufen an beiden
  Wallboxen Vorgänge, stützen Tessie und Home Assistant erst, wenn Strom oder Phasen das Fahrzeug an
  der anderen Wallbox ausschließen (Elimination), also frühestens nach 60 s Laden dort; vorher
  schließen sie nur aus. Ein Test, der dann eine Erkennung erwartet, lässt die andere Wallbox auf
  einer anderen Phasenzahl oder mit anderem Strom laden oder stützt das Fahrzeug über Strom und
  Phasen.
- **Simulator (2c-1, 2c-2a, 2c-2b, 2d):** Er nutzt die echten Aktoren und die Parser der Adapter. Ändert man deren
  Zeiten, Texte oder Signale, ändern sich die Szenarien mit. Alle achtundzwanzig Szenarien laufen in
  `tests/integration/test_sim_scenarios.py`, dazu eine Abwandlung von `spitze-15min` für den
  Boost-Anteil des Wächters; je etwa 3 bis 13 s, zusammen rund 3,5 min (gemessen auf
  der Entwicklungsmaschine); der Job `python` der CI braucht damit 7 bis 11 min, der ganze Ablauf
  mit UI, E2E und Docker 9 bis 12 min (Läufe vom 14. und 15.10.2026). Die Szenarien sind
  deterministisch (feste Saat).
- **Dokumentationstests (2c-1, 2c-2a, 2c-2b, 2d):** `tests/unit/test_docs.py` prüft die JSON-Beispiele, die Tabellen
  der Gründe und die Optionen und Szenarien des Simulators im Betriebshandbuch gegen den Code, im
  Abschnitt „Akku und Spitzen“ außerdem die dokumentierten Standardwerte (die Tabelle der Einstellungen
  gegen `ControlConfig`) und die Felder von Health (das Beispiel gegen `health()` von Akku-Sollwert und
  Wächter). Seit 2c-2b prüft er auch den Ausschnitt von Health zu den Wallboxen (Felder und Werte gegen
  `wallbox_health()`, `AssignmentKind` und `Clue`), dass das Handbuch jede Art der Zuordnung und jedes
  Indiz nennt, und den Beispielsatz zu `assignment_detected` gegen `decision_text`. Ändert man die
  Absicht, einen Grund-Text, die Optionen von `bindaems.tools.sim`, einen dokumentierten Standardwert
  oder die Felder von Health, schlägt er an, bis das Handbuch stimmt. Die Werte der Beispiele prüft er
  nicht, die Zustände von Health nur gegen eine feste Liste im Test, und eine neue Einstellung, die in
  seiner Liste `BATTERY_SETTINGS` fehlt, bemerkt er nicht. Seit 2d kennt `CHARGING_REASONS` auch
  `self_start`, `BATTERY_SETTINGS` die drei Einstellungen `tesla_reserve_commands`,
  `tesla_against_s` und `tesla_against_retry_s`, und ein Test verlangt, dass Abschnitt 12 des
  Handbuchs „Laden“, „Entscheidungen“, „Warum?“ und „Bedienen“ nennt und Abschnitt 7 die
  Bedienelemente (Lademodus, Regelbetrieb, Boost, Zuordnung, `notify_service`). Die Tabelle der
  Szenarien prüft er gegen `SCENARIOS`; die Zahl im Text (achtundzwanzig) prüft er mit.

## Stand der Installation beim Betreiber

- Docker auf einer VM im LAN des Betreibers, Images vom Stand nach Plan 4a (`fb23bd0`, laut Betreiber
  ausgerollt am 18.10.2026; davor 2c-1 bis 3b am 17.10.2026 mit `6a6904a` und der Stand nach 2b vom
  12.10.2026). Plan 4b ist nicht ausgerollt (erst nach der Abnahme der Phase 3); bis dahin kein
  `docker compose pull`, denn `latest` enthält seit dem 19.10.2026 Plan 4b, seit dem 20.10.2026 auch
  6a, 3d und 5a und seit dem 21.10.2026 5b; alle rollen gemeinsam aus. Laut Betreiber
  sind die Schritte zum Ausrollen erledigt (HA-Token, Mosquitto-ACL, `config.yaml`) und Leistungspreis
  und Planung eingerichtet.
- Wallbox (`wallbox.evcs`) und Tesla laut Betreiber seit dem 17.10.2026 **live**, evcc abgeschaltet;
  der Dry-Run neben evcc ist dabei übersprungen worden. Die Zuordnung der Autos hat der Betreiber am
  19.10.2026 in mehreren Konstellationen im Live-Betrieb geprüft, ohne Befund. Offen ist Teil 2
  „Verbindung unterbrochen“ für die Wallbox. Victron im Dry-Run (DESS an).
- Reverse Proxy mit HTTPS sowie Tarif und OeMAG-Wert im UI: laut Betreiber eingerichtet
  (11.10.2026). Zum Proxy gehören laut Betriebshandbuch 6: Port 8080 erreicht nur der Proxy,
  `app.trusted_proxies` ist gesetzt, `cookie_secure` steht wieder auf `true`.
- **Prüfprotokolle** in `docs/verification/`, Bewertungen jeweils am Ende des Protokolls:
  - Preise am 10.10.2026 bestanden (smartENERGY brutto).
  - Teil 1 am 10.10.2026 mit Befunden, am 11.10.2026 mit erreichbarer EVCS wiederholt.
  - Watchdog-Probe (Teil 2, Punkt 17.2-5) am 11.10.2026 bestanden, am 12.10.2026 mit 1.1.0
    erneut bestanden.
  - Teil 2 EVCS am 12.10.2026 bestanden: Schreibweg über den GX-Dienst `evcharger` bestätigt,
    fremde Änderung erkannt, Watchdog setzt den sicheren Strom. Offen ist „Verbindung
    unterbrochen“.
  - Teil 2 Victron am 12.10.2026 mit vorübergehend abgeschaltetem DESS: Override setzen und mit
    `null` aufheben bestätigt, Ruhezustand `null`, Entladesperre und Netzladen wirken. Neu: Der
    Override verschwindet nach 5 min ohne Auffrischen. Am Abend in einem zweiten Lauf 17.2-8
    „Importlimit“ bestanden: Mit einer Last, die eine Phase über 20 A gebracht hätte, blieb L3 bei
    höchstens 19,4 A, der Akku lieferte dabei 3072 W. Ein Vorlauf mit dem Multi auf „Nur Laden“
    (charger only) ergab 33,3 A auf L3 ohne Beitrag des Akkus: In diesem Modus wirkt das
    Importlimit nicht. Offen ist 17.2-4.
  - Teil 2 Tesla am 12.10.2026 bestanden (drei Läufe und Schlaf-Test): Das Auto folgt
    Strombefehlen über Tessie, „Laden aus und an“ wirkt, ein schlafendes Auto wird nicht geweckt
    (10 min Schlaf-Test). Ein erster Befehl auf 5 A wirkt über Tessie nicht, obwohl Tessie ihn
    bestätigt; der zweite wirkt (Versuch im Nachtrag zum zweiten Bericht). Seit dem dritten Lauf
    erkennt der Aktor das im Tessie-Cache, und das Werkzeug wiederholt den Befehl: „Verhalten bei
    5 A“ bestanden, der Wall Connector war 4 s nach der Wiederholung bei 5 A. „Fahrzeug nicht zu
    Hause.“ im ersten Lauf kam von einer falschen `site.location`, die der Betreiber korrigiert
    hat.
- **Befunde:**
  - Dynamic ESS bleibt nach Entscheidung des Betreibers an, bis Victron live geschaltet wird. Für den
    Dry-Run des Victron-Teils schaltet er es in einem Fenster ab, das er bestimmt (Design 2c-2, 11.1).
  - Die EVCS (AC22NS, Produkt-ID 0xC026) liest der Cerbo per Modbus TCP unter ihrer
    LAN-Adresse. BindaEMS bekam unter derselben Adresse zuerst nur Nullen. Ursache war die
    IP-Whitelist der EVCS: Die Adresse der VM stand nicht darauf. Die EVCS nimmt die Verbindung
    dann trotzdem an und liefert nur Nullen. Seit der Betreiber die Whitelist am 11.10.2026
    korrigiert hat, liest BindaEMS ihre Register selbst (Firmware v2.09, manueller Modus).
  - Seit dem 10.10.2026 folgt die Registerbelegung Victrons GX-Treiber: Sollstrom 5016,
    Maximalstrom 5017. Ohne bekannte Produkt-ID gelten die Registerwerte als ungültig; Strom
    und Modus kommen dann vom Cerbo.
  - **Peak Shaving:** Importlimit 20 A je Phase mit „immer“ (`Settings/CGwacs/AcInputLimit`,
    `AlwaysPeakShave` = 1). Das ist gewollt (Entscheidung des Betreibers vom 11.10.2026), also
    deutlich unter der 35-A-Sicherung. Bezug über 20 A je Phase deckt der Akku.
  - Akku 96 kWh nutzbar.
- **Watchdog:** Version 1.1.0 am Cerbo installiert (12.10.2026), Probe am selben Tag bestanden.
  Er wartet, solange der core nichts braucht (kein Aktor im Regelbetrieb live).
- **HA-Daten in InfluxDB:** Datenbank `homeassistant`, `bindaems` hat READ. HA schreibt mit
  `measurement_attr: entity_id`:
  - Messung = Entität (`sensor.…`), Tags `domain` und `entity_id` (ohne Domain)
  - Felder `value`, `unit_of_measurement_str`, `device_class_str`, `state_class_str`,
    `friendly_name_str`
  - Wichtig auch für die Lastprognose aus der HA-Historie.
- **Offen an der Anlage, als Nächstes (nach Plan 4a):**
  1. Die Images ausrollen: `docker compose pull && docker compose up -d` (Betriebshandbuch 4).
     **Erledigt:** 2c-1 bis 3b am 17.10.2026 (`6a6904a`), 3c und 4a am 18.10.2026 (`fb23bd0`). Weil
     Wallbox und Tesla live sind, vor jedem Update des core den Regelbetrieb aus und danach wieder an.
     **Zu 2d gehört:**
     - Die Migration der app (`0007`, Tabelle `decision`) läuft beim Start der app von selbst. Die
       Entscheidungen der letzten 7 Tage holt die app dann aus den Dateien des core nach.
     - `BINDAEMS_HA_TOKEN` braucht jetzt auch die app (für die Benachrichtigungen). Die `.env` gilt
       schon für beide Dienste; sie hat den Token wegen `home_entity` ohnehin. Ohne ihn meldet die
       Komponente `ha-notify` unter **System** „Benachrichtigungen aus: BINDAEMS_HA_TOKEN fehlt.“
     - Optional `homeassistant.notify_service` in `config.yaml` (etwa `notify.mobile_app_handy`) für
       einen Push auf das Handy; die persistente Meldung in Home Assistant gibt es ohne ihn.
     - Die ACL des Mosquitto-Benutzers `bindaems` braucht Schreibrechte auf
       `homeassistant/select/bindaems/#`, `…/switch/…` und `…/button/…` und das Leserecht
       `bindaems/cmd/#` (Betriebshandbuch 7); sonst bleiben die Bedienelemente aus.
     - Ein Benutzer mit der Rolle „Bedienen“ ist für Home Assistant nicht nötig; HA hat die Rechte
       dieser Rolle ohne Benutzer. Für Menschen, die nur bedienen sollen, legst du einen an.
     - Neue Felder mit Standardwerten: `control.tesla_reserve_commands`, `control.tesla_against_s`,
       `control.tesla_against_retry_s`, `app.decisions_keep_days`, `homeassistant.notify_service`. Die
       Konfiguration prüft jetzt Fahrzeugnamen (nicht leer, nicht mit Leerzeichen am Rand, ohne
       Beachtung der Schreibweise eindeutig, nicht „Automatisch“) und die Objekt-IDs in Home Assistant;
       prüfe die `config.yaml` anhand von `deploy/config.example.yaml`. Der Watchdog bleibt
       unverändert, und keines der neuen Felder verlangt eine neue `config.ini`.
     **Zu 3a gehört** (Betriebshandbuch, „Prognosen“):
     - Die Migration der app (`0008`, Tabellen `forecast_record` und `forecast_metric`) läuft beim Start
       der app von selbst.
     - Neu und optional: `homeassistant.load_history` mit `entity` und `subtract` (verlangt
       `influxdb.ha_database`, sonst lehnt die app die Konfiguration beim Start ab); Muster in
       `deploy/config.example.yaml` und im Betriebshandbuch, „Prognosen“. Ohne sie rechnet die
       Lastprognose nur aus der Abrechnung.
     - Den Leistungspreis trägst du im Tarif-Editor (Einstellungen) ein, mit der Vorlage
       „Leistungspreis (ElWG)“ (Stufe 10 kW, Mindestverrechnung 2 kW, gültig ab 1.1.2027). Die Sätze
       stehen nicht vor; sie kommen aus dem Preisblatt des Netzbetreibers. Leistungspreis und Gebühren
       fließen erst ab 3b und 3c in etwas ein.
     - **Risiken an der Anlage prüfen** (Betriebshandbuch, „Prognosen“, „An der Anlage prüfen“ nennt die
       Schritte):
       - Die InfluxQL-Abfrage der HA-Historie ist nicht gegen die echte InfluxDB geprüft: `fill(previous)`
         auf dem Einheitenfeld und die 56-Tage-Abfrage in Minutenauflösung gegen die 15 s Zeitlimit und die
         Höchstzahl an Zeilen. Eine scheiternde Abfrage wiederholt die app stündlich; im Log steht dann
         „Lastverlauf aus Home Assistant nicht lesbar“.
       - Die Strahlung von Open-Meteo in den Tests ist künstlich erzeugt (kein Mitschnitt); ob Klarheit
         und Klassen an der Anlage sinnvoll ausfallen, zeigt erst der Betrieb.
       - Die PV-Kalibrierung lernt auch Abregelung (Einspeisegrenze, voller Akku) als niedrigeren
         Faktor.
     - **Hauslast aus Home Assistant:** entschieden am 16.10.2026: ohne. In HA gibt es keine Entität
       „nur Hauslast“, und `homeassistant.load_history` bleibt aus. Die Lastprognose lernt nur aus der
       Abrechnung; sie ist in den ersten Wochen ungenauer, trägt nach etwa 3 Wochen und ist nach 8 Wochen
       voll.
     **Zu 3b gehört** (Betriebshandbuch, „Planung“):
     - Die Migration der app (`0009`, Tabellen `plan` und `plan_inputs`) läuft beim Start der app von
       selbst. Das Image der app bekommt `pulp` und `highspy` (mit `numpy`) dazu und wird größer.
     - Neue Einstellungen „Planung“ (Admin, im Editor der Einstellungen) mit Standardwerten: Netzladen
       erlaubt aus, Marge 2,0 ct, Roundtrip 88 %, Verschleiß 2,0 ct, Restwertfaktor 0,9, Trägheit
       0,5 ct. Neue Felder in `config.yaml` gibt es nicht, und der Watchdog bleibt unverändert.
     - Der Leistungspreis, den du für 3a einträgst, geht seit 3b in das Ziel, die erwarteten Kosten und
       die Budget-Vorschau des Plans ein; ausgeführt wird er erst mit 3c. Ohne Satz im Monat bleibt er
       aus dem Ziel.
     - In HA erscheinen die vier Plan-Sensoren, der Schalter „Netzladen erlaubt“ und die Taste „Neu
       planen“; die ACL des Mosquitto-Benutzers braucht dafür dieselben Rechte wie für die
       Bedienelemente aus 2d. Mit 3c tragen die Sensoren `execution` statt `preview: true`.
     **Zu 3c gehört** (Betriebshandbuch, „Akku und Spitzen“ und „Planung“):
     - Keine Migration, keine neuen Felder in `config.yaml`, der Watchdog bleibt unverändert, und es
       braucht keine neue `config.ini`. Gespeicherte Absichten laden mit den neuen Standardwerten
       (Akku-Unterstützung erlaubt, Schwelle aus `control.support_soc_pct`, kein Plan).
     - **ACL:** Die Unterstützungsschwelle ist die erste Zahl (`number`) in HA. Der Mosquitto-Benutzer
       `bindaems` braucht dafür `topic write homeassistant/number/bindaems/#` (Betriebshandbuch 7);
       ohne die Zeile fehlt die Entität in HA. Der Schalter „Akku-Unterstützung erlaubt“ braucht nichts
       Neues.
     - Die app übergibt den Plan von selbst, sobald sie mit dem core verbunden ist und sein Health hat
       (Betriebshandbuch, „Planung“, „Übergabe an den core“). Mit eingeschaltetem Regelbetrieb rechnet
       der core ihn im Dry-Run mit; Victron bleibt im Dry-Run, bis ein Admin live schaltet (Punkt 4).
       Ohne Regelbetrieb steht im Zeitplan „Regelbetrieb ist aus: Der core führt den Plan nicht aus.“
     - Mit einem Leistungspreis mit Satz (frühestens 2027) hält der 15-min-Wächter das Budget des Plans,
       auch ohne `control.peak_target_kw`; vorher ändert sich am Wächter nichts.
     Aus den Plänen davor: Die neuen Felder `grid.import_limit_a`, `control` (seit der Hysterese auch
     `control.evcs_hysteresis_a`, seit 2c-2a `support_soc_pct`, `support_hysteresis_pct`,
     `support_max_w`, `support_max_s`, `setpoint_deadband_w`, `override_refresh_s`, `peak_target_kw`
     und `peak_margin_kw`), `vehicles.<name>.wake` und seit 2c-2b `vehicles.<name>.home_entity` haben
     Standardwerte (`home_entity`: keiner); prüfe sie anhand von `deploy/config.example.yaml`. Für den
     e-Golf trägst du `home_entity` mit dem `device_tracker` aus Home Assistant ein; das verlangt den
     Abschnitt `homeassistant` in `config.yaml` und `BINDAEMS_HA_TOKEN` in der `.env`
     (Betriebshandbuch, „Zuordnung Auto und Wallbox“). Der Watchdog bleibt unverändert (1.1.0), und
     keines der neuen Felder verlangt eine neue `config.ini`. Der 15-min-Wächter ruht ohne
     `control.peak_target_kw`, solange kein Plan ein Budget bringt.
  2. **Übersprungen:** Wallbox und Tesla sind seit dem 17.10.2026 live, die Zuordnung ist am
     19.10.2026 im Live-Betrieb geprüft. Ursprünglich: Den Dry-Run neben evcc beginnen: den Regelbetrieb im UI einschalten (Karte „Bedienung“ unter
     **System** oder Seite „Laden“, als „Bedienen“ oder Admin); alle Aktoren bleiben im Dry-Run
     (Betriebshandbuch, „Laderegelung“; ohne app eine Absicht mit `"control": true` per
     `PUT /v1/intent`). Der core sendet dabei nichts auf die Geräte, und die Teil-2-Werkzeuge
     schreiben nicht mit: Sie schreiben nur, wenn du sie aufrufst. Die Entscheidungen sichtet der
     Betreiber auf der Seite „Entscheidungen“ und in Grafana (Betriebshandbuch 13, „Entscheidungslog“
     mit der Abfrage für die Markierungen).
     - Die EVCS-Hysterese ist eingebaut (14.10.2026). Ohne sie hätte der Dry-Run mit rund 100
       EVCS-Entscheidungen je Stunde PV-Laden begonnen; im Simulator sind es jetzt 3 bei ruhiger
       Sonne und rund 40 unter wechselnden Wolken.
     - Das Stundenbudget des Tesla kappt den Vergleich bei Wolken (Punkt 1 unten); seit der
       Fix-Welle steht das als „zurückgestellt“ im Log.
     - **An der Anlage im Dry-Run prüfen:** welcher EVCS-Status bei angestecktem Auto auf
       „Laden aus“ (`StartStop` = 0) folgt (ist es 3, bliebe ein Auto in PV nach dem ersten Stopp
       auf „geladen“ stehen, bis es abgesteckt wird); welche Phasenzahl Tessie (`charger_phases`)
       meldet, während der Tesla dreiphasig lädt (weicht sie von `vehicles.tesla.phases` ab, gibt es
       für ihn keinen Stromvergleich); wie oft die EVCS bei ruhiger Sonne ihren Strom ändert
       (deutlich über 10 Entscheidungen je Stunde heißt: Das Rauschen an der Anlage ist größer als
       die Hysterese, `control.evcs_hysteresis_a` anheben).
     - **Vor der Live-Schaltung von EVCS und Tesla im Dry-Run bestanden** (Betriebshandbuch,
       „Hinweise für die Anlage“; die Indizien stehen in Health unter `wallboxes`):
       - Verlässt der Tesla den Wall Connector, während der e-Golf an der EVCS steckt und nicht
         lädt, bleibt die EVCS „e-Golf, angenommen“, der Tesla wird dort nicht erkannt, und nach
         ≤ 60 s schließt Tessie ihn aus (Tessie meldet `plugged` bis zu 60 s veraltet).
       - Lädt der Tesla am Wall Connector und ändert die Regelung seinen Strom, erscheint er dort
         nie als Gast und die Zuordnung nie als unklar, und `current` schließt ihn dort nie aus.
       - Ein Gast an der EVCS bleibt nach Stromänderungen erkannt und wechselt nicht zum e-Golf: Der
         core leitet die Phasenzahl der EVCS aus Leistung und Strom ab, die nicht gleichzeitig
         gemessen werden, und ein abweichender Zyklus startet die 60 s der Phasen-Signatur neu.
     - **Inbetriebnahme Punkt 3** (Design 2c-2, 11.1; Betriebshandbuch, „Zuordnung Auto und Wallbox“,
       „Inbetriebnahme“): Die Erkennung läuft im Dry-Run mit, und `GET /v1/health` zeigt unter
       `wallboxes`, wer wo steht (dafür muss der core regeln: Regelbetrieb an, sonst ruht die
       Regelung). Prüfe, ob an jeder Wallbox mit Auto das richtige steht, ob „unklar“ ausbleibt, wie
       schnell `home_entity` das Ankommen des e-Golf meldet (steht nach dem Anstecken zuerst ein
       „Gast, angenommen“ an der EVCS, ist der Tracker zu spät) und ob ein Tesla an der EVCS, ein
       e-Golf am Wall Connector und ein Gast erkannt werden (rechne mit einigen Minuten: 60 s
       Kandidatur, und Tessie meldet ein neu angestecktes Auto bis zu 10 min verspätet).
  3. EVCS-Teil-2 „Verbindung unterbrochen“ und Victron 17.2-4 nachholen (17.2-4 ist Voraussetzung der
     Live-Schaltung von Victron, Punkt 4):
     - EVCS: den Schritt „Verbindung unterbrochen“ (Punkt `evcs` erneut, die Rückfrage mit `j`
       beantworten und das Netzwerkkabel der EVCS ziehen, wenn das Werkzeug es verlangt).
     - Victron: 17.2-4 „Kein Akku ins Auto“, während ein Auto an der EVCS lädt (die übrigen
       Rückfragen mit `n`). Dieser Punkt kommt vor Punkt 4: Dynamic ESS dafür vorübergehend abschalten
       (Entscheidung des Betreibers vom 12.10.2026) und danach wieder einschalten; erst ab dem Dry-Run
       des Victron-Teils (Punkt 4.1) bleibt DESS aus. Der Multi muss dabei auf „Ein“ stehen.
     - Die Berichte nach `docs/verification/` übernehmen.
     - Das verstellt die EVCS (Punkt `evcs`: Strom und „Laden an/aus“) und den Netz-Sollwert und
       damit den Akku (17.2-4 schreibt nur den Override), während evcc die EVCS regelt; den Tesla
       berührt keiner der Schritte. Notiere die Zeit, damit du die Entscheidungen dieser Zeit beim
       Vergleich mit evcc ausnimmst.
  4. **Victron in den Dry-Run und live, mit 2 Wochen Dry-Run der Planausführung** (Design 2c-2, 11.1;
     Plan 3c, Entscheidung 14; Betriebshandbuch, „Akku und Spitzen“, „Victron in den Dry-Run und
     live“), in dieser Reihenfolge (nach dem Ausrollen, Punkt 1, bei dem der core neu startet):
     1. **DESS aus für den Dry-Run,** für die ganzen 2 Wochen: Solange DESS an ist, ist Victron
        gesperrt, der Akku-Sollwert sendet auch im Dry-Run nichts, und der Zeitplan zeigt „Victron ist
        gesperrt: Der core führt den Plan nicht aus.“ In diesen 2 Wochen läuft der Akku ohne
        Preisoptimierung (Eigenverbrauch des ESS), denn im Dry-Run stellt auch der Plan nichts.
     2. **Plan beim core prüfen:** Regelbetrieb an; unter **System** meldet `planning` „Plan von HH:MM
        Uhr“, der core steht in `CONTROL` (oder `DEGRADED`), Victron ist im Dry-Run ohne Sperre, und
        die oberste Zeile der Seite **Zeitplan** lautet „Dry-Run: Der core rechnet den Plan mit und
        sendet nichts an Victron.“ (`GET /v1/health`: `plan.state` `valid`).
     3. **2 Wochen beobachten:** die Entscheidungen an `victron` mit `plan_hold` und `plan_grid_charge`
        (passen Zeit und Absicht zum „Ablauf“ des Plans?), ihre Zahl je Stunde (im Simulator,
        `plan-halten`, höchstens 40; die Abfrage steht im Betriebshandbuch) und die Karte „Plan gegen
        Ist (heute)“ im Zeitplan. „Netzladen erlaubt“ bleibt aus, bis der Ablauf plausibel ist (Punkt
        5.2); erst dann erscheinen Entscheidungen mit `plan_grid_charge`. Kein Befund sind: bei
        `HALTEN` bis rund 0,05 kWh Entladung je Viertelstunde (Totband) und, wenn Haus und Autos mehr
        als `victron.max_grid_charge_setpoint_w` wollen, der Rest aus dem Akku (Regel 2).
     4. **Im Dry-Run beobachten, auch ohne Plan:** wie viele Entscheidungen an `victron` je Stunde entstehen (im
        Simulator rund 67 unter schnell wechselnden Wolken, `akku-wolkig`), ob das Rauschen der
        Hauslast das Totband von 200 W übersteigt, und **ob der Wächter bei kleinen Senkungen
        nachdrosselt** (Entscheidungen mit `peak_guard`). Die Drosselung hat kein Totband: Im Modell
        gab es kein Nachdrosseln aus Rauschen, seit der Nachrunde aber eines aus dem alten Mittel
        (Boost-Abwandlung von `spitze-15min`, unter „Bewusst offen gelassen“). Nach einer Drosselung
        klingt das alte 30-s-Mittel genau mit der Wartezeit von 30 s ab (`SETTLE_S`, eine Sekunde
        Abstand), und das Modell hat kein Netzrauschen. An der Anlage könnten kleine Nachdrosselungen
        und eine verzögerte Aufhebung entstehen, die Marge `peak_margin_kw` fängt sie ab. Dann
        entscheidet der Betreiber, ob ein Totband in die Drosselung kommt (etwa eine Senkung unter
        100 W ignorieren). Stufen sind seit der Nachrunde nicht gewollt: Wollen die Autos mehr als
        die feste Grenze von Regel 2 und liegt `P_zul` darunter, senkt der Wächter mit Budget einmal
        um den Unterschied (in `spitze-15min` rund 300 W, 2 Entscheidungen mit `peak_guard` an der
        EVCS in 4440 Schritten: der e-Golf in der zweiten und dritten Viertelstunde je einmal von 16
        auf 15 A). Mehrere Entscheidungen mit `peak_guard` in kurzer Folge an derselben Wallbox wären
        also ein Befund.
     5. **Prüfprotokoll Teil 2, Victron 17.2-4 „Kein Akku ins Auto“,** falls es nicht schon unter
        Punkt 3 bestanden ist; DESS ist hier schon aus. Es prüft genau den Mechanismus des
        Akku-Sollwerts.
     6. **`victron.live_allowed: true`** in `config.yaml`, den core neu starten, die `config.ini`
        neu erzeugen und mit `install.sh` übertragen. Die Override-Aktion steht nur in einer
        `config.ini`, die mit der Freigabe erzeugt wurde; die Probe braucht sie, deshalb kommt die
        Freigabe vor der Probe (Design 2c-2, 11.1 nennt sie danach). Die Freigabe allein schaltet
        Victron nicht live.
     7. **Watchdog-Probe mit vorher verstellten Victron-Werten:** Heute verstellt nur der Punkt
        `evcs` von `verify_part2` etwas, und zwar den EVCS-Strom. Min-SOC, Entladegrenze und
        Override verstellt man vorher von Hand und prüft nach der Probe, dass die Auslösung sie
        zurückgesetzt hat.
     8. **Victron live schalten,** nach den 2 Wochen und mit den vier Bedingungen aus Spec 8.7
        (`live_allowed`, Teil 2 bestanden, Watchdog scharf, Admin im UI mit Bestätigung): im UI mit
        dem Dialog „Live schalten“ oder per Absicht (`"actuators": {"victron": "live"}`). Ab dann
        stellt der Plan den Akku („Der core führt den Plan aus.“). DESS bleibt danach aus; schaltet
        man es wieder ein, sperrt sich Victron, und der Override verfällt nach 300 s.
  5. **Den Plan beobachten und die 30 echten Tage prüfen** (nach dem Ausrollen, Punkt 1, parallel zum
     Dry-Run der Planausführung, Punkt 4; Betriebshandbuch, „Planung“, „An der Anlage prüfen“ und
     „Szenarien“). Solange Victron im Dry-Run ist, stellt der Plan nichts am Akku; die Schritte sind
     lesend:
     1. **Plan beobachten:** Meldet die Komponente `planning` unter **System** „Plan von HH:MM
        Uhr“ und nicht „Ersatzplan von … : Solver fehlgeschlagen: …“? Das Log nennt die Rechenzeit
        (`docker compose logs --since 1h ems-app | grep "Plan erstellt"`); nahe 20 s stößt das
        Zeitlimit an. Stimmen Preise und Prognosen der Seite **Zeitplan** mit der Übersicht überein,
        und sind Ablauf und Blöcke plausibel? Springt die Zeit im Kopf jede Viertelstunde?
     2. **Netzladen erlaubt** bleibt aus, bis dir der Ablauf plausibel erscheint. Eingeschaltet darf
        kein Block „Netzladen“ bis in einen Zeitraum reichen, in dem laut PV-Prognose eingespeist
        wird, ohne dass der Akku dazwischen auf der Reserve ankommt (Regel R, seit Plan 3d).
     3. **30 echte Tage:** Sobald das Prognose-Archiv gefüllt ist, erst `export_days`, dann
        `scenario_days --takt 15` (Befehle im Betriebshandbuch, „Szenarien“). „Bestanden“ über
        mindestens 30 Tage ist mit den 2 Wochen Dry-Run (Punkt 4) die Voraussetzung der Abnahme der
        Phase 3. Ein Befund dort ändert den Planer, nicht die Ausführung. Die Dateien
        liegen unter `/data` und gehören nie ins Repository.
     4. **Risiken, die erst die Anlage zeigt:**
        - Die erzeugten Tage der CI sind einfacher als echte: Der Prognosefehler je Stunde ist für alle
          Horizontklassen gleich, und die Prognosen sind sauberer als Open-Meteo und die Lastprognose.
          Die 30 echten Tage über `export_days` und `scenario_days` sind die eigentliche Prüfung der
          Abnahme der Phase 3.
        - Ein übersprungener Tag mitten im Zeitraum (keine Preise, zu viele Lücken) lässt eine Lücke:
          SOC und Monatsspitze laufen über sie hinweg, auf beiden Seiten gleich, und der höchste Bezug
          des fehlenden Tages fehlt im Leistungspreis. Das Ergebnis nach der Lücke ist nur eine
          Näherung. Der Bericht nennt die Tage unter der Tabelle („Übersprungene Tage: …“).
        - `export_days` ist bisher nur gegen eine Test-Datenbank gelaufen, nicht gegen die echte
          Datenbank der Anlage. Rechne mit einem ersten Lauf, der Lücken oder Fehler zeigt.
        - Speicher: rund 80 KB je Plan mit Eingangsdaten, bei 100 bis 200 Läufen am Tag rund 60 bis
          115 MB über die 7 Tage mit Eingangsdaten; gekürzte Pläne danach nur noch wenige KB. SQLite
          gibt gelöschten Platz nicht von selbst ans Dateisystem zurück, die Datei wächst bis zu
          diesem Stand und bleibt dann etwa so groß.
        - Der Optimierer kann bei negativen Preisen PV-Einspeisung planen, obwohl der Akku noch Platz
          hat. Das ist selten; die Plausibilitätsprüfung verwirft den Plan dann, und der Ersatzplan
          gilt.
        - Jeder Lauf startet einen frischen Kindprozess (`spawn`) und zahlt dessen Startzeit mit
          dem Import von PuLP und HiGHS. Das ist der Preis für den Schutz vor einem hängenden Solver;
          bei 100 bis 200 Läufen am Tag harmlos, an einer schwachen VM aber zu beobachten.
        - Der Test `test_app_runtime::test_runtime_starts_and_stops_cleanly` ist in der Entwicklung
          einmal gescheitert (`last_attempt` war `None`), einzeln und in allen späteren Läufen
          grün. Scheitert er in der CI, ist das ein bekannter Wackler und kein neuer Fehler.
  6. **Statistik (Plan 4a) ausrollen und die Abnahme beginnen** (ausgerollt laut Betreiber am
     18.10.2026, Abnahme 18.10. bis 16.11.2026; Betriebshandbuch, Abschnitt 8,
     „Statistik ausrollen (Plan 4a)“). Plan 4a braucht nur `ems-app`: Der core bleibt, der Regelbetrieb
     bleibt an, und die Aktoren behalten ihre Betriebsart. Er hängt nicht an den Punkten 1 bis 5 und kann
     vor ihnen, mit ihnen (ein `docker compose pull && docker compose up -d` bringt alles zugleich) oder
     danach kommen; mit Punkt 1 zusammen gibt es einen Neustart der app weniger. Die Images von GHCR
     gibt es erst, wenn der Branch auf `main` ist (Integration); ein lokaler Build geht
     auch vom Arbeitszweig.
     1. Vorher die Datenbank kopieren (Betriebshandbuch 8, „Statistik ausrollen (Plan 4a)“, Schritt 1:
        `docker compose stop ems-app`, dann `cp /data/bindaems.sqlite3 /backup/vor-plan-4a.sqlite3` im
        Container). Das ist der Rückfall: Eine ältere app startet mit der migrierten Datenbank nicht,
        und schon die erste nächtliche Sicherung nach dem Ausrollen enthält `0010`.
     2. Ausrollen: `cd /opt/bindaems && docker compose pull ems-app && docker compose up -d --no-deps
        ems-app` (der core läuft weiter, der Regelbetrieb bleibt an). `/opt/bindaems` hat nur
        `docker-compose.yml`, `config.yaml` und `.env`, ein Build geht dort nicht; lokal baut man im
        `deploy/`-Ordner eines Checkouts (mit `.env` und `config.yaml` dort) mit `docker compose up -d
        --build --no-deps ems-app`. Die Migrationen `0010` und `0011` laufen beim Start der app. Keine neuen Felder in
        `config.yaml`, der Watchdog bleibt unverändert, keine neue `config.ini`, keine neue ACL für
        Mosquitto (die neuen HA-Entitäten sind Sensoren).
     3. Unter **Statistik** die ersten Werte prüfen: „Monat“ mit Kacheln und Abdeckung; der Abgleich der
        letzten 35 Tage erscheint nach dem ersten Nachtrag der Abrechnung (höchstens 10 Minuten nach dem
        Start); die Sessions beginnen mit dem Ausrollen, die Ladeenergie davor steht in „Autos“ als
        „Unbekannt“. Der Einstandspreis wird beim ersten Start aus der gesamten Abrechnung gerechnet.
     4. Im Tarif **OeMAG-Werte und monatliche Grundgebühren** nachtragen (soweit noch nicht geschehen), dann
        die Abrechnung der betroffenen Tage neu bewerten. Ohne sie sind Einspeiseerlös, Kosten und
        Ersparnis „–“.
     5. Optional das **Grafana-Dashboard** `deploy/grafana/bindaems.json` importieren (Betriebshandbuch 3,
        „Grafana“). Es ist nie gegen eine echte Grafana-Instanz importiert worden; ein Fehler dort ist ein
        Befund. Die Abfragen (auch das POST an `/query`) sind nicht gegen die echte InfluxDB geprüft.
     6. **Abnahme, Teil Statistik:** 30 Tage ab dem Ausrollen; im Abgleich (`/statistik`, „Monat“, oder
        `GET /api/ledger/checks?from=…&to=…`) liegen Netzbezug und Einspeisung innerhalb ±1 %, markierte
        Tage haben eine Ursache, einzelne Tage stimmen mit dem Zähler des Netzbetreibers, Unterschrift
        (`docs/verification/abnahme-phase-4.md`). Der 25.10.2026 hat 100 Viertelstunden (Zeitumstellung).
     7. **Risiken, die erst die Anlage zeigt:** Der Abgleich und die Nachrechnung sind nur gegen
        Fake-Reader und Test-Antworten der InfluxDB gelaufen, nicht gegen die echte: die Abfrage des
        Abgleichs (`last("kwh")` in der halben Stunde vor und `first("kwh")` in der halben Stunde
        nach jeder Mitternacht, ohne `GROUP BY time`, mit `GROUP BY "source","id","direction"` auf
        `"raw"."energy"`; zwei Anweisungen je Mitternacht, bis zu 72 in einer POST-Anfrage) und die
        15-min-Fenster in `raw`. Liefert der Abgleich an allen Tagen „Zählerstand fehlt“, stimmen
        Zeitfenster, die Zeit des Selektors oder die Tags nicht (Spec 11.2); ob der Netzzähler und
        die Wallbox-Zähler als `energy` ankommen, zeigt die erste Nacht. Die Belegung der Wallboxen
        kommt aus Health (`wallboxes[].occupant`, 10-s-Takt): Ohne Regelbetrieb steht dort niemand,
        und alle Sessions heißen „Unbekannt“ (der Steckzustand kommt aber auch dann).
  7. **Geplantes Laden (Plan 4b) ausrollen**, erst **nach der Abnahme der Phase 3**; das ist ein
     Schritt des Betreibers (Entscheidung 17 des Plans), und nichts geht dabei von selbst live. Die
     Images enthalten 4b seit dem 19.10.2026 (`latest`). Die Schritte stehen
     im Betriebshandbuch, „Laderegelung“, „Geplantes Laden ausrollen (Plan 4b)“:
     1. **ACL:** Der Benutzer `bindaems` braucht für die Abfahrtszeit die Zeile
        `topic write homeassistant/text/bindaems/#` in der ACL von Mosquitto (Betriebshandbuch 7),
        danach das Add-on neu starten. Ohne sie fehlt in Home Assistant nur die Abfahrtszeit.
     2. **Regelbetrieb aus** (Seite „System“ oder HA), dann die **Datenbank kopieren**
        (`/backup/vor-plan-4b.sqlite3`; eine ältere app startet mit der Migration `0012` nicht).
     3. **Ausrollen:** `cd /opt/bindaems && docker compose pull && docker compose up -d`; core und app
        starten neu, die Migration `0012` (`planned_charge`) läuft beim Start der app.
     4. **`soc_entity`** des e-Golf prüfen; der core liest sie jetzt selbst. Ein gleichlautender
        Eintrag `vehicle.<Schlüssel>.soc_pct` unter `homeassistant.entities` kann entfallen (ein
        abweichender macht die Konfiguration ungültig). Optional `charge_limit_sync: true` beim Tesla.
     5. **Regelbetrieb an** und einen Abend „Geplant“ im Dry-Run beobachten (Health `planned`, der
        Plan mit „Fahrzeug geändert“ und dem Diagramm „Autos“, die Entscheidungen, am nächsten Tag
        die Karte „Geplantes Laden“).
     6. **Abnahme, Teile „Ziel-SOC“ und „HA in beide Richtungen“** (`docs/verification/abnahme-phase-4.md`,
        Abschnitte 4 und 5): Die Quote „Ziel-SOC“ verlangt EVCS und Tesla live, denn im Dry-Run lädt
        evcc.
     7. **Rückweg:** Ein core vor 4b liest die gespeicherte Absicht nicht, sobald sie Ziel, Abfahrt
        oder Autos im Plan trägt, und startet ohne Absicht mit dem Alarm `intent.file`
        (Betriebshandbuch 4, „Rollback auf einen Stand vor Plan 4b“).
  8. **Klimaanlage (Plan 6a) ausrollen**, mit 4b nach der Abnahme der Phase 3;
     das ist ein Schritt des Betreibers, und nichts geht dabei von selbst live. Die Schritte stehen im
     Betriebshandbuch, „Klimaanlage (Phase 6a)“, und in `docs/verification/abnahme-phase-6a.md`:
     1. **Netz:** Routing und Firewall für UDP 5442 zwischen 192.168.81.0/24 (C.M.I.) und
        192.168.82.0/24 (BindaEMS) öffnen; am C.M.I. Pakete von 192.168.82.20 (Knoten 60) annehmen.
     2. **Compose-Datei und Images:** Die Datei in `/opt/bindaems` ist die Kopie aus der Installation;
        die neue aus `deploy/` (Port `5442:5442/udp` am core) übernehmen, eigene Änderungen vorher per
        `diff` hineinnehmen. Dann mit 4b ausrollen (`pull`, `up -d`), noch ohne Abschnitt `thermal`.
        Ohne die neue Datei kommt kein Paket des C.M.I. an.
     3. **Empfang prüfen:** `coe_probe listen` bei gestopptem core (Warnung zum Watchdog beachten,
        wenn EVCS oder Victron live sind); Knotennummer, Einheit der Leistung und Absender notieren,
        `config.yaml` (`thermal.devices.klima`) anpassen.
     4. **Punkt `thermal`:** `verify_part2 --punkt thermal --geraet klima` (sendet nur auf Aufruf).
     5. **core starten, Dry-Run** (der core sendet 0) und 7 Tage beobachten, dann `thermal.klima` live
        und die Abnahme 6a.
  9. **Regel R1 bis der Akku leer ist (Plan 3d) ausrollen**; wann, entscheidet der Betreiber am Ende
     (Entscheidung 6 des Plans). Vorschlag: im selben Zug wie 4b und 6a (Punkte 7 und 8), also nach der
     Abnahme der Phase 3. Nur die app ändert sich (`docker compose pull && docker compose up -d`; der
     core, `shared` und das UI sind unverändert, es gibt keine Migration). Das Image `latest` enthält
     3d erst, wenn der Branch auf `main` ist. Nichts geht dabei von selbst live: Die Regel wirkt nur
     im Plan, und der Plan wirkt nur, wenn Victron live ist und „Netzladen erlaubt“ an ist. Danach
     beim Beobachten des Plans (Punkt 5) auf den Satz „Kein Netzladen: Ab … Uhr speist die PV … ein,
     bevor der Akku leer ist (Regel R)“ und darauf achten, dass der Akku vor der Einspeisung wirklich
     auf der Reserve ist.
  10. **Lastmodell (Plan 5a) ausrollen**, mit oder nach dem gemeinsamen Ausrollen von 4b und 6a, also
      nach der Abnahme der Phase 3; bis dahin kein `docker compose pull`. Das ist ein Schritt des
      Betreibers. 5a schaltet nichts an der Anlage, es ändert nur die Eingabe des Planers. Das Modell
      gilt, wenn es in den letzten 14 Tagen mindestens 10 % genauer ist als das Profil, und das kann
      schon gleich nach dem ersten Training sein (Schalter „Lastmodell verwenden“ ab Werk an,
      Grundlage dann der Backtest). Die Schritte stehen im Betriebshandbuch, „Lastmodell (Phase 5a)“,
      „Lastmodell ausrollen (Plan 5a)“, und in `docs/verification/abnahme-phase-5a.md`:
      1. **Datenbank kopieren** (empfohlen): `/backup/vor-plan-5a.sqlite3`; eine ältere app startet
         mit der Migration `0013` nicht (Betriebshandbuch 4, „Rollback auf einen Stand vor Plan 5a“).
      2. **Ausrollen:** nach (oder mit) 4b und 6a nur die app:
         `docker compose pull ems-app && docker compose up -d ems-app`. Der core bleibt, der Regelbetrieb
         bleibt an. Das Image ist rund 130 MB größer (LightGBM und `libgomp1`).
      3. **Beim Start:** Die Migration `0013` läuft (`weather_slot`, `model_registry`), die Temperatur-Historie
         (92 Tage von Open-Meteo) wird einmal geholt, und das erste Training läuft gleich danach. Hat die
         Abrechnung noch keine 28 Tage mit Daten, steht auf der Seite „Prognosen“ „Zu wenig Daten: {n} von
         28 Tagen.“, und jede Nacht um 03:15 Uhr folgt ein neuer Versuch.
      4. **Sensoren wählen** (optional): unter **Einstellungen** → „Lastmodell“ bis zu 8 Entitäten aus der
         HA-Datenbank (etwa Anwesenheit `person.…`), dann auf **Prognosen** „Jetzt neu trainieren“.
      5. **Abnahme 5a:** 28 Tage ab dem Ausrollen. Auf „Prognosen“ (Zeitraum „28 Tage“) hat „Last Modell“
         über alle Vorlaufklassen zusammen (Abruf mit `jq` in der Checkliste, Abschnitt 3)
         mindestens 10 % niedrigeren MAE als „Last Profil“, **oder** das Profil war automatisch aktiv,
         solange das Modell es nicht schaffte; dazu jede Nacht eine Version (Registry), keine Fehler in
         `load-model` und der Rückfall bei fehlender Modelldatei, an der Anlage beobachtet (Datei
         umbenennen, `ems-app` starten: „Verfahren: Profil“, danach trainiert die app von selbst neu).
      6. **Bei der Abnahme zu wissen:** Das Modell kann gleich nach dem ersten Training aktiv werden,
         wenn es im Backtest mindestens 10 % genauer ist; ab 10 Tagen mit Werten im Schattenbetrieb
         entscheidet der Schattenbetrieb. Der Backtest ist in den ersten Wochen zu günstig, weil
         Training und Backtest mit der archivierten Temperatur rechnen; zudem haben die Profile des
         Backtests wenig Historie, was das Profil dort schwächer macht. Wer zuerst nur beobachten
         will, schaltet gleich nach dem Start unter **Einstellungen** → „Lastmodell“ „Lastmodell
         verwenden“ aus (reiner Schattenbetrieb, das Modell rechnet weiter mit) und später an
         derselben Stelle wieder ein; die app bewertet dann sofort neu. Die Dauer eines Trainings an
         der Anlage ist nicht gemessen (Zeitlimit 10 min); das Log nennt „Lastmodell trainiert“.
  11. **Fahrverhalten (Plan 5b) ausrollen**, gemeinsam mit 3d, 4b, 5a und 6a und damit nach der Abnahme der
      Phase 3; bis dahin kein `docker compose pull`, und erst nach dem Gesamtreview und der Zustimmung des
      Betreibers zur Integration auf `main`. Das ist ein Schritt des Betreibers. Anders als 5a ändert 5b den
      core (`departure_on`), core und app rollen darum gemeinsam aus, **mit ausgeschaltetem Regelbetrieb**.
      Nichts geht von selbst live: Die Automatik ist aus, bis du sie einschaltest. Die Schritte stehen im
      Betriebshandbuch, „Fahrverhalten (Phase 5b)“, „Fahrverhalten ausrollen (Plan 5b)“, und in
      `docs/verification/abnahme-phase-5b.md`:
      1. **Regelbetrieb aus** (System → „Bedienung“) und warten, bis die Übergabe fertig ist; das **vor**
         dem Stoppen der app, sonst ist der Schalter nicht erreichbar.
      2. **Datenbank sichern:** `docker compose stop ems-app`, dann Kopie nach `/backup/vor-plan-5b.sqlite3`;
         die Migration `0014` legt `driving_profile` und `driving_automation` an, eine ältere app startet
         danach nicht (Betriebshandbuch 4, „Rollback auf einen Stand vor Plan 5b“).
      3. **core und app gemeinsam ziehen** (`cd /opt/bindaems && docker compose pull && docker compose up -d`;
         jede Absicht mit Eintrag unter `vehicles` trägt jetzt `departure_on`, ein core vor 5b liest sie nicht),
         dann **Regelbetrieb an**, sobald **System** beide mit demselben Stand zeigt.
      4. **Anwesenheit eintragen:** **Einstellungen** → „Fahrverhalten“ → beim Tesla „Anwesenheit aus Home
         Assistant“ den `device_tracker.…` des Tesla (er ist in Home Assistant vorhanden; der Betreiber hat
         es am 20.10.2026 bestätigt). Der Eintrag wirkt nur mit `influxdb.ha_database`, sonst warnt die app und nimmt die nächste Quelle. Leer gelten Tessie oder `home_entity`.
      5. **Prüfen:** Komponente `driving` auf **System** („Fahrverhalten aktuell“ oder „Noch keine
         Fahrdaten“), der erste Nachtlauf um 03:40 Uhr oder als Admin „Jetzt neu berechnen“ auf **Prognosen**.
      6. **Automatik bewusst einschalten**, je Auto erst, wenn die Vorschläge mindestens die Konfidenz
         „mittel“ haben und du sie für richtig hältst.
      7. **Abnahme 5b:** 28 Tage ab dem Ausrollen (Checkliste oben): Vorschläge mit Konfidenz,
         Trefferquoten je mindestens 80 % bei mindestens 10 bewerteten Tagen, keine Zeile „geändert“ durch
         die Automatik, ein erwartetes Auto im Zeitplan, beim Anstecken ersetzt (einmal beobachtet).
  - Teil 2 schreibt auf die Geräte: nicht während der 7 Tage der Abnahme Phase 1 und ihres
    24-h-Mitschnitts (`docs/verification/abnahme-phase-1.md`, 4.2 und 4.3). Seit dem
    19.10.2026 sind 4.2 und 4.3 überholt (`docs/verification/2026-10-19-abnahme-phase-1.md`).
  - **Vor der Live-Schaltung von EVCS und Tesla** waren diese Punkte zu entscheiden oder umzusetzen
    (Gesamtreview von 2c-1; je Befund und Abhilfe, knapp auch im Betriebshandbuch, „Laderegelung“,
    „Hinweise für die Anlage“). **Stand nach Plan 2d:** Die Punkte 1 bis 3 sind umgesetzt, 4, 6 und 7
    waren es schon mit 2c-2a, und 5 und 8 sind mit einer Entscheidung abgeschlossen. Offen ist nur,
    was an der Anlage zu prüfen ist (Dry-Run, drei Prüfungen zur Zuordnung, Teil 2). Dann gehen EVCS
    und Tesla gemeinsam mit dem Live-Schalter live (Betriebshandbuch, „Umstieg von evcc“):
    1. **Stundenbudget des Tesla** — erledigt mit Plan 2d: Unter PV-Wolken verbrauchte die Regelung die
       20 Befehle je Stunde (im Simulator in `wolkig` nach 2410 s; 8 von 19 Strombefehlen gingen an
       die 5-A-Eigenheit), und danach wirkten auch ein Stopp und ein Moduswechsel erst später (in
       einer Probe im Simulator wirkte „Aus“ nach 19 min), gegen Design 6.3. Jetzt ist der Stopp in
       „Aus“ dringend und umgeht die Stundenlimits, und `control.tesla_reserve_commands` (4) hält
       Befehle frei: Ist das Budget bis auf die Reserve verbraucht, geht ein Strombefehl nur noch
       hinaus, wenn er den Strom senkt und der Ist-Strom über der Grenze des Wächters liegt.
       Starts, Stopps, der Höchststrom an der EVCS und dringende Befehle sind frei. Im Simulator
       gibt es in `dry-run` und `tesla-aus` keine Zurückstellung wegen des Stundenlimits mehr.
    2. **Selbststart im laufenden Vorgang** — erledigt mit Plan 2d: Startet das Auto selbst
       (Tesla-App), sendet die Regelung nach 120 s gegen den Modus „Laden aus“ erneut, je Fahrzeug
       höchstens alle 600 s, im Start/Stopp-Budget (im Modus „Aus“ dringend). Szenario
       `tesla-selbststart`: gestoppt binnen 210 s.
    3. **Ansteckerkennung des Tesla** — erledigt mit Plan 2d: Tessie fragt ein abgestecktes Auto zu
       Hause alle 60 s ab (sonst weiter alle 600 s) und sofort, frühestens 10 s nach der letzten
       Abfrage, wenn an einer Wallbox ein Fahrzeug erscheint (`TessieAdapter.poll_soon`).
    4. **Ausnahme in der Regelung** — erledigt mit Plan 2c-2a: `RegulationLink` fängt jede Ausnahme
       des Kerns. Die Regelung ruht in diesem Zyklus (`idle`; wirft auch das, wird der Kern neu
       gebaut), die Laufzeit führt keinen Befehl der Regelung mehr aus, der Heartbeat bleibt aus, und
       der Keeper läuft weiter. Der Alarm `control.exception` (Fehler) bleibt, bis ein Zyklus mit
       Regelung ohne Ausnahme läuft, auch wenn die Regelung zwischendurch ruht (etwa nach dem
       Abschalten des Regelbetriebs); das Log nennt die erste Ausnahme einer Folge mit Traceback.
    5. **5-A-Eigenheit:** Befehle auf oder von 5 A gehen über Tessie beim ersten Mal verloren und
       kosten einen Neuversuch. Seit 2c-2a kommt hinzu: Startet der Tesla nachts in Min+PV mit 16 A,
       lädt er 90 s mit 16 A, bis der Befehl auf 5 A wirkt. Regel 2 begrenzt den Override auf 8 kW, und
       der Akku liefert den Rest: im Simulator bis zu 5,8 kW in die Autos für 90 s, obwohl die
       Unterstützung auf 3 kW begrenzt ist (`akku-minpv` startet den Tesla deshalb mit 5 A).
       Entscheidung (Plan 2d): Der Code bleibt bei 5 A (Design 7.2). Für die `config.yaml` der
       Anlage empfiehlt sich `vehicles.tesla.min_a: 6`; das entscheidet der Betreiber dort.
    6. **Simulator** — erledigt mit Plan 2c-2a: Das Szenario `schieflast-pv` (PV konstant 12 kW,
       5,5 kW mehr Last auf L1 ab 10 min) lässt das Importziel binden. Der e-Golf geht über `import_limit`
       von 11 auf 8 A, die längste Zeit über dem Ziel in PV-Modi ist 6 s (Grenze 10 s).
    7. **Technische Schulden vor 2c-2** — erledigt mit Plan 2c-2a (Umbau in den ersten drei Tasks, ohne
       geänderte Testdatei): `charging.py` gab die Ladepunkte und ihre Verteilung (`charge_points.py`),
       den Tesla-Teil (`charging_tesla.py`) und die Merker der letzten eigenen Werte (`last_values.py`)
       ab, die Verdrahtung der Regelung zog aus `runtime.py` nach `regulation_link.py`, und die
       doppelten Hilfen sind zusammengeführt (`held_since` und `lasted` in `timing.py`, `ok_number` in
       `charge_inputs.py`, `PV_SURPLUS` in `charge_modes.py`). Die Zeilenzahlen heute (`wc -l`):
       `charging.py` 895 (vorher 1065; die Grenzen des Wächters und `cars()` kamen dazu, der Plan
       nannte rund 700), `runtime.py` 1296 (vorher 1289, geplant rund 1200: Die Verdrahtung ist
       ausgezogen, dafür kamen die Erwartung des Overrides, das Budget und die Viertelstunde des
       Wächters dazu), `regulation.py` 300, `regulation_link.py` 325, `battery.py` 605, `peak_guard.py`
       661, `charge_points.py` 183, `charging_tesla.py` 194 und `last_values.py` 164. `charging.py` und
       `runtime.py` sind damit weiter groß; ein weiterer Abbau wäre ein eigener Umbau (etwa die
       Drosselfunktionen aus `peak_guard.py` nach `peak_throttle.py`, siehe „Bewusst offen gelassen“).

    Dazu kommen die Punkte „Offen vor dem Live-Betrieb des Tesla“ (Reserve des sicheren Zustands,
    Annahmen des Prüffensters) unter „Hinweise für die Pläne 2c-2 und 2d“.
  - **Kleine Nacharbeiten aus dem Gesamtreview von 2c-1:** erledigt am 14.10.2026, je ein Commit
    auf `ccr-9778345e-t3x44k`: Doku-Korrekturen (20 Dry-Run-Entscheidungen an den Tesla, `applied`
    einer Zurückstellung, „rechnet nie mit Akku-Leistung“), die Drossel des Entscheidungslogs
    unterscheidet den angeforderten Wert (ein zurückgestellter Stopp kurz nach einem
    zurückgestellten Start erscheint jetzt), und die LAN-Adressen der Anlage sind durch Platzhalter
    ersetzt (die Git-Historie behält sie).
- **Offen an der Anlage, aus Phase 1 und 2a:**
  1. Die manuellen Punkte von Teil 1 nachtragen: Sicherheitsprofil und Modbus-Dienstliste
     (17.1-3), NTP am Cerbo (17.1-12).
  2. Die Log-Zeile „Aktionen abgeschlossen: …“ der Probe vom 11.10.2026 nachreichen; sie zeigt, ob
     jede Aktion „ok“ oder „unverändert“ war.

## Nächste Schritte im Projekt

1. **Abnahme der Phase 1** nach `docs/verification/abnahme-phase-1.md`:
   - 7 Tage lückenlose Aufzeichnung (Tagesbilanz „96 von 96 · 100 %“)
   - Tagesbilanz innerhalb ±2 % von VRM
   - Preise und Prognose sichtbar
   - nachweislich keine Schreibpfade zu Geräten; die Punkte 4.2 (24 h ohne `W/#`) und 4.3 (Einstellungen
     unverändert) sind seit der Live-Schaltung von Wallbox und Tesla überholt (Festlegung des
     Betreibers vom 19.10.2026, `docs/verification/2026-10-19-abnahme-phase-1.md`)
2. **Phase 2**, Wallbox-Lastmanagement und PV-Überschussladen (Spec 18), in vier Plänen
   (Zuschnitt in `docs/superpowers/plans/2026-10-10-phase-2a-sicherheitsbasis.md`):
   - **2a Sicherheitsbasis:** umgesetzt, im Gesamtreview geprüft und an der Anlage installiert;
     die Watchdog-Probe ist bestanden (11.10.2026).
   - **2b Aktoren:** umgesetzt, je Task und im Gesamtreview geprüft, auf `main` und an der Anlage
     ausgerollt (12.10.2026); das Prüfprotokoll Teil 2 ist bis auf zwei Schritte bestanden (siehe
     „Offen an der Anlage“).
     - Aktoren für EVCS, Victron und Tesla mit Dry-Run und Rücklese-Prüfung
     - sicherer Zustand (Spec 8.5) und Übergabe am Ende des Live-Betriebs
     - Entscheidungslog im core (Spool, Stream, `GET /v1/decisions`)
     - Erkennung fremder Änderungen (Spec 7.9)
     - Werkzeug `verify_part2` für das Prüfprotokoll Teil 2, schreibend und gemeinsam mit dem
       Betreiber (Spec 17.2); an der Anlage am 12.10.2026 ausgeführt
     - Watchdog 1.1.0 mit der Aktion für den hub4-Override
   - **2c Regelung**, geteilt (Entscheidung des Betreibers vom 12.10.2026):
     - **2c-1 Laderegelung:** umgesetzt, je Task und im Gesamtreview geprüft, an der Anlage noch
       nicht ausgerollt (siehe Kurzfassung): Modi Sofort, PV, Min+PV, Aus und Boost, Phasenwächter,
       Tesla-Steuerung, vereinfachte Zuordnung Auto ↔ Wallbox, Degradation der Ladepunkte,
       Anlagensimulator mit Regelkreistests in der CI.
     - **2c-2a Akku und Spitzen:** umgesetzt, je Task und im Gesamtreview geprüft, auf `main`
       (`31721a6`, 15.10.2026): Umbau, Befehle der Regelung an Victron
       (Akku-Unterstützung und Setpoint-Kompensation, Spec 7.6 und 7.7, Auffrischen des Overrides vor
       seinem Timeout von 5 min), 15-min-Wächter (Spec 7.4), Simulator mit 18 Szenarien (mit 2d heute 23).
     - **2c-2b Zuordnung:** umgesetzt, je Task und im Gesamtreview geprüft, die Fix-Welle danach
       umgesetzt, auf `main` (`31721a6`, 15.10.2026): die volle Zuordnungserkennung (Spec 7.8, Design
       2c-2, Abschnitt 7) samt Gastfahrzeug und `vehicles.<name>.home_entity`, der Grund
       `assignment_detected`, Health je Wallbox und im Simulator der e-Golf „zu Hause“ oder „weg“,
       das Gastfahrzeug, jedes Auto an jeder Wallbox und die Szenarien aus Design 10.3 (damals 21, mit 2d 23).
   - **2d Bedienung:** umgesetzt, im Gesamtreview geprüft, auf `main` (`0198900`, 16.10.2026): Absicht aus der app
     (Regelbetrieb, Live-Schalter mit Bestätigung, Lademodi, Boost, Zuordnung, Gast-Modus, Vorrang),
     Entscheidungslog in SQLite, UI-Seiten „Laden“ und „Entscheidungen“, HA-Bedienelemente und
     Benachrichtigungen, die Tesla-Punkte vor der Live-Schaltung. Ziel-SOC und Abfahrt gehören zum
     Modus „Geplant“ und kommen mit Phase 4.

   Die Abnahme der Phase 2 besteht aus 2 Wochen Dry-Run, der schon nach 2c-1 beginnt, während 2c-2
   und 2d entstehen, und der Live-Schaltung danach. Abweichend von der Spec gehen EVCS und Tesla
   **gemeinsam** live, mit dem Live-Schalter im UI (seit 2d) und ganz abgeschaltetem evcc;
   Victron geht nach 2c-2a live, sobald Dynamic ESS aus ist (Design 2c-1, Abschnitt 14; Design 2c-2,
   Abschnitt 11.1).

   **EVCS in 2b:**
   - **Lesen:** Seit der korrigierten IP-Whitelist antwortet die EVCS BindaEMS direkt per Modbus
     (Prüfprotokoll Teil 1 vom 11.10.2026); gelesen wird weiter so.
   - **Schreiben:** über den GX-Dienst `evcharger` des Cerbo (`/SetCurrent`, `/StartStop`), wie
     der Watchdog (Entscheidung des Betreibers vom 11.10.2026). Das Prüfprotokoll Teil 2 hat den Weg
     an der Anlage am 12.10.2026 bestätigt.
   - Geschrieben wird nie der Maximalstrom (5017).

3. **Phase 3**, Planung und Optimierung (Spec 18), in drei Plänen (Entscheidung des Betreibers vom
   16.10.2026; Einordnung im Plan 3a):
   - **3a Tarif und Prognosen:** umgesetzt, je Task und im Gesamtreview geprüft, die Fix-Welle danach
     umgesetzt, auf `main` (bis `e16bff5`). Nur app, der core bleibt unverändert.
   - **3b Optimierer und Zeitplan:** umgesetzt, je Task und im Gesamtreview geprüft, die Fix-Welle danach
     umgesetzt, auf `main` (17.10.2026). Optimierer (HiGHS über PuLP, eigener Prozess, Zeitlimit) nur für den Akku, Regel R1,
     Wirtschaftlichkeit, Schalter „Netzladen erlaubt“, Planungsablauf mit Speicherung, UI-Seite
     „Zeitplan“, HA-Sensoren und Taste „Neu planen“, Szenario-Werkzeuge mit dem Vergleich mit „Victron
     allein“. Pläne entstanden als Vorschau; der core bekam sie noch nicht. Nur app, `shared` und UI.
   - **3c Planausführung im core:** umgesetzt, je Task und im Gesamtreview geprüft, die Fix-Welle
     danach ist umgesetzt, und auf `main` (17.10.2026, Fast-Forward,
     Kurzfassung). Plan in der Absicht, Prüfung im core, Akku-Absichten `EIGENVERBRAUCH`, `HALTEN` und
     `NETZLADEN(P)` über den Override, Budget des 15-min-Wächters aus dem Plan, Plan-Schwelle und
     Schalter der Akku-Unterstützung, Stand der Ausführung, „Plan gegen Ist“, Mehrkosten beim Boost,
     Simulator mit Wirkungsgrad und Plan-Szenarien. An der Anlage folgen 2 Wochen Dry-Run der
     Planausführung (DESS aus) und die 30 echten Tage, dann geht Victron live (Abnahme Phase 3).

4. **Phase 4**, Geplantes Laden, Statistik, HA komplett (Spec 18), in zwei Plänen (Entscheidung des
   Betreibers vom 17.10.2026; Einordnung im Plan 4a):
   - **4a Statistik:** umgesetzt und je Task geprüft, seit dem 18.10.2026 auf `main`; Gesamtreview,
     Fix-Welle und Nacharbeit sind abgeschlossen. Nur app, UI und `deploy/`, der core bleibt unverändert (Kurzfassung). An der Anlage
     genügt der Neubau von `ems-app` (Punkt 6 unter „Offen an der Anlage“), dann laufen die 30 Tage
     der Abnahme.
   - **4b Geplantes Laden:** umgesetzt, je Task und im Gesamtreview geprüft, die Fix-Welle ist umgesetzt
     und das Re-Review sauber, seit dem 19.10.2026 auf `main`, nicht ausgerollt (Kurzfassung). Er ändert core und Optimierer (Modus „Geplant“
     mit Ziel-SOC und Abfahrt, die Autos im Planer, Regel R2, späteste Startzeit, Tesla-Ladelimit, HA
     vollständig, Benachrichtigungen komplett) und braucht den Neustart des core. Die vier Objekt-IDs
     der Statistik-Sensoren stehen jetzt in der Prüfung der Konfiguration (`HA_SYSTEM_IDS` und
     `HA_VEHICLE_SUFFIXES` in `shared/config.py`). Es folgt, nach der Abnahme der Phase 3, das
     Ausrollen durch den Betreiber (Punkt 7 unter „Offen an der Anlage“); danach die Abnahmepunkte
     „Ziel-SOC“ und „HA in beide Richtungen“.
5. **Phase 6a**, Klimaanlage (Spec 18, „Phase 6 – Wärme und Kälte“): umgesetzt, je Task und im
   Gesamtreview geprüft, Fix-Welle und Re-Review erledigt, seit dem 20.10.2026
   auf `main`, nicht ausgerollt (Kurzfassung). Es folgt das Ausrollen mit 4b nach der Abnahme der Phase 3 (Punkt 8 unter
   „Offen an der Anlage“). Danach Phase 5; 6b (Wärmepumpe) frühestens Herbst 2027 nach Phase 5.
6. **Plan 3d** (Regel R1 nur, bis der Akku leer ist): umgesetzt, je Task und im Gesamtreview geprüft,
   Fix-Welle umgesetzt, seit dem 20.10.2026 auf `main`
   (Kurzfassung); es folgt das Ausrollen nach
   Entscheidung des Betreibers (Punkt 9 unter „Offen an der Anlage“).
7. **Phase 5**, Lernen (Spec 18), in drei Plänen (Entscheidung des Betreibers vom 20.10.2026;
   Einordnung im Plan 5a):
   - **5a Lastmodell und Prognosegüte:** umgesetzt und je Task geprüft, auf dem Branch
     `claude/phase-5-plan-s7xe0n`, **noch nicht auf `main`** (die Integration braucht die Zustimmung des
     Betreibers), nicht ausgerollt (Kurzfassung). Nur app und UI, der core bleibt gleich. Es folgt das
     Ausrollen mit oder nach 4b und 6a (Punkt 10 unter „Offen an der Anlage“) und die Abnahme über 28 Tage.
   - **5b Fahrverhalten:** umgesetzt und je Task geprüft, seit dem 21.10.2026 per Fast-Forward auf `main`
     (Gesamtreview und Fix-Welle erledigt, Zustimmung des Betreibers), nicht ausgerollt (Kurzfassung). Es lernt Abfahrt, Ankunft und SOC bei Ankunft je Auto und Wochentag,
     macht Vorschläge mit Konfidenz, hat eine optionale Automatik für „Geplant“ und liefert die
     Anwesenheitsprognose für den Optimierer (erwartete Autos). **Nicht in 5b** ist die Akku-Unterstützung
     für PV-Autos im MILP (Spec 7.6, Stand Plan 5b). Ändert auch den core (eine Zeile). Es folgt das
     Ausrollen gemeinsam mit 3d, 4b, 5a und 6a (Punkt 11 unter „Offen an der Anlage“) und die Abnahme über
     28 Tage; „Vorschläge erscheinen mit Konfidenz“ gehört zu ihr.
   - **5c Vorheizen und Vorkühlen (später):** ein Wärmemodell des Hauses aus den Daten der Klimaanlage und
     die Klimaanlage als flexible Last im Planer. Es braucht Wochen mit Daten aus 6a, also erst nach dem
     Ausrollen von 6a; ob 5c allein oder mit 6b kommt, entscheidet der Betreiber dann.

### Nächster Schritt: die Anlage

1. Plan 2b ist auf `main`; Gesamtreview, Fix-Durchgang und Re-Review sind erledigt, die
   Entscheidungen stehen in `docs/superpowers/plans/2026-10-11-phase-2b-entscheidungen.md`. Plan
   2c-1 ist umgesetzt, je Task und im Gesamtreview geprüft, und die Fix-Welle danach ist umgesetzt;
   er ist auf `main`, ebenso die EVCS-Hysterese und die kleinen Nacharbeiten danach (`47a09a7`).
   Die Entscheidungen von 2c-1 stehen im Kopf des Designs und in
   den Absätzen „Stand Plan 2c-1“ der Spec, die offenen Punkte vor der Live-Schaltung in „Offen an
   der Anlage“.
2. An der Anlage die Schritte aus „Offen an der Anlage“.
3. Plan 2c-2a ist umgesetzt und im Gesamtreview geprüft, die Fix-Welle danach ist umgesetzt
   (`docs/superpowers/plans/2026-10-14-phase-2c2a-akku-spitzen.md`). Plan 2c-2b ist umgesetzt, je
   Task und im Gesamtreview geprüft, und die Fix-Welle danach ist umgesetzt
   (`docs/superpowers/plans/2026-10-15-phase-2c2b-zuordnung.md`). Beide sind auf `main`
   (`31721a6`, Fast-Forward mit Zustimmung des Betreibers vom 15.10.2026, Tests auf `main` grün).
4. **Plan 2d** (Bedienung in UI und HA) ist umgesetzt (Kurzfassung): die Auswahl des Gast-Modus, die
   Anzeige von Health je Wallbox, die Übersteuerung der Zuordnung und die Tesla-Punkte vor der
   Live-Schaltung. Gesamtreview und Fix-Welle sind erledigt, er ist auf `main` (`0198900`,
   Fast-Forward mit Zustimmung des Betreibers vom 16.10.2026, alle Prüfungen auf `main` grün). An der
   Anlage folgt die
   **Inbetriebnahme Punkt 3** (Design 2c-2, 11.1): Die Erkennung läuft im Dry-Run mit, und Health
   zeigt, wer wo steht. Sie braucht nicht 2d, nur die ausgerollten Images; die Schritte stehen unter
   „Offen an der Anlage“.
   - **Boost und Übersteuerung:** Eine neue Version der Absicht bindet jedes Fahrzeug neu, das
     `boost_until` oder `wallbox` nennt. Die app schickt deshalb einen abgelaufenen Boost nicht
     zurück (`IntentService`, Betriebshandbuch 13, „Absicht und Regelbetrieb“); wer eine Absicht
     per `curl` sendet, lässt beides weg, wo es nicht mehr gelten soll.
5. **Plan 3a** (Tarif und Prognosen) ist umgesetzt, im Gesamtreview geprüft, die Fix-Welle danach ist
   umgesetzt, und er ist auf `main` (bis `e16bff5`). Die Lastprognose läuft ohne HA-Historie
   (Entscheidung vom 16.10.2026).
6. **Plan 3b** (Optimierer und Zeitplan) ist umgesetzt und im Gesamtreview geprüft (Kurzfassung), die
   Fix-Welle danach ist umgesetzt, das Re-Review war sauber. Die **Integration auf `main`** ist
   erledigt: am 17.10.2026 auf die Trail-Commits rebased und per Fast-Forward übernommen. Das
   Ausrollen an der Anlage geschieht jetzt gemeinsam mit 3c (Punkt 7).
7. **Plan 3c** (Planausführung im core) ist umgesetzt, je Task und im Gesamtreview geprüft, und die
   Fix-Welle danach ist umgesetzt; er ist auf `main` (17.10.2026, Fast-Forward, Kurzfassung; behoben sind das Fenster ohne vertrauten Stand nach
   einem Neustart des core für die Übergabe des Plans und das Budget unter dem eigenen Bezug). Es
   folgen:
   - an der Anlage das **gemeinsame Ausrollen von 2d, 3a, 3b und 3c** (Punkt 1 unter „Offen an der
     Anlage“, mit der neuen ACL-Zeile für `number`), dann der **Dry-Run der Planausführung über 2
     Wochen** mit DESS aus (Punkt 4) und parallel die **30 echten Tage** durch `export_days` und
     `scenario_days` (Punkt 5);
   - danach **Victron live** mit den vier Bedingungen aus Spec 8.7 und die **Abnahme der Phase 3**.
     Dann wird Plan 4b ausgerollt (Modus „Geplant“, Regel R2, die Autos im Planer; umgesetzt auf dem
     Branch, Punkt 7 unter „Offen an der Anlage“); die Statistik aus 4a ist schon gebaut und kann
     vorher ausgerollt werden (Punkt 6 dort).
8. **Plan 5a** (Lastmodell) ist umgesetzt und je Task geprüft, auf dem Branch
   `claude/phase-5-plan-s7xe0n` und noch nicht auf `main` (Kurzfassung). Es folgen:
   - die **Integration auf `main`** (Fast-Forward, nur mit Zustimmung des Betreibers, nach Gesamtreview
     und grüner CI des Branch-Kopfs, auch dem Bau des Images mit `libgomp1`);
   - an der Anlage das Ausrollen von `ems-app` mit oder nach 4b und 6a (Punkt 10 unter „Offen an der
     Anlage“) und die Abnahme über 28 Tage;
   - als nächster Plan **5b (Fahrverhalten)**, Spec 10.4 und 18; danach 5c (Vorheizen und Vorkühlen).

## Hinweise für die Pläne 2c-2 und 2d

Aus den Reviews der Pläne 2a bis 2c-2b. Plan 2c-2a hat die Punkte zur Regelung umgesetzt, Plan 2c-2b
die zur Zuordnung; die Reste stehen dort. Plan 2d hat die Punkte zur Bedienung umgesetzt (unten); die
übrigen Reste gehören zu Phase 3. Was
Plan 2c-1 aus der früheren Liste erledigt hat (Freigabe erst ohne offenen Schreibvorgang, Aufräumen des
Entscheidungslogs, Heartbeat-Zähler je Lauf, SAFE ohne Tesla, der nicht lädt, Auslösung des Watchdogs,
Laden nach einem Inselbetrieb, Selbstprüfung des Multi-Modus), steht in der Kurzfassung.

- **Regelung (2c-2a umgesetzt; Reste für Phase 3):**
  - Befehle der Regelung gehen durch `CommandExecutor.execute`: Er ruft `SafetyLayer.authorize`
    genau einmal je betrachtetem Befehl auf, denn `authorize` verändert Rate-Limiter und
    Schreibbudget. Die Laderegelung zeigt das Muster: Sie berechnet Befehle, die Laufzeit führt sie aus
    und gibt jedes Urteil mit `note_verdict` an sie zurück.
  - **Bei „Regelbetrieb aus“ regelt auch der Akku-Sollwert nicht** (so umgesetzt), nicht in DEGRADED
    oder SAFE mit dem Übergabe-Grund: Dort gehören dem core nur die Sicherwerte der Übergabe.
  - **Importlimit:** Peak Shaving steht an der Anlage bei 20 A je Phase mit „immer“ (gewollt,
    Spec 8.1). Es wirkt nur, solange der Multi auf „Ein“ steht: Mit „Nur Laden“ stieg L3 am
    12.10.2026 auf 33,3 A, nahe an der 35-A-Sicherung. Seit 2c-1 warnen die Selbstprüfungen
    `multi_mode` und `import_limit`, und der Phasenwächter rechnet ohne die Hilfe des Akkus. Die
    Setpoint-Kompensation (2c-2a) berücksichtigt das Importlimit nicht eigens; Regel 2 und das
    Importlimit wirken zusammen (Design 2c-2, 5.4).
  - `SafetyContext.modes` muss `ActuatorMode`-Werte enthalten; ein String „live“ bliebe still im
    Dry-Run. `wallbox_phase_a` ist nach Netzphasen geschlüsselt (der Erzeuger wendet `phase_map`
    an), und `present[(aktor, current)]` ist der Sollstrom, nicht der Messstrom. Das stellt
    `core/control/context.py` bereit.
  - `grid_budget_w` darf nie NaN oder ±∞ sein. Seit 2c-2a erzeugt es der 15-min-Wächter (`PeakGuard`),
    und es ist nie NaN oder ±∞. Es geht nur hinaus, wenn der SOC bekannt ist und über USV-Reserve +
    `control.reserve_guard_pct` liegt, sonst ist es `None` (Spec 8.3, Stand Plan 2c-2a).
  - `VictronMqttAdapter.write` nur aus dem Event-Loop-Thread aufrufen; es reiht nur ein.
  - Die `inputs` der Entscheidungen enthalten seit 2c-1 SOC, Akkuleistung, Netzstrom je Phase,
    Überschuss, Grenze des Wächters und Ist-Werte, seit 2c-2a beim Override auch `pv_w`, `house_w`,
    `cars_w`, `deficit_w`, `allowed_w`, `target_w` und `actual_setpoint_w`. Das Budget steht nur
    mittelbar darin (`target_w`); Preis und Plan kommen mit Phase 3 dazu. Weckregeln für den Tesla im
    Modus „Geplant“ kommen mit Phase 4.
  - **Tesla-Ladestrom:** Über Tessie wirkt ein Befehl, der auf 5 A setzt oder von 5 A weggeht, erst
    beim zweiten Mal, obwohl Tessie den ersten mit `result: true` bestätigt (Prüfprotokoll Teil 2
    vom 12.10.2026, Nachtrag). Der Tesla-Aktor prüft den Strom nach der Bestätigung im Tessie-Cache:
    Es zählt die erste Meldung 10 bis 75 s danach (`SETTLE_S`, `VERIFY_S`; die Frist beträgt 85 s).
    So fängt das Fenster auch die 60-s-Abfrage eines angesteckten, noch nicht ladenden Autos.
    Zeigt die Meldung einen anderen Strom, meldet der Aktor `NOT_TAKEN`; ohne Meldung gilt Tessies
    Bestätigung. Er sendet nicht selbst nach. Die Laderegelung fordert den Strom seit 2c-1 als
    normalen Befehl erneut an (Grund `not_taken_retry`), er wartet auf die 90-s-Regel. Auch einen
    bestätigten Befehl nimmt sie nicht als Wirkung, maßgeblich ist der gemessene Strom. Offen vor dem
    Live-Betrieb des Tesla:
    - **Reserve des sicheren Zustands** (Spec 8.5): Bei Netzausfall sendet der Keeper dem Tesla
      „Laden aus“ alle 10 s erneut, bis der Cache es zeigt, beim Laden bis zu 30 s. Das kostet bis
      zu 3 der 6 Start/Stopp-Befehle je Stunde und alle 3 Versuche des Vorfalls. Zahl und Abstand der
      Versuche oder `min_a` prüfen.
    - **Annahmen des Fensters:** Tessie bestätigt binnen etwa 5 s (bei einer langsameren Antwort
      verkürzt sich das Fenster), und der Cache zeigt das Auto binnen 10 s. Im Lauf vom 12.10.2026
      kam die erste gewertete Meldung erst nach rund 30 s; der Verzug ist damit nicht gemessen.
  - **Netz-Sollwert-Override:** Er verschwindet nach 5 min ohne Auffrischen (Prüfprotokoll Teil 2
    vom 12.10.2026). Seit 2c-2a frischt die Regelung einen gewollten Override alle 240 s auf.
  - **Entladegrenze (Phase 3):** Es gibt einen flüchtigen Override `ess.override.maxdischargepower`.
    Prüfen, ob er die persistente Entladegrenze ersetzen kann; das schont den Flash.
- **Bedienung (2d umgesetzt; was die Punkte unten für die Pflege bedeuten):**
  - **Boost und Übersteuerung:** Eine neue Version der Absicht bindet jedes Fahrzeug neu, das
    `boost_until` oder `wallbox` nennt. Die app schickt deshalb einen abgelaufenen Boost nicht
    zurück (`app/control/intent.py`, `_clean`).
  - Die Absicht kennt seit 2c-1 `vehicles`, `priority` und `surplus_priority` (Spec 5.3). Der core
    lehnt mit 422 und den Gründen in `details` ab: unbekannte Fahrzeuge oder Wallboxen, eine Wallbox
    für mehrere Fahrzeuge und eine Priorität, die nicht jedes Fahrzeug genau einmal nennt. Die app
    zeigt diese Sätze.
  - `GET /v1/health` nennt je Fahrzeug (`vehicles`) Modus, Boost bis, Wallbox, Zuordnung, Grenze,
    angesteckt, lädt, Weckvorgänge heute und den letzten Grund, und seit 2c-2b je Wallbox
    (`wallboxes`), wer dort steht, die Art der Zuordnung und die Indizien; die UI-Seite „Laden“ kann
    sie nutzen. `occupant` ist auch während eines Vorgangs `null`, solange die Zuordnung unklar ist
    und an der Wallbox kein Fahrzeug der Konfiguration steht.
  - **Gast-Modus:** `guest.mode` wählt man jetzt im UI (Karte „Gast“) und in HA („Gast-Lademodus“); der
    Modus gilt für jeden Gast an jeder Wallbox.
  - Die Quittierung von Sperren (Spec 7.9) gibt es seit 2d; sonst lösen sie sich nach
    `safety.lock_release_s`.
  - Die app nimmt für die Version der Absicht Millisekunden seit 1970.
  - Die app speichert die Entscheidungen (SQLite, Markierungen für Grafana) und holt den Rückstand
    mit `GET /v1/decisions`. Die Zeitzone im Query als `%2B` kodieren.
  - Eine erkannte fremde Änderung erzeugt neben Alarm und Sperre seit 2d eine Entscheidung (Ergebnis
    `foreign`).
  - Der LiveFeed der app ignoriert `mode_change`. In 2a reicht die Health-Abfrage alle 10 s.
  - `FakeView` in `tests/integration/test_core_link.py` kennt `intent()`, `submit_intent()` und
    `decisions()` noch nicht.
  - Entscheidungen tragen die Wanduhr. Springt sie zurück (NTP, Failover), bekommen neue
    Entscheidungen Zeiten vor dem letzten `since`, und ein Leser, der mit dem `ts` der letzten
    weiterliest, übersieht sie. Die app gleicht darum über die `id` ab: Der Rückstand beginnt 10 min
    vor dem jüngsten `ts` der Tabelle, und doppelte `id` werden übergangen.
- **Watchdog (vor der Live-Schaltung von Victron):**
  - Nur der Punkt `evcs` von `verify_part2` verstellt vor der Probe etwas, und zwar den EVCS-Strom.
    Min-SOC, Entladegrenze und die Override-Aktion (mit `victron.live_allowed`) prüft bisher nur
    `/LastTripFailures` zusammen mit dem Log des Cerbo (Betriebshandbuch 14, „Probe“).
  - Vor der Live-Schaltung von Victron auch diese Werte vor der Probe verstellen und prüfen, dass
    die Auslösung sie zurücksetzt. Die Schritte stehen unter „Offen an der Anlage“, Punkt 4.

## Bewusst offen gelassen

Entschieden in Phase 1c; im Commit-Verlauf bzw. der Abschlussnachricht begründet:

- HA-Verbraucherwerte bis 24 h alt, ohne Altersangabe; ältere zeigen „kein Wert in den letzten
  24 h“. HA schreibt nur bei Änderung.
- CSP `connect-src 'self'`: Auf sehr alten Safari-Versionen bliebe die Live-Verbindung getrennt.
- Kein Offline-Modus, kein Service Worker.
- Ein Konflikt beim Speichern der Einstellungen (409) bietet Neuladen, aber kein Zusammenführen.
- Der Einstellungs-Editor ändert nur Werte. Zeitfenster und Gültigkeit gehen über den
  YAML-Export/-Import.
- Nach echten 30 min Leerlauf geht ein ungespeicherter Entwurf mit der Abmeldung verloren.
- Die System-Seite zeigt nicht, wenn die Brutto/Netto-Erkennung vom eingestellten Modus abweicht.
- `theme-color` folgt dem System, nicht der manuellen Wahl im UI.
- Ein E2E-Test für die Neuverbindung der Live-Anzeige fehlt. Die Logik ist mit Unit-Tests
  abgedeckt.

Entschieden in Phase 2a; die Festlegungen stehen im Plan (Präzisierungen) bzw. im Commit-Verlauf:

- Ein Teilsystem wird gesperrt, der ganze core bleibt im Betrieb: Ein fehlgeschlagener Victron-Punkt
  sperrt nur Victron (Präzisierung 1).
- Ohne Bedarf gibt es keinen Heartbeat. Ein laufender core kann den Watchdog mit der 0
  abschalten (Präzisierung 2). Seit 2b besteht Bedarf nur im Regelbetrieb (Plan 2b, Präzisierung 2).
- Sperren lösen sich in 2a nur automatisch. Das Schreibbudget (`write_budget.json`) entsteht erst
  ab 2b; 2a legt nur Format und Klasse fest.
- `PUT /v1/intent` der internen API ist in 2a der einzige Weg zu einer Absicht. Die app sendet sie
  erst ab 2d, und die API prüft die Typen der Felder nicht streng (etwa `"version": true`).
- Die Texte der Plausibilitätsprüfung aus Phase 1 zeigen den Dezimalpunkt („300.0 V“), auch unter
  „Eingangsdaten“ auf der System-Seite.
- Die Probe behandelt nur SIGINT. `docker stop` oder SIGTERM beenden sie ohne Freigabe des
  Watchdogs (Betriebshandbuch 14). Ihre Schritte „scharf“, „Auslösung“ und „Freigabe“ können
  „OK“ melden, ohne dass sich der Zustand geändert hat, wenn der Startzustand schon dem Ziel
  entspricht.
- Die `config.ini` des Watchdogs ändert man nicht von Hand (Betriebshandbuch 14). Täte man es,
  verwürfe der Watchdog einen Abschnitt mit Tippfehler im Kopf (`[actions:x]`) still. Das
  Verzeichnis nach `os.replace` sichern core und Watchdog seit 2b per fsync.
- `health()` des core kann Felder aus zwei Zyklen mischen.
- UI: Ein deaktivierter Watchdog, den der Betrieb braucht, zeigt nur eine Warnung (der core meldet
  einen Fehler-Alarm). Nach einem Verbindungsabbruch zum core bleiben die Karten „Aktoren“ und
  „Watchdog am Cerbo“ bis etwa 25 s ohne Hinweis stehen. Die Datei `ui/src/routes/system/+page.svelte`
  hat rund 500 Zeilen; bei weiterem Wachstum gehören die Karten in eigene Komponenten.

Entschieden in Phase 2c-1; die Festlegungen stehen im Plan (Präzisierungen), im Kopf des Designs und
in den Absätzen „Stand Plan 2c-1“ der Spec:

- Die EVCS folgt dem Überschuss in ganzen Ampere mit Hysterese beim Erhöhen
  (`control.evcs_hysteresis_a`, 0,5 A; Design 6.5). Ganz ruhig bleibt ihr Strom nur, solange das
  Rauschen des Anteils von Spitze zu Spitze unter der Hysterese liegt; sonst flattert er seltener,
  aber weiter. Den Wert stellt man nach dem Dry-Run nach, ohne neues Image. Ein Zeitpfad („erhöhen
  nach etwa 30 s“) fehlt bewusst: In der Messung brachte er mehr Flattern statt weniger. Bleibt der
  Überschuss ruhig knapp über einer Stufe, lässt die EVCS bis zu 1,5 A liegen; das lädt der
  Hausakku.
- Bei schnell wechselnden Wolken deckt ohne Akku-Sollwert (Victron im Dry-Run) der Hausakku einen
  spürbaren Teil der PV-Ladung des Tesla, denn der Tesla darf seinem Mittel nur alle 90 s folgen. Das
  nimmt das Design in Kauf (Design 6.5). Im Simulator (`wolkig`, Victron im Dry-Run) waren es rund
  40 % seiner Ladeenergie, roh gerechnet: Die Kennzahl „Akku-Energie in PV-Autos“ nimmt die Übergänge
  aus und zeigt dort 0 %, der Bericht nennt den rohen Anteil aller PV-Autos (18 %) als
  Informationszahl. Eine Abhilfe wäre, den Tesla im PV-Modus am vorsichtigen Minimum statt am Mittel
  zu führen; das wäre eine Designänderung und eine Entscheidung des Betreibers. Mit Victron live
  verschiebt der Akku-Sollwert den Fehlbetrag aufs Netz, solange die Unterstützung nicht gilt.
- Ohne Ströme von EVCS oder Wall Connector startet die Regelung kein Auto (Design 5.5), auch wenn die
  Grenze sonst reichte.
- Der Tessie-Adapter trennt seine Quelle bei fehlschlagenden Abfragen nicht (nur beim Abbruch). Die
  Stufe „Tessie nicht erreichbar: Tesla nicht steuerbar“ (Spec 8.4) greift an der Anlage darum erst
  über die Fehler-Sperre des Aktors oder das Veralten der Werte nach 900 s.

Entschieden in Phase 2c-2a; die Festlegungen stehen im Plan (Präzisierungen), im Kopf des Designs
und in den Absätzen „Stand Plan 2c-2a“ der Spec. Hier die Entscheidungen mit Kosten, falls sie
falsch sind:

- **Umbau:** `charging.py` und `runtime.py` sind nach dem Umbau größer geblieben als geplant
  (Zeilenzahlen: Liste „Vor der Live-Schaltung“, Punkt 7), denn die Tabellen der Tasks waren
  vollständig umgesetzt; mehr zu verschieben stand nicht im Plan (kosmetisch). Ein einziger
  bestehender Test wurde gelöscht, der das alte Verhalten festschrieb, dass eine Ausnahme der
  Regelung den ganzen Zyklus abbricht und der Keeper nicht schreibt; der neue Test
  `test_exception_in_the_regulation_keeps_the_keeper_running` deckt den Keeper ab (falls falsch: Die
  Versuchszählung des Keepers bei einer Ausnahme ist ungeprüft).
- **Alarm `control.exception`:** Er bleibt, bis ein Zyklus mit Regelung ohne Ausnahme läuft, auch
  wenn die Regelung zwischendurch ruht (etwa nach dem Abschalten des Regelbetriebs), und sein „seit“
  ist der erste gescheiterte Zyklus der Folge; der Heartbeat entscheidet dagegen je Zyklus. Sonst
  verschwände die Ursache, sobald der core nach der Auslösung des Watchdogs in SAFE geht, und der
  Alarm flatterte (falls falsch: Er steht länger als nötig).
- **Konfiguration:** Die acht neuen Felder von `ControlConfig` lehnen ausdrücklich ±∞ und NaN ab,
  weil `grid_budget_w` nie NaN oder ±∞ sein darf und ein unendliches Ziel ein unendliches `P_zul`
  ergäbe. Neun ältere Felder von `ControlConfig` (aus 2c-1) und `grid.max_import_kw` akzeptieren
  weiter +∞ (NaN und −∞ lehnen sie ab). Falls falsch: Eine Konfiguration mit `.inf` wird abgelehnt,
  was ohnehin sinnlos wäre.
- **Auffrischen:** `refresh` wirkt nur beim Netz-Sollwert, auch in der Dringlichkeit der
  Safety-Schicht und in der stillen Regel des Ausführers. Nach einer erkannten fremden Änderung hat
  die neue Erwartung keine Frist, denn sie ist dann der fremde Wert, nicht der eigene Override. Ein
  Override, dessen Alter nach einem Neustart unbekannt ist (der Cerbo hielt ihn schon), wird sofort
  aufgefrischt (falls falsch: ein Schreibauftrag mehr nach Start oder Keeper). Der Akku-Sollwert
  ignoriert Urteile und Ergebnisse fremder Schlüssel, damit keiner den Takt des Auffrischens
  verschiebt.
- **Akku-Sollwert:** `cars()` nennt jeden Ladepunkt, auch den eines abgesteckten Fahrzeugs an seiner
  Standard-Wallbox (0 W). Ein nicht endliches Budget macht das Ziel unbekannt. `W'` rechnet mit dem
  auf 10 W abgerundeten Budget, sonst klemmte Regel 2 fast jede Entscheidung, bei der das Budget
  bindet, auf `P_zul` (kein Vielfaches von 10 W, `range.ceiling` im Log); falls falsch, läge der
  Override bis zu 10 W unter dem Budget. Die Invarianten aus Design 10.4 (nie negativ, nie mehr als
  der Fehlbetrag, mehr erlaubter Akku-Anteil ergibt nie mehr Netzbezug) prüft ein Property-Test auch
  am Bedarf aus `assess`.
- **Wächter:** Er integriert Boost nach dem Halteprinzip der Abrechnung, und beide teilen
  `HOLD_MAX_S` (5 s). Integrierte er über Lücken bis 10 s, die Abrechnung aber nur bis 5 s, zöge er
  Energie ab, die in `grid_wh` nie stand (`E_bisher` zu klein, `P_zul` zu groß). Springt die Wanduhr
  zurück, ist die Viertelstunde lückenhaft. `exceeded` hat bis zum Ende der Viertelstunde Vorrang
  vor `gap`. Eine `QuarterObs` mit anderem Beginn als die laufende Viertelstunde zählt wie keine,
  ein kurzer Ausfall von `grid_w` (bis 10 s) rechnet mit `grid_wh` und dem Mittel weiter, und ein
  `P_zul`, das keine endliche Zahl ist, macht den Schritt zu einem, der nicht rechnet. Der Alarm
  `peak.exceeded` endet mit seiner Viertelstunde, auch wenn die Regelung ruht
  (`Regulation.alarms(wall_now)`). Nur ein Auto an einem geregelten Ladepunkt wird gedrosselt;
  ungeregelte Autos ohne Boost gehen gar nicht in den Wächter.
- **Drosselung:** Eine Senkung, die die Sicherung allein verlangt, bleibt dringend, auch wenn die
  Grenze des Wächters niedriger liegt (Sicherheit vor Wortlaut; falls falsch: ein dringender Befehl
  mehr). Wechselt ein gedrosseltes Auto zwischen PV-Überschuss, Min+PV und Sofort, verliert es seine
  Grenze still, ohne neue Wartezeit: Sonst behielte ein Auto, das von PV (0 W) auf Sofort wechselt,
  eine Grenze unter dem Boden von Sofort. Das gilt auch für ein Auto, das geht oder in Boost oder
  Aus wechselt (falls falsch: nach einem Moduswechsel kurz mehr Netzbezug, bis die nächste
  Drosselung nach 30 s greift). Die Senkung je Auto ist nie negativ; ohne Leistung zählt ein
  gedrosseltes Auto als nicht gefolgt. `peak_guard.py` blieb ungeteilt, nach der Nachrunde mit 661
  Zeilen (falls falsch: ein späterer Umzug der Drosselfunktionen nach `peak_throttle.py`).
- **Simulator:** `_Boost` trägt das absolute Ende des Boosts. Die Kennzahl der Akku-Energie im Auto
  überspringt je Entladefolge die ersten `TRANSITION_S` s, ohne den Tesla-Fall. Ein Auffrischen im
  Dry-Run zählt dort als eigener Wert ohne Entscheidung, sonst meldete die Kennzahl falsche
  Wiederholungen.
- **Szenarien:** Zwei haben einen angepassten Aufbau (Betriebshandbuch, „Simulator“), beide aus
  demselben Grund: Ihre Prüfung ließ sich im Modell sonst nicht erfüllen, obwohl die Regelung Plan
  und Design folgte. `akku-minpv` startet den Tesla bei 5 A (mit 16 A lädt er 90 s voll, Regel 2
  kappt bei 8 kW), `schieflast-pv` bringt die Last auf L1 erst nach 10 min (Notiz unten).
  `spitze-15min` startete den Tesla bis zur Nachrunde ebenfalls bei 5 A; mit ihren Regeln besteht
  es alle Prüfungen auch mit 16 A, wie im Plan. Seine Prüfung der Reihenfolge belegt trotzdem wenig
  und ist im Handbuch so benannt: Den e-Golf drosselt der Wächter erst, wenn der Tesla schon am
  Mindeststrom lädt, und eine falsche Reihenfolge fiele in der ersten Viertelstunde mit einer
  Senkung des Phasenwächters zusammen. Die Reihenfolge belegen die Unit- und Property-Tests der
  Drosselung (falls falsch: kein Ende-zu-Ende-Beleg; ein Szenario mit einem PV-Auto über dem
  Mindeststrom im Wächter wäre nötig).
- **Kein Totband in der Drosselung:** Im Modell war sie nicht nötig (seit der Nachrunde gibt es dort
  eine kleine Nachdrosselung aus dem alten Mittel, unten); die Beobachtung an der Anlage steht unter
  „Offen an der Anlage“, Punkt 4 (falls falsch: kleine Nachdrosselungen und verzögerte Aufhebung,
  begrenzt durch die Marge).

Der Gesamtreview von Plan 2c-2a (15.10.2026) fand zwei wichtige Lücken in der Verdrahtung des
15-min-Wächters und zwei Kleinigkeiten. Die Fix-Welle danach hat alle behoben, die beiden Lücken und
das Totband je mit einem Test, der vorher scheiterte; die Präzisierungen 9 und 10 des Plans, die
Absätze „Stand Plan 2c-2a“ der Spec, der Kopf des Designs 2c-2 und das Betriebshandbuch sind
nachgezogen.

- **Gewollter Netzbezug nur bis zur festen Grenze von Regel 2:** Der Wächter verglich `Basis + W`
  ungekappt mit `P_zul`. Regel 2 begrenzt den Override aber auf `max_grid_charge_setpoint_w`, die
  Absicherung und `max_import_kw`, und darüber liefert der Akku (Design 2c-2, Entscheidung 3). Zwei
  Autos in Sofort mit zusammen 18 kW wären bei einem Ziel von 10 kW um rund 10 kW gedrosselt worden,
  obwohl der Netzbezug ohnehin bei 8 kW blieb. Jetzt gibt der Kern dem Wächter `W` höchstens bis zu
  dieser Grenze, und solange Victron gesperrt oder nicht steuerbar ist, zählt nur das Mittel. In
  `spitze-15min` (Ziel 8 kW, `P_zul` knapp unter der Grenze) drosselte der Wächter den e-Golf danach
  je Viertelstunde in Stufen von 1 A statt in einem Schritt auf 9 A. Diese Stufen und die langsame
  Senkung ohne Budget, die die Kappung mit sich brachte, hat die Nachrunde beseitigt (unten).
- **Boost-Anteil mit PV und Akku zuerst an Boost:** Den Netzanteil, den der Wächter für Boost abzieht,
  nahm er aus der Verteilung der Abrechnung, die PV und Akku in der Reihenfolge von `config.yaml`
  verteilt (erst die EVCS). Lud der Tesla in Boost und der e-Golf in Sofort, buchte sie den Netzbezug
  des e-Golf auf den Wall Connector, und der Wächter zog ihn als Boost ab: im Simulator 1,51 kWh je
  Viertelstunde bei einem Ziel von 5 kW, ohne `peak.exceeded`. Jetzt rechnet die Verdrahtung den
  Anteil aus denselben abgeleiteten Größen ein zweites Mal, mit PV und Akku zuerst für die Wallboxen
  in Boost; dieselbe Probe blieb bei 1,24 kWh, seit der Nachrunde bei 1,20 kWh (unten; Test in
  `tests/integration/test_sim_scenarios.py`).
  Wer in Boost ist, weiß der Link aus dem letzten Zyklus des Kerns (einen Zyklus alt, so
  entschieden).
- **Kleinigkeiten:** `battery.override_w` in Health ist der angeforderte Wert vor den Regeln 2 und 4
  (jetzt so dokumentiert; was der Cerbo hält, zeigt `actual_setpoint_w`), und
  `control.setpoint_deadband_w` muss über 0 liegen. Dazu kamen Tests für den Stream in einem
  gescheiterten Zyklus und für die Frist der Erwartung eines Overrides (`_lapses_at`), die Reihenfolge
  der Inbetriebnahme im Kopf des Designs 2c-2 und kleine Korrekturen in dieser Übergabe und im
  Betriebshandbuch.
- **Offen:** Ein Szenario Ende zu Ende mit einem PV-Auto über seinem Mindeststrom im Wächter würde die
  Lücke der Prüfung der Reihenfolge in `spitze-15min` schließen (oben, „Szenarien“).

Die Nachrunde zu Plan 2c-2a (15.10.2026) hat die beiden Restpunkte der Drosselung geschlossen, die
nach der Fix-Welle blieben, jeden mit Tests, die vorher scheiterten; die Präzisierungen 10 und 11
des Plans, die Spec (7.4), der Kopf des Designs 2c-2 und das Betriebshandbuch sind nachgezogen.

- **Senkung bei einem Ziel unter der festen Grenze:** Mit der Kappung bei `C − Basis` blieb die
  verlangte Senkung `C − P_zul`, solange die Autos mehr als die feste Grenze `C` wollten. Mit Budget
  für den Akku senkte der Wächter so nach jeder Wartezeit erneut um diesen Unterschied und nahm dem
  Akku, was er nach Entscheidung 3 über `C` liefert; ohne Budget hält Regel 2 den Override bei `C`,
  und der Netzbezug lag minutenlang über `P_zul`, während der Wächter in Stufen senkte. Jetzt
  bekommt der Wächter `W` ungekappt und kennt `C` selbst. Mit Budget rechnet er mit dem Netzbezug
  ohne seine eigenen, gemerkten Senkungen und senkt insgesamt nur um den Teil unter `C`, der über
  `P_zul` liegt, und das einmal; ohne Budget senkt er, solange `P_zul` unter `C` liegt, in einem
  Schritt um alles, was über `P_zul` liegt, und sonst nicht (Fix-Runde, unten). In `spitze-15min`
  gibt es statt 20 Entscheidungen mit `peak_guard` an der EVCS (Stufen bis
  10 oder 9 A) noch 2, je einmal von 16 auf 15 A; der e-Golf lädt dort 8,85 statt 7,33 kWh, der
  Akku liefert 6,86 statt 5,24 kWh (mit dem alten Aufbau, den Tesla bei 5 A, wären es 8,76 und
  6,58 kWh), und die geprüften Viertelstunden liegen bei 1,961, 1,964 und 1,964 kWh (vorher 1,955,
  1,918 und 1,918, Grenze 2,02). Falls falsch: Eine gemerkte Senkung zählt auch, wenn ihre Grenze
  nicht mehr bindet, etwa beim Tesla, der von selbst auf 5 A ging; den Unterschied von höchstens
  `C − P_zul` liefert dann bis zum Ende der Viertelstunde der Akku.
- **Drosselung bei der echten Leistung:** Ein Auto mit Grenze sank bisher von der Grenze aus. Lag
  sie weit über seiner Leistung, etwa beim Tesla in Min+PV, den der Wächter bei 16 A drosselte und
  der ohne PV inzwischen mit 5 A lädt, senkte jede weitere Drosselung nur diese Grenze, ohne
  Wirkung. Jetzt setzt sie beim kleineren von Grenze und Leistung an: Der Tesla an seinem Boden
  wird übersprungen, und die Senkung geht an den e-Golf. Eine Folge zeigt die Boost-Abwandlung von
  `spitze-15min` (Ziel 5 kW): Dort liegt der Netzbezug 3 s lang über dem Override, bis der e-Golf
  seiner ersten Grenze folgt, und 30 s später verlangt das Mittel daraus noch 93 W. Diese Senkung
  fiel vorher in den Abstand zwischen der Grenze (4711 W) und dem Strom in ganzen Ampere (10 A,
  4600 W); jetzt geht der e-Golf von 10 auf 9 A, und die Viertelstunde liegt bei 1,20 statt
  1,24 kWh. Falls falsch: Eine kleine Senkung aus dem alten Mittel kostet an der Anlage ein Ampere
  bis zum Aufheben; ein Totband in der Drosselung (Punkt 4 unter „Offen an der Anlage“) finge sie.
- **`spitze-15min` mit 16 A:** Das Szenario startet den Tesla wieder wie im Plan und besteht alle
  Prüfungen; die erste Senkung der Viertelstunde ab 18:00 trifft dort den Tesla, den e-Golf erst
  die ab 18:15. Was die Prüfung der Reihenfolge damit belegt, steht oben unter „Szenarien“.
- **Fix-Runde nach dem Review der Nachrunde:** Ohne Budget zählte `Basis + W` zunächst immer ganz,
  auch wenn `P_zul` über `C` lag. Regel 2 hält den Override aber auch ohne Budget bei `C`, und der
  Netzbezug blieb dann ohnehin unter `P_zul`: Bei einem Ziel von 10 kW (`P_zul` 9,7 kW) und einem
  SOC an der Schwelle drosselte der Wächter zwei Autos in Sofort mit 18,4 kW um 8,7 kW (e-Golf auf
  2760 W, Tesla auf 6940 W), obwohl der Netzbezug bei 8 kW blieb; ein SOC, der um die Schwelle
  pendelte, löste dieselbe Drosselung aus. Jetzt zählt `Basis + W` ohne Budget nur ganz, solange
  `P_zul` unter `C` liegt, sonst bis `C`; was die Autos darüber wollen, liefert der Akku wie ohne
  Ziel, und ein Akku an seiner Reserve zeigt sich im Mittel. Die Szenarien ändern sich dadurch
  nicht (in `spitze-15min` hat der Akku immer ein Budget). Außerdem beschreibt das Handbuch die
  Schwelle des Aufhebens jetzt richtig: Wollen die Autos mit Budget mehr als `C`, hält der Override
  den Netzbezug bei `C`, sobald `P_zul` darüber liegt, und das Mittel zählt mit; eine Grenze fällt
  erst, wenn `P_zul` die Grenze `C` um 1 kW plus ihre Senkung übersteigt (im Simulator bei rund
  9,3 kW), nicht schon 1 kW darüber. Falls falsch: Ohne Budget verlangt der Wächter knapp unter
  `C` die ganze Senkung, knapp darüber keine; den Sprung macht die Grenze von Regel 2.

Notizen für später aus Plan 2c-2a:

- **Unstimmigkeit aus 2c-1:** Der Überschuss zählt die Ladeleistung des Akkus als verfügbar für die
  Autos (`battery_until_soc`, über 30 % SOC), der Phasenwächter zählt dieselbe Ladeleistung als
  feste Last der Phase. Bei einer schweren Einphasenlast startet ein PV-Auto deshalb nicht, solange
  der Akku lädt, obwohl es mit rund 9 A im Importziel liefe; es startet, sobald der Akku voll ist.
  Das Szenario `schieflast-pv` weicht darum aus (Last erst nach 10 min). Eine Entscheidung über
  Design 2c-1, 5.1 gehört dem Betreiber.
- **Wächter und Abrechnung teilen `HOLD_MAX_S`:** `E_bisher` kommt aus der Abrechnung (Design 2c-2,
  6.1), also fehlen darin Intervalle über 5 s und Zyklen ohne Bilanz, solange der Wächter sie nicht
  als Lücke erkennt (über 10 s). Das steht im Betriebshandbuch („Akku und Spitzen“, „Messung“).

Aufgeschobene Kleinigkeiten aus Plan 2c-2a (die inhaltlichen; sie sind klein, keine blockiert
etwas):

- **Doppelte Definitionen:** Die Regel „`refresh` zählt nur beim Netz-Sollwert“ steht dreimal
  (`core/safety/layer.py`, `core/control/executor.py`, `tools/sim/runner.py`); ein gemeinsames
  Prädikat neben `Command` verhinderte Abweichungen. Den Alias `_Key` für (Aktor, Art) gibt es in
  `executor.py`, `foreign.py`, `safe_state.py`, `runtime.py` und `tools/part2/session.py`; er ließe
  sich zusammenführen, wenn `Key` in `shared/control` läge. Den Schlüssel (`victron`,
  `grid_setpoint`) gibt es in `battery.py` und `regulation.py` doppelt, die Menge der
  Unterstützungs-Modi in `battery.py` und `charging.py`. Die Platzierung von `ControlCommand`,
  `LastValues` und `ok_number` ist plangemäß, aber schwach. `MovingAverage` liegt in
  `tesla_policy.py` und trägt einen Docstring „Zeit der Safety-Schicht“, der Wächter nutzt es auf
  der Wanduhr.
- **Grenzfälle im Verhalten:**
  - `health().peak` zeigt nach dem Ende einer überschrittenen Viertelstunde weiter `exceeded`,
    solange die Regelung ruht (nur Anzeige; der Alarm endet).
  - Ein eigener Override, der im Totband über einem inzwischen gefallenen Budget liegt, wird beim
    Auffrischen von Regel 2 auf einen Wert ohne 10-W-Raster geklemmt (nur wenn `P_zul` fällt, etwa
    in einer überschrittenen Viertelstunde).
  - `lowered` ist auch wahr, wenn das Abrunden auf 10 W `W'` um weniger als 5 W kürzt und das Ziel
    gleich bleibt; dann steht `peak_guard` an einem Wert, den der Wächter nicht geändert hat
    (schmales Band).
  - Im Zyklus eines Moduswechsels wendet die Laderegelung die Grenze des alten Modus noch einmal an
    (begrenzt auf 1 s).
  - Die Grenzen in W gegen ganze Ampere und die Nennspannung: Eine kleine Senkung (Rauschen) startet
    `SETTLE_S` neu, ohne den Strom zu ändern, und das „ausstehend“ eines Autos am Boden wird über
    230 V nie 0. Die Marge fängt es; ein Totband wäre eine Entscheidung des Betreibers (siehe „Offen
    an der Anlage“).
  - Ein dringender Befehl mit `refresh` bekommt `urgent.ignored` mit dem Text „nicht Richtung
    sicherer Zustand“, obwohl nur `refresh` die Dringlichkeit verhindert.
  - `projected_kw` und `reduction_kw` in Health können bei physikalisch unsinnigen Eingaben
    unendlich werden (nur das Budget ist garantiert endlich); `MovingAverage.value` wirft bei einer
    überlaufenden Summe einen `OverflowError`, und die Regelung setzte dann mit `control.exception`
    aus. Beides ist physikalisch unerreichbar.
- **Simulator:** Das Modell hebt den Override bei einer Auslösung des Watchdogs nicht auf (der echte
  Watchdog tut es mit `victron.live_allowed`), und kein Szenario verbindet heute den Ausfall
  `cerbo`, den Watchdog und Victron live. `sim_config` prüft `control` nur feldweise (die Prüfung
  von `import_margin_a` gegen das Importlimit läuft nicht erneut). Die Verstoß-Sätze einiger
  Szenario-Prüfungen runden ihren Wert (ein Verstoß um 0,001 läse sich wie die Grenze); je ein
  Aufruf von `shown_number` behöbe das.
- **Tests:** Es fehlen Tests dafür, dass der Modus des Gastes aus `intent.guest.mode` kommt (über
  `Regulation.step`) und dass `start_cycle` den Merker `_regulated` zurücksetzt; die Tests für
  `_lapses_at` und für den Stream in einem gescheiterten Zyklus kamen mit der Fix-Welle nach dem
  Gesamtreview. Die Hilfe für die 8-kW-Konfiguration steht dreimal in Tests,
  `test_runtime_regulation.py` holt Hilfen aus `test_runtime_charging.py` statt aus einem
  gemeinsamen Modul, und `test_control.py` pinnt die letzten beiden Felder von `Command` sowie
  `test_config.py` den genauen Leerraum der Beispielzeile `peak_target_kw`.

Entschieden in Phase 2c-2b; die Festlegungen stehen im Plan (Präzisierungen), im Kopf des Designs
2c-2 und im Absatz „Stand Plan 2c-2b“ der Spec (7.8). Hier die Entscheidungen mit Kosten, falls sie
falsch sind:

- **Ausschluss (`elsewhere`):** Er stützt ein Fahrzeug nur, wenn Tessie es „zu Hause und angesteckt“
  meldet, nicht schon bei „angesteckt“ (der Plan sagt „angesteckt“). Ein Auto, das woanders angesteckt
  ist (an einer Ladesäule), steht nie an unserer Wallbox; wörtlich ergäbe der Tesla an einer Ladesäule
  plus ein dreiphasiger Gast bei uns einen falschen Widerspruch (Tessie schließt aus, Ausschluss und
  Phasen stützen). Falls falsch: Bei unbekanntem Ort fehlt dieses Indiz; Tessie liefert den Ort
  praktisch immer.
- **Stromvergleich und zeitliche Korrelation** geben kein Indiz für ein Auto, das Tessie als „nicht zu
  Hause“ meldet (ein unbekannter Ort zählt nicht als „woanders“). Sonst ergäbe ein Tesla an einer
  öffentlichen AC-Säule mit 16 A auf drei Phasen neben einem dreiphasigen Gast bei uns mit 16 A auf
  drei Phasen einen falschen Widerspruch. Falls falsch: nichts Messbares; ein Auto unterwegs steht nie
  an unserer Wallbox, und Tessie schließt es ohnehin aus.
- **„Erkannt“:** Die 60 s zählen die Kandidatur an der Wallbox; „an keiner anderen Wallbox Kandidat“
  gilt je Zyklus. Nach einem kurzen Konflikt ist ein Fahrzeug sofort erkannt, wenn seine 60 s an der
  verbliebenen Wallbox schon liefen. Falls falsch: eine um bis zu 60 s frühere Erkennung nach einem
  Konflikt; ein Konflikt über 60 s macht ohnehin „unklar“, das 60 s nachwirkt.
- **Zwei erkannte Fahrzeuge an einer Wallbox:** Keins gilt als erkannt, und das ist kein Widerspruch
  (der Plan: „gilt keins“). Falls falsch: Die Wallbox fiele auf „unklar“ statt auf „angenommen“.
- **Boost-Bindung:** Ein Boost ohne Übersteuerung bindet an den Ansteckvorgang der Wallbox, an der das
  Fahrzeug im vorigen Zyklus stand (erkannt, angenommen oder an seiner Standard-Wallbox mit laufendem
  Vorgang), solange dieser Vorgang noch läuft; sonst wie bisher an die Standard-Wallbox (laufender
  oder nächster Vorgang). Sonst wirkte ein Boost für den an der EVCS erkannten Tesla dort nicht und
  spränge beim nächsten Vorgang am Wall Connector an. Falls falsch: Der Boost wirkt an der Wallbox, an
  der die Zuordnung das Auto sieht; liegt sie falsch, wirkt er am falschen Auto bis zum Abstecken.
  Die bekannte Grenze steht unten.
- **Grund `assignment_detected`:** nur bis zum ersten Zyklus mit Befehlen für das Auto (Präzisierung 8
  wörtlich). Ein zurückgestellter erster Befehl kommt ohne Grund wieder, und ist der Tesla bei der
  Erkennung nicht steuerbar, trägt sein späteres `max_a` den Grund nicht. Falls falsch: ein fehlender
  Grund an diesen späteren Befehlen im Log.
- **Szenario `tesla-evcs`:** Es prüft „erkannt“ vor 3 min statt binnen 2 min. Die Regelung beginnt im
  Simulator erst nach rund 60 s; Design 10.3 „binnen 2 min“ zählt ab Beginn der Regelung. Falls
  falsch: eine um 60 s lockerere Prüfung.
- **Prüfung `_all_charge_from`** („Ab 60 min laden beide Autos“, in `ausfaelle`) übergeht den Gast:
  „beide Autos“ sind die zwei Fahrzeuge der Konfiguration. Falls falsch: eine Prüfung, die einen Gast
  nie verlangt.
- **Health je Wallbox:** `occupant` ist `null` ohne Vorgang und auch während eines Vorgangs, solange
  die Zuordnung unklar ist und an der Wallbox kein Fahrzeug der Konfiguration steht (`assignment` ist
  dann `unclear`). Wer `wallboxes` liest, muss `null` also auch bei einem Vorgang abfangen (Betriebshandbuch,
  „Zuordnung Auto und Wallbox“).
- **Prüfungen `_clear_from` und `_occupied_from`** des Simulators melden zusätzlich „Der Lauf endet vor
  {from_s} s.“, wenn nach `from_s` keine Gesundheit mehr kommt (wie andere Prüfungen des Moduls; der
  Satz fehlt in „Texte“). Falls falsch: ein zusätzlicher Befundsatz.
- **Elimination (Tessie und Home Assistant bei mehreren Vorgängen):** Sie stützen ein Fahrzeug an
  einer Wallbox mit Vorgang nur, wenn Stromvergleich oder Phasen-Signatur es an jeder anderen Wallbox
  mit Vorgang ausschließen; bei genau einem Vorgang gilt das immer. Ausschließen tun sie wie bisher an
  jeder Wallbox mit Vorgang (Abweichung von Design 7.2 und von Präzisierung 2; entschieden nach dem
  Review der Doku). „Zu Hause (und angesteckt)“ sagt nicht, an welcher Wallbox das Fahrzeug steht:
  Zusammen mit der Phasen-Signatur erkannten Tessie und Home Assistant sonst einen dreiphasigen Gast
  an der EVCS als den Tesla, der nicht ladend am Wall Connector steckt (der Wall Connector zeigte
  „Gast, angenommen“), und einen zweiphasigen Gast am Wall Connector als den e-Golf, der „geladen“ an
  der EVCS steckt. Im Kernfall, e-Golf zweiphasig am Wall Connector und Tesla dreiphasig an der
  EVCS, sind beide erkannt. Für die Elimination zählen nur Strom und Phasen; eine Übersteuerung eines
  anderen Fahrzeugs an die andere Wallbox zählt nicht (nach dem Gesamtreview entschieden; ein
  übersteuertes Fahrzeug ist an jeder anderen Wallbox ohnehin ausgeschlossen). Falls falsch:
  Kommt ein zweiter Vorgang hinzu, fällt eine Erkennung, die auf Tessie oder Home Assistant beruht,
  auf „angenommen“ zurück, bis die andere Wallbox das Fahrzeug ausschließt (60 s Laden dort) und es
  60 s Kandidat war, und so lange, wie dort nichts lädt. Die Abweichung beim Widerspruch
  (Präzisierung 4) greift seitdem nur noch, wenn die zeitliche Korrelation an der anderen Wallbox
  stützt.
- **Tessie nach dem Ende eines Vorgangs** (nach dem Gesamtreview): Endet an einer Wallbox ein
  Vorgang (sie meldet kein Fahrzeug mehr, oder an ihr beginnt ein neuer), stützen Tessie und
  Ausschluss ein Fahrzeug an einer anderen Wallbox erst wieder mit einem Wert, dessen Abfrage
  (`plugged_at`) mindestens 60 s nach diesem Ende lag; ohne `plugged_at` nicht mehr. Ausschlüsse
  durch Tessie gelten sofort; Stromvergleich, zeitliche Korrelation und Home Assistant bleiben.
  Sonst galt der Tesla, der den Wall Connector verlässt, mit Tessies veraltetem „angesteckt“ an der
  EVCS als erkannt, an der der e-Golf ohne zu laden steckt: Er bekam `max_a`, und die EVCS lud kurz
  im Modus des Tesla. Falls falsch: Eine Erkennung, die auf Tessie und Ausschluss beruht, kommt nach
  dem Ende eines Vorgangs an der anderen Wallbox erst 60 s nach der ersten Abfrage, die mindestens
  60 s nach dem Ende liegt; bis dahin bleibt das Fahrzeug angenommen. Das unterbricht auch eine
  laufende Erkennung: Ist der Tesla nicht ladend an der EVCS über Tessie und zeitliche Korrelation
  erkannt und verlässt ein zweiphasiger Gast den Wall Connector, zeigt die EVCS rund 2 min lang
  „angenommen“ (e-Golf oder Gast), dann ist der Tesla neu erkannt, mit einem neuen
  `assignment_detected`.
- **Stromvergleich** (nach dem Gesamtreview): Meldet Tessie eine andere Phasenzahl als
  `vehicles.<name>.phases`, sagt er nichts (unplausibel, also unbekannt). „Passt nicht“ zählt an
  einer Wallbox erst mit einem Wert, dessen Abfrage mindestens 60 s nach der letzten Änderung der
  Ströme dieser Wallbox lag; erst dann beginnen die 60 s des Ausschlusses. Änderungen: eine Phase
  um mehr als 1 A, eine Phase beginnt oder endet zu laden, Laden beginnt oder endet, die Ströme
  werden bekannt oder unbekannt, der erste beobachtete Zyklus der Wallbox. „Passt“ bleibt. Sonst
  schloss der Stromvergleich den Tesla an seinem eigenen Wall Connector aus, wenn Tessie eine
  Stromänderung erst nach 60 s meldete oder eine falsche Phasenzahl: dort ein Gast, nach 60 s
  „unklar“, die Tesla-Befehle pausierten. Falls falsch: Ein Fahrzeug, das nur der Stromvergleich
  ausschließt, ist frühestens 60 s nach der ersten Abfrage ausgeschlossen, die 60 s nach der letzten
  Stromänderung liegt, und nie, solange sich der Strom öfter ändert; bei falscher Phasenzahl fehlt
  der Stromvergleich ganz.
- **Beobachtete Zeit** (nach dem Gesamtreview): Liegen zwischen zwei Zyklen der Erkennung mehr als
  60 s (die Regelung ruhte), beginnen alle 60 s von Indizien und Entscheidung neu; der erste Zyklus
  danach zählt wie der erste überhaupt, für den Stromvergleich auch als Änderung der Ströme. Die
  Zeiten von „unklar“ bleiben. Falls falsch: Nach einer Pause dauert jede Erkennung wieder
  mindestens 60 s, ein Ausschluss durch den Strom 2 min.

Offene Punkte aus Plan 2c-2b (nicht umgesetzt):

- **`tools/verify_part1.py`** (Prüfprotokoll 17.1-11) prüft nur `homeassistant.entities`, nicht
  `home_entity`, denn der core ergänzt diese Zuordnung erst zur Laufzeit (`ha_entities`). Ob der core
  den `device_tracker` liest, zeigt bis dahin Health: Läuft nur an einer Wallbox ein Vorgang und ist
  das Fahrzeug zu Hause, steht dort das Indiz `home` unter `indicators`. Bei zwei Vorgängen steht es
  dort erst nach der Elimination, wenn Strom oder Phasen das Fahrzeug an der anderen Wallbox
  ausschließen; „nicht zu Hause“ steht an jeder Wallbox mit Vorgang sofort.
- **`home_entity`** ersetzt einen gleichnamigen Eintrag in `homeassistant.entities` (das Signal
  `vehicle.<name>.home_state`) still; kein Test pinnt den Vorrang.
- **Übersteuerung und Elimination:** Eine Übersteuerung eines anderen Fahrzeugs an die andere
  Wallbox zählt für die Elimination nicht (entschieden nach dem Gesamtreview); ein übersteuertes
  Fahrzeug ist an jeder anderen Wallbox ohnehin ausgeschlossen. Ist etwa der e-Golf an den Wall
  Connector übersteuert und steht der Tesla an der EVCS, ohne zu laden, bleibt dort „Gast,
  angenommen“ mit dem Modus des Gastes, bis auch der Tesla übersteuert ist.
- **Phasenzahl der EVCS:** Der core leitet sie aus Leistung und Strom ab (`derived.py`), die nicht
  gleichzeitig gemessen werden (Leistung über MQTT vom GX, Strom über Modbus). Ein einziger
  abweichender Zyklus startet die 60 s der Phasen-Signatur neu; ein Gast an der EVCS, den bei
  anwesendem e-Golf nur die Phasen vom e-Golf trennen, kann so kurz zum e-Golf wechseln und zurück.
  Geprüft wird das im Dry-Run (oben, „Vor der Live-Schaltung von EVCS und Tesla“); Abhilfen wären
  die Leistung je Phase des GX (`wallbox.<id>.l<n>.power_w`) oder eine Toleranz von ein, zwei
  Zyklen.
- **Tessies Zeitstempel:** `plugged_at` ist die Zeit der Abfrage von BindaEMS (Präzisierung 9),
  nicht Tessies eigener Zeitstempel (`charge_state.timestamp`). Tessie antwortet aus seinem Cache;
  der eigene Zeitstempel würde die Regeln für Beginn, Ende und Stromänderung genauer machen. Das
  verlangt eine Entscheidung.
- **Simulator:** Sein Tessie meldet bei jeder Abfrage die Werte des Modells, und die EVCS meldet
  Leistung und Strom aus demselben Schritt. Es fehlen Regler für die Verzögerung von Tessie und der
  Leistung der EVCS; die Fälle des Gesamtreviews (veraltetes „angesteckt“, nachhinkender Strom,
  abweichende Phasenzahl der EVCS) pinnen so nur Unit-Tests.
- **Boost für ein Fahrzeug, dessen Standard-Wallbox ein anderes erkanntes Fahrzeug belegt,** geht
  verloren: Ein Boost für den e-Golf, während der erkannte Tesla die EVCS belegt, bindet an den Vorgang
  des Tesla und endet mit ihm (wie in 2c-1 bei Übersteuerung). Nach dem Abstecken des Tesla braucht es
  eine neue Version der Absicht.
- **Spätes Ankommen:** Meldet `home_entity` das Ankommen des e-Golf spät, gilt er bis dahin als Gast
  (angenommen) und lädt im Gast-Modus (`guest.mode`, Standard PV-Überschuss), nicht im Modus des e-Golf.
  Das Betriebshandbuch nennt es; ob der Tracker an der Anlage schnell genug ist, zeigt die
  Inbetriebnahme Punkt 3. Dasselbe gilt, solange ein Tracker zu Hause kurz `not_home` meldet.
- **Gast-Erkennung:** Erkannt ist ein Gast erst, wenn an seiner Wallbox jedes Fahrzeug der
  Konfiguration ausgeschlossen ist; bis dahin steht dort „Gast, angenommen“ oder das
  Standardfahrzeug. Ein Fahrzeug, das zu Hause angesteckt ist, schließen an der Wallbox des Gastes
  nur Strom oder Phasen aus, nicht Tessie oder Home Assistant. Ein dreiphasiger Gast an der EVCS neben
  dem Tesla, der nicht ladend am Wall Connector steckt, bleibt so „Gast, angenommen“, der Tesla
  angenommen am Wall Connector (vor der Elimination: der Tesla erkannt an der EVCS, am Wall Connector
  ein Gast); lädt der Tesla mit anderem Strom, ist der Gast nach rund 2 min erkannt, solange sich der
  Strom an der EVCS nicht ändert (ändert die Regelung ihn oft, etwa alle 45 s, bleibt dort „Gast,
  angenommen“). Ein Gast mit zwei
  Phasen sieht für den core aus wie der e-Golf, solange dieser laut Home Assistant zu Hause ist, denn
  der e-Golf meldet kein „angesteckt“: Allein an einer Wallbox ist er als e-Golf erkannt, auch wenn der
  e-Golf nicht angesteckt ist, und neben einem zweiten Auto ebenso, sobald dieses auf einer anderen
  Phasenzahl lädt und den e-Golf so an seiner Wallbox ausschließt; sonst bleiben die Wallboxen
  angenommen, etwa bei einem Gast am Wall Connector neben dem e-Golf an der EVCS: dort der e-Golf
  angenommen, am Wall Connector ein Gast, angenommen. Den dreiphasigen Gast an der EVCS neben dem
  Tesla, den zweiphasigen Gast am Wall Connector neben dem e-Golf, den Kernfall und den Rückfall einer
  Erkennung, wenn ein zweiter Vorgang beginnt, pinnen Tests in
  `tests/unit/core/control/test_assignment.py`.

Aufgeschobene Kleinigkeiten aus Plan 2c-2b (die inhaltlichen; sie sind klein, keine blockiert etwas):

- **Tests der Erkennung:** Nicht gepinnt ist die Phasen-Signatur mit einer Phase (n = 1);
  der Uhrentest erkennt nur gemischte Nutzung der beiden Uhren, nicht einen konsequenten Tausch.
  Es fehlen Controller-Tests dafür, dass ein neu erkannter Gast an seinen ersten EVCS-Befehlen
  `assignment_detected` trägt und dass der Merker eines neu erkannten Autos ein `idle` übersteht.
  `test_wallbox_health_is_empty_before_the_first_step_and_after_idle` prüft nicht, dass der Stand vor
  `idle` nicht leer war.
- **Docstrings und Doppelungen:** Der Docstring von `assignment.py` nennt die Boost-Regel ungenau
  („an dem das Fahrzeug steht“ statt „im vorigen Aufruf stand, solange dieser Vorgang läuft“).
  `_NOBODY` in `charging.py` wiederholt das Literal „ohne Vorgang“ aus `assignment.py`; ein vom
  Detektor anderswo erkanntes, übersteuertes Fahrzeug überschriebe seinen Platz in
  `_place_at_sessions` (heute unmöglich, der Detektor schließt es aus).
- **Simulator:** `model.py` nimmt für den Gast `next(iter(cfg.wallboxes))` (ohne Wallbox ein Fehler),
  überschriebe ein Fahrzeug mit dem Schlüssel `gast`, und ein unbekannter Schlüssel in `away` bleibt
  ohne Wirkung. `runner.py` wiederholt für den Gast die Feldzuordnung der Fahrzeugschleife, und
  `_clear_from` und `_occupied_from` in `scenarios.py` haben denselben Prolog („ab `from_s`
  filtern, sonst endet der Lauf“) doppelt. `zuordnung` pinnt „der Tesla ist nie kurz erkannt“ nur über
  den Test des Aufbaus.

Doku-Korrekturen aus dem Re-Review der Fix-Welle von 2c-2b (erledigt am 15.10.2026 auf
`claude/project-thread-57rkit`; der Code stimmte jeweils, nur die Texte waren ungenau; die Zeilen
gelten für `31721a6`):

- **Kosten der Sperre nach dem Ende eines Vorgangs:** Die Texte sagen, eine Erkennung über Tessie und
  Ausschluss komme nach dem Ende eines Vorgangs an der anderen Wallbox später. Die Sperre unterbricht
  aber auch eine laufende Erkennung: Ist der Tesla nicht ladend an der EVCS über Tessie und zeitliche
  Korrelation erkannt und verlässt ein zweiphasiger Gast den Wall Connector, zeigt die EVCS rund
  2 min lang „angenommen“ (e-Golf oder Gast), dann ist der Tesla neu erkannt, mit einem neuen
  `assignment_detected`. Ergänzen in `src/bindaems/core/control/clues.py` (Docstring, um Zeile
  46-50), `docs/betrieb.md` (um 1655-1657), Spec 7.8 (`2026-10-08-bindaems-design.md`, um 905), im
  Kopf des Designs 2c-2 (um 88-91) und hier („Tessie nach dem Ende eines Vorgangs“ unter den
  Entscheidungen nach dem Gesamtreview).
- **Gast nach rund 2 min erkannt:** Der Satz „lädt der Tesla mit einem anderen Strom, ist der Gast
  nach rund 2 min erkannt“ (`docs/betrieb.md` um 1789-1790 und hier unter „Gast-Erkennung“) gilt
  nur, solange sich der Strom an der EVCS nicht ändert. Ändert die Regelung ihn oft (etwa alle
  45 s), bleibt dort „Gast, angenommen“; der Absatz „Stromvergleich nach einer Änderung“ in
  `docs/betrieb.md` (um 1672-1673) sagt es richtig. Den Satz einschränken.
- **Wortlaut der Regel zu Übersteuerung und Elimination:** `docs/betrieb.md` (um 1626-1627),
  Spec 7.8 (um 908) und hier (Elimination und „Übersteuerung und Elimination“ unter den offenen
  Punkten) nennen die Übersteuerung des Fahrzeugs selbst an die andere Wallbox. Gemeint ist die
  Übersteuerung eines anderen Fahrzeugs an der anderen Wallbox; ein übersteuertes Fahrzeug ist an
  jeder anderen Wallbox ohnehin ausgeschlossen. `detection.py` (um 25-26) und `clues.py` (um 26-27)
  sagen es richtig. Im Beispiel gilt „stützen ihn dort weder `tessie` noch `elsewhere`“ nur, solange
  der übersteuerte e-Golf am Wall Connector nicht zweiphasig lädt; lädt er, stützt `tessie` den
  Tesla an der EVCS, allein reicht das nicht, und es bleibt „Gast, angenommen“.

Entschieden in Plan 2d; die Festlegungen stehen im Plan (Präzisierungen) und in den Absätzen „Stand
Plan 2d“ der Spec:

- **Bereinigung der Absicht:** Die app schickt Boost und Übersteuerung nur zurück, wo Health sie zeigt
  (oder wo sie auf einen Ansteckvorgang warten), und vertraut Health nur, wenn seine `intent_version` die
  gültige Version erreicht; sonst gilt der Stand als unbekannt (Boost in der Zukunft und Übersteuerung
  bleiben, ein abgelaufener Boost entfällt). Die Regel gilt auch für das Fahrzeug, das die Bedienung
  gerade ändert, vor der Änderung; die Präzisierung nahm es zuerst aus. Dafür hat Health die Felder
  `intent_version`, `boost_pending` und `override_pending` bekommen (falls falsch: ein alter Boost
  könnte nach einer Bedienung beim nächsten Anstecken anspringen).
- **Version der Absicht:** Millisekunden seit 1970 oder die gültige Version + 1, was größer ist. Nach
  der ersten Absicht der app braucht eine Absicht per `curl` eine Version aus `GET /v1/intent` plus 1.
- **Reserve:** Sie hält nur Strombefehle zurück, die den Strom nicht senken oder den Ist-Strom nicht
  über die Grenze des Wächters; Starts, Stopps, der Höchststrom an der EVCS und dringende Befehle gehen
  immer. Die Konfiguration lehnt `control.tesla_reserve_commands` ab, der nicht unter `per_hour`
  jedes Fahrzeugs mit Tessie liegt.
- **Selbststart:** Die Regelung vergisst nur ihren letzten Wert für „Laden an/aus“, nicht den Strom, und
  zählt nur Korrekturen, die sie senden kann. Im Modus „Aus“ ist der Befehl dringend und umgeht das
  Start/Stopp-Limit.
- **Szenario `tesla-aus`:** Es belegt „Aus wirkt binnen 10 s“, aber nicht, dass der dringende Stopp das
  Stundenlimit umgeht: Die Reserve hält das Budget unter den Wolken offen, es gibt keine Zurückstellung
  (Betriebshandbuch, „Simulator“). Das Umgehen prüfen die Tests der Regelung.
- **Quittierung:** Der core antwortet aus dem Stand des letzten Zyklus und wendet die Quittierung im
  nächsten an. Quittierbar sind Sperren, deren Ursache fehlt, und solche, deren einzige Ursache der
  Alarm einer fremden Änderung ist; sonst 409 mit der Ursache. Schreibt der fremde Regler weiter,
  sperrt die Erkennung nach 3 Prüfungen erneut.
- **Live-Schalter:** nur im UI, nur für Admins; EVCS und Tesla (jeder Aktor außer Victron) verlangen
  zusätzlich die Bestätigung, dass evcc ganz abgeschaltet ist. Die Bestätigungen entfallen, wenn sich die
  Auswahl ändert; „Auf Dry-Run“ wählt alle live angeforderten Aktoren vor und braucht kein Passwort.
  Ein Merkmal für „Prüfprotokoll Teil 2 bestanden“ gibt es nicht, darum das Häkchen. Der Dialog lässt nur
  Aktoren mit `live_allowed` wählen.
- **Entscheidungslog in der app:** Der Rückstand beginnt 10 min vor dem jüngsten `ts` (Rücksprünge der
  Wanduhr), die `id` des core ist eindeutig, die Textsuche achtet nicht auf Groß- und Kleinschreibung
  (auch bei Umlauten). Die Quelle des Rückstands sind die Dateien des core (7 Tage): War die app länger
  weg, fehlen Ältere. Strombefehle, die die Reserve zurückhält, stehen nicht im Log.
- **Home Assistant:** Gehaltene Nachrichten auf `bindaems/cmd/…` ignoriert die app. Höchstens ein Befehl
  je Sekunde und Entität; nach jedem Befehl wird der Zustand neu veröffentlicht. HA hat die Rechte von
  „Bedienen“ ohne Benutzer, aber weder Live-Schalter noch Vorrang. Die Konfiguration lehnt Fahrzeugnamen
  ab, die leer, gepolstert, ohne Beachtung der Schreibweise doppelt oder „Automatisch“ sind, und
  Schlüssel, deren Objekt-ID in Home Assistant mit einer anderen Entität zusammenfiele. Der Sensor
  „Wallbox“ zeigt den Anzeigenamen der Wallbox (eigener Name, sonst „EVCS“ oder „Wall Connector“).
- **Benachrichtigungen:** Ein Fehler-Alarm wird nach 30 s einmal gemeldet (persistente Meldung
  `bindaems_<Alarm-ID>` und, falls eingestellt, der `notify`-Dienst) und beim Verschwinden entfernt;
  höchstens 10 Meldungen je Minute, davon höchstens 4 für Ablehnungen, der Rest geht ins Log. Ein
  Alarm gilt erst als gemeldet, wenn die persistente Meldung angekommen ist; sonst versucht die app es
  jede Minute erneut, solange er besteht. Ein gescheitertes Entfernen wiederholt sie ebenso. Ohne
  `BINDAEMS_HA_TOKEN` meldet die Komponente `ha-notify` „Benachrichtigungen aus: BINDAEMS_HA_TOKEN
  fehlt.“.
- **Demo-Backend:** Der core der Demo-Welt meldet DEGRADED. Der Benutzer `bedienen` (Rolle „Bedienen“) ist
  neu.
- **Nicht in 2d:** `assignment_log` und `charging_session` (Spec 11.3) folgen mit der Statistik; manuelle
  Zuordnungen stehen im Änderungsprotokoll, erkannte in den Entscheidungen und in Health.

Aufgeschobene Kleinigkeiten aus Plan 2d (aus den Reviews; keine davon gefährdet die Anlage):

- **Quittierung aus veraltetem Stand:** Die Quittierung gilt der Erkennung, die beim Anwenden steht.
  War eine neue fremde Änderung schon vor dem Klick erkannt, aber noch nicht auf der Seite des
  Betreibers, wird sie mitquittiert (Handbuch, Abschnitt 13). Abhilfe wäre, die gesehene Erkennung mit
  der Quittierung zu senden.
- **Boost nach Neustart des core:** Der core merkt sich verbrauchte Boosts nur im Speicher. Nach einem
  Neustart, oder bei Ab- und Anstecken während die Regelung ruht, kann ein alter Boost aus der Absicht
  an den laufenden Vorgang binden.
- **`PUT /v1/intent` am core:** Der Notfallweg kennt kein Passwort (Token genügt) und übernimmt jede
  neuere Version ohne Abgleich der Basis; schreiben app und ein direkter Aufruf im selben Moment, gilt
  der spätere.
- **Benachrichtigungen:** Kehrt ein Alarm zurück, während das Entfernen seiner alten Meldung noch
  aussteht, und verschwindet er vor einer neuen Meldung wieder, bleibt die alte Meldung in Home
  Assistant stehen. Ablehnungen und Alarme teilen sich die Verbindung zu Home Assistant der Reihe nach.
- **HA-Befehle:** Die Begrenzung auf einen Befehl je Sekunde misst beim Verarbeiten, nicht beim
  Eintreffen; antwortet der core langsam, werden gestaute Befehle ausgeführt statt verworfen. Ein
  Abruf der Absicht neben einer Bedienung kann den Zustand in Home Assistant für bis zu 5 s
  zurückspringen lassen.
- **UI:** Die Bestätigungen im Live-Dialog bleiben stehen, wenn eine Aktualisierung die Auswahl über
  `live_allowed` verkleinert (sichere Richtung). Die URL-Filter der Seite „Entscheidungen“ können bei
  sehr schnellem Wechsel kurz von der Anzeige abweichen. Ein ungültiger SOC-Wert wird still
  zurückgesetzt.
- **Entscheidungslog:** Ein `before`-Zeiger auf eine aufgeräumte Zeile liefert eine leere Seite; fällt
  die Datenbank aus, wächst der Puffer der app bis zur Rückkehr.
- **Tesla:** `poll_soon` verkürzt die Wartezeit nach einem Fehler (höchstens eine Abfrage mehr je
  Anstecken). Der Simulator beschriftet Boost-Sekunden nach der Absicht, nicht nach der Regelung.
- **Tests:** Einige Prüfungen greifen auf interne Felder zu (`_acks`), die Zeitstempel-Ersetzung der
  fremden Entscheidung ist nur über den Ablauf getestet.

Entschieden in Plan 3a; die Festlegungen stehen, soweit sie das Verhalten betreffen, im Plan
(Präzisierungen) und in den Absätzen „Stand Plan 3a“ der Spec. Hier die Entscheidungen mit Kosten,
falls sie falsch sind:

- **Jede Task lässt das ganze Repository grün:** Task 1 weitet schon das UI-Schema der
  Tarifbestandteile (`v.variant('kind', …)`) und schreibt die Vertragsdateien neu; der Plan hatte das
  für Task 4 vorgesehen und Tasks 1 bis 3 rot gelassen. Falls falsch: etwas UI-Arbeit wandert zwischen
  den Tasks.
- **Texte im Tarif-Editor:** Der Plan gilt für das UI, auch wo der Server anders formuliert (Viertelstunde:
  „Uhrzeit auf einer Viertelstunde, z. B. 10:15“). Zusätzliche Prüftexte, die der Plan nicht listet
  („Name eingeben“, „Höchstens 80 Zeichen“, „Datum wählen“, „Wert größer 0“, „Wert ab N“, „Wert von 0 bis
  100“), spiegeln Prüfungen des Servers. Falls falsch: nur Wortlaute.
- **Abgelehntes Speichern:** Der Hinweis „Bitte die markierten Felder prüfen.“ steht nicht im Plan, ist
  aber nötig, damit ein gescheiterter Speichervorgang neben „Speichern“ etwas zeigt. Falls falsch: ein
  Text.
- **„Neu bewerten“:** Das Formular wird gleich nach dem Speichern geprüft, nicht erst nach dem
  Neuladen; es ist seit Phase 1c Zustand der Seite. Falls falsch: nur die Erwartung im E2E-Test.
- **HA-Historie mit Vorlauf:** Die Abfrage holt für jede Entität den letzten Wert vor dem Zeitraum
  (Seed), denn HA schreibt nur bei Änderung, und eine ruhende Wallbox (abzuziehende Entität) hat tagelang
  keinen Punkt; sonst fiele jeder Slot weg und `subtract` nützte nichts. Der Review stufte das von
  „Minor“ auf „Important“ hoch. Falls falsch: eine Seed-Abfrage mehr je Neuaufbau.
- **Zahlen im UI:** Negative Leistungen stehen mit dem ASCII-Minus aus `format.ts` („-80 W“), nicht mit
  „−80 W“ wie im Plan, denn `format.ts` ist der verbindliche Formatierer. Falls falsch: ein Formatierer
  für das ganze UI.
- **Fix-Welle nach dem Gesamtreview:** Sie nahm den wichtigen Befund (Tageserlös der Einspeisung
  ignorierte die Zeitfenster) und drei kleine Punkte (Beschriftung „Heute (ab jetzt)“, Stufe und
  Mindestverrechnung in der Tarifansicht, ElWG-Vorlage mit Name und Beginn 1.1.2027). Der dritte
  verhindert, dass ein Leistungspreis still für 2026 gilt, wenn 3b damit rechnet. Falls falsch: drei
  kleine UI-Änderungen zurücknehmen.
- **Tageserlös ohne Export:** Ein Tag ohne Einspeisung und ohne OeMAG-Wert meldet Erlös 0,0 statt
  `None`, wie `cost_eur` an Tagen ohne Bezug. Falls falsch: eine Bedingung in `summarize_slots`.
- **`newComponent` mit `existingKinds`:** Der Parameter ist Pflicht; der erste Leistungspreis wird zur
  ElWG-Vorlage, und kein Aufrufer löst sie aus Versehen aus. Falls falsch: eine Signatur in einer
  Hilfsfunktion.

Aufgeschobene Kleinigkeiten aus Plan 3a (aus den Reviews; keine davon gefährdet die Anlage):

- **Zwei Fälle, die an der Anlage zählen** (Gesamtreview, M4 und M5):
  - Der Nachtlauf um 02:30 fällt in der Nacht der Zeitumstellung im März aus (die Stunde gibt es nicht;
    der Plan schreibt 02:30 vor); der nächste Lauf holt es nach.
  - `fill(previous)` der HA-Abfrage trägt Werte über lange Ausfälle von Home Assistant fort; der Median
    dämpft es. Dazu die Risiken unter „Offen an der Anlage“, „Zu 3a gehört“.
- **Tarif, Server:**
  - `PowerComponent.tier_kw` lässt unendlich zu (so im Plan).
  - Die Fehlerpfade des Servers tragen den Tag der Union (`tariff.components.2.energy_per_kwh.value_ct`);
    das UI schneidet ihn ab.
  - Ein Einspeise- oder Gebührenbestandteil ohne Wert zählt in der Rechnung still 0; die Warnung der
    Einstellungen („hat noch keinen Wert“) deckt alle Arten ab.
  - Es fehlen Tests für den letzten gültigen Tag (einschließlich) bei Gebühren und Leistungspreis und
    für den Wechsel Dezember zu Januar in `month_peak`; die Verdrahtung von `month_peak` in
    `runtime._build_ha_inputs` ist ungetestet, und ein Kommentar im ersten Test von `month_peak` ist
    unglücklich umbrochen.
- **Tarif-Editor im UI:**
  - Das Datum prüft nur die Form (2026-13-45 geht durch den Client; der Server lehnt es ab).
  - Der Wert eines Zeitfensters heißt nur „Wert“ (nicht nach dem Modus), und die Einheit steht
    außerhalb des Labels.
  - Die Auswahlgruppen und Schalter haben keine Gruppensemantik und kein `aria-describedby`.
  - Eine Änderung der Struktur löscht alle Fehler, auch die der OeMAG-Felder; der Hinweis „Bitte die
    markierten Felder prüfen.“ bleibt danach stehen.
  - `focusFirstError` tut still nichts, wenn zu einem Fehlerschlüssel des Clients kein Element
    gezeichnet ist.
  - Die lokale Variable `window` in `WindowEditor` verdeckt das globale Objekt.
  - Tests fehlen für: einen Spotbestandteil ohne Wert, Einspeise-Zeitfenster hin und zurück, das Ende
    24:00.
- **Tagestyp und Schätzpreise:**
  - Kein Test für den Frühlingstag mit 92 Slots und für ein naives Datum in `slot_key` (ValueError).
  - Kein Test für die Untergrenze `jetzt − 28 d` (einschließlich), für die Zeitumstellung (erster
    Durchlauf von 02:00, Planung über einen Tag mit 100 Slots) und für einen einzigen Wert desselben
    Tagestyps, der auf die Stufe über alle Tagestypen fällt.
  - `planning(start < jetzt − 28 d)` kennt gespeicherte Preise vor dem Fenster nicht; es dient nur der
    Vorausplanung (Hinweis im Docstring).
- **Archiv, Kalibrierung, Prognosegüte:**
  - Der Test der Zeitumstellung im Archiv füttert UTC-Schlüssel und übt die lokalen Falten nicht;
    zwei Schlüssel für denselben Zeitpunkt würden die Zahl in `record` überzählen.
  - Der Fixture der Strahlung ist künstlich (Docstring in `tests/unit/app/forecast/conftest.py`), und
    Tests pinnen seine Literale (12,1; 11,5); `test_clearness_comes_from_first_plane` nutzt nur eine
    Fläche; der Test des Archivfehlers prüft die Warnung nicht, und die Importe von `FIXTURE`
    sind gemischt.
  - Kalibrierung: Die Grenze des 30-Tage-Fensters ist ungetestet, Tests schreiben `service._cfg`, und
    `recalibrate` tut ohne Archiv still nichts.
  - Der Nachtlauf wartet höchstens 60 s auf die Kalibrierung, und das Budget umfasst die Güte;
    löst eine Ausnahme vor dem `try/finally` aus, bleibt das Ereignis bis zum Neustart ungesetzt. Der
    Test der Wartezeit schläft echte 50 ms. Es fehlt ein Test der Zeitumstellung für
    `ForecastQuality.update`, und die Konstanten in `runtime.py` hat der Formatter umbrochen (Kommentare
    stehen darüber).
- **Lastprognose und HA-Historie:**
  - Ein Lesefehler aus Home Assistant steht nur im Log, der Status bleibt `ok`.
  - `_ha_ok` ist ein Nebeneffekt der öffentlichen `history()`; die Reihen beider Schemata können sich
    überlappen; ein leeres Profil wird stündlich neu gebaut (gewollt, Kommentar); ein ungenutzter
    `monkeypatch`-Parameter; `LoadForecast.p50_w` ist ein veränderliches Dict.
  - Der Seed mit zwei `last()`-Selektoren kann bei einer Entität in beiden Schemata eine der Reihen
    wählen; eine HA-Entität, die länger als 7 Tage schweigt, fällt weiter weg.
- **UI der Prognosen:**
  - Die Höhe des Bandes `P75 − P25` ist nicht auf 0 begrenzt; ungetestet sind der Ausschluss von „PV P25“
    in der Legende und verschiedene x-Punkte der Reihen.
- **Doku:**
  - Im Betriebshandbuch fehlt beim Band die Schwellenangabe, „`fill(previous)` auf dem Einheitenfeld“
    ist ungenau formuliert, und die Zeile 3b der Tabelle in Spec 18 kürzt den freigegebenen Wortlaut.

Entschieden in Plan 3b; die Festlegungen stehen, soweit sie das Verhalten betreffen, im Plan
(Präzisierungen) und in den Absätzen „Stand Plan 3b“ der Spec. Hier die Entscheidungen mit Kosten,
falls sie falsch sind:

- **Optimierer:**
  - `pv_h = min(PV, Last)` ist im Modell fest (Plan-Defekt aus dem Review von Task 4, im Plan
    nachgetragen): PV deckt zuerst das Haus, im Akkumodell und in jeder Victron-Absicht. Mit freiem
    `pv_h` lud der Solver verdeckt aus dem Netz (über `pv_b + gr_h`, bei ausgeschaltetem Schalter und
    ohne Marge und R1). Das schließt auch gleichzeitigen Bezug und Einspeisung aus. Falls falsch: eine
    Nebenbedingung.
  - Zeitlimit ohne Lösung ergibt den Status `time_limit` mit leeren Flüssen, nicht `error`; der
    Planungslauf macht daraus den Ersatzplan mit dem Grund „Zeitlimit ohne Lösung“. Falls falsch: eine
    Zeile in der Zuordnung.
  - `highspy>=1.8` bleibt ohne Pin (ein Pin blockierte Updates). Falls ein Update den Test auf das
    Zeitlimit wackeln lässt: den Test per Mock umstellen.
- **Absichten und Plausibilität:**
  - Netzladen, das auf 0 W rundet oder kappt, wird kein `NETZLADEN` (der Validator verbietet 0 W).
    Falls falsch: eine Bedingung.
  - Die Spread-Regel prüft mit dem Verschleiß auf der gelieferten Energie: `η_rt · (π_später − w) − π_t
    ≥ m`, ein gemeinsamer Helfer (`spread_pays`) für Plausibilität und Ersatzplan. Netzladen für den
    **Restwert** am Horizontende gilt als Nutzung, wenn der End-SOC um die geladene Energie über der
    unteren Grenze liegt und `η_ch · Restwert − π_t ≥ m` ist (Gesamtreview, I1). Falls falsch: Die
    Plausibilität lässt Netzladen für einen Restwert zu, den der Optimierer ohnehin wollte (oder
    verwirft ihn wieder zu Unrecht).
  - `derive_intents` kürzt das letzte `NETZLADEN` vor einer Einspeisung in der Nachrechnung um die
    eingespeiste Energie (auf 100 W aufgerundet, unter 100 W wird es `HALTEN`). Grund: Die Rundung einer
    Teilentladung auf `HALTEN` ließ Regel R1 in der Nachrechnung reißen (Property-Test, Saat 3). Falls
    falsch: etwas weniger Netzladen als optimal.
  - `pays_off` hat den Zusatzparameter `by` (`discharge` oder `import`) mit dem Standard der alten
    Signatur; die Erklärung nutzt `import` für „Regel R blockiert Netzladen“ und „Netzladen aus“,
    `discharge` für „Netzladen günstig“. Falls falsch: Der Satz nennt den Zeitpunkt eines anderen Slots.
  - `_de` rundet kaufmännisch (Decimal, half-up) statt mit Pythons `round`: Der Plan-Test verlangt
    7,25 als „7,3“. Falls falsch: Die Anzeige weicht in der letzten Stelle ab.
- **Ersatzplan:**
  - „Über dem 80. Perzentil“ heißt `≥ p80`: Bei kurzen Abendblöcken ist p80 (inklusive Quantile) das
    Maximum, ein strenges `>` fände nie einen teuren Slot. Ein flacher Tag hat keine teuren Slots.
    Falls falsch: Halten und Netzladen greifen bei Gleichstand am Maximum nicht.
  - Die Netzladen-Phase rechnet nur gegen den ersten knappen teuren Slot (Schleife endet ohne
    Kandidat), wie das Halten. Falls falsch: Der Ersatzplan verpasst seltene spätere Spitzen, und nur im
    Rückfall.
  - Präzisierung 10 bindet: Das Halten läuft, bis kein Kandidat mehr bleibt (der Abend braucht 2,13 kWh,
    mehr als 2 kWh über der Reserve). Der Plan-Test mit den Halte-Slots 1 und 3 widersprach dem; er
    prüft jetzt ein Szenario mit genau zwei Halte-Slots, das Ursprungsszenario steht als eigener Test.
    Falls falsch: ein Test.
- **Dienst, Speicher und Auslöser:**
  - Das Kürzen nach 7 Tagen behält Slots mit `start < valid_until` (10:45 bleibt bei `valid_until` 10:46):
    Der Slot war noch gültig und gehört zum Vergleich Plan gegen Ist. Das Beispiel im Plan war falsch.
    Falls falsch: ein Vergleich.
  - Eine SOC-Abweichung löst nicht erneut aus, wenn der aktuelle Plan selbst durch „soc“ im selben Slot
    entstanden ist (sonst alle 30 s ein Plan bis zum nächsten Tick). Falls falsch: Eine echte zweite
    Abweichung im selben Slot wartet bis zum Tick (höchstens 15 min).
  - Der Preis-Fingerabdruck zählt nur einen neuen letzten Slot oder mehr `primary`-Slots als neue Preise
    (das gleitende 48-h-Fenster löste sonst jede Viertelstunde „prices“ aus). Falls falsch: Eine
    Preisänderung ohne neuen Slot wartet bis zum nächsten Tick.
  - Zusätze des Planungslaufs sind akzeptiert: Weicht die Zahl der Slots ab, gilt der Ersatzplan; ein
    Fehler ohne Meldung heißt „unbekannter Fehler“; ungültiges JSON im Kind ist ein Fehler; eine
    negative BMS-Grenze zählt als 0; das Aufräumen des Kindes ist abgeschirmt. Falls falsch: einzelne
    Zweige in `process.py` und `service.py`.
- **UI:**
  - Ob ein Plan abgelaufen ist, entscheidet nur das `valid` des Servers (mit Auffrischen alle 60 s), nie
    die Uhr des Browsers: Ein Fehlalarm ist schlimmer als bis zu 60 s Verzug. Falls falsch: Der Ablauf
    erscheint bis zu 60 s zu spät.
  - Die Seite „Zeitplan“ trägt immer (mit und ohne Plan) die Vorschau-Zeile „Vorschau – der core führt
    den Plan noch nicht aus.“ (Gesamtreview, M2). Falls falsch: ein Wortlaut.
  - Der Demo-Plan bleibt nur Eigenverbrauch (flache Preise, Netzladen aus); Blöcke deckt ein Unit-Test.
    Falls falsch: Demo-Preise mit Spread.
  - Der E2E-Server (`tests/e2e/ui_server.py`, außerhalb der Dateiliste des Plans) startet
    `planning.run()`, sonst ändert „Neu planen“ im E2E nichts. Falls falsch: Der E2E-Server läuft ohne
    Schleife.
- **HA:**
  - `hourly[].soc_pct` ist der SOC zu Beginn des Slots zur vollen Stunde. Falls falsch: eine Zeile.
  - Die vier Plan-Sensoren tragen immer `preview: true`; `budget` hat dafür jetzt Attribute. Falls
    falsch: ein Attribut.
  - Ohne `planning` fallen Schalter und Taste in den Pfad „unbekannte Entität“ (Debug-Log, keine
    Ablehnung), denn der Ablehnungstext braucht den Namen aus der Tabelle; die Laufzeit übergibt
    `planning` immer. Falls falsch: wenige Zeilen.
- **Szenarien und Werkzeuge:**
  - Die Winter-PV-Spitze der erzeugten Tage ist 800 W statt 2000 W. Mit 2000 W deckt der PV-Überschuss
    den Abend, „Victron allein“ bezieht nie zum Spitzenpreis, und es gibt keinen Netzladevorteil (nur
    0,83 ct, unter der Marge von 2 ct nach Verschleiß); 800 W trägt über die Saaten 1 bis 5. Falls
    falsch: `winter-*` zeigt einen trüben statt eines mittleren Januartags.
  - Der Tag `wechselhaft` hat 1500 W PV-Spitze (mit 3000 W bezieht „Victron allein“ nie zur Spitze).
    1500 W ist die höchste Spitze mit mehr als 1 kWh Bezug zum Spitzenpreis für die Saaten 1 bis 5
    (Saat 1 mit 1,33 kWh knapp darüber). Falls falsch: `wechselhaft` ist ein trüber Wintertag.
  - `export_days` nimmt die Monatsspitze vor dem ersten Tag aus den Slots der Abrechnung (Monatsanfang
    bis erster Tag), nicht aus `month_peak(Vortag)`: Der liefert den ganzen Monat samt der Exporttage
    (die Annahme im Plan war falsch). Falls falsch: Die Spitze steht zu niedrig.
  - Ein übersprungener Tag mitten im Zeitraum lässt eine Lücke; SOC und Spitze laufen über sie hinweg
    (beide Seiten gleich), und der Tag steht in `skipped_days` und im Bericht. Falls falsch: Das Ergebnis
    nach der Lücke ist nur eine Näherung (Anlagenrisiko, siehe „Offen an der Anlage“, Punkt 5).
  - `export_days` und `scenario_days` stellen die Logs nur im Programmpfad (`__main__`) ein, nicht in
    `main()`. Falls falsch: Tests, die `main` aufrufen, verschleppen die Logging-Konfiguration wieder.

Aufgeschobene Kleinigkeiten aus Plan 3b (aus den Reviews; keine davon gefährdet die Anlage). Was die
Fix-Welle oder spätere Tasks erledigt haben, steht nicht mehr hier (etwa die Punkte aus Task 9 und
die Punkte B der Fix-Welle):

- **Plan-Typen und Eingangsdaten (Tasks 1 bis 3):**
  - `Plan` hat keinen Validator für `valid_until > created_at` und aufsteigende, eindeutige Slots
    (`horizon_end` und `slot_at` nehmen die Ordnung an); der JSON-Roundtrip-Test hat keine gefüllten
    Parameter eines Grundes und keinen `NETZLADEN`-Slot. `BatteryState` und `PeakMonth.month` sind
    unvalidiert.
  - Akkumodell: Der erste Test von `battery_w` wiederholt die Formel, die `SLOT_H`-Prüfung steht im
    falschen Test, und der Docstring sagt nicht, dass Platz und lieferbare Energie nicht durch die
    Leistungsgrenzen gekappt sind. Der Wirkungsgrad `eta()**2` ergibt 0,8800000000000001 statt
    `roundtrip/100`.
  - Eingangsdaten: `_within` dedupliziert nicht nach Start (Median des Restwerts); der Wert `after` von
    `horizon_prices` ist ungenutzt; es fehlen Tests für `feed_in` ohne Wert (0 ct) und für den
    Frühlingstag mit 92 Slots.
- **Optimierer und Absichten (Tasks 4 bis 6):**
  - Die Schranke von `pv_h` hat keinen Clamp bei negativer PV oder Last (die Eingaben sind nie negativ).
  - Ersatzplan: Der Zusatz-Guard „kein Platz im Akku“ ist ungetestet und nutzt `SHORT_W` fachfremd; ein
    toter Ausdruck `- charge_grid_w`; `_local_day` gibt `object` statt `date` zurück; es gibt keine
    Gleichstands-Tests, und der Standardtest läuft über den frühen Rückgabepfad.
  - Die Halbaufrundung statt `round()` ist undokumentiert; der Test der Slot-Reihenfolge kreuzt keine
    Schleifen; der R1-Test prüft nur ein Präfix.
- **Builder und Erklärungen (Task 7):** `params_of` läuft je `HALTEN`-Slot neu; `_hhmm` dupliziert
  `plausibility._local_time`; die Zweige „`NETZLADEN`-Fallback im letzten Slot“ und die Längenprüfung sind
  ungetestet; `BUDGET_SPITZE` nutzt das Budget des Halte-Slots auch über eine Monatsgrenze; ein Monat ohne
  Spitze des Optimierers nimmt `peak0` des Monats. Optional ein Grund „Ergebnis mit n statt m Slots“.
- **Store und Prozess (Tasks 8 und 9):** Der Test von `latest()` speichert aufsteigend (Ordnung und
  Gleichstand sind ungeprüft); `slots[:1]` des Ersatzplans und `reason[:200]` sind ungetestet; es fehlt ein
  Test mit einem Offset ungleich UTC; eine beschädigte Zeile blockiert die Transaktion des Aufräumens; eine
  zweite Abbrechung kann den Empfänger früh schließen; es fehlt ein Monatsgrenzen-Test für das lokale Datum
  der Monatsspitze; `no_live_children` ist schwach.
- **Dienst, API und HA (Tasks 11 und 12):** Der Retry-Test prüft nicht zwei `update`-Aufrufe; es fehlt ein
  Test für den gleichzeitigen gleichen Wert; ein Parameter `session` ist ungenutzt (Guard); der Zustand des
  Schalters in HA ist bis zu 5 s verzögert; die mypy-Verzweigungen in `_execute`.
- **UI (Tasks 13 und 14):**
  - `ui_world` setzt das private `app.planning._solver` (`AppRuntime` hat kein `solver=`); `applyPlanning`
    ist ein zweiter Prüfpfad neben `readNumber`; ein Cast statt Eingrenzen.
  - Der Timeout von 40 s nach „Neu planen“ ist ohne Hinweis und Test; bei gleichem Wert des Schalters
    zeigt die Seite 40 s „Plant …“ und der Schalter ist 40 s gesperrt; die Achse beginnt am ersten Slot
    statt bei jetzt; `markArea` fasst nur nach Absicht zusammen; der Text eines Blocks ist der Satz des
    ersten Slots; ein geschütztes Leerzeichen in `blockLabel`.
- **Szenarien und Werkzeuge (Tasks 15 und 16):**
  - Der Prognosefehler je Stunde ist für alle Klassen gleich (schmeichelt dem Optimierer; die echten Tage
    sind die Prüfung, siehe „Offen an der Anlage“); der Verschleiß steckt nicht in den Szenariokosten; der
    Einspeisetarif gilt nur für den ersten Monat; der Name eines Tests (`baseline_never…`) prüft den
    Optimierer.
  - `export_days` erzwingt „nur lesen“ nicht (der `SettingsService` legt Version 1 an, wenn die
    Einstellungen leer sind); es fehlen Tests für einen Tag mit 100 Slots, Lücken am Rand und die Klassen
    `h6_24` und `h24`; ein Pfad hängt vom Arbeitsverzeichnis ab; die Logs gehen auf die Standardausgabe;
    eine Ausnahme aus `run_scenario` endet mit Code 1 statt 2.
- **Fix-Welle (Re-Review):** `_keep_rule_r1` verdeckt einen echten R1-Fehler des Optimierers (das Signal
  geht verloren, solange die Kürzung ihn ausgleicht), und `pays_off_at_end` prüft je Slot, nicht die Summe
  aller Ladeslots.

Entschieden in Plan 3c; die 14 Entscheidungen hat der Betreiber am 17.10.2026 mit dem Plan freigegeben
(`docs/superpowers/plans/2026-10-17-phase-3c-planausfuehrung.md`, „Vorgeschlagene Entscheidungen“, und
Spec 18, „Stand Plan 3c“). Die Festlegungen der Umsetzung stehen unten und, soweit sie das Verhalten
betreffen, in den Absätzen „Stand Plan 3c“ der Spec. Hier beides mit Kosten, falls es falsch ist:

- **Die Entscheidungen (mit der Alternative aus dem Plan):**
  1. **Plan in der Absicht:** nur der bindende Teil (Slots vor `valid_until`, höchstens 8, rund 2 KB),
     jeder Plan eine neue Version der Absicht, ohne Eintrag im Änderungsprotokoll. Alternative: ein
     eigener Endpunkt `PUT /v1/plan` mit eigener Speicherung im core. Falls falsch: Der Nachweis eines
     Plans steht nur im Plan-Archiv der app.
  2. **Absichten über den vorhandenen Override:** `HALTEN` `max(0, Hauslast + Autos − PV − Basis)`,
     `NETZLADEN(P)` dasselbe plus `P`; Totband, Raster, Auffrischen, Budget und Regel 2 wie bisher.
     Alternative: der flüchtige Override `ess.override.maxdischargepower` (an der Anlage nicht geprüft).
     Falls falsch: Unter 200 W gewolltem Netzbezug entlädt der Akku bei `HALTEN` bis rund 0,05 kWh je
     Viertelstunde.
  3. **Boost bei `HALTEN` und `NETZLADEN` lädt aus dem Netz.** Alternative: Boost immer aus dem Akku.
     Falls falsch: Ein Boost am Abend kostet Netzbezug zum teuren Preis, statt den gehaltenen Akku zu
     leeren.
  4. **Prüfung und Gültigkeit im core:** auf der Uhr des core, Klemmen von `valid_until` (45 min) und
     `P`, 422 für Lücken, Raster und Netzladen trotz Schalter aus, abgelaufene Pläne angenommen ohne
     Wirkung. Falls falsch: Ein Plan, den der core ablehnt oder der auf seiner Uhr nicht gilt (etwa bei
     einer Abweichung der Uhren), wirkt nicht, ohne dass eine Bedienung scheitert; der core fährt bis
     zum nächsten passenden Plan den Eigenverbrauch, und sichtbar ist das nur im Stand der Ausführung
     („Der Plan ist noch nicht beim core.“) und im Log der app.
  5. **Budget:** Das `budget_kw` des Slots ersetzt `control.peak_target_kw`; eine Überschreitung warnt
     nur, der nächste Plan hebt das Budget. Falls falsch: bis zu einer Viertelstunde mit gewarntem, nicht
     verhindertem Bezug über der akzeptierten Spitze; erst mit einem Leistungspreis mit Satz (frühestens
     2027).
  6. **Plan-Schwelle der Unterstützung:** rückwärts über den Horizont, wirksam das Maximum mit der
     Unterstützungsschwelle, Grundcode `UNTERSTUETZUNG_RESERVIERT`. Alternative: die Unterstützung im
     MILP (braucht die Autos im Planer, Phase 4). Falls falsch: Autos bekommen tagsüber weniger aus dem
     Akku, als sich lohnen würde.
  7. **Schalter und Schwelle in der Absicht** wie der Gast-Modus, Standard erlaubt mit 70 %, Bereich
     USV-Reserve bis 100 %. Alternative: Laufzeit-Einstellungen (wirkten erst mit dem nächsten Plan).
     Falls falsch: eine Bedienung mehr in der Absicht.
  8. **Übergabe:** nach jedem Lauf, jeder Verbindung und alle 30 s, solange der core nicht den neuesten
     gültigen Plan hat; „Netzladen erlaubt“ aus schickt sofort denselben Plan ohne `NETZLADEN`. Falls
     falsch: bis zu 30 s mit dem alten Plan.
  9. **Entscheidungslog:** Gründe `plan_hold` und `plan_grid_charge`, Eingaben `price_ct`,
     `plan_slot_ts`, `plan_grid_charge_w` und `budget_kw` nur, wenn es den Wert gibt. Falls falsch:
     Auswertungen müssen fehlende Schlüssel erwarten.
  10. **Stand der Ausführung statt „Vorschau“** (sieben Stände, HA-Attribut `execution`). Falls falsch:
      ein Wortlaut und ein Attribut.
  11. **„Plan gegen Ist“** für heute bis jetzt, eine Karte im Zeitplan. Falls falsch: Andere Tage gibt es
      nur über die API.
  12. **Mehrkosten beim Boost** als Obergrenze mit der vollen Ladeleistung des Autos, nur mit Satz im
      Monat. Falls falsch: Die Anzeige überschätzt die Kosten eines Boosts, der die Spitze weniger hebt.
  13. **Simulator mit Wirkungsgrad:** alle Szenarien mit 88 %, das Modell ohne Angabe verlustfrei, fünf
      Plan-Szenarien. Falls falsch: Die Szenarien rechnen mit einem anderen Wirkungsgrad als der Akku
      der Anlage.
  14. **Nichts geht von selbst live:** Der Plan wirkt nur mit Regelbetrieb und in der Betriebsart von
      Victron; live nach den vier Bedingungen aus Spec 8.7.
- **core:**
  - Health `plan.state` ist `expired` auch für einen Plan, der wegen einer Abweichung der Uhren noch nicht
    gilt (`wall_now < created_at`): Der Stand kennt nur `none`, `valid` und `expired`, und der Plan wirkt
    so oder so nicht. Falls falsch: Das UI zeigt „abgelaufen“ für einen Plan, der von einer vorgehenden
    Uhr der app zu früh kommt; nur eine Anzeige.
  - `plan_health` zeigt `valid` auch, wenn die Regelung ruht und der Plan zur Wanduhr gilt (die
    Slot-Felder sind dann `null`); dafür gibt es `Regulation.rest_plan` und das Gegenstück in
    `RegulationLink`. Falls falsch: harmlos.
  - Negative Messwerte von PV und Hauslast zählen im Bedarf des Plans als 0, wie im Fehlbetrag. Falls
    falsch: Ein verrauschter negativer Wert verschiebt `W` um seinen Betrag, wenig.
  - Unter `HALTEN` und `NETZLADEN` bekommt der Wächter als Netzanteil jedes Autos (`peak_cars`) seinen
    ganzen Fehlbetrag, auch in Boost (folgt aus Entscheidung 3; `Support.update` hat dafür
    `plan_blocks`). Falls falsch: Der Wächter rechnet den Autos zu viel Netzbezug zu und drosselt nur
    vorsichtiger.
  - `PeakGuard` behält `control.peak_target_kw` aus dem Konstruktor nur als Health-Wert vor dem ersten
    Schritt; die Schritte lesen ihn nie (Health in OBSERVE und ein Test der Safety-Schicht brauchen ihn).
    Falls falsch: ein Health-Wert vor dem ersten Schritt.
  - Läuft ein Plan, der allein das Ziel brachte (`peak_target_kw` leer), mitten in einer Viertelstunde
    ab, ruht der Wächter sofort und lässt Grenzen und den Alarm `peak.exceeded` fallen (Präzisierung 9
    wörtlich; Betriebshandbuch, „15-min-Wächter“). Falls falsch: Die Autos bekommen im Rest dieser
    Viertelstunde die volle Leistung zurück, nachdem die app keinen Plan mehr schickt; nur mit
    Leistungspreis (ab 2027).
  - `RegulationLink.plan_inputs` liefert die Eingaben des Plans für die Entscheidungen; sie gehen an die
    Entscheidungen der Ladepunkte und des Overrides, nicht an Keeper, Übergabe und fremde Änderungen.
- **app:**
  - Die Plan-Schwelle eines Slots, der selbst entlädt, schließt seine eigene Entladung ein (Präzisierung
    6: `R_t = max(0, d_t + R_{t+1})`); der Test `test_pv_refill_releases_the_threshold` verlangt deshalb
    `[30, 20]`, sein Kommentar meinte Slot 1. Falls falsch: Die Schwelle in einem entladenden Slot liegt
    10 Punkte zu hoch, die Unterstützung beginnt später (sichere Seite).
  - `PlanSender` sendet nicht, solange die app kein Health des core hat (Fix-Runde von Task 10), und
    seit der Fix-Welle nach dem Gesamtreview nur auf einem Health, dem sie traut (unten,
    „Gesamtreview“). `LiveFeed.connect_handlers` und `LiveFeed.health_handlers` sind dafür öffentlich.
    Falls falsch: nach einer Verbindung bis zu einer Abfrage von Health (10 s) ohne Plan im core.
  - Für eine Schwelle über 100 und eine Schwelle, die keine endliche Zahl ist, hat die app eigene Sätze
    („Unterstützungsschwelle 150 % liegt über 100 %.“, „Unterstützungsschwelle ist keine endliche
    Zahl.“); die „Texte“ des Plans nennen nur den Satz „unter der USV-Reserve“. Falls falsch: zwei Sätze.
- **UI und Demo-Welt:**
  - Ein älterer core ohne den Block `battery` in Health bricht die Seiten „System“ und „Laden“ nicht
    (Fix-Runde von Task 13: der Block ist optional). Falls falsch: ein Schema.
  - Eine Differenz, die auf 0,00 rundet, steht ohne Minus (Fix-Runde von Task 15).
  - Der Plan der Demo-Welt um 09:01 entsteht aus den Eingaben des Plans von 10:00, um 1 h verschoben, und
    geht direkt in die Planablage (ein echter Lauf um 09:01 hätte keine Lastprognose). Falls falsch: Nur
    die Demo-Daten sind auf andere Weise künstlich.
  - Die Demo-Welt hat einen erfundenen Leistungspreis (60 €/kW/Jahr), damit die Mehrkosten des Boosts
    Fahrzeuge zeigen; dadurch änderten sich die Vertragsdateien von Einstellungen, System, Protokoll und
    Plan (ein „Budget 2,0 kW“ und eine Warnung, die Absichten des Plans blieben gleich). Die Tests nehmen
    den Tesla für 11,04 kW, denn der e-Golf hat 2 Phasen. Falls falsch: Die Demo zeigt einen
    Leistungspreis, den die Anlage noch nicht hat.
- **Simulator:**
  - Das Plan-Ereignis heißt `_Plan(start, at_s, slots, *, valid_s, grid_charge_allowed=True)` statt der
    Signatur im Plan; Ereignisse bekommen nur `(model, rt)`, und Hilfen bauen beides aus einem Wert.
    Falls falsch: eine API des Simulators.
  - Alle 23 alten Szenarien bestehen mit 88 % ohne gelockerte Grenze. `plan-budget` prüft seit der
    Fix-Runde von Task 8 auch die Untergrenze je Viertelstunde und die Gründe (vorher bestand es auch,
    wenn der core den Plan überging).
- **Gesamtreview (17.10.2026, „mit Korrekturen“, keine kritischen Befunde):** Die beiden Punkte, die
  bis dahin hier offen standen, waren die wichtigen Befunde I1 und I2; die Fix-Welle hat sie behoben:
  - **Fenster ohne vertrauten Stand nach einem Neustart des core (I1, Teil der app):** Jede neue
    Version bindet im core Boost und Übersteuerung neu und gilt als nach seinem Start angenommen. Ein
    Plan auf einem Stand, dem die app nicht traut, machte so einen wiederhergestellten oder schon
    verbrauchten Boost für das nächste Anstecken scharf. Jetzt sendet `PlanSender` nur auf einem
    Health, dem die app traut (`health_trusted`: `intent_version ≥ intent.version`, dieselbe Regel wie
    `_trusted`), `LiveFeed` verwirft Health bei der Trennung (auch eine Antwort, die vor ihr gefragt
    wurde) und weckt den Sender nach jeder Abfrage, und der Stand der Ausführung nennt „Regelbetrieb
    aus“ und „regelt nicht“ vor „noch nicht beim core“.
  - **Budget unter dem eigenen Bezug (I2):** Der Ersatzplan nahm `peak0_kw` als Budget, auch unter
    seinem eigenen Bezug bei `HALTEN` und `NETZLADEN`, und am Monatsanfang mit `minimum_kw` 0 lag
    das Budget bei 0 kW. Jetzt gilt für beide Quellen eine Formel (`builder._budgets`, unten in der
    Festlegung).
  - **Festlegungen des Gesamtreviews:**
    1. I1 wird in der app behoben (Senden nur auf vertrautem Health, Health bei der Trennung
       verworfen, Sender nach jeder Abfrage von Health geweckt, „aus“ und „regelt nicht“ vor „noch
       nicht beim core“); das Binden im core nur bei geänderten `vehicles` ist aufgeschoben: Die
       Sperre in der app nimmt den Weg weg, der zur Regel wurde, und Bedienungen im Fenster ohne
       vertrauten Stand gibt es seit 2d. Falls falsch: Eine Bedienung direkt nach einem Neustart des
       core kann weiter einen Boost scharf machen.
    2. I2 wird jetzt in `builder._budgets` behoben: Budget = auf 0,1 kW aufgerundet das Größte aus
       `peak0_kw`, der Spitze des Optimierers (nur Optimierer), dem höchsten Bezug der Nachrechnung im
       Monat und `control.peak_target_kw` (ohne Wert 0); ist das noch 0, der größte Mindestladestrom
       eines Autos der Konfiguration (`min_a · phases · grid.voltage_nominal_v`). So bleibt der Plan
       bei seinem eigenen Bezug, und am Monatsanfang drosselt der Wächter nicht bis zum Boden und
       alarmiert nicht grundlos. Falls falsch: Die akzeptierte Monatsspitze kann um den Boden eines
       Autos steigen, ohne dass der Betreiber es wählt; mit `control.peak_target_kw` oder `minimum_kw`
       setzt Binda eine andere Untergrenze.
    3. Die kleinen Befunde 1 (Stand der Ausführung nur mit `plan.state` `valid` im Health des core),
       3 (der core hebt `support.soc_pct` mit Hinweis auf die USV-Reserve, statt die ganze Absicht
       abzulehnen; sonst scheiterte auch „Regelbetrieb aus“ mit 422), 4 (Hinweis zum Rollback) und 5
       (Hinweis im Betriebshandbuch und ein Test, dass jede Version neu bindet) gehören in die
       Fix-Welle; 2 und 6 sind aufgeschoben: billig und sicherheitsrelevant gegenüber selten oder
       kosmetisch. Falls falsch: nach einer Wiederherstellung der Datenbank der app ein übersprungener
       Planungszyklus, und die Karte „Plan gegen Ist“ zeigt Pläne, die der core nie hatte.
- **Offen nach dem Gesamtreview:**
  - **Neu binden im core nur bei geänderten `vehicles`** (Folge von I1): `Assignment` bindet bei jeder
    neuen Version neu und entscheidet „wiederhergestellt“ an der Annahmezeit dieser Version. Robuster
    wäre, nur neu zu binden, wenn sich `vehicles` ändert, und „wiederhergestellt“ an der Annahme der
    letzten Version zu entscheiden, die `vehicles` geändert hat (in `intent.json` neben `accepted_at`
    gespeichert). Das deckte auch Bedienungen im Fenster ohne vertrauten Stand.
  - **Kleiner Befund 2:** `core_has_plan` vergleicht nur `(id, grid_charge_allowed)`. Nach einer
    Wiederherstellung der Datenbank der app überspringt der Sender mit einer wiederverwendeten `id`
    einen anderen Plan für einen Planungszyklus, und der Zeitplan meldet die Ausführung. Abhilfe: auch
    `created_at` vergleichen, das Health schon trägt.
  - **Kleiner Befund 6:** „Plan gegen Ist“ vergleicht mit dem Archiv der app, nicht mit dem, was der
    core hatte; Slots von Plänen, die der core nie bekam (core aus, 422), stehen als „Plan“. Abhilfe:
    solche Slots kennzeichnen (braucht einen Vermerk, wann ein Plan gesendet wurde) oder es im
    Hilfetext der Karte sagen.

Aufgeschobene Kleinigkeiten aus Plan 3c (aus den Reviews; keine davon gefährdet die Anlage). Was spätere Tasks erledigt haben, steht nicht mehr hier (etwa die Doku-Punkte aus
den Tasks 4, 6, 7, 11 und 16, die mit Task 17 eingearbeitet sind):

- **Typen und Prüfung (Tasks 1 und 2):** Der Helfer `_stored` in `tests/unit/shared/test_plan.py` braucht
  ein `type: ignore` für `**kwargs`; `intent_plan` schneidet still nach 8 Slots ab (ein Hinweis im
  Docstring fehlt); `PlanSlot.support_soc_pct` ist nur endlich, `IntentPlanSlot` verlangt 0 bis 100.
  `GRID_CHARGE_CAP` in `core/control/intent.py` wiederholt den Namen aus `limits.py` (ein Test hält beide
  gleich, eine gemeinsame Konstante wäre besser); eine doppelte Prüfung auf `NETZLADEN`; der API-Test
  zum geklemmten Plan schickt keinen Plan; „19.5 %“ steht mit Punkt statt Komma (`:g`).
- **Slot und Health (Task 3):** `active is not None or valid_at` ist zum Teil doppelt; `idle()` schreibt
  `plan` `none`, wenn der Aufrufer nicht auch `rest_plan` ruft, und nach einem gescheiterten Zyklus
  steht für einen Zyklus `none`; das UI-Schema von Health kennt `plan` nicht (der Stand kommt aus der
  app); der Neustart-Test stoppt die erste Laufzeit nicht, bevor er `data_dir` wiederverwendet.
- **Unterstützung und Wächter (Tasks 5 und 6):** `Support.update(plan_blocks=False)` könnte Pflicht sein;
  der Property-Test nur mit fester Schwelle; hängende Kommentare in `test_battery.py` nach dem Umbruch
  durch ruff; `assess` hat `support` und `active` nicht nur als Schlüsselwort. Im Leerlauf zeigt Health
  das letzte Budget des Plans als `target_kw`.
- **Entscheidungen (Task 7):** Die Integrationstests prüfen nur Entscheidungen an Victron, keiner die
  Reihenfolge des Zusammenführens; Preis und Budget prüft `regulation.py` nur auf `is not None` (NaN
  käme durch, das Modell des Plans prüft es).
- **Simulator (Task 8):** `plan-netzladen` rechnet fest mit 30 kWh (`BATTERY_KWH`) und meldet mit
  `--config` einer Anlage mit deutlich mehr nutzbarer Kapazität zu Unrecht einen Fehler; die Klemme an
  der Reserve macht `_reserve_kept` am Netz unscheiterbar; `_Plan.__call__` wiederholt den Block von
  `_new_version`; die Version von `_Boost(3, …)` in `plan-boost` ist von Hand gekoppelt;
  `test_default_model_stays_lossless` nutzt `approx`.
- **Plan-Schwelle (Task 9):** `model_copy` im Builder überspringt die Prüfung von `support_soc_pct`;
  `DEFAULT_SUPPORT_SOC_PCT` in `inputs.py` wiederholt den Standard der Konfiguration (das Argument von
  `assemble_inputs` könnte Pflicht sein); der Parameter `um` von `UNTERSTUETZUNG_RESERVIERT` ist ISO,
  die anderen Codes nehmen HH:MM; es fehlen Tests für den `ValueError` von `reasons_for` bei falscher
  Länge und für eine Schwelle in `NETZLADEN`-Slots ohne Code. Eine geänderte Unterstützungsschwelle löst
  keine Neuplanung aus; der Grundcode kann bis zu 15 min veraltet sein.
- **Übergabe (Task 10):** `test_core_link.py` holt `FakePlanning` aus einem Unit-Test-Modul; `PlanSender`
  ist auf den konkreten `PlanningService` getypt (ein Protocol spart `type: ignore` in den Tests).
- **Stand der Ausführung (Task 11):** unerreichbare `None`-Zweige in `execution.py`; kein Test für
  „idle vor locked“ (SAFE und gesperrt ergibt `idle`); ein lokaler Import von `timedelta` in
  `test_routes.py`; die Seite „Zeitplan“ fragt `/api/plan` alle 60 s ab, „noch nicht beim core“ kann bis
  zu 1 min nachhängen.
- **Bedienung der Unterstützung (Tasks 12 und 13):** Die HA-Zahl nimmt Dezimalzahlen und Unterstriche an
  (`float()`), Schritt 1 wird nicht erzwungen; `_support` in HA liest `current()`, und `apply` leitet neu
  ab (ein kleines Rennen zwischen UI und HA um das jeweils andere Feld); `_support_comment` mit sechs
  gültigen Stellen (`:g`); der Typ der Befehle steht dreimal; `extra=forbid` in `SupportBody` ist doppelt;
  der HA-Bereich ist nur mit Reserve 20 getestet. Im UI: ein verwaister Doc-Kommentar in `schemas.ts`
  (bei `Intent`), die Karte bleibt verborgen, bis `/api/limits` geladen ist, `badInput` ist im Baustein
  ungetestet, der Entwurf setzt sich nach einer abgelehnten Änderung zurück, der Hinweis hängt nicht an
  `healthIsCurrent`, und ein ausdrückliches `"battery": null` ist weiter ungültig.
- **Plan gegen Ist (Tasks 14 und 15):** kein Test mit gemischt bepreisten Slots; `local_day_slots` läuft
  zweimal; den frühen Stunden des ältesten Tages können Pläne fehlen (Aufbewahrung); `covered_s` bleibt
  bei Teil-Slots der Abrechnung unbeachtet. Im UI ist `null` als Kosten nur auf der Ist-Seite getestet,
  `soc.error` wird nicht gezeigt, die SOC-Reihen nutzen die Farben von Akku und Wallbox, und `ui_world`
  greift auf `app.planning._store` zu.
- **Mehrkosten beim Boost (Task 16):** kein Routentest über eine Monatsgrenze in Wiener Zeit; der Test der
  Stufe mit `tier_kw` 100 wiederholt den linearen Fall; `month_peak_kw` ist der rohe Wert der
  Abrechnung.
- **Nach der Fix-Welle (Nachprüfung):** Fast nach jeder Übergabe oder Bedienung schreibt die app einmal
  „Plan wartet, bis der core mit der Absicht regelt“, obwohl der Plan schon beim core ist (die sofortige
  Abfrage der Health landet vor dem nächsten Regelzyklus; etwa 100 Zeilen am Tag, `sender.py`). Kommt
  der core innerhalb einer Abfrage (10 s) nach einem Neustart zurück, kann die gespeicherte Health bis
  zum Wecken beim Verbinden überleben (`core_link.py`, `_connected` leert sie nicht).

Entschieden in Plan 4a; die 13 Entscheidungen hat der Betreiber am 17.10.2026 mit dem Plan freigegeben
(`docs/superpowers/plans/2026-10-17-phase-4a-statistik.md`, „Vorgeschlagene Entscheidungen“, und Spec 18,
„Stand Plan 4a“), mit einer Änderung an den Entscheidungen 4 und 5: PV-Strom, der ins Auto geht (direkt
oder über den Akku), kostet 0 ct statt den OeMAG-Wert. Die Festlegungen der Umsetzung stehen unten und,
soweit sie das Verhalten betreffen, in den Absätzen „Stand Plan 4a“ der Spec und im Betriebshandbuch. Dazu
kommen zwei Entscheidungen des Betreibers vom 18.10.2026 und die Festlegungen der Nacharbeit danach. Hier
alles mit Kosten, falls es falsch ist:

- **Die Entscheidungen (mit der Alternative aus dem Plan):**
  1. **Zuschnitt der Phase 4:** 4a „Statistik“ (nur app, UI und `deploy/`), 4b „Geplantes Laden“ (ändert
     core und Optimierer, kommt nach der Abnahme der Phase 3). Alternative: ein Plan für die ganze Phase.
     Falls falsch: Die Statistik käme erst mit dem Planer.
  2. **Nur app, core unverändert:** kein neuer Typ in `shared`; `ChargingSession` lebt in
     `app/charging/sessions.py`. Alternative: der Typ in `shared`. Falls falsch: Das core-Image ändert sich,
     und das Ausrollen braucht einen Neustart des core.
  3. **Ladesessions aus dem Steckzustand:** Eine Session ist die Zeit, in der an einer Wallbox ein Auto
     steckt; das Fahrzeug kommt aus der Belegung in Health, eine Pause der Beobachtung bis 30 min trennt
     sie nicht. Alternative: Sessions aus der Ladeenergie der Abrechnung (sie trennte jede PV-Pause und
     kennte keine Steckzeiten). Falls falsch: Die Belegung kommt nur, wenn der core regelt (Regelbetrieb
     an); sonst heißen alle Sessions „Unbekannt“.
  4. **Einstandspreis des Akkus mit PV zu 0 ct** (Betreiber, abweichend von Spec 11.4): PV-Ladung 0 ct,
     Netzladung zum Bezugspreis, Entladung mit dem Mittelwert plus `planning.wear_ct`, Inhalt begrenzt auf
     `battery.usable_kwh`. Alternative: PV-Ladung mit dem OeMAG-Wert. Falls falsch: Die Kosten je Auto
     lassen den entgangenen Einspeiseerlös aus; die Änderung wäre eine Zeile in `costing.py` und eine
     Neurechnung.
  5. **Kosten je Auto:** Netz × Bezugspreis + PV × 0 + Akku × Einstandspreis, je Slot der Session an der
     Wallbox zugeordnet; fehlt ein Bezugspreis, ist die Summe unbekannt. Falls falsch: dieselbe Zeile wie
     bei 4.
  6. **Abgleich:** nachts um 02:45 Uhr für 7 Tage, beim Start für 35; Netzbezug, Einspeisung, PV (ohne
     Markierung) und jede Wallbox gegen die Zählerstände um Mitternacht; Markierung bei mehr als 1 % und
     mehr als 0,1 kWh; ein fehlender oder zurückgesetzter Zähler ergibt keine Markierung, sondern einen
     Grund. Falls falsch: Schwellen und Fenster stehen als Konstanten in `checks.py` und `runtime.py`.
  7. **Nachrechnung von Lücken:** höchstens 7 Tage, mit Zählerständen an beiden Rändern, gleichmäßig auf
     die Viertelstunden verteilt, Herkunft `estimate` und `covered_s = 0`. Falls falsch: Geschätzte Tage
     verbessern den Abgleich von selbst (die Schätzung rechnet aus denselben Zählern); die Abnahme nennt
     deshalb die Zahl der geschätzten Viertelstunden.
  8. **Verbraucher je Tag** in `consumer_day`: 31 Tage beim Start, nachts die letzten 3. Älter bleibt leer.
     Falls falsch: Die Statistik eines Verbrauchers über mehr als 31 Tage rückwärts hat Lücken.
  9. **Seite „Statistik“** mit Tag, Woche, Monat und Jahr; die Ersparnis bleibt ohne Grundgebühren und
     Leistungspreis. Falls falsch: eine Rechnung in `app/stats/service.py` und ein Wortlaut.
  10. **Grundgebühren und Leistungspreis** in der Statistik: Grundgebühren anteilig je Tag bis heute, der
      Leistungspreis je Monat aus der Monatsspitze, nur in Monat und Jahr. Falls falsch: Die Kachel
      „Kosten“ zeigt weniger oder mehr als die Rechnung des Versorgers.
  11. **Statistik-Sensoren in HA** alle 15 Minuten und bei neuen Slots. Falls falsch: Werte bis zu
      15 Minuten alt.
  12. **Grafana:** ein Startdashboard als JSON, die Datenquelle beim Import. Falls falsch: ein Dashboard
      in der Instanz des Betreibers anders aufbauen.
  13. **Abnahme:** 30 Tage ab dem Ausrollen, ±1 % im Abgleich; „HA in beide Richtungen“ und „Ziel-SOC“
      mit 4b. Falls falsch: Die Phase 4 wäre früher oder später ganz abgenommen.
- **Entscheidungen des Betreibers vom 18.10.2026** (zur Liste der aufgeschobenen Punkte; der Plan blieb
  unverändert, Spec und Betriebshandbuch sind angepasst):
  - **Akku-Anteil ohne Bezugspreis ist „unbekannt“:** Lädt der Akku aus dem Netz, bevor der Bezugspreis
    da ist, führt die app diese Energie als Anteil ohne Preis mit. Solange er mindestens 1 % des
    Akkubestands ist (der gerechnete Inhalt, nicht der SOC), ist der Einstandspreis unbekannt und damit
    Akku → Auto, die Kosten der Session und die Kosten je Auto (`cost_eur` ist `null`, die Statistik
    zeigt „–“, „Laden“ lässt die Kosten weg). Darunter gilt der Mittelwert der bepreisten Energie. Ein
    nachgetragener Preis macht den Wert wieder bekannt. Alternative aus dem Plan: Der Anteil kostet bis
    zum Nachtrag nur den Verschleiß (2 ct/kWh). Falls falsch: Die Schwelle ist die Konstante
    `UNPRICED_SHARE` in `costing.py`; die Alternative wäre eine Änderung in `cost_basis`.
  - **Euro überall „0,52 €“:** Zahl, geschütztes Leerzeichen, „€“, mit österreichischen Tausenderpunkten
    („1.234,50 €“); die Statistik schrieb vorher „€ 0,52“. Es gibt dafür eine Funktion, `formatEur`; das
    Minus bleibt das ASCII-Zeichen („-1,07 €“). Alternative: überall „€ 0,52“. Falls falsch: ein
    Wortlaut, eine Hilfsfunktion und ihre Tests.
- **Festlegungen der Nacharbeit vom 18.10.2026 mit sichtbaren Folgen:**
  - **Zählerstand um Mitternacht aus `raw`:** Der Abgleich liest die Zähler aus `"raw"."energy"` (jeder
    Wert mit seinem Lesezeitpunkt; `long` stempelt mit dem Anfang der Minute) und interpoliert zwischen
    dem letzten Wert davor und dem ersten danach (je höchstens eine halbe Stunde); fehlt einer, gilt der
    andere bis 2 min Abstand. Vorher stand der Wert bis zu 60 s zu früh. Falls falsch: Ein guter Tag mit
    großer Last um Mitternacht kann wieder markiert werden.
  - **Abfragen an InfluxDB per POST:** Der `InfluxReader` schickt `q` im Formular-Body (für alle
    Aufrufer), damit die Abfrage des Abgleichs (bis zu 72 Anweisungen) keine URL-Grenze trifft. Falls
    falsch: Ein Proxy, der für `/query` nur GET zulässt, bricht alle Abfragen der app; Abhilfe ist, dort
    POST zu erlauben.
  - **Nachführung des Einstandspreises im eigenen Job:** Sie hängt nicht mehr am Live-Strom und wird von
    Slots, Preisnachträgen und einer Änderung des Verschleißes geweckt. Scheitert sie, steht ein
    Traceback im Log, und die app versucht es nach 60 s (`COSTING_RETRY_S`) noch einmal, ohne zu drehen.
    Beim Beenden wartet die app bis zu 5 s auf den laufenden Block (`docker compose stop` beendet nach
    10 s hart); was ungerechnet bleibt, findet der nächste Start über `ledger_slot.updated_at`,
    Verschleiß und Kapazität. Falls falsch: Die Kosten je Auto können nach einem Fehler bis zu 1 min
    veraltet bleiben.
  - **Statistik beim Blättern:** Die alten Werte stehen gedimmt (`aria-busy`), bis die neuen da sind;
    scheitert die Abfrage des neuen Zeitraums, steht die Fehlermeldung statt der alten Werte. Falls
    falsch: eine Zeile in `current` der Statistik-Seite.
- **Festlegungen der Umsetzung (entschieden beim Bau; jede mit Kosten, falls falsch):**
  - **Listener statt Konstruktorverweis:** `LedgerService.on_slots_changed(callback)` (eine Liste von
    Listenern wie `SettingsService.on_change`); die Laufzeit meldet `costing.mark_dirty` an. Ein Verweis
    des Einstandspreises auf den Dienst und zurück wäre zirkulär gewesen. Falls falsch: ein kleiner Umbau
    der Verdrahtung in `runtime.py`.
  - **Fahrzeug einer laufenden Session:** Der Tracker bestimmt es jede Minute neu, vorläufig mit dem
    aktuellen Insassen, solange noch keiner Zeit hat (Präzisierung 6 nannte nur Wechsel und Ende). Das
    hält „Ladeenergie“ in HA und die Zeile auf „Laden“ aktuell. Falls falsch: eine zusätzliche
    Datenbanklesung je Minute.
  - **Nachrechnung nur im Fenster der letzten 7 Tage:** Bei einem längeren Ausfall wird der Teil im
    Fenster geschätzt (mit Zählerständen an beiden Rändern), der Teil davor bleibt leer. Falls falsch: Die
    letzten 7 Tage eines langen Ausfalls tragen Schätzungen statt einer Lücke; sie sind als `estimate`
    gekennzeichnet.
  - **Start des Abgleichs wartet auf den ersten Nachtrag der Abrechnung** (höchstens 10 Minuten), sonst
    liefen die 35 Tage gegen eine halbe Abrechnung, und Tage 8 bis 35 behielten falsche Markierungen bis
    zum nächsten Start. Falls falsch: Der Start-Abgleich beginnt bis zu 10 Minuten später.
  - **Verbraucher je Tag holt nachts 3 Tage nach,** nicht nur gestern; ein Ausfall von InfluxDB um 02:45
    Uhr ließe sonst gestern bis zum nächsten Start leer. Gespeicherte Tage werden übersprungen. Falls
    falsch: bis zu zwei Abfragen mehr in der Nacht.
  - **Kennzahlen unvollständiger Zeiträume:** Verhältnisse sind nur bei Nenner 0 leer (noch nicht
    begonnene Tage zählen als 0), Geldbeträge, sobald ein Tag sie nicht kennt. Falls falsch:
    Teilverhältnisse eines laufenden Zeitraums stehen da, wo „unbekannt“ erwartet wird.
  - **Home Assistant:** Einheit `EUR` statt „€“ (ISO 4217, wie `monetary` sie verlangt); „Ladeenergie“
    ohne Zustandsklasse (HA lehnt `measurement` bei `energy` ab, der Wert gilt je Session); nach einer
    gescheiterten Rechnung über Mitternacht zeigen alle Statistik-Sensoren „unbekannt“ statt den Wert von
    gestern. Die Prüfung
    der Objekt-IDs in `shared/config.py` kennt die vier neuen IDs nicht, denn `shared` blieb unverändert
    (Entscheidung 2): Die app bricht bei einer Kollision beim Start ab, statt dass die Konfiguration sie
    ablehnt. Falls falsch: ein Wortlaut, eine Einheit; bei der IDs-Prüfung eine Fehlermeldung später.
  - **Wortlaut der Seite außerhalb der „Texte“ des Plans:** In „Autos“ stehen PV, Akku und Netz in kWh
    (die API liefert kWh); der Hinweis „Angezeigt werden die 200 neuesten Sessions.“ bei genau 200
    Einträgen; die Karte „Monatsspitzen“ fehlt ohne Einträge; die Wallbox-Namen im Abgleich kommen aus
    `/api/limits`; eine Session über Mitternacht zeigt beim Ende das Datum („18:00–09.10. 01:15“).
    Falls falsch: nur Wortlaut.
  - **Grafana:** Die Entscheidungen stehen in `raw` mit den Feldern `text` und `codes` (nicht wie in der
    Spec `text` und `value`), `price` und `ledger` schreibt die app direkt nach `long`; das Dashboard
    folgt dem, was geschrieben wird. Der Abschnitt „Grafana“ ist ein `###` im Abschnitt 3 des
    Betriebshandbuchs (ein neuer `##` verschöbe die Nummern). Falls falsch: keine, nur Lage im Handbuch.

Planer: Plausibilität gegen Optimierer (gefunden am 18.10.2026, besteht seit Plan 3b; **festgelegt,
entschieden vom Betreiber am 18.10.2026: „Beides anpassen“**). Der Property-Test
`test_plan_plausible.py` hatte einen Horizont gefunden, in dem die Prüfung den Plan des Optimierers
verwarf („Netzladen um 07:15 rechnet sich nicht“) und der Ersatzplan galt: Der Optimierer lud billig
nach und entlud in teuren Slots je knapp unter 100 W, die Prüfung zählte nur spätere Entladung ab
100 W je Slot. Daneben gab es seltene Fälle, in denen die Prüfung recht hatte, weil die Absichten
unter 100 W kein `HALTEN` kennen (`HOLD_MIN_W`) und die geladene Energie sofort zum selben billigen
Preis verbraucht wurde. Festgelegt ist jetzt (Spec 9.5, Nachtrag vom 18.10.2026; Betriebshandbuch,
„Planung“):

- Die Spread-Regel der Prüfung zählt jede spätere Entladung über 1 W (`USED_ABOVE_W` in
  `planning/plausibility.py`). Die 100 W gelten nur noch für die Erklärungen (`MENTION_W`, vorher
  `USEFUL_W`) und für „lohnt sich“ (Netzbezug).
- Die Absichten streichen ein `NETZLADEN`, das sich an der Nachrechnung nicht rechnet
  (`_drop_unprofitable` in `planning/intents.py`), mit derselben Rechnung wie die Prüfung
  (`grid_charge_pays`). Der Slot wird `HALTEN` oder `EIGENVERBRAUCH` nach der Regel ohne Netzladen
  und geht so als gewöhnliche Absicht an den core.
- Die Prüfung selbst bleibt streng: Gelangt doch ein solches `NETZLADEN` in die Absichten, gilt der
  Ersatzplan. Für Pläne des Optimierers findet sie damit nichts mehr; gegen einen Fehler in den
  Absichten schützt sie weiter.
- Die Spread-Regel ist nicht mengenbezogen: Schon eine lohnende spätere Entladung über 1 W
  rechtfertigt jede Ladeleistung. Ein Netzladen, das fast ganz sofort verbraucht wird und nur einen
  kleinen Rest für später lässt, bleibt deshalb bestehen; wirtschaftlich ist das etwa ±0. Eine
  mengenbezogene Prüfung (geladene Energie gegen die Summe lohnender späterer Entladung) wäre der
  nächste Schritt; sie wird nur auf Wunsch gebaut.

Regressionstests mit dem festen Beispiel und einem Fall aus der gezielten Suche stehen in
`tests/unit/app/planning/test_plausibility.py` und `test_intents.py`. Befund und Optionen:
`/mnt/project-files/plaene/planer-plausibilitaet.md`.

Aufgeschobene Kleinigkeiten aus Plan 4a (aus den Reviews; keine davon gefährdet die Anlage). Was die
Nacharbeit vom 18.10.2026 erledigt hat, steht nicht mehr hier. Offen sind:

- **An der Anlage ansehen (bisher nur gegen Attrappen geprüft):**
  - Die Abfragen an InfluxDB gehen per POST an `/query`. Das ist laut Dokumentation von InfluxDB 1.x
    erlaubt, aber an einer echten Instanz nicht ausprobiert (nach dem ersten Abgleich ins Log sehen).
  - Der Zeitbereich `now-13d/d` der beiden Panels „Abrechnung je Tag“ ist gegen die Dokumentation und
    den Code von Grafana 10 geprüft, nicht an einer laufenden Instanz. Grafana rundet `/d` in der
    Zeitzone des Dashboards (`Europe/Vienna`); in einem Browser in einer anderen Zone könnte der erste
    Tag versetzt sein.
- **Einmal messen, nichts bauen:** Eine seit Monaten offene Session (das Auto steckt dauerhaft) rechnet
  ihre Zuordnung über alle Slots seit ihrem Beginn, in HA alle 15 min und auf „Laden“ jede Minute. Wie
  lang ein Lauf dauert, messen; gebaut wird erst, wenn es stört.
- **Sessions und Tracker:**
  - Springt die Uhr hinter den Beginn einer offenen Session, schreibt der Tracker kein Fahrzeug
    (praktisch ausgeschlossen).
  - Die Route liefert höchstens 200 Sessions im Zeitraum, die neuesten (`MAX_SESSIONS`; die UI sagt es
    und hält die Zahl als `SESSIONS_LIMIT`). Ein gefiltertes `sessions()` liest die ganze Spanne und
    rechnet Summen, die es verwirft (klein). `by_vehicle` weitet einen Slot, der mitten im Slot
    beginnt, und liefert Einträge mit 0 für Fahrzeuge mit Session, aber ohne Energie.
  - Die neuen HA-Sensoren und die erste Erkennung zeigen beim ersten Start bis zu 5 s „unbekannt“.
- **Einstandspreis:** Zwei Änderungen desselben Slots in derselben Mikrosekunde sind an `updated_at`
  nicht zu unterscheiden (praktisch ausgeschlossen).
- **Statistik:** Die Jahresansicht liest die Slots noch zwei- bis dreimal (linear, rund 2,5 s für ein
  volles Jahr). Das Jahr zeigt die markierten Tage des Abgleichs nicht (das Fenster ist höchstens 35
  Tage lang). Die Tabelle „Verlauf“ (`BalanceTable`) hat dieselben Regeln wie `.scroll.wide` und könnte
  sie übernehmen.
- **Verbraucher je Tag:** Beim ersten Start gibt es höchstens 31 Einzelabfragen an InfluxDB, einmal.
- **Home Assistant:** Die Prüfung der Objekt-IDs auf Kollision (`shared/config.py`) kennt die vier neuen
  IDs nicht; sie zu ergänzen ändert das core-Image und passt zu Plan 4b. **Erledigt in Plan 4b**
  (Präzisierung 4).
- **Tests und Demo-Welt:** Die Tests des Dashboards lesen den Quelltext der Schreiber (spröde, aber
  wirksam) und holen einen Helfer aus einem anderen Testmodul; `ui_world` greift auf private Mitglieder
  zu; der Akku der Demo hat 15 kWh Reserve (nur im erfundenen Verlauf, die Beispielkonfiguration hat
  6 kWh); außerhalb von 4a warten ältere Tests (Maintenance, planning, ha, core) `task.cancel()` nicht
  ab; und `pytest tests/unit/app/ledger tests/unit/test_docs.py tests/unit/app/stats` in dieser
  Reihenfolge findet in den Routentests der Statistik die Fixture `make_client` nicht (einzeln und im
  vollen Lauf tritt das nicht auf).
- **InfluxDB-Leser:** Die Schutzschicht, die nur lesende Anweisungen durchlässt, kennt reguläre
  Ausdrücke nur nach `FROM`, `=~` und `!~`; heute erzeugt kein Aufrufer etwas anderes.
- **Abnahme:** Hat eine Größe nie einen Zählerwert (etwa PV bei DC-Kopplung), steht auf der Karte
  „Abgleich“ dauerhaft „Ohne Zählerwerte: PV.“. Das ist richtig, aber bei der Abnahme zu wissen.

Entschieden in Plan 5a; die 17 Entscheidungen hat der Betreiber am 20.10.2026 mit dem Plan freigegeben
(„Ja passt“; `docs/superpowers/plans/2026-10-20-phase-5a-lastmodell.md`, „Entscheidungen“, und Spec 18,
„Stand Plan 5a“). Die Festlegungen der Umsetzung stehen unten und, soweit sie das Verhalten betreffen, in
den Absätzen „Stand Plan 5a“ der Spec und im Betriebshandbuch („Lastmodell (Phase 5a)“). Hier alles
kurz, mit der Alternative oder den Kosten, falls es falsch ist:

- **Die Entscheidungen (mit der Alternative aus dem Plan, soweit der Plan eine nennt):**
  1. **Zuschnitt der Phase 5:** 5a Lastmodell und Prognosegüte, 5b Fahrverhalten, 5c Vorheizen und
     Vorkühlen (nach Daten aus 6a). Nicht in 5a: Fahrverhalten, Vorheizen und Vorkühlen, Quantile P25/P75
     für die Last (der Planer nutzt nur P50), ein Modell für die PV (die Kalibrierung aus 3a bleibt), neue
     Sensoren in Home Assistant, Grafana.
  2. **Nur app, core unverändert:** `shared/settings.py` bekommt einen Abschnitt, den nur die app liest.
     An der Anlage genügt der Neubau von `ems-app` mit Regelbetrieb an. Falls falsch: ein Typ in `shared`
     ändert das core-Image.
  3. **LightGBM als neue Abhängigkeit** mit festen Parametern (deterministisch, ein Thread), ohne Suche
     nach Hyperparametern. Das Image wird rund 130 MB größer und braucht `libgomp1`. Alternative: ein
     eigenes lineares Modell ohne neue Abhängigkeit; es bildet Wechselwirkungen (Uhrzeit × Temperatur)
     nicht ohne Handarbeit ab.
  4. **Lerndaten der Hauslast** wie beim Profil (Abrechnung, nur vollständige Slots; HA nur mit
     `homeassistant.load_history`), Fenster 56 Tage; trainiert wird erst ab 28 Tagen mit je mindestens
     80 von 96 Slots. Falls falsch (es gibt doch eine passende HA-Entität der Hauslast): `load_history`
     wirkt auch für das Modell.
  5. **Merkmale:** Kalender, Vorlauf, Temperatur (Slot und Tagesmittel), Last vor 1 und 7 Tagen, mittlere
     Last der letzten Stunde, das Profil selbst und die gewählten Sensoren; Fehlendes bleibt leer.
  6. **Temperatur aus einem Wetterarchiv** (`weather_slot`) mit einmaliger Nachholung von 92 Tagen. Im
     Training gilt die archivierte, in der Vorhersage die aktuelle Prognose; das macht den Backtest etwas
     zu günstig, der Schattenbetrieb misst ehrlich. Alternative: die gemessene Außentemperatur aus HA; für
     die Zukunft gibt es sie nicht, als Sensor (7) kannst du sie trotzdem wählen.
  7. **HA-Sensoren als Merkmale:** bis zu 8, Wert = Mittel des letzten vollständigen Slots vor der Ausgabe,
     für den ganzen Vorlauf fortgeschrieben; Auswahl unter Einstellungen, versioniert; eine Änderung wirkt
     ab dem nächsten Training.
  8. **Ausgabezeitpunkte** 00:00, 06:00, 12:00 und 18:00 Uhr lokal, je Ausgabe alle Slots bis 72 h danach
     mit Ist-Wert; Vorhersage stündlich zur Minute 9; Ziel ist der Median (`l1`).
  9. **Training** nachts um 03:15 Uhr, beim Start ohne Modell und per Taste (Admin); 14 Versionen plus die
     aktive; Thread mit Zeitlimit 10 min.
  10. **Backtest** mit rollierendem Startpunkt über 2 bis 8 Wochen, verglichen mit dem Profil des jeweiligen
      Tages, Ergebnis je Woche und Vorlaufklasse.
  11. **Aktivierung** ab 10 % niedrigerem MAE über die letzten 14 vollen Tage, Grundlage Schattenbetrieb
      (ab 10 Tagen mit Werten), sonst Backtest, sonst Profil; ohne Hysterese. Alternative aus dem Plan:
      Abschalten erst unter 5 %, das verhinderte ein Hin und Her an der Grenze. Falls falsch: Die Regel
      täglich neu anzuwenden kann an der Grenze zwischen Modell und Profil wechseln lassen; die Planung
      nimmt jeweils nur die Prognose, nichts an der Anlage ändert sich.
  12. **Schattenbetrieb:** jede Ausgabe schreibt Profil und Modell ins Archiv, `load` ist das aktive
      Verfahren; Lücken füllt das Profil bis 10 % der Slots, darüber (oder bei einem Fehler) gilt das
      Profil; Vorhersagen liegen zwischen 0 und dem 1,5-Fachen des höchsten Trainingswerts.
  13. **Seite „Prognosen“** mit den Karten „Lastprognose“, „Prognosegüte“, „Fehler je Tag“, „Backtest“ und
      „Merkmale“, in der Navigation nach „Zeitplan“, für alle Rollen.
  14. **Sensorauswahl** unter Einstellungen → „Lastmodell“ mit dem Schalter „Lastmodell verwenden“.
  15. **Rückfall:** Scheitert das Training, bleibt die bisherige Version; fehlt die Datei, gilt die Version
      als unbrauchbar und das Profil; die Komponente `load-model` nennt den Zustand.
  16. **Abnahme:** 28 Tage ab dem Ausrollen (Modell mindestens 10 % besser oder Profil automatisch aktiv,
      dazu Training jede Nacht, keine Fehler in `load-model`, Rückfall beobachtet).
  17. **Nichts geht von selbst live:** 5a schaltet nichts an der Anlage, es ändert nur die Eingabe des
      Planers.
- **Festlegungen der Umsetzung (R1 bis R11 des Controllers, je mit Kosten, falls falsch):**
  - **R1: Entscheidung als JSON.** `ModelRegistry.decide` nimmt die Entscheidung als Zuordnung (die
    Aktivierung als JSON) und das Kennzeichen `active` statt eines Objekts, weil die Registry (Task 7) vor
    der Aktivierung (Task 8) gebaut wurde. Falls falsch: eine kleine Signaturänderung im Lerndienst.
  - **R2: Genau 10 % aktiviert.** Der Vergleich hat eine Toleranz von 1e-9, damit eine Verbesserung von
    genau 10 % trotz Rundung der Gleitkommazahlen aktiviert, wie die Spec „mindestens 10 %“ sagt. Falls
    falsch: Der Grenzfall verschiebt sich um 1e-9, ohne praktische Wirkung.
  - **R3: Migrationstests** stehen in `tests/unit/app/test_db.py` (dem bisherigen Ort), nicht in einer
    neuen Datei. Falls falsch: nur der Ort der Datei.
  - **R4: Volle Läufe gestückelt.** Je Task lief die Prüfkette in vier parallelen Teilen ohne Abdeckung;
    der volle Lauf mit `--cov` kam einmal am Ende (Task 14). Falls falsch: Eine Abdeckungslücke fiele erst
    am Ende auf.
  - **R5: Wochenzahl als eigene Funktion.** `week_count(days_with_data)` in `backtest.py` rechnet die
    Zahl der Testwochen (höchstens 8, mindestens 2, sonst keine); eine Woche, deren Training scheitert,
    entfällt, bleibt keine, gibt es keinen Backtest. Falls falsch: ein Test bräuchte 8 Trainings.
  - **R6: Commit-Zeilen einheitlich.** Alle Commits der Umsetzung tragen dieselbe Zuordnung (die Zeilen
    aus der Vorgabe der Sitzung); ein Unterauftrag mit anderer Vorgabe wurde angeglichen. Falls falsch:
    nur der Text der Schlusszeile.
  - **R7: Backtest als Grundlage der Aktivierung** nur, wenn er im Fenster der letzten 14 Tage an
    mindestens 10 Tagen Werte hat (Tage ohne Werte zählen nicht), sonst „zu wenige Tage“. Die Reihenfolge
    der Gründe ist: Schalter aus, kein Modell, dann die Rechnung; die Zahlen der Grundlage werden auch bei
    Schalter aus und ohne Modell gefüllt, damit die Seite den Vergleich zeigt. Falls falsch: nur die
    Logik der Aktivierung ändert sich.
  - **R8: Der Satz rundet ab.** Der Satz „Das Modell ist nur {x} % genauer, nötig sind 10 %“ rundet
    positive Werte ab (9,6 % heißt „nur 9 %“), damit er sich nie widerspricht. Falls falsch: nur der
    angezeigte Text.
  - **R9: Archivarten früher.** Die Arten `load_profile` und `load_model` gibt es im Archiv schon ab
    Task 9, denn der Lerndienst liest die Schatten; die Güte rechnete bis Task 10 weiter nur PV und Last.
    Falls falsch: Task 10 hätte nur zwei Zeilen verschoben.
  - **R10: Mindestdaten über 56 Tage.** Die 28 Tage zählen die Tage mit Daten in den 56 Trainingstagen,
    der Backtest bekommt die Zahl über das ganze geladene Fenster (56 Tage plus bis zu 8 Wochen).
    „Zu wenig Daten“ heißt damit: Das Training hätte zu wenig. Falls falsch: eine einzelne Zählung.
  - **R11: Anteil am Gewinn.** `in_model` eines Sensors bleibt „Anteil am Gewinn über 0“ (die Seite zeigt
    „nicht im Modell“ für einen Sensor ohne Anteil); `GET /api/learning/load` nimmt den Zustand des
    Dienstes unverändert und ergänzt nur `method` aus der zuletzt ausgegebenen Lastprognose; die Route
    der Taste ist `async`, weil sie die laufende Ereignisschleife braucht. Falls falsch: Der Dienst
    müsste zusätzlich speichern, welche Sensoren im Training Werte hatten.
  - **Wetterarchiv ohne Aufräumen:** `weather_slot` wächst um rund 35 000 Zeilen im Jahr, das Training
    braucht 112 Tage; ein Aufräumen ist nicht gebaut. Falls falsch: ein Löschlauf im Nachtlauf.
  - **Weitere Festlegungen beim Bau:** Der Feiertag kommt aus `is_holiday()` in `app/daytype.py`. Bei der
    doppelten Stunde im Herbst gilt für die Last vor einem Tag der erste Durchlauf; an Tagen ohne
    passenden Slot ist das Merkmal leer. Der Trainingssatz begrenzt Ausgaben und Ziele durch das Ende des
    Fensters. Die Wochen des Backtests enden an der lokalen Mitternacht, damit an der Zeitumstellung
    nichts doppelt zählt. Die Temperatur-Historie wird beim Start über ein Ereignis abgewartet (höchstens
    600 s), bevor das erste Training beginnt. Die Schemas des UI sind valibot (der Plan nannte Zod). Die
    Vorlaufklasse heißt auch auf „System“ „0–6 h“.

Aufgeschobene Kleinigkeiten aus Plan 5a (aus den Reviews; keine davon gefährdet die Anlage, denn das
Modell ändert nur die Eingabe des Planers, und das Profil fängt jeden Fehler ab). Reine Testpflege ist hier
nicht aufgeführt. Offen sind:

- **Wetterarchiv:** Die Nachholung stempelt `issued_at` vor dem Abruf, deshalb können wenige Slots im
  Überlapp mit einer Prognose einen etwas älteren Wert tragen. Das Archiv wird nicht aufgeräumt.
- **HA-Sensoren:**
  - Die Kandidatenliste fragt den letzten Wert und die letzte Einheit mit zwei Selektoren; InfluxDB
    liefert dann nicht die Zeit des Punkts. Der Tiebreak „jüngste Reihe“ wirkt nur für Entitäten in
    mehreren Messungen. Das betrifft nur einen Wechsel der Einheit und die Anzeige der Einheit.
  - Steht eine Entität in der Messung der Entität und in der der Einheit, gewinnt die spätere still.
  - Hilfen zur Zahl und zum HA-Verweis gibt es dreimal (hier, bei den Verbrauchern und in der
    Lastprognose).
  - Ohne HA-Reader liefert der Server eine leere Liste ohne Grund, und die Einstellungen markieren jeden
    gespeicherten Sensor mit „nicht in HA gefunden“.
- **Merkmale und Modell:**
  - Ein `NaN` in der Last vergiftet `recent_w` (das Training filtert endliche Werte).
  - Das Training berechnet alle Spalten und dünnt sie dann aus; das ist langsamer als nötig.
  - Beim Laden wird `max_w` nicht geprüft; ein `NaN` aus der Vorhersage würde still zu 0, und ein Fehler von
    LightGBM bei der Vorhersage ist nicht zugeordnet (die Lastprognose fängt jede Ausnahme und nimmt das
    Profil).
- **Registry:** `decide` prüft die Art der Version nicht; scheitert der Commit nach dem Schreiben der
  Datei, bleibt eine Modelldatei verwaist (nicht dokumentiert); das in `decision` gespeicherte `active` kann
  nach einem späteren Wechsel veraltet sein (die API zeigt die Spalte `active`); die Gewichtung des
  Backtests steht in `backtest_json` und in `improvement()` doppelt.
- **Aktivierung:** Hat das Profil im Fenster keinen Fehler, aber das Modell, lautet der Satz „nur 0 %
  genauer“. Je Slot und Vorlaufklasse gilt die jüngste Ausgabe, im Zweifel die mit dem kürzesten Vorlauf
  (eine Entwurfsentscheidung). Ein `or 0.0` ist überflüssig.
- **Lerndienst:**
  - Das Training um 03:15 Uhr fällt mit der nächtlichen Sicherung der Datenbank (ebenfalls 03:15 Uhr,
    `BACKUP_AT`) zusammen; beide können gleichzeitig laufen.
  - Ist beim Start die gespeicherte Entscheidung „disabled“ und der Schalter inzwischen an, bewertet der
    Dienst erst um 03:15 Uhr neu. Eine Bewertung, die hinter einem langen Training wartet, behält bis dahin
    „disabled“. Ein zweites Training kann hinter einer Bewertung
    in die Warteschlange (sie laufen nacheinander). `close()` wartet nicht auf andere Datenbankaufrufe im
    Thread.
  - Bei Lücken fragt die Lastprognose die HA-Historie über 112 Tage ab (`_ha_samples`); an der Anlage ist
    `load_history` nicht gesetzt, dort greift das nicht.
- **Lastprognose:** Ein Satz im Docstring von `_predict` ist verschachtelt; `model.active()` wird zweimal
  gelesen; jede Ausgabe schreibt das Archiv in drei getrennten Aufrufen (je ein Thread).
- **UI:**
  - Nach einer Antwort 409 („Training läuft schon.“) bleibt die Meldung stehen und hat die Stufe Fehler.
  - Die Seite fragt den Stand eines Trainings auch im verborgenen Tab ab.
  - Der Verlauf „Fehler je Tag“ markiert veraltete Werte nicht; die Karte „Lastprognose“ zeigt zusätzlich
    zum Satz einen Status-Punkt.
  - Die Prüfung auf die Obergrenze von 8 beim Wählen eines Sensors ist im Code nicht erreichbar (die
    Auswahl ist ab 8 gesperrt).
- **Tests und CI:** In `stats.spec.ts` („Statistik wechselt den Zeitraum“) zeigte die Kachel „Kosten“ in
  einzelnen Läufen „–“ (GitHub-CI, Lauf 414, und je der erste lokale Lauf der Tasks 12 und 13). Ursache
  war der geteilte Demo-Zustand: `settings.spec.ts` bewertete ohne `page.clock` vom Monatsersten bis
  zum echten Datum neu, und weil die Demo die Bezugspreise der 14 Tage Verlauf nur in die Abrechnung
  schrieb, nicht in den Preisspeicher, verloren diese Slots dabei ihren Preis. Seit der Nacharbeit legt
  die Demo die Börsenpreise in den Preisspeicher (Neubewerten ändert die Kosten nur mit dem Tarif),
  und der Test bewertet mit der Uhr der Demo nur den 09.10. neu
  (`test_reprice_keeps_the_demo_costs`).
- **Abnahme:** Das Modell kann gleich nach dem ersten Training aktiv werden: Bis 10 Tage Schattenbetrieb
  vorliegen, entscheidet der Backtest, der mit der archivierten Temperatur rechnet und in den ersten
  Wochen wenig Historie für das Profil hat und damit etwas zu günstig ist. Wer zuerst nur beobachten
  will, schaltet „Lastmodell verwenden“ gleich nach dem Start aus (Betriebshandbuch, „Lastmodell
  ausrollen (Plan 5a)“, „Reiner Schattenbetrieb“). Die Dauer eines Trainings an der Anlage und der Speicher dafür sind nicht gemessen.

Aufgeschobene Kleinigkeiten aus Plan 5b (Deferred minors 5b), aus den Reviews der Tasks 1 bis 14, dem
Gesamtreview und dem Re-Review der Fix-Welle. Keine davon gefährdet die Anlage: Gelernte Werte erreichen die
Safety-Schicht nie, und die Automatik ist ab Werk aus.

- **Daten und Tage:** Eine Abfahrt genau um 00:00:00 geht verloren; der Ladestand bei der Ankunft sieht nur
  Ansteckvorgänge ab der Ankunft; der Seed liest nur den jüngsten Punkt (90 Tage Rückblick, `raw` hält 90 Tage);
  `load_history` liest die Ansteckvorgänge synchron im Event-Loop (kleine Abfrage); `WINDOW_DAYS` steht in zwei
  Modulen; `presence[0]` setzt sortierte Punkte voraus.
- **Profil und Trefferquote:** Die Trefferquote liegt nur im Speicher (nach einem Neustart bis zum Startlauf
  n = 0) und wird nach einem gescheiterten Schritt nicht als veraltet markiert; die Prüfung auf `away` in
  `driving_quality` ist redundant; `driving_profile` importiert `Source` aus dem Lesemodul.
- **Absicht und Automatik:** Ein vergangenes `departure_on` bleibt in der Absicht stehen (fällt auf die tägliche
  Uhrzeit zurück); `departure_on` nimmt in der API auch einen Zeitstempel an; nach einem „clamped“-Ergebnis zeigt
  der Zustand kurz „applied“ mit Pause (der nächste Check korrigiert); vor dem ersten Check nach dem Start
  zeigt ein eingeschaltetes Auto kurz „nicht auf Geplant“; Schalter und Änderungsprotokoll stehen in zwei
  Transaktionen (ein Fehler im Protokoll gibt 500, obwohl der Schalter gespeichert ist).
- **Planer:** Kein Test mit einer Ankunft außerhalb einer Slotgrenze; „Kein Plan · lädt spätestens ab“ und
  „Fertig um“ zeigen bei einer Abfahrt in mehreren Tagen keinen Tag; zwei Service-Tests prüfen mit
  `sleep(0.1)`-Fenstern.
- **Code und Tests:** private Importe (`_local_instant`, `_Model`); `VehicleKey` doppelt zu `WallboxKey`;
  `WEEKDAYS` über mehrere Zeilen; einzelne Tests greifen auf private Felder zu oder pinnen Grenzen
  (30 min, 25-h-Tag im Herbst) nur teilweise; kein Test für „clamped“ und `CoreDown` ohne Rückruf; kein Test
  gegen eine Flächenfüllung erwarteter Autos im Diagramm.
- **UI und Doku:** Die Demo-Welt schaltet die Automatik ohne Protokolleintrag ein; NBSP vor „%“ wie im übrigen
  `format.ts`; die Prognosen-Seite lädt die vollen Einstellungen nur für den Namen der Entität; der
  Rollback-Befehl 5b nennt „Regelbetrieb aus“ nur im Einleitungssatz.

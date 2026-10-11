# Phase 2b – Aktoren: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BindaEMS bekommt Aktoren für EVCS, Victron und Tesla und darf damit erstmals auf Geräte schreiben. Jeder Befehl geht durch die Safety-Schicht; im Dry-Run wird er nur protokolliert. Dazu kommen:
- ein Ausführer, der Safety-Urteil, Senden, Rücklesen und Entscheidungslog verbindet
- der sichere Zustand (Spec 8.4, 8.5) und die Übergabe am Ende des Live-Betriebs
- der Heartbeat nur, solange der core regelt; das löst den Blocker „core hängt in OBSERVE“
- das Entscheidungslog im core: Spool, Stream `decision`, `GET /v1/decisions`
- die Erkennung fremder Änderungen (7.9)
- das Werkzeug für das Prüfprotokoll Teil 2 und die Watchdog-Aktion für den hub4-Override

Eine Regelung gibt es in 2b noch nicht, sie kommt mit 2c. Befehle entstehen im core nur aus dem sicheren Zustand und der Übergabe. Das Teil-2-Werkzeug schreibt selbst, mit Rückfrage beim Betreiber.

**Architecture:**
- **Rein in `core/control`** (ohne I/O, Zeit als Argument):
  - Aktor-Schnittstelle (`actuator.py`) und Ausführer (`executor.py`)
  - sicherer Zustand (`safe_state.py`) und Übergabe (`ownership.py`)
  - fremde Änderungen (`foreign.py`) und der Aufbau des `SafetyContext` (`context.py`)
- **Konkrete Aktoren in `core/actuators`:** EVCS und Victron schreiben per MQTT über den Victron-Adapter, der Tesla über Tessie in einer Hintergrundaufgabe.
- **Entscheidungen:** Typ und Text in `shared/decisions.py`, Spool in `core/persistence/decision_spool.py`, Stream und API im core.
- **Laufzeit:** verbindet alles im Regelzyklus; der Heartbeat folgt der neuen Regel.
- **Teil-2-Werkzeug** `bindaems.tools.verify_part2`: nutzt dieselben Adapter und Aktoren, eigenständig wie die Probe.

**Tech Stack:** wie Plan 2a: Python 3.12 mit uv, pydantic v2, FastAPI, aiomqtt, pymodbus und httpx; Tests mit pytest, hypothesis, respx und pytest-cov; ruff, mypy und import-linter. Cerbo-Watchdog Python ≥ 3.8.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, vor allem 3.1, 3.7, 5.3, 6.1–6.5, 7.1, 7.2, 7.9, 7.10, 8, 17.2 und 18.
- Ausführende lesen Spec und Plan zusammen; Verweise wie „Spec 8.5“ beziehen sich darauf.
- Vorgänger sind die Pläne 1a–1c und 2a (`docs/superpowers/plans/2026-10-10-phase-2a-sicherheitsbasis.md`, Entscheidungen in `2026-10-10-phase-2a-entscheidungen.md`), alle umgesetzt. Ihre Global Constraints gelten weiter.

## Einordnung

Phase 2 ist laut Plan 2a in vier Pläne geteilt: 2a Sicherheitsbasis (umgesetzt), **2b Aktoren** (dieser Plan), 2c Regelung, 2d Bedienung.

Ergebnis von 2b an der Anlage:
- Prüfprotokoll Teil 2 für EVCS und Tesla gemeinsam durchgeführt.
- Der Victron-Teil folgt, sobald der Betreiber Dynamic ESS abschaltet, also vor der Live-Schaltung von Victron.
- Live im Regelbetrieb geht in 2b noch kein Aktor; das beginnt nach dem Dry-Run (nach 2d).

Stand an der Anlage am 11.10.2026:
- Watchdog 1.0.0 installiert, Probe bestanden (`docs/verification/2026-10-11-watchdog-probe.md`).
- Die EVCS antwortet BindaEMS per Modbus, seit die IP-Whitelist stimmt: Produkt-ID 0xC026, Firmware v2.09, manueller Modus.
- Dynamic ESS bleibt an, bis Victron live geht. Peak-Shaving-Importlimit 20 A je Phase mit „immer“, gewollt.

## Entscheidungen des Betreibers (11.10.2026)

1. **Heartbeat** nur, solange der core regelt (Präzisierung 2).
2. **Übergabe:** erst die Sicherwerte, dann die Freigabe des Watchdogs (Präzisierung 3).
3. **EVCS-Schreibweg** über den GX-Dienst `evcharger` des Cerbo, nicht direkt per Modbus (Präzisierung 1).
4. **Importlimit** 20 A je Phase ist gewollt (Spec 8.1).

## Präzisierungen gegenüber der Spec

1. **Schreibwege (6.2, 6.3, 6.5).**
   - **EVCS** per MQTT über den GX-Dienst `evcharger`:
     - Sollstrom: `W/<portal>/evcharger/<Instanz>/SetCurrent` in ganzen Ampere.
     - Laden: `W/<portal>/evcharger/<Instanz>/StartStop`, 1 = an, 0 = aus.
     - Die Instanz steht in `victron.instances.evcharger`; ohne sie ist die EVCS nicht steuerbar.
     - Zurückgelesen wird aus dem eigenen Modbus-Abbild (`wallbox.<n>.set_current_a`, `wallbox.<n>.start_stop`), sonst aus den GX-Werten (`gx_set_current_a`, `gx_start_stop`).
     - Den Modus schreibt BindaEMS nie. Gelesen wird weiter direkt per Modbus (6.3).
     - EMS und Watchdog nutzen damit denselben GX-Dienst; der Betreiber hat das so entschieden.
   - **Victron** per MQTT:
     - Netz-Sollwert als flüchtiger Override `W/<portal>/hub4/0/Overrides/Setpoint` in W. `null` hebt ihn auf; das ist vorläufig, bis Teil 2 Punkt 1 es bestätigt.
     - Entladegrenze persistent: `settings/0/Settings/CGwacs/MaxDischargePower` in W, −1 = unbegrenzt.
     - Min-SOC persistent: `settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit` in %.
     - Zurückgelesen wird über `ess.override.setpoint`, `ess.max_discharge_w` und `ess.min_soc_pct`.
     - Modbus TCP zum Cerbo (Register 2716/2717) bleibt ungenutzt.
   - **Tesla** über Tessie:
     - `POST https://api.tessie.com/{vin}/command/start_charging`, `…/stop_charging` und `…/set_charging_amps?amps=N`, jeweils mit `wait_for_completion=true`, Timeout 60 s.
     - Bestätigt ist ein Befehl, wenn Tessie `{"result": true}` liefert.
     - Tessie weckt das Fahrzeug bei Befehlen selbst. BindaEMS sendet deshalb nur, wenn der Cache `online` meldet und das Fahrzeug zu Hause und angesteckt ist (3.7, 6.5: nie wecken). Weckregeln für Sofort und Geplant kommen mit 2c.
   - Die erlaubten MQTT-Pfade stehen in einer festen Liste (`writable_paths`); alles andere bleibt lesend.
2. **Heartbeat nur, solange der core regelt (8.6; Entscheidung 1).**
   - **Bedarf** (`watchdog_required`): ein Victron- oder EVCS-Aktor mit `live_allowed`, der live gewünscht ist (Regelbetrieb an und Betriebsart live) oder gerade übergeben wird. Neu gegenüber 2a: Ohne Regelbetrieb besteht kein Bedarf.
   - **Den Zähler** sendet der core nur, wenn Bedarf besteht, der Zyklus fehlerfrei war (Präzisierung 5) und er
     - in CONTROL, DEGRADED oder SAFE steht oder
     - in OBSERVE steht, alle übrigen Gate-Bedingungen erfüllt sind (Regelbetrieb, Messwerte seit 30 s frisch, Phasen bestätigt, kein Grund für SAFE) und der Watchdog nicht für ihn scharf ist. Das ist das Scharfschalten.
   - **Folge:**
     - Hängt der core nach einem Neustart in OBSERVE, bleibt der Heartbeat aus. Der Watchdog löst nach `timeout_s` aus und setzt die Sicherwerte.
     - Ein Neustart, der samt 30 s Frische unter 90 s bleibt, löst nichts aus.
   - **Scharf für diesen core** (`armed_for_core`):
     - `/State` = 1,
     - `/Heartbeat` zeigt einen der drei zuletzt gesendeten Zähler,
     - `/TimeoutSeconds` = `victron.watchdog.timeout_s`.

     Nur das zählt für das Gate und die wirksame Betriebsart. Weicht der Timeout ab, kommt der Alarm `watchdog.timeout`.
   - **Freigabe 0:**
     - Nur ohne Bedarf und wenn kein Victron- oder EVCS-Aktor mehr Werte des EMS halten kann (Präzisierung 3).
     - Zeigt der Watchdog 2 × `heartbeat_s` nach der Freigabe noch „scharf“, sendet der core die 0 erneut (verlorene Freigabe, Review 2a).
     - Steht er ohne Bedarf und ohne Werte des EMS seit 60 s auf „ausgelöst“, gibt der core ihn mit der 0 frei (Aufräumen nach einer Auslösung).
   - Beim Herunterfahren sendet der core weiter nichts.
3. **Übergabe am Ende des Live-Betriebs (8.5, 8.7; Entscheidung 2).**
   - **Werte des EMS** (`Ownership.owned`):
     - Ein Aktor hält Werte des EMS, sobald der core ihm einen Wert gesendet hat.
     - Beim Start gelten alle Aktoren als owned, die die gespeicherte Absicht live anfordert und `config.yaml` freigibt: Ihr Gerät kann noch Werte vom letzten Lauf halten.
   - **Übergabe:**
     - Ein owned Aktor, der nicht mehr live gewünscht ist (Dry-Run oder Regelbetrieb aus), bekommt seine Sicherwerte (8.5) als dringende Befehle.
     - Sind alle zurückgelesen, ist er übergeben und nicht mehr owned.
     - Während der Übergabe ist er wirksam live, falls `live_allowed` gilt und bei Victron und EVCS der Watchdog für den core scharf ist. Der Grund lautet „Übergabe: Sicherwerte werden gesetzt.“. Geschrieben werden nur seine Sicherwerte.
     - Regelbetrieb aus bei laufender Übergabe: Der core bleibt in DEGRADED (aus SAFE: in SAFE) mit „Regelbetrieb wird beendet: Übergabe der Aktoren läuft.“ und geht erst danach nach OBSERVE.
     - Danach folgt die Freigabe 0, wenn kein Aktor den Watchdog mehr braucht.
   - **Scheitert die Übergabe** (3 Versuche, Präzisierung 4):
     - Es gibt einen Alarm und keinen Heartbeat mehr (Präzisierung 5).
     - Bei Victron und EVCS löst der Watchdog aus und setzt die Sicherwerte. Sobald sie zurückgelesen sind, gilt die Übergabe als erledigt, und der core gibt frei.
     - Beim Tesla bleibt nur der Alarm.
   - **Kann der core nicht schreiben,** etwa in OBSERVE nach einem Neustart, übergibt er nicht und gibt nicht frei. Der Watchdog löst aus, weil der Heartbeat ausbleibt; danach geht es weiter wie oben.
   - **Gesperrte Aktoren** (7.9) werden nicht übergeben. Sie verlieren „owned“ sofort, denn ein anderes System regelt sie.
4. **Sicherer Zustand (8.4, 8.5).**
   - **Ziele:**
     - In SAFE bekommt jeder nicht gesperrte Aktor seine Sicherwerte (`HardLimits.safe_commands`).
     - Bei „Netzausfall (Inselbetrieb).“ kommt für EVCS und Tesla zusätzlich „Laden aus“ dazu (8.4).
     - Übergaben (Präzisierung 3) sind Ziele in jedem Zustand, in dem der core schreibt.
   - **Wirksam live geschaltete Aktoren:** `SafeStateKeeper` sendet je Aktor und Art einen Befehl, wenn
     - der zurückgelesene Wert nicht der Sicherwert ist,
     - kein Schreibvorgang offen ist und
     - der letzte Versuch mindestens 10 s her ist.

     Je Vorfall gibt es höchstens 3 Versuche. Danach gilt die Art als gescheitert (Alarm `actuator.failed.<aktor>`), bis der Wert stimmt.
   - **Dry-Run-Aktoren** bekommen je Vorfall einmal die Entscheidung „Dry-Run“, ohne Wiederholung.
   - Ein Vorfall endet, sobald der Aktor kein Ziel mehr ist; der nächste beginnt mit neuen Versuchen.
   - Die Befehle sind dringend, mit dem Grund `safe_state`, `handback` oder `grid_outage`. Die Safety-Schicht prüft sie wie jeden Befehl (Plan 2a, Präzisierung 4).
5. **Fehlerfreier Zyklus (7.1, Schritt 7).**
   - Fehlerfrei ist ein Zyklus, wenn keine Ausnahme auftrat, für keinen Victron- oder EVCS-Aktor ein Schreibvorgang scheiterte (Fehler, Timeout, nicht gesendet) und keine Sicherwert-Art dieser Aktoren gescheitert ist.
   - Ein einzelner Fehler lässt nur diesen Heartbeat ausfallen; der nächste fehlerfreie Zyklus holt ihn nach. Erst anhaltende Fehler lassen den Watchdog auslösen.
   - Fehler beim Tesla zählen nicht.
6. **Ausführung und Rücklesen (6.1).**
   - **Ablauf je Befehl** (`CommandExecutor.execute`):
     - Ist für Aktor und Art noch ein Schreibvorgang offen, wird der Befehl nicht betrachtet; die Safety-Schicht wird dann nicht aufgerufen.
     - Sonst ruft er genau einmal `SafetyLayer.authorize` auf.
     - Soll gesendet werden, prüft er die Fähigkeiten, sendet und wartet auf die Bestätigung.
   - **Bestätigt** ist ein Wert, wenn der zurückgelesene Wert dem gesendeten entspricht. Toleranzen und Fristen stehen in der Tabelle; die Frist läuft ab dem Senden.

     | Art | Bestätigt bei | Frist |
     |---|---|---|
     | Strom (EVCS) | ±0,4 A | 10 s |
     | Laden (EVCS) | gleich | 10 s |
     | Netz-Sollwert | ±1 W, oder beide `None` | 10 s |
     | Entladegrenze | ±1 W | 10 s |
     | Min-SOC | ±0,5 % | 10 s |
     | Tesla | Tessie liefert `result: true` | 75 s |
   - Nach der Bestätigung erwartet die Erkennung fremder Änderungen diesen Wert (Präzisierung 8).
   - **Tesla:** Nach 3 gescheiterten Befehlen in 15 min ist er für 15 min nicht steuerbar (6.5, 8.4). Dazu kommt der Alarm `actuator.unavailable.<aktor>`.
7. **Entscheidungslog (7.10).**
   - **Protokolliert werden:**
     - jeder gesendete Befehl mit Ergebnis: ok, Fehler, Timeout oder nicht gesendet
     - jedes Dry-Run-Urteil, das einen Wert ergäbe
     - jede Ablehnung; die gleiche Ablehnung desselben Aktors und derselben Art höchstens alle 60 s

     `UNCHANGED` und `DEFERRED` werden nicht protokolliert.
   - **Felder:**
     - `id`, `ts` (Wanduhr, UTC), `actuator`, `kind`
     - `requested` und `applied`, `dry_run`, `outcome`, `result`
     - `codes`: Gründe des Befehls, dann Befunde der Safety-Schicht
     - `text` auf Deutsch
     - `inputs`: in 2b SOC, Akkuleistung und Netzstrom je Phase; 2c ergänzt Preis, Überschuss, Budget und Plan
   - **Spool:** JSON-Zeilen je Tag unter `core.data_dir/decisions/`, höchstens 7 Tage und 50 MB. Ein Fehler beim Schreiben unterbricht den Zyklus nicht; er kommt ins Log.
   - **Stream:** `{"type": "decision", "data": …}`.
   - **API:** `GET /v1/decisions?since=<ISO>&limit=<n>` (Standard 1000, höchstens 5000) liefert `{"decisions": […], "more": bool}`. Die app speichert Entscheidungen erst ab 2d.
8. **Fremde Änderungen (7.9).**
   - **Beobachtet** werden EVCS-Strom, Victron-Netz-Sollwert-Override und Tesla-Strom, aber nur bei owned Aktoren und erst, nachdem der core einen Wert bestätigt hat.
   - **Prüfung:** alle 10 s, zusammen mit der Selbstprüfung.
     - Weicht der Ist-Wert ab und ist kein Schreibvorgang offen, zählt das als Treffer.
     - Nach 3 Treffern in Folge gilt der Wert als fremd geändert. Ab dann wird der neue Wert erwartet.
   - **Ursache:** der Alarm `competitor.foreign.<aktor>` (Fehler) für `safety.lock_release_s` ab der letzten Erkennung.
     - Er sperrt den Aktor wie jede andere Ursache (2a), und HA zeigt „Mitregelndes System“.
     - Der gesperrte Aktor verliert „owned“ (Präzisierung 3).
   - Der Tesla-Ladeplan bleibt wie in 2a.
9. **Uhr (Review 2a).**
   - **`AnchoredClock`** ist die Wanduhr beim Start plus die monotone Zeit seither. Ein Sprung der Wanduhr (NTP) wirkt sich damit erst nach einem Neustart aus.
     - Mit ihr rechnen Safety-Schicht, Rate-Limiter, Schreibbudget, Fristen und Heartbeat.
     - Messwerte, Viertelstunden und die Zeitpunkte der Entscheidungen bleiben bei der Wanduhr.
   - **Rate-Limiter:** Seine Einträge für Aktoren mit Stundenlimits (Tesla) überstehen einen Neustart (`rate_limits.json`).
     - Einträge, die älter als 1 h sind, fallen beim Laden weg.
     - Eine unlesbare Datei gilt als leer; das kommt ins Log.
   - **Schreibbudget:** Einträge in der Zukunft zählen weiter, das ist die sichere Seite. Liegt einer mehr als 5 min in der Zukunft, kommt der Alarm `safety.clock`.
10. **Watchdog am Cerbo, Version 1.1.0 (8.6).**
    - **„Ausgelöst“ übersteht einen Neustart des Cerbo als „scharf“:** Bleibt der Heartbeat danach aus, löst er erneut aus. Die Aktionen lesen zuerst und schreiben nur, was abweicht.
    - **`fsync`:** `state.json` und die Dateien des core sichern nach `os.replace` auch das Verzeichnis.
    - **Override-Aktion:** Mit `victron.live_allowed` kommt die Aktion „Netz-Sollwert-Override aufheben“ dazu (`com.victronenergy.hub4`, `/Overrides/Setpoint`, Wert `null`). Ohne Victron-Freigabe gehört der Override nicht dem EMS, denn DESS nutzt ihn; er bleibt dann unberührt.
    - **Probe:** Sie liest `/LastTripFailures` nach „Wieder scharf“; dann ist der Wert der Endstand.
    - **Version:** Der core erwartet 1.1.0. Läuft noch 1.0.0, meldet er die bekannte Warnung „bitte mit install.sh aktualisieren“.
11. **Prüfprotokoll Teil 2 (17.2).**
    - **Aufruf:** `python -m bindaems.tools.verify_part2 --punkt evcs|tesla|victron`.
    - **Interaktiv** wie die Probe: Rückfrage vor jedem schreibenden Schritt, `--yes` überspringt sie.
    - **Bericht:** `JJJJ-MM-TT-pruefprotokoll-teil2-<punkt>.md`, die Punkte heißen 17.2-x.
    - **Sicherheit:**
      - Es nutzt dieselben Adapter und Aktoren wie der core und prüft jeden Wert mit den Regeln 1–3.
      - Nach jedem Schritt und bei Abbruch (Strg+C) stellt es die vorgefundenen Werte wieder her.
    - **Der core** läuft dabei im Dry-Run weiter. Seine Erkennung fremder Änderungen beobachtet nur owned Aktoren und stört nicht.
    - **`victron`** verweigert, solange Dynamic ESS aktiv ist, denn DESS nullt fremde Overrides (3.2). Dieser Teil läuft, wenn der Betreiber DESS vor der Live-Schaltung von Victron abschaltet.
12. **Aktor-Schnittstelle (6.1).** Jeder Aktor bietet:
    - **`capabilities(snap, now)`:** steuerbar ja oder nein, mit Gründen aus der Tabelle „Texte“
    - **`present(snap)`:** die zurückgelesenen Werte je Art, für `SafetyContext.present` und das Rücklesen. Beim Strom ist das der Sollstrom, nicht der Messstrom (Review 2a).
    - **`send(kind, value)`:** stößt das Schreiben an und blockiert nie. Ergebnis ist `None` oder ein deutscher Grund, warum nicht gesendet wurde.
    - **`confirm(kind, value, snap)`:** bestätigt, offen oder gescheitert
13. **Kleine Punkte aus dem Gesamtreview 2a**, vor dem Verdrahten der Safety-Schicht:
    - **Netz-Sollwert:**
      - `SafetyContext.base_grid_setpoint_w` darf `None` (unbekannt) sein. Die Regeln prüfen ihn mit `finite_number`.
      - Ist der wirksame Ist-Sollwert unbekannt, lehnt Regel 4 beim Netz-Sollwert jeden Zahlenwert ab; nur `None` (Override aufheben) geht durch. `toward_safe` gilt dann nur für `None`.
    - **Befehlsarten** werden mit `==` verglichen, nicht mit `is`.
    - **`invalid_value`** kürzt die Darstellung des Werts auf 60 Zeichen.
    - **`VictronMqttAdapter`:**
      - Endet ein Hilfs-Task (Sender oder Keepalive), endet die Sitzung.
      - `write` liefert ohne laufende Sitzung `False` und wird nur aus dem Event-Loop aufgerufen.

## Global Constraints

- **Gilt weiter:** alle Global Constraints aus 1a, 1b und 2a.
- **Schreibwege in 2b, abschließend:**
  - **MQTT auf dem Cerbo** nur über `VictronMqttAdapter.write` und nur auf Pfade aus `writable_paths(cfg.victron)`:
    - den Heartbeat
    - `hub4/0/Overrides/Setpoint`
    - `settings/0/Settings/CGwacs/MaxDischargePower`
    - `settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit`
    - `evcharger/<Instanz>/SetCurrent` und `evcharger/<Instanz>/StartStop` für jede Instanz in `victron.instances.evcharger`
  - **Tessie:** nur `start_charging`, `stop_charging` und `set_charging_amps`.
  - **Prüfung:**
    - Jeder Befehl des core geht durch `SafetyLayer.authorize`.
    - Das Teil-2-Werkzeug prüft mit den Regeln 1–3 und fragt den Betreiber.
  - **Nie geschrieben werden:** EVCS-Modus, Maximalstrom der EVCS (5017), ESS-Modus, BatteryLife, Dynamic ESS, Ladefenster, Peak-Shaving-Grenzen, Register 2716/2717.
- **Reinheit:** `core.safety` und `core.control` importieren zusätzlich nichts aus `core.actuators` (Importvertrag, Task 17). Aktoren erreichen sie nur über das Protokoll `Actuator`, das Entscheidungslog nur über `DecisionSink`.
- **Abdeckung:** Zeilenabdeckung ≥ 90 % für `core/safety`, `core/control` und neu `core/actuators`, ≥ 75 % für das ganze Paket.
- **Ablage des core:** neu sind `rate_limits.json` und das Verzeichnis `decisions/` unter `core.data_dir`.
- **Zeit:** Safety-relevante Zeit kommt von `AnchoredClock`. Reine Module bekommen sie als Argument.
- **Nicht blockieren:**
  - Der Zyklus wartet nie auf ein Gerät.
  - MQTT-Schreiben reiht nur ein; Tesla-Befehle laufen als Hintergrundaufgabe.
- **Cerbo-Watchdog:** wie in 2a, Version 1.1.0.
- **Texte:** Gründe, Alarme und Entscheidungstexte stehen exakt in der Tabelle „Texte“; Tests prüfen den Wortlaut. Zahlen kommen über `de()`.

## Review Focus

1. **Neustart des core, während die EVCS live gewünscht ist:**
   - Zurück binnen 90 s, einschließlich 30 s Frische: keine Auslösung, danach CONTROL.
   - Der core hängt in OBSERVE, etwa weil die Phasen nicht bestätigt sind: Es kommt kein Heartbeat, und der Watchdog löst nach 90 s aus. Scharfgeschaltet wird erst wieder, wenn das Gate bis auf den Watchdog offen ist.
   - Tests:
     - Task 9 `test_no_counter_when_not_allowed`
     - Task 11 `test_restart_stuck_in_observe_lets_watchdog_trip` und `test_restart_within_timeout_resumes_control`
2. **Live → Dry-Run oder Regelbetrieb aus:**
   - Erst die Sicherwerte, dann das Rücklesen, erst danach die Freigabe 0. OBSERVE kommt erst nach der Übergabe.
   - Scheitert das Rücklesen, gibt es weder Heartbeat noch Freigabe.
   - Tests:
     - Task 8 `test_handback_completes_only_after_safe_values_confirmed`
     - Task 11 `test_control_off_hands_back_then_observe` und `test_failed_handback_stops_heartbeat`
3. **Ein gesendeter Wert kommt nie zurück** (Cerbo stumm oder EVCS übernimmt nicht):
   - Nach 10 s Timeout und die Entscheidung „timeout“.
   - Höchstens 3 Versuche je Vorfall, nie ein Schreiben im Sekundentakt. Persistente Arten verbrauchen das Budget höchstens dreimal.
   - Tests: Task 3 `test_unconfirmed_write_times_out_once`, Task 8 `test_safe_values_retry_three_times_then_fail`.
4. **Tesla schläft oder Tessie hängt:**
   - Kein Befehl ohne `online`, und der Zyklus blockiert nie.
   - Nach 3 Fehlern ist der Tesla 15 min nicht steuerbar.
   - Tests:
     - Task 3 `test_not_controllable_is_not_sent`
     - Task 7 `test_asleep_is_not_controllable`, `test_command_runs_in_background` und `test_three_failures_make_it_unavailable`
5. **Fremde Änderung gegen den eigenen Schreibvorgang:**
   - Ein offener eigener Wert ist nie fremd.
   - Erst 3 Treffer in Folge sperren, und die Sperre löst sich wieder.
   - Tests: Task 10 `test_pending_write_is_not_foreign` und `test_three_hits_lock_then_release`.

## Texte

**Gründe** (Betriebsart, Betriebszustand, Fähigkeiten, Senden):

| Name | Text |
|---|---|
| `REASON_HANDBACK_MODE` (Betriebsart) | Übergabe: Sicherwerte werden gesetzt. |
| `REASON_HANDBACK` (Betriebszustand) | Regelbetrieb wird beendet: Übergabe der Aktoren läuft. |
| EVCS ohne Instanz | Keine evcharger-Instanz in victron.instances.evcharger. |
| Cerbo getrennt | Cerbo nicht verbunden. |
| EVCS-Modus | EVCS nicht im manuellen Modus. |
| Tessie getrennt | Tessie nicht erreichbar. |
| Tesla schläft | Fahrzeug schläft oder ist offline; es wird nicht geweckt. |
| Tesla unterwegs | Fahrzeug nicht zu Hause. |
| Tesla nicht angesteckt | Fahrzeug nicht angesteckt. |
| Tesla nach Fehlern | Nach 3 Fehlern in 15 min bis {HH:MM} nicht steuerbar. |
| MQTT-Auftrag abgelehnt | Cerbo-Verbindung: Auftrag nicht angenommen. |
| Fähigkeit fehlt (Ausführer) | nicht steuerbar: {Gründe, mit Leerzeichen verbunden} |

`{HH:MM}` ist Wiener Zeit.

**Alarme:**

| ID | Stufe | Text |
|---|---|---|
| `actuator.failed.<aktor>` | Fehler | {label}: Sicherwert für „{art}“ nicht bestätigt. – bei Victron und EVCS mit dem Zusatz „ Der Watchdog am Cerbo übernimmt.“ |
| `actuator.unavailable.<aktor>` | Warnung | {label} ist nach 3 Fehlern in 15 min bis {HH:MM} nicht steuerbar. |
| `competitor.foreign.<aktor>` | Fehler | {label}: {Größe} von außen geändert ({ist} statt {soll}). |
| `watchdog.timeout` | Fehler | Watchdog-Timeout {x} s am Cerbo, erwartet {y} s; config.ini neu erzeugen und mit install.sh übertragen. |
| `safety.clock` | Warnung | Gespeicherte Schreibzeiten liegen bis zu {n} min in der Zukunft; persistente Schreibzugriffe bleiben bis dahin begrenzt. |

- {art} ist der Wert von `CommandKind`, etwa „current“.
- {Größe}: „Strom“ mit Werten in A, „Netz-Sollwert“ mit Werten in W oder „kein Override“.
- {n} ist aufgerundet auf ganze Minuten.

**Entscheidungstext** (`decision_text`, Shared):
`{label}{ (Gründe)}: {Befehl} – {Ergebnis}.`, danach, falls vorhanden, die Befundtexte der Safety-Schicht, mit Leerzeichen verbunden.

- **Gründe:** `safe_state` → „sicherer Zustand“, `handback` → „Übergabe“, `grid_outage` → „Netzausfall“. Unbekannte Codes erscheinen unverändert; mehrere werden mit „, “ verbunden.
- **Befehl:** Er nennt `applied`, sonst `requested`.

  | Art | Befehlstext |
  |---|---|
  | `current` | Strom {v} A |
  | `charging` | Laden an, Laden aus |
  | `grid_setpoint` | Netz-Sollwert {v} W, bei `None` Netz-Sollwert-Override aufheben |
  | `max_discharge` | Entladegrenze {v} W, bei −1 Entladegrenze unbegrenzt |
  | `min_soc` | Min-SOC {v} % |
- **Ergebnis:**

  | Ergebnis | Text |
  |---|---|
  | ok | gesendet und bestätigt |
  | error | Fehler: {Detail} |
  | timeout | nicht bestätigt (Zeitüberschreitung) |
  | not_sent | nicht gesendet: {Detail} |
  | dry_run | Dry-Run, nicht gesendet |
  | rejected | abgelehnt |

Beispiel: `EVCS „evcs“ (sicherer Zustand): Strom 6 A – gesendet und bestätigt. Dringend (Richtung sicherer Zustand): ohne Mindestabstand und Limits.`

## Dateistruktur

```
src/bindaems/shared/timeutil.py                (geändert) AnchoredClock
src/bindaems/shared/decisions.py               Decision, DecisionResult, JSON, decision_text
src/bindaems/core/persistence/files.py         (geändert) fsync des Verzeichnisses
src/bindaems/core/persistence/decision_spool.py  DecisionSpool
src/bindaems/core/safety/types.py, phases.py, limits.py, rate.py, layer.py, budget.py, modes.py, locks.py  (geändert)
src/bindaems/core/control/actuator.py          Actuator, Capabilities, Confirmation, matches()
src/bindaems/core/control/executor.py          CommandExecutor, DecisionSink, WriteResult
src/bindaems/core/control/safe_state.py        SafeStateKeeper, SafeTarget, safe_values_reached()
src/bindaems/core/control/ownership.py         Ownership
src/bindaems/core/control/foreign.py           ForeignChangeDetector, ForeignChange
src/bindaems/core/control/context.py           safety_context(), decision_inputs()
src/bindaems/core/control/operating.py         (geändert) Übergabe, gate_ready_except_watchdog()
src/bindaems/core/watchdog_client/status.py, heartbeat.py   (geändert)
src/bindaems/core/adapters/victron_mqtt.py     (geändert) Schreibpfade, Ende der Sitzung
src/bindaems/core/actuators/__init__.py, evcs.py, victron.py, tesla.py
src/bindaems/core/runtime.py, core/api/app.py  (geändert)
cerbo-watchdog/bindaems_watchdog/__init__.py, service.py, state_file.py   (geändert)
src/bindaems/tools/watchdog_config.py, watchdog_probe.py   (geändert)
src/bindaems/tools/verify_part2.py             Einstieg Teil 2
src/bindaems/tools/part2/__init__.py, session.py, evcs.py, tesla.py, victron.py
tests/unit/shared/…, tests/unit/core/{persistence,safety,control,actuators,watchdog_client,adapters,api}/…,
tests/integration/test_runtime_control.py, tests/unit/cerbo_watchdog/…, tests/unit/tools/part2/…
docs: Spec, Betriebshandbuch, docs/verification/README.md, docs/uebergabe.md
```

Neue Pakete unter `src/bindaems` bekommen eine `__init__.py` (Layout-Test). Testverzeichnisse bekommen keine.

---

### Task 1: Uhr, Dateien und Härtungen aus dem Review

**Files:**
- Modify: `src/bindaems/shared/timeutil.py`, `src/bindaems/core/persistence/files.py`, `src/bindaems/core/safety/{types,phases,limits,rate,layer,budget}.py`
- Test: `tests/unit/shared/test_timeutil.py`, `tests/unit/core/persistence/test_files.py`, `tests/unit/core/safety/{test_types,test_phases,test_limits,test_layer,test_budget}.py`

**Interfaces:**
- Consumes: `RateLimiter`, `SafetyLayer`, `PersistentWriteBudget`, `write_json_atomic`, `read_json` (2a)
- Produces:
  - `class AnchoredClock` in `shared/timeutil.py`, erfüllt `Clock`:
    - `__init__(self, wall: Callable[[], datetime] = lambda: datetime.now(UTC), monotonic: Callable[[], float] = time.monotonic)`
    - Der Konstruktor liest `wall()` und `monotonic()` genau einmal.
    - `now() -> datetime` = Anker + (`monotonic()` − Startwert).
  - `fsync_directory(path: Path) -> None` in `core/persistence/files.py`:
    - `write_json_atomic` ruft es nach `os.replace` für `path.parent` auf.
    - Ein `OSError` daraus wird ignoriert, denn der neue Inhalt steht schon.
  - `SafetyContext.base_grid_setpoint_w: float | None = 0.0`; `None`, NaN und ±∞ gelten als unbekannt (Präzisierung 13).
  - `invalid_value(kind, value)`: `repr(value)` mit höchstens 60 Zeichen; gekürzt sind es die ersten 59 Zeichen und „…“.
  - `RateLimiter`:
    - `hourly_entries() -> list[tuple[str, CommandKind, datetime]]`: nur Aktoren mit Stundenlimits, also Tesla.
    - `restore(entries, now) -> None`: verwirft Einträge mit `now − t ≥ 3600 s`.
  - `SafetyLayer.__init__(self, limits, budget, *, rate_file: Path | None = None, now: datetime | None = None)`:
    - Mit `rate_file` und `now` lädt sie die Einträge beim Start.
    - Nach jedem `record` eines Aktors mit Stundenlimits schreibt sie die Datei neu: `{"version": 1, "entries": [{"actuator": …, "kind": …, "ts": ISO}]}`.
    - Eine unlesbare Datei ergibt einen leeren Start und `log.warning("Datei der Rate-Limits unlesbar", …)`.
  - `PersistentWriteBudget.alarms(now)` liefert zusätzlich `safety.clock` (Text siehe „Texte“), wenn ein Eintrag mehr als 300 s nach `now` liegt.
  - In `core/safety` werden Befehlsarten nur noch mit `==` verglichen. Das betrifft `limits.toward_safe` und jede andere Stelle mit `kind is …`. Betriebsarten bleiben bei `is`: Ein Text „live“ bleibt bewusst im Dry-Run (2a).

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/shared/test_timeutil.py
def test_anchored_clock_ignores_wall_jumps() -> None:
    walls = iter([T0, T0 - timedelta(hours=1)])
    mono = [100.0]
    clock = AnchoredClock(wall=lambda: next(walls), monotonic=lambda: mono[0])
    mono[0] = 105.0
    assert clock.now() == T0 + timedelta(seconds=5)
    assert clock.now() == T0 + timedelta(seconds=5)  # die Wanduhr wird nicht neu gelesen


# tests/unit/core/persistence/test_files.py
def test_write_syncs_the_directory(tmp_path, monkeypatch) -> None:
    synced: list[Path] = []
    monkeypatch.setattr(files, "fsync_directory", synced.append)
    files.write_json_atomic(tmp_path / "a.json", {"x": 1})
    assert synced == [tmp_path]


def test_directory_sync_error_keeps_the_new_file(tmp_path, monkeypatch) -> None:
    def fail(path: Path) -> None:
        raise OSError("kein fsync")

    monkeypatch.setattr(files, "fsync_directory", fail)
    files.write_json_atomic(tmp_path / "a.json", {"x": 1})
    assert files.read_json(tmp_path / "a.json") == {"x": 1}


# tests/unit/core/safety/test_types.py
def test_invalid_value_text_is_truncated() -> None:
    shown = repr("x" * 200)[:59] + "…"
    finding = invalid_value(CommandKind.CURRENT, "x" * 200)
    assert finding.text == f"Ungültiger Wert für „current“: {shown}."


# tests/unit/core/safety/test_phases.py
@pytest.mark.parametrize("base", [None, math.nan, math.inf])
def test_unknown_base_setpoint_allows_only_clearing(limits, base) -> None:
    ctx = make_ctx(base_grid_setpoint_w=base, present={(VICTRON, K.GRID_SETPOINT): None})
    assert check_phases(Command(VICTRON, K.GRID_SETPOINT, -500.0), -500.0, ctx, limits).reject
    assert not check_phases(Command(VICTRON, K.GRID_SETPOINT, None), None, ctx, limits).reject


def test_missing_override_and_unknown_base_reject_numbers(limits) -> None:
    ctx = make_ctx(base_grid_setpoint_w=None, present={})
    assert check_phases(Command(VICTRON, K.GRID_SETPOINT, 0.0), 0.0, ctx, limits).reject


def test_known_override_does_not_need_the_base(limits) -> None:
    ctx = make_ctx(base_grid_setpoint_w=None, present={(VICTRON, K.GRID_SETPOINT): 100.0})
    assert not check_phases(Command(VICTRON, K.GRID_SETPOINT, 50.0), 50.0, ctx, limits).reject


# tests/unit/core/safety/test_limits.py
def test_toward_safe_with_unknown_base_only_clears(limits) -> None:
    ctx = make_ctx(base_grid_setpoint_w=None, present={(VICTRON, K.GRID_SETPOINT): None})
    assert toward_safe(Command(VICTRON, K.GRID_SETPOINT, None, urgent=True), ctx, limits)
    assert not toward_safe(Command(VICTRON, K.GRID_SETPOINT, -1000.0, urgent=True), ctx, limits)


def test_kind_given_as_plain_text_counts_as_that_kind(limits) -> None:
    plain = Command("wallbox.evcs", cast(CommandKind, "current"), 6.0, urgent=True)
    ctx = make_ctx(present={("wallbox.evcs", K.CURRENT): 10.0})
    assert toward_safe(plain, ctx, limits)


# tests/unit/core/safety/test_layer.py
def test_tesla_hour_window_survives_restart(tmp_path, limits_live) -> None:
    rate_file = tmp_path / "rate_limits.json"
    layer = SafetyLayer(limits_live, budget(tmp_path), rate_file=rate_file, now=T0)
    for i in range(6):  # start_stop_per_hour = 6
        moment = T0 + timedelta(seconds=100 * i)
        command = Command("vehicle.tesla", K.CHARGING, i % 2 == 0)
        assert layer.authorize(command, make_ctx(now=moment)).outcome == Outcome.ACCEPTED
    later = T0 + timedelta(seconds=700)
    fresh = SafetyLayer(limits_live, budget(tmp_path), rate_file=rate_file, now=later)
    verdict = fresh.authorize(Command("vehicle.tesla", K.CHARGING, True), make_ctx(now=later))
    assert verdict.outcome == Outcome.DEFERRED
    assert verdict.findings[-1].code == "rate.start_stop"


def test_unreadable_rate_file_starts_empty(tmp_path, limits_live) -> None:
    rate_file = tmp_path / "rate_limits.json"
    rate_file.write_text("{", encoding="utf-8")
    layer = SafetyLayer(limits_live, budget(tmp_path), rate_file=rate_file, now=T0)
    command = Command("vehicle.tesla", K.CHARGING, True)
    assert layer.authorize(command, make_ctx()).outcome == Outcome.ACCEPTED


# tests/unit/core/safety/test_budget.py
def test_future_entries_raise_the_clock_alarm(tmp_path) -> None:
    path = tmp_path / "budget.json"
    write_json_atomic(path, {"writes": [(T0 + timedelta(minutes=10)).isoformat()]})
    alarms = PersistentWriteBudget(12, 100, path, T0).alarms(T0)
    assert [(a.id, a.message) for a in alarms] == [
        (
            "safety.clock",
            "Gespeicherte Schreibzeiten liegen bis zu 10 min in der Zukunft; "
            "persistente Schreibzugriffe bleiben bis dahin begrenzt.",
        )
    ]
```

`budget(tmp_path)` ist der vorhandene Helfer in `test_layer.py` (`PersistentWriteBudget(12, 100, tmp_path / "budget.json", T0)`); `make_ctx` kommt aus `tests.unit.core.safety.conftest`.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/shared/test_timeutil.py tests/unit/core/persistence tests/unit/core/safety -q`
Expected: FAIL (`ImportError: cannot import name 'AnchoredClock'` bzw. die neuen Erwartungen)

- [ ] **Step 3: Umsetzen**
  - **Regel 4 und `toward_safe`:** Sie lesen den Basis-Sollwert über `finite_number(ctx.base_grid_setpoint_w)`.
    - Ist der wirksame Ist-Sollwert bekannt (Override bekannt oder Basis bekannt), gilt die Rechnung aus 2a.
    - Ist er unbekannt, lehnt Regel 4 jeden Zahlenwert ab: Befund `phase.unknown_setpoint`, Text „Wirksamer Netz-Sollwert unbekannt; nur das Aufheben des Overrides ist erlaubt.“. `toward_safe` gilt dann nur für `None`.
  - **`safety.clock`:** {n} ist aufgerundet auf ganze Minuten; `since` ist `now`.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/shared tests/unit/core/persistence tests/unit/core/safety tests/property -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared/timeutil.py src/bindaems/core/persistence/files.py src/bindaems/core/safety tests/unit/shared tests/unit/core/persistence tests/unit/core/safety
git commit -m "feat(safety): monotone Uhr, Rate-Limits über Neustarts, Härtungen aus dem Review 2a"
```

---

### Task 2: Entscheidungen: Typ, Text und Spool

**Files:**
- Create: `src/bindaems/shared/decisions.py`, `src/bindaems/core/persistence/decision_spool.py`
- Test: `tests/unit/shared/test_decisions.py`, `tests/unit/core/persistence/test_decision_spool.py`

**Interfaces:**
- Consumes: `CommandKind`, `CommandValue` (`shared/control.py`), `de()`. Für `shared` ist `de` aus `core.safety.types` tabu: Lege `de` in `shared/numbers.py` ab, und `core.safety.types` importiert es von dort (gleiches Verhalten).
- Produces:
  - `class DecisionResult(StrEnum)`: `OK = "ok"`, `ERROR = "error"`, `TIMEOUT = "timeout"`, `NOT_SENT = "not_sent"`, `DRY_RUN = "dry_run"`, `REJECTED = "rejected"`
  - `@dataclass(frozen=True) class Decision`, Felder wie in Präzisierung 7:
    - `id: str`, `ts: datetime`, `actuator: str`, `kind: CommandKind`
    - `requested: CommandValue`, `applied: CommandValue`, `dry_run: bool`
    - `outcome: str` (Wert von `Outcome`), `result: DecisionResult`
    - `codes: tuple[str, ...]`, `text: str`, `inputs: Mapping[str, float | None]`
  - `decision_to_json(decision: Decision) -> dict[str, Any]` mit `ts` als ISO-Zeit (UTC) und `decision_from_json(data: Any) -> Decision`. Letzteres wirft bei falscher Form einen `ValueError` mit deutschem Text.
  - `decision_text(label: str, kind: CommandKind, value: CommandValue, result: DecisionResult, *, detail: str = "", reasons: Sequence[str] = (), findings: Sequence[str] = ()) -> str`; Format siehe „Texte“.
  - `class DecisionSpool` in `core/persistence/decision_spool.py`:
    - `__init__(self, directory: Path, *, max_bytes: int = 50 * 1024 * 1024, max_age: timedelta = timedelta(days=7))`
    - `append(decision: Decision) -> bool`: eine JSON-Zeile in `decisions-JJJJ-MM-TT.jsonl` (UTC-Datum von `ts`). `False` bei `OSError`, mit `log.error`.
    - `read_since(since: datetime, limit: int) -> tuple[list[Decision], bool]`: Entscheidungen mit `ts > since`, aufsteigend, höchstens `limit`; das Zweite sagt, ob es mehr gibt. Unlesbare Zeilen überspringt sie mit `log.warning`.
    - `prune(now: datetime) -> None`: löscht Dateien, deren Datum mehr als `max_age` zurückliegt, danach die ältesten, bis die Summe ≤ `max_bytes` ist.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/shared/test_decisions.py
def decision(**changes: Any) -> Decision:
    fields: dict[str, Any] = {
        "id": "a1", "ts": T0, "actuator": "wallbox.evcs", "kind": CommandKind.CURRENT,
        "requested": 6.0, "applied": 6.0, "dry_run": False, "outcome": "accepted",
        "result": DecisionResult.OK, "codes": ("safe_state",), "text": "…",
        "inputs": {"soc_pct": 46.0, "grid_l1_a": None},
    }
    return Decision(**(fields | changes))


def test_json_round_trip() -> None:
    d = decision()
    assert decision_from_json(decision_to_json(d)) == d


def test_from_json_rejects_wrong_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        decision_from_json(decision_to_json(decision()) | {"kind": "brake"})


def test_text_for_a_confirmed_safe_current() -> None:
    text = decision_text(
        "EVCS „evcs“", CommandKind.CURRENT, 6.0, DecisionResult.OK,
        reasons=("safe_state",),
        findings=("Dringend (Richtung sicherer Zustand): ohne Mindestabstand und Limits.",),
    )
    assert text == (
        "EVCS „evcs“ (sicherer Zustand): Strom 6 A – gesendet und bestätigt. "
        "Dringend (Richtung sicherer Zustand): ohne Mindestabstand und Limits."
    )


@pytest.mark.parametrize(
    ("kind", "value", "result", "detail", "expected"),
    [
        (CommandKind.CHARGING, False, DecisionResult.DRY_RUN, "", "V: Laden aus – Dry-Run, nicht gesendet."),
        (CommandKind.GRID_SETPOINT, None, DecisionResult.TIMEOUT, "",
         "V: Netz-Sollwert-Override aufheben – nicht bestätigt (Zeitüberschreitung)."),
        (CommandKind.MAX_DISCHARGE, -1.0, DecisionResult.REJECTED, "", "V: Entladegrenze unbegrenzt – abgelehnt."),
        (CommandKind.MIN_SOC, 20.5, DecisionResult.ERROR, "Verbindung weg",
         "V: Min-SOC 20,5 % – Fehler: Verbindung weg."),
        (CommandKind.CURRENT, 16.0, DecisionResult.NOT_SENT, "nicht steuerbar: Cerbo nicht verbunden.",
         "V: Strom 16 A – nicht gesendet: nicht steuerbar: Cerbo nicht verbunden."),
    ],
)
def test_text_variants(kind, value, result, detail, expected) -> None:
    assert decision_text("V", kind, value, result, detail=detail) == expected


# tests/unit/core/persistence/test_decision_spool.py
def test_read_since_across_days_with_limit(tmp_path) -> None:
    spool = DecisionSpool(tmp_path)
    for i, ts in enumerate([T0, T0 + timedelta(hours=1), T0 + timedelta(days=1)]):
        assert spool.append(decision(id=f"d{i}", ts=ts))
    found, more = spool.read_since(T0, limit=1)
    assert [d.id for d in found] == ["d1"] and more is True
    found, more = spool.read_since(T0, limit=10)
    assert [d.id for d in found] == ["d1", "d2"] and more is False


def test_prune_drops_old_days_and_keeps_size(tmp_path) -> None:
    spool = DecisionSpool(tmp_path, max_bytes=10_000)
    spool.append(decision(ts=T0 - timedelta(days=8)))
    spool.append(decision(ts=T0))
    spool.prune(T0)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["decisions-2026-10-08.jsonl"]


def test_unwritable_directory_returns_false(tmp_path) -> None:
    blocker = tmp_path / "datei"
    blocker.write_text("x")
    assert DecisionSpool(blocker).append(decision()) is False


def test_broken_line_is_skipped(tmp_path) -> None:
    spool = DecisionSpool(tmp_path)
    spool.append(decision(id="ok1", ts=T0 + timedelta(seconds=1)))
    with (tmp_path / "decisions-2026-10-08.jsonl").open("a", encoding="utf-8") as f:
        f.write("{kaputt\n")
    assert [d.id for d in spool.read_since(T0, 10)[0]] == ["ok1"]
```

`decision()` steht als Helfer auch in `test_decision_spool.py`, oder beide importieren ihn aus `tests.unit.shared.test_decisions`. Die Tests formatieren Zeilen über 100 Zeichen mit ruff um.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/shared/test_decisions.py tests/unit/core/persistence/test_decision_spool.py -q`
Expected: FAIL (`ModuleNotFoundError: bindaems.shared.decisions`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces. `de` nach `shared/numbers.py` verschieben; `core.safety.types` importiert es von dort, und `tests/unit/core/safety/test_types.py` bleibt grün.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/shared tests/unit/core -q && uv run lint-imports`
Expected: PASS, Importverträge eingehalten

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared src/bindaems/core/persistence src/bindaems/core/safety/types.py tests/unit/shared tests/unit/core/persistence
git commit -m "feat(core): Entscheidungen mit Text und Spool"
```

---

### Task 3: Aktor-Schnittstelle und Ausführer

**Files:**
- Create: `src/bindaems/core/control/actuator.py`, `src/bindaems/core/control/executor.py`
- Test: `tests/unit/core/control/test_executor.py`, `tests/unit/core/control/fakes.py` (`FakeActuator`)

**Interfaces:**
- Consumes: `SafetyLayer`, `Verdict`, `Outcome`, `SafetyContext` (2a); `Decision`, `DecisionResult`, `decision_text` (Task 2); `Snapshot`
- Produces in `core/control/actuator.py`:
  - `class Confirmation(StrEnum)`: `PENDING = "pending"`, `CONFIRMED = "confirmed"`, `FAILED = "failed"`
  - `@dataclass(frozen=True) class Capabilities`: `controllable: bool`, `reasons: tuple[str, ...] = ()`
  - `class Actuator(Protocol)`:
    - Attribute: `id: str`, `confirm_timeout_s: float`
    - `capabilities(snap: Snapshot, now: datetime) -> Capabilities`
    - `present(snap: Snapshot) -> dict[CommandKind, CommandValue]`
    - `send(kind: CommandKind, value: CommandValue) -> str | None`
    - `confirm(kind: CommandKind, value: CommandValue, snap: Snapshot) -> tuple[Confirmation, str]`
  - `matches(kind: CommandKind, wanted: CommandValue, actual: CommandValue) -> bool` mit den Toleranzen aus Präzisierung 6. Werte vom falschen Typ passen nie; `None` passt nur zu `None`.
- Produces in `core/control/executor.py`:
  - `class DecisionSink(Protocol)`: `__call__(self, decision: Decision) -> None`
  - `@dataclass(frozen=True) class WriteResult`: `actuator: str`, `kind: CommandKind`, `value: CommandValue`, `result: DecisionResult` (OK, ERROR, TIMEOUT oder NOT_SENT), `detail: str`
  - `class CommandExecutor`:
    - `__init__(self, layer: SafetyLayer, actuators: Mapping[str, Actuator], sink: DecisionSink, labels: Mapping[str, str], *, new_id: Callable[[], str] = lambda: uuid4().hex)`
    - `execute(command: Command, ctx: SafetyContext, snap: Snapshot, wall_now: datetime, inputs: Mapping[str, float | None]) -> Verdict | None`:
      - `None`, wenn für Aktor und Art ein Schreibvorgang offen ist; die Safety-Schicht wird dann nicht aufgerufen.
      - Sonst genau ein `layer.authorize`.
      - Bei `verdict.send`:
        - nicht steuerbar: `NOT_SENT` mit „nicht steuerbar: {Gründe}“;
        - `send` liefert einen Grund: `NOT_SENT` mit diesem Grund;
        - sonst offen mit der Frist `ctx.now + confirm_timeout_s`.
      - Dry-Run mit `sendable`: Entscheidung `DRY_RUN`. `REJECTED`: Entscheidung `REJECTED`, höchstens alle 60 s je Aktor, Art und gleichen Befund-Codes.
    - `poll(snap: Snapshot, now: datetime, wall_now: datetime) -> list[WriteResult]`: fragt jeden offenen Schreibvorgang mit `confirm` ab; bestätigt = `OK`, gescheitert = `ERROR`, Frist vorbei = `TIMEOUT`. Jeder abgeschlossene Vorgang ergibt genau eine Entscheidung.
    - `pending() -> frozenset[tuple[str, CommandKind]]`
    - `take_failures() -> list[WriteResult]`: alle `ERROR`, `TIMEOUT` und `NOT_SENT` seit dem letzten Aufruf (für Präzisierung 5)
  - **Entscheidungen** (`DecisionSink`):
    - `id` von `new_id()`, `ts = wall_now`, `requested = command.value`, `applied = verdict.value`.
    - `codes = command.reasons + Befund-Codes`.
    - `text = decision_text(labels[aktor], kind, applied oder requested, result, detail=…, reasons=command.reasons, findings=Befundtexte)`.
    - `inputs` werden durchgereicht.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/fakes.py
@dataclass
class FakeActuator:
    """Aktor für Tests: merkt sich Sendungen, Rücklesen über ``echo``."""

    id: str
    confirm_timeout_s: float = 10.0
    controllable: bool = True
    refuse: str | None = None
    echo: dict[CommandKind, CommandValue] = field(default_factory=dict)
    sent: list[tuple[CommandKind, CommandValue]] = field(default_factory=list)

    def capabilities(self, snap: Snapshot, now: datetime) -> Capabilities:
        return Capabilities(self.controllable, () if self.controllable else ("Cerbo nicht verbunden.",))

    def present(self, snap: Snapshot) -> dict[CommandKind, CommandValue]:
        return dict(self.echo)

    def send(self, kind: CommandKind, value: CommandValue) -> str | None:
        if self.refuse is None:
            self.sent.append((kind, value))
        return self.refuse

    def confirm(self, kind, value, snap) -> tuple[Confirmation, str]:
        if kind in self.echo and matches(kind, value, self.echo[kind]):
            return Confirmation.CONFIRMED, ""
        return Confirmation.PENDING, ""


# tests/unit/core/control/test_executor.py
EVCS = "wallbox.evcs"


def setup(tmp_path, limits, **actuator) -> tuple[CommandExecutor, FakeActuator, list[Decision]]:
    fake = FakeActuator(EVCS, **actuator)
    decisions: list[Decision] = []
    layer = SafetyLayer(limits, PersistentWriteBudget(12, 100, tmp_path / "b.json", T0))
    ids = iter(f"d{i}" for i in range(100))
    executor = CommandExecutor(layer, {EVCS: fake}, decisions.append, {EVCS: "EVCS „evcs“"}, new_id=lambda: next(ids))
    return executor, fake, decisions


SAFE = Command(EVCS, CommandKind.CURRENT, 6.0, urgent=True, reasons=("safe_state",))
LIVE_CTX = make_ctx(present={(EVCS, CommandKind.CURRENT): 16.0})


def test_dry_run_verdict_logs_without_sending(tmp_path, limits) -> None:
    executor, fake, decisions = setup(tmp_path, limits)
    ctx = make_ctx(modes={}, present={(EVCS, CommandKind.CURRENT): 16.0})
    executor.execute(SAFE, ctx, snap({}), T0, {})
    assert fake.sent == [] and [d.result for d in decisions] == [DecisionResult.DRY_RUN]


def test_live_write_is_confirmed_and_logged_once(tmp_path, limits_live) -> None:
    executor, fake, decisions = setup(tmp_path, limits_live)
    assert executor.execute(SAFE, LIVE_CTX, snap({}), T0, {}).send
    assert fake.sent == [(CommandKind.CURRENT, 6.0)] and decisions == []
    fake.echo[CommandKind.CURRENT] = 6
    results = executor.poll(snap({}), T0 + timedelta(seconds=2), T0)
    assert [r.result for r in results] == [DecisionResult.OK]
    assert decisions[0].text.startswith("EVCS „evcs“ (sicherer Zustand): Strom 6 A – gesendet und bestätigt.")


def test_unconfirmed_write_times_out_once(tmp_path, limits_live) -> None:
    executor, _, decisions = setup(tmp_path, limits_live)
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    assert executor.poll(snap({}), T0 + timedelta(seconds=9), T0) == []
    assert [r.result for r in executor.poll(snap({}), T0 + timedelta(seconds=10), T0)] == [DecisionResult.TIMEOUT]
    assert executor.poll(snap({}), T0 + timedelta(seconds=20), T0) == []
    assert [d.result for d in decisions] == [DecisionResult.TIMEOUT]
    assert [f.result for f in executor.take_failures()] == [DecisionResult.TIMEOUT]
    assert executor.take_failures() == []


def test_pending_write_is_not_reconsidered(tmp_path, limits_live) -> None:
    executor, fake, _ = setup(tmp_path, limits_live)
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    assert executor.execute(SAFE, LIVE_CTX, snap({}), T0, {}) is None
    assert executor.pending() == {(EVCS, CommandKind.CURRENT)} and len(fake.sent) == 1


def test_not_controllable_is_not_sent(tmp_path, limits_live) -> None:
    executor, fake, decisions = setup(tmp_path, limits_live, controllable=False)
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    assert fake.sent == []
    assert decisions[0].result == DecisionResult.NOT_SENT
    assert "nicht gesendet: nicht steuerbar: Cerbo nicht verbunden." in decisions[0].text


def test_refused_send_is_not_sent(tmp_path, limits_live) -> None:
    executor, _, decisions = setup(tmp_path, limits_live, refuse="Cerbo-Verbindung: Auftrag nicht angenommen.")
    executor.execute(SAFE, LIVE_CTX, snap({}), T0, {})
    assert decisions[0].result == DecisionResult.NOT_SENT and executor.pending() == frozenset()


def test_same_rejection_is_logged_once_a_minute(tmp_path, limits_live) -> None:
    executor, _, decisions = setup(tmp_path, limits_live)
    locked = make_ctx(locks={EVCS: ("EVCS nicht im manuellen Modus.",)})
    for seconds in (0, 30, 61):
        executor.execute(SAFE, replace(locked, now=T0 + timedelta(seconds=seconds)), snap({}), T0, {})
    assert [d.result for d in decisions] == [DecisionResult.REJECTED, DecisionResult.REJECTED]
```

`make_ctx` stammt aus `tests.unit.core.safety.conftest`, `snap` aus `tests.helpers`.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_executor.py -q`
Expected: FAIL (`ModuleNotFoundError: bindaems.core.control.executor`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces. Die Frist hängt an `ctx.now`, die Entscheidungszeit an `wall_now`.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control -q && uv run lint-imports`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control tests/unit/core/control
git commit -m "feat(control): Aktor-Schnittstelle und Ausführer mit Rücklesen und Entscheidungen"
```

---

### Task 4: Victron-Adapter: Schreibpfade und Ende der Sitzung

**Files:**
- Modify: `src/bindaems/core/adapters/victron_mqtt.py`
- Test: `tests/unit/core/adapters/test_victron_mqtt.py`

**Interfaces:**
- Consumes: `VictronConfig`, `VictronInstances` (Konfiguration); `FakeTransport` aus `tests/unit/core/adapters/conftest.py`
- Produces:
  - Konstanten:
    - `SETPOINT_OVERRIDE_PATH = "hub4/0/Overrides/Setpoint"`
    - `MAX_DISCHARGE_PATH = "settings/0/Settings/CGwacs/MaxDischargePower"`
    - `MIN_SOC_PATH = "settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit"`
  - `evcharger_path(instance: int, name: Literal["SetCurrent", "StartStop"]) -> str` → `"evcharger/<instance>/<name>"`
  - `writable_paths(cfg: VictronConfig) -> frozenset[str]`: der Heartbeat, die drei Pfade oben und je Instanz in `cfg.instances.evcharger` beide `evcharger`-Pfade
  - `VictronMqttAdapter.__init__` liest die erlaubten Pfade aus `writable_paths(cfg)`; `WRITABLE_PATHS` entfällt.
  - **Ende der Sitzung:** Endet der Sender- oder Keepalive-Task, endet die Sitzung mit `ConnectionError("Hilfs-Task beendet: …")`, egal ob mit oder ohne Ausnahme. Danach folgt die Wiederverbindung mit Backoff.
- Unverändert: `write` reiht nur ein; ohne laufende Sitzung liefert es `False`.

- [ ] **Step 1: Failing Tests schreiben**

```python
def test_writable_paths_follow_the_config(cfg) -> None:
    paths = writable_paths(cfg.victron)  # Beispielkonfiguration: evcharger {40: evcs}
    assert paths == {
        "bindaems/0/Heartbeat",
        "hub4/0/Overrides/Setpoint",
        "settings/0/Settings/CGwacs/MaxDischargePower",
        "settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit",
        "evcharger/40/SetCurrent",
        "evcharger/40/StartStop",
    }


async def test_write_rejects_other_paths(fake_env) -> None:
    adapter, transports, _ = fake_env()
    with pytest.raises(ValueError, match="Schreibpfad nicht freigegeben"):
        adapter.write("settings/0/Settings/CGwacs/Hub4Mode", 3)


async def test_dead_sender_ends_the_session(fake_env) -> None:
    adapter, _, _ = fake_env(fail_publish_prefix="W/")  # Veröffentlichen von W/… wirft
    session = asyncio.create_task(adapter._session())
    while not adapter.health().connected:
        await asyncio.sleep(0.001)
    assert adapter.write("evcharger/40/SetCurrent", 6) is True  # eingereiht
    with pytest.raises(ConnectionError, match="Hilfs-Task beendet"):
        await asyncio.wait_for(session, 1.0)
    assert adapter.write("evcharger/40/SetCurrent", 6) is False  # ohne Sitzung
```

`FakeTransport` in `tests/unit/core/adapters/conftest.py` bekommt dazu die Option `fail_publish_prefix: str | None = None`: Beginnt das Topic damit, wirft `publish` eine `aiomqtt.MqttError("Senden fehlgeschlagen")`. `fake_env` reicht die Option an jeden Transport weiter.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/adapters/test_victron_mqtt.py -q`
Expected: FAIL (`ImportError: cannot import name 'writable_paths'`)

- [ ] **Step 3: Umsetzen.** In `_session` die Nachrichtenschleife als eigenen Task neben Sender und Keepalive führen und mit `asyncio.wait(..., return_when=FIRST_COMPLETED)` warten. Endet ein Hilfs-Task zuerst, `ConnectionError` werfen. Im `finally` bleibt alles wie bisher: Warteschlange verwerfen, Tasks abbrechen.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/adapters tests/integration -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/adapters/victron_mqtt.py tests/unit/core/adapters
git commit -m "feat(adapters): Schreibpfade für Aktoren aus der Konfiguration, Sitzung endet mit dem Sender"
```

---

### Task 5: EVCS-Aktor über den GX-Dienst

**Files:**
- Create: `src/bindaems/core/actuators/__init__.py`, `src/bindaems/core/actuators/evcs.py`
- Modify: `src/bindaems/core/control/actuator.py`: Das Protokoll bekommt `alarms(now: datetime) -> list[Alarm]`; EVCS und Victron liefern `[]`, `FakeActuator` ebenso.
- Test: `tests/unit/core/actuators/test_evcs.py`

**Interfaces:**
- Consumes:
  - `Actuator`, `Capabilities`, `Confirmation`, `matches` (Task 3)
  - `evcharger_path` (Task 4)
  - `EvcsConfig`, `wallbox_actuator`
  - `evcs_mode` aus `core/checks/values.py`; `core.actuators` darf `core.checks` importieren
- Produces: `class EvcsActuator`
  - `__init__(self, key: str, cfg: EvcsConfig, instance: int | None, send: Callable[[str, float | int | None], bool], connected: Callable[[], bool])`
  - `id = wallbox_actuator(key)`, `confirm_timeout_s = 10.0`
  - **`capabilities(snap, now)`:** Gründe in dieser Reihenfolge, Texte siehe „Texte“:
    1. keine Instanz
    2. `connected()` ist falsch: Cerbo nicht verbunden
    3. `evcs_mode(snap, key)` ist nicht 0, auch unbekannt: nicht im manuellen Modus
  - **`present(snap)`:**
    - `CURRENT` aus `wallbox.<key>.set_current_a`, wenn das Signal `OK` ist und eine Zahl hat, sonst aus `wallbox.<key>.gx_set_current_a`.
    - `CHARGING` aus `wallbox.<key>.start_stop`, sonst aus `gx_start_stop`: 1 = `True`, 0 = `False`, jeder andere Wert fehlt.
  - **`send(kind, value)`:**
    - `CURRENT` → `send(evcharger_path(i, "SetCurrent"), round(value))`.
    - `CHARGING` → `send(evcharger_path(i, "StartStop"), 1 if value else 0)`.
    - Ohne Instanz oder wenn `send` `False` liefert, kommt der Grund aus „Texte“ zurück.
  - **`confirm`:** `CONFIRMED`, wenn `present(snap)[kind]` mit `matches` passt, sonst `PENDING`; die Frist überwacht der Ausführer.

- [ ] **Step 1: Failing Tests schreiben**

```python
def readings(**signals: tuple[Value, Quality]) -> Snapshot:
    """Snapshot mit Qualität je Signal; Punkte im Namen als „__“ schreiben."""
    return Snapshot(T0, {
        name.replace("__", "."): Reading(value, T0, quality, "evcs", SignalKind.STATE)
        for name, (value, quality) in signals.items()
    })


def actuator(cfg, sent: list, *, instance: int | None = 40, connected: bool = True) -> EvcsActuator:
    def send(path: str, value: float | int | None) -> bool:
        sent.append((path, value))
        return True

    return EvcsActuator("evcs", cfg.wallboxes["evcs"], instance, send, lambda: connected)


def test_writes_go_to_the_gx_service(cfg) -> None:
    sent: list = []
    evcs = actuator(cfg, sent)
    assert evcs.send(CommandKind.CURRENT, 6.0) is None
    assert evcs.send(CommandKind.CHARGING, False) is None
    assert sent == [("evcharger/40/SetCurrent", 6), ("evcharger/40/StartStop", 0)]


def test_present_prefers_modbus_and_falls_back_to_gx(cfg) -> None:
    evcs = actuator(cfg, [])
    both = readings(wallbox__evcs__set_current_a=(12, Quality.OK), wallbox__evcs__gx_set_current_a=(10, Quality.OK))
    stale = readings(wallbox__evcs__set_current_a=(12, Quality.STALE), wallbox__evcs__gx_set_current_a=(10, Quality.OK))
    assert evcs.present(both)[CommandKind.CURRENT] == 12
    assert evcs.present(stale)[CommandKind.CURRENT] == 10


def test_capabilities_name_every_reason(cfg) -> None:
    evcs = actuator(cfg, [], instance=None, connected=False)
    caps = evcs.capabilities(readings(wallbox__evcs__gx_mode=(1, Quality.OK)), T0)
    assert caps == Capabilities(False, (
        "Keine evcharger-Instanz in victron.instances.evcharger.",
        "Cerbo nicht verbunden.",
        "EVCS nicht im manuellen Modus.",
    ))


def test_refused_write_names_the_reason(cfg) -> None:
    evcs = EvcsActuator("evcs", cfg.wallboxes["evcs"], 40, lambda p, v: False, lambda: True)
    assert evcs.send(CommandKind.CURRENT, 6.0) == "Cerbo-Verbindung: Auftrag nicht angenommen."


def test_confirm_waits_for_the_register(cfg) -> None:
    evcs = actuator(cfg, [])
    old = readings(wallbox__evcs__set_current_a=(16, Quality.OK))
    new = readings(wallbox__evcs__set_current_a=(6, Quality.OK))
    assert evcs.confirm(CommandKind.CURRENT, 6.0, old)[0] == Confirmation.PENDING
    assert evcs.confirm(CommandKind.CURRENT, 6.0, new)[0] == Confirmation.CONFIRMED
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/actuators/test_evcs.py -q`
Expected: FAIL (`ModuleNotFoundError: bindaems.core.actuators`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/actuators tests/unit/core/control -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/actuators src/bindaems/core/control/actuator.py tests/unit/core/actuators tests/unit/core/control
git commit -m "feat(actuators): EVCS-Aktor über den GX-Dienst evcharger"
```

---

### Task 6: Victron-Aktor

**Files:**
- Create: `src/bindaems/core/actuators/victron.py`
- Test: `tests/unit/core/actuators/test_victron.py`

**Interfaces:**
- Consumes: `SETPOINT_OVERRIDE_PATH`, `MAX_DISCHARGE_PATH`, `MIN_SOC_PATH` (Task 4); `VICTRON`
- Produces: `class VictronActuator`
  - `__init__(self, send: Callable[[str, float | int | None], bool], connected: Callable[[], bool])`
  - `id = VICTRON`, `confirm_timeout_s = 10.0`
  - **`capabilities`:** steuerbar, wenn `connected()`; sonst „Cerbo nicht verbunden.“
  - **`present(snap)`:**
    - `GRID_SETPOINT` aus `ess.override.setpoint`: Hat das Signal die Qualität `OK`, ist der Wert die Zahl. Bei ungültig (`None`) ist er `None`, also kein Override aktiv. Fehlt das Signal oder ist es nicht `OK`, fehlt der Eintrag (unbekannt).
    - `MAX_DISCHARGE` aus `ess.max_discharge_w`, `MIN_SOC` aus `ess.min_soc_pct`, jeweils nur als `OK`-Zahl.
  - **`send`:** `GRID_SETPOINT` → `SETPOINT_OVERRIDE_PATH` mit `float` oder `None`; `MAX_DISCHARGE` → `MAX_DISCHARGE_PATH`; `MIN_SOC` → `MIN_SOC_PATH`. Liefert `send` `False`, kommt der Grund aus „Texte“ zurück.
  - **`confirm`** wie beim EVCS-Aktor.

- [ ] **Step 1: Failing Tests schreiben**

```python
@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        (CommandKind.GRID_SETPOINT, 1500.0, ("hub4/0/Overrides/Setpoint", 1500.0)),
        (CommandKind.GRID_SETPOINT, None, ("hub4/0/Overrides/Setpoint", None)),
        (CommandKind.MAX_DISCHARGE, -1.0, ("settings/0/Settings/CGwacs/MaxDischargePower", -1.0)),
        (CommandKind.MIN_SOC, 20.0, ("settings/0/Settings/CGwacs/BatteryLife/MinimumSocLimit", 20.0)),
    ],
)
def test_paths_and_values(kind, value, expected) -> None:
    sent: list = []
    victron = VictronActuator(lambda p, v: sent.append((p, v)) or True, lambda: True)
    assert victron.send(kind, value) is None and sent == [expected]


def test_no_override_differs_from_unknown() -> None:
    victron = VictronActuator(lambda p, v: True, lambda: True)
    assert victron.present(snap({"ess.override.setpoint": None})) == {CommandKind.GRID_SETPOINT: None}
    assert CommandKind.GRID_SETPOINT not in victron.present(snap({}))


@pytest.mark.parametrize(
    ("kind", "wanted", "actual", "state"),
    [
        (CommandKind.GRID_SETPOINT, 1000.0, 1000.6, Confirmation.CONFIRMED),
        (CommandKind.GRID_SETPOINT, None, None, Confirmation.CONFIRMED),
        (CommandKind.MIN_SOC, 20.0, 20.4, Confirmation.CONFIRMED),
        (CommandKind.MIN_SOC, 20.0, 21.0, Confirmation.PENDING),
    ],
)
def test_confirm_tolerances(kind, wanted, actual, state) -> None:
    signal = {
        CommandKind.GRID_SETPOINT: "ess.override.setpoint",
        CommandKind.MIN_SOC: "ess.min_soc_pct",
    }[kind]
    victron = VictronActuator(lambda p, v: True, lambda: True)
    assert victron.confirm(kind, wanted, snap({signal: actual}))[0] == state
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/actuators/test_victron.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/actuators -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/actuators/victron.py tests/unit/core/actuators/test_victron.py
git commit -m "feat(actuators): Victron-Aktor mit Netz-Sollwert-Override, Entladegrenze und Min-SOC"
```

---

### Task 7: Tesla-Aktor über Tessie

**Files:**
- Create: `src/bindaems/core/actuators/tesla.py`
- Test: `tests/unit/core/actuators/test_tesla.py`

**Interfaces:**
- Consumes: `TESSIE_BASE_URL` aus `core/adapters/tessie.py`; `VehicleConfig`; `vehicle_actuator`; `LOCAL_TZ` aus `shared/timeutil.py`
- Produces:
  - Konstanten: `COMMAND_TIMEOUT_S = 60.0`, `FAILURES_ALLOWED = 3`, `FAILURE_WINDOW = timedelta(minutes=15)`
  - `class TeslaActuator`:
    - `__init__(self, key: str, cfg: VehicleConfig, token: SecretStr, client: httpx.AsyncClient, connected: Callable[[], bool], *, clock: Callable[[], datetime])`
    - `id = vehicle_actuator(key)`, `confirm_timeout_s = 75.0`, Anzeigename `cfg.name`
  - **`capabilities(snap, now)`:** Gründe in dieser Reihenfolge:
    1. `connected()` ist falsch: Tessie nicht erreichbar
    2. `vehicle.<key>.online` ist nicht `True`: schläft
    3. `vehicle.<key>.at_home` ist nicht `True`: nicht zu Hause
    4. `vehicle.<key>.plugged` ist nicht `True`: nicht angesteckt
    5. `now` liegt vor `unavailable_until`: nach Fehlern nicht steuerbar
  - **`present(snap)`:** `CURRENT` aus `vehicle.<key>.charge_amps`; `CHARGING` ist `True` bei `charging_state == "Charging"`, `False` bei jedem anderen Text.
  - **`send(kind, value)`:**
    - Startet `asyncio.get_running_loop().create_task(self._command(kind, value))` und kehrt sofort zurück.
    - Läuft für die Art noch ein Befehl, kommt „Befehl an Tessie läuft noch.“ zurück.
  - **`_command`:**
    - `POST {TESSIE_BASE_URL}/{vin}/command/{name}` mit `params={"wait_for_completion": "true"}`, bei `set_charging_amps` zusätzlich `amps=<int>`.
    - Header `Authorization: Bearer <token>`, Timeout `COMMAND_TIMEOUT_S`.
    - `name` ist `set_charging_amps`, `start_charging` oder `stop_charging`.
    - Ergebnis: 2xx mit `{"result": true}` → bestätigt.
    - Fehler: Status ≠ 2xx → „Tessie antwortet mit HTTP {code}.“; `result` nicht `true` → „Tessie meldet result=false.“; Ausnahme → `{Typ}: {Text}`. Der Token erscheint nie im Text.
    - Jeder Fehler wird mit `clock()` gezählt. Der dritte binnen 15 min setzt `unavailable_until = clock() + 15 min`.
  - **`confirm(kind, value, snap)`:** offen, solange der Task läuft; danach sein Ergebnis.
  - **`alarms(now)`:** `actuator.unavailable.<id>`, solange `now < unavailable_until`; Text aus „Texte“ mit `cfg.name`.

- [ ] **Step 1: Failing Tests schreiben**

```python
VIN = "5YJ3E7EB0MF000000"
URL = f"https://api.tessie.com/{VIN}/command/set_charging_amps"
HOME = {"vehicle.tesla.online": True, "vehicle.tesla.at_home": True, "vehicle.tesla.plugged": True}


def tesla(cfg, client, now: list[datetime]) -> TeslaActuator:
    return TeslaActuator(
        "tesla", cfg.vehicles["tesla"], SecretStr("geheim"), client, lambda: True, clock=lambda: now[0]
    )


async def test_asleep_is_not_controllable(cfg) -> None:
    async with httpx.AsyncClient() as client:
        caps = tesla(cfg, client, [T0]).capabilities(snap(HOME | {"vehicle.tesla.online": False}), T0)
    assert caps == Capabilities(False, ("Fahrzeug schläft oder ist offline; es wird nicht geweckt.",))


async def test_command_runs_in_background(cfg, respx_mock) -> None:
    release = asyncio.Event()

    async def answer(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json={"result": True})

    route = respx_mock.post(URL).mock(side_effect=answer)
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        assert car.send(CommandKind.CURRENT, 10.0) is None  # kehrt sofort zurück
        await asyncio.sleep(0)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({}))[0] == Confirmation.PENDING
        release.set()
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({}))[0] == Confirmation.CONFIRMED
    request = route.calls[0].request
    assert request.url.params["amps"] == "10" and request.url.params["wait_for_completion"] == "true"
    assert request.headers["Authorization"] == "Bearer geheim"


async def test_result_false_fails(cfg, respx_mock) -> None:
    respx_mock.post(URL).respond(json={"result": False})
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        car.send(CommandKind.CURRENT, 10.0)
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == (Confirmation.FAILED, "Tessie meldet result=false.")


async def test_three_failures_make_it_unavailable(cfg, respx_mock) -> None:
    respx_mock.post(URL).respond(500)
    now = [T0]
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, now)
        for minute in range(3):
            now[0] = T0 + timedelta(minutes=minute)
            car.send(CommandKind.CURRENT, 10.0)
            await asyncio.sleep(0.01)
        later = T0 + timedelta(minutes=3)
        caps = car.capabilities(snap(HOME), later)
        assert caps.reasons == ("Nach 3 Fehlern in 15 min bis 12:17 nicht steuerbar.",)
        assert [a.message for a in car.alarms(later)] == [
            "Tesla Model 3 ist nach 3 Fehlern in 15 min bis 12:17 nicht steuerbar."
        ]
        assert car.capabilities(snap(HOME), T0 + timedelta(minutes=18)).controllable


async def test_token_never_in_failure_text(cfg, respx_mock) -> None:
    respx_mock.post(URL).mock(side_effect=httpx.ConnectError("Bearer geheim abgelehnt"))
    async with httpx.AsyncClient() as client:
        car = tesla(cfg, client, [T0])
        car.send(CommandKind.CURRENT, 10.0)
        await asyncio.sleep(0.01)
        assert car.confirm(CommandKind.CURRENT, 10.0, snap({})) == (
            Confirmation.FAILED, "ConnectError: Bearer *** abgelehnt"
        )
```

`_command` ersetzt jedes Vorkommen des Tokens im Ausnahmetext durch `***`. `T0` ist 10:00 UTC, also 12:00 Wiener Sommerzeit. Der letzte Fehler liegt bei 12:02, daher „bis 12:17“.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/actuators/test_tesla.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/actuators -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/actuators/tesla.py tests/unit/core/actuators/test_tesla.py
git commit -m "feat(actuators): Tesla-Aktor über Tessie im Hintergrund, ohne zu wecken"
```

---

### Task 8: Sicherer Zustand und Übergabe

**Files:**
- Create: `src/bindaems/core/control/safe_state.py`, `src/bindaems/core/control/ownership.py`
- Modify: `src/bindaems/core/safety/modes.py`, `src/bindaems/core/control/operating.py`
- Test: `tests/unit/core/control/test_safe_state.py`, `test_ownership.py`, `test_operating.py`; `tests/unit/core/safety/test_modes.py`

**Interfaces:**
- Consumes: `HardLimits.safe_commands`, `matches` (Task 3), `Intent`, `ModeInfo`
- Produces in `core/control/safe_state.py`:
  - `SAFE_RETRY_S: Final = 10.0`, `SAFE_ATTEMPTS: Final = 3`
  - `@dataclass(frozen=True) class SafeTarget`: `actuator: str`, `reason: Literal["safe_state", "handback"]`, `stop_charging: bool = False`
  - `safe_values(limits: HardLimits, target: SafeTarget) -> dict[CommandKind, CommandValue]`: die Werte aus `safe_commands`; mit `stop_charging` bei EVCS und Tesla zusätzlich `CHARGING: False`
  - `safe_values_reached(limits, target, present) -> bool`: jede Art passt per `matches`; ein unbekannter Ist-Wert passt nie
  - `class SafeStateKeeper`:
    - `__init__(self, limits: HardLimits)`
    - `commands(now, targets: Sequence[SafeTarget], present, pending: AbstractSet[tuple[str, CommandKind]], live: AbstractSet[str]) -> list[Command]`:
      - Befehle nach Präzisierung 4, dringend.
      - Gründe: `(target.reason,)`; der Stopp bei Netzausfall `("grid_outage",)`.
      - Ziele, die nicht mehr vorkommen, vergisst er (neuer Vorfall).
    - `failed: frozenset[tuple[str, CommandKind]]` nach dem letzten `commands`: Arten wirksam live geschalteter Ziele mit 3 Versuchen, ohne Treffer, ohne offenen Schreibvorgang und mit dem letzten Versuch ≥ 10 s her
    - `alarms(now, labels: Mapping[str, str], needs_watchdog: AbstractSet[str]) -> list[Alarm]`: je Aktor mit gescheiterten Arten `actuator.failed.<aktor>`. Mehrere Arten nennt der Text als „„a“, „b““; bei Aktoren mit Watchdog kommt der Zusatz aus „Texte“ dazu.
- Produces in `core/control/ownership.py`: `class Ownership`
  - `__init__(self, owned: Iterable[str] = ())`
  - `owned -> frozenset[str]`, `note_sent(actuator)`, `release(actuator)`
  - `drop(actuators: Iterable[str]) -> frozenset[str]`: nimmt sie heraus und liefert die, die owned waren
  - `handing_back(wanted: AbstractSet[str]) -> frozenset[str]` = owned − wanted
- Produces in `core/safety/modes.py`:
  - `REASON_HANDBACK_MODE: Final = "Übergabe: Sicherwerte werden gesetzt."`
  - `live_wanted(intent, limits) -> frozenset[str]`: Regelbetrieb an, Betriebsart live und `live_allowed`
  - `initially_owned(intent, limits) -> frozenset[str]`: Betriebsart live und `live_allowed`, ohne Rücksicht auf den Regelbetrieb
  - `watchdog_required(intent, limits, *, handing_back: AbstractSet[str] = frozenset()) -> bool`: ein Aktor mit `needs_watchdog` und `live_allowed`, der in `live_wanted` oder in `handing_back` steht
  - `effective_modes(intent, limits, watchdog_armed, *, handing_back: AbstractSet[str] = frozenset())`:
    - Ein Aktor in `handing_back` behält die angeforderte Betriebsart.
    - Wirksam ist er `LIVE` mit dem Grund `REASON_HANDBACK_MODE`, wenn `_live_denial` nichts einwendet; sonst `DRY_RUN` mit dem Einwand.
- Produces in `core/control/operating.py`:
  - `REASON_HANDBACK: Final = "Regelbetrieb wird beendet: Übergabe der Aktoren läuft."`
  - `OperatingInputs.handback_pending: bool = False`
    - Regelbetrieb aus in CONTROL oder DEGRADED mit `handback_pending`: DEGRADED mit `(REASON_HANDBACK,)`.
    - Regelbetrieb aus in SAFE mit `handback_pending`: SAFE mit `(REASON_HANDBACK,)`.
  - `gate_ready_except_watchdog(inputs: OperatingInputs) -> bool`: Regelbetrieb, keine `safe_reasons`, frisch und Phasen bestätigt

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_safe_state.py
EVCS = "wallbox.evcs"
CUR = (EVCS, CommandKind.CURRENT)


def test_safe_current_for_a_live_target(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    cmds = keeper.commands(T0, [SafeTarget(EVCS, "safe_state")], {CUR: 16.0}, frozenset(), {EVCS})
    assert cmds == [Command(EVCS, CommandKind.CURRENT, 6.0, urgent=True, reasons=("safe_state",))]


def test_matching_value_needs_no_command(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    assert keeper.commands(T0, [SafeTarget(EVCS, "safe_state")], {CUR: 6}, frozenset(), {EVCS}) == []


def test_dry_run_target_once_per_incident(limits) -> None:
    keeper, target = SafeStateKeeper(limits), [SafeTarget(EVCS, "safe_state")]
    assert len(keeper.commands(T0, target, {CUR: 16.0}, frozenset(), set())) == 1
    assert keeper.commands(T0 + timedelta(seconds=30), target, {CUR: 16.0}, frozenset(), set()) == []
    keeper.commands(T0 + timedelta(seconds=31), [], {}, frozenset(), set())  # Vorfall endet
    assert len(keeper.commands(T0 + timedelta(seconds=32), target, {CUR: 16.0}, frozenset(), set())) == 1


def test_safe_values_retry_three_times_then_fail(limits_live) -> None:
    keeper, target = SafeStateKeeper(limits_live), [SafeTarget(EVCS, "safe_state")]
    sent_at = [
        s for s in (0, 5, 10, 20, 30, 40, 60)
        if keeper.commands(T0 + timedelta(seconds=s), target, {CUR: 16.0}, frozenset(), {EVCS})
    ]
    assert sent_at == [0, 10, 20]
    assert keeper.failed == {CUR}
    alarms = keeper.alarms(T0, {EVCS: "EVCS „evcs“"}, {EVCS})
    assert [a.message for a in alarms] == [
        "EVCS „evcs“: Sicherwert für „current“ nicht bestätigt. Der Watchdog am Cerbo übernimmt."
    ]


def test_pending_write_waits(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    assert keeper.commands(T0, [SafeTarget(EVCS, "safe_state")], {CUR: 16.0}, {CUR}, {EVCS}) == []


def test_grid_outage_adds_stop(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    present = {CUR: 16.0, (EVCS, CommandKind.CHARGING): True}
    cmds = keeper.commands(T0, [SafeTarget(EVCS, "safe_state", stop_charging=True)], present, frozenset(), {EVCS})
    assert [(c.kind, c.value, c.reasons) for c in cmds] == [
        (CommandKind.CURRENT, 6.0, ("safe_state",)),
        (CommandKind.CHARGING, False, ("grid_outage",)),
    ]


def test_victron_safe_values(limits_live) -> None:
    keeper = SafeStateKeeper(limits_live)
    cmds = keeper.commands(T0, [SafeTarget("victron", "handback")], {}, frozenset(), {"victron"})
    assert [(c.kind, c.value) for c in cmds] == [
        (CommandKind.GRID_SETPOINT, None), (CommandKind.MAX_DISCHARGE, -1.0), (CommandKind.MIN_SOC, 20.0)
    ]


# tests/unit/core/control/test_ownership.py
def test_handback_completes_only_after_safe_values_confirmed(limits_live) -> None:
    owners = Ownership({EVCS})
    target = SafeTarget(EVCS, "handback")
    assert owners.handing_back(frozenset()) == {EVCS}
    assert not safe_values_reached(limits_live, target, {CUR: 16.0})
    assert safe_values_reached(limits_live, target, {CUR: 6})
    owners.release(EVCS)
    assert owners.handing_back(frozenset()) == frozenset()


def test_drop_returns_only_owned() -> None:
    owners = Ownership({EVCS})
    assert owners.drop({EVCS, "victron"}) == {EVCS} and owners.owned == frozenset()


# tests/unit/core/safety/test_modes.py
def test_initially_owned_ignores_control(limits_live) -> None:
    intent = Intent(version=1, control=False, actuators={EVCS: ActuatorMode.LIVE})
    assert initially_owned(intent, limits_live) == {EVCS}
    assert live_wanted(intent, limits_live) == frozenset()


def test_required_needs_control_or_handback(limits_live) -> None:
    off = Intent(version=1, control=False, actuators={EVCS: ActuatorMode.LIVE})
    assert not watchdog_required(off, limits_live)
    assert watchdog_required(off, limits_live, handing_back={EVCS})
    assert watchdog_required(off.model_copy(update={"control": True}), limits_live)


def test_handback_actor_is_live_with_reason(limits_live) -> None:
    modes = effective_modes(Intent(version=1), limits_live, True, handing_back={EVCS})
    assert modes[EVCS] == ModeInfo(ActuatorMode.DRY_RUN, ActuatorMode.LIVE, ("Übergabe: Sicherwerte werden gesetzt.",))
    unarmed = effective_modes(Intent(version=1), limits_live, False, handing_back={EVCS})
    assert unarmed[EVCS].effective == ActuatorMode.DRY_RUN


# tests/unit/core/control/test_operating.py
def test_control_off_waits_for_handback() -> None:
    machine = running_machine()  # vorhandener Helfer: Automat in CONTROL
    machine.step(T0, inputs(control_requested=False, handback_pending=True))
    assert (machine.state, machine.reasons) == (
        OperatingState.DEGRADED, ("Regelbetrieb wird beendet: Übergabe der Aktoren läuft.",)
    )
    machine.step(T0, inputs(control_requested=False))
    assert machine.state == OperatingState.OBSERVE


def test_gate_ready_except_watchdog() -> None:
    assert gate_ready_except_watchdog(inputs(watchdog_required=True, watchdog_armed=False))
    assert not gate_ready_except_watchdog(inputs(data_fresh=False))
```

`running_machine()` und `inputs(**changes)` gibt es in `test_operating.py` noch nicht unter diesem Namen. Lege sie dort an: `inputs` erzeugt `OperatingInputs` mit allen Bedingungen erfüllt; `running_machine` fährt einen Automaten mit zwei Schritten nach CONTROL. `limits_live` hat `battery.reserve_soc_pct` = 20 aus der Beispielkonfiguration.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control tests/unit/core/safety/test_modes.py -q`
Expected: FAIL (`ModuleNotFoundError: bindaems.core.control.safe_state`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces und den Präzisierungen 3 und 4.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core tests/integration -q`
Expected: PASS. Die 2a-Tests zu `watchdog_required` mit Regelbetrieb aus ändern ihre Erwartung; passe sie an Präzisierung 2 an und nenne jede Änderung im Bericht.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control src/bindaems/core/safety/modes.py tests/unit/core/control tests/unit/core/safety/test_modes.py
git commit -m "feat(control): sicherer Zustand mit Wiederholungen und Übergabe am Ende des Live-Betriebs"
```

---

### Task 9: Watchdog-Client: Echo, Timeout und Heartbeat-Regel

**Files:**
- Modify: `src/bindaems/core/watchdog_client/status.py`, `src/bindaems/core/watchdog_client/heartbeat.py`
- Test: `tests/unit/core/watchdog_client/test_status.py`, `test_heartbeat.py`

**Interfaces:**
- Consumes: Signale `watchdog.heartbeat`, `watchdog.timeout_s` (2a)
- Produces in `status.py`:
  - `WatchdogStatus.heartbeat: int | None = None` und `WatchdogStatus.timeout_s: float | None = None`; `read_watchdog` füllt beide.
  - `armed_for_core(status: WatchdogStatus, recent: Collection[int], timeout_s: float) -> bool` nach Präzisierung 2.
  - `watchdog_alarms(status, required, now, *, expected_timeout_s: float | None = None)`: zusätzlich `watchdog.timeout`, wenn beide Timeouts bekannt sind und abweichen, unabhängig von `required`.
- Produces in `heartbeat.py`:
  - Konstanten: `RECENT_KEPT: Final = 3`, `RELEASE_RESEND_FACTOR: Final = 2.0`, `TRIPPED_RELEASE_S: Final = 60.0`
  - `HeartbeatClient.recent -> tuple[int, ...]`: die zuletzt gesendeten Zähler, neueste zuletzt
  - `HeartbeatClient.on_cycle(now, *, required: bool, allowed: bool = True, release_allowed: bool = True, watchdog: WatchdogState = WatchdogState.UNKNOWN)`:
    - Mit `required`: Den fälligen Zähler gibt es nur bei `allowed`. Es gibt nie eine Freigabe.
    - Ohne `required`:
      - Status „active“ mit `release_allowed`: 0 senden, dann „released“.
      - „released“ und `watchdog` ist ARMED seit ≥ `RELEASE_RESEND_FACTOR × interval` nach der letzten 0: 0 erneut senden.
      - `watchdog` ist TRIPPED, `release_allowed` gilt, und der Zustand hält seit ≥ `TRIPPED_RELEASE_S` (gezählt ab dem ersten Zyklus, der ihn so sieht): 0 senden, höchstens einmal je `TRIPPED_RELEASE_S`.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/watchdog_client/test_status.py
def status(**changes: Any) -> WatchdogStatus:
    fields = {"state": WatchdogState.ARMED, "trip_count": 0, "last_trip": None,
              "last_trip_failures": 0, "version": "1.1.0", "heartbeat": 7, "timeout_s": 90.0}
    return WatchdogStatus(**(fields | changes))


def test_armed_for_core_needs_echo_and_timeout() -> None:
    assert armed_for_core(status(), (5, 6, 7), 90.0)
    assert not armed_for_core(status(heartbeat=1234), (5, 6, 7), 90.0)  # Zähler eines früheren Laufs
    assert not armed_for_core(status(timeout_s=120.0), (5, 6, 7), 90.0)
    assert not armed_for_core(status(state=WatchdogState.TRIPPED), (5, 6, 7), 90.0)


def test_timeout_mismatch_alarm() -> None:
    alarms = watchdog_alarms(status(timeout_s=120.0), False, T0, expected_timeout_s=90.0)
    assert [(a.id, a.message) for a in alarms] == [(
        "watchdog.timeout",
        "Watchdog-Timeout 120 s am Cerbo, erwartet 90 s; config.ini neu erzeugen und mit install.sh übertragen.",
    )]


# tests/unit/core/watchdog_client/test_heartbeat.py
def test_no_counter_when_not_allowed() -> None:
    sent, client = recording(interval_s=10.0)  # vorhandener Helfer oder neu
    client.on_cycle(T0, required=True, allowed=False)
    assert sent == [] and client.status == "off"


def test_release_needs_release_allowed() -> None:
    sent, client = recording(interval_s=10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(T0 + timedelta(seconds=1), required=False, release_allowed=False)
    assert sent == [1]
    client.on_cycle(T0 + timedelta(seconds=2), required=False, release_allowed=True)
    assert sent == [1, 0]


def test_lost_release_is_sent_again() -> None:
    sent, client = recording(interval_s=10.0)
    client.on_cycle(T0, required=True)
    client.on_cycle(T0 + timedelta(seconds=1), required=False)
    for s in (5, 20, 21, 41):
        client.on_cycle(T0 + timedelta(seconds=s), required=False, watchdog=WatchdogState.ARMED)
    assert sent == [1, 0, 0, 0]  # bei 21 s und bei 41 s


def test_tripped_without_need_is_released_after_60_s() -> None:
    sent, client = recording(interval_s=10.0)
    for s in (0, 59, 60):
        client.on_cycle(T0 + timedelta(seconds=s), required=False, watchdog=WatchdogState.TRIPPED)
    assert sent == [0]


def test_recent_keeps_last_three() -> None:
    sent, client = recording(interval_s=10.0)
    for s in range(0, 50, 10):
        client.on_cycle(T0 + timedelta(seconds=s), required=True)
    assert client.recent == (3, 4, 5)
```

`recording(interval_s)` liefert die Liste der gesendeten Werte und einen `HeartbeatClient`, dessen `send` in diese Liste schreibt und `True` liefert. Gibt es so einen Helfer in `test_heartbeat.py` noch nicht, lege ihn dort an.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/watchdog_client -q`
Expected: FAIL (`ImportError: cannot import name 'armed_for_core'`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces. Die bestehenden Tests aus 2a bleiben grün, denn die neuen Parameter haben Defaults mit dem alten Verhalten.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/watchdog_client tests/integration -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/watchdog_client tests/unit/core/watchdog_client
git commit -m "feat(watchdog): scharf nur mit eigenem Heartbeat-Echo, Freigabe wiederholen und nach Auslösung aufräumen"
```

---

### Task 10: Fremde Änderungen

**Files:**
- Create: `src/bindaems/core/control/foreign.py`
- Modify: `src/bindaems/core/safety/locks.py`
- Test: `tests/unit/core/control/test_foreign.py`, `tests/unit/core/safety/test_locks.py`

**Interfaces:**
- Consumes: `matches` (Task 3), `LockManager`, `lock_causes` (2a), `de`
- Produces in `foreign.py`:
  - Konstanten: `FOREIGN_HITS: Final = 3`, `WATCHED_KINDS: Final = frozenset({CommandKind.CURRENT, CommandKind.GRID_SETPOINT})`
  - `@dataclass(frozen=True) class ForeignChange`: `actuator: str`, `kind: CommandKind`, `expected: CommandValue`, `actual: CommandValue`, `at: datetime`
  - `class ForeignChangeDetector`:
    - `__init__(self, hold_s: float, hits: int = FOREIGN_HITS)`
    - `expect(actuator, kind, value) -> None`: nur für Arten aus `WATCHED_KINDS`; setzt den Zähler zurück
    - `forget(actuator) -> None`
    - `check(present, pending, now) -> list[ForeignChange]`: nur Paare mit Erwartung und ohne offenen Schreibvorgang. Ein unbekannter Ist-Wert (Eintrag fehlt) zählt weder als Treffer noch als Übereinstimmung. Nach `hits` Treffern in Folge: Erkennung; der Ist-Wert wird die neue Erwartung.
    - `alarms(now, labels: Mapping[str, str]) -> list[Alarm]`: `competitor.foreign.<aktor>` mit der Stufe Fehler. Text aus „Texte“, `since` = letzte Erkennung. Aktiv, solange `now − since < hold_s`.
- Produces in `locks.py`: Der Präfix `competitor.foreign.` sperrt den Aktor, dessen ID der Rest der Alarm-ID ist; er muss in `limits.actuators` stehen.

- [ ] **Step 1: Failing Tests schreiben**

```python
EVCS = "wallbox.evcs"
CUR = (EVCS, CommandKind.CURRENT)
LABELS = {EVCS: "EVCS „evcs“", "victron": "Victron (ESS)"}


def checks(detector: ForeignChangeDetector, values: Sequence[CommandValue], pending=frozenset()) -> list[ForeignChange]:
    found: list[ForeignChange] = []
    for i, value in enumerate(values):
        found += detector.check({CUR: value}, pending, T0 + timedelta(seconds=10 * i))
    return found


def test_pending_write_is_not_foreign() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    assert checks(detector, [10, 10, 10, 10], pending={CUR}) == []


def test_three_hits_in_a_row_are_needed() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    assert checks(detector, [10, 6, 10, 10]) == []
    assert len(checks(detector, [10])) == 1


def test_new_value_becomes_expected() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    assert len(checks(detector, [10, 10, 10, 10, 10, 10])) == 1


def test_three_hits_lock_then_release(limits) -> None:
    detector, locks = ForeignChangeDetector(60.0), LockManager(60.0)
    detector.expect(EVCS, CommandKind.CURRENT, 6.0)
    checks(detector, [10, 10, 10])  # Erkennung bei T0 + 20 s

    def locked_at(seconds: int) -> bool:
        now = T0 + timedelta(seconds=seconds)
        locks.update(now, lock_causes(detector.alarms(now, LABELS), [], limits))
        return EVCS in locks.active(now)

    assert locked_at(20) and locked_at(79)  # Ursache besteht bis unter 80 s
    assert locked_at(80) and locked_at(139)  # Freigabewartezeit ab 80 s
    assert not locked_at(140)


def test_alarm_text_for_the_setpoint_override() -> None:
    detector = ForeignChangeDetector(60.0)
    detector.expect("victron", CommandKind.GRID_SETPOINT, None)
    for i in range(3):
        detector.check({("victron", CommandKind.GRID_SETPOINT): 500.0}, frozenset(), T0 + timedelta(seconds=10 * i))
    assert [a.message for a in detector.alarms(T0 + timedelta(seconds=20), LABELS)] == [
        "Victron (ESS): Netz-Sollwert von außen geändert (500 W statt kein Override)."
    ]


def test_unexpected_pairs_are_not_watched() -> None:
    assert checks(ForeignChangeDetector(60.0), [10, 10, 10, 10]) == []
```

Die Ursache besteht von 20 s bis unter 80 s (`hold_s` = 60). Die Freigabewartezeit beginnt bei der ersten Prüfung ohne Ursache (80 s); frei wird die Sperre, sobald `now − cleared_at` 60 s erreicht, also bei 140 s.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_foreign.py tests/unit/core/safety/test_locks.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control/foreign.py src/bindaems/core/safety/locks.py tests/unit/core/control/test_foreign.py tests/unit/core/safety/test_locks.py
git commit -m "feat(control): fremde Änderungen an Strom und Override erkennen und sperren"
```

---

### Task 11: Laufzeit I: Aktoren, sicherer Zustand und Entscheidungslog

**Files:**
- Create: `src/bindaems/core/control/context.py`, `tests/integration/plant.py`
- Modify:
  - `src/bindaems/core/runtime.py`, `src/bindaems/core/api/app.py` (Felder der Aktoren in `HealthReport`)
  - `tests/integration/conftest.py`: `runtime_env` übergibt `safety_clock=clock` und nimmt `before_cycle: Callable[[], None] | None`
  - `ui/src/lib/api/contract/health.json` (regeneriert)
- Test: `tests/unit/core/control/test_context.py`, `tests/integration/test_runtime_control.py`

**Interfaces:**
- Consumes: die Tasks 1–8 und 10; `Derived.phase_import_a`, `Derived.wallbox_phase_a` (1a); `ISLAND_SOURCE` (Laufzeit)
- Produces in `core/control/context.py`:
  - `safety_context(*, now, operating, modes, locks, inputs, snap, phase_import_a, wallbox_phase_a, present) -> SafetyContext`. Belegung:
    - `vehicle_wallbox = {}`: Die Zuordnung kommt mit 2c, bis dahin unbekannt und damit vorsichtig.
    - `battery_discharge_w = max(−battery.power_w, 0)`.
    - `soc_pct = battery.soc_pct`.
    - `base_grid_setpoint_w = ess.grid_setpoint_w`, wenn endlich, sonst `None`.
    - `grid_budget_w = None`.
    - `context.py` importiert `core.state` nicht, sondern bekommt die beiden Mappings.
  - `decision_inputs(snap, phase_import_a) -> dict[str, float | None]` mit den Schlüsseln `soc_pct`, `battery_w`, `grid_l1_a`, `grid_l2_a`, `grid_l3_a`
- Produces in `CoreRuntime`:
  - `__init__(…, safety_clock: Clock | None = None, mqtt_write: Callable[[str, float | int | None], bool] | None = None)`:
    - `safety_clock` ist standardmäßig `AnchoredClock()`.
    - `mqtt_write` ist standardmäßig `write` des Victron-Adapters, ohne ihn `False`. Ohne `watchdog_send` geht auch der Heartbeat darüber.
  - `self.actuators: dict[str, Actuator]` in der Katalogreihenfolge:
    - Victron → `VictronActuator`.
    - Jede EVCS → `EvcsActuator`, mit der Instanz aus `victron.instances.evcharger`, gesucht über den Schlüssel.
    - Jedes Fahrzeug mit `tessie` → `TeslaActuator` mit `clock=safety_clock.now`. Ohne Tessie-Token meldet `connected` immer `False`.
  - `self.layer`: `SafetyLayer` mit `write_budget.json` und `rate_file=rate_limits.json` unter `core.data_dir`.
  - `self.decision_spool`: `DecisionSpool(core.data_dir / "decisions")`; `prune` beim Start und danach stündlich.
  - `self.executor`: `CommandExecutor`. Seine Senke schreibt jede Entscheidung in den Spool und veröffentlicht `{"type": "decision", "data": decision_to_json(d)}`.
  - `self.keeper`: `SafeStateKeeper`.
  - `decisions(since: datetime, limit: int) -> tuple[list[Decision], bool]`: liest aus dem Spool (für Task 13).
- **Zyklus in diesem Task:** Reihenfolge nach `_update_operation`, ohne Übergabe und neue Heartbeat-Regel (die folgen in Task 12):
  1. `present` aus allen Aktoren; `executor.poll(snap, snow, now)`.
  2. In CONTROL, DEGRADED und SAFE: `ctx = safety_context(...)`.
     - Ziele: In SAFE jeder nicht gesperrte Aktor mit `SafeTarget(a, "safe_state", stop_charging=Inselbetrieb)`.
     - `live` = wirksam live.
     - Jeder Befehl aus `keeper.commands(...)` geht an `executor.execute(...)`.
  3. In START und OBSERVE ruft der Zyklus `keeper.commands(snow, [], …)` auf; das beendet die Vorfälle.
  4. Alarme: dazu kommen `keeper.alarms` und `alarms(now)` jedes Aktors.
- **Gesundheit, je Aktor zusätzlich:** `"controllable": bool`, `"capability_reasons": [...]`, `"pending": [Art, …]`; `"owned"` folgt in Task 12.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_context.py
def test_context_reads_battery_and_setpoint() -> None:
    s = snap({"battery.power_w": -1500.0, "battery.soc_pct": 46.0, "ess.grid_setpoint_w": 50.0})
    ctx = build(s)  # Helfer: safety_context mit leeren Sperren, CONTROL, phase_import_a 1–3 A
    assert (ctx.battery_discharge_w, ctx.soc_pct, ctx.base_grid_setpoint_w) == (1500.0, 46.0, 50.0)
    assert ctx.vehicle_wallbox == {} and ctx.grid_budget_w is None


def test_missing_setpoint_is_unknown() -> None:
    assert build(snap({})).base_grid_setpoint_w is None


def test_decision_inputs() -> None:
    s = snap({"battery.power_w": -1500.0, "battery.soc_pct": 46.0})
    assert decision_inputs(s, {"L1": 1.5, "L2": None, "L3": -0.5}) == {
        "soc_pct": 46.0, "battery_w": -1500.0, "grid_l1_a": 1.5, "grid_l2_a": None, "grid_l3_a": -0.5
    }


# tests/integration/test_runtime_control.py
async def test_island_sends_safe_values_to_live_evcs(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16, "wallbox.evcs.gx_start_stop": 1}
    rt, _, clock = runtime_env(
        state=state, configure=allow_evcs_live, mqtt_write=plant.write, before_cycle=plant.before_cycle
    )
    plant.attach(rt.store)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 33)
    assert rt.health().mode == "CONTROL"
    state |= {"system.active_in_source": 240}  # Inselbetrieb
    run(rt, clock, 3)
    assert ("evcharger/40/SetCurrent", 6) in plant.writes and ("evcharger/40/StartStop", 0) in plant.writes
    evcs = [d for d in rt.decisions(T0, 100)[0] if d.actuator == "wallbox.evcs"]
    assert {(d.kind, d.result) for d in evcs} == {
        (CommandKind.CURRENT, DecisionResult.OK), (CommandKind.CHARGING, DecisionResult.OK)
    }


async def test_dry_run_safe_state_is_logged_once(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, _, clock = runtime_env(state=state, mqtt_write=plant.write, before_cycle=plant.before_cycle)
    plant.attach(rt.store)
    rt.submit_intent(Intent(version=1, control=True))  # alles im Dry-Run
    run(rt, clock, 33)
    state |= {"system.active_in_source": 240}
    run(rt, clock, 30)
    evcs = [d for d in rt.decisions(T0, 100)[0] if d.actuator == "wallbox.evcs"]
    assert [(d.kind, d.result) for d in evcs] == [
        (CommandKind.CURRENT, DecisionResult.DRY_RUN), (CommandKind.CHARGING, DecisionResult.DRY_RUN)
    ]
    assert not any(path.startswith("evcharger/") for path, _ in plant.writes)


async def test_decisions_are_streamed(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16, "wallbox.evcs.gx_start_stop": 1}
    rt, _, clock = runtime_env(
        state=state, configure=allow_evcs_live, mqtt_write=plant.write, before_cycle=plant.before_cycle
    )
    plant.attach(rt.store)
    messages = record_broadcast(rt)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 33)
    state |= {"system.active_in_source": 240}
    run(rt, clock, 3)
    evcs = [m["data"] for m in messages if m["type"] == "decision" and m["data"]["actuator"] == "wallbox.evcs"]
    assert [d["result"] for d in evcs] == ["ok", "ok"]


async def test_health_lists_capabilities(runtime_env) -> None:
    rt, _, clock = runtime_env(state=GOOD_STATE)
    run(rt, clock, 1)
    evcs = next(a for a in rt.health().actuators if a["id"] == "wallbox.evcs")
    assert (evcs["controllable"], evcs["pending"]) == (True, [])
```

`T0` ist der Start von `runtime_env` (`2026-10-08T10:00:00+00:00`). Bis CONTROL dauert es 33 Zyklen, denn das Gate verlangt 30 s frische Messwerte. Das Heartbeat-Echo zählt erst ab Task 12; in diesem Task gilt der Watchdog wie in 2a als scharf, sobald `Plant` den Zustand 1 meldet.

`tests/integration/plant.py`:

```python
class Plant:
    """Simulierte Anlage für die Laufzeit-Tests (Plan 2b).

    ``write`` ist der MQTT-Schreibweg des core. Vor jedem Zyklus (``before_cycle``) meldet sie den
    Watchdog (``watchdog_state``, Echo des zuletzt geschriebenen Heartbeats, Timeout 90 s,
    Version 1.1.0) und übernimmt geschriebene EVCS-Werte als GX-Werte, solange ``follow`` gilt.
    """

    def __init__(self, *, watchdog_state: int = 0, echo: int | None = None, follow: bool = True) -> None: ...
    def attach(self, store: StateStore) -> None: ...
    def write(self, path: str, value: float | int | None) -> bool: ...
    def before_cycle(self) -> None: ...
    def heartbeats(self) -> list[float | int | None]: ...
```

Die Signale schreibt sie mit der Quelle `victron`:
- `watchdog.state`, `watchdog.heartbeat`, `watchdog.timeout_s`, `watchdog.version`
- `wallbox.evcs.gx_set_current_a` (von `evcharger/40/SetCurrent`) und `wallbox.evcs.gx_start_stop` (von `evcharger/40/StartStop`)

`runtime_env` ruft `before_cycle` nach den Werten aus `VALUES` und `state` auf: Was `Plant` übernimmt, gewinnt also gegen den Startwert in `state`.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_context.py tests/integration/test_runtime_control.py -q`
Expected: FAIL (`ModuleNotFoundError: bindaems.core.control.context`, unbekannte Parameter)

- [ ] **Step 3: Umsetzen** wie in den Interfaces. Danach die Vertragsdatei der UI erneuern:
  - `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py -q`
  - `HealthSchema` der UI ignoriert die neuen Felder (valibot entfernt unbekannte Schlüssel).

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core tests/integration -q && (cd ui && pnpm test)`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core tests/unit/core/control/test_context.py tests/integration ui/src/lib/api/contract/health.json
git commit -m "feat(core): Aktoren in der Laufzeit, sicherer Zustand und Entscheidungslog"
```

---

### Task 12: Laufzeit II: Übergabe, Heartbeat-Regel und fremde Änderungen

**Files:**
- Modify: `src/bindaems/core/runtime.py`, `tests/integration/test_runtime_control.py`
- Test: `tests/integration/test_runtime_control.py`

**Interfaces:**
- Consumes: `Ownership`, `live_wanted`, `initially_owned`, `watchdog_required`, `effective_modes` mit `handing_back`, `OperatingInputs.handback_pending`, `gate_ready_except_watchdog` (Task 8); `armed_for_core`, `HeartbeatClient.on_cycle` mit den neuen Parametern (Task 9); `ForeignChangeDetector` (Task 10)
- Produces, der Zyklus vollständig:
  1. `now = clock.now()`, `snow = safety_clock.now()`; Telemetrie, Abrechnung und Plausibilität wie bisher.
  2. `present` aus allen Aktoren. Jeder `WriteResult` mit `OK` aus `executor.poll` geht als Erwartung an `foreign.expect`.
  3. In Prüfzyklen (alle 10 s): mitregelnde Systeme, Selbstprüfung und `foreign.check(present, executor.pending(), snow)`.
  4. **`_update_operation`:**
     - Sperren aus `lock_causes(Alarme mitregelnder Systeme + foreign.alarms(snow, labels), Selbstprüfung, limits)`.
     - `ownership.drop(gesperrte Aktoren)`; jeder dabei verlorene Aktor auch `foreign.forget`.
     - `armed = armed_for_core(status, heartbeat.recent, cfg.victron.watchdog.timeout_s)`.
     - `wanted = live_wanted(...)`, `handing_back = ownership.handing_back(wanted)`.
     - `required = watchdog_required(..., handing_back=handing_back)`.
     - `modes = effective_modes(..., armed, handing_back=handing_back)`.
     - `OperatingInputs(..., watchdog_armed=armed, handback_pending=bool(handing_back))`.
  5. **Befehle** in CONTROL, DEGRADED und SAFE:
     - Ziele wie in Task 11, dazu `SafeTarget(a, "handback")` für jeden Aktor in `handing_back`.
     - Jeder gesendete Befehl (`verdict.send`) ruft `ownership.note_sent(aktor)`.
  6. In jedem Zyklus: Hat ein Aktor aus `handing_back` seine Sicherwerte erreicht (`safe_values_reached`), folgen `ownership.release` und `foreign.forget`.
  7. `cycle_ok` nach Präzisierung 5 aus `executor.take_failures()` und `keeper.failed`. Bei beiden zählen nur Aktoren mit `needs_watchdog`.
  8. **Heartbeat:** `heartbeat.on_cycle(snow, required=required, allowed=…, release_allowed=…, watchdog=status.state)`:
     - `allowed = cycle_ok and (Zustand in CONTROL, DEGRADED, SAFE or (OBSERVE and gate_ready_except_watchdog(inputs) and not armed))`
     - `release_allowed`: kein owned Aktor mit `needs_watchdog`
  - Beim Start: `ownership = Ownership(initially_owned(gespeicherte Absicht, limits))`.
  - Gesundheit: je Aktor zusätzlich `"owned": bool`.
  - Alarme: zusätzlich `foreign.alarms` und `watchdog.timeout` (`expected_timeout_s = cfg.victron.watchdog.timeout_s`).

- [ ] **Step 1: Failing Tests schreiben und 2a-Tests anpassen**

Diese Tests aus Plan 2a ändern ihre Erwartung. Präzisierung 2 ändert den Bedarf (Regelbetrieb nötig) und das Senden (Gate bis auf den Watchdog offen, Echo).
- `test_heartbeat_only_with_live_watchdog_actuator` heißt jetzt `test_heartbeat_starts_when_the_gate_waits_only_for_the_watchdog`:
  - Mit Regelbetrieb und live gewünschter EVCS kommt bis 30 s frischer Messwerte kein Heartbeat, danach der Zähler 1.
  - Scharf wird der Watchdog erst mit Echo: `Plant(watchdog_state=1)`. Dann folgt CONTROL.
- `test_release_when_live_dropped` und `test_shutdown_sends_no_release` laufen mit Regelbetrieb und `Plant`:
  - Die Freigabe 0 kommt erst nach der Übergabe.
  - Beim Herunterfahren kommt keine 0.
- `test_unarmed_watchdog_keeps_live_request_in_dry_run`, `test_health_reports_intent_live_actuator_and_watchdog` und `test_heartbeat_goes_through_the_victron_adapter`: anpassen, wo das Echo fehlt.

Jede Änderung einer Erwartung nennt der Bericht mit dem Grund.

```python
def live_evcs(runtime_env, *, plant: Plant, state: dict, **kw: Any):
    rt, sink, clock = runtime_env(
        state=state, configure=allow_evcs_live, mqtt_write=plant.write, before_cycle=plant.before_cycle, **kw
    )
    plant.attach(rt.store)
    rt.submit_intent(Intent(version=1, control=True, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    return rt, clock


async def test_restart_within_timeout_resumes_control(runtime_env) -> None:
    plant = Plant(watchdog_state=1, echo=1234)  # scharf, Zähler des früheren Laufs
    rt, clock = live_evcs(runtime_env, plant=plant, state=dict(GOOD_STATE))
    run(rt, clock, 29)
    assert plant.heartbeats() == [] and rt.health().mode == "OBSERVE"
    run(rt, clock, 3)  # Gate bis auf den Watchdog offen: Zähler 1, Echo, CONTROL
    assert plant.heartbeats() == [1] and rt.health().mode == "CONTROL"


async def test_restart_stuck_in_observe_lets_watchdog_trip(runtime_env) -> None:
    plant = Plant(watchdog_state=1, echo=1234)
    state = {k: v for k, v in GOOD_STATE.items() if k != "vebus.phases"}  # Phasen nicht bestätigt
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 100)
    assert rt.health().mode == "OBSERVE" and plant.heartbeats() == []


async def test_control_off_hands_back_then_observe(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 33)
    assert rt.health().mode == "CONTROL"
    rt.ownership.note_sent("wallbox.evcs")  # an Stelle eines Befehls der Regelung (2c)
    rt.submit_intent(Intent(version=2, control=False, actuators={"wallbox.evcs": ActuatorMode.LIVE}))
    run(rt, clock, 1)
    health = rt.health()
    assert (health.mode, health.mode_reasons) == (
        "DEGRADED", ["Regelbetrieb wird beendet: Übergabe der Aktoren läuft."]
    )
    assert ("evcharger/40/SetCurrent", 6) in plant.writes
    run(rt, clock, 3)
    assert rt.health().mode == "OBSERVE"
    assert plant.heartbeats()[-1] == 0  # Freigabe erst nach der Übergabe


async def test_failed_handback_stops_heartbeat(runtime_env) -> None:
    plant = Plant(watchdog_state=1, follow=False)  # die EVCS übernimmt nichts
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 33)
    rt.ownership.note_sent("wallbox.evcs")  # an Stelle eines Befehls der Regelung (2c)
    rt.submit_intent(Intent(version=2, control=True))  # EVCS auf Dry-Run
    run(rt, clock, 45)
    writes = [w for w in plant.writes if w[0] == "evcharger/40/SetCurrent"]
    assert len(writes) == 3
    sent_before = len(plant.heartbeats())
    run(rt, clock, 30)
    assert len(plant.heartbeats()) == sent_before  # kein Heartbeat, keine Freigabe
    assert any(a["id"] == "actuator.failed.wallbox.evcs" for a in rt.health().alarms)


async def test_foreign_change_locks_the_evcs(runtime_env) -> None:
    plant = Plant(watchdog_state=1)
    state = GOOD_STATE | {"wallbox.evcs.gx_set_current_a": 16}
    rt, clock = live_evcs(runtime_env, plant=plant, state=state)
    run(rt, clock, 33)
    state |= {"system.active_in_source": 240}
    run(rt, clock, 3)  # SAFE: 6 A geschrieben und bestätigt, jetzt erwartet
    state.pop("system.active_in_source")
    state |= {"wallbox.evcs.gx_set_current_a": 10}
    plant.follow = False
    run(rt, clock, 40)
    locked = {a["id"]: a["locked"] for a in rt.health().actuators}
    assert locked["wallbox.evcs"] == ["EVCS „evcs“: Strom von außen geändert (10 A statt 6 A)."]
```

Eine EVCS hält erst Werte des EMS, wenn der core ihr etwas gesendet hat. In 2b gibt es noch keine Regelung, deshalb setzen zwei Tests `rt.ownership.note_sent(...)` an die Stelle eines solchen Befehls.

`GOOD_STATE` enthält kein `system.active_in_source`. Ohne das Signal liegt kein Inselbetrieb vor; nach dem Inselbetrieb entfernt `state.pop` es wieder, und `runtime_env` schreibt es nicht mehr nach.

**Zeitablauf in `test_failed_handback_stops_heartbeat`:**
- Die Versuche liegen bei 0, 10 und 20 s nach dem Wechsel auf Dry-Run.
- Ab 30 s ist die Art gescheitert: kein Heartbeat mehr, keine Freigabe.

**`test_foreign_change_locks_the_evcs`:**
- Die Erwartung ist 6 A, der Ist-Wert 10 A.
- Nach drei Prüfungen (bis zu 30 s) ist die EVCS gesperrt und nicht mehr owned.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/integration/test_runtime_control.py -q`
Expected: FAIL (die neuen Tests)

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit tests/integration tests/property -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/runtime.py tests/integration
git commit -m "feat(core): Übergabe, Heartbeat nur bei Regelbetrieb und fremde Änderungen in der Laufzeit"
```

---

### Task 13: Interne API: Entscheidungen

**Files:**
- Modify: `src/bindaems/core/api/app.py`
- Test: `tests/unit/core/api/test_app.py`

**Interfaces:**
- Consumes: `CoreRuntime.decisions` (Task 11), `decision_to_json`
- Produces:
  - `CoreView.decisions(since: datetime, limit: int) -> tuple[list[Decision], bool]`
  - `GET /v1/decisions?since=<ISO-Zeit>&limit=<n>`:
    - `since` ist Pflicht und braucht eine Zeitzone; `limit` ist standardmäßig 1000, erlaubt 1–5000.
    - Antwort `{"decisions": [decision_to_json(...)], "more": bool}`.
    - Ohne gültiges Token: 401.
    - Bei falschem `since` oder `limit`: 422 mit `{"detail": "since muss eine ISO-Zeit mit Zeitzone sein"}` bzw. `"limit muss zwischen 1 und 5000 liegen"`.

- [ ] **Step 1: Failing Tests schreiben**

```python
def test_decisions_since_with_limit(api_client, fake_view) -> None:
    fake_view.decision_list = [decision(id="d1", ts=T0 + timedelta(seconds=1))]
    response = api_client.get("/v1/decisions", params={"since": T0.isoformat(), "limit": 1}, headers=AUTH)
    assert response.status_code == 200
    assert response.json()["decisions"][0]["id"] == "d1" and response.json()["more"] is False
    assert fake_view.decision_calls == [(T0, 1)]


@pytest.mark.parametrize(
    ("params", "detail"),
    [
        ({"since": "gestern"}, "since muss eine ISO-Zeit mit Zeitzone sein"),
        ({"since": "2026-10-08T10:00:00"}, "since muss eine ISO-Zeit mit Zeitzone sein"),
        ({"since": "2026-10-08T10:00:00+00:00", "limit": 0}, "limit muss zwischen 1 und 5000 liegen"),
    ],
)
def test_decisions_reject_bad_parameters(api_client, params, detail) -> None:
    response = api_client.get("/v1/decisions", params=params, headers=AUTH)
    assert (response.status_code, response.json()) == (422, {"detail": detail})


def test_decisions_need_the_token(api_client) -> None:
    assert api_client.get("/v1/decisions", params={"since": T0.isoformat()}).status_code == 401
```

`api_client`, `fake_view` und `AUTH` sind die vorhandenen Fixtures bzw. Konstanten der API-Tests. `fake_view` bekommt `decisions` mit den Feldern `decision_list` und `decision_calls`, `decision()` kommt aus `tests.unit.shared.test_decisions`. Heißen die Fixtures anders, nimm die vorhandenen und passe nur die Namen an.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/api -q`
Expected: FAIL (404)

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/api tests/integration -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/api/app.py tests/unit/core/api
git commit -m "feat(api): GET /v1/decisions liefert das Entscheidungslog seit einem Zeitpunkt"
```

---

### Task 14: Cerbo-Watchdog 1.1.0, Override-Aktion und Probe

**Files:**
- Modify:
  - `cerbo-watchdog/bindaems_watchdog/__init__.py`, `service.py`, `state_file.py`
  - `src/bindaems/core/watchdog_client/status.py` (`WATCHDOG_VERSION`)
  - `src/bindaems/tools/watchdog_config.py`, `src/bindaems/tools/watchdog_probe.py`
  - `cerbo-watchdog/config.ini.example`, `cerbo-watchdog/README.md`
- Test: `tests/unit/cerbo_watchdog/test_service.py`, `test_state_file.py`, `test_contracts.py`; `tests/unit/tools/test_watchdog_config.py`, `test_watchdog_probe.py`

**Interfaces:**
- Produces:
  - `VERSION = "1.1.0"` im Watchdog und `WATCHDOG_VERSION = "1.1.0"` im core. Ein Test prüft die Gleichheit; den gibt es in `test_contracts.py` schon.
  - `WatchdogService._step` speichert `StoredState(state in (ARMED, TRIPPED), …)`: „ausgelöst“ übersteht einen Neustart als „scharf“.
  - `write_state` sichert nach `os.replace` auch das Verzeichnis: `os.open(dir, os.O_RDONLY)`, `os.fsync`, `os.close`, mit Python 3.8. Ein Fehler dabei kommt nicht durch: Der Inhalt steht schon.
  - `render_config(cfg)` schreibt bei `cfg.victron.live_allowed` diesen Abschnitt hinter die EVCS-Abschnitte:

    ```
    ; Netz-Sollwert-Override des EMS aufheben (victron.live_allowed)
    [action:override_setpoint]
    service = com.victronenergy.hub4
    path = /Overrides/Setpoint
    value = null
    ```
  - Probe: Die Zeile „Auslösung ohne Heartbeat“ entsteht nach „Wieder scharf“ mit dem dann gelesenen `/LastTripFailures`. Die Reihenfolge der Zeilen im Bericht bleibt.

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/cerbo_watchdog/test_service.py
def test_tripped_is_stored_as_armed() -> None:
    saved: list[StoredState] = []
    service, clock = armed_service(save=saved.append)  # vorhandener Helfer der Datei
    clock.advance(91)  # ohne Heartbeat: Auslösung
    service.tick()
    assert saved[-1].armed is True


# tests/unit/cerbo_watchdog/test_state_file.py
def test_write_state_syncs_the_directory(tmp_path, monkeypatch) -> None:
    synced: list[int] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: synced.append(fd) or real_fsync(fd))
    write_state(str(tmp_path / "state.json"), StoredState(armed=True))
    assert len(synced) == 2  # Datei und Verzeichnis


# tests/unit/tools/test_watchdog_config.py
def test_override_action_only_with_victron_live(cfg) -> None:
    assert "[action:override_setpoint]" not in render_config(cfg)
    live = cfg.model_copy(update={"victron": cfg.victron.model_copy(update={"live_allowed": True})})
    text = render_config(live)
    assert (
        "[action:override_setpoint]\nservice = com.victronenergy.hub4\n"
        "path = /Overrides/Setpoint\nvalue = null" in text
    )
    assert parse_settings(text).actions[-1].value is None  # der Watchdog liest es als „ungültig“


# tests/unit/tools/test_watchdog_probe.py
async def test_trip_failures_are_read_after_rearming() -> None:
    cerbo = SimulatedCerbo(failures_after_rearm=1)  # meldet den Endstand erst beim Wiederscharfwerden
    findings = await run_probe(cerbo.transport(), P, FAST)
    trip = next(f for f in findings if f.title == "Auslösung ohne Heartbeat")
    assert "fehlgeschlagene Aktionen 1" in trip.detail
```

- `armed_service`, `SimulatedCerbo` und `FAST` sind die vorhandenen Helfer der jeweiligen Testdatei. `SimulatedCerbo` bekommt die Option `failures_after_rearm: int | None`: `/LastTripFailures` springt erst beim Wechsel nach „scharf“ auf diesen Wert.
- `parse_settings` kommt aus `bindaems_watchdog.config`. Die Tests importieren das Watchdog-Paket schon so, wie es `tests/unit/cerbo_watchdog` vorgibt.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/cerbo_watchdog tests/unit/tools -q`
Expected: FAIL (die neuen Tests)

- [ ] **Step 3: Umsetzen** wie in den Interfaces. `config.ini.example` und `cerbo-watchdog/README.md` nennen die neue Aktion und die Version. ruff mit `target-version = "py38"` und der `ast`-Test aus 2a gelten weiter.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/cerbo_watchdog tests/unit/tools tests/unit/core/watchdog_client -q && uv run ruff check cerbo-watchdog`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add cerbo-watchdog src/bindaems/core/watchdog_client/status.py src/bindaems/tools tests/unit/cerbo_watchdog tests/unit/tools
git commit -m "feat(watchdog): 1.1.0 – ausgelöst übersteht Neustart, Override-Aktion, Probe liest Endstand"
```

---

### Task 15: Prüfprotokoll Teil 2: Gerüst und EVCS (17.2-6, 17.2-5)

**Files:**
- Create: `src/bindaems/tools/verify_part2.py`, `src/bindaems/tools/part2/__init__.py`, `src/bindaems/tools/part2/session.py`, `src/bindaems/tools/part2/evcs.py`
- Test: `tests/unit/tools/part2/conftest.py`, `test_session.py`, `test_evcs.py`, `test_cli.py`

**Interfaces:**
- Consumes:
  - Adapter: `VictronMqttAdapter`, `AiomqttTransport`, `EvcsAdapter`, `PymodbusReader`, `TessieAdapter`, `StateStore`
  - Aktoren (Tasks 5–7), `check_range` (Regeln 1–3), `ForeignChangeDetector` (Task 10)
  - Aus den Werkzeugen: `Finding`, `render_report` (verify_checks) und `run_probe`, `timings_from_config` (Probe)
- Produces in `part2/session.py`:
  - `class Part2Session`:
    - `__init__(self, cfg: Config, *, store: StateStore, adapters: Sequence[Adapter], actuators: Mapping[str, Actuator], ask: Callable[[str], bool], monotonic: Callable[[], float] = time.monotonic, poll_s: float = 0.5)`
    - `cfg` bleibt als Attribut erreichbar.
    - `async start()` startet die Adapter als Tasks; `async stop()` bricht sie ab.
    - `snapshot() -> Snapshot`
    - `async wait_for(predicate: Callable[[Snapshot], bool], timeout_s: float) -> float | None`: Sekunden bis zum Treffer oder `None`
    - `async set_and_confirm(actuator: str, kind: CommandKind, value: CommandValue, timeout_s: float) -> float | None`:
      - Prüft mit `check_range`; Ablehnung oder Klemmen ergibt einen `ValueError` (Programmierfehler im Werkzeug).
      - Merkt sich beim ersten Schreiben je Aktor und Art den Ist-Wert aus `present`.
      - Sendet über den Aktor und wartet auf `CONFIRMED`.
    - `async restore() -> list[Finding]`: stellt die gemerkten Werte in umgekehrter Reihenfolge her (Frist 15 s); je Wert ein Befund „Wiederhergestellt“ (OK) oder FEHLER.
    - `ask(question: str) -> bool`
  - `build_session(cfg: Config, secrets: Secrets, *, ask: Callable[[str], bool]) -> Part2Session`:
    - echte Adapter: Victron per MQTT, je EVCS Modbus (lesend), je Fahrzeug Tessie, falls ein Token da ist
    - die Aktoren wie im core, mit `write` des Victron-Adapters
- Produces in `part2/evcs.py`:
  - `@dataclass(frozen=True) class EvcsTimings`: `confirm_s: float = 15.0`, `check_s: float = 10.0`, `foreign_max_s: float = 120.0`, `unplug_s: float = 60.0`, `safe_s: float = 30.0`
  - `async run_evcs(session, cfg, key: str, *, probe: Callable[[], Awaitable[list[Finding]]], timings: EvcsTimings = EvcsTimings()) -> list[Finding]`
- Produces in `verify_part2.py`:
  - `main(argv: list[str] | None = None) -> int` mit diesen Argumenten:
    - `--config` (Pflicht)
    - `--punkt {evcs,tesla,victron}` (Pflicht)
    - `--wallbox` (Standard: erste EVCS) und `--fahrzeug` (Standard: erstes Fahrzeug mit Tessie)
    - `--out` (Standard: `docs/verification/<JJJJ-MM-TT>-pruefprotokoll-teil2-<punkt>.md`)
    - `--yes` und `--schlafend` (nur `tesla`)
  - Ablauf: Konfiguration und Secrets laden, `build_session`, `start`, den Teil ausführen. Im `finally` immer `restore`, dann `stop`, dann den Bericht schreiben.
  - Exitcodes wie bei der Probe:

    | Code | Bedeutung |
    |---|---|
    | 0 | kein FEHLER |
    | 1 | FEHLER, verneinte Rückfrage oder Bericht nicht geschrieben |
    | 2 | Konfiguration oder Secrets ungültig |
    | 130 | Strg+C, nachdem `restore` gelaufen ist |
  - Bericht: `render_report(findings, meta, title="Prüfprotokoll Teil 2 – EVCS")` (bzw. „Tesla“, „Victron“). Meta: Werkzeug `verify_part2`, Datum, Punkt, Gerät, Venus OS und Firmware, soweit bekannt.

**Die Schritte von `run_evcs`:**

| Punkt | Titel | Ablauf | Ergebnis |
|---|---|---|---|
| 17.2-6 | EVCS bereit | `capabilities` | OK „Instanz {i}, manueller Modus, Sollstrom {a} A, Laden {an\|aus}“; sonst FEHLER mit den Gründen, und der Teil endet ohne Schreiben |
| 17.2-6 | Strom {x} A | `set_and_confirm` für `min_a`, 10 (falls dazwischen) und `max_a` | OK „bestätigt nach {t} s“; FEHLER „nicht bestätigt nach {confirm_s} s“ |
| 17.2-6 | Laden aus und an | Rückfrage „Darf die EVCS kurz „Laden aus“ und wieder „an“ schalten? Ein angestecktes Fahrzeug pausiert dann.“; `CHARGING` False, dann True | OK „aus nach {t1} s, an nach {t2} s“, bei ladendem Fahrzeug (> 500 W vorher) dazu „, Leistung unter 100 W nach {t3} s“; nein: INFO „übersprungen“ |
| 17.2-6 | Fremde Änderung | Rückfrage „Bitte jetzt den Ladestrom der EVCS von Hand ändern (VictronConnect oder Cerbo). Bereit?“; Erkennung wie im core, Erwartung = letzter bestätigter Strom, Prüfung alle `check_s` | OK „erkannt nach {n} Prüfungen ({ist} A statt {soll} A)“; WARNUNG „keine Änderung erkannt“; nein: INFO „übersprungen“ |
| 17.2-6 | Verbindung unterbrochen | Rückfrage „Netzwerkkabel der EVCS für 60 s abziehen?“; vorher Strom 10 A (höchstens `max_a`); nach „Wieder angesteckt?“ bis `unplug_s` auf Messwerte warten | INFO „nach der Unterbrechung {x} A (vorher {y} A)“; WARNUNG „keine Messwerte nach der Unterbrechung“ |
| 17.2-5 | Sicherer Strom über den Watchdog | Rückfrage „Watchdog-Probe mit verstelltem Strom starten? Der Watchdog löst aus.“; Strom auf `min(safe_a + 4, max_a)`, dann `probe()` (deren Befunde stehen davor im Bericht), danach bis `safe_s` auf `safe_a` warten | OK „Watchdog setzte {safe} A (vorher {x} A)“; FEHLER „Strom nach der Auslösung {x} A statt {safe} A“ |

Am Ende stellt `restore` alle Werte wieder her (eigene Zeilen).

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/tools/part2/conftest.py
class FakeEvcs:
    """EVCS hinter dem GX-Dienst für die Teil-2-Tests.

    ``send`` merkt sich jeden Auftrag in ``writes`` und übernimmt ihn nach ``delay_s`` als
    GX-Wert in den Speicher (``SetCurrent`` → ``gx_set_current_a``, ``StartStop`` →
    ``gx_start_stop``), solange ``follow`` gilt. ``change_by_hand`` setzt den Strom sofort, wie
    eine Änderung in VictronConnect.
    """

    def __init__(self, store: StateStore, *, delay_s: float = 0.01, follow: bool = True) -> None:
        self.store, self.delay_s, self.follow = store, delay_s, follow
        self.writes: list[tuple[str, float | int | None]] = []

    def send(self, path: str, value: float | int | None) -> bool:
        self.writes.append((path, value))
        if self.follow:
            signal = "gx_set_current_a" if path.endswith("SetCurrent") else "gx_start_stop"
            asyncio.get_running_loop().call_later(self.delay_s, self._apply, signal, value)
        return True

    def change_by_hand(self, current_a: int) -> None:
        self._apply("gx_set_current_a", current_a)

    def _apply(self, signal: str, value: float | int | None) -> None:
        self.store.update(f"wallbox.evcs.{signal}", value, source="victron", kind=SignalKind.STATE)


@pytest.fixture
def evcs_session(cfg) -> Callable[..., tuple[Part2Session, FakeEvcs]]:
    def build(
        *,
        answers: Sequence[bool] = (),
        state: Mapping[str, Value] | None = None,
        follow: bool = True,
        hand_change_a: int | None = None,
    ) -> tuple[Part2Session, FakeEvcs]:
        store = StateStore(SystemClock())
        store.register_source("victron", 5.0)
        store.set_connected("victron", True)
        start = {"wallbox.evcs.gx_mode": 0, "wallbox.evcs.gx_set_current_a": 16} | dict(state or {})
        for name, value in start.items():
            store.update(name, value, source="victron", kind=SignalKind.STATE)
        evcs = FakeEvcs(store, follow=follow)
        replies = iter(answers)

        def ask(question: str) -> bool:
            reply = next(replies)
            if reply and hand_change_a is not None and question.startswith("Bitte jetzt den Ladestrom"):
                evcs.change_by_hand(hand_change_a)
            return reply

        actuator = EvcsActuator("evcs", cfg.wallboxes["evcs"], 40, evcs.send, lambda: True)
        session = Part2Session(
            cfg, store=store, adapters=[], actuators={actuator.id: actuator}, ask=ask, poll_s=0.01
        )
        return session, evcs

    return build


# tests/unit/tools/part2/test_evcs.py
FAST = EvcsTimings(confirm_s=0.2, check_s=0.01, foreign_max_s=0.5, unplug_s=0.2, safe_s=0.2)


async def test_not_manual_stops_without_writes(evcs_session) -> None:
    session, evcs = evcs_session(state={"wallbox.evcs.gx_mode": 1})
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert findings[0].status == "fail" and evcs.writes == []


async def test_currents_are_confirmed_and_restored(evcs_session) -> None:
    session, evcs = evcs_session(answers=[False, False, False, False], state={"wallbox.evcs.gx_set_current_a": 12})
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    findings += await session.restore()
    assert [f.title for f in findings if f.status == "ok"][:3] == ["Strom 6 A", "Strom 10 A", "Strom 16 A"]
    assert [v for p, v in evcs.writes if p.endswith("SetCurrent")] == [6, 10, 16, 12]


async def test_unconfirmed_current_is_an_error(evcs_session) -> None:
    session, _ = evcs_session(follow=False, answers=[False] * 4)
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert any(f.title == "Strom 6 A" and f.detail == "nicht bestätigt nach 0,2 s" for f in findings)


async def test_foreign_change_is_detected(evcs_session) -> None:
    session, evcs = evcs_session(answers=[False, True, False, False], hand_change_a=10)
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    foreign = next(f for f in findings if f.title == "Fremde Änderung")
    assert (foreign.status, foreign.detail) == ("ok", "erkannt nach 3 Prüfungen (10 A statt 16 A)")


async def test_watchdog_step_checks_the_safe_current(evcs_session) -> None:
    session, evcs = evcs_session(answers=[False, False, False, True])

    async def probe() -> list[Finding]:  # Watchdog setzt den sicheren Strom
        evcs.send("evcharger/40/SetCurrent", 6)
        return [Finding("17.2-5", "Auslösung ohne Heartbeat", "ok", "…")]

    findings = await run_evcs(session, session.cfg, "evcs", probe=probe, timings=FAST)
    step = next(f for f in findings if f.title == "Sicherer Strom über den Watchdog")
    assert (step.status, step.detail) == ("ok", "Watchdog setzte 6 A (vorher 10 A)")


async def test_values_outside_the_limits_raise(evcs_session) -> None:
    session, _ = evcs_session()
    with pytest.raises(ValueError):
        await session.set_and_confirm("wallbox.evcs", CommandKind.CURRENT, 32.0, 0.2)


# tests/unit/tools/part2/test_cli.py
def test_invalid_config_returns_2(capsys) -> None:
    assert main(["--config", "/gibt/es/nicht.yaml", "--punkt", "evcs"]) == 2


def test_report_is_written_and_values_restored(tmp_path, monkeypatch, fake_part) -> None:
    # fake_part ersetzt build_session und run_evcs; prüft: restore lief, Bericht liegt unter --out
    out = tmp_path / "bericht.md"
    assert main(["--config", "deploy/config.example.yaml", "--punkt", "evcs", "--yes", "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Prüfprotokoll Teil 2 – EVCS – ")
    assert fake_part.restored is True


def test_ctrl_c_restores_and_returns_130(tmp_path, fake_part) -> None:
    fake_part.raise_in_step = KeyboardInterrupt
    out = tmp_path / "bericht.md"
    assert main(["--config", "deploy/config.example.yaml", "--punkt", "evcs", "--yes", "--out", str(out)]) == 130
    assert fake_part.restored is True
```

**`fake_part`** ersetzt per `monkeypatch` `build_session` (durch eine Session mit `FakeEvcs`) und `run_evcs` (liefert einen OK-Befund oder wirft `raise_in_step`). Es merkt sich in `restored`, ob `restore` lief. `no_probe` liefert `[]`. Die Zahl der Rückfragen in `answers` folgt der Tabelle oben: Laden aus/an, fremde Änderung, Verbindung, Watchdog.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools/part2 -q`
Expected: FAIL (`ModuleNotFoundError: bindaems.tools.part2`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces und der Tabelle. Zahlen in Texten über `de()`.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools tests/unit/tools/part2
git commit -m "feat(tools): Prüfprotokoll Teil 2 – Gerüst und EVCS mit Wiederherstellen"
```

---

### Task 16: Prüfprotokoll Teil 2: Tesla (17.2-7)

**Files:**
- Create: `src/bindaems/tools/part2/tesla.py`
- Modify: `src/bindaems/tools/verify_part2.py` (Punkt `tesla`)
- Test: `tests/unit/tools/part2/test_tesla.py`

**Interfaces:**
- Consumes: `Part2Session` (Task 15), `TeslaActuator` (Task 7); Wall-Connector-Signale `wallbox.<twc>.l1.current_a` bis `l3`, `wallbox.<twc>.vehicle_connected`, `wallbox.<twc>.contactor_closed`
- Produces:
  - `@dataclass(frozen=True) class TeslaTimings`: `confirm_s: float = 75.0`, `effect_s: float = 90.0`, `sleep_total_s: float = 600.0`, `sleep_check_s: float = 60.0`
  - `async run_tesla(session, cfg, key: str, *, asleep_check: bool = False, timings: TeslaTimings = TeslaTimings()) -> list[Finding]`

**Die Schritte:** Ladestrom am Wall Connector ist der größte Phasenstrom.

| Punkt | Titel | Ablauf | Ergebnis |
|---|---|---|---|
| 17.2-7 | Tesla bereit | `capabilities` | OK „SOC {s} %, Ladestrom {a} A, {charging_state}“; sonst FEHLER mit den Gründen, Ende ohne Schreiben |
| 17.2-7 | Ladestrom {x} A | `set_and_confirm` mit 10 A (falls im Bereich); dann warten, bis der Ladestrom am Wall Connector höchstens 1,5 A von x abweicht | OK „Tessie {t1} s, Wall Connector {t2} s“; ohne Fahrzeug am Wall Connector OK „Tessie {t1} s; Wirkung nicht messbar (nicht am Wall Connector)“; FEHLER „nicht bestätigt nach {confirm_s} s“ |
| 17.2-7 | Verhalten bei 5 A | wie oben mit `min_a` (5) | wie oben |
| 17.2-7 | Laden aus und an | Rückfrage „Darf der Tesla kurz „Laden aus“ und wieder „an“ schalten?“; `CHARGING` False, dann True | OK „aus nach {t1} s (Schütz offen nach {t2} s), an nach {t3} s“; nein: INFO „übersprungen“ |
| 17.2-7 | Schlafendes Auto wird nicht geweckt | nur mit `asleep_check`; verlangt `online` False am Anfang; Prüfung alle `sleep_check_s`, insgesamt `sleep_total_s` | OK „{min} min ohne Wecken, Befehle verweigert“; WARNUNG „Fahrzeug wurde nach {t} min wach; Ursache prüfen (App, Zeitplan)“ |

- Mit `asleep_check` läuft nur der letzte Schritt.
- `capabilities` muss dabei den Grund „Fahrzeug schläft …“ nennen.
- Ohne `asleep_check` bei schlafendem Fahrzeug: „Tesla bereit“ ist FEHLER mit diesem Grund.

- [ ] **Step 1: Failing Tests schreiben**

```python
FAST = TeslaTimings(confirm_s=0.3, effect_s=0.3, sleep_total_s=0.3, sleep_check_s=0.1)


async def test_current_change_reaches_the_wall_connector(tesla_session, respx_mock) -> None:
    respx_mock.post(AMPS_URL).respond(json={"result": True})
    session, twc = tesla_session(answers=[False], twc_follows=True)  # TWC übernimmt den Strom
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    step = next(f for f in findings if f.title == "Ladestrom 10 A")
    assert step.status == "ok" and step.detail.startswith("Tessie ") and "Wall Connector" in step.detail


async def test_asleep_stops_before_any_command(tesla_session, respx_mock) -> None:
    session, _ = tesla_session(state={"vehicle.tesla.online": False})
    findings = await run_tesla(session, session.cfg, "tesla", timings=FAST)
    assert (findings[0].status, findings[0].detail) == (
        "fail", "Fahrzeug schläft oder ist offline; es wird nicht geweckt."
    )
    assert respx_mock.calls.call_count == 0


async def test_sleep_check_never_sends(tesla_session, respx_mock) -> None:
    session, _ = tesla_session(state={"vehicle.tesla.online": False})
    findings = await run_tesla(session, session.cfg, "tesla", asleep_check=True, timings=FAST)
    assert [f.status for f in findings] == ["ok"] and respx_mock.calls.call_count == 0
```

`tesla_session` baut die Session wie `evcs_session`, mit einem `TeslaActuator` an einem `httpx.AsyncClient` unter respx. Es setzt die Zustandswerte für ein Fahrzeug zu Hause, angesteckt und online, dazu Wall-Connector-Werte. Mit `twc_follows` übernimmt der Wall Connector den bestätigten Strom als Phasenstrom. `AMPS_URL` ist die Tessie-URL aus Task 7.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools/part2/test_tesla.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces und der Tabelle.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools tests/unit/tools/part2
git commit -m "feat(tools): Prüfprotokoll Teil 2 – Tesla über Tessie, ohne zu wecken"
```

---

### Task 17: Prüfprotokoll Teil 2: Victron (17.2-1 bis 17.2-4, 17.2-8)

**Files:**
- Create: `src/bindaems/tools/part2/victron.py`
- Modify: `src/bindaems/tools/verify_part2.py` (Punkt `victron`)
- Test: `tests/unit/tools/part2/test_victron.py`

**Interfaces:**
- Consumes: `Part2Session`, `VictronActuator`; Signale `dess.mode`, `grid.power_w` (Summe der Phasen aus `grid.l1.power_w` bis `l3`), `battery.power_w`, `ess.override.*`, `ess.grid_setpoint_w`, `ess.max_discharge_w`, `wallbox.<evcs>.power_w`, `grid.l<n>.current_a`
- Produces:
  - `@dataclass(frozen=True) class VictronTimings`: `confirm_s: float = 15.0`, `observe_s: float = 30.0`, `effect_s: float = 60.0`, `hold_s: float = 600.0`, `reboot_s: float = 600.0`
  - `async run_victron(session, cfg, *, timings: VictronTimings = VictronTimings()) -> list[Finding]`

**Die Schritte:** Netzleistung ist jeweils der Mittelwert über `observe_s`.

| Punkt | Titel | Ablauf | Ergebnis |
|---|---|---|---|
| 17.2-1 | Victron bereit | `dess.mode` muss 0 sein; dazu die `capabilities` | Ist DESS an: FEHLER „Dynamic ESS ist aktiv (Modus {m}); die Victron-Punkte erst nach dem Abschalten von DESS.“, Ende ohne Schreiben; sonst OK „hub4-Overrides vorher: {Liste}“ |
| 17.2-1 | Netz-Sollwert-Override | Rückfrage; `GRID_SETPOINT` = `min(1000, grid_setpoint_max)`, Wirkung beobachten; dann `None`, ebenso | OK „{v} W nach {t1} s bestätigt; Netz vorher {a} W, mit Override {b} W; aufgehoben nach {t2} s, danach {c} W“. Ist `None` nicht bestätigt: FEHLER „Aufheben mit null nicht bestätigt; Weg für die Rücknahme klären.“ |
| 17.2-1 | Override ohne Timeout | optional (Rückfrage); Override setzen, `hold_s` halten | OK „nach {min} min noch aktiv“; INFO „nach {t} min verschwunden“ |
| 17.2-1 | Override nach Neustart des Cerbo | optional; Override setzen, Rückfrage „Cerbo jetzt neu starten?“, auf Neuverbindung warten (bis `reboot_s`) | OK „nach dem Neustart aufgehoben“; WARNUNG „nach dem Neustart noch aktiv ({v} W)“ |
| 17.2-1 | Zusammenspiel mit dem persistenten Sollwert | liest `ess.grid_setpoint_w` vor und nach den Override-Schritten; schreibt ihn nicht | INFO „AcPowerSetPoint {x} W blieb unverändert“ bzw. „änderte sich von {x} W auf {y} W“ |
| 17.2-2 | Entladesperre | INFO, ob `ess.override.maxdischargepower` existiert; Rückfrage; `MAX_DISCHARGE` 0, `effect_s` beobachten, Originalwert zurück | OK „Akku entlädt höchstens {w} W“ (≤ 100 W); WARNUNG sonst |
| 17.2-3 | Netzladen | Rückfrage; Override `min(2000, grid_setpoint_max)` | OK „Akku lädt mit {w} W“ (> 500 W binnen `effect_s`); WARNUNG sonst; Override aufheben |
| 17.2-4 | Kein Akku ins Auto | nur bei EVCS-Leistung > 1000 W (sonst INFO „übersprungen: kein Laden an der EVCS“); Rückfrage; Override = EVCS-Leistung | OK „Akku entlädt höchstens {w} W“ (≤ 200 W); WARNUNG sonst; Override aufheben |
| 17.2-8 | Importlimit | Rückfrage „Last so einschalten, dass eine Phase über 20 A käme?“; `effect_s` beobachten | INFO „höchster Netzstrom {a} A (L{n}), Akku {w} W“ |

Am Ende `restore`: Override aufheben und den Originalwert der Entladegrenze herstellen.

- [ ] **Step 1: Failing Tests schreiben**

```python
FAST = VictronTimings(confirm_s=0.2, observe_s=0.05, effect_s=0.2, hold_s=0.1, reboot_s=0.2)


async def test_dess_on_stops_without_writes(victron_session) -> None:
    session, cerbo = victron_session(state={"dess.mode": 1})
    findings = await run_victron(session, session.cfg, timings=FAST)
    assert findings[0].detail == "Dynamic ESS ist aktiv (Modus 1); die Victron-Punkte erst nach dem Abschalten von DESS."
    assert cerbo.writes == []


async def test_override_and_its_release(victron_session) -> None:
    session, cerbo = victron_session(answers=[True] + [False] * 6)
    findings = await run_victron(session, session.cfg, timings=FAST)
    step = next(f for f in findings if f.title == "Netz-Sollwert-Override")
    assert step.status == "ok" and "aufgehoben nach" in step.detail
    assert [v for p, v in cerbo.writes if p == "hub4/0/Overrides/Setpoint"] == [1000.0, None]


async def test_null_that_is_not_confirmed_is_reported(victron_session) -> None:
    session, _ = victron_session(answers=[True] + [False] * 6, null_clears=False)
    findings = await run_victron(session, session.cfg, timings=FAST)
    step = next(f for f in findings if f.title == "Netz-Sollwert-Override")
    assert (step.status, step.detail) == ("fail", "Aufheben mit null nicht bestätigt; Weg für die Rücknahme klären.")
```

`victron_session` baut die Session mit `VictronActuator` über einen `FakeCerbo`: Er übernimmt geschriebene Werte in `ess.override.setpoint`, `ess.max_discharge_w` und `ess.min_soc_pct`. Mit `null_clears=False` bleibt der Override nach `null` stehen. Messwerte für Netz und Akku kommen aus `state`, mit `dess.mode` 0 als Vorgabe.

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools/part2/test_victron.py -q`
Expected: FAIL (`ModuleNotFoundError`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces und der Tabelle. Persistente Schreibzugriffe macht das Werkzeug je Lauf höchstens viermal: zweimal Entladegrenze, und je einmal Wiederherstellen bei Abbruch.

- [ ] **Step 4: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools tests/unit/tools/part2
git commit -m "feat(tools): Prüfprotokoll Teil 2 – Victron-Punkte mit Override, Entladesperre und Netzladen"
```

---

### Task 18: Doku, Spec, Importvertrag und Abdeckung

**Files:**
- Modify:
  - `pyproject.toml`: Importvertrag
  - `.github/workflows/ci.yml`: Abdeckung `core/actuators`
  - `docs/superpowers/specs/2026-10-08-bindaems-design.md`
  - `docs/betrieb.md`, `docs/verification/README.md`, `docs/uebergabe.md`, `deploy/config.example.yaml` (nur Kommentare)
- Test: `tests/unit/test_layout.py` (falls er die Pakete aufzählt), Importverträge

**Inhalt:**
- **Importvertrag** „Safety-Schicht und Betriebszustände bleiben rein“: zusätzlich `bindaems.core.actuators` verbieten.
- **CI:** `coverage report --include="src/bindaems/core/safety/*,src/bindaems/core/control/*,src/bindaems/core/actuators/*" --fail-under=90`.
- **Spec,** je ein Absatz „Stand Plan 2b“ (`docs/superpowers/plans/2026-10-11-phase-2b-aktoren.md`, Präzisierung n) in:

  | Abschnitt | Inhalt |
  |---|---|
  | 5.3 | `GET /v1/decisions` mit `limit`, Stream `decision` |
  | 6.2 | Schreibwege per MQTT, `null` vorläufig |
  | 6.3 | EVCS schreibt über den GX-Dienst `evcharger` (Entscheidung des Betreibers), liest per Modbus |
  | 6.5 | Tessie-Befehle, nie wecken, 3 Fehler in 15 min |
  | 7.1 | fehlerfreier Zyklus |
  | 7.2 | Heartbeat-Regel und Übergabe mit `REASON_HANDBACK` |
  | 7.9 | fremde Änderungen mit Treffern und Haltezeit |
  | 7.10 | Felder, Spool und Umfang des Protokolls |
  | 8.5 | Übergabe und `SafeStateKeeper` |
  | 8.6 | Version 1.1.0, Override-Aktion, „ausgelöst“ übersteht Neustart, Freigabe-Regeln |
  | 17.2 | Werkzeug `verify_part2` und Reihenfolge der Punkte (EVCS und Tesla jetzt, Victron nach dem Abschalten von DESS) |
- **Betriebshandbuch:**
  - Neuer Abschnitt „Prüfprotokoll Teil 2“: Aufruf je Punkt wie bei der Probe (`docker compose run --rm -v "$PWD/verification:/out" --entrypoint python ems-core -m bindaems.tools.verify_part2 --config /config/config.yaml --punkt evcs --out /out/<Datum>-pruefprotokoll-teil2-evcs.md`), dazu Voraussetzungen, Rückfragen, Wiederherstellen, Strg+C, DESS-Hinweis und Bericht.
  - Abschnitt 13: Übergabe beim Wechsel auf Dry-Run und bei „Regelbetrieb aus“; Heartbeat nur bei Regelbetrieb.
  - Abschnitt 14: Update auf 1.1.0 (`config.ini` neu erzeugen, `install.sh`), Override-Aktion bei `victron.live_allowed`.
  - „Meldungen des core“: die neuen Alarme aus „Texte“.
  - Entscheidungen unter `/data/decisions/`.
  - Geparkter Befund aus 2a: Hinweis „Warte, bis der Watchdog ‚wartet‘ zeigt“ gilt nur, wenn kein anderer Victron- oder EVCS-Aktor live bleibt.
- **`docs/verification/README.md`:**
  - Abschnitt „Teil 2“ mit dem Werkzeug, den Berichten und den Punkten.
  - Geparkter Befund aus 2a: Die Tabelle der Probe ist durch einen Absatz unterbrochen; die Zeile „Freigabe ohne Auslösung“ gehört in die Tabelle.
- **`docs/uebergabe.md`:**
  - Stand 2b.
  - Als Nächstes an der Anlage:
    1. Images ausrollen.
    2. `config.ini` neu erzeugen und den Watchdog mit `install.sh` auf 1.1.0 bringen.
    3. Probe wiederholen.
    4. Teil 2 EVCS und Tesla gemeinsam durchführen.
    5. Die Berichte nach `docs/verification/` übernehmen.
  - Danach: Plan 2c. Victron-Teil 2, sobald DESS aus ist.
  - Hinweis für 2c: Importlimit 20 A je Phase (Peak Shaving „immer“) beim Phasenwächter und bei der Setpoint-Kompensation berücksichtigen.

- [ ] **Step 1: Ändern** wie oben.

- [ ] **Step 2: Alle Prüfungen laufen lassen**

Run: `uv run ruff check . && uv run ruff format --check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports && uv run pytest -q --cov --cov-fail-under=75 && uv run coverage report --include="src/bindaems/core/safety/*,src/bindaems/core/control/*,src/bindaems/core/actuators/*" --fail-under=90 && (cd ui && pnpm lint && pnpm check && pnpm test)`
Expected: alles grün, 5 Importverträge eingehalten

- [ ] **Step 3: Commit**

```bash
git add pyproject.toml .github/workflows/ci.yml docs deploy/config.example.yaml
git commit -m "docs: Plan 2b in Spec, Betriebshandbuch, Prüfprotokollen und Übergabe; Importvertrag und Abdeckung"
```

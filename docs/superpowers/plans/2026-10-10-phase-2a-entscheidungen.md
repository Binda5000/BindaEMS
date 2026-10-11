# Plan 2a – Entscheidungen und offene Punkte aus der Umsetzung

Stand: 11.10.2026, nach Gesamtreview und Fix-Welle. Diese Datei hält fest, was bei der Umsetzung von
`2026-10-10-phase-2a-sicherheitsbasis.md` entschieden wurde, wo der Plan dafür angepasst wurde, und
welche kleinen Befunde bewusst aufgeschoben sind. Jede Entscheidung nennt Grund und Folgen, falls
sie falsch ist. Präfixe: `F..` = Vorabprüfung des Plans, `Task N` = Task des Plans, `Final` =
Gesamtreview.

## Entscheidungen (83)

- F01 Ruling: Fixtures `limits`/`limits_live` (T2) in neue `tests/unit/core/conftest.py`; `PHASES`/`make_ctx` bleiben in `tests/unit/core/safety/conftest.py` — T11-Tests unter core/control sähen die Fixtures sonst nicht — falls falsch: eine Datei verschieben.
- F02 Ruling: T12 `test_write_publishes_json_value_when_connected`: Prädikat mit `bool(transports) and …` — run_until prüft vor dem ersten Adapterlauf (IndexError) — falls falsch: nichts.
- F03 Ruling: T13 `test_connected_reports_sources()` ohne Fixture, `StateStore(ManualClock(T0))` — es gibt keine `clock`-Fixture unter tests/unit/core/state — falls falsch: nichts.
- F04 Ruling: T13 `test_health_lists_actuators_and_watchdog` mit `runtime_env(state=GOOD_STATE)` — ohne Zustand ist der EVCS-Modus unbekannt und sperrt wallbox.evcs (Tabelle T8) — falls falsch: Test erwartet falsche Sperre.
- F05 Ruling: T17 fügt `watchdog_tripped` nach `problem_device_offline` ein und passt `test_entities_from_config` an — bestehender Test prüft die exakte Liste — falls falsch: andere Reihenfolge in HA.
- F06 Ruling: T9-Property-Tests erzeugen `present` typgerecht je Art (endliche Zahlen; bool für charging; None nur bei grid_setpoint = kein Override); T2/T4 unverändert — `present` kommt ab 2b aus Rücklesewerten der Aktoren mit festen Typen — falls falsch: falsch getypte Rücklesewerte könnten in 2b TypeError werfen; dann dort normalisieren.
- F07 Ruling: `cerbo-watchdog/ruff.toml` bekommt `[lint.pyupgrade] keep-runtime-typing = true` — py38 braucht Optional/List-Annotationen, sonst UP006/UP045 — falls falsch: nichts.
- F08 Ruling: T15-Testcode ruff-fest: `stored: StoredState | None = None` statt Default-Aufruf (B008); `shutil.which("bash")` und `# noqa: S603` beim subprocess-Aufruf — sonst scheitert `ruff check .` — falls falsch: nichts.
- F09 Ruling: T15 macht Skripte mit `chmod +x` ausführbar (git übernimmt den Modus beim `git add`) statt `git update-index --chmod=+x` — der Test prüft os.access auf der Platte — falls falsch: nichts.
- F10 Ruling: T13 `test_shutdown_sends_no_release` prüft `sent == [1]` statt `0 not in sent` — die alte Prüfung bestünde auch ohne jeden Heartbeat — falls falsch: Test zu streng bei anderem Takt.
- F11 Ruling: T16 `test_main_aborts_without_confirmation` behält den Mock und prüft `assert_not_called()` — main könnte die AssertionError fangen — falls falsch: nichts.
- F12 Ruling: T14 `test_compat.py` bekommt `test_sources_found` mit `len(SOURCES) >= 4` — leere Parametrisierung prüft sonst nichts — falls falsch: nichts.
- F13 Ruling: T10 `REASON_WATCHDOG = REASON_WATCHDOG_MODE` aus `core.safety.modes` — gleicher Text, eine Quelle — falls falsch: nichts.
- F14 Ruling: `render_report(findings, meta, *, title="Prüfprotokoll Teil 1")`; die Probe übergibt `"Prüfprotokoll Teil 2 – Watchdog-Probe"`; T16 ändert `tools/verify_checks.py` — die Überschrift wäre sonst falsch — falls falsch: Überschrift.
- F15 Ruling: T18-Doku-Befehle nach dem Muster aus docs/betrieb.md: `docker compose run --rm -T --entrypoint python ems-core -m bindaems.tools.watchdog_config --config /config/config.yaml > config.ini`; Probe mit `-v "$PWD/verification:/out" --entrypoint python … --out /out/<Datum>-watchdog-probe.md` — ENTRYPOINT ist `python -m bindaems.core` — falls falsch: Doku-Befehl.
- F16 Ruling: Global Constraint ergänzt: der Watchdog schreibt zusätzlich sein flüchtiges Log nach `/var/log/bindaems-watchdog/` (multilog, wie andere Venus-Dienste) — Standard unter daemontools — falls falsch: Log-Ort.
- F17 Ruling: T9 bleibt (dringend umgeht das Schreibbudget nur mit dem exakten Sicherwert); Präzisierung 4 wird angeglichen — schützt den Flash auch bei fehlerhaft „dringenden“ Folgen — falls falsch: ein dringender, nicht exakt sicherer persistenter Befehl wird bei erschöpftem Budget abgelehnt.
- F18 Ruling: Dringende Befehle umgehen das Zustands-Gate nur in SAFE (nicht OBSERVE, nicht START); T9 Schritt 3, Property-Orakel und Präzisierung 4 angepasst, neuer Test `test_urgent_toward_safe_rejected_in_observe` — Spec 7.2 „OBSERVE: nur lesen“, 8.3 Regel 8; SAFE muss den sicheren Zustand herstellen können (8.4/8.5) — falls falsch: nach einem Neustart reduziert der core in OBSERVE nicht; Ebene 0 (Peak Shaving) und Watchdog bleiben.
- F19 Ruling: Property-Orakel mit halboffenen Fenstern (Abstand < 3600 s, < 1 h, < 24 h) wie T5/T6; Budget-Folgen nur nicht dringend — konsistent mit `now − t < …` — falls falsch: Orakel zu streng/lasch.
- F20 Ruling: T4: fehlt `(victron, grid_setpoint)` in `present`, gilt `S0 = min(base_grid_setpoint_w, 0)` (vorsichtigste Annahme, ein Override ist nie negativ); neuer Test `test_setpoint_unknown_present_assumes_lowest` — falls falsch: Sollwert-Erhöhungen bei unbekanntem Override etwas zu stark begrenzt.
- F21 Ruling: T4: bei `current` ist die bindende Phase die mit kleinstem `h_p + m_p` (dieselbe Größe wie „erlaubt“); bestehende Erwartungen unverändert — falls falsch: Phasenname im Text.
- F22 Ruling: T11: „gleicher Inhalt“ wird nach dem Klemmen verglichen; eine erneut gesendete Absicht bekommt dieselbe Antwort (accepted bzw. clamped mit Details) ohne neue Speicherung; neuer Test `test_resubmitting_clamped_intent_is_idempotent` — idempotente Wiederholung für die app (2d) — falls falsch: Wiederholungen liefern 200 statt 409.
- F23 Ruling: T11 Step 2 Expected nur `ModuleNotFoundError` — falls falsch: nichts.
- F24 Ruling: T11 verdrahtet bereits minimal in `CoreRuntime`: `limits = HardLimits.from_config(cfg)`, `intents = IntentStore(cfg.core.data_dir / "intent.json", limits)` (im Konstruktor geladen), `intent()`, `submit_intent()`; `runtime_env` setzt `core.data_dir` auf `tmp_path / "core"`. T13 übernimmt das — sonst scheitert `mypy src` (runtime.py:318 übergibt CoreRuntime als CoreView) von T11 bis T13 — falls falsch: kleine Verschiebung zwischen T11 und T13.
- F25 Ruling: T15: `/Heartbeat` startet mit `None` (Test erwartet None) — velib-SetValue ruft den Callback bei gleichem Wert nicht auf; nach Neustart „scharf“ würde eine erste Freigabe 0 verschluckt und der Watchdog fälschlich auslösen — falls falsch: nichts.
- F26 Ruling: T16-Probe-Test mit größeren Abständen: Simulator `timeout_s=0.8`, `rearm_s=0.15`, `gap_s=0.5`; FAST `heartbeat_s=0.05, answer_wait_s=1.0, arm_wait_s=2.0, timeout_s=0.8, trip_slack_s=1.0, rearm_wait_s=2.0, release_wait_s=1.0` — 0,12 s Lücke ist auf belasteter CI zu knapp — falls falsch: ~1 s längere Testdauer.
- F27 Ruling: T17 E2E-Befehl in einer Subshell mit absolutem FONTCONFIG_FILE — falls falsch: nichts.
- F28 Ruling: Files-Listen ergänzt (T1 limits.json, T13 system.json, T15 tests/unit/cerbo_watchdog/fakes.py) — falls falsch: nichts.
- F29 Ruling: Global Constraints: `write_budget.json` entsteht ab 2b, wenn Aktoren die SafetyLayer aufrufen; 2a legt nur Format und Klasse fest — kein 2a-Task verdrahtet das Budget — falls falsch: nichts.
- Task 2: Ruling: `toward_safe` folgt der Tabelle; ein Sicherwert-Strom über dem bekannten Ist-Strom ist nicht „Richtung sicher“ (Präzisierung 4 „oder ein Sicherwert“ meint nur nicht erhöhende Sicherwerte) — der sichere Zustand erhöht nie einen Strom — falls falsch: 2b muss im sicheren Zustand Erhöhungen auf den Sicherwert auslassen (gewollt).
- Task 3: Ruling: `check_range` klemmt die Netz-Sollwert-Obergrenze bei negativem Budget auf 0 (`max(ceiling, 0.0)`) — Spec 8.3 Regel 2 „nie negativ“ und 7.7 (kein Akku ins Netz) gehen vor der Budget-Obergrenze — falls falsch: 2c kann den Akku nicht zum Einspeisen zwingen, um ein Budget zu halten (gewollt).
- Task 3: Ruling: zusätzliche 26 Rand-/Texttests bleiben (Mutationen 25 → 0 überlebend); Brief-Erwartung „20 passed“ gilt für die Brief-Tests — kostet nur Laufzeit.
- Task 3: Ruling: `range.integer` zeigt den abgerundeten Wert mit `de()` statt `{n}` — Global Constraint „Zahlen in Begründungstexten über de()“; identisch für n ≥ 0 — falls falsch: nichts.
- Task 3: Ruling: T9 macht `authorize` robust gegen riesige ints: `limits._number` (Task 2) fängt `OverflowError` wie `ranges._finite_number` ab, mit Test — sonst wirft `toward_safe` für dringende Befehle mit Wert > 1,8e308 — falls falsch: eine Zeile mehr.
- Task 4: Ruling: Regel 4 `current`: bei unbekanntem Ist-Strom gilt ein Wert ≤ `safe_a` nicht als Erhöhung (nur Wert > `safe_a`) — `safe_a` ist per Definition sicher (8.5, der Watchdog setzt ihn blind), `toward_safe` nutzt dieselbe Grenze, und Spec 8.4 verlangt bei veralteten Cerbo-Daten die Wallboxen auf den sicheren Strom; sonst lehnt Regel 4 genau diesen Befehl ab — falls falsch: bei sicherem Strom > Mindeststrom kann eine Wallbox mit unbekanntem Ist-Strom um bis zu (safe_a − min_a) steigen. Vor dem Review an den Implementierer zurück.
- Task 4: Ruling: bindende Phase bei `charging` = kleinstes `h_p + m_p` (wie die Regel für `current`) — nennt die Phase, die tatsächlich überläuft; gleich für m_p = 0 — falls falsch: Phasenname im Text.
- Task 4: Ruling: Zusatzverhalten bleibt (`value.invalid` bei falschem Typ, unbrauchbare Zahlen = unbekannt, unbekannte Wallbox des Teslas = L1–L3 ohne Messwerte) — schließt sicher, wirft nie — falls falsch: nichts.
- Task 4: Ruling: I2 wird mit einem gemeinsamen Helfer `finite_number` in `core/safety/types.py` behoben, den ranges.py, phases.py und `limits._number` (Overflow-Schutz) nutzen; damit erledigt sich die T3-Ruling-Aufgabe für T9 (`limits._number`/OverflowError) schon hier — ein Ort für die Zahlenregel — falls falsch: kleine Verschiebung zwischen Tasks.
- Task 5: Ruling: alte Einträge werden in `record` verworfen, nicht in `check` (Abweichung vom Hinweis in Step 3) — `check` bleibt ohne Seiteneffekt, Ergebnis für monotone Uhr identisch — falls falsch: nichts.
- Task 6: Ruling: Zusatzverhalten bleibt (strenges Dateiformat nur mit `writes`, ISO-Zeiten mit Zone, `record` lehnt naive Zeiten ab und speichert UTC, `check()` beendet die Ablehnungsphase, eigene Log-Meldung für Schreibfehler) — schließt sicher — falls falsch: nichts.
- Task 6: Ruling: 61 zusätzliche Randfalltests bleiben (Brief erwartet „10 passed“ für die Brief-Tests) — falls falsch: nur Laufzeit.
- Task 7: Ruling: Sprungerkennung bezieht sich auf die Auswertungszeit der letzten gültigen SOC-Beobachtung (InputCheck merkt sich Wert und das `now`, zu dem er zuletzt mit Qualität OK gesehen wurde), nicht auf `Reading.ts` — mit suppress-republish bleibt `Reading.ts` auf der letzten Änderung, der Wert war aber bei jeder Auswertung gültig; sonst wäre ein Sprung nach einem Plateau > 60 s unsichtbar — falls falsch: nach einer Ausfallzeit < 60 s mit echter Änderung > 5 pp (bei 96 kWh physikalisch kaum möglich) 60 s Sperre. Vor dem Review an den Implementierer zurück.
- Task 7: Ruling: zusätzliche Randfalltests bleiben (67 statt 6) — falls falsch: nur Laufzeit.
- Task 8: Ruling: zusätzliche Tests bleiben (44 statt 12) — falls falsch: nur Laufzeit.
- Task 8: Ruling: die Freigabe einer Sperre „durch Quittierung“ (Spec 7.9) kommt mit der Bedienung in 2d; in 2a lösen Sperren nur automatisch nach `lock_release_s` ohne Ursache — 2a hat außer PUT /v1/intent keinen Bedienweg — falls falsch: eine Sperre mit flackernder Ursache lässt sich in 2a nicht von Hand lösen.
- Task 9: Ruling: Laufzeit der Property-Tests (~24 s, Suite ~50 s) wird akzeptiert; das Budget-Fenster von 24 h braucht echte Folgen — falls falsch: CI je Lauf ~25 s langsamer.
- Task 9: Ruling: Property-Tests stärker als der Brief (mehrere Befehle je Fall, Gewichtung, Vorgeschichte, Folgen prüfen auch „genau das Erlaubte“, riesige ints) bleiben — sie verschärfen nur — falls falsch: nichts.
- Task 9: Ruling: dry_run auch bei frühen Ablehnungen gemeldet; bei dringenden Befehlen fällt ein nicht zählendes Totband-Finding weg; die Budget-Ausnahme vergleicht den Wert nach den Regeln 1–4 mit dem Sicherwert — Brief schweigt, alle getestet — falls falsch: nur Darstellung bzw. strengere Ausnahme.
- Task 9: Ruling: `urgent_ok` verlangt `toward_safe` für den Rohwert UND für den Wert nach den Regeln 1–3 (check_range ist rein und darf früh berechnet werden; lehnt er ab, ist urgent_ok falsch); das Orakel von test_gate_lock_inputs_and_dry_run verlangt `safer` für Rohwert und gesendeten Wert — sonst trägt ein „dringender“ Befehl die Umgehungen, obwohl der gesendete Wert nicht sicherer ist — falls falsch: ein dringender Befehl mit unzulässigem Rohwert wird normal geprüft (gewollt).
- Task 9: Ruling (T4-Querbezug aus dem Review): Start-Prüfung bei `charging` nutzt `I_c = max(bekannter Ist-Strom, min_a)`, sonst `max_a` — ein startendes Auto zieht mindestens den Mindeststrom; ein Adapter, der im Stillstand 0 A meldet, machte die Prüfung sonst trivial — falls falsch: Starts werden bei knappem Spielraum etwas früher abgelehnt.
- Task 10: Ruling: START mit Regelbetrieb und offenem Gate → OBSERVE mit Gründen `()` (nicht `degraded_reasons`) — „ohne Weiterschalten“: es fehlt nichts, der nächste Zyklus schaltet weiter; praktisch unerreichbar (Gate in den ersten 30 s zu) — falls falsch: ein Zyklus lang leere statt degradierter Gründe.
- Task 10: Ruling: `O = OperatingState` im Brief-Test bekommt `# noqa: E741` (ruff-Gate) — falls falsch: nichts.
- Task 10: Ruling: zusätzliche Tests bleiben (44 statt 11) — falls falsch: nur Laufzeit.
- Task 11: Ruling: `IntentStore.submit`/`load` laufen unter einem `threading.Lock` (PUT läuft im Threadpool von FastAPI, `write_json_atomic` verträgt nur einen Schreiber je Datei); drei Verdrahtungstests in tests/integration/test_runtime.py bleiben — falls falsch: nichts.
- Task 11: Ruling: `load()` klemmt nicht neu und prüft keine Aktor-IDs — `effective_modes` und `watchdog_required` prüfen `live_allowed` ohnehin bei jeder Auswertung, unbekannte IDs werden dort ignoriert — falls falsch: die UI zeigt „live angefordert“ für einen inzwischen nicht mehr freigegebenen Aktor (wirksam Dry-Run mit Grund).
- Task 12: Ruling: Abbau der Sitzung ignoriert Ausnahmen der Hilfstasks (`suppress(CancelledError, Exception)` wie beim HA-Adapter) — sonst hielt `run()` einen Abbruch für einen Verbindungsfehler und verband sich endlos neu — falls falsch: ein gestorbener Sender fällt erst mit dem Ende des Streams auf (dann löst der Watchdog aus; sicher).
- Task 13: Ruling: offene Punkte des Briefs wie umgesetzt (Sperrgründe in Katalogreihenfolge; `health()` zeigt den Stand des letzten Zyklus und ruft nie `locks.active()`; fehlendes kritisches Signal zählt als „Cerbo-Daten veraltet“; `watchdog_to_json` in api/app.py); 25 Zusatztests, davon 7 in test_api.py — falls falsch: nichts.
- Task 13: Ruling: `core/__main__.py` (Startlog `mode="OBSERVE"`, Text „Phase 1a: nur beobachten“) korrigiert Task 18 — nicht in der Dateiliste von T13 — falls falsch: irreführende Startmeldung bis T18.
- Task 13: Ruling: Plausibilitätstexte aus Phase 1 („Netzspannung L1 unplausibel: 300.0 V“) bleiben in 2a unverändert, obwohl sie jetzt auch in `health.inputs.problems` erscheinen — die de()-Regel gilt für die neuen Begründungstexte von 2a; Umstellung der Phase-1-Texte ist eine eigene Änderung — falls falsch: Dezimalpunkt in einigen Eingangsproblemen der UI.
- Task 14: Ruling: zusätzliche ConfigError-Fälle mit eigenen deutschen Texten (Syntax, unlesbar, enabled, instance, tolerance, leeres state_file, NaN/Infinity) und strenges `read_state` (jede unerwartete Form → scharf starten) bleiben — schließt sicher — falls falsch: ein ungewöhnlich formatiertes state.json lässt den Watchdog scharf starten (gewollt).
- Task 14: Ruling: Heartbeat 0 setzt Lauf und `last_seen` in jedem Zustand zurück (Zustandswechsel nur aus ARMED/TRIPPED); negative Werte werden ignoriert; `last_seen = now` bei der Konstruktion — falls falsch: nichts Sicherheitsrelevantes.
- Task 14: Ruling (für T15): install.sh prüft neben python3 ≥ 3.8 auch, dass die nötigen Module importierbar sind (`python3 -c "import configparser, dataclasses, json, contextlib, dbus; from gi.repository import GLib"`), und der Dienst loggt beim Start die geladenen Aktionen (Namen und Anzahl) — Venus OS kann Teile der Standardbibliothek weglassen; ein vertippter Aktionsabschnitt würde sonst still verschwinden — falls falsch: eine Zeile mehr im Installationsskript und im Log.
- Task 15: Ruling: bei ungültiger config.ini loggt der Dienst den Fehler und wartet 60 s, bevor er mit Code 2 endet — sonst startet daemontools ihn jede Sekunde neu (CPU und Log auf dem Cerbo) — falls falsch: Fehler erscheint nur einmal je Minute im Log.
- Task 15: Ruling: install.sh überträgt die Dateien per `tar … | ssh … tar -x` statt `scp -r` — moderne scp-Clients nutzen SFTP, das der Dropbear-Server auf Venus OS nicht sicher anbietet; tar über ssh braucht nur BusyBox-tar — falls falsch: nichts (gleiche Dateien, gleiche Rechte).
- Task 15: Ruling: Zusatzentscheidungen bleiben (SetValue mit Variant-Signatur und 5 s Timeout; abgelehnter Schreibzugriff = Fehlversuch; stumme Dienste werden bei find_service übersprungen; Prüfung auf velib; rc.local ohne Endmarke → Abbruch ohne Änderung, Warnung bei `exit`-Zeile; Deinstallation mit `svc -dx`) — falls falsch: nichts Sicherheitsrelevantes.
- Task 15: Ruling (Re-Grade): (1) fehlgeschlagenes Speichern von state.json wird bei jedem Tick wiederholt, bis es gelingt (ohne zu werfen) — sonst startet der Watchdog nach einem Neustart nicht scharf, obwohl er es war (Review-Focus 2) — falls falsch: ein Schreibversuch je Sekunde bei voller Platte.
- Task 15: Ruling: (2) der Aktionslauf endet, sobald der Watchdog TRIPPED verlässt (Freigabe 0 oder wieder scharf): keine weiteren Schreibversuche, offene Ergebnisse bleiben „offen“, `/LastTripFailures` wird mit dem bisherigen Stand veröffentlicht, deutscher Logeintrag — README-Zusage „schreibt nichts, solange er wartet oder scharf ist“ gilt damit für jede Konfiguration — falls falsch: eine bei der Wiederkehr des core noch laufende Aktion bleibt unvollständig (der core regelt dann selbst).
- Task 15: Ruling: (3) bei jeder neuen Auslösung wird `/LastTripFailures` sofort mit 0 veröffentlicht — sonst zeigt der Alarm des core die Fehlerzahl der vorigen Auslösung — falls falsch: nichts.
- Task 15: Ruling: (4) install.sh prüft den rc.local-Block (Endmarke) als Erstes, vor jeder Änderung, bei Installation und Deinstallation — sonst bleibt bei kaputtem Block ein halb deinstallierter Zustand, der beim nächsten Boot den Dienst wieder verlinkt — falls falsch: nichts.
- Task 16: Ruling: Entscheidungen, zu denen der Brief schweigt, bleiben (nur „j“ bestätigt; Exit 2 bei ungültiger Config oder Secrets; Verbindungsfehler als fail-Zeile; unbekannter Fehlerzähler → warn; nicht schreibbarer Berichtspfad → Bericht auf stdout, Exit 1) — falls falsch: nichts Sicherheitsrelevantes.
- Task 16: Ruling: Fix-Runde 1 umfasst das Important plus, nach Wirkung hochgestuft: (M1) bei Abbruch/Verbindungsverlust gehen die bis dahin gesammelten Findings nicht verloren (gekoppelt an denselben Fehlerpfad; das Protokoll ist der Nachweis für Teil 2); (M2) `render_config` lehnt 2,5 × heartbeat_s ≥ timeout_s mit ConfigError ab (sonst entsteht still eine config.ini, die der Watchdog verweigert); (M3) je EVCS ohne GX-Instanz eine Warnzeile auf stderr, und ein nicht schreibbares `--out` endet mit Meldung und Exit 2 statt Traceback — falls falsch: etwas mehr Code in zwei Werkzeugen.
- Task 17: Ruling: kleine Abweichungen vom Brief-Wortlaut bleiben (Betriebsgründe als eigene Zeile über die volle Breite, „nie“ nur bei trip_count 0 sonst Strich, „Keine Daten vom core“ ohne Bericht; Schema-Tests in contract.test.ts) — bessere Darstellung bei 360 px und keine falsche Aussage — falls falsch: nur Darstellung.
- Task 17: Ruling: strenge UI-Schemas bleiben; core und app werden immer gemeinsam aus demselben Stand ausgerollt (gleiche Compose-Datei und Image-Tags) — eine abweichende Version zeigte sonst nur „Unerwartete Antwort vom Server“ auf der System-Seite — falls falsch: System-Seite bei gemischten Versionen leer.
- Task 18: Ruling: zusätzliche Spec-Nachträge (5.3, 7.9, 8.2, 11.1, 16 als „Stand Plan 2a“) und drei zusätzliche Doku-Tests bleiben — falls falsch: streichbar ohne Folgen.
- Task 18: Ruling: der CI-Schritt „Shellcheck“ prüft zusätzlich cerbo-watchdog/install.sh, service/run und service/log/run, und der Kommentar „Phase 1a: nur beobachten“ in deploy/Dockerfile.core wird angepasst — vor dem Review an den Implementierer zurück — falls falsch: nichts.
- Task 18: Ruling: das Abdeckungs-Gate misst wie im Brief nur das Paket `bindaems`; das Watchdog-Paket hat eigene Tests (220), aber kein Abdeckungs-Gate (seine D-Bus-Teile laufen in der CI nicht) — falls falsch: Abdeckungsrückgang im Watchdog-Paket fiele der CI nicht auf.
- Task 18: Ruling: zusätzliche Doku-Änderungen außerhalb der Dateiliste (docs/verification/README.md Abschnitt „Teil 2 – Watchdog-Probe“, abnahme-phase-1.md §1, §4.2, §4.3) bleiben — vom Reviewer gegen den Code geprüft und nötig — falls falsch: nichts.
- Final: Ruling: Fix-Welle umfasst I1, I2, I3, M1 (Images zuerst), M2 (Doku nach der Probe: Min-SOC/Entladegrenze prüfen, nach manueller Freigabe /State 0 prüfen), M3 (install.sh bricht bei einer exit-Zeile auf oberster Ebene in rc.local vor jeder Änderung ab – Abkehr vom T15-Ruling „nur Warnung“, weil der Watchdog sonst nach dem nächsten Neustart fehlt), M5 (Quertests für Zustandscodes und bindaems-Topics zwischen core, Watchdog und Probe), M7 (Doku: vor dem Entzug von live_allowed den Aktor auf Dry-Run stellen), die Spec-Reste (19.3, 17.2-6, 15) und in docs/uebergabe.md die 2b-Voraussetzungen inkl. I4 als Blocker vor live — Wirkung an der Anlage (falsche Abnahme, abgeschalteter Watchdog, Spec-Widersprüche für 2b) — falls falsch: etwas mehr Arbeit vor dem Merge.
- Final: Ruling: I4 wird nicht in 2a gelöst (2a schreibt außer dem Heartbeat nichts, es gibt nichts wiederherzustellen), sondern als Blocker vor jeder Live-Schaltung in 2b festgehalten; der Einwand des Reviewers gegen den „falls falsch“-Satz von F18 und Präzisierung 2 ist berechtigt und geht in dieselbe 2b-Entscheidung — falls falsch: 2b muss das Thema zuerst lösen (so vorgesehen).
- Final: Ruling: M4 (UI-Texte abhängig von `required`), M6 (Watchdog nur als scharf werten, wenn der eigene Heartbeat zurückkommt; /TimeoutSeconds abgleichen) und M8 (shellcheck der heredoc-Teile) werden aufgeschoben (2d bzw. 2b) — keine Wirkung in 2a — falls falsch: Darstellung bzw. Erkennung eines hängenden Watchdogs erst in 2b.
- Final: parked — Tabelle in docs/verification/README.md:126-130 durch den neuen Absatz unterbrochen (letzte Zeile „Freigabe ohne Auslösung“ fällt aus der Tabelle) — Ruling: kleiner Darstellungsfehler, beim nächsten Doku-Durchgang beheben — falls falsch: ein Prüfschritt erscheint als Fließtext statt als Tabellenzeile.
- Final: parked — Probe liest `/LastTripFailures` nach der festen 45-s-Pause statt nach „Wieder scharf“; bei hängenden D-Bus-Aufrufen kann ein vorläufiges 0/OK entstehen — Ruling: in 2b das Finding nach „Wieder scharf“ bilden (dann ist der Wert immer endgültig); bis dahin deckt die vorgeschriebene Log-Prüfung nach jeder Probe es ab — falls falsch: falsches „OK“ nur bei hängendem D-Bus, das Log zeigt es.
- Final: parked — betrieb.md:659-660/719-721 „Warte, bis der Watchdog ‚wartet‘ zeigt“ gilt nur, wenn kein anderer Victron-/EVCS-Aktor live angefordert bleibt — Ruling: Formulierung beim nächsten Doku-Durchgang ergänzen — falls falsch: Betreiber wartet vergeblich, solange ein anderer Aktor live ist.

## Geparkte Befunde nach dem Gesamtreview (3)

- Final: parked — Tabelle in docs/verification/README.md:126-130 durch den neuen Absatz unterbrochen (letzte Zeile „Freigabe ohne Auslösung“ fällt aus der Tabelle) — Ruling: kleiner Darstellungsfehler, beim nächsten Doku-Durchgang beheben — falls falsch: ein Prüfschritt erscheint als Fließtext statt als Tabellenzeile.
- Final: parked — Probe liest `/LastTripFailures` nach der festen 45-s-Pause statt nach „Wieder scharf“; bei hängenden D-Bus-Aufrufen kann ein vorläufiges 0/OK entstehen — Ruling: in 2b das Finding nach „Wieder scharf“ bilden (dann ist der Wert immer endgültig); bis dahin deckt die vorgeschriebene Log-Prüfung nach jeder Probe es ab — falls falsch: falsches „OK“ nur bei hängendem D-Bus, das Log zeigt es.
- Final: parked — betrieb.md:659-660/719-721 „Warte, bis der Watchdog ‚wartet‘ zeigt“ gilt nur, wenn kein anderer Victron-/EVCS-Aktor live angefordert bleibt — Ruling: Formulierung beim nächsten Doku-Durchgang ergänzen — falls falsch: Betreiber wartet vergeblich, solange ein anderer Aktor live ist.

## Hinweise für die Pläne 2b bis 2d (7)

- Task 2: note für 2c: `grid_budget_w` darf nie NaN/±inf sein (Erzeuger 15-min-Wächter).
- Task 4: note für 2b: `SafetyContext.wallbox_phase_a` muss nach Netzphasen geschlüsselt sein (Erzeuger wendet `phase_map` an); `present[(aktor, current)]` ist der Sollstrom, nicht der Messstrom.
- Task 5: note für 2b: Zustand des RateLimiters nur im Speicher (Neustart vergisst das Tesla-Stundenfenster) und `ctx.now` kommt von der Wanduhr — in 2b entscheiden: monoton verankerte Uhr für die Safety-Schicht und ggf. Persistenz/Anlaufschonfrist für Tesla.
- Task 6: note für 2b: Einträge in der Zukunft zählen (nach korrigierter Uhr bleiben persistente Schreibzugriffe gesperrt, bis die Uhr aufholt) – mit der Uhr-Entscheidung in 2b klären.
- Task 9: note für 2b: `ctx.modes` muss `ActuatorMode`-Werte enthalten (ein String „live“ bleibt still Dry-Run); `authorize` verändert Rate-Limiter und Budget – genau einmal je betrachtetem Befehl aufrufen.
- Task 12: note für 2b: `write()` = eingereiht, nicht zugestellt; eine Freigabe 0 kurz vor Verbindungsabbruch geht verloren und der Watchdog löst 90 s später unnötig aus — in 2b „freigegeben, aber Watchdog weiter scharf → 0 erneut senden“ erwägen; Heartbeat pausiert bei rückspringender Uhr (Uhr-Entscheidung 2b).
- Task 13: note für 2d: der LiveFeed der app ignoriert `mode_change`; in 2a reicht die Health-Abfrage alle 10 s.

## Aufgeschobene kleine Befunde (78)

Einordnung durch den Gesamtreview: keiner davon blockiert den Merge; die meisten passen zu 2b
(vor dem Verdrahten der SafetyLayer) oder einem späteren Aufräumen. Die Liste in
`docs/uebergabe.md` („Vor einer Live-Schaltung in 2b zu klären“) nennt die wichtigsten davon.

- Task 1: minor (deferred): test_safety_defaults prüft kaum echte Defaults (Beispiel-YAML setzt die Abschnitte); Grenzwerte (per_hour == start_stop, lock_release_s 3600) nicht positiv getestet.
- Task 1: minor (deferred): `grid.max_import_kw` fehlt als kommentierte Zeile in deploy/config.example.yaml.
- Task 2: minor (deferred): `grid_setpoint_max` mit nicht endlichem Budget: NaN wird ignoriert, −inf wird Obergrenze (limits.py:149-152) – nicht endliches Budget als 0 behandeln.
- Task 2: minor (deferred): Arten in `toward_safe` mit `is` verglichen (limits.py:197ff); String-Art ergibt nie „sicher“ – `==` nutzen.
- Task 2: minor (deferred): `ActuatorSpec(...)` mit positionalen Booleans (limits.py:104-125) – Schlüsselwortargumente.
- Task 2: minor (deferred): Docstring „nur lesen“, Mappings aber normale dicts (limits.py:1-5, 80-85).
- Task 2: minor (deferred): `safe_commands` liefert für unbekannte Aktoren () statt Fehler (limits.py:170-172).
- Task 2: minor (deferred): Testlücken: Gleichstandsregel `grid_setpoint_max`, Min-SOC-Untergrenze in toward_safe, `Verdict.send/sendable`, `limits_live`.
- Task 2: minor (deferred): test_de_formats_german liegt unter tests/unit/shared, importiert aber core.safety.types (plan-vorgegeben).
- Task 3: minor (deferred): int-Budget ergibt int-Wert in `Step.value` (ranges.py:101/106) – `float(ceiling)`.
- Task 3: minor (deferred, Hinweis 2b/2c): berechnete Ströme wie 9,999… A ergeben den Text „10 A → 9 A“; Aufrufer runden vorher auf ganze Ampere.
- Task 4: minor (deferred): `base_grid_setpoint_w` läuft nicht durch `_finite` (phases.py:176/182; +inf → fail-open, NaN → irreführender Text, riesiger int → OverflowError).
- Task 4: minor (deferred): Aktor außerhalb `limits.actuators`/`currents` wirft KeyError (dokumentierte Vorbedingung, Task 9 prüft vorher).
- Task 4: minor (deferred): `removeprefix("wallbox.")` dupliziert das ID-Format von `wallbox_actuator()` (phases.py:258).
- Task 4: minor (deferred): `limits._number` behält NaN/±inf als Zahl (eigener OverflowError-Schutz statt `finite_number`); kein Test pinnt das — ein späterer Tausch gegen `finite_number` bliebe grün.
- Task 5: minor (deferred): Stundenfenster verlieren Einträge bei rückwärts springender Uhr (rate.py:85-91; nach ≥1 h Vorwärtssprung und Korrektur das ganze Fenster); Docstring untertreibt.
- Task 5: minor (deferred): Rücksprung der Uhr sperrt den Schlüssel für die Dauer des Sprungs (fail-safe, nur Verfügbarkeit; rate.py:61/99).
- Task 6: minor (deferred): `test_any_iso_time_with_an_offset_is_accepted` erkennt ignorierten Offset nicht (per_hour=1 nutzen).
- Task 6: minor (deferred): `json.dumps` mit allow_nan=True widerspricht dem Docstring (files.py:31).
- Task 6: minor (deferred): kein fsync des Verzeichnisses nach os.replace (files.py:39).
- Task 7: minor (deferred): Status bei OK-Qualität mit nicht numerischem SOC (bool/str) nicht festgenagelt (test_inputs.py:352-359; Verbraucher phases.py prüft nach).
- Task 7: minor (deferred): `_track_soc` baut `Snapshot.ok` nach (inputs.py:114-119) – `finite_number(snap.ok(...))`.
- Task 7: minor (deferred): abgelaufene Haltezeit kann bei rückspringender Uhr wieder auftauchen (inputs.py:108; fail-safe, 2b-Uhr-Entscheidung).
- Task 8: minor (deferred): Report nennt drei Entscheidungen „festgenagelt“, die keinen Test haben (Reihenfolge der Schlüssel, rückwärts laufende Uhr hält die Sperre, unbekannte Intent-IDs ignoriert).
- Task 8: minor (deferred): Docstring modes.py:1 zitiert „Spec 8.7, Regel 8“ – richtig „8.3 Regel 8 und 8.7“.
- Task 9: minor (deferred): keine Property-Folge schickt dringende Befehle durch Regel 5 und 6 (nur Unit-Tests pinnen Umgehung und Budget-Ausnahme).
- Task 9: minor (deferred): Laufzeit der Budget-Folge hängt an der fsync-Latenz der CI (≈9000 echte Schreibvorgänge).
- Task 9: minor (deferred): `invalid_value` formatiert `{value!r}`; ein int mit > 4300 Ziffern wirft ValueError (Python-Ziffernlimit), jetzt auf mehr Pfaden (layer.py:117, types.py:70-72) – begrenzte Darstellung.
- Task 9: minor (deferred): `toward_safe` vergleicht Arten mit `is` (limits.py:203-228; schon aus Task 2 aufgeschoben).
- Task 10: minor (deferred): Evidenzdateien task-10-green*.txt zeigen 43 statt 44 (vor der letzten Teständerung erzeugt).
- Task 11: minor (deferred): Wiederholung nach fehlgeschlagenem Speichern verschweigt den Hinweis „nicht gespeichert“ (intent.py:142-146).
- Task 11: minor (deferred): gespeicherte Absicht teilt das `actuators`-dict mit dem Aufrufer (intent.py:160) – Kopie anlegen.
- Task 11: minor (deferred): Neustart + erneutes Senden (F22-Fall) nicht durch einen Test festgenagelt.
- Task 11: minor (deferred): zwei Tests in test_runtime.py bauen CoreRuntime ohne umgelenktes data_dir (nur lesend).
- Task 11: minor (deferred): PUT /v1/intent nimmt `{"version": true}`, `"3"` und `"control": "yes"` an (StrictInt/StrictBool).
- Task 11: minor (deferred): `FakeView` in tests/integration/test_core_link.py ohne intent()/submit_intent() (für 2d ergänzen).
- Task 11: minor (deferred): Docstring `_parse` nennt `accepted_at`, das weder gelesen noch geprüft wird.
- Task 12: minor (deferred): Abbau der Sitzung kann einen Abbruch verlieren, wenn er während des Wartens auf einen Hilfstask eintrifft (victron_mqtt.py:233-237, schon vor 2a) – `asyncio.gather(*tasks, return_exceptions=True)` plus Regressionstest.
- Task 12: minor (deferred, für 2b wichtig): ein gestorbener Sender bleibt unbemerkt und erholt sich nicht (aiomqtt-Timeout trennt nicht); `write` liefert weiter True – Session bei beendetem Hilfstask beenden oder `write` → False und Fehler loggen.
- Task 12: minor (deferred): `HeartbeatClient(first=…)` ohne Prüfung auf 1…HEARTBEAT_MAX.
- Task 13: minor (deferred): `health()` kann Felder aus zwei Zyklen mischen (runtime.py:271-288) – ein eingefrorener Datensatz je Zyklus.
- Task 13: minor (deferred): stabiles `since` friert für `watchdog.tripped` den Entdeckungszeitpunkt ein, wenn `last_trip_time` später kommt – `min(vorher, neu)` übernehmen.
- Task 13: minor (deferred): Review-Focus 1 auf Laufzeitebene nur teilweise festgenagelt (0 genau einmal über mehrere Zyklen; Neustart mit live EVCS → erster Zyklus sendet 1, keine 0).
- Task 13: minor (deferred): doppelte since-Übernahme in `_check_counters` (runtime.py:438) neben der allgemeinen Regel.
- Task 13: minor (deferred): test_runtime.py:128/134 und test_entrypoint lesen weiter `/data` (nur lesend).
- Task 13: minor (deferred): Attribut `_inputs` (InputCheck) vs. lokale `inputs` (OperatingInputs) in runtime.py:355/365 – umbenennen.
- Task 13: minor (deferred, vor 2a): vitest-Hinweis „jsdom was created 51 times“ im UI-Testlauf.
- Task 14: minor (deferred): unbekannte/vertippte Abschnittsköpfe wie `[actions:x]` werden still verworfen (config.py:100-104).
- Task 14: minor (deferred): kein fsync des Verzeichnisses nach os.replace (state_file.py:72).
- Task 14: minor (deferred): RecursionError aus json.loads entkommt in config.py:122-123.
- Task 15: minor (deferred): Optionen `timeout=CALL_TIMEOUT_S` und `introspect=False` in DbusItemAccess nicht getestet (test_service.py:376-390).
- Task 15: minor (deferred): README:47-48 Formulierung „Ein gescheiterter Versuch folgt nach retry_s erneut“.
- Task 16: minor (deferred): Prompt `Fortfahren? [j/N]` in keinem Test geprüft; SimulatedCerbo antwortet auf den Keepalive unabhängig vom Abonnement (Reihenfolge Subscribe/Keepalive ungeprüft).
- Task 16: minor (deferred): `_secrets_error` (4. Kopie) und `_discover_portal` (3. Kopie) – gemeinsame Helfer.
- Task 16: minor (deferred): Schritte 2, 3, 5 können ohne Zustandswechsel „ok“ werden, wenn der Startzustand schon dem Ziel entspricht.
- Task 16: minor (deferred): Freigabeversuch auch, wenn schon der erste Heartbeat nicht beim Broker ankam (Zähler vor dem Publish erhöht; konservativ).
- Task 16: minor (deferred): Meldung „mit dem Heartbeat 0 freigegeben“ überzeichnet bei QoS 0 – „gesendet“.
- Task 16: minor (deferred): zweites Strg+C während der Freigabe unterdrückt den Hinweis „Achtung“.
- Task 16: minor (deferred): README/Docstring „im Bericht und auf der Konsole“ – bei Strg+C nur Konsole.
- Task 16: minor (deferred): nur SIGINT behandelt; SIGTERM/SIGHUP/docker stop beenden ohne Freigabe.
- Task 17: minor (deferred): StatusDot-Label des Watchdogs wiederholt den sichtbaren Text statt der Schwere (+page.svelte:297-298).
- Task 17: minor (deferred): `disabled` bei `required` nur als Warnung, der core meldet ERROR (+page.svelte:62-73, plan-vorgegeben).
- Task 17: minor (deferred): „Eingangsdaten“ zeigt bei ok=false ohne Probleme eine leere Zelle (+page.svelte:166-177).
- Task 17: minor (deferred): HA-Sensor publiziert OFF für jeden Zustand außer tripped/unknown (auch unbekannte künftige Zustände) – nur waiting/armed/disabled → OFF.
- Task 17: minor (deferred): bis ~25 s veraltete Aktor-/Watchdog-Karten nach Verbindungsabbruch zum core ohne Hinweis.
- Task 17: minor (deferred): system/+page.svelte auf ~513 Zeilen gewachsen – Karten bei weiterem Wachstum auslagern.
- Task 17: minor (deferred, vor 2a): StarletteDeprecationWarning im E2E-Webserver.
- Task 18: minor (deferred): betrieb.md §14 nennt nicht, dass zuerst das 2a-Image ausgerollt sein muss (`docker compose pull && up -d`), ebenso uebergabe.md „Als Nächstes an der Anlage“.
- Task 18: minor (deferred): Spec-Reste widersprechen 8.6 (19.3 und 17.2-6: EVCS-Weg offen; 15: „Bei Failover greift der Watchdog“ ohne Einschränkung).
- Task 18: minor (deferred): uebergabe.md:31 „Property-Tests der Safety-Schicht 7“ – es sind 5.
- Task 18: minor (deferred): abnahme-phase-1.md:121-122 „niemand kann live schalten“ zu absolut.
- Task 18: minor (deferred): uebergabe.md:199-202 nennt „Zeit als Argument, Dateien nur über persistence“ als Gate, gegated ist nur der Importteil.
- Task 18: minor (deferred): Test der Startmeldung prüft nur „nicht OBSERVE“, nicht „liest den Zustand der Laufzeit“.
- Task 18: minor (deferred): Doku-Test prüft `--entrypoint python` nur in betrieb.md; Link-Test scannt Codeblöcke mit.
- Task 18: minor (deferred): Watchdog-Paket ohne Abdeckungs-Gate (ruled) – in 2b mit den hub4-Aktionen neu bewerten.
- Final: minor (deferred): M4 UI – Heartbeat-Text „aus (kein Aktor live)“ auch bei fehlschlagendem Senden; „wartet“ mit gelbem Punkt im Normalbetrieb von 2a.
- Final: minor (deferred): M6 core nutzt watchdog.heartbeat und watchdog.timeout_s nicht (hängender Watchdog bleibt „scharf“, veraltetes Timeout unbemerkt) – 2b.
- Final: minor (deferred): M8 heredoc-Code in install.sh nicht mit shellcheck geprüft.

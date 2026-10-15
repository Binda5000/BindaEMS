# Phase 2d – Bedienung in UI und HA: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Betreiber bedient BindaEMS über die app statt über `PUT /v1/intent`, und EVCS und Tesla sind bereit für die gemeinsame Live-Schaltung:
- die app sendet die Absicht: Regelbetrieb, Live-Schalter mit Bestätigung, Lademodi, Boost, Zuordnung, Gast-Modus und Vorrang
- Quittierung von Sperren; eine fremde Änderung steht im Entscheidungslog
- Entscheidungslog in SQLite mit Markierungen für Grafana
- UI-Seiten „Laden“ und „Entscheidungen“, „Warum?“ auf der Übersicht, Live-Schalter und Quittierung auf der System-Seite
- Home Assistant: Selects, Switch und Buttons fürs Laden, Benachrichtigungen bei Alarmen
- die Tesla-Punkte vor der Live-Schaltung: Stundenbudget, Selbststart, Ansteckerkennung

**Architecture:**
- **core:** kleine Erweiterungen der bestehenden Teile:
  - `shared/decisions.py` und `core/runtime.py`: Entscheidung bei fremder Änderung; Health mit `watchdog.armed`, `actuators[].watchdog_needed` und `vehicles[].commands_hour`
  - `core/safety/locks.py`, `core/control/foreign.py`, `core/api/app.py`: Quittierung `POST /v1/locks/{actuator}/ack`
  - `core/control/charging.py`, `charging_tesla.py`: Stopp in „Aus“ dringend, Reserve im Stundenbudget, Selbststart
  - `core/adapters/tessie.py`: schneller abfragen, solange das Auto zu Hause ist, und sofort bei einem neuen Vorgang
- **app, neues Paket `bindaems.app.control`:**
  - `intent.py`: `IntentService` leitet jede Bedienung aus der gültigen Absicht ab und sendet sie an den core
  - `decisions.py`: `DecisionStore` (SQLite) und `DecisionCollector` (Stream, Rückstand, Aufräumen, Grafana)
  - `routes.py`: `/api/control/…` und `/api/decisions…`
- **app, HA:** `ha/entities.py` und `ha/publisher.py` bekommen Plattformen mit Befehlen, `ha/commands.py` führt sie aus, `ha/notify.py` sendet Benachrichtigungen über die WebSocket-API von Home Assistant.
- **UI:** Seiten `/laden` und `/entscheidungen`, Karte „Warum?“ auf der Übersicht, Bedienung auf der System-Seite; Bausteine in `ui/src/lib/charging/` und `ui/src/lib/decisions/`.

**Tech Stack:** wie Plan 2c-2b: Python 3.12 mit uv, pydantic v2, FastAPI, SQLAlchemy Core mit Alembic, httpx, websockets und aiomqtt; Tests mit pytest, hypothesis, respx und pytest-cov; ruff, mypy und import-linter. UI: SvelteKit 2 mit Svelte 5, valibot, Vitest mit @testing-library/svelte und Playwright.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, vor allem 5.3, 7.9, 7.10, 8.7, 11.3, 12, 13, 14 und 18.
- Verweise wie „Spec 8.7“ beziehen sich darauf, „Design 2c-1“ auf `docs/superpowers/specs/2026-10-12-phase-2c1-laderegelung-design.md`, „Design 2c-2“ auf `docs/superpowers/specs/2026-10-14-phase-2c2-akku-spitzen-design.md`.
- Die Absätze „Stand Plan …“ der Spec gehen dem älteren Text davor vor. Die Präzisierungen unten gehen beiden vor.
- Vorgänger ist Plan 2c-2b (`docs/superpowers/plans/2026-10-15-phase-2c2b-zuordnung.md`), umgesetzt und auf `main`. Seine Global Constraints und die der Pläne davor gelten weiter.
- Die Tesla-Punkte stehen in `docs/uebergabe.md` („Vor der Live-Schaltung von EVCS und Tesla“) und im Betriebshandbuch („Laderegelung“, „Hinweise für die Anlage“).

## Einordnung

Ergebnis von 2d:
- Der Dry-Run neben evcc läuft über die Bedienung: Regelbetrieb, Modi, Boost und Zuordnung im UI und in HA, die Entscheidungen auf der Seite „Entscheidungen“ und in Grafana.
- EVCS und Tesla können gemeinsam live gehen, sobald 2 Wochen Dry-Run gesichtet sind und die drei Prüfungen zur Zuordnung bestanden sind (Betriebshandbuch, „Hinweise für die Anlage“). Geschaltet wird mit dem Live-Schalter im UI (Spec 8.7).
- Victron geht wie bisher getrennt live (Design 2c-2, 11.1); auch dafür dient jetzt der Live-Schalter.

Stand an der Anlage laut Übergabe: Images vom Stand nach Plan 2b; die Pläne 2c-1, 2c-2a und 2c-2b sind nicht ausgerollt. Kein Aktor ist live. Plan 2d ändert daran nichts: Er baut die Bedienung, schaltet aber nichts an der Anlage.

## Vorgeschlagene Entscheidungen (zur Freigabe)

Diese Punkte entscheidet der Betreiber mit der Freigabe des Plans. Der Plan ist auf den Vorschlag geschrieben; eine andere Wahl ändert die genannten Tasks.

1. **Zuschnitt: ein Plan mit 18 Tasks.** Alternative: Teilung in 2d-1 (core-Grundlagen, app, UI: Tasks 1, 2, 7 bis 15, 18) und 2d-2 (Tesla und HA: Tasks 3 bis 6, 16, 17). Die Tasks hängen nur über die Schnittstellen zusammen, darum genügt ein Plan.
2. **Nicht in 2d** (Präzisierung 1): Ziel-SOC, Abfahrt und Modus „Geplant“ (Phase 4), Mehrkostenanzeige beim Boost, Schalter „Netzladen erlaubt“ und „Akku-Unterstützung erlaubt“, Zahl „Unterstützungsschwelle“, Taste „neu planen“ (Phase 3), die HA-Sensoren Ladeenergie, Fertig-um und Ziel erreichbar (Phase 4).
3. **Rollen** (Präzisierung 3): „Bedienen“ darf Regelbetrieb, Lademodi, Boost, Zuordnung, Gast-Modus und Vorrang ändern; nur „Admin“ schaltet live oder auf Dry-Run und quittiert Sperren. Home Assistant hat die Rechte von „Bedienen“.
4. **Live-Schalter** (Präzisierung 4): nur im UI, nur für Admins, mit erneuter Eingabe des Passworts und dem Häkchen „Prüfprotokoll Teil 2 für die gewählten Aktoren ist bestanden.“; bei EVCS oder Tesla dazu „evcc ist ganz abgeschaltet.“ Mehrere Aktoren in einem Zug. Zurück auf Dry-Run ohne Passwort, mit einfacher Bestätigung.
5. **Boost** (Präzisierung 5): eine Taste „Boost“, gültig bis zum Abstecken, höchstens 12 h (die Klemmung des core), und „Boost beenden“. Keine Wahl der Dauer.
6. **Tesla vor der Live-Schaltung** (Präzisierungen 9 bis 11):
   - Der Stopp im Modus „Aus“ ist dringend und umgeht damit die Stundenlimits; „Aus“ wirkt sofort (Punkt 1 der Liste).
   - Die Regelung hält 4 Befehle je Stunde frei (`control.tesla_reserve_commands`): Ist das Budget bis auf diese Reserve verbraucht, ändert sie den Strom des Tesla nur noch, wenn der Phasenwächter es verlangt (Punkt 1).
   - Lädt der Tesla 120 s gegen seinen Modus, sendet die Regelung „Laden aus“ erneut, höchstens alle 10 min (Punkt 2).
   - Tessie fragt ein abgestecktes Auto alle 60 s ab, solange es zu Hause ist, und sofort, wenn an einer Wallbox ein Vorgang beginnt (Punkt 3).
   - `vehicles.tesla.min_a`: Der Code bleibt bei 5 A (Design 7.2). Empfehlung für die `config.yaml` der Anlage: 6 A (Punkt 5); das entscheidet der Betreiber dort.
   - Netzausfall (Punkt 8): unverändert; ein Netzausfall ist selten, und die dringenden Befehle des sicheren Zustands umgehen die Limits schon.
7. **Entscheidungslog** (Präzisierung 7): 180 Tage in SQLite (`app.decisions_keep_days`), Markierungen für Grafana in InfluxDB (Retention Policy `raw`, 90 Tage).
8. **Benachrichtigungen** (Präzisierung 14): jeder Alarm mit Schwere „Fehler“, der 30 s besteht, als `persistent_notification` und, falls eingestellt, über einen `notify.*`-Dienst; verschwindet der Alarm, verschwindet die persistente Meldung. Dazu abgelehnte Befehle aus HA.

## Präzisierungen gegenüber der Spec

1. **Zuschnitt (Spec 18, Plan 2a „Zuschnitt“).** 2d bringt die Bedienung aus Plan 2a samt den Punkten, die 2b bis 2c-2b an 2d verwiesen haben: Quittierung (Spec 7.9), Eintrag der fremden Änderung (7.9, Stand Plan 2b), Auswahl des Gast-Modus (5.3, Stand Plan 2c-2a), Health je Wallbox in der UI, Abgleich der Entscheidungen über die `id` (Wanduhr), die Tesla-Punkte. „Ziele“ aus dem Zuschnitt von Plan 2a sind Ziel-SOC und Abfahrt; sie gehören zum Modus „Geplant“ (Phase 4), den der core nicht kennt. Was sonst nicht kommt: Entscheidung 2. `assignment_log` und `charging_session` (Spec 11.3) folgen mit der Statistik (Phase 4); manuelle Zuordnungen stehen im Änderungsprotokoll, erkannte in den Entscheidungen und in Health.
2. **Absicht aus der app (Spec 5.3).**
   - Jede Bedienung ist eine Änderung an der gültigen Absicht: Die app holt sie mit `GET /v1/intent`, ändert genau das Bediente, setzt die Version und sendet sie mit `PUT /v1/intent`. Ohne gültige Absicht ist die Basis `Intent(version=1)` (Regelbetrieb aus, alles Dry-Run, alle im PV-Überschuss).
   - **Version:** `max(Millisekunden seit 1970 nach der Uhr der app, gültige Version + 1)`.
   - **Boost und Übersteuerung nicht zurückschicken** (Übergabe, „Hinweis für 2d“): Beim Ableiten behält ein Fahrzeug `boost_until` nur, wenn Health für dieses Fahrzeug einen Boost zeigt (`vehicles[].boost_until` nicht `null`), und `wallbox` nur, wenn Health `assignment: "override"` an genau dieser Wallbox zeigt. Ist der Stand der Regelung unbekannt (`vehicles[].mode` ist `null`, etwa bei ausgeschaltetem Regelbetrieb), bleiben ein `boost_until` in der Zukunft und `wallbox` erhalten; ein `boost_until` in der Vergangenheit entfällt immer. Das gilt nicht für das Fahrzeug, das die Bedienung gerade selbst ändert.
   - **Gleichzeitige Bedienung:** Der `IntentService` führt Änderungen nacheinander aus (`asyncio.Lock`). Antwortet der core mit 409 (Version veraltet, etwa nach einer Absicht per `curl`), holt er die gültige Absicht neu und wendet die Änderung genau einmal neu an; ein zweites 409 geht als 409 an den Aufrufer.
   - **Antworten des core:** `accepted` und `clamped` sind Erfolg (`clamped` zeigt die `details`), 422 geht mit den Sätzen aus `details` als 422 hinaus, ein nicht erreichbarer core als 503.
   - Jede Änderung steht im Änderungsprotokoll (`AuditLog.record`) mit der Quelle `ui` oder `ha`, der Aktion aus „Texte“ und `details` mit altem und neuem Wert, Version und Ergebnis.
3. **Rollen (Spec 14, Entscheidung 3).** Die Routen der Bedienung verlangen `operator`; Live-Schaltung und Quittierung verlangen `admin`. Die bestehende Rolle `operator` („Bedienen“) bekommt damit erstmals eigene Rechte; die Navigation zeigt ihr dieselben Seiten wie „Lesen“, die Bedienelemente erscheinen ab „Bedienen“. Lesen sieht alles, ohne Bedienelemente.
4. **Live-Schalter (Spec 8.7, Entscheidung 4).**
   - Die vier Bedingungen: `live_allowed` prüft der core (Klemmung, Hinweis in `details`); Teil 2 bestätigt der Admin per Häkchen, denn ein Merkmal dafür gibt es nicht; die Bestätigung im UI ist der Dialog mit Passwort; den Watchdog prüft der core (Health `watchdog.armed`, `actuators[].watchdog_needed`).
   - `PUT /api/control/actuators` mit `{"modes": {Aktor-ID: "dry_run"|"live"}, "password": …, "confirmed": bool}`. Nennt `modes` einen Aktor mit `live`, verlangt die app ein richtiges Passwort (`AuthService.check_password`) und `confirmed: true`, sonst 403 bzw. 422 und keine Änderung. Nur Aktoren, deren Betriebsart sich ändert, zählen dafür.
   - Der Dialog zeigt je Aktor die Bedingungen: „live erlaubt“, „Watchdog scharf“ (nur Victron und EVCS), „Regelbetrieb an“, „nicht gesperrt“. Er lässt nur Aktoren mit `live_allowed` wählen; die übrigen Bedingungen sind Hinweise, denn der core wartet von selbst.
5. **Bedienung im Einzelnen (Spec 12, 13).**
   - **Regelbetrieb:** `control` an oder aus.
   - **Lademodus:** `vehicles.<key>.mode`.
   - **Boost:** an setzt `boost_until` auf jetzt + 12 h (der core klemmt ohnehin auf 12 h und bindet an den Ansteckvorgang); aus entfernt `boost_until`.
   - **Zuordnung je Wallbox** (Spec 12: „Auto/Tesla/e-Golf/keins“): „Automatisch“ entfernt jede Übersteuerung auf diese Wallbox; ein Fahrzeug setzt dessen `wallbox` auf diese Wallbox und entfernt die Übersteuerung eines anderen Fahrzeugs dorthin. „keins“ gibt es nicht: Die Absicht kann eine Wallbox nicht als leer übersteuern.
   - **Gast-Modus:** `guest.mode`. **Vorrang:** `priority` (alle Fahrzeuge genau einmal) und `surplus_priority`.
6. **Quittierung (Spec 7.9).**
   - `POST /v1/locks/{actuator}/ack` am core. Er antwortet sofort aus dem Stand des letzten Zyklus und wendet die Quittierung im nächsten Zyklus an (die API läuft in einem anderen Thread als der Zyklus).
   - Quittierbar ist eine Sperre, deren Ursache fehlt (die Wartezeit `safety.lock_release_s` läuft) oder deren einzige Ursache der Alarm einer fremden Änderung ist. Die Quittierung gibt die Sperre frei und lässt den Alarm der fremden Änderung fallen; die Erwartung der Erkennung bleibt beim neuen Wert.
   - Besteht eine andere Ursache (DESS aktiv, EVCS nicht manuell, Ladeplan aktiv, ein Prüfpunkt nicht lesbar), lehnt der core mit 409 ab und nennt sie. Ohne Sperre 409, unbekannter Aktor 404.
7. **Entscheidungslog in der app (Spec 7.10, 11.3, Entscheidung 7).**
   - Tabelle `decision` (Migration `0007`): `seq` (Integer, Primärschlüssel, aufsteigend), `id` (Text, eindeutig: die `id` des core), `ts`, `actuator`, `kind`, `requested`, `applied` (JSON), `dry_run`, `outcome`, `result`, `codes` (JSON-Liste), `text`, `inputs` (JSON), Index auf `ts`.
   - **Stream:** jede Meldung `decision` sofort in einen Puffer, der jede Sekunde in einem Zug geschrieben wird.
   - **Rückstand** nach jedem Verbindungsaufbau zum core: `GET /v1/decisions` ab dem jüngsten `ts` der Tabelle minus 10 min (`CATCH_UP_OVERLAP`), ohne Eintrag ab jetzt minus 7 Tage, seitenweise mit `more`. Doppelte `id` werden übergangen (`INSERT … ON CONFLICT DO NOTHING`). Die Überlappung fängt Rücksprünge der Wanduhr bis 10 min (Übergabe, „Entscheidungen tragen die Wanduhr“).
   - **Aufräumen** in der bestehenden Wartung: Einträge älter als `app.decisions_keep_days` (Standard 180, 1 bis 3650).
   - **Grafana:** je neu gespeicherte Entscheidung ein Punkt `decision` in InfluxDB (Retention Policy `raw`), Tags `actuator`, `result`, `dry_run` (`"true"`/`"false"`), Felder `text` und `codes` (mit „,“ verbunden), Zeit `ts`. Das Betriebshandbuch nennt die Abfrage für eine Annotation.
8. **Fremde Änderung im Entscheidungslog (Spec 7.9, Stand Plan 2b).** Jede neue Erkennung (`ForeignChangeDetector.check` liefert sie) wird eine Entscheidung: `kind` wie erkannt, `requested` der erwartete Wert, `applied` der gefundene, `dry_run` falsch, `outcome` `"none"` (`NO_VERDICT`, kein Urteil der Safety-Schicht), `result` `foreign` (neu), `codes` `("foreign_change",)`, Text nach „Texte“, `inputs` wie bei jeder Entscheidung des Zyklus.
9. **Tesla: Stundenbudget (Übergabe, Punkt 1; Entscheidung 6).**
   - **„Aus“ dringend:** Ein Stopp (`charging=False`), weil der geltende Modus „Aus“ ist, ist dringend (`urgent=True`). `charging=False` ist Richtung sicherer Zustand; die Safety-Schicht lässt ihn ohne Mindestabstand und Limits durch (`urgent.bypass`). Das gilt für Tesla und EVCS.
   - **Reserve:** `control.tesla_reserve_commands` (Standard 4, ganze Zahl ≥ 0, kleiner als `commands.per_hour` jedes Fahrzeugs mit Tessie). `ChargeInputs.commands_hour` nennt je Aktor eines Fahrzeugs mit Tessie die Befehle im gleitenden Stundenfenster der Safety-Schicht (`SafetyLayer.hourly_count`). Erreicht die Zahl `per_hour − tesla_reserve_commands`, sendet die Regelung dem Tesla einen Strombefehl nur noch, wenn er den Strom senkt und der Ist-Strom über der Grenze des Wächters liegt (`limit`). Starts, Stopps, der Höchststrom „Tesla an der EVCS“ und dringende Befehle sind davon frei. Ein zurückgehaltener Strombefehl erscheint nicht im Log (wie die 90-s-Regel).
   - **Health:** `vehicles[].commands_hour` (die Zahl aus `ChargeInputs.commands_hour`, sonst `null`).
10. **Tesla: Selbststart (Übergabe, Punkt 2).** Lädt ein Fahrzeug mit Tessie laut Tessie (`charging` wahr, `actual_current_a` ≥ 1 A) an einem Ladepunkt, den die Regelung regelt, während sein Modus „nicht laden“ ergibt (`ModeDecision.charge` falsch) und die Zuordnung klar ist, `control.tesla_against_s` (Standard 120 s) ohne Unterbrechung, vergisst die Regelung ihren letzten Wert für `charging` des Aktors. So geht „Laden aus“ erneut hinaus, mit den Gründen `(Modus, "self_start")`, höchstens alle `control.tesla_against_retry_s` (Standard 600 s) je Fahrzeug. Er zählt im Start/Stopp-Budget, außer im Modus „Aus“ (Präzisierung 9). Die Zeiten laufen auf der Uhr der Safety-Schicht.
11. **Tesla: Ansteckerkennung (Übergabe, Punkt 3).**
    - `poll_interval_s(charging_state, plugged, at_home)`: 30 s beim Laden, 60 s angesteckt, 60 s abgesteckt, aber zu Hause (`at_home` wahr), sonst 600 s.
    - `TessieAdapter.poll_soon()`: Die laufende Wartezeit endet sofort, frühestens 10 s (`MIN_GAP_S`) nach der letzten Abfrage. Die Laufzeit ruft es für jeden Tessie-Adapter, wenn an einer Wallbox ein Fahrzeug erscheint (`connected` wechselt von falsch auf wahr; unbekannt zählt nicht).
12. **UI-Seite „Laden“ (Spec 13).** Je Fahrzeug eine Karte: Ladestand, Wallbox und Art der Zuordnung, angesteckt, lädt, Grenze, letzter Grund, Befehle der letzten Stunde (Fahrzeuge mit Tessie), Lademodus (Auswahl), Boost (Taste „Boost“ bzw. „Boost beenden“, mit „bis HH:MM“). Je Wallbox eine Karte: wer dort steht, Art, Indizien als Sätze, Zuordnung (Auswahl „Automatisch“ oder ein Fahrzeug). Dazu die Karten „Gast“ (Modus) und „Vorrang“ (Reihenfolge der Fahrzeuge mit „nach oben“ und „nach unten“, Vorrang beim Überschuss mit SOC). Ist der Regelbetrieb aus, steht oben ein Hinweis mit der Taste „Regelbetrieb einschalten“ (ab „Bedienen“).
13. **UI-Seite „Entscheidungen“ und „Warum?“ (Spec 7.10, 13).**
    - Filter: Zeitraum (letzte Stunde, heute, 7 Tage, eigenes Datum), Aktor, Ergebnis, „Dry-Run zeigen“ (Standard an) und Suche im Text; neueste zuerst, 100 je Seite mit „Ältere laden“.
    - „Warum?“ auf der Übersicht: je Aktor die jüngste Entscheidung der letzten 24 h mit Alter, dazu ein Verweis auf „Entscheidungen“. Die Begründung des Plan-Slots kommt mit Phase 3.
14. **Benachrichtigungen (Spec 12, Entscheidung 8).**
    - `homeassistant.notify_service` (optional, Muster `^notify\.[a-z0-9_]+$`). Die app braucht `BINDAEMS_HA_TOKEN` (dieselbe `.env` wie der core). Ohne Token oder ohne Abschnitt `homeassistant` sendet sie nichts, und die Komponente `ha-notify` meldet es.
    - Gesendet wird je Alarm mit Schwere `error`, der 30 s (`NOTIFY_AFTER_S`) ohne Unterbrechung besteht, einmal: `persistent_notification.create` mit `notification_id` `bindaems_<Alarm-ID, Punkte als _>` und, falls eingestellt, der `notify`-Dienst. Verschwindet der Alarm, folgt `persistent_notification.dismiss`. Höchstens 10 Meldungen je Minute; der Rest geht ins Log.
    - Abgelehnte Befehle aus HA: eine persistente Meldung `bindaems_ha_command`, ersetzt bei jeder neuen Ablehnung.
    - Der core ruft weiter nie Dienste in Home Assistant auf; die Benachrichtigungen sendet die app.
15. **HA-Bedienelemente (Spec 12).**
    - Je Fahrzeug: `select` `<v>_mode` „Lademodus“, `button` `<v>_boost` „Boost“ und `<v>_boost_stop` „Boost beenden“, `sensor` `<v>_wallbox` „Wallbox“.
    - Je Wallbox: `select` `<w>_assignment` „Zuordnung“ („Automatisch“ und die Namen der Fahrzeuge), `sensor` `<w>_occupant` „Fahrzeug“.
    - System: `switch` `regulation` „Regelbetrieb“, `select` `guest_mode` „Gast-Lademodus“, `binary_sensor` `problem_assignment_unclear` „Zuordnung unklar“ (eine Wallbox oder ein Fahrzeug zeigt `unclear`).
    - Befehle kommen über `{base_topic}/cmd/<object_id>`. Höchstens ein Befehl je Sekunde und Entität; was dazwischen kommt, wird verworfen. Nach jedem Befehl, auch einem verworfenen oder abgelehnten, veröffentlicht die app den Zustand der Entität neu.
16. **Health für die Bedienung.** `watchdog.armed` (der Watchdog ist für diesen core scharf, `_watchdog_armed`), `actuators[].watchdog_needed` (Victron und EVCS), `vehicles[].commands_hour` (Präzisierung 9). Die app reicht Health wie bisher unverändert an `/api/system` weiter.

## Global Constraints

- **Gilt weiter:** alle Global Constraints aus 1a bis 2c-2b.
- **Schreibwege:** unverändert. Auf Geräte schreibt nur der core, jeder Befehl geht durch `CommandExecutor.execute`, also genau einmal durch `SafetyLayer.authorize`. Die app schreibt nur die Absicht und die Quittierung an die interne API.
- **Home Assistant:** Der core bleibt lesend (nie Dienstaufrufe). Die app ruft nur `persistent_notification.create`, `persistent_notification.dismiss` und den eingestellten `notify.*`-Dienst auf.
- **Kein Aktor wird live geschaltet,** auch nicht in Tests gegen echte Geräte; Tests laufen gegen Fakes, die Demo-Welt und den Simulator.
- **Reinheit:** `core/safety` und `core/control` importieren weiter nichts aus `core.actuators`, `core.adapters`, `core.api`, `core.runtime`, `core.telemetry`, `core.accounting` und `core.checks`.
- **Zeit:** Haltezeiten der Regelung auf `ChargeInputs.now`; die app nimmt ihre `Clock` (`self._clock`), nie `datetime.now` direkt.
- **Abdeckung:** ≥ 90 % für `core/safety`, `core/control` und `core/actuators`, ≥ 75 % insgesamt.
- **UI:** Bedienelemente mindestens 44 × 44 px, keine waagrechte Scrollleiste bei 360 px, `{#each}` über Texte vom Server mit dem Index als Schlüssel, jede Antwort mit einem valibot-Schema, jede Vertragsdatei in `GET_ENDPOINTS`/`OTHER` und in `SCHEMAS`.
- **Texte:** exakt wie in „Texte“; Tests prüfen den Wortlaut. Zahlen über `de()` im core, über `format.ts` im UI.
- **Öffentliches Repository:** keine Daten der Anlage; Namen wie `notify.mobile_app_handy` sind Beispiele. Keine Verweise auf Ledger-Einträge (R…).

## Review Focus

1. **Ein abgelaufener Boost oder eine beendete Übersteuerung geht mit der nächsten Bedienung zurück an den core** (Übergabe, „Hinweis für 2d“): Erwartet: Nach dem Abstecken springt ein alter Boost beim nächsten Anstecken nicht an, auch wenn jemand inzwischen einen Modus geändert hat. Tests: Task 9 `test_expired_boost_is_not_sent_back`, `test_ended_override_is_not_sent_back`, `test_unknown_regulation_keeps_a_future_boost`.
2. **Zwei Bedienungen kurz nacheinander** (UI und HA, oder eine Absicht per `curl` dazwischen): Erwartet: keine geht verloren, keine wird doppelt angewendet, 409 nur nach einem zweiten Konflikt. Tests: Task 9 `test_conflict_reapplies_the_change_on_the_new_intent`, `test_changes_are_serialised`.
3. **core startet neu oder ist kurz weg, während Entscheidungen entstehen; die Wanduhr springt zurück:** Erwartet: keine Lücke und keine Doppelten in der Tabelle, Bedienung meldet „core nicht erreichbar.“. Tests: Task 8 `test_catch_up_after_reconnect_has_no_gap_and_no_duplicate`, `test_clock_jump_back_within_the_overlap_is_caught`; Task 10 `test_core_down_gives_503`.
4. **Live-Schaltung durch die falsche Person oder ohne Bestätigung:** Erwartet: Bedienen bekommt 403, ein falsches Passwort 403, ohne Häkchen 422, und die Absicht bleibt unverändert; ein Aktor ohne `live_allowed` bleibt Dry-Run mit dem Hinweis des core. Tests: Task 10 `test_live_needs_admin_password_and_confirmation`, `test_live_without_live_allowed_is_clamped`.
5. **Eine Automation in HA sendet Unsinn oder eine Flut von Befehlen:** Erwartet: ungültige Werte ändern nichts, der Zustand wird neu veröffentlicht, eine Meldung erscheint, höchstens ein Befehl je Sekunde und Entität wirkt. Tests: Task 16 `test_invalid_option_is_rejected_and_state_republished`, `test_more_than_one_command_per_second_is_dropped`.

## Texte

**Gründe** (`_REASON_TEXT` in `shared/decisions.py`, nach `assignment_detected`):

| Code | Text |
|---|---|
| `self_start` | Selbststart |
| `foreign_change` | Änderung von außen |

**Ergebnis** (`DecisionResult`): `FOREIGN = "foreign"`, mit Detail wie „Fehler“: `von außen geändert: erwartet {Wert}`. Beispiel:
`EVCS „evcs“ (Änderung von außen): Strom 10 A – von außen geändert: erwartet 8 A.`
Beispiel Selbststart: `Tesla Model 3 (Aus, Selbststart): Laden aus – Dry-Run, nicht gesendet.`

**Quittierung** (core, `detail`):

| Fall | Status | Text |
|---|---|---|
| angenommen | 202 | `Quittiert. Die Sperre von {Name} löst sich im nächsten Zyklus.` |
| ohne Sperre | 409 | `{Name} ist nicht gesperrt.` |
| Ursache besteht | 409 | `Ursache besteht weiter: {Gründe, mit „ “ verbunden}` |
| unbekannt | 404 | `Unbekannter Aktor „{id}“.` |

**app** (`detail`):

| Fall | Status | Text |
|---|---|---|
| core nicht erreichbar | 503 | `core nicht erreichbar.` |
| zweites 409 | 409 | `Die Absicht wurde gleichzeitig geändert. Bitte erneut versuchen.` |
| Live ohne Bestätigung | 422 | `Bitte bestätige Prüfprotokoll Teil 2.` |
| Live mit falschem Passwort | 403 | `Passwort falsch.` |
| unbekanntes Fahrzeug, Wallbox | 404 | `Unbekanntes Fahrzeug „{key}“.` / `Unbekannte Wallbox „{key}“.` |
| Boost für Fahrzeug ohne Stand | – | (kein Fehler; der core bindet) |

**Änderungsprotokoll** (Aktion, Ziel): `intent.control` (`regelbetrieb`), `intent.mode` (Fahrzeug), `intent.boost` (Fahrzeug), `intent.assignment` (Wallbox), `intent.guest_mode` (`gast`), `intent.priority` (`vorrang`), `intent.actuators` (Aktor-IDs mit „,“), `lock.acknowledge` (Aktor).

**UI:**
- Navigation: „Laden“ (Symbol `car`), „Entscheidungen“ (Symbol `list`, neu), nach „Übersicht“ bzw. nach „System“.
- Lademodi: `pv` „PV-Überschuss“, `min_pv` „Min+PV“, `now` „Sofort“, `off` „Aus“, `boost` „Boost“.
- Zuordnung: `detected` „erkannt“, `default` „angenommen“, `override` „Übersteuerung“, `unclear` „unklar“; „Gast“ für `guest`; ohne Vorgang „kein Fahrzeug“.
- Indizien (stützt / schließt aus):
  - `tessie`: „Tessie: zu Hause und angesteckt“ / „Tessie: nicht angesteckt oder nicht zu Hause“
  - `current`: „Strom passt zu Tessie“ / „Strom passt nicht zu Tessie“
  - `timing`: „Angesteckt, als der Vorgang begann“ / „Angesteckt zu anderer Zeit“
  - `phases`: „Phasenzahl passt“ / „Phasenzahl passt nicht“
  - `home`: „Home Assistant: zu Hause“ / „Home Assistant: nicht zu Hause“
  - `elsewhere`: „Angesteckt, andere Wallbox leer“ / „Andere Wallbox belegt“
- Vorrang beim Überschuss: `cars_first` „Autos zuerst“, `battery_first` „Akku zuerst“, `battery_until_soc` „Akku bis {SOC} %, dann Autos“.
- Hinweise: „Regelbetrieb ist aus. Der core regelt nichts; Modi und Boost wirken erst mit Regelbetrieb.“, Taste „Regelbetrieb einschalten“; auf der System-Seite Karte „Bedienung“ mit Schalter „Regelbetrieb“.
- Live-Dialog: Titel „Live schalten“, Häkchen „Prüfprotokoll Teil 2 für die gewählten Aktoren ist bestanden.“ und (bei EVCS oder Tesla) „evcc ist ganz abgeschaltet.“, Hinweis „EVCS und Tesla gehen gemeinsam live.“, Feld „Passwort“, Taste „Live schalten“. Dry-Run: Titel „Auf Dry-Run stellen“, Text „Der core übergibt die gewählten Aktoren und setzt ihre Sicherwerte.“, Taste „Auf Dry-Run“.
- Bedingungen im Dialog: „live erlaubt“, „Watchdog scharf“, „Regelbetrieb an“, „nicht gesperrt“, je mit „ja“/„nein“.
- Quittierung: Taste „Quittieren“ in der Spalte „Hinweise“ der Karte „Aktoren“ (nur Admin, nur bei Sperre).
- Entscheidungen: Spalten „Zeit“, „Aktor“, „Entscheidung“, „Ergebnis“; leer: „Keine Entscheidungen im Zeitraum.“; Taste „Ältere laden“.
- Ergebnisse: `ok` „bestätigt“, `error` „Fehler“, `timeout` „nicht bestätigt“, `not_sent` „nicht gesendet“, `dry_run` „Dry-Run“, `rejected` „abgelehnt“, `deferred` „zurückgestellt“, `foreign` „von außen geändert“.
- „Warum?“: Titel „Warum?“, leer: „Keine Entscheidungen in den letzten 24 h.“, Verweis „Alle Entscheidungen“.

**Home Assistant:** Optionen der Lademodi wie im UI ohne „Boost“; Zuordnung „Automatisch“ und die Namen der Fahrzeuge; Sensor „Wallbox“: Name der Wallbox oder „keine“; Sensor „Fahrzeug“: Name des Fahrzeugs, „Gast“ oder „keins“. Benachrichtigung: Titel „BindaEMS“, Text des Alarms; Ablehnung: `Befehl aus Home Assistant abgelehnt: {Grund}`.

## Dateistruktur

```
src/bindaems/shared/decisions.py               (geändert) FOREIGN, NO_VERDICT, Gründe self_start, foreign_change
src/bindaems/shared/config.py                  (geändert) control.tesla_reserve_commands, tesla_against_s, tesla_against_retry_s;
                                                          app.decisions_keep_days; homeassistant.notify_service
src/bindaems/core/safety/locks.py              (geändert) LockManager.acknowledge, acknowledgeable
src/bindaems/core/safety/rate.py, layer.py     (geändert) RateLimiter.count, SafetyLayer.hourly_count
src/bindaems/core/control/foreign.py           (geändert) ForeignChangeDetector.acknowledge
src/bindaems/core/control/charge_inputs.py     (geändert) ChargeInputs.commands_hour, wallbox_connected
src/bindaems/core/control/charging.py, charging_tesla.py   (geändert) Aus dringend, Reserve, Selbststart, commands_hour
src/bindaems/core/adapters/tessie.py           (geändert) at_home im Abstand, poll_soon
src/bindaems/core/regulation_link.py, runtime.py, api/app.py   (geändert)
src/bindaems/tools/sim/cars.py, scenarios.py   (geändert) Selbststart, zwei Szenarien
src/bindaems/app/core_link.py                  (geändert) intent, submit_intent, decisions, acknowledge; decision, mode_change
src/bindaems/app/control/__init__.py           (neu)
src/bindaems/app/control/intent.py             IntentService, Änderungen, Ableitung
src/bindaems/app/control/decisions.py          DecisionStore, DecisionCollector
src/bindaems/app/control/routes.py             /api/control/…, /api/decisions…
src/bindaems/app/db/schema.py, migrations/versions/v0007_decisions.py
src/bindaems/app/ha/entities.py, publisher.py  (geändert) select, switch, button, Befehle
src/bindaems/app/ha/commands.py                (neu) HaCommandHandler
src/bindaems/app/ha/notify.py                  (neu) HaNotifier, AlarmNotifications
src/bindaems/app/runtime.py                    (geändert) Verdrahtung
ui/src/lib/api/{schemas,endpoints}.ts, contract/*.json, contract.test.ts   (geändert)
ui/src/lib/{nav,roles}.ts, components/icons.ts, IconShapes.svelte   (geändert)
ui/src/lib/charging/{view.ts,VehicleCard.svelte,WallboxCard.svelte,PriorityCard.svelte}   (neu)
ui/src/lib/decisions/{view.ts,DecisionTable.svelte,WhyCard.svelte}   (neu)
ui/src/lib/system/{ControlCard.svelte,LiveDialog.svelte}   (neu); view.ts (geändert)
ui/src/routes/laden/+page.svelte, entscheidungen/+page.svelte   (neu); +page.svelte, system/+page.svelte (geändert)
ui/tests/e2e/control.spec.ts (neu), pages.spec.ts (geändert)
tests/ui_world.py, tests/integration/{test_ui_contract,test_core_link}.py   (geändert)
deploy/config.example.yaml, deploy/docker-compose.yml (unverändert, .env gilt schon für beide)
docs: Betriebshandbuch, Spec, docs/uebergabe.md
```

`bindaems.app.control` ist ein neues Paket und braucht eine `__init__.py` (`tests/unit/test_layout.py`). Testverzeichnisse bekommen keine `__init__.py`.

---

### Task 1: Fremde Änderung im Entscheidungslog, Health für die Bedienung

**Files:**
- Modify: `src/bindaems/shared/decisions.py`, `src/bindaems/core/runtime.py`, `src/bindaems/core/api/app.py`
- Test: `tests/unit/shared/test_decisions.py`, `tests/integration/test_runtime_control.py`, `tests/unit/core/api/test_api.py`

**Interfaces:**
- Consumes: `ForeignChangeDetector.check(present, pending, now) -> list[ForeignChange]`, `CoreRuntime._decide(decision)`, `watchdog_to_json`, `_actuator_json`
- Produces:
  - `DecisionResult.FOREIGN = "foreign"`; `_RESULT_WITH_DETAIL[FOREIGN] = "von außen geändert"`
  - `NO_VERDICT: Final = "none"` in `shared/decisions.py`
  - `_REASON_TEXT` um `self_start` und `foreign_change` (Task 4 nutzt `self_start`)
  - `foreign_decision(change: ForeignChange, label: str, inputs: Mapping[str, float | None], new_id: str) -> Decision` in `core/runtime.py` (Präzisierung 8); der Laufzeit-Zyklus gibt jede Rückgabe von `foreign.check` an `_decide`
  - Health: `watchdog.armed: bool`, `actuators[].watchdog_needed: bool` (aus `ActuatorSpec.needs_watchdog`); `HealthReport` ändert dafür nur die Inhalte der bestehenden Felder

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/shared/test_decisions.py
def test_foreign_change_text() -> None:
    text = decision_text(
        "EVCS „evcs“", CommandKind.CURRENT, 10.0, DecisionResult.FOREIGN,
        detail="erwartet 8 A", reasons=("foreign_change",),
    )
    assert text == "EVCS „evcs“ (Änderung von außen): Strom 10 A – von außen geändert: erwartet 8 A."


def test_self_start_reason_text() -> None:
    text = decision_text("Tesla Model 3", CommandKind.CHARGING, False, DecisionResult.DRY_RUN,
                         reasons=("mode_off", "self_start"))
    assert text == "Tesla Model 3 (Aus, Selbststart): Laden aus – Dry-Run, nicht gesendet."


def test_foreign_decision_round_trips() -> None:
    decision = replace(SAMPLE, result=DecisionResult.FOREIGN, outcome=NO_VERDICT)
    assert decision_from_json(decision_to_json(decision)) == decision


# tests/integration/test_runtime_control.py
def test_foreign_change_is_logged_once() -> None:
    # EVCS live mit bestätigtem Strom 8 A; der Cerbo meldet danach dreimal im Abstand von 10 s 10 A
    # Erwartet: genau eine Entscheidung an "wallbox.evcs" mit result "foreign", requested 8.0,
    # applied 10.0, dry_run False, outcome "none", codes ["foreign_change"], im Stream und im Spool;
    # weitere Prüfungen mit 10 A erzeugen keine zweite


def test_health_names_armed_watchdog_and_watchdog_need() -> None:
    report = runtime.health()
    assert report.watchdog["armed"] is False
    needs = {a["id"]: a["watchdog_needed"] for a in report.actuators}
    assert needs == {"victron": True, "wallbox.evcs": True, "vehicle.tesla": False}
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/shared/test_decisions.py tests/integration/test_runtime_control.py tests/unit/core/api/test_api.py -q`
Expected: FAIL (`AttributeError: FOREIGN`)

- [ ] **Step 3: Umsetzen** wie in den Interfaces. Die Entscheidung entsteht im Prüfzyklus direkt nach `foreign.check`, mit der Bezeichnung des Aktors (`labels`) und den `inputs` des Zyklus. `decision_text` bekommt dafür `value=change.actual` und `detail=f"erwartet {Wert mit Einheit}"` (die Einheit wie in `_command_text`: A bzw. W).

- [ ] **Step 4: Vertrag neu schreiben und alles laufen lassen**

Run: `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py -q && uv run pytest -q && uv run ruff check . && uv run ruff format --check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports && (cd ui && pnpm check && pnpm test)`
Expected: PASS (die UI-Schemas sind `v.object`, neue Felder stören nicht)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests ui/src/lib/api/contract
git commit -m "feat(core): Entscheidung bei fremder Änderung, Watchdog scharf und Watchdog-Bedarf in Health"
```

---

### Task 2: Quittierung von Sperren

**Files:**
- Modify: `src/bindaems/core/safety/locks.py`, `src/bindaems/core/control/foreign.py`, `src/bindaems/core/runtime.py`, `src/bindaems/core/api/app.py`
- Test: `tests/unit/core/safety/test_locks.py`, `tests/unit/core/control/test_foreign.py`, `tests/unit/core/api/test_api.py`, `tests/integration/test_runtime_control.py`

**Interfaces:**
- Consumes: `LockManager`, `ForeignChangeDetector`, `lock_causes`, `CoreView`
- Produces (Präzisierung 6):
  - `LockManager.acknowledge(actuator: str) -> bool`: entfernt eine Sperre mit `cleared_at` nicht `None`; sonst `False`
  - `ForeignChangeDetector.acknowledge(actuator: str) -> None`: lässt die letzte Erkennung des Aktors fallen (der Alarm verschwindet), die Erwartung bleibt
  - `AckReply(status: Literal["accepted", "not_locked", "cause_present", "unknown"], detail: str)` (frozen dataclass) in `core/api/app.py`
  - `CoreView.acknowledge(actuator: str) -> AckReply`; `CoreRuntime.acknowledge` prüft gegen den Stand des letzten Zyklus (Sperren und Ursachen ohne die fremde Änderung) und reiht eine angenommene Quittierung in `_acks` (threadsicher, `collections.deque`)
  - `CoreRuntime._apply_acks(now)`: in jedem Zyklus vor `_update_operation`: je Eintrag `foreign.acknowledge`, Ursachen neu (`lock_causes` mit den Alarmen danach), `locks.update`, `locks.acknowledge`; das Log nennt jede Quittierung
  - `POST /v1/locks/{actuator}/ack` (Token wie alle Routen): 202, 404, 409 nach „Texte“, Antwort `{"detail": …}`

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/safety/test_locks.py
def test_acknowledge_releases_a_lock_whose_cause_is_gone() -> None:
    locks = LockManager(60)
    locks.update(T0, {"wallbox.evcs": ["EVCS nicht manuell"]})
    locks.update(T0 + timedelta(seconds=10), {"wallbox.evcs": []})
    assert locks.acknowledge("wallbox.evcs") is True
    assert locks.active(T0 + timedelta(seconds=11)) == {}


def test_acknowledge_keeps_a_lock_whose_cause_persists() -> None:
    locks = LockManager(60)
    locks.update(T0, {"victron": ["Dynamic ESS aktiv"]})
    assert locks.acknowledge("victron") is False
    assert "victron" in locks.active(T0)


# tests/unit/core/control/test_foreign.py
def test_acknowledge_drops_the_alarm_but_keeps_the_new_expectation() -> None:
    # nach einer Erkennung (8 → 10 A): acknowledge → alarms(now) == []; check mit 10 A → keine Erkennung


# tests/unit/core/api/test_api.py
@pytest.mark.parametrize(
    ("reply", "status"),
    [(AckReply("accepted", "Quittiert. Die Sperre von EVCS „evcs“ löst sich im nächsten Zyklus."), 202),
     (AckReply("not_locked", "EVCS „evcs“ ist nicht gesperrt."), 409),
     (AckReply("cause_present", "Ursache besteht weiter: EVCS nicht im manuellen Modus."), 409),
     (AckReply("unknown", "Unbekannter Aktor „x“."), 404)],
)
def test_ack_route_maps_the_reply(view_client, reply, status) -> None:
    view, client = view_client
    view.ack_reply = reply
    response = client.post("/v1/locks/wallbox.evcs/ack", headers=AUTH)
    assert (response.status_code, response.json()["detail"]) == (status, reply.detail)


# tests/integration/test_runtime_control.py
def test_acknowledged_foreign_change_unlocks_in_the_next_cycle() -> None:
    # fremde Änderung an der EVCS → gesperrt; acknowledge("wallbox.evcs").status == "accepted";
    # nach einem Zyklus: keine Sperre, kein Alarm competitor.foreign.wallbox.evcs, Betriebszustand CONTROL


def test_ack_with_present_cause_is_refused() -> None:
    # EVCS-Modus nicht manuell → acknowledge(...).status == "cause_present", Sperre bleibt
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/safety/test_locks.py tests/unit/core/control/test_foreign.py tests/unit/core/api/test_api.py tests/integration/test_runtime_control.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces; `FakeView` in `test_api.py` bekommt `ack_reply` und `acknowledge`.

- [ ] **Step 4: Alles laufen lassen** (wie Task 1, Step 4, ohne Vertrag)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests
git commit -m "feat(core): Quittierung von Sperren über die interne API"
```

---

### Task 3: Tesla – „Aus“ sofort und Reserve im Stundenbudget

**Files:**
- Modify: `src/bindaems/shared/config.py`, `src/bindaems/core/safety/rate.py`, `src/bindaems/core/safety/layer.py`, `src/bindaems/core/control/charge_inputs.py`, `src/bindaems/core/control/charging.py`, `src/bindaems/core/regulation_link.py`, `deploy/config.example.yaml`
- Test: `tests/unit/shared/test_config.py`, `tests/unit/core/safety/test_rate.py`, `tests/unit/core/control/test_charging.py`, `tests/unit/core/test_regulation_link.py`

**Interfaces:**
- Consumes: `RateLimiter`, `ChargeController._switch_off`, `_charge`
- Produces (Präzisierung 9):
  - `ControlConfig.tesla_reserve_commands: Annotated[int, Field(ge=0)] = 4`; der Validator von `Config` lehnt einen Wert ≥ `commands.per_hour` eines Fahrzeugs mit Tessie ab: `control.tesla_reserve_commands muss unter vehicles.{key}.commands.per_hour liegen`
  - `RateLimiter.count(actuator: str, now: datetime) -> int` (alle Arten im Stundenfenster); `SafetyLayer.hourly_count(actuator: str, now: datetime) -> int`
  - `ChargeInputs.commands_hour: Mapping[str, int] = field(default_factory=dict)` (letztes Feld; Schlüssel Aktor-ID)
  - `RegulationLink.step` füllt es für jeden Aktor eines Fahrzeugs mit Tessie
  - `_switch_off(..., urgent=mode == Effective.OFF)` für den Stopp nach dem Modus
  - Health `vehicles[].commands_hour: int | None`
  - `deploy/config.example.yaml`: `tesla_reserve_commands: 4` unter `control`, mit Kommentar

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_charging.py
def test_off_stops_urgently(cfg) -> None:
    # Tesla lädt (PV), Absicht wechselt auf "off": der Befehl charging=False an vehicle.tesla hat urgent=True
    # und die Gründe ("mode_off",)


def test_off_stop_passes_an_exhausted_hourly_budget(cfg) -> None:
    # SafetyLayer mit 20 erfassten Tesla-Befehlen in der letzten Stunde; der Stopp nach "off" bekommt
    # ein Urteil ACCEPTED mit Befund "urgent.bypass", nicht DEFERRED


def test_reserve_holds_back_pv_current_changes(cfg) -> None:
    # commands_hour={"vehicle.tesla": 16} (20 − 4): steigt der Wunsch im PV-Überschuss, kommt kein
    # Strombefehl an vehicle.tesla; bei 15 kommt er


def test_reserve_lets_guard_reductions_through(cfg) -> None:
    # commands_hour 16, Ist-Strom 16 A, Grenze des Wächters 10 A → Strombefehl 10 A an vehicle.tesla


def test_reserve_does_not_hold_starts_and_stops(cfg) -> None:
    # commands_hour 16: ein Start (charging=True) und ein Stopp im PV-Überschuss gehen hinaus


def test_health_names_commands_of_the_last_hour(cfg) -> None:
    # nach einem Schritt mit commands_hour={"vehicle.tesla": 7}: health() Tesla → commands_hour == 7,
    # e-Golf → None


# tests/unit/core/safety/test_rate.py
def test_count_covers_all_kinds_within_the_hour() -> None:
    # zwei current, ein charging vor 10 min, ein current vor 61 min → count == 3


# tests/unit/shared/test_config.py
def test_reserve_must_stay_below_the_hourly_limit(tmp_path) -> None:
    # control.tesla_reserve_commands: 20 bei per_hour 20 → ValidationError mit dem Text aus den Interfaces
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/shared/test_config.py tests/unit/core/safety/test_rate.py tests/unit/core/control/test_charging.py tests/unit/core/test_regulation_link.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen.** Die Reserve prüft `_charge` vor dem Strombefehl eines Fahrzeugs mit Tessie am Wall Connector und `maximum_command` nicht (Präzisierung 9). Die Tabelle der Einstellungen im Betriebshandbuch („Akku und Spitzen“) und `BATTERY_SETTINGS` in `tests/unit/test_docs.py` folgen in Task 18.

- [ ] **Step 4: Alles laufen lassen,** dazu die Szenarien

Run: `uv run pytest -q` (enthält `tests/integration/test_sim_scenarios.py`) und die Prüfungen wie Task 1, Step 4
Expected: PASS. Scheitert ein Szenario, liegt der Fehler zuerst in der Regelung (eigener `fix(core)`-Commit mit Test); ein Szenario ändert man nur, wenn es physikalisch nicht zu schaffen ist. Der Bericht des Tasks nennt die Zahl der Tesla-Befehle in `wolkig` vorher und nachher.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems deploy/config.example.yaml tests
git commit -m "feat(core): Tesla – Aus stoppt sofort, Reserve im Stundenbudget"
```

---

### Task 4: Tesla – Selbststart korrigieren

**Files:**
- Modify: `src/bindaems/shared/config.py`, `src/bindaems/core/control/charging.py`, `src/bindaems/core/control/charging_tesla.py`, `deploy/config.example.yaml`
- Test: `tests/unit/core/control/test_charging_tesla.py`, `tests/unit/shared/test_config.py`

**Interfaces:**
- Consumes: `LastValues.forget`, `ModeDecision.charge`, `VehicleObs.charging`, `VehicleObs.actual_current_a`
- Produces (Präzisierung 10):
  - `ControlConfig.tesla_against_s: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 120.0`, `tesla_against_retry_s: Annotated[float, Field(gt=0, allow_inf_nan=False)] = 600.0`
  - `SELF_START: Final = "self_start"` in `charging_tesla.py`
  - `AgainstMode(against_s: float, retry_s: float)` in `charging_tesla.py`: `due(key: str, now: datetime, against: bool) -> bool` (wahr, wenn `against` seit `against_s` ohne Unterbrechung gilt und der letzte Neuversuch mindestens `retry_s` zurückliegt; zählt dann den Neuversuch), `clear(key)` (neuer Vorgang, `idle`)
  - `ChargeController` vergisst bei `due` den Wert `(actuator, CHARGING)` und hängt `SELF_START` an die Gründe des folgenden `_switch_off`

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/control/test_charging_tesla.py
def test_against_mode_needs_120_s_without_interruption() -> None:
    against = AgainstMode(120, 600)
    assert not against.due("tesla", T0, True)
    assert not against.due("tesla", T0 + timedelta(seconds=119), True)
    assert against.due("tesla", T0 + timedelta(seconds=120), True)


def test_against_mode_retries_at_most_every_10_min() -> None:
    # nach einem due bei 120 s: bei 300 s nicht, bei 720 s wieder


def test_interruption_restarts_the_120_s() -> None:
    # True bei 0 s, False bei 60 s, True ab 61 s → due erst bei 181 s


def test_self_starting_tesla_in_off_gets_charging_off_again(cfg) -> None:
    # Modus "off", die Regelung hat charging=False gesendet (bestätigt); Tessie meldet danach 150 s lang
    # charging True und 16 A → ein neuer Befehl charging=False an vehicle.tesla mit den Gründen
    # ("mode_off", "self_start"), urgent=True


def test_self_start_in_pv_without_surplus(cfg) -> None:
    # PV ohne Überschuss, gestoppt; Tesla lädt 120 s selbst → charging=False mit ("mode_pv", "self_start"),
    # urgent False


def test_no_correction_while_the_assignment_is_unclear(cfg) -> None:
    # CONTRADICTION ab UNCLEAR_FROM_S und Tesla lädt gegen den Modus → kein Befehl mit "self_start"
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/control/test_charging_tesla.py tests/unit/shared/test_config.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces; `against` ist „Tessie: lädt mit ≥ 1 A, Modus: nicht laden, Zuordnung klar, Ladepunkt geregelt“. Ein neuer Vorgang und `idle` rufen `clear`.

- [ ] **Step 4: Alles laufen lassen** (wie Task 3, Step 4)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems deploy/config.example.yaml tests
git commit -m "feat(core): Tesla – Laden gegen den Modus nach 120 s erneut stoppen"
```

---

### Task 5: Tesla – Ansteckerkennung

**Files:**
- Modify: `src/bindaems/core/adapters/tessie.py`, `src/bindaems/core/control/charge_inputs.py`, `src/bindaems/core/runtime.py`
- Test: `tests/unit/core/adapters/test_tessie.py`, `tests/unit/core/control/test_charge_inputs.py`, `tests/integration/test_runtime.py`

**Interfaces:**
- Consumes: `parse_state`, `TessieAdapter.run`, die Wallbox-Beobachtungen in `charge_inputs.py`
- Produces (Präzisierung 11):
  - `poll_interval_s(charging_state: str | None, plugged: bool | None, at_home: bool | None) -> float`
  - `MIN_GAP_S: Final = 10.0`; `TessieAdapter.poll_soon() -> None` (`asyncio.Event`; `run` wartet auf die Wartezeit oder das Ereignis, dann frühestens `MIN_GAP_S` nach der letzten Abfrage)
  - `wallbox_connected(snap: Snapshot, cfg: Config) -> dict[str, bool | None]` in `charge_inputs.py` (je Wallbox `connected` wie in den Beobachtungen)
  - `CoreRuntime` merkt sich den Stand des letzten Zyklus und ruft bei einem Wechsel falsch → wahr `poll_soon()` jedes Tessie-Adapters

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/core/adapters/test_tessie.py
@pytest.mark.parametrize(
    ("state", "plugged", "home", "expected"),
    [("Charging", True, True, 30.0), ("Stopped", True, False, 60.0),
     ("Disconnected", False, True, 60.0), ("Disconnected", False, False, 600.0),
     ("Disconnected", False, None, 600.0), (None, None, None, 600.0)],
)
def test_poll_interval(state, plugged, home, expected) -> None:
    assert poll_interval_s(state, plugged, home) == expected


async def test_poll_soon_ends_the_wait_after_the_minimum_gap() -> None:
    # Adapter mit falscher sleep-Uhr: nach einer Abfrage (Auto weg, 600 s) ruft der Test poll_soon();
    # die nächste Abfrage folgt nach MIN_GAP_S, nicht nach 600 s


# tests/integration/test_runtime.py
def test_new_vehicle_at_a_wallbox_triggers_a_tessie_poll(cfg) -> None:
    # Zyklus 1: twc vehicle_connected False; Zyklus 2: True → poll_soon des Tessie-Adapters einmal gerufen;
    # Zyklus 3: True → kein weiterer Aufruf; unbekannt → False zählt nicht
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/core/adapters/test_tessie.py tests/unit/core/control/test_charge_inputs.py tests/integration/test_runtime.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen.** Der Simulator nutzt `poll_interval_s` über sein Tessie-Modell (`Tesla.next_poll`); er bekommt `at_home` aus seinen Werten, `poll_soon` simuliert er nicht.

- [ ] **Step 4: Alles laufen lassen** (wie Task 3, Step 4)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests
git commit -m "feat(core): Tessie fragt zu Hause und bei neuem Vorgang schneller ab"
```

---

### Task 6: Simulator – „Aus“ unter Wolken und Selbststart

**Files:**
- Modify: `src/bindaems/tools/sim/cars.py`, `src/bindaems/tools/sim/scenarios.py`, `docs/betrieb.md` (Tabelle „Szenarien“)
- Test: `tests/unit/tools/sim/test_sim_checks.py`, `tests/unit/tools/sim/test_sim_model.py`, `tests/integration/test_sim_scenarios.py`, `tests/unit/test_docs.py`

**Interfaces:**
- Consumes: `_new_version`, `Tesla.charge_enabled`, `_cloudy`, `Checks`
- Produces:
  - Ereignis `_SelfStart(vehicle: str)`: setzt beim Tesla `charge_enabled = True` (das Auto startet selbst, etwa aus der App)
  - Prüfungen `_stops_within(actuator: str, at_s: int, within_s: int)` und `_no_deferred(actuator: str, kind: CommandKind)` (Sätze in der Tabelle unten)
  - Szenario `tesla-aus`: wie `wolkig` (60 min, EVCS und Tesla live), bei 45 min wechselt der Tesla per `_new_version` auf `off`
  - Szenario `tesla-selbststart`: Tesla am Wall Connector im Modus `off`, live, gestoppt; bei 5 min `_SelfStart("tesla")`
  - `SCENARIOS` um beide; die Tabelle im Betriebshandbuch („Simulator“, „Szenarien“) um beide

| Prüfung | Satz | Befund |
|---|---|---|
| `_stops_within(actuator, at_s, within_s)` | `„{actuator}“ bekommt „Laden aus“ binnen {within_s} s nach {m} min.` | `„{actuator}“ bekam nach {at_s} s kein „Laden aus“ binnen {within_s} s.` |
| `_no_deferred(actuator, kind)` | `Kein zurückgestellter Befehl „{kind}“ an „{actuator}“.` | `{n} zurückgestellte Befehle „{kind}“ an „{actuator}“, der erste bei {t} s.` |

- `tesla-aus` prüft `_stops_within("vehicle.tesla", 2700, 10)` und `_no_deferred("vehicle.tesla", CommandKind.CHARGING)`, dazu die üblichen Grenzen.
- `tesla-selbststart` prüft `_stops_within("vehicle.tesla", 300, 210)` (120 s plus die 60-s-Abfrage von Tessie angesteckt plus der Verzug des Befehls) und dass der Tesla ab 9 min nicht mehr lädt (`Checks` wie in `tesla-schlaeft`).

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/tools/sim/test_sim_checks.py
def test_stops_within_finds_a_late_stop() -> None:
    assert check("tesla-aus", 0).text == "„vehicle.tesla“ bekommt „Laden aus“ binnen 10 s nach 45 min."
    # Entscheidung charging=False bei 2715 s → ["„vehicle.tesla“ bekam nach 2700 s kein „Laden aus“ binnen 10 s."]


def test_no_deferred_counts_deferred_commands() -> None:
    assert check("tesla-aus", 1).text == "Kein zurückgestellter Befehl „charging“ an „vehicle.tesla“."


# tests/unit/tools/sim/test_sim_model.py
def test_self_start_sets_charge_enabled() -> None:
    # Modell mit Tesla am twc, charge_enabled False; _SelfStart("tesla") → charge_enabled True, lädt im nächsten Schritt
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/tools/sim tests/integration/test_sim_scenarios.py tests/unit/test_docs.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Tests laufen lassen,** dann `uv run python -m bindaems.tools.sim --szenario tesla-aus --out /tmp/tesla-aus.md` und den Bericht lesen
Expected: PASS; alle 23 Szenarien grün. Scheitert eines, gilt die Regel aus Task 3, Step 4.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools/sim tests docs/betrieb.md
git commit -m "test(sim): Szenarien tesla-aus und tesla-selbststart"
```

---

### Task 7: app – Verbindung zum core für Absicht, Entscheidungen und Quittierung

**Files:**
- Modify: `src/bindaems/app/core_link.py`
- Test: `tests/unit/app/test_core_link.py`, `tests/integration/test_core_link.py`

**Interfaces:**
- Consumes: die Routen des core (`GET/PUT /v1/intent`, `GET /v1/decisions`, `POST /v1/locks/{actuator}/ack`), `Intent`, `decision_from_json`
- Produces:
  - `CoreReply(status: int, body: dict[str, Any])` (frozen dataclass)
  - `CoreClient.intent() -> Intent | None`, `CoreClient.submit_intent(intent: Intent) -> CoreReply`, `CoreClient.decisions(since: datetime, limit: int) -> tuple[list[Decision], bool]`, `CoreClient.acknowledge(actuator: str) -> CoreReply`; Netzfehler, Zeitüberschreitung (5 s) und ungültiges JSON: `CoreUnavailableError`; `since` wird mit `%2B` kodiert (httpx `params`)
  - `LiveFeed(..., decision_handlers: Sequence[DecisionHandler] = (), mode_handlers: Sequence[ModeHandler] = (), connect_handlers: Sequence[ConnectHandler] = ())` mit `DecisionHandler = Callable[[Decision], Awaitable[None]]`, `ModeHandler = Callable[[dict[str, Any]], Awaitable[None]]`, `ConnectHandler = Callable[[], Awaitable[None]]`
  - `_dispatch`: `decision` → `decision_from_json` → jeder Handler (Fehler ins Log, ein ungültiges Objekt ebenso); `mode_change` → `refresh_health()` und jeder `mode_handler`; nach jedem Verbindungsaufbau jeder `connect_handler`
  - `LiveFeed.refresh_health() -> None`: Die nächste Health-Abfrage folgt sofort (Ereignis im `_health_loop`)

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/app/test_core_link.py
async def test_decision_messages_reach_the_handlers() -> None:
    # Stream mit {"type": "decision", "data": decision_to_json(SAMPLE)} → Handler bekommt SAMPLE


async def test_invalid_decision_is_logged_and_skipped(caplog) -> None:
    # {"type": "decision", "data": {"id": 1}} → kein Handler-Aufruf, eine Log-Zeile, der Stream läuft weiter


async def test_mode_change_refreshes_health_at_once() -> None:
    # nach {"type": "mode_change", ...} ruft der Feed health() vor Ablauf von HEALTH_S erneut


async def test_connect_handlers_run_after_each_connection() -> None:
    # zwei Verbindungen nacheinander → der Handler läuft zweimal


# tests/integration/test_core_link.py (FakeView um intent, submit_intent, decisions, acknowledge)
async def test_client_submits_and_reads_the_intent(core_api) -> None:
    reply = await client.submit_intent(Intent(version=5, control=True))
    assert (reply.status, reply.body["result"]) == (200, "accepted")
    assert (await client.intent()) == Intent(version=5, control=True)


async def test_client_reads_decisions_with_timezone(core_api) -> None:
    decisions, more = await client.decisions(T0.astimezone(VIENNA), 10)
    assert view.decision_calls == [(T0, 10)] and more is False
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/app/test_core_link.py tests/integration/test_core_link.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: Alles laufen lassen** (wie Task 1, Step 4, ohne Vertrag)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/core_link.py tests
git commit -m "feat(app): Absicht, Entscheidungen und Quittierung über die interne API"
```

---

### Task 8: app – Entscheidungslog in SQLite und Markierungen für Grafana

**Files:**
- Create: `src/bindaems/app/control/__init__.py`, `src/bindaems/app/control/decisions.py`, `src/bindaems/app/db/migrations/versions/v0007_decisions.py`
- Modify: `src/bindaems/app/db/schema.py`, `src/bindaems/shared/config.py`, `src/bindaems/app/runtime.py`
- Test: `tests/unit/app/control/test_decisions.py`, `tests/unit/app/test_db.py`

**Interfaces:**
- Consumes: `CoreClient.decisions`, `LiveFeed` (Handler aus Task 7), `PointSink`, `Clock`
- Produces (Präzisierung 7):
  - Tabelle `decision` wie in Präzisierung 7; Migration `revision = "0007"`, `down_revision = "0006"`
  - `AppConfig.decisions_keep_days: Annotated[int, Field(ge=1, le=3650)] = 180`
  - `StoredDecision(cursor: int, decision: Decision)` (frozen dataclass)
  - `DecisionQuery(since: datetime | None = None, until: datetime | None = None, actuator: str | None = None, result: DecisionResult | None = None, dry_run: bool = True, text: str | None = None, before: int | None = None, limit: int = 100)` (frozen dataclass; `dry_run=False` blendet Dry-Run aus, `text` sucht ohne Groß- und Kleinschreibung, `before` ist ein `cursor`)
  - `DecisionStore(engine, clock)`: `add(decisions: Sequence[Decision]) -> list[Decision]` (die neu gespeicherten), `newest_ts() -> datetime | None`, `query(q: DecisionQuery) -> tuple[list[StoredDecision], bool]` (neueste zuerst nach `ts`, dann `seq`; `bool` = es gibt ältere), `latest_per_actuator(since: datetime) -> list[StoredDecision]`, `cleanup(keep_days: int) -> int`
  - `CATCH_UP_OVERLAP: Final = timedelta(minutes=10)`, `CATCH_UP_EMPTY: Final = timedelta(days=7)`, `PAGE_LIMIT: Final = 1000`
  - `DecisionCollector(store, client, sink, clock)`: `async on_decision(decision)` (puffert), `async catch_up()` (Connect-Handler), `async run()` (schreibt den Puffer jede Sekunde mit `asyncio.to_thread`), `async cleanup(keep_days)`; zu jeder neu gespeicherten Entscheidung ein `Point("decision", …, rp="raw")` wie in Präzisierung 7
  - `AppRuntime`: `self.decisions = DecisionStore(...)`, `self.collector = DecisionCollector(...)`, als Handler am `LiveFeed`, Job `decisions` in `run()`, Aufräumen in der Wartung

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/app/control/test_decisions.py
def test_add_ignores_duplicate_ids(store) -> None:
    assert store.add([SAMPLE]) == [SAMPLE]
    assert store.add([SAMPLE]) == []


def test_query_filters_and_pages(store) -> None:
    # 250 Entscheidungen, abwechselnd dry_run; query(DecisionQuery(dry_run=False, limit=100)) → 100
    # neueste ohne Dry-Run, more True; mit before=letzter cursor → die nächsten; text="strom 8" findet
    # nur Texte mit „Strom 8 A“; actuator="vehicle.tesla" nur den Tesla


def test_latest_per_actuator_in_the_last_24_h(store) -> None:
    # je Aktor die jüngste; eine Entscheidung von vor 25 h fehlt


def test_cleanup_removes_old_rows(store, clock) -> None:
    # Einträge vor 181 und vor 179 Tagen → cleanup(180) == 1


async def test_catch_up_after_reconnect_has_no_gap_and_no_duplicate(collector, fake_client) -> None:
    # Tabelle bis T0; der core hat T0−5 min bis T0+1 h in zwei Seiten (more True, dann False)
    # → Aufrufe since=T0−10 min, dann ab dem ts der letzten; jede id genau einmal in der Tabelle


async def test_clock_jump_back_within_the_overlap_is_caught(collector, fake_client) -> None:
    # Tabelle bis T0; neue Entscheidung des core mit ts T0−3 min (Wanduhr zurück) und neuer id → gespeichert


async def test_empty_table_catches_up_7_days(collector, fake_client, clock) -> None:
    # erste Abfrage mit since = jetzt − 7 Tage


async def test_new_decisions_become_grafana_points(collector, sink) -> None:
    # on_decision(SAMPLE) und run einen Takt → ein Point("decision", {"actuator": …, "result": "dry_run",
    # "dry_run": "true"}, {"text": SAMPLE.text, "codes": "mode_pv,pv_surplus"}, SAMPLE.ts, rp="raw");
    # dieselbe Entscheidung im Rückstand → kein zweiter Punkt


# tests/unit/app/test_db.py
def test_migration_0007_creates_the_decision_table(engine) -> None:
    # Spalten wie in Präzisierung 7, id eindeutig, Index auf ts
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/app/control/test_decisions.py tests/unit/app/test_db.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces; die Textsuche mit `LIKE` und `lower()`.

- [ ] **Step 4: Alles laufen lassen** (wie Task 1, Step 4, ohne Vertrag)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests
git commit -m "feat(app): Entscheidungslog in SQLite mit Rückstand, Aufräumen und Grafana-Markierungen"
```

---

### Task 9: app – Absicht aus der Bedienung

**Files:**
- Create: `src/bindaems/app/control/intent.py`
- Test: `tests/unit/app/control/test_intent.py`

**Interfaces:**
- Consumes: `CoreClient.intent`, `CoreClient.submit_intent`, `LiveState.health`, `LiveFeed.refresh_health`, `AuditLog.record`, `Clock`, `Intent`
- Produces (Präzisierungen 2 und 5):
  - Änderungen als frozen dataclasses: `SetControl(on: bool)`, `SetMode(vehicle: str, mode: ChargeMode)`, `SetBoost(vehicle: str, on: bool)`, `SetAssignment(wallbox: str, vehicle: str | None)` (`None` = Automatisch), `SetGuestMode(mode: ChargeMode)`, `SetPriority(priority: list[str], surplus: SurplusPriority)`, `SetActuators(modes: dict[str, ActuatorMode])`; `Change = SetControl | SetMode | …`
  - `derive(current: Intent | None, health: Mapping[str, Any] | None, change: Change, now: datetime) -> Intent` (rein): Basis, Bereinigung von Boost und Übersteuerung nach Präzisierung 2, dann die Änderung, dann die Version
  - `boost_until(now: datetime) -> datetime`: `now + 12 h`
  - `ChangeResult(result: Literal["accepted", "clamped"], version: int, details: list[str], intent: Intent)` (frozen dataclass)
  - Fehler: `CoreDown`, `ConflictAgain`, `Rejected(details: list[str])`, `UnknownTarget(text: str)`
  - `IntentService(client, live, feed, audit, clock, cfg)`: `async apply(change: Change, *, actor: str, source: Source) -> ChangeResult` (prüft Fahrzeug und Wallbox gegen `cfg`, `asyncio.Lock`, ein Neuversuch bei 409, Änderungsprotokoll nach „Texte“, danach `feed.refresh_health()`), `async current() -> Intent | None` (vom core), `cached() -> Intent | None` (die zuletzt gesehene, für HA)

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/app/control/test_intent.py
def test_version_is_milliseconds_or_one_more(clock) -> None:
    assert derive(None, None, SetControl(True), T_APP).version == epoch_ms(T_APP)
    future = Intent(version=epoch_ms(T_APP) + 50)
    assert derive(future, None, SetControl(True), T_APP).version == epoch_ms(T_APP) + 51


def test_expired_boost_is_not_sent_back() -> None:
    current = Intent(version=1, vehicles={"egolf": VehicleIntent(boost_until=T_APP + timedelta(hours=2))})
    health = {"vehicles": [{"id": "egolf", "mode": "pv", "boost_until": None, "assignment": "default", "wallbox": "evcs"}]}
    new = derive(current, health, SetMode("tesla", ChargeMode.NOW), T_APP)
    assert new.vehicles["egolf"].boost_until is None


def test_ended_override_is_not_sent_back() -> None:
    # current egolf.wallbox "twc", Health zeigt egolf assignment "default" → new.vehicles["egolf"].wallbox is None


def test_active_boost_and_override_stay() -> None:
    # Health zeigt boost_until und assignment "override" an "twc" → beide bleiben


def test_unknown_regulation_keeps_a_future_boost() -> None:
    # Health vehicles[].mode None → boost_until in der Zukunft und wallbox bleiben; einer in der Vergangenheit fällt


def test_boost_on_sets_12_h_and_off_removes_it() -> None:
    # SetBoost("tesla", True) → boost_until == T_APP + 12 h; SetBoost("tesla", False) → None


def test_assignment_moves_the_override() -> None:
    # tesla.wallbox "evcs" (aktiv), SetAssignment("evcs", "egolf") → egolf.wallbox "evcs", tesla.wallbox None;
    # SetAssignment("evcs", None) → keine Übersteuerung auf "evcs"


async def test_conflict_reapplies_the_change_on_the_new_intent(service, fake_client) -> None:
    # erstes PUT 409; dazwischen gilt eine Absicht mit control True (von curl) → zweites PUT enthält
    # control True und die eigene Änderung (Modus), Version über der neuen


async def test_second_conflict_raises(service, fake_client) -> None:
    # zwei 409 → ConflictAgain


async def test_changes_are_serialised(service, fake_client) -> None:
    # zwei apply gleichzeitig (asyncio.gather) → zwei PUT nacheinander, das zweite auf der Absicht des ersten


async def test_rejection_and_core_down(service, fake_client) -> None:
    # 422 mit details → Rejected(details); CoreUnavailableError → CoreDown


async def test_change_is_audited(service, audit) -> None:
    # SetMode("tesla", NOW) von "admin" über "ui" → audit.recent()[0]: action "intent.mode", target "tesla",
    # details {"from": "pv", "to": "now", "version": …, "result": "accepted"}
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/app/control/test_intent.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces. `derive` vergleicht die Zeit mit `now` der app; Health kommt aus `live.health` (bis 10 s alt, nach jeder Änderung frisch).

- [ ] **Step 4: Alles laufen lassen** (wie Task 1, Step 4, ohne Vertrag)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/control tests
git commit -m "feat(app): Absicht aus der Bedienung, ohne abgelaufenen Boost und beendete Übersteuerung"
```

---

### Task 10: app – Routen der Bedienung und der Entscheidungen

**Files:**
- Create: `src/bindaems/app/control/routes.py`
- Modify: `src/bindaems/app/runtime.py`, `tests/ui_world.py`, `tests/integration/test_ui_contract.py`
- Test: `tests/unit/app/control/test_routes.py`, `tests/integration/test_app_runtime.py`

**Interfaces:**
- Consumes: `IntentService`, `DecisionStore`, `CoreClient.acknowledge`, `AuthService.check_password`, `Guard.require`, `AuditLog`
- Produces:
  - `control_router(service: IntentService, store: DecisionStore, client: CoreClient, auth: AuthService, audit: AuditLog, guard: Guard) -> APIRouter`
  - Routen (Bodies mit `extra="forbid"`; Antwort jeder Änderung `{"result", "version", "details", "intent"}`):

    | Route | Rolle | Body |
    |---|---|---|
    | `GET /api/control` | viewer | – → `{"core_connected": bool, "intent": Intent \| null}` |
    | `PUT /api/control/regulation` | operator | `{"on": bool}` |
    | `PUT /api/control/vehicles/{key}/mode` | operator | `{"mode": ChargeMode}` |
    | `PUT /api/control/vehicles/{key}/boost` | operator | `{"on": bool}` |
    | `PUT /api/control/wallboxes/{key}/vehicle` | operator | `{"vehicle": str \| null}` |
    | `PUT /api/control/guest` | operator | `{"mode": ChargeMode}` |
    | `PUT /api/control/priority` | operator | `{"priority": list[str], "surplus_priority": SurplusPriority}` |
    | `PUT /api/control/actuators` | admin | `{"modes": dict[str, ActuatorMode], "password": str \| null, "confirmed": bool}` |
    | `POST /api/control/locks/{actuator}/ack` | admin | – → `{"detail": …}` mit dem Status des core |
    | `GET /api/decisions` | viewer | Query wie `DecisionQuery` (`limit` 1 bis 500) → `{"decisions": [… mit "cursor"], "more": bool}` |
    | `GET /api/decisions/latest` | viewer | – → `{"decisions": […]}` (letzte 24 h, Reihenfolge der Aktoren aus Health) |

  - Fehler nach „Texte“: `CoreDown` 503, `ConflictAgain` 409, `Rejected` 422 mit `detail` = die Sätze mit „ “ verbunden, `UnknownTarget` 404
  - `AppRuntime`: `self.intents = IntentService(...)`, Router nach `ledger`
  - Demo-Welt: Benutzer `bedienen` (Rolle `operator`, Passwort `demo-passwort-1`); nach dem Start eine Absicht `Intent(version=1, control=True, vehicles={"tesla": VehicleIntent(mode=ChargeMode.MIN_PV)})`; die app-Tabelle `decision` bekommt `DEMO_DECISIONS` (feste Liste in `tests/ui_world.py`: je Aktor zwei, darunter eine `foreign` und eine `deferred`)
  - Vertrag: `control`, `decisions`, `decisions-latest` in `GET_ENDPOINTS`, `control-reply` (Antwort von `PUT /api/control/vehicles/tesla/mode` mit `now`) in `OTHER`

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/app/control/test_routes.py
def test_viewer_cannot_change_and_operator_can(client, auth) -> None:
    viewer = login_as(client, auth, "viewer")
    assert client.put("/api/control/vehicles/tesla/mode", json={"mode": "now"}, headers=viewer).status_code == 403
    operator = login_as(client, auth, "operator")
    response = client.put("/api/control/vehicles/tesla/mode", json={"mode": "now"}, headers=operator)
    assert response.status_code == 200 and response.json()["intent"]["vehicles"]["tesla"]["mode"] == "now"


def test_live_needs_admin_password_and_confirmation(client, auth, fake_core) -> None:
    body = {"modes": {"wallbox.evcs": "live"}, "password": PASSWORD, "confirmed": True}
    assert client.put("/api/control/actuators", json=body, headers=login_as(client, auth, "operator")).status_code == 403
    admin = login_as(client, auth, "admin", username="chef")
    wrong = client.put("/api/control/actuators", json=body | {"password": "falsch-falsch"}, headers=admin)
    assert (wrong.status_code, wrong.json()["detail"]) == (403, "Passwort falsch.")
    unconfirmed = client.put("/api/control/actuators", json=body | {"confirmed": False}, headers=admin)
    assert (unconfirmed.status_code, unconfirmed.json()["detail"]) == (422, "Bitte bestätige Prüfprotokoll Teil 2.")
    assert fake_core.submitted == []
    assert client.put("/api/control/actuators", json=body, headers=admin).status_code == 200


def test_dry_run_needs_no_password(client, auth, fake_core) -> None:
    # {"modes": {"wallbox.evcs": "dry_run"}, "password": None, "confirmed": False} als Admin → 200


def test_live_without_live_allowed_is_clamped(client, auth, fake_core) -> None:
    # der core antwortet clamped mit dem Hinweis zu live_allowed → 200, result "clamped", details mit dem Satz


def test_core_down_gives_503(client, auth, fake_core) -> None:
    # CoreUnavailableError → 503 „core nicht erreichbar.“


def test_unknown_vehicle_gives_404(client, auth) -> None:
    # PUT /api/control/vehicles/vw/mode → 404 „Unbekanntes Fahrzeug „vw“.“


def test_ack_passes_status_and_detail(client, auth, fake_core) -> None:
    # core 409 „Ursache besteht weiter: …“ → 409 mit demselben Text; Änderungsprotokoll lock.acknowledge


def test_decisions_query_parameters(client, auth, store) -> None:
    # GET /api/decisions?actuator=vehicle.tesla&dry_run=false&limit=2 → zwei, more True, je mit cursor;
    # limit=501 → 422
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/app/control/test_routes.py tests/integration/test_app_runtime.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces; die Routen folgen dem Muster von `settings/routes.py` (kein `from __future__ import annotations`, `Annotated`-Abhängigkeiten).

- [ ] **Step 4: Vertrag schreiben und alles laufen lassen**

Run: `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py -q`, dann wie Task 1, Step 4
Expected: PASS bis auf `contract.test.ts` (die neuen Dateien haben noch kein Schema; das folgt in Task 11). Der Task endet mit `uv run pytest -q` grün; die UI-Prüfung läuft am Ende von Task 11.

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests ui/src/lib/api/contract
git commit -m "feat(app): Routen für Bedienung, Live-Schaltung, Quittierung und Entscheidungen"
```

---

### Task 11: UI – Schemas, Endpunkte, Rollen und Navigation

**Files:**
- Modify: `ui/src/lib/api/schemas.ts`, `ui/src/lib/api/endpoints.ts`, `ui/src/lib/api/contract.test.ts`, `ui/src/lib/roles.ts`, `ui/src/lib/nav.ts`, `ui/src/lib/components/icons.ts`, `ui/src/lib/components/IconShapes.svelte`
- Test: `ui/src/lib/api/contract.test.ts`, `ui/src/lib/roles.test.ts`, `ui/src/lib/nav.test.ts`

**Interfaces:**
- Consumes: die Vertragsdateien aus Task 1 und 10
- Produces:
  - Schemas: `ChargeModeSchema`, `IntentSchema` (alle Felder von `Intent`), `ControlResponseSchema`, `ControlReplySchema`, `DecisionSchema` (mit `cursor` optional), `DecisionsResponseSchema`, `LatestDecisionsResponseSchema`, `AckResponseSchema`; `CoreHealthSchema` um `vehicles` (alle Felder aus Spec 5.3, Stand Plan 2c-1, dazu `commands_hour`), `wallboxes` (Plan 2c-2b, Präzisierung 11), `watchdog.armed`, `actuators[].watchdog_needed`, `locked`; `ActuatorModeSchema` exportiert
  - `api.control()`, `api.setRegulation(on)`, `api.setMode(vehicle, mode)`, `api.setBoost(vehicle, on)`, `api.setWallboxVehicle(wallbox, vehicle | null)`, `api.setGuestMode(mode)`, `api.setPriority(priority, surplus)`, `api.setActuators(modes, password, confirmed)`, `api.acknowledge(actuator)`, `api.decisions(query, load?)`, `api.latestDecisions(load?)`
  - `canOperate(user): boolean` (ab `operator`)
  - `PAGES`: `/laden` „Laden“ (`car`) nach `/`, `/entscheidungen` „Entscheidungen“ (`list`) nach `/system`; Symbol `list` in `icons.ts` und `IconShapes.svelte`
  - `SCHEMAS` um `control`, `control-reply`, `decisions`, `decisions-latest`

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/lib/roles.test.ts
it('Bedienen und Admin dürfen bedienen, Lesen nicht', () => {
  expect(canOperate({ ...user, role: 'operator' })).toBe(true);
  expect(canOperate({ ...user, role: 'admin' })).toBe(true);
  expect(canOperate({ ...user, role: 'viewer' })).toBe(false);
});

// ui/src/lib/nav.test.ts
it('zeigt Laden und Entscheidungen für alle Rollen', () => {
  expect(navItems('viewer').map((i) => i.href)).toEqual(
    ['/', '/laden', '/verlauf', '/verbraucher', '/system', '/entscheidungen', '/einstellungen', '/konto']);
});

// ui/src/lib/api/contract.test.ts
it('Gesundheitsbericht nennt Fahrzeuge, Wallboxen und den scharfen Watchdog', () => {
  const health = v.parse(SystemResponseSchema, system).core.health!;
  expect(health.watchdog.armed).toBe(false);
  expect(health.wallboxes.map((w) => w.id)).toEqual(['evcs', 'twc']);
  expect(health.actuators.find((a) => a.id === 'vehicle.tesla')?.watchdog_needed).toBe(false);
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm test -- --run src/lib/api src/lib/roles.test.ts src/lib/nav.test.ts`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces.

- [ ] **Step 4: UI-Prüfungen**

Run: `cd ui && pnpm check && pnpm lint && pnpm test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat(ui): Schemas und Endpunkte der Bedienung, Rolle Bedienen, Navigation"
```

---

### Task 12: UI – Seite „Laden“

**Files:**
- Create: `ui/src/lib/charging/view.ts`, `ui/src/lib/charging/VehicleCard.svelte`, `ui/src/lib/charging/WallboxCard.svelte`, `ui/src/lib/charging/PriorityCard.svelte`, `ui/src/routes/laden/+page.svelte`
- Test: `ui/src/lib/charging/view.test.ts`, `ui/src/routes/laden/page.test.ts`

**Interfaces:**
- Consumes: `api.system`, `api.control`, `api.limits`, die Änderungen aus Task 11, `canOperate`, `Resource`
- Produces (Präzisierung 12, Texte):
  - `modeLabel(mode)`, `assignmentLabel(kind)`, `indicatorText(indicator: {vehicle, clue, supports}, names): string` (mit dem Namen des Fahrzeugs davor: „Tesla Model 3: Strom passt zu Tessie“), `occupantText(wallbox, names)`, `surplusLabel(priority)`, `moveUp(order, key)`, `moveDown(order, key)` in `view.ts`
  - Die Seite lädt `system` (15 s), `control` (15 s) und `limits` (einmal); nach jeder Änderung `control` sofort und `system` nach 2 s neu; ein Fehler steht als `Notice` an der Karte (Text aus `ApiError.detail`), `clamped` als Hinweis mit den `details`
  - Ohne `canOperate` keine Bedienelemente, die Werte bleiben sichtbar

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/lib/charging/view.test.ts
it('beschreibt Indizien als Sätze', () => {
  expect(indicatorText({ vehicle: 'tesla', clue: 'current', supports: false }, names))
    .toBe('Tesla Model 3: Strom passt nicht zu Tessie');
});
it('verschiebt im Vorrang', () => {
  expect(moveUp(['tesla', 'egolf'], 'egolf')).toEqual(['egolf', 'tesla']);
  expect(moveUp(['tesla', 'egolf'], 'tesla')).toEqual(['tesla', 'egolf']);
});

// ui/src/routes/laden/page.test.ts
it('Bedienen wählt einen Lademodus', async () => {
  // Auswahl „Lademodus“ der Karte „Tesla Model 3“ auf „Sofort“ → api.setMode('tesla', 'now'), danach api.control erneut
});
it('Boost zeigt „Boost beenden“ mit Uhrzeit, solange er gilt', async () => {
  // Health mit boost_until 2026-10-09T12:00Z → Text „bis 14:00“ und Taste „Boost beenden“
});
it('Lesen sieht keine Bedienelemente', async () => {
  // Rolle viewer → keine combobox, keine Taste „Boost“
});
it('Hinweis bei ausgeschaltetem Regelbetrieb', async () => {
  // intent.control false → „Regelbetrieb ist aus. …“ und Taste „Regelbetrieb einschalten“ → api.setRegulation(true)
});
it('zeigt eine Ablehnung des core an der Karte', async () => {
  // setWallboxVehicle wirft ApiError(422, 'Wallbox „twc“ ist mehreren Fahrzeugen zugeordnet.') → Notice mit dem Text
});
it('Zuordnung je Wallbox mit Indizien', async () => {
  // Wallbox „evcs“: „Tesla Model 3“, „erkannt“, Liste der Indizien; Auswahl „Zuordnung“ mit „Automatisch“, Fahrzeugen
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm test -- --run src/lib/charging src/routes/laden`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces; Karten im Raster der Übersicht (`repeat(auto-fit, minmax(min(100%,20rem),1fr))`).

- [ ] **Step 4: UI-Prüfungen** (wie Task 11, Step 4)

- [ ] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat(ui): Seite Laden mit Modus, Boost, Zuordnung, Gast und Vorrang"
```

---

### Task 13: UI – Seite „Entscheidungen“ und „Warum?“

**Files:**
- Create: `ui/src/lib/decisions/view.ts`, `ui/src/lib/decisions/DecisionTable.svelte`, `ui/src/lib/decisions/WhyCard.svelte`, `ui/src/routes/entscheidungen/+page.svelte`
- Modify: `ui/src/routes/+page.svelte`
- Test: `ui/src/lib/decisions/view.test.ts`, `ui/src/routes/entscheidungen/page.test.ts`, `ui/src/routes/layout-dashboard.test.ts` (oder der bestehende Test der Übersicht)

**Interfaces:**
- Consumes: `api.decisions`, `api.latestDecisions`, `api.system` (Namen der Aktoren aus `actuators[].label`)
- Produces (Präzisierung 13):
  - `resultLabel(result)`, `rangeQuery(range: 'hour' | 'today' | 'week' | {date: string}, now: Date): {since: string, until?: string}` (Wiener Zeit, „heute“ ab 0 Uhr Wien), `ageText` aus `system/view.ts` weiterverwendet
  - Filter in der URL (`?range=…&actuator=…&result=…&dry_run=…&q=…`), damit ein Link aus „Warum?“ gefiltert öffnet
  - „Warum?“ als dritte Karte der Übersicht, lädt alle 30 s

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/lib/decisions/view.test.ts
it('heute beginnt um 0 Uhr Wiener Zeit', () => {
  expect(rangeQuery('today', new Date('2026-10-09T08:00:00Z')).since).toBe('2026-10-08T22:00:00.000Z');
});

// ui/src/routes/entscheidungen/page.test.ts
it('filtert nach Aktor ohne Dry-Run', async () => {
  // Auswahl „Aktor“ = „Tesla Model 3“, „Dry-Run zeigen“ aus → api.decisions mit {actuator: 'vehicle.tesla', dry_run: false}
});
it('lädt ältere mit dem Cursor', async () => {
  // more true → Taste „Ältere laden“ → api.decisions mit before = cursor der letzten Zeile; die Zeilen hängen an
});
it('zeigt den leeren Zeitraum', async () => {
  // keine Entscheidungen → „Keine Entscheidungen im Zeitraum.“
});

// Übersicht
it('Warum? nennt je Aktor die jüngste Entscheidung', async () => {
  // latestDecisions mit zwei Einträgen → Karte „Warum?“ mit beiden Texten und „Alle Entscheidungen“ (Link /entscheidungen)
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm test -- --run src/lib/decisions src/routes`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces; die Tabelle scrollt bei 360 px in sich (`.scroll` wie auf der System-Seite).

- [ ] **Step 4: UI-Prüfungen** (wie Task 11, Step 4)

- [ ] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat(ui): Seite Entscheidungen mit Filtern und Warum? auf der Übersicht"
```

---

### Task 14: UI – Regelbetrieb, Live-Schalter und Quittierung auf der System-Seite

**Files:**
- Create: `ui/src/lib/system/ControlCard.svelte`, `ui/src/lib/system/LiveDialog.svelte`
- Modify: `ui/src/routes/system/+page.svelte`, `ui/src/lib/system/view.ts`
- Test: `ui/src/routes/system/page.test.ts`, `ui/src/lib/system/view.test.ts`

**Interfaces:**
- Consumes: `api.setRegulation`, `api.setActuators`, `api.acknowledge`, `api.control`, Health (`actuators`, `watchdog.armed`, `intent`), `Dialog`, `canOperate`, `hasRole`
- Produces (Präzisierung 4, Texte):
  - `liveConditions(actuator, health, intent): {label: string, met: boolean}[]` in `view.ts`
  - `ControlCard` (Karte „Bedienung“ vor „Aktoren“): Schalter „Regelbetrieb“ (ab Bedienen), Tasten „Live schalten…“ und „Auf Dry-Run…“ (nur Admin)
  - `LiveDialog`: Auswahl der Aktoren (nur `live_allowed` wählbar), Bedingungen je Aktor, die Häkchen, Passwort, Fehlertext aus `ApiError.detail`; Esc oder „Abbrechen“ ändert nichts
  - Spalte „Hinweise“ der Karte „Aktoren“: Taste „Quittieren“ bei einer Sperre (nur Admin); die Antwort als Hinweis unter der Tabelle
  - Watchdog-Karte: Zeile „Scharf“ (`ja`/`nein`)
  - Die System-Seite wächst über 500 Zeilen; die neuen Teile gehören darum in die beiden Komponenten (Übergabe, „Bewusst offen gelassen“, Phase 2a)

- [ ] **Step 1: Failing Tests schreiben**

```ts
// ui/src/routes/system/page.test.ts
it('Admin schaltet EVCS und Tesla gemeinsam live', async () => {
  // „Live schalten…“ → Dialog; Häkchen „EVCS „evcs““ und „Tesla Model 3“, beide Bestätigungen, Passwort →
  // api.setActuators({'wallbox.evcs': 'live', 'vehicle.tesla': 'live'}, 'demo-passwort-1', true)
});
it('ohne beide Häkchen bleibt „Live schalten“ gesperrt', async () => {
  // Taste disabled, solange ein Häkchen fehlt oder kein Aktor gewählt ist
});
it('Aktor ohne live_allowed ist nicht wählbar', async () => {
  // victron live_allowed false → Häkchen disabled, Bedingung „live erlaubt: nein“
});
it('falsches Passwort bleibt im Dialog', async () => {
  // setActuators wirft ApiError(403, 'Passwort falsch.') → Text im Dialog, Dialog offen
});
it('Bedienen schaltet den Regelbetrieb, sieht aber keinen Live-Schalter', async () => {
  // Rolle operator → Schalter „Regelbetrieb“ da, „Live schalten…“ fehlt, „Quittieren“ fehlt
});
it('Quittieren zeigt die Antwort des core', async () => {
  // gesperrte EVCS → „Quittieren“ → api.acknowledge('wallbox.evcs'); ApiError(409, 'Ursache besteht weiter: …') als Hinweis
});
```

- [ ] **Step 2: Tests laufen lassen**

Run: `cd ui && pnpm test -- --run src/routes/system src/lib/system`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces; der Dialog folgt dem Muster von `routes/benutzer/+page.svelte` (Tasten unten, `$effect` beim Schließen).

- [ ] **Step 4: UI-Prüfungen** (wie Task 11, Step 4)

- [ ] **Step 5: Commit**

```bash
git add ui/src
git commit -m "feat(ui): Regelbetrieb, Live-Schalter mit Bestätigung und Quittierung auf der System-Seite"
```

---

### Task 15: UI – E2E

**Files:**
- Create: `ui/tests/e2e/control.spec.ts`
- Modify: `ui/tests/e2e/pages.spec.ts`, `ui/tests/e2e/helpers.ts`

**Interfaces:**
- Consumes: Demo-Welt aus Task 10 (Benutzer `bedienen`, Absicht, `DEMO_DECISIONS`)
- Produces: `PAGES` um `/laden` und `/entscheidungen`; E2E für Bedienung, Live-Dialog und Entscheidungen

- [ ] **Step 1: Tests schreiben**

```ts
// ui/tests/e2e/control.spec.ts
test('Bedienen stellt den Tesla auf Sofort', async ({ page }) => {
  await login(page, 'bedienen');
  await page.goto('/laden');
  // Lademodus „Sofort“ → nach dem Neuladen der Seite steht „Sofort“; /protokoll als admin zeigt intent.mode
});
test('Admin öffnet den Live-Dialog und bricht ab', async ({ page }) => {
  // /system → „Live schalten…“ → Dialog mit Bedingungen → „Abbrechen“ → Aktoren unverändert Dry-Run
});
test('Entscheidungen filtern', async ({ page }) => {
  // /entscheidungen → Ergebnis „von außen geändert“ → genau eine Zeile aus DEMO_DECISIONS
});
```

- [ ] **Step 2: Laufen lassen**

Run: `cd ui && pnpm build && pnpm e2e` und mit der Schrift der CI: `FONTCONFIG_FILE=$PWD/tests/e2e/fonts-ci.conf pnpm e2e`
Expected: PASS, auch „alle Seiten bei 360 px …“ und „Bedienelemente … 44 × 44 px“ für die neuen Seiten. Scheitert die Breite, wird die Seite angepasst, nicht der Test.

- [ ] **Step 3: Commit**

```bash
git add ui/tests
git commit -m "test(ui): E2E für Laden, Live-Dialog und Entscheidungen"
```

---

### Task 16: HA – Bedienelemente und Befehle

**Files:**
- Create: `src/bindaems/app/ha/commands.py`
- Modify: `src/bindaems/app/ha/entities.py`, `src/bindaems/app/ha/publisher.py`, `src/bindaems/app/runtime.py`
- Test: `tests/unit/app/ha/test_entities.py`, `tests/unit/app/ha/test_publisher.py`, `tests/unit/app/ha/test_commands.py`

**Interfaces:**
- Consumes: `IntentService.apply`, `IntentService.cached`, `IntentService.current`, `LiveState.health`, `HaNotifier.reject` (Task 17; bis dahin ein Protokoll `Rejections` mit `async reject(text: str)`)
- Produces (Präzisierung 15):
  - `EntitySpec.component: Literal["sensor", "binary_sensor", "select", "switch", "button"]`; neue Felder `options: tuple[str, ...] = ()` und `command: bool = False`; `discovery` setzt bei `command` `command_topic` `{base}/cmd/<oid>`, bei `select` `options`, bei `switch` `payload_on` `ON` und `payload_off` `OFF`, bei `button` `payload_press` `PRESS`
  - `HaInputs` um `intent: Intent | None`, `vehicles: list[dict[str, Any]]`, `wallboxes: list[dict[str, Any]]` (aus Health)
  - `build_entities(cfg)` um die Entitäten aus Präzisierung 15; Zustände nach „Texte“
  - `allowed_topic` um `select`, `switch`, `button` und `{base}/cmd/<id>`
  - `HaPublisher(..., commands: CommandHandler | None = None)` mit `CommandHandler = Callable[[str, str], Awaitable[None]]`: abonniert `{base}/cmd/+` und reicht `(object_id, payload)` weiter; danach `republish(object_id)`
  - `HaCommandHandler(cfg, intents, rejections, clock)`: `async __call__(object_id: str, payload: str) -> None`; Zuordnung Entität → `Change` (Optionen über die Texte zurück auf `ChargeMode` bzw. Fahrzeug-Schlüssel), `actor="home-assistant"`, `source="ha"`; höchstens ein Befehl je Sekunde und Entität (`RATE_S: Final = 1.0`)
  - `AppRuntime`: `_ha_inputs_loop` ruft zusätzlich `intents.current()` (fehlertolerant) und baut `HaInputs` mit der Absicht

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/app/ha/test_entities.py
def test_discovery_of_the_mode_select(cfg) -> None:
    spec = entity(cfg, "tesla_mode")
    payload = discovery(spec, cfg.homeassistant.mqtt)
    assert payload["command_topic"] == "bindaems/cmd/tesla_mode"
    assert payload["options"] == ["PV-Überschuss", "Min+PV", "Sofort", "Aus"]


def test_states_follow_the_intent(cfg) -> None:
    inputs = sample_inputs(intent=Intent(version=3, control=True, vehicles={"tesla": VehicleIntent(mode=ChargeMode.NOW)}))
    assert state(cfg, "tesla_mode", inputs) == "Sofort"
    assert state(cfg, "regulation", inputs) == "ON"
    assert state(cfg, "evcs_assignment", inputs) == "Automatisch"


def test_assignment_unclear_problem(cfg) -> None:
    # vehicles[].assignment "unclear" → problem_assignment_unclear "ON"


# tests/unit/app/ha/test_commands.py
async def test_select_sets_the_mode(handler, intents) -> None:
    await handler("tesla_mode", "Sofort")
    assert intents.applied == [(SetMode("tesla", ChargeMode.NOW), "home-assistant", "ha")]


async def test_invalid_option_is_rejected_and_state_republished(handler, intents, rejections) -> None:
    await handler("tesla_mode", "Turbo")
    assert intents.applied == []
    assert rejections.texts == ["Befehl aus Home Assistant abgelehnt: Unbekannte Option „Turbo“ für „Lademodus“."]


async def test_more_than_one_command_per_second_is_dropped(handler, intents, clock) -> None:
    await handler("regulation", "ON"); await handler("regulation", "OFF")
    assert len(intents.applied) == 1
    clock.advance(1.0); await handler("regulation", "OFF")
    assert len(intents.applied) == 2


async def test_core_rejection_becomes_a_notification(handler, intents, rejections) -> None:
    # intents.apply wirft Rejected(["Wallbox „twc“ ist mehreren Fahrzeugen zugeordnet."]) → Meldung mit dem Satz


# tests/unit/app/ha/test_publisher.py
async def test_commands_are_subscribed_and_forwarded(publisher_env) -> None:
    # eingehend "bindaems/cmd/regulation" = "ON" → Handler ("regulation", "ON"), danach Zustand von "regulation" neu


def test_allowed_topic_accepts_command_platforms(cfg) -> None:
    # homeassistant/select/bindaems/tesla_mode/config und bindaems/cmd/tesla_mode erlaubt; homeassistant/light/… nicht
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/app/ha -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces. Texte für unbekannte Optionen und Nutzlasten: `Unbekannte Option „{p}“ für „{Name}“.` bzw. `Unbekannter Befehl „{p}“ für „{Name}“.`

- [ ] **Step 4: Alles laufen lassen** (wie Task 1, Step 4, ohne Vertrag)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests
git commit -m "feat(app): Bedienelemente in Home Assistant für Lademodus, Boost, Zuordnung, Gast und Regelbetrieb"
```

---

### Task 17: HA – Benachrichtigungen

**Files:**
- Create: `src/bindaems/app/ha/notify.py`
- Modify: `src/bindaems/shared/config.py`, `src/bindaems/app/runtime.py`, `deploy/config.example.yaml`
- Test: `tests/unit/app/ha/test_notify.py`, `tests/unit/shared/test_config.py`

**Interfaces:**
- Consumes: `LiveState.alarms`, `LiveState.subscribe`, `Secrets.ha_token`, `HomeAssistantConfig.url`, `Clock`
- Produces (Präzisierung 14):
  - `HomeAssistantConfig.notify_service: Annotated[str | None, Field(pattern=r"^notify\.[a-z0-9_]+$")] = None`
  - `HaServiceCall = Callable[[str, str, dict[str, Any]], Awaitable[None]]` (Domain, Dienst, Daten)
  - `websocket_service_call(url: str, token: SecretStr) -> HaServiceCall`: je Aufruf eine Verbindung `ws(s)://…/api/websocket`, `auth`, `call_service`, Antwort prüfen, schließen; Zeitüberschreitung 10 s
  - `NOTIFY_AFTER_S: Final = 30.0`, `MAX_PER_MINUTE: Final = 10`
  - `HaNotifier(call: HaServiceCall, notify_service: str | None, clock)`: `async alarm_raised(alarm_id: str, text: str)`, `async alarm_cleared(alarm_id: str)`, `async reject(text: str)`; die Begrenzung je Minute
  - `AlarmNotifications(live, notifier, clock)`: `async run()`: prüft jede Sekunde `live.alarms`; ein Alarm mit `severity == "error"`, der `NOTIFY_AFTER_S` besteht, → `alarm_raised` (einmal je Auftreten); verschwindet er nach einer Meldung → `alarm_cleared`
  - `AppRuntime`: mit Abschnitt `homeassistant` und `ha_token` die Jobs `ha-notify`; sonst eine Komponente `ha-notify` mit `ok=False` und der Meldung `Benachrichtigungen aus: BINDAEMS_HA_TOKEN fehlt.` bzw. ohne Abschnitt keine Komponente
  - `deploy/config.example.yaml`: `notify_service: notify.mobile_app_handy` auskommentiert unter `homeassistant`

- [ ] **Step 1: Failing Tests schreiben**

```python
# tests/unit/app/ha/test_notify.py
async def test_error_alarm_is_sent_after_30_s(env) -> None:
    # Alarm {"id": "competitor.foreign.wallbox.evcs", "severity": "error", "text": "…"} ab T0; bei T0+29 s keine
    # Meldung, bei T0+30 s: ("persistent_notification", "create", {"notification_id":
    # "bindaems_competitor_foreign_wallbox_evcs", "title": "BindaEMS", "message": "…"}) und
    # ("notify", "mobile_app_handy", {"title": "BindaEMS", "message": "…"})


async def test_short_alarm_is_not_sent(env) -> None:
    # Alarm 20 s, dann weg → keine Meldung, kein dismiss


async def test_cleared_alarm_dismisses_the_notification(env) -> None:
    # nach der Meldung verschwindet der Alarm → ("persistent_notification", "dismiss", {"notification_id": …})


async def test_warnings_are_not_sent(env) -> None:
    # severity "warning" 60 s → keine Meldung


async def test_rate_limit_per_minute(env, caplog) -> None:
    # 12 Fehler-Alarme gleichzeitig → 10 Meldungen, zwei Log-Zeilen


async def test_rejection_replaces_one_notification(env) -> None:
    # zweimal reject → zweimal create mit notification_id "bindaems_ha_command"


async def test_service_call_failure_is_logged_and_retried_with_the_next_alarm(env, caplog) -> None:
    # call wirft → Log-Zeile, der Lauf geht weiter


# tests/unit/shared/test_config.py
def test_notify_service_pattern(tmp_path) -> None:
    # "notify.mobile_app_handy" gültig, "light.kueche" → ValidationError
```

- [ ] **Step 2: Tests laufen lassen**

Run: `uv run pytest tests/unit/app/ha/test_notify.py tests/unit/shared/test_config.py -q`
Expected: FAIL

- [ ] **Step 3: Umsetzen** wie in den Interfaces; `websocket_service_call` mit `websockets.asyncio.client.connect` wie im core-Adapter, getestet gegen einen kleinen Server in `tests/unit/app/ha/test_notify.py` (Antworten `auth_required`, `auth_ok`, `result`).

- [ ] **Step 4: Alles laufen lassen** (wie Task 1, Step 4, ohne Vertrag)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems deploy/config.example.yaml tests
git commit -m "feat(app): Benachrichtigungen in Home Assistant bei Alarmen und abgelehnten Befehlen"
```

---

### Task 18: Doku und Abdeckung

**Files:**
- Modify: `docs/betrieb.md`, `docs/superpowers/specs/2026-10-08-bindaems-design.md`, `docs/uebergabe.md`, `README.md` (Demo-Zugang `bedienen`)
- Test: `tests/unit/test_docs.py`

**Interfaces:**
- Consumes: alles Vorige
- Produces:
  - **Betriebshandbuch:**
    - 12 „Web-UI“: Seiten „Laden“ und „Entscheidungen“, „Warum?“, die Rolle „Bedienen“ mit ihren Rechten
    - 13: „Absicht und Regelbetrieb“ (die app sendet die Absicht; `PUT /v1/intent` bleibt für Notfälle und mischt sich über die Version korrekt ein), „Sperren“ (Quittierung, Eintrag der fremden Änderung), „Dry-Run und live“ (Live-Schalter mit Dialog, Bedingungen, Passwort; Live-Freigabe zurücknehmen über „Auf Dry-Run“), „Entscheidungslog“ (Tabelle in der app, 180 Tage, Grafana mit Beispielabfrage `SELECT "text" FROM "raw"."decision" WHERE $timeFilter AND "dry_run" = 'false'`)
    - 7 „Home Assistant“: die neuen Entitäten, Befehle, Benachrichtigungen (`notify_service`, `BINDAEMS_HA_TOKEN` für die app)
    - „Laderegelung“: „Absicht per PUT /v1/intent“ als Weg ohne app; Gründe-Tabelle um `self_start`; „Tesla“ um Reserve, „Aus“ sofort, Selbststart, Ansteckerkennung; „Hinweise für die Anlage“: Punkte 1 bis 3 als erledigt, Punkt 5 und 8 mit der Entscheidung; „Umstieg von evcc“ mit dem Live-Schalter
    - „Akku und Spitzen“: Tabelle der Einstellungen um `tesla_reserve_commands`, `tesla_against_s`, `tesla_against_retry_s`
  - **`tests/unit/test_docs.py`:** `CHARGING_REASONS` um `self_start`; `BATTERY_SETTINGS` um die drei Einstellungen; ein Test, dass Abschnitt 12 „Laden“ und „Entscheidungen“ nennt und Abschnitt 7 die Bedienelemente
  - **Spec:** Absätze „Stand Plan 2d“ in 5.3 (Absicht aus der app, Version, Ableitung, Quittierung `POST /v1/locks/{actuator}/ack`), 7.9 (Quittierung, Eintrag im Log), 7.10 (Ergebnis `foreign`, Gründe), 8.7 (Live-Schalter), 11.3 (Tabelle `decision`), 12 (Entitäten in 2d, Benachrichtigungen), 13 (Seiten), 18 (2d umgesetzt; Ziel-SOC und Abfahrt in Phase 4)
  - **Übergabe:** Kurzfassung und Stand, Testzahl, Rulings und aufgeschobene Kleinigkeiten, „Offen an der Anlage“ mit den Schritten zum Ausrollen von 2d (Migration läuft beim Start der app, `BINDAEMS_HA_TOKEN` für die app, `notify_service`, Benutzer mit Rolle „Bedienen“ für HA nicht nötig), die Liste „Vor der Live-Schaltung“ mit dem neuen Stand

- [ ] **Step 1: Failing Test schreiben**

```python
# tests/unit/test_docs.py
CHARGING_REASONS = {..., "self_start"}  # die bestehende Menge, um den neuen Grund ergänzt
BATTERY_SETTINGS = {..., "tesla_reserve_commands", "tesla_against_s", "tesla_against_retry_s"}


def test_operations_guide_describes_the_operation() -> None:
    web = _guide_section("12. Web-UI")
    for topic in ("Laden", "Entscheidungen", "Warum?", "Bedienen"):
        assert topic in web
    ha = _guide_section("7. Home Assistant über MQTT")
    for topic in ("Lademodus", "Regelbetrieb", "Boost", "Zuordnung", "notify_service"):
        assert topic in ha
```

- [ ] **Step 2: Test laufen lassen**

Run: `uv run pytest tests/unit/test_docs.py -q`
Expected: FAIL

- [ ] **Step 3: Doku schreiben** wie in den Interfaces.

- [ ] **Step 4: Alles laufen lassen**

Run:
```bash
export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS
uv run ruff format --check . && uv run ruff check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports
uv run pytest -q --cov --cov-report=term --cov-fail-under=75
uv run coverage report --include="src/bindaems/core/safety/*,src/bindaems/core/control/*,src/bindaems/core/actuators/*" --fail-under=90
cd ui && pnpm check && pnpm lint && pnpm test && pnpm build && pnpm e2e
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docs README.md tests/unit/test_docs.py
git commit -m "docs: Bedienung in UI und HA, Tesla vor der Live-Schaltung, Stand Plan 2d"
```

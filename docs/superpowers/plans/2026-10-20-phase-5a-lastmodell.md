# Phase 5a – Lastmodell und Prognosegüte: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Lastprognose lernt aus Wetter, Kalender, jüngster Last und ausgewählten HA-Sensoren und wird nur dann verwendet, wenn sie nachweislich besser ist:
- Ein LightGBM-Modell sagt die Hauslast je Viertelstunde bis 72 h voraus; es wird jede Nacht neu trainiert und versioniert abgelegt.
- Ein Backtest mit rollierendem Startpunkt über bis zu 8 Wochen vergleicht Modell und Profil v1.
- Beide Verfahren rechnen ständig mit (Schattenbetrieb); der Planer bekommt das Modell nur, wenn es in den letzten 14 Tagen mindestens 10 % niedrigeren mittleren absoluten Fehler hat, sonst automatisch das Profil.
- Die Temperatur kommt aus einem Wetterarchiv (Open-Meteo, einmal 92 Tage nachgeholt), weitere Merkmale aus bis zu 8 HA-Sensoren, die der Betreiber im UI auswählt.
- Eine eigene Seite „Prognosen“ zeigt Prognosegüte, Verbesserung gegenüber dem Profil, Backtest, Merkmale und Stand des Modells.

**Architecture:** 5a ändert nur app, UI, `shared/settings.py`, Tests und `deploy/Dockerfile.app`; der core, `shared/control.py`, `shared/plan.py` und der Cerbo-Watchdog bleiben unverändert.
- **`app/learning/`** (neu, Spec 5.2 „ab Phase 5“): `weather.py` (Wetterarchiv), `ha_sensors.py` (HA-Sensoren als Merkmale), `features.py` (Merkmalsbasis, rein), `model.py` (LightGBM, rein), `backtest.py` (rein), `activation.py` (Aktivierungsregel, rein), `registry.py` (SQLite und Dateien), `service.py` (Nachtlauf, Vorhersage, Status), `routes.py`.
- **`app/forecast/`:** `load.py` gibt Profil und Modell als Schatten ins Archiv und das aktive Verfahren als `load` aus; `openmeteo.py` holt die Temperatur-Historie; `service.py` füllt das Wetterarchiv; `archive.py` und `quality.py` kennen die Arten `load_profile` und `load_model`; `routes.py` liefert Güte je Tag.
- **Migration `0013`:** `weather_slot`, `model_registry`; `forecast_record.kind` auf 16 Zeichen.
- **`shared/settings.py`:** `LearningSettings` in `RuntimeSettings` (der core liest keine Laufzeit-Einstellungen).
- **UI:** Seite „Prognosen“, Abschnitt „Lastmodell“ in den Einstellungen, Verweis aus der Karte „Prognosegüte“ der System-Seite.

**Tech Stack:** wie Plan 6a, dazu **`lightgbm>=4.6,<5`** und **`numpy>=2`** im Extra `app` (Entscheidung 3). Im Image von `ems-app` zusätzlich das Systempaket `libgomp1`. Keine neuen Schlüssel in `config.yaml`.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, vor allem 5.2, 9.5, 10.1 bis 10.3, 10.5, 11.3, 13 und 18 („Phase 5 – Lernen“).
- Verweise wie „Spec 10.3“ beziehen sich darauf. Die Absätze „Stand …“ gehen dem älteren Text vor; die Präzisierungen unten gehen beiden vor.
- Vorgänger ist Plan 6a (`docs/superpowers/plans/2026-10-19-phase-6a-klimaanlage.md`, auf `main` seit `e0a6e50`). Seine Global Constraints und die der Pläne davor gelten weiter.

## Einordnung

Phase 5 hat laut Spec 18 vier Teile: Daten (Historie, Merkmalsbasis, Sensorauswahl), Lastmodell, Fahrverhalten und die Ansicht der Prognosegüte. Dazu kommen zwei Punkte, die frühere Pläne nach Phase 5 geschoben haben: Vorheizen und Vorkühlen im Planer (Plan 6a) und die Akku-Unterstützung für PV-Autos im Optimierer, die eine Anwesenheitsprognose braucht (Plan 4b). Das ist zu viel für einen Plan; vorgeschlagen ist dieser Schnitt (Entscheidung 1):
- **5a „Lastmodell und Prognosegüte“** (dieser Plan): Daten und Merkmale, LightGBM mit Backtest, Aktivierungsregel und Rückfall, Sensorauswahl, Seite „Prognosen“. Nur app und UI.
- **5b „Fahrverhalten“** (eigener Plan danach): Abfahrt, Ankunft und SOC bei Ankunft je Auto und Wochentag lernen (Ladesessions seit 4a, Tessie `at_home` und `plugged`, HA `home_entity`), Vorschläge für Abfahrt (20. Perzentil) und Ziel-SOC mit Konfidenz, optionale Automatik für „Geplant“, Anwesenheitsprognose für den Optimierer und damit die Akku-Unterstützung für PV-Autos im MILP. Braucht einige Wochen Ladesessions; die laufen seit dem 18.10.2026.
- **5c „Vorheizen und Vorkühlen“** (später): ein Wärmemodell des Hauses aus den Daten der Klimaanlage und die Klimaanlage als flexible Last im Planer. Braucht Wochen mit Daten der Klimaanlage, also erst nach dem Ausrollen von 6a; ob 5c allein kommt oder mit 6b (Wärmepumpe), entscheidest du dann.

Weitere Punkte:
- **Freigabe:** Der Betreiber hat den Schnitt 5a/5b/5c und Plan 5a mit allen 17 Entscheidungen am 20.10.2026 freigegeben („Ja passt“). Umgesetzt wird subagent-driven auf dem Branch `claude/phase-5-plan-s7xe0n`. An der Anlage ändert sich nichts, bis du ausrollst.
- **Ausrollen:** 5a ändert nur `ems-app` (Entscheidung 2). Weil `latest` schon 4b und 6a enthält, kommt 5a erst **mit oder nach** dem gemeinsamen Ausrollen von 4b und 6a an die Anlage, also nach der Abnahme der Phase 3 (Punkt 10 deiner Anlagenliste). Danach laufen die 4 Wochen der Abnahme (Entscheidung 16).
- **Wirkung:** Die Lastprognose ist nur eine Eingabe der Planung (Spec 10.5). Solange Victron im Dry-Run ist, rechnet der core den Plan nur mit; die Wirkung zeigt sich im Zeitplan.
- **Danach:** Plan 5b.

## Entscheidungen (freigegeben am 20.10.2026)

Der Plan ist auf diese Entscheidungen geschrieben; der Betreiber hat alle 17 so freigegeben.

1. **Zuschnitt** 5a, 5b, 5c wie in der Einordnung.
   - **In 5a:** Wetterarchiv mit Nachholung, HA-Sensoren als Merkmale mit Auswahl im UI, Merkmalsbasis, LightGBM, Backtest, Registry, Aktivierungsregel, Schattenbetrieb, Rückfall, Seite „Prognosen“, Betrieb und Abnahme.
   - **Nicht in 5a:** alles zum Fahrverhalten (5b), Vorheizen und Vorkühlen (5c), Quantile P25/P75 für die Last (der Planer nutzt nur P50), ein Modell für die PV (die Kalibrierung aus 3a bleibt), neue Sensoren in Home Assistant, Grafana.
2. **Nur app** (alle Tasks). Der core und sein Image bleiben im Verhalten gleich; `shared/settings.py` bekommt einen Abschnitt, den nur die app liest. An der Anlage genügt der Neubau von `ems-app` mit Regelbetrieb an, wie bei 4a.
3. **LightGBM als neue Abhängigkeit** (Task 1), wie in Spec 10.3 festgelegt.
   - `lightgbm>=4.6,<5` und `numpy>=2` im Extra `app`. LightGBM bringt `scipy` mit; das Image von `ems-app` wird etwa 130 MB größer. LightGBM braucht `libgomp1` im Image (`python:3.12-slim` hat es nicht).
   - Feste Parameter, deterministisch, ein Thread (Präzisierung 5). Keine Suche nach Hyperparametern: bei rund 60 000 Zeilen bringt sie wenig und macht den Nachtlauf unvorhersehbar lang.
   - Alternative: ein eigenes lineares Modell ohne neue Abhängigkeit. Es kann Wechselwirkungen (Uhrzeit × Temperatur) nicht ohne Handarbeit abbilden; die Spec nennt LightGBM.
4. **Lerndaten der Hauslast** (Tasks 6, 9): dieselbe Historie wie das Profil v1 (`LoadForecastService.history`: Abrechnung, nur vollständige Slots; HA nur mit `homeassistant.load_history`). Es bleibt bei deiner Entscheidung vom 16.10.2026, ohne HA-Historie der Hauslast; falls du doch eine passende Entität hast (etwa Hausverbrauch minus beide Wallboxen), wirkt `load_history` auch für das Modell.
   - **Fenster:** 56 Tage wie das Profil.
   - **Mindestdaten:** trainiert wird erst ab **28 Tagen** mit je mindestens 80 von 96 vollständigen Slots; vorher bleibt das Profil, und die Seite zeigt „Zu wenig Daten: {n} von 28 Tagen.“ Die Abrechnung läuft an der Anlage seit Phase 1b; beim Ausrollen reicht das.
5. **Merkmale** (Task 4), je Ziel-Slot und Ausgabezeitpunkt (Spec 10.3):
   - Kalender: Uhrzeit (Minute des lokalen Tages), Wochentag, Tagestyp (Werktag, Samstag, Sonn- und Feiertag wie das Profil), Feiertag, Monat.
   - Vorlauf in Stunden (`lead_h`): ein Modell für alle Vorläufe bis 72 h.
   - Temperatur: im Ziel-Slot und als Mittel des lokalen Tages.
   - Verzögerte Last: derselbe lokale Slot vor einem Tag (nur wenn vor der Ausgabe vollständig, sonst leer) und vor sieben Tagen; die mittlere Last der letzten Stunde vor der Ausgabe.
   - Das Profil v1 selbst als Merkmal (`profile_w`): Das Modell lernt so die Abweichungen vom Profil, etwa an kalten Tagen.
   - Ausgewählte HA-Sensoren: ihr Wert zum Ausgabezeitpunkt (Entscheidung 7).
   - Fehlende Werte bleiben leer (`NaN`); LightGBM kommt damit zurecht.
6. **Temperatur aus einem Wetterarchiv** (Task 2).
   - Neue Tabelle `weather_slot`. Jede PV-Prognose schreibt die Temperatur der Slots bis 6 h nach ihrer Ausgabe (inklusive des Vortags aus `past_days=1`); spätere Ausgaben ersetzen frühere. So steht für die Vergangenheit der beste bekannte Wert.
   - **Nachholung:** Ist das Archiv beim Start jünger als 56 Tage, holt die app einmal 92 Tage Temperatur von Open-Meteo (`past_days=92`, nur `temperature_2m`, am Standort).
   - Im Training gilt die archivierte Temperatur (nahe am Ist), in der Vorhersage die aktuelle Prognose. Das macht den Backtest etwas zu günstig; der Schattenbetrieb misst ehrlich (Entscheidung 11).
   - Alternative: die gemessene Außentemperatur aus HA (`sensor.aussentemperatur`). Für die Zukunft gibt es sie nicht; als HA-Sensor (Entscheidung 7) kannst du sie trotzdem als Wert zum Ausgabezeitpunkt dazunehmen.
7. **HA-Sensoren als Merkmale** (Tasks 3, 12).
   - Bis zu **8** Entitäten aus der HA-Datenbank, numerisch oder mit Zustand, den HA als Zahl schreibt (an/aus, zu Hause/weg). „Anwesenheit“ (Spec 10.3) ist einer davon, etwa `person.…` oder eine Gruppe; das UI nennt das als Beispiel.
   - Wert = Mittel des letzten vollständigen Slots vor der Ausgabe; er gilt für den ganzen Vorlauf (Fortschreibung). Für die Zukunft kennt BindaEMS keine Sensorwerte.
   - Auswahl unter **Einstellungen → Lastmodell**, versioniert wie alle Laufzeit-Einstellungen. Eine Änderung wirkt ab dem nächsten Training; jedes Modell merkt sich seine eigene Merkmalsliste.
8. **Ausgabezeitpunkte und Ziel** (Tasks 4, 5).
   - Training: Ausgaben um 00:00, 06:00, 12:00 und 18:00 Uhr lokal, je Ausgabe alle Slots vom Slot der Ausgabe bis 72 h danach, die einen Ist-Wert haben. Bei 56 Tagen sind das rund 60 000 Zeilen.
   - Vorhersage: bei jeder stündlichen Ausgabe der Lastprognose (Minute 9) für dieselben Slots wie das Profil (bis Ende übermorgen).
   - Zielfunktion `l1`: das Modell schätzt den Median, wie das Profil v1 (P50).
9. **Training** (Tasks 7, 9).
   - Nachts um **03:15 Uhr** lokal (nach Kalibrierung 02:30 und Abgleich 02:45), beim Start, wenn es noch kein Modell gibt, und über die Taste „Jetzt neu trainieren“ (nur Admin).
   - Jedes Training legt eine neue Version an (Registry in SQLite, Modell als Textdatei unter `<data_dir>/models/load/`, Spec 10.3). Behalten werden die **14** neuesten Versionen und die aktive.
   - Training und Backtest laufen in einem Thread (`asyncio.to_thread`); dauern sie länger als 10 min, gilt der Lauf als gescheitert.
10. **Backtest** (Task 6), rollierender Startpunkt (Spec 10.3):
    - Startpunkte sind die lokalen Mitternächte 7, 14, … Tage vor heute; je Startpunkt trainiert ein Modell nur mit Daten davor (höchstens 56 Tage) und sagt die Ausgaben der folgenden 7 Tage voraus.
    - So viele Wochen, wie die Daten nach den ersten 14 Tagen erlauben, höchstens 8 („über 4–8 Wochen“); mindestens 2 (die letzten 14 Tage), sonst kein Backtest.
    - Verglichen wird mit dem Profil, das an demselben Tag gegolten hätte (gebaut aus den Daten vor diesem Tag, wie im Betrieb).
    - Ergebnis je Woche und Vorlaufklasse (`h0_6`, `h6_24`, `h24`): MAE von Modell und Profil, Zahl der Slots.
11. **Aktivierung** (Task 8, Spec 10.3), jede Nacht nach dem Training:
    - Aktiv ist das Modell, wenn sein MAE über die letzten 14 vollen Tage **mindestens 10 %** unter dem des Profils liegt (`1 − MAE_Modell / MAE_Profil ≥ 0,10`), über alle Vorlaufklassen zusammen.
    - **Grundlage:** der Schattenbetrieb (Entscheidung 12), sobald er an mindestens 10 dieser 14 Tage Werte hat; vorher die letzten 14 Tage des Backtests. So nutzt das Modell schon kurz nach dem Ausrollen, wenn es im Backtest klar besser ist, und der Betrieb entscheidet, sobald er genug gesehen hat.
    - Sonst gilt automatisch das Profil, ebenso ohne Modell, mit ausgeschaltetem Schalter „Lastmodell verwenden“ (Standard an) und bei jedem Fehler.
    - Keine Hysterese: Die Regel wird täglich neu angewandt, wie in der Spec. Alternative: aus erst unter 5 %, das verhindert ein Hin und Her an der Grenze.
12. **Schattenbetrieb und Ausgabe** (Task 10).
    - Jede stündliche Ausgabe rechnet Profil und Modell und schreibt beide ins Archiv (`load_profile`, `load_model`); als `load` (für den Planer, InfluxDB und die bisherige Güte) geht das aktive Verfahren.
    - Fehlt dem Modell für einzelne Slots ein Wert, nimmt die Ausgabe dort das Profil; fehlt er für mehr als 10 % der Slots oder wirft die Vorhersage, gilt für diese Ausgabe ganz das Profil.
    - **Grenzen (Spec 10.5):** Jede Vorhersage liegt zwischen 0 und dem 1,5-Fachen des höchsten Slot-Werts der Trainingsdaten.
13. **Seite „Prognosen“** (Task 13, Spec 18 „Ansicht der Prognosegüte“), in der Navigation nach „Zeitplan“, für alle Rollen:
    - **Lastprognose:** aktives Verfahren mit Grund, Modellversion (trainiert am, Daten von–bis, Slots), Ergebnis des letzten Trainings, Taste „Jetzt neu trainieren“ (Admin).
    - **Prognosegüte:** MAE, Bias und relativer MAE je Art und Vorlaufklasse für PV, Last (aktiv), Profil und Modell, dazu die Verbesserung des Modells gegenüber dem Profil (Spec 10.3 „Anzeige im UI“); Zeitraum 7, 14, 28 oder 60 Tage.
    - **Verlauf:** MAE je Tag für Profil und Modell als Linien.
    - **Backtest:** je Woche und Vorlaufklasse MAE von Modell und Profil und die Verbesserung.
    - **Merkmale:** Anteil jedes Merkmals am Gewinn des Modells, absteigend.
    - Die Karte „Prognosegüte“ auf der System-Seite bleibt und verweist auf die Seite.
14. **Sensorauswahl im UI** (Task 12): Einstellungen → „Lastmodell“: Schalter „Lastmodell verwenden“ und eine Liste der HA-Sensoren mit Auswahl aus den Entitäten der HA-Datenbank (letzte 30 Tage). Ohne HA-Datenbank steht dort der Grund.
15. **Rückfall** (Tasks 7, 9, 10): Training scheitert oder hat zu wenig Daten → die bisherige Version bleibt, die Aktivierung wird trotzdem neu bewertet (Schattenbetrieb). Modelldatei fehlt oder ist nicht lesbar (etwa nach einer Rücksicherung der Datenbank) → Version gilt als unbrauchbar, Profil, nächstes Training legt eine neue an. Die Komponente `load-model` in „Komponenten“ nennt den Zustand.
16. **Abnahme** (Task 14, Spec 18): 4 Wochen (28 Tage) ab dem Ausrollen. Bestanden, wenn auf „Prognosen“ über 28 Tage das Modell mindestens 10 % niedrigeren MAE hat als das Profil, **oder** das Profil in dieser Zeit automatisch aktiv war, sobald und solange das Modell es nicht schaffte. Dazu: jede Nacht ein Training (Registry), keine Fehler in `load-model`, der Rückfall bei fehlender Modelldatei an der Anlage beobachtet.
17. **Nichts geht von selbst live.** 5a schaltet nichts an der Anlage; es ändert nur die Eingabe des Planers.

## Präzisierungen gegenüber der Spec

1. **Migration `0013`** (`v0013_learning.py`):
   - `weather_slot(slot_start UtcDateTime PK, temp_c Float NOT NULL, issued_at UtcDateTime NOT NULL)`.
   - `model_registry(id Integer PK, kind String(16) NOT NULL = 'load', trained_at, data_from, data_to UtcDateTime NOT NULL, samples Integer, features JSON, max_w Float, file String(200), backtest JSON NULL, importance JSON, usable Boolean = true, active Boolean = false, decided_at UtcDateTime NULL, decision JSON NULL)`, Index `ix_model_registry_trained_at`.
   - `forecast_record.kind` und `forecast_metric.kind` als `String(16)` (Batch-Änderung; SQLite prüft die Länge nicht, das Schema soll stimmen).
2. **Arten im Archiv:** `Kind = Literal["pv", "load", "load_profile", "load_model"]`. `load` ist die ausgegebene Prognose (wie bisher), die beiden anderen die Schatten. `ForecastQuality.update` rechnet alle vier; Ist für alle `load*` ist `house_w`.
3. **Einstellungen** (`shared/settings.py`):
   - `HaEntityId = Annotated[str, Field(pattern=r"^[a-z_]+\.[a-z0-9_]+$")]`
   - `LearningSettings(load_model_enabled: bool = True, ha_sensors: Annotated[list[HaEntityId], Field(max_length=8)] = [])`, Einträge eindeutig („Sensoren dürfen nicht doppelt vorkommen“).
   - `RuntimeSettings.learning: LearningSettings = Field(default_factory=LearningSettings)`; gespeicherte Einstellungen ohne den Abschnitt laden mit den Standardwerten.
4. **Merkmalsnamen** (fest, in dieser Reihenfolge, dann je Sensor `ha:<entity_id>` in der Reihenfolge der Einstellungen): `lead_h`, `minute_of_day`, `weekday`, `day_type` (0 Werktag, 1 Samstag, 2 Sonn-/Feiertag), `holiday` (0/1, Feiertag nach `holidays` AT), `month`, `temp_c`, `temp_day_c`, `lag_1d_w`, `lag_7d_w`, `recent_w`, `profile_w`.
   - `lead_h = (slot − issued_at) / 1 h` (ab −0,25).
   - `lag_1d_w`, `lag_7d_w`: Last im Slot mit derselben lokalen Uhrzeit 1 bzw. 7 Kalendertage vorher; nur, wenn dieser Slot vor `slot_start(issued_at)` endet, sonst `NaN`. An Tagen der Zeitumstellung ohne passenden Slot `NaN`.
   - `recent_w`: Mittel der vorhandenen Werte der 4 Slots vor `slot_start(issued_at)`, ohne Wert `NaN`.
   - `temp_day_c`: Mittel der bekannten Temperaturen des lokalen Tages des Ziel-Slots.
   - `profile_w`: `LoadProfile.at(slot)` des Profils, das am lokalen Tag der Ausgabe gilt (gebaut aus den Daten vor diesem Tag).
   - `ha:<id>`: Mittel des Slots vor `slot_start(issued_at)` aus den Minutenmitteln (`fill(previous)`, Wert vor dem Bereich als Anfang, wie die Lastprognose aus HA); ohne Wert `NaN`.
5. **Modell** (`app/learning/model.py`): `PARAMS = {"objective": "l1", "learning_rate": 0.05, "num_leaves": 31, "min_data_in_leaf": 50, "feature_fraction": 0.9, "num_threads": 1, "deterministic": True, "force_row_wise": True, "seed": 0, "verbosity": -1}`, `ROUNDS = 300`, `MIN_ROWS = 1000`. Gespeichert wird `Booster.model_to_string()`; geladen mit `Booster(model_str=…)`. Wichtigkeit: `feature_importance("gain")`, normiert auf Summe 1.
6. **Aktivierung, Rechnung:** Je lokalem Tag `DaySums(n, model_abs_w, profile_abs_w)` über die Slots, für die beide Verfahren denselben Slot und dieselbe Vorlaufklasse haben und ein Ist-Wert vorliegt. Über die Tage des Fensters `[heute − 14, heute − 1]`: `MAE = Σ abs / Σ n`. `improvement_pct = (1 − MAE_Modell / MAE_Profil) · 100`, bei `MAE_Profil = 0` leer (dann nicht aktiv). Aktiv ab `improvement_pct ≥ 10,0`.
7. **Status des Lernens** (`GET /api/learning/load`):

   ```json
   {
     "enabled": true,
     "method": "model",
     "activation": {"active": true, "basis": "shadow", "days": 14, "model_mae_w": 212.0,
                    "profile_mae_w": 251.0, "improvement_pct": 15.5, "reason": "better",
                    "text": "…", "decided_at": "…"},
     "model": {"id": 7, "trained_at": "…", "data_from": "…", "data_to": "…", "samples": 61234,
               "features": [{"name": "temp_c", "label": "Temperatur", "importance": 0.31}]},
     "backtest": {"weeks": [{"origin": "…", "end": "…", "horizon": "h0_6", "n": 1310,
                             "model_mae_w": 190.0, "profile_mae_w": 230.0,
                             "improvement_pct": 17.4}],
                  "total": {"model_mae_w": 205.0, "profile_mae_w": 244.0, "improvement_pct": 16.0}},
     "training": {"running": false, "last_at": "…", "outcome": "trained", "text": "…"},
     "sensors": [{"entity_id": "sensor.aussentemperatur", "in_model": true}],
     "status": {"ok": true, "text": "…"}
   }
   ```

   `model`, `backtest`, `activation.decided_at` und die Werte der Aktivierung können `null` sein. `basis` ist `shadow`, `backtest` oder `none`; `reason` ist `better`, `not_better`, `too_few_days`, `disabled` oder `no_model`; `outcome` ist `trained`, `too_little_data`, `failed` oder `null` (noch kein Lauf).
8. **Güte je Tag** (`GET /api/forecast/quality/daily?days=28`, `days` 1 bis 60): `{"days": 28, "rows": [{"date": "2026-11-20", "kind": "load_model", "n": 288, "mae_w": 201.5}]}` für alle vier Arten, Vorlaufklassen zusammen, nach Datum und Art sortiert.
9. **`GET /api/forecast/quality`:** Zeilen auch für `load_profile` und `load_model`; jede Zeile hat `improvement_pct` (nur bei `load_model`, gegenüber `load_profile` derselben Vorlaufklasse, sonst `null`). `GET /api/forecast/load` hat zusätzlich `"method": "profile" | "model"`.

## Global Constraints

- Deutsch in Texten, Logs (ganze Sätze, `tests/unit/test_layout.py`), Commits im Stil `feat(app): …`; keine Modellbezeichnungen im Repo.
- `core` und `app` importieren nur aus `shared`; die Importverträge bleiben unverändert.
- `git diff main --stat -- src/bindaems/core cerbo-watchdog src/bindaems/shared/control.py src/bindaems/shared/plan.py src/bindaems/shared/config.py` ist am Ende leer.
- Abdeckung ≥ 75 % gesamt, ≥ 90 % für `core/safety`, `core/control`, `core/actuators` (unverändert); `app/learning` soll ≥ 90 % erreichen.
- Prüfkette vor jedem Commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src cerbo-watchdog/bindaems_watchdog`, `uv run lint-imports`, `uv run pytest -q --cov --cov-fail-under=75` plus der 90-%-Bericht; UI: `pnpm lint`, `pnpm check`, `pnpm test`; E2E bei UI-Änderungen. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Tests schreiben nie nach `/data`: Modelle liegen in Tests unter `tmp_path` (`app.data_dir`).
- Training ist deterministisch (Präzisierung 5); Tests vergleichen Zahlen mit Toleranz, nie Modelltexte.
- Gelernte Werte erreichen die Safety-Schicht nie und ändern keine harte Grenze (Spec 10.5).
- Kein Aktor ist live; nichts an der Anlage schalten.

## Review Focus

1. **Blick in die Zukunft (Leck).** Ein Merkmal darf nur nutzen, was zum Ausgabezeitpunkt bekannt war; der Backtest trainiert je Woche nur mit Daten vor dem Startpunkt.
   - Erwartet: `lag_1d_w` ist bei Vorlauf über 24 h leer, `recent_w` und Sensoren enden vor dem Slot der Ausgabe, das Profil im Backtest ist das des Tages.
   - Tests: Task 4 `test_no_value_after_issuance_is_used`, `test_lag_1d_is_empty_beyond_one_day`; Task 6 `test_fold_trains_only_before_origin`.
2. **Lücken und Zeitumstellung** (Tag mit 92 oder 100 Slots, Ausfall der app).
   - Erwartet: vier Ausgaben je Tag auch an Umstellungstagen; Slots ohne Ist fehlen im Training; ein Tag mit weniger als 80 Slots zählt nicht zu den 28 Tagen.
   - Tests: Task 4 `test_issuances_on_dst_days`, `test_slots_without_actual_are_skipped`; Task 9 `test_days_with_gaps_do_not_count`.
3. **HA-Sensor fehlt, ist umbenannt oder nicht numerisch.**
   - Erwartet: das Merkmal ist leer, das Training läuft, die Seite zeigt den Sensor als „nicht im Modell“, ohne Ausnahme im Log je Ausgabe.
   - Tests: Task 3 `test_unknown_entity_yields_no_values`; Task 9 `test_training_runs_when_a_sensor_has_no_data`.
4. **Modelldatei fehlt oder ist kaputt** (Rücksicherung, Datenträger voll).
   - Erwartet: Version unbrauchbar, Ausgabe mit dem Profil, Status in `load-model`, nächstes Training legt eine neue Version an.
   - Tests: Task 7 `test_missing_file_marks_version_unusable`; Task 10 `test_broken_model_falls_back_to_profile`.
5. **Auswahl der Sensoren ändert sich zwischen Training und Vorhersage.**
   - Erwartet: das Modell rechnet mit seiner eigenen Merkmalsliste; ein entfernter Sensor ist leer, ein neuer zählt erst ab dem nächsten Training.
   - Tests: Task 9 `test_model_keeps_its_feature_list_after_settings_change`.

## Texte

- **Navigation:** „Prognosen“ (Symbol `forecast`, neu), nach „Zeitplan“.
- **Seite „Prognosen“, Karten:** „Lastprognose“, „Prognosegüte“, „Fehler je Tag“, „Backtest“, „Merkmale“.
- **Karte „Lastprognose“:** „Verfahren: Profil“ bzw. „Verfahren: Modell“; „Modell vom {TT.MM.JJJJ HH:MM}“, „Daten {TT.MM.}–{TT.MM.JJJJ} · {n} Viertelstunden“; Taste „Jetzt neu trainieren“, während des Laufs „Training läuft …“; ohne Modell „Noch kein Modell trainiert.“
- **Gründe der Aktivierung (`text`):**
  - `better`: „Modell aktiv: in den letzten 14 Tagen {x} % genauer als das Profil ({Grundlage}).“ mit Grundlage „im Schattenbetrieb“ oder „im Backtest“
  - `not_better`: „Profil aktiv: Das Modell ist nur {x} % genauer, nötig sind 10 %.“; bei negativem Wert „Profil aktiv: Das Modell ist {x} % ungenauer als das Profil.“
  - `too_few_days`: „Profil aktiv: Für den Vergleich fehlen noch Tage ({n} von 10).“
  - `disabled`: „Profil aktiv: Das Lastmodell ist in den Einstellungen ausgeschaltet.“
  - `no_model`: „Profil aktiv: Es gibt noch kein brauchbares Modell.“
- **Ergebnis des Trainings (`training.text`):** `trained` „Trainiert am {TT.MM. HH:MM} mit {n} Viertelstunden.“; `too_little_data` „Zu wenig Daten: {n} von 28 Tagen.“; `failed` „Training fehlgeschlagen: {Fehler}“.
- **Karte „Prognosegüte“:** Spalten „Art“, „Vorlauf“, „Slots“, „MAE“, „Bias“, „rel. MAE“, „besser als Profil“; Arten „PV“, „Last (verwendet)“, „Last Profil“, „Last Modell“; Vorlauf „0–6 h“, „6–24 h“, „über 24 h“; Zeitraum „7 Tage“, „14 Tage“, „28 Tage“, „60 Tage“. Leer: „Noch keine Werte im Zeitraum.“
- **Karte „Fehler je Tag“:** Reihen „Profil“, „Modell“, Achse „MAE (W)“.
- **Karte „Backtest“:** Spalten „Woche ab“, „Vorlauf“, „Slots“, „Modell“, „Profil“, „besser“; Zeile „Gesamt“. Leer: „Noch kein Backtest (braucht mindestens 28 Tage Daten).“
- **Karte „Merkmale“:** Spalten „Merkmal“, „Anteil“. Namen: `lead_h` „Vorlauf“, `minute_of_day` „Uhrzeit“, `weekday` „Wochentag“, `day_type` „Tagestyp“, `holiday` „Feiertag“, `month` „Monat“, `temp_c` „Temperatur“, `temp_day_c` „Tagesmittel der Temperatur“, `lag_1d_w` „Last vor einem Tag“, `lag_7d_w` „Last vor einer Woche“, `recent_w` „Last der letzten Stunde“, `profile_w` „Profil“, `ha:<id>` die Entität.
- **Einstellungen, Abschnitt „Lastmodell“:** Schalter „Lastmodell verwenden“ mit Hilfe „Aus: Die Planung nutzt immer das Profil. Das Modell rechnet weiter mit.“; Liste „Zusätzliche Sensoren aus Home Assistant (höchstens 8)“ mit Hilfe „Zum Beispiel Anwesenheit (person.…) oder die gemessene Außentemperatur. Wirkt ab dem nächsten Training.“; ohne HA-Datenbank „HA-Datenbank nicht eingerichtet (influxdb.ha_database)“.
- **System-Seite, Karte „Prognosegüte“:** Verweis „Mehr unter Prognosen“.
- **Komponente `load-model`:** „Lastmodell aktiv“, „Profil aktiv, Modell bereit“, „Noch kein Modell“, „Lastmodell fehlgeschlagen: {Fehler}“.

---

### Task 1: Abhängigkeit, Image, Migration 0013 und Einstellungen

**Files:**
- Modify: `pyproject.toml` (Extra `app`: `lightgbm>=4.6,<5`, `numpy>=2`), `uv.lock` (`uv lock`), `deploy/Dockerfile.app` (`apt-get install -y --no-install-recommends libgomp1` vor `uv sync`, Cache aufräumen), `src/bindaems/app/db/schema.py`, `src/bindaems/shared/settings.py`, `tests/unit/test_deploy_files.py`
- Create: `src/bindaems/app/db/migrations/versions/v0013_learning.py`, `src/bindaems/app/learning/__init__.py`
- Test: `tests/unit/app/db/test_migrations.py`, `tests/unit/shared/test_settings.py`, `tests/unit/test_deploy_files.py`

**Interfaces:**
- Produces: Tabellen `weather_slot`, `model_registry` in `schema.py` (Präzisierung 1); `LearningSettings`, `HaEntityId`, `RuntimeSettings.learning` (Präzisierung 3).

- [ ] **Step 1: Write the failing tests**

```python
def test_migration_0013_creates_learning_tables(engine) -> None: ...  # weather_slot, model_registry
def test_migration_0013_keeps_forecast_records(engine) -> None: ...  # Zeilen von 0012 bleiben
def test_learning_defaults() -> None:
    assert RuntimeSettings().learning == LearningSettings(load_model_enabled=True, ha_sensors=[])
def test_at_most_eight_sensors() -> None: ...  # 9 → ValidationError
def test_sensors_must_be_unique() -> None: ...  # „Sensoren dürfen nicht doppelt vorkommen“
def test_old_settings_without_learning_load() -> None: ...
def test_app_image_has_libgomp() -> None: ...  # Dockerfile.app installiert libgomp1
def test_lightgbm_imports() -> None:
    import lightgbm  # noqa: F401
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/db tests/unit/shared/test_settings.py tests/unit/test_deploy_files.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** Migration, Schema, Einstellungen, Abhängigkeit und Image. `uv sync --extra core --extra app --extra dev` danach; `docker build -f deploy/Dockerfile.app .` lokal einmal bauen, wenn Docker da ist, sonst prüft es die CI.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/db tests/unit/shared/test_settings.py tests/unit/test_deploy_files.py -q && uv run mypy src`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml uv.lock deploy src tests
git commit -m "feat(app): Abhängigkeit LightGBM, Tabellen und Einstellungen für das Lastmodell"
```

### Task 2: Wetterarchiv und Nachholung der Temperatur

**Files:**
- Create: `src/bindaems/app/learning/weather.py`
- Modify: `src/bindaems/app/forecast/openmeteo.py`, `src/bindaems/app/forecast/service.py`, `src/bindaems/app/runtime.py`
- Test: `tests/unit/app/learning/test_weather.py`, `tests/unit/app/forecast/test_openmeteo.py`, `tests/unit/app/forecast/test_service.py`

**Interfaces:**
- Consumes: `WeatherSlot` (`openmeteo.py`), `get_raw`.
- Produces:
  - `class WeatherArchive(engine: Engine)` mit `record(slots: Sequence[WeatherSlot], issued_at: datetime) -> int` (nur Slots mit `slot_start < issued_at + 6 h`; ersetzt ältere `issued_at`), `temperatures(start: datetime, end: datetime) -> dict[datetime, float]`, `oldest() -> datetime | None`, `async backfill(fetch: Callable[[], Awaitable[dict[datetime, float]]], now: datetime) -> int` (nur wenn `oldest()` fehlt oder jünger als 56 Tage ist; schreibt mit `issued_at = now`, ersetzt keine vorhandenen Slots)
  - `async fetch_temperature_history(http: httpx.AsyncClient, clock: Clock, location: LatLon) -> RawResponse` (`minutely_15=temperature_2m`, `past_days=92`, `forecast_days=1`, sonst wie `fetch_plane`) und `parse_temperatures(body: str) -> dict[datetime, float]` (gleiche Prüfungen wie `parse_plane`, Slotbeginn = Zeitstempel − 15 min)
  - `ForecastService(…, weather: WeatherArchive | None = None)`: nach jedem erfolgreichen Abruf `record` mit den Slots der ersten Fläche; `temperatures() -> Mapping[datetime, float]` der letzten Antwort (für Task 9)

- [ ] **Step 1: Write the failing tests**

```python
def test_record_keeps_only_slots_up_to_six_hours_ahead(archive) -> None: ...
def test_newer_issue_replaces_older(archive) -> None: ...
async def test_backfill_runs_once_when_archive_is_young(archive) -> None: ...
async def test_backfill_does_not_overwrite_recorded_slots(archive) -> None: ...
async def test_backfill_failure_is_logged_and_retried_next_start(runtime) -> None: ...
def test_parse_temperatures_shifts_to_slot_start() -> None: ...
def test_parse_temperatures_rejects_wrong_unit() -> None: ...
async def test_forecast_refresh_fills_weather_archive(service, archive) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_weather.py tests/unit/app/forecast -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Entscheidung 6. Die Nachholung startet die Laufzeit einmal beim Start im Hintergrund; ein Fehler wird gewarnt („Temperatur-Historie von Open-Meteo nicht abrufbar“), die app läuft weiter. Einheit muss `°C` sein.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning tests/unit/app/forecast -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests
git commit -m "feat(app): Wetterarchiv mit Nachholung der Temperatur"
```

### Task 3: HA-Sensoren als Merkmale

**Files:**
- Create: `src/bindaems/app/learning/ha_sensors.py`
- Modify: `src/bindaems/app/consumers/values.py` (`ha_selection(refs, *, units: Sequence[str] = ("W", "kW"))`, bestehende Aufrufe unverändert)
- Test: `tests/unit/app/learning/test_ha_sensors.py`, `tests/unit/app/consumers/test_values.py`

**Interfaces:**
- Consumes: `InfluxReader`, `ha_selection`, `quote_ident`, `quote_str`.
- Produces:
  - `@dataclass(frozen=True) class SensorCandidate: entity_id: str; unit: str | None`
  - `class HaSensorReader(reader: InfluxReader | None, database: str | None)` mit
    - `async candidates() -> tuple[list[SensorCandidate], str | None]` (Entitäten mit Feld `value` in den letzten 30 Tagen aus beiden Schemata; Fehlertext wie bei den Verbrauchern, `NOT_CONFIGURED` ohne Datenbank)
    - `async slot_values(entities: Sequence[str], start: datetime, end: datetime) -> dict[str, dict[datetime, float]]` (Mittel je Slot aus den Minutenmitteln mit `fill(previous)` und dem Wert vor dem Bereich; Entitäten ohne Werte fehlen im Ergebnis)

- [ ] **Step 1: Write the failing tests**

```python
async def test_candidates_list_numeric_entities_of_both_schemas(fake_reader) -> None: ...
async def test_candidates_without_database_name_the_reason() -> None: ...
async def test_slot_values_average_minutes_and_seed_from_before(fake_reader) -> None: ...
async def test_unknown_entity_yields_no_values(fake_reader) -> None: ...
async def test_query_error_yields_empty_result_and_one_warning(fake_reader, caplog) -> None: ...
def test_ha_selection_with_extra_units() -> None: ...  # "°C" als Messung je Einheit
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_ha_sensors.py tests/unit/app/consumers -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Entscheidung 7 und Präzisierung 4 (`ha:<id>`). Die Abfrage nimmt als Messungen je Einheit die Einheiten aus `candidates` (gemerkt), sonst nur die Messung je Entität. Einheiten werden nicht umgerechnet.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning tests/unit/app/consumers -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests
git commit -m "feat(app): HA-Sensoren als Merkmale lesen"
```

### Task 4: Merkmalsbasis

**Files:**
- Create: `src/bindaems/app/learning/features.py`
- Test: `tests/unit/app/learning/test_features.py`

**Interfaces:**
- Consumes: `LoadProfile`, `LoadSample`, `build_profile` (`forecast/load.py`), `day_type` (`app/daytype.py`), `slot_start`, `LOCAL_TZ`, `SLOT`.
- Produces:
  - `ISSUE_HOURS: Final = (0, 6, 12, 18)`, `MAX_LEAD: Final = timedelta(hours=72)`, `BASE_FEATURES: Final[tuple[str, ...]]` (Präzisierung 4), `def feature_names(sensors: Sequence[str]) -> tuple[str, ...]`
  - `@dataclass(frozen=True) class FeatureInputs: load_w: Mapping[datetime, float]; temp_c: Mapping[datetime, float]; sensors: Mapping[str, Mapping[datetime, float]]; profile_for: Callable[[date], LoadProfile | None]`
  - `def issuances(start: datetime, end: datetime) -> list[datetime]` (lokale 00/06/12/18 Uhr in `[start, end)`, als UTC)
  - `def feature_rows(issued_at: datetime, slots: Sequence[datetime], inputs: FeatureInputs, names: Sequence[str]) -> list[list[float]]`
  - `@dataclass(frozen=True) class TrainingSet: rows: list[list[float]]; targets: list[float]; issued: list[datetime]; slots: list[datetime]`
  - `def training_set(inputs: FeatureInputs, start: datetime, end: datetime, names: Sequence[str]) -> TrainingSet` (Ausgaben in `[start, end)`, je Ausgabe die Slots von `slot_start(issued)` bis `issued + MAX_LEAD` mit Ist in `load_w` und Slot `< end`)
  - `def profile_cache(samples: Sequence[LoadSample]) -> Callable[[date], LoadProfile | None]` (baut je lokalem Tag mit `build_profile` nur aus Stichproben vor Mitternacht dieses Tags, gemerkt)

- [ ] **Step 1: Write the failing tests**

```python
def test_feature_names_append_sensors_in_order() -> None:
    assert feature_names(["sensor.a", "person.b"])[-2:] == ("ha:sensor.a", "ha:person.b")
def test_issuances_on_dst_days() -> None:
    # 25.10.2026 und 29.03.2026: je 4 Ausgaben zu den lokalen Uhrzeiten
def test_calendar_features_use_local_time() -> None: ...  # Feiertag 26.10. → day_type 2, holiday 1
def test_lag_1d_is_empty_beyond_one_day() -> None: ...  # Vorlauf 30 h → NaN, Vorlauf 3 h → Wert
def test_no_value_after_issuance_is_used() -> None:
    # Last, Sensoren und recent_w nach slot_start(issued) dürfen das Ergebnis nicht ändern
def test_recent_is_mean_of_last_hour() -> None: ...
def test_sensor_value_is_held_over_the_lead() -> None: ...
def test_profile_feature_is_profile_of_issue_day() -> None: ...
def test_slots_without_actual_are_skipped() -> None: ...
def test_training_set_stops_at_end() -> None: ...
def test_missing_inputs_are_nan() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_features.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Entscheidungen 5 und 8 und Präzisierung 4. Reine Rechnung ohne IO; `float("nan")` für fehlende Werte. Die lokale Verschiebung um Kalendertage rechnet über `LOCAL_TZ` und `slot_start`; ohne passenden Slot (Zeitumstellung) `NaN`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning/test_features.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/learning tests
git commit -m "feat(app): Merkmalsbasis für das Lastmodell"
```

### Task 5: Modell

**Files:**
- Create: `src/bindaems/app/learning/model.py`
- Test: `tests/unit/app/learning/test_model.py`

**Interfaces:**
- Consumes: `TrainingSet` (Task 4).
- Produces:
  - `PARAMS`, `ROUNDS`, `MIN_ROWS` (Präzisierung 5); `class TrainingError(Exception)`
  - `@dataclass(frozen=True) class TrainedModel: text: str; features: tuple[str, ...]; max_w: float` mit `predict(rows: Sequence[Sequence[float]]) -> list[float]` (begrenzt auf `[0, max_w]`, Entscheidung 12) und `importance() -> dict[str, float]` (Summe 1, ohne Gewinn alle 0)
  - `def train(data: TrainingSet, features: Sequence[str]) -> TrainedModel` (`max_w = 1,5 · max(targets)`; `TrainingError("zu wenige Zeilen: …")` unter `MIN_ROWS`)
  - `def load_model(text: str, features: Sequence[str], max_w: float) -> TrainedModel` (`TrainingError` bei nicht lesbarem Text)

- [ ] **Step 1: Write the failing tests**

```python
def test_model_learns_temperature_effect(synthetic_set) -> None:
    # Last = Profil + 40 W/K unter 15 °C: MAE des Modells < 0,5 · MAE ohne Temperatur
def test_training_is_deterministic(synthetic_set) -> None: ...  # zweimal gleiche Vorhersagen
def test_predictions_are_clamped(synthetic_set) -> None: ...  # nie < 0, nie > max_w
def test_too_few_rows_raise(synthetic_set) -> None: ...
def test_text_round_trip_predicts_the_same(synthetic_set) -> None: ...
def test_importance_sums_to_one(synthetic_set) -> None: ...
def test_broken_text_raises_training_error() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_model.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** mit `lightgbm.train(PARAMS, Dataset(numpy.asarray(rows, float), label=…, feature_name=[…]), ROUNDS)`. Merkmalsnamen für LightGBM ohne `:` (`ha:sensor.a` → `ha__sensor_a`), die Zuordnung bleibt in `features`. Die synthetischen Daten baut ein Helfer in `tests/unit/app/learning/conftest.py` (28 Tage, fester Zufallsstart), den Task 6 und Task 9 wiederverwenden.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning/test_model.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/learning tests
git commit -m "feat(app): LightGBM-Modell der Hauslast"
```

### Task 6: Backtest mit rollierendem Startpunkt

**Files:**
- Create: `src/bindaems/app/learning/backtest.py`
- Test: `tests/unit/app/learning/test_backtest.py`

**Interfaces:**
- Consumes: `FeatureInputs`, `training_set`, `feature_rows`, `issuances` (Task 4); `train` (Task 5); `horizon_class`, `HorizonClass` (`forecast/archive.py`).
- Produces:
  - `MIN_TRAIN_DAYS: Final = 14`, `MAX_WEEKS: Final = 8`, `MIN_WEEKS: Final = 2`
  - `@dataclass(frozen=True) class WeekResult: origin: datetime; end: datetime; horizon: HorizonClass; n: int; model_mae_w: float; profile_mae_w: float`
  - `@dataclass(frozen=True) class DaySums: n: int; model_abs_w: float; profile_abs_w: float` (Summen eines lokalen Tages; Task 8 nutzt sie für die Aktivierung)
  - `@dataclass(frozen=True) class BacktestResult: weeks: list[WeekResult]; daily: dict[date, DaySums]` (je lokalem Tag der Ausgabe)
  - `def rolling_backtest(inputs: FeatureInputs, names: Sequence[str], today: date, days_with_data: int) -> BacktestResult | None` (Wochen = `min(MAX_WEEKS, (days_with_data − MIN_TRAIN_DAYS) // 7)`, unter `MIN_WEEKS` `None`)

- [ ] **Step 1: Write the failing tests**

```python
def test_weeks_depend_on_available_days() -> None: ...  # 42 Tage → 4, 100 → 8, 27 → None
def test_fold_trains_only_before_origin(monkeypatch) -> None:
    # train bekommt je Woche nur Zeilen mit Slot < origin
def test_temperature_driven_load_beats_profile(synthetic_inputs) -> None:
    # Verbesserung über alle Wochen ≥ 10 %
def test_profile_like_load_does_not_beat_profile(synthetic_inputs) -> None:
    # Last = Profil + Rauschen: Verbesserung < 10 %
def test_daily_sums_cover_the_last_fourteen_days(synthetic_inputs) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_backtest.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Entscheidung 10. Je Woche: Training mit `training_set(inputs, origin − 56 d, origin, names)` und nur Zielen vor `origin`; Test mit den Ausgaben in `[origin, origin + 7 d)`, Ziele bis zur lokalen Mitternacht von `today`; das Profil je Ausgabe ist `inputs.profile_for(Tag der Ausgabe).at(slot)`. Slots ohne Profilwert zählen nicht.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning/test_backtest.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/learning tests
git commit -m "feat(app): Backtest des Lastmodells mit rollierendem Startpunkt"
```

### Task 7: Registry

**Files:**
- Create: `src/bindaems/app/learning/registry.py`
- Test: `tests/unit/app/learning/test_registry.py`

**Interfaces:**
- Consumes: `model_registry` (Task 1), `TrainedModel`, `load_model` (Task 5), `BacktestResult` (Task 6), `Activation` (Task 8, nur als JSON gespeichert).
- Produces:
  - `@dataclass(frozen=True) class RegistryEntry: id: int; trained_at: datetime; data_from: datetime; data_to: datetime; samples: int; features: tuple[str, ...]; max_w: float; backtest: dict[str, Any] | None; importance: dict[str, float]; usable: bool; active: bool; decided_at: datetime | None; decision: dict[str, Any] | None`
  - `class ModelRegistry(engine: Engine, directory: Path)` mit
    - `add(model: TrainedModel, *, trained_at: datetime, data_from: datetime, data_to: datetime, samples: int, backtest: BacktestResult | None) -> RegistryEntry` (Datei `load-<id>.txt`, erst als `.tmp` geschrieben, dann umbenannt)
    - `latest_usable() -> RegistryEntry | None`, `get(entry_id: int) -> RegistryEntry | None`
    - `load(entry: RegistryEntry) -> TrainedModel | None` (Datei fehlt oder nicht lesbar → `usable = False`, Warnung, `None`)
    - `decide(entry_id: int, activation: Activation, at: datetime) -> None` (setzt `active` nur bei dieser Zeile, alle anderen `False`)
    - `prune(keep: int = 14) -> int` (löscht ältere Zeilen und Dateien, nie die aktive)

- [ ] **Step 1: Write the failing tests**

```python
def test_add_writes_file_and_row(registry, tmp_path, model) -> None: ...
def test_load_round_trip(registry, model) -> None: ...
def test_missing_file_marks_version_unusable(registry, model) -> None: ...
def test_latest_usable_skips_unusable(registry, model) -> None: ...
def test_only_one_version_is_active(registry, model) -> None: ...
def test_prune_keeps_fourteen_and_the_active_one(registry, model) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_registry.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Entscheidung 9 und Präzisierung 1. Synchron (Aufrufer nehmen `asyncio.to_thread`). `backtest` als JSON in der Form von `GET /api/learning/load` (Präzisierung 7), ohne `daily`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning/test_registry.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/learning tests
git commit -m "feat(app): versionierte Ablage der Lastmodelle"
```

### Task 8: Aktivierungsregel

**Files:**
- Create: `src/bindaems/app/learning/activation.py`
- Test: `tests/unit/app/learning/test_activation.py`

**Interfaces:**
- Consumes: `ForecastRecord`, `Actual` (`forecast/archive.py`), `DaySums` (Task 6).
- Produces:
  - `ACTIVATION_DAYS: Final = 14`, `MIN_SHADOW_DAYS: Final = 10`, `MIN_IMPROVEMENT_PCT: Final = 10.0`
  - `Basis = Literal["shadow", "backtest", "none"]`, `Reason = Literal["better", "not_better", "too_few_days", "disabled", "no_model"]`
  - `@dataclass(frozen=True) class Activation: active: bool; basis: Basis; days: int; model_mae_w: float | None; profile_mae_w: float | None; improvement_pct: float | None; reason: Reason` mit `text() -> str` (Texte aus dem Abschnitt „Texte“) und `as_json() -> dict[str, Any]`
  - `def shadow_sums(model: Sequence[ForecastRecord], profile: Sequence[ForecastRecord], actual: Mapping[datetime, Actual]) -> dict[date, DaySums]` (nur Paare gleicher `slot_start` und `horizon` mit Ist; Tag = lokaler Tag des Slots)
  - `def decide(shadow: Mapping[date, DaySums], backtest: Mapping[date, DaySums] | None, *, enabled: bool, has_model: bool, today: date) -> Activation`

- [ ] **Step 1: Write the failing tests**

```python
def test_exactly_ten_percent_activates() -> None: ...  # 225 gegen 250 W → aktiv
def test_just_below_ten_percent_keeps_profile() -> None: ...  # 225,1 gegen 250 W
def test_shadow_wins_from_ten_days() -> None: ...  # Schatten 10 Tage schlechter, Backtest besser → Profil
def test_backtest_decides_before_ten_shadow_days() -> None: ...
def test_too_few_days_without_backtest() -> None: ...  # reason too_few_days, days = Schattentage
def test_disabled_and_no_model() -> None: ...
def test_window_excludes_today() -> None: ...
def test_shadow_sums_pair_same_slot_and_horizon() -> None: ...
def test_texts_for_better_and_worse() -> None:
    # „Modell aktiv: in den letzten 14 Tagen 15 % genauer als das Profil (im Schattenbetrieb).“
    # „Profil aktiv: Das Modell ist 4 % ungenauer als das Profil.“
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_activation.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Entscheidung 11 und Präzisierung 6. Prozente im Text gerundet auf ganze Zahlen, deutsches Format.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning/test_activation.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/learning tests
git commit -m "feat(app): Aktivierungsregel des Lastmodells"
```

### Task 9: Lerndienst

**Files:**
- Create: `src/bindaems/app/learning/service.py`
- Modify: `src/bindaems/app/forecast/load.py` (`LoadForecastService.profile() -> LoadProfile | None`, das gemerkte Profil), `src/bindaems/app/runtime.py` (Dienst bauen, Job `load-model`, Komponente `load-model`, Nachholung aus Task 2 beim Start)
- Test: `tests/unit/app/learning/test_service.py`

**Interfaces:**
- Consumes: `LoadForecastService.history`, `.profile()` (in diesem Task ergänzt), `WeatherArchive` (Task 2), `ForecastService.temperatures()` (Task 2), `HaSensorReader` (Task 3), Tasks 4 bis 8, `ForecastArchive.records`, `actuals`, `next_local`, Einstellungen über `SettingsService.current()`.
- Produces:
  - `TRAIN_AT: Final = time(3, 15)`, `MIN_DAYS: Final = 28`, `MIN_SLOTS_PER_DAY: Final = 80`, `TRAIN_TIMEOUT_S: Final = 600`
  - `Outcome = Literal["trained", "too_little_data", "failed"]`; `@dataclass(frozen=True) class TrainingResult: outcome: Outcome; at: datetime; text: str`
  - `class LoadModelService` mit
    - `async train() -> TrainingResult` (Daten holen, Tage zählen, trainieren, Backtest, `registry.add`, danach `evaluate`; ein Lauf zugleich)
    - `async evaluate() -> Activation` (Schatten der letzten 14 Tage aus dem Archiv, Backtest der neuesten brauchbaren Version, Schalter aus den Einstellungen; `registry.decide`)
    - `def start_training() -> bool` (Hintergrundlauf für die Taste; `False`, wenn schon einer läuft)
    - `active() -> bool` (aktive Version geladen und brauchbar)
    - `async predict(issued_at: datetime, slots: Sequence[datetime], profile: LoadProfile) -> dict[datetime, float] | None` (mit der geladenen Version, auch wenn sie nicht aktiv ist; `None` ohne Modell)
    - `status() -> dict[str, Any]` (Präzisierung 7 ohne `method`, das ergänzt die Route)
    - `async run()` (beim Start Version laden, ohne Version gleich trainieren; dann täglich um `TRAIN_AT`; nach einem Fehler weiter im Tagesrhythmus)
    - `component_status() -> ComponentStatus`

- [ ] **Step 1: Write the failing tests**

```python
async def test_too_little_data_keeps_profile(service) -> None:
    # 27 Tage → outcome too_little_data, Text „Zu wenig Daten: 27 von 28 Tagen.“
async def test_days_with_gaps_do_not_count(service) -> None: ...  # Tag mit 79 Slots
async def test_training_registers_and_evaluates(service, registry) -> None: ...
async def test_training_runs_when_a_sensor_has_no_data(service) -> None: ...
async def test_model_keeps_its_feature_list_after_settings_change(service) -> None:
    # Sensor nach dem Training entfernt → predict rechnet mit den alten Namen, Wert NaN
async def test_training_failure_keeps_previous_version(service, registry) -> None: ...
async def test_second_start_while_running_is_refused(service) -> None: ...
async def test_nightly_run_at_quarter_past_three(service, clock) -> None: ...
async def test_evaluate_uses_switch_from_settings(service) -> None: ...  # aus → disabled
def test_component_status_texts(service) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_service.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Entscheidungen 4, 9, 11 und 15. Daten für Training und Backtest: Hauslast der letzten `56 + 7 · MAX_WEEKS` Tage über `history`, Temperatur aus dem Archiv, Sensoren über `slot_values`. Für `predict`: Last der letzten 8 Tage über `history`, Temperatur aus `ForecastService.temperatures()` mit dem Archiv als Rückfall, Sensoren der letzten Stunde, `profile_for` liefert das übergebene Profil. Die schwere Rechnung läuft in `asyncio.to_thread`, mit `asyncio.wait_for(…, TRAIN_TIMEOUT_S)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests
git commit -m "feat(app): nächtliches Training und Aktivierung des Lastmodells"
```

### Task 10: Lastprognose mit Schattenbetrieb

**Files:**
- Modify: `src/bindaems/app/forecast/load.py`, `src/bindaems/app/forecast/archive.py` (`Kind`), `src/bindaems/app/forecast/quality.py` (`QualityRow.kind`, Reihenfolge der Arten, Ist für `load*`), `src/bindaems/app/runtime.py` (`attach_model`)
- Test: `tests/unit/app/forecast/test_load.py`, `tests/unit/app/forecast/test_quality.py`

**Interfaces:**
- Consumes: `LoadModelService.active()`, `.predict()` (Task 9).
- Produces:
  - `LoadForecast.method: Literal["profile", "model"] = "profile"`
  - `LoadForecastService.attach_model(model: LoadModelService) -> None`
  - Archiv je Ausgabe: `load_profile` immer, `load_model` wenn eine Vorhersage da ist, `load` = ausgegebene Prognose

- [ ] **Step 1: Write the failing tests**

```python
async def test_without_model_output_is_profile(service) -> None: ...  # method profile, kein load_model
async def test_shadow_records_both_methods(service, archive, model) -> None: ...
async def test_active_model_is_output(service, model) -> None: ...  # method model, load = Modell
async def test_inactive_model_only_in_shadow(service, model) -> None: ...
async def test_gaps_are_filled_with_profile(service, model) -> None: ...  # 5 % fehlen
async def test_many_gaps_fall_back_to_profile(service, model) -> None: ...  # 11 % fehlen
async def test_broken_model_falls_back_to_profile(service, model) -> None: ...  # predict wirft
def test_quality_rates_shadow_kinds(quality) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/forecast -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Entscheidung 12 und Präzisierung 2. InfluxDB bekommt wie bisher nur `kind=load` (die ausgegebene Prognose). Der Auslöser `load_forecast` des Planers bleibt unverändert; ein Wechsel des Verfahrens ändert die Prognose und plant damit neu.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/forecast tests/unit/app/planning -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests
git commit -m "feat(app): Lastprognose mit Schattenbetrieb von Profil und Modell"
```

### Task 11: API, Demo-Welt und Vertragsdateien

**Files:**
- Create: `src/bindaems/app/learning/routes.py`
- Modify: `src/bindaems/app/forecast/routes.py` (`method`, `improvement_pct`, `/quality/daily`), `src/bindaems/app/forecast/quality.py` (`daily(first: date, last: date) -> list[DailyRow]`), `src/bindaems/app/runtime.py` (Router), `tests/ui_world.py` (Modell, Schatten, Backtest in der Demo-Welt), `ui/src/lib/api/contract/*.json` (neu geschrieben), `ui/src/lib/api/schemas.ts`, `docs/betrieb.md` (API-Liste), `tests/unit/test_docs.py`
- Test: `tests/unit/app/learning/test_routes.py`, `tests/unit/app/forecast/test_routes.py`, `tests/integration/test_ui_contract.py`

**Interfaces:**
- Consumes: `LoadModelService.status()`, `.start_training()` (Task 9), `HaSensorReader.candidates()` (Task 3), `ForecastQuality`.
- Produces:
  - `GET /api/learning/load` (Viewer, Präzisierung 7), `POST /api/learning/load/train` (Admin; 202 `{"started": true}`, 409 „Training läuft schon.“), `GET /api/learning/candidates` (Admin; `{"entities": [{"entity_id", "unit"}], "error"}`)
  - `GET /api/forecast/quality/daily` (Präzisierung 8); `improvement_pct` und `method` (Präzisierung 9)
  - `@dataclass(frozen=True) class DailyRow: day: date; kind: Kind; n: int; mae_w: float`
  - Zod-Schemas `learningStatusSchema`, `qualityDailySchema`, `sensorCandidatesSchema`

- [ ] **Step 1: Write the failing tests**

```python
def test_learning_status_shape(client_as) -> None: ...
def test_train_requires_admin(client_as) -> None: ...  # viewer → 403
def test_second_train_is_conflict(client_as) -> None: ...  # 409
def test_candidates_require_admin(client_as) -> None: ...
def test_quality_has_improvement_for_model_rows(client_as) -> None: ...
def test_quality_daily_sorted_by_date_and_kind(client_as) -> None: ...
def test_quality_daily_rejects_days_out_of_range(client_as) -> None: ...  # 0 und 61 → 422
def test_load_forecast_names_method(client_as) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_routes.py tests/unit/app/forecast/test_routes.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierungen 7 bis 9. Merkmalsnamen für das UI (`label`) aus dem Abschnitt „Texte“; `sensors[].in_model` heißt: der Sensor steht in der Merkmalsliste der geladenen Version und hatte beim Training Werte. Danach `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app tests/integration/test_ui_contract.py tests/unit/test_docs.py -q && cd ui && pnpm check`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests ui/src/lib/api docs
git commit -m "feat(app): API für Lastmodell und Prognosegüte je Tag"
```

### Task 12: Einstellungen „Lastmodell“

**Files:**
- Create: `ui/src/lib/settings/LearningEditor.svelte`, `ui/src/lib/settings/LearningEditor.test.ts`
- Modify: `ui/src/lib/settings/SettingsEditor.svelte`, `ui/src/lib/settings/model.ts` (+ Test), `ui/src/routes/einstellungen/+page.svelte`
- Test: `ui/src/lib/settings/LearningEditor.test.ts`, `ui/src/lib/settings/model.test.ts`

**Interfaces:**
- Consumes: `GET /api/settings`, `PUT /api/settings`, `GET /api/learning/candidates` (Task 11).
- Produces: Abschnitt „Lastmodell“ (Entscheidung 14, Texte).

- [ ] **Step 1: Write the failing tests**

```ts
it('zeigt den Schalter „Lastmodell verwenden“ an', ...);
it('wählt Sensoren aus der Liste und speichert sie mit den Einstellungen', ...);
it('erlaubt höchstens 8 Sensoren', ...);  // die Auswahl ist danach gesperrt
it('nennt den Grund, wenn die HA-Datenbank fehlt', ...);
it('zeigt einen gespeicherten Sensor, der nicht mehr in HA steht, mit Hinweis', ...);
it('ist für Viewer nur lesbar', ...);
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pnpm test -- LearningEditor model`
Expected: FAIL

- [ ] **Step 3: Implement** wie die übrigen Abschnitte des Editors (`PlanningEditor` als Vorbild). Ein gespeicherter Sensor ohne Treffer in den Kandidaten bleibt in der Liste mit „nicht in HA gefunden“.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && pnpm test && pnpm check && pnpm lint`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui
git commit -m "feat(ui): Einstellungen für das Lastmodell"
```

### Task 13: Seite „Prognosen“

**Files:**
- Create: `ui/src/routes/prognosen/+page.svelte`, `ui/src/routes/prognosen/page.test.ts`, `ui/src/lib/forecast/view.ts` (+ `view.test.ts`), `ui/src/lib/forecast/ModelCard.svelte`, `ui/src/lib/forecast/QualityTable.svelte`, `ui/src/lib/forecast/DailyChart.svelte`, `ui/src/lib/forecast/BacktestTable.svelte`, `ui/src/lib/forecast/FeatureTable.svelte`, `ui/tests/e2e/prognosen.spec.ts`
- Modify: `ui/src/lib/nav.ts` (+ Test), das Symbol `forecast` in der Symbolkomponente, `ui/src/lib/system/QualityCard.svelte` (Verweis, + Test)
- Test: wie oben

**Interfaces:**
- Consumes: `GET /api/learning/load`, `POST /api/learning/load/train`, `GET /api/forecast/quality?days=`, `GET /api/forecast/quality/daily?days=` (Task 11); bestehende Diagrammbausteine aus `ui/src/lib/charts`.
- Produces:
  - `view.ts`: `qualityRows(rows) -> QualityView[]` (Arten in der Reihenfolge PV, Last (verwendet), Last Profil, Last Modell; Vorlauf-Texte), `improvementText(pct: number | null) -> string` („+15 %“, „−4 %“, „–“), `dailySeries(rows) -> {profile: Point[], model: Point[]}`, `featureRows(features) -> {label, share}[]`
  - Seite nach Entscheidung 13 und den Texten.

- [ ] **Step 1: Write the failing tests**

```ts
it('ordnet die Arten und Vorläufe wie im Text', ...);
it('zeigt die Verbesserung nur bei Last Modell', ...);
it('zeigt Verfahren und Grund der Aktivierung', ...);
it('Taste „Jetzt neu trainieren“ nur für Admins, gesperrt während des Laufs', ...);
it('zeigt den Hinweis, wenn es noch keinen Backtest gibt', ...);
it('wechselt den Zeitraum 7/14/28/60 Tage', ...);
it('Navigation hat „Prognosen“ nach „Zeitplan“', ...);
it('Karte „Prognosegüte“ verweist auf Prognosen', ...);
```

E2E (`prognosen.spec.ts`): Seite öffnen, alle fünf Karten sichtbar mit den Werten der Demo-Welt, Zeitraum auf 28 Tage, als Admin „Jetzt neu trainieren“ zeigt „Training läuft …“.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pnpm test -- forecast prognosen nav QualityCard`
Expected: FAIL

- [ ] **Step 3: Implement** mit den Mustern der Seite „Statistik“ (Karten, Zeitraum-Tasten, Laden über `resource.svelte.ts`). Während eines Trainings fragt die Seite den Status alle 5 s ab, bis `training.running` falsch ist.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && pnpm test && pnpm check && pnpm lint && pnpm build && pnpm e2e`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui
git commit -m "feat(ui): Seite Prognosen mit Güte, Backtest und Merkmalen"
```

### Task 14: Dokumentation, Abnahme und Stand der Spec

**Files:**
- Create: `docs/verification/abnahme-phase-5a.md`
- Modify: `docs/betrieb.md` (Abschnitt „Lastmodell (Phase 5a)“: Ablauf, Mindestdaten, Nachtlauf, Aktivierung, Rückfall, Sensorauswahl, Seite „Prognosen“, Modelle unter `/data/models/load/`, keine Sicherung nötig, weil jede Nacht neu trainiert wird; „Prognosen“ verweist auf die neue Seite; Ausrollen von 5a), `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absätze „Stand Plan 5a“ in 5.2, 10.2, 10.3, 11.3, 13 und 18), `docs/uebergabe.md` (Stand, „Entschieden in Plan 5a“, „Bewusst offen gelassen“, „Offen an der Anlage“, nächster Schritt Plan 5b), `docs/verification/README.md`
- Test: `uv run pytest tests/unit/test_docs.py -q`

- [ ] **Step 1: Write „Stand Plan 5a“** in die genannten Abschnitte der Spec, mit den Entscheidungen, wie du sie freigibst, und den Festlegungen der Umsetzung. In 18 auch der Schnitt 5a, 5b, 5c.
- [ ] **Step 2: Write the acceptance checklist** nach Entscheidung 16: Start der 28 Tage (Datum des Ausrollens); auf „Prognosen“ mit Zeitraum 28 Tage die Zeilen „Last Profil“ und „Last Modell“ und die Verbesserung; Verfahren und Grund in der Karte „Lastprognose“; Registry: jede Nacht eine Version (`GET /api/learning/load`, `model.trained_at`); Rückfall: `ems-app` stoppen, die Datei der aktiven Version umbenennen, starten → „Verfahren: Profil“, Komponente `load-model` nennt es, nach „Jetzt neu trainieren“ wieder eine brauchbare Version; Unterschrift.
- [ ] **Step 3: Add to the guide** die Schritte an der Anlage: nach (oder mit) dem Ausrollen von 4b und 6a `docker compose pull ems-app && docker compose up -d ems-app` (der core bleibt, Regelbetrieb bleibt an); Migration 0013 läuft beim Start; die Temperatur-Historie wird einmal geholt; das erste Training läuft gleich nach dem Start; unter **Einstellungen → Lastmodell** Sensoren wählen (optional), dann „Jetzt neu trainieren“.
- [ ] **Step 4: Run the whole repository**

Run: `uv run ruff format --check . && uv run ruff check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports && uv run pytest -q --cov && cd ui && pnpm check && pnpm lint && pnpm test && pnpm build && pnpm e2e`
Expected: PASS, Abdeckung ≥ 75 % gesamt und ≥ 90 % für safety/control/actuators; `git diff main --stat -- src/bindaems/core cerbo-watchdog src/bindaems/shared/control.py src/bindaems/shared/plan.py src/bindaems/shared/config.py` ist leer; die GitHub-CI des Branch-Kopfs ist grün (auch der Bau des Images mit `libgomp1`).

- [ ] **Step 5: Commit**

```bash
git add docs
git commit -m "docs: Stand Plan 5a in Spec, Betriebshandbuch, Abnahme und Übergabe"
```

## Selbstprüfung (beim Schreiben erledigt)

- **Spec 18, Phase 5, Teil Lastmodell abgedeckt:** Import der Historie (Wetter-Nachholung Task 2, HA-Sensoren Task 3, Hauslast wie Profil Entscheidung 4), Merkmalsbasis (Task 4), Sensorauswahl im UI (Tasks 1, 12), LightGBM (Task 5), Backtest über 4–8 Wochen (Task 6), versionierte Ablage (Task 7), Aktivierung mit 10 % über 14 Tage und automatischer Rückfall (Tasks 8, 9, 10), Anzeige von MAE, Bias und Verbesserung je Horizont (Tasks 11, 13), Ansicht der Prognosegüte (Task 13), Abnahme über 4 Wochen (Task 14). Spec 10.5: Klemmung (Task 5), nur Eingabe der Planung (Task 10).
- **Fahrverhalten** (Spec 10.4, Teil von Phase 5) ist bewusst Plan 5b; „Vorschläge erscheinen mit Konfidenz“ gehört zu dessen Abnahme.
- **Namen:** `FeatureInputs`, `TrainingSet`, `TrainedModel`, `BacktestResult`, `DaySums`, `Activation`, `RegistryEntry`, `LoadModelService` werden in den Tasks so verwendet, wie sie eingeführt sind; `DaySums` entsteht in Task 6 (`backtest.py`) und wird in Task 8 importiert; `LoadForecastService.profile()` entsteht in Task 9, `attach_model` in Task 10.
- **Umfang:** 14 Tasks; reine Rechnung (Tasks 4 bis 8) getrennt von IO (Tasks 2, 3, 7, 9), damit jeder Reviewer eine Einheit prüft.

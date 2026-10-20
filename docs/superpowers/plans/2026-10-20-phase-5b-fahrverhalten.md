# Phase 5b – Fahrverhalten: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BindaEMS lernt, wann die Autos wegfahren und heimkommen und wie viel sie unterwegs verbrauchen, und hilft damit beim Modus „Geplant“:
- Je Auto und Wochentag: erste Abfahrt des Tages, Rückkehr für die Nacht, Ladestand bei der Ankunft und Verbrauch bis zur Rückkehr, aus Anwesenheit (Tessie, Home Assistant), Steckzustand und Ladestand der letzten 8 Wochen.
- **Vorschläge mit Konfidenz:** Abfahrt (20. Perzentil) und Ziel-SOC (Verbrauch plus Puffer), geklemmt auf einstellbare Grenzen; ein Knopf übernimmt sie.
- **Automatik (optional, je Auto, Standard aus):** In „Geplant“ setzt die app Ziel und Abfahrt für die nächste erwartete Fahrt selbst, auch über Tage ohne Fahrt hinweg (Abfahrt an einem bestimmten Tag).
- **Anwesenheitsprognose:** Wahrscheinlichkeit je Viertelstunde, dass ein Auto angesteckt ist. Der Planer nimmt ein Auto in „Geplant“, das noch nicht steckt, ab seiner erwarteten Ankunft als **erwartetes Auto** in den Plan auf; der core bekommt dafür nichts.
- Trefferquote der Vorschläge, Anzeige auf „Prognosen“ und „Laden“, Bedienung in Home Assistant.

**Architecture:** 5b ändert vor allem app und UI. Dazu kommt eine kleine Änderung in `shared/control.py` und im core (Abfahrt an einem Datum, Entscheidung 9); ohne sie bliebe der core gleich.
- **`app/learning/`:** `driving_data.py` (Lesen aus InfluxDB und SQLite), `driving_days.py` (Aufenthalte und Tage, rein), `driving_profile.py` (Profil, Vorschläge, Konfidenz, Anwesenheitsprognose, nächste Fahrt, rein), `driving_quality.py` (Trefferquote, rein), `driving_service.py` (Nachtlauf, Ablage, Status), `driving_auto.py` (Automatik), Routen in `routes.py`.
- **`app/planning/`:** erwartete Autos in `vehicles.py`, `optimizer.py` (Variablen erst ab der Ankunft), `builder.py`, `explain.py`, `plausibility.py`, `triggers.py`; `shared/plan.py` (`PlanVehicle.expected`, `arrival_at`, Grundcode `AUTO_ERWARTET`; `intent_plan` lässt erwartete Autos weg).
- **`app/charging/planned.py`:** Eine Änderung durch die Automatik kurz nach Beginn einer geplanten Ladung schließt die Zeile nicht als „geändert“ (Entscheidung 8).
- **Migration `0014`:** `driving_profile` (Stand je Nacht und Auto), `driving_automation` (Zustand der Automatik).
- **`shared/settings.py`:** `DrivingSettings` unter `learning.driving`.
- **UI:** Karte „Fahrverhalten“ und „Anwesenheit“ auf „Prognosen“, Vorschlag und Automatik in der Fahrzeugkarte auf „Laden“, erwartete Autos im „Zeitplan“, Abschnitt „Fahrverhalten“ in den Einstellungen.
- **Home Assistant:** je Fahrzeug Schalter „Automatik Ziel und Abfahrt“, Sensoren „Vorschlag Abfahrt“, „Vorschlag Ziel-SOC“ und „Nächste Abfahrt“.

**Tech Stack:** wie Plan 5a; keine neue Abhängigkeit (Perzentile mit der Standardbibliothek, Entscheidung 2). Keine neuen Schlüssel in `config.yaml`.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, vor allem 5.3, 7.5, 9.3, 9.5, 10.4, 10.5, 11.2, 11.3, 12, 13 und 18 („Phase 5 – Lernen“, Stand Plan 5a).
- Verweise wie „Spec 10.4“ beziehen sich darauf. Die Absätze „Stand …“ gehen dem älteren Text vor; die Präzisierungen unten gehen beiden vor.
- Vorgänger ist Plan 5a (`docs/superpowers/plans/2026-10-20-phase-5a-lastmodell.md`, auf `main` seit `c316fc4`); 5b baut auf Plan 4b auf (`docs/superpowers/plans/2026-10-18-phase-4b-geplantes-laden.md`). Ihre Global Constraints und die der Pläne davor gelten weiter.

## Einordnung

5b ist der zweite der drei Pläne der Phase 5 (Spec 18, Stand Plan 5a): Abfahrt, Ankunft und Ladestand bei der Ankunft je Auto und Wochentag, Vorschläge mit Konfidenz, optionale Automatik für „Geplant“ und die Anwesenheitsprognose für den Optimierer.
- **Zeitpunkt:** Der Plan liegt jetzt zur Freigabe bereit. Umgesetzt wird nach deiner Freigabe; das ändert an der Anlage nichts.
- **Ausrollen:** frühestens mit dem gemeinsamen Ausrollen von 3d, 4b, 5a und 6a nach der Abnahme der Phase 3 (nach dem 16.11.2026), sonst danach. Weil 5b den core ändert (Entscheidung 9), kommen core und app gemeinsam, mit Regelbetrieb aus während des Wechsels, wie bei 4b. Migration `0014` läuft beim Start; vorher die Datenbank sichern.
- **Daten:** Die Ansteckvorgänge laufen seit dem 18.10.2026 (Plan 4a), die Anwesenheit des Tesla (`at_home`) und der Ladestand stehen seit Phase 1 in InfluxDB (`raw`, 90 Tage), die Anwesenheit des e-Golf in der HA-Datenbank. Beim Ausrollen Ende November sind das rund 6 Wochen Ansteckvorgänge und die vollen 8 Wochen Anwesenheit.
- **Wirkung:** Vorschläge und Prognose sind nur Eingaben (Spec 10.5). Die Automatik ist ab Werk aus. Erwartete Autos ändern nur den Plan des Akkus; solange Victron im Dry-Run ist, rechnet der core ihn nur mit.
- **Danach:** 5c (Vorheizen und Vorkühlen), sobald Wochen mit Daten der Klimaanlage da sind.

## Vorgeschlagene Entscheidungen (zur Freigabe)

Der Plan ist auf diese Vorschläge geschrieben; eine andere Wahl ändert die genannten Tasks. **Offen für dich sind vor allem 1, 8, 9, 11 und 12**; die übrigen folgen aus der Spec oder aus 4b und 5a.

1. **Zuschnitt** (Spec 10.4, 18). **Offen.**
   - **In 5b:** Daten aus Anwesenheit, Steckzustand und Ladestand, Profil je Auto und Wochentag, Vorschläge mit Konfidenz, „Vorschlag übernehmen“, Automatik für „Geplant“ mit Abfahrt an einem Datum, Anwesenheitsprognose, erwartete Autos im Planer, Trefferquote, Anzeige in UI und HA, Abnahme.
   - **Nicht in 5b:** die Akku-Unterstützung für PV-Autos im MILP (Entscheidung 12), Vorschläge und Prognose für Gäste, Laden außer Haus als eigene Größe, ein Lernmodell (LightGBM) für das Fahrverhalten (Entscheidung 2), Vorheizen und Vorkühlen (5c).
2. **Empirische Verteilungen statt LightGBM** (Tasks 4, 5). Je Auto und Wochentag gibt es in 8 Wochen höchstens 8 Werte; Perzentile, Anteile und Streuung beschreiben das ehrlicher als ein Modell und brauchen keine neue Abhängigkeit. Alternative: LightGBM mit Merkmalen wie Wetter und Ferien; erst sinnvoll mit Monaten an Daten.
3. **Datenquellen** (Task 3).
   - **Anwesenheit, in dieser Reihenfolge:** eine HA-Entität, die du unter Einstellungen → „Fahrverhalten“ für das Auto einträgst (etwa `device_tracker.…` des Tesla, wenn HA ihn hat; die HA-Datenbank reicht weiter zurück); sonst `home_entity` des Autos aus der HA-Datenbank (ohne `influxdb.ha_database` aus der BindaEMS-Datenbank, Signal `vehicle.<k>.home_state`); sonst beim Tesla `vehicle.<k>.at_home` aus Tessie (BindaEMS-Datenbank, Messung `status`); sonst nur die Ansteckvorgänge (`charging_session`).
   - **Steckzustand:** die Ansteckvorgänge aus 4a, je Auto (Gast und Unbekannt zählen nicht).
   - **Ladestand:** BindaEMS-Datenbank (`soc`, Tesla seit Phase 1, e-Golf ab 4b); fehlt er, `soc_entity` aus der HA-Datenbank.
   - **Frage an dich:** Gibt es in Home Assistant einen `device_tracker` des Tesla (etwa aus der Tessie- oder Tesla-Integration)? Dann trägst du ihn nach dem Ausrollen ein, und die Vorschläge stehen sofort auf breiterer Grundlage.
4. **Fenster und Gruppen** (Tasks 4, 5): die letzten **8 Wochen** (56 Tage) wie beim Lastmodell; Feiertage zählen wie Sonntag. Je Wochentag; hat ein Wochentag weniger als **3** beobachtete Tage, gilt die Gruppe (Montag bis Freitag, Samstag, Sonn- und Feiertag), und der Vorschlag sagt es. Alternative: 12 Wochen; trägt mehr Werte, reagiert aber langsamer auf einen neuen Arbeitsweg.
5. **Was gelernt wird** (Task 4, Präzisierung 5), je lokalem Tag:
   - **Erste Abfahrt:** die erste Abfahrt des Tages, wenn das Auto die Nacht davor zu Hause war (um 03:00 Uhr). Abfahrt ist der Wechsel auf „weg“; wurde das Auto in den 30 min davor abgesteckt, gilt das Abstecken.
   - **Kein Fahrtag:** zu Hause über die Nacht und am ganzen Tag keine Abfahrt. Tage, an denen das Auto um 03:00 Uhr nicht zu Hause war (mehrtägige Fahrt), zählen nicht.
   - **Rückkehr für die Nacht:** die Ankunft, mit der der Aufenthalt über die nächste Nacht beginnt; Ladestand dabei der erste Wert nach der Ankunft (höchstens 30 min, und vor dem Laden).
   - **Verbrauch:** Ladestand bei der ersten Abfahrt minus Ladestand bei der Rückkehr für die Nacht, in Prozentpunkten. Das ist „bis zur nächsten Rückkehr“ aus Spec 10.4, gelesen als Rückkehr für die Nacht: Ein kurzer Halt zu Hause mittags trennt die Fahrt nicht.
6. **Vorschläge** (Task 5, Spec 10.4):
   - **Abfahrt:** das 20. Perzentil der ersten Abfahrten des Wochentags, abgerundet auf die Viertelstunde. Ist der Anteil der Fahrtage unter **50 %**, heißt der Vorschlag „keine Fahrt erwartet“.
   - **Ziel-SOC:** Puffer bei der Rückkehr (Standard **20 %**) plus das 80. Perzentil des Verbrauchs, aufgerundet auf 5 %, **geklemmt** auf „Ziel-SOC mindestens“ (Standard **50 %**) und „höchstens“ (Standard **90 %**). Die drei Werte stellst du unter „Fahrverhalten“ ein; das sind die „konfigurierten Wertebereiche“ aus Spec 10.5. Ist geklemmt, sagt der Vorschlag es.
   - Alternative zum Ziel: Median plus größerer Puffer; das 80. Perzentil trifft schlechte Tage besser.
7. **Konfidenz** (Task 5), je Vorschlag „hoch“, „mittel“ oder „niedrig“:
   - **Abfahrt:** hoch ab 6 Fahrtagen des Wochentags (nicht der Gruppe) und höchstens 60 min zwischen 20. und 80. Perzentil; mittel ab 3 Fahrtagen und höchstens 120 min; sonst niedrig.
   - **Ziel:** hoch ab 6 Verbrauchswerten und höchstens 15 Prozentpunkten zwischen 20. und 80. Perzentil; mittel ab 3 und höchstens 30; sonst niedrig.
   - Ohne Werte gibt es keinen Vorschlag („Noch keine Daten“).
8. **Automatik für „Geplant“** (Tasks 7, 8, Spec 10.4 „optional, nur für Geplant“). **Offen.**
   - Je Auto ein Schalter „Ziel und Abfahrt automatisch“, **Standard aus**, auf „Laden“ und in HA, ab der Rolle „Bedienen“.
   - **An:** Steht das Auto in „Geplant“, setzt die app Ziel, Abfahrt und Abfahrtstag auf den Vorschlag der **nächsten erwarteten Fahrt** (der erste Tag ab heute mit Fahrt erwartet, höchstens 7 Tage voraus). Sie übernimmt nur Werte mit Konfidenz mindestens „mittel“; ein unsicherer Wert bleibt, wie er ist. Den Modus ändert sie nie.
   - **Wann:** Sie prüft jede Minute. Sie ändert nichts, solange eine geplante Ladung des Autos läuft (offene Zeile in `planned_charge`); so entstehen keine „geänderten“ Ladungen in der Abnahme der Phase 4. Ausnahme: In den ersten 10 Minuten einer Zeile übernimmt die Zeile die Werte der Automatik, statt als „geändert“ zu enden. Das braucht es, weil nach einer Abfahrt, zu der das Auto stecken blieb, sofort die Zeile für den nächsten Tag beginnt.
   - **Deine Änderung geht vor:** Änderst du Ziel oder Abfahrt eines Autos mit Automatik selbst (UI, HA, `curl`), pausiert sie bis zu der Abfahrt, die du gesetzt hast.
   - Jede Änderung steht im Änderungsprotokoll mit „Automatik“ als Urheber.
   - Alternative: nur Vorschläge und „Übernehmen“, keine Automatik. Dann ist in 5b weder Entscheidung 9 noch die Ausnahme in `planned_charge` nötig.
9. **Abfahrt an einem Datum** (Task 2). **Offen.**
   - `VehicleIntent` bekommt `departure_on` (Datum, optional). Mit Datum gilt die Abfahrt an diesem Tag zur Uhrzeit `departure`; ist sie vorbei oder fehlt das Datum, gilt wie bisher das nächste Vorkommen der Uhrzeit.
   - Nur die Automatik und „Vorschlag übernehmen“ setzen ein Datum. Wer die Abfahrtszeit von Hand setzt (UI, HA), löscht es: Von Hand gilt die Uhrzeit täglich, wie in 4b.
   - **Wozu:** Steckt der Tesla am Freitagabend an und fährt erst am Montag, plant BindaEMS mit dem Montag. Samstag und Sonntag zählt dann die PV (Regel R2), statt in der Nacht auf Samstag aus dem Netz zu laden.
   - **Kosten:** `shared/control.py` und ein Aufruf im core ändern sich; der core muss mit ausgerollt werden (das geschieht beim gemeinsamen Ausrollen ohnehin).
   - Alternative: ohne Datum, nur app. Die Automatik setzt dann immer die Uhrzeit des nächsten Tages; an Tagen ohne Fahrt lädt das Auto unnötig früh, oft aus dem Netz.
10. **Anwesenheitsprognose** (Task 5, Spec 10.4 „Wahrscheinlichkeit, dass das Auto eingesteckt ist“): je Auto, Wochentag (Gruppen wie in Entscheidung 4) und lokaler Viertelstunde der Anteil der Tage, an denen ein Ansteckvorgang des Autos die Mitte der Viertelstunde überdeckt. Es zählen die Tage ab dem ersten Ansteckvorgang eines Autos in der Datenbank. Die Seite „Prognosen“ zeigt sie für die nächsten 48 h.
11. **Erwartete Autos im Planer** (Tasks 9, 10, Spec 9.3). **Offen.**
   - Ein Auto in „Geplant“, das laut Health nicht steckt, kommt als **erwartetes Auto** in den Plan, wenn die Prognose für eine spätere Viertelstunde vor seiner Abfahrt mindestens **50 %** sagt. Ab dieser Viertelstunde (erwartete Ankunft) bis zur Abfahrt steht es wie ein angestecktes Auto im Modell: Ladestand der Median der Ankunft des Wochentags, Ziel und Abfahrt aus der Absicht, Wallbox `default_wallbox`. Ohne gelernten Ladestand bei der Ankunft oder ohne `default_wallbox` kommt es nicht hinein.
   - **Wozu:** Der Plan des Akkus weiß dann vorher, dass in der Nacht ein Auto Netzstrom braucht: günstige Slots, Bezugsgrenze und Leistungspreis (Monatsspitze) teilt er mit dem Auto, statt die Nacht ganz für den Akku zu verplanen.
   - Der core bekommt für ein erwartetes Auto keine Leistung (`intent_plan` lässt es weg). Steckt das Auto an, plant der Auslöser `vehicles` neu, jetzt mit dem echten Ladestand. Ein erwartetes Auto löst keine Warnung aus („Ziel-SOC unerreichbar“), zählt nicht in `planned_charge` und hat in HA kein „Fertig um“.
   - Der Ersatzplan (Spec 9.4) und die Planungsszenarien bleiben ohne erwartete Autos.
   - Alternative: die Prognose nur anzeigen und den Planer lassen, wie er ist.
12. **Akku-Unterstützung für PV-Autos im MILP: nicht in 5b** (Spec 7.6, Stand Plan 3c und 4b). **Offen.**
   - Heute hebt der Plan die Schwelle der Unterstützung nur an, wo er den Akku später fürs Haus braucht („Akku für 18–21 Uhr reserviert“); das wirkt schon. Eine Optimierung im MILP müsste die Ladung der PV-Autos als unsichere Last modellieren (Prognose mal Überschuss) und dem Plan erlauben, die Schwelle auch zu **senken**. Das ändert die Regel im core aus 3c, und der Gewinn ist klein: Strom aus dem Akku ins PV-Auto fehlt am Abend dem Haus.
   - Vorschlag: Der Punkt entfällt; die Spec sagt es in „Stand Plan 5b“. Alternative: ein eigener Plan später, wenn die Statistik zeigt, dass PV-Autos oft mitten im Laden stoppen.
13. **Berechnung und Ablage** (Task 6): jede Nacht um **03:40 Uhr** lokal (nach dem Lastmodell um 03:15 Uhr), beim Start und mit der Taste „Jetzt neu berechnen“ (Admin). Jeder Lauf legt je Auto eine Zeile in `driving_profile` ab; behalten werden 70 Tage. Scheitert ein Lauf (InfluxDB weg), gilt der letzte Stand weiter. Die Komponente `driving` in „Komponenten“ nennt den Zustand.
14. **Trefferquote** (Task 6): Für jeden Fahrtag der letzten 28 Tage vergleicht die app die tatsächliche erste Abfahrt mit dem Vorschlag, der am Vorabend galt (Konfidenz mindestens „mittel“): getroffen, wenn das Auto nicht früher fuhr. Beim Ziel: getroffen, wenn der vorgeschlagene Ziel-SOC minus der tatsächliche Verbrauch mindestens der Puffer ist.
15. **Anzeige und Bedienung** (Tasks 11 bis 13, Spec 12, 13):
    - **„Prognosen“:** Karte „Fahrverhalten“ je Auto (Wochentage mit Fahrtanteil, Abfahrt, Ankunft, Ladestand bei Ankunft, Verbrauch, Vorschlägen, Konfidenz und Trefferquote) und Karte „Anwesenheit“ (Prognose der nächsten 48 h).
    - **„Laden“, Fahrzeugkarte:** „Vorschlag: Mo 07:15 · 60 % (Konfidenz hoch)“, Taste „Übernehmen“ und der Schalter der Automatik mit ihrem Zustand.
    - **„Zeitplan“:** erwartete Autos mit „erwartet ab HH:MM“, gestrichelt.
    - **Einstellungen → „Fahrverhalten“:** Puffer bei der Rückkehr, Ziel-SOC mindestens und höchstens, je Auto die HA-Entität der Anwesenheit.
    - **Home Assistant**, je Fahrzeug: `switch` „Automatik Ziel und Abfahrt“, `sensor` „Vorschlag Abfahrt“ (Zeitstempel), „Vorschlag Ziel-SOC“ (%), „Nächste Abfahrt“ (Zeitstempel, aus Health). Die Konfidenz steht als Attribut.
16. **Abnahme** (Task 14, Spec 18 „Vorschläge erscheinen mit Konfidenz“): 28 Tage ab dem Ausrollen.
    - Für jedes Auto mit Daten erscheinen Vorschläge mit Konfidenz auf „Prognosen“, „Laden“ und in HA.
    - Für jedes Auto mit mindestens 10 bewerteten Tagen: Trefferquote der Abfahrt und des Ziels je mindestens **80 %**.
    - Mit Automatik an (wenn du sie einschaltest): keine Zeile „geändert“ durch die Automatik in „Geplante Ladungen“ und keine Änderung während einer laufenden geplanten Ladung.
    - Ein erwartetes Auto steht im Zeitplan und wird beim Anstecken durch das echte ersetzt (einmal an der Anlage beobachtet).
17. **Nichts geht von selbst live.** Die Automatik ist aus, bis du sie einschaltest; erwartete Autos ändern nur den Plan; gelernte Werte erreichen die Safety-Schicht nie (Spec 10.5).

## Präzisierungen gegenüber der Spec

1. **Migration `0014`** (`v0014_driving.py`):
   - `driving_profile(id Integer PK, vehicle String(32) NOT NULL, computed_at UtcDateTime NOT NULL, data_from UtcDateTime NOT NULL, data_to UtcDateTime NOT NULL, source String(16) NOT NULL, profile JSON NOT NULL)`, Index `ix_driving_profile_vehicle_computed_at`. `profile` ist das `VehicleProfile` aus Präzisierung 6 als JSON.
   - `driving_automation(vehicle String(32) PK, paused_until UtcDateTime NULL, last_set_at UtcDateTime NULL, last_values JSON NULL)`.
2. **Einstellungen** (`shared/settings.py`):
   - `DrivingSettings(return_reserve_pct: Annotated[int, Field(ge=0, le=50)] = 20, target_min_pct: Annotated[int, Field(ge=20, le=100)] = 50, target_max_pct: Annotated[int, Field(ge=20, le=100)] = 90, automation: dict[VehicleKey, bool] = {}, presence_entities: dict[VehicleKey, HaEntityId] = {})`; Prüfung „Ziel-SOC mindestens liegt über höchstens.“, wenn `target_min_pct > target_max_pct`. `VehicleKey` wie `WallboxKey`.
   - `LearningSettings.driving: DrivingSettings = Field(default_factory=DrivingSettings)`; gespeicherte Einstellungen ohne den Abschnitt laden mit den Standardwerten. Einträge für Fahrzeuge, die `config.yaml` nicht kennt, übergeht der Dienst.
3. **Abfahrt an einem Datum** (`shared/control.py`, Entscheidung 9):
   - `VehicleIntent.departure_on: date | None = None`.
   - `next_departure(departure: str, now: datetime, on: date | None = None) -> datetime`: mit `on` und `_local_instant(on, departure) > now` dieser Zeitpunkt, sonst wie bisher. Die Regeln der Zeitumstellung gelten unverändert.
   - core (`core/control/planned.py`) und app (`app/planning/vehicles.py`) rufen `next_departure(intent.departure, now, intent.departure_on)`.
   - `SetTarget.departure_on: date | None = None`. Ist `departure` gesetzt, wird `departure_on` übernommen (`None` löscht es); ohne `departure` bleibt `departure_on` stehen. `departure_on` ohne `departure` lehnt die app ab („Ein Abfahrtstag verlangt eine Abfahrtszeit.“).
   - Änderungsprotokoll: „Ziel 80 %, Abfahrt 07:00“ wie bisher, mit Datum „Ziel 80 %, Abfahrt Mo 23.11. 07:00“.
4. **Daten** (`app/learning/driving_data.py`), Fenster `[lokale Mitternacht heute − 56 Tage, jetzt]`:
   - `PresencePoint(ts: datetime, home: bool)`; `VehicleHistory(key, source: Literal["ha", "home_entity", "tessie", "sessions"], presence: list[PresencePoint], seed: bool | None, sessions: list[tuple[datetime, datetime | None]], soc: list[tuple[datetime, float]])`. `seed` ist der Zustand vor dem Fenster (letzter Punkt davor), `None` unbekannt.
   - HA-Datenbank: `SELECT "value", "state" FROM /.*/ WHERE "domain" = '…' AND "entity_id" = '…'` ohne Gruppierung; zu Hause heißt `value = 1` oder `state = 'home'`, jeder andere Punkt weg (HA schreibt `home` als 1, `not_home` als 0, eine Zone nur als Text in `state`).
   - BindaEMS-Datenbank: `status` mit `id = 'vehicle.<k>.at_home'` (`value_bool`) bzw. `'vehicle.<k>.home_state'` (`value_str = 'home'`).
   - Ladestand: `SELECT last("pct") FROM "soc" WHERE "id" = '<k>' … GROUP BY time(5m) fill(none)`; ohne Punkte im Fenster und mit `soc_entity` und HA-Datenbank dasselbe mit `last("value")` aus der HA-Datenbank.
   - Ansteckvorgänge aus `charging_session` mit `vehicle = <k>` (offenes Ende gilt bis jetzt).
   - Ein Fehler beim Lesen wirft `DrivingDataError`; der Dienst behält dann den letzten Stand.
5. **Tage** (`app/learning/driving_days.py`, rein):
   - `DayRecord(day: date, weekday: int, away: bool, departure: datetime | None, departure_soc: float | None, arrival: datetime | None, arrival_soc: float | None, return_at: datetime | None, return_soc: float | None, consumption_pp: float | None)`; `weekday` ist 6 für Feiertage (`is_holiday`).
   - Zeitlinie: `seed` und die Punkte; ohne Anwesenheit (`source = "sessions"`) heißt zu Hause „ein Ansteckvorgang läuft“.
   - Bewertet wird ein Tag `d` (vor heute), wenn der Zustand ab `d 00:00` bekannt ist. `away`, wenn das Auto um `d 03:00` nicht zu Hause war; dann sind die übrigen Felder leer.
   - `departure`: der erste Wechsel zu Hause → weg am Tag `d` nach `d 03:00`; endete ein Ansteckvorgang des Autos in den 30 min davor, dessen Ende.
   - `departure_soc`: der letzte Ladestand in `[departure − 6 h, departure]`.
   - `return_at`: der erste Wechsel weg → zu Hause nach `departure`, dessen Aufenthalt die nächste lokale 03:00 Uhr enthält; `return_soc` der erste Ladestand in `[return_at, min(return_at + 30 min, Beginn des nächsten Ansteckvorgangs + 5 min)]`.
   - `arrival`, `arrival_soc`: der Wechsel weg → zu Hause am Tag `d`, dessen Aufenthalt `d+1 03:00` enthält, mit derselben Regel für den Ladestand.
   - `consumption_pp = departure_soc − return_soc`, wenn beide bekannt sind (kann negativ sein, wenn unterwegs geladen wurde).
6. **Profil** (`app/learning/driving_profile.py`, rein):
   - `percentile(values: Sequence[float], p: float) -> float`: lineare Interpolation zwischen den Rängen (wie `numpy.percentile` mit `linear`).
   - `Confidence = Literal["high", "medium", "low"]`.
   - `WeekdayProfile(weekday, basis: Literal["weekday", "group"], days, trips, trip_share, departure_min: int | None, departure_p50_min, departure_p80_min, departure_confidence: Confidence | None, arrival_p50_min, arrival_soc_p50, consumption_p50_pp, consumption_p80_pp, target_soc_pct: int | None, target_clamped: bool, target_confidence: Confidence | None)`; Minuten sind Minuten des lokalen Tages.
   - `VehicleProfile(key, source, data_from, data_to, days, weekdays: list[WeekdayProfile] (7), presence: list[list[float]] (7 × 96))`.
   - `build_profile(key, source, records, sessions, settings, *, today) -> VehicleProfile`. Gruppe: Wochentage 0–4, 5, 6; Gruppe statt Wochentag, wenn der Wochentag weniger als `MIN_DAYS = 3` bewertete, nicht `away` Tage hat.
   - `departure_min = floor15(P20)`, nur bei `trip_share ≥ 0,5`; `target = clamp(ceil5(reserve + max(0, P80 Verbrauch)), min, max)`.
   - `presence[w][q]`: Tage ab dem ersten Ansteckvorgang, `q` die Viertelstunde des lokalen Tages (an Umstellungstagen nach lokaler Uhrzeit, 92 bzw. 100 Slots auf 96 abgebildet), Anteil der Tage, an denen ein Ansteckvorgang die Mitte überdeckt; Gruppe wie oben.
   - `TripSuggestion(date, departure: str, departure_confidence, target_soc_pct: int | None, target_confidence)`.
   - `next_trip(profile, now) -> TripSuggestion | None`: der erste lokale Tag ab heute (höchstens 7 Tage) mit `departure_min`, dessen Abfahrt nach `now` liegt.
   - `presence_at(profile, moment) -> float`.
7. **Trefferquote** (`app/learning/driving_quality.py`, rein): `hit_rates(records, snapshots, reserve_pct, *, today, days=28) -> HitRates(departure_n, departure_hits, target_n, target_hits)`; maßgeblich ist je Tag `d` der Stand mit dem größten `computed_at` vor `d 00:00` lokal.
8. **Automatik** (`app/learning/driving_auto.py`), je Fahrzeug mit `automation[key]` an, jede Minute und nach jedem Lauf des Dienstes:
   - Zustände: `off`, `inactive` (nicht in „Geplant“), `waiting` (geplante Ladung läuft), `paused` (bis `paused_until`), `uncertain` (kein Vorschlag mit Konfidenz ab „mittel“), `applied` (Werte gesetzt), `error`.
   - Reihenfolge der Prüfung: Health vertraut (`health_trusted`), Modus `planned` in der Absicht, keine offene Zeile in `planned_charge` (außer ihr Beginn liegt höchstens 10 min zurück), `paused_until` vorbei, `next_trip` mit Konfidenz ab „mittel“ für Abfahrt bzw. Ziel.
   - Weicht `(target_soc_pct, departure, departure_on)` von der Absicht ab: `IntentService.apply(SetTarget(key, target, departure, departure_on), actor="Automatik", source="system")`; `last_set_at` und `last_values` speichern. Bei `CoreDown` oder `Rejected` Zustand `error` mit dem Text, nächster Versuch in einer Minute.
   - **Pause:** `IntentService` ruft nach jeder angenommenen `SetTarget`-Änderung mit `source` ungleich `system` die Rückmeldung `on_manual_target(key, departure_at)`; die Automatik setzt `paused_until = departure_at` (die nächste Abfahrt der neuen Absicht).
9. **Übernahme in `planned_charge`** (`app/charging/planned.py`): Ändern sich Ziel oder Abfahrt einer offenen Zeile, deren `started_at` höchstens `AUTO_ADOPT = 10 min` zurückliegt, und hat die Automatik das Auto in den letzten 2 min gesetzt (`DrivingAutomation.recently_set(key, now)`), übernimmt die Zeile Ziel, Abfahrt und Machbarkeit neu, statt mit `changed` zu enden.
10. **Erwartete Autos** (`app/planning/vehicles.py`, `shared/plan.py`):
    - `PlanVehicleInput.expected: bool = False`, `PlanVehicleInput.arrival_at: AwareDatetime | None = None`.
    - `expected_vehicles(cfg, intent, health, profiles, slots, now) -> list[PlanVehicleInput]`: Modus `planned` in der Absicht, `plugged` in Health genau `False`, `default_wallbox` gesetzt, `departure_at = next_departure(…, on)` nach dem aktuellen Slot; `arrival_at` der Beginn des ersten Slots nach dem aktuellen mit `presence_at ≥ 0,5` vor `departure_at`; `soc_pct` = `arrival_soc_p50` des Wochentags von `arrival_at`, `soc_assumed = False`; sonst nicht aufgenommen. `need_kwh`, `horizon_need_kwh` und `grid_allowed_kwh` wie bei angesteckten Autos, R2 ab `arrival_at`.
    - Optimierer: Variablen eines Autos nur in den Slots `[arrival_at, departure_at)`.
    - `PlanVehicle.expected: bool = False`, `PlanVehicle.arrival_at: AwareDatetime | None = None`; `intent_plan` übernimmt nur Autos mit `expected = False`.
    - Grundcode `AUTO_ERWARTET` (`name`, `um`, `soc`) im Slot der Ankunft, vor den Codes der Autos aus 4b; `ZIEL_SOC_UNERREICHBAR` nicht für erwartete Autos.
    - Plausibilität: „Auto {Name} lädt vor der erwarteten Ankunft um HH:MM“.
    - Auslöser `vehicles` zusätzlich, wenn sich die Menge der erwarteten Autos mit `(key, arrival_at, departure_at, target_soc_pct)` ändert.
    - Warnungen, HA („Fertig um“, „Ziel erreichbar“, „Ziel-SOC unerreichbar“) und `planned_charge` übergehen Einträge mit `expected = True`.
11. **Status des Fahrverhaltens** (`GET /api/learning/driving`):

    ```json
    {
      "computed_at": "…", "running": false,
      "status": {"ok": true, "text": "Fahrverhalten berechnet am 24.11. 03:40."},
      "vehicles": [{
        "key": "tesla", "name": "Tesla Model 3", "source": "tessie",
        "data_from": "…", "data_to": "…", "days": 52,
        "weekdays": [{"weekday": 0, "basis": "weekday", "days": 8, "trips": 8, "trip_pct": 100,
                      "departure": "07:15", "departure_p50": "07:30", "departure_p80": "07:45",
                      "departure_confidence": "high", "arrival_p50": "17:40", "arrival_soc_p50": 41.0,
                      "consumption_p50_pp": 17.0, "consumption_p80_pp": 22.0,
                      "target_soc_pct": 50, "target_clamped": true, "target_confidence": "high"}],
        "next_trip": {"date": "2026-11-24", "departure": "07:15", "departure_confidence": "high",
                      "target_soc_pct": 50, "target_confidence": "high"},
        "presence": [{"start": "…", "p": 0.88}],
        "quality": {"days": 28, "departure_n": 19, "departure_hit_pct": 89,
                    "target_n": 17, "target_hit_pct": 94},
        "automation": {"enabled": false, "state": "off", "text": "…",
                       "paused_until": null, "last_set_at": null}
      }]
    }
    ```

    `next_trip`, `quality.*_hit_pct` und die Werte je Wochentag können `null` sein; `source` ist `ha`, `home_entity`, `tessie` oder `sessions`; ein Auto ohne Stand fehlt nicht, es hat `days: 0` und leere Wochentage.
12. **Weitere Endpunkte:** `POST /api/learning/driving/recompute` (Admin, 202, 409 während eines Laufs); `PUT /api/control/vehicles/{key}/automation` mit `{"on": true}` (ab „Bedienen“, Aktion `control.automation` im Änderungsprotokoll); `PUT /api/control/vehicles/{key}/target` nimmt zusätzlich `departure_on` (`YYYY-MM-DD`).
13. **HA** (`HA_VEHICLE_SUFFIXES` in `shared/config.py`): `auto_target` (`switch`), `suggested_departure`, `suggested_target`, `next_departure` (`sensor`); Attribute `confidence` (`hoch`, `mittel`, `niedrig`) und `basis`.

## Global Constraints

- Deutsch in Texten, Logs (ganze Sätze, `tests/unit/test_layout.py`), Commits im Stil `feat(app): …`; keine Modellbezeichnungen im Repo.
- `core` und `app` importieren nur aus `shared`; die Importverträge bleiben unverändert.
- Im core ändert sich nur der Aufruf von `next_departure` in `core/control/planned.py` (mit Test): `git diff main --stat -- src/bindaems/core cerbo-watchdog` nennt am Ende nur diese Datei. In `shared` ändern sich `control.py`, `plan.py`, `settings.py` und `config.py` (HA-Suffixe).
- **Rückwärts lesbar:** Absichten ohne `departure_on`, Pläne und Eingangsdaten ohne erwartete Autos und Einstellungen ohne `learning.driving` laden unverändert. Ohne Profil, ohne Automatik und ohne erwartete Autos verhält sich alles wie mit 5a; die 33 Szenarien des Simulators und die Planungsszenarien bleiben grün, ohne gelockerte Prüfungen.
- Abdeckung ≥ 75 % gesamt, ≥ 90 % für `core/safety`, `core/control`, `core/actuators` (unverändert); `app/learning` soll ≥ 90 % erreichen.
- Prüfkette vor jedem Commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src cerbo-watchdog/bindaems_watchdog`, `uv run lint-imports`, `uv run pytest -q --cov --cov-fail-under=75` plus der 90-%-Bericht; UI: `pnpm lint`, `pnpm check`, `pnpm test`; E2E bei UI-Änderungen. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Tests schreiben nie nach `/data` (`core.data_dir` und `app.data_dir` unter `tmp_path`); InfluxDB nur über einen Fake-Reader.
- **Zeit:** Tage, Wochentage und Viertelstunden lokal (Wiener Zeit), gespeichert wird UTC; Feiertage über `app/daytype.is_holiday`.
- **Unbekannt bleibt unbekannt:** Ohne Daten gibt es keinen Vorschlag, keine Prognose und kein erwartetes Auto, nie einen Ersatzwert.
- Gelernte Werte erreichen die Safety-Schicht nie und ändern keine harte Grenze (Spec 10.5); die Automatik ist ab Werk aus.
- **Dokumentation unter Testzwang:** neue Endpunkte, HA-Objekte, Einstellungen, Grundcodes und Komponenten stehen in `docs/betrieb.md` und `tests/unit/test_docs.py` und ändern sich in derselben Task.
- **UI:** wie in 4a (44 × 44 px, keine waagrechte Scrollleiste bei 360 px, valibot-Schema je Antwort, Vertragsdateien, Texte über `format.ts`).
- **Öffentliches Repository:** keine Daten der Anlage; Werte in Tests und Demo sind erfunden.
- Kein Aktor ist live; nichts an der Anlage schalten.

## Review Focus

1. **Zeitumstellung, Mitternacht und Feiertage.**
   - Erwartet: Eine Abfahrt um 00:30 gehört zum neuen Tag; an Umstellungstagen stimmen Minuten und Viertelstunden der lokalen Uhr; ein Feiertag am Montag zählt als Sonntag; `departure_on` mit 02:30 am Tag der Sommerzeit wird 03:00.
   - Tests: Task 2 `test_next_departure_on_date_at_dst`; Task 4 `test_departure_after_midnight_belongs_to_new_day`, `test_holiday_counts_as_sunday`; Task 5 `test_presence_on_dst_days_maps_local_quarters`.
2. **Fehlende oder lückenhafte Quellen** (InfluxDB nachts weg, Tessie schweigt, HA umbenannt, kein Ladestand).
   - Erwartet: Tage ohne bekannten Zustand zählen nicht; ein Lauf ohne Daten behält den letzten Stand; ohne Ladestand gibt es Abfahrt, aber kein Ziel; die Seite nennt die Quelle.
   - Tests: Task 3 `test_zone_state_counts_as_away`, `test_read_error_raises_driving_data_error`; Task 4 `test_day_without_known_state_is_skipped`; Task 6 `test_failed_run_keeps_last_profile`.
3. **Automatik gegen Bedienung und Zählung.**
   - Erwartet: keine Änderung während einer laufenden geplanten Ladung; eine Änderung von Hand pausiert bis zur gesetzten Abfahrt; nach einer Abfahrt, zu der das Auto stecken blieb, übernimmt die neue Zeile die Werte ohne „geändert“; Schalter aus heißt sofort keine Änderung mehr.
   - Tests: Task 8 `test_no_change_while_planned_charge_runs`, `test_manual_change_pauses_until_departure`, `test_switch_off_stops_immediately`; Task 8 `test_fresh_row_adopts_automation_change`.
4. **Ungewöhnliche Tage** (mehrtägige Fahrt, Laden unterwegs, Gast oder Unbekannt an der Wallbox, zwei Fahrten am Tag).
   - Erwartet: Tage unterwegs zählen nicht; negativer Verbrauch bleibt ein Wert, der Vorschlag nimmt höchstens 0; Ansteckvorgänge von Gästen zählen für kein Auto; nur die erste Abfahrt zählt, die Rückkehr für die Nacht beendet die Fahrt.
   - Tests: Task 4 `test_multi_day_trip_marks_days_away`, `test_noon_stop_does_not_split_the_trip`; Task 5 `test_negative_consumption_counts_as_zero_for_target`; Task 3 `test_guest_sessions_are_ignored`.
5. **Erwartetes Auto kommt nicht, früher oder anders.**
   - Erwartet: Der core bekommt nie Leistung für ein erwartetes Auto; beim Anstecken plant der Auslöser neu; ein erwartetes Auto löst keine Warnung und keine geplante Ladung aus; steckt es schon (Health `plugged` wahr), ist es kein erwartetes Auto mehr.
   - Tests: Task 10 `test_intent_plan_drops_expected_cars`, `test_expected_car_has_no_unreachable_warning`; Task 9 `test_plugged_car_is_not_expected`; Task 10 `test_plug_in_of_expected_car_triggers_replan`.

## Texte

- **Konfidenz:** `high` „hoch“, `medium` „mittel“, `low` „niedrig“.
- **Wochentage:** „Mo“, „Di“, „Mi“, „Do“, „Fr“, „Sa“, „So“; Gruppen „Mo–Fr“, „Sa“, „So/Feiertag“; Hinweis bei Gruppe „(aus {Gruppe})“.
- **Karte „Fahrverhalten“** (Prognosen), je Auto: Kopf „{Name} · Quelle: {Quelle} · {n} Tage“, Quellen „Home Assistant ({entity})“, „Home Assistant (home_entity)“, „Tessie“, „nur Ansteckvorgänge“. Spalten „Tag“, „Fahrten“, „Abfahrt“ (P20, darunter „meist {P50}“), „Ankunft“, „Ladestand bei Ankunft“, „Verbrauch“ (P80), „Vorschlag Ziel“, „Konfidenz“. Ohne Fahrt „keine Fahrt erwartet“. Geklemmt „{x} % (begrenzt)“. Fußzeile „Trefferquote 28 Tage: Abfahrt {x} % ({n} Tage), Ziel {y} % ({m} Tage)“; zu wenig „Trefferquote: noch zu wenig Tage“. Leer: „Noch keine Daten für dieses Auto.“
- **Karte „Anwesenheit“:** „Wahrscheinlichkeit angesteckt, nächste 48 Stunden“, Achse „%“.
- **Taste:** „Jetzt neu berechnen“, während des Laufs „Berechnung läuft …“.
- **Status (`status.text`):** „Fahrverhalten berechnet am {TT.MM. HH:MM}.“, „Noch keine Fahrdaten.“, „Fahrverhalten fehlgeschlagen: {Fehler}“ (dazu „Es gilt der Stand vom {TT.MM. HH:MM}.“, wenn es einen gibt).
- **Fahrzeugkarte „Laden“:** „Vorschlag: {Tag} {HH:MM} · {x} % (Konfidenz {k})“; Taste „Übernehmen“; ohne „Kein Vorschlag (zu wenig Daten).“; Schalter „Ziel und Abfahrt automatisch“.
- **Zustand der Automatik (`automation.text`):**
  - `off`: „Automatik aus.“
  - `inactive`: „Automatik wartet: Das Auto steht nicht auf „Geplant“.“
  - `waiting`: „Automatik wartet: Eine geplante Ladung läuft bis {Tag} {HH:MM}.“
  - `paused`: „Automatik pausiert bis {Tag} {HH:MM}, weil Ziel oder Abfahrt von Hand geändert wurden.“
  - `uncertain`: „Automatik wartet: Der Vorschlag ist noch zu unsicher.“
  - `applied`: „Automatik: {Tag} {HH:MM}, Ziel {x} % (gesetzt {TT.MM. HH:MM}).“
  - `error`: „Automatik fehlgeschlagen: {Fehler}“
- **Zeitplan:** „{Name} erwartet ab {HH:MM}“.
- **Grundcode `AUTO_ERWARTET`:** „{name} wird um {um} Uhr mit etwa {soc} % erwartet (Anwesenheitsprognose).“
- **Plausibilität:** „Auto {Name} lädt vor der erwarteten Ankunft um {HH:MM}.“
- **Einstellungen, Abschnitt „Fahrverhalten“:** „Puffer bei der Rückkehr (%)“ mit Hilfe „So viel Ladestand soll nach der typischen Fahrt noch übrig sein.“; „Ziel-SOC mindestens (%)“, „Ziel-SOC höchstens (%)“ mit Hilfe „Vorschläge und Automatik bleiben in diesem Bereich.“; je Fahrzeug „Anwesenheit aus Home Assistant“ mit Hilfe „Optional, etwa device_tracker.… des Autos. Leer: Tessie oder home_entity.“; Fehler „Ziel-SOC mindestens liegt über höchstens.“
- **HA:** `switch` „Automatik Ziel und Abfahrt“, `sensor` „Vorschlag Abfahrt“, „Vorschlag Ziel-SOC“, „Nächste Abfahrt“.
- **Komponente `driving`:** „Fahrverhalten aktuell“, „Noch keine Fahrdaten“, „Fahrverhalten fehlgeschlagen: {Fehler}“.
- **Änderungsprotokoll:** Urheber „Automatik“; Aktion `control.automation` „Automatik an“ / „Automatik aus“.

---

### Task 1: Migration 0014 und Einstellungen

**Files:**
- Create: `src/bindaems/app/db/migrations/versions/v0014_driving.py`
- Modify: `src/bindaems/app/db/schema.py`, `src/bindaems/shared/settings.py`
- Test: `tests/unit/app/test_db.py`, `tests/unit/shared/test_settings.py`

**Interfaces:**
- Produces: Tabellen `driving_profile`, `driving_automation` (Präzisierung 1); `DrivingSettings`, `VehicleKey`, `LearningSettings.driving` (Präzisierung 2).

- [ ] **Step 1: Write the failing tests**

```python
def test_migration_0014_creates_driving_tables(engine) -> None: ...  # driving_profile, driving_automation
def test_migration_0014_keeps_model_registry(engine) -> None: ...  # Zeilen aus 0013 bleiben
def test_driving_defaults() -> None:
    d = RuntimeSettings().learning.driving
    assert (d.return_reserve_pct, d.target_min_pct, d.target_max_pct) == (20, 50, 90)
    assert d.automation == {} and d.presence_entities == {}
def test_target_min_above_max_is_rejected() -> None: ...  # „Ziel-SOC mindestens liegt über höchstens.“
def test_reserve_above_50_is_rejected() -> None: ...
def test_old_learning_settings_without_driving_load() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/test_db.py tests/unit/shared/test_settings.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** Migration, Schema und Einstellungen.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/test_db.py tests/unit/shared/test_settings.py -q && uv run mypy src`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(app): Tabellen und Einstellungen für das Fahrverhalten"
```

### Task 2: Abfahrt an einem Datum

**Files:**
- Modify: `src/bindaems/shared/control.py`, `src/bindaems/core/control/planned.py`, `src/bindaems/app/planning/vehicles.py`, `src/bindaems/app/control/intent.py`, `src/bindaems/app/control/routes.py`
- Test: `tests/unit/shared/test_control.py`, `tests/unit/core/control/test_planned.py`, `tests/unit/app/control/test_intent.py`, `tests/unit/app/control/test_routes.py`, `tests/unit/app/planning/test_vehicles.py`

**Interfaces:**
- Produces: `VehicleIntent.departure_on`, `next_departure(departure, now, on=None)`, `SetTarget.departure_on` (Präzisierung 3); `PUT /api/control/vehicles/{key}/target` mit `departure_on`.

- [ ] **Step 1: Write the failing tests**

```python
def test_next_departure_with_date() -> None:
    now = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)  # Freitag
    assert next_departure("07:15", now, date(2026, 11, 23)) == datetime(2026, 11, 23, 6, 15, tzinfo=UTC)
def test_next_departure_with_past_date_falls_back_to_daily() -> None: ...  # Datum vorbei → nächstes 07:15
def test_next_departure_on_date_at_dst() -> None: ...  # 29.3.2026 02:30 → 03:00 lokal
def test_old_intent_without_departure_on_loads() -> None: ...
def test_core_planned_uses_departure_on() -> None: ...  # PlannedState.departure_at = Montag
def test_set_target_with_time_replaces_date() -> None: ...  # departure ohne Datum löscht departure_on
def test_set_target_without_time_keeps_date() -> None: ...
def test_date_without_time_is_rejected() -> None: ...  # „Ein Abfahrtstag verlangt eine Abfahrtszeit.“
def test_audit_comment_names_the_date() -> None: ...  # „Ziel 80 %, Abfahrt Mo 23.11. 07:15“
def test_planner_departure_uses_date() -> None: ...  # app/planning/vehicles
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/shared/test_control.py tests/unit/core/control/test_planned.py tests/unit/app/control tests/unit/app/planning/test_vehicles.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** Feld, Funktion, die zwei Aufrufer, `SetTarget` und die Route; die HA-Bedienung der Abfahrtszeit sendet weiter nur `departure` (löscht also das Datum).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q -m "not slow" && uv run pytest tests/integration/test_sim_scenarios.py -q`
Expected: PASS, alle 33 Szenarien grün

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(core): Abfahrt für „Geplant“ an einem bestimmten Tag"
```

### Task 3: Daten des Fahrverhaltens lesen

**Files:**
- Create: `src/bindaems/app/learning/driving_data.py`
- Test: `tests/unit/app/learning/test_driving_data.py`

**Interfaces:**
- Consumes: `InfluxReader`, `charging_session`, `DrivingSettings.presence_entities`, `Config` (`home_entity`, `tessie`, `soc_entity`, `influxdb.ha_database`).
- Produces: `PresencePoint`, `VehicleHistory`, `DrivingDataError`, `async def load_history(reader, engine, cfg, settings, key, *, start: datetime, end: datetime) -> VehicleHistory` (Präzisierung 4).

- [ ] **Step 1: Write the failing tests**

```python
async def test_source_order_prefers_configured_entity() -> None: ...  # presence_entities vor home_entity vor tessie
async def test_home_entity_from_ha_database() -> None: ...  # value 1/0
async def test_zone_state_counts_as_away() -> None: ...  # Punkt nur mit state="Arbeit" → weg
async def test_tessie_at_home_from_status() -> None: ...  # value_bool
async def test_seed_is_last_point_before_window() -> None: ...
async def test_sessions_only_without_presence() -> None: ...  # source == "sessions"
async def test_guest_sessions_are_ignored() -> None: ...
async def test_soc_falls_back_to_ha_entity() -> None: ...
async def test_read_error_raises_driving_data_error() -> None: ...
def test_queries_are_select_only() -> None: ...  # kein INTO, beginnt mit SELECT
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_driving_data.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** `load_history` mit einer Anfrage je Quelle; Abfragen wie Präzisierung 4, Namen in Anführungszeichen, Werte escaped wie in `ha_sensors.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning/test_driving_data.py -q && uv run mypy src`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(app): Anwesenheit, Ansteckvorgänge und Ladestand für das Fahrverhalten lesen"
```

### Task 4: Aufenthalte und Tage

**Files:**
- Create: `src/bindaems/app/learning/driving_days.py`
- Test: `tests/unit/app/learning/test_driving_days.py`

**Interfaces:**
- Consumes: `VehicleHistory` (Task 3).
- Produces: `DayRecord`, `def day_records(history: VehicleHistory, *, today: date) -> list[DayRecord]` (Präzisierung 5).

- [ ] **Step 1: Write the failing tests**

```python
def test_commute_day() -> None: ...  # 07:20 weg, 17:45 heim → departure, return_at, consumption
def test_departure_uses_unplug_within_30_min() -> None: ...  # abgesteckt 07:05, weg 07:20 → 07:05
def test_departure_ignores_unplug_older_than_30_min() -> None: ...
def test_day_at_home_is_no_trip() -> None: ...  # away False, departure None
def test_multi_day_trip_marks_days_away() -> None: ...
def test_noon_stop_does_not_split_the_trip() -> None: ...  # 12:00–13:00 daheim, Rückkehr 18:00
def test_departure_after_midnight_belongs_to_new_day() -> None: ...  # 00:30
def test_holiday_counts_as_sunday() -> None: ...  # weekday 6
def test_return_soc_before_charging() -> None: ...  # Ladestand nach Beginn + 5 min zählt nicht
def test_day_without_known_state_is_skipped() -> None: ...  # vor dem ersten Punkt, ohne seed
def test_sessions_only_history() -> None: ...  # zu Hause = Ansteckvorgang läuft
def test_today_is_not_rated() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_driving_days.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** `day_records`: Zeitlinie aus `seed` und Punkten, Aufenthalte als Intervalle, dann je lokalem Tag die Regeln aus Präzisierung 5.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning/test_driving_days.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(app): Abfahrt, Rückkehr und Verbrauch je Tag"
```

### Task 5: Profil, Vorschläge, Konfidenz und Anwesenheitsprognose

**Files:**
- Create: `src/bindaems/app/learning/driving_profile.py`
- Test: `tests/unit/app/learning/test_driving_profile.py`

**Interfaces:**
- Consumes: `DayRecord` (Task 4), Ansteckvorgänge aus `VehicleHistory.sessions`, `DrivingSettings`.
- Produces: `percentile`, `Confidence`, `WeekdayProfile`, `VehicleProfile`, `build_profile(...)`, `TripSuggestion`, `next_trip(profile, now)`, `presence_at(profile, moment)` (Präzisierung 6). `VehicleProfile` ist ein pydantic-Modell (JSON für `driving_profile.profile`).

- [ ] **Step 1: Write the failing tests**

```python
def test_percentile_linear() -> None:
    assert percentile([420, 430, 440, 450, 460], 20) == 428.0
def test_departure_is_p20_floored_to_quarter() -> None: ...  # P20 07:28 → "07:15"
def test_no_trip_expected_below_half() -> None: ...  # 2 von 5 Samstagen → departure None
def test_group_used_with_fewer_than_three_days() -> None: ...  # basis "group"
def test_target_is_reserve_plus_p80_rounded_up_to_5() -> None: ...  # 20 + 22,4 → 45 → geklemmt 50
def test_target_clamped_to_max() -> None: ...  # target_clamped True
def test_negative_consumption_counts_as_zero_for_target() -> None: ...
def test_confidence_levels() -> None: ...  # hoch / mittel / niedrig an den Grenzen 6/3 und 60/120 min, 15/30 pp
def test_group_basis_is_never_high() -> None: ...
def test_no_soc_gives_departure_but_no_target() -> None: ...
def test_presence_share_per_quarter() -> None: ...
def test_presence_on_dst_days_maps_local_quarters() -> None: ...
def test_next_trip_skips_days_without_trip() -> None: ...  # Freitag 18:00 → Montag
def test_next_trip_after_todays_departure_passed() -> None: ...
def test_next_trip_none_within_seven_days() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_driving_profile.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** das Modul nach Entscheidungen 4, 6, 7, 10 und Präzisierung 6.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning/test_driving_profile.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(app): Profil, Vorschläge und Anwesenheitsprognose je Auto"
```

### Task 6: Dienst, Ablage und Trefferquote

**Files:**
- Create: `src/bindaems/app/learning/driving_quality.py`, `src/bindaems/app/learning/driving_service.py`
- Modify: `src/bindaems/app/runtime.py` (Dienst starten, Komponente `driving`)
- Test: `tests/unit/app/learning/test_driving_quality.py`, `tests/unit/app/learning/test_driving_service.py`

**Interfaces:**
- Consumes: `load_history` (Task 3), `day_records` (Task 4), `build_profile` (Task 5), Tabelle `driving_profile`.
- Produces: `HitRates`, `hit_rates(...)` (Präzisierung 7); `DrivingService` mit `async run()` (Nachtlauf 03:40, Start), `async recompute() -> bool` (falsch, wenn ein Lauf läuft), `profile(key) -> VehicleProfile | None`, `profiles() -> Mapping[str, VehicleProfile]`, `status() -> dict` (Form aus Präzisierung 11 ohne `automation`), `COMPONENT = "driving"`.

- [ ] **Step 1: Write the failing tests**

```python
def test_departure_hit_when_not_earlier() -> None: ...
def test_suggestion_of_the_evening_before_counts() -> None: ...  # Stand vor d 00:00 lokal
def test_low_confidence_days_are_not_rated() -> None: ...
def test_target_hit_needs_reserve_left() -> None: ...
async def test_run_stores_one_row_per_vehicle(tmp_path) -> None: ...
async def test_rows_older_than_70_days_are_deleted(tmp_path) -> None: ...
async def test_failed_run_keeps_last_profile(tmp_path) -> None: ...  # DrivingDataError → letzter Stand, Status-Text
async def test_profiles_load_from_database_at_start(tmp_path) -> None: ...
async def test_recompute_while_running_returns_false(tmp_path) -> None: ...
async def test_unknown_vehicle_in_settings_is_ignored(tmp_path) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_driving_quality.py tests/unit/app/learning/test_driving_service.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** Trefferquote und Dienst; Planung der Läufe wie `LoadModelService` (`TRAIN_AT`-Muster mit `RUN_AT = time(3, 40)`), Rechnen in `asyncio.to_thread`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning -q && uv run mypy src`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(app): Fahrverhalten jede Nacht berechnen und ablegen"
```

### Task 7: Rückmeldung der Bedienung an die Automatik

**Files:**
- Modify: `src/bindaems/app/control/intent.py`
- Test: `tests/unit/app/control/test_intent.py`

**Interfaces:**
- Produces: `IntentService.on_manual_target(callback: Callable[[str, datetime], None]) -> None`; der Rückruf erhält Fahrzeug und `next_departure` der neuen Absicht nach jeder angenommenen `SetTarget`-Änderung mit `source != "system"`.

- [ ] **Step 1: Write the failing tests**

```python
async def test_manual_target_change_calls_back() -> None: ...  # source "ui" → (key, Abfahrt)
async def test_system_change_does_not_call_back() -> None: ...
async def test_rejected_change_does_not_call_back() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/control/test_intent.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** in `IntentService._finish`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/control -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(app): Bedienung von Ziel und Abfahrt meldet sich bei der Automatik"
```

### Task 8: Automatik für „Geplant“ und Übernahme in der Zählung

**Files:**
- Create: `src/bindaems/app/learning/driving_auto.py`
- Modify: `src/bindaems/app/charging/planned.py`, `src/bindaems/app/runtime.py`
- Test: `tests/unit/app/learning/test_driving_auto.py`, `tests/unit/app/charging/test_planned.py`

**Interfaces:**
- Consumes: `DrivingService.profile`, `next_trip` (Task 5), `IntentService.apply` und `on_manual_target` (Task 7), `PlannedCharges` (offene Zeilen), Tabelle `driving_automation`.
- Produces: `DrivingAutomation` mit `async tick()`, `state(key) -> dict` (Feld `automation` aus Präzisierung 11), `recently_set(key, now) -> bool`, `async set_enabled(key, on, *, actor, source)`; `PlannedCharges` nimmt `recently_set: Callable[[str, datetime], bool]` (Präzisierung 9).

- [ ] **Step 1: Write the failing tests**

```python
async def test_applies_next_trip_in_planned_mode() -> None: ...  # SetTarget mit departure_on, actor "Automatik"
async def test_does_nothing_when_off() -> None: ...
async def test_inactive_outside_planned_mode() -> None: ...  # Modus PV → kein Senden
async def test_no_change_while_planned_charge_runs() -> None: ...
async def test_manual_change_pauses_until_departure() -> None: ...
async def test_uncertain_values_are_kept() -> None: ...  # Abfahrt mittel übernommen, Ziel niedrig bleibt
async def test_no_send_when_values_already_match() -> None: ...
async def test_core_down_sets_error_and_retries() -> None: ...
async def test_switch_off_stops_immediately() -> None: ...
async def test_state_survives_restart(tmp_path) -> None: ...  # paused_until aus driving_automation
def test_fresh_row_adopts_automation_change() -> None: ...  # Zeile < 10 min, recently_set → kein „changed“
def test_older_row_still_ends_changed() -> None: ...
def test_manual_change_of_fresh_row_ends_changed() -> None: ...  # recently_set falsch
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_driving_auto.py tests/unit/app/charging/test_planned.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** Automatik (Präzisierung 8) mit einer Schleife je Minute in `runtime.py` und einem `tick` nach jedem Lauf des Dienstes; Übernahme in `PlannedCharges._update` (Präzisierung 9).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app -q -m "not slow"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(app): Automatik für Ziel und Abfahrt in „Geplant“"
```

### Task 9: Erwartete Autos als Eingangsdaten

**Files:**
- Modify: `src/bindaems/app/planning/vehicles.py`, `src/bindaems/app/planning/inputs.py`, `src/bindaems/app/planning/service.py`
- Test: `tests/unit/app/planning/test_vehicles.py`, `tests/unit/app/planning/test_inputs.py`

**Interfaces:**
- Consumes: `DrivingService.profiles`, `presence_at` (Task 5), `next_departure(…, on)` (Task 2).
- Produces: `PlanVehicleInput.expected`, `PlanVehicleInput.arrival_at`, `expected_vehicles(cfg, intent, health, profiles, slots, now) -> list[PlanVehicleInput]` (Präzisierung 10); `PlanInputs.vehicles` enthält sie nach den angesteckten.

- [ ] **Step 1: Write the failing tests**

```python
def test_unplugged_planned_car_is_expected_from_arrival() -> None: ...  # Prognose 0,3 bis 17:15, 0,8 ab 17:30
def test_plugged_car_is_not_expected() -> None: ...
def test_unknown_plug_state_is_not_expected() -> None: ...  # plugged None
def test_car_in_pv_mode_is_not_expected() -> None: ...
def test_no_arrival_soc_means_not_expected() -> None: ...
def test_no_default_wallbox_means_not_expected() -> None: ...
def test_arrival_after_departure_means_not_expected() -> None: ...
def test_grid_allowed_counts_pv_from_arrival() -> None: ...  # R2 ab arrival_at
def test_old_inputs_without_expected_load() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning/test_vehicles.py tests/unit/app/planning/test_inputs.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** `expected_vehicles` und den Aufruf in der Planung.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/planning -q -m "not slow"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(app): erwartete Autos aus der Anwesenheitsprognose im Planer"
```

### Task 10: Erwartete Autos in Optimierer, Plan, Erklärung und Auslöser

**Files:**
- Modify: `src/bindaems/app/planning/optimizer.py`, `src/bindaems/app/planning/builder.py`, `src/bindaems/app/planning/explain.py`, `src/bindaems/app/planning/plausibility.py`, `src/bindaems/app/planning/fallback.py`, `src/bindaems/app/planning/triggers.py`, `src/bindaems/shared/plan.py`, `src/bindaems/app/ha/entities.py` (Warnungen und „Fertig um“ ohne erwartete Autos), `src/bindaems/app/charging/planned.py`
- Test: `tests/unit/app/planning/test_optimizer.py`, `tests/unit/app/planning/test_builder.py`, `tests/unit/app/planning/test_explain.py`, `tests/unit/app/planning/test_plausibility.py`, `tests/unit/app/planning/test_triggers.py`, `tests/unit/shared/test_plan.py`, `tests/unit/app/ha/test_entities.py`

**Interfaces:**
- Consumes: `PlanVehicleInput.expected`, `arrival_at` (Task 9).
- Produces: `PlanVehicle.expected`, `PlanVehicle.arrival_at`, Grundcode `AUTO_ERWARTET`; `intent_plan` ohne erwartete Autos (Präzisierung 10).

- [ ] **Step 1: Write the failing tests**

```python
def test_expected_car_charges_only_after_arrival() -> None: ...  # e_ct = 0 vor arrival_at
def test_expected_car_shares_night_with_battery() -> None: ...  # grid_max_import: Akku lädt weniger als ohne Auto
def test_intent_plan_drops_expected_cars() -> None: ...
def test_expected_car_reason_in_arrival_slot() -> None: ...  # AUTO_ERWARTET mit name, um, soc
def test_expected_car_has_no_unreachable_warning() -> None: ...  # kein ZIEL_SOC_UNERREICHBAR, kein HA-Problem
def test_plausibility_flags_charging_before_arrival() -> None: ...
def test_fallback_ignores_expected_cars() -> None: ...
def test_plug_in_of_expected_car_triggers_replan() -> None: ...
def test_changed_expected_set_triggers_replan() -> None: ...  # andere Ankunft oder Ziel
def test_planned_charge_ignores_expected_cars() -> None: ...
def test_old_plan_without_expected_loads() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning tests/unit/shared/test_plan.py tests/unit/app/ha -q -m "not slow"`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierung 10: Variablen ab `arrival_at`, Zusammenfassung mit `expected` und `arrival_at`, Grundcode, Befund, Auslöser, Ausnahmen in HA und Zählung.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q && uv run pytest tests/integration/test_planning_scenarios.py -q`
Expected: PASS, Planungsszenarien unverändert grün

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(app): erwartete Autos in Optimierer, Plan und Erklärungen"
```

### Task 11: API, Bedienung der Automatik, Demo-Welt und Vertragsdateien

**Files:**
- Modify: `src/bindaems/app/learning/routes.py`, `src/bindaems/app/control/routes.py`, `src/bindaems/app/audit.py` (Aktion `control.automation`), Demo-Welt (`tests/ui_world.py`), `docs/betrieb.md`, Vertragsdateien (`UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py`)
- Test: `tests/unit/app/learning/test_routes.py`, `tests/unit/app/control/test_routes.py`, `tests/unit/test_docs.py`, `tests/integration/test_ui_contract.py`

**Interfaces:**
- Consumes: `DrivingService.status`, `recompute` (Task 6), `DrivingAutomation.state`, `set_enabled` (Task 8).
- Produces: `GET /api/learning/driving`, `POST /api/learning/driving/recompute`, `PUT /api/control/vehicles/{key}/automation` (Präzisierungen 11, 12); Demo-Welt mit einem Tesla-Profil (Pendeln Mo–Fr, Automatik an) und einem e-Golf mit wenig Daten (Konfidenz niedrig).

- [ ] **Step 1: Write the failing tests**

```python
def test_driving_status_shape(client) -> None: ...  # Felder aus Präzisierung 11
def test_vehicle_without_profile_has_zero_days(client) -> None: ...
def test_recompute_needs_admin(client) -> None: ...  # 403 für Bedienen
def test_recompute_while_running_is_409(client) -> None: ...
def test_automation_switch_needs_operate_role(client) -> None: ...  # 403 für Lesen
def test_automation_switch_is_audited(client) -> None: ...  # control.automation
def test_unknown_vehicle_is_404(client) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_routes.py tests/unit/app/control/test_routes.py tests/unit/test_docs.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** Routen, Demo-Welt, Betriebshandbuch (Abschnitt „Fahrverhalten (Phase 5b)“ mit Endpunkten, Komponente, Automatik) und Vertragsdateien neu schreiben.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q -m "not slow"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests docs ui
git commit -m "feat(app): Fahrverhalten und Automatik über die API"
```

### Task 12: Home Assistant

**Files:**
- Modify: `src/bindaems/shared/config.py` (`HA_VEHICLE_SUFFIXES`), `src/bindaems/app/ha/entities.py`, `src/bindaems/app/ha/commands.py`, `docs/betrieb.md`
- Test: `tests/unit/app/ha/test_entities.py`, `tests/unit/app/ha/test_commands.py`, `tests/unit/shared/test_config.py`, `tests/unit/test_docs.py`

**Interfaces:**
- Consumes: `DrivingService.status`, `DrivingAutomation.set_enabled` (Tasks 6, 8), Health `planned.departure_at`.
- Produces: je Fahrzeug `<oid>_auto_target` (`switch`), `<oid>_suggested_departure`, `<oid>_suggested_target`, `<oid>_next_departure` (`sensor`) (Präzisierung 13).

- [ ] **Step 1: Write the failing tests**

```python
def test_vehicle_has_driving_entities() -> None: ...
def test_suggested_departure_is_timestamp_with_confidence() -> None: ...  # Attribut confidence „hoch“
def test_no_suggestion_is_unknown_not_none() -> None: ...  # „unknown“, nie der Text „None“
def test_auto_target_switch_both_directions() -> None: ...  # HA → set_enabled, Zustand → HA
def test_next_departure_from_health() -> None: ...
def test_suffix_collision_is_detected() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/ha tests/unit/shared/test_config.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** Entitäten und den Befehl des Schalters (Quelle `ha`, Rolle wie die übrigen HA-Bedienelemente); Betriebshandbuch.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit -q -m "not slow"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests docs
git commit -m "feat(app): Fahrverhalten und Automatik in Home Assistant"
```

### Task 13: UI – Prognosen, Laden, Zeitplan und Einstellungen

**Files:**
- Create: `ui/src/lib/forecast/DrivingCard.svelte`, `ui/src/lib/forecast/PresenceChart.svelte`, `ui/src/lib/settings/DrivingEditor.svelte`, je mit Test
- Modify: `ui/src/routes/prognosen/+page.svelte`, `ui/src/lib/charging/VehicleCard.svelte`, `ui/src/lib/plan/PlanBlocks.svelte`, `ui/src/lib/plan/model.ts`, `ui/src/lib/settings/LearningEditor.svelte`, `ui/src/lib/settings/LearningView.svelte`, `ui/src/lib/format.ts`, Schemas unter `ui/src/lib/api/`
- Test: die zugehörigen `*.test.ts`, `ui/e2e/` (Prognosen und Laden)

**Interfaces:**
- Consumes: Endpunkte aus Task 11, `PlanVehicle.expected`, `arrival_at` (Task 10), `PUT /api/control/vehicles/{key}/target` mit `departure_on` (Task 2).

- [ ] **Step 1: Write the failing tests**

```ts
it('zeigt je Wochentag Abfahrt, Ziel und Konfidenz', ...)          // DrivingCard
it('zeigt „keine Fahrt erwartet“ und „(aus Mo–Fr)“', ...)
it('zeigt „(begrenzt)“ bei geklemmtem Ziel', ...)
it('zeigt die Trefferquote oder „noch zu wenig Tage“', ...)
it('Übernehmen sendet Ziel, Abfahrt und Abfahrtstag', ...)          // VehicleCard
it('Schalter der Automatik nur ab Bedienen', ...)
it('zeigt den Zustand der Automatik', ...)
it('zeichnet erwartete Autos gestrichelt mit „erwartet ab“', ...)  // PlanBlocks
it('lehnt Ziel-SOC mindestens über höchstens ab', ...)             // DrivingEditor
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pnpm test`
Expected: FAIL

- [ ] **Step 3: Implement** Karten, Fahrzeugkarte, Zeitplan und Einstellungen mit den Texten aus „Texte“.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && pnpm lint && pnpm check && pnpm test && pnpm build && pnpm e2e`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui
git commit -m "feat(ui): Fahrverhalten, Vorschläge, Automatik und erwartete Autos"
```

### Task 14: Dokumentation, Abnahme und Stand der Spec

**Files:**
- Create: `docs/verification/abnahme-phase-5b.md`
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absätze „Stand Plan 5b“ in 5.3, 7.5, 7.6, 9.3, 10.4, 11.3, 12, 13, 18), `docs/betrieb.md` („Fahrverhalten ausrollen (Plan 5b)“: Datenbank sichern, core und app gemeinsam mit Regelbetrieb aus, Entität der Anwesenheit eintragen, Automatik bewusst einschalten), `docs/uebergabe.md`
- Test: `tests/unit/test_docs.py`

- [ ] **Step 1: Write the failing test**

```python
def test_docs_name_driving_endpoints_entities_and_component() -> None: ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_docs.py -q`
Expected: FAIL

- [ ] **Step 3: Write** Abnahme-Checkliste nach Entscheidung 16, Spec-Absätze mit den Festlegungen der Umsetzung, Betriebshandbuch und Übergabe.

- [ ] **Step 4: Run the whole chain**

Run: die Prüfkette aus „Global Constraints“
Expected: alles grün

- [ ] **Step 5: Commit**

```bash
git add docs tests
git commit -m "docs: Fahrverhalten (Plan 5b) in Spec, Betrieb, Abnahme und Übergabe"
```

## Selbstprüfung (beim Schreiben erledigt)

- **Spec 10.4:** Verteilung von Abfahrt und Ankunft je Auto und Wochentag (Tasks 4, 5), aus Tessie-Standort, Stecker und HA-Anwesenheit (Task 3); Ladestand bei der Ankunft (Tasks 4, 5); Abfahrt als 20. Perzentil, Ziel aus Verbrauch plus Puffer (Task 5); Automatik optional nur für „Geplant“ (Task 8); Anwesenheitsprognose für den Optimierer (Tasks 5, 9, 10).
- **Spec 10.5:** nur Eingaben, geklemmt auf einstellbare Bereiche (Task 5), keine harte Grenze berührt.
- **Spec 18:** „Vorschläge erscheinen mit Konfidenz“ (Tasks 11 bis 14).
- **Offene Punkte aus 4b:** Anwesenheitsprognose und Vorschläge (erledigt), Akku-Unterstützung im MILP (Entscheidung 12).
- **Typen:** `VehicleProfile`, `TripSuggestion`, `PlanVehicleInput.expected`/`arrival_at` und `PlanVehicle.expected`/`arrival_at` heißen in allen Tasks gleich; `next_departure` hat in Tasks 2, 5 und 9 dieselbe Signatur.

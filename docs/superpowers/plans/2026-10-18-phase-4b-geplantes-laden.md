# Phase 4b – Geplantes Laden: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Auto lädt bis zur Abfahrt auf seinen Ziel-SOC, so günstig wie möglich:
- Lademodus „Geplant“ mit Ziel-SOC und Abfahrt je Fahrzeug, in UI und Home Assistant.
- Die geplanten Autos stehen im Optimierer, gemeinsam mit dem Akku, mit Regel R2 und einem Ersatzplan.
- Der core folgt der Ladeleistung des Plans und nimmt PV-Überschuss mit. Ohne Plan lädt er nach der spätesten Startzeit.
- Am Ziel-SOC stoppt er; der Ladestand des e-Golf wird geschätzt, wenn Home Assistant keinen liefert.
- Optional gleicht er das Ladelimit des Tesla an das Ziel an.
- Home Assistant zeigt „Fertig um“, „Ziel erreichbar“ und die Warnung „Ziel-SOC unerreichbar“; die Benachrichtigungen decken alle Warnungen aus Spec 12 ab.
- Die app zählt die geplanten Ladungen für die Abnahme („Ziel-SOC in 95 % der machbaren“).

**Architecture:** 4b ändert `shared`, core, app, UI und Simulator.
- **`shared`:** `ChargeMode.PLANNED`, Ziel und Abfahrt in `VehicleIntent`, `next_departure`, Ladeleistung je Auto im Plan (`PlanSlot.ev_w`, `IntentPlanSlot.vehicles`, `Plan.vehicles`), neue Grundcodes, `CommandKind.CHARGE_LIMIT`, zwei Schlüssel je Fahrzeug in `config.yaml`, `soc_entity` wird abonniert.
- **core:**
  - `core/control/vehicle_soc.py` (neu): Ladestand je Fahrzeug, gemessen oder geschätzt.
  - `core/control/planned.py` (neu): Bedarf, späteste Startzeit und Stand des geplanten Ladens.
  - `charging.py`, `charge_modes.py`, `charge_points.py`, `surplus.py`, `peak_guard.py`, `regulation.py`: der Modus „Geplant“.
  - `core/actuators/tesla.py` und `core/safety/`: der Befehl `set_charge_limit`.
- **app:**
  - `app/planning/vehicles.py` (neu): geplante Autos aus Health, Absicht und Konfiguration; Bedarf und R2.
  - `optimizer.py`, `battery_model.py`, `intents.py`, `plausibility.py`, `builder.py`, `fallback.py`, `explain.py`, `triggers.py`, `service.py`, `sender.py`, `routes.py`: die Autos im Planer.
  - `app/control/`: `SetTarget`; `app/ha/`: Plattform `text`, neue Entitäten, Warnungen als Benachrichtigung.
  - `app/charging/planned.py` (neu) und Migration `0012`: die geplanten Ladungen für die Abnahme.
- **UI:** Ziel-SOC, Abfahrt und „Fertig um“ in der Fahrzeugkarte, die Autos im Zeitplan, Karte „Geplantes Laden“ in der Statistik.
- **Simulator und Szenarien:** Ladestand und Ladelimit im Modell, fünf Szenarien; die Szenario-Tage der Planung bekommen ein Auto.

**Tech Stack:** wie Plan 4a. Keine neuen Abhängigkeiten. Neue Schlüssel in `config.yaml` (optional, mit Standard): `vehicles.<name>.charge_efficiency_pct`, `vehicles.<name>.charge_limit_sync`. Migration `0012`.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, vor allem 5.3, 6.5, 6.6, 7.5, 7.6, 7.7, 7.10, 8.3, 9.3, 9.4, 9.5, 9.6, 11.1, 11.3, 11.5, 12, 13, 16 und 18 (Phase 4).
- Verweise wie „Spec 7.5“ beziehen sich darauf.
- Die Absätze „Stand Plan …“ der Spec gehen dem älteren Text davor vor; die Präzisierungen unten gehen beiden vor.
- Vorgänger ist Plan 4a (`docs/superpowers/plans/2026-10-17-phase-4a-statistik.md`, auf `main` seit `fb23bd0`). Seine Global Constraints und die der Pläne davor gelten weiter, ebenso die Festlegung des Betreibers vom 17.10.2026, dass PV-Strom ins Auto 0 ct kostet, und die vom 18.10.2026 zum Planer (Plausibilität und Absichten rechnen Netzladen gleich).

## Einordnung

4b ist der zweite Plan der Phase 4 (Tabelle in Spec 18, „Stand Plan 4a“).
- **Zeitpunkt:** Der Plan wird jetzt zur Freigabe geschrieben. Umgesetzt wird er erst nach der Abnahme der Phase 3 (Victron live nach 2 Wochen Dry-Run, 30 echte Tage), denn er ändert den core und den Planer, die diese Abnahme prüft (Entscheidung des Betreibers vom 17.10.2026).
- **Ergebnis an der Anlage:** core und app werden gemeinsam neu ausgerollt (die Absicht ändert ihre Form). Vorher geht der Regelbetrieb aus, danach wieder an. Ein Fahrzeug lädt erst „Geplant“, wenn es jemand darauf stellt; alle anderen Modi verhalten sich wie heute. Live bleibt hinter den vier Bedingungen aus Spec 8.7.
- **Danach:** die Abnahme der Phase 4 (30 Tage Abrechnung aus 4a, „HA in beide Richtungen“ und „Ziel-SOC“ aus 4b), dann Phase 5.

## Vorgeschlagene Entscheidungen (zur Freigabe)

Der Plan ist auf diese Vorschläge geschrieben; eine andere Wahl ändert die genannten Tasks.

1. **Zuschnitt** (Spec 18, Phase 4).
   - **In 4b:** Modus „Geplant“ mit Ziel-SOC und Abfahrt, die geplanten Autos im Optimierer mit R2, Ersatzplan für Autos, späteste Startzeit, Ladestand des e-Golf, Tesla-Ladelimit, HA vollständig (Ziel-SOC, Abfahrtszeit, „Fertig um“, „Ziel erreichbar“, „Ziel-SOC unerreichbar“), Benachrichtigungen komplett, Zählung der geplanten Ladungen für die Abnahme, Simulator- und Planungsszenarien.
   - **Nicht in 4b:** eine Anwesenheitsprognose für nicht angesteckte Autos und Vorschläge für Abfahrt und Ziel (Phase 5, Spec 10.4); die Optimierung der Akku-Unterstützung für PV-Autos im MILP (braucht diese Prognose); die Influx-Messung `plan` (Spec 11.2, bisher nicht gebaut); Autos in den echten Tagen von `export_days` (die Datei bleibt bei Version 1).
2. **Ziel und Abfahrt in der Absicht** (Spec 5.3, 7.5; Tasks 1, 2, 14).
   - `VehicleIntent` bekommt `target_soc_pct` (ganze Zahl 20 bis 100, Standard 80) und `departure` (lokale Uhrzeit `HH:MM`, Standard `07:00`). Beide bleiben auch in anderen Modi stehen, so muss beim Umschalten auf „Geplant“ nichts nachgetragen werden.
   - **Abfahrt ist eine tägliche Uhrzeit:** Es gilt das nächste Vorkommen nach jetzt (Wiener Zeit). Ist sie vorbei und das Auto steckt noch, gilt die des nächsten Tages. Das passt zu „Abfahrtszeit (HH:MM)“ in Spec 12.
   - Ein Gast kann nicht „Geplant“ laden (kein Ladestand, keine Abfahrt): Der core lehnt `guest.mode = planned` ab, UI und HA bieten es dort nicht an.
   - Alternative: eine Abfahrt als fester Zeitpunkt mit Datum. Sie müsste nach jeder Fahrt neu gesetzt werden.
3. **Ladestand der Autos** (Spec 7.5, 6.6, 19.2; Tasks 3, 4).
   - **Tesla:** aus Tessie (`vehicle.<k>.soc_pct`).
   - **e-Golf:** Der core abonniert jetzt `soc_entity` selbst (unter `vehicle.<k>.soc_pct`, wie `home_entity`). Fehlt ein frischer Wert, **schätzt** er: letzter gemessener Wert plus die Energie, die der Zähler der Wallbox seither gezählt hat, mal Wirkungsgrad, geteilt durch `usable_kwh`. Der Messwert zählt nur, wenn er höchstens 60 min vor dem Anstecken oder danach gelesen wurde (sonst kann das Auto seither gefahren sein).
   - **Ohne jeden Wert** rechnet „Geplant“ mit 0 % („angenommen“): Es lädt früh genug, kann das Ziel aber nicht erkennen und lädt, bis das Auto selbst aufhört. UI und HA sagen das.
   - Alternative: ohne Ladestand auf PV-Überschuss zurückfallen. Dann wäre das Ziel bei einem schweigenden HA nie sicher.
4. **„Geplant“ im core** (Spec 7.5; Tasks 5, 6).
   - **Mit gültigem Plan-Slot:** Die Ladeleistung des Slots ist ein fester Bedarf (wie Sofort, aber mit der Leistung des Plans); zusätzlicher PV-Überschuss kommt obendrauf. Ein Slot mit 0 W lädt nur PV-Überschuss, mit den Zeiten des PV-Modus.
   - **Ohne gültigen Slot** (kein Plan, abgelaufen, Auto erst nach dem Plan angesteckt): **späteste Startzeit** `Abfahrt − Bedarf / (P_voll · η) · 1,1 − 15 min`; davor PV-Überschuss, ab da volle Leistung bis zum Ziel. Ist die Startzeit einmal erreicht, bleibt das Auto bis zum Ziel auf voller Leistung (kein Flattern, wenn ein neuer Plan kommt, der weniger will, gilt wieder der Plan).
   - **Am Ziel-SOC stoppt der core** (Grund `target_reached`); er startet erst wieder, wenn der Ladestand 3 Prozentpunkte darunter fällt oder das Ziel steigt. Über dem Ziel lädt er auch keinen PV-Überschuss (Spec 7.5: „Ist er erreicht, stoppt das EMS“).
   - **Akku:** „Geplant“ bekommt keine Akku-Unterstützung (Spec 7.6); sein Fehlbetrag geht über den Override ans Netz, wie bei Sofort.
   - **Wächter:** Der 15-min-Wächter drosselt „Geplant“ nach PV und Min+PV, vor Sofort; Mindestleistung ist `min_a`.
   - Alternative zum Stopp: über dem Ziel weiter PV-Überschuss laden. Das wäre kostenlos, liefe aber gegen ein Ziel, das man wegen des Akkus des Autos niedrig gewählt hat.
5. **Wirkungsgrad des Ladens:** `vehicles.<name>.charge_efficiency_pct` in `config.yaml`, Standard 90 (50 bis 100). Bedarf = `(Ziel − SOC) / 100 · usable_kwh / η`. Planer und core rechnen damit gleich. Alternative: fester Wert im Code; er wäre für den e-Golf und den Tesla derselbe.
6. **Autos im Optimierer** (Spec 9.3; Tasks 8, 9, 10).
   - Im Modell stehen nur Autos, die laut Health in „Geplant“ geregelt werden und angesteckt sind (es gibt noch keine Anwesenheitsprognose). Die übrigen Autos bleiben wie heute außerhalb.
   - Je Auto und Slot bis zur Abfahrt: Ladeleistung `e` mit Ein/Aus `u` (`P_min · u ≤ e ≤ P_max · u`), Herkunft PV und Netz, Fehlmenge mit sehr hoher Strafe (2000 ct/kWh).
   - **PV deckt zuerst das Haus, dann die Autos, dann den Akku** (wie die Abrechnung und der core): Solange ein Auto aus dem Netz lädt, gibt es keine PV-Ladung des Akkus und keine Einspeisung (Binärvariable je Slot). Sonst könnte das Modell Netz über das Auto in den Akku schieben, an R1 und „Netzladen erlaubt“ vorbei.
   - **Kein Akku → Auto:** `bt_ev = 0`, passend zu Entscheidung 4.
   - **Abfahrt hinter dem Horizont:** Im Horizont muss nur der Teil des Bedarfs geladen werden, der danach nicht mehr ginge (`max(0, Bedarf − P_max · η · (Abfahrt − Horizontende))`).
   - **Planträgheit** gilt in den ersten 4 Slots auch für die Ladeleistung jedes Autos (Spec 9.3).
   - Alternative: Akku → Auto als Variable mit Freigabe je Slot. Sie bräuchte eine neue Art der Unterstützung im core und lohnt sich selten gegenüber billigem Netz in der Nacht.
7. **Regel R2** (Spec 9.3; Tasks 8, 9).
   - `E_netz_erlaubt` je Auto = Bedarf minus der PV-Überschuss nach **P25** (`max(0, P25 − Last)`) in den Slots bis zur Abfahrt; der Überschuss wird in der Reihenfolge `priority` auf die Autos verteilt.
   - Netzenergie des Autos bis zur Abfahrt ≤ `E_netz_erlaubt + ρ`, `ρ` mit 200 ct/kWh bestraft. Rangfolge damit: Ziel vor Regel R vor Kosten.
   - Sofort, Boost und Min+PV sind nicht im Modell und darum ausgenommen.
8. **Ersatzplan für Autos** (Spec 9.4; Task 11): zuerst der erwartete PV-Überschuss (P50) bis zur Abfahrt, dann die günstigsten Slots davor mit voller Leistung, bis der Bedarf gedeckt ist. R2 hält das von selbst ein, denn P50 ≥ P25.
9. **Plan an den core** (Spec 5.3; Tasks 1, 2, 12). Je Slot geht die Ladeleistung je Auto mit (`vehicles`, W, ganze Zahl). Der core lehnt negative Werte und unbekannte Fahrzeuge ab und klemmt auf die Höchstleistung des Fahrzeugs („Plan für {Name} auf {W} W begrenzt.“).
10. **Auslöser** (Spec 9.5; Task 12): neuer Auslöser `vehicles`, wenn sich bei einem geplanten Auto Ein- oder Ausstecken, Wallbox, Ziel, Abfahrt, „Ladestand angenommen“ oder das Ladelimit des Tesla ändert, und wenn ein Auto in „Geplant“ kommt oder es verlässt.
11. **Tesla-Ladelimit** (Spec 6.5, 7.5; Task 7).
    - Neuer Schlüssel `vehicles.<name>.charge_limit_sync` (Standard aus, nur mit `tessie`).
    - **An:** In „Geplant“ setzt der core das Ladelimit des Tesla einmal je Ansteckvorgang und nach jeder Änderung des Ziels auf `max(Ziel, 50)`, über Tessie `set_charge_limit`, nur an ein waches Auto (kein Wecken dafür). Danach stoppt der Tesla selbst am Ziel. Das alte Limit wird nicht zurückgestellt.
    - **Aus:** Liegt das Limit im Auto unter dem Ziel, rechnen Planer und core mit dem Limit als Ziel, und UI und HA sagen es („Ladelimit im Auto {x} % liegt unter dem Ziel.“).
    - Der Befehl zählt im Stundenbudget des Tesla (20 je Stunde) und hat einen Mindestabstand von 10 min. Eine Änderung des Limits in der Tesla-App ist keine fremde Änderung.
    - Alternative: das Limit nur anheben, nie senken. Dann stoppt der core am Ziel selbst, und jede Session kostet einen Stopp-Befehl mehr.
12. **Anzeige** (Spec 12, 13; Tasks 15, 18): „Fertig um“ ist das Ende des letzten Slots, in dem der Plan das Auto lädt; „Ziel erreichbar“ ist ja, wenn die Fehlmenge des Plans höchstens 0,1 kWh ist. Ohne Plan rechnet die app beides aus der spätesten Startzeit in Health. Der Zeitplan zeigt die geplante Ladeleistung je Auto.
13. **Home Assistant vollständig** (Spec 12; Task 15).
    - Je Fahrzeug: `number` „Ziel-SOC“ (20 bis 100 %, Schritt 1), `text` „Abfahrtszeit“ (`HH:MM`, neue Plattform `text`, eine Zeile mehr in der ACL), `sensor` „Fertig um“ (Zeitstempel), `sensor` „Ziel erreichbar“ („ja“, „nein“); im `select` „Lademodus“ die Option „Geplant“ (nicht im Gast-Modus).
    - System: `binary_sensor` „Ziel-SOC unerreichbar“.
    - Die Objekt-IDs der Statistik-Sensoren aus 4a kommen in die Prüfung der Konfiguration (offen aus 4a).
14. **Benachrichtigungen komplett** (Spec 12; Task 16). Jede Warnung aus der Tabelle in Spec 12 meldet sich, wenn sie 30 s ohne Unterbrechung besteht, einmal als `persistent_notification` (und über `notify_service`) und verschwindet mit ihr. Neu dazu kommen „Ziel-SOC unerreichbar“ (je Fahrzeug), „Preise fehlen“, „Gerät offline“, „Zuordnung unklar“ und „Mitregelndes System“; „Watchdog ausgelöst“ meldet schon der Alarm. Für „Mitregelndes System“ gibt es keine zweite Meldung, solange ein Alarm `competitor.*` der Schwere „Fehler“ gemeldet ist. Die Warnungen teilen das Budget der Alarme. Alternative: auch „Ziel erreicht“ melden; das steht nicht in der Spec und kommt täglich.
15. **Abnahme „Ziel-SOC“** (Spec 18; Task 17).
    - Die app legt je geplanter Ladung (Session in „Geplant“, je Abfahrt) eine Zeile in `planned_charge` an: Ziel, Abfahrt, Ladestand zu Beginn und an der Abfahrt, **machbar** (zu Beginn reichte volle Leistung bis zur Abfahrt, und das Ladelimit liegt nicht unter dem Ziel) und **erreicht** (Ladestand an der Abfahrt mindestens Ziel − 1 Prozentpunkt, oder das Ziel war vorher erreicht).
    - Vor der Abfahrt abgesteckt oder ohne Ladestand zählt nicht mit (eigener Zähler).
    - Die Statistik zeigt „{erreicht} von {machbar} machbaren Ladungen erreicht ({pct} %)“; die Abnahme verlangt mindestens 95 %.
    - „Alle HA-Bedienelemente wirken in beide Richtungen“ prüft ein Test je Bedienelement und ein Abschnitt der Checkliste.
16. **Simulator und Szenarien** (Tasks 8, 13).
    - Anlagensimulator: Ladestand des e-Golf über Home Assistant (abschaltbar), Zähler der EVCS, `set_charge_limit` im Tessie-Modell; fünf Szenarien `geplant-plan`, `geplant-spaet`, `geplant-pv`, `geplant-unbekannt`, `tesla-ladelimit` (dann 33).
    - Planungsszenarien: erzeugte Tage mit einem Auto (abends angesteckt, morgens Abfahrt). Bestanden heißt zusätzlich: jedes Ziel erreicht, kein Verstoß gegen R2, und die Kosten höchstens so hoch wie „Victron allein“ mit einem Auto, das ab dem Anstecken voll lädt.
17. **Nichts geht von selbst live.** „Geplant“ wirkt nur mit Regelbetrieb und in der Betriebsart der Aktoren. Ausgerollt wird nach der Abnahme der Phase 3, core und app gemeinsam, mit Regelbetrieb aus während des Wechsels.

## Präzisierungen gegenüber der Spec

1. **Absicht** (`shared/control.py`):
   - `ChargeMode.PLANNED = "planned"`.
   - `VehicleIntent.target_soc_pct: Annotated[int, Field(ge=20, le=100)] = 80`, `VehicleIntent.departure: Annotated[str, Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")] = "07:00"`.
   - `next_departure(departure: str, now: datetime) -> datetime` (UTC): das erste lokale `departure` echt nach `now`. Gibt es die lokale Uhrzeit an dem Tag nicht (Sommerzeit, 02:30), gilt die erste gültige Zeit danach (03:00); gibt es sie zweimal (Winterzeit), gilt das erste Vorkommen.
   - Gespeicherte Absichten ohne die Felder laden mit den Standardwerten.
2. **Prüfung im core** (`IntentStore._invalid`): „Ein Gast kann nicht „Geplant“ laden.“ für `guest.mode = planned`; im Plan „Plan nennt unbekanntes Fahrzeug „{key}“.“ und „Plan hat negative Ladeleistung für „{key}“.“. `_clamp_plan` klemmt `vehicles[key]` auf `max_a · U · phases` des Fahrzeugs mit dem Hinweis „Plan für {Name} auf {W} W begrenzt.“.
3. **Plan** (`shared/plan.py`):
   - `PlanSlot.ev_w: dict[str, Finite] = {}` (Ladeleistung je Fahrzeug-Schlüssel, W, nur Autos mit Leistung > 0).
   - `PlanVehicle` (neu): `key`, `name`, `wallbox`, `target_soc_pct`, `departure_at`, `soc_start_pct`, `soc_assumed: bool`, `limit_pct: float | None`, `need_kwh`, `planned_kwh`, `shortfall_kwh`, `expected_soc_pct`, `finish_at: AwareDatetime | None`, `reachable: bool`. `Plan.vehicles: list[PlanVehicle] = []`.
   - `IntentPlanSlot.vehicles: dict[str, Annotated[int, Field(ge=0)]] = {}`; `intent_plan` übernimmt `round(ev_w)`.
   - `ReasonCode` bekommt `ZIEL_SOC_ABFAHRT`, `AUTO_WARTET_AUF_PV` und `ZIEL_SOC_UNERREICHBAR`; `PlanTrigger` bekommt `vehicles`.
   - Alte gespeicherte Pläne und Eingangsdaten laden mit den Standardwerten (leere Autos).
4. **Konfiguration** (`shared/config.py`):
   - `VehicleConfig.charge_efficiency_pct: Annotated[float, Field(ge=50, le=100)] = 90.0`, `VehicleConfig.charge_limit_sync: bool = False` (verlangt `tessie`: „charge_limit_sync verlangt einen tessie-Abschnitt“).
   - `soc_signal(vehicle) -> str` = `vehicle.<k>.soc_pct`; `ha_entities` bildet `soc_signal(k) → soc_entity` für jedes Fahrzeug mit `soc_entity`. Steht dasselbe Signal in `homeassistant.entities` mit einer anderen Entität, ist die Konfiguration ungültig („vehicle.{k}.soc_pct ist in homeassistant.entities anders belegt als soc_entity“).
   - `HA_VEHICLE_SUFFIXES` bekommt `charge_energy`, `target_soc`, `departure`, `done_by`, `target_reachable`; `HA_SYSTEM_IDS` bekommt `savings_today`, `savings_month`, `autarky_today`, `problem_target_unreachable`.
5. **Ladestand** (`core/control/vehicle_soc.py`, rein):
   - `SocReading(pct: float | None, source: Literal["measured", "estimated", "assumed"])`.
   - `SocTracker.update(key, *, wall_now, measured: float | None, measured_at: datetime | None, counter_kwh: float | None, session_start: datetime | None, efficiency: float, usable_kwh: float) -> SocReading`.
   - Gemessen heißt Qualität OK und für Tessie nicht älter als `FRESHNESS_S` (900 s, wie das Signal). Ein Anker ist `(pct, counter_kwh)` zum Zeitpunkt der Messung; er gilt, solange `measured_at ≥ session_start − SOC_ANCHOR_BEFORE` (60 min). Geschätzt: `min(100, Anker + (Zähler − Zähler_Anker) · η / usable · 100)`. Sonst `assumed` mit `pct = 0`. Ein Zähler, der sinkt (Tausch), macht den Anker ungültig.
   - Der Zähler ist `wallbox.<w>.total_kwh` der Wallbox, an der das Fahrzeug steht.
6. **Bedarf und späteste Startzeit** (`core/control/planned.py`, rein; `app` rechnet dieselbe Formel, ein Test vergleicht beide):
   - `effective_target(target, limit_pct, sync) -> float` = `target`, bei `limit_pct` bekannt und `sync` falsch `min(target, limit_pct)`.
   - `need_kwh(soc_pct, target_pct, usable_kwh, efficiency) = max(0, target − soc) / 100 · usable / η`.
   - `full_power_w(vehicle, wallbox) = min(vehicle.max_a, wallbox.max_a) · U · phases`.
   - `latest_start(departure_at, need, full_w, efficiency) = departure_at − need / (full_w / 1000) · LATEST_START_MARGIN (1,1) h − LATEST_START_BUFFER (15 min)`. Ohne Bedarf gibt es keine.
   - `PlannedState`: `state` (`plan`, `surplus`, `due`, `reached`), `target_soc_pct`, `departure_at`, `need_kwh`, `latest_start`, `soc_pct`, `soc_source`.
   - `due` hält bis zum Ziel oder Ende des Ansteckvorgangs (`latched`), außer ein gültiger Slot nennt das Auto mit Leistung über 0.
   - `reached`: Ladestand bekannt (`measured` oder `estimated`) und ≥ Ziel; zurück, wenn er unter Ziel − `TARGET_RESTART_PP` (3) fällt oder das Ziel steigt.
7. **Regelung „Geplant“** (`charging.py`):
   - `Effective.PLANNED`, Code `mode_planned` („Geplant“). Boost geht vor.
   - `ChargeController.step(inputs, peak, active: ActiveSlot | None = None)`.
   - Fester Bedarf `reserved_w`: Leistung des Slots (`plan`) oder `full_power_w` (`due`), sonst 0. `SurplusCar.reserved_w` wird vor den PV-Autos vom Überschuss abgezogen. `wanted_a` = `max(ceil(reserved_w / (U · n)), Anteil)`, begrenzt auf `[min_a, max_a]`; ohne festen Bedarf wie PV.
   - Mit festem Bedarf gilt das Ziel des Phasenwächters wie bei voller Leistung (`full_power=True`); ohne wie PV.
   - Gründe: `plan_power` („Plan: Laden“), `latest_start` („Späteste Startzeit“), `target_reached` („Ziel-SOC erreicht“), dazu `pv_surplus` wie bisher.
   - `reached` stoppt wie „geladen“ (kein Start, „Laden aus“ einmal); `ModeMachine` behandelt PLANNED ohne festen Bedarf wie PV, mit festem Bedarf wie Sofort (über `ModeInput.firm: bool`).
   - Health je Fahrzeug zusätzlich: `soc_pct`, `soc_source`, `charge_limit_pct`, `planned` (`state`, `target_soc_pct`, `departure_at`, `need_kwh`, `latest_start`, `plan_w`) oder `null`.
8. **Wächter und Akku:** `_LEVELS = (PV, MIN_PV, PLANNED, NOW)`; `_floor_w` für PLANNED ist `min_w`. `SUPPORT_MODES` bleibt `{PV, MIN_PV}`.
9. **Ladelimit** (`CommandKind.CHARGE_LIMIT = "charge_limit"`, Wert in %):
   - Safety: nur `vehicle.<k>`, ganze Zahl 50 bis 100 (darunter `range.invalid`, darüber geklemmt), Mindestabstand 600 s, zählt in `per_hour`, nie dringend, nicht in `PERSISTENT_KINDS`, nicht in `WATCHED_KINDS`.
   - Aktor: `set_charge_limit?percent=N`; `present` liest `charge_limit_pct`; Bestätigung wie beim Strom im Cache (`charge_limit_pct == N` zwischen 10 und 75 s).
   - Regelung: Befehl, wenn Sync an, Modus PLANNED, angesteckt, `charge_limit_pct` bekannt und ungleich `max(Ziel, 50)`, höchstens einmal je (Ansteckvorgang, Ziel); Grund `charge_limit` („Ladelimit“). Text im Log: „Ladelimit {N} %“.
10. **Geplante Autos im Planer** (`app/planning/vehicles.py`):
    - `PlanVehicleInput`: `key`, `name`, `wallbox`, `soc_pct`, `soc_assumed`, `target_soc_pct` (wirksam), `limit_pct`, `departure_at`, `efficiency`, `usable_kwh`, `min_w`, `max_w`, `need_kwh`, `horizon_need_kwh`, `grid_allowed_kwh`.
    - Aufgenommen wird ein Fahrzeug, wenn Health es mit `mode == "planned"`, `plugged == true` und einer Wallbox nennt, Health vertraut ist (`intent_version ≥ intent.version`) und `departure_at` nach dem aktuellen Slot liegt. Ladestand, Quelle, Limit, Ziel und Abfahrt kommen aus Health (`planned`).
    - `SlotInput.pv_p25_w: Finite | None = None` (ohne Wert gilt `pv_w`); `PlanInputs.vehicles: list[PlanVehicleInput] = []`.
11. **Modell mit Autos** (`optimizer.py`), je geplantem Auto `c` und Slot `t < T_c` (`T_c` = erster Slot ab `departure_at`, höchstens Horizontende):
    - `e_ct = pv_ev_ct + gr_ev_ct`, `min_w · u_ct ≤ e_ct ≤ max_w · u_ct`; danach keine Variablen.
    - PV: `pv_h + Σ_c pv_ev + pv_b + pv_x = PV`; Netzbezug `gr_h + gr_b + Σ_c gr_ev ≤ grid_max_import_w` und in der Spitze.
    - Vorrang der Autos vor dem Akku: Binär `w_t`; `Σ_c gr_ev_ct ≤ M_t · w_t`, `pv_b_t + pv_x_t ≤ PV_t · (1 − w_t)` mit `M_t = Σ_c max_w`.
    - Bedarf: `Σ_t η_c · e_ct · Δ + short_c ≥ horizon_need_kwh_c`, Strafe `SHORTFALL_CT = 2000` je kWh.
    - R2: `Σ_t gr_ev_ct · Δ ≤ grid_allowed_kwh_c + rho_c`, Strafe `R2_CT = 200` je kWh.
    - Trägheit: in den ersten 4 Slots zusätzlich `inertia_ct · Δ · |e_ct − e_prev,ct| / 1000` mit der Leistung des vorigen Plans (`SlotInput.prev_ev_w: dict[str, float] | None`).
    - Kosten der Netzenergie der Autos stehen im Bezug (`π_t · g⁺_t`), PV ins Auto kostet nichts.
    - `SlotFlows.ev: Mapping[str, tuple[float, float]]` (`(pv_ev, gr_ev)` je Auto), `OptimizerResult.shortfall_kwh: Mapping[str, float]`.
12. **Nachrechnung** (`battery_model.py`): `SimSlot.ev_w: float = 0` (Summe der Autos). `step` deckt mit PV zuerst das Haus, dann die Autos, dann den Akku; der Akku entlädt nur für den Rest des Hauses; der Rest der Autos kommt aus dem Netz. `SimStep` bekommt `ev_pv_w` und `ev_grid_w`.
13. **Grundcodes** (`explain.py`), nach dem Hauptcode, je Auto in der Reihenfolge `priority`, vor `UNTERSTUETZUNG_RESERVIERT` und `GESCHAETZT`:
    - `ZIEL_SOC_ABFAHRT` (`name`, `kw`, `soc`, `um`) in Slots, in denen das Auto mindestens 100 W aus dem Netz lädt;
    - `AUTO_WARTET_AUF_PV` (`name`) in Slots vor der Abfahrt ohne Laden, wenn `grid_allowed_kwh < horizon_need_kwh` (R2 bindet) und ein späterer Slot das Auto aus PV lädt;
    - `ZIEL_SOC_UNERREICHBAR` (`name`, `soc`, `um`) im ersten Slot, wenn `shortfall_kwh > 0,1`.
14. **Zusammenfassung je Auto** (`builder.py`): `planned_kwh = Σ η · e · Δ`; `expected_soc_pct = soc_start + planned_kwh / usable · 100` (höchstens 100); `finish_at` = Ende des letzten Slots mit `e ≥ 1 W`, `None` ohne Laden; `reachable = shortfall ≤ 0,1 kWh` (bei Abfahrt hinter dem Horizont gegen `horizon_need_kwh`).
15. **Plausibilität** (`plausibility.py`): Bilanz mit Autos; je Auto `e` 0 oder in `[min_w, max_w]` (1 W Toleranz), kein Laden ab der Abfahrt; neue Befunde „Auto {Name} lädt außerhalb seiner Grenzen um HH:MM“ und „Auto {Name} lädt nach der Abfahrt“.
16. **Geplante Ladung** (Migration `0012`, Tabelle `planned_charge`): `id`, `session_id` (FK `charging_session.id`), `vehicle`, `target_soc_pct`, `departure_at`, `started_at`, `start_soc_pct`, `start_soc_source`, `feasible`, `reached_at` (leer), `end_soc_pct` (leer), `end_soc_source` (leer), `outcome` (`open`, `reached`, `missed`, `early`, `unknown`); Index auf `departure_at`. Eine Zeile entsteht beim ersten Health mit `planned` für eine laufende Session und je neuer `departure_at`; sie schließt an der Abfahrt (Ladestand des letzten Health davor) oder beim Ende der Session.

## Global Constraints

- **Gilt weiter:** alle Global Constraints aus 1a bis 4a, auch „Jede Task lässt das ganze Repository grün, auch den UI-Vertragstest und `tests/unit/test_docs.py`“, und die Abdeckungsgrenzen (≥ 75 % gesamt, ≥ 90 % für `core/safety`, `core/control`, `core/actuators`).
- **Rückwärts lesbar:** Eine gespeicherte Absicht, ein gespeicherter Plan und gespeicherte Eingangsdaten aus 4a laden unverändert (neue Felder mit Standard). Für Fahrzeuge in PV, Min+PV, Sofort, Aus und Boost ändert sich das Verhalten nicht; die 28 bisherigen Szenarien des Simulators und die vier Planungsszenarien bleiben grün, ohne dass ihre Prüfungen gelockert werden.
- **Importgrenzen:** `core/control/vehicle_soc.py` und `planned.py` sind rein wie der Rest von `core/control`; die app importiert nichts aus dem core. Die Formel aus Präzisierung 6 steht deshalb zweimal; `tests/unit/test_planned_formula.py` vergleicht beide.
- **Zeit:** Abfahrt in Wiener Zeit über `next_departure`; Regelung auf `ChargeInputs.now`, Abfahrt, späteste Startzeit und Plan-Slot auf `wall_now` (wie Boost und Wächter).
- **Unbekannt bleibt unbekannt:** Ein angenommener Ladestand heißt in Health, Plan, UI und HA „angenommen“, nie „gemessen“; „Ziel erreicht“ gibt es nur mit bekanntem Ladestand.
- **Dokumentation unter Testzwang:** neue Gründe, HA-Objekte, Endpunkte, Szenarien und Einstellungen stehen in `docs/betrieb.md` und `tests/unit/test_docs.py` und ändern sich in derselben Task.
- **UI:** wie in 4a (44 × 44 px, keine waagrechte Scrollleiste bei 360 px, valibot-Schema je Antwort, Vertragsdateien, Texte über `format.ts`).
- **Texte:** exakt wie in „Texte“; Tests prüfen den Wortlaut.
- **Öffentliches Repository:** keine Daten der Anlage; Werte in Tests und Demo sind erfunden.

## Review Focus

1. **Abfahrt an der Zeitumstellung und um Mitternacht.**
   - Erwartet: 02:30 am Tag der Sommerzeit wird 03:00; an der Winterzeit das erste 02:30; 00:00 um 23:59 ist in einer Minute; nach der Abfahrt rollt sie auf den nächsten Tag.
   - Tests: Task 1 `test_next_departure_on_dst_days`, `test_next_departure_rolls_over_after_passing`.
2. **Ladestand fehlt oder veraltet** (Tessie offline, HA neu verbunden, e-Golf meldet nur beim Parken).
   - Erwartet: geschätzt mit Anker aus dieser Session, sonst „angenommen“ mit 0 %; kein Stopp am Ziel ohne bekannten Ladestand; ein Zählertausch macht die Schätzung nicht negativ.
   - Tests: Task 3 `test_anchor_older_than_plug_in_is_ignored`, `test_counter_reset_drops_the_anchor`, `test_stale_tessie_value_is_not_measured`; Task 5 `test_assumed_soc_never_reports_reached`.
3. **Abstecken, neu anstecken und Abfahrt vorbei, während das Auto steckt.**
   - Erwartet: Der Merker `due` und das gesetzte Ladelimit gelten je Ansteckvorgang; nach der Abfahrt gilt die nächste, und ein erreichtes Ziel bleibt erreicht.
   - Tests: Task 5 `test_due_latch_resets_with_new_session`, `test_departure_passed_while_plugged_uses_next_day`; Task 7 `test_charge_limit_once_per_session_and_target`.
4. **Plan läuft mitten im Laden ab** (app aus, core weiter).
   - Erwartet: Übergang auf die späteste Startzeit ohne Stopp, wenn sie schon erreicht ist; sonst PV-Überschuss mit den Zeiten des PV-Modus (kein sofortiger Stopp in den Mindestlaufzeiten).
   - Tests: Task 6 `test_plan_expiry_falls_back_to_latest_start_without_stop`; Szenario `geplant-plan`.
5. **Ziel nicht machbar** (zu spät angesteckt, Limit unter dem Ziel, Netz voll).
   - Erwartet: Der Optimierer findet trotzdem eine Lösung (Fehlmenge), der Plan sagt „nicht erreichbar“ mit dem erwarteten Ladestand, HA zeigt „Ziel-SOC unerreichbar“ und meldet es einmal; die Abnahme zählt die Ladung als „nicht machbar“.
   - Tests: Task 9 `test_infeasible_target_yields_shortfall_not_infeasible`; Task 16 `test_target_unreachable_notifies_once`; Task 17 `test_late_plug_in_is_not_feasible`.

## Texte

- **Lademodus:** „Geplant“ (UI, HA, Grund `mode_planned`).
- **Gründe im Entscheidungslog:** `mode_planned` „Geplant“, `plan_power` „Plan: Laden“, `latest_start` „Späteste Startzeit“, `target_reached` „Ziel-SOC erreicht“, `charge_limit` „Ladelimit“. Befehl: „Ladelimit {N} %“.
- **Ablehnungen im core:** „Ein Gast kann nicht „Geplant“ laden.“, „Plan nennt unbekanntes Fahrzeug „{key}“.“, „Plan hat negative Ladeleistung für „{key}“.“; Hinweis „Plan für {Name} auf {W} W begrenzt.“.
- **Ablehnungen in der app:** „Ziel-SOC „{Wert}“ liegt außerhalb von 20–100 %.“, „Abfahrt „{Wert}“ ist keine Uhrzeit (HH:MM).“.
- **Fahrzeugkarte auf „Laden“:** Felder „Ziel-SOC“ (%) und „Abfahrt“, Taste „Übernehmen“. Zeilen, die erste zutreffende:
  - „Ziel erreicht ({soc} %).“
  - „Fertig um {HH:MM} · Ziel {soc} % bis {HH:MM}“
  - „Ziel {soc} % bis {HH:MM} nicht erreichbar, voraussichtlich {x} %.“
  - „Kein Plan · lädt spätestens ab {HH:MM}“
  - Zusätze: „Ladestand geschätzt“, „Ladestand unbekannt, gerechnet ab 0 %.“, „Ladelimit im Auto {x} % liegt unter dem Ziel.“
- **Grundcodes:** `ZIEL_SOC_ABFAHRT` „{name} lädt {kw} kW, damit es um {um} Uhr {soc} % hat.“; `AUTO_WARTET_AUF_PV` „{name} wartet auf PV-Überschuss (Regel R).“; `ZIEL_SOC_UNERREICHBAR` „{name} erreicht bis {um} Uhr voraussichtlich nur {soc} %.“
- **Zeitplan:** Diagramm „Autos“ mit je einer Reihe „{Name}“ (kW); unter dem Kopf je geplantem Auto eine Zeile wie in der Fahrzeugkarte.
- **HA:** „Ziel-SOC“, „Abfahrtszeit“, „Fertig um“, „Ziel erreichbar“ (Zustände „ja“, „nein“), „Ziel-SOC unerreichbar“.
- **Benachrichtigungen** (Titel „BindaEMS“): „Ziel-SOC unerreichbar: {Name} erreicht bis {HH:MM} voraussichtlich nur {soc} %.“, „Preise fehlen.“, „Gerät offline: {Namen}.“, „Zuordnung Auto und Wallbox unklar.“, „Mitregelndes System erkannt.“
- **Statistik:** Karte „Geplantes Laden“: „{a} von {b} machbaren Ladungen erreicht ({pct} %)“, darunter „Nicht machbar: {n} · Vor der Abfahrt abgesteckt: {n} · Ohne Ladestand: {n}“ und „Vor der Abfahrt geändert oder verlassen: {n}“ (nachgetragen bei der Umsetzung, Task 17/18; die Quote ist abgerundet). Leer: „Im Zeitraum gab es keine geplante Ladung.“
- **Änderungsprotokoll:** Aktion `intent.target` „Ziel geändert“, Kommentar „Ziel {soc} %, Abfahrt {HH:MM}“.

---

### Task 1: Absicht, Plan und Grundcodes in `shared`

**Files:**
- Modify: `src/bindaems/shared/control.py` (`ChargeMode.PLANNED`, `VehicleIntent`, `next_departure`), `src/bindaems/shared/plan.py` (`PlanSlot.ev_w`, `PlanVehicle`, `Plan.vehicles`, `IntentPlanSlot.vehicles`, `intent_plan`, `ReasonCode`, `PlanTrigger`), `src/bindaems/shared/decisions.py` (`_REASON_TEXT`, `_command_text` folgt in Task 7), `ui/src/lib/api/schemas.ts` (`ChargeModeSchema`, `EffectiveModeSchema`, `VehicleIntentSchema`, `PlanReasonCodeSchema`, `PlanTriggerSchema`), `ui/src/lib/charging/view.ts` (`MODES`, `REASONS`; `CHARGE_MODES` bekommt `planned`, ein neues `GUEST_MODES` ohne)
- Test: `tests/unit/shared/test_control.py`, `tests/unit/shared/test_plan.py`, `tests/unit/shared/test_decisions.py`

**Interfaces:**
- Produces: `ChargeMode.PLANNED`; `VehicleIntent.target_soc_pct: int`, `VehicleIntent.departure: str`; `next_departure(departure: str, now: datetime) -> datetime`; `PlanVehicle` (Präzisierung 3); `PlanSlot.ev_w`, `Plan.vehicles`, `IntentPlanSlot.vehicles`; `ReasonCode` mit drei neuen Werten; `PlanTrigger` mit `vehicles`.

- [ ] **Step 1: Write the failing tests**

```python
def test_vehicle_intent_defaults_and_old_files_load() -> None:
    v = VehicleIntent.model_validate({"mode": "pv"})
    assert (v.target_soc_pct, v.departure) == (80, "07:00")
    assert VehicleIntent(mode=ChargeMode.PLANNED).mode == "planned"

@pytest.mark.parametrize("bad", ["7:00", "24:00", "07:60", ""])
def test_departure_must_be_hh_mm(bad: str) -> None:
    with pytest.raises(ValidationError):
        VehicleIntent(departure=bad)

def test_next_departure_rolls_over_after_passing() -> None:
    now = vienna(2026, 10, 18, 7, 0)
    assert next_departure("07:00", now) == vienna(2026, 10, 19, 7, 0)
    assert next_departure("00:00", vienna(2026, 10, 18, 23, 59)) == vienna(2026, 10, 19, 0, 0)

def test_next_departure_on_dst_days() -> None:
    assert next_departure("02:30", vienna(2027, 3, 28, 1, 0)) == vienna(2027, 3, 28, 3, 0)
    first = next_departure("02:30", vienna(2026, 10, 25, 1, 0))
    assert first.astimezone(LOCAL_TZ).fold == 0

def test_intent_plan_carries_vehicle_power() -> None:
    plan = sample_plan(ev_w={"tesla": 7012.6})
    assert intent_plan(plan, grid_charge_allowed=False).slots[0].vehicles == {"tesla": 7013}

def test_old_plan_json_loads_without_vehicles() -> None:
    # gespeichertes JSON aus 4a (ohne ev_w und vehicles) validiert; beide leer
```

In `test_decisions.py`: `decision_text` nennt für `mode_planned`, `plan_power`, `latest_start`, `target_reached`, `charge_limit` die Texte aus „Texte“.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/shared -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierungen 1 und 3. `next_departure` nimmt das lokale Datum von `now`, baut `datetime.combine(Tag, Uhrzeit, LOCAL_TZ)`, normalisiert über UTC (das schiebt eine fehlende Zeit nach vorn) und nimmt den nächsten Tag, solange das Ergebnis nicht echt nach `now` liegt. Die UI-Schemas und `view.ts` folgen im selben Commit, damit der Vertragstest grün bleibt.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/shared tests/integration/test_ui_contract.py -q && cd ui && pnpm check && pnpm test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared tests/unit/shared ui/src/lib
git commit -m "feat(shared): Modus „Geplant“, Ziel und Abfahrt, Ladeleistung je Auto im Plan"
```

### Task 2: Konfiguration und Prüfung der Absicht im core

**Files:**
- Modify: `src/bindaems/shared/config.py` (Präzisierung 4), `deploy/config.example.yaml` (der manuelle Eintrag `vehicle.egolf.soc_pct` entfällt, `soc_entity` reicht), `src/bindaems/core/control/intent.py` (`_invalid`, `_clamp_plan`), `tests/unit/app/ha/test_entities.py` (die selbst ergänzten 4a-IDs entfallen)
- Test: `tests/unit/shared/test_config.py`, `tests/unit/core/control/test_intent.py`

**Interfaces:**
- Consumes: Task 1.
- Produces: `VehicleConfig.charge_efficiency_pct`, `VehicleConfig.charge_limit_sync`; `soc_signal(vehicle: str) -> str`; `ha_entities` mit `soc_entity`; `IntentStore` prüft Präzisierung 2.

- [ ] **Step 1: Write the failing tests**

```python
def test_soc_entity_is_subscribed_under_the_soc_signal(cfg_with_ha) -> None:
    assert ha_entities(cfg_with_ha)["vehicle.egolf.soc_pct"] == "sensor.egolf_soc"

def test_soc_entity_conflicting_with_entities_is_rejected() -> None:
    # homeassistant.entities: {"vehicle.egolf.soc_pct": "sensor.other"} → ConfigError mit dem Text aus Präzisierung 4

def test_charge_limit_sync_needs_tessie() -> None: ...

def test_object_ids_include_planned_and_statistics_entities(cfg) -> None:
    ids = ha_object_ids(cfg)
    assert {"savings_today", "problem_target_unreachable", "tesla_target_soc", "tesla_departure"} <= set(ids)

def test_guest_cannot_be_planned(store, now) -> None:
    reply = store.submit(intent(guest=GuestIntent(mode=ChargeMode.PLANNED)), now)
    assert reply.rejection == "invalid" and "Ein Gast kann nicht „Geplant“ laden." in reply.details

def test_plan_vehicle_power_is_checked_and_clamped(store, now) -> None:
    # unbekanntes Fahrzeug → invalid; tesla 20000 W → clamped auf 16·230·3 = 11040 W, Hinweis „Plan für Tesla Model 3 auf 11040 W begrenzt.“
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/shared/test_config.py tests/unit/core/control/test_intent.py tests/unit/app/ha/test_entities.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierungen 2 und 4. `IntentStore` bekommt dafür die Höchstleistung je Fahrzeug (`vehicle_max_w: Mapping[str, float]`, gebaut in `core/runtime.py` aus `max_a · grid.voltage_nominal_v · phases`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/shared tests/unit/core tests/unit/app/ha -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared src/bindaems/core deploy tests
git commit -m "feat(core): Ladestand des e-Golf aus soc_entity, Prüfung von „Geplant“ und Autos im Plan"
```

### Task 3: Ladestand je Fahrzeug, gemessen oder geschätzt

**Files:**
- Create: `src/bindaems/core/control/vehicle_soc.py`
- Modify: `src/bindaems/core/control/charge_inputs.py` (`VehicleObs.soc_pct`, `soc_at`, `charge_limit_pct`; `WallboxObs.total_kwh`; `_vehicle_obs`, `_wallbox_obs`)
- Test: `tests/unit/core/control/test_vehicle_soc.py`, `tests/unit/core/control/test_charge_inputs.py`

**Interfaces:**
- Produces: `SocReading`, `SocTracker` (Präzisierung 5), Konstante `SOC_ANCHOR_BEFORE = timedelta(minutes=60)`; `VehicleObs.soc_pct: float | None`, `VehicleObs.soc_at: datetime | None` (Zeitstempel des Werts), `VehicleObs.charge_limit_pct: float | None`, `WallboxObs.total_kwh: float | None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_measured_value_wins(tracker) -> None:
    r = tracker.update("egolf", wall_now=T, measured=55.0, measured_at=T, counter_kwh=100.0, session_start=T - H, efficiency=0.9, usable_kwh=36.0)
    assert r == SocReading(55.0, "measured")

def test_estimate_adds_counter_energy(tracker) -> None:
    # Anker 55 % bei 100,0 kWh; später ohne Messung, Zähler 104,0 → 55 + 4·0,9/36·100 = 65,0 %, "estimated"

def test_anchor_older_than_plug_in_is_ignored(tracker) -> None:
    # Messung 61 min vor session_start → "assumed", pct 0

def test_counter_reset_drops_the_anchor(tracker) -> None:
    # Zähler sinkt unter den des Ankers → "assumed"

def test_stale_tessie_value_is_not_measured() -> None:
    # charge_inputs: soc_pct mit Qualität STALE → VehicleObs.soc_pct None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/control/test_vehicle_soc.py tests/unit/core/control/test_charge_inputs.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierung 5. `_vehicle_obs` liest `vehicle.<k>.soc_pct` und `charge_limit_pct` mit `ok_number`; `soc_at` ist der Zeitstempel des Werts (wie `plugged_at`). `WallboxObs.total_kwh` aus `wallbox.<k>.total_kwh`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/control -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control tests/unit/core/control
git commit -m "feat(core): Ladestand der Autos gemessen oder aus dem Zähler der Wallbox geschätzt"
```

### Task 4: Bedarf und späteste Startzeit

**Files:**
- Create: `src/bindaems/core/control/planned.py`
- Test: `tests/unit/core/control/test_planned.py`

**Interfaces:**
- Consumes: `next_departure` (Task 1), `SocReading` (Task 3).
- Produces: `effective_target`, `need_kwh`, `full_power_w`, `latest_start`, `LATEST_START_MARGIN = 1.1`, `LATEST_START_BUFFER = timedelta(minutes=15)`, `TARGET_RESTART_PP = 3.0`; `PlannedState` (frozen: `state`, `target_soc_pct`, `departure_at`, `need_kwh`, `latest_start`, `plan_w`, `soc_pct`, `soc_source`); `PlannedTracker.step(key, *, wall_now, session: int | None, intent: VehicleIntent, soc: SocReading, limit_pct: float | None, sync: bool, full_w: float, efficiency: float, usable_kwh: float, slot_w: float | None) -> PlannedState`.

- [ ] **Step 1: Write the failing tests**

```python
def test_latest_start_formula() -> None:
    # 20 kWh Bedarf, 11040 W → 20/11,04·1,1 h ≈ 1 h 59 min 34 s, minus 15 min
    assert latest_start(DEP, 20.0, 11040.0) == DEP - timedelta(hours=20 / 11.04 * 1.1) - timedelta(minutes=15)

def test_limit_below_target_is_the_target_without_sync() -> None:
    assert effective_target(80, 70.0, sync=False) == 70.0
    assert effective_target(80, 70.0, sync=True) == 80.0

def test_states_in_order(tracker) -> None:
    # slot_w 7000 → "plan"; slot_w None vor latest_start → "surplus"; danach "due"; soc ≥ Ziel (measured) → "reached"

def test_due_latch_resets_with_new_session(tracker) -> None: ...

def test_reached_until_three_points_below(tracker) -> None:
    # Ziel 80, erreicht bei 80; 78 bleibt "reached", 76,9 nicht mehr

def test_assumed_soc_never_reports_reached(tracker) -> None: ...

def test_departure_passed_while_plugged_uses_next_day(tracker) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/control/test_planned.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierung 6. `PlannedTracker` merkt sich je Fahrzeug `(session, latched, reached_target)`; ein neuer Ansteckvorgang oder ein höheres Ziel setzt beides zurück.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core/control/test_planned.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core/control/planned.py tests/unit/core/control/test_planned.py
git commit -m "feat(core): Bedarf, späteste Startzeit und Stand des geplanten Ladens"
```

### Task 5: Modus „Geplant“ in der Laderegelung

**Files:**
- Modify: `src/bindaems/core/control/charge_modes.py` (`Effective.PLANNED`, `MODE_CODES`, `ModeInput.firm`, Regeln für PLANNED), `charge_points.py` (`wanted_a`, `can_charge` mit `reached`), `surplus.py` (`SurplusCar.reserved_w`, `share_surplus`), `charging.py` (`_mode`, `_allocate`, `_surplus_car`, `_decide`, `health`, `SocTracker` und `PlannedTracker` verdrahten, Gründe), `shared/decisions.py` (Gründe aus „Texte“)
- Test: `tests/unit/core/control/test_charge_modes.py`, `test_surplus.py`, `test_charging.py`

**Interfaces:**
- Consumes: Tasks 3, 4.
- Produces: `Effective.PLANNED`; Codes `mode_planned`, `plan_power`, `latest_start`, `target_reached`; `SurplusCar.reserved_w: float = 0.0`; Health je Fahrzeug nach Präzisierung 7 (`soc_pct`, `soc_source`, `charge_limit_pct`, `planned`).

- [ ] **Step 1: Write the failing tests**

```python
def test_planned_without_plan_charges_surplus_before_latest_start(ctl) -> None:
    # Geplant, Bedarf 10 kWh, Abfahrt in 6 h, Überschuss 0 → kein Start; Health planned.state "surplus"

def test_planned_starts_full_at_latest_start(ctl) -> None:
    # Uhr auf latest_start → Laden an mit max_a, Gründe ["mode_planned", "latest_start"]

def test_planned_stops_at_target(ctl) -> None:
    # Tesla SOC 80 gemessen, Ziel 80 → Laden aus mit "target_reached", danach kein Start bei Überschuss

def test_reserved_power_is_taken_before_pv_cars() -> None:
    # share_surplus: Geplant reserviert 4600 W, PV-Auto bekommt Überschuss − 4600

def test_boost_overrides_planned(ctl) -> None: ...

def test_other_modes_unchanged(ctl) -> None:
    # bestehende Tests von test_charging.py laufen unverändert; Health der übrigen Fahrzeuge hat planned null
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/control -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierung 7. In diesem Task gibt es noch keinen Plan-Slot (`slot_w = None` immer); `PlannedTracker` liefert `surplus`, `due` oder `reached`. `full_power` für `phase_target` gilt bei `due`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core tests/property -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests/unit/core
git commit -m "feat(core): Lademodus „Geplant“ mit spätester Startzeit und Stopp am Ziel-SOC"
```

### Task 6: Plan-Leistung, Wächter und Akku für „Geplant“

**Files:**
- Modify: `src/bindaems/core/control/plan_exec.py` (`ActiveSlot.vehicles`), `regulation.py` (`charger.step(charge, self._peak, self._active)`), `charging.py` (Slot-Leistung als `slot_w`), `peak_guard.py` (`_LEVELS`, `_floor_w`), `battery.py` (nur Test, dass PLANNED keine Unterstützung bekommt)
- Test: `tests/unit/core/control/test_plan_exec.py`, `test_charging.py`, `test_peak_throttle.py`, `test_battery.py`, `test_regulation.py`

**Interfaces:**
- Consumes: Task 5, `IntentPlanSlot.vehicles` (Task 1).
- Produces: `ActiveSlot.vehicles: Mapping[str, int]`; `ChargeController.step(inputs, peak=NO_LIMITS, active=None)`.

- [ ] **Step 1: Write the failing tests**

```python
def test_slot_power_is_a_firm_demand(ctl) -> None:
    # Slot vehicles {"tesla": 6900} → Strom ceil(6900/690) = 10 A, Gründe ["mode_planned", "plan_power"]

def test_surplus_on_top_of_slot_power(ctl) -> None:
    # Slot 4140 W (6 A), Anteil am Überschuss 10 A → 10 A

def test_zero_slot_power_behaves_like_pv(ctl) -> None: ...

def test_plan_expiry_falls_back_to_latest_start_without_stop(ctl) -> None:
    # lädt mit Plan; Plan läuft ab, latest_start schon vorbei → bleibt an, Grund "latest_start"; kein "Laden aus"

def test_planned_is_throttled_after_min_pv_before_now() -> None:
    # _in_order: PV, MIN_PV, PLANNED, NOW

def test_planned_gets_no_battery_support() -> None:
    # allowed(): PLANNED-Auto bekommt 0, sein Fehlbetrag geht in W
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/control -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierungen 7 und 8. `active_slot` kopiert `vehicles` aus dem `IntentPlanSlot`; `charging.py` gibt `slot_w = active.vehicles.get(key, 0)` an den `PlannedTracker`, wenn ein Slot gilt, sonst `None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core tests/property -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/core tests/unit/core
git commit -m "feat(core): „Geplant“ folgt der Ladeleistung des Plans, Wächter und Akku kennen den Modus"
```

### Task 7: Tesla-Ladelimit

**Files:**
- Modify: `src/bindaems/shared/control.py` (`CommandKind.CHARGE_LIMIT`), `shared/decisions.py` (`_command_text`, Grund `charge_limit`), `core/safety/limits.py` (eigene Arten für den Tesla), `core/safety/ranges.py` (`_check_charge_limit`), `core/safety/rate.py` (`_interval_s`: 600 s), `core/actuators/tesla.py` (`_request`, `present`, Prüfung im Cache), `core/control/charging_tesla.py` (`limit_command`), `core/control/charging.py` (Aufruf), `core/control/actuator.py` (`_TOLERANCE`: 0,5)
- Test: `tests/unit/core/safety/test_ranges.py`, `test_rate.py`, `tests/unit/core/actuators/test_tesla.py`, `tests/unit/core/control/test_charging_tesla.py`, `tests/property/test_safety_layer.py` (`KINDS` und Strategie)

**Interfaces:**
- Consumes: `VehicleConfig.charge_limit_sync` (Task 2), `PlannedState` (Task 4).
- Produces: `CommandKind.CHARGE_LIMIT`; `limit_command(cfg, last, key, planned: PlannedState | None, obs: VehicleObs, session: int | None, blocked: bool) -> ControlCommand | None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_charge_limit_range() -> None:
    # 49 → range.invalid (REJECTED); 101 → geklemmt auf 100; 80 → OK

def test_charge_limit_interval_and_hour_budget() -> None:
    # zweiter Befehl nach 300 s → DEFERRED; zählt in per_hour

async def test_tesla_sends_set_charge_limit(respx_mock) -> None:
    # POST …/command/set_charge_limit?percent=80&wait_for_completion=true; Cache zeigt 80 → OK, zeigt 70 → NOT_TAKEN

def test_charge_limit_once_per_session_and_target() -> None:
    # Sync an, Geplant, Limit 70, Ziel 80 → Befehl 80 mit Grund "charge_limit"; gleicher Ansteckvorgang und Ziel → kein zweiter;
    # Ziel 90 → Befehl 90; Ziel 40 → Befehl 50; Sync aus → nie

def test_limit_change_in_the_app_is_not_foreign() -> None:
    assert CommandKind.CHARGE_LIMIT not in WATCHED_KINDS
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core tests/property/test_safety_layer.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierung 9. Der Befehl geht nur an ein waches Auto (`capabilities` wie bisher, kein `wake`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/core tests/property -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems tests
git commit -m "feat(core): Ladelimit des Tesla an das Ziel angleichen (optional)"
```

### Task 8: Simulator mit Ladestand und fünf Szenarien

**Files:**
- Modify: `src/bindaems/tools/sim/cars.py` (`set_charge_limit` in `COMMANDS` und `_execute`), `model.py` (Ladestand des e-Golf über HA, abschaltbar mit `PlantModel.egolf_soc_known`; Zähler `total_kwh` der EVCS über `Ac/Energy/Forward`), `scenarios.py` (Ereignis `_Target(version, vehicle, target, departure)`, Parameter `tesla_limit_pct` in `_setup`, fünf Szenarien, `SCENARIOS`), `metrics.py` (`CarSample.soc_pct`)
- Test: `tests/integration/test_sim_scenarios.py`, `tests/unit/tools/sim/test_cars.py`

**Interfaces:**
- Consumes: Tasks 1 bis 7.
- Produces: Szenarien (Victron live wie die Plan-Szenarien, EVCS und Tesla live):
  - `geplant-plan` (1 h): Tesla 60 %, Ziel 70 %, Plan mit 6900 W in vier Slots; Prüfung: lädt mit mindestens 9 A, stoppt mit `target_reached` bei ≥ 70 %, danach kein Start.
  - `geplant-spaet` (2 h): e-Golf 50 % gemessen, Ziel 70 %, Abfahrt in 2 h, kein Plan; Prüfung: kein Laden vor `latest_start` (Health), Ziel vor der Abfahrt erreicht.
  - `geplant-pv` (2 h): sonnig, Tesla Geplant mit Slot 0 W; Prüfung: lädt nur aus Überschuss (Akku-Anteil wie bei PV), stoppt am Ziel.
  - `geplant-unbekannt` (2 h): e-Golf ohne Ladestand; Prüfung: `soc_source` „assumed“, Start zur spätesten Startzeit mit 0 %, kein `target_reached`.
  - `tesla-ladelimit` (1 h): Sync an, Limit 60 %, Ziel 75 %; Prüfung: genau ein „Ladelimit 75 %“, das Modell-Limit ist danach 75, der Tesla meldet „Complete“ bei 75 %.

- [ ] **Step 1: Write the failing test** – die fünf Namen in `SCENARIOS` und ihre Prüfungen; die Tabelle der Szenarien in `docs/betrieb.md` („Simulator“) bekommt sie, die Zahl im Text wird „dreiunddreißig“.
- [ ] **Step 2: Run to verify it fails**

Run: `uv run pytest tests/integration/test_sim_scenarios.py -q -k "geplant or ladelimit"`
Expected: FAIL

- [ ] **Step 3: Implement.** Scheitert ein Szenario, liegt der Fehler zuerst in der Regelung (eigener `fix(core)`-Commit mit Test); das Szenario ändert sich nur, wenn es physikalisch nicht zu schaffen ist.
- [ ] **Step 4: Run all scenarios**

Run: `uv run pytest tests/integration/test_sim_scenarios.py tests/unit/test_docs.py -q`
Expected: PASS, die 28 bisherigen unverändert

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools/sim tests docs/betrieb.md
git commit -m "test(sim): Ladestand und Ladelimit im Modell, fünf Szenarien für „Geplant“"
```

### Task 9: Geplante Autos als Eingangsdaten des Planers

**Files:**
- Create: `src/bindaems/app/planning/vehicles.py`
- Modify: `src/bindaems/app/planning/inputs.py` (`SlotInput.pv_p25_w`, `SlotInput.prev_ev_w`, `PlanInputs.vehicles`, `assemble_inputs` nimmt P25 und Fahrzeuge), `service.py` (`collect`: `forecast.latest().p25_w`, Health und Absicht aus `LiveState` und `IntentService.cached()`), `tests/plan_helpers.py` (`scenario(..., vehicles=..., pv_p25=...)`, `planned_car(...)`)
- Create: `tests/unit/test_planned_formula.py`
- Test: `tests/unit/app/planning/test_vehicles.py`, `test_inputs.py`

**Interfaces:**
- Consumes: Health `vehicles[].planned` (Task 5), `next_departure` (Task 1).
- Produces: `PlanVehicleInput` (Präzisierung 10); `planned_vehicles(cfg, health, intent, *, now, slots) -> list[PlanVehicleInput]`; `grid_allowed(vehicles, slots, priority) -> dict[str, float]`; `horizon_need(need, max_w, efficiency, departure_at, horizon_end) -> float`.

- [ ] **Step 1: Write the failing tests**

```python
def test_only_planned_plugged_cars_with_trusted_health() -> None:
    # tesla planned, plugged, wallbox twc → aufgenommen; egolf pv → nicht; Health mit älterer intent_version → keins

def test_r2_allowance_uses_p25_surplus_in_priority_order() -> None:
    # P25 − Last ergibt 6 kWh bis zur Abfahrt; tesla Bedarf 10, egolf 4, Vorrang tesla → tesla 4, egolf 4

def test_departure_beyond_horizon_reduces_the_need() -> None:
    # Bedarf 30 kWh, 11040 W, η 0,9, 2 h nach dem Horizont → 30 − 11,04·0,9·2 = 10,128 kWh

def test_planner_and_core_use_the_same_formula() -> None:
    # tests/unit/test_planned_formula.py: need_kwh und latest_start aus core.control.planned gleich denen aus app.planning.vehicles

def test_old_inputs_load_without_vehicles(store) -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning tests/unit/test_planned_formula.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierung 10 und Entscheidungen 6, 7.
- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/planning tests/unit/test_planned_formula.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/planning tests
git commit -m "feat(app): geplante Autos, Bedarf und Regel R2 als Eingangsdaten des Planers"
```

### Task 10: Autos im Optimierer

**Files:**
- Modify: `src/bindaems/app/planning/optimizer.py` (Präzisierung 11)
- Test: `tests/unit/app/planning/test_optimizer.py`, `tests/property/test_optimizer.py`

**Interfaces:**
- Consumes: `PlanInputs.vehicles`, `SlotInput.pv_p25_w`, `SlotInput.prev_ev_w` (Task 9).
- Produces: `SlotFlows.ev: Mapping[str, tuple[float, float]]`, `OptimizerResult.shortfall_kwh: Mapping[str, float]`; Konstanten `SHORTFALL_CT = 2000.0`, `R2_CT = 200.0`.

- [ ] **Step 1: Write the failing tests**

```python
def test_car_charges_in_the_cheapest_slots_before_departure() -> None:
    # 8 Slots, Preise 30/30/10/10/30/30/30/30 ct, Bedarf 3,1 kWh bei 11040 W → Laden in den 10-ct-Slots, Fehlmenge 0

def test_pv_goes_to_the_car_before_the_battery() -> None:
    # PV-Überschuss 5 kW, Auto lädt 7 kW → pv_ev 5 kW, gr_ev 2 kW, pv_b 0, pv_x 0

def test_rule_r2_waits_for_pv() -> None:
    # Abfahrt nach Mittag, P25 deckt den Bedarf → kein Netz ins Auto in der billigen Nacht

def test_infeasible_target_yields_shortfall_not_infeasible() -> None:
    # Bedarf 40 kWh, 2 Slots bis zur Abfahrt → status optimal, shortfall_kwh["tesla"] > 0

def test_no_battery_to_car_and_no_grid_through_car_into_battery() -> None: ...
```

Property-Test: `test_optimizer_respects_limits_and_rule_r1` bekommt zufällige geplante Autos; zusätzlich: Autos laden nie nach der Abfahrt, `e` ist 0 oder in `[min_w, max_w]`, `Σ gr_ev ≤ grid_allowed + rho`, und PV wird nicht eingespeist, solange ein Auto aus dem Netz lädt.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning/test_optimizer.py tests/property/test_optimizer.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierung 11. Ohne Autos entsteht genau das Modell von heute (gleiche Variablen, gleiche Lösung; die bisherigen Tests bleiben unverändert).
- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/planning tests/property -q`
Expected: PASS; ein Lauf mit 144 Slots und zwei Autos bleibt unter dem Zeitlimit (`test_two_cars_over_36_hours_solve_in_time`, markiert `slow`, Zeit im Ergebnis ausgegeben)

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/planning/optimizer.py tests
git commit -m "feat(app): geplante Autos im Optimierer mit Fehlmenge und Regel R2"
```

### Task 11: Nachrechnung, Plausibilität, Plan und Erklärungen mit Autos

**Files:**
- Modify: `src/bindaems/app/planning/battery_model.py` (Präzisierung 12), `intents.py` (Last ohne Autos für `HALTEN`), `plausibility.py` (Präzisierung 15), `builder.py` (`ev_w`, `Plan.vehicles`, Präzisierung 14), `explain.py` (Präzisierung 13), `fallback.py` (Entscheidung 8: `fallback_vehicles(inputs) -> dict[str, list[float]]`), `service.py` (`plan_once` reicht die Autos durch)
- Test: `tests/unit/app/planning/test_battery_model.py`, `test_plausibility.py`, `test_builder.py`, `test_explain.py`, `test_fallback.py`, `tests/property/test_plan_plausible.py`

**Interfaces:**
- Consumes: Tasks 9, 10.
- Produces: `SimSlot.ev_w`, `SimStep.ev_pv_w`, `SimStep.ev_grid_w`; `build_plan(..., ev_w: Sequence[Mapping[str, float]] = (), shortfall_kwh: Mapping[str, float] = {})`; `fallback_vehicles`.

- [ ] **Step 1: Write the failing tests**

```python
def test_battery_does_not_feed_the_car() -> None:
    # EIGENVERBRAUCH, PV 0, Haus 500 W, Auto 7000 W → Entladung 500 W, Netz 7000 W

def test_plan_summary_per_car() -> None:
    # finish_at = Ende des letzten Slots mit Laden; reachable bei Fehlmenge ≤ 0,1 kWh; expected_soc_pct aus η

def test_fallback_charges_pv_first_then_cheapest_slots() -> None: ...

def test_car_codes_follow_the_main_code() -> None:
    # Reihenfolge ERSATZPLAN, Hauptcode, ZIEL_SOC_ABFAHRT, UNTERSTUETZUNG_RESERVIERT, GESCHAETZT; Sätze wie in „Texte“

def test_car_after_departure_is_implausible() -> None: ...
```

`tests/property/test_plan_plausible.py` bekommt zufällige geplante Autos; Pläne des Optimierers bestehen weiter die Plausibilität.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning tests/property -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierungen 12 bis 15. Der Ersatzplan nutzt dieselbe Zusammenfassung je Auto.
- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/planning tests/property -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/planning tests
git commit -m "feat(app): Autos in Nachrechnung, Plausibilität, Ersatzplan und Erklärungen"
```

### Task 12: Auslöser, Übergabe und API des Plans

**Files:**
- Modify: `src/bindaems/app/planning/triggers.py` (`vehicles_fingerprint(health, intent) -> tuple`), `service.py` (`_watch` löst `vehicles` aus), `sender.py` (nichts außer, falls nötig, dem Vergleich in `core_has_plan`), `routes.py` (`slot_json.ev_w`, `plan_json.vehicles`), `ui/src/lib/api/schemas.ts`, Vertragsdatei `plan.json`
- Test: `tests/unit/app/planning/test_triggers.py`, `test_service.py`, `test_sender.py`, `test_routes.py`

**Interfaces:**
- Produces: Auslöser `vehicles` (Entscheidung 10); `GET /api/plan` mit `slots[].ev_w` (`{key: W}`) und `plan.vehicles[]` (Felder von `PlanVehicle`, Zeiten ISO).

- [ ] **Step 1: Write the failing tests**

```python
def test_plug_in_of_a_planned_car_triggers() -> None: ...
def test_target_change_triggers_but_soc_drift_does_not() -> None: ...
async def test_sender_sends_vehicle_power(service, client) -> None:
    # gesendete Absicht: plan.slots[0].vehicles == {"tesla": 6900}
```

- [ ] **Step 2: Run to verify they fail** – `uv run pytest tests/unit/app/planning -q` → FAIL
- [ ] **Step 3: Implement.** Vertragsdateien: `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py`.
- [ ] **Step 4: Run to verify they pass** – `uv run pytest tests/unit/app tests/integration/test_ui_contract.py -q && cd ui && pnpm test` → PASS
- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/planning ui/src/lib/api tests
git commit -m "feat(app): Auslöser für geplante Autos und Ladeleistung in Absicht und API"
```

### Task 13: Planungsszenarien mit einem Auto

**Files:**
- Modify: `src/bindaems/tools/scenario/data.py` (`ScenarioCar(key, arrive: str, depart: str, soc_arrive_pct, target_pct, usable_kwh, max_w, min_w, efficiency)`, `ScenarioFile.version: Literal[1, 2]`, `cars: list[ScenarioCar] = []`, `ScenarioSlot.pv_p25_w`), `synthetic.py` (je Tagesart ein Auto: an 18:00, ab 07:00, 40 % → 80 %, 60 kWh, 11 kW), `runner.py` (Auto im Plan; Ausführung: Ladeleistung des Plans, begrenzt auf den Bedarf; „Victron allein“: Auto lädt ab Ankunft voll; `DayResult.targets_reached`, `r2_violations`), `report.py` (`passed` verlangt zusätzlich alle Ziele und R2)
- Test: `tests/unit/tools/scenario/test_runner.py`, `tests/integration/test_planning_scenarios.py`

**Interfaces:**
- Produces: `ScenarioResult.targets_missed: int`, `ScenarioResult.r2_violations: int`.

- [ ] **Step 1: Write the failing tests** – `test_optimizer_beats_victron_alone` für die vier Tagesarten mit Auto: bereinigte Kosten ≤ „Victron allein“, `targets_missed == 0`, `r2_violations == 0`; Version-1-Dateien laufen ohne Auto wie bisher.
- [ ] **Step 2: Run to verify they fail** – `uv run pytest tests/unit/tools/scenario tests/integration/test_planning_scenarios.py -q` → FAIL
- [ ] **Step 3: Implement.** Ein R2-Verstoß ist ein Plan, dessen Netzenergie ins Auto `grid_allowed_kwh` um mehr als 0,1 kWh übersteigt. Scheitert eine Tagesart, liegt der Fehler zuerst im Planer.
- [ ] **Step 4: Run to verify they pass** – dieselben Befehle → PASS; die Ergebnisse (Differenz zu „Victron allein“ je Tagesart) kommen in den Bericht des Tasks.
- [ ] **Step 5: Commit**

```bash
git add src/bindaems/tools/scenario tests
git commit -m "test(app): Planungsszenarien mit einem geplanten Auto"
```

### Task 14: Ziel und Abfahrt bedienen – app

**Files:**
- Modify: `src/bindaems/app/control/intent.py` (`SetTarget(vehicle, target_soc_pct: int | None, departure: str | None)`, `_apply`, `_check`, `_audit` mit `intent.target`), `routes.py` (`PUT /api/control/vehicles/{key}/target`, Body `TargetBody{target_soc_pct: int | None, departure: str | None}`, ab „Bedienen“), `ui/src/lib/audit/view.ts` („Ziel geändert“)
- Test: `tests/unit/app/control/test_intent.py`, `test_routes.py`

**Interfaces:**
- Produces: `SetTarget`; Route wie oben (Antwort wie `PUT …/mode`); Ablehnungen aus „Texte“.

- [ ] **Step 1: Write the failing tests**

```python
async def test_set_target_changes_only_the_given_fields(service) -> None: ...
async def test_target_out_of_range_is_rejected(client) -> None:
    # 15 → 422 „Ziel-SOC „15“ liegt außerhalb von 20–100 %.“
async def test_target_change_is_audited(service, audit) -> None:
    # Aktion intent.target, Kommentar „Ziel 80 %, Abfahrt 07:00“
async def test_reader_cannot_set_target(client_reader) -> None: ...
```

- [ ] **Step 2–4: Run, implement, run** – `uv run pytest tests/unit/app/control -q`
- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/control ui/src/lib/audit tests
git commit -m "feat(app): Ziel-SOC und Abfahrt bedienen"
```

### Task 15: Home Assistant vollständig

**Files:**
- Modify: `src/bindaems/app/ha/entities.py` (Plattform `text` in `EntitySpec` und `discovery` mit `pattern`; Entitäten aus Entscheidung 13; `MODE_LABELS` mit „Geplant“, `GUEST_MODE_LABELS` ohne; `HaInputs.plan_vehicles`), `commands.py` (`target_soc`, `departure`, Option „Geplant“), `publisher.py` (`allowed_topic` mit `text`), `docs/betrieb.md` (Abschnitt 7: Tabelle, ACL-Zeile `text`), `tests/unit/test_docs.py` (`HA_PLATFORMS` mit `text` und `binary_sensor`)
- Test: `tests/unit/app/ha/test_entities.py`, `test_commands.py`, `test_publisher.py`, `tests/integration/test_ha_round_trip.py` (neu)

**Interfaces:**
- Consumes: `SetTarget` (Task 14), `Plan.vehicles` (Task 11), Health `planned` (Task 5).
- Produces: `{oid}_target_soc` (number, %, 20–100, Schritt 1), `{oid}_departure` (text, `^([01]\d|2[0-3]):[0-5]\d$`), `{oid}_done_by` (sensor, `timestamp`), `{oid}_target_reachable` (sensor, `enum`, „ja“/„nein“, Attribute `expected_soc_pct`, `departure_at`, `soc_source`), `problem_target_unreachable` (binary_sensor, `problem`, Attribut `vehicles`).

- [ ] **Step 1: Write the failing tests**

```python
def test_done_by_from_plan_and_from_latest_start_without_plan() -> None: ...
def test_guest_select_has_no_planned_option() -> None: ...
def test_departure_text_rejects_bad_time(handler) -> None:
    # „7.00“ → Ablehnung „Abfahrt „7.00“ ist keine Uhrzeit (HH:MM).“
async def test_every_command_entity_round_trips(world) -> None:
    # je Bedienelement mit command=True: Befehl aus HA → Absicht geändert → veröffentlichter Zustand gleich;
    # Änderung über die API → derselbe Zustand in HA (Abnahme „HA in beide Richtungen“)
```

- [ ] **Step 2–4: Run, implement, run** – `uv run pytest tests/unit/app/ha tests/integration/test_ha_round_trip.py tests/unit/test_docs.py -q`
- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/ha docs/betrieb.md tests
git commit -m "feat(app): Home Assistant mit Ziel-SOC, Abfahrtszeit, Fertig um und Ziel erreichbar"
```

### Task 16: Benachrichtigungen komplett

**Files:**
- Modify: `src/bindaems/app/ha/notify.py` (`WarningNotifications(inputs: Callable[[], HaInputs], notifier, clock)`, `HaNotifier.warning_raised(warning_id, text)`, `warning_cleared`), `src/bindaems/app/runtime.py` (starten), `docs/betrieb.md` (Abschnitt 7, „Benachrichtigungen“)
- Test: `tests/unit/app/ha/test_notify.py`

**Interfaces:**
- Produces: Meldungen nach Entscheidung 14, `notification_id` `bindaems_warning_<object_id>` (je Fahrzeug `bindaems_warning_<oid>_target`), Texte aus „Texte“.

- [ ] **Step 1: Write the failing tests**

```python
def test_warning_after_30_seconds_once_and_dismissed() -> None: ...
def test_target_unreachable_notifies_once() -> None:
    # bleibt unerreichbar über 10 Pläne → eine Meldung; erreichbar → dismiss; wieder unerreichbar → neue Meldung
def test_competitor_warning_silent_while_error_alarm_is_notified() -> None: ...
def test_warnings_share_the_alarm_budget() -> None: ...
```

- [ ] **Step 2–4: Run, implement, run** – `uv run pytest tests/unit/app/ha -q`
- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app docs/betrieb.md tests
git commit -m "feat(app): Benachrichtigungen für alle Warnungen aus Home Assistant"
```

### Task 17: Geplante Ladungen zählen

**Files:**
- Create: `src/bindaems/app/db/migrations/versions/v0012_planned_charge.py`, `src/bindaems/app/charging/planned.py`
- Modify: `src/bindaems/app/db/schema.py` (Präzisierung 16), `app/runtime.py` (`PlannedCharges.record` nach jedem Health), `app/stats/service.py` und `routes.py` (`planned: {reached, feasible, missed, infeasible, early, unknown}`), `ui/src/lib/api/schemas.ts`, Vertragsdatei `stats.json`
- Test: `tests/unit/app/test_db.py`, `tests/unit/app/charging/test_planned.py`, `tests/unit/app/stats/test_service.py`

**Interfaces:**
- Produces: `PlannedCharges(engine, clock, cfg)` mit `record(health, sessions: Mapping[str, StoredSession])` und `summary(first: date, last: date) -> PlannedSummary`.

- [ ] **Step 1: Write the failing tests**

```python
def test_reached_before_departure_counts() -> None: ...
def test_late_plug_in_is_not_feasible() -> None:
    # Bedarf 30 kWh, 1 h bis zur Abfahrt bei 11 kW → feasible False
def test_unplugged_before_departure_is_early() -> None: ...
def test_assumed_soc_is_unknown() -> None: ...
def test_new_departure_opens_a_new_row() -> None: ...
def test_migration_0012_creates_and_drops_planned_charge(engine) -> None: ...
```

- [ ] **Step 2–4: Run, implement, run** – `uv run pytest tests/unit/app -q`
- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app tests ui/src/lib/api
git commit -m "feat(app): geplante Ladungen für die Abnahme zählen"
```

### Task 18: UI – Fahrzeugkarte, Zeitplan, Statistik, Demo-Welt

**Files:**
- Modify: `ui/src/lib/charging/VehicleCard.svelte` (Felder, Taste, Zeilen aus „Texte“), `view.ts` (`plannedLine`), `ui/src/lib/api/endpoints.ts` (`setTarget`), `ui/src/routes/laden/+page.svelte`, `ui/src/lib/charging/GuestCard.svelte` (`GUEST_MODES`), `ui/src/lib/plan/chart.ts` und `ui/src/routes/zeitplan/+page.svelte` (Diagramm „Autos“, Zeilen je Auto), `ui/src/lib/stats/PlannedCard.svelte` (neu), `ui/src/routes/statistik/+page.svelte`, `tests/ui_world.py` (ein Tesla in „Geplant“ mit Plan und ein e-Golf mit geschätztem Ladestand), Vertragsdateien, `ui/tests/e2e/control.spec.ts`
- Test: `ui/src/lib/charging/VehicleCard.test.ts`, `view.test.ts`, `ui/src/lib/plan/chart.test.ts`, `ui/src/lib/stats/PlannedCard.test.ts`, E2E „Bedienen stellt den Tesla auf Geplant mit Ziel 80 % und Abfahrt 07:00“ (prüft die Zeile im Änderungsprotokoll „Ziel geändert“)

- [ ] **Step 1: Write the failing tests** – Komponenten und `plannedLine` je Zeile aus „Texte“; das E2E oben; `pages.spec.ts` (360 px, 44 × 44 px) läuft mit den neuen Feldern.
- [ ] **Step 2: Run to verify they fail** – `cd ui && pnpm test` → FAIL
- [ ] **Step 3: Implement.** Vertragsdateien neu schreiben.
- [ ] **Step 4: Run** – `uv run pytest tests/integration/test_ui_contract.py -q && cd ui && pnpm check && pnpm lint && pnpm test && pnpm build && pnpm e2e` → PASS
- [ ] **Step 5: Commit**

```bash
git add ui tests
git commit -m "feat(ui): Ziel-SOC, Abfahrt und Fertig um, Autos im Zeitplan, geplante Ladungen in der Statistik"
```

### Task 19: Dokumentation, Abnahme und Stand der Spec

**Files:**
- Modify: `docs/betrieb.md` („Laderegelung“: Modus „Geplant“, Ladestand, späteste Startzeit, Ladelimit, Gründe; „Planung“: Autos, R2, Ersatzplan, Auslöser, Grundcodes; Abschnitt 7 und 12; „Geplantes Laden ausrollen (Plan 4b)“), `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absätze „Stand Plan 4b“ in 5.3, 6.5, 7.5, 7.6, 7.10, 9.3, 9.4, 9.5, 9.6, 11.1, 11.3, 12, 13, 16 und 18), `docs/verification/abnahme-phase-4.md` (Abschnitte „Ziel-SOC“ und „HA in beide Richtungen“), `docs/uebergabe.md`
- Test: `uv run pytest tests/unit/test_docs.py -q`

- [ ] **Step 1: Write „Stand Plan 4b“** in die genannten Abschnitte der Spec, mit den Entscheidungen und den Festlegungen der Umsetzung.
- [ ] **Step 2: Write the acceptance sections:** „Ziel-SOC“: Zeitraum, Abruf `GET /api/stats?period=month` (`planned`), mindestens 95 % von `feasible` erreicht, Begründung für jede verfehlte Ladung; „HA in beide Richtungen“: je Bedienelement einmal in HA ändern und im UI sehen, einmal im UI ändern und in HA sehen (Tabelle mit allen Bedienelementen).
- [ ] **Step 3: Add to the guide** das Ausrollen: Regelbetrieb aus, Datenbank sichern, `docker compose pull && docker compose up -d` (core und app), Migration beim Start, `soc_entity` prüfen (der manuelle Eintrag unter `homeassistant.entities` kann entfallen), optional `charge_limit_sync`, eine `text`-Zeile in der ACL, Regelbetrieb an, einen Abend „Geplant“ im Dry-Run beobachten.
- [ ] **Step 4: Run the whole repository**

Run: `uv run ruff format --check . && uv run ruff check . && uv run mypy src cerbo-watchdog/bindaems_watchdog && uv run lint-imports && uv run pytest -q --cov --cov-fail-under=75 && uv run coverage report --include="src/bindaems/core/safety/*,src/bindaems/core/control/*,src/bindaems/core/actuators/*" --fail-under=90 && cd ui && pnpm check && pnpm lint && pnpm test && pnpm build && pnpm e2e`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add docs
git commit -m "docs: Stand Plan 4b in Spec, Betriebshandbuch, Abnahme und Übergabe"
```

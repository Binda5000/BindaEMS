# Phase 5c – Vorheizen und Vorkühlen: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** BindaEMS nutzt das Haus als Wärmespeicher: Die Klimaanlage heizt oder kühlt etwas vor, wenn Strom billig ist oder PV-Überschuss sonst eingespeist würde, und nimmt in teuren Stunden weniger auf.
- **Wärmemodell je Betriebsart** (Heizen, Kühlen) aus den Daten der Klimaanlage (Ist, Soll, Versatz, Leistung, Betriebsart), der Außentemperatur aus dem Wetterarchiv und der PV-Leistung: Grundleistung bei Versatz 0, Zeitkonstante des Hauses und „Speicher“ in kWh Strom je Kelvin.
- **Planer:** Nach dem Plan für Akku und Autos rechnet ein zweiter, kleiner Schritt, wann sich Vorheizen/Vorkühlen und Absenken lohnen, im Komfortband, das du einstellst. Der Plan zeigt das immer als Vorschau; an den core geht es erst, wenn du „Vorheizen und Vorkühlen“ einschaltest und das Modell für die laufende Betriebsart taugt.
- **core:** Die Strategie der Klimaanlage folgt dem geplanten Wunsch je Viertelstunde, mit allen Regeln aus 6a (Rangfolge, Spitze, Phasen, Schrittregel). Ohne Planwert bleibt alles wie in 6a.
- Anzeige auf „Prognosen“, „Zeitplan“, „System“, in „Warum?“ und in Home Assistant.

**Architecture:** 5c ändert app, UI, `shared` und an zwei Stellen den core.
- **core:** `core/control/thermal.py` und `core/control/regulation.py` folgen dem Planwert (Task 8). Die Betriebsart steht schon in InfluxDB und Health (Task 1 entfällt).
- **`app/learning/`:** `thermal_data.py` (Lesen aus InfluxDB und Wetterarchiv), `thermal_model.py` (Modell, rein), `thermal_service.py` (Nachtlauf, Ablage, Status), Routen in `routes.py`.
- **`app/planning/`:** `thermal_shift.py` (zweiter Schritt, ein LP mit HiGHS über PuLP, rein), Einbindung in `service.py`, `explain.py`, `plausibility.py`, `triggers.py`; `shared/plan.py` (Planwerte je Slot, `Plan.thermal`, Grundcodes).
- **Migration `0015`:** `thermal_model` (Stand je Nacht, Gerät und Betriebsart).
- **`shared/settings.py`:** `ThermalPlanSettings` unter `learning.thermal`.
- **UI:** Karte „Wärmemodell“ auf „Prognosen“, Spalte „Klima“ im „Zeitplan“, Zeile „Plan“ in der Karte „Klimaanlage“ auf „System“, Abschnitt „Vorheizen und Vorkühlen“ in den Einstellungen.
- **Home Assistant:** Schalter „Vorheizen und Vorkühlen“, je Gerät Sensor „Geplanter Versatz“.

**Tech Stack:** wie Plan 5a und 3b; keine neue Abhängigkeit (numpy kommt mit LightGBM aus 5a, PuLP/HiGHS aus 3b). Keine neuen Schlüssel in `config.yaml`.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, vor allem 7.9, 8.2, 8.3, 9.3, 9.5, 9.6, 10.3, 10.5, 11.2, 11.3, 12, 13 und 18 („Phase 5 – Lernen“, Stand Plan 5a; „Phase 6 – Wärme und Kälte“, Stand Plan 6a).
- Verweise wie „Spec 9.3“ beziehen sich darauf. Die Absätze „Stand …“ gehen dem älteren Text vor; die Präzisierungen unten gehen beiden vor.
- Vorgänger sind Plan 6a (`docs/superpowers/plans/2026-10-19-phase-6a-klimaanlage.md`, Festlegungen in `2026-10-19-phase-6a-entscheidungen.md`) und Plan 5b. Ihre Global Constraints und die der Pläne davor gelten weiter.

## Einordnung

5c ist der dritte Plan der Phase 5 (Spec 18, Stand Plan 5a: „ein Wärmemodell des Hauses und die Klimaanlage als flexible Last im Planer; es braucht Wochen mit Daten der Klimaanlage“).
- **Daten heute:** Seit dem 26.10.2026 abends läuft 6a an der Anlage im Dry-Run. InfluxDB hat seitdem `thermal` (`actual_c`, `setpoint_c`, `offset_k`, `offset_sent_k`), `power` mit `source=thermal` und die Betriebsbits als Zustände in `status` (bei jeder Änderung).
- **Was die Daten hergeben:** Im Dry-Run ist der Versatz immer 0. Daraus lassen sich die Grundleistung (Leistung gegen Temperaturdifferenz) und die Zeitkonstante (Auskühlen, wenn die Anlage nicht läuft) bestimmen, und beides zusammen ergibt den Speicher je Kelvin. Wie stark die Anlage auf einen Versatz reagiert, zeigt sich erst im Live-Betrieb von 6a; die Versatz-Ereignisse führen das Modell dann nach (Entscheidung 10).
- **Jahreszeit:** Ein Heizmodell kann es ab etwa Mitte bis Ende November geben (14 Tage Heizen mit aufgezeichneter Betriebsart), ein Kühlmodell erst im Sommer 2027. Bis dahin bleibt die jeweilige Betriebsart bei 6a.
- **Ausrollen:** frühestens nach den Abnahmen von 5a und 5b (22.11.2026) und nicht während des Wechsels von Victron auf live; wann, entscheidest du (Entscheidung 15). core und app kommen gemeinsam, mit Regelbetrieb aus während des Wechsels; Migration `0015` läuft beim Start, vorher die Datenbank sichern.
- **Wirkung:** Ab Werk ist „Vorheizen und Vorkühlen“ aus; der Plan rechnet die Verschiebung nur als Vorschau. Selbst eingeschaltet wirkt sie erst, wenn der Aktor `thermal.<name>` live ist; im Dry-Run sendet der core weiter 0 und protokolliert den Wunsch.
- **Parallel:** Der Umbau der Übersicht (Plan `2026-10-27-uebersicht-kennzahlen.md`) ändert nur `ui/`. 5c fasst die Übersicht (`ui/src/routes/+page.svelte`, `ui/src/lib/dashboard/`) und `ui/src/lib/charts/options.ts` nicht an. Überschneiden können sich `ui/src/lib/system/view.ts`, die Spec (Abschnitt 13) und `docs/uebergabe.md`: Die Umsetzung von 5c beginnt auf dem Stand von `main` nach der Übersicht oder rebased vor dem Fast-Forward darauf.
- **Danach:** 6b (Wärmepumpe) nutzt Modell und zweiten Planungsschritt mit einem Pufferspeicher statt des Hauses; das ist nicht Teil von 5c.

## Entscheidungen (freigegeben am 27.10.2026)

Der Plan ist auf diese Entscheidungen geschrieben; der Betreiber hat alle 17 am 27.10.2026 mit den vorgeschlagenen Werten freigegeben („Freigeben, Task 1 vorziehen“); die mit **Offen** markierten Punkte gelten mit dem jeweiligen Vorschlag. Entscheidung 5 und Task 1 sind bei der Umsetzung entfallen, weil die Betriebsart schon aufgezeichnet wird.

1. **Zuschnitt** (Spec 18).
   - **In 5c:** Betriebsart in InfluxDB, Wärmemodell je Betriebsart mit Nachtlauf und Status, zweiter Planungsschritt mit Vorschau, Planwert an den core, Folgen im core mit den Regeln aus 6a, Schalter, Komfortband, Anzeige in UI und HA, Simulator-Szenarien, Abnahme.
   - **Nicht in 5c:** Wärmepumpe (6b), mehrere Klimaanlagen, Raumtemperaturen einzelner Räume aus Home Assistant im Modell (die Klimaanlage regelt auf die ungünstigste), der Ölofen der Bäder als eigene Größe, Lernen des Komforts, Sonnenstrahlung als eigene Prognose (die PV-Prognose steht dafür), Wärmemodell im MILP des Akkus (Entscheidung 2).
2. **Zweiter Schritt nach dem Plan für Akku und Autos, nicht im selben MILP** (Task 6). **Offen.**
   - **Vorschlag:** Der Optimierer aus 3b/4b/5b bleibt unverändert. Danach rechnet `thermal_shift` ein kleines LP auf den fertigen Slots: Mehrverbrauch kommt zuerst aus der geplanten Einspeisung, dann aus dem Netz, aber nie in einem Slot, in dem der Akku das Haus deckt; Minderverbrauch spart Bezug, Akkuenergie (zum Restwert) oder speist ein. So gilt die Rangfolge Akku → Autos → Klima → Einspeisung (Spec 18) schon durch den Aufbau, und mit Schalter aus oder ohne Modell ist der Plan Bit für Bit der heutige.
   - **Alternative:** das Haus als dritten Speicher in den MILP. Rechnet Akku und Klima gemeinsam (etwa Vorheizen statt Netzladen), ändert aber den Optimierer, Regel R1, die Plausibilität und alle Planungsszenarien, und die Rangfolge müsste als eigene Bedingung hinein.
3. **Grey-Box-Modell statt LightGBM** (Task 4). Ein Speicher erster Ordnung je Betriebsart: Grundleistung `P₀ = u · ΔT ∓ g · PV + b` (ΔT = Soll ohne Versatz − außen beim Heizen, außen − Soll beim Kühlen), Zeitkonstante `τ` aus dem Auskühlen bzw. Aufwärmen, wenn die Anlage nicht läuft, und Speicher `C = τ · u` in kWh Strom je K. Drei Zahlen lassen sich aus wenigen Wochen schätzen und gelten auch für Tage, die es im Training nicht gab; ein LightGBM-Modell sähe nie, wie das Haus auf einen Versatz reagiert. Alternative: LightGBM für `P₀`; erst sinnvoll mit einem Jahr an Daten.
4. **Wann ein Modell taugt** (Task 4), je Betriebsart, Fenster die letzten **28 Tage**:
   - mindestens **14 Tage** mit dieser Betriebsart (ein Tag zählt mit mindestens 4 h darin)
   - mindestens **3 Auskühl-Phasen**: Anlage läuft nicht seit mindestens 2 h, PV unter 50 W, Abstand innen–außen mindestens 3 K
   - Anpassung der Grundleistung **R² ≥ 0,5** über die Slots, in denen die Anlage läuft, der Versatz 0 ist und Ist und Soll höchstens 0,5 K auseinander liegen
   - **τ zwischen 4 h und 200 h**
   - Sonst sagt der Status den ersten Grund, der fehlt; die Vorschau rechnet dann nicht.
5. **Betriebsart aufzeichnen: entfällt.** Freigegeben war „Task 1 vorziehen“. Bei der Umsetzung zeigte sich, dass die Betriebsbits schon seit 6a in InfluxDB `status` stehen (Task 1). Das Heizmodell hat beim Ausrollen also die Daten seit dem 26.10.2026.
6. **Komfortband** (Task 2, Einstellungen). **Offen.**
   - **Vorschlag:** Heizen bis **+1,5 K** vorheizen, bis **−1,0 K** absenken; Kühlen bis **−1,5 K** vorkühlen, bis **+1,0 K** wärmer werden lassen. Einstellbar in Stufen von 0,5 K: vor 0 bis 3 K, zurück 0 bis 2 K; nie über `max_offset_k` aus `config.yaml` (harte Grenze ±3 K bleibt in der Safety-Schicht).
   - **Frage an dich:** Passt das für eure Räume, oder lieber enger (etwa +1,0/−0,5 K)?
7. **Wert einer kWh je Slot** (Task 6, Präzisierung 6): Mehrverbrauch aus geplanter Einspeisung kostet den Einspeisepreis (ohne OeMAG-Wert 0), aus dem Netz den Bezugspreis plus **Marge 2 ct/kWh** (einstellbar 0 bis 20, wie die Marge beim Netzladen); Minderverbrauch spart den Bezugspreis, im Akku-Slot den Restwert des Akkus (`end_value_ct`), bei Einspeisung den Einspeisepreis. Eine Verschiebung, die im ganzen Horizont weniger als **2 ct** spart, unterbleibt.
8. **Kein Vorheizen aus dem Akku.** Der Plan nimmt Netzbezug für Mehrverbrauch nur in Slots, in denen der Akku nicht entlädt. Weicht die Wirklichkeit ab (Akku entlädt trotzdem, etwa solange Victron im Dry-Run ist), geht der core eine Stufe zurück (Task 8, Entscheidung 9).
9. **Planwert an den core** (Tasks 7, 8): je Slot `thermal_steps` (Leistungswunsch in Stufen, positiv mehr) und `thermal_grid_w` (geplanter Netzbezug dafür). Die Strategie folgt dem Planwert eine Stufe je Intervall nach oben, sofort nach unten. Vor dem Planwert gehen weiter: sofort 0 (Werte alt, Betriebsart, DEGRADED), Spitzen- und Phasendruck, Vorrang der Autos, und neu der Akku-Wächter (Akku entlädt mindestens 300 W seit 300 s bei positivem Wunsch: eine Stufe zurück). „Mehr“ bei echtem Überschuss darf über den Planwert hinaus; „weniger“ aus dem Hinweis entfällt, solange es einen Planwert gibt; „zurück bei Netzbezug“ rechnet mit `import_above_w + thermal_grid_w`. Ohne Planwert (`None`) ist die Strategie die aus 6a.
10. **Nachführen aus Versatz-Ereignissen** (Task 4). Sobald es im Live-Betrieb mindestens **3 Ereignisse** gibt (Versatz von 0 auf einen Wert, mindestens 30 min gehalten, Anlage läuft), ersetzt der Median ihres Speichers je K den Wert `C`, begrenzt auf das 0,5- bis 1,5-Fache des Werts aus Grundleistung und τ. Status und Karte zeigen beide Werte und den Fehler. Alternative: Ereignisse nur anzeigen.
11. **Schalter und Vorschau** (Tasks 2, 7): „Vorheizen und Vorkühlen“ ist ab Werk **aus**. Die Verschiebung rechnet der Plan trotzdem und zeigt sie als **Vorschau** mit der Ersparnis; an den core geht sie nur mit Schalter an, tauglichem Modell für die laufende Betriebsart und bekannter Betriebsart `heizen` oder `kuehlen`.
12. **Berechnung und Ablage** (Task 5): jede Nacht um **03:50 Uhr** lokal (nach Lastmodell 03:15 und Fahrverhalten 03:40, vor der Sicherung 04:00), beim Start und mit „Jetzt neu berechnen“ (Admin). Je Lauf eine Zeile je Gerät und Betriebsart in `thermal_model`; behalten werden 70 Tage. Scheitert ein Lauf, gilt der letzte Stand. Ein neues Modell löst neu planen aus.
13. **Sollwert und Laufzeit in der Zukunft** (Task 4): Ändert die UVR den Sollwert nach Uhrzeit (etwa Nachtabsenkung), kennt der Planer das nur aus der Vergangenheit. Je Wochentaggruppe (Mo–Fr, Sa, So/Feiertag) und lokaler Viertelstunde nimmt er den Median des Sollwerts ohne Versatz und den Anteil der Zeit, in der die Anlage lief, über 14 Tage. Verschoben wird nur in Viertelstunden mit Laufanteil mindestens 0,8; dort ist `P₀` die Grundleistung mal Laufanteil.
14. **Anzeige** (Tasks 9 bis 11): Karte „Wärmemodell“ (Prognosen), Spalte „Klima“ und Zeile „Vorheizen/Vorkühlen“ im Zeitplan (Vorschau oder aktiv), Zeile „Plan“ in der Karte „Klimaanlage“, neue Gründe in „Warum?“, HA-Schalter und Sensor. Der Chart im Zeitplan bleibt, wie er ist.
15. **Ausrollen** (Task 12): core und app gemeinsam, Regelbetrieb aus während des Wechsels, Migration `0015`, frühestens nach den Abnahmen 5a/5b am 22.11.2026; wann, entscheidest du dann.
16. **Abnahme** (Task 12): **Teil A (Vorschau, 14 Tage, Schalter aus):** Heizmodell taugt, die Vorschau steht in jedem Plan mit Ersparnis und ohne Fehler, die Karte zeigt τ und `C` plausibel. **Teil B (14 Tage live, nach deiner Entscheidung: Aktor live, Schalter an):** Ist minus Soll ohne Versatz liegt in mindestens 95 % der Viertelstunden im Komfortband ±0,5 K; der Versatz bleibt in ±3 K und folgt der Schrittregel (wie 6a); jeder positive Planwert, während der Akku entlud, ging binnen 6 min eine Stufe zurück; die geschätzte Ersparnis ist nicht negativ; die Räume in Home Assistant bleiben beim Vorheizen höchstens 0,5 K über dem eingestellten Band (Sichtprüfung, kein Code); und du hast nichts Unangenehmes gemerkt.
17. **Nichts geht von selbst live** (Spec 10.5): Schalter aus ab Werk, Aktor im Dry-Run bis zu deiner Freigabe, gelernte Werte erreichen die Safety-Schicht nie und ändern keine harte Grenze. Das Lastmodell (5a) lernt aus Tagen mit Verschiebung mit; das nehmen wir hin, weil die Verschiebung über den Tag fast energieneutral ist (Review Focus 5).

**Antworten des Betreibers (27.10.2026):**
- **Zweite Wärmequelle:** Ein Ölofen heizt die Badezimmer. Er wird nächstes Jahr durch die Wärmepumpe ersetzt, die dann das ganze Haus und das Warmwasser heizt.
  - Folge für das Modell: Seine Wärme ist nicht gemessen. Sie wirkt vor allem in den Bädern und verlängert dort das Auskühlen; die Güte (R², τ, Ereignisse) zeigt, ob das Heizmodell trotzdem trägt. Keine eigene Größe für den Ölofen in 5c.
  - Folge für den Nutzen: Der Heizteil von 5c wirkt voraussichtlich nur diesen Winter. Mit der Wärmepumpe (6b) heizt die Klimaanlage kaum noch; 6b bekommt dann ein eigenes Modell mit dem Pufferspeicher. Der Kühlteil bleibt.
- **„Klima KNX Ist“ (UVR A9)** ist beim Heizen die niedrigste und beim Kühlen die höchste Raumtemperatur. Das Modell lernt also genau die Größe, auf die die Klimaanlage regelt, und das Komfortband gilt für den ungünstigsten Raum; die anderen Räume liegen beim Vorheizen ähnlich darüber. Die einzelnen Räume stehen in Home Assistant; im Modell nutzt 5c sie nicht (Entscheidung 1), zur Kontrolle in der Abnahme Teil B schon (Entscheidung 16).
- **Nachtabsenkung:** gibt es derzeit nicht. Entscheidung 13 bleibt trotzdem: Der Sollwert-Median ist dann über den Tag gleich, und der Laufanteil je Viertelstunde zeigt, wann die Anlage läuft.

## Präzisierungen gegenüber der Spec

1. **Aufzeichnung:** unverändert. Die Betriebsbits stehen in `status` (`id` = `thermal.<name>.running|heating|cooling|venting`, `value_bool`, nur bei Änderung); Health nennt `mode`, `running`, `actual_c`, `base_setpoint_c`.
2. **Einstellungen** (`shared/settings.py`):
   - `ThermalPlanSettings`: `enabled: bool = False`, `heat_up_k = 1.5`, `heat_down_k = 1.0`, `cool_up_k = 1.5`, `cool_down_k = 1.0` (Vielfache von 0,5; vor 0 bis 3, zurück 0 bis 2), `margin_ct = 2.0` (0 bis 20).
   - `LearningSettings.thermal: ThermalPlanSettings = ThermalPlanSettings()`; alte Einstellungen ohne `thermal` laden.
   - Fehlertext bei einem Wert, der kein Vielfaches von 0,5 ist: „Das Komfortband geht in Schritten von 0,5 K.“
3. **Migration `0015`** (`v0015_thermal_model.py`): Tabelle `thermal_model` mit `id`, `computed_at` (UTC), `device` (String 32), `mode` (`heizen`/`kuehlen`), `params` (JSON: `ModeModel` ohne Profile), `profile` (JSON: Sollwert und Laufanteil je Gruppe und Viertelstunde), `usable` (bool), `reason` (String 32); Index auf `(device, mode, computed_at)`.
4. **Daten** (`app/learning/thermal_data.py`), je Viertelstunde (UTC-Beginn) im Fenster:
   - Mittel von `actual_c`, `setpoint_c`, `offset_k`, `p_w` (Messung `power`, `source=thermal`), PV gesamt (`power`, `source=pv`, `phase=total`, über alle `id` summiert) aus InfluxDB `raw`; Außentemperatur aus dem Wetterarchiv (5a).
   - Die Bits aus `status`: je Bit der letzte Wert vor dem Fenster (eine Abfrage `LAST` bis zum Fensterbeginn) und alle Änderungen im Fenster; daraus der Zeitanteil „wahr“ je Viertelstunde. Ohne bekannten Wert (vor dem ersten Punkt) ist der Anteil unbekannt. `running`: wahr, wenn der Anteil mindestens 80 % ist, falsch bei höchstens 20 %, sonst `None`. `mode`: `heizen`/`kuehlen`/`lueften` nach dem Anteil wie bei `running`, `aus` bei `running` falsch, sonst `None`.
   - Slots ohne `actual_c` oder ohne Außentemperatur fehlen; es gibt nie einen Ersatzwert.
5. **Modell** (`app/learning/thermal_model.py`, rein), je Betriebsart `m` und Fenster:
   - **Grundleistung:** kleinste Quadrate (numpy `lstsq`) über die tauglichen Slots (Entscheidung 4): `p_w ≈ u · ΔT + s · pv_kw + b` mit `ΔT` wie in Entscheidung 3; `s` frei im Vorzeichen. `P₀(slot) = max(0, u · ΔT + s · pv_kw + b)`. R² über dieselben Slots.
   - **Zeitkonstante:** je Auskühl-Phase lineare Anpassung von `ln |T_innen − T_außen|` über der Zeit in h, `τ = −1 / Steigung`; maßgeblich ist der Median der Phasen.
   - **Speicher:** `c_kwh_per_k = τ · u / 1000`. Nach Entscheidung 10: Ereignis-Energie = Σ(`p_w` − `P₀`) · 0,25 h über die Slots ab dem Sprung, bis Ist und Soll höchstens 0,3 K auseinander liegen (höchstens 3 h), geteilt durch den Betrag des Versatzes.
   - **Höchstleistung:** `max_w` = 95. Perzentil von `p_w` über laufende Slots dieser Betriebsart.
   - **Profil** (Entscheidung 13): Gruppe über `weekday_of` und die Gruppen aus `driving_profile` (Feiertag wie Sonntag), Viertelstunde der lokalen Uhr (an Umstellungstagen nach lokaler Uhrzeit, wie 5b).
6. **Zweiter Schritt** (`app/planning/thermal_shift.py`), Einheiten W, kWh, ct; Slotlänge `Δ = 0,25 h`, `a = exp(−Δ/τ)`, `C = c_kwh_per_k`:
   - Variablen je Slot: `up_x, up_g, dn_g, dn_b, dn_x ≥ 0`, `S` (kWh, Speicherstand am Slotende).
   - `S_t = a · S_{t−1} + (up_x + up_g − dn_g − dn_b − dn_x) · Δ / 1000`, `S_{−1} = start_k · C` (Ist minus Soll ohne Versatz jetzt, ins Band geklemmt; beim Kühlen mit umgekehrtem Vorzeichen).
   - `−down_k · C ≤ S_t ≤ up_k · C`; `S_t − S_{t−1} ≤ 0,5 · C` (eine Stufe je Viertelstunde nach oben, Schrittregel); `S_end ≥ 0`.
   - `up_x ≤ export_w`; `up_g ≤ 0`, wenn `discharge_w > 50`, sonst `up_g ≤ max(0, budget_w − import_w)` (ohne Budget unbegrenzt); `up_x + up_g ≤ max_w − base_w`; `dn_g ≤ import_w`, `dn_b ≤ discharge_w`; `dn_g + dn_b + dn_x ≤ base_w`; in Slots mit Laufanteil unter 0,8 alle fünf 0.
   - Ziel: min Σ Δ/1000 · (`feed_in · up_x + (import + margin) · up_g − import · dn_g − end_value · dn_b − feed_in · dn_x`), `feed_in = 0` ohne Wert.
   - Ergebnis je Slot: `thermal_w = up − dn`, `grid_w = up_g`, `steps = round(S_t / C / 0,5)` in `[−down_k/0,5, up_k/0,5]`. Spart der Horizont weniger als `MIN_SAVING_CT = 2.0`, ist das Ergebnis „lohnt nicht“ (alle 0). Zeitlimit 5 s; ohne Lösung keine Verschiebung.
7. **Plan** (`shared/plan.py`):
   - `PlanSlot.thermal_w: Finite = 0.0`, `PlanSlot.thermal_steps: int | None = None`.
   - `Plan.thermal: PlanThermal | None = None` mit `device`, `mode`, `active` (geht an den core), `saving_ct`, `model_computed_at`, `reason` (`ok`, `off`, `no_model`, `mode`, `not_worth`, `solver`).
   - `ReasonCode` neu `KLIMA_MEHR` und `KLIMA_WENIGER` (Parameter `mode`, `k`); `PlanTrigger` neu `thermal`.
   - `IntentPlanSlot.thermal_steps: int | None = None` (−6 bis 6), `IntentPlanSlot.thermal_grid_w: NonNegativeW = 0.0`; `intent_plan` setzt sie nur bei `plan.thermal.active`. Alte Absichten laden mit `None`/0.
   - Die Werte `import_w`, `export_w`, `cost_ct` der Slots bleiben die des ersten Schritts; die Ersparnis steht nur in `Plan.thermal.saving_ct`.
8. **Auslöser** (`app/planning/triggers.py`): Betriebsart aus Health wechselt zwischen `heizen`, `kuehlen` und dem Rest, oder ein neues Modell: neu planen mit `thermal` (höchstens alle 5 min).
9. **Endpunkte:** `GET /api/learning/thermal` (ab „Lesen“): je Betriebsart `ModeModel` als JSON, `usable`, `reason`, `text`, `computed_at`, dazu `enabled` und `status`; `POST /api/learning/thermal/recompute` (Admin, 202, 409 während eines Laufs). Einstellungen über den vorhandenen Einstellungs-Endpunkt (`learning.thermal`). `GET /api/plan` liefert die neuen Felder.
10. **HA** (`shared/config.py`, `app/ha/entities.py`): `HA_THERMAL_SUFFIXES` um `planned_offset` (Sensor „Geplanter Versatz“, K, Versatz des laufenden Slots nach `offset_for`, sonst 0); globaler Schalter `thermal_plan` „Vorheizen und Vorkühlen“ (setzt `learning.thermal.enabled`, Änderungsprotokoll wie in 5b).

## Global Constraints

- Deutsch in Texten, Logs (ganze Sätze, `tests/unit/test_layout.py`), Commits im Stil `feat(app): …`; keine Modellbezeichnungen im Repo.
- `core` und `app` importieren nur aus `shared`; die Importverträge bleiben unverändert.
- Im core ändern sich nur `core/control/thermal.py` (Tasks 3, 8) und `core/control/regulation.py` (Task 8): `git diff main --stat -- src/bindaems/core cerbo-watchdog` nennt am Ende nur diese zwei Dateien. Die Safety-Schicht (`core/safety`) bleibt unverändert.
- **Rückwärts lesbar:** Absichten ohne `thermal_steps`, Pläne ohne `thermal`, Einstellungen ohne `learning.thermal` laden unverändert. Mit Schalter aus oder ohne taugliches Modell sind Absicht und core-Verhalten genau die aus 6a: die Simulator-Szenarien (auch die sechs `klima-*`) und die Planungsszenarien bleiben grün, ohne gelockerte Prüfungen.
- Scheitert ein Simulator-Szenario, liegt der Fehler zuerst in der Regelung (eigener `fix(core)`-Commit mit Test); das Szenario nur ändern, wenn es physikalisch nicht zu schaffen ist.
- Abdeckung ≥ 75 % gesamt, ≥ 90 % für `core/safety`, `core/control`, `core/actuators`; `app/learning` und `app/planning/thermal_shift.py` sollen ≥ 90 % erreichen.
- Prüfkette vor jedem Commit: `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src cerbo-watchdog/bindaems_watchdog`, `uv run lint-imports`, `uv run pytest -q --cov --cov-fail-under=75` plus der 90-%-Bericht; UI: `pnpm lint`, `pnpm check`, `pnpm test`; E2E bei UI-Änderungen (lokal mit `launchOptions.executablePath: /opt/pw-browsers/chromium`). In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`. Vor einem Fast-Forward auf `main` auch die GitHub-CI des Branch-Kopfs prüfen.
- Tests schreiben nie nach `/data` (`core.data_dir` und `app.data_dir` unter `tmp_path`); InfluxDB nur über einen Fake-Reader.
- **Zeit:** Gruppen und Viertelstunden lokal (Wiener Zeit), gespeichert wird UTC; Feiertage über `app/daytype.is_holiday`.
- **Unbekannt bleibt unbekannt:** Ohne Daten kein Modell, ohne Modell keine Verschiebung, ohne bekannte Betriebsart kein Planwert; nie ein Ersatzwert.
- Gelernte Werte erreichen die Safety-Schicht nie und ändern keine harte Grenze (Spec 10.5); das Komfortband liegt immer innerhalb von `max_offset_k`.
- **Dokumentation unter Testzwang:** neue Endpunkte, HA-Objekte, Einstellungen, Grundcodes, Gründe und Komponenten stehen in `docs/betrieb.md` und `tests/unit/test_docs.py` und ändern sich in derselben Task.
- **UI:** wie in 4a (44 × 44 px, keine waagrechte Scrollleiste bei 360 px, valibot-Schema je Antwort, Vertragsdateien, Texte über `format.ts`). Übersicht und `charts/options.ts` bleiben unberührt.
- **Öffentliches Repository:** keine Daten der Anlage; Werte in Tests und Demo sind erfunden.
- Kein Aktor ist live; nichts an der Anlage schalten.

## Review Focus

1. **Betriebsart wechselt oder ist unbekannt, mitten im Plan** (UVR schaltet auf Lüften oder Aus, Heizen ↔ Kühlen in der Übergangszeit).
   - Erwartet: Der core geht im selben Zyklus auf 0 (6a); die app plant neu und sendet ohne `heizen`/`kuehlen` keinen Planwert; ein Plan für Heizen wirkt nie beim Kühlen.
   - Tests: Task 8 `test_plan_steps_ignored_when_mode_changes`; Task 7 `test_no_thermal_steps_without_known_mode`, `test_mode_change_triggers_replan`.
2. **Lücken in den Daten** (InfluxDB nachts weg, C.M.I. schweigt, Wetterarchiv ohne Wert, ein Bit hat im Fenster noch keinen Punkt).
   - Erwartet: Slots ohne Ist oder Außentemperatur fehlen; ohne Betriebsart zählt der Slot nicht; ein Lauf ohne Daten behält den letzten Stand; der Status nennt den fehlenden Grund.
   - Tests: Task 3 `test_slot_without_outdoor_temp_is_missing`, `test_mixed_mode_slot_has_no_mode`; Task 4 `test_days_without_mode_do_not_count`; Task 5 `test_failed_run_keeps_last_model`.
3. **Der Akku deckt, was der Plan dem Netz zugedacht hat** (Victron im Dry-Run, Prognose daneben).
   - Erwartet: Der Plan nimmt kein Netz in Slots mit Entladung; im core geht ein positiver Planwert eine Stufe zurück, wenn der Akku 300 s lang mindestens 300 W entlädt, und steigt erst wieder, wenn er nicht mehr entlädt.
   - Tests: Task 6 `test_no_grid_up_while_battery_discharges`; Task 8 `test_battery_guard_steps_back_planned_heat`; Simulator `klima-plan-akku`.
4. **Zeitumstellung und lokale Viertelstunden** für Profil und Auskühl-Phasen.
   - Erwartet: Am 25-h- und 23-h-Tag gehören die Werte zur lokalen Uhrzeit; eine Auskühl-Phase über die Umstellung rechnet mit echter Dauer in UTC.
   - Tests: Task 4 `test_profile_on_dst_days_maps_local_quarters`, `test_episode_across_dst_uses_utc_hours`.
5. **Ende des Plans und Rückwirkung** (Plan läuft ab, Horizont endet, Lastmodell lernt die Verschiebung mit).
   - Erwartet: `S_end ≥ 0`, kein Vorheizen ohne folgendes Absenken im Horizont, das sich nicht lohnt; ohne gültigen Slot verhält sich der core wie 6a; die Tagesenergie der Klimaanlage mit Verschiebung weicht in der Vorschau höchstens um die Verluste ab.
   - Tests: Task 6 `test_storage_ends_non_negative`, `test_shift_energy_balance_within_losses`; Task 8 `test_without_plan_slot_strategy_is_6a`.

## Texte

- **Karte „Wärmemodell“** (Prognosen), je Betriebsart: Kopf „Heizen“ / „Kühlen“ · „{n} Tage“; Zeilen „Grundleistung“ „{u} W je K Temperaturdifferenz“, „Zeitkonstante“ „{τ} h“, „Speicher“ „{c} kWh Strom je K“ (mit Ereignissen: „{c} kWh je K (aus {k} Versatz-Ereignissen, Modell {c₀})“), „Auskühl-Phasen“ „{n}“, „Anpassung“ „R² {r}“; Zustand „Modell taugt.“ oder der Grund:
  - `few_days`: „Noch zu wenig Tage mit {Betriebsart}: {n} von 14.“
  - `few_episodes`: „Noch zu wenig Auskühl-Phasen: {n} von 3.“
  - `poor_fit`: „Die Grundleistung passt noch nicht (R² {r}, nötig 0,5).“
  - `implausible_tau`: „Die Zeitkonstante {τ} h ist unplausibel (4 bis 200 h).“
  - `no_data`: „Noch keine Daten der Klimaanlage.“
  Fuß „Berechnet am {TT.MM. HH:MM}.“, Taste „Jetzt neu berechnen“ („Berechnung läuft …“).
- **Zeitplan:** Spalte „Klima“ mit „+1,0 K“ / „−0,5 K“ / „–“; Zeile über der Tabelle: aktiv „Vorheizen/Vorkühlen aktiv: spart etwa {x} ct.“, Vorschau „Vorschau: Vorheizen/Vorkühlen würde etwa {x} ct sparen.“ (nur mit Schalter aus), sonst nach `reason`: `off` „Vorheizen und Vorkühlen ist aus.“, `no_model` „Kein taugliches Wärmemodell für {Betriebsart}.“, `mode` „Die Klimaanlage heizt oder kühlt gerade nicht.“, `not_worth` „Verschieben lohnt sich in diesem Plan nicht.“, `solver` „Die Verschiebung ließ sich nicht berechnen.“
- **Grundcodes:** `KLIMA_MEHR` „Vorheizen um {k} K, weil Strom jetzt günstig ist oder PV übrig bleibt.“ (beim Kühlen „Vorkühlen um {k} K, …“); `KLIMA_WENIGER` „Heizung {k} K tiefer, weil Strom jetzt teuer ist.“ (beim Kühlen „Kühlung {k} K schwächer, …“).
- **Grund im Entscheidungslog** `thermal_plan`: „Klimaanlage folgt dem Plan ({k} K).“
- **Karte „Klimaanlage“** (System): Zeile „Plan“ „{k} K bis {HH:MM}“ oder „kein Planwert“.
- **Einstellungen, Abschnitt „Vorheizen und Vorkühlen“:** Schalter „Vorheizen und Vorkühlen“ mit Hilfe „Die Klimaanlage nutzt das Haus als Wärmespeicher. Aus: Der Zeitplan zeigt nur eine Vorschau.“; „Heizen: vorheizen bis (K)“, „Heizen: absenken bis (K)“, „Kühlen: vorkühlen bis (K)“, „Kühlen: wärmer werden lassen bis (K)“, „Mindestvorteil bei Netzbezug (ct/kWh)“; Fehler wie in Präzisierung 2.
- **HA:** `switch` „Vorheizen und Vorkühlen“, `sensor` „Geplanter Versatz“.
- **Komponente `thermal_model`:** „Wärmemodell aktuell“, „Noch keine Daten der Klimaanlage“, „Wärmemodell fehlgeschlagen: {Fehler}“.

---

### Task 1: entfällt (Ruling vom 27.10.2026)

Bei der Umsetzung zeigte sich: Die Betriebsbits der Klimaanlage (`running`, `heating`, `cooling`, `venting`, `cmd_on`) sind im CoE-Adapter Zustände (`SignalKind.STATE`) und gehen schon seit 6a bei jeder Änderung nach InfluxDB, Messung `status`, Tag `id` = `thermal.<name>.<bit>`, Feld `value_bool` (`core/telemetry/sampler.py`, `_status`). Health nennt `mode`, `running`, `actual_c` und `base_setpoint_c` schon (`core/runtime.py`, `_thermal_json`). An der Anlage wird die Betriebsart also seit dem 26.10.2026 aufgezeichnet; der core bleibt bis Task 8 unverändert. Task 3 liest die Bits aus `status`.

### Task 2: Migration 0015 und Einstellungen

**Files:**
- Create: `src/bindaems/app/db/migrations/versions/v0015_thermal_model.py`
- Modify: `src/bindaems/app/db/schema.py`, `src/bindaems/shared/settings.py`
- Test: `tests/unit/app/test_db.py`, `tests/unit/shared/test_settings.py`

**Interfaces:**
- Produces: Tabelle `thermal_model` (Präzisierung 3); `ThermalPlanSettings`, `LearningSettings.thermal` (Präzisierung 2).

- [ ] **Step 1: Write the failing tests**

```python
def test_migration_0015_creates_thermal_model(engine) -> None: ...
def test_migration_0015_keeps_driving_tables(engine) -> None: ...
def test_thermal_plan_defaults() -> None:
    t = RuntimeSettings().learning.thermal
    assert (t.enabled, t.heat_up_k, t.heat_down_k, t.cool_up_k, t.cool_down_k, t.margin_ct) == (
        False, 1.5, 1.0, 1.5, 1.0, 2.0)
def test_band_not_multiple_of_half_is_rejected() -> None: ...  # „Das Komfortband geht in Schritten von 0,5 K.“
def test_band_above_limits_is_rejected() -> None: ...  # up 3.5, down 2.5
def test_old_learning_settings_without_thermal_load() -> None: ...
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
git commit -m "feat(app): Tabelle und Einstellungen für Vorheizen und Vorkühlen"
```

### Task 3: Daten der Klimaanlage lesen

**Files:**
- Create: `src/bindaems/app/learning/thermal_data.py`
- Test: `tests/unit/app/learning/test_thermal_data.py`

**Interfaces:**
- Consumes: `InfluxReader.query` (`app/history/influx.py`), `WeatherArchive` (`app/learning/weather.py`).
- Modify: `src/bindaems/shared/plan.py` (`ThermalMode` zieht neben `ThermalHint`), `src/bindaems/core/control/thermal.py` (importiert `ThermalMode` von dort, ohne Verhaltensänderung); die app darf den core nicht importieren.
- Produces:
  - `ThermalSlot(start: datetime, actual_c: float, setpoint_c: float | None, offset_k: float | None, power_w: float | None, running: bool | None, mode: Literal["heizen", "kuehlen", "lueften", "aus"] | None, outdoor_c: float, pv_w: float | None)`
  - `async def read_thermal_slots(reader: InfluxReader, weather: WeatherArchive, device: str, start: datetime, end: datetime) -> list[ThermalSlot]`
  - `class ThermalDataError(Exception)`

- [ ] **Step 1: Write the failing tests**

```python
async def test_slots_are_quarter_hour_means() -> None: ...
async def test_running_needs_80_percent() -> None: ...  # 0.85 → True, 0.15 → False, 0.5 → None
async def test_flags_carry_last_status_value_into_window() -> None: ...  # `status` vor dem Fenster wahr, keine Änderung → Anteil 1.0
async def test_flag_before_first_point_is_unknown() -> None: ...
async def test_mixed_mode_slot_has_no_mode() -> None: ...
async def test_slot_without_outdoor_temp_is_missing() -> None: ...
async def test_pv_sums_all_planes() -> None: ...
async def test_read_error_raises_thermal_data_error() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_thermal_data.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Präzisierung 4: für die Zahlen eine InfluxQL-Abfrage je Messung mit `GROUP BY time(15m)`, Mittel; die Bits aus `status` als Stufenfunktion (letzter Wert vorher plus Änderungen), Zeitanteil je Viertelstunde.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning/test_thermal_data.py -q && uv run mypy src && uv run lint-imports`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(app): Daten der Klimaanlage je Viertelstunde lesen"
```

### Task 4: Wärmemodell

**Files:**
- Create: `src/bindaems/app/learning/thermal_model.py`
- Test: `tests/unit/app/learning/test_thermal_model.py`

**Interfaces:**
- Consumes: `ThermalSlot` (Task 3); `weekday_of`, Gruppen aus `app/learning/driving_profile.py`.
- Produces:
  - `Episode(start: datetime, hours: float, tau_h: float)`, `OffsetEvent(start: datetime, offset_k: float, kwh_per_k: float)`
  - `ModeModel(mode, days: int, u_w_per_k: float | None, pv_w_per_kw: float | None, base_w: float | None, r2: float | None, tau_h: float | None, episodes: int, c_kwh_per_k: float | None, c_model_kwh_per_k: float | None, events: int, event_error_pct: float | None, max_w: float | None, usable: bool, reason: Literal["ok", "no_data", "few_days", "few_episodes", "poor_fit", "implausible_tau"])`
  - `ModeProfile(setpoint_c: dict[tuple[int, int], float], on_share: dict[tuple[int, int], float])` (Schlüssel: Gruppe, lokale Viertelstunde 0 bis 95)
  - `def fit_mode(slots: Sequence[ThermalSlot], mode: Literal["heizen", "kuehlen"]) -> ModeModel`
  - `def mode_profile(slots: Sequence[ThermalSlot], mode: Literal["heizen", "kuehlen"], *, today: date) -> ModeProfile` (14 Tage)
  - `def base_power_w(model: ModeModel, setpoint_c: float, outdoor_c: float, pv_w: float) -> float`

- [ ] **Step 1: Write the failing tests** mit erfundenen Slots aus einem kleinen RC-Modell (`τ = 40 h`, `u = 120 W/K`):

```python
def test_fit_recovers_u_and_tau_from_synthetic_house() -> None:
    m = fit_mode(synthetic(days=21, tau_h=40, u=120), "heizen")
    assert m.usable and m.reason == "ok"
    assert m.tau_h == pytest.approx(40, rel=0.15) and m.u_w_per_k == pytest.approx(120, rel=0.1)
    assert m.c_kwh_per_k == pytest.approx(40 * 120 / 1000, rel=0.2)
def test_few_days_is_not_usable() -> None: ...  # 13 Tage → few_days
def test_days_without_mode_do_not_count() -> None: ...
def test_few_episodes_is_not_usable() -> None: ...
def test_poor_fit_is_not_usable() -> None: ...  # Rauschen → R² < 0,5
def test_implausible_tau_is_not_usable() -> None: ...  # τ 2 h
def test_cooling_uses_outdoor_minus_setpoint() -> None: ...
def test_events_replace_capacity_within_bounds() -> None: ...  # 3 Ereignisse mit 2× → 1,5×
def test_two_events_do_not_replace_capacity() -> None: ...
def test_profile_on_dst_days_maps_local_quarters() -> None: ...
def test_episode_across_dst_uses_utc_hours() -> None: ...
def test_holiday_counts_as_sunday_group() -> None: ...
def test_base_power_is_never_negative() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_thermal_model.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** nach Entscheidungen 3, 4, 10, 13 und Präzisierung 5 (numpy, rein, ohne Uhr). Die Schwellen als `Final`-Konstanten am Kopf des Moduls.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning/test_thermal_model.py -q && uv run mypy src`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(app): Wärmemodell der Klimaanlage je Betriebsart"
```

### Task 5: Dienst, Ablage und Status

**Files:**
- Create: `src/bindaems/app/learning/thermal_service.py`
- Modify: `src/bindaems/app/runtime.py` (Dienst starten und schließen), `src/bindaems/app/status.py` (Komponente `thermal_model`)
- Test: `tests/unit/app/learning/test_thermal_service.py`

**Interfaces:**
- Consumes: Tasks 2 bis 4; das erste Gerät der Art `coe_offset` aus `thermal.devices` (ohne Gerät läuft der Dienst nicht und die Komponente fehlt).
- Produces:
  - `RUN_AT: Final = time(3, 50)`, `KEEP_DAYS: Final = 70`
  - `class ThermalModelService` mit `async run()`, `start_run() -> bool`, `running: bool`, `current(mode) -> tuple[ModeModel, ModeProfile, datetime] | None` (letzter Stand, auch untauglich), `status() -> dict[str, Any]` (Präzisierung 9), `component_status() -> ComponentStatus`, `on_change(callback: Callable[[], None]) -> None`

- [ ] **Step 1: Write the failing tests**

```python
async def test_run_stores_one_row_per_mode() -> None: ...
async def test_failed_run_keeps_last_model() -> None: ...
async def test_rows_older_than_70_days_are_pruned() -> None: ...
async def test_new_model_calls_on_change() -> None: ...
async def test_start_run_refuses_while_running() -> None: ...
async def test_without_coe_device_service_is_absent() -> None: ...
def test_component_texts() -> None: ...  # „Wärmemodell aktuell“ usw.
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_thermal_service.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** wie `driving_service.py` (Zeitplan, Lauf in `asyncio.to_thread` für die Anpassung, Ablage, Laden des letzten Stands beim Start). Fenster 28 Tage bis jetzt.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app -q && uv run mypy src`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(app): Wärmemodell jede Nacht berechnen und ablegen"
```

### Task 6: Zweiter Planungsschritt

**Files:**
- Create: `src/bindaems/app/planning/thermal_shift.py`
- Test: `tests/unit/app/planning/test_thermal_shift.py`

**Interfaces:**
- Produces:
  - `ShiftSlot(start: datetime, base_w: float, on_share: float, export_w: float, import_w: float, discharge_w: float, import_ct: float, feed_in_ct: float | None, budget_w: float | None)`
  - `ShiftInput(mode: Literal["heizen", "kuehlen"], c_kwh_per_k: float, tau_h: float, max_w: float, up_k: float, down_k: float, margin_ct: float, end_value_ct: float, start_k: float, slots: Sequence[ShiftSlot])`
  - `ShiftedSlot(start: datetime, thermal_w: float, grid_w: float, steps: int)`
  - `ShiftResult(slots: list[ShiftedSlot], saving_ct: float, reason: Literal["ok", "not_worth", "solver"])`
  - `def shift(inp: ShiftInput, time_limit_s: float = 5.0) -> ShiftResult`; `MIN_SAVING_CT: Final = 2.0`

- [ ] **Step 1: Write the failing tests**

```python
def test_preheats_before_expensive_morning() -> None:
    r = shift(night_cheap_morning_expensive())
    assert r.reason == "ok" and r.saving_ct > 2
    assert max(s.steps for s in r.slots[:16]) > 0 and min(s.steps for s in r.slots[24:32]) < 0
def test_uses_planned_export_before_grid() -> None: ...
def test_no_grid_up_while_battery_discharges() -> None: ...  # discharge_w 800 → grid_w 0
def test_respects_budget() -> None: ...  # import_w + grid_w ≤ budget_w
def test_steps_stay_in_band() -> None: ...  # up_k 1.0, down_k 0.5 → steps in [−1, 2]
def test_at_most_one_step_up_per_slot() -> None: ...
def test_storage_ends_non_negative() -> None: ...
def test_flat_price_is_not_worth() -> None: ...  # reason not_worth, alle 0
def test_off_quarters_are_not_shifted() -> None: ...  # on_share 0.5
def test_shift_energy_balance_within_losses() -> None: ...  # Σ thermal_w·Δ ≥ 0 und ≤ Verluste aus τ
def test_cooling_mirrors_heating() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/planning/test_thermal_shift.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** das LP nach Präzisierung 6 mit PuLP und HiGHS wie `optimizer.py`; ohne Lösung oder bei Zeitlimit `reason="solver"` und alle Slots 0.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/planning/test_thermal_shift.py -q && uv run mypy src`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(app): Vorheizen und Vorkühlen als zweiter Planungsschritt"
```

### Task 7: Verschiebung im Plan, in der Absicht und in den Erklärungen

**Files:**
- Modify: `src/bindaems/shared/plan.py`, `src/bindaems/app/planning/service.py`, `src/bindaems/app/planning/explain.py`, `src/bindaems/app/planning/plausibility.py`, `src/bindaems/app/planning/triggers.py`, `src/bindaems/app/runtime.py` (Verdrahtung)
- Test: `tests/unit/shared/test_plan.py`, `tests/unit/app/planning/test_service.py`, `test_explain.py`, `test_plausibility.py`, `test_triggers.py`, `test_intents.py`

**Interfaces:**
- Consumes: `ThermalModelService.current` (Task 5), `shift` (Task 6), Health `thermal.<name>.mode`, `actual_c`, `base_setpoint_c` (vorhanden, `core/runtime.py`), `PlanInputs.end_value_ct`.
- Produces: `PlanSlot.thermal_w`, `PlanSlot.thermal_steps`, `Plan.thermal: PlanThermal | None`, `IntentPlanSlot.thermal_steps`, `IntentPlanSlot.thermal_grid_w`, Grundcodes `KLIMA_MEHR`/`KLIMA_WENIGER`, Auslöser `thermal` (Präzisierungen 7 und 8).

- [ ] **Step 1: Write the failing tests**

```python
def test_plan_without_thermal_is_unchanged() -> None: ...  # ohne Gerät/Modell: Plan gleich dem heutigen, thermal None oder reason
def test_shadow_shift_is_not_sent() -> None: ...  # enabled False → Plan.thermal.active False, intent ohne thermal_steps
def test_active_shift_goes_into_intent() -> None: ...  # thermal_steps und thermal_grid_w je Slot
def test_no_thermal_steps_without_known_mode() -> None: ...  # mode lueften/unbekannt → reason mode
def test_model_for_other_mode_is_not_used() -> None: ...
def test_mode_change_triggers_replan() -> None: ...
def test_new_model_triggers_replan() -> None: ...
def test_explain_names_preheat_and_setback() -> None: ...
def test_plausibility_flags_steps_outside_band() -> None: ...
def test_old_intent_without_thermal_steps_loads() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/shared/test_plan.py tests/unit/app/planning -q`
Expected: FAIL

- [ ] **Step 3: Implement**: In `plan_once` (oder direkt danach im Dienst) die `ShiftSlot` aus den Slots des Plans bauen (`base_w` aus `base_power_w` mit Profil-Sollwert, Temperatur der PV-Prognose, PV der Slots, mal `on_share`), `shift` aufrufen, Ergebnis in `PlanSlot`/`Plan.thermal`; `intent_plan` setzt die Felder nur bei `active`. `start_k` aus Health (`actual_c − base_setpoint_c`) – ohne Wert 0.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/unit/shared tests/unit/app/planning tests/integration/test_planning_scenarios.py && uv run mypy src`
Expected: PASS, die Planungsszenarien unverändert grün

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(app): Vorheizen und Vorkühlen in Plan, Absicht und Erklärung"
```

### Task 8: Der core folgt dem Planwert

**Files:**
- Modify: `src/bindaems/core/control/thermal.py`, `src/bindaems/core/control/regulation.py`, `src/bindaems/tools/sim/scenarios.py` (drei neue Szenarien, Kopfkommentar wie bei den `klima-*`)
- Test: `tests/unit/core/control/test_thermal.py`, `tests/unit/core/control/test_regulation.py`, `tests/integration/test_runtime_thermal.py`, `tests/integration/test_sim_scenarios.py`, `tests/unit/tools/sim/test_sim_thermal.py`

**Interfaces:**
- Consumes: `IntentPlanSlot.thermal_steps`, `IntentPlanSlot.thermal_grid_w` (Task 7).
- Produces: `ThermalObs.planned_steps: int | None = None`, `ThermalObs.planned_grid_w: float = 0.0`, `ThermalObs.battery_discharge_w: float | None = None`; Grund `thermal_plan`; Health `thermal.<name>.planned` (Stufen oder `None`).

- [ ] **Step 1: Write the failing tests**

```python
def test_without_plan_slot_strategy_is_6a() -> None: ...  # planned_steps None: dieselben Wünsche wie heute
def test_follows_plan_one_step_per_interval() -> None:
    # Planwert +3: +1 sofort, +2 nach 600 s, +3 nach 1200 s, Grund thermal_plan
def test_plan_down_is_immediate() -> None: ...  # +2 → Planwert −1 im selben Zyklus
def test_plan_negative_replaces_hint_less() -> None: ...
def test_surplus_may_exceed_plan() -> None: ...  # Einspeisung 800 W seit 10 min → Planwert + 1
def test_import_threshold_adds_planned_grid() -> None: ...  # 300 W + 1500 W geplant: kein Rückschritt bei 1200 W
def test_battery_guard_steps_back_planned_heat() -> None: ...  # Entladung 400 W seit 300 s → eine Stufe zurück
def test_peak_pressure_zeroes_planned_positive() -> None: ...
def test_cars_priority_beats_plan() -> None: ...
def test_plan_steps_ignored_when_mode_changes() -> None: ...
def test_health_names_planned_steps() -> None: ...
```

Simulator: `klima-plan-vorheizen` (billige Nacht, teurer Morgen: Wunsch steigt planmäßig, geht im teuren Block auf −2, nie über die Schrittregel), `klima-plan-akku` (Planwert +2 aus dem Netz, Akku deckt: Rückschritt binnen 6 min), `klima-plan-ende` (Plan läuft ab: Verhalten wie 6a).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/core/control -q -k thermal`
Expected: FAIL

- [ ] **Step 3: Implement** nach Entscheidung 9: Regel „Plan“ nach Regel 4 (Akku), Akku-Wächter als Teil von Regel 4 bei `planned_steps > 0` (statt der SOC-Schwelle, die für geplante Stufen entfällt), Regel 6 mit erhöhter Schwelle, Regeln 7 und „weniger endet“ nur ohne Planwert. Die Regelung gibt `planned_steps`, `planned_grid_w` und die Entladeleistung des Akkus (aus dem Zyklus) in `ThermalObs`. Die Safety-Schicht bleibt unverändert.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/unit/core tests/unit/tools/sim tests/integration/test_sim_scenarios.py tests/integration/test_runtime_thermal.py && uv run pytest -q --cov --cov-fail-under=75`
Expected: PASS, die 90-%-Grenze für `core/control` hält, alle bisherigen Szenarien grün

- [ ] **Step 5: Commit**

```bash
git add src tests
git commit -m "feat(core): Klimaanlage folgt dem Planwert für Vorheizen und Vorkühlen"
```

### Task 9: API, Demo-Welt und Vertragsdateien

**Files:**
- Modify: `src/bindaems/app/learning/routes.py`, die Demo-Welt (`app/…/demo*`, wie in 5b), Vertragsdateien unter `ui/src/lib/api/` (Beispielantworten), `docs/betrieb.md`
- Test: `tests/unit/app/learning/test_routes.py`, `tests/unit/test_docs.py`, `ui/src/lib/api/contract.test.ts`

**Interfaces:**
- Produces: `GET /api/learning/thermal`, `POST /api/learning/thermal/recompute`; neue Felder in `GET /api/plan` (Präzisierung 9); Demo mit einem tauglichen Heizmodell, einem untauglichen Kühlmodell und einem Plan mit Vorschau.

- [ ] **Step 1: Write the failing tests**

```python
async def test_thermal_status_for_readers() -> None: ...
async def test_recompute_needs_admin() -> None: ...  # 403, Admin 202, während Lauf 409
async def test_thermal_status_without_device_is_404() -> None: ...
async def test_plan_has_thermal_fields() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_routes.py tests/unit/test_docs.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** Endpunkte, Demo-Welt, Vertragsdateien und Betriebshandbuch (Endpunkte, Einstellungen, Komponente, Grundcodes, Grund `thermal_plan`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/unit/app tests/unit/test_docs.py && cd ui && pnpm test -- contract`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests ui docs
git commit -m "feat(app): Endpunkte und Demo für das Wärmemodell"
```

### Task 10: Home Assistant

**Files:**
- Modify: `src/bindaems/shared/config.py` (`HA_THERMAL_SUFFIXES`), `src/bindaems/app/ha/entities.py`, `docs/betrieb.md`
- Test: `tests/unit/app/ha/test_entities.py`, `tests/unit/test_docs.py`

**Interfaces:**
- Produces: Schalter `thermal_plan`, Sensor `planned_offset` je Gerät (Präzisierung 10).

- [ ] **Step 1: Write the failing tests**

```python
def test_thermal_plan_switch_sets_setting() -> None: ...  # Änderungsprotokoll, Urheber HA
def test_planned_offset_follows_current_slot() -> None: ...  # Heizen +2 Stufen → 1.0; ohne Planwert 0
def test_planned_offset_in_cooling_is_negative() -> None: ...
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/ha -q -k thermal`
Expected: FAIL

- [ ] **Step 3: Implement** wie die Schalter und Sensoren aus 5b und 6a.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -q tests/unit/app/ha tests/unit/test_docs.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src tests docs
git commit -m "feat(app): Vorheizen und Vorkühlen in Home Assistant"
```

### Task 11: UI – Prognosen, Zeitplan, System und Einstellungen

**Files:**
- Create: `ui/src/lib/learning/thermal.ts`, `ui/src/lib/learning/ThermalModelCard.svelte` (+ Tests daneben)
- Modify: `ui/src/lib/api/schemas.ts`, `ui/src/routes/prognosen/+page.svelte`, `ui/src/routes/zeitplan/+page.svelte`, `ui/src/lib/system/thermal.ts`, `ui/src/lib/system/ThermalCard.svelte`, `ui/src/routes/einstellungen/+page.svelte`, `ui/src/lib/format.ts`
- Test: `ui/src/lib/learning/thermal.test.ts`, `ThermalModelCard.test.ts`, `ui/src/routes/prognosen/page.test.ts`, `ui/src/routes/zeitplan/page.test.ts`, `ui/src/lib/system/ThermalCard.test.ts`, `ui/src/routes/einstellungen/page.test.ts`, E2E `ui/tests/e2e/pages.spec.ts`

**Interfaces:**
- Consumes: Endpunkte aus Task 9; Texte aus „Texte“.
- Produces: keine für andere Tasks.

- [ ] **Step 1: Write the failing tests**

```ts
it('zeigt je Betriebsart Zeitkonstante und Speicher', ...)
it('nennt den Grund, wenn das Modell nicht taugt', ...)  // „Noch zu wenig Tage mit Heizen: 9 von 14.“
it('zeigt im Zeitplan die Spalte Klima und die Vorschau', ...)  // „Vorschau: … würde etwa 12 ct sparen.“
it('zeigt in der Karte Klimaanlage den Planwert', ...)
it('lehnt ein Komfortband außerhalb der Schritte ab', ...)
it('passt bei 360 px ohne waagrechte Scrollleiste', ...)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pnpm test`
Expected: FAIL

- [ ] **Step 3: Implement** Schemas (valibot), Karte, Spalte, Zeile und Abschnitt; Rechenlogik nur in den `.ts`-Modulen. Übersicht und `charts/options.ts` bleiben unberührt.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && pnpm lint && pnpm check && pnpm test && pnpm test:e2e`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui
git commit -m "feat(ui): Wärmemodell, Vorheizen und Vorkühlen anzeigen und einstellen"
```

### Task 12: Dokumentation, Abnahme und Stand der Spec

**Files:**
- Create: `docs/verification/abnahme-phase-5c.md`
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absatz „Stand Plan 5c“ in Phase 5 und Phase 6, Hinweis in 9.3), `docs/betrieb.md` (Abschnitt „Vorheizen und Vorkühlen (Plan 5c)“ mit Ausrollen: Sicherung, Migration 0015, core und app gemeinsam), `docs/uebergabe.md`
- Test: `tests/unit/test_docs.py`

- [ ] **Step 1: Write the failing test** `test_docs_name_phase_5c_endpoints_and_settings` (Endpunkte, Einstellungen, Komponente, Grundcodes, HA-Objekte stehen im Betriebshandbuch).
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_docs.py -q` – Expected: FAIL
- [ ] **Step 3: Implement** Abnahme nach Entscheidung 16 (Teil A und B mit Befehlen und Prüfpunkten), Stand der Spec, Betriebshandbuch und Übergabe.
- [ ] **Step 4: Run** die ganze Prüfkette (Global Constraints) – Expected: PASS
- [ ] **Step 5: Commit**

```bash
git add docs tests
git commit -m "docs: Vorheizen und Vorkühlen in Spec, Betrieb, Abnahme und Übergabe"
```

## Selbstprüfung (beim Schreiben erledigt)

- **Spec-Abdeckung:** „Wärmemodell des Hauses“ → Tasks 3 bis 5; „Klimaanlage als flexible Last im Planer“ → Tasks 6, 7; „Vorheizen und Vorkühlen über Stunden nach Preis“ (6a, „Sonst 0“) → Tasks 6 bis 8; Rangfolge Akku → Autos → Klima (18) → Entscheidungen 2, 8, 9; harte Grenzen und Schrittregel (8.2, 8.3) bleiben in der Safety-Schicht, der Planer hält sie zusätzlich (Präzisierung 6); Spec 10.5 → Entscheidung 17; 11.2 bleibt (Task 1 entfällt); HA und UI (12, 13) → Tasks 10, 11; Abnahme → Task 12.
- **Typen:** `ThermalSlot` (3) → `fit_mode`/`mode_profile` (4) → `ThermalModelService.current` (5) → `ShiftSlot`/`shift` (6) → `PlanThermal`, `IntentPlanSlot.thermal_steps`/`thermal_grid_w` (7) → `ThermalObs.planned_steps`/`planned_grid_w` (8). `ThermalMode` zieht in Task 3 nach `shared`.
- **Review Focus:** jede der fünf Zeilen hat Tests in der Task, die den Code besitzt.
- **Umfang:** 11 Tasks (Task 1 entfällt).

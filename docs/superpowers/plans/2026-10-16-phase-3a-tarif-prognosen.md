# Phase 3a – Tarif und Prognosen: Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die app hat alle Eingangsdaten, die der Optimierer in 3b braucht, und der Betreiber sieht und pflegt sie:
- das vollständige Tarifmodell (Energie, Leistungspreis nach ElWG mit Stufe und Mindestverrechnung, Grundgebühren, Einspeisung) mit einem Editor im UI, der auch Zeitfenster und Gültigkeit ändert
- die Monatsspitze aus der Abrechnung, als Sensor in Home Assistant
- Schätzpreise für Slots ohne bekannten Preis (Spec 9.2)
- die Lastprognose v1 als Profil aus der Historie (Spec 10.2)
- die PV-Prognose mit Kalibrierung und den Quantilen P25 und P75 (Spec 10.1)
- Kennzahlen der Prognosegüte

**Architecture:** 3a ändert nur die app, `shared/settings.py` und das UI; der core bleibt unverändert.
- **Tarif:** `shared/settings.py` bekommt vier Arten von Bestandteilen (diskriminierte Union über `kind`), `app/tariff/calc.py` rechnet Leistungspreis, Gebühren und Einspeisung. Die Monatsspitze liest `LedgerService.month_peak` aus `ledger_slot`.
- **Tagestyp:** `app/daytype.py` (Werktag, Samstag, Sonn- und Feiertag in AT) für Schätzpreise und Lastprofil.
- **Prognosen:** neues Archiv `app/forecast/archive.py` (Tabelle `forecast_record`) hält je Slot und Horizontklasse eine ausgegebene Prognose; die Ist-Werte kommen aus `ledger_slot`. Darauf bauen Kalibrierung und Quantile der PV (`app/forecast/calibration.py`), die Lastprognose (`app/forecast/load.py`) und die Kennzahlen (`app/forecast/quality.py`, Tabelle `forecast_metric`).
- **UI:** Tarif-Editor in „Einstellungen“, PV-Band und Lastprognose im Diagramm der Übersicht, Karten „Lastprognose“ und „Prognosegüte“ auf der System-Seite.

**Tech Stack:** wie Plan 2d: Python 3.12 mit uv, pydantic v2, FastAPI, SQLAlchemy Core mit Alembic, httpx; Tests mit pytest, hypothesis und respx; ruff, mypy und import-linter. UI: SvelteKit 2 mit Svelte 5, valibot, ECharts 6, Vitest mit @testing-library/svelte und Playwright. Neu: die Bibliothek `holidays` (Spec 10.2) in den Extras `app`.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, vor allem 5.5, 9.1, 9.2, 10.1, 10.2, 11.2, 11.3, 12, 13 und 18.
- Verweise wie „Spec 9.1“ beziehen sich darauf. Die Absätze „Stand Plan …“ der Spec gehen dem älteren Text davor vor. Die Präzisierungen unten gehen beiden vor.
- Vorgänger ist Plan 2d (`docs/superpowers/plans/2026-10-15-phase-2d-bedienung.md`), umgesetzt und auf `main`. Seine Global Constraints und die der Pläne davor gelten weiter.

## Einordnung: Zuschnitt der Phase 3

Phase 3 (Spec 18) wird in drei Pläne geteilt. Jeder liefert für sich getestete, lauffähige Software. Erst 3c ändert, was der core an Victron sendet.

| Plan | Inhalt | Ergebnis an der Anlage |
|---|---|---|
| **3a** (dieser Plan) | Tarifmodell vollständig mit Editor, Monatsspitze, Schätzpreise, Lastprognose v1, PV-Kalibrierung und Quantile, Prognosegüte | Tarif aus den Preisblättern eingetragen; Prognosen und ihre Güte sichtbar; die Archive füllen sich für 3b |
| **3b** „Optimierer und Zeitplan“ | Plan-Typen in `shared`, Optimierer (HiGHS über PuLP, eigener Prozess, Zeitlimit) nur für den Akku, Regel R1, Wirtschaftlichkeit mit Marge, Schalter „Netzladen erlaubt“, Planträgheit, Plausibilitätsprüfung, Ersatzplan, Erklärungen mit Grundcodes und Sätzen, Planungsablauf mit Auslösern und Speicherung (`plan`, `plan_inputs`), UI-Seite „Zeitplan“ (36 h), HA-Sensoren zum Plan und Taste „neu planen“, Szenario-Test-Umgebung mit historischen Tagen und der Vergleich mit „Victron allein“ | Pläne entstehen als Vorschau; der core bekommt sie noch nicht. Die 30 historischen Tage laufen an der Anlage durch die Szenario-Tests |
| **3c** „Planausführung“ | Plan in der Absicht, Prüfung und Gültigkeit im core; Akku-Absichten `EIGENVERBRAUCH`, `HALTEN` und `NETZLADEN(P)`; Plan-Schwelle der Unterstützung (7.6); Budget aus der akzeptierten Monatsspitze für den 15-min-Wächter (7.4); abgelaufener Plan → `EIGENVERBRAUCH`; Preis und Plan-Slot in den `inputs` der Entscheidungen; Simulator mit Wirkungsgrad und Plan-Szenarien; „Plan vs. Ist“ im Zeitplan; Schalter „Akku-Unterstützung erlaubt“ und Zahl „Unterstützungsschwelle“ in UI und HA; Mehrkostenanzeige beim Boost | 2 Wochen Dry-Run der Planausführung, danach geht Victron live (Spec 18, Abnahme Phase 3) |

Danach folgt die Abnahme der Phase 3 (Spec 18): Szenario-Tests über mindestens 30 historische Tage mit niedrigeren Kosten als „Victron allein“, ohne Verletzung von Regel R, dann 2 Wochen Dry-Run und die Live-Schaltung.

Stand an der Anlage laut Übergabe: Images vom Stand nach Plan 2b, die Pläne 2c-1 bis 2d sind noch nicht ausgerollt. Kein Aktor ist live. 3a schaltet nichts an der Anlage. Die Prognosen brauchen die Abrechnung (`ledger_slot`) als Ist-Werte; sie läuft seit Phase 1b an der Anlage.

## Vorgeschlagene Entscheidungen (zur Freigabe)

Diese Punkte entscheidet der Betreiber mit der Freigabe des Plans. Der Plan ist auf den Vorschlag geschrieben; eine andere Wahl ändert die genannten Tasks bzw. die Pläne 3b und 3c.

1. **Zuschnitt in 3a, 3b und 3c** (oben). Alternative: 3b und 3c zusammen; das ergäbe einen Plan mit über 30 Tasks, und die Pläne ließen sich nicht als Vorschau sichten, bevor der core sie ausführt.
2. **Lastprognose aus der Abrechnung** (Präzisierung 8): Die Hauslast je Slot kommt aus `ledger_slot` (Flüsse `*>house`, ohne Wallboxen und Akkuladung). Ältere Historie aus Home Assistant liest die app nur, wenn `homeassistant.load_history` eine Entität nennt. **Frage an den Betreiber:** welche HA-Entität die Hauslast in W ohne die beiden Wallboxen zeigt, oder welche Gesamtlast und welche Wallbox-Entitäten abzuziehen sind. Ohne Angabe beginnt das Profil mit den Daten von BindaEMS (seit Phase 1b) und erreicht die 8 Wochen der Spec erst nach und nach.
3. **Kein Temperaturterm** in der Lastprognose v1 (Spec 10.2 „optional“); Temperatur als Merkmal kommt mit dem Lernmodell (Phase 5).
4. **Feiertage wie Sonntage**, auch bei den Schätzpreisen (Präzisierung 5; Spec 9.2 nennt nur Werktag, Samstag, Sonntag).
5. **Leistungspreis:** Ein Bestandteil „Leistungspreis (ElWG)“ mit Stufe bei 10 kW, Mindestverrechnung 2 kW und Gültigkeit ab 1. 1. 2027, aber ohne Sätze; die trägt der Betreiber nach der SNE-T-V ein (Spec 19.3). Die Mindestverrechnung „20 % der vereinbarten Leistung“ (Spec 3.9) entfällt; es zählt ein fester Wert in kW.
6. **Für 3b und 3c vorab** (damit die Pläne darauf aufbauen können):
   - **„Victron allein“** ist der Eigenverbrauch des ESS ohne Netzladen und ohne Halten, auf denselben Tagen gerechnet. Dynamic ESS lässt sich nicht nachrechnen.
   - **Szenario-Tests** rechnen im Viertelstundenraster mit einem Akkumodell, das die Absichten so umsetzt wie der core; der Anlagensimulator im Sekundentakt bleibt für die Regelung (3c bekommt dort Szenarien zur Planausführung). Die CI rechnet mit erzeugten Tagen, denn das Repository ist öffentlich; die 30 echten Tage laufen an der Anlage mit einem Exportwerkzeug.
   - **Regel R2 kommt mit Phase 4:** In Phase 3 plant der Optimierer nur den Akku; Autos mit Abfahrt und Bedarf gibt es erst mit dem Modus „Geplant“. Sofort, Boost und Min+PV sind von R2 ohnehin ausgenommen.
   - **`HALTEN`** setzt der core über den Netz-Sollwert-Override um, den er schon schreibt (Sollwert = Last − PV, nicht unter 0, nachgeführt; dazu Design 3c). So braucht es keinen neuen Schreibweg; der flüchtige Override `ess.override.maxdischargepower` bleibt eine Alternative, die an der Anlage geprüft werden kann.

## Präzisierungen gegenüber der Spec

1. **Zuschnitt (Spec 18, Entscheidung 1).** 3a bringt aus Spec 18 „Tarif“ und „Prognosen“ sowie die Schätzpreise (9.2), die der Optimierer braucht. Nicht in 3a: alles unter „Planung“, „Netzladen“, die Szenario-Test-Umgebung, die Seite „Zeitplan“ (3b, 3c). Die Ansicht der Prognosegüte als eigene Seite folgt mit Phase 5 (Spec 18); 3a zeigt die Kennzahlen als Karte auf der System-Seite.
2. **Bestandteile des Tarifs (Spec 9.1).**
   - `kind` bestimmt Art und Richtung; ein Feld `gilt_fuer` gibt es nicht: `energy_per_kwh`, `power_per_kw_year` und `fixed_per_month` gelten für den Bezug, `feed_in_per_kwh` für die Einspeisung.
   - **`energy_per_kwh`** wie bisher (`source` `spot` oder `fixed`, `value_ct` netto, `vat`, Gültigkeit, Zeitfenster).
   - **`feed_in_per_kwh`:** `value_ct` netto (darf negativ sein, etwa ein Entgelt), `vat` (Standard falsch), Gültigkeit, Zeitfenster wie bei Energie. Kein `source`.
   - **`power_per_kw_year`:** `value_eur` (€/kW/Jahr netto bis zur Stufe), `value_above_eur` (über der Stufe; leer = wie `value_eur`), `tier_kw` (Standard 10), `minimum_kw` (Standard 2), `vat` (Standard wahr), Gültigkeit. Leistungspreise dürfen sich zeitlich nicht überschneiden.
   - **`fixed_per_month`:** `value_eur` (€/Monat netto), `vat`, Gültigkeit. Nur für Statistik und Kostenanzeige (Spec 9.1, 7).
   - Ein Wert `None` heißt „noch nicht eingetragen“ und zählt als 0, mit Warnung (wie bisher bei Energie).
   - Gespeicherte Einstellungen ohne `kind` (vor 3a) laden als `energy_per_kwh`. Bestehende Einstellungen bekommen die neuen Standardbestandteile nicht von selbst; eine Warnung nennt den fehlenden Leistungspreis, und der Editor bietet ihn als Vorlage an.
3. **Berechnung (Spec 9.1).**
   - **Bezugspreis je Slot:** wie bisher, nur aus den Energiebestandteilen.
   - **Einspeisepreis je Slot:** OeMAG-Monatswert plus die gültigen `feed_in_per_kwh` (mit USt., wo `vat`); ohne OeMAG-Wert unbekannt.
   - **Leistungspreis eines Monats:** Es gilt der Bestandteil, der am übergebenen Tag gültig ist. Verrechnet wird `max(Monatsspitze, minimum_kw)`; Kosten je Monat = `(value_eur · min(kW, tier_kw) + value_above_eur · max(0, kW − tier_kw)) / 12`, mal `1 + USt.` bei `vat`. Ohne gültigen Bestandteil gibt es keinen Leistungspreis.
   - **Gebühren eines Monats:** Summe der am Tag gültigen `fixed_per_month`, brutto.
4. **Monatsspitze (Spec 7.4, 11.3).** Die höchste Viertelstunde des Netzbezugs im lokalen Monat aus `ledger_slot` (`import_wh · 4 / 1000` kW), mit dem Slot. Die Tabelle `month_peak` aus Spec 11.3 entfällt; die Abfrage über `ledger_slot` ist schnell genug und kann nicht auseinanderlaufen. Boost zählt mit, denn der Zähler sieht ihn. In HA: Sensor „Monatsspitze“ (kW).
5. **Tagestyp.** `workday`, `saturday` oder `sunday`; Sonntage und die bundesweiten Feiertage in Österreich (`holidays.country_holidays("AT")`) sind `sunday`. Er gilt für Schätzpreise und Lastprofil (Entscheidung 4). Maßgeblich ist das lokale Datum (Europe/Vienna).
6. **Schätzpreise (Spec 9.2).**
   - Für einen Slot ohne gespeicherten Preis ist der Börsenpreis (netto) der Median der gespeicherten `spot_net_ct` derselben lokalen Uhrzeit (Stunde und Minute) und desselben Tagestyps in den 28 Tagen vor jetzt; mindestens 2 Werte. Sonst derselbe Median ohne Tagestyp, sonst kein Schätzpreis.
   - Darauf kommen die Tarifbestandteile wie bei echten Preisen (`import_price`, `feed_in_price`) mit den aktuellen Einstellungen.
   - `PriceService.planning(start, end)` liefert je Slot einen `PlanningPrice` mit Herkunft `primary`, `fallback` oder `estimated`; Slots ohne jeden Preis fehlen. Das Speichern der Planungsbasis mit Version gehört zu den Eingangsdaten des Plans (3b).
7. **Prognose-Archiv.**
   - Tabelle `forecast_record` (Migration `0008`): `kind` (`pv`, `load`), `slot_start`, `horizon` (`h0_6`, `h6_24`, `h24`), `issued_at`, `p50_w`, `raw_w` (PV: Modell ohne Kalibrierung; Last: wie `p50_w`), `clearness` (PV: `clear`, `mixed`, `overcast` oder leer); Primärschlüssel `(kind, slot_start, horizon)`.
   - **Horizont:** `lead = slot_start − issued_at`; aufgenommen werden nur Slots mit `lead ≥ 0`. `h0_6` bis unter 6 h, `h6_24` bis unter 24 h, sonst `h24`.
   - **Welche Ausgabe bleibt:** je Slot und Klasse die, deren `lead` am nächsten an der Klassenmitte liegt (`h0_6` 3 h, `h6_24` 15 h, `h24` 30 h); bei Gleichstand die neuere. So vergleichen die Kennzahlen typische Vorläufe und nicht nur die letzte Ausgabe vor dem Slot.
   - **Ist-Werte** aus `ledger_slot`, nur vollständige Slots (`covered_s ≥ 855`): PV = Summe der Flüsse `pv>*`, Hauslast = Summe der Flüsse `*>house`, je `· 4` von Wh auf W.
   - Aufbewahrung 60 Tage, aufgeräumt in der nächtlichen Prognose-Wartung.
8. **Lastprognose v1 (Spec 10.2, Entscheidungen 2 und 3).**
   - **Historie:** Hauslast je Slot aus `ledger_slot` (Ist-Werte wie in Präzisierung 7). Für Slots ohne vollständigen Abrechnungs-Slot liest die app, falls `homeassistant.load_history.entity` gesetzt ist, die HA-Datenbank: Mittel der Entität je Viertelstunde (aus Minutenmitteln mit `fill(previous)`, beide Schemata wie bei den Verbrauchern), minus die Mittel der Entitäten unter `subtract`, mindestens 0. Fehlt ein Wert einer abzuziehenden Entität, fällt der Slot weg.
   - **Profil:** je Tagestyp und lokaler Uhrzeit des Slots (an den Tagen der Zeitumstellung über die Uhrzeit, die Stunde 02:00–03:00 der doppelten Nacht teilt sich ein Profil) der gewichtete Median der Stichproben der letzten 56 Tage, Gewicht `0,5^(Alter in Tagen / 14)`. Gewichteter Median: der kleinste Wert, bei dem das kumulierte Gewicht (aufsteigend sortiert) die Hälfte des Gesamtgewichts erreicht.
   - **Rückfall:** weniger als 3 Stichproben für Tagestyp und Uhrzeit → dieselbe Uhrzeit über alle Tagestypen (mindestens 3) → gewichteter Median aller Stichproben → keine Prognose.
   - **Ausgabe:** stündlich zur Minute 9 für die Slots vom laufenden bis zum Ende des übernächsten lokalen Tages (wie die PV); das Profil wird beim Start und beim ersten Lauf eines neuen lokalen Tages neu gebaut. Jede Ausgabe geht ins Archiv und nach InfluxDB (`forecast`, `kind=load`, `quantile=p50`, Feld `kw`, RP `long`).
9. **PV: Klarheit, Kalibrierung und Quantile (Spec 10.1).**
   - **Klarheit:** Open-Meteo liefert zusätzlich `shortwave_radiation` und `diffuse_radiation` (am Standort, aus der Antwort der ersten Fläche). `k = 1 − diffus / global`; `clear` ab 0,6, `mixed` ab 0,3, sonst `overcast`; unter 50 W/m² global keine Klasse (Nacht, Dämmerung). Fehlen die Variablen in der Antwort, bleibt die Klasse leer.
   - **Kalibrierung, robust:** je lokaler Stunde und Klarheitsklasse der Median von `Ist / raw_w` über die Archiv-Einträge der Klasse `h0_6` der letzten 30 Tage mit `raw_w ≥ Schwelle` (Schwelle = `max(100 W, 2 % der Summe der kWp)`), mindestens 8 Paare; sonst die Stunde über alle Klassen (mindestens 8), sonst 1,0. Jeder Faktor wird auf 0,5–1,5 begrenzt.
   - **P50** = `raw_w · Faktor`, begrenzt auf 0 bis `pv.inverter_ac_max_w`.
   - **Quantile:** je Horizontklasse und Klarheitsklasse das 25. und 75. Perzentil (`statistics.quantiles(…, n=4, method="inclusive")`) von `Ist / p50_w` über die Archiv-Einträge der letzten 30 Tage mit `p50_w ≥ Schwelle`, mindestens 20 Paare; sonst über die Horizontklasse allein (mindestens 20), sonst 0,7 und 1,2. `P25 = P50 · q25`, `P75 = P50 · q75`, dann `P25 ≤ P50 ≤ P75` erzwungen und `P75` auf `inverter_ac_max_w` begrenzt. Für die Horizontklasse eines Slots zählt der Vorlauf ab der Ausgabe.
   - **Wann:** Kalibrierung und Quantile beim Start und nächtlich um 02:30 Uhr (lokal); sie liegen im Speicher. Jede Ausgabe der PV-Prognose geht ins Archiv (`p50_w` kalibriert, `raw_w`, `clearness`) und nach InfluxDB (`quantile` `p25`, `p50`, `p75`).
10. **Prognosegüte.**
    - Tabelle `forecast_metric` (Migration `0008`): `day` (lokales Datum), `kind`, `horizon`, `n`, `abs_err_sum_w`, `err_sum_w`, `actual_sum_w`; Primärschlüssel `(day, kind, horizon)`. Fehler = Prognose (`p50_w`) − Ist, nur Slots mit Ist-Wert; bei PV nur Slots mit `p50_w` oder Ist ≥ Schwelle (Nachtstunden verwässern sonst den Fehler).
    - Nächtlich um 02:30 Uhr für gestern und vorgestern neu gerechnet (die Abrechnung holt Lücken aus InfluxDB nach), beim Start für die letzten 3 Tage.
    - `GET /api/forecast/quality?days=14` (1 bis 60): je Art und Horizont `n`, MAE (`Σ|Fehler| / n`), Bias (`ΣFehler / n`) und relativer MAE in % (`Σ|Fehler| / ΣIst`; ohne Ist `null`).
11. **Editor für den Tarif (Spec 13, „Bewusst offen gelassen“).** Admins ändern im UI alle Felder der Bestandteile samt Zeitfenstern und Gültigkeit, fügen Bestandteile hinzu (Arten: Energie, Leistungspreis, Monatliche Gebühr, Einspeisung) und entfernen sie; der Börsenpreis bleibt und ist nicht entfernbar. Dazu USt. in % und der Fixtarif zum Vergleich. Die ID eines neuen Bestandteils entsteht aus dem Namen (Kleinbuchstaben, Ziffern, `_`, eindeutig mit `_2`, `_3` …) und bleibt danach fest. Nach einer Änderung an Energiebestandteilen oder der USt. erscheint wie bisher das Formular „Neu bewerten“.
12. **Diagramm der Übersicht und System-Seite (Spec 13).** Das Diagramm „Preise und PV“ zeigt die PV-Prognose mit dem Band P25–P75 und die Lastprognose als Linie. Die System-Seite bekommt die Karten „Lastprognose“ (Stand, Datenbasis, Energie der drei Tage) und „Prognosegüte“ (14 Tage, je Art und Horizont).

## Global Constraints

- **Gilt weiter:** alle Global Constraints aus 1a bis 2d.
- **core unverändert:** 3a ändert nichts unter `src/bindaems/core/` und nichts an der Absicht. `shared/settings.py` importiert der core nicht; das bleibt so.
- **Keine Schreibwege:** 3a schreibt auf kein Gerät und sendet keine Absicht.
- **Zeit:** Die app nimmt ihre `Clock` (`self._clock`), nie `datetime.now` direkt. Intern UTC, Tagestyp, Uhrzeiten und Monate in Europe/Vienna (`LOCAL_TZ`). Slots über `slot_start`, `local_day_slots`; Tage mit 92 und 100 Slots müssen funktionieren.
- **Datenbank:** neue Tabellen nur über die Migration `0008` (`revision = "0008"`, `down_revision = "0007"`, Datei `v0008_forecasts.py`), Zeitstempel in der Migration als `String(32)`, in `db/schema.py` als `UtcDateTime`. DB-Arbeit aus asynchronem Code über `asyncio.to_thread`.
- **Abdeckung:** ≥ 75 % insgesamt (CI). Die neuen Module unter `app/forecast/` und `app/tariff/` sind reine Rechenlogik; Ziel dort ≥ 90 %, ohne neue Schwelle in der CI.
- **UI:** Bedienelemente mindestens 44 × 44 px, keine waagrechte Scrollleiste bei 360 px, `{#each}` über Texte vom Server mit dem Index als Schlüssel, jede Antwort mit einem valibot-Schema, jede Vertragsdatei in `GET_ENDPOINTS`/`OTHER` und in `SCHEMAS` (`UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py`).
- **Texte:** exakt wie in „Texte“; Tests prüfen den Wortlaut. Zahlen im UI über `format.ts`.
- **Öffentliches Repository:** keine Daten der Anlage. Entitäten wie `sensor.hausverbrauch` sind Beispiele. Keine Tarifwerte aus Preisblättern im Code außer den Werten, die die Spec nennt (1,2 ct, SNAP 0,8, 10 kW, 2 kW, 20 %, 30 ct).
- **Abhängigkeit:** `holidays>=0.60` in `[project.optional-dependencies] app`, `uv.lock` neu (`uv lock`). In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.

## Review Focus

1. **Tage der Zeitumstellung (92 und 100 Slots):** Erwartet: Profil, Schätzpreise, Archiv und Kennzahlen ordnen jeden Slot über die lokale Uhrzeit zu, ohne Fehler und ohne doppelte Schlüssel; die doppelte Stunde im Oktober bekommt in beiden Durchläufen den Profilwert. Tests: Task 7 `test_estimate_on_dst_day_uses_local_clock`, Task 11 `test_profile_covers_100_slot_day`, Task 8 `test_record_on_dst_day_keeps_both_0200_slots`.
2. **Kalter Start ohne Historie** (neue Installation, leere Abrechnung, leeres Archiv): Erwartet: PV mit Faktor 1,0 und Band 0,7–1,2, keine Lastprognose mit der Meldung „Noch keine Daten für die Lastprognose“, Kennzahlen leer, keine Ausnahme. Tests: Task 10 `test_cold_start_uses_neutral_calibration`, Task 11 `test_no_history_gives_no_forecast_and_status`, Task 12 `test_quality_without_records_is_empty`.
3. **Lücken in der Abrechnung** (core aus, Slot mit 600 s): Erwartet: unvollständige Slots zählen weder als Ist noch als Lasthistorie, verfälschen also weder Kalibrierung noch Kennzahlen noch Profil; für die Lasthistorie springt Home Assistant ein, wenn eingestellt. Tests: Task 8 `test_incomplete_slot_has_no_actual`, Task 11 `test_incomplete_ledger_slot_falls_back_to_ha`.
4. **Einstellungen von vor 3a** (gespeicherte Version und YAML-Export ohne `kind`, ohne Leistungspreis): Erwartet: laden unverändert als Energiebestandteile, Editor und Neubewertung arbeiten, die Warnung nennt den fehlenden Leistungspreis. Tests: Task 1 `test_stored_settings_without_kind_load_as_energy`, `test_old_yaml_export_imports`, `test_missing_power_price_warns`.
5. **Leistungspreis an den Rändern:** vor dem Gültigkeitsdatum, ohne Sätze, Spitze unter der Mindestverrechnung, ohne Spitze, genau an der Stufe, zwei Leistungspreise mit überschneidender Gültigkeit. Erwartet: kein Leistungspreis vor der Gültigkeit, 0 € mit Warnung ohne Satz, Verrechnung der Mindestleistung, Überschneidung abgelehnt. Tests: Task 2 `test_power_cost_uses_minimum_without_peak`, `test_power_rate_before_validity_is_none`, `test_power_cost_at_tier_boundary`; Task 1 `test_overlapping_power_prices_are_rejected`.

## Texte

- **Warnungen der Einstellungen** (`settings_warnings`):
  - `Tarifbestandteil „{name}“ hat noch keinen Wert.` (für jede Art; bei Leistungspreis, wenn `value_eur` fehlt)
  - `Kein Leistungspreis eingetragen; nach ElWG gilt er ab 1. 1. 2027.` (kein Bestandteil `power_per_kw_year`)
  - unverändert: `Noch kein OeMAG-Monatswert eingetragen.`
- **Validierung** (pydantic, deutsch):
  - `Leistungspreise dürfen sich zeitlich nicht überschneiden`
- **Prüfung im UI** (zusätzlich zu den Sätzen des Servers): `Uhrzeit auf einer Viertelstunde, z. B. 10:15`, `Zahl, z. B. 8,11` (wie bisher), `Mindestens ein Monat`, `Mindestens ein Wochentag`, `„gültig ab“ liegt nach „gültig bis“`.
- **Arten im UI:** „Energie (ct/kWh)“, „Leistungspreis (€/kW/Jahr)“, „Monatliche Gebühr (€/Monat)“, „Einspeisung (ct/kWh)“.
- **Felder im UI:** „Name“, „Wert netto“, „bis zur Stufe (€/kW/Jahr netto)“, „über der Stufe (€/kW/Jahr netto)“, „Stufe (kW)“, „Mindestverrechnung (kW)“, „mit USt.“, „gültig ab“, „gültig bis“, „Zeitfenster“, „Monate“, „Wochentage“, „von“, „bis“, „Faktor“, „fester Wert (ct/kWh netto)“, „USt. (%)“, „Fixtarif zum Vergleich (ct/kWh brutto)“.
- **Tasten im UI:** „Bestandteil hinzufügen“, „Bestandteil entfernen“, „Zeitfenster hinzufügen“, „Zeitfenster entfernen“.
- **Hinweis im UI:** Der Börsenpreis zeigt „kommt aus der Preisquelle“ statt eines Wertfelds.
- **Komponenten-Status:** `load-forecast`: „Lastprognose von {HH:MM} Uhr“, „Noch keine Daten für die Lastprognose“, „Lastprognose fehlgeschlagen: {Fehler}“. `forecast` (PV) unverändert, mit dem Zusatz „, kalibriert aus {n} Viertelstunden“ wenn `n > 0`.
- **Karten auf der System-Seite:** „Lastprognose“ mit den Zeilen „Stand“, „Datenbasis“ („{a} Viertelstunden aus BindaEMS, {b} aus Home Assistant“), „Heute“, „Morgen“, „Übermorgen“ (kWh). „Prognosegüte“ mit Untertitel „letzte 14 Tage“, Spalten „Art“, „Vorlauf“, „Werte“, „MAE“, „Abweichung“, „MAE relativ“; Arten „PV“ und „Last“, Vorläufe „bis 6 h“, „6–24 h“, „über 24 h“; ohne Zeilen „Noch keine Kennzahlen.“
- **Diagramm:** Reihen „PV-Prognose“, „PV P25–P75“, „Lastprognose“.
- **HA:** Sensor `month_peak`, Name „Monatsspitze“, Einheit kW, Attribut `slot` (ISO-Zeit des Slots).

---

### Task 1: Tarifmodell – Arten der Bestandteile

**Files:**
- Modify: `src/bindaems/shared/settings.py`
- Modify: `src/bindaems/app/settings/service.py` (nur falls Warnungen dort gebündelt werden; sonst unverändert)
- Test: `tests/unit/shared/test_settings_tariff.py` (neu), `tests/unit/app/settings/test_service.py`

**Interfaces:**
- Produces:
  - `TariffKind = Literal["energy_per_kwh", "power_per_kw_year", "fixed_per_month", "feed_in_per_kwh"]`
  - `EnergyComponent` (bisher `PriceComponent`, `kind: Literal["energy_per_kwh"] = "energy_per_kwh"`, Felder unverändert)
  - `FeedInComponent(id, name, kind: Literal["feed_in_per_kwh"], value_ct: Ct | None, vat: bool = False, valid_from, valid_until, windows: list[TimeWindow])`
  - `PowerComponent(id, name, kind: Literal["power_per_kw_year"], value_eur: NonNegative | None, value_above_eur: NonNegative | None, tier_kw: PositiveFloat = 10.0, minimum_kw: NonNegative = 2.0, vat: bool = True, valid_from, valid_until)`
  - `MonthlyFeeComponent(id, name, kind: Literal["fixed_per_month"], value_eur: NonNegative | None, vat: bool = True, valid_from, valid_until)`
  - `PriceComponent = Annotated[EnergyComponent | FeedInComponent | PowerComponent | MonthlyFeeComponent, Discriminator(_component_kind)]`, wobei `_component_kind(value)` bei einem `dict` ohne `kind` `"energy_per_kwh"` liefert, sonst `value["kind"]` bzw. `value.kind`
  - `TariffSettings.components: list[PriceComponent]`; Standard zusätzlich `PowerComponent(id="power_price", name="Leistungspreis (ElWG)", valid_from=date(2027, 1, 1))` und `MonthlyFeeComponent(id="grid_base_fee", name="Grundgebühr Netz")`
  - `settings_warnings(settings) -> list[str]` mit den Texten aus „Texte“
  - `SNAP_WINDOW` unverändert

- [ ] **Step 1: Write the failing tests**

```python
def test_stored_settings_without_kind_load_as_energy():
    data = {"tariff": {"components": [
        {"id": "spot", "name": "Börsenpreis", "source": "spot"},
        {"id": "grid_usage", "name": "Netz", "value_ct": 8.11},
    ]}}
    tariff = RuntimeSettings.model_validate(data).tariff
    assert [type(c) for c in tariff.components] == [EnergyComponent, EnergyComponent]
    assert tariff.components[1].value_ct == 8.11

def test_default_tariff_has_power_price_and_base_fee():
    tariff = TariffSettings()
    power = next(c for c in tariff.components if c.id == "power_price")
    assert isinstance(power, PowerComponent)
    assert (power.tier_kw, power.minimum_kw, power.valid_from) == (10.0, 2.0, date(2027, 1, 1))
    assert power.value_eur is None
    assert any(isinstance(c, MonthlyFeeComponent) for c in tariff.components)

def test_overlapping_power_prices_are_rejected():
    a = {"id": "p1", "name": "A", "kind": "power_per_kw_year", "value_eur": 19,
         "valid_from": "2027-01-01", "valid_until": "2027-12-31"}
    b = {**a, "id": "p2", "valid_from": "2027-06-01", "valid_until": None}
    with pytest.raises(ValidationError, match="Leistungspreise dürfen sich zeitlich nicht überschneiden"):
        TariffSettings.model_validate({"components": [SPOT, a, b]})

def test_adjacent_power_prices_are_allowed():
    a = {"id": "p1", "name": "A", "kind": "power_per_kw_year", "valid_until": "2027-12-31"}
    b = {"id": "p2", "name": "B", "kind": "power_per_kw_year", "valid_from": "2028-01-01"}
    TariffSettings.model_validate({"components": [SPOT, a, b]})

def test_windows_only_for_energy_and_feed_in():
    fee = {"id": "f", "name": "Gebühr", "kind": "fixed_per_month",
           "windows": [{"start": "10:00", "end": "16:00", "factor": 0.8}]}
    with pytest.raises(ValidationError):  # extra="forbid": MonthlyFeeComponent hat kein Feld windows
        TariffSettings.model_validate({"components": [SPOT, fee]})

def test_feed_in_value_may_be_negative():
    item = {"id": "fee", "name": "Einspeiseentgelt", "kind": "feed_in_per_kwh", "value_ct": -0.5}
    tariff = TariffSettings.model_validate({"components": [SPOT, item]})
    assert tariff.components[1].value_ct == -0.5 and tariff.components[1].vat is False

def test_missing_power_price_warns():
    settings = RuntimeSettings(tariff=TariffSettings(components=[EnergyComponent(id="spot", name="B", source="spot")]))
    assert "Kein Leistungspreis eingetragen; nach ElWG gilt er ab 1. 1. 2027." in settings_warnings(settings)

def test_power_price_without_value_warns():
    assert "Tarifbestandteil „Leistungspreis (ElWG)“ hat noch keinen Wert." in settings_warnings(RuntimeSettings())
```

`SPOT = {"id": "spot", "name": "Börsenpreis", "source": "spot"}`. Dazu in `tests/unit/app/settings/test_service.py`:

```python
def test_old_yaml_export_imports(settings_service):
    text = OLD_EXPORT  # YAML wie ein Export vor 3a: Bestandteile ohne kind
    version = settings_service.import_yaml(text, actor="admin", source="ui")
    assert all(c.kind == "energy_per_kwh" for c in version.settings.tariff.components)
```

`OLD_EXPORT` ist ein fester YAML-Text im Test mit `spot` und `grid_usage` (mit SNAP-Fenster `start: '10:00'`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/shared/test_settings_tariff.py tests/unit/app/settings/test_service.py -q`
Expected: FAIL (ImportError `EnergyComponent`)

- [ ] **Step 3: Implement the component types and validators in `src/bindaems/shared/settings.py`**

Gemeinsame Basis `_ComponentBase(id, name, vat, valid_from, valid_until)` mit dem bisherigen Gültigkeits-Validator. Die Überschneidungsprüfung in `TariffSettings._consistent`: für alle Paare von `PowerComponent` die Intervalle `[valid_from or date.min, valid_until or date.max]` auf Schnitt prüfen. Bestehende Importe von `PriceComponent` im Code (`app/tariff/calc.py`, `tests/app_helpers.py`) auf `EnergyComponent` umstellen, wo eine Energie gemeint ist; `with_grid_usage` bleibt gültig. Die JSON-Schema-Route `GET /api/settings/schema` liefert die Union automatisch.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/shared tests/unit/app/settings tests/unit/app/tariff -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/shared/settings.py tests/unit/shared/test_settings_tariff.py tests/unit/app/settings/test_service.py tests/app_helpers.py
git commit -m "feat(tarif): Leistungspreis, Gebühren und Einspeisung als Bestandteile"
```

### Task 2: Tarifberechnung – Leistungspreis, Gebühren, Einspeisung

**Files:**
- Modify: `src/bindaems/app/tariff/calc.py`, `src/bindaems/app/prices/service.py`, `src/bindaems/app/ledger/service.py`
- Test: `tests/unit/app/tariff/test_calc.py`

**Interfaces:**
- Consumes: Typen aus Task 1.
- Produces:
  - `import_price(slot_start, spot_net_ct, tariff) -> ImportPrice` (unverändert; zählt nur `EnergyComponent`)
  - `feed_in_price(slot_start: datetime, feed_in: FeedInSettings, tariff: TariffSettings) -> float | None` (neuer Parameter `tariff`; alle Aufrufer angepasst)
  - `@dataclass(frozen=True) PowerRate(component_id: str, below_eur_kw_year: float, above_eur_kw_year: float, tier_kw: float, minimum_kw: float, vat_factor: float, missing: bool)`
  - `power_rate(day: date, tariff: TariffSettings) -> PowerRate | None` (`missing` wahr, wenn `value_eur` fehlt; dann zählen beide Sätze als 0)
  - `billed_kw(peak_kw: float | None, rate: PowerRate) -> float`
  - `power_cost_eur(peak_kw: float | None, rate: PowerRate) -> float` (Monatsbetrag brutto)
  - `monthly_fees_eur(day: date, tariff: TariffSettings) -> float` (brutto)

- [ ] **Step 1: Write the failing tests**

```python
RATE = TariffSettings.model_validate({"components": [SPOT, {
    "id": "power_price", "name": "Leistungspreis", "kind": "power_per_kw_year",
    "value_eur": 19.0, "value_above_eur": 38.0, "valid_from": "2027-01-01"}]})

def test_power_rate_before_validity_is_none():
    assert power_rate(date(2026, 12, 31), RATE) is None

def test_power_cost_below_tier():
    rate = power_rate(date(2027, 1, 15), RATE)
    assert power_cost_eur(7.5, rate) == pytest.approx(14.25)        # 7,5 · 19 / 12 · 1,2

def test_power_cost_above_tier():
    rate = power_rate(date(2027, 1, 15), RATE)
    assert power_cost_eur(12.0, rate) == pytest.approx(26.6)        # (10 · 19 + 2 · 38) / 12 · 1,2

def test_power_cost_at_tier_boundary():
    rate = power_rate(date(2027, 1, 15), RATE)
    assert power_cost_eur(10.0, rate) == pytest.approx(19.0)

def test_power_cost_uses_minimum_without_peak():
    rate = power_rate(date(2027, 1, 15), RATE)
    assert billed_kw(None, rate) == 2.0 and billed_kw(1.2, rate) == 2.0
    assert power_cost_eur(None, rate) == pytest.approx(3.8)

def test_power_rate_without_value_is_missing_and_free():
    tariff = TariffSettings()  # Standard: Leistungspreis ohne Satz ab 2027
    rate = power_rate(date(2027, 3, 1), tariff)
    assert rate is not None and rate.missing and power_cost_eur(9.0, rate) == 0.0

def test_above_rate_defaults_to_below_rate():
    tariff = TariffSettings.model_validate({"components": [SPOT, {
        "id": "p", "name": "P", "kind": "power_per_kw_year", "value_eur": 24.0}]})
    assert power_rate(date(2027, 1, 1), tariff).above_eur_kw_year == 24.0

def test_monthly_fees_sum_valid_components_gross():
    tariff = TariffSettings.model_validate({"components": [SPOT,
        {"id": "a", "name": "A", "kind": "fixed_per_month", "value_eur": 3.5},
        {"id": "b", "name": "B", "kind": "fixed_per_month", "value_eur": 2.0, "vat": False,
         "valid_until": "2026-09-30"}]})
    assert monthly_fees_eur(date(2026, 10, 1), tariff) == pytest.approx(4.2)

def test_feed_in_adds_feed_in_components():
    tariff = TariffSettings.model_validate({"components": [SPOT,
        {"id": "fee", "name": "Entgelt", "kind": "feed_in_per_kwh", "value_ct": -0.5}]})
    feed_in = FeedInSettings(monthly_ct={"2026-10": 7.3})
    assert feed_in_price(at("2026-10-09T08:00:00+00:00"), feed_in, tariff) == pytest.approx(6.8)
    assert feed_in_price(at("2026-09-09T08:00:00+00:00"), feed_in, tariff) is None

def test_import_price_ignores_other_kinds():
    tariff = TariffSettings()  # mit Leistungspreis und Grundgebühr
    price = import_price(at("2026-10-09T08:00:00+00:00"), 10.0, tariff)
    assert set(price.parts_net) <= {c.id for c in tariff.components if c.kind == "energy_per_kwh"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/tariff/test_calc.py -q`
Expected: FAIL (ImportError `power_rate`)

- [ ] **Step 3: Implement in `src/bindaems/app/tariff/calc.py`** und passe die Aufrufer von `feed_in_price` an (`prices/service.py::_view`, `ledger/service.py` `_summary` und `_publish`: jeweils `settings.tariff` mitgeben). Gültigkeit eines Bestandteils am Tag über eine gemeinsame Hilfsfunktion `_valid_on(component, day) -> bool`, die auch `component_net_ct` nutzt.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/tariff tests/unit/app/prices tests/unit/app/ledger -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/tariff/calc.py src/bindaems/app/prices/service.py src/bindaems/app/ledger/service.py tests/unit/app/tariff/test_calc.py
git commit -m "feat(tarif): Leistungspreis, Gebühren und Einspeisung berechnen"
```

### Task 3: Monatsspitze in der Abrechnung und in Home Assistant

**Files:**
- Modify: `src/bindaems/app/ledger/service.py`, `src/bindaems/app/ha/entities.py`, `src/bindaems/shared/config.py` (`HA_SYSTEM_IDS`), `src/bindaems/app/runtime.py` (`_build_ha_inputs`)
- Test: `tests/unit/app/ledger/test_service.py`, `tests/unit/app/ha/test_entities.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) MonthPeak(kw: float, slot_start: datetime)`
  - `LedgerService.month_peak(day: date) -> MonthPeak | None`: höchste `import_wh · 4 / 1000` aller `ledger_slot` im lokalen Monat von `day`; bei Gleichstand der frühere Slot; ohne Slot `None`
  - `HaInputs.month_peak: MonthPeak | None`; Entität `month_peak` (sensor, kW, eine Nachkommastelle, Attribut `slot`)

- [ ] **Step 1: Write the failing tests**

```python
def test_month_peak_is_highest_quarter_of_local_month(ledger):
    ingest(ledger, "2026-09-30T21:45:00+00:00", {"grid>house": 3000})   # 30. 9. 23:45 lokal: September
    ingest(ledger, "2026-10-01T06:00:00+00:00", {"grid>house": 1500})
    ingest(ledger, "2026-10-05T17:15:00+00:00", {"grid>house": 1200, "grid>wb:evcs": 800})
    peak = ledger.month_peak(date(2026, 10, 9))
    assert peak == MonthPeak(8.0, datetime(2026, 10, 5, 17, 15, tzinfo=UTC))

def test_month_peak_without_slots_is_none(ledger):
    assert ledger.month_peak(date(2026, 10, 9)) is None

def test_month_peak_sensor(cfg):
    inputs = sample_inputs(month_peak=MonthPeak(7.43, datetime(2026, 10, 5, 17, 15, tzinfo=UTC)))
    spec = entity(build_entities(cfg), "month_peak")
    assert spec.value(inputs) == "7.4" and spec.attributes(inputs)["slot"] == "2026-10-05T17:15:00+00:00"
```

`ingest(ledger, when: str | datetime, flows, covered_s=900.0)` ist ein neuer Helfer in `tests/app_helpers.py`: `ledger.ingest(SlotFlows(<when als UTC-Zeit>, covered_s, flows, {}), "stream")`; die Tasks 8, 11 und 12 nutzen ihn weiter. Der erste Slot (30. 9. 23:45 lokal) liegt im September und zählt nicht. `HaInputs.month_peak` hat den Standard `None`; `sample_inputs` überschreibt ihn über `**changes`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/ledger/test_service.py tests/unit/app/ha/test_entities.py -q -k month_peak`
Expected: FAIL

- [ ] **Step 3: Implement** `month_peak` (eine Abfrage mit `ORDER BY import_wh DESC, slot_start ASC LIMIT 1` über die UTC-Grenzen des lokalen Monats), die Entität und `HaInputs.month_peak` (in `_build_ha_inputs` mit `ledger.month_peak(lokales Datum von jetzt)`). Die Entität steht in `HA_SYSTEM_IDS`; der bestehende Test hält beide Listen gleich.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/ledger tests/unit/app/ha tests/unit/shared -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/ledger/service.py src/bindaems/app/ha/entities.py src/bindaems/shared/config.py src/bindaems/app/runtime.py tests/app_helpers.py tests/unit/app/ledger/test_service.py tests/unit/app/ha/test_entities.py
git commit -m "feat(app): Monatsspitze aus der Abrechnung, Sensor in Home Assistant"
```

### Task 4: Tarif-Editor im UI – Entwurf und Prüfung

**Files:**
- Modify: `ui/src/lib/api/schemas.ts` (Union der Bestandteile mit `v.variant('kind', …)`), `ui/src/lib/settings/model.ts`
- Test: `ui/src/lib/settings/model.test.ts`

**Interfaces:**
- Consumes: Settings-JSON aus Task 1 (jeder Bestandteil mit `kind`).
- Produces (in `model.ts`):
  - `type ComponentKind = 'energy_per_kwh' | 'power_per_kw_year' | 'fixed_per_month' | 'feed_in_per_kwh'`
  - `interface WindowDraft { months: number[]; weekdays: number[]; start: string; end: string; mode: 'factor' | 'value'; amount: string }`
  - `interface ComponentDraft { id: string; kind: ComponentKind; name: string; source: 'fixed' | 'spot'; value: string; valueAbove: string; tierKw: string; minimumKw: string; vat: boolean; validFrom: string; validUntil: string; windows: WindowDraft[] }`
  - `interface TariffDraft { vatPct: string; fixedPriceCt: string; components: ComponentDraft[] }`
  - `SettingsDraft.tariff: TariffDraft` ersetzt `SettingsDraft.values`
  - `newComponent(kind: ComponentKind, name: string, existingIds: string[]): ComponentDraft`
  - `componentId(name: string, existingIds: string[]): string`
  - `applyDraft(settings, draft): DraftResult` (wie bisher; Fehlerschlüssel `tariff.vat_pct`, `tariff.fixed_price_gross_ct`, `tariff.components.{i}.{feld}`, `tariff.components.{i}.windows.{j}.{feld}`)
  - `tariffChanged` wahr, wenn sich ein Energiebestandteil oder `vat_pct` ändert

- [ ] **Step 1: Write the failing tests**

```ts
it('builds ids from names, unique and fixed', () => {
	expect(componentId('Grundgebühr Lieferant', ['spot'])).toBe('grundgebuehr_lieferant');
	expect(componentId('Netz', ['netz'])).toBe('netz_2');
	expect(componentId('Netz', ['netz', 'netz_2'])).toBe('netz_3');
});

it('round-trips the default tariff without changes', () => {
	const result = applyDraft(SETTINGS, draftFrom(SETTINGS));
	expect('settings' in result && result.settings).toEqual(SETTINGS);
	expect('settings' in result && result.tariffChanged).toBe(false);
});

it('edits a window factor and marks the tariff as changed', () => {
	const draft = draftFrom(SETTINGS);
	const grid = draft.tariff.components.find((c) => c.id === 'grid_usage')!;
	grid.windows[0].amount = '0,75';
	const result = applyDraft(SETTINGS, draft);
	expect('settings' in result && result.tariffChanged).toBe(true);
	const saved = 'settings' in result ? result.settings.tariff.components.find((c) => c.id === 'grid_usage') : null;
	expect(saved && 'windows' in saved && saved.windows[0].factor).toBe(0.75);
});

it('adds a power price with tier and minimum', () => {
	const draft = draftFrom(SETTINGS);
	draft.tariff.components.push({ ...newComponent('power_per_kw_year', 'Leistung 2028', ids(draft)), value: '34', valueAbove: '68', validFrom: '2028-01-01' });
	const result = applyDraft(SETTINGS, draft);
	const added = 'settings' in result ? result.settings.tariff.components.at(-1) : null;
	expect(added).toMatchObject({ kind: 'power_per_kw_year', id: 'leistung_2028', value_eur: 34, value_above_eur: 68, tier_kw: 10, minimum_kw: 2, valid_from: '2028-01-01' });
	expect('settings' in result && result.tariffChanged).toBe(false);
});

it('reports a window that ends before it starts', () => {
	const draft = draftFrom(SETTINGS);
	draft.tariff.components.find((c) => c.id === 'grid_usage')!.windows[0].end = '09:00';
	const result = applyDraft(SETTINGS, draft);
	expect('errors' in result && result.errors['tariff.components.2.windows.0.end']).toBe('Ende muss nach dem Beginn liegen (00:00 = Tagesende)');
});

it('requires a quarter hour', () => {
	const draft = draftFrom(SETTINGS);
	draft.tariff.components.find((c) => c.id === 'grid_usage')!.windows[0].start = '10:10';
	const result = applyDraft(SETTINGS, draft);
	expect('errors' in result && result.errors['tariff.components.2.windows.0.start']).toBe('Uhrzeit auf einer Viertelstunde, z. B. 10:15');
});
```

`SETTINGS` ist die Vertragsdatei der Standardeinstellungen (`contract/settings.json`, nach Task 1 neu erzeugt), `ids(draft)` die IDs des Entwurfs. Die Umschreibung: ä → ae, ö → oe, ü → ue, ß → ss, alles andere außer `[a-z0-9]` → `_`, mehrfache `_` zusammengefasst, Rand-`_` entfernt, höchstens 40 Zeichen; leer → `bestandteil`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pnpm vitest run src/lib/settings/model.test.ts`
Expected: FAIL

- [ ] **Step 3: Implement** Schemas und `model.ts`. Die Prüfungen spiegeln die des Servers (Zahlen, Viertelstunden, Ende nach Beginn, genau eines von Faktor oder Wert, mindestens ein Monat und ein Wochentag, gültig ab ≤ gültig bis); was nur der Server prüft (Überschneidung der Leistungspreise), kommt über `fieldErrors` aus der 422-Antwort zurück. Die bestehenden Texte `describeWindow` und `validityText` bleiben für die Leseansicht.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && pnpm vitest run src/lib/settings && pnpm check`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui/src/lib/api/schemas.ts ui/src/lib/settings/model.ts ui/src/lib/settings/model.test.ts ui/src/lib/api/contract
git commit -m "feat(ui): Entwurf des Tarifs mit Zeitfenstern, Gültigkeit und neuen Arten"
```

### Task 5: Tarif-Editor im UI – Bedienung

**Files:**
- Create: `ui/src/lib/settings/TariffEditor.svelte`, `ui/src/lib/settings/WindowEditor.svelte`
- Modify: `ui/src/lib/settings/SettingsEditor.svelte`, `ui/src/lib/settings/TariffView.svelte` (Leseansicht der neuen Arten), `ui/tests/e2e/settings.spec.ts`, `docs/betrieb.md` (Abschnitt 8 „Tarif und Abrechnung“)
- Test: `ui/src/lib/settings/TariffEditor.test.ts`, `ui/src/lib/settings/SettingsEditor.test.ts`

**Interfaces:**
- Consumes: `TariffDraft`, `ComponentDraft`, `WindowDraft`, `newComponent`, `componentId` aus Task 4.
- Produces: `<TariffEditor bind:draft={draft.tariff} errors={errors} />`; `<WindowEditor bind:window errors prefix />`.

- [ ] **Step 1: Write the failing tests**

```ts
it('adds and removes a component', async () => {
	const { draft } = renderEditor();
	await user.selectOptions(screen.getByLabelText('Art'), 'fixed_per_month');
	await user.click(screen.getByRole('button', { name: 'Bestandteil hinzufügen' }));
	expect(draft().components.at(-1)?.kind).toBe('fixed_per_month');
	await user.click(screen.getAllByRole('button', { name: 'Bestandteil entfernen' }).at(-1)!);
	expect(draft().components.some((c) => c.id === 'neue_gebuehr')).toBe(false);
});

it('never offers to remove the spot price', () => {
	renderEditor();
	const spot = screen.getByRole('group', { name: 'Börsenpreis' });
	expect(within(spot).queryByRole('button', { name: 'Bestandteil entfernen' })).toBeNull();
	expect(within(spot).getByText('kommt aus der Preisquelle')).toBeTruthy();
});

it('shows tier and minimum only for the power price', () => {
	renderEditor();
	const power = screen.getByRole('group', { name: 'Leistungspreis (ElWG)' });
	expect(within(power).getByLabelText('Stufe (kW)')).toBeTruthy();
	expect(within(power).queryByText('Zeitfenster')).toBeNull();
});

it('shows server errors at the window field', () => {
	renderEditor({ errors: { 'tariff.components.2.windows.0.end': 'Ende muss nach dem Beginn liegen (00:00 = Tagesende)' } });
	expect(screen.getByText('Ende muss nach dem Beginn liegen (00:00 = Tagesende)')).toBeTruthy();
});
```

Neuer Bestandteil: Name vorbelegt mit der Art („Neue Gebühr“, „Neuer Leistungspreis“, „Neue Energie“, „Neue Einspeisung“); die ID entsteht beim Hinzufügen mit `componentId` und wird nicht angezeigt. E2E in `settings.spec.ts`: `admin edits the SNAP window and saves` (Faktor 0,8 → 0,75, Speichern, Neuladen zeigt „× 0,75“ in der Leseansicht und das Formular „Neu bewerten“).

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pnpm vitest run src/lib/settings`
Expected: FAIL

- [ ] **Step 3: Implement** die beiden Komponenten. Je Bestandteil ein `<fieldset>` mit `<legend>` = Name; Monate und Wochentage als Umschalter (`aria-pressed`) mit den Kürzeln aus `model.ts`; Uhrzeiten als `<select>` mit allen Viertelstunden (00:00 bis 23:45, als Ende zusätzlich „24:00“ = `00:00`); Faktor oder fester Wert per Radio. Auf 360 px stehen die Felder untereinander. `SettingsEditor` ersetzt die bisherige Tabelle der Werte durch `TariffEditor` und behält OeMAG, Preise und Namen. `docs/betrieb.md` Abschnitt 8: Bearbeiten beschreibt den Editor; der Satz, Zeitfenster gingen nur über YAML, entfällt.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && pnpm check && pnpm lint && pnpm vitest run && pnpm exec playwright test tests/e2e/settings.spec.ts`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui/src/lib/settings ui/tests/e2e/settings.spec.ts docs/betrieb.md
git commit -m "feat(ui): Tarif-Editor mit Zeitfenstern, Gültigkeit und allen Arten"
```

### Task 6: Tagestyp mit Feiertagen

**Files:**
- Create: `src/bindaems/app/daytype.py`
- Modify: `pyproject.toml`, `uv.lock`
- Test: `tests/unit/app/test_daytype.py`

**Interfaces:**
- Produces:
  - `DayType = Literal["workday", "saturday", "sunday"]`
  - `day_type(day: date) -> DayType`
  - `slot_key(slot: datetime) -> tuple[DayType, time]` (lokales Datum und lokale Uhrzeit des Slotbeginns)

- [ ] **Step 1: Write the failing tests**

```python
def test_day_types():
    assert day_type(date(2026, 10, 16)) == "workday"     # Freitag
    assert day_type(date(2026, 10, 17)) == "saturday"
    assert day_type(date(2026, 10, 18)) == "sunday"
    assert day_type(date(2026, 10, 26)) == "sunday"      # Nationalfeiertag, Montag
    assert day_type(date(2026, 12, 8)) == "sunday"       # Mariä Empfängnis

def test_slot_key_uses_local_time():
    assert slot_key(datetime(2026, 10, 16, 22, 15, tzinfo=UTC)) == ("saturday", time(0, 15))

def test_slot_key_on_autumn_dst_day_repeats_0200():
    first = datetime(2026, 10, 25, 0, 0, tzinfo=UTC)    # 02:00 MESZ
    second = datetime(2026, 10, 25, 1, 0, tzinfo=UTC)   # 02:00 MEZ
    assert slot_key(first) == slot_key(second) == ("sunday", time(2, 0))
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/test_daytype.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** mit `holidays.country_holidays("AT")` (einmal je Jahr erzeugt und zwischengespeichert). `holidays>=0.60` in die Extras `app`, `uv lock`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv sync --all-extras && uv run pytest tests/unit/app/test_daytype.py -q && uv run mypy src`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/daytype.py tests/unit/app/test_daytype.py pyproject.toml uv.lock
git commit -m "feat(app): Tagestyp mit österreichischen Feiertagen"
```

### Task 7: Schätzpreise und Planungsbasis der Preise

**Files:**
- Create: `src/bindaems/app/prices/estimate.py`
- Modify: `src/bindaems/app/prices/service.py`
- Test: `tests/unit/app/prices/test_estimate.py`

**Interfaces:**
- Consumes: `slot_key` (Task 6), `PriceStore.get`, `import_price`, `feed_in_price` (Task 2).
- Produces:
  - `estimate_spot(history: Sequence[StoredPrice], slot: datetime, now: datetime) -> float | None` (Präzisierung 6; `WINDOW = timedelta(days=28)`, `MIN_VALUES = 2`)
  - `@dataclass(frozen=True) PlanningPrice(slot_start: datetime, spot_net_ct: float, import_net_ct: float, import_gross_ct: float, feed_in_ct: float | None, origin: Literal["primary", "fallback", "estimated"])`
  - `PriceService.planning(start: datetime, end: datetime, now: datetime) -> list[PlanningPrice]` (Slots von `start` bis ausschließlich `end`, aufsteigend; ohne gespeicherten Preis und ohne Schätzung fehlt der Slot)

- [ ] **Step 1: Write the failing tests**

```python
NOW = datetime(2026, 10, 16, 10, 0, tzinfo=UTC)   # Freitag 12:00 lokal

def stored(iso: str, ct: float) -> StoredPrice: ...   # Helfer im Test: origin "primary"

def test_estimate_is_median_of_same_quarter_and_day_type():
    history = [stored(f"2026-10-{d:02d}T10:00:00+00:00", ct)
               for d, ct in [(12, 10.0), (13, 12.0), (14, 14.0), (15, 30.0), (10, 99.0)]]  # 10. ist Samstag
    assert estimate_spot(history, datetime(2026, 10, 19, 10, 0, tzinfo=UTC), NOW) == 13.0

def test_estimate_ignores_values_older_than_28_days():
    history = [stored("2026-09-17T10:00:00+00:00", 50.0), stored("2026-10-15T10:00:00+00:00", 8.0),
               stored("2026-10-14T10:00:00+00:00", 10.0)]
    assert estimate_spot(history, datetime(2026, 10, 19, 10, 0, tzinfo=UTC), NOW) == 9.0

def test_estimate_falls_back_to_all_day_types():
    history = [stored("2026-10-10T10:00:00+00:00", 20.0), stored("2026-10-11T10:00:00+00:00", 24.0)]
    assert estimate_spot(history, datetime(2026, 10, 19, 10, 0, tzinfo=UTC), NOW) == 22.0

def test_estimate_without_enough_values_is_none():
    assert estimate_spot([stored("2026-10-15T10:00:00+00:00", 8.0)], datetime(2026, 10, 19, 10, 0, tzinfo=UTC), NOW) is None

def test_estimate_on_dst_day_uses_local_clock():
    # 02:00 MEZ am 25. 10. (zweiter Durchlauf) passt zu 02:00 der Sonntage davor
    history = [stored("2026-10-11T00:00:00+00:00", 5.0), stored("2026-10-18T00:00:00+00:00", 7.0)]
    assert estimate_spot(history, datetime(2026, 10, 25, 1, 0, tzinfo=UTC), datetime(2026, 10, 24, 12, 0, tzinfo=UTC)) == 6.0

def test_planning_marks_estimated_slots(price_service, store):
    store.save([price_row(at("2026-10-16T10:00:00+00:00"), 10.0, "primary")]
               + [price_row(at(f"2026-10-{d}T10:15:00+00:00"), 12.0, "primary") for d in (12, 13)])
    rows = price_service.planning(at("2026-10-16T10:00:00+00:00"), at("2026-10-16T10:30:00+00:00"), NOW)
    assert [(r.origin, r.spot_net_ct) for r in rows] == [("primary", 10.0), ("estimated", 12.0)]
```

`price_row(slot, net, origin)` aus `tests/app_helpers.py`. Die Werte des Medians: gerade Anzahl → Mittel der beiden mittleren (`statistics.median`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/prices/test_estimate.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** `estimate.py` und `PriceService.planning` (eine Abfrage `store.get(now − 28 d, end)`, daraus gespeicherte Preise für die angefragten Slots und die Historie vor `now`). Nur `spot_net_ct` geht in den Median; die Tarifbestandteile rechnet `planning` danach wie `_view`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/prices -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/prices/estimate.py src/bindaems/app/prices/service.py tests/unit/app/prices/test_estimate.py tests/app_helpers.py
git commit -m "feat(preise): Schätzpreise und Planungsbasis mit Herkunft"
```

### Task 8: Prognose-Archiv und Ist-Werte

**Files:**
- Create: `src/bindaems/app/forecast/archive.py`, `src/bindaems/app/db/migrations/versions/v0008_forecasts.py`
- Modify: `src/bindaems/app/db/schema.py` (Tabellen `forecast_record`, `forecast_metric`)
- Test: `tests/unit/app/forecast/test_archive.py`, `tests/unit/app/test_db.py`

**Interfaces:**
- Consumes: `LedgerSlot` aus `ledger/service.py`.
- Produces:
  - `HorizonClass = Literal["h0_6", "h6_24", "h24"]`, `REPRESENTATIVE_LEAD: Mapping[HorizonClass, timedelta]` (3 h, 15 h, 30 h)
  - `Clearness = Literal["clear", "mixed", "overcast"]`
  - `horizon_class(lead: timedelta) -> HorizonClass | None` (`None` bei `lead < 0`)
  - `@dataclass(frozen=True) RecordValue(p50_w: float, raw_w: float, clearness: Clearness | None = None)`
  - `@dataclass(frozen=True) ForecastRecord(kind: Literal["pv", "load"], slot_start: datetime, horizon: HorizonClass, issued_at: datetime, p50_w: float, raw_w: float, clearness: Clearness | None)`
  - `@dataclass(frozen=True) Actual(pv_w: float, house_w: float)`; `actual_from_ledger(slot: LedgerSlot) -> Actual | None` (`COMPLETE_S = 855.0`)
  - `ForecastArchive(engine: Engine)` mit `record(kind, issued_at, values: Mapping[datetime, RecordValue]) -> int`, `records(kind, start, end, horizon: HorizonClass | None = None) -> list[ForecastRecord]`, `cleanup(before: datetime) -> int`
  - `actuals(ledger: LedgerService, start: datetime, end: datetime) -> dict[datetime, Actual]`

- [ ] **Step 1: Write the failing tests**

```python
def test_horizon_classes():
    assert horizon_class(timedelta(minutes=-1)) is None
    assert horizon_class(timedelta(0)) == "h0_6"
    assert horizon_class(timedelta(hours=6)) == "h6_24"
    assert horizon_class(timedelta(hours=24)) == "h24"

def test_record_keeps_issue_closest_to_class_middle(archive):
    slot = at("2026-10-16T20:00:00+00:00")
    archive.record("pv", at("2026-10-16T04:00:00+00:00"), {slot: RecordValue(1000, 1000)})  # 16 h
    archive.record("pv", at("2026-10-16T08:00:00+00:00"), {slot: RecordValue(2000, 2000)})  # 12 h
    archive.record("pv", at("2026-10-16T17:00:00+00:00"), {slot: RecordValue(3000, 3000)})  # 3 h
    rows = {r.horizon: r.p50_w for r in archive.records("pv", slot, slot + SLOT)}
    assert rows == {"h6_24": 1000, "h0_6": 3000}

def test_record_skips_slots_before_issue(archive):
    n = archive.record("load", at("2026-10-16T10:07:00+00:00"),
                       {at("2026-10-16T10:00:00+00:00"): RecordValue(500, 500)})
    assert n == 0

def test_record_on_dst_day_keeps_both_0200_slots(archive):
    a, b = at("2026-10-25T00:00:00+00:00"), at("2026-10-25T01:00:00+00:00")
    archive.record("load", at("2026-10-24T22:00:00+00:00"), {a: RecordValue(1, 1), b: RecordValue(2, 2)})
    assert [r.p50_w for r in archive.records("load", a, b + SLOT)] == [1, 2]

def test_actual_from_complete_slot():
    slot = make_ledger_slot({"pv>house": 250, "pv>grid": 500, "grid>house": 100, "battery>house": 50,
                        "grid>wb:evcs": 1000}, covered_s=900)
    assert actual_from_ledger(slot) == Actual(pv_w=3000.0, house_w=1600.0)

def test_incomplete_slot_has_no_actual():
    assert actual_from_ledger(make_ledger_slot({"pv>house": 250}, covered_s=600)) is None

def test_cleanup_removes_old_records(archive):
    archive.record("pv", at("2026-08-01T08:00:00+00:00"), {at("2026-08-01T09:00:00+00:00"): RecordValue(1, 1)})
    assert archive.cleanup(at("2026-08-15T00:00:00+00:00")) == 1
```

`make_ledger_slot(flows, covered_s)` baut ein `LedgerSlot` für den Slot `2026-10-15T08:00Z`. `cleanup(before)` löscht Einträge mit `slot_start < before`. In `test_db.py`: Migration `0008` legt beide Tabellen an und lässt sich auf `0007` zurücknehmen.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/forecast/test_archive.py tests/unit/app/test_db.py -q`
Expected: FAIL

- [ ] **Step 3: Implement** Schema, Migration und Archiv. `record` liest die vorhandenen Zeilen der betroffenen Slots in einem Zug, entscheidet je Slot und Klasse nach Präzisierung 7 und schreibt mit `INSERT … ON CONFLICT DO UPDATE`. `forecast_metric` wird hier nur angelegt (Task 12 füllt sie).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/forecast/archive.py src/bindaems/app/db tests/unit/app/forecast/test_archive.py tests/unit/app/test_db.py
git commit -m "feat(prognose): Archiv der Prognosen je Horizont und Ist-Werte aus der Abrechnung"
```

### Task 9: PV – Klarheit und Archiv bei jeder Ausgabe

**Files:**
- Create: `src/bindaems/app/forecast/calibration.py` (vorerst nur `clearness_class`)
- Modify: `src/bindaems/app/forecast/openmeteo.py`, `src/bindaems/app/forecast/service.py`, `src/bindaems/app/runtime.py`
- Test: `tests/unit/app/forecast/test_calibration.py`, `tests/unit/app/forecast/test_openmeteo.py`, `tests/unit/app/forecast/test_service.py`; Aufnahme `tests/fixtures/openmeteo_sued_2026-10-09.json` um die beiden Variablen ergänzt (neue Aufnahme oder von Hand aus den Werten von Open-Meteo für den Tag)

**Interfaces:**
- Consumes: `ForecastArchive`, `RecordValue`, `Clearness` (Task 8).
- Produces:
  - `VARIABLES = ("global_tilted_irradiance", "temperature_2m", "shortwave_radiation", "diffuse_radiation")`
  - `WeatherSlot(slot_start, gti_w_m2, temp_c, ghi_w_m2: float | None = None, diffuse_w_m2: float | None = None)`
  - `clearness_class(ghi_w_m2: float | None, diffuse_w_m2: float | None) -> Clearness | None` (in `app/forecast/calibration.py`, Task 10 nutzt sie weiter)
  - `PvForecast(issued_at, source, p50_w, raw_w: Mapping[datetime, float], p25_w: Mapping[datetime, float], p75_w: Mapping[datetime, float], clearness: Mapping[datetime, Clearness | None])`; in diesem Task gilt `p50_w = raw_w`, `p25_w = p75_w = p50_w`
  - `ForecastService(..., archive: ForecastArchive | None = None)`: `refresh` schreibt die Ausgabe ins Archiv (`kind="pv"`)

- [ ] **Step 1: Write the failing tests**

```python
def test_clearness_classes():
    assert clearness_class(800, 100) == "clear"
    assert clearness_class(400, 200) == "mixed"
    assert clearness_class(300, 270) == "overcast"
    assert clearness_class(30, 30) is None
    assert clearness_class(None, None) is None

def test_parse_without_radiation_variables_keeps_working():
    slots = parse_plane(OLD_BODY)  # Antwort ohne shortwave/diffuse
    assert slots and slots[0].ghi_w_m2 is None

async def test_refresh_records_forecast(service, archive, clock):
    await service.refresh()
    rows = archive.records("pv", clock.now(), clock.now() + timedelta(days=1))
    assert rows and all(r.raw_w == r.p50_w for r in rows)
    assert {r.clearness for r in rows if r.raw_w > 0} - {None} <= {"clear", "mixed", "overcast"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/forecast -q`
Expected: FAIL

- [ ] **Step 3: Implement.** Fehlende Einträge der beiden neuen Variablen lassen den Slot gelten (nur die Klarheit bleibt leer); die Einheit beider muss `W/m²` sein, wenn vorhanden. Die Klarheit kommt aus der ersten Fläche. In `runtime.py` bekommt `ForecastService` das Archiv.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/forecast tests/integration/test_app_runtime.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/forecast src/bindaems/app/runtime.py tests/unit/app/forecast tests/fixtures/openmeteo_sued_2026-10-09.json
git commit -m "feat(prognose): Klarheit aus Open-Meteo, PV-Ausgaben im Archiv"
```

### Task 10: PV – Kalibrierung und Quantile

**Files:**
- Modify: `src/bindaems/app/forecast/calibration.py` (aus Task 9), `src/bindaems/app/forecast/service.py`, `src/bindaems/app/forecast/routes.py`, `src/bindaems/app/runtime.py`
- Test: `tests/unit/app/forecast/test_calibration.py`, `tests/unit/app/forecast/test_service.py`, `tests/unit/app/forecast/test_routes.py`

**Interfaces:**
- Consumes: Archiv und `actuals` (Task 8), `PvForecast` (Task 9).
- Produces:
  - `@dataclass(frozen=True) CalPair(local_hour: int, clearness: Clearness | None, raw_w: float, actual_w: float)`
  - `@dataclass(frozen=True) Calibration(factors: Mapping[tuple[int, Clearness | None], float], hour_factors: Mapping[int, float], samples: int)` mit `factor(hour: int, clearness: Clearness | None) -> float`; `NEUTRAL = Calibration({}, {}, 0)`
  - `calibrate(pairs: Sequence[CalPair], threshold_w: float) -> Calibration` (`MIN_PAIRS_FACTOR = 8`, `FACTOR_RANGE = (0.5, 1.5)`)
  - `@dataclass(frozen=True) ErrPair(horizon: HorizonClass, clearness: Clearness | None, p50_w: float, actual_w: float)`
  - `@dataclass(frozen=True) Bands(by_class: Mapping[tuple[HorizonClass, Clearness | None], tuple[float, float]], by_horizon: Mapping[HorizonClass, tuple[float, float]])` mit `band(horizon, clearness) -> tuple[float, float]`; `DEFAULT_BAND = (0.7, 1.2)`
  - `fit_bands(pairs: Sequence[ErrPair], threshold_w: float) -> Bands` (`MIN_PAIRS_BAND = 20`)
  - `pv_threshold_w(cfg: Config) -> float` = `max(100.0, 0.02 · Σ kWp · 1000)`
  - `apply_band(p50_w: float, band: tuple[float, float], max_w: float) -> tuple[float, float, float]` (P25, P50, P75; `P25 = min(P50 · q25, P50)`, `P75 = min(max(P50 · q75, P50), max_w)`)
  - `ForecastService.recalibrate(ledger: LedgerService) -> None` (synchron, der Job ruft sie über `asyncio.to_thread`; 30 Tage, Präzisierung 9); `refresh` wendet Faktor und Band an
  - Route `GET /api/forecast/pv`: Slots `{start, p50_w, p25_w, p75_w}`

- [ ] **Step 1: Write the failing tests**

```python
def test_factor_is_median_ratio_per_hour_and_class():
    pairs = [CalPair(12, "clear", 5000, 4000)] * 10
    assert calibrate(pairs, 200).factor(12, "clear") == pytest.approx(0.8)

def test_factor_is_clamped():
    pairs = [CalPair(12, "clear", 5000, 500)] * 10
    assert calibrate(pairs, 200).factor(12, "clear") == 0.5

def test_factor_falls_back_to_hour_then_neutral():
    pairs = [CalPair(12, "clear", 5000, 4500)] * 9 + [CalPair(12, "mixed", 3000, 3000)] * 2
    cal = calibrate(pairs, 200)
    assert cal.factor(12, "mixed") == pytest.approx(0.9)    # Median der Stunde über 11 Paare
    assert cal.factor(9, "clear") == 1.0

def test_pairs_below_threshold_are_ignored():
    assert calibrate([CalPair(7, "clear", 150, 50)] * 10, 200).factor(7, "clear") == 1.0

def test_bands_from_error_quartiles():
    ratios = [0.6 + 0.04 * i for i in range(21)]          # 0,60 … 1,40
    pairs = [ErrPair("h0_6", "clear", 1000, 1000 * r) for r in ratios]
    assert fit_bands(pairs, 200).band("h0_6", "clear") == pytest.approx((0.8, 1.2))
    assert fit_bands(pairs, 200).band("h24", "mixed") == DEFAULT_BAND

async def test_cold_start_uses_neutral_calibration(service):
    forecast = await service.refresh()
    slot, p50 = next((s, w) for s, w in forecast.p50_w.items() if w > 1000)
    assert p50 == forecast.raw_w[slot]
    assert forecast.p25_w[slot] == pytest.approx(0.7 * p50) and forecast.p75_w[slot] == pytest.approx(min(1.2 * p50, 10000))

def test_bands_keep_order_and_inverter_limit():
    assert apply_band(9000.0, (0.9, 1.3), 10000.0) == (8100.0, 9000.0, 10000.0)
    assert apply_band(9000.0, (1.1, 1.3), 10000.0) == (9000.0, 9000.0, 10000.0)

def test_pv_route_has_quantiles(client):
    slot = client.get("/api/forecast/pv").json()["slots"][0]
    assert set(slot) == {"start", "p50_w", "p25_w", "p75_w"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/forecast -q`
Expected: FAIL

- [ ] **Step 3: Implement.** `recalibrate` liest Archiv (`h0_6` für die Faktoren, alle Klassen für die Bänder) und `actuals` der letzten 30 Tage, baut die Paare (lokale Stunde des Slots) und setzt beide Modelle; `refresh` berechnet je Slot `p50 = clamp(raw · factor, 0, inverter)`, das Band über die Horizontklasse des Slots ab `issued_at` (Slots vor `issued_at` nehmen `h0_6`) und schreibt alle drei Quantile nach InfluxDB. Der Komponenten-Status bekommt den Zusatz aus „Texte“. In `runtime.py` ein Job `forecast-nightly` (`_forecast_nightly_loop`): beim Start und täglich um 02:30 lokal (`next_local`) `forecast.recalibrate(ledger)` und `archive.cleanup(jetzt − 60 Tage)`; Task 12 hängt die Kennzahlen an.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/forecast tests/integration/test_app_runtime.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/forecast src/bindaems/app/runtime.py tests/unit/app/forecast
git commit -m "feat(prognose): PV-Kalibrierung je Stunde und Klarheit, Quantile P25 und P75"
```

### Task 11: Lastprognose v1

**Files:**
- Create: `src/bindaems/app/forecast/load.py`
- Modify: `src/bindaems/shared/config.py` (`HomeAssistantConfig.load_history`), `deploy/config.example.yaml` (auskommentiertes Beispiel), `src/bindaems/app/forecast/routes.py`, `src/bindaems/app/runtime.py`
- Test: `tests/unit/app/forecast/test_load.py`, `tests/unit/app/forecast/test_routes.py`, `tests/unit/shared/test_config.py`

**Interfaces:**
- Consumes: `slot_key` (Task 6), `actuals`, `ForecastArchive`, `RecordValue` (Task 8), `InfluxReader`, `quote_ident`, `quote_str`.
- Produces:
  - `LoadHistoryConfig(entity: Annotated[str, Field(pattern=r"^sensor\.[a-z0-9_]+$")], subtract: list[same] = [])`; `HomeAssistantConfig.load_history: LoadHistoryConfig | None = None`
  - `@dataclass(frozen=True) LoadSample(slot_start: datetime, house_w: float, source: Literal["ledger", "ha"])`
  - `weighted_median(items: Sequence[tuple[float, float]]) -> float` (Wert, Gewicht)
  - `@dataclass(frozen=True) LoadProfile(by_key: Mapping[tuple[DayType, time], float], by_time: Mapping[time, float], overall: float | None, samples: int)` mit `at(slot: datetime) -> float | None`
  - `build_profile(samples: Sequence[LoadSample], now: datetime) -> LoadProfile` (`WINDOW_DAYS = 56`, `HALF_LIFE_DAYS = 14`, `MIN_SAMPLES = 3`)
  - `@dataclass(frozen=True) LoadForecast(issued_at: datetime, p50_w: Mapping[datetime, float], basis: Mapping[str, int])` mit `day_energy_kwh(day) -> float | None`
  - `LoadForecastService(ledger, cfg, reader: InfluxReader | None, archive, clock, sink=None, *, sleep=asyncio.sleep)` mit `latest()`, `async history(start, end) -> list[LoadSample]`, `async refresh() -> LoadForecast | None`, `async run()`, `component_status()` (Name `load-forecast`)
  - Route `GET /api/forecast/load` → `{issued_at, slots: [{start, p50_w}], days: [{date, kwh}], basis: {ledger, ha}, status}`

- [ ] **Step 1: Write the failing tests**

```python
def test_weighted_median():
    assert weighted_median([(100, 1), (200, 1), (300, 1)]) == 200
    assert weighted_median([(100, 3), (200, 1), (300, 1)]) == 100

def test_profile_separates_day_types():
    samples = [sample(day, "08:00", 400) for day in workdays(4)] + [sample(day, "08:00", 900) for day in saturdays(4)]
    profile = build_profile(samples, NOW)
    assert profile.at(local("2026-10-19 08:00")) == 400     # Montag
    assert profile.at(local("2026-10-17 08:00")) == 900     # Samstag
    assert profile.at(local("2026-10-26 08:00")) == 400     # Feiertag: keine Sonntagswerte → alle Tagestypen

def test_recent_samples_weigh_more():
    samples = [sample(NOW - days(1), "08:00", 300), sample(NOW - days(15), "08:00", 900), sample(NOW - days(22), "08:00", 900)]
    assert build_profile(samples, NOW).at(local("2026-10-19 08:00")) == 300   # ungewichtet wäre es 900

def test_samples_older_than_56_days_are_ignored():
    profile = build_profile([sample(NOW - days(57), "08:00", 900)] * 3, NOW)
    assert profile.samples == 0 and profile.at(local("2026-10-19 08:00")) is None

def test_profile_covers_100_slot_day():
    profile = build_profile([sample(day, t, 500) for day in sundays(3) for t in all_quarters()], NOW)
    slots = local_day_slots(date(2026, 10, 25))
    assert len(slots) == 100 and all(profile.at(s) == 500 for s in slots)

async def test_no_history_gives_no_forecast_and_status(load_service):
    assert await load_service.refresh() is None
    assert load_service.component_status().message == "Noch keine Daten für die Lastprognose"

async def test_incomplete_ledger_slot_falls_back_to_ha(load_service_with_ha, ledger, influx):
    ingest(ledger, "2026-10-12T06:00:00+00:00", {"grid>house": 100}, covered_s=600)
    influx.returns(entity="sensor.hausverbrauch", minutes={"2026-10-12T06:00": 800, ...})
    samples = await load_service_with_ha.history(at("2026-10-12T06:00:00+00:00"), at("2026-10-12T06:15:00+00:00"))
    assert samples == [LoadSample(at("2026-10-12T06:00:00+00:00"), 800.0, "ha")]

async def test_refresh_records_and_publishes(load_service, archive, sink, ledger):
    for day in workdays(2):
        ingest(ledger, at_local(day, "10:00"), {"grid>house": 125})          # 500 W
    forecast = await load_service.refresh()
    assert forecast is not None and forecast.basis == {"ledger": 2, "ha": 0}
    assert archive.records("load", NOW, NOW + timedelta(days=1))
    assert {p.tags["kind"] for p in sink.points if p.measurement == "forecast"} == {"load"}
```

`sample`, `workdays`, `saturdays`, `sundays`, `all_quarters`, `local`, `days` sind kleine Helfer im Test; `NOW = 2026-10-16 06:00 UTC`. Die HA-Abfrage im Test über respx wie in `tests/unit/app/consumers`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/forecast/test_load.py -q`
Expected: FAIL

- [ ] **Step 3: Implement.** HA-Abfrage: `SELECT mean("value") FROM <beide Schemata> WHERE "entity_id" = … AND time >= … AND time < … GROUP BY time(1m) fill(previous)`, dann je Slot das Mittel der Minuten; Einheit `kW` wird mit 1000 multipliziert. Historie: `ledger.slots(now − 56 d, now)` → `actual_from_ledger`; HA nur für Slots ohne Ist. `run()`: beim Start bauen, dann zur Minute 9 jeder Stunde ausgeben; neu bauen beim ersten Lauf eines neuen lokalen Tages; nach einem Fehler spätestens nach 600 s erneut. Job `load-forecast` in `runtime.py`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/forecast tests/unit/shared tests/integration/test_app_runtime.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/forecast src/bindaems/shared/config.py deploy/config.example.yaml src/bindaems/app/runtime.py tests/unit/app/forecast tests/unit/shared/test_config.py
git commit -m "feat(prognose): Lastprognose v1 als gewichtetes Profil aus Abrechnung und HA"
```

### Task 12: Kennzahlen der Prognosegüte

**Files:**
- Create: `src/bindaems/app/forecast/quality.py`
- Modify: `src/bindaems/app/forecast/routes.py`, `src/bindaems/app/runtime.py` (`_forecast_nightly_loop`)
- Test: `tests/unit/app/forecast/test_quality.py`, `tests/unit/app/forecast/test_routes.py`

**Interfaces:**
- Consumes: Archiv, `actuals` (Task 8), `pv_threshold_w` (Task 10).
- Produces:
  - `ForecastQuality(engine, archive, ledger, threshold_w: float)` mit `update(day: date) -> int` (Zeilen geschrieben) und `summary(first: date, last: date) -> list[QualityRow]`
  - `@dataclass(frozen=True) QualityRow(kind: Literal["pv", "load"], horizon: HorizonClass, n: int, mae_w: float, bias_w: float, rel_mae_pct: float | None)`
  - Route `GET /api/forecast/quality?days=14` → `{days, rows: [{kind, horizon, n, mae_w, bias_w, rel_mae_pct}]}`; `days` außerhalb 1–60 → 422

- [ ] **Step 1: Write the failing tests**

```python
def test_metrics_for_a_day(quality, archive, ledger):
    s1, s2 = at("2026-10-15T08:00:00+00:00"), at("2026-10-15T08:15:00+00:00")
    archive.record("load", at("2026-10-15T05:00:00+00:00"), {s1: RecordValue(1000, 1000), s2: RecordValue(500, 500)})
    ingest(ledger, "2026-10-15T08:00:00+00:00", {"grid>house": 200})    # 800 W
    ingest(ledger, "2026-10-15T08:15:00+00:00", {"grid>house": 175})    # 700 W
    assert quality.update(date(2026, 10, 15)) == 1
    [row] = quality.summary(date(2026, 10, 15), date(2026, 10, 15))
    assert (row.kind, row.horizon, row.n, row.mae_w, row.bias_w) == ("load", "h0_6", 2, 200.0, 0.0)
    assert row.rel_mae_pct == pytest.approx(26.67, abs=0.01)

def test_pv_night_slots_are_ignored(quality, archive, ledger):
    night = at("2026-10-15T00:00:00+00:00")
    archive.record("pv", at("2026-10-14T21:00:00+00:00"), {night: RecordValue(0, 0)})
    ingest(ledger, "2026-10-15T00:00:00+00:00", {"grid>house": 100})
    assert quality.update(date(2026, 10, 15)) == 0

def test_update_replaces_the_day(quality, archive, ledger):
    ...  # Aufbau wie test_metrics_for_a_day
    quality.update(date(2026, 10, 15)); quality.update(date(2026, 10, 15))
    assert [r.n for r in quality.summary(date(2026, 10, 15), date(2026, 10, 15))] == [2]

def test_quality_without_records_is_empty(quality):
    assert quality.summary(date(2026, 10, 1), date(2026, 10, 15)) == []

def test_quality_route_validates_days(client):
    assert client.get("/api/forecast/quality?days=0").status_code == 422
    assert client.get("/api/forecast/quality?days=61").status_code == 422
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/forecast/test_quality.py -q`
Expected: FAIL

- [ ] **Step 3: Implement.** `update(day)` löscht die Zeilen des Tages und schreibt sie aus Archiv und `actuals` der Slots des lokalen Tages neu (Summen je Art und Horizont). `summary` summiert über die Tage und rechnet MAE, Bias und relativen MAE. Der nächtliche Job ruft `update` für gestern und vorgestern, beim Start für die letzten 3 Tage, jeweils vor der Kalibrierung.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/forecast tests/integration/test_app_runtime.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/bindaems/app/forecast src/bindaems/app/runtime.py tests/unit/app/forecast
git commit -m "feat(prognose): Kennzahlen der Prognosegüte je Art und Horizont"
```

### Task 13: Prognosen im UI

**Files:**
- Modify: `ui/src/lib/api/schemas.ts`, `ui/src/lib/api/endpoints.ts`, `ui/src/lib/api/contract/*`, `tests/integration/test_ui_contract.py`, `tests/ui_world.py` (Demo-Welt: Lastprognose, Kennzahlen), `ui/src/lib/charts/options.ts`, `ui/src/routes/+page.svelte`, `ui/src/routes/system/+page.svelte`
- Create: `ui/src/lib/system/LoadForecastCard.svelte`, `ui/src/lib/system/QualityCard.svelte`
- Test: `ui/src/lib/charts/options.test.ts`, `ui/src/lib/system/LoadForecastCard.test.ts`, `ui/src/lib/system/QualityCard.test.ts`, `ui/tests/e2e/pages.spec.ts`

**Interfaces:**
- Consumes: Routen aus Tasks 10 bis 12.
- Produces:
  - `ForecastSlotSchema = { start, p50_w, p25_w, p75_w }`; `LoadForecastSchema`, `QualitySchema`
  - `priceForecastOption(prices, forecast, load: LoadForecast | null, nowIso, palette)`: Reihen „PV-Prognose“, „PV P25–P75“ (gestapelte Fläche aus P25 und P75 − P25, ohne Linie), „Lastprognose“ (Linie, gestrichelt)

- [ ] **Step 1: Write the failing tests**

```ts
it('draws the PV band and the load forecast', () => {
	const option = priceForecastOption(PRICES, FORECAST, LOAD, NOW, palette) as { series: { name: string }[] };
	expect(option.series.map((s) => s.name)).toEqual(expect.arrayContaining(['PV-Prognose', 'PV P25–P75', 'Lastprognose']));
});

it('works without load forecast', () => {
	const option = priceForecastOption(PRICES, FORECAST, null, NOW, palette) as { series: { name: string }[] };
	expect(option.series.some((s) => s.name === 'Lastprognose')).toBe(false);
});

it('shows quality rows with German labels', () => {
	render(QualityCard, { data: { days: 14, rows: [{ kind: 'pv', horizon: 'h0_6', n: 120, mae_w: 412.5, bias_w: -80, rel_mae_pct: 18.3 }] } });
	expect(screen.getByText('PV')).toBeTruthy();
	expect(screen.getByText('bis 6 h')).toBeTruthy();
	expect(screen.getByText('18,3 %')).toBeTruthy();
});

it('shows the empty state', () => {
	render(QualityCard, { data: { days: 14, rows: [] } });
	expect(screen.getByText('Noch keine Kennzahlen.')).toBeTruthy();
});
```

MAE und Abweichung in W ohne Nachkommastelle (`412 W`, `−80 W`) über `format.ts`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ui && pnpm vitest run src/lib/charts src/lib/system`
Expected: FAIL

- [ ] **Step 3: Implement**, dann die Vertragsdateien neu erzeugen und die Demo-Welt (`tests/ui_world.py`) um Lastprognose und Kennzahlen ergänzen.

- [ ] **Step 4: Run tests to verify they pass**

Run: `UPDATE_UI_CONTRACT=1 uv run pytest tests/integration/test_ui_contract.py -q && cd ui && pnpm check && pnpm lint && pnpm vitest run && pnpm exec playwright test`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add ui tests/integration/test_ui_contract.py tests/ui_world.py
git commit -m "feat(ui): PV-Band, Lastprognose und Prognosegüte"
```

### Task 14: Dokumentation und Stand der Spec

**Files:**
- Modify: `docs/betrieb.md` (neuer Abschnitt „Prognosen“: Lastprognose mit `homeassistant.load_history`, Kalibrierung, Quantile, Kennzahlen, nächtlicher Lauf; Abschnitt 8 um Leistungspreis, Gebühren und Einspeisung; HA-Sensor „Monatsspitze“), `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absätze „Stand Plan 3a“ unter 9.1, 9.2, 10.1, 10.2, 11.3 und 18), `deploy/config.example.yaml` (falls in Task 11 noch nicht)
- Test: `tests/unit/test_docs.py` (bestehende Prüfungen der Doku)

- [ ] **Step 1:** Abschnitte schreiben. Der Absatz unter Spec 18 nennt den Zuschnitt (Entscheidung 1) und die Vorab-Entscheidungen für 3b und 3c (Entscheidung 6), so wie der Betreiber sie freigegeben hat.
- [ ] **Step 2:** Run: `uv run pytest tests/unit/test_docs.py -q` — Expected: PASS
- [ ] **Step 3:** Alle Prüfungen der Projektanweisung: `uv run ruff format --check . && uv run ruff check . && uv run mypy src cerbo-watchdog && uv run lint-imports && uv run pytest --cov` (≥ 75 % gesamt, Schwellen der CI) und im UI `pnpm check && pnpm lint && pnpm vitest run`. Expected: alles grün.
- [ ] **Step 4: Commit**

```bash
git add docs deploy/config.example.yaml
git commit -m "docs: Tarif und Prognosen (Plan 3a) in Betriebshandbuch und Spec"
```

`docs/uebergabe.md` wird mit dem Abschluss der Phase nachgeführt, nicht in diesem Task.

# Netzladen auf Vorrat verhindern Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der Optimierer lädt den Akku nicht mehr aus dem Netz auf Vorrat für die Zeit nach dem Horizont, wenn das Haus diese Energie danach gar nicht braucht. Heute lädt er bei hohen geschätzten Preisen nach dem Horizont bis 100 % (Plan vom 14.11.2026, 19:18: rund 70 kWh zu 25–28 ct von 22 % auf 99 %, Erwartete Kosten 27,93 € gegen 6,92 € für den Victron allein).

**Architecture:** Nur app, Planung. `assemble_inputs` berechnet neu `PlanInputs.end_need_kwh`: die Energie, die das Haus in den 24 h nach dem Horizont aus dem Akku braucht (Last minus PV, auf der Akkuseite). Der Optimierer zieht den Restwert für den Teil des End-SOC über `Reserve + end_need_kwh` wieder ab, **aber nur, wenn am Horizontende Netzenergie im Akku sein kann** (`c_T` aus Regel R1). Die Plausibilität (`pays_off_at_end`) und der Text des Slots (neu `NETZLADEN_RESTWERT`) rechnen mit derselben Grenze.

**Tech Stack:** Python 3.12, PuLP/HiGHS, pydantic, pytest; UI nur die Liste der Codes (TypeScript, Vitest).

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Restwert (Zeile ~398 und ~1554), Regel R1 (Stand Plan 3d), Begründungscodes (Zeile ~1756). Neuer Absatz „Stand Plan Netzladen auf Vorrat“ kommt mit Task 4.

## Befund

- **Ziel heute** (`optimizer.py`, `_solve`): `terms.append(-inputs.end_value_ct * soc[-1])`. Der Restwert ist linear und unbegrenzt: Jede kWh im Akku am Horizontende ist `end_value_ct` wert, egal wie voll der Akku ist.
- **Restwert** (`inputs.end_value_ct`): `0,9 · (η_dis · Median der Bezugspreise in [Horizontende, +24 h) − Verschleiß)`. Endet der Horizont Sonntag 24:00, sind das die *geschätzten* Montagspreise (Median der Werktage der letzten 28 Tage, `prices/estimate.py`), im November hoch.
- **Netzladen für den Restwert lohnt sich**, sobald `Bezugspreis + Marge < η_ch · Restwert`. Mit den Standardwerten (Roundtrip 88 %, Verschleiß 2 ct, Marge 2 ct, Faktor 0,9) heißt das bei 27,6 ct ein Median ab etwa 39,5 ct, bei 25,5 ct ab etwa 37 ct.
- **Nachgerechnet** mit dem Optimierer auf `sha-146d7cc` (80 kWh, Preise wie am 15.11., `scenario`): Median 40 ct → 64 kWh Netzladen, End-SOC 98 %; Median 34 ct → 5 kWh (nur für 16:00 und 21:00 mit 38,5 ct); Faktor 0,6 bei 40 ct → 5 kWh. Auf `main` steht dieselbe Zielfunktion.
- **Wirtschaftlich** kostet eine kWh, die bei 26,5 ct gekauft und später ins Haus gegeben wird, mit Verlust und Verschleiß rund 32 ct. Rund 65 kWh sind etwa zwei Tage Hausverbrauch; der Victron gibt sie rund um die Uhr ab, auch zu 25–30 ct. Der Restwert für die ganze Menge ist deshalb falsch, nur der Teil, den das Haus nach dem Horizont tatsächlich braucht, ist ihn wert.
- **Plausibilität** (`grid_charge_pays`): je Slot genügt irgendeine spätere Entladung, für die sich der Spread rechnet (`pays_off`), ohne Menge. Darum nennen alle Slots „um 16:00 Uhr 38,5 ct“, obwohl um 16:00 nur 2 % entladen werden.
- **Nicht die Ursache:** Regel R1 hält der Plan ein (er lädt nur bis 99 %, damit der PV-Überschuss noch hineinpasst). Die 7 kW sind erlaubt (Leistungspreis erst ab 2027; die 3 kW von Netzgrenze je Phase gelten nur für `gr_r`/`gr_s`).
- **Übergang an der Anlage:** Binda hat am 14.11.2026 um 19:31 den Restwertfaktor auf 0,6 gestellt. Der Plan danach: rund 10 kWh Netzladen zu 25,5 ct für 15–23 Uhr (38,5 ct), Ende bei 20 %, 6,01 € gegen 6,78 €.

## Entscheidungen

1. **Grenze nur bei Netzenergie im Akku.** Die Grenze greift nur, wenn am Horizontende Netzenergie aus einem Netzladen im Horizont im Akku sein kann (`c_T` = 1 aus Regel R1). Ein Akku, den nur die PV füllt, behält den linearen Restwert wie heute, sonst würde der Plan im Sommer PV einspeisen statt speichern. Netzenergie von vor dem Horizont (`k_t`, `grid_in_battery_at_start`) zählt nicht: das Laden ist schon geschehen.
2. **Bedarf nach dem Horizont** `end_need_kwh` = `Σ max(0, Last − PV) · Δ / η_dis` über die 96 Slots `[Horizontende, +24 h)`, auf der Akkuseite (kWh SOC). Last und PV sind die P50-Prognosen, die `assemble_inputs` schon bekommt. Autos zählen nicht (ihr Bedarf nach dem Horizont ist unbekannt). Fehlt für einen Slot eine der beiden Prognosen, gilt der Slot 24 h davor, wenn es dort beide gibt. Fehlt auch der, zählt der Slot nicht; die Summe der gezählten Slots wird auf 96 Slots hochgerechnet. Weil der Horizont mindestens 4 Slots hat und das Fenster 24 h davor ihn immer trifft, zählt immer mindestens ein Slot.
3. **Über der Grenze ist der Restwert 0, weich.** Neue Variable `über ≥ 0` mit `über ≥ s_T − (Reserve + end_need_kwh) − span · (1 − c_T)` und Kosten `end_value_ct · über`. Kein Verbot, das Modell bleibt immer lösbar. `Reserve` ist `reserve_kwh(inputs)` (nicht `lo_kwh`), `span = hi_kwh − lo_kwh`.
4. **Preisbasis bleibt** Median × Restwertfaktor. Die Menge war der Fehler, nicht der Preis.
5. **Neuer Code `NETZLADEN_RESTWERT`** für einen `NETZLADEN`-Slot, dessen geladene Energie bis zum Horizontende im Akku bleibt (der SOC fällt danach nie mehr als `SOC_TOL_KWH` unter das Slotende): „Akku lädt 6,6 kW aus dem Netz: jetzt 25,5 ct, für die Zeit nach 00:00 Uhr.“ Sonst bleibt `NETZLADEN_GUENSTIG` wie heute.
6. **Plausibilität:** `pays_off_at_end` gilt nur noch, wenn der End-SOC der Nachrechnung höchstens `Reserve + end_need_kwh + SOC_TOL_KWH` beträgt (ohne `end_need_kwh`: wie heute).
7. **Ausrollen:** nur app, keine Migration, keine neue Einstellung, mit Stufe 2 (ab 23.11.2026). Gespeicherte Eingangsdaten ohne das Feld laden mit `end_need_kwh = None` (keine Grenze). Der Restwertfaktor bleibt auf 0,6, bis Binda nach dem Ausrollen entscheidet (siehe „Nach dem Ausrollen“).

## Global Constraints

- Deutsch, Commits im Stil `fix(app): …`, keine Modellbezeichnungen im Repo.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy --strict` (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators); UI: `pnpm check`, `pnpm lint`, `pnpm test`. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Der core bleibt unverändert. Kein Aktor wird geschaltet.
- `end_need_kwh: NonNegativeFinite | None = None` in `PlanInputs`; `None` heißt „keine Grenze“ (alte Eingangsdaten, Tests mit `scenario` ohne Angabe).

## Review Focus

- **Akku nur aus PV voll, Schalter an, kein Netzladen** (Sommertag): End-SOC und Einspeisung sind gleich wie ohne Grenze. Test in Task 2.
- **Netzladen nachts, PV füllt tagsüber über die Grenze, Akku wird nie leer**: Der Plan lädt nachts nur, was sich ohne Restwert über der Grenze rechnet; kein Einspeisen trotz R1. Test in Task 2.
- **Prognose endet vor oder mit dem Horizont** (Horizont endet am Ende der Lastprognose, übermorgen 24:00): Ersatz über Slot −24 h, Hochrechnung. Tests in Task 1.
- **SOC₀ unter der Reserve mit Nachladen `gr_r`** und Grenze: Nachladen bis zur Reserve bleibt, keine doppelte Strafe, die das Nachladen verhindert. Test in Task 2.
- **Text bei Netzladen, das teils am Nachmittag gebraucht wird und teils bis zum Ende bleibt**: frühe Slots (bis zum späteren Minimum des SOC) `NETZLADEN_GUENSTIG`, spätere `NETZLADEN_RESTWERT`. Test in Task 3.

---

### Task 1: Bedarf nach dem Horizont in den Eingangsdaten

**Files:**
- Modify: `src/bindaems/app/planning/inputs.py` (`PlanInputs`, neue Funktion `end_need_kwh`, `assemble_inputs`, Modul-Docstring)
- Modify: `tests/plan_helpers.py` (`scenario`: neuer Parameter `end_need_kwh: float | None = None`)
- Test: `tests/unit/app/planning/test_inputs.py`

**Interfaces:**
- Produces: `PlanInputs.end_need_kwh: NonNegativeFinite | None = None`; `end_need_kwh(pv_w: Mapping[datetime, float], load_w: Mapping[datetime, float], horizon_end: datetime, eta_dis: float) -> float` (kWh auf der Akkuseite); `scenario(..., end_need_kwh=...)`.

- [ ] **Step 1: Failing tests** in `test_inputs.py`:
  - `test_end_need_from_load_minus_pv_after_horizon`: `assemble(prices=known(NOW, slots=20) , pv=flat(NOW, 200, 0.0), load=flat(NOW, 200, 500.0))` → Horizont 20 Slots; `inputs.end_need_kwh == pytest.approx(96 * 0.5 * 0.25 / math.sqrt(0.88))`.
  - `test_end_need_counts_only_net_load`: PV 800 W, Last 500 W in allen Slots → `end_need_kwh == 0.0`; PV 300 W, Last 500 W → `96 * 0.2 * 0.25 / math.sqrt(0.88)`.
  - `test_end_need_uses_slot_a_day_earlier`: Prognosen nur bis Horizontende + 48 Slots, Horizont 96 Slots (Preise 96 Slots, Last 500 W in den ersten 96 + 48 Slots, im Horizont in den Slots 48–95 1000 W) → die 48 fehlenden Slots nehmen den Wert von 24 h vorher (1000 W): `end_need_kwh == pytest.approx((48 * 0.5 + 48 * 1.0) * 0.25 / math.sqrt(0.88))`.
  - `test_end_need_scales_up_partial_window`: Preise 16 Slots (Horizont 16), Prognosen 26 Slots, Last 500 W, PV 0 → 10 Slots direkt und 16 über den Slot −24 h (die Slots des Horizonts) gezählt, 70 fehlen → hochgerechnet `96 * 0.5 * 0.25 / math.sqrt(0.88)` (ohne Hochrechnung wären es 26 Slots).
  - `test_end_need_without_forecast_after_uses_day_before`: Preise und Prognosen enden nach 16 Slots, Last 400 W, PV 0 → die 16 Slots −24 h zählen, hochgerechnet `96 * 0.4 * 0.25 / math.sqrt(0.88)`.
  - `test_stored_inputs_without_end_need_load_as_none`: `PlanInputs.model_validate(data)` mit `data` aus `sample_inputs().model_dump(mode="json")` ohne den Schlüssel → `end_need_kwh is None`.
- [ ] **Step 2:** `pytest tests/unit/app/planning/test_inputs.py -q` → FAIL (Feld/Funktion fehlt).
- [ ] **Step 3:** Feld mit Kommentar „Energie (kWh auf der Akkuseite), die das Haus in den 24 h nach dem Horizont aus dem Akku braucht (Plan Netzladen auf Vorrat, Entscheidung 2); alte Eingangsdaten: keine Grenze“. Funktion `end_need_kwh` nach den Regeln von Entscheidung 2 (Fenster `END_WINDOW`, Slotfolge ab `horizon_end` in Schritten `SLOT`, Ersatz aus `pv_w`/`load_w` bei `t − 24 h`). In `assemble_inputs`: `end_need_kwh=end_need_kwh(pv_w, load_w, horizon_end, eta(planning.roundtrip_pct))`. `scenario` reicht `end_need_kwh` durch. Docstring der Moduls um einen Absatz ergänzen.
- [ ] **Step 4:** Tests grün; dann `pytest tests/unit/app/planning -q` (bestehende Tests mit `assemble` bekommen jetzt eine Grenze; ein Test, der davon kippt, ist ein Fund für Task 2 und wird dort angesehen, nicht hier angepasst).
- [ ] **Step 5: Commit** `feat(app): Bedarf des Hauses nach dem Horizont in den Eingangsdaten der Planung`

### Task 2: Optimierer zieht den Restwert über dem Bedarf ab

**Files:**
- Modify: `src/bindaems/app/planning/optimizer.py` (`_solve` nach dem Block Regel R1; Modul-Docstring neuer Absatz „Netzladen auf Vorrat“)
- Test: `tests/unit/app/planning/test_optimizer.py` (neuer Abschnitt `# --- Netzladen auf Vorrat (Plan Netzladen auf Vorrat) ---`)

**Interfaces:**
- Consumes: `PlanInputs.end_need_kwh`, `scenario(end_need_kwh=...)` (Task 1); `reserve_kwh` (`battery_model.py`); `in_battery` aus dem R1-Block.
- Produces: im Modell die Variable `ueber_restwert` und Bedingung `restwert_grenze`; sonst keine neue Schnittstelle.

- [ ] **Step 1: Failing tests** (Akku `state(usable_kwh=80.0)`, Reserve 20 % = 16 kWh, Start `2026-11-14T18:15:00+00:00`, 115 Slots, Preise wie in `Befund` je Ortszeit: 19–24 Uhr 36 ct, 0–1 Uhr 30, 1–7 Uhr 25,5, 7–13 Uhr 27, 13–16 Uhr 30, 16–17 Uhr 38,5, 17–21 Uhr 33, 21–22 Uhr 38,5, 22–24 Uhr 32; Last 1400 W; PV 0, außer am 15.11. 9–15 Uhr `3000 · sin(π · (h − 9) / 6)`; SOC₀ 23 %, Schalter an; Hilfsfunktion `_vorrat_scenario(end_value_ct: float, end_need_kwh: float | None)` im Test):
  - `test_grid_charge_for_end_value_stops_at_need`: `end_value_ct=32.0, end_need_kwh=20.0` → `result.flows[-1].soc_end_kwh <= 16.0 + 20.0 + 0.1`.
  - `test_without_need_keeps_old_behaviour`: `end_value_ct=32.0, end_need_kwh=None` → `result.flows[-1].soc_end_kwh >= 0.95 * 80` (heutiges Verhalten, schützt `None`).
  - `test_need_does_not_touch_pv_only_battery`: Schalter an, 96 Slots, PV-Bogen mit `pv_arc` (Spitze 8 kW) über die Slots 32–63, Last 500 W, Einspeisepreis 7 ct, Bezug überall 30 ct (Netzladen rechnet sich nirgends), SOC₀ 30 %, `end_value_ct=25.0`, `end_need_kwh=5.0` gegen `None` → `soc_end_kwh` und `Σ pv_x` beider Läufe gleich (auf 0,01 kWh).
  - `test_night_charge_with_pv_above_need_has_no_export`: Schalter an, Nacht 20 ct, Tag PV 6 kW über 6 h, Abend 40 ct, Last 500 W, `end_value_ct=30.0`, `end_need_kwh=5.0` → Lösung `optimal`, keine Einspeisung in einem Slot mit `c_t` (geprüft über die Nachrechnung wie die R1-Tests: `export_while_charged_kwh(...) <= R1_TOL_KWH` für jeden `NETZLADEN`-Slot), und `Σ gr_b · Δ` kleiner als derselbe Lauf mit `end_need_kwh=None`.
  - `test_recharge_to_reserve_unaffected_by_need`: SOC₀ 10 %, Schalter an, `end_value_ct=30.0`, `end_need_kwh=0.0` → `soc_end_kwh >= 16.0 - 0.01` und `Σ gr_r > 0` (Nachladen bleibt).
- [ ] **Step 2:** `pytest tests/unit/app/planning/test_optimizer.py -k "vorrat or need" -q` → die drei ersten mit Grenze FAIL (End-SOC bei ~98 %), die beiden Schutztests dürfen schon grün sein.
- [ ] **Step 3:** In `_solve`, wenn `z` und `inputs.end_need_kwh is not None`: Variable `ueber_restwert ≥ 0`, Bedingung `ueber_restwert ≥ soc[-1] − (reserve + end_need_kwh) − span · (1 − in_battery[count − 1])` (Name `restwert_grenze`), Ziel `+ end_value_ct · ueber_restwert`. `span` wie im R1-Block (`hi_kwh − lo_kwh`); `reserve` ist die schon vorhandene Reserve in kWh. Docstring-Absatz mit Formel und Entscheidungen 1 und 3.
- [ ] **Step 4:** Neue Tests grün; dann `pytest tests/unit/app/planning tests/property -q`. Kippt ein bestehender Test, weil `assemble` jetzt eine Grenze setzt: prüfen, ob das neue Verhalten physikalisch richtig ist (dann den Test mit Begründung im Ledger anpassen oder `end_need_kwh=None` in der Eingabe setzen, wenn der Test den Restwert selbst prüft), sonst Fehler im Code.
- [ ] **Step 5: Commit** `fix(app): kein Netzladen auf Vorrat über den Bedarf nach dem Horizont`

### Task 3: Plausibilität und Text des Slots

**Files:**
- Modify: `src/bindaems/app/planning/plausibility.py` (`pays_off_at_end`)
- Modify: `src/bindaems/app/planning/explain.py` (`_SENTENCES`, `_main_reason` Regel 3, Docstring)
- Modify: `src/bindaems/shared/plan.py` (`ReasonCode`: `"NETZLADEN_RESTWERT"` nach `"NETZLADEN_GUENSTIG"`)
- Modify: `ui/src/lib/api/schemas.ts` (Liste der Codes, gleiche Stelle)
- Test: `tests/unit/app/planning/test_plausibility.py`, `tests/unit/app/planning/test_explain.py`; UI ohne eigenen Test (nur die Liste der Codes, `pnpm check`)

**Interfaces:**
- Consumes: `PlanInputs.end_need_kwh` (Task 1), `reserve_kwh`, `SOC_TOL_KWH`.
- Produces: `Reason(code="NETZLADEN_RESTWERT", params={"kw": float, "ct": float, "um": "HH:MM"})`; Satz `"Akku lädt {kw} kW aus dem Netz: jetzt {ct} ct, für die Zeit nach {um} Uhr."` (Zahlen wie bei `NETZLADEN_GUENSTIG` mit `_de(…, 1)`).

- [ ] **Step 1: Failing tests:**
  - `test_pays_off_at_end_only_up_to_need` (Plausibilität): Nachrechnung mit End-SOC = Reserve + Bedarf + 1 kWh und `end_value_ct` hoch genug → `pays_off_at_end(...) is False`; End-SOC = Reserve + Bedarf → `True`; `end_need_kwh=None` → `True` (wie heute).
  - `test_text_netzladen_restwert` (in der Tabelle der Sätze in `test_explain.py`, Zeile ~51 und ~124): `{"kw": 6.6, "ct": 25.5, "um": "00:00"}` → `"Akku lädt 6,6 kW aus dem Netz: jetzt 25,5 ct, für die Zeit nach 00:00 Uhr."`.
  - `test_grid_charge_kept_to_end_explains_end_value`: Plan mit `NETZLADEN` in Slot 2 und 3, SOC danach nie unter dem Ende von Slot 3 → Slot 3 hat `NETZLADEN_RESTWERT` mit `um` = Horizontende (lokal).
  - `test_grid_charge_used_later_keeps_guenstig`: wie oben, aber in Slot 6 entlädt der Akku unter das Ende von Slot 2 → Slot 2 bleibt `NETZLADEN_GUENSTIG` (Review Focus, gemischter Fall).
- [ ] **Step 2:** Tests → FAIL.
- [ ] **Step 3:** `pays_off_at_end`: zusätzliche Bedingung aus Entscheidung 6. `_main_reason`, Regel 3: vor der Suche nach `_first_paying_discharge` prüfen, ob `min(steps[j].soc_end_kwh for j > index) ≥ steps[index].soc_end_kwh − SOC_TOL_KWH` (im letzten Slot: wahr); dann `NETZLADEN_RESTWERT` mit `um` = `horizon_end`. Code in `ReasonCode` und `schemas.ts`.
- [ ] **Step 4:** Tests grün, dazu `pytest tests/unit/app/planning tests/unit/test_docs.py -q` und `cd ui && pnpm check && pnpm test` (der Docs-Test schlägt fehl, bis Task 4 die Tabelle nachführt: das ist erwartet und wird in Task 4 grün).
- [ ] **Step 5: Commit** `fix(app): Netzladen für den Restwert prüfen und benennen`

### Task 4: Doku, Spec und Übergabe

**Files:**
- Modify: `docs/betrieb.md` (Tabelle der Begründungen, Zeile ~6893: neue Zeile `NETZLADEN_RESTWERT` mit Beispielsatz und „Netzladen, das bis zum Horizontende im Akku bleibt“; Abschnitt „Planung“, Restwert: ein Absatz zur Grenze mit Bedarf und Entscheidung 1)
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (neuer Absatz „Stand Plan Netzladen auf Vorrat (14.11.2026)“ beim Restwert, Zeile ~398, mit den Entscheidungen 1 bis 3, 5 und 6; Liste der Codes, Zeile ~1756)
- Modify: `docs/uebergabe.md` (Kopfblock: Plan auf `main`, nicht ausgerollt, Punkt 29; unter „Offen an der Anlage“ neuer Punkt 29 mit „nur app, keine Migration, Stufe 2“ und „Nach dem Ausrollen“ unten; Rulings und aufgeschobene Kleinigkeiten nach dem Gesamtreview)
- Test: `tests/unit/test_docs.py` (Code `NETZLADEN_RESTWERT` mit Beispielparametern `{"kw": 6.6, "ct": 25.5, "um": "00:00"}` in beiden Listen, Zeile ~999 und ~1035)

- [ ] **Step 1:** `test_docs.py` ergänzen → FAIL.
- [ ] **Step 2:** Doku wie oben; Satz in der Tabelle genau wie `slot_text` ihn erzeugt.
- [ ] **Step 3:** Ganze Prüfkette (Global Constraints) grün.
- [ ] **Step 4: Commit** `docs: Netzladen auf Vorrat (Restwert nur für den Bedarf)`

## Nach dem Ausrollen (Binda)

1. Einen Abend mit hohen geschätzten Preisen nach dem Horizont abwarten (Samstag ist der typische Fall). Im Zeitplan sollte das Netzladen bei Faktor 0,6 gleich bleiben wie jetzt.
2. Dann den Restwertfaktor probeweise auf 0,9 zurück und „Neu planen“: Der Plan darf jetzt höchstens bis etwa `Reserve + Bedarf des nächsten Tages` laden, nicht mehr bis 100 %, und die Slots heißen „für die Zeit nach 00:00 Uhr“. Wenn dir das noch zu viel ist, bleib bei 0,6 bis 0,75. Das ist eine Frage der Vorsicht bei Schätzpreisen, nicht mehr ein Fehler.

## Aufgeschoben (nicht in diesem Plan)

- Vorheizen/Vorkühlen (`thermal_shift`) bewertet Akkuenergie am Ende weiter mit `end_value_ct`, auch über der Grenze. 5c ist nicht ausgerollt; Wirkung klein.
- Die Spread-Regel der Plausibilität (`pays_off`) prüft weiter nur, ob es eine spätere teure Entladung gibt, nicht die Menge. Mit der Grenze im Optimierer und dem neuen Text ist das kein Fehler mehr im Plan, nur eine schwache Prüfung.

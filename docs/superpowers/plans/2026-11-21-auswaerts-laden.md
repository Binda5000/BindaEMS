# Auswärts laden im Fahrverhalten Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Das Fahrverhalten (Plan 5b) erkennt Fahrten, auf denen das Auto auswärts geladen hat. „Verbrauch“ zeigt dann den echten Fahrverbrauch (nie negativ), und der Vorschlag Ziel rechnet mit dem tiefsten Stand unterwegs statt mit „Abfahrt minus Rückkehr“.

**Architecture:** Nur app und UI. `driving_days.py` wertet für jeden Fahrtag die Ladestände zwischen Abfahrt und Rückkehr aus (die Messung `soc` liegt schon im 5-min-Raster vor) und liefert drei neue Größen je Tag. `driving_profile.py` rechnet das Ziel aus dem P80 des tiefsten Stands, `driving_quality.py` bewertet das Ziel damit. Die Karte „Fahrverhalten“ zeigt den Anteil der Fahrten mit Laden auswärts. Der Planer bleibt, wie er ist.

**Tech Stack:** Python 3.12, pydantic, pytest; SvelteKit, valibot, Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, 10.4 „Stand Plan 5b“ (Zeile ~1880, „Tage“, „Vorschläge“, „Trefferquote“, „Nicht in 5b: … Laden außer Haus als eigene Größe“), 13 „Stand Plan 5b“ (Zeile ~2239). Neuer Absatz „Stand Plan Auswärts laden“ kommt mit Task 4.

## Befund

- **Anlass (21.11.2026):** Auf **Prognosen → Fahrverhalten** zeigt der Tesla in der Spalte „Verbrauch“ negative Werte (Mo −14 %, Mi −33 %, Fr −25 %). Binda: Der Tesla lädt zu etwa 90 % auswärts.
- **Ursache** (`driving_days._record`): `consumption_pp = departure_soc − return_soc`. Lädt das Auto unterwegs mehr nach, als es verfährt, wird der Wert negativ. Die Spalte zeigt das P80 davon (`DrivingCard.svelte`, `consumption_p80_pp`). Der Plan 5b hat das bewusst so gelassen („kann negativ sein, wenn unterwegs geladen wurde“, „Laden außer Haus als eigene Größe“ ist „Nicht in 5b“).
- **Folge für das Ziel:** `_target` nimmt `max(0, P80)`, also nur den Puffer (20 %), geklemmt auf „Ziel-SOC mindestens“ (50 %). Beim Tesla steht das Ziel darum fest auf 50 % „(begrenzt)“, unabhängig davon, wie weit er bis zur Ladestelle fährt. An einem Tag ohne Laden unterwegs kann das zu wenig sein.
- **Folge für die Trefferquote** (`driving_quality.hit_rates`): Das Ziel gilt als getroffen, wenn `Ziel − Verbrauch ≥ Puffer`. Mit negativem Verbrauch ist das fast immer wahr. Die Trefferquote „Ziel“ des Tesla in der Abnahme 5b (ab 23.11.2026) sagt darum wenig.
- **Nicht falsch ist der Planer:** Für ein angestecktes Auto rechnet er mit dem gemessenen Ladestand. Für ein erwartetes Auto (`planning/vehicles.expected_vehicles`) nimmt er `arrival_soc_p50`, den Median des Ladestands bei der Ankunft. Der enthält das Laden unterwegs schon richtig: Kommt der Tesla meist mit 70 % heim, ist der Bedarf daheim klein. Der Ladebedarf daheim ist also nicht unterschätzt; verzerrt sind die Anzeige „Verbrauch“, die Grundlage des Ziels und seine Trefferquote.
- **Daten sind da:** Der core schreibt `vehicle.<Auto>.soc_pct` in die Messung `soc` (`core/telemetry/sampler.py`), auch unterwegs. Tessie fragt unterwegs alle 600 s ab, beim Laden alle 30 s (`core/adapters/tessie.poll_interval_s`). `driving_data._soc` liest die Werte im 5-min-Raster schon für das ganze Fenster, also auch zwischen Abfahrt und Rückkehr; heute nutzt `driving_days` davon nur den letzten Wert vor der Abfahrt und den ersten nach der Rückkehr.

## Optionen

**A – Laden unterwegs erkennen, Ziel aus dem tiefsten Stand (empfohlen, dieser Plan).** Je Fahrt wird aus den Ladeständen unterwegs erkannt, ob und wie viel nachgeladen wurde. „Verbrauch“ wird der Fahrverbrauch der ganzen Strecke (Abfahrt minus Rückkehr plus das Nachgeladene). Das Ziel deckt den **tiefsten Stand unterwegs**: Ziel = Puffer + P80 von „Abfahrt minus tiefster Stand“. Beim Tesla ist das die Strecke bis zur Ladestelle, ohne Laden unterwegs die ganze Fahrt wie heute. Ein Auto, das nie auswärts lädt (e-Golf), rechnet fast genau wie bisher. Kein Schalter, nichts einzutragen.

**B – Fahrten mit Laden unterwegs beim Ziel weglassen.** Erkennung wie A, aber das Ziel rechnet nur mit Fahrten ohne Laden unterwegs (Verbrauch der ganzen Strecke). Beim Tesla blieben so etwa 10 % der Fahrten, also in 8 Wochen eine Handvoll; die Konfidenz bliebe meist „niedrig“, die Automatik übernähme das Ziel kaum, und wenn, wäre es höher als nötig (mehr Laden daheim, obwohl er auswärts lädt).

**C – Einstellung je Auto „lädt meist auswärts“.** Ohne Erkennung: Ist sie an, gibt es für das Auto keinen Verbrauch und das Ziel ist immer „Ziel-SOC mindestens“. Einfach, aber von Hand zu pflegen, und das Ziel kennt die Strecke bis zur Ladestelle nicht. Das ist fast der Zustand von heute, nur ehrlich beschriftet.

**Warum nicht „Bedarf daheim als erwarteter Ankunfts-SOC“:** Das macht der Planer für erwartete Autos schon (`arrival_soc_p50`, siehe Befund). Was fehlt, ist nur die richtige Grundlage für das Ziel bei der Abfahrt.

## Entscheidungen (vom Betreiber am 21.11.2026 um 17:02 freigegeben: Variante A)

1. **Option A.**
2. **Laden unterwegs** ist ein Anstieg des Ladestands um **mindestens 3 Prozentpunkte** zwischen Abfahrt und Rückkehr (`AWAY_CHARGE_MIN_PP = 3.0`). Gezählt wird je zusammenhängendem Anstieg (aufeinanderfolgende Werte, die nicht fallen); kleinere Anstiege sind Messrauschen (Kälte, Rundung) und zählen nicht.
3. **Zwischenstopp zu Hause:** Ein Anstieg, der einen Aufenthalt zu Hause berührt (das Auto war mittags daheim und hat dort geladen), ist **kein** Laden auswärts. Er geht in den Verbrauch ein (sonst wäre der zu klein), aber nicht in den Anteil „auswärts geladen“.
4. **Drei Größen je Fahrtag** (`DayRecord`): `consumption_pp` (Fahrverbrauch: Abfahrt − Rückkehr + alle gezählten Anstiege), `charged_away_pp` (Summe der Anstiege auswärts, 0 ohne) und `drawdown_pp` (Abfahrt minus tiefster Stand unterwegs einschließlich Rückkehr, mindestens 0). Dazu bleibt `net_pp` (Abfahrt − Rückkehr, der bisherige Wert). Fehlt der Ladestand bei Abfahrt oder Rückkehr, sind alle vier `None` wie heute.
5. **Unbekannt bleibt unbekannt:** Beginnt das erste Laden auswärts schon mit dem ersten Schritt nach der Abfahrt (kein Messwert zwischen Abfahrt und Ladebeginn, etwa weil das Auto schlief), ist `drawdown_pp` `None`: Wie tief der Stand bis zur Ladestelle fiel, ist nicht bekannt. Verbrauch und `charged_away_pp` gibt es trotzdem.
6. **Ziel** = `clamp(ceil5(Puffer + max(0, P80(drawdown_pp))), min, max)`; die Konfidenz des Ziels rechnet mit Anzahl und Streuung der `drawdown_pp` (Schwellen wie bisher). `WeekdayProfile` bekommt `drawdown_p80_pp` und `target_basis` (`"net"` für gespeicherte Stände von vor diesem Plan, `"drawdown"` für neue). `with_settings` rechnet bei `"drawdown"` aus `drawdown_p80_pp` (fehlt er, kein Ziel), bei `"net"` wie bisher aus `consumption_p80_pp`. So bleibt die Trefferquote über alte Stände richtig.
7. **Trefferquote Ziel:** getroffen, wenn `Ziel − drawdown_pp ≥ Puffer` (der tiefste Stand blieb über dem Puffer); Tage ohne `drawdown_pp` zählen nicht.
8. **Anzeige:** „Verbrauch“ zeigt weiter das P80, jetzt des Fahrverbrauchs (nie negativ). Der Kopf je Auto bekommt „· auswärts geladen an {a} von {n} Fahrten“ (`n` = Fahrtage mit bekanntem `charged_away_pp` im Fenster, `a` davon mit `charged_away_pp > 0`; ohne `n` fehlt der Zusatz). Ab einem Anteil von 50 % steht unter der Tabelle: „Lädt meist auswärts: Der Vorschlag Ziel reicht bis zum tiefsten Stand unterwegs, nicht für die ganze Strecke.“ Keine neue Spalte (die Tabelle ist am Handy schon lang).
9. **Planer unverändert:** Erwartete Autos behalten `arrival_soc_p50`. Ein Auto, das an einem Tag ohne Laden auswärts tiefer heimkommt, korrigiert der Plan beim Anstecken mit dem gemessenen Ladestand.
10. **Ausrollen:** nur app und UI, keine Migration (das Profil ist JSON, neue Felder haben Standardwerte), keine neue Einstellung, keine Änderung an `config.yaml`; der core bleibt unverändert. Frühestens mit Stufe 2, sonst mit dem nächsten Pull danach. Home Assistant ändert sich nur im Wert von „Vorschlag Ziel-SOC“.

## Global Constraints

- Deutsch, du; Commits im Stil `feat(app): …`, `feat(ui): …`, `docs: …`; keine Modellbezeichnungen im Repo.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy --strict` (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators); UI: `pnpm check`, `pnpm lint`, `pnpm test`. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Der core bleibt unverändert. Kein Aktor wird geschaltet. Gelernte Werte bleiben Eingaben der Planung (Spec 10.5).
- Gespeicherte Profile (`driving_profile.profile`, JSON) von vor diesem Plan laden ohne Fehler: `drawdown_p80_pp = None`, `target_basis = "net"`, `away_trips = 0`, `charge_known = 0`.

## Review Focus

- **Rauschen unterwegs** (Tesla steht kalt, Ladestand springt 61 → 62 → 61 → 62): kein Laden auswärts, Verbrauch gleich Abfahrt − Rückkehr. Test in Task 1 (`test_small_rises_are_noise`).
- **Mittags daheim geladen** (Zwischenstopp zu Hause 12:00–13:00, dort von 40 auf 70 %): `charged_away_pp == 0`, Verbrauch enthält die 30 pp, der Sprung über den Aufenthalt zählt nicht als auswärts. Test in Task 1 (`test_charge_during_home_stop_is_not_away`).
- **Auto schläft bis zur Ladestelle** (nach der Abfahrt mit 50 % der erste Wert unterwegs ist schon 75 % beim Laden): `drawdown_pp is None`, `charged_away_pp` und Verbrauch bekannt. Test in Task 1 (`test_drawdown_unknown_without_point_before_charge`).
- **Gespeicherter Stand von vor dem Plan** (JSON ohne die neuen Felder) in `with_settings` und `hit_rates`: Ziel wie bisher aus `consumption_p80_pp`, Trefferquote gegen `drawdown_pp`. Test in Task 2 (`test_old_snapshot_keeps_net_target`).
- **Woche mit fast nur Laden auswärts** (alle `drawdown_pp` bekannt und klein): Ziel = „Ziel-SOC mindestens“ mit `target_clamped`, Konfidenz aus den Tiefstständen, Kopf „auswärts geladen an 9 von 10 Fahrten“ und der Hinweissatz. Tests in Task 2 und Task 3.

---

### Task 1: Laden unterwegs in den Tagesdatensätzen

**Files:**
- Modify: `src/bindaems/app/learning/driving_days.py` (`DayRecord`, `_record`, neue Funktion `_trip_soc`, Konstante, Modul-Docstring)
- Modify: `tests/ui_world.py` (`_trip`, `_stay`: neue Felder; der Tesla der Demo-Welt lädt an zwei Tagen auswärts)
- Test: `tests/unit/app/learning/test_driving_days.py`

**Interfaces:**
- Produces:
  - `AWAY_CHARGE_MIN_PP: Final = 3.0`
  - `DayRecord` mit den neuen Feldern **am Ende**, alle mit Standard `None`: `net_pp: float | None`, `charged_away_pp: float | None`, `drawdown_pp: float | None`. `consumption_pp` bleibt an seiner Stelle, heißt aber jetzt Fahrverbrauch (Entscheidung 4).
  - `@dataclass(frozen=True) class TripSoc: consumption_pp: float; charged_away_pp: float; drawdown_pp: float | None`
  - `_trip_soc(soc: list[tuple[datetime, float]], stays: list[Stay], departure: datetime, departure_soc: float, return_at: datetime, return_soc: float) -> TripSoc`

- [ ] **Step 1: Write the failing tests** (Helfer `history`, `pt`, `at`, `record` aus der Datei)

```python
def test_away_charge_is_detected() -> None:
    hist = history(
        pt(MON, "07:20", False),
        pt(MON, "17:45", True),
        soc=[(at(MON, "07:10"), 40.0), (at(MON, "08:00"), 30.0),
             (at(MON, "08:30"), 45.0), (at(MON, "09:00"), 75.0),
             (at(MON, "17:50"), 55.0)],
    )
    rec = record(hist, MON)
    assert rec.net_pp == -15.0
    assert rec.charged_away_pp == 45.0
    assert rec.consumption_pp == 30.0
    assert rec.drawdown_pp == 10.0

def test_small_rises_are_noise() -> None:
    # 70 → 61 → 62 → 61 → 62 → 60: Anstiege von 1 pp zählen nicht
    ...
    assert rec.charged_away_pp == 0.0
    assert rec.consumption_pp == rec.net_pp == 10.0
    assert rec.drawdown_pp == 10.0

def test_charge_during_home_stop_is_not_away() -> None:
    # Fahrt 07:30, daheim 12:00–13:00 (Ladestand 12:05 40, 12:55 70), zurück 18:00 mit 50
    ...
    assert rec.charged_away_pp == 0.0
    assert rec.consumption_pp == 50.0   # 70 − 50 + 30
    assert rec.drawdown_pp == 30.0      # Abfahrt 70, tiefster Stand 40

def test_drawdown_unknown_without_point_before_charge() -> None:
    # Abfahrt 50, erster Wert unterwegs 75, zurück 60
    ...
    assert rec.charged_away_pp == 25.0
    assert rec.consumption_pp == 15.0
    assert rec.drawdown_pp is None

def test_away_charge_split_by_drop_counts_both_runs() -> None:
    # 60 → 40 → 50 → 45 → 70 → 55: zwei Anstiege (10 und 25), beide ≥ 3
    ...
    assert rec.charged_away_pp == 35.0
    assert rec.consumption_pp == 40.0
    assert rec.drawdown_pp == 20.0
```

`test_consumption_can_be_negative` wird zu `test_net_can_be_negative`: dieselben Daten (40 → 55 ohne Werte unterwegs) ergeben `net_pp == -15.0`, `charged_away_pp == 15.0`, `consumption_pp == 0.0`, `drawdown_pp is None`. Die übrigen Tests der Datei bleiben unverändert grün (ohne Anstieg ist `consumption_pp` gleich wie bisher).

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_driving_days.py -q`
Expected: FAIL (`DayRecord` hat kein `net_pp`)

- [ ] **Step 3: Implement `_trip_soc` und die Felder in `_record`**

Punkte: `(departure, departure_soc)`, alle `soc`-Werte mit `departure < ts < return_at`, `(return_at, return_soc)`. Ein Schritt `i → i+1` berührt einen Aufenthalt zu Hause, wenn ein `stay` mit `start < t[i+1]` und `end > t[i]` existiert (der Aufenthalt ab `return_at` berührt den letzten Schritt nicht). Ein Anstieg ist eine Folge von Schritten mit `Δ ≥ 0`, die ein `Δ < 0` beendet; seine Höhe ist die Summe der `Δ`. Ein Anstieg zählt ab `AWAY_CHARGE_MIN_PP`; er ist „zu Hause“, wenn einer seiner Schritte einen Aufenthalt berührt, sonst „auswärts“. `consumption_pp = net + Summe aller gezählten Anstiege`, `charged_away_pp = Summe der auswärtigen`, `drawdown_pp = max(0, departure_soc − min(Werte))`, aber `None`, wenn der erste gezählte auswärtige Anstieg mit dem ersten Schritt beginnt. In `_record` nur rufen, wenn `departure_soc` und `return_soc` bekannt sind; `net_pp` ist der bisherige Wert.

- [ ] **Step 4: `tests/ui_world.py` nachziehen**

`_trip` bekommt `net_pp=used_pp`, `charged_away_pp=0.0`, `drawdown_pp=used_pp`; zwei Fahrtage des Tesla bekommen `charged_away_pp=20.0`, `consumption_pp=used_pp + 20`, `drawdown_pp=used_pp`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/bindaems/app/learning/driving_days.py tests/unit/app/learning/test_driving_days.py tests/ui_world.py
git commit -m "feat(app): Laden unterwegs in den Tagesdatensätzen des Fahrverhaltens erkennen"
```

### Task 2: Ziel und Trefferquote aus dem tiefsten Stand

**Files:**
- Modify: `src/bindaems/app/learning/driving_profile.py` (`WeekdayProfile`, `VehicleProfile`, `_weekday_profile`, `with_settings`, `build_profile`)
- Modify: `src/bindaems/app/learning/driving_quality.py` (`hit_rates`)
- Test: `tests/unit/app/learning/test_driving_profile.py`, `tests/unit/app/learning/test_driving_quality.py`

**Interfaces:**
- Consumes: `DayRecord.consumption_pp`, `.charged_away_pp`, `.drawdown_pp` (Task 1)
- Produces:
  - `WeekdayProfile.drawdown_p80_pp: float | None = None`, `WeekdayProfile.target_basis: Literal["net", "drawdown"] = "net"`
  - `VehicleProfile.away_trips: int = 0`, `VehicleProfile.charge_known: int = 0` (über alle Tagesdatensätze, nicht je Gruppe)
  - `consumption_p50_pp`/`consumption_p80_pp` jetzt aus dem Fahrverbrauch

- [ ] **Step 1: Write the failing tests**

```python
def test_target_uses_drawdown_not_net() -> None:
    # fünf Montage: net −20, drawdown 12, 15, 10, 14, 13 → P80 14,2 → 20 + 14,2 → 35 → geklemmt 50
    wp = build(...).weekdays[0]
    assert wp.target_basis == "drawdown"
    assert wp.drawdown_p80_pp == pytest.approx(14.2)
    assert (wp.target_soc_pct, wp.target_clamped) == (50, True)
    assert wp.consumption_p80_pp > 0

def test_drawdown_none_days_do_not_count_for_target() -> None:
    # drei Tage nur mit drawdown None → kein Ziel, keine Konfidenz des Ziels
    assert wp.target_soc_pct is None and wp.target_confidence is None

def test_vehicle_counts_away_trips() -> None:
    # 10 Fahrtage, 9 mit charged_away_pp > 0, ein Tag mit None
    assert (profile.away_trips, profile.charge_known) == (9, 10)  # der None-Tag zählt nicht

def test_old_snapshot_keeps_net_target() -> None:
    old = VehicleProfile.model_validate(json_without_new_fields)
    assert old.weekdays[0].target_basis == "net"
    assert with_settings(old, settings).weekdays[0].target_soc_pct == <wie bisher aus consumption_p80_pp>

def test_with_settings_drawdown_basis_without_p80_has_no_target() -> None: ...
```

In `test_driving_quality.py`: `test_target_hit_uses_drawdown` (Ziel 50, Puffer 20, `drawdown_pp` 25 → Treffer, 35 → kein Treffer) und `test_target_day_without_drawdown_is_not_rated` (`target_n` bleibt 0). Der Helfer `record(...)` dort bekommt `drawdown: float | None` dazu.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_driving_profile.py tests/unit/app/learning/test_driving_quality.py -q`
Expected: FAIL

- [ ] **Step 3: Implement**

`_weekday_profile`: Liste `drawdown` aus `r.drawdown_pp`; Ziel und Konfidenz des Ziels daraus (`_target(drawdown_p80, settings)`, `_level(len(drawdown), _spread(drawdown), …)`), `target_basis="drawdown"`. `with_settings`: bei `"drawdown"` aus `drawdown_p80_pp` (ohne ihn Ziel `None`, `target_clamped` `False`), bei `"net"` der bisherige Weg. `hit_rates`: `record.drawdown_pp` statt `record.consumption_pp`. Docstrings nachziehen (Modul `driving_profile`, `_target`, `hit_rates`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/app/learning tests/unit/app/planning/test_vehicles.py tests/unit/app/ha/test_entities.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(app): Ziel des Fahrverhaltens aus dem tiefsten Stand unterwegs"
```

### Task 3: API und Karte „Fahrverhalten“

**Files:**
- Modify: `src/bindaems/app/learning/driving_service.py` (`_weekday_json`, `_vehicle_json`)
- Modify: `ui/src/lib/api/contract/learning-driving.json`, `ui/src/lib/api/schemas.ts` (`DrivingWeekdaySchema`, Schema des Autos)
- Modify: `ui/src/lib/forecast/DrivingCard.svelte`
- Test: `tests/unit/app/learning/test_driving_service.py`, `ui/src/lib/forecast/DrivingCard.test.ts`, `ui/src/lib/api/contract.test.ts` (läuft über die Vertragsdatei), `ui/tests/e2e/prognosen.spec.ts`

**Interfaces:**
- Consumes: `WeekdayProfile.drawdown_p80_pp`, `VehicleProfile.away_trips`, `.charge_known` (Task 2)
- Produces: JSON je Wochentag `"drawdown_p80_pp"`, je Auto `"away_trips"`, `"charge_known"` (bei einem Auto ohne Profil `0` und `0`)

- [ ] **Step 1: Write the failing tests**

Python: `test_vehicle_json_has_away_counts` (Profil mit `away_trips=9`, `charge_known=10` → JSON trägt beide; Wochentag trägt `drawdown_p80_pp`). Vitest in `DrivingCard.test.ts`:

```ts
it('nennt im Kopf die Fahrten mit Laden auswärts und ab 50 % den Hinweis', () => {
	// Tesla: away_trips 9, charge_known 10
	expect(screen.getByRole('heading', { name: /auswärts geladen an 9 von 10 Fahrten/ })).toBeVisible();
	expect(screen.getByText('Lädt meist auswärts: Der Vorschlag Ziel reicht bis zum tiefsten Stand unterwegs, nicht für die ganze Strecke.')).toBeVisible();
});
it('ohne bekannte Fahrten kein Zusatz und kein Hinweis', () => { /* charge_known 0 */ });
it('unter 50 % nur der Zusatz im Kopf', () => { /* 2 von 10 */ });
```

E2E (`prognosen.spec.ts`, Test „Gast sieht das Fahrverhalten beider Autos …“): Der Tesla der Demo-Welt zeigt „auswärts geladen an 2 von …“, ohne Hinweissatz.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/app/learning/test_driving_service.py -q` und `cd ui && pnpm vitest run src/lib/forecast src/lib/api`
Expected: FAIL

- [ ] **Step 3: Implement**

Kopf in `head(car)`: `… · {days} Tage` plus `· auswärts geladen an {away_trips} von {charge_known} Fahrten`, nur wenn `charge_known > 0`. Hinweis als `<p class="muted">` unter der Tabelle, wenn `away_trips * 2 >= charge_known` und `charge_known > 0`. Die Spalte „Verbrauch“ bleibt (`consumption_p80_pp`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ui && pnpm check && pnpm lint && pnpm test`, dann `pnpm exec playwright test tests/e2e/prognosen.spec.ts --trace off`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(ui): Fahrverhalten nennt Laden auswärts"
```

### Task 4: Spec, Betrieb, Abnahme, Übergabe

**Files:**
- Modify: `docs/superpowers/specs/2026-10-08-bindaems-design.md` (10.4 und 13: Absatz „Stand Plan Auswärts laden“; „Nicht in 5b: … Laden außer Haus“ verweist darauf)
- Modify: `docs/betrieb.md` („Fahrverhalten (Phase 5b)“: Verbrauch, Ziel, Kopfzeile, Hinweis)
- Modify: `docs/verification/abnahme-phase-5b.md` (Satz: Die Trefferquote „Ziel“ eines Autos, das meist auswärts lädt, war vor diesem Plan wenig aussagekräftig; nach dem Ausrollen gilt der tiefste Stand)
- Modify: `docs/uebergabe.md` (Kopfblock: Plan umgesetzt, nicht ausgerollt; Punkt 30 unter „Offen an der Anlage“: nur app und UI, keine Migration, mit oder nach Stufe 2; nach dem Ausrollen „Jetzt neu berechnen“ auf **Prognosen**, dann Kopf des Tesla prüfen)

- [ ] **Step 1: Texte schreiben** (Entscheidungen 1 bis 10 in Spec-Sprache; keine Code-Blöcke)
- [ ] **Step 2: Prüfkette komplett** (Global Constraints), Ergebnis im Ledger
- [ ] **Step 3: Commit**

```bash
git commit -am "docs: Auswärts laden im Fahrverhalten"
```

## Nach dem Ausrollen

- Auf **Prognosen** „Jetzt neu berechnen“ (Admin). Erwartet beim Tesla: „Verbrauch“ positiv, Kopf „auswärts geladen an etwa 9 von 10 Fahrten“, der Hinweissatz, Vorschlag Ziel aus dem tiefsten Stand (wahrscheinlich weiter 50 % „(begrenzt)“, solange die Strecke bis zur Ladestelle kurz ist).
- Zeigt der Kopf beim Tesla „0 von …“, obwohl er auswärts lädt, fehlen die Ladestände unterwegs in Influx (`SELECT last("pct") FROM "soc" WHERE "id" = 'tesla' AND time > now() - 2d GROUP BY time(5m) fill(none)` muss tagsüber Werte haben). Dann melden, bevor die Automatik das Ziel übernimmt.

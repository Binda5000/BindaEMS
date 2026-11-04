# Ladeauswahl per Nachricht beim Anstecken – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Steckst du ein Auto an, kommt aufs Handy eine Nachricht mit Ladestand, Kosten je Lademodus und Knöpfen; ein Tipp stellt den Lademodus um, ohne das UI zu öffnen.

**Architecture:** Die app rechnet je angestecktem Fahrzeug eine **Ladeschätzung** für die Modi „Geplant“, „Sofort“, „PV-Überschuss“ und „Min+PV“ aus dem gültigen Plan (Preise und PV-Überschuss je Viertelstunde) und veröffentlicht sie als neuen HA-Sensor „Ladeschätzung“ mit Attributen. Der bestehende Blueprint `bindaems_benachrichtigungen.yaml` liest den Sensor in die Meldung „angesteckt“, hängt Knöpfe an (Actionable Notifications der Companion-App) und setzt bei einem Tipp über `select.select_option` den Lademodus, also über denselben Weg und dieselben Rechte wie heute jede Bedienung aus HA. core, `shared`, UI, Datenbank und `config.yaml` bleiben gleich.

**Tech Stack:** Python 3.12, pydantic, pytest, hypothesis; Home Assistant Blueprint (YAML/Jinja), Test mit `pytest-homeassistant-custom-component` (HA 2026.2) im Scratch wie am 20.10.2026. Keine neuen Abhängigkeiten im Repo, keine Migration.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Abschnitt 7.5 (Lademodi), Abschnitt 12 (Home Assistant), „Stand Plan 4a“ (PV-Strom ins Auto kostet 0 ct), „Stand Plan 4b“ (Geplant, Ziel und Abfahrt), „Stand Plan Sofort-Limit“. Idee 12.1 aus `/mnt/project-files/ideen/brainstorming-2026-11.md`.

## Einordnung

- **Anlass (Betreiber, 4.11.2026):** Idee 12.1 „Nachricht mit Knöpfen beim Anstecken“, Platz 2 der Top 10: „Tesla angesteckt, 41 %. Geplant bis 07:00 auf 80 % kostet ca. 2,10 €. [Passt] [Sofort] [Nur PV]“.
- **Heute:** Der Blueprint meldet „🔌 Tesla angesteckt · An Wall Connector. Lademodus „PV-Überschuss“. SOC 41 %.“ Um den Modus zu ändern, öffnest du UI oder HA. Kosten je Modus gibt es nirgends: Der Plan kennt nur Autos in „Geplant“, und die Statistik rechnet erst nach dem Laden.
- **Eine Richtigstellung zur Idee:** „Die Kosten liefert der Plan schon“ stimmt nur halb. Der Plan hat Preise und PV-Überschuss je Viertelstunde, aber keine Kosten je Auto und nichts für einen Modus, in dem das Auto gerade nicht steht. Darum rechnet dieser Plan eine eigene, einfache Schätzung (Task 1).
- **Was sich für dich ändert:** Die Meldung beim Anstecken sieht etwa so aus:
  **🔌 Tesla angesteckt · 41 %**
  An Wall Connector, jetzt „PV-Überschuss“: bis Do 07:00 ca. 52 %, 0,00 €.
  Geplant auf 80 %: ca. 2,10 €, fertig ca. 05:30. Sofort: ca. 3,40 €, fertig ca. 21:15.
  **[Geplant · 2,10 €] [Sofort · 3,40 €] [Passt]**
  Nach einem Tipp ersetzt sie sich durch „✅ Tesla lädt jetzt „Sofort“ · fertig ca. 21:15 · ca. 3,40 €“. Ein Tipp auf die Meldung selbst öffnet die Seite „Laden“ von BindaEMS (wenn du die Adresse einträgst).
- **Ausrollen:** `ems-app` mit Stufe 2 (ab 23.11.2026, nach `sammel-pull.md`, nie `ems-app` allein ohne den Stand dort), dazu den Blueprint in HA neu kopieren. **Bis zum 22.11.2026 nichts an der Anlage**, die Abnahmen laufen. Ohne neuen Blueprint ändert sich nichts außer einem weiteren Sensor; ohne neue app zeigt der neue Blueprint die Meldung wie heute plus Knöpfe ohne Kosten.
- **Rückweg:** Alter Blueprint zurück, ältere app: der Sensor verschwindet (verwaiste Entität in HA löschen). Keine Einstellung, kein Schema, nichts zu exportieren.
- **Parallel:** `claude/project-thread-ambh18` (Netzgrenze je Phase) ist nicht auf `main`. Er berührt `planning/` und `ha/entities.py` nicht an den Stellen dieses Plans; wer zuerst auf `main` kommt, der andere rebased. Mit der Netzgrenze werden die Zahlen im Plan (Überschuss, Import) nur genauer, an der Schätzung ändert sich nichts.

## Entscheidungen (Entscheidung 1 gewählt am 4.11.2026; Freigabe des ganzen Plans offen)

1. **Knöpfe (vom Betreiber am 4.11.2026 auf der Karte gewählt: „Geplant, Sofort, PV + Passt“):** Höchstens drei, denn Android zeigt nicht mehr. Festgelegt: „Geplant“, „Sofort“ und „PV-Überschuss“, ohne den Modus, in dem das Auto schon steht; ist noch ein Platz frei, kommt „Passt“ (schließt die Meldung). Steht das Auto auf „Min+PV“ oder „Aus“, sind es die drei Modi ohne „Passt“ (Wegwischen heißt dann „passt“). Im Blueprint ist die Auswahl der Modi ein Feld, du kannst sie also später selbst ändern.
2. **Schätzung statt zweitem Optimierer-Lauf.** Ein zweiter Lauf „was wäre, wenn das Auto geplant lädt“ wäre genauer, kostet aber je Auto und Minute eine Optimierung und änderte den Planer. Vorschlag: eine einfache Rechnung über die Viertelstunden des gültigen Plans (Task 1): erst der PV-Überschuss (0 ct, wie in der Statistik seit 4a), dann die billigsten Viertelstunden vor der Abfahrt. Darum steht überall „ca.“. Der Akku zählt in der Schätzung nicht (Akku ins Auto, Akku-Unterstützung): Die echten Kosten liegen dann eher darunter, nie darüber.
3. **Leistungspreis bei „Sofort“:** Gilt im Monat ein Leistungspreis, kommt zu „Sofort“ die Mehrkosten der vollen Ladeleistung dazu, mit derselben Rechnung wie beim Boost (`boost_cost`, Plan 3c, Präzisierung 14). Der gedachte Satz „Spitze vermeiden ohne Leistungspreis“ (Netzgrenze je Phase) zählt nicht, er ist kein Geld. Heute gilt kein Leistungspreis; die Zeile erscheint dann nicht.
4. **Gäste:** bekommen dieselbe Meldung mit Knöpfen „Sofort“, „PV-Überschuss“, „Min+PV“ (ohne den aktuellen Gast-Lademodus), aber ohne Kosten: Ohne Ladestand und Kapazität gibt es keinen Bedarf. Der Tipp setzt den Gast-Lademodus.
5. **Wer tippen darf:** jedes Handy, das die Meldung bekommt (die „Geräte“ der Automation). Der Wechsel steht im Änderungsprotokoll wie jede Bedienung aus HA (Urheber `home-assistant`). Lehnt BindaEMS ab (core nicht erreichbar), kommt die Warnung wie heute über „Warnungen und Alarme“, und die Meldung sagt „nicht übernommen“.
6. **Kein Element im UI.** Die Seite „Laden“ bekommt die Schätzung in diesem Plan nicht; das wäre ein Kandidat für die Dezember-Welle, wenn du sie dort haben willst.
7. **Ziel und Abfahrt** kommen aus der Absicht des Autos (Ziel-SOC, Abfahrtszeit und Tag, wie „Geplant“ sie nimmt), unter einem kleineren Ladelimit im Auto. Ändern geht nicht per Knopf (Android: drei Knöpfe), sondern mit Tipp auf die Meldung ins UI.

## Global Constraints

- Texte für den Betreiber deutsch, Anrede „du“; Zahlen mit Komma (`bindaems.shared.numbers.de`, im Blueprint `replace('.', ',')`); Kommentare und Docstrings deutsch im Stil der Umgebung.
- Sensor: object_id `<fahrzeug>_charge_estimate`, Name „Ladeschätzung“, Einheit `EUR`, Geräteklasse `monetary`, am Gerät „BindaEMS <Fahrzeug>“; in HA also `sensor.bindaems_<fahrzeug-slug>_ladeschatzung`.
- Attributschlüssel englisch wie bei „Ladeenergie“; Modi mit den Werten von `ChargeMode` (`planned`, `now`, `pv`, `min_pv`).
- Knopf-Kennung (`action`) im Blueprint: `BINDAEMS_MODUS|<ziel>|<modus-label>|<tag>`; `<ziel>` ist der Fahrzeug-Slug oder `gast`. „Passt“: `BINDAEMS_PASST|<tag>`.
- Prüfkette vor jedem Commit: `ruff format --check`, `ruff check`, `mypy --strict` (src + cerbo-watchdog), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % safety/control/actuators), UI `pnpm check`, `pnpm lint`, `pnpm test`. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Commits deutsch im Stil `feat(app): …`. Kein Aktor wird live geschaltet; core bleibt unverändert. Nichts an der Anlage vor dem 23.11.2026.
- `/mnt/project-files` ist ein Netz-Mount: Pfade mit Schrägstrich am Ende, nie den ganzen Ordner durchsuchen, eine geschriebene Datei nach etwa zehn Sekunden noch einmal lesen.

## Review Focus

- Anstecken kurz vor oder über die Abfahrt (Abfahrt in 10 min, Abfahrt schon vorbei): Die Abfahrt ist immer die nächste nach jetzt (`next_departure`), das Fenster darf nicht negativ werden → `test_departure_within_current_slot` und `test_departure_passed_uses_next_day` in Task 1, `test_estimate_departure_is_next` in Task 2.
- Abfahrt hinter dem Ende des Plans (Preise für morgen fehlen noch): nur bis zum Planende rechnen, `until` setzen, „erreichbar“ ehrlich nein → `test_window_ends_at_plan_end` in Task 1.
- Auto steht schon auf „Geplant“ und ist im Plan: Sein eigenes Laden im Plan darf den Überschuss nicht kleiner rechnen → `test_own_planned_charging_counts_as_surplus` in Task 1.
- Ladestand unbekannt (Tesla schläft, e-Golf nur „angenommen“): keine Zahlen erfinden, Sensor „unbekannt“, Meldung ohne Kosten, Knöpfe trotzdem → `test_estimate_unknown_without_measured_soc` in Task 2, Szenario „ohne Schätzung“ in Task 3.
- Doppelter Tipp oder Tipp auf eine alte Meldung, nachdem das Auto abgesteckt ist: höchstens ein Wechsel, kein Fehler; der Modus des Fahrzeugs ändert sich trotzdem (das Fahrzeug, nicht die Wallbox, trägt den Modus) → Szenario „doppelter Tipp“ in Task 3.

---

### Task 1: Ladeschätzung je Modus (reine Rechnung)

**Files:**
- Create: `src/bindaems/app/charging/estimate.py`
- Test: `tests/unit/app/charging/test_estimate.py`

**Interfaces:**
- Consumes: `PlanSlot` (`shared/plan.py`: `start`, `import_w`, `export_w`, `import_ct`, `estimated`, `ev_w`, `ev_battery_w`), `need_kwh` aus `app/planning/vehicles.py`, `SLOT` aus `shared/timeutil.py`, `ChargeMode`.
- Produces:
  - `ESTIMATED_MODES: Final = (ChargeMode.PLANNED, ChargeMode.NOW, ChargeMode.PV, ChargeMode.MIN_PV)`
  - `REACH_EPS_KWH: Final = 0.1` (wie `reachable` im Plan)
  - `@dataclass(frozen=True) class ModeEstimate: energy_kwh: float; cost_eur: float; finish_at: datetime | None; soc_pct: float; reachable: bool; power_cost_eur: float | None = None` (Energie an der Wallbox; `soc_pct` an der Abfahrt bzw. am Fensterende; `power_cost_eur` nur bei `NOW`)
  - `@dataclass(frozen=True) class ChargeEstimate: soc_pct: float; target_soc_pct: float; departure_at: datetime; until: datetime; need_kwh: float; price_estimated: bool; modes: Mapping[ChargeMode, ModeEstimate]`
  - `def pv_for_car(slot: PlanSlot, key: str) -> float` – PV-Überschuss in W, den das Auto `key` im Slot hätte: `export_w + max(0, ev_w[key] − ev_battery_w[key] − import_w)` (sein eigenes PV-Laden im Plan zählt mit, Grid- und Akku-Anteil nicht).
  - `def estimate_charge(slots: Sequence[PlanSlot], *, key: str, now: datetime, soc_pct: float, target_soc_pct: float, departure_at: datetime, usable_kwh: float, efficiency: float, min_w: float, max_w: float, now_w: float, power_cost_eur: float | None) -> ChargeEstimate`

Rechnung (das bestimmt die Signatur nicht, darum hier):

- **Fenster:** Slots mit `start + SLOT > now` und `start < departure_at`; der erste und der letzte zählen anteilig (ab `now`, bis `departure_at`). `until = min(departure_at, Ende des letzten Slots)`. Leeres Fenster: alle Modi Energie 0, `reachable` nur bei Bedarf ≤ `REACH_EPS_KWH`.
- **Bedarf:** `need_kwh(soc_pct, target_soc_pct, usable_kwh, efficiency)`; jede Rechnung hört auf, wenn er gedeckt ist (der letzte genutzte Slot anteilig).
- **PV (`PV`):** je Slot `p = min(pv_for_car, max_w)`, wenn `pv_for_car ≥ min_w`, sonst 0. Kosten 0.
- **Min+PV (`MIN_PV`):** je Slot `p = min(max_w, max(min_w, pv_for_car))`; Netzanteil `max(0, p − pv_for_car)` zu `import_ct`.
- **Sofort (`NOW`):** je Slot ab jetzt `p = now_w`; Netzanteil `max(0, p − pv_for_car)` zu `import_ct`; dazu `power_cost_eur`.
- **Geplant (`PLANNED`):** Jeder Slot hat zwei Stücke: ein freies (`min(pv_for_car, max_w) · h`, 0 ct) und ein Netzstück (`(max_w − min(pv_for_car, max_w)) · h` zu `import_ct`). Erst alle freien Stücke, dann die Netzstücke nach `import_ct` aufsteigend (bei Gleichstand das frühere), bis der Bedarf gedeckt ist (das letzte anteilig). Die Mindestleistung zählt hier nicht (der Planer mischt PV und Netz); das macht „Geplant“ höchstens um Cent zu günstig. So ist „Geplant“ nie teurer als „Sofort“ (Property-Test).
- `finish_at`: Ende des letzten Slots mit Laden (bei anteiligem letzten: der Zeitpunkt, an dem der Bedarf gedeckt ist), `None` ohne Laden. `soc_pct = min(target, soc + energy · efficiency / usable_kwh · 100)`. `reachable = energy ≥ need − REACH_EPS_KWH`. `price_estimated`: mindestens ein Slot mit Netzanteil in irgendeinem Modus ist `estimated`.
- `cost_eur` auf 2 Stellen gerundet, `energy_kwh` und `soc_pct` auf 1.

- [ ] **Step 1: Tests schreiben** – mit einem Helfer `slot(start, *, import_ct=20.0, export_w=0.0, import_w=0.0, estimated=False, ev_w=None, ev_battery_w=None)`, der einen gültigen `PlanSlot` baut. Werte: Tesla `usable_kwh=75`, `efficiency=0.9`, `min_w=4140` (6 A · 230 V · 3), `max_w=11040`.
  - `test_pv_only_cost_is_zero`: 8 Slots mit `export_w=5000` → `modes[PV].cost_eur == 0.0`, `energy_kwh == 10.0` (8 · 5 kW · 0,25 h, Bedarf größer).
  - `test_pv_below_min_power_charges_nothing`: `export_w=3000` → `modes[PV].energy_kwh == 0.0`; `modes[MIN_PV].cost_eur > 0`.
  - `test_planned_picks_cheapest_slots`: 4 Slots, `import_ct` 30, 10, 20, 40, Bedarf genau 2 Slots voll (Ladestand so wählen, kein Überschuss) → Netzenergie in den Slots mit 10 und 20 ct, `cost_eur == round(11.04*0.25*(0.10+0.20), 2)`, `finish_at` = Ende des dritten Slots.
  - `test_now_charges_from_now`: dieselben Slots → `modes[NOW].cost_eur == round(11.04*0.25*(0.30+0.10), 2)`, `finish_at` = Ende des zweiten Slots.
  - `test_planned_never_costs_more_than_now` (hypothesis: zufällige Preise 0–60 ct, Überschuss 0–12 kW, 1–96 Slots, Ladestand 0–100): Erreicht `NOW` das Ziel im Fenster, dann `modes[PLANNED].cost_eur ≤ modes[NOW].cost_eur + 0.01` und `modes[PLANNED].reachable`; immer `energy_kwh ≤ need_kwh + 1e-6` und `soc_pct ≤ target_soc_pct`.
  - `test_now_includes_power_cost`: `power_cost_eur=1.5` → `modes[NOW].power_cost_eur == 1.5`, andere Modi `None`.
  - `test_departure_within_current_slot`: `now` 06:52, Abfahrt 07:00 → nur 8 min zählen, `energy_kwh == round(11.04 * 8/60, 1)` bei Sofort.
  - `test_departure_passed_uses_next_day`: Abfahrt vor `now` → `ValueError` (die Aufrufer geben immer `next_departure`; Task 2 testet das).
  - `test_window_ends_at_plan_end`: Plan endet 23:00, Abfahrt 07:00 → `until` 23:00, `reachable is False` wenn der Bedarf größer ist als bis dahin ladbar.
  - `test_own_planned_charging_counts_as_surplus`: Slot `export_w=0`, `import_w=0`, `ev_w={"tesla": 5000}` → `pv_for_car(slot, "tesla") == 5000`; mit `import_w=2000` → 3000; mit `ev_battery_w={"tesla": 1000}` und `import_w=0` → 4000.
  - `test_already_at_target`: Ladestand 80, Ziel 80 → `need_kwh == 0`, alle Modi `energy_kwh == 0`, `reachable`, `finish_at is None`.
  - `test_price_estimated_flag`: genutzter Netz-Slot mit `estimated=True` → `price_estimated is True`; nur PV-Slots geschätzt → `False`.
  - `test_dst_day`: Fenster über den 25.10.2026 02:00–03:00 (doppelte Stunde) zählt 100 Viertelstunden richtig (Energie bei Sofort über 25 h = `min(Bedarf, 11,04 · 25)`).
- [ ] **Step 2: Tests laufen lassen, sie scheitern** – `uv run pytest tests/unit/app/charging/test_estimate.py -v` → FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: `estimate.py` umsetzen** nach den Interfaces und der Rechnung oben; Modul-Docstring nennt Entscheidungen 2 und 3 (Akku zählt nicht, „ca.“).
- [ ] **Step 4: Tests laufen lassen, sie bestehen** – derselbe Befehl → PASS; dann die ganze Prüfkette.
- [ ] **Step 5: Commit** – `git add src/bindaems/app/charging/estimate.py tests/unit/app/charging/test_estimate.py && git commit -m "feat(app): Ladeschätzung je Lademodus aus dem Plan"`

### Task 2: Sensor „Ladeschätzung“ in Home Assistant

**Files:**
- Modify: `src/bindaems/app/ha/entities.py` (`HaInputs` ~118–175, `build_entities` Fahrzeug-Schleife ~1177, `build_inputs` ~1439)
- Modify: `src/bindaems/app/runtime.py` (`_build_ha_inputs` ~584)
- Modify: `docs/betrieb.md` (Abschnitt 7, Tabelle der Sensoren je Fahrzeug, nach „Ladeenergie“ ~752)
- Test: `tests/unit/app/ha/test_entities.py`, `tests/unit/app/test_runtime.py` (oder wo `_build_ha_inputs` schon getestet wird)

**Interfaces:**
- Consumes: `estimate_charge`, `ChargeEstimate`, `ESTIMATED_MODES` (Task 1); `boost_cost` (`app/control/boost_cost.py`); `full_power_w` (`app/planning/vehicles.py`); `next_departure` (`shared/control.py`); Health `vehicles` (`wallbox`, `plugged`, `soc_pct`, `soc_source`, `charge_limit_pct`).
- Produces:
  - `HaInputs.boost_cost_eur: Mapping[str, float] = field(default_factory=dict)` – je Fahrzeug die Mehrkosten des Leistungspreises bei voller Leistung; leer, wenn kein Leistungspreis gilt.
  - `build_inputs(..., boost_cost_eur: Mapping[str, float] | None = None)`; `_build_ha_inputs` füllt es aus `boost_cost(cfg, settings.tariff, month_peak_kw, heute)["vehicles"]` nur bei `applies`.
  - `def charge_estimate(cfg: Config, key: str, inputs: HaInputs) -> ChargeEstimate | None` in `entities.py`: `None` ohne `inputs.plan`, ohne `inputs.now`, ohne Health-Eintrag mit `plugged is True` und einer Wallbox aus `cfg.wallboxes`, oder wenn `soc_source` nicht `measured`/`estimated` ist. Ziel = `min(intent target_soc_pct, charge_limit_pct)`, Abfahrt = `next_departure(departure, now, departure_on)` der Absicht (ohne Absicht: `VehicleIntent()`-Standard). `max_w = full_power_w(...)`, `min_w = vehicle.min_a · U · phases`, `now_w = max_w` oder mit `now_max_a` begrenzt (`min(max_w, now_max_a · U · phases)`).
  - Entität `EntitySpec(f"{oid}_charge_estimate", "sensor", "Ladeschätzung", device, …, unit="EUR", device_class="monetary", attributes=…)`; Zustand `modes[PLANNED].cost_eur` mit 2 Stellen, sonst `UNKNOWN`.
  - Attribute (Ortszeit ISO für Zeiten, `None` wenn unbekannt): `soc_pct`, `target_soc_pct`, `departure_at`, `until`, `need_kwh`, `price_estimated`, und je Modus `planned`, `now`, `pv`, `min_pv` ein Objekt `{"energy_kwh", "cost_eur", "finish_at", "soc_pct", "reachable"}` (bei `now` zusätzlich `power_cost_eur`). Ohne Schätzung alle Schlüssel mit `None`.

- [ ] **Step 1: Tests schreiben** (mit den vorhandenen Helfern für `HaInputs` und Health in `test_entities.py`):
  - `test_charge_estimate_entity_discovery`: Discovery für `tesla_charge_estimate` hat `unit_of_measurement == "EUR"`, `device_class == "monetary"`, `json_attributes_topic`, Gerät „BindaEMS Tesla“.
  - `test_charge_estimate_state_is_planned_cost`: Plan mit 4 Slots, Tesla angesteckt, `soc_source="measured"` → Zustand gleich `estimate_charge(...).modes[PLANNED].cost_eur` als `"2.10"`-Form; Attribute haben alle Modi.
  - `test_estimate_unknown_without_measured_soc`: `soc_source="assumed"` → Zustand `"None"`, `attributes["planned"] is None`.
  - `test_estimate_unknown_when_unplugged_or_no_plan`: `plugged=False` bzw. `plan=None` → `"None"`.
  - `test_estimate_uses_charge_limit`: Absicht 90 %, `charge_limit_pct=80` → `target_soc_pct == 80`.
  - `test_estimate_departure_is_next`: `now` 08:00, Abfahrt „07:00“ → `departure_at` morgen 07:00 Ortszeit.
  - `test_estimate_now_respects_now_limit`: `now_max_a=10` beim Tesla → Sofort-Energie je voller Viertelstunde `10·230·3·0,25/1000`.
  - `test_estimate_now_power_cost`: `boost_cost_eur={"tesla": 1.5}` → `attributes["now"]["power_cost_eur"] == 1.5`; ohne → `None`.
  - In `test_runtime.py`: `test_ha_inputs_boost_cost_only_with_power_rate` – ohne Leistungspreis im Tarif `boost_cost_eur == {}`, mit Satz der Wert aus `boost_cost`.
- [ ] **Step 2: Tests laufen lassen, sie scheitern** – `uv run pytest tests/unit/app/ha/test_entities.py -k estimate -v` → FAIL.
- [ ] **Step 3: Umsetzen** nach den Interfaces; der Entity-Snapshot bzw. die Zählung der Entitäten in bestehenden Tests wird um eine Entität je Fahrzeug nachgeführt (nicht abgeschwächt).
- [ ] **Step 4: Tests laufen lassen, sie bestehen**, dann die ganze Prüfkette.
- [ ] **Step 5: `docs/betrieb.md`** – Zeile „Ladeschätzung“ in der Tabelle, Text: Zustand = Kosten für „Geplant“ in €, Attribute wie oben, „unbekannt“ ohne gültigen Plan, ohne gemessenen oder geschätzten Ladestand oder wenn das Auto nicht steckt; Hinweis „ohne Akku, darum eher zu hoch“.
- [ ] **Step 6: Commit** – `git commit -m "feat(app): Sensor Ladeschätzung je Fahrzeug in Home Assistant"`

### Task 3: Blueprint mit Knöpfen, Test in HA, Doku

**Files:**
- Modify: `/mnt/project-files/ha-benachrichtigungen/bindaems_benachrichtigungen.yaml` (vorher frisch lesen; Kopie der alten Fassung als `bindaems_benachrichtigungen-2026-10-27.yaml` daneben)
- Modify: `/mnt/project-files/ha-benachrichtigungen/LIESMICH.md`
- Test: im Scratch, `pytest-homeassistant-custom-component` für HA 2026.2 (nicht im Repo, wie am 20.10.2026)
- Modify: `docs/uebergabe.md` (Kurzfassung, „Offen an der Anlage“), `/mnt/project-files/anlage/sammel-pull.md` (Stufe 2: Blueprint neu kopieren)

**Interfaces:**
- Consumes: `sensor.bindaems_<slug>_ladeschatzung` (Task 2), `select.bindaems_<slug>_lademodus`, `select.bindaems_gast_lademodus`, die bestehenden Variablen des Blueprints (`praefix`, `lademodus`, `tag`, `fahrzeug_name`).
- Produces (neue Eingaben unter „Laden“):
  - `knoepfe` (boolean, Standard an): „Knöpfe beim Anstecken“.
  - `knoepfe_modi` (Mehrfachauswahl aus „Geplant“, „Sofort“, „PV-Überschuss“, „Min+PV“, „Aus“; Standard nach Entscheidung 1).
  - `laden_url` (Text, Standard leer): Adresse der Seite „Laden“, z. B. `https://ems.example/laden`; leer = Tipp öffnet nur die App.

Verhalten:

- Meldung „angesteckt“: Titel `🔌 <Auto> angesteckt · <SOC> %`. Text aus dem Sensor: aktueller Modus mit Ladestand an der Abfahrt und Kosten; dann die Modi der Knöpfe mit Kosten und „fertig ca.“; „Ziel nicht erreichbar bis <Abfahrt>“ statt „fertig“, wenn `reachable` falsch; „(Preise teils geschätzt)“ bei `price_estimated`; bei `power_cost_eur` „+ bis zu x € Leistungspreis“. Ohne Sensorwert: der Text von heute.
- `data.actions`: die Modi aus `knoepfe_modi` ohne den aktuellen, höchstens drei; ist Platz, zuletzt „Passt“. Titel „Sofort · 3,40 €“ (ohne Kosten beim Gast und ohne Schätzung). `data.clickAction` und `data.url` = `laden_url`, wenn gesetzt.
- Neuer Auslöser `event` `mobile_app_notification_action`, id `knopf`; Bedingung: `trigger.event.data.action` beginnt mit `BINDAEMS_`. `knopf` gehört zu keinem Ereignis der Auswahl „Ereignisse“, ist aber nur aktiv, wenn `knoepfe` an ist.
- `BINDAEMS_PASST|<tag>`: `clear_notification` mit `tag` an alle Geräte.
- `BINDAEMS_MODUS|<ziel>|<label>|<tag>`: steht der Select schon auf `<label>`, nichts tun (doppelter Tipp). Sonst `select.select_option`, dann `wait_template` bis der Select `<label>` zeigt, höchstens 20 s. Danach dieselbe `tag`-Meldung ersetzen: „✅ <Auto> lädt jetzt „<label>“ · fertig ca. … · ca. … €“ (Werte des Modus aus dem Sensor), bei Zeitüberschreitung „⚠️ <Auto>: „<label>“ nicht übernommen. Details unter Warnungen.“.

- [ ] **Step 1: HA-Testumgebung im Scratch** aufsetzen (wie am 20.10.: `pytest-homeassistant-custom-component` passend zu HA 2026.2, `mobile_app`-Notify durch einen aufzeichnenden Dienst ersetzt, Selects als `input_select`-Ersatz bzw. MQTT-freie Template-Selects mit denselben Entitäts-IDs). Hinweis aus damals: `block_till_done` hängt, solange ein `delay` läuft; mit `asyncio.sleep(0)` takten.
- [ ] **Step 2: Szenarien schreiben**, die mit dem alten Blueprint scheitern:
  - „angesteckt mit Schätzung“: Sensor mit Attributen, Modus „PV-Überschuss“ → eine Meldung, `actions` = Geplant, Sofort, Passt; Titel mit Kosten „2,10 €“.
  - „ohne Schätzung“: Sensor `unknown` → Meldung wie heute plus Knöpfe ohne Kosten.
  - „Gast“: Fahrzeug „Gast“ → Knöpfe Sofort, PV-Überschuss, Min+PV ohne Kosten (Gast-Modus „PV-Überschuss“ → Sofort, Min+PV, Passt).
  - „Tipp Sofort“: Event `BINDAEMS_MODUS|tesla|Sofort|<tag>` → `select.select_option` mit `Sofort` an `select.bindaems_tesla_lademodus`, danach Bestätigung mit demselben `tag`.
  - „doppelter Tipp“: zweimal dasselbe Event → genau ein `select_option`.
  - „abgelehnt“: Select bleibt → nach 20 s „nicht übernommen“.
  - „Passt“: → `clear_notification` mit `tag`.
  - „Knöpfe aus“: `knoepfe: false` → Meldung ohne `actions`, Event tut nichts.
  - Die bestehenden Szenarien (angesteckt, gestartet, beendet, abgesteckt, Gast erst erkannt, Warnungen) bleiben grün.
- [ ] **Step 3: Szenarien laufen lassen**, die neuen scheitern mit dem alten Blueprint.
- [ ] **Step 4: Blueprint umsetzen** (Kopie der alten Fassung vorher sichern).
- [ ] **Step 5: Alle Szenarien grün**; die Datei nach zehn Sekunden noch einmal lesen.
- [ ] **Step 6: `LIESMICH.md`** – Abschnitt „Knöpfe beim Anstecken (Stand 2026-11)“: Beispiel, was die Schätzung kann und nicht kann („ca.“, ohne Akku, Preise nach 13 Uhr für morgen), die drei Eingaben, „Aktualisieren“ wie bisher; Hinweis: braucht app ab diesem Plan für die Kosten.
- [ ] **Step 7: Doku im Repo** – `docs/uebergabe.md`: Kurzfassung (Plan, Stand, nicht ausgerollt, mit Stufe 2), „Offen an der Anlage“ neuer Punkt „Blueprint neu kopieren, einmal anstecken, Knopf probieren“; `sammel-pull.md` Stufe 2 bekommt dieselbe Zeile.
- [ ] **Step 8: Commit** – `git add docs/ && git commit -m "docs: Ladeauswahl beim Anstecken in Übergabe und Betrieb"`

## Abschluss

Gesamtreview über den Branch, Fix-Welle für kritische und wichtige Befunde (je Fix ein Test, der vorher scheitert), kleine sammeln. Abschluss-Datei `/mnt/project-files/plaene/ladeauswahl-abschluss.md`: Ergebnis und Tests, Rulings, aufgeschobene Kleinigkeiten, Frage nach der Integration (`main` nur mit Zustimmung, per Fast-Forward, nach grüner Branch-CI).

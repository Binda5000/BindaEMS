# Lokale Prognosemodelle: erst messen, dann bauen – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mit den aufgezeichneten Tagen der Anlage in Euro messen, wie viel bessere Prognosen (Hauslast, PV) dem Optimierer überhaupt bringen könnten und wie viel das vorhandene Lastmodell aus 5a davon holt, und erst danach entscheiden, ob ein weiteres lokales Modell gebaut wird.

**Architecture:** Der Optimierer bleibt exakt berechnet (MILP); ein Modell liefert höchstens Prognosen. Teil A ist eine Messung an der Anlage mit den vorhandenen Werkzeugen `export_days` und `scenario_days`, ohne Code und ohne Pull. Teil B (Task 1, nur nach der Entscheidungsregel) gibt `export_days` zwei Optionen, damit auch die Prognose des Lastmodells (Schattenbetrieb `load_model`) in Euro gemessen werden kann. Nur `tools/`; app, core, UI und Datenbank bleiben unverändert.

**Tech Stack:** Python 3.12, pydantic, der bestehende Szenario-Runner (PuLP/HiGHS über den Planer), pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, 10 „Prognosen und Lernen“ (10.3 Lastmodell, 10.5 Grenzen des Lernens), 9.5 und „Stand Plan 3b“ (Szenario-Vergleich). Grundlage: Plan 5a (`2026-10-20-phase-5a-lastmodell.md`), Plan 5b (`2026-10-20-phase-5b-fahrverhalten.md`), Plan 3b (`export_days`, `scenario_days`). Verwandt: Plan „Was lag auf dem Tisch?“ (`/mnt/project-files/plaene/2026-11-04-was-lag-auf-dem-tisch.md`, Idee 18.1, wartet auf Freigabe).

## Ausgangsfrage und kurze Antwort

Frage des Betreibers (23.11.2026): Kann eine kleine, lokal auf der CPU laufende KI die Optimierung verbessern? Eine Google Coral ist vorhanden; Cloud-Dienste kommen nicht in Frage.

Kurze Antwort:
- **Das Modell, das hier Sinn ergibt, läuft schon.** Plan 5a hat ein LightGBM-Modell (Gradient Boosting) für die Hauslast gebaut. Es trainiert jede Nacht auf der CPU der VM (ein Thread, Zeitlimit 10 min, rund 60 000 Zeilen), rechnet seit dem 26.10.2026 im Schattenbetrieb mit und wird nur verwendet, wenn es mindestens 10 % genauer ist als das Profil. An der Anlage steht „Lastmodell verwenden“ aus. Die Abnahme 5a (28 Tage) ist ab heute fällig.
- **Der Optimierer selbst soll kein Modell werden.** Ein gelerntes Steuerverfahren (etwa Reinforcement Learning) bräuchte Jahre an Daten, könnte „Warum?“ nicht mehr erklären, wäre in den Simulator-Szenarien nicht prüfbar und würde Spec 10.5 widersprechen (Gelerntes ändert keine harte Grenze und erreicht die Safety-Schicht nie).
- **Die Coral bringt hier nichts.** Sie rechnet nur kleine, auf 8 Bit quantisierte TFLite-Netze. Die Daten sind tabellarisch (Uhrzeit, Wetter, Kalender, jüngste Last); dafür ist Gradient Boosting auf der CPU genauer und braucht für die Vorhersage Millisekunden. Die Coral bräuchte ein neuronales Netz, eine Übersetzung mit dem Edge-TPU-Compiler und die Weitergabe des USB-Geräts in die VM, ohne Gewinn an Genauigkeit.
- **Der mögliche Gewinn ist vermutlich klein, aber messbar.** Der Optimierer war im Probelauf vom 26.10.2026 über 8 Oktobertage 1,82 € günstiger als „Victron allein“ (rund 9 %, etwa 7 € im Monat). Bessere Prognosen holen nur einen Teil dessen, was zwischen dem heutigen Planer und demselben Planer mit perfekter Voraussicht liegt. Mit einem großen Hausakku (96 kWh laut Übersicht) verschiebt ein Fehler der Lastprognose von 1 bis 2 kWh am Tag selten eine Entscheidung. Schätzung ohne Messung: perfekte Lastprognose unter 3 € im Monat, ein realistisches Modell holt davon vielleicht ein Drittel. Die PV-Prognose wiegt im Winter mehr (Netzladen in der Nacht hängt an der PV des nächsten Tages, Hochnebel). Teil A misst genau das, bevor etwas gebaut wird.

## Was es schon gibt

| Prognose | Verfahren heute | Daten | Stand |
|---|---|---|---|
| Hauslast | Profil v1 (Median je Tagestyp und Viertelstunde, 56 Tage) und LightGBM (5a) im Schattenbetrieb, Aktivierung ab 10 % kleinerem MAE | Abrechnung seit Phase 1b, Wetterarchiv 92 Tage, bis 8 HA-Sensoren als Merkmale | ausgerollt 26.10., Schalter aus, Abnahme ab 23.11. |
| PV | Open-Meteo mit Kalibrierfaktoren je Stunde und Himmelsklasse (3a), Bänder P25/P75, Schneekürzung (auf `main`) | Ist-PV seit Mitte Oktober | ausgerollt (Schnee mit Stufe 2) |
| Abfahrt und Ziel der Autos | Perzentile je Wochentag und Gruppe (5b), Konfidenz | Tracker seit 27.10., Ladesessions seit 18.10. | ausgerollt, Abnahme ab 23.11. |
| Preise ohne Börsenwert | Median derselben Uhrzeit der letzten 28 Tage | Spotpreise | ausgerollt; Idee 2.2 (Modell aus Wind/PV-Prognose) offen |
| Wärme des Hauses | Wärmemodell der Klimaanlage (5c) | Klimadaten seit 26.10. | auf `main`, Stufe 2 |

**Datenhistorie** für ein weiteres Modell: rund 5 bis 6 Wochen, nur Herbst. Für Last und Fahrverhalten genügt das für den Vergleich, nicht für Jahreszeiten. Ein PV-Korrekturmodell braucht mindestens einen Winter (Hochnebel, Schnee) und einen Sommer, also frühestens Frühjahr 2027 eine sinnvolle Grundlage.

**Fahrverhalten:** Ein Modell statt der Perzentile ergibt keinen Sinn. Je Auto gibt es rund 20 Fahrtage in der Gruppe Mo–Fr; Perzentile sind bei so wenigen Werten robuster als jedes gelernte Verfahren. Verfehlt die Abnahme 5b die 80 %, fehlt eher ein Merkmal (Kalender in HA, Idee 3.1) als ein anderes Verfahren.

**Ohne Code sofort möglich:** 5a nimmt bis zu 8 HA-Sensoren als Merkmale (Einstellungen → „Lastmodell“). Anwesenheit (`person.…`, Idee 18.2) ist der naheliegende Kandidat; „Jetzt neu trainieren“ auf „Prognosen“, dann zeigt der Backtest, ob sie hilft.

## Entscheidungen

Jede Entscheidung hat eine Empfehlung; mit der Freigabe gelten die Empfehlungen, wenn der Betreiber nichts anderes sagt.

1. **Kein Modell im Optimierer, keine Coral.** Gelernt werden nur Prognosen, wie in Spec 10 und 10.5. Begründung oben.
2. **Erst messen (Teil A), dann entscheiden.** Gemessen wird der Wert perfekter Voraussicht: derselbe Planer mit den Ist-Werten als Prognose. Das ist die obere Grenze dessen, was jedes Modell für diese Größe holen kann.
3. **Zeitraum 27.10. bis 22.11.2026** (27 Tage): derselbe Planer (Stufe 1 seit 3.11., davor `d1875af`; `scenario_days` rechnet ohnehin mit dem Planer des Images), und das Lastmodell rechnete im Schattenbetrieb mit. Der 26.10. fällt weg (Fenster). Einspeisung ohne OeMAG-Wert für November zählt auf beiden Seiten 0 ct; das verzerrt alle vier Läufe gleich.
4. **Vier Läufe in Teil A:** A „Prognose wie gehabt“, B „Last perfekt“, C „PV perfekt“, D „beide perfekt“. Gewinn einer Größe = Kosten A − Kosten mit perfekter Größe, bereinigt um den End-SOC (Zeile „Optimierer bereinigt um den End-SOC“ des Berichts), hochgerechnet auf 30 Tage.
5. **Entscheidungsregel** (nach Teil A):
   - **Last:** Gewinn B unter 1 € im Monat → kein weiteres Lastmodell, Task 1 entfällt; „Lastmodell verwenden“ nach der Abnahme 5a einschalten oder nicht, es macht in Euro kaum einen Unterschied. Ab 1 € im Monat → Task 1 umsetzen und mit Lauf E messen, wie viel davon 5a schon holt.
   - **PV:** Gewinn C ab 3 € im Monat → eigener Plan „PV-Korrekturmodell“ (Gradient Boosting auf Open-Meteo-Größen, gleiche Bauweise wie 5a: Registry, Backtest, Schattenbetrieb, Aktivierungsregel), frühestens mit Daten eines Winters. Darunter → bleibt bei der Kalibrierung aus 3a; Ideen 2.1 (Schnee, auf `main`) und 18.3 (Wettermodell wählen) sind günstiger.
   - **Warum diese Schwellen:** Ein realistisches Modell holt etwa ein Drittel des Werts perfekter Voraussicht. Unter rund 1 € im Monat Gewinn lohnen sich Abhängigkeit, Nachtlauf, Pflege und Abnahme eines weiteren Modells nicht.
   - Liegt D deutlich über B + C, wirken PV und Last zusammen; dann misst der Plan „Was lag auf dem Tisch?“ (18.1) das genauer mit Shapley-Anteilen und zusätzlich die Schätzpreise.
6. **Verhältnis zu 18.1.** Teil A ist die einmalige, kostenlose Fassung dessen, was 18.1 jede Nacht rechnen soll. Ergebnisse aus Teil A gehen in die Entscheidung über 18.1 ein; Task 1 berührt `export_days` nur mit neuen Optionen und verträgt sich mit Task 3 von 18.1 (dort ein Rebase).
7. **Task 1 nur bei Bedarf** (Entscheidung 5). Er ändert nur `tools/export_days.py`, kommt mit dem nächsten Sammel-Pull an die Anlage und schaltet nichts.

## Teil A: Messung an der Anlage (Betreiber, ohne Code)

Läuft mit dem ausgerollten Image `sha-146d7cc`. Die Werkzeuge lesen nur (Datenbank `mode=ro`). Die Dateien bleiben im Volume der app und gehören nie ins Repository.

```bash
cd /opt/bindaems
# A: Prognosen wie im Archiv (Last = Profil, weil „Lastmodell verwenden“ aus ist)
docker compose run --rm --entrypoint python ems-app \
  -m bindaems.tools.export_days --config /config/config.yaml \
  --von 2026-10-27 --bis 2026-11-22 --out /data/prog-a.json
# B und C: eine Größe ohne Prognose (der Lauf nimmt dann den Ist-Wert)
docker compose run --rm --entrypoint python ems-app -c '
import json
for name, key in (("last", "load_forecast_w"), ("pv", "pv_forecast_w")):
    d = json.load(open("/data/prog-a.json"))
    for s in d["slots"]:
        s[key] = {}
    json.dump(d, open(f"/data/prog-{name}.json", "w"))
'
# D: beide perfekt
docker compose run --rm --entrypoint python ems-app \
  -m bindaems.tools.export_days --config /config/config.yaml \
  --von 2026-10-27 --bis 2026-11-22 --ohne-prognose --out /data/prog-beide.json
# Vier Läufe (je einige Minuten)
for f in a last pv beide; do
  docker compose run --rm --entrypoint python ems-app \
    -m bindaems.tools.scenario_days --tage /data/prog-$f.json --takt 15 --out /data/prog-$f.md
done
for f in a last pv beide; do
  echo "== $f"; docker compose run --rm -T --entrypoint grep ems-app -E "bereinigt|Victron allein \(€\)|Ergebnis" /data/prog-$f.md
done
```

Dazu von der Seite **Prognosen** (Zeitraum „28 Tage“): die Zeilen „Last Profil“ und „Last Modell“ der Karte „Prognosegüte“ (MAE je Vorlauf, „besser als Profil“) und die Zeile „Gesamt“ des Backtests. Das ist zugleich die Abnahme 5a (`docs/verification/abnahme-phase-5a.md`).

Schick mir die Ausgabe der letzten Schleife, die Zeile je Tag von `export_days` mit „Prognose … %“ (nur, wenn ein Tag unter 100 % liegt oder übersprungen ist) und die Werte von „Prognosen“. Ich rechne daraus die Tabelle „Gewinn je Größe in € im Monat“ und wende Entscheidung 5 an.

**Hinweise:**
- Ein Slot ohne archivierte Prognose zählt im Lauf mit dem Ist-Wert, also perfekt. Liegt „Prognose“ in der Ausgabe von `export_days` an einem Tag unter 100 %, ist Lauf A dort zu günstig; ich nehme solche Tage aus der Auswertung.
- Die Läufe rechnen „Victron allein“ jedes Mal gleich; verglichen wird nur die Zeile des Optimierers.

## Global Constraints

- Keine Änderung an core, app, UI, `shared/`, Datenbank oder Planer; nur `src/bindaems/tools/export_days.py`, seine Tests und Doku.
- `export_days` ohne neue Optionen schreibt genau dieselbe Datei wie bisher (Version 1); `--ohne-prognose` bleibt und wirkt wie `--pv ist --last ist`.
- Die Datenbank bleibt nur lesend geöffnet (`mode=ro`), das Werkzeug legt nichts an.
- Texte und Hilfen deutsch, Commits im Stil `feat(tools): …`, keine Modellbezeichnungen im Repo.
- Prüfkette vor jedem Commit: `uv run ruff format --check .`, `uv run ruff check .`, `uv run mypy src cerbo-watchdog/bindaems_watchdog`, `uv run lint-imports`, `uv run pytest -q --cov --cov-fail-under=75` (≥ 90 % für safety/control/actuators unverändert). UI unverändert, also keine UI-Prüfung nötig. In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Kein Aktor ist live geschaltet durch diesen Plan; nichts an der Anlage schalten.

## Review Focus

1. **Modellprognose fehlt für einzelne Slots oder Horizontklassen** (Modell erst ab dem ersten Training, Lücken bei Training oder Ausfall): Ein leerer Eintrag würde im Lauf den Ist-Wert nehmen und das Modell zu gut aussehen lassen. Erwartet: je Slot und Horizontklasse ohne `load_model` gilt der Wert von `load` (was der Planer wirklich bekam), und die Zeile je Tag nennt den Anteil der Slots mit Modellwert. Test: Task 1 `test_missing_model_value_falls_back_to_used_forecast`.
2. **Kombination mit `--ohne-prognose`:** `--ohne-prognose` zusammen mit `--pv` oder `--last` ist ein Widerspruch. Erwartet: Exitcode 2 mit Meldung. Test: Task 1 `test_ohne_prognose_conflicts_with_new_options`.
3. **Tag der Zeitumstellung** (100 Slots): Die Ersetzung je Größe zählt alle Slots. Test: Task 1 `test_pv_ist_on_dst_day_replaces_every_slot`.
4. **`--last profil` vor dem 26.10.2026** (keine Schatten-Arten im Archiv): Erwartet wie 1, Rückfall auf `load`. Abgedeckt durch denselben Test mit `kind="load_profile"`.
5. **Unbekannter Wert einer Option** (`--last modelle`): argparse lehnt ab, Exitcode 2. Test: Task 1 `test_unknown_choice_exits_2`.

---

### Task 1: `export_days` mit Wahl der Prognose je Größe (nur nach Entscheidung 5)

**Files:**
- Modify: `src/bindaems/tools/export_days.py` (`_day`, `export_days_with_lines`, `export_days`, `_parser`, `main`, Modul-Docstring)
- Test: `tests/unit/tools/test_export_days.py`
- Modify: `docs/betrieb.md` (Abschnitt „Lastmodell (Phase 5a)“: neuer Unterabschnitt „Was bringt das Modell in Euro?“ mit den Befehlen aus Teil A und Lauf E)
- Modify: `docs/uebergabe.md` (eine Zeile im Kopfblock und in „Offen an der Anlage“, mit dem nächsten Sammel-Pull)

**Interfaces:**
- Consumes: `ForecastArchive.records(kind, start, end)`, `Kind = Literal["pv", "load", "load_profile", "load_model"]` (`app/forecast/archive.py`).
- Produces:
  - `PvSource = Literal["prognose", "ist"]`, `LoadSource = Literal["verwendet", "profil", "modell", "ist"]` in `export_days.py`; `LOAD_KIND: Final[dict[LoadSource, Kind]] = {"verwendet": "load", "profil": "load_profile", "modell": "load_model"}`.
  - `export_days_with_lines(engine, cfg, first, last, *, start_soc_pct: float, pv: PvSource = "prognose", load: LoadSource = "verwendet", clock: Clock) -> tuple[ScenarioFile, list[str]]` und `export_days(...)` mit denselben Schlüsselwörtern. Der Parameter `with_forecast` entfällt; Aufrufer im Repo werden angepasst (`with_forecast=False` → `pv="ist", load="ist"`).
  - `_day(services, day, *, pv: PvSource, load: LoadSource)`: `ist` → `{"h0_6": Ist-Wert}`; `prognose` / `verwendet` wie bisher; `profil` / `modell` → je Horizontklasse der Wert der Schatten-Art, fehlende Klassen aus `load` ergänzt.
  - Zeile je Tag: wie bisher, bei `--last profil|modell` zusätzlich `, Lastmodell {n} %` bzw. `, Profil {n} %` (Anteil der Slots, für die die Schatten-Art mindestens eine Klasse hatte).
  - CLI: `--pv {prognose,ist}` (Standard `prognose`), `--last {verwendet,profil,modell,ist}` (Standard `verwendet`); `--ohne-prognose` bleibt als Kurzform für `--pv ist --last ist` und ist mit einer der beiden neuen Optionen unvereinbar (Meldung „--ohne-prognose nicht zusammen mit --pv oder --last“, Exitcode 2).

- [ ] **Step 1: Failing tests** in `tests/unit/tools/test_export_days.py` (Helfer `fill_day` bekommt bei Bedarf `kinds: Sequence[Kind]` für die archivierten Arten):
  - `test_last_ist_keeps_pv_forecast`: Tag mit Prognose; `load="ist"` → `slots[50].load_forecast_w == {"h0_6": 600.0}` und `pv_forecast_w` gleich dem Archivwert.
  - `test_pv_ist_keeps_load_forecast`: spiegelbildlich.
  - `test_last_modell_uses_shadow_values`: Archiv `load` 600 W und `load_model` 450 W in allen Klassen → `load_forecast_w` hat überall 450.0.
  - `test_missing_model_value_falls_back_to_used_forecast`: `load_model` nur für `h0_6`, `load` für alle drei Klassen → `{"h0_6": 450.0, "h6_24": 600.0, "h24": 600.0}`; ein Slot ganz ohne `load_model` hat die Werte von `load`; die Zeile des Tages enthält `Lastmodell 99 %` (95 von 96 Slots). Dasselbe mit `load="profil"` und `load_profile`.
  - `test_pv_ist_on_dst_day_replaces_every_slot`: 25.10.2026, `pv="ist"` → 100 Slots, jeder mit `pv_forecast_w == {"h0_6": pv_w}`.
  - `test_default_file_is_unchanged`: ohne Optionen ist `model_dump()` gleich dem bisherigen Ergebnis (Vergleich mit einem Lauf `pv="prognose", load="verwendet"` und den bestehenden Erwartungen; Version 1).
  - `test_ohne_prognose_conflicts_with_new_options`: `main([... "--ohne-prognose", "--last", "modell"])` → 2, Meldung auf stderr.
  - `test_unknown_choice_exits_2`: `main([... "--last", "modelle"])` → 2.
  - Bestehende Tests mit `with_forecast=` werden auf die neuen Schlüsselwörter umgestellt; ihre Erwartungen bleiben.
- [ ] **Step 2:** `uv run pytest tests/unit/tools/test_export_days.py -q` → die neuen Tests scheitern.
- [ ] **Step 3:** Optionen, `LOAD_KIND`, Rückfall je Klasse und die Zeile je Tag umsetzen; Docstring des Moduls um die Optionen ergänzen.
- [ ] **Step 4:** `uv run pytest tests/unit/tools -q` grün, dann die ganze Prüfkette.
- [ ] **Step 5:** Doku: in `docs/betrieb.md` den Unterabschnitt mit Teil A und Lauf E:

  ```bash
  docker compose run --rm --entrypoint python ems-app \
    -m bindaems.tools.export_days --config /config/config.yaml \
    --von 2026-10-27 --bis 2026-11-22 --last modell --out /data/prog-modell.json
  docker compose run --rm --entrypoint python ems-app \
    -m bindaems.tools.scenario_days --tage /data/prog-modell.json --takt 15 --out /data/prog-modell.md
  ```

  Gewinn des Modells = bereinigte Kosten A − bereinigte Kosten E (A mit `--last profil`, falls „Lastmodell verwenden“ zwischendurch an war). In `docs/uebergabe.md` eine Zeile „Plan Lokale Prognosemodelle: Task 1 auf `main`, nicht ausgerollt, nur `tools/`“ und der Punkt unter „Offen an der Anlage“.
- [ ] **Step 6:** Commit `feat(tools): export_days wählt die Prognose je Größe (Lastmodell, Profil, Ist)`.

## Danach (nicht in diesem Plan)

- **Auswertung von Teil A** schreibe ich als kurzen Bericht nach `/mnt/project-files/plaene/` und trage die Werte in die Anlagenliste (C3) ein.
- **PV-Korrekturmodell** nur, wenn Entscheidung 5 es verlangt, als eigener Plan mit eigener Freigabe; frühestens nach einem Winter an Daten.
- **18.1 „Was lag auf dem Tisch?“** bleibt das laufende Messinstrument; Teil A hilft bei der Entscheidung, ob er sich lohnt.

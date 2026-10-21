# Aufgeschobene Kleinigkeiten vor dem gemeinsamen Ausrollen – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die aufgeschobenen Kleinigkeiten aus den Plänen 2c bis 6a, die an der Anlage oder in der Bedienung etwas bringen, vor dem gemeinsamen Pull nach dem 16.11.2026 (3d + 4b + 5a + 6a + 5b) beheben, damit der Pull sauberer an die Anlage geht.

**Architecture:** Kein neues Feature, eine Sammlung kleiner, voneinander unabhängiger Korrekturen in 13 Tasks, gruppiert nach Bereich (core, app, UI, Werkzeuge, Aufräumen, Doku). Jeder Punkt hat eine Nummer `K1` bis `K56`; der Betreiber kann einzelne Punkte abwählen, die Tasks schrumpfen dann entsprechend. Keine Migration, keine neue Abhängigkeit, keine neue Einstellung.

**Tech Stack:** wie bisher (Python 3.12, pytest, Hypothesis, SvelteKit mit Vitest und Playwright). Keine neuen Pakete.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md` (Absätze „Stand Plan …“ gehen vor). Quellen der Punkte: `docs/uebergabe.md` („Bewusst offen gelassen“, Listen „Aufgeschobene Kleinigkeiten aus Plan 2c-2a … 5b“), `/mnt/project-files/plaene/phase-*-abschluss.md`, `docs/superpowers/plans/2026-10-19-phase-6a-entscheidungen.md`, dazu der Befund „Wall Connector meldet 1 W“ (Entscheidung des Betreibers vom 21.10.2026, 10:24).

## Einordnung

- **Gesichtet:** rund 250 Einträge. Gestrichen, weil erledigt oder überholt: die Doku-Korrekturen aus 2c-2b (erledigt am 15.10.), die ganze Liste aus `4a-aufgeschoben.md` A bis C (Fix-Welle vom 18.10.), die HA-Kollisionsprüfung aus 4a und 6a (jetzt für Fahrzeuge, Wallboxen, Klima und System gemeinsam, `shared/config.py`), das Abschneiden nach 8 Slots (3c, Docstring und Prüfung im core vorhanden), `shown_number` in sieben Prüfsätzen des Simulators (2c-2a, zum Teil erledigt), der NaN-Fall bei Preis und Budget in `regulation.py` (3c, die Typen lassen kein NaN mehr zu) und das Warten des Nachtlaufs auf die Kalibrierung (3a, alle Schritte vor dem `finally` sind abgefangen).
- **Geprüft:** Jeder Punkt unten ist am Stand `2b0c25b` im Code nachgesehen (Datei und Zeile stehen beim Punkt).
- **Nicht vorgeschlagen** (bleiben in der Übergabe, mit Grund): siehe Abschnitt am Ende. Darunter die zwei größeren Punkte „Boost nach Neustart des core“ und „Quittierung mit der gesehenen Erkennung“; sie verdienen einen eigenen kleinen Plan.
- **Reine Testpflege** aus den Listen (private Felder in Tests, Hilfen aus fremden Testmodulen, `sleep`-Fenster) ist nicht aufgenommen, außer wo sie Läufe stört (K56).

## Zum gemeinsamen Ausrollen

- **Keine Migration.** `plans.trigger` ist ein freier Text; kein Punkt ändert eine Tabelle.
- **core ändert sich** in K1 bis K10 und K50/K51/K53 (Texte, Konstanten, Health-Felder, zwei kleine Regelungskorrekturen K2 und K4). Das Health-Schema bekommt zwei Felder (K3) und ein Grundcode kommt dazu (K5); deshalb gehen core und app **gemeinsam** aus demselben Stand, wie es für den gemeinsamen Pull ohnehin vorgesehen ist (Betriebshandbuch, „core und app immer gemeinsam“).
- **Konfiguration:** K6 prüft `thermal.devices.<name>.receive.<kanal>.unit` neu auf 0 bis 255. Eine `config.yaml` mit einem Wert außerhalb davon würde beim Start abgelehnt; die Beispielkonfiguration und das Betriebshandbuch nennen nur kleine Einheitennummern.
- **Ändert Pläne:** nur K18 (etwas mehr Autoenergie im Horizont, wenn die Abfahrt knapp hinter seinem Ende liegt) und indirekt K14/K15 (wann neu geplant wird).
- **Ändert die Regelung:** nur K2 (Tesla-Ladelimit bei „Complete“ an der EVCS) und K4 (Klimaanlage nach dem Ende des Hinweises nicht sofort wieder höher). Beide gehen in die sichere Richtung.

## Auswahl

Jeder Punkt trägt Nutzen und Risiko. **Empfohlen sind alle Punkte K1 bis K56.** Wer abwählen will: Am wenigsten verloren geht mit dem Aufräumen K50 bis K55 (ohne sichtbare Wirkung) und den UI-Kleinigkeiten K37 bis K44. Am meisten bringen K1, K3, K11 bis K16, K19 bis K22, K26, K28 und K45 bis K47 (Anlage und Ausrollen).

| Nr. | Kurz | Bereich | Nutzen | Risiko |
|---|---|---|---|---|
| K1 | Wall Connector 0 W bei offenem Schütz | core | hoch (Anzeige, Statistik ~13 kWh/Jahr) | klein |
| K2 | Ladelimit bleibt bei „Complete“ ohne Limit | core, Regelung | mittel | klein |
| K3 | Grundlast der Klimaanlage in Health und auf der Karte | core, UI | hoch (7 Tage Dry-Run) | klein |
| K4 | Klima: nach Hinweisende Wartezeit vor +1 | core, Regelung | mittel | klein |
| K5 | Eigener Grund „kein Echo“ | core, shared, UI | mittel | keins |
| K6 | CoE-Adapter robuster, Einheit 0–255 | core, shared | mittel | klein (Konfig) |
| K7 | Health `peak` nach Ende der Viertelstunde | core | klein | keins |
| K8 | Health `target_kw` im Leerlauf | core | klein | keins |
| K9 | Zahlen in Texten mit Komma | core, app | klein | keins |
| K10 | Eigener Befund „Auffrischen ist nie dringend“ | core | klein | keins |
| K11 | Plan-Vergleich auch mit `created_at` | app | mittel | keins |
| K12 | Keine falsche Zeile „Plan wartet …“ | app | mittel (~100 Logzeilen/Tag) | keins |
| K13 | Health beim Verbinden verwerfen | app | mittel | klein |
| K14 | Neu planen bei geänderter Unterstützungsschwelle | app | mittel | klein |
| K15 | Auslöser „Fahrzeug“ höchstens alle 5 min | app | hoch (Rechenlast) | klein |
| K16 | Warnung, wenn die Kürzung R1 nachbessert | app | mittel | keins |
| K17 | Plan-Ablage: kaputte Zeile blockiert nicht, Plan prüft Ordnung | app, shared | mittel | klein |
| K18 | Puffer für eine Abfahrt knapp hinter dem Horizont | app, Planer | mittel | klein |
| K19 | Datenbank-Sicherung um 04:00 statt 03:15 | app, Doku | hoch | keins |
| K20 | Lastmodell: beim Start neu bewerten | app | mittel | keins |
| K21 | Lastmodell: Schutz gegen NaN | app | mittel | keins |
| K22 | Wetterarchiv aufräumen | app | mittel | klein |
| K23 | Sensorauswahl ohne HA-Leser mit Grund | app | klein | keins |
| K24 | HA-Lesefehler der Lastprognose im Status | app | mittel | keins |
| K25 | Modell-Registry: Art prüfen, keine verwaiste Datei | app | klein | keins |
| K26 | Puffer des Entscheidungslogs begrenzen | app | hoch | klein |
| K27 | Blättern im Entscheidungslog nach dem Aufräumen | app | klein | keins |
| K28 | HA-Meldung bleibt nicht stehen | app | mittel | klein |
| K29 | HA-Zahl der Schwelle nur ganzzahlig | app | klein | keins |
| K30 | Ziel-API: 404 vor 422, leere Anfrage abgelehnt | app | klein | keins |
| K31 | `departure_on` nur als Datum | app | klein | keins |
| K32 | Fahrverhalten: Abfahrt um 00:00:00 | app | klein | keins |
| K33 | Automatik: Protokollfehler ergibt kein 500 | app | klein | keins |
| K34 | Automatik: geklemmter Wert pausiert nicht | app | klein | klein |
| K35 | Automatik: Stand vor der ersten Prüfung | app, UI | klein | keins |
| K36 | Trefferquote nach Fehler nicht stehen lassen | app | klein | keins |
| K37 | Zeitplan-Achse ab jetzt | UI | klein | keins |
| K38 | „Neu planen“ ohne 40 s Warten bei gleichem Wert | UI | mittel | keins |
| K39 | Hilfetext „Plan gegen Ist“ | UI | klein | keins |
| K40 | Tag bei „Fertig um“ und „lädt spätestens ab“ | UI | mittel | keins |
| K41 | Laden-Karte: Kleinigkeiten | UI | klein | keins |
| K42 | Prognosen: 409 als Hinweis, kein Abfragen im verborgenen Tab | UI | klein | keins |
| K43 | Prognoseband nie negativ | UI | klein | keins |
| K44 | Tarif-Editor: Datum, Fehler, Fokus | UI | mittel (EEG-Tarif) | keins |
| K45 | `verify_part2` thermal: Schritt 4 nach Fehler überspringen | Werkzeug | hoch (Ausrollen 6a) | keins |
| K46 | Watchdog-Probe gibt bei `docker stop` frei | Werkzeug | hoch (Sicherheit) | klein |
| K47 | `export_days`/`scenario_days` nur lesend, stderr, Code 2 | Werkzeug | mittel (Abnahme) | keins |
| K48 | Szenario `plan-netzladen` mit Kapazität der Konfiguration | Werkzeug | klein | keins |
| K49 | Restliche Prüfsätze mit `shown_number` | Werkzeug | klein | keins |
| K50 | Doppelte Konstanten zusammenführen | core, app, shared | klein | keins |
| K51 | Doppelte Regeln und Aliasse | core, shared, Werkzeug | klein | keins |
| K52 | Private Helfer öffentlich, doppelte HA-Helfer | app | klein | keins |
| K53 | Signaturen schärfen (`plan_blocks`, `assess`) | core | klein | keins |
| K54 | Grafana: Filter und Lücken | deploy | klein | keins |
| K55 | Minuszeichen einheitlich | UI | klein | keins |
| K56 | Testläufe in beliebiger Reihenfolge, Abbrüche abwarten | Tests | klein | keins |

## Global Constraints

- Deutsch, Anrede „du“ in Texten an den Betreiber; Commits deutsch im Stil `fix(core): …`, `fix(app): …`, `fix(ui): …`, `fix(tools): …`, `refactor(…): …`, `docs: …`. Keine Modellbezeichnungen im Repo.
- Vor jedem Commit: `ruff format --check`, `ruff check`, `mypy` (strict; `src` und `cerbo-watchdog`), `lint-imports`, `pytest` mit Abdeckung (≥ 75 % gesamt, ≥ 90 % `core/safety`, `core/control`, `core/actuators`); bei UI-Änderungen `pnpm check`, `pnpm lint`, `pnpm test`.
- In der Cloud vor uv: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Jeder Fix mit einem Test, der vorher scheitert (bei reinem Aufräumen genügt die grüne Kette).
- Ändert ein Punkt eine API-Antwort, werden die Vertragsdateien neu geschrieben (`docs/uebergabe.md`, „Befehle“).
- Scheitert ein Simulator-Szenario, liegt der Fehler zuerst in der Regelung.
- Kein Aktor ist live; nichts an der Anlage schalten.
- Tests, die eine `CoreRuntime` bauen, setzen `core.data_dir` unter `tmp_path` (sonst rot in der GitHub-CI).

## Review Focus

1. **Alte `config.yaml` an der Anlage** (K6): eine Einheit außerhalb 0–255 oder digitale Kanäle mit `unit`; erwartet: kleine Einheiten laden wie bisher. Test in Task 2.
2. **core neu, app alt oder umgekehrt** (K3, K5): Das UI-Schema muss die neuen Health-Felder als optional nehmen, damit eine kurze Versionsmischung beim Ausrollen die Seite „System“ nicht bricht. Test in Task 2.
3. **Auslöser „Fahrzeug“ bei schnellem Wechsel von Ziel oder Abfahrt** (K15): Eine Bedienung muss sofort neu planen, nur Ladelimit und Ladestand-Quelle werden gedrosselt. Test in Task 5.
4. **Kaputte Plan-Zeile nach dem neuen Validator** (K17): Pläne aus dem Archiv, die gegen die neue Prüfung verstoßen, dürfen weder den Start noch das Aufräumen blockieren. Test in Task 5.
5. **Datenbank-Ausfall über Stunden** (K26): Der Puffer bleibt begrenzt, die app läuft weiter, und nach der Rückkehr fehlen nur die verworfenen ältesten Einträge (die der core 7 Tage hält). Test in Task 7.

---

### Task 1: core – Wallboxen (K1, K2)

**Files:**
- Modify: `src/bindaems/core/adapters/twc.py` (`parse_vitals`, Zeile 47-67)
- Modify: `src/bindaems/core/control/assignment.py` (Zeile 614-621)
- Test: `tests/unit/core/adapters/test_twc.py`, `tests/unit/core/control/test_assignment.py`

**Interfaces:** keine neuen.

**K1 – Wall Connector meldet bei offenem Schütz 0 W.** Heute rechnet `parse_vitals` `power_w = Σ current × voltage`; im Leerlauf (Prüfprotokoll 17.1-9, `docs/verification/2026-10-11-pruefprotokoll-teil1.md`) ergibt das 0,2 A × 7,4 V = 1,48 W. Entscheidung (Betreiber 21.10.2026): `power_w = 0.0`, wenn `contactor_closed` ausdrücklich `False` ist; bei `True` oder fehlendem Feld wird gerechnet wie bisher. Phasenströme und -spannungen bleiben unverändert (der Sicherungsschutz sieht weiter die Messung). Keine allgemeine Totzone.

- [ ] **Step 1: Test** `test_open_contactor_reports_zero_power`: Vitals `contactor_closed=False`, `currentA/B/C_a = 0.2/0.0/0.2`, `voltageA/B/C_v = 0.0/2.6/7.4` → `result["power_w"][0] == 0.0`, und `l?.current_a` sind weiter 0,2/0,0/0,2. Dazu `test_missing_contactor_flag_still_computes_power` (Feld fehlt → 1,48 W, `approx`).
- [ ] **Step 2:** Test läuft rot (1,48 statt 0,0).
- [ ] **Step 3:** In `parse_vitals` nach der Schleife: ist `_flag(data, "contactor_closed") is False`, gilt `power = 0.0`.
- [ ] **Step 4:** Tests grün; die Simulator-Szenarien laufen mit (`pytest tests/integration/test_sim_scenarios.py`), weil der Simulator `parse_vitals` nutzt.
- [ ] **Step 5: Commit** `fix(core): Wall Connector meldet bei offenem Schütz 0 W`

**K2 – „Complete“ an der EVCS ohne bekanntes Ladelimit überschreibt kein bekanntes Limit.** Heute setzt `assignment.py:614-621` `self._charged[key] = (session, None)`, und `None` heißt dort „jedes Limit“: Ein später höheres Limit (für „Geplant“) hebt „geladen“ nicht mehr auf. Entscheidung: Ist `limit is None` und gilt der gemerkte Eintrag derselben Session, bleibt das gemerkte Limit.

- [ ] **Step 6: Test** `test_complete_without_limit_keeps_known_limit`: Tesla an der EVCS, Tessie meldet Limit 80, EVCS meldet `complete` → geladen bei 80; nächster Zyklus `complete` mit unbekanntem Limit; dann Limit 90 → das Fahrzeug gilt nicht mehr als geladen.
- [ ] **Step 7:** rot, dann die Bedingung in `assignment.py` ergänzen, grün.
- [ ] **Step 8: Commit** `fix(core): Complete ohne Ladelimit behält das bekannte Limit`

---

### Task 2: core – Klimaanlage (K3, K4, K5, K6)

**Files:**
- Modify: `src/bindaems/core/control/regulation.py` (`_Thermal`, `_thermal_json` um Zeile 638), `src/bindaems/core/runtime.py` (`_thermal_health`, Zeile 1136)
- Modify: `src/bindaems/core/control/thermal.py` (`_rule_less_end` Zeile 293, `_reset_reason` Zeile 302)
- Modify: `src/bindaems/shared/decisions.py` (Texte, Zeile 75-88)
- Modify: `src/bindaems/core/adapters/coe.py` (Zeile 165-190, 280), `src/bindaems/shared/coe.py` (Zeile 82-84), `src/bindaems/shared/config.py` (`CoeChannel`, Zeile 247-278)
- Modify: UI-Schema von Health (`ui/src/lib/schemas.ts`, Block `thermal`) und die Karte der Klimaanlage (`ui/src/lib/system/`)
- Test: `tests/unit/core/control/test_thermal*.py`, `tests/unit/core/control/test_regulation*.py`, `tests/unit/core/adapters/test_coe.py`, `tests/unit/shared/test_coe.py`, `tests/unit/shared/test_config.py`, Vitest der Karte

**Interfaces:**
- Produces: Health `thermal.<name>.base_w: float | null` (gelernte Leistung bei Versatz 0), `thermal.<name>.spare_w: float | null` (zuletzt freigebbare Leistung, `freeable_w`); Grundcode `thermal_no_echo`.

**K3 – Grundlast sichtbar.** `_Thermal.base_w` lernt nur, wenn der entschiedene Versatz 5 min 0 war; im Dry-Run kann er einfrieren, ohne dass man es sieht (6a, R28). Entscheidung: `base_w` und `spare_w` (gerundet auf ganze W) kommen in den Health-Block jeder Klimaanlage; die Karte auf „System“ zeigt eine Zeile „Grundlast {base} W · freigebbar {spare} W“ („–“ ohne Wert). Im UI-Schema sind beide Felder optional (Review Focus 2).
- [ ] Tests: `test_thermal_health_shows_base_and_spare` (nach gelernter Grundlast 400 W und Leistung 900 W bei Wunsch +1 → `base_w == 400`, `spare_w == 500`); Vitest: Karte zeigt „Grundlast 400 W · freigebbar 500 W“, ohne Felder „–“, und ein Health ohne die Felder besteht das Schema. Rot → umsetzen → grün. Vertragsdateien neu schreiben.
- [ ] Commit `feat(core): Grundlast der Klimaanlage in Health und auf der Karte`

**K4 – Nach dem Ende des Hinweises nicht sofort wieder höher.** `_rule_less_end` setzt den Wunsch auf 0, lässt aber `_export_since` stehen; im nächsten Zyklus kann `_rule_more` gleich +1 wählen (−2 → 0 → +1). Entscheidung: `_rule_less_end` setzt `self._export_since = obs.now`; die Einspeisung muss danach die volle Wartezeit anstehen.
- [ ] Test `test_hint_end_restarts_the_export_timer`: Wunsch −2 mit Hinweis „weniger“, Einspeisung steht seit 20 min an, Hinweis endet → Zyklus 1: 0 (`thermal_less`); Zyklus 2 eine Sekunde später: weiter 0; nach Ablauf der Wartezeit der Strategie: +1. Rot → umsetzen → grün; die Szenarien `klima-*` laufen mit.
- [ ] Commit `fix(core): Klimaanlage wartet nach dem Ende des Hinweises vor dem nächsten Schritt`

**K5 – Eigener Grund „kein Echo“.** Heute wird „nicht steuerbar“ (kein Echo oder gesperrt) als `thermal_stale` mit dem Text „Keine Werte der Klimaanlage, Versatz 0“ geloggt, obwohl Werte da sind. Entscheidung: neuer Code `thermal_no_echo` mit dem Text „Klimaanlage nicht steuerbar (kein Echo oder gesperrt), Versatz 0“; `_reset_reason` gibt ihn, wenn `obs.fresh` und nicht `obs.controllable`. Der Grund nach einem Zurücksetzen bleibt `thermal_off` (kein neuer Code).
- [ ] Test `test_reset_reason_without_echo_is_no_echo` (fresh, nicht steuerbar → `thermal_no_echo`; nicht fresh → weiter `thermal_stale`); Test, dass jeder Code der Strategie einen Text in `shared/decisions.py` hat (falls es ihn noch nicht gibt). UI: falls die Codes im UI aufgezählt sind, dort ergänzen. Rot → umsetzen → grün.
- [ ] Commit `fix(core): eigener Grund, wenn die Klimaanlage kein Echo hat`

**K6 – CoE robuster.** Entscheidungen je Unterpunkt:
- `permit()` (`core/adapters/coe.py:165-167`) fängt beim Zurücknehmen `OSError` ab und loggt ihn (Warnung), statt den Zyklus zu stören.
- `set_offset` setzt `_held_k` erst nach erfolgreichem `_send` (`:189-190`).
- Die Logzeile bei „stale“ (`:280`) nennt kein `retry_in_s=0.0` mehr (eigene Logzeile ohne Wiederholungszeit).
- `offset_packet` (`shared/coe.py:82-84`) lehnt nicht endliche Werte mit `ValueError("Versatz ist keine endliche Zahl.")` ab.
- `CoeChannel.unit` hat den Bereich 0 bis 255 (ein Byte im Paket). Digitale Kanäle mit `unit`/`scale` und das Header-Byte 1 bleiben, wie sie sind (erst an der Anlage bestätigen, siehe „Nicht vorgeschlagen“).
- [ ] Tests: `test_permit_revoke_survives_oserror`, `test_held_offset_unchanged_when_send_fails`, `test_stale_log_has_no_retry`, `test_offset_packet_rejects_non_finite` (inf und nan, deutscher Text), `test_channel_unit_out_of_range_is_rejected` (256 abgelehnt, 0 und 7 angenommen) und `test_example_config_still_loads` (Review Focus 1, falls nicht vorhanden). Rot → umsetzen → grün.
- [ ] Commit `fix(core): CoE-Adapter übersteht Sendefehler, Einheit 0 bis 255`

---

### Task 3: core – Anzeige und Texte (K7, K8, K9, K10)

**Files:**
- Modify: `src/bindaems/core/control/peak_guard.py` (`idle` Zeile 423-439, `health` Zeile 437-461), `src/bindaems/core/control/regulation.py` (Aufruf von `health`, Zeile 595)
- Modify: `src/bindaems/core/safety/layer.py` (Zeile 61-64, 172-182)
- Modify: `src/bindaems/core/control/intent.py:273-274`, `src/bindaems/app/control/intent.py:428, 528-531`, `src/bindaems/app/ha/commands.py:224`
- Test: `tests/unit/core/control/test_peak_guard.py`, `tests/unit/core/safety/test_layer.py`, Tests der Intent-Prüfung in core und app

**K7 – `peak` zeigt nach dem Ende der Viertelstunde nicht mehr `exceeded`.** Entscheidung: `PeakGuard.health(wall_now)` meldet `gap` statt `exceeded`, sobald `wall_now >= quarter_end` und die Regelung ruht (wie `alarms(wall_now)`); der Aufrufer in `regulation.py` reicht die Wanduhr durch.
- [ ] Test `test_health_drops_exceeded_after_the_quarter_while_idle`. Rot → umsetzen → grün.

**K8 – `target_kw` im Leerlauf.** Entscheidung: `idle()` setzt `_target_kw` auf `control.peak_target_kw` aus dem Konstruktor zurück (ohne Wert `None`); `state` bleibt dabei unverändert.
- [ ] Test `test_idle_forgets_the_plan_budget`: nach einem Schritt mit Plan-Budget 3,5 kW und `idle()` zeigt Health `target_kw == control.peak_target_kw`.

**K9 – Zahlen mit Komma.** Alle Stellen mit `:g` in Texten an den Betreiber nehmen `de()` aus `shared/numbers.py` (core `intent.py:273-274`, app `intent.py:428` und `528-531`, `ha/commands.py:224`).
- [ ] Test `test_support_threshold_text_uses_comma`: Schwelle 19,5 % unter der USV-Reserve → Text enthält „19,5 %“, nicht „19.5 %“ (core und app je ein Test).

**K10 – Auffrischen ist nie dringend.** Entscheidung: Ist ein Befehl dringend und auffrischend, bekommt er den neuen Befund `urgent.refresh` mit dem Text „Auffrischen ist nie dringend“ statt `urgent.ignored` („nicht Richtung sicherer Zustand“). Heute erzeugt kein Aufrufer diese Kombination; es geht nur um den richtigen Text.
- [ ] Test `test_urgent_refresh_gets_its_own_finding`.
- [ ] **Commit** (ein Commit je Punkt oder einer für K7 bis K10): `fix(core): Health und Texte nach Leerlauf, Komma in Zahlen`

---

### Task 4: app – Übergabe des Plans (K11, K12, K13)

**Files:**
- Modify: `src/bindaems/app/planning/execution.py:45-55` (`core_has_plan`), `src/bindaems/app/planning/sender.py:119-126`, `src/bindaems/app/core_link.py:372-389`
- Test: `tests/unit/app/planning/test_execution.py`, `tests/unit/app/planning/test_sender.py`, `tests/unit/app/test_core_link.py`

**Interfaces:**
- Produces: `core_has_plan(health: Mapping[str, Any] | None, plan: Plan, *, grid_charge_allowed: bool) -> bool` vergleicht `id`, `grid_charge_allowed` und `created_at` (ISO aus Health, als `datetime` verglichen). Die Signatur bleibt, wie sie heute ist; nur der Vergleich wird strenger.

**K11:** Nach einer Wiederherstellung der Datenbank kann eine `id` wiederkehren. `core_has_plan` vergleicht zusätzlich `created_at`.
- [ ] Test `test_same_id_other_created_at_is_not_the_plan` → `False`.

**K12:** Der Sender prüft zuerst, ob der core den Plan schon hat (`core_has_plan`), und meldet dann „aktuell“, bevor er auf vertrautes Health wartet. Die Zeile „Plan wartet, bis der core mit der Absicht regelt“ erscheint nur noch, wenn der core den Plan nicht hat.
- [ ] Test `test_no_wait_line_when_core_already_has_the_plan` (Health mit älterer `intent_version`, aber gleichem Plan → kein Logeintrag `plan_waits`, kein Senden).

**K13:** `_connected` verwirft gespeichertes Health (`self._live.health = None`) und zählt `_disconnects` hoch wie `_disconnected`.
- [ ] Test `test_connect_discards_health_from_before` (Health gesetzt, dann `_connected` → `health is None`).
- [ ] **Commit** `fix(app): Plan-Übergabe vergleicht created_at und verwirft altes Health`

---

### Task 5: app – Planer (K14, K15, K16, K17, K18)

**Files:**
- Modify: `src/bindaems/app/planning/triggers.py` (`Fingerprint`, `changed`, Zeile 31-100), `src/bindaems/app/planning/service.py` (`_watch`, Zeile 324-352)
- Modify: `src/bindaems/app/planning/intents.py` (`_keep_rule_r1`, Zeile 74-121)
- Modify: `src/bindaems/app/planning/store.py:118-127`, `src/bindaems/shared/plan.py` (`Plan`, Zeile 160-179)
- Modify: `src/bindaems/app/planning/vehicles.py` (`horizon_need`, Zeile 141-153)
- Test: `tests/unit/app/planning/test_triggers.py`, `test_service.py`, `test_intents.py`, `test_store.py`, `tests/unit/shared/test_plan.py`, `test_vehicles.py`

**K14 – Neu planen bei geänderter Unterstützungsschwelle.** Der Fingerabdruck bekommt die wirksame Schwelle (`self._support_pct()`); ändert sie sich, wird mit dem vorhandenen Auslöser `"settings"` neu geplant (kein neuer Literal, kein Schema).
- [ ] Test `test_support_threshold_change_requests_a_plan` (70 → 60 → genau eine Anfrage `"settings"`).

**K15 – Auslöser „Fahrzeug“ gedrosselt.** Ändern sich nur Ladelimit oder die Quelle des Ladestands, plant der Dienst höchstens alle 5 min neu (`VEHICLE_REPLAN_MIN_S = 300`). Ändern sich die Menge der geplanten Autos, Ziel oder Abfahrt, plant er sofort (Review Focus 3).
- [ ] Tests: `test_charge_limit_flapping_replans_at_most_every_five_minutes` (Limit wechselt alle 30 s über 10 min → höchstens 3 Anfragen `"vehicles"`), `test_target_change_replans_immediately` (zweite Zieländerung 40 s nach der ersten → sofort eine Anfrage).

**K16 – Kürzung nach R1 sichtbar.** `_keep_rule_r1` loggt eine Warnung `plan_r1_trimmed` (mit Slot und gekürzten kWh), wenn sie mehr als `R1_TOL_KWH` kürzt. Damit bleibt ein echter Fehler des Optimierers im Log sichtbar.
- [ ] Test `test_r1_trim_is_logged` (mit `structlog.testing.capture_logs`); unter der Toleranz kein Eintrag.

**K17 – Plan-Ablage und Plan-Prüfung.** Erst die Ablage, dann der Validator (Review Focus 4):
- `store.py`: Ein Plan, der sich nicht lesen lässt, wird beim Aufräumen übersprungen und geloggt (`plan_row_unreadable`), statt die Transaktion zurückzurollen; `latest()` überspringt ihn ebenso.
- `shared/plan.py`: `Plan` prüft `valid_until > created_at` und streng aufsteigende, eindeutige `slots[].start` (`model_validator(mode="after")`, Fehlertexte deutsch).
- [ ] Tests: `test_cleanup_skips_an_unreadable_row` (eine kaputte JSON-Zeile, eine alte gültige → die gültige wird gelöscht), `test_plan_rejects_unsorted_slots`, `test_plan_rejects_valid_until_before_created`. Bestehende Fixtures, die Pläne bauen, müssen weiter gelten (sonst ist der Fixture falsch, nicht der Validator).

**K18 – Puffer für eine Abfahrt knapp hinter dem Horizont.** `horizon_need` rechnet die Leistung nach dem Horizont wie `latest_start`: geteilt durch `LATEST_START_MARGIN` (1,1) und abzüglich `LATEST_START_BUFFER` (15 min). Wirkung: etwas mehr Autoenergie im Horizont, nie weniger.
- [ ] Test `test_horizon_need_keeps_the_latest_start_margin` (Abfahrt 30 min hinter dem Horizont, Bedarf 10 kWh, 11 kW → Bedarf im Horizont = 10 − 11 · (0,5 h − 0,25 h)/1,1 kWh, nicht 10 − 5,5).
- [ ] **Commits** je Punkt, etwa `fix(app): Neuplanung bei geänderter Unterstützungsschwelle`, `fix(app): Auslöser Fahrzeug bei Ladelimit höchstens alle 5 min`, `fix(app): Kürzung nach Regel R1 im Log`, `fix(app): kaputte Plan-Zeile blockiert das Aufräumen nicht`, `fix(app): Puffer für eine Abfahrt knapp hinter dem Horizont`

---

### Task 6: app – Lastmodell, Prognose, Sicherung (K19 bis K25)

**Files:**
- Modify: `src/bindaems/app/runtime.py:120` (`BACKUP_AT`), `docs/betrieb.md` (Zeilen mit „03:15“ zur Sicherung)
- Modify: `src/bindaems/app/learning/service.py` (`run`, Zeile 546-562), `features.py:115-178`, `model.py:78, 107-109`, `weather.py`, `ha_sensors.py:97-100`, `registry.py:116-144, 198-218`
- Modify: `src/bindaems/app/forecast/load.py` (`component_status`, Zeile 403-411)
- Test: entsprechende Dateien unter `tests/unit/app/learning/` und `tests/unit/app/forecast/`, `tests/unit/test_docs.py`

**K19 – Sicherung um 04:00.** `BACKUP_AT = (4, 0)`: nach Nachtlauf (02:30), Abrechnung (02:45), Training (03:15, bis 10 min) und Fahrverhalten (03:40); die Sicherung enthält so auch das neue Modell und die Profile. Betriebshandbuch nachziehen.
- [ ] Test `test_backup_runs_after_the_night_jobs` (`BACKUP_AT` später als `TRAIN_AT` + 10 min und als die Uhrzeit des Fahrverhaltens).

**K20 – Neu bewerten beim Start.** Nach `_load_latest()` bewertet der Dienst neu, wenn der Schalter an ist und die gespeicherte Aktivierung fehlt oder `reason == "disabled"` lautet.
- [ ] Test `test_start_reevaluates_a_disabled_decision_when_switched_on`.

**K21 – NaN.** `_mean` in `features.py` nimmt nur endliche Werte; `predict` gibt für nicht endliche Vorhersagen NaN zurück (statt 0), damit der vorhandene Filter in `load.py:302` greift; `load_model` wirft `TrainingError`, wenn `max_w` nicht endlich oder ≤ 0 ist.
- [ ] Tests `test_recent_load_ignores_nan`, `test_nan_prediction_is_not_zero`, `test_model_with_bad_max_is_unusable`.

**K22 – Wetterarchiv aufräumen.** `WeatherArchive.cleanup(before: datetime) -> int` löscht Slots älter als 120 Tage (Training braucht 112); der Nachtlauf ruft es nach dem Aufräumen des Prognosearchivs. Die Nachholung stempelt `issued_at` nach dem Abruf.
- [ ] Tests `test_weather_cleanup_keeps_120_days`, `test_backfill_stamps_after_fetch`.

**K23 – Ohne HA-Leser ein Grund.** `ha_sensors` gibt ohne Leser den Grund „Home Assistant ist nicht eingerichtet.“ zurück (wie `NOT_CONFIGURED`); die Einstellungen markieren dann keine Sensoren als „nicht in HA gefunden“.
- [ ] Test der Route und ein Vitest des Editors.

**K24 – HA-Lesefehler im Status.** Ist der letzte Lesevorgang aus HA gescheitert (`_ha_ok` falsch), sagt der Status der Komponente „Verlauf aus HA nicht lesbar, Profil aus der Abrechnung.“ bei `ok=True` (keine Benachrichtigung; die Lastprognose läuft ja weiter).
- [ ] Test `test_status_mentions_failed_ha_history`.

**K25 – Registry.** `decide` und das Zurücksetzen von `active` filtern auf `kind == KIND`; scheitert die Transaktion nach dem Schreiben der Datei, wird die Datei gelöscht.
- [ ] Tests `test_decide_ignores_other_kinds`, `test_failed_commit_removes_the_model_file`.
- [ ] **Commits** je Punkt (`fix(app): …`).

---

### Task 7: app – Entscheidungslog, HA, Bedienen (K26 bis K31)

**Files:**
- Modify: `src/bindaems/app/control/decisions.py` (Zeile 155-164, 231-250)
- Modify: `src/bindaems/app/ha/notify.py` (Zeile 258-272), `src/bindaems/app/ha/commands.py:215-226`
- Modify: `src/bindaems/app/control/intent.py:533-550`, `src/bindaems/app/control/routes.py:72-82`
- Test: `tests/unit/app/control/test_decisions.py`, `tests/unit/app/ha/test_notify.py`, `test_commands.py`, `tests/unit/app/control/test_intent.py`, `test_routes.py`

**K26 – Puffer begrenzen.** `DecisionCollector` hält höchstens `BUFFER_MAX = 10_000` Einträge; darüber fallen die ältesten weg, mit einer Warnung `decisions_dropped` je Ausfallphase (nicht je Eintrag). Der core hält seine Entscheidungen 7 Tage; beim nächsten Verbinden holt `catch_up` nach, was noch da ist.
- [ ] Test `test_buffer_is_capped_while_the_database_is_down` (Review Focus 5: 12 000 Einträge bei kaputter Datenbank → 10 000 im Puffer, eine Warnung, nach Rückkehr werden 10 000 geschrieben).

**K27 – Blättern nach dem Aufräumen.** Fehlt die Zeile des `before`-Zeigers, gilt `seq < before` statt einer leeren Seite.
- [ ] Test `test_before_cursor_on_a_cleaned_row_still_pages`.

**K28 – Meldung in HA bleibt nicht stehen.** `_dismiss_at[item]` wird erst gelöscht, wenn die neue Meldung angekommen ist; verschwindet der Alarm vorher wieder, entfernt die app die alte Meldung.
- [ ] Test `test_returning_alarm_that_vanishes_again_is_dismissed`.

**K29 – HA-Zahl der Schwelle ganzzahlig.** Wie beim Ziel-SOC (`commands.py:160-170`) nur ganze Zahlen (`"45"`, `"45.0"`); `"45.5"` und `"4_5"` werden mit Text abgelehnt.
- [ ] Test `test_support_threshold_rejects_fractions_and_underscores`.

**K30 – Ziel-API.** `_check` prüft zuerst den Fahrzeugschlüssel (unbekannt → 404), dann die Bereiche; eine Anfrage ohne Ziel und ohne Abfahrt wird mit 422 „Ziel oder Abfahrt angeben.“ abgelehnt und nicht protokolliert.
- [ ] Tests `test_unknown_vehicle_with_bad_target_is_404`, `test_empty_target_request_is_rejected`.

**K31 – `departure_on` nur als Datum.** Ein Validator nimmt nur `YYYY-MM-DD` an (kein Zeitstempel, keine Zahl).
- [ ] Test `test_departure_on_rejects_timestamps`.
- [ ] **Commits** je Punkt.

---

### Task 8: app – Fahrverhalten (K32 bis K36)

**Files:**
- Modify: `src/bindaems/app/learning/driving_days.py:98-100, 171`, `driving_auto.py` (Zeile 80, 114, 204, 282-313, 455-486), `driving_service.py:297-330`
- Modify: UI-Schema und Text des Automatik-Stands (`ui/src/lib/schemas.ts`, Karte auf „Laden“)
- Test: `tests/unit/app/learning/test_driving_days.py`, `test_driving_auto.py`, `test_driving_service.py`, Vitest

**K32:** Eine Abfahrt genau um 00:00:00 zählt am Rand mit (eingeschlossenes Ende).
- [ ] Test `test_departure_at_midnight_is_kept`.

**K33:** Scheitert der Eintrag ins Änderungsprotokoll nach dem gespeicherten Schalter, loggt die app einen Fehler und antwortet trotzdem mit Erfolg (der Schalter ist gespeichert).
- [ ] Test `test_audit_failure_does_not_fail_the_switch`.

**K34:** Klemmt der core Ziel oder Abfahrt (`clamped`), gilt der geklemmte Wert als von der Automatik gesetzt; der nächste Check pausiert deshalb nicht.
- [ ] Test `test_clamped_result_does_not_pause` (Fake klemmt Ziel 95 → 90; zwei Checks; Stand bleibt `applied`).

**K35:** Vor der ersten Prüfung nach dem Start zeigt ein eingeschaltetes Auto den neuen Stand `pending` mit „Automatik prüft in Kürze.“ statt „Das Auto steht nicht auf „Geplant““. `State` und UI-Schema bekommen `pending`.
- [ ] Test `test_enabled_vehicle_is_pending_before_the_first_check` und ein Vitest.

**K36:** Scheitert die Berechnung eines Fahrzeugs, wird seine Trefferquote verworfen (die Seite zeigt dann „keine Daten“ statt eines alten Werts).
- [ ] Test `test_failed_step_drops_the_hit_rate`.
- [ ] **Commits** je Punkt.

---

### Task 9: UI – Zeitplan und Laden (K37 bis K41)

**Files:**
- Modify: `ui/src/lib/plan/chart.ts:33-45`, `ui/src/routes/zeitplan/+page.svelte:22-24, 89-122`, `ui/src/lib/plan/PlanReview.svelte`, `ui/src/lib/plan/PlanHeader.svelte:54`, `ui/src/lib/charging/view.ts:389-395, 433-456, 495`, `ui/src/lib/charging/VehicleCard.svelte:160-163, 201`, `ui/src/lib/format.ts`
- Test: Vitest neben den Dateien, ggf. E2E `tests/e2e/`

**Interfaces:**
- Produces: `formatDayTime(at: Date, now: Date): string` in `format.ts`: „HH:MM“ für heute, „morgen HH:MM“, sonst „Mi 22.10. HH:MM“ (wie `departureText`).

**K37:** Die Zeitachse beginnt beim Slot von jetzt (`max(erster Slot, Slot von jetzt)`), die Linie „jetzt“ bleibt.
**K38:** Ändert „Netzladen erlaubt“ die Version der Einstellungen nicht (gleicher Wert) oder passt der Plan schon, wartet die Seite nicht; nach 40 s ohne neuen Plan erscheint „Kein neuer Plan nach 40 s. Die Seite zeigt den letzten.“ (Fake-Timer-Test).
**K39:** Unter „Plan gegen Ist“ steht „Plan zeigt das Archiv der app, auch Pläne, die der core nicht bekommen hat.“
**K40:** „Fertig um“ und „lädt spätestens ab“ nehmen `formatDayTime`.
**K41:** Ein leeres Feld zeigt in der Fehlermeldung „(leer)“ statt „“; „geschätzt“ steht auf der Karte nur einmal; der Kopf des Zeitplans vergleicht `car.limit_pct` mit dem Ziel und zeigt den vorhandenen Hinweis „Ladelimit“.
- [ ] Je Punkt ein Vitest, der vorher scheitert, dann umsetzen; `pnpm check`, `lint`, `test`; betroffene E2E-Tests laufen.
- [ ] **Commits** `fix(ui): …` je Punkt oder zusammengefasst je Datei.

---

### Task 10: UI – Prognosen und Tarif-Editor (K42, K43, K44)

**Files:**
- Modify: `ui/src/routes/prognosen/+page.svelte:58-109`, `ui/src/lib/learning/ModelCard.svelte:58-59`, `ui/src/lib/charts/options.ts:194`, `ui/src/lib/settings/model.ts:137, 459, 464`, `ui/src/lib/settings/SettingsEditor.svelte:88-96, 168`
- Test: Vitest neben den Dateien

**K42:** Eine Antwort 409 auf „Jetzt neu trainieren“ erscheint als Hinweis (Stufe `info`, wie bei `recompute`); die Abfrage im Takt ruht, solange der Tab verborgen ist (`document.visibilityState === 'hidden'`, wie `lib/activity.ts`).
**K43:** Die Höhe des Bandes ist `Math.max(0, p75 − p25)`.
**K44:** Das Datum muss ein echtes Kalenderdatum sein (`2026-02-30` wird abgelehnt: „Datum gibt es nicht“); eine Änderung der Struktur löscht nur die Fehler unter `tariff.*` und den Hinweis „Bitte die markierten Felder prüfen.“, wenn keine Fehler bleiben; findet `focusFirstError` kein Feld, fokussiert er den Hinweis.
- [ ] Je Punkt ein Vitest, der vorher scheitert; dann umsetzen; UI-Kette grün.
- [ ] **Commits** `fix(ui): …`

---

### Task 11: Werkzeuge an der Anlage (K45 bis K49)

**Files:**
- Modify: `src/bindaems/tools/part2/thermal.py:309-366`, `src/bindaems/tools/watchdog_probe.py:418-495`, `src/bindaems/tools/export_days.py:86-95, 304`, `src/bindaems/tools/scenario_days.py:102-104`, `src/bindaems/shared/logging.py` (Ausgabe für Werkzeuge), `src/bindaems/tools/sim/scenarios.py:271-278, 1503-1514` und die Prüfsätze in Zeile 796, 802, 833, 1194, 1337, 1342, 1476, 1553, 1826, `src/bindaems/tools/sim/runner.py:112-130`
- Modify: `docs/betrieb.md` (Abschnitt 14 und der Absatz zu `verify_part2` um Zeile 1223, 2231)
- Test: `tests/unit/tools/…` passend zu jeder Datei

**K45:** `_offset` und `_zero` geben zurück, ob der Schritt bestanden hat; ist Schritt 2 gescheitert, überspringt `run()` Schritt 4 mit dem Befund „übersprungen, weil Schritt 2 nicht bestanden hat“.
- [ ] Test `test_step4_skipped_after_failed_step2`.

**K46:** Die Probe behandelt SIGTERM (und SIGHUP) wie SIGINT: `loop.add_signal_handler(signal.SIGTERM, task.cancel)`; die vorhandene Freigabe im `finally` läuft (höchstens `RELEASE_ATTEMPT_S` = 2 s, passt in die 10 s von `docker stop`); `main` gibt nach Abbruch 143 und „Abgebrochen“ zurück. Betriebshandbuch 14 nachziehen.
- [ ] Test `test_cancel_releases_the_watchdog` (Task abbrechen → Heartbeat 0 gesendet) und `test_sigterm_handler_is_registered`.

**K47:** `export_days` öffnet die Datenbank nur lesend (`sqlite:///file:…?mode=ro&uri=true`) und liest die Einstellungen direkt (kein `SettingsService`, der Version 1 anlegt); ein relatives `data_dir` gilt relativ zur Konfigurationsdatei; Logs der Werkzeuge gehen nach stderr; `scenario_days` gibt bei einer Ausnahme 2 zurück.
- [ ] Tests `test_export_days_does_not_write`, `test_relative_data_dir_follows_the_config`, `test_tool_logs_go_to_stderr`, `test_scenario_exception_exits_2`.

**K48:** `plan-netzladen` rechnet den Anstieg aus `cfg.battery.usable_kwh` statt fest 30 kWh (`SimResult` bekommt `usable_kwh`).
- [ ] Test `test_plan_netzladen_scales_with_capacity` (60 kWh → kein falscher Befund).

**K49:** Die neun restlichen Prüfsätze nehmen `shown_number`.
- [ ] Test `test_violation_text_never_reads_like_the_limit` (ein Verstoß um 0,001 zeigt eine Stelle mehr).
- [ ] **Commits** `fix(tools): …` je Punkt.

---

### Task 12: Aufräumen ohne Wirkung (K50 bis K56)

Reines Aufräumen; die Kette muss vorher und nachher grün sein, neue Tests nur, wo eine Konstante sonst unbewacht wäre.

- **K50 – Konstanten:** `SAME_K` (`core/safety/offset.py:30`) und `_TOLERANCE[SETPOINT_OFFSET]` (`core/control/actuator.py:36`) und das Literal in `tools/part2/thermal.py:405` → eine Konstante `OFFSET_SAME_K` in `core/control/actuator.py`. `HIGH_PERCENTILE_INDEX` nur in `shared/plan.py`, mit einer Hilfe `high_percentile(prices)`, die `fallback.py` nutzt. `GRID_CHARGE_CAP` nur in `core/safety/limits.py`, `core/control/intent.py` importiert sie (der vorhandene Test bleibt). `EMPTY_TOL_KWH = R1_TOL_KWH` in `plausibility.py`; die Docstrings im Optimierer sagen „untere Grenze `min(Reserve, SOC₀)`“ (die Binärvariable des letzten Slots bleibt, um die Lösezeit nicht zu berühren). `THERMAL_DEFAULT_LABEL` aus dem Standard von `CoeOffsetConfig`. Grenzen des Ziel-SOC als `TARGET_SOC_MIN/MAX` in `shared/control.py`, die app importiert sie. `_KWH_EPS` und der Filter `EV_ON_W` einmal in `app/planning/vehicles.py`.
- **K51 – Regeln und Aliasse:** `Command.refreshes` (Property in `shared/control.py`) ersetzt die drei Fassungen in `core/safety/layer.py:172`, `core/control/executor.py:335-337`, `tools/sim/runner.py:404`; die fünf `_Key`-Aliasse nehmen `Key` aus `core/control/last_values.py`. Der Modul-Docstring von `assignment.py` nennt die Boost-Regel wie `update` und `_bind`; `NO_SESSION` aus `assignment.py` ersetzt `_NOBODY` in `charging.py`.
- **K52 – Private Helfer:** `_trusted`, `_health_vehicles`, `_slots_before` werden öffentlich benannt (`trusted`, `health_vehicles`, `slots_before`); `number()` und `ha_ref()` aus `app/consumers/values.py` ersetzen die Kopien in `learning/ha_sensors.py`, `consumers/energy.py` und `forecast/load.py` (Verträge von import-linter prüfen; erlaubt er den Import nicht, kommen die Helfer nach `app/ha_refs.py`).
- **K53 – Signaturen:** `Support.update(..., plan_blocks: bool)` ohne Standard; `assess(..., *, support=…, active=…)`.
- **K54 – Grafana:** Leistung der Klimaanlage mit `"phase" = 'total'`; der Versatz mit `fill(null)` statt `fill(previous)` (`deploy/grafana/bindaems.json:411, 422`); der Test des Dashboards zieht nach.
- **K55 – Minuszeichen:** Die Karte der Klimaanlage (`ui/src/lib/system/thermal.ts:11`) und `ui/src/lib/forecast/view.ts:46` nehmen den Formatierer aus `format.ts` (ASCII-Minus wie überall, Festlegung aus 3a).
- **K56 – Tests:** `FakeInflux` zieht von `tests/unit/app/ledger/conftest.py` nach `tests/helpers.py` (dann findet `pytest tests/unit/app/ledger tests/unit/test_docs.py tests/unit/app/stats` in dieser Reihenfolge `make_client`); `tests/unit/app/ha/test_notify.py:325, 689` und `tests/unit/core/adapters/test_victron_mqtt.py:335` warten abgebrochene Tasks ab.
- [ ] Je Punkt ein Commit `refactor(…): …`; nach K50/K51 die Simulator-Szenarien laufen lassen.

---

### Task 13: Doku und Abschluss

**Files:**
- Modify: `docs/uebergabe.md` (Listen „Aufgeschobene Kleinigkeiten …“: erledigte Punkte streichen, mit Verweis auf diesen Plan; Kurzfassung um diese Welle ergänzen), `docs/betrieb.md` (Sicherung 04:00, Probe und SIGTERM, Grundlast auf der Karte, Grund `thermal_no_echo`, Wall Connector 0 W bei offenem Schütz), Spec Abschnitt 18 („Stand Kleinigkeiten 21.10.2026“, nur wo Verhalten sich ändert: K1, K2, K4, K15, K18)
- [ ] Volle Kette mit Abdeckung, Simulator- und Planungsszenarien, UI-Kette und E2E; GitHub-CI auf dem Branch grün.
- [ ] Commit `docs: Übergabe und Handbuch nach den aufgeschobenen Kleinigkeiten`

---

## Nicht vorgeschlagen (bleiben in der Übergabe)

| Punkt | Grund |
|---|---|
| Boost nach Neustart des core / neu binden nur bei geänderten `vehicles` (2d, 3c) | Mittelgroß (150–250 Zeilen) und heikel: ändert die Bindung von Boost und Übersteuerung im core und das Format von `intent.json`; behebt allein nur einen von drei Fällen. Besser ein eigener kleiner Plan nach der Abnahme von 4b. |
| Quittierung mit der gesehenen Erkennung (2d) | Ändert die API zwischen app und core; selten. |
| `pays_off_at_end` über die Summe (3b) | Ändert den Planer spürbar (mehr Ersatzpläne); erst mit Daten der Anlage. |
| Ersatzplan über R2 bei kleinem PV-Überschuss (4b) | Ändert den Ersatzplan; erst nach der Abnahme der Phase 4. |
| HA-Befehle: Begrenzung beim Eintreffen (2d) | Mittelgroß; nur, wenn es an der Anlage auffällt. |
| Nachtlauf in der März-Nacht (3a) | Gewollt; der nächste Lauf holt nach. |
| Vergangenes `departure_on` bleibt in der Absicht (5b) | Der Rückfall auf die tägliche Uhrzeit ist richtig; Löschen bräuchte einen Schreibvorgang der Absicht. |
| CoE: Header-Byte 1, `unit`/`scale` an digitalen Kanälen (6a) | Erst beim ersten Empfang an der Anlage bestätigen. |
| „nicht erreichbar“ auf der Karte ohne Plan (4b) | Braucht Daten aus Health, die das UI nicht hat; HA zeigt es. |
| Feinheiten im Vorrang der Autos vor der Klimaanlage (6a) | Erst nach dem Dry-Run entscheiden (dafür K3). |
| `charging.py`- und `scenarios.py`-Aufteilung | Großer Umbau ohne Wirkung. |
| Alles, was an der Anlage gemessen oder bestätigt wird | Kein Code: InfluxDB per POST, Grafana `now-13d/d`, Trainingsdauer, Lösezeit im Winter, HA „Abfahrtszeit“, Rundung von Tessie, CoE-Zeiten. |
| Entscheidungen des Betreibers | Totband der Drosselung, Tessies Zeitstempel, Tesla am Minimum statt am Mittel, Unstimmigkeit Schieflast. |
| Liste D aus 4a und reine Testpflege | So entschieden („lassen“) bzw. ohne Wirkung. |

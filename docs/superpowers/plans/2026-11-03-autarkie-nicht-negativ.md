# Autarkie nicht negativ Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Autarkie und Eigenverbrauch liegen überall zwischen 0 % und 100 %. Heute zeigt die Tagesbilanz vom 02.11.2026 „−3 %“ (Bezug 36,1 kWh, Verbrauch 35,1 kWh), weil der Akku an dem Tag 4,81 kWh aus dem Netz geladen und nur 0,14 kWh abgegeben hat.

**Architecture:** Nur app. Eine reine Funktion `share(part, total)` in `app/ledger/service.py` rechnet `part ÷ total`, begrenzt auf 0 bis 1 (`None` bei `total ≤ 0`). `summarize_slots` (Tageswerte, Tagesbilanz) und `stats.service._sums` (Kacheln, Home Assistant „Autarkie heute“ und `month_pct`) rechnen damit. UI, core, API-Form und Datenbank bleiben unverändert.

**Tech Stack:** Python 3.12, pytest.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Abschnitt Abrechnung, „Kennzahlen“ (Zeile ~1928).

## Befund

- Formeln heute (gleich in `ledger/service.py:245` und `stats/service.py:151`): Autarkie `1 − Bezug ÷ Verbrauch`, Eigenverbrauch `(PV − Einspeisung) ÷ PV`, Verbrauch = Haus + Wallboxen.
- Der Bezug enthält die Netzladung des Akkus, der Verbrauch nicht. Die Bilanz vom 02.11. geht auf: PV 3,62 + Bezug 36,1 + Akku entladen 0,14 = 39,86 kWh ≈ Haus 35,1 + Akku geladen 4,81 = 39,91 kWh. Die Formel ist also richtig gerechnet; negativ wird sie nur, weil Netzstrom in den Akku ging, der erst am Folgetag verbraucht wird. Über Monat und Jahr gleicht sich das aus (der Akku ist dort fast netto null), am Tag nicht.
- Eigenverbrauch kann nur über 100 % oder unter 0 % fallen, wenn Einspeisung > PV oder < 0 gemessen wird (Messrauschen; Strom vom Akku ins Netz verbietet 1.2.3). Gleiche Begrenzung, damit die Kachel nie Unsinn zeigt.

## Entscheidungen

1. **Begrenzen statt neu definieren:** Autarkie `share(Verbrauch − Bezug, Verbrauch)`, Eigenverbrauch `share(PV − Einspeisung, PV)`. Für 02.11.: 0 %. Die Definition bleibt die gängige (Bezug zählt als Fremdstrom, auch der für den Akku); ein Tag, an dem mehr Netzstrom kam als verbraucht wurde, ist 0 % autark.
2. **Nicht gewählt:** Netzanteil des Akkus mitführen (Autarkie = 1 − (Netz → Verbraucher + Netzanteil der Akku-Entladung) ÷ Verbrauch). Das wäre je Tag genauer (02.11. etwa 10 %), braucht aber einen eigenen, tageübergreifenden Speicherstand wie der Einstandspreis (`ledger/costing.py`) und ändert Monats- und Jahreswerte. Nur auf Wunsch des Betreibers als eigener Plan.
3. **Monat und Jahr:** gleiche Begrenzung, auch wenn sie dort kaum greift (Summen der Tage, nicht Mittel der begrenzten Tageswerte, wie bisher).
4. **Kein Hinweis in der Tabelle:** „0 %“ ohne Fußnote; die Spalte „Akku geladen“ daneben erklärt den Tag. Der Spec-Nachtrag nennt den Grund.
5. **Ausrollen:** nur ems-app, mit Stufe 2 des Sammel-Pulls (ab 23.11.), nie allein.

## Review Focus

- Bezug > Verbrauch → Autarkie genau 0.0, nicht negativ und nicht `None`.
- Bezug 0 → 1.0; Verbrauch 0 → `None` wie bisher (Zukunft, leere Tage).
- Einspeisung > PV → Eigenverbrauch 0.0; PV 0 → `None`.
- HA „Autarkie heute“ zeigt „0“, nicht „-3“; `month_pct` ebenso begrenzt.
- Bestehende Tests mit Werten im Bereich bleiben unverändert grün.

---

### Task 1: Kennzahlen auf 0 bis 100 % begrenzen

**Files:**
- Modify: `src/bindaems/app/ledger/service.py` (neue Funktion `share`, `summarize_slots`)
- Modify: `src/bindaems/app/stats/service.py` (`_sums`, Moduldocstring)
- Test: `tests/unit/app/ledger/test_service.py`, `tests/unit/app/stats/test_service.py`
- Docs: Spec „Kennzahlen“ (Nachtrag 03.11.2026), `docs/uebergabe.md` (Stand; Offen an der Anlage: kommt mit Stufe 2)

**Interfaces:**
- Produces: `def share(part: float, total: float) -> float | None` in `bindaems.app.ledger.service`

- [ ] **Step 1: Failing tests**
  - `test_service.py` (ledger): `share(-1.0, 35.1) == 0.0`, `share(36.0, 35.1) == 1.0`, `share(1.0, 4.0) == 0.25`, `share(1.0, 0.0) is None`. Ein Tag mit `grid>house` 1000 Wh und `grid>battery` 500 Wh → `autarky == 0.0` (heute −0,5). Ein Tag mit PV 1000 Wh und einem Export über 1000 Wh → `self_consumption == 0.0`.
  - `test_service.py` (stats): Tag mit Netzladung des Akkus → `period("day", …)["ratios"]["autarky"] == 0.0` und `today().autarky_today == 0.0`.
- [ ] **Step 2:** `uv run pytest tests/unit/app/ledger/test_service.py tests/unit/app/stats/test_service.py` → die neuen Tests scheitern (Funktion fehlt, Werte negativ).
- [ ] **Step 3:** `share` implementieren (`None` bei `total <= 0`, sonst `min(1.0, max(0.0, part / total))`); `summarize_slots` und `_sums` rechnen Autarkie und Eigenverbrauch damit; Docstrings nennen die Begrenzung.
- [ ] **Step 4:** Tests grün; dann die ganze Kette: ruff format/check, mypy strict (src + cerbo-watchdog), lint-imports, pytest mit Abdeckung, UI `pnpm check`, `lint`, `test` (unverändert, zur Sicherheit).
- [ ] **Step 5:** Spec-Nachtrag „Stand Plan Autarkie nicht negativ“ unter „Kennzahlen“ und `docs/uebergabe.md`; Commit `fix(app): Autarkie und Eigenverbrauch nie unter 0 % oder über 100 %`.

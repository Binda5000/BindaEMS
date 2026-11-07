# Phasenzuordnung prüfen – Implementierungsplan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Der core erkennt von selbst, wenn die Phasen des Multi oder einer Wallbox nicht zu denen des Netzzählers passen. Er meldet das als Warnung, mit der wahrscheinlich richtigen Zuordnung.

**Architecture:** Ein neues, reines Modul `core/state/phase_order.py` rechnet je Zyklus den Verbrauch je Phase für jede mögliche Zuordnung. Es sammelt Stimmen über die letzten entscheidenden Zyklen und liefert Alarme mit den IDs `wiring.…`. Die Laufzeit hängt diese Alarme neben die Plausibilität. Weil sie nicht mit `plaus.` beginnen, sperren sie die Regelung nicht.

**Tech Stack:** Python 3.12, pytest. Keine neue Abhängigkeit, keine Migration, keine Änderung an `config.yaml`, nur `ems-core`.

**Spec:** `docs/superpowers/specs/2026-10-08-bindaems-design.md`, Abschnitt 7.3, Regel 7 („Eingangsdaten werden auf Frische und Plausibilität geprüft“). Anlass ist der Befund vom 4.11.2026 (Thread „Phasenverdrehung automatisch erkennen“):
- **Multi:** Multi-L1 liegt am Netz auf L2, Multi-L2 auf L3, Multi-L3 auf L1. Bestätigt mit dem Wasserkocher-Test um 19:40 und mit dem Zähler von Netz NÖ.
- **EVCS:** Ihre L1 und L2 liegen auf Netz-L2 und L3.
- **Folgen:** Victron glich die Phasen in die falsche Richtung aus. L2 lag rund 3 h bei 24–27 A. Der Phasenwächter sah „Grenze −2 A“ und drosselte den e-Golf auf 6 A.

## Entscheidungen

Vorgeschlagen sind 1 bis 5. Der Betreiber gibt sie mit dem Plan frei.

1. **Nur melden.** Die Alarme haben die Stufe Warnung und die ID-Vorsilbe `wiring.`, nicht `plaus.`. `InputMonitor.evaluate` zählt nur `plaus.…`, daher sperrt eine falsche Zuordnung weder Regelung noch Aktoren. Der Phasenwächter bleibt in diesem Fall vorsichtig, wie am 4.11. gesehen. Eine Sperre würde die Anlage bis zum Elektriker lahmlegen.
2. **Keine Korrektur per Software für den Multi.** Victron selbst braucht die richtige Zuordnung für den Ausgleich der Phasen. Ein Umrechnen im core würde den Fehler verdecken. Der Alarm sagt, wie die Phasen passen würden. Bei der EVCS gibt es schon `phase_map` in `config.yaml`, und der Alarm nennt den passenden Anfang dafür.
3. **Rechnung.** Je Netzphase `p` und Zuordnung `σ` (Netzphase → Multi-Phase) ist der Verbrauch
   `c_σ(p) = Netz_p − AC-in_σ(p) + AC-out_σ(p) + PV_p`,
   wie `derive` es für die Zuordnung 1:1 rechnet.
   - Eine Zuordnung **passt**, wenn `min_p c_σ(p) ≥ −TOL_W`, und ist **widerlegt**, wenn `min_p c_σ(p) < −NEG_W`.
   - Ein Zyklus ist **entscheidend**, wenn mindestens eine der sechs Zuordnungen widerlegt ist. Er stimmt „falsch“, wenn die Zuordnung 1:1 widerlegt ist.
   - Für eine Wallbox gilt dasselbe mit `h_π(p) = c_1:1(p) − U · I_π(p)`. `π` vertauscht dabei die Ströme je Phase der Wallbox (`Derived.wallbox_phase_a`), `U` ist `grid.voltage_nominal_v`.
   - Ein Zyklus mit einem fehlenden Wert zählt nicht.
4. **Schwellen.** `NEG_W = 500`, `TOL_W = 200`, `DECISIVE_SAMPLES = 300` entscheidende Zyklen (bei 1 s rund 5 min), `ON_SHARE = 0.8`, `OFF_SHARE = 0.2`. Der Alarm kommt erst bei voller Liste, wenn mindestens 80 % „falsch“ stimmen. Er geht, wenn höchstens 20 % „falsch“ stimmen, dazwischen bleibt er. Ohne entscheidende Zyklen bleibt der Zustand, wie er ist. So endet der Alarm nicht, nur weil das Auto fertig ist.
5. **Ausrollen** mit Stufe 2, gemeinsam mit core und app wie Netzgrenze je Phase (Übergabe, Punkt 21). Nur `ems-core` ändert sich.

## Global Constraints

- Deutsch, Commits im Stil `feat(core): …`, keine Modellbezeichnungen im Repo.
- Vor `uv`: `export UV_SYSTEM_CERTS=1; unset UV_NATIVE_TLS`.
- Prüfungen:
  - ruff format/check
  - mypy strict (src und cerbo-watchdog)
  - lint-imports
  - pytest ohne `--cov`; die Abdeckung misst die CI des Branchs, mindestens 75 % gesamt und 90 % für safety/control/actuators
- Kein Aktor wird geschaltet. Nichts an der Anlage.
- Texte für den Betreiber auf Deutsch mit „…“-Anführungszeichen. Zahlen in W ohne Nachkommastellen, wie in `plausibility.py` (`f"{w:.0f}"`).

## Review Focus

1. **Versatz beim Lastsprung:** Zähler und Multi melden einige Sekunden versetzt. Ein Lastsprung von 3 kW darf keinen Alarm auslösen. Test in Task 1: 30 % „falsch“ in der vollen Liste ergibt keinen Alarm.
2. **Fehlender Messwert:** Fehlt ein Wert der Phase (`None`, Qualität nicht OK), zählt der Zyklus nicht, und die Liste bleibt unverändert. Test in Task 1.
3. **Symmetrischer Betrieb:** Netzladen auf allen Phasen gleich oder ein Multi, der nur durchreicht (Wasserkocher-Test), liefert keinen entscheidenden Zyklus und nie einen Alarm. Test in Task 1.
4. **Zuordnung korrigiert:** Ist der Alarm aktiv und passt danach 1:1, endet er, sobald höchstens 20 % „falsch“ stimmen. Test in Task 1.
5. **Wallbox bei falschem Multi:** Ist der Multi-Alarm aktiv oder passt 1:1 im Zyklus nicht, prüft die Wallbox-Prüfung nichts, damit kein falscher Wallbox-Alarm entsteht. Test in Task 2.

---

### Task 1: Prüfung Multi gegen Netzzähler

**Files:**
- Create: `src/bindaems/core/state/phase_order.py`
- Modify: `src/bindaems/core/state/derived.py`: `_pv_ids` wird öffentlich als `pv_ids` (Aufrufer in `derive` anpassen)
- Test: `tests/unit/core/state/test_phase_order.py`

**Interfaces:**
- Produces:
  - `Permutation = tuple[Phase, Phase, Phase]`. `σ[i]` ist die Multi-Phase auf der Netzphase `PHASES[i]`. `IDENTITY = ("L1", "L2", "L3")`, `PERMUTATIONS` sind alle sechs, `IDENTITY` zuerst.
  - `@dataclass(frozen=True) class PhaseSample: grid_w, ac_in_w, ac_out_w, pv_w: Mapping[Phase, float]`
  - `phase_sample(snap: Snapshot, cfg: Config) -> PhaseSample | None` liest `grid.lN.power_w`, `vebus.lN.ac_in_power_w`, `vebus.lN.ac_out_power_w` und die Summe von `pv.<id>.lN.power_w` über `pv_ids(snap, cfg)`. Fehlt ein Wert, gibt es `None`.
  - `consumption_w(sample: PhaseSample, sigma: Permutation) -> dict[Phase, float]`
  - `class Votes` mit `__init__(self, size: int = DECISIVE_SAMPLES)`, `add(self, bad: bool) -> None` und `active: bool` (Regel aus Entscheidung 4)
  - `class MultiPhaseCheck` mit `update(self, sample: PhaseSample | None) -> str | None`: der Text des Alarms, solange er aktiv ist, sonst `None`. Dazu `fits_identity: bool | None` für den letzten Zyklus: passt 1:1? `None` ohne Messwerte.
  - Die Konstanten `NEG_W`, `TOL_W`, `DECISIVE_SAMPLES`, `ON_SHARE`, `OFF_SHARE` aus Entscheidung 4.

- [ ] **Step 1: Failing tests schreiben**

Als Hilfe eine Funktion `sample(grid, ac_in, ac_out, pv=(0, 0, 0)) -> PhaseSample` mit je drei Werten in der Reihenfolge L1, L2, L3. Die Werte sind die vom 4.11. 12:00 als `MIDDAY`: `grid=(0, 6000, -2000)`, `ac_in=(3400, -3500, 0)`, `ac_out=(0, 0, 0)`, `pv=(300, 0, 0)`.

```python
def test_consumption_identity_matches_derive_formula():
    assert consumption_w(MIDDAY, IDENTITY) == {"L1": -3100.0, "L2": 9500.0, "L3": -2000.0}

def test_rotated_multi_fits():
    # Multi-L1 auf Netz-L2, Multi-L2 auf Netz-L3, Multi-L3 auf Netz-L1
    assert min(consumption_w(MIDDAY, ("L3", "L1", "L2")).values()) >= -TOL_W

def test_alarm_after_full_window_with_suggestion():
    check = MultiPhaseCheck()
    texts = [check.update(MIDDAY) for _ in range(DECISIVE_SAMPLES)]
    assert texts[-2] is None
    assert texts[-1] == (
        "Phasen von Multi und Netzzähler passen nicht zusammen: Der Verbrauch auf L1 wäre "
        "rechnerisch -3100 W. Passend wäre: Multi-L1 am Netz auf L2, Multi-L2 auf L3, "
        "Multi-L3 auf L1."
    )

def test_no_suggestion_when_no_permutation_fits():
    # 3 kW Erzeugung am Netz, die im Abgleich fehlt; der Multi reicht nur durch
    unmetered = sample(grid=(-3000, 500, 500), ac_in=(1000, 0, 0), ac_out=(1000, 0, 0))
    check = MultiPhaseCheck()
    for _ in range(DECISIVE_SAMPLES):
        text = check.update(unmetered)
    assert text == (
        "Phasen von Multi und Netzzähler passen nicht zusammen: Der Verbrauch auf L1 wäre "
        "rechnerisch -3000 W. Keine andere Zuordnung der Multi-Phasen passt; fehlt eine "
        "Erzeugung im Abgleich?"
    )

def test_load_step_skew_gives_no_alarm():  # Review Focus 1
    check = MultiPhaseCheck()
    # entscheidend und 1:1 passend: der Multi lädt auf L1 aus dem Netz
    good = sample(grid=(3300, 300, 300), ac_in=(3000, 0, 0), ac_out=(0, 0, 0))
    for i in range(2 * DECISIVE_SAMPLES):
        text = check.update(MIDDAY if i % 10 < 3 else good)
    assert text is None

def test_missing_value_does_not_count():  # Review Focus 2
    check = MultiPhaseCheck()
    for _ in range(DECISIVE_SAMPLES - 1):
        check.update(MIDDAY)
    assert check.update(None) is None
    assert check.fits_identity is None
    assert check.update(MIDDAY) is not None

def test_symmetric_operation_is_never_decisive():  # Review Focus 3
    check = MultiPhaseCheck()
    charging = sample(grid=(2500, 2500, 2500), ac_in=(2300, 2300, 2300), ac_out=(100, 100, 100))
    kettle = sample(grid=(300, 2400, 300), ac_in=(2100, 0, 0), ac_out=(2100, 0, 0))
    for _ in range(2 * DECISIVE_SAMPLES):
        assert check.update(charging) is None
        assert check.update(kettle) is None

def test_alarm_ends_after_wiring_fixed():  # Review Focus 4
    check = MultiPhaseCheck()
    for _ in range(DECISIVE_SAMPLES):
        check.update(MIDDAY)
    # dieselbe Lage wie MIDDAY, aber mit Multi-Phasen, die zum Zähler passen
    fixed = sample(grid=(0, 6000, -2000), ac_in=(0, 3400, -3500), ac_out=(0, 0, 0), pv=(300, 0, 0))
    texts = [check.update(fixed) for _ in range(DECISIVE_SAMPLES)]
    assert texts[0] is not None                         # hält zwischen 20 und 80 %
    assert texts[int(0.8 * DECISIVE_SAMPLES)] is None   # höchstens 20 % „falsch“

def test_alarm_text_fixed_while_active():
    # Der Text nennt Phase und Wert vom Einschalten und bleibt fest (wie peak.exceeded)
    ...  # nach dem Einschalten ein MIDDAY mit −2900 W auf L1: Text unverändert

def test_phase_sample_none_on_missing_signal(cfg):
    # aus tests.unit.core.state.conftest BASE ohne "vebus.l2.ac_out_power_w"
    assert phase_sample(snap(base_without_l2_out), cfg) is None
```

- [ ] **Step 2: Tests laufen lassen, sie scheitern**

Run: `uv run pytest tests/unit/core/state/test_phase_order.py -q -p no:cacheprovider`
Expected: FAIL (ImportError `bindaems.core.state.phase_order`)

- [ ] **Step 3: `phase_order.py` umsetzen**

`MultiPhaseCheck.update` arbeitet so:
- Den Minimalwert jeder Zuordnung bestimmen.
- Ist der Zyklus entscheidend (eine Zuordnung `< −NEG_W`), `Votes.add(1:1 widerlegt)` aufrufen.
- Beim Wechsel auf aktiv den Text festlegen: Phase und Wert des kleinsten `c_1:1(p)`.
- Zur Wahl der Zuordnung zählt `MultiPhaseCheck` in den „falsch“-Zyklen der Liste, wie oft jede andere Zuordnung passt. Vorschlag ist die häufigste, wenn sie in mindestens `ON_SHARE` dieser Zyklen passt, sonst gibt es keinen Vorschlag.
- Der Vorschlag nennt für jede Multi-Phase m die Netzphase p mit `σ(p) = m`.

- [ ] **Step 4: Tests laufen lassen, sie bestehen**

Run: `uv run pytest tests/unit/core/state -q -p no:cacheprovider`
Expected: PASS, auch `test_derived.py` mit `pv_ids`

- [ ] **Step 5: ruff, mypy, lint-imports, dann Commit**

```bash
git add src/bindaems/core/state/phase_order.py src/bindaems/core/state/derived.py tests/unit/core/state/test_phase_order.py
git commit -m "feat(core): Phasen von Multi und Netzzähler prüfen"
```

---

### Task 2: Prüfung Wallbox gegen Netzzähler und Monitor

**Files:**
- Modify: `src/bindaems/core/state/phase_order.py`
- Test: `tests/unit/core/state/test_phase_order.py`

**Interfaces:**
- Consumes: Task 1 (`PhaseSample`, `consumption_w`, `IDENTITY`, `PERMUTATIONS`, `Votes`, Konstanten, `MultiPhaseCheck`)
- Produces:
  - `class WallboxPhaseCheck` mit `__init__(self, name: str, evcs: bool, voltage_v: float)` und `update(self, sample: PhaseSample | None, currents_a: Mapping[Phase, float] | None) -> str | None`
  - `class PhaseOrderMonitor` mit `__init__(self, cfg: Config)` und `evaluate(self, snap: Snapshot, derived: Derived, now: datetime) -> list[Alarm]`. Die IDs sind `wiring.multi_phases` und `wiring.wallbox_phases.<name>`, die Stufe ist `Severity.WARNING`. `since` bleibt der erste Zyklus, solange der Alarm aktiv ist, wie in `PlausibilityMonitor`.

- [ ] **Step 1: Failing tests schreiben**

Gemessen sind die Ströme der EVCS laut core, `phase_map` 1:1: `{"L1": 7.0, "L2": 7.0, "L3": 0.0}`.
- **`EGOLF_OK`:** Multi stimmt, Auto auf Netz-L2 und L3, Haus 200 W je Phase: `grid=(200, 1810, 1810)`, `ac_in=(0, 0, 0)`, `ac_out=(0, 0, 0)`.

```python
def test_evcs_on_other_phases_alarm_names_phase_map():
    check = WallboxPhaseCheck("evcs", evcs=True, voltage_v=230.0)
    for _ in range(DECISIVE_SAMPLES):
        text = check.update(EGOLF_OK, {"L1": 7.0, "L2": 7.0, "L3": 0.0})
    assert text == (
        "Phasen der Wallbox „evcs“ passen nicht zum Netzzähler: Das Auto lädt rechnerisch auf "
        "L2 und L3, die Zuordnung nennt L1 und L2. Abhilfe: phase_map in config.yaml mit L2 und "
        "L3 beginnen lassen."
    )

def test_twc_alarm_asks_for_wiring_check():
    check = WallboxPhaseCheck("twc", evcs=False, voltage_v=230.0)
    one_phase = sample(grid=(200, 200, 3420), ac_in=(0, 0, 0), ac_out=(0, 0, 0))
    for _ in range(DECISIVE_SAMPLES):
        text = check.update(one_phase, {"L1": 14.0, "L2": 0.0, "L3": 0.0})
    assert text.endswith("Der Wall Connector meldet seine Phasen selbst; lass den Anschluss prüfen.")
    assert "lädt rechnerisch auf L3, die Zuordnung nennt L1." in text

def test_three_phase_charging_is_never_decisive():
    ...  # gleiche Ströme auf allen drei Phasen: nie ein Text

def test_monitor_skips_wallbox_while_multi_wrong(cfg):  # Review Focus 5
    # Snapshot mit MIDDAY-Werten und laufender EVCS: nur wiring.multi_phases, kein wiring.wallbox_phases.*
    ...

def test_monitor_alarm_ids_severity_and_since(cfg):
    # Nach DECISIVE_SAMPLES Zyklen: Alarm wiring.multi_phases, Severity.WARNING,
    # since = Zeitpunkt des Zyklus, in dem er aktiv wurde; derselbe since im nächsten Zyklus
    ...
```

- [ ] **Step 2: Tests laufen lassen, sie scheitern**

Run: `uv run pytest tests/unit/core/state/test_phase_order.py -q -p no:cacheprovider`
Expected: FAIL (`WallboxPhaseCheck` fehlt)

- [ ] **Step 3: Umsetzen**

`WallboxPhaseCheck` wertet nur Zyklen aus, die alle drei Bedingungen erfüllen:
- 1:1 passt für den Multi im Zyklus (`min c_1:1 ≥ −TOL_W`).
- `currents_a` ist bekannt.
- Mindestens eine Phase hat Strom.

Die Kandidaten sind die verschiedenen Vertauschungen der drei Ströme, die gemessene Verteilung zuerst. Für die Texte gilt:
- Phasenlisten heißen „L2 und L3“ und „L1, L2 und L3“.
- „rechnerisch auf …“ nennt die Phasen mit Strom der häufigsten passenden Vertauschung. Für sie gilt dieselbe `ON_SHARE`-Regel wie beim Multi. Ohne Vorschlag lautet der Satz „Das Auto lädt rechnerisch nicht auf {Zuordnung}.“
- Der Zusatz für die EVCS lautet „Abhilfe: phase_map in config.yaml mit {Phasen} beginnen lassen.“ Für den Wall Connector lautet er „Der Wall Connector meldet seine Phasen selbst; lass den Anschluss prüfen.“

`PhaseOrderMonitor` hält eine `MultiPhaseCheck` und eine `WallboxPhaseCheck` je `cfg.wallboxes`, mit `evcs=isinstance(wallbox, EvcsConfig)` und `voltage_v=cfg.grid.voltage_nominal_v`. Er gibt den Wallboxen die Ströme aus `derived.wallbox_phase_a.get(name)`, solange der Multi-Alarm nicht aktiv ist, sonst `None`.

- [ ] **Step 4: Tests laufen lassen, sie bestehen**

Run: `uv run pytest tests/unit/core/state -q -p no:cacheprovider`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git commit -am "feat(core): Phasen der Wallboxen gegen den Netzzähler prüfen"
```

---

### Task 3: In die Laufzeit hängen, Doku

**Files:**
- Modify: `src/bindaems/core/runtime.py`: `PhaseOrderMonitor(cfg)` neben `self._plausibility` anlegen, `evaluate(snap, derived, now)` neben `self._plausibility.evaluate` aufrufen und die Alarme in die Liste von `_update_alarms` hängen, nach `*plausibility`. **Nicht** an `_update_operation` geben.
- Test: `tests/integration/test_runtime.py` (Muster der bestehenden Alarm-Tests dort und in `tests/integration/plant.py`)
- Modify: `docs/betrieb.md`, Abschnitt 14, „Meldungen des core“: zwei neue Zeilen. Dazu ein Hinweis in „Laderegelung“, „Hinweise für die Anlage“ auf den Befund vom 4.11. und den Wasserkocher-Test.
- Modify: Spec 7.3, Regel 7: Absatz „Stand Plan Phasenzuordnung (7.11.2026)“ mit den Entscheidungen 1 bis 4
- Modify: `docs/uebergabe.md`: neuer Punkt unter „Offen an der Anlage“, mit Stufe 2, nur `ems-core`, keine Migration und keine Änderung an `config.yaml`. Danach beobachten, ob „Phasen von Multi und Netzzähler …“ erscheint, solange der e-Golf lädt und die Verdrehung noch nicht behoben ist.

- [ ] **Step 1: Failing Integrationstest**

`test_wiring_alarm_does_not_block_control`: Die Anlage im Testaufbau hat die MIDDAY-Werte je Phase und ist im Regelbetrieb. Nach `DECISIVE_SAMPLES` Zyklen steht `wiring.multi_phases` in den Alarmen (Health). Der Status der Eingangsdaten ist ok, und die Betriebsart bleibt CONTROL.

- [ ] **Step 2: Test laufen lassen, er scheitert**

Run: `uv run pytest tests/integration/test_runtime.py -k wiring -q -p no:cacheprovider`
Expected: FAIL (kein Alarm `wiring.multi_phases`)

- [ ] **Step 3: Laufzeit anpassen**, wie oben unter Files

- [ ] **Step 4: Tests bestehen, Simulator ohne neuen Alarm**

Run: `uv run pytest tests/integration/test_runtime.py tests/integration/test_sim_scenarios.py tests/unit/test_docs.py -q -p no:cacheprovider`
Expected: PASS. Kein Simulator-Szenario meldet einen Alarm `wiring.…`. Tut es eins, liegt der Fehler zuerst in der Prüfung, nicht im Szenario (Arbeitsweise).

- [ ] **Step 5: Doku schreiben, Prüfungen, Commit**

```bash
git commit -am "feat(core): Phasenzuordnung im Betrieb melden, Doku"
```

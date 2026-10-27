"""Vorheizen und Vorkühlen als zweiter Planungsschritt (Plan 5c, Task 6, Präzisierung 6).

Nach dem MILP für Akku und Autos verschiebt ein kleines LP auf den fertigen Slots den Verbrauch
der Klimaanlage in der Zeit; das Haus ist der Speicher. Der Optimierer bleibt unverändert, die
Rangfolge Akku → Autos → Klima → Einspeisung (Spec 18) folgt aus dem Aufbau.

Einheiten W, kWh, ct/kWh; Slotlänge ``Δ = 0,25 h``, ``a = exp(−Δ/τ)``, ``C = c_kwh_per_k``.
``S_t`` ist der Speicher in kWh am Ende von Slot ``t``, ``S_t / C`` die Abweichung vom Soll
ohne Versatz in K (positiv: vorgeheizt beim Heizen, vorgekühlt beim Kühlen). Das LP ist im
Vorzeichen symmetrisch: Beim Kühlen gibt der Aufrufer ``start_k`` schon mit umgekehrtem
Vorzeichen und deutet ``steps`` ebenso.

Je Slot ``up_x`` (Mehrverbrauch aus geplanter Einspeisung), ``up_g`` (aus dem Netz), ``dn_g``,
``dn_b``, ``dn_x`` (Minderverbrauch spart Bezug, Akkuenergie, speist ein), alle ≥ 0:

- ``S_t = a · S_{t−1} + (up_x + up_g − dn_g − dn_b − dn_x) · Δ / 1000``,
  ``S_{−1} = start_k · C`` mit ``start_k`` ins Band ``[−down_k, up_k]`` geklemmt;
- ``−down_k · C ≤ S_t ≤ up_k · C``, ``S_t − S_{t−1} ≤ 0,5 · C`` (eine Stufe je Viertelstunde),
  ``S_end ≥ 0``;
- ``up_x ≤ export_w``; ``up_g = 0``, wenn ``discharge_w > 50`` (kein Vorheizen aus dem Akku),
  sonst ``up_g ≤ max(0, budget_w − import_w)``, ohne Budget unbegrenzt;
  ``up_x + up_g ≤ max(0, max_w − base_w)``; ``dn_g ≤ import_w``, ``dn_b ≤ discharge_w``,
  ``dn_g + dn_b + dn_x ≤ base_w``; bei einem Laufanteil unter 0,8 alle fünf 0;
- **Energiebedingung** ``S_{−1} + Σ (up − dn) · Δ / 1000 ≥ 0``: Die Verschiebung braucht im
  Horizont mindestens so viel Strom, wie sie dem Speicher entnimmt. Ohne sie lohnte sich dauerndes
  Absenken schon bei gleichem Preis, weil ``a`` ein abgesenktes Haus (``S < 0``) ohne Strom zum
  Soll zurückführt; das wäre Sparen am Komfort, keine Verschiebung;
- Ziel ``min Σ Δ/1000 · (feed_in · up_x + (import + margin) · up_g − import · dn_g
  − end_value · dn_b − feed_in · dn_x)``, ``feed_in = 0`` ohne Einspeisepreis.

Mehr- und Minderverbrauch im selben Slot heben sich in ``S`` auf. Das LP setzt deshalb einen Plan
voraus, der in einem Slot nicht zugleich einspeist und zu einem höheren Preis bezieht oder aus
dem Akku entlädt, dessen Restwert über dem Einspeisepreis liegt; sonst buchte es eine Ersparnis,
die es nicht gibt.

Der unverschobene Plan kostet in diesem Modell 0; ``saving_ct`` ist minus der Zielwert. Spart der
Horizont weniger als ``MIN_SAVING_CT``, lohnt die Verschiebung nicht (``not_worth``, alle Slots
0). Ohne optimale Lösung, bei Zeitlimit oder Fehler des Solvers ist das Ergebnis ``solver``,
ebenfalls ohne Verschiebung. Ohne Slots ist es ``not_worth`` mit leerer Liste.

Je Slot ``thermal_w = up − dn``, ``grid_w = up_g`` (höchstens ``thermal_w``, falls ein
gleichwertiges Optimum Mehr- und Minderverbrauch im selben Slot hat; Reste des Solvers unter
1e-6 W sind 0) und ``steps``: ``S_t / C / 0,5`` auf ganze Stufen gerundet, genau dazwischen
Richtung 0 wie der Versatz in ``core/safety/ranges.py``, begrenzt auf
``[−round(down_k/0,5), round(up_k/0,5)]``.

Das Modul ist rein: Es liest weder Plan noch Einstellungen noch Wärmemodell; ``ShiftInput`` baut
der Aufrufer. Untaugliche Modellwerte (``C``, ``τ``, ``max_w`` nicht endlich und positiv, Band,
Marge, Restwert oder ``start_k`` nicht endlich, Band negativ) sind ein Fehler des Aufrufers und
lösen ``ValueError`` aus.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Literal

import pulp

from bindaems.shared.timeutil import SLOT

MIN_SAVING_CT: Final = 2.0  # darunter unterbleibt die Verschiebung
TIME_LIMIT_S: Final = 5.0
STEP_K: Final = 0.5  # eine Stufe des Sollwertversatzes
MIN_ON_SHARE: Final = 0.8  # darunter keine Verschiebung im Slot
DISCHARGE_W: Final = 50.0  # darüber deckt der Akku das Haus, kein Mehrverbrauch aus dem Netz
CLEAN_W: Final = 1e-6  # Reste des Solvers darunter sind 0

_DT_H: Final = SLOT.total_seconds() / 3600
_KWH: Final = _DT_H / 1000  # W über einen Slot → kWh


@dataclass(frozen=True)
class ShiftSlot:
    """Ein Slot des fertigen Plans: Grundleistung und Laufanteil der Klimaanlage, geplante
    Einspeisung, geplanter Bezug und Entladung des Akkus ins Haus (W), Preise (ct/kWh, ohne
    Einspeisepreis ``None``) und Budget des Bezugs (W, ohne ``None``)."""

    start: datetime
    base_w: float
    on_share: float
    export_w: float
    import_w: float
    discharge_w: float
    import_ct: float
    feed_in_ct: float | None
    budget_w: float | None


@dataclass(frozen=True)
class ShiftInput:
    """Wärmemodell der Betriebsart, Komfortband in K, Marge und Restwert des Akkus in ct/kWh,
    Abweichung jetzt (``start_k``, beim Kühlen mit umgekehrtem Vorzeichen) und die Slots."""

    mode: Literal["heizen", "kuehlen"]
    c_kwh_per_k: float
    tau_h: float
    max_w: float
    up_k: float
    down_k: float
    margin_ct: float
    end_value_ct: float
    start_k: float
    slots: Sequence[ShiftSlot]


@dataclass(frozen=True)
class ShiftedSlot:
    """Verschiebung eines Slots: Mehr- (positiv) oder Minderverbrauch in W, davon aus dem Netz,
    und der Versatz in Stufen am Slotende."""

    start: datetime
    thermal_w: float
    grid_w: float
    steps: int


@dataclass(frozen=True)
class ShiftResult:
    slots: list[ShiftedSlot]
    saving_ct: float
    reason: Literal["ok", "not_worth", "solver"]


def shift(inp: ShiftInput, time_limit_s: float = TIME_LIMIT_S) -> ShiftResult:
    """Rechnet die Verschiebung; ``ValueError`` bei untauglichen Modellwerten."""
    _validate(inp)
    if not inp.slots:
        return ShiftResult(slots=[], saving_ct=0.0, reason="not_worth")
    try:
        solved = _solve(inp, time_limit_s)
    except Exception:  # Solver und Modellaufbau dürfen die Planung nie anhalten
        solved = None
    if solved is None:
        return _unshifted(inp, 0.0, "solver")
    shifted, saving_ct = solved
    if saving_ct < MIN_SAVING_CT:
        return _unshifted(inp, saving_ct, "not_worth")
    return ShiftResult(slots=shifted, saving_ct=saving_ct, reason="ok")


def _validate(inp: ShiftInput) -> None:
    if inp.mode not in ("heizen", "kuehlen"):
        raise ValueError(f"Unbekannte Betriebsart {inp.mode!r}.")
    for name in ("c_kwh_per_k", "tau_h", "max_w"):
        value = getattr(inp, name)
        if not (math.isfinite(value) and value > 0):
            raise ValueError(f"{name} muss endlich und positiv sein, ist {value}.")
    for name in ("up_k", "down_k"):
        value = getattr(inp, name)
        if not (math.isfinite(value) and value >= 0):
            raise ValueError(f"{name} muss endlich und nicht negativ sein, ist {value}.")
    for name in ("margin_ct", "end_value_ct", "start_k"):
        value = getattr(inp, name)
        if not math.isfinite(value):
            raise ValueError(f"{name} muss endlich sein, ist {value}.")


def _unshifted(
    inp: ShiftInput, saving_ct: float, reason: Literal["not_worth", "solver"]
) -> ShiftResult:
    slots = [ShiftedSlot(start=s.start, thermal_w=0.0, grid_w=0.0, steps=0) for s in inp.slots]
    return ShiftResult(slots=slots, saving_ct=saving_ct, reason=reason)


def _solve(inp: ShiftInput, time_limit_s: float) -> tuple[list[ShiftedSlot], float] | None:
    c = inp.c_kwh_per_k
    a = math.exp(-_DT_H / inp.tau_h)
    start_kwh = min(max(inp.start_k, -inp.down_k), inp.up_k) * c
    s_prev: Any = start_kwh
    prob = pulp.LpProblem("thermal_shift", pulp.LpMinimize)
    terms: list[pulp.LpAffineExpression] = []
    net: list[pulp.LpAffineExpression] = []
    rows: list[tuple[pulp.LpVariable, ...]] = []
    for t, item in enumerate(inp.slots):
        running = item.on_share >= MIN_ON_SHARE
        if item.discharge_w > DISCHARGE_W:
            grid_cap: float | None = 0.0  # kein Vorheizen aus dem Akku
        elif item.budget_w is not None:
            grid_cap = item.budget_w - item.import_w
        else:
            grid_cap = None
        up_x = _flow(prob, f"up_x_{t}", item.export_w, running=running)
        up_g = _flow(prob, f"up_g_{t}", grid_cap, running=running)
        dn_g = _flow(prob, f"dn_g_{t}", item.import_w, running=running)
        dn_b = _flow(prob, f"dn_b_{t}", item.discharge_w, running=running)
        dn_x = _flow(prob, f"dn_x_{t}", item.base_w, running=running)
        prob += up_x + up_g <= max(0.0, inp.max_w - item.base_w), f"up_max_{t}"
        prob += dn_g + dn_b + dn_x <= max(0.0, item.base_w), f"dn_max_{t}"
        s = prob.add_variable(f"s_{t}", lowBound=-inp.down_k * c, upBound=inp.up_k * c)
        change = up_x + up_g - dn_g - dn_b - dn_x
        prob += s == a * s_prev + change * _KWH, f"store_{t}"
        prob += s - s_prev <= STEP_K * c, f"step_{t}"
        feed_in = item.feed_in_ct if item.feed_in_ct is not None else 0.0
        terms.append(
            _KWH
            * (
                feed_in * up_x
                + (item.import_ct + inp.margin_ct) * up_g
                - item.import_ct * dn_g
                - inp.end_value_ct * dn_b
                - feed_in * dn_x
            )
        )
        net.append(change * _KWH)
        rows.append((up_x, up_g, dn_g, dn_b, dn_x, s))
        s_prev = s
    prob += s_prev >= 0, "store_end"
    prob += start_kwh + pulp.lpSum(net) >= 0, "energy"
    prob += pulp.lpSum(terms)

    prob.solve(pulp.HiGHS(msg=False, timeLimit=time_limit_s))
    if prob.status != pulp.LpStatusOptimal or prob.sol_status != pulp.LpSolutionOptimal:
        return None
    lo, hi = -round(inp.down_k / STEP_K), round(inp.up_k / STEP_K)
    shifted: list[ShiftedSlot] = []
    for item, (up_x, up_g, dn_g, dn_b, dn_x, s) in zip(inp.slots, rows, strict=True):
        up = _value(up_x) + _value(up_g)
        dn = _value(dn_g) + _value(dn_b) + _value(dn_x)
        shifted.append(
            ShiftedSlot(
                start=item.start,
                thermal_w=_clean(up - dn),
                grid_w=_clean(min(_value(up_g), max(0.0, up - dn))),
                steps=_steps(_value(s) / c / STEP_K, lo, hi),
            )
        )
    return shifted, 0.0 - float(pulp.value(prob.objective))  # 0.0 − macht aus −0,0 eine 0,0


def _flow(prob: pulp.LpProblem, name: str, upper: float | None, *, running: bool) -> Any:
    """Fluss ≥ 0 bis ``upper`` (negativ zählt als 0, ``None`` unbegrenzt); 0 in einem Slot mit
    Laufanteil unter ``MIN_ON_SHARE``."""
    if not running:
        upper = 0.0
    return prob.add_variable(name, lowBound=0, upBound=None if upper is None else max(0.0, upper))


def _value(var: pulp.LpVariable) -> float:
    value = var.varValue
    return float(value) if value is not None else 0.0


def _clean(value: float) -> float:
    return 0.0 if abs(value) < CLEAN_W else value


def _steps(x: float, lo: int, hi: int) -> int:
    """Ganze Stufen, genau dazwischen Richtung 0 (Reste des Solvers bis 1e-6 zählen als genau),
    begrenzt auf ``[lo, hi]``."""
    x = round(x, 6)
    steps = int(math.copysign(math.ceil(abs(x) - 0.5), x))
    return min(max(steps, lo), hi)

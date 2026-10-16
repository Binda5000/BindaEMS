"""Eigenschaften des Optimierers (Präzisierungen 5 und 6) über zufällige kurze Horizonte.

Jede Eingabe ist lösbar. Für jedes Ergebnis: SOC innerhalb der Grenzen, Einspeisung nie über der
PV, das Haus bezieht aus Netz und Akku nie mehr als den Fehlbetrag (PV deckt es zuerst), nie Laden
und Entladen im selben Slot, kein Netzladen bei ausgeschaltetem Schalter und Regel R1: Nach einem
Slot mit Netzladen wird bis zum Horizontende nichts mehr eingespeist.
"""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.plan_helpers import scenario

from bindaems.app.planning.battery_model import SLOT_H, params_of
from bindaems.app.planning.optimizer import solve


@st.composite
def horizons(draw: st.DrawFn) -> dict[str, object]:
    n = draw(st.integers(min_value=4, max_value=12))
    series = st.lists
    return {
        "prices": draw(series(st.floats(-5.0, 40.0), min_size=n, max_size=n)),
        "pv": draw(series(st.floats(0.0, 9000.0), min_size=n, max_size=n)),
        "load": draw(series(st.floats(100.0, 3000.0), min_size=n, max_size=n)),
        "soc_pct": draw(st.floats(10.0, 100.0)),
        "grid_charge": draw(st.booleans()),
    }


@settings(max_examples=30, deadline=None)
@given(horizons())
def test_optimizer_respects_limits_and_rule_r1(kwargs: dict[str, object]) -> None:
    inputs = scenario(**kwargs)  # type: ignore[arg-type]
    result = solve(inputs)
    assert result.status in ("optimal", "time_limit") and result.flows
    params = params_of(inputs)
    for index, (slot, flow) in enumerate(zip(inputs.slots, result.flows, strict=True)):
        assert params.lo_kwh - 0.01 <= flow.soc_end_kwh <= params.hi_kwh + 0.01
        assert flow.pv_x <= slot.pv_w + 1
        assert flow.gr_h + flow.bt_h <= max(0.0, slot.load_w - slot.pv_w) + 1
        assert min(flow.pv_b + flow.gr_b, flow.bt_h) <= 1
        if not inputs.planning.grid_charge_allowed:
            assert flow.gr_b == 0
        if flow.gr_b > 1:
            later = sum(f.pv_x for f in result.flows[index:]) * SLOT_H / 1000
            assert later <= 0.001

"""Der Optimierer und die Plausibilitätsprüfung passen zusammen (Final-Welle A1).

Für zufällige, realistische Horizonte muss der Plan des Optimierers die Plausibilität bestehen:
Bezugspreise 10–45 ct brutto je Stunde, Einspeisung 0–8 ct oder keine, PV mit Tagesverlauf bis
9 kW, Last 0,3–3 kW je Stunde, SOC 20–90 %, Netzladen an oder aus, 16–96 Slots und ein Restwert
wie aus ``inputs`` (Median der Preise danach). Ein Ersatzplan hieße, dass beide Seiten
verschieden rechnen.
"""

from __future__ import annotations

import math

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.plan_helpers import SCENARIO_START, at, scenario

from bindaems.app.planning.battery_model import eta
from bindaems.app.planning.optimizer import solve
from bindaems.app.planning.service import plan_once
from bindaems.shared.settings import PlanningSettings
from bindaems.shared.timeutil import LOCAL_TZ, SLOT


def _pv_shape(local_hour: float, peak_w: float) -> float:
    """PV über den lokalen Tag: Sinusbogen von 06:00 bis 18:00 mit der Spitze ``peak_w``."""
    if not 6.0 <= local_hour <= 18.0:
        return 0.0
    return peak_w * math.sin(math.pi * (local_hour - 6.0) / 12.0)


@st.composite
def horizons(draw: st.DrawFn) -> dict[str, object]:
    n = draw(st.integers(min_value=16, max_value=96))
    offset = draw(st.integers(min_value=0, max_value=95))
    first = at(SCENARIO_START) + offset * SLOT
    hours = (n + 3) // 4
    hourly = st.lists
    prices_h = draw(hourly(st.floats(10.0, 45.0), min_size=hours, max_size=hours))
    loads_h = draw(hourly(st.floats(300.0, 3000.0), min_size=hours, max_size=hours))
    peak_w = draw(st.floats(0.0, 9000.0))
    feed_in = draw(st.one_of(st.none(), st.floats(0.0, 8.0)))
    later_median = draw(st.floats(10.0, 45.0))
    planning = PlanningSettings()
    end_value_ct = max(
        0.0,
        (eta(planning.roundtrip_pct) * later_median - planning.wear_ct) * planning.end_value_factor,
    )
    starts = [first + i * SLOT for i in range(n)]
    hour_of = [i // 4 for i in range(n)]  # Preis und Last je Stunde ab dem Start
    local_hours = [
        start.astimezone(LOCAL_TZ).hour + start.astimezone(LOCAL_TZ).minute / 60 + 7.5 / 60
        for start in starts
    ]
    return {
        "prices": [prices_h[h] for h in hour_of],
        "pv": [_pv_shape(h, peak_w) for h in local_hours],
        "load": [loads_h[h] for h in hour_of],
        "soc_pct": draw(st.floats(20.0, 90.0)),
        "grid_charge": draw(st.booleans()),
        "feed_in": feed_in,
        "start": first.isoformat(),
        "end_value_ct": end_value_ct,
    }


@settings(max_examples=60, deadline=None)
@given(horizons())
def test_optimizer_plans_pass_the_plausibility_check(kwargs: dict[str, object]) -> None:
    inputs = scenario(**kwargs)  # type: ignore[arg-type]
    plan = plan_once(inputs, solve(inputs))
    assert plan.source == "optimizer", plan.issues

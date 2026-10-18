from datetime import UTC, datetime, timedelta

import pytest

from bindaems.core.control.planned import (
    LATEST_START_BUFFER,
    LATEST_START_MARGIN,
    TARGET_RESTART_PP,
    PlannedState,
    PlannedTracker,
    effective_target,
    full_power_w,
    latest_start,
    need_kwh,
)
from bindaems.core.control.vehicle_soc import SocReading
from bindaems.shared.config import EvcsConfig, TwcConfig, VehicleConfig
from bindaems.shared.control import ChargeMode, VehicleIntent

# Abfahrt 07:00 Wiener Zeit (Sommerzeit) ist 05:00 UTC; das Auto steckt seit dem Abend davor.
DEP = datetime(2026, 10, 21, 5, 0, tzinfo=UTC)
EVENING = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
H = timedelta(hours=1)
MIN = timedelta(minutes=1)
FULL_W = 11040.0
EFF = 0.9
USABLE = 50.0
INTENT = VehicleIntent(mode=ChargeMode.PLANNED, target_soc_pct=80, departure="07:00")


@pytest.fixture
def tracker() -> PlannedTracker:
    return PlannedTracker()


def step(tracker: PlannedTracker, key: str = "egolf", **kw: object) -> PlannedState:
    args: dict[str, object] = {
        "wall_now": EVENING,
        "session": 1,
        "intent": INTENT,
        "soc": SocReading(30.0, "measured"),
        "limit_pct": None,
        "sync": False,
        "full_w": FULL_W,
        "efficiency": EFF,
        "usable_kwh": USABLE,
        "slot_w": None,
    }
    args.update(kw)
    return tracker.step(key, **args)  # type: ignore[arg-type]


def measured(pct: float) -> SocReading:
    return SocReading(pct, "measured")


# Vorgaben des Plans


def test_constants() -> None:
    assert LATEST_START_MARGIN == 1.1
    assert LATEST_START_BUFFER == timedelta(minutes=15)
    assert TARGET_RESTART_PP == 3.0


def test_latest_start_formula() -> None:
    # 20 kWh Bedarf, 11040 W → 20/11,04·1,1 h ≈ 1 h 59 min 34 s, minus 15 min
    assert latest_start(DEP, 20.0, 11040.0) == DEP - timedelta(hours=20 / 11.04 * 1.1) - timedelta(
        minutes=15
    )


def test_latest_start_without_need_is_none() -> None:
    assert latest_start(DEP, 0.0, 11040.0) is None


def test_limit_below_target_is_the_target_without_sync() -> None:
    assert effective_target(80, 70.0, sync=False) == 70.0
    assert effective_target(80, 70.0, sync=True) == 80.0


def test_effective_target_without_limit_or_above_it() -> None:
    assert effective_target(80, None, sync=False) == 80.0
    assert effective_target(80, 90.0, sync=False) == 80.0


def test_need_kwh_formula() -> None:
    # (80 − 35) / 100 · 50 / 0,9 = 25 kWh
    assert need_kwh(35.0, 80.0, 50.0, 0.9) == pytest.approx(25.0)
    assert need_kwh(85.0, 80.0, 50.0, 0.9) == 0.0


def test_full_power_is_the_smaller_current_on_the_vehicle_phases() -> None:
    golf = VehicleConfig(
        name="Golf", usable_kwh=32.0, phases=2, min_a=6, max_a=16, default_wallbox="evcs"
    )
    car = VehicleConfig(
        name="Auto", usable_kwh=60.0, phases=3, min_a=6, max_a=32, default_wallbox="twc"
    )
    evcs = EvcsConfig(type="victron_evcs_ns", host="evcs.local", max_a=16)
    twc = TwcConfig(type="tesla_wall_connector_gen3", host="twc.local", max_a=20)
    assert full_power_w(golf, evcs, 230.0) == 16 * 230.0 * 2
    assert full_power_w(car, twc, 230.0) == 20 * 230.0 * 3


def test_states_in_order(tracker) -> None:
    # Bedarf (80 − 30) / 100 · 50 / 0,9 ≈ 27,8 kWh → späteste Startzeit ≈ 01:59 UTC
    first = step(tracker, slot_w=7000.0)
    assert first.state == "plan"
    assert first.plan_w == 7000.0
    assert first.departure_at == DEP
    assert first.target_soc_pct == 80.0
    assert first.need_kwh == pytest.approx(50 / 100 * 50 / 0.9)
    assert first.latest_start == latest_start(DEP, first.need_kwh, FULL_W)
    assert (first.soc_pct, first.soc_source) == (30.0, "measured")

    before = step(tracker, wall_now=EVENING + H)
    assert before.state == "surplus"
    assert before.plan_w is None
    assert before.latest_start is not None

    due = step(tracker, wall_now=before.latest_start)
    assert due.state == "due"

    reached = step(tracker, wall_now=DEP - H, soc=measured(80.0))
    assert reached.state == "reached"
    assert reached.need_kwh == 0.0
    assert reached.latest_start is None


def test_zero_watt_slot_is_surplus_before_latest_start(tracker) -> None:
    assert step(tracker, slot_w=0.0).state == "surplus"


def test_valid_slot_keeps_latest_start_from_latching(tracker) -> None:
    # Ein gültiger Slot mit 0 W gilt auch nach der spätesten Startzeit: Er lädt PV-Überschuss.
    late = DEP - 30 * MIN
    assert step(tracker, wall_now=late, slot_w=0.0).state == "surplus"
    # Ohne Slot und mit kleinem Bedarf läge die späteste Startzeit nach jetzt: kein Merker.
    assert step(tracker, wall_now=late, soc=measured(78.0)).state == "surplus"


def test_due_latch_resets_with_new_session(tracker) -> None:
    start = step(tracker, wall_now=EVENING + H).latest_start
    assert start is not None
    assert step(tracker, wall_now=start).state == "due"
    # Mit 70 % ist der Bedarf klein, die späteste Startzeit läge erst nach jetzt: Der Merker hält.
    later = start + 5 * MIN
    small = latest_start(DEP, need_kwh(70.0, 80.0, USABLE, EFF), FULL_W)
    assert small is not None and small > later
    assert step(tracker, wall_now=later, soc=measured(70.0)).state == "due"
    # Ein Slot mit 0 W hebt ihn nicht auf, ein Slot mit Leistung gilt vor ihm.
    assert step(tracker, wall_now=later, soc=measured(70.0), slot_w=0.0).state == "due"
    assert step(tracker, wall_now=later, soc=measured(70.0), slot_w=5000.0).state == "plan"
    # Läuft der Slot ab, gilt der Merker wieder.
    assert step(tracker, wall_now=later, soc=measured(70.0)).state == "due"
    # Ein neuer Ansteckvorgang beginnt ohne Merker.
    assert step(tracker, wall_now=later, soc=measured(70.0), session=2).state == "surplus"


def test_due_latch_is_per_vehicle(tracker) -> None:
    start = step(tracker, wall_now=EVENING + H).latest_start
    assert start is not None
    assert step(tracker, wall_now=start).state == "due"
    later = start + 5 * MIN
    assert step(tracker, "tesla", wall_now=later, soc=measured(70.0)).state == "surplus"
    assert step(tracker, wall_now=later, soc=measured(70.0)).state == "due"


def test_reaching_the_target_ends_the_latch(tracker) -> None:
    start = step(tracker, wall_now=EVENING + H).latest_start
    assert start is not None
    assert step(tracker, wall_now=start).state == "due"
    assert step(tracker, wall_now=start + H, soc=measured(80.0)).state == "reached"
    # 4 Punkte darunter lädt es wieder, nach der spätesten Startzeit für den neuen Bedarf.
    assert step(tracker, wall_now=start + H, soc=measured(76.0)).state == "surplus"


def test_reached_until_three_points_below(tracker) -> None:
    # Ziel 80, erreicht bei 80; 78 bleibt "reached", 76,9 nicht mehr
    assert step(tracker, soc=measured(79.9)).state == "surplus"
    assert step(tracker, soc=measured(80.0)).state == "reached"
    assert step(tracker, soc=measured(78.0)).state == "reached"
    assert step(tracker, soc=measured(77.0)).state == "reached"
    assert step(tracker, soc=measured(76.9)).state == "surplus"
    assert step(tracker, soc=measured(78.0)).state == "surplus"


def test_estimated_soc_counts_as_known(tracker) -> None:
    state = step(tracker, soc=SocReading(81.0, "estimated"))
    assert state.state == "reached"
    assert state.soc_source == "estimated"


def test_higher_target_ends_reached(tracker) -> None:
    assert step(tracker, soc=measured(80.0)).state == "reached"
    higher = VehicleIntent(mode=ChargeMode.PLANNED, target_soc_pct=85, departure="07:00")
    state = step(tracker, soc=measured(83.0), intent=higher)
    assert state.state == "surplus"
    assert state.target_soc_pct == 85.0
    # Ein niedrigeres Ziel lässt ein erreichtes Ziel stehen.
    assert step(tracker, soc=measured(85.0), intent=higher).state == "reached"
    assert step(tracker, soc=measured(83.0), intent=INTENT).state == "reached"


def test_reached_resets_with_new_session(tracker) -> None:
    assert step(tracker, soc=measured(80.0)).state == "reached"
    assert step(tracker, soc=measured(78.0), session=2).state == "surplus"


def test_assumed_soc_never_reports_reached(tracker) -> None:
    assumed = step(tracker, soc=SocReading(0.0, "assumed"))
    assert assumed.state == "surplus"
    assert (assumed.soc_pct, assumed.soc_source) == (0.0, "assumed")
    assert assumed.need_kwh == pytest.approx(80 / 100 * 50 / 0.9)
    # Auch nach einem erreichten Ziel: Ohne bekannten Ladestand heißt es nie „erreicht“.
    assert step(tracker, soc=measured(80.0)).state == "reached"
    assert step(tracker, soc=SocReading(0.0, "assumed")).state == "surplus"
    assert step(tracker, soc=SocReading(None, "assumed")).state == "surplus"
    assert step(tracker, soc=SocReading(None, "measured")).state == "surplus"


def test_limit_below_target_reaches_at_the_limit(tracker) -> None:
    state = step(tracker, soc=measured(70.0), limit_pct=70.0)
    assert state.state == "reached"
    assert state.target_soc_pct == 70.0
    synced = step(tracker, "tesla", soc=measured(70.0), limit_pct=70.0, sync=True)
    assert synced.state == "surplus"
    assert synced.target_soc_pct == 80.0


def test_departure_passed_while_plugged_uses_next_day(tracker) -> None:
    after = DEP + 30 * MIN
    state = step(tracker, wall_now=after)
    assert state.departure_at == DEP + timedelta(days=1)
    assert state.latest_start == latest_start(DEP + timedelta(days=1), state.need_kwh, FULL_W)
    assert state.state == "surplus"
    # Ein erreichtes Ziel bleibt nach der Abfahrt erreicht.
    assert step(tracker, "tesla", wall_now=DEP - MIN, soc=measured(80.0)).state == "reached"
    kept = step(tracker, "tesla", wall_now=after, soc=measured(79.0))
    assert (kept.state, kept.departure_at) == ("reached", DEP + timedelta(days=1))

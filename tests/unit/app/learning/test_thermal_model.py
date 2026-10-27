import json
import math
import random
from collections.abc import Collection, Sequence
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, time, timedelta

import pytest

from bindaems.app.learning.thermal_data import ThermalSlot
from bindaems.app.learning.thermal_model import (
    PROFILE_DAYS,
    ModeModel,
    base_power_w,
    fit_mode,
    free_float_episodes,
    mode_profile,
    offset_events,
)
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, local_day_slots

FIRST_DAY = date(2026, 11, 2)  # ein Montag, kein Feiertag und keine Umstellung in drei Wochen
SUMMER_DAY = date(2027, 7, 5)  # ein Montag im Sommer
OFF_HOURS = (18.0, 22.0)  # abends läuft die Anlage nicht: Auskühl-Phase ohne PV
GAIN_W_PER_K = 3000.0  # Verstärkung der Regelung um den Sollwert
EVENT_HOURS = (10.0, 12.0)  # Versatz-Ereignis am Vormittag
MAX_W = 6000.0  # Höchstleistung der erfundenen Anlage
FAN_W = 40.0  # Grundlast der Anlage (b)
SOLAR_W_PER_KW = 60.0  # Sonnengewinn je kW PV
STEPS_PER_SLOT = 15  # Minutenschritte je Viertelstunde


def local(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=LOCAL_TZ).astimezone(UTC)


def synthetic(
    *,
    days: int,
    tau_h: float,
    u: float,
    mode: str = "heizen",
    first: date | None = None,
    off_days: Collection[int] | None = None,
    event_days: Collection[int] = (),
    event_gain: float = 1.0,
    noise_w: float = 20.0,
    seed: int = 7,
) -> list[ThermalSlot]:
    """Ein erfundenes Haus erster Ordnung (Speicher ``tau_h · u`` Wh je K, Verlust ``u`` W je K).

    Die Anlage hält den Sollwert (Leistung für den Sollwert plus Regelung auf den Abstand,
    höchstens ``MAX_W``), abends von 18 bis 22 Uhr läuft sie nicht (an den Tagen ``off_days``,
    ohne Angabe an allen). An ``event_days``
    steht von 10 bis 12 Uhr ein Versatz von 1 K in Richtung der Betriebsart; ``event_gain``
    vervielfacht die gemeldete Mehrleistung darin. Gerechnet wird minutenweise und exakt für
    stückweise konstante Eingänge; gemeldet wird das Mittel je Viertelstunde.
    """
    heating = mode == "heizen"
    sign = 1.0 if heating else -1.0
    base_setpoint = 21.0 if heating else 24.0
    rng = random.Random(seed)  # noqa: S311 - feste Testdaten
    a = math.exp(-1 / 60 / tau_h)
    start_day = first or (FIRST_DAY if heating else SUMMER_DAY)
    temp = base_setpoint
    slots: list[ThermalSlot] = []
    for d in range(days):
        day = start_day + timedelta(days=d)
        daily = (-8.0 if heating else 26.0) + 18.0 * ((d * 7) % 11) / 10
        for start in local_day_slots(day):
            clock = start.astimezone(LOCAL_TZ)
            hour = clock.hour + clock.minute / 60
            off = (off_days is None or d in off_days) and OFF_HOURS[0] <= hour < OFF_HOURS[1]
            event = d in event_days and EVENT_HOURS[0] <= hour < EVENT_HOURS[1]
            offset = sign * 1.0 if event else 0.0
            target = base_setpoint + offset
            pv_w = 5000 * math.sin(math.pi * (hour - 8) / 8) if 8 <= hour < 16 else 0.0
            # tagsüber wärmer, abends und nachts gleich (die Auskühl-Phasen bleiben exponentiell)
            day_wave = math.sin(math.pi * (hour - 6) / 12) if 6 <= hour < 18 else 0.0
            outdoor = daily + 3.0 * day_wave
            gain = SOLAR_W_PER_KW * pv_w / 1000
            temps: list[float] = []
            powers: list[float] = []
            for _ in range(STEPS_PER_SLOT):
                if off:
                    q = 0.0
                else:
                    # Leistung für den Sollwert plus Regelung auf den Abstand, begrenzt
                    steady = sign * (u * (target - outdoor) - gain)
                    q = min(max(steady + GAIN_W_PER_K * sign * (target - temp), 0.0), MAX_W)
                temp = (
                    outdoor + (sign * q + gain) / u + (temp - outdoor - (sign * q + gain) / u) * a
                )
                q_base = sign * (u * (base_setpoint - outdoor) - gain)
                reported = q_base + event_gain * (q - q_base) if event else q
                temps.append(temp)
                powers.append(0.0 if off else FAN_W + reported)
            power = sum(powers) / len(powers)
            if not off:
                power += rng.gauss(0.0, noise_w)
            slots.append(
                ThermalSlot(
                    start=start,
                    actual_c=sum(temps) / len(temps),
                    setpoint_c=target,
                    offset_k=offset,
                    power_w=power,
                    running=not off,
                    mode="aus" if off else mode,  # type: ignore[arg-type]
                    outdoor_c=outdoor,
                    pv_w=pv_w,
                )
            )
    return slots


def model(**changes: object) -> ModeModel:
    values: dict[str, object] = {
        "mode": "heizen",
        "days": 20,
        "u_w_per_k": 120.0,
        "pv_w_per_kw": -60.0,
        "base_w": 40.0,
        "r2": 0.95,
        "tau_h": 40.0,
        "episodes": 20,
        "c_kwh_per_k": 4.8,
        "c_model_kwh_per_k": 4.8,
        "events": 0,
        "event_error_pct": None,
        "max_w": 6000.0,
        "usable": True,
        "reason": "ok",
    }
    values.update(changes)
    return ModeModel(**values)  # type: ignore[arg-type]


def slot(
    start: datetime,
    *,
    actual: float = 21.0,
    setpoint: float | None = 21.0,
    offset: float | None = 0.0,
    power: float | None = 1500.0,
    running: bool | None = True,
    mode: str | None = "heizen",
    outdoor: float = 5.0,
    pv: float | None = 0.0,
) -> ThermalSlot:
    return ThermalSlot(
        start=start,
        actual_c=actual,
        setpoint_c=setpoint,
        offset_k=offset,
        power_w=power,
        running=running,
        mode=mode,  # type: ignore[arg-type]
        outdoor_c=outdoor,
        pv_w=pv,
    )


def without_mode(slots: Sequence[ThermalSlot], days: Collection[date]) -> list[ThermalSlot]:
    return [
        replace(s, mode=None) if s.start.astimezone(LOCAL_TZ).date() in days else s for s in slots
    ]


# --- Anpassung --------------------------------------------------------------------------------


def test_fit_recovers_u_and_tau_from_synthetic_house() -> None:
    m = fit_mode(synthetic(days=21, tau_h=40, u=120), "heizen")
    assert m.usable and m.reason == "ok"
    assert m.tau_h == pytest.approx(40, rel=0.15) and m.u_w_per_k == pytest.approx(120, rel=0.1)
    assert m.c_kwh_per_k == pytest.approx(40 * 120 / 1000, rel=0.2)


def test_fit_reports_all_numbers_of_the_synthetic_house() -> None:
    m = fit_mode(synthetic(days=21, tau_h=40, u=120), "heizen")
    assert m.mode == "heizen" and m.days == 21 and m.episodes == 21
    # Sonne senkt die Leistung beim Heizen. Genauer ist ``s`` nicht: Das Nachheizen nach der
    # Auskühl-Phase fällt in den letzten 0,5 K in die Anpassung und hebt die Leistung ohne PV
    assert m.pv_w_per_kw is not None and m.pv_w_per_kw < 0
    assert m.base_w is not None and m.r2 is not None and m.r2 >= 0.8
    assert m.c_model_kwh_per_k == m.c_kwh_per_k and m.events == 0 and m.event_error_pct is None
    assert m.max_w == pytest.approx(MAX_W + FAN_W, rel=0.05)


def test_model_is_json_serialisable() -> None:
    m = fit_mode(synthetic(days=21, tau_h=40, u=120, event_days=(3, 6, 9)), "heizen")
    data = asdict(m)
    assert json.loads(json.dumps(data, allow_nan=False)) == data
    assert all(type(v) in (int, float, str, bool, type(None)) for v in data.values())


def test_no_slots_is_no_data() -> None:
    m = fit_mode([], "heizen")
    assert not m.usable and m.reason == "no_data" and m.days == 0
    assert m.u_w_per_k is None and m.tau_h is None and m.c_kwh_per_k is None and m.max_w is None


def test_few_days_is_not_usable() -> None:
    m = fit_mode(synthetic(days=13, tau_h=40, u=120), "heizen")
    assert not m.usable and m.reason == "few_days" and m.days == 13
    # Die übrigen Zahlen stehen trotzdem für den Status
    assert m.u_w_per_k == pytest.approx(120, rel=0.1) and m.episodes == 13


def test_days_without_mode_do_not_count() -> None:
    slots = synthetic(days=21, tau_h=40, u=120)
    gone = {FIRST_DAY + timedelta(days=d) for d in range(8)}
    slots = without_mode(slots, gone)
    # Ein weiterer Tag hat nur 3 h mit Heizen: zählt nicht
    short = FIRST_DAY + timedelta(days=8)
    kept = 0
    for i, s in enumerate(slots):
        if s.start.astimezone(LOCAL_TZ).date() == short and s.mode == "heizen":
            kept += 1
            if kept > 12:
                slots[i] = replace(s, mode=None)
    m = fit_mode(slots, "heizen")
    assert m.reason == "few_days" and m.days == 12


def test_mode_with_exactly_four_hours_counts_as_a_day() -> None:
    day = FIRST_DAY
    slots = [slot(local(day, 8) + i * SLOT) for i in range(16)]
    assert fit_mode(slots, "heizen").days == 1
    assert fit_mode(slots[:15], "heizen").days == 0


def test_few_episodes_is_not_usable() -> None:
    m = fit_mode(synthetic(days=21, tau_h=40, u=120, off_days={2, 5}), "heizen")
    assert not m.usable and m.reason == "few_episodes" and m.episodes == 2
    assert m.tau_h == pytest.approx(40, rel=0.15)


def test_poor_fit_is_not_usable() -> None:
    m = fit_mode(synthetic(days=21, tau_h=40, u=120, noise_w=4000), "heizen")
    assert not m.usable and m.reason == "poor_fit"
    assert m.r2 is not None and m.r2 < 0.5


def test_constant_temperature_difference_is_poor_fit() -> None:
    """Ohne Streuung der Temperaturdifferenz ist ``u`` nicht bestimmbar: kein Ersatzwert."""
    slots = synthetic(days=21, tau_h=40, u=120)
    flat = [replace(s, outdoor_c=5.0, pv_w=0.0) if s.running else s for s in slots]
    m = fit_mode(flat, "heizen")
    assert m.reason == "poor_fit" and m.u_w_per_k is None and m.r2 is None
    assert m.c_model_kwh_per_k is None and m.c_kwh_per_k is None


def test_negative_u_is_poor_fit() -> None:
    """Leistung, die mit der Temperaturdifferenz fällt, ist kein Heizen; das Modell taugt nicht."""
    slots = synthetic(days=21, tau_h=40, u=120)
    flipped = [
        replace(s, power_w=6000 - 120 * (21 - s.outdoor_c)) if s.running else s for s in slots
    ]
    m = fit_mode(flipped, "heizen")
    assert m.reason == "poor_fit" and m.u_w_per_k is not None and m.u_w_per_k < 0
    assert m.r2 is not None and m.r2 > 0.5 and m.c_kwh_per_k is None


def test_constant_pv_is_left_out_of_the_fit() -> None:
    slots = synthetic(days=21, tau_h=40, u=120)
    no_sun = [replace(s, pv_w=0.0) for s in slots]
    m = fit_mode(no_sun, "heizen")
    assert m.pv_w_per_kw == 0.0 and m.u_w_per_k == pytest.approx(120, rel=0.1)


def test_fit_uses_only_slots_at_setpoint_with_offset_zero() -> None:
    slots = synthetic(days=21, tau_h=40, u=120)
    # Unbrauchbare Slots mit wilden Leistungen: Versatz unbekannt, Versatz nicht 0, weit vom
    # Soll, Leistung oder PV unbekannt; keiner darf die Anpassung stören
    noisy: list[ThermalSlot] = []
    for i, s in enumerate(slots):
        if not s.running or i % 5:
            noisy.append(s)
            continue
        kind = (i // 5) % 5
        if kind == 0:
            noisy.append(replace(s, offset_k=None, power_w=9000.0))
        elif kind == 1:
            noisy.append(replace(s, offset_k=0.5, setpoint_c=s.setpoint_c + 0.5, power_w=9000.0))
        elif kind == 2:
            noisy.append(replace(s, actual_c=s.actual_c - 0.6, power_w=9000.0))
        elif kind == 3:
            noisy.append(replace(s, pv_w=None, power_w=9000.0))
        else:
            noisy.append(replace(s, power_w=None))
    m = fit_mode(noisy, "heizen")
    assert m.u_w_per_k == pytest.approx(120, rel=0.1) and m.r2 is not None and m.r2 > 0.8


def test_implausible_tau_is_not_usable() -> None:
    m = fit_mode(synthetic(days=21, tau_h=2, u=120), "heizen")
    assert not m.usable and m.reason == "implausible_tau"
    assert m.tau_h == pytest.approx(2, rel=0.15)


def test_tau_above_200_h_is_implausible() -> None:
    m = fit_mode(synthetic(days=21, tau_h=400, u=120), "heizen")
    assert m.reason == "implausible_tau" and m.tau_h is not None and m.tau_h > 200


def test_rising_difference_in_free_float_is_implausible_tau() -> None:
    """Wächst der Abstand innen–außen ohne Anlage, gibt es keine Zeitkonstante."""
    slots = synthetic(days=21, tau_h=40, u=120)
    rising: list[ThermalSlot] = []
    for s in slots:
        clock = s.start.astimezone(LOCAL_TZ)
        if s.running is False:
            rising.append(replace(s, actual_c=21.0 + 0.1 * (clock.hour * 4 + clock.minute // 15)))
        else:
            rising.append(s)
    m = fit_mode(rising, "heizen")
    assert m.reason == "implausible_tau" and m.tau_h is None and m.episodes == 21
    assert m.c_kwh_per_k is None


def test_cooling_uses_outdoor_minus_setpoint() -> None:
    slots = synthetic(days=21, tau_h=40, u=120, mode="kuehlen")
    m = fit_mode(slots, "kuehlen")
    assert m.usable and m.reason == "ok"
    assert m.u_w_per_k == pytest.approx(120, rel=0.1) and m.tau_h == pytest.approx(40, rel=0.15)
    # Sonne erhöht die Leistung beim Kühlen
    assert m.pv_w_per_kw is not None and m.pv_w_per_kw > 0
    heating = fit_mode(slots, "heizen")
    assert heating.reason == "few_days" and heating.days == 0 and heating.episodes == 0


# --- Versatz-Ereignisse -----------------------------------------------------------------------


def test_events_replace_capacity_within_bounds() -> None:
    slots = synthetic(days=21, tau_h=40, u=120, event_days=(3, 8, 13), event_gain=2.0)
    m = fit_mode(slots, "heizen")
    assert m.usable and m.events == 3
    assert m.c_model_kwh_per_k is not None
    assert m.c_kwh_per_k == pytest.approx(1.5 * m.c_model_kwh_per_k)
    assert m.event_error_pct is not None and m.event_error_pct > 50


def test_events_inside_bounds_give_their_median() -> None:
    slots = synthetic(days=21, tau_h=40, u=120, event_days=(3, 8, 13))
    m = fit_mode(slots, "heizen")
    events = offset_events(slots, "heizen", m)
    assert len(events) == 3 == m.events
    assert all(e.offset_k == 1.0 for e in events)
    median = sorted(e.kwh_per_k for e in events)[1]
    assert m.c_model_kwh_per_k is not None
    assert 0.5 * m.c_model_kwh_per_k < median < 1.5 * m.c_model_kwh_per_k
    assert m.c_kwh_per_k == pytest.approx(median)
    assert m.event_error_pct == pytest.approx((median / m.c_model_kwh_per_k - 1) * 100)


def test_events_below_bounds_are_clamped_up() -> None:
    slots = synthetic(days=21, tau_h=40, u=120, event_days=(3, 8, 13), event_gain=0.2)
    m = fit_mode(slots, "heizen")
    assert m.events == 3 and m.c_model_kwh_per_k is not None
    assert m.c_kwh_per_k == pytest.approx(0.5 * m.c_model_kwh_per_k)


def test_two_events_do_not_replace_capacity() -> None:
    slots = synthetic(days=21, tau_h=40, u=120, event_days=(3, 8), event_gain=2.0)
    m = fit_mode(slots, "heizen")
    assert m.events == 2 and m.c_kwh_per_k == m.c_model_kwh_per_k
    assert m.event_error_pct is not None and m.event_error_pct > 50


def test_cooling_events_count_precooling() -> None:
    slots = synthetic(days=21, tau_h=40, u=120, mode="kuehlen", event_days=(3, 8, 13))
    m = fit_mode(slots, "kuehlen")
    events = offset_events(slots, "kuehlen", m)
    assert m.events == 3 and all(e.offset_k == -1.0 and e.kwh_per_k > 0 for e in events)
    assert m.c_model_kwh_per_k is not None and m.c_kwh_per_k is not None
    assert 0.5 * m.c_model_kwh_per_k <= m.c_kwh_per_k <= 1.5 * m.c_model_kwh_per_k


def _event_run(offsets: Sequence[float | None], gaps: Sequence[float]) -> list[ThermalSlot]:
    """Ein Ereignis von Hand: Versatz und Abstand Ist–Soll je Slot, Leistung 3000 W."""
    start = local(FIRST_DAY, 10)
    result: list[ThermalSlot] = []
    for i, (offset, gap) in enumerate(zip(offsets, gaps, strict=True)):
        setpoint = 21.0 + (offset or 0.0)
        result.append(
            slot(
                start + i * SLOT,
                actual=setpoint - gap,
                setpoint=setpoint,
                offset=offset,
                power=3000,
            )
        )
    return result


def test_event_energy_sums_until_settled() -> None:
    # P₀ = 120 · (21 − 5) + 40 = 1960 W; Mehrleistung 1040 W je Slot
    m = model()
    run = _event_run([0.0, 1.0, 1.0, 1.0, 1.0, 1.0], [0.0, 0.9, 0.6, 0.2, 0.0, 0.0])
    (event,) = offset_events(run, "heizen", m)
    assert event.start == run[1].start and event.offset_k == 1.0
    assert event.kwh_per_k == pytest.approx(3 * 1040 * 0.25 / 1000)


def test_event_with_partial_jump_slot_takes_the_held_offset() -> None:
    m = model()
    run = _event_run([0.0, 0.4, 1.0, 1.0, 1.0], [0.0, 0.3, 0.8, 0.25, 0.0])
    (event,) = offset_events(run, "heizen", m)
    assert event.offset_k == 1.0
    assert event.kwh_per_k == pytest.approx(3 * 1040 * 0.25 / 1000)


def test_event_setback_divides_by_offset_direction() -> None:
    m = model()
    start = local(FIRST_DAY, 10)
    run = [slot(start, power=1960.0)] + [
        slot(start + i * SLOT, actual=21.0 - 0.6 * (i == 1), setpoint=20.0, offset=-1.0, power=0.0)
        for i in range(1, 4)
    ]
    # Abstand im ersten Slot 0,4 K, danach 0 K: Summe über zwei Slots mit je −1960 W
    run[1] = replace(run[1], actual_c=20.4)
    run[2] = replace(run[2], actual_c=20.2)
    (event,) = offset_events(run, "heizen", m)
    assert event.offset_k == -1.0
    assert event.kwh_per_k == pytest.approx(2 * 1960 * 0.25 / 1000)


def test_event_not_held_30_min_does_not_count() -> None:
    m = model()
    assert offset_events(_event_run([0.0, 1.0, 1.0, 0.0], [0.0, 0.9, 0.6, 0.0]), "heizen", m) == []
    assert offset_events(_event_run([0.0, 0.4, 1.0, 0.5], [0.0, 0.9, 0.6, 0.0]), "heizen", m) == []
    assert offset_events(_event_run([0.0, 1.5, 1.0, 1.0], [0.0, 0.9, 0.6, 0.0]), "heizen", m) == []
    assert offset_events(_event_run([0.0, 1.0], [0.0, 0.9]), "heizen", m) == []


def test_event_that_changes_offset_before_settling_does_not_count() -> None:
    m = model()
    run = _event_run([0.0, 1.0, 1.0, 1.0, 1.5, 1.5], [0.0, 0.9, 0.7, 0.5, 0.9, 0.1])
    assert offset_events(run, "heizen", m) == []


def test_event_without_known_previous_offset_does_not_count() -> None:
    m = model()
    assert offset_events(_event_run([None, 1.0, 1.0, 1.0], [0.0, 0.9, 0.6, 0.1]), "heizen", m) == []
    gap = _event_run([0.0, 1.0, 1.0, 1.0], [0.0, 0.9, 0.6, 0.1])
    gap[0] = replace(gap[0], start=gap[0].start - SLOT)
    assert offset_events(gap, "heizen", m) == []


def test_event_is_capped_at_three_hours() -> None:
    m = model()
    run = _event_run([0.0] + [1.0] * 15, [0.0] + [0.9] * 15)
    (event,) = offset_events(run, "heizen", m)
    assert event.kwh_per_k == pytest.approx(12 * 1040 * 0.25 / 1000)


def test_event_with_missing_power_does_not_count() -> None:
    m = model()
    run = _event_run([0.0, 1.0, 1.0, 1.0, 1.0, 1.0], [0.0, 0.9, 0.6, 0.4, 0.4, 0.1])
    for index in (2, 4):
        broken = list(run)
        broken[index] = replace(run[index], power_w=None)
        assert offset_events(broken, "heizen", m) == []


def test_events_without_time_constant_keep_capacity_unknown() -> None:
    slots = synthetic(days=21, tau_h=40, u=120, off_days=(), event_days=(3, 8, 13))
    m = fit_mode(slots, "heizen")
    assert m.reason == "few_episodes" and m.events == 3
    assert m.c_kwh_per_k is None and m.c_model_kwh_per_k is None and m.event_error_pct is None


def test_offset_events_need_a_fitted_model() -> None:
    run = _event_run([0.0, 1.0, 1.0, 1.0], [0.0, 0.9, 0.6, 0.1])
    with pytest.raises(ValueError, match="Grundleistung"):
        offset_events(run, "heizen", model(u_w_per_k=None))


# --- Auskühl-Phasen ---------------------------------------------------------------------------


def _free_float(start: datetime, hours: float, *, tau_h: float = 40.0) -> list[ThermalSlot]:
    count = round(hours * 4)
    return [
        slot(
            start + i * SLOT,
            actual=5.0 + 16.0 * math.exp(-(i * 0.25) / tau_h),
            running=False,
            mode="aus",
            power=0.0,
        )
        for i in range(count)
    ]


def test_episode_across_dst_uses_utc_hours() -> None:
    # Nacht auf den 25.10.2026: lokal 00:00 MESZ bis 06:00 MEZ sind 7 h, die Uhr zeigt 6 h
    start = local(date(2026, 10, 25), 0)
    slots = _free_float(start, 7.0)
    assert slots[-1].start + SLOT == local(date(2026, 10, 25), 6)
    (episode,) = free_float_episodes(slots, "heizen")
    assert episode.start == start and episode.hours == 7.0
    assert episode.tau_h == pytest.approx(40.0, rel=1e-6)


def test_episode_needs_two_hours_without_running() -> None:
    start = local(FIRST_DAY, 0)
    assert len(free_float_episodes(_free_float(start, 2.0), "heizen")) == 1
    assert free_float_episodes(_free_float(start, 1.75), "heizen") == []


def test_episode_breaks_at_gaps_sun_and_small_difference() -> None:
    start = local(FIRST_DAY, 0)
    base = _free_float(start, 6.0)
    with_gap = base[:10] + base[11:]
    assert [e.hours for e in free_float_episodes(with_gap, "heizen")] == [2.5, 3.25]
    sunny = [replace(s, pv_w=50.0) if i == 10 else s for i, s in enumerate(base)]
    assert [e.hours for e in free_float_episodes(sunny, "heizen")] == [2.5, 3.25]
    unknown_pv = [replace(s, pv_w=None) if i == 10 else s for i, s in enumerate(base)]
    assert [e.hours for e in free_float_episodes(unknown_pv, "heizen")] == [2.5, 3.25]
    close = [replace(s, outdoor_c=s.actual_c - 2.9) if i == 10 else s for i, s in enumerate(base)]
    assert [e.hours for e in free_float_episodes(close, "heizen")] == [2.5, 3.25]
    unknown_run = [replace(s, running=None) if i == 10 else s for i, s in enumerate(base)]
    assert [e.hours for e in free_float_episodes(unknown_run, "heizen")] == [2.5, 3.25]


def test_episode_direction_follows_mode() -> None:
    """Beim Heizen kühlt das Haus aus (innen wärmer), beim Kühlen wärmt es sich auf."""
    start = local(FIRST_DAY, 0)
    cooling_down = _free_float(start, 4.0)
    assert len(free_float_episodes(cooling_down, "heizen")) == 1
    assert free_float_episodes(cooling_down, "kuehlen") == []
    warming = [replace(s, outdoor_c=40.0) for s in cooling_down]
    assert free_float_episodes(warming, "heizen") == []
    assert len(free_float_episodes(warming, "kuehlen")) == 1


def test_episodes_ignore_slot_order() -> None:
    start = local(FIRST_DAY, 0)
    slots = _free_float(start, 4.0)
    assert free_float_episodes(list(reversed(slots)), "heizen") == free_float_episodes(
        slots, "heizen"
    )


# --- Profil -----------------------------------------------------------------------------------


def _day_slots(day: date, **kwargs: object) -> list[ThermalSlot]:
    """Alle Slots des lokalen Tages; Sollwert 20 + lokale Stunde."""
    result = []
    for start in local_day_slots(day):
        clock = start.astimezone(LOCAL_TZ)
        result.append(slot(start, setpoint=20.0 + clock.hour, **kwargs))  # type: ignore[arg-type]
    return result


def test_profile_on_dst_days_maps_local_quarters() -> None:
    # 25.10.2026 (Sonntag, 25 h): 02:00 bis 03:00 gibt es zweimal; erst läuft die Anlage, dann nicht
    autumn = date(2026, 10, 25)
    slots = _day_slots(autumn)
    assert len(slots) == 100
    first_two = local(autumn, 2)
    for i, s in enumerate(slots):
        if first_two + timedelta(hours=1) <= s.start < first_two + timedelta(hours=2):
            slots[i] = replace(s, running=False, mode="aus", setpoint_c=99.0)
    p = mode_profile(slots, "heizen", today=autumn + timedelta(days=1))
    assert len(p.on_share) == 96 and len(p.setpoint_c) == 96
    assert all(group == 2 for group, _ in p.on_share)
    for quarter in range(8, 12):
        assert p.on_share[(2, quarter)] == pytest.approx(0.5)
        assert p.setpoint_c[(2, quarter)] == 22.0
    assert p.on_share[(2, 12)] == 1.0 and p.setpoint_c[(2, 12)] == 23.0
    assert p.setpoint_c[(2, 4)] == 21.0

    # 28.03.2027 (Sonntag, 23 h): 02:00 bis 03:00 gibt es nicht
    spring = date(2027, 3, 28)
    slots = _day_slots(spring)
    assert len(slots) == 92
    p = mode_profile(slots, "heizen", today=spring + timedelta(days=1))
    assert len(p.on_share) == 92
    assert all((2, quarter) not in p.on_share for quarter in range(8, 12))
    assert p.setpoint_c[(2, 12)] == 23.0 and p.setpoint_c[(2, 7)] == 21.0


def test_holiday_counts_as_sunday_group() -> None:
    holiday = date(2026, 12, 8)  # Dienstag, Mariä Empfängnis
    workday = date(2026, 12, 2)  # Mittwoch
    saturday = date(2026, 12, 5)
    slots = _day_slots(holiday) + _day_slots(workday) + _day_slots(saturday, mode="aus")
    p = mode_profile(slots, "heizen", today=date(2026, 12, 9))
    assert {group for group, _ in p.on_share} == {0, 1, 2}
    assert p.on_share[(2, 40)] == 1.0 and p.on_share[(0, 40)] == 1.0
    assert p.on_share[(1, 40)] == 0.0 and (1, 40) not in p.setpoint_c


def test_profile_takes_median_setpoint_without_offset_over_14_days() -> None:
    today = date(2026, 12, 1)  # Dienstag
    slots: list[ThermalSlot] = []
    # drei Werktage im Fenster mit 20, 21 und 23 °C um 08:00, einer davon mit Versatz 1 K
    for day, base, offset in (
        (date(2026, 11, 24), 20.0, 0.0),
        (date(2026, 11, 25), 21.0, 1.0),
        (date(2026, 11, 26), 23.0, 0.0),
    ):
        slots.append(slot(local(day, 8), setpoint=base + offset, offset=offset))
    # Außerhalb des Fensters: vor 14 Tagen und heute
    slots.append(slot(local(today - timedelta(days=PROFILE_DAYS + 1), 8), setpoint=40.0))
    slots.append(slot(local(today, 8), setpoint=40.0))
    # ohne Versatz oder Sollwert zählt der Slot für den Laufanteil, nicht für den Sollwert
    slots.append(slot(local(date(2026, 11, 27), 8), setpoint=40.0, offset=None))
    slots.append(slot(local(date(2026, 11, 30), 8), setpoint=None))
    # ohne bekannte Betriebsart zählt er gar nicht; eine andere Betriebsart zählt als aus
    slots.append(slot(local(date(2026, 11, 23), 8), mode=None, running=None))
    slots.append(slot(local(date(2026, 11, 20), 8), mode="kuehlen"))
    p = mode_profile(slots, "heizen", today=today)
    assert p.setpoint_c == {(0, 32): 21.0}
    assert p.on_share == {(0, 32): pytest.approx(5 / 6)}


def test_profile_without_data_is_empty() -> None:
    p = mode_profile([], "heizen", today=date(2026, 12, 1))
    assert p.setpoint_c == {} and p.on_share == {}


# --- Grundleistung ----------------------------------------------------------------------------


def test_base_power_is_never_negative() -> None:
    m = model(u_w_per_k=120.0, pv_w_per_kw=-500.0, base_w=0.0)
    assert base_power_w(m, 21.0, 20.0, 5000.0) == 0.0
    assert base_power_w(m, 21.0, 5.0, 1000.0) == pytest.approx(120 * 16 - 500)


def test_base_power_for_cooling_uses_outdoor_minus_setpoint() -> None:
    m = model(mode="kuehlen", u_w_per_k=100.0, pv_w_per_kw=50.0, base_w=30.0)
    assert base_power_w(m, 24.0, 32.0, 2000.0) == pytest.approx(100 * 8 + 100 + 30)
    assert base_power_w(m, 24.0, 10.0, 0.0) == 0.0


def test_base_power_needs_a_fitted_model() -> None:
    with pytest.raises(ValueError, match="Grundleistung"):
        base_power_w(model(u_w_per_k=None, reason="poor_fit", usable=False), 21.0, 5.0, 0.0)

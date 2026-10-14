"""Tests der Kennzahlen des Simulators (Design 13.2, Präzisierung 16).

Die erwarteten Werte sind von Hand gerechnet, mit den Grenzen der Beispielkonfiguration: Sicherung
35 A, Ziel an der Sicherung 33 A, Ziel am Importlimit 19 A (gezählt ab 19,5 A), Tesla-Strombefehle
mindestens 90 s auseinander, höchstens 6 Weckvorgänge je Wiener Tag im Abstand von 1800 s.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest

from bindaems.shared.config import load_config
from bindaems.shared.control import CommandKind, CommandValue
from bindaems.shared.decisions import Decision, DecisionResult
from bindaems.tools.sim.metrics import (
    CarSample,
    Forgotten,
    OwnValue,
    Sample,
    battery_into_car_pct,
    measure,
    tesla_transition_s,
)

CFG: Final = load_config(Path("deploy/config.example.yaml"))
T0: Final = datetime(2026, 6, 21, 8, 0, tzinfo=UTC)  # 10:00 Wiener Zeit

EGOLF: Final = "wallbox.evcs"
TESLA: Final = "vehicle.tesla"


def car(current_a: float = 0.0, *, mode: str = "pv", min_a: int = 6, phases: int = 2) -> CarSample:
    """Ein Auto, das mit ``current_a`` lädt (0: lädt nicht), auf ``phases`` Phasen."""
    return CarSample(
        charging=current_a > 0,
        current_a=current_a,
        power_w=current_a * phases * 230.0,
        mode=mode,
        min_a=min_a,
    )


def tesla(current_a: float = 0.0, *, mode: str = "pv") -> CarSample:
    return car(current_a, mode=mode, min_a=5, phases=3)


def sample(
    t: int,
    *,
    l1: float = 0.0,
    l2: float = 0.0,
    l3: float = 0.0,
    feed: float = 0.0,
    battery_w: float = 0.0,
    soc_pct: float = 60.0,
    cars: Mapping[str, CarSample] | None = None,
    pv_w: float = 0.0,
    house_w: float = 0.0,
) -> Sample:
    """Eine Probe mit sonst ruhigen Werten: Netzstrom ``l1`` bis ``l3``, ``feed`` aus dem Akku auf
    L1, kein Auto lädt; ``pv_w`` und ``house_w`` sind PV und Hauslast in W."""
    return Sample(
        t=t,
        grid_a={"L1": l1, "L2": l2, "L3": l3},
        battery_feed_a={"L1": feed, "L2": 0.0, "L3": 0.0},
        battery_w=battery_w,
        soc_pct=soc_pct,
        cars=cars if cars is not None else {"tesla": tesla(), "egolf": car()},
        pv_w=pv_w,
        house_w=house_w,
    )


def decision(
    seconds: float,
    actuator: str,
    kind: CommandKind,
    value: CommandValue,
    *,
    result: DecisionResult = DecisionResult.OK,
    codes: tuple[str, ...] = ("mode_pv", "pv_surplus"),
) -> Decision:
    """Eine Entscheidung ``seconds`` nach ``T0``; die Kennung nennt Aktor, Art und Zeit."""
    return Decision(
        id=f"{actuator}/{kind}/{seconds}",
        ts=T0 + timedelta(seconds=seconds),
        actuator=actuator,
        kind=kind,
        requested=value,
        applied=value,
        dry_run=result is DecisionResult.DRY_RUN,
        outcome="accepted",
        result=result,
        codes=codes,
        text="",
        inputs={},
    )


def test_quiet_samples_pass_every_limit() -> None:
    metrics = measure([sample(t) for t in range(10)], [], CFG)
    assert metrics.failures(CFG) == []
    assert (metrics.max_phase_a, metrics.over_33_s, metrics.battery_share_pct) == (0.0, 0, 0.0)


# --- Netzstrom ------------------------------------------------------------------------------------


def test_longest_time_above_33_a() -> None:
    samples = [sample(t, l1=34.0 if 5 <= t < 12 else 20.0) for t in range(20)]
    assert measure(samples, [], CFG).over_33_s == 7


def test_time_above_33_a_counts_any_phase_and_breaks_at_33_a() -> None:
    # 0–4 s L1 über 33 A, 5 s genau 33 A (nicht darüber), 6–16 s erst L2, dann L3 darüber: 11 s
    levels = [{"l1": 34.0}] * 5 + [{"l1": 33.0}] + [{"l2": 33.5}] * 6 + [{"l3": 40.0}] * 5
    metrics = measure([sample(t, **level) for t, level in enumerate(levels)], [], CFG)
    assert metrics.over_33_s == 11
    assert metrics.failures(CFG) == [
        "Höchster Phasenstrom: 40 A, Grenze 35 A.",
        "Längste Zeit über 33 A: 11 s, Grenze 10 s.",
    ]


def test_highest_phase_current_up_to_the_fuse() -> None:
    metrics = measure([sample(0, l2=35.0), sample(1, l3=-36.0)], [], CFG)
    assert metrics.max_phase_a == 35.0  # Einspeisung zählt nicht
    assert metrics.failures(CFG) == []
    over = measure([sample(0, l3=35.04321)], [], CFG)
    assert over.failures(CFG) == ["Höchster Phasenstrom: 35,04 A, Grenze 35 A."]
    barely = measure([sample(0, l1=35.001)], [], CFG)  # gerundet 35,00: zeigt den genauen Wert
    assert barely.failures(CFG) == ["Höchster Phasenstrom: 35,001 A, Grenze 35 A."]


# --- Ziel in PV-Modi und Akku-Energie -------------------------------------------------------------


def pv_run(seconds: int, *, egolf: CarSample, tesla_car: CarSample | None = None) -> list[Sample]:
    """Ruhig, dann ``seconds`` lang L1 bei 15 A und 5 A aus dem Akku (20 A > 19,5 A), dann ruhig."""
    cars = {"tesla": tesla_car or tesla(), "egolf": egolf}
    return [
        sample(t, l1=15.0, feed=5.0, cars=cars) if 3 <= t < 3 + seconds else sample(t, cars=cars)
        for t in range(seconds + 6)
    ]


def test_time_over_the_target_in_pv_modes() -> None:
    metrics = measure(pv_run(11, egolf=car(8.0)), [], CFG)
    assert (metrics.over_target_s, metrics.over_target_tesla_s) == (11, 0)
    assert metrics.failures(CFG) == ["Längste Zeit über dem Ziel in PV-Modi: 11 s, Grenze 10 s."]
    assert measure(pv_run(10, egolf=car(8.0, mode="min_pv")), [], CFG).failures(CFG) == []


def test_time_over_the_target_counts_only_pv_cars_above_their_minimum() -> None:
    assert measure(pv_run(30, egolf=car(6.4)), [], CFG).over_target_s == 0  # am Mindeststrom
    assert measure(pv_run(30, egolf=car(6.5)), [], CFG).over_target_s == 30
    assert measure(pv_run(30, egolf=car(16.0, mode="now")), [], CFG).over_target_s == 0
    assert measure(pv_run(30, egolf=car()), [], CFG).over_target_s == 0  # niemand lädt
    # lädt ein Auto in Sofort mit, zählt die Sekunde nicht
    both = pv_run(30, egolf=car(8.0), tesla_car=tesla(16.0, mode="now"))
    assert measure(both, [], CFG).over_target_s == 0


def test_cars_the_regulation_does_not_steer_live_are_no_pv_cars() -> None:
    # Im Dry-Run stellt ein fremder Regler die Ladepunkte: Was er tut, messen die PV-Kennzahlen
    # nicht, auch wenn die Absicht die Autos in PV führt (Entscheidung des Koordinators zu Task 15).
    steered = CarSample(True, 8.7, 4000.0, "pv", 6)

    def run(egolf: CarSample) -> list[Sample]:
        return [
            sample(t, l1=15.0, feed=5.0, battery_w=-2000.0, cars={"egolf": egolf})
            for t in range(60)
        ]

    # gesteuert: 60 s über dem Ziel; Entladung 2000 W nach 10 s Übergang, 50 × 2000 / 60 × 4000
    metrics = measure(run(steered), [], CFG)
    assert (metrics.over_target_s, round(metrics.battery_share_pct, 2)) == (60, 41.67)
    foreign = measure(run(replace(steered, steered=False)), [], CFG)
    assert (foreign.over_target_s, foreign.battery_share_pct) == (0, 0.0)
    # lädt neben einem gesteuerten Auto eines, das ein anderer stellt, zählt die Sekunde nicht
    mixed = {"egolf": steered, "tesla": replace(tesla(8.0), steered=False)}
    together = measure(
        [sample(t, l1=25.0, battery_w=-2000.0, cars=mixed) for t in range(60)], [], CFG
    )
    assert (together.over_target_s, together.over_target_tesla_s) == (0, 0)
    assert together.battery_share_pct == 0.0


def test_a_charging_tesla_may_stay_a_tesla_transition_over_the_target() -> None:
    # Übergang mit Tesla: zwei Strombefehle im Abstand von 90 s und 20 s, also 200 s
    metrics = measure(pv_run(200, egolf=car(), tesla_car=tesla(8.0)), [], CFG)
    assert (metrics.over_target_s, metrics.over_target_tesla_s) == (0, 200)
    assert metrics.failures(CFG) == []
    longer = measure(pv_run(201, egolf=car(8.0), tesla_car=tesla(8.0)), [], CFG)
    assert longer.failures(CFG) == [
        "Längste Zeit über dem Ziel in PV-Modi, Tesla lädt: 201 s, Grenze 200 s."
    ]


def test_battery_share_of_pv_cars() -> None:
    # 30 Proben: PV-Auto 4000 W, Entladung 600 W in den ersten 20; die ersten 10 s sind ein
    # Übergang, 10 zählen → 6000 / 120 000 = 5 %. Mit 22 zählen 12 → 6 %, ein Verstoß.
    def run(discharging: int) -> list[Sample]:
        cars = {"tesla": tesla(), "egolf": CarSample(True, 8.7, 4000.0, "pv", 6)}
        return [
            sample(t, battery_w=-600.0 if t < discharging else 0.0, cars=cars) for t in range(30)
        ]

    assert measure(run(20), [], CFG).battery_share_pct == 5.0
    assert measure(run(20), [], CFG).failures(CFG) == []
    assert measure(run(22), [], CFG).failures(CFG) == ["Akku-Energie in PV-Autos: 6 %, Grenze 5 %."]


def test_battery_share_counts_only_what_the_pv_cars_draw() -> None:
    # 14 s Entladung von 3000 W in ein PV-Auto mit 2000 W: nach dem Übergang zählen 4 × 2000 W.
    # Danach 2 s am Mindeststrom (zählen gar nicht), dann 26 s ohne Entladung: 8000 / 80 000.
    full = CarSample(True, 8.7, 2000.0, "pv", 6)
    minimum = CarSample(True, 6.0, 2760.0, "pv", 6)
    samples = [
        *(sample(t, battery_w=-3000.0, cars={"egolf": full}) for t in range(14)),
        *(sample(t, battery_w=-3000.0, cars={"egolf": minimum}) for t in range(14, 16)),
        *(sample(t, battery_w=0.0, cars={"egolf": full}) for t in range(16, 42)),
    ]
    assert measure(samples, [], CFG).battery_share_pct == 10.0


def battery_run(seconds: int, key: str, *, quiet: int = 0, cfg=CFG) -> float:
    """Der Akku-Anteil: ``seconds`` lang 2000 W aus dem Akku in das PV-Auto ``key`` mit 4000 W,
    dann ``quiet`` Sekunden ohne Entladung."""
    car_4000 = CarSample(True, 8.7, 4000.0, "pv", 5)
    samples = [
        sample(t, battery_w=-2000.0 if t < seconds else 0.0, cars={key: car_4000})
        for t in range(seconds + quiet)
    ]
    return measure(samples, [], cfg).battery_share_pct


def test_battery_energy_in_a_tesla_transition_does_not_count() -> None:
    assert battery_run(150, "tesla") == 0.0  # ganz im Übergang von 200 s
    assert battery_run(250, "tesla") == 10.0  # die letzten 50 s: 100 000 / 1 000 000


def test_battery_energy_counts_after_10_s_without_the_tesla() -> None:
    assert battery_run(30, "egolf", quiet=70) == 10.0  # die letzten 20 s: 40 000 / 400 000


def test_the_tesla_transition_comes_from_the_config() -> None:
    commands = CFG.vehicles["tesla"].commands.model_copy(update={"current_interval_s": 60})
    tesla_cfg = CFG.vehicles["tesla"].model_copy(update={"commands": commands})
    cfg = CFG.model_copy(update={"vehicles": {**CFG.vehicles, "tesla": tesla_cfg}})
    assert tesla_transition_s(cfg) == 140.0  # 2 × 60 + 20
    assert battery_run(150, "tesla", quiet=50, cfg=cfg) == 2.5  # die letzten 10 s: 20 000 / 800 000


def test_the_raw_battery_share_keeps_the_transitions() -> None:
    # Informationszahl ohne Grenze: Ganz im Übergang des Tesla ist die Kennzahl 0 %, roh kommt die
    # Hälfte seiner Energie aus dem Akku (2000 von 4000 W). Wie die Kennzahl zählt sie nur Sekunden
    # mit PV-Autos: Ein Auto in Sofort zählt nicht.
    car_4000 = CarSample(True, 8.7, 4000.0, "pv", 5)
    samples = [sample(t, battery_w=-2000.0, cars={"tesla": car_4000}) for t in range(150)]
    metrics = measure(samples, [], CFG)
    assert (metrics.battery_share_pct, metrics.battery_share_raw_pct) == (0.0, 50.0)
    assert metrics.failures(CFG) == []
    now = [
        sample(t, battery_w=-2000.0, cars={"egolf": replace(car_4000, mode="now")})
        for t in range(30)
    ]
    assert measure(now, [], CFG).battery_share_raw_pct == 0.0


# --- Akku ins Auto --------------------------------------------------------------------------------

EGOLF_2000 = CarSample(True, 4.3, 2000.0, "pv", 6)  # lädt mit 2000 W


def into_car(
    seconds: int,
    cars: Mapping[str, CarSample],
    *,
    battery_w: float = -1400.0,
    pv_w: float = 500.0,
    house_w: float = 900.0,
) -> list[Sample]:
    """``seconds`` gleiche Proben, wenn nichts anderes steht: PV 500 W, Haus 900 W, Entladung
    1400 W. Das Haus nimmt zuerst die PV und dann 400 W vom Akku; 1000 W der Entladung bleiben."""
    return [
        sample(t, battery_w=battery_w, pv_w=pv_w, house_w=house_w, cars=cars)
        for t in range(seconds)
    ]


def test_battery_into_car_counts_after_pv_and_house() -> None:
    # PV 500 W, Haus 900 W, Entladung 1400 W, Auto 2000 W: 1000 W Akku ins Auto in der Sekunde. Von
    # 20 Sekunden zählen die ersten 10 (Übergang) nicht: 10 × 1000 W von 20 × 2000 W.
    assert battery_into_car_pct(into_car(20, {"egolf": EGOLF_2000}), "egolf") == 25.0


def test_a_second_charging_car_gets_only_the_rest() -> None:
    # Vom Rest nach dem Haus (1000 W) nimmt der Tesla, der in ``cars`` vor dem e-Golf steht, zuerst:
    # 600 W. Dem e-Golf bleiben 400 W von seinen 2000 W; der Tesla bekommt alle seine 600 W.
    cars = {"tesla": CarSample(True, 0.9, 600.0, "pv", 5), "egolf": EGOLF_2000}
    samples = into_car(20, cars)
    assert battery_into_car_pct(samples, "tesla") == 50.0  # 10 × 600 W von 20 × 600 W
    assert battery_into_car_pct(samples, "egolf") == 10.0  # 10 × 400 W von 20 × 2000 W
    # Nimmt der Tesla mehr, als der Rest hergibt, bleibt dem e-Golf nichts (nie unter 0).
    big = {"tesla": CarSample(True, 2.2, 1500.0, "pv", 5), "egolf": EGOLF_2000}
    assert battery_into_car_pct(into_car(20, big), "egolf") == 0.0
    assert battery_into_car_pct(into_car(20, big), "tesla") == pytest.approx(100 / 3)  # 10 × 1000
    # Ein Auto, das nicht lädt, nimmt nichts.
    idle = {"tesla": CarSample(False, 0.0, 600.0, "pv", 5), "egolf": EGOLF_2000}
    assert battery_into_car_pct(into_car(20, idle), "egolf") == 25.0


def test_the_transition_of_each_discharge_run_does_not_count() -> None:
    # Eine Folge mit Entladung hat 15 Sekunden: Die ersten 10 zählen nicht, 5 × 1000 W. Dann 3
    # Sekunden ohne Entladung (das Auto lädt weiter) und eine zweite Folge mit 12 Sekunden:
    # 2 × 1000 W.
    levels = [-1400.0] * 15 + [0.0] * 3 + [-1400.0] * 12
    samples = [
        sample(t, battery_w=w, pv_w=500.0, house_w=900.0, cars={"egolf": EGOLF_2000})
        for t, w in enumerate(levels)
    ]
    assert battery_into_car_pct(samples, "egolf") == pytest.approx(100 * 7000 / (30 * 2000))


def test_a_car_that_stops_for_a_second_starts_a_new_transition() -> None:
    # Die Entladung dauert 25 Sekunden, das Auto lädt aber bei 12 s nicht: zwei Folgen mit je 12
    # Sekunden Laden, in jeder zählen 2 Sekunden: 4 × 1000 W von 24 × 2000 W.
    stopped = CarSample(False, 0.0, 0.0, "pv", 6)
    samples = [
        sample(
            t,
            battery_w=-1400.0,
            pv_w=500.0,
            house_w=900.0,
            cars={"egolf": stopped if t == 12 else EGOLF_2000},
        )
        for t in range(25)
    ]
    assert battery_into_car_pct(samples, "egolf") == pytest.approx(100 * 4000 / (24 * 2000))


def test_battery_into_car_counts_at_the_minimum_current_and_in_every_mode() -> None:
    # Anders als die Kennzahl der PV-Autos zählt hier jede Sekunde, in der das Auto lädt: auch am
    # Mindeststrom (6 A auf 2 Phasen, 2760 W), in Sofort und wenn ein anderer den Ladepunkt stellt.
    minimum = CarSample(True, 6.0, 2760.0, "min_pv", 6)
    expected = pytest.approx(100 * 10000 / (20 * 2760))
    assert battery_into_car_pct(into_car(20, {"egolf": minimum}), "egolf") == expected
    foreign_now = replace(minimum, mode="now", steered=False)
    assert battery_into_car_pct(into_car(20, {"egolf": foreign_now}), "egolf") == expected


def test_battery_into_car_needs_more_than_the_house_takes() -> None:
    # Haus 1500 W, keine PV: Der Akku trägt mit seinen 1400 W nur das Haus.
    house = into_car(20, {"egolf": EGOLF_2000}, pv_w=0.0, house_w=1500.0)
    assert battery_into_car_pct(house, "egolf") == 0.0
    # PV über der Hauslast: Die ganze Entladung bleibt für das Auto, 10 × 1400 W von 20 × 2000 W.
    sunny = into_car(20, {"egolf": EGOLF_2000}, pv_w=2000.0, house_w=900.0)
    assert battery_into_car_pct(sunny, "egolf") == 35.0
    # Ohne Entladung kommt nichts aus dem Akku (aber das Auto lädt: 0 %, nicht ``None``).
    charging = into_car(20, {"egolf": EGOLF_2000}, battery_w=500.0)
    assert battery_into_car_pct(charging, "egolf") == 0.0


def test_battery_into_car_is_none_without_charging() -> None:
    idle = into_car(20, {"tesla": car(), "egolf": car()})
    assert battery_into_car_pct(idle, "egolf") is None
    assert battery_into_car_pct([], "egolf") is None
    # ein Auto, das in den Proben fehlt
    assert battery_into_car_pct(into_car(20, {"egolf": EGOLF_2000}), "tesla") is None


def test_the_figures_of_the_run_do_not_include_battery_into_car() -> None:
    metrics = measure(into_car(20, {"egolf": EGOLF_2000}), [], CFG)
    assert [figure.name for figure in metrics.figures(CFG)] == [
        "Höchster Phasenstrom",
        "Längste Zeit über 33 A",
        "Längste Zeit über dem Ziel in PV-Modi",
        "Längste Zeit über dem Ziel in PV-Modi, Tesla lädt",
        "Akku-Energie in PV-Autos",
        "Starts je Auto und Stunde",
        "Kürzester Abstand der Tesla-Strombefehle",
        "Weckvorgänge je Tag",
        "Kürzester Abstand der Weckvorgänge",
        "Wiederholte Dry-Run-Entscheidungen",
    ]


# --- Starts ---------------------------------------------------------------------------------------


def test_starts_per_car_in_a_sliding_hour() -> None:
    # Der e-Golf lädt bei 0 s schon (kein Start), startet bei 10, 1000, 2000, 3000 und 3609 s:
    # in [10, 3610) sind es 5. Ohne den bei 3609 s (später bei 3610 s) höchstens 4.
    def run(last: int) -> list[Sample]:
        starts = {10, 1000, 2000, 3000, last}
        stops = {start - 5 for start in starts} | {5}
        charging = True
        samples = []
        for t in range(3700):
            charging = (charging or t in starts) and t not in stops
            samples.append(sample(t, cars={"egolf": car(8.0 if charging else 0.0)}))
        return samples

    assert measure(run(3609), [], CFG).starts_per_hour == 5
    assert measure(run(3609), [], CFG).failures(CFG) == ["Starts je Auto und Stunde: 5, Grenze 4."]
    assert measure(run(3610), [], CFG).starts_per_hour == 4


# --- Tesla ----------------------------------------------------------------------------------------


def test_tesla_intervals_ignore_urgent_commands() -> None:
    # Entscheidungen bei 0, 60 (mit urgent.bypass) und 95 s → kürzester Abstand 95 s
    decisions = [
        decision(0, TESLA, CommandKind.CURRENT, 10.0),
        decision(
            60, TESLA, CommandKind.CURRENT, 5.0, codes=("mode_now", "overload", "urgent.bypass")
        ),
        decision(95, TESLA, CommandKind.CURRENT, 8.0, result=DecisionResult.DRY_RUN),
    ]
    assert measure([], decisions, CFG).tesla_interval_s == 95.0


def test_tesla_intervals_count_only_ok_and_dry_run_currents() -> None:
    decisions = [
        decision(0, TESLA, CommandKind.CURRENT, 10.0),
        decision(30, TESLA, CommandKind.CURRENT, 5.0, result=DecisionResult.ERROR),
        decision(40, TESLA, CommandKind.CURRENT, 5.0, result=DecisionResult.NOT_SENT),
        decision(50, TESLA, CommandKind.CHARGING, True),
        decision(60, EGOLF, CommandKind.CURRENT, 8.0),
        decision(80, TESLA, CommandKind.CURRENT, 12.0),
    ]
    metrics = measure([], decisions, CFG)
    assert metrics.tesla_interval_s == 80.0
    assert metrics.failures(CFG) == ["Kürzester Abstand der Tesla-Strombefehle: 80 s, Grenze 90 s."]
    assert measure([], decisions[:1], CFG).tesla_interval_s is None  # nur ein Befehl


def test_tesla_intervals_use_the_time_of_sending() -> None:
    # bestätigt nach 45 bzw. 12 s: gesendet 90 s auseinander, entschieden 57 s auseinander
    first = decision(45, TESLA, CommandKind.CURRENT, 10.0)
    second = decision(102, TESLA, CommandKind.CURRENT, 12.0)
    sent = {first.id: T0, second.id: T0 + timedelta(seconds=90)}
    assert measure([], [first, second], CFG).tesla_interval_s == 57.0
    assert measure([], [first, second], CFG, sent=sent).tesla_interval_s == 90.0


def wake(at: datetime, *, result: DecisionResult = DecisionResult.OK) -> Decision:
    """Ein Weckstart zur Wanduhr ``at``."""
    found = decision(0, TESLA, CommandKind.CHARGING, True, result=result)
    codes = ("mode_pv", "pv_surplus", "wake")
    return replace(found, id=f"wake/{at.isoformat()}", ts=at, codes=codes)


def vienna(day: int, hour: int, minute: int = 0, second: int = 0) -> datetime:
    """Oktober 2026 in Wiener Zeit (UTC+2), als UTC."""
    return datetime(2026, 10, day, hour, minute, second, tzinfo=UTC) - timedelta(hours=2)


def test_wakes_count_per_vienna_day() -> None:
    # 22:30 und 23:45 am 12., 00:15 am 13.: höchstens 2 je Tag, Abstand mindestens 30 min
    wakes = [wake(vienna(12, 22, 30)), wake(vienna(12, 23, 45)), wake(vienna(13, 0, 15))]
    metrics = measure([], wakes, CFG)
    assert (metrics.wakes_per_day, metrics.wake_interval_s) == (2, 1800.0)
    assert metrics.failures(CFG) == []


def test_too_many_or_too_close_wakes() -> None:
    seven = [wake(vienna(12, hour)) for hour in range(8, 15)]
    assert measure([], seven, CFG).failures(CFG) == ["Weckvorgänge je Tag: 7, Grenze 6."]
    close = [wake(vienna(12, 8)), wake(vienna(12, 8, 29, 59))]
    assert measure([], close, CFG).failures(CFG) == [
        "Kürzester Abstand der Weckvorgänge: 1799 s, Grenze 1800 s."
    ]
    rejected = [wake(vienna(12, 8)), wake(vienna(12, 8, 1), result=DecisionResult.REJECTED)]
    assert measure([], rejected, CFG).wakes_per_day == 1  # ohne Wert zählt er nicht


def test_a_deferred_wake_start_is_no_wake() -> None:
    # Zurückgestellt (Stundenlimit) ist kein Urteil mit Wert: kein Weckvorgang, wie im Kontingent.
    deferred = wake(vienna(12, 8, 1), result=DecisionResult.DEFERRED)
    assert measure([], [wake(vienna(12, 8)), deferred], CFG).wakes_per_day == 1
    assert measure([], [deferred, wake(vienna(12, 8, 2))], CFG).wake_interval_s is None


# --- Dry-Run --------------------------------------------------------------------------------------


def test_repeated_dry_run_decisions() -> None:
    # EVCS current 10, 10, 11 (dry_run) → 1
    dry = DecisionResult.DRY_RUN
    decisions = [
        decision(0, EGOLF, CommandKind.CURRENT, 10.0, result=dry),
        decision(5, EGOLF, CommandKind.CHARGING, True, result=dry),
        decision(10, EGOLF, CommandKind.CURRENT, 10.0, result=dry),
        decision(15, EGOLF, CommandKind.CURRENT, 11.0, result=dry),
    ]
    metrics = measure([], decisions, CFG)
    assert metrics.repeated_dry_runs == 1
    assert metrics.failures(CFG) == ["Wiederholte Dry-Run-Entscheidungen: 1, Grenze 0."]


def test_a_dry_run_decision_after_an_own_change_is_no_repeat() -> None:
    # Präzisierung 10: UNCHANGED setzt den letzten eigenen Wert ohne Entscheidung. Die Regelung
    # will 6 A, dann 5 A (der fremde Regler hat schon 5 A), dann wieder 6 A: zwei gleiche
    # Dry-Run-Entscheidungen hintereinander, aber jede für eine eigene Änderung.
    dry = DecisionResult.DRY_RUN
    decisions = [
        decision(880, TESLA, CommandKind.CURRENT, 6.0, result=dry),
        decision(1114, TESLA, CommandKind.CURRENT, 6.0, result=dry),
    ]
    own = [
        OwnValue(TESLA, CommandKind.CURRENT, 6.0, dry_run=True),
        OwnValue(TESLA, CommandKind.CURRENT, 5.0, dry_run=False),  # UNCHANGED
        OwnValue(TESLA, CommandKind.CURRENT, 6.0, dry_run=True),
    ]
    assert measure([], decisions, CFG).repeated_dry_runs == 1  # ohne die eigenen Werte
    assert measure([], decisions, CFG, own=own).repeated_dry_runs == 0


def test_a_dry_run_decision_repeats_the_previous_own_value() -> None:
    current, charging = CommandKind.CURRENT, CommandKind.CHARGING
    own = [
        OwnValue(TESLA, current, 6.0, dry_run=True),
        OwnValue(EGOLF, current, 6.0, dry_run=True),  # ein anderer Aktor
        OwnValue(TESLA, charging, True, dry_run=True),  # eine andere Art
        OwnValue(TESLA, current, 6.0, dry_run=True),  # wiederholt
        OwnValue(TESLA, current, 7.0, dry_run=False),  # mit Wert gesendet
        OwnValue(TESLA, current, 7.0, dry_run=True),  # wiederholt den gesendeten
        OwnValue(TESLA, current, 7.0, dry_run=False),  # UNCHANGED: keine Entscheidung
    ]
    metrics = measure([], [], CFG, own=own)
    assert metrics.repeated_dry_runs == 2
    assert metrics.failures(CFG) == ["Wiederholte Dry-Run-Entscheidungen: 2, Grenze 0."]


def test_a_dry_run_decision_after_the_regulation_forgot_is_no_repeat() -> None:
    # Vergisst die Regelung ihren letzten Wert (forget je Aktor, idle für alle, „nicht gesendet“
    # je Aktor und Art), entscheidet sie danach neu; das ist keine Wiederholung.
    current, charging = CommandKind.CURRENT, CommandKind.CHARGING
    six = OwnValue(TESLA, current, 6.0, dry_run=True)
    on = OwnValue(TESLA, charging, True, dry_run=True)

    def repeats(*own: OwnValue | Forgotten) -> int:
        return measure([], [], CFG, own=own).repeated_dry_runs

    assert repeats(six, six) == 1
    assert repeats(six, Forgotten(TESLA), six) == 0
    assert repeats(six, Forgotten(EGOLF), six) == 1  # ein anderer Aktor
    assert repeats(six, Forgotten(), six) == 0  # alle
    assert repeats(six, on, Forgotten(TESLA, charging), six, on) == 1  # nur „Laden“ vergessen


def test_deferred_decisions_are_no_dry_run_decisions() -> None:
    # Eine Zurückstellung ist keine Dry-Run-Entscheidung und kein eigener Wert der Regelung: Die
    # Dry-Run-Entscheidung über 12 A nach zwei zurückgestellten über 12 A ist keine Wiederholung.
    dry, deferred = DecisionResult.DRY_RUN, DecisionResult.DEFERRED
    hourly = ("mode_pv", "pv_surplus", "rate.per_hour")
    decisions = [
        decision(0, TESLA, CommandKind.CURRENT, 10.0, result=dry),
        decision(600, TESLA, CommandKind.CURRENT, 12.0, result=deferred, codes=hourly),
        decision(660, TESLA, CommandKind.CURRENT, 12.0, result=deferred, codes=hourly),
        decision(3600, TESLA, CommandKind.CURRENT, 12.0, result=dry),
    ]
    assert measure([], decisions, CFG).repeated_dry_runs == 0


def test_live_decisions_are_no_repeated_dry_runs() -> None:
    decisions = [
        decision(0, EGOLF, CommandKind.CURRENT, 10.0),
        decision(10, EGOLF, CommandKind.CURRENT, 10.0),
        decision(20, EGOLF, CommandKind.CURRENT, 10.0, result=DecisionResult.DRY_RUN),
    ]
    assert measure([], decisions, CFG).repeated_dry_runs == 0


# --- Reserve-Absicherung und Informationszahlen ---------------------------------------------------


def test_longest_time_above_the_import_target_under_the_reserve_guard() -> None:
    # Unter USV-Reserve + Absicherung (20 + 5 = 25 %) richtet sich auch Sofort am Importziel aus
    # (19 A, gezählt ab 19,5 A). Gezählt wird die Last ohne Akku-Hilfe, solange ein Auto live über
    # seinem Mindeststrom lädt: auf L1 von 5 bis 15 s 15 + 5 = 20 A.
    sofort = CarSample(True, 13.0, 5980.0, "now", 6)

    def longest(soc_pct: float, egolf: CarSample = sofort) -> int:
        samples = [
            sample(
                t,
                l1=15.0 if 5 <= t < 16 else 10.0,
                feed=5.0,
                soc_pct=soc_pct,
                cars={"egolf": egolf},
            )
            for t in range(30)
        ]
        return measure(samples, [], CFG).reserve_over_target_s

    assert longest(24.9) == 11
    assert longest(25.0) == 0  # ohne Absicherung lädt Sofort bis zur Sicherung
    assert longest(24.9, replace(sofort, current_a=6.4)) == 0  # am Mindeststrom
    assert longest(24.9, replace(sofort, steered=False)) == 0  # stellt ein anderer
    assert longest(24.9, replace(sofort, charging=False, current_a=0.0)) == 0  # lädt nicht


def test_deferrals_for_the_hourly_limit_per_vehicle_with_tessie() -> None:
    # Informationszahl ohne Grenze: die Entscheidungen „zurückgestellt“ wegen eines Stundenlimits,
    # je Fahrzeug mit Tessie; andere Ergebnisse, Befunde und Aktoren zählen nicht.
    deferred = DecisionResult.DEFERRED
    decisions = [
        decision(0, TESLA, CommandKind.CURRENT, 8.0, result=deferred, codes=("rate.per_hour",)),
        decision(
            60, TESLA, CommandKind.CHARGING, False, result=deferred, codes=("rate.start_stop",)
        ),
        decision(70, TESLA, CommandKind.CURRENT, 8.0, result=DecisionResult.DRY_RUN),
        decision(80, TESLA, CommandKind.CURRENT, 9.0, result=deferred, codes=("rate.interval",)),
        decision(90, EGOLF, CommandKind.CURRENT, 8.0, result=deferred, codes=("rate.per_hour",)),
    ]
    metrics = measure([], decisions, CFG)
    assert metrics.hourly_deferrals == {"tesla": 2}
    assert metrics.failures(CFG) == []


def test_the_information_figures_name_their_values() -> None:
    car_4000 = CarSample(True, 8.7, 4000.0, "pv", 5)
    samples = [sample(t, battery_w=-2000.0, cars={"tesla": car_4000}) for t in range(150)]
    deferred = decision(0, TESLA, CommandKind.CURRENT, 8.0, result=DecisionResult.DEFERRED)
    metrics = measure(samples, [replace(deferred, codes=("rate.per_hour",))], CFG)
    assert [(info.name, info.value) for info in metrics.info()] == [
        ("Akku-Energie in PV-Autos, roh (mit Übergängen)", "50 %"),
        ("Zurückstellungen wegen des Stundenlimits an „tesla“", "1"),
    ]

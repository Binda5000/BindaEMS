"""Szenarien des Simulators (Design 13.2): Anlage, Absicht, Wetter, Ereignisse und Prüfungen.

Ein ``Scenario`` beschreibt einen Lauf (``runner.run_scenario``): Start in UTC, Dauer, Saat, den
Aufbau des Modells (``setup``), die Absicht (``intent``; ``live`` schaltet die Aktoren der Regelung
live), die Ereignisse je Sekunde und die eigenen Prüfungen am Ergebnis (``checks``). Ort,
PV-Wechselrichter und alle Grenzen kommen aus der Konfiguration des Laufs, in der CI aus
``deploy/config.example.yaml``; die Szenarien nennen deren Fahrzeuge ``tesla`` und ``egolf`` und
Wallboxen ``twc`` und ``evcs``.

**Wetter und Hauslast** sind in jeder Sekunde ein Ereignis (``_Weather``): die PV und die Hauslast
je Phase, ``HOUSE_W`` mit einem gleichverteilten Rauschen von ± ``HOUSE_NOISE_W`` und den Zuschlägen
des Szenarios. Die Sonne ist ein halber Sinus über ``DAYLIGHT_H`` Stunden mit ``SUN_PEAK_W`` um
``SOLAR_NOON_H`` Uhr Wiener Zeit (``sun_w``). Rauschen und Wolken zieht je ein eigener Generator aus
der Saat des Szenarios; dieselbe Saat ergibt dieselben Werte, auch in einem zweiten Lauf.

Zu Beginn stecken die Autos an ihrer Standard-Wallbox, laden aber nicht, und der Tesla ist wach.
Die Absicht verlangt den Regelbetrieb.

Abweichungen von der Tabelle des Plans (Entscheidung des Koordinators zu Task 14): In ``sonnig``
und ``wolkig`` steht der Tesla knapp unter seinem Ladelimit, und die Prüfungen verlangen, dass die
Autos nacheinander laden; ``reserve`` hat 1 kW mehr Hauslast auf L1, damit das Importziel greift.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from typing import TYPE_CHECKING, Final

from bindaems.shared.control import ChargeMode, Intent, VehicleIntent
from bindaems.shared.numbers import de
from bindaems.shared.timeutil import LOCAL_TZ
from bindaems.tools.sim.model import PlantModel

if TYPE_CHECKING:
    from bindaems.core.runtime import CoreRuntime
    from bindaems.tools.sim.metrics import Sample
    from bindaems.tools.sim.runner import SimResult

Action = Callable[[PlantModel, "CoreRuntime"], None]  # ein Ereignis

MINUTE_S: Final = 60
HOUR_S: Final = 3600
HOUSE_W: Final = 300.0  # Hauslast je Phase in W
HOUSE_NOISE_W: Final = 50.0  # Rauschen der Hauslast je Phase und Sekunde, ± in W
SUN_PEAK_W: Final = 9000.0  # PV eines sonnigen Tages am Mittag
SOLAR_NOON_H: Final = 13.0  # Mittag in Wiener Zeit (Sommerzeit)
DAYLIGHT_H: Final = 16.0  # Tageslänge Ende Juni: Sonne von 5 bis 21 Uhr
CLOUD_FACTORS: Final = (1.0, 0.25)  # Sonne und Wolke im Wechsel, Anteil an der PV
CLOUD_SWITCH_S: Final = (30, 180)  # so lange bleibt es sonnig bzw. bewölkt, gleichverteilt

SUMMER: Final = datetime(2026, 6, 21, 10, tzinfo=LOCAL_TZ).astimezone(UTC)
EVENING: Final = datetime(2026, 10, 12, 18, tzinfo=LOCAL_TZ).astimezone(UTC)
PEAK_W: Final = 2500.0  # Lastspitze auf L1 am Abend …
PEAK_S: Final = (20 * MINUTE_S, 25 * MINUTE_S)  # … von 20 bis 25 min
PEAK_REACTION_S: Final = 10  # so lange darf die Regelung auf die Spitze reagieren
RAMP_W: Final = 3000.0  # Rampe auf L1 bei „Nur Laden“ …
RAMP_S: Final = (20 * MINUTE_S, 40 * MINUTE_S)  # … von 20 bis 40 min, danach bleibt die Last
RESERVE_PCT: Final = 20.0  # unter diese USV-Reserve sinkt der SOC nie (Szenario „reserve“)
RESERVE_L1_W: Final = 1000.0  # dort mehr Hauslast auf L1: frei ≈ 13 A unter dem Importziel
SUNNY_TESLA_PCT: Final = 72.0  # SOC des Tesla in „sonnig“: „Complete“ nach etwa einer Stunde
CLOUDY_TESLA_PCT: Final = 75.0  # dasselbe in „wolkig“, wo er im Mittel weniger Strom bekommt


@dataclass(frozen=True)
class Scenario:
    """Ein Szenario des Simulators.

    - ``name``: Name für den Aufruf, ``title``: Überschrift des Berichts
    - ``start``: Beginn in UTC, ``duration_s``: Dauer in s, ``seed``: Saat aller Zufallswerte
    - ``setup``: stellt das Modell vor der ersten Sekunde ein (Akku, Autos, Multi)
    - ``intent``: die Absicht; mit ``live`` sind die Aktoren der Regelung (EVCS und Fahrzeuge mit
      Tessie) live, sonst im Dry-Run
    - ``events``: Sekunde und Aktion, vor der Physik dieser Sekunde, in ihrer Reihenfolge
    - ``checks``: die eigenen Prüfungen; je Verstoß ein Satz
    """

    name: str
    title: str
    start: datetime
    duration_s: int
    seed: int
    setup: Callable[[PlantModel], None]
    intent: Intent
    live: bool
    events: tuple[tuple[int, Action], ...]
    checks: Callable[[SimResult], list[str]]


@dataclass(frozen=True, slots=True)
class _Weather:
    """PV und Hauslast je Phase einer Sekunde, als Ereignis."""

    pv_w: float
    house_w: tuple[float, float, float]

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        model.pv_w = self.pv_w
        model.house_w = self.house_w


def sun_w(at: datetime) -> float:
    """Die PV eines sonnigen Tages zur Zeit ``at``: ein halber Sinus von Auf- bis Untergang."""
    local = at.astimezone(LOCAL_TZ)
    hours = local.hour + local.minute / 60 + local.second / 3600
    since_rise = hours - (SOLAR_NOON_H - DAYLIGHT_H / 2)
    if not 0 < since_rise < DAYLIGHT_H:
        return 0.0
    return SUN_PEAK_W * math.sin(math.pi * since_rise / DAYLIGHT_H)


def _clouds(seed: int, duration_s: int) -> list[float]:
    """Je Sekunde der Anteil der Sonne: ``CLOUD_FACTORS`` im Wechsel, sonnig zuerst."""
    rng = random.Random(f"wolken-{seed}")  # noqa: S311 - Saat der Simulation, kein Geheimnis
    factors: list[float] = []
    switches = 0
    while len(factors) < duration_s:
        factors += [CLOUD_FACTORS[switches % 2]] * rng.randint(*CLOUD_SWITCH_S)
        switches += 1
    return factors[:duration_s]


def _dark(t: int) -> float:
    return 0.0


def _weather(
    start: datetime,
    duration_s: int,
    seed: int,
    *,
    pv: Callable[[int], float] = _dark,
    l1: Callable[[int], float] = _dark,
) -> tuple[tuple[int, Action], ...]:
    """Je Sekunde ``t`` ein ``_Weather``: PV ``pv(t)`` und die Hauslast, auf L1 mit ``l1(t)``."""
    rng = random.Random(f"haus-{seed}")  # noqa: S311 - Saat der Simulation, kein Geheimnis

    def house() -> float:
        return HOUSE_W + rng.uniform(-HOUSE_NOISE_W, HOUSE_NOISE_W)

    return tuple(
        (t, _Weather(pv(t), (house() + l1(t), house(), house()))) for t in range(duration_s)
    )


def _sun(start: datetime) -> Callable[[int], float]:
    """Die Sonne ``t`` Sekunden nach ``start``."""

    def pv(t: int) -> float:
        return sun_w(start + timedelta(seconds=t))

    return pv


def _setup(
    model: PlantModel,
    *,
    soc_pct: float,
    tesla: bool = True,
    charger_only: bool = False,
    tesla_soc_pct: float | None = None,
) -> None:
    """Akku und Multi; der e-Golf an der EVCS, der Tesla am Wall Connector, wenn ``tesla``, mit
    dem SOC ``tesla_soc_pct``, wenn angegeben."""
    model.soc_pct = soc_pct
    model.charger_only = charger_only
    if tesla:
        model.plug("tesla", "twc")
        if tesla_soc_pct is not None:
            model.cars["tesla"].soc_pct = tesla_soc_pct
    model.plug("egolf", "evcs")


def _intent(**modes: ChargeMode) -> Intent:
    """Die Absicht mit Regelbetrieb und den Modi der Fahrzeuge."""
    vehicles = {key: VehicleIntent(mode=mode) for key, mode in modes.items()}
    return Intent(version=1, control=True, vehicles=vehicles)


# --- Prüfungen -----------------------------------------------------------------------------------


def _minutes(samples: Sequence[Sample], key: str) -> float:
    """So viele Minuten lädt das Auto ``key`` in ``samples``."""
    return sum(1 for s in samples if s.cars[key].charging) / MINUTE_S


def _one_after_the_other(result: SimResult, *, tesla_min: float, egolf_min: float) -> list[str]:
    """Erst lädt der Tesla bis „Complete“, mindestens ``tesla_min`` Minuten; danach lädt der
    e-Golf mindestens ``egolf_min`` Minuten. Der e-Golf startet erst, wenn der Tesla „Complete“
    erreicht hat."""
    samples = result.samples
    found: list[str] = []
    done = next((s.t for s in samples if s.cars["tesla"].complete), None)
    if done is None:
        found.append("„tesla“ erreicht „Complete“ nicht.")
    minutes = _minutes(samples, "tesla")
    if minutes < tesla_min:
        found.append(f"„tesla“ lädt {de(round(minutes, 1))} min, verlangt {de(tesla_min)} min.")
    started = next((s.t for s in samples if s.cars["egolf"].charging), None)
    if started is not None and (done is None or started <= done):
        found.append(f"„egolf“ startet bei {started} s, bevor „tesla“ „Complete“ erreicht.")
    after = _minutes([s for s in samples if done is not None and s.t > done], "egolf")
    if after < egolf_min:
        shown = de(round(after, 1))
        found.append(f"„egolf“ lädt danach {shown} min, verlangt {de(egolf_min)} min.")
    return found


def _evcs_lower_during_the_peak(result: SimResult) -> list[str]:
    """Ab ``PEAK_REACTION_S`` nach Beginn der Spitze liegt der Strom der EVCS unter dem davor."""
    begin, end = PEAK_S
    before = [s.cars["egolf"].current_a for s in result.samples if s.t == begin - 1]
    during = [
        s.cars["egolf"].current_a for s in result.samples if begin + PEAK_REACTION_S <= s.t < end
    ]
    if not before or not during:
        return ["Die Lastspitze liegt nicht im Lauf."]
    if max(during) < before[0]:
        return []
    return [f"EVCS-Strom während der Spitze bis {de(max(during))} A, davor {de(before[0])} A."]


def _reserve_kept(result: SimResult) -> list[str]:
    """Eine Entscheidung nennt ``reserve_guard``; der SOC sinkt nie unter ``RESERVE_PCT``."""
    found: list[str] = []
    if not any("reserve_guard" in d.codes for d in result.decisions):
        found.append("Keine Entscheidung mit „reserve_guard“.")
    lowest = min((s.soc_pct for s in result.samples), default=RESERVE_PCT)
    if lowest < RESERVE_PCT:
        shown = de(round(lowest, 4))
        found.append(f"SOC bis {shown} %, unter der USV-Reserve von {de(RESERVE_PCT)} %.")
    return found


def _multi_mode_warns(result: SimResult) -> list[str]:
    """Die Selbstprüfung ``multi_mode`` warnt."""
    warned = any(
        check["id"] == "multi_mode" and check["status"] == "warn"
        for _, health in result.health
        for check in health.selfcheck
    )
    return [] if warned else ["Die Selbstprüfung „multi_mode“ warnt nicht."]


# --- Szenarien 1 bis 5 ----------------------------------------------------------------------------


def _sonnig() -> Scenario:
    """Die Verteilung ist gierig nach Priorität (Design 6.2): Mit höchstens 10 kW PV laden die
    Autos nacheinander, erst der Tesla bis „Complete“, dann der e-Golf."""
    start, duration_s, seed = SUMMER, 2 * HOUR_S, 1
    return Scenario(
        name="sonnig",
        title="Sonniger Tag, beide Autos im PV-Modus",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla_soc_pct=SUNNY_TESLA_PCT),
        intent=_intent(tesla=ChargeMode.PV, egolf=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed, pv=_sun(start)),
        checks=partial(_one_after_the_other, tesla_min=45.0, egolf_min=45.0),
    )


def _wolkig() -> Scenario:
    """Die Verteilung ist gierig nach Priorität (Design 6.2): Mit höchstens 10 kW PV laden die
    Autos nacheinander, erst der Tesla bis „Complete“, dann der e-Golf."""
    start, duration_s, seed = SUMMER, 2 * HOUR_S, 2
    sun, clouds = _sun(start), _clouds(seed, duration_s)

    def pv(t: int) -> float:
        return sun(t) * clouds[t]

    return Scenario(
        name="wolkig",
        title="Wolkiger Tag, beide Autos im PV-Modus",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla_soc_pct=CLOUDY_TESLA_PCT),
        intent=_intent(tesla=ChargeMode.PV, egolf=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed, pv=pv),
        checks=partial(_one_after_the_other, tesla_min=20.0, egolf_min=20.0),
    )


def _abend_sofort() -> Scenario:
    start, duration_s, seed = EVENING, HOUR_S, 3

    def peak(t: int) -> float:
        return PEAK_W if PEAK_S[0] <= t < PEAK_S[1] else 0.0

    return Scenario(
        name="abend-sofort",
        title="Abends beide Autos auf Sofort, mit Lastspitze",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0),
        intent=_intent(tesla=ChargeMode.NOW, egolf=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed, l1=peak),
        checks=_evcs_lower_during_the_peak,
    )


def _reserve() -> Scenario:
    start, duration_s, seed = EVENING, HOUR_S, 4

    def more_on_l1(t: int) -> float:
        return RESERVE_L1_W

    return Scenario(
        name="reserve",
        title="Sofort knapp über der USV-Reserve",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=23.0, tesla=False),
        intent=_intent(egolf=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed, l1=more_on_l1),
        checks=_reserve_kept,
    )


def _nur_laden() -> Scenario:
    start, duration_s, seed = EVENING, HOUR_S, 5

    def ramp(t: int) -> float:
        begin, end = RAMP_S
        return RAMP_W * min(max(t - begin, 0) / (end - begin), 1.0)

    return Scenario(
        name="nur-laden",
        title="Multi auf „Nur Laden“ mit Sofort",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, charger_only=True),
        intent=_intent(tesla=ChargeMode.NOW, egolf=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed, l1=ramp),
        checks=_multi_mode_warns,
    )


SCENARIOS: Final[dict[str, Scenario]] = {
    scenario.name: scenario
    for scenario in (_sonnig(), _wolkig(), _abend_sofort(), _reserve(), _nur_laden())
}

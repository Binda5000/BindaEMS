"""Regelbasierter Ersatzplan für Akku und geplante Autos (Plan 3b, Präzisierung 10; Plan 4b,
Entscheidung 8).

Gilt, wenn der Optimierer scheitert oder sein Plan unplausibel ist. Grundlage ist
``EIGENVERBRAUCH`` in allen Slots. Reicht der SOC für einen teuren Slot des heutigen Tages nur
knapp, hält der Plan den Akku in den billigsten Slots davor zurück und lädt, wenn der Schalter es
erlaubt, die Spread-Regel erfüllt ist und Regel R1 hält (keine Einspeisung, bevor der Akku in der
Nachrechnung wieder leer ist), in billigen Slots aus dem Netz.

Die geplanten Autos laden zuerst den erwarteten PV-Überschuss bis zur Abfahrt, dann in den
günstigsten Slots davor mit voller Leistung (``fallback_vehicles``); der Plan des Akkus rechnet
sie mit, und sein Netzladen lässt neben Haus und Autos den höchsten Netzbezug
(``grid_max_import_w``) und, wenn gesetzt, die Grenze je Phase (``grid_phase_import_w``) frei.
Erwartete Autos (Plan 5b, Entscheidung 11) kennt der Ersatzplan nicht: Er plant nur, was
angesteckt ist.

Liegt der SOC zu Beginn unter der Reserve, lädt er ihn zuerst auf die Reserve nach, auch ohne den
Schalter (Plan Netzgrenze je Phase, Entscheidung 9, ``_recharge``), und hält danach, was
nachgeladen ist.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from math import ceil, floor
from typing import Final

from bindaems.app.planning.battery_model import (
    SLOT_H,
    BatteryParams,
    SimSlot,
    SimStep,
    params_of,
    reserve_kwh,
    sim_slots,
    simulate,
    step,
)
from bindaems.app.planning.grid_phases import PHASES, phase_shares
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.optimizer import grid_charge_cap_w, recharge_caps_w
from bindaems.app.planning.plausibility import (
    R1_TOL_KWH,
    SOC_TOL_KWH,
    export_while_charged_kwh,
    spread_pays,
)
from bindaems.app.planning.vehicles import KWH_EPS, cars_charging, slots_before
from bindaems.shared.plan import (
    RESERVE_RECHARGE_MAX_W,
    BatteryIntent,
    BatteryIntentKind,
    high_percentile,
)
from bindaems.shared.timeutil import LOCAL_TZ, ensure_utc

SHORT_W: Final = 100.0  # ab dieser Netzleistung im teuren Slot reicht der SOC nur knapp
ROUND_W: Final = 100.0  # Schritt der Netzladeleistung
RECHARGE_SLOTS: Final = 96  # Nachladen auf die Reserve in den ersten 24 h (Entscheidung 9)


def _local_day(inputs: PlanInputs, index: int) -> object:
    return ensure_utc(inputs.slots[index].start).astimezone(LOCAL_TZ).date()


def expensive_today(inputs: PlanInputs) -> list[int]:
    """Indizes der teuren Slots: Slots des heutigen lokalen Tages ab dem aktuellen Slot, deren
    Bezugspreis das 80. Perzentil der Preise dieser Slots erreicht.

    Ein einheitlicher Preis ergibt keinen teuren Slot (es gäbe nichts zu verschieben), und ein
    Preis genau am Perzentil zählt mit, sonst bliebe bei vielen gleichen Spitzenpreisen keiner.
    """
    if not inputs.slots:
        return []
    today = _local_day(inputs, 0)
    indices = [i for i in range(len(inputs.slots)) if _local_day(inputs, i) == today]
    prices = [inputs.slots[i].import_ct for i in indices]
    if len(prices) < 2 or min(prices) == max(prices):
        return []
    threshold = high_percentile(prices)
    return [i for i in indices if inputs.slots[i].import_ct >= threshold]


def _first_short(steps: Sequence[SimStep], expensive: Sequence[int]) -> int | None:
    """Erster teurer Slot, in dem die Nachrechnung mehr als ``SHORT_W`` aus dem Netz bezieht,
    weil der Akku den Fehlbetrag nicht deckt; Netzladen und die Autos zählen nicht."""
    for index in expensive:
        item = steps[index]
        if item.import_w - item.charge_grid_w - item.ev_grid_w > SHORT_W:
            return index
    return None


def fallback_vehicles(inputs: PlanInputs) -> dict[str, list[float]]:
    """Ladeleistung je geplantem Auto und Slot im Ersatzplan in W (Entscheidung 8).

    Je Auto in der Reihenfolge der Priorität (``inputs.vehicles``), nur in den Slots vor der
    Abfahrt:

    1. **PV:** der erwartete Überschuss (P50, ``pv_w − load_w``), den die Autos davor übrig
       lassen, Slot für Slot bis der ganze Bedarf (``need_kwh``) gedeckt ist. Ein Slot zählt
       nur, wenn der Überschuss mindestens ``min_w`` erreicht.
    2. **Netz:** dann die günstigsten Slots (beim Gleichstand der frühere) mit voller Leistung,
       bis der Bedarf im Horizont (``horizon_need_kwh``) gedeckt ist; der letzte lädt nur den
       Rest, mindestens ``min_w``. Je Slot laden alle Autos zusammen höchstens
       ``grid_max_import_w`` plus PV minus Haus. Ist ``grid_phase_import_w`` gesetzt, hat jede
       Phase den Platz ``max(0, Grenze − max(0, Last − PV) / 3)`` abzüglich der Netzanteile der
       Autos davor (nur dieser Netzschritt belegt Platz, der PV-Schritt nicht); ein Auto bekommt
       höchstens ``n_c · min_p Platz_p`` zusätzlich (``n_c`` = Zahl seiner Phasen), liegt seine
       Leistung dann unter ``min_w``, lädt es in diesem Slot nicht (Entscheidung 6).

    Regel R2 hält das meist von selbst ein, denn der Überschuss nach P50 ist mindestens der nach
    P25. Nicht immer: Einen Überschuss unter ``min_w`` rechnet R2 (``grid_allowed_kwh``) dem Auto
    als PV an, der Ersatzplan lädt dort aber nicht; bei vielen solchen Slots lädt er mehr aus dem
    Netz als ``grid_allowed_kwh`` erlaubt. Was nicht in den Horizont passt, bleibt Fehlmenge.
    Erwartete Autos (``expected``) fehlen im Ergebnis; sie nehmen keinem anderen PV oder Netz.
    """
    count = len(inputs.slots)
    pv_left = [max(0.0, slot.pv_w - slot.load_w) for slot in inputs.slots]
    room = [inputs.battery.grid_max_import_w + slot.pv_w - slot.load_w for slot in inputs.slots]
    phase_limit_w = inputs.battery.grid_phase_import_w
    phase_room: list[dict[str, float]] = [
        {phase: max(0.0, phase_limit_w - max(0.0, slot.load_w - slot.pv_w) / 3) for phase in PHASES}
        if phase_limit_w is not None
        else {}
        for slot in inputs.slots
    ]
    result: dict[str, list[float]] = {}
    for car in inputs.vehicles:
        if car.expected:
            continue
        shares = phase_shares(car.grid_phases)
        watts = [0.0] * count
        before = slots_before(car.departure_at, inputs.slots) if car.need_kwh > 0 else 0

        def charge(index: int, power_w: float, *, from_pv: bool, w: list[float] = watts) -> None:
            added = power_w - w[index]
            w[index] = power_w
            room[index] -= added
            if from_pv:
                pv_left[index] -= added

        left = car.need_kwh
        for index in range(before):
            if left <= KWH_EPS:
                break
            power_w = min(pv_left[index], car.max_w, max(left / SLOT_H * 1000, car.min_w))
            if power_w < car.min_w:
                continue
            charge(index, power_w, from_pv=True)
            left -= power_w * SLOT_H / 1000

        left = car.horizon_need_kwh - sum(watts) * SLOT_H / 1000
        cheapest = sorted(range(before), key=lambda index: (inputs.slots[index].import_ct, index))
        for index in cheapest:
            if left <= KWH_EPS:
                break
            current = watts[index]
            power_w = max(min(car.max_w, current + left / SLOT_H * 1000), car.min_w)
            power_w = min(power_w, current + room[index])
            if phase_limit_w is not None:
                free_w = len(shares) * min(phase_room[index][phase] for phase in shares)
                power_w = min(power_w, current + free_w)
            if power_w < car.min_w or power_w <= current:
                continue
            if phase_limit_w is not None:
                for phase, share in shares.items():
                    phase_room[index][phase] -= (power_w - current) * share
            charge(index, power_w, from_pv=False)
            left -= (power_w - current) * SLOT_H / 1000
        result[car.key] = watts
    return result


def _phase_headroom_w(
    inputs: PlanInputs,
    ev_w: Sequence[Mapping[str, float]],
    slots: Sequence[SimSlot],
    index: int,
) -> float:
    """Platz fürs Netzladen im Slot unter der Grenze je Phase in W (Entscheidung 6): dreimal der
    kleinste Platz einer Phase nach Haus und der ganzen Ladeleistung der Autos. Ein Schlüssel ohne
    passendes Auto zählt 3-phasig symmetrisch."""
    limit_w = inputs.battery.grid_phase_import_w
    if limit_w is None:
        raise ValueError("Grenze je Phase nötig")
    phases = {car.key: car.grid_phases for car in inputs.vehicles}
    cars = ev_w[index] if ev_w else {}
    house_w = max(0.0, slots[index].load_w - slots[index].pv_w) / 3
    room = min(
        limit_w
        - house_w
        - sum(watts * phase_shares(phases.get(key)).get(phase, 0.0) for key, watts in cars.items())
        for phase in PHASES
    )
    return 3 * room


def per_slot(powers: Mapping[str, Sequence[float]], count: int) -> list[dict[str, float]]:
    """Ladeleistung je Slot und Auto aus ``fallback_vehicles``; ein Auto unter ``EV_ON_W``
    fehlt im Slot."""
    return [
        cars_charging({key: watts[index] for key, watts in powers.items()})
        for index in range(count)
    ]


def _grid_headroom_w(
    inputs: PlanInputs,
    ev_w: Sequence[Mapping[str, float]],
    slots: Sequence[SimSlot],
    steps: Sequence[SimStep],
    index: int,
) -> float:
    """Platz fürs Netzladen im Slot neben Haus und Autos: unter dem höchsten Netzbezug und, wenn
    gesetzt, unter der Grenze je Phase (``_phase_headroom_w``)."""
    headroom_w = (
        inputs.battery.grid_max_import_w
        - steps[index].ev_grid_w
        - max(0.0, slots[index].load_w - slots[index].pv_w)
    )
    if inputs.battery.grid_phase_import_w is not None:
        headroom_w = min(headroom_w, _phase_headroom_w(inputs, ev_w, slots, index))
    return headroom_w


def _keep_recharged(
    inputs: PlanInputs,
    slots: Sequence[SimSlot],
    intents: Sequence[BatteryIntent],
    params: BatteryParams,
) -> list[BatteryIntent]:
    """``intents`` mit ``HALTEN`` in jedem Slot ``EIGENVERBRAUCH`` nach dem ersten Nachladen, der
    in der Nachrechnung unter die Reserve entlüde: Victron entlädt unter seinem Min-SOC (der
    Reserve) nicht, die Nachrechnung mit der Untergrenze des Laufs (``lo_kwh``, hier der SOC zu
    Beginn) schon. Ohne Nachladen unverändert."""
    first = next((i for i, intent in enumerate(intents) if intent.reserve_recharge), None)
    if first is None:
        return list(intents)
    reserve = reserve_kwh(inputs)
    result = list(intents)
    soc_kwh = inputs.battery.soc_pct / 100 * params.capacity_kwh
    for index, slot in enumerate(slots):
        item = step(soc_kwh, slot, result[index], params)
        if (
            index > first
            and result[index].kind is BatteryIntentKind.EIGENVERBRAUCH
            and item.discharge_w > 0
            and item.soc_end_kwh < reserve - SOC_TOL_KWH
        ):
            result[index] = BatteryIntent(kind=BatteryIntentKind.HALTEN)
            item = step(soc_kwh, slot, result[index], params)
        soc_kwh = item.soc_end_kwh
    return result


def _recharge(
    inputs: PlanInputs,
    ev_w: Sequence[Mapping[str, float]],
    slots: Sequence[SimSlot],
    intents: list[BatteryIntent],
    run: Callable[[Sequence[BatteryIntent]], list[SimStep]],
) -> list[BatteryIntent]:
    """Nachladen auf die Reserve (Entscheidung 9): Liegt der SOC zu Beginn unter der Reserve und
    erreicht die Nachrechnung sie in den ersten 24 h nicht (etwa durch PV), lädt der Plan in den
    billigsten Slots dieser 24 h (beim Gleichstand der frühere) ``NETZLADEN`` mit
    ``reserve_recharge``, je Slot ``min(RESERVE_RECHARGE_MAX_W, recharge_cap_w, Platz)`` (Platz
    unter dem höchsten Netzbezug und der Grenze je Phase, auf ``ROUND_W`` abgerundet), der letzte
    nur den Rest (auf ``ROUND_W`` aufgerundet), bis die Nachrechnung die Reserve erreicht. ``run``
    rechnet nach und hält das Nachgeladene (``_keep_recharged``)."""
    reserve = reserve_kwh(inputs)
    window = range(min(len(slots), RECHARGE_SLOTS))
    params = params_of(inputs)

    def missing_kwh(steps: Sequence[SimStep]) -> float:
        return reserve - max(steps[index].soc_end_kwh for index in window)

    if inputs.battery.soc_pct >= inputs.battery.reserve_pct or not window:
        return intents
    caps_w = recharge_caps_w(inputs)
    steps = run(intents)
    for candidate in sorted(window, key=lambda index: (inputs.slots[index].import_ct, index)):
        missing = missing_kwh(steps)
        if missing <= SOC_TOL_KWH:
            break
        if intents[candidate].kind is not BatteryIntentKind.EIGENVERBRAUCH:
            continue
        cap_w = min(
            RESERVE_RECHARGE_MAX_W,
            caps_w[candidate],
            _grid_headroom_w(inputs, ev_w, slots, steps, candidate),
        )
        need_w = missing / params.eta_ch / SLOT_H * 1000
        power_w = min(ROUND_W * floor(cap_w / ROUND_W), ROUND_W * ceil(need_w / ROUND_W))
        if power_w <= 0:
            continue
        intents = list(intents)
        intents[candidate] = BatteryIntent(
            kind=BatteryIntentKind.NETZLADEN, grid_charge_w=power_w, reserve_recharge=True
        )
        steps = run(intents)
    return intents


def fallback_intents(
    inputs: PlanInputs, ev_w: Sequence[Mapping[str, float]] = ()
) -> list[BatteryIntent]:
    """Absicht je Slot des Ersatzplans für den Akku; ``ev_w`` ist die Ladeleistung der Autos je
    Slot (``per_slot(fallback_vehicles(…))``), die die Nachrechnung mitrechnet. Das Netzladen
    bleibt unter dem höchsten Netzbezug und, wenn ``grid_phase_import_w`` gesetzt ist, unter der
    Grenze je Phase: ``3 · min_p (Grenze − max(0, Last − PV) / 3 − Σ Ladeleistung der Autos ·
    Anteil auf p)``; hier zählt die ganze Ladeleistung der Autos (Entscheidung 6). Vor allem
    anderen lädt er unter der Reserve nach (``_recharge``, auch ohne teure Slots und ohne
    Schalter); was nachgeladen ist, hält er (``_keep_recharged``)."""
    count = len(inputs.slots)
    params = params_of(inputs)
    slots = sim_slots(inputs, ev_w)
    soc0_kwh = inputs.battery.soc_pct / 100 * params.capacity_kwh

    def run(plan: Sequence[BatteryIntent]) -> list[SimStep]:
        return simulate(soc0_kwh, slots, _keep_recharged(inputs, slots, plan, params), params)

    intents = _recharge(inputs, ev_w, slots, [BatteryIntent() for _ in range(count)], run)
    expensive = expensive_today(inputs)
    if not expensive:
        return _keep_recharged(inputs, slots, intents, params)

    planning = inputs.planning
    prices = [slot.import_ct for slot in inputs.slots]
    is_expensive = set(expensive)

    steps = run(intents)

    # Halten: die billigsten entladenden Slots vor dem ersten knappen teuren Slot.
    for _ in range(count):
        short = _first_short(steps, expensive)
        if short is None:
            break
        candidates = [
            i
            for i in range(short)
            if i not in is_expensive
            and intents[i].kind is BatteryIntentKind.EIGENVERBRAUCH
            and steps[i].discharge_w > 0
        ]
        if not candidates:
            break
        chosen = min(candidates, key=lambda i: (prices[i], i))
        intents[chosen] = BatteryIntent(kind=BatteryIntentKind.HALTEN)
        steps = run(intents)

    if not planning.grid_charge_allowed:
        return _keep_recharged(inputs, slots, intents, params)

    # Netzladen: noch immer knappe teure Slots, billige Slots davor laden aus dem Netz.
    for _ in range(count):
        short = _first_short(steps, expensive)
        if short is None:
            break
        for candidate in sorted(
            (
                i
                for i in range(short)
                if i not in is_expensive and intents[i].kind is not BatteryIntentKind.NETZLADEN
            ),
            key=lambda i: (prices[i], i),
        ):
            if not spread_pays(planning, prices[candidate], prices[short]):
                continue
            # Unter dem höchsten Netzbezug bleibt neben Haus und Autos Platz fürs Netzladen
            headroom_w = _grid_headroom_w(inputs, ev_w, slots, steps, candidate)
            cap_w = min(grid_charge_cap_w(inputs, candidate), headroom_w)
            power_w = ROUND_W * floor(cap_w / ROUND_W)
            if power_w <= 0:
                continue
            trial = list(intents)
            trial[candidate] = BatteryIntent(
                kind=BatteryIntentKind.NETZLADEN, grid_charge_w=power_w
            )
            trial_steps = run(trial)
            if trial_steps[candidate].charge_grid_w < SHORT_W:
                continue  # kein Platz im Akku: das Netzladen brächte nichts
            # Regel R1 für jedes Netzladen: Ein früheres kann den Akku vor einem schon
            # angenommenen über die Leer-Grenze heben und dessen Fenster in die Einspeisung ziehen.
            if any(
                trial[i].kind is BatteryIntentKind.NETZLADEN
                and export_while_charged_kwh(trial_steps, i, reserve_kwh(inputs)) > R1_TOL_KWH
                for i in range(count)
            ):
                continue
            intents, steps = trial, trial_steps
            break
        else:
            break
    return _keep_recharged(inputs, slots, intents, params)

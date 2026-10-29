"""Erklärungen je Slot (Plan 3b, Präzisierung 12): Grundcodes aus Absicht und Nachrechnung und
ihre Sätze.

Der Hauptcode eines Slots folgt der ersten passenden Regel; dazu kommen ``ERSATZPLAN`` an erster
und ``GESCHAETZT`` an letzter Stelle. Nach dem Hauptcode stehen die Codes der geplanten Autos
(Plan 4b, Präzisierung 13), im Slot der Ankunft eines erwarteten Autos zuerst ``AUTO_ERWARTET``
(Plan 5b, Präzisierung 10). Eine Verschiebung der Klimaanlage, die an den core geht (Plan 5c),
nennt ``KLIMA_MEHR`` oder ``KLIMA_WENIGER`` vor ``GESCHAETZT`` (``with_thermal``). Reine Rechnung
ohne Zugriff auf Zeit oder Konfiguration.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from bindaems.app.planning.battery_model import SimStep, params_of
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.plausibility import (
    EMPTY_TOL_KWH,
    MENTION_W,
    R1_TOL_KWH,
    charged_window_end,
    export_while_charged_kwh,
    pays_off,
    spread_pays,
)
from bindaems.app.planning.thermal_shift import STEP_K
from bindaems.app.planning.vehicles import EV_ON_W, KWH_EPS, charge_window
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind, PlanSlot, PlanVehicle, Reason
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, ensure_utc

Params = Mapping[str, float | str]


def _de(value: float, digits: int) -> str:
    """Zahl mit Dezimalkomma und ``digits`` Nachkommastellen; Hälften runden auf (7,25 → 7,3)."""
    quantum = Decimal(1).scaleb(-digits)
    rounded = Decimal(repr(float(value))).quantize(quantum, rounding=ROUND_HALF_UP)
    if rounded == 0:
        rounded = abs(rounded)  # kein „-0,0“
    return format(rounded, f".{digits}f").replace(".", ",")


def _hhmm(moment: datetime) -> str:
    return ensure_utc(moment).astimezone(LOCAL_TZ).strftime("%H:%M")


def _num(params: Params, key: str) -> float:
    return float(params[key])


def _txt(params: Params, key: str) -> str:
    return str(params[key])


def _auto_wartet(params: Params) -> str:
    # Pläne vor der Uhrzeit „bis“ tragen nur den Namen und behalten ihren Satz.
    if "um" not in params:
        return f"{_txt(params, 'name')} wartet auf PV-Überschuss (Regel R)."
    return (
        f"{_txt(params, 'name')} wartet bis {_txt(params, 'um')} Uhr auf PV-Überschuss (Regel R)."
    )


def _klima_mehr(params: Params) -> str:
    verb = "Vorkühlen" if _txt(params, "mode") == "kuehlen" else "Vorheizen"
    k = _de(abs(_num(params, "k")), 1)
    return f"{verb} um {k} K, weil Strom jetzt günstig ist oder PV übrig bleibt."


def _klima_weniger(params: Params) -> str:
    k = _de(abs(_num(params, "k")), 1)
    if _txt(params, "mode") == "kuehlen":
        return f"Kühlung {k} K schwächer, weil Strom jetzt teuer ist."
    return f"Heizung {k} K tiefer, weil Strom jetzt teuer ist."


_SENTENCES: dict[str, Callable[[Params], str]] = {
    "NETZLADEN_GUENSTIG": lambda p: (
        f"Akku lädt {_de(_num(p, 'kw'), 1)} kW aus dem Netz: jetzt {_de(_num(p, 'ct'), 1)} ct, "
        f"um {_txt(p, 'um')} Uhr {_de(_num(p, 'ct_spaeter'), 1)} ct."
    ),
    "HALTEN_FUER_SPITZE": lambda p: (
        f"Akku hält {_de(_num(p, 'soc'), 0)} %, weil {_txt(p, 'von')}–{_txt(p, 'bis')} Uhr "
        f"{_de(_num(p, 'ct'), 1)} ct erwartet werden."
    ),
    "BUDGET_SPITZE": lambda p: (
        f"Akku hält {_de(_num(p, 'soc'), 0)} %, damit die Monatsspitze unter "
        f"{_de(_num(p, 'kw'), 1)} kW bleibt."
    ),
    "HALTEN_RESTWERT": lambda p: (
        f"Akku hält {_de(_num(p, 'soc'), 0)} % für die Zeit nach {_txt(p, 'um')} Uhr."
    ),
    "PV_LADEN": lambda p: f"Akku lädt {_de(_num(p, 'kw'), 1)} kW aus PV-Überschuss.",
    "PV_UEBERSCHUSS_ERWARTET": lambda p: (
        f"Akku deckt den Verbrauch; ab {_txt(p, 'um')} Uhr lädt ihn die PV wieder."
    ),
    "REGEL_R_BLOCKIERT_NETZLADEN": lambda p: (
        f"Kein Netzladen: Ab {_txt(p, 'um')} Uhr speist die PV laut Prognose "
        f"{_de(_num(p, 'kwh'), 1)} kWh ein, bevor der Akku leer ist (Regel R)."
    ),
    "NETZLADEN_AUS": lambda p: (
        f"Netzladen würde sich lohnen (jetzt {_de(_num(p, 'ct'), 1)} ct, um {_txt(p, 'um')} Uhr "
        f"{_de(_num(p, 'ct_spaeter'), 1)} ct), ist aber ausgeschaltet."
    ),
    "AKKU_AN_UNTERGRENZE": lambda p: (
        f"Akku steht an der Untergrenze ({_de(_num(p, 'soc'), 0)} %), "
        "das Haus bezieht aus dem Netz."
    ),
    "EIGENVERBRAUCH": lambda _: "Akku deckt den Eigenverbrauch.",
    "UNTERSTUETZUNG_RESERVIERT": lambda p: (
        f"Akku-Unterstützung fürs Auto erst ab {_de(_num(p, 'soc'), 0)} %, weil der Plan den "
        f"Akku bis {_hhmm(datetime.fromisoformat(_txt(p, 'um')))} Uhr braucht."
    ),
    "GESCHAETZT": lambda _: "Preis geschätzt.",
    "ERSATZPLAN": lambda _: "Ersatzplan.",
    "ZIEL_SOC_ABFAHRT": lambda p: (
        f"{_txt(p, 'name')} lädt {_de(_num(p, 'kw'), 1)} kW, damit es um {_txt(p, 'um')} Uhr "
        f"{_de(_num(p, 'soc'), 0)} % hat."
    ),
    "AUTO_WARTET_AUF_PV": _auto_wartet,
    "ZIEL_SOC_UNERREICHBAR": lambda p: (
        f"{_txt(p, 'name')} erreicht bis {_txt(p, 'um')} Uhr voraussichtlich nur "
        f"{_de(_num(p, 'soc'), 0)} %."
    ),
    "AUTO_ERWARTET": lambda p: (
        f"{_txt(p, 'name')} wird um {_txt(p, 'um')} Uhr mit etwa {_de(_num(p, 'soc'), 0)} % "
        "erwartet (Anwesenheitsprognose)."
    ),
    "AUTO_AUS_AKKU": lambda p: (
        f"{_txt(p, 'name')} lädt {_de(_num(p, 'kw'), 1)} kW aus dem Akku statt aus dem Netz."
    ),
    "SOFORT_AUS_AKKU": lambda p: (
        f"Autos in Sofort dürfen aus dem Akku laden, solange er über {_de(_num(p, 'soc'), 0)} % "
        "liegt."
    ),
    "KLIMA_MEHR": _klima_mehr,
    "KLIMA_WENIGER": _klima_weniger,
}


def sentence(reason: Reason) -> str:
    """Satz zu einem Grundcode, wörtlich nach „Texte“."""
    return _SENTENCES[reason.code](reason.params)


def slot_text(slot: PlanSlot) -> str:
    """Text eines Slots: die Sätze seiner Codes in Reihenfolge, durch ein Leerzeichen getrennt."""
    return " ".join(sentence(reason) for reason in slot.reasons)


def thermal_reason(mode: str, steps: int | None) -> Reason | None:
    """Grundcode einer Verschiebung der Klimaanlage im Slot (Plan 5c): ``KLIMA_MEHR`` bei
    ``steps > 0``, ``KLIMA_WENIGER`` bei ``steps < 0``, sonst ``None``. Parameter ``mode``
    (``heizen`` oder ``kuehlen``) und ``k`` (``steps · 0,5`` in K, mit Vorzeichen; der Satz nennt
    den Betrag)."""
    if steps is None or steps == 0:
        return None
    code = "KLIMA_MEHR" if steps > 0 else "KLIMA_WENIGER"
    return Reason(code=code, params={"mode": mode, "k": steps * STEP_K})


def with_thermal(reasons: Sequence[Reason], reason: Reason | None) -> list[Reason]:
    """``reasons`` mit dem Grundcode der Klimaanlage ``reason`` vor einem ``GESCHAETZT`` am Ende
    (sonst am Ende); ohne ``reason`` unverändert."""
    result = list(reasons)
    if reason is None:
        return result
    at = len(result) - 1 if result and result[-1].code == "GESCHAETZT" else len(result)
    result.insert(at, reason)
    return result


def _soc_end_pct(inputs: PlanInputs, steps: Sequence[SimStep], index: int) -> float:
    return steps[index].soc_end_kwh / params_of(inputs).capacity_kwh * 100


def _spike_block(
    inputs: PlanInputs, steps: Sequence[SimStep], index: int
) -> tuple[int, int] | None:
    """Erster Block zusammenhängender Slots nach ``index`` mit Entladung ab ``MENTION_W`` und
    höherem Preis als im Slot ``index``."""
    now_ct = inputs.slots[index].import_ct

    def member(later: int) -> bool:
        return steps[later].discharge_w >= MENTION_W and inputs.slots[later].import_ct > now_ct

    count = len(steps)
    first = next((later for later in range(index + 1, count) if member(later)), None)
    if first is None:
        return None
    last = first
    while last + 1 < count and member(last + 1):
        last += 1
    return first, last


def _budget_peak(
    inputs: PlanInputs, steps: Sequence[SimStep], index: int, budget_kw: float | None
) -> bool:
    """Entlädt später ein Slot, dessen Last abzüglich PV das Budget übersteigt?"""
    if budget_kw is None:
        return False
    return any(
        steps[later].discharge_w >= MENTION_W
        and inputs.slots[later].load_w - inputs.slots[later].pv_w > budget_kw * 1000
        for later in range(index + 1, len(steps))
    )


def _first_paying_discharge(
    inputs: PlanInputs, steps: Sequence[SimStep], index: int
) -> tuple[int, float] | None:
    """Erster späterer Slot mit Entladung ab ``MENTION_W``, für den die Spread-Regel mit dem
    Preis von ``index`` gilt, und sein Bezugspreis; ``None``, wenn es keinen gibt.

    Das Netzladen im Slot ``index`` dient der nächsten solchen Entladung, nicht der teuersten im
    Horizont (die ``pays_off`` für Plausibilität und Absichten sucht).
    """
    now_ct = inputs.slots[index].import_ct
    for later in range(index + 1, len(steps)):
        later_ct = inputs.slots[later].import_ct
        if steps[later].discharge_w >= MENTION_W and spread_pays(inputs.planning, now_ct, later_ct):
            return later, later_ct
    return None


def _at_floor(inputs: PlanInputs, item: SimStep) -> bool:
    """Der Akku steht an der Untergrenze: er entlädt weniger als ``MENTION_W``, das Haus bezieht
    mindestens ``MENTION_W`` aus dem Netz (ohne die Autos), und der SOC am Slotbeginn liegt
    höchstens ``EMPTY_TOL_KWH`` über ``lo_kwh``."""
    return (
        item.discharge_w < MENTION_W
        and item.import_w - item.ev_grid_w >= MENTION_W
        and item.soc_start_kwh <= params_of(inputs).lo_kwh + EMPTY_TOL_KWH
    )


def _main_reason(
    inputs: PlanInputs,
    intents: Sequence[BatteryIntent],
    steps: Sequence[SimStep],
    budgets: Sequence[float | None],
    index: int,
) -> Reason:
    """Hauptcode des Slots ``index``; die erste passende Regel gilt, in dieser Reihenfolge
    (wie die Tabelle in docs/betrieb.md, „Planung“):

    1. ``NETZLADEN_GUENSTIG`` bei Absicht ``NETZLADEN``; „um“ ist die erste spätere Entladung, für
       die sich das Laden auszahlt (``_first_paying_discharge``), sonst der teuerste spätere Slot.
    2. ``HALTEN_FUER_SPITZE``, ``BUDGET_SPITZE`` oder ``HALTEN_RESTWERT`` bei Absicht ``HALTEN``.
    3. ``PV_LADEN``, wenn der Akku mindestens ``MENTION_W`` aus PV lädt.
    4. ``REGEL_R_BLOCKIERT_NETZLADEN`` bzw. ``NETZLADEN_AUS``, wenn sich Netzladen lohnen würde.
    5. ``PV_UEBERSCHUSS_ERWARTET``, wenn der Akku entlädt und später die PV ihn lädt.
    6. ``AKKU_AN_UNTERGRENZE``, wenn der Akku an der Untergrenze steht und das Haus aus dem Netz
       bezieht (``_at_floor``); ``soc`` ist ``lo_kwh`` in % der Kapazität.
    7. ``EIGENVERBRAUCH`` sonst.
    """
    slots = inputs.slots
    count = len(slots)
    horizon_end = _hhmm(slots[-1].start + SLOT)
    now_ct = slots[index].import_ct
    intent = intents[index]
    item = steps[index]

    if intent.kind is BatteryIntentKind.NETZLADEN:
        hit = _first_paying_discharge(inputs, steps, index)
        if hit is not None:
            later, later_ct = hit
        elif index + 1 < count:
            later = max(range(index + 1, count), key=lambda i: (slots[i].import_ct, -i))
            later_ct = slots[later].import_ct
        else:
            later, later_ct = index, now_ct
        return Reason(
            code="NETZLADEN_GUENSTIG",
            params={
                "kw": intent.grid_charge_w / 1000,
                "ct": now_ct,
                "um": _hhmm(slots[later].start),
                "ct_spaeter": later_ct,
            },
        )

    if intent.kind is BatteryIntentKind.HALTEN:
        soc = _soc_end_pct(inputs, steps, index)
        block = _spike_block(inputs, steps, index)
        if block is not None:
            first, last = block
            return Reason(
                code="HALTEN_FUER_SPITZE",
                params={
                    "soc": soc,
                    "von": _hhmm(slots[first].start),
                    "bis": _hhmm(slots[last].start + SLOT),
                    "ct": max(slots[i].import_ct for i in range(first, last + 1)),
                },
            )
        budget = budgets[index]
        if budget is not None and _budget_peak(inputs, steps, index, budget):
            return Reason(code="BUDGET_SPITZE", params={"soc": soc, "kw": budget})
        return Reason(code="HALTEN_RESTWERT", params={"soc": soc, "um": horizon_end})

    if item.charge_pv_w >= MENTION_W:
        return Reason(code="PV_LADEN", params={"kw": item.charge_pv_w / 1000})

    # „Lohnt sich“: ein späterer Slot bezieht in der Nachrechnung Netzstrom (Präzisierung 12).
    hit = pays_off(inputs, steps, index, by="import")
    switch = inputs.planning.grid_charge_allowed
    if switch and hit is not None:
        lo_kwh = params_of(inputs).lo_kwh
        exported = export_while_charged_kwh(steps, index, lo_kwh)
        if exported > R1_TOL_KWH:
            # Beginn des ersten Slots mit Einspeisung, solange der Akku noch geladen ist
            first = next(
                later
                for later in range(index, charged_window_end(steps, index, lo_kwh))
                if steps[later].export_w > 0
            )
            return Reason(
                code="REGEL_R_BLOCKIERT_NETZLADEN",
                params={"um": _hhmm(slots[first].start), "kwh": exported},
            )
    if not switch and hit is not None:
        later, later_ct = hit
        return Reason(
            code="NETZLADEN_AUS",
            params={"ct": now_ct, "um": _hhmm(slots[later].start), "ct_spaeter": later_ct},
        )
    if item.discharge_w >= MENTION_W:
        refill = next(
            (later for later in range(index + 1, count) if steps[later].charge_pv_w >= MENTION_W),
            None,
        )
        if refill is not None:
            return Reason(code="PV_UEBERSCHUSS_ERWARTET", params={"um": _hhmm(slots[refill].start)})
    if _at_floor(inputs, item):
        params = params_of(inputs)
        return Reason(
            code="AKKU_AN_UNTERGRENZE",
            params={"soc": params.lo_kwh / params.capacity_kwh * 100},
        )
    return Reason(code="EIGENVERBRAUCH")


def _car_reasons(
    inputs: PlanInputs,
    ev_w: Sequence[Mapping[str, float]],
    vehicles: Sequence[PlanVehicle],
    ev_battery_w: Sequence[Mapping[str, float]] = (),
) -> list[list[Reason]]:
    """Codes der geplanten Autos je Slot, je Auto in der Reihenfolge der Priorität
    (``inputs.vehicles``; Präzisierung 13).

    Die PV eines Slots (Überschuss über das Haus) geht in dieser Reihenfolge an die Autos, der
    Rest ihrer Ladeleistung kommt aus dem Akku (``ev_battery_w``, Akku ins Auto) und aus dem Netz,
    wie in der Nachrechnung.

    - ``ZIEL_SOC_ABFAHRT`` in Slots, in denen das Auto mindestens ``MENTION_W`` aus dem Netz lädt
      (Ladeleistung minus PV minus Akku); ``kw`` ist die Ladeleistung des Autos, ``soc`` das Ziel,
      wenn es erreichbar ist, sonst der erwartete Ladestand.
    - ``AUTO_AUS_AKKU`` in Slots, in denen das Auto mindestens ``MENTION_W`` aus dem Akku lädt, nach
      ``ZIEL_SOC_ABFAHRT`` desselben Autos; ``kw`` ist der Akkuanteil.
    - ``AUTO_WARTET_AUF_PV`` im ersten Slot jeder zusammenhängenden Folge von Slots vor der Abfahrt
      ohne Laden (ein erwartetes Auto erst ab seiner Ankunft), in denen Regel R2 bindet
      (``grid_allowed_kwh < horizon_need_kwh``) und ein späterer Slot davor das Auto mit mindestens
      ``MENTION_W`` aus PV lädt; ``um`` ist der Beginn des ersten solchen späteren Slots.
    - ``ZIEL_SOC_UNERREICHBAR`` im ersten Slot, wenn der Plan das Ziel nicht erreicht; nicht für
      erwartete Autos (Plan 5b).
    - ``AUTO_ERWARTET`` im Slot der Ankunft eines erwarteten Autos mit Name, Ankunft und dem
      Ladestand bei der Ankunft, vor allen übrigen Codes der Autos in diesem Slot.
    """
    count = len(inputs.slots)
    result: list[list[Reason]] = [[] for _ in range(count)]
    arrivals: list[list[Reason]] = [[] for _ in range(count)]
    summary = {vehicle.key: vehicle for vehicle in vehicles}
    pv_left = [max(0.0, slot.pv_w - slot.load_w) for slot in inputs.slots]
    for car in inputs.vehicles:
        plan = summary.get(car.key)
        watts = [ev_w[index].get(car.key, 0.0) if ev_w else 0.0 for index in range(count)]
        from_pv: list[float] = []
        for index, power_w in enumerate(watts):
            take = min(pv_left[index], power_w)
            pv_left[index] -= take
            from_pv.append(take)
        from_battery = [
            min(ev_battery_w[index].get(car.key, 0.0), watts[index] - from_pv[index])
            if ev_battery_w
            else 0.0
            for index in range(count)
        ]
        reachable = plan is None or plan.reachable
        expected = plan.expected_soc_pct if plan is not None else car.target_soc_pct
        um = _hhmm(car.departure_at)
        window = charge_window(car, inputs.slots)
        if car.expected and car.arrival_at is not None and window.start < count:
            arrivals[window.start].append(
                Reason(
                    code="AUTO_ERWARTET",
                    params={"name": car.name, "um": _hhmm(car.arrival_at), "soc": car.soc_pct},
                )
            )
        binds = car.grid_allowed_kwh < car.horizon_need_kwh - KWH_EPS
        waiting = False  # der vorige Slot gehört schon zur Wartezeit
        for index in range(count):
            from_grid = watts[index] - from_pv[index] - from_battery[index]
            if from_grid >= MENTION_W:
                result[index].append(
                    Reason(
                        code="ZIEL_SOC_ABFAHRT",
                        params={
                            "name": car.name,
                            "kw": watts[index] / 1000,
                            "soc": car.target_soc_pct if reachable else expected,
                            "um": um,
                        },
                    )
                )
            # erster späterer Slot im Fenster, in dem das wartende Auto aus PV lädt
            pv_slot: int | None = None
            if from_grid < MENTION_W and binds and index in window and watts[index] < EV_ON_W:
                pv_slot = next(
                    (
                        later
                        for later in range(index + 1, window.stop)
                        if from_pv[later] >= MENTION_W
                    ),
                    None,
                )
            if pv_slot is not None and not waiting:
                result[index].append(
                    Reason(
                        code="AUTO_WARTET_AUF_PV",
                        params={"name": car.name, "um": _hhmm(inputs.slots[pv_slot].start)},
                    )
                )
            waiting = pv_slot is not None
            if from_battery[index] >= MENTION_W:
                result[index].append(
                    Reason(
                        code="AUTO_AUS_AKKU",
                        params={"name": car.name, "kw": from_battery[index] / 1000},
                    )
                )
        if not reachable and not car.expected and count:
            result[0].append(
                Reason(
                    code="ZIEL_SOC_UNERREICHBAR",
                    params={"name": car.name, "soc": expected, "um": um},
                )
            )
    return [first + rest for first, rest in zip(arrivals, result, strict=True)]


def reasons_for(
    inputs: PlanInputs,
    intents: Sequence[BatteryIntent],
    steps: Sequence[SimStep],
    budgets: Sequence[float | None],
    *,
    fallback: bool,
    thresholds: Sequence[tuple[float, datetime]] | None = None,
    ev_w: Sequence[Mapping[str, float]] = (),
    vehicles: Sequence[PlanVehicle] = (),
    ev_battery_w: Sequence[Mapping[str, float]] = (),
    now_battery: Sequence[bool] = (),
) -> list[list[Reason]]:
    """Grundcodes je Slot: ``ERSATZPLAN`` (im Ersatzplan), Hauptcode, die Codes der geplanten Autos
    (``_car_reasons``), ``SOFORT_AUS_AKKU``, ``UNTERSTUETZUNG_RESERVIERT`` (Präzisierung 6),
    ``GESCHAETZT`` (bei Schätzpreis).

    ``thresholds`` sind Plan-Schwelle und „bis“ je Slot (``support_thresholds``); ohne sie gibt es
    den Grundcode ``UNTERSTUETZUNG_RESERVIERT`` nie. ``ev_w`` ist die Ladeleistung je Auto und Slot,
    ``ev_battery_w`` der Teil davon aus dem Akku, ``vehicles`` die Zusammenfassung je Auto
    (``builder.vehicle_summary``). ``now_battery`` markiert Slots, in denen Autos in Sofort aus dem
    Akku laden dürfen; ``SOFORT_AUS_AKKU`` steht nur im ersten Slot jeder zusammenhängenden Folge,
    mit der Plan-Schwelle des Slots als ``soc`` (ohne ``thresholds`` mit der Unterstützungsschwelle
    des Laufs).
    """
    count = len(inputs.slots)
    if not len(intents) == len(steps) == len(budgets) == count:
        raise ValueError("Absichten, Schritte und Budgets müssen zu den Slots passen")
    if thresholds is not None and len(thresholds) != count:
        raise ValueError("Schwellen müssen zu den Slots passen")
    if ev_w and len(ev_w) != count:
        raise ValueError("Ladeleistung der Autos muss zu den Slots passen")
    if ev_battery_w and len(ev_battery_w) != count:
        raise ValueError("Akkuanteil der Autos muss zu den Slots passen")
    if now_battery and len(now_battery) != count:
        raise ValueError("Sofort aus dem Akku muss zu den Slots passen")
    cars = _car_reasons(inputs, ev_w, vehicles, ev_battery_w)
    result: list[list[Reason]] = []
    for index, slot in enumerate(inputs.slots):
        reasons: list[Reason] = []
        if fallback:
            reasons.append(Reason(code="ERSATZPLAN"))
        reasons.append(_main_reason(inputs, intents, steps, budgets, index))
        reasons.extend(cars[index])
        if now_battery and now_battery[index] and (index == 0 or not now_battery[index - 1]):
            soc_pct = thresholds[index][0] if thresholds is not None else inputs.support_soc_pct
            reasons.append(Reason(code="SOFORT_AUS_AKKU", params={"soc": soc_pct}))
        if thresholds is not None and intents[index].kind is BatteryIntentKind.EIGENVERBRAUCH:
            soc_pct, until = thresholds[index]
            if soc_pct > inputs.support_soc_pct:
                reasons.append(
                    Reason(
                        code="UNTERSTUETZUNG_RESERVIERT",
                        params={"soc": soc_pct, "um": ensure_utc(until).isoformat()},
                    )
                )
        if slot.origin == "estimated":
            reasons.append(Reason(code="GESCHAETZT"))
        result.append(reasons)
    return result

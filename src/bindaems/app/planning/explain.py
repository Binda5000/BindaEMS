"""Erklärungen je Slot (Plan 3b, Präzisierung 12): Grundcodes aus Absicht und Nachrechnung und
ihre Sätze.

Der Hauptcode eines Slots folgt der ersten passenden Regel; dazu kommen ``ERSATZPLAN`` an erster
und ``GESCHAETZT`` an letzter Stelle. Reine Rechnung ohne Zugriff auf Zeit oder Konfiguration.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from bindaems.app.planning.battery_model import SimStep, params_of
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.plausibility import (
    R1_TOL_KWH,
    USEFUL_W,
    export_after_kwh,
    pays_off,
)
from bindaems.shared.plan import BatteryIntent, BatteryIntentKind, PlanSlot, Reason
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
        f"Kein Netzladen: Laut Prognose speist die PV bis {_txt(p, 'um')} Uhr noch "
        f"{_de(_num(p, 'kwh'), 1)} kWh ein (Regel R)."
    ),
    "NETZLADEN_AUS": lambda p: (
        f"Netzladen würde sich lohnen (jetzt {_de(_num(p, 'ct'), 1)} ct, um {_txt(p, 'um')} Uhr "
        f"{_de(_num(p, 'ct_spaeter'), 1)} ct), ist aber ausgeschaltet."
    ),
    "EIGENVERBRAUCH": lambda _: "Akku deckt den Eigenverbrauch.",
    "UNTERSTUETZUNG_RESERVIERT": lambda p: (
        f"Akku-Unterstützung fürs Auto erst ab {_de(_num(p, 'soc'), 0)} %, weil der Plan den "
        f"Akku bis {_hhmm(datetime.fromisoformat(_txt(p, 'um')))} Uhr braucht."
    ),
    "GESCHAETZT": lambda _: "Preis geschätzt.",
    "ERSATZPLAN": lambda _: "Ersatzplan.",
}


def sentence(reason: Reason) -> str:
    """Satz zu einem Grundcode, wörtlich nach „Texte“."""
    return _SENTENCES[reason.code](reason.params)


def slot_text(slot: PlanSlot) -> str:
    """Text eines Slots: die Sätze seiner Codes in Reihenfolge, durch ein Leerzeichen getrennt."""
    return " ".join(sentence(reason) for reason in slot.reasons)


def _soc_end_pct(inputs: PlanInputs, steps: Sequence[SimStep], index: int) -> float:
    return steps[index].soc_end_kwh / params_of(inputs).capacity_kwh * 100


def _spike_block(
    inputs: PlanInputs, steps: Sequence[SimStep], index: int
) -> tuple[int, int] | None:
    """Erster Block zusammenhängender Slots nach ``index`` mit Entladung ab ``USEFUL_W`` und
    höherem Preis als im Slot ``index``."""
    now_ct = inputs.slots[index].import_ct

    def member(later: int) -> bool:
        return steps[later].discharge_w >= USEFUL_W and inputs.slots[later].import_ct > now_ct

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
        steps[later].discharge_w >= USEFUL_W
        and inputs.slots[later].load_w - inputs.slots[later].pv_w > budget_kw * 1000
        for later in range(index + 1, len(steps))
    )


def _main_reason(
    inputs: PlanInputs,
    intents: Sequence[BatteryIntent],
    steps: Sequence[SimStep],
    budgets: Sequence[float | None],
    index: int,
) -> Reason:
    slots = inputs.slots
    count = len(slots)
    horizon_end = _hhmm(slots[-1].start + SLOT)
    now_ct = slots[index].import_ct
    intent = intents[index]
    item = steps[index]

    if intent.kind is BatteryIntentKind.NETZLADEN:
        hit = pays_off(inputs, steps, index, by="discharge")
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

    if item.charge_pv_w >= USEFUL_W:
        return Reason(code="PV_LADEN", params={"kw": item.charge_pv_w / 1000})

    # „Lohnt sich“: ein späterer Slot bezieht in der Nachrechnung Netzstrom (Präzisierung 12).
    hit = pays_off(inputs, steps, index, by="import")
    switch = inputs.planning.grid_charge_allowed
    if switch and hit is not None:
        exported = export_after_kwh(steps, index)
        if exported > R1_TOL_KWH:
            return Reason(
                code="REGEL_R_BLOCKIERT_NETZLADEN", params={"um": horizon_end, "kwh": exported}
            )
    if not switch and hit is not None:
        later, later_ct = hit
        return Reason(
            code="NETZLADEN_AUS",
            params={"ct": now_ct, "um": _hhmm(slots[later].start), "ct_spaeter": later_ct},
        )
    if item.discharge_w >= USEFUL_W:
        refill = next(
            (later for later in range(index + 1, count) if steps[later].charge_pv_w >= USEFUL_W),
            None,
        )
        if refill is not None:
            return Reason(code="PV_UEBERSCHUSS_ERWARTET", params={"um": _hhmm(slots[refill].start)})
    return Reason(code="EIGENVERBRAUCH")


def reasons_for(
    inputs: PlanInputs,
    intents: Sequence[BatteryIntent],
    steps: Sequence[SimStep],
    budgets: Sequence[float | None],
    *,
    fallback: bool,
    thresholds: Sequence[tuple[float, datetime]] | None = None,
) -> list[list[Reason]]:
    """Grundcodes je Slot: ``ERSATZPLAN`` (im Ersatzplan), Hauptcode, ``UNTERSTUETZUNG_RESERVIERT``
    (Präzisierung 6), ``GESCHAETZT`` (bei Schätzpreis).

    ``thresholds`` sind Plan-Schwelle und „bis“ je Slot (``support_thresholds``); ohne sie gibt es
    den Grundcode nie.
    """
    count = len(inputs.slots)
    if not len(intents) == len(steps) == len(budgets) == count:
        raise ValueError("Absichten, Schritte und Budgets müssen zu den Slots passen")
    if thresholds is not None and len(thresholds) != count:
        raise ValueError("Schwellen müssen zu den Slots passen")
    result: list[list[Reason]] = []
    for index, slot in enumerate(inputs.slots):
        reasons: list[Reason] = []
        if fallback:
            reasons.append(Reason(code="ERSATZPLAN"))
        reasons.append(_main_reason(inputs, intents, steps, budgets, index))
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

"""Tests der Erklärungen (Präzisierung 12): Sätze wörtlich nach „Texte“ und Hauptcode je Regel."""

from __future__ import annotations

from datetime import datetime
from typing import Any, get_args

from tests.plan_helpers import (
    SCENARIO_START,
    at,
    month,
    planned_car,
    resimulate,
    scenario,
    small_battery,
)

from bindaems.app.planning.builder import build_plan
from bindaems.app.planning.explain import reasons_for, sentence, slot_text
from bindaems.app.planning.inputs import PlanInputs
from bindaems.app.planning.vehicles import PlanVehicleInput
from bindaems.shared.plan import (
    BatteryIntent,
    BatteryIntentKind,
    Plan,
    PlanSlot,
    PlanSource,
    Reason,
    ReasonCode,
)
from bindaems.shared.timeutil import SLOT

EV = BatteryIntent()
HOLD = BatteryIntent(kind=BatteryIntentKind.HALTEN)


def charge(watts: float) -> BatteryIntent:
    return BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=watts)


SAMPLE_PARAMS: dict[str, dict[str, float | str]] = {
    "NETZLADEN_GUENSTIG": {"kw": 2.3, "ct": 10.0, "um": "18:00", "ct_spaeter": 40.0},
    "HALTEN_FUER_SPITZE": {"soc": 64.4, "von": "18:00", "bis": "20:00", "ct": 38.0},
    "BUDGET_SPITZE": {"soc": 55.0, "kw": 4.3},
    "HALTEN_RESTWERT": {"soc": 70.0, "um": "05:00"},
    "PV_LADEN": {"kw": 3.0},
    "PV_UEBERSCHUSS_ERWARTET": {"um": "09:30"},
    "REGEL_R_BLOCKIERT_NETZLADEN": {"um": "12:00", "kwh": 7.25},
    "NETZLADEN_AUS": {"ct": 10.0, "um": "18:00", "ct_spaeter": 40.0},
    "EIGENVERBRAUCH": {},
    "UNTERSTUETZUNG_RESERVIERT": {"soc": 70.0, "um": "2026-10-16T16:00:00+00:00"},
    "GESCHAETZT": {},
    "ERSATZPLAN": {},
    "ZIEL_SOC_ABFAHRT": {"name": "Tesla Model 3", "kw": 7.0, "soc": 80.0, "um": "07:00"},
    "AUTO_WARTET_AUF_PV": {"name": "e-Golf"},
    "ZIEL_SOC_UNERREICHBAR": {"name": "e-Golf", "soc": 62.4, "um": "06:30"},
}


def codes(
    inputs: PlanInputs,
    intents: list[BatteryIntent],
    *,
    budgets: list[float | None] | None = None,
    fallback: bool = False,
) -> list[list[str]]:
    steps = resimulate(inputs, intents)
    result = reasons_for(
        inputs, intents, steps, budgets or [None] * len(intents), fallback=fallback
    )
    return [[r.code for r in reasons] for reasons in result]


def test_sentences() -> None:
    assert (
        sentence(
            Reason(
                code="NETZLADEN_GUENSTIG",
                params={"kw": 2.3, "ct": 10.0, "um": "18:00", "ct_spaeter": 40.0},
            )
        )
        == "Akku lädt 2,3 kW aus dem Netz: jetzt 10,0 ct, um 18:00 Uhr 40,0 ct."
    )
    assert (
        sentence(
            Reason(
                code="HALTEN_FUER_SPITZE",
                params={"soc": 64.4, "von": "18:00", "bis": "20:00", "ct": 38.0},
            )
        )
        == "Akku hält 64 %, weil 18:00–20:00 Uhr 38,0 ct erwartet werden."
    )
    assert (
        sentence(Reason(code="REGEL_R_BLOCKIERT_NETZLADEN", params={"um": "12:00", "kwh": 7.25}))
        == "Kein Netzladen: Laut Prognose speist die PV bis 12:00 Uhr noch 7,3 kWh ein (Regel R)."
    )


def test_all_sentences_match_the_texts_exactly() -> None:
    expected = {
        "NETZLADEN_GUENSTIG": "Akku lädt 2,3 kW aus dem Netz: jetzt 10,0 ct, um 18:00 Uhr 40,0 ct.",
        "HALTEN_FUER_SPITZE": "Akku hält 64 %, weil 18:00–20:00 Uhr 38,0 ct erwartet werden.",
        "BUDGET_SPITZE": "Akku hält 55 %, damit die Monatsspitze unter 4,3 kW bleibt.",
        "HALTEN_RESTWERT": "Akku hält 70 % für die Zeit nach 05:00 Uhr.",
        "PV_LADEN": "Akku lädt 3,0 kW aus PV-Überschuss.",
        "PV_UEBERSCHUSS_ERWARTET": "Akku deckt den Verbrauch; ab 09:30 Uhr lädt ihn die PV wieder.",
        "REGEL_R_BLOCKIERT_NETZLADEN": (
            "Kein Netzladen: Laut Prognose speist die PV bis 12:00 Uhr noch 7,3 kWh ein (Regel R)."
        ),
        "NETZLADEN_AUS": (
            "Netzladen würde sich lohnen (jetzt 10,0 ct, um 18:00 Uhr 40,0 ct), "
            "ist aber ausgeschaltet."
        ),
        "EIGENVERBRAUCH": "Akku deckt den Eigenverbrauch.",
        "UNTERSTUETZUNG_RESERVIERT": (
            "Akku-Unterstützung fürs Auto erst ab 70 %, "
            "weil der Plan den Akku bis 18:00 Uhr braucht."
        ),
        "GESCHAETZT": "Preis geschätzt.",
        "ERSATZPLAN": "Ersatzplan.",
        "ZIEL_SOC_ABFAHRT": "Tesla Model 3 lädt 7,0 kW, damit es um 07:00 Uhr 80 % hat.",
        "AUTO_WARTET_AUF_PV": "e-Golf wartet auf PV-Überschuss (Regel R).",
        "ZIEL_SOC_UNERREICHBAR": "e-Golf erreicht bis 06:30 Uhr voraussichtlich nur 62 %.",
    }
    assert set(expected) == set(get_args(ReasonCode))
    for code, text in expected.items():
        assert sentence(Reason(code=code, params=SAMPLE_PARAMS[code])) == text  # type: ignore[arg-type]


def test_reserved_sentence() -> None:
    reason = Reason(
        code="UNTERSTUETZUNG_RESERVIERT",
        params={"soc": 82, "um": "2026-10-16T19:00:00+00:00"},
    )
    assert sentence(reason) == (
        "Akku-Unterstützung fürs Auto erst ab 82 %, weil der Plan den Akku bis 21:00 Uhr braucht."
    )


def test_every_code_has_a_sentence() -> None:
    for code in get_args(ReasonCode):
        assert sentence(Reason(code=code, params=SAMPLE_PARAMS[code])).endswith(".")


def test_numbers_use_decimal_comma_and_round_half_up() -> None:
    plain = Reason(code="PV_LADEN", params={"kw": 0.05})
    assert sentence(plain) == "Akku lädt 0,1 kW aus PV-Überschuss."
    percent = Reason(code="HALTEN_RESTWERT", params={"soc": 64.5, "um": "05:00"})
    assert sentence(percent) == "Akku hält 65 % für die Zeit nach 05:00 Uhr."


def test_slot_text_joins_sentences_with_a_space() -> None:
    inputs = scenario(prices=[20.0] * 2, pv=[0.0] * 2, load=[500.0] * 2)
    slot = PlanSlot.model_validate(
        {
            "start": inputs.slots[0].start,
            "intent": EV,
            "soc_start_pct": 50.0,
            "soc_end_pct": 50.0,
            "pv_w": 0.0,
            "load_w": 0.0,
            "import_w": 0.0,
            "export_w": 0.0,
            "charge_pv_w": 0.0,
            "charge_grid_w": 0.0,
            "discharge_w": 0.0,
            "import_ct": 20.0,
            "feed_in_ct": None,
            "estimated": True,
            "cost_ct": 0.0,
            "budget_kw": None,
            "reasons": [Reason(code="EIGENVERBRAUCH"), Reason(code="GESCHAETZT")],
        }
    )
    assert slot_text(slot) == "Akku deckt den Eigenverbrauch. Preis geschätzt."


def test_grid_charge_names_the_later_slot_that_pays_off() -> None:
    inputs = scenario(
        prices=[10.0] * 4 + [40.0] * 4,
        pv=[0.0] * 8,
        load=[2000.0] * 8,
        soc_pct=20.0,
        grid_charge=True,
        battery=small_battery(),
    )
    intents = [charge(2300.0)] * 4 + [EV] * 4
    steps = resimulate(inputs, intents)
    (first, *_), *_ = reasons_for(inputs, intents, steps, [None] * 8, fallback=False)
    assert first.code == "NETZLADEN_GUENSTIG"
    assert first.params == {"kw": 2.3, "ct": 10.0, "um": "03:00", "ct_spaeter": 40.0}


def test_grid_charge_without_payoff_names_the_most_expensive_later_slot() -> None:
    # Die Batterie ist voll genug, dass kein späterer Slot entlädt, die Spread-Regel also nie
    # greift; der teuerste spätere Slot (bei Gleichstand der frühere) wird genannt.
    inputs = scenario(
        prices=[10.0, 12.0, 30.0, 30.0],
        pv=[0.0] * 4,
        load=[0.0] * 4,
        soc_pct=20.0,
        grid_charge=True,
        battery=small_battery(),
    )
    intents = [charge(1000.0), EV, EV, EV]
    steps = resimulate(inputs, intents)
    (first, *_), *_ = reasons_for(inputs, intents, steps, [None] * 4, fallback=False)
    assert first.code == "NETZLADEN_GUENSTIG"
    assert first.params["ct_spaeter"] == 30.0 and first.params["um"] == "02:30"


def test_hold_for_expensive_block_beats_budget_and_remaining_value() -> None:
    inputs = scenario(prices=[20.0] * 4 + [38.0] * 8, pv=[0.0] * 12, load=[1000.0] * 12)
    result = codes(inputs, [HOLD] * 4 + [EV] * 8, budgets=[1.0] * 12)
    assert result[0] == ["HALTEN_FUER_SPITZE"]


def test_hold_for_budget_when_a_later_load_peak_discharges() -> None:
    # Gleicher Preis: kein „teurerer Block“, aber ein Lastspitzen-Slot über dem Budget.
    load = [500.0] * 3 + [4000.0] + [500.0] * 2
    inputs = scenario(prices=[20.0] * 6, pv=[0.0] * 6, load=load)
    result = codes(inputs, [HOLD] * 3 + [EV] * 3, budgets=[2.5] * 6)
    assert result[0] == ["BUDGET_SPITZE"]
    steps = resimulate(inputs, [HOLD] * 3 + [EV] * 3)
    reasons = reasons_for(inputs, [HOLD] * 3 + [EV] * 3, steps, [2.5] * 6, fallback=False)
    assert reasons[0][0].params["kw"] == 2.5


def test_hold_for_remaining_value_otherwise() -> None:
    inputs = scenario(prices=[20.0] * 6, pv=[0.0] * 6, load=[500.0] * 6, soc_pct=60.0)
    steps = resimulate(inputs, [HOLD] * 6)
    reasons = reasons_for(inputs, [HOLD] * 6, steps, [None] * 6, fallback=False)
    assert reasons[0][0].code == "HALTEN_RESTWERT"
    # Datum 16. 10. 2026, 00:00 UTC = 02:00 Uhr lokal; Horizontende nach sechs Slots.
    assert reasons[0][0].params == {"soc": 60.0, "um": "03:30"}


def test_pv_charge_comes_first_for_eigenverbrauch() -> None:
    inputs = scenario(prices=[20.0] * 4, pv=[3000.0] * 4, load=[500.0] * 4, soc_pct=30.0)
    steps = resimulate(inputs, [EV] * 4)
    reasons = reasons_for(inputs, [EV] * 4, steps, [None] * 4, fallback=False)
    assert reasons[0][0].code == "PV_LADEN"
    assert reasons[0][0].params == {"kw": 2.5}


def test_rule_r_blocks_grid_charge_when_pv_export_is_expected() -> None:
    # Nachts teurer Bezug später, aber am Ende speist die PV ein.
    prices = [10.0] * 2 + [40.0] * 2 + [10.0] * 4
    pv = [0.0] * 4 + [6000.0] * 4
    load = [2000.0] * 4 + [500.0] * 4
    inputs = scenario(
        prices=prices,
        pv=pv,
        load=load,
        soc_pct=20.0,
        grid_charge=True,
        battery=small_battery(usable_kwh=2.0, max_charge_w=1000.0),
    )
    steps = resimulate(inputs, [EV] * 8)
    reasons = reasons_for(inputs, [EV] * 8, steps, [None] * 8, fallback=False)
    assert reasons[0][0].code == "REGEL_R_BLOCKIERT_NETZLADEN"
    assert reasons[0][0].params["um"] == "04:00"
    assert float(reasons[0][0].params["kwh"]) > 0.1


def test_switched_off_grid_charge_is_explained() -> None:
    inputs = scenario(prices=[10.0] * 4 + [40.0] * 4, pv=[0.0] * 8, load=[2000.0] * 8, soc_pct=20.0)
    steps = resimulate(inputs, [EV] * 8)
    (first, *_), *_ = reasons_for(inputs, [EV] * 8, steps, [None] * 8, fallback=False)
    assert first.code == "NETZLADEN_AUS"
    assert first.params == {"ct": 10.0, "um": "03:00", "ct_spaeter": 40.0}


def test_pv_surplus_expected_after_a_discharging_slot() -> None:
    pv = [0.0, 0.0, 0.0, 0.0, 4000.0, 4000.0]
    inputs = scenario(prices=[20.0] * 6, pv=pv, load=[1000.0] * 6, soc_pct=60.0)
    steps = resimulate(inputs, [EV] * 6)
    reasons = reasons_for(inputs, [EV] * 6, steps, [None] * 6, fallback=False)
    assert reasons[0][0].code == "PV_UEBERSCHUSS_ERWARTET"
    assert reasons[0][0].params == {"um": "03:00"}
    assert reasons[4][0].code == "PV_LADEN"


def test_plain_eigenverbrauch_when_nothing_special() -> None:
    inputs = scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[500.0] * 4)
    assert codes(inputs, [EV] * 4) == [["EIGENVERBRAUCH"]] * 4


def test_fallback_and_estimated_codes_surround_the_main_code() -> None:
    inputs = scenario(
        prices=[20.0] * 4,
        pv=[0.0] * 4,
        load=[500.0] * 4,
        origins=["primary", "estimated", "primary", "estimated"],
    )
    assert codes(inputs, [EV] * 4, fallback=True) == [
        ["ERSATZPLAN", "EIGENVERBRAUCH"],
        ["ERSATZPLAN", "EIGENVERBRAUCH", "GESCHAETZT"],
        ["ERSATZPLAN", "EIGENVERBRAUCH"],
        ["ERSATZPLAN", "EIGENVERBRAUCH", "GESCHAETZT"],
    ]


def test_budgets_do_not_change_plain_eigenverbrauch() -> None:
    inputs = scenario(
        prices=[20.0] * 4, pv=[0.0] * 4, load=[500.0] * 4, peak_months=[month("2026-10", 2.0)]
    )
    assert codes(inputs, [EV] * 4) == [["EIGENVERBRAUCH"]] * 4


# Geplante Autos (Plan 4b, Präzisierung 13)


def _car_plan(
    ev_w: list[dict[str, float]],
    vehicles: list[PlanVehicleInput],
    *,
    pv: list[float] | None = None,
    load: float = 500.0,
    prices: list[float] | None = None,
    source: PlanSource = "optimizer",
    **kwargs: Any,
) -> Plan:
    count = len(ev_w)
    inputs = scenario(
        prices=prices or [20.0] * count,
        pv=pv or [0.0] * count,
        load=[load] * count,
        vehicles=vehicles,
        **kwargs,
    )
    return build_plan(
        inputs,
        [EV] * count,
        source=source,
        solver_status="optimal" if source == "optimizer" else "timeout",
        ev_w=ev_w,
    )


def _slot(n: int) -> datetime:
    return at(SCENARIO_START) + n * SLOT


def test_car_codes_follow_the_main_code() -> None:
    # Der Akku deckt 2 kW Haus über acht Slots, die Schwelle steigt über 20 %; 1,75 kWh decken
    # den Bedarf. Abfahrt nach acht Slots: 04:00 Ortszeit.
    car = planned_car(departure_at=_slot(8), need_kwh=1.75)
    plan = _car_plan(
        [{"tesla": 7000.0}] + [{}] * 7,
        [car],
        load=2000.0,
        source="fallback",
        support_soc_pct=20.0,
        origins=["estimated"] + ["primary"] * 7,
    )
    assert [r.code for r in plan.slots[0].reasons] == [
        "ERSATZPLAN",
        "EIGENVERBRAUCH",
        "ZIEL_SOC_ABFAHRT",
        "UNTERSTUETZUNG_RESERVIERT",
        "GESCHAETZT",
    ]
    assert slot_text(plan.slots[0]).startswith(
        "Ersatzplan. Akku deckt den Eigenverbrauch. "
        "Tesla Model 3 lädt 7,0 kW, damit es um 04:00 Uhr 80 % hat."
    )
    assert "ZIEL_SOC_ABFAHRT" not in [r.code for r in plan.slots[1].reasons]


def test_unreachable_target_is_named_in_the_first_slot() -> None:
    # 40 kWh in vier vollen Slots: 11,04 kWh, also 40 % + 13,2 Prozentpunkte
    car = planned_car(departure_at=_slot(4), need_kwh=40.0)
    plan = _car_plan([{"tesla": 11040.0}] * 4, [car])
    assert [r.code for r in plan.slots[0].reasons] == [
        "EIGENVERBRAUCH",
        "ZIEL_SOC_ABFAHRT",
        "ZIEL_SOC_UNERREICHBAR",
    ]
    assert slot_text(plan.slots[0]) == (
        "Akku deckt den Eigenverbrauch. "
        "Tesla Model 3 lädt 11,0 kW, damit es um 03:00 Uhr 53 % hat. "
        "Tesla Model 3 erreicht bis 03:00 Uhr voraussichtlich nur 53 %."
    )
    assert [r.code for r in plan.slots[1].reasons] == ["EIGENVERBRAUCH", "ZIEL_SOC_ABFAHRT"]


def test_car_waits_for_pv_when_rule_r2_binds() -> None:
    car = planned_car(departure_at=_slot(4), need_kwh=2.75, grid_allowed_kwh=0.0)
    ev_w: list[dict[str, float]] = [{}, {}, {"tesla": 5500.0}, {"tesla": 5500.0}]
    plan = _car_plan(ev_w, [car], pv=[0.0, 0.0, 6000.0, 6000.0])
    texts = [slot_text(s) for s in plan.slots]
    assert texts[0].endswith("Tesla Model 3 wartet auf PV-Überschuss (Regel R).")
    assert "AUTO_WARTET_AUF_PV" in [r.code for r in plan.slots[1].reasons]
    # Aus PV geladen: kein Grund zum Netzladen des Autos
    assert [r.code for r in plan.slots[2].reasons] == ["EIGENVERBRAUCH"]
    # Ohne bindende Regel R2 wartet es nicht
    free = car.model_copy(update={"grid_allowed_kwh": car.horizon_need_kwh})
    plan = _car_plan(ev_w, [free], pv=[0.0, 0.0, 6000.0, 6000.0])
    assert all("AUTO_WARTET_AUF_PV" not in [r.code for r in s.reasons] for s in plan.slots)


def test_cars_follow_their_priority() -> None:
    golf = planned_car(
        key="egolf", name="e-Golf", wallbox="evcs", departure_at=_slot(4), need_kwh=0.9
    )
    tesla = planned_car(departure_at=_slot(4), need_kwh=0.9)
    plan = _car_plan([{"tesla": 3600.0, "egolf": 3600.0}] + [{}] * 3, [golf, tesla])
    assert [r.params.get("name") for r in plan.slots[0].reasons[1:]] == ["e-Golf", "Tesla Model 3"]


def test_car_import_does_not_make_grid_charge_worthwhile() -> None:
    # Später teuer, aber dort bezieht nur das Auto: Der Akku könnte es nicht versorgen
    car = planned_car(departure_at=_slot(8), need_kwh=1.75)
    plan = _car_plan(
        [{}] * 6 + [{"tesla": 7000.0}, {}],
        [car],
        prices=[10.0] * 4 + [40.0] * 4,
        soc_pct=90.0,
    )
    assert plan.slots[0].reasons[0].code == "EIGENVERBRAUCH"

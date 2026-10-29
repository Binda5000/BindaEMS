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
    "AKKU_AN_UNTERGRENZE": {"soc": 20.0},
    "EIGENVERBRAUCH": {},
    "UNTERSTUETZUNG_RESERVIERT": {"soc": 70.0, "um": "2026-10-16T16:00:00+00:00"},
    "GESCHAETZT": {},
    "ERSATZPLAN": {},
    "ZIEL_SOC_ABFAHRT": {"name": "Tesla Model 3", "kw": 7.0, "soc": 80.0, "um": "07:00"},
    "AUTO_WARTET_AUF_PV": {"name": "e-Golf", "um": "09:30"},
    "ZIEL_SOC_UNERREICHBAR": {"name": "e-Golf", "soc": 62.4, "um": "06:30"},
    "AUTO_ERWARTET": {"name": "Tesla Model 3", "um": "17:30", "soc": 40.6},
    "KLIMA_MEHR": {"mode": "heizen", "k": 1.0},
    "KLIMA_WENIGER": {"mode": "kuehlen", "k": -0.5},
    "AUTO_AUS_AKKU": {"name": "Tesla Model 3", "kw": 2.5},
    "SOFORT_AUS_AKKU": {"soc": 62.0},
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
        == "Kein Netzladen: Ab 12:00 Uhr speist die PV laut Prognose 7,3 kWh ein, "
        "bevor der Akku leer ist (Regel R)."
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
            "Kein Netzladen: Ab 12:00 Uhr speist die PV laut Prognose 7,3 kWh ein, "
            "bevor der Akku leer ist (Regel R)."
        ),
        "NETZLADEN_AUS": (
            "Netzladen würde sich lohnen (jetzt 10,0 ct, um 18:00 Uhr 40,0 ct), "
            "ist aber ausgeschaltet."
        ),
        "AKKU_AN_UNTERGRENZE": (
            "Akku steht an der Untergrenze (20 %), das Haus bezieht aus dem Netz."
        ),
        "EIGENVERBRAUCH": "Akku deckt den Eigenverbrauch.",
        "UNTERSTUETZUNG_RESERVIERT": (
            "Akku-Unterstützung fürs Auto erst ab 70 %, "
            "weil der Plan den Akku bis 18:00 Uhr braucht."
        ),
        "GESCHAETZT": "Preis geschätzt.",
        "ERSATZPLAN": "Ersatzplan.",
        "ZIEL_SOC_ABFAHRT": "Tesla Model 3 lädt 7,0 kW, damit es um 07:00 Uhr 80 % hat.",
        "AUTO_WARTET_AUF_PV": "e-Golf wartet bis 09:30 Uhr auf PV-Überschuss (Regel R).",
        "ZIEL_SOC_UNERREICHBAR": "e-Golf erreicht bis 06:30 Uhr voraussichtlich nur 62 %.",
        "AUTO_ERWARTET": (
            "Tesla Model 3 wird um 17:30 Uhr mit etwa 41 % erwartet (Anwesenheitsprognose)."
        ),
        "KLIMA_MEHR": "Vorheizen um 1,0 K, weil Strom jetzt günstig ist oder PV übrig bleibt.",
        "KLIMA_WENIGER": "Kühlung 0,5 K schwächer, weil Strom jetzt teuer ist.",
        "AUTO_AUS_AKKU": "Tesla Model 3 lädt 2,5 kW aus dem Akku statt aus dem Netz.",
        "SOFORT_AUS_AKKU": (
            "Autos in Sofort dürfen aus dem Akku laden, solange er über 62 % liegt."
        ),
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


def test_grid_charge_names_the_first_paying_discharge() -> None:
    # Geladen wird für die Entladung gleich danach, nicht für den teuersten Slot im Horizont: der
    # erste spätere Slot mit Entladung, für den die Spread-Regel gilt, steht im Satz.
    inputs = scenario(
        prices=[20.0, 40.0, 40.0, 60.0],
        pv=[0.0] * 4,
        load=[1000.0] * 4,
        soc_pct=20.0,
        grid_charge=True,
        battery=small_battery(),
    )
    intents = [charge(5000.0), EV, EV, EV]
    steps = resimulate(inputs, intents)
    assert all(item.discharge_w > 100.0 for item in steps[1:])
    reasons = reasons_for(inputs, intents, steps, [None] * 4, fallback=False)
    first = reasons[0][0]
    assert first.code == "NETZLADEN_GUENSTIG"
    assert first.params["um"] == "02:15" and first.params["ct_spaeter"] == 40.0
    assert sentence(first).endswith("um 02:15 Uhr 40,0 ct.")


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


def _rule_r_scenario(soc_pct: float) -> PlanInputs:
    # Nachts teurer Bezug später (Slots 2 und 3), danach speist die PV ein (ab Slot 4, 03:00 Uhr).
    prices = [10.0] * 2 + [40.0] * 2 + [10.0] * 4
    pv = [0.0] * 4 + [6000.0] * 4
    load = [2000.0] * 4 + [500.0] * 4
    return scenario(
        prices=prices,
        pv=pv,
        load=load,
        soc_pct=soc_pct,
        grid_charge=True,
        battery=small_battery(usable_kwh=2.0, max_charge_w=1000.0),
    )


def test_rule_r_reason_names_first_export_before_empty() -> None:
    # Der Akku ist voll und wird ab Slot 1 gehalten: er ist noch geladen, wenn die PV einspeist.
    inputs = _rule_r_scenario(soc_pct=100.0)
    intents = [EV, HOLD, HOLD, HOLD] + [EV] * 4
    steps = resimulate(inputs, intents)
    reasons = reasons_for(inputs, intents, steps, [None] * 8, fallback=False)
    assert reasons[0][0].code == "REGEL_R_BLOCKIERT_NETZLADEN"
    assert reasons[0][0].params["um"] == "03:00"  # erster Slot mit Einspeisung, nicht Horizontende
    assert float(reasons[0][0].params["kwh"]) > 0.1


def test_no_rule_r_reason_when_battery_empties_before_pv() -> None:
    # Wie oben, aber der Akku ist vor der Einspeisung leer: die PV findet ihn leer vor.
    inputs = _rule_r_scenario(soc_pct=20.0)
    intents = [EV] * 8
    steps = resimulate(inputs, intents)
    assert sum(item.export_w for item in steps) > 0
    reasons = reasons_for(inputs, intents, steps, [None] * 8, fallback=False)
    assert "REGEL_R_BLOCKIERT_NETZLADEN" not in [r.code for r in reasons[0]]
    # Der Akku startet an der Reserve, das Haus bezieht aus dem Netz
    assert reasons[0][0].code == "AKKU_AN_UNTERGRENZE"


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


def test_battery_at_floor_says_so() -> None:
    # Nachts an der Reserve: der Akku gibt nichts, das Haus läuft aus dem Netz.
    inputs = scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[1000.0] * 4, soc_pct=20.0)
    steps = resimulate(inputs, [EV] * 4)
    reasons = reasons_for(inputs, [EV] * 4, steps, [None] * 4, fallback=False)
    assert [r[0].code for r in reasons] == ["AKKU_AN_UNTERGRENZE"] * 4
    assert reasons[0][0].params == {"soc": 20.0}
    assert sentence(reasons[0][0]) == (
        "Akku steht an der Untergrenze (20 %), das Haus bezieht aus dem Netz."
    )
    # derselbe Slot mit 50 %: der Akku deckt das Haus
    inputs = scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[1000.0] * 4, soc_pct=50.0)
    assert codes(inputs, [EV] * 4) == [["EIGENVERBRAUCH"]] * 4
    pv = [0.0, 0.0, 4000.0, 4000.0]
    inputs = scenario(prices=[20.0] * 4, pv=pv, load=[1000.0] * 4, soc_pct=50.0)
    assert codes(inputs, [EV] * 4)[0] == ["PV_UEBERSCHUSS_ERWARTET"]


def test_battery_below_the_reserve_names_its_own_floor() -> None:
    # Unter der Reserve gestartet (17 % bei 20 % Reserve): die Untergrenze des Laufs ist der SOC.
    inputs = scenario(prices=[20.0] * 4, pv=[0.0] * 4, load=[1000.0] * 4, soc_pct=17.0)
    steps = resimulate(inputs, [EV] * 4)
    reasons = reasons_for(inputs, [EV] * 4, steps, [None] * 4, fallback=False)
    assert reasons[0][0] == Reason(code="AKKU_AN_UNTERGRENZE", params={"soc": 17.0})


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
    assert texts[0].endswith("Tesla Model 3 wartet bis 02:30 Uhr auf PV-Überschuss (Regel R).")
    # nur im ersten Slot der Wartezeit
    assert "AUTO_WARTET_AUF_PV" not in [r.code for r in plan.slots[1].reasons]
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


# Erwartete Autos (Plan 5b, Präzisierung 10)


def _expected_car(**changes: Any) -> PlanVehicleInput:
    """Der Tesla, noch nicht angesteckt, ab dem Slot 2 (02:30 Ortszeit) mit 35 % erwartet."""
    values: dict[str, Any] = {
        "soc_pct": 35.0,
        "departure_at": _slot(6),
        "need_kwh": 2.75,
        "expected": True,
        "arrival_at": _slot(2),
    }
    return planned_car(**(values | changes))


def test_expected_car_reason_in_arrival_slot() -> None:
    # Der e-Golf steckt und lädt im selben Slot; der Grundcode des erwarteten Autos steht vor
    # den Codes der Autos aus 4b, auch vor denen eines Autos mit höherer Priorität
    golf = planned_car(
        key="egolf", name="e-Golf", wallbox="evcs", departure_at=_slot(6), need_kwh=0.9
    )
    ev_w: list[dict[str, float]] = [{}, {}, {"egolf": 3600.0, "tesla": 5500.0}, {"tesla": 5500.0}]
    plan = _car_plan(ev_w + [{}] * 4, [golf, _expected_car()])
    arrival = plan.slots[2].reasons
    assert [(r.code, r.params.get("name")) for r in arrival] == [
        ("EIGENVERBRAUCH", None),
        ("AUTO_ERWARTET", "Tesla Model 3"),
        ("ZIEL_SOC_ABFAHRT", "e-Golf"),
        ("ZIEL_SOC_ABFAHRT", "Tesla Model 3"),
    ]
    assert arrival[1].params == {"name": "Tesla Model 3", "um": "02:30", "soc": 35.0}
    assert slot_text(plan.slots[2]).startswith(
        "Akku deckt den Eigenverbrauch. "
        "Tesla Model 3 wird um 02:30 Uhr mit etwa 35 % erwartet (Anwesenheitsprognose). "
    )
    # nur im Slot der Ankunft
    assert all(
        "AUTO_ERWARTET" not in [r.code for r in s.reasons]
        for index, s in enumerate(plan.slots)
        if index != 2
    )
    (_, tesla) = plan.vehicles
    assert (tesla.expected, tesla.arrival_at) == (True, _slot(2))


def test_expected_car_has_no_unreachable_warning() -> None:
    # 40 kWh zwischen Ankunft und Abfahrt (zwei volle Slots) erreicht der Plan nicht; für ein
    # erwartetes Auto gibt es trotzdem keinen Code „Ziel-SOC unerreichbar“
    car = _expected_car(departure_at=_slot(4), need_kwh=40.0)
    plan = _car_plan([{}, {}, {"tesla": 11040.0}, {"tesla": 11040.0}], [car])
    assert not plan.vehicles[0].reachable and plan.vehicles[0].expected
    assert all("ZIEL_SOC_UNERREICHBAR" not in [r.code for r in s.reasons] for s in plan.slots)
    # dasselbe Auto angesteckt nennt ihn
    plugged = car.model_copy(update={"expected": False, "arrival_at": None})
    plan = _car_plan([{"tesla": 11040.0}] * 4, [plugged])
    assert "ZIEL_SOC_UNERREICHBAR" in [r.code for r in plan.slots[0].reasons]


def test_expected_car_waits_for_pv_only_after_arrival() -> None:
    car = _expected_car(departure_at=_slot(4), grid_allowed_kwh=0.0)
    ev_w: list[dict[str, float]] = [{}, {}, {}, {"tesla": 5500.0}]
    plan = _car_plan(ev_w, [car], pv=[0.0, 0.0, 0.0, 6000.0])
    waits = ["AUTO_WARTET_AUF_PV" in [r.code for r in s.reasons] for s in plan.slots]
    assert waits == [False, False, True, False]


def test_waiting_run_restarts_after_charging() -> None:
    # Warten, ein Slot PV-Laden, wieder warten, dann PV: je eine Nennung am Beginn der Folge
    car = planned_car(departure_at=_slot(6), need_kwh=2.75, grid_allowed_kwh=0.0)
    ev_w: list[dict[str, float]] = [{}, {}, {"tesla": 5500.0}, {}, {}, {"tesla": 5500.0}]
    plan = _car_plan(ev_w, [car], pv=[0.0, 0.0, 6000.0, 0.0, 0.0, 6000.0])
    waits = [[r.params for r in s.reasons if r.code == "AUTO_WARTET_AUF_PV"] for s in plan.slots]
    assert waits == [
        [{"name": "Tesla Model 3", "um": "02:30"}],
        [],
        [],
        [{"name": "Tesla Model 3", "um": "03:15"}],
        [],
        [],
    ]


def test_old_waiting_reason_without_time_keeps_its_sentence() -> None:
    reason = Reason(code="AUTO_WARTET_AUF_PV", params={"name": "e-Golf"})
    assert sentence(reason) == "e-Golf wartet auf PV-Überschuss (Regel R)."


# --- Vorheizen und Vorkühlen (Plan 5c) ------------------------------------------------------


def test_explain_names_preheat_and_setback() -> None:
    from bindaems.app.planning.explain import thermal_reason, with_thermal

    more = thermal_reason("heizen", 2)
    less = thermal_reason("heizen", -1)
    assert more == Reason(code="KLIMA_MEHR", params={"mode": "heizen", "k": 1.0})
    assert less == Reason(code="KLIMA_WENIGER", params={"mode": "heizen", "k": -0.5})
    assert thermal_reason("heizen", 0) is None and thermal_reason("kuehlen", None) is None
    assert sentence(more) == (
        "Vorheizen um 1,0 K, weil Strom jetzt günstig ist oder PV übrig bleibt."
    )
    assert sentence(less) == "Heizung 0,5 K tiefer, weil Strom jetzt teuer ist."
    cool_more = thermal_reason("kuehlen", 3)
    cool_less = thermal_reason("kuehlen", -2)
    assert cool_more is not None and cool_less is not None
    assert sentence(cool_more) == (
        "Vorkühlen um 1,5 K, weil Strom jetzt günstig ist oder PV übrig bleibt."
    )
    assert sentence(cool_less) == "Kühlung 1,0 K schwächer, weil Strom jetzt teuer ist."
    # vor „Preis geschätzt.“ am Ende, sonst am Ende
    main = Reason(code="EIGENVERBRAUCH")
    guess = Reason(code="GESCHAETZT")
    assert with_thermal([main, guess], more) == [main, more, guess]
    assert with_thermal([main], less) == [main, less]
    assert with_thermal([main, guess], None) == [main, guess]
    assert with_thermal([], more) == [more]


# Akku ins Auto


def test_sentences_from_the_battery_literally() -> None:
    reason = Reason(code="AUTO_AUS_AKKU", params={"name": "Tesla", "kw": 2.5})
    assert sentence(reason) == "Tesla lädt 2,5 kW aus dem Akku statt aus dem Netz."
    reason = Reason(code="SOFORT_AUS_AKKU", params={"soc": 62})
    assert (
        sentence(reason) == "Autos in Sofort dürfen aus dem Akku laden, solange er über 62 % liegt."
    )


def _battery_codes(
    ev_w: list[dict[str, float]],
    ev_battery_w: list[dict[str, float]],
    *,
    pv: float = 0.0,
) -> list[list[Reason]]:
    count = len(ev_w)
    inputs = scenario(
        prices=[20.0] * count,
        pv=[pv] * count,
        load=[500.0] * count,
        vehicles=[planned_car(departure_at=_slot(count), need_kwh=1.0)],
    )
    intents = [EV] * count
    return reasons_for(
        inputs,
        intents,
        resimulate(inputs, intents),
        [None] * count,
        fallback=False,
        ev_w=ev_w,
        ev_battery_w=ev_battery_w,
    )


def test_car_from_battery_only_is_not_ziel_soc_abfahrt() -> None:
    result = _battery_codes([{"tesla": 3000.0}], [{"tesla": 3000.0}])
    assert [r.code for r in result[0]] == ["EIGENVERBRAUCH", "AUTO_AUS_AKKU"]
    assert result[0][1].params == {"name": "Tesla Model 3", "kw": 3.0}


def test_grid_share_keeps_ziel_soc_abfahrt_before_auto_aus_akku() -> None:
    # 7 kW: 2,5 kW aus dem Akku, 1 kW aus PV (1,5 kW PV, 0,5 kW Haus), 3,5 kW aus dem Netz
    result = _battery_codes([{"tesla": 7000.0}], [{"tesla": 2500.0}], pv=1500.0)
    assert [r.code for r in result[0]][1:] == ["ZIEL_SOC_ABFAHRT", "AUTO_AUS_AKKU"]
    assert result[0][1].params["kw"] == 7.0
    assert result[0][2].params["kw"] == 2.5


def test_small_battery_share_is_not_mentioned() -> None:
    result = _battery_codes([{"tesla": 7000.0}], [{"tesla": 50.0}])
    assert [r.code for r in result[0]] == ["EIGENVERBRAUCH", "ZIEL_SOC_ABFAHRT"]


def _now_codes(now_battery: list[bool]) -> list[list[Reason]]:
    count = len(now_battery)
    inputs = scenario(prices=[20.0] * count, pv=[0.0] * count, load=[500.0] * count)
    intents = [EV] * count
    thresholds = [(62.0, SCENARIO_START_AT)] * count
    return reasons_for(
        inputs,
        intents,
        resimulate(inputs, intents),
        [None] * count,
        fallback=False,
        thresholds=thresholds,
        now_battery=now_battery,
    )


SCENARIO_START_AT = at(SCENARIO_START)


def test_sofort_aus_akku_only_at_the_start_of_a_run() -> None:
    result = _now_codes([True, True, False, True])
    assert ["SOFORT_AUS_AKKU" in [r.code for r in c] for c in result] == [True, False, False, True]
    assert result[0][-1] == Reason(code="SOFORT_AUS_AKKU", params={"soc": 62.0})


def test_build_plan_passes_the_battery_share_on_to_the_reasons() -> None:
    inputs = scenario(
        prices=[20.0] * 2,
        pv=[0.0] * 2,
        load=[500.0] * 2,
        vehicles=[planned_car(departure_at=_slot(2), need_kwh=1.0)],
        car_battery=True,
    )
    plan = build_plan(
        inputs,
        [EV] * 2,
        source="optimizer",
        solver_status="optimal",
        ev_w=[{"tesla": 3000.0}, {}],
        ev_battery_w=[{"tesla": 3000.0}, {}],
    )
    assert [r.code for r in plan.slots[0].reasons][:2] == ["EIGENVERBRAUCH", "AUTO_AUS_AKKU"]

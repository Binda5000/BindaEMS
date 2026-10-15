import json
from datetime import datetime, timedelta
from typing import Any

import pytest
from tests.helpers import T0

from bindaems.shared.control import CommandKind
from bindaems.shared.decisions import (
    Decision,
    DecisionResult,
    decision_from_json,
    decision_text,
    decision_to_json,
)
from bindaems.shared.timeutil import LOCAL_TZ


def decision(**changes: Any) -> Decision:
    """Eine Entscheidung zum Prüfen; ``changes`` ersetzt einzelne Felder.

    Auch die Tests des Spools und der API nutzen diesen Helfer.
    """
    fields: dict[str, Any] = {
        "id": "a1",
        "ts": T0,
        "actuator": "wallbox.evcs",
        "kind": CommandKind.CURRENT,
        "requested": 6.0,
        "applied": 6.0,
        "dry_run": False,
        "outcome": "accepted",
        "result": DecisionResult.OK,
        "codes": ("safe_state",),
        "text": "…",
        "inputs": {"soc_pct": 46.0, "grid_l1_a": None},
    }
    return Decision(**(fields | changes))


# JSON-Form


def test_json_round_trip() -> None:
    d = decision()
    assert decision_from_json(decision_to_json(d)) == d


def test_from_json_rejects_wrong_kind() -> None:
    with pytest.raises(ValueError, match="kind"):
        decision_from_json(decision_to_json(decision()) | {"kind": "brake"})


def test_json_form() -> None:
    assert decision_to_json(decision()) == {
        "id": "a1",
        "ts": "2026-10-08T10:00:00+00:00",
        "actuator": "wallbox.evcs",
        "kind": "current",
        "requested": 6.0,
        "applied": 6.0,
        "dry_run": False,
        "outcome": "accepted",
        "result": "ok",
        "codes": ["safe_state"],
        "text": "…",
        "inputs": {"soc_pct": 46.0, "grid_l1_a": None},
    }


def test_json_is_plain_json_text() -> None:
    data = decision_to_json(decision(text="Größe „ok“", codes=("a", "b")))
    assert json.loads(json.dumps(data, allow_nan=False)) == data


def test_json_ts_is_utc() -> None:
    vienna = datetime(2026, 10, 8, 12, 0, tzinfo=LOCAL_TZ)  # Sommerzeit: UTC+2
    d = decision(ts=vienna)
    assert decision_to_json(d)["ts"] == "2026-10-08T10:00:00+00:00"
    assert decision_from_json(decision_to_json(d)) == d


def test_json_keeps_microseconds() -> None:
    d = decision(ts=T0 + timedelta(microseconds=123456))
    assert decision_to_json(d)["ts"] == "2026-10-08T10:00:00.123456+00:00"
    assert decision_from_json(decision_to_json(d)).ts == d.ts


def test_json_rejects_a_naive_timestamp_when_writing() -> None:
    with pytest.raises(ValueError, match="Zeitzone"):
        decision_to_json(decision(ts=datetime(2026, 10, 8, 10, 0)))


@pytest.mark.parametrize(
    ("kind", "requested", "applied"),
    [
        (CommandKind.CURRENT, 16.0, 10.0),
        (CommandKind.CHARGING, True, True),
        (CommandKind.CHARGING, True, False),
        (CommandKind.GRID_SETPOINT, None, None),
        (CommandKind.GRID_SETPOINT, -250.5, 0.0),
        (CommandKind.MAX_DISCHARGE, -1.0, -1.0),
        (CommandKind.MIN_SOC, 20.5, 20.5),
        (CommandKind.CURRENT, 6.0, None),  # abgelehnt: nichts angewendet
    ],
)
def test_json_round_trip_of_every_value_type(
    kind: CommandKind, requested: object, applied: object
) -> None:
    d = decision(kind=kind, requested=requested, applied=applied)
    back = decision_from_json(decision_to_json(d))
    assert back == d
    assert type(back.requested) is type(requested)
    assert type(back.applied) is type(applied)


@pytest.mark.parametrize("result", list(DecisionResult))
def test_json_round_trip_of_every_result(result: DecisionResult) -> None:
    back = decision_from_json(decision_to_json(decision(result=result)))
    assert back.result is result


def test_a_deferral_is_written_as_deferred() -> None:
    # Zurückgestellt wegen eines Stundenlimits (Spec 7.10, Stand Plan 2c-1)
    deferred = decision(outcome="deferred", result=DecisionResult.DEFERRED)
    assert decision_to_json(deferred)["result"] == "deferred"


def test_json_round_trip_without_codes_and_inputs() -> None:
    d = decision(codes=(), inputs={})
    data = decision_to_json(d)
    assert (data["codes"], data["inputs"]) == ([], {})
    assert decision_from_json(data) == d


def test_from_json_makes_codes_a_tuple() -> None:
    back = decision_from_json(decision_to_json(decision(codes=("handback", "grid_outage"))))
    assert back.codes == ("handback", "grid_outage")
    assert isinstance(back.codes, tuple)


@pytest.mark.parametrize(
    "value",
    [
        float("nan"),
        float("inf"),
        float("-inf"),
        pytest.param(10**400, id="int-too-large"),
        pytest.param("10", id="string"),
        pytest.param([6.0], id="list"),
    ],
)
def test_json_writes_a_value_that_json_cannot_hold_as_null(value: object) -> None:
    data = decision_to_json(decision(requested=value, applied=value))
    json.dumps(data, allow_nan=False)  # NaN und ∞ wären kein gültiges JSON
    assert (data["requested"], data["applied"]) == (None, None)


def test_json_writes_unknown_inputs_as_null() -> None:
    d = decision(inputs={"soc_pct": float("nan"), "grid_l1_a": float("inf"), "battery_w": -120.5})
    data = decision_to_json(d)
    json.dumps(data, allow_nan=False)
    assert data["inputs"] == {"soc_pct": None, "grid_l1_a": None, "battery_w": -120.5}


def test_from_json_turns_integers_into_floats() -> None:
    data = decision_to_json(decision()) | {"requested": 6, "applied": 6, "inputs": {"soc_pct": 46}}
    back = decision_from_json(data)
    assert (type(back.requested), type(back.applied)) == (float, float)
    assert back.inputs == {"soc_pct": 46.0}
    assert type(back.inputs["soc_pct"]) is float


def test_from_json_converts_any_zone_to_utc() -> None:
    back = decision_from_json(decision_to_json(decision()) | {"ts": "2026-10-08T12:00:00+02:00"})
    assert back.ts == T0
    assert back.ts.utcoffset() == timedelta(0)


def test_from_json_accepts_z_as_utc() -> None:
    back = decision_from_json(decision_to_json(decision()) | {"ts": "2026-10-08T10:00:00Z"})
    assert back.ts == T0


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"id": 7}, "id"),
        ({"ts": "gestern"}, "ts"),
        ({"ts": "2026-10-08T10:00:00"}, "ts"),  # ohne Zeitzone
        ({"ts": "2026-10-08"}, "ts"),
        ({"ts": 1791453600}, "ts"),
        ({"ts": "0001-01-01T00:00:00+05:00"}, "ts"),  # läge vor dem Jahr 1 in UTC
        ({"actuator": None}, "actuator"),
        ({"kind": "brake"}, "kind"),
        ({"kind": 3}, "kind"),
        ({"kind": ["current"]}, "kind"),
        ({"requested": "6"}, "requested"),
        ({"requested": [6]}, "requested"),
        ({"requested": float("nan")}, "requested"),
        ({"applied": "6"}, "applied"),
        ({"applied": float("inf")}, "applied"),
        ({"dry_run": 1}, "dry_run"),
        ({"dry_run": None}, "dry_run"),
        ({"outcome": None}, "outcome"),
        ({"result": "maybe"}, "result"),
        ({"result": 1}, "result"),
        ({"codes": "safe_state"}, "codes"),
        ({"codes": ["safe_state", 1]}, "codes"),
        ({"codes": None}, "codes"),
        ({"text": None}, "text"),
        ({"inputs": [1.0]}, "inputs"),
        ({"inputs": None}, "inputs"),
        ({"inputs": {"soc_pct": "46"}}, "soc_pct"),
        ({"inputs": {"soc_pct": True}}, "soc_pct"),
        ({"inputs": {"soc_pct": float("nan")}}, "soc_pct"),
        ({"inputs": {1: 2.0}}, "inputs"),
    ],
)
def test_from_json_names_the_wrong_field_in_german(changes: dict[str, Any], field: str) -> None:
    with pytest.raises(ValueError, match=field) as raised:
        decision_from_json(decision_to_json(decision()) | changes)
    assert str(raised.value).startswith("Entscheidung ungültig")


def test_from_json_rejects_missing_and_foreign_fields() -> None:
    data = decision_to_json(decision())
    without_id = {key: value for key, value in data.items() if key != "id"}
    with pytest.raises(ValueError, match=r"fehlend.*id"):
        decision_from_json(without_id)
    with pytest.raises(ValueError, match=r"fremd.*extra"):
        decision_from_json(data | {"extra": 1})


@pytest.mark.parametrize("data", [None, [], "text", 7, [decision_to_json(decision())]])
def test_from_json_rejects_anything_but_an_object(data: object) -> None:
    with pytest.raises(ValueError, match="kein Objekt") as raised:
        decision_from_json(data)
    assert str(raised.value).startswith("Entscheidung ungültig")


def test_from_json_error_shows_a_long_value_shortened() -> None:
    with pytest.raises(ValueError, match="requested") as raised:
        decision_from_json(decision_to_json(decision()) | {"requested": "x" * 500})
    assert len(str(raised.value)) < 200


# Text


def test_text_for_a_confirmed_safe_current() -> None:
    text = decision_text(
        "EVCS „evcs“",
        CommandKind.CURRENT,
        6.0,
        DecisionResult.OK,
        reasons=("safe_state",),
        findings=("Dringend (Richtung sicherer Zustand): ohne Mindestabstand und Limits.",),
    )
    assert text == (
        "EVCS „evcs“ (sicherer Zustand): Strom 6 A – gesendet und bestätigt. "
        "Dringend (Richtung sicherer Zustand): ohne Mindestabstand und Limits."
    )


@pytest.mark.parametrize(
    ("kind", "value", "result", "detail", "expected"),
    [
        (
            CommandKind.CHARGING,
            False,
            DecisionResult.DRY_RUN,
            "",
            "V: Laden aus – Dry-Run, nicht gesendet.",
        ),
        (
            CommandKind.GRID_SETPOINT,
            None,
            DecisionResult.TIMEOUT,
            "",
            "V: Netz-Sollwert-Override aufheben – nicht bestätigt (Zeitüberschreitung).",
        ),
        (
            CommandKind.MAX_DISCHARGE,
            -1.0,
            DecisionResult.REJECTED,
            "",
            "V: Entladegrenze unbegrenzt – abgelehnt.",
        ),
        (
            CommandKind.MIN_SOC,
            20.5,
            DecisionResult.ERROR,
            "Verbindung weg",
            "V: Min-SOC 20,5 % – Fehler: Verbindung weg.",
        ),
        (
            CommandKind.CURRENT,
            16.0,
            DecisionResult.NOT_SENT,
            "nicht steuerbar: Cerbo nicht verbunden.",
            "V: Strom 16 A – nicht gesendet: nicht steuerbar: Cerbo nicht verbunden.",
        ),
    ],
)
def test_text_variants(
    kind: CommandKind, value: Any, result: DecisionResult, detail: str, expected: str
) -> None:
    assert decision_text("V", kind, value, result, detail=detail) == expected


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        (CommandKind.CURRENT, 6.0, "Strom 6 A"),
        (CommandKind.CURRENT, 10.5, "Strom 10,5 A"),
        (CommandKind.CHARGING, True, "Laden an"),
        (CommandKind.CHARGING, False, "Laden aus"),
        (CommandKind.GRID_SETPOINT, 0.0, "Netz-Sollwert 0 W"),
        (CommandKind.GRID_SETPOINT, 1500.0, "Netz-Sollwert 1500 W"),
        (CommandKind.GRID_SETPOINT, -300.0, "Netz-Sollwert −300 W"),  # echtes Minus
        (CommandKind.GRID_SETPOINT, None, "Netz-Sollwert-Override aufheben"),
        (CommandKind.MAX_DISCHARGE, 3000.0, "Entladegrenze 3000 W"),
        (CommandKind.MAX_DISCHARGE, 0.0, "Entladegrenze 0 W"),
        (CommandKind.MAX_DISCHARGE, -1.0, "Entladegrenze unbegrenzt"),
        (CommandKind.MAX_DISCHARGE, -1, "Entladegrenze unbegrenzt"),
        (CommandKind.MIN_SOC, 20.5, "Min-SOC 20,5 %"),
        (CommandKind.MIN_SOC, 30, "Min-SOC 30 %"),
    ],
)
def test_command_text_per_kind(kind: CommandKind, value: Any, expected: str) -> None:
    text = decision_text("V", kind, value, DecisionResult.OK)
    assert text == f"V: {expected} – gesendet und bestätigt."


@pytest.mark.parametrize(
    ("result", "detail", "expected"),
    [
        (DecisionResult.OK, "", "gesendet und bestätigt."),
        (DecisionResult.OK, "ohne Belang", "gesendet und bestätigt."),
        (DecisionResult.TIMEOUT, "ohne Belang", "nicht bestätigt (Zeitüberschreitung)."),
        (DecisionResult.DRY_RUN, "ohne Belang", "Dry-Run, nicht gesendet."),
        (DecisionResult.REJECTED, "ohne Belang", "abgelehnt."),
        (DecisionResult.DEFERRED, "ohne Belang", "zurückgestellt."),
        (DecisionResult.ERROR, "Verbindung weg", "Fehler: Verbindung weg."),
        (DecisionResult.ERROR, "Verbindung weg.", "Fehler: Verbindung weg."),
        (DecisionResult.ERROR, "", "Fehler."),
        (
            DecisionResult.NOT_SENT,
            "Cerbo nicht verbunden.",
            "nicht gesendet: Cerbo nicht verbunden.",
        ),
        (DecisionResult.NOT_SENT, "", "nicht gesendet."),
    ],
)
def test_result_text(result: DecisionResult, detail: str, expected: str) -> None:
    text = decision_text("V", CommandKind.CURRENT, 8.0, result, detail=detail)
    assert text == f"V: Strom 8 A – {expected}"


@pytest.mark.parametrize(
    ("reasons", "expected"),
    [
        ((), "V"),
        (("safe_state",), "V (sicherer Zustand)"),
        (("handback",), "V (Übergabe)"),
        (("grid_outage",), "V (Netzausfall)"),
        (("grid_outage", "safe_state"), "V (Netzausfall, sicherer Zustand)"),
        (("surplus",), "V (surplus)"),  # unbekannte Codes bleiben unverändert
        (("handback", "surplus"), "V (Übergabe, surplus)"),
    ],
)
def test_reasons_are_named_in_german(reasons: tuple[str, ...], expected: str) -> None:
    text = decision_text("V", CommandKind.CURRENT, 8.0, DecisionResult.OK, reasons=reasons)
    assert text == f"{expected}: Strom 8 A – gesendet und bestätigt."


def test_charging_reasons_have_texts() -> None:
    text = decision_text(
        "EVCS „evcs“",
        CommandKind.CURRENT,
        8.0,
        DecisionResult.DRY_RUN,
        reasons=("mode_pv", "pv_surplus"),
    )
    assert text == "EVCS „evcs“ (PV-Überschuss, Überschuss): Strom 8 A – Dry-Run, nicht gesendet."


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("mode_pv", "PV-Überschuss"),
        ("mode_min_pv", "Min+PV"),
        ("mode_now", "Sofort"),
        ("mode_off", "Aus"),
        ("boost", "Boost"),
        ("pv_surplus", "Überschuss"),
        ("phase_limit", "Phasengrenze"),
        ("import_limit", "Importlimit"),
        ("reserve_guard", "Schutz der USV-Reserve"),
        ("overload", "Überlast"),
        ("wake", "Wecken"),
        ("not_taken_retry", "erneut nach „nicht übernommen“"),
        ("assignment_unclear", "Zuordnung unklar"),
        ("resume_after_outage", "Wiederaufnahme nach Ausfall"),
    ],
)
def test_every_charging_reason_is_named_in_german(code: str, expected: str) -> None:
    text = decision_text("V", CommandKind.CURRENT, 8.0, DecisionResult.OK, reasons=(code,))
    assert text == f"V ({expected}): Strom 8 A – gesendet und bestätigt."


def test_battery_reasons_have_texts() -> None:  # Plan 2c-2a, „Texte“
    text = decision_text(
        "Victron (ESS)",
        CommandKind.GRID_SETPOINT,
        2340.0,
        DecisionResult.DRY_RUN,
        reasons=("battery_comp",),
    )
    assert text == (
        "Victron (ESS) (Akku nicht ins Auto): Netz-Sollwert 2340 W – Dry-Run, nicht gesendet."
    )


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("battery_comp", "Akku nicht ins Auto"),
        ("battery_support", "Akku-Unterstützung"),
        ("peak_guard", "15-min-Wächter"),
    ],
)
def test_every_battery_reason_is_named_in_german(code: str, expected: str) -> None:
    text = decision_text("V", CommandKind.GRID_SETPOINT, 2000.0, DecisionResult.OK, reasons=(code,))
    assert text == f"V ({expected}): Netz-Sollwert 2000 W – gesendet und bestätigt."


def test_assignment_detected_has_its_text() -> None:  # Plan 2c-2b, „Texte“
    shown = decision_text(
        "Tesla Model 3",
        CommandKind.CURRENT,
        16.0,
        DecisionResult.OK,
        reasons=("mode_now", "assignment_detected"),
    )
    assert shown == (
        "Tesla Model 3 (Sofort, Zuordnung erkannt): Strom 16 A – gesendet und bestätigt."
    )


def test_findings_follow_the_sentence_separated_by_spaces() -> None:
    text = decision_text(
        "V",
        CommandKind.CURRENT,
        8.0,
        DecisionResult.REJECTED,
        findings=["Erstens.", "Zweitens ohne Punkt", "Drittens."],
    )
    assert text == "V: Strom 8 A – abgelehnt. Erstens. Zweitens ohne Punkt Drittens."


def test_empty_findings_add_nothing() -> None:
    text = decision_text("V", CommandKind.CURRENT, 8.0, DecisionResult.OK, findings=("", ""))
    assert text == "V: Strom 8 A – gesendet und bestätigt."


def test_detail_with_a_period_gets_no_second_one() -> None:
    text = decision_text("V", CommandKind.CURRENT, 8.0, DecisionResult.ERROR, detail="Weg.")
    assert text == "V: Strom 8 A – Fehler: Weg."
    assert ".." not in text


@pytest.mark.parametrize(
    ("kind", "value", "expected"),
    [
        (CommandKind.CURRENT, "10", "Strom '10' A"),
        (CommandKind.CURRENT, float("nan"), "Strom nan A"),
        (CommandKind.CURRENT, None, "Strom None A"),
        (CommandKind.CHARGING, 6.0, "Laden 6"),
        (CommandKind.CHARGING, None, "Laden None"),
        (CommandKind.GRID_SETPOINT, True, "Netz-Sollwert True W"),
        (CommandKind.MAX_DISCHARGE, float("-inf"), "Entladegrenze -inf W"),
        (CommandKind.MIN_SOC, [1], "Min-SOC [1] %"),
    ],
)
def test_text_shows_a_value_that_does_not_fit_the_kind_instead_of_failing(
    kind: CommandKind, value: Any, expected: str
) -> None:
    # Eine abgelehnte Anfrage mit ungültigem Wert wird protokolliert wie jede andere.
    text = decision_text("V", kind, value, DecisionResult.REJECTED)
    assert text == f"V: {expected} – abgelehnt."


def test_text_names_an_unknown_kind_instead_of_failing() -> None:
    kind: Any = "brake"
    text = decision_text("V", kind, 5.0, DecisionResult.REJECTED)
    assert text == "V: brake 5 – abgelehnt."


@pytest.mark.parametrize(
    "value",
    ["x" * 200, 10**400, list(range(100))],
    ids=["string", "int-too-large", "list"],
)
def test_text_shortens_a_long_invalid_value(value: Any) -> None:
    # wie der Befund ``value.invalid``: die ersten 59 Zeichen der Darstellung und „…“
    shown = repr(value)[:59] + "…"
    text = decision_text("V", CommandKind.MIN_SOC, value, DecisionResult.REJECTED)
    assert text == f"V: Min-SOC {shown} % – abgelehnt."

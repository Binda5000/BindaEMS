"""Tests der Auslöser der Neuplanung: Fingerabdrücke, Viertelstunde, SOC, Monatsspitze und
Einstellungen (Plan 3b, Task 10)."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from tests.plan_helpers import at, planned_car, planned_health, sample_plan, vehicle_health

from bindaems.app.planning.triggers import (
    Fingerprint,
    changed,
    expected_cars,
    expected_soc_pct,
    next_tick,
    peak_rose,
    settings_relevant,
    soc_deviates,
    vehicles_fingerprint,
)
from bindaems.shared.control import ChargeMode, Intent, VehicleIntent
from bindaems.shared.settings import FeedInSettings, PlanningSettings, RuntimeSettings


def test_slot_tick_one_minute_after_slot_start():
    assert next_tick(at("2026-10-16T10:01:30+00:00")) == at("2026-10-16T10:16:00+00:00")


def test_slot_tick_right_at_the_tick_is_the_next_one():
    assert next_tick(at("2026-10-16T10:16:00+00:00")) == at("2026-10-16T10:31:00+00:00")
    assert next_tick(at("2026-10-16T10:15:00+00:00")) == at("2026-10-16T10:31:00+00:00")


def test_changed_fingerprints():
    old = Fingerprint(at("2026-10-16T21:45:00+00:00"), 48, at("2026-10-16T10:07:00+00:00"), None)
    new = replace(
        old,
        last_price=at("2026-10-17T21:45:00+00:00"),
        load_issued=at("2026-10-16T10:09:00+00:00"),
    )
    assert changed(old, new) == ["prices", "load_forecast"] and changed(None, new) == []


def test_changed_all_in_order_and_nothing():
    old = Fingerprint(None, 0, None, None)
    new = Fingerprint(
        at("2026-10-16T21:45:00+00:00"),
        4,
        at("2026-10-16T10:07:00+00:00"),
        at("2026-10-16T10:09:00+00:00"),
    )
    assert changed(old, new) == ["prices", "pv_forecast", "load_forecast"]
    assert changed(new, new) == []


def test_more_primary_prices_are_new_prices_a_sliding_window_is_not():
    old = Fingerprint(at("2026-10-16T21:45:00+00:00"), 40, None, None)
    assert changed(old, replace(old, primary=44)) == ["prices"]  # Ersatzpreise ersetzt
    assert changed(old, replace(old, primary=39)) == []  # ein Slot fiel aus dem Fenster


def test_soc_deviation_interpolates_in_slot():
    plan = sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00"], soc=(50.0, 60.0))
    assert expected_soc_pct(plan, at("2026-10-16T10:07:30+00:00")) == pytest.approx(55.0)
    assert not soc_deviates(plan, at("2026-10-16T10:07:30+00:00"), 59.9)
    assert soc_deviates(plan, at("2026-10-16T10:07:30+00:00"), 60.1)
    assert not soc_deviates(None, at("2026-10-16T10:07:30+00:00"), 10.0)


def test_expected_soc_outside_the_plan_is_unknown():
    plan = sample_plan(
        created="2026-10-16T10:01:00+00:00", slots=["10:00", "10:15"], soc=(50.0, 60.0)
    )
    assert expected_soc_pct(plan, at("2026-10-16T10:15:00+00:00")) == pytest.approx(50.0)
    assert expected_soc_pct(plan, at("2026-10-16T10:00:30+00:00")) is None  # vor dem Plan
    assert expected_soc_pct(plan, at("2026-10-16T10:46:00+00:00")) is None  # abgelaufen
    short = sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00"])
    assert expected_soc_pct(short, at("2026-10-16T10:20:00+00:00")) is None  # ohne Slot
    assert not soc_deviates(plan, at("2026-10-16T10:46:00+00:00"), 0.0)
    assert not soc_deviates(plan, at("2026-10-16T10:07:30+00:00"), None)


def test_peak_rose_and_settings_relevant():
    plan = sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00"], month_peak_kw=4.0)
    assert not peak_rose(plan, 4.05) and peak_rose(plan, 4.06)
    base = RuntimeSettings()
    assert settings_relevant(
        base, base.model_copy(update={"planning": PlanningSettings(margin_ct=3.0)})
    )
    assert not settings_relevant(
        base, base.model_copy(update={"wallbox_names": {"evcs": "Garage"}})
    )


def test_peak_rose_without_plan_peak_or_values():
    plan = sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00"])
    assert peak_rose(plan, 0.06) and not peak_rose(plan, 0.05)  # ohne Spitze im Plan: ab 0
    assert not peak_rose(plan, None) and not peak_rose(None, 9.0)


def test_settings_relevant_for_tariff_and_feed_in():
    base = RuntimeSettings()
    feed_in = FeedInSettings(monthly_ct={"2026-10": 7.5})
    assert settings_relevant(base, base.model_copy(update={"feed_in": feed_in}))
    tariff = base.tariff.model_copy(update={"vat_pct": 10.0})
    assert settings_relevant(base, base.model_copy(update={"tariff": tariff}))
    assert not settings_relevant(base, base)


def test_exact_thresholds_are_no_change_despite_float_noise():
    plan = sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00"], month_peak_kw=4.1)
    assert 4.15 - 4.1 > 0.05  # Rundungsrauschen
    assert not peak_rose(plan, 4.15) and peak_rose(plan, 4.16)
    soc = sample_plan(created="2026-10-16T10:01:00+00:00", slots=["10:00"], soc=(3.3, 3.3))
    assert 8.3 - 3.3 > 5.0  # Rundungsrauschen
    moment = at("2026-10-16T10:07:30+00:00")
    assert not soc_deviates(soc, moment, 8.3) and soc_deviates(soc, moment, 8.4)


# --- Geplante Autos (Plan 4b, Entscheidung 10) ---------------------------------------------

PLANNED_INTENT = Intent(version=3, vehicles={"tesla": VehicleIntent(mode=ChargeMode.PLANNED)})


def fingerprint_of(*entries: dict) -> tuple:
    """Fingerabdruck der geplanten Autos aus Health mit diesen Einträgen (Absicht Version 3)."""
    return vehicles_fingerprint(planned_health(*entries, version=3), PLANNED_INTENT)


def test_plug_in_of_a_planned_car_triggers():
    old = Fingerprint(None, 0, None, None, fingerprint_of(vehicle_health("tesla", plugged=False)))
    new = replace(old, vehicles=fingerprint_of(vehicle_health("tesla", plugged=True)))
    assert changed(old, new) == ["vehicles"]
    assert changed(new, old) == ["vehicles"]  # Ausstecken
    assert changed(new, new) == []


def test_target_change_triggers_but_soc_drift_does_not():
    base = fingerprint_of(vehicle_health("tesla"))
    old = Fingerprint(None, 0, None, None, base)
    # Ladestand, Quelle gemessen/geschätzt und Laden an/aus ändern sich laufend: kein Auslöser
    drift = fingerprint_of(
        vehicle_health("tesla", soc_pct=47.5, soc_source="estimated", charging=True)
    )
    assert changed(old, replace(old, vehicles=drift)) == []
    for change in (
        {"target": 90.0},
        {"departure_at": "2026-10-18T05:00:00+00:00"},
        {"wallbox": "evcs"},
        {"charge_limit_pct": 70.0},
        {"soc_pct": None, "soc_source": "assumed"},  # Ladestand angenommen
    ):
        moved = replace(old, vehicles=fingerprint_of(vehicle_health("tesla", **change)))
        assert changed(old, moved) == ["vehicles"], change


def test_car_entering_or_leaving_planned_mode_triggers():
    planned = fingerprint_of(vehicle_health("tesla"))
    other = fingerprint_of(vehicle_health("tesla", mode="pv"))
    old = Fingerprint(None, 0, None, None, other)
    assert other == () and planned != ()
    assert changed(old, replace(old, vehicles=planned)) == ["vehicles"]
    assert changed(replace(old, vehicles=planned), old) == ["vehicles"]


def test_vehicles_trigger_comes_after_the_forecasts():
    old = Fingerprint(None, 0, None, None)
    new = Fingerprint(
        at("2026-10-16T21:45:00+00:00"),
        4,
        at("2026-10-16T10:07:00+00:00"),
        at("2026-10-16T10:09:00+00:00"),
        fingerprint_of(vehicle_health("tesla")),
    )
    assert changed(old, new) == ["prices", "pv_forecast", "load_forecast", "vehicles"]


def test_vehicles_fingerprint_is_empty_without_trusted_health():
    health = planned_health(vehicle_health("tesla"), version=3)
    assert vehicles_fingerprint(health, PLANNED_INTENT) != ()
    assert vehicles_fingerprint(None, PLANNED_INTENT) == ()
    assert vehicles_fingerprint(health, None) == ()
    assert vehicles_fingerprint(health, PLANNED_INTENT.model_copy(update={"version": 4})) == ()


def test_vehicles_fingerprint_ignores_unusable_entries():
    health = planned_health(vehicle_health("tesla"), version=3)
    health["vehicles"].append({"mode": "planned", "plugged": True})  # ohne Schlüssel
    health["vehicles"].append("kaputt")
    assert vehicles_fingerprint(health, PLANNED_INTENT) == fingerprint_of(vehicle_health("tesla"))


# --- Erwartete Autos (Plan 5b, Präzisierung 10, Ruling T10-4) --------------------------------

ARRIVAL = at("2026-10-16T15:30:00+00:00")


def test_changed_expected_set_triggers_replan():
    # Die Menge der erwarteten Autos mit Ankunft, Abfahrt und Ziel; angesteckte zählen nicht
    car = planned_car(expected=True, arrival_at=ARRIVAL, soc_pct=35.0)
    plugged = planned_car(key="egolf", name="e-Golf", wallbox="evcs")
    base = expected_cars([plugged, car])
    assert base == ((car.key, ARRIVAL, car.departure_at, 80.0),)
    assert expected_cars([plugged]) == () and expected_cars([]) == ()
    # Ladestand und Bedarf allein ändern nichts: Sie folgen aus Profil und Ziel
    assert expected_cars([plugged, car.model_copy(update={"soc_pct": 40.0, "need_kwh": 1.0})]) == (
        base
    )
    for change in (
        {"arrival_at": ARRIVAL + timedelta(minutes=15)},
        {"departure_at": at("2026-10-18T05:00:00+00:00")},
        {"target_soc_pct": 90.0},
    ):
        assert expected_cars([plugged, car.model_copy(update=change)]) != base, change
    # zwei erwartete Autos: sortiert nach Schlüssel, unabhängig von der Priorität
    golf = planned_car(key="egolf", name="e-Golf", expected=True, arrival_at=ARRIVAL)
    assert expected_cars([car, golf]) == expected_cars([golf, car])
    assert [entry[0] for entry in expected_cars([car, golf])] == ["egolf", "tesla"]

"""Tests der Auslöser der Neuplanung: Fingerabdrücke, Viertelstunde, SOC, Monatsspitze und
Einstellungen (Plan 3b, Task 10)."""

from __future__ import annotations

from dataclasses import replace

import pytest
from tests.plan_helpers import at, sample_plan

from bindaems.app.planning.triggers import (
    Fingerprint,
    changed,
    expected_soc_pct,
    next_tick,
    peak_rose,
    settings_relevant,
    soc_deviates,
)
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

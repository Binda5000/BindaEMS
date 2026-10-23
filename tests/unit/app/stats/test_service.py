from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import insert
from tests.app_helpers import ingest, set_tariff
from tests.unit.app.stats.stats_helpers import T0, TODAY, add_consumer, add_session

from bindaems.app.consumers.days import NO_DAY
from bindaems.app.db.schema import charging_session, consumer_day, ledger_check, planned_charge
from bindaems.app.stats.service import (
    NOT_YET,
    TodayStats,
    fees_eur,
    period_range,
)
from bindaems.app.tariff.calc import monthly_fees_eur
from bindaems.shared.domain import SlotFlows
from bindaems.shared.influx.lineprotocol import epoch_ms
from bindaems.shared.settings import PowerComponent, TariffSettings
from bindaems.shared.timeutil import SLOT

PRICE_CT = 13.44  # 10 ct Börsenpreis netto, Standardtarif, brutto


def with_fee(tariff: TariffSettings, monthly_eur: float) -> TariffSettings:
    components = [
        c.model_copy(update={"value_eur": monthly_eur, "vat": False})
        if c.id == "grid_base_fee"
        else c
        for c in tariff.components
    ]
    return tariff.model_copy(update={"components": components})


def with_power(
    tariff: TariffSettings, eur_kw_year: float | None, valid_from: date | None = None
) -> TariffSettings:
    components = [
        c.model_copy(update={"value_eur": eur_kw_year, "valid_from": valid_from})
        if isinstance(c, PowerComponent)
        else c
        for c in tariff.components
    ]
    return tariff.model_copy(update={"components": components})


def test_period_ranges() -> None:
    friday = date(2026, 10, 9)
    assert period_range("day", friday) == (friday, friday)
    assert period_range("month", friday) == (date(2026, 10, 1), date(2026, 10, 31))
    assert period_range("year", friday) == (date(2026, 1, 1), date(2026, 12, 31))
    assert period_range("month", date(2028, 2, 10)) == (date(2028, 2, 1), date(2028, 2, 29))


def test_week_is_monday_to_sunday() -> None:
    assert period_range("week", date(2026, 10, 9)) == (date(2026, 10, 5), date(2026, 10, 11))
    assert period_range("week", date(2026, 10, 5)) == (date(2026, 10, 5), date(2026, 10, 11))
    assert period_range("week", date(2026, 10, 11)) == (date(2026, 10, 5), date(2026, 10, 11))
    # die Woche reicht über den Jahreswechsel (ISO: KW 53 von 2026)
    assert period_range("week", date(2027, 1, 1)) == (date(2026, 12, 28), date(2027, 1, 3))


def test_fees_are_prorated_per_day(settings_service) -> None:
    tariff = with_fee(TariffSettings(), 9.0)
    assert fees_eur(date(2026, 10, 1), date(2026, 10, 10), tariff) == pytest.approx(9 * 10 / 31)
    assert fees_eur(date(2026, 10, 1), date(2026, 10, 31), tariff) == pytest.approx(9.0)
    # über die Monatsgrenze: je Monat die eigene Zahl der Tage
    assert fees_eur(date(2026, 9, 29), date(2026, 10, 2), tariff) == pytest.approx(
        9 * 2 / 30 + 9 * 2 / 31
    )
    assert fees_eur(date(2026, 10, 25), date(2026, 10, 25), tariff) == pytest.approx(9 / 31)
    assert fees_eur(date(2026, 10, 10), date(2026, 10, 9), tariff) == 0.0
    assert monthly_fees_eur(date(2026, 10, 9), tariff) == 9.0


def test_fees_only_count_the_days_up_to_today(stats, settings_service) -> None:
    set_tariff(settings_service, with_fee(TariffSettings(), 9.0))
    assert stats.period("month", TODAY)["money"]["fees_eur"] == pytest.approx(9 * 9 / 31)
    assert stats.period("month", date(2026, 9, 15))["money"]["fees_eur"] == pytest.approx(9.0)
    assert stats.period("month", date(2026, 11, 15))["money"]["fees_eur"] == 0.0


def test_day_buckets_on_dst_days(stats) -> None:
    ingest(stats._ledger, "2026-10-25T01:30:00+00:00", {"pv>house": 800.0})  # 2. 02:30 (MEZ)
    ingest(stats._ledger, "2026-10-25T00:30:00+00:00", {"pv>house": 200.0})  # 1. 02:30 (MESZ)
    autumn = stats.period("day", date(2026, 10, 25))
    buckets = autumn["buckets"]
    assert len(buckets) == 25
    assert buckets[2]["start"] == "2026-10-25T02:00:00+02:00"
    assert buckets[3]["start"] == "2026-10-25T02:00:00+01:00"
    assert (buckets[2]["pv_kwh"], buckets[3]["pv_kwh"]) == (0.2, 0.8)
    assert sum(bucket["pv_kwh"] for bucket in buckets) == pytest.approx(autumn["energy"]["pv_kwh"])
    spring = stats.period("day", date(2026, 3, 29))["buckets"]
    assert len(spring) == 23
    assert spring[1]["start"] == "2026-03-29T01:00:00+01:00"
    assert spring[2]["start"] == "2026-03-29T03:00:00+02:00"  # 02:00 gibt es nicht
    assert len(stats.period("day", TODAY)["buckets"]) == 24


def test_day_buckets_carry_energy_and_net_cost(stats, price_all, feed_in_7ct) -> None:
    price_all(T0, T0 + SLOT * 8)
    ingest(stats._ledger, T0, {"grid>house": 1000.0, "pv>grid": 500.0})
    ingest(stats._ledger, T0 + SLOT, {"grid>house": 1000.0})
    ingest(stats._ledger, T0 + SLOT * 4, {"pv>house": 600.0})
    hours = stats.period("day", TODAY)["buckets"]
    assert [b["start"][:13] for b in hours[8:10]] == ["2026-10-09T08", "2026-10-09T09"]
    first = hours[8]
    assert (first["import_kwh"], first["export_kwh"], first["pv_kwh"]) == (2.0, 0.5, 0.5)
    assert first["consumption_kwh"] == 2.0
    assert first["net_eur"] == pytest.approx(2.0 * PRICE_CT / 100 - 0.5 * 0.07)
    assert hours[9]["pv_kwh"] == 0.6 and hours[9]["consumption_kwh"] == 0.6
    assert hours[10]["net_eur"] == 0.0 and hours[10]["import_kwh"] == 0.0


def test_week_and_month_have_day_buckets_and_year_month_buckets(stats) -> None:
    week = stats.period("week", TODAY)
    assert [b["start"] for b in week["buckets"]] == [f"2026-10-{n:02d}" for n in range(5, 12)]
    month = stats.period("month", TODAY)
    assert len(month["buckets"]) == 31 and month["buckets"][0]["start"] == "2026-10-01"
    year = stats.period("year", TODAY)
    assert [b["start"] for b in year["buckets"]] == [f"2026-{n:02d}" for n in range(1, 13)]


def test_year_has_month_buckets_and_peaks(stats, settings_service) -> None:
    set_tariff(settings_service, with_power(TariffSettings(), 60.0))
    ingest(stats._ledger, "2026-03-10T10:00:00+00:00", {"grid>house": 2000.0, "pv>house": 300.0})
    ingest(stats._ledger, "2026-10-05T10:00:00+00:00", {"grid>house": 1000.0})
    ingest(stats._ledger, "2026-10-06T10:00:00+00:00", {"grid>house": 500.0})
    year = stats.period("year", TODAY)
    assert year["first"] == "2026-01-01" and year["last"] == "2026-12-31"
    assert year["buckets"][2]["import_kwh"] == 2.0 and year["buckets"][2]["pv_kwh"] == 0.3
    assert year["buckets"][9]["import_kwh"] == 1.5
    assert year["buckets"][5]["import_kwh"] == 0.0
    assert year["month_peaks"] == [
        {
            "month": "2026-03",
            "kw": 8.0,
            "slot_start": "2026-03-10T10:00:00+00:00",
            "power_eur": pytest.approx(48.0),  # 60 €/kW/Jahr · 8 kW ÷ 12 · 1,2
        },
        {
            "month": "2026-10",
            "kw": 4.0,
            "slot_start": "2026-10-05T10:00:00+00:00",
            "power_eur": pytest.approx(24.0),
        },
    ]
    assert year["money"]["power_eur"] == pytest.approx(72.0)


def test_power_price_only_for_month_and_year(stats, settings_service) -> None:
    ingest(stats._ledger, "2026-10-05T10:00:00+00:00", {"grid>house": 1000.0})
    # Standardtarif: der Leistungspreis hat noch keinen Satz
    month = stats.period("month", TODAY)
    assert month["money"]["power_eur"] is None
    assert [peak["power_eur"] for peak in month["month_peaks"]] == [None]
    set_tariff(settings_service, with_power(TariffSettings(), 60.0))
    month = stats.period("month", TODAY)
    assert month["money"]["power_eur"] == pytest.approx(24.0)
    assert stats.period("year", TODAY)["money"]["power_eur"] == pytest.approx(24.0)
    for period in ("day", "week"):
        result = stats.period(period, date(2026, 10, 5))
        assert result["money"]["power_eur"] is None and result["month_peaks"] == []
    # gilt der Leistungspreis erst ab November, bleibt der Oktober ohne
    set_tariff(settings_service, with_power(TariffSettings(), 60.0, date(2026, 11, 1)))
    month = stats.period("month", TODAY)
    assert month["money"]["power_eur"] is None and month["month_peaks"][0]["kw"] == 4.0


def test_total_adds_fees_and_power_to_the_net_cost(
    stats, settings_service, price_all, feed_in_7ct
) -> None:
    set_tariff(settings_service, with_power(with_fee(TariffSettings(), 9.0), 60.0))
    price_all(T0, T0 + SLOT)
    ingest(stats._ledger, T0, {"grid>house": 2000.0, "pv>grid": 1000.0})
    money = stats.period("month", TODAY)["money"]
    net = 2.0 * PRICE_CT / 100 - 1.0 * 0.07
    assert money["import_eur"] == pytest.approx(2.0 * PRICE_CT / 100)
    assert money["revenue_eur"] == pytest.approx(0.07)
    assert money["net_eur"] == pytest.approx(net)
    assert money["fees_eur"] == pytest.approx(9 * 9 / 31)
    assert money["power_eur"] == pytest.approx(60 * 8 / 12 * 1.2)
    assert money["total_eur"] == pytest.approx(net + 9 * 9 / 31 + 48.0)
    # die Ersparnis bleibt ohne Gebühren und Leistungspreis
    assert money["savings_no_plant_eur"] == pytest.approx(2.0 * 0.30 - net)


def test_total_is_null_when_net_is_unknown(stats, settings_service) -> None:
    set_tariff(settings_service, with_fee(TariffSettings(), 9.0))
    ingest(stats._ledger, T0, {"grid>house": 2000.0})  # kein Bezugspreis gespeichert
    money = stats.period("day", TODAY)["money"]
    assert money["import_eur"] is None and money["net_eur"] is None
    assert money["total_eur"] is None and money["savings_no_plant_eur"] is None
    assert money["savings_same_import_eur"] is None
    assert money["fees_eur"] == pytest.approx(9 / 31)
    assert stats.period("day", TODAY)["buckets"][8]["net_eur"] is None


def test_one_unknown_day_makes_the_money_of_the_week_unknown(
    stats, settings_service, price_all, feed_in_7ct
) -> None:
    set_tariff(settings_service, with_fee(TariffSettings(), 9.0))
    monday = T0 - timedelta(days=4)  # 05.10., 08:00 Ortszeit
    price_all(monday, monday + SLOT)
    ingest(stats._ledger, monday, {"grid>house": 1000.0})  # bepreist
    ingest(stats._ledger, T0 - timedelta(days=3), {"grid>house": 2000.0})  # 06.10.: ohne Preis
    week = stats.period("week", TODAY)
    money = week["money"]
    assert week["energy"]["import_kwh"] == 3.0  # die Energie ist bekannt
    assert money["import_eur"] is None and money["net_eur"] is None
    assert money["total_eur"] is None
    assert money["savings_no_plant_eur"] is None and money["savings_same_import_eur"] is None
    assert money["fees_eur"] == pytest.approx(9 * 5 / 31)  # 05. bis 09.10.
    nets = {bucket["start"]: bucket["net_eur"] for bucket in week["buckets"]}
    assert nets["2026-10-05"] == pytest.approx(PRICE_CT / 100) and nets["2026-10-06"] is None


def test_unknown_feed_in_price_makes_revenue_and_savings_unknown(stats, price_all) -> None:
    price_all(T0, T0 + SLOT)
    ingest(stats._ledger, T0, {"grid>house": 1000.0, "pv>grid": 500.0})  # kein OeMAG-Wert
    money = stats.period("day", TODAY)["money"]
    assert money["import_eur"] == pytest.approx(PRICE_CT / 100)
    assert money["revenue_eur"] is None and money["net_eur"] is None and money["total_eur"] is None


def test_period_ratios_come_from_sums_not_from_means(stats, price_all) -> None:
    price_all(T0 - timedelta(days=1), T0 - timedelta(days=1) + SLOT)
    ingest(stats._ledger, T0 - timedelta(days=2), {"pv>house": 1000.0, "pv>grid": 1000.0})
    ingest(stats._ledger, T0 - timedelta(days=1), {"grid>house": 3000.0})
    result = stats.period("week", TODAY)
    assert result["energy"]["consumption_kwh"] == 4.0 and result["energy"]["import_kwh"] == 3.0
    assert result["ratios"]["autarky"] == pytest.approx(0.25)  # 1 − 3 ÷ 4, nicht (1 + 0) ÷ 2
    assert result["ratios"]["self_consumption"] == pytest.approx(0.5)  # (2 − 1) ÷ 2
    money = result["money"]
    assert money["import_eur"] == pytest.approx(3.0 * PRICE_CT / 100)
    # ohne Einspeisepreis ist der Erlös unbekannt, die Kosten bleiben es nicht
    assert money["revenue_eur"] is None


def test_savings_follow_the_day_formulas_over_the_period(stats, price_all, feed_in_7ct) -> None:
    price_all(T0, T0 + SLOT)
    ingest(stats._ledger, T0 - timedelta(days=1), {"pv>house": 1000.0})
    ingest(stats._ledger, T0, {"grid>house": 1000.0, "pv>grid": 500.0, "pv>wb:twc": 500.0})
    result = stats.period("week", TODAY)["money"]
    net = 1.0 * PRICE_CT / 100 - 0.5 * 0.07
    assert result["savings_no_plant_eur"] == pytest.approx(2.5 * 0.30 - net)
    assert result["savings_same_import_eur"] == pytest.approx(1.0 * 0.30 - 0.5 * 0.07 - net)


def test_empty_period_has_zero_energy_and_no_ratios(stats) -> None:
    result = stats.period("day", date(2026, 10, 12))  # Zukunft
    assert result["complete"] is False and result["coverage"] == 0.0
    assert result["estimated_slots"] == 0
    assert set(result["energy"].values()) == {0.0}
    assert result["ratios"] == {"autarky": None, "self_consumption": None}
    assert result["money"]["fees_eur"] == 0.0
    assert result["vehicles"] == [] and result["battery_to_vehicles_kwh"] == 0.0
    assert result["month_peaks"] == []
    assert result["checks"] == {"days": 0, "flagged_days": [], "totals": []}
    past = stats.period("day", date(2026, 10, 1))  # Vergangenheit ohne Daten
    assert past["complete"] is True and past["coverage"] == 0.0
    assert past["ratios"]["autarky"] is None


def test_coverage_complete_and_estimated_slots(stats) -> None:
    yesterday = T0 - timedelta(days=1)
    ingest(stats._ledger, yesterday, {"grid>house": 100.0}, covered_s=900.0)
    stats._ledger.ingest(SlotFlows(yesterday + SLOT, 0.0, {"pv>house": 100.0}, {}), "estimate")
    day = stats.period("day", yesterday.date())
    assert day["complete"] is True and day["estimated_slots"] == 1
    assert day["coverage"] == pytest.approx(1 / 96)
    week = stats.period("week", TODAY)
    assert week["complete"] is False  # der Zeitraum läuft noch
    expected = 4 * 96 + 40  # 5. bis 8. vollständig, heute bis 10:00 Ortszeit
    assert week["coverage"] == pytest.approx(1 / expected)
    assert week["estimated_slots"] == 1


def test_vehicles_in_config_order_then_guest_and_unknown(stats, engine, price_all, cfg) -> None:
    assert list(cfg.vehicles)[:2] == ["tesla", "egolf"]
    add_session(engine, T0, T0 + SLOT, "egolf", "evcs")
    add_session(engine, T0, T0 + SLOT, "tesla", "twc")
    add_session(engine, T0 + SLOT, T0 + SLOT * 2, "guest", "twc")
    add_session(engine, T0 + SLOT * 3, T0 + SLOT * 4, "old-car", "twc")
    ingest(stats._ledger, T0 + SLOT * 2, {"pv>wb:evcs": 200.0})  # ohne Session
    ingest(stats._ledger, T0 + SLOT * 3, {"pv>wb:twc": 700.0})
    ingest(
        stats._ledger,
        T0,
        {"pv>wb:twc": 1000.0, "battery>wb:twc": 500.0, "pv>wb:evcs": 300.0},
    )
    ingest(stats._ledger, T0 + SLOT, {"pv>wb:twc": 400.0, "battery>wb:twc": 100.0})
    result = stats.period("day", TODAY)
    keys = [vehicle["key"] for vehicle in result["vehicles"]]
    assert keys == ["tesla", "egolf", "old-car", "guest", "unknown"]
    tesla, egolf, old, guest, unknown = result["vehicles"]
    assert tesla["name"] == cfg.vehicles["tesla"].name and tesla["kwh"] == 1.5
    assert (tesla["pv_kwh"], tesla["battery_kwh"], tesla["grid_kwh"]) == (1.0, 0.5, 0.0)
    assert tesla["cost_eur"] == pytest.approx(
        0.01
    )  # 0,5 kWh Akku zu 0 ct Einstand + 2 ct Verschleiß
    assert tesla["sessions"] == 1 and egolf["kwh"] == 0.3
    assert old["name"] == "old-car" and old["kwh"] == 0.7
    assert (guest["name"], guest["kwh"], guest["battery_kwh"]) == ("Gast", 0.5, 0.1)
    assert (unknown["name"], unknown["kwh"], unknown["sessions"]) == ("Unbekannt", 0.2, 0)
    assert result["battery_to_vehicles_kwh"] == pytest.approx(0.6)
    assert result["energy"]["wallbox_kwh"] == pytest.approx(3.2)


def test_vehicles_without_energy_are_dropped(stats, engine) -> None:
    add_session(engine, T0, T0 + SLOT, "tesla", "twc")  # steckt, lädt nicht
    result = stats.period("day", TODAY)
    assert result["vehicles"] == [] and result["battery_to_vehicles_kwh"] == 0.0


def test_vehicle_cost_is_unknown_without_a_grid_price(stats, engine) -> None:
    add_session(engine, T0, T0 + SLOT, "tesla", "twc")
    ingest(stats._ledger, T0, {"grid>wb:twc": 1000.0})
    [tesla] = stats.period("day", TODAY)["vehicles"]
    assert tesla["kwh"] == 1.0 and tesla["cost_eur"] is None


def test_vehicle_cost_is_unknown_while_the_battery_holds_unpriced_grid_energy(
    stats, engine
) -> None:
    add_session(engine, T0 + SLOT, T0 + SLOT * 2, "tesla", "twc")
    ingest(stats._ledger, T0, {"grid>battery": 1000.0})  # ohne Bezugspreis
    ingest(stats._ledger, T0 + SLOT, {"battery>wb:twc": 500.0})
    [tesla] = stats.period("day", TODAY)["vehicles"]
    assert tesla["battery_kwh"] == 0.5 and tesla["cost_eur"] is None


def add_days(engine, rows: dict[tuple[str, int], float | None]) -> None:
    with engine.begin() as conn:
        for (day, consumer), kwh in rows.items():
            note = None if kwh is not None else "kein Verlauf seit Mitternacht"
            conn.execute(
                insert(consumer_day).values(day=day, consumer_id=consumer, kwh=kwh, note=note)
            )


async def test_consumers_use_stored_days_and_the_cache_for_today(
    stats, engine, consumers, energy, influx
) -> None:
    from bindaems.app.history.influx import Series

    a, b = add_consumer(consumers, "a"), add_consumer(consumers, "b")
    add_days(engine, {("2026-10-08", a): 2.0, ("2026-10-08", b): None})
    start = epoch_ms(datetime(2026, 10, 8, 22, 0, tzinfo=UTC))
    influx.series = [
        Series(
            "power",
            {"source": "load", "id": "a"},
            ["time", "w"],
            [[start + n * 60_000, 600.0] for n in range(120)],
        )
    ]
    await energy.refresh(consumers.list())  # heute: 600 W · 2 h = 1,2 kWh
    yesterday = stats.period("day", date(2026, 10, 8))["consumers"]
    assert yesterday == [
        {"id": a, "name": "a", "kwh": 2.0, "note": None},
        {"id": b, "name": "b", "kwh": None, "note": "kein Verlauf seit Mitternacht"},
    ]
    today = stats.period("day", TODAY)["consumers"]
    assert today[0]["kwh"] == pytest.approx(1.2) and today[1]["kwh"] is None
    both = stats.period("week", TODAY)["consumers"]  # 5. bis 7. nicht gespeichert
    assert both[0] == {"id": a, "name": "a", "kwh": None, "note": NO_DAY}
    add_days(engine, {(f"2026-10-0{n}", a): 1.0 for n in (5, 6, 7)})
    assert stats.period("week", TODAY)["consumers"][0]["kwh"] == pytest.approx(3 + 2 + 1.2)
    # ein Tag in der Zukunft zählt nicht, ein Zeitraum nur aus der Zukunft hat keine Werte
    assert stats.period("day", date(2026, 10, 12))["consumers"][0] == {
        "id": a,
        "name": "a",
        "kwh": None,
        "note": NOT_YET,
    }


def test_no_consumers_gives_an_empty_list(stats) -> None:
    assert stats.period("day", TODAY)["consumers"] == []


def add_check(engine, day: str, signal: str, ledger: float, counter, flagged=False, note=None):
    deviation = (ledger - counter) / counter * 100 if counter else None
    with engine.begin() as conn:
        conn.execute(
            insert(ledger_check).values(
                day=day,
                signal=signal,
                ledger_kwh=ledger,
                counter_kwh=counter,
                deviation_pct=deviation,
                flagged=flagged,
                note=note,
                checked_at=T0,
            )
        )


def test_checks_summary_counts_days_and_flagged_days(stats, engine) -> None:
    add_check(engine, "2026-10-07", "import", 10.0, 10.05)
    add_check(engine, "2026-10-07", "export", 1.0, None, note="Zählerstand fehlt")
    add_check(engine, "2026-10-08", "import", 12.0, 10.0, flagged=True)
    add_check(engine, "2026-09-30", "import", 5.0, 5.0)
    checks = stats.period("week", TODAY)["checks"]
    assert checks["days"] == 2 and checks["flagged_days"] == ["2026-10-08"]
    totals = {total["signal"]: total for total in checks["totals"]}
    assert totals["import"]["ledger_kwh"] == 22.0 and totals["import"]["counter_kwh"] == 20.05
    assert totals["import"]["deviation_pct"] == pytest.approx(1.95 / 20.05 * 100)
    assert totals["export"]["counter_kwh"] is None and totals["export"]["deviation_pct"] is None
    assert stats.period("month", TODAY)["checks"]["days"] == 2


def test_today_stats_for_home_assistant(stats, price_all, feed_in_7ct) -> None:
    price_all(T0, T0 + SLOT)
    ingest(stats._ledger, T0 - timedelta(days=3), {"pv>house": 2000.0})
    ingest(stats._ledger, T0, {"grid>house": 1000.0, "pv>house": 1000.0, "pv>grid": 500.0})
    result = stats.today()
    net = 1.0 * PRICE_CT / 100 - 0.5 * 0.07
    assert isinstance(result, TodayStats)
    assert result.savings_today_eur == pytest.approx(2.0 * 0.30 - net)
    assert result.savings_same_import_today_eur == pytest.approx(1.0 * 0.30 - 0.5 * 0.07 - net)
    assert result.savings_month_eur == pytest.approx(4.0 * 0.30 - net)
    assert result.savings_same_import_month_eur == pytest.approx(1.0 * 0.30 - 0.5 * 0.07 - net)
    assert result.autarky_today == pytest.approx(0.5)
    assert result.autarky_month == pytest.approx(1 - 1.0 / 4.0)


def test_today_stats_are_unknown_without_data_or_prices(stats) -> None:
    empty = stats.today()
    assert empty == TodayStats(0.0, 0.0, 0.0, 0.0, None, None)
    ingest(stats._ledger, T0, {"grid>house": 1000.0})  # ohne Bezugspreis
    result = stats.today()
    assert result.savings_today_eur is None and result.savings_month_eur is None
    assert result.autarky_today == 0.0


def test_vehicle_buckets_follow_the_buckets(stats) -> None:
    assert stats.period("day", TODAY)["vehicle_buckets"] == []
    for period in ("week", "month", "year"):
        result = stats.period(period, TODAY)
        assert [b["start"] for b in result["vehicle_buckets"]] == [
            b["start"] for b in result["buckets"]
        ]


def test_vehicle_buckets_per_day_in_config_order(stats, engine, price_all) -> None:
    yesterday = T0 - timedelta(days=1)
    add_session(engine, yesterday, yesterday + SLOT, "egolf", "evcs")
    add_session(engine, T0, T0 + SLOT, "guest", "evcs")
    add_session(engine, T0, T0 + SLOT, "tesla", "twc")
    ingest(stats._ledger, yesterday, {"pv>wb:evcs": 2000.0})
    ingest(stats._ledger, T0, {"pv>wb:twc": 1000.0, "grid>wb:evcs": 500.0})
    result = stats.period("week", TODAY)
    by_start = {b["start"]: b["vehicles"] for b in result["vehicle_buckets"]}
    assert [v["key"] for v in by_start["2026-10-08"]] == ["egolf"]
    assert [v["key"] for v in by_start["2026-10-09"]] == ["tesla", "guest"]
    assert by_start["2026-10-05"] == []
    tesla = by_start["2026-10-09"][0]
    assert (tesla["kwh"], tesla["pv_kwh"], tesla["cost_eur"]) == (1.0, 1.0, 0.0)
    assert set(tesla) == {"key", "kwh", "pv_kwh", "battery_kwh", "grid_kwh", "cost_eur"}


def test_vehicle_bucket_cost_unknown_only_where_the_price_is_missing(stats, engine) -> None:
    yesterday = T0 - timedelta(days=1)
    add_session(engine, yesterday, T0 + SLOT, "tesla", "twc")
    ingest(stats._ledger, yesterday, {"pv>wb:twc": 1000.0})
    ingest(stats._ledger, T0, {"grid>wb:twc": 1000.0})  # ohne Bezugspreis
    by_start = {b["start"]: b["vehicles"] for b in stats.period("week", TODAY)["vehicle_buckets"]}
    assert by_start["2026-10-08"][0]["cost_eur"] == 0.0
    assert by_start["2026-10-09"][0]["cost_eur"] is None
    [tesla] = stats.period("week", TODAY)["vehicles"]
    assert tesla["cost_eur"] is None


def test_year_vehicle_buckets_are_months(stats, engine) -> None:
    september = T0 - timedelta(days=20)
    add_session(engine, september, september + SLOT, "tesla", "twc")
    add_session(engine, T0, T0 + SLOT, "tesla", "twc")
    ingest(stats._ledger, september, {"pv>wb:twc": 1000.0})
    ingest(stats._ledger, T0, {"pv>wb:twc": 500.0})
    by_start = {b["start"]: b["vehicles"] for b in stats.period("year", TODAY)["vehicle_buckets"]}
    assert by_start["2026-09"][0]["kwh"] == 1.0 and by_start["2026-10"][0]["kwh"] == 0.5
    assert by_start["2026-01"] == []


def test_vehicle_totals_equal_the_sum_of_the_buckets(stats, engine, price_all) -> None:
    price_all(T0 - timedelta(days=3), T0 - timedelta(days=3) + SLOT)
    add_session(engine, T0 - timedelta(days=3), T0 - timedelta(days=3) + SLOT, "tesla", "twc")
    add_session(engine, T0, None, "tesla", "twc")  # offen
    ingest(stats._ledger, T0 - timedelta(days=3), {"grid>wb:twc": 800.0})
    ingest(stats._ledger, T0, {"pv>wb:twc": 600.0, "battery>wb:twc": 200.0})
    ingest(stats._ledger, T0 + SLOT, {"pv>wb:evcs": 100.0})  # ohne Session
    result = stats.period("month", TODAY)
    for vehicle in result["vehicles"]:
        parts = [
            v
            for b in result["vehicle_buckets"]
            for v in b["vehicles"]
            if v["key"] == vehicle["key"]
        ]
        assert sum(p["kwh"] for p in parts) == pytest.approx(vehicle["kwh"])
        assert sum(p["cost_eur"] for p in parts) == pytest.approx(vehicle["cost_eur"])


def test_year_view_attributes_the_sessions_only_once(stats, engine, monkeypatch) -> None:
    import bindaems.app.charging.sessions as sessions_module

    calls: list[int] = []
    original = sessions_module.attribute_days

    def counted(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append(len(args[0]))
        return original(*args, **kwargs)

    def forbidden(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("attribute darf nicht aufgerufen werden")

    monkeypatch.setattr(sessions_module, "attribute_days", counted)
    monkeypatch.setattr(sessions_module, "attribute", forbidden)
    add_session(engine, T0, T0 + SLOT, "tesla", "twc")
    add_session(engine, T0 + SLOT, T0 + SLOT * 2, "tesla", "twc")
    ingest(stats._ledger, T0, {"pv>wb:twc": 1000.0})
    ingest(stats._ledger, T0 + SLOT, {"pv>wb:twc": 500.0})
    [tesla] = stats.period("year", TODAY)["vehicles"]
    assert (tesla["kwh"], tesla["sessions"]) == (1.5, 2)
    assert len(calls) == 1  # eine Zuordnung je Tag und Auto; die Anzahl ohne Zuordnung


def test_planned_charges_of_the_period(stats, engine) -> None:
    # Abfahrten am 9. Oktober (Ortszeit) und eine am 1. Oktober; offene Zeilen zählen nicht
    with engine.begin() as conn:
        session_id = conn.execute(
            insert(charging_session)
            .returning(charging_session.c.id)
            .values(wallbox="twc", vehicle="tesla", start=T0, end=None, last_seen=T0)
        ).scalar_one()
        for departure, feasible, outcome in [
            (T0 + timedelta(hours=1), True, "reached"),
            (T0 + timedelta(hours=2), True, "missed"),
            (T0 + timedelta(hours=3), False, "missed"),
            (T0 + timedelta(hours=4), True, "early"),
            (T0 + timedelta(hours=5), True, "unknown"),
            (T0 + timedelta(hours=6), True, "open"),
            (T0 + timedelta(hours=7), True, "changed"),
            (datetime(2026, 10, 1, 5, 0, tzinfo=UTC), True, "reached"),
        ]:
            conn.execute(
                insert(planned_charge).values(
                    session_id=session_id,
                    vehicle="tesla",
                    target_soc_pct=80,
                    departure_at=departure,
                    started_at=T0,
                    start_soc_pct=40.0,
                    start_soc_source="measured",
                    feasible=feasible,
                    outcome=outcome,
                )
            )
    assert stats.period("day", TODAY)["planned"] == {
        "reached": 1,
        "feasible": 2,
        "missed": 1,
        "infeasible": 1,
        "early": 1,
        "unknown": 1,
        "changed": 1,
    }
    assert stats.period("month", TODAY)["planned"]["reached"] == 2
    assert stats.period("day", date(2026, 10, 12))["planned"] == {
        "reached": 0,
        "feasible": 0,
        "missed": 0,
        "infeasible": 0,
        "early": 0,
        "unknown": 0,
        "changed": 0,
    }

import json

from bindaems.core.adapters.victron_topics import InstanceResolver, SignalUpdate, parse_message
from bindaems.shared.config import VictronInstances
from bindaems.shared.domain import SignalKind

P = "c0619ab1234"
M, S = SignalKind.MEASUREMENT, SignalKind.STATE


def resolver() -> InstanceResolver:
    return InstanceResolver(
        VictronInstances(
            grid=30,
            vebus=276,
            battery=512,
            pv={31: "huawei"},
            acload={32: "obergeschoss"},
            evcharger={40: "evcs"},
        )
    )


def msg(path: str, value: object) -> tuple[str, bytes]:
    return f"N/{P}/{path}", json.dumps({"value": value}).encode()


def test_grid_phase_power() -> None:
    assert parse_message(*msg("grid/30/Ac/L2/Power", 812.5), P, resolver()) == [
        SignalUpdate("grid.l2.power_w", 812.5, M)
    ]


def test_grid_energy_counters() -> None:
    r = resolver()
    assert parse_message(*msg("grid/30/Ac/Energy/Forward", 1234.5), P, r)[0].signal == (
        "grid.energy_import_kwh"
    )
    assert parse_message(*msg("grid/30/Ac/Energy/Reverse", 99.0), P, r)[0].signal == (
        "grid.energy_export_kwh"
    )


def test_pvinverter_uses_configured_id() -> None:
    out = parse_message(*msg("pvinverter/31/Ac/Power", 4000), P, resolver())
    assert out[0].signal == "pv.huawei.power_w"


def test_unknown_pv_instance_gets_fallback_id() -> None:
    out = parse_message(*msg("pvinverter/33/Ac/Power", 1), P, resolver())
    assert out[0].signal == "pv.pvinverter33.power_w"


def test_other_grid_instance_ignored() -> None:
    assert parse_message(*msg("grid/99/Ac/Power", 1), P, resolver()) == []


def test_vebus_phases_is_state() -> None:
    assert parse_message(*msg("vebus/276/Ac/NumberOfPhases", 3), P, resolver()) == [
        SignalUpdate("vebus.phases", 3, S)
    ]


def test_vebus_ac_in_out() -> None:
    r = resolver()
    assert parse_message(*msg("vebus/276/Ac/ActiveIn/L1/P", 500), P, r)[0].signal == (
        "vebus.l1.ac_in_power_w"
    )
    assert parse_message(*msg("vebus/276/Ac/Out/L3/P", 300), P, r)[0].signal == (
        "vebus.l3.ac_out_power_w"
    )


def test_settings_alias_and_generic() -> None:
    out = parse_message(*msg("settings/0/Settings/CGwacs/Hub4Mode", 1), P, resolver())
    assert SignalUpdate("ess.hub4_mode", 1, S) in out
    assert SignalUpdate("settings.cgwacs.hub4mode", 1, S) in out


def test_schedule_and_dess() -> None:
    r = resolver()
    schedule = parse_message(
        *msg("settings/0/Settings/CGwacs/BatteryLife/Schedule/Charge/2/Day", -7), P, r
    )
    assert SignalUpdate("ess.schedule.2.day", -7, S) in schedule
    dess = parse_message(*msg("settings/0/Settings/DynamicEss/Mode", 0), P, r)
    assert SignalUpdate("dess.mode", 0, S) in dess


def test_hub4_override() -> None:
    assert parse_message(*msg("hub4/0/Overrides/Setpoint", 150), P, resolver()) == [
        SignalUpdate("ess.override.setpoint", 150, S)
    ]


def test_evcharger() -> None:
    r = resolver()
    assert parse_message(*msg("evcharger/40/Ac/Power", 7360), P, r) == [
        SignalUpdate("wallbox.evcs.power_w", 7360, M)
    ]
    assert parse_message(*msg("evcharger/40/Status", 2), P, r) == [
        SignalUpdate("wallbox.evcs.status", 2, S)
    ]


def test_null_value_yields_invalid_update() -> None:
    assert parse_message(*msg("grid/30/Ac/L1/Power", None), P, resolver()) == [
        SignalUpdate("grid.l1.power_w", None, M)
    ]


def test_empty_payload_marks_value_removed() -> None:
    # dbus-flashmq leert die Topics eines verschwundenen Dienstes
    assert parse_message(f"N/{P}/grid/30/Ac/L1/Power", b"", P, resolver()) == [
        SignalUpdate("grid.l1.power_w", None, M)
    ]


def test_broken_json_ignored() -> None:
    assert parse_message(f"N/{P}/grid/30/Ac/L1/Power", b"{kaputt", P, resolver()) == []


def test_other_portal_and_non_n_topics_ignored() -> None:
    r = resolver()
    assert parse_message("N/other/grid/30/Ac/Power", b'{"value": 1}', P, r) == []
    assert parse_message(f"R/{P}/keepalive", b"", P, r) == []

from datetime import timedelta
from types import MappingProxyType

from tests.helpers import T0, snap

from bindaems.core.checks.selfcheck import CheckResult
from bindaems.shared.domain import Quality, Reading, SignalKind, Snapshot
from bindaems.tools.verify_checks import (
    Finding,
    check_battery,
    check_clock_offsets,
    check_evcs_dump,
    check_grid_meter,
    check_ha,
    check_hub4_overrides,
    check_influx,
    check_mqtt_inventory,
    check_peak_shaving,
    check_pv_and_submeter,
    check_selfcheck,
    check_tessie,
    check_twc,
    evcharger_connection,
    render_report,
)

STATE = SignalKind.STATE
PHASES = {
    f"grid.l{n}.{q}": v
    for n in (1, 2, 3)
    for q, v in (("power_w", 500.0), ("current_a", 2.2), ("voltage_v", 230.0))
}


def test_grid_current_sign_detection() -> None:
    signed = [
        snap({"grid.l1.power_w": -2300.0, "grid.l1.current_a": -10.0, "grid.l1.voltage_v": 230.0})
    ]
    unsigned = [
        snap({"grid.l1.power_w": -2300.0, "grid.l1.current_a": 10.0, "grid.l1.voltage_v": 230.0})
    ]
    assert any("vorzeichenbehaftet" in f.detail for f in check_grid_meter(signed))
    assert any("vorzeichenlos" in f.detail for f in check_grid_meter(unsigned))


def test_grid_meter_presence_and_update_interval() -> None:
    # Probe alle 5 s, Wert jeweils 0,5 s alt → Aktualisierung etwa jede Sekunde
    def sample(at, values):
        readings = {
            k: Reading(
                v, at - timedelta(seconds=0.5), Quality.OK, "victron", SignalKind.MEASUREMENT
            )
            for k, v in values.items()
        }
        return Snapshot(at, MappingProxyType(readings))

    samples = [sample(T0 + timedelta(seconds=5 * i), PHASES) for i in range(4)]
    findings = check_grid_meter(samples)
    presence = next(f for f in findings if f.title == "Netzzähler P/I/U je Phase")
    assert presence.status == "ok"
    interval = next(f for f in findings if f.title == "Netzzähler Aktualisierung")
    assert interval.status == "ok" and "etwa alle 1.0 s" in interval.detail
    missing = check_grid_meter(
        [snap({k: v for k, v in PHASES.items() if k != "grid.l3.voltage_v"})]
    )
    assert next(f for f in missing if f.title == "Netzzähler P/I/U je Phase").status == "fail"


def test_pv_position_ac_out_ok() -> None:
    f = check_pv_and_submeter(
        snap({"pv.huawei.position": 1, "pv.huawei.energy_kwh": 5.0}, kind=STATE)
    )
    assert f[0].status == "ok"


def test_pv_position_ac_in_warns() -> None:
    assert check_pv_and_submeter(snap({"pv.huawei.position": 0}, kind=STATE))[0].status == "warn"


def test_submeter_counter_reported() -> None:
    findings = check_pv_and_submeter(
        snap(
            {
                "pv.huawei.position": 1,
                "pv.huawei.energy_kwh": 5.0,
                "load.obergeschoss.energy_kwh": 7.5,
            }
        )
    )
    assert any(f.status == "ok" and "load.obergeschoss.energy_kwh" in f.detail for f in findings)


def test_evcs_dump_unknown_product_warns() -> None:
    assert check_evcs_dump({5000: 0x1234, 5007: 1, 5008: 2})[0].status == "warn"


def test_evcs_dump_lists_nonzero_registers() -> None:
    findings = check_evcs_dump({5000: 0xC025, 5009: 0, 5017: 16, 5018: 125})
    assert findings[0].status == "ok"
    assert any(
        "5017" in f.detail and "5018" in f.detail and "5009" not in f.detail
        for f in findings
        if f.status == "info"
    )


def test_evcs_dump_without_registers_fails() -> None:
    assert check_evcs_dump({})[0].status == "fail"


def test_evcs_dump_all_zero_warns_with_the_cerbo_address() -> None:
    # Prüfprotokoll 10.10.2026: 5000–5199 alle 0, der Cerbo las die EVCS unter 192.168.81.41
    regs = dict.fromkeys(range(5000, 5200), 0)
    last = check_evcs_dump(regs, gx_connection="Modbus TCP 192.168.81.41")[-1]
    assert (last.item, last.title, last.status) == ("17.1-8", "EVCS-Registerabbild leer", "warn")
    assert last.detail == (
        "Alle 200 Register sind 0. Der Cerbo liest die EVCS über „Modbus TCP 192.168.81.41“; "
        "prüfe host und unit_id der Wallbox in config.yaml."
    )
    assert check_evcs_dump(regs)[-1].detail == (
        "Alle 200 Register sind 0. Prüfe host und unit_id der Wallbox in config.yaml."
    )


def test_evcharger_connection_on_the_cerbo() -> None:
    topics = {
        "N/c0619ab344fa/evcharger/40/Mgmt/Connection": '{"value":"Modbus TCP 192.168.81.41"}',
        "N/c0619ab344fa/evcharger/40/Mode": '{"value":0}',
    }
    assert evcharger_connection(topics, 40) == "Modbus TCP 192.168.81.41"
    assert evcharger_connection(topics, 41) is None
    assert evcharger_connection({"N/x/evcharger/40/Mgmt/Connection": "kein JSON"}, 40) is None


def test_mqtt_inventory_missing_instance_warns(cfg) -> None:
    f = check_mqtt_inventory(
        {
            "grid": {30},
            "vebus": {276},
            "battery": {512},
            "pvinverter": set(),
            "acload": {32},
            "evcharger": {40},
        },
        cfg,
    )
    assert f.status == "warn" and "pvinverter 31" in f.detail


def test_mqtt_inventory_complete_is_ok(cfg) -> None:
    services = {"grid": {30}, "vebus": {276}, "battery": {512}, "pvinverter": {31}, "acload": {32}}
    f = check_mqtt_inventory(services | {"evcharger": {40}, "system": {0}}, cfg)
    assert f.status == "ok" and "grid 30" in f.detail


def test_selfcheck_results_are_mapped_to_items() -> None:
    findings = check_selfcheck(
        [
            CheckResult("phases", "ok", "3 Phasen erkannt."),
            CheckResult("dess", "fail", "Dynamic ESS ist aktiv (Modus 1)."),
            CheckResult("evcs_mode.evcs", "unknown", "EVCS-Modus nicht verfügbar."),
            CheckResult("fresh_data", "ok", "Kritische Messwerte seit mindestens 30 s aktuell."),
        ]
    )
    assert [(f.item, f.status) for f in findings] == [
        ("17.1-1", "ok"),
        ("17.1-2", "fail"),
        ("17.1-8", "warn"),
        ("17.1-4", "ok"),
    ]
    assert findings[1].detail == "Dynamic ESS ist aktiv (Modus 1)."


def test_battery_values() -> None:
    complete = {
        "battery.soc_pct": 55.0,
        "battery.voltage_v": 52.1,
        "battery.current_a": 3.0,
        "battery.power_w": 156.0,
        "battery.ccl_a": 100.0,
        "battery.dcl_a": 150.0,
        "battery.installed_capacity_ah": 600.0,
    }
    assert check_battery(snap(complete)).status == "ok"
    partial = check_battery(snap({"battery.soc_pct": 55.0}))
    assert partial.status == "warn" and "battery.ccl_a" in partial.detail


def test_hub4_overrides_and_peak_shaving_are_info() -> None:
    s = snap(
        {
            "ess.override.setpoint": 0,
            "ess.override.maxdischargepower": -1,
            "settings.cgwacs.acpowersetpoint": 50,
            "settings.cgwacs.maxfeedinpower": -1,
            "settings.cgwacs.peakshaving.importlimit": 11000,
        },
        kind=STATE,
    )
    overrides = check_hub4_overrides(s)
    assert overrides.status == "info" and "ess.override.setpoint" in overrides.detail
    peak = check_peak_shaving(s)
    assert peak.status == "info" and "importlimit" in peak.detail
    assert "acpowersetpoint" not in peak.detail


def test_twc_fields() -> None:
    vitals = (
        {
            "contactor_closed": True,
            "vehicle_connected": True,
            "evse_state": 11,
            "session_energy_wh": 1.0,
        }
        | {f"current{t}_a": 1.0 for t in "ABC"}
        | {f"voltage{t}_v": 230.0 for t in "ABC"}
    )
    assert check_twc(vitals).status == "ok"
    missing = check_twc({k: v for k, v in vitals.items() if k != "voltageB_v"})
    assert missing.status == "warn" and "voltageB_v" in missing.detail


def test_tessie_schedule_warns() -> None:
    state = {
        "state": "online",
        "charge_state": {
            "battery_level": 63,
            "charge_limit_soc": 80,
            "charging_state": "Charging",
            "charge_amps": 16,
            "charge_current_request": 16,
            "charger_actual_current": 16,
            "charger_phases": 3,
            "charger_power": 11,
            "scheduled_charging_mode": "Off",
        },
        "drive_state": {"latitude": 48.2, "longitude": 16.37},
    }
    assert check_tessie(state).status == "ok"
    state["charge_state"]["scheduled_charging_mode"] = "StartAt"
    planned = check_tessie(state)
    assert planned.status == "warn" and "Ladeplan" in planned.detail


def test_ha_missing_entity_fails() -> None:
    f = check_ha({"sensor.egolf_soc": None})
    assert f[0].status == "fail"


def test_ha_entity_states() -> None:
    findings = check_ha(
        {"sensor.egolf_soc": {"state": "55"}, "sensor.egolf_range": {"state": "unavailable"}}
    )
    assert [f.status for f in findings] == ["ok", "warn"]


def test_influx_schema() -> None:
    show = {
        "databases": ["_internal", "bindaems", "homeassistant"],
        "retention_policies": [
            {"name": "raw", "duration": "2160h0m0s", "default": True},
            {"name": "long", "duration": "0s", "default": False},
        ],
        "ha_database": "homeassistant",
        "ha_measurements": ["%", "W", "kWh"],
    }
    findings = check_influx(show)
    assert [f.status for f in findings] == ["ok", "ok", "info"]
    no_long = show | {"retention_policies": show["retention_policies"][:1]}
    assert check_influx(no_long)[1].status == "fail"


def test_clock_offset_warns() -> None:
    f = check_clock_offsets({"homeassistant": 0.4, "influxdb": -3.2})
    assert f.status == "warn" and "influxdb" in f.detail


def test_clock_offset_ok() -> None:
    assert check_clock_offsets({"homeassistant": 0.4, "influxdb": -1.0}).status == "ok"


def test_report_contains_table_and_labels() -> None:
    md = render_report(
        [
            Finding("17.1-1", "Phasen", "ok", "3 Phasen erkannt."),
            Finding("17.1-8", "EVCS", "warn", "Unbekannte Produkt-ID"),
        ],
        {"Datum": "2026-10-08"},
    )
    assert md.startswith("# Prüfprotokoll Teil 1 – 2026-10-08")
    assert "| 17.1-1 | Phasen | OK | 3 Phasen erkannt. |" in md
    assert "| 17.1-8 | EVCS | WARNUNG | Unbekannte Produkt-ID |" in md


def test_report_escapes_table_breakers() -> None:
    md = render_report([Finding("17.1-9", "TWC", "fail", "a | b\nc")], {"Datum": "2026-10-08"})
    assert "| 17.1-9 | TWC | FEHLER | a \\| b<br>c |" in md

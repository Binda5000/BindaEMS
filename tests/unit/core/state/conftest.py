"""Gemeinsame Messwerte für abgeleitete Größen und Plausibilität."""

BASE: dict[str, float] = (
    {f"grid.l{n}.power_w": 1000.0 for n in (1, 2, 3)}
    | {f"grid.l{n}.voltage_v": 230.0 for n in (1, 2, 3)}
    | {f"vebus.l{n}.ac_in_power_w": 500.0 for n in (1, 2, 3)}
    | {f"vebus.l{n}.ac_out_power_w": 300.0 for n in (1, 2, 3)}
    | {f"pv.huawei.l{n}.power_w": 1000.0 for n in (1, 2, 3)}
    | {"pv.huawei.power_w": 3000.0, "grid.power_w": 3000.0}
)

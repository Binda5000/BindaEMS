"""Erfundener Verlauf für die Demo-Welt: 14 Tage Abrechnung vor ``T_DEMO``, Ladesessions und die
Zählerstände für den Abgleich (Plan 4a, Task 11). Reine Rechnung ohne app und Datenbank, damit
die Welt bei jedem Lauf dieselben Werte liefert.

Tagesprofil: PV als Glocke zwischen 07:00 und 17:00 (je Tag ein Bewölkungsfaktor), Hauslast mit
Spitzen am Morgen, Mittag und Abend, ein Akku von 30 kWh, der die Hälfte als Reserve hält: so
fällt an jedem Tag Netzbezug an, und der Abgleich vergleicht echte Werte. Die Flüsse folgen der
Wasserfall-Regel des core: PV deckt Haus und Wallboxen, lädt den Akku, der Rest geht ins Netz; was
fehlt, kommt aus dem Akku (außer in der Nachtladung), dann aus dem Netz. An jedem vierten Tag lädt
das Netz den Akku nachts. Die Wallboxen laden nur in den Sessions von ``SESSIONS``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from bindaems.shared.timeutil import LOCAL_TZ, SLOT, local_day_slots

FIRST_DAY = date(2026, 9, 25)
DAYS = 14  # 25.09. bis 08.10.2026; heute (09.10.) bringt die Welt selbst
LAST_DAY = FIRST_DAY + timedelta(days=DAYS - 1)
ESTIMATED_DAY = date(2026, 10, 3)
ESTIMATED_STARTS = (time(13, 0), time(13, 15))  # Ortszeit: zwei geschätzte Viertelstunden
IMPORT_COUNTER_END = 4290.0  # Zählerstand Netzbezug am Ende des Verlaufs (heute 09:00: 4321)
EXPORT_COUNTER_END = 1180.0

# Bewölkung je Tag (1 = klar)
CLOUD = (0.9, 0.5, 0.8, 1.0, 0.3, 0.7, 0.95, 0.6, 0.85, 0.4, 0.75, 1.0, 0.65, 0.8)
PV_PEAK_W = 4800.0
USABLE_KWH = 30.0
RESERVE_KWH = 15.0
START_STOCK_KWH = 16.0
MAX_CHARGE_WH = 800.0  # je Viertelstunde
MAX_DISCHARGE_WH = 3000.0
GRID_CHARGE_WH = 1250.0
GRID_CHARGE_HOURS = (2.0, 5.0)  # Ortszeit, von bis
WALLBOX_W = {"evcs": 2300.0, "twc": 4140.0}

# Preis des Bezugs brutto in ct/kWh nach Ortszeit (Stunde ab, Preis)
PRICE_CT = ((0, 11.8), (6, 17.5), (10, 14.2), (16, 24.6), (21, 16.4))


@dataclass(frozen=True)
class DemoSession:
    """Eine Session der Demo; ``end`` ``None``: läuft noch. Zeiten in Ortszeit, volle
    Viertelstunden; ``kind_later`` ist die Art der Belegung nach den ersten 30 Minuten."""

    wallbox: str
    vehicle: str | None  # Schlüssel des Fahrzeugs, ``guest`` oder ``None`` (unbekannt)
    start: datetime
    end: datetime | None
    kind: str
    kind_later: str


def _local(day: date, hour: int, minute: int) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=LOCAL_TZ)


# je Wallbox zwei beendete Sessions, an der EVCS dazu eine ohne erkanntes Fahrzeug („Unbekannt“);
# die Garage (``twc``) hat dazu die offene von heute
SESSIONS = (
    DemoSession(
        "evcs", "egolf", _local(date(2026, 9, 27), 8, 0), _local(date(2026, 9, 27), 12, 0),
        "default", "detected",
    ),
    DemoSession(
        "twc", "tesla", _local(date(2026, 9, 29), 17, 45), _local(date(2026, 9, 29), 21, 45),
        "detected", "detected",
    ),
    DemoSession(
        "evcs", None, _local(date(2026, 10, 3), 8, 30), _local(date(2026, 10, 3), 11, 0),
        "unclear", "unclear",
    ),
    DemoSession(
        "twc", "tesla", _local(date(2026, 10, 5), 10, 0), _local(date(2026, 10, 5), 14, 30),
        "detected", "detected",
    ),
    DemoSession(
        "evcs", "guest", _local(date(2026, 10, 7), 19, 0), _local(date(2026, 10, 7), 23, 0),
        "default", "default",
    ),
    DemoSession("twc", "tesla", _local(date(2026, 10, 9), 8, 45), None, "detected", "detected"),
)  # fmt: skip


@dataclass(frozen=True)
class HistorySlot:
    start: datetime  # UTC
    flows_wh: dict[str, float]
    counters: dict[str, tuple[float, float]]
    estimated: bool
    price_ct: float


def _bump(hour: float, center: float, width: float) -> float:
    return math.exp(-(((hour - center) / width) ** 2) / 2)


def _pv_wh(day_index: int, hour: float) -> float:
    if not 7.0 <= hour <= 17.0:
        return 0.0
    return PV_PEAK_W * CLOUD[day_index] * math.sin(math.pi * (hour - 7.0) / 10.0) / 4


def _house_wh(hour: float) -> float:
    watts = (
        480.0
        + 600.0 * _bump(hour, 7.5, 1.0)
        + 300.0 * _bump(hour, 12.5, 1.0)
        + 1900.0 * _bump(hour, 19.0, 2.2)
    )
    return watts / 4


def price_ct(start: datetime) -> float:
    """Bezugspreis brutto des Slots in ct/kWh."""
    hour = start.astimezone(LOCAL_TZ).hour
    return [price for from_hour, price in PRICE_CT if hour >= from_hour][-1]


def _wallbox_wh(start: datetime) -> dict[str, float]:
    """Wh je Wallbox im Slot: sie laden in den Sessions mit fester Leistung."""
    wanted: dict[str, float] = {}
    for session in SESSIONS:
        if session.end is not None and session.start <= start.astimezone(LOCAL_TZ) < session.end:
            wanted[session.wallbox] = WALLBOX_W[session.wallbox] / 4
    return wanted


def _flows(
    pv: float, house: float, wallboxes: dict[str, float], stock_kwh: float, grid_charge: bool
) -> tuple[dict[str, float], float]:
    """Flüsse eines Slots in Wh und der Akkuinhalt danach (kWh), nach der Regel des core."""
    flows: dict[str, float] = {}

    def add(name: str, wh: float) -> None:
        if wh > 0.05:
            flows[name] = round(wh, 1)

    left = pv
    covered = min(left, house)
    add("pv>house", covered)
    left -= covered
    house_need = house - covered
    need: dict[str, float] = {}
    for key, wh in wallboxes.items():
        taken = min(left, wh)
        add(f"pv>wb:{key}", taken)
        left -= taken
        need[key] = wh - taken
    charge = min(left, MAX_CHARGE_WH, (USABLE_KWH - stock_kwh) * 1000)
    add("pv>battery", charge)
    left -= charge
    stock_kwh += charge / 1000
    add("pv>grid", left)

    limit = 0.0 if grid_charge else min(max(0.0, stock_kwh - RESERVE_KWH) * 1000, MAX_DISCHARGE_WH)
    for target, wanted in (("house", house_need), *((f"wb:{k}", w) for k, w in need.items())):
        given = min(wanted, limit)
        add(f"battery>{target}", given)
        limit -= given
        stock_kwh -= given / 1000
        add(f"grid>{target}", wanted - given)
    if grid_charge:
        bought = min(GRID_CHARGE_WH, (USABLE_KWH - stock_kwh) * 1000)
        add("grid>battery", bought)
        stock_kwh += bought / 1000
    return flows, stock_kwh


def _is_estimated(start: datetime) -> bool:
    local = start.astimezone(LOCAL_TZ)
    return local.date() == ESTIMATED_DAY and local.time() in ESTIMATED_STARTS


def history_slots() -> list[HistorySlot]:
    """Alle Slots der 14 Tage in zeitlicher Reihenfolge; die Zählerstände enden bei
    ``IMPORT_COUNTER_END`` bzw. ``EXPORT_COUNTER_END``."""
    raw: list[tuple[datetime, dict[str, float], bool]] = []
    stock = START_STOCK_KWH
    for index in range(DAYS):
        for start in local_day_slots(FIRST_DAY + timedelta(days=index)):
            local = start.astimezone(LOCAL_TZ)
            hour = local.hour + local.minute / 60 + SLOT.total_seconds() / 7200
            charging = index % 4 == 1 and GRID_CHARGE_HOURS[0] <= hour < GRID_CHARGE_HOURS[1]
            flows, stock = _flows(
                _pv_wh(index, hour), _house_wh(hour), _wallbox_wh(start), stock, charging
            )
            raw.append((start, flows, _is_estimated(start)))
    imported = [
        sum(wh for name, wh in flows.items() if name.startswith("grid>")) for _, flows, _ in raw
    ]
    exported = [
        sum(wh for name, wh in flows.items() if name.endswith(">grid")) for _, flows, _ in raw
    ]
    import_kwh = IMPORT_COUNTER_END - sum(imported) / 1000
    export_kwh = EXPORT_COUNTER_END - sum(exported) / 1000
    slots: list[HistorySlot] = []
    for (start, flows, estimated), grid_in, grid_out in zip(raw, imported, exported, strict=True):
        counters = {
            "grid.energy_import_kwh": (round(import_kwh, 3), round(import_kwh + grid_in / 1000, 3)),
            "grid.energy_export_kwh": (
                round(export_kwh, 3),
                round(export_kwh + grid_out / 1000, 3),
            ),
        }
        import_kwh += grid_in / 1000
        export_kwh += grid_out / 1000
        slots.append(
            HistorySlot(start.astimezone(UTC), flows, {} if estimated else counters, estimated,
                        price_ct(start))
        )  # fmt: skip
    return slots


# --- Sessions -----------------------------------------------------------------------------

Observation = tuple[datetime, dict[str, tuple[bool, str | None, str | None]]]
HEARTBEAT = timedelta(minutes=15)  # die Beobachtung ist dichter als SESSION_GAP (30 min)
KIND_AFTER = timedelta(minutes=30)


def observations(now: datetime) -> list[Observation]:
    """Beobachtungen ``(Zeitpunkt, je Wallbox: steckt, Belegung, Art)`` für
    ``ChargingTracker.record`` in zeitlicher Reihenfolge: je Session alle 15 Minuten, am Ende
    abgezogen, die offene bis ``now``."""
    idle = (False, None, None)
    events: dict[datetime, dict[str, tuple[bool, str | None, str | None]]] = {}
    for session in SESSIONS:
        start = session.start.astimezone(UTC)
        end = session.end.astimezone(UTC) if session.end is not None else now
        tick = start
        while tick <= end:
            state = events.setdefault(tick, {})
            if session.end is not None and tick == end:
                state[session.wallbox] = idle
            else:
                kind = session.kind if tick - start < KIND_AFTER else session.kind_later
                state[session.wallbox] = (True, session.vehicle, kind)
            tick += HEARTBEAT
        if session.end is not None:
            events.setdefault(end, {})[session.wallbox] = idle
    return [
        (when, {wallbox: events[when].get(wallbox, idle) for wallbox in WALLBOX_W})
        for when in sorted(events)
    ]


# --- Zählerstände für den Abgleich -----------------------------------------------------------

# Verhältnis Zähler zu Abrechnung je Tag und Größe; sonst eine kleine Abweichung unter 1 %
COUNTER_FACTOR = {(date(2026, 10, 4), "import"): 1.035}  # der markierte Tag
PV_FACTOR = 1.06  # DC-gekoppelte PV: die Abrechnung liegt unter dem Zähler, ohne Markierung
MISSING_READING = (date(2026, 10, 6), "wb:evcs")  # Mitternacht, an der der Zählerstand fehlt
COUNTER_BASE = {
    "import": 3880.0,
    "export": 1010.0,
    "pv": 7600.0,
    "wb:evcs": 310.5,
    "wb:twc": 1210.2,
}


def day_kwh(slots: Sequence[HistorySlot]) -> dict[date, dict[str, float]]:
    """Summe der Abrechnung je Ortstag in kWh: ``import``, ``export``, ``pv``, ``wb:<id>``."""
    totals: dict[date, dict[str, float]] = {}
    for slot in slots:
        day = totals.setdefault(slot.start.astimezone(LOCAL_TZ).date(), {})
        for name, wh in slot.flows_wh.items():
            source, _, target = name.partition(">")
            keys = [k for k, hit in (("import", source == "grid"), ("export", target == "grid"),
                                     ("pv", source == "pv"), (target, target.startswith("wb:")))
                    if hit]  # fmt: skip
            for key in keys:
                day[key] = day.get(key, 0.0) + wh / 1000
    return totals


def counter_readings(
    slots: Sequence[HistorySlot], first: date, last: date, names: Mapping[str, Sequence[str]]
) -> dict[tuple[str, datetime], float | None]:
    """Zählerstände um die Mitternächte von ``first`` bis ``last + 1``. ``names`` bildet die Größe
    der Abrechnung (``import`` …) auf die Zählersignale ab; bei mehreren trägt das erste alles."""
    totals = day_kwh(slots)
    readings: dict[tuple[str, datetime], float | None] = {}
    value = {key: COUNTER_BASE[key] for key in names}
    days = [first + timedelta(days=n) for n in range((last - first).days + 1)]
    for number, day in enumerate([*days, last + timedelta(days=1)]):
        midnight = local_day_slots(day)[0]
        for key, signals in names.items():
            for position, signal in enumerate(signals):
                shown = value[key] if position == 0 else 0.0
                missing = (day, key) == MISSING_READING
                readings[(signal, midnight)] = None if missing else round(shown, 3)
        if number < len(days):
            for key in names:
                factor = COUNTER_FACTOR.get((day, key), 1 + 0.004 * math.sin(number + len(key)))
                if key == "pv":
                    factor = PV_FACTOR
                value[key] += totals.get(day, {}).get(key, 0.0) * factor
    return readings

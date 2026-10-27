"""Wärmemodell des Hauses je Betriebsart der Klimaanlage (Plan 5c, Entscheidungen 3, 4, 10, 13).

Ein Speicher erster Ordnung, grau gerechnet aus den Viertelstunden der Klimaanlage
(``ThermalSlot``):

- **Grundleistung** ``P₀ = max(0, u · ΔT + s · pv_kw + b)``, kleinste Quadrate über die Slots, in
  denen die Anlage in der Betriebsart läuft, der Versatz 0 ist und Ist und Soll höchstens 0,5 K
  auseinander liegen. ``ΔT`` ist beim Heizen Soll ohne Versatz minus außen, beim Kühlen außen
  minus Soll; ``s`` hat ein freies Vorzeichen.
- **Zeitkonstante** ``τ``: je Auskühl-Phase (Anlage läuft mindestens 2 h nicht, PV unter 50 W,
  Abstand innen–außen mindestens 3 K in Richtung der Betriebsart) eine Gerade durch
  ``ln |T_innen − T_außen|`` über der Zeit in Stunden (UTC, auch über die Zeitumstellung),
  ``τ = −1 / Steigung``; maßgeblich ist der Median der Phasen.
- **Speicher** ``C = τ · u / 1000`` in kWh Strom je K; ab drei Versatz-Ereignissen der Median
  ihres Speichers je K, begrenzt auf das 0,5- bis 1,5-Fache davon.
- **Höchstleistung**: 95. Perzentil der Leistung über die laufenden Slots der Betriebsart.

Dazu je Wochentaggruppe (Mo–Fr, Sa, So/Feiertag) und lokaler Viertelstunde der Median des
Sollwerts ohne Versatz und der Laufanteil über 14 Tage (``mode_profile``).

Das Modul ist rein: keine Uhr, kein Lesen und Schreiben. Das Fenster (28 Tage, ``FIT_DAYS``) gibt
der Aufrufer mit den Slots vor. Unbekannt bleibt unbekannt: Ein Slot ohne Versatz, Leistung oder
PV zählt nicht als „Versatz 0“, nicht als Auskühlen und nicht in der Anpassung.
"""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from typing import Final, Literal

import numpy as np

from bindaems.app.learning.driving_profile import GROUPS, percentile, weekday_of
from bindaems.app.learning.thermal_data import ThermalSlot
from bindaems.shared.timeutil import LOCAL_TZ, SLOT

Mode = Literal["heizen", "kuehlen"]
Reason = Literal["ok", "no_data", "few_days", "few_episodes", "poor_fit", "implausible_tau"]

FIT_DAYS: Final = 28  # Fenster der Anpassung, gibt der Aufrufer vor
PROFILE_DAYS: Final = 14  # Fenster des Profils (Entscheidung 13)
SLOT_H: Final = SLOT.total_seconds() / 3600

# Entscheidung 4: wann ein Modell taugt
MIN_DAYS: Final = 14
DAY_MIN_H: Final = 4.0  # ein Tag zählt mit mindestens so viel Zeit in der Betriebsart
MIN_EPISODES: Final = 3
EPISODE_MIN_H: Final = 2.0
EPISODE_MAX_PV_W: Final = 50.0  # PV darunter
EPISODE_MIN_DIFF_K: Final = 3.0
FIT_MAX_GAP_K: Final = 0.5  # Ist und Soll höchstens so weit auseinander
MIN_R2: Final = 0.5
TAU_MIN_H: Final = 4.0
TAU_MAX_H: Final = 200.0

# Entscheidung 10: Versatz-Ereignisse
MIN_EVENTS: Final = 3
EVENT_HOLD_SLOTS: Final = 2  # mindestens 30 min gehalten
EVENT_SETTLED_K: Final = 0.3  # bis Ist und Soll höchstens so weit auseinander liegen
EVENT_MAX_SLOTS: Final = 12  # höchstens 3 h
EVENT_C_MIN: Final = 0.5  # Faktor auf den Speicher aus Grundleistung und τ
EVENT_C_MAX: Final = 1.5

# Viertelstundenmittel des Versatzes: so nah an 0 gilt er als 0, so nah am Wert als gehalten
OFFSET_TOL_K: Final = 0.05

MAX_W_PERCENTILE: Final = 95.0


@dataclass(frozen=True)
class Episode:
    """Eine Auskühl-Phase (beim Kühlen: Aufwärm-Phase); ``start`` UTC, Dauer in UTC-Stunden.

    ``tau_h`` ist ``math.inf``, wenn der Abstand innen–außen nicht abnimmt."""

    start: datetime
    hours: float
    tau_h: float


@dataclass(frozen=True)
class OffsetEvent:
    """Ein Versatz-Ereignis: Sprung des Versatzes von 0 auf ``offset_k`` (``start`` UTC) und der
    Speicher je K, den die Mehrleistung bis zum Erreichen des neuen Sollwerts ergibt."""

    start: datetime
    offset_k: float
    kwh_per_k: float


@dataclass(frozen=True)
class ModeModel:
    """Das Wärmemodell einer Betriebsart; mit ``dataclasses.asdict`` als JSON ablegbar."""

    mode: Mode
    days: int
    u_w_per_k: float | None
    pv_w_per_kw: float | None
    base_w: float | None
    r2: float | None
    tau_h: float | None
    episodes: int
    c_kwh_per_k: float | None
    c_model_kwh_per_k: float | None
    events: int
    event_error_pct: float | None
    max_w: float | None
    usable: bool
    reason: Reason


@dataclass(frozen=True)
class ModeProfile:
    """Sollwert ohne Versatz (Median) und Laufanteil in der Betriebsart je
    ``(Gruppe, lokale Viertelstunde)``; Gruppe 0 = Mo–Fr, 1 = Sa, 2 = So/Feiertag. Ohne Werte
    fehlt der Schlüssel."""

    setpoint_c: dict[tuple[int, int], float]
    on_share: dict[tuple[int, int], float]


@dataclass(frozen=True)
class _Fit:
    u: float
    s: float
    b: float
    r2: float | None


def _delta_t(mode: Mode, inner_c: float, outdoor_c: float) -> float:
    """Temperaturdifferenz in Richtung der Betriebsart: beim Heizen innen minus außen, beim
    Kühlen außen minus innen."""
    return inner_c - outdoor_c if mode == "heizen" else outdoor_c - inner_c


def _direction(mode: Mode) -> float:
    """Vorzeichen eines Versatzes, der mehr Leistung braucht (Vorheizen +, Vorkühlen −)."""
    return 1.0 if mode == "heizen" else -1.0


def _is_zero(offset_k: float | None) -> bool:
    return offset_k is not None and abs(offset_k) < OFFSET_TOL_K


def _base_setpoint(slot: ThermalSlot) -> float | None:
    if slot.setpoint_c is None or slot.offset_k is None:
        return None
    return slot.setpoint_c - slot.offset_k


def _ordered(slots: Sequence[ThermalSlot]) -> list[ThermalSlot]:
    return sorted(slots, key=lambda s: s.start)


def _follows(earlier: ThermalSlot, later: ThermalSlot) -> bool:
    return later.start - earlier.start == SLOT


def _mode_days(slots: Sequence[ThermalSlot], mode: Mode) -> int:
    """Lokale Tage mit mindestens ``DAY_MIN_H`` in der Betriebsart."""
    per_day: dict[date, int] = defaultdict(int)
    for slot in slots:
        if slot.mode == mode:
            per_day[slot.start.astimezone(LOCAL_TZ).date()] += 1
    return sum(1 for count in per_day.values() if count * SLOT_H >= DAY_MIN_H)


def _fit_base(slots: Sequence[ThermalSlot], mode: Mode) -> _Fit | None:
    """Kleinste Quadrate ``p_w ≈ u · ΔT + s · pv_kw + b`` über die tauglichen Slots.

    Ändert sich die PV in den Slots nicht, ist ``s`` nicht bestimmbar und bleibt 0 (die PV trägt
    dann im Bereich der Daten nichts bei). Ist ``u`` nicht bestimmbar (gleiche Differenz in allen
    Slots), gibt es keine Anpassung."""
    rows: list[tuple[float, float, float]] = []
    for slot in slots:
        base = _base_setpoint(slot)
        if (
            slot.mode != mode
            or slot.running is not True
            or not _is_zero(slot.offset_k)
            or base is None
            or slot.setpoint_c is None
            or slot.power_w is None
            or slot.pv_w is None
            or abs(slot.actual_c - slot.setpoint_c) > FIT_MAX_GAP_K
        ):
            continue
        rows.append((_delta_t(mode, base, slot.outdoor_c), slot.pv_w / 1000, slot.power_w))
    if not rows:
        return None
    data = np.array(rows, dtype=float)
    delta, pv_kw, power = data[:, 0], data[:, 1], data[:, 2]
    with_pv = bool(np.ptp(pv_kw) > 0)
    columns = [delta, pv_kw, np.ones_like(delta)] if with_pv else [delta, np.ones_like(delta)]
    design = np.column_stack(columns)
    coef, _, rank, _ = np.linalg.lstsq(design, power, rcond=None)
    if rank < design.shape[1]:
        return None
    u = float(coef[0])
    s = float(coef[1]) if with_pv else 0.0
    b = float(coef[-1])
    residual = power - design @ coef
    ss_tot = float(np.sum((power - power.mean()) ** 2))
    r2 = 1 - float(np.sum(residual**2)) / ss_tot if ss_tot > 0 else None
    return _Fit(u=u, s=s, b=b, r2=r2)


def _p0(fit: _Fit, mode: Mode, setpoint_c: float, outdoor_c: float, pv_w: float) -> float:
    return max(0.0, fit.u * _delta_t(mode, setpoint_c, outdoor_c) + fit.s * pv_w / 1000 + fit.b)


def _free_float(slot: ThermalSlot, mode: Mode) -> bool:
    return (
        slot.running is False
        and slot.pv_w is not None
        and slot.pv_w < EPISODE_MAX_PV_W
        and _delta_t(mode, slot.actual_c, slot.outdoor_c) >= EPISODE_MIN_DIFF_K
    )


def _episode(run: Sequence[ThermalSlot], mode: Mode) -> Episode:
    first = run[0].start
    hours = np.array([(s.start - first).total_seconds() / 3600 for s in run])
    logs = np.array([math.log(_delta_t(mode, s.actual_c, s.outdoor_c)) for s in run])
    design = np.column_stack([hours, np.ones_like(hours)])
    (slope, _), *_ = np.linalg.lstsq(design, logs, rcond=None)
    tau_h = -1 / float(slope) if slope < 0 else math.inf
    span = (run[-1].start + SLOT - first).total_seconds() / 3600
    return Episode(start=first, hours=span, tau_h=tau_h)


def free_float_episodes(slots: Sequence[ThermalSlot], mode: Mode) -> list[Episode]:
    """Die Auskühl-Phasen der Betriebsart (beim Kühlen: Aufwärm-Phasen), nach Beginn sortiert.

    Eine Phase ist eine lückenlose Folge von Viertelstunden, in denen die Anlage nicht läuft
    (``running`` falsch, nicht unbekannt), die PV unter 50 W liegt und der Abstand innen–außen in
    Richtung der Betriebsart mindestens 3 K ist; sie zählt ab 2 h."""
    episodes: list[Episode] = []
    run: list[ThermalSlot] = []

    def close() -> None:
        if len(run) * SLOT_H >= EPISODE_MIN_H:
            episodes.append(_episode(run, mode))
        run.clear()

    for slot in _ordered(slots):
        if run and not _follows(run[-1], slot):
            close()
        if _free_float(slot, mode):
            run.append(slot)
        else:
            close()
    close()
    return episodes


def _fit_of(model: ModeModel) -> _Fit:
    if model.u_w_per_k is None or model.pv_w_per_kw is None or model.base_w is None:
        raise ValueError("Das Modell hat keine Grundleistung.")
    return _Fit(u=model.u_w_per_k, s=model.pv_w_per_kw, b=model.base_w, r2=model.r2)


@dataclass(frozen=True)
class _EventSlot:
    slot: ThermalSlot
    offset_k: float
    setpoint_c: float
    power_w: float
    pv_w: float


def _event_slot(slot: ThermalSlot, mode: Mode) -> _EventSlot | None:
    """Die Werte eines Slots, der in einem Ereignis zählen kann: Anlage läuft in der Betriebsart,
    Versatz, Sollwert, Leistung und PV bekannt."""
    if (
        slot.mode != mode
        or slot.running is not True
        or slot.offset_k is None
        or slot.setpoint_c is None
        or slot.power_w is None
        or slot.pv_w is None
    ):
        return None
    return _EventSlot(slot, slot.offset_k, slot.setpoint_c, slot.power_w, slot.pv_w)


def _event_run(slots: Sequence[ThermalSlot], i: int, mode: Mode) -> list[_EventSlot]:
    """Die lückenlose Folge zählbarer Slots ab ``i``, höchstens 3 h."""
    run: list[_EventSlot] = []
    for j in range(i, min(i + EVENT_MAX_SLOTS, len(slots))):
        item = _event_slot(slots[j], mode)
        if item is None or (j > i and not _follows(slots[j - 1], slots[j])):
            break
        run.append(item)
    return run


def _event_at(slots: Sequence[ThermalSlot], i: int, mode: Mode, fit: _Fit) -> OffsetEvent | None:
    """Das Ereignis, wenn der Versatz in Slot ``i`` von 0 springt.

    Der Slot des Sprungs ist ein Mittel und darf zwischen 0 und dem neuen Wert liegen; der Wert
    ist der des folgenden Slots, gehalten mindestens 30 min (zwei Slots). Die Mehrleistung über
    ``P₀`` zählt ab dem Sprung bis einschließlich des ersten Slots mit dem neuen Wert, in dem Ist
    und Soll höchstens 0,3 K auseinander liegen, höchstens 3 h. Ändert sich der Versatz vorher,
    fehlt ein Wert oder reißt die Folge ab, zählt das Ereignis nicht."""
    prev = slots[i - 1]
    if not _follows(prev, slots[i]) or not _is_zero(prev.offset_k):
        return None
    run = _event_run(slots, i, mode)
    if len(run) < 1 + EVENT_HOLD_SLOTS:
        return None
    jump, value = run[0].offset_k, run[1].offset_k
    if (
        _is_zero(jump)
        or jump * value <= 0
        or abs(jump) >= abs(value) + OFFSET_TOL_K
        or any(abs(item.offset_k - value) >= OFFSET_TOL_K for item in run[1 : 1 + EVENT_HOLD_SLOTS])
    ):
        return None

    energy_wh = 0.0
    settled = False
    for index, item in enumerate(run):
        at_value = abs(item.offset_k - value) < OFFSET_TOL_K
        if index > 0 and not at_value:
            return None
        base = item.setpoint_c - item.offset_k
        p0 = _p0(fit, mode, base, item.slot.outdoor_c, item.pv_w)
        energy_wh += (item.power_w - p0) * SLOT_H
        if at_value and abs(item.slot.actual_c - item.setpoint_c) <= EVENT_SETTLED_K:
            settled = True
            break
    if not settled and len(run) < EVENT_MAX_SLOTS:
        return None
    # Durch den Versatz in Richtung der Betriebsart: beim Vorheizen ist das der Betrag; beim
    # Absenken sind Energie und Versatz negativ, der Speicher wieder positiv
    kwh_per_k = energy_wh / 1000 / (value * _direction(mode))
    return OffsetEvent(start=run[0].slot.start, offset_k=value, kwh_per_k=kwh_per_k)


def offset_events(slots: Sequence[ThermalSlot], mode: Mode, model: ModeModel) -> list[OffsetEvent]:
    """Die Versatz-Ereignisse der Betriebsart, mit ``P₀`` aus der Grundleistung von ``model``.

    Ein Modell ohne Grundleistung ist ein Programmfehler (``ValueError``)."""
    fit = _fit_of(model)
    ordered = _ordered(slots)
    events: list[OffsetEvent] = []
    for i in range(1, len(ordered)):
        event = _event_at(ordered, i, mode, fit)
        if event is not None:
            events.append(event)
    return events


def _reason(
    slots: Sequence[ThermalSlot],
    days: int,
    episodes: int,
    fit: _Fit | None,
    tau_h: float | None,
) -> Reason:
    """Der erste Grund, der fehlt (Entscheidung 4); ``ok``, wenn das Modell taugt.

    Fällt die Leistung mit der Temperaturdifferenz (``u ≤ 0``), beschreibt die Anpassung kein
    Heizen oder Kühlen: ``poor_fit``."""
    if not slots:
        return "no_data"
    if days < MIN_DAYS:
        return "few_days"
    if episodes < MIN_EPISODES:
        return "few_episodes"
    if fit is None or fit.r2 is None or fit.r2 < MIN_R2 or fit.u <= 0:
        return "poor_fit"
    if tau_h is None or not TAU_MIN_H <= tau_h <= TAU_MAX_H:
        return "implausible_tau"
    return "ok"


def fit_mode(slots: Sequence[ThermalSlot], mode: Mode) -> ModeModel:
    """Das Wärmemodell der Betriebsart aus den Slots des Fensters.

    Alle Zahlen, die sich bestimmen lassen, stehen auch in einem untauglichen Modell (für den
    Status); ``reason`` nennt den ersten fehlenden Grund."""
    ordered = _ordered(slots)
    days = _mode_days(ordered, mode)
    fit = _fit_base(ordered, mode)
    episodes = free_float_episodes(ordered, mode)
    tau_all = statistics.median(e.tau_h for e in episodes) if episodes else None
    tau_h = tau_all if tau_all is not None and math.isfinite(tau_all) else None
    c_model = tau_h * fit.u / 1000 if fit is not None and fit.u > 0 and tau_h is not None else None
    powers = [s.power_w for s in ordered if s.mode == mode and s.power_w is not None]
    reason = _reason(ordered, days, len(episodes), fit, tau_h)
    model = ModeModel(
        mode=mode,
        days=days,
        u_w_per_k=fit.u if fit is not None else None,
        pv_w_per_kw=fit.s if fit is not None else None,
        base_w=fit.b if fit is not None else None,
        r2=fit.r2 if fit is not None else None,
        tau_h=tau_h,
        episodes=len(episodes),
        c_kwh_per_k=c_model,
        c_model_kwh_per_k=c_model,
        events=0,
        event_error_pct=None,
        max_w=percentile(powers, MAX_W_PERCENTILE) if powers else None,
        usable=reason == "ok",
        reason=reason,
    )
    if fit is None:
        return model
    events = offset_events(ordered, mode, model)
    if not events:
        return model
    median = statistics.median(e.kwh_per_k for e in events)
    c = c_model
    error_pct = None
    if c_model is not None:
        error_pct = (median / c_model - 1) * 100
        if len(events) >= MIN_EVENTS:
            c = min(max(median, EVENT_C_MIN * c_model), EVENT_C_MAX * c_model)
    return replace(model, c_kwh_per_k=c, events=len(events), event_error_pct=error_pct)


def _group(day: date) -> int:
    weekday = weekday_of(day)
    return next(index for index, group in enumerate(GROUPS) if weekday in group)


def mode_profile(slots: Sequence[ThermalSlot], mode: Mode, *, today: date) -> ModeProfile:
    """Sollwert ohne Versatz und Laufanteil je Gruppe und lokaler Viertelstunde über die 14
    vollständigen lokalen Tage vor ``today`` (Entscheidung 13).

    Der Sollwert ist der Median über die Slots in der Betriebsart mit bekanntem Sollwert und
    Versatz. Der Laufanteil ist der Anteil der Slots in der Betriebsart an denen mit bekannter
    Betriebsart, je Tag gemittelt: An Umstellungstagen gilt die lokale Uhrzeit, eine doppelte
    Viertelstunde zählt je zur Hälfte, eine fehlende fließt nicht ein."""
    first = today - timedelta(days=PROFILE_DAYS)
    setpoints: dict[tuple[int, int], list[float]] = defaultdict(list)
    flags: dict[tuple[date, int], list[bool]] = defaultdict(list)
    for slot in slots:
        local = slot.start.astimezone(LOCAL_TZ)
        day = local.date()
        if not first <= day < today or slot.mode is None:
            continue
        quarter = (local.hour * 60 + local.minute) // 15
        flags[(day, quarter)].append(slot.mode == mode)
        base = _base_setpoint(slot)
        if slot.mode == mode and base is not None:
            setpoints[(_group(day), quarter)].append(base)
    shares: dict[tuple[int, int], list[float]] = defaultdict(list)
    for (day, quarter), values in flags.items():
        shares[(_group(day), quarter)].append(sum(values) / len(values))
    return ModeProfile(
        setpoint_c={key: statistics.median(values) for key, values in sorted(setpoints.items())},
        on_share={key: sum(values) / len(values) for key, values in sorted(shares.items())},
    )


def base_power_w(model: ModeModel, setpoint_c: float, outdoor_c: float, pv_w: float) -> float:
    """Grundleistung ``P₀ = max(0, u · ΔT + s · pv_kw + b)`` beim Sollwert ohne Versatz
    ``setpoint_c``; ein Modell ohne Grundleistung ist ein Programmfehler (``ValueError``)."""
    return _p0(_fit_of(model), model.mode, setpoint_c, outdoor_c, pv_w)

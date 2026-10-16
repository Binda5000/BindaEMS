"""Auslöser der Neuplanung (Plan 3b, Präzisierung 14): reine Prüfungen für den Planungsdienst.

Der Dienst prüft alle ``WATCH_S`` Sekunden Fingerabdrücke von Preisen und Prognosen, den SOC
gegen den Plan und die Monatsspitze gegen den Plan; die Viertelstunde kommt zur Minute 1 nach
Slotbeginn. Einstellungen lösen nur aus, wenn sich Planung, Tarif oder Einspeisung ändern.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final

from bindaems.shared.plan import Plan, PlanTrigger
from bindaems.shared.settings import RuntimeSettings
from bindaems.shared.timeutil import SLOT, ensure_utc, slot_start

WATCH_S: Final = 30.0
TICK_DELAY: Final = timedelta(minutes=1)
SOC_DEVIATION_PP: Final = 5.0
PEAK_RISE_KW: Final = 0.05


@dataclass(frozen=True)
class Fingerprint:
    """Stand der Eingangsdaten: letzter bekannter Preis-Slot und Zahl der ``primary``-Slots im
    Fenster, Ausgabezeit von PV- und Lastprognose."""

    last_price: datetime | None
    primary: int
    pv_issued: datetime | None
    load_issued: datetime | None


def changed(old: Fingerprint | None, new: Fingerprint) -> list[PlanTrigger]:
    """Auslöser aus zwei Fingerabdrücken, in der Reihenfolge Preise, PV-, Lastprognose.

    Neue Preise heißt: ein anderer letzter Slot oder mehr ``primary``-Slots (Ersatzpreise
    ersetzt). Weniger ``primary``-Slots löst nicht aus, denn das Fenster wandert mit dem
    aktuellen Slot und verliert dabei jede Viertelstunde einen. Ohne alten Fingerabdruck (erste
    Prüfung) gibt es keinen Auslöser.
    """
    if old is None:
        return []
    result: list[PlanTrigger] = []
    if new.last_price != old.last_price or new.primary > old.primary:
        result.append("prices")
    if new.pv_issued != old.pv_issued:
        result.append("pv_forecast")
    if new.load_issued != old.load_issued:
        result.append("load_forecast")
    return result


def next_tick(now: datetime) -> datetime:
    """Nächste Neuplanung zur Viertelstunde: eine Minute nach Beginn des nächsten Slots."""
    return slot_start(now) + SLOT + TICK_DELAY


def expected_soc_pct(plan: Plan, now: datetime) -> float | None:
    """Erwarteter SOC (%) zu ``now``, linear zwischen Anfang und Ende des aktuellen Slots;
    ``None``, wenn der Plan nicht gilt oder den Slot nicht enthält."""
    if not plan.valid_at(now):
        return None
    slot = plan.slot_at(now)
    if slot is None:
        return None
    share = (ensure_utc(now) - slot.start) / SLOT
    return slot.soc_start_pct + (slot.soc_end_pct - slot.soc_start_pct) * share


def soc_deviates(plan: Plan | None, now: datetime, soc_pct: float | None) -> bool:
    """Weicht der SOC um mehr als ``SOC_DEVIATION_PP`` Prozentpunkte vom Plan ab?"""
    if plan is None or soc_pct is None:
        return False
    expected = expected_soc_pct(plan, now)
    return expected is not None and abs(soc_pct - expected) > SOC_DEVIATION_PP


def peak_rose(plan: Plan | None, peak_kw: float | None) -> bool:
    """Liegt die Monatsspitze mehr als ``PEAK_RISE_KW`` über der des Plans (ohne: 0)?"""
    if plan is None or peak_kw is None:
        return False
    return peak_kw - (plan.month_peak_kw or 0.0) > PEAK_RISE_KW


def settings_relevant(old: RuntimeSettings, new: RuntimeSettings) -> bool:
    """Betrifft die Änderung den Plan (Planung, Tarif oder Einspeisung)?"""
    return (old.planning, old.tariff, old.feed_in) != (new.planning, new.tariff, new.feed_in)

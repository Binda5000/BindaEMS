"""Aktivierungsregel des Lastmodells (Plan 5a, Entscheidung 11).

Reine Rechnung ohne IO. Jede Nacht entscheidet sie, ob die Planung das Lastmodell oder das Profil
nutzt: Das Modell gilt, wenn sein MAE über die letzten 14 vollen Tage mindestens 10 % unter dem des
Profils liegt. Grundlage ist der Schattenbetrieb, sobald er an genug Tagen Werte hat, davor der
Backtest. Heute zählt nie mit, denn der Tag ist noch nicht voll.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final, Literal

from bindaems.app.forecast.archive import Actual, ForecastRecord, HorizonClass
from bindaems.app.learning.backtest import DaySums
from bindaems.shared.timeutil import LOCAL_TZ

ACTIVATION_DAYS: Final = 14
MIN_SHADOW_DAYS: Final = 10
MIN_IMPROVEMENT_PCT: Final = 10.0
_TOLERANCE: Final = 1e-9  # genau 10 % aktiviert trotz Rundung der Gleitkommazahlen

Basis = Literal["shadow", "backtest", "none"]
Reason = Literal["better", "not_better", "too_few_days", "disabled", "no_model"]


def _percent(value: float) -> int:
    """Auf ganze Prozent gerundet (kaufmännisch)."""
    return math.floor(value + 0.5)


@dataclass(frozen=True)
class Activation:
    active: bool
    basis: Basis
    days: int
    model_mae_w: float | None
    profile_mae_w: float | None
    improvement_pct: float | None
    reason: Reason

    def text(self) -> str:
        """Der Grund in einem deutschen Satz für die Anzeige."""
        if self.reason == "better":
            basis = "im Schattenbetrieb" if self.basis == "shadow" else "im Backtest"
            pct = _percent(self.improvement_pct or 0.0)
            return (
                f"Modell aktiv: in den letzten {ACTIVATION_DAYS} Tagen "
                f"{pct} % genauer als das Profil ({basis})."
            )
        if self.reason == "not_better":
            improvement = self.improvement_pct
            if improvement is None or improvement >= 0.0:
                # Knapp verfehlt wird abgerundet, damit nie „nur 10 %“ bei nötigen 10 % dasteht.
                pct = math.floor(improvement + _TOLERANCE) if improvement is not None else 0
            else:
                pct = _percent(improvement)
            if pct < 0:
                return f"Profil aktiv: Das Modell ist {-pct} % ungenauer als das Profil."
            return (
                f"Profil aktiv: Das Modell ist nur {pct} % genauer, "
                f"nötig sind {_percent(MIN_IMPROVEMENT_PCT)} %."
            )
        if self.reason == "too_few_days":
            return (
                "Profil aktiv: Für den Vergleich fehlen noch Tage "
                f"({self.days} von {MIN_SHADOW_DAYS})."
            )
        if self.reason == "disabled":
            return "Profil aktiv: Das Lastmodell ist in den Einstellungen ausgeschaltet."
        return "Profil aktiv: Es gibt noch kein brauchbares Modell."

    def as_json(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "basis": self.basis,
            "days": self.days,
            "model_mae_w": self.model_mae_w,
            "profile_mae_w": self.profile_mae_w,
            "improvement_pct": self.improvement_pct,
            "reason": self.reason,
            "text": self.text(),
        }


def _latest(records: Sequence[ForecastRecord]) -> dict[tuple[datetime, HorizonClass], float]:
    """Je (Slot, Vorlaufklasse) der Wert der jüngsten Ausgabe."""
    newest: dict[tuple[datetime, HorizonClass], ForecastRecord] = {}
    for record in records:
        key = (record.slot_start, record.horizon)
        current = newest.get(key)
        if current is None or record.issued_at > current.issued_at:
            newest[key] = record
    return {key: record.p50_w for key, record in newest.items()}


def shadow_sums(
    model: Sequence[ForecastRecord],
    profile: Sequence[ForecastRecord],
    actual: Mapping[datetime, Actual],
) -> dict[date, DaySums]:
    """Absolutfehler je lokalem Tag aus den archivierten Ausgaben von Modell und Profil.

    Gezählt werden nur Paare mit gleichem Slot und gleicher Vorlaufklasse, zu denen ein Ist-Wert
    vorliegt (Ist = Hausverbrauch). Gibt es mehrere Ausgaben je Slot und Vorlaufklasse, gilt die
    jüngste.
    """
    model_by_key = _latest(model)
    profile_by_key = _latest(profile)
    count: defaultdict[date, int] = defaultdict(int)
    model_abs: defaultdict[date, float] = defaultdict(float)
    profile_abs: defaultdict[date, float] = defaultdict(float)
    for key, model_w in model_by_key.items():
        profile_w = profile_by_key.get(key)
        slot_actual = actual.get(key[0])
        if profile_w is None or slot_actual is None:
            continue
        day = key[0].astimezone(LOCAL_TZ).date()
        count[day] += 1
        model_abs[day] += abs(model_w - slot_actual.house_w)
        profile_abs[day] += abs(profile_w - slot_actual.house_w)
    return {day: DaySums(count[day], model_abs[day], profile_abs[day]) for day in count}


@dataclass(frozen=True)
class _Window:
    days: int
    model_mae_w: float
    profile_mae_w: float


def _window(sums: Mapping[date, DaySums], today: date) -> _Window | None:
    """Summen über die vollen Tage ``[heute − 14, heute − 1]`` mit Werten; ``None`` ohne Werte."""
    first = today - timedelta(days=ACTIVATION_DAYS)
    last = today - timedelta(days=1)
    used = [s for day, s in sums.items() if first <= day <= last and s.n > 0]
    n = sum(s.n for s in used)
    if n == 0:
        return None
    return _Window(
        len(used),
        sum(s.model_abs_w for s in used) / n,
        sum(s.profile_abs_w for s in used) / n,
    )


def decide(
    shadow: Mapping[date, DaySums],
    backtest: Mapping[date, DaySums] | None,
    *,
    enabled: bool,
    has_model: bool,
    today: date,
) -> Activation:
    """Entscheidet, ob das Modell aktiv ist.

    Die Grundlage ist der Schattenbetrieb ab ``MIN_SHADOW_DAYS`` Tagen mit Werten, sonst der
    Backtest unter derselben Bedingung, sonst keine. Ausgeschaltet oder ohne Modell ist das Profil
    aktiv; die Zahlen der Grundlage werden trotzdem gefüllt, damit die Anzeige sie zeigen kann.
    """
    shadow_window = _window(shadow, today)
    backtest_window = _window(backtest, today) if backtest is not None else None
    basis: Basis
    window: _Window | None
    if shadow_window is not None and shadow_window.days >= MIN_SHADOW_DAYS:
        basis, window = "shadow", shadow_window
    elif backtest_window is not None and backtest_window.days >= MIN_SHADOW_DAYS:
        basis, window = "backtest", backtest_window
    else:
        basis, window = "none", None

    shadow_days = shadow_window.days if shadow_window is not None else 0
    days = window.days if window is not None else shadow_days
    model_mae = window.model_mae_w if window is not None else None
    profile_mae = window.profile_mae_w if window is not None else None
    improvement: float | None = None
    if model_mae is not None and profile_mae:
        improvement = (1.0 - model_mae / profile_mae) * 100.0

    reason: Reason
    if not enabled:
        reason = "disabled"
    elif not has_model:
        reason = "no_model"
    elif window is None:
        reason = "too_few_days"
    elif improvement is not None and improvement >= MIN_IMPROVEMENT_PCT - _TOLERANCE:
        reason = "better"
    else:
        reason = "not_better"
    return Activation(reason == "better", basis, days, model_mae, profile_mae, improvement, reason)

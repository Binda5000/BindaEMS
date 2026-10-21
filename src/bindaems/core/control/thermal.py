"""Klimaanlage: Betriebsart und Strategie (Plan 6a, Entscheidungen 4, 5 und 11).

Die Betriebsart bestimmt das Vorzeichen des Sollwertversatzes; bei ``lueften``, ``aus`` und
``unbekannt`` ist der Versatz 0. Die Strategie rechnet aus den Beobachtungen einen
Leistungswunsch in ganzen Stufen (mehr, 0, weniger); erst ``offset_for`` macht daraus das
Vorzeichen je Betriebsart.

Die Schicht ist rein: Sie liest nur den Snapshot bzw. die übergebene Beobachtung und kennt
keine Uhr; die Zeit kommt als monotone Sekunde in der Beobachtung.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Literal

from bindaems.shared.config import CoeOffsetConfig
from bindaems.shared.domain import Snapshot
from bindaems.shared.plan import ThermalHint

ThermalMode = Literal["heizen", "kuehlen", "lueften", "aus", "unbekannt"]

# Reihenfolge der Vorgabe: Sind mehrere Bits gesetzt, gilt das erste
_ORDER: Final[tuple[tuple[str, ThermalMode], ...]] = (
    ("heating", "heizen"),
    ("venting", "lueften"),
    ("cooling", "kuehlen"),
)


def thermal_mode(snap: Snapshot, name: str) -> ThermalMode:
    """Die Betriebsart des Geräts ``name`` aus ``thermal.<name>.running``, ``.heating`` usw.

    - ``aus``, wenn ``running`` falsch ist
    - sonst, wenn die Anlage läuft, ``heizen``, ``lueften`` oder ``kuehlen`` nach dem ersten
      gesetzten Bit in dieser Reihenfolge (widersprüchliche Bits: das erste gilt)
    - ``unbekannt``, wenn ``running`` unbekannt ist, die Anlage ohne gesetzte Betriebsart läuft
      oder ein Bit vor dem ersten gesetzten unbekannt ist (es könnte vorgehen)

    Ein Zustand zählt nur als Wahrheitswert mit Qualität ``OK``.
    """
    prefix = f"thermal.{name}."
    running = _flag(snap, prefix + "running")
    if running is None:
        return "unbekannt"
    if not running:
        return "aus"
    for signal, mode in _ORDER:
        flag = _flag(snap, prefix + signal)
        if flag is None:
            return "unbekannt"
        if flag:
            return mode
    return "unbekannt"


def _flag(snap: Snapshot, signal: str) -> bool | None:
    value = snap.ok(signal)
    return value if isinstance(value, bool) else None


# Eine Stufe von 1 A: weniger offener Bedarf der Autos zählt nicht (Entscheidung 5.3)
_CARS_UNMET_W: Final = 230.0
# Die Klimaanlage weicht dem Akku erst, wenn er so weit unter der Schwelle liegt.
_BATTERY_MARGIN_PCT: Final = 5.0
# Netzbezug muss so lange ohne Unterbrechung anliegen, bevor eine Stufe zurückgeht (5.6)
_IMPORT_HOLD_S: Final = 300.0
# „Weniger“ gilt nur, solange der Slot keine nennenswerte Einspeisung bringt (5.7)
_LESS_EXPORT_BELOW_W: Final = 100.0


@dataclass(frozen=True)
class ThermalObs:
    """Was die Strategie in einem Zyklus sieht.

    ``now`` ist die monotone Zeit in Sekunden. ``export_w``, ``import_w`` und ``soc_pct`` sind
    ``None``, wenn unbekannt; dann feuern die Regeln, die sie brauchen, nicht.
    """

    now: float
    fresh: bool
    mode: ThermalMode
    degraded: bool
    controllable: bool
    export_w: float | None
    import_w: float | None
    soc_pct: float | None
    soc_gate_pct: float
    hint: ThermalHint
    peak_pressure: bool
    phase_pressure: bool
    unmet_cars_w: float


@dataclass(frozen=True)
class ThermalWish:
    """Leistungswunsch in ganzen Stufen (positiv: mehr Leistung) und sein Grund (Code)."""

    steps: int
    reason: str


_INITIAL: Final = ThermalWish(0, "thermal_off")


def offset_for(wish: ThermalWish, mode: ThermalMode, step_k: float) -> float:
    """Der Sollwertversatz in K: ``Stufen · step_k``, beim Kühlen negativ, beim Heizen positiv.

    Bei ``lueften``, ``aus`` und ``unbekannt`` ist er 0.
    """
    if wish.steps == 0 or mode not in ("heizen", "kuehlen"):
        return 0.0
    sign = 1.0 if mode == "heizen" else -1.0
    return wish.steps * step_k * sign


class ThermalStrategy:
    """Regelbasierte Strategie in der Rangfolge der Entscheidung 5; höhere Regeln gewinnen.

    Je Zyklus handelt höchstens eine Regel:

    1. sofort 0 (Werte nicht frisch, nicht steuerbar, DEGRADED, Betriebsart aus/lüften/unbekannt
       oder gewechselt)
    2. Spitzen- oder Phasendruck: ein positiver Wunsch fällt auf 0, ein negativer bleibt
    3. Autos haben Vorrang: eine Stufe zurück, sofort und dann je Intervall
    4. Der Akku hat Vorrang: wie 3, mit dem Hinweis ``mehr`` entfällt die Schwelle
    5. mehr: Einspeisung über ``more_above_w`` seit einem Intervall
    6. zurück: Netzbezug über ``import_above_w`` seit 300 s bei positivem Wunsch
    7. weniger: Hinweis ``weniger`` ohne Einspeisung; endet der Hinweis, sofort 0
    8. sonst bleibt der Wunsch stehen (Totband)

    Eine höhere Regel geht nur vor, wenn sie handelt. Wartet sie nur (Autos, Akku, Netzbezug),
    darf eine tiefere Regel, die den Betrag senkt, trotzdem handeln; neue positive Stufen sperren
    Autos, Akku-Schwelle und Druck unabhängig davon. Endet der Hinweis ``weniger``, geht ein
    negativer Wunsch noch vor Regel 5 auf 0.

    Eine Erhöhung des Betrags (in beide Richtungen) braucht ``step_interval_s`` seit der letzten
    Erhöhung; Verringerungen gehen sofort. Die Zeiten sind die monotone Sekunde des ersten
    Zyklus, in dem die Bedingung galt, und ``None``, sobald sie unterbrochen wurde.
    """

    def __init__(self, cfg: CoeOffsetConfig) -> None:
        self._interval = cfg.step_interval_s
        self._max_steps = round(cfg.max_offset_k / cfg.step_k)
        self._less_max_steps = cfg.strategy.less_max_steps
        self._more_above_w = cfg.strategy.more_above_w
        self._import_above_w = cfg.strategy.import_above_w
        self._wish = _INITIAL
        self._mode: ThermalMode | None = None
        self._last_increase: float | None = None
        self._export_since: float | None = None
        self._import_since: float | None = None
        self._less_since: float | None = None
        self._cars_back_at: float | None = None
        self._battery_back_at: float | None = None

    @property
    def wish(self) -> ThermalWish:
        """Der Wunsch des letzten ``step``; vor dem ersten und nach ``reset`` 0."""
        return self._wish

    def reset(self) -> None:
        """Zurück in den Anfangszustand: Wunsch 0 und keine laufende Zeit."""
        self._wish = _INITIAL
        self._mode = None
        self._last_increase = None
        self._clear_timers()

    def step(self, obs: ThermalObs) -> ThermalWish:
        """Ein Zyklus: der Wunsch nach der ersten Regel, die greift, sonst der alte."""
        previous_mode, self._mode = self._mode, obs.mode
        reset_reason = _reset_reason(obs, previous_mode)
        if reset_reason is not None:
            return self._zero(reset_reason)
        pressure = obs.peak_pressure or obs.phase_pressure
        if pressure and self._wish.steps > 0:
            return self._zero("thermal_peak")
        self._track(obs)
        # Druck, offener Wagenbedarf und ein Akku unter der Schwelle sperren neue positive Stufen
        allow_up = not pressure and not _cars_unmet(obs) and _gate_met(obs)
        for rule in (
            self._rule_cars,
            self._rule_battery,
            self._rule_less_end,
            self._rule_more,
            self._rule_import,
            self._rule_less,
        ):
            if rule(obs, allow_up):
                break
        return self._wish

    def _zero(self, reason: str) -> ThermalWish:
        self._wish = ThermalWish(0, reason)
        self._clear_timers()
        return self._wish

    def _clear_timers(self) -> None:
        self._export_since = None
        self._import_since = None
        self._less_since = None
        self._cars_back_at = None
        self._battery_back_at = None

    def _track(self, obs: ThermalObs) -> None:
        """Die laufenden Zeiten fortschreiben: setzen beim ersten Zyklus, löschen bei Abbruch."""
        now = obs.now
        positive = self._wish.steps > 0
        export, imported = obs.export_w, obs.import_w
        self._export_since = _since(
            self._export_since, now, export is not None and export >= self._more_above_w
        )
        self._import_since = _since(
            self._import_since,
            now,
            positive and imported is not None and imported >= self._import_above_w,
        )
        self._less_since = _since(
            self._less_since,
            now,
            obs.hint == "weniger" and export is not None and export < _LESS_EXPORT_BELOW_W,
        )
        if not (positive and _cars_unmet(obs)):
            self._cars_back_at = None
        if not (positive and _battery_low(obs)):
            self._battery_back_at = None

    def _may_move(self, target: int, now: float) -> bool:
        """Eine Erhöhung des Betrags (in beide Richtungen) braucht das Intervall seit der letzten.

        Die laufenden Zeiten der Regeln 5 und 7 sichern das ohnehin; die Prüfung ist die zweite
        Sicherung neben der Schrittregel der Safety-Schicht.
        """
        return abs(target) <= abs(self._wish.steps) or _elapsed(
            self._last_increase, now, self._interval
        )

    def _move(self, steps: int, reason: str, now: float) -> None:
        if abs(steps) > abs(self._wish.steps):
            self._last_increase = now
        self._wish = ThermalWish(steps, reason)

    def _rule_cars(self, obs: ThermalObs, allow_up: bool) -> bool:
        if self._wish.steps <= 0 or not _cars_unmet(obs):
            return False
        if not _elapsed(self._cars_back_at, obs.now, self._interval):
            return False
        self._cars_back_at = obs.now
        self._move(self._wish.steps - 1, "thermal_cars", obs.now)
        return True

    def _rule_battery(self, obs: ThermalObs, allow_up: bool) -> bool:
        if self._wish.steps <= 0 or not _battery_low(obs):
            return False
        if not _elapsed(self._battery_back_at, obs.now, self._interval):
            return False
        self._battery_back_at = obs.now
        self._move(self._wish.steps - 1, "thermal_battery", obs.now)
        return True

    def _rule_more(self, obs: ThermalObs, allow_up: bool) -> bool:
        steps = self._wish.steps
        if not _elapsed_since(self._export_since, obs.now, self._interval):
            return False
        if steps >= self._max_steps:
            return False
        target = steps + 1
        if (target > 0 and not allow_up) or not self._may_move(target, obs.now):
            return False
        self._export_since = obs.now
        self._move(target, "thermal_more", obs.now)
        return True

    def _rule_import(self, obs: ThermalObs, allow_up: bool) -> bool:
        # ``_import_since`` gilt nur bei positivem Wunsch (siehe ``_track``)
        if not _elapsed_since(self._import_since, obs.now, _IMPORT_HOLD_S):
            return False
        self._import_since = obs.now
        self._move(self._wish.steps - 1, "thermal_back", obs.now)
        return True

    def _rule_less(self, obs: ThermalObs, allow_up: bool) -> bool:
        steps = self._wish.steps
        if not _elapsed_since(self._less_since, obs.now, self._interval):
            return False
        target = steps - 1
        if steps <= -self._less_max_steps or not self._may_move(target, obs.now):
            return False
        self._less_since = obs.now
        self._move(target, "thermal_less", obs.now)
        return True

    def _rule_less_end(self, obs: ThermalObs, allow_up: bool) -> bool:
        # Ein negativer Wunsch stammt nur aus „weniger“; ohne den Hinweis geht er sofort auf 0.
        # Der Grund bleibt ``thermal_less``: keiner der zehn Codes sagt „Hinweis zu Ende“ besser.
        if self._wish.steps >= 0 or obs.hint == "weniger":
            return False
        self._wish = ThermalWish(0, "thermal_less")
        # Die Einspeisung muss danach die volle Wartezeit anstehen: sonst folgte im nächsten
        # Zyklus gleich +1 (−2 → 0 → +1).
        self._export_since = obs.now
        return True


def _reset_reason(obs: ThermalObs, previous_mode: ThermalMode | None) -> str | None:
    """Der Grund für „sofort 0“ (Regel 1) oder ``None``."""
    if not obs.fresh:
        return "thermal_stale"
    if not obs.controllable:
        return "thermal_no_echo"
    if obs.degraded or obs.mode not in ("heizen", "kuehlen"):
        return "thermal_off"
    if previous_mode is not None and previous_mode != obs.mode:
        return "thermal_mode"
    return None


def _cars_unmet(obs: ThermalObs) -> bool:
    return obs.unmet_cars_w >= _CARS_UNMET_W


def _battery_low(obs: ThermalObs) -> bool:
    return (
        obs.hint != "mehr"
        and obs.soc_pct is not None
        and obs.soc_pct < obs.soc_gate_pct - _BATTERY_MARGIN_PCT
    )


def _gate_met(obs: ThermalObs) -> bool:
    """Ein neuer positiver Schritt braucht SOC ≥ Schwelle; der Hinweis ``mehr`` ersetzt das."""
    return obs.hint == "mehr" or (obs.soc_pct is not None and obs.soc_pct >= obs.soc_gate_pct)


def _since(since: float | None, now: float, holds: bool) -> float | None:
    if not holds:
        return None
    return now if since is None else since


def _elapsed(since: float | None, now: float, period: float) -> bool:
    """``True``, wenn es keinen Bezugszeitpunkt gibt oder ``period`` seitdem vergangen ist."""
    return since is None or now - since >= period


def _elapsed_since(since: float | None, now: float, period: float) -> bool:
    """``True``, wenn die Bedingung läuft und ``period`` seit ihrem Beginn vergangen ist."""
    return since is not None and now - since >= period

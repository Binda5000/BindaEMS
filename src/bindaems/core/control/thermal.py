"""Klimaanlage: Betriebsart und Strategie (Plan 6a, Entscheidungen 4, 5 und 11; Plan 5c,
Entscheidung 9).

Die Betriebsart bestimmt das Vorzeichen des Sollwertversatzes; bei ``lueften``, ``aus`` und
``unbekannt`` ist der Versatz 0. Die Strategie rechnet aus den Beobachtungen einen
Leistungswunsch in ganzen Stufen (mehr, 0, weniger); erst ``offset_for`` macht daraus das
Vorzeichen je Betriebsart. Nennt der Plan einen Planwert (Vorheizen und Vorkühlen, Plan 5c),
folgt sie ihm; ohne Planwert ist sie die aus 6a.

Die Schicht ist rein: Sie liest nur den Snapshot bzw. die übergebene Beobachtung und kennt
keine Uhr; die Zeit kommt als monotone Sekunde in der Beobachtung.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Final

from bindaems.shared.config import CoeOffsetConfig
from bindaems.shared.domain import Snapshot

# ``ThermalMode`` steht in shared.plan und bleibt hier importierbar (regulation, Tests)
from bindaems.shared.plan import ThermalHint
from bindaems.shared.plan import ThermalMode as ThermalMode

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
# Akku-Wächter für geplante Stufen (Plan 5c, Entscheidung 9): Entlädt der Akku mindestens so viel
# so lange ohne Unterbrechung, geht ein positiver Wunsch eine Stufe zurück; solange er so viel
# entlädt, steigt der Plan nicht.
DISCHARGE_GUARD_W: Final = 300.0
DISCHARGE_GUARD_S: Final = 300.0


@dataclass(frozen=True)
class ThermalObs:
    """Was die Strategie in einem Zyklus sieht.

    ``now`` ist die monotone Zeit in Sekunden. ``export_w``, ``import_w`` und ``soc_pct`` sind
    ``None``, wenn unbekannt; dann feuern die Regeln, die sie brauchen, nicht.

    ``planned_steps`` ist der Planwert des gültigen Slots in Stufen (positiv: mehr Leistung),
    ``None`` ohne Planwert (dann gilt 6a); ``planned_grid_w`` der Netzbezug, den der Plan dafür
    vorsieht, und ``battery_discharge_w`` die Entladeleistung des Akkus in W (0 beim Laden,
    ``None`` unbekannt). Beide wirken nur mit Planwert.
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
    planned_steps: int | None = None
    planned_grid_w: float = 0.0
    battery_discharge_w: float | None = None


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

    **Mit Planwert** (``planned_steps``, Plan 5c, Entscheidung 9) gilt diese Rangfolge:

    1. und 2. wie oben
    P↓. Plan nach unten: Liegt der Wunsch über dem Planwert (und den Stufen aus dem Überschuss
       über ``max(Planwert, 0)``, Regel 5), folgt er sofort; ein Vorzeichenwechsel geht über 0,
       und eine Erhöhung des Betrags ins Negative braucht das Intervall seit der letzten (die
       Schrittregel der Safety-Schicht; ging eine Erhöhung später hinaus, zählt
       ``note_raised``). Die Regel steht vor 3 und 4, die nur eine Stufe zurückgehen.
    3. wie oben
    4. Bei einem Planwert über 0 statt der SOC-Schwelle der Akku-Wächter: Entlädt der Akku
       ``DISCHARGE_GUARD_W`` seit ``DISCHARGE_GUARD_S`` ohne Unterbrechung, geht ein positiver
       Wunsch eine Stufe zurück, und so je weitere ``DISCHARGE_GUARD_S``. Bei einem Planwert von
       höchstens 0 gilt Regel 4 wie oben.
    P↑. Plan nach oben (mehr Leistung): ein negativer Wunsch sofort bis 0, ab 0 eine Stufe je
       Intervall. Eine neue positive Stufe des Plans braucht: kein Druck, kein offener
       Wagenbedarf und einen Akku mit bekannter Leistung, der weniger als
       ``DISCHARGE_GUARD_W`` entlädt; die SOC-Schwelle gilt für geplante Stufen nicht.
    5. mehr wie oben, auch über den Planwert hinaus, aber erst ab ``max(Planwert, 0)`` und bei
       einem positiven Planwert nicht, solange der Akku ``DISCHARGE_GUARD_W`` entlädt. Solche
       Stufen bleiben über ``max(Planwert, 0)``, auch wenn er sich ändert; Stufen aus 6a vor
       einem positiven Planwert zählen ebenso, vor einem Planwert von höchstens 0 nicht.
    6. zurück: Netzbezug über ``import_above_w + planned_grid_w``; nur Stufen über
       ``max(Planwert, 0)`` gehen so zurück (Regel 6 steht unter dem Plan).
    7. und „weniger endet“ entfallen: Der Hinweis ``weniger`` wirkt nicht, solange es einen
       Planwert gibt.

    Endet der Planwert (abgelaufen, keine Absicht, andere Betriebsart), gehen geplante positive
    Stufen im selben Zyklus auf 0 (Grund ``thermal_plan``, nach den Regeln 1 und 2); nur die
    Stufen aus dem Überschuss bleiben, und es gilt 6a.
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
        self._discharge_since: float | None = None
        # Planwert des letzten Zyklus und die Stufen aus dem Überschuss über ihm (ohne Plan 0)
        self._planned: int | None = None
        self._above = 0

    @property
    def wish(self) -> ThermalWish:
        """Der Wunsch des letzten ``step``; vor dem ersten und nach ``reset`` 0."""
        return self._wish

    @property
    def planned(self) -> int | None:
        """Der Planwert des letzten ``step``, auf ``±max_offset_k`` begrenzt; ``None`` ohne."""
        return self._planned

    def note_raised(self, now: float) -> None:
        """Eine Erhöhung des Betrags ging erst zur monotonen Sekunde ``now`` hinaus (etwa nach
        einem offenen Schreibvorgang): Die Wartezeit der nächsten zählt ab dort, wie die der
        Schrittregel der Safety-Schicht. Ein früherer Zeitpunkt ändert nichts."""
        if self._last_increase is None or now > self._last_increase:
            self._last_increase = now

    def reset(self) -> None:
        """Zurück in den Anfangszustand: Wunsch 0 und keine laufende Zeit."""
        self._wish = _INITIAL
        self._mode = None
        self._last_increase = None
        self._planned = None
        self._above = 0
        self._clear_timers()

    def step(self, obs: ThermalObs) -> ThermalWish:
        """Ein Zyklus: der Wunsch nach der ersten Regel, die greift, sonst der alte."""
        previous_mode, self._mode = self._mode, obs.mode
        planned, kept = self._take_plan(obs)
        reset_reason = _reset_reason(obs, previous_mode)
        if reset_reason is not None:
            return self._zero(reset_reason)
        pressure = obs.peak_pressure or obs.phase_pressure
        if pressure and self._wish.steps > 0:
            return self._zero("thermal_peak")
        self._track(obs)
        if kept is not None and self._wish.steps > kept:
            # Ende des Planwerts: geplante Stufen sofort auf 0, nur die aus dem Überschuss bleiben
            # (Entscheidung 8: kein Vorheizen aus dem Akku, bis die SOC-Schwelle greift)
            self._move(kept, "thermal_plan", obs.now)
            return self._wish
        # Druck, offener Wagenbedarf und ein Akku unter der Schwelle sperren neue positive Stufen
        allow_up = not pressure and not _cars_unmet(obs) and _gate_met(obs)
        if planned is None:
            rules = (
                self._rule_cars,
                self._rule_battery,
                self._rule_less_end,
                self._rule_more,
                self._rule_import,
                self._rule_less,
            )
        else:
            rules = (
                partial(self._rule_plan_down, planned=planned),
                self._rule_cars,
                self._rule_battery_guard if planned > 0 else self._rule_battery,
                partial(self._rule_plan_up, planned=planned),
                self._rule_more,
                self._rule_import,
            )
        for rule in rules:
            if rule(obs, allow_up):
                break
        if planned is not None:
            self._above = max(0, min(self._above, self._wish.steps - max(planned, 0)))
        return self._wish

    def _take_plan(self, obs: ThermalObs) -> tuple[int | None, int | None]:
        """Der Planwert dieses Zyklus, auf ``±max_offset_k`` begrenzt, und die Stufen aus dem
        Überschuss; endet der Planwert in diesem Zyklus, dazu diese Stufen (sonst ``None``).

        Stufen aus dem Überschuss zählen nur über ``max(Planwert, 0)``. Beginnt ein positiver
        Planwert, zählen die Stufen des Wunschs über ihm als solche (der Wunsch stammt aus 6a);
        bei einem Planwert von höchstens 0 keine: Dann holt der Plan auch positive Stufen aus 6a
        zurück. Ohne Planwert gibt es keine.
        """
        planned = obs.planned_steps
        kept = None
        if planned is not None:
            planned = max(-self._max_steps, min(self._max_steps, planned))
            if self._planned is None:
                self._above = max(0, self._wish.steps - planned) if planned > 0 else 0
        else:
            if self._planned is not None:
                kept = self._above
            self._above = 0
        self._planned = planned
        return planned, kept

    def _zero(self, reason: str) -> ThermalWish:
        self._wish = ThermalWish(0, reason)
        self._above = 0
        self._clear_timers()
        return self._wish

    def _clear_timers(self) -> None:
        self._export_since = None
        self._import_since = None
        self._less_since = None
        self._cars_back_at = None
        self._battery_back_at = None
        self._discharge_since = None

    def _track(self, obs: ThermalObs) -> None:
        """Die laufenden Zeiten fortschreiben: setzen beim ersten Zyklus, löschen bei Abbruch."""
        now = obs.now
        positive = self._wish.steps > 0
        export, imported = obs.export_w, obs.import_w
        self._export_since = _since(
            self._export_since, now, export is not None and export >= self._more_above_w
        )
        import_above_w = self._import_above_w
        if obs.planned_steps is not None:
            import_above_w += obs.planned_grid_w
        self._import_since = _since(
            self._import_since,
            now,
            positive and imported is not None and imported >= import_above_w,
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
        guarded = positive and self._planned is not None and self._planned > 0
        self._discharge_since = _since(self._discharge_since, now, guarded and _discharging(obs))

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

    def _rule_battery_guard(self, obs: ThermalObs, allow_up: bool) -> bool:
        # ``_discharge_since`` gilt nur bei positivem Wunsch und positivem Planwert (``_track``)
        if not _elapsed_since(self._discharge_since, obs.now, DISCHARGE_GUARD_S):
            return False
        self._discharge_since = obs.now
        self._move(self._wish.steps - 1, "thermal_battery", obs.now)
        return True

    def _rule_plan_down(self, obs: ThermalObs, allow_up: bool, *, planned: int) -> bool:
        """Regel „Plan“ nach unten (weniger Leistung): Liegt der Wunsch über dem Planwert und den
        Stufen aus dem Überschuss, ein positiver sofort bis dorthin, höchstens bis 0 (ein
        Vorzeichenwechsel geht über 0); ein Wunsch von höchstens 0 eine Stufe je Intervall."""
        steps, now = self._wish.steps, obs.now
        ceiling = planned if self._above == 0 else max(planned, 0) + self._above
        ceiling = min(self._max_steps, ceiling)
        if steps <= ceiling:
            return False
        target = max(ceiling, 0) if steps > 0 else steps - 1
        if not self._may_move(target, now):
            return False
        self._move(target, "thermal_plan", now)
        return True

    def _rule_plan_up(self, obs: ThermalObs, allow_up: bool, *, planned: int) -> bool:
        """Regel „Plan“ nach oben (mehr Leistung): ein negativer Wunsch sofort bis 0 (höchstens
        bis zum Planwert), ab 0 eine Stufe je Intervall, wenn nichts sie sperrt
        (``_plan_may_rise``)."""
        steps, now = self._wish.steps, obs.now
        if steps >= planned:
            return False
        if steps < 0:
            target = min(planned, 0)
        elif _plan_may_rise(obs):
            target = steps + 1
        else:
            return False
        if not self._may_move(target, now):
            return False
        self._move(target, "thermal_plan", now)
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
        planned = self._planned
        if planned is not None and not _surplus_may_rise(obs, steps, planned):
            return False
        self._export_since = obs.now
        self._move(target, "thermal_more", obs.now)
        if planned is not None:
            self._above = max(0, target - max(planned, 0))
        return True

    def _rule_import(self, obs: ThermalObs, allow_up: bool) -> bool:
        # ``_import_since`` gilt nur bei positivem Wunsch (siehe ``_track``); mit Planwert gehen
        # nur Stufen über ihm zurück
        if not _elapsed_since(self._import_since, obs.now, _IMPORT_HOLD_S):
            return False
        if self._planned is not None and self._wish.steps <= max(self._planned, 0):
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


def _discharging(obs: ThermalObs) -> bool:
    """Entlädt der Akku mindestens ``DISCHARGE_GUARD_W``? Unbekannt zählt nicht."""
    discharge = obs.battery_discharge_w
    return discharge is not None and discharge >= DISCHARGE_GUARD_W


def _plan_may_rise(obs: ThermalObs) -> bool:
    """Eine neue positive Stufe des Plans: kein Druck, kein offener Wagenbedarf und ein Akku mit
    bekannter Leistung, der weniger als ``DISCHARGE_GUARD_W`` entlädt; die SOC-Schwelle gilt für
    geplante Stufen nicht."""
    pressure = obs.peak_pressure or obs.phase_pressure
    known = obs.battery_discharge_w is not None
    return not pressure and not _cars_unmet(obs) and known and not _discharging(obs)


def _surplus_may_rise(obs: ThermalObs, steps: int, planned: int) -> bool:
    """Regel 5 mit Planwert: Stufen aus dem Überschuss nur ab ``max(Planwert, 0)`` (darunter
    gehört der Bereich dem Plan) und bei einem positiven Planwert nicht, solange der Akku
    ``DISCHARGE_GUARD_W`` entlädt."""
    if steps < max(planned, 0):
        return False
    return not (planned > 0 and _discharging(obs))


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

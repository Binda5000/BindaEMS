"""Prüfung der Phasenzuordnung: passen die Phasen des Multi zu denen des Netzzählers?

Je Netzphase ``p`` und Zuordnung ``sigma`` (Netzphase → Multi-Phase) ist der Verbrauch
``c_sigma(p) = Netz_p − AC-in_sigma(p) + AC-out_sigma(p) + PV_p``, wie ``derive`` ihn für die
Zuordnung 1:1 rechnet. Eine Zuordnung passt, wenn kein Verbrauch unter ``−TOL_W`` liegt, und ist
widerlegt, wenn einer unter ``−NEG_W`` liegt. Ein Zyklus ist entscheidend, wenn mindestens eine
Zuordnung widerlegt ist; er stimmt „falsch“, wenn die Zuordnung 1:1 widerlegt ist.
Die Prüfung greift nicht ein.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from itertools import permutations

from bindaems.core.state.derived import PHASES, Derived, _n, _total, number, pv_ids
from bindaems.shared.config import Config, EvcsConfig, Phase
from bindaems.shared.domain import Alarm, Severity, Snapshot

NEG_W = 500.0
TOL_W = 200.0
DECISIVE_SAMPLES = 300
ON_SHARE = 0.8
OFF_SHARE = 0.2

Permutation = tuple[Phase, Phase, Phase]
"""``sigma[i]`` ist die Multi-Phase auf der Netzphase ``PHASES[i]``."""

IDENTITY: Permutation = ("L1", "L2", "L3")
PERMUTATIONS: tuple[Permutation, ...] = (
    IDENTITY,
    ("L1", "L3", "L2"),
    ("L2", "L1", "L3"),
    ("L2", "L3", "L1"),
    ("L3", "L1", "L2"),
    ("L3", "L2", "L1"),
)


@dataclass(frozen=True)
class PhaseSample:
    """Messwerte eines Zyklus je Phase: Netz, Multi (AC-in, AC-out) und PV."""

    grid_w: Mapping[Phase, float]
    ac_in_w: Mapping[Phase, float]
    ac_out_w: Mapping[Phase, float]
    pv_w: Mapping[Phase, float]


def phase_sample(snap: Snapshot, cfg: Config) -> PhaseSample | None:
    """Messwerte je Phase aus dem Snapshot; fehlt einer, gibt es ``None``."""
    pvs = pv_ids(snap, cfg)
    grid: dict[Phase, float] = {}
    ac_in: dict[Phase, float] = {}
    ac_out: dict[Phase, float] = {}
    pv: dict[Phase, float] = {}
    for p in PHASES:
        n = _n(p)
        g = number(snap, f"grid.l{n}.power_w")
        i = number(snap, f"vebus.l{n}.ac_in_power_w")
        o = number(snap, f"vebus.l{n}.ac_out_power_w")
        v = _total(number(snap, f"pv.{pv_id}.l{n}.power_w") for pv_id in pvs)
        if g is None or i is None or o is None or v is None:
            return None
        grid[p], ac_in[p], ac_out[p], pv[p] = g, i, o, v
    return PhaseSample(grid, ac_in, ac_out, pv)


def consumption_w(sample: PhaseSample, sigma: Permutation) -> dict[Phase, float]:
    """Verbrauch je Netzphase, wenn der Multi nach ``sigma`` angeschlossen wäre."""
    result: dict[Phase, float] = {}
    for p, m in zip(PHASES, sigma, strict=True):
        result[p] = (sample.grid_w[p] - sample.ac_in_w[m]) + (sample.ac_out_w[m] + sample.pv_w[p])
    return result


class Votes:
    """Liste der letzten entscheidenden Zyklen mit Schalter und Hysterese.

    Ein: Liste voll und mindestens ``ON_SHARE`` „falsch“. Aus: höchstens ``OFF_SHARE`` „falsch“.
    Dazwischen bleibt der Zustand.
    """

    def __init__(self, size: int = DECISIVE_SAMPLES) -> None:
        self._size = size
        self._votes: deque[bool] = deque(maxlen=size)
        self.active = False

    def add(self, bad: bool) -> None:
        self._votes.append(bad)
        if len(self._votes) < self._size:
            return
        share = sum(self._votes) / self._size
        if share >= ON_SHARE:
            self.active = True
        elif share <= OFF_SHARE:
            self.active = False


class MultiPhaseCheck:
    """Prüft die Phasen des Multi gegen die des Netzzählers (Alarm ``wiring.multi_phases``)."""

    def __init__(self) -> None:
        self._votes = Votes()
        # je entscheidendem Zyklus: passende Zuordnungen, falls „falsch“, sonst None
        self._fitting: deque[tuple[Permutation, ...] | None] = deque(maxlen=DECISIVE_SAMPLES)
        self._text: str | None = None
        self.fits_identity: bool | None = None

    def update(self, sample: PhaseSample | None) -> str | None:
        """Wertet einen Zyklus aus; gibt den Alarmtext zurück, solange der Alarm aktiv ist."""
        if sample is None:
            self.fits_identity = None
            return self._text
        minima = {s: min(consumption_w(sample, s).values()) for s in PERMUTATIONS}
        self.fits_identity = minima[IDENTITY] >= -TOL_W
        if min(minima.values()) < -NEG_W:  # entscheidender Zyklus
            bad = minima[IDENTITY] < -NEG_W
            self._fitting.append(
                tuple(s for s in PERMUTATIONS[1:] if minima[s] >= -TOL_W) if bad else None
            )
            was_active = self._votes.active
            self._votes.add(bad)
            if self._votes.active and not was_active:
                self._text = self._alarm_text(sample)
            elif not self._votes.active:
                self._text = None
        return self._text

    def _alarm_text(self, sample: PhaseSample) -> str:
        c = consumption_w(sample, IDENTITY)
        phase = min(PHASES, key=lambda p: c[p])
        text = (
            "Phasen von Multi und Netzzähler passen nicht zusammen: "
            f"Der Verbrauch auf {phase} wäre rechnerisch {c[phase]:.0f} W. "
        )
        sigma = self._suggestion()
        if sigma is None:
            return text + (
                "Keine andere Zuordnung der Multi-Phasen passt; fehlt eine Erzeugung im Abgleich?"
            )
        parts = [
            f"Multi-{m} am Netz auf {PHASES[sigma.index(m)]}"
            if m == "L1"
            else f"Multi-{m} auf {PHASES[sigma.index(m)]}"
            for m in PHASES
        ]
        return text + "Passend wäre: " + ", ".join(parts) + "."

    def _suggestion(self) -> Permutation | None:
        """Die Zuordnung, die in mindestens ``ON_SHARE`` der „falsch“-Zyklen passt."""
        bad_cycles = [fits for fits in self._fitting if fits is not None]
        counts = {s: sum(s in fits for fits in bad_cycles) for s in PERMUTATIONS[1:]}
        best = max(counts, key=lambda s: counts[s])
        if counts[best] >= ON_SHARE * len(bad_cycles):
            return best
        return None


def _phase_list(phases: tuple[Phase, ...]) -> str:
    """„L2“, „L2 und L3“ oder „L1, L2 und L3“."""
    if len(phases) == 1:
        return phases[0]
    return ", ".join(phases[:-1]) + " und " + phases[-1]


class WallboxPhaseCheck:
    """Prüft die Phasen einer Wallbox gegen den Netzzähler (Alarm ``wiring.wallbox_phases.<name>``).

    Je Phase ist ``h(p) = c_1:1(p) − U · I(p)`` der Verbrauch ohne das Auto. Entscheidend ist ein
    Zyklus nur, wenn es mindestens zwei verschiedene Verteilungen der Ströme auf die Phasen gibt
    und mindestens eine widerlegt ist; „falsch“ ist er, wenn die gemessene Verteilung widerlegt
    ist. Der Multi muss im Zyklus zur Zuordnung 1:1 passen, sonst ist ``c`` nicht verlässlich.
    """

    def __init__(self, name: str, evcs: bool, voltage_v: float) -> None:
        self._name = name
        self._evcs = evcs
        self._voltage_v = voltage_v
        self._votes = Votes()
        # je entscheidendem Zyklus: Phasen mit Strom der passenden Verteilungen, falls „falsch“
        self._fitting: deque[tuple[tuple[Phase, ...], ...] | None] = deque(maxlen=DECISIVE_SAMPLES)
        self._text: str | None = None

    def update(
        self, sample: PhaseSample | None, currents_a: Mapping[Phase, float] | None
    ) -> str | None:
        """Wertet einen Zyklus aus; gibt den Alarmtext zurück, solange der Alarm aktiv ist."""
        if sample is None or currents_a is None:
            return self._text
        c = consumption_w(sample, IDENTITY)
        if min(c.values()) < -TOL_W:  # Multi passt nicht: nichts prüfen
            return self._text
        measured = tuple(currents_a[p] for p in PHASES)
        if not any(i > 0 for i in measured):
            return self._text
        candidates = [measured, *(d for d in set(permutations(measured)) if d != measured)]
        if len(candidates) < 2:
            return self._text  # Strom auf allen Phasen gleich: nichts zu unterscheiden
        minima = {
            d: min(c[p] - self._voltage_v * i for p, i in zip(PHASES, d, strict=True))
            for d in candidates
        }
        if min(minima.values()) >= -NEG_W:
            return self._text  # keine Verteilung widerlegt
        bad = minima[measured] < -NEG_W
        self._fitting.append(
            tuple(self._on_phases(d) for d in candidates[1:] if minima[d] >= -TOL_W)
            if bad
            else None
        )
        was_active = self._votes.active
        self._votes.add(bad)
        if self._votes.active and not was_active:
            self._text = self._alarm_text(self._on_phases(measured))
        elif not self._votes.active:
            self._text = None
        return self._text

    @staticmethod
    def _on_phases(distribution: tuple[float, ...]) -> tuple[Phase, ...]:
        return tuple(p for p, i in zip(PHASES, distribution, strict=True) if i > 0)

    def _alarm_text(self, named: tuple[Phase, ...]) -> str:
        text = f"Phasen der Wallbox „{self._name}“ passen nicht zum Netzzähler: "
        suggestion = self._suggestion()
        if suggestion is None:
            text += f"Das Auto lädt rechnerisch nicht auf {_phase_list(named)}."
        else:
            text += (
                f"Das Auto lädt rechnerisch auf {_phase_list(suggestion)}, "
                f"die Zuordnung nennt {_phase_list(named)}."
            )
        if not self._evcs:
            return (
                text + " Der Wall Connector meldet seine Phasen selbst; lass den Anschluss prüfen."
            )
        if suggestion is None:
            return text
        return (
            text
            + f" Abhilfe: phase_map in config.yaml mit {_phase_list(suggestion)} beginnen lassen."
        )

    def _suggestion(self) -> tuple[Phase, ...] | None:
        """Die Phasen, die in mindestens ``ON_SHARE`` der „falsch“-Zyklen passen."""
        bad_cycles = [fits for fits in self._fitting if fits is not None]
        counts: dict[tuple[Phase, ...], int] = {}
        for fits in bad_cycles:
            for phases in set(fits):
                counts[phases] = counts.get(phases, 0) + 1
        if not counts:
            return None
        best = max(counts, key=lambda phases: counts[phases])
        if counts[best] >= ON_SHARE * len(bad_cycles):
            return best
        return None


class PhaseOrderMonitor:
    """Alarme zur Phasenzuordnung von Multi und Wallboxen; greift nicht ein.

    ``Alarm.since`` bleibt der Zeitpunkt des ersten Zyklus, solange der Alarm aktiv ist.
    """

    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        self._multi = MultiPhaseCheck()
        self._wallboxes = {
            name: WallboxPhaseCheck(
                name,
                evcs=isinstance(wallbox, EvcsConfig),
                voltage_v=cfg.grid.voltage_nominal_v,
            )
            for name, wallbox in cfg.wallboxes.items()
        }
        self._since: dict[str, datetime] = {}

    def evaluate(self, snap: Snapshot, derived: Derived, now: datetime) -> list[Alarm]:
        sample = phase_sample(snap, self._cfg)
        findings: dict[str, str] = {}
        multi_text = self._multi.update(sample)
        if multi_text is not None:
            findings["wiring.multi_phases"] = multi_text
        for name, check in self._wallboxes.items():
            # solange der Multi nicht passt, wäre ein Wallbox-Alarm ein Folgefehler
            currents = derived.wallbox_phase_a.get(name) if multi_text is None else None
            text = check.update(sample, currents)
            if text is not None:
                findings[f"wiring.wallbox_phases.{name}"] = text
        for alarm_id in set(self._since) - set(findings):
            del self._since[alarm_id]
        return [
            Alarm(alarm_id, Severity.WARNING, message, self._since.setdefault(alarm_id, now))
            for alarm_id, message in findings.items()
        ]

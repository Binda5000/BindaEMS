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

from bindaems.core.state.derived import PHASES, _n, _total, number, pv_ids
from bindaems.shared.config import Config, Phase
from bindaems.shared.domain import Snapshot

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

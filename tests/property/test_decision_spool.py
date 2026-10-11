"""Eigenschaft von ``DecisionSpool.read_since``: Seitenweises Lesen verliert und doppelt nichts.

Alle Entscheidungen eines Zyklus tragen dieselbe Zeit. Wer mit ``since`` = letzter Zeit weiterliest,
bekommt trotzdem jede Entscheidung genau einmal und in der Reihenfolge der Zeit (bei gleicher Zeit
in der Reihenfolge des Anhängens), für jedes Limit und jede Verteilung gleicher Zeiten über mehrere
Tage. Jeder Fall hat sein eigenes temporäres Verzeichnis, denn hypothesis verträgt keine
funktionsbezogenen Fixtures wie ``tmp_path``.
"""

import tempfile
from datetime import timedelta
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st
from tests.helpers import T0
from tests.unit.shared.test_decisions import decision

from bindaems.core.persistence.decision_spool import DecisionSpool

STEP = timedelta(hours=8)  # drei Schritte je Tag: gleiche Zeiten gibt es innerhalb eines Tages


@given(
    steps=st.lists(st.integers(min_value=0, max_value=11), max_size=30),
    limit=st.integers(min_value=1, max_value=8),
)
@settings(max_examples=150, deadline=None)
def test_paging_returns_every_decision_exactly_once_in_order(steps: list[int], limit: int) -> None:
    with tempfile.TemporaryDirectory() as directory:
        spool = DecisionSpool(Path(directory))
        for index, step in enumerate(steps):  # in beliebiger Reihenfolge angehängt
            assert spool.append(decision(id=f"d{index}", ts=T0 + step * STEP))
        expected = [f"d{index}" for index in sorted(range(len(steps)), key=lambda i: steps[i])]

        seen: list[str] = []
        since = T0 - timedelta(seconds=1)
        for _ in range(len(steps) + 1):  # jede Seite bringt mindestens eine Entscheidung weiter
            page, more = spool.read_since(since, limit)
            seen.extend(d.id for d in page)
            if not more:
                break
            assert page, "mehr da, aber eine leere Seite: der Abruf käme nie weiter"
            assert len(page) >= limit
            since = page[-1].ts
        else:
            raise AssertionError("das seitenweise Lesen endet nicht")
        assert seen == expected

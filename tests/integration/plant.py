"""Simulierte Anlage für die Laufzeit-Tests (Plan 2b): Watchdog am Cerbo und EVCS „evcs“."""

from __future__ import annotations

from typing import Final

from bindaems.core.adapters.victron_mqtt import HEARTBEAT_PATH
from bindaems.core.state.store import StateStore
from bindaems.core.watchdog_client.status import WATCHDOG_VERSION
from bindaems.shared.domain import SignalKind, Value

SOURCE: Final = "victron"
TIMEOUT_S: Final = 90
# Schreibpfade der EVCS „evcs“ (Instanz 40 der Beispielkonfiguration) und die GX-Werte dazu
FOLLOWED: Final = {
    "evcharger/40/SetCurrent": "wallbox.evcs.gx_set_current_a",
    "evcharger/40/StartStop": "wallbox.evcs.gx_start_stop",
}


class Plant:
    """Simulierte Anlage für die Laufzeit-Tests (Plan 2b).

    ``write`` ist der MQTT-Schreibweg des core. Vor jedem Zyklus (``before_cycle``) meldet sie den
    Watchdog (``watchdog_state``, Echo des zuletzt geschriebenen Heartbeats, ``timeout_s``,
    Version ``WATCHDOG_VERSION``) und übernimmt geschriebene EVCS-Werte als GX-Werte, solange
    ``follow`` gilt.

    ``echo`` ist das Echo, bevor der core einen Heartbeat geschrieben hat, etwa der Zähler eines
    früheren Laufs; ``None`` heißt, der Watchdog meldet keins, ebenso bei ``timeout_s``. Ohne
    ``accept`` nimmt der Cerbo keinen EVCS-Auftrag an. Die Signale schreibt sie mit der Quelle
    ``victron``. ``writes`` hält alle Schreibaufträge in ihrer Reihenfolge, auch die abgelehnten.
    """

    def __init__(
        self,
        *,
        watchdog_state: int = 0,
        echo: int | None = None,
        follow: bool = True,
        accept: bool = True,
        timeout_s: int | None = TIMEOUT_S,
    ) -> None:
        self.watchdog_state = watchdog_state
        self.echo = echo
        self.follow = follow
        self.accept = accept
        self.timeout_s = timeout_s
        self.writes: list[tuple[str, float | int | None]] = []
        self._store: StateStore | None = None
        self._gx: dict[str, Value] = {}  # die übernommenen EVCS-Werte je GX-Signal

    def attach(self, store: StateStore) -> None:
        """Verbindet die Anlage mit dem Zustandsspeicher des core."""
        self._store = store

    def write(self, path: str, value: float | int | None) -> bool:
        """Nimmt einen Schreibauftrag an, wie ``VictronMqttAdapter.write``.

        Ein EVCS-Auftrag ohne ``accept`` wird abgelehnt (``False``), jeder andere angenommen.
        """
        self.writes.append((path, value))
        if path == HEARTBEAT_PATH and isinstance(value, int):
            self.echo = value
        elif path in FOLLOWED:
            if not self.accept:
                return False
            if self.follow:
                self._gx[FOLLOWED[path]] = value
        return True

    def before_cycle(self) -> None:
        """Meldet den Watchdog und, solange ``follow`` gilt, die übernommenen EVCS-Werte."""
        if self._store is None:
            raise RuntimeError("Plant.attach fehlt: kein Zustandsspeicher")
        signals: dict[str, Value] = {
            "watchdog.state": self.watchdog_state,
            "watchdog.version": WATCHDOG_VERSION,
        }
        if self.timeout_s is not None:
            signals["watchdog.timeout_s"] = self.timeout_s
        if self.echo is not None:
            signals["watchdog.heartbeat"] = self.echo
        if self.follow:
            signals |= self._gx
        for signal, value in signals.items():
            self._store.update(signal, value, source=SOURCE, kind=SignalKind.STATE)

    def heartbeats(self) -> list[float | int | None]:
        """Die geschriebenen Heartbeats in ihrer Reihenfolge, die Freigabe 0 eingeschlossen."""
        return [value for path, value in self.writes if path == HEARTBEAT_PATH]

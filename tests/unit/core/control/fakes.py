"""Test-Aktor für die Tests der Aktor-Schnittstelle und des Ausführers."""

from dataclasses import dataclass, field
from datetime import datetime

from bindaems.core.control.actuator import Capabilities, Confirmation, matches
from bindaems.shared.control import CommandKind, CommandValue
from bindaems.shared.domain import Alarm, Snapshot


@dataclass
class FakeActuator:
    """Aktor für Tests: merkt sich Sendungen, Rücklesen über ``echo``."""

    id: str
    confirm_timeout_s: float = 10.0
    controllable: bool = True
    refuse: str | None = None
    echo: dict[CommandKind, CommandValue] = field(default_factory=dict)
    sent: list[tuple[CommandKind, CommandValue]] = field(default_factory=list)

    def capabilities(self, snap: Snapshot, now: datetime) -> Capabilities:
        return Capabilities(
            self.controllable, () if self.controllable else ("Cerbo nicht verbunden.",)
        )

    def present(self, snap: Snapshot) -> dict[CommandKind, CommandValue]:
        return dict(self.echo)

    def send(self, kind: CommandKind, value: CommandValue) -> str | None:
        if self.refuse is None:
            self.sent.append((kind, value))
        return self.refuse

    def confirm(
        self, kind: CommandKind, value: CommandValue, snap: Snapshot
    ) -> tuple[Confirmation, str]:
        if kind in self.echo and matches(kind, value, self.echo[kind]):
            return Confirmation.CONFIRMED, ""
        return Confirmation.PENDING, ""

    def alarms(self, now: datetime) -> list[Alarm]:
        return []


@dataclass
class FakeWakeActuator(FakeActuator):
    """Aktor mit Start mit Weckerlaubnis (``WakeStart``): ``wakeable`` sagt, ob er weckbar ist.

    ``controllable`` gilt wie beim ``FakeActuator`` für ``capabilities``, ``wakeable`` für
    ``wake_capabilities``; beide Antworten sind unabhängig voneinander.
    """

    wakeable: bool = True

    def wake_capabilities(self, snap: Snapshot, now: datetime) -> Capabilities:
        if self.wakeable:
            return Capabilities(True)
        return Capabilities(False, ("Fahrzeug nicht angesteckt.",))

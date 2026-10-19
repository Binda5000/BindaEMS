"""Tests der CoE-Probe (Plan 6a, Entscheidung 14): mithören, nie senden."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Callable
from datetime import datetime

import pytest

from bindaems.shared.coe import CoeValue, encode
from bindaems.shared.timeutil import LOCAL_TZ
from bindaems.tools import coe_probe
from bindaems.tools.coe_probe import PORT_BUSY, listen, main, packet_lines, port_busy

AT = datetime(2026, 10, 19, 14, 5, 9, tzinfo=LOCAL_TZ)
CMI = ("192.168.81.92", 5442)


class _ListenPort:
    """Ersatz für den UDP-Port: merkt sich, ob gesendet oder geschlossen wurde."""

    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))

    def close(self) -> None:
        self.closed = True


def test_a_packet_shows_one_line_per_value() -> None:
    data = encode(
        [
            CoeValue(56, 1, True, 1, 215),
            CoeValue(56, 4, True, 10, 120),
            CoeValue(56, 2, False, 43, 1),
        ]
    )
    assert packet_lines(data, CMI, AT) == [
        "14:05:09 von 192.168.81.92: Knoten 56, analog 1, Einheit 1, Rohwert 215",
        "14:05:09 von 192.168.81.92: Knoten 56, analog 4, Einheit 10, Rohwert 120",
        "14:05:09 von 192.168.81.92: Knoten 56, digital 2, Einheit 43, Rohwert 1",
    ]


def test_a_negative_raw_value_keeps_its_sign() -> None:
    data = encode([CoeValue(60, 1, True, 1, -15)])
    assert packet_lines(data, CMI, AT) == [
        "14:05:09 von 192.168.81.92: Knoten 60, analog 1, Einheit 1, Rohwert -15"
    ]


def test_a_broken_packet_is_named_with_its_sender() -> None:
    lines = packet_lines(b"\x02\x00\x0c", ("192.168.81.7", 40000), AT)
    assert len(lines) == 1
    assert lines[0].startswith("14:05:09 von 192.168.81.7: ungültiges Paket (")


async def test_listen_prints_every_packet_and_never_sends() -> None:
    ports: list[_ListenPort] = []
    printed: list[str] = []

    async def factory(
        on_datagram: Callable[[bytes, tuple[str, int]], None], port: int
    ) -> _ListenPort:
        assert port == 5442
        opened = _ListenPort()
        ports.append(opened)
        loop = asyncio.get_running_loop()
        loop.call_soon(on_datagram, encode([CoeValue(56, 3, True, 1, 5)]), CMI)
        loop.call_soon(on_datagram, b"\x01", CMI)
        return opened

    count = await listen(5442, 0.05, out=printed.append, port_factory=factory)
    assert count == 2
    assert printed[0].endswith("von 192.168.81.92: Knoten 56, analog 3, Einheit 1, Rohwert 5")
    assert "ungültiges Paket" in printed[1]
    assert ports[0].sent == []  # mithören heißt: nie senden
    assert ports[0].closed


def test_the_number_of_seconds_must_be_positive(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["listen", "--sekunden", "0"])
    assert exc.value.code == 2


def test_listen_without_a_subcommand_is_refused() -> None:
    with pytest.raises(SystemExit) as exc:
        main([])
    assert exc.value.code == 2


def test_main_listens_for_the_given_seconds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[int, float | None]] = []

    async def fake_listen(port: int, seconds: float | None, **kwargs: object) -> int:
        calls.append((port, seconds))
        return 3

    monkeypatch.setattr(coe_probe, "listen", fake_listen)
    assert main(["listen", "--sekunden", "2.5"]) == 0
    assert calls == [(5442, 2.5)]
    assert "3 Pakete empfangen." in capsys.readouterr().out


def test_a_busy_port_names_the_core(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def busy(port: int, seconds: float | None, **kwargs: object) -> int:
        raise OSError(98, "Address already in use")

    monkeypatch.setattr(coe_probe, "listen", busy)
    assert main(["listen"]) == 2
    assert capsys.readouterr().err.strip() == "Port 5442 ist belegt. Läuft der core noch?"


def test_the_busy_text_names_the_port() -> None:
    assert PORT_BUSY.format(port=5442) == "Port 5442 ist belegt. Läuft der core noch?"


def test_ctrl_c_ends_listening_normally(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def interrupted(port: int, seconds: float | None, **kwargs: object) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(coe_probe, "listen", interrupted)
    assert main(["listen"]) == 0
    assert "Beendet." in capsys.readouterr().out


def test_port_busy_sees_a_bound_port() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as taken:
        taken.bind(("0.0.0.0", 0))  # noqa: S104 - wie der core: alle Schnittstellen
        port = taken.getsockname()[1]
        assert port_busy(port)
    assert not port_busy(port)


async def test_listen_on_a_busy_port_raises_address_in_use() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as taken:
        taken.bind(("0.0.0.0", 0))  # noqa: S104 - wie der core: alle Schnittstellen
        port = taken.getsockname()[1]
        with pytest.raises(OSError) as exc:
            await listen(port, 0.01, out=lambda line: None)
    assert coe_probe.address_in_use(exc.value)

"""Tests des Teils Klimaanlage (Plan 6a, Entscheidung 14) mit dem Ersatzport aus Task 3."""

from __future__ import annotations

import asyncio

import pytest
from tests.unit.tools.part2.conftest import FAST_THERMAL

from bindaems.shared.config import Config
from bindaems.tools.part2.session import SKIPPED_UNATTENDED
from bindaems.tools.part2.thermal import (
    ITEM,
    QUESTION_TIMEOUT,
    QUESTION_VISU,
    SKIPPED,
    build_thermal_session,
    kelvin,
    run_thermal,
)
from bindaems.tools.verify_checks import Finding


async def _run(session, cfg: Config) -> list[Finding]:
    await session.start()
    try:
        return await run_thermal(session, cfg, "klima", timings=FAST_THERMAL)
    finally:
        session.findings.extend(await session.restore(FAST_THERMAL.echo_s))
        await session.stop()


async def _started(session) -> None:
    """Startet die Sitzung und wartet, bis der Adapter seinen Port geöffnet hat."""
    await session.start()
    for _ in range(100):
        if session.adapter.set_offset(0.0):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Der Adapter hat keinen Port geöffnet.")


def _rows(findings: list[Finding]) -> list[tuple[str, str]]:
    return [(f.title, f.status) for f in findings]


async def test_all_four_steps_pass_and_the_end_sends_zero(thermal_session, cfg: Config) -> None:
    asked: list[str] = []
    session, cmi = thermal_session(answers=[True, True], asked=asked, uvr_timeout_s=0.2)
    findings = await _run(session, cfg)
    assert _rows(findings) == [
        ("Werte der Empfangstabelle", "ok"),
        ("Versatz +0,5 K", "ok"),
        ("Visu der UVR", "ok"),
        ("Versatz 0", "ok"),
        ("Timeout der UVR", "ok"),
    ]
    assert all(f.item == ITEM for f in findings)
    assert asked == [QUESTION_VISU, QUESTION_TIMEOUT]
    assert "alle 9 Werte" in findings[0].detail
    assert "Leistung 1200 W" in findings[0].detail
    assert "Betriebsart kuehlen" in findings[0].detail
    assert findings[2].detail == "zeigt „EMS“ +0,5 K"
    # Start mit 0, dann +0,5, 0, +0,5 und am Ende 0; der letzte Wert ist immer 0
    sent = cmi.sent_k()
    assert sent[0] == 0.0
    assert 0.5 in sent
    assert sent[-1] == 0.0
    assert session.findings[-1].title == "Am Ende: Versatz 0"
    assert session.findings[-1].status == "ok"


async def test_nothing_goes_out_while_the_uvr_times_out(thermal_session, cfg: Config) -> None:
    session, cmi = thermal_session(answers=[True, True], uvr_timeout_s=0.3)
    findings = await _run(session, cfg)
    assert findings[-1].status == "ok"
    assert "ohne Paket" in findings[-1].detail
    # zwischen dem letzten +0,5 K und dem Echo 0 schwieg der Port mindestens den Timeout lang
    times = [at for at, value in cmi.offsets]
    last_half = max(i for i, (_, value) in enumerate(cmi.offsets) if value == 0.5)
    assert times[last_half + 1] - times[last_half] >= 0.3


async def test_a_missing_echo_is_a_failure(thermal_session, cfg: Config) -> None:
    session, cmi = thermal_session(answers=[True], echoes=False)
    findings = await _run(session, cfg)
    rows = dict(_rows(findings))
    assert rows["Versatz +0,5 K"] == "fail"
    assert next(f for f in findings if f.title == "Versatz +0,5 K").detail == (
        "Echo nach 0,5 s 0 K"
    )
    assert rows["Timeout der UVR"] == "fail"
    assert "Visu der UVR" not in rows  # ohne Echo keine Rückfrage zur Visu
    assert cmi.sent_k()[-1] == 0.0  # das Ende sendet trotzdem 0


async def test_missing_values_end_the_part_without_sending(thermal_session, cfg: Config) -> None:
    session, cmi = thermal_session(omit=["power_kw", "running"])
    findings = await _run(session, cfg)
    assert _rows(findings) == [("Werte der Empfangstabelle", "fail")]
    assert findings[0].detail == "nach 1 s fehlen: Läuft, Leistung"
    assert set(cmi.sent_k()) == {0.0}  # nur die 0 beim Start und am Ende


async def test_yes_skips_the_visual_check_and_the_timeout(thermal_session, cfg: Config) -> None:
    session, _ = thermal_session(unattended=True)
    findings = await _run(session, cfg)
    rows = {f.title: (f.status, f.detail) for f in findings}
    assert rows["Visu der UVR"] == ("info", SKIPPED_UNATTENDED)
    assert rows["Timeout der UVR"] == ("info", SKIPPED_UNATTENDED)
    assert rows["Versatz +0,5 K"][0] == "ok"


async def test_a_declined_timeout_test_is_skipped(thermal_session, cfg: Config) -> None:
    session, _ = thermal_session(answers=[True, False])
    findings = await _run(session, cfg)
    assert (findings[-1].title, findings[-1].status, findings[-1].detail) == (
        "Timeout der UVR",
        "info",
        SKIPPED,
    )


async def test_a_visu_without_the_offset_is_a_failure(thermal_session, cfg: Config) -> None:
    session, _ = thermal_session(answers=[False, False])
    findings = await _run(session, cfg)
    visu = next(f for f in findings if f.title == "Visu der UVR")
    assert (visu.status, visu.detail) == ("fail", "zeigt „EMS“ nicht +0,5 K")


async def test_an_uvr_without_timeout_is_a_failure(thermal_session, cfg: Config) -> None:
    session, cmi = thermal_session(answers=[True, True], uvr_timeout_s=None)
    findings = await _run(session, cfg)
    assert (findings[-1].status, findings[-1].detail) == (
        "fail",
        "nach 0,6 s ohne Paket +0,5 K",
    )
    assert session.findings[-1].status == "ok"  # das Ende holt die 0 trotzdem
    assert cmi.sent_k()[-1] == 0.0


async def test_an_abort_sends_zero(thermal_session, cfg: Config) -> None:
    session, cmi = thermal_session()
    await _started(session)
    try:
        assert await session.send_and_confirm(0.5, FAST_THERMAL.echo_s) is not None
        assert cmi.echo_k == 0.5
        # was nach Strg+C oder einem Fehler läuft (``verify_part2._finish``)
        [finding] = await session.restore(FAST_THERMAL.echo_s)
    finally:
        await session.stop()
    assert finding.status == "ok"
    assert cmi.sent_k()[-1] == 0.0
    assert cmi.echo_k == 0.0


async def test_the_end_without_values_still_sends_zero(thermal_session, cfg: Config) -> None:
    session, cmi = thermal_session(omit=list(cfg.thermal.devices["klima"].receive.channels()))
    await _started(session)
    try:
        [finding] = await session.restore(FAST_THERMAL.echo_s)
    finally:
        await session.stop()
    assert (finding.status, finding.detail) == (
        "info",
        "0 gesendet; ohne Werte der Klimaanlage kein Echo",
    )
    assert cmi.sent_k()[-1] == 0.0


async def test_the_end_without_echo_is_a_failure(thermal_session, cfg: Config) -> None:
    # Ruling R27: Ohne Echo ist der Aktor nicht steuerbar, die Werte der Klimaanlage kommen aber.
    # Das Ende wartet dann auf das Echo und meldet, dass es fehlt, statt „ohne Werte“.
    session, cmi = thermal_session(omit=["offset_active_k"])
    await _started(session)
    try:
        [finding] = await session.restore(FAST_THERMAL.echo_s)
    finally:
        await session.stop()
    assert finding.status == "fail"
    assert finding.detail.startswith("0 gesendet, aber Echo nach")
    assert cmi.sent_k()[-1] == 0.0


async def test_the_end_without_a_port_is_a_failure(cfg: Config) -> None:
    session = build_thermal_session(cfg, "klima", ask=lambda question: True)
    [finding] = await session.restore(1.0)  # nie gestartet: kein Port
    assert finding.status == "fail"
    assert finding.detail.startswith("nicht gesendet:")


async def test_a_value_beyond_the_limit_is_an_error_of_the_tool(
    thermal_session, cfg: Config
) -> None:
    session, cmi = thermal_session()
    with pytest.raises(ValueError, match="nicht zulässig"):
        await session.send_and_confirm(3.5, 0.1)
    assert cmi.offsets == []


def test_an_unknown_device_is_refused(cfg: Config) -> None:
    with pytest.raises(ValueError, match="Keine Klimaanlage „wp“"):
        build_thermal_session(cfg, "wp", ask=lambda question: True)


async def test_run_thermal_refuses_an_unknown_device(thermal_session, cfg: Config) -> None:
    session, _ = thermal_session()
    with pytest.raises(ValueError, match="Keine Klimaanlage"):
        await run_thermal(session, cfg, "wp")


def test_kelvin_shows_sign_and_comma() -> None:
    assert kelvin(0.5) == "+0,5 K"
    assert kelvin(-0.0) == "0 K"
    assert kelvin(-1.0) == "\N{MINUS SIGN}1 K"
    assert kelvin(None) == "kein Echo"

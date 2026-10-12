"""Tests des Teils EVCS im Prüfprotokoll Teil 2 (Spec 17.2-6 und der sichere Strom, 17.2-5)."""

import asyncio
import re
from dataclasses import replace

import pytest
from tests.unit.tools.part2.conftest import FakeEvcs, no_probe

from bindaems.shared.config import Config
from bindaems.shared.control import CommandKind
from bindaems.shared.domain import SignalKind
from bindaems.tools.part2.evcs import EvcsTimings, run_evcs
from bindaems.tools.verify_checks import Finding

FAST = EvcsTimings(confirm_s=0.2, check_s=0.01, foreign_max_s=0.5, unplug_s=0.2, safe_s=0.2)
ROOMY = replace(FAST, confirm_s=1.0, foreign_max_s=1.0, unplug_s=1.0)  # Luft für Wartezeiten

# Die Rückfragen in ihrer Reihenfolge (Texte aus dem Plan)
CHARGING_QUESTION = (
    "Darf die EVCS kurz „Laden aus“ und wieder „an“ schalten? "
    "Ein angestecktes Fahrzeug pausiert dann."
)
FOREIGN_QUESTION = (
    "Bitte jetzt den Ladestrom der EVCS von Hand ändern (VictronConnect oder Cerbo). Bereit?"
)
UNPLUG_QUESTION = "Netzwerkkabel der EVCS für 60 s abziehen?"
REPLUGGED_QUESTION = "Wieder angesteckt?"
WATCHDOG_QUESTION = "Watchdog-Probe mit verstelltem Strom starten? Der Watchdog löst aus."
SECONDS = r"\d+(,\d)?"  # eine Zeit in Sekunden mit höchstens einer Nachkommastelle


def step(findings: list[Finding], title: str) -> Finding:
    return next(f for f in findings if f.title == title)


def currents(evcs: FakeEvcs) -> list[float | int | None]:
    return [v for p, v in evcs.writes if p.endswith("SetCurrent")]


def with_evcs(cfg: Config, **limits: int) -> Config:
    """Die Beispielkonfiguration mit anderen Stromgrenzen der EVCS."""
    wallbox = cfg.wallboxes["evcs"].model_copy(update=limits)
    return cfg.model_copy(update={"wallboxes": {**cfg.wallboxes, "evcs": wallbox}})


# Vorgabe des Plans


async def test_not_manual_stops_without_writes(evcs_session) -> None:
    session, evcs = evcs_session(state={"wallbox.evcs.gx_mode": 1})
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert findings[0].status == "fail" and evcs.writes == []


async def test_currents_are_confirmed_and_restored(evcs_session) -> None:
    session, evcs = evcs_session(
        answers=[False, False, False, False], state={"wallbox.evcs.gx_set_current_a": 12}
    )
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    findings += await session.restore()
    # Abweichung vom Plan: Auch „EVCS bereit“ ist laut Tabelle ein OK-Befund und steht vorn;
    # die Aussage (die drei Ströme sind in dieser Reihenfolge bestätigt) bleibt.
    assert [f.title for f in findings if f.status == "ok"][:4] == [
        "EVCS bereit",
        "Strom 6 A",
        "Strom 10 A",
        "Strom 16 A",
    ]
    assert [v for p, v in evcs.writes if p.endswith("SetCurrent")] == [6, 10, 16, 12]


async def test_unconfirmed_current_is_an_error(evcs_session) -> None:
    session, _ = evcs_session(follow=False, answers=[False] * 4)
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert any(
        f.title == "Strom 6 A" and f.detail == "nicht bestätigt nach 0,2 s" for f in findings
    )


async def test_foreign_change_is_detected(evcs_session) -> None:
    session, _ = evcs_session(answers=[False, True, False, False], hand_change_a=10)
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    foreign = next(f for f in findings if f.title == "Fremde Änderung")
    assert (foreign.status, foreign.detail) == ("ok", "erkannt nach 3 Prüfungen (10 A statt 16 A)")


async def test_watchdog_step_checks_the_safe_current(evcs_session) -> None:
    session, evcs = evcs_session(answers=[False, False, False, True])

    async def probe() -> list[Finding]:  # Watchdog setzt den sicheren Strom
        evcs.send("evcharger/40/SetCurrent", 6)
        return [Finding("17.2-5", "Auslösung ohne Heartbeat", "ok", "…")]

    findings = await run_evcs(session, session.cfg, "evcs", probe=probe, timings=FAST)
    step = next(f for f in findings if f.title == "Sicherer Strom über den Watchdog")
    assert (step.status, step.detail) == ("ok", "Watchdog setzte 6 A (vorher 10 A)")


async def test_values_outside_the_limits_raise(evcs_session) -> None:
    session, _ = evcs_session()
    with pytest.raises(ValueError):
        await session.set_and_confirm("wallbox.evcs", CommandKind.CURRENT, 32.0, 0.2)


# EVCS bereit


async def test_ready_names_instance_mode_current_and_charging(evcs_session) -> None:
    session, _ = evcs_session(answers=[False] * 4, state={"wallbox.evcs.gx_start_stop": 1})
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert findings[0] == Finding(
        "17.2-6", "EVCS bereit", "ok", "Instanz 40, manueller Modus, Sollstrom 16 A, Laden an"
    )


async def test_ready_names_charging_off(evcs_session) -> None:
    session, _ = evcs_session(answers=[False] * 4, state={"wallbox.evcs.gx_start_stop": 0})
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert findings[0].detail == "Instanz 40, manueller Modus, Sollstrom 16 A, Laden aus"


async def test_not_ready_reports_the_reasons_and_asks_nothing(evcs_session) -> None:
    asked: list[str] = []
    session, _ = evcs_session(state={"wallbox.evcs.gx_mode": 1}, asked=asked)
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert findings == [Finding("17.2-6", "EVCS bereit", "fail", "EVCS nicht im manuellen Modus.")]
    assert asked == []


async def test_ready_waits_for_the_values_after_the_start(evcs_session) -> None:
    # Nach dem Start kommen die Werte erst nach und nach; bis confirm_s darf das dauern.
    session, evcs = evcs_session(answers=[False] * 4, state={"wallbox.evcs.gx_mode": 1})
    asyncio.get_running_loop().call_later(
        0.05,
        lambda: evcs.store.update(
            "wallbox.evcs.gx_mode", 0, source="victron", kind=SignalKind.STATE
        ),
    )
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=ROOMY)
    assert findings[0].status == "ok" and currents(evcs) == [6, 10, 16]


async def test_unknown_current_is_not_ready(evcs_session) -> None:
    # Ohne vorgefundenen Sollstrom ließe er sich nicht wiederherstellen.
    session, evcs = evcs_session(state={"wallbox.evcs.gx_set_current_a": None})
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert findings == [Finding("17.2-6", "EVCS bereit", "fail", "Sollstrom unbekannt.")]
    assert evcs.writes == []


# Ströme


@pytest.mark.parametrize(
    ("limits", "titles"),
    [
        ({"min_a": 10, "safe_a": 10, "max_a": 16}, ["Strom 10 A", "Strom 16 A"]),
        ({"min_a": 6, "safe_a": 6, "max_a": 10}, ["Strom 6 A", "Strom 10 A"]),
        ({"min_a": 8, "safe_a": 8, "max_a": 8}, ["Strom 8 A"]),
        ({"min_a": 6, "safe_a": 6, "max_a": 32}, ["Strom 6 A", "Strom 10 A", "Strom 32 A"]),
    ],
)
async def test_ten_amps_only_between_min_and_max(
    evcs_session, cfg: Config, limits: dict[str, int], titles: list[str]
) -> None:
    session, _ = evcs_session(answers=[False] * 4, config=with_evcs(cfg, **limits))
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert [f.title for f in findings if f.title.startswith("Strom ")] == titles


async def test_current_steps_name_the_time_to_confirm(evcs_session) -> None:
    session, _ = evcs_session(answers=[False] * 4)
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    for title in ("Strom 6 A", "Strom 10 A", "Strom 16 A"):
        assert step(findings, title).item == "17.2-6"
        assert re.fullmatch(rf"bestätigt nach {SECONDS} s", step(findings, title).detail)


async def test_refused_current_names_the_reason(evcs_session) -> None:
    session, evcs = evcs_session(answers=[False] * 4, accept=False)
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert step(findings, "Strom 6 A") == Finding(
        "17.2-6", "Strom 6 A", "fail", "nicht gesendet: Cerbo-Verbindung: Auftrag nicht angenommen."
    )
    assert evcs.writes == []


# Rückfragen


async def test_declined_steps_are_skipped(evcs_session) -> None:
    asked: list[str] = []
    session, evcs = evcs_session(answers=[False] * 4, asked=asked)
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert asked == [CHARGING_QUESTION, FOREIGN_QUESTION, UNPLUG_QUESTION, WATCHDOG_QUESTION]
    assert findings[4:] == [
        Finding("17.2-6", "Laden aus und an", "info", "übersprungen"),
        Finding("17.2-6", "Fremde Änderung", "info", "übersprungen"),
        Finding("17.2-6", "Verbindung unterbrochen", "info", "übersprungen"),
        Finding("17.2-5", "Sicherer Strom über den Watchdog", "info", "übersprungen"),
    ]
    assert not any(p.endswith("StartStop") for p, _ in evcs.writes)


async def test_findings_are_kept_in_the_session_as_they_come(evcs_session) -> None:
    # Nach Strg+C nennt der Bericht die Schritte bis dahin: Sie stehen schon in der Sitzung.
    session, _ = evcs_session(answers=[False] * 4)
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert session.findings == findings


# Laden aus und an


async def test_charging_off_and_on(evcs_session) -> None:
    session, evcs = evcs_session(
        answers=[True, False, False, False], state={"wallbox.evcs.gx_start_stop": 1}
    )
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    result = step(findings, "Laden aus und an")
    assert result.status == "ok"
    assert re.fullmatch(rf"aus nach {SECONDS} s, an nach {SECONDS} s", result.detail)
    assert [v for p, v in evcs.writes if p.endswith("StartStop")] == [0, 1]


async def test_charging_car_names_when_the_power_drops(evcs_session) -> None:
    session, _ = evcs_session(
        answers=[True, False, False, False],
        state={"wallbox.evcs.gx_start_stop": 1, "wallbox.evcs.power_w": 3500.0},
        charging_power_w=3500.0,
    )
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    result = step(findings, "Laden aus und an")
    assert result.status == "ok"
    assert re.fullmatch(
        rf"aus nach {SECONDS} s, an nach {SECONDS} s, Leistung unter 100 W nach {SECONDS} s",
        result.detail,
    )


async def test_power_that_stays_up_is_a_warning(evcs_session) -> None:
    # Die EVCS bestätigt „aus“, die Leistung bleibt aber bei 3,5 kW.
    session, _ = evcs_session(
        answers=[True, False, False, False],
        state={"wallbox.evcs.gx_start_stop": 1, "wallbox.evcs.power_w": 3500.0},
    )
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    result = step(findings, "Laden aus und an")
    assert result.status == "warn"
    assert re.fullmatch(
        rf"aus nach {SECONDS} s, an nach {SECONDS} s, Leistung nach 0,2 s nicht unter 100 W",
        result.detail,
    )


async def test_little_power_before_needs_no_power_check(evcs_session) -> None:
    session, _ = evcs_session(
        answers=[True, False, False, False],
        state={"wallbox.evcs.gx_start_stop": 1, "wallbox.evcs.power_w": 400.0},
    )
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert "Leistung" not in step(findings, "Laden aus und an").detail


def stop_following(evcs: FakeEvcs) -> None:
    """Ab jetzt übernimmt die EVCS keinen Auftrag mehr."""
    evcs.follow = False


async def test_charging_off_not_confirmed_is_an_error(evcs_session) -> None:
    session, evcs = evcs_session(
        answers=[True, False, False, False],
        state={"wallbox.evcs.gx_start_stop": 1},
        react={CHARGING_QUESTION: stop_following},
    )
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert step(findings, "Laden aus und an") == Finding(
        "17.2-6", "Laden aus und an", "fail", "Laden aus: nicht bestätigt nach 0,2 s"
    )
    assert [v for p, v in evcs.writes if p.endswith("StartStop")] == [0]  # kein „an“ danach


# Fremde Änderung


async def test_no_foreign_change_is_a_warning(evcs_session) -> None:
    session, _ = evcs_session(answers=[False, True, False, False])
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert step(findings, "Fremde Änderung") == Finding(
        "17.2-6", "Fremde Änderung", "warn", "keine Änderung erkannt"
    )


async def test_foreign_change_expects_the_last_confirmed_current(evcs_session, cfg: Config) -> None:
    # Höchststrom 10 A: Der letzte bestätigte Strom ist 10 A, nicht der vorgefundene (16 A).
    config = with_evcs(cfg, min_a=6, safe_a=6, max_a=10)
    session, _ = evcs_session(answers=[False, True, False, False], hand_change_a=8, config=config)
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert step(findings, "Fremde Änderung").detail == "erkannt nach 3 Prüfungen (8 A statt 10 A)"


async def test_checks_before_the_change_count_too(evcs_session) -> None:
    # Der Betreiber ändert erst nach seiner Antwort; die Prüfungen bis dahin zählen mit.
    def later(evcs: FakeEvcs) -> None:
        asyncio.get_running_loop().call_later(0.15, evcs.change_by_hand, 10)

    session, _ = evcs_session(answers=[False, True, False, False], react={FOREIGN_QUESTION: later})
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=ROOMY)
    result = step(findings, "Fremde Änderung")
    assert result.status == "ok"
    found = re.fullmatch(r"erkannt nach (\d+) Prüfungen \(10 A statt 16 A\)", result.detail)
    assert found is not None and int(found[1]) > 3


# Verbindung unterbrochen


async def test_current_after_the_interruption(evcs_session) -> None:
    # Nach dem Wiederanstecken meldet die EVCS 12 A; vorher waren 10 A eingestellt.
    asked: list[str] = []

    def replugged(evcs: FakeEvcs) -> None:
        asyncio.get_running_loop().call_later(0.02, evcs.change_by_hand, 12)

    session, evcs = evcs_session(
        answers=[False, False, True, True, False],
        react={REPLUGGED_QUESTION: replugged},
        asked=asked,
    )
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=ROOMY)
    assert asked[2:4] == [UNPLUG_QUESTION, REPLUGGED_QUESTION]
    assert step(findings, "Verbindung unterbrochen") == Finding(
        "17.2-6", "Verbindung unterbrochen", "info", "nach der Unterbrechung 12 A (vorher 10 A)"
    )
    assert currents(evcs) == [6, 10, 16, 10]


async def test_old_readings_do_not_count_after_the_interruption(evcs_session) -> None:
    session, _ = evcs_session(answers=[False, False, True, True, False])
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert step(findings, "Verbindung unterbrochen") == Finding(
        "17.2-6", "Verbindung unterbrochen", "warn", "keine Messwerte nach der Unterbrechung"
    )


async def test_no_unplugging_without_the_current_before(evcs_session) -> None:
    # Übernimmt die EVCS die 10 A nicht, gibt es keinen Vergleich: keine weitere Rückfrage.
    asked: list[str] = []
    session, _ = evcs_session(
        answers=[False, False, True, False],
        react={UNPLUG_QUESTION: stop_following},
        asked=asked,
    )
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert step(findings, "Verbindung unterbrochen") == Finding(
        "17.2-6", "Verbindung unterbrochen", "fail", "Strom 10 A: nicht bestätigt nach 0,2 s"
    )
    assert REPLUGGED_QUESTION not in asked


async def test_interruption_sets_at_most_the_maximum(evcs_session, cfg: Config) -> None:
    config = with_evcs(cfg, min_a=6, safe_a=6, max_a=8)
    session, evcs = evcs_session(answers=[False, False, True, True, False], config=config)
    await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert currents(evcs) == [6, 8, 8]


# Sicherer Strom über den Watchdog


async def test_probe_findings_come_before_the_watchdog_step(evcs_session) -> None:
    session, evcs = evcs_session(answers=[False, False, False, True])
    probe_finding = Finding("17.2-5", "Auslösung ohne Heartbeat", "ok", "…")

    async def probe() -> list[Finding]:
        evcs.send("evcharger/40/SetCurrent", 6)
        return [probe_finding]

    findings = await run_evcs(session, session.cfg, "evcs", probe=probe, timings=FAST)
    assert findings[-2:] == [
        probe_finding,
        Finding(
            "17.2-5", "Sicherer Strom über den Watchdog", "ok", "Watchdog setzte 6 A (vorher 10 A)"
        ),
    ]


async def test_current_the_watchdog_did_not_set_is_an_error(evcs_session) -> None:
    session, _ = evcs_session(answers=[False, False, False, True])
    findings = await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    assert step(findings, "Sicherer Strom über den Watchdog") == Finding(
        "17.2-5",
        "Sicherer Strom über den Watchdog",
        "fail",
        "Strom nach der Auslösung 10 A statt 6 A",
    )


async def test_watchdog_step_starts_at_most_at_the_maximum(evcs_session, cfg: Config) -> None:
    # sicherer Strom 14 A: verstellt wird auf min(14 + 4, 16) = 16 A
    config = with_evcs(cfg, min_a=6, safe_a=14, max_a=16)
    session, evcs = evcs_session(answers=[False, False, False, True], config=config)

    async def probe() -> list[Finding]:
        evcs.send("evcharger/40/SetCurrent", 14)
        return []

    findings = await run_evcs(session, session.cfg, "evcs", probe=probe, timings=FAST)
    assert step(findings, "Sicherer Strom über den Watchdog").detail == (
        "Watchdog setzte 14 A (vorher 16 A)"
    )


async def test_no_probe_without_the_changed_current(evcs_session) -> None:
    # Übernimmt die EVCS den verstellten Strom nicht, ist die Probe ohne Aussage und läuft nicht.
    session, _ = evcs_session(
        answers=[False, False, False, True], react={WATCHDOG_QUESTION: stop_following}
    )
    probed: list[bool] = []

    async def probe() -> list[Finding]:
        probed.append(True)
        return []

    findings = await run_evcs(session, session.cfg, "evcs", probe=probe, timings=FAST)
    assert step(findings, "Sicherer Strom über den Watchdog") == Finding(
        "17.2-5",
        "Sicherer Strom über den Watchdog",
        "fail",
        "vor der Probe Strom 10 A: nicht bestätigt nach 0,2 s",
    )
    assert probed == []


# Wiederherstellen nach dem Teil


async def test_restore_brings_back_current_and_charging(evcs_session) -> None:
    session, evcs = evcs_session(
        answers=[True, True, False, False],
        state={"wallbox.evcs.gx_set_current_a": 12, "wallbox.evcs.gx_start_stop": 0},
        hand_change_a=8,
    )
    await run_evcs(session, session.cfg, "evcs", probe=no_probe, timings=FAST)
    restored = await session.restore()
    assert [(f.title, f.status) for f in restored] == [
        ("Wiederherstellen: Laden aus", "ok"),
        ("Wiederherstellen: Strom 12 A", "ok"),
    ]
    assert evcs.writes[-2:] == [("evcharger/40/StartStop", 0), ("evcharger/40/SetCurrent", 12)]

"""Dienst für das Wärmemodell der Klimaanlage (Plan 5c, Entscheidung 12, Präzisierung 9).

Jede Nacht um 03:50 Uhr Ortszeit (nach dem Lastmodell um 03:15 Uhr und dem Fahrverhalten um
03:40 Uhr, vor der Sicherung um 04:00 Uhr), beim Start und auf die Taste „Jetzt neu berechnen“
liest der Dienst die Viertelstunden der Klimaanlage der letzten 28 Tage, rechnet Modell und
Profil je Betriebsart in einem Thread und legt je Betriebsart eine Zeile in ``thermal_model`` ab;
behalten werden 70 Tage. Der Dienst gilt dem ersten Gerät der Art ``coe_offset`` in
``thermal.devices`` (``thermal_device``); ohne ein solches entsteht er nicht.

Scheitert das Lesen oder Rechnen, gilt der letzte Stand weiter, und die Komponente
``thermal_model`` nennt den Fehler. Auch ein untaugliches Modell ist ein Stand: Der Status zeigt
seine Zahlen und den Grund, die Planung verschiebt mit ihm nichts. Nach jedem abgelegten Stand
rufen die mit ``on_change`` angemeldeten Rückrufe (neu planen).
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import asdict, dataclass, fields
from datetime import date, datetime, time, timedelta
from typing import Any, Final, get_args

import structlog
from sqlalchemy import Engine, delete, insert, select

from bindaems.app.db.schema import thermal_model
from bindaems.app.history.influx import InfluxReader
from bindaems.app.learning.thermal_data import ThermalDataError, ThermalSlot, read_thermal_slots
from bindaems.app.learning.thermal_model import (
    FIT_DAYS,
    MIN_DAYS,
    MIN_EPISODES,
    MIN_R2,
    TAU_MAX_H,
    TAU_MIN_H,
    Mode,
    ModeModel,
    ModeProfile,
    Reason,
    fit_mode,
    mode_profile,
)
from bindaems.app.learning.weather import WeatherArchive
from bindaems.app.schedule import next_local
from bindaems.app.status import ComponentStatus
from bindaems.shared.config import Config
from bindaems.shared.timeutil import LOCAL_TZ, Clock, ensure_utc

log = structlog.get_logger(__name__)

RUN_AT: Final = time(3, 50)
COMPONENT: Final = "thermal_model"
KEEP_DAYS: Final = 70
READY_WAIT_S: Final = 600.0  # so lange wartet der erste Lauf höchstens auf das Wetterarchiv
MODES: Final[tuple[Mode, ...]] = get_args(Mode)
_REASONS: Final = frozenset(get_args(Reason))
_FIELDS: Final = frozenset(field.name for field in fields(ModeModel))
_COUNTS: Final = frozenset({"days", "episodes", "events"})  # ganze Zahlen in ``ModeModel``
_MODE_NAMES: Final[dict[Mode, str]] = {"heizen": "Heizen", "kuehlen": "Kühlen"}
NO_DATA: Final = "Noch keine Daten der Klimaanlage"

ReadSlots = Callable[
    [InfluxReader, WeatherArchive, str, datetime, datetime], Awaitable[list[ThermalSlot]]
]


@dataclass(frozen=True)
class _State:
    """Ein Stand einer Betriebsart: Modell, Profil und ``computed_at`` (UTC)."""

    model: ModeModel
    profile: ModeProfile
    computed_at: datetime


def thermal_device(cfg: Config) -> str | None:
    """Das erste Gerät der Art ``coe_offset`` in ``thermal.devices``; ``None`` ohne ein solches."""
    return next(
        (key for key, device in cfg.thermal.devices.items() if device.kind == "coe_offset"), None
    )


def _build(slots: list[ThermalSlot], today: date) -> dict[Mode, tuple[ModeModel, ModeProfile]]:
    """Modell und Profil je Betriebsart (blockiert: im Thread)."""
    return {mode: (fit_mode(slots, mode), mode_profile(slots, mode, today=today)) for mode in MODES}


# --- JSON der Ablage --------------------------------------------------------------------------


def _finite(value: Any) -> Any:
    """``inf`` und ``nan`` sind in JSON nicht erlaubt (die API lehnt sie ab): unbekannt."""
    return None if isinstance(value, float) and not math.isfinite(value) else value


def model_json(model: ModeModel) -> dict[str, Any]:
    """``ModeModel`` als JSON (Spalte ``params`` und Status)."""
    return {key: _finite(value) for key, value in asdict(model).items()}


def _model_of(data: Any) -> ModeModel:
    """``ModeModel`` aus ``model_json``; unlesbar wirft ``ValueError``."""
    if not isinstance(data, dict) or set(data) != _FIELDS:
        raise ValueError("Die Felder des Wärmemodells passen nicht.")
    if (
        data["mode"] not in MODES
        or data["reason"] not in _REASONS
        or not isinstance(data["usable"], bool)
    ):
        raise ValueError("Betriebsart, Grund oder Tauglichkeit des Wärmemodells sind unlesbar.")
    values = dict(data)
    for key in _FIELDS - {"mode", "reason", "usable"}:
        value = data[key]
        count = key in _COUNTS
        if (
            isinstance(value, bool)
            or (count and not isinstance(value, int))
            or not (value is None or isinstance(value, int | float))
        ):
            raise ValueError(f"Das Feld {key} des Wärmemodells ist keine passende Zahl.")
        if not count and value is not None:
            values[key] = float(value)
    return ModeModel(**values)


def _profile_json(profile: ModeProfile) -> dict[str, Any]:
    """``ModeProfile`` als JSON: je Größe eine Liste ``[Gruppe, Viertelstunde, Wert]``."""
    return {
        "setpoint_c": [
            [group, quarter, value] for (group, quarter), value in profile.setpoint_c.items()
        ],
        "on_share": [
            [group, quarter, value] for (group, quarter), value in profile.on_share.items()
        ],
    }


def _entries(data: Any) -> dict[tuple[int, int], float]:
    result: dict[tuple[int, int], float] = {}
    for entry in data:
        group, quarter, value = entry
        if any(isinstance(v, bool) for v in entry) or not isinstance(value, int | float):
            raise ValueError("Ein Eintrag des Profils ist keine Zahl.")
        result[(int(group), int(quarter))] = float(value)
    return result


def _profile_of(data: Any) -> ModeProfile:
    """``ModeProfile`` aus ``_profile_json``; unlesbar wirft ``ValueError``."""
    if not isinstance(data, dict):
        raise ValueError("Das Profil des Wärmemodells ist kein Objekt.")
    try:
        return ModeProfile(
            setpoint_c=_entries(data["setpoint_c"]), on_share=_entries(data["on_share"])
        )
    except (KeyError, TypeError) as exc:
        raise ValueError("Das Profil des Wärmemodells ist unlesbar.") from exc


# --- Texte ------------------------------------------------------------------------------------


def _de(value: float, digits: int) -> str:
    return f"{value:.{digits}f}".replace(".", ",")


def reason_text(model: ModeModel) -> str:
    """Der Zustand des Modells als deutscher Satz (Karte „Wärmemodell“)."""
    match model.reason:
        case "ok":
            return "Modell taugt."
        case "few_days":
            name = _MODE_NAMES[model.mode]
            return f"Noch zu wenig Tage mit {name}: {model.days} von {MIN_DAYS}."
        case "few_episodes":
            return f"Noch zu wenig Auskühl-Phasen: {model.episodes} von {MIN_EPISODES}."
        case "poor_fit":
            r2 = "unbekannt" if model.r2 is None else _de(model.r2, 2)
            return f"Die Grundleistung passt noch nicht (R² {r2}, nötig {_de(MIN_R2, 1)})."
        case "implausible_tau":
            bounds = f"({TAU_MIN_H:g} bis {TAU_MAX_H:g} h)"
            if model.tau_h is None:
                return f"Die Zeitkonstante ist unplausibel {bounds}."
            return f"Die Zeitkonstante {_de(model.tau_h, 1)} h ist unplausibel {bounds}."
    return f"{NO_DATA}."


def _stamp(moment: datetime) -> str:
    return f"{ensure_utc(moment).astimezone(LOCAL_TZ):%d.%m. %H:%M}"


class ThermalModelService:
    COMPONENT = COMPONENT

    def __init__(
        self,
        *,
        reader: InfluxReader,
        weather: WeatherArchive,
        engine: Engine,
        device: str,
        clock: Clock,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        ready: Callable[[], Awaitable[object]] | None = None,
        read: ReadSlots = read_thermal_slots,
    ) -> None:
        self._reader = reader
        self._weather = weather
        self._engine = engine
        self._device = device
        self._clock = clock
        self._sleep = sleep
        self._ready = ready  # Ende der Nachholung des Wetterarchivs (auch nach einem Fehler)
        self._read = read
        self._running = False
        self._states: dict[Mode, _State] = {}
        self._error: str | None = None  # Fehler des letzten Laufs
        self._tasks: set[asyncio.Task[Any]] = set()  # Aufgaben in ``run`` und ``start_run``
        # Rechnungen und Schreibzugriffe im Thread; nach einem Abbruch laufen sie weiter, bis sie
        # wirklich enden (``close`` wartet darauf, bevor die Datenbank schließt)
        self._threads: set[asyncio.Future[Any]] = set()
        self._changed: list[Callable[[], None]] = []

    # --- Betrieb --------------------------------------------------------------------------

    async def run(self) -> None:
        """Beim Start den abgelegten Stand laden und nach der Nachholung des Wetterarchivs neu
        rechnen; dann täglich um 03:50 Uhr Ortszeit."""
        self._track()
        await self.load_stored()
        await self._wait_ready()
        await self._recompute_logged()
        while True:
            now = self._clock.now()
            due = next_local(now, lambda t: (t.hour, t.minute) == (RUN_AT.hour, RUN_AT.minute))
            await self._sleep((due - now).total_seconds())
            await self._recompute_logged()

    async def _wait_ready(self) -> None:
        if self._ready is None:
            return
        try:
            await asyncio.wait_for(self._ready(), timeout=READY_WAIT_S)
        except TimeoutError:
            log.warning(
                "Nachholung des Wetterarchivs dauert zu lange, das Wärmemodell rechnet ohne sie"
            )

    def _track(self) -> None:
        """Merkt sich die laufende Aufgabe, damit ``close`` sie abbrechen kann."""
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)

    async def _recompute_logged(self) -> None:
        try:
            if not await self.recompute():
                log.warning("Berechnung des Wärmemodells übersprungen, weil schon eine läuft")
        except Exception:  # der Tagesrhythmus darf nicht abreißen
            log.exception("Berechnung des Wärmemodells fehlgeschlagen")

    @property
    def running(self) -> bool:
        return self._running

    def on_change(self, callback: Callable[[], None]) -> None:
        """Meldet einen Rückruf an, den jeder Lauf mit neu abgelegtem Stand ruft (neu planen).

        Ein gescheiterter Lauf ruft nicht. Ein Fehler in einem Rückruf wird protokolliert, die
        übrigen laufen weiter."""
        self._changed.append(callback)

    async def recompute(self) -> bool:
        """Rechnet neu; ``False``, wenn schon ein Lauf läuft."""
        if self._running:
            return False
        self._running = True
        await self._run_claimed()
        return True

    def start_run(self) -> bool:
        """Rechnet im Hintergrund neu (Taste „Jetzt neu berechnen“); ``False``, wenn schon ein Lauf
        läuft. Der Lauf gilt sofort als laufend, auch bevor die Aufgabe beginnt. Nur aus der
        Ereignisschleife aufrufen."""
        if self._running:
            return False
        self._running = True
        task = asyncio.get_running_loop().create_task(
            self._run_claimed_logged(), name="thermal-model-recompute"
        )
        self._tasks.add(task)  # ``close`` bricht sie ab, auch bevor sie begonnen hat
        task.add_done_callback(self._tasks.discard)
        return True

    async def _run_claimed_logged(self) -> None:
        try:
            await self._run_claimed()
        except Exception:  # eine Taste darf nichts abreißen
            log.exception("Berechnung des Wärmemodells fehlgeschlagen")

    async def _run_claimed(self) -> None:
        """Der Lauf, nachdem ``_running`` gesetzt wurde."""
        self._track()
        try:
            await self._compute()
        finally:
            self._running = False

    async def _offload(self, func: Callable[..., Any], *args: Any) -> Any:
        """Rechnet ``func`` in einem Thread; ein Abbruch beendet nur das Warten (``close`` wartet
        auf den Thread)."""
        work = asyncio.ensure_future(asyncio.to_thread(func, *args))
        self._threads.add(work)
        work.add_done_callback(self._thread_done)
        return await asyncio.shield(work)

    def _thread_done(self, work: asyncio.Future[Any]) -> None:
        self._threads.discard(work)
        if not work.cancelled():
            work.exception()  # holt einen Fehler ab, auf den nach einem Abbruch niemand wartet

    async def close(self, timeout_s: float) -> bool:
        """Beim Herunterfahren: bricht laufende Läufe ab und wartet höchstens ``timeout_s`` auf
        Rechnungen und Schreibzugriffe im Thread. ``True``, wenn keiner mehr läuft."""
        tasks = [task for task in self._tasks if task is not asyncio.current_task()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        pending = set(self._threads)
        if not pending:
            return True
        done, _ = await asyncio.wait(pending, timeout=timeout_s)
        return len(done) == len(pending)

    async def _compute(self) -> None:
        now = self._clock.now()
        today = now.astimezone(LOCAL_TZ).date()
        try:
            slots = await self._read(
                self._reader, self._weather, self._device, now - timedelta(days=FIT_DAYS), now
            )
            built = await self._offload(_build, slots, today)
            await self._offload(self._store, built, now)
        except ThermalDataError as exc:
            log.warning(
                "Daten der Klimaanlage nicht gelesen, das letzte Wärmemodell gilt",
                device=self._device,
                error=str(exc),
            )
            self._error = str(exc)
            return
        except Exception as exc:
            log.exception("Wärmemodell nicht berechnet, das letzte gilt", device=self._device)
            self._error = str(exc) or type(exc).__name__
            return
        for mode, (model, profile) in built.items():
            self._states[mode] = _State(model, profile, now)
        self._error = None
        log.info(
            "Wärmemodell berechnet",
            device=self._device,
            slots=len(slots),
            usable=[mode for mode, (model, _) in built.items() if model.usable],
        )
        for callback in self._changed:
            try:
                callback()
            except Exception:
                log.exception("Rückmeldung nach einem neuen Wärmemodell fehlgeschlagen")

    # --- Ablage ---------------------------------------------------------------------------

    def _store(self, built: dict[Mode, tuple[ModeModel, ModeProfile]], now: datetime) -> None:
        """Legt je Betriebsart eine Zeile ab und löscht in derselben Transaktion, was älter als
        70 Tage ist."""
        with self._engine.begin() as conn:
            conn.execute(
                delete(thermal_model).where(
                    thermal_model.c.computed_at < now - timedelta(days=KEEP_DAYS)
                )
            )
            conn.execute(
                insert(thermal_model),
                [
                    {
                        "computed_at": now,
                        "device": self._device,
                        "mode": mode,
                        "params": model_json(model),
                        "profile": _profile_json(profile),
                        "usable": model.usable,
                        "reason": model.reason,
                    }
                    for mode, (model, profile) in built.items()
                ],
            )

    def _rows(self, mode: Mode) -> Iterator[tuple[datetime, Any, Any]]:
        statement = (
            select(thermal_model.c.computed_at, thermal_model.c.params, thermal_model.c.profile)
            .where(thermal_model.c.device == self._device, thermal_model.c.mode == mode)
            .order_by(thermal_model.c.computed_at.desc())
        )
        with self._engine.connect() as conn:
            for row in conn.execute(statement):
                yield ensure_utc(row.computed_at), row.params, row.profile

    def _newest(self, mode: Mode) -> _State | None:
        """Der jüngste lesbare Stand der Betriebsart; nur Zeilen, die jünger sind als er und
        unlesbar, melden eine Warnung."""
        for computed_at, params, profile in self._rows(mode):
            try:
                return _State(_model_of(params), _profile_of(profile), computed_at)
            except ValueError:
                log.warning(
                    "Ein abgelegtes Wärmemodell ist unlesbar", device=self._device, mode=mode
                )
        return None

    def _latest(self) -> dict[Mode, _State]:
        found: dict[Mode, _State] = {}
        for mode in MODES:
            newest = self._newest(mode)
            if newest is not None:
                found[mode] = newest
        return found

    async def load_stored(self) -> None:
        """Lädt den jüngsten abgelegten Stand je Betriebsart (beim Start)."""
        try:
            latest = await self._offload(self._latest)
        except Exception:
            log.exception("Abgelegte Wärmemodelle nicht gelesen")
            return
        self._states.update(latest)
        log.info("Wärmemodelle geladen", device=self._device, modes=len(latest))

    # --- Zugriff --------------------------------------------------------------------------

    def current(self, mode: Mode) -> tuple[ModeModel, ModeProfile, datetime] | None:
        """Der letzte Stand der Betriebsart, auch ein untauglicher; ``None`` ohne Stand."""
        state = self._states.get(mode)
        return None if state is None else (state.model, state.profile, state.computed_at)

    # --- Status ---------------------------------------------------------------------------

    def _has_data(self) -> bool:
        return any(state.model.reason != "no_data" for state in self._states.values())

    def _last_computed(self) -> datetime | None:
        return max((state.computed_at for state in self._states.values()), default=None)

    def component_status(self) -> ComponentStatus:
        if self._error is not None:
            return ComponentStatus(COMPONENT, False, f"Wärmemodell fehlgeschlagen: {self._error}")
        if not self._has_data():
            return ComponentStatus(COMPONENT, True, NO_DATA)
        return ComponentStatus(COMPONENT, True, "Wärmemodell aktuell")

    def _status_text(self) -> str:
        computed = self._last_computed()
        if self._error is not None:
            text = f"Wärmemodell fehlgeschlagen: {self._error}"
            if computed is None:
                return text
            return f"{text.rstrip('.')}. Es gilt der Stand vom {_stamp(computed)}."
        if computed is None or not self._has_data():
            return f"{NO_DATA}."
        return f"Wärmemodell berechnet am {_stamp(computed)}."

    def status(self) -> dict[str, Any]:
        """Zustand für ``GET /api/learning/thermal`` (Präzisierung 9, ohne ``enabled``: den
        Schalter ergänzt der Endpunkt aus den Einstellungen)."""
        computed = self._last_computed()
        return {
            "computed_at": computed.isoformat() if computed is not None else None,
            "running": self._running,
            "status": {"ok": self.component_status().ok, "text": self._status_text()},
            "modes": [self._mode_json(mode) for mode in MODES],
        }

    def _mode_json(self, mode: Mode) -> dict[str, Any]:
        state = self._states.get(mode)
        if state is None:  # eine Betriebsart ohne Stand fehlt nicht: kein Modell, keine Daten
            return {
                "mode": mode,
                "model": None,
                "usable": False,
                "reason": "no_data",
                "text": f"{NO_DATA}.",
                "computed_at": None,
            }
        return {
            "mode": mode,
            "model": model_json(state.model),
            "usable": state.model.usable,
            "reason": state.model.reason,
            "text": reason_text(state.model),
            "computed_at": state.computed_at.isoformat(),
        }

"""Lerndienst des Lastmodells (Plan 5a, Entscheidungen 4, 9, 11 und 15, Präzisierung 7).

Jede Nacht um 03:15 Uhr lernt der Dienst ein neues Modell der Hauslast an, rechnet den Backtest,
legt die Version in der Registry ab und bewertet danach die Aktivierung. Beim Start lädt er die
neueste brauchbare Version; ohne eine trainiert er gleich, sobald die Nachholung des Wetterarchivs
vorbei ist. Training und Backtest laufen in einem Thread mit Zeitlimit; scheitern sie oder reichen
die Daten nicht, bleibt die bisherige Version, und die Aktivierung wird trotzdem neu bewertet.

``predict`` rechnet mit der geladenen Version, auch wenn sie nicht aktiv ist (Schattenbetrieb),
und immer mit der Merkmalsliste dieser Version: Ein seither entfernter Sensor bleibt leer, ein neuer
zählt erst ab dem nächsten Training.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import threading
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from types import MappingProxyType
from typing import Any, Final, Literal, Protocol

import structlog

from bindaems.app.forecast.archive import ForecastArchive, SlotSource, actuals
from bindaems.app.forecast.load import WINDOW_DAYS, LoadProfile, LoadSample
from bindaems.app.learning.activation import ACTIVATION_DAYS, Activation, decide, shadow_sums
from bindaems.app.learning.backtest import MAX_WEEKS, BacktestResult, DaySums, rolling_backtest
from bindaems.app.learning.features import (
    SENSOR_PREFIX,
    FeatureInputs,
    TrainingSet,
    feature_names,
    feature_rows,
    local_midnight,
    profile_cache,
    training_set,
)
from bindaems.app.learning.ha_sensors import HaSensorReader
from bindaems.app.learning.model import TrainedModel, TrainingError, train
from bindaems.app.learning.registry import ModelRegistry, RegistryEntry
from bindaems.app.learning.weather import WeatherArchive
from bindaems.app.schedule import next_local
from bindaems.app.settings.service import SettingsService, SettingsVersion
from bindaems.app.status import ComponentStatus
from bindaems.shared.numbers import de
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, Clock, ensure_utc, slot_start

log = structlog.get_logger(__name__)

TRAIN_AT: Final = time(3, 15)
MIN_DAYS: Final = 28
MIN_SLOTS_PER_DAY: Final = 80
TRAIN_TIMEOUT_S: Final = 600
COMPONENT: Final = "load-model"
HISTORY_DAYS: Final = WINDOW_DAYS + 7 * MAX_WEEKS  # Training (56 Tage) und alle Backtest-Wochen
PREDICT_HISTORY: Final = timedelta(days=8)  # Last vor der Ausgabe: 7 Tage Verzögerung und mehr
SENSOR_CHUNK: Final = timedelta(days=7)  # HA-Abfragen in Stücken, sonst reicht das Zeitlimit nicht
SENSOR_RECENT: Final = timedelta(hours=1)
READY_WAIT_S: Final = 600.0  # so lange wartet das erste Training höchstens auf die Nachholung
FEATURE_LABELS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "lead_h": "Vorlauf",
        "minute_of_day": "Uhrzeit",
        "weekday": "Wochentag",
        "day_type": "Tagestyp",
        "holiday": "Feiertag",
        "month": "Monat",
        "temp_c": "Temperatur",
        "temp_day_c": "Tagesmittel der Temperatur",
        "lag_1d_w": "Last vor einem Tag",
        "lag_7d_w": "Last vor einer Woche",
        "recent_w": "Last der letzten Stunde",
        "profile_w": "Profil",
    }
)

Outcome = Literal["trained", "too_little_data", "failed"]


class LoadHistory(Protocol):
    """Hauslast je Slot wie für das Profil (``LoadForecastService.history``)."""

    async def history(self, start: datetime, end: datetime) -> list[LoadSample]: ...


class TemperatureForecast(Protocol):
    """Temperatur der aktuellen Wetterprognose (``ForecastService.temperatures``)."""

    def temperatures(self) -> Mapping[datetime, float]: ...


@dataclass(frozen=True)
class TrainingResult:
    """Ergebnis eines Trainingslaufs mit dem Satz für die Anzeige."""

    outcome: Outcome
    at: datetime
    text: str


def days_with_data(load_w: Mapping[datetime, float], since: datetime | None = None) -> int:
    """Lokale Tage mit mindestens ``MIN_SLOTS_PER_DAY`` Slots mit Last (ab ``since``)."""
    per_day = Counter(
        slot.astimezone(LOCAL_TZ).date()
        for slot, value in load_w.items()
        if math.isfinite(value) and (since is None or slot >= since)
    )
    return sum(1 for count in per_day.values() if count >= MIN_SLOTS_PER_DAY)


_Fitted = tuple[TrainingSet, TrainedModel, BacktestResult | None]


def _fit(inputs: FeatureInputs, names: Sequence[str], today: date, days: int) -> _Fitted:
    """Training über die letzten 56 Tage vor heute und Backtest (blockiert: im Thread)."""
    end = local_midnight(today)
    data = training_set(inputs, local_midnight(today - timedelta(days=WINDOW_DAYS)), end, names)
    model = train(data, names)
    return data, model, rolling_backtest(inputs, names, today, days)


def _consume(fit: asyncio.Future[Any]) -> None:
    """Holt den Fehler einer Rechnung ab, auf die nach einem Zeitlimit niemand mehr wartet."""
    if not fit.cancelled():
        fit.exception()


def _predict(
    model: TrainedModel, issued: datetime, slots: Sequence[datetime], inputs: FeatureInputs
) -> list[float]:
    return model.predict(feature_rows(issued, slots, inputs, model.features))


def _activation(data: Mapping[str, Any] | None) -> Activation | None:
    """Die gespeicherte Entscheidung (``Activation.as_json``) als Objekt; unlesbar ``None``."""
    if data is None:
        return None
    try:
        return Activation(
            active=bool(data["active"]),
            basis=data["basis"],
            days=int(data["days"]),
            model_mae_w=data["model_mae_w"],
            profile_mae_w=data["profile_mae_w"],
            improvement_pct=data["improvement_pct"],
            reason=data["reason"],
        )
    except (KeyError, TypeError, ValueError):
        return None


def _model_json(entry: RegistryEntry) -> dict[str, Any]:
    # nach Anteil am Gewinn absteigend; bei Gleichstand in der Reihenfolge des Modells
    ranked = sorted(entry.features, key=lambda name: -entry.importance.get(name, 0.0))
    features = [
        {
            "name": name,
            "label": FEATURE_LABELS.get(name, name.removeprefix(SENSOR_PREFIX)),
            "importance": entry.importance.get(name, 0.0),
        }
        for name in ranked
    ]
    return {
        "id": entry.id,
        "trained_at": ensure_utc(entry.trained_at).isoformat(),
        "data_from": ensure_utc(entry.data_from).isoformat(),
        "data_to": ensure_utc(entry.data_to).isoformat(),
        "samples": entry.samples,
        "features": features,
    }


class LoadModelService:
    def __init__(
        self,
        *,
        load: LoadHistory,
        ledger: SlotSource,
        weather: WeatherArchive,
        forecast: TemperatureForecast,
        sensors: HaSensorReader,
        archive: ForecastArchive,
        registry: ModelRegistry,
        settings: SettingsService,
        clock: Clock,
        ready: Callable[[], Awaitable[object]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout_s: float = TRAIN_TIMEOUT_S,
    ) -> None:
        self._load = load
        self._ledger = ledger
        self._weather = weather
        self._forecast = forecast
        self._sensors = sensors
        self._archive = archive
        self._registry = registry
        self._settings = settings
        self._clock = clock
        self._ready = ready  # Ende der Nachholung des Wetterarchivs (auch nach einem Fehler)
        self._sleep = sleep
        self._timeout_s = timeout_s
        self._lock = asyncio.Lock()  # ein Training oder eine Bewertung zugleich
        self._training = False  # ein Lauf von ``train`` ist im Gang
        self._waiting_ready = False  # ``run`` wartet beim Start auf die Nachholung
        self._task: asyncio.Task[None] | None = None  # Lauf über die Taste
        # Rechnung im Thread; nach einem Zeitlimit rechnet sie weiter, bis sie wirklich endet
        self._fit: asyncio.Future[_Fitted] | None = None
        self._evaluations: set[asyncio.Task[None]] = set()  # Bewertungen nach dem Schalter
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None
        self._seen_lock = threading.Lock()
        self._seen_enabled = settings.current().settings.learning.load_model_enabled
        self._entry: RegistryEntry | None = None  # geladene, brauchbare Version
        self._model: TrainedModel | None = None
        self._active = False  # Entscheidung der letzten Bewertung für die geladene Version
        self._activation: Activation | None = None
        self._decided_at: datetime | None = None
        # Backtest je Tag zur Version (nicht in der Registry; nach einem Neustart fehlt er bis
        # zum nächsten Training, dann entscheidet nur der Schattenbetrieb)
        self._backtest: tuple[int, Mapping[date, DaySums] | None] | None = None
        self._result: TrainingResult | None = None
        self._error: str | None = None  # Fehler des letzten Trainings
        self._units_known = False  # ``candidates`` gelang, die Messungen je Einheit sind bekannt
        self._listeners: list[Callable[[], None]] = []  # hören auf Änderungen von ``active``
        self._announced = False  # ``active`` bei der letzten Meldung
        settings.on_change(self._on_settings)

    def on_active_change(self, callback: Callable[[], None]) -> None:
        """Meldet jede Änderung von ``active`` (Schalter, Bewertung, Laden beim Start); die
        Lastprognose gibt dann gleich neu aus. Der Aufruf kommt aus der Ereignisschleife."""
        self._listeners.append(callback)

    def _announce(self) -> None:
        """Ruft die Hörer, wenn sich ``active`` seit der letzten Meldung geändert hat."""
        active = self.active()
        if active == self._announced:
            return
        self._announced = active
        for callback in list(self._listeners):
            try:
                callback()
            except Exception:  # ein Hörer darf Training und Bewertung nicht stören
                log.exception("Meldung des Lastmodells fehlgeschlagen")

    # --- Training -------------------------------------------------------------------------

    async def train(self) -> TrainingResult:
        """Ein Trainingslauf und danach die Bewertung der Aktivierung; Läufe warten aufeinander.
        Ein Fehler gibt das Ergebnis ``failed``, die bisherige Version bleibt."""
        self._bind()
        async with self._lock:
            self._training = True
            try:
                now = self._clock.now()
                self._error = None
                try:
                    result = await self._train(now)
                except Exception as exc:
                    log.exception("Training des Lastmodells fehlgeschlagen")
                    result = self._failed(now, str(exc))
                self._result = result
                try:
                    await self._evaluate()
                except Exception:
                    log.exception("Aktivierung des Lastmodells nicht bewertet")
                self._announce()  # auch nach einer gescheiterten Bewertung der neuen Version
                return result
            finally:
                self._training = False

    def _failed(self, now: datetime, error: str) -> TrainingResult:
        self._error = error
        return TrainingResult("failed", now, f"Training fehlgeschlagen: {error}")

    async def _train(self, now: datetime) -> TrainingResult:
        today = now.astimezone(LOCAL_TZ).date()
        start = local_midnight(today - timedelta(days=HISTORY_DAYS))
        end = local_midnight(today)
        if self._fit is not None and not self._fit.done():
            log.warning("Das vorige Training des Lastmodells rechnet noch, dieses entfällt")
            return self._failed(now, "Das vorige Training rechnet noch.")
        samples = await self._load.history(start, end)
        load_w = {ensure_utc(item.slot_start): item.house_w for item in samples}
        # die Schwelle gilt in den 56 Trainingstagen, der Backtest sieht das ganze Fenster
        train_days = days_with_data(load_w, local_midnight(today - timedelta(days=WINDOW_DAYS)))
        days = days_with_data(load_w)
        if train_days < MIN_DAYS:
            log.info("Zu wenig Daten für das Lastmodell", days=train_days, needed=MIN_DAYS)
            return TrainingResult(
                "too_little_data", now, f"Zu wenig Daten: {train_days} von {MIN_DAYS} Tagen."
            )
        sensors = list(self._settings.current().settings.learning.ha_sensors)
        names = feature_names(sensors)
        temps = await asyncio.to_thread(self._weather.temperatures, start, end)
        inputs = FeatureInputs(
            load_w=load_w,
            temp_c=temps,
            sensors=await self._sensor_history(sensors, start, end),
            profile_for=profile_cache(samples),
        )
        fit = asyncio.ensure_future(asyncio.to_thread(_fit, inputs, names, today, days))
        fit.add_done_callback(_consume)
        self._fit = fit
        try:
            # ``shield``: das Zeitlimit beendet nur das Warten, ``fit`` endet mit dem Thread
            data, model, backtest = await asyncio.wait_for(
                asyncio.shield(fit), timeout=self._timeout_s
            )
        except TimeoutError:
            log.warning("Training des Lastmodells dauert zu lange", limit_s=self._timeout_s)
            return self._failed(now, f"Zeitlimit von {de(self._timeout_s)} s überschritten")
        except TrainingError as exc:
            log.warning("Lastmodell nicht trainiert", error=str(exc))
            return self._failed(now, str(exc))
        entry = await asyncio.to_thread(
            self._registry.add,
            model,
            trained_at=now,
            data_from=min(data.slots),
            data_to=max(data.slots),
            samples=len(data.rows),
            backtest=backtest,
        )
        try:
            await asyncio.to_thread(self._registry.prune)
        except Exception as exc:  # die neue Version gilt trotzdem
            log.warning("Alte Versionen des Lastmodells nicht gelöscht", error=str(exc))
        self._use(entry, model)
        self._backtest = (entry.id, backtest.daily if backtest is not None else None)
        log.info("Lastmodell trainiert", version=entry.id, samples=len(data.rows), days=days)
        local = now.astimezone(LOCAL_TZ)
        return TrainingResult(
            "trained",
            now,
            f"Trainiert am {local:%d.%m. %H:%M} mit {len(data.rows)} Viertelstunden.",
        )

    async def _sensor_history(
        self, sensors: Sequence[str], start: datetime, end: datetime
    ) -> dict[str, dict[datetime, float]]:
        """Slot-Werte der Sensoren in ``[start, end)``, in Stücken von 7 Tagen gelesen."""
        if not sensors:
            return {}
        await self._learn_units()  # Messungen je Einheit für ``slot_values``
        merged: dict[str, dict[datetime, float]] = {}
        first = ensure_utc(start)
        day = first.astimezone(LOCAL_TZ).date()
        while first < end:
            day += SENSOR_CHUNK  # Stücke bis zur lokalen Mitternacht, auch über die Umstellung
            last = min(local_midnight(day), end)
            for entity, series in (await self._sensors.slot_values(sensors, first, last)).items():
                merged.setdefault(entity, {}).update(series)
            first = last
        return merged

    def _use(self, entry: RegistryEntry, model: TrainedModel) -> None:
        self._entry, self._model = entry, model
        self._activation = _activation(entry.decision)
        self._active = entry.active and self._activation is not None  # unlesbar: nicht aktiv
        self._decided_at = entry.decided_at

    def running(self) -> bool:
        """Ein Training läuft, wartet auf die Nachholung, ist über die Taste angestoßen oder
        rechnet nach einem Zeitlimit noch im Thread."""
        return (
            self._training
            or self._waiting_ready
            or (self._task is not None and not self._task.done())
            or (self._fit is not None and not self._fit.done())
        )

    def start_training(self) -> bool:
        """Training im Hintergrund (Taste „Jetzt neu trainieren“); ``False``, wenn schon eines
        läuft. Nur aus der Ereignisschleife aufrufen."""
        if self.running():
            return False
        self._bind()
        self._task = asyncio.get_running_loop().create_task(
            self._train_logged(), name="load-model-training"
        )
        return True

    async def close(self, timeout_s: float) -> bool:
        """Beim Herunterfahren: bricht den Lauf über die Taste und Bewertungen ab und wartet
        höchstens ``timeout_s`` auf eine Rechnung im Thread. ``True``, wenn keine mehr läuft."""
        tasks = [task for task in (self._task, *self._evaluations) if task is not None]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        fit = self._fit
        if fit is None or fit.done():
            return True
        done, _ = await asyncio.wait({fit}, timeout=timeout_s)
        return bool(done)

    async def _train_logged(self) -> None:
        try:
            await self.train()
        except Exception:  # der Tagesrhythmus darf nicht abreißen
            log.exception("Training des Lastmodells fehlgeschlagen")

    # --- Aktivierung ----------------------------------------------------------------------

    async def evaluate(self) -> Activation:
        """Entscheidet über die Aktivierung: Schatten der letzten 14 Tage aus dem Archiv,
        Backtest der geladenen Version, Schalter aus den Einstellungen. Die Entscheidung kommt
        zur Version in die Registry. Wartet auf ein laufendes Training."""
        self._bind()
        async with self._lock:
            return await self._evaluate()

    async def _evaluate(self) -> Activation:
        """``evaluate`` ohne Lock; nur unter ``_lock`` aufrufen."""
        now = self._clock.now()
        today = now.astimezone(LOCAL_TZ).date()
        entry = self._entry if self._model is not None else None
        enabled = self._settings.current().settings.learning.load_model_enabled
        start = local_midnight(today - timedelta(days=ACTIVATION_DAYS))
        shadow = await asyncio.to_thread(self._shadow, start, local_midnight(today))
        backtest = None
        if entry is not None and self._backtest is not None and self._backtest[0] == entry.id:
            backtest = self._backtest[1]
        activation = decide(
            shadow, backtest, enabled=enabled, has_model=entry is not None, today=today
        )
        if entry is not None:
            await asyncio.to_thread(
                self._registry.decide, entry.id, activation.as_json(), activation.active, now
            )
        self._activation, self._decided_at, self._active = activation, now, activation.active
        self._announce()
        log.info(
            "Aktivierung des Lastmodells bewertet",
            reason=activation.reason,
            active=activation.active,
            basis=activation.basis,
        )
        return activation

    def _bind(self) -> None:
        """Merkt sich die Ereignisschleife, damit Änderungen des Schalters dort bewerten."""
        if self._loop is None:
            self._loop, self._loop_thread = asyncio.get_running_loop(), threading.get_ident()

    def _on_settings(self, version: SettingsVersion) -> None:
        """Ändert sich der Schalter „Lastmodell verwenden“, die Änderung gleich melden und neu
        bewerten (aus jedem Thread); vor dem ersten Lauf ohne Wirkung, ``active`` liest den
        Schalter ohnehin."""
        enabled = version.settings.learning.load_model_enabled
        with self._seen_lock:
            changed, self._seen_enabled = enabled != self._seen_enabled, enabled
        loop = self._loop
        if not changed or loop is None or loop.is_closed():
            return
        if threading.get_ident() == self._loop_thread:
            self._switched()
        else:
            with contextlib.suppress(RuntimeError):  # Schleife inzwischen geschlossen
                loop.call_soon_threadsafe(self._switched)

    def _switched(self) -> None:
        # aus wirkt sofort (``active`` liest den Schalter); an erst mit der Bewertung danach
        self._announce()
        self._spawn_evaluation()

    def _spawn_evaluation(self) -> None:
        task = asyncio.get_running_loop().create_task(
            self._evaluate_logged(), name="load-model-evaluate"
        )
        self._evaluations.add(task)
        task.add_done_callback(self._evaluations.discard)

    async def _evaluate_logged(self) -> None:
        try:
            await self.evaluate()
        except Exception:
            log.exception("Aktivierung des Lastmodells nicht bewertet")

    def _shadow(self, start: datetime, end: datetime) -> dict[date, DaySums]:
        return shadow_sums(
            self._archive.records("load_model", start, end),
            self._archive.records("load_profile", start, end),
            actuals(self._ledger, start, end),
        )

    def active(self) -> bool:
        """Die geladene Version ist brauchbar und aktiv; der ausgeschaltete Schalter wirkt sofort,
        nicht erst nach der nächsten Bewertung."""
        enabled = self._settings.current().settings.learning.load_model_enabled
        return self._model is not None and self._active and enabled

    # --- Vorhersage -----------------------------------------------------------------------

    async def predict(
        self, issued_at: datetime, slots: Sequence[datetime], profile: LoadProfile
    ) -> dict[datetime, float] | None:
        """Erwartete Hauslast der Slots mit der geladenen Version, auch wenn sie nicht aktiv ist;
        ``None`` ohne Version. Last der letzten 8 Tage, Temperatur der Prognose (sonst aus dem
        Archiv), Sensoren der letzten Stunde, als Profil ``profile``. Fehler gehen an den Aufrufer.
        """
        model = self._model
        if model is None:
            return None
        issued = ensure_utc(issued_at)
        targets = [ensure_utc(slot) for slot in slots]
        if not targets:
            return {}
        cutoff = slot_start(issued)
        samples = await self._load.history(cutoff - PREDICT_HISTORY, cutoff)
        first = local_midnight(issued.astimezone(LOCAL_TZ).date())
        archived = await asyncio.to_thread(self._weather.temperatures, first, max(targets) + SLOT)
        forecast = {ensure_utc(slot): t for slot, t in self._forecast.temperatures().items()}
        selected = self._settings.current().settings.learning.ha_sensors
        wanted = [e for e in selected if f"{SENSOR_PREFIX}{e}" in model.features]
        inputs = FeatureInputs(
            load_w={ensure_utc(item.slot_start): item.house_w for item in samples},
            temp_c={**archived, **forecast},
            sensors=await self._recent_sensors(wanted, cutoff),
            profile_for=lambda _day: profile,
        )
        values = await asyncio.to_thread(_predict, model, issued, targets, inputs)
        return dict(zip(targets, values, strict=True))

    async def _recent_sensors(
        self, sensors: Sequence[str], cutoff: datetime
    ) -> dict[str, dict[datetime, float]]:
        if not sensors:
            return {}
        if not self._units_known:
            await self._learn_units()
        return await self._sensors.slot_values(sensors, cutoff - SENSOR_RECENT, cutoff)

    async def _learn_units(self) -> None:
        """Fragt die Messungen je Einheit ab (``candidates``). Nur eine gelungene Abfrage macht
        sie bekannt; nach einem Fehler versucht es die nächste Vorhersage erneut."""
        _, error = await self._sensors.candidates()
        if error is None:
            self._units_known = True

    # --- Betrieb --------------------------------------------------------------------------

    async def run(self) -> None:
        """Beim Start die neueste brauchbare Version laden, ohne eine gleich trainieren (nach der
        Nachholung des Wetterarchivs); dann täglich um 03:15 Uhr Ortszeit. Eine gespeicherte
        Entscheidung „ausgeschaltet“ oder eine fehlende bewertet der Dienst gleich neu, wenn der
        Schalter an ist: Er könnte zwischen zwei Starts angegangen sein."""
        self._bind()
        await self._load_latest()
        if self._needs_evaluation():
            await self._evaluate_logged()
        if self._model is None:
            self._waiting_ready = True
            try:
                await self._wait_ready()
            finally:
                self._waiting_ready = False
            await self._train_logged()
        while True:
            now = self._clock.now()
            due = next_local(now, lambda t: (t.hour, t.minute) == (TRAIN_AT.hour, TRAIN_AT.minute))
            await self._sleep((due - now).total_seconds())
            await self._train_logged()

    def _needs_evaluation(self) -> bool:
        if self._model is None or not self._settings.current().settings.learning.load_model_enabled:
            return False
        return self._activation is None or self._activation.reason == "disabled"

    async def _load_latest(self) -> None:
        try:
            entry = await asyncio.to_thread(self._registry.latest_usable)
            model = await asyncio.to_thread(self._registry.load, entry) if entry else None
        except Exception:
            log.exception("Lastmodell nicht geladen")
            return
        if entry is not None and model is not None:
            self._use(entry, model)
            log.info("Lastmodell geladen", version=entry.id, active=entry.active)
            self._announce()  # die erste Ausgabe kam womöglich noch ohne die Version

    async def _wait_ready(self) -> None:
        if self._ready is None:
            return
        try:
            await asyncio.wait_for(self._ready(), timeout=READY_WAIT_S)
        except TimeoutError:
            log.warning(
                "Nachholung des Wetterarchivs dauert zu lange, das Training beginnt ohne sie"
            )

    # --- Status ---------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        """Zustand für ``GET /api/learning/load`` (Präzisierung 7, ohne ``method``)."""
        learning = self._settings.current().settings.learning
        entry = self._entry if self._model is not None else None
        result = self._result
        component = self.component_status()
        return {
            "enabled": learning.load_model_enabled,
            "activation": self._activation_json(learning.load_model_enabled, entry),
            "model": _model_json(entry) if entry is not None else None,
            "backtest": entry.backtest if entry is not None else None,
            "training": {
                "running": self.running(),
                "last_at": ensure_utc(result.at).isoformat() if result is not None else None,
                "outcome": result.outcome if result is not None else None,
                "text": result.text if result is not None else None,
            },
            "sensors": [
                {
                    "entity_id": entity,
                    # im Modell nur mit Anteil am Gewinn: ein Sensor ohne Daten zählt nicht
                    "in_model": entry is not None
                    and entry.importance.get(f"{SENSOR_PREFIX}{entity}", 0.0) > 0.0,
                }
                for entity in learning.ha_sensors
            ],
            "status": {"ok": component.ok, "text": component.message},
        }

    def _activation_json(self, enabled: bool, entry: RegistryEntry | None) -> dict[str, Any]:
        activation, decided_at = self._activation, self._decided_at
        if activation is None:  # noch nie bewertet: nur Schalter und Modell
            today = self._clock.now().astimezone(LOCAL_TZ).date()
            activation = decide({}, None, enabled=enabled, has_model=entry is not None, today=today)
            decided_at = None
        if not enabled and activation.reason != "disabled":
            activation = replace(activation, active=False, reason="disabled")
        decided = ensure_utc(decided_at).isoformat() if decided_at is not None else None
        return {**activation.as_json(), "decided_at": decided}

    def component_status(self) -> ComponentStatus:
        if self._error is not None:
            return ComponentStatus(COMPONENT, False, f"Lastmodell fehlgeschlagen: {self._error}")
        if self._model is None:
            return ComponentStatus(COMPONENT, True, "Noch kein Modell")  # normal, kein Fehler
        if self.active():
            return ComponentStatus(COMPONENT, True, "Lastmodell aktiv")
        return ComponentStatus(COMPONENT, True, "Profil aktiv, Modell bereit")

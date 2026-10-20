"""Lastprognose v1: gewichtetes Profil der Hauslast je Tagestyp und Uhrzeit (Präzisierung 8).

Die Historie kommt aus der Abrechnung (``ledger_slot``); für Slots ohne vollständigen Abrechnungs-
Slot liest der Dienst, falls ``homeassistant.load_history`` gesetzt ist, die HA-Datenbank. Das
Profil wird beim Start und beim ersten Lauf eines neuen lokalen Tages gebaut; die stündlichen
Ausgaben nutzen das gemerkte Profil. Die Prognose liegt im Speicher; jede Ausgabe geht
zusätzlich ins Archiv und nach InfluxDB.

Mit angehängtem Lastmodell (Plan 5a, Entscheidung 12) rechnet jede Ausgabe Profil und Modell und
archiviert beide (``load_profile``, ``load_model``); als ``load`` geht das aktive Verfahren, bei
fehlenden Modellwerten (mehr als 10 % der Slots) oder einem Fehler der Vorhersage das Profil.
"""

from __future__ import annotations

import asyncio
import math
from collections import defaultdict
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Literal

import structlog

from bindaems.app.consumers.energy import HA_SEED, ha_points
from bindaems.app.consumers.values import HaRef, ha_selection
from bindaems.app.daytype import DayType, slot_key
from bindaems.app.forecast.archive import ForecastArchive, Kind, RecordValue, SlotSource, actuals
from bindaems.app.history.influx import (
    RP_LONG,
    InfluxQueryError,
    InfluxReader,
    Series,
)
from bindaems.app.schedule import next_local
from bindaems.app.status import ComponentStatus
from bindaems.shared.config import Config
from bindaems.shared.influx.lineprotocol import Point as InfluxPoint
from bindaems.shared.influx.lineprotocol import PointSink, epoch_ms
from bindaems.shared.timeutil import (
    LOCAL_TZ,
    SLOT,
    Clock,
    ensure_utc,
    local_day_slots,
    slot_start,
    slots_between,
)

if TYPE_CHECKING:  # ``learning.service`` importiert dieses Modul; zur Laufzeit nur der Name
    from bindaems.app.learning.service import LoadModelService

log = structlog.get_logger(__name__)

WINDOW_DAYS = 56
HALF_LIFE_DAYS = 14.0
MIN_SAMPLES = 3
OUTPUT_MINUTE = 9
RETRY_S = 600.0
SLOT_HOURS = 0.25
OUTPUT_DAYS = 3  # heute, morgen, übermorgen
MINUTES_PER_SLOT = 15
_MINUTE_MS = 60_000
_SCALE_TOLERANCE = 1e-9  # Rundung der Gewichtssummen
MAX_MODEL_GAP = 0.10  # Anteil der Slots ohne Modellwert, bis zu dem das Profil sie auffüllt

Source = Literal["ledger", "ha"]


@dataclass(frozen=True)
class LoadSample:
    slot_start: datetime
    house_w: float
    source: Source


def weighted_median(items: Sequence[tuple[float, float]]) -> float:
    """Gewichteter Median aus (Wert, Gewicht): der kleinste Wert, bei dem das kumulierte Gewicht
    (aufsteigend nach Wert) die Hälfte des Gesamtgewichts erreicht."""
    if not items:
        raise ValueError("Median ohne Werte")
    ordered = sorted(items)
    total = sum(weight for _, weight in ordered)
    reached = 0.0
    for value, weight in ordered:
        reached += weight
        if reached >= total / 2 * (1 - _SCALE_TOLERANCE):
            return value
    return ordered[-1][0]


@dataclass(frozen=True)
class LoadProfile:
    by_key: Mapping[tuple[DayType, time], float]
    by_time: Mapping[time, float]
    overall: float | None
    samples: int

    def at(self, slot: datetime) -> float | None:
        """Erwartete Hauslast in W für den Slot: Tagestyp und Uhrzeit, sonst Uhrzeit, sonst alle."""
        key = slot_key(slot)
        value = self.by_key.get(key)
        if value is None:
            value = self.by_time.get(key[1])
        return value if value is not None else self.overall


def build_profile(samples: Sequence[LoadSample], now: datetime) -> LoadProfile:
    """Profil aus den Stichproben der letzten 56 Tage, Gewicht ``0,5^(Alter / 14 Tage)``."""
    current = ensure_utc(now)
    oldest = current - timedelta(days=WINDOW_DAYS)
    by_key: defaultdict[tuple[DayType, time], list[tuple[float, float]]] = defaultdict(list)
    by_time: defaultdict[time, list[tuple[float, float]]] = defaultdict(list)
    everything: list[tuple[float, float]] = []
    for item in samples:
        start = ensure_utc(item.slot_start)
        if not oldest <= start <= current:
            continue
        age_days = (current - start).total_seconds() / 86_400
        entry = (item.house_w, 0.5 ** (age_days / HALF_LIFE_DAYS))
        key = slot_key(start)
        by_key[key].append(entry)
        by_time[key[1]].append(entry)
        everything.append(entry)
    return LoadProfile(
        by_key=MappingProxyType(
            {key: weighted_median(v) for key, v in by_key.items() if len(v) >= MIN_SAMPLES}
        ),
        by_time=MappingProxyType(
            {key: weighted_median(v) for key, v in by_time.items() if len(v) >= MIN_SAMPLES}
        ),
        overall=weighted_median(everything) if everything else None,
        samples=len(everything),
    )


@dataclass(frozen=True)
class LoadForecast:
    issued_at: datetime
    p50_w: Mapping[datetime, float]
    basis: Mapping[str, int]  # Stichproben aus der Abrechnung ("ledger") und aus HA ("ha")
    method: Literal["profile", "model"] = "profile"  # Verfahren, das die Werte liefert

    def day_energy_kwh(self, day: date) -> float | None:
        """Energie des lokalen Tages aus den vorhandenen Slots; ohne Slots ``None``."""
        values = [self.p50_w[slot] for slot in local_day_slots(day) if slot in self.p50_w]
        return sum(value * SLOT_HOURS / 1000.0 for value in values) if values else None


class LoadForecastService:
    def __init__(
        self,
        ledger: SlotSource,
        cfg: Config,
        reader: InfluxReader | None,
        archive: ForecastArchive | None,
        clock: Clock,
        sink: PointSink | None = None,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._ledger = ledger
        self._cfg = cfg
        self._reader = reader
        self._archive = archive
        self._clock = clock
        self._sink = sink
        self._sleep = sleep
        self._forecast: LoadForecast | None = None
        self._error: str | None = None
        self._profile: LoadProfile | None = None
        self._basis: dict[str, int] = {"ledger": 0, "ha": 0}
        self._profile_day: date | None = None  # lokaler Tag, an dem das Profil gebaut wurde
        self._ha_ok = True  # die HA-Abfrage der letzten Historie hat geklappt
        self._warned_units: set[str] = set()  # Entitäten mit falscher Einheit, schon gemeldet
        self._model: LoadModelService | None = None

    def attach_model(self, model: LoadModelService) -> None:
        """Hängt das Lastmodell an (es braucht seinerseits dieses Profil, daher nicht im Aufbau).
        Ohne Modell gibt jede Ausgabe das Profil aus."""
        self._model = model

    def latest(self) -> LoadForecast | None:
        return self._forecast

    def profile(self) -> LoadProfile | None:
        """Das gemerkte Profil (gebaut beim ersten Lauf des Tages); vor dem ersten ``None``."""
        return self._profile

    async def history(self, start: datetime, end: datetime) -> list[LoadSample]:
        """Hauslast je Viertelstunde in ``[start, end)``: Abrechnung, wo vollständig, sonst HA."""
        known = await asyncio.to_thread(actuals, self._ledger, start, end)
        samples = [LoadSample(slot, actual.house_w, "ledger") for slot, actual in known.items()]
        missing = [slot for slot in slots_between(start, end) if slot not in known]
        if missing:
            samples += await self._ha_samples(missing)
        return sorted(samples, key=lambda item: item.slot_start)

    async def _ha_samples(self, missing: Sequence[datetime]) -> list[LoadSample]:
        """Hauslast aus HA für die Slots ``missing`` mit einer Abfrage über ihren Bereich."""
        history = self._cfg.homeassistant.load_history if self._cfg.homeassistant else None
        database = self._cfg.influxdb.ha_database
        if history is None or database is None or self._reader is None:
            return []
        refs = [_ref(entity) for entity in (history.entity, *history.subtract)]
        first, last = missing[0], missing[-1] + SLOT
        try:
            series = await self._reader.query(ha_query(refs, first, last), database=database)
            # HA schreibt nur bei Änderung: der Wert vor dem Bereich gilt bis zum ersten Punkt
            earlier = await self._reader.query(seed_query(refs, first), database=database)
        except InfluxQueryError as exc:
            self._ha_ok = False
            log.warning("Lastverlauf aus Home Assistant nicht lesbar", error=str(exc))
            return []
        entities = [self._entity_values(series, earlier, ref) for ref in refs]
        samples: list[LoadSample] = []
        for slot in missing:
            means = [slot_mean(entity, slot) for entity in entities]
            main, rest = means[0], means[1:]
            if main is None or any(value is None for value in rest):
                continue  # fehlt ein Wert, fällt der Slot weg
            house_w = max(0.0, main - sum(value for value in rest if value is not None))
            samples.append(LoadSample(slot, house_w, "ha"))
        return samples

    def _entity_values(
        self, series: Sequence[Series], earlier: Sequence[Series], ref: HaRef
    ) -> MinuteMeans:
        rejected: set[str] = set()
        minutes = dict(ha_points(series, ref, rejected))
        seeds = sorted(ha_points(earlier, ref, rejected))
        name = f"{ref.domain}.{ref.object_id}"
        if rejected and name not in self._warned_units:
            self._warned_units.add(name)  # nur einmal je Entität melden
            log.warning(
                "Einheit der Lastverlauf-Entität ist weder W noch kW, Werte werden ignoriert",
                entity=name,
                units=sorted(rejected),
            )
        return MinuteMeans(minutes, min(minutes, default=None), seeds[-1][1] if seeds else None)

    async def refresh(self) -> LoadForecast | None:
        """Neue Ausgabe; das Profil wird beim ersten Lauf eines Tages neu gebaut. Scheitert das,
        bleibt die bisherige Prognose."""
        now = self._clock.now()
        today = now.astimezone(LOCAL_TZ).date()
        if self._profile is None or self._profile.overall is None or self._profile_day != today:
            try:
                await self._rebuild(now, today)
            except Exception as exc:
                self._error = str(exc)
                log.warning("Lastprofil nicht zu bauen", error=str(exc))
                return self._forecast
        profile = self._profile
        if profile is None or profile.overall is None:
            self._forecast, self._error = None, None
            return None
        profile_w: dict[datetime, float] = {}
        for day in (today + timedelta(days=offset) for offset in range(OUTPUT_DAYS)):
            for slot in local_day_slots(day):
                value = profile.at(slot)
                if slot >= slot_start(now) and value is not None:
                    profile_w[slot] = value
        model_w = await self._predict(now, profile_w, profile)
        p50_w, method = self._output(profile_w, model_w)
        forecast = LoadForecast(now, p50_w, dict(self._basis), method)
        self._forecast, self._error = forecast, None
        self._publish(forecast)
        await self._archive_forecast(forecast, profile_w, model_w)
        return forecast

    async def _predict(
        self, now: datetime, profile_w: Mapping[datetime, float], profile: LoadProfile
    ) -> dict[datetime, float] | None:
        """Die Modellwerte der Slots (nur endliche); ohne Modell, ohne Version oder bei einem Fehler
        der Vorhersage ``None``. Nur schattenweise (nicht aktiv, kein Archiv) lohnt sie nicht."""
        model = self._model
        if model is None or not profile_w or (self._archive is None and not model.active()):
            return None
        try:
            predicted = await model.predict(now, sorted(profile_w), profile)
        except Exception as exc:  # das Modell darf die Prognose nie verhindern
            log.warning(
                "Vorhersage des Lastmodells fehlgeschlagen, die Ausgabe nimmt das Profil",
                error=str(exc),
            )
            return None
        if predicted is None:
            return None
        return {
            slot: predicted[slot]
            for slot in profile_w
            if slot in predicted and math.isfinite(predicted[slot])
        }

    def _output(
        self, profile_w: Mapping[datetime, float], model_w: Mapping[datetime, float] | None
    ) -> tuple[dict[datetime, float], Literal["profile", "model"]]:
        """Die ausgegebenen Werte: das Modell, wenn es aktiv ist und höchstens 10 % der Slots
        fehlen (dort gilt das Profil), sonst das Profil."""
        model = self._model
        if model is None or model_w is None or not model.active():
            return dict(profile_w), "profile"
        missing = len(profile_w) - len(model_w)
        if missing > MAX_MODEL_GAP * len(profile_w):
            log.warning(
                "Das Lastmodell hat zu viele Lücken, die Ausgabe nimmt das Profil",
                missing=missing,
                slots=len(profile_w),
            )
            return dict(profile_w), "profile"
        return {slot: model_w.get(slot, value) for slot, value in profile_w.items()}, "model"

    async def _rebuild(self, now: datetime, today: date) -> None:
        start = now - timedelta(days=WINDOW_DAYS)
        self._ha_ok = True
        samples = await self.history(start, slot_start(now))
        self._profile = build_profile(samples, now)
        self._basis = {
            "ledger": sum(1 for s in samples if s.source == "ledger"),
            "ha": sum(1 for s in samples if s.source == "ha"),
        }
        # ohne HA-Daten (Abfrage gescheitert) bald erneut versuchen statt einen Tag zu warten
        self._profile_day = today if self._ha_ok else None
        log.info("Lastprofil gebaut", samples=self._profile.samples, basis=self._basis)

    async def _archive_forecast(
        self,
        forecast: LoadForecast,
        profile_w: Mapping[datetime, float],
        model_w: Mapping[datetime, float] | None,
    ) -> None:
        """Ausgabe und Schatten ins Archiv schreiben (``load_model`` nur mit Vorhersage); ein
        Fehler dort lässt die neue Prognose bestehen."""
        if self._archive is None:
            return
        shadows: list[tuple[Kind, Mapping[datetime, float]]] = [
            ("load", forecast.p50_w),
            ("load_profile", profile_w),
        ]
        if model_w is not None:
            shadows.append(("load_model", model_w))
        for kind, powers in shadows:
            values = {slot: RecordValue(power_w, power_w) for slot, power_w in powers.items()}
            try:
                await asyncio.to_thread(self._archive.record, kind, forecast.issued_at, values)
            except Exception as exc:  # das Archiv darf die Prognose nicht verhindern
                log.warning(
                    "Lastprognose konnte nicht archiviert werden", kind=kind, error=str(exc)
                )

    def _publish(self, forecast: LoadForecast) -> None:
        if self._sink is None:
            return
        tags = {"kind": "load", "quantile": "p50"}
        for slot, power_w in sorted(forecast.p50_w.items()):
            self._sink.write(
                InfluxPoint("forecast", tags, {"kw": power_w / 1000.0}, slot, rp=RP_LONG)
            )

    async def run(self) -> None:
        """Sofort bauen und ausgeben, dann zur Minute 9 jeder Stunde; nach Fehler nach 10 min."""
        while True:
            try:
                await self.refresh()
                succeeded = self._error is None
            except Exception as exc:  # die Schleife darf nie stehen bleiben
                log.exception("Lastprognose fehlgeschlagen")
                self._error = str(exc)
                succeeded = False
            now = self._clock.now()
            wait = (next_local(now, lambda t: t.minute == OUTPUT_MINUTE) - now).total_seconds()
            if not succeeded:
                wait = min(wait, RETRY_S)
            await self._sleep(max(wait, 0.0))

    def component_status(self) -> ComponentStatus:
        if self._error is not None:
            return ComponentStatus(
                "load-forecast", False, f"Lastprognose fehlgeschlagen: {self._error}"
            )
        if self._forecast is None:
            return ComponentStatus("load-forecast", False, "Noch keine Daten für die Lastprognose")
        issued = self._forecast.issued_at.astimezone(LOCAL_TZ)
        return ComponentStatus("load-forecast", True, f"Lastprognose von {issued:%H:%M} Uhr")


def ha_query(
    refs: Sequence[HaRef],
    first: datetime,
    last: datetime,
    *,
    units: Sequence[str] = ("W", "kW"),
) -> str:
    """Minutenmittel der Entitäten in ``[first, last)`` aus beiden Schemata (Messung je Einheit,
    Standard ``W``/``kW``, und je Entität); die Einheit der Messung je Entität steht im Feld
    ``unit_of_measurement_str``."""
    measurements, condition = ha_selection(refs, units=units)
    # Bezeichner und Werte sind maskiert
    return (
        f'SELECT mean("value") AS "w", last("unit_of_measurement_str") AS "u" '  # noqa: S608
        f"FROM {measurements} "
        f"WHERE time >= {epoch_ms(first)}ms AND time < {epoch_ms(last)}ms AND ({condition}) "
        'GROUP BY time(60s),"domain","entity_id" fill(previous)'
    )


def seed_query(
    refs: Sequence[HaRef], first: datetime, *, units: Sequence[str] = ("W", "kW")
) -> str:
    """Letzter Wert jeder Entität in den ``HA_SEED`` vor ``first``."""
    measurements, condition = ha_selection(refs, units=units)
    begin = epoch_ms(first - HA_SEED)
    # Bezeichner und Werte sind maskiert
    return (
        f'SELECT last("value") AS "w", last("unit_of_measurement_str") AS "u" '  # noqa: S608
        f"FROM {measurements} "
        f"WHERE time >= {begin}ms AND time < {epoch_ms(first)}ms AND ({condition}) "
        'GROUP BY "domain","entity_id"'
    )


@dataclass(frozen=True)
class MinuteMeans:
    minutes: Mapping[int, float]  # Minutenmittel (Millisekunden seit Epoche)
    earliest: int | None  # erste Minute mit Wert im Bereich
    seed: float | None  # letzter Wert vor dem Bereich


def _ref(entity: str) -> HaRef:
    """``sensor.x`` als Entität; die Einheit gilt, wenn HA keine mitschreibt, als W."""
    domain, _, object_id = entity.partition(".")
    return HaRef(domain, object_id, "W")


def slot_mean(entity: MinuteMeans, slot: datetime) -> float | None:
    """Mittel der Minutenwerte des Slots; Minuten vor dem ersten Punkt im Bereich erhalten den
    Wert vor dem Bereich. Ohne Wert ``None``."""
    values: list[float] = []
    for index in range(MINUTES_PER_SLOT):
        minute = epoch_ms(slot) + index * _MINUTE_MS
        if minute in entity.minutes:
            values.append(entity.minutes[minute])
        elif entity.seed is not None and (entity.earliest is None or minute < entity.earliest):
            values.append(entity.seed)
    return sum(values) / len(values) if values else None

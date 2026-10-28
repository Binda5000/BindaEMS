"""Szenarien des Simulators (Design 13.2): Anlage, Absicht, Wetter, Ereignisse und Prüfungen.

Ein ``Scenario`` beschreibt einen Lauf (``runner.run_scenario``): Start in UTC, Dauer, Saat, den
Aufbau des Modells (``setup``), die Absicht (``intent``; ``live`` schaltet EVCS und Fahrzeuge mit
Tessie live, ``victron_live`` dazu Victron), Werte für die Regelung (``control``), die Ereignisse je
Sekunde und die eigenen Prüfungen am Ergebnis (``checks``). Ort, PV-Wechselrichter und alle Grenzen
kommen aus der Konfiguration des Laufs, in der CI aus ``deploy/config.example.yaml``; die Szenarien
nennen deren Fahrzeuge ``tesla`` und ``egolf`` und Wallboxen ``twc`` und ``evcs``.

**Prüfungen** sind ``Checks``: je ``Check`` ein Satz, was er verlangt, und eine Funktion, die je
Verstoß einen Satz liefert. So zeigt der Bericht jede Prüfung mit OK oder FEHLER.

**Wetter und Hauslast** sind in jeder Sekunde ein Ereignis (``_Weather``): die PV und die Hauslast
je Phase, ``HOUSE_W`` mit einem gleichverteilten Rauschen von ± ``HOUSE_NOISE_W`` und den Zuschlägen
des Szenarios. Die Sonne ist ein halber Sinus über ``DAYLIGHT_H`` Stunden mit ``SUN_PEAK_W`` um
``SOLAR_NOON_H`` Uhr Wiener Zeit (``sun_w``). Rauschen und Wolken zieht je ein eigener Generator aus
der Saat des Szenarios; dieselbe Saat ergibt dieselben Werte, auch in einem zweiten Lauf.

Zu Beginn stecken die Autos an ihrer Standard-Wallbox, laden aber nicht, und der Tesla ist wach,
soweit ein Szenario nichts anderes sagt. Der Gast des Modells (``GUEST_CAR``) steht abgesteckt, und
Home Assistant meldet den e-Golf zu Hause; ein Szenario ändert das mit ``_setup`` (``guest``,
``away``). Die Absicht verlangt den Regelbetrieb. Andere Ereignisse sind Ausfälle (``_Outage``),
eine neue Version der Absicht mit Übersteuerung (``_Override``), mit Boost (``_Boost``) oder mit
einem Plan (``_Plan``, Plan 3c) und das Abstecken (``_Unplug``). Der Akku rechnet in jedem Szenario
mit ``ROUNDTRIP_PCT`` Roundtrip (``Scenario.roundtrip_pct``, Plan 3c, Präzisierung 15).

Abweichungen von der Tabelle des Plans (Entscheidung des Koordinators zu Task 14): In ``sonnig``
und ``wolkig`` steht der Tesla knapp unter seinem Ladelimit, und die Prüfungen verlangen, dass die
Autos nacheinander laden; ``reserve`` hat 1 kW mehr Hauslast auf L1, damit das Importziel greift.

Die Szenarien für Akku und Spitzen (Plan 2c-2a, Design 10.2) schalten Victron live. Abweichungen
von der Tabelle des Plans 2c-2a, weil ihre Erwartung im Modell nicht zu schaffen ist:

- In ``akku-minpv`` steht der Tesla zu Beginn auf seinem Mindeststrom von 5 A (``TESLA_MIN_A``).
  Sonst lädt er nach dem Start 90 s mit 16 A: Der erste Befehl auf 5 A geht über Tessie verloren,
  und der Neuversuch folgt im Abstand von ``current_interval_s`` (Plan 2c-1, Design 7.2). Der Akku
  liefert dann bis zu 5,8 kW in die Autos, denn Regel 2 begrenzt den Override auf
  ``max_grid_charge_setpoint_w`` (8 kW).
- In ``schieflast-pv`` kommt die schwere Last auf L1 erst nach 10 min dazu (``UNBALANCED_FROM_S``),
  wenn der e-Golf schon lädt. Von Anfang an startete er nie: Der Phasenwächter zählt die
  Ladeleistung des Akkus zur Last der Phase (Plan 2c-1, Design 5.1) und lässt auf L1 nur etwa 3 A
  frei, weniger als den Mindeststrom. Lädt der e-Golf, lädt der Akku auf jeder Phase je Ampere des
  e-Golf 2/3 A weniger, und das Importziel bindet ihn zwischen Mindest- und Höchststrom.

``spitze-15min`` startet den Tesla wie im Plan mit 16 A. Die Prüfung der Reihenfolge belegt dort
trotzdem wenig: In der ersten Viertelstunde senkt der Wächter den Tesla in Min+PV um rund 270 W
(seine Grenze bindet nicht, denn ohne PV geht er ohnehin auf 5 A), danach deckt die gemerkte
Senkung das Band unter der festen Grenze von Regel 2 (Plan 2c-2a, Präzisierung 10). Den e-Golf in
Sofort drosselt er erst ab 18:15, wenn der Tesla mit 5 A lädt. Auch ein Wächter, der Sofort vor
Min+PV drosselte, bestünde die Prüfung: Seine Senkung des e-Golf um 1 A fiele um 18:00 mit der des
Phasenwächters zusammen, und die Entscheidung nennte ``phase_limit``, nicht ``peak_guard``. Die
Reihenfolge der Drosselung sichern ``tests/unit/core/control/test_peak_throttle.py`` und
``tests/property/test_peak_guard.py``.

Die Szenarien für die Zuordnung (Plan 2c-2b, Design 10.3) lassen Victron im Dry-Run. ``zuordnung``
ändert sich bewusst (Design 10.3): Das Bild „Tesla an der EVCS“ ist jetzt erkannt und steht in
``tesla-evcs``; ``zuordnung`` prüft „unklar“ an einem echten Widerspruch. Der Tesla steckt dort
woanders (``tesla_elsewhere``), Tessie meldet ihn zu Hause und angesteckt, und weil der Wall
Connector leer ist, stützt ihn an der EVCS auch der Ausschluss; dort lädt aber der e-Golf auf zwei
Phasen, und die Phasen-Signatur schließt den Tesla aus. Die EVCS lädt den e-Golf schon vor dem
Start des core (``evcs_on``): So zählt die Phasen-Signatur im selben Zyklus, in dem die 60 s des
Tesla als Kandidat um wären, und er wird nie kurz erkannt. Lüde die EVCS erst auf den ersten
Befehl der Regelung, gälte der Tesla dort einige Sekunden als erkannt, und die EVCS regelte so
lange in seinem Modus. Unklar ist die Zuordnung so ab rund 3 min: Nach dem Start beobachtet der
core rund 60 s, dann folgen 60 s Phasen-Signatur und 60 s Widerspruch.

Abweichung von der Tabelle des Plans 2c-2b (Entscheidung des Koordinators zu Task 8):
``tesla-evcs`` verlangt „erkannt“ vor 3 min statt binnen 2 min (Design 10.3). Die Erkennung beginnt
erst mit der Regelung, rund 60 s nach dem Start, und braucht 60 s; die 2 min des Designs zählen ab
dem Beginn der Regelung.

Die Szenarien für „Geplant“ und das Ladelimit des Tesla (Plan 4b, Entscheidung 16) schalten Victron
live und lassen nur ein Auto angesteckt. Ziel und Abfahrt kommen wie aus der app als Version 2 der
Absicht (``_Target``), ein Plan danach mit der nächsten. Die Autos melden ihren Ladestand in ganzen
Prozent, abgerundet (``Car.reported_pct``): Tessie und Home Assistant nennen das Ziel so erst, wenn
das Auto es erreicht hat, und der Tesla meldet sein Ladelimit genau mit „Complete“. Die Prüfungen
„bei mindestens 70 %“ gelten darum für den Ladestand im Modell. Abweichung von der Tabelle des
Plans 4b: In ``geplant-plan`` gilt der Plan 30 min statt der 45 min eines Plans der app
(``PLAN_VALID``), dann fällt die app aus; ab da ist die späteste Startzeit erreicht, und er lädt
ohne Stopp mit voller Leistung weiter (Review Focus 4). Grund ist der Abstand zum Ende des
60-min-Laufs: Mit 45 min Plan erreicht der Tesla das Ziel erst bei rund 3530 s und stoppt bei
rund 3545 s, knapp eine Minute vor dem Ende; „lädt danach nicht mehr“ sähe kaum etwas. Mit 30 min
stoppt er bei rund 3215 s, gut sechs Minuten vor dem Ende.

Die Szenarien für die Klimaanlage (Plan 6a, Entscheidung 15) hängen die Klimaanlage des Modells an
den echten CoE-Adapter (``Scenario.thermal``) und lassen sonst nur EVCS und Fahrzeuge live; kein
Auto steckt zu Beginn an. Das Haus rauscht dort nur mit ± ``KLIMA_NOISE_W``, die PV ist konstant:
So hängt jede Stufe an der Regel und nicht am Zufall. Die Prüfungen lesen, was die UVR im Modell
bekam (``ThermalSample.received_k``), und den Stand der Regelung je Sekunde; eine Entscheidung
trägt den Zeitpunkt ihrer Bestätigung durch das Echo, meist eine Sekunde nach dem Paket an die UVR.
„Richtung mehr“ heißt beim Kühlen ein negativer, beim Heizen ein positiver Versatz. Abweichungen
von der Tabelle des Plans:

- ``klima-auto-vorrang`` speist ohne Versatz 4 kW statt 1,5 kW ein (``CAR_EXPORT_W``): Mit 1,5 kW
  startet der e-Golf an der EVCS nie (6 A auf zwei Phasen sind 2760 W). Dass die Klimaanlage
  dann den Überschuss nimmt (Ruling R23 c), hält eine Abwandlung in
  ``tests/integration/test_sim_scenarios.py`` fest.
- ``klima-spitze`` steht auf „Nur Laden“, damit der Akku die schwere Last nicht deckt, und die
  Last kommt zu Beginn einer Viertelstunde (``PEAK_LOAD_S``): Der 15-min-Wächter rechnet je
  Viertelstunde.
- ``klima-teuer-heizen`` sagt „weniger“ mit dem Plan je Viertelstunde (``_every_quarter``), ohne
  PV am Winterabend.

Die Szenarien für Vorheizen und Vorkühlen (Plan 5c, Task 8) bauen darauf auf: Die app schickt je
Viertelstunde einen Plan mit Planwert der Klimaanlage (``_ShiftPlan``, ``_shift_every_quarter``:
``thermal_steps``, ``thermal_grid_w`` und die Betriebsart je Slot), die Klimaanlage heizt in einer
Winternacht ohne PV. Abweichungen vom Plan:

- Der Akku steht auf „Nur Laden“ (``charger_only``), solange der Plan Netzbezug vorsieht: Der Plan
  nimmt Netz für Mehrverbrauch nur in Slots, in denen der Akku nicht entlädt (Entscheidung 8). Mit
  Victron im Dry-Run deckte der Akku sonst jede Last, auch die geplante; das spielt
  ``klima-plan-akku`` ab ``BATTERY_COVERS_S`` durch (``_BatteryCovers``).
- Endet der Plan (``klima-plan-ende``), gehen die geplanten Stufen im selben Zyklus auf 0
  (Grund ``thermal_plan``); nur Stufen aus dem Überschuss blieben für 6a.
- In ``klima-plan-vorheizen`` folgt der teure Block nach drei Viertelstunden
  (``EXPENSIVE_S``), in ``klima-plan-ende`` gilt der einzige Plan nur ``PLAN_ENDS_S`` statt der
  45 min eines Plans der app; danach fällt die app aus. So bleiben die Läufe kurz.
- Geht ein Wunsch über 0 hinweg (+3 → −2), bekommt die UVR im selben Zyklus 0 und das Negative
  erst, wenn die Schrittregel eine Erhöhung des Betrags zulässt: Ein Vorzeichenwechsel geht nur
  über 0.

Die Szenarien für Akku ins Auto (Plan Akku ins Auto, Task 8) schalten Victron live und lassen nur
ein Auto angesteckt; ein einziger Plan kommt zu Beginn (``_plan`` mit ``vehicles_battery``,
``now_battery``, ``support_soc_pct`` und ``soc_end_pct``). Abweichungen von der Vorgabe des Plans,
weil sie im Modell nicht zu schaffen ist:

- In ``akku-uebrig-sofort`` lädt der Tesla mit höchstens ``SPARE_TESLA_A`` (10 A, 6,9 kW, über
  ``now_max_a``). Mit 16 A (11 kW) kappt Regel 2 den Override nach dem Erreichen der Plan-Schwelle
  auf ``max_grid_charge_setpoint_w`` (8 kW, wie in ``akku-sofort``); der Akku gäbe dem Auto dann
  weiter rund 3 kW, und „Netzbezug mindestens Autoleistung minus 300 W“ ginge nicht.
- Ebenda prüft der dritte Punkt die Gesundheit (``battery.spare``) statt des Entscheidungslogs:
  Solange der übrige Akku gilt, trägt der Akku den ganzen Fehlbetrag des Autos in Sofort. Ohne Rest
  fürs Netz gibt es keinen Override und damit keine Entscheidung, die ``battery_spare`` nennen
  könnte, wie bei Boost aus dem Akku in ``boost-abend``; den Grund trägt nur ein Override für einen
  Rest. Die erste Entscheidung an Victron ist der Override mit ``battery_comp``.
"""

from __future__ import annotations

import math
import random
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import KW_ONLY, dataclass
from datetime import UTC, datetime, timedelta
from functools import partial
from itertools import pairwise
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final

from bindaems.core.adapters.coe import WATCH_S
from bindaems.core.control.fleet import GUEST
from bindaems.shared.control import (
    VICTRON,
    ChargeMode,
    CommandKind,
    GuestIntent,
    Intent,
    VehicleIntent,
    thermal_actuator,
    vehicle_actuator,
    wallbox_actuator,
)
from bindaems.shared.decisions import DecisionResult
from bindaems.shared.numbers import de, finite_number
from bindaems.shared.plan import (
    PLAN_VALID,
    BatteryIntent,
    BatteryIntentKind,
    IntentPlan,
    IntentPlanSlot,
    ThermalHint,
    ThermalMode,
)
from bindaems.shared.timeutil import LOCAL_TZ, SLOT, slot_start
from bindaems.tools.sim.cars import TESSIE_DELAY_S, Evcs
from bindaems.tools.sim.metrics import (
    MINIMUM_MARGIN_A,
    PV_MODES,
    TRANSITION_S,
    battery_into_car_pct,
    cars_w,
    shown_number,
)
from bindaems.tools.sim.model import (
    CERBO,
    CMI_EVERY_S,
    EVCS,
    GRID,
    GUEST_CAR,
    TESSIE,
    TWC,
    AcMode,
    AirConditioner,
    PlantModel,
)

if TYPE_CHECKING:
    from bindaems.core.runtime import CoreRuntime
    from bindaems.shared.decisions import Decision
    from bindaems.tools.sim.metrics import Sample, ThermalSample
    from bindaems.tools.sim.runner import SimResult

Action = Callable[[PlantModel, "CoreRuntime"], None]  # ein Ereignis

MINUTE_S: Final = 60
HOUR_S: Final = 3600
HOUSE_W: Final = 300.0  # Hauslast je Phase in W
HOUSE_NOISE_W: Final = 50.0  # Rauschen der Hauslast je Phase und Sekunde, ± in W
ROUNDTRIP_PCT: Final = 88.0  # Wirkungsgrad des Akkus in allen Szenarien, wie in der Planung
SUN_PEAK_W: Final = 9000.0  # PV eines sonnigen Tages am Mittag
SOLAR_NOON_H: Final = 13.0  # Mittag in Wiener Zeit (Sommerzeit)
DAYLIGHT_H: Final = 16.0  # Tageslänge Ende Juni: Sonne von 5 bis 21 Uhr
CLOUD_FACTORS: Final = (1.0, 0.25)  # Sonne und Wolke im Wechsel, Anteil an der PV
CLOUD_SWITCH_S: Final = (30, 180)  # so lange bleibt es sonnig bzw. bewölkt, gleichverteilt

MORNING: Final = datetime(2026, 6, 21, 9, tzinfo=LOCAL_TZ).astimezone(UTC)
SUMMER: Final = datetime(2026, 6, 21, 10, tzinfo=LOCAL_TZ).astimezone(UTC)
EVENING: Final = datetime(2026, 10, 12, 18, tzinfo=LOCAL_TZ).astimezone(UTC)
PEAK_W: Final = 2500.0  # Lastspitze auf L1 am Abend …
PEAK_S: Final = (20 * MINUTE_S, 25 * MINUTE_S)  # … von 20 bis 25 min
PEAK_REACTION_S: Final = 10  # so lange darf die Regelung auf die Spitze reagieren
RAMP_W: Final = 3000.0  # Rampe auf L1 bei „Nur Laden“ …
RAMP_S: Final = (20 * MINUTE_S, 40 * MINUTE_S)  # … von 20 bis 40 min, danach bleibt die Last
RESERVE_PCT: Final = 20.0  # unter diese USV-Reserve entlädt das Modell nie (Szenario „reserve“)
RESERVE_L1_W: Final = 1000.0  # dort mehr Hauslast auf L1: frei ≈ 13 A unter dem Importziel
SUNNY_TESLA_PCT: Final = 72.0  # SOC des Tesla in „sonnig“: „Complete“ nach etwa einer Stunde
CLOUDY_TESLA_PCT: Final = 75.0  # dasselbe in „wolkig“, wo er im Mittel weniger Strom bekommt
NOW_LIMIT_A: Final = 8  # „sofort-limit“: Höchststrom des e-Golf in Sofort (Plan Sofort-Limit)
NOW_LIMIT_TESLA_PCT: Final = 50.0  # … SOC des Tesla: Er lädt in der Stunde nie „Complete“
# „sonnig“: höchstens so viele Strombefehle an die EVCS je Stunde Laden des e-Golf (Design 6.5)
EVCS_CURRENTS_PER_HOUR: Final = 10

FIVE_A_PV_W: Final = 4600.0  # konstante PV in „tesla-5a“: Der Anteil des Tesla ist 5 A …
FIVE_A: Final = 5.0  # … und mit diesem Strom lädt er am Ende
TESLA_START_A: Final = 10  # Strom im Tesla zu Beginn von „tesla-5a“ und „tesla-evcs“
TESLA_MAX_A: Final = 16.0  # Höchststrom des Tesla in der Beispielkonfiguration („tesla-evcs“)
SLEEPY_PV_W: Final = 6000.0  # „tesla-schlaeft“: Die PV steigt linear auf 6 kW …
SLEEPY_RAMP_S: Final = 30 * MINUTE_S  # … in 30 min und bleibt dann
# „ausfaelle“: je Ausfall Name, Beginn und Ende in Minuten, einer nach dem anderen
OUTAGE_MIN: Final = (
    (EVCS, 10, 15),
    (TWC, 20, 25),
    (TESSIE, 30, 35),
    (CERBO, 40, 41),
    (GRID, 50, 53),
)
SAFE_OUTAGES: Final = (CERBO, GRID)  # bei diesen Ausfällen ist der core im sicheren Zustand
RESUMED_S: Final = 60 * MINUTE_S  # ab hier laden in „ausfaelle“ wieder beide Autos
OVERRIDE_S: Final = 10 * MINUTE_S  # „zuordnung“: Übersteuerung „egolf“ an „evcs“ …
UNPLUG_S: Final = 40 * MINUTE_S  # … Abstecken …
EVCS_OFF_S: Final = 41 * MINUTE_S  # … und ab hier lädt die EVCS nicht mehr

# Szenarien für Akku und Spitzen (Plan 2c-2a, Design 10.2)
SETTLED_S: Final = 2 * MINUTE_S  # ab hier prüfen „akku-sofort“, „boost-abend“, „override-stunde“
CAR_FROM_BATTERY_PCT: Final = 5.0  # „akku-wolkig“: so viel der Energie des e-Golf aus dem Akku
TESLA_MIN_A: Final = 5  # „akku-minpv“: Strom im Tesla zu Beginn (Mindeststrom)
# „akku-minpv“: Akku in die Autos höchstens SUPPORT_W von SUPPORT_FROM_S bis SUPPORT_UNTIL_S nach
# dem ersten Laden (die Unterstützung endet nach support_max_s, 900 s), ab GRID_ONLY_S danach
# höchstens GRID_ONLY_W. SUPPORT_W ist support_max_w (3000 W) plus das Totband des Overrides
# (setpoint_deadband_w, 200 W): So weit darf der Bedarf wandern, ehe der Override folgt.
# GRID_ONLY_W ist das Totband allein.
SUPPORT_W: Final = 3200.0
SUPPORT_FROM_S: Final = 10
SUPPORT_UNTIL_S: Final = 890
GRID_ONLY_W: Final = 200.0
GRID_ONLY_S: Final = 930
LOADS_ABOVE_W: Final = 8300.0  # „akku-sofort“: Ziehen Autos und Haus mehr, …
GRID_BETWEEN_W: Final = (7800.0, 8200.0)  # … liegt der Netzbezug hier, um 8 kW (Regel 2)
BOOST_SOC_PCT: Final = 25.0  # „boost-abend“: Über diesem SOC (Reserve + reserve_guard_pct) …
BOOST_GRID_W: Final = 300.0  # … zieht das Haus höchstens so viel aus dem Netz
OVERRIDE_AGE_S: Final = 245.0  # „override-stunde“: höchstens so lange seit dem letzten Schreiben
VICTRON_DECISIONS: Final = 5  # … und höchstens so viele Entscheidungen an Victron
LOCKED_FROM_S: Final = MINUTE_S  # „dess-an“: ab hier ist Victron für den Akku-Sollwert gesperrt
PEAK_TARGET_KW: Final = 8.0  # „spitze-15min“: das 15-min-Ziel; der Lauf beginnt …
PEAK_LEAD_S: Final = MINUTE_S  # … so lange vor 18:00, der core regelt ab der Viertelstunde
QUARTER_S: Final = 15 * MINUTE_S
PEAK_QUARTERS: Final = 3  # … so viele Viertelstunden ab 18:00 prüft er, je mit höchstens …
QUARTER_KWH: Final = 2.02  # … so viel Netzbezug (8 kW · 0,25 h + 1 %) …
PEAK_BOOST_S: Final = 46 * MINUTE_S  # … und ab hier (18:45) lädt der e-Golf in Boost
UNBALANCED_PV_W: Final = 12000.0  # „schieflast-pv“: konstante PV …
UNBALANCED_L1_W: Final = 5500.0  # … und so viel mehr Last auf L1 …
UNBALANCED_FROM_S: Final = 10 * MINUTE_S  # … ab hier, wenn der e-Golf schon lädt
# Szenarien „Aus“ und Selbststart des Tesla (Plan 2d, Task 6)
OFF_AT_S: Final = 45 * MINUTE_S  # „tesla-aus“: der Tesla geht auf „Aus“ …
OFF_WITHIN_S: Final = 10  # … und bekommt binnen so vielen Sekunden „Laden aus“
OFF_TESLA_PCT: Final = 45.0  # SOC des Tesla in „tesla-aus“: Er lädt auch nach 45 min noch
SELF_START_S: Final = 5 * MINUTE_S  # „tesla-selbststart“: der Tesla startet selbst …
# … und bekommt binnen 120 s (control.tesla_against_s) plus 60 s Abfrage von Tessie plus dem
# Verzug des Befehls „Laden aus“
SELF_START_WITHIN_S: Final = 210
IDLE_FROM_S: Final = 9 * MINUTE_S  # … und lädt ab hier nicht mehr
SELF_START_TESLA_PCT: Final = 50.0
PEAK_GUARD: Final = "peak_guard"  # Grund einer Drosselung durch den 15-min-Wächter
# Szenarien zur Planausführung (Plan 3c, Entscheidung 13)
NIGHT: Final = datetime(2026, 10, 13, 2, tzinfo=LOCAL_TZ).astimezone(UTC)
PLAN_SLOTS: Final = 4  # so viele Slots hat ein Plan, der alle 15 min kommt, …
PLAN_VALID_S: Final = int(PLAN_VALID.total_seconds())  # … und so lange gilt er
HOLD_W: Final = 300.0  # bei HALTEN entlädt der Akku im Mittel jeder Minute höchstens so viel …
HOLD_SOC_DROP_PCT: Final = 1.5  # … der SOC fällt höchstens so viele Prozentpunkte …
HOLD_DECISIONS_PER_HOUR: Final = 40  # … mit höchstens so vielen Entscheidungen an Victron je h
HOLD_JUMP_W: Final = 2000.0  # „plan-halten“: so viel mehr Last auf L1 von 20 bis 25 min (PEAK_S)
GRID_CHARGE_W: Final = 3000.0  # „plan-netzladen“: Netzladen für 30 min …
GRID_CHARGE_S: Final = 30 * MINUTE_S
GRID_CHARGE_BAND_W: Final = 300.0  # … der Netzbezug bei Haus + 3000 W auf ± so viel …
GRID_CHARGE_SHARE: Final = 0.9  # … und der SOC steigt um mindestens diesen Anteil der Energie


def grid_charge_rise_pct(usable_kwh: float) -> float:
    """Der Anstieg des SOC in „plan-netzladen“ in Prozentpunkten bei ``usable_kwh`` nutzbaren kWh:
    90 % von 3 kW · 0,5 h · √0,88, geteilt durch die Kapazität."""
    stored_kwh = GRID_CHARGE_SHARE * GRID_CHARGE_W / 1000 * GRID_CHARGE_S / HOUR_S
    return stored_kwh * math.sqrt(ROUNDTRIP_PCT / 100) / usable_kwh * 100


NIGHT_SOC_PCT: Final = 30.0
EXPIRES_S: Final = 20 * MINUTE_S  # „plan-abgelaufen“: Der Plan gilt so lange; ab …
FREE_FROM_S: Final = 26 * MINUTE_S  # … hier (Override verfallen, 300 s plus 60 s) gilt keiner …
CARRY_W: Final = 300.0  # … und die Akkuleistung liegt höchstens so viel über −Haus
BUDGET_CHARGE_W: Final = 6000.0  # „plan-budget“: Netzladen mit dem Budget …
BUDGET_KW: Final = 4.0
BUDGET_QUARTER_KWH: Final = 1.02  # … je Viertelstunde höchstens 4 kW · 0,25 h + 0,02 kWh …
# … und mindestens so viel: Ohne Plan trüge der Akku das Haus, der Netzbezug bliebe nahe 0
BUDGET_QUARTER_MIN_KWH: Final = 0.9
BOOST_FROM_S: Final = MINUTE_S  # „plan-boost“: Boost ab hier …
BOOSTING_SHARE: Final = 0.9  # … und der e-Golf lädt in mindestens diesem Anteil der Sekunden

# Szenarien für „Geplant“ und das Ladelimit des Tesla (Plan 4b, Entscheidung 16)
TARGET_REACHED: Final = "target_reached"  # Grund des Stopps am Ziel-SOC
PLANNED_TARGET_PCT: Final = 70  # das Ziel in „geplant-plan“, „-spaet“, „-pv“ und „-unbekannt“
PLANNED_TESLA_PCT: Final = 60.0  # SOC des Tesla zu Beginn von „geplant-plan“ und „geplant-pv“
PLAN_TESLA_W: Final = 6900  # „geplant-plan“: der Plan lädt den Tesla mit 10 A auf 3 Phasen …
PLAN_TESLA_A: Final = 10.0  # … solange der Plan gilt, mit diesem Strom …
PLAN_A_TOLERANCE: Final = 0.5  # … auf so viel genau, …
TESSIE_ANSWER_S: Final = math.ceil(TESSIE_DELAY_S[1])  # … nach der längsten Antwortzeit von Tessie
PLAN_TESLA_MIN_A: Final = 9.0  # … und er lädt bis zum Ziel mit mindestens so viel Strom; …
PLAN_RUNS_OUT_S: Final = 30 * MINUTE_S  # … der Plan gilt so lange, dann fällt die app aus, …
PLAN_DEPARTURE: Final = "19:05"  # … und mit dieser Abfahrt ist die späteste Startzeit da vorbei
LATE_DEPARTURE: Final = "20:00"  # „geplant-spaet“: Abfahrt 2 h nach dem Beginn
PV_DEPARTURE: Final = "07:00"  # „geplant-pv“: Abfahrt am nächsten Morgen
# „geplant-unbekannt“: mit 0 % ist die späteste Startzeit um 18:31 (30 kWh mit 7,36 kW, · 1,1,
# 15 min Puffer), und das Laden beginnt binnen so vieler Sekunden danach
UNKNOWN_DEPARTURE: Final = "23:15"
LATEST_START_WITHIN_S: Final = 30
LIMIT_TARGET_PCT: Final = 75  # „tesla-ladelimit“: das Ziel, …
LIMIT_START_PCT: Final = 60.0  # … das Ladelimit im Auto zu Beginn …
LIMIT_TESLA_PCT: Final = 65.0  # … und sein SOC, über dem Limit: Er ist zu Beginn „Complete“; …
LIMIT_DEPARTURE: Final = "19:00"  # … mit dieser Abfahrt ist die späteste Startzeit schon vorbei

TESLA: Final = "tesla"  # Schlüssel der Fahrzeuge und Wallboxen in der Konfiguration
EGOLF: Final = "egolf"
WALL_CONNECTOR: Final = "twc"
EVCS_BOX: Final = "evcs"
WAKE: Final = "wake"  # Grund eines Weckstarts
UNCLEAR: Final = "unclear"  # Zuordnung „unklar“ in der Gesundheit
DETECTED: Final = "detected"  # Zuordnung „erkannt“ in der Gesundheit
_SHOWN: Final = {UNCLEAR: "unklar", DETECTED: "erkannt"}  # so nennen die Prüfungen die Zuordnung
SAFE: Final = "SAFE"
FOREIGN_ALARM: Final = "competitor.foreign."  # Anfang der Alarme fremder Änderungen
# Entscheidungen ohne Befehl der Regelung an den Aktor: abgelehnt, wegen eines Stundenlimits
# zurückgestellt oder eine fremde Änderung (sie nennt den erwarteten Wert, aber das EMS sendet
# nichts); neue Ergebnisse zählen so nicht ungefragt als Befehl
_HELD_BACK: Final = frozenset(
    {DecisionResult.REJECTED, DecisionResult.DEFERRED, DecisionResult.FOREIGN}
)
# dieselben ohne die abgelehnten: Eine abgelehnte Entscheidung zählt als Entscheidung, eine
# zurückgestellte nicht (ihr Befehl kommt noch einmal), eine fremde Änderung auch nicht
_NOT_THE_COMMAND: Final = frozenset({DecisionResult.DEFERRED, DecisionResult.FOREIGN})


@dataclass(frozen=True)
class Check:
    """Eine Prüfung eines Szenarios: ``text`` sagt, was sie verlangt; ``find`` liefert je Verstoß
    einen Satz, bestanden ist sie ohne."""

    text: str
    find: Callable[[SimResult], list[str]]


@dataclass(frozen=True)
class Checks:
    """Die Prüfungen eines Szenarios; der Aufruf liefert alle Verstöße in ihrer Reihenfolge."""

    items: tuple[Check, ...]

    def __call__(self, result: SimResult) -> list[str]:
        return [found for check in self.items for found in check.find(result)]


@dataclass(frozen=True)
class Scenario:
    """Ein Szenario des Simulators.

    - ``name``: Name für den Aufruf, ``title``: Überschrift des Berichts
    - ``start``: Beginn in UTC, ``duration_s``: Dauer in s, ``seed``: Saat aller Zufallswerte
    - ``setup``: stellt das Modell vor der ersten Sekunde ein (Akku, Autos, Multi)
    - ``intent``: die Absicht; mit ``live`` sind EVCS und Fahrzeuge mit Tessie live, sonst im
      Dry-Run
    - ``events``: Sekunde und Aktion, vor der Physik dieser Sekunde, in ihrer Reihenfolge
    - ``checks``: die eigenen Prüfungen, meist ``Checks``; je Verstoß ein Satz
    - ``victron_live``: Victron (der Akku-Sollwert) ist zusätzlich live; ohne bleibt es im Dry-Run
    - ``control``: Werte für ``ControlConfig``, die die Konfiguration des Laufs überschreiben, etwa
      ``{"peak_target_kw": 8.0}``; ein ungültiger Wert ist ein ``ValidationError``
    - ``roundtrip_pct``: der Wirkungsgrad des Akkus im Modell, Laden und Entladen zusammen
      (Plan 3c, Präzisierung 15)
    - ``vehicles``: je Fahrzeug Werte für ``VehicleConfig``, die die Konfiguration des Laufs
      überschreiben, etwa ``{"tesla": {"charge_limit_sync": True}}`` (Plan 4b)
    - ``thermal``: Die Klimaanlage des Modells hängt am echten CoE-Adapter und ist live (Plan 6a);
      ohne bleibt sie ohne Verbindung, aus und ohne Leistung
    """

    name: str
    title: str
    start: datetime
    duration_s: int
    seed: int
    setup: Callable[[PlantModel], None]
    intent: Intent
    live: bool
    events: tuple[tuple[int, Action], ...]
    checks: Callable[[SimResult], list[str]]
    victron_live: bool = False
    control: Mapping[str, Any] = MappingProxyType({})
    roundtrip_pct: float = ROUNDTRIP_PCT
    vehicles: Mapping[str, Mapping[str, Any]] = MappingProxyType({})
    thermal: bool = False


@dataclass(frozen=True, slots=True)
class _Weather:
    """PV und Hauslast je Phase einer Sekunde, als Ereignis."""

    pv_w: float
    house_w: tuple[float, float, float]

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        model.pv_w = self.pv_w
        model.house_w = self.house_w


@dataclass(frozen=True, slots=True)
class _Outage:
    """Beginn (``begins``) oder Ende eines Ausfalls aus ``model.OUTAGES``, als Ereignis."""

    name: str
    begins: bool

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        if self.begins:
            model.outages.add(self.name)
        else:
            model.outages.discard(self.name)


def _new_version(
    rt: CoreRuntime, version: int, vehicle: str, change: Mapping[str, Any], event: str
) -> None:
    """Reicht die gültige Absicht als Version ``version`` ein, mit dem Eintrag des Fahrzeugs
    ``vehicle`` um ``change`` geändert (``event`` nennt das Ereignis in den Fehlern).

    Sonst bleibt die gültige Absicht, wie sie ist, auch ihre Betriebsarten. Ohne gültige Absicht
    und bei einer Ablehnung bricht der Lauf ab (``ValueError``).
    """
    current = rt.intent()
    if current is None:
        raise ValueError(f"{event} ohne gültige Absicht.")
    entry = current.vehicles.get(vehicle, VehicleIntent())
    vehicles = current.vehicles | {vehicle: entry.model_copy(update=change)}
    reply = rt.submit_intent(current.model_copy(update={"version": version, "vehicles": vehicles}))
    if reply.result == "rejected":
        raise ValueError(f"{event} abgelehnt: {' '.join(reply.details)}")


@dataclass(frozen=True, slots=True)
class _Override:
    """Eine neue Version der Absicht: Das Fahrzeug ``vehicle`` hängt an der Wallbox ``wallbox``.

    Sonst bleibt die gültige Absicht, wie sie ist, auch ihre Betriebsarten. Ohne gültige Absicht
    und bei einer Ablehnung bricht der Lauf ab (``ValueError``).
    """

    version: int
    vehicle: str
    wallbox: str

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        _new_version(rt, self.version, self.vehicle, {"wallbox": self.wallbox}, "Übersteuerung")


@dataclass(frozen=True, slots=True)
class _Boost:
    """Eine neue Version der Absicht: Das Fahrzeug ``vehicle`` lädt in Boost bis ``until``.

    Das Ende ist absolut (UTC), denn ein Ereignis kennt den Beginn des Szenarios nicht; das
    Szenario rechnet es aus seinem ``start``. Sonst wie ``_Override``: Die gültige Absicht bleibt,
    wie sie ist; ohne gültige Absicht und bei einer Ablehnung bricht der Lauf ab (``ValueError``).
    """

    version: int
    vehicle: str
    until: datetime

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        _new_version(rt, self.version, self.vehicle, {"boost_until": self.until}, "Boost")


@dataclass(frozen=True, slots=True)
class _Mode:
    """Eine neue Version der Absicht: Das Fahrzeug ``vehicle`` lädt im Modus ``mode``.

    Sonst wie ``_Override``: Die gültige Absicht bleibt, wie sie ist; ohne gültige Absicht und bei
    einer Ablehnung bricht der Lauf ab (``ValueError``).
    """

    version: int
    vehicle: str
    mode: ChargeMode

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        _new_version(rt, self.version, self.vehicle, {"mode": self.mode}, "Moduswechsel")


@dataclass(frozen=True, slots=True)
class _Target:
    """Eine neue Version der Absicht: Das Fahrzeug ``vehicle`` hat das Ziel ``target`` (Ziel-SOC
    in %) und die Abfahrt ``departure`` (``HH:MM``, Wiener Zeit), wie aus der app (Plan 4b).

    Sonst wie ``_Override``: Die gültige Absicht bleibt, wie sie ist; ohne gültige Absicht und bei
    einer Ablehnung bricht der Lauf ab (``ValueError``).
    """

    version: int
    vehicle: str
    target: int
    departure: str

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        change = {"target_soc_pct": self.target, "departure": self.departure}
        _new_version(rt, self.version, self.vehicle, change, "Ziel")


PlanSlots = tuple[tuple[int, BatteryIntent, float | None], ...]


@dataclass(frozen=True, slots=True)
class _Plan:
    """Eine neue Version der Absicht mit einem Plan, wie ihn die app schickt (Plan 3c,
    Präzisierung 15).

    ``slots`` nennt je Slot den Versatz in Viertelstunden ab der Viertelstunde, in der das
    Szenario beginnt (``start``), die Absicht und ``budget_kw``; ``vehicles`` die Ladeleistung je
    Fahrzeug in W, gleich in jedem Slot (Plan 4b; ohne Angabe kein Auto), ``thermal`` den Hinweis
    für die Klimaanlage, gleich in jedem Slot (Plan 6a). Für Akku ins Auto (Plan Akku ins Auto)
    nennt ``vehicles_battery`` je Fahrzeug den Akkuanteil in W (``ev_battery_w``), ``now_battery``
    erlaubt Sofort übrigen Akku und ``support_soc_pct`` ist die Plan-Schwelle, alle drei gleich in
    jedem Slot; ``soc_end_pct`` nennt je Slot den geplanten SOC am Ende, in der Reihenfolge von
    ``slots`` (ohne Angabe keiner). Der Plan entsteht in der
    Sekunde ``at_s`` des Laufs: Das ist sein ``created_at`` und seine ``id``, und er gilt
    ``valid_s`` danach. Beginn und Sekunde kennt ein Ereignis nicht; das Szenario gibt sie mit
    (``_plan``).

    Die Version ist die nächste nach der gültigen. Sonst bleibt die gültige Absicht, wie sie ist,
    auch Boost und Übersteuerung. Ohne gültige Absicht und bei einer Ablehnung bricht der Lauf ab
    (``ValueError``); einen geklemmten Plan nimmt der core an.
    """

    start: datetime
    at_s: int
    slots: PlanSlots
    _: KW_ONLY
    valid_s: int
    grid_charge_allowed: bool = True
    vehicles: Mapping[str, int] = MappingProxyType({})
    thermal: ThermalHint = "neutral"
    vehicles_battery: Mapping[str, int] = MappingProxyType({})
    now_battery: bool = False
    support_soc_pct: float | None = None
    soc_end_pct: Sequence[float] | None = None

    def plan(self) -> IntentPlan:
        created = self.start + timedelta(seconds=self.at_s)
        first = slot_start(self.start)
        ends = self.soc_end_pct
        if ends is not None and len(ends) != len(self.slots):
            raise ValueError(f"{len(ends)} SOC am Ende für {len(self.slots)} Slots.")
        return IntentPlan(
            id=self.at_s,
            created_at=created,
            valid_until=created + timedelta(seconds=self.valid_s),
            grid_charge_allowed=self.grid_charge_allowed,
            slots=[
                IntentPlanSlot(
                    start=first + offset * SLOT,
                    intent=intent,
                    budget_kw=budget_kw,
                    vehicles=dict(self.vehicles),
                    thermal=self.thermal,
                    ev_battery_w=dict(self.vehicles_battery),
                    now_battery=self.now_battery,
                    support_soc_pct=self.support_soc_pct,
                    soc_end_pct=None if ends is None else ends[index],
                )
                for index, (offset, intent, budget_kw) in enumerate(self.slots)
            ],
        )

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        _submit_plan(rt, self.plan())


def _submit_plan(rt: CoreRuntime, plan: IntentPlan) -> None:
    """Die nächste Version der gültigen Absicht mit ``plan``; ohne gültige Absicht und bei einer
    Ablehnung bricht der Lauf ab (``ValueError``)."""
    current = rt.intent()
    if current is None:
        raise ValueError("Plan ohne gültige Absicht.")
    update = {"version": current.version + 1, "plan": plan}
    reply = rt.submit_intent(current.model_copy(update=update))
    if reply.result == "rejected":
        raise ValueError(f"Plan abgelehnt: {' '.join(reply.details)}")


def _plan(
    start: datetime,
    at_s: int,
    slots: PlanSlots,
    *,
    valid_s: int,
    grid_charge_allowed: bool = True,
    vehicles: Mapping[str, int] = MappingProxyType({}),
    thermal: ThermalHint = "neutral",
    vehicles_battery: Mapping[str, int] = MappingProxyType({}),
    now_battery: bool = False,
    support_soc_pct: float | None = None,
    soc_end_pct: Sequence[float] | None = None,
) -> tuple[int, Action]:
    """Das Ereignis ``_Plan`` in der Sekunde ``at_s`` eines Szenarios ab ``start``."""
    plan = _Plan(
        start,
        at_s,
        slots,
        valid_s=valid_s,
        grid_charge_allowed=grid_charge_allowed,
        vehicles=vehicles,
        thermal=thermal,
        vehicles_battery=vehicles_battery,
        now_battery=now_battery,
        support_soc_pct=support_soc_pct,
        soc_end_pct=None if soc_end_pct is None else tuple(soc_end_pct),
    )
    return at_s, plan


def _every_quarter(
    start: datetime,
    duration_s: int,
    intent: BatteryIntent,
    *,
    budget_kw: float | None = None,
    vehicles: Mapping[str, int] = MappingProxyType({}),
    thermal: ThermalHint = "neutral",
) -> tuple[tuple[int, Action], ...]:
    """Pläne wie von der app: einer zu Beginn, dann zu jeder Viertelstunde des Laufs, je mit
    ``PLAN_SLOTS`` Slots ``intent`` (und ``budget_kw``, Ladeleistung ``vehicles``, Hinweis
    ``thermal``) ab seiner Viertelstunde, gültig für ``PLAN_VALID_S``."""
    into = int((start - slot_start(start)).total_seconds())
    seconds = sorted({0, *range((QUARTER_S - into) % QUARTER_S, duration_s, QUARTER_S)})
    return tuple(
        _plan(
            start,
            t,
            tuple(((into + t) // QUARTER_S + k, intent, budget_kw) for k in range(PLAN_SLOTS)),
            valid_s=PLAN_VALID_S,
            vehicles=vehicles,
            thermal=thermal,
        )
        for t in seconds
    )


@dataclass(frozen=True, slots=True)
class _SelfStart:
    """Der Tesla ``vehicle`` startet selbst, etwa aus der App: „Laden an“ im Auto, als Ereignis.

    Der core sendet nichts dazu; das Auto lädt, sobald die Wallbox Strom gibt.
    """

    vehicle: str

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        model.teslas[self.vehicle].charge_enabled = True


@dataclass(frozen=True, slots=True)
class _Unplug:
    """Das Fahrzeug ``vehicle`` wird abgesteckt, als Ereignis."""

    vehicle: str

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        model.unplug(self.vehicle)


def sun_w(at: datetime) -> float:
    """Die PV eines sonnigen Tages zur Zeit ``at``: ein halber Sinus von Auf- bis Untergang."""
    local = at.astimezone(LOCAL_TZ)
    hours = local.hour + local.minute / 60 + local.second / 3600
    since_rise = hours - (SOLAR_NOON_H - DAYLIGHT_H / 2)
    if not 0 < since_rise < DAYLIGHT_H:
        return 0.0
    return SUN_PEAK_W * math.sin(math.pi * since_rise / DAYLIGHT_H)


def _clouds(seed: int, duration_s: int) -> list[float]:
    """Je Sekunde der Anteil der Sonne: ``CLOUD_FACTORS`` im Wechsel, sonnig zuerst."""
    rng = random.Random(f"wolken-{seed}")  # noqa: S311 - Saat der Simulation, kein Geheimnis
    factors: list[float] = []
    switches = 0
    while len(factors) < duration_s:
        factors += [CLOUD_FACTORS[switches % 2]] * rng.randint(*CLOUD_SWITCH_S)
        switches += 1
    return factors[:duration_s]


def _dark(t: int) -> float:
    return 0.0


def _weather(
    start: datetime,
    duration_s: int,
    seed: int,
    *,
    pv: Callable[[int], float] = _dark,
    l1: Callable[[int], float] = _dark,
    each: Callable[[int], float] = _dark,
    noise_w: float = HOUSE_NOISE_W,
) -> tuple[tuple[int, Action], ...]:
    """Je Sekunde ``t`` ein ``_Weather``: PV ``pv(t)`` und die Hauslast mit dem Rauschen
    ± ``noise_w`` je Phase, auf jeder Phase mit ``each(t)``, auf L1 dazu mit ``l1(t)``."""
    rng = random.Random(f"haus-{seed}")  # noqa: S311 - Saat der Simulation, kein Geheimnis

    def house(t: int) -> float:
        return HOUSE_W + each(t) + rng.uniform(-noise_w, noise_w)

    return tuple(
        (t, _Weather(pv(t), (house(t) + l1(t), house(t), house(t)))) for t in range(duration_s)
    )


def _sun(start: datetime) -> Callable[[int], float]:
    """Die Sonne ``t`` Sekunden nach ``start``."""

    def pv(t: int) -> float:
        return sun_w(start + timedelta(seconds=t))

    return pv


def _cloudy(start: datetime, duration_s: int, seed: int) -> Callable[[int], float]:
    """Die Sonne mit Wolken (``_clouds``), ``t`` Sekunden nach ``start``."""
    sun, clouds = _sun(start), _clouds(seed, duration_s)

    def pv(t: int) -> float:
        return sun(t) * clouds[t]

    return pv


def _setup(
    model: PlantModel,
    *,
    soc_pct: float,
    tesla: str | None = WALL_CONNECTOR,
    egolf: str | None = EVCS_BOX,
    charger_only: bool = False,
    foreign: bool = False,
    tesla_soc_pct: float | None = None,
    tesla_amps: int | None = None,
    tesla_starts: bool = False,
    asleep: bool = False,
    dess: bool = False,
    guest: str | None = None,
    guest_phases: int = 3,
    away: frozenset[str] = frozenset(),
    tesla_elsewhere: bool = False,
    evcs_on: bool = False,
    tesla_limit_pct: float | None = None,
    egolf_soc_known: bool = True,
) -> None:
    """Akku, Multi, fremder Regler und Dynamic ESS (``dess``); der Tesla an der Wallbox ``tesla``,
    der e-Golf an ``egolf`` (``None``: abgesteckt).

    Für den Tesla, wenn angegeben: SOC ``tesla_soc_pct``, Strom ``tesla_amps`` und Ladelimit
    ``tesla_limit_pct`` (sonst ``TESLA_LIMIT_PCT``). Mit
    ``tesla_starts`` hat er „Laden an“ im Auto und lädt, sobald die Wallbox Strom gibt; mit
    ``asleep`` schläft er. Mit ``tesla_elsewhere`` steckt er woanders (``Tesla.elsewhere``): Tessie
    meldet ihn „Stopped“, obwohl er an keiner Wallbox steckt.

    Der Gast steckt an der Wallbox ``guest`` (``None``: abgesteckt) und lädt auf ``guest_phases``
    Phasen (1 bis 3). ``away`` nennt die Fahrzeuge, die Home Assistant nicht zu Hause meldet; ohne
    sind alle zu Hause. Mit ``evcs_on`` steht die EVCS ``EVCS_BOX`` schon vor dem Start des core auf
    „Laden an“ (ihr Sollstrom ist noch ihr sicherer Strom): Ein Auto an ihr lädt von der ersten
    Sekunde an. Mit ``egolf_soc_known`` falsch meldet Home Assistant den Ladestand des e-Golf
    nicht (``PlantModel.egolf_soc_known``).
    """
    model.soc_pct = soc_pct
    model.charger_only = charger_only
    model.foreign = foreign
    model.dess = dess
    car = model.teslas[TESLA]
    if tesla is not None:
        model.plug(TESLA, tesla)
    if tesla_soc_pct is not None:
        car.soc_pct = tesla_soc_pct
    if tesla_amps is not None:
        car.charge_amps = tesla_amps
    if tesla_limit_pct is not None:
        car.limit_pct = tesla_limit_pct
    car.charge_enabled = tesla_starts
    car.asleep = asleep
    car.elsewhere = tesla_elsewhere
    if egolf is not None:
        model.plug(EGOLF, egolf)
    model.cars[GUEST_CAR].phases = guest_phases
    if guest is not None:
        model.plug(GUEST_CAR, guest)
    model.away = set(away)
    model.egolf_soc_known = egolf_soc_known
    if evcs_on:
        box = model.wallboxes[EVCS_BOX]
        if not isinstance(box, Evcs):
            raise ValueError(f"Wallbox „{EVCS_BOX}“ ist keine EVCS.")
        box.start_stop = 1


def _intent(
    *,
    guest_mode: ChargeMode = ChargeMode.PV,
    limits: Mapping[str, int] | None = None,
    **modes: ChargeMode,
) -> Intent:
    """Die Absicht mit Regelbetrieb, den Modi der Fahrzeuge und dem Modus des Gastes. ``limits``
    nennt je Fahrzeug den Höchststrom in „Sofort“ (``now_max_a``); ohne Eintrag lädt es mit voller
    Leistung."""
    limits = limits or {}
    vehicles = {
        key: VehicleIntent(mode=mode, now_max_a=limits.get(key)) for key, mode in modes.items()
    }
    return Intent(version=1, control=True, vehicles=vehicles, guest=GuestIntent(mode=guest_mode))


# --- Prüfungen -----------------------------------------------------------------------------------


def _minutes(samples: Sequence[Sample], key: str) -> float:
    """So viele Minuten lädt das Auto ``key`` in ``samples``."""
    return sum(1 for s in samples if s.cars[key].charging) / MINUTE_S


def _one_after_the_other(result: SimResult, *, tesla_min: float, egolf_min: float) -> list[str]:
    """Erst lädt der Tesla bis „Complete“, mindestens ``tesla_min`` Minuten; danach lädt der
    e-Golf mindestens ``egolf_min`` Minuten. Der e-Golf startet erst, wenn der Tesla „Complete“
    erreicht hat."""
    samples = result.samples
    found: list[str] = []
    done = next((s.t for s in samples if s.cars[TESLA].complete), None)
    if done is None:
        found.append("„tesla“ erreicht „Complete“ nicht.")
    minutes = _minutes(samples, TESLA)
    if minutes < tesla_min:
        shown = shown_number(minutes, 1, lambda number: number >= tesla_min)
        found.append(f"„tesla“ lädt {shown} min, verlangt {de(tesla_min)} min.")
    started = next((s.t for s in samples if s.cars[EGOLF].charging), None)
    if started is not None and (done is None or started <= done):
        found.append(f"„egolf“ startet bei {started} s, bevor „tesla“ „Complete“ erreicht.")
    after = _minutes([s for s in samples if done is not None and s.t > done], EGOLF)
    if after < egolf_min:
        shown = shown_number(after, 1, lambda number: number >= egolf_min)
        found.append(f"„egolf“ lädt danach {shown} min, verlangt {de(egolf_min)} min.")
    return found


def _one_after_the_other_check(*, tesla_min: float, egolf_min: float) -> Check:
    text = (
        f"Erst lädt „tesla“ bis „Complete“, mindestens {de(tesla_min)} min; danach lädt „egolf“ "
        f"mindestens {de(egolf_min)} min."
    )
    return Check(text, partial(_one_after_the_other, tesla_min=tesla_min, egolf_min=egolf_min))


def _few_evcs_currents(result: SimResult) -> list[str]:
    """Höchstens ``EVCS_CURRENTS_PER_HOUR`` Strombefehle an die EVCS je Stunde, in der der e-Golf
    lädt (Hysterese, Design 6.5). Es zählen Entscheidungen mit Wert, nicht zurückgestellte oder
    abgelehnte. Lädt er nicht, gibt es nichts zu prüfen; das meldet ``_one_after_the_other``."""
    minutes = _minutes(result.samples, EGOLF)
    if minutes == 0:
        return []
    evcs = wallbox_actuator(EVCS_BOX)
    count = sum(
        1
        for d in result.decisions
        if d.actuator == evcs and d.kind is CommandKind.CURRENT and d.result not in _HELD_BACK
    )
    per_hour = count * MINUTE_S / minutes
    if per_hour <= EVCS_CURRENTS_PER_HOUR:
        return []
    shown = shown_number(per_hour, 1, lambda number: number <= EVCS_CURRENTS_PER_HOUR)
    return [
        f"{count} Strombefehle an die EVCS in {de(round(minutes, 1))} min Laden von „egolf“: "
        f"{shown} je Stunde, verlangt höchstens {EVCS_CURRENTS_PER_HOUR}."
    ]


def _evcs_lower_during_the_peak(result: SimResult) -> list[str]:
    """Ab ``PEAK_REACTION_S`` nach Beginn der Spitze liegt der Strom der EVCS unter dem davor."""
    begin, end = PEAK_S
    before = [s.cars[EGOLF].current_a for s in result.samples if s.t == begin - 1]
    during = [
        s.cars[EGOLF].current_a for s in result.samples if begin + PEAK_REACTION_S <= s.t < end
    ]
    if not before or not during:
        return ["Die Lastspitze liegt nicht im Lauf."]
    if max(during) < before[0]:
        return []
    return [f"EVCS-Strom während der Spitze bis {de(max(during))} A, davor {de(before[0])} A."]


def _named(code: str, *, at: str | None = None) -> Check:
    """Mindestens eine Entscheidung nennt den Grund ``code``, mit ``at`` eine an diesen Aktor."""
    where = "" if at is None else f" an „{at}“"

    def find(result: SimResult) -> list[str]:
        if any(code in d.codes and at in (None, d.actuator) for d in result.decisions):
            return []
        return [f"Keine Entscheidung{where} mit „{code}“."]

    return Check(f"Eine Entscheidung{where} nennt „{code}“.", find)


def _reserve_kept(result: SimResult) -> list[str]:
    """Der SOC sinkt nie unter ``RESERVE_PCT``: eine Invariante des Modells, keine Prüfung der
    Regelung. Am Netz entlädt das Modell den Akku nie unter die USV-Reserve (``PlantModel``, ESS
    und Peak Shaving), was immer die Regelung tut; scheitern kann die Prüfung nur an einem Fehler
    des Modells. Ob die Regelung die Reserve schützt, prüft ``_import_target_kept``."""
    lowest = min((s.soc_pct for s in result.samples), default=RESERVE_PCT)
    if lowest < RESERVE_PCT:
        shown = de(round(lowest, 4))
        return [f"SOC bis {shown} %, unter der USV-Reserve von {de(RESERVE_PCT)} %."]
    return []


def _import_target_kept(result: SimResult) -> list[str]:
    """Unter der Reserve-Absicherung hält die Regelung das Importziel (Design 5.2), auch in Sofort.

    Solange der SOC unter USV-Reserve + ``control.reserve_guard_pct`` liegt und ein Auto live über
    seinem Mindeststrom lädt, liegt außerhalb von Übergängen (``TRANSITION_S``) keine Phase mit der
    Last ohne Akku-Hilfe über dem Ziel am Importlimit samt Toleranz
    (``Metrics.reserve_over_target_s``). An der Anlage zöge Peak Shaving den Akku sonst unter die
    Reserve; im Modell hält der Akku sie selbst ein (``_reserve_kept``), darum prüft erst diese
    Prüfung die Regelung.
    """
    longest = result.metrics.reserve_over_target_s
    if longest <= TRANSITION_S:
        return []
    return [
        f"Unter der Reserve-Absicherung {longest} s am Stück über dem Importziel, verlangt "
        f"höchstens {TRANSITION_S} s."
    ]


def _multi_mode_warns(result: SimResult) -> list[str]:
    """Die Selbstprüfung ``multi_mode`` warnt."""
    warned = any(
        check["id"] == "multi_mode" and check["status"] == "warn"
        for _, health in result.health
        for check in health.selfcheck
    )
    return [] if warned else ["Die Selbstprüfung „multi_mode“ warnt nicht."]


def _charges_with(key: str, amps: float) -> Check:
    """Das Auto ``key`` lädt in der letzten Sekunde des Laufs mit ``amps`` je Phase."""

    def find(result: SimResult) -> list[str]:
        last = result.samples[-1].cars[key] if result.samples else None
        if last is None or not last.charging:
            return [f"„{key}“ lädt am Ende nicht, verlangt {de(amps)} A."]
        if last.current_a != amps:
            return [f"„{key}“ lädt am Ende mit {de(last.current_a)} A, verlangt {de(amps)} A."]
        return []

    return Check(f"„{key}“ lädt am Ende mit {de(amps)} A.", find)


def _charges_at_the_end(key: str) -> Check:
    """Das Auto ``key`` lädt in der letzten Sekunde des Laufs."""

    def find(result: SimResult) -> list[str]:
        if result.samples and result.samples[-1].cars[key].charging:
            return []
        return [f"„{key}“ lädt am Ende nicht."]

    return Check(f"„{key}“ lädt am Ende.", find)


def _one_wake(key: str) -> Check:
    """Genau ein Weckvorgang: eine Entscheidung mit dem Grund ``wake`` und einem Wert, also weder
    abgelehnt noch zurückgestellt."""
    actuator = vehicle_actuator(key)

    def find(result: SimResult) -> list[str]:
        wakes = [
            d
            for d in result.decisions
            if d.actuator == actuator and WAKE in d.codes and d.result not in _HELD_BACK
        ]
        if len(wakes) == 1:
            return []
        return [f"{len(wakes)} Weckvorgänge an „{key}“, verlangt genau einer."]

    return Check(f"Genau ein Weckvorgang an „{key}“.", find)


def _safe_during(name: str, begin_min: int, end_min: int) -> Check:
    """Jede Gesundheit während des Ausfalls ``name`` zeigt SAFE, und es gibt mindestens eine."""
    begin, end = begin_min * MINUTE_S, end_min * MINUTE_S

    def find(result: SimResult) -> list[str]:
        during = [(t, health.mode) for t, health in result.health if begin <= t < end]
        if not during:
            return [f"Der Ausfall „{name}“ liegt nicht im Lauf."]
        return [
            f"Während des Ausfalls „{name}“ bei {t} s {mode}, verlangt SAFE."
            for t, mode in during
            if mode != SAFE
        ][:1]

    return Check(f"SAFE während des Ausfalls „{name}“ ({begin_min}–{end_min} min).", find)


def _all_charge_from(begin_s: int) -> Check:
    """Ab ``begin_s`` lädt jedes Fahrzeug der Konfiguration in jeder Sekunde des Laufs. Der Gast
    (``GUEST_CAR``) steht in jeder Probe, ist aber kein Fahrzeug der Konfiguration und zählt nicht:
    Abgesteckt lädt er nie."""

    def find(result: SimResult) -> list[str]:
        after = [s for s in result.samples if s.t >= begin_s]
        if not after:
            return [f"Der Lauf endet vor {begin_s} s."]
        found: list[str] = []
        for key in [key for key in after[0].cars if key != GUEST_CAR]:
            idle = next((s.t for s in after if not s.cars[key].charging), None)
            if idle is not None:
                found.append(f"„{key}“ lädt bei {idle} s nicht.")
        return found

    return Check(f"Ab {de(begin_s / MINUTE_S)} min laden beide Autos.", find)


def _shown_before(key: str, kind: str, until_s: int) -> Check:
    """Vor ``until_s`` zeigt die Gesundheit für ``key`` mindestens einmal die Zuordnung ``kind``,
    ``UNCLEAR`` oder ``DETECTED``."""
    word = _SHOWN[kind]

    def find(result: SimResult) -> list[str]:
        shown = any(
            vehicle["id"] == key and vehicle["assignment"] == kind
            for t, health in result.health
            if t < until_s
            for vehicle in health.vehicles
        )
        return [] if shown else [f"Die Gesundheit zeigt „{key}“ vor {until_s} s nie „{word}“."]

    return Check(f"Die Gesundheit zeigt „{key}“ vor {de(until_s / MINUTE_S)} min „{word}“.", find)


def _one_current(key: str, amps: float) -> Check:
    """Genau eine Entscheidung über den Strom ``amps`` an das Fahrzeug ``key``; eine
    zurückgestellte zählt nicht, denn ihr Befehl kommt danach noch einmal, und eine fremde
    Änderung auch nicht."""
    actuator = vehicle_actuator(key)

    def find(result: SimResult) -> list[str]:
        found = [
            d
            for d in result.decisions
            if d.actuator == actuator
            and d.kind is CommandKind.CURRENT
            and d.requested == amps
            and d.result not in _NOT_THE_COMMAND
        ]
        if len(found) == 1:
            return []
        return [
            f"{len(found)} Entscheidungen „Strom {de(amps)} A“ an „{key}“, verlangt genau eine."
        ]

    return Check(f"Genau eine Entscheidung „Strom {de(amps)} A“ an „{key}“.", find)


def _evcs_off_from(key: str, begin_s: int) -> Check:
    """Ab ``begin_s`` lädt die EVCS ``key`` nicht: Kein Auto lädt, ihr letzter Befehl „Laden“
    mit Wert davor ist „aus“, und danach kommt kein „Laden an“.

    Die Proben kennen Autos, nicht Ladepunkte: „Kein Auto lädt“ heißt darum gar keines, auch an
    einer anderen Wallbox. Das passt nur zu Szenarien, in denen ab ``begin_s`` an keiner anderen
    Wallbox ein Auto lädt, wie in „zuordnung“: Dort steckt der Tesla an keiner Wallbox, der e-Golf
    ab ``UNPLUG_S`` nicht mehr."""
    actuator = wallbox_actuator(key)
    valued = frozenset({DecisionResult.OK, DecisionResult.DRY_RUN})

    def find(result: SimResult) -> list[str]:
        found: list[str] = []
        charging = next((s.t for s in result.samples if s.t >= begin_s and _any_charging(s)), None)
        if charging is not None:
            found.append(f"Bei {charging} s lädt ein Auto.")
        switches = [
            (d.ts, d.applied)
            for d in result.decisions
            if d.actuator == actuator and d.kind is CommandKind.CHARGING and d.result in valued
        ]
        moment = result.scenario.start + timedelta(seconds=begin_s)
        before = [value for ts, value in switches if ts <= moment]
        if not before or before[-1] is not False:
            found.append(f"Die EVCS „{key}“ ist bei {begin_s} s nicht auf „Laden aus“.")
        if any(value is True for ts, value in switches if ts > moment):
            found.append(f"Die EVCS „{key}“ bekommt nach {begin_s} s „Laden an“.")
        return found

    return Check(f"Ab {de(begin_s / MINUTE_S)} min lädt die EVCS „{key}“ nicht.", find)


def _stops_within(actuator: str, at_s: int, within_s: int) -> Check:
    """Der Aktor ``actuator`` bekommt ab der Sekunde ``at_s`` binnen ``within_s`` s „Laden aus“:
    eine Entscheidung über ``charging`` mit dem Wert falsch, die ein Befehl war (nicht abgelehnt,
    zurückgestellt oder eine fremde Änderung); auch ein Dry-Run oder ein Fehlschlag zählt, denn der
    Befehl ging hinaus."""

    def find(result: SimResult) -> list[str]:
        begin = result.scenario.start + timedelta(seconds=at_s)
        end = begin + timedelta(seconds=within_s)
        stopped = any(
            d.actuator == actuator
            and d.kind is CommandKind.CHARGING
            and d.requested is False
            and d.result not in _HELD_BACK
            and begin <= d.ts <= end
            for d in result.decisions
        )
        if stopped:
            return []
        return [f"„{actuator}“ bekam nach {at_s} s kein „Laden aus“ binnen {within_s} s."]

    text = f"„{actuator}“ bekommt „Laden aus“ binnen {within_s} s nach {de(at_s / MINUTE_S)} min."
    return Check(text, find)


def _no_deferred(actuator: str, kind: CommandKind) -> Check:
    """Keine zurückgestellte Entscheidung der Art ``kind`` an den Aktor ``actuator``: Ein
    Stundenlimit hielt keinen solchen Befehl zurück."""

    def find(result: SimResult) -> list[str]:
        deferred = [
            d
            for d in result.decisions
            if d.actuator == actuator and d.kind is kind and d.result is DecisionResult.DEFERRED
        ]
        if not deferred:
            return []
        first = _second(result, min(d.ts for d in deferred))
        return [
            f"{len(deferred)} zurückgestellte Befehle „{kind.value}“ an „{actuator}“, "
            f"der erste bei {first} s."
        ]

    return Check(f"Kein zurückgestellter Befehl „{kind.value}“ an „{actuator}“.", find)


def _idle_from(key: str, begin_s: int) -> Check:
    """Das Auto ``key`` lädt in keiner Sekunde ab ``begin_s``; der Befund nennt die erste."""

    def find(result: SimResult) -> list[str]:
        charging = next(
            (s.t for s in result.samples if s.t >= begin_s and s.cars[key].charging), None
        )
        return [] if charging is None else [f"„{key}“ lädt bei {charging} s."]

    return Check(f"Ab {de(begin_s / MINUTE_S)} min lädt „{key}“ nicht.", find)


def _any_charging(sample: Sample) -> bool:
    return any(car.charging for car in sample.cars.values())


def _dry_runs_at(*actuators: str) -> Check:
    """Mindestens eine Dry-Run-Entscheidung an jedem der ``actuators``."""

    def find(result: SimResult) -> list[str]:
        decided = {d.actuator for d in result.decisions if d.result is DecisionResult.DRY_RUN}
        return [f"Keine Dry-Run-Entscheidung an „{a}“." for a in actuators if a not in decided]

    shown = " und ".join(f"„{actuator}“" for actuator in actuators)
    return Check(f"Mindestens eine Dry-Run-Entscheidung an {shown}.", find)


def _no_repeated_dry_runs(result: SimResult) -> list[str]:
    repeated = result.metrics.repeated_dry_runs
    return (
        []
        if repeated == 0
        else [f"Wiederholte Dry-Run-Entscheidungen: {repeated}, verlangt keine."]
    )


def _no_foreign_alarm(result: SimResult) -> list[str]:
    """Keine Gesundheit nennt einen Alarm ``competitor.foreign.*``."""
    for t, health in result.health:
        for alarm in health.alarms:
            if alarm["id"].startswith(FOREIGN_ALARM):
                return [f"Alarm „{alarm['id']}“ bei {t} s."]
    return []


# --- Prüfungen für Akku und Spitzen --------------------------------------------------------------


def _second(result: SimResult, ts: datetime) -> int:
    """Die Sekunde des Laufs, in der der Zeitpunkt ``ts`` liegt."""
    return math.floor((ts - result.scenario.start).total_seconds())


def _hhmm(start: datetime, t: int) -> str:
    """Die Sekunde ``t`` eines Laufs ab ``start`` als Wiener Uhrzeit, etwa „18:45“."""
    return f"{(start + timedelta(seconds=t)).astimezone(LOCAL_TZ):%H:%M}"


def _at(decisions: Sequence[Decision], actuator: str) -> list[Decision]:
    return [d for d in decisions if d.actuator == actuator]


def _above(
    samples: Sequence[Sample], value: Callable[[Sample], float], limit: float, what: str
) -> list[str]:
    """Ein Satz, wenn ``value`` in einer der ``samples`` über ``limit`` W liegt: die erste solche
    Sekunde, wie viele es sind und der höchste Wert, auf ganze W gerundet (``shown_number``)."""
    over = [(s.t, value(s)) for s in samples if value(s) > limit]
    if not over:
        return []
    (t, watts), (_, worst) = over[0], max(over, key=lambda found: found[1])

    def shown(found: float) -> str:
        return shown_number(found, 0, lambda number: number <= limit)

    return [
        f"{what} bei {t} s {shown(watts)} W, verlangt höchstens {de(limit)} W "
        f"({len(over)} s darüber, bis {shown(worst)} W)."
    ]


def _from_battery_at_most(key: str, limit_pct: float) -> Check:
    """Höchstens ``limit_pct`` der Energie des Autos ``key`` kommen aus dem Akku
    (``battery_into_car_pct``, ohne Übergänge). Lädt es nie, ist das ein Verstoß: Die Prüfung
    hätte nichts gemessen."""

    def find(result: SimResult) -> list[str]:
        pct = battery_into_car_pct(result.samples, key)
        if pct is None:
            return [f"„{key}“ lädt nie."]
        if pct <= limit_pct:
            return []
        shown = shown_number(pct, 2, lambda number: number <= limit_pct)
        return [f"„{key}“ lädt zu {shown} % aus dem Akku, verlangt höchstens {de(limit_pct)} %."]

    text = (
        f"Höchstens {de(limit_pct)} % der Energie von „{key}“ kommen aus dem Akku (ohne Übergänge)."
    )
    return Check(text, find)


def _battery_into_cars_w(sample: Sample) -> float:
    """Was der Akku in die Autos liefert: ``max(0, Entladung − Haus)``, ohne PV."""
    return max(0.0, max(0.0, -sample.battery_w) - sample.house_w)


def _support_then_grid(result: SimResult) -> list[str]:
    """Der Akku liefert in die Autos ab ``SUPPORT_FROM_S`` nach dem ersten Laden bis
    ``SUPPORT_UNTIL_S`` danach höchstens ``SUPPORT_W``, ab ``GRID_ONLY_S`` danach höchstens
    ``GRID_ONLY_W`` (``_battery_into_cars_w``). Das erste Laden ist die erste Sekunde, in der ein
    Auto lädt."""
    samples = result.samples
    first = next((s.t for s in samples if _any_charging(s)), None)
    if first is None:
        return ["Kein Auto lädt."]
    begin, end = first + SUPPORT_FROM_S, first + SUPPORT_UNTIL_S
    support = [s for s in samples if begin <= s.t <= end]
    grid = [s for s in samples if s.t >= first + GRID_ONLY_S]
    if not grid:
        return [f"Der Lauf endet vor {first + GRID_ONLY_S} s."]
    what = "Akku in die Autos"
    return [
        *_above(support, _battery_into_cars_w, SUPPORT_W, what),
        *_above(grid, _battery_into_cars_w, GRID_ONLY_W, what),
    ]


def _grid_at_the_setpoint_cap(result: SimResult) -> list[str]:
    """Ab ``SETTLED_S`` liegt der Netzbezug in jeder Sekunde, in der Autos und Haus mehr als
    ``LOADS_ABOVE_W`` ziehen, in ``GRID_BETWEEN_W``; es muss solche Sekunden geben."""
    low, high = GRID_BETWEEN_W
    loaded = [
        s for s in result.samples if s.t >= SETTLED_S and cars_w(s) + s.house_w > LOADS_ABOVE_W
    ]
    if not loaded:
        return [f"Autos und Haus ziehen ab {SETTLED_S} s nie mehr als {de(LOADS_ABOVE_W)} W."]
    outside = [s for s in loaded if not low <= s.grid_w <= high]
    if not outside:
        return []
    first = outside[0]
    shown = shown_number(first.grid_w, 0, lambda number: low <= number <= high)
    return [
        f"Netzbezug bei {first.t} s {shown} W, verlangt {de(low)} bis {de(high)} W "
        f"({len(outside)} s außerhalb)."
    ]


def _no_victron_number(result: SimResult) -> list[str]:
    """Keine Entscheidung an Victron nennt eine Zahl, also einen Override."""
    for d in _at(result.decisions, VICTRON):
        watts = finite_number(d.requested)
        if watts is not None:
            return [f"Entscheidung an „{VICTRON}“ bei {_second(result, d.ts)} s: {de(watts)} W."]
    return []


def _battery_carries_the_house(result: SimResult) -> list[str]:
    """Ab ``SETTLED_S`` liegt der Netzbezug höchstens bei ``BOOST_GRID_W``, solange der SOC über
    ``BOOST_SOC_PCT`` liegt; es muss solche Sekunden geben."""
    above = [s for s in result.samples if s.t >= SETTLED_S and s.soc_pct > BOOST_SOC_PCT]
    if not above:
        return [f"Der SOC liegt ab {SETTLED_S} s nie über {de(BOOST_SOC_PCT)} %."]
    return _above(above, lambda s: s.grid_w, BOOST_GRID_W, "Netzbezug")


def _override_kept(result: SimResult) -> list[str]:
    """Ab ``SETTLED_S`` gilt in jeder Probe ein Override, zuletzt vor höchstens
    ``OVERRIDE_AGE_S`` geschrieben; gemeldet wird der erste Verstoß."""
    after = [s for s in result.samples if s.t >= SETTLED_S]
    if not after:
        return [f"Der Lauf endet vor {SETTLED_S} s."]
    for s in after:
        if s.override_w is None or s.override_age_s is None:
            return [f"Bei {s.t} s gilt kein Override."]
        if s.override_age_s > OVERRIDE_AGE_S:
            age = de(round(s.override_age_s))
            return [
                f"Bei {s.t} s ist der Override vor {age} s geschrieben, verlangt höchstens "
                f"{de(OVERRIDE_AGE_S)} s."
            ]
    return []


def _few_victron_decisions(result: SimResult) -> list[str]:
    """Höchstens ``VICTRON_DECISIONS`` Entscheidungen an Victron."""
    count = len(_at(result.decisions, VICTRON))
    if count <= VICTRON_DECISIONS:
        return []
    return [f"{count} Entscheidungen an „{VICTRON}“, verlangt höchstens {VICTRON_DECISIONS}."]


def _no_victron_decision(result: SimResult) -> list[str]:
    """Keine Entscheidung an Victron; gemeldet wird die erste."""
    found = _at(result.decisions, VICTRON)
    if not found:
        return []
    first = found[0]
    watts = finite_number(first.requested)
    shown = "kein Override" if watts is None else f"{de(watts)} W"
    return [
        f"Entscheidungen an „{VICTRON}“: {len(found)}, die erste bei "
        f"{_second(result, first.ts)} s ({shown})."
    ]


def _battery_locked(result: SimResult) -> list[str]:
    """Ab ``LOCKED_FROM_S`` zeigt jede Gesundheit den Akku-Sollwert gesperrt (``locked``)."""
    states = [(t, health.battery.get("state")) for t, health in result.health if t >= LOCKED_FROM_S]
    if not states:
        return [f"Der Lauf endet vor {LOCKED_FROM_S} s."]
    for t, state in states:
        if state != "locked":
            shown = "ohne Wert" if state is None else f"„{state}“"
            return [f"„battery.state“ bei {t} s {shown}, verlangt „locked“."]
    return []


def _quarters_within(start: datetime, limit_kwh: float, low_kwh: float | None = None) -> Check:
    """Der Netzbezug der ``PEAK_QUARTERS`` Viertelstunden ab ``PEAK_LEAD_S`` liegt je höchstens
    bei ``limit_kwh``, mit ``low_kwh`` auch mindestens dort: ``Σ max(0, Netzbezug) · 1 s`` über
    die Proben der Viertelstunde. ``start`` ist der Beginn des Szenarios, für die Uhrzeiten im
    Text."""
    begins = [PEAK_LEAD_S + k * QUARTER_S for k in range(PEAK_QUARTERS)]  # Sekunden des Laufs

    def find(result: SimResult) -> list[str]:
        found: list[str] = []
        for begin in begins:
            shown = _hhmm(result.scenario.start, begin)
            inside = [s for s in result.samples if begin <= s.t < begin + QUARTER_S]
            if len(inside) < QUARTER_S:
                found.append(f"Die Viertelstunde ab {shown} liegt nicht ganz im Lauf.")
                continue
            kwh = sum(max(0.0, s.grid_w) for s in inside) / 3_600_000
            if kwh > limit_kwh:
                at_most = shown_number(kwh, 3, lambda number: number <= limit_kwh)
                found.append(
                    f"Netzbezug der Viertelstunde ab {shown}: {at_most} kWh, verlangt "
                    f"höchstens {de(limit_kwh)} kWh."
                )
            if low_kwh is not None and kwh < low_kwh:
                at_least = shown_number(kwh, 3, lambda number: number >= low_kwh)
                found.append(
                    f"Netzbezug der Viertelstunde ab {shown}: {at_least} kWh, verlangt "
                    f"mindestens {de(low_kwh)} kWh."
                )
        return found

    *first, last = (_hhmm(start, begin) for begin in begins)
    bound = (
        f"höchstens {de(limit_kwh)}"
        if low_kwh is None
        else f"zwischen {de(low_kwh)} und {de(limit_kwh)}"
    )
    text = f"Netzbezug der Viertelstunden ab {', '.join(first)} und {last} je {bound} kWh."
    return Check(text, find)


def _named_before(code: str, until_s: int, start: datetime) -> Check:
    """Vor ``until_s`` nennt mindestens eine Entscheidung den Grund ``code``. ``start`` ist der
    Beginn des Szenarios, für die Uhrzeit im Text."""

    def find(result: SimResult) -> list[str]:
        if any(code in d.codes and _second(result, d.ts) < until_s for d in result.decisions):
            return []
        return [f"Keine Entscheidung mit „{code}“ vor {until_s} s."]

    return Check(f"Eine Entscheidung vor {_hhmm(start, until_s)} nennt „{code}“.", find)


def _not_named_from(code: str, actuator: str, from_s: int, start: datetime) -> Check:
    """Ab ``from_s`` nennt keine Entscheidung an ``actuator`` den Grund ``code``. ``start`` ist der
    Beginn des Szenarios, für die Uhrzeit im Text."""

    def find(result: SimResult) -> list[str]:
        found = [
            d
            for d in _at(result.decisions, actuator)
            if code in d.codes and _second(result, d.ts) >= from_s
        ]
        if not found:
            return []
        return [
            f"Entscheidungen an „{actuator}“ mit „{code}“ ab {from_s} s: {len(found)}, die erste "
            f"bei {_second(result, found[0].ts)} s."
        ]

    shown = _hhmm(start, from_s)
    return Check(f"Ab {shown} nennt keine Entscheidung an „{actuator}“ „{code}“.", find)


def _now_throttled_last(key: str, actuator: str) -> Check:
    """Bei der ersten Drosselung des Autos ``key`` in Sofort (der ersten Entscheidung an seinen
    Aktor ``actuator`` mit ``peak_guard``, während die Probe es in Sofort zeigt) lädt kein Auto in
    PV-Überschuss oder Min+PV über seinem Mindeststrom + ``MINIMUM_MARGIN_A`` (Design 6.3). Ohne
    eine solche Drosselung ist das ein Verstoß: Die Prüfung hätte nichts gemessen."""

    def find(result: SimResult) -> list[str]:
        by_second = {s.t: s for s in result.samples}
        for d in _at(result.decisions, actuator):
            sample = by_second.get(_second(result, d.ts))
            if PEAK_GUARD in d.codes and sample is not None and sample.cars[key].mode == "now":
                return [
                    f"Bei der ersten Drosselung von „{key}“ ({sample.t} s) lädt „{other}“ "
                    f"({car.mode}) mit {de(car.current_a)} A, Mindeststrom {car.min_a} A."
                    for other, car in sample.cars.items()
                    if car.mode in PV_MODES
                    and car.charging
                    and car.current_a > car.min_a + MINIMUM_MARGIN_A
                ]
        return [f"Keine Drosselung von „{key}“ in Sofort."]

    text = (
        f"Bei der ersten Drosselung von „{key}“ in Sofort lädt kein Auto in PV-Überschuss oder "
        f"Min+PV über Mindeststrom + {de(MINIMUM_MARGIN_A)} A."
    )
    return Check(text, find)


# --- Prüfungen zur Planausführung ----------------------------------------------------------------


def _held(from_s: int, until_s: int | None = None) -> Check:
    """In jeder ganzen Minute des Laufs ab ``from_s`` (bis ``until_s``, sonst bis zum Ende) liegt
    die mittlere Akkuleistung bei mindestens −``HOLD_W``: Der Akku hält. Eine angebrochene Minute
    am Ende zählt nicht; gibt es keine ganze, ist das ein Verstoß."""

    def find(result: SimResult) -> list[str]:
        minutes: defaultdict[int, list[float]] = defaultdict(list)
        for s in result.samples:
            if from_s <= s.t and (until_s is None or s.t < until_s):
                minutes[s.t // MINUTE_S].append(s.battery_w)
        means = [
            (minute * MINUTE_S, sum(watts) / MINUTE_S)
            for minute, watts in sorted(minutes.items())
            if len(watts) == MINUTE_S
        ]
        if not means:
            return [f"Der Lauf hat ab {from_s} s keine ganze Minute."]
        below = [(t, mean) for t, mean in means if mean < -HOLD_W]
        if not below:
            return []
        t, mean = below[0]
        shown = shown_number(mean, 1, lambda number: number >= -HOLD_W)
        return [
            f"Mittlere Akkuleistung der Minute ab {t} s {shown} W, verlangt mindestens "
            f"{de(-HOLD_W)} W ({len(below)} von {len(means)} Minuten darunter)."
        ]

    since = de(from_s / MINUTE_S)
    span = f"Ab {since} min" if until_s is None else f"Von {since} bis {de(until_s / MINUTE_S)} min"
    return Check(f"{span} entlädt der Akku im Mittel jeder Minute höchstens {de(HOLD_W)} W.", find)


def _soc_dropped_at_most(result: SimResult) -> list[str]:
    """Der SOC fällt höchstens um ``HOLD_SOC_DROP_PCT`` unter den der ersten Probe."""
    if not result.samples:
        return ["Der Lauf hat keine Probe."]
    first = result.samples[0].soc_pct
    drop = first - min(s.soc_pct for s in result.samples)
    if drop <= HOLD_SOC_DROP_PCT:
        return []
    shown = shown_number(drop, 2, lambda number: number <= HOLD_SOC_DROP_PCT)
    return [f"Der SOC fällt um {shown} Prozentpunkte, verlangt höchstens {de(HOLD_SOC_DROP_PCT)}."]


def _victron_decisions_per_hour(result: SimResult) -> list[str]:
    """Höchstens ``HOLD_DECISIONS_PER_HOUR`` Entscheidungen an Victron je Stunde des Laufs."""
    if not result.samples:
        return ["Der Lauf hat keine Probe."]
    count = len(_at(result.decisions, VICTRON))
    minutes = len(result.samples) / MINUTE_S
    per_hour = count * MINUTE_S / minutes
    if per_hour <= HOLD_DECISIONS_PER_HOUR:
        return []
    shown = shown_number(per_hour, 1, lambda number: number <= HOLD_DECISIONS_PER_HOUR)
    return [
        f"{count} Entscheidungen an „{VICTRON}“ in {de(round(minutes, 1))} min: "
        f"{shown} je Stunde, verlangt höchstens {HOLD_DECISIONS_PER_HOUR}."
    ]


def _grid_at_house_plus_charge(result: SimResult) -> list[str]:
    """Ab ``SETTLED_S`` liegt der Netzbezug in jeder Sekunde bei Haus + ``GRID_CHARGE_W`` auf
    ± ``GRID_CHARGE_BAND_W``; gemeldet wird die erste Sekunde außerhalb."""
    after = [s for s in result.samples if s.t >= SETTLED_S]
    if not after:
        return [f"Der Lauf endet vor {SETTLED_S} s."]

    def bounds(s: Sample) -> tuple[float, float]:
        wanted = s.house_w + GRID_CHARGE_W
        return wanted - GRID_CHARGE_BAND_W, wanted + GRID_CHARGE_BAND_W

    outside = [s for s in after if not bounds(s)[0] <= s.grid_w <= bounds(s)[1]]
    if not outside:
        return []
    first = outside[0]
    low, high = bounds(first)
    shown = shown_number(first.grid_w, 0, lambda number: low <= number <= high)
    return [
        f"Netzbezug bei {first.t} s {shown} W bei {de(round(first.house_w))} W Haus, verlangt "
        f"{de(round(low))} bis {de(round(high))} W ({len(outside)} s außerhalb)."
    ]


def _soc_raised(result: SimResult) -> list[str]:
    """Der SOC der letzten Probe liegt mindestens ``grid_charge_rise_pct`` über dem der ersten, bei
    der nutzbaren Kapazität des Laufs."""
    if not result.samples:
        return ["Der Lauf hat keine Probe."]
    required = grid_charge_rise_pct(result.usable_kwh)
    rise = result.samples[-1].soc_pct - result.samples[0].soc_pct
    if rise >= required:
        return []
    shown = shown_number(rise, 2, lambda number: number >= required)
    return [
        f"Der SOC steigt um {shown} Prozentpunkte, verlangt mindestens {de(round(required, 2))}."
    ]


def _free_after_expiry(result: SimResult) -> list[str]:
    """Ab ``FREE_FROM_S`` gilt kein Override, und die Akkuleistung liegt in jeder Sekunde
    höchstens bei −Haus + ``CARRY_W``: Der Akku trägt das Haus wieder."""
    after = [s for s in result.samples if s.t >= FREE_FROM_S]
    if not after:
        return [f"Der Lauf endet vor {FREE_FROM_S} s."]
    found: list[str] = []
    kept = next((s for s in after if s.override_w is not None), None)
    if kept is not None and kept.override_w is not None:
        found.append(f"Bei {kept.t} s gilt ein Override ({de(round(kept.override_w))} W).")

    def limit(s: Sample) -> float:
        return -s.house_w + CARRY_W

    over = [s for s in after if s.battery_w > limit(s)]
    if over:
        first, most = over[0], limit(over[0])
        shown = shown_number(first.battery_w, 0, lambda number: number <= most)
        found.append(
            f"Akkuleistung bei {first.t} s {shown} W bei {de(round(first.house_w))} W Haus, "
            f"verlangt höchstens {de(round(most))} W ({len(over)} s darüber)."
        )
    return found


def _boosting_from(result: SimResult) -> list[str]:
    """Ab ``BOOST_FROM_S`` lädt der e-Golf in mindestens ``BOOSTING_SHARE`` der Sekunden, und die
    Probe zeigt ihn in Boost: Jede neue Version mit Plan behält den Boost (Review Focus 1)."""
    after = [s for s in result.samples if s.t >= BOOST_FROM_S]
    if not after:
        return [f"Der Lauf endet vor {BOOST_FROM_S} s."]
    boosting = sum(1 for s in after if s.cars[EGOLF].charging and s.cars[EGOLF].mode == "boost")
    share = boosting / len(after)
    if share >= BOOSTING_SHARE:
        return []
    shown = shown_number(share * 100, 1, lambda number: number >= BOOSTING_SHARE * 100)
    return [
        f"„{EGOLF}“ lädt ab {BOOST_FROM_S} s in {shown} % der Sekunden in "
        f"Boost, verlangt mindestens {de(BOOSTING_SHARE * 100)} %."
    ]


# --- Prüfungen für die Zuordnung -----------------------------------------------------------------


def _clear_from(from_s: int) -> Check:
    """Ab ``from_s`` zeigt keine Gesundheit ein Fahrzeug mit der Zuordnung ``UNCLEAR``; der Befund
    nennt die erste, die eins zeigt. Gibt es ab ``from_s`` keine Gesundheit, ist das ein Verstoß:
    Die Prüfung hätte nichts gesehen."""
    word = _SHOWN[UNCLEAR]

    def find(result: SimResult) -> list[str]:
        after = [(t, health) for t, health in result.health if t >= from_s]
        if not after:
            return [f"Der Lauf endet vor {from_s} s."]
        for t, health in after:
            for vehicle in health.vehicles:
                if vehicle["assignment"] == UNCLEAR:
                    return [f"Bei {t} s zeigt die Gesundheit „{vehicle['id']}“ „{word}“."]
        return []

    text = f"Ab {de(from_s / MINUTE_S)} min zeigt die Gesundheit kein Fahrzeug „{word}“."
    return Check(text, find)


def _occupied_from(wallbox: str, occupant: str, from_s: int) -> Check:
    """Ab ``from_s`` zeigt jede Gesundheit an der Wallbox ``wallbox`` (``health.wallboxes``) das
    Auto ``occupant``, den Schlüssel eines Fahrzeugs oder ``GUEST``, erkannt (``DETECTED``);
    gemeldet wird die erste, die es nicht tut. Gibt es ab ``from_s`` keine Gesundheit, ist das ein
    Verstoß: Die Prüfung hätte nichts gesehen."""

    def find(result: SimResult) -> list[str]:
        after = [(t, health) for t, health in result.health if t >= from_s]
        if not after:
            return [f"Der Lauf endet vor {from_s} s."]
        for t, health in after:
            if not any(
                entry["id"] == wallbox
                and entry["occupant"] == occupant
                and entry["assignment"] == DETECTED
                for entry in health.wallboxes
            ):
                return [f"Bei {t} s steht an „{wallbox}“ nicht „{occupant}“, erkannt."]
        return []

    minutes = de(from_s / MINUTE_S)
    text = f"Ab {minutes} min steht an „{wallbox}“ laut Gesundheit „{occupant}“, erkannt."
    return Check(text, find)


def _no_decision_at(actuator: str) -> Check:
    """Keine Entscheidung an den Aktor ``actuator``, mit welchem Ergebnis auch immer; der Befund
    nennt ihre Zahl und die Sekunde der ersten."""

    def find(result: SimResult) -> list[str]:
        found = _at(result.decisions, actuator)
        if not found:
            return []
        first = _second(result, found[0].ts)
        return [f"{len(found)} Entscheidungen an „{actuator}“, die erste bei {first} s."]

    return Check(f"Keine Entscheidung an „{actuator}“.", find)


# --- Prüfungen für „Geplant“ und das Ladelimit des Tesla ------------------------------------------


def _vehicle_entries(result: SimResult, key: str) -> list[tuple[int, dict[str, Any]]]:
    """Je Gesundheit, die das Fahrzeug ``key`` nennt, die Sekunde und sein Eintrag."""
    return [
        (t, vehicle)
        for t, health in result.health
        for vehicle in health.vehicles
        if vehicle.get("id") == key
    ]


def _target_stop(result: SimResult, actuator: str) -> Decision | None:
    """Das erste „Laden aus“ an ``actuator`` mit dem Grund ``target_reached``, das ein Befehl war
    (nicht abgelehnt, zurückgestellt oder eine fremde Änderung)."""
    return next(
        (
            d
            for d in _at(result.decisions, actuator)
            if d.kind is CommandKind.CHARGING
            and d.requested is False
            and TARGET_REACHED in d.codes
            and d.result not in _HELD_BACK
        ),
        None,
    )


def _command_second(result: SimResult, stop: Decision) -> int:
    """Die Sekunde, in der der Befehl ``stop`` wirkt: zu der er Tessie erreichte
    (``SimResult.sent``), sonst die seiner Entscheidung. Tessie bestätigt erst später; das Auto
    folgt schon dem Befehl."""
    return _second(result, result.sent.get(stop.id, stop.ts))


def _charges_with_at_least(key: str, amps: float) -> Check:
    """Vom ersten Laden des Autos ``key`` bis zu seinem Stopp am Ziel (``_target_stop`` an seinem
    eigenen Aktor, ab der Sekunde ``_command_second``) lädt es in jeder Sekunde mit mindestens
    ``amps``, also auch ohne Pause; ohne Stopp bis zum Ende. Lädt es nie, ist das ein Verstoß."""

    def find(result: SimResult) -> list[str]:
        samples = result.samples
        first = next((s.t for s in samples if s.cars[key].charging), None)
        if first is None:
            return [f"„{key}“ lädt nie."]
        stop = _target_stop(result, vehicle_actuator(key))
        end = _command_second(result, stop) if stop is not None else samples[-1].t + 1
        low = [s for s in samples if first <= s.t < end and s.cars[key].current_a < amps]
        if not low:
            return []
        return [
            f"„{key}“ lädt bei {low[0].t} s mit {de(low[0].cars[key].current_a)} A, verlangt "
            f"mindestens {de(amps)} A ({len(low)} s darunter)."
        ]

    text = f"Vom ersten Laden bis zum Stopp am Ziel lädt „{key}“ mit mindestens {de(amps)} A."
    return Check(text, find)


def _charges_with_plan_power(key: str, amps: float, until_s: int) -> Check:
    """Vom ersten Strombefehl an das Auto ``key`` bis ``until_s`` lädt es in jeder Sekunde mit
    ``amps`` auf ``PLAN_A_TOLERANCE`` genau: mit der Leistung des Plans, nicht mit voller.

    Für den Befehl zählt die Sekunde, in der er Tessie erreichte (``SimResult.sent``), sonst die
    seiner Entscheidung; zurückgestellte und abgelehnte Befehle zählen nicht. Die Prüfung beginnt
    ``TESSIE_ANSWER_S`` nach diesem Befehl und dem ersten Laden, je nachdem, was später kommt: Der
    Strombefehl geht vor „Laden an“ hinaus, doch Tessie führt Befehle erst mit ihrer Antwortzeit
    aus, und so startet der Tesla kurz mit dem Strom, der noch im Auto steht."""
    actuator = vehicle_actuator(key)

    def find(result: SimResult) -> list[str]:
        first = next(
            (
                d
                for d in _at(result.decisions, actuator)
                if d.kind is CommandKind.CURRENT and d.result not in _HELD_BACK
            ),
            None,
        )
        if first is None:
            return [f"Kein Strombefehl an „{actuator}“."]
        charging = next((s.t for s in result.samples if s.cars[key].charging), None)
        if charging is None:
            return [f"„{key}“ lädt nie."]
        begin = max(_command_second(result, first), charging) + TESSIE_ANSWER_S
        off = [
            s
            for s in result.samples
            if begin <= s.t < until_s and abs(s.cars[key].current_a - amps) > PLAN_A_TOLERANCE
        ]
        if not off:
            return []
        return [
            f"„{key}“ lädt bei {off[0].t} s mit {de(off[0].cars[key].current_a)} A, verlangt "
            f"{de(amps)} A ± {de(PLAN_A_TOLERANCE)} A ({len(off)} s daneben)."
        ]

    text = (
        f"Vom ersten Strombefehl an „{key}“ bis {until_s} s lädt es mit der Leistung des Plans, "
        f"{de(amps)} A ± {de(PLAN_A_TOLERANCE)} A, ab {TESSIE_ANSWER_S} s nach Strombefehl und "
        "erstem Laden (Antwortzeit von Tessie)."
    )
    return Check(text, find)


def _stops_at_target(key: str, actuator: str, target_pct: float) -> Check:
    """Der Ladepunkt ``actuator`` des Autos ``key`` bekommt „Laden aus“ mit ``target_reached``
    (``_target_stop``); in der Sekunde, in der er wirkt (``_command_second``), hat das Auto im
    Modell mindestens ``target_pct``. Danach hört es auf zu laden (an der EVCS mit ihrem Verzug),
    lädt nicht wieder, und kein „Laden an“ an ``actuator`` geht hinaus."""

    def find(result: SimResult) -> list[str]:
        stop = _target_stop(result, actuator)
        if stop is None:
            return [f"Kein „Laden aus“ mit „{TARGET_REACHED}“ an „{actuator}“."]
        at = _command_second(result, stop)
        found: list[str] = []
        soc = next((s.cars[key].soc_pct for s in result.samples if s.t == at), None)
        if soc is None or soc < target_pct:
            shown = "ohne Ladestand" if soc is None else f"{de(round(soc, 2))} %"
            found.append(
                f"„{key}“ hat beim Stopp am Ziel ({at} s) {shown}, verlangt mindestens "
                f"{de(target_pct)} %."
            )
        after = [s for s in result.samples if s.t >= at]
        stopped = next((s.t for s in after if not s.cars[key].charging), None)
        if stopped is None:
            found.append(f"„{key}“ lädt nach dem Stopp am Ziel weiter.")
        else:
            again = next((s.t for s in after if s.t > stopped and s.cars[key].charging), None)
            if again is not None:
                found.append(f"„{key}“ lädt nach dem Stopp am Ziel wieder, bei {again} s.")
        starts = [
            d
            for d in _at(result.decisions, actuator)
            if d.kind is CommandKind.CHARGING
            and d.requested is True
            and d.result not in _HELD_BACK
            and d.ts > stop.ts
        ]
        if starts:
            found.append(
                f"{len(starts)} Befehle „Laden an“ an „{actuator}“ nach dem Stopp am Ziel, der "
                f"erste bei {_second(result, starts[0].ts)} s."
            )
        return found

    where = "" if actuator == vehicle_actuator(key) else f" an „{actuator}“"
    text = (
        f"„{key}“ bekommt „Laden aus“{where} mit „{TARGET_REACHED}“ bei mindestens "
        f"{de(target_pct)} % und lädt danach nicht mehr."
    )
    return Check(text, find)


def _waits_for_latest_start(key: str, *, within_s: int | None = None) -> Check:
    """Das Auto ``key`` lädt erst ab der spätesten Startzeit, die die letzte Gesundheit vor seinem
    ersten Laden nennt (``planned.latest_start``); mit ``within_s`` beginnt es binnen so vieler
    Sekunden danach. Danach rückt die späteste Startzeit mit dem Laden weiter, darum zählt die
    davor. Lädt es nie oder nennt die Gesundheit davor keine, ist das ein Verstoß."""

    def find(result: SimResult) -> list[str]:
        first = next((s.t for s in result.samples if s.cars[key].charging), None)
        if first is None:
            return [f"„{key}“ lädt nie."]
        before = [(t, entry) for t, entry in _vehicle_entries(result, key) if t < first]
        planned = before[-1][1].get("planned") if before else None
        latest = planned.get("latest_start") if isinstance(planned, dict) else None
        if not isinstance(latest, str):
            return [
                f"Die Gesundheit nennt vor dem ersten Laden von „{key}“ ({first} s) keine "
                "späteste Startzeit."
            ]
        latest_s = (datetime.fromisoformat(latest) - result.scenario.start).total_seconds()
        if first < latest_s:
            return [
                f"„{key}“ lädt bei {first} s, vor der spätesten Startzeit "
                f"({de(round(latest_s))} s laut Gesundheit bei {before[-1][0]} s)."
            ]
        if within_s is not None and first - latest_s > within_s:
            return [
                f"„{key}“ beginnt bei {first} s, {de(round(first - latest_s))} s nach der "
                f"spätesten Startzeit, verlangt binnen {within_s} s."
            ]
        return []

    text = (
        f"„{key}“ lädt erst ab der spätesten Startzeit, die die Gesundheit vor seinem ersten Laden "
        "nennt"
    )
    text += "." if within_s is None else f", und beginnt binnen {within_s} s danach."
    return Check(text, find)


def _reaches_before(key: str, target_pct: float, before_s: int) -> Check:
    """Der Ladestand des Autos ``key`` im Modell erreicht ``target_pct`` vor der Sekunde
    ``before_s``, der Abfahrt."""

    def find(result: SimResult) -> list[str]:
        socs = [s.cars[key].soc_pct for s in result.samples if s.t < before_s]
        best = max((soc for soc in socs if soc is not None), default=None)
        if best is None:
            return [f"„{key}“ hat vor der Abfahrt keinen Ladestand."]
        if best >= target_pct:
            return []
        shown = shown_number(best, 2, lambda number: number >= target_pct)
        return [f"„{key}“ hat vor der Abfahrt höchstens {shown} %."]

    text = f"„{key}“ erreicht {de(target_pct)} % vor der Abfahrt ({de(before_s / MINUTE_S)} min)."
    return Check(text, find)


def _soc_assumed(key: str) -> Check:
    """Jede Gesundheit, die einen Ladestand für ``key`` nennt (``soc_source``), nennt ihn
    ``assumed`` mit 0 %, und es gibt mindestens eine; gemeldet wird die erste andere."""

    def find(result: SimResult) -> list[str]:
        shown = [
            (t, entry.get("soc_pct"), entry["soc_source"])
            for t, entry in _vehicle_entries(result, key)
            if entry.get("soc_source") is not None
        ]
        if not shown:
            return [f"Die Gesundheit nennt für „{key}“ nie einen Ladestand."]
        for t, pct, source in shown:
            if source != "assumed" or pct != 0:
                value = "ohne Wert" if pct is None else f"{de(pct)} %"
                return [
                    f"Bei {t} s nennt die Gesundheit für „{key}“ {value} ({source}), verlangt "
                    "0 % (assumed)."
                ]
        return []

    text = (
        f"Jede Gesundheit, die einen Ladestand für „{key}“ nennt, nennt ihn „angenommen“ mit 0 %."
    )
    return Check(text, find)


def _never_named(code: str) -> Check:
    """Keine Entscheidung nennt den Grund ``code``; der Befund nennt ihre Zahl und die erste."""

    def find(result: SimResult) -> list[str]:
        found = [d for d in result.decisions if code in d.codes]
        if not found:
            return []
        first = _second(result, found[0].ts)
        return [f"{len(found)} Entscheidungen nennen „{code}“, die erste bei {first} s."]

    return Check(f"Keine Entscheidung nennt „{code}“.", find)


def _one_charge_limit(key: str, pct: int) -> Check:
    """Genau eine Entscheidung „Ladelimit ``pct`` %“ an das Fahrzeug ``key`` und keine andere über
    das Ladelimit; eine zurückgestellte zählt nicht, denn ihr Befehl kommt noch einmal, und eine
    fremde Änderung auch nicht."""
    actuator = vehicle_actuator(key)

    def find(result: SimResult) -> list[str]:
        limits = [
            d
            for d in _at(result.decisions, actuator)
            if d.kind is CommandKind.CHARGE_LIMIT and d.result not in _NOT_THE_COMMAND
        ]
        wanted = [d for d in limits if d.requested == pct]
        found: list[str] = []
        if len(wanted) != 1:
            found.append(
                f"{len(wanted)} Entscheidungen „Ladelimit {pct} %“ an „{key}“, verlangt genau eine."
            )
        others = [d for d in limits if d.requested != pct]
        if others:
            value = finite_number(others[0].requested)
            shown = str(others[0].requested) if value is None else de(value)
            found.append(
                f"Eine andere Entscheidung über das Ladelimit: {shown} % bei "
                f"{_second(result, others[0].ts)} s."
            )
        return found

    text = (
        f"Genau eine Entscheidung „Ladelimit {pct} %“ an „{key}“, keine andere über das Ladelimit."
    )
    return Check(text, find)


def _limit_shown(key: str, pct: int) -> Check:
    """Die letzte Gesundheit, die das Fahrzeug ``key`` nennt, zeigt das Ladelimit ``pct`` %: So
    meldet Tessie das Limit im Modell."""

    def find(result: SimResult) -> list[str]:
        entries = _vehicle_entries(result, key)
        if not entries:
            return [f"Die Gesundheit nennt „{key}“ nie."]
        last = entries[-1][1].get("charge_limit_pct")
        if last is None:
            return [f"Am Ende zeigt die Gesundheit für „{key}“ kein Ladelimit."]
        if last != pct:
            return [f"Am Ende zeigt die Gesundheit für „{key}“ das Ladelimit {de(last)} %."]
        return []

    return Check(f"Am Ende zeigt die Gesundheit für „{key}“ das Ladelimit {pct} %.", find)


def _complete_at(key: str, pct: int) -> Check:
    """Nach dem ersten Laden ist das Auto ``key`` im Modell „Complete“, und zwar mit genau
    ``pct`` %: Es hat am Ladelimit ``pct`` aufgehört (``Car.draw`` lädt nie darüber)."""

    def find(result: SimResult) -> list[str]:
        samples = result.samples
        first = next((s.t for s in samples if s.cars[key].charging), None)
        if first is None:
            return [f"„{key}“ lädt nie."]
        done = next((s for s in samples if s.t > first and s.cars[key].complete), None)
        if done is None:
            return [f"„{key}“ meldet nach dem Laden nie „Complete“."]
        soc = done.cars[key].soc_pct
        if soc is None or not math.isclose(soc, pct):
            shown = "ohne Ladestand" if soc is None else f"bei {de(round(soc, 2))} %"
            return [f"„{key}“ meldet „Complete“ {shown}, verlangt {pct} %."]
        return []

    return Check(f"Nach dem Laden meldet „{key}“ „Complete“ bei {pct} %.", find)


# --- Szenarien 1 bis 5 ----------------------------------------------------------------------------


def _sonnig() -> Scenario:
    """Die Verteilung ist gierig nach Priorität (Design 6.2): Mit höchstens 10 kW PV laden die
    Autos nacheinander, erst der Tesla bis „Complete“, dann der e-Golf. Dem Rauschen der Hauslast
    folgt die EVCS dank der Hysterese nicht (Design 6.5)."""
    start, duration_s, seed = SUMMER, 2 * HOUR_S, 1
    few = Check(
        f"Höchstens {EVCS_CURRENTS_PER_HOUR} Strombefehle an die EVCS je Stunde, in der „egolf“ "
        "lädt (Hysterese).",
        _few_evcs_currents,
    )
    return Scenario(
        name="sonnig",
        title="Sonniger Tag, beide Autos im PV-Modus",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla_soc_pct=SUNNY_TESLA_PCT),
        intent=_intent(tesla=ChargeMode.PV, egolf=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed, pv=_sun(start)),
        checks=Checks((_one_after_the_other_check(tesla_min=45.0, egolf_min=45.0), few)),
    )


def _wolkig() -> Scenario:
    """Die Verteilung ist gierig nach Priorität (Design 6.2): Mit höchstens 10 kW PV laden die
    Autos nacheinander, erst der Tesla bis „Complete“, dann der e-Golf."""
    start, duration_s, seed = SUMMER, 2 * HOUR_S, 2
    return Scenario(
        name="wolkig",
        title="Wolkiger Tag, beide Autos im PV-Modus",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla_soc_pct=CLOUDY_TESLA_PCT),
        intent=_intent(tesla=ChargeMode.PV, egolf=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed, pv=_cloudy(start, duration_s, seed)),
        checks=Checks((_one_after_the_other_check(tesla_min=20.0, egolf_min=20.0),)),
    )


def _abend_sofort() -> Scenario:
    start, duration_s, seed = EVENING, HOUR_S, 3

    def peak(t: int) -> float:
        return PEAK_W if PEAK_S[0] <= t < PEAK_S[1] else 0.0

    lower = Check(
        "Während der Lastspitze liegt der Strom der EVCS unter dem davor.",
        _evcs_lower_during_the_peak,
    )
    return Scenario(
        name="abend-sofort",
        title="Abends beide Autos auf Sofort, mit Lastspitze",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0),
        intent=_intent(tesla=ChargeMode.NOW, egolf=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed, l1=peak),
        checks=Checks((lower,)),
    )


def _reserve() -> Scenario:
    start, duration_s, seed = EVENING, HOUR_S, 4

    def more_on_l1(t: int) -> float:
        return RESERVE_L1_W

    target = Check(
        "Unter der Reserve-Absicherung liegt keine Phase länger als "
        f"{TRANSITION_S} s über dem Importziel (Last ohne Akku-Hilfe).",
        _import_target_kept,
    )
    kept = Check(
        f"Der SOC sinkt nie unter {de(RESERVE_PCT)} % (Invariante des Modells).", _reserve_kept
    )
    return Scenario(
        name="reserve",
        title="Sofort knapp über der USV-Reserve",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=23.0, tesla=None),
        intent=_intent(egolf=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed, l1=more_on_l1),
        checks=Checks((_named("reserve_guard"), target, kept)),
    )


def _nur_laden() -> Scenario:
    start, duration_s, seed = EVENING, HOUR_S, 5

    def ramp(t: int) -> float:
        begin, end = RAMP_S
        return RAMP_W * min(max(t - begin, 0) / (end - begin), 1.0)

    warns = Check("Die Selbstprüfung „multi_mode“ warnt.", _multi_mode_warns)
    return Scenario(
        name="nur-laden",
        title="Multi auf „Nur Laden“ mit Sofort",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, charger_only=True),
        intent=_intent(tesla=ChargeMode.NOW, egolf=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed, l1=ramp),
        checks=Checks((warns,)),
    )


# --- Szenarien 6 bis 10 ---------------------------------------------------------------------------


def _tesla_5a() -> Scenario:
    """Die PV trägt den Tesla mit 5 A (Design 6.2); sein erster Befehl von 10 auf 5 A geht verloren,
    und die Regelung fordert ihn nach „nicht übernommen“ erneut an (Design 7.2)."""
    start, duration_s, seed = SUMMER, HOUR_S, 6

    def pv(t: int) -> float:
        return FIVE_A_PV_W

    return Scenario(
        name="tesla-5a",
        title="Tesla auf 5 A mit verlorenem erstem Befehl",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=90.0, egolf=None, tesla_amps=TESLA_START_A),
        intent=_intent(tesla=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed, pv=pv),
        checks=Checks((_named("not_taken_retry"), _charges_with(TESLA, FIVE_A))),
    )


def _tesla_schlaeft() -> Scenario:
    """Der Tesla schläft, bis der Überschuss seinen Mindeststrom trägt; dann weckt ihn der Start
    mit Weckerlaubnis (Design 7.1)."""
    start, duration_s, seed = MORNING, HOUR_S, 7

    def pv(t: int) -> float:
        return SLEEPY_PV_W * min(t / SLEEPY_RAMP_S, 1.0)

    return Scenario(
        name="tesla-schlaeft",
        title="Schlafender Tesla im PV-Modus",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=90.0, egolf=None, asleep=True),
        intent=_intent(tesla=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed, pv=pv),
        checks=Checks((_one_wake(TESLA), _charges_at_the_end(TESLA))),
    )


def _ausfaelle() -> Scenario:
    """Jeder Ausfall einzeln (Design 9); nach dem Netzausfall nimmt die Regelung das Laden nach
    dem Modus wieder auf."""
    start, duration_s, seed = EVENING, HOUR_S + 30 * MINUTE_S, 8
    outages = tuple(
        event
        for name, begin, end in OUTAGE_MIN
        for event in (
            (begin * MINUTE_S, _Outage(name, begins=True)),
            (end * MINUTE_S, _Outage(name, begins=False)),
        )
    )
    safe = tuple(_safe_during(n, b, e) for n, b, e in OUTAGE_MIN if n in SAFE_OUTAGES)
    return Scenario(
        name="ausfaelle",
        title="Ausfälle einzeln, Netzausfall mit Wiederaufnahme",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0),
        intent=_intent(tesla=ChargeMode.NOW, egolf=ChargeMode.NOW),
        live=True,
        events=(*_weather(start, duration_s, seed), *outages),
        checks=Checks((*safe, _all_charge_from(RESUMED_S), _named("resume_after_outage"))),
    )


def _zuordnung() -> Scenario:
    """Ein echter Widerspruch macht die Zuordnung unklar (Plan 2c-2b, Präzisierung 4): Der Tesla
    steckt woanders, Tessie meldet ihn aber zu Hause und angesteckt; Tessie und der Ausschluss
    stützen ihn so an der EVCS, wo die Phasen-Signatur des e-Golf ihn ausschließt. Die
    Übersteuerung des e-Golf an die EVCS hebt „unklar“ sofort auf (Plan 2c-1, Präzisierung 5), das
    Abstecken beendet sie (Plan 2c-1, Präzisierung 3), und danach lädt die EVCS nicht mehr. Warum
    die EVCS schon vor dem Start des core lädt, steht im Docstring des Moduls."""
    start, duration_s, seed = EVENING, HOUR_S, 9
    events = (
        (OVERRIDE_S, _Override(2, EGOLF, EVCS_BOX)),
        (UNPLUG_S, _Unplug(EGOLF)),
    )
    checks = (
        _shown_before(EGOLF, UNCLEAR, OVERRIDE_S),
        _clear_from(OVERRIDE_S + MINUTE_S),
        _evcs_off_from(EVCS_BOX, EVCS_OFF_S),
    )
    return Scenario(
        name="zuordnung",
        title="Zuordnung unklar und Übersteuerung",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None, tesla_elsewhere=True, evcs_on=True),
        intent=_intent(egolf=ChargeMode.NOW),
        live=True,
        events=(*_weather(start, duration_s, seed), *events),
        checks=Checks(checks),
    )


def _dry_run() -> Scenario:
    """Ein fremder Regler wie evcc stellt die Ladepunkte (Modell: ``foreign``), die Regelung
    entscheidet daneben im Dry-Run (Design 12). Die PV-Kennzahlen zählen seine Autos nicht, denn
    ihre Ladepunkte steuert die Regelung nicht live (``CarSample.steered``)."""
    start, duration_s, seed = SUMMER, HOUR_S, 10
    checks = (
        Check("Keine wiederholte Dry-Run-Entscheidung.", _no_repeated_dry_runs),
        _dry_runs_at(wallbox_actuator(EVCS_BOX), vehicle_actuator(TESLA)),
        Check("Kein Alarm „competitor.foreign.*“.", _no_foreign_alarm),
    )
    return Scenario(
        name="dry-run",
        title="Dry-Run neben einem fremden Regler",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, foreign=True, tesla_starts=True),
        intent=_intent(tesla=ChargeMode.PV, egolf=ChargeMode.PV),
        live=False,
        events=_weather(start, duration_s, seed, pv=_cloudy(start, duration_s, seed)),
        checks=Checks(checks),
    )


# --- Szenarien 11 bis 18: Akku und Spitzen, Victron live ------------------------------------------


def _akku_wolkig() -> Scenario:
    """Ohne Unterstützung (SOC unter ``support_soc_pct``) liefert der Akku dem e-Golf nichts
    (Design 5.1, 5.2): Was ihm unter Wolken fehlt, holt der Override aus dem Netz."""
    start, duration_s, seed = SUMMER, HOUR_S, 11
    return Scenario(
        name="akku-wolkig",
        title="PV unter Wolken, SOC 60 %",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None),
        intent=_intent(egolf=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed, pv=_cloudy(start, duration_s, seed)),
        checks=Checks(
            (
                _from_battery_at_most(EGOLF, CAR_FROM_BATTERY_PCT),
                _named("battery_comp", at=VICTRON),
            )
        ),
        victron_live=True,
    )


def _akku_minpv() -> Scenario:
    """Mit Unterstützung liefert der Akku den Autos in Min+PV zusammen höchstens
    ``support_max_w``, nach ``support_max_s`` nichts mehr (Design 5.2). Der Tesla steht zu Beginn
    auf 5 A (Abweichung, Docstring des Moduls)."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 12
    supported = Check(
        f"Akku in die Autos (Entladung − Haus): ab {SUPPORT_FROM_S} s nach dem ersten Laden bis "
        f"{SUPPORT_UNTIL_S} s danach höchstens {de(SUPPORT_W)} W, ab {GRID_ONLY_S} s danach "
        f"höchstens {de(GRID_ONLY_W)} W.",
        _support_then_grid,
    )
    return Scenario(
        name="akku-minpv",
        title="Min+PV ohne Sonne, SOC 80 %",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=80.0, tesla_amps=TESLA_MIN_A),
        intent=_intent(tesla=ChargeMode.MIN_PV, egolf=ChargeMode.MIN_PV),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks((supported,)),
        victron_live=True,
    )


def _akku_sofort() -> Scenario:
    """In Sofort liefert der Akku nur, was über ``max_grid_charge_setpoint_w`` hinausgeht
    (Design 5.4, Regel 2)."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 13
    low, high = GRID_BETWEEN_W
    capped = Check(
        f"Ab {de(SETTLED_S / MINUTE_S)} min liegt der Netzbezug in jeder Sekunde, in der Autos und "
        f"Haus mehr als {de(LOADS_ABOVE_W)} W ziehen, zwischen {de(low)} und {de(high)} W.",
        _grid_at_the_setpoint_cap,
    )
    return Scenario(
        name="akku-sofort",
        title="Sofort am Abend, beide Autos",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0),
        intent=_intent(tesla=ChargeMode.NOW, egolf=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks((capped,)),
        victron_live=True,
    )


def _boost_abend() -> Scenario:
    """In Boost darf der Akku alles liefern (Design 5.2, Entscheidung 2): Es gibt keinen Override.
    Über der Reserve-Absicherung trägt der Akku Haus und Auto; unter die USV-Reserve entlädt das
    Modell nie."""
    start, duration_s, seed = EVENING, 45 * MINUTE_S, 14
    boost = VehicleIntent(boost_until=start + timedelta(seconds=duration_s))
    house = Check(
        f"Solange der SOC über {de(BOOST_SOC_PCT)} % liegt, ab {de(SETTLED_S / MINUTE_S)} min "
        f"Netzbezug höchstens {de(BOOST_GRID_W)} W.",
        _battery_carries_the_house,
    )
    kept = Check(
        f"Der SOC sinkt nie unter {de(RESERVE_PCT)} % (Invariante des Modells).", _reserve_kept
    )
    return Scenario(
        name="boost-abend",
        title="Boost am Abend",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=35.0, tesla=None),
        intent=Intent(version=1, control=True, vehicles={EGOLF: boost}),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks(
            (
                Check(f"Keine Entscheidung an „{VICTRON}“ mit einer Zahl.", _no_victron_number),
                house,
                kept,
            )
        ),
        victron_live=True,
    )


def _override_stunde() -> Scenario:
    """Ein Override bleibt eine Stunde lang: Die Regelung frischt ihn alle ``override_refresh_s``
    auf, ohne Eintrag im Log und ohne Alarm einer fremden Änderung (Design 5.5)."""
    start, duration_s, seed = EVENING, HOUR_S, 15
    kept = Check(
        f"Ab {de(SETTLED_S / MINUTE_S)} min gilt in jeder Sekunde ein Override, zuletzt vor "
        f"höchstens {de(OVERRIDE_AGE_S)} s geschrieben.",
        _override_kept,
    )
    few = Check(
        f"Höchstens {VICTRON_DECISIONS} Entscheidungen an „{VICTRON}“.", _few_victron_decisions
    )
    return Scenario(
        name="override-stunde",
        title="Eine Stunde mit aktivem Override",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=50.0, tesla=None),
        intent=_intent(egolf=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks((kept, Check("Kein Alarm „competitor.foreign.*“.", _no_foreign_alarm), few)),
        victron_live=True,
    )


def _dess_an() -> Scenario:
    """Mit Dynamic ESS ist Victron gesperrt: Der Akku-Sollwert sendet nichts, auch keine
    Ablehnung, und die Wallbox lädt weiter (Design 5.6)."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 16
    locked = Check(
        f"Ab {de(LOCKED_FROM_S / MINUTE_S)} min zeigt jede Gesundheit „battery.state“ „locked“.",
        _battery_locked,
    )
    return Scenario(
        name="dess-an",
        title="Dynamic ESS an",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None, dess=True),
        intent=_intent(egolf=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks(
            (
                Check(f"Keine Entscheidung an „{VICTRON}“.", _no_victron_decision),
                locked,
                _charges_at_the_end(EGOLF),
            )
        ),
        victron_live=True,
    )


def _spitze_15min() -> Scenario:
    """Der 15-min-Wächter hält das Ziel von 8 kW (Design 6): Er drosselt in der Reihenfolge der
    Modi, der Akku überbrückt den Rest; Boost drosselt er nie. Der Lauf beginnt ``PEAK_LEAD_S``
    vor 18:00: Nach dem Start beobachtet der core rund 60 s und regelt so ab der Viertelstunde um
    18:00; die angebrochene davor ist lückenhaft. Von 18:45 an lädt der e-Golf in Boost, darum
    prüft der Lauf die Viertelstunde ab 18:45 nicht. Der Tesla startet mit 16 A; was die Prüfung
    der Reihenfolge damit belegt, steht im Docstring des Moduls."""
    start, duration_s, seed = EVENING - timedelta(seconds=PEAK_LEAD_S), 75 * MINUTE_S, 17
    boost = _Boost(2, EGOLF, start + timedelta(seconds=duration_s))
    return Scenario(
        name="spitze-15min",
        title="15-min-Ziel 8 kW",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0),
        intent=_intent(tesla=ChargeMode.MIN_PV, egolf=ChargeMode.NOW),
        live=True,
        events=(*_weather(start, duration_s, seed), (PEAK_BOOST_S, boost)),
        checks=Checks(
            (
                _quarters_within(start, QUARTER_KWH),
                _named_before(PEAK_GUARD, PEAK_BOOST_S, start),
                _not_named_from(PEAK_GUARD, wallbox_actuator(EVCS_BOX), PEAK_BOOST_S, start),
                _now_throttled_last(EGOLF, wallbox_actuator(EVCS_BOX)),
            )
        ),
        victron_live=True,
        control=MappingProxyType({"peak_target_kw": PEAK_TARGET_KW}),
    )


def _schieflast_pv() -> Scenario:
    """Mit schwerer Last auf L1 bindet das Importziel den e-Golf in PV zwischen Mindest- und
    Höchststrom (Punkt 6 der Liste „Vor der Live-Schaltung“). Die Last kommt erst nach 10 min dazu,
    wenn der e-Golf lädt (Abweichung, Docstring des Moduls)."""
    start, duration_s, seed = SUMMER, HOUR_S, 18

    def pv(t: int) -> float:
        return UNBALANCED_PV_W

    def heavy(t: int) -> float:
        return UNBALANCED_L1_W if t >= UNBALANCED_FROM_S else 0.0

    return Scenario(
        name="schieflast-pv",
        title="PV mit schwerer Last auf L1",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None),
        intent=_intent(egolf=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed, pv=pv, l1=heavy),
        checks=Checks((_named("import_limit", at=wallbox_actuator(EVCS_BOX)),)),
        victron_live=True,
    )


# --- Szenarien 19 bis 21: Zuordnung, Victron im Dry-Run -------------------------------------------


def _tesla_evcs() -> Scenario:
    """Der Tesla steht ohne Übersteuerung an der EVCS: Die Erkennung findet ihn dort (Plan 2c-2b,
    Präzisierung 4), er bekommt einmal seinen Höchststrom, und die EVCS regelt in seinem Modus
    (Plan 2c-2b, Präzisierung 7). Abends ohne PV lüde er in PV-Überschuss nicht; mit 16 A am Ende
    lädt er in Sofort. „Erkannt“ verlangt die Prüfung vor 3 min statt binnen 2 min (Abweichung,
    Docstring des Moduls)."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 19
    checks = (
        _shown_before(TESLA, DETECTED, 3 * MINUTE_S),
        _one_current(TESLA, TESLA_MAX_A),
        _charges_with(TESLA, TESLA_MAX_A),
    )
    return Scenario(
        name="tesla-evcs",
        title="Tesla an der EVCS ohne Übersteuerung",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(
            _setup,
            soc_pct=60.0,
            tesla=EVCS_BOX,
            egolf=None,
            tesla_amps=TESLA_START_A,
            tesla_starts=True,
        ),
        intent=_intent(tesla=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks(checks),
    )


def _egolf_twc() -> Scenario:
    """Der e-Golf steht am Wall Connector: Home Assistant und die Phasen-Signatur stützen ihn dort,
    Tessie schließt den Tesla aus, und die Erkennung findet den e-Golf (Plan 2c-2b, Präzisierung 4).
    Der Tesla bekommt keinen Befehl. Der Wall Connector hat für den e-Golf keinen Aktor: Der e-Golf
    lädt als Last mit 16 A, obwohl er in PV-Überschuss steht und abends keine PV scheint. Bis zur
    Erkennung steht dort ein angenommener Gast."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 20
    checks = (
        _occupied_from(WALL_CONNECTOR, EGOLF, 4 * MINUTE_S),
        _no_decision_at(vehicle_actuator(TESLA)),
        _charges_with(EGOLF, 16.0),
    )
    return Scenario(
        name="egolf-twc",
        title="e-Golf am Wall Connector",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None, egolf=WALL_CONNECTOR),
        intent=_intent(egolf=ChargeMode.PV),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks(checks),
    )


def _gast_evcs() -> Scenario:
    """Ein Gast mit 3 Phasen steht an der EVCS, der e-Golf ist weg: Tessie schließt den Tesla aus,
    Home Assistant den e-Golf, und die Erkennung findet den Gast (Plan 2c-2b, Präzisierung 4). Die
    EVCS lädt ihn im Modus des Gastes (Plan 2c-2b, Präzisierung 6): Abends ohne PV lüde er in
    PV-Überschuss nicht; mit 16 A am Ende lädt er in Sofort."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 21
    return Scenario(
        name="gast-evcs",
        title="Gast an der EVCS, e-Golf weg",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(
            _setup,
            soc_pct=60.0,
            tesla=None,
            egolf=None,
            guest=EVCS_BOX,
            guest_phases=3,
            away=frozenset({EGOLF}),
        ),
        intent=_intent(guest_mode=ChargeMode.NOW),
        live=True,
        events=_weather(start, duration_s, seed),
        checks=Checks(
            (_occupied_from(EVCS_BOX, GUEST, 3 * MINUTE_S), _charges_with(GUEST_CAR, 16.0))
        ),
    )


# --- Szenarien 22 und 23: „Aus“ und Selbststart des Tesla -----------------------------------------


def _tesla_aus() -> Scenario:
    """Wie ``wolkig``, doch der Tesla wechselt bei 45 min auf „Aus“ (Plan 2d, Präzisierung 9): Er
    bekommt binnen 10 s „Laden aus“, und kein Befehl „charging“ an ihn wird zurückgestellt.

    Dass der dringende Stopp das Stundenlimit umgeht, belegt das Szenario nicht: Die Reserve
    (``control.tesla_reserve_commands``) hält das Stundenbudget unter den Wolken offen, es ist nie
    ausgeschöpft. Das Umgehen prüft der Test ``test_off_stops_urgently`` der Regelung."""
    start, duration_s, seed = SUMMER, HOUR_S, 22
    stop = OFF_AT_S, _Mode(2, TESLA, ChargeMode.OFF)
    checks = (
        _stops_within(vehicle_actuator(TESLA), OFF_AT_S, OFF_WITHIN_S),
        _no_deferred(vehicle_actuator(TESLA), CommandKind.CHARGING),
    )
    return Scenario(
        name="tesla-aus",
        title="Tesla wird unter Wolken auf Aus gestellt",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla_soc_pct=OFF_TESLA_PCT),
        intent=_intent(tesla=ChargeMode.PV, egolf=ChargeMode.PV),
        live=True,
        events=(*_weather(start, duration_s, seed, pv=_cloudy(start, duration_s, seed)), stop),
        checks=Checks(checks),
    )


def _tesla_selbststart() -> Scenario:
    """Der Tesla steht am Wall Connector im Modus „Aus“ und lädt nicht; bei 5 min startet er selbst
    (Plan 2d, Präzisierung 10). Die Regelung sieht es mit der nächsten Abfrage von Tessie (60 s,
    angesteckt), wartet 120 s und stoppt ihn dann; mit dem Verzug des Befehls bleiben 210 s."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 23
    checks = (
        _stops_within(vehicle_actuator(TESLA), SELF_START_S, SELF_START_WITHIN_S),
        _idle_from(TESLA, IDLE_FROM_S),
    )
    return Scenario(
        name="tesla-selbststart",
        title="Tesla startet selbst im Modus Aus",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, egolf=None, tesla_soc_pct=SELF_START_TESLA_PCT),
        intent=_intent(tesla=ChargeMode.OFF),
        live=True,
        events=(
            *_weather(start, duration_s, seed),
            (SELF_START_S, _SelfStart(TESLA)),
        ),
        checks=Checks(checks),
    )


# --- Szenarien 24 bis 28: Planausführung, Victron live (Plan 3c, Entscheidung 13) ----------------


HOLD: Final = BatteryIntent(kind=BatteryIntentKind.HALTEN)


def _plan_halten() -> Scenario:
    """Pläne mit ``HALTEN`` alle 15 min, wie von der app: Der core hält den Netzbezug bei
    Haus − PV (Präzisierung 7), auch über einen Lastsprung auf L1 hinweg; das Totband hält die Zahl
    der Entscheidungen klein (Review Focus 4)."""
    start, duration_s, seed = EVENING, HOUR_S, 24
    begin, end = PEAK_S

    def jump(t: int) -> float:
        return HOLD_JUMP_W if begin <= t < end else 0.0

    dropped = Check(
        f"Der SOC fällt höchstens um {de(HOLD_SOC_DROP_PCT)} Prozentpunkte.", _soc_dropped_at_most
    )
    few = Check(
        f"Höchstens {HOLD_DECISIONS_PER_HOUR} Entscheidungen an „{VICTRON}“ je Stunde.",
        _victron_decisions_per_hour,
    )
    return Scenario(
        name="plan-halten",
        title="Halten am Abend",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None),
        intent=_intent(egolf=ChargeMode.PV),
        live=True,
        events=(
            *_weather(start, duration_s, seed, l1=jump),
            *_every_quarter(start, duration_s, HOLD),
        ),
        checks=Checks((_held(SETTLED_S), dropped, few)),
        victron_live=True,
    )


def _plan_netzladen() -> Scenario:
    """Ein Plan mit ``NETZLADEN(3000)`` für 30 min in der Nacht: Der Netzbezug folgt Haus + 3000 W,
    und der Akku speichert davon ``√0,88``."""
    start, duration_s, seed = NIGHT, GRID_CHARGE_S, 25
    charge = BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=GRID_CHARGE_W)
    slots = tuple((k, charge, None) for k in range(GRID_CHARGE_S // QUARTER_S))
    charged = Check(
        f"Ab {de(SETTLED_S / MINUTE_S)} min liegt der Netzbezug in jeder Sekunde bei Haus + "
        f"{de(GRID_CHARGE_W)} W, auf ± {de(GRID_CHARGE_BAND_W)} W.",
        _grid_at_house_plus_charge,
    )
    raised = Check(
        f"Der SOC steigt um mindestens {de(GRID_CHARGE_SHARE * 100)} % von "
        f"{de(GRID_CHARGE_W / 1000)} kW · {de(GRID_CHARGE_S / HOUR_S)} h · "
        f"√{de(ROUNDTRIP_PCT / 100)}, geteilt durch die nutzbare Kapazität des Akkus.",
        _soc_raised,
    )
    return Scenario(
        name="plan-netzladen",
        title="Netzladen in der Nacht",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=NIGHT_SOC_PCT, tesla=None),
        intent=_intent(egolf=ChargeMode.PV),
        live=True,
        events=(
            *_weather(start, duration_s, seed),
            _plan(start, 0, slots, valid_s=GRID_CHARGE_S),
        ),
        checks=Checks((charged, raised)),
        victron_live=True,
    )


def _plan_abgelaufen() -> Scenario:
    """Ein Plan mit ``HALTEN``, gültig 20 min, und kein neuer: Bis dahin hält der Akku, danach gilt
    ``EIGENVERBRAUCH`` (Spec 7.7). Der Override verfällt spätestens 300 s nach dem letzten
    Schreiben; ab 26 min gilt keiner mehr, und der Akku trägt das Haus."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 26
    slots = ((0, HOLD, None), (1, HOLD, None))
    free = Check(
        f"Ab {de(FREE_FROM_S / MINUTE_S)} min gilt kein Override, und der Akku trägt das Haus: "
        f"Akkuleistung höchstens −Haus + {de(CARRY_W)} W.",
        _free_after_expiry,
    )
    return Scenario(
        name="plan-abgelaufen",
        title="Plan läuft ab",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None),
        intent=_intent(egolf=ChargeMode.PV),
        live=True,
        events=(*_weather(start, duration_s, seed), _plan(start, 0, slots, valid_s=EXPIRES_S)),
        checks=Checks((_held(SETTLED_S, EXPIRES_S), free)),
        victron_live=True,
    )


def _plan_budget() -> Scenario:
    """Pläne mit ``NETZLADEN(6000)`` und ``budget_kw`` 4,0: Das Budget ersetzt
    ``control.peak_target_kw`` (Präzisierung 9), und der Wächter senkt das Netzladen, bis jede
    Viertelstunde höchstens 1 kWh bezieht. Der Lauf beginnt wie ``spitze-15min`` eine Minute vor
    18:00 und prüft die Viertelstunden ab 18:00. Die Untergrenze und die Gründe ``plan_grid_charge``
    und ``peak_guard`` belegen, dass der Plan und sein Budget wirken: Ohne Plan trüge der Akku das
    Haus, und der Netzbezug bliebe nahe 0."""
    start, duration_s, seed = EVENING - timedelta(seconds=PEAK_LEAD_S), 47 * MINUTE_S, 27
    charge = BatteryIntent(kind=BatteryIntentKind.NETZLADEN, grid_charge_w=BUDGET_CHARGE_W)
    return Scenario(
        name="plan-budget",
        title="Netzladen unter dem Budget",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None),
        intent=_intent(egolf=ChargeMode.PV),
        live=True,
        events=(
            *_weather(start, duration_s, seed),
            *_every_quarter(start, duration_s, charge, budget_kw=BUDGET_KW),
        ),
        checks=Checks(
            (
                _quarters_within(start, BUDGET_QUARTER_KWH, BUDGET_QUARTER_MIN_KWH),
                _named("plan_grid_charge", at=VICTRON),
                _named(PEAK_GUARD, at=VICTRON),
            )
        ),
        victron_live=True,
    )


def _plan_boost() -> Scenario:
    """Der e-Golf lädt ab 1 min in Boost, und alle 15 min kommt ein Plan mit ``HALTEN``: Jede neue
    Version behält den Boost (Review Focus 1), und unter ``HALTEN`` lädt Boost aus dem Netz, nicht
    aus dem Akku (Entscheidung 3). Der erste Plan ist Version 2, der Boost darum Version 3."""
    start, duration_s, seed = EVENING, HOUR_S, 28
    boost = _Boost(3, EGOLF, start + timedelta(seconds=duration_s))
    boosting = Check(
        f"Ab {de(BOOST_FROM_S / MINUTE_S)} min lädt „{EGOLF}“ in mindestens "
        f"{de(BOOSTING_SHARE * 100)} % der Sekunden in Boost.",
        _boosting_from,
    )
    return Scenario(
        name="plan-boost",
        title="Boost während Halten",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None),
        intent=_intent(egolf=ChargeMode.PV),
        live=True,
        events=(
            *_weather(start, duration_s, seed),
            *_every_quarter(start, duration_s, HOLD),
            (BOOST_FROM_S, boost),
        ),
        checks=Checks((boosting, _from_battery_at_most(EGOLF, CAR_FROM_BATTERY_PCT))),
        victron_live=True,
    )


# --- Szenarien 29 bis 33: „Geplant“ und Ladelimit, Victron live (Plan 4b, Entscheidung 16) ------


def _departure_s(start: datetime, departure: str) -> int:
    """Die Sekunde des Laufs ab ``start``, in der die Abfahrt ``departure`` (``HH:MM``, Wiener
    Zeit) am selben Tag liegt."""
    local = start.astimezone(LOCAL_TZ)
    hour, minute = (int(part) for part in departure.split(":"))
    at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return int((at - local).total_seconds())


def _geplant_plan() -> Scenario:
    """Der Tesla lädt „Geplant“ mit der Leistung des Plans, 6900 W in vier Slots (Präzisierung 7).
    Der Plan gilt 30 min, dann fällt die app aus (Abweichung, Docstring des Moduls); ab da ist die
    späteste Startzeit erreicht, und er lädt ohne Stopp mit voller Leistung weiter (Review
    Focus 4). Am Ziel stoppt er mit ``target_reached`` und startet nicht wieder."""
    start, duration_s, seed = EVENING, HOUR_S, 29
    target = _Target(2, TESLA, PLANNED_TARGET_PCT, PLAN_DEPARTURE)
    slots = tuple((k, BatteryIntent(), None) for k in range(PLAN_SLOTS))
    plan = _plan(start, 0, slots, valid_s=PLAN_RUNS_OUT_S, vehicles={TESLA: PLAN_TESLA_W})
    checks = (
        _charges_with_at_least(TESLA, PLAN_TESLA_MIN_A),
        _stops_at_target(TESLA, vehicle_actuator(TESLA), PLANNED_TARGET_PCT),
        _charges_with_plan_power(TESLA, PLAN_TESLA_A, PLAN_RUNS_OUT_S),
    )
    return Scenario(
        name="geplant-plan",
        title="Geplant mit Plan, der Plan läuft ab",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, egolf=None, tesla_soc_pct=PLANNED_TESLA_PCT),
        intent=_intent(tesla=ChargeMode.PLANNED),
        live=True,
        events=(*_weather(start, duration_s, seed), (0, target), plan),
        checks=Checks(checks),
        victron_live=True,
    )


def _geplant_spaet() -> Scenario:
    """Der e-Golf steht mit gemessenen 50 % „Geplant“ ohne Plan, Ziel 70 %, Abfahrt in 2 h: Er lädt
    erst ab der spätesten Startzeit (Präzisierung 6), dann mit voller Leistung, und erreicht das
    Ziel vor der Abfahrt. Den Ladestand meldet Home Assistant; dazwischen schätzt der core ihn aus
    dem Zähler der EVCS."""
    start, duration_s, seed = EVENING, 2 * HOUR_S, 30
    evcs = wallbox_actuator(EVCS_BOX)
    checks = (
        _waits_for_latest_start(EGOLF),
        _stops_at_target(EGOLF, evcs, PLANNED_TARGET_PCT),
        _reaches_before(EGOLF, PLANNED_TARGET_PCT, _departure_s(start, LATE_DEPARTURE)),
    )
    return Scenario(
        name="geplant-spaet",
        title="Geplant ohne Plan, Start zur spätesten Startzeit",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None),
        intent=_intent(egolf=ChargeMode.PLANNED),
        live=True,
        events=(
            *_weather(start, duration_s, seed),
            (0, _Target(2, EGOLF, PLANNED_TARGET_PCT, LATE_DEPARTURE)),
        ),
        checks=Checks(checks),
        victron_live=True,
    )


def _geplant_pv() -> Scenario:
    """An einem sonnigen Tag steht der Tesla „Geplant“, und die Pläne nennen ihn mit 0 W: Er lädt
    nur aus dem PV-Überschuss, mit den Zeiten des PV-Modus (Entscheidung 4), höchstens 5 % aus dem
    Akku wie ein PV-Auto, und stoppt am Ziel, obwohl die Sonne weiter scheint."""
    start, duration_s, seed = SUMMER, 2 * HOUR_S, 31
    plans = _every_quarter(start, duration_s, BatteryIntent(), vehicles={TESLA: 0})
    checks = (
        _from_battery_at_most(TESLA, CAR_FROM_BATTERY_PCT),
        _stops_at_target(TESLA, vehicle_actuator(TESLA), PLANNED_TARGET_PCT),
    )
    return Scenario(
        name="geplant-pv",
        title="Geplant mit PV-Überschuss",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, egolf=None, tesla_soc_pct=PLANNED_TESLA_PCT),
        intent=_intent(tesla=ChargeMode.PLANNED),
        live=True,
        events=(
            *_weather(start, duration_s, seed, pv=_sun(start)),
            (0, _Target(2, TESLA, PLANNED_TARGET_PCT, PV_DEPARTURE)),
            *plans,
        ),
        checks=Checks(checks),
        victron_live=True,
    )


def _geplant_unbekannt() -> Scenario:
    """Home Assistant meldet keinen Ladestand des e-Golf: „Geplant“ rechnet mit angenommenen 0 %
    (Entscheidung 3), beginnt zur spätesten Startzeit dafür und erkennt das Ziel nie."""
    start, duration_s, seed = EVENING, 2 * HOUR_S, 32
    checks = (
        _soc_assumed(EGOLF),
        _waits_for_latest_start(EGOLF, within_s=LATEST_START_WITHIN_S),
        _never_named(TARGET_REACHED),
    )
    return Scenario(
        name="geplant-unbekannt",
        title="Geplant ohne Ladestand",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla=None, egolf_soc_known=False),
        intent=_intent(egolf=ChargeMode.PLANNED),
        live=True,
        events=(
            *_weather(start, duration_s, seed),
            (0, _Target(2, EGOLF, PLANNED_TARGET_PCT, UNKNOWN_DEPARTURE)),
        ),
        checks=Checks(checks),
        victron_live=True,
    )


def _tesla_ladelimit() -> Scenario:
    """Mit ``charge_limit_sync`` setzt der core das Ladelimit des Tesla von 60 % auf das Ziel von
    75 %, einmal (Entscheidung 11). Der Tesla steht zu Beginn über dem alten Limit und ist
    „Complete“; die späteste Startzeit ist schon vorbei, er lädt mit voller Leistung und meldet
    „Complete“ bei 75 %."""
    start, duration_s, seed = EVENING, HOUR_S, 33
    checks = (
        _one_charge_limit(TESLA, LIMIT_TARGET_PCT),
        _limit_shown(TESLA, LIMIT_TARGET_PCT),
        _complete_at(TESLA, LIMIT_TARGET_PCT),
    )
    return Scenario(
        name="tesla-ladelimit",
        title="Ladelimit des Tesla an das Ziel angleichen",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(
            _setup,
            soc_pct=60.0,
            egolf=None,
            tesla_soc_pct=LIMIT_TESLA_PCT,
            tesla_limit_pct=LIMIT_START_PCT,
        ),
        intent=_intent(tesla=ChargeMode.PLANNED),
        live=True,
        events=(
            *_weather(start, duration_s, seed),
            (0, _Target(2, TESLA, LIMIT_TARGET_PCT, LIMIT_DEPARTURE)),
        ),
        checks=Checks(checks),
        victron_live=True,
        vehicles=MappingProxyType({TESLA: MappingProxyType({"charge_limit_sync": True})}),
    )


# --- Szenario 34: Sofort-Limit (Plan Sofort-Limit, Entscheidungen 3 und 4) ------------------------


def _sofort_limit() -> Scenario:
    """Sommer, der e-Golf lädt an der EVCS in Sofort mit dem Höchststrom ``NOW_LIMIT_A`` (8 A),
    der Tesla am Wall Connector in PV-Überschuss (Plan Sofort-Limit, Entscheidungen 3 und 4). Das
    Limit gilt hart, auch wenn die Sonne mehr Überschuss gäbe; der Rest geht an den Tesla. Der Lauf
    beginnt wie die Klimaanlagen-Szenarien am Mittag (``SUMMER_NOON``) statt um 10:00: Um 10:00
    bleiben nach Haus und e-Golf (rund 2,9 kW) weniger als der Mindeststrom des Tesla (5 A auf 3
    Phasen, rund 3,5 kW) übrig, er startet erst nach 55 min. Der Gast kommt nicht vor, ihn deckt ein
    Test der Regelung."""
    start, duration_s, seed = SUMMER_NOON, HOUR_S, 40
    evcs = wallbox_actuator(EVCS_BOX)

    def never_above(result: SimResult) -> list[str]:
        currents = [
            (d, d.requested)
            for d in result.decisions
            if d.actuator == evcs and d.kind is CommandKind.CURRENT
        ]
        found = [
            f"Strombefehl {de(amps)} A an die EVCS bei {_second(result, d.ts)} s, "
            f"verlangt höchstens {NOW_LIMIT_A} A."
            for d, amps in currents
            if isinstance(amps, int | float) and amps > NOW_LIMIT_A
        ]
        found += [
            f"„{EGOLF}“ lädt bei {s.t} s mit {de(s.cars[EGOLF].current_a)} A, "
            f"verlangt höchstens {NOW_LIMIT_A} A."
            for s in result.samples
            if s.cars[EGOLF].current_a > NOW_LIMIT_A
        ][:3]
        if not currents:
            found.append("Kein Strombefehl an die EVCS: Die Prüfung hätte nichts gemessen.")
        return found

    def tesla_from_the_surplus(result: SimResult) -> list[str]:
        minutes = _minutes(result.samples, TESLA)
        if minutes >= duration_s / MINUTE_S / 2:
            return []
        shown = shown_number(minutes, 1, lambda number: number >= duration_s / MINUTE_S / 2)
        return [
            f"„{TESLA}“ lädt {shown} min, verlangt mindestens {de(duration_s / MINUTE_S / 2)} min."
        ]

    return Scenario(
        name="sofort-limit",
        title="Sofort mit Ladelimit",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=60.0, tesla_soc_pct=NOW_LIMIT_TESLA_PCT),
        intent=_intent(tesla=ChargeMode.PV, egolf=ChargeMode.NOW, limits={EGOLF: NOW_LIMIT_A}),
        live=True,
        events=_weather(start, duration_s, seed, pv=_sun(start)),
        checks=Checks(
            (
                Check(f"Die EVCS wird nie über {NOW_LIMIT_A} A angefordert.", never_above),
                Check(
                    "Der Tesla lädt in mindestens der Hälfte der Zeit aus dem Überschuss.",
                    tesla_from_the_surplus,
                ),
            )
        ),
    )


# --- Szenarien 35 bis 40: Klimaanlage (Plan 6a, Entscheidung 15) -------------------------------

KLIMA: Final = "klima"  # Schlüssel der Klimaanlage in der Beispielkonfiguration
KLIMA_ACTUATOR: Final = thermal_actuator(KLIMA)
KLIMA_EXPORT_W: Final = 1500.0  # Einspeisung ohne Versatz in „klima-pv-kuehlen“
KLIMA_BASE_W: Final = 600.0  # Leistung der Klimaanlage ohne Versatz beim Kühlen
KLIMA_HEAT_W: Final = 1500.0  # … und beim Heizen
KLIMA_NOISE_W: Final = 20.0  # ein ruhiges Haus: Rauschen der Hauslast je Phase, ± in W
KLIMA_STEP_K: Final = 0.5
KLIMA_STEP_S: Final = 600  # step_interval_s der Beispielkonfiguration
KLIMA_MORE_W: Final = 800.0  # more_above_w der Beispielkonfiguration
KLIMA_MAX_K: Final = 3.0  # max_offset_k der Beispielkonfiguration
CAR_EXPORT_W: Final = 4000.0  # „klima-auto-vorrang“: Einspeisung ohne Versatz, …
CAR_PLUG_S: Final = 40 * MINUTE_S  # … ab hier steckt der e-Golf an
MODE_SWITCH_S: Final = 2115  # „klima-moduswechsel“: Wechsel auf Heizen, nach drei Stufen
SILENT_S: Final = 1500  # „klima-paketverlust“: ab hier schweigt das C.M.I.
HEAT_EXPORT_W: Final = 1600.0  # „klima-spitze“: Einspeisung ohne Versatz beim Heizen, …
# … ab hier (11:30, Beginn einer Viertelstunde) zieht das Haus PEAK_LOAD_W mehr …
PEAK_LOAD_S: Final = 30 * MINUTE_S
PEAK_LOAD_W: Final = 8000.0
KLIMA_PEAK_KW: Final = 5.0  # … bei diesem Ziel des 15-min-Wächters
OWN_USE: Final = BatteryIntent(kind=BatteryIntentKind.EIGENVERBRAUCH)
KLIMA_STALE_S: Final = 180.0  # stale_after_s der Beispielkonfiguration
SILENCE: Final = "erste 0 nach dem Schweigen"
PRESSURE: Final = "Druck des 15-min-Wächters"
KLIMA_SETTLED_S: Final = 10 * MINUTE_S  # über die letzten so vielen Sekunden mitteln die Prüfungen
SUMMER_NOON: Final = datetime(2026, 7, 15, 12, tzinfo=LOCAL_TZ).astimezone(UTC)
WINTER_NOON: Final = datetime(2026, 12, 15, 11, tzinfo=LOCAL_TZ).astimezone(UTC)
WINTER_EVENING: Final = datetime(2026, 12, 15, 18, tzinfo=LOCAL_TZ).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class _AcMode:
    """Die Klimaanlage wechselt die Betriebsart, als Ereignis."""

    mode: AcMode

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        _unit(model).mode = self.mode


@dataclass(frozen=True, slots=True)
class _Silent:
    """Das C.M.I. sendet keine Pakete mehr (``silent``) oder wieder, als Ereignis."""

    silent: bool

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        _unit(model).silent = self.silent


@dataclass(frozen=True, slots=True)
class _Plug:
    """Das Fahrzeug ``vehicle`` wird an die Wallbox ``wallbox`` gesteckt, als Ereignis."""

    vehicle: str
    wallbox: str

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        model.plug(self.vehicle, self.wallbox)


def _unit(model: PlantModel) -> AirConditioner:
    if model.thermal is None:
        raise ValueError("Das Modell hat keine Klimaanlage der Art coe_offset.")
    return model.thermal


def _klima_setup(
    model: PlantModel,
    *,
    mode: AcMode,
    base_w: float,
    soc_pct: float = 100.0,
    charger_only: bool = False,
) -> None:
    """Akku und Multi, kein Auto angesteckt (``_setup``), die Klimaanlage in ``mode`` mit
    ``base_w`` ohne Versatz, die Leistung schon eingeschwungen."""
    _setup(model, soc_pct=soc_pct, tesla=None, egolf=None, charger_only=charger_only)
    unit = _unit(model)
    unit.mode, unit.base_w, unit.power_w = mode, base_w, base_w


def _constant(watts: float) -> Callable[[int], float]:
    def value(t: int) -> float:
        return watts

    return value


def _kelvin(value: float) -> str:
    """Ein Versatz für die Sätze der Prüfungen: „−0,5 K“, „0 K“, „+1 K“."""
    value = round(value, 1) + 0.0  # aus -0.0 wird 0.0
    return f"{'+' if value > 0 else ''}{de(value)} K"


def _thermal_samples(result: SimResult) -> list[tuple[int, ThermalSample]]:
    return [(s.t, s.thermal) for s in result.samples if s.thermal is not None]


def _receipts(result: SimResult) -> list[tuple[int, float]]:
    """Die Versätze, die die UVR vom core bekam, mit ihrer Sekunde und in ihrer Reihenfolge."""
    return [(t, k) for t, unit in _thermal_samples(result) for k in unit.received_k]


def _changes(result: SimResult) -> list[tuple[int, float, float]]:
    """Je Wechsel des Versatzes an der UVR Sekunde, alter und neuer Wert; vor dem ersten Paket
    gilt 0."""
    changes, last = [], 0.0
    for t, k in _receipts(result):
        if k != last:
            changes.append((t, last, k))
            last = k
    return changes


def _sample_at(result: SimResult, t: int) -> ThermalSample | None:
    return next((unit for at, unit in _thermal_samples(result) if at == t), None)


def _steps(
    sign: int, *, at_least: int, at_most: int | None = None, until_s: int | None = None
) -> Check:
    """Bis zur Sekunde ``until_s`` (ohne: bis zum Ende) wechselt der Versatz an der UVR nur in
    Stufen von 0,5 K in Richtung ``sign`` (−1 oder +1), im Abstand von mindestens
    ``KLIMA_STEP_S``, und das mindestens ``at_least``-mal, mit ``at_most`` höchstens so oft."""
    step = sign * KLIMA_STEP_K

    def find(result: SimResult) -> list[str]:
        changes = [c for c in _changes(result) if until_s is None or c[0] < until_s]
        found = [
            f"Die UVR bekam bei {t} s {_kelvin(new)} nach {_kelvin(old)}, "
            f"verlangt ist ein Schritt von {_kelvin(step)}."
            for t, old, new in changes
            if not math.isclose(new - old, step)
        ]
        found += [
            f"Die UVR bekam bei {t} s einen Schritt nur {t - before} s nach dem vorigen, "
            f"verlangt sind mindestens {KLIMA_STEP_S} s."
            for (before, _, _), (t, _, _) in pairwise(changes)
            if t - before < KLIMA_STEP_S
        ]
        if len(changes) < at_least or (at_most is not None and len(changes) > at_most):
            found.append(f"Die UVR bekam {len(changes)} Schritte, verlangt sind {wanted}.")
        return found

    wanted = (
        str(at_least)
        if at_most == at_least
        else f"mindestens {at_least}" + ("" if at_most is None else f", höchstens {at_most}")
    )
    until = "" if until_s is None else f" bis {de(until_s / MINUTE_S)} min"
    text = (
        f"Der Versatz an der UVR wechselt{until} nur in Schritten von {_kelvin(step)} im Abstand "
        f"von mindestens {KLIMA_STEP_S} s, {wanted} Schritte."
    )
    return Check(text, find)


def _offset_within(low_k: float, high_k: float) -> Check:
    """Jeder Versatz, den die UVR bekommt, liegt zwischen ``low_k`` und ``high_k``."""

    def find(result: SimResult) -> list[str]:
        outside = [(t, k) for t, k in _receipts(result) if not low_k <= k <= high_k]
        if not outside:
            return []
        (t, k), worst = outside[0], max(outside, key=lambda found: abs(found[1]))
        return [
            f"Die UVR bekam bei {t} s {_kelvin(k)} ({len(outside)} Pakete außerhalb, "
            f"bis {_kelvin(worst[1])})."
        ]

    return Check(
        f"Jeder Versatz an der UVR liegt zwischen {_kelvin(low_k)} und {_kelvin(high_k)}.", find
    )


MODE_NAMES: Final = {"kuehlen": "Kühlen", "heizen": "Heizen", "lueften": "Lüften", "aus": "Aus"}


def _towards_more(result: SimResult) -> list[str]:
    """Kein Versatz an der UVR gegen „mehr“ der Betriebsart dieser Sekunde: beim Kühlen höchstens
    0, beim Heizen mindestens 0, beim Lüften und Aus genau 0. Für Szenarien ohne „weniger“."""
    wrong = [
        (t, unit.mode, k)
        for t, unit in _thermal_samples(result)
        for k in unit.received_k
        if (unit.mode == "kuehlen" and k > 0)
        or (unit.mode == "heizen" and k < 0)
        or (unit.mode in {"lueften", "aus"} and k != 0)
    ]
    return [
        f"Die UVR bekam bei {t} s {_kelvin(k)} beim {MODE_NAMES.get(mode, mode)} "
        f"({len(wrong)} solche Pakete)."
        for t, mode, k in wrong[:1]
    ]


TOWARDS_MORE: Final = Check(
    "Jeder Versatz an der UVR geht in Richtung „mehr“ ihrer Betriebsart: beim Kühlen höchstens 0, "
    "beim Heizen mindestens 0.",
    _towards_more,
)


def _signed_decisions(switch_s: int) -> Check:
    """Die Entscheidungen an die Klimaanlage verlangen vor der Sekunde ``switch_s`` höchstens 0
    (Kühlen), ab ihr mindestens 0 (Heizen)."""

    def find(result: SimResult) -> list[str]:
        found = []
        for d in _at(result.decisions, KLIMA_ACTUATOR):
            t, k = _second(result, d.ts), finite_number(d.requested)
            if k is not None and (k > 0 if t < switch_s else k < 0):
                found.append(
                    f"Die Regelung verlangte bei {t} s {_kelvin(k)} ({', '.join(d.codes)})."
                )
        return found

    return Check(
        f"Die Regelung verlangt vor {switch_s} s keinen Versatz über 0 und ab dann keinen unter 0.",
        find,
    )


def _export_below(limit_w: float) -> Check:
    """Die Einspeisung liegt über die letzten ``KLIMA_SETTLED_S`` im Mittel unter ``limit_w``."""

    def find(result: SimResult) -> list[str]:
        last = result.samples[-KLIMA_SETTLED_S:]
        export_w = -sum(s.grid_w for s in last) / max(len(last), 1)
        if export_w < limit_w:
            return []
        return [
            f"Die Einspeisung lag in den letzten {de(KLIMA_SETTLED_S / MINUTE_S)} min im Mittel "
            f"bei {export_w:.0f} W, verlangt unter {de(limit_w)} W."
        ]

    text = (
        f"Die Einspeisung liegt in den letzten {de(KLIMA_SETTLED_S / MINUTE_S)} min im Mittel "
        f"unter {de(limit_w)} W."
    )
    return Check(text, find)


def _offset_at_the_end(expected_k: float) -> Check:
    """Der Versatz, den die UVR am Ende anwendet, ist ``expected_k``."""

    def find(result: SimResult) -> list[str]:
        units = _thermal_samples(result)
        if units and math.isclose(units[-1][1].active_k, expected_k):
            return []
        shown = "keinen Versatz" if not units else _kelvin(units[-1][1].active_k)
        return [f"Die UVR wendet am Ende {shown} an, verlangt {_kelvin(expected_k)}."]

    return Check(f"Die UVR wendet am Ende {_kelvin(expected_k)} an.", find)


def _zero_in_the_second(at: Callable[[SimResult], int | None], what: str, reason: str) -> Check:
    """In der Sekunde ``at`` (``what`` sagt, welche das ist) bekommt die UVR 0, als letztes Paket
    dieser Sekunde; die Regelung wünscht dort 0 Stufen mit dem Grund ``reason``."""

    def find(result: SimResult) -> list[str]:
        t = at(result)
        if t is None:
            return [f"Den Zeitpunkt „{what}“ gab es nicht."]
        unit = _sample_at(result, t)
        found = []
        if unit is None or not unit.received_k or unit.received_k[-1] != 0.0:
            got = "nichts" if unit is None or not unit.received_k else _kelvin(unit.received_k[-1])
            found.append(f"Die UVR bekam bei {t} s ({what}) {got}, verlangt 0 K.")
        if unit is not None and (unit.wish, unit.reason) != (0, reason):
            wished = (
                "keinen Wunsch"
                if unit.wish is None
                else f"{unit.wish} Stufen mit {unit.reason or 'keinem Grund'}"
            )
            found.append(f"Die Regelung hatte bei {t} s {wished}, verlangt 0 mit {reason}.")
        return found

    return Check(f"Bei „{what}“ bekommt die UVR im selben Zyklus 0 ({reason}).", find)


def _only_zero_from(at: Callable[[SimResult], int | None], what: str) -> Check:
    """Ab der Sekunde ``at`` bekommt die UVR bis zum Ende nur 0."""

    def find(result: SimResult) -> list[str]:
        t = at(result)
        if t is None:
            return [f"Den Zeitpunkt „{what}“ gab es nicht."]
        later = [(s, k) for s, k in _receipts(result) if s > t and k != 0.0]
        return [f"Die UVR bekam nach {what} bei {s} s {_kelvin(k)}." for s, k in later[:1]]

    return Check(f"Nach „{what}“ bekommt die UVR bis zum Ende nur 0.", find)


def _second_of(seconds: int) -> Callable[[SimResult], int]:
    def at(result: SimResult) -> int:
        return seconds

    return at


def _first_zero_after(begin_s: int) -> Callable[[SimResult], int | None]:
    """Die erste Sekunde ab ``begin_s``, in der die UVR 0 bekommt."""

    def at(result: SimResult) -> int | None:
        return next((t for t, k in _receipts(result) if t >= begin_s and k == 0.0), None)

    return at


def _first_pressure(result: SimResult) -> int | None:
    """Die erste Sekunde, in der der 15-min-Wächter Druck macht."""
    return next((t for t, unit in _thermal_samples(result) if unit.peak_pressure), None)


def _zero_after_silence(result: SimResult) -> list[str]:
    """Die erste 0 nach dem Schweigen des C.M.I. kommt ``stale_after_s`` nach dem letzten Paket,
    höchstens ``WATCH_S`` später; das letzte Paket ging im Takt ``CMI_EVERY_S`` vor
    ``SILENT_S``."""
    t = _first_zero_after(SILENT_S)(result)
    earliest = SILENT_S - CMI_EVERY_S + KLIMA_STALE_S
    latest = SILENT_S + KLIMA_STALE_S + WATCH_S
    wanted = f"verlangt zwischen {earliest:.0f} und {latest:.0f} s"
    if t is None:
        return [f"Die UVR bekam nach dem Schweigen keine 0, {wanted}."]
    if earliest <= t <= latest:
        return []
    return [f"Die UVR bekam die erste 0 nach dem Schweigen bei {t} s, {wanted}."]


def _reached_before(k: float, until_s: int) -> Check:
    """Vor der Sekunde ``until_s`` erreicht der Versatz an der UVR ``k``."""

    def find(result: SimResult) -> list[str]:
        if any(t < until_s and math.isclose(got, k) for t, got in _receipts(result)):
            return []
        return [f"Die UVR bekam vor {until_s} s nie {_kelvin(k)}."]

    return Check(f"Vor {de(until_s / MINUTE_S)} min bekommt die UVR {_kelvin(k)}.", find)


def _backs_off_for_cars(from_s: int) -> Check:
    """Ab der Sekunde ``from_s`` wird der Versatz an der UVR nur kleiner, in Schritten von 0,5 K,
    bis 0, und jede Entscheidung, die ihn kleiner macht, nennt ``thermal_cars``."""

    def find(result: SimResult) -> list[str]:
        changes = [c for c in _changes(result) if c[0] >= from_s]
        found = [
            f"Die UVR bekam bei {t} s {_kelvin(new)} nach {_kelvin(old)}: Der Versatz wurde "
            f"größer, obwohl ein Auto Vorrang hat."
            for t, old, new in changes
            if abs(new) > abs(old)
        ]
        found += [
            f"Die UVR bekam bei {t} s {_kelvin(new)} nach {_kelvin(old)}, "
            f"verlangt ein Schritt von {_kelvin(KLIMA_STEP_K)} zurück."
            for t, old, new in changes
            if abs(new) < abs(old) and not math.isclose(abs(old) - abs(new), KLIMA_STEP_K)
        ]
        last = 0.0
        for d in _at(result.decisions, KLIMA_ACTUATOR):
            k = finite_number(d.requested)
            if k is None:
                continue
            t = _second(result, d.ts)
            if t >= from_s and abs(k) < abs(last) and "thermal_cars" not in d.codes:
                found.append(
                    f"Die Entscheidung bei {t} s auf {_kelvin(k)} nennt {', '.join(d.codes)}, "
                    "verlangt thermal_cars."
                )
            last = k
        return found

    return Check(
        f"Ab {de(from_s / MINUTE_S)} min geht der Versatz in Schritten von 0,5 K zurück, nur "
        "wegen der Autos (thermal_cars), und wird nie größer.",
        find,
    )


def _thermal_reason(code: str) -> Check:
    """Jede Entscheidung an die Klimaanlage außer 0 nennt ``code``.

    Eine 0 geht immer hinaus, auch gleich dem Echo (Ruling R26): Die Regelung bietet sie zu
    Beginn an, mit dem Grund ihres Wunschs 0 (etwa ``thermal_off``)."""

    def find(result: SimResult) -> list[str]:
        return [
            f"Die Entscheidung bei {_second(result, d.ts)} s nennt {', '.join(d.codes)}, "
            f"verlangt {code}."
            for d in _at(result.decisions, KLIMA_ACTUATOR)
            if code not in d.codes and finite_number(d.requested) != 0
        ]

    return Check(f"Jede Entscheidung an die Klimaanlage außer 0 nennt {code}.", find)


def _klima_pv_w(export_w: float, base_w: float) -> float:
    """Die PV, mit der ohne Versatz ``export_w`` eingespeist wird: Haus, Klimaanlage und das."""
    return 3 * HOUSE_W + base_w + export_w


def _klima_pv_kuehlen() -> Scenario:
    """Sommer, Akku voll, Einspeisung 1,5 kW: Die Klimaanlage kühlt stufenweise stärker."""
    start, duration_s, seed = SUMMER_NOON, HOUR_S, 34
    pv = _constant(_klima_pv_w(KLIMA_EXPORT_W, KLIMA_BASE_W))
    return Scenario(
        name="klima-pv-kuehlen",
        title="Klimaanlage nimmt PV-Überschuss beim Kühlen",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_klima_setup, mode="kuehlen", base_w=KLIMA_BASE_W),
        intent=_intent(),
        live=True,
        events=_weather(start, duration_s, seed, pv=pv, noise_w=KLIMA_NOISE_W),
        checks=Checks(
            (
                _steps(-1, at_least=3),
                _offset_within(-KLIMA_MAX_K, 0.0),
                TOWARDS_MORE,
                _thermal_reason("thermal_more"),
                _export_below(KLIMA_MORE_W),
            )
        ),
        thermal=True,
    )


def _klima_auto_vorrang() -> Scenario:
    """Wie „klima-pv-kuehlen“, mit mehr Überschuss; nach 40 min steckt der e-Golf im Modus PV an
    der EVCS an und bekommt den Überschuss, die Klimaanlage geht stufenweise zurück."""
    start, duration_s, seed = SUMMER_NOON, CAR_PLUG_S + 40 * MINUTE_S, 35
    pv = _constant(_klima_pv_w(CAR_EXPORT_W, KLIMA_BASE_W))
    return Scenario(
        name="klima-auto-vorrang",
        title="Klimaanlage weicht einem Auto im Modus PV",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_klima_setup, mode="kuehlen", base_w=KLIMA_BASE_W),
        intent=_intent(egolf=ChargeMode.PV),
        live=True,
        events=(
            *_weather(start, duration_s, seed, pv=pv, noise_w=KLIMA_NOISE_W),
            (CAR_PLUG_S, _Plug(EGOLF, EVCS_BOX)),
        ),
        checks=Checks(
            (
                _steps(-1, at_least=3, until_s=CAR_PLUG_S),
                _backs_off_for_cars(CAR_PLUG_S),
                TOWARDS_MORE,
                _charges_at_the_end(EGOLF),
                _offset_at_the_end(0.0),
                _export_below(KLIMA_MORE_W),
            )
        ),
        thermal=True,
    )


def _klima_moduswechsel() -> Scenario:
    """Kühlen bis −1,5 K, dann Heizen: Im selben Zyklus geht 0 hinaus."""
    start, duration_s, seed = SUMMER_NOON, MODE_SWITCH_S + 15 * MINUTE_S, 36
    pv = _constant(_klima_pv_w(KLIMA_EXPORT_W, KLIMA_BASE_W))
    return Scenario(
        name="klima-moduswechsel",
        title="Betriebsart der Klimaanlage wechselt",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_klima_setup, mode="kuehlen", base_w=KLIMA_BASE_W),
        intent=_intent(),
        live=True,
        events=(
            *_weather(start, duration_s, seed, pv=pv, noise_w=KLIMA_NOISE_W),
            (MODE_SWITCH_S, _AcMode("heizen")),
        ),
        checks=Checks(
            (
                _steps(-1, at_least=3, until_s=MODE_SWITCH_S),
                _zero_in_the_second(
                    _second_of(MODE_SWITCH_S), "Wechsel auf Heizen", "thermal_mode"
                ),
                TOWARDS_MORE,
                _signed_decisions(MODE_SWITCH_S),
            )
        ),
        thermal=True,
    )


def _klima_paketverlust() -> Scenario:
    """Kühlen mit Versatz, dann schweigt das C.M.I.: nach 180 s Versatz 0."""
    start, duration_s, seed = SUMMER_NOON, SILENT_S + 10 * MINUTE_S, 37
    pv = _constant(_klima_pv_w(KLIMA_EXPORT_W, KLIMA_BASE_W))
    return Scenario(
        name="klima-paketverlust",
        title="Pakete der Klimaanlage bleiben aus",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_klima_setup, mode="kuehlen", base_w=KLIMA_BASE_W),
        intent=_intent(),
        live=True,
        events=(
            *_weather(start, duration_s, seed, pv=pv, noise_w=KLIMA_NOISE_W),
            (SILENT_S, _Silent(True)),
        ),
        checks=Checks(
            (
                _reached_before(-1.0, SILENT_S),
                Check(
                    f"Die erste 0 kommt {de(KLIMA_STALE_S)} s nach dem letzten Paket des C.M.I.",
                    _zero_after_silence,
                ),
                _zero_in_the_second(_first_zero_after(SILENT_S), SILENCE, "thermal_stale"),
                _only_zero_from(_first_zero_after(SILENT_S), SILENCE),
                TOWARDS_MORE,
            )
        ),
        thermal=True,
    )


def _klima_spitze() -> Scenario:
    """Heizen mit +1,0 K, dann eine schwere Last: Der 15-min-Wächter reduziert, sofort 0."""
    start, duration_s, seed = WINTER_NOON, PEAK_LOAD_S + 10 * MINUTE_S, 38
    pv = _constant(_klima_pv_w(HEAT_EXPORT_W, KLIMA_HEAT_W))

    def load(t: int) -> float:
        return PEAK_LOAD_W / 3 if t >= PEAK_LOAD_S else 0.0

    return Scenario(
        name="klima-spitze",
        title="Spitze droht beim Heizen",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_klima_setup, mode="heizen", base_w=KLIMA_HEAT_W, charger_only=True),
        intent=_intent(),
        live=True,
        events=_weather(start, duration_s, seed, pv=pv, each=load, noise_w=KLIMA_NOISE_W),
        checks=Checks(
            (
                _reached_before(1.0, PEAK_LOAD_S),
                _zero_in_the_second(_first_pressure, PRESSURE, "thermal_peak"),
                _only_zero_from(_first_pressure, PRESSURE),
                TOWARDS_MORE,
            )
        ),
        control={"peak_target_kw": KLIMA_PEAK_KW},
        thermal=True,
    )


def _klima_teuer_heizen() -> Scenario:
    """Winterabend, der Plan sagt „weniger“: −0,5 K, nach 600 s −1,0 K, nicht weiter."""
    start, duration_s, seed = WINTER_EVENING, HOUR_S, 39
    return Scenario(
        name="klima-teuer-heizen",
        title="Teurer Strom beim Heizen",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_klima_setup, mode="heizen", base_w=KLIMA_HEAT_W, soc_pct=60.0),
        intent=_intent(),
        live=True,
        events=(
            *_weather(start, duration_s, seed, noise_w=KLIMA_NOISE_W),
            *_every_quarter(start, duration_s, OWN_USE, thermal="weniger"),
        ),
        checks=Checks(
            (
                _steps(-1, at_least=2, at_most=2),
                _offset_within(-2 * KLIMA_STEP_K, 0.0),
                _thermal_reason("thermal_less"),
            )
        ),
        thermal=True,
    )


# --- Szenarien 40 bis 42: Vorheizen und Vorkühlen (Plan 5c, Task 8) ---------------------------

# Je Slot des Plans: Planwert in Stufen, Netzbezug dafür in W und der Hinweis für die Klimaanlage
ShiftSlot = tuple[int, float, ThermalHint]
SHIFT_GRID_W: Final = 200.0  # geplanter Netzbezug je Stufe: 0,5 K mal ``AC_GAIN_W_PER_K``
# „klima-plan-vorheizen“: +3 in der billigen Nacht …
CHEAP: Final[ShiftSlot] = (3, 3 * SHIFT_GRID_W, "neutral")
EXPENSIVE: Final[ShiftSlot] = (-2, 0.0, "weniger")  # … und −2 im teuren Block ab …
EXPENSIVE_S: Final = 3 * QUARTER_S  # … dieser Sekunde (06:45)
# „klima-plan-akku“ und „klima-plan-ende“: +2
PLAN_UP: Final[ShiftSlot] = (2, 2 * SHIFT_GRID_W, "neutral")
BATTERY_COVERS_S: Final = 25 * MINUTE_S  # „klima-plan-akku“: ab hier deckt der Akku die Last
GUARD_WITHIN_S: Final = 6 * MINUTE_S  # … und binnen so vieler Sekunden geht eine Stufe zurück
PLAN_ENDS_S: Final = 20 * MINUTE_S  # „klima-plan-ende“: so lange gilt der einzige Plan
PLAN_END: Final = "Ende des Plans"
WINTER_NIGHT: Final = datetime(2026, 12, 16, 6, tzinfo=LOCAL_TZ).astimezone(UTC)


@dataclass(frozen=True, slots=True)
class _ShiftPlan:
    """Wie ``_Plan``, mit dem Planwert der Klimaanlage für die Betriebsart ``mode`` (Plan 5c).

    ``slots`` nennt je Slot ab der Viertelstunde, in der das Szenario beginnt (``start``), den
    Versatz in Viertelstunden und ``ShiftSlot``; die Absicht jedes Slots ist Eigenverbrauch. Der
    Plan entsteht in der Sekunde ``at_s`` und gilt ``valid_s`` danach.
    """

    start: datetime
    at_s: int
    slots: tuple[tuple[int, ShiftSlot], ...]
    mode: ThermalMode
    _: KW_ONLY
    valid_s: int

    def plan(self) -> IntentPlan:
        created = self.start + timedelta(seconds=self.at_s)
        first = slot_start(self.start)
        return IntentPlan(
            id=self.at_s,
            created_at=created,
            valid_until=created + timedelta(seconds=self.valid_s),
            grid_charge_allowed=False,
            slots=[
                IntentPlanSlot(
                    start=first + offset * SLOT,
                    intent=OWN_USE,
                    thermal=hint,
                    thermal_steps=steps,
                    thermal_grid_w=grid_w,
                    thermal_mode=self.mode,
                )
                for offset, (steps, grid_w, hint) in self.slots
            ],
        )

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        _submit_plan(rt, self.plan())


def _shift_every_quarter(
    start: datetime,
    duration_s: int,
    slot: Callable[[int], ShiftSlot],
    mode: ThermalMode,
) -> tuple[tuple[int, Action], ...]:
    """Pläne wie von der app: einer zu Beginn, dann zu jeder Viertelstunde des Laufs, je mit
    ``PLAN_SLOTS`` Slots ab seiner Viertelstunde, gültig für ``PLAN_VALID_S``; ``slot`` gibt je
    Viertelstunde des Laufs (0 ist die erste) Planwert, Netzbezug und Hinweis."""
    into = int((start - slot_start(start)).total_seconds())
    seconds = sorted({0, *range((QUARTER_S - into) % QUARTER_S, duration_s, QUARTER_S)})
    events: list[tuple[int, Action]] = []
    for t in seconds:
        first = (into + t) // QUARTER_S
        slots = tuple((q, slot(q)) for q in range(first, first + PLAN_SLOTS))
        events.append((t, _ShiftPlan(start, t, slots, mode, valid_s=PLAN_VALID_S)))
    return tuple(events)


@dataclass(frozen=True, slots=True)
class _BatteryCovers:
    """Der Akku darf wieder entladen (``charger_only`` aus), als Ereignis: Mit Victron im Dry-Run
    deckt der ESS dann jede Last, auch die, die der Plan dem Netz zugedacht hat."""

    def __call__(self, model: PlantModel, rt: CoreRuntime) -> None:
        model.charger_only = False


def _step_rule(result: SimResult) -> list[str]:
    """Jede Erhöhung des Betrags an der UVR ist eine Stufe von 0,5 K, geht nicht über 0 hinweg
    und kommt mindestens ``KLIMA_STEP_S`` nach der vorigen Erhöhung; Verringerungen sind frei."""
    found, raised = [], None
    for t, old, new in _changes(result):
        if new != 0 and old * new < 0:
            found.append(
                f"Die UVR bekam bei {t} s {_kelvin(new)} nach {_kelvin(old)} über 0 hinweg."
            )
            raised = t
            continue
        if abs(new) <= abs(old):
            continue
        if not math.isclose(abs(new) - abs(old), KLIMA_STEP_K):
            found.append(
                f"Die UVR bekam bei {t} s {_kelvin(new)} nach {_kelvin(old)}, verlangt ist eine "
                f"Stufe von {_kelvin(KLIMA_STEP_K)}."
            )
        if raised is not None and t - raised < KLIMA_STEP_S:
            found.append(
                f"Die UVR bekam bei {t} s eine Erhöhung nur {t - raised} s nach der vorigen, "
                f"verlangt sind mindestens {KLIMA_STEP_S} s."
            )
        raised = t
    return found


STEP_RULE: Final = Check(
    f"Jede Erhöhung des Betrags an der UVR ist eine Stufe von {_kelvin(KLIMA_STEP_K)}, geht nicht "
    f"über 0 hinweg und kommt mindestens {KLIMA_STEP_S} s nach der vorigen.",
    _step_rule,
)


def _never_rejected(result: SimResult) -> list[str]:
    """Die Safety-Schicht lehnt keinen Versatz an die Klimaanlage ab."""
    return [
        f"Die Safety-Schicht lehnte bei {_second(result, d.ts)} s "
        f"{_kelvin(finite_number(d.requested) or 0.0)} ab ({', '.join(d.codes)})."
        for d in _at(result.decisions, KLIMA_ACTUATOR)
        if d.result == DecisionResult.REJECTED
    ]


NEVER_REJECTED: Final = Check(
    "Die Safety-Schicht lehnt keinen Versatz an die Klimaanlage ab.", _never_rejected
)


def _backs_off(from_s: int, codes: frozenset[str], within_s: int) -> Check:
    """Ab der Sekunde ``from_s`` wird der Versatz an der UVR nur kleiner, zuerst binnen
    ``within_s``, und jede Entscheidung, die ihn kleiner macht, nennt einen der ``codes``."""
    named = " oder ".join(sorted(codes))

    def find(result: SimResult) -> list[str]:
        changes = [c for c in _changes(result) if c[0] >= from_s]
        found = [
            f"Die UVR bekam bei {t} s {_kelvin(new)} nach {_kelvin(old)}: Der Versatz wurde größer."
            for t, old, new in changes
            if abs(new) > abs(old)
        ]
        first = next((t for t, old, new in changes if abs(new) < abs(old)), None)
        if first is None or first - from_s > within_s:
            shown = "nie" if first is None else f"erst bei {first} s"
            found.append(
                f"Der Versatz an der UVR ging nach {from_s} s {shown} zurück, verlangt binnen "
                f"{within_s} s."
            )
        last = 0.0
        for d in _at(result.decisions, KLIMA_ACTUATOR):
            k = finite_number(d.requested)
            if k is None:
                continue
            t = _second(result, d.ts)
            if t >= from_s and abs(k) < abs(last) and not codes & set(d.codes):
                found.append(
                    f"Die Entscheidung bei {t} s auf {_kelvin(k)} nennt {', '.join(d.codes)}, "
                    f"verlangt {named}."
                )
            last = k
        return found

    return Check(
        f"Ab {de(from_s / MINUTE_S)} min geht der Versatz an der UVR binnen {within_s} s zurück, "
        f"nur wegen {named}, und wird nie größer.",
        find,
    )


def _without_plan_from(from_s: int) -> Check:
    """Nach der Sekunde ``from_s`` nennt die Regelung keinen Planwert, und keine Entscheidung an
    die Klimaanlage außer 0 nennt ``thermal_plan`` (die 0 zum Ende des Plans nennt ihn)."""

    def find(result: SimResult) -> list[str]:
        found = [
            f"Die Regelung nannte bei {t} s den Planwert {unit.planned}."
            for t, unit in _thermal_samples(result)
            if t > from_s and unit.planned is not None
        ][:1]
        found += [
            f"Die Entscheidung bei {t} s nennt thermal_plan."
            for d in _at(result.decisions, KLIMA_ACTUATOR)
            if (t := _second(result, d.ts)) > from_s
            and "thermal_plan" in d.codes
            and finite_number(d.requested) != 0
        ]
        return found

    return Check(
        f"Nach {de(from_s / MINUTE_S)} min nennt die Regelung keinen Planwert und keine "
        "Entscheidung außer 0 thermal_plan.",
        find,
    )


def _klima_plan_vorheizen() -> Scenario:
    """Billige Nacht, teurer Morgen: Der Wunsch steigt planmäßig auf +3 (+1,5 K), im teuren Block
    über 0 auf −2 (−1,0 K), nie über die Schrittregel."""
    start, duration_s, seed = WINTER_NIGHT, EXPENSIVE_S + 30 * MINUTE_S, 41

    def slot(q: int) -> ShiftSlot:
        return CHEAP if q < EXPENSIVE_S // QUARTER_S else EXPENSIVE

    return Scenario(
        name="klima-plan-vorheizen",
        title="Vorheizen in der billigen Nacht",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(
            _klima_setup, mode="heizen", base_w=KLIMA_HEAT_W, soc_pct=60.0, charger_only=True
        ),
        intent=_intent(),
        live=True,
        events=(
            *_weather(start, duration_s, seed, noise_w=KLIMA_NOISE_W),
            *_shift_every_quarter(start, duration_s, slot, "heizen"),
        ),
        checks=Checks(
            (
                _reached_before(3 * KLIMA_STEP_K, EXPENSIVE_S),
                _zero_in_the_second(
                    _second_of(EXPENSIVE_S), "Beginn des teuren Blocks", "thermal_plan"
                ),
                _reached_before(-2 * KLIMA_STEP_K, duration_s),
                _offset_within(-2 * KLIMA_STEP_K, 3 * KLIMA_STEP_K),
                STEP_RULE,
                NEVER_REJECTED,
                _thermal_reason("thermal_plan"),
            )
        ),
        thermal=True,
    )


def _klima_plan_akku() -> Scenario:
    """Planwert +2 aus dem Netz, dann deckt der Akku die Last: binnen 6 min eine Stufe zurück und
    keine mehr hoch, solange er entlädt."""
    start, duration_s, seed = WINTER_NIGHT, BATTERY_COVERS_S + 15 * MINUTE_S, 42
    return Scenario(
        name="klima-plan-akku",
        title="Akku deckt das geplante Vorheizen",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(
            _klima_setup, mode="heizen", base_w=KLIMA_HEAT_W, soc_pct=60.0, charger_only=True
        ),
        intent=_intent(),
        live=True,
        events=(
            *_weather(start, duration_s, seed, noise_w=KLIMA_NOISE_W),
            *_shift_every_quarter(start, duration_s, lambda q: PLAN_UP, "heizen"),
            (BATTERY_COVERS_S, _BatteryCovers()),
        ),
        checks=Checks(
            (
                _reached_before(2 * KLIMA_STEP_K, BATTERY_COVERS_S),
                _backs_off(BATTERY_COVERS_S, frozenset({"thermal_battery"}), GUARD_WITHIN_S),
                _offset_at_the_end(0.0),
                TOWARDS_MORE,
                STEP_RULE,
                NEVER_REJECTED,
            )
        ),
        thermal=True,
    )


def _klima_plan_ende() -> Scenario:
    """Planwert +2, dann läuft der Plan ab und die app schweigt: Im selben Zyklus gehen die
    geplanten Stufen auf 0 (kein Vorheizen ohne Plan, Entscheidung 8), danach gilt 6a."""
    start, duration_s, seed = WINTER_NIGHT, PLAN_ENDS_S + 15 * MINUTE_S, 43
    slots = tuple((q, PLAN_UP) for q in range(PLAN_SLOTS))
    return Scenario(
        name="klima-plan-ende",
        title="Plan der Klimaanlage läuft ab",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(
            _klima_setup, mode="heizen", base_w=KLIMA_HEAT_W, soc_pct=60.0, charger_only=True
        ),
        intent=_intent(),
        live=True,
        events=(
            *_weather(start, duration_s, seed, noise_w=KLIMA_NOISE_W),
            (0, _ShiftPlan(start, 0, slots, "heizen", valid_s=PLAN_ENDS_S)),
        ),
        checks=Checks(
            (
                _reached_before(2 * KLIMA_STEP_K, PLAN_ENDS_S),
                _without_plan_from(PLAN_ENDS_S),
                _zero_in_the_second(_second_of(PLAN_ENDS_S), PLAN_END, "thermal_plan"),
                _only_zero_from(_second_of(PLAN_ENDS_S), PLAN_END),
                _offset_at_the_end(0.0),
                TOWARDS_MORE,
            )
        ),
        thermal=True,
    )


# --- Szenarien 44 und 45: Akku ins Auto, Victron live (Plan Akku ins Auto, Task 8) ---------------

SPARE_BAND_W: Final = 300.0  # Toleranz der Prüfungen auf den Netzbezug in beiden Szenarien
DISCHARGE_W: Final = 12000.0  # max_discharge_w der Beispielkonfiguration: Entladegrenze des Modells
SPARE_THRESHOLD_PCT: Final = 70.0  # „akku-uebrig-sofort“: Plan-Schwelle des Slots …
SPARE_SOC_PCT: Final = 72.0  # … und SOC des Akkus zu Beginn
SPARE_SLOTS: Final = 2  # … der Plan hat so viele Slots, je mit übrigem Akku für Sofort
SPARE_TESLA_A: Final = 10  # … der Tesla lädt in Sofort mit höchstens so viel Strom (now_max_a)
# „akku-uebrig-geplant“: Slot-Leistung und Akkuanteil des e-Golf; der core lädt ihn mit 7 A auf
# zwei Phasen (3220 W), die letzte Stufe gibt auch der Akku (Fix-Welle F7) …
PLANNED_BATTERY_W: Final = 2800
PLANNED_SOC_PCT: Final = 60.0  # … SOC des Akkus zu Beginn …
# … und der geplante SOC am Ende je Slot: Der Akkuanteil endet im dritten Slot, wenn der SOC
# seinen Wert erreicht; der Akku verliert mit Auto (3220 W) und Haus rund 3,6 Punkte je Slot
PLANNED_SOC_END_PCT: Final = (55.0, 52.0, 51.0)
PLANNED_SHARE_SLOT: Final = 2  # in diesem Slot (vom ersten an gezählt ab 0) endet der Akkuanteil
SOC_END_MARGIN_PCT: Final = 1.0  # der SOC fällt höchstens so weit unter das kleinste SOC am Ende
SPARE_TARGET_PCT: Final = 80  # das Ziel des e-Golf in „akku-uebrig-geplant“, …
SPARE_DEPARTURE: Final = "07:00"  # … seine Abfahrt: Die späteste Startzeit liegt nach dem Lauf
BATTERY_SPARE: Final = "battery_spare"  # Grund: Geplant oder Sofort laden aus übrigem Akku
BATTERY_COMP: Final = "battery_comp"  # Grund: Der Fehlbetrag der Autos geht aufs Netz


def _first_soc_at_or_below(samples: Sequence[Sample], pct: float) -> int | None:
    """Die erste Sekunde, in der der SOC ``pct`` erreicht (``≤``); ``None``, wenn nie."""
    return next((s.t for s in samples if s.soc_pct <= pct), None)


def _grid_at_most_over_the_discharge_limit(result: SimResult) -> list[str]:
    """Ab ``SETTLED_S``, solange der SOC über ``SPARE_THRESHOLD_PCT`` liegt, ist der Netzbezug in
    jeder Sekunde höchstens ``max(0, Haus + Autos − DISCHARGE_W) + SPARE_BAND_W``: Der Akku trägt
    Haus und Auto, kein Override des core holt das Auto aus dem Netz. Es muss solche Sekunden
    geben, und mindestens eine, in der ein Auto lädt."""
    reached = _first_soc_at_or_below(result.samples, SPARE_THRESHOLD_PCT)
    above = [s for s in result.samples if s.t >= SETTLED_S and (reached is None or s.t < reached)]
    if not any(_any_charging(s) for s in above):
        return [
            f"Ab {SETTLED_S} s lädt kein Auto, solange der SOC über "
            f"{de(SPARE_THRESHOLD_PCT)} % liegt."
        ]
    over = [
        (s, max(0.0, s.house_w + cars_w(s) - DISCHARGE_W) + SPARE_BAND_W)
        for s in above
        if s.grid_w > max(0.0, s.house_w + cars_w(s) - DISCHARGE_W) + SPARE_BAND_W
    ]
    if not over:
        return []
    first, limit = over[0]
    return [
        f"Netzbezug bei {first.t} s {shown_number(first.grid_w, 0, lambda n: n <= limit)} W, "
        f"verlangt höchstens {de(round(limit))} W ({len(over)} s darüber)."
    ]


def _grid_carries_the_cars_from(begin: Callable[[SimResult], int | None], what: str) -> Check:
    """Ab ``SETTLED_S`` nach der Sekunde ``begin`` ist der Netzbezug in jeder Sekunde mindestens
    die Leistung der Autos minus ``SPARE_BAND_W``: Der Akku gibt den Autos nichts mehr. ``what``
    nennt die Sekunde in der Prüfung; ohne sie oder ohne ladendes Auto danach ist das ein
    Verstoß."""

    def find(result: SimResult) -> list[str]:
        at = begin(result)
        if at is None:
            return [f"Es gibt keine Sekunde „{what}“."]
        after = [s for s in result.samples if s.t >= at + SETTLED_S]
        if not any(_any_charging(s) for s in after):
            return [f"Ab {at + SETTLED_S} s ({what} plus {SETTLED_S} s) lädt kein Auto."]
        under = [s for s in after if s.grid_w < cars_w(s) - SPARE_BAND_W]
        if not under:
            return []
        first = under[0]
        low = cars_w(first) - SPARE_BAND_W
        shown = shown_number(first.grid_w, 0, lambda n: n >= low)
        return [
            f"Netzbezug bei {first.t} s {shown} W, verlangt mindestens {de(round(low))} W "
            f"(Autos {de(round(cars_w(first)))} W, {len(under)} s darunter)."
        ]

    text = (
        f"Ab {de(SETTLED_S / MINUTE_S)} min nach {what} liegt der Netzbezug in jeder Sekunde "
        f"mindestens bei der Leistung der Autos minus {de(SPARE_BAND_W)} W."
    )
    return Check(text, find)


def _spare_before_comp(result: SimResult) -> list[str]:
    """Vor der ersten Entscheidung mit ``BATTERY_COMP`` zeigt mindestens eine Gesundheit den
    übrigen Akku für Sofort mit einem Anteil (``battery.spare`` „on“, ``battery.spare_w`` über 0);
    die Entscheidung gibt es. Im Log selbst steht ``BATTERY_SPARE`` hier nie: Der Akku trägt den
    ganzen Fehlbetrag, und ohne Rest fürs Netz gibt es keinen Override und keine Entscheidung
    (Abweichung, Docstring des Moduls)."""
    comp = next((d for d in result.decisions if BATTERY_COMP in d.codes), None)
    if comp is None:
        return [f"Keine Entscheidung nennt „{BATTERY_COMP}“."]
    until = _second(result, comp.ts)
    for t, health in result.health:
        if t >= until:
            break
        share = finite_number(health.battery.get("spare_w"))
        if health.battery.get("spare") == "on" and share is not None and share > 0:
            return []
    return [
        f"Vor der ersten Entscheidung mit „{BATTERY_COMP}“ bei {until} s zeigt keine Gesundheit "
        f"„battery.spare“ „on“ mit einem Anteil über 0 W."
    ]


def _planned_share_ends(result: SimResult) -> int | None:
    """Die erste Sekunde, in der der SOC den geplanten SOC am Ende seines Slots erreicht
    (``PLANNED_SOC_END_PCT``): Dort endet der Akkuanteil des Autos in Geplant."""
    for s in result.samples:
        slot = min(s.t // QUARTER_S, len(PLANNED_SOC_END_PCT) - 1)
        if s.soc_pct <= PLANNED_SOC_END_PCT[slot]:
            return s.t
    return None


def _no_grid_for_the_car(result: SimResult) -> list[str]:
    """Ab ``SETTLED_S`` bis zum Ende des Akkuanteils (``_planned_share_ends``) ist der Netzbezug
    in jeder Sekunde höchstens ``SPARE_BAND_W``; der Akkuanteil endet im Slot
    ``PLANNED_SHARE_SLOT``, und davor lädt ein Auto."""
    end = _planned_share_ends(result)
    found: list[str] = []
    if end is None or end // QUARTER_S != PLANNED_SHARE_SLOT:
        where = "nie" if end is None else f"bei {end} s"
        found.append(f"Der Akkuanteil endet {where}, verlangt im Slot {PLANNED_SHARE_SLOT + 1}.")
    before = [s for s in result.samples if SETTLED_S <= s.t and (end is None or s.t < end)]
    if not any(_any_charging(s) for s in before):
        return [*found, "Kein Auto lädt, solange der Akkuanteil gilt."]
    return [*found, *_above(before, lambda s: s.grid_w, SPARE_BAND_W, "Netzbezug")]


def _soc_above_the_plan(result: SimResult) -> list[str]:
    """Der SOC fällt nie unter das kleinste SOC am Ende (``PLANNED_SOC_END_PCT``) minus
    ``SOC_END_MARGIN_PCT``."""
    floor = min(PLANNED_SOC_END_PCT) - SOC_END_MARGIN_PCT
    low = min(result.samples, key=lambda s: s.soc_pct)
    if low.soc_pct >= floor:
        return []
    shown = shown_number(low.soc_pct, 2, lambda n: n >= floor)
    return [f"Der SOC fällt bei {low.t} s auf {shown} %, verlangt mindestens {de(floor)} %."]


def _akku_uebrig_sofort() -> Scenario:
    """Der Tesla lädt am Abend in Sofort, der Plan erlaubt in jedem Slot übrigen Akku für Sofort
    über der Plan-Schwelle von 70 % (Plan Akku ins Auto, Entscheidung 6). Solange der SOC darüber
    liegt, trägt der Akku Haus und Auto ohne Override; erreicht er sie, holt der Override das Auto
    aus dem Netz (``battery_comp``), und ohne PV kommt der SOC nicht mehr 5 Punkte darüber."""
    start, duration_s, seed = EVENING, 30 * MINUTE_S, 44
    slots = tuple((k, OWN_USE, None) for k in range(SPARE_SLOTS))
    plan = _plan(
        start,
        0,
        slots,
        valid_s=duration_s,
        now_battery=True,
        support_soc_pct=SPARE_THRESHOLD_PCT,
    )
    carried = Check(
        f"Solange der SOC über {de(SPARE_THRESHOLD_PCT)} % liegt, ab "
        f"{de(SETTLED_S / MINUTE_S)} min kein Override des core: Netzbezug höchstens Haus und "
        f"Autos über der Entladegrenze des Modells ({de(DISCHARGE_W)} W) plus "
        f"{de(SPARE_BAND_W)} W.",
        _grid_at_most_over_the_discharge_limit,
    )

    def reached(result: SimResult) -> int | None:
        return _first_soc_at_or_below(result.samples, SPARE_THRESHOLD_PCT)

    return Scenario(
        name="akku-uebrig-sofort",
        title="Sofort am Abend mit übrigem Akku",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=SPARE_SOC_PCT, egolf=None),
        intent=_intent(tesla=ChargeMode.NOW, limits={TESLA: SPARE_TESLA_A}),
        live=True,
        events=(*_weather(start, duration_s, seed), plan),
        checks=Checks(
            (
                carried,
                _grid_carries_the_cars_from(
                    reached, f"dem SOC {de(SPARE_THRESHOLD_PCT)} % (Akku nicht ins Auto)"
                ),
                Check(
                    f"Vor der ersten Entscheidung mit „{BATTERY_COMP}“ zeigt die Gesundheit den "
                    f"übrigen Akku („battery.spare“ „on“, Anteil über 0 W; Grund "
                    f"„{BATTERY_SPARE}“).",
                    _spare_before_comp,
                ),
            )
        ),
        victron_live=True,
    )


def _akku_uebrig_geplant() -> Scenario:
    """Der e-Golf lädt in der Nacht „Geplant“ mit 2800 W (``PLANNED_BATTERY_W``), alle aus dem Akku
    (``ev_battery_w``), solange der SOC über dem geplanten SOC am Ende des Slots liegt (Plan Akku
    ins Auto, Entscheidung 4). Der core rundet auf 7 A auf (3220 W, Plan 4b, Präzisierung 7); die
    letzte Stromstufe gibt auch der Akku (Fix-Welle F7). Das Haus braucht mehr, als der Plan
    annimmt: Im dritten Slot erreicht der SOC sein Ende, und der Rest kommt aus dem Netz; der Akku
    trägt nur noch das Haus."""
    start, duration_s, seed = NIGHT, 45 * MINUTE_S, 45
    slots = tuple((k, OWN_USE, None) for k in range(len(PLANNED_SOC_END_PCT)))
    plan = _plan(
        start,
        0,
        slots,
        valid_s=duration_s,
        vehicles={EGOLF: PLANNED_BATTERY_W},
        vehicles_battery={EGOLF: PLANNED_BATTERY_W},
        soc_end_pct=PLANNED_SOC_END_PCT,
    )
    floor = min(PLANNED_SOC_END_PCT) - SOC_END_MARGIN_PCT
    return Scenario(
        name="akku-uebrig-geplant",
        title="Geplant in der Nacht aus dem Akku",
        start=start,
        duration_s=duration_s,
        seed=seed,
        setup=partial(_setup, soc_pct=PLANNED_SOC_PCT, tesla=None),
        intent=_intent(egolf=ChargeMode.PLANNED),
        live=True,
        events=(
            *_weather(start, duration_s, seed),
            (0, _Target(2, EGOLF, SPARE_TARGET_PCT, SPARE_DEPARTURE)),
            plan,
        ),
        checks=Checks(
            (
                Check(
                    f"Ab {de(SETTLED_S / MINUTE_S)} min, bis der SOC das geplante Ende seines "
                    f"Slots erreicht (im Slot {PLANNED_SHARE_SLOT + 1}), höchstens "
                    f"{de(SPARE_BAND_W)} W Netzbezug für das Auto.",
                    _no_grid_for_the_car,
                ),
                _grid_carries_the_cars_from(_planned_share_ends, "dem Ende des Akkuanteils"),
                Check(
                    f"Der SOC fällt nie unter {de(floor)} % (kleinstes SOC am Ende minus "
                    f"{de(SOC_END_MARGIN_PCT)} Punkt).",
                    _soc_above_the_plan,
                ),
            )
        ),
        victron_live=True,
    )


SCENARIOS: Final[dict[str, Scenario]] = {
    scenario.name: scenario
    for scenario in (
        _sonnig(),
        _wolkig(),
        _abend_sofort(),
        _reserve(),
        _nur_laden(),
        _tesla_5a(),
        _tesla_schlaeft(),
        _ausfaelle(),
        _zuordnung(),
        _dry_run(),
        _akku_wolkig(),
        _akku_minpv(),
        _akku_sofort(),
        _boost_abend(),
        _override_stunde(),
        _dess_an(),
        _spitze_15min(),
        _schieflast_pv(),
        _tesla_evcs(),
        _egolf_twc(),
        _gast_evcs(),
        _tesla_aus(),
        _tesla_selbststart(),
        _plan_halten(),
        _plan_netzladen(),
        _plan_abgelaufen(),
        _plan_budget(),
        _plan_boost(),
        _geplant_plan(),
        _geplant_spaet(),
        _geplant_pv(),
        _geplant_unbekannt(),
        _tesla_ladelimit(),
        _sofort_limit(),
        _klima_pv_kuehlen(),
        _klima_auto_vorrang(),
        _klima_moduswechsel(),
        _klima_paketverlust(),
        _klima_spitze(),
        _klima_teuer_heizen(),
        _klima_plan_vorheizen(),
        _klima_plan_akku(),
        _klima_plan_ende(),
        _akku_uebrig_sofort(),
        _akku_uebrig_geplant(),
    )
}

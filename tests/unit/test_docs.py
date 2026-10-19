import importlib.util
import json
import re
from pathlib import Path

import pytest

from bindaems.app.planning.execution import EXECUTION_TEXTS


def test_acceptance_checklist_covers_spec_18() -> None:
    text = Path("docs/verification/abnahme-phase-1.md").read_text(encoding="utf-8")
    for criterion in (
        "7 Tage lückenlose Aufzeichnung",
        "±2 %",
        "VRM",
        "Preise und Prognose sichtbar",
        "keine Schreibpfade",
    ):
        assert criterion in text


def test_acceptance_checklist_of_phase_4_covers_the_statistics_part() -> None:
    # Spec 18, Phase 4: die Abrechnung über 30 Tage innerhalb ±1 % der Zählerstände; das README
    # nennt die Datei.
    text = " ".join(Path("docs/verification/abnahme-phase-4.md").read_text("utf-8").split())
    for criterion in (
        "30 Tage",
        "±1 %",
        "GET /api/ledger/checks",
        "`import`",
        "`export`",
        "Markierte Tage",
        "Zähler des Netzbetreibers",
        "Unterschrift",
    ):
        assert criterion in text, criterion
    assert "folgt mit Plan 4b" not in text  # Plan 4b hat beide Punkte nachgetragen
    assert "abnahme-phase-4.md" in Path("docs/verification/README.md").read_text("utf-8")


def _checklist_section(heading: str) -> str:
    """Der Abschnitt ``## heading`` der Checkliste der Phase 4 bis zum nächsten ``## ``."""
    text = Path("docs/verification/abnahme-phase-4.md").read_text(encoding="utf-8")
    _, found, rest = text.partition(f"\n## {heading}\n")
    assert found, f"Der Abschnitt „{heading}“ fehlt in der Checkliste der Phase 4."
    return rest.split("\n## ", 1)[0]


def test_acceptance_checklist_of_phase_4_covers_the_planned_part() -> None:
    # Plan 4b, Entscheidung 15: „Ziel-SOC“ mit Zeitraum, Abruf, Quote und Begründung jeder
    # verfehlten Ladung; „HA in beide Richtungen“ mit einer Zeile je Bedienelement der app.
    from bindaems.app.ha.entities import build_entities
    from bindaems.shared.config import load_config

    target = " ".join(_checklist_section("4. Ziel-SOC").split())
    for topic in (
        "Prüfzeitraum",
        "GET /api/stats?period=month",
        "`planned`",
        "`reached`",
        "`feasible`",
        "`missed`",
        "`infeasible`",
        "`early`",
        "`unknown`",
        "`changed`",
        "mindestens 95 %",
        "Begründung",
        "planned_charge",
    ):
        assert topic in target, topic
    section = _checklist_section("5. HA in beide Richtungen")
    rows = {
        re.sub(r" \(.*\)$", "", cells[0])
        for line in section.splitlines()
        if line.startswith("| ")
        and (cells := [cell.strip() for cell in line.strip().strip("|").split("|")])
        and cells[0] not in {"Bedienelement", "---"}
    }
    cfg = load_config(Path("deploy/config.example.yaml"))
    assert rows == {spec.name for spec in build_entities(cfg) if spec.command}
    for topic in ("in Home Assistant ändern", "im UI ändern", "Taste ohne Zustand"):
        assert topic in " ".join(section.split()), topic


def test_rollout_of_plan_4a_in_the_guide() -> None:
    # „Statistik ausrollen (Plan 4a)“: nur die app, der core bleibt, Migration beim Start, erste
    # Werte prüfen, Tarif nachtragen, Grafana optional.
    section = " ".join(_guide_section("8. Tarif und Abrechnung").split())
    _, found, text = section.partition("### Statistik ausrollen (Plan 4a) ")
    assert found, "Der Unterabschnitt „Statistik ausrollen (Plan 4a)“ fehlt."
    for topic in (
        "docker compose pull ems-app && docker compose up -d --no-deps ems-app",
        "docker compose up -d --build --no-deps ems-app",
        "/backup/vor-plan-4a.sqlite3",
        "Der core läuft weiter",
        "der Regelbetrieb bleibt an",
        "`0010`",
        "Seite **Statistik**",
        "OeMAG-Werte und monatliche Gebühren",
        "Grafana-Dashboard importieren (optional)",
        "docs/verification/abnahme-phase-4.md",
    ):
        assert topic in text, topic
    assert "Einstandspreis des Akkus" in section


def test_rollout_of_plan_4b_in_the_guide() -> None:
    # „Geplantes Laden ausrollen (Plan 4b)“: Regelbetrieb aus, Datenbank sichern, core und app
    # gemeinsam, Migration beim Start, soc_entity, optional charge_limit_sync, ACL-Zeile für
    # `text`, Regelbetrieb an, ein Abend „Geplant“ im Dry-Run.
    section = " ".join(_guide_section("Laderegelung").split())
    _, found, text = section.partition("### Geplantes Laden ausrollen (Plan 4b) ")
    assert found, "Der Unterabschnitt „Geplantes Laden ausrollen (Plan 4b)“ fehlt."
    for topic in (
        "nach der Abnahme der Phase 3",
        "Nichts geht dabei von selbst live",
        "topic write homeassistant/text/bindaems/#",
        "Regelbetrieb aus",
        "/backup/vor-plan-4b.sqlite3",
        "docker compose pull && docker compose up -d",
        "Migration `0012`",
        "`soc_entity`",
        "`homeassistant.entities`",
        "`charge_limit_sync: true`",
        "Regelbetrieb an",
        "im Dry-Run beobachten",
        "docs/verification/abnahme-phase-4.md",
    ):
        assert topic in text, topic
    bedienung = " ".join(_guide_section("4. Bedienung").split())
    assert "Rollback auf einen Stand vor Plan 4b" in bedienung
    assert "kommt mit den folgenden Tasks" not in Path("docs/betrieb.md").read_text("utf-8")


def test_planned_health_fields_in_the_guide_match_the_core() -> None:
    # „Health für die Bedienung“ nennt die Felder von „Geplant“ je Fahrzeug (Plan 4b,
    # Präzisierung 7) wie ``ChargeController.health``.
    from datetime import UTC, datetime

    from bindaems.core.control.charging import ChargeController, _planned_health
    from bindaems.core.control.planned import PlannedState
    from bindaems.core.safety.limits import HardLimits
    from bindaems.shared.config import load_config

    cfg = load_config(Path("deploy/config.example.yaml"))
    started = datetime(2026, 10, 12, tzinfo=UTC)
    health = ChargeController(cfg, HardLimits.from_config(cfg), started=started).health()[0]
    section = _guide_section("Laderegelung").partition("### Health für die Bedienung\n")[2]
    section = section.split("\n### ", 1)[0]
    for field in ("soc_pct", "soc_source", "charge_limit_pct", "planned"):
        assert field in health, field
        assert f"| `vehicles[].{field}` |" in section, field
    state = PlannedState("plan", 80.0, started, 10.0, started, 3000.0, 50.0, "measured")
    planned = _planned_health(state)
    assert planned is not None
    row = next(line for line in section.splitlines() if "`vehicles[].planned`" in line)
    for key in planned:
        assert f"`{key}`" in row, key
    for kind in ("plan", "surplus", "due", "reached"):
        assert f"`{kind}`" in row, kind
    for source in ("measured", "estimated", "assumed"):
        assert f"`{source}`" in section, source


def test_planned_mode_in_the_charging_section() -> None:
    # „Geplant“ in der Laderegelung: Zeile der Lademodi, Ladestand, späteste Startzeit mit dem
    # Zuschlag ohne zweiten Wirkungsgrad, Stopp am Ziel und die Dringlichkeit mit festem Bedarf.
    section = " ".join(_guide_section("Laderegelung").split())
    assert "| Geplant | `planned` |" in section
    assert "Es gibt **kein Ziel-SOC**" not in section
    _, found, text = section.partition("### Geplant ")
    assert found, "Der Unterabschnitt „Geplant“ fehlt in „Laderegelung“."
    text = text.split(" ### ", 1)[0]
    for topic in (
        "`target_soc_pct`",
        "`departure`",
        "höchstens 900 s alt",
        "60 min vor dem Anstecken",
        "ersten Zählerstand dieses Ansteckvorgangs",
        "rechnet dann mit 0 %",
        "`Abfahrt − Bedarf / volle Leistung · 1,1 − 15 min`",
        "nicht ein zweites Mal",
        "3 Prozentpunkte",
        "keine Akku-Unterstützung",
    ):
        assert topic in text, topic
    assert (
        "In „Geplant“ mit festem Bedarf (oben, „Geplant“) ist nur eine Senkung dringend" in section
    )


def test_planning_section_describes_the_cars() -> None:
    # „Autos im Plan“ und der Ersatzplan mit Autos (Plan 4b, Entscheidungen 6 bis 8); die
    # Befunde der Prüfung stehen mit dem Wortlaut der app im Abschnitt.
    section = " ".join(_guide_section("Planung").split())
    _, found, text = section.partition("### Autos im Plan ")
    assert found, "Der Unterabschnitt „Autos im Plan“ fehlt in „Planung“."
    for topic in (
        "Regel R2",
        "P25",
        "2000 ct",
        "200 ct",
        "Fehlmenge",
        "nie aus dem Akku",
        "Abfahrt hinter dem Horizont",
        "mit voller Leistung bis zur Abfahrt noch lädt",
    ):
        assert topic in text, topic
    for finding in ("Auto {Name} lädt außerhalb seiner Grenzen um HH:MM", "Auto {Name} lädt nach"):
        assert finding in section, finding
    assert "Der Ersatzplan rechnet nur den Akku" not in section
    assert "Dann die günstigsten Slots" in section
    source = Path("src/bindaems/app/planning/plausibility.py").read_text(encoding="utf-8")
    assert "Auto {car.name} lädt außerhalb seiner Grenzen um {at}" in source
    assert "Auto {car.name} lädt nach der Abfahrt" in source


def test_scenario_report_with_cars_in_the_guide_matches_the_tool() -> None:
    # Der Bericht „Szenario-Vergleich mit Victron allein“ (Plan 3b, mit Autos Plan 4b, Task 13):
    # Spalten und Gründe für „Nicht bestanden“ stehen mit dem Wortlaut des Werkzeugs im Abschnitt.
    from bindaems.tools.scenario import report

    section = " ".join(_guide_section("Planung").split())
    for column in (*report._COLUMNS, *report._CAR_COLUMNS):
        assert f"„{column}“" in section, column
    source = Path(report.__file__).read_text(encoding="utf-8")
    for reason, shown in (
        ("- Bereinigte Kosten über Victron allein", "„Bereinigte Kosten über Victron allein“"),
        ("- Regel R verletzt ({result.r1_violations})", "„Regel R verletzt ({n})“"),
        (
            "- Ziel-SOC verfehlt ({result.targets_missed} von {result.targets})",
            "„Ziel-SOC verfehlt ({a} von {b})“",
        ),
        ("- Regel R2 verletzt ({result.r2_violations})", "„Regel R2 verletzt ({n})“"),
    ):
        assert reason in source, reason
        assert shown in section, shown


def test_statistics_page_in_the_guide_uses_the_texts_of_the_ui() -> None:
    # „Statistik“ in Abschnitt 12 nennt die Texte der Seite wörtlich, wie `ui/src` sie ausgibt.
    section = " ".join(_guide_section("12. Web-UI").split())
    _, found, text = section.partition("### Statistik ")
    assert found, "Der Unterabschnitt „Statistik“ fehlt in „Web-UI“."
    text = text.split(" ### ", 1)[0]
    source = " ".join(
        " ".join(path.read_text(encoding="utf-8").split())
        for path in Path("ui/src").rglob("*")
        if path.suffix in {".svelte", ".ts"} and ".test." not in path.name
    )
    for phrase in (
        "Im Zeitraum wurde kein Auto geladen.",
        "Keine Ladesessions im Zeitraum.",
        "Noch kein Tag abgeglichen.",
        "Keine Zählerwerte für diesen Zeitraum.",
        "gegenüber Fixtarif ohne Anlage",
        "Akku → Autos",
        "PV (ohne Markierung)",
        "Abgleich mit den Zählern",
        "Ersparnis bei gleichem Netzbezug",
        "Geplantes Laden",
        "Im Zeitraum gab es keine geplante Ladung.",
        "machbaren Ladungen erreicht",
        "Vor der Abfahrt abgesteckt:",
        "Vor der Abfahrt geändert oder verlassen:",
    ):
        assert phrase in text, phrase
        assert phrase in source, phrase
    # die Grenze steht im UI als SESSIONS_LIMIT (gleich MAX_SESSIONS des Servers, test_routes.py)
    assert "Angezeigt werden die 200 neuesten Sessions." in text
    assert "Angezeigt werden die {SESSIONS_LIMIT} neuesten Sessions." in source
    assert "export const SESSIONS_LIMIT = 200;" in source
    assert "\n| Statistik | " in _guide_section("12. Web-UI")  # die Zeile der Seitenliste
    for topic in ("Session seit {HH:MM}", "Letzte Session {TT.MM.}"):
        assert topic in section, topic


def test_charging_page_in_the_guide_uses_the_planned_texts_of_the_ui() -> None:
    # „Laden“ in Abschnitt 12 nennt die Felder und Zeilen von „Geplant“ wie `ui/src` (Plan 4b).
    section = " ".join(_guide_section("12. Web-UI").split())
    _, found, text = section.partition("### Laden ")
    assert found, "Der Unterabschnitt „Laden“ fehlt in „Web-UI“."
    text = text.split(" ### ", 1)[0]
    source = " ".join(
        " ".join(path.read_text(encoding="utf-8").split())
        for path in Path("ui/src").rglob("*")
        if path.suffix in {".svelte", ".ts"} and ".test." not in path.name
    )
    assert "Min+PV, Sofort, Aus oder Geplant" in text
    for phrase in (
        "Ziel-SOC",
        "Abfahrt",
        "Übernehmen",
        "Kein Plan · lädt spätestens ab",
        "Ladestand geschätzt",
        "liegt unter dem Ziel.",
        "/api/control/vehicles/",
    ):
        assert phrase in text, phrase
        assert phrase in source, phrase
    for line in (
        "„Ziel erreicht ({soc} %).“",
        "„Fertig um {HH:MM} · Ziel {soc} % bis {HH:MM}“",
        "„Ziel {soc} % bis {HH:MM} nicht erreichbar, voraussichtlich {x} %.“",
        "„Kein Plan · lädt spätestens ab {HH:MM}“",
        "„Ladestand unbekannt, gerechnet ab 0 %.“",
    ):
        assert line in text, line
    zeitplan = section.partition("### Zeitplan ")[2].split(" ### ", 1)[0]
    assert "Diagramm „Autos“" in zeitplan


def test_operations_guide_describes_the_web_ui() -> None:
    text = Path("docs/betrieb.md").read_text(encoding="utf-8")
    for topic in ("## 12. Web-UI", "App installieren", "Sitzung", "Bestätigungscode"):
        assert topic in text


def test_operations_guide_describes_the_operation() -> None:
    # Die Bedienung in der app und in Home Assistant steht im Handbuch (Plan 2d).
    web = _guide_section("12. Web-UI")
    for topic in ("Laden", "Entscheidungen", "Warum?", "Bedienen"):
        assert topic in web
    ha = _guide_section("7. Home Assistant über MQTT")
    for topic in ("Lademodus", "Regelbetrieb", "Boost", "Zuordnung", "notify_service"):
        assert topic in ha


def test_statistics_sensors_in_the_guide_match_the_entities() -> None:
    # Die Tabelle in Abschnitt 7 nennt Namen, Einheiten und Attribute der Statistik-Sensoren
    # wie die Entitäten der app (Plan 4a, Task 10).
    from bindaems.app.ha.entities import build_entities
    from bindaems.shared.config import load_config

    cfg = load_config(Path("deploy/config.example.yaml"))
    specs = {spec.object_id: spec for spec in build_entities(cfg)}
    section = _guide_section("7. Home Assistant über MQTT")
    expected = {
        "savings_today": ("Ersparnis heute", "EUR", ("same_import_eur",)),
        "savings_month": ("Ersparnis Monat", "EUR", ()),
        "autarky_today": ("Autarkie heute", "%", ("month_pct",)),
        "tesla_charge_energy": (
            "Ladeenergie",
            "kWh",
            ("start", "end", "pv_kwh", "battery_kwh", "grid_kwh", "cost_eur", "month_kwh"),
        ),
    }
    for oid, (name, unit, attributes) in expected.items():
        assert (specs[oid].name, specs[oid].unit) == (name, unit), oid
        row = next((line for line in section.splitlines() if line.startswith(f"| {name} ")), "")
        assert f"| {unit} |" in row, oid
        for attribute in attributes:
            assert f"`{attribute}`" in row, (oid, attribute)
    flat = " ".join(section.split())
    assert "nicht im 5-s-Takt" in flat and "alle 15 Minuten" in flat


def test_warning_notifications_in_the_guide_match_the_app() -> None:
    # Die Tabelle „Benachrichtigungen“ in Abschnitt 7 nennt je Warnung Kennung und Text wie die
    # app (Plan 4b, Task 16).
    from bindaems.app.ha.notify import TARGET_SUFFIX, TARGET_TEXT, WARNING_PREFIX, WARNING_TEXTS

    section = _guide_section("7. Home Assistant über MQTT")
    rows = [line.strip() for line in section.splitlines() if line.strip().startswith("| ")]
    for oid, text in WARNING_TEXTS.items():
        row = next((line for line in rows if f"`{WARNING_PREFIX}{oid}`" in line), "")
        assert f"„{text.format(names='{Namen}')}“" in row, oid
    target = next(
        (line for line in rows if f"`{WARNING_PREFIX}<Fahrzeug>{TARGET_SUFFIX}`" in line), ""
    )
    sentence = TARGET_TEXT.format(name="{Name}", hhmm="{HH:MM}", soc="{soc}")
    assert f"„{sentence}“" in target
    assert "watchdog_tripped" not in WARNING_TEXTS


def test_operations_guide_describes_charging() -> None:
    text = Path("docs/betrieb.md").read_text(encoding="utf-8")
    for topic in ("## Laderegelung", "## Simulator", '"vehicles"', "-m bindaems.tools.sim"):
        assert topic in text


def _guide_section(heading: str) -> str:
    """Der Abschnitt ``## heading`` des Betriebshandbuchs bis zum nächsten ``## ``."""
    text = Path("docs/betrieb.md").read_text(encoding="utf-8")
    _, found, rest = text.partition(f"\n## {heading}\n")
    assert found, f"Der Abschnitt „{heading}“ fehlt im Betriebshandbuch."
    return rest.split("\n## ", 1)[0]


def test_json_examples_of_the_charging_section_are_valid() -> None:
    # Die Beispiele sind eine Absicht für PUT /v1/intent, ein Ausschnitt von GET /v1/health (die
    # Wallboxen) und ein Eintrag von GET /v1/decisions: Die Modelle des core müssen sie lesen
    # können, sonst führt das Handbuch in die Irre. Der Ausschnitt nennt genau die Felder und
    # Werte, die der core meldet.
    from datetime import UTC, datetime

    from bindaems.core.control.assignment import AssignmentKind
    from bindaems.core.control.charging import ChargeController
    from bindaems.core.control.clues import Clue
    from bindaems.core.safety.limits import HardLimits
    from bindaems.shared.config import load_config
    from bindaems.shared.control import Intent
    from bindaems.shared.decisions import decision_from_json

    cfg = load_config(Path("deploy/config.example.yaml"))
    started = datetime(2026, 10, 12, tzinfo=UTC)
    fields = set(
        ChargeController(cfg, HardLimits.from_config(cfg), started=started).wallbox_health()[0]
    )
    examples = re.findall(r"```json\n(.*?)\n```", _guide_section("Laderegelung"), re.DOTALL)
    data = [json.loads(example) for example in examples]
    assert [("version" in item) for item in data].count(True) >= 2
    names = {key: vehicle.name for key, vehicle in cfg.vehicles.items()} | {"guest": "Gast"}
    found: set[str] = set()
    for item in data:
        if "version" in item:
            found.add("intent")
            Intent.model_validate(item)
        elif "wallboxes" in item:
            found.add("health")
            assert set(item) == {"wallboxes"}
            assert [wallbox["id"] for wallbox in item["wallboxes"]] == list(cfg.wallboxes)
            for wallbox in item["wallboxes"]:
                assert set(wallbox) == fields
                assert wallbox["assignment"] in {kind.value for kind in AssignmentKind} | {None}
                occupant = wallbox["occupant"]
                assert occupant in {*cfg.vehicles, "guest", None}
                assert wallbox["name"] == names.get(occupant)  # ohne Insassen ist es null
                if occupant is None and wallbox["assignment"] is not None:
                    assert wallbox["assignment"] == "unclear"  # im Vorgang steht sonst jemand
                for indicator in wallbox["indicators"]:
                    assert set(indicator) == {"vehicle", "clue", "supports"}
                    assert indicator["vehicle"] in cfg.vehicles
                    assert indicator["clue"] in {clue.value for clue in Clue}
                    assert isinstance(indicator["supports"], bool)
        else:
            found.add("decision")
            decision_from_json(item)
    assert found == {"intent", "health", "decision"}


# die Gründe der Laderegelung (Plan 2c-1, 2c-2b und 2d, „Texte“)
CHARGING_REASONS = {
    "mode_pv",
    "mode_min_pv",
    "mode_now",
    "mode_off",
    "mode_planned",
    "boost",
    "pv_surplus",
    "phase_limit",
    "import_limit",
    "reserve_guard",
    "overload",
    "wake",
    "not_taken_retry",
    "assignment_unclear",
    "assignment_detected",
    "resume_after_outage",
    "self_start",
    "plan_power",
    "latest_start",
    "target_reached",
    "charge_limit",
}


def _documented_reasons(section: str, known: set[str]) -> dict[str, str]:
    """Code → Text aus den Zeilen ``| `code` | Text | Bedeutung |`` der Tabelle des Abschnitts.

    Es zählen nur Zeilen, deren Codes alle zu ``known`` gehören. Eine Zeile mit mehreren Codes hat
    ebenso viele Texte, getrennt mit „, “.
    """
    documented: dict[str, str] = {}
    for line in _guide_section(section).splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        codes = re.findall(r"`(\w+)`", cells[0]) if len(cells) >= 3 else []
        if line.startswith("| `") and codes and set(codes) <= known:
            labels = [label.strip() for label in cells[1].split(", ")]
            assert len(labels) == len(codes), line
            documented.update(zip(codes, labels, strict=True))
    return documented


def test_reason_codes_in_the_guide_match_the_decision_log() -> None:
    # Die Tabelle der Gründe nennt jeden Code mit dem Text, den das Entscheidungslog zeigt.
    from bindaems.shared.control import CommandKind
    from bindaems.shared.decisions import DecisionResult, decision_text

    documented = _documented_reasons("Laderegelung", CHARGING_REASONS)
    assert set(documented) == CHARGING_REASONS
    for code, label in documented.items():
        shown = decision_text(
            "A", CommandKind.CURRENT, 8.0, DecisionResult.DRY_RUN, reasons=(code,)
        )
        assert f"({label})" in shown, code


def test_operations_guide_describes_the_assignment() -> None:
    section = _guide_section("Laderegelung")
    for topic in (
        "### Zuordnung Auto und Wallbox",
        "home_entity",
        '"wallboxes"',
        "Gast, angenommen",
        "guest",
        "detected",
    ):
        assert topic in section, topic


def test_assignment_kinds_and_clues_in_the_guide_match_the_code() -> None:
    # Health nennt die Art der Zuordnung und die Indizien mit diesen Werten: Das Handbuch erklärt
    # jeden.
    from bindaems.core.control.assignment import AssignmentKind
    from bindaems.core.control.clues import Clue

    section = _guide_section("Laderegelung")
    for name in (*(kind.value for kind in AssignmentKind), *(clue.value for clue in Clue)):
        assert f"`{name}`" in section, name


def test_the_detected_reason_example_matches_the_decision_log() -> None:
    # Der Beispielsatz zu `assignment_detected` ist der, den das Entscheidungslog zeigt.
    from bindaems.shared.control import CommandKind
    from bindaems.shared.decisions import DecisionResult, decision_text

    shown = decision_text(
        "Tesla Model 3",
        CommandKind.CURRENT,
        16.0,
        DecisionResult.OK,
        reasons=("mode_now", "assignment_detected"),
    )
    assert (
        shown == "Tesla Model 3 (Sofort, Zuordnung erkannt): Strom 16 A – gesendet und bestätigt."
    )
    assert shown in _guide_section("Laderegelung")


# die Gründe des Akku-Sollwerts und des 15-min-Wächters (Plan 2c-2a, „Texte“), dazu die der
# Planausführung (Plan 3c, „Texte“)
BATTERY_REASONS = {"battery_comp", "battery_support", "peak_guard", "plan_hold", "plan_grid_charge"}

# die Einstellungen unter ``control`` für Akku-Sollwert, 15-min-Wächter (Plan 2c-2a, Design 3.2)
# und die Tesla-Punkte vor der Live-Schaltung (Plan 2d)
BATTERY_SETTINGS = {
    "support_soc_pct",
    "support_hysteresis_pct",
    "support_max_w",
    "support_max_s",
    "setpoint_deadband_w",
    "override_refresh_s",
    "peak_target_kw",
    "peak_margin_kw",
    "reserve_guard_pct",
    "tesla_reserve_commands",
    "tesla_against_s",
    "tesla_against_retry_s",
}


def test_operations_guide_describes_battery_and_peak() -> None:
    section = _guide_section("Akku und Spitzen")
    for topic in (
        '"battery"',
        '"peak"',
        "control.exception",
        "peak.exceeded",
        "victron.live_allowed",
        "peak_target_kw",
    ):
        assert topic in section, topic


def test_battery_reasons_in_the_guide_match_the_decision_log() -> None:
    # Wie bei der Laderegelung: Die Tabelle nennt jeden Grund mit dem Text, den das Log zeigt.
    from bindaems.shared.control import CommandKind
    from bindaems.shared.decisions import DecisionResult, decision_text

    documented = _documented_reasons("Akku und Spitzen", BATTERY_REASONS)
    assert set(documented) == BATTERY_REASONS
    for code, label in documented.items():
        shown = decision_text(
            "Victron (ESS)",
            CommandKind.GRID_SETPOINT,
            2340.0,
            DecisionResult.DRY_RUN,
            reasons=(code,),
        )
        expected = f"Victron (ESS) ({label}): Netz-Sollwert 2340 W – Dry-Run, nicht gesendet."
        assert shown == expected, code


def test_json_examples_of_the_battery_section_are_valid() -> None:
    # Die Beispiele sind eine Absicht für PUT /v1/intent, ein Ausschnitt von GET /v1/health und
    # Einträge von GET /v1/decisions. Der Ausschnitt nennt genau die Werte, die der core meldet,
    # und die Gründe eines Eintrags stehen mit ihrem Text aus der Tabelle in seinem Satz.
    from bindaems.core.control.battery import BatteryController
    from bindaems.core.control.peak_guard import PeakGuard
    from bindaems.shared.config import load_config
    from bindaems.shared.control import Intent
    from bindaems.shared.decisions import decision_from_json

    cfg = load_config(Path("deploy/config.example.yaml"))
    reasons = _documented_reasons("Akku und Spitzen", BATTERY_REASONS)
    # Auch ein Beispiel in einem Listenpunkt zählt: Die schließende Zeile hat dieselbe Einrückung.
    blocks = re.findall(
        r"^( *)```json\n(.*?)\n\1```$",
        _guide_section("Akku und Spitzen"),
        re.DOTALL | re.MULTILINE,
    )
    found: set[str] = set()
    for _, example in blocks:
        item = json.loads(example)
        if "version" in item:
            found.add("intent")
            Intent.model_validate(item)
        elif "battery" in item:
            found.add("health")
            assert set(item) == {"battery", "peak"}
            assert set(item["battery"]) == set(BatteryController(cfg).health())
            ceiling_w = cfg.victron.max_grid_charge_setpoint_w
            peak = PeakGuard(cfg.control, cfg.battery.reserve_soc_pct, ceiling_w).health()
            assert set(item["peak"]) == set(peak)
            assert item["battery"]["state"] in {"ok", "unknown", "locked", "idle"}
            assert item["battery"]["support"] in {"on", "soc", "time", "mode", "off", "plan", None}
            assert item["battery"]["support_source"] in {"config", "intent", "plan", None}
            assert item["peak"]["state"] in {"off", "gap", "active", "exceeded"}
        else:
            found.add("decision")
            decision = decision_from_json(item)
            labels = ", ".join(reasons[code] for code in decision.codes if code in reasons)
            assert f"({labels}): " in decision.text
    assert found == {"intent", "health", "decision"}


def test_battery_settings_in_the_guide_match_the_configuration() -> None:
    # Die Tabelle nennt jede Einstellung mit dem Standardwert aus ControlConfig; „keiner“ heißt
    # ohne Wert.
    from bindaems.shared.config import ControlConfig

    defaults = ControlConfig()
    documented: dict[str, str] = {}
    for line in _guide_section("Akku und Spitzen").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        name = re.fullmatch(r"`control\.(\w+)`", cells[0]) if len(cells) >= 3 else None
        if line.startswith("| `control.") and name:
            documented[name[1]] = cells[1]
    assert set(documented) == BATTERY_SETTINGS
    for key, shown in documented.items():
        value = None if shown == "keiner" else float(shown.replace(",", "."))
        assert value == getattr(defaults, key), key


def test_simulator_section_matches_the_tool(capsys: pytest.CaptureFixture[str]) -> None:
    # Die Szenarien und Optionen im Handbuch gibt es, und das Werkzeug kennt keine ungenannten.
    from bindaems.tools.sim.__main__ import main
    from bindaems.tools.sim.scenarios import SCENARIOS

    with pytest.raises(SystemExit):
        main(["--help"])
    known = set(re.findall(r"--[a-z][a-z-]*", capsys.readouterr().out))
    section = _guide_section("Simulator")
    documented = set(re.findall(r"--[a-z][a-z-]*", section)) - {"--rm", "--entrypoint"}
    assert documented == known - {"--help"}
    scenarios = re.findall(r"^\| `([a-z0-9-]+)` \|", section, re.MULTILINE)
    assert scenarios == list(SCENARIOS)
    assert len(SCENARIOS) == 33 and "achtundzwanzig" not in section  # die Zahl im Text zieht mit
    assert section.count("dreiunddreißig") == 3


def test_operations_guide_describes_operating_states_and_the_watchdog() -> None:
    text = Path("docs/betrieb.md").read_text(encoding="utf-8")
    for topic in (
        "## 13. Betriebszustände und Freigaben",
        "## 14. Watchdog am Cerbo",
        "### Reihenfolge der Inbetriebnahme",
        "### Wartung",
        "-m bindaems.tools.watchdog_config --config /config/config.yaml > config.ini.new",
        "-m bindaems.tools.watchdog_probe --config /config/config.yaml",
        "./install.sh root@cerbo.lan /opt/bindaems/config.ini",
        "./install.sh --uninstall root@cerbo.lan",
        "Watchdog ausgelöst",
    ):
        assert topic in text


@pytest.mark.parametrize("doc", ["docs/betrieb.md", "cerbo-watchdog/README.md"])
def test_watchdog_config_is_only_replaced_when_generated(doc: str) -> None:
    # Schlägt die Erzeugung fehl, leerte „> config.ini“ die bisherige Datei.
    text = Path(doc).read_text(encoding="utf-8")
    assert "> config.ini.new \\\n  && mv config.ini.new config.ini\n" in text
    assert not re.search(r"> config\.ini$", text, re.M)


TOOL_COMMAND = re.compile(r"docker compose run [^`]*?-m (bindaems\.tools\.\w+)", re.DOTALL)


def test_documented_tool_commands_start_python_directly() -> None:
    # Der ENTRYPOINT des core-Images ist ``python -m bindaems.core``: Ein Werkzeug braucht
    # ``--entrypoint python``, und das Modul muss es geben. Das Betriebshandbuch nennt alle.
    handbook = Path("docs/betrieb.md").read_text(encoding="utf-8")
    assert {
        "bindaems.tools.verify_part1",
        "bindaems.tools.verify_part2",
        "bindaems.tools.watchdog_config",
        "bindaems.tools.watchdog_probe",
        "bindaems.tools.export_days",
        "bindaems.tools.scenario_days",
    } <= set(TOOL_COMMAND.findall(handbook))
    for doc in ("docs/betrieb.md", "docs/verification/README.md", "cerbo-watchdog/README.md"):
        for command in TOOL_COMMAND.finditer(Path(doc).read_text(encoding="utf-8")):
            assert importlib.util.find_spec(command[1]) is not None, doc
            assert "--entrypoint python" in command[0], doc


def test_part2_options_in_the_docs_exist(capsys: pytest.CaptureFixture[str]) -> None:
    # Die Abschnitte zu Teil 2 nennen nur Optionen, die verify_part2 kennt (und Docker-Flags).
    from bindaems.tools.verify_part2 import main

    with pytest.raises(SystemExit):
        main(["--help"])
    known = set(re.findall(r"--[a-z][a-z-]*", capsys.readouterr().out)) | {"--rm", "--entrypoint"}
    sections = {
        "docs/betrieb.md": r"### Prüfprotokoll Teil 2\n(.*?)\n### ",
        "docs/verification/README.md": r"## Teil 2 – schreibend \(17\.2\)\n(.*?)\n## ",
    }
    for doc, pattern in sections.items():
        found = re.search(pattern, Path(doc).read_text(encoding="utf-8"), re.DOTALL)
        assert found, doc
        documented = set(re.findall(r"--[a-z][a-z-]*", found[1]))
        assert {"--punkt", "--out", "--yes", "--schlafend"} <= documented, doc
        assert documented <= known, f"{doc}: {sorted(documented - known)}"


def test_relative_links_in_the_docs_exist() -> None:
    link = re.compile(r"\]\(([^)\s]+)\)")
    docs = [Path("README.md"), Path("cerbo-watchdog/README.md"), *Path("docs").rglob("*.md")]
    missing = []
    for doc in docs:
        for target in link.findall(doc.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "mailto:", "#")):
                continue
            if not (doc.parent / target.split("#")[0]).exists():
                missing.append(f"{doc}: {target}")
    assert missing == []


def test_operations_guide_describes_planning() -> None:
    # Der Abschnitt „Planung“ (Plan 3b) nennt Seite, Schalter, Ersatzplan, Taste und den Vergleich.
    section = _guide_section("Planung")
    for topic in (
        "Zeitplan",
        "Netzladen erlaubt",
        "Ersatzplan",
        "Neu planen",
        "Victron allein",
        "### An der Anlage prüfen",
        "export_days",
        "scenario_days",
        "/data",
    ):
        assert topic in section, topic


def test_plan_reasons_in_the_guide_match_the_code() -> None:
    # Die Tabelle der Grundcodes nennt jeden ReasonCode mit dem Satz, den das UI zeigt; ein
    # Code, den es nicht gibt, steht nirgends im Abschnitt.
    from typing import get_args

    from bindaems.app.planning.explain import sentence
    from bindaems.shared.plan import Reason, ReasonCode

    codes = set(get_args(ReasonCode))
    examples: dict[str, dict[str, float | str]] = {
        "NETZLADEN_GUENSTIG": {"kw": 4.0, "ct": 12.3, "um": "18:00", "ct_spaeter": 31.5},
        "HALTEN_FUER_SPITZE": {"soc": 64.0, "von": "18:00", "bis": "20:00", "ct": 38.0},
        "BUDGET_SPITZE": {"soc": 55.0, "kw": 6.5},
        "HALTEN_RESTWERT": {"soc": 40.0, "um": "10:00"},
        "PV_LADEN": {"kw": 3.2},
        "PV_UEBERSCHUSS_ERWARTET": {"um": "09:30"},
        "REGEL_R_BLOCKIERT_NETZLADEN": {"um": "17:00", "kwh": 8.4},
        "NETZLADEN_AUS": {"ct": 10.0, "um": "18:00", "ct_spaeter": 30.0},
        "EIGENVERBRAUCH": {},
        "UNTERSTUETZUNG_RESERVIERT": {"soc": 70.0, "um": "2026-10-16T16:00:00+00:00"},
        "GESCHAETZT": {},
        "ERSATZPLAN": {},
        "ZIEL_SOC_ABFAHRT": {"name": "Tesla Model 3", "kw": 7.0, "soc": 80.0, "um": "07:00"},
        "AUTO_WARTET_AUF_PV": {"name": "e-Golf"},
        "ZIEL_SOC_UNERREICHBAR": {"name": "e-Golf", "soc": 62.0, "um": "06:30"},
    }
    assert set(examples) == codes
    section = _guide_section("Planung")
    rows: dict[str, str] = {}
    for line in section.splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        found = re.fullmatch(r"`([A-Z_]+)`", cells[0]) if line.startswith("| `") else None
        if found and len(cells) >= 3:
            rows[found[1]] = cells[1]
    assert set(rows) == codes
    # Hauptcodes in der Reihenfolge der Prüfung von ``explain._main_reason``, dann die Zusätze
    assert list(rows) == [
        "NETZLADEN_GUENSTIG",
        "HALTEN_FUER_SPITZE",
        "BUDGET_SPITZE",
        "HALTEN_RESTWERT",
        "PV_LADEN",
        "REGEL_R_BLOCKIERT_NETZLADEN",
        "NETZLADEN_AUS",
        "PV_UEBERSCHUSS_ERWARTET",
        "EIGENVERBRAUCH",
        "ZIEL_SOC_ABFAHRT",
        "AUTO_WARTET_AUF_PV",
        "ZIEL_SOC_UNERREICHBAR",
        "UNTERSTUETZUNG_RESERVIERT",
        "GESCHAETZT",
        "ERSATZPLAN",
    ]
    for code, shown in rows.items():
        expected = sentence(Reason(code=code, params=examples[code]))  # type: ignore[arg-type]
        assert shown == expected, code
    named = set(re.findall(r"`([A-Z]+_[A-Z_]+)`", section))
    assert named <= codes, sorted(named - codes)


def _ts_labels(source: str, name: str) -> dict[str, str]:
    """Einträge ``schlüssel: 'Text'`` der Konstante ``name`` aus einer TypeScript-Datei."""
    found = re.search(rf"export const {name}\b[^=]*=\s*\{{(.*?)\}}", source, re.S)
    assert found, name
    return dict(re.findall(r"(\w+): '([^']*)'", found[1]))


def test_plan_triggers_and_sources_in_the_guide_match_the_ui() -> None:
    # Auslöser und Quellen stehen mit dem Wortlaut aus ``ui/src/lib/plan/model.ts`` im Abschnitt.
    from typing import get_args

    from bindaems.shared.plan import PlanSource, PlanTrigger

    model = Path("ui/src/lib/plan/model.ts").read_text(encoding="utf-8")
    triggers = _ts_labels(model, "TRIGGER_LABELS")
    sources = _ts_labels(model, "SOURCE_LABELS")
    assert set(triggers) == set(get_args(PlanTrigger))
    assert set(sources) == set(get_args(PlanSource))
    section = _guide_section("Planung")
    for trigger, label in triggers.items():
        assert f"| `{trigger}` | {label} |" in section, trigger
    for label in sources.values():
        assert f"„{label}“" in section, label


# Plattformen der Tabellen im Handbuch
HA_PLATFORMS = {"sensor", "binary_sensor", "switch", "number", "text", "button"}


def _documented_entities(section: str) -> dict[str, tuple[str, str]]:
    """Objekt-ID → (Name, Plattform) der HA-Tabelle des Abschnitts."""
    documented: dict[str, tuple[str, str]] = {}
    for line in _guide_section(section).splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        found = re.fullmatch(r"`([a-z_]+)`", cells[0]) if line.startswith("| `") else None
        if found and len(cells) >= 4 and cells[2] in HA_PLATFORMS:
            documented[found[1]] = (cells[1], cells[2])
    return documented


def _check_entities_exist(documented: dict[str, tuple[str, str]], expected: set[str]) -> None:
    from bindaems.app.ha.entities import build_entities
    from bindaems.shared.config import load_config

    cfg = load_config(Path("deploy/config.example.yaml"))
    specs = {spec.object_id: spec for spec in build_entities(cfg)}
    assert set(documented) == expected
    for oid, (name, component) in documented.items():
        assert (specs[oid].name, specs[oid].component) == (name, component), oid


def test_planning_entities_in_the_guide_exist() -> None:
    # Die Tabelle „Home Assistant“ nennt Objekt-ID, Namen und Plattform wie die Entitäten der app.
    _check_entities_exist(
        _documented_entities("Planung"),
        {
            "battery_plan_now",
            "battery_plan_next",
            "planned_soc",
            "budget",
            "grid_charge",
            "replan",
        },
    )


def test_planned_charging_entities_in_the_guide_exist() -> None:
    # „Geplantes Laden“ in Abschnitt 7: Objekt-ID, Name und Plattform wie die Entitäten der app;
    # die Bedienelemente stehen auch in der Tabelle „Bedienelemente und Befehle“.
    _check_entities_exist(
        _documented_entities("7. Home Assistant über MQTT"),
        {
            "tesla_target_soc",
            "tesla_departure",
            "tesla_done_by",
            "tesla_target_reachable",
            "problem_target_unreachable",
        },
    )
    section = _guide_section("7. Home Assistant über MQTT")
    assert "| Ziel-SOC (je Fahrzeug) | Zahl |" in section
    assert "| Abfahrtszeit (je Fahrzeug) | Text |" in section
    assert "topic write homeassistant/text/bindaems/#" in section
    assert "Sofort, Aus oder Geplant" in section


def test_support_entities_in_the_guide_exist() -> None:
    # „Akku-Unterstützung bedienen“: der Schalter und die Zahl (Plattform `number`) mit Namen wie
    # in der app; die Bedienelemente der Tabelle in Abschnitt 7 nennen dieselben Namen.
    _check_entities_exist(
        _documented_entities("Akku und Spitzen"), {"battery_support", "support_soc"}
    )
    controls = _guide_section("7. Home Assistant über MQTT")
    assert "| Akku-Unterstützung erlaubt | Schalter |" in controls
    assert "| Unterstützungsschwelle | Zahl |" in controls
    assert "`control.support`" in Path("docs/betrieb.md").read_text(encoding="utf-8")
    assert "PUT /api/control/support" in _guide_section("Akku und Spitzen")


def test_planning_settings_in_the_guide_match_the_configuration() -> None:
    # Die Tabelle der Einstellungen „Planung“ nennt jedes Feld mit dem Standardwert.
    from bindaems.shared.settings import PlanningSettings

    defaults = PlanningSettings()
    documented: dict[str, str] = {}
    for line in _guide_section("Planung").splitlines():
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        found = re.fullmatch(r"`planning\.(\w+)`", cells[0]) if line.startswith("| `p") else None
        if found and len(cells) >= 4:
            documented[found[1]] = cells[2]
    assert set(documented) == set(PlanningSettings.model_fields)
    for key, shown in documented.items():
        value = getattr(defaults, key)
        if isinstance(value, bool):
            assert shown == ("ein" if value else "aus"), key
        else:
            assert float(shown.replace(",", ".")) == value, key


def test_plan_execution_texts_in_the_guide_and_the_ui() -> None:
    # Jeder Satz zum Stand der Ausführung steht im Handbuch; die Seite zeigt den Satz des Servers
    # und nicht mehr die Zeile „Vorschau“; das HA-Attribut `execution` ersetzt `preview: true`.
    section = _guide_section("Planung")
    for state, text in EXECUTION_TEXTS.items():
        assert text.replace("{mode}", "{Betriebszustand}") in section, state
        assert f"| `{state}` |" in section, state
    assert "| `none` |" in section
    header = Path("ui/src/lib/plan/PlanHeader.svelte").read_text(encoding="utf-8")
    assert "Vorschau" not in header
    assert "preview" not in section and "Vorschau" not in section
    assert "Attribut `execution`" in section


def test_charging_sessions_endpoint_in_the_guide() -> None:
    # „Ladesessions und Kosten je Auto“: der Endpunkt mit Parametern, Grenzen und Kostenregel.
    section = _guide_section("8. Tarif und Abrechnung")
    assert "GET /api/charging/sessions?from=JJJJ-MM-TT&to=JJJJ-MM-TT" in section
    for text in ("höchstens 400 Tage", "höchstens 200 Einträge", "`guest`", "`unknown`"):
        assert text in section, text
    for field in ("pv_kwh", "battery_kwh", "grid_kwh", "cost_eur", "wallbox_name", "vehicle_name"):
        assert f"`{field}`" in section, field
    assert "PV-Strom ins Auto kostet 0 ct" in section


def test_ledger_checks_endpoint_in_the_guide() -> None:
    # „Abgleich mit den Zählern“: Zeitpunkt, Endpunkt, Markierungsregel und die Gründe.
    section = " ".join(_guide_section("8. Tarif und Abrechnung").split())  # ohne Zeilenumbrüche
    assert "GET /api/ledger/checks?from=JJJJ-MM-TT&to=JJJJ-MM-TT" in section
    assert "02:45 Uhr" in section and "letzten 7 vollständigen Tage" in section
    assert "beim Start die der letzten 35" in section
    for text in ("höchstens 400 Tage", "mehr als 1 % und mehr als 0,1 kWh", "`pv` wird"):
        assert text in section, text
    for field in ("day", "signal", "ledger_kwh", "counter_kwh", "deviation_pct", "flagged", "note"):
        assert f"`{field}`" in section, field
    for note in ("Zählerstand fehlt", "Zähler zurückgesetzt", "Zähler zeigt 0 kWh"):
        assert note in section, note


def test_stats_endpoint_in_the_guide() -> None:
    # „Statistik je Zeitraum“: Endpunkt, Antwortfelder, Kennzahlen, Gebühren und Leistungspreis.
    section = " ".join(_guide_section("8. Tarif und Abrechnung").split())  # ohne Zeilenumbrüche
    assert "GET /api/stats?period=day|week|month|year" in section
    assert "ein anderes `period` gibt 422" in section
    for field in (
        "complete",
        "coverage",
        "estimated_slots",
        "energy",
        "money",
        "ratios",
        "buckets",
        "vehicles",
        "battery_to_vehicles_kwh",
        "planned",
        "consumers",
        "month_peaks",
        "checks",
        "fees_eur",
        "power_eur",
        "total_eur",
    ):
        assert f"`{field}`" in section, field
    for text in (
        "Montag bis Sonntag",
        "nicht als Mittel der Tageskennzahlen",
        "23, 24 oder 25 Einträge",
        "Monatsgebühr ÷ Tage des Monats",
        "nur bei „Monat“ und „Jahr“",
        "`null`, wenn `net_eur` unbekannt ist",
    ):
        assert text in section, text
    # Geplantes Laden (Plan 4b, Entscheidung 15): Zähler, Tabelle, Migration und Abnahme
    for text in (
        "`reached`",
        "`feasible`",
        "`missed`",
        "`infeasible`",
        "`early`",
        "`unknown`",
        "`changed`",
        "`planned_charge`",
        "Migration `0012`",
        "Ziel − 1 Prozentpunkt",
        "mindestens 95 %",
        "sie löscht keine Zeile",
        "zählt nirgends",
        "Sofort und Boost lassen die Ladung offen",
        "PV, Min+PV oder Aus",
        "älter als 15 min",
        "eine erreichte bleibt machbar",
        "läuft die geplante Ladung darin weiter",
    ):
        assert text in section, text


def test_grafana_section_in_the_guide_matches_the_dashboard() -> None:
    # „Grafana“ (Unterabschnitt von „InfluxDB einrichten“): Datei, Import, Datenquelle und die
    # Zeilen des Dashboards stehen wie in `deploy/grafana/bindaems.json`.
    section = " ".join(_guide_section("3. InfluxDB einrichten").split())
    _, found, text = section.partition("### Grafana ")
    assert found, "Der Unterabschnitt „Grafana“ fehlt in „InfluxDB einrichten“."
    dashboard = json.loads(Path("deploy/grafana/bindaems.json").read_text(encoding="utf-8"))
    assert "`deploy/grafana/bindaems.json`" in text
    assert "Dashboards → New → Import" in text
    assert "InfluxQL" in text and "`bindaems`" in text
    (item,) = dashboard["__inputs"]
    assert f"`{item['name']}`" in text
    rows = [panel["title"] for panel in dashboard["panels"] if panel["type"] == "row"]
    assert rows
    for row in rows:
        assert f"„{row}“" in text, row
    for topic in ("`raw`", "`long`", "90 Tage", "Entscheidungen", "Europe/Vienna"):
        assert topic in text, topic
    assert "keine Zugangsdaten" in text
    for topic in ("PV gesamt", "Haus (Verbrauch)", "`pv_total`", "`house_load`", "„Einzellast“"):
        assert topic in text, topic

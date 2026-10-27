import importlib.util
import json
import re
from pathlib import Path
from typing import get_args

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
        "where outcome = 'missed' and feasible = 1",  # dieselben Zeilen wie `missed`
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
        "im gemeinsamen Fenster nach dem 25.10.2026",
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
        "der gültige Slot nennt das Auto nicht",
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
        "jedes Auto des Plans in jedem Slot vor seiner Abfahrt, auch mit 0 W",
    ):
        assert topic in text, topic
    for finding in (
        "Auto {Name} lädt außerhalb seiner Grenzen um HH:MM",
        "Auto {Name} lädt nach",
        "Auto {Name} lädt vor der erwarteten Ankunft um HH:MM",  # Plan 5b
    ):
        assert finding in section, finding
    assert "Der Ersatzplan rechnet nur den Akku" not in section
    assert "Dann die günstigsten Slots" in section
    source = Path("src/bindaems/app/planning/plausibility.py").read_text(encoding="utf-8")
    assert "Auto {car.name} lädt außerhalb seiner Grenzen um {at}" in source
    assert "Auto {car.name} lädt nach der Abfahrt" in source
    assert "Auto {car.name} lädt vor der erwarteten Ankunft um {at}" in source


def test_rule_r_finding_in_the_guide_matches_the_app() -> None:
    # Befund „Regel R verletzt“ (seit Plan 3d: Einspeisung, bevor der Akku leer ist) mit dem
    # Wortlaut der app (``check_plan``, geprüft in test_plausibility.py) im Abschnitt „Planung“.
    section = " ".join(_guide_section("Planung").split())
    shown = "„Regel R verletzt: Netzladen um HH:MM, danach {kWh} kWh Einspeisung, bevor der Akku "
    assert shown + "leer ist“" in section


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
        "Weitere 50 anzeigen",
        "CSV herunterladen",
        "Laden je Auto",
        "Den Verlauf zeigen Woche, Monat und Jahr.",
    ):
        assert phrase in text, phrase
        assert phrase in source, phrase
    # die Grenze steht im UI als SESSIONS_LIMIT (gleich MAX_SESSIONS des Servers, test_routes.py)
    assert "Angezeigt werden die 2000 neuesten Sessions." in text
    assert "Angezeigt werden die {SESSIONS_LIMIT} neuesten Sessions." in source
    assert "export const SESSIONS_LIMIT = 2000;" in source
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
    # nach einer Bedienung holt die Seite Health und Plan nach 2 s (SYSTEM_REFRESH_MS) neu
    assert "nach jeder Bedienung (auch „Übernehmen“) nach 2 s zusammen mit Health" in text
    assert "const SYSTEM_REFRESH_MS = 2000;" in source and "void plan.refresh();" in source
    # passt der Plan nicht zu Ziel und Abfahrt in Health, fragt die Seite ihn alle 10 s neu
    assert "sobald die app neu geplant hat" not in text
    assert "fragt die Seite den Plan alle 10 s neu, bis er passt" in text
    assert "const PLAN_RECHECK_MS = 10_000;" in source
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
    # „Mitregelndes System“ schweigt schon, solange der Fehler besteht (kein Doppel-Push)
    assert "auch in den 30 s, bevor der Alarm selbst gemeldet wird" in " ".join(section.split())


def test_spec_names_the_notification_module_and_the_target_id() -> None:
    # Spec 12, Stand Plan 4b: das Modul der Benachrichtigungen und die Kennung je Fahrzeug wie in
    # der app (``WARNING_PREFIX`` + Objekt-ID + ``TARGET_SUFFIX``)
    from bindaems.app.ha.notify import TARGET_SUFFIX, WARNING_PREFIX

    spec = Path("docs/superpowers/specs/2026-10-08-bindaems-design.md").read_text("utf-8")
    assert "(`app/ha/entities.py`, `notify.py`)" in spec
    assert Path("src/bindaems/app/ha/notify.py").exists()
    assert "notifications.py" not in spec
    assert f"`{WARNING_PREFIX}<Fahrzeug>{TARGET_SUFFIX}`" in spec


def test_guide_describes_the_load_model() -> None:
    # Plan 5a, Task 14: Ablauf, Mindestdaten, Aktivierung, Rückfall, Sensorauswahl, Seite und
    # Ausrollen; die Sätze der Aktivierung stehen so da, wie die app sie schreibt.
    from bindaems.app.learning.activation import Activation, Reason

    section = " ".join(_guide_section("Lastmodell (Phase 5a)").split())
    for topic in (
        "03:15 Uhr",
        "28 Tage",
        "80 von 96",
        "**mindestens 10 %**",
        "Schattenbetrieb",
        "höchstens 8",
        "`/data/models/load/`",
        "Eine Sicherung ist nicht nötig",
        "`libgomp1`",
        "docker compose pull ems-app && docker compose up -d ems-app",
        "`0013`",
        "/backup/vor-plan-5a.sqlite3",
        "Jetzt neu trainieren",
        "docs/verification/abnahme-phase-5a.md",
        "Zu wenig Daten: {n} von 28 Tagen.",
        "Noch kein Backtest (braucht mindestens 28 Tage Daten).",
    ):
        assert topic in section, topic

    def sentence(reason: Reason, improvement: float | None) -> str:
        active = reason == "better"
        return Activation(active, "shadow", 7, 100.0, 120.0, improvement, reason).text()

    # die Sätze der Tabelle mit {x} und {n} als Platzhalter; die festen Sätze stehen wörtlich da
    assert sentence("better", 15.0).replace("15", "{x}") in section
    assert sentence("not_better", 4.0).replace("4", "{x}", 1) in section
    assert sentence("not_better", -6.0).replace("6", "{x}") in section
    assert sentence("too_few_days", None).replace("(7 ", "({n} ") in section
    assert sentence("disabled", None) in section
    assert sentence("no_model", None) in section


def test_acceptance_checklist_of_phase_5a_covers_the_load_model() -> None:
    # Spec 18, Phase 5, Teil Lastmodell; Plan 5a, Entscheidung 16.
    text = " ".join(Path("docs/verification/abnahme-phase-5a.md").read_text("utf-8").split())
    for criterion in (
        "28 Tage",
        "mindestens 10 %",
        "Last Profil",
        "Last Modell",
        "besser als Profil",
        "Karte „Lastprognose“",
        "GET /api/learning/load",
        "model_registry",
        "load-model",
        "Verfahren: Profil",
        "Jetzt neu trainieren",
        "Unterschrift",
    ):
        assert criterion in text, criterion
    assert "abnahme-phase-5a.md" in Path("docs/verification/README.md").read_text("utf-8")


def test_docs_name_driving_endpoints_entities_and_component() -> None:
    # Plan 5b, Task 14: Betriebshandbuch, Abnahme, Spec und Übergabe nennen, was 5b baut.
    guide = " ".join(_guide_section("Fahrverhalten (Phase 5b)").split())
    for endpoint in (
        "`GET /api/learning/driving`",
        "`POST /api/learning/driving/recompute`",
        "`PUT /api/control/vehicles/{key}/automation`",
    ):
        assert endpoint in guide, endpoint
    for entity in ("auto_target", "suggested_departure", "suggested_target", "next_departure"):
        assert f"`tesla_{entity}`" in guide, entity
    assert "Komponente `driving`" in guide
    assert "**03:40 Uhr**" in guide
    # Ausrollen: Sicherung, core und app gemeinsam, Entität der Anwesenheit, Automatik bewusst
    _, found, rest = _guide_section("Fahrverhalten (Phase 5b)").partition(
        "### Fahrverhalten ausrollen (Plan 5b)\n"
    )
    assert found, "Der Unterabschnitt „Fahrverhalten ausrollen (Plan 5b)“ fehlt."
    rollout = " ".join(rest.split("\n### ", 1)[0].split())
    for step in (
        "/backup/vor-plan-5b.sqlite3",
        "`0014`",
        "`driving_profile`",
        "`driving_automation`",
        "core und app gemeinsam",
        "Regelbetrieb aus",
        "`departure_on`",
        "`device_tracker.…`",
        "„Anwesenheit aus Home Assistant“",
        "Tessie oder `home_entity`",
        "Komponente `driving`",
        "03:40",
        "„Jetzt neu berechnen“",
        "Konfidenz „mittel“",
        "Phase 3",
        "3d, 4b, 5a und 6a",
        "docs/verification/abnahme-phase-5b.md",
    ):
        assert step in rollout, step
    # Abnahme: die vier Kriterien der Entscheidung 16
    text = " ".join(Path("docs/verification/abnahme-phase-5b.md").read_text("utf-8").split())
    for criterion in (
        "28 Tage",
        "Vorschläge mit Konfidenz",
        "„Prognosen“",
        "„Laden“",
        "mindestens 10 bewerteten Tagen",
        "mindestens 80 %",
        "GET /api/learning/driving",
        "Zeile „geändert“",
        "laufenden geplanten Ladung",
        "erwartetes Auto",
        "wird beim Anstecken durch das echte ersetzt",
        "Unterschrift",
    ):
        assert criterion in text, criterion
    assert "abnahme-phase-5b.md" in Path("docs/verification/README.md").read_text("utf-8")
    assert "Nichts geht von selbst live" in text  # Entscheidung 17
    assert "nicht auf `main`" in Path("docs/uebergabe.md").read_text("utf-8")
    # Reihenfolge des Ausrollens: Regelbetrieb aus, bevor ems-app stoppt (sonst ist der Schalter
    # nicht erreichbar), dann sichern, dann core und app gemeinsam ziehen, dann wieder an
    steps = [
        rollout.index(marker)
        for marker in (
            "Regelbetrieb aus",
            "docker compose stop ems-app",
            "docker compose pull && docker compose up -d",
            "Regelbetrieb an",
        )
    ]
    assert steps == sorted(steps), steps
    assert "influxdb.ha_database" in rollout
    # Spec: „Stand Plan 5b“ in jedem Abschnitt, in dem 5b etwas ändert
    spec = Path("docs/superpowers/specs/2026-10-08-bindaems-design.md").read_text("utf-8")
    for number in ("5.3", "7.5", "7.6", "9.3", "10.4", "11.3", "12", "13", "18"):
        heading = f"\n## {number}. " if "." not in number else f"\n### {number} "
        _, found, rest = spec.partition(heading)
        assert found, number
        body = re.split(r"\n###? \d", rest, maxsplit=1)[0]
        assert "**Stand Plan 5b**" in body, number


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
    from datetime import UTC, datetime

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
            guard = PeakGuard(cfg.control, cfg.battery.reserve_soc_pct, ceiling_w)
            peak = guard.health(datetime(2026, 10, 12, tzinfo=UTC))
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
    assert len(SCENARIOS) == 42 and "neununddreißig" not in section  # die Zahl im Text zieht mit
    assert section.count("zweiundvierzig") == 3


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
        "bindaems.tools.coe_probe",
    } <= set(TOOL_COMMAND.findall(handbook))
    for doc in (
        "docs/betrieb.md",
        "docs/verification/README.md",
        "docs/verification/abnahme-phase-6a.md",
        "cerbo-watchdog/README.md",
    ):
        for command in TOOL_COMMAND.finditer(Path(doc).read_text(encoding="utf-8")):
            assert importlib.util.find_spec(command[1]) is not None, doc
            assert "--entrypoint python" in command[0], doc


def test_part2_options_in_the_docs_exist(capsys: pytest.CaptureFixture[str]) -> None:
    # Die Abschnitte zu Teil 2 nennen nur Optionen, die verify_part2 kennt (und Docker-Flags).
    from bindaems.tools.verify_part2 import main

    with pytest.raises(SystemExit):
        main(["--help"])
    known = set(re.findall(r"--[a-z][a-z-]*", capsys.readouterr().out)) | {
        "--rm",
        "--entrypoint",
        "--service-ports",  # Docker: der Punkt `thermal` braucht den veröffentlichten UDP-Port
    }
    sections = [
        ("docs/betrieb.md", r"### Prüfprotokoll Teil 2\n(.*?)\n### ", ("--schlafend",)),
        (
            "docs/betrieb.md",
            r"### Prüfprotokoll Teil 2, Punkt thermal\n(.*?)\n### ",
            ("--geraet",),
        ),
        (
            "docs/verification/README.md",
            r"## Teil 2 – schreibend \(17\.2\)\n(.*?)\n## ",
            ("--schlafend", "--geraet"),
        ),
    ]
    for doc, pattern, extra in sections:
        found = re.search(pattern, Path(doc).read_text(encoding="utf-8"), re.DOTALL)
        assert found, doc
        documented = set(re.findall(r"--[a-z][a-z-]*", found[1]))
        assert {"--punkt", "--out", "--yes", *extra} <= documented, doc
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
        "AUTO_ERWARTET": {"name": "Tesla Model 3", "um": "17:30", "soc": 41.0},
        "KLIMA_MEHR": {"mode": "heizen", "k": 1.0},
        "KLIMA_WENIGER": {"mode": "heizen", "k": -0.5},
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
        "AUTO_ERWARTET",
        "ZIEL_SOC_ABFAHRT",
        "AUTO_WARTET_AUF_PV",
        "ZIEL_SOC_UNERREICHBAR",
        "UNTERSTUETZUNG_RESERVIERT",
        "KLIMA_MEHR",
        "KLIMA_WENIGER",
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
    # Ruling R23: ein Lauf vor der Bestätigung der Absicht wird für die Autos wiederholt
    flat = " ".join(section.split())
    assert "Bestätigt Health die Absicht, plant die app mit `vehicles` neu" in flat
    # der core regelt ab seinem nächsten Zyklus mit der neuen Absicht; nachlaufen tut Health der app
    assert "regelt der core bis zum nächsten Health noch mit der alten" not in flat
    assert "regelt der core ab seinem nächsten Zyklus mit ihr" in flat
    assert "bis zu 10 s" in flat


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
    assert "auf die volle Minute aufgerundet" in " ".join(section.split())
    assert "| Ziel-SOC (je Fahrzeug) | Zahl |" in section
    assert "| Abfahrtszeit (je Fahrzeug) | Text |" in section
    assert "topic write homeassistant/text/bindaems/#" in section
    assert "Sofort, Aus oder Geplant" in section


def test_driving_entities_in_the_guide_exist() -> None:
    # „Fahrverhalten (Phase 5b)“, Unterabschnitt „Home Assistant“: Objekt-ID, Name und Plattform
    # wie die Entitäten der app; der Schalter steht auch in der Tabelle der Bedienelemente und
    # alle vier im Abschnitt 7.
    _check_entities_exist(
        _documented_entities("Fahrverhalten (Phase 5b)"),
        {
            "tesla_auto_target",
            "tesla_suggested_departure",
            "tesla_suggested_target",
            "tesla_next_departure",
        },
    )
    section = " ".join(_guide_section("Fahrverhalten (Phase 5b)").split())
    for confidence in ("„hoch“", "„mittel“", "„niedrig“"):
        assert confidence in section
    for basis in ("„Wochentag“", "„Gruppe Mo–Fr“", "„Gruppe Sa“", "„Gruppe So/Feiertag“"):
        assert basis in section
    # Zeitstempel sind UTC, `HH:MM` ist Ortszeit (Fehler der Task 11 im Satz zu den Zeiten)
    assert "Alle Zeiten sind ISO 8601 in UTC" not in section
    assert "Alle Zeitstempel sind ISO 8601 in UTC, Werte der Form `HH:MM` sind Ortszeit" in section
    controls = _guide_section("7. Home Assistant über MQTT")
    assert "| Automatik Ziel und Abfahrt (je Fahrzeug) | Schalter |" in controls
    for name in ("Vorschlag Abfahrt", "Vorschlag Ziel-SOC", "Nächste Abfahrt"):
        assert name in controls


def test_thermal_plan_entities_in_the_guide_exist() -> None:
    # „Vorheizen und Vorkühlen (Phase 5c)“, Unterabschnitt „Home Assistant“: Objekt-ID, Name und
    # Plattform wie die Entitäten der app; Schalter und Sensor stehen auch in den Tabellen von
    # Abschnitt 7, der Schalter mit der Wirkung auf `learning.thermal.enabled`.
    _check_entities_exist(
        _documented_entities("Vorheizen und Vorkühlen (Phase 5c)"),
        {"thermal_plan", "thermal_klima_planned_offset"},
    )
    section = " ".join(_guide_section("Vorheizen und Vorkühlen (Phase 5c)").split())
    for topic in (
        "`learning.thermal.enabled`",
        "Quelle „ha“, Urheber `home-assistant`",
        "„Vorheizen und Vorkühlen ein“",
        "„Vorheizen und Vorkühlen aus“",
        "0,5 K",
        "negativ beim Kühlen",
    ):
        assert topic in section, topic
    guide = " ".join(_guide_section("7. Home Assistant über MQTT").split())
    assert "| Vorheizen und Vorkühlen | Schalter |" in guide
    assert "| Klima Geplanter Versatz (`thermal_<Schlüssel>_planned_offset`) | K |" in guide


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
    for text in ("höchstens 400 Tage", "höchstens 2000 Einträge", "`guest`", "`unknown`"):
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
        "Ein unbekannter Modus",
        "darum gilt das nie als `early`",
        "War das Ziel vor der Lücke erreicht, bleibt die Ladung `reached`; läuft sie in einer "
        "neuen Session weiter, bleibt es eine Ladung, auch wenn danach wieder Bedarf da ist.",
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


def test_climate_chapter_matches_the_code(capsys: pytest.CaptureFixture[str]) -> None:
    # „Klimaanlage (Phase 6a)“: Gründe mit dem Text des Logs, die Alarme, die Zeiten, die
    # Konfigurationsschlüssel, die Sensoren in Home Assistant und die Werkzeuge stehen wie im Code
    # (Plan 6a, Task 13).
    from bindaems.app.ha.entities import build_entities
    from bindaems.core.actuators.thermal import ECHO_MISMATCH_S, REASON_NO_ECHO
    from bindaems.core.control.charging import FOLLOW_HOLD_S, RAMP_UP_S
    from bindaems.core.regulation_link import THERMAL_EXCEPTION
    from bindaems.core.runtime import THERMAL_PERMIT_S
    from bindaems.shared.config import (
        HA_THERMAL_PREFIX,
        CoeOffsetConfig,
        CoeReceive,
        ThermalStrategyConfig,
        load_config,
    )
    from bindaems.shared.decisions import _REASON_TEXT
    from bindaems.tools import coe_probe
    from bindaems.tools.part2.thermal import ITEM
    from bindaems.tools.verify_part2 import main as verify_main

    section = " ".join(_guide_section("Klimaanlage (Phase 6a)").split())
    codes = {code: text for code, text in _REASON_TEXT.items() if code.startswith("thermal_")}
    assert len(codes) == 11
    for code, text in codes.items():
        assert f"| `{code}` | {text} |" in section, code
    assert f"`{THERMAL_EXCEPTION}`" in section
    assert "`thermal.echo_mismatch.<name>`" in section
    assert f"mehr als {ECHO_MISMATCH_S:.0f} s" in section
    # die Zeiten der Autos und der Freigabe, je an ihrer Stelle (nicht irgendwo im Kapitel)
    assert f"**Anlauf:** {RAMP_UP_S:.0f} s nach einem eigenen Start" in section
    assert f"Anlauf eines Autos ({RAMP_UP_S:.0f} s)" in section
    assert f"„begrenzt sich selbst“ wechselt erst nach {FOLLOW_HOLD_S:.0f} s" in section
    assert f"nach {THERMAL_PERMIT_S:.0f} s ab" in section
    assert f"in den letzten {THERMAL_PERMIT_S:.0f} s freigegeben" in section
    # die Freigabe nur für einen Versatz ungleich 0, ohne Echo nicht steuerbar (Rulings R26, R27)
    assert "Freigegeben wird nur ein Versatz ungleich 0" in section
    assert f"„{REASON_NO_ECHO}“" in section
    # jeder Schlüssel der Konfiguration steht im Kapitel
    keys = {*CoeOffsetConfig.model_fields, *CoeReceive.model_fields}
    keys |= set(ThermalStrategyConfig.model_fields)
    for key in sorted(keys):
        assert f"`{key}`" in section or f"{key}:" in section, key
    # die Sensoren in Home Assistant: Name und Kennung je Sensor des Beispiels
    cfg = load_config(Path("deploy/config.example.yaml"))
    handbook = Path("docs/betrieb.md").read_text(encoding="utf-8")
    device = f"{HA_THERMAL_PREFIX}klima_"  # der Schalter „Vorheizen und Vorkühlen“ gehört zu 5c
    sensors = [s for s in build_entities(cfg) if s.object_id.startswith(device)]
    assert len(sensors) == 5
    for spec in sensors:
        suffix = spec.object_id.removeprefix(device)
        row = f"| {spec.name} (`{HA_THERMAL_PREFIX}<Schlüssel>_{suffix}`) |"
        assert row in handbook, spec.name
    # die Flags der Werkzeuge im Kapitel gibt es
    with pytest.raises(SystemExit):
        verify_main(["--help"])
    assert "--geraet" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        coe_probe.main(["listen", "--help"])
    listen_help = capsys.readouterr().out
    for flag in ("--sekunden", "--port"):
        assert flag in listen_help and f"`{flag}`" in section, flag
    assert "coe_probe listen" in section and "--punkt thermal" in section
    assert "`--geraet <Name>`" in section
    assert ITEM == "17.2-9"
    assert "5442/udp" in section
    spec_text = Path("docs/superpowers/specs/2026-10-08-bindaems-design.md").read_text("utf-8")
    assert "9. **`thermal` (Klimaanlage, Stand Plan 6a):**" in spec_text
    abnahme = Path("docs/verification/abnahme-phase-6a.md").read_text(encoding="utf-8")
    assert "abnahme-phase-6a.md" in Path("docs/verification/README.md").read_text("utf-8")
    assert "7 Tage" in abnahme and "±3 K" in abnahme
    # der Watchdog beim Stoppen des core steht im Kapitel, in der Abnahme und im README
    readme = Path("docs/verification/README.md").read_text("utf-8")
    for text in (section, " ".join(abnahme.split()), " ".join(readme.split())):
        assert re.search(r"löst der Watchdog (am Cerbo )?nach 90 s", text), text[:80]


def test_guide_lists_the_endpoints_of_load_model_and_forecast_quality() -> None:
    # Plan 5a, Task 11: die API-Liste im Abschnitt „Prognosen“ nennt jeden neuen Aufruf mit der
    # Rolle, die er verlangt, und die Texte, die das UI zeigt.
    section = " ".join(_guide_section("Prognosen").split())
    _, found, text = section.partition("### API für Lastmodell und Prognosegüte ")
    assert found, "Der Unterabschnitt „API für Lastmodell und Prognosegüte“ fehlt."
    text = text.split("### ", 1)[0]
    for topic in (
        "`GET /api/forecast/load` (ab „Lesen“)",
        "`method`",
        "`GET /api/forecast/quality?days=14` (ab „Lesen“)",
        "`improvement_pct`",
        "`GET /api/forecast/quality/daily?days=28` (ab „Lesen“, `days` von 1 bis 60)",
        "`GET /api/learning/load` (ab „Lesen“)",
        "`POST /api/learning/load/train` (nur Admin, Antwort 202",
        "409 „Training läuft schon.“",
        "`GET /api/learning/candidates` (nur Admin)",
        "HA-Datenbank nicht eingerichtet (influxdb.ha_database)",
    ):
        assert topic in text, topic


def test_guide_describes_the_driving_behaviour() -> None:
    # Plan 5b, Task 11: Abschnitt „Fahrverhalten (Phase 5b)“ mit Nachtlauf, Komponente, Automatik
    # und jedem neuen Aufruf samt Rolle; die Sätze der Automatik stehen so im Code.
    from datetime import UTC, datetime

    from bindaems.app.learning import driving_auto as auto
    from bindaems.app.learning.driving_service import COMPONENT, RUN_AT
    from bindaems.app.learning.routes import RECOMPUTE_RUNNING

    text = " ".join(_guide_section("Fahrverhalten (Phase 5b)").split())
    assert (RUN_AT.hour, RUN_AT.minute) == (3, 40) and COMPONENT == "driving"
    assert RECOMPUTE_RUNNING == "Berechnung läuft schon."
    for topic in (
        "03:40 Uhr",
        "Jetzt neu berechnen",
        "Berechnung läuft …",
        "`driving_profile`",
        "Komponente `driving`",
        "Fahrverhalten aktuell",
        "Noch keine Fahrdaten",
        "Fahrverhalten fehlgeschlagen: {Fehler}",
        "ab Werk **aus**",
        "pausiert die Automatik bis zu dieser Abfahrt",
        "Konfidenz „mittel“",
        "`control.automation`",
        "„Automatik an“",
        "„Automatik aus“",
        "Urheber „Automatik“",
        "`GET /api/learning/driving` (ab „Lesen“)",
        "`POST /api/learning/driving/recompute` (nur Admin, Antwort 202",
        "409 „Berechnung läuft schon.“",
        "`PUT /api/control/vehicles/{key}/automation` (ab „Bedienen“)",
        '`{"on": true}`',
        "404",
        "`presence`",
        "`next_trip`",
        "`AUTO_ERWARTET`",
        "`return_reserve_pct`",
        "`target_min_pct`",
        "`target_max_pct`",
        "`presence_entities`",
    ):
        assert topic in text, topic
    # die Sätze der Zustände stehen in der Tabelle wie im Code
    moment = datetime(2026, 10, 12, 5, 15, tzinfo=UTC)
    for status in (
        auto._off(),
        auto._pending(),
        auto._inactive(),
        auto._uncertain(),
        auto._waiting(moment),
        auto._paused(moment),
    ):
        expected = status.text.replace("Mo 07:15", "{Tag} {HH:MM}")
        assert f"| `{status.state}` | „{expected}“ |" in "\n".join(
            line.strip() for line in _guide_section("Fahrverhalten (Phase 5b)").splitlines()
        ), status.state


def test_guide_describes_the_thermal_model_endpoints_and_component() -> None:
    # Plan 5c, Task 9: Abschnitt „Vorheizen und Vorkühlen (Phase 5c)“ mit Nachtlauf, Komponente,
    # Gründen des Modells, beiden Aufrufen samt Rolle und Fehlertexten; die Sätze stehen im Code.
    from dataclasses import fields, replace

    from bindaems.app.learning.routes import NO_THERMAL_DEVICE, RECOMPUTE_RUNNING
    from bindaems.app.learning.thermal_model import ModeModel, Reason
    from bindaems.app.learning.thermal_service import (
        COMPONENT,
        KEEP_DAYS,
        NO_DATA,
        RUN_AT,
        reason_text,
    )

    section = _guide_section("Vorheizen und Vorkühlen (Phase 5c)")
    text = " ".join(section.split())
    assert (RUN_AT.hour, RUN_AT.minute, KEEP_DAYS, COMPONENT) == (3, 50, 70, "thermal_model")
    assert NO_THERMAL_DEVICE == "Keine Klimaanlage eingerichtet."
    for topic in (
        "**03:50 Uhr**",
        "nach dem Lastmodell um 03:15 und dem Fahrverhalten um 03:40, vor der Sicherung um 04:00",
        "behalten werden 70 Tage",
        "Jetzt neu berechnen",
        "Berechnung läuft …",
        "Scheitert das Lesen oder Rechnen, gilt der letzte Stand weiter",
        "löst neu planen aus",
        "Komponente `thermal_model`",
        f"„{NO_DATA}“",
        "Wärmemodell aktuell",
        "Wärmemodell fehlgeschlagen: {Fehler}",
        "`GET /api/learning/thermal` (ab „Lesen“)",
        "`POST /api/learning/thermal/recompute` (nur Admin, Antwort 202",
        f"409 „{RECOMPUTE_RUNNING}“",
        f"404 „{NO_THERMAL_DEVICE}“",
        "`GET /api/plan` (ab „Lesen“) nennt seit Plan 5c zusätzlich",
        "erste Gerät der Art `coe_offset`",
        "ab Werk **aus**",
    ):
        assert topic in text, topic
    # jedes Feld des Modells steht in der Beschreibung der Antwort
    for field in fields(ModeModel):
        assert f"`{field.name}`" in text, field.name
    # die Sätze der Gründe stehen in der Tabelle wie im Code
    base = ModeModel(
        mode="heizen",
        days=9,
        u_w_per_k=80.0,
        pv_w_per_kw=None,
        base_w=None,
        r2=0.25,
        tau_h=3.0,
        episodes=2,
        c_kwh_per_k=None,
        c_model_kwh_per_k=None,
        events=0,
        event_error_pct=None,
        max_w=None,
        usable=False,
        reason="few_days",
        gap_w_per_k=None,
    )
    shown: dict[str, str] = {}
    for reason in ("ok", "few_days", "few_episodes", "poor_fit", "implausible_tau", "no_data"):
        sentence = reason_text(replace(base, reason=reason, usable=reason == "ok"))
        sentence = sentence.replace("Heizen", "{Betriebsart}").replace("3,0 h", "{τ} h")
        sentence = sentence.replace("0,25", "{r}").replace("2 von", "{n} von")
        shown[reason] = sentence.replace("9 von", "{n} von")
    assert set(shown) == set(get_args(Reason))
    assert shown["few_days"] == "Noch zu wenig Tage mit {Betriebsart}: {n} von 14."
    for reason, sentence in shown.items():
        assert f"| `{reason}` | „{sentence}“ |" in section, reason


def test_guide_describes_the_thermal_settings() -> None:
    # Die Tabelle der Einstellungen `learning.thermal` nennt Felder, Standardwerte und Bereiche wie
    # das Modell, dazu die Regel der 0,5-K-Schritte mit ihrem Fehlertext und die Kappung am Versatz.
    from pydantic import ValidationError

    from bindaems.app.planning.thermal_stage import band
    from bindaems.shared.settings import LearningSettings, ThermalPlanSettings

    section = _guide_section("Vorheizen und Vorkühlen (Phase 5c)")
    rows = {
        cells[0].strip("`"): cells[1:]
        for line in section.splitlines()
        if line.startswith("| `")
        and (cells := [cell.strip() for cell in line.strip().strip("|").split("|")])
        and cells[0].strip("`") in ThermalPlanSettings.model_fields
    }
    assert set(rows) == set(ThermalPlanSettings.model_fields)
    defaults = ThermalPlanSettings()
    assert defaults.enabled is False and rows["enabled"][0] == "aus"
    for name, bounds in (
        ("heat_up_k", "0 bis 3 K"),
        ("heat_down_k", "0 bis 2 K"),
        ("cool_up_k", "0 bis 3 K"),
        ("cool_down_k", "0 bis 2 K"),
        ("margin_ct", "0 bis 20 ct/kWh"),
    ):
        assert rows[name][0] == str(getattr(defaults, name)).replace(".", ","), name
        assert rows[name][1] == bounds, name
    # die Bereiche gelten wirklich: Grenzen erlaubt, knapp darüber nicht
    ThermalPlanSettings(heat_up_k=3.0, heat_down_k=2.0, cool_up_k=0.0, margin_ct=20.0)
    for bad in ({"heat_up_k": 3.5}, {"heat_down_k": 2.5}, {"cool_up_k": -0.5}, {"margin_ct": 21}):
        with pytest.raises(ValidationError):
            ThermalPlanSettings(**bad)
    message = "Das Komfortband geht in Schritten von 0,5 K."
    with pytest.raises(ValidationError, match=message):
        ThermalPlanSettings(heat_up_k=1.2)
    text = " ".join(section.split())
    assert f"„{message}“" in text and "mit 422" in text
    # ältere Einstellungen ohne den Abschnitt laden mit den Standardwerten
    assert LearningSettings.model_validate({}).thermal == defaults
    assert "ältere Einstellungen ohne den Abschnitt laden mit den Standardwerten" in text
    # das Band bleibt innerhalb von `max_offset_k`, auf 0,5 K abgerundet
    assert band("heizen", ThermalPlanSettings(), max_offset_k=1.2) == (1.0, 1.0)
    assert "`max_offset_k`" in text and "auf ein Vielfaches von 0,5 K abgerundet" in text


def test_guide_describes_the_thermal_plan_fields_and_migration() -> None:
    # `GET /api/plan` und die Reihen des Handbuchs: jedes neue Feld und jeder Grund steht da; die
    # Migration `0015` legt die Tabelle an, die das Handbuch beschreibt.
    from tests.plan_helpers import sample_plan

    from bindaems.app.db.schema import thermal_model
    from bindaems.app.planning.routes import slot_json, thermal_json
    from bindaems.shared.plan import PlanThermal, ThermalReason

    section = _guide_section("Vorheizen und Vorkühlen (Phase 5c)")
    text = " ".join(section.split())
    plan = sample_plan("2026-10-09T08:00:00+00:00", ["08:00"])
    new_slot_keys = {"thermal_w", "thermal_grid_w", "thermal_steps"}
    assert new_slot_keys <= set(slot_json(plan.slots[0]))
    for key in new_slot_keys:
        assert f"`{key}`" in text, key
    thermal = PlanThermal(
        device="klima",
        mode="heizen",
        active=False,
        saving_ct=1.0,
        model_computed_at=None,
        reason="ok",
    )
    for key in thermal_json(thermal):
        assert f"`{key}`" in text, key
    reasons = re.findall(r"^\| `(\w+)` \| .* \|$", section.split("| `thermal.reason`")[1], re.M)
    assert reasons == list(get_args(ThermalReason))
    assert "**Vorschau**" in text and "`KLIMA_MEHR`" in text and "`KLIMA_WENIGER`" in text
    # Migration 0015 und Tabelle
    migration = Path("src/bindaems/app/db/migrations/versions/v0015_thermal_model.py")
    assert 'revision = "0015"' in migration.read_text("utf-8")
    assert "`0015`" in text and "`thermal_model`" in text
    for column in thermal_model.c.keys():
        if column != "id":
            assert f"`{column}`" in text, column
    assert "/backup/vor-plan-5c.sqlite3" in text
    update = " ".join(_guide_section("4. Bedienung").split())
    for topic in (
        "Update mit Plan 5c (Vorheizen und Vorkühlen)",
        "Rollback auf einen Stand vor Plan 5c",
        "`0015`",
        'command.downgrade(config, "0014")',
        "Datenbank auf Stand 0014",
        "/backup/vor-plan-5c.sqlite3",
        "`learning.thermal`",
    ):
        assert topic in update, topic

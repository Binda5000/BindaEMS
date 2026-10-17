"""Das Grafana-Startdashboard (`deploy/grafana/bindaems.json`) passt zu dem, was geschrieben wird.

Messungen, Felder, Tags und Retention Policies stehen an einer Stelle (`KNOWN`); ein Test
vergleicht sie mit den Schreibern (Sampler im core, Abrechnung, Preise, Entscheidungen) und mit
`deploy/influxdb-setup.sh`, die anderen prüfen das Dashboard gegen diese Liste.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from tests.unit.shared.test_decisions import decision

from bindaems.app.control import decisions as decisions_module
from bindaems.core.telemetry.sampler import DERIVED_FIELDS, RULES

DASHBOARD = Path("deploy/grafana/bindaems.json")
SOURCES = Path("src/bindaems")
INPUT = "DS_BINDAEMS"
ROWS = ("Leistung", "Ladestand", "Preise", "Abrechnung je Tag")


@dataclass(frozen=True)
class Known:
    rps: frozenset[str]  # Retention Policies, in denen die Messung steht
    fields: frozenset[str]
    tags: frozenset[str]


# Messungen, Felder und Tags, die das Dashboard nutzen darf (Spec 11.2, nach dem Code):
# `decision` schreibt `text` und `codes` (nicht `value`) in `raw`; `price` und `ledger` schreibt die
# app in `long`; `power` und `soc` stehen in `raw` und, als Minutenmittel, in `long`.
KNOWN = {
    "power": Known(
        frozenset({"raw", "long"}),
        frozenset({"p_w", "i_a", "u_v"}),
        frozenset({"source", "id", "phase"}),
    ),
    "soc": Known(frozenset({"raw", "long"}), frozenset({"pct"}), frozenset({"id"})),
    "price": Known(frozenset({"long"}), frozenset({"ct_kwh"}), frozenset({"kind"})),
    "ledger": Known(frozenset({"long"}), frozenset({"kwh", "eur"}), frozenset({"flow"})),
    "decision": Known(
        frozenset({"raw"}),
        frozenset({"text", "codes"}),
        frozenset({"actuator", "kind", "result", "dry_run"}),
    ),
}
# Werte von Tags, die das Dashboard festlegt
TAG_VALUES = {
    ("power", "source"): {"grid", "pv", "battery", "load", "wallbox", "derived"},
    ("power", "id"): {"house_load", "pv_total"},  # Festlegungen; die übrigen ids sind Geräte
    ("power", "phase"): {"total"},
    ("price", "kind"): {"import_gross", "feed_in"},
}


def _dashboard() -> dict[str, Any]:
    data = json.loads(DASHBOARD.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _panels(dashboard: dict[str, Any]) -> Iterator[dict[str, Any]]:
    for panel in dashboard["panels"]:
        yield panel
        yield from panel.get("panels", [])


def _queries(dashboard: dict[str, Any]) -> list[tuple[str, str]]:
    """(Ort, InfluxQL) aller Abfragen: Ziele der Panels und Markierungen."""
    found = []
    for panel in _panels(dashboard):
        for target in panel.get("targets", []):
            found.append((f"Panel {panel['title']}", target["query"]))
    for annotation in dashboard["annotations"]["list"]:
        if annotation.get("name") != "Grafana":  # die eingebaute Markierung hat keine Abfrage
            found.append((f"Markierung {annotation['name']}", annotation["query"]))
    return found


def _datasource_refs(node: object) -> Iterator[object]:
    """Alle Werte unter dem Schlüssel `datasource`, beliebig tief."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "datasource":
                yield value
            else:
                yield from _datasource_refs(value)
    elif isinstance(node, list):
        for item in node:
            yield from _datasource_refs(item)


def test_dashboard_is_valid_json_with_datasource_input() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    dashboard = json.loads(text)
    assert text == json.dumps(dashboard, indent=2, ensure_ascii=False) + "\n"  # lesbar, eingerückt
    (item,) = dashboard["__inputs"]
    assert item["name"] == INPUT
    assert item["type"] == "datasource"
    assert item["pluginId"] == "influxdb"
    assert item["label"]
    assert any(
        req["type"] == "datasource" and req["id"] == "influxdb" for req in dashboard["__requires"]
    )
    assert dashboard["schemaVersion"] >= 39
    assert dashboard["title"] == "BindaEMS"
    assert dashboard["timezone"] == "Europe/Vienna"
    # zum Importieren: keine feste Kennung, keine Versionsnummer aus einer anderen Installation
    assert dashboard["id"] is None
    assert dashboard["uid"] == "bindaems"
    # jede Datenquelle ist die Eingabe, keine feste UID
    refs = list(_datasource_refs(dashboard))
    assert refs
    for ref in refs:
        assert ref == {"type": "influxdb", "uid": f"${{{INPUT}}}"}, ref
    # die Zeilen in der vorgesehenen Reihenfolge
    rows = [panel["title"] for panel in dashboard["panels"] if panel["type"] == "row"]
    assert rows == list(ROWS)
    # Variablen und Vorlagen brauchen keine eigene Datenquelle
    assert dashboard.get("templating", {}).get("list", []) == []


def test_every_panel_belongs_to_a_row_and_has_queries() -> None:
    dashboard = _dashboard()
    current = None
    ids = []
    for panel in dashboard["panels"]:
        ids.append(panel["id"])
        if panel["type"] == "row":
            current = panel["title"]
            continue
        assert current is not None, f"{panel['title']} steht vor der ersten Zeile"
        assert panel["targets"], panel["title"]
        assert panel["description"], panel["title"]
        for target in panel["targets"]:
            assert target["rawQuery"] is True
            assert target["resultFormat"] == "time_series"
    assert len(ids) == len(set(ids))


def test_the_rows_hold_the_expected_data() -> None:
    dashboard = _dashboard()
    by_row: dict[str, list[str]] = {}
    current = ""
    for panel in dashboard["panels"]:
        if panel["type"] == "row":
            current = panel["title"]
            by_row[current] = []
        else:
            by_row[current] += [target["query"] for target in panel["targets"]]
    joined = {row: "\n".join(queries) for row, queries in by_row.items()}
    assert set(joined) == set(ROWS)
    for source in ("grid", "battery", "load", "wallbox", "derived"):
        assert f"\"source\" = '{source}'" in joined["Leistung"], source
    # Haus und PV gesamt aus den abgeleiteten Reihen; die PV je Gerät nicht zusätzlich (doppelt)
    for derived in ("house_load", "pv_total"):
        assert f"\"id\" = '{derived}'" in joined["Leistung"], derived
    assert "\"source\" = 'pv'" not in joined["Leistung"]
    assert "\"phase\" = 'total'" in joined["Leistung"]
    assert 'FROM "raw"."power"' in joined["Leistung"]
    assert 'FROM "raw"."soc"' in joined["Ladestand"]
    for kind in ("import_gross", "feed_in"):
        assert f"\"kind\" = '{kind}'" in joined["Preise"], kind
    assert 'FROM "long"."price"' in joined["Preise"]
    ledger = joined["Abrechnung je Tag"]
    assert 'FROM "long"."ledger"' in ledger
    assert 'sum("kwh")' in ledger and 'sum("eur")' in ledger
    assert ledger.count("tz('Europe/Vienna')") == len(by_row["Abrechnung je Tag"])
    for query in by_row["Abrechnung je Tag"]:
        assert 'GROUP BY time(1d), "flow"' in query
    (annotation,) = [a for a in dashboard["annotations"]["list"] if a.get("name") != "Grafana"]
    assert annotation["name"] == "Entscheidungen"
    assert 'FROM "raw"."decision"' in annotation["query"]
    assert annotation["enable"] is True


_FROM = re.compile(r'FROM "(?P<rp>[a-z]+)"\."(?P<measurement>[a-z_]+)"')
_SELECT = re.compile(r"^SELECT (?P<select>.*?) FROM ", re.DOTALL)
_FUNCTION_FIELD = re.compile(r'\b(?:mean|sum|last|max|min)\("(?P<field>[a-z_]+)"\)')
_QUOTED = re.compile(r'"(?P<name>[a-z_]+)"')
_CONDITION = re.compile(r"\"(?P<tag>[a-z_]+)\" = '(?P<value>[^']*)'")
_WHERE = re.compile(r" WHERE (?P<where>.*?)(?: GROUP BY | ORDER BY | tz\(|$)", re.DOTALL)
_GROUP = re.compile(r" GROUP BY (?P<group>.*?)(?: fill\(| tz\(| ORDER BY |$)", re.DOTALL)


def _check_query(where: str, query: str) -> None:
    """Messung, Retention Policy, Felder und Tags der Abfrage stehen in `KNOWN`."""
    query = " ".join(query.split())
    source = _FROM.search(query)
    assert source, f'{where}: kein FROM "rp"."messung" in {query}'
    measurement = source["measurement"]
    assert measurement in KNOWN, f"{where}: unbekannte Messung {measurement}"
    known = KNOWN[measurement]
    assert source["rp"] in known.rps, f"{where}: {measurement} steht nicht in {source['rp']}"
    select = _SELECT.search(query)
    assert select, f"{where}: kein SELECT in {query}"
    fields = _FUNCTION_FIELD.findall(select["select"]) or _QUOTED.findall(select["select"])
    assert fields, f"{where}: kein Feld in {query}"
    for name in fields:
        assert name in known.fields | known.tags, f"{where}: {measurement} hat kein Feld {name}"
    # Tags in WHERE und GROUP BY
    clause = _WHERE.search(query)
    for condition in _CONDITION.finditer(clause["where"] if clause else ""):
        tag, value = condition["tag"], condition["value"]
        assert tag in known.tags, f"{where}: {measurement} hat kein Tag {tag}"
        allowed = TAG_VALUES.get((measurement, tag))
        assert allowed is None or value in allowed, f"{where}: {tag} = {value}"
    group = _GROUP.search(query)
    for name in _QUOTED.findall(group["group"] if group else ""):
        assert name in known.tags, f"{where}: {measurement} hat kein Tag {name}"
    # Zeitfilter, damit Grafana den Zeitraum des Dashboards nutzt
    assert "$timeFilter" in query, f"{where}: ohne $timeFilter"


def test_every_query_uses_a_known_measurement_and_field() -> None:
    queries = _queries(_dashboard())
    assert len(queries) >= 8
    for where, query in queries:
        _check_query(where, query)
    # alle Messungen der Liste, die das Dashboard braucht, kommen vor
    used = {_FROM.search(query)["measurement"] for _, query in queries}  # type: ignore[index]
    assert used == set(KNOWN)


def test_the_check_rejects_unknown_names() -> None:
    # Die Prüfung selbst: falsche Messung, Retention Policy, Feld oder Tag fallen auf.
    ok = 'SELECT mean("pct") FROM "raw"."soc" WHERE $timeFilter GROUP BY time($__interval), "id"'
    _check_query("ok", ok)
    for bad in (
        ok.replace('"soc"', '"soc2"'),
        ok.replace('"raw"', '"long2"'),
        ok.replace('"pct"', '"percent"'),
        ok.replace('"id"', '"name"'),
        ok.replace('"raw"."soc"', '"raw"."price"'),
        ok.replace("WHERE $timeFilter", "WHERE \"source\" = 'grid' AND $timeFilter"),
        ok.replace("$timeFilter", "time > now() - 1h"),
    ):
        with pytest.raises(AssertionError):
            _check_query("bad", bad)


def test_known_list_matches_the_writers() -> None:
    # power und soc: Felder und Tags aus den Regeln des Samplers im core
    for measurement in ("power", "soc"):
        rules = [rule for rule in RULES if rule.measurement == measurement]
        assert rules
        assert {rule.field for rule in rules} == KNOWN[measurement].fields
        assert {tag for rule in rules for tag in rule.tags} == KNOWN[measurement].tags
    sources = {rule.tags["source"] for rule in RULES if rule.measurement == "power"}
    # `derived` steht nicht in RULES: der Sampler schreibt es aus DERIVED_FIELDS (ohne „_w“)
    derived = {name.removesuffix("_w") for name in DERIVED_FIELDS}
    assert TAG_VALUES[("power", "source")] - {"derived"} <= sources
    assert "derived" not in sources
    assert TAG_VALUES[("power", "id")] <= derived
    # decision: Tags und Felder des Punkts, den die app schreibt, und seine Retention Policy
    point = decisions_module._point(decision(id="x"))
    assert point.measurement == "decision"
    assert point.rp == "raw"
    assert set(point.tags) == KNOWN["decision"].tags
    assert set(point.fields) == KNOWN["decision"].fields
    # price und ledger: die app schreibt sie mit diesen Namen in `long`
    pipeline = (SOURCES / "app/prices/pipeline.py").read_text(encoding="utf-8")
    assert 'Point("price", {"kind": kind}, {"ct_kwh": value}, ts, rp=RP_LONG)' in pipeline
    for kind in TAG_VALUES[("price", "kind")]:
        assert f'_write(self._sink, "{kind}"' in pipeline, kind
    ledger = (SOURCES / "app/ledger/service.py").read_text(encoding="utf-8")
    assert 'Point("ledger", {"flow": name}, fields, start, rp=RP_LONG)' in ledger
    assert '{"kwh": kwh}' in ledger and 'fields["eur"]' in ledger
    # Retention Policies und Continuous Queries des Einrichtungsskripts
    setup = Path("deploy/influxdb-setup.sh").read_text(encoding="utf-8").replace('\\"', '"')
    assert "retention_policy raw " in setup and "retention_policy long " in setup
    for measurement, known in KNOWN.items():
        in_long = f'INTO $LONG."{measurement}"' in setup
        if measurement in ("power", "soc"):
            assert in_long and known.rps == {"raw", "long"}, measurement
        else:  # nur dort, wo die app oder die Entscheidungen sie schreiben
            assert not in_long and len(known.rps) == 1, measurement


def test_no_plant_data_in_dashboard() -> None:
    text = DASHBOARD.read_text(encoding="utf-8")
    assert not re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text), "IP-Adresse"
    assert not re.search(r"https?://|\bwww\.|\.lan\b|\.local\b|\.at\b|\.com\b", text), "Adresse"
    assert not re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), "E-Mail-Adresse"
    assert not re.search(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text, re.IGNORECASE
    ), "UUID"
    assert not re.search(r"\b\d{6,}\b", text), "lange Zahl (Portal-ID, Seriennummer)"
    assert not re.search(r"portal|token|passwor|secret|serial", text, re.IGNORECASE)
    # keine Namen und Hosts aus der Konfiguration (auch nicht die des Beispiels)
    config = yaml.safe_load(Path("deploy/config.example.yaml").read_text(encoding="utf-8"))
    names = [str(vehicle["name"]) for vehicle in config["vehicles"].values() if vehicle.get("name")]
    names += [str(wb["host"]) for wb in config["wallboxes"].values() if wb.get("host")]
    assert names
    for name in names:
        assert name not in text, name
    # keine Datenquelle ist fest verdrahtet
    for ref in _datasource_refs(json.loads(text)):
        assert isinstance(ref, dict) and ref["uid"] == f"${{{INPUT}}}"

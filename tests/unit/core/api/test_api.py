import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import datetime, timedelta
from types import MappingProxyType
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from starlette.websockets import WebSocketDisconnect
from tests.helpers import EMPTY_DERIVED, T0, snap
from tests.unit.shared.test_decisions import decision

from bindaems.core.api.app import HealthReport, create_api, snapshot_to_json
from bindaems.core.control.intent import IntentReply
from bindaems.core.watchdog_client.status import WATCHDOG_VERSION
from bindaems.shared.control import Intent
from bindaems.shared.decisions import Decision, decision_to_json

TOKEN = "x" * 32
H = {"Authorization": "Bearer " + TOKEN}


def on_event_loop() -> bool:
    """Ob der Aufruf auf einem Thread läuft, in dem eine Ereignisschleife läuft."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class FakeView:
    def __init__(self) -> None:
        self.current: Intent | None = None
        self.reply = IntentReply("accepted", 1)
        self.submitted: list[Intent] = []
        self.decision_list: list[Decision] = []
        self.decision_more = False
        self.decision_calls: list[tuple[datetime, int]] = []
        self.decision_on_loop: list[bool] = []

    def decisions(self, since: datetime, limit: int) -> tuple[list[Decision], bool]:
        self.decision_calls.append((since, limit))
        self.decision_on_loop.append(on_event_loop())
        return self.decision_list, self.decision_more

    def intent(self) -> Intent | None:
        return self.current

    def submit_intent(self, intent: Intent) -> IntentReply:
        self.submitted.append(intent)
        return self.reply

    def health(self) -> HealthReport:
        return HealthReport(
            mode="OBSERVE",
            version="0.1.0",
            adapters=[{"name": "victron", "connected": True}],
            selfcheck=[],
            alarms=[],
            cycle_ms_p95=None,
        )

    def state(self) -> dict[str, Any]:
        return snapshot_to_json(snap({"grid.l1.power_w": 1000.0}), EMPTY_DERIVED)

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[AsyncIterator[dict[str, Any]]]:
        async def messages() -> AsyncIterator[dict[str, Any]]:
            yield {"type": "state", "data": {}}

        yield messages()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_api(FakeView(), SecretStr(TOKEN)))


@pytest.fixture
def view_client() -> tuple[TestClient, FakeView]:
    view = FakeView()
    return TestClient(create_api(view, SecretStr(TOKEN))), view


def test_health_requires_token(client) -> None:
    assert client.get("/v1/health").status_code == 401


@pytest.mark.parametrize(
    "header", ["Bearer " + "y" * 32, "Basic " + TOKEN, TOKEN, "Bearer " + TOKEN + "x"]
)
def test_wrong_credentials_rejected(client, header: str) -> None:
    assert client.get("/v1/state", headers={"Authorization": header}).status_code == 401


def test_health_ok(client) -> None:
    body = client.get("/v1/health", headers=H).json()
    assert body["mode"] == "OBSERVE" and body["adapters"][0]["name"] == "victron"


def test_health_report_defaults_keep_existing_calls_valid() -> None:
    report = FakeView().health()  # ohne die Felder aus Plan 2a
    assert report.model_dump(exclude={"mode", "version", "adapters", "selfcheck", "alarms"}) == {
        "cycle_ms_p95": None,
        "mode_since": None,
        "mode_reasons": [],
        "intent": None,
        "actuators": [],
        "watchdog": {
            "state": "unknown",
            "trip_count": None,
            "last_trip": None,
            "last_trip_failures": None,
            "version": None,
            "expected_version": WATCHDOG_VERSION,
            "heartbeat": "off",
            "required": False,
        },
        "inputs": {"ok": True, "problems": []},
    }


@pytest.mark.parametrize("mode", ["START", "OBSERVE", "CONTROL", "DEGRADED", "SAFE"])
def test_health_report_accepts_the_operating_states(mode: str) -> None:
    report = FakeView().health().model_dump() | {"mode": mode}
    assert HealthReport.model_validate(report).mode == mode


def test_health_report_rejects_other_modes() -> None:
    with pytest.raises(ValidationError):
        HealthReport.model_validate(FakeView().health().model_dump() | {"mode": "observe"})


def test_state_shape(client) -> None:
    body = client.get("/v1/state", headers=H).json()
    assert body["signals"]["grid.l1.power_w"] == {
        "v": 1000.0,
        "ts": "2026-10-08T10:00:00+00:00",
        "q": "ok",
    }


def test_snapshot_to_json_converts_derived_mappings() -> None:
    derived = replace(
        EMPTY_DERIVED,
        house_load_w=1500.0,
        consumption_l=MappingProxyType({"L1": 500.0, "L2": None, "L3": 300.0}),
        wallbox_phase_a=MappingProxyType(
            {"evcs": MappingProxyType({"L1": 16.0, "L2": 0.0, "L3": 0.0})}
        ),
    )
    body = snapshot_to_json(snap({}), derived)
    assert body["ts"] == "2026-10-08T10:00:00+00:00"
    assert body["derived"]["house_load_w"] == 1500.0
    assert body["derived"]["grid_w"] is None
    assert body["derived"]["consumption_l"] == {"L1": 500.0, "L2": None, "L3": 300.0}
    assert body["derived"]["wallbox_phase_a"] == {"evcs": {"L1": 16.0, "L2": 0.0, "L3": 0.0}}
    assert type(body["derived"]["wallbox_phase_a"]["evcs"]) is dict  # JSON-fähig


def test_stream_sends_messages(client) -> None:
    with client.websocket_connect("/v1/stream", headers=H) as ws:
        assert ws.receive_json()["type"] == "state"


def test_stream_rejects_token_in_query(client) -> None:
    # ein Token in der URL landet in Zugriffslogs (uvicorn protokolliert den Pfad): nur Header
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(f"/v1/stream?token={TOKEN}") as ws:
            ws.receive_json()
    assert exc.value.code == 4401


@pytest.mark.parametrize("path", ["/v1/stream?token=falsch", "/v1/stream"])
def test_stream_rejects_bad_token(client, path: str) -> None:
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(path) as ws:
            ws.receive_json()
    assert exc.value.code == 4401


def test_put_intent_accepted(view_client) -> None:
    client, view = view_client
    view.reply = IntentReply("accepted", 3)
    r = client.put("/v1/intent", json={"version": 3, "control": True}, headers=H)
    assert (r.status_code, r.json()) == (200, {"result": "accepted", "version": 3, "details": []})
    assert view.submitted == [Intent(version=3, control=True)]


def test_put_intent_stale_is_409(view_client) -> None:
    client, view = view_client
    view.reply = IntentReply(
        "rejected", 5, ("Version 4 ist älter als die gültige Version 5.",), "stale"
    )
    assert client.put("/v1/intent", json={"version": 4}, headers=H).status_code == 409


def test_put_intent_unknown_actuator_is_422(view_client) -> None:
    client, view = view_client
    view.reply = IntentReply(
        "rejected", None, ("Unbekannter Aktor „wallbox.twc“.",), "unknown_actuator"
    )
    r = client.put(
        "/v1/intent", json={"version": 1, "actuators": {"wallbox.twc": "live"}}, headers=H
    )
    assert (r.status_code, r.json()["details"]) == (422, ["Unbekannter Aktor „wallbox.twc“."])


def test_get_intent(view_client) -> None:
    client, view = view_client
    view.current = Intent(version=3)
    assert client.get("/v1/intent").status_code == 401
    assert client.get("/v1/intent", headers=H).json() == {
        "intent": {"version": 3, "control": False, "actuators": {}}
    }


# Weitere Randfälle


def test_get_intent_without_intent_is_null(view_client) -> None:
    client, _ = view_client
    assert client.get("/v1/intent", headers=H).json() == {"intent": None}


def test_put_intent_clamped_is_200_with_details(view_client) -> None:
    client, view = view_client
    details = (
        "EVCS „evcs“: in config.yaml nicht für den Live-Betrieb freigegeben; bleibt im Dry-Run.",
    )
    view.reply = IntentReply("clamped", 1, details)
    r = client.put(
        "/v1/intent", json={"version": 1, "actuators": {"wallbox.evcs": "live"}}, headers=H
    )
    assert (r.status_code, r.json()) == (
        200,
        {"result": "clamped", "version": 1, "details": list(details)},
    )
    assert view.submitted == [
        Intent.model_validate({"version": 1, "actuators": {"wallbox.evcs": "live"}})
    ]


def test_put_intent_requires_token(view_client) -> None:
    client, view = view_client
    for headers in ({}, {"Authorization": "Bearer " + "y" * 32}):
        assert client.put("/v1/intent", json={"version": 1}, headers=headers).status_code == 401
        # ohne Token erfährt niemand, was der Body hätte erfüllen müssen
        assert client.put("/v1/intent", json={"version": 0}, headers=headers).status_code == 401
    assert view.submitted == []


@pytest.mark.parametrize(
    "body",
    [
        {"version": 0},
        {"control": True},
        {"version": 1, "actuators": {"victron": "an"}},
        {"version": 1, "unbekannt": True},
    ],
    ids=["version-0", "ohne-version", "ungueltiger-modus", "fremdfeld"],
)
def test_put_intent_with_invalid_body_is_422_and_not_submitted(view_client, body) -> None:
    client, view = view_client
    assert client.put("/v1/intent", json=body, headers=H).status_code == 422
    assert view.submitted == []


# Entscheidungen

SINCE_DETAIL = "since muss eine ISO-Zeit mit Zeitzone sein"
LIMIT_DETAIL = "limit muss zwischen 1 und 5000 liegen"


def test_decisions_since_with_limit(view_client) -> None:
    client, view = view_client
    view.decision_list = [decision(id="d1", ts=T0 + timedelta(seconds=1))]
    response = client.get("/v1/decisions", params={"since": T0.isoformat(), "limit": 1}, headers=H)
    assert response.status_code == 200
    assert response.json()["decisions"][0]["id"] == "d1" and response.json()["more"] is False
    assert view.decision_calls == [(T0, 1)]


@pytest.mark.parametrize(
    ("params", "detail"),
    [
        ({"since": "gestern"}, "since muss eine ISO-Zeit mit Zeitzone sein"),
        ({"since": "2026-10-08T10:00:00"}, "since muss eine ISO-Zeit mit Zeitzone sein"),
        (
            {"since": "2026-10-08T10:00:00+00:00", "limit": 0},
            "limit muss zwischen 1 und 5000 liegen",
        ),
    ],
)
def test_decisions_reject_bad_parameters(client, params, detail) -> None:
    response = client.get("/v1/decisions", params=params, headers=H)
    assert (response.status_code, response.json()) == (422, {"detail": detail})


def test_decisions_need_the_token(client) -> None:
    assert client.get("/v1/decisions", params={"since": T0.isoformat()}).status_code == 401


def test_decisions_ask_for_the_token_before_checking_the_parameters(view_client) -> None:
    client, view = view_client
    for headers in ({}, {"Authorization": "Bearer " + "y" * 32}):
        good = client.get("/v1/decisions", params={"since": T0.isoformat()}, headers=headers)
        # ohne Token erfährt niemand, was die Parameter hätten erfüllen müssen
        bad = client.get("/v1/decisions", params={"since": "gestern", "limit": 0}, headers=headers)
        assert (good.status_code, bad.status_code) == (401, 401)
    assert view.decision_calls == []


def test_decisions_without_limit_use_the_default_of_1000(view_client) -> None:
    client, view = view_client
    response = client.get("/v1/decisions", params={"since": T0.isoformat()}, headers=H)
    assert response.status_code == 200
    assert view.decision_calls == [(T0, 1000)]


@pytest.mark.parametrize("limit", [1, 5000])
def test_decisions_accept_the_limits_at_both_edges(view_client, limit: int) -> None:
    client, view = view_client
    response = client.get(
        "/v1/decisions", params={"since": T0.isoformat(), "limit": limit}, headers=H
    )
    assert response.status_code == 200
    assert view.decision_calls == [(T0, limit)]


@pytest.mark.parametrize(
    "since",
    ["2026-10-08T10:00:00Z", "2026-10-08T12:00:00+02:00", "2026-10-08T09:00:00-01:00"],
    ids=["Z", "plus-02-00", "minus-01-00"],
)
def test_decisions_hand_since_to_the_view_in_utc(view_client, since: str) -> None:
    client, view = view_client
    assert client.get("/v1/decisions", params={"since": since}, headers=H).status_code == 200
    [(moment, _)] = view.decision_calls
    assert moment == T0 and moment.utcoffset() == timedelta(0)


def test_decisions_can_be_continued_from_the_ts_of_a_returned_decision(view_client) -> None:
    # Die app liest weiter, indem sie das ts der letzten Entscheidung als since zurückschickt
    client, view = view_client
    last = T0 + timedelta(seconds=1, microseconds=123456)
    view.decision_list = [decision(ts=last)]
    first = client.get("/v1/decisions", params={"since": T0.isoformat()}, headers=H)
    ts = first.json()["decisions"][0]["ts"]
    assert ts == "2026-10-08T10:00:01.123456+00:00"
    assert client.get("/v1/decisions", params={"since": ts}, headers=H).status_code == 200
    assert view.decision_calls == [(T0, 1000), (last, 1000)]


@pytest.mark.parametrize(
    ("params", "detail"),
    [
        ({}, SINCE_DETAIL),  # since ist Pflicht
        ({"since": ""}, SINCE_DETAIL),
        ({"since": "2026-10-08"}, SINCE_DETAIL),  # nur ein Datum: keine Zeitzone
        ({"since": "2026-10-08 10:00:00"}, SINCE_DETAIL),
        ({"since": "10:00:00+00:00"}, SINCE_DETAIL),  # nur eine Uhrzeit
        ({"since": "2026-10-08T10:00:00+00:00x"}, SINCE_DETAIL),
        ({"since": "0001-01-01T00:00:00+01:00"}, SINCE_DETAIL),  # in UTC vor dem Jahr 1
        ({"since": "9999-12-31T23:59:59-01:00"}, SINCE_DETAIL),  # in UTC nach dem Jahr 9999
        ({"since": "gestern", "limit": 0}, SINCE_DETAIL),  # beides falsch: since zuerst
        ({"since": T0.isoformat(), "limit": ""}, LIMIT_DETAIL),
        ({"since": T0.isoformat(), "limit": "abc"}, LIMIT_DETAIL),
        ({"since": T0.isoformat(), "limit": "1.5"}, LIMIT_DETAIL),
        ({"since": T0.isoformat(), "limit": "9" * 5000}, LIMIT_DETAIL),
        ({"since": T0.isoformat(), "limit": -1}, LIMIT_DETAIL),
        ({"since": T0.isoformat(), "limit": 5001}, LIMIT_DETAIL),
    ],
    ids=[
        "ohne-since",
        "since-leer",
        "nur-datum",
        "ohne-zeitzone",
        "nur-uhrzeit",
        "text-hinter-der-zeit",
        "vor-jahr-1-in-utc",
        "nach-jahr-9999-in-utc",
        "beides-falsch",
        "limit-leer",
        "limit-text",
        "limit-bruch",
        "limit-riesig",
        "limit-negativ",
        "limit-zu-gross",
    ],
)
def test_decisions_with_bad_parameters_never_reach_the_view(view_client, params, detail) -> None:
    client, view = view_client
    response = client.get("/v1/decisions", params=params, headers=H)
    assert (response.status_code, response.json()) == (422, {"detail": detail})
    assert view.decision_calls == []


def test_decisions_longer_than_the_limit_are_returned_as_they_are(view_client) -> None:
    # Eine Gruppe gleicher Zeit (ein Zyklus) bleibt beisammen: Das Ergebnis darf das Limit sprengen
    client, view = view_client
    group = [decision(id=f"d{i}", ts=T0 + timedelta(seconds=1)) for i in range(3)]
    view.decision_list, view.decision_more = group, True
    response = client.get("/v1/decisions", params={"since": T0.isoformat(), "limit": 1}, headers=H)
    assert response.status_code == 200
    assert response.json() == {"decisions": [decision_to_json(d) for d in group], "more": True}
    assert view.decision_calls == [(T0, 1)]


def test_decisions_are_in_their_json_form_in_the_order_of_the_view(view_client) -> None:
    client, view = view_client
    first = decision(id="d1", ts=T0 + timedelta(seconds=1), inputs={"soc_pct": None})
    second = decision(id="d2", ts=T0 + timedelta(seconds=2), requested=None, applied=None)
    view.decision_list = [first, second]
    body = client.get("/v1/decisions", params={"since": T0.isoformat()}, headers=H).json()
    assert body == {"decisions": [decision_to_json(first), decision_to_json(second)], "more": False}
    assert body["decisions"][0]["ts"] == "2026-10-08T10:00:01+00:00"
    assert body["decisions"][0]["inputs"] == {"soc_pct": None}


def test_decisions_when_there_are_none(view_client) -> None:
    client, _ = view_client
    response = client.get("/v1/decisions", params={"since": T0.isoformat()}, headers=H)
    assert (response.status_code, response.json()) == (200, {"decisions": [], "more": False})


def test_decisions_are_read_off_the_event_loop(view_client) -> None:
    # Das Lesen der Tagesdateien darf den Zyklus nicht aufhalten, der auf derselben Schleife läuft
    client, view = view_client
    assert (
        client.get("/v1/decisions", params={"since": T0.isoformat()}, headers=H).status_code == 200
    )
    assert view.decision_on_loop == [False]


def test_no_public_schema_or_docs(client) -> None:
    assert client.get("/openapi.json").status_code == 404
    assert client.get("/docs").status_code == 404


async def test_real_client_sees_4401_not_http_403() -> None:
    import asyncio
    import socket

    import uvicorn
    from websockets.asyncio.client import connect
    from websockets.exceptions import ConnectionClosed

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config = uvicorn.Config(
        create_api(FakeView(), SecretStr(TOKEN)),
        host="127.0.0.1",
        port=port,
        log_level="warning",
        lifespan="off",
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(5):
            while not server.started:  # noqa: ASYNC110 - uvicorn bietet dafür kein Event
                await asyncio.sleep(0.01)
        async with connect(f"ws://127.0.0.1:{port}/v1/stream?token=falsch") as ws:
            with pytest.raises(ConnectionClosed) as exc:
                await ws.recv()
        assert exc.value.rcvd is not None and exc.value.rcvd.code == 4401
    finally:
        server.should_exit = True
        await task

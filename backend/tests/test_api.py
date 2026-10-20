import json

import boto3
import pytest
from moto import mock_aws

from app import handler
from app.repo import BY_START_INDEX, Repo


@pytest.fixture(autouse=True)
def table(monkeypatch):
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    with mock_aws():
        boto3.client("dynamodb").create_table(
            TableName="t",
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "startedAt", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            LocalSecondaryIndexes=[{
                "IndexName": BY_START_INDEX,
                "KeySchema": [
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "startedAt", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
        )
        monkeypatch.setattr(handler, "_repo", Repo("t"))
        yield


def call(method, path, body=None, query=None, user="alice", params=None):
    route = path
    for k, v in (params or {}).items():
        route = route.replace(v, "{" + k + "}")
    event = {
        "routeKey": f"{method} {route}",
        "pathParameters": params,
        "queryStringParameters": query,
        "body": json.dumps(body) if body is not None else None,
        "requestContext": {"authorizer": {"jwt": {"claims": {"sub": user}}}},
    }
    resp = handler.lambda_handler(event, None)
    return resp["statusCode"], json.loads(resp["body"]) if resp["body"] else None


def make_project(name="Client A", user="alice"):
    status, p = call("POST", "/projects", {"name": name}, user=user)
    assert status == 201
    return p


RANGE = {"from": "2026-01-01T00:00:00Z", "to": "2026-12-31T23:59:59Z"}


def test_project_crud():
    p = make_project()
    assert p["color"] == "#4f7cff"
    status, p2 = call("PATCH", f"/projects/{p['id']}", {"archived": True}, params={"id": p["id"]})
    assert status == 200 and p2["archived"] is True
    assert call("GET", "/projects")[1][0]["id"] == p["id"]
    assert call("DELETE", f"/projects/{p['id']}", params={"id": p["id"]})[0] == 204
    assert call("GET", "/projects")[1] == []


def test_validation_errors():
    assert call("POST", "/projects", {"name": ""})[0] == 400
    assert call("POST", "/projects", {"name": "x", "color": "red"})[0] == 400
    p = make_project()
    bad = {"projectId": p["id"], "startedAt": "2026-01-02T10:00:00Z", "endedAt": "2026-01-02T09:00:00Z"}
    assert call("POST", "/entries", bad)[0] == 400
    assert call("POST", "/entries", {**bad, "startedAt": "yesterday"})[0] == 400
    assert call("GET", "/entries", query={"from": "2026-01-01T00:00:00Z"})[0] == 400


def test_manual_entries_and_summary():
    a, b = make_project("A"), make_project("B")
    call("POST", "/entries", {"projectId": a["id"], "startedAt": "2026-03-01T09:00:00Z",
                              "endedAt": "2026-03-01T10:30:00Z"})
    call("POST", "/entries", {"projectId": b["id"], "startedAt": "2026-03-02T09:00:00Z",
                              "endedAt": "2026-03-02T09:15:00Z"})
    status, entries = call("GET", "/entries", query=RANGE)
    assert status == 200
    assert [e["seconds"] for e in entries] == [900, 5400]  # newest first

    status, s = call("GET", "/reports/summary", query=RANGE)
    assert s["totalSeconds"] == 6300
    assert {d["date"]: d["seconds"] for d in s["byDay"]} == {"2026-03-01": 5400, "2026-03-02": 900}

    narrow = {"from": "2026-03-02T00:00:00Z", "to": "2026-03-02T23:59:59Z"}
    assert len(call("GET", "/entries", query=narrow)[1]) == 1


def test_update_entry_rechecks_ordering():
    p = make_project()
    _, e = call("POST", "/entries", {"projectId": p["id"], "startedAt": "2026-03-01T09:00:00Z",
                                     "endedAt": "2026-03-01T10:00:00Z"})
    assert call("PATCH", f"/entries/{e['id']}", {"startedAt": "2026-03-01T11:00:00Z"},
                params={"id": e["id"]})[0] == 400
    status, e2 = call("PATCH", f"/entries/{e['id']}", {"description": "standup"}, params={"id": e["id"]})
    assert status == 200 and e2["description"] == "standup" and e2["seconds"] == 3600


def test_timer_start_stop_and_switch():
    a, b = make_project("A"), make_project("B")
    assert call("GET", "/timer")[1] == {"running": None}
    assert call("POST", "/timer/stop")[0] == 404

    _, t1 = call("POST", "/timer/start", {"projectId": a["id"], "description": "coding"})
    assert call("GET", "/timer")[1]["running"]["id"] == t1["running"]["id"]

    # Starting a second timer stops the first.
    _, t2 = call("POST", "/timer/start", {"projectId": b["id"]})
    first = handler.repo().get_entry("alice", t1["running"]["id"])
    assert first["endedAt"] is not None
    assert call("GET", "/timer")[1]["running"]["id"] == t2["running"]["id"]

    status, stopped = call("POST", "/timer/stop")
    assert status == 200 and stopped["endedAt"]
    assert call("GET", "/timer")[1] == {"running": None}


def test_cannot_set_end_on_running_entry():
    p = make_project()
    _, t = call("POST", "/timer/start", {"projectId": p["id"]})
    eid = t["running"]["id"]
    assert call("PATCH", f"/entries/{eid}", {"endedAt": "2030-01-01T00:00:00Z"}, params={"id": eid})[0] == 400


def test_deleting_running_entry_clears_timer():
    p = make_project()
    _, t = call("POST", "/timer/start", {"projectId": p["id"]})
    eid = t["running"]["id"]
    assert call("DELETE", f"/entries/{eid}", params={"id": eid})[0] == 204
    assert call("GET", "/timer")[1] == {"running": None}


def test_project_with_entries_cannot_be_deleted():
    p = make_project()
    call("POST", "/entries", {"projectId": p["id"], "startedAt": "2026-03-01T09:00:00Z",
                              "endedAt": "2026-03-01T10:00:00Z"})
    assert call("DELETE", f"/projects/{p['id']}", params={"id": p["id"]})[0] == 409


def test_entry_needs_existing_project():
    body = {"projectId": "nope", "startedAt": "2026-03-01T09:00:00Z", "endedAt": "2026-03-01T10:00:00Z"}
    assert call("POST", "/entries", body)[0] == 404


def test_users_are_isolated():
    p = make_project(user="alice")
    assert call("GET", "/projects", user="bob")[1] == []
    assert call("PATCH", f"/projects/{p['id']}", {"name": "x"}, user="bob", params={"id": p["id"]})[0] == 404
    assert call("POST", "/timer/start", {"projectId": p["id"]}, user="bob")[0] == 404


def test_missing_auth_and_unknown_route():
    resp = handler.lambda_handler({"routeKey": "GET /projects", "requestContext": {}}, None)
    assert resp["statusCode"] == 401
    assert handler.lambda_handler({"routeKey": "GET /nope"}, None)["statusCode"] == 404

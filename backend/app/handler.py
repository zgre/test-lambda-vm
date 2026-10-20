"""Lambda entry point for the HTTP API (payload format 2.0)."""

import json
import re
from collections import defaultdict
from datetime import datetime, timezone

from .repo import Conflict, NotFound, Repo, now_iso

ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

_repo: Repo | None = None


def repo() -> Repo:
    global _repo
    if _repo is None:
        _repo = Repo()
    return _repo


class BadRequest(Exception):
    pass


def _resp(status: int, body=None) -> dict:
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": "" if body is None else json.dumps(body),
    }


def _body(event) -> dict:
    raw = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        import base64

        raw = base64.b64decode(raw).decode()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise BadRequest("body must be JSON") from e
    if not isinstance(data, dict):
        raise BadRequest("body must be a JSON object")
    return data


def _str(data: dict, key: str, *, required: bool, max_len: int = 200) -> str | None:
    if key not in data:
        if required:
            raise BadRequest(f"{key} is required")
        return None
    v = data[key]
    if not isinstance(v, str):
        raise BadRequest(f"{key} must be a string")
    v = v.strip()
    if len(v) > max_len:
        raise BadRequest(f"{key} is too long")
    return v


def _time(data: dict, key: str, *, required: bool) -> str | None:
    v = _str(data, key, required=required, max_len=20)
    if v is not None and not ISO_RE.match(v):
        raise BadRequest(f"{key} must be an ISO-8601 UTC time like 2026-01-31T09:00:00Z")
    return v


def _seconds(start: str, end: str | None) -> int:
    fmt = "%Y-%m-%dT%H:%M:%SZ"
    s = datetime.strptime(start, fmt).replace(tzinfo=timezone.utc)
    e = datetime.strptime(end or now_iso(), fmt).replace(tzinfo=timezone.utc)
    return max(0, int((e - s).total_seconds()))


def _range(event) -> tuple[str, str]:
    q = event.get("queryStringParameters") or {}
    start = _time(q, "from", required=True)
    end = _time(q, "to", required=True)
    if end < start:
        raise BadRequest("to must not be before from")
    return start, end


# ---- routes ---------------------------------------------------------------


def list_projects(user, event, params):
    return 200, repo().list_projects(user)


def create_project(user, event, params):
    data = _body(event)
    name = _str(data, "name", required=True, max_len=80)
    if not name:
        raise BadRequest("name must not be empty")
    color = _str(data, "color", required=False, max_len=7) or "#4f7cff"
    if not COLOR_RE.match(color):
        raise BadRequest("color must look like #aabbcc")
    return 201, repo().create_project(user, name, color)


def update_project(user, event, params):
    data = _body(event)
    changes = {}
    if "name" in data:
        name = _str(data, "name", required=True, max_len=80)
        if not name:
            raise BadRequest("name must not be empty")
        changes["name"] = name
    if "color" in data:
        color = _str(data, "color", required=True, max_len=7)
        if not COLOR_RE.match(color):
            raise BadRequest("color must look like #aabbcc")
        changes["color"] = color
    if "archived" in data:
        if not isinstance(data["archived"], bool):
            raise BadRequest("archived must be a boolean")
        changes["archived"] = data["archived"]
    return 200, repo().update_project(user, params["id"], changes)


def delete_project(user, event, params):
    repo().delete_project(user, params["id"])
    return 204, None


def list_entries(user, event, params):
    start, end = _range(event)
    entries = repo().list_entries(user, start, end)
    for e in entries:
        e["seconds"] = _seconds(e["startedAt"], e.get("endedAt"))
    return 200, entries


def create_entry(user, event, params):
    data = _body(event)
    project_id = _str(data, "projectId", required=True, max_len=64)
    description = _str(data, "description", required=False, max_len=500) or ""
    started = _time(data, "startedAt", required=True)
    ended = _time(data, "endedAt", required=True)
    if ended < started:
        raise BadRequest("endedAt must not be before startedAt")
    entry = repo().create_entry(user, project_id, description, started, ended)
    entry["seconds"] = _seconds(started, ended)
    return 201, entry


def update_entry(user, event, params):
    data = _body(event)
    current = repo().get_entry(user, params["id"])
    changes = {}
    if "projectId" in data:
        changes["projectId"] = _str(data, "projectId", required=True, max_len=64)
    if "description" in data:
        changes["description"] = _str(data, "description", required=True, max_len=500)
    if "startedAt" in data:
        changes["startedAt"] = _time(data, "startedAt", required=True)
    if "endedAt" in data:
        if current.get("endedAt") is None:
            raise BadRequest("stop the running timer instead of setting endedAt")
        changes["endedAt"] = _time(data, "endedAt", required=True)
    start = changes.get("startedAt", current["startedAt"])
    end = changes.get("endedAt", current.get("endedAt"))
    if end is not None and end < start:
        raise BadRequest("endedAt must not be before startedAt")
    entry = repo().update_entry(user, params["id"], changes)
    entry["seconds"] = _seconds(entry["startedAt"], entry.get("endedAt"))
    return 200, entry


def delete_entry(user, event, params):
    repo().delete_entry(user, params["id"])
    return 204, None


def get_timer(user, event, params):
    entry = repo().get_timer(user)
    if entry:
        entry["seconds"] = _seconds(entry["startedAt"], None)
    return 200, {"running": entry}


def start_timer(user, event, params):
    data = _body(event)
    project_id = _str(data, "projectId", required=True, max_len=64)
    description = _str(data, "description", required=False, max_len=500) or ""
    entry = repo().start_timer(user, project_id, description)
    entry["seconds"] = 0
    return 201, {"running": entry}


def stop_timer(user, event, params):
    entry = repo().stop_timer(user)
    entry["seconds"] = _seconds(entry["startedAt"], entry["endedAt"])
    return 200, entry


def summary(user, event, params):
    start, end = _range(event)
    by_project: dict[str, int] = defaultdict(int)
    by_day: dict[str, int] = defaultdict(int)
    for e in repo().list_entries(user, start, end):
        secs = _seconds(e["startedAt"], e.get("endedAt"))
        by_project[e["projectId"]] += secs
        by_day[e["startedAt"][:10]] += secs
    return 200, {
        "totalSeconds": sum(by_project.values()),
        "byProject": [{"projectId": k, "seconds": v} for k, v in sorted(by_project.items())],
        "byDay": [{"date": k, "seconds": v} for k, v in sorted(by_day.items())],
    }


ROUTES = {
    "GET /projects": list_projects,
    "POST /projects": create_project,
    "PATCH /projects/{id}": update_project,
    "DELETE /projects/{id}": delete_project,
    "GET /entries": list_entries,
    "POST /entries": create_entry,
    "PATCH /entries/{id}": update_entry,
    "DELETE /entries/{id}": delete_entry,
    "GET /timer": get_timer,
    "POST /timer/start": start_timer,
    "POST /timer/stop": stop_timer,
    "GET /reports/summary": summary,
}


def lambda_handler(event, context):
    route = ROUTES.get(event.get("routeKey", ""))
    if route is None:
        return _resp(404, {"error": "route not found"})
    try:
        user = event["requestContext"]["authorizer"]["jwt"]["claims"]["sub"]
    except (KeyError, TypeError):
        return _resp(401, {"error": "unauthorized"})
    try:
        status, body = route(user, event, event.get("pathParameters") or {})
    except BadRequest as e:
        return _resp(400, {"error": str(e)})
    except NotFound as e:
        return _resp(404, {"error": str(e)})
    except Conflict as e:
        return _resp(409, {"error": str(e)})
    return _resp(status, body)

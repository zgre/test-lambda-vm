"""DynamoDB access layer.

Single-table design, partitioned by user:

    PK = USER#<sub>   SK = PROJECT#<id>   -> project
    PK = USER#<sub>   SK = ENTRY#<id>     -> time entry (has startedAt, indexed by LSI "byStart")
    PK = USER#<sub>   SK = TIMER          -> pointer to the currently running entry
"""

import os
import uuid
from datetime import datetime, timezone

import boto3
from boto3.dynamodb.conditions import Key
from botocore.exceptions import ClientError

BY_START_INDEX = "byStart"


class NotFound(Exception):
    pass


class Conflict(Exception):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_id() -> str:
    return uuid.uuid4().hex


def _pk(user: str) -> str:
    return f"USER#{user}"


def _strip(item: dict) -> dict:
    return {k: v for k, v in item.items() if k not in ("PK", "SK")}


class Repo:
    def __init__(self, table_name: str | None = None):
        name = table_name or os.environ["TABLE_NAME"]
        self.table = boto3.resource("dynamodb").Table(name)
        # The resource's client (de)serializes Python values for us.
        self.client = self.table.meta.client

    # ---- projects -------------------------------------------------------

    def list_projects(self, user: str) -> list[dict]:
        items = self._query(
            KeyConditionExpression=Key("PK").eq(_pk(user)) & Key("SK").begins_with("PROJECT#")
        )
        return sorted((_strip(i) for i in items), key=lambda p: p["name"].lower())

    def get_project(self, user: str, project_id: str) -> dict:
        item = self.table.get_item(Key={"PK": _pk(user), "SK": f"PROJECT#{project_id}"}).get("Item")
        if not item:
            raise NotFound("project not found")
        return _strip(item)

    def create_project(self, user: str, name: str, color: str) -> dict:
        project = {
            "id": _new_id(),
            "name": name,
            "color": color,
            "archived": False,
            "createdAt": now_iso(),
        }
        self.table.put_item(Item={"PK": _pk(user), "SK": f"PROJECT#{project['id']}", **project})
        return project

    def update_project(self, user: str, project_id: str, changes: dict) -> dict:
        return self._update(user, f"PROJECT#{project_id}", changes, "project not found")

    def delete_project(self, user: str, project_id: str) -> None:
        if any(e["projectId"] == project_id for e in self._all_entries(user)):
            raise Conflict("project has time entries; archive it instead")
        self._delete(user, f"PROJECT#{project_id}", "project not found")

    # ---- entries --------------------------------------------------------

    def list_entries(self, user: str, start_from: str, start_to: str) -> list[dict]:
        items = self._query(
            IndexName=BY_START_INDEX,
            KeyConditionExpression=Key("PK").eq(_pk(user))
            & Key("startedAt").between(start_from, start_to),
            ScanIndexForward=False,
        )
        return [_strip(i) for i in items]

    def create_entry(
        self, user: str, project_id: str, description: str, started_at: str, ended_at: str | None
    ) -> dict:
        self.get_project(user, project_id)
        entry = {
            "id": _new_id(),
            "projectId": project_id,
            "description": description,
            "startedAt": started_at,
            "endedAt": ended_at,
        }
        self.table.put_item(Item={"PK": _pk(user), "SK": f"ENTRY#{entry['id']}", **entry})
        return entry

    def get_entry(self, user: str, entry_id: str) -> dict:
        item = self.table.get_item(Key={"PK": _pk(user), "SK": f"ENTRY#{entry_id}"}).get("Item")
        if not item:
            raise NotFound("entry not found")
        return _strip(item)

    def update_entry(self, user: str, entry_id: str, changes: dict) -> dict:
        if "projectId" in changes:
            self.get_project(user, changes["projectId"])
        return self._update(user, f"ENTRY#{entry_id}", changes, "entry not found")

    def delete_entry(self, user: str, entry_id: str) -> None:
        timer = self.get_timer(user)
        if timer and timer["id"] == entry_id:
            self.stop_timer(user)
        self._delete(user, f"ENTRY#{entry_id}", "entry not found")

    # ---- timer ----------------------------------------------------------

    def get_timer(self, user: str) -> dict | None:
        pointer = self.table.get_item(Key={"PK": _pk(user), "SK": "TIMER"}).get("Item")
        if not pointer:
            return None
        try:
            return self.get_entry(user, pointer["entryId"])
        except NotFound:
            return None

    def start_timer(self, user: str, project_id: str, description: str) -> dict:
        self.get_project(user, project_id)
        if self.get_timer(user):
            self.stop_timer(user)
        entry = {
            "id": _new_id(),
            "projectId": project_id,
            "description": description,
            "startedAt": now_iso(),
            "endedAt": None,
        }
        try:
            self.client.transact_write_items(
                TransactItems=[
                    {"Put": {
                        "TableName": self.table.name,
                        "Item": {"PK": _pk(user), "SK": f"ENTRY#{entry['id']}", **entry},
                    }},
                    {"Put": {
                        "TableName": self.table.name,
                        "Item": {"PK": _pk(user), "SK": "TIMER", "entryId": entry["id"]},
                        # Guards against two concurrent starts both succeeding.
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }},
                ]
            )
        except ClientError as e:
            reasons = [r.get("Code") for r in e.response.get("CancellationReasons", [])]
            if "ConditionalCheckFailed" in reasons:
                raise Conflict("a timer was started concurrently") from e
            raise
        return entry

    def stop_timer(self, user: str) -> dict:
        entry = self.get_timer(user)
        if not entry:
            self.table.delete_item(Key={"PK": _pk(user), "SK": "TIMER"})
            raise NotFound("no timer running")
        ended = max(now_iso(), entry["startedAt"])
        entry = self._update(user, f"ENTRY#{entry['id']}", {"endedAt": ended}, "entry not found")
        self.table.delete_item(Key={"PK": _pk(user), "SK": "TIMER"})
        return entry

    # ---- helpers --------------------------------------------------------

    def _all_entries(self, user: str) -> list[dict]:
        return self._query(
            KeyConditionExpression=Key("PK").eq(_pk(user)) & Key("SK").begins_with("ENTRY#")
        )

    def _query(self, **kwargs) -> list[dict]:
        items, start_key = [], None
        while True:
            if start_key:
                kwargs["ExclusiveStartKey"] = start_key
            resp = self.table.query(**kwargs)
            items.extend(resp["Items"])
            start_key = resp.get("LastEvaluatedKey")
            if not start_key:
                return items

    def _update(self, user: str, sk: str, changes: dict, missing_msg: str) -> dict:
        if not changes:
            item = self.table.get_item(Key={"PK": _pk(user), "SK": sk}).get("Item")
            if not item:
                raise NotFound(missing_msg)
            return _strip(item)
        names = {f"#f{i}": k for i, k in enumerate(changes)}
        values = {f":v{i}": v for i, v in enumerate(changes.values())}
        expr = "SET " + ", ".join(f"#f{i} = :v{i}" for i in range(len(changes)))
        try:
            resp = self.table.update_item(
                Key={"PK": _pk(user), "SK": sk},
                UpdateExpression=expr,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ConditionExpression="attribute_exists(PK)",
                ReturnValues="ALL_NEW",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise NotFound(missing_msg) from e
            raise
        return _strip(resp["Attributes"])

    def _delete(self, user: str, sk: str, missing_msg: str) -> None:
        try:
            self.table.delete_item(
                Key={"PK": _pk(user), "SK": sk}, ConditionExpression="attribute_exists(PK)"
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise NotFound(missing_msg) from e
            raise

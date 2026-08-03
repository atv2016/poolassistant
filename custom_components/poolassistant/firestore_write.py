"""Helpers for encoding plain Python values back into Firestore REST 'Value' objects."""
from __future__ import annotations
import time
import uuid
from typing import Any


def to_firestore_value(value: Any) -> dict:
    """Convert a plain Python value into a Firestore REST 'Value' object."""
    if value is None:
        return {"nullValue": None}
    if isinstance(value, bool):
        return {"booleanValue": value}
    if isinstance(value, int):
        return {"integerValue": str(value)}
    if isinstance(value, float):
        return {"doubleValue": value}
    if isinstance(value, str):
        return {"stringValue": value}
    if isinstance(value, dict):
        return {"mapValue": {"fields": {k: to_firestore_value(v) for k, v in value.items()}}}
    if isinstance(value, (list, tuple)):
        return {"arrayValue": {"values": [to_firestore_value(v) for v in value]}}
    raise TypeError(f"Cannot convert {type(value)!r} to a Firestore value")


def now_ms() -> int:
    """Current time as epoch milliseconds, matching the app's 'time' fields."""
    return int(time.time() * 1000)


def new_id() -> str:
    """A UUID4 string, matching the app's resultId/uniqueID format for manual entries."""
    return str(uuid.uuid4())


async def async_create_pool_document(session, firebase, project_id: str, fields: dict) -> str:
    """Create a new pool document in Firestore. Returns the new pool_id.

    Shared by the create_pool service and the config flow's "create a new
    pool" step, so both go through the exact same write.
    """
    pool_id = new_id()
    token = await firebase.get_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    document = (
        f"projects/{project_id}/databases/(default)/documents/"
        f"users/{firebase.local_id}/allPools/{pool_id}"
    )
    url = (
        f"https://firestore.googleapis.com/v1/projects/{project_id}"
        "/databases/(default)/documents:commit"
    )
    body = {
        "writes": [
            {
                "update": {
                    "name": document,
                    "fields": {
                        k: to_firestore_value(v)
                        for k, v in {**fields, "uniqueID": pool_id}.items()
                    },
                }
            }
        ]
    }
    async with session.post(url, headers=headers, json=body) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"Pool Assistant create_pool failed: HTTP {resp.status}: {text}")
    return pool_id


async def async_list_pools(session, project_id: str, firebase) -> list[dict]:
    """List every pool under this Firebase account. Returns a list of
    {"pool_id": ..., "name": ...} dicts - used both by the config flow's
    pool picker, and by the coordinator's ongoing check for pools created
    directly in the app that HA doesn't have an entry for yet."""
    token = await firebase.get_token()
    headers = {"Authorization": f"Bearer {token}"}
    url = (
        f"https://firestore.googleapis.com/v1/projects/{project_id}"
        f"/databases/(default)/documents/users/{firebase.local_id}/allPools"
    )
    async with session.get(url, headers=headers) as resp:
        if resp.status != 200:
            text = await resp.text()
            raise RuntimeError(f"Firestore returned {resp.status}: {text}")
        data = await resp.json()

    pools = []
    for doc in data.get("documents", []):
        pool_id = doc["name"].rsplit("/", 1)[-1]
        fields = doc.get("fields", {})
        name = fields.get("name", {}).get("stringValue", pool_id)
        pools.append({"pool_id": pool_id, "name": name})
    return pools

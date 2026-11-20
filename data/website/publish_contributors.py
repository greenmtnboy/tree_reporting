#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["google-cloud-firestore>=2.20,<3"]
# ///
"""Publish the daily public contributor counts to arborary.world."""

import json
import os
from collections import defaultdict
from datetime import datetime, timezone

from publish_homepage import BUCKET, HmacClient, PUBLIC_HOST

OBJECT = "arborary/website/v1/contributors.json"
PROJECT = "sf-tree-reporting-prod"


def trees_by_user(records):
    result = defaultdict(set)
    for record in records:
        uid, tree = record.get("userId"), record.get("treeId")
        if isinstance(uid, str) and uid.strip() and isinstance(tree, str) and tree.strip():
            result[uid].add(tree)
    return result


def public_name(preference):
    """The name a contributor chose to show, or None without explicit consent."""
    name = preference.get("publicName")
    if preference.get("shareName") is not True or not isinstance(name, str):
        return None
    name = name.strip()
    return name if 0 < len(name) <= 60 else None


def contributor_feed(checkins, published_modifications, preferences):
    """Named contributors get a row each; everyone else is one combined total.

    An anonymous row of its own would still be a fingerprint: its counts are
    unique near the top, move by one with each public tree check-in count, and
    keep the numbers a withdrawn name had the day before. So contributors who
    have not chosen a name only add to `anonymous`, whose counts are distinct
    trees across the whole group.
    """
    visits = trees_by_user(checkins)
    updates = trees_by_user(published_modifications)
    rows = []
    anonymous = {"contributors": 0, "checked_in": set(), "updated": set()}
    for uid in visits.keys() | updates.keys():
        name = public_name(preferences.get(uid, {}))
        if name is None:
            anonymous["contributors"] += 1
            anonymous["checked_in"] |= visits[uid]
            anonymous["updated"] |= updates[uid]
            continue
        rows.append({
            "name": name,
            "checked_in": len(visits[uid]),
            "updated": len(updates[uid]),
            "trees": len(visits[uid] | updates[uid]),
        })
    rows.sort(key=lambda r: (-r["trees"], -r["checked_in"], -r["updated"], r["name"]))
    return {
        "contributors": rows,
        "anonymous": {
            "contributors": anonymous["contributors"],
            "checked_in": len(anonymous["checked_in"]),
            "updated": len(anonymous["updated"]),
            "trees": len(anonymous["checked_in"] | anonymous["updated"]),
        },
    }


def publish():
    from google.cloud import firestore
    from google.cloud.firestore_v1.base_query import FieldFilter
    from google.oauth2 import service_account

    storage = HmacClient.from_env()
    if storage is None:
        raise RuntimeError("The contributor publisher requires the website GCS HMAC credentials")
    generation = storage.generations(BUCKET, OBJECT).get(OBJECT, 0)
    # The dedicated reader is required: falling back to ambient credentials
    # would read Firestore as whatever identity the executor happens to have.
    # Only the emulator, which ignores credentials, runs without it.
    reader = os.environ.get("CONTRIBUTOR_FIRESTORE_READER_JSON")
    if not reader and not os.environ.get("FIRESTORE_EMULATOR_HOST"):
        raise RuntimeError("The contributor publisher requires CONTRIBUTOR_FIRESTORE_READER_JSON")
    credentials = service_account.Credentials.from_service_account_info(json.loads(reader)) if reader else None
    db = firestore.Client(project=PROJECT, credentials=credentials)
    fields = ["userId", "treeId"]
    visits = db.collection("checkins").select(fields)
    updates = db.collection("treeModifications").where(filter=FieldFilter("status", "==", "published")).select(fields)
    preferences = db.collection("contributorPreferences").select(["shareName", "publicName"])
    feed = contributor_feed(
        (doc.to_dict() for doc in visits.stream()),
        (doc.to_dict() for doc in updates.stream()),
        {doc.id: doc.to_dict() for doc in preferences.stream()},
    )
    body = json.dumps({
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        **feed,
    }, ensure_ascii=False).encode("utf-8")
    storage.put(BUCKET, OBJECT, body, if_generation_match=generation, cache_control="no-store")
    print(f"::trilogy-output name=contributor_feed kind=link value={PUBLIC_HOST}/{BUCKET}/{OBJECT}", flush=True)


if __name__ == "__main__":
    publish()

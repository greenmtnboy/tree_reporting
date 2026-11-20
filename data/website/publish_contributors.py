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


def contributor_rows(checkins, published_modifications, preferences):
    visits = trees_by_user(checkins)
    updates = trees_by_user(published_modifications)
    rows = []
    for uid in visits.keys() | updates.keys():
        preference = preferences.get(uid, {})
        name = preference.get("publicName")
        name = name.strip() if preference.get("shareName") is True and isinstance(name, str) else ""
        rows.append({
            "name": name if 0 < len(name) <= 60 else None,
            "checked_in": len(visits[uid]),
            "updated": len(updates[uid]),
            "trees": len(visits[uid] | updates[uid]),
        })
    return sorted(rows, key=lambda r: (-r["trees"], -r["checked_in"], -r["updated"], r["name"] or ""))


def publish():
    from google.cloud import firestore
    from google.cloud.firestore_v1.base_query import FieldFilter
    from google.oauth2 import service_account

    storage = HmacClient.from_env()
    if storage is None:
        raise RuntimeError("The contributor publisher requires the website GCS HMAC credentials")
    generation = storage.generations(BUCKET, OBJECT).get(OBJECT, 0)
    reader = os.environ.get("CONTRIBUTOR_FIRESTORE_READER_JSON")
    credentials = service_account.Credentials.from_service_account_info(json.loads(reader)) if reader else None
    db = firestore.Client(project=PROJECT, credentials=credentials)
    fields = ["userId", "treeId"]
    visits = db.collection("checkins").select(fields)
    updates = db.collection("treeModifications").where(filter=FieldFilter("status", "==", "published")).select(fields)
    preferences = db.collection("contributorPreferences").select(["shareName", "publicName"])
    rows = contributor_rows(
        (doc.to_dict() for doc in visits.stream()),
        (doc.to_dict() for doc in updates.stream()),
        {doc.id: doc.to_dict() for doc in preferences.stream()},
    )
    body = json.dumps({
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "contributors": rows,
    }, ensure_ascii=False).encode("utf-8")
    storage.put(BUCKET, OBJECT, body, if_generation_match=generation, cache_control="no-store")
    print(f"::trilogy-output name=contributor_feed kind=link value={PUBLIC_HOST}/{BUCKET}/{OBJECT}", flush=True)


if __name__ == "__main__":
    publish()

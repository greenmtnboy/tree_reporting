"""Approved tree reports resolve to one correction per tree, the latest winning.

The contract is the reviewer's (reviewer/modifications.ts): the export lists
every approved modification in publish order; a consumer takes the last
non-null value per field, and a later `missing` wins over an earlier `update`.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import community_update_time  # noqa: E402
import tree_modification_info as mods  # noqa: E402


def update(tree_id: str, at: str, **fields):
    return {"treeId": tree_id, "city": "USBOS", "kind": "update", "publishedAt": at,
            "species": None, "dbhInches": None, "latitude": None, "longitude": None, **fields}


def missing(tree_id: str, at: str):
    return {"treeId": tree_id, "city": "USBOS", "kind": "missing", "missingReason": "removed", "publishedAt": at}


def by_tree(records):
    return {row["tree_id"]: row for row in mods.resolve(records)}


def test_each_field_takes_its_latest_correction():
    rows = by_tree([
        update("bos-1", "2026-10-01T00:00:00Z", species="Quercus palustris", dbhInches=10),
        update("bos-1", "2026-10-03T00:00:00Z", dbhInches=12.5),
        update("bos-1", "2026-10-02T00:00:00Z", species="Quercus rubra", latitude=42.31, longitude=-71.1),
    ])
    assert rows["bos-1"] == {
        "tree_id": "bos-1", "species": "Quercus rubra", "diameter_at_breast_height": 12.5,
        "latitude": 42.31, "longitude": -71.1, "missing": False,
    }, "publishedAt orders the reports, not file order; a report that leaves a field alone keeps it"


def test_the_latest_report_decides_whether_the_tree_is_gone():
    rows = by_tree([
        update("bos-1", "2026-10-01T00:00:00Z", species="Acer rubrum"),
        missing("bos-1", "2026-10-02T00:00:00Z"),
        missing("bos-2", "2026-10-01T00:00:00Z"),
        update("bos-2", "2026-10-05T00:00:00Z", dbhInches=8),
    ])
    assert rows["bos-1"]["missing"] is True
    assert rows["bos-1"]["species"] == "Acer rubrum", "a missing report keeps the earlier values, in case it is reversed"
    assert rows["bos-2"]["missing"] is False, "a later update says the tree is there after all"
    assert rows["bos-2"]["diameter_at_breast_height"] == 8


def test_a_proposed_species_is_folded_like_every_other_source():
    rows = by_tree([
        update("a", "1", species="platanus x acerifolia 'Bloodgood'"),
        update("b", "1", species="Pin oak"),
        update("c", "1", species="palm"),
    ])
    assert rows["a"]["species"] == "Platanus x hispanica", "synonyms fold onto the accepted name; the cultivar is not the species"
    assert rows["b"]["species"] is None, "a common name names no taxon, so the source's species is kept, not wiped to Unknown"
    assert rows["c"]["species"] == "Palm", "a growth form is kept as its sentinel"


def test_bad_values_are_ignored_and_a_position_needs_both_halves():
    rows = by_tree([
        update("a", "1", dbhInches=-3, latitude=42.3, longitude=None),
        update("b", "1", dbhInches=900, latitude=91, longitude=-71),
        update("", "1", species="Acer rubrum"),
    ])
    assert rows["a"]["diameter_at_breast_height"] is None and rows["a"]["latitude"] is None
    assert rows["b"]["diameter_at_breast_height"] is None and rows["b"]["latitude"] is None
    assert "" not in rows


def test_no_export_is_zero_rows_with_the_schema(monkeypatch):
    class NotFound:
        status_code = 404

    monkeypatch.setattr(mods, "EXPORT_PATH", None)
    monkeypatch.setattr(mods.requests, "get", lambda *a, **k: NotFound())
    table = mods.records_to_table(mods.load_records())
    assert table.num_rows == 0
    assert table.schema == mods.SCHEMA


def test_a_local_fixture_can_stand_in_for_the_export(tmp_path, monkeypatch):
    fixture = tmp_path / "mods.ndjson"
    fixture.write_text('{"treeId": "x", "kind": "missing", "publishedAt": "1"}\n\n', encoding="utf-8")
    monkeypatch.setattr(mods, "EXPORT_PATH", str(fixture))
    assert mods.records_to_table(mods.load_records()).to_pylist()[0]["missing"] is True


def test_an_approved_report_advances_its_citys_community_freshness(monkeypatch):
    manifests = {
        community_update_time.MANIFEST_URL: {"latestPublishedAtByCity": {"USBOS": "2026-09-01T00:00:00Z", "USSFO": "2026-10-05T00:00:00Z"}},
        community_update_time.MODIFICATIONS_MANIFEST_URL: {"latestPublishedAtByCity": {"USBOS": "2026-10-12T00:00:00Z", "USSFO": "2026-10-01T00:00:00Z"}},
    }

    class Response:
        status_code = 200

        def __init__(self, url):
            self.url = url

        def raise_for_status(self):
            pass

        def json(self):
            return manifests[self.url]

    monkeypatch.setattr(community_update_time.requests, "get", lambda url, timeout: Response(url))
    by_city = community_update_time.fetch_published_at_by_city()
    assert by_city["USBOS"] == datetime(2026, 10, 12, tzinfo=timezone.utc), "the later of the two manifests"
    assert by_city["USSFO"] == datetime(2026, 10, 5, tzinfo=timezone.utc)
    assert by_city["USNYC"] == community_update_time.EMPTY_DATASET_TIMESTAMP


def test_a_missing_modifications_manifest_leaves_community_freshness_alone(monkeypatch):
    class Response:
        def __init__(self, url):
            self.status_code = 404 if "modifications" in url else 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"latestPublishedAtByCity": {"USBOS": "2026-09-01T00:00:00Z"}}

    monkeypatch.setattr(community_update_time.requests, "get", lambda url, timeout: Response(url))
    assert community_update_time.fetch_published_at_by_city()["USBOS"] == datetime(2026, 9, 1, tzinfo=timezone.utc)

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "website"))
from publish_contributors import contributor_rows


def test_unique_credit_and_explicit_consent():
    visits = [
        {"userId": "guest", "treeId": "a"},
        {"userId": "guest", "treeId": "a"},
        {"userId": "named", "treeId": "a"},
    ]
    updates = [
        {"userId": "guest", "treeId": "a"},
        {"userId": "guest", "treeId": "b"},
        {"userId": "fixer", "treeId": "c"},
    ]
    preferences = {
        "named": {"shareName": True, "publicName": "  Juniper  "},
        "fixer": {"shareName": "true", "publicName": "Private"},
        "inactive": {"shareName": True, "publicName": "Inactive"},
    }
    rows = contributor_rows(visits, updates, preferences)
    assert rows == [
        {"name": None, "checked_in": 1, "updated": 2, "trees": 2},
        {"name": "Juniper", "checked_in": 1, "updated": 0, "trees": 1},
        {"name": None, "checked_in": 0, "updated": 1, "trees": 1},
    ]
    del preferences["named"]
    assert all(r["name"] is None for r in contributor_rows(visits, updates, preferences))
    assert contributor_rows([], [], preferences) == []

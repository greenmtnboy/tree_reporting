import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "website"))
from publish_contributors import contributor_feed

VISITS = [
    {"userId": "guest", "treeId": "a"},
    {"userId": "guest", "treeId": "a"},
    {"userId": "named", "treeId": "a"},
    {"userId": "named", "treeId": "d"},
    {"userId": "walker", "treeId": "a"},
    {"userId": "walker", "treeId": "e"},
]
UPDATES = [
    {"userId": "guest", "treeId": "a"},
    {"userId": "guest", "treeId": "b"},
    {"userId": "fixer", "treeId": "c"},
]


def test_named_rows_need_explicit_consent_and_the_rest_are_one_total():
    preferences = {
        "named": {"shareName": True, "publicName": "  Juniper  "},
        "fixer": {"shareName": "true", "publicName": "Private"},
        "walker": {"shareName": True, "publicName": "   "},
        "inactive": {"shareName": True, "publicName": "Inactive"},
    }
    assert contributor_feed(VISITS, UPDATES, preferences) == {
        "contributors": [{"name": "Juniper", "checked_in": 2, "updated": 0, "trees": 2}],
        # guest, fixer and walker: distinct trees across the group, so tree
        # "a" counts once though two of them checked in there.
        "anonymous": {"contributors": 3, "checked_in": 2, "updated": 3, "trees": 4},
    }


def test_no_anonymous_contributor_is_published_on_its_own():
    feed = contributor_feed(VISITS, UPDATES, {})
    assert feed["contributors"] == []
    assert feed["anonymous"] == {"contributors": 4, "checked_in": 3, "updated": 3, "trees": 5}
    # Nothing about an account other than a chosen name leaves the publisher.
    text = json.dumps(feed)
    for uid in ("guest", "named", "walker", "fixer"):
        assert uid not in text


def test_withdrawing_a_name_folds_the_row_into_the_total():
    shared = contributor_feed(VISITS, UPDATES, {"named": {"shareName": True, "publicName": "Juniper"}})
    withdrawn = contributor_feed(VISITS, UPDATES, {})
    assert [r["name"] for r in shared["contributors"]] == ["Juniper"]
    assert withdrawn["contributors"] == []
    assert withdrawn["anonymous"]["contributors"] == shared["anonymous"]["contributors"] + 1


def test_empty():
    assert contributor_feed([], [], {}) == {
        "contributors": [],
        "anonymous": {"contributors": 0, "checked_in": 0, "updated": 0, "trees": 0},
    }

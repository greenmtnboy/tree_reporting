"""Prague's ingest names a dead MapServer as an outage, not as a bad request.

In November 2026 the register's MapServer lost its data source: every query
failed and identify answered `Invalid 'layerDefs' is specified`, which read
like the ingest's filter had broken.  The walk now starts with a count, and
a failed count says the service is down before identify can mislead.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from shared.ingest import UpstreamUnavailable  # noqa: E402


@pytest.fixture(scope="module")
def prg():
    spec = importlib.util.spec_from_file_location(
        "prague_tree_info", RAW_DIR / "czprg" / "prague_tree_info.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_dead_layer_stops_before_the_walk(prg, monkeypatch):
    def dead(layer, *, where="1=1"):
        raise UpstreamUnavailable("returned HTTP 200 with an error payload: 400: Unable to complete operation.")

    def walk(*args, **kwargs):
        raise AssertionError("the identify walk ran against a layer that cannot count")

    monkeypatch.setattr(prg, "feature_count", dead)
    monkeypatch.setattr(prg, "max_record_count", lambda layer: 1000)
    monkeypatch.setattr(prg, "iter_tree_tiles", walk)
    with pytest.raises(UpstreamUnavailable, match="not serving the layer"):
        prg.main()


def test_the_count_asks_for_trees_only(prg, monkeypatch):
    asked = {}

    def count(layer, *, where="1=1"):
        asked["where"] = where
        return 190_015

    monkeypatch.setattr(prg, "feature_count", count)
    assert prg.count_trees() == 190_015
    assert asked["where"] == prg.TREE_DEFINITION

"""Taipei's street and park files overlap; the ingest keeps one row per tree.
Pinned without a network fetch."""

import importlib.util
import sys
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, RAW_DIR / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


taipei = _load("taipei_tree_info", "twtpe/taipei_tree_info.py")


def row(tree_id: str, diameter: str = "20") -> dict:
    return {"TreeID": tree_id, "TreeType": "", "Dist": "文山區",
            "TWD97X": "304344.66", "TWD97Y": "2764620.84", "Diameter": diameter}


def test_a_tree_in_both_files_publishes_once_as_its_street_row(capsys):
    street = taipei.transform([row("WS1030021170", "20"), row("A1")], "street")
    park = taipei.transform([row("WS1030021170", "99"), row("P1")], "park")
    table = taipei.drop_repeated_ids(taipei.pa.concat_tables([street, park]))
    assert table.column("tree_id").to_pylist() == ["tpe-WS1030021170", "tpe-A1", "tpe-P1"]
    assert table.column("diameter_at_breast_height")[0].as_py() == taipei.parse_dbh("20")
    assert "dropped 1 row(s) repeating a tree_id" in capsys.readouterr().err


def test_no_repeats_is_untouched(capsys):
    table = taipei.transform([row("A1"), row("A2")], "street")
    assert taipei.drop_repeated_ids(table).num_rows == 2
    assert "repeating" not in capsys.readouterr().err

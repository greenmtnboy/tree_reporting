"""Bulk imagery predictions (`PREDICTED_{code}`): the registry, each wired
city's model, and the ingest's mapping of the staged file.

What the merge does with the rows is test_predicted_reconciliation.py.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from shared.ingest import (  # noqa: E402
    DATA_SOURCES,
    MUNICIPAL_DATA_SOURCES,
    PREDICTED_DATA_SOURCES,
    predicted_source_for,
)
from test_data_sources import city_models, enum_values  # noqa: E402


def test_every_predicted_city_is_a_city():
    assert set(PREDICTED_DATA_SOURCES) <= set(MUNICIPAL_DATA_SOURCES)
    for code, label in PREDICTED_DATA_SOURCES.items():
        assert label == predicted_source_for(code) == f"PREDICTED_{code}"
        assert label in DATA_SOURCES


@pytest.mark.parametrize("code", sorted(PREDICTED_DATA_SOURCES))
def test_predicted_city_is_fully_wired(code: str):
    lower = code.lower()
    text = city_models()[code].read_text(encoding="utf-8")
    label = PREDICTED_DATA_SOURCES[code]
    assert label in enum_values(city_models()[code])
    assert f"complete where city = '{code}' and {lower}_source = '{label}'" in text
    assert re.search(
        rf"file `\.\./predicted_tree_info\.py`\s*\nwhere city = '{code}';", text
    ), f"{code}'s predicted datasource has no `where city = '{code}'`"
    column = f"{lower}_predicted_data_updated_through"
    assert f"property <*>.{column} datetime;" in (RAW_DIR / "tree_common.preql").read_text(encoding="utf-8")
    assert f"{column}: {column}" in text, f"{code} does not probe its own predictions column"
    assert "file `../predicted_update_time.py`" in text
    assert column in text.split("greatest(", 1)[1].split(")", 1)[0], (
        f"{code}'s published watermark must include {column} or an uploaded "
        "prediction run never rebuilds the Parquet"
    )


@pytest.mark.parametrize("code", sorted(set(MUNICIPAL_DATA_SOURCES) - set(PREDICTED_DATA_SOURCES)))
def test_a_city_without_predictions_declares_no_predicted_partition(code: str):
    assert "PREDICTED_" not in city_models()[code].read_text(encoding="utf-8")


def test_probe_emits_one_column_per_wired_city(monkeypatch):
    import predicted_update_time
    from datetime import datetime, timezone

    stamp = datetime(2026, 10, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(predicted_update_time, "staging_modified_at", lambda name: stamp)
    by_city = predicted_update_time.fetch_modified_by_city()
    assert {predicted_update_time.column_for(c) for c in by_city} == {
        f"{c.lower()}_predicted_data_updated_through" for c in PREDICTED_DATA_SOURCES
    }
    assert set(by_city.values()) == {stamp}


def test_the_shared_merge_classes_the_predicted_partition():
    """Without its own class a prediction reads as municipal: it owns its
    cluster, absorbs the city's real inventory row, and its genus wins."""
    text = (RAW_DIR / "tree_dedup.preql").read_text(encoding="utf-8")
    assert "= 'PREDICTED_' then 'predicted'" in text
    # A prediction never anchors anything.
    assert "source_class != 'osm' and source_class != 'predicted'" in text
    assert "predicted_osm_anchor" in text.split("auto cluster_id <-", 1)[1]


def record(**overrides):
    base = {
        "tree_id": "pred-1", "city": "USBOS", "latitude": 42.33, "longitude": -71.08,
        "genus": "Acer", "species": "Acer rubrum", "score": 0.4, "crown_width_m": 5.5,
        "run_id": "run", "imagery_version": "naip2023",
        "species_confidence": 0.85, "genus_confidence": 0.9,
    }
    return {**base, **overrides}


def test_ingest_publishes_a_confident_species_and_never_a_diameter():
    from predicted_tree_info import records_to_table

    row = records_to_table([record()], "USBOS").to_pylist()[0]
    assert row["data_source"] == "PREDICTED_USBOS"
    assert row["species"] == "Acer rubrum"
    assert row["diameter_at_breast_height"] is None
    assert row["crown_width_m"] == 5.5


def test_ingest_names_only_what_the_model_is_sure_of():
    """Species at NAME_CONFIDENCE or more, else a confident genus, else Unknown;
    the threshold is inclusive, and a file without confidences names nothing."""
    from predicted_tree_info import NAME_CONFIDENCE, records_to_table

    rows = records_to_table(
        [
            record(tree_id="pred-1", species_confidence=NAME_CONFIDENCE),
            record(tree_id="pred-2", species_confidence=0.5, genus_confidence=0.95),
            record(tree_id="pred-3", species_confidence=0.5, genus_confidence=0.5),
            record(tree_id="pred-4", species=None, genus_confidence=0.9),
            record(tree_id="pred-5", species_confidence=None, genus_confidence=None),
            record(tree_id="pred-6", species="quercus palustris", genus=None, species_confidence=0.3),
        ],
        "USBOS",
    ).to_pylist()
    assert [r["species"] for r in rows] == ["Acer rubrum", "Acer", "Unknown", "Acer", "Unknown", "Unknown"]


def test_ingest_skips_rows_it_cannot_trust():
    from predicted_tree_info import records_to_table

    rows = records_to_table(
        [
            record(tree_id="sat-1"),               # not a prediction id
            record(tree_id="pred-2", city="USNYC"),
            record(tree_id="pred-3", latitude=40.7),  # outside Boston
            record(tree_id="pred-4", latitude=None),
            record(tree_id="pred-5"),
        ],
        "USBOS",
    ).to_pylist()
    assert [r["tree_id"] for r in rows] == ["pred-5"]


def test_a_missing_staged_file_is_no_rows(tmp_path, monkeypatch):
    from predicted_tree_info import load_staged

    monkeypatch.setenv("PREDICTED_STAGING_URL", str(tmp_path / "{code}.parquet"))
    assert load_staged("USBOS") == []

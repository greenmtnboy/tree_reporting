"""crown_width_m: the OSM tag parser, Tokyo's column and the shared range guard."""

import sys
from pathlib import Path

import pyarrow as pa
import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from jptyo.tokyo_tree_info import parse_crown_width  # noqa: E402
from shared.ingest import CROWN_WIDTH_MAX_M, enforce_tree_schema  # noqa: E402
from shared.osm import crown_diameter_tag_to_m  # noqa: E402


@pytest.mark.parametrize(
    ("tag", "metres"),
    [
        ("5", 5.0),
        ("2.412m", 2.412),
        ("2 m", 2.0),
        ("2,21 m", 2.21),
        ("350 cm", 3.5),
        ("450", 4.5),  # a bare value no crown reaches is centimetres
        ("0", None),
        ("wide", None),
        (None, None),
    ],
)
def test_osm_diameter_crown_is_read_in_metres(tag, metres):
    assert crown_diameter_tag_to_m(tag) == (pytest.approx(metres) if metres else None)


def test_tokyo_crown_spread_blank_is_unmeasured():
    assert parse_crown_width("2.5") == 2.5
    assert parse_crown_width("") is None
    assert parse_crown_width(None) is None


def test_implausible_crowns_are_nulled_with_a_count(capsys):
    table = pa.table(
        {
            "tree_id": ["a", "b", "c", "d"],
            "city": ["JPTYO"] * 4,
            "data_source": ["TOKYO_METRO"] * 4,
            "species": ["Zelkova serrata"] * 4,
            "crown_width_m": pa.array([6.0, 0.0, 130.0, None], type=pa.float64()),
        }
    )
    out = enforce_tree_schema(table, city="Tokyo")
    assert out.column("crown_width_m").to_pylist() == [6.0, None, None, None]
    assert f"over {CROWN_WIDTH_MAX_M:.0f} m" in capsys.readouterr().err


def test_a_source_without_crowns_emits_a_typed_null_column():
    table = pa.table(
        {
            "tree_id": ["a"],
            "city": ["USSFO"],
            "data_source": ["SF_OPENDATA"],
            "species": ["Platanus x hispanica"],
        }
    )
    out = enforce_tree_schema(table)
    assert out.schema.field("crown_width_m").type.equals(pa.float64())
    assert out.column("crown_width_m").to_pylist() == [None]

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy", "pytest"]
# ///

"""Tests for _ingest_shared.py helper module.

Run with:
    pytest data/raw/tests/test_ingest_shared.py
or standalone:
    uv run data/raw/tests/test_ingest_shared.py
"""

import io
import struct
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa
import pytest

# Allow import from data/raw/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _ingest_shared import (
    TREE_COLUMN_TYPES,
    circumference_cm_to_dbh_inches,
    cm_to_inches,
    emit,
    enforce_tree_schema,
    make_point_wkt,
    normalize_species,
    normalize_species_parts,
    parse_plant_date_year,
    parse_wkb_point,
    rd_centroid,
    rd_to_wgs84,
    validate_coordinates,
)


# ---------------------------------------------------------------------------
# normalize_species
# ---------------------------------------------------------------------------

class TestNormalizeSpecies:
    def test_none(self):
        assert normalize_species(None) is None

    def test_empty_string(self):
        assert normalize_species("") is None

    def test_whitespace_only(self):
        assert normalize_species("   ") is None

    def test_single_word(self):
        assert normalize_species("quercus") == "Quercus"

    def test_binomial(self):
        assert normalize_species("platanus x hispanica") == "Platanus x hispanica"

    def test_binomial_already_mixed_case(self):
        assert normalize_species("PLATANUS X HISPANICA") == "Platanus x hispanica"

    def test_strip_double_colon_suffix(self):
        assert normalize_species("Platanus x hispanica :: London Plane") == "Platanus x hispanica"

    def test_strip_dash_suffix(self):
        assert normalize_species("Quercus robur - English Oak") == "Quercus robur"

    def test_strip_double_colon_no_space(self):
        # Suffix starts right after "::" with no leading space — split still strips
        result = normalize_species("Acer platanoides::Norway Maple")
        assert result == "Acer platanoides"

    def test_mixed_case_binomial(self):
        assert normalize_species("ACER PLATANOIDES") == "Acer platanoides"

    def test_preserves_cultivar_in_name(self):
        # We don't strip cultivar notation — that's left to individual scripts
        result = normalize_species("Betula pendula 'youngii'")
        assert result == "Betula pendula 'youngii'"


# ---------------------------------------------------------------------------
# normalize_species_parts
# ---------------------------------------------------------------------------

class TestNormalizeSpeciesParts:
    def test_none_genus(self):
        assert normalize_species_parts(None, "robur") is None

    def test_empty_genus(self):
        assert normalize_species_parts("", "robur") is None

    def test_none_epithet(self):
        assert normalize_species_parts("Quercus", None) == "Quercus"

    def test_empty_epithet(self):
        assert normalize_species_parts("Quercus", "") == "Quercus"

    def test_both_present(self):
        assert normalize_species_parts("Platanus", "hispanica") == "Platanus hispanica"

    def test_uppercase_genus(self):
        assert normalize_species_parts("PLATANUS", "HISPANICA") == "Platanus hispanica"

    def test_whitespace_stripping(self):
        assert normalize_species_parts("  Acer  ", "  platanoides  ") == "Acer platanoides"


# ---------------------------------------------------------------------------
# parse_wkb_point
# ---------------------------------------------------------------------------

def _make_wkb_point(lon: float, lat: float) -> bytes:
    """Build a little-endian WKB Point blob."""
    return struct.pack("<BIdd", 1, 1, lon, lat)


class TestParseWkbPoint:
    def test_valid_little_endian(self):
        lon, lat = 4.9, 52.37
        wkb = _make_wkb_point(lon, lat)
        result_lon, result_lat = parse_wkb_point(wkb)
        assert abs(result_lon - lon) < 1e-9
        assert abs(result_lat - lat) < 1e-9

    def test_none_returns_none(self):
        lon, lat = parse_wkb_point(None)
        assert lon is None
        assert lat is None

    def test_too_short_returns_none(self):
        # 20 bytes is one short of the minimum 21
        lon, lat = parse_wkb_point(b"\x01" * 20)
        assert lon is None
        assert lat is None

    def test_empty_bytes_returns_none(self):
        lon, lat = parse_wkb_point(b"")
        assert lon is None
        assert lat is None

    def test_big_endian(self):
        # byte 0 = 0 → big-endian
        lon_val, lat_val = -0.1276, 51.5074
        wkb = struct.pack(">BIdd", 0, 1, lon_val, lat_val)
        result_lon, result_lat = parse_wkb_point(wkb)
        assert abs(result_lon - lon_val) < 1e-9
        assert abs(result_lat - lat_val) < 1e-9


# ---------------------------------------------------------------------------
# make_point_wkt
# ---------------------------------------------------------------------------

class TestMakePointWkt:
    def test_both_present(self):
        assert make_point_wkt(4.9, 52.37) == "POINT(4.9 52.37)"

    def test_lon_none(self):
        assert make_point_wkt(None, 52.37) is None

    def test_lat_none(self):
        assert make_point_wkt(4.9, None) is None

    def test_both_none(self):
        assert make_point_wkt(None, None) is None


# ---------------------------------------------------------------------------
# rd_to_wgs84
# ---------------------------------------------------------------------------

class TestRdToWgs84:
    def test_amsterdam_city_center(self):
        # Amsterdam city centre in RD New is approximately (121000, 487000)
        # Expected WGS84: ~52.37°N, 4.90°E
        lat, lon = rd_to_wgs84(121000, 487000)
        assert 52.30 < lat < 52.45, f"lat={lat} out of expected range"
        assert 4.80 < lon < 5.00, f"lon={lon} out of expected range"

    def test_reference_point(self):
        # The polynomial is defined around (155000, 463000) → (52.155..., 5.387...)
        lat, lon = rd_to_wgs84(155000, 463000)
        assert abs(lat - 52.15517440) < 0.01
        assert abs(lon - 5.38720621) < 0.01


# ---------------------------------------------------------------------------
# rd_centroid
# ---------------------------------------------------------------------------

class TestRdCentroid:
    def test_simple_square(self):
        # A square with corners at (0,0), (4,0), (4,4), (0,4)
        ring = [(0, 0), (4, 0), (4, 4), (0, 4)]
        cx, cy = rd_centroid(ring)
        assert cx == pytest.approx(2.0)
        assert cy == pytest.approx(2.0)

    def test_single_point(self):
        ring = [(100000, 450000)]
        cx, cy = rd_centroid(ring)
        assert cx == 100000
        assert cy == 450000


# ---------------------------------------------------------------------------
# parse_plant_date_year
# ---------------------------------------------------------------------------

class TestParsePlantDateYear:
    def test_valid_int(self):
        assert parse_plant_date_year(2010) == date(2010, 1, 1)

    def test_valid_string(self):
        assert parse_plant_date_year("1985") == date(1985, 1, 1)

    def test_zero(self):
        assert parse_plant_date_year(0) is None

    def test_none(self):
        assert parse_plant_date_year(None) is None

    def test_negative(self):
        assert parse_plant_date_year(-5) is None

    def test_out_of_range_high(self):
        assert parse_plant_date_year(2200) is None

    def test_boundary_2100(self):
        assert parse_plant_date_year(2100) == date(2100, 1, 1)

    def test_non_numeric_string(self):
        assert parse_plant_date_year("not-a-year") is None

    def test_float_string(self):
        # int("1990.0") raises ValueError, so None
        assert parse_plant_date_year("1990.0") is None


# ---------------------------------------------------------------------------
# circumference_cm_to_dbh_inches
# ---------------------------------------------------------------------------

class TestCircumferenceCmToDbhInches:
    def test_known_value(self):
        import math
        circ_cm = 100.0
        expected = circ_cm / (math.pi * 2.54)
        assert circumference_cm_to_dbh_inches(circ_cm) == pytest.approx(expected)

    def test_zero(self):
        assert circumference_cm_to_dbh_inches(0) is None

    def test_none(self):
        assert circumference_cm_to_dbh_inches(None) is None

    def test_string_number(self):
        import math
        result = circumference_cm_to_dbh_inches("50")
        assert result == pytest.approx(50 / (math.pi * 2.54))


# ---------------------------------------------------------------------------
# cm_to_inches
# ---------------------------------------------------------------------------

class TestCmToInches:
    def test_known_value(self):
        assert cm_to_inches(25.4) == pytest.approx(10.0)

    def test_none(self):
        assert cm_to_inches(None) is None

    def test_zero(self):
        assert cm_to_inches(0) == pytest.approx(0.0)

    def test_string_number(self):
        assert cm_to_inches("2.54") == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# validate_coordinates
# ---------------------------------------------------------------------------

class TestValidateCoordinates:
    def _good_table(self, n: int = 10) -> pa.Table:
        return pa.table({
            "latitude": pa.array([52.37] * n, type=pa.float64()),
            "longitude": pa.array([4.90] * n, type=pa.float64()),
        })

    def test_passes_with_good_table(self):
        # Should not raise
        validate_coordinates(self._good_table())

    def test_fails_on_zero_rows(self):
        table = pa.table({
            "latitude": pa.array([], type=pa.float64()),
            "longitude": pa.array([], type=pa.float64()),
        })
        with pytest.raises(ValueError, match="0 rows"):
            validate_coordinates(table, city="Test")

    def test_fails_on_high_null_pct(self):
        # 5 out of 10 rows null → 50% > default 10% threshold
        lats = [52.37] * 5 + [None] * 5
        lons = [4.90] * 5 + [None] * 5
        table = pa.table({
            "latitude": pa.array(lats, type=pa.float64()),
            "longitude": pa.array(lons, type=pa.float64()),
        })
        with pytest.raises(ValueError, match="50%"):
            validate_coordinates(table, city="Test")

    def test_passes_with_custom_threshold(self):
        # 3 out of 10 null = 30% — passes with threshold=0.50
        lats = [52.37] * 7 + [None] * 3
        lons = [4.90] * 7 + [None] * 3
        table = pa.table({
            "latitude": pa.array(lats, type=pa.float64()),
            "longitude": pa.array(lons, type=pa.float64()),
        })
        validate_coordinates(table, city="Test", threshold=0.50)

    def test_fails_when_all_null(self):
        table = pa.table({
            "latitude": pa.array([None] * 5, type=pa.float64()),
            "longitude": pa.array([None] * 5, type=pa.float64()),
        })
        with pytest.raises(ValueError):
            validate_coordinates(table, city="Test")


# ---------------------------------------------------------------------------
# emit
# ---------------------------------------------------------------------------

class TestEmit:
    def test_emits_valid_arrow_ipc(self, monkeypatch):
        """emit() should write a valid Arrow IPC stream to stdout.buffer."""
        buf = io.BytesIO()

        # Temporarily replace sys.stdout.buffer with our BytesIO
        class _FakeStdout:
            buffer = buf

        monkeypatch.setattr(sys, "stdout", _FakeStdout())

        table = pa.table({
            "city": pa.array(["TEST"], type=pa.string()),
            "value": pa.array([42], type=pa.int64()),
        })
        emit(table)

        buf.seek(0)
        reader = pa.ipc.open_stream(buf)
        result = reader.read_all()

        assert result.num_rows == 1
        assert result.column("city")[0].as_py() == "TEST"
        assert result.column("value")[0].as_py() == 42


# ---------------------------------------------------------------------------
# enforce_tree_schema
# ---------------------------------------------------------------------------

def _tree_table(**overrides) -> pa.Table:
    """A minimal canonical tree table; pass kwargs to swap in off-type columns."""
    cols = {
        "tree_id": pa.array(["t-1"], type=pa.string()),
        "city": pa.array(["USSFO"], type=pa.string()),
        "data_source": pa.array(["SF_OPENDATA"], type=pa.string()),
        "species": pa.array(["Platanus x hispanica"], type=pa.string()),
        "tree_name": pa.array(["London Plane"], type=pa.string()),
        "plant_date": pa.array([date(2001, 5, 4)], type=pa.date32()),
        "latitude": pa.array([37.77], type=pa.float64()),
        "longitude": pa.array([-122.42], type=pa.float64()),
        "diameter_at_breast_height": pa.array([12.5], type=pa.float64()),
        "submission_photo_url": pa.array([None], type=pa.string()),
    }
    cols.update(overrides)
    return pa.table(cols)


class TestEnforceTreeSchema:
    def test_canonical_table_is_unchanged(self):
        table = _tree_table()
        assert enforce_tree_schema(table).schema.equals(table.schema)

    def test_all_canonical_columns_have_declared_types(self):
        out = enforce_tree_schema(_tree_table())
        for name, expected in TREE_COLUMN_TYPES.items():
            assert out.schema.field(name).type.equals(expected), name

    def test_int_dbh_is_widened_to_float(self):
        """USNYC/USSFO regression: whole-number dbh inferred as int64 → BIGINT parquet."""
        table = _tree_table(
            diameter_at_breast_height=pa.array([12], type=pa.int64())
        )
        out = enforce_tree_schema(table)
        assert out.schema.field("diameter_at_breast_height").type.equals(pa.float64())
        assert out.column("diameter_at_breast_height")[0].as_py() == 12.0

    def test_null_typed_plant_date_becomes_date32(self):
        """FRPAR regression: pa.null() plant_date materialised as INT32, breaking year()."""
        table = _tree_table(plant_date=pa.array([None], type=pa.null()))
        out = enforce_tree_schema(table)
        assert out.schema.field("plant_date").type.equals(pa.date32())
        assert out.column("plant_date")[0].as_py() is None

    def test_string_dbh_is_parsed(self):
        table = _tree_table(
            diameter_at_breast_height=pa.array(["12.5"], type=pa.string())
        )
        out = enforce_tree_schema(table)
        assert out.column("diameter_at_breast_height")[0].as_py() == 12.5

    def test_column_overrides_map_source_native_names(self):
        table = pa.table({
            "treeid": pa.array(["sf-1"], type=pa.string()),
            "city": pa.array(["USSFO"], type=pa.string()),
            "qspecies": pa.array(["Platanus x hispanica"], type=pa.string()),
            "plantdate": pa.array([None], type=pa.null()),
            "dbh": pa.array([12], type=pa.int64()),
        })
        out = enforce_tree_schema(
            table,
            data_source="SF_OPENDATA",
            columns={
                "tree_id": "treeid",
                "species": "qspecies",
                "plant_date": "plantdate",
                "diameter_at_breast_height": "dbh",
            },
        )
        assert out.schema.field("dbh").type.equals(pa.float64())
        assert out.schema.field("plantdate").type.equals(pa.date32())

    def test_extra_columns_pass_through_untouched(self):
        table = _tree_table().append_column(
            "borough", pa.array(["Camden"], type=pa.string())
        )
        out = enforce_tree_schema(table)
        assert out.column("borough")[0].as_py() == "Camden"

    def test_absent_optional_columns_are_backfilled_as_typed_nulls(self):
        """Every ingest must emit the identical column set.

        The preql datasources map `submission_photo_url: ?submission_photo_url`
        for every city, including the ones with no photos, so the column has to
        exist or the generated SELECT fails on a missing column.
        """
        table = _tree_table().drop_columns(["plant_date", "tree_name"])
        out = enforce_tree_schema(table)
        assert set(TREE_COLUMN_TYPES).issubset(out.schema.names)
        assert out.schema.field("plant_date").type.equals(pa.date32())
        assert out.column("plant_date")[0].as_py() is None
        assert out.column("tree_name")[0].as_py() is None

    def test_backfill_respects_column_overrides(self):
        table = pa.table({
            "treeid": pa.array(["sf-1"], type=pa.string()),
            "city": pa.array(["USSFO"], type=pa.string()),
            "qspecies": pa.array(["Platanus x hispanica"], type=pa.string()),
        })
        out = enforce_tree_schema(
            table,
            data_source="SF_OPENDATA",
            columns={"tree_id": "treeid", "species": "qspecies", "diameter_at_breast_height": "dbh"},
        )
        assert out.schema.field("dbh").type.equals(pa.float64())
        assert "diameter_at_breast_height" not in out.schema.names

    def test_data_source_kwarg_is_validated_against_the_picklist(self):
        with pytest.raises(ValueError, match="not a known source label"):
            enforce_tree_schema(_tree_table(), data_source="NOT_A_REAL_SOURCE")

    def test_data_source_kwarg_overrides_any_existing_column(self):
        out = enforce_tree_schema(_tree_table(), data_source="NYC_OPENDATA")
        assert out.column("data_source").to_pylist() == ["NYC_OPENDATA"]

    def test_missing_required_column_raises(self):
        table = _tree_table().drop_columns(["species"])
        with pytest.raises(ValueError, match="required column 'species'"):
            enforce_tree_schema(table, city="Testville")

    def test_unknown_override_key_raises(self):
        with pytest.raises(ValueError, match="unknown canonical column"):
            enforce_tree_schema(_tree_table(), columns={"dbh": "dbh"})

    def test_lossy_cast_raises_rather_than_truncating(self):
        table = _tree_table(
            diameter_at_breast_height=pa.array(["not-a-number"], type=pa.string())
        )
        with pytest.raises(ValueError, match="cannot be safely cast"):
            enforce_tree_schema(table, city="Testville")


# ---------------------------------------------------------------------------
# Entry point (standalone uv run)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))

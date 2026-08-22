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
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow as pa
import pytest

# Allow import from data/raw/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _ingest_shared import (
    PORTAL_UNAVAILABLE_TIMESTAMP,
    TREE_COLUMN_TYPES,
    UpstreamUnavailable,
    circumference_cm_to_dbh_inches,
    cm_to_inches,
    emit,
    emit_freshness,
    enforce_tree_schema,
    get_json_with_retry,
    response_json,
    make_point_wkt,
    normalize_species,
    normalize_species_parts,
    parse_plant_date_year,
    parse_wkb_point,
    rd_centroid,
    rd_to_wgs84,
    UNKNOWN_SPECIES,
    sanitize_species,
    form_sentinel_for,
    SPECIES_SENTINELS,
    PALM_SPECIES,
    SHRUB_SPECIES,
    CACTUS_SPECIES,
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
# sanitize_species
# ---------------------------------------------------------------------------

class TestSanitizeSpecies:
    """The `species` value is the join key into the enrichment table, so the
    question this answers is not "how is it spelled" but "is it a taxon at
    all".  Anything that is not becomes null."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Acer platanoides", "Acer platanoides"),
            ("Acer", "Acer"),                       # genus alone is a real answer
            ("quercus ROBUR", "Quercus robur"),
            ("Quercus robur - English Oak", "Quercus robur"),
            ("Platanus x hispanica :: London Plane", "Platanus x hispanica"),
        ],
    )
    def test_keeps_scientific_names(self, raw, expected):
        assert sanitize_species(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # Both hybrid spellings survive verbatim.  The enrichment table is
            # keyed on whichever form a city emitted, so normalising one into
            # the other would orphan every already-enriched hybrid.
            ("Platanus x hispanica", "Platanus x hispanica"),
            ("Citrus × limon", "Citrus × limon"),
            ("Tilia × euchlora", "Tilia × euchlora"),
            ("X amelasorbus jackii", "X amelasorbus jackii"),   # nothogenus
        ],
    )
    def test_preserves_hybrid_marks(self, raw, expected):
        assert sanitize_species(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Gleditsia triacanthos var. inermis", "Gleditsia triacanthos"),
            ("Rhododendron degronianum ssp. yakushimanum", "Rhododendron degronianum"),
            ("Lonicera chrysantha forma villosa", "Lonicera chrysantha"),
            ("Viburnum cf. corylifolium", "Viburnum"),
            ("Fagus spp", "Fagus"),
            ("Tilia spec.", "Tilia"),
        ],
    )
    def test_truncates_to_species_rank(self, raw, expected):
        assert sanitize_species(raw) == expected

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Prunus serrulata 'kwanzan'", "Prunus serrulata"),
            # The note trailing a cultivar goes with it, rather than being
            # mistaken for the epithet.
            ("Malus 'spring snow' high brnch", "Malus"),
            ("Arbutus 'marina'", "Arbutus"),
        ],
    )
    def test_drops_cultivars_and_trailing_notes(self, raw, expected):
        assert sanitize_species(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        [
            # Inventory placeholders for an empty or unidentifiable site.
            "Vacant", "Vacant well", "Vacant/ok to replant", "Unknown",
            "Unknown tree", "Unbekannt", "Onbekend", "No identificado",
            "Other", "Dead tree", "Stump stump", "Empty pit/planting site",
            # OSM contributors free-type into the species tag.
            "Pin oak", "Red maple", "Serviceberry or dogwood?",
            "Gymnocladus dioicus espresso or amur maackia",
            # An abbreviated genus cannot be resolved to a real name.
            "Amel. laevis 'spring flurry'",
            # Numeric content is never part of a name.
            "Tree(s) 2",
            None, "", "   ",
        ],
    )
    def test_drops_non_taxa(self, raw):
        assert sanitize_species(raw) is None

    def test_common_name_rule_only_applies_to_the_epithet(self):
        """"Maple" as an epithet means a common name; as a genus it is real."""
        assert sanitize_species("Red maple") is None
        assert sanitize_species("Magnolia grandiflora") == "Magnolia grandiflora"
        assert sanitize_species("Catalpa speciosa") == "Catalpa speciosa"

    @pytest.mark.parametrize(
        "raw",
        [
            # A common name standing alone is genus-shaped and the structural
            # rules cannot see it; _NON_TAXON_REWRITES names them.
            "Oak", "Willow", "Cedar", "Redwood", "Eucalypt", "Greengage",
            # ...including the adjective half of one.
            "Red", "White", "Northern red", "Eastern white", "European horse",
            # ...and the same thing in the languages the wired cities publish
            # in.  Accents are stripped before the lookup.
            "Kastanie", "Kiefer", "Birke", "Néflier", "Süsskirsche",
            "Gewone esdoorn", "Pin maritime",
            # A specific epithet whose genus was lost upstream.  "Japonica" is
            # not a genus, but nothing about its shape says so.
            "Japonica", "Nigra", "Biloba", "Serrulata", "Negundo",
            # Cultivar and marketing names with no genus attached.
            "Tai haku", "Sunset boulevard", "James grieve", "Heaven scent",
            # Free text an inventory left in the species column.
            "Misc", "Mixed species", "Scheduled planting", "Uknown taxus",
        ],
    )
    def test_drops_values_that_name_no_genus(self, raw):
        assert sanitize_species(raw) is None

    def test_family_is_not_a_species_rank_name(self):
        """A family would otherwise be handed to the LLM as if it were a tree."""
        assert sanitize_species("Platanaceae") is None
        assert sanitize_species("Platanus") == "Platanus"

    @pytest.mark.parametrize(
        "raw,expected",
        [
            # A placeholder in epithet position says the source gave up on the
            # species -- but it did record the genus, which is a real answer.
            ("Acer unidentified", "Acer"),
            ("Quercus unidentified", "Quercus"),
            ("Ceanothus species", "Ceanothus"),
            ("Thuja type", "Thuja"),
            ("Malus spec", "Malus"),
            ("Yucca no", "Yucca"),
            # Same for a cultivar name that is not a Latin epithet.
            ("Callistemon king", "Callistemon"),
            ("Cydonia champion", "Cydonia"),
            ("Prunus tai", "Prunus"),
            # A mark with nothing after it is dangling, not a hybrid.
            ("Parkinsonia x", "Parkinsonia"),
        ],
    )
    def test_keeps_the_genus_when_only_the_epithet_is_junk(self, raw, expected):
        assert sanitize_species(raw) == expected

    @pytest.mark.parametrize(
        "raw",
        ["X ambigua", "X media", "X soulangeana", "× acerifolia"],
    )
    def test_drops_a_hybrid_epithet_whose_genus_is_missing(self, raw):
        """A nothogenus name is still genus + epithet.

        With one token after the mark, the genus is what went missing: these
        are `Genus × epithet` values that lost their genus upstream, and
        reading them as a nothogenus would invent one.
        """
        assert sanitize_species(raw) is None

    def test_a_real_nothogenus_still_survives(self):
        assert sanitize_species("X amelasorbus jackii") == "X amelasorbus jackii"
        assert sanitize_species("× chitalpa tashkentensis") == "× chitalpa tashkentensis"

    def test_accents_are_stripped_from_a_latin_name(self):
        """A scientific name is ASCII; an accent means a typo or a common name.

        Stripping first lets one rule cover both -- "Mālus" is the real genus
        misspelled, "Néflier" is French for medlar.
        """
        assert sanitize_species("Mālus") == "Malus"
        assert sanitize_species("Néflier") is None

    def test_a_misspelled_binomial_is_left_for_enrichment(self):
        """Badly typed is not the same as not a taxon.

        Dropping these to Unknown would lose a tree we can identify; the
        enrichment step resolves the spelling.
        """
        assert sanitize_species("Crateagus monogyna") == "Crateagus monogyna"
        assert sanitize_species("Sequioa sempervirens") == "Sequioa sempervirens"


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
# JSON responses and freshness probes
# ---------------------------------------------------------------------------

# The exact body gdi.berlin.de serves — HTTP 200, no content-type, HTML — for
# every path (WFS and metadata API alike) while the platform is in maintenance.
MAINTENANCE_PAGE = (
    "\n        <!DOCTYPE html>\n        <html lang=\"de\">\n"
    "        <head><title>Wartungsarbeiten</title></head>\n        </html>"
)


class _FakeResponse:
    def __init__(self, text: str, status_code: int = 200, headers: dict | None = None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}
        self.content = text.encode()

    def json(self):
        import json

        return json.loads(self.text)

    def raise_for_status(self):
        import requests

        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


def _capture_stdout(monkeypatch) -> io.BytesIO:
    buf = io.BytesIO()

    class _FakeStdout:
        buffer = buf

    monkeypatch.setattr(sys, "stdout", _FakeStdout())
    return buf


class TestResponseJson:
    def test_parses_json(self):
        assert response_json(_FakeResponse('{"a": 1}'), "http://x") == {"a": 1}

    def test_overpass_remark_raises_upstream_unavailable(self):
        """Overpass reports an overloaded/timed-out query as HTTP 200 with a
        well-formed body, an empty `elements` list and the failure in `remark`.

        Unclassified, that reaches the caller as a valid payload with no rows,
        and the script's own "no features" guard turns a transient Overpass
        hiccup into a fatal error with no retry -- which is exactly how the
        London landmarks refresh failed.
        """
        body = (
            '{"version": 0.6, "elements": [], "remark": "runtime error: Query '
            'timed out in \\"query\\" at line 3 after 180 seconds."}'
        )
        with pytest.raises(UpstreamUnavailable) as e:
            response_json(_FakeResponse(body), "http://overpass")
        assert "timed out" in str(e.value)

    def test_overpass_benign_remark_is_not_an_error(self):
        """`remark` also carries attribution and tag advisories."""
        body = (
            '{"elements": [{"id": 1}], '
            '"remark": "Please note: data is licensed ODbL."}'
        )
        assert response_json(_FakeResponse(body), "http://overpass") == {
            "elements": [{"id": 1}],
            "remark": "Please note: data is licensed ODbL.",
        }

    def test_arcgis_error_envelope_raises_upstream_unavailable(self):
        """A 200 carrying {"error": {...}} is the portal failing, not our schema.

        Burlington's ArcGIS answered a statistics query this way during an
        outage; classified as a payload, it reached the probe's "no features"
        guard and aborted the whole refresh.
        """
        body = '{"error":{"code":500,"message":"Error performing query operation"}}'
        with pytest.raises(UpstreamUnavailable) as e:
            response_json(_FakeResponse(body), "http://arcgis/query")
        assert "Error performing query operation" in str(e.value)

    def test_socrata_error_flag_raises(self):
        with pytest.raises(UpstreamUnavailable):
            response_json(
                _FakeResponse('{"error": true, "message": "backend down"}'), "http://x"
            )

    def test_ckan_success_false_raises(self):
        with pytest.raises(UpstreamUnavailable):
            response_json(_FakeResponse('{"success": false}'), "http://x")

    def test_payload_with_unrelated_error_field_is_returned(self):
        """Only an error *envelope* counts; a data column named error does not."""
        payload = response_json(
            _FakeResponse('{"features": [{"error": 0.5}], "success": true}'),
            "http://x",
        )
        assert payload["features"] == [{"error": 0.5}]

    def test_maintenance_page_raises_upstream_unavailable(self):
        with pytest.raises(UpstreamUnavailable) as e:
            response_json(_FakeResponse(MAINTENANCE_PAGE), "http://portal/api")
        # The message must name the host and what it served — the bare
        # JSONDecodeError this replaces said only "line 2 column 9".
        assert "http://portal/api" in str(e.value)
        assert "HTTP 200" in str(e.value)
        assert "Wartungsarbeiten" in str(e.value)


class TestGetJsonWithRetry:
    def test_retries_non_json_body_then_raises(self, monkeypatch):
        calls = []

        def fake_request(method, url, **kwargs):
            calls.append(url)
            return _FakeResponse(MAINTENANCE_PAGE)

        import requests

        monkeypatch.setattr(requests, "request", fake_request)
        monkeypatch.setattr(time, "sleep", lambda _s: None)

        with pytest.raises(UpstreamUnavailable):
            get_json_with_retry("http://portal/api", max_retries=3)
        assert len(calls) == 3

    def test_returns_payload_once_the_portal_recovers(self, monkeypatch):
        bodies = [MAINTENANCE_PAGE, '{"ok": true}']

        import requests

        monkeypatch.setattr(
            requests, "request", lambda method, url, **kw: _FakeResponse(bodies.pop(0))
        )
        monkeypatch.setattr(time, "sleep", lambda _s: None)

        assert get_json_with_retry("http://portal/api") == {"ok": True}


class TestEmitFreshness:
    def _read(self, buf: io.BytesIO) -> pa.Table:
        buf.seek(0)
        return pa.ipc.open_stream(buf).read_all()

    def test_emits_the_fetched_timestamp(self, monkeypatch):
        buf = _capture_stdout(monkeypatch)
        ts = datetime(2025, 11, 19, tzinfo=timezone.utc)
        emit_freshness("DEBER", lambda: ts)

        table = self._read(buf)
        assert table.column("city")[0].as_py() == "DEBER"
        assert table.column("data_updated_through")[0].as_py() == ts
        assert table.schema.field("data_updated_through").type.equals(
            pa.timestamp("us", tz="UTC")
        )

    def test_unavailable_portal_degrades_instead_of_raising(self, monkeypatch):
        """A dead portal must not abort the run — one raising probe fails every city."""
        buf = _capture_stdout(monkeypatch)

        def fetch():
            raise UpstreamUnavailable("portal in maintenance")

        emit_freshness("DEBER", fetch)

        table = self._read(buf)
        assert (
            table.column("data_updated_through")[0].as_py()
            == PORTAL_UNAVAILABLE_TIMESTAMP
        )

    def test_parse_failure_still_raises(self, monkeypatch):
        """A payload we can't read means our field mapping drifted: stay loud."""
        _capture_stdout(monkeypatch)

        def fetch():
            raise KeyError("gmd:dateStamp")

        with pytest.raises(KeyError):
            emit_freshness("DEBER", fetch)

    def test_city_is_omitted_when_none(self, monkeypatch):
        buf = _capture_stdout(monkeypatch)
        emit_freshness(
            None, lambda: datetime(2025, 1, 1, tzinfo=timezone.utc), label="ecoregion"
        )
        assert self._read(buf).schema.names == ["data_updated_through"]


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


class TestEnforceTreeSchemaSpecies:
    """The species column is cleaned at this one chokepoint, for every city."""

    def _table(self, species):
        return pa.table(
            {
                "tree_id": pa.array(["t%d" % i for i in range(len(species))]),
                "city": pa.array(["USBOS"] * len(species)),
                "species": pa.array(species, type=pa.string()),
            }
        )

    def test_non_taxa_become_the_sentinel(self):
        out = enforce_tree_schema(
            self._table(["Acer rubrum", "Vacant", "Pin oak", None]),
            city="Test",
            data_source="CITY_OF_BOSTON",
        )
        assert out.column("species").to_pylist() == [
            "Acer rubrum",
            UNKNOWN_SPECIES,
            UNKNOWN_SPECIES,
            UNKNOWN_SPECIES,
        ]

    def test_growth_form_values_keep_their_own_sentinel(self):
        """"Palm" is not a taxon, but it is not nothing either.

        Merging it into UNKNOWN_SPECIES throws away the one fact the source did
        record, which is what the map icon is chosen from.
        """
        out = enforce_tree_schema(
            self._table(["Palm", "arbusto", "Cactus", "Vacant"]),
            city="Test",
            data_source="CITY_OF_BOSTON",
        )
        assert out.column("species").to_pylist() == [
            PALM_SPECIES,
            SHRUB_SPECIES,
            CACTUS_SPECIES,
            UNKNOWN_SPECIES,
        ]

    def test_species_column_is_never_null(self):
        """`species` is a Trilogy key; a null there is what silently dropped
        rows from Boston's species-keyed dbh imputation."""
        out = enforce_tree_schema(
            self._table([None, "", "   ", "Quercus robur"]),
            city="Test",
            data_source="CITY_OF_BOSTON",
        )
        assert out.column("species").null_count == 0

    def test_cleanup_is_reported(self, capsys):
        enforce_tree_schema(
            self._table(["Vacant", "Prunus serrulata 'kwanzan'"]),
            city="Test",
            data_source="CITY_OF_BOSTON",
        )
        err = capsys.readouterr().err
        assert "species cleanup" in err
        assert "1 value(s)" in err


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


# ---------------------------------------------------------------------------
# Growth-form sentinels


class TestFormSentinels:
    """Non-taxa that still name a growth form keep it; the rest merge."""

    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Palm", PALM_SPECIES),
            ("palm tree", PALM_SPECIES),
            ("Palmera", PALM_SPECIES),
            ("Shrub", SHRUB_SPECIES),
            ("arbusto", SHRUB_SPECIES),
            ("Struik", SHRUB_SPECIES),
            ("Cactus", CACTUS_SPECIES),
            ("Cactaceae", CACTUS_SPECIES),
            ("Vacant", None),
            ("Unbekannt", None),
            ("Acer rubrum", None),
        ],
    )
    def test_form_sentinel_for(self, raw, expected):
        assert form_sentinel_for(raw) == expected

    @pytest.mark.parametrize("raw", ["Palm", "Shrub", "Cactus", "arbusto"])
    def test_a_form_value_is_never_kept_as_a_genus(self, raw):
        """These are genus-shaped and would otherwise pass the genus test,
        landing in the species key as invented genera."""
        assert sanitize_species(raw) is None

    def test_sentinels_are_the_full_set(self):
        assert SPECIES_SENTINELS == frozenset(
            {UNKNOWN_SPECIES, PALM_SPECIES, SHRUB_SPECIES, CACTUS_SPECIES}
        )

    @pytest.mark.parametrize(
        "raw", ["Unbekannt", "Nvt", "Privet", "Tree(s)", "--", "Unknown tree species"]
    )
    def test_leaked_non_taxa_are_placeholders(self, raw):
        """Values measured in the published data that survived as fake genera."""
        assert sanitize_species(raw) is None
        assert form_sentinel_for(raw) is None


class TestCoordinateDropReporting:
    """A dropped row's *cause* matters more than the count.

    Null and (0, 0) coordinates fail the bounding-box comparison exactly like a
    genuinely distant one, so reporting every drop as "outside bounds" reads as
    a too-tight bounding box. LA drops 262,112 rows and every one of them is a
    missing or null-island coordinate -- 120,000 sampled records contained no
    valid coordinate outside the box at all -- but the message said geography
    and cost a real investigation to disprove.
    """

    @staticmethod
    def _table(lat, lon):
        return pa.table(
            {
                "latitude": pa.array(lat, type=pa.float64()),
                "longitude": pa.array(lon, type=pa.float64()),
            }
        )

    def test_breaks_the_count_down_by_cause(self, capsys):
        table = self._table(
            [34.0] * 90 + [None] * 3 + [0.0] * 5 + [40.0] * 2,
            [-118.3] * 93 + [0.0] * 5 + [-70.0] * 2,
        )
        kept = validate_coordinates(table, city="Test", city_code="USLAX")
        err = capsys.readouterr().err
        assert kept.num_rows == 90
        assert "3 missing a coordinate" in err
        assert "5 at (0, 0)" in err
        assert "2 outside" in err

    def test_says_nothing_when_every_row_is_usable(self, capsys):
        table = self._table([34.0, 34.1], [-118.3, -118.2])
        assert validate_coordinates(table, city="Test", city_code="USLAX").num_rows == 2
        assert capsys.readouterr().err == ""

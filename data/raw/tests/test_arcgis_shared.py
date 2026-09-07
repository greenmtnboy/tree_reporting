"""`_arcgis_shared`, with the HTTP layer faked.

The paging rules are what these are really about.  Both were latent bugs in the
hand-rolled copies this module replaced, and both fail *silently* -- as a city
that publishes fewer trees than it used to, which looks exactly like a city
that published fewer trees than it used to.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

import _arcgis_shared  # noqa: E402
from _arcgis_shared import (  # noqa: E402
    FeatureLayer,
    coded_value_domain,
    esri_geometry_to_wkt,
    esri_ms_to_date,
    esri_ms_to_datetime,
    esri_point,
    feature_count,
    field_max,
    iter_attributes,
    iter_features,
    layer_last_edit,
    max_record_count,
)

LAYER = FeatureLayer("https://example.test/arcgis/rest/services/Trees/FeatureServer/0")


class FakePortal:
    """Stands in for `get_json_with_retry`, recording what was asked for."""

    def __init__(self, metadata: dict | None = None, pages: list[dict] | None = None):
        # `is None`, not `or`: an empty metadata dict is a case under test.
        self.metadata = {"maxRecordCount": 2000} if metadata is None else metadata
        self.pages = pages or []
        self.calls: list[dict] = []

    def __call__(self, url, params=None, timeout=None, **kwargs):
        self.calls.append({"url": url, "params": params or {}})
        if url.endswith("?f=json"):
            return self.metadata
        if not self.pages:
            return {"features": []}
        return self.pages.pop(0)


@pytest.fixture
def portal(monkeypatch):
    def install(fake: FakePortal) -> FakePortal:
        monkeypatch.setattr(_arcgis_shared, "get_json_with_retry", fake)
        return fake

    return install


def page(n: int, *, more: bool | None = None, start: int = 0) -> dict:
    body: dict = {
        "features": [
            {"attributes": {"OBJECTID": start + i}, "geometry": {"x": 1.0, "y": 2.0}}
            for i in range(n)
        ]
    }
    if more is not None:
        body["exceededTransferLimit"] = more
    return body


# ---------------------------------------------------------------------------
# Addressing
# ---------------------------------------------------------------------------

class TestFeatureLayer:
    def test_query_url_is_derived(self):
        assert LAYER.query_url == (
            "https://example.test/arcgis/rest/services/Trees/FeatureServer/0/query"
        )

    def test_a_trailing_slash_is_tolerated(self):
        assert FeatureLayer(LAYER.url + "/").query_url == LAYER.query_url

    def test_passing_the_query_endpoint_is_refused(self):
        """Every copy this replaced stored the /query URL; mixing the two silently
        produces `.../query/query`, which 404s a long way from the cause."""
        with pytest.raises(ValueError, match="not its /query endpoint"):
            FeatureLayer(LAYER.query_url)


# ---------------------------------------------------------------------------
# Esri time
# ---------------------------------------------------------------------------

class TestEsriTime:
    def test_epoch_milliseconds_become_utc(self):
        assert esri_ms_to_datetime(1785589536398) == datetime(
            2026, 8, 1, 13, 5, 36, 398000, tzinfo=timezone.utc
        )

    def test_a_date_is_the_same_value_truncated(self):
        assert esri_ms_to_date(1785589536398).isoformat() == "2026-08-01"

    @pytest.mark.parametrize("value", [None, "", "not a number"])
    def test_empty_and_unparseable_are_none(self, value):
        assert esri_ms_to_datetime(value) is None

    def test_an_out_of_range_stamp_is_none_not_an_exception(self):
        """A portal writing year 30827 into one row is publishing junk in that
        row, not telling us the field mapping is wrong."""
        assert esri_ms_to_datetime(9e18) is None


# ---------------------------------------------------------------------------
# Freshness
# ---------------------------------------------------------------------------

class TestFreshness:
    def test_data_last_edit_is_preferred_over_last_edit(self, portal):
        """`lastEditDate` also moves for a schema-only edit; the rows are what
        make a Parquet stale."""
        portal(FakePortal(metadata={"editingInfo": {
            "lastEditDate": 1785589554570,
            "dataLastEditDate": 1785589536398,
        }}))
        assert layer_last_edit(LAYER) == esri_ms_to_datetime(1785589536398)

    def test_last_edit_is_the_fallback(self, portal):
        portal(FakePortal(metadata={"editingInfo": {"lastEditDate": 1785589554570}}))
        assert layer_last_edit(LAYER) == esri_ms_to_datetime(1785589554570)

    def test_no_editing_info_raises(self, portal):
        """Must NOT degrade: `emit_freshness` turns an availability failure into
        the epoch, and a layer with no editingInfo is a schema question. Reporting
        "no new data" there would freeze the city's Parquet silently and for ever.
        """
        portal(FakePortal(metadata={"maxRecordCount": 2000}))
        with pytest.raises(RuntimeError, match="editingInfo"):
            layer_last_edit(LAYER)

    def test_field_max_builds_its_own_out_statistics(self, portal):
        fake = portal(FakePortal(pages=[
            {"features": [{"attributes": {"max_value": 1785589536398}}]}
        ]))
        assert field_max(LAYER, "EditDate") == esri_ms_to_datetime(1785589536398)
        stats = fake.calls[-1]["params"]["outStatistics"]
        assert '"onStatisticField": "EditDate"' in stats
        assert '"statisticType": "max"' in stats

    def test_field_max_tolerates_the_echoed_case(self, portal):
        """ArcGIS is inconsistent about the case it echoes outStatisticFieldName
        in, which is why the lookup is case-insensitive."""
        portal(FakePortal(pages=[
            {"features": [{"attributes": {"MAX_VALUE": 1785589536398}}]}
        ]))
        assert field_max(LAYER, "EditDate") == esri_ms_to_datetime(1785589536398)

    def test_field_max_with_no_rows_raises(self, portal):
        portal(FakePortal(pages=[{"features": []}]))
        with pytest.raises(RuntimeError, match="no statistics row"):
            field_max(LAYER, "EditDate")


class TestFeatureCount:
    def test_count_only(self, portal):
        portal(FakePortal(pages=[{"count": 359263}]))
        assert feature_count(LAYER) == 359263

    def test_a_missing_count_raises(self, portal):
        portal(FakePortal(pages=[{}]))
        with pytest.raises(RuntimeError, match="no count"):
            feature_count(LAYER)


# ---------------------------------------------------------------------------
# Paging -- the part that fails silently
# ---------------------------------------------------------------------------

class TestPaging:
    def test_page_size_comes_from_the_layer(self, portal):
        """Asking for more than `maxRecordCount` is SILENTLY CAPPED, so a
        hardcoded page size larger than the layer's makes every page a short
        page -- and a short page is the signal that the data ran out. One
        metadata request removes a whole class of silently truncated city.
        """
        fake = portal(FakePortal(
            metadata={"maxRecordCount": 500},
            pages=[page(500, more=True), page(120, more=False)],
        ))
        assert max_record_count(LAYER) == 500
        rows = [r for p in iter_features(LAYER) for r in p]
        assert len(rows) == 620
        query_calls = [c for c in fake.calls if c["url"].endswith("/query")]
        assert all(c["params"]["resultRecordCount"] == "500" for c in query_calls)

    def test_a_missing_max_record_count_falls_back(self, portal):
        portal(FakePortal(metadata={}))
        assert max_record_count(LAYER) == _arcgis_shared.FALLBACK_PAGE_SIZE

    def test_exceeded_transfer_limit_ends_the_loop(self, portal):
        """Esri's own "there is more" flag is exact. The short-page heuristic is
        not: a full last page is indistinguishable from a truncated one, so a
        layer whose row count is an exact multiple of the page size would fetch
        one extra page under the heuristic and, worse, a capped page would end
        the loop early.
        """
        portal(FakePortal(pages=[
            page(2000, more=True, start=0),
            page(2000, more=False, start=2000),
        ]))
        rows = [r for p in iter_features(LAYER) for r in p]
        assert len(rows) == 4000

    def test_a_full_final_page_is_not_refetched(self, portal):
        fake = portal(FakePortal(pages=[page(2000, more=False)]))
        rows = [r for p in iter_features(LAYER) for r in p]
        assert len(rows) == 2000
        assert len([c for c in fake.calls if c["url"].endswith("/query")]) == 1

    def test_servers_without_the_flag_use_the_short_page_rule(self, portal):
        portal(FakePortal(pages=[page(2000), page(37)]))
        rows = [r for p in iter_features(LAYER) for r in p]
        assert len(rows) == 2037

    def test_offset_advances_by_rows_returned(self, portal):
        fake = portal(FakePortal(pages=[
            page(2000, more=True), page(2000, more=True), page(1, more=False)
        ]))
        list(iter_features(LAYER))
        offsets = [
            c["params"]["resultOffset"]
            for c in fake.calls
            if c["url"].endswith("/query")
        ]
        assert offsets == ["0", "2000", "4000"]

    def test_an_empty_first_page_ends_cleanly(self, portal):
        portal(FakePortal(pages=[{"features": []}]))
        assert list(iter_features(LAYER)) == []

    def test_order_by_is_required(self, portal):
        """Offset paging over an unordered ArcGIS result may repeat or skip rows
        between requests, and a short page from that ends the loop early."""
        portal(FakePortal())
        with pytest.raises(ValueError, match="order_by"):
            list(iter_features(LAYER, order_by=""))

    def test_geometry_is_off_by_default_and_out_sr_rides_with_it(self, portal):
        fake = portal(FakePortal(pages=[page(1, more=False)]))
        list(iter_features(LAYER))
        params = fake.calls[-1]["params"]
        assert params["returnGeometry"] == "false"
        assert "outSR" not in params

        fake = portal(FakePortal(pages=[page(1, more=False)]))
        list(iter_features(LAYER, return_geometry=True, out_sr=4326))
        params = fake.calls[-1]["params"]
        assert params["returnGeometry"] == "true"
        assert params["outSR"] == "4326"

    def test_iter_attributes_strips_the_envelope(self, portal):
        portal(FakePortal(pages=[page(2, more=False)]))
        rows = [r for p in iter_attributes(LAYER) for r in p]
        assert rows == [{"OBJECTID": 0}, {"OBJECTID": 1}]


# ---------------------------------------------------------------------------
# Field domains
# ---------------------------------------------------------------------------

class TestCodedValueDomain:
    """A layer's own data dictionary, and why an empty answer must not be one.

    Ottawa's `SPECIES` column stores an inverted common name and its domain
    holds the binomial for each -- so a layer that reads like "common name
    only" is in fact fully identified, and reading the domain is the
    difference between 304k identified trees and 304k `Unknown`.
    """

    def test_reads_the_code_to_name_map(self, portal):
        portal(
            FakePortal(
                metadata={
                    "fields": [
                        {"name": "DBH", "type": "esriFieldTypeInteger"},
                        {
                            "name": "SPECIES",
                            "type": "esriFieldTypeString",
                            "domain": {
                                "type": "codedValue",
                                "codedValues": [
                                    {"code": "Maple Sugar", "name": "Acer saccharum"},
                                    {"code": "Oak Red", "name": "Quercus rubra"},
                                ],
                            },
                        },
                    ]
                }
            )
        )
        assert coded_value_domain(LAYER, "SPECIES") == {
            "Maple Sugar": "Acer saccharum",
            "Oak Red": "Quercus rubra",
        }

    def test_a_field_with_no_domain_raises(self, portal):
        """Degrading to `{}` would publish every tree as Unknown and say nothing."""
        portal(FakePortal(metadata={"fields": [{"name": "SPECIES", "type": "x"}]}))
        with pytest.raises(RuntimeError, match="no coded-value domain"):
            coded_value_domain(LAYER, "SPECIES")

    def test_a_missing_field_raises(self, portal):
        portal(FakePortal(metadata={"fields": [{"name": "DBH", "type": "x"}]}))
        with pytest.raises(RuntimeError, match="no field named"):
            coded_value_domain(LAYER, "SPECIES")


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

class TestEsriPoint:
    """`"NaN"` is a *string*, and it is how two on-prem servers here say
    "this feature has no location".  Read straight into a float column it
    fails at `pa.array` with a message about type conversion."""

    def test_a_normal_point(self):
        assert esri_point({"x": -79.5, "y": 43.6}) == (43.6, -79.5)

    @pytest.mark.parametrize(
        "geometry",
        [
            None,
            {},
            {"x": "NaN", "y": "NaN"},
            {"x": float("nan"), "y": float("nan")},
            {"x": None, "y": 43.6},
            {"x": -79.5},
        ],
    )
    def test_nothing_usable_is_none_none(self, geometry):
        assert esri_point(geometry) == (None, None)

    def test_wkt_refuses_a_nan_point_rather_than_writing_one(self):
        assert esri_geometry_to_wkt({"x": "NaN", "y": "NaN"}) is None
        assert esri_geometry_to_wkt({"x": 1.0, "y": 2.0}) == "POINT(1.0 2.0)"

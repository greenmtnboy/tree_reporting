"""The homepage feed publisher, offline: shaping, validation and the guards
that keep a bad run from replacing the live object.

`build_feed` is pure, so these drive it with a hand-built collection rather
than parquets. The network side (listing, signed upload, generation
preconditions) is exercised by a real dry run, not here.
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

RAW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW))
sys.path.insert(0, str(RAW.parent / "website"))

import publish_homepage as ph  # noqa: E402
from shared.ingest import SPECIES_SENTINELS  # noqa: E402

AT = "2026-10-04T06:00:00+00:00"

CITIES = [
    {"code": "USSFO", "name": "San Francisco", "country": "United States", "coordinates": [-122.4, 37.8]},
    {"code": "CAVAN", "name": "Vancouver", "country": "Canada", "coordinates": [-123.1, 49.3]},
    {"code": "GBLON", "name": "London", "country": "United Kingdom", "coordinates": [-0.13, 51.5]},
    {"code": "USNEW", "name": "Newtown", "country": "United States", "coordinates": [-100.0, 40.0]},
]


def collection() -> dict:
    return {
        "city_trees": {"USSFO": 100, "CAVAN": 60, "GBLON": 40},
        "species_city": {
            "Platanus x hispanica": {"USSFO": 30, "GBLON": 25},
            "Acer rubrum": {"USSFO": 20, "CAVAN": 30},
            "Prunus": {"CAVAN": 25},  # genus only: never a globe observation
            "Quercus robur": {"GBLON": 10, "USSFO": 5},
            "Tilia cordata": {"CAVAN": 5, "GBLON": 5},
        },
        "enrichment": {
            "Platanus x hispanica": ("x hispanica", "London plane"),
            "Acer rubrum": ("rubrum", "Red maple"),
            "Quercus robur": ("robur", None),
        },
        "regions": {
            "USSFO": {"id": 1, "name": "California coast", "biome": "Med", "source_version": "2017",
                      "match": "contains", "distance_m": 0},
        },
        "published": {"USSFO": 1, "CAVAN": 2, "GBLON": 3},
    }


def feed() -> dict:
    return ph.build_feed(CITIES, collection(), AT)


def test_sentinels_cover_the_pipelines_own():
    assert SPECIES_SENTINELS <= set(ph.SENTINELS), (
        "publish_homepage.SENTINELS has fallen behind shared.ingest.SPECIES_SENTINELS"
    )


def test_a_built_feed_passes_validation():
    ph.validate(feed())


def test_totals_and_counts():
    s = feed()["stats"]
    assert s["total_trees"] == 200
    assert s["city_count"] == 4
    assert s["cities_with_data"] == 3
    assert s["country_count"] == 3
    assert s["species_count"] == 5
    assert feed()["catalogue_count"] == 5


def test_an_unpublished_city_is_pending_not_missing():
    f = feed()
    newtown = next(c for c in f["stats"]["cities"] if c["code"] == "USNEW")
    assert newtown["trees"] == 0
    assert "USNEW" not in f["city_links"]
    assert "USNEW" not in {n["code"] for n in f["field_notes"]}


def test_top_species_are_ranked_with_null_common_names_kept():
    top = feed()["stats"]["top_species"]
    assert [p["scientific_name"] for p in top][:2] == ["Platanus x hispanica", "Acer rubrum"]
    assert top[0]["slug"] == "platanus-x-hispanica"
    assert next(p for p in top if p["scientific_name"] == "Quercus robur")["common_name"] is None


def test_globe_tour_opens_in_order_and_skips_genus_only_species():
    notes = feed()["field_notes"]
    assert [n["code"] for n in notes] == ["GBLON", "USSFO", "CAVAN"]
    vancouver = next(n for n in notes if n["code"] == "CAVAN")
    assert [o["species"] for o in vancouver["observations"]] == ["Acer rubrum", "Tilia cordata"]
    london = next(n for n in notes if n["code"] == "GBLON")
    # A missing common name falls back to the scientific one.
    assert london["observations"][1] == {
        "species": "Quercus robur", "commonName": "Quercus robur", "slug": "quercus-robur", "trees": 10,
    }


def test_timestamps_agree():
    f = feed()
    assert f["generated_at"] == f["stats"]["generated_at"] == f["source_generated_at"]["stats"] == AT


@pytest.mark.parametrize(
    "break_it",
    [
        lambda f: f["stats"].update(total_trees=f["stats"]["total_trees"] + 1),
        lambda f: f["stats"].update(stale=True),
        lambda f: f["stats"]["cities"].append(dict(f["stats"]["cities"][0])),
        lambda f: f["stats"]["top_species"].reverse(),
        lambda f: f.update(field_notes=[]),
        lambda f: f["field_notes"][0]["observations"][0].update(trees=10**9),
        lambda f: f["city_links"].update(XXXXX="https://example.com/x.parquet"),
        lambda f: f["stats"].update(generated_at="2026-10-05T00:00:00+00:00"),
    ],
    ids=["total", "stale", "duplicate-city", "top-order", "no-stops", "obs-over-city", "unknown-link", "timestamps"],
)
def test_validation_refuses_a_broken_candidate(break_it):
    f = copy.deepcopy(feed())
    break_it(f)
    with pytest.raises(ph.PublishError):
        ph.validate(f)


def test_a_previously_published_city_that_vanished_fails_the_run():
    live = {"stats": {"cities": [{"code": "USSFO", "trees": 100}]}}
    generations = {c["code"]: None for c in CITIES}
    with pytest.raises(ph.PublishError, match="USSFO"):
        ph.collect(CITIES, generations, live)


def test_only_timestamps_differ_means_unchanged():
    a = feed()
    b = ph.build_feed(CITIES, collection(), "2026-10-05T06:00:00+00:00")
    assert ph.content_of(a) == ph.content_of(b)
    c = copy.deepcopy(b)
    c["stats"]["cities"][0]["trees"] += 1
    assert ph.content_of(a) != ph.content_of(c)


def test_every_registry_city_has_a_country():
    cities = ph.load_cities()
    assert cities and all(c["country"] for c in cities)

"""Every place a city has to be registered, checked for every city.

Adding a city means editing roughly twenty files across four languages, and
the failure mode for almost every one of them is *silence*: a city missing from
`core.preql`'s ecoregion case gets a null ecoregion, one missing from
`tree_info.preql`'s `latest_update_through` never marks the rollup stale, one
missing from `cityConfig.json` simply never appears in the UI.  None of those
raise.

The existing suites cover the parts that were bought with an outage --
`test_data_sources` the source enums and community partitions,
`test_cloud_jobs` the schedules and the rollup file list.  This one is the
sweep: it walks the registries a city has to appear in and fails naming the
file and the line to add, so a half-wired city is a red test rather than a
quiet hole in the map.

Everything here is parametrised over `MUNICIPAL_DATA_SOURCES`, so a new city
is covered the moment it is added there -- which is step 3 of the runbook and
the first edit anyone makes.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = RAW_DIR.parent
REPO_DIR = DATA_DIR.parent
SRC_DIR = REPO_DIR / "src" / "src"
sys.path.insert(0, str(RAW_DIR))

from _ingest_shared import (  # noqa: E402
    CITY_BOUNDS,
    MUNICIPAL_DATA_SOURCES,
    OSM_DATA_SOURCES,
)

CITY_CODES = sorted(MUNICIPAL_DATA_SOURCES)


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def city_dir(code: str) -> Path:
    return RAW_DIR / code.lower()


def tree_model(code: str) -> Path:
    """The city's tree model, found by its directory rather than by name.

    The preql file is named after the city ("denver_tree_info.preql") while the
    directory is named after the code, and no rule ties the two -- so discover
    rather than assume.
    """
    matches = sorted(city_dir(code).glob("*_tree_info.preql"))
    assert len(matches) == 1, (
        f"{code}: expected exactly one *_tree_info.preql in "
        f"data/raw/{code.lower()}/, found {[m.name for m in matches]}"
    )
    return matches[0]


def landmark_model(code: str) -> Path:
    matches = sorted(city_dir(code).glob("*_landmarks.preql"))
    assert len(matches) == 1, (
        f"{code}: expected exactly one *_landmarks.preql in "
        f"data/raw/{code.lower()}/, found {[m.name for m in matches]}. "
        "Every city needs a landmarks model even if it yields zero rows -- a "
        "missing file fails the landmark lane for every city."
    )
    return matches[0]


# ---------------------------------------------------------------------------
# The city directory itself
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", CITY_CODES)
def test_city_has_a_directory(code: str):
    assert city_dir(code).is_dir(), (
        f"{code}: no data/raw/{code.lower()}/ directory"
    )


@pytest.mark.parametrize("code", CITY_CODES)
def test_city_has_a_tree_model(code: str):
    assert tree_model(code).exists()


@pytest.mark.parametrize("code", CITY_CODES)
def test_city_has_a_landmark_model(code: str):
    assert landmark_model(code).exists()


# ---------------------------------------------------------------------------
# core.preql
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", CITY_CODES)
def test_city_is_in_the_core_enum(code: str):
    text = read(RAW_DIR / "core.preql")
    enum = re.search(r"key city enum<string>\[(.*?)\];", text, re.S)
    assert enum, "core.preql declares no `key city enum`"
    values = re.findall(r"'([^']+)'", enum.group(1))
    assert code in values, (
        f"{code} is missing from the `city` enum in core.preql. Trilogy "
        f"rejects every `complete where city = '{code}'` clause until it is "
        f"there, and reports it against the city's model rather than here."
    )


@pytest.mark.parametrize("code", CITY_CODES)
def test_city_has_an_ecoregion(code: str):
    text = read(RAW_DIR / "core.preql")
    assert re.search(rf"when city = '{code}' then \d+", text), (
        f"{code} has no `ecoregion_id` branch in core.preql. Without one the "
        f"city's trees resolve to a null ecoregion and drop out of every "
        f"nativeness chart, silently. Look the code up at the city centroid: "
        f"RESOLVE_Ecoregions FeatureServer, ECO_ID at that point."
    )


# ---------------------------------------------------------------------------
# Freshness properties
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", CITY_CODES)
def test_municipal_freshness_property_matches_the_source_list(code: str):
    """A municipal freshness column exists exactly when a municipal source does.

    A community-only city (Milos, Santorini) must NOT declare one: its
    published watermark is a `greatest()` over the columns something actually
    feeds, and a term nothing feeds never resolves.
    """
    text = read(RAW_DIR / "tree_common.preql")
    declared = f"property <*>.{code.lower()}_data_updated_through" in text
    expected = bool(MUNICIPAL_DATA_SOURCES[code])
    if expected:
        assert declared, (
            f"{code} has a municipal source but no "
            f"`property <*>.{code.lower()}_data_updated_through` in "
            f"tree_common.preql"
        )
    else:
        assert not declared, (
            f"{code} has no municipal source, so it must not declare "
            f"`{code.lower()}_data_updated_through` -- a `greatest()` term "
            f"nothing feeds never resolves"
        )


@pytest.mark.parametrize("code", CITY_CODES)
def test_community_freshness_property_exists(code: str):
    text = read(RAW_DIR / "community_tree_info.preql")
    assert f"property <*>.{code.lower()}_community_data_updated_through" in text, (
        f"{code} has no community freshness property in "
        f"community_tree_info.preql. Every city takes community submissions, "
        f"so every city needs its own column -- a shared one would mark all "
        f"cities stale on one approval."
    )


@pytest.mark.parametrize("code", sorted(OSM_DATA_SOURCES))
def test_osm_freshness_property_exists(code: str):
    text = read(RAW_DIR / "tree_common.preql")
    assert f"property <*>.{code.lower()}_osm_data_updated_through" in text, (
        f"{code} has an OSM partition but no "
        f"`property <*>.{code.lower()}_osm_data_updated_through` in "
        f"tree_common.preql"
    )


@pytest.mark.parametrize("code", CITY_CODES)
def test_landmark_freshness_is_declared_and_aggregated(code: str):
    """The property has to exist *and* be in the `greatest()`.

    Declaring it alone is the easy half and the useless one: the landmark lane
    decides staleness from `latest_landmark_update_through`, so a city left out
    of that expression never republishes the union.
    """
    text = read(RAW_DIR / "landmark_common.preql")
    prop = f"{code.lower()}_landmark_data_updated_through"
    assert f"property <*>.{prop}" in text, (
        f"{code}: no `property <*>.{prop}` in landmark_common.preql"
    )
    rollup = re.search(
        r"auto latest_landmark_update_through <- greatest\((.*?)\);", text, re.S
    )
    assert rollup, "landmark_common.preql declares no latest_landmark_update_through"
    assert prop in rollup.group(1), (
        f"{code}: {prop} is declared but missing from "
        f"`latest_landmark_update_through`, so the landmark union never sees "
        f"this city change"
    )


# ---------------------------------------------------------------------------
# Cross-city models
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", CITY_CODES)
def test_cross_city_tree_model_wires_the_city(code: str):
    """`tree_info.preql` needs all three of import, merge and watermark.

    This is the model the *app's* dashboard queries resolve against, so a city
    missing here is invisible to every chart even with a perfectly healthy
    pipeline of its own.
    """
    text = read(RAW_DIR / "tree_info.preql")
    stem = tree_model(code).stem
    assert f"import {code.lower()}.{stem};" in text, (
        f"{code}: tree_info.preql is missing "
        f"`import {code.lower()}.{stem};`"
    )
    assert f"merge {code.lower()}_source into data_source;" in text, (
        f"{code}: tree_info.preql is missing "
        f"`merge {code.lower()}_source into data_source;`, so this city's rows "
        f"carry no data_source in the cross-city view"
    )
    rollup = re.search(
        r"auto latest_update_through <- greatest\((.*?)\);", text, re.S
    )
    assert rollup, "tree_info.preql declares no latest_update_through"
    assert f"{code.lower()}_published_data_updated_through" in rollup.group(1), (
        f"{code}: {code.lower()}_published_data_updated_through is missing "
        f"from `latest_update_through` in tree_info.preql"
    )


@pytest.mark.parametrize("code", CITY_CODES)
def test_landmark_lane_imports_the_city(code: str):
    text = read(RAW_DIR / "landmark_info.preql")
    stem = landmark_model(code).stem
    assert f"import {code.lower()}.{stem};" in text, (
        f"{code}: landmark_info.preql is missing "
        f"`import {code.lower()}.{stem};`"
    )


# ---------------------------------------------------------------------------
# The frontend
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code", CITY_CODES)
def test_city_is_in_the_frontend_config(code: str):
    """`cityConfig.json` is what puts the city button on the map.

    Also the reason this test is in the Python suite rather than vitest: it is
    the same registry question as every check above, and a city addition should
    have one place that tells it what it still owes.
    """
    config = json.loads(read(SRC_DIR / "cityConfig.json"))
    assert code in config, (
        f"{code} is missing from src/src/cityConfig.json, so it never appears "
        f"in the city picker"
    )
    entry = config[code]
    assert entry.get("name"), f"{code}: cityConfig.json entry has no name"
    center = entry.get("center")
    assert isinstance(center, list) and len(center) == 2, (
        f"{code}: cityConfig.json center must be [longitude, latitude]"
    )
    lon, lat = center
    lat_min, lat_max, lon_min, lon_max = CITY_BOUNDS[code]
    # Ordering is the trap: MapLibre takes [lon, lat] and everything else in
    # this repo says lat first, so a swapped pair centres the map in the sea.
    assert lat_min <= lat <= lat_max and lon_min <= lon <= lon_max, (
        f"{code}: cityConfig.json center {center} is outside CITY_BOUNDS "
        f"{CITY_BOUNDS[code]}. The center is [longitude, latitude] -- check "
        f"the order before widening the bounds."
    )


def display_name(code: str) -> str:
    """The name the UI shows, which is the one attribution has to use.

    `cityConfig.json`, not `OSM_CITY_NAMES`: the two disagree (the config says
    "Washington, DC" and the OSM map says "Washington DC") and the config is
    what the city picker and the Info page render.
    """
    return json.loads(read(SRC_DIR / "cityConfig.json"))[code]["name"]


def catalog_block(const: str) -> str:
    catalog = read(SRC_DIR / "data" / "sourceCatalog.ts")
    block = re.search(rf"{const}[^=]*= \[(.*?)\n\]", catalog, re.S)
    assert block, f"sourceCatalog.ts declares no {const}"
    return block.group(1)


@pytest.mark.parametrize("code", CITY_CODES)
def test_city_is_attributed(code: str):
    """Every city names its tree source and its landmark source on the Info page.

    Attribution is a licence obligation for the OSM partition and simple good
    manners for the rest, and it is the step most often skipped: it lives in a
    different language from everything else a city addition touches.
    """
    name = display_name(code)
    for const in ("TREE_INVENTORY_SOURCES", "LANDMARK_SOURCES"):
        assert f"city: '{name}'" in catalog_block(const), (
            f"{code} ({name}) has no entry in {const} in "
            f"src/src/data/sourceCatalog.ts"
        )


@pytest.mark.parametrize("code", sorted(OSM_DATA_SOURCES))
def test_osm_city_carries_odbl_attribution(code: str):
    """OSM data is ODbL; the attribution line is required, not optional.

    Satisfied either by a line naming this city or by the 'All cities' line --
    every city carries an OSM partition, so the catalogue says it once. The
    per-city form is still accepted so that a future city wired ahead of the
    others can attribute itself.
    """
    name = display_name(code)
    lines = [
        line
        for line in catalog_block("TREE_INVENTORY_SOURCES").splitlines()
        if "OpenStreetMap" in line
        and (f"city: '{name}'" in line or "city: 'All cities'" in line)
    ]
    assert lines, (
        f"{code} ({name}) has an OSM partition but nothing in "
        f"TREE_INVENTORY_SOURCES attributes OpenStreetMap for it -- neither a "
        f"line naming the city nor the 'All cities' line"
    )

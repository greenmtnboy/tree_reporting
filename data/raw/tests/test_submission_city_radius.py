"""The cap that keeps a submission from being recorded as a city it is not in.

A community submission's city is chosen in the browser, by nearest configured
city center, and that answer used to be unbounded.  A tree photographed on
Milos on 2026-08-28 at 09:13 UTC -- 34 minutes before the commit that put
Milos in `cityConfig.json` -- was recorded as Berlin, 1,955 km away.  It was
approved, and `community_tree_info.py` then dropped it for falling outside
`CITY_TERRITORY['DEBER']`, so the tree reached neither city's parquet and
nothing reported it.

Two places now refuse that, and they have to agree:
`CITY_RADIUS_KM` in `src/src/composables/useMapData.ts` (the submit form) and
`CITY_RADIUS_KM` in `reviewer/submissionCity.ts` (the approval gate).  Neither
can import the other -- the reviewer is its own pnpm package and the frontend
constant is behind a Vue import -- so this pins them together, and pins the
number to the measurement that justifies it.

The cap is a coordinate sanity bound and not a boundary.  `CITY_TERRITORY`
remains the authority on which city a tree is in; what the cap catches is the
case that authority handles by silently dropping the row.
"""

from __future__ import annotations

import json
import math
import re
import sys
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
REPO_DIR = RAW_DIR.parent.parent
SRC_DIR = REPO_DIR / "src" / "src"
REVIEWER_DIR = REPO_DIR / "reviewer"
sys.path.insert(0, str(RAW_DIR))

from _ingest_shared import CITY_BOUNDS  # noqa: E402

FRONTEND = SRC_DIR / "composables" / "useMapData.ts"
REVIEWER = REVIEWER_DIR / "submissionCity.ts"


def declared_radius(path: Path) -> int:
    """The `CITY_RADIUS_KM` a TypeScript file exports."""
    match = re.search(r"CITY_RADIUS_KM\s*=\s*(\d+)", path.read_text(encoding="utf-8"))
    assert match, f"{path} declares no CITY_RADIUS_KM"
    return int(match.group(1))


def city_config() -> dict[str, dict]:
    return json.loads((SRC_DIR / "cityConfig.json").read_text(encoding="utf-8"))


def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    radius = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lng / 2) ** 2
    )
    return radius * 2 * math.asin(math.sqrt(a))


def test_the_form_and_the_approval_gate_use_the_same_radius() -> None:
    """A gate looser than the form lets through what the form refuses."""
    assert declared_radius(FRONTEND) == declared_radius(REVIEWER), (
        f"CITY_RADIUS_KM disagrees between {FRONTEND.relative_to(REPO_DIR)} and "
        f"{REVIEWER.relative_to(REPO_DIR)}; the submit form and the approval gate "
        "must refuse the same submissions"
    )


@pytest.mark.parametrize("code", sorted(city_config()))
def test_every_city_fits_inside_the_radius(code: str) -> None:
    """No legitimate tree may fall outside the cap.

    `CITY_BOUNDS` is the generous sanity box a municipal row must fall in, so
    its furthest corner bounds how far from the configured center a real tree
    can sit.  A city whose box outgrows the cap would have its own submissions
    refused -- silently for the submitter, who would simply be told the spot is
    not in the city they are standing in.
    """
    radius = declared_radius(FRONTEND)
    bounds = CITY_BOUNDS.get(code)
    assert bounds, f"{code} is in cityConfig.json but has no CITY_BOUNDS entry"
    lng, lat = city_config()[code]["center"]
    lat_min, lat_max, lng_min, lng_max = bounds
    furthest = max(
        haversine_km(lat, lng, corner_lat, corner_lng)
        for corner_lat in (lat_min, lat_max)
        for corner_lng in (lng_min, lng_max)
    )
    assert furthest <= radius, (
        f"{code}'s CITY_BOUNDS reaches {furthest:.0f} km from its cityConfig.json "
        f"center, past the {radius} km CITY_RADIUS_KM. Raise the cap in both "
        f"{FRONTEND.name} and {REVIEWER.name}, or check the center is the city's."
    )


def test_the_radius_is_not_so_wide_it_would_have_missed_the_bug() -> None:
    """The cap has to be tight enough to catch what it was written for.

    Milos's coordinates against Berlin's center: if a future city addition
    pushes the cap past this, the check stops doing its job.
    """
    radius = declared_radius(FRONTEND)
    berlin = city_config()["DEBER"]["center"]
    distance = haversine_km(36.76573485105567, 24.522211532312724, berlin[1], berlin[0])
    assert distance > radius, (
        f"a {radius} km cap would have accepted the Milos submission as Berlin "
        f"({distance:.0f} km away)"
    )

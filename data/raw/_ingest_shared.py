"""
Shared helpers for city ingest scripts (tree_info.py / landmarks.py).

NOT a uv inline script — this is a regular importable module.
Scripts that only use non-HTTP helpers need pyarrow and pytrilogy; requests is only needed when calling the HTTP helper functions below.

Usage in each city script:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent))
    from _ingest_shared import emit, normalize_species, ...
"""

from __future__ import annotations

import io
import math
import os
import re
import struct
import sys
import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING

import pyarrow as pa
from trilogy.io.arrow import emit_arrow as emit  # noqa: F401  (re-exported)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator

    import requests


# ---------------------------------------------------------------------------
# Species normalisation
# ---------------------------------------------------------------------------

def normalize_species(s: str | None) -> str | None:
    """Capitalize first word, lowercase the rest.  Strip '::' or ' - ' suffixes.

    Returns None for blank / None input.

    Examples:
        "platanus x hispanica"          -> "Platanus x hispanica"
        "Platanus :: London Plane"      -> "Platanus"
        "Quercus robur - English Oak"   -> "Quercus robur"
    """
    if not s or not s.strip():
        return None
    # Strip any common-name suffix separated by "::" or " - "
    for sep in ("::", " - "):
        if sep in s:
            s = s.split(sep)[0]
    parts = s.strip().split()
    if not parts:
        return None
    return " ".join([parts[0].capitalize()] + [p.lower() for p in parts[1:]])


def normalize_species_parts(genus: str | None, epithet: str | None) -> str | None:
    """Combine genus + epithet into a normalised scientific name.

    Genus is capitalised; epithet is lowercased.  Returns None when genus is
    absent or blank (epithet-only is meaningless).

    Examples:
        ("Platanus", "hispanica") -> "Platanus hispanica"
        ("QUERCUS", "ROBUR")     -> "Quercus robur"
        (None, "robur")          -> None
        ("Quercus", None)        -> "Quercus"
    """
    g = (genus or "").strip()
    e = (epithet or "").strip()
    if not g:
        return None
    parts = [g.capitalize()]
    if e:
        parts.append(e.lower())
    return " ".join(parts)


# Inventory placeholders that several portals use for an empty or
# unidentifiable planting site.  They are not taxa and must never reach the
# `species` key, where they would be handed to the enrichment LLM every run.
_SPECIES_PLACEHOLDERS = frozenset(
    {
        "unknown", "unknown tree", "unknown tree species", "unbekannt",
        "onbekend", "onbekend (algemeen)", "no identificado", "unidentified",
        "unidentified unidentified", "undetermined", "not identified",
        "none", "n/a", "na", "nvt", "other", "vacant", "dead", "stump",
        "empty", "tree", "trees", "tree(s)", "arbol", "árbol", "arbre",
        "boom", "baum", "privet", "--", "-",
    }
)

# English common-name nouns that never occur as a Latin specific epithet.  A
# two-word value ending in one of these is a common name ("Pin oak", "Red
# maple"), not a binomial.  Genus-shaped entries (magnolia, catalpa) are safe
# here because they are only ever tested in *epithet* position.
_COMMON_NAME_NOUNS = frozenset(
    {
        "oak", "maple", "elm", "ash", "pine", "spruce", "fir", "cedar",
        "cherry", "plum", "birch", "beech", "linden", "locust", "willow",
        "poplar", "hawthorn", "dogwood", "sycamore", "walnut", "hickory",
        "gum", "holly", "plane", "tree", "palm", "cypress", "hemlock",
        "larch", "alder", "aspen", "buckeye", "chestnut", "catalpa",
        "redbud", "pear", "apple", "crabapple", "magnolia", "ginkgo",
        "juniper", "yew", "laurel", "sweetgum", "cottonwood", "boxelder",
        "fruit", "flower", "fleur", "shrub", "hedge",
    }
)

# Both spellings of the hybrid mark occur in the wild and both are kept
# verbatim: SF publishes "Platanus x hispanica", OSM publishes
# "Citrus × limon", and the enrichment table is already keyed on
# whichever form its city emitted.  Rewriting one into the other here would
# orphan every already-enriched hybrid.
_HYBRID_MARKS = frozenset({"x", "×"})

# Rank qualifiers introduce infraspecific detail the species key does not
# carry; the name is truncated in front of them ("Viburnum cf. corylifolium"
# -> "Viburnum corylifolium" is wrong, so it becomes "Viburnum").
_RANK_QUALIFIERS = frozenset(
    {"cf", "var", "subsp", "ssp", "forma", "f", "spp", "sp", "group", "x"}
)

# A cultivar epithet is always quoted, and whatever trails it is a note
# ("Malus 'spring snow' high brnch"), so the name is truncated at the quote
# rather than having the quoted run excised from the middle.
_QUOTE_CHARS = "'\"‘’“”"


# What a tree whose species we do not know is called.  `species` is a Trilogy
# key, and carrying a real value rather than a null keeps it join-safe
# everywhere without relying on null-matching semantics.  It is excluded from
# enrichment by name via SKIP_SPECIES / SPECIES_EXCLUSION_SQL in
# enrichment/_tree_shared.py -- an explicit, greppable exclusion rather than a
# silent skip.
UNKNOWN_SPECIES = "Unknown"

# Some non-taxa still carry real information: a source that gives up on the
# species but records "Palm" or "Shrub" has told us the growth form, which is
# what drives the map icon and colour.  Merging those into UNKNOWN_SPECIES
# throws that away, so they get their own sentinels instead.  Like
# UNKNOWN_SPECIES they are excluded from enrichment by name -- their
# presentation is hardcoded in src/src/data/species.ts rather than
# guessed by an LLM, because "Palm" is not a taxon and asking a model to
# describe one yields a plausible, specific and wrong answer: the "Unknown"
# row came back as Orania timikae, a critically endangered New Guinea palm,
# and labelled 189k trees across every city until it was purged.
PALM_SPECIES = "Palm"
SHRUB_SPECIES = "Shrub"
CACTUS_SPECIES = "Cactus"

# Values that name a growth form rather than a taxon, in the languages the
# wired cities publish in.  Anything not listed here that fails
# `sanitize_species` merges into UNKNOWN_SPECIES.
_FORM_SENTINEL_ALIASES: dict[str, str] = {
    "palm": PALM_SPECIES,
    "palms": PALM_SPECIES,
    "palm tree": PALM_SPECIES,
    "palmera": PALM_SPECIES,   # es
    "palmeira": PALM_SPECIES,  # pt
    "palmier": PALM_SPECIES,   # fr
    "palme": PALM_SPECIES,     # de
    "shrub": SHRUB_SPECIES,
    "shrubs": SHRUB_SPECIES,
    "bush": SHRUB_SPECIES,
    "hedge": SHRUB_SPECIES,
    "arbusto": SHRUB_SPECIES,  # es/pt
    "arbuste": SHRUB_SPECIES,  # fr
    "struik": SHRUB_SPECIES,   # nl
    "strauch": SHRUB_SPECIES,  # de
    "cactus": CACTUS_SPECIES,
    "cacti": CACTUS_SPECIES,
    "cactaceae": CACTUS_SPECIES,
}

# Every value the `species` key can hold that is not a scientific name.  The
# enrichment scripts and the frontend both key off this set, so a new sentinel
# is added here and picked up in both places.
SPECIES_SENTINELS: frozenset[str] = frozenset(
    {UNKNOWN_SPECIES, PALM_SPECIES, SHRUB_SPECIES, CACTUS_SPECIES}
)


def form_sentinel_for(value: str | None) -> str | None:
    """Return the growth-form sentinel *value* names, or ``None``.

    Called only for values `sanitize_species` has already rejected as taxa, to
    decide whether they merge into ``UNKNOWN_SPECIES`` or keep their form.

    Examples:
        "Palm"    -> "Palm"
        "arbusto" -> "Shrub"
        "Vacant"  -> None   (says nothing about a plant)
    """
    if value is None:
        return None
    return _FORM_SENTINEL_ALIASES.get(value.strip().lower())


def sanitize_species(value: str | None) -> str | None:
    """Reduce a raw species string to a Latin binomial, or ``None``.

    ``normalize_species`` fixes *casing*; this decides whether the value is a
    scientific name at all.  Sources disagree wildly on what they put in a
    species field -- OSM contributors free-type ("Serviceberry or dogwood?",
    "Pin oak", "Malus 'spring snow' high brnch"), and municipal inventories
    use placeholders for empty sites ("Vacant", "Onbekend").  Everything that
    is not a binomial is dropped to ``None`` rather than kept, because the
    `species` key is the join key into the enrichment table: junk there is
    both a permanent LLM cost and a wrong label on the map.

    Returns the genus, optionally followed by a hybrid mark and an epithet.
    Cultivars, rank qualifiers and trailing notes are truncated away.

    Examples:
        "Acer platanoides"              -> "Acer platanoides"
        "Citrus × limon"                -> "Citrus × limon"   (mark preserved)
        "Platanus x hispanica"          -> "Platanus x hispanica"
        "X amelasorbus jackii"          -> "X amelasorbus jackii"
        "Fagus spp"                     -> "Fagus"
        "Malus 'spring snow' high brnch"-> "Malus"
        "Pin oak"                       -> None  (common name)
        "Serviceberry or dogwood?"      -> None
        "Amel. laevis 'spring flurry'"  -> None  (abbreviated genus)
        "Vacant"                        -> None
        "Palm"                          -> None  (a form, not a taxon; see
                                                  form_sentinel_for)
    """
    s = normalize_species(value)
    if s is None:
        return None

    if s.lower() in _SPECIES_PLACEHOLDERS:
        return None
    # "Palm" / "Shrub" / "Cactus" are genus-shaped and would otherwise survive
    # as invented genera; enforce_tree_schema maps them to a form sentinel.
    if s.lower() in _FORM_SENTINEL_ALIASES:
        return None
    # Free-typed uncertainty and any numeric content are never taxa.
    if "?" in s or "/" in s or any(ch.isdigit() for ch in s):
        return None
    if " or " in s.lower():
        return None

    # Truncate at a cultivar quote, dropping the cultivar and any trailing note.
    cut = [s.find(q) for q in _QUOTE_CHARS if s.find(q) != -1]
    if cut:
        s = s[: min(cut)]
    tokens = s.split()
    if not tokens:
        return None

    prefix = ""
    # A leading hybrid mark denotes a nothogenus ("X amelasorbus jackii").
    if tokens[0].lower() in _HYBRID_MARKS:
        prefix = tokens[0]
        tokens = tokens[1:]
        if not tokens:
            return None

    genus = tokens[0]
    # An abbreviated or one/two-letter genus cannot be resolved to a real name.
    if genus.endswith(".") or len(genus) < 3 or not genus.isalpha():
        return None
    if genus.lower() in _SPECIES_PLACEHOLDERS:
        return None

    hybrid = ""
    epithet = ""
    for tok in tokens[1:]:
        low = tok.rstrip(".").lower()
        if tok.lower() in _HYBRID_MARKS and not epithet:
            hybrid = tok
            continue
        if low in _RANK_QUALIFIERS or tok.endswith("."):
            break
        if not tok.isalpha():
            break
        epithet = tok
        break

    # "Pin oak" / "Red maple": a Latin epithet is never an English tree noun.
    if epithet and not hybrid and epithet.lower() in _COMMON_NAME_NOUNS:
        return None

    parts = [p for p in (prefix, genus, hybrid, epithet) if p]
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Data source labels
# ---------------------------------------------------------------------------

# Every tree row carries the dataset it came from.  These values are the
# picklist: they must stay byte-identical to the per-city `{code}_source` enums
# declared in each city's tree model, and each one is the sole source label of
# exactly one raw datasource.  That one-to-one mapping is not cosmetic — a
# city's raw sources declare `complete where city = 'X' and {code}_source = 'Y'`,
# and it is the disjoint source partition that lets Trilogy UNION a city's
# municipal and community rows into one Parquet.  A city whose only source
# claimed `complete where city = 'X'` would leave no room for community rows,
# which is exactly how the first cut of this feature silently dropped every one.
#
# The enums are per-city rather than one global enum because Trilogy proves a
# Parquet complete by checking its sources cover every value of the partitioning
# enum; a 30-value global enum is never covered by one city's two sources.
#
# Keyed by city code so `community_source_for` can derive the community label
# and so tests can assert the two lists agree.
MUNICIPAL_DATA_SOURCES: dict[str, tuple[str, ...]] = {
    "USSFO": ("SF_OPENDATA",),
    "USNYC": ("NYC_OPENDATA",),
    "USBOS": ("CITY_OF_BOSTON", "ARNOLD_ARBORETUM", "CAMBRIDGE", "BROOKLINE"),
    "FRPAR": ("PARIS_OPENDATA",),
    "USBTV": ("BURLINGTON_OPENDATA",),
    "CAVAN": ("VANCOUVER_OPENDATA",),
    "DEBER": ("BERLIN_OPENDATA",),
    "NLAMS": ("AMSTERDAM_OPENDATA",),
    "GBLON": ("LONDON_OPENDATA",),
    "AUMEL": ("MELBOURNE_OPENDATA",),
    "ARBUE": ("BUENOSAIRES_OPENDATA",),
    "USLAX": ("LOSANGELES_OPENDATA",),
    "USWAS": ("WASHINGTONDC_OPENDATA",),
    "USTEM": ("TEMPE_OPENDATA",),
}


def community_source_for(city_code: str) -> str:
    """The `data_source` label for approved community trees in *city_code*."""
    return f"COMMUNITY_{city_code}"


COMMUNITY_DATA_SOURCES: dict[str, str] = {
    code: community_source_for(code) for code in MUNICIPAL_DATA_SOURCES
}

# Cities with a supplemental OpenStreetMap source, keyed to its label.  Opt-in
# per city (unlike community, which every city gets): a city appears here once
# its OSM staging parquet is committed and its tree model declares the
# `OSM_{code}` enum value and staging datasource.  OSM rows overlap the
# municipal inventory by construction, so each wired city also derives an
# `is_duplicate` flag in its model — see the four-grid dedup block in
# ustem/tempe_tree_info.preql for the reference implementation.
OSM_DATA_SOURCES: dict[str, str] = {
    "USTEM": "OSM_USTEM",
    "USBOS": "OSM_USBOS",
}

DATA_SOURCES: tuple[str, ...] = tuple(
    label
    for code in MUNICIPAL_DATA_SOURCES
    for label in (
        *MUNICIPAL_DATA_SOURCES[code],
        COMMUNITY_DATA_SOURCES[code],
        *((OSM_DATA_SOURCES[code],) if code in OSM_DATA_SOURCES else ()),
    )
)


# ---------------------------------------------------------------------------
# Canonical tree output schema
# ---------------------------------------------------------------------------

# Arrow types every city's tree ingest must emit, mirroring the property
# declarations in tree_common.preql.  Trilogy passes these types through to the
# materialised parquet unchanged, so a column left to inference silently
# changes the parquet's physical type: an all-null pa.null() plant_date lands
# as INT32 (breaking year()), and a whole-number dbh read from CSV lands as
# BIGINT instead of DOUBLE.  Enforce rather than infer.
TREE_COLUMN_TYPES: dict[str, pa.DataType] = {
    "tree_id": pa.string(),
    "city": pa.string(),
    "data_source": pa.string(),
    "species": pa.string(),
    "tree_name": pa.string(),
    "plant_date": pa.date32(),
    "latitude": pa.float64(),
    "longitude": pa.float64(),
    "diameter_at_breast_height": pa.float64(),
    "submission_photo_url": pa.string(),
}

# Columns without a `?` prefix in the preql datasources — absence is a bug.
REQUIRED_TREE_COLUMNS = ("tree_id", "city", "data_source", "species")


def enforce_tree_schema(
    table: pa.Table,
    *,
    columns: dict[str, str] | None = None,
    city: str = "",
    data_source: str | None = None,
) -> pa.Table:
    """Cast the tree ingest columns to their canonical Arrow types.

    Every city script calls this immediately before ``emit`` so all cities'
    parquets share identical column types.

    Parameters
    ----------
    table:    The Arrow table about to be emitted.
    columns:  Maps a canonical name to the actual column name in *table*, for
              scripts that emit source-native names — e.g. SF passes
              ``{"tree_id": "treeid", "diameter_at_breast_height": "dbh"}``.
              Canonical names not listed are looked up as-is.
    city:     Optional city name used in error messages.
    data_source: The ``data_source`` enum value every row of this ingest
              carries (e.g. ``"SF_OPENDATA"``).  Appended as a constant column,
              which is what lets Trilogy union a city's municipal and community
              sources as disjoint partitions.  Scripts that emit a per-row
              ``data_source`` column (only the community ingest does) omit it.

    Extra columns (``borough``, …) pass through untouched.
    Casts are *safe*: a lossy conversion raises rather than silently
    truncating values.
    """
    import pyarrow.compute as pc

    prefix = f"{city} ingest" if city else "Ingest"
    overrides = columns or {}
    if data_source is not None:
        if data_source not in DATA_SOURCES:
            raise ValueError(
                f"{prefix}: data_source {data_source!r} is not a known source "
                f"label; add it to MUNICIPAL_DATA_SOURCES here and to that "
                f"city's `{{code}}_source` enum in its tree model together"
            )
        if "data_source" in table.schema.names:
            table = table.drop_columns(["data_source"])
        table = table.append_column(
            "data_source",
            pa.array([data_source] * table.num_rows, type=pa.string()),
        )
    unknown = set(overrides) - set(TREE_COLUMN_TYPES)
    if unknown:
        raise ValueError(
            f"{prefix}: enforce_tree_schema got unknown canonical column(s) "
            f"{sorted(unknown)}; expected any of {sorted(TREE_COLUMN_TYPES)}"
        )

    resolved = {c: overrides.get(c, c) for c in TREE_COLUMN_TYPES}
    names = set(table.schema.names)

    for canonical in REQUIRED_TREE_COLUMNS:
        actual = resolved[canonical]
        if actual not in names:
            raise ValueError(
                f"{prefix}: required column '{actual}' ({canonical}) is missing "
                f"from the emitted table"
            )

    # Species hygiene, applied for every city at the one chokepoint rather than
    # per source.  The raw value is the join key into the enrichment table, so
    # a non-taxon there is both a permanent LLM cost and a wrong map label; see
    # sanitize_species.  Sources vary in how much junk they carry -- OSM's
    # free-typed tags and the municipal "Vacant"/"Onbekend" placeholders are the
    # two big ones -- but none of them are exempt from the binomial contract.
    species_col = resolved["species"]
    if species_col in names:
        sidx = table.schema.get_field_index(species_col)
        raw_species = table.column(sidx).to_pylist()
        cleaned: list[str] = []
        dropped = rewritten = formed = 0
        for value in raw_species:
            keep = sanitize_species(value)
            if keep is not None:
                if keep != value:
                    rewritten += 1
                cleaned.append(keep)
                continue
            # Not a taxon.  Keep the growth form if the source named one --
            # "Palm" says less than a binomial but far more than "Unknown",
            # and it is what the map icon is chosen from.
            sentinel = form_sentinel_for(value)
            if sentinel is not None:
                formed += 1
                cleaned.append(sentinel)
                continue
            if value is not None:
                dropped += 1
            cleaned.append(UNKNOWN_SPECIES)
        table = table.set_column(
            sidx, species_col, pa.array(cleaned, type=pa.string())
        )
        # Never silent: a run that reshapes a tenth of its species column
        # should say so in the refresh log.
        if dropped or rewritten or formed:
            print(
                f"{prefix}: species cleanup -- {dropped} value(s) were not "
                f"scientific names and became {UNKNOWN_SPECIES!r}, "
                f"{rewritten} normalised to species rank, "
                f"{formed} kept as a growth-form sentinel",
                file=sys.stderr,
            )

    for canonical, target in TREE_COLUMN_TYPES.items():
        actual = resolved[canonical]
        if actual not in names:
            continue
        idx = table.schema.get_field_index(actual)
        current = table.schema.field(idx).type
        if current.equals(target):
            continue
        try:
            cast = pc.cast(table.column(idx), target)
        except (pa.ArrowInvalid, pa.ArrowNotImplementedError) as e:
            raise ValueError(
                f"{prefix}: column '{actual}' ({canonical}) has type {current}, "
                f"which cannot be safely cast to the canonical {target}: {e}"
            ) from e
        table = table.set_column(idx, actual, cast)

    # Backfill the optional columns this source has no value for, as typed
    # nulls.  Every ingest then emits the identical column set, so a preql
    # datasource can map `submission_photo_url: ?submission_photo_url` uniformly across cities that do
    # and don't have photos without the SELECT failing on a missing column.
    for canonical, target in TREE_COLUMN_TYPES.items():
        actual = resolved[canonical]
        if actual in table.schema.names:
            continue
        table = table.append_column(
            actual, pa.nulls(table.num_rows, type=target)
        )

    return table


# ---------------------------------------------------------------------------
# Coordinate validation & bounding-box filtering
# ---------------------------------------------------------------------------

# Generous bounding boxes per city code — wide enough for metro-area trees,
# tight enough to catch wrong-hemisphere / wrong-continent geocoding errors.
# Format: (lat_min, lat_max, lon_min, lon_max)
CITY_BOUNDS: dict[str, tuple[float, float, float, float]] = {
    "USSFO": (37.60, 37.90, -122.60, -122.30),
    "USNYC": (40.45, 40.95, -74.30, -73.65),
    "USBOS": (42.15, 42.55, -71.25, -70.85),
    "FRPAR": (48.70, 49.05, 2.10, 2.60),
    "USBTV": (44.35, 44.60, -73.35, -73.10),
    "CAVAN": (49.10, 49.40, -123.30, -122.95),
    "DEBER": (52.30, 52.70, 13.05, 13.80),
    "NLAMS": (52.25, 52.45, 4.70, 5.10),
    "GBLON": (51.25, 51.75, -0.55, 0.35),
    "AUMEL": (-38.10, -37.55, 144.55, 145.40),
    "ARBUE": (-34.80, -34.45, -58.55, -58.30),
    "USLAX": (33.70, 34.35, -118.70, -118.10),
    "USWAS": (38.78, 39.01, -77.15, -76.88),
    "USTEM": (33.30, 33.48, -112.05, -111.80),
}


def validate_coordinates(
    table: pa.Table,
    city: str = "",
    city_code: str = "",
    threshold: float = 0.10,
) -> pa.Table:
    """Validate and filter coordinates, returning the cleaned table.

    1. Raises ValueError if the table has 0 rows or >threshold null lat/lon.
    2. If *city_code* matches a CITY_BOUNDS entry, drops rows outside the
       bounding box and logs the count to stderr.

    Parameters
    ----------
    table:      The Arrow table to validate.
    city:       Optional city name used in error messages.
    city_code:  City code (e.g. "USSFO") for bounding-box filtering.
    threshold:  Maximum allowed null fraction for latitude/longitude (default 10%).

    Returns
    -------
    The table with out-of-bounds rows removed (if a bounding box was applied).
    """
    import pyarrow.compute as pc

    n = table.num_rows
    prefix = f"{city} ingest" if city else "Ingest"
    if n == 0:
        raise ValueError(f"{prefix} produced 0 rows")
    for col in ("latitude", "longitude"):
        null_count = table.column(col).null_count
        if null_count == n:
            raise ValueError(
                f"{prefix}: '{col}' is NULL for all {n} rows — "
                "coordinate extraction failed"
            )
        null_pct = null_count / n
        if null_pct > threshold:
            raise ValueError(
                f"{prefix}: '{col}' is NULL for {null_pct:.0%} of rows "
                f"({null_count}/{n})"
            )

    bounds = CITY_BOUNDS.get(city_code)
    if bounds:
        lat_min, lat_max, lon_min, lon_max = bounds
        mask = (
            pc.and_(
                pc.and_(
                    pc.greater_equal(table["latitude"], lat_min),
                    pc.less_equal(table["latitude"], lat_max),
                ),
                pc.and_(
                    pc.greater_equal(table["longitude"], lon_min),
                    pc.less_equal(table["longitude"], lon_max),
                ),
            )
        )
        filtered = table.filter(mask)
        dropped = n - filtered.num_rows
        if dropped:
            print(
                f"{prefix}: dropped {dropped} rows outside {city_code} bounds "
                f"({lat_min}–{lat_max}°N, {lon_min}–{lon_max}°E)",
                file=sys.stderr,
            )
        return filtered

    return table


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

# Overpass API rejects the default `python-requests/...` User-Agent with
# HTTP 406 ("Not Acceptable"). OSM etiquette also requires identifying the
# application. Cities that fetch from Overpass should pass these headers to
# `post_with_retry` / `get_with_retry`.
OVERPASS_HEADERS = {
    "User-Agent": "sf-tree-reporting/1.0 (https://github.com/greenmtnboy/sf_tree_reporting)"
}


class UpstreamUnavailable(RuntimeError):
    """An open data portal did not serve usable data.

    Covers everything that is the *portal's* problem rather than ours: connection
    errors, 5xx, 429, and 2xx responses whose body is not what the endpoint
    documents (a maintenance page served with HTTP 200 is the common one).
    Distinct from a parse error against a genuine payload, which means our field
    mapping is wrong and must stay loud.

    Subclasses RuntimeError so callers that only catch RuntimeError still work.
    """


def _body_snippet(response: requests.Response, limit: int = 160) -> str:
    """A one-line, truncated preview of a response body for error messages."""
    text = " ".join((response.text or "").split())
    # ASCII ellipsis: probe stderr lands in job logs with unpredictable encodings.
    return text[:limit] + ("..." if len(text) > limit else "")


# Overpass reports overload the same way whether it ran out of time or memory;
# anything else in `remark` (attribution notes, tag advisories) is not a failure.
_OVERPASS_FAILURE_RE = re.compile(
    r"runtime error|timed out|out of memory|too many requests", re.I
)


def error_envelope(payload) -> str | None:
    """A server-side error reported *inside* an HTTP 200 JSON body, if present.

    The GIS platforms these ingests use all answer a failed query with 200 and
    an error object rather than a 5xx — ArcGIS returns
    ``{"error": {"code": 500, "message": "Error performing query operation"}}``
    for a statistics query its backend could not run.  Left unclassified, that
    reaches the caller as a well-formed payload with no rows, and every probe's
    "no features" guard turns a portal hiccup into a fatal error.
    """
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    # ArcGIS / CKAN: a nested error object.
    if isinstance(error, dict) and (error.get("message") or error.get("code")):
        return f"{error.get('code', 'error')}: {error.get('message', '')}".strip()
    # Socrata: {"error": true, "message": "..."}
    if error is True:
        return str(payload.get("message") or "error")
    # CKAN failure with no error object.
    if payload.get("success") is False:
        return str(error or "success=false")
    # Overpass: an overloaded or timed-out query is HTTP 200 with a well-formed
    # body, an empty `elements` list and the failure in `remark`:
    #   {"elements": [], "remark": "runtime error: Query timed out in
    #    \"query\" at line 3 after 180 seconds."}
    # There is no `error` key, so without this the caller sees a valid payload
    # with no rows and its own "no features" guard turns an Overpass hiccup
    # into a fatal, unretried error.  `remark` is also used for benign notes,
    # so only the failure wordings count.
    remark = payload.get("remark")
    if isinstance(remark, str) and _OVERPASS_FAILURE_RE.search(remark):
        return f"overpass remark: {remark.strip()}"
    return None


def response_json(response: requests.Response, url: str):
    """Decode a JSON response, or raise UpstreamUnavailable describing the body.

    ``response.json()`` on an HTML maintenance page raises a bare
    ``JSONDecodeError: Expecting value: line 2 column 9``, which names neither
    the host nor what it actually served — the failure mode that made a
    gdi.berlin.de outage read like a bug in the probe.  This reports the status,
    content type, size and the first line of the body instead.

    A 200 carrying a provider error envelope is treated the same way: it is the
    portal saying it could not serve the request, so it is worth retrying and
    worth degrading on, not worth failing the whole refresh over.
    """
    try:
        payload = response.json()
    except ValueError as e:
        content_type = response.headers.get("Content-Type") or "unset"
        raise UpstreamUnavailable(
            f"{url} returned HTTP {response.status_code} with a non-JSON body "
            f"(content-type {content_type}, {len(response.content)} bytes): "
            f"{_body_snippet(response)!r} ({e})"
        ) from e
    detail = error_envelope(payload)
    if detail:
        raise UpstreamUnavailable(
            f"{url} returned HTTP {response.status_code} with an error payload: "
            f"{detail}"
        )
    return payload


def _retry(
    attempt: "Callable[[], object]",
    *,
    url: str,
    what: str,
    max_retries: int,
    backoff: float,
):
    """Call *attempt* until it succeeds, backing off on UpstreamUnavailable.

    *attempt* raises UpstreamUnavailable for a failure worth retrying; any other
    exception (a 4xx, a bad field mapping) propagates on the first try.
    """
    err = ""
    for i in range(max_retries):
        try:
            return attempt()
        except UpstreamUnavailable as e:
            err = str(e)
        if i < max_retries - 1:
            wait = backoff * (2 ** i)
            print(
                f"[retry {i + 1}/{max_retries}] {err}, waiting {wait:.0f}s",
                file=sys.stderr,
            )
            time.sleep(wait)
    raise UpstreamUnavailable(
        f"Failed to {what} {url} after {max_retries} attempts: {err}"
    )


def _send(method: str, url: str, **kwargs) -> requests.Response:
    """One HTTP attempt, classified: retryable failures raise UpstreamUnavailable.

    4xx other than 429 raise HTTPError immediately — auth, forbidden and not
    found are client-side problems that retrying cannot fix.
    """
    import requests

    try:
        r = requests.request(method, url, **kwargs)
    except requests.exceptions.RequestException as e:
        raise UpstreamUnavailable(str(e)) from e
    if r.status_code < 400:
        return r
    if 400 <= r.status_code < 500 and r.status_code != 429:
        r.raise_for_status()  # raises immediately, no retry
    raise UpstreamUnavailable(f"HTTP {r.status_code}")


def get_with_retry(
    url: str,
    timeout: int = 120,
    max_retries: int = 5,
    backoff: float = 2.0,
    headers: dict | None = None,
    params: dict | None = None,
) -> requests.Response:
    """GET with exponential backoff on 5xx / connection errors.

    4xx errors (except 429 Too Many Requests) are not retried — they indicate
    a client-side problem (auth, forbidden, not found) that retrying won't fix.

    Callers expecting JSON should use `get_json_with_retry`, which also retries
    a 2xx whose body isn't JSON.
    """
    return _retry(
        lambda: _send("GET", url, timeout=timeout, headers=headers, params=params),
        url=url,
        what="fetch",
        max_retries=max_retries,
        backoff=backoff,
    )


def get_json_with_retry(
    url: str,
    timeout: int = 120,
    max_retries: int = 5,
    backoff: float = 2.0,
    headers: dict | None = None,
    params: dict | None = None,
):
    """GET and decode JSON, retrying non-JSON 2xx bodies as well as 5xx.

    Portals in maintenance often answer *every* path with HTTP 200 and an HTML
    holding page — gdi.berlin.de serves a 1.4 KB "Wartungsarbeiten" page for the
    WFS and the metadata API alike.  That is a transient outage, so it is worth
    the same backoff as a 503, and worth an error message that says so.
    """

    def attempt():
        response = _send("GET", url, timeout=timeout, headers=headers, params=params)
        return response_json(response, url)

    return _retry(
        attempt, url=url, what="fetch JSON from", max_retries=max_retries, backoff=backoff
    )


def post_with_retry(
    url: str,
    data: dict,
    timeout: int = 240,
    max_retries: int = 5,
    backoff: float = 10.0,
    headers: dict | None = None,
) -> requests.Response:
    """POST with exponential backoff on 5xx / connection errors.

    Longer default backoff than get_with_retry — suited to Overpass API.
    4xx errors (except 429) are not retried.
    """
    return _retry(
        lambda: _send("POST", url, data=data, timeout=timeout, headers=headers),
        url=url,
        what="POST",
        max_retries=max_retries,
        backoff=backoff,
    )


def post_json_with_retry(
    url: str,
    data: dict,
    timeout: int = 240,
    max_retries: int = 5,
    backoff: float = 10.0,
    headers: dict | None = None,
):
    """POST and decode JSON, retrying non-JSON 2xx bodies as well as 5xx.

    Overpass in particular answers an overloaded instance with a 200 HTML error
    page rather than the JSON its API documents.
    """

    def attempt():
        response = _send("POST", url, data=data, timeout=timeout, headers=headers)
        return response_json(response, url)

    return _retry(
        attempt,
        url=url,
        what="POST JSON to",
        max_retries=max_retries,
        backoff=backoff,
    )


def download_parquet(url: str, timeout: int = 300) -> io.BytesIO:
    """Stream-download a parquet file into a BytesIO buffer and return it seeked to 0."""
    import requests

    r = requests.get(url, stream=True, timeout=timeout)
    r.raise_for_status()
    buf = io.BytesIO()
    for chunk in r.iter_content(chunk_size=1024 * 1024):
        if chunk:
            buf.write(chunk)
    buf.seek(0)
    return buf


# ---------------------------------------------------------------------------
# Staged sources
# ---------------------------------------------------------------------------

# Some sources are too fragile to hit during a refresh.  Overpass allows two
# concurrent slots per client IP and answers an over-budget request with HTTP
# 200 carrying an error remark, so a refresh at `parallelism = 3` could fail a
# city on a transient -- `london_landmark_info` died that way and took
# `full_landmark_info` with it, while the same script run alone finished in
# 6.8s.  Those sources are *staged*: an extract script fetches them on its own
# schedule and writes a parquet here, and the refresh only ever reads the
# staged copy.
#
# The staging objects live in GCS rather than in the repo.  They were committed
# at first, which worked but made the freshness signal a lie: the probes read
# the file's mtime, and **git does not preserve mtime**.  Every fresh clone --
# which is every cloud job run -- stamps the checkout time, so the watermark
# advanced on every tick and Boston and Tempe rebuilt three times a day.  That
# is the same every-tick thrash the staging design set out to avoid; it had
# only swapped Overpass's minute-resolution timestamp for a checkout timestamp.
# A GCS object's Last-Modified is a real publication time that survives
# cloning, so `staging_modified_at` is a watermark that only moves when an
# extract is actually re-run.
STAGING_BASE_URL = "https://storage.googleapis.com/trilogy_public_models/duckdb/staging"
STAGING_GCS_PREFIX = "gs://trilogy_public_models/duckdb/staging"


def staging_url(name: str) -> str:
    """Public read URL for a staged parquet, for a preql `file` clause."""
    return f"{STAGING_BASE_URL}/{name}"


def staging_gcs_uri(name: str) -> str:
    """`gs://` write URI for a staged parquet."""
    return f"{STAGING_GCS_PREFIX}/{name}"


def staging_modified_at(name: str) -> datetime:
    """Publication time of a staged parquet, from the GCS object metadata.

    Returns the epoch when the object does not exist, matching how a missing
    staging file behaved when these lived on disk: an absent optional source
    sits out the run rather than aborting it.  A transport failure raises
    `UpstreamUnavailable` so `emit_freshness` degrades the same way.

    The objects are served with `Cache-Control: max-age=3600`, so the request
    carries a cache-buster -- without one a probe run just after an extract
    would read the previous publication time and call the city fresh.
    """
    import requests

    url = f"{staging_url(name)}?cb={int(time.time())}"
    try:
        response = requests.head(url, timeout=30, allow_redirects=True)
    except requests.RequestException as err:
        raise UpstreamUnavailable(f"Failed to HEAD staged object {name}: {err}") from err
    if response.status_code == 404:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if response.status_code >= 400:
        raise UpstreamUnavailable(
            f"HEAD {url} returned HTTP {response.status_code}"
        )
    header = response.headers.get("Last-Modified")
    if not header:
        # Not an availability problem: GCS always sends this, so its absence
        # means the URL is not the object we think it is.
        raise RuntimeError(f"staged object {name} has no Last-Modified header")
    return parsedate_to_datetime(header).astimezone(timezone.utc)


def upload_staging(local_path, name: str) -> None:
    """Publish a locally written staging parquet to GCS.

    Called by the extract scripts, which run on their own schedule and are the
    only writers.  Uploading is what makes the city's Parquet stale, so it is
    also the moment the refresh is allowed to notice the new data.
    """
    from google.cloud import storage as gcs

    uri = staging_gcs_uri(name)
    bucket_name, _, blob_name = uri[len("gs://"):].partition("/")
    blob = gcs.Client().bucket(bucket_name).blob(blob_name)
    blob.upload_from_filename(str(local_path))
    print(f"uploaded {local_path} -> {uri}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Streaming ingest
# ---------------------------------------------------------------------------

# A city ingest that accumulates every source record before transforming holds
# the whole dataset as Python dicts, which is the most expensive representation
# available: Amsterdam's 325k records peaked at 882 MB of Python heap and failed
# every cloud refresh that actually rebuilt it, while passing locally every
# time.  The failure is latent rather than absent for the others -- a city is
# only rebuilt when its source updates, so an ingest can sit oversized for
# months and break the day its portal publishes.
#
# The fix is always the same shape: yield a chunk of records, convert it to
# Arrow, drop the dicts.  Peak memory becomes one chunk plus the accumulated
# columnar data, which is roughly a tenth of the dict form and does not grow
# with the number of chunks.  These helpers exist so a new city gets that for
# free instead of reinventing the accumulate-everything loop.

DEFAULT_CHUNK_ROWS = 50_000


def stream_to_table(
    chunks: Iterable[list[dict]],
    transform: Callable[[list[dict]], pa.Table],
    *, keep: Callable[[dict], bool] | None = None,
    label: str = "",
) -> pa.Table:
    """Transform each chunk to Arrow and concatenate.

    The counterpart to the `iter_*` helpers below and the piece that actually
    bounds memory: `transform` runs per chunk, so the dicts for a chunk become
    garbage as soon as its Arrow table exists.

    `keep` filters records before transforming, for sources that carry rows
    which are not trees at all (Amsterdam publishes tree stumps alongside
    trees).  Filtering here rather than after the concat means the dropped rows
    never occupy a column.
    """
    tables: list[pa.Table] = []
    seen = kept = 0
    for chunk in chunks:
        seen += len(chunk)
        if keep is not None:
            chunk = [r for r in chunk if keep(r)]
        kept += len(chunk)
        if chunk:
            tables.append(transform(chunk))
    if not tables:
        raise RuntimeError(f"{label or 'ingest'}: source produced no rows")
    table = pa.concat_tables(tables)
    print(
        f"{label or 'ingest'}: streamed {seen} record(s) in {len(tables)} chunk(s)"
        + (f", {seen - kept} filtered out" if seen != kept else ""),
        file=sys.stderr,
    )
    return table


def iter_link_pages(
    url: str, *, rows_key: str, next_key: str = "next", **kwargs
) -> Iterator[list[dict]]:
    """Pages from an API that advertises the next page as a link.

    The HAL/DSO shape: `_embedded.<rows_key>` holds the records and
    `_links.<next_key>.href` the next page, absent on the last.
    """
    while url:
        data = get_json_with_retry(url, **kwargs)
        yield data.get("_embedded", {}).get(rows_key, [])
        link = data.get("_links", {}).get(next_key, {})
        url = link.get("href") if isinstance(link, dict) else None


def iter_offset_pages(
    fetch_page: Callable[[int], list[dict]], *, page_size: int
) -> Iterator[list[dict]]:
    """Pages from an API paged by offset, stopping on a short or empty page.

    `fetch_page(offset)` returns that page's records.  Covers both the Socrata
    `$offset`/`$limit` and WFS `startIndex`/`COUNT` spellings -- the caller
    supplies the request, this owns the loop and the termination rule.
    """
    offset = 0
    while True:
        batch = fetch_page(offset)
        if not batch:
            return
        yield batch
        if len(batch) < page_size:
            return
        offset += page_size


def iter_csv_row_chunks(
    url: str,
    *,
    chunk_rows: int = DEFAULT_CHUNK_ROWS,
    delimiter: str | None = None,
    timeout: int = 600,
) -> Iterator[list[dict]]:
    """Chunks of dict rows from a remote CSV, without holding the whole file.

    Streams the body to a temporary file and reads it back with
    `csv.DictReader`, so peak memory is one chunk rather than the file text
    *and* a dict per row simultaneously -- London's 1.1M-row CSV was doing
    both.  The temporary file is removed on the way out.

    The delimiter is sniffed from the header when not given, since these
    exports are inconsistently comma- and semicolon-separated.
    """
    import csv
    import tempfile

    import requests

    handle, path = tempfile.mkstemp(suffix=".csv")
    os.close(handle)
    try:
        with requests.get(url, stream=True, timeout=timeout) as r:
            r.raise_for_status()
            with open(path, "wb") as fh:
                for block_ in r.iter_content(chunk_size=1024 * 1024):
                    if block_:
                        fh.write(block_)
        # utf-8-sig strips a BOM when present; latin-1 never fails, so it is a
        # safe last resort for these municipal exports.
        for encoding in ("utf-8-sig", "latin-1"):
            try:
                with open(path, "r", encoding=encoding, newline="") as fh:
                    header = fh.readline()
                    if delimiter is None:
                        sep = max(",;	|", key=header.count)
                    else:
                        sep = delimiter
                    fh.seek(0)
                    reader = csv.DictReader(fh, delimiter=sep)
                    chunk: list[dict] = []
                    for row in reader:
                        chunk.append(row)
                        if len(chunk) >= chunk_rows:
                            yield chunk
                            chunk = []
                    if chunk:
                        yield chunk
                return
            except UnicodeDecodeError:
                continue
        raise RuntimeError(f"could not decode CSV at {url}")
    finally:
        try:
            os.remove(path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Freshness probes
# ---------------------------------------------------------------------------

# What a probe emits when its portal is unreachable.  It loses every
# `greatest()` against a real timestamp, so the city's Parquet compares as fresh
# and is skipped for this run rather than rebuilt from a portal that is down.
PORTAL_UNAVAILABLE_TIMESTAMP = datetime(1970, 1, 1, tzinfo=timezone.utc)


def emit_freshness(
    city_code: str | None,
    fetch: "Callable[[], datetime]",
    *,
    label: str = "",
) -> None:
    """Emit the one-row freshness table Trilogy probes, tolerating a dead portal.

    *city_code* is the five-letter code the probe reports for, emitted as the
    `city` column; pass None for a probe with no city (the ecoregion layer) and
    name it with *label* for the log line instead.

    Every tree and landmark probe is a root datasource feeding some Parquet's
    `freshness by`, and Trilogy collects those watermarks in one un-isolated
    planning phase: `_collect_root_watermarks` calls `future.result()` with no
    per-probe guard, so a single probe raising ends the whole `trilogy refresh
    raw` command before any asset is refreshed.  One city's portal being in
    maintenance therefore fails all fourteen cities plus landmarks and
    enrichment — the same blast radius the community 403 had (see EXTENDING.md).

    So an *availability* failure (connection error, 5xx, 429, or a 2xx that
    isn't the documented payload) degrades to PORTAL_UNAVAILABLE_TIMESTAMP with
    a loud stderr note: this city sits out the run and the next scheduled tick
    picks it up once the portal is back.

    A *parse* failure does not degrade.  A KeyError or a bad date against a
    genuine payload means our field mapping drifted from the portal's schema,
    and silently reporting "no new data" would freeze the city's Parquet
    indefinitely with nothing in the logs.  Those still abort, loudly.
    """
    try:
        updated_at = fetch()
    except UpstreamUnavailable as e:
        # One line, and short.  A degrading probe is the *expected* path when a
        # portal is down, but the retry helper's message quotes the offending
        # body, and a run's captured stderr is a tail: Berlin in maintenance
        # printed its 1.4 KB holding page ten times over and pushed the
        # traceback of a genuinely failing asset clean out of the window, which
        # cost two diagnostic round trips on an unrelated bug.  A probe that is
        # working as designed must not be able to hide the errors of one that
        # is not.
        detail = " ".join(str(e).split())
        if len(detail) > 200:
            detail = detail[:200] + "…"
        print(
            f"{label or city_code} freshness probe: portal unavailable "
            f"({detail}); reporting no new data so the refresh can proceed",
            file=sys.stderr,
        )
        updated_at = PORTAL_UNAVAILABLE_TIMESTAMP
    columns = {}
    if city_code is not None:
        columns["city"] = pa.array([city_code], type=pa.string())
    columns["data_updated_through"] = pa.array(
        [updated_at], type=pa.timestamp("us", tz="UTC")
    )
    emit(pa.table(columns))


# ---------------------------------------------------------------------------
# WKB / WKT geometry helpers
# ---------------------------------------------------------------------------

def parse_wkb_point(wkb: bytes | None) -> tuple[float | None, float | None]:
    """Parse a WKB binary Point into (lon, lat).

    OpenDataSoft exports geo_point_2d as WKB:
      byte 0   : byte order (1 = little-endian, 0 = big-endian)
      bytes 1-4: geometry type (uint32, value 1 = Point)
      bytes 5-12: x (double) = longitude
      bytes 13-20: y (double) = latitude

    Returns (None, None) for None or too-short input.
    """
    if wkb is None or len(wkb) < 21:
        return None, None
    bo = "<" if wkb[0] == 1 else ">"
    x, y = struct.unpack_from(bo + "dd", wkb, 5)
    return x, y


def make_point_wkt(lon, lat) -> str | None:
    """Return a WKT POINT string or None if either coordinate is None."""
    if lon is None or lat is None:
        return None
    return f"POINT({lon} {lat})"


# ---------------------------------------------------------------------------
# RD New (EPSG:28992) → WGS84
# ---------------------------------------------------------------------------

def rd_to_wgs84(x: float, y: float) -> tuple[float, float]:
    """Convert RD New (x, y) to (lat, lon) in WGS84.

    Polynomial approximation (~1 m accuracy).
    Coefficients from the Dutch Kadaster / RDNAPTRANS reference.
    """
    x0, y0 = 155000.0, 463000.0
    phi0, lam0 = 52.15517440, 5.38720621

    dx = (x - x0) * 1e-5
    dy = (y - y0) * 1e-5

    coefs_phi = [
        (0, 1, 3235.65389),
        (2, 0, -32.58297),
        (0, 2, -0.24750),
        (2, 1, -0.84978),
        (0, 3, -0.06550),
        (2, 2, -0.01709),
        (1, 0, -0.00738),
        (4, 0, 0.00530),
        (2, 3, -0.00039),
        (4, 1, 0.00033),
        (1, 1, -0.00012),
    ]
    coefs_lam = [
        (1, 0, 5260.52916),
        (1, 1, 105.94684),
        (1, 2, 2.45656),
        (3, 0, -0.81885),
        (1, 3, 0.05594),
        (3, 1, -0.05607),
        (0, 1, 0.01199),
        (3, 2, -0.00256),
        (1, 4, 0.00128),
        (0, 2, 0.00022),
        (2, 0, -0.00022),
        (5, 0, 0.00026),
    ]

    dphi = sum(c * (dx ** p) * (dy ** q) for p, q, c in coefs_phi)
    dlam = sum(c * (dx ** p) * (dy ** q) for p, q, c in coefs_lam)

    lat = phi0 + dphi / 3600.0
    lon = lam0 + dlam / 3600.0
    return lat, lon


def rd_centroid(ring: list) -> tuple[float, float]:
    """Return (mean_x, mean_y) of a coordinate ring (RD New)."""
    xs = [c[0] for c in ring]
    ys = [c[1] for c in ring]
    return sum(xs) / len(xs), sum(ys) / len(ys)


# ---------------------------------------------------------------------------
# Plant date helpers
# ---------------------------------------------------------------------------

def parse_plant_date_year(year) -> date | None:
    """Convert an integer or string year to January 1 of that year.

    Returns None for None, 0, negative values, values > 2100, or
    non-numeric input.
    """
    if year is None:
        return None
    try:
        y = int(year)
    except (ValueError, TypeError):
        return None
    if y <= 0 or y > 2100:
        return None
    return date(y, 1, 1)


# ---------------------------------------------------------------------------
# DBH / dimension conversion helpers
# ---------------------------------------------------------------------------

def circumference_cm_to_dbh_inches(circ_cm) -> float | None:
    """Convert trunk circumference in cm to diameter at breast height in inches.

    DBH = circumference / π, then convert cm → inches (÷ 2.54).
    Returns None for None or zero input.
    """
    if circ_cm is None:
        return None
    try:
        v = float(circ_cm)
    except (ValueError, TypeError):
        return None
    if v == 0:
        return None
    return v / (math.pi * 2.54)


def cm_to_inches(cm) -> float | None:
    """Convert a centimetre value to inches (÷ 2.54).  Returns None for None."""
    if cm is None:
        return None
    try:
        return float(cm) / 2.54
    except (ValueError, TypeError):
        return None

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pillow", "instructor[litellm]", "duckdb", "google-genai", "jsonref", "pytrilogy", "pydantic", "google-cloud-storage"]
# ///
"""Localhost admin UI for hand-editing the species enrichment parquet.

The enrichment table (`tree_enrichment_v{n}.parquet` in GCS) is the one row
per species that every tree row joins to: its common name, growth form, traits,
native range and photo.  It is written by an LLM run, and the LLM is sometimes
wrong -- a photo of a different plant, a description of the wrong taxon, a
trait that reads as off.  `backfill_enrichment.py` can re-ask the model, but a
reviewer who already knows the answer should be able to just write it down.

This serves a small form over the table.  Edits are staged locally and land in
GCS only on an explicit publish, which:

  1. re-reads the *current* published table rather than uploading the copy this
     process loaded -- the daily `refresh-enrichment` job may have appended new
     species in the meantime, and uploading a stale snapshot would drop them;
  2. patches the staged rows into it, and the same values into every alias
     row of the taxon -- its hybrid-mark twin and each name in `synonyms`
     (see `with_species_aliases`); and
  3. writes a local parquet, uploads it, and reads it back to verify, exactly
     the way `tree_enrichment.py --limit` does.

An edited row carries today's `enriched_at`, which is what keeps the daily job
from re-enriching it: the retry window in `_tree_shared.REENRICH_INCOMPLETE_BEFORE`
is a fixed date, and a row stamped after it is left alone.  The row must still
clear `ENRICHMENT_COMPLETE_SQL` (a common name and a growth form) or the
freshness probe would report the table stale on every tick, so the form
refuses to save without them.

Sentinel rows ("Unknown", "Palm", ...) are authored in `_ingest_shared` and
re-appended on every run, so they cannot be edited here; fix them in code.

Synonyms
--------
`synonyms` lists the other scientific names of the taxon.  Adding a name there
is how a reviewer folds a duplicate row into this one: on publish the
duplicate's row is overwritten with this row's values (its own `synonyms` then
pointing back here), and the two stay in step from then on whichever is
edited.  That bridges the join for tree rows still carrying the old name; it
does not change what the ingest publishes.  For that the pair goes into
`SPECIES_SYNONYMS` in `_ingest_shared.py`, after which the synonym's row is an
alias the daily job maintains and the form shows read-only.

A duplicate that is not a synonym but a *misspelling* -- `Acer platenoides`,
which POWO cannot match at all -- takes the same two steps and lands in
`SPECIES_MISSPELLINGS` instead.  The difference is only in what the pair
claims: a misspelling never appears in the accepted row's `synonyms`, because
that column says what else the taxon is called and a typo is not one of its
names.  The form treats both the same way, read-only and pointing here.

Run
---
    gcloud auth application-default login        # once
    cd data/raw && uv run enrichment_admin.py

then open http://127.0.0.1:4175.  The server binds to localhost only.  Set
ENRICHMENT_ADMIN_PORT to change the port.

The species table is a public object, so browsing needs no credentials; only
publish does.  The website (arborary.world) picks the change up on its next
build -- a `workflow_dispatch` of its deploy workflow republishes with fresh
data -- and the map on the next `refresh-enrichment` tick is unaffected, since
the browser reads the parquet directly.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from random import randint
from urllib.parse import parse_qs, unquote, urlparse

import pyarrow as pa
import pyarrow.parquet as pq

RAW_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(RAW_DIR))

from _ingest_shared import (  # noqa: E402
    SPECIES_MISSPELLINGS,
    SPECIES_SYNONYMS,
    _sanitize_taxon,
    synonyms_of,
)
from enrichment._tree_shared import (  # noqa: E402
    DATA_VERSION,
    ENRICHMENT_GCS_URI,
    SKIP_SPECIES,
    SPECIES_SENTINELS,
    TREE_INFO_PARQUET,
    alias_keys,
    normalize_common_names,
    other_names,
)

# The pieces of tree_enrichment.py we reuse, loaded without running its
# __main__ -- the same trick backfill_enrichment.py uses.
import importlib.util as _ilu  # noqa: E402

_te_spec = _ilu.spec_from_file_location("tree_enrichment", RAW_DIR / "tree_enrichment.py")
_te = _ilu.module_from_spec(_te_spec)  # type: ignore[arg-type]
_te_spec.loader.exec_module(_te)  # type: ignore[union-attr]

SCHEMA: pa.Schema = _te.SCHEMA
HTML = RAW_DIR / "enrichment_admin.html"
# Staged edits survive a restart.  Gitignored.
EDITS_PATH = RAW_DIR / "enrichment_admin_edits.json"
# What publish writes before uploading; `data/raw/*.parquet` is gitignored.
PUBLISH_PATH = RAW_DIR / "tree_enrichment_admin_publish.parquet"
PUBLISHED_URL = ENRICHMENT_GCS_URI.replace("gs://", "https://storage.googleapis.com/")
# Versioned like the enrichment table.  `_ecoregion_shared.REMOTE_ECOREGION_PARQUET`
# still names v1, which is gone from the bucket.
ECOREGION_URL = f"https://storage.googleapis.com/trilogy_public_models/duckdb/trees/ecoregion_info_v{DATA_VERSION}.parquet"


def published_url() -> str:
    """A cache-busted URL for the published table.

    `_tree_shared.ENRICHMENT_PARQUET` carries one random `cb` per process, so
    reading it twice in one process can hand back a cached copy.  Reload and
    publish both need the object as it is *now*.
    """
    return f"{PUBLISHED_URL}?cb={randint(0, 2**32)}"


# ── Field vocabulary ───────────────────────────────────────────────────────────
#
# The enums are the values the LLM prompt (`_tree_enrichment_models.py`) is
# allowed to return, which is also what the published table actually holds --
# note `non-invasive` and `part_shade`, not the `non_invasive` / `partial_shade`
# spellings the preql comments mention.  Anything else here would be a value no
# other row has and no consumer looks for.

LOW_MOD_HIGH = ["low", "moderate", "high"]
ENUMS: dict[str, list[str]] = {
    "growth_rate": ["slow", "moderate", "fast"],
    "drought_tolerance": LOW_MOD_HIGH,
    "water_needs": LOW_MOD_HIGH,
    "pollution_tolerance": LOW_MOD_HIGH,
    "wildlife_value": LOW_MOD_HIGH,
    "fire_risk": LOW_MOD_HIGH,
    "root_behavior": ["non-invasive", "moderate", "invasive"],
    "tree_form": [
        "broadleaf", "conifer", "palm", "columnar", "ornamental",
        "spreading", "weeping", "multi_trunk", "default",
    ],
    "photo_license": sorted(_te._INAT_ACCEPTABLE_LICENSES),
    "trunk_photo_license": sorted(_te._INAT_ACCEPTABLE_LICENSES),
}
LIST_ENUMS: dict[str, list[str]] = {
    "sun_exposure": ["full_sun", "part_shade", "full_shade"],
}
# Ranges whose min must not exceed max.
RANGES = [
    ("mature_height_min_ft", "mature_height_max_ft"),
    ("canopy_spread_min_ft", "canopy_spread_max_ft"),
    ("lifespan_min_years", "lifespan_max_years"),
    ("usda_zone_min", "usda_zone_max"),
]
LONG_TEXT = {"description"}
KEY = "species"
DERIVED = {"is_complete", "enriched_at"}
EDITABLE = [f.name for f in SCHEMA if f.name != KEY and f.name not in DERIVED]

# How the form groups the fields.  Every editable column must appear exactly
# once; `test_enrichment_admin.py` checks that, so a schema addition cannot
# silently fall off the form.
GROUPS: list[tuple[str, list[str]]] = [
    ("Names", ["genus", "species_epithet", "family", "synonyms", "common_names"]),
    ("Description", ["description"]),
    ("Form and size", [
        "tree_form", "is_evergreen",
        "mature_height_min_ft", "mature_height_max_ft",
        "canopy_spread_min_ft", "canopy_spread_max_ft",
        "lifespan_min_years", "lifespan_max_years", "growth_rate",
    ]),
    ("Site", [
        "sun_exposure", "soil_preferences", "water_needs", "drought_tolerance",
        "root_behavior", "coastal_tolerance", "salt_tolerance", "pollution_tolerance",
        "usda_zone_min", "usda_zone_max",
    ]),
    ("Ecology", ["bloom_months", "wildlife_value", "fire_risk", "native_ecoregions"]),
    ("Photo", ["photo_url", "photo_license", "photo_attribution"]),
    ("Trunk photo", ["trunk_photo_url", "trunk_photo_license", "trunk_photo_attribution"]),
]


def field_specs() -> list[dict]:
    """What the form renders, derived from SCHEMA so the two cannot drift."""
    specs = []
    for field in SCHEMA:
        name = field.name
        if name == KEY or name in DERIVED:
            continue
        t = field.type
        if name in ENUMS:
            kind, options = "enum", ENUMS[name]
        elif name in LIST_ENUMS:
            kind, options = "list<enum>", LIST_ENUMS[name]
        elif pa.types.is_boolean(t):
            kind, options = "bool", None
        elif pa.types.is_integer(t):
            kind, options = "int", None
        elif pa.types.is_floating(t):
            kind, options = "float", None
        elif pa.types.is_list(t):
            kind = "list<int>" if pa.types.is_integer(t.value_type) else "list<string>"
            options = None
        else:
            kind, options = ("text" if name in LONG_TEXT else "string"), None
        specs.append({"name": name, "kind": kind, "options": options})
    return specs


# ── Validation ─────────────────────────────────────────────────────────────────


class ValidationError(ValueError):
    def __init__(self, problems: list[str]):
        super().__init__("; ".join(problems))
        self.problems = problems


def _blank(v) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)


def coerce_row(species: str, payload: dict) -> dict:
    """Turn a form submission into a row that fits SCHEMA, or raise.

    Empty strings and empty lists become NULL -- that is how the pipeline
    writes "unknown" (`enrichment.sun_exposure or None`), and a consumer that
    tests `IS NOT NULL` would otherwise see an empty list as an answer.
    """
    problems: list[str] = []
    row: dict = {KEY: species}
    kinds = {s["name"]: s for s in field_specs()}

    for name, spec in kinds.items():
        v = payload.get(name)
        kind = spec["kind"]
        try:
            if _blank(v):
                row[name] = None
            elif kind == "enum":
                if v not in spec["options"]:
                    raise ValueError(f"must be one of {', '.join(spec['options'])}")
                row[name] = v
            elif kind == "list<enum>":
                vals = _as_str_list(v)
                bad = [x for x in vals if x not in spec["options"]]
                if bad:
                    raise ValueError(f"unknown value(s) {bad}; allowed: {', '.join(spec['options'])}")
                row[name] = _dedupe(vals) or None
            elif kind == "bool":
                if isinstance(v, str):
                    v = {"true": True, "false": False, "yes": True, "no": False}.get(v.lower())
                if not isinstance(v, bool):
                    raise ValueError("must be true, false or blank")
                row[name] = v
            elif kind == "int":
                f = float(v)
                if not f.is_integer():
                    raise ValueError("must be a whole number")
                row[name] = int(f)
            elif kind == "float":
                # Through the column's own width, so a value that survives the
                # parquet unchanged compares equal to what was loaded.
                row[name] = pa.scalar(float(v), SCHEMA.field(name).type).as_py()
            elif kind == "list<int>":
                row[name] = sorted(_dedupe([int(x) for x in _as_list(v)])) or None
            elif kind == "list<string>":
                row[name] = _dedupe(_as_str_list(v)) or None
            else:  # string / text
                if not isinstance(v, str):
                    raise ValueError("must be text")
                row[name] = v.strip()
        except (TypeError, ValueError) as exc:
            problems.append(f"{name}: {exc}")

    if problems:
        raise ValidationError(problems)

    # Cross-field rules.  These mirror what the pipeline and its consumers
    # assume rather than inventing new policy.
    if _blank(row["common_names"]) or _blank(row["tree_form"]):
        problems.append(
            "common_names and tree_form are required: without both, the freshness "
            "probe reports the table incomplete on every run (ENRICHMENT_COMPLETE_SQL)"
        )
    for lo, hi in RANGES:
        if row[lo] is not None and row[hi] is not None and row[lo] > row[hi]:
            problems.append(f"{lo} ({row[lo]}) is greater than {hi} ({row[hi]})")
    for name in ("mature_height_min_ft", "mature_height_max_ft", "canopy_spread_min_ft",
                 "canopy_spread_max_ft", "lifespan_min_years", "lifespan_max_years"):
        if row[name] is not None and row[name] < 0:
            problems.append(f"{name} cannot be negative")
    for name in ("usda_zone_min", "usda_zone_max"):
        if row[name] is not None and not 1 <= row[name] <= 13:
            problems.append(f"{name} must be between 1 and 13")
    if row["bloom_months"] and any(not 1 <= m <= 12 for m in row["bloom_months"]):
        problems.append("bloom_months must be between 1 and 12")
    if row["native_ecoregions"] and any(i <= 0 for i in row["native_ecoregions"]):
        problems.append("native_ecoregions must be positive ecoregion ids")
    for prefix in ("photo", "trunk_photo"):
        if row[f"{prefix}_url"] and not re.match(r"^https?://", row[f"{prefix}_url"]):
            problems.append(f"{prefix}_url must be an http(s) URL")
        if row[f"{prefix}_url"] and not row[f"{prefix}_license"]:
            problems.append(f"a {prefix.replace('_', ' ')} needs a licence")
    for name in row["synonyms"] or []:
        # Written the way the ingest emits a species, so the alias row it
        # produces is a key a tree row can actually carry.
        if name == species:
            problems.append(f"synonyms: {name!r} is this species")
        elif name in SKIP_SPECIES:
            problems.append(f"synonyms: {name!r} is a sentinel, not a name")
        elif _sanitize_taxon(name) != name:
            problems.append(
                f"synonyms: {name!r} is not a species-rank scientific name as the "
                f"ingest would write it (expected {_sanitize_taxon(name)!r})"
            )
    if row["synonyms"]:
        row["synonyms"] = sorted(row["synonyms"])
    # Sentence case, as the run writes it; "Evergreen Pear" is staged as
    # "Evergreen pear" and the form shows the reviewer what will be published.
    row["common_names"] = normalize_common_names(row["common_names"])
    if problems:
        raise ValidationError(problems)

    row["is_complete"] = is_complete(row)
    return row


def _as_list(v) -> list:
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    raise ValueError("must be a list")


def _as_str_list(v) -> list[str]:
    out = []
    for x in _as_list(v):
        if not isinstance(x, str):
            raise ValueError("must be a list of text values")
        if x.strip():
            out.append(x.strip())
    return out


def _dedupe(xs: list) -> list:
    seen: set = set()
    out = []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def is_complete(row: dict) -> bool:
    """Python twin of tree_enrichment._NEW_COMPLETENESS_EXPR."""
    return all(
        not _blank(row.get(name))
        for name in (
            "common_names", "is_evergreen", "mature_height_max_ft", "canopy_spread_max_ft",
            "growth_rate", "drought_tolerance", "tree_form",
        )
    )


# ── Table patching ─────────────────────────────────────────────────────────────


def accepted_for(species: str) -> str | None:
    """The accepted name when *species* is a code-level fold, else None.

    Such a row is an alias the daily job rewrites from the accepted row on
    every load (`with_species_aliases`), so an edit to it would not survive;
    the form sends the reviewer to the accepted row instead.  A synonym added
    by hand is different: both rows list each other and editing either
    patches both, so neither is read-only.

    Both code maps count.  A misspelling is not a synonym -- POWO has never
    heard of it -- but its row is maintained the same way, so editing it is
    just as futile.
    """
    return SPECIES_SYNONYMS.get(species) or SPECIES_MISSPELLINGS.get(species)


def apply_edits(table: pa.Table, edits: dict[str, dict]) -> tuple[pa.Table, dict]:
    """Return *table* with each edited species' row replaced.

    Every alias key of the row -- its hybrid-mark twin, each name in its
    `synonyms`, and their twins -- gets the same values under its own key, its
    `synonyms` rewritten to point back.  An alias with no row yet is appended,
    which is how a hand-added synonym starts bridging the join; an alias that
    already had a row of its own is overwritten, which is the merge.  A species
    with no row in *table* is appended too, and the summary says so, since it
    usually means the daily job purged it or the key changed underneath us.
    """
    rows = table.to_pylist()
    index = {r[KEY]: i for i, r in enumerate(rows)}
    replaced, aliased, appended = [], [], []
    for species, row in edits.items():
        synonyms = list(row.get("synonyms") or [])
        for target in [species, *alias_keys(species, synonyms)]:
            if target == species:
                new = {**row, KEY: target}
            else:
                new = {**row, KEY: target, "synonyms": other_names(target, species, synonyms)}
            if target in index:
                rows[index[target]] = new
                (aliased if target != species else replaced).append(target)
            elif target == species:
                index[target] = len(rows)
                rows.append(new)
                appended.append(target)
            else:
                index[target] = len(rows)
                rows.append(new)
                aliased.append(target)
                appended.append(target)
    patched = pa.Table.from_pylist(rows, schema=SCHEMA)
    _assert_shape(table, patched, appended)
    return patched, {"replaced": replaced, "aliased": aliased, "appended": appended}


def _assert_shape(before: pa.Table, after: pa.Table, appended: list[str]) -> None:
    """The invariants every consumer of this table relies on."""
    expected = len(before) + len(appended)
    if len(after) != expected:
        raise RuntimeError(f"patched table has {len(after)} rows, expected {expected}")
    species = after.column(KEY).to_pylist()
    if len(set(species)) != len(species):
        dupes = sorted({s for s in species if species.count(s) > 1})
        raise RuntimeError(f"duplicate species after patch: {dupes}")
    missing = SPECIES_SENTINELS - set(species)
    if missing:
        raise RuntimeError(f"sentinel rows went missing: {sorted(missing)}")


# ── Tree counts ────────────────────────────────────────────────────────────────


def load_tree_counts() -> dict[str, tuple[int, int]]:
    """species -> (trees, cities) over the published cross-city rollup.

    The list is ordered by this, so a reviewer enriching "the most common
    trees first" starts at the top.  One aggregate over the rollup's `species`
    and `city` columns; DuckDB reads only those two, so it is a few seconds
    over HTTP rather than a 5.9M-row download.  Keyed on the species exactly
    as the rollup carries it -- a city not yet rebuilt still publishes the old
    synonym -- and `AdminState.trees_for` folds a row's alias keys together.
    """
    import duckdb

    con = duckdb.connect()
    try:
        rows = con.execute(
            "SELECT species, count(*), count(DISTINCT city) FROM read_parquet(?) "
            "WHERE species IS NOT NULL GROUP BY species",
            [TREE_INFO_PARQUET],
        ).fetchall()
    finally:
        con.close()
    return {species: (int(trees), int(cities)) for species, trees, cities in rows}


# ── State ──────────────────────────────────────────────────────────────────────


def _json_default(v):
    if isinstance(v, datetime):
        return v.isoformat()
    raise TypeError(type(v))


def _parse_edit(row: dict) -> dict:
    """A staged edit read back from disk, brought up to the current SCHEMA.

    An edit staged before a column was added (the trunk photo, `synonyms`)
    has no key for it; every reader indexes rows by name, so fill the gap
    with NULL rather than let the first search after an upgrade fail.
    """
    row = {f.name: None for f in SCHEMA} | dict(row)
    if isinstance(row.get("enriched_at"), str):
        row["enriched_at"] = datetime.fromisoformat(row["enriched_at"])
    # An edit staged before the sentence-case rule existed is still a row
    # this process will publish, so it follows the rule too.
    row["common_names"] = normalize_common_names(row.get("common_names"))
    return row


class AdminState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.table: pa.Table | None = None
        self.rows: dict[str, dict] = {}
        self.loaded_at: datetime | None = None
        self.edits: dict[str, dict] = {}
        self.tree_counts: dict[str, tuple[int, int]] = {}
        self.counts_loaded_at: datetime | None = None
        self._ecoregions: list[dict] | None = None
        if EDITS_PATH.exists():
            saved = json.loads(EDITS_PATH.read_text(encoding="utf-8"))
            self.edits = {k: _parse_edit(v) for k, v in saved.items()}
            print(f"[admin] restored {len(self.edits)} staged edit(s) from {EDITS_PATH.name}", file=sys.stderr)

    # -- loading ---------------------------------------------------------------

    def load(self) -> None:
        print("[admin] reading the published enrichment table ...", file=sys.stderr)
        table = _te.load_existing_table(published_url())
        if table is None:
            raise RuntimeError(f"no enrichment table at {PUBLISHED_URL}")
        with self.lock:
            self.table = table
            self.rows = {r[KEY]: r for r in table.to_pylist()}
            self.loaded_at = datetime.now(tz=timezone.utc)
        print(f"[admin] {len(table)} rows", file=sys.stderr)
        self.load_counts()

    def load_counts(self) -> None:
        """Tree counts are a convenience for ordering; an unreachable rollup
        leaves the list alphabetical rather than taking the form down."""
        print("[admin] counting trees per species in the published rollup ...", file=sys.stderr)
        try:
            counts = load_tree_counts()
        except Exception as exc:  # noqa: BLE001
            print(f"[admin] tree counts unavailable ({exc}); list is unordered", file=sys.stderr)
            return
        with self.lock:
            self.tree_counts = counts
            self.counts_loaded_at = datetime.now(tz=timezone.utc)
        print(f"[admin] {sum(t for t, _ in counts.values()):,} trees over {len(counts)} species keys", file=sys.stderr)

    def trees_for(self, species: str, row: dict) -> tuple[int, int]:
        """(trees, cities) for a row, its alias keys folded in: a city that
        still publishes `Platanus x acerifolia` counts towards the London
        plane's accepted row, because that is the row those trees will join
        to once the city is rebuilt."""
        synonyms = sorted({*(row.get("synonyms") or []), *synonyms_of(species)})
        keys = [species, *alias_keys(species, synonyms)]
        hits = [self.tree_counts[k] for k in keys if k in self.tree_counts]
        if not hits:
            return 0, 0
        return sum(t for t, _ in hits), max(c for _, c in hits)

    def _persist_edits(self) -> None:
        if self.edits:
            EDITS_PATH.write_text(json.dumps(self.edits, default=_json_default, indent=1), encoding="utf-8")
        elif EDITS_PATH.exists():
            EDITS_PATH.unlink()

    # -- reads -------------------------------------------------------------------

    def status(self) -> dict:
        return {
            "gcs_uri": ENRICHMENT_GCS_URI,
            "published_url": PUBLISHED_URL,
            "loaded_at": self.loaded_at.isoformat() if self.loaded_at else None,
            "row_count": len(self.table) if self.table is not None else 0,
            "pending": sorted(self.edits),
            "tree_counts": bool(self.tree_counts),
            "counts_loaded_at": self.counts_loaded_at.isoformat() if self.counts_loaded_at else None,
        }

    def current(self, species: str) -> dict | None:
        return self.edits.get(species) or self.rows.get(species)

    SORTS = ("trees", "name", "enriched", "incomplete")

    def search(self, q: str, flt: str, limit: int, sort: str = "trees") -> list[dict]:
        """`sort`: "trees" (most common first, the default), "name" (A-Z),
        "enriched" (least recently enriched first, never-enriched at the
        top), or "incomplete" (rows still short of a common name or a form
        first, then by trees).  A query always puts exact and prefix matches
        ahead of the chosen order, and sentinels always come last."""
        q = q.strip().lower()
        if sort not in self.SORTS:
            raise ValidationError([f"sort must be one of {', '.join(self.SORTS)}"])
        out = []
        for species, row in self.rows.items():
            row = self.edits.get(species, row)
            # An alias row -- a code-level synonym, or the U+00D7 spelling of a
            # hybrid whose ASCII row exists -- is the same taxon under another
            # key and would sit next to it with the same count.  Listed only
            # when the query names it.
            if not (q and q in species.lower()) and (
                accepted_for(species) is not None
                or (" × " in species and species.replace(" × ", " x ") in self.rows)
            ):
                continue
            if flt == "pending" and species not in self.edits:
                continue
            if flt == "incomplete" and row["is_complete"]:
                continue
            if flt == "nophoto" and row["photo_url"]:
                continue
            if flt == "notrunk" and row["trunk_photo_url"]:
                continue
            if flt == "nodesc" and row["description"]:
                continue
            if q:
                hay = " ".join([species, *(row["common_names"] or []), *(row["synonyms"] or [])]).lower()
                if q not in hay:
                    continue
            out.append(self._summary(species, row))
        # Exact and prefix matches first, then the most common trees, so the
        # default view is "what to enrich next" rather than the alphabet.
        # Sentinels last: "Unknown" carries a million trees and nothing to enrich.
        order = {
            "trees": lambda s: (-s["trees"], s["species"]),
            "name": lambda s: (s["species"],),
            "enriched": lambda s: (s["enriched_at"] or "", -s["trees"]),
            "incomplete": lambda s: (s["is_complete"], -s["trees"], s["species"]),
        }[sort]
        out.sort(key=lambda s: (
            s["species"].lower() != q,
            not s["species"].lower().startswith(q),
            s["sentinel"],
            *order(s),
        ))
        return out[:limit]

    def _summary(self, species: str, row: dict) -> dict:
        trees, cities = self.trees_for(species, row)
        return {
            "species": species,
            "trees": trees,
            "cities": cities,
            "enriched_at": row["enriched_at"].isoformat() if row.get("enriched_at") else None,
            "common_name": (row["common_names"] or [None])[0],
            "tree_form": row["tree_form"],
            "is_complete": row["is_complete"],
            "has_photo": bool(row["photo_url"]),
            "has_trunk_photo": bool(row["trunk_photo_url"]),
            "sentinel": species in SKIP_SPECIES,
            "alias_of": accepted_for(species),
            "pending": species in self.edits,
        }

    def detail(self, species: str) -> dict | None:
        published = self.rows.get(species)
        edit = self.edits.get(species)
        if published is None and edit is None:
            return None
        row = edit or published
        synonyms = list(row.get("synonyms") or [])
        aliases = alias_keys(species, synonyms)
        # A synonym that has a row of its own which does not point back here
        # is a duplicate this publish will fold in.
        merges = [
            name for name in synonyms
            if name in self.rows and species not in (self.rows[name].get("synonyms") or [])
        ]
        trees, cities = self.trees_for(species, row)
        return {
            "species": species,
            "trees": trees,
            "cities": cities,
            "row": row,
            "published": published,
            "pending": edit is not None,
            "changed": sorted(k for k in EDITABLE if edit and (edit.get(k) != (published or {}).get(k))),
            "sentinel": species in SKIP_SPECIES,
            "alias_of": accepted_for(species),
            "aliases": aliases,
            "new_aliases": [a for a in aliases if a not in self.rows],
            "merges": merges,
        }

    def ecoregions(self) -> list[dict]:
        """The picker's vocabulary: every ecoregion the published table knows."""
        if self._ecoregions is None:
            import duckdb

            con = duckdb.connect()
            try:
                rows = con.execute(
                    "SELECT ecoregion_id, ecoregion_name, biome, realm FROM read_parquet(?) "
                    "WHERE ecoregion_id IS NOT NULL ORDER BY ecoregion_name",
                    [ECOREGION_URL],
                ).fetchall()
            except Exception as exc:  # noqa: BLE001 - fall back to the enrichment run's own loader
                print(f"[admin] {ECOREGION_URL} unreadable ({exc}); using the ArcGIS fallback", file=sys.stderr)
                rows = [
                    (e.ecoregion_id, e.ecoregion_name, e.biome, e.realm)
                    for e in _te.load_ecoregion_references()
                ]
            finally:
                con.close()
            self._ecoregions = [{"id": int(i), "name": n, "biome": b, "realm": r} for i, n, b, r in rows]
        return self._ecoregions

    # -- writes ------------------------------------------------------------------

    def save(self, species: str, payload: dict) -> dict:
        if species in SKIP_SPECIES:
            raise ValidationError([f"{species!r} is a sentinel; its row is authored in _ingest_shared.py"])
        accepted = accepted_for(species)
        if accepted is not None:
            raise ValidationError([
                f"{species!r} folds onto {accepted!r} ("
                f"{'SPECIES_SYNONYMS' if species in SPECIES_SYNONYMS else 'SPECIES_MISSPELLINGS'}); its row is "
                f"an alias rewritten from that one on every run, so edit {accepted!r} instead"
            ])
        if species not in self.rows:
            raise ValidationError([f"{species!r} has no row in the published table"])
        row = coerce_row(species, payload)
        row["enriched_at"] = datetime.now(tz=timezone.utc)
        with self.lock:
            if row_equals(row, self.rows[species]):
                # Nothing changed: do not stage a no-op that would only bump enriched_at.
                self.edits.pop(species, None)
            else:
                self.edits[species] = row
            self._persist_edits()
        return self.detail(species)  # type: ignore[return-value]

    def discard(self, species: str) -> None:
        with self.lock:
            self.edits.pop(species, None)
            self._persist_edits()

    def discard_all(self) -> None:
        with self.lock:
            self.edits.clear()
            self._persist_edits()

    def publish(self) -> dict:
        with self.lock:
            if not self.edits:
                raise ValidationError(["nothing to publish"])
            edits = dict(self.edits)
        # Against the table as it is *now*, not the one this process loaded.
        current = _te.load_existing_table(published_url())
        if current is None:
            raise RuntimeError("published table vanished")
        patched, summary = apply_edits(current, edits)
        pq.write_table(patched, PUBLISH_PATH)
        print(f"[admin] wrote {len(patched)} rows to {PUBLISH_PATH.name}", file=sys.stderr)
        _te.upload_to_gcs(str(PUBLISH_PATH), ENRICHMENT_GCS_URI)  # verifies after upload
        with self.lock:
            self.table = patched
            self.rows = {r[KEY]: r for r in patched.to_pylist()}
            self.loaded_at = datetime.now(tz=timezone.utc)
            for species in edits:
                self.edits.pop(species, None)
            self._persist_edits()
        summary["rows"] = len(patched)
        return summary


def row_equals(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in EDITABLE)


# ── iNaturalist photo candidates ───────────────────────────────────────────────


INAT_PAGE_SIZE = 24


def inat_candidates(species: str, page: int = 1, taxon_id: int | None = None, limit: int = INAT_PAGE_SIZE) -> dict:
    """Openly licensed photos for the picker, one page at a time.

    Page 1 is the taxon's default photo followed by the best research-grade
    observation photos (by votes); each later page is the next `limit`
    observations, all of their photos.  Same sources and licence filter as
    `tree_enrichment.fetch_inat_photo`.  The first ten are often foliage
    close-ups, and a bark or habit shot -- what the trunk slot wants -- is
    usually a few pages down, so the picker keeps a "more" button until iNat
    runs out.  The taxon id comes back with page 1 and is passed in for the
    rest, so a later page is one request rather than two.
    """
    licenses = _te._INAT_ACCEPTABLE_LICENSES
    photos: list[dict] = []
    seen: set[str] = set()
    taxon = None
    if taxon_id is not None:
        data = _te._inat_get(f"/taxa/{taxon_id}", {})
        results = data.get("results", [])
        taxon = results[0] if results else None
    else:
        for query in _te.build_inat_lookup_candidates(species):
            data = _te._inat_get("/taxa", {"q": query, "rank": "species,hybrid", "per_page": 5})
            results = data.get("results", [])
            if not results:
                continue
            lower = query.lower().replace(" x ", " × ")
            taxon = next((r for r in results if r.get("name", "").lower() in (lower, query.lower())), results[0])
            break
    if taxon is None:
        return {"taxon": None, "photos": [], "page": page, "has_more": False}

    def add(url: str | None, lic: str | None, attribution: str | None, source: str) -> None:
        if not url or lic not in licenses or url in seen:
            return
        seen.add(url)
        medium = re.sub(r"/(square|small|large|original)\.", "/medium.", url)
        photos.append({
            "url": medium,
            "thumb": medium.replace("/medium.", "/square."),
            "license": lic,
            "attribution": attribution,
            "source": source,
        })

    if page == 1:
        dp = taxon.get("default_photo") or {}
        add(dp.get("medium_url") or dp.get("url"), dp.get("license_code"), dp.get("attribution"), "taxon default")
    obs = _te._inat_get("/observations", {
        "taxon_id": taxon["id"],
        "photos": "true",
        "quality_grade": "research",
        "license": ",".join(sorted(licenses)),
        "photo_license": ",".join(sorted(licenses)),
        "per_page": limit,
        "page": page,
        "order_by": "votes",
    })
    results = obs.get("results", [])
    for o in results:
        for p in o.get("photos", []):
            add(p.get("url"), p.get("license_code"), p.get("attribution"), f"observation {o.get('id')}")
    return {
        "taxon": {
            "id": taxon["id"],
            "name": taxon.get("name"),
            "common_name": taxon.get("preferred_common_name"),
            "url": f"https://www.inaturalist.org/taxa/{taxon['id']}",
        },
        "photos": photos,
        "page": page,
        "has_more": len(results) >= limit,
    }


# ── HTTP ───────────────────────────────────────────────────────────────────────

STATE = AdminState()


class Handler(BaseHTTPRequestHandler):
    server_version = "enrichment-admin/1.0"

    def log_message(self, fmt, *args):  # quieter than the default
        if "/api/" in (args[0] if args else ""):
            return
        super().log_message(fmt, *args)

    # -- helpers -------------------------------------------------------------------

    def _json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, default=_json_default).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: HTTPStatus, message: str, problems: list[str] | None = None) -> None:
        self._json({"error": message, "problems": problems or [message]}, status)

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        data = json.loads(raw or b"{}")
        if not isinstance(data, dict):
            raise ValueError("body must be a JSON object")
        return data

    def _route(self) -> tuple[str, list[str], dict[str, str]]:
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.split("/") if p]
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        return url.path, parts, query

    # -- verbs -----------------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802
        path, parts, q = self._route()
        try:
            if path == "/" or path == "/index.html":
                body = HTML.read_bytes()
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif parts == ["api", "status"]:
                self._json(STATE.status())
            elif parts == ["api", "schema"]:
                self._json({"fields": field_specs(), "groups": GROUPS})
            elif parts == ["api", "ecoregions"]:
                self._json(STATE.ecoregions())
            elif parts == ["api", "species"]:
                self._json(STATE.search(q.get("q", ""), q.get("filter", ""), int(q.get("limit", 200)), q.get("sort", "trees")))
            elif len(parts) == 3 and parts[:2] == ["api", "species"]:
                detail = STATE.detail(parts[2])
                if detail is None:
                    self._error(HTTPStatus.NOT_FOUND, f"no species {parts[2]!r}")
                else:
                    self._json(detail)
            elif parts == ["api", "inat"]:
                self._json(inat_candidates(
                    q.get("species", ""),
                    page=max(1, int(q.get("page", 1))),
                    taxon_id=int(q["taxon_id"]) if q.get("taxon_id") else None,
                ))
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
        except Exception as exc:  # noqa: BLE001
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_PUT(self) -> None:  # noqa: N802
        _, parts, _ = self._route()
        try:
            if len(parts) == 3 and parts[:2] == ["api", "species"]:
                self._json(STATE.save(parts[2], self._body()))
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
        except ValidationError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid", exc.problems)
        except Exception as exc:  # noqa: BLE001
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_DELETE(self) -> None:  # noqa: N802
        _, parts, _ = self._route()
        try:
            if len(parts) == 4 and parts[:2] == ["api", "species"] and parts[3] == "edit":
                STATE.discard(parts[2])
                self._json(STATE.detail(parts[2]) or {})
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
        except Exception as exc:  # noqa: BLE001
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))

    def do_POST(self) -> None:  # noqa: N802
        _, parts, _ = self._route()
        try:
            if parts == ["api", "publish"]:
                self._json(STATE.publish())
            elif parts == ["api", "reload"]:
                if self._body().get("discard"):
                    STATE.discard_all()
                STATE.load()
                self._json(STATE.status())
            elif parts == ["api", "recount"]:
                STATE.load_counts()
                self._json(STATE.status())
            else:
                self._error(HTTPStatus.NOT_FOUND, "not found")
        except ValidationError as exc:
            self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid", exc.problems)
        except Exception as exc:  # noqa: BLE001
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=int(os.environ.get("ENRICHMENT_ADMIN_PORT", 4175)))
    args = parser.parse_args()

    STATE.load()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"[admin] http://127.0.0.1:{args.port}  (localhost only; Ctrl-C to stop)", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

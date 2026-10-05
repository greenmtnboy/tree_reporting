#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb"]
# ///

"""Publish the arborary.world homepage feed.

    gs://trilogy_public_models/arborary/website/v1/homepage.json

Run by the `publish-website` [[cloud.job]] (`website_homepage.preql`
`call`s this script) after the daily core. The website bundles a snapshot of
this object and swaps in the live copy only after validating it, so the feed
moves city coverage, totals, species leaders, ecoregions, download links and
the globe's field notes without a website deploy.

What it reads, and from where:

- **Every city's own published parquet**, unioned. Never `full_tree_info`: the
  rollup is republished on the core's cadence and lags a city file that moved
  since, so the homepage counts the same relation the city files are.
- `tree_enrichment` for common names and the genus-only test, and
  `ecoregion_info` for the ecoregion under each city's map pin.
- **No portal, no staging object, no city rebuild.**

Every input is read at the generation a HEAD returned at the start of the run,
so a city republishing mid-run fails the run (its pinned generation is gone)
rather than mixing two publications into one total.

**Publishing is all or nothing.** The candidate is built whole, validated
against the v1 contract (the website's `homepage-feed.ts` check, plus the
rules it cannot see), and uploaded once with `x-goog-if-generation-match` on
the generation this run read, so a slower, older run can never overwrite a
newer one. Anything else fails the job and leaves the last good object
serving: a network or auth error, a previously published city that is now
missing or empty, a count that does not add up. Only a city that has never
been published -- absent from the live feed *and* a confirmed 404 -- is
published as pending (zero trees, no link, no globe stop).

Credentials are the org's `GOOGLE_HMAC_KEY` / `GOOGLE_HMAC_SECRET`, the same
pair DuckDB writes the parquets with. Those are interoperability keys, not ADC,
so the upload is a V4-signed request to the GCS XML API rather than a
google-cloud-storage client.

The contract (fields, ordering, ecoregion policy, sentinels, slugs) is ported
from the website's `scripts/build_homepage.py`, `build_tree_stats.py`,
`build_species.py` and `city_ecoregions.py`; keep them in step.

Locally:

    uv run website/publish_homepage.py --dry-run --output homepage.json
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import unicodedata
from xml.etree import ElementTree
from collections import defaultdict
from datetime import datetime, timezone
from math import cos, isfinite, radians
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
# A copy of src/src/cityConfig.json: the cloud bundle holds only data/, so the
# frontend registry is mirrored here. `test_city_wiring.py` fails when the two
# differ, and `tools/new_city.py` writes both.
CITY_CONFIG = HERE / "city_config.json"

BUCKET = "trilogy_public_models"
OBJECT = "arborary/website/v1/homepage.json"
PUBLIC_HOST = "https://storage.googleapis.com"
TREES_BASE = f"{PUBLIC_HOST}/{BUCKET}/duckdb/trees"
DATA_VERSION = 2
CONTENT_TYPE = "application/json"
CACHE_CONTROL = "public,max-age=300"

SCHEMA_VERSION = 1
TOP_SPECIES = 10
CATALOGUE_SIZE = 2000
OBSERVATIONS_PER_STOP = 3
TOUR_ORDER = ["GBLON", "USNYC", "USSFO", "AUMEL", "ARBUE", "FRPAR", "DEBER"]
NEARBY_METERS = 5_000

# Crowther et al., "Mapping tree density at a global scale", Nature 525 (2015).
# Editorial reference data, preserved as the website states it; never derived
# from the urban inventories.
WORLD_TREE_ESTIMATE = 3_040_000_000_000
WORLD_TREE_SOURCE = {
    "citation": "Crowther, T. W. et al. Mapping tree density at a global scale. Nature 525, 201-205 (2015).",
    "url": "https://www.nature.com/articles/nature14967",
}

# Rows that are real trees but not species: the pipeline's own sentinels
# (`shared.ingest.SPECIES_SENTINELS`, spelled out so this script does not pull
# in pytrilogy; `test_publish_homepage.py` keeps the two in step) plus
# placeholder values the website excludes because they once leaked through a
# city normaliser.
SENTINELS = (
    "Cactus", "Dead", "Palm", "Shrub", "Unknown",
    "Mistaken", "New", "Non specifie", "Planting site", "Scheduled planting", "Unplantable",
)

# City codes are {ISO 3166-1 alpha-2}{3 letters}, so the country is the
# prefix. Reference data, not a city list: a city in a new country fails
# `test_city_wiring.py::test_city_has_a_homepage_country` until it is added.
COUNTRIES = {
    "AR": "Argentina",
    "AU": "Australia",
    "CA": "Canada",
    "CO": "Colombia",
    "DE": "Germany",
    "DK": "Denmark",
    "FI": "Finland",
    "FR": "France",
    "GB": "United Kingdom",
    "GR": "Greece",
    "JP": "Japan",
    "NL": "Netherlands",
    "TW": "Taiwan",
    "US": "United States",
}


class PublishError(RuntimeError):
    """A reason not to publish. The job fails and the live object stays."""


def log(message: str) -> None:
    print(f"[homepage] {message}", flush=True)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def load_cities(path: Path = CITY_CONFIG) -> list[dict]:
    """`{code, name, country, coordinates}` per city, in registry order."""
    config = json.loads(path.read_text(encoding="utf-8"))
    cities = []
    for code, entry in config.items():
        country = COUNTRIES.get(code[:2])
        if country is None:
            raise PublishError(f"{code}: no country for prefix {code[:2]!r}; add it to COUNTRIES")
        lon, lat = entry["center"]
        cities.append({"code": code, "name": entry["name"], "country": country, "coordinates": [lon, lat]})
    return cities


def city_url(code: str) -> str:
    return f"{TREES_BASE}/{code.lower()}_tree_info_v{DATA_VERSION}.parquet"


def enrichment_url() -> str:
    return f"{TREES_BASE}/tree_enrichment_v{DATA_VERSION}.parquet"


def ecoregion_url() -> str:
    return f"{TREES_BASE}/ecoregion_info_v{DATA_VERSION}.parquet"


# ---------------------------------------------------------------------------
# GCS: public reads, HMAC-signed XML API calls
# ---------------------------------------------------------------------------


def head_generation(url: str) -> int | None:
    """The live generation of a public object, or None on a confirmed 404.
    Any other failure raises: it is evidence about the network, not the object."""
    try:
        with urlopen(Request(url, method="HEAD"), timeout=60) as resp:
            generation = resp.headers.get("x-goog-generation")
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    if not generation:
        raise PublishError(f"{url}: no x-goog-generation header")
    return int(generation)


def pinned(url: str, generation: int) -> str:
    return f"{url}?generation={generation}"


class HmacClient:
    """The GCS XML API with V4 signatures over an HMAC key (GOOG4-HMAC-SHA256)."""

    host = "storage.googleapis.com"

    def __init__(self, access_id: str, secret: str):
        self.access_id = access_id
        self.secret = secret

    @classmethod
    def from_env(cls) -> HmacClient | None:
        key, secret = os.environ.get("GOOGLE_HMAC_KEY"), os.environ.get("GOOGLE_HMAC_SECRET")
        return cls(key, secret) if key and secret else None

    def _signed(
        self, method: str, path: str, headers: dict[str, str], body: bytes, query: dict[str, str] | None = None
    ) -> Request:
        now = datetime.now(timezone.utc)
        stamp, day = now.strftime("%Y%m%dT%H%M%SZ"), now.strftime("%Y%m%d")
        payload_hash = hashlib.sha256(body).hexdigest()
        headers = {
            **{k.lower(): v.strip() for k, v in headers.items()},
            "host": self.host,
            "x-goog-date": stamp,
            "x-goog-content-sha256": payload_hash,
        }
        query_string = "&".join(f"{quote(k, safe='~')}={quote(v, safe='~')}" for k, v in sorted((query or {}).items()))
        signed_headers = ";".join(sorted(headers))
        canonical = "\n".join(
            [
                method,
                quote(path, safe="/~"),
                query_string,
                "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)),
                signed_headers,
                payload_hash,
            ]
        )
        scope = f"{day}/auto/storage/goog4_request"
        to_sign = "\n".join(
            ["GOOG4-HMAC-SHA256", stamp, scope, hashlib.sha256(canonical.encode()).hexdigest()]
        )
        key = f"GOOG4{self.secret}".encode()
        for part in (day, "auto", "storage", "goog4_request"):
            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
        headers["authorization"] = (
            f"GOOG4-HMAC-SHA256 Credential={self.access_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        headers.pop("host")
        url = f"https://{self.host}{quote(path, safe='/~')}" + (f"?{query_string}" if query_string else "")
        return Request(url, data=body or None, method=method, headers=headers)

    def generations(self, bucket: str, prefix: str) -> dict[str, int]:
        """`{object name: live generation}` under `prefix`, from a bucket listing.

        A listing, because it is the one read the edge cache never answers: a
        GET or HEAD of a public object -- signed or not, with `Cache-Control:
        no-cache` or not -- can return a generation up to five minutes old,
        and a generation pinned from that is already deleted."""
        found: dict[str, int] = {}
        marker = ""
        while True:
            query = {"prefix": prefix, **({"marker": marker} if marker else {})}
            try:
                with urlopen(self._signed("GET", f"/{bucket}", {}, b"", query), timeout=60) as resp:
                    root = ElementTree.fromstring(resp.read())
            except HTTPError as exc:
                raise PublishError(f"listing gs://{bucket}/{prefix}: HTTP {exc.code} {exc.read()[:300]!r}") from exc
            ns = {"s3": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
            tag = (lambda t: f"s3:{t}") if ns else (lambda t: t)
            for item in root.findall(tag("Contents"), ns):
                name = item.findtext(tag("Key"), namespaces=ns)
                found[name] = int(item.findtext(tag("Generation"), namespaces=ns))
                marker = name
            if (root.findtext(tag("IsTruncated"), namespaces=ns) or "false").lower() != "true":
                return found

    def put(self, bucket: str, name: str, body: bytes, *, if_generation_match: int) -> int:
        """Upload; returns the new generation. 412 means another run published first."""
        headers = {
            "content-type": CONTENT_TYPE,
            "cache-control": CACHE_CONTROL,
            "x-goog-if-generation-match": str(if_generation_match),
        }
        try:
            with urlopen(self._signed("PUT", f"/{bucket}/{name}", headers, body), timeout=120) as resp:
                return int(resp.headers["x-goog-generation"])
        except HTTPError as exc:
            if exc.code == 412:
                raise PublishError(
                    f"gs://{bucket}/{name} moved past generation {if_generation_match} during this run; "
                    "another publisher won, so this run's older candidate is dropped"
                ) from exc
            raise PublishError(f"writing gs://{bucket}/{name}: HTTP {exc.code} {exc.read()[:300]!r}") from exc


def read_json(url: str) -> dict:
    with urlopen(url, timeout=60) as resp:
        return json.loads(resp.read())


def read_live(client: HmacClient | None) -> tuple[dict | None, int]:
    """The feed now serving and its generation (0 = never published)."""
    url = f"{PUBLIC_HOST}/{BUCKET}/{OBJECT}"
    if client is not None:
        generation = client.generations(BUCKET, OBJECT).get(OBJECT)
        if generation is None:
            return None, 0
        # Pinned, so the cache key names this generation and nothing older.
        return read_json(pinned(url, generation)), generation
    # Dry run without credentials: the public copy, possibly five minutes
    # behind. A dry run never uploads, so no precondition is built on it.
    try:
        with urlopen(url, timeout=60) as resp:
            return json.loads(resp.read()), int(resp.headers.get("x-goog-generation") or 0)
    except HTTPError as exc:
        if exc.code == 404:
            return None, 0
        raise


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def slugify(name: str, taken: set[str]) -> str:
    """`Platanus x acerifolia` -> `platanus-x-acerifolia`, unique within `taken`.
    The website's `build_species.slugify`; species pages are keyed on it."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-") or "species"
    candidate, n = slug, 2
    while candidate in taken:
        candidate = f"{slug}-{n}"
        n += 1
    taken.add(candidate)
    return candidate


def connect():
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    return con


def snapshot_inputs(cities: list[dict], client: HmacClient | None) -> dict[str, int | None]:
    """Generation of every input at the start of the run; None = not published.

    From one listing when there are credentials (see `HmacClient.generations`);
    a dry run without them falls back to a HEAD per object."""
    urls = {c["code"]: city_url(c["code"]) for c in cities}
    urls["enrichment"] = enrichment_url()
    urls["ecoregions"] = ecoregion_url()
    if client is not None:
        prefix = TREES_BASE.removeprefix(f"{PUBLIC_HOST}/{BUCKET}/") + "/"
        listed = client.generations(BUCKET, prefix)
        generations = {
            key: listed.get(url.removeprefix(f"{PUBLIC_HOST}/{BUCKET}/")) for key, url in urls.items()
        }
    else:
        generations = {key: head_generation(url) for key, url in urls.items()}
    for key in ("enrichment", "ecoregions"):
        if generations[key] is None:
            raise PublishError(f"{urls[key]} is missing")
    return generations


def city_ecoregions(con, cities: list[dict], source: str) -> dict[str, dict]:
    """Map-pin containment first, the nearest polygon within 5 km second, else
    nothing. These describe the pin, not the municipal boundary. The website's
    `city_ecoregions.py`."""
    con.execute("INSTALL spatial; LOAD spatial;")
    con.execute(
        """
        CREATE OR REPLACE TEMP TABLE city_regions AS
        SELECT ecoregion_id, ecoregion_name, biome, source_version,
               bbox_min_lon, bbox_max_lon, bbox_min_lat, bbox_max_lat,
               ST_GeomFromGeoJSON(geometry_geojson) AS geometry
        FROM read_parquet(?) WHERE is_terrestrial
        """,
        [source],
    )
    matches = {}
    for city in cities:
        lon, lat = city["coordinates"]
        row = con.execute(
            """
            SELECT ecoregion_id, ecoregion_name, biome, source_version
            FROM city_regions
            WHERE ? BETWEEN bbox_min_lon AND bbox_max_lon
              AND ? BETWEEN bbox_min_lat AND bbox_max_lat
              AND ST_Covers(geometry, ST_Point(?, ?))
            ORDER BY ecoregion_id LIMIT 1
            """,
            [lon, lat, lon, lat],
        ).fetchone()
        match, distance = "contains", 0
        if row is None:
            # A generalised coastline can exclude a coastal pin. Bounded to
            # 5 km and labelled; never the closest polygon at any distance.
            lat_pad = NEARBY_METERS / 110_000
            lon_pad = lat_pad / max(cos(radians(lat)), 0.01)
            projection = f"+proj=aeqd +lat_0={lat} +lon_0={lon} +datum=WGS84 +units=m"
            row = con.execute(
                """
                SELECT ecoregion_id, ecoregion_name, biome, source_version,
                       ST_Distance(ST_Transform(geometry, 'EPSG:4326', ?,
                           always_xy := true), ST_Point(0, 0)) AS distance
                FROM city_regions
                WHERE ? BETWEEN bbox_min_lon - ? AND bbox_max_lon + ?
                  AND ? BETWEEN bbox_min_lat - ? AND bbox_max_lat + ?
                ORDER BY distance, ecoregion_id LIMIT 1
                """,
                [projection, lon, lon_pad, lon_pad, lat, lat_pad, lat_pad],
            ).fetchone()
            if row is None or row[4] > NEARBY_METERS:
                continue
            match, distance = "nearby", round(row[4])
        matches[city["code"]] = {
            "id": int(row[0]),
            "name": row[1],
            "biome": row[2],
            "source_version": row[3],
            "match": match,
            "distance_m": distance,
        }
    return matches


def collect(cities: list[dict], generations: dict[str, int | None], live: dict | None) -> dict:
    """Query the pinned inputs; returns the raw material `build_feed` shapes."""
    previously_published = {
        c["code"] for c in (live or {}).get("stats", {}).get("cities", []) if c.get("trees", 0) > 0
    }
    published = {c["code"]: generations[c["code"]] for c in cities if generations[c["code"]] is not None}
    for city in cities:
        if city["code"] not in published:
            if city["code"] in previously_published:
                raise PublishError(f"{city['code']}: {city_url(city['code'])} is gone but the live feed counts it")
            log(f"{city['code']}: awaiting first publication")
    if not published:
        raise PublishError("no city parquet is published")

    con = connect()
    files = {pinned(city_url(code), gen): code for code, gen in published.items()}
    file_list = ", ".join(f"'{url}'" for url in files)
    sentinel_sql = ", ".join("'" + s.replace("'", "''") + "'" for s in SENTINELS)
    # One scan of two projected columns does every count on the page: city
    # totals by file, and (species, city) pairs for diversity, the leaders and
    # the per-city catalogue counts the globe's field notes come from.
    rows = con.execute(
        f"""
        SELECT filename, city, species, count(*) AS n
        FROM read_parquet([{file_list}], filename = true, union_by_name = true)
        GROUP BY ALL
        """
    ).fetchall()

    city_trees: dict[str, int] = defaultdict(int)
    species_city: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    sentinels = set(SENTINELS)
    for filename, city, species, n in rows:
        city_trees[files[filename]] += n
        if species is not None and species not in sentinels:
            species_city[species][city] += n
    for code in published:
        if city_trees[code] == 0 and code in previously_published:
            raise PublishError(f"{code}: the published parquet has no rows but the live feed counts it")

    enrichment = {
        species: (epithet, next((n for n in (names or []) if n), None))
        for species, epithet, names in con.execute(
            f"""
            SELECT species, any_value(species_epithet), any_value(common_names)
            FROM read_parquet('{pinned(enrichment_url(), generations["enrichment"])}')
            WHERE species IS NOT NULL GROUP BY species
            """
        ).fetchall()
    }
    regions = city_ecoregions(con, cities, pinned(ecoregion_url(), generations["ecoregions"]))
    log(f"queried {len(published)} city files, {len(species_city):,} distinct species")
    return {
        "city_trees": dict(city_trees),
        "species_city": {s: dict(c) for s, c in species_city.items()},
        "enrichment": enrichment,
        "regions": regions,
        "published": published,
    }


# ---------------------------------------------------------------------------
# The feed
# ---------------------------------------------------------------------------


def build_feed(cities: list[dict], collected: dict, generated_at: str) -> dict:
    """Shape the v1 object. Pure: no I/O, so the tests drive it directly."""
    city_trees = collected["city_trees"]
    enrichment = collected["enrichment"]
    detail = [
        {
            "code": c["code"],
            "name": c["name"],
            "country": c["country"],
            "trees": int(city_trees.get(c["code"], 0)),
            "ecoregion": collected["regions"].get(c["code"]),
        }
        for c in cities
    ]
    with_data = [c for c in detail if c["trees"] > 0]

    totals = {s: sum(by_city.values()) for s, by_city in collected["species_city"].items()}
    ranked = sorted(totals, key=lambda s: (-totals[s], s))[:CATALOGUE_SIZE]
    taken: set[str] = set()
    catalogue = []
    for species in ranked:
        epithet, common = enrichment.get(species, (None, None))
        catalogue.append(
            {
                "species": species,
                "slug": slugify(species, taken),
                "common_name": common,
                "trees": totals[species],
                "is_genus_only": epithet is None and " " not in species.strip(),
                "cities": collected["species_city"][species],
            }
        )

    top_species = [
        {"scientific_name": s["species"], "common_name": s["common_name"], "slug": s["slug"], "trees": s["trees"]}
        for s in catalogue[:TOP_SPECIES]
    ]

    by_code = {c["code"]: c for c in detail}
    tour = sorted(
        cities,
        key=lambda c: TOUR_ORDER.index(c["code"]) if c["code"] in TOUR_ORDER else len(TOUR_ORDER),
    )
    field_notes = []
    for city in tour:
        if by_code[city["code"]]["trees"] <= 0:
            continue
        # Stable over catalogue rank, so a tie keeps the catalogue's order.
        observations = sorted(
            (
                {
                    "species": s["species"],
                    "commonName": s["common_name"] or s["species"],
                    "slug": s["slug"],
                    "trees": s["cities"][city["code"]],
                }
                for s in catalogue
                if not s["is_genus_only"] and s["cities"].get(city["code"], 0) > 0
            ),
            key=lambda o: -o["trees"],
        )[:OBSERVATIONS_PER_STOP]
        if observations:
            field_notes.append(
                {
                    "code": city["code"],
                    "city": city["name"],
                    "country": city["country"],
                    "coordinates": city["coordinates"],
                    "observations": observations,
                }
            )

    stats = {
        "generated_at": generated_at,
        "world_tree_estimate": WORLD_TREE_ESTIMATE,
        "world_tree_source": WORLD_TREE_SOURCE,
        "total_trees": sum(c["trees"] for c in detail),
        "city_count": len(detail),
        "cities_with_data": len(with_data),
        "country_count": len({c["country"] for c in with_data}),
        "species_count": len(totals),
        "data_version": DATA_VERSION,
        "cities": detail,
        "top_species": top_species,
    }
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        # One collection feeds all three, so they share its time.
        "source_generated_at": {"stats": generated_at, "species": generated_at, "datasets": generated_at},
        "stats": stats,
        "catalogue_count": len(catalogue),
        "city_links": {
            c["code"]: city_url(c["code"]) for c in cities if c["code"] in collected["published"]
        },
        "field_notes": field_notes,
    }


_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CODE = re.compile(r"^[A-Z0-9]{5}$")


def _text(v) -> bool:
    return isinstance(v, str) and bool(v.strip())


def _count(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 2**53 - 1


def _date(v) -> bool:
    if not _text(v):
        return False
    try:
        datetime.fromisoformat(v)
    except ValueError:
        return False
    return True


def _https(v) -> bool:
    if not _text(v):
        return False
    u = urlparse(v)
    return u.scheme == "https" and bool(u.netloc) and not u.username and not u.password


def validate(feed: dict) -> None:
    """The website's `isHomepageFeed`, plus the rules it cannot check from the
    outside (ordering, top-species sort, observation ranking). Raises with
    every problem found, so one failed run names all of them."""
    problems: list[str] = []

    def check(ok: bool, message: str) -> None:
        if not ok:
            problems.append(message)

    check(feed.get("schema_version") == SCHEMA_VERSION, "schema_version must be 1")
    check(_date(feed.get("generated_at")), "generated_at is not an ISO timestamp")
    s = feed.get("stats") or {}
    src = feed.get("source_generated_at") or {}
    check(all(_date(src.get(k)) for k in ("stats", "species", "datasets")), "source_generated_at incomplete")
    check(feed.get("generated_at") == s.get("generated_at") == src.get("stats"), "generated_at disagrees")
    for key in ("data_version", "total_trees", "city_count", "cities_with_data", "country_count",
                "species_count", "world_tree_estimate"):
        check(_count(s.get(key)), f"stats.{key} is not a count")
    check(bool(s.get("total_trees")) and bool(s.get("species_count")), "no trees or no species")
    check(_count(feed.get("catalogue_count")) and feed.get("catalogue_count", 0) > 0, "empty catalogue")
    check("stale" not in s, "a publisher never ships a stale fallback")
    source = s.get("world_tree_source") or {}
    check(_text(source.get("citation")) and _https(source.get("url")), "world_tree_source incomplete")

    cities = s.get("cities") or []
    check(bool(cities), "no cities")
    for c in cities:
        eco = c.get("ecoregion")
        check(
            _CODE.match(c.get("code") or "") is not None and _text(c.get("name")) and _text(c.get("country"))
            and _count(c.get("trees"))
            and (eco is None or (_text(eco.get("name")) and eco.get("match") in ("contains", "nearby"))),
            f"bad city row {c.get('code')}",
        )
    by_code = {c.get("code"): c for c in cities}
    populated = [c for c in cities if (c.get("trees") or 0) > 0]
    check(len(by_code) == len(cities), "duplicate city codes")
    check(s.get("city_count") == len(cities), "city_count disagrees with cities")
    check(s.get("cities_with_data") == len(populated), "cities_with_data disagrees")
    check(s.get("country_count") == len({c["country"] for c in populated}), "country_count disagrees")
    check(s.get("total_trees") == sum(c.get("trees") or 0 for c in cities), "total_trees is not the sum of cities")

    top = s.get("top_species") or []
    check(0 < len(top) <= TOP_SPECIES, "top_species must hold 1-10 entries")
    for p in top:
        check(
            _text(p.get("scientific_name")) and (p.get("common_name") is None or isinstance(p["common_name"], str))
            and _SLUG.match(p.get("slug") or "") is not None and _count(p.get("trees"))
            and p["trees"] <= (s.get("total_trees") or 0),
            f"bad top species {p.get('scientific_name')}",
        )
    check(top == sorted(top, key=lambda p: (-p["trees"], p["scientific_name"])), "top_species out of order")
    check(not {p.get("scientific_name") for p in top} & set(SENTINELS), "a sentinel ranks as a species")

    links = feed.get("city_links")
    check(isinstance(links, dict) and all(k in by_code and _https(v) for k, v in links.items()), "bad city_links")

    notes = feed.get("field_notes") or []
    check(bool(notes), "v1 needs at least one globe stop")
    check(len({n.get("code") for n in notes}) == len(notes), "a globe stop repeats")
    for n in notes:
        city = by_code.get(n.get("code"))
        coords = n.get("coordinates")
        obs = n.get("observations") or []
        ok = (
            city is not None and n.get("city") == city["name"] and n.get("country") == city["country"]
            and isinstance(coords, list) and len(coords) == 2
            and all(isinstance(x, (int, float)) and isfinite(x) for x in coords)
            and abs(coords[0]) <= 180 and abs(coords[1]) <= 90
            and 0 < len(obs) <= OBSERVATIONS_PER_STOP
            and all(
                _text(o.get("species")) and _text(o.get("commonName")) and _SLUG.match(o.get("slug") or "")
                and _count(o.get("trees")) and 0 < o["trees"] <= city["trees"]
                for o in obs
            )
            and [o["trees"] for o in obs] == sorted((o["trees"] for o in obs), reverse=True)
        )
        check(ok, f"bad globe stop {n.get('code')}")
    order = [n.get("code") for n in notes]
    lead = [c for c in TOUR_ORDER if c in order]
    check(order[: len(lead)] == lead, "the tour does not open in TOUR_ORDER")

    if problems:
        raise PublishError("candidate failed validation: " + "; ".join(problems))


def content_of(feed: dict) -> dict:
    """The feed minus its timestamps: what decides whether a run changed anything."""
    stripped = {k: v for k, v in feed.items() if k not in ("generated_at", "source_generated_at")}
    stripped["stats"] = {k: v for k, v in feed.get("stats", {}).items() if k != "generated_at"}
    return stripped


def dumps(feed: dict) -> bytes:
    return (json.dumps(feed, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")


# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="build and validate, never upload")
    parser.add_argument("--output", type=Path, help="also write the candidate here")
    args = parser.parse_args(argv)

    started = now_iso()
    client = HmacClient.from_env()
    if client is None and not args.dry_run:
        raise PublishError("GOOGLE_HMAC_KEY / GOOGLE_HMAC_SECRET are not set")

    cities = load_cities()
    live, live_generation = read_live(client)
    log(f"live feed: generation {live_generation or 'none'}"
        + (f", generated_at {live['generated_at']}" if live else ""))
    generations = snapshot_inputs(cities, client)
    log("inputs: " + ", ".join(f"{k}@{v}" for k, v in generations.items() if v is not None))

    collected = collect(cities, generations, live)
    feed = build_feed(cities, collected, started)
    validate(feed)
    body = dumps(feed)
    s = feed["stats"]
    log(
        f"candidate: {s['total_trees']:,} trees, {s['cities_with_data']}/{s['city_count']} cities, "
        f"{s['country_count']} countries, {s['species_count']:,} species, "
        f"{len(feed['field_notes'])} globe stops, {len(body):,} bytes"
    )
    if args.output:
        args.output.write_bytes(body)
        log(f"wrote {args.output}")

    public_url = f"{PUBLIC_HOST}/{BUCKET}/{OBJECT}"
    if live is not None and content_of(live) == content_of(feed):
        # Leave the live object, and its honest generated_at, alone.
        log(f"unchanged since {live['generated_at']}; not uploading")
    elif args.dry_run:
        log("dry run: not uploading")
        return 0
    else:
        generation = client.put(BUCKET, OBJECT, body, if_generation_match=live_generation)
        log(f"published gs://{BUCKET}/{OBJECT} generation {generation} (replaced {live_generation or 'nothing'})")
    print(f"::trilogy-output name=homepage_feed kind=link value={public_url}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublishError as exc:
        print(f"[homepage] not published: {exc}", file=sys.stderr)
        raise SystemExit(1)

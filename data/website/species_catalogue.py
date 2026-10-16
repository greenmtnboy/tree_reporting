"""Aggregate species for the upstream website release.

Ported from arborary-world/website's collector. The publisher supplies a
connection whose trees view and enrichment/ecoregion URLs are pinned to
published GCS generations. This module has no website-checkout dependency.
"""

from __future__ import annotations

import re
from collections import defaultdict

from publish_homepage import now_iso, slugify, SENTINELS
SENTINEL_SQL = ", ".join("'" + value.replace("'", "''") + "'" for value in SENTINELS)

TOP_N = 2000

# Diameter at breast height is in inches.  Anything past this is a data-entry
# artefact (a circumference, a millimetre value, a typo), not a tree; a real
# 20-foot-diameter trunk does not stand on a city street.
DBH_MAX_INCHES = 240

# Age in years from `plant_date`, or NULL where the date is missing or absurd.
#
# The floor is 1500, not something more recent, because the oldest dated trees
# are real: the Endicott Pear in Danvers (1632, an OSM row in USBOS), the Jardin
# des Plantes cedar (1734) and Robinia (1600) in Paris, and Berlin oaks from
# 1700 with 50-inch trunks.  Below 1500 the data is unambiguous junk: two-digit
# years parsed as AD 8 or AD 32, Amsterdam's 1000-01-01 sentinel, a DC row
# dated 1170 with a zero diameter.  Future dates are typos (2108, 3024) and
# also nulled.  This is defence in depth; the same rule belongs upstream in
# the per-city normalisers so the published column is clean.
AGE_MIN_YEAR = 1500
AGE_SQL = (
    f"CASE WHEN plant_date IS NOT NULL AND year(plant_date) >= {AGE_MIN_YEAR} "
    "AND plant_date <= current_date THEN date_diff('day', plant_date, current_date) / 365.25 END"
)

# A city's median age is only meaningful when a decent share of that species'
# trees there carry a plant date.  New York dates 12% of its trees and nearly
# all of them are recent plantings, so its "median age" would read as 4 years.
AGE_MIN_SHARE = 0.25
AGE_MIN_TREES = 5

ENRICHMENT_COLUMNS = [
    "genus",
    "species_epithet",
    "common_names",
    "description",
    "is_evergreen",
    "mature_height_min_ft",
    "mature_height_max_ft",
    "canopy_spread_min_ft",
    "canopy_spread_max_ft",
    "growth_rate",
    "lifespan_min_years",
    "lifespan_max_years",
    "drought_tolerance",
    "water_needs",
    "sun_exposure",
    "soil_preferences",
    "root_behavior",
    "coastal_tolerance",
    "salt_tolerance",
    "pollution_tolerance",
    "bloom_months",
    "wildlife_value",
    "fire_risk",
    "tree_form",
    "usda_zone_min",
    "usda_zone_max",
    "native_ecoregions",
    "photo_url",
    "photo_license",
    "photo_attribution",
    "trunk_photo_url",
    "trunk_photo_license",
    "trunk_photo_attribution",
]


def collect(con, cities: list[dict], enrichment_source: str, ecoregion_source: str,
            slug_registry: dict[str, str]) -> dict:
    """Build from an already registered, generation-pinned trees relation."""
    city_meta = {c["code"]: c for c in cities}

    # Named (non-sentinel) trees per city: the denominator for "share of city".
    city_named = dict(
        con.execute(
            f"SELECT city, count(*) FROM trees "
            f"WHERE species IS NOT NULL AND species NOT IN ({SENTINEL_SQL}) GROUP BY 1"
        ).fetchall()
    )
    named_total = sum(city_named.values())

    # Species x city, restricted to the top N species overall.  One scan over
    # the three projected columns does both the ranking and the breakdown.
    rows = con.execute(
        f"""
        WITH named AS (
            SELECT city, species, diameter_at_breast_height AS dbh, {AGE_SQL} AS age
            FROM trees
            WHERE species IS NOT NULL AND species NOT IN ({SENTINEL_SQL})
        ),
        top AS (
            SELECT species, count(*) AS trees
            FROM named GROUP BY 1 ORDER BY trees DESC, species LIMIT {TOP_N}
        )
        SELECT n.species, n.city, count(*) AS trees,
               count(CASE WHEN dbh > 0 AND dbh <= {DBH_MAX_INCHES} THEN 1 END) AS dbh_n,
               quantile_cont(CASE WHEN dbh > 0 AND dbh <= {DBH_MAX_INCHES} THEN dbh END, 0.5) AS dbh_median,
               count(age) AS age_n,
               quantile_cont(age, 0.5) AS age_median
        FROM named n JOIN top USING (species)
        GROUP BY 1, 2
        """
    ).fetchall()

    per_species: dict[str, list] = defaultdict(list)
    for species, city, trees, dbh_n, dbh_median, age_n, age_median in rows:
        per_species[species].append((city, trees, dbh_n, dbh_median, age_n, age_median))

    # Whole-species trunk and age statistics need the raw rows again (a median
    # is not a weighted mean of medians).  Same scan, different grouping.
    dbh_rows = con.execute(
        f"""
        WITH named AS (
            SELECT species, diameter_at_breast_height AS dbh FROM trees
            WHERE species IS NOT NULL AND species NOT IN ({SENTINEL_SQL})
              AND diameter_at_breast_height > 0 AND diameter_at_breast_height <= {DBH_MAX_INCHES}
        )
        SELECT species, count(*), quantile_cont(dbh, 0.5), avg(dbh), quantile_cont(dbh, 0.9)
        FROM named GROUP BY 1
        """
    ).fetchall()
    dbh_by_species = {s: (n, med, mean, p90) for s, n, med, mean, p90 in dbh_rows}

    age_rows = con.execute(
        f"""
        WITH named AS (
            SELECT species, {AGE_SQL} AS age FROM trees
            WHERE species IS NOT NULL AND species NOT IN ({SENTINEL_SQL})
        )
        SELECT species, count(age), quantile_cont(age, 0.5), quantile_cont(age, 0.1), quantile_cont(age, 0.9)
        FROM named WHERE age IS NOT NULL GROUP BY 1
        """
    ).fetchall()
    age_by_species = {s: (n, med, p10, p90) for s, n, med, p10, p90 in age_rows}

    enrichment = {
        r[0]: dict(zip(ENRICHMENT_COLUMNS, r[1:]))
        for r in con.execute(
            f"SELECT species, {', '.join(ENRICHMENT_COLUMNS)} FROM read_parquet('{enrichment_source}')"
        ).fetchall()
    }

    # Aggregate every named entry, including those outside the top-N pages and
    # records identified only to genus. Never rank genera from the short index.
    species_counts = con.execute(
        f"SELECT species, count(*) FROM trees WHERE species IS NOT NULL "
        f"AND species NOT IN ({SENTINEL_SQL}) GROUP BY 1"
    ).fetchall()
    genera = rank_genera(species_counts, enrichment)

    ecoregions = {
        int(i): {"id": int(i), "name": name, "biome": biome, "realm": realm}
        for i, name, biome, realm in con.execute(
            f"SELECT ecoregion_id, ecoregion_name, biome, realm FROM read_parquet('{ecoregion_source}')"
        ).fetchall()
    }

    ranked = sorted(per_species.items(), key=lambda kv: (-sum(r[1] for r in kv[1]), kv[0]))
    taken: set[str] = set(slug_registry.values()) | {'index', 'detail'}
    catalogue = []
    for rank, (species, city_rows) in enumerate(ranked, start=1):
        trees = sum(r[1] for r in city_rows)
        if species not in slug_registry:
            slug_registry[species] = slugify(species, taken)
        e = enrichment.get(species, {})
        common_names = [n for n in (e.get("common_names") or []) if n]
        genus = e.get("genus") or species.split(" ")[0]
        epithet = e.get("species_epithet")
        dbh = dbh_by_species.get(species)
        age = age_by_species.get(species)
        age_ok = age is not None and age[0] >= AGE_MIN_TREES and age[0] / trees >= AGE_MIN_SHARE

        city_breakdown = sorted(
            (
                {
                    "code": city,
                    "name": city_meta.get(city, {}).get("name", city),
                    "country": city_meta.get(city, {}).get("country"),
                    "trees": int(n),
                    "share_of_city": round(n / city_named[city], 5) if city_named.get(city) else None,
                    "dbh_median_in": round(med, 1) if med is not None else None,
                    "age_n": int(age_n),
                    # Null unless enough of this species' trees here are dated
                    # to make a median honest; see AGE_MIN_SHARE.
                    "age_median_yrs": (
                        round(age_med, 1)
                        if age_med is not None and age_n >= AGE_MIN_TREES and age_n / n >= AGE_MIN_SHARE
                        else None
                    ),
                }
                for city, n, dbh_n, med, age_n, age_med in city_rows
            ),
            key=lambda c: -c["trees"],
        )

        catalogue.append(
            {
                "rank": rank,
                "slug": slug_registry[species],
                "species": species,
                "genus": genus,
                "genus_rank": genera[genus]["rank"],
                "genus_trees": genera[genus]["trees"],
                "epithet": epithet,
                "is_genus_only": epithet is None and " " not in species.strip(),
                "common_name": common_names[0] if common_names else None,
                "common_names": common_names,
                "trees": int(trees),
                "share_of_named": round(trees / named_total, 6),
                "city_count": len(city_breakdown),
                "cities": city_breakdown,
                "dbh": (
                    {
                        "n": int(dbh[0]),
                        "median_in": round(dbh[1], 1),
                        "mean_in": round(dbh[2], 1),
                        "p90_in": round(dbh[3], 1),
                    }
                    if dbh
                    else None
                ),
                # Whole-species age, from the cities that date their trees.
                # `dated_share` says how much of the species that covers.
                "age": (
                    {
                        "n": int(age[0]),
                        "dated_share": round(age[0] / trees, 3),
                        "median_yrs": round(age[1], 1),
                        "p10_yrs": round(age[2], 1),
                        "p90_yrs": round(age[3], 1),
                    }
                    if age_ok
                    else None
                ),
                "enriched": species in enrichment,
                "description": e.get("description"),
                "traits": {
                    "tree_form": e.get("tree_form"),
                    "is_evergreen": e.get("is_evergreen"),
                    "mature_height_ft": _range(e.get("mature_height_min_ft"), e.get("mature_height_max_ft")),
                    "canopy_spread_ft": _range(e.get("canopy_spread_min_ft"), e.get("canopy_spread_max_ft")),
                    "lifespan_years": _range(e.get("lifespan_min_years"), e.get("lifespan_max_years")),
                    "usda_zones": _range(e.get("usda_zone_min"), e.get("usda_zone_max")),
                    "growth_rate": e.get("growth_rate"),
                    "drought_tolerance": e.get("drought_tolerance"),
                    "water_needs": e.get("water_needs"),
                    "sun_exposure": e.get("sun_exposure") or [],
                    "soil_preferences": e.get("soil_preferences") or [],
                    "root_behavior": e.get("root_behavior"),
                    "coastal_tolerance": e.get("coastal_tolerance"),
                    "salt_tolerance": e.get("salt_tolerance"),
                    "pollution_tolerance": e.get("pollution_tolerance"),
                    "wildlife_value": e.get("wildlife_value"),
                    "fire_risk": e.get("fire_risk"),
                    "bloom_months": sorted(e.get("bloom_months") or []),
                },
                "native_ecoregions": [
                    ecoregions[i] for i in (e.get("native_ecoregions") or []) if i in ecoregions
                ],
                "photo": _photo(e),
                "trunk_photo": _photo(e, "trunk_photo"),
            }
        )

    total_species = con.execute(
        f"SELECT count(DISTINCT species) FROM trees WHERE species IS NOT NULL AND species NOT IN ({SENTINEL_SQL})"
    ).fetchone()[0]

    return {
        "generated_at": now_iso(),
        "top_n": TOP_N,
        "species_count_total": int(total_species),
        "genus_count_total": len(genera),
        "named_trees_total": int(named_total),
        "named_trees_in_catalogue": int(sum(s["trees"] for s in catalogue)),
        "cities": [
            {"code": c["code"], "name": c["name"], "country": c["country"], "named_trees": int(city_named.get(c["code"], 0))}
            for c in cities
        ],
        "species": catalogue,
    }


_INAT = re.compile(r"^(https://inaturalist-open-data\.s3\.amazonaws\.com/photos/\d+/)medium\.(\w+)$", re.I)


def rank_genera(species_counts: list[tuple[str, int]], enrichment: dict) -> dict:
    totals: dict[str, int] = defaultdict(int)
    for species, trees in species_counts:
        genus = enrichment.get(species, {}).get("genus") or species.split(" ")[0]
        totals[genus] += trees
    # Competition ranks: genera with equal counts share a rank.
    result = {}
    previous_count, rank = None, 0
    for position, (genus, trees) in enumerate(sorted(totals.items(), key=lambda row: (-row[1], row[0])), 1):
        if trees != previous_count:
            rank = position
        result[genus] = {"rank": rank, "trees": trees}
        previous_count = trees
    return result


def _photo(e: dict, prefix: str = "photo") -> dict | None:
    """Every enrichment photo is an iNaturalist upload addressed as
    `.../photos/{id}/medium.{ext}`.  A handful of rows carry a truncated URL
    with no extension; those are broken links, so they count as no photo.
    `thumb` is the 75px `square` rendition for list views."""
    m = _INAT.match(e.get(f"{prefix}_url") or "")
    if not m:
        return None
    return {
        "url": m.group(0),
        "thumb": f"{m.group(1)}square.{m.group(2)}",
        "license": e.get(f"{prefix}_license"),
        "attribution": e.get(f"{prefix}_attribution"),
    }


def _range(lo, hi):
    if lo is None and hi is None:
        return None
    return [_num(lo), _num(hi)]


def _num(v):
    if v is None:
        return None
    return int(v) if float(v).is_integer() else round(float(v), 1)


def to_index(catalogue: dict) -> dict:
    """The fields the list page needs and nothing it does not."""
    return {
        "generated_at": catalogue["generated_at"],
        "schema_version": 1,
        "revision": catalogue["revision"],
        "genus_count_total": catalogue["genus_count_total"],
        "cities": catalogue["cities"],
        "slug_registry": catalogue["slug_registry"],
        "top_n": catalogue["top_n"],
        "species_count_total": catalogue["species_count_total"],
        "named_trees_total": catalogue["named_trees_total"],
        "named_trees_in_catalogue": catalogue["named_trees_in_catalogue"],
        "species": [
            {
                "rank": s["rank"],
                "slug": s["slug"],
                "species": s["species"],
                "genus": s["genus"],
                "common_name": s["common_name"],
                "trees": s["trees"],
                "city_count": s["city_count"],
                "top_city": s["cities"][0]["code"] if s["cities"] else None,
                "tree_form": s["traits"]["tree_form"],
                "is_evergreen": s["traits"]["is_evergreen"],
                "thumb": s["photo"]["thumb"] if s["photo"] else None,
            }
            for s in catalogue["species"]
        ],
    }


"""Notable-tree registers: link each register tree to the inventory tree it is.

NOT a uv inline script -- a regular importable module, like `shared.ingest`.

A notable register (`NOTABLE_{code}`: Milan's alberi monumentali, Tree Boston's
Big Old Trees) names trees the city's inventory already holds, and its
coordinates are a register's rather than a survey's.  Measured on Milan's 13
monuments against the inventory: two sit within 5 m of their own tree, and
three sit within 2 m of a *different* tree (a hackberry beside an elm, a plane
beside a ginkgo).  The shared grid match in tree_dedup.preql would hang the
monument's name on whatever tree shares its cell, so a register row never
takes part in it.  Instead its ingest links it here, once, to one inventory
row, and emits that row's id as `linked_tree_id`; tree_dedup puts the register
row in that tree's cluster and nowhere else.

The rule (`link_to_inventory`): the nearest inventory tree of the **same
genus**, within `max_metres`, whose trunk is within `dbh_tolerance` of the
register's (when both are measured).  A monumental tree is the biggest of its
genus for some distance around, so trunk size is what separates it from the
younger tree of the same genus beside it -- Milan's Platanus at Giardini
Montanelli has an 8 cm plane 21 m away and nothing of its own 199 cm within
30 m, and is left unlinked rather than named onto a sapling.

**A register is metadata, never a tree.**  An unlinked row is dropped
(`notable_table`): a register's point is not a survey's, so a monument the
inventory does not hold, or holds too far off to trust, is not added to the
map as a new tree at the register's coordinates.  It comes back on its own
when the inventory or the link rule finds its tree.

**The ids are the inventory partition's own.**  The caller passes the table the
municipal ingest emits -- the same reader, the same filters, after
`enforce_tree_schema` -- so a link never names an id the build does not hold.
A dangling link would put the register row in a cluster with no survivor, and
the tree would vanish without an error.
"""

from __future__ import annotations

import math
import re
import sys

import pyarrow as pa

# Within this distance a register tree and an inventory tree can be the same
# one.  Milan's linked monuments sit 3.7-23.9 m from their inventory point; the
# next same-genus tree of a matching size is 37 m or more away for every one.
DEFAULT_MAX_METRES = 30.0
# |inventory dbh - register dbh| / register dbh.  Milan's six links agree to
# 0-9% (a 644 cm girth is 205 cm across, the inventory says 205).
DEFAULT_DBH_TOLERANCE = 0.30

_EARTH_METRES_PER_DEGREE = 111_320.0


def genus_of(species: str | None) -> str | None:
    """`Platanus` from `Platanus x acerifolia`, `Platanus acerifolia (Aiton) Willd`."""
    if not species:
        return None
    head = species.strip().split()
    if not head:
        return None
    genus = head[0].capitalize()
    return genus if re.fullmatch(r"[A-Z][a-z]+", genus) else None


def diameter_from_circumference_cm(text: str | None) -> float | None:
    """A trunk diameter in centimetres from a register's girth field.

    Registers write a multi-stemmed tree's girths together (`410 -300`,
    `350 - 300`); the largest stem is the one the tree is listed for, and the
    one an inventory measures, so that is what is compared.
    """
    if text is None:
        return None
    values = [float(v) for v in re.findall(r"\d+(?:[.,]\d+)?", str(text).replace(",", "."))]
    values = [v for v in values if v > 0]
    return max(values) / math.pi if values else None


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dy = (lat2 - lat1) * _EARTH_METRES_PER_DEGREE
    dx = (lon2 - lon1) * _EARTH_METRES_PER_DEGREE * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dx, dy)


def link_to_inventory(
    register: list[dict],
    inventory: pa.Table | None,
    *,
    max_metres: float = DEFAULT_MAX_METRES,
    dbh_tolerance: float = DEFAULT_DBH_TOLERANCE,
    label: str = "",
) -> list[str | None]:
    """The inventory `tree_id` each register row is, or None.

    *register* rows carry `species`, `latitude`, `longitude` and `dbh_in`
    (inches, or None).  *inventory* is the municipal ingest's emitted table:
    `tree_id`, `species`, `latitude`, `longitude`, `diameter_at_breast_height`
    (inches).  Two register rows never link to the same tree: the closer one
    keeps it and the other is left unlinked.  An empty register needs no
    inventory (None), so its ingest need not download one.
    """
    if not register or inventory is None:
        return [None] * len(register)
    by_genus: dict[str, list[tuple[str, float, float, float | None]]] = {}
    genera = {genus_of(r.get("species")) for r in register} - {None}
    ids = inventory.column("tree_id").to_pylist()
    species = inventory.column("species").to_pylist()
    lats = inventory.column("latitude").to_pylist()
    lons = inventory.column("longitude").to_pylist()
    dbhs = inventory.column("diameter_at_breast_height").to_pylist()
    for tid, sp, lat, lon, dbh in zip(ids, species, lats, lons, dbhs):
        genus = genus_of(sp)
        if genus in genera and lat is not None and lon is not None:
            by_genus.setdefault(genus, []).append((tid, lat, lon, dbh))

    candidates: list[tuple[float, int, str]] = []
    for index, row in enumerate(register):
        genus = genus_of(row.get("species"))
        lat, lon = row.get("latitude"), row.get("longitude")
        if genus is None or lat is None or lon is None:
            continue
        want = row.get("dbh_in")
        best: tuple[float, str] | None = None
        for tid, tlat, tlon, tdbh in by_genus.get(genus, ()):
            if abs(tlat - lat) * _EARTH_METRES_PER_DEGREE > max_metres:
                continue
            distance = _distance_m(lat, lon, tlat, tlon)
            if distance > max_metres:
                continue
            if want and tdbh and abs(tdbh - want) / want > dbh_tolerance:
                continue
            if best is None or distance < best[0]:
                best = (distance, tid)
        if best is not None:
            candidates.append((best[0], index, best[1]))

    links: list[str | None] = [None] * len(register)
    taken: set[str] = set()
    for _, index, tid in sorted(candidates):
        if tid in taken:
            continue
        taken.add(tid)
        links[index] = tid
    print(
        f"{label or 'Notable'} register: {sum(1 for l in links if l)} of "
        f"{len(register)} linked to an inventory tree (same genus, within "
        f"{max_metres:g} m, trunk within {dbh_tolerance:.0%}); the rest publish "
        f"at the register's own point",
        file=sys.stderr,
    )
    return links


def notable_table(
    rows: list[dict],
    links: list[str | None],
    *,
    city: str,
) -> pa.Table:
    """The register's linked rows as the tree schema plus the three notable
    columns.  An unlinked row is dropped: a register only lends its name and
    blurb to a tree the inventory holds (see the module docstring).

    `enforce_tree_schema` is still the caller's to run (with
    `proper_names=True`); `significance`, `significance_source` and
    `linked_tree_id` pass through it as extra columns.
    """
    kept = [(r, link) for r, link in zip(rows, links, strict=True) if link is not None]
    if len(kept) < len(rows):
        print(
            f"{city} notable register: {len(rows) - len(kept)} of {len(rows)} rows match no "
            "inventory tree and are dropped",
            file=sys.stderr,
        )
    rows = [r for r, _ in kept]
    links = [link for _, link in kept]
    return pa.table(
        {
            "tree_id": pa.array([r["tree_id"] for r in rows], type=pa.string()),
            "city": pa.array([city] * len(rows), type=pa.string()),
            "species": pa.array([r.get("species") for r in rows], type=pa.string()),
            "tree_name": pa.array([r.get("tree_name") for r in rows], type=pa.string()),
            "latitude": pa.array([r.get("latitude") for r in rows], type=pa.float64()),
            "longitude": pa.array([r.get("longitude") for r in rows], type=pa.float64()),
            "diameter_at_breast_height": pa.array(
                [r.get("dbh_in") for r in rows], type=pa.float64()
            ),
            "significance": pa.array([r.get("significance") for r in rows], type=pa.string()),
            "significance_source": pa.array(
                [r.get("significance_source") for r in rows], type=pa.string()
            ),
            "linked_tree_id": pa.array(links, type=pa.string()),
        }
    )

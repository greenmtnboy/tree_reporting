#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Barcelona's municipal trees, from the Biodiversity Atlas tree layer (Open Data BCN).

Source: the Ajuntament de Barcelona's *Atles de la Biodiversitat* tree
inventory, published on `opendata-ajuntament.barcelona.cat` (CKAN) as three
datasets, each "Creative Commons Attribution 4.0" (`license_id` CC-BY-4.0 on
every package):

* `atles-biodiversitat-arbrat-geometries`, resource `7c64504a`: one point per
  tree, `geom_fid` and `geom_wgs84` (WKT `POINT (lon lat)`) -- 243,999 rows
  on 2026-11-22;
* `atles-biodiversitat-arbrat-rel-geometria-especie`, resource `506c3c5d`:
  `geom_fid` -> `especie_id`, one row per tree (243,999);
* `atles-biodiversitat-arbrat-especies`, resource `c654c488`: `especie_id`
  -> `nom_ctf`, the scientific name (525 taxa).

**Why the atlas and not the three inventory datasets.**  The inventory proper
is `arbrat-viari` (street trees, the majority), `arbrat-zona` and
`arbrat-parcs`.  Street trees have no datastore and every file download on
the portal answers 403 to a scripted client (still so on 2026-11-22), so they
cannot be read.  The atlas is the whole city in one layer: 98% of zona and
parcs trees planted before 2023 sit within 1 cm of an atlas point, and the
other ~187k atlas points are the street trees.  What it costs:

* it is a **snapshot published 2025-02-25** (`fecha_publicacion`), not the
  weekly reload its `frequency` claims: most zona trees planted in 2025-2026
  are not in it;
* it carries **no planting date, diameter, height or crown** -- the inventory
  datasets have no diameter either, only a planting date on ~10% of rows.

**`geom_fid` is the per-tree id**: unique and non-null over all 243,999 rows
(checked 2026-11-22) and sparse (1,217..1,062,832), the feature id of an
edited GIS layer rather than a row number of a reload.  Prefixed `bcn-`.
The run refuses a short read of any of the three resources (rows read vs the
datastore's `total`), and refuses when more than 1% of points have no species
row.

* `nom_ctf` is the scientific name, cultivar quoted, hybrid sign `×`
  (`Platanus × acerifolia`).  Registered trade designations are written in
  capitals with `®` (`Acer × freemanii AUTUMN BLAZE® 'Jeffersred'`, `Malus
  PERPETU® 'Evereste'`); they are stripped so the cultivar is what follows
  the binomial.  `Washingtonia sp` is genus only.  A leading `X` names a
  nothogenus (`X chitalpa tashkentensis`, `X hesperotropsis leylandii`) and
  is dropped; a possessive inside a quoted cultivar (`'Paul's Scarlet'`,
  `'Alphen's Globe'`) is passed as the cultivar column, since the shared
  parser would close the quote on its apostrophe.
* `Washingtonia filifera var. robusta` is how the inventory writes the
  Mexican fan palm (3,227 rows); truncated to species rank it would fold into
  the California fan palm, so it is published as `Washingtonia robusta`, its
  accepted name at Kew (WCVP).
* The atlas `nom_cat` on a geometry names a catalogued *group* ("Plàtans de
  les Rambles") on 662 rows; it is not a tree's own name and is not mapped.
* The layer holds trees only: no planting sites, stumps or removed trees.
"""

import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    stream_to_table,
    validate_coordinates,
)
from shared.platforms.ckan import CkanResource, iter_datastore_rows, point_lon_lat

PORTAL = "opendata-ajuntament.barcelona.cat/data"
GEOMETRIES = CkanResource(PORTAL, "7c64504a-a718-48fe-b466-8c93a160b0dd", timeout=180)
GEOMETRY_SPECIES = CkanResource(PORTAL, "506c3c5d-8f9b-409d-ae96-c0912da4a505", timeout=180)
SPECIES = CkanResource(PORTAL, "c654c488-2822-4e51-ad9b-f443989a4b41", timeout=180)

# Refuse to publish when more than this share of points has no species row:
# the relation table and the geometries have drifted apart.
MAX_UNMATCHED_SPECIES = 0.01

# Infraspecific names whose species-rank truncation lands on a *different*
# accepted species.  Kew (WCVP) lists W. filifera var. robusta as a synonym of
# W. robusta.
INFRASPECIFIC_ACCEPTED = {
    "washingtonia filifera var. robusta": "Washingtonia robusta",
}
PLACEHOLDER_PREFIXES = ("indeterminat",)

# `AUTUMN BLAZE®`, `PERPETU®`, `RESISTA®`, `OBELISK®`: an unquoted token in
# capitals, or one carrying the registered sign, is a trade designation.
_REGISTERED = "®"
_TRADE_TOKEN = re.compile(r"^[A-Z][A-Z\-]{2,}$")
_POSSESSIVE_CULTIVAR = re.compile(r"'\s*([^']*\w's\b[^']*)'")


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def as_key(value) -> int | None:
    """A CKAN `numeric` id (`'490757'`) as an int; None when absent or fractional."""
    text = clean(value)
    if text is None:
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    if number != number.to_integral_value():
        return None
    return int(number)


def scientific_name(value) -> tuple[str | None, str | None]:
    """The portal's name with trade designations removed, and the cultivar
    where the shared parser would misread it (None lets it parse the quote)."""
    text = clean(value)
    if text is None or text == "-":
        return None, None
    low = text.lower()
    if low.startswith(PLACEHOLDER_PREFIXES):
        return None, None
    for prefix, accepted in INFRASPECIFIC_ACCEPTED.items():
        if low == prefix or low.startswith(prefix + " "):
            rest = text[len(prefix):].strip()
            return f"{accepted} {rest}".strip(), None
    tokens = []
    for token in text.split(" "):
        if token[:1] in "'‘’\"":
            tokens.append(token.replace(_REGISTERED, ""))
            continue
        if _REGISTERED in token or _TRADE_TOKEN.match(token):
            continue
        tokens.append(token)
    # `X chitalpa tashkentensis`, `X hesperotropsis leylandii`: a leading mark
    # names a nothogenus; the shared hygiene would keep `X chitalpa ...` as a
    # key of its own, so the mark is dropped, as Montpellier and Toulouse do.
    if len(tokens) > 2 and tokens[0] in ("X", "x", "×"):
        tokens = [tokens[1].capitalize(), *tokens[2:]]
    text = " ".join(tokens)
    if not text:
        return None, None
    # `'Alphen's Globe'`, `'Paul's Scarlet'`: an apostrophe inside the quoted
    # cultivar, which the shared parser would read as the closing quote.
    possessive = _POSSESSIVE_CULTIVAR.search(text)
    if possessive:
        return text[: possessive.start()].strip(), possessive.group(1).strip()
    return text, None


def datastore_total(resource: CkanResource) -> int:
    """The datastore's row count.

    Asked with `limit=1`: this portal leaves `total` out of a `limit=0`
    answer, which `shared.platforms.ckan.datastore_total` relies on.
    """
    payload = get_json_with_retry(
        f"{resource.api}/datastore_search",
        params={"resource_id": resource.resource_id, "limit": 1, "fields": "_id"},
        timeout=resource.timeout,
    )
    total = (payload.get("result") or {}).get("total")
    if total is None:
        raise RuntimeError(
            f"Barcelona datastore {resource.resource_id} reported no total"
        )
    return int(total)


def read_all(resource: CkanResource, fields: str, label: str) -> list[dict]:
    """Every row of a small-record resource, refusing a short read."""
    total = datastore_total(resource)
    rows = [
        rec
        for page in iter_datastore_rows(resource, fields=fields, sort="_id")
        for rec in page
    ]
    if len(rows) != total:
        raise RuntimeError(
            f"Barcelona {label}: read {len(rows)} rows of the {total} the "
            "datastore reports -- reloading mid-read?"
        )
    return rows


Taxon = tuple[str | None, str | None]  # (species text, cultivar override)
NO_TAXON: Taxon = (None, None)


def species_by_geometry() -> dict[int, Taxon]:
    names: dict[int, Taxon] = {}
    for rec in read_all(SPECIES, "especie_id,nom_ctf", "species table"):
        key = as_key(rec.get("especie_id"))
        if key is None:
            raise RuntimeError(f"Barcelona species table: unreadable especie_id {rec!r}")
        names[key] = scientific_name(rec.get("nom_ctf"))

    by_geometry: dict[int, Taxon] = {}
    repeated = unknown_species = 0
    for rec in read_all(GEOMETRY_SPECIES, "geom_fid,especie_id", "geometry-species"):
        fid = as_key(rec.get("geom_fid"))
        especie = as_key(rec.get("especie_id"))
        if fid is None:
            continue
        if fid in by_geometry:
            # Two species for one point: the id cannot say which one is right.
            repeated += 1
            by_geometry[fid] = NO_TAXON
            continue
        if especie not in names:
            unknown_species += 1
        by_geometry[fid] = names.get(especie, NO_TAXON)
    if repeated or unknown_species:
        print(
            f"Barcelona geometry-species: {repeated} repeated geom_fid(s) "
            f"(species nulled), {unknown_species} especie_id(s) not in the "
            "species table",
            file=sys.stderr,
        )
    return by_geometry


class Geometries:
    """The geometry pages, counted so a short read and a species gap refuse."""

    def __init__(self, species: dict[int, Taxon]):
        self.species = species
        self.total = datastore_total(GEOMETRIES)
        self.seen = 0
        self.unidentified = 0
        self.unmatched = 0

    def chunks(self):
        for page in iter_datastore_rows(
            GEOMETRIES, fields="geom_fid,geom_wgs84", sort="_id"
        ):
            self.seen += len(page)
            yield page

    def keep(self, rec: dict) -> bool:
        if as_key(rec.get("geom_fid")) is None:
            self.unidentified += 1
            return False
        return True

    def transform(self, rows: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        for rec in rows:
            fid = as_key(rec.get("geom_fid"))
            tree_id.append(f"bcn-{fid}")
            if fid not in self.species:
                self.unmatched += 1
            name, selection = self.species.get(fid, NO_TAXON)
            species.append(name)
            cultivar.append(selection)
            lon, lat = point_lon_lat(rec.get("geom_wgs84"))
            latitude.append(lat)
            longitude.append(lon)
        n = len(tree_id)
        return pa.table({
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["ESBCN"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array([None] * n, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array([None] * n, type=pa.float64()),
        })

    def check(self, kept: int) -> None:
        if self.seen != self.total:
            raise RuntimeError(
                f"Barcelona geometries: read {self.seen} rows of the {self.total} "
                "the datastore reports -- reloading mid-read?"
            )
        if self.unidentified:
            print(
                f"Barcelona: dropped {self.unidentified} point(s) with no geom_fid",
                file=sys.stderr,
            )
        if kept and self.unmatched / kept > MAX_UNMATCHED_SPECIES:
            raise RuntimeError(
                f"Barcelona: {self.unmatched} of {kept} points have no row in the "
                "geometry-species table -- have the atlas datasets drifted apart?"
            )
        if self.unmatched:
            print(
                f"Barcelona: {self.unmatched} point(s) with no species row "
                "(published as Unknown)",
                file=sys.stderr,
            )


if __name__ == "__main__":
    geometries = Geometries(species_by_geometry())
    table = stream_to_table(
        geometries.chunks(),
        geometries.transform,
        keep=geometries.keep,
        label="Barcelona OpenData",
    )
    geometries.check(table.num_rows)
    table = validate_coordinates(table, city="Barcelona", city_code="ESBCN")
    table = enforce_tree_schema(
        table, city="Barcelona", data_source="BARCELONA_OPENDATA"
    )
    emit(table)

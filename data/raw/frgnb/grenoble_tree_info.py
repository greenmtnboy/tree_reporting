#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Grenoble's tree inventory: the Ville de Grenoble's layer plus the Métropole's.

Both are published on Grenoble-Alpes Métropole's open-data portal
(data.metropolegrenoble.fr, a data4citizen/CKAN site) under the **Open Data
Commons Open Database License 1.0** -- each dataset's `license_title` says so
and links https://opendefinition.org/licenses/odc-odbl.  The portal's
records API (`/d4c/api/records/1.0/...`) is slow to the point of timing out;
each dataset's CSV resource is a static file and is what this reads.  Its
current path is resolved from the dataset's `/d4c/api/datasets/1.0/{id}/`
record (`resources_versions`), so a re-upload under a new name is followed.

1. **`arbres-grenoble`**, *Arbres de la Ville de Grenoble*, published by the
   Ville de Grenoble: every tree in the commune, managed by the city (15.7k)
   or by the Métropole (15.2k).  31,670 rows when this was written; the CSV
   was last re-uploaded 2026-02-28.
2. **`patrimoine_arbore_du_territoire_metropolitain`**, *Patrimoine arboré du
   territoire Métropolitain*, published by Grenoble-Alpes Métropole: the
   Métropole-managed trees (street trees, mostly) across its communes.  33,779
   rows, a snapshot uploaded 2023-09-11 and not refreshed since.  Its 15.1k
   Grenoble rows are the same trees as layer 1's Métropole-managed ones (14.2k
   lie within 3 m of a layer-1 tree, and none of the other communes' rows
   do), so only rows whose `commune` is not Grenoble are kept: 18.6k trees in
   ~45 communes (Échirolles 4.0k, Saint-Martin-d'Hères 2.0k, Fontaine 1.2k,
   Eybens 1.1k, Saint-Égrève 1.1k, ...).  17 rows with no commune are dropped
   with a count (they cannot be told from Grenoble's).  About 150 rows in the
   southern communes (Saint-Georges-de-Commiers 125, Vif, Notre-Dame-de-
   Commiers, ...) lie south of 45.05 and fall outside the city's territory box.

Meylan and Saint-Égrève publish their own (2023, static) layers on the same
portal; both include the Métropole's trees in those communes again and are not
read here.

**Ids.**  Layer 1 is keyed on `nom` (`AR36318`), the tree's asset reference
(equal to `bien_reference` on every row), verified non-null and unique over
the whole layer; published as `grenoble-AR36318`.  In layer 2 `id_metro` is
neither (780 blank, 1.4k repeated across distinct trees), so it is keyed on
`ogc_fid` (`M46301`), non-null and unique over all 33,779 rows and frozen with
the snapshot; published as `grenoble-metro-M46301`.

**Species.**  Layer 1 has `genre_bota` (genus), `espece` (one lower-case
epithet token) and `variete` (a cultivar, title-cased: `Chanticleer`,
`Austriaca`, `Inermis skyline`); `inconnu` is unknown.  `Inermis` is the
botanical variety of Gleditsia triacanthos and is dropped from the cultivar
(`Inermis skyline` -> `Skyline`).  A `variete` with no `espece` is the
cultivar of a genus (`Prunus` / `Collingwood Ingram`) except `Pubescens`
(`Quercus`), which is the epithet.  Layer 2 has `genre` and `espece`, the
latter sometimes carrying a hybrid sign (`x canescens`, `X freemanii`) and an
unquoted cultivar (`americana Autumn Purple`, `calleryana Chanticleer`); a
few genera carry the epithet too (`Liquidambar styraciflua Manon`).
Placeholders (`.`, `n.d.`, blank, `Inconnu`) are genus-only or unknown.  A
handful of `genre` values are French common names (`Erable`, `Cypres`,
`Troene`, `Tulipier`, 12 rows); common names are not mapped, so they are
published as unknown with a count.  `nom_commun` (common name) is not used.

**No diameter.**  Layer 1's `diametrearbre` is empty on every row and layer 2
has no measurement, so `diameter_at_breast_height` is null throughout.
`hauteurarbre` is a height *class* ("de 10 m à 20 m") and is not published.

**Rows kept.**  Layer 1 has no status column (`anneeabattage`, `causeabattage`
and `essouchement` are empty on every row).  91 rows whose `remarques` is
exactly `ABSENT` (a stand of 71 pines, mostly) are trees no longer there and
are dropped with a count; a tree remarked `MORT` (dead) stays, as the shared
rule keeps dead trees.

**Planting date.**  Layer 1's `anneedeplantation`, a year, on 93% of rows,
1900-2024, published as 1 January.  1975 holds 10.6% (3.4k rows, 89% adult,
mostly parks); it reads as the estate-building cohort of the 1970s (1970, 1980
and 1965 are spikes too) rather than a default stamp, and is kept.  A
non-blank year that does not parse raises above 1% of rows.  Layer 2 has no
date.
"""

import csv
import io
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    emit,
    enforce_tree_schema,
    get_json_with_retry,
    get_with_retry,
    validate_coordinates,
)

PORTAL = "https://data.metropolegrenoble.fr"
CITY_DATASET = "arbres-grenoble"
METRO_DATASET = "patrimoine_arbore_du_territoire_metropolitain"
SOURCE_URL = f"{PORTAL}/visualisation/information/?id={CITY_DATASET}"

CITY_FIELDS = ("nom", "genre_bota", "espece", "variete", "anneedeplantation", "remarques", "geo_point_2d")
METRO_FIELDS = ("ogc_fid", "commune", "genre", "espece", "geo_point_2d")

UNKNOWN = frozenset({"", "inconnu", "inconnue", "indetermine", "indéterminé", "n.d.", "nd", "."})
# French common names written into the genus column; not mapped.
COMMON_NAME_GENERA = frozenset({"erable", "érable", "cypres", "cyprès", "troene", "troène", "tulipier"})
EPITHET_PLACEHOLDERS = frozenset({"", ".", "n.d.", "nd", "sp", "sp.", "spp", "spp."})
# Layer 1: a `variete` on a row with no `espece` that is the epithet, not a cultivar.
VARIETE_AS_EPITHET = frozenset({"pubescens"})
# Botanical-variety words that are not a cultivar (Gleditsia triacanthos var. inermis).
NOT_A_CULTIVAR = frozenset({"inermis"})
HYBRID_SIGNS = frozenset({"x", "×"})
# Layer 2 epithets written as two words.
EPITHET_FIXES = {"crus galli": "crus-galli"}
# A cultivar written as the epithet: Ulmus 'Lutèce' (the INRA elm, `lutece`,
# and `lutea`, which is no elm), Magnolia 'Galaxy' (`galaxy`, `X galaxy`).
CULTIVAR_AS_EPITHET = {
    ("ulmus", "lutece"): "Lutèce",
    ("ulmus", "lutea"): "Lutèce",
    ("magnolia", "galaxy"): "Galaxy",
}


def cultivar_as_epithet(species: str | None, cultivar: str | None) -> tuple[str | None, str | None]:
    words = (species or "").split()
    if len(words) >= 2 and (words[0].lower(), words[-1].lower()) in CULTIVAR_AS_EPITHET:
        return words[0], CULTIVAR_AS_EPITHET[(words[0].lower(), words[-1].lower())]
    return species, cultivar


MAX_UNREADABLE_YEARS = 0.01


def clean(value) -> str:
    return " ".join(str(value or "").split())


def resolve_csv(dataset: str) -> str:
    """The current URL of a dataset's CSV resource on the portal."""
    meta = get_json_with_retry(f"{PORTAL}/d4c/api/datasets/1.0/{dataset}/", timeout=60)
    versions = [
        version
        for resource in meta.get("resources_versions") or []
        if str(resource.get("name", "")).lower().endswith(".csv")
        for version in resource.get("versions") or []
        if version.get("filepath")
    ]
    if not versions:
        raise RuntimeError(f"{dataset}: no CSV resource in the portal's dataset record")
    latest = max(versions, key=lambda v: v.get("creation_date") or "")
    path = latest["filepath"]
    # The record names the file on whichever host uploaded it (sometimes none,
    # `https:///sites/...`); the file is served from the portal's own host.
    if "/sites/" not in path:
        raise RuntimeError(f"{dataset}: unexpected resource path {path!r}")
    return PORTAL + path[path.index("/sites/"):]


def read_csv(dataset: str, fields: tuple[str, ...]) -> list[dict]:
    url = resolve_csv(dataset)
    text = get_with_retry(url, timeout=300).content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text, newline=""))
    missing = set(fields) - set(reader.fieldnames or ())
    if missing:
        raise RuntimeError(f"Grenoble ingest: {url} is missing field(s) {sorted(missing)}")
    return list(reader)


def parse_point(value) -> tuple[float | None, float | None]:
    """(lat, lon) from the portal's `"45.1671,5.7401"`."""
    lat_text, _, lon_text = clean(value).partition(",")
    try:
        return float(lat_text), float(lon_text)
    except ValueError:
        return None, None


def title_if_lower(text: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in text.split()) if text == text.lower() else text


def genus_or_none(value) -> str | None:
    genus = clean(value)
    if genus.lower() in UNKNOWN or genus.lower() in COMMON_NAME_GENERA:
        return None
    return genus


def parse_city_species(genus, epithet, variete) -> tuple[str | None, str | None]:
    """Layer 1: (species, cultivar) from genus, epithet and `variete`."""
    name = genus_or_none(genus)
    if name is None:
        return None, None
    epithet = clean(epithet)
    cultivar = clean(variete)
    if epithet.lower() in EPITHET_PLACEHOLDERS:
        epithet = ""
    if not epithet and cultivar.lower() in VARIETE_AS_EPITHET:
        epithet, cultivar = cultivar.lower(), ""
    words = cultivar.split()
    while words and words[0].lower() in NOT_A_CULTIVAR:
        words = words[1:]
    if words and len(words) < len(cultivar.split()):
        # What follows `Inermis` was written in lower case (`Inermis skyline`).
        words = [w[:1].upper() + w[1:] for w in words]
    cultivar = " ".join(words)
    species = f"{name} {epithet.lower()}" if epithet else name
    return cultivar_as_epithet(species, cultivar or None)


def parse_metro_species(genus, epithet) -> tuple[str | None, str | None]:
    """Layer 2: (species, cultivar) from `genre` and `espece`, cultivar unquoted."""
    words = clean(genus).split()
    if not words:
        return None, None
    name = genus_or_none(words[0])
    if name is None:
        return None, None
    rest = clean(" ".join(words[1:] + [clean(epithet)])).replace('"', "").replace("'", "")
    rest = rest.split(" ou ")[0]  # `dulcis ou amygdalus`: either; take the first
    for wrong, right in EPITHET_FIXES.items():
        rest = rest.replace(wrong, right)
    tokens = [t for t in rest.split() if t.lower() not in EPITHET_PLACEHOLDERS]
    epithet_words: list[str] = []
    if tokens and tokens[0].lower() in HYBRID_SIGNS and len(tokens) > 1:
        epithet_words, tokens = ["x", tokens[1].lower()], tokens[2:]
    elif tokens and (tokens[0][:1].islower() or len(tokens) == 1):
        # A lower-case word is the epithet; so is a lone capitalised one
        # (`Cordata`).  A capitalised run is a cultivar with no species
        # (`Pink Giant`).
        epithet_words, tokens = [tokens[0].lower()], tokens[1:]
    while tokens and tokens[0].lower() in NOT_A_CULTIVAR:
        tokens = tokens[1:]
    species = " ".join([name] + epithet_words)
    cultivar = title_if_lower(" ".join(tokens)) if tokens else None
    return cultivar_as_epithet(species, cultivar)


def parse_year(value) -> tuple[date | None, bool]:
    """(1 January of the year, whether a non-blank value failed to parse)."""
    text = clean(value)
    if not text:
        return None, False
    try:
        year = int(float(text))
    except ValueError:
        return None, True
    if year < 1700 or year > date.today().year:
        return None, True
    return date(year, 1, 1), False


def transform(city_rows: list[dict], metro_rows: list[dict]) -> pa.Table:
    columns: dict[str, list] = {
        "tree_id": [], "species": [], "cultivar": [], "plant_date": [],
        "latitude": [], "longitude": [],
    }
    dropped: Counter[str] = Counter()
    common_name_genera = 0
    unreadable_years = 0

    def add(tree_id, species, cultivar, planted, point) -> None:
        lat, lon = parse_point(point)
        columns["tree_id"].append(tree_id)
        columns["species"].append(species)
        columns["cultivar"].append(cultivar)
        columns["plant_date"].append(planted)
        columns["latitude"].append(lat)
        columns["longitude"].append(lon)

    for rec in city_rows:
        key = clean(rec["nom"])
        if not key:
            dropped["city layer, no id"] += 1
            continue
        if clean(rec["remarques"]).upper() == "ABSENT":
            dropped["city layer, remarked ABSENT"] += 1
            continue
        if clean(rec["genre_bota"]).lower() in COMMON_NAME_GENERA:
            common_name_genera += 1
        species, cultivar = parse_city_species(rec["genre_bota"], rec["espece"], rec["variete"])
        planted, unreadable = parse_year(rec["anneedeplantation"])
        unreadable_years += unreadable
        add(f"grenoble-{key}", species, cultivar, planted, rec["geo_point_2d"])

    if city_rows and unreadable_years / len(city_rows) > MAX_UNREADABLE_YEARS:
        raise RuntimeError(
            f"Grenoble ingest: {unreadable_years} of {len(city_rows)} planting years "
            "do not parse; the portal changed `anneedeplantation`"
        )

    for rec in metro_rows:
        key = clean(rec["ogc_fid"])
        commune = clean(rec["commune"])
        if not key:
            dropped["Métropole layer, no id"] += 1
            continue
        if not commune:
            dropped["Métropole layer, no commune"] += 1
            continue
        if commune.lower() == "grenoble":
            dropped["Métropole layer, in Grenoble (in the city layer)"] += 1
            continue
        if clean(rec["genre"]).lower() in COMMON_NAME_GENERA:
            common_name_genera += 1
        species, cultivar = parse_metro_species(rec["genre"], rec["espece"])
        add(f"grenoble-metro-{key}", species, cultivar, None, rec["geo_point_2d"])

    print(
        f"Grenoble OpenData: {len(city_rows)} city-layer rows, {len(metro_rows)} "
        f"Métropole-layer rows; kept {len(columns['tree_id'])}; dropped "
        + (", ".join(f"{n} {why}" for why, n in sorted(dropped.items())) or "none")
        + f"; {common_name_genera} French common names in the genus column published "
        f"as unknown; {unreadable_years} unreadable planting years nulled",
        file=sys.stderr,
    )
    n = len(columns["tree_id"])
    return pa.table(
        {
            "tree_id": pa.array(columns["tree_id"], type=pa.string()),
            "city": pa.array(["FRGNB"] * n, type=pa.string()),
            "species": pa.array(columns["species"], type=pa.string()),
            "cultivar": pa.array(columns["cultivar"], type=pa.string()),
            "plant_date": pa.array(columns["plant_date"], type=pa.date32()),
            "latitude": pa.array(columns["latitude"], type=pa.float64()),
            "longitude": pa.array(columns["longitude"], type=pa.float64()),
            "diameter_at_breast_height": pa.array([None] * n, type=pa.float64()),
        }
    )


def main() -> None:
    table = transform(
        read_csv(CITY_DATASET, CITY_FIELDS),
        read_csv(METRO_DATASET, METRO_FIELDS),
    )
    table = validate_coordinates(table, city="Grenoble", city_code="FRGNB")
    table = enforce_tree_schema(table, city="Grenoble", data_source="GRENOBLE_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()

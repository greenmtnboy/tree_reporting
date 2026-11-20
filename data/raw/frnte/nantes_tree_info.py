#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Nantes's tree inventory ("Patrimoine arboré de Nantes Métropole et de la
Ville de Nantes").

Source: OpenDataSoft dataset `244400404_patrimoine-arbore-nantes-metropole` on
data.nantesmetropole.fr
(https://data.nantesmetropole.fr/explore/dataset/244400404_patrimoine-arbore-nantes-metropole/),
published by Nantes Métropole under the Licence Ouverte (Etalab,
https://www.etalab.gouv.fr/licence-ouverte-open-licence), as the dataset's
`license` field states.  **124,583 rows** when this was written; the
publisher's update frequency is annual.  The parquet export returns the whole
layer as one ~4 MB file.

**Coverage is the metropole.**  The publisher describes it as the ~83,500
street trees on the public roads of all 24 communes of Nantes Métropole plus
the ~40,350 trees on the Ville de Nantes's own land (parks, gardens, schools).
57% of the rows are in Nantes itself; Saint-Herblain (9.8k), Carquefou (8.1k),
Orvault, Rezé and the other 19 communes have their street trees.

**The id is a site number scoped by its owner and commune.**  `id_emplacement`
alone is NOT unique (71,774 distinct over 124,577 non-null): Nantes Métropole
numbers its sites per commune, and the Ville de Nantes numbers its own in a
separate series that overlaps the metropole's.  Verified on the whole layer:
the Ville de Nantes's ids (`proprietaire = 'Nantes'`) are unique among its
40,142 rows, and (`code_insee`, `id_emplacement`) is unique over every other
owner (Nantes Métropole, and a handful owned by Vertou, Le Pellerin and
Saint-Léger-les-Vignes).  Published as `nantes-ville-161300` and
`nantes-44162-5021`.  Rows with no id, or no commune outside the Ville's
series, are dropped with a count (6); a duplicate id raises, because it means
the numbering rule changed.

**Rows that are not trees are dropped**: `lib_genre = 'EMPLACEMENT CONSERVE'`
("site kept", espece `VIDE`, diameter 0) is an empty planting site the
service keeps -- 706 of them.  `statut = 'A qualifier'` with no genus (19,982
sites, none ever inventoried: no date, size or species) are kept as trees of
unknown species: the publisher counts them among its trees and says their
descriptions are "being acquired".  `statut` is otherwise `Planté` or blank,
plus 97 rows carrying the stray value `2668`; none of them is a status that
removes a tree.

**Species is `lib_genre` + `lib_espece`** (no common-name column):

  * the hybrid mark is written after the epithet, `acerifolia (x)`,
    `hispanica(x)`, `Sentinel (×)`, or before it, `X Canadensis`, and once on
    the genus (`Cupressocyparis (x)`);
  * a capitalised "epithet" is mostly a cultivar (`Magnolia Gresham (x)`,
    `Malus Red Sentinel (x)`, `Acer red shine`), so it goes to `cultivar`,
    except for a few real epithets typed with a capital (`Quercus Conferta`,
    `Crataegus Laevigata`), which are listed;
  * `sp`, `hybride`, `VIDE` (empty), `non déterminée` and `Non déterminé` are
    the genus only, or unknown;
  * `lib_sous_espece` is a subspecies (`Pinus nigra` laricio, austriaca),
    dropped, or `pendula`, the weeping form, kept as the cultivar; `type` and
    `vide` mean nothing.
  * `variete` is a numeric code (826, 832, ...) into a list the portal does
    not publish, and is not used.

**DBH is `diametre`**, in centimetres.  The distribution confirms a diameter
rather than a circumference: median 24 cm, and trees in the `21-30 m` height
class have a median of 62 cm (a circumference would put them at 20 cm).  0 is
"not measured" and the shared guard nulls it, with the handful of typos over
5 m.

**Crown width is `diametre_estime_couronne`**, "estimated crown diameter",
metres (median 5 m).  0 is unmeasured and the shared guard nulls it.
`lib_hauteur_totale` is a height class (`6-10 m`), not a measurement, and is
not mapped.

**Planting date is `annee_plantation`**, a year, published as 1 January of
it.  One stamp is nulled: 4,847 Nantes Métropole trees in the commune of
Nantes carry 2002, seven times the neighbouring years, and 20% of them have a
trunk of 40 cm or more (1% of the 2001 and 2003 plantings do) -- the year
the service's database begins (the earliest `date_inventaire` is November
2002), stamped on trees whose real year was not known.  2002 elsewhere looks
like plantings and is kept.  A year that does not parse raises past 1%.
"""

import re
import sys
import unicodedata
from collections import Counter
from datetime import date
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import (
    cm_to_inches,
    download_parquet,
    emit,
    enforce_tree_schema,
    parse_wkb_point,
    validate_coordinates,
)

DATASET = "244400404_patrimoine-arbore-nantes-metropole"
FIELDS = [
    "id_emplacement",
    "code_insee",
    "proprietaire",
    "lib_genre",
    "lib_espece",
    "lib_sous_espece",
    "annee_plantation",
    "diametre",
    "diametre_estime_couronne",
    "geo_point_2d",
]
SOURCE_URL = (
    f"https://data.nantesmetropole.fr/api/explore/v2.1/catalog/datasets/{DATASET}"
    f"/exports/parquet?select={','.join(FIELDS)}&timezone=UTC"
)

# The Ville de Nantes numbers its own trees in one series; every other owner's
# ids are per commune.
VILLE_OWNER = "nantes"
# An empty planting site the service keeps.
EMPTY_SITE_GENUS = "emplacement conserve"
UNKNOWN_GENUS = frozenset({"non determine"})
# Epithets that mean "no species": the genus alone.
GENUS_ONLY = frozenset({"sp", "sp.", "spp", "hybride", "vide", "non determinee"})
# Real epithets the register typed with a capital; any other capitalised
# epithet is a cultivar.
CAPITALISED_EPITHETS = frozenset(
    {
        "conferta", "laevigata", "communis", "engleriana", "dulcis", "sempervirens",
        "germanica", "calliprinos", "doltsopa", "quassoides",
        "fortunei", "canadensis", "media", "amoena", "yunnannensis",
    }
)
# Lowercase "epithets" that are cultivars (or a cultivar series).
EPITHET_IS_CULTIVAR = {
    ("ulmus", "resista"): "Resista",
    ("ulmus", "columella"): "Columella",
    ("ulmus", "lutece"): "Lutèce",
    ("malus", "everest"): "Evereste",
    ("malus", "van eseltine"): "Van Eseltine",
    ("acer", "red shine"): "Red Shine",
    ("magnolia", "black tulipe"): "Black Tulip",
    ("magnolia", "pickard's crystal"): "Pickard's Crystal",
    ("michelia", "picotee"): "Picotee",
}
# A hybrid mark the register puts on a plain species (`Olea europaea (x)`).
NOT_HYBRIDS = frozenset({("olea", "europaea")})
QUOTES = "'‘’\""
# `lib_sous_espece` values kept as the cultivar.
SUBSPECIES_AS_CULTIVAR = {"pendula": "Pendula"}
# A coded column that stops parsing is a format change, not noise.
MAX_UNREADABLE_SHARE = 0.01
# The stamped planting year (see the docstring): owner, commune, year.
STAMPED_PLANT_YEAR = ("nantes métropole", 44109, 2002)

_HYBRID_MARK = re.compile(r"\(\s*[x×]\s*\)|(?:^|\s)[x×](?=\s|$)", re.IGNORECASE)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def fold(text: str) -> str:
    """Lowercase without accents (`Média` = `media`)."""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text.lower()) if not unicodedata.combining(ch)
    )


def title_case(text: str) -> str:
    return " ".join(w[:1].upper() + w[1:] for w in text.split())


def parse_species(genus_value, espece_value, sous_espece_value) -> tuple[str | None, str | None]:
    """(species, cultivar) from the register's genus, species and subspecies."""
    genus_text = clean(genus_value)
    if genus_text is None:
        return None, None
    genus_text = clean(_HYBRID_MARK.sub(" ", genus_text))
    if genus_text is None or fold(genus_text) in UNKNOWN_GENUS:
        return None, None
    genus = genus_text.split()[0].capitalize()
    text = clean(espece_value)
    if text is None:
        return genus, None
    hybrid = bool(_HYBRID_MARK.search(text))
    text = clean(_HYBRID_MARK.sub(" ", text))
    if text is None or fold(text) in GENUS_ONLY:
        return genus, None
    key = (genus.lower(), text.lower())
    if key in EPITHET_IS_CULTIVAR:
        return genus, EPITHET_IS_CULTIVAR[key]
    if text[:1] in QUOTES:
        # `'Nicolas Hemmebelle' (x)`: a quoted cultivar.
        return genus, clean(text.strip(QUOTES))
    words = text.split()
    if words[0][:1].isupper() and fold(words[0]) not in CAPITALISED_EPITHETS:
        # `Gresham (x)`, `Red Sentinel (x)`, `Pickard s Ruby (x)`.  A cross of
        # two cultivars (`Pickard s x Firfly`, `kosar-devos x galaxie`)
        # publishes as the genus.
        if " x " in f" {clean(espece_value).lower()} " and not clean(espece_value).lower().endswith(" x"):
            return genus, None
        return genus, title_case(text.replace(" s ", "'s "))
    if " x " in f" {clean(espece_value).lower()} " and len(words) > 1:
        return genus, None
    epithet = fold(words[0])
    rest = words[1:]
    hybrid = hybrid and (genus.lower(), epithet) not in NOT_HYBRIDS
    name = f"{genus} x {epithet}" if hybrid else f"{genus} {epithet}"
    cultivar = None
    if rest and rest[0].lower() not in {"mughus"}:
        # `bimundorum (X) CRIMSON SPIRE`, `Média Pfitzeriana (x)`.
        cultivar = title_case(" ".join(rest).lower())
    sous = (clean(sous_espece_value) or "").lower()
    if cultivar is None and sous in SUBSPECIES_AS_CULTIVAR:
        cultivar = SUBSPECIES_AS_CULTIVAR[sous]
    return name, cultivar


class YearParser:
    """A four-digit planting year, counting values that are present but unreadable."""

    def __init__(self) -> None:
        self.present = 0
        self.unreadable = 0
        self.examples: set[str] = set()

    def __call__(self, value) -> int | None:
        text = clean(value)
        if text is None:
            return None
        self.present += 1
        if re.fullmatch(r"\d{4}", text):
            return int(text)
        self.unreadable += 1
        if len(self.examples) < 5:
            self.examples.add(text)
        return None

    def check(self) -> None:
        if self.present and self.unreadable / self.present > MAX_UNREADABLE_SHARE:
            raise RuntimeError(
                f"Nantes ingest: {self.unreadable} of {self.present} `annee_plantation` "
                f"values are not years (e.g. {sorted(self.examples)}); the format changed"
            )
        if self.unreadable:
            print(
                f"Nantes OpenData: {self.unreadable} unreadable `annee_plantation` values "
                f"nulled (e.g. {sorted(self.examples)})",
                file=sys.stderr,
            )


def make_id(key, insee, owner) -> str | None:
    if key is None:
        return None
    if (clean(owner) or "").lower() == VILLE_OWNER:
        return f"nantes-ville-{int(key)}"
    if insee is None:
        return None
    return f"nantes-{int(insee)}-{int(key)}"


def transform(table: pa.Table) -> pa.Table:
    missing = set(FIELDS) - set(table.schema.names)
    if missing:
        raise RuntimeError(f"Nantes ingest: export is missing field(s) {sorted(missing)}")

    years = YearParser()
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []
    dropped: Counter[str] = Counter()
    stamped = 0

    columns = [table[f].to_pylist() for f in FIELDS]
    for key, insee, owner, genus, espece, sous, planted, diameter, spread, point in zip(*columns):
        identifier = make_id(key, insee, owner)
        if identifier is None:
            dropped["no id"] += 1
            continue
        if (clean(genus) or "").lower() == EMPTY_SITE_GENUS:
            dropped["empty site (EMPLACEMENT CONSERVE)"] += 1
            continue
        lon, lat = parse_wkb_point(point)
        name, selection = parse_species(genus, espece, sous)
        year = years(planted)
        if year is not None and ((clean(owner) or "").lower(), insee, year) == STAMPED_PLANT_YEAR:
            stamped += 1
            year = None
        tree_id.append(identifier)
        species.append(name)
        cultivar.append(selection)
        plant_date.append(date(year, 1, 1) if year is not None else None)
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(cm_to_inches(diameter))
        crown.append(float(spread) if spread is not None else None)

    years.check()
    duplicates = [k for k, n in Counter(tree_id).items() if n > 1]
    if duplicates:
        raise RuntimeError(
            f"Nantes ingest: {len(duplicates)} duplicate tree id(s) (e.g. {duplicates[:5]}); "
            "the owner/commune numbering rule no longer holds"
        )
    print(
        f"Nantes OpenData: {table.num_rows} rows; dropped "
        + ", ".join(f"{n} {why}" for why, n in sorted(dropped.items()))
        + f"; nulled {stamped} stamped 2002 planting years",
        file=sys.stderr,
    )
    n = len(tree_id)
    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["FRNTE"] * n, type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


def main() -> None:
    table = transform(pq.read_table(download_parquet(SOURCE_URL)))
    table = validate_coordinates(table, city="Nantes", city_code="FRNTE")
    table = enforce_tree_schema(table, city="Nantes", data_source="NANTES_OPENDATA")
    emit(table)


if __name__ == "__main__":
    main()

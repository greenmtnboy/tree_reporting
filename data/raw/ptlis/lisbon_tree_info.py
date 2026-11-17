#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Lisbon's municipal tree inventory (Arvoredo), from the city's ArcGIS Online.

Source: "Arvoredo", layer 0 of the `Ambiente_DMEVAE` FeatureServer (ArcGIS
item d2eb1eb1cfae400496bec1aeec4e25b2, owner `geodados_CML`, Câmara
Municipal de Lisboa / Direção Municipal da Estrutura Verde, Ambiente e
Energia).  Catalogued on Lisboa Aberta (dados.cm-lisboa.pt, dataset
`arvoredo`) and on dados.gov.pt (`arvoredo`) under Creative Commons CCZero;
the item's `licenseInfo` says the same ("Aplica-se a licença Creative
Commons CCZero ... devendo ser citados os créditos do autor").  82,059 rows
on 2026-11-16: street trees (`LOCAL` = Via pública, 48k), green spaces
(22k), cemeteries, schools and a few hundred in woodland, nurseries and
private grounds.  Geometry is read reprojected to WGS84 (`outSR=4326`);
every row has a point, all inside -9.227..-9.091, 38.692..38.796.

**`GlobalID` is the key** (`lis-{guid}`, lower-case): 82,059 distinct, none
null.  The layer's other ids are worse: `COD_SIG` (the city's SIG code) is
blank on 23,568 rows and repeated on 1,018 values (`110`, `Qs`, `Pp` --
park and species shorthands, not asset numbers); `COD_SIG_NEW` runs 1..82,059
without a gap, a renumbering stamped on each export; the OID `GESP_C_PK`
(62,234..167,053) is unique but is only the hosted table's row number.

**`OCUPACAO` says what occupies the site, and four of its values are not
trees.**  `Árvore` (71,677) is a tree; `Árvore seca` (628, one spelled
`Árvore Seca`) is a standing dead tree and publishes as the `Dead` sentinel,
whatever species the record still names, as Seattle's snags do.  `Vazia`
(empty pit, 2,867), `Eliminada` (removed, 1,587), `Cepo` (stump, 1,207) and
`Pavimentada` (pit paved over, 559) are dropped with a count.  A blank
`OCUPACAO` (3,533) is a point the surveyors placed and never filled in --
3,485 of them `Não identificada` with no girth, mostly in green spaces and
along the Ponte 25 de Abril approaches -- and is kept as an unnamed tree; a
single `Em atualização` ("being updated") is kept too.  Any other value
raises, because the side of the line it falls on cannot be guessed.

**Species is `ESPECIE_VA`, a scientific name** (485 values; `NOME_VULGA`
beside it is the Portuguese common name and is not mapped).  `Não
identificada` (7,208), `Em atualização` (103) and `Bambu` (1) publish as
`Unknown`.  The
register writes cultivars as varieties with a capitalised name (`Pyrus
calleryana var. Chanticleer`, `Ulmus x resista var. Sapporo Gold`,
`Robinia pseudoacacia var. Casque Rouge`), quoted (`Gleditsia triacanthos
var. inermis 'Sunburst'`, `Acer platanoides "Columnare"`) or bare after the
binomial (`Cupressus macrocarpa Aurea`); all three move to `cultivar`
(an author citation in that last position, `Cupressus arizonica Greene`,
is dropped instead).  A
lower-case `var.`/`subsp.`/`f.` is a botanical rank and is left to the shared
hygiene, which truncates it to the species.  Non-breaking spaces
(`Quercus\\xa0robur\\xa0f.\\xa0fastigiata`) are normalised first.

**DBH is from `PAP`, perímetro à altura do peito: trunk circumference at
breast height in centimetres**, published as text.  Medians by species bear
the unit out (Platanus x hybrida 87 cm, Pinus pinea 115 cm, i.e. 11 and 14 in
of diameter).  `0` (13,686) and the negative sentinels `-1`/`-10` are
unmeasured and null.  A decimal comma is read (`19,5`).  A multi-stemmed tree
is written as its stems' girths joined by `+` (`25+24`, `86+93+78+93`) or
listed with a comma and a space (`35, 33, 25`), 58 standing trees in all,
and publishes the single-stem equivalent, the square root of the summed
squares (ISA's rule for a multi-stem DBH).  Anything else (`72 (42 e 40)`,
`127-86`, `4,5m`) is unreadable and null; more than 1% of the non-blank
values unreadable refuses the build.  The shared 200-inch guard nulls the few
typed-in-metres-of-digits outliers (`411000090019`).

The layer has no planting date, crown spread or recorded height.
"""

import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    DEAD_SPECIES,
    circumference_cm_to_dbh_inches,
    emit,
    enforce_tree_schema,
    extract_cultivar,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://services.arcgis.com/1dSrzEWVQn5kHHyK/arcgis/rest/services/Ambiente_DMEVAE/FeatureServer/0",
    timeout=180,
)
OUT_FIELDS = "GESP_C_PK,GlobalID,ESPECIE_VA,PAP,OCUPACAO"
ORDER_BY = "GESP_C_PK"

# `OCUPACAO`, folded (lower case, no accents, single spaces).
LIVE_TREE = frozenset({"arvore", "", "em atualizacao"})
DEAD_TREE = frozenset({"arvore seca"})
NOT_A_TREE = {
    "vazia": "empty site (OCUPACAO = Vazia)",
    "eliminada": "removed (OCUPACAO = Eliminada)",
    "cepo": "stump (OCUPACAO = Cepo)",
    "pavimentada": "pit paved over (OCUPACAO = Pavimentada)",
}

# `ESPECIE_VA` values that name no taxon, folded.  `Bambu` (Portuguese for
# bamboo, 1 row) is a common name for a whole subfamily and would otherwise
# survive as an invented genus.
UNNAMED = frozenset({"nao identificada", "nao identificado", "em atualizacao", "bambu"})

MAX_UNREADABLE_GIRTH_SHARE = 0.01

_NUMBER = r"\d+(?:[.,]\d+)?"
_SINGLE = re.compile(rf"^{_NUMBER}$")
_STEMS = re.compile(rf"^{_NUMBER}(?:\s*\+\s*{_NUMBER})+$")
# Stems listed with a comma *and a space* (`35, 33, 25`); a bare comma is a
# decimal comma (`19,5`).
_LISTED_STEMS = re.compile(r"^\d+(?:,\s+\d+)+$")
_NEGATIVE = re.compile(rf"^-\s*{_NUMBER}$")
# `var.` / `cv.` followed by a capitalised name is a cultivar, not a variety.
_CAPITAL_VARIETY = re.compile(r"^(?P<taxon>.+?)\s+(?:var|cv)\.\s+(?P<cultivar>[A-Z].*)$")
# A binomial (optionally with a hybrid mark) followed by a capitalised word.
_TRAILING_CULTIVAR = re.compile(
    r"^(?P<taxon>[A-Z][a-z]+(?:\s+[x×])?\s+[a-z][a-z-]+)\s+(?P<cultivar>[A-Z][A-Za-z' ]+)$"
)
# Authorities written after a binomial, which the trailing-cultivar rule would
# otherwise read as a selection (`Cupressus arizonica Greene`, 2 rows).
AUTHOR_CITATIONS = frozenset({"Greene"})

_QUOTES = "'\"‘’“”`"


def fold(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return " ".join(text.lower().split())


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).replace("\xa0", " ").split())
    return text or None


def split_species(raw) -> tuple[str | None, str | None]:
    """`ESPECIE_VA` -> (species text for the shared hygiene, cultivar).

    `Pyrus calleryana var. Chanticleer`        -> (`Pyrus calleryana`, `Chanticleer`)
    `Gleditsia triacanthos var. inermis 'Sunburst'`
                                               -> (`Gleditsia triacanthos var. inermis`, `Sunburst`)
    `Cupressus macrocarpa Aurea`               -> (`Cupressus macrocarpa`, `Aurea`)
    `Crataegus laevigata var. Paul`s Scarlet`  -> (`Crataegus laevigata`, `Paul's Scarlet`)
    `Populus nigra var. italica`               -> unchanged, no cultivar
    `Não identificada`                         -> (None, None)
    """
    text = clean(raw)
    if text is None or fold(text) in UNNAMED:
        return None, None
    quoted = extract_cultivar(text)
    if quoted:
        cut = min(text.find(q) for q in _QUOTES if q in text)
        return text[:cut].strip() or None, quoted
    match = _CAPITAL_VARIETY.match(text) or _TRAILING_CULTIVAR.match(text)
    if match:
        cultivar = match.group("cultivar").replace("`", "'").strip()
        if cultivar in AUTHOR_CITATIONS:
            return match.group("taxon").strip(), None
        return match.group("taxon").strip(), cultivar
    return text, None


class Lisbon:
    def __init__(self) -> None:
        self.dropped: Counter[str] = Counter()
        self.unreadable_girth: Counter[str] = Counter()
        self.girths = 0
        self.multi_stem = 0
        self.dead = 0
        self.rows = 0

    def keep(self, feature: dict) -> bool:
        rec = feature.get("attributes") or {}
        occupation = fold(rec.get("OCUPACAO"))
        if occupation in NOT_A_TREE:
            self.dropped[NOT_A_TREE[occupation]] += 1
            return False
        if occupation not in LIVE_TREE and occupation not in DEAD_TREE:
            raise RuntimeError(
                f"Lisbon OCUPACAO {rec.get('OCUPACAO')!r} is new; decide whether it is a tree"
            )
        if not str(rec.get("GlobalID") or "").strip("{} "):
            self.dropped["no GlobalID"] += 1
            return False
        return True

    def girth_cm(self, raw) -> float | None:
        """`PAP` as one circumference in centimetres, or None."""
        text = clean(raw)
        if text is None:
            return None
        self.girths += 1
        if _SINGLE.match(text):
            return float(text.replace(",", ".")) or None
        if _NEGATIVE.match(text):
            return None
        if _LISTED_STEMS.match(text):
            text = re.sub(r",\s+", "+", text)
        if _STEMS.match(text):
            stems = [float(v.replace(",", ".")) for v in re.findall(_NUMBER, text)]
            self.multi_stem += 1
            return math.sqrt(sum(s * s for s in stems)) or None
        self.unreadable_girth[text] += 1
        return None

    def __call__(self, features: list[dict]) -> pa.Table:
        tree_id: list[str] = []
        species: list[str | None] = []
        cultivar: list[str | None] = []
        latitude: list[float | None] = []
        longitude: list[float | None] = []
        dbh: list[float | None] = []

        for feature in features:
            rec = feature.get("attributes") or {}
            tree_id.append("lis-" + str(rec["GlobalID"]).strip("{} ").lower())
            if fold(rec.get("OCUPACAO")) in DEAD_TREE:
                self.dead += 1
                name, cv = DEAD_SPECIES, None
            else:
                name, cv = split_species(rec.get("ESPECIE_VA"))
            species.append(name)
            cultivar.append(cv)
            lat, lon = esri_point(feature.get("geometry"))
            latitude.append(lat)
            longitude.append(lon)
            dbh.append(circumference_cm_to_dbh_inches(self.girth_cm(rec.get("PAP"))))
        self.rows += len(tree_id)

        return pa.table(
            {
                "tree_id": pa.array(tree_id, type=pa.string()),
                "city": pa.array(["PTLIS"] * len(tree_id), type=pa.string()),
                "species": pa.array(species, type=pa.string()),
                "cultivar": pa.array(cultivar, type=pa.string()),
                "plant_date": pa.array([None] * len(tree_id), type=pa.date32()),
                "latitude": pa.array(latitude, type=pa.float64()),
                "longitude": pa.array(longitude, type=pa.float64()),
                "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            }
        )

    def report(self) -> None:
        for reason, n in sorted(self.dropped.items()):
            print(f"Lisbon: dropped {n:,} rows: {reason}", file=sys.stderr)
        print(
            f"Lisbon: {self.dead:,} standing dead trees published as {DEAD_SPECIES!r}; "
            f"{self.multi_stem:,} multi-stem girths combined",
            file=sys.stderr,
        )
        unreadable = sum(self.unreadable_girth.values())
        if unreadable:
            print(
                f"Lisbon: {unreadable:,} PAP values unreadable (published null): "
                f"{dict(self.unreadable_girth.most_common(10))}",
                file=sys.stderr,
            )
        if self.girths and unreadable / self.girths > MAX_UNREADABLE_GIRTH_SHARE:
            raise RuntimeError(
                f"Lisbon: {unreadable:,} of {self.girths:,} PAP values are unreadable; "
                "the column's format has changed"
            )


def iter_row_chunks():
    """One ArcGIS page (2,000 features) at a time, geometry in WGS84."""
    return iter_features(
        LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326, order_by=ORDER_BY
    )


if __name__ == "__main__":
    lisbon = Lisbon()
    table = stream_to_table(iter_row_chunks(), lisbon, keep=lisbon.keep, label="Lisbon OpenData")
    lisbon.report()
    table = validate_coordinates(table, city="Lisbon", city_code="PTLIS")
    table = enforce_tree_schema(table, city="Lisbon", data_source="LISBON_OPENDATA")
    emit(table)

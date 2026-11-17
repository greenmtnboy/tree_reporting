#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Bristol's council-managed trees, from Bristol City Council's map server.

Source: "Trees" (ArcGIS Online item 7a99218a4bf347ff948f0e5882406a8c, owner
`Layer_Library` in the Bristol City Council organisation, listed on
opendata.bristol.gov.uk), the layer
`ext/ll_environment_and_planning/MapServer/32` on the council's own ArcGIS
Server.  "Point Feature depicting individual Tree Features under the
responsibility of Bristol City Council and managed by Environment & Leisure
Services ... stored in the Confirm Arboriculture Asset Management System."
56,550 rows on 2026-11-16.  The item's licence: "Subject to Open Government
Licence V 3.0", access information "Bristol City Council".  Trees mapped as
woodland are a separate polygon layer and are not read.

**The layer is reloaded whole.**  Every row's `created_date` and
`last_edited_date` is the moment of the last load (2026-11-16), and
`OBJECTID` starts at 40,412,943: it is a load counter, not an id.

**`ASSET_ID` is the key** (`bristol-{ASSET_ID}`): the Confirm asset number,
non-null and distinct on all 56,550 rows (also case-insensitively; checked
over the whole layer on 2026-11-16), and identical to `CENTRAL_ASSET_ID` on
every row.  The layer has no GlobalID.  A row without one is dropped with a
logged count.

**Location is the geometry**, stored in British National Grid and read
reprojected to WGS84 by the server (within 0.4 m of pyproj's EPSG:27700
transform over a 114-row sample).  66 rows are council trees at two sites
outside the city -- Dean Field Study Centre in the Forest of Dean and Notton
House School in Wiltshire -- and `validate_coordinates` drops them.

**Species is `LATIN_NAME`** (`TREE_SPECIES` is the same value on every row).
Names are cut at 30 characters by the asset system, which mostly truncates a
cultivar (`Chamaecyparis lawsoniana Ellwo`), and some abbreviate the species
(`Carpinus bet 'Rockhampton Red'`, `Sorbus a. 'Cardinal Royal'`).  `TAXA` is
the closed table of those, read by hand against the layer's own
`FULL_COMMON_NAME` (`Lawson's cypress 'Ellwoodii'`); everything else goes
through `split_species`: a quoted cultivar (straight or curly quotes,
sometimes mismatched or unterminated) or an unquoted capitalised word after the
binomial (`Fraxinus oxycarpa Raywood`), as in Nottingham's ingest.
`Genus - Species Unknown` (and its spellings) is the genus.  `cv` after a
binomial says only "a cultivar" and is dropped.  `Unknown species - Conifer`
publishes as `Unknown conifer`; `Unknown species - Broadleaf`, `Mixed
Species`, `Mixed Species Group` and `No Code Allocated` as Unknown; `Shrub
Group` as Shrub; `ZZ Dead Tree` (and `DEAD` = `Y`, on no row today) as Dead.
`COMMON_NAME` and `FULL_COMMON_NAME` are not mapped.

**Not every row is a tree.**  `ZZ Tree stump` (480), `ZZ Empty tree pit`
(50), `ZZ Tree Planting Location` (3) and `ZZ Tree Entered in Error` (1) are
dropped, as are rows whose `CANCELLATION_REASON` is set (3: a planting the
council cancelled).  `PRIM_MEAS` is a count of trees at the point (1 on 98%
of rows; 0 on 1,078, more than one on 331, mostly `Mixed Species Group`):
every row is published as one point, since the layer gives no positions for
the others.

**DBH is `DBH`, a coded string of centimetres of diameter** (`32
Centimetres`; also misspelt `Centrimetres`).  Diameter, not girth: the median
rises with recorded crown width from 6 cm at a 1 m crown to 41 cm at 10 m and
82 cm at 20 m, which is a diameter's allometry.  Divided by 2.54.  `No Code
Allocated` (5,050) is null; anything unreadable is counted and the ingest
refuses above 1%.  Newly planted trees are mostly recorded at 6 cm.

**Crown width is `CROWN_WIDTH`** (`8 Metres`), the spread in metres, same
rules.  `CROWN_HEIGHT` has no canonical column.

**Plant date is `PLANTING_SEASON`** (`Planting Season 22/23`, on 11,925
rows): the winter season November to March, published as 1 January of its
second year.  A season still in progress lands in the future and is nulled by
the schema.  `Not Applicable` is null; anything else unreadable counts
against the same 1% guard.
"""

import re
import sys
from datetime import date
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.platforms.arcgis import FeatureLayer, esri_point, iter_features
from shared.ingest import (
    DEAD_SPECIES,
    SHRUB_SPECIES,
    UNKNOWN_CONIFER_SPECIES,
    UNKNOWN_SPECIES,
    emit,
    enforce_tree_schema,
    stream_to_table,
    validate_coordinates,
)

LAYER = FeatureLayer(
    "https://maps2.bristol.gov.uk/server2/rest/services/ext/"
    "ll_environment_and_planning/MapServer/32",
    timeout=180,
)

OUT_FIELDS = (
    "OBJECTID,ASSET_ID,LATIN_NAME,DBH,CROWN_WIDTH,PLANTING_SEASON,DEAD,"
    "CANCELLATION_REASON"
)

# `LATIN_NAME` values that record a site or a record, not a tree.
NOT_A_TREE = frozenset(
    {"ZZ Tree stump", "ZZ Empty tree pit", "ZZ Tree Planting Location", "ZZ Tree Entered in Error"}
)

NO_CODE = "No Code Allocated"
NOT_APPLICABLE = "Not Applicable"

# `LATIN_NAME` (whitespace collapsed) -> (species, cultivar): truncated or
# abbreviated names, cultivars written in the epithet's place, and the
# placeholders.  Truncated cultivars are completed from `FULL_COMMON_NAME`.
TAXA: dict[str, tuple[str, str | None]] = {
    NO_CODE: (UNKNOWN_SPECIES, None),
    "Mixed Species": (UNKNOWN_SPECIES, None),
    "Mixed Species Group": (UNKNOWN_SPECIES, None),
    "Unknown species - Broadleaf": (UNKNOWN_SPECIES, None),
    "Unknown species - Conifer": (UNKNOWN_CONIFER_SPECIES, None),
    "Shrub Group": (SHRUB_SPECIES, None),
    "ZZ Dead Tree": (DEAD_SPECIES, None),
    # Truncated cultivars.
    "Acer platanoides Goldsworth Pu": ("Acer platanoides", "Goldsworth Purple"),
    "Acer pseudoplatanus Brillianti": ("Acer pseudoplatanus", "Brilliantissimum"),
    "Amelanchier lamarckii Robin H": ("Amelanchier lamarckii", "Robin Hill"),
    "Amelanchier x grandiflora 'RH'": ("Amelanchier x grandiflora", "Robin Hill"),
    "Betula albosinensis fascinatio": ("Betula albosinensis", "Fascination"),
    "Betula pendula 'Fastigiata Joe": ("Betula pendula", "Fastigiata Joes"),
    "Betula utilis Jaquemontii Snow": ("Betula utilis", "Snow Queen"),
    "Carpinus betulus Frans Fontane": ("Carpinus betulus", "Frans Fontaine"),
    "Chamaecyparis lawsoniana Allum": ("Chamaecyparis lawsoniana", "Allumii"),
    "Chamaecyparis lawsoniana Colum": ("Chamaecyparis lawsoniana", "Columnaris"),
    "Chamaecyparis lawsoniana Ellwo": ("Chamaecyparis lawsoniana", "Ellwoodii"),
    "Chamaecyparis lawsoniana Erect": ("Chamaecyparis lawsoniana", "Erecta"),
    "Chamaecyparis lawsoniana Fletc": ("Chamaecyparis lawsoniana", "Fletcheri"),
    "Chamaecyparis lawsoniana Frase": ("Chamaecyparis lawsoniana", "Fraseri"),
    "Chamaecyparis lawsoniana Winst": ("Chamaecyparis lawsoniana", "Winston Churchill"),
    "Chamaecyparis lawsoniana Young": ("Chamaecyparis lawsoniana", "Youngii"),
    "Chamaecyparis pisifera 'Filife": ("Chamaecyparis pisifera", "Filifera"),
    "Chitalpa taschkentensis Sum Ba": ("Chitalpa taschkentensis", "Summer Bells"),
    "Crataegus laevigata 'Paul's Sc": ("Crataegus laevigata", "Paul's Scarlet"),
    "Crataegus persimilis 'Splenden": ("Crataegus persimilis", "Splendens"),
    "Crataegus x persimilis Prunifo": ("Crataegus x persimilis", "Prunifolia"),
    "Fraxinus excelsior Diversifoli": ("Fraxinus excelsior", "Diversifolia"),
    "Fraxinus excelsior Westhof's G": ("Fraxinus excelsior", "Westhof's Glory"),
    "Ilex x altaclerensis Camellifo": ("Ilex x altaclerensis", "Camelliifolia"),
    "Ilex x altaclerensis Golden Ki": ("Ilex x altaclerensis", "Golden King"),
    "Liquidambar styraciflua Stel": ("Liquidambar styraciflua", None),
    "Liquidambar styraciflua ’Worp'": ("Liquidambar styraciflua", "Worplesdon"),
    "Liriodendron tulipifera Fastig": ("Liriodendron tulipifera", "Fastigiatum"),
    "Platanus x acerifolia Pyramida": ("Platanus x acerifolia", "Pyramidalis"),
    "Populus x canadensis Regenerat": ("Populus x canadensis", "Regenerata"),
    "Prunus insititia ‘Merryweath": ("Prunus insititia", "Merryweather"),
    "Quercus robur Fastigiata Koste": ("Quercus robur", "Fastigiata Koster"),
    "Sorbus x thuringiaca Fastigiat": ("Sorbus x thuringiaca", "Fastigiata"),
    "Syringa reticulata subs reticu": ("Syringa reticulata", None),
    "X Cupressocyparis l. Castlewel": ("x Cupressocyparis leylandii", "Castlewellan"),
    "X Cupressocyparis leylandii": ("x Cupressocyparis leylandii", None),
    # Abbreviated species.
    "Carpinus bet 'Rockhampton Red'": ("Carpinus betulus", "Rockhampton Red"),
    "Crataegus laev 'Crimson Cloud'": ("Crataegus laevigata", "Crimson Cloud"),
    "Ginkgo bilo 'Fastigiata Blagon": ("Ginkgo biloba", "Fastigiata Blagon"),
    "Hippophae salicif 'Streetwise'": ("Hippophae salicifolia", "Streetwise"),
    "Liquidamber s. Slender Silhout": ("Liquidambar styraciflua", "Slender Silhouette"),
    "Magnolia g. Galissoniere": ("Magnolia grandiflora", "Galissonière"),
    "Magnolia x L. Leonard Messel": ("Magnolia x loebneri", "Leonard Messel"),
    "Prunus × subhirtella 'AR'": ("Prunus subhirtella", "Autumnalis Rosea"),
    "Quercus c. 'Green Spire'": ("Quercus castaneifolia", "Green Spire"),
    "Robinia p. 'Casque Rouge'": ("Robinia pseudoacacia", "Casque Rouge"),
    "Robinia pseudoacacia Bessonian": ("Robinia pseudoacacia", "Bessoniana"),
    "Sorbus a 'Cardinal Royal'": ("Sorbus aucuparia", "Cardinal Royal"),
    "Sorbus a. 'Cardinal Royal'": ("Sorbus aucuparia", "Cardinal Royal"),
    "Styphnolobium jap 'Regent'": ("Styphnolobium japonicum", "Regent"),
    "Taxus b. F.Aureomarginata": ("Taxus baccata", "Fastigiata Aureomarginata"),
    # A capitalised epithet that is the species, not a cultivar.
    "Magnolia Kobus": ("Magnolia kobus", None),
    # Cultivars written in the epithet's place.
    "Prunus pissardii": ("Prunus cerasifera", "Pissardii"),
    "Prunus shimidsu sakura": ("Prunus", "Shimidsu Sakura"),
    "Ulmus columella": ("Ulmus", "Columella"),
    "Ulmus minor Dampieri Aurea": ("Ulmus x hollandica", "Dampieri Aurea"),
    "Sorbus 'Autumn Spire'": ("Sorbus aucuparia", "Autumn Spire"),
    "Prunus serrula Tibetica": ("Prunus serrula", None),
    # Dessert apples are Malus domestica; the crab apples stay at the genus.
    "Malus Bramley's Seedling": ("Malus domestica", "Bramley's Seedling"),
    "Malus Cox's Orange Pippin": ("Malus domestica", "Cox's Orange Pippin"),
    "Malus Elstar": ("Malus domestica", "Elstar"),
    "Malus Golden Delicious": ("Malus domestica", "Golden Delicious"),
    "Malus James Grieve": ("Malus domestica", "James Grieve"),
    "Malus Jonagold": ("Malus domestica", "Jonagold"),
}

# `Genus - Species Unknown`, `Genus - Unknown Species`, `Populus nigra - Unkown Species`.
_UNKNOWN_SUFFIX = re.compile(r"\s+-\s+(species\s+unknown|(unknown|unkown)\s+species)$", re.IGNORECASE)

# Straight and curly quotes; the layer uses both, sometimes mismatched.
_QUOTES = "'\"‘’“”"
_QUOTED = re.compile(rf"^(?P<taxon>[^{_QUOTES}]*?)\s*[{_QUOTES}](?P<cultivar>.+?)[{_QUOTES}]?\s*$")

_HYBRID = frozenset({"x", "X", "×"})

_DBH = re.compile(r"(\d+)\s+Cen[a-z]*", re.IGNORECASE)
_METRES = re.compile(r"(\d+)\s+Metres?", re.IGNORECASE)
_SEASON = re.compile(r"Planting Season (\d{2})/(\d{2})")

# Refuse when more than this share of one coded field cannot be read.
MAX_UNREADABLE_SHARE = 0.01


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def split_species(raw) -> tuple[str | None, str | None]:
    """`(species string, cultivar)` from the layer's `LATIN_NAME`."""
    text = clean(raw)
    if text is None:
        return UNKNOWN_SPECIES, None
    if text in TAXA:
        return TAXA[text]
    text = _UNKNOWN_SUFFIX.sub("", text)
    match = _QUOTED.match(text)
    if match and match["taxon"]:
        return clean(match["taxon"]), clean(match["cultivar"].strip(_QUOTES))
    words = text.split()
    if words[-1] == "cv":
        return " ".join(words[:-1]), None
    # `Prunus Kanzan`, `Magnolia Galaxy`: a capitalised name straight after the genus.
    if len(words) >= 2 and words[1][:1].isupper() and words[1] not in _HYBRID:
        return words[0], " ".join(words[1:])
    # `Fraxinus oxycarpa Raywood`, `Populus x canadensis Robusta`.
    epithet = 2 if len(words) >= 3 and words[1] in _HYBRID else 1
    if (
        len(words) > epithet + 1
        and words[epithet].islower()
        and words[epithet + 1][:1].isupper()
        and words[epithet + 1].replace("-", "").isalpha()
    ):
        return " ".join(words[: epithet + 1]), " ".join(words[epithet + 1:])
    return text, None


def coded_number(value, pattern: re.Pattern, field: str) -> float | None:
    """The number in a coded `"32 Centimetres"` value; None for no code."""
    text = clean(value)
    if text is None or text == NO_CODE:
        return None
    match = pattern.fullmatch(text)
    if match is None:
        _counts[f"unreadable {field}"] += 1
        return None
    return float(match.group(1))


def planting_date(value) -> date | None:
    """`Planting Season 22/23` -> 2023-01-01, inside the winter it names."""
    text = clean(value)
    if text is None or text == NOT_APPLICABLE:
        return None
    match = _SEASON.fullmatch(text)
    if match is None or (int(match.group(1)) + 1) % 100 != int(match.group(2)):
        _counts["unreadable PLANTING_SEASON"] += 1
        return None
    return date(2000 + int(match.group(2)), 1, 1)


def is_tree(feature: dict) -> bool:
    rec = feature.get("attributes") or {}
    if clean(rec.get("LATIN_NAME")) in NOT_A_TREE:
        _counts["not a tree (stump, pit, planting location, error)"] += 1
        return False
    if clean(rec.get("CANCELLATION_REASON")) not in (None, NOT_APPLICABLE):
        _counts["cancelled planting"] += 1
        return False
    return True


def iter_row_chunks():
    """One ArcGIS page (1,000 features) at a time, geometry in WGS84."""
    return iter_features(LAYER, out_fields=OUT_FIELDS, return_geometry=True, out_sr=4326)


_counts: dict[str, int] = {
    "no ASSET_ID": 0,
    "not a tree (stump, pit, planting location, error)": 0,
    "cancelled planting": 0,
    "unreadable DBH": 0,
    "unreadable CROWN_WIDTH": 0,
    "unreadable PLANTING_SEASON": 0,
}
_rows_read = 0


def transform(features: list[dict]) -> pa.Table:
    global _rows_read
    tree_id: list[str] = []
    species: list[str | None] = []
    cultivar: list[str | None] = []
    plant_date: list[date | None] = []
    latitude: list[float | None] = []
    longitude: list[float | None] = []
    dbh: list[float | None] = []
    crown: list[float | None] = []

    for feature in features:
        _rows_read += 1
        rec = feature.get("attributes") or {}
        raw_id = clean(rec.get("ASSET_ID"))
        if raw_id is None:
            _counts["no ASSET_ID"] += 1
            continue
        lat, lon = esri_point(feature.get("geometry"))
        name, selection = split_species(rec.get("LATIN_NAME"))
        if clean(rec.get("DEAD")) == "Y":
            name, selection = DEAD_SPECIES, None
        diameter_cm = coded_number(rec.get("DBH"), _DBH, "DBH")

        tree_id.append(f"bristol-{raw_id}")
        species.append(name)
        cultivar.append(selection)
        plant_date.append(planting_date(rec.get("PLANTING_SEASON")))
        latitude.append(lat)
        longitude.append(lon)
        dbh.append(diameter_cm / 2.54 if diameter_cm else None)
        crown.append(coded_number(rec.get("CROWN_WIDTH"), _METRES, "CROWN_WIDTH") or None)

    return pa.table(
        {
            "tree_id": pa.array(tree_id, type=pa.string()),
            "city": pa.array(["GBBRS"] * len(tree_id), type=pa.string()),
            "species": pa.array(species, type=pa.string()),
            "cultivar": pa.array(cultivar, type=pa.string()),
            "plant_date": pa.array(plant_date, type=pa.date32()),
            "latitude": pa.array(latitude, type=pa.float64()),
            "longitude": pa.array(longitude, type=pa.float64()),
            "diameter_at_breast_height": pa.array(dbh, type=pa.float64()),
            "crown_width_m": pa.array(crown, type=pa.float64()),
        }
    )


def report() -> None:
    for reason in ("no ASSET_ID", "not a tree (stump, pit, planting location, error)", "cancelled planting"):
        if _counts[reason]:
            print(f"Bristol: dropped {_counts[reason]:,} rows: {reason}", file=sys.stderr)
    for field in ("DBH", "CROWN_WIDTH", "PLANTING_SEASON"):
        bad = _counts[f"unreadable {field}"]
        if bad:
            print(f"Bristol: {bad:,} unreadable {field} values nulled", file=sys.stderr)
        if _rows_read and bad / _rows_read > MAX_UNREADABLE_SHARE:
            raise RuntimeError(
                f"Bristol: {bad:,} of {_rows_read:,} {field} values are unreadable; "
                "the coding changed"
            )


if __name__ == "__main__":
    table = stream_to_table(iter_row_chunks(), transform, keep=is_tree, label="Bristol OpenData")
    report()
    table = validate_coordinates(table, city="Bristol", city_code="GBBRS")
    table = enforce_tree_schema(table, city="Bristol", data_source="BRISTOL_OPENDATA")
    emit(table)

#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "requests", "pytrilogy"]
# ///
"""Milan's monumental trees: the city's entries on Italy's national register.

Source: "Gli alberi monumentali nel Comune di Milano" (DS2489) on
dati.comune.milano.it, CC BY 4.0: the 13 trees in the city on the Ministry's
Elenco degli alberi monumentali d'Italia (law 10/2013), each with its register
number (`N. SCHEDA`, `03/F205/MI/03`), the criterion it was listed under, its
girth and height, and a link to the Comune's page for it.

The `NOTABLE_ITMIL` partition (tree_dedup.preql, "notable row"):

  * **Linked, not grid-matched.**  The register's points are a map pin, not a
    survey; three sit within 2 m of a different tree.  Each row is linked by
    shared/notable.py to the inventory tree of the same genus and a matching
    trunk within 30 m, read from milan_tree_info.py's own published table so
    the id exists in the build.  Six of the 13 link (2026-11-15); the others
    publish at the register's point as trees of their own.
  * **The name is the Comune's.**  The register has no name column, but the
    Comune's page for each tree is titled, and the title is in the link's slug.
    A slug that starts with an article (`il-platano-siamese-di-villa-litta`)
    is a proper name -- "Il Platano Siamese di Villa Litta" -- and is
    published as `tree_name`; a slug that is a species and a place
    (`bagolaro-villa-belgiojoso`) is a description, and the tree goes unnamed.
  * **The significance** says what the register says: listed, for which
    criterion, with its girth and height, and whether the tree is also declared
    of notable public interest (`PROPOSTA ... = vigente`).  The source is the
    Comune's page.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.ingest import emit, enforce_tree_schema, validate_coordinates  # noqa: E402
from shared.notable import (  # noqa: E402
    diameter_from_circumference_cm,
    link_to_inventory,
    notable_table,
)
from shared.platforms.ckan import CkanResource, read_geojson_features  # noqa: E402

RESOURCE = CkanResource(
    "dati.comune.milano.it", "ea95b273-0a6d-444a-a1fb-b83eae24ce4d"
)
DATA_SOURCE = "NOTABLE_ITMIL"
REGISTER = "Elenco degli alberi monumentali d'Italia"

# The listing criteria of DM 23/10/2014, art. 5, as the register words them.
CRITERIA_EN = {
    "età e/o dimensioni": "its age and/or size",
    "forma e portamento": "its form and bearing",
    "valore ecologico": "its ecological value",
    "pregio paesaggistico": "its landscape value",
    "rarità botanica": "its botanical rarity",
    "architettura vegetale": "its plant architecture",
    "valore storico, culturale, religioso": "its historical, cultural or religious value",
}

# Words a title keeps in lower case, and the ones the slug lost an accent or
# numerals' case from.
_LOWER = {"di", "del", "dei", "della", "delle", "dello", "degli", "a", "ai", "al", "alla", "e", "in"}
_SPELLED = {"biodiversita": "Biodiversità", "xxiv": "XXIV"}
_ARTICLES = ("il", "la", "lo", "l", "i", "gli", "le")

REQUIRED = (
    "N. SCHEDA", "Link", "SPECIE - NOME SCIENTIFICO", "CIRCONFERENZA FUSTO (cm)",
    "ALTEZZA (m)", "CRITERI DI MONUMENTALITÀ",
    "PROPOSTA DICHIARAZIONE NOTEVOLE INTERESSE PUBBLICO",
)


def clean(value) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def name_from_link(link: str | None) -> str | None:
    """The tree's own name from its Comune page's slug, when the slug is one."""
    if not link:
        return None
    slug = link.rstrip("/").rsplit("/", 1)[-1].lower()
    words = [w for w in slug.split("-") if w]
    if len(words) < 2 or words[0] not in _ARTICLES:
        return None
    titled = []
    for i, word in enumerate(words):
        if word in _SPELLED:
            titled.append(_SPELLED[word])
        elif i > 0 and word in _LOWER:
            titled.append(word)
        else:
            titled.append(word.capitalize())
    return " ".join(titled)


def scientific_name(value: str | None) -> str | None:
    """`Platanus acerifolia` from `Platanus acerifolia (Aiton) Willd`."""
    words = (clean(value) or "").split()
    if not words:
        return None
    if len(words) >= 3 and words[1].lower() in ("x", "×"):
        return " ".join(words[:3])
    return " ".join(words[:2])


def significance_for(props: dict) -> str:
    scheda = clean(props.get("N. SCHEDA"))
    criteria = clean(props.get("CRITERI DI MONUMENTALITÀ")) or ""
    reasons = [
        CRITERIA_EN.get(c.strip().lower(), f"«{c.strip()}»")
        for c in re.split(r";|\n", criteria)
        if c.strip()
    ]
    text = "Listed on Italy's national register of monumental trees"
    if scheda:
        text += f" (no. {scheda})"
    if reasons:
        text += " for " + " and ".join(reasons)
    text += "."
    girth = clean(props.get("CIRCONFERENZA FUSTO (cm)"))
    height = clean(props.get("ALTEZZA (m)"))
    measures = []
    if girth:
        measures.append(f"trunk girth {girth.replace(' ', '')} cm")
    if height:
        measures.append(f"height {height} m")
    if measures:
        text += " " + ", ".join(measures).capitalize() + "."
    if (clean(props.get("PROPOSTA DICHIARAZIONE NOTEVOLE INTERESSE PUBBLICO")) or "").lower() == "vigente":
        text += " Also declared of notable public interest."
    return text


def register_rows(features: list[dict]) -> list[dict]:
    if features:
        missing = [c for c in REQUIRED if c not in (features[0].get("properties") or {})]
        if missing:
            raise RuntimeError(f"Milan monumental trees: columns {missing} are missing")
    rows = []
    for feature in features:
        props = feature.get("properties") or {}
        scheda = clean(props.get("N. SCHEDA"))
        coords = (feature.get("geometry") or {}).get("coordinates") or [None, None]
        if not scheda:
            continue
        diameter_cm = diameter_from_circumference_cm(props.get("CIRCONFERENZA FUSTO (cm)"))
        link = clean(props.get("Link"))
        rows.append(
            {
                "tree_id": "mil-mon-" + re.sub(r"[^A-Za-z0-9]+", "-", scheda).strip("-"),
                "species": scientific_name(props.get("SPECIE - NOME SCIENTIFICO")),
                "tree_name": name_from_link(link),
                "latitude": float(coords[1]) if coords[1] is not None else None,
                "longitude": float(coords[0]) if coords[0] is not None else None,
                "dbh_in": diameter_cm / 2.54 if diameter_cm else None,
                "significance": significance_for(props),
                "significance_source": link or RESOURCE.landing_url,
            }
        )
    return rows


def build(features: list[dict], inventory) -> "pa.Table":  # noqa: F821
    rows = register_rows(features)
    links = link_to_inventory(rows, inventory, label="Milan monumental")
    table = notable_table(rows, links, city="ITMIL")
    table = validate_coordinates(table, city="Milan", city_code="ITMIL")
    return enforce_tree_schema(
        table, city="Milan", data_source=DATA_SOURCE, proper_names=True
    )


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent))
    from milan_tree_info import load_published  # noqa: E402

    emit(build(read_geojson_features(RESOURCE), load_published()))

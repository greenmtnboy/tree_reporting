"""`shared.wcvp`, the WCVP ingests, and the species audit's WCVP lookup.

Run on a synthetic `wcvp.zip` in Kew's layout (pipe-delimited, no quoting),
so nothing here reaches Kew.  The rows are the cases the audit's verdicts
turn on: an accepted name, a synonym, a hybrid written with `×`, a name with
a second record under another author (`Quercus lyrata`), and a misapplied
record beside an accepted one (`Platanus occidentalis`).
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pyarrow as pa
import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))
sys.path.insert(0, str(RAW_DIR / "tools"))

from shared import wcvp  # noqa: E402

NAMES_HEADER = (
    "plant_name_id|ipni_id|taxon_rank|taxon_status|family|genus_hybrid|genus|species_hybrid|"
    "species|infraspecific_rank|infraspecies|parenthetical_author|primary_author|"
    "publication_author|place_of_publication|volume_and_page|first_published|"
    "nomenclatural_remarks|geographic_area|lifeform_description|climate_description|"
    "taxon_name|taxon_authors|accepted_plant_name_id|basionym_plant_name_id|"
    "replaced_synonym_author|homotypic_synonym|parent_plant_name_id|powo_id|hybrid_formula|reviewed"
)


def name_row(pid, status, genus, species, taxon_name, accepted, authors="L.", hybrid=""):
    cells = [
        str(pid), f"{pid}-1", "Species", status, "Testaceae", "", genus, hybrid, species,
        "", "", "", authors, "", '"Fl. Carol."', ": 1", "(1753)", "", "E. U.S.A.", "tree",
        "temperate", taxon_name, authors, "" if accepted is None else str(accepted), "", "",
        "", "", f"{pid}-1", "", "N",
    ]
    return "|".join(cells)


NAMES = [
    name_row(1, "Accepted", "Acer", "rubrum", "Acer rubrum", 1),
    name_row(2, "Synonym", "Acer", "sanguineum", "Acer sanguineum", 1),
    name_row(3, "Accepted", "Platanus", "hispanica", "Platanus × hispanica", 3, hybrid="×"),
    name_row(4, "Accepted", "Quercus", "lyrata", "Quercus lyrata", 4, authors="Walter"),
    name_row(5, "Synonym", "Quercus", "lyrata", "Quercus lyrata", 6, authors="Spreng."),
    name_row(6, "Accepted", "Quercus", "lobata", "Quercus lobata", 6),
    name_row(7, "Accepted", "Platanus", "occidentalis", "Platanus occidentalis", 7),
    name_row(8, "Misapplied", "Platanus", "occidentalis", "Platanus occidentalis", 3, authors="Hook. & Arn."),
    name_row(9, "Unplaced", "Ulmus", "mysteria", "Ulmus mysteria", None),
]
DISTRIBUTION = [
    "plant_locality_id|plant_name_id|continent_code_l1|continent|region_code_l2|region|"
    "area_code_l3|area|introduced|extinct|location_doubtful",
    "10|1|7|NORTHERN AMERICA|78|Southeastern U.S.A.|VRG|Virginia|0|0|0",
    "11|3|1|EUROPE|10|Northern Europe|GRB|Great Britain|1|0|0",
]


@pytest.fixture()
def wcvp_zip(tmp_path: Path) -> Path:
    path = tmp_path / "wcvp.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(wcvp.NAMES_MEMBER, "\n".join([NAMES_HEADER, *NAMES]) + "\n")
        archive.writestr(wcvp.DISTRIBUTION_MEMBER, "\n".join(DISTRIBUTION) + "\n")
    return path


@pytest.mark.parametrize(
    "name, key",
    [
        ("Platanus × hispanica", "platanus x hispanica"),
        ("Platanus x hispanica", "platanus x hispanica"),
        ("  Acer   RUBRUM ", "acer rubrum"),
        ("× Cupressocyparis leylandii", "x cupressocyparis leylandii"),
        ("Rubus microgène", "rubus microgene"),
        ("", None),
        (None, None),
    ],
)
def test_name_key_folds_what_the_ingests_differ_on(name, key):
    assert wcvp.name_key(name) == key


def test_the_arrow_key_matches_the_python_key():
    names = ["Platanus × hispanica", "Rubus microgène x", "  Acer  rubrum", None, ""]
    keys = wcvp.name_key_array(pa.chunked_array([names])).to_pylist()
    assert keys == [wcvp.name_key(n) for n in names]


def test_names_ingest_resolves_accepted_names(wcvp_zip, monkeypatch):
    monkeypatch.setenv("WCVP_ZIP", str(wcvp_zip))
    import wcvp_names

    table = wcvp_names.build()
    rows = {r["plant_name_id"]: r for r in table.to_pylist()}
    assert len(rows) == len(NAMES)
    assert rows[2]["accepted_taxon_name"] == "Acer rubrum"
    assert rows[3]["name_key"] == "platanus x hispanica"
    assert rows[9]["accepted_taxon_name"] is None
    # A quote is text in WCVP, not a field delimiter.
    assert set(table.schema.names) >= set(wcvp.NAME_COLUMNS)
    assert table.column("source_version")[0].as_py() == wcvp.SOURCE_CITATION


def test_distribution_flags_are_booleans(wcvp_zip, monkeypatch):
    monkeypatch.setenv("WCVP_ZIP", str(wcvp_zip))
    import wcvp_distribution

    rows = {r["plant_locality_id"]: r for r in wcvp_distribution.build().to_pylist()}
    assert rows[10]["introduced"] is False and rows[11]["introduced"] is True
    assert rows[10]["area_code_l3"] == "VRG"


def test_read_member_refuses_a_shifted_row(tmp_path):
    path = tmp_path / "wcvp.zip"
    bad = DISTRIBUTION[:2] + ["12|1|7|NORTHERN AMERICA\n|78|x|VRG|Virginia|0|0|0"]
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(wcvp.DISTRIBUTION_MEMBER, "\n".join(bad) + "\n")
    with pytest.raises(Exception):
        wcvp.read_member(path, wcvp.DISTRIBUTION_MEMBER, wcvp.DISTRIBUTION_COLUMNS)


@pytest.fixture()
def kew(wcvp_zip):
    from species_audit import Wcvp

    return Wcvp(str(wcvp_zip))


@pytest.mark.parametrize(
    "name, status, accepted",
    [
        ("Acer rubrum", "accepted", None),
        ("Acer sanguineum", "synonym", "Acer rubrum"),
        ("Platanus x hispanica", "accepted", None),
        ("Acer rubra", "not_found", None),
        ("Ulmus mysteria", "unplaced", None),
        # The misapplied record says nothing about what the name means.
        ("Platanus occidentalis", "accepted", None),
    ],
)
def test_lookup_reads_status_and_synonymy(kew, name, status, accepted):
    reading = kew.lookup(name)
    assert reading["status"] == status
    assert reading["accepted"] == accepted


def test_two_records_that_disagree_are_ambiguous(kew):
    reading = kew.lookup("Quercus lyrata")
    assert reading["status"] == "ambiguous"
    assert reading["readings"] == ["accepted:-", "synonym:Quercus lobata"]


def test_classify_maps_a_typo_and_refuses_two_real_names(kew):
    from species_audit import classify

    pairs = [
        ("Acer rubrumm", 50, 1, "Acer rubrum", 9000, 20),
        ("Quercus lobata", 100, 2, "Quercus lyrata", 2773, 3),
    ]
    kew.prefetch([p[0] for p in pairs] + [p[3] for p in pairs])
    verdicts = classify(pairs, kew)
    assert [v[:2] for v in verdicts["MISSPELLING"]] == [("Acer rubrumm", "Acer rubrum")]
    assert [v[:2] for v in verdicts["REFUSED"]] == [("Quercus lobata", "Quercus lyrata")]

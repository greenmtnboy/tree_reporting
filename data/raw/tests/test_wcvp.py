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


@pytest.fixture()
def enrichment_kew(wcvp_zip, monkeypatch):
    from enrichment import _tree_enrichment_sources as sources

    monkeypatch.setattr(sources, "_WCVP", wcvp.Checklist(str(wcvp_zip)))
    monkeypatch.setattr(sources, "_WCVP_DISABLED", False)
    return sources


def test_enrichment_reads_the_accepted_taxon_and_its_areas(enrichment_kew):
    text = enrichment_kew.fetch_wcvp_text("Acer sanguineum")
    assert "WCVP accepted name via synonym: Acer rubrum" in text
    # The areas are the accepted taxon's, in the lines the ecoregion matcher reads.
    assert "Distribution natives: Virginia" in text
    assert "path=Northern America > Southeastern U.S.A. > Virginia" in text
    assert "Lifeform: tree" in text


def test_enrichment_reports_a_homonym_and_a_misapplication_without_following_them(enrichment_kew):
    lyrata = enrichment_kew.fetch_wcvp_text("Quercus lyrata")
    assert "Taxonomic status: Accepted" in lyrata
    assert "as a synonym of Quercus lobata" in lyrata
    platanus = enrichment_kew.fetch_wcvp_text("Platanus occidentalis")
    assert "Taxonomic status: Accepted" in platanus
    assert "misapplied (auct.) to: Platanus × hispanica" in platanus


def test_enrichment_falls_back_to_the_binomial_and_marks_introduced_areas(enrichment_kew):
    text = enrichment_kew.fetch_wcvp_text("Platanus x hispanica 'Bloodgood'")
    assert text.startswith("WCVP query normalized to: Platanus x hispanica")
    assert "Distribution introduced: Great Britain" in text
    assert "Distribution natives" not in text


def test_enrichment_carries_on_without_kew_when_the_parquets_are_missing(monkeypatch, tmp_path, capsys):
    from enrichment import _tree_enrichment_sources as sources

    monkeypatch.setattr(sources, "_WCVP", wcvp.Checklist(str(tmp_path / "absent.zip")))
    monkeypatch.setattr(sources, "_WCVP_DISABLED", False)
    assert sources.fetch_wcvp_text("Acer rubrum") is None
    assert "WCVP unavailable" in capsys.readouterr().err
    assert sources.wcvp_checklist() is None


def test_wikipedia_keeps_the_intro_and_the_descriptive_sections():
    from enrichment._tree_enrichment_sources import wikipedia_article_parts

    extract = (
        "Halesia carolina is a tree.\n\n\n== Description ==\nGrows to 8 m.\n\n"
        "=== Flowers ===\nWhite bells.\n\n\n== Taxonomy ==\nNamed by Linnaeus.\n\n\n"
        "== Distribution and habitat ==\nSoutheastern U.S.A.\n\n\n== References ==\nx"
    )
    intro, sections = wikipedia_article_parts(extract)
    assert intro == "Halesia carolina is a tree."
    assert sections == [
        ("Description", "Grows to 8 m.\nFlowers:\nWhite bells."),
        ("Distribution and habitat", "Southeastern U.S.A."),
    ]


def test_wikipedia_reads_the_same_sections_in_a_fallback_language():
    from enrichment._tree_enrichment_sources import wikipedia_article_parts

    extract = (
        "Myrcianthes leucoxyla es una especie.\n\n\n== Descripción ==\nÁrbol de 15 m.\n\n\n"
        "== Taxonomía ==\nx\n\n\n== Distribución y hábitat ==\nAndes de Colombia."
    )
    _, sections = wikipedia_article_parts(extract)
    assert [title for title, _ in sections] == ["Descripción", "Distribución y hábitat"]

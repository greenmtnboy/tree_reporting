"""`family` is Kew's: the name's own record, else an unambiguous genus.

The column was in the table's schema from the start and null on every row;
these pin where it now comes from, what it refuses to guess, and that a load
fills what is missing without touching what is there.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import pyarrow as pa
import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from enrichment import _tree_enrichment_sources as sources  # noqa: E402
from shared import wcvp  # noqa: E402

NAMES_HEADER = (
    "plant_name_id|ipni_id|taxon_rank|taxon_status|family|genus_hybrid|genus|species_hybrid|"
    "species|infraspecific_rank|infraspecies|parenthetical_author|primary_author|"
    "publication_author|place_of_publication|volume_and_page|first_published|"
    "nomenclatural_remarks|geographic_area|lifeform_description|climate_description|"
    "taxon_name|taxon_authors|accepted_plant_name_id|basionym_plant_name_id|"
    "replaced_synonym_author|homotypic_synonym|parent_plant_name_id|powo_id|hybrid_formula|reviewed"
)


def name_row(pid, rank, status, family, taxon_name, accepted):
    genus, _, species = taxon_name.replace("× ", "").partition(" ")
    cells = [
        str(pid), f"{pid}-1", rank, status, family, "", genus, "", species,
        "", "", "", "L.", "", "", ": 1", "(1753)", "", "", "tree",
        "temperate", taxon_name, "L.", "" if accepted is None else str(accepted), "", "",
        "", "", f"{pid}-1", "", "N",
    ]
    return "|".join(cells)


NAMES = [
    name_row(1, "Species", "Accepted", "Sapindaceae", "Acer rubrum", 1),
    name_row(2, "Genus", "Accepted", "Sapindaceae", "Acer", 2),
    # A sunk homonym in another family beside the accepted genus, as Kew has
    # for Ilex, Syringa, Lonicera, Phoenix and Cedrus.
    name_row(3, "Genus", "Accepted", "Aquifoliaceae", "Ilex", 3),
    name_row(4, "Genus", "Synonym", "Fagaceae", "Ilex", 9),
    # Two accepted genera of one name: nothing to choose between them.
    name_row(5, "Genus", "Accepted", "Rosaceae", "Dubia", 5),
    name_row(6, "Genus", "Accepted", "Fabaceae", "Dubia", 6),
    # No accepted record, and the synonyms disagree.
    name_row(7, "Genus", "Synonym", "Magnoliaceae", "Michelia", 9),
    name_row(8, "Genus", "Synonym", "Alismataceae", "Michelia", 9),
    name_row(9, "Genus", "Accepted", "Fagaceae", "Quercus", 9),
    name_row(10, "Species", "Synonym", "Magnoliaceae", "Michelia doltsopa", 11),
    name_row(11, "Species", "Accepted", "Magnoliaceae", "Magnolia doltsopa", 11),
    name_row(12, "Species", "Accepted", "Platanaceae", "Platanus × hispanica", 12),
    name_row(13, "Genus", "Accepted", "Bignoniaceae", "× Chitalpa", 13),
    name_row(14, "Genus", "Synonym", "Cactaceae", "Cactus", 9),
    # A species named like a genus must not answer for the genus.
    name_row(15, "Species", "Accepted", "Testaceae", "Sorbus", 15),
    name_row(16, "Species", "Misapplied", "Wrongaceae", "Acer rubrum", 11),
]


@pytest.fixture()
def kew(tmp_path, monkeypatch):
    path = tmp_path / "wcvp.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(wcvp.NAMES_MEMBER, "\n".join([NAMES_HEADER, *NAMES]) + "\n")
        archive.writestr(
            wcvp.DISTRIBUTION_MEMBER,
            "|".join(wcvp.DISTRIBUTION_COLUMNS) + "\n"
            "10|1|7|NORTHERN AMERICA|78|Southeastern U.S.A.|VRG|Virginia|0|0|0\n",
        )
    checklist = wcvp.Checklist(str(path))
    monkeypatch.setattr(sources, "_WCVP", checklist)
    monkeypatch.setattr(sources, "_WCVP_DISABLED", False)
    return checklist


@pytest.mark.parametrize(
    "name, family",
    [
        ("Acer rubrum", "Sapindaceae"),                 # its own record; the misapplied one is not read
        ("Acer", "Sapindaceae"),                        # a genus-keyed row
        ("Acer rubrum 'October Glory'", "Sapindaceae"),  # a cultivar, by its genus
        ("Acer rubrumm", "Sapindaceae"),                # a misspelt epithet, by its genus
        ("Ilex opaca", "Aquifoliaceae"),                # the accepted genus, not its sunk homonym
        ("Michelia doltsopa", "Magnoliaceae"),          # a synonym carries its accepted family
        ("Platanus x hispanica", "Platanaceae"),        # the ingest's `x` is Kew's hybrid sign
        ("Chitalpa tashkentensis", "Bignoniaceae"),     # a nothogenus written without its mark
        ("x Chitalpa tashkentensis", "Bignoniaceae"),
        ("Dubia vulgaris", None),                       # two accepted genera
        ("Michelia alba", None),                        # no accepted genus, synonyms disagree
        ("Nonesuch arborea", None),
        ("Sorbus aria", None),                          # `Sorbus` here is a species-rank record
        ("Unknown", None),
        ("Cactus", None),                               # a sentinel, though Kew has a genus Cactus
        ("", None),
        (None, None),
    ],
)
def test_family_is_the_names_own_or_its_unambiguous_genus(kew, name, family):
    assert sources.family_from_checklist(kew, name) == family


def table(rows: list[tuple[str, str | None]]) -> pa.Table:
    return pa.table({
        "species": pa.array([s for s, _ in rows], pa.string()),
        "family": pa.array([f for _, f in rows], pa.string()),
    })


def test_a_load_fills_what_is_null_and_keeps_what_is_there(kew, capsys):
    before = table([
        ("Acer rubrum", None),
        ("Ilex opaca", "Typed by a reviewer"),
        ("Dubia vulgaris", None),
        ("Cactus", None),
    ])
    after = sources.with_wcvp_family(before)
    assert after.column("family").to_pylist() == ["Sapindaceae", "Typed by a reviewer", None, None]
    assert after.schema == before.schema
    assert "filled family on 1 row(s)" in capsys.readouterr().err


def test_a_filled_table_is_returned_untouched(kew):
    filled = table([("Acer rubrum", "Sapindaceae"), ("Unknown", None)])
    assert sources.with_wcvp_family(filled) is filled


def test_without_kew_the_table_comes_back_as_it_was(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(sources, "_WCVP", wcvp.Checklist(str(tmp_path / "absent.zip")))
    monkeypatch.setattr(sources, "_WCVP_DISABLED", False)
    before = table([("Acer rubrum", None)])
    assert sources.with_wcvp_family(before) is before
    assert sources.wcvp_family("Acer rubrum") is None
    assert "WCVP unavailable" in capsys.readouterr().err


def test_the_job_fills_family_on_load_and_for_a_new_row():
    """The two places a row's family is set, read from the job's source: the
    load chain (before the alias step, so an alias row inherits it) and the
    row a run writes.  `"family": None` there is what left the column empty."""
    job = (RAW_DIR / "tree_enrichment.py").read_text(encoding="utf-8")
    assert '"family":               wcvp_family(scientific_name)' in job
    chain = job[job.index("return with_sentinel_rows("):]
    assert chain.index("with_species_aliases(") < chain.index("with_wcvp_family(")

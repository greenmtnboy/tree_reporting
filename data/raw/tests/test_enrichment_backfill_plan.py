"""What backfill.py decides before it calls anything: the queue, what has been
asked, and whether a checkpoint may replace the published table."""

from __future__ import annotations

import sys
from pathlib import Path

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

from enrichment._backfill_plan import (  # noqa: E402
    already_asked,
    covering,
    read_asked,
    rebase_on_published,
    record_asked,
    regressions,
    tree_ordered,
)

COUNTS = {"Acer platanoides": 500, "Tilia cordata": 300, "Quercus robur": 150, "Sorbus aria": 40, "Abies alba": 10}


def test_the_queue_is_most_trees_first_and_ties_break_by_name():
    names = ["Zelkova serrata", "Abies alba", "Tilia cordata", "Acer platanoides", "Carya ovata"]
    assert tree_ordered(names, COUNTS) == [
        "Acer platanoides", "Tilia cordata", "Abies alba", "Carya ovata", "Zelkova serrata",
    ]


def test_coverage_stops_at_the_species_that_reaches_the_share():
    ordered = tree_ordered(list(COUNTS), COUNTS)
    assert covering(ordered, COUNTS, 0.5) == ["Acer platanoides"]
    assert covering(ordered, COUNTS, 0.8) == ["Acer platanoides", "Tilia cordata"]
    assert covering(ordered, COUNTS, 0.95) == ["Acer platanoides", "Tilia cordata", "Quercus robur"]
    assert covering(ordered, COUNTS, 1.0) == ordered
    # No counts at all is not a reason to ask nobody.
    assert covering(["A b", "C d"], {}, 0.97) == ["A b", "C d"]


def test_a_species_that_answered_is_never_asked_again_but_a_failed_call_may_be(tmp_path):
    log = tmp_path / "asked.jsonl"
    assert read_asked(str(log)) == {}
    record_asked(str(log), "Acer rubrum", "answered", ["fire_risk"])
    record_asked(str(log), "Tilia cordata", "answered", [])  # every field came back null
    record_asked(str(log), "Quercus robur", "llm_failed", [])
    record_asked(str(log), "Hedge end", "no_sources", [])
    asked = read_asked(str(log))
    assert already_asked(asked, retry_failed=False) == {"Acer rubrum", "Tilia cordata", "Quercus robur", "Hedge end"}
    assert already_asked(asked, retry_failed=True) == {"Acer rubrum", "Tilia cordata", "Hedge end"}
    # The retry that then answers replaces the failure.
    record_asked(str(log), "Quercus robur", "answered", ["fire_risk"])
    assert already_asked(read_asked(str(log)), retry_failed=True) >= {"Quercus robur"}


def row(species, **values):
    return {"species": species, "family": None, "fire_risk": None, "sun_exposure": None,
            "common_names": None, "is_complete": False, **values}


def test_the_published_table_is_the_base_and_the_checkpoint_only_fills_its_nulls():
    published = [
        row("Acer rubrum", common_names=["Red maple"], fire_risk="low"),
        row("Tilia cordata", common_names=["Small-leaved lime"]),   # renamed in the form since the checkpoint
        row("Ulmus nova"),                                          # added by the daily job since
    ]
    checkpoint = [
        row("Acer rubrum", common_names=["Red maple"], fire_risk="high", family="Sapindaceae"),
        row("Tilia cordata", common_names=["Littleleaf linden"], family="Malvaceae", sun_exposure=["full_sun"]),
        row("Gone away", family="Rosaceae"),                        # purged upstream since
    ]
    rebased, gained = rebase_on_published(checkpoint, published, ["family", "fire_risk", "sun_exposure"])
    assert [r["species"] for r in rebased] == ["Acer rubrum", "Tilia cordata", "Ulmus nova"]
    assert rebased[0]["fire_risk"] == "low"                         # a published value is never replaced
    assert rebased[0]["family"] == "Sapindaceae"
    assert rebased[1]["common_names"] == ["Small-leaved lime"]      # nor one in a field not asked for
    assert rebased[1]["sun_exposure"] == ["full_sun"]
    assert rebased[2] == published[2]
    assert gained == {"family": 2, "fire_risk": 0, "sun_exposure": 1}
    assert regressions(rebased, published) == []


def test_a_field_not_requested_is_not_carried_over():
    published = [row("Acer rubrum")]
    checkpoint = [row("Acer rubrum", family="Sapindaceae", fire_risk="low")]
    rebased, _ = rebase_on_published(checkpoint, published, ["family"])
    assert rebased[0]["fire_risk"] is None


def test_a_lost_row_or_value_is_named_and_a_derived_column_is_not():
    published = [
        row("Acer rubrum", common_names=["Red maple"], fire_risk="low", is_complete=True),
        row("Tilia cordata", family="Malvaceae"),
    ]
    candidate = [row("Acer rubrum", common_names=None, fire_risk="high", is_complete=False)]
    assert regressions(candidate, published) == [
        "Acer rubrum.fire_risk: 'low' -> 'high'",
        "Acer rubrum.common_names: ['Red maple'] -> None",
        "Tilia cordata: row missing",
    ]
    # Gaining a value where the published row had none is the point.
    assert regressions([row("Acer rubrum", family="Sapindaceae")], [row("Acer rubrum")]) == []

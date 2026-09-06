#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pytest", "pyarrow", "requests", "pillow", "instructor[litellm]", "duckdb", "google-genai", "jsonref", "pytrilogy", "pydantic", "google-cloud-storage"]
# ///
"""The enrichment admin must only ever write rows the pipeline would have.

Its output goes straight to the object every tree row joins to, so the
invariants that `tree_enrichment.py` and `_tree_shared.py` maintain --
one row per species, sentinels present and untouched, both hybrid spellings
in step, empty-means-NULL, a common name and a growth form on every row --
have to hold for a hand-written row as much as for a generated one.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW_DIR))

# The admin reuses tree_enrichment.py, which imports the LLM client at module
# scope.  The lightweight `uv run --with pytest python -m pytest tests -q` sweep
# does not carry that dependency, so this file steps aside there rather than
# breaking collection for every other test; run it in full with
# `uv run tests/test_enrichment_admin.py`, which resolves its own dependencies.
pytest.importorskip("instructor", reason="run `uv run tests/test_enrichment_admin.py` for the admin tests")

import enrichment_admin as admin  # noqa: E402
from enrichment._tree_shared import (  # noqa: E402
    SPECIES_SENTINELS,
    sentinel_enrichment_rows,
    with_sentinel_rows,
)

NOW = datetime(2026, 9, 4, tzinfo=timezone.utc)


def _row(species: str, **over) -> dict:
    base = {f.name: None for f in admin.SCHEMA}
    base.update(
        species=species,
        genus=species.split(" ")[0],
        common_names=["a name"],
        tree_form="broadleaf",
        is_complete=False,
        enriched_at=NOW,
    )
    base.update(over)
    return base


def _table(*rows: dict) -> pa.Table:
    empty = pa.Table.from_pylist([], schema=admin.SCHEMA)
    return with_sentinel_rows(pa.concat_tables([empty, pa.Table.from_pylist(list(rows), schema=admin.SCHEMA)]))


def _payload(**over) -> dict:
    p = {"common_names": "Red maple, Swamp maple", "tree_form": "broadleaf"}
    p.update(over)
    return p


# ── form vocabulary ────────────────────────────────────────────────────────────


def test_every_editable_column_is_on_the_form_exactly_once():
    """A schema addition has to show up on the form, not silently fall off it."""
    on_form = [name for _, names in admin.GROUPS for name in names]
    assert sorted(on_form) == sorted(admin.EDITABLE)
    assert len(on_form) == len(set(on_form))


def test_field_specs_cover_the_schema_and_nothing_derived():
    names = [s["name"] for s in admin.field_specs()]
    assert names == admin.EDITABLE
    assert "species" not in names and "is_complete" not in names and "enriched_at" not in names


def test_enum_spellings_match_what_the_llm_prompt_emits():
    """`non-invasive` and `part_shade`, as the pydantic model and the published
    table have them -- not the `non_invasive` / `partial_shade` the preql
    comments mention.  A value nothing else writes is a value nothing reads."""
    from enrichment._tree_enrichment_models import TreeEnrichment

    fields = TreeEnrichment.model_fields
    assert set(admin.ENUMS["root_behavior"]) == set(fields["root_behavior"].annotation.__args__[0].__args__)
    assert set(admin.LIST_ENUMS["sun_exposure"]) == set(fields["sun_exposure"].annotation.__args__[0].__args__)
    assert set(admin.ENUMS["tree_form"]) == set(fields["tree_form"].annotation.__args__)


# ── coercion ───────────────────────────────────────────────────────────────────


def test_empty_becomes_null_the_way_the_pipeline_writes_unknown():
    row = admin.coerce_row("Acer rubrum", _payload(description="  ", sun_exposure=[], bloom_months=[], usda_zone_min=""))
    assert row["description"] is None
    assert row["sun_exposure"] is None
    assert row["bloom_months"] is None
    assert row["usda_zone_min"] is None
    assert row["species"] == "Acer rubrum"


def test_lists_accept_comma_text_and_are_deduped_and_trimmed():
    row = admin.coerce_row("Acer rubrum", _payload(common_names=" Red maple ,Swamp maple, Red maple", soil_preferences=["well-drained", "well-drained"]))
    assert row["common_names"] == ["Red maple", "Swamp maple"]
    assert row["soil_preferences"] == ["well-drained"]


def test_bloom_months_and_ecoregions_are_sorted_ints():
    row = admin.coerce_row("Acer rubrum", _payload(bloom_months=["4", 3, 3], native_ecoregions=[402, 7, 402]))
    assert row["bloom_months"] == [3, 4]
    assert row["native_ecoregions"] == [7, 402]


def test_floats_go_through_the_columns_own_width():
    row = admin.coerce_row("Acer rubrum", _payload(mature_height_max_ft="65.3"))
    assert row["mature_height_max_ft"] == pa.scalar(65.3, pa.float32()).as_py()


def test_is_complete_mirrors_the_pipelines_expression():
    complete = admin.coerce_row("Acer rubrum", _payload(
        is_evergreen=False, mature_height_max_ft=60, canopy_spread_max_ft=40, growth_rate="fast", drought_tolerance="moderate",
    ))
    assert complete["is_complete"] is True
    assert admin.coerce_row("Acer rubrum", _payload())["is_complete"] is False


@pytest.mark.parametrize(
    ("payload", "needle"),
    [
        (_payload(common_names=""), "common_names and tree_form are required"),
        (_payload(tree_form=None), "common_names and tree_form are required"),
        (_payload(tree_form="round"), "tree_form: must be one of"),
        (_payload(sun_exposure=["full_sun", "partial_shade"]), "sun_exposure: unknown value"),
        (_payload(root_behavior="non_invasive"), "root_behavior: must be one of"),
        (_payload(mature_height_min_ft=80, mature_height_max_ft=40), "greater than"),
        (_payload(usda_zone_min=0), "between 1 and 13"),
        (_payload(bloom_months=[13]), "between 1 and 12"),
        (_payload(lifespan_min_years="80.5"), "whole number"),
        (_payload(is_evergreen="maybe"), "true, false or blank"),
        (_payload(photo_url="ftp://x"), "http(s) URL"),
        (_payload(photo_url="https://example.com/a.jpg"), "needs a licence"),
        (_payload(photo_url="https://example.com/a.jpg", photo_license="gpl"), "photo_license: must be one of"),
    ],
)
def test_bad_values_are_named_not_guessed(payload, needle):
    with pytest.raises(admin.ValidationError) as exc:
        admin.coerce_row("Acer rubrum", payload)
    assert any(needle in p for p in exc.value.problems), exc.value.problems


def test_all_problems_are_reported_together():
    with pytest.raises(admin.ValidationError) as exc:
        admin.coerce_row("Acer rubrum", _payload(tree_form="round", growth_rate="quick"))
    assert len(exc.value.problems) == 2


# ── patching the table ─────────────────────────────────────────────────────────


def test_apply_edits_replaces_the_row_and_keeps_every_other_one():
    table = _table(_row("Acer rubrum"), _row("Quercus agrifolia", description="an oak"))
    edit = admin.coerce_row("Acer rubrum", _payload(description="a maple"))
    edit["enriched_at"] = NOW
    patched, summary = admin.apply_edits(table, {"Acer rubrum": edit})

    assert len(patched) == len(table)
    rows = {r["species"]: r for r in patched.to_pylist()}
    assert rows["Acer rubrum"]["description"] == "a maple"
    assert rows["Acer rubrum"]["common_names"] == ["Red maple", "Swamp maple"]
    assert rows["Quercus agrifolia"]["description"] == "an oak"
    assert summary == {"replaced": ["Acer rubrum"], "aliased": [], "appended": []}


def test_sentinel_rows_survive_a_patch_untouched():
    table = _table(_row("Acer rubrum"))
    edit = admin.coerce_row("Acer rubrum", _payload())
    edit["enriched_at"] = NOW
    patched, _ = admin.apply_edits(table, {"Acer rubrum": edit})
    rows = {r["species"]: r for r in patched.to_pylist()}
    for authored in sentinel_enrichment_rows():
        assert rows[authored["species"]]["common_names"] == authored["common_names"]
        assert rows[authored["species"]]["tree_form"] == authored["tree_form"]


def test_hybrid_twin_gets_the_same_edit_under_its_own_key():
    """Both spellings are in the table until the aliasing is retired; editing
    one and not the other would leave Paris and San Francisco disagreeing."""
    table = _table(_row("Acer x freemanii"), _row("Acer × freemanii"))
    edit = admin.coerce_row("Acer x freemanii", _payload(common_names="Freeman maple"))
    edit["enriched_at"] = NOW
    patched, summary = admin.apply_edits(table, {"Acer x freemanii": edit})
    rows = {r["species"]: r for r in patched.to_pylist()}
    assert rows["Acer x freemanii"]["common_names"] == ["Freeman maple"]
    assert rows["Acer × freemanii"]["common_names"] == ["Freeman maple"]
    assert rows["Acer × freemanii"]["species"] == "Acer × freemanii"
    assert summary["aliased"] == ["Acer × freemanii"]
    assert len(patched) == len(table)


def test_hybrid_without_a_twin_in_the_table_gets_one_on_publish():
    """The daily job would add the twin on its next load anyway; publishing
    it now means the join lands for both spellings straight away."""
    table = _table(_row("Acer x freemanii"))
    edit = admin.coerce_row("Acer x freemanii", _payload())
    edit["enriched_at"] = NOW
    patched, summary = admin.apply_edits(table, {"Acer x freemanii": edit})
    assert len(patched) == len(table) + 1
    assert summary["aliased"] == ["Acer × freemanii"]
    assert summary["appended"] == ["Acer × freemanii"]


def test_a_trunk_photo_needs_a_licence_and_an_http_url():
    with pytest.raises(admin.ValidationError, match="trunk photo needs a licence"):
        admin.coerce_row("Acer rubrum", _payload(trunk_photo_url="https://x/medium.jpg"))
    with pytest.raises(admin.ValidationError, match="trunk_photo_url must be an http"):
        admin.coerce_row("Acer rubrum", _payload(trunk_photo_url="ftp://x", trunk_photo_license="cc0"))
    row = admin.coerce_row("Acer rubrum", _payload(trunk_photo_url="https://x/medium.jpg", trunk_photo_license="cc0"))
    assert row["trunk_photo_url"] == "https://x/medium.jpg"
    # The two slots are independent: a trunk photo does not need a leaf one.
    assert row["photo_url"] is None


def test_common_names_are_staged_in_sentence_case():
    """The form follows the same rule as the run, so a reviewer typing
    'Evergreen Pear' sees what will be published."""
    row = admin.coerce_row("Pyrus kawakamii", _payload(common_names="EVERGREEN PEAR, Evergreen Pear, Callery Pear"))
    assert row["common_names"] == ["Evergreen pear", "Callery pear"]


def test_synonyms_are_validated_as_ingest_shaped_names():
    with pytest.raises(admin.ValidationError, match="not a species-rank"):
        admin.coerce_row("Acer rubrum", _payload(synonyms="acer rubrum var. drummondii"))
    with pytest.raises(admin.ValidationError, match="is this species"):
        admin.coerce_row("Acer rubrum", _payload(synonyms="Acer rubrum"))
    with pytest.raises(admin.ValidationError, match="sentinel"):
        admin.coerce_row("Acer rubrum", _payload(synonyms="Unknown"))
    row = admin.coerce_row("Acer rubrum", _payload(synonyms="Acer sanguineum, Acer carolinianum"))
    assert row["synonyms"] == ["Acer carolinianum", "Acer sanguineum"]


def test_adding_a_synonym_folds_the_duplicate_row_in_on_publish():
    """The merge: the duplicate's row is overwritten with this row's values
    and points back, so the old key keeps joining and the two stay in step."""
    table = _table(_row("Acer rubrum", description="the accepted row"),
                   _row("Acer sanguineum", description="a duplicate the LLM wrote"))
    edit = admin.coerce_row("Acer rubrum", _payload(synonyms="Acer sanguineum", description="the accepted row"))
    edit["enriched_at"] = NOW
    patched, summary = admin.apply_edits(table, {"Acer rubrum": edit})
    rows = {r["species"]: r for r in patched.to_pylist()}
    assert rows["Acer sanguineum"]["description"] == "the accepted row"
    assert rows["Acer sanguineum"]["synonyms"] == ["Acer rubrum"]
    assert rows["Acer rubrum"]["synonyms"] == ["Acer sanguineum"]
    assert summary["aliased"] == ["Acer sanguineum"]
    assert summary["appended"] == []
    assert len(patched) == len(table)


def test_a_new_synonym_with_no_row_gets_an_alias_row():
    table = _table(_row("Acer rubrum"))
    edit = admin.coerce_row("Acer rubrum", _payload(synonyms="Acer sanguineum"))
    edit["enriched_at"] = NOW
    patched, summary = admin.apply_edits(table, {"Acer rubrum": edit})
    rows = {r["species"]: r for r in patched.to_pylist()}
    assert rows["Acer sanguineum"]["common_names"] == rows["Acer rubrum"]["common_names"]
    assert summary["appended"] == ["Acer sanguineum"]
    assert len(patched) == len(table) + 1


def test_a_code_level_synonym_row_is_read_only(tmp_path):
    """The daily job rewrites it from the accepted row on every load, so an
    edit here would not survive; the form sends the reviewer to the accepted
    name instead."""
    state = _state(tmp_path, _row("Platanus x hispanica"), _row("Platanus x acerifolia"))
    with pytest.raises(admin.ValidationError, match="synonym of 'Platanus x hispanica'"):
        state.save("Platanus x acerifolia", _payload())
    assert state.detail("Platanus x acerifolia")["alias_of"] == "Platanus x hispanica"
    assert state.detail("Platanus x hispanica")["alias_of"] is None


def test_detail_reports_aliases_and_pending_merges(tmp_path):
    state = _state(tmp_path, _row("Acer rubrum"), _row("Acer sanguineum"))
    state.save("Acer rubrum", _payload(synonyms="Acer sanguineum, Acer carolinianum"))
    detail = state.detail("Acer rubrum")
    assert detail["aliases"] == ["Acer carolinianum", "Acer sanguineum"]
    assert detail["new_aliases"] == ["Acer carolinianum"]
    assert detail["merges"] == ["Acer sanguineum"]


def test_search_matches_a_synonym(tmp_path):
    state = _state(tmp_path, _row("Acer rubrum", synonyms=["Acer sanguineum"]), _row("Quercus rubra"))
    assert [s["species"] for s in state.search("sanguineum", "", 10)] == ["Acer rubrum"]


def test_a_species_that_lost_its_row_is_appended_and_reported():
    table = _table(_row("Acer rubrum"))
    edit = admin.coerce_row("Acer saccharum", _payload())
    edit["enriched_at"] = NOW
    patched, summary = admin.apply_edits(table, {"Acer saccharum": edit})
    assert len(patched) == len(table) + 1
    assert summary["appended"] == ["Acer saccharum"]


def test_patched_table_fits_the_schema_exactly():
    table = _table(_row("Acer rubrum"))
    edit = admin.coerce_row("Acer rubrum", _payload(mature_height_max_ft=60, bloom_months=[3, 4], native_ecoregions=[402]))
    edit["enriched_at"] = NOW
    patched, _ = admin.apply_edits(table, {"Acer rubrum": edit})
    assert patched.schema.equals(admin.SCHEMA)
    assert SPECIES_SENTINELS <= set(patched.column("species").to_pylist())


# ── state ──────────────────────────────────────────────────────────────────────


def _state(tmp_path, *rows) -> admin.AdminState:
    admin.EDITS_PATH = tmp_path / "edits.json"
    state = admin.AdminState()
    state.table = _table(*rows)
    state.rows = {r["species"]: r for r in state.table.to_pylist()}
    state.loaded_at = NOW
    return state


def test_saving_stages_stamps_enriched_at_and_persists(tmp_path):
    state = _state(tmp_path, _row("Acer rubrum"))
    detail = state.save("Acer rubrum", _payload(genus="Acer", description="a maple"))
    assert detail["pending"] is True
    assert detail["changed"] == ["common_names", "description"]
    assert state.edits["Acer rubrum"]["enriched_at"] > NOW
    assert admin.EDITS_PATH.exists()

    # A fresh process picks the staged edit back up.
    again = admin.AdminState()
    assert again.edits["Acer rubrum"]["description"] == "a maple"
    assert again.edits["Acer rubrum"]["enriched_at"].tzinfo is not None


def test_an_edit_staged_before_a_schema_addition_is_restored_with_the_new_columns(tmp_path):
    """The trunk photo columns landed while an edit was staged on disk; every
    reader indexes rows by name, so the first search after the upgrade failed
    on a KeyError until the restore filled the gap."""
    state = _state(tmp_path, _row("Acer rubrum"))
    state.save("Acer rubrum", _payload(description="a maple"))
    import json

    saved = json.loads(admin.EDITS_PATH.read_text(encoding="utf-8"))
    for name in ("trunk_photo_url", "trunk_photo_license", "trunk_photo_attribution", "synonyms"):
        del saved["Acer rubrum"][name]
    admin.EDITS_PATH.write_text(json.dumps(saved), encoding="utf-8")

    again = admin.AdminState()
    again.table, again.rows = state.table, state.rows
    assert again.edits["Acer rubrum"]["trunk_photo_url"] is None
    # ... and one staged before the sentence-case rule is brought under it.
    saved["Acer rubrum"]["common_names"] = ["RED MAPLE", "Swamp Maple"]
    admin.EDITS_PATH.write_text(json.dumps(saved), encoding="utf-8")
    assert admin.AdminState().edits["Acer rubrum"]["common_names"] == ["Red maple", "Swamp maple"]
    assert [s["species"] for s in again.search("", "notrunk", 10) if not s["sentinel"]] == ["Acer rubrum"]


def test_saving_the_published_values_stages_nothing(tmp_path):
    """Pressing save on an untouched form must not bump enriched_at for no reason."""
    state = _state(tmp_path, _row("Acer rubrum", common_names=["Red maple"], description="a maple"))
    detail = state.save("Acer rubrum", {**state.rows["Acer rubrum"]})
    assert detail["pending"] is False
    assert state.edits == {}
    assert not admin.EDITS_PATH.exists()


def test_sentinels_and_unknown_species_cannot_be_saved(tmp_path):
    state = _state(tmp_path, _row("Acer rubrum"))
    with pytest.raises(admin.ValidationError, match="sentinel"):
        state.save("Unknown", _payload())
    with pytest.raises(admin.ValidationError, match="no row"):
        state.save("Acer nope", _payload())


def test_the_list_orders_the_most_common_trees_first(tmp_path):
    """The default view is what to enrich next, and a city still publishing a
    synonym counts towards the accepted row it will join to."""
    state = _state(tmp_path, _row("Acer rubrum"), _row("Platanus x hispanica"), _row("Quercus rubra"))
    state.tree_counts = {
        "Acer rubrum": (5000, 3),
        "Platanus x hispanica": (52_919, 8),
        "Platanus x acerifolia": (157_823, 6),
        "Quercus rubra": (48_102, 9),
    }
    listed = [s for s in state.search("", "", 10) if not s["sentinel"]]
    assert [s["species"] for s in listed] == ["Platanus x hispanica", "Quercus rubra", "Acer rubrum"]
    assert listed[0]["trees"] == 52_919 + 157_823
    assert listed[0]["cities"] == 8
    assert state.detail("Platanus x hispanica")["trees"] == 52_919 + 157_823
    # A query still puts the exact and prefix matches ahead of the count.
    assert [s["species"] for s in state.search("acer", "", 10)][0] == "Acer rubrum"


def test_alias_rows_are_hidden_from_the_list_and_sentinels_come_last(tmp_path):
    state = _state(tmp_path, _row("Platanus x hispanica"), _row("Platanus x acerifolia"),
                   _row("Platanus × hispanica"), _row("Acer rubrum"))
    state.tree_counts = {"Platanus x hispanica": (10, 1), "Unknown": (1_000_000, 18), "Acer rubrum": (5, 1)}
    listed = [s["species"] for s in state.search("", "", 20)]
    assert listed[:2] == ["Platanus x hispanica", "Acer rubrum"]
    assert "Platanus x acerifolia" not in listed and "Platanus × hispanica" not in listed
    assert listed[-1] != "Platanus x hispanica" and "Unknown" in listed[2:]
    # Asking for the alias by name still finds it.
    assert [s["species"] for s in state.search("acerifolia", "", 5)] == ["Platanus x acerifolia"]


def test_the_list_can_be_sorted_by_name_recency_or_completeness(tmp_path):
    state = _state(
        tmp_path,
        _row("Quercus rubra", enriched_at=datetime(2026, 1, 1, tzinfo=timezone.utc), is_complete=True),
        _row("Acer rubrum", enriched_at=datetime(2026, 6, 1, tzinfo=timezone.utc), is_complete=True),
        _row("Zelkova serrata", enriched_at=None, is_complete=False),
    )
    state.tree_counts = {"Quercus rubra": (100, 2), "Acer rubrum": (300, 3), "Zelkova serrata": (200, 1)}
    real = lambda rows: [s["species"] for s in rows if not s["sentinel"]]  # noqa: E731
    assert real(state.search("", "", 10)) == ["Acer rubrum", "Zelkova serrata", "Quercus rubra"]
    assert real(state.search("", "", 10, "name")) == ["Acer rubrum", "Quercus rubra", "Zelkova serrata"]
    assert real(state.search("", "", 10, "enriched")) == ["Zelkova serrata", "Quercus rubra", "Acer rubrum"]
    assert real(state.search("", "", 10, "incomplete")) == ["Zelkova serrata", "Acer rubrum", "Quercus rubra"]
    with pytest.raises(admin.ValidationError, match="sort must be one of"):
        state.search("", "", 10, "colour")


def test_missing_counts_leave_the_list_alphabetical(tmp_path):
    state = _state(tmp_path, _row("Quercus rubra"), _row("Acer rubrum"))
    listed = [s["species"] for s in state.search("", "", 10) if s["species"] in ("Acer rubrum", "Quercus rubra")]
    assert listed == ["Acer rubrum", "Quercus rubra"]
    assert state.detail("Acer rubrum")["trees"] == 0


def test_search_overlays_staged_edits_and_filters(tmp_path):
    state = _state(tmp_path, _row("Acer rubrum"), _row("Quercus agrifolia", photo_url="https://x/medium.jpg", photo_license="cc0"))
    state.save("Acer rubrum", _payload(common_names="Red maple"))
    assert [s["species"] for s in state.search("red maple", "", 10)] == ["Acer rubrum"]
    assert [s["species"] for s in state.search("", "pending", 10)] == ["Acer rubrum"]
    assert [s["species"] for s in state.search("", "nophoto", 10) if not s["sentinel"]] == ["Acer rubrum"]
    assert state.search("acer rubrum", "", 10)[0]["pending"] is True


def test_discard_removes_the_staged_edit(tmp_path):
    state = _state(tmp_path, _row("Acer rubrum"))
    state.save("Acer rubrum", _payload(description="x"))
    state.discard("Acer rubrum")
    assert state.edits == {}
    assert state.detail("Acer rubrum")["pending"] is False


def test_publish_patches_the_live_table_not_the_loaded_one(tmp_path, monkeypatch):
    """The daily job may have appended species since this process loaded.
    Publishing the loaded snapshot would drop them; publishing a patch of the
    live table keeps them."""
    state = _state(tmp_path, _row("Acer rubrum"))
    state.save("Acer rubrum", _payload(description="a maple"))

    live = _table(_row("Acer rubrum"), _row("Betula nigra", description="added by the job"))
    uploaded: dict = {}
    monkeypatch.setattr(admin._te, "load_existing_table", lambda url: live)
    monkeypatch.setattr(admin._te, "upload_to_gcs", lambda path, uri: uploaded.update(path=path, uri=uri))
    monkeypatch.setattr(admin, "PUBLISH_PATH", tmp_path / "publish.parquet")

    summary = state.publish()

    import pyarrow.parquet as pq

    written = {r["species"]: r for r in pq.read_table(uploaded["path"]).to_pylist()}
    assert written["Betula nigra"]["description"] == "added by the job"
    assert written["Acer rubrum"]["description"] == "a maple"
    assert uploaded["uri"] == admin.ENRICHMENT_GCS_URI
    assert summary["replaced"] == ["Acer rubrum"]
    assert state.edits == {}
    assert state.rows["Betula nigra"]["description"] == "added by the job"


def test_publish_with_nothing_staged_is_refused(tmp_path):
    state = _state(tmp_path, _row("Acer rubrum"))
    with pytest.raises(admin.ValidationError, match="nothing to publish"):
        state.publish()


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

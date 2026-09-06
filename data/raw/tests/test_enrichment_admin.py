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
    assert summary == {"replaced": ["Acer rubrum"], "twinned": [], "appended": []}


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
    table = _table(_row("Platanus x hispanica"), _row("Platanus × hispanica"))
    edit = admin.coerce_row("Platanus x hispanica", _payload(common_names="London plane"))
    edit["enriched_at"] = NOW
    patched, summary = admin.apply_edits(table, {"Platanus x hispanica": edit})
    rows = {r["species"]: r for r in patched.to_pylist()}
    assert rows["Platanus x hispanica"]["common_names"] == ["London plane"]
    assert rows["Platanus × hispanica"]["common_names"] == ["London plane"]
    assert rows["Platanus × hispanica"]["species"] == "Platanus × hispanica"
    assert summary["twinned"] == ["Platanus × hispanica"]
    assert len(patched) == len(table)


def test_hybrid_without_a_twin_in_the_table_does_not_invent_one():
    table = _table(_row("Platanus x hispanica"))
    edit = admin.coerce_row("Platanus x hispanica", _payload())
    edit["enriched_at"] = NOW
    patched, summary = admin.apply_edits(table, {"Platanus x hispanica": edit})
    assert len(patched) == len(table)
    assert summary["twinned"] == []


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

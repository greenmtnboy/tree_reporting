"""The per-city manifests are the source of every shared city registry.

tools/sync_cities.py writes the registries from raw/{code}/city.toml; these
tests fail while a generated file disagrees with the manifests (someone edited
a generated region by hand, or added a manifest without rerunning the sync).
The downstream generators the sync runs have their own staleness tests
(test_city_derived.py, test_dedup_cells.py, the app-model and OSM-territory
checks), so only the sync's own outputs are compared here.
"""

import sys
from pathlib import Path

import pytest

RAW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW / "tools"))

import city_manifest  # noqa: E402
import sync_cities  # noqa: E402


@pytest.fixture(scope="module")
def cities():
    return city_manifest.load_all()


def test_every_city_directory_has_a_manifest_and_every_manifest_a_city(cities):
    with_model = {p.parent.name for p in RAW.glob("*/*_tree_info.preql")}
    with_manifest = {c.lc for c in cities}
    assert with_model - with_manifest == set(), "city directories without a city.toml"
    assert with_manifest - with_model == set(), "manifests without a city model"
    for c in cities:
        assert (RAW / c.lc / f"{c.slug}_tree_info.preql").exists(), f"{c.code}: slug {c.slug!r} names no model"


@pytest.mark.parametrize("path", [*sync_cities.WHOLE, *sync_cities.REGIONS], ids=lambda p: p.name)
def test_generated_files_match_the_manifests(cities, path):
    planned = sync_cities.planned(cities)
    on_disk = path.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert on_disk == planned[path], (
        f"{path.name} is stale against the city manifests: run "
        "`cd data/raw && uv run tools/sync_cities.py` (and never edit a "
        "`sync_cities:` region by hand -- edit the city's city.toml)"
    )


def test_a_manifest_round_trips(cities):
    """new_city.py writes manifests with city_manifest.dumps; what it writes
    must read back as what it was given."""
    for c in cities:
        assert city_manifest.dumps(c.data).startswith("#")


REGION = (
    "hand\n"
    "# >>> sync_cities: city jobs\n"
    "{body}"
    "# <<< sync_cities: city jobs\n"
    "{after}"
)


def conflict(ours: str, theirs: str, base: str | None = None) -> str:
    middle = f"||||||| base\n{base}" if base is not None else ""
    return f"<<<<<<< HEAD\n{ours}{middle}=======\n{theirs}>>>>>>> branch\n"


def test_resolve_takes_the_current_side_inside_a_generated_region():
    text = REGION.format(body=conflict("ours\n", "theirs\n"), after="tail\n")
    resolved, outside = sync_cities.resolve_text(text, whole=False)
    assert outside == []
    assert "ours\n" in resolved and "theirs" not in resolved and "<<<<<<<" not in resolved


def test_resolve_drops_a_diff3_base_section():
    text = REGION.format(body=conflict("ours\n", "theirs\n", base="base\n"), after="")
    resolved, outside = sync_cities.resolve_text(text, whole=False)
    assert outside == []
    assert "base" not in resolved.replace("sync_cities", "") and "|||||||" not in resolved


def test_resolve_refuses_a_conflict_in_hand_written_text():
    """A hand-edited schedule outside the generated jobs is somebody's change:
    taking the current side would silently drop the incoming one."""
    text = REGION.format(body="gen\n", after=conflict('schedule = "0 0 4 * * SUN"\n', 'schedule = "0 0 5 * * SUN"\n'))
    _, outside = sync_cities.resolve_text(text, whole=False)
    assert outside == [text.split("\n").index("<<<<<<< HEAD") + 1]


def test_resolve_refuses_a_conflict_that_crosses_a_marker():
    text = (
        "hand\n# >>> sync_cities: city jobs\ngen\n"
        + conflict("gen2\n# <<< sync_cities: city jobs\nhand2\n", "gen3\n# <<< sync_cities: city jobs\nhand3\n")
    )
    _, outside = sync_cities.resolve_text(text, whole=False)
    assert outside


def test_resolve_writes_nothing_when_any_conflict_is_outside(tmp_path, monkeypatch):
    good, bad = tmp_path / "good.preql", tmp_path / "bad.preql"
    good_text = REGION.format(body=conflict("ours\n", "theirs\n"), after="")
    bad_text = REGION.format(body="gen\n", after=conflict("mine\n", "yours\n"))
    good.write_text(good_text, encoding="utf-8")
    bad.write_text(bad_text, encoding="utf-8")
    monkeypatch.setattr(sync_cities, "REPO", tmp_path)
    monkeypatch.setattr(sync_cities, "WHOLE", {})
    monkeypatch.setattr(sync_cities, "GENERATED_WHOLE", ())
    monkeypatch.setattr(sync_cities, "GENERATED_REGIONS", ())
    monkeypatch.setattr(sync_cities, "REGIONS", {good: None, bad: None})
    with pytest.raises(SystemExit, match="bad.preql:"):
        sync_cities.resolve()
    assert good.read_text(encoding="utf-8") == good_text  # untouched, not half-resolved
    assert bad.read_text(encoding="utf-8") == bad_text


def test_the_manifests_never_reach_the_cloud_bundle():
    """Jobs read the generated files; the manifests are workstation input
    (~440 KB that the 2 MiB sync body cannot spare)."""
    import tomllib

    exclude = tomllib.loads((RAW.parent / "trilogy.toml").read_text(encoding="utf-8"))["cloud"]["exclude"]
    assert "raw/*/city.toml" in exclude

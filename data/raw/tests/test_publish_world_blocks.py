"""The world block publisher, offline: cutting projections into blocks, the
manifest, the guards that keep a bad run from replacing the live one, and the
contract with the map that reads the files.

The build functions take a DuckDB connection, so these drive them over small
synthetic projections. The network side (listing, signed upload, generation
preconditions, deletes) is exercised by a real dry run, not here.
"""

from __future__ import annotations

import math
import re
import sys
import tomllib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest

RAW = Path(__file__).resolve().parents[1]
DATA = RAW.parent
REPO = DATA.parent
sys.path.insert(0, str(DATA / "world"))
sys.path.insert(0, str(DATA / "website"))

import publish_blocks as pb  # noqa: E402
from publish_homepage import PublishError  # noqa: E402

AT = "2026-11-09T06:00:00+00:00"
BOSTON = (-71.0589, 42.3601)
BURLINGTON = (-73.2121, 44.4759)
CITIES = [
    {"code": "USBOS", "name": "Boston", "country": "United States", "coordinates": list(BOSTON)},
    {"code": "USBTV", "name": "Burlington, VT", "country": "United States", "coordinates": list(BURLINGTON)},
    {"code": "USSTO", "name": "Stoughton", "country": "United States", "coordinates": [-71.1, 42.12]},
    {"code": "USNEW", "name": "Newtown", "country": "United States", "coordinates": [-100.0, 40.0]},
]


def tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    """The reference Web Mercator tile of a point (the map's `lngLatToTile`)."""
    n = 2 ** zoom
    rad = math.radians(lat)
    return (
        int((lon + 180.0) / 360.0 * n),
        int((1.0 - math.log(math.tan(rad) + 1.0 / math.cos(rad)) / math.pi) / 2.0 * n),
    )


def block_key(lon: float, lat: float) -> int:
    x, y = tile(lon, lat, pb.BLOCK_ZOOM)
    return x * 2 ** pb.BLOCK_ZOOM + y


def write_projection(con, path: Path, code: str, center: tuple[float, float], rows: int, *, spread=0.2,
                     species=("Acer rubrum", "Quercus palustris"), drop: tuple[str, ...] = (), extra: str = "") -> str:
    """A browser projection (tree_browser_city.preql's columns) of `rows` trees around `center`."""
    lon, lat = center
    columns = {
        "tree_id": f"'{code.lower()}-' || i",
        "city": f"'{code}'",
        "data_source": f"'{code}_OPENDATA'",
        "species": f"[{', '.join(repr(s) for s in species)}][1 + i % {len(species)}]",
        "cultivar": "CASE WHEN i % 7 = 0 THEN 'Armstrong' END",
        "tree_name": "CASE WHEN i % 3 = 0 THEN 'Street tree' END",
        "display_name": f"[{', '.join(repr(s + ' (common)') for s in species)}][1 + i % {len(species)}]",
        "tree_form": "'broadleaf'",
        "plant_date": "CAST(NULL AS DATE)",
        "diameter_at_breast_height": "CAST(4 + i % 30 AS DOUBLE)",
        "crown_width_m": "CAST(NULL AS DOUBLE)",
        "predicted_crown_width_m": "CAST(3 + i % 9 AS DOUBLE) + 0.123456789",
        "height_m": "CAST(NULL AS DOUBLE)",
        "predicted_height_m": "CAST(6 + i % 12 AS DOUBLE)",
        "latitude": f"{lat} + ((i * 7919) % 1000 - 500) / 1000.0 * {spread}",
        "longitude": f"{lon} + ((i * 104729) % 1000 - 500) / 1000.0 * {spread}",
        "submission_photo_url": "CAST(NULL AS VARCHAR)",
        "prediction_inputs_published_through": "TIMESTAMPTZ '2026-11-09 06:00:00+00'",
    }
    select = ", ".join(f"{expr} AS {name}" for name, expr in columns.items() if name not in drop)
    con.execute(f"COPY (SELECT {select} FROM range({rows}) t(i) {extra}) TO '{path.as_posix()}' (FORMAT parquet)")
    return path.as_posix()


@pytest.fixture()
def con():
    connection = duckdb.connect()
    yield connection
    connection.close()


def build(con, tmp_path: Path, sources: dict[str, str], name="out") -> tuple[dict, Path]:
    out = tmp_path / name
    pb.load_rows(con, sources)
    pb.build_world(con)
    blocks, vocab = pb.write_files(con, out)
    manifest = pb.build_manifest(CITIES, blocks, vocab, {code: 1 for code in sources}, AT)
    pb.validate(manifest, None)
    return manifest, out


@pytest.fixture()
def world(con, tmp_path):
    sources = {
        "USBOS": write_projection(con, tmp_path / "usbos.parquet", "USBOS", BOSTON, 5000),
        "USBTV": write_projection(con, tmp_path / "usbtv.parquet", "USBTV", BURLINGTON, 800, spread=0.05,
                                  species=("Ulmus americana", "Acer saccharum")),
    }
    manifest, out = build(con, tmp_path, sources)
    return manifest, out, sources


# ---------------------------------------------------------------------------
# The cut
# ---------------------------------------------------------------------------


def test_every_tree_lands_in_the_block_of_its_own_position(con, world):
    manifest, out, _ = world
    assert manifest["trees"] == 5800 == sum(b[1] for b in manifest["blocks"])
    for key, rows, digest, _cities in manifest["blocks"]:
        path = out / pb.block_name(key, digest)
        positions = con.execute(f"SELECT longitude, latitude FROM read_parquet('{path.as_posix()}')").fetchall()
        assert len(positions) == rows
        assert {block_key(lon, lat) for lon, lat in positions} == {key}


def test_a_block_is_the_projection_columns_without_the_watermark(con, world):
    manifest, out, _ = world
    key, _rows, digest, _ = manifest["blocks"][0]
    described = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{(out / pb.block_name(key, digest)).as_posix()}')").fetchall()
    assert [(name, kind) for name, kind, *_ in described] == list(pb.COLUMNS)
    assert manifest["columns"] == [name for name, _ in pb.COLUMNS]
    assert "prediction_inputs_published_through" not in manifest["columns"]


def test_blocks_from_different_inventories_sit_side_by_side_and_the_manifest_says_whose(world):
    manifest, _, _ = world
    codes = [c["code"] for c in manifest["cities"]]
    # Registry order, and only cities with trees: the unpublished one is not a bookmark yet.
    assert codes == ["USBOS", "USBTV"]
    assert {c["code"]: c["trees"] for c in manifest["cities"]} == {"USBOS": 5000, "USBTV": 800}
    by_key = {b[0]: b for b in manifest["blocks"]}
    assert by_key[block_key(*BOSTON)][3][0] == codes.index("USBOS")
    assert by_key[block_key(*BURLINGTON)][3][0] == codes.index("USBTV")
    for city in manifest["cities"]:
        assert city["blocks"] == sum(1 for b in manifest["blocks"] if codes.index(city["code"]) in b[3][0::2])


def test_rows_are_in_cell_order_so_a_phone_cell_is_one_run(con, world):
    manifest, out, _ = world
    key, _rows, digest, _ = max(manifest["blocks"], key=lambda b: b[1])
    path = (out / pb.block_name(key, digest)).as_posix()
    x, y = pb.tile_sql("longitude", "latitude", 14)
    cells = [row[0] for row in con.execute(
        f"SELECT {x} * 16384 + {y} FROM read_parquet('{path}', file_row_number = true) ORDER BY file_row_number"
    ).fetchall()]
    runs = 1 + sum(1 for a, b in zip(cells, cells[1:]) if a != b)
    assert runs == len(set(cells)), "a z14 cell's rows are split across the file"


def test_a_tree_two_cities_publish_is_written_once(con, tmp_path):
    boston = write_projection(con, tmp_path / "usbos.parquet", "USBOS", BOSTON, 100, spread=0.01)
    neighbour = tmp_path / "ussto.parquet"
    con.execute(
        f"COPY (SELECT * REPLACE ('USSTO' AS city) FROM read_parquet('{boston}') WHERE tree_id IN ('usbos-1', 'usbos-2')) "
        f"TO '{neighbour.as_posix()}' (FORMAT parquet)"
    )
    manifest, out = build(con, tmp_path, {"USBOS": boston, "USSTO": neighbour.as_posix()})
    assert manifest["trees"] == 100
    files = ", ".join(f"'{(out / pb.block_name(b[0], b[2])).as_posix()}'" for b in manifest["blocks"])
    kept = con.execute(f"SELECT tree_id, city FROM read_parquet([{files}]) WHERE tree_id IN ('usbos-1', 'usbos-2')").fetchall()
    # The first city by code keeps it, so a rebuild keeps the same row.
    assert sorted(kept) == [("usbos-1", "USBOS"), ("usbos-2", "USBOS")]
    assert [c["code"] for c in manifest["cities"]] == ["USBOS"]


def test_the_vocabulary_holds_every_value_of_every_enum_column(con, world):
    manifest, out, _ = world
    files = ", ".join(f"'{(out / pb.block_name(b[0], b[2])).as_posix()}'" for b in manifest["blocks"])
    vocab = (out / manifest["vocab"]).as_posix()
    for column in pb.VOCAB_COLUMNS:
        in_blocks = {row[0] for row in con.execute(
            f"SELECT DISTINCT {column} FROM read_parquet([{files}]) WHERE {column} IS NOT NULL").fetchall()}
        in_vocab = {row[0] for row in con.execute(
            f"SELECT value FROM read_parquet('{vocab}') WHERE kind = '{column}'").fetchall()}
        assert in_vocab == in_blocks, column
    assert "Ulmus americana" in {row[0] for row in con.execute(
        f"SELECT value FROM read_parquet('{vocab}') WHERE kind = 'species'").fetchall()}


def test_a_rebuild_of_the_same_trees_names_every_file_the_same(con, tmp_path, world):
    manifest, _, sources = world
    again, _ = build(con, tmp_path, sources, name="again")
    assert again["blocks"] == manifest["blocks"]
    assert again["vocab"] == manifest["vocab"]
    assert pb.content_of(again) == pb.content_of(manifest)


def test_one_changed_tree_renames_only_its_block(con, tmp_path, world):
    manifest, _, sources = world
    moved = tmp_path / "usbtv-edited.parquet"
    con.execute(
        f"COPY (SELECT * REPLACE (CASE WHEN tree_id = 'usbtv-0' THEN 99.0 ELSE diameter_at_breast_height END "
        f"AS diameter_at_breast_height) FROM read_parquet('{sources['USBTV']}')) TO '{moved.as_posix()}' (FORMAT parquet)"
    )
    edited, _ = build(con, tmp_path, {**sources, "USBTV": moved.as_posix()}, name="edited")
    changed = [a[0] for a, b in zip(manifest["blocks"], edited["blocks"]) if a != b]
    assert len(changed) == 1
    assert edited["vocab"] == manifest["vocab"]


def test_a_projection_built_before_a_column_existed_reads_it_as_null(con, tmp_path):
    older = write_projection(con, tmp_path / "usbos.parquet", "USBOS", BOSTON, 50, spread=0.01,
                             drop=("height_m", "predicted_height_m", "cultivar"))
    manifest, out = build(con, tmp_path, {"USBOS": older})
    files = ", ".join(f"'{(out / pb.block_name(b[0], b[2])).as_posix()}'" for b in manifest["blocks"])
    assert con.execute(
        f"SELECT count(*), count(height_m), count(cultivar) FROM read_parquet([{files}])").fetchone() == (50, 0, 0)


def test_a_projection_missing_a_required_column_fails_the_run(con, tmp_path):
    broken = write_projection(con, tmp_path / "usbos.parquet", "USBOS", BOSTON, 10, drop=("display_name",))
    with pytest.raises(PublishError, match="display_name"):
        pb.load_rows(con, {"USBOS": broken})


def test_rows_without_a_position_are_left_out(con, tmp_path):
    path = write_projection(con, tmp_path / "usbos.parquet", "USBOS", BOSTON, 20, spread=0.01)
    holed = tmp_path / "holed.parquet"
    con.execute(
        f"COPY (SELECT * REPLACE (CASE WHEN tree_id = 'usbos-3' THEN NULL ELSE latitude END AS latitude) "
        f"FROM read_parquet('{path}')) TO '{holed.as_posix()}' (FORMAT parquet)"
    )
    manifest, _ = build(con, tmp_path, {"USBOS": holed.as_posix()})
    assert manifest["trees"] == 19


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


def test_a_candidate_that_loses_a_quarter_of_the_published_trees_is_refused(world):
    manifest, _, _ = world
    live = {**manifest, "trees": int(manifest["trees"] / 0.74)}
    with pytest.raises(PublishError, match="under 75%"):
        pb.validate(manifest, live)
    pb.validate(manifest, live, force=True)
    # A shrink inside the ratio is a day's churn.
    pb.validate(manifest, {**manifest, "trees": int(manifest["trees"] / 0.8)})


def test_a_published_city_missing_from_the_candidate_is_refused(world):
    manifest, _, _ = world
    live = {**manifest, "cities": [*manifest["cities"], {"code": "USSTO", "trees": 1, "blocks": 1}]}
    with pytest.raises(PublishError, match="USSTO"):
        pb.validate(manifest, live)


def test_a_manifest_that_does_not_add_up_is_refused(world):
    manifest, _, _ = world
    with pytest.raises(PublishError, match="add up"):
        pb.validate({**manifest, "trees": manifest["trees"] + 1}, None)
    with pytest.raises(PublishError, match="no blocks"):
        pb.validate({**manifest, "blocks": []}, None)
    doubled = {**manifest, "blocks": [*manifest["blocks"], manifest["blocks"][0]]}
    with pytest.raises(PublishError, match="twice"):
        pb.validate(doubled, None)


def test_rows_of_a_city_the_registry_does_not_list_fail_the_run(con, tmp_path):
    stray = write_projection(con, tmp_path / "xxabc.parquet", "XXABC", BOSTON, 5, spread=0.01)
    pb.load_rows(con, {"XXABC": stray})
    pb.build_world(con)
    blocks, vocab = pb.write_files(con, tmp_path / "out")
    with pytest.raises(PublishError, match="XXABC"):
        pb.build_manifest(CITIES, blocks, vocab, {}, AT)


def test_an_unchanged_world_is_not_a_new_manifest(world):
    manifest, _, _ = world
    later = {**manifest, "generated_at": "2026-11-10T06:00:00+00:00",
             "cities": [{**c, "generation": 99} for c in manifest["cities"]]}
    assert pb.content_of(later) == pb.content_of(manifest)
    assert pb.content_of({**manifest, "trees": 1}) != pb.content_of(manifest)


def test_only_files_no_manifest_names_and_older_than_the_retention_are_deleted(world):
    manifest, _, _ = world
    now = datetime(2026, 11, 9, 6, tzinfo=timezone.utc)
    old = now - timedelta(days=pb.RETAIN_DAYS + 1)
    kept_name = pb.block_name(manifest["blocks"][0][0], manifest["blocks"][0][2])
    listed = {
        f"{pb.PREFIX}/{kept_name}": {"generation": 1, "modified": old},
        f"{pb.PREFIX}/{manifest['vocab']}": {"generation": 2, "modified": old},
        f"{pb.PREFIX}/blocks/1-0000000000000000.parquet": {"generation": 3, "modified": old},
        f"{pb.PREFIX}/blocks/2-1111111111111111.parquet": {"generation": 4, "modified": now - timedelta(hours=5)},
        f"{pb.PREFIX}/vocab-2222222222222222.parquet": {"generation": 5, "modified": old},
        f"{pb.PREFIX}/manifest.json": {"generation": 6, "modified": old},
        f"{pb.PREFIX}/notes.txt": {"generation": 7, "modified": old},
    }
    assert pb.stale_objects(listed, pb.referenced(manifest), now) == [
        f"{pb.PREFIX}/blocks/1-0000000000000000.parquet",
        f"{pb.PREFIX}/vocab-2222222222222222.parquet",
    ]
    # The manifest being replaced keeps its files too: a tab that opened on it is still reading them.
    previous = {"blocks": [[1, 5, "0000000000000000", [0, 5]]], "vocab": "vocab-2222222222222222.parquet"}
    assert pb.stale_objects(listed, pb.referenced(manifest) | pb.referenced(previous), now) == []
    assert pb.referenced(None) == set()


# ---------------------------------------------------------------------------
# The contract with the map, and the job
# ---------------------------------------------------------------------------


def frontend(path: str) -> str:
    return (REPO / "src" / "src" / path).read_text(encoding="utf-8")


def test_the_block_columns_are_the_ones_the_worker_reads():
    source = frontend("workers/cityTables.ts")
    listed = re.search(r"export const WORLD_BLOCK_COLUMNS = \[(.*?)\] as const", source, re.S).group(1)
    assert re.findall(r"'([a-z_]+)'", listed) == [name for name, _ in pb.COLUMNS]
    kinds = re.search(r"export const VOCAB_KINDS[^=]*= \{(.*?)\n\}", source, re.S).group(1)
    assert sorted(re.findall(r": '([a-z_]+)'", kinds)) == sorted(pb.VOCAB_COLUMNS)


def test_the_block_grid_is_the_maps_chunk_grid():
    assert f"export const BLOCK_ZOOM = {pb.BLOCK_ZOOM}" in frontend("workers/worldBlocks.ts")
    assert f"export const CHUNK_ZOOM = {pb.BLOCK_ZOOM}" in frontend("workers/cityPoints.ts")
    assert f"export const WORLD_MANIFEST_FORMAT = {pb.FORMAT}" in frontend("workers/worldBlocks.ts")
    assert f"export const TILE_INDEX_ZOOM = {pb.INDEX_ZOOM}" in frontend("workers/cityTables.ts")
    # Where the map looks for the manifest is where the job writes it.
    assert pb.PREFIX.endswith("/world/v1") and "/world/v1`" in frontend("workers/parquetUrls.ts")


def test_the_tile_of_a_point_matches_the_maps(con):
    for lon, lat in [BOSTON, BURLINGTON, (-122.4194, 37.7749), (144.9631, -37.8136), (-58.3816, -34.6037), (139.69, 35.68)]:
        for zoom in (pb.BLOCK_ZOOM, 14, pb.INDEX_ZOOM):
            x, y = pb.tile_sql(str(lon), str(lat), zoom)
            assert con.execute(f"SELECT {x}, {y}").fetchone() == tile(lon, lat, zoom)


def test_the_job_runs_in_the_cores_tick_every_day():
    """The projections are each city's own job's, on its own cron; the blocks
    take whatever is published, once a day after the rollup, whatever moved."""
    config = tomllib.loads((DATA / "trilogy.toml").read_text(encoding="utf-8"))
    jobs = {job["key"]: job for job in config["cloud"]["job"]}
    job = jobs["publish-world-blocks"]
    assert job["operation"] == "run"
    assert job["entrypoint"] == "world_tree_blocks.preql"
    assert job["schedule"] == jobs["publish-full"]["schedule"], "one cron, so the platform orders one tick"
    edge = config["dependencies"][job["entrypoint"]]
    assert jobs["publish-full"]["entrypoint"] in edge["after"]
    assert edge["when"] == "always", "a day the rollup did not move must still cut the blocks and clean up"
    statements = (DATA / job["entrypoint"]).read_text(encoding="utf-8")
    assert re.search(r"^call `\./world/publish_blocks\.py`;", statements, re.M)
    assert (DATA / "world" / "publish_blocks.py").exists()

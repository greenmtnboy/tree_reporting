#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb>=1.4,<2"]
# ///

"""Publish the map's trees as geo blocks: one parquet per Web Mercator z12 tile.

    gs://trilogy_public_models/duckdb/trees/world/v1/manifest.json
    gs://trilogy_public_models/duckdb/trees/world/v1/blocks/{key}-{hash}.parquet
    gs://trilogy_public_models/duckdb/trees/world/v1/vocab-{hash}.parquet

Run by the `publish-world-blocks` [[cloud.job]] (`world_tree_blocks.preql`
`call`s this script) after the browser projections. The map reads these and
nothing city-shaped: it fetches the manifest, then the blocks under the camera
(docs/SEAMLESS_WORLD.md), so panning from one inventory into the next needs no
city switch, and a city is a bookmark.

What it reads: **every city's browser projection** (`{code}_tree_browser`,
data/raw/city_derived.preql, one `derived-{code}` job each), each at the generation a listing returned
at the start of the run. No portal, no rollup, no rebuild. The projections
already carry the display name, the growth form and the predictions, so a
block is the same row shape cut along a different line.

What it writes:

- **Blocks.** A block is the z12 tile `key = x * 4096 + y`, the grid the
  desktop's point chunks already use (src/src/workers/cityPoints.ts), and
  holds every tree in it whichever city published it. Rows are in Morton
  order of their z20 tile, so a phone's z14 cell is a contiguous run. A tree
  two neighbouring inventories both publish (an OSM id inside both
  territories) is written once.
- **The vocabulary.** Every distinct value of the string columns the worker
  stores as ENUMs, one file for the world (a few hundred KB).
- **The manifest.** The list of blocks with their row counts and cities, the
  vocabulary's name, and each city's tree count. It is the commit point.

**Blocks and the vocabulary are content-addressed** (the name carries a hash
of the bytes) and immutable, so a manifest never points at a file from
another build, a browser may cache a block for a year, and a block whose
trees did not change is not uploaded again. The manifest is written last,
with `x-goog-if-generation-match` on the generation this run read. Files no
manifest still names are deleted once they are RETAIN_DAYS old, which is
longer than any open tab's manifest stays in use.

**A run that would lose a quarter of the published trees, or a city that was
published, is refused** and the live manifest keeps serving (the same ratio
`@kept` holds every city parquet to; never a row floor).

Locally, against the published projections, writing nothing to the bucket:

    uv run world/publish_blocks.py --dry-run --output-dir /tmp/world
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen
from xml.etree import ElementTree

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "website"))

from publish_homepage import (  # noqa: E402
    BUCKET, DATA_VERSION, PUBLIC_HOST, TREES_BASE, HmacClient, PublishError, head_generation, load_cities,
    now_iso, pinned,
)

FORMAT = 1
PREFIX = "duckdb/trees/world/v1"
MANIFEST = f"{PREFIX}/manifest.json"
#: The block grid: Web Mercator tiles at this zoom. The frontend's point
#: chunks are the same tiles (CHUNK_ZOOM in src/src/workers/cityPoints.ts);
#: test_publish_world_blocks.py keeps the two in step.
BLOCK_ZOOM = 12
#: The zoom rows are indexed at before they are cut into blocks.
INDEX_ZOOM = 20
#: 10,240 as the projections are written, for the same reason: a phone's
#: window filters on a cell and DuckDB skips the groups outside it.
ROW_GROUP_SIZE = 10_240
#: A candidate with fewer trees than this share of the live manifest's is
#: refused (`--force` overrides it, for a deliberate shrink).
KEPT_RATIO = 0.75
#: How long a block no manifest names is kept. A tab holds the manifest it
#: opened with; it re-reads it when a block it names is gone.
RETAIN_DAYS = 3
#: DuckDB's memory limit for the build; the job's container has 4 GiB.
DEFAULT_MEMORY_LIMIT = "2500MB"
MANIFEST_CACHE_CONTROL = "public,max-age=300"
IMMUTABLE_CACHE_CONTROL = "public,max-age=31536000,immutable"

#: The columns of a block, in order: the projection's, less its watermark.
#: `test_publish_world_blocks.py` holds the worker's read to the same list.
COLUMNS = (
    ("tree_id", "VARCHAR"),
    ("city", "VARCHAR"),
    ("data_source", "VARCHAR"),
    ("species", "VARCHAR"),
    ("cultivar", "VARCHAR"),
    ("tree_name", "VARCHAR"),
    ("display_name", "VARCHAR"),
    ("tree_form", "VARCHAR"),
    ("plant_date", "DATE"),
    ("diameter_at_breast_height", "DOUBLE"),
    ("crown_width_m", "DOUBLE"),
    # The worker holds these three as FLOAT; a double's low bits are noise
    # that does not compress.
    ("predicted_crown_width_m", "FLOAT"),
    ("height_m", "FLOAT"),
    ("predicted_height_m", "FLOAT"),
    ("latitude", "DOUBLE"),
    ("longitude", "DOUBLE"),
    ("submission_photo_url", "VARCHAR"),
)
#: Columns a projection built before they existed may lack; read as null.
OPTIONAL_COLUMNS = {"cultivar", "crown_width_m", "height_m", "predicted_height_m", "submission_photo_url"}
#: The string columns the worker stores as ENUMs (CITY_ENUM_TYPES in
#: src/src/workers/cityTables.ts), as `kind` in the vocabulary file.
VOCAB_COLUMNS = ("city", "data_source", "species", "cultivar", "tree_name", "display_name")


def log(message: str) -> None:
    print(f"[world-blocks] {message}", flush=True)


def projection_url(code: str) -> str:
    return f"{TREES_BASE}/{code.lower()}_tree_browser_v{DATA_VERSION}.parquet"


def block_name(key: int, digest: str) -> str:
    return f"blocks/{key}-{digest}.parquet"


def vocab_name(digest: str) -> str:
    return f"vocab-{digest}.parquet"


# ---------------------------------------------------------------------------
# GCS
# ---------------------------------------------------------------------------


class Bucket:
    """The listing, upload and delete this job needs, over the homepage
    publisher's signed XML client."""

    def __init__(self, client: HmacClient):
        self.client = client

    def listing(self, prefix: str) -> dict[str, dict]:
        """`{object name: {generation, modified}}` under `prefix`."""
        found: dict[str, dict] = {}
        marker = ""
        while True:
            query = {"prefix": prefix, **({"marker": marker} if marker else {})}
            try:
                with urlopen(self.client._signed("GET", f"/{BUCKET}", {}, b"", query), timeout=60) as resp:
                    root = ElementTree.fromstring(resp.read())
            except HTTPError as exc:
                raise PublishError(f"listing gs://{BUCKET}/{prefix}: HTTP {exc.code} {exc.read()[:300]!r}") from exc
            ns = {"s3": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
            tag = (lambda t: f"s3:{t}") if ns else (lambda t: t)
            for item in root.findall(tag("Contents"), ns):
                name = item.findtext(tag("Key"), namespaces=ns)
                found[name] = {
                    "generation": int(item.findtext(tag("Generation"), namespaces=ns)),
                    "modified": datetime.fromisoformat(
                        item.findtext(tag("LastModified"), namespaces=ns).replace("Z", "+00:00")
                    ),
                }
                marker = name
            if (root.findtext(tag("IsTruncated"), namespaces=ns) or "false").lower() != "true":
                return found

    def put(self, name: str, body: bytes, *, content_type: str, cache_control: str,
            if_generation_match: int | None = None) -> int:
        headers = {"content-type": content_type, "cache-control": cache_control}
        if if_generation_match is not None:
            headers["x-goog-if-generation-match"] = str(if_generation_match)
        try:
            with urlopen(self.client._signed("PUT", f"/{BUCKET}/{name}", headers, body), timeout=300) as resp:
                return int(resp.headers["x-goog-generation"])
        except HTTPError as exc:
            if exc.code == 412:
                raise PublishError(
                    f"gs://{BUCKET}/{name} moved past generation {if_generation_match} during this run; "
                    "another publisher won, so this run's candidate is dropped"
                ) from exc
            raise PublishError(f"writing gs://{BUCKET}/{name}: HTTP {exc.code} {exc.read()[:300]!r}") from exc

    def delete(self, name: str, generation: int) -> None:
        """Delete exactly the generation that was listed; an object replaced since is left."""
        try:
            with urlopen(self.client._signed("DELETE", f"/{BUCKET}/{name}", {}, b"", {"generation": str(generation)}),
                         timeout=60):
                pass
        except HTTPError as exc:
            if exc.code != 404:
                raise PublishError(f"deleting gs://{BUCKET}/{name}: HTTP {exc.code} {exc.read()[:300]!r}") from exc


def read_live(bucket: Bucket | None, listed: dict[str, dict]) -> tuple[dict | None, int]:
    """The published manifest and its generation; (None, 0) before the first publish."""
    url = f"{PUBLIC_HOST}/{BUCKET}/{MANIFEST}"
    generation = listed[MANIFEST]["generation"] if MANIFEST in listed else (None if bucket else head_generation(url))
    if not generation:
        return None, 0
    with urlopen(pinned(url, generation), timeout=60) as resp:
        return json.loads(resp.read()), generation


# ---------------------------------------------------------------------------
# Build
# ---------------------------------------------------------------------------


def tile_sql(lon: str, lat: str, zoom: int) -> tuple[str, str]:
    """A point's Web Mercator tile at `zoom`, as the worker computes it
    (cityShape in src/src/workers/cityTables.ts)."""
    n = 2 ** zoom
    clamped = f"LEAST(GREATEST({lat}, -85.05112878), 85.05112878)"
    x = f"LEAST({n - 1}, GREATEST(0, CAST(floor((({lon} + 180.0) / 360.0) * {n}) AS INTEGER)))"
    y = (f"LEAST({n - 1}, GREATEST(0, CAST(floor((1.0 - ln(tan(radians({clamped})) + 1.0 / cos(radians({clamped}))) "
         f"/ pi()) / 2.0 * {n}) AS INTEGER)))")
    return x, y


def morton_sql(x: str, y: str, bits: int) -> str:
    """The low `bits` of x and y interleaved: a z-order key within a block."""
    return " | ".join(
        f"((({x} >> {i}) & 1) << {2 * i}) | ((({y} >> {i}) & 1) << {2 * i + 1})" for i in range(bits)
    )


def connect(workdir: Path):
    import duckdb

    con = duckdb.connect(str(workdir / "world.duckdb"))
    con.execute("INSTALL httpfs; LOAD httpfs;")
    con.execute(f"SET temp_directory = '{(workdir / 'tmp').as_posix()}'")
    # DuckDB sizes itself to the host's memory, not the container's: without a
    # limit the sort over the world's rows is what gets the job killed. Past
    # the limit it spills to the temp directory above.
    con.execute(f"SET memory_limit = '{os.environ.get('WORLD_BLOCKS_MEMORY_LIMIT', DEFAULT_MEMORY_LIMIT)}'")
    return con


def load_rows(con, sources: dict[str, str]) -> dict[str, int]:
    """Every source's positioned rows into `staged`, with their z20 tile.
    Returns the rows read per city code."""
    x, y = tile_sql("longitude", "latitude", INDEX_ZOOM)
    con.execute(
        "CREATE OR REPLACE TABLE staged ("
        + ", ".join(f"{name} {kind}" for name, kind in COLUMNS)
        + ", xt INTEGER, yt INTEGER)"
    )
    read: dict[str, int] = {}
    for code, source in sources.items():
        have = {row[0] for row in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{source}')").fetchall()}
        missing = [name for name, _ in COLUMNS if name not in have and name not in OPTIONAL_COLUMNS]
        if missing:
            raise PublishError(f"{code}: projection lacks {missing}")
        select = ", ".join(
            f"CAST({name if name in have else 'NULL'} AS {kind}) AS {name}" for name, kind in COLUMNS
        )
        con.execute(
            f"INSERT INTO staged SELECT {select}, {x} AS xt, {y} AS yt FROM read_parquet('{source}') "
            "WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND tree_id IS NOT NULL"
        )
        read[code] = con.execute("SELECT count(*) FROM staged WHERE city = ?", [code]).fetchone()[0]
    return read


def build_world(con) -> None:
    """`world`: one row per tree id, keyed and ordered by block.

    A tree id two cities publish is one tree (an OpenStreetMap node inside
    two territories); the first city by code keeps it, so a rebuild keeps
    the same one."""
    shift = INDEX_ZOOM - BLOCK_ZOOM
    per_axis = 2 ** BLOCK_ZOOM
    names = ", ".join(name for name, _ in COLUMNS)
    con.execute(f"""
        CREATE OR REPLACE TABLE world AS
        SELECT {names},
            (xt >> {shift}) * {per_axis} + (yt >> {shift}) AS block,
            {morton_sql('xt', 'yt', shift)} AS morton
        FROM staged
        QUALIFY row_number() OVER (PARTITION BY tree_id ORDER BY city, latitude, longitude) = 1
        ORDER BY block, morton, tree_id
    """)
    con.execute("DROP TABLE staged")


def write_files(con, out: Path) -> tuple[list[dict], str]:
    """The block files and the vocabulary into `out`, named by content.
    Returns the blocks (`key`, `rows`, `hash`, `cities`) and the vocabulary's name."""
    (out / "blocks").mkdir(parents=True, exist_ok=True)
    names = ", ".join(name for name, _ in COLUMNS)
    options = f"FORMAT parquet, COMPRESSION zstd, ROW_GROUP_SIZE {ROW_GROUP_SIZE}"

    by_block: dict[int, dict] = {}
    for key, city, rows in con.execute(
        "SELECT block, city, count(*) FROM world GROUP BY 1, 2 ORDER BY 1, 2"
    ).fetchall():
        entry = by_block.setdefault(key, {"key": key, "rows": 0, "cities": {}})
        entry["rows"] += rows
        entry["cities"][city] = rows

    scratch = out / "blocks" / "_writing.parquet"
    for key, entry in by_block.items():
        con.execute(
            f"COPY (SELECT {names} FROM world WHERE block = {key} ORDER BY morton, tree_id) "
            f"TO '{scratch.as_posix()}' ({options})"
        )
        entry["hash"] = hashlib.sha256(scratch.read_bytes()).hexdigest()[:16]
        scratch.replace(out / block_name(key, entry["hash"]))

    vocab_scratch = out / "_vocab.parquet"
    union = " UNION ALL ".join(
        f"SELECT '{column}' AS kind, {column} AS value FROM world WHERE {column} IS NOT NULL GROUP BY 2"
        for column in VOCAB_COLUMNS
    )
    con.execute(f"COPY (SELECT kind, value FROM ({union}) ORDER BY kind, value) TO '{vocab_scratch.as_posix()}' ({options})")
    vocab = vocab_name(hashlib.sha256(vocab_scratch.read_bytes()).hexdigest()[:16])
    vocab_scratch.replace(out / vocab)
    return list(by_block.values()), vocab


def build_manifest(cities: list[dict], blocks: list[dict], vocab: str, inputs: dict[str, int | None],
                   generated_at: str) -> dict:
    """The manifest: what the map fetches first.

    `blocks` rows are `[key, rows, hash, [city index, rows, ...]]`, the city
    index into `cities`; a block's file is `blocks/{key}-{hash}.parquet`."""
    trees: dict[str, int] = {}
    count: dict[str, int] = {}
    for block in blocks:
        for code, rows in block["cities"].items():
            trees[code] = trees.get(code, 0) + rows
            count[code] = count.get(code, 0) + 1
    listed = [c for c in cities if trees.get(c["code"])]
    index = {c["code"]: i for i, c in enumerate(listed)}
    unknown = sorted(set(trees) - set(index))
    if unknown:
        raise PublishError(f"rows carry city codes the registry does not list: {unknown}")
    return {
        "format": FORMAT,
        "generated_at": generated_at,
        "block_zoom": BLOCK_ZOOM,
        "columns": [name for name, _ in COLUMNS],
        "vocab": vocab,
        "trees": sum(block["rows"] for block in blocks),
        "cities": [
            {"code": c["code"], "name": c["name"], "center": c["coordinates"], "trees": trees[c["code"]],
             "blocks": count[c["code"]], "generation": inputs.get(c["code"])}
            for c in listed
        ],
        "blocks": [
            [block["key"], block["rows"], block["hash"],
             [v for code, rows in sorted(block["cities"].items(), key=lambda kv: index[kv[0]]) for v in (index[code], rows)]]
            for block in sorted(blocks, key=lambda b: b["key"])
        ],
    }


def validate(manifest: dict, live: dict | None, *, force: bool = False) -> None:
    """Refuse a candidate that contradicts itself or loses what is published."""
    blocks = manifest["blocks"]
    if not blocks:
        raise PublishError("no blocks: every projection was empty or unreadable")
    keys = [b[0] for b in blocks]
    if len(set(keys)) != len(keys):
        raise PublishError("a block key appears twice")
    per_axis = 2 ** manifest["block_zoom"]
    for key, rows, digest, city_rows in blocks:
        if not (0 <= key < per_axis * per_axis) or rows <= 0 or len(digest) != 16:
            raise PublishError(f"block {key}: bad entry ({rows} rows, hash {digest!r})")
        if sum(city_rows[1::2]) != rows or any(not 0 <= i < len(manifest["cities"]) for i in city_rows[0::2]):
            raise PublishError(f"block {key}: its cities do not add up to its {rows} rows")
    if sum(b[1] for b in blocks) != manifest["trees"]:
        raise PublishError("the blocks do not add up to the manifest's tree count")
    if sum(c["trees"] for c in manifest["cities"]) != manifest["trees"]:
        raise PublishError("the cities do not add up to the manifest's tree count")
    if live is None or force:
        return
    if manifest["trees"] < KEPT_RATIO * live["trees"]:
        raise PublishError(
            f"candidate holds {manifest['trees']:,} trees against {live['trees']:,} published: "
            f"under {KEPT_RATIO:.0%} of them (--force publishes it anyway)"
        )
    lost = sorted({c["code"] for c in live["cities"]} - {c["code"] for c in manifest["cities"]})
    if lost:
        raise PublishError(f"published cities are missing from the candidate: {lost} (--force publishes it anyway)")


def content_of(manifest: dict) -> dict:
    """The manifest without what changes on a run that changed nothing."""
    body = {k: v for k, v in manifest.items() if k != "generated_at"}
    body["cities"] = [{k: v for k, v in c.items() if k != "generation"} for c in manifest["cities"]]
    return body


def referenced(manifest: dict | None) -> set[str]:
    """Object names (under PREFIX) a manifest points at."""
    if manifest is None:
        return set()
    return {block_name(b[0], b[2]) for b in manifest["blocks"]} | {manifest["vocab"]}


def dumps(manifest: dict) -> bytes:
    return json.dumps(manifest, separators=(",", ":"), ensure_ascii=False).encode()


# ---------------------------------------------------------------------------
# Publish
# ---------------------------------------------------------------------------


def snapshot_inputs(cities: list[dict], bucket: Bucket | None) -> dict[str, int | None]:
    """Each projection's generation at the start of the run; None = never published."""
    urls = {c["code"]: projection_url(c["code"]) for c in cities}
    if bucket is None:
        return {code: head_generation(url) for code, url in urls.items()}
    listed = bucket.listing(TREES_BASE.removeprefix(f"{PUBLIC_HOST}/{BUCKET}/") + "/")
    return {
        code: (listed.get(url.removeprefix(f"{PUBLIC_HOST}/{BUCKET}/")) or {}).get("generation")
        for code, url in urls.items()
    }


def stale_objects(listed: dict[str, dict], keep: set[str], now: datetime) -> list[str]:
    """Blocks and vocabularies no kept manifest names, old enough to delete."""
    cutoff = now - timedelta(days=RETAIN_DAYS)
    stale = []
    for name, meta in listed.items():
        relative = name.removeprefix(f"{PREFIX}/")
        if not (relative.startswith("blocks/") or relative.startswith("vocab-")) or not relative.endswith(".parquet"):
            continue
        if relative not in keep and meta["modified"] < cutoff:
            stale.append(name)
    return sorted(stale)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="build and validate, never upload or delete")
    parser.add_argument("--output-dir", type=Path, help="keep the built files here (the layout under world/v1/)")
    parser.add_argument("--inputs-dir", type=Path,
                        help="read {code}_tree_browser_v2.parquet from this directory instead of the bucket")
    parser.add_argument("--force", action="store_true", help="publish a candidate the kept-ratio guard refuses")
    args = parser.parse_args(argv)

    started = now_iso()
    client = HmacClient.from_env()
    if client is None and not args.dry_run:
        raise PublishError("GOOGLE_HMAC_KEY / GOOGLE_HMAC_SECRET are not set")
    bucket = Bucket(client) if client else None

    cities = load_cities()
    listed = bucket.listing(f"{PREFIX}/") if bucket else {}
    live, live_generation = read_live(bucket, listed)
    log(f"live manifest: generation {live_generation or 'none'}"
        + (f", {live['trees']:,} trees in {len(live['blocks']):,} blocks" if live else ""))

    if args.inputs_dir:
        inputs: dict[str, int | None] = {}
        sources = {}
        for city in cities:
            path = args.inputs_dir / f"{city['code'].lower()}_tree_browser_v{DATA_VERSION}.parquet"
            if path.exists():
                sources[city["code"]] = path.as_posix()
    else:
        inputs = snapshot_inputs(cities, bucket)
        sources = {code: pinned(projection_url(code), generation) for code, generation in inputs.items() if generation}
    pending = [c["code"] for c in cities if c["code"] not in sources]
    if pending:
        log(f"no projection yet for {pending}; they join the world once theirs is published")

    with tempfile.TemporaryDirectory(prefix="world-blocks-") as tmp:
        workdir = Path(tmp)
        out = args.output_dir or workdir / "out"
        out.mkdir(parents=True, exist_ok=True)
        con = connect(workdir)
        read = load_rows(con, sources)
        build_world(con)
        blocks, vocab = write_files(con, out)
        con.close()

        manifest = build_manifest(cities, blocks, vocab, inputs, started)
        validate(manifest, live, force=args.force)
        body = dumps(manifest)
        (out / "manifest.json").write_bytes(body)
        duplicates = sum(read.values()) - manifest["trees"]
        log(
            f"candidate: {manifest['trees']:,} trees in {len(blocks):,} blocks from {len(manifest['cities'])} cities "
            f"({duplicates:,} rows another city also publishes dropped), "
            f"{sum((out / block_name(b['key'], b['hash'])).stat().st_size for b in blocks) / 1e6:.0f} MB of blocks, "
            f"manifest {len(body):,} bytes"
        )

        public_url = f"{PUBLIC_HOST}/{BUCKET}/{MANIFEST}"
        if live is not None and content_of(live) == content_of(manifest):
            log(f"unchanged since {live['generated_at']}; not uploading")
        elif args.dry_run or bucket is None:
            log("dry run: not uploading")
            return 0
        else:
            wanted = sorted(referenced(manifest))
            new = [name for name in wanted if f"{PREFIX}/{name}" not in listed]

            def upload(name: str) -> None:
                bucket.put(f"{PREFIX}/{name}", (out / name).read_bytes(),
                           content_type="application/vnd.apache.parquet", cache_control=IMMUTABLE_CACHE_CONTROL)

            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(upload, new))
            log(f"uploaded {len(new):,} of {len(wanted):,} files; the rest were already published")
            generation = bucket.put(MANIFEST, body, content_type="application/json",
                                    cache_control=MANIFEST_CACHE_CONTROL, if_generation_match=live_generation)
            log(f"published gs://{BUCKET}/{MANIFEST} generation {generation} (replaced {live_generation or 'nothing'})")

            # After the commit: the new manifest and the one it replaced stay whole.
            stale = stale_objects(listed, referenced(manifest) | referenced(live), datetime.now(timezone.utc))
            for name in stale:
                bucket.delete(name, listed[name]["generation"])
            if stale:
                log(f"deleted {len(stale):,} files no manifest has named for {RETAIN_DAYS} days")
        print(f"::trilogy-output name=world_manifest kind=link value={public_url}", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PublishError as exc:
        print(f"[world-blocks] not published: {exc}", file=sys.stderr)
        raise SystemExit(1)

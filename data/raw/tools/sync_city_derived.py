"""Write every city's derived job: its model, its probe, its job and its ordering.

Run after adding a city, or after changing what a city's derived file says
(from data/raw):

    uv run --no-project --with pyarrow --with "pytrilogy>=0.3.464" python tools/sync_city_derived.py

Each city in MUNICIPAL_DATA_SOURCES gets (city_derived.preql says why):

  raw/{code}/{slug}_tree_derived.preql   its predictions, browser projection and
                                         vocabulary, extending city_derived.preql's
                                         templates over its own published parquet
  raw/{code}/{slug}_derived_probe.py     its watermark (shared/city_derived.py)
  a `derived-{code}` [[cloud.job]]       on its city job's cron and memory

and raw/tree_predictions_source.preql lists every city's predictions file, for
the consumers that read all of them (the crown rollup).

The jobs sit between the `# >>> sync_city_derived: jobs` markers in
trilogy.toml and are rewritten whole. A derived model reads its city's parquet
through a root named like the city job's managed output, which is the edge
trilogy-cloud orders the tick by. A city whose model
imports tree_height.preql reads its LIDAR heights from its own parquet; any
other binds them to a typed null. Pass --check to only report what would
change (tests/test_city_derived.py fails while anything would).
"""

from __future__ import annotations

import re
import sys
import tomllib
from pathlib import Path

RAW = Path(__file__).resolve().parents[1]
DATA = RAW.parent
TOML = DATA / "trilogy.toml"
sys.path.insert(0, str(RAW))

from shared.ingest import MUNICIPAL_DATA_SOURCES  # noqa: E402

BUCKET = "trilogy_public_models/duckdb/trees"
JOBS_MARKERS = ("# >>> sync_city_derived: jobs\n", "# <<< sync_city_derived: jobs\n")

HEIGHTS = """    height_m: ?height_m,
    height_crown_radius_m: ?height_crown_radius_m,"""
NO_HEIGHTS = """    raw('''cast(null as float)'''): ?height_m,
    raw('''cast(null as float)'''): ?height_crown_radius_m,"""


def target(lc: str, name: str) -> str:
    return (f"file f`https://storage.googleapis.com/{BUCKET}/{lc}_{name}_v{{data_version}}.parquet`"
            f":f`gcs://{BUCKET}/{lc}_{name}_v{{data_version}}.parquet`;")


def city_model(code: str) -> Path:
    (path,) = sorted((RAW / code.lower()).glob("*_tree_info.preql"))
    return path


def city_output(code: str) -> str:
    """The city job's managed tree datasource: the name the derived edge keys on."""
    (name,) = re.findall(r"^partial datasource (\w+) extends published_tree_info\b",
                         city_model(code).read_text(encoding="utf-8"), re.M)
    return name


def has_heights(code: str) -> bool:
    return re.search(r"^import \.\.tree_height;", city_model(code).read_text(encoding="utf-8"), re.M) is not None


def model(code: str) -> str:
    lc, slug = code.lower(), city_model(code).name.removesuffix("_tree_info.preql")
    return f"""# {code}'s predictions and browser projection, from its own published parquet.
# Written by tools/sync_city_derived.py: edit that, not this. city_derived.preql
# says what each part is.

import ..city_derived;

root datasource {city_output(code)} extends published_city_trees (
{HEIGHTS if has_heights(code) else NO_HEIGHTS}
)
file f`https://storage.googleapis.com/{BUCKET}/{lc}_tree_info_v{{data_version}}.parquet`;

root datasource {lc}_derived_inputs extends city_derived_inputs ()
file `./{slug}_derived_probe.py`;

datasource {lc}_tree_predictions extends city_tree_predictions ()
{target(lc, "tree_predictions")}

root datasource {lc}_published_predictions extends published_city_predictions ()
file f`https://storage.googleapis.com/{BUCKET}/{lc}_tree_predictions_v{{data_version}}.parquet`;

datasource {lc}_tree_browser extends city_tree_browser ()
{target(lc, "tree_browser")}

datasource {lc}_tree_vocab extends city_tree_vocab ()
{target(lc, "tree_vocab")}
"""


PREDICTIONS_SOURCE = RAW / "tree_predictions_source.preql"


def predictions_source() -> str:
    files = ",\n".join(
        f"    f`https://storage.googleapis.com/{BUCKET}/{code.lower()}_tree_predictions_v{{data_version}}.parquet`"
        for code in sorted(MUNICIPAL_DATA_SOURCES)
    )
    return f"""# Every city's published predictions, as a *source*. Written by
# tools/sync_city_derived.py: edit that, not this.
#
# One root datasource over the per-city files the derived-{{code}} jobs
# publish (city_derived.preql), read as a single multi-file scan, for a
# consumer that wants every city's widths at once: the crown rollup
# (tree_rollups.preql). Root means unmanaged: importing this never builds a
# prediction.
#
#   * `tree_id: ~tree_id`: a predictions file omits imagery detections with no
#     LIDAR height, so it is a partial key. The planner pads the join and a
#     tree with no prediction keeps its other columns with a null width.
#   * Every listed file must exist, or the whole scan fails: a new city's
#     derived job must publish once before the rollup next runs.
#   * It must never be in the frontend's model bundle, which carries no
#     predictions at all (test_tree_predictions.py).

import tree_common;

property tree_id.predicted_crown_width_m float; # the allometric crown width, as the city's derived job published it; null where the model had nothing to size from
property tree_id.predicted_height_m float; # the allometric height, as published; null where the model had nothing to size from
property tree_id.accepted_height_m float; # the tree's LIDAR height where it passed the gates (or could not be checked); null where it has none or failed them
property tree_id.prediction_inputs_published_through datetime; # the newest of the city's inputs, as its file was published with

root datasource tree_predictions (
    tree_id: ~tree_id,
    city: city,
    predicted_crown_width_m: ?predicted_crown_width_m,
    predicted_height_m: ?predicted_height_m,
    accepted_height_m: ?accepted_height_m,
    prediction_inputs_published_through: prediction_inputs_published_through,
)
grain (tree_id)
file [
{files},
];
"""


def probe(code: str) -> str:
    return f'''#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy", "requests"]
# ///
"""{code}'s derived-tables watermark. Written by tools/sync_city_derived.py; see shared/city_derived.py."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from shared.city_derived import main  # noqa: E402

if __name__ == "__main__":
    main("{code}")
'''


def city_job(code: str, toml_text: str) -> dict:
    parsed = tomllib.loads(toml_text)
    return next(j for j in parsed["cloud"]["job"] if j["key"] == f"city-{code.lower()}")


def jobs_block(toml_text: str) -> str:
    out = [JOBS_MARKERS[0]]
    for code in sorted(MUNICIPAL_DATA_SOURCES):
        lc, city = code.lower(), city_job(code, toml_text)
        out.append(f"""[[cloud.job]]
key = "derived-{lc}"
name = "urban-tree-derived-{lc}"
entrypoint = "raw/{lc}/{city_model(code).name.removesuffix('_tree_info.preql')}_tree_derived.preql"
operation = "refresh"
schedule = "{city['schedule']}"
timeout_seconds = 1800
memory_mb = {city['memory_mb']}

""")
    out.append(JOBS_MARKERS[1])
    return "".join(out)


def replace_block(text: str, markers: tuple[str, str], block: str) -> str:
    start, end = text.index(markers[0]), text.index(markers[1]) + len(markers[1])
    return text[:start] + block + text[end:]


def planned() -> dict[Path, str]:
    """Every file this writes, with the content it should have."""
    files: dict[Path, str] = {}
    for code in sorted(MUNICIPAL_DATA_SOURCES):
        path = city_model(code)
        slug = path.name.removesuffix("_tree_info.preql")
        files[path.with_name(f"{slug}_tree_derived.preql")] = model(code)
        files[path.with_name(f"{slug}_derived_probe.py")] = probe(code)
    files[PREDICTIONS_SOURCE] = predictions_source()
    toml_text = TOML.read_text(encoding="utf-8")
    files[TOML] = replace_block(toml_text, JOBS_MARKERS, jobs_block(toml_text))
    return files


def main(argv: list[str]) -> int:
    stale = [p for p, text in planned().items() if not p.exists() or p.read_text(encoding="utf-8") != text]
    if "--check" in argv:
        for p in stale:
            print(f"would write {p.relative_to(DATA)}")
        return 1 if stale else 0
    for p, text in planned().items():
        if p in stale:
            p.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {p.relative_to(DATA)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

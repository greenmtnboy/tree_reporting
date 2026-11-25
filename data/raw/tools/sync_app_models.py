"""Write the app's tree model, tree_info.preql, from every city's published target.

Run after adding a city, or after changing what a city publishes (from data/raw):

    uv run --no-project --with pyarrow --with "pytrilogy>=0.3.541" python tools/sync_app_models.py

`tree_info.preql` is what the browser's dashboards and chat resolve their
PreQL against, and it is no job's entrypoint. It used to import every city's
build model, which brought each city's raw partitions, the dedup, position
and height derivations and their merges into every resolver request, plus a
`merge {code}_source into data_source` per city and a `greatest()` over every
city's watermark. None of that is needed to *read* a published parquet: a
city's parquet already carries `data_source`, `species`, `latitude` and the
rest as plain columns.

So it is written from the build instead. For each city in
MUNICIPAL_DATA_SOURCES it takes the city's published target (the datasource
that extends `published_tree_info`) as Trilogy composes it, and declares a
root partial datasource over the same file with the same `complete where`,
binding every column the target stores whose concept the consumer vocabulary
(tree_common + tree_published_columns) declares. The city's own source enum
becomes the shared `data_source`; the watermark, the dedup provenance and the
prune's columns are left out, and so is the prune itself, because the parquet
is already pruned. No `freshness by`: nothing builds from this file.

Pass --check to only report whether the file would change
(tests/test_app_models.py fails while it would).
"""

from __future__ import annotations

import sys
from pathlib import Path

RAW = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAW))

from shared.ingest import MUNICIPAL_DATA_SOURCES  # noqa: E402

BUCKET = "trilogy_public_models/duckdb/trees"
TREE_INFO = RAW / "tree_info.preql"
VOCABULARY = ("tree_common", "tree_published_columns")

HEADER = """\
# The app's tree model: what the browser's dashboards and chat resolve against.
# Written by tools/sync_app_models.py from every city's published target: edit
# that, not this.
#
# It reads published parquets and builds nothing, so it is no job's entrypoint
# and carries no `freshness by`. Each city's parquet is a root partial
# datasource with the `complete where city = '...'` of the target that writes
# it, which is what lets the planner answer a single-city chart from that
# city's file instead of the cross-city rollup (`tree_info`, over
# full_tree_info_v{n}.parquet), and the rollup answers everything else.
#
# No city build model is imported and nothing is merged. A city's parquet
# already stores the merged columns its build derived, and its `data_source`
# under that name, so each column binds straight to the shared concept.

"""


def city_model(code: str) -> Path:
    (path,) = sorted((RAW / code.lower()).glob("*_tree_info.preql"))
    return path


def _environment(text: str):
    from trilogy import Environment

    env = Environment(working_path=RAW)
    env.parse(text)
    return env


def vocabulary() -> set[str]:
    """The concepts the consumer model declares, by address."""
    env = _environment("".join(f"import {name};\n" for name in VOCABULARY))
    return set(env.concepts.keys())


def published_targets() -> dict:
    """Every city's published tree target, as Trilogy composes it, by code."""
    codes = sorted(MUNICIPAL_DATA_SOURCES)
    env = _environment(
        "".join(f"import {code.lower()}.{city_model(code).stem};\n" for code in codes)
    )
    return {code: env.datasources[city_model(code).stem] for code in codes}


def complete_where(datasource) -> str:
    from trilogy.parsing.render import Renderer

    return Renderer().to_string(datasource.non_partial_for.conditional)


def columns(code: str, target, known: set[str]) -> list[str]:
    from trilogy.core.enums import Modifier

    source = f"local.{code.lower()}_source"
    lines = []
    for column in target.columns:
        address = column.concept.address
        if address == source:
            concept = "data_source"
        elif address in known:
            concept = address.removeprefix("local.")
        else:
            continue
        nullable = "?" if Modifier.NULLABLE in column.modifiers else ""
        lines.append(f"    {column.alias}: {nullable}{concept},")
    return lines


# The columns every city's parquet carries, declared once; a city's datasource
# extends it and names only what differs (`borough`, LIDAR heights, a column
# its target declares nullable where the template does not).
TEMPLATE = "app_city_trees"
TEMPLATE_COLUMNS = (
    "    tree_id: tree_id,",
    "    city: city,",
    "    data_source: data_source,",
    "    species: species,",
    "    tree_name: ?tree_name,",
    "    plant_date: ?plant_date,",
    "    diameter_at_breast_height: ?diameter_at_breast_height,",
    "    latitude: ?latitude,",
    "    longitude: ?longitude,",
    "    submission_photo_url: ?submission_photo_url,",
    "    cultivar: ?cultivar,",
    "    crown_width_m: ?crown_width_m,",
    "    significance: ?significance,",
    "    significance_source: ?significance_source,",
)


# The cross-city rollup (`publish-full`, full_tree_publish.preql), for the
# queries no one city's parquet answers. Declared here, named `tree_info` as
# it always was in this file, rather than imported from the rollup's own
# source view (full_tree_info_source.preql), which stays out of the browser's
# bundle (test_tree_predictions.py).
ROLLUP = (
    f"root datasource tree_info extends {TEMPLATE} ()\n"
    f"file f`https://storage.googleapis.com/{BUCKET}/full_tree_info_v{{data_version}}.parquet`;\n"
)


def template() -> str:
    body = "\n".join(TEMPLATE_COLUMNS)
    return f"template datasource {TEMPLATE} (\n{body}\n)\ngrain (tree_id);\n"


def _alias(line: str) -> str:
    return line.strip().split(":", 1)[0]


def city_block(code: str, target, known: set[str]) -> str:
    lc = code.lower()
    lines = columns(code, target, known)
    shared = {_alias(line) for line in TEMPLATE_COLUMNS}
    missing = shared - {_alias(line) for line in lines}
    if missing:
        raise SystemExit(f"{code}: its published target stores none of {sorted(missing)}, "
                         f"which every city's parquet is expected to carry ({TEMPLATE})")
    extra = [line for line in lines if line not in TEMPLATE_COLUMNS]
    body = ("\n" + "\n".join(extra) + "\n") if extra else ""
    return (
        f"root partial datasource {target.name} extends {TEMPLATE} ({body})\n"
        f"complete where {complete_where(target)}\n"
        f"file f`https://storage.googleapis.com/{BUCKET}/{lc}_tree_info_v{{data_version}}.parquet`;\n"
    )


def planned() -> str:
    known = vocabulary()
    targets = published_targets()
    imports = "".join(f"import {name};\n" for name in VOCABULARY)
    blocks = "\n".join(city_block(code, targets[code], known) for code in sorted(targets))
    return f"{HEADER}{imports}\n{template()}\n{ROLLUP}\n{blocks}"


def main(argv: list[str]) -> int:
    text = planned()
    current = TREE_INFO.read_text(encoding="utf-8") if TREE_INFO.exists() else ""
    if text == current:
        print("tree_info.preql is up to date")
        return 0
    if "--check" in argv:
        print("tree_info.preql would change; run tools/sync_app_models.py")
        return 1
    TREE_INFO.write_text(text, encoding="utf-8", newline="\n")
    print(f"wrote {TREE_INFO.relative_to(RAW)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

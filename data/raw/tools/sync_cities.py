"""Write every shared city registry from the per-city manifests (raw/{code}/city.toml).

    cd data/raw && uv run tools/sync_cities.py             # write what is stale
    cd data/raw && uv run tools/sync_cities.py --check     # exit 1 if anything is stale
    cd data/raw && uv run tools/sync_cities.py --resolve   # after a merge conflict in a generated file

A city's facts live in its manifest (tools/city_manifest.py describes the
keys); everything below is generated from all of them, so a city addition
adds files and edits none by hand:

  shared/city_registry.py         MUNICIPAL_DATA_SOURCES, OSM_DATA_SOURCES, CITY_BOUNDS,
                                  CITY_TERRITORY, DEDUP_CELL_METRES, OSM_CITY_NAMES,
                                  SATELLITE_DATA_SOURCES, PREDICTED_DATA_SOURCES,
                                  PREDICTED_STAGED_WITH
                                  (shared/ingest.py and shared/osm.py re-export them)
  core.preql                      the city enum
  city_ecoregion.preql            each city's home ecoregion
  tree_common.preql               {lc}_data_updated_through, {lc}_osm_data_updated_through,
                                  {lc}_predicted_data_updated_through
  satellite_tree_info.preql       {lc}_satellite_data_updated_through
  community_tree_info.preql       {lc}_community_data_updated_through
  landmark_common.preql           {lc}_landmark_data_updated_through and their greatest()
  full_tree_publish.preql         the rollup's file list
  full_landmark_publish.preql     the landmark rollup's file list
  ../trilogy.toml                 city-, osm- and refresh-landmarks- jobs
  src/src/cityConfig.json         + its homepage mirror, data/website/city_config.json
  src/src/data/dataSources.ts     the municipal data_source labels
  src/src/data/sourceCatalog.ts   tree and landmark attribution rows
  src/src/composables/dashboardContextSource.ts   ecoregion, zone, biome, realm

Outside the one whole file, each output is a region between
`>>> sync_cities: <name>` and `<<< sync_cities: <name>` comment markers; the
text around the regions is hand-written and kept.  Then the generators that
read these registries run in turn (sync_osm_territory, dedup_cells,
sync_city_derived, sync_app_models), so one command brings every derived
file up to date.

**A merge conflict in a generated file is resolved by regenerating it.**
Two branches that each add a city conflict only here; `--resolve` takes the
current side of every conflict inside a generated region (merge or diff3
style), then rewrites the regions from the manifests, which hold both
branches' cities.  A conflict outside a region, or across a region's
markers, is a hand-written change on both sides: `--resolve` then names it
and writes nothing.
tests/test_sync_cities.py fails while anything is stale.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
RAW = TOOLS.parent
DATA = RAW.parent
REPO = DATA.parent
SRC = REPO / "src" / "src"
sys.path.insert(0, str(TOOLS))

from city_manifest import City, load_all  # noqa: E402

BUCKET = "https://storage.googleapis.com/trilogy_public_models/duckdb"


# ---------------------------------------------------------------------------
# regions
# ---------------------------------------------------------------------------

def markers(name: str, prefix: str) -> tuple[str, str]:
    return f"{prefix} >>> sync_cities: {name}\n", f"{prefix} <<< sync_cities: {name}\n"


def region(text: str, name: str, prefix: str) -> tuple[int, int]:
    start, end = markers(name, prefix)
    if text.count(start) != 1 or text.count(end) != 1:
        raise SystemExit(f"expected one `{start.strip()}` region")
    a = text.index(start) + len(start)
    return a, text.index(end, a)


def put(text: str, name: str, prefix: str, body: str) -> str:
    a, b = region(text, name, prefix)
    return text[:a] + body + text[b:]


def put_items(text: str, name: str, prefix: str, item: str, items: list[str]) -> str:
    """Rewrite the run of `item`-matching lines inside a region that wraps a
    whole statement (a `#` comment cannot sit inside its SQL or file list)."""
    a, b = region(text, name, prefix)
    lines = text[a:b].split("\n")
    run = [n for n, line in enumerate(lines) if re.match(item, line)]
    if not run or run != list(range(run[0], run[-1] + 1)):
        raise SystemExit(f"region {name}: the item lines are not one run")
    trailing = lines[run[-1]].rstrip().endswith(",")
    rendered = [f"{x}," for x in items]
    if not trailing:
        rendered[-1] = rendered[-1].removesuffix(",")
    lines[run[0]:run[-1] + 1] = rendered
    return text[:a] + "\n".join(lines) + text[b:]


def comment(note: str | None, indent: str, prefix: str = "#") -> str:
    if not note:
        return ""
    return "".join(f"{indent}{prefix} {line}".rstrip() + "\n" for line in note.rstrip("\n").split("\n"))


# ---------------------------------------------------------------------------
# shared/city_registry.py
# ---------------------------------------------------------------------------

def py_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def py_tuple(values) -> str:
    inner = ", ".join(py_str(v) if isinstance(v, str) else repr(v) for v in values)
    return f"({inner},)" if len(values) == 1 else f"({inner})"


def city_registry(cities: list[City]) -> str:
    def block(name: str, annotation: str, entry) -> str:
        return f"{name}: {annotation} = {{\n" + "".join(entry(c) for c in cities) + "}\n"

    def territory(c: City) -> str:
        boxes = c["territory"]
        head = comment(c.get("territory_note"), "    ")
        if len(boxes) == 1 and not isinstance(boxes[0], str):
            return f"{head}    {py_str(c.code)}: ({py_tuple(boxes[0])},),\n"
        body = "".join(
            comment(b, "        ") if isinstance(b, str) else f"        {py_tuple(b)},\n" for b in boxes
        )
        return f"{head}    {py_str(c.code)}: (\n{body}    ),\n"

    return "\n\n".join([
        '''"""Every city's registry facts, generated by tools/sync_cities.py from the
per-city manifests (raw/{code}/city.toml).  Do not edit: change the city's
manifest and rerun the sync.  shared/ingest.py and shared/osm.py re-export
these and say what each one is for."""

Box = tuple[float, float, float, float]
''',
        block("MUNICIPAL_DATA_SOURCES", "dict[str, tuple[str, ...]]", lambda c:
              comment(c.get("municipal_sources_note"), "    ")
              + f"    {py_str(c.code)}: {py_tuple(c['municipal_sources']) if c['municipal_sources'] else '()'},\n"),
        block("OSM_DATA_SOURCES", "dict[str, str]", lambda c: f'    {py_str(c.code)}: "OSM_{c.code}",\n'),
        block("CITY_BOUNDS", "dict[str, tuple[float, float, float, float]]", lambda c:
              comment(c.get("bounds_note"), "    ") + f"    {py_str(c.code)}: {py_tuple(c['bounds'])},\n"),
        block("CITY_TERRITORY", "dict[str, tuple[Box, ...]]", territory),
        block("DEDUP_CELL_METRES", "dict[str, int]", lambda c:
              comment(c.get("dedup_note"), "    ") + f"    {py_str(c.code)}: {c['dedup_cell_metres']},\n"),
        block("OSM_CITY_NAMES", "dict[str, str]", lambda c:
              comment(c.get("osm_name_note"), "    ") + f"    {py_str(c.code)}: {py_str(c.osm_name)},\n"),
        imagery_block("SATELLITE_DATA_SOURCES", [c for c in cities if c.satellite],
                      lambda c: f'"SATELLITE_{c.code}"'),
        imagery_block("PREDICTED_DATA_SOURCES", [c for c in cities if c.predicted],
                      lambda c: f'"PREDICTED_{c.code}"'),
        imagery_block("PREDICTED_STAGED_WITH", [c for c in cities if c.predicted_staged_with],
                      lambda c: py_str(c.predicted_staged_with)),
    ])


def imagery_block(name: str, cities: list[City], value) -> str:
    """An opt-in registry: only the cities whose manifest sets the flag."""
    return f"{name}: dict[str, str] = {{\n" + "".join(f"    {py_str(c.code)}: {value(c)},\n" for c in cities) + "}\n"


# ---------------------------------------------------------------------------
# models
# ---------------------------------------------------------------------------

def core(text: str, cities: list[City]) -> str:
    codes = ", ".join(f"'{c.code}'" for c in cities)
    return put(text, "city enum", "#",
               f"key city enum<string>[{codes}]; # UN/LOCODE-style code for the source city\n")


def city_ecoregion(text: str, cities: list[City]) -> str:
    return put_items(text, "city ecoregions", "#", r"    \('[A-Z]{5}', \d+\)",
                     [f"    ('{c.code}', {c['ecoregion']})" for c in cities])


def tree_common(text: str, cities: list[City]) -> str:
    out = []
    for c in cities:
        if c["data_freshness"] == "probe":
            note = c.get("data_freshness_comment") or f"when {c.name} data is through"
            out.append(comment(c.get("data_freshness_note"), ""))
            out.append(f"property <*>.{c.lc}_data_updated_through datetime; # {note}\n")
        elif c["data_freshness"] == "model":
            out.append(comment(c.get("data_freshness_note"), ""))
            out.append(f"# property <*>.{c.lc}_data_updated_through datetime; # {c['data_freshness_comment']}\n")
        osm = c.get("osm_freshness_comment", f"GCS publication time of {c.name}'s staged OSM parquet")
        out.append(f"property <*>.{c.lc}_osm_data_updated_through datetime;{f' # {osm}' if osm else ''}\n")
    text = put(text, "city freshness", "#", "".join(out))
    by_code = {c.code: c for c in cities}
    predicted = []
    for c in cities:
        if not c.predicted:
            continue
        host = c.predicted_staged_with
        note = (f"GCS publication time of the {by_code[host].name} run {c.name} reads its predictions from "
                "(PREDICTED_STAGED_WITH)" if host else
                f"GCS publication time of {c.name}'s staged bulk imagery predictions (predicted_update_time.py)")
        predicted.append(f"property <*>.{c.lc}_predicted_data_updated_through datetime; # {note}\n")
    return put(text, "predicted freshness", "#", "".join(predicted))


def satellite(text: str, cities: list[City]) -> str:
    return put(text, "satellite freshness", "#", "".join(
        f"property <*>.{c.lc}_satellite_data_updated_through datetime; "
        f"# when published satellite trees for {c.code} are through\n" for c in cities if c.satellite))


def community(text: str, cities: list[City]) -> str:
    return put(text, "community freshness", "#", "".join(
        f"property <*>.{c.lc}_community_data_updated_through datetime; "
        f"# when approved community submissions for {c.code} are through\n" for c in cities))


def landmark_common(text: str, cities: list[City]) -> str:
    text = put(text, "landmark freshness", "#", "".join(
        f"property <*>.{c.lc}_landmark_data_updated_through datetime;\n" for c in cities))
    terms = ", ".join(f"{c.lc}_landmark_data_updated_through" for c in cities)
    return put(text, "latest landmark update", "#", f"auto latest_landmark_update_through <- greatest({terms});\n")


def rollup(kind: str, stem: str):
    def write(text: str, cities: list[City]) -> str:
        return put_items(text, f"{kind} files", "#", rf"    f`{re.escape(BUCKET)}/{kind}/[a-z]{{5}}_{stem}_v",
                         [f"    f`{BUCKET}/{kind}/{c.lc}_{stem}_v{{data_version}}.parquet`" for c in cities])
    return write


# ---------------------------------------------------------------------------
# trilogy.toml
# ---------------------------------------------------------------------------

JOB = {
    "city": ("city-{lc}", "urban-tree-city-{lc}", "raw/{lc}/{slug}_tree_info.preql"),
    "osm": ("osm-{lc}", "urban-tree-osm-{lc}", "osm_staging/{lc}_osm_staging.preql"),
    "landmarks": ("refresh-landmarks-{lc}", "urban-tree-landmarks-refresh-{lc}", "raw/{lc}/{slug}_landmarks.preql"),
}


def job(c: City, kind: str) -> str:
    key, name, entrypoint = (s.format(lc=c.lc, slug=c.slug) for s in JOB[kind])
    j = c.job(kind)
    out = [comment(j.get("note"), ""),
           f'[[cloud.job]]\nkey = "{key}"\nname = "{name}"\nentrypoint = "{entrypoint}"\noperation = "refresh"\n']
    for field in ("schedule", "timeout_seconds", "memory_mb"):
        out.append(comment(j.get(f"{field}_note"), ""))
        value = j[field]
        out.append(f'{field} = "{value}"\n' if isinstance(value, str) else f"{field} = {value}\n")
    return "".join(out) + "\n"


def trilogy_toml(text: str, cities: list[City]) -> str:
    for kind in ("landmarks", "city", "osm"):
        text = put(text, f"{kind} jobs", "#", "".join(job(c, kind) for c in cities))
    return text


# ---------------------------------------------------------------------------
# frontend
# ---------------------------------------------------------------------------

def city_config(cities: list[City]) -> str:
    rows = [
        f'  "{c.code}": {{ "name": {json.dumps(c.name, ensure_ascii=False)}, '
        f'"center": [{c["center"][0]!r}, {c["center"][1]!r}] }}'
        for c in cities
    ]
    return "{\n" + ",\n".join(rows) + "\n}\n"


def ts_str(value: str) -> str:
    """Prettier's choice: single quotes unless the value holds more of them."""
    if value.count("'") > value.count('"'):
        return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def ts_key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", key) else ts_str(key)


def data_sources(text: str, cities: list[City]) -> str:
    return put(text, "data source labels", "  //", "".join(
        f"  {ts_key(source)}: {ts_str(label)},\n" for c in cities for source, label in c.get("labels", {}).items()))


def source_catalog(text: str, cities: list[City]) -> str:
    ordered = sorted(cities, key=lambda c: (c.name.casefold(), c.code))
    for key, name in (("tree_sources", "tree sources"), ("landmark_sources", "landmark sources")):
        rows = []
        for c in ordered:
            for row in c.get(key, []):
                url = f", url: {ts_str(row['url'])}" if row.get("url") else ""
                rows.append(comment(row.get("note"), "  ", "//")
                            + f"  {{ city: {ts_str(c.name)}, label: {ts_str(row['label'])}{url} }},\n")
        text = put(text, name, "  //", "".join(rows))
    return text


def dashboard_context(text: str, cities: list[City]) -> str:
    return put(text, "dashboard context", "  //", "".join(
        f"  {c.code}: {{\n"
        f"    ecoregionId: {c['ecoregion']}, // {c['ecoregion_name']}\n"
        f"    usdaZone: {c['usda_zone']},\n"
        f"    biome: {ts_str(c['biome'])},\n"
        f"    realm: {ts_str(c['realm'])},\n"
        f"  }},\n" for c in cities))


# ---------------------------------------------------------------------------

REGIONS = {
    RAW / "core.preql": core,
    RAW / "city_ecoregion.preql": city_ecoregion,
    RAW / "tree_common.preql": tree_common,
    RAW / "community_tree_info.preql": community,
    RAW / "satellite_tree_info.preql": satellite,
    RAW / "landmark_common.preql": landmark_common,
    RAW / "full_tree_publish.preql": rollup("trees", "tree_info"),
    RAW / "full_landmark_publish.preql": rollup("landmarks", "landmark_info"),
    DATA / "trilogy.toml": trilogy_toml,
    SRC / "data" / "dataSources.ts": data_sources,
    SRC / "data" / "sourceCatalog.ts": source_catalog,
    SRC / "composables" / "dashboardContextSource.ts": dashboard_context,
}
WHOLE = {
    RAW / "shared" / "city_registry.py": city_registry,
    SRC / "cityConfig.json": city_config,
    DATA / "website" / "city_config.json": city_config,
}
# Run after this one, in order: each reads what the previous wrote.
DOWNSTREAM = ("sync_osm_territory.py", "dedup_cells.py", "sync_city_derived.py", "sync_app_models.py")
DOWNSTREAM_WRITE = {"dedup_cells.py": ["--write"]}


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def planned(cities: list[City]) -> dict[Path, str]:
    files = {path: render(cities) for path, render in WHOLE.items()}
    for path, render in REGIONS.items():
        files[path] = render(read(path), cities)
    return files


# One conflict hunk, merge or diff3 style: the current side, an optional
# `|||||||` base section, then the incoming side.
CONFLICT = re.compile(
    r"^<<<<<<< [^\n]*\n(?P<ours>.*?)(?:^\|\|\|\|\|\|\| [^\n]*\n.*?)?^=======\n.*?^>>>>>>> [^\n]*\n",
    re.S | re.M,
)
# Every generated region a conflict may sit in: this sync's, and the other
# generators' it runs (the derived jobs block, the dedup grid).
REGION_START = re.compile(r"^.*(?:>>> sync_cities: |>>> sync_city_derived: |# BEGIN dedup_cells).*\n", re.M)
REGION_END = re.compile(r"^.*(?:<<< sync_cities: |<<< sync_city_derived: |# END dedup_cells).*\n", re.M)
# Files some generator writes whole, beyond WHOLE.
GENERATED_WHOLE = (RAW / "tree_info.preql", RAW / "tree_predictions_source.preql")
GENERATED_REGIONS = (RAW / "tree_dedup.preql",)


def generated_spans(text: str) -> list[tuple[int, int]] | None:
    """(start, end) of each region's body, or None when the markers do not pair up."""
    starts, ends = list(REGION_START.finditer(text)), list(REGION_END.finditer(text))
    if len(starts) != len(ends):
        return None
    spans = [(s.end(), e.start()) for s, e in zip(starts, ends)]
    if any(a > b for a, b in spans) or any(spans[i][1] > spans[i + 1][0] for i in range(len(spans) - 1)):
        return None
    return spans


def resolve_text(text: str, whole: bool) -> tuple[str, list[int]]:
    """The text with every conflict's current side taken, and the line of each
    conflict that is not wholly inside a generated region (those must not be
    resolved here: the incoming side there is somebody's hand-written change)."""
    spans = [(0, len(text))] if whole else generated_spans(text)
    outside = []
    for m in CONFLICT.finditer(text):
        if spans is None or not any(a <= m.start() and m.end() <= b for a, b in spans):
            outside.append(text.count("\n", 0, m.start()) + 1)
    resolved = CONFLICT.sub(lambda m: m.group("ours"), text)
    if "<<<<<<< " in resolved or "\n>>>>>>> " in resolved:
        outside.append(0)  # a hunk this did not recognise
    return resolved, outside


def resolve() -> None:
    """Take the current side of every conflict inside a generated region; the
    regeneration that follows rewrites those regions from the manifests.
    Refuses, writing nothing, if any conflict sits outside a region or crosses
    a marker: that one is a real merge, for a person."""
    planned_writes: dict[Path, str] = {}
    problems: list[str] = []
    candidates = [(p, True) for p in (*WHOLE, *GENERATED_WHOLE)] + [(p, False) for p in (*REGIONS, *GENERATED_REGIONS)]
    for path, whole in candidates:
        text = read(path)
        if "<<<<<<< " not in text:
            continue
        resolved, outside = resolve_text(text, whole)
        rel = path.relative_to(REPO)
        problems += [f"{rel}:{line}" if line else f"{rel}: an unrecognised conflict hunk" for line in outside]
        planned_writes[path] = resolved
    if problems:
        raise SystemExit(
            "not resolving: these conflicts are outside a generated region (or cross one's\n"
            "markers), so the incoming side is a hand-written change -- merge them by hand,\n"
            "then rerun --resolve:\n  " + "\n  ".join(problems)
        )
    for path, text in planned_writes.items():
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"took the current side of the generated conflicts in {path.relative_to(REPO)}")


def main(argv: list[str]) -> int:
    if "--resolve" in argv:
        resolve()
    cities = load_all()
    stale = {p: t for p, t in planned(cities).items() if read(p).replace("\r\n", "\n") != t}
    check = "--check" in argv
    for path, text in stale.items():
        if check:
            print(f"would write {path.relative_to(REPO)}")
        else:
            path.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {path.relative_to(REPO)}")
    failed = bool(stale) and check
    for script in DOWNSTREAM:
        args = ["--check"] if check else DOWNSTREAM_WRITE.get(script, [])
        if subprocess.run([sys.executable, str(TOOLS / script), *args], cwd=RAW).returncode:
            failed = True
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

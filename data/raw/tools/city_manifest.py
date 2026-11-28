"""Per-city manifests: `raw/{code}/city.toml`, the one place a city's registry facts live.

Everything a city used to add to a shared file -- its line in the city enum,
its bounds and territory, its data sources and their labels, its dedup cell,
its ecoregion and dashboard context, its three cloud jobs, its attribution
rows -- is written here, and `tools/sync_cities.py` generates the shared
files from every manifest.  Adding a city therefore adds files and edits
none, so two branches that each add cities no longer conflict over the
registries; where they meet in a generated file, rerunning the sync is the
resolution (`sync_cities.py --resolve`).

The manifests are workstation input: the cloud bundle excludes them
(trilogy.toml), and nothing a job runs reads them -- jobs read the generated
files (`shared/city_registry.py`, the models, the job table).

Schema (every key is required unless marked optional):

    code = "USDEN"                    # five letters; the directory is code.lower()
    name = "Denver"                   # display name: city config, attribution rows
    slug = "denver"                   # file stem of raw/{lc}/{slug}_tree_info.preql
    center = [-104.9903, 39.7392]     # lon, lat (the city config's order)
    osm_name = "..."                  # optional; OSM_CITY_NAMES when not `name`
    ecoregion = 402                   # RESOLVE ECO_ID: city_ecoregion + dashboard
    ecoregion_name = "..."            # its name, the dashboard context's comment
    usda_zone = 6
    biome = "..."
    realm = "nearctic"
    bounds = [lat_min, lat_max, lon_min, lon_max]       # CITY_BOUNDS
    territory = [[lat_min, lat_max, lon_min, lon_max], "a comment line", ...]
    municipal_sources = ["DENVER_OPENDATA"]             # may be empty
    dedup_cell_metres = 10
    data_freshness = "probe" | "model" | "none"         # tree_common's {lc}_data_updated_through
    *_note = '''...'''                # optional; written as comments above the entry
                                      # (bounds_note, territory_note,
                                      # municipal_sources_note, dedup_note,
                                      # data_freshness_note, osm_name_note)
    [labels]                          # DATA_SOURCE_LABELS rows, by data_source
    [[tree_sources]] / [[landmark_sources]]   # attribution rows: label, url?, note?
    [jobs.city] / [jobs.osm] / [jobs.landmarks]
        schedule, timeout_seconds, memory_mb, comment? (lines inside the block)
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RAW = Path(__file__).resolve().parents[1]
MANIFEST = "city.toml"

HEADER = (
    "# {name}'s registry facts.  The shared registries are generated from this\n"
    "# file: edit it, then run `cd data/raw && uv run tools/sync_cities.py`.\n"
)


@dataclass(frozen=True)
class City:
    path: Path
    data: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, default)

    @property
    def code(self) -> str:
        return self.data["code"]

    @property
    def lc(self) -> str:
        return self.code.lower()

    @property
    def name(self) -> str:
        return self.data["name"]

    @property
    def slug(self) -> str:
        return self.data["slug"]

    @property
    def osm_name(self) -> str:
        return self.data.get("osm_name", self.name)

    def job(self, kind: str) -> dict[str, Any]:
        return self.data["jobs"][kind]


REQUIRED = (
    "code", "name", "slug", "center", "ecoregion", "ecoregion_name", "usda_zone",
    "biome", "realm", "bounds", "territory", "municipal_sources",
    "dedup_cell_metres", "data_freshness", "jobs",
)


def validate(city: City) -> None:
    missing = [k for k in REQUIRED if k not in city.data]
    if missing:
        raise ValueError(f"{city.path}: missing {', '.join(missing)}")
    if not re.fullmatch(r"[A-Z]{5}", city.code):
        raise ValueError(f"{city.path}: code {city.code!r} is not five capitals")
    if city.path.parent.name != city.lc:
        raise ValueError(f"{city.path}: directory is not {city.lc}")
    if city["data_freshness"] not in ("probe", "model", "none"):
        raise ValueError(f"{city.path}: data_freshness is probe, model or none")
    for kind in ("city", "osm", "landmarks"):
        job = city["jobs"].get(kind)
        if not job or not {"schedule", "timeout_seconds", "memory_mb"} <= set(job):
            raise ValueError(f"{city.path}: [jobs.{kind}] needs schedule, timeout_seconds, memory_mb")
    for box in city["territory"]:
        if not isinstance(box, str) and len(box) != 4:
            raise ValueError(f"{city.path}: territory box {box!r} is not four numbers")


def load(path: Path) -> City:
    city = City(path, tomllib.loads(path.read_text(encoding="utf-8")))
    validate(city)
    return city


def load_all(raw: Path = RAW) -> list[City]:
    """Every city, sorted by code."""
    cities = sorted((load(p) for p in raw.glob(f"*/{MANIFEST}")), key=lambda c: c.code)
    seen: dict[str, Path] = {}
    for c in cities:
        if c.code in seen:
            raise ValueError(f"{c.code} declared by {seen[c.code]} and {c.path}")
        seen[c.code] = c.path
    return cities


# ---------------------------------------------------------------------------
# Writing: a small emitter for exactly this schema (tomllib only reads).
# ---------------------------------------------------------------------------

def _str(value: str) -> str:
    if "\n" in value:
        body = value.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
        # The newline after the opening quotes is not part of the value.
        return f'"""\n{body}"""'
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _str(value)
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_value(v) for v in value) + "]"
    raise TypeError(f"cannot write {value!r}")


def _key(key: str) -> str:
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else _str(key)


def _territory(boxes: list) -> str:
    if len(boxes) == 1:
        return f"territory = [{_value(boxes[0])}]\n"
    lines = "".join(f"    {_value(b)},\n" for b in boxes)
    return f"territory = [\n{lines}]\n"


def dumps(data: dict[str, Any]) -> str:
    out = [HEADER.format(name=data["name"])]
    tables = {k: v for k, v in data.items() if isinstance(v, dict) or (isinstance(v, list) and v and isinstance(v[0], dict))}
    for key, value in data.items():
        if key in tables:
            continue
        if key == "territory":
            out.append(_territory(value))
        else:
            out.append(f"{_key(key)} = {_value(value)}\n")
    for key in ("labels",):
        if data.get(key):
            out.append(f"\n[{key}]\n")
            out.extend(f"{_key(k)} = {_value(v)}\n" for k, v in data[key].items())
    for key in ("tree_sources", "landmark_sources"):
        for row in data.get(key, []):
            out.append(f"\n[[{key}]]\n")
            out.extend(f"{_key(k)} = {_value(v)}\n" for k, v in row.items())
    for kind, job in data["jobs"].items():
        out.append(f"\n[jobs.{kind}]\n")
        out.extend(f"{_key(k)} = {_value(v)}\n" for k, v in job.items())
    text = "".join(out)
    if tomllib.loads(text) != {k: v for k, v in data.items()}:
        raise AssertionError(f"{data['code']}: manifest does not round-trip")
    return text

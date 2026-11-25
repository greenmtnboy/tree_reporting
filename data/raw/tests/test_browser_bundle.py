"""The browser's model bundle (src/src/trilogyModels.ts) is the app's
consumption models, and carries every file they import.

The hosted resolver can only parse what it is sent, and it parses all of it on
every request. A bundled model that gains an `import x;` the list lacks makes
every dashboard and chat query fail with "Unable to import file x, not
resolvable from provided source files" -- which only the resolver-backed
frontend suites notice, and only online. A city *build* model in the list is
the other failure: it is never imported, so nothing breaks, and every request
parses its raw partitions and derivations for nothing. This catches both
offline.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = RAW_DIR.parents[1] / "src" / "src"
BUNDLE = SRC_DIR / "trilogyModels.ts"

IMPORT_RE = re.compile(r"^import ([\w.]+)(?: as \w+)?;", re.M)


def bundled() -> dict[str, Path]:
    """Each bundled alias and the file it is read from."""
    text = BUNDLE.read_text(encoding="utf-8")
    files = {
        name: (BUNDLE.parent / path).resolve()
        for name, path in re.findall(r"^import (\w+) from '([^']+\.preql)\?raw'", text, re.M)
    }
    return {
        alias: files[variable]
        for alias, variable in re.findall(r"\{ alias: '([\w.]+)', contents: (\w+) \}", text)
    }


def test_the_bundle_names_its_files():
    assert bundled(), "no `{ alias, contents }` entries found in trilogyModels.ts"
    assert "import.meta.glob" not in BUNDLE.read_text(encoding="utf-8"), (
        "trilogyModels.ts globs model files; the bundle lists the consumption models by hand"
    )


@pytest.mark.parametrize("alias", sorted(bundled()))
def test_every_import_is_bundled(alias: str):
    path = bundled()[alias]
    missing = {
        name for name in IMPORT_RE.findall(path.read_text(encoding="utf-8"))
        if not name.startswith("std.")
    } - set(bundled())
    assert not missing, (
        f"{alias} imports {sorted(missing)}, which src/src/trilogyModels.ts does not send to the resolver"
    )


# Two shared models are still their own writers: the bundle reads the species
# dimension and the ecoregions from the files that build them. Each is one
# file with no city behind it, and neither has a consumer twin the browser can
# use yet -- tree_enrichment_source.preql declares `genus` as a key, which
# cannot share a scope with the enrichment model's property.
STILL_BUILD_MODELS = {"tree_enrichment", "ecoregion_info"}


@pytest.mark.parametrize("alias", sorted(bundled()))
def test_no_build_model_is_bundled(alias: str):
    path = bundled()[alias]
    assert path.parent == RAW_DIR or path.parent == SRC_DIR / "models", (
        f"{alias} is read from {path}; a city's build model is never sent to the resolver"
    )
    if alias in STILL_BUILD_MODELS:
        return
    text = path.read_text(encoding="utf-8")
    assert not re.search(r"^\s*freshness by", text, re.M), f"{alias} is a build target, not a consumption model"
    assert not re.search(r"^file `\./\w+\.py`", text, re.M), f"{alias} runs a script, not a published read"

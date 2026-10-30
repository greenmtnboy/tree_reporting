"""The browser's model bundle (src/src/trilogyModels.ts) carries every shared
file a city model imports.

The bundle globs the city models but lists the shared files they import by
hand, and the hosted resolver can only parse what it is sent.  A city model
that gains an `import ..x;` the list lacks makes every dashboard and chat
query fail with "Unable to import file x, not resolvable from provided source
files" -- which only the resolver-backed frontend suites notice, and only
online.  This catches it offline.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

RAW_DIR = Path(__file__).resolve().parents[1]
BUNDLE = RAW_DIR.parents[1] / "src" / "src" / "trilogyModels.ts"

IMPORT_RE = re.compile(r"^import \.\.(\w+)(?: as \w+)?;", re.M)


def bundled_aliases() -> set[str]:
    return set(re.findall(r"alias: '([\w.]+)'", BUNDLE.read_text(encoding="utf-8")))


def city_models() -> list[Path]:
    return sorted(RAW_DIR.glob("*/*_tree_info.preql")) + sorted(RAW_DIR.glob("*/*_landmarks.preql"))


@pytest.mark.parametrize("model", city_models(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_every_shared_import_is_bundled(model: Path):
    missing = set(IMPORT_RE.findall(model.read_text(encoding="utf-8"))) - bundled_aliases()
    assert not missing, (
        f"{model.parent.name}/{model.name} imports {sorted(missing)}, which "
        "src/src/trilogyModels.ts does not send to the resolver"
    )

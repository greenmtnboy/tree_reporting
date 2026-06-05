#!/usr/bin/env python3
"""Pre-install DuckDB community extensions serially, before the parallel refresh.

Why this exists
---------------
`trilogy refresh` opens several DuckDB connections in parallel (see the
`parallelism` setting in trilogy.toml). Every connection runs the same preamble
to wire up the `uv_run` macro:

    INSTALL shellfs FROM community;
    INSTALL arrow   FROM community;
    LOAD   shellfs;
    LOAD   arrow;

When the local extension cache (``~/.duckdb/extensions/<ver>/<platform>/``) is
empty -- as it always is on a fresh CI runner -- those concurrent ``INSTALL``s
race to download and atomically rename the *same* files into the *same* shared
directory, producing the intermittent failure that breaks the refresh:

    IO Error: Could not remove file ".../arrow.duckdb_extension":
              No such file or directory

Running the installs once, serially, up front populates the cache so that the
parallel connections only have to ``LOAD`` already-present extensions (which is
safe to do concurrently). It also turns an opaque mid-refresh crash into a
fast, clearly-labelled failure if a community extension is genuinely
unavailable for the resolved DuckDB version.

This must use the same `duckdb` Python package (and therefore the same
``~/.duckdb`` home) that trilogy uses, so run it in the same environment.
"""

from __future__ import annotations

import sys
import time

import duckdb

# Order matters: `arrow` performs a nested load of `nanoarrow` on DuckDB >=1.5,
# so install `nanoarrow` first to guarantee it is cached. On versions where
# `nanoarrow` is not published (<=1.4.x) the install is skipped with a warning
# and the run continues -- `arrow` bundles what it needs there.
EXTENSIONS = ("shellfs", "nanoarrow", "arrow")

# The two extensions the refresh preamble actually LOADs. If either cannot be
# loaded after install, fail loudly here rather than deep inside trilogy.
REQUIRED_LOADS = ("shellfs", "arrow")


def install(con: duckdb.DuckDBPyConnection, ext: str, *, retries: int = 4, backoff: float = 3.0) -> bool:
    """INSTALL one community extension with bounded retry on transient failures.

    Returns True on success, False if it could not be installed (e.g. the
    extension is not published for this DuckDB version / platform). A False here
    is only fatal if the extension is in REQUIRED_LOADS (checked later via LOAD).
    """
    for attempt in range(1, retries + 1):
        try:
            con.execute(f"INSTALL {ext} FROM community")
            print(f"  INSTALL {ext}: ok", file=sys.stderr)
            return True
        except Exception as exc:  # noqa: BLE001 - any failure is retry-worthy
            msg = str(exc).splitlines()[0]
            if attempt == retries:
                print(f"  INSTALL {ext}: giving up after {retries} attempts: {msg}", file=sys.stderr)
                return False
            wait = backoff * attempt
            print(
                f"  INSTALL {ext}: attempt {attempt}/{retries} failed ({msg}); "
                f"retrying in {wait:.0f}s",
                file=sys.stderr,
            )
            time.sleep(wait)
    return False


def main() -> int:
    print(f"DuckDB {duckdb.__version__}: pre-warming community extensions", file=sys.stderr)
    con = duckdb.connect()

    for ext in EXTENSIONS:
        install(con, ext)

    # Verify the extensions the refresh preamble depends on can actually load.
    failures: list[tuple[str, str]] = []
    for ext in REQUIRED_LOADS:
        try:
            con.execute(f"LOAD {ext}")
            print(f"  LOAD {ext}: ok", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            failures.append((ext, str(exc).splitlines()[0]))

    if failures:
        for ext, msg in failures:
            print(f"FATAL: LOAD {ext} failed: {msg}", file=sys.stderr)
        print(
            "Required community extension(s) unavailable for this DuckDB version. "
            "Check the duckdb pin in requirements.txt.",
            file=sys.stderr,
        )
        return 1

    print("Community extensions cached and loadable.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

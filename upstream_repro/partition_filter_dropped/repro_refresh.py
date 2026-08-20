# /// script
# requires-python = ">=3.12"
# dependencies = ["pytrilogy==0.3.335"]
# ///
"""Reproduce the merge-into persist projection loss (the dbh-imputation bug).

    uv run repro_refresh.py [model.preql] [target_datasource_id]

Defaults to repro.preql / out_a. Drives the same planner the CLI's
`trilogy refresh --dry-run -f <target>` uses (create_refresh_plan /
execute_refresh_plan with dry_run=True). Fully offline: root datasources are
inline `query` blocks and the run is dry, so nothing external is read.

Expected: dry-run prints persist SQL whose projection includes `measure`.
Actual, by pinned version (uv run --no-project --with pytrilogy==X python
repro_refresh.py):
  <=0.3.315         correct SQL, `measure` present
  0.3.316-0.3.330   "OK", but `measure` silently missing from the projection
  0.3.331+          UnresolvableQueryException: "the plan's final projection
                    has no source for measure ... would shift every later
                    column into the wrong field"
"""

import sys
import traceback
from pathlib import Path

from trilogy import Dialects, Environment
from trilogy.execution.state import (
    RefreshPolicy,
    create_refresh_plan,
    execute_refresh_plan,
)

MODEL = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent / "repro.preql").resolve()
TARGET = sys.argv[2] if len(sys.argv) > 2 else "out_a"

env = Environment(working_path=MODEL.parent)
executor = Dialects.DUCK_DB.default_executor(environment=env)
executor.parse_text(MODEL.read_text(encoding="utf-8"), root=MODEL)

skip = {
    ds_id
    for ds_id, ds in executor.environment.datasources.items()
    if ds_id != TARGET and not ds.is_root
}
print(f"target: {TARGET}\nskipping {len(skip)} other managed datasource(s)\n", flush=True)

try:
    plan = create_refresh_plan(
        executor,
        policy=RefreshPolicy(force_sources={TARGET}),
        skip_datasources=skip,
    )
    print(f"planned: {plan.stale_count} stale", flush=True)
    result = execute_refresh_plan(
        executor,
        plan,
        on_refresh_query=lambda ds_id, sql: print(f"\n--- SQL for {ds_id} ---\n{sql}"),
        dry_run=True,
    )
    print(f"\nOK: {result}")
except Exception as e:
    print(f"\nFAILED: {type(e).__name__}: {e}")
    traceback.print_exc(limit=10)
    sys.exit(1)

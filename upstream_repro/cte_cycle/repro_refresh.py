"""Reproduce the refresh-time CTE cycle for one materialized datasource.

Drives the same planner the CLI uses (create_refresh_plan / execute_refresh_plan
with dry_run=True), restricted to a single target the way directory mode is:
every other managed datasource is `skip_datasources`, because in a directory
run each city's own script owns and refreshes its own asset first.

    python repro_refresh.py <model.preql> <target_datasource_id>

reorder_ctes is instrumented so a cycle prints its members instead of only
"CTE dependency graph contains a cycle".
"""

import sys
import time
import traceback
from pathlib import Path

from trilogy import Dialects, Environment
from trilogy.core import optimization
from trilogy.dialect.config import DuckDBConfig
from trilogy.execution.state import (
    RefreshPolicy,
    create_refresh_plan,
    execute_refresh_plan,
)

MODEL = Path(sys.argv[1]).resolve()
TARGET = sys.argv[2]

_original = optimization.reorder_ctes


def _edges(ctes):
    mapping = {c.name: c for c in ctes}
    return {
        cte.name: sorted(
            {
                p.name
                for p in [
                    *cte.dependency_nodes(),
                    *optimization.subquery_sources(cte, mapping),
                ]
                if p.name in mapping
            }
        )
        for cte in ctes
    }


def instrumented(input):
    try:
        return _original(input)
    except ValueError:
        edges = _edges(input)
        remaining = dict(edges)
        changed = True
        while changed:
            changed = False
            for name in list(remaining):
                if all(parent not in remaining for parent in remaining[name]):
                    del remaining[name]
                    changed = True
        mapping = {c.name: c for c in input}
        print(f"\n=== CYCLE: {len(remaining)} of {len(input)} CTEs ===", flush=True)
        for name in sorted(remaining):
            cte = mapping[name]
            base = getattr(cte, "base_name", "") or ""
            print(f"  {name} [{type(cte).__name__}{f' <- {base}' if base else ''}]")
            outputs = sorted(c.address for c in cte.output_columns)
            print(f"      outputs: {outputs[:8]}")
            for parent in remaining[name]:
                print(f"      * cycles with {parent}")
            deps = [p for p in edges[name] if p not in remaining]
            if deps:
                print(f"      depends on {deps}")
        raise


optimization.reorder_ctes = instrumented
for module in list(sys.modules.values()):
    if getattr(module, "reorder_ctes", None) is _original:
        module.reorder_ctes = instrumented

env = Environment(working_path=MODEL.parent)
executor = Dialects.DUCK_DB.default_executor(
    environment=env,
    # Mirrors data/trilogy.toml [engine.config]; the python-script roots are
    # unreadable without it.
    conf=DuckDBConfig(
        enable_python_datasources=True, enable_gcs=True, enable_spatial=True
    ),
)
executor.parse_text(MODEL.read_text(encoding="utf-8"), root=MODEL)

skip = {
    ds_id
    for ds_id, ds in executor.environment.datasources.items()
    if ds_id != TARGET and not ds.is_root
}
print(f"target: {TARGET}\nskipping {len(skip)} other managed datasource(s)\n", flush=True)

start = time.time()
try:
    plan = create_refresh_plan(
        executor,
        policy=RefreshPolicy(force_sources={TARGET}),
        skip_datasources=skip,
    )
    print(f"planned in {time.time() - start:.1f}s: {plan.stale_count} stale", flush=True)
    result = execute_refresh_plan(
        executor,
        plan,
        on_refresh_query=lambda ds_id, sql: print(f"\n--- SQL for {ds_id} ---\n{sql[:800]}"),
        dry_run=True,
    )
    print(f"OK in {time.time() - start:.1f}s: {result}")
except Exception as e:
    print(f"\nFAILED in {time.time() - start:.1f}s: {type(e).__name__}: {str(e)[:600]}")
    traceback.print_exc(limit=10)
    sys.exit(1)

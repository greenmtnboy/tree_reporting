# The self-contained HTML/JavaScript template intentionally has lines that are
# more readable above the Python line-length limit.
# ruff: noqa: E501

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pandas as pd
from pyproj import Transformer

from urban_tree_ml.config import ProjectConfig

VEGETATION_HEURISTIC_PROFILE: dict[str, object] = {
    "id": "naip-rgbn-conservative-gray-v1",
    "label": "Conservative gray + low NIR",
    "outer_radius_px": 6,
    "inner_radius_px": 3,
    "ndvi_p90_max": -0.04,
    "gray_fraction_min": 0.5,
    "action_status": "uncertain",
}


def _write_json_atomic(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _vegetation_features(
    raw: np.ndarray,
    x: float,
    y: float,
    profile: dict[str, object] = VEGETATION_HEURISTIC_PROFILE,
) -> dict[str, object]:
    if raw.ndim != 3 or raw.shape[0] < 4:
        raise ValueError("vegetation heuristics require RGB-NIR imagery")
    red, green, blue, nir = raw[:4].astype(np.float32)
    ndvi = (nir - red) / np.maximum(nir + red, 1.0)
    maximum = np.maximum.reduce([red, green, blue])
    minimum = np.minimum.reduce([red, green, blue])
    saturation = (maximum - minimum) / np.maximum(maximum, 1.0)
    brightness = (red + green + blue) / 3.0
    rows, columns = np.ogrid[: raw.shape[1], : raw.shape[2]]
    outer_radius = int(profile["outer_radius_px"])
    inner_radius = int(profile["inner_radius_px"])
    outer = (columns - x) ** 2 + (rows - y) ** 2 <= outer_radius**2
    inner = (columns - x) ** 2 + (rows - y) ** 2 <= inner_radius**2
    ndvi_p90 = float(np.percentile(ndvi[outer], 90))
    gray_fraction = float(np.mean((saturation[inner] < 0.12) & (brightness[inner] > 45)))
    candidate = ndvi_p90 < float(profile["ndvi_p90_max"])
    candidate = candidate and gray_fraction > float(profile["gray_fraction_min"])
    return {
        "profile_id": profile["id"],
        "ndvi_p90": ndvi_p90,
        "gray_fraction": gray_fraction,
        "candidate": candidate,
    }


def _attach_coordinate_stack_sizes(samples: list[dict[str, object]]) -> tuple[int, int]:
    coordinate_counts = Counter(
        (float(sample["longitude"]), float(sample["latitude"])) for sample in samples
    )
    for sample in samples:
        key = (float(sample["longitude"]), float(sample["latitude"]))
        sample["coordinate_stack_size"] = coordinate_counts[key]
    stack_sizes = [size for size in coordinate_counts.values() if size > 1]
    return len(stack_sizes), sum(stack_sizes)


def _diverse_order(frame: pd.DataFrame, seed: int) -> pd.DataFrame:
    """Prioritize taxonomic, spatial, size, and split diversity deterministically."""
    candidates = frame.copy()
    rng = np.random.default_rng(seed)
    candidates["_random"] = rng.random(len(candidates))
    dbh = pd.to_numeric(candidates["diameter_at_breast_height"], errors="coerce")
    quantiles = min(4, int(dbh.nunique()))
    if quantiles:
        candidates["_dbh_bin"] = (
            pd.qcut(
                dbh,
                q=quantiles,
                labels=False,
                duplicates="drop",
            )
            .fillna(-1)
            .astype(int)
        )
    else:
        candidates["_dbh_bin"] = -1

    buckets: list[list[int]] = []
    for _, group in candidates.groupby(["split", "_dbh_bin"], sort=True, observed=True):
        randomized = group.sort_values("_random")
        prioritized = pd.concat(
            [
                randomized.drop_duplicates("species"),
                randomized.drop_duplicates(["split_block_x", "split_block_y"]),
                randomized,
            ]
        ).drop_duplicates("tree_id")
        buckets.append(prioritized.index.tolist())

    ordered_indices: list[int] = []
    while buckets:
        remaining: list[list[int]] = []
        for bucket in buckets:
            if bucket:
                ordered_indices.append(bucket.pop(0))
            if bucket:
                remaining.append(bucket)
        buckets = remaining
    return candidates.loc[ordered_indices]


def _rgb_preview(raw: np.ndarray, input_scale: float) -> np.ndarray:
    rgb = np.moveaxis(raw[:3].astype(np.float32) * input_scale, 0, 2)
    return np.rint(np.clip(rgb, 0, 1) * 255).astype(np.uint8)


def _mosaic_context(
    source_path: Path,
    xs: np.ndarray,
    ys: np.ndarray,
) -> tuple[list[list[str]], list[float | None], int]:
    manifest_path = source_path.with_suffix(".manifest.json")
    if not manifest_path.exists():
        return ([[] for _ in xs], [None for _ in xs], 0)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sources = [
        source
        for source in manifest.get("sources", [])
        if isinstance(source, dict)
        and isinstance(source.get("bounds"), list)
        and len(source["bounds"]) == 4
    ]
    if not sources:
        return ([[] for _ in xs], [None for _ in xs], 0)

    bounds = [tuple(float(value) for value in source["bounds"]) for source in sources]
    global_left = min(bound[0] for bound in bounds)
    global_bottom = min(bound[1] for bound in bounds)
    global_right = max(bound[2] for bound in bounds)
    global_top = max(bound[3] for bound in bounds)
    internal_x = sorted(
        {
            edge
            for bound in bounds
            for edge in (bound[0], bound[2])
            if not np.isclose(edge, global_left) and not np.isclose(edge, global_right)
        }
    )
    internal_y = sorted(
        {
            edge
            for bound in bounds
            for edge in (bound[1], bound[3])
            if not np.isclose(edge, global_bottom) and not np.isclose(edge, global_top)
        }
    )
    item_ids: list[list[str]] = []
    seam_distances: list[float | None] = []
    for x, y in zip(xs, ys, strict=True):
        covering = [
            str(source.get("item_id", "unknown"))
            for source, bound in zip(sources, bounds, strict=True)
            if bound[0] <= x <= bound[2] and bound[1] <= y <= bound[3]
        ]
        distances = [abs(float(x) - edge) for edge in internal_x]
        distances.extend(abs(float(y) - edge) for edge in internal_y)
        item_ids.append(covering)
        seam_distances.append(min(distances) if distances else None)
    return item_ids, seam_distances, len(sources)


def _render_grouped_registration_html(
    samples: list[dict[str, object]],
    metadata: dict[str, object],
    scenes: list[dict[str, object]],
) -> str:
    payload = json.dumps(samples, separators=(",", ":")).replace("<", "\\u003c")
    scene_payload = json.dumps(scenes, separators=(",", ":")).replace("<", "\\u003c")
    metadata_payload = json.dumps(metadata, separators=(",", ":")).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Urban tree grouped registration review</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #101613; color: #eef5ef; }}
    body.modal-open {{ overflow: hidden; }}
    header {{ position: sticky; top: 0; z-index: 20; padding: 16px 22px; background: #17211cf2;
      backdrop-filter: blur(12px); border-bottom: 1px solid #33453a; }}
    h1 {{ margin: 0 0 5px; font-size: 21px; }}
    .lede {{ margin: 0; color: #b7c8bc; font-size: 14px; }}
    .guide {{ margin-top: 9px; color: #c5d4ca; font-size: 13px; }}
    .guide summary {{ width: fit-content; color: #dce9df; cursor: pointer; }}
    .guide ul {{ max-width: 1050px; margin: 8px 0 0; padding-left: 20px; line-height: 1.45; }}
    .guide li + li {{ margin-top: 3px; }}
    .toolbar {{ display: flex; flex-wrap: wrap; gap: 9px; margin-top: 12px; align-items: center; }}
    button, select, .file-label {{ border: 1px solid #496252; border-radius: 7px; padding: 7px 10px;
      color: #eef5ef; background: #203027; cursor: pointer; font: inherit; }}
    .toolbar button, .toolbar select, .toolbar .file-label {{ display: inline-flex; align-items: center;
      justify-content: center; min-height: 38px; }}
    button:hover, .file-label:hover {{ background: #2b4234; }}
    input[type=file] {{ display: none; }}
    .toggle-label {{ display: inline-flex; align-items: center; gap: 7px; min-height: 38px;
      padding: 7px 10px; border: 1px solid #496252; border-radius: 7px; background: #203027; }}
    .toggle-label input {{ accent-color: #d5ebda; }}
    #sync {{ color: #9eb6a5; font-size: 12px; }}
    #stats {{ margin-left: auto; color: #c8d7cc; font-variant-numeric: tabular-nums; }}
    main {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(430px, 1fr));
      gap: 16px; padding: 18px; align-items: start; }}
    .card {{ overflow: hidden; border: 1px solid #30443a; border-radius: 10px; background: #18231d; }}
    .card.completed {{ border-color: #78b68b; box-shadow: 0 0 0 1px #78b68b55; }}
    .card.completed .card-head {{ background: #203229; }}
    .card-head {{ padding: 10px 12px; display: flex; justify-content: space-between; gap: 10px; }}
    .identity {{ min-width: 0; }}
    .identity strong, .identity span {{ display: block; }}
    .identity span, .details {{ color: #aebfb3; font-size: 12px; }}
    .badges {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 5px; }}
    .badges button {{ align-self: start; min-height: 28px; padding: 3px 8px; font-size: 11px; }}
    .badge {{ align-self: start; padding: 3px 7px; border-radius: 999px; background: #293a31;
      font-size: 11px; text-transform: uppercase; }}
    .seam {{ background: #614d22; color: #ffe5a0; cursor: help; }}
    .stack-badge {{ background: #493953; color: #ead8f3; cursor: help; }}
    .scene-previous, .scene-next, .done-button {{ min-width: 92px; }}
    .expand-button {{ min-width: 82px; text-transform: uppercase; }}
    .done-button.active, .select-all-button.active {{ background: #d5ebda; border-color: #d5ebda; color: #102016; }}
    .heuristic-button.preview {{ border-color: #ffcf66; color: #ffdf91; }}
    .heuristic-button.applied {{ border-color: #ff9f43; color: #ffd0a1; }}
    .fullscreen-only {{ display: none; }}
    .image-wrap {{ position: relative; width: 100%; aspect-ratio: 1; background: #050806; cursor: crosshair; }}
    .image-wrap img {{ display: block; width: 100%; height: 100%; object-fit: contain;
      image-rendering: auto; user-select: none; }}
    .tree-marker {{ position: absolute; width: 22px; height: 22px; padding: 0; border: 2px solid #36e5f2;
      border-radius: 50%; transform: translate(-50%, -50%); background: #07110dcc; color: #f3ffff;
      font-size: 9px; font-weight: 800; line-height: 17px; text-align: center; z-index: 3; }}
    .tree-marker:hover {{ background: #152c24; transform: translate(-50%, -50%) scale(1.15); }}
    .tree-marker[data-status="offset"] {{ border-color: #ffe34e; color: #ffe34e; }}
    .tree-marker[data-status="not-tree"] {{ border-color: #ff5757; color: #ff9c9c; }}
    .tree-marker[data-status="uncertain"] {{ border-color: #ff9f43; color: #ffd0a1; }}
    .tree-marker[data-status="duplicate"] {{ border-color: #c084fc; color: #e9d5ff; }}
    .tree-species-label {{ display: none; position: absolute; z-index: 2; max-width: 150px;
      padding: 2px 5px; overflow: hidden; transform: translate(14px, calc(-50% + var(--label-offset)));
      border-radius: 4px; background: #07110da8; color: #d7e5da; font-size: 10px; line-height: 1.2;
      text-overflow: ellipsis; white-space: nowrap; pointer-events: none; text-shadow: 0 1px 2px #000; }}
    .tree-species-label.active {{ z-index: 6; background: #e5f3e9e8; color: #102016; font-weight: 700;
      text-shadow: none; }}
    .tree-species-label[data-status="duplicate"] {{ color: #e9d5ff; box-shadow: inset 2px 0 #c084fc; }}
    .tree-species-label.active[data-status="duplicate"] {{ color: #44215f; }}
    .tree-marker.stacked, .tree-choice.stacked {{ border-style: dashed; border-color: #a38aaa;
      color: #d9c4df; opacity: .72; }}
    body:not(.show-stacks) .tree-marker.stacked, body:not(.show-stacks) .tree-choice.stacked,
    body:not(.show-stacks) .tree-species-label.stacked {{ display: none; }}
    .tree-marker.heuristic-suggestion {{ box-shadow: 0 0 0 3px #ffcf66, 0 0 0 5px #17140d; z-index: 6; }}
    .tree-marker.active {{ box-shadow: 0 0 0 3px #fff, 0 0 0 5px #102016; z-index: 5; }}
    .image-wrap.multi-select {{ cursor: not-allowed; }}
    .picked {{ display: none; position: absolute; width: 18px; height: 18px; border: 2px solid #ffe34e;
      transform: translate(-50%, -50%) rotate(45deg); pointer-events: none; z-index: 4;
      box-shadow: 0 0 0 1px #111; }}
    .street-view-camera {{ display: none; position: absolute; width: 46px; height: 46px;
      transform: translate(-50%, -50%) rotate(var(--camera-heading)); pointer-events: none; z-index: 7; }}
    .street-view-camera::before {{ content: ""; position: absolute; left: 13px; top: -18px;
      width: 0; height: 0; border-left: 10px solid transparent; border-right: 10px solid transparent;
      border-bottom: 31px solid #57d6ff88; filter: drop-shadow(0 0 2px #061217); }}
    .street-view-camera::after {{ content: ""; position: absolute; left: 15px; top: 15px;
      width: 16px; height: 16px; border: 3px solid #d8f7ff; border-radius: 50%;
      background: #1685a6; box-shadow: 0 0 0 2px #071317; }}
    .street-view-camera.outside {{ opacity: .72; }}
    .tree-list {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(38px, 1fr)); gap: 6px;
      max-height: 132px; padding: 9px 12px 0; overflow-y: auto; }}
    .tree-choice {{ width: 100%; min-width: 0; height: 32px; padding: 0; font-size: 11px; }}
    .tree-choice.active {{ background: #d5ebda; border-color: #d5ebda; color: #102016; }}
    .tree-choice[data-status="offset"] {{ border-color: #ffe34e; }}
    .tree-choice[data-status="not-tree"] {{ border-color: #ff5757; }}
    .tree-choice[data-status="uncertain"] {{ border-color: #ff9f43; }}
    .tree-choice[data-status="duplicate"] {{ border-color: #c084fc; }}
    .tree-choice.heuristic-suggestion {{ background: #594819; border-color: #ffcf66; color: #fff1bf; }}
    .details {{ padding: 9px 12px 0; line-height: 1.5; min-height: 70px; }}
    .selected-species {{ color: #eef5ef; font-size: 14px; font-weight: 700; }}
    .actions {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(82px, 1fr)); gap: 5px; padding: 10px 12px; }}
    .actions button {{ min-height: 40px; padding: 6px 3px; font-size: 12px; }}
    .actions button.active {{ background: #d5ebda; border-color: #d5ebda; color: #102016; }}
    .offset-hint {{ color: #ffe6a3; }}
    textarea {{ width: calc(100% - 24px); min-height: 48px; margin: 0 12px 12px; resize: vertical;
      border: 1px solid #3b5144; border-radius: 6px; padding: 7px; color: #eef5ef; background: #101713; }}
    .card.fullscreen {{ position: fixed; inset: 0; z-index: 100; display: grid; overflow: auto;
      grid-template-columns: minmax(0, 1fr) minmax(330px, 420px); grid-template-rows: auto auto auto auto 1fr;
      grid-template-areas: "head head" "image list" "image details" "image actions" "image note";
      border: 0; border-radius: 0; background: #101713; }}
    .card.fullscreen .card-head {{ grid-area: head; border-bottom: 1px solid #30443a; }}
    .card.fullscreen .fullscreen-only {{ display: inline-block; }}
    .card.fullscreen .tree-species-label {{ display: block; }}
    .card.fullscreen .image-wrap {{ grid-area: image; align-self: start; justify-self: center;
      width: min(calc(100vw - 440px), calc(100vh - 72px)); max-width: 100%; }}
    .card.fullscreen .tree-list {{ grid-area: list; padding-top: 14px; }}
    .card.fullscreen .details {{ grid-area: details; }}
    .card.fullscreen .actions {{ grid-area: actions; }}
    .card.fullscreen textarea {{ grid-area: note; align-self: start; }}
    .street-view-panel {{ display: none; min-width: 200px; min-height: 420px; overflow: hidden;
      border: 1px solid #30443a; border-radius: 8px; background: #0b110e; }}
    .street-view-head {{ display: flex; align-items: center; justify-content: space-between; gap: 8px;
      min-height: 42px; padding: 6px 8px; color: #c8d7cc; font-size: 12px; }}
    .street-view-head a {{ color: #b9e6c5; }}
    .street-view-head button {{ min-height: 28px; padding: 3px 8px; font-size: 11px; }}
    .street-view-frame-wrap {{ width: 100%; height: calc(100% - 42px); min-height: 378px; }}
    .street-view-frame {{ display: block; width: 100%; height: 100%; min-height: 378px; border: 0; }}
    .street-view-frame.locked {{ pointer-events: none; }}
    .card.fullscreen.street-view-open {{ grid-template-columns: minmax(300px, 1fr) minmax(300px, 1fr) minmax(330px, 400px);
      grid-template-areas: "head head head" "image street list" "image street details" "image street actions" "image street note"; }}
    .card.fullscreen.street-view-open .image-wrap {{ width: min(calc(50vw - 205px), calc(100vh - 72px)); }}
    .card.fullscreen.street-view-open .street-view-panel {{ grid-area: street; display: block; align-self: stretch; }}
    .hidden {{ display: none; }}
    @media (max-width: 1100px) {{
      .card.fullscreen.street-view-open {{ grid-template-columns: minmax(0, 1fr) minmax(330px, 420px);
        grid-template-areas: "head head" "street list" "street details" "street actions" "street note"; }}
      .card.fullscreen.street-view-open .image-wrap {{ display: none; }}
    }}
    @media (max-width: 850px) {{
      main {{ padding: 8px; grid-template-columns: 1fr; }} #stats {{ width: 100%; }}
      .card.fullscreen {{ display: block; }}
      .card.fullscreen .image-wrap {{ width: min(100vw, calc(100vh - 72px)); }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>Grouped registration review</h1>
    <p class="lede">Each numbered ring is one inventory tree. Select a ring, then click its apparent
      tree center to record an offset. Cyan = aligned, yellow = offset, red = not tree,
      orange = uncertain, purple = duplicate. Shift-click rings or numbers to select several.</p>
    <details class="guide">
      <summary>How should I classify ambiguous trees?</summary>
      <ul>
        <li><strong>Aligned:</strong> the ring plausibly belongs to the visible tree. Do not move a point merely from the trunk side to the canopy center in an angled image.</li>
        <li><strong>Offset:</strong> the same tree is clearly identifiable at a genuinely different location. Select its ring, then click that location.</li>
        <li><strong>Uncertain:</strong> use this when a small, shadowed, overhung, merged, or off-nadir tree cannot be located confidently. It will be excluded from supervision.</li>
        <li><strong>Not tree:</strong> use only when you are confident no matching tree existed when the imagery was captured. Unnumbered nearby trees are outside this inventory review.</li>
        <li><strong>Duplicate:</strong> the point repeats another numbered inventory record for the same physical tree. Keep one record aligned or offset, and mark only the extra record(s) duplicate.</li>
        <li><strong>Non-vegetation helper:</strong> “Check non-veg” previews conservative low-NIR gray candidates in gold. Review the highlights, then explicitly apply them as uncertain; they never become hard not-tree negatives.</li>
        <li><strong>Coordinate stacks:</strong> exact lat/lon stacks are hidden by default. Unresolved stacks are excluded by target collision handling; show them when you want to split resolvable records with explicit offsets.</li>
        <li><strong>Bulk review:</strong> Shift-click markers or numbered buttons to add or remove trees from the selection, or use <em>Select all</em>. A/N/U/D mark the selected trees aligned/not-tree/uncertain/duplicate in full-screen mode. Center marking and notes are disabled while several trees are selected.</li>
      </ul>
    </details>
    <div class="toolbar">
      <select id="split-filter"><option value="">All splits</option></select>
      <select id="coverage-filter"><option value="">All coverage</option><option value="seam">Tile seams</option>
        <option value="interior">Tile interiors</option></select>
      <select id="status-filter"><option value="">All statuses</option><option value="unreviewed">Unreviewed</option>
        <option value="aligned">Aligned</option><option value="offset">Offset</option>
        <option value="not-tree">Not tree</option><option value="uncertain">Uncertain</option>
        <option value="duplicate">Duplicate</option></select>
      <select id="scene-status-filter"><option value="">All images</option><option value="pending">To review</option>
        <option value="done">Done</option></select>
      <label class="toggle-label"><input id="show-stacks" type="checkbox"> <span id="stack-toggle-label">Show stacked</span></label>
      <button id="export">Export reviews</button>
      <label class="file-label" for="import">Import reviews</label><input id="import" type="file" accept="application/json">
      <button id="finalize">Finalize training feedback</button>
      <button id="clear">Reset all to aligned</button>
      <span id="sync">Loading saved reviews…</span><span id="stats"></span>
    </div>
  </header>
  <main id="cards"></main>
  <script>
    const samples = {payload};
    const scenes = {scene_payload};
    const metadata = {metadata_payload};
    const streetViewEmbedApiKey = null;
    const samplesById = Object.fromEntries(samples.map(sample => [sample.sample_id, sample]));
    const scenesById = Object.fromEntries(scenes.map(scene => [scene.scene_id, scene]));
    const isStacked = sample => Number(sample.coordinate_stack_size || 1) > 1;
    const stackedSamples = samples.filter(isStacked);
    const activeByScene = Object.fromEntries(scenes.map(scene => [scene.scene_id,
      scene.sample_ids.find(sampleId => !isStacked(samplesById[sampleId])) || scene.sample_ids[0]
    ]));
    const selectedByScene = Object.fromEntries(Object.entries(activeByScene).map(
      ([sceneId, sampleId]) => [sceneId, new Set([sampleId])]
    ));
    const storageKey = `urban-tree-registration:${{metadata.review_id}}`;
    const storedState = JSON.parse(localStorage.getItem(storageKey) || "{{}}");
    const hasWrappedState = storedState && typeof storedState === "object"
      && Object.prototype.hasOwnProperty.call(storedState, "reviews");
    let reviews = hasWrappedState ? (storedState.reviews || {{}}) : (storedState || {{}});
    let sceneReviews = hasWrappedState ? (storedState.scene_reviews || {{}}) : {{}};
    const suggestedByScene = {{}};
    const streetViewMetadataCache = new Map();
    let syncTimer = null;
    const cards = document.getElementById("cards");
    const splitFilter = document.getElementById("split-filter");
    const coverageFilter = document.getElementById("coverage-filter");
    const statusFilter = document.getElementById("status-filter");
    const sceneStatusFilter = document.getElementById("scene-status-filter");
    const showStacks = document.getElementById("show-stacks");
    document.getElementById("stack-toggle-label").textContent = `Show ${{stackedSamples.length}} stacked`;
    const statusOf = sampleId => (reviews[sampleId] || {{}}).status || "unreviewed";
    const withAlignedDefaults = source => Object.fromEntries(samples.map(sample => [
      sample.sample_id, {{status: "aligned", ...(source[sample.sample_id] || {{}})}}
    ]));
    const median = values => {{
      if (!values.length) return null;
      const sorted = [...values].sort((a, b) => a - b), middle = Math.floor(sorted.length / 2);
      return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
    }};
    const sampleTitle = sample => {{
      const dbh = sample.dbh_in == null ? "DBH unknown" : `${{sample.dbh_in.toFixed(1)}} in DBH`;
      const spectral = sample.vegetation_heuristic;
      const heuristic = spectral?.candidate
        ? ` · non-veg candidate (NDVI p90 ${{spectral.ndvi_p90.toFixed(2)}}, gray ${{Math.round(100 * spectral.gray_fraction)}}%)`
        : "";
      return `${{sample.species}} · ${{dbh}} · ${{sample.tree_id}} · ${{statusOf(sample.sample_id)}}${{heuristic}}`;
    }};
    async function syncReviews() {{
      clearTimeout(syncTimer);
      const sync = document.getElementById("sync");
      try {{
        const response = await fetch("/api/reviews", {{method: "PUT", headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{schema_version: 1, metadata, reviews, scene_reviews: sceneReviews}})}});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${{response.status}}`);
        sync.textContent = `Saved ${{result.reviews}} tree reviews · ${{result.completed_scenes}} images done · durable snapshot updated`;
        return true;
      }} catch (error) {{
        sync.textContent = location.protocol === "file:" ? "Local only — serve the UI to auto-save" : `Save failed: ${{error.message}}`;
        return false;
      }}
    }}
    function storeLocalState() {{
      localStorage.setItem(storageKey, JSON.stringify({{reviews, scene_reviews: sceneReviews}}));
    }}
    function persist() {{
      storeLocalState(); update();
      document.getElementById("sync").textContent = "Saving…";
      clearTimeout(syncTimer); syncTimer = setTimeout(syncReviews, 250);
    }}
    function selectionFor(sceneId) {{
      if (!selectedByScene[sceneId]?.size) {{
        selectedByScene[sceneId] = new Set([activeByScene[sceneId]]);
      }}
      return selectedByScene[sceneId];
    }}
    function setStatus(sceneId, status) {{
      selectionFor(sceneId).forEach(sampleId => {{
        const next = {{...(reviews[sampleId] || {{}}), status, source: "human"}};
        delete next.heuristic_id;
        ["image_x", "image_y", "east_m", "north_m"].forEach(field => delete next[field]);
        reviews[sampleId] = next;
      }});
      persist();
    }}
    function runVegetationHeuristic(sceneId) {{
      const scene = scenesById[sceneId];
      const sceneSamples = scene.sample_ids.map(sampleId => samplesById[sampleId]);
      const heuristicId = metadata.vegetation_heuristic?.id;
      const applied = sceneSamples.filter(sample => {{
        const review = reviews[sample.sample_id] || {{}};
        return review.source === "heuristic" && review.heuristic_id === heuristicId;
      }});
      if (applied.length) {{
        applied.forEach(sample => {{
          const next = {{...(reviews[sample.sample_id] || {{}}), status: "aligned"}};
          delete next.source; delete next.heuristic_id; reviews[sample.sample_id] = next;
        }});
        delete suggestedByScene[sceneId]; persist(); return;
      }}
      if (Object.prototype.hasOwnProperty.call(suggestedByScene, sceneId)) {{
        suggestedByScene[sceneId].filter(sampleId => statusOf(sampleId) === "aligned").forEach(sampleId => {{
          reviews[sampleId] = {{...(reviews[sampleId] || {{}}), status: "uncertain",
            source: "heuristic", heuristic_id: heuristicId}};
          ["image_x", "image_y", "east_m", "north_m"].forEach(field => delete reviews[sampleId][field]);
        }});
        delete suggestedByScene[sceneId]; persist(); return;
      }}
      suggestedByScene[sceneId] = sceneSamples.filter(sample =>
        !isStacked(sample) && sample.vegetation_heuristic?.candidate
          && statusOf(sample.sample_id) === "aligned"
      ).map(sample => sample.sample_id);
      update();
    }}
    function selectSample(sceneId, sampleId, extend = false) {{
      const selected = selectionFor(sceneId);
      if (!extend) {{
        selected.clear(); selected.add(sampleId);
      }} else if (selected.has(sampleId) && selected.size > 1) {{
        selected.delete(sampleId);
      }} else {{
        selected.add(sampleId);
      }}
      activeByScene[sceneId] = selected.has(sampleId) ? sampleId : [...selected][0];
      update();
    }}
    function toggleSelectAll(sceneId) {{
      const available = scenesById[sceneId].sample_ids.filter(
        sampleId => showStacks.checked || !isStacked(samplesById[sampleId])
      );
      if (!available.length) return;
      const selected = selectionFor(sceneId);
      const allSelected = available.length > 1 && available.every(sampleId => selected.has(sampleId));
      if (allSelected) {{
        selectedByScene[sceneId] = new Set([activeByScene[sceneId]]);
      }} else {{
        selectedByScene[sceneId] = new Set(available);
        if (!selectedByScene[sceneId].has(activeByScene[sceneId])) {{
          activeByScene[sceneId] = available[0];
        }}
      }}
      update();
    }}
    function setSceneDone(sceneId, done) {{
      if (done) sceneReviews[sceneId] = {{done: true, completed_at: new Date().toISOString()}};
      else delete sceneReviews[sceneId];
      persist();
    }}
    function externalStreetViewUrl(sample) {{
      const parameters = new URLSearchParams({{
        api: "1", map_action: "pano", viewpoint: `${{sample.latitude}},${{sample.longitude}}`
      }});
      return `https://www.google.com/maps/@?${{parameters}}`;
    }}
    function embeddedStreetViewUrl(sample, panorama = null) {{
      const parameters = new URLSearchParams({{
        key: streetViewEmbedApiKey,
        location: `${{sample.latitude}},${{sample.longitude}}`,
        radius: "50",
        source: "outdoor",
        pitch: "0",
        fov: "70",
      }});
      if (panorama?.pano_id) parameters.set("pano", panorama.pano_id);
      if (panorama?.heading != null) parameters.set("heading", panorama.heading.toFixed(2));
      return `https://www.google.com/maps/embed/v1/streetview?${{parameters}}`;
    }}
    function bearingDegrees(origin, destination) {{
      const radians = value => value * Math.PI / 180;
      const originLatitude = radians(origin.lat), destinationLatitude = radians(destination.lat);
      const longitudeDelta = radians(destination.lng - origin.lng);
      const y = Math.sin(longitudeDelta) * Math.cos(destinationLatitude);
      const x = Math.cos(originLatitude) * Math.sin(destinationLatitude)
        - Math.sin(originLatitude) * Math.cos(destinationLatitude) * Math.cos(longitudeDelta);
      return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
    }}
    function cameraPixel(sample, camera) {{
      const earthRadiusM = 6378137;
      const latitudeDelta = (camera.lat - sample.latitude) * Math.PI / 180;
      const longitudeDelta = (camera.lng - sample.longitude) * Math.PI / 180;
      const meanLatitude = (camera.lat + sample.latitude) * Math.PI / 360;
      const eastM = earthRadiusM * longitudeDelta * Math.cos(meanLatitude);
      const northM = earthRadiusM * latitudeDelta;
      const determinant = sample.transform_a * sample.transform_e - sample.transform_b * sample.transform_d;
      if (Math.abs(determinant) < 1e-12) return null;
      return {{
        x: sample.target_x + (sample.transform_e * eastM - sample.transform_b * northM) / determinant,
        y: sample.target_y + (-sample.transform_d * eastM + sample.transform_a * northM) / determinant,
      }};
    }}
    function renderStreetViewCamera(card, sample, panorama) {{
      const marker = card.querySelector(".street-view-camera");
      const pixel = cameraPixel(sample, panorama.location);
      if (!pixel) {{ marker.style.display = "none"; return; }}
      const scene = scenesById[card.dataset.scene];
      const outside = pixel.x < 0 || pixel.y < 0 || pixel.x > scene.image_width || pixel.y > scene.image_height;
      const clampedX = Math.max(8, Math.min(scene.image_width - 8, pixel.x));
      const clampedY = Math.max(8, Math.min(scene.image_height - 8, pixel.y));
      marker.style.display = "block";
      marker.style.left = `${{100 * clampedX / scene.image_width}}%`;
      marker.style.top = `${{100 * clampedY / scene.image_height}}%`;
      marker.style.setProperty("--camera-heading", `${{panorama.heading}}deg`);
      marker.classList.toggle("outside", outside);
      marker.title = `Street View camera${{outside ? " (outside tile; clamped to edge)" : ""}} · `
        + `${{panorama.heading.toFixed(0)}}° toward selected tree · ${{panorama.location.lat.toFixed(6)}}, `
        + panorama.location.lng.toFixed(6);
    }}
    async function resolveStreetViewPanorama(sample) {{
      if (streetViewMetadataCache.has(sample.sample_id)) return streetViewMetadataCache.get(sample.sample_id);
      const request = (async () => {{
        const parameters = new URLSearchParams({{
          location: `${{sample.latitude}},${{sample.longitude}}`,
          source: "outdoor",
          key: streetViewEmbedApiKey,
        }});
        const response = await fetch(`https://maps.googleapis.com/maps/api/streetview/metadata?${{parameters}}`,
          {{referrerPolicy: "strict-origin-when-cross-origin"}});
        if (!response.ok) throw new Error(`metadata HTTP ${{response.status}}`);
        const result = await response.json();
        if (result.status !== "OK" || !result.location || !result.pano_id) {{
          throw new Error(result.error_message || result.status || "no nearby panorama");
        }}
        const location = {{lat: Number(result.location.lat), lng: Number(result.location.lng)}};
        return {{...result, location, heading: bearingDegrees(location,
          {{lat: sample.latitude, lng: sample.longitude}})}};
      }})();
      streetViewMetadataCache.set(sample.sample_id, request);
      try {{ return await request; }} catch (error) {{ streetViewMetadataCache.delete(sample.sample_id); throw error; }}
    }}
    function closeStreetView(card) {{
      card.classList.remove("street-view-open");
      const frame = card.querySelector(".street-view-frame");
      frame.src = "about:blank";
      delete frame.dataset.viewpoint;
      frame.classList.add("locked");
      const interaction = card.querySelector(".street-view-interaction");
      interaction.textContent = "Unlock";
      const locationLabel = card.querySelector(".street-view-location");
      delete locationLabel.dataset.initialHeading;
      card.querySelector(".street-view-camera").style.display = "none";
    }}
    async function refreshStreetView(card, sample) {{
      const frame = card.querySelector(".street-view-frame");
      if (frame.dataset.viewpoint === sample.sample_id) return;
      frame.dataset.viewpoint = sample.sample_id;
      frame.src = "about:blank";
      const locationLabel = card.querySelector(".street-view-location");
      locationLabel.textContent = "Finding nearest street camera…";
      locationLabel.title = "";
      delete locationLabel.dataset.initialHeading;
      card.querySelector(".street-view-external").href = externalStreetViewUrl(sample);
      card.querySelector(".street-view-camera").style.display = "none";
      try {{
        const panorama = await resolveStreetViewPanorama(sample);
        if (frame.dataset.viewpoint !== sample.sample_id || !card.classList.contains("street-view-open")) return;
        frame.src = embeddedStreetViewUrl(sample, panorama);
        locationLabel.textContent = `Camera ${{panorama.heading.toFixed(0)}}° toward tree`;
        locationLabel.dataset.initialHeading = panorama.heading.toFixed(0);
        renderStreetViewCamera(card, sample, panorama);
      }} catch (error) {{
        if (frame.dataset.viewpoint !== sample.sample_id || !card.classList.contains("street-view-open")) return;
        frame.src = embeddedStreetViewUrl(sample);
        locationLabel.textContent = `Nearest panorama · camera metadata unavailable`;
        locationLabel.title = error.message;
      }}
    }}
    function toggleStreetViewInteraction(card) {{
      const frame = card.querySelector(".street-view-frame");
      const nextLocked = !frame.classList.contains("locked");
      frame.classList.toggle("locked", nextLocked);
      const button = card.querySelector(".street-view-interaction");
      button.textContent = nextLocked ? "Unlock" : "Lock view";
      const locationLabel = card.querySelector(".street-view-location");
      if (locationLabel.dataset.initialHeading) {{
        locationLabel.textContent = nextLocked
          ? `Camera ${{locationLabel.dataset.initialHeading}}° toward tree`
          : `Initial ${{locationLabel.dataset.initialHeading}}° · live view may differ`;
      }}
      button.title = nextLocked
        ? "Enable panorama controls; the overhead cone shows only the initial view"
        : "Lock panorama controls so the overhead heading stays representative";
    }}
    function setExpanded(card, expanded) {{
      document.querySelectorAll(".card.fullscreen").forEach(openCard => {{
        closeStreetView(openCard);
        openCard.classList.remove("fullscreen");
        const openButton = openCard.querySelector(".expand-button");
        openButton.textContent = "Full screen"; openButton.setAttribute("aria-expanded", "false");
      }});
      if (expanded) {{
        card.classList.add("fullscreen");
        const button = card.querySelector(".expand-button");
        button.textContent = "Close"; button.setAttribute("aria-expanded", "true"); card.scrollTop = 0;
        if (streetViewEmbedApiKey && selectionFor(card.dataset.scene).size === 1) {{
          card.classList.add("street-view-open");
          refreshStreetView(card, samplesById[activeByScene[card.dataset.scene]]);
        }}
      }}
      document.body.classList.toggle("modal-open", Boolean(document.querySelector(".card.fullscreen")));
    }}
    function moveScene(card, direction) {{
      const visibleCards = [...document.querySelectorAll(".card:not(.hidden)")];
      if (visibleCards.length < 2) return;
      const currentIndex = visibleCards.indexOf(card);
      const nextIndex = (currentIndex + direction + visibleCards.length) % visibleCards.length;
      setExpanded(visibleCards[nextIndex], true);
    }}
    function update() {{
      let visibleScenes = 0;
      const completedScenes = scenes.filter(scene => Boolean(sceneReviews[scene.scene_id]?.done)).length;
      const east = [], north = [];
      samples.forEach(sample => {{
        const review = reviews[sample.sample_id] || {{}};
        if (review.status === "offset" && review.east_m != null) {{ east.push(review.east_m); north.push(review.north_m); }}
      }});
      document.querySelectorAll(".card").forEach(card => {{
        const scene = scenesById[card.dataset.scene];
        const sceneDone = Boolean(sceneReviews[scene.scene_id]?.done);
        const sceneSamples = scene.sample_ids.map(sampleId => samplesById[sampleId]);
        const selectableSamples = sceneSamples.filter(sample => showStacks.checked || !isStacked(sample));
        const statusMatches = selectableSamples.filter(sample => !statusFilter.value
          || (statusFilter.value === "unreviewed"
            ? !sceneDone : statusOf(sample.sample_id) === statusFilter.value));
        const visible = (!splitFilter.value || scene.splits.includes(splitFilter.value))
          && (!coverageFilter.value || (coverageFilter.value === "seam") === Boolean(scene.seam_priority))
          && (!sceneStatusFilter.value || (sceneStatusFilter.value === "done") === sceneDone)
          && statusMatches.length > 0;
        if (!visible && card.classList.contains("fullscreen")) setExpanded(card, false);
        card.classList.toggle("hidden", !visible);
        if (visible) visibleScenes += 1;
        let activeChanged = false;
        if (!statusMatches.some(sample => sample.sample_id === activeByScene[scene.scene_id])) {{
          activeByScene[scene.scene_id] = statusMatches[0]?.sample_id
            || selectableSamples[0]?.sample_id || scene.sample_ids[0];
          activeChanged = true;
        }}
        const activeId = activeByScene[scene.scene_id];
        const selected = samplesById[activeId];
        const selectedReview = reviews[activeId] || {{}};
        const selectableIds = new Set(selectableSamples.map(sample => sample.sample_id));
        const sceneSelection = selectionFor(scene.scene_id);
        [...sceneSelection].filter(sampleId => !selectableIds.has(sampleId)).forEach(
          sampleId => sceneSelection.delete(sampleId)
        );
        if (activeChanged || !sceneSelection.size) {{
          sceneSelection.clear(); sceneSelection.add(activeId);
        }} else if (!sceneSelection.has(activeId)) {{
          sceneSelection.add(activeId);
        }}
        const selectedIds = [...sceneSelection];
        const multiSelect = selectedIds.length > 1;
        card.classList.toggle("completed", sceneDone);
        const doneButton = card.querySelector(".done-button");
        doneButton.textContent = sceneDone ? "Done ✓" : "Mark done";
        doneButton.classList.toggle("active", sceneDone);
        doneButton.setAttribute("aria-pressed", sceneDone ? "true" : "false");
        const selectAllButton = card.querySelector(".select-all-button");
        const allSelected = selectableSamples.length > 1
          && selectableSamples.every(sample => sceneSelection.has(sample.sample_id));
        selectAllButton.disabled = selectableSamples.length < 2;
        selectAllButton.textContent = allSelected ? "Single select" : "Select all";
        selectAllButton.classList.toggle("active", allSelected);
        selectAllButton.setAttribute("aria-pressed", allSelected ? "true" : "false");
        const heuristicButton = card.querySelector(".heuristic-button");
        const heuristicId = metadata.vegetation_heuristic?.id;
        const appliedCount = sceneSamples.filter(sample => {{
          const review = reviews[sample.sample_id] || {{}};
          return review.source === "heuristic" && review.heuristic_id === heuristicId;
        }}).length;
        const hasPreview = Object.prototype.hasOwnProperty.call(suggestedByScene, scene.scene_id);
        if (hasPreview) suggestedByScene[scene.scene_id] = suggestedByScene[scene.scene_id]
          .filter(sampleId => statusOf(sampleId) === "aligned");
        const previewCount = hasPreview ? suggestedByScene[scene.scene_id].length : 0;
        heuristicButton.disabled = !heuristicId;
        heuristicButton.textContent = !heuristicId ? "Non-veg unavailable" : appliedCount
          ? `Undo ${{appliedCount}} flags` : hasPreview
            ? (previewCount ? `Mark ${{previewCount}} uncertain` : "No candidates") : "Check non-veg";
        heuristicButton.classList.toggle("preview", hasPreview && previewCount > 0);
        heuristicButton.classList.toggle("applied", appliedCount > 0);
        card.querySelectorAll(".tree-marker").forEach(marker => {{
          const markerSample = samplesById[marker.dataset.sampleId];
          marker.dataset.status = statusOf(markerSample.sample_id);
          marker.classList.toggle("active", sceneSelection.has(markerSample.sample_id));
          marker.classList.toggle("heuristic-suggestion", Boolean(
            suggestedByScene[scene.scene_id]?.includes(markerSample.sample_id)
          ));
          marker.title = sampleTitle(markerSample) + (isStacked(markerSample)
            ? ` · exact coordinate stack ×${{markerSample.coordinate_stack_size}} · excluded unless split with offsets` : "")
            + " · Shift-click to add or remove from selection";
          marker.setAttribute("aria-pressed", sceneSelection.has(markerSample.sample_id) ? "true" : "false");
        }});
        card.querySelectorAll(".tree-species-label").forEach(label => {{
          label.dataset.status = statusOf(label.dataset.sampleId);
          label.classList.toggle("active", sceneSelection.has(label.dataset.sampleId));
        }});
        card.querySelectorAll(".tree-choice").forEach(choice => {{
          choice.dataset.status = statusOf(choice.dataset.sampleId);
          choice.classList.toggle("active", sceneSelection.has(choice.dataset.sampleId));
          choice.classList.toggle("heuristic-suggestion", Boolean(
            suggestedByScene[scene.scene_id]?.includes(choice.dataset.sampleId)
          ));
          const choiceSample = samplesById[choice.dataset.sampleId];
          choice.title = sampleTitle(choiceSample) + (isStacked(choiceSample)
            ? ` · exact coordinate stack ×${{choiceSample.coordinate_stack_size}} · excluded unless split with offsets` : "")
            + " · Shift-click to add or remove from selection";
        }});
        card.querySelector(".selected-species").textContent = multiSelect
          ? `${{selectedIds.length}} trees selected` : selected.species;
        const dbh = selected.dbh_in == null ? "DBH unknown" : `${{selected.dbh_in.toFixed(1)}} in DBH`;
        card.querySelector(".selected-facts").textContent = multiSelect
          ? "Choose a classification below to apply it to the entire selection."
          : `${{selected.tree_id}} · ${{dbh}} · ${{selected.split}} · ${{statusOf(activeId)}}` +
            (isStacked(selected) ? ` · coordinate stack ×${{selected.coordinate_stack_size}} (excluded unless split)` : "");
        const offset = card.querySelector(".offset");
        offset.textContent = multiSelect ? "Center marking is disabled for a multi-selection."
          : selectedReview.east_m == null ? "Select this ring, then click the apparent center if it is offset."
          : `Measured offset: ${{selectedReview.east_m.toFixed(2)}} m east, ${{selectedReview.north_m.toFixed(2)}} m north`;
        offset.classList.toggle("offset-hint", multiSelect || selectedReview.east_m == null);
        card.querySelector(".image-wrap").classList.toggle("multi-select", multiSelect);
        card.querySelectorAll("[data-review-status]").forEach(button => {{
          const selectedStatus = selectedIds.every(
            sampleId => statusOf(sampleId) === button.dataset.reviewStatus
          );
          button.classList.toggle("active", selectedStatus);
          button.textContent = multiSelect
            ? `${{button.dataset.reviewLabel}} (${{selectedIds.length}})`
            : button.dataset.reviewLabel;
        }});
        const note = card.querySelector("textarea");
        note.disabled = multiSelect;
        note.value = multiSelect ? "" : selectedReview.note || "";
        note.placeholder = multiSelect
          ? "Notes are disabled while multiple trees are selected."
          : note.dataset.singlePlaceholder;
        if (multiSelect && card.classList.contains("street-view-open")) closeStreetView(card);
        if (!multiSelect && streetViewEmbedApiKey && card.classList.contains("fullscreen")) {{
          card.classList.add("street-view-open");
        }}
        if (card.classList.contains("street-view-open")) {{
          refreshStreetView(card, selected);
        }}
        const picked = card.querySelector(".picked");
        if (!multiSelect && selectedReview.status === "offset" && selectedReview.image_x != null) {{
          picked.style.display = "block";
          picked.style.left = `${{100 * selectedReview.image_x / scene.image_width}}%`;
          picked.style.top = `${{100 * selectedReview.image_y / scene.image_height}}%`;
        }} else picked.style.display = "none";
      }});
      const eastMedian = median(east), northMedian = median(north);
      document.getElementById("stats").textContent = `${{completedScenes}}/${{scenes.length}} images done · ` +
        `${{visibleScenes}} shown · ${{samples.length - stackedSamples.length}} reviewable · ` +
        `${{stackedSamples.length}} stacked hidden` +
        (eastMedian == null ? "" : ` · median ${{eastMedian.toFixed(2)}} m E, ${{northMedian.toFixed(2)}} m N`);
    }}
    function createCard(scene, sceneIndex) {{
      const sceneSamples = scene.sample_ids.map(sampleId => samplesById[sampleId]);
      const card = document.createElement("article"); card.className = "card"; card.dataset.scene = scene.scene_id;
      const head = document.createElement("div"); head.className = "card-head";
      const identity = document.createElement("div"); identity.className = "identity";
      const title = document.createElement("strong"); title.textContent = `Image ${{sceneIndex + 1}} of ${{scenes.length}}`;
      const stackCount = sceneSamples.filter(isStacked).length;
      const reviewableCount = sceneSamples.length - stackCount;
      const count = document.createElement("span"); count.textContent = `${{reviewableCount}} reviewable tree${{reviewableCount === 1 ? "" : "s"}}` +
        (stackCount ? ` · ${{stackCount}} stacked hidden` : "");
      identity.append(title, count);
      const badges = document.createElement("div"); badges.className = "badges";
      scene.splits.forEach(value => {{ const badge = document.createElement("span"); badge.className = "badge"; badge.textContent = value; badges.append(badge); }});
      if (scene.seam_priority) {{
        const badge = document.createElement("span"); badge.className = "badge seam"; badge.textContent = "tile seam";
        badge.title = `Priority review: nearest inventory point is ${{scene.tile_seam_distance_m.toFixed(1)}} m from a source-tile seam`;
        badges.append(badge);
      }}
      if (stackCount) {{
        const badge = document.createElement("span"); badge.className = "badge stack-badge";
        badge.textContent = `${{stackCount}} stacked`;
        badge.title = "Unresolved exact-coordinate stacks are hidden and excluded from model supervision";
        badges.append(badge);
      }}
      const heuristic = document.createElement("button"); heuristic.className = "heuristic-button";
      heuristic.textContent = "Check non-veg";
      heuristic.title = "Preview conservative low-NIR gray candidates; click again to mark them uncertain";
      heuristic.addEventListener("click", () => runVegetationHeuristic(scene.scene_id)); badges.append(heuristic);
      const selectAll = document.createElement("button"); selectAll.className = "select-all-button";
      selectAll.textContent = "Select all"; selectAll.setAttribute("aria-pressed", "false");
      selectAll.title = "Select every currently available tree in this image";
      selectAll.addEventListener("click", () => toggleSelectAll(scene.scene_id)); badges.append(selectAll);
      const done = document.createElement("button"); done.className = "done-button";
      done.textContent = "Mark done"; done.setAttribute("aria-pressed", "false");
      done.title = "Mark this entire image as reviewed";
      done.addEventListener("click", () => setSceneDone(scene.scene_id, !sceneReviews[scene.scene_id]?.done));
      badges.append(done);
      const previous = document.createElement("button"); previous.className = "scene-previous fullscreen-only";
      previous.textContent = "← Previous"; previous.title = "Previous visible scene (Left Arrow)";
      previous.addEventListener("click", () => moveScene(card, -1)); badges.append(previous);
      const next = document.createElement("button"); next.className = "scene-next fullscreen-only";
      next.textContent = "Next →"; next.title = "Next visible scene (Right Arrow)";
      next.addEventListener("click", () => moveScene(card, 1)); badges.append(next);
      const expand = document.createElement("button"); expand.className = "expand-button"; expand.textContent = "Full screen";
      expand.setAttribute("aria-expanded", "false"); expand.title = "Open a large review view with the same per-tree controls";
      expand.addEventListener("click", () => setExpanded(card, !card.classList.contains("fullscreen"))); badges.append(expand);
      head.append(identity, badges);
      const wrap = document.createElement("div"); wrap.className = "image-wrap";
      const image = document.createElement("img"); image.src = scene.image; image.alt = `NAIP scene with ${{scene.tree_count}} inventory trees`; image.draggable = false;
      wrap.append(image);
      sceneSamples.forEach((sample, index) => {{
        const marker = document.createElement("button"); marker.className = "tree-marker"; marker.dataset.sampleId = sample.sample_id;
        marker.classList.toggle("stacked", isStacked(sample));
        marker.style.left = `${{100 * sample.target_x / scene.image_width}}%`; marker.style.top = `${{100 * sample.target_y / scene.image_height}}%`;
        marker.textContent = index + 1; marker.setAttribute("aria-label", `Select tree ${{index + 1}}: ${{sample.species}}`);
        marker.addEventListener("click", event => {{
          event.stopPropagation(); selectSample(scene.scene_id, sample.sample_id, event.shiftKey);
        }});
        wrap.append(marker);
        const speciesLabel = document.createElement("span"); speciesLabel.className = "tree-species-label";
        speciesLabel.dataset.sampleId = sample.sample_id;
        speciesLabel.classList.toggle("stacked", isStacked(sample));
        speciesLabel.style.left = `${{100 * sample.target_x / scene.image_width}}%`;
        speciesLabel.style.top = `${{100 * sample.target_y / scene.image_height}}%`;
        speciesLabel.style.setProperty("--label-offset", `${{index % 2 ? 8 : -8}}px`);
        speciesLabel.textContent = sample.species || "Unknown species";
        wrap.append(speciesLabel);
      }});
      const picked = document.createElement("span"); picked.className = "picked"; wrap.append(picked);
      const streetViewCamera = document.createElement("span"); streetViewCamera.className = "street-view-camera";
      wrap.append(streetViewCamera);
      wrap.addEventListener("click", event => {{
        if (selectionFor(scene.scene_id).size !== 1) return;
        const sampleId = activeByScene[scene.scene_id], sample = samplesById[sampleId];
        const rect = image.getBoundingClientRect();
        const x = (event.clientX - rect.left) / rect.width * scene.image_width;
        const y = (event.clientY - rect.top) / rect.height * scene.image_height;
        const dx = x - sample.target_x, dy = y - sample.target_y;
        reviews[sampleId] = {{...(reviews[sampleId] || {{}}), status: "offset", source: "human", image_x: x, image_y: y,
          east_m: sample.transform_a * dx + sample.transform_b * dy,
          north_m: sample.transform_d * dx + sample.transform_e * dy}};
        delete reviews[sampleId].heuristic_id;
        persist();
      }});
      const treeList = document.createElement("div"); treeList.className = "tree-list";
      sceneSamples.forEach((sample, index) => {{
        const choice = document.createElement("button"); choice.className = "tree-choice"; choice.dataset.sampleId = sample.sample_id;
        choice.classList.toggle("stacked", isStacked(sample));
        choice.textContent = index + 1; choice.addEventListener("click", event =>
          selectSample(scene.scene_id, sample.sample_id, event.shiftKey)); treeList.append(choice);
      }});
      const details = document.createElement("div"); details.className = "details";
      const species = document.createElement("div"); species.className = "selected-species";
      const facts = document.createElement("div"); facts.className = "selected-facts";
      const offset = document.createElement("div"); offset.className = "offset"; details.append(species, facts, offset);
      const actions = document.createElement("div"); actions.className = "actions";
      [["aligned", "Aligned"], ["not-tree", "Not tree"], ["uncertain", "Uncertain"],
        ["duplicate", "Duplicate"]].forEach(([status, label]) => {{
        const button = document.createElement("button"); button.dataset.reviewStatus = status;
        button.dataset.reviewLabel = label; button.textContent = label;
        button.addEventListener("click", () => setStatus(scene.scene_id, status)); actions.append(button);
      }});
      const streetViewPanel = document.createElement("section"); streetViewPanel.className = "street-view-panel";
      const streetViewHead = document.createElement("div"); streetViewHead.className = "street-view-head";
      const streetViewLocation = document.createElement("span"); streetViewLocation.className = "street-view-location";
      const streetViewExternal = document.createElement("a"); streetViewExternal.className = "street-view-external";
      streetViewExternal.target = "_blank"; streetViewExternal.rel = "noopener noreferrer";
      streetViewExternal.textContent = "Open in Maps ↗";
      const streetViewInteraction = document.createElement("button");
      streetViewInteraction.className = "street-view-interaction"; streetViewInteraction.textContent = "Unlock";
      streetViewInteraction.title = "Enable panorama controls; the overhead cone shows only the initial view";
      streetViewInteraction.addEventListener("click", () => toggleStreetViewInteraction(card));
      streetViewHead.append(streetViewLocation, streetViewExternal, streetViewInteraction);
      const streetViewFrameWrap = document.createElement("div"); streetViewFrameWrap.className = "street-view-frame-wrap";
      const streetViewFrame = document.createElement("iframe"); streetViewFrame.className = "street-view-frame";
      streetViewFrame.title = "Google Street View near selected tree"; streetViewFrame.loading = "lazy";
      streetViewFrame.allowFullscreen = true; streetViewFrame.referrerPolicy = "strict-origin-when-cross-origin";
      streetViewFrame.classList.add("locked"); streetViewFrameWrap.append(streetViewFrame);
      streetViewPanel.append(streetViewHead, streetViewFrameWrap);
      const note = document.createElement("textarea"); note.placeholder = "Shadow, stale inventory, merged crowns, systematic shift…";
      note.dataset.singlePlaceholder = note.placeholder;
      note.addEventListener("change", () => {{
        if (selectionFor(scene.scene_id).size !== 1) return;
        const sampleId = activeByScene[scene.scene_id]; reviews[sampleId] = {{...(reviews[sampleId] || {{}}), note: note.value}}; persist();
      }});
      card.append(head, wrap, streetViewPanel, treeList, details, actions, note); return card;
    }}
    [...new Set(samples.map(sample => sample.split))].forEach(value => {{
      const option = document.createElement("option"); option.value = value; option.textContent = value; splitFilter.append(option);
    }});
    scenes.forEach((scene, index) => cards.append(createCard(scene, index)));
    document.addEventListener("keydown", event => {{
      const openCard = document.querySelector(".card.fullscreen");
      if (!openCard) return;
      if (event.key === "Escape") {{ setExpanded(openCard, false); return; }}
      const editing = event.target.matches("textarea, input, select") || event.target.isContentEditable;
      if (editing) return;
      const reviewHotkeys = {{a: "aligned", n: "not-tree", u: "uncertain", d: "duplicate"}};
      const reviewStatus = reviewHotkeys[event.key.toLowerCase()];
      if (reviewStatus && !event.ctrlKey && !event.metaKey && !event.altKey) {{
        event.preventDefault(); setStatus(openCard.dataset.scene, reviewStatus); return;
      }}
      if (event.key === "ArrowRight") {{ event.preventDefault(); moveScene(openCard, 1); }}
      if (event.key === "ArrowLeft") {{ event.preventDefault(); moveScene(openCard, -1); }}
    }});
    splitFilter.addEventListener("change", update); coverageFilter.addEventListener("change", update);
    statusFilter.addEventListener("change", update); sceneStatusFilter.addEventListener("change", update);
    showStacks.addEventListener("change", () => {{
      document.body.classList.toggle("show-stacks", showStacks.checked); update();
    }});
    document.getElementById("export").addEventListener("click", () => {{
      const result = {{schema_version: 1, metadata, exported_at: new Date().toISOString(),
        reviews: samples.map(sample => ({{...sample, ...(reviews[sample.sample_id] || {{}})}})),
        scene_reviews: sceneReviews}};
      const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], {{type: "application/json"}}));
      link.download = "registration-reviews.json"; link.click(); URL.revokeObjectURL(link.href);
    }});
    document.getElementById("import").addEventListener("change", event => {{
      const reader = new FileReader(); reader.onload = () => {{
        const imported = JSON.parse(reader.result);
        const importedReviews = Array.isArray(imported.reviews)
          ? Object.fromEntries(imported.reviews.map(review => [review.sample_id, review])) : (imported.reviews || {{}});
        reviews = withAlignedDefaults(importedReviews); sceneReviews = imported.scene_reviews || {{}}; persist();
      }}; if (event.target.files[0]) reader.readAsText(event.target.files[0]);
    }});
    document.getElementById("finalize").addEventListener("click", async () => {{
      const saved = await syncReviews();
      if (!saved) {{ alert("Reviews must be served and saved before finalization."); return; }}
      const response = await fetch("/api/finalize", {{method: "POST"}}); const result = await response.json();
      if (!response.ok) {{ alert(`Finalization failed: ${{result.error}}`); return; }}
      alert(`Training feedback finalized. Registration: ${{result.registration_status}}; ` +
        `correction ${{result.correction_m.east.toFixed(2)}} m E, ${{result.correction_m.north.toFixed(2)}} m N; ` +
        `${{result.point_corrected_points}} point corrections and ${{result.excluded_points}} exclusions. Rebuild chips before training.`);
    }});
    document.getElementById("clear").addEventListener("click", () => {{
      if (confirm("Reset every tree decision to aligned and mark every image to review?")) {{
        reviews = withAlignedDefaults({{}}); sceneReviews = {{}}; persist();
      }}
    }});
    async function hydrateServerReviews() {{
      try {{
        const response = await fetch("/api/reviews"); if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
        const persisted = await response.json(); reviews = withAlignedDefaults({{...(persisted.reviews || {{}}), ...reviews}});
        sceneReviews = {{...(persisted.scene_reviews || {{}}), ...sceneReviews}};
        storeLocalState(); document.getElementById("sync").textContent = "Loaded saved reviews";
      }} catch (error) {{
        reviews = withAlignedDefaults(reviews); storeLocalState();
        document.getElementById("sync").textContent = location.protocol === "file:" ? "Local only — serve the UI to auto-save" : `Load failed: ${{error.message}}`;
      }} finally {{ update(); syncTimer = setTimeout(syncReviews, 250); }}
    }}
    hydrateServerReviews();
    const requestedSceneId = new URLSearchParams(location.search).get("scene");
    if (requestedSceneId && scenesById[requestedSceneId]) {{
      splitFilter.value = ""; coverageFilter.value = ""; statusFilter.value = "";
      sceneStatusFilter.value = ""; update();
      requestAnimationFrame(() => {{
        const requestedCard = document.querySelector(`[data-scene="${{requestedSceneId}}"]`);
        if (requestedCard) {{ requestedCard.scrollIntoView({{block: "center"}}); setExpanded(requestedCard, true); }}
      }});
    }}
  </script>
</body>
</html>
"""


def _render_registration_html(
    samples: list[dict[str, object]],
    metadata: dict[str, object],
    scenes: list[dict[str, object]] | None = None,
) -> str:
    if scenes is not None:
        return _render_grouped_registration_html(samples, metadata, scenes)
    payload = json.dumps(samples, separators=(",", ":")).replace("<", "\\u003c")
    metadata_payload = json.dumps(metadata, separators=(",", ":")).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Urban tree registration review</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #101613; color: #eef5ef; }}
    header {{ position: sticky; top: 0; z-index: 4; padding: 16px 22px; background: #17211ccc;
      backdrop-filter: blur(12px); border-bottom: 1px solid #33453a; }}
    h1 {{ margin: 0 0 5px; font-size: 21px; }}
    .lede {{ margin: 0; color: #b7c8bc; font-size: 14px; }}
    .toolbar {{ display: flex; flex-wrap: wrap; gap: 9px; margin-top: 12px; align-items: center; }}
    button, select, .file-label {{ border: 1px solid #496252; border-radius: 7px; padding: 7px 10px;
      color: #eef5ef; background: #203027; cursor: pointer; font: inherit; }}
    button:hover, .file-label:hover {{ background: #2b4234; }}
    input[type=file] {{ display: none; }}
    #sync {{ color: #9eb6a5; font-size: 12px; }}
    #stats {{ margin-left: auto; color: #c8d7cc; font-variant-numeric: tabular-nums; }}
    main {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
      gap: 16px; padding: 18px; }}
    .card {{ overflow: hidden; border: 1px solid #30443a; border-radius: 10px; background: #18231d; }}
    .card.reviewed {{ border-color: #698c76; }}
    .card-head {{ padding: 10px 12px; display: flex; justify-content: space-between; gap: 10px; }}
    .identity {{ min-width: 0; }}
    .identity strong, .identity span {{ display: block; white-space: nowrap; overflow: hidden;
      text-overflow: ellipsis; }}
    .identity span, .details {{ color: #aebfb3; font-size: 12px; }}
    .split {{ align-self: start; padding: 3px 7px; border-radius: 999px; background: #293a31;
      font-size: 11px; text-transform: uppercase; }}
    .badges {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 5px; }}
    .seam {{ align-self: start; padding: 3px 7px; border-radius: 999px; background: #614d22;
      color: #ffe5a0; font-size: 11px; text-transform: uppercase; cursor: help; }}
    .image-wrap {{ position: relative; width: 100%; aspect-ratio: 1; background: #050806; cursor: crosshair; }}
    .image-wrap img {{ display: block; width: 100%; height: 100%; object-fit: contain;
      image-rendering: auto; user-select: none; }}
    .picked {{ display: none; position: absolute; width: 16px; height: 16px; border: 2px solid #ffe34e;
      border-radius: 50%; transform: translate(-50%, -50%); pointer-events: none;
      box-shadow: 0 0 0 1px #111; }}
    .details {{ padding: 9px 12px 0; line-height: 1.45; }}
    .actions {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(64px, 1fr)); gap: 5px; padding: 10px 12px; }}
    .actions button {{ padding: 6px 3px; font-size: 12px; }}
    .actions button.active {{ background: #d5ebda; border-color: #d5ebda; color: #102016; }}
    textarea {{ width: calc(100% - 24px); min-height: 48px; margin: 0 12px 12px; resize: vertical;
      border: 1px solid #3b5144; border-radius: 6px; padding: 7px; color: #eef5ef; background: #101713; }}
    .hidden {{ display: none; }}
    @media (max-width: 600px) {{ main {{ padding: 8px; grid-template-columns: 1fr; }} #stats {{ width: 100%; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Registration review</h1>
    <p class="lede">Red cross = selected inventory point; cyan rings = nearby inventory points.
      Every sample starts aligned. Click an apparent tree center to mark and correct an offset.</p>
    <div class="toolbar">
      <select id="split-filter"><option value="">All splits</option></select>
      <select id="coverage-filter">
        <option value="">All coverage</option><option value="seam">Tile seams</option>
        <option value="interior">Tile interiors</option>
      </select>
      <select id="status-filter">
        <option value="">All statuses</option><option value="unreviewed">Unreviewed</option>
        <option value="aligned">Aligned</option><option value="offset">Offset</option>
        <option value="not-tree">Not tree</option><option value="uncertain">Uncertain</option>
        <option value="duplicate">Duplicate</option>
      </select>
      <button id="export">Export reviews</button>
      <label class="file-label" for="import">Import reviews</label><input id="import" type="file" accept="application/json">
      <button id="finalize">Finalize training feedback</button>
      <button id="clear">Reset all to aligned</button>
      <span id="sync">Loading saved reviews…</span>
      <span id="stats"></span>
    </div>
  </header>
  <main id="cards"></main>
  <script>
    const samples = {payload};
    const metadata = {metadata_payload};
    const storageKey = `urban-tree-registration:${{metadata.review_id}}`;
    let reviews = JSON.parse(localStorage.getItem(storageKey) || "{{}}");
    let syncTimer = null;
    const cards = document.getElementById("cards");
    const splitFilter = document.getElementById("split-filter");
    const coverageFilter = document.getElementById("coverage-filter");
    const statusFilter = document.getElementById("status-filter");
    const escapeText = value => value == null ? "" : String(value);
    const withAlignedDefaults = source => Object.fromEntries(samples.map(sample => [
      sample.sample_id, {{status: "aligned", ...(source[sample.sample_id] || {{}})}}
    ]));
    const median = values => {{
      if (!values.length) return null;
      const sorted = [...values].sort((a, b) => a - b), middle = Math.floor(sorted.length / 2);
      return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
    }};
    async function syncReviews() {{
      clearTimeout(syncTimer);
      const sync = document.getElementById("sync");
      try {{
        const response = await fetch("/api/reviews", {{method: "PUT", headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{schema_version: 1, metadata, reviews}})}});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${{response.status}}`);
        sync.textContent = `Saved ${{result.reviews}} reviews`;
        return true;
      }} catch (error) {{
        sync.textContent = location.protocol === "file:" ? "Local only — serve the UI to auto-save" : `Save failed: ${{error.message}}`;
        return false;
      }}
    }}
    function persist() {{
      localStorage.setItem(storageKey, JSON.stringify(reviews)); update();
      document.getElementById("sync").textContent = "Saving…";
      clearTimeout(syncTimer); syncTimer = setTimeout(syncReviews, 250);
    }}
    function update() {{
      let reviewed = 0;
      const east = [], north = [];
      document.querySelectorAll(".card").forEach(card => {{
        const review = reviews[card.dataset.id] || {{}};
        const status = review.status || "unreviewed";
        const isSeam = card.dataset.seam === "true";
        const visible = (!splitFilter.value || card.dataset.split === splitFilter.value)
          && (!coverageFilter.value || (coverageFilter.value === "seam") === isSeam)
          && (!statusFilter.value || status === statusFilter.value);
        card.classList.toggle("hidden", !visible);
        card.classList.toggle("reviewed", status !== "unreviewed");
        card.querySelectorAll("[data-status]").forEach(button =>
          button.classList.toggle("active", button.dataset.status === status));
        const marker = card.querySelector(".picked");
        if (review.image_x != null) {{
          marker.style.display = "block";
          marker.style.left = `${{100 * review.image_x / Number(card.dataset.imageWidth)}}%`;
          marker.style.top = `${{100 * review.image_y / Number(card.dataset.imageHeight)}}%`;
        }} else marker.style.display = "none";
        card.querySelector("textarea").value = review.note || "";
        const offset = card.querySelector(".offset");
        offset.textContent = review.east_m == null ? "No measured offset" :
          `Measured offset: ${{review.east_m.toFixed(2)}} m east, ${{review.north_m.toFixed(2)}} m north`;
        if (status !== "unreviewed") reviewed += 1;
        if (review.east_m != null) {{ east.push(review.east_m); north.push(review.north_m); }}
      }});
      const eastMedian = median(east), northMedian = median(north);
      document.getElementById("stats").textContent = `${{reviewed}}/${{samples.length}} reviewed` +
        (eastMedian == null ? "" : ` · median ${{eastMedian.toFixed(2)}} m E, ${{northMedian.toFixed(2)}} m N`);
    }}
    function createCard(sample) {{
      const card = document.createElement("article");
      card.className = "card";
      card.dataset.id = sample.sample_id; card.dataset.split = sample.split;
      card.dataset.seam = Boolean(sample.seam_priority);
      card.dataset.imageWidth = sample.image_width; card.dataset.imageHeight = sample.image_height;
      const head = document.createElement("div"); head.className = "card-head";
      const identity = document.createElement("div"); identity.className = "identity";
      const title = document.createElement("strong"); title.textContent = escapeText(sample.species);
      const tree = document.createElement("span"); tree.textContent = escapeText(sample.tree_id);
      identity.append(title, tree);
      const split = document.createElement("span"); split.className = "split"; split.textContent = sample.split;
      const badges = document.createElement("div"); badges.className = "badges";
      badges.append(split);
      if (sample.seam_priority) {{
        const seam = document.createElement("span"); seam.className = "seam"; seam.textContent = "tile seam";
        seam.title = `Priority review: inventory point is ${{sample.tile_seam_distance_m.toFixed(1)}} m from a source-tile seam`;
        badges.append(seam);
      }}
      head.append(identity, badges);
      const wrap = document.createElement("div"); wrap.className = "image-wrap";
      const image = document.createElement("img"); image.src = sample.image; image.alt = `NAIP crop for ${{sample.tree_id}}`;
      image.draggable = false;
      const marker = document.createElement("span"); marker.className = "picked"; wrap.append(image, marker);
      wrap.addEventListener("click", event => {{
        const rect = image.getBoundingClientRect();
        const x = (event.clientX - rect.left) / rect.width * sample.image_width;
        const y = (event.clientY - rect.top) / rect.height * sample.image_height;
        const dx = x - sample.target_x, dy = y - sample.target_y;
        reviews[sample.sample_id] = {{...(reviews[sample.sample_id] || {{}}), status: "offset", image_x: x, image_y: y,
          east_m: sample.transform_a * dx + sample.transform_b * dy,
          north_m: sample.transform_d * dx + sample.transform_e * dy}};
        persist();
      }});
      const details = document.createElement("div"); details.className = "details";
      const facts = document.createElement("div");
      const dbh = sample.dbh_in == null ? "DBH unknown" : `${{sample.dbh_in.toFixed(1)}} in DBH`;
      facts.textContent = `${{dbh}} · ${{sample.nearby_inventory_count}} inventory points in view`;
      const offset = document.createElement("div"); offset.className = "offset";
      details.append(facts, offset);
      const actions = document.createElement("div"); actions.className = "actions";
      [["aligned", "Aligned"], ["offset", "Offset"], ["not-tree", "Not tree"],
        ["uncertain", "Uncertain"], ["duplicate", "Duplicate"]]
        .forEach(([status, label]) => {{
          const button = document.createElement("button"); button.dataset.status = status; button.textContent = label;
          button.addEventListener("click", () => {{
            reviews[sample.sample_id] = {{...(reviews[sample.sample_id] || {{}}), status}}; persist();
          }}); actions.append(button);
        }});
      const note = document.createElement("textarea"); note.placeholder = "Shadow, stale inventory, merged crowns, systematic shift…";
      note.addEventListener("change", () => {{
        reviews[sample.sample_id] = {{...(reviews[sample.sample_id] || {{}}), note: note.value}}; persist();
      }});
      card.append(head, wrap, details, actions, note); return card;
    }}
    [...new Set(samples.map(sample => sample.split))].forEach(value => {{
      const option = document.createElement("option"); option.value = value; option.textContent = value; splitFilter.append(option);
    }});
    samples.forEach(sample => cards.append(createCard(sample)));
    splitFilter.addEventListener("change", update); coverageFilter.addEventListener("change", update);
    statusFilter.addEventListener("change", update);
    document.getElementById("export").addEventListener("click", () => {{
      const result = {{schema_version: 1, metadata, exported_at: new Date().toISOString(),
        reviews: samples.map(sample => ({{...sample, ...(reviews[sample.sample_id] || {{}})}}))}};
      const link = document.createElement("a");
      link.href = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], {{type: "application/json"}}));
      link.download = "registration-reviews.json"; link.click(); URL.revokeObjectURL(link.href);
    }});
    document.getElementById("import").addEventListener("change", event => {{
      const reader = new FileReader(); reader.onload = () => {{
        const imported = JSON.parse(reader.result);
        const importedReviews = Array.isArray(imported.reviews)
          ? Object.fromEntries(imported.reviews.map(review => [review.sample_id, review]))
          : (imported.reviews || {{}});
        reviews = withAlignedDefaults(importedReviews);
        persist();
      }}; if (event.target.files[0]) reader.readAsText(event.target.files[0]);
    }});
    document.getElementById("finalize").addEventListener("click", async () => {{
      const saved = await syncReviews();
      if (!saved) {{ alert("Reviews must be served and saved before finalization."); return; }}
      const response = await fetch("/api/finalize", {{method: "POST"}});
      const result = await response.json();
      if (!response.ok) {{ alert(`Finalization failed: ${{result.error}}`); return; }}
      alert(`Training feedback finalized. Registration: ${{result.registration_status}}; ` +
        `correction ${{result.correction_m.east.toFixed(2)}} m E, ${{result.correction_m.north.toFixed(2)}} m N; ` +
        `${{result.point_corrected_points}} point corrections and ${{result.excluded_points}} exclusions. ` +
        `Rebuild chips before training.`);
    }});
    document.getElementById("clear").addEventListener("click", () => {{
      if (confirm("Reset every decision to aligned?")) {{ reviews = withAlignedDefaults({{}}); persist(); }}
    }});
    async function hydrateServerReviews() {{
      try {{
        const response = await fetch("/api/reviews");
        if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
        const persisted = await response.json();
        reviews = withAlignedDefaults({{...(persisted.reviews || {{}}), ...reviews}});
        localStorage.setItem(storageKey, JSON.stringify(reviews));
        update();
        await syncReviews();
      }} catch (_) {{
        reviews = withAlignedDefaults(reviews);
        localStorage.setItem(storageKey, JSON.stringify(reviews));
        document.getElementById("sync").textContent = "Local only — serve the UI to auto-save";
        update();
      }}
    }}
    hydrateServerReviews();
  </script>
</body>
</html>
"""


def append_validation_chip_to_registration_review(
    config: ProjectConfig,
    raster_path: str | Path,
    review_dir: str | Path,
    chip_id: str,
    ground_truth: pd.DataFrame,
) -> dict[str, object]:
    """Add one model-validation chip to the existing registration review."""
    import re

    try:
        import rasterio
        from PIL import Image
        from rasterio.windows import Window
    except ImportError as error:  # pragma: no cover - exercised by CLI installations
        raise RuntimeError(
            "Install the imagery dependency group: uv sync --group imagery"
        ) from error

    match = re.fullmatch(r"r(?P<row>\d{6})_c(?P<column>\d{6})", chip_id)
    if match is None:
        raise ValueError(f"invalid validation chip id {chip_id!r}")
    directory = Path(review_dir).resolve()
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata = manifest.get("metadata")
    samples = manifest.get("samples")
    scenes = manifest.get("scenes")
    if not isinstance(metadata, dict) or not isinstance(samples, list) or not isinstance(scenes, list):
        raise ValueError("registration review manifest has an invalid shape")
    for scene in scenes:
        if isinstance(scene, dict) and scene.get("validation_chip_id") == chip_id:
            return {"scene_id": str(scene["scene_id"]), "added": False}
    existing_scene_by_tree = {
        str(sample["tree_id"]): str(sample["scene_id"])
        for sample in samples
        if isinstance(sample, dict) and sample.get("tree_id") is not None
    }
    requested_tree_ids = ground_truth["tree_id"].astype(str)
    unreviewed_truth = ground_truth[~requested_tree_ids.isin(existing_scene_by_tree)].copy()
    if unreviewed_truth.empty and not ground_truth.empty:
        first_tree_id = str(ground_truth.iloc[0]["tree_id"])
        return {
            "scene_id": existing_scene_by_tree[first_tree_id],
            "added": False,
            "already_reviewed": True,
        }
    ground_truth = unreviewed_truth
    if ground_truth.empty:
        raise ValueError(f"validation chip {chip_id!r} has no retained inventory trees to curate")

    raster = Path(raster_path).resolve()
    chip_pixels = config.imagery.chip_pixels
    row_offset = int(match.group("row")) * chip_pixels
    column_offset = int(match.group("column")) * chip_pixels
    with rasterio.open(raster) as source:
        window = Window(column_offset, row_offset, chip_pixels, chip_pixels)
        masks = source.read_masks(config.imagery.bands, window=window)
        raw = source.read(config.imagery.bands, window=window)
        valid_fraction = float(np.all(masks > 0, axis=0).mean())
        transform = source.transform
    image_dir = directory / "images"
    image_dir.mkdir(parents=True, exist_ok=True)
    image_name = f"validation-{chip_id}.png"
    Image.fromarray(_rgb_preview(raw, config.imagery.input_scale)).save(
        image_dir / image_name,
        format="PNG",
        optimize=True,
    )
    scene_id = f"scene-{len(scenes):03d}"
    sample_ids: list[str] = []
    ordered_truth = ground_truth.sort_values(["pixel_row", "pixel_col", "tree_id"])
    for row in ordered_truth.itertuples(index=False):
        sample_id = f"sample-{len(samples):04d}"
        sample_ids.append(sample_id)
        target_x = float(row.pixel_col - column_offset)
        target_y = float(row.pixel_row - row_offset)
        samples.append(
            {
                "sample_id": sample_id,
                "scene_id": scene_id,
                "tree_id": str(row.tree_id),
                "split": str(row.split),
                "species": str(row.species) if pd.notna(row.species) else "Unknown species",
                "genus": str(row.genus) if pd.notna(row.genus) else "Unknown genus",
                "dbh_in": float(row.dbh_in) if pd.notna(row.dbh_in) else None,
                "longitude": float(row.longitude),
                "latitude": float(row.latitude),
                "image": f"images/{image_name}",
                "image_width": chip_pixels,
                "image_height": chip_pixels,
                "target_x": target_x,
                "target_y": target_y,
                "vegetation_heuristic": (
                    _vegetation_features(raw, target_x, target_y)
                    if raw.shape[0] >= 4
                    else None
                ),
                "transform_a": float(transform.a),
                "transform_b": float(transform.b),
                "transform_d": float(transform.d),
                "transform_e": float(transform.e),
                "nearby_inventory_count": len(ordered_truth),
                "valid_fraction": valid_fraction,
                "source_item_ids": [],
                "tile_seam_distance_m": None,
                "seam_priority": False,
                "validation_chip_id": chip_id,
            }
        )
    scenes.append(
        {
            "scene_id": scene_id,
            "image": f"images/{image_name}",
            "image_width": chip_pixels,
            "image_height": chip_pixels,
            "sample_ids": sample_ids,
            "splits": sorted(ground_truth["split"].astype(str).unique()),
            "tree_count": len(sample_ids),
            "valid_fraction": valid_fraction,
            "seam_priority": False,
            "tile_seam_distance_m": None,
            "source_item_ids": [],
            "validation_chip_id": chip_id,
        }
    )
    coordinate_stack_groups, coordinate_stack_points = _attach_coordinate_stack_sizes(samples)
    samples_by_id = {str(sample["sample_id"]): sample for sample in samples}
    for scene in scenes:
        scene["coordinate_stack_points"] = sum(
            int(samples_by_id[str(sample_id)]["coordinate_stack_size"] > 1)
            for sample_id in scene["sample_ids"]
        )
    metadata["rendered_samples"] = len(samples)
    metadata["rendered_scenes"] = len(scenes)
    metadata["coordinate_stack_groups"] = coordinate_stack_groups
    metadata["coordinate_stack_points"] = coordinate_stack_points
    metadata["extended_from_validation_chips"] = int(
        metadata.get("extended_from_validation_chips", 0)
    ) + 1
    metadata["updated_at"] = datetime.now(UTC).isoformat()
    _write_json_atomic(manifest_path, manifest)
    (directory / "index.html").write_text(
        _render_registration_html(samples, metadata, scenes),
        encoding="utf-8",
    )
    return {"scene_id": scene_id, "added": True, "samples": len(sample_ids)}


def refresh_registration_heuristics(
    config: ProjectConfig,
    raster_path: str | Path,
    *,
    review_dir: str | Path | None = None,
    profile_id: str = str(VEGETATION_HEURISTIC_PROFILE["id"]),
    ndvi_p90_max: float = float(VEGETATION_HEURISTIC_PROFILE["ndvi_p90_max"]),
    gray_fraction_min: float = float(VEGETATION_HEURISTIC_PROFILE["gray_fraction_min"]),
) -> dict[str, object]:
    """Enrich an existing review without changing its scenes, sample IDs, or feedback."""
    try:
        import rasterio
        from rasterio.windows import Window
    except ImportError as error:  # pragma: no cover - exercised by CLI installations
        raise RuntimeError(
            "Install the imagery dependency group: uv sync --group imagery"
        ) from error

    if not -1 <= ndvi_p90_max <= 1:
        raise ValueError("ndvi_p90_max must be between -1 and 1")
    if not 0 <= gray_fraction_min <= 1:
        raise ValueError("gray_fraction_min must be between 0 and 1")
    profile = {
        **VEGETATION_HEURISTIC_PROFILE,
        "id": profile_id,
        "ndvi_p90_max": ndvi_p90_max,
        "gray_fraction_min": gray_fraction_min,
    }
    raster = Path(raster_path).resolve()
    directory = (
        Path(review_dir)
        if review_dir is not None
        else config.paths.root / "qa" / "registration" / raster.stem
    )
    manifest_path = directory / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    metadata = manifest.get("metadata")
    samples = manifest.get("samples")
    scenes = manifest.get("scenes")
    if (
        not isinstance(metadata, dict)
        or not isinstance(samples, list)
        or not isinstance(scenes, list)
    ):
        raise ValueError("registration review manifest has an invalid shape")
    if Path(str(metadata.get("source_raster", ""))).name != raster.name:
        raise ValueError("registration review was generated for a different raster")

    samples_by_id = {
        str(sample["sample_id"]): sample
        for sample in samples
        if isinstance(sample, dict) and "sample_id" in sample
    }
    with rasterio.open(raster) as source:
        if source.crs is None:
            raise ValueError("imagery raster must declare a CRS")
        if len(config.imagery.bands) < 4 or max(config.imagery.bands) > source.count:
            raise ValueError("vegetation heuristics require configured RGB-NIR bands")
        transformer = Transformer.from_crs("EPSG:4326", source.crs, always_xy=True)
        inverse = ~source.transform
        for scene in scenes:
            if not isinstance(scene, dict) or not scene.get("sample_ids"):
                raise ValueError("registration review scene has no samples")
            scene_samples = [samples_by_id[str(value)] for value in scene["sample_ids"]]
            first = scene_samples[0]
            first_x, first_y = transformer.transform(
                float(first["longitude"]), float(first["latitude"])
            )
            first_col, first_row = inverse * (first_x, first_y)
            col_off = round(first_col - float(first["target_x"]))
            row_off = round(first_row - float(first["target_y"]))
            width = int(scene["image_width"])
            height = int(scene["image_height"])
            raw = source.read(
                config.imagery.bands,
                window=Window(col_off, row_off, width, height),
            )
            for sample in scene_samples:
                sample["vegetation_heuristic"] = _vegetation_features(
                    raw,
                    float(sample["target_x"]),
                    float(sample["target_y"]),
                    profile,
                )

    coordinate_stack_groups, coordinate_stack_points = _attach_coordinate_stack_sizes(samples)
    for scene in scenes:
        scene["coordinate_stack_points"] = sum(
            int(samples_by_id[str(sample_id)]["coordinate_stack_size"] > 1)
            for sample_id in scene["sample_ids"]
        )
    metadata["vegetation_heuristic"] = profile
    metadata["vegetation_heuristic_candidates"] = sum(
        int(sample["vegetation_heuristic"]["candidate"]) for sample in samples
    )
    metadata["coordinate_stack_groups"] = coordinate_stack_groups
    metadata["coordinate_stack_points"] = coordinate_stack_points
    metadata["heuristics_refreshed_at"] = datetime.now(UTC).isoformat()
    _write_json_atomic(manifest_path, manifest)
    html_path = directory / "index.html"
    html_path.write_text(
        _render_registration_html(samples, metadata, scenes),
        encoding="utf-8",
    )
    return {
        "manifest": str(manifest_path),
        "html": str(html_path),
        "samples": len(samples),
        "vegetation_candidates": metadata["vegetation_heuristic_candidates"],
        "coordinate_stack_groups": coordinate_stack_groups,
        "coordinate_stack_points": coordinate_stack_points,
        "reviews_preserved": (directory / "reviews.json").exists(),
    }


def build_registration_review(
    config: ProjectConfig,
    raster_path: str | Path,
    *,
    samples: int = 100,
    window_pixels: int = 128,
    include_test: bool = False,
    output_dir: str | Path | None = None,
    extend_existing: bool = False,
) -> dict[str, object]:
    if samples < 1:
        raise ValueError("samples must be at least one")
    if window_pixels < 32 or window_pixels % 2:
        raise ValueError("window_pixels must be an even integer of at least 32")
    try:
        import rasterio
        from PIL import Image
        from rasterio.windows import Window
    except ImportError as error:  # pragma: no cover - exercised by CLI installations
        raise RuntimeError(
            "Install the imagery dependency group: uv sync --group imagery"
        ) from error

    source_path = Path(raster_path).resolve()
    destination = (
        Path(output_dir)
        if output_dir is not None
        else config.paths.root / "qa" / "registration" / source_path.stem
    )
    image_dir = destination / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = destination / "manifest.json"
    existing_metadata: dict[str, object] = {}
    generated: list[dict[str, object]] = []
    generated_scenes: list[dict[str, object]] = []
    if extend_existing:
        if not manifest_path.exists():
            raise FileNotFoundError(
                f"cannot extend a registration review without a manifest: {manifest_path}"
            )
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        existing_metadata = existing.get("metadata", {})
        existing_samples = existing.get("samples", [])
        existing_scenes = existing.get("scenes", [])
        if not isinstance(existing_metadata, dict):
            raise ValueError("existing registration metadata has an invalid shape")
        if not isinstance(existing_samples, list) or not isinstance(existing_scenes, list):
            raise ValueError("existing registration manifest has an invalid shape")
        if int(existing_metadata.get("window_pixels", -1)) != window_pixels:
            raise ValueError("existing registration review uses a different window size")
        if bool(existing_metadata.get("test_labels_included")) != include_test:
            raise ValueError("existing registration review uses a different test-label policy")
        existing_raster = existing_metadata.get("source_raster")
        if existing_raster is None or Path(str(existing_raster)).name != source_path.name:
            raise ValueError("existing registration review was generated for a different raster")
        generated = [dict(sample) for sample in existing_samples]
        generated_scenes = [dict(scene) for scene in existing_scenes]

    initial_sample_count = len(generated)
    initial_scene_count = len(generated_scenes)

    inventory_path = (
        config.paths.root / "inventory" / config.inventory.city.lower() / "inventory.parquet"
    )
    frame = pd.read_parquet(inventory_path)
    allowed_splits = ["train", "validation"] + (["test"] if include_test else [])
    frame = frame[frame["split_eligible"] & frame["split"].isin(allowed_splits)].copy()

    with rasterio.open(source_path) as source:
        if source.crs is None:
            raise ValueError("imagery raster must declare a CRS")
        if max(config.imagery.bands) > source.count:
            raise ValueError(
                f"configured band {max(config.imagery.bands)} exceeds raster count {source.count}"
            )
        transformer = Transformer.from_crs("EPSG:4326", source.crs, always_xy=True)
        xs, ys = transformer.transform(frame["longitude"].to_numpy(), frame["latitude"].to_numpy())
        xs = np.asarray(xs)
        ys = np.asarray(ys)
        source_item_ids, seam_distances, mosaic_sources = _mosaic_context(source_path, xs, ys)
        frame["source_item_ids"] = source_item_ids
        frame["tile_seam_distance_m"] = seam_distances
        inverse = ~source.transform
        locations = [inverse * (x, y) for x, y in zip(xs, ys, strict=True)]
        frame["pixel_col"] = [location[0] for location in locations]
        frame["pixel_row"] = [location[1] for location in locations]
        frame["scene_col"] = np.floor(frame["pixel_col"] / window_pixels).astype(int)
        frame["scene_row"] = np.floor(frame["pixel_row"] / window_pixels).astype(int)
        frame = frame[
            (frame["scene_col"] >= 0)
            & (frame["scene_row"] >= 0)
            & ((frame["scene_col"] + 1) * window_pixels <= source.width)
            & ((frame["scene_row"] + 1) * window_pixels <= source.height)
        ].copy()
        if frame.empty:
            raise ValueError("no eligible inventory points fall inside the QA raster")

        scene_groups = {
            (int(scene_col), int(scene_row)): group.copy()
            for (scene_col, scene_row), group in frame.groupby(
                ["scene_col", "scene_row"], sort=False
            )
        }
        existing_samples_by_id = {str(sample["sample_id"]): sample for sample in generated}
        scene_key_by_tree_id = {
            str(row.tree_id): (int(row.scene_col), int(row.scene_row))
            for row in frame.itertuples(index=False)
        }
        existing_scene_keys: set[tuple[int, int]] = set()
        for scene in generated_scenes:
            scene_sample_ids = scene.get("sample_ids", [])
            if not isinstance(scene_sample_ids, list) or not scene_sample_ids:
                raise ValueError("existing registration scene has no samples")
            try:
                keys = {
                    scene_key_by_tree_id[str(existing_samples_by_id[str(sample_id)]["tree_id"])]
                    for sample_id in scene_sample_ids
                }
            except KeyError as error:
                raise ValueError(
                    "existing registration sample is absent from the current inventory"
                ) from error
            if len(keys) != 1:
                raise ValueError("existing registration scene no longer maps to one image window")
            existing_scene_keys.update(keys)
        minimum_scenes = min(len(scene_groups), max(len(allowed_splits), int(np.ceil(samples / 4))))

        def balanced_scene_order(candidates: pd.DataFrame, seed: int) -> list[tuple[int, int]]:
            per_split: list[list[tuple[int, int]]] = []
            for split_index, split in enumerate(allowed_splits):
                split_frame = candidates[candidates["split"] == split]
                keys: list[tuple[int, int]] = []
                if not split_frame.empty:
                    for row in _diverse_order(split_frame, seed + split_index).itertuples(
                        index=False
                    ):
                        key = (int(row.scene_col), int(row.scene_row))
                        if key not in keys:
                            keys.append(key)
                per_split.append(keys)
            ordered_keys: list[tuple[int, int]] = []
            while any(per_split):
                for keys in per_split:
                    while keys and keys[0] in ordered_keys:
                        keys.pop(0)
                    if keys:
                        ordered_keys.append(keys.pop(0))
            return ordered_keys

        ordered_scene_keys = balanced_scene_order(frame, config.seed)
        seam_scene_keys: list[tuple[int, int]] = []
        if mosaic_sources:
            seam_scene_budget = max(1, minimum_scenes // 4)
            seam_pool = frame[frame["tile_seam_distance_m"].notna()].nsmallest(
                min(len(frame), max(32, seam_scene_budget * 16)),
                "tile_seam_distance_m",
            )
            seam_scene_keys = balanced_scene_order(seam_pool, config.seed + 1)[:seam_scene_budget]

        scene_order = list(seam_scene_keys)
        for scene_key in ordered_scene_keys:
            if scene_key not in scene_order:
                scene_order.append(scene_key)

        for scene_key in scene_order:
            if len(generated) >= samples and len(generated_scenes) >= minimum_scenes:
                break
            if scene_key in existing_scene_keys:
                continue
            scene_col, scene_row = scene_key
            col_off = scene_col * window_pixels
            row_off = scene_row * window_pixels
            window = Window(col_off, row_off, window_pixels, window_pixels)
            masks = source.read_masks(config.imagery.bands, window=window)
            valid_fraction = float(np.all(masks > 0, axis=0).mean())
            if valid_fraction < config.imagery.minimum_valid_fraction:
                continue
            raw = source.read(config.imagery.bands, window=window)
            preview = Image.fromarray(_rgb_preview(raw, config.imagery.input_scale))
            scene_id = f"scene-{len(generated_scenes):03d}"
            image_name = f"{len(generated_scenes):03d}.png"
            preview.save(image_dir / image_name, format="PNG", optimize=True)
            scene_samples: list[str] = []
            scene_frame = scene_groups[scene_key].sort_values(["pixel_row", "pixel_col", "tree_id"])
            scene_is_seam = scene_key in seam_scene_keys
            for row in scene_frame.itertuples(index=False):
                sample_id = f"sample-{len(generated):04d}"
                scene_samples.append(sample_id)
                generated.append(
                    {
                        "sample_id": sample_id,
                        "scene_id": scene_id,
                        "tree_id": str(row.tree_id),
                        "split": str(row.split),
                        "species": (
                            str(row.species) if not pd.isna(row.species) else "Unknown species"
                        ),
                        "genus": str(row.genus) if not pd.isna(row.genus) else "Unknown genus",
                        "dbh_in": (
                            float(row.diameter_at_breast_height)
                            if pd.notna(row.diameter_at_breast_height)
                            else None
                        ),
                        "longitude": float(row.longitude),
                        "latitude": float(row.latitude),
                        "split_block_x": int(row.split_block_x),
                        "split_block_y": int(row.split_block_y),
                        "image": f"images/{image_name}",
                        "image_width": window_pixels,
                        "image_height": window_pixels,
                        "target_x": float(row.pixel_col - col_off),
                        "target_y": float(row.pixel_row - row_off),
                        "vegetation_heuristic": (
                            _vegetation_features(
                                raw,
                                float(row.pixel_col - col_off),
                                float(row.pixel_row - row_off),
                            )
                            if raw.shape[0] >= 4
                            else None
                        ),
                        "transform_a": float(source.transform.a),
                        "transform_b": float(source.transform.b),
                        "transform_d": float(source.transform.d),
                        "transform_e": float(source.transform.e),
                        "nearby_inventory_count": len(scene_frame),
                        "valid_fraction": valid_fraction,
                        "source_item_ids": list(row.source_item_ids),
                        "tile_seam_distance_m": (
                            float(row.tile_seam_distance_m)
                            if pd.notna(row.tile_seam_distance_m)
                            else None
                        ),
                        "seam_priority": scene_is_seam,
                    }
                )
            seam_distances_in_scene = pd.to_numeric(
                scene_frame["tile_seam_distance_m"], errors="coerce"
            )
            generated_scenes.append(
                {
                    "scene_id": scene_id,
                    "image": f"images/{image_name}",
                    "image_width": window_pixels,
                    "image_height": window_pixels,
                    "sample_ids": scene_samples,
                    "splits": sorted(scene_frame["split"].astype(str).unique()),
                    "tree_count": len(scene_samples),
                    "valid_fraction": valid_fraction,
                    "seam_priority": scene_is_seam,
                    "tile_seam_distance_m": (
                        float(seam_distances_in_scene.min())
                        if seam_distances_in_scene.notna().any()
                        else None
                    ),
                    "source_item_ids": sorted(
                        {
                            str(item_id)
                            for item_ids in scene_frame["source_item_ids"]
                            for item_id in item_ids
                        }
                    ),
                }
            )
            if len(generated) >= samples and len(generated_scenes) >= minimum_scenes:
                break

    if not generated:
        raise ValueError("no sufficiently valid registration QA windows could be rendered")
    coordinate_stack_groups, coordinate_stack_points = _attach_coordinate_stack_sizes(generated)
    generated_by_id = {str(sample["sample_id"]): sample for sample in generated}
    for scene in generated_scenes:
        scene["coordinate_stack_points"] = sum(
            int(generated_by_id[str(sample_id)]["coordinate_stack_size"] > 1)
            for sample_id in scene["sample_ids"]
        )
    review_id = (
        f"{source_path.stem}-{config.seed}-grouped-v2-"
        f"{'with-test' if include_test else 'development'}"
    )
    now = datetime.now(UTC).isoformat()
    metadata: dict[str, object] = {
        "review_id": review_id,
        "generated_at": existing_metadata.get("generated_at", now),
        "updated_at": now,
        "source_raster": str(source_path.resolve()),
        "inventory": str(inventory_path.resolve()),
        "requested_samples": samples,
        "minimum_scenes": minimum_scenes,
        "rendered_samples": len(generated),
        "rendered_scenes": len(generated_scenes),
        "window_pixels": window_pixels,
        "included_splits": allowed_splits,
        "test_labels_included": include_test,
        "mosaic_sources": mosaic_sources,
        "seam_prioritized_samples": sum(
            int(scene["tree_count"]) for scene in generated_scenes if scene["seam_priority"]
        ),
        "vegetation_heuristic": (
            VEGETATION_HEURISTIC_PROFILE if len(config.imagery.bands) >= 4 else None
        ),
        "vegetation_heuristic_candidates": sum(
            int(bool((sample.get("vegetation_heuristic") or {}).get("candidate")))
            for sample in generated
        ),
        "coordinate_stack_groups": coordinate_stack_groups,
        "coordinate_stack_points": coordinate_stack_points,
        "instruction": (
            "Estimate any registration correction using training samples only; use validation "
            "to verify it and do not tune against test samples."
        ),
    }
    if extend_existing:
        metadata["extended_from_samples"] = initial_sample_count
        metadata["extended_from_scenes"] = initial_scene_count
    _write_json_atomic(
        manifest_path,
        {"metadata": metadata, "scenes": generated_scenes, "samples": generated},
    )
    html_path = destination / "index.html"
    html_path.write_text(
        _render_registration_html(generated, metadata, generated_scenes),
        encoding="utf-8",
    )
    split_counts = pd.Series([sample["split"] for sample in generated]).value_counts()
    return {
        "samples": len(generated),
        "scenes": len(generated_scenes),
        "splits": {str(name): int(count) for name, count in split_counts.items()},
        "html": str(html_path),
        "manifest": str(manifest_path),
        "test_labels_included": include_test,
        "extended": extend_existing,
        "added_samples": len(generated) - initial_sample_count,
        "added_scenes": len(generated_scenes) - initial_scene_count,
    }

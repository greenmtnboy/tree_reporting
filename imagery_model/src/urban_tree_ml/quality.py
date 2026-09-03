# The self-contained HTML/JavaScript template intentionally has lines that are
# more readable above the Python line-length limit.
# ruff: noqa: E501

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

from urban_tree_ml.config import ProjectConfig


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
    header {{ position: sticky; top: 0; z-index: 20; padding: 16px 22px; background: #17211cf2;
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
    main {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(430px, 1fr));
      gap: 16px; padding: 18px; align-items: start; }}
    .card {{ overflow: hidden; border: 1px solid #30443a; border-radius: 10px; background: #18231d; }}
    .card.reviewed {{ border-color: #698c76; }}
    .card-head {{ padding: 10px 12px; display: flex; justify-content: space-between; gap: 10px; }}
    .identity {{ min-width: 0; }}
    .identity strong, .identity span {{ display: block; }}
    .identity span, .details {{ color: #aebfb3; font-size: 12px; }}
    .badges {{ display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 5px; }}
    .badge {{ align-self: start; padding: 3px 7px; border-radius: 999px; background: #293a31;
      font-size: 11px; text-transform: uppercase; }}
    .seam {{ background: #614d22; color: #ffe5a0; cursor: help; }}
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
    .tree-marker.active {{ box-shadow: 0 0 0 3px #fff, 0 0 0 5px #102016; z-index: 5; }}
    .picked {{ display: none; position: absolute; width: 18px; height: 18px; border: 2px solid #ffe34e;
      transform: translate(-50%, -50%) rotate(45deg); pointer-events: none; z-index: 4;
      box-shadow: 0 0 0 1px #111; }}
    .tree-list {{ display: flex; gap: 6px; padding: 9px 12px 0; overflow-x: auto; }}
    .tree-choice {{ flex: 0 0 auto; min-width: 34px; padding: 5px 8px; font-size: 11px; }}
    .tree-choice.active {{ background: #d5ebda; border-color: #d5ebda; color: #102016; }}
    .tree-choice[data-status="offset"] {{ border-color: #ffe34e; }}
    .tree-choice[data-status="not-tree"] {{ border-color: #ff5757; }}
    .tree-choice[data-status="uncertain"] {{ border-color: #ff9f43; }}
    .details {{ padding: 9px 12px 0; line-height: 1.5; min-height: 70px; }}
    .selected-species {{ color: #eef5ef; font-size: 14px; font-weight: 700; }}
    .actions {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 5px; padding: 10px 12px; }}
    .actions button {{ padding: 6px 3px; font-size: 12px; }}
    .actions button.active {{ background: #d5ebda; border-color: #d5ebda; color: #102016; }}
    .offset-hint {{ color: #ffe6a3; }}
    textarea {{ width: calc(100% - 24px); min-height: 48px; margin: 0 12px 12px; resize: vertical;
      border: 1px solid #3b5144; border-radius: 6px; padding: 7px; color: #eef5ef; background: #101713; }}
    .hidden {{ display: none; }}
    @media (max-width: 600px) {{ main {{ padding: 8px; grid-template-columns: 1fr; }} #stats {{ width: 100%; }} }}
  </style>
</head>
<body>
  <header>
    <h1>Grouped registration review</h1>
    <p class="lede">Each numbered ring is one inventory tree. Select a ring, then click its apparent
      tree center to record an offset. Cyan = aligned, yellow = offset, red = not tree, orange = uncertain.</p>
    <div class="toolbar">
      <select id="split-filter"><option value="">All splits</option></select>
      <select id="coverage-filter"><option value="">All coverage</option><option value="seam">Tile seams</option>
        <option value="interior">Tile interiors</option></select>
      <select id="status-filter"><option value="">All statuses</option><option value="unreviewed">Unreviewed</option>
        <option value="aligned">Aligned</option><option value="offset">Offset</option>
        <option value="not-tree">Not tree</option><option value="uncertain">Uncertain</option></select>
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
    const samplesById = Object.fromEntries(samples.map(sample => [sample.sample_id, sample]));
    const scenesById = Object.fromEntries(scenes.map(scene => [scene.scene_id, scene]));
    const activeByScene = Object.fromEntries(scenes.map(scene => [scene.scene_id, scene.sample_ids[0]]));
    const storageKey = `urban-tree-registration:${{metadata.review_id}}`;
    let reviews = JSON.parse(localStorage.getItem(storageKey) || "{{}}");
    let syncTimer = null;
    const cards = document.getElementById("cards");
    const splitFilter = document.getElementById("split-filter");
    const coverageFilter = document.getElementById("coverage-filter");
    const statusFilter = document.getElementById("status-filter");
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
      return `${{sample.species}} · ${{dbh}} · ${{sample.tree_id}} · ${{statusOf(sample.sample_id)}}`;
    }};
    async function syncReviews() {{
      clearTimeout(syncTimer);
      const sync = document.getElementById("sync");
      try {{
        const response = await fetch("/api/reviews", {{method: "PUT", headers: {{"Content-Type": "application/json"}},
          body: JSON.stringify({{schema_version: 1, metadata, reviews}})}});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || `HTTP ${{response.status}}`);
        sync.textContent = `Saved ${{result.reviews}} tree reviews`;
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
    function setStatus(sampleId, status) {{
      const next = {{...(reviews[sampleId] || {{}}), status}};
      if (status !== "offset") {{
        ["image_x", "image_y", "east_m", "north_m"].forEach(field => delete next[field]);
      }}
      reviews[sampleId] = next; persist();
    }}
    function selectSample(sceneId, sampleId) {{ activeByScene[sceneId] = sampleId; update(); }}
    function update() {{
      let reviewed = 0, visibleScenes = 0;
      const east = [], north = [];
      samples.forEach(sample => {{
        const review = reviews[sample.sample_id] || {{}};
        if ((review.status || "unreviewed") !== "unreviewed") reviewed += 1;
        if (review.status === "offset" && review.east_m != null) {{ east.push(review.east_m); north.push(review.north_m); }}
      }});
      document.querySelectorAll(".card").forEach(card => {{
        const scene = scenesById[card.dataset.scene];
        const sceneSamples = scene.sample_ids.map(sampleId => samplesById[sampleId]);
        const statusMatches = sceneSamples.filter(sample => !statusFilter.value || statusOf(sample.sample_id) === statusFilter.value);
        const visible = (!splitFilter.value || scene.splits.includes(splitFilter.value))
          && (!coverageFilter.value || (coverageFilter.value === "seam") === Boolean(scene.seam_priority))
          && statusMatches.length > 0;
        card.classList.toggle("hidden", !visible);
        if (visible) visibleScenes += 1;
        if (statusFilter.value && !statusMatches.some(sample => sample.sample_id === activeByScene[scene.scene_id])) {{
          activeByScene[scene.scene_id] = statusMatches[0]?.sample_id || scene.sample_ids[0];
        }}
        const activeId = activeByScene[scene.scene_id];
        const selected = samplesById[activeId];
        const selectedReview = reviews[activeId] || {{}};
        card.classList.toggle("reviewed", sceneSamples.every(sample => statusOf(sample.sample_id) !== "unreviewed"));
        card.querySelectorAll(".tree-marker").forEach(marker => {{
          const markerSample = samplesById[marker.dataset.sampleId];
          marker.dataset.status = statusOf(markerSample.sample_id);
          marker.classList.toggle("active", markerSample.sample_id === activeId);
          marker.title = sampleTitle(markerSample);
          marker.setAttribute("aria-pressed", markerSample.sample_id === activeId ? "true" : "false");
        }});
        card.querySelectorAll(".tree-choice").forEach(choice => {{
          choice.dataset.status = statusOf(choice.dataset.sampleId);
          choice.classList.toggle("active", choice.dataset.sampleId === activeId);
          choice.title = sampleTitle(samplesById[choice.dataset.sampleId]);
        }});
        card.querySelector(".selected-species").textContent = selected.species;
        const dbh = selected.dbh_in == null ? "DBH unknown" : `${{selected.dbh_in.toFixed(1)}} in DBH`;
        card.querySelector(".selected-facts").textContent = `${{selected.tree_id}} · ${{dbh}} · ${{selected.split}} · ${{statusOf(activeId)}}`;
        const offset = card.querySelector(".offset");
        offset.textContent = selectedReview.east_m == null ? "Select this ring, then click the apparent center if it is offset." :
          `Measured offset: ${{selectedReview.east_m.toFixed(2)}} m east, ${{selectedReview.north_m.toFixed(2)}} m north`;
        offset.classList.toggle("offset-hint", selectedReview.east_m == null);
        card.querySelectorAll("[data-review-status]").forEach(button =>
          button.classList.toggle("active", button.dataset.reviewStatus === statusOf(activeId)));
        card.querySelector("textarea").value = selectedReview.note || "";
        const picked = card.querySelector(".picked");
        if (selectedReview.status === "offset" && selectedReview.image_x != null) {{
          picked.style.display = "block";
          picked.style.left = `${{100 * selectedReview.image_x / scene.image_width}}%`;
          picked.style.top = `${{100 * selectedReview.image_y / scene.image_height}}%`;
        }} else picked.style.display = "none";
      }});
      const eastMedian = median(east), northMedian = median(north);
      document.getElementById("stats").textContent = `${{reviewed}}/${{samples.length}} trees · ${{visibleScenes}}/${{scenes.length}} scenes` +
        (eastMedian == null ? "" : ` · median ${{eastMedian.toFixed(2)}} m E, ${{northMedian.toFixed(2)}} m N`);
    }}
    function createCard(scene, sceneIndex) {{
      const sceneSamples = scene.sample_ids.map(sampleId => samplesById[sampleId]);
      const card = document.createElement("article"); card.className = "card"; card.dataset.scene = scene.scene_id;
      const head = document.createElement("div"); head.className = "card-head";
      const identity = document.createElement("div"); identity.className = "identity";
      const title = document.createElement("strong"); title.textContent = `Scene ${{sceneIndex + 1}}`;
      const count = document.createElement("span"); count.textContent = `${{scene.tree_count}} tree${{scene.tree_count === 1 ? "" : "s"}} in this image`;
      identity.append(title, count);
      const badges = document.createElement("div"); badges.className = "badges";
      scene.splits.forEach(value => {{ const badge = document.createElement("span"); badge.className = "badge"; badge.textContent = value; badges.append(badge); }});
      if (scene.seam_priority) {{
        const badge = document.createElement("span"); badge.className = "badge seam"; badge.textContent = "tile seam";
        badge.title = `Priority review: nearest inventory point is ${{scene.tile_seam_distance_m.toFixed(1)}} m from a source-tile seam`;
        badges.append(badge);
      }}
      head.append(identity, badges);
      const wrap = document.createElement("div"); wrap.className = "image-wrap";
      const image = document.createElement("img"); image.src = scene.image; image.alt = `NAIP scene with ${{scene.tree_count}} inventory trees`; image.draggable = false;
      wrap.append(image);
      sceneSamples.forEach((sample, index) => {{
        const marker = document.createElement("button"); marker.className = "tree-marker"; marker.dataset.sampleId = sample.sample_id;
        marker.style.left = `${{100 * sample.target_x / scene.image_width}}%`; marker.style.top = `${{100 * sample.target_y / scene.image_height}}%`;
        marker.textContent = index + 1; marker.setAttribute("aria-label", `Select tree ${{index + 1}}: ${{sample.species}}`);
        marker.addEventListener("click", event => {{ event.stopPropagation(); selectSample(scene.scene_id, sample.sample_id); }});
        wrap.append(marker);
      }});
      const picked = document.createElement("span"); picked.className = "picked"; wrap.append(picked);
      wrap.addEventListener("click", event => {{
        const sampleId = activeByScene[scene.scene_id], sample = samplesById[sampleId];
        const rect = image.getBoundingClientRect();
        const x = (event.clientX - rect.left) / rect.width * scene.image_width;
        const y = (event.clientY - rect.top) / rect.height * scene.image_height;
        const dx = x - sample.target_x, dy = y - sample.target_y;
        reviews[sampleId] = {{...(reviews[sampleId] || {{}}), status: "offset", image_x: x, image_y: y,
          east_m: sample.transform_a * dx + sample.transform_b * dy,
          north_m: sample.transform_d * dx + sample.transform_e * dy}};
        persist();
      }});
      const treeList = document.createElement("div"); treeList.className = "tree-list";
      sceneSamples.forEach((sample, index) => {{
        const choice = document.createElement("button"); choice.className = "tree-choice"; choice.dataset.sampleId = sample.sample_id;
        choice.textContent = index + 1; choice.addEventListener("click", () => selectSample(scene.scene_id, sample.sample_id)); treeList.append(choice);
      }});
      const details = document.createElement("div"); details.className = "details";
      const species = document.createElement("div"); species.className = "selected-species";
      const facts = document.createElement("div"); facts.className = "selected-facts";
      const offset = document.createElement("div"); offset.className = "offset"; details.append(species, facts, offset);
      const actions = document.createElement("div"); actions.className = "actions";
      [["aligned", "Aligned"], ["not-tree", "Not tree"], ["uncertain", "Uncertain"]].forEach(([status, label]) => {{
        const button = document.createElement("button"); button.dataset.reviewStatus = status; button.textContent = label;
        button.addEventListener("click", () => setStatus(activeByScene[scene.scene_id], status)); actions.append(button);
      }});
      const note = document.createElement("textarea"); note.placeholder = "Shadow, stale inventory, merged crowns, systematic shift…";
      note.addEventListener("change", () => {{
        const sampleId = activeByScene[scene.scene_id]; reviews[sampleId] = {{...(reviews[sampleId] || {{}}), note: note.value}}; persist();
      }});
      card.append(head, wrap, treeList, details, actions, note); return card;
    }}
    [...new Set(samples.map(sample => sample.split))].forEach(value => {{
      const option = document.createElement("option"); option.value = value; option.textContent = value; splitFilter.append(option);
    }});
    scenes.forEach((scene, index) => cards.append(createCard(scene, index)));
    splitFilter.addEventListener("change", update); coverageFilter.addEventListener("change", update); statusFilter.addEventListener("change", update);
    document.getElementById("export").addEventListener("click", () => {{
      const result = {{schema_version: 1, metadata, exported_at: new Date().toISOString(),
        reviews: samples.map(sample => ({{...sample, ...(reviews[sample.sample_id] || {{}})}}))}};
      const link = document.createElement("a"); link.href = URL.createObjectURL(new Blob([JSON.stringify(result, null, 2)], {{type: "application/json"}}));
      link.download = "registration-reviews.json"; link.click(); URL.revokeObjectURL(link.href);
    }});
    document.getElementById("import").addEventListener("change", event => {{
      const reader = new FileReader(); reader.onload = () => {{
        const imported = JSON.parse(reader.result);
        const importedReviews = Array.isArray(imported.reviews)
          ? Object.fromEntries(imported.reviews.map(review => [review.sample_id, review])) : (imported.reviews || {{}});
        reviews = withAlignedDefaults(importedReviews); persist();
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
      if (confirm("Reset every tree decision to aligned?")) {{ reviews = withAlignedDefaults({{}}); persist(); }}
    }});
    async function hydrateServerReviews() {{
      try {{
        const response = await fetch("/api/reviews"); if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
        const persisted = await response.json(); reviews = withAlignedDefaults({{...(persisted.reviews || {{}}), ...reviews}});
        localStorage.setItem(storageKey, JSON.stringify(reviews)); document.getElementById("sync").textContent = "Loaded saved reviews";
      }} catch (error) {{
        reviews = withAlignedDefaults(reviews); localStorage.setItem(storageKey, JSON.stringify(reviews));
        document.getElementById("sync").textContent = location.protocol === "file:" ? "Local only — serve the UI to auto-save" : `Load failed: ${{error.message}}`;
      }} finally {{ update(); syncTimer = setTimeout(syncReviews, 250); }}
    }}
    hydrateServerReviews();
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
    .actions {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 5px; padding: 10px 12px; }}
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
      [["aligned", "Aligned"], ["offset", "Offset"], ["not-tree", "Not tree"], ["uncertain", "Uncertain"]]
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


def build_registration_review(
    config: ProjectConfig,
    raster_path: str | Path,
    *,
    samples: int = 100,
    window_pixels: int = 128,
    include_test: bool = False,
    output_dir: str | Path | None = None,
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

    source_path = Path(raster_path)
    destination = (
        Path(output_dir)
        if output_dir is not None
        else config.paths.root / "qa" / "registration" / source_path.stem
    )
    image_dir = destination / "images"
    image_dir.mkdir(parents=True, exist_ok=True)

    inventory_path = (
        config.paths.root / "inventory" / config.inventory.city.lower() / "inventory.parquet"
    )
    frame = pd.read_parquet(inventory_path)
    allowed_splits = ["train", "validation"] + (["test"] if include_test else [])
    frame = frame[frame["split_eligible"] & frame["split"].isin(allowed_splits)].copy()

    generated: list[dict[str, object]] = []
    generated_scenes: list[dict[str, object]] = []
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
        ordered = _diverse_order(frame, config.seed)
        seam_scene_keys: list[tuple[int, int]] = []
        if mosaic_sources:
            seam_budget = max(1, samples // 4)
            seam_pool = frame[frame["tile_seam_distance_m"].notna()].nsmallest(
                min(len(frame), max(32, seam_budget * 4)),
                "tile_seam_distance_m",
            )
            seam_owned_trees = 0
            for row in _diverse_order(seam_pool, config.seed + 1).itertuples(index=False):
                scene_key = (int(row.scene_col), int(row.scene_row))
                if scene_key in seam_scene_keys:
                    continue
                seam_scene_keys.append(scene_key)
                seam_owned_trees += len(scene_groups[scene_key])
                if seam_owned_trees >= seam_budget:
                    break

        scene_order = list(seam_scene_keys)
        for row in ordered.itertuples(index=False):
            scene_key = (int(row.scene_col), int(row.scene_row))
            if scene_key not in scene_order:
                scene_order.append(scene_key)

        for scene_key in scene_order:
            scene_col, scene_row = scene_key
            col_off = scene_col * window_pixels
            row_off = scene_row * window_pixels
            window = Window(col_off, row_off, window_pixels, window_pixels)
            masks = source.read_masks(config.imagery.bands, window=window)
            valid_fraction = float(np.all(masks > 0, axis=0).mean())
            if valid_fraction < config.imagery.minimum_valid_fraction:
                continue
            raw = source.read(config.imagery.bands[:3], window=window)
            preview = Image.fromarray(_rgb_preview(raw, config.imagery.input_scale))
            scene_id = f"scene-{len(generated_scenes):03d}"
            image_name = f"{len(generated_scenes):03d}.png"
            preview.save(image_dir / image_name, format="PNG", optimize=True)
            scene_samples: list[str] = []
            scene_frame = scene_groups[scene_key].sort_values(
                ["pixel_row", "pixel_col", "tree_id"]
            )
            scene_is_seam = scene_key in seam_scene_keys
            for row in scene_frame.itertuples(index=False):
                sample_id = f"sample-{len(generated):04d}"
                scene_samples.append(sample_id)
                generated.append({
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
                })
            seam_distances_in_scene = pd.to_numeric(
                scene_frame["tile_seam_distance_m"], errors="coerce"
            )
            generated_scenes.append({
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
            })
            if len(generated) >= samples:
                break

    if not generated:
        raise ValueError("no sufficiently valid registration QA windows could be rendered")
    review_id = (
        f"{source_path.stem}-{config.seed}-grouped-v2-"
        f"{'with-test' if include_test else 'development'}"
    )
    metadata: dict[str, object] = {
        "review_id": review_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_raster": str(source_path.resolve()),
        "inventory": str(inventory_path.resolve()),
        "requested_samples": samples,
        "rendered_samples": len(generated),
        "rendered_scenes": len(generated_scenes),
        "window_pixels": window_pixels,
        "included_splits": allowed_splits,
        "test_labels_included": include_test,
        "mosaic_sources": mosaic_sources,
        "seam_prioritized_samples": sum(
            int(scene["tree_count"])
            for scene in generated_scenes
            if scene["seam_priority"]
        ),
        "instruction": (
            "Estimate any registration correction using training samples only; use validation "
            "to verify it and do not tune against test samples."
        ),
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {"metadata": metadata, "scenes": generated_scenes, "samples": generated},
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
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
    }

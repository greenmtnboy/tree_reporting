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
    quantiles = min(4, int(candidates["diameter_at_breast_height"].nunique()))
    candidates["_dbh_bin"] = (
        pd.qcut(
            candidates["diameter_at_breast_height"],
            q=max(1, quantiles),
            labels=False,
            duplicates="drop",
        )
        .fillna(0)
        .astype(int)
    )

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


def _render_registration_html(samples: list[dict[str, object]], metadata: dict[str, object]) -> str:
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
      Click the apparent center of the selected tree to measure an offset, choose a verdict, then export.</p>
    <div class="toolbar">
      <select id="split-filter"><option value="">All splits</option></select>
      <select id="status-filter">
        <option value="">All statuses</option><option value="unreviewed">Unreviewed</option>
        <option value="aligned">Aligned</option><option value="offset">Offset</option>
        <option value="not-tree">Not tree</option><option value="uncertain">Uncertain</option>
      </select>
      <button id="export">Export reviews</button>
      <label class="file-label" for="import">Import reviews</label><input id="import" type="file" accept="application/json">
      <button id="finalize">Finalize training feedback</button>
      <button id="clear">Clear local reviews</button>
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
    const statusFilter = document.getElementById("status-filter");
    const escapeText = value => value == null ? "" : String(value);
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
        const visible = (!splitFilter.value || card.dataset.split === splitFilter.value)
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
      card.dataset.imageWidth = sample.image_width; card.dataset.imageHeight = sample.image_height;
      const head = document.createElement("div"); head.className = "card-head";
      const identity = document.createElement("div"); identity.className = "identity";
      const title = document.createElement("strong"); title.textContent = escapeText(sample.species);
      const tree = document.createElement("span"); tree.textContent = escapeText(sample.tree_id);
      identity.append(title, tree);
      const split = document.createElement("span"); split.className = "split"; split.textContent = sample.split;
      head.append(identity, split);
      const wrap = document.createElement("div"); wrap.className = "image-wrap";
      const image = document.createElement("img"); image.src = sample.image; image.alt = `NAIP crop for ${{sample.tree_id}}`;
      image.draggable = false;
      const marker = document.createElement("span"); marker.className = "picked"; wrap.append(image, marker);
      wrap.addEventListener("click", event => {{
        const rect = image.getBoundingClientRect();
        const x = (event.clientX - rect.left) / rect.width * sample.image_width;
        const y = (event.clientY - rect.top) / rect.height * sample.image_height;
        const dx = x - sample.target_x, dy = y - sample.target_y;
        reviews[sample.sample_id] = {{...(reviews[sample.sample_id] || {{}}), image_x: x, image_y: y,
          east_m: sample.transform_a * dx + sample.transform_b * dy,
          north_m: sample.transform_d * dx + sample.transform_e * dy}};
        persist();
      }});
      const details = document.createElement("div"); details.className = "details";
      const facts = document.createElement("div");
      facts.textContent = `${{sample.dbh_in.toFixed(1)}} in DBH · ${{sample.nearby_inventory_count}} inventory points in view`;
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
    splitFilter.addEventListener("change", update); statusFilter.addEventListener("change", update);
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
        reviews = Array.isArray(imported.reviews)
          ? Object.fromEntries(imported.reviews.map(review => [review.sample_id, review]))
          : (imported.reviews || {{}});
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
        `${{result.excluded_points}} points excluded. Rebuild chips before training.`);
    }});
    document.getElementById("clear").addEventListener("click", () => {{
      if (confirm("Clear all locally stored decisions for this review?")) {{ reviews = {{}}; persist(); }}
    }});
    async function hydrateServerReviews() {{
      try {{
        const response = await fetch("/api/reviews");
        if (!response.ok) throw new Error(`HTTP ${{response.status}}`);
        const persisted = await response.json();
        reviews = {{...(persisted.reviews || {{}}), ...reviews}};
        localStorage.setItem(storageKey, JSON.stringify(reviews));
        update();
        await syncReviews();
      }} catch (_) {{
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
        from PIL import Image, ImageDraw
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
    with rasterio.open(source_path) as source:
        if source.crs is None:
            raise ValueError("imagery raster must declare a CRS")
        if max(config.imagery.bands) > source.count:
            raise ValueError(
                f"configured band {max(config.imagery.bands)} exceeds raster count {source.count}"
            )
        transformer = Transformer.from_crs("EPSG:4326", source.crs, always_xy=True)
        xs, ys = transformer.transform(frame["longitude"].to_numpy(), frame["latitude"].to_numpy())
        inverse = ~source.transform
        locations = [inverse * (x, y) for x, y in zip(xs, ys, strict=True)]
        frame["pixel_col"] = [location[0] for location in locations]
        frame["pixel_row"] = [location[1] for location in locations]
        margin = window_pixels / 2
        frame = frame[
            (frame["pixel_col"] >= margin)
            & (frame["pixel_col"] < source.width - margin)
            & (frame["pixel_row"] >= margin)
            & (frame["pixel_row"] < source.height - margin)
        ].copy()
        if frame.empty:
            raise ValueError("no eligible inventory points fall inside the QA raster")

        ordered = _diverse_order(frame, config.seed)
        for row in ordered.itertuples(index=False):
            col_off = int(np.floor(row.pixel_col - window_pixels / 2))
            row_off = int(np.floor(row.pixel_row - window_pixels / 2))
            window = Window(col_off, row_off, window_pixels, window_pixels)
            masks = source.read_masks(config.imagery.bands, window=window)
            valid_fraction = float(np.all(masks > 0, axis=0).mean())
            if valid_fraction < config.imagery.minimum_valid_fraction:
                continue
            raw = source.read(config.imagery.bands[:3], window=window)
            preview = Image.fromarray(_rgb_preview(raw, config.imagery.input_scale)).convert("RGBA")
            overlay = Image.new("RGBA", preview.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            nearby = frame[
                frame["pixel_col"].between(col_off, col_off + window_pixels, inclusive="left")
                & frame["pixel_row"].between(row_off, row_off + window_pixels, inclusive="left")
            ]
            for nearby_row in nearby.itertuples(index=False):
                x = float(nearby_row.pixel_col - col_off)
                y = float(nearby_row.pixel_row - row_off)
                draw.ellipse((x - 2.5, y - 2.5, x + 2.5, y + 2.5), outline=(40, 235, 255, 230))
            target_x = float(row.pixel_col - col_off)
            target_y = float(row.pixel_row - row_off)
            draw.ellipse(
                (target_x - 5, target_y - 5, target_x + 5, target_y + 5),
                outline=(255, 55, 55, 255),
                width=2,
            )
            draw.line((target_x - 8, target_y, target_x + 8, target_y), fill=(255, 55, 55, 255))
            draw.line((target_x, target_y - 8, target_x, target_y + 8), fill=(255, 55, 55, 255))
            preview = Image.alpha_composite(preview, overlay).convert("RGB")
            image_name = f"{len(generated):03d}.png"
            preview.save(image_dir / image_name, format="PNG", optimize=True)
            generated.append(
                {
                    "sample_id": f"sample-{len(generated):03d}",
                    "tree_id": str(row.tree_id),
                    "split": str(row.split),
                    "species": str(row.species),
                    "genus": str(row.genus),
                    "dbh_in": float(row.diameter_at_breast_height),
                    "longitude": float(row.longitude),
                    "latitude": float(row.latitude),
                    "split_block_x": int(row.split_block_x),
                    "split_block_y": int(row.split_block_y),
                    "image": f"images/{image_name}",
                    "image_width": window_pixels,
                    "image_height": window_pixels,
                    "target_x": target_x,
                    "target_y": target_y,
                    "transform_a": float(source.transform.a),
                    "transform_b": float(source.transform.b),
                    "transform_d": float(source.transform.d),
                    "transform_e": float(source.transform.e),
                    "nearby_inventory_count": len(nearby),
                    "valid_fraction": valid_fraction,
                }
            )
            if len(generated) >= samples:
                break

    if not generated:
        raise ValueError("no sufficiently valid registration QA windows could be rendered")
    review_id = f"{source_path.stem}-{config.seed}-{'with-test' if include_test else 'development'}"
    metadata: dict[str, object] = {
        "review_id": review_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_raster": str(source_path.resolve()),
        "inventory": str(inventory_path.resolve()),
        "requested_samples": samples,
        "rendered_samples": len(generated),
        "window_pixels": window_pixels,
        "included_splits": allowed_splits,
        "test_labels_included": include_test,
        "instruction": (
            "Estimate any registration correction using training samples only; use validation "
            "to verify it and do not tune against test samples."
        ),
    }
    manifest_path = destination / "manifest.json"
    manifest_path.write_text(
        json.dumps({"metadata": metadata, "samples": generated}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    html_path = destination / "index.html"
    html_path.write_text(_render_registration_html(generated, metadata), encoding="utf-8")
    split_counts = pd.Series([sample["split"] for sample in generated]).value_counts()
    return {
        "samples": len(generated),
        "splits": {str(name): int(count) for name, count in split_counts.items()},
        "html": str(html_path),
        "manifest": str(manifest_path),
        "test_labels_included": include_test,
    }

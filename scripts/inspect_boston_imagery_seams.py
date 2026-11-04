#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["rasterio", "numpy", "matplotlib"]
# ///
"""Read-only reproduction of two blank NAIP seams in the Boston v13 map.

    uv run scripts/inspect_boston_imagery_seams.py

Reads the local cached imagery; never fetches, repairs, predicts or publishes.
Writes a diagnostic PNG and JSON next to the map exports. These two examples
establish missing input pixels, not an exhaustive census of every map gap.
"""
import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import rasterio


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path(__file__).resolve().parents[1] / "imagery_model/artifacts/point-tiles/naip2023")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("usbos_sources_seam_diagnostic.png"))
    args = parser.parse_args()
    examples = [
        ("p26919_e320102_n4688794_30cm", "Vertical gap", "columns"),
        ("p26919_e318566_n4687258_30cm", "Horizontal gap", "rows"),
    ]
    fig, axes = plt.subplots(1, 2, figsize=(12, 6.8), facecolor="#090f14")
    results = []
    for ax, (name, label, direction) in zip(axes, examples):
        raster = args.cache / f"{name}.tif"
        manifest_path = args.cache / f"{name}.manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        with rasterio.open(raster) as ds:
            data = ds.read()
            blank = np.all(data == 0, axis=0)
            fractions = blank.mean(axis=0 if direction == "columns" else 1)
            indices = np.flatnonzero(fractions == 1)
            resolution = abs(ds.transform.a if direction == "columns" else ds.transform.e)
            metres = len(indices) * resolution
            results.append({"raster": str(raster), "manifest": str(manifest_path),
                            "label": label, "direction": direction, "blank_pixel_indices": indices.tolist(),
                            "blank_strip_metres": metres, "all_bands_zero_fraction": float(blank.mean()),
                            "reported_valid_fraction": manifest["valid_fraction"],
                            "bounds_projected": list(ds.bounds), "crs": str(ds.crs),
                            "sources": manifest["sources"]})
        ax.imshow(data[:3].transpose(1, 2, 0))
        ax.axis("off")
        ax.set_title(f"{label}: {metres:.1f} m of blank pixels", color="#d9e1e3", fontsize=13, pad=14)
        ax.text(.5, -.045, f"Cache reports {manifest['valid_fraction']:.0%} valid / actual blank area {blank.mean():.1%}",
                transform=ax.transAxes, ha="center", color="#81949b", fontsize=9)
    fig.suptitle("Boston imagery seams: missing pixels in cached inputs", color="#d9e1e3", fontsize=17, y=.96)
    fig.text(.5, .035, "NAIP 2023 / read-only cache inspection / neither a map-projection artifact nor a repaired prediction layer",
             ha="center", color="#81949b", fontsize=9)
    fig.subplots_adjust(left=.035, right=.965, top=.85, bottom=.13, wspace=.06)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=180, facecolor=fig.get_facecolor())
    plt.close(fig)
    args.output.with_suffix(".json").write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps([{k: r[k] for k in ("label", "blank_strip_metres", "all_bands_zero_fraction", "reported_valid_fraction")} for r in results], indent=2))


if __name__ == "__main__":
    main()

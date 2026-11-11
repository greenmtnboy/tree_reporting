#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["pillow", "matplotlib"]
# ///
"""Assemble the four SF sharing posters' insets for side-by-side review."""
import argparse
import json
from pathlib import Path

from matplotlib.font_manager import findfont
from PIL import Image, ImageDraw, ImageFont


NAMES = ("alamo_square", "golden_gate_inner_sunset", "presidio_marina", "bernal_mission")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=Path(__file__).resolve().parent / "sf_renders")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output or args.input_dir / "san_francisco_treelines_inset_comparison.png"
    canvas = Image.new("RGB", (1600, 1810), "#f7f5ed")
    draw = ImageDraw.Draw(canvas)
    font_path = findfont("DejaVu Sans")
    font = ImageFont.truetype(font_path, 27)
    small = ImageFont.truetype(font_path, 22)
    expected = None
    for i, name in enumerate(NAMES):
        stem = args.input_dir / f"san_francisco_treelines_{name}_city_and_aerial"
        metadata = json.loads(stem.with_suffix(".json").read_text(encoding="utf-8"))
        counts = metadata["in_frame_counts_by_source"]
        if expected is None:
            expected = counts
        if counts != expected or len(metadata["insets"]) != 1:
            raise ValueError(f"{name}: expected one inset and consistent source counts across the set")
        inset = metadata["insets"][0]
        with Image.open(stem.with_suffix(".png")) as poster:
            width, height = poster.size
            # Single-inset axes in render_city_treelines: left .735, width .245,
            # bottom .5. Its square is sized in figure-width units at any DPI.
            crop = tuple(round(v) for v in (.735*width, .5*height-.245*width,
                                            .98*width, .5*height))
            square = poster.crop(crop).convert("RGB").resize((740, 740), Image.Resampling.LANCZOS)
        x, y = 30 + (i % 2)*800, 25 + (i // 2)*900
        draw.text((x, y), inset["label"], font=font, fill="#233b30")
        canvas.paste(square, (x, y+55))
        draw.text((x, y+812),
                  f"{inset['inventory_share_percent']:.1f}% city inventory | {inset['count']:,} trees | 2 × 2 km",
                  font=small, fill="#68746a")
    output.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(output)
    print(f"Saved {output}")


if __name__ == "__main__":
    main()

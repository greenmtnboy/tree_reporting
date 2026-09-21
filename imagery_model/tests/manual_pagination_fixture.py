"""Disposable 30-scene reviewer for browser testing; never touches live data."""
import json
import runpy
import tempfile
from pathlib import Path
import rasterio

from urban_tree_ml.config import load_config
from urban_tree_ml.quality import build_registration_review
from urban_tree_ml.qa_server import serve_registration_review

root = Path(tempfile.mkdtemp(prefix="curation-pagination-"))
config = load_config("configs/sf_naip_baseline.yaml")
config.paths.root = root / "artifacts"
config.paths.annotations = root / "annotations"
raster = runpy.run_path("tests/test_quality.py")["_write_qa_fixture"](config.paths.root)
result = build_registration_review(config, raster, samples=4, window_pixels=64)
directory = Path(result["manifest"]).parent
manifest = json.loads((directory / "manifest.json").read_text())
scene = manifest["scenes"][0]
sample = next(s for s in manifest["samples"] if s["sample_id"] in scene["sample_ids"])
manifest["scenes"] = [dict(scene, scene_id=f"scene-{i}", sample_ids=[f"sample-{i}"], tree_count=1) for i in range(30)]
manifest["samples"] = [dict(sample, scene_id=f"scene-{i}", sample_id=f"sample-{i}", tree_id=f"tree-{i}", coordinate_stack_size=1) for i in range(30)]
with rasterio.open(raster) as source:
    manifest['metadata'].update(curation_schema_version=2, curation_crs=source.crs.to_string())
(directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
print(root, flush=True)
serve_registration_review(config, raster, review_dir=directory, port=8767)

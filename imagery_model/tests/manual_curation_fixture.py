"""Isolated synthetic browser smoke; never reads or writes live annotations."""

import json
import runpy
import tempfile
from pathlib import Path

from urban_tree_ml import backup_job, qa_server
from urban_tree_ml.config import load_config
from urban_tree_ml.quality import build_registration_review

root = Path(tempfile.mkdtemp(prefix="curation-browser-smoke-"))
config = load_config("configs/sf_naip_baseline.yaml")
config.paths.root = root / "artifacts"
config.paths.annotations = root / "annotations"
fixture = runpy.run_path("tests/test_quality.py")["_write_qa_fixture"]
raster = fixture(config.paths.root)
result = build_registration_review(config, raster, samples=10, window_pixels=256)
page = Path(result["html"])
manifest_path = page.parent / "manifest.json"
manifest = json.loads(manifest_path.read_text())
scene = manifest["scenes"][0]
chips = ["r000000_c000000", "r000000_c000001", "r000000_c000002"]
manifest["scenes"] = [
    dict(scene, scene_id=f"scene-{i}", validation_chip_id=chip, splits=["train"])
    for i, chip in enumerate(chips)
]
manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
qa_server.load_queue = lambda *args: {
    "dataset": "fixture",
    "items": [
        {
            "chip_id": chip,
            "tag": "large" if i != 1 else "other",
            "trees": 10,
            "large": 5,
            "collisions": 0,
        }
        for i, chip in enumerate(chips)
    ],
}
qa_server.training_chip_catalog = lambda *args: {"pending": chips}
qa_server.training_image = lambda *args: (page.parent / scene["image"]).read_bytes()
backup_job.backup = lambda *args: None
print(str(page), flush=True)
qa_server.serve_registration_review(config, raster, review_dir=page.parent, port=8767)

import json
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import from_origin

from urban_tree_ml.config import load_config
from urban_tree_ml.model_debug import (
    MODEL_DEBUG_HTML,
    ModelDebugBundle,
    inject_studio_navigation,
    render_studio_home,
)


def _debug_fixture(tmp_path: Path) -> tuple[ModelDebugBundle, Path]:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_smoke.yaml")
    config.paths.root = tmp_path / "artifacts"
    evaluation_dir = (
        config.paths.root
        / "runs"
        / config.experiment
        / "evaluation"
        / "validation"
    )
    evaluation_dir.mkdir(parents=True)
    metrics = {
        "confidence_threshold": 0.35,
        "ground_truth_trees": 2,
        "predictions_above_threshold": 2,
        "chips": 1,
        "metrics_by_match_radius_m": {"2.0": {}, "4.0": {}},
    }
    (evaluation_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    predictions = pd.DataFrame(
        [
            {
                "chip_id": "r000000_c000000",
                "output_x": 10,
                "output_y": 12,
                "score": 0.8,
                "dbh_in": 9.5,
                "genus_id": 0,
                "genus_confidence": 0.7,
                "species_id": 0,
                "species_confidence": 0.6,
                "species_top_ids": [0, 1],
                "genus": "Acacia",
                "species": "Acacia dealbata",
            },
            {
                "chip_id": "r000000_c000000",
                "output_x": 80,
                "output_y": 82,
                "score": 0.5,
                "dbh_in": 4.0,
                "genus_id": 1,
                "genus_confidence": 0.5,
                "species_id": 1,
                "species_confidence": 0.4,
                "species_top_ids": [1, 0],
                "genus": "Arbutus",
                "species": "Arbutus menziesii",
            },
        ],
        index=[4, 7],
    )
    predictions.to_parquet(evaluation_dir / "predictions.parquet", index=True)
    ground_truth = pd.DataFrame(
        [
            {
                "tree_id": "tree-a",
                "chip_id": "r000000_c000000",
                "output_x": 10,
                "output_y": 12,
                "dbh_log1p": np.log1p(10.0),
                "genus_id": 0,
                "species_id": 0,
            },
            {
                "tree_id": "tree-b",
                "chip_id": "r000000_c000000",
                "output_x": 40,
                "output_y": 42,
                "dbh_log1p": np.nan,
                "genus_id": np.nan,
                "species_id": np.nan,
            },
        ]
    )
    ground_truth.to_parquet(evaluation_dir / "ground-truth.parquet", index=False)
    pd.DataFrame(
        [
            {
                "radius_m": 2.0,
                "prediction_index": 4,
                "tree_id": "tree-a",
                "distance_m": 0.0,
            }
        ]
    ).to_parquet(evaluation_dir / "matches.parquet", index=False)
    (evaluation_dir / "taxonomy.json").write_text(
        json.dumps(
            {
                "genera": ["Acacia", "Arbutus"],
                "species": ["Acacia dealbata", "Arbutus menziesii"],
            }
        ),
        encoding="utf-8",
    )
    raster_path = tmp_path / "image.tif"
    image = np.full((4, 256, 256), 100, dtype=np.uint8)
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=256,
        height=256,
        count=4,
        dtype="uint8",
        crs="EPSG:32610",
        transform=from_origin(550_000, 4_185_000, 0.6, 0.6),
    ) as target:
        target.write(image)
    return ModelDebugBundle(config, evaluation_dir, raster_path), raster_path


def test_model_debug_bundle_serves_metrics_predictions_and_chip_image(tmp_path: Path) -> None:
    bundle, _ = _debug_fixture(tmp_path)

    summary = bundle.summary()
    assert summary["metrics"]["ground_truth_trees"] == 2
    assert summary["chips"] == [
        {
            "chip_id": "r000000_c000000",
            "ground_truth": 2,
            "predictions": 2,
            "matched": 1,
            "missed": 1,
            "false_positive": 1,
            "species_errors": 0,
        }
    ]
    chip = bundle.chip("r000000_c000000")
    assert chip["ground_truth"][0]["species"] == "Acacia dealbata"
    assert chip["predictions"][0]["prediction_id"] == 4
    assert bundle.chip_image("r000000_c000000").startswith(b"\x89PNG")


def test_studio_html_links_registration_and_model_views() -> None:
    home = render_studio_home(model_available=True)
    registration = inject_studio_navigation("<html><body>review</body></html>")

    assert "Urban Tree Model Studio" in home
    assert "Validation artifacts loaded" in home
    assert 'href="/registration"' in home
    assert 'href="/model"' in registration
    assert "/api/model/summary" in MODEL_DEBUG_HTML
    assert "/api/model/chip/" in MODEL_DEBUG_HTML

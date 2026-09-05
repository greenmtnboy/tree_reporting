import json
from pathlib import Path

import numpy as np
import pandas as pd
import rasterio
from pyproj import Transformer
from rasterio.transform import from_origin

from urban_tree_ml.config import load_config
from urban_tree_ml.quality import (
    append_validation_chip_to_registration_review,
    build_registration_review,
    refresh_registration_heuristics,
)


def _write_qa_fixture(root: Path) -> Path:
    inventory_dir = root / "inventory" / "ussfo"
    inventory_dir.mkdir(parents=True)
    origin_x, origin_y = 550_000.0, 4_185_000.0
    pixels = [(40 + index * 14, 45 + index * 13) for index in range(12)]
    inverse = Transformer.from_crs("EPSG:32610", "EPSG:4326", always_xy=True)
    coordinates = [
        inverse.transform(origin_x + col * 0.6, origin_y - row * 0.6) for col, row in pixels
    ]
    coordinates[1] = coordinates[0]
    splits = ["train"] * 6 + ["validation"] * 4 + ["test"] * 2
    pd.DataFrame(
        {
            "tree_id": [f"tree-{index}" for index in range(12)],
            "species": [f"Genus{index % 4} species{index}" for index in range(12)],
            "genus": [f"Genus{index % 4}" for index in range(12)],
            "diameter_at_breast_height": [float(4 + index * 2) for index in range(12)],
            "longitude": [coordinate[0] for coordinate in coordinates],
            "latitude": [coordinate[1] for coordinate in coordinates],
            "split": splits,
            "split_eligible": [True] * 12,
            "split_block_x": [index // 2 for index in range(12)],
            "split_block_y": [index // 3 for index in range(12)],
        }
    ).to_parquet(inventory_dir / "inventory.parquet", index=False)

    raster_path = root / "image.tif"
    image = np.zeros((4, 256, 256), dtype=np.uint8)
    image[0] = np.arange(256, dtype=np.uint8)[None, :]
    image[1] = 100
    image[2] = np.arange(256, dtype=np.uint8)[:, None]
    image[3] = 180
    with rasterio.open(
        raster_path,
        "w",
        driver="GTiff",
        width=256,
        height=256,
        count=4,
        dtype="uint8",
        crs="EPSG:32610",
        transform=from_origin(origin_x, origin_y, 0.6, 0.6),
    ) as target:
        target.write(image)
    return raster_path


def test_registration_review_builds_clickable_ui_without_test_labels(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path / "artifacts"
    raster_path = _write_qa_fixture(config.paths.root)

    result = build_registration_review(config, raster_path, samples=6, window_pixels=64)

    assert result["samples"] >= 6
    assert 1 <= result["scenes"] < result["samples"]
    assert set(result["splits"]) == {"train", "validation"}
    assert result["test_labels_included"] is False
    html = Path(result["html"]).read_text(encoding="utf-8")
    assert "Export reviews" in html
    assert "Finalize training feedback" in html
    assert "/api/reviews" in html
    assert "/api/finalize" in html
    assert "localStorage" in html
    assert 'status: "aligned"' in html
    assert "Each numbered ring is one inventory tree" in html
    assert 'status: "offset", source: "human", image_x: x' in html
    assert "Reset all to aligned" in html
    assert "How should I classify ambiguous trees?" in html
    assert '<option value="duplicate">Duplicate</option>' in html
    assert "mark only the extra record(s) duplicate" in html
    assert "It will be excluded from supervision" in html
    assert "Full screen" in html
    assert 'classList.add("fullscreen")' in html
    assert 'className = "scene-next fullscreen-only"' in html
    assert 'event.key === "ArrowRight"' in html
    assert 'event.key === "ArrowLeft"' in html
    assert 'id="coverage-filter"' in html
    assert 'id="scene-status-filter"' in html
    assert 'statusFilter.value === "unreviewed"' in html
    assert "? !sceneDone : statusOf(sample.sample_id)" in html
    assert 'className = "done-button"' in html
    assert 'className = "heuristic-button"' in html
    assert 'className = "select-all-button"' in html
    assert 'id="show-stacks"' in html
    assert "Check non-veg" in html
    assert "images done" in html
    assert "scene_reviews: sceneReviews" in html
    assert "repeat(auto-fill, minmax(38px, 1fr))" in html
    assert "Tile seams" in html
    assert "event.shiftKey" in html
    assert "selectedByScene" in html
    assert "Center marking is disabled for a multi-selection." in html
    assert "selectionFor(scene.scene_id).size !== 1" in html
    assert 'a: "aligned", n: "not-tree", u: "uncertain", d: "duplicate"' in html
    assert 'map_action: "pano"' in html
    assert "https://www.google.com/maps/@?" in html
    assert "const streetViewEmbedApiKey = null;" in html
    assert "https://www.google.com/maps/embed/v1/streetview?" in html
    assert 'className = "street-view-panel"' in html
    assert 'referrerPolicy = "strict-origin-when-cross-origin"' in html
    assert "card.append(head, wrap, streetViewPanel" in html
    assert "maps.googleapis.com/maps/api/streetview/metadata?" in html
    assert "bearingDegrees(location" in html
    assert 'className = "street-view-camera"' in html
    assert 'className = "street-view-interaction"' in html
    assert 'streetViewFrame.classList.add("locked")' in html
    assert 'card.classList.add("street-view-open")' in html
    assert 'card.classList.contains("fullscreen")' in html
    assert 'className = "street-view-button"' not in html
    assert 'className = "tree-species-label"' in html
    assert 'sample.species || "Unknown species"' in html
    assert '.card.fullscreen .tree-species-label' in html
    assert 'label.dataset.status = statusOf(label.dataset.sampleId)' in html
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["metadata"]["test_labels_included"] is False
    assert manifest["metadata"]["rendered_scenes"] == len(manifest["scenes"])
    assert all(sample["split"] != "test" for sample in manifest["samples"])
    assert {sample["sample_id"] for sample in manifest["samples"]} == {
        sample_id for scene in manifest["scenes"] for sample_id in scene["sample_ids"]
    }
    assert any(scene["tree_count"] > 1 for scene in manifest["scenes"])
    assert manifest["metadata"]["vegetation_heuristic"]["action_status"] == "uncertain"
    assert all("vegetation_heuristic" in sample for sample in manifest["samples"])
    assert len({sample["image"] for sample in manifest["samples"]}) == len(manifest["scenes"])
    assert all(
        (Path(result["html"]).parent / scene["image"]).exists() for scene in manifest["scenes"]
    )


def test_registration_review_requires_explicit_opt_in_for_test_labels(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path / "artifacts"
    raster_path = _write_qa_fixture(config.paths.root)

    result = build_registration_review(
        config,
        raster_path,
        samples=12,
        window_pixels=64,
        include_test=True,
        output_dir=tmp_path / "with-test",
    )

    assert result["test_labels_included"] is True
    assert result["splits"]["test"] == 2
    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["metadata"]["coordinate_stack_groups"] == 1
    assert manifest["metadata"]["coordinate_stack_points"] == 2


def test_registration_review_extension_preserves_existing_ids_and_reviews(
    tmp_path: Path,
) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path / "artifacts"
    raster_path = _write_qa_fixture(config.paths.root)
    initial = build_registration_review(config, raster_path, samples=6, window_pixels=64)
    review_dir = Path(initial["html"]).parent
    initial_manifest = json.loads(Path(initial["manifest"]).read_text(encoding="utf-8"))
    reviews_path = review_dir / "reviews.json"
    reviews_path.write_text(
        json.dumps({"reviews": {"sample-0000": {"status": "offset"}}}),
        encoding="utf-8",
    )
    reviews_before = reviews_path.read_bytes()

    extended = build_registration_review(
        config,
        raster_path,
        samples=16,
        window_pixels=64,
        output_dir=review_dir,
        extend_existing=True,
    )

    extended_manifest = json.loads(Path(extended["manifest"]).read_text(encoding="utf-8"))
    assert extended["extended"] is True
    assert extended["added_scenes"] > 0
    assert (
        extended_manifest["samples"][: len(initial_manifest["samples"])]
        == initial_manifest["samples"]
    )
    assert (
        extended_manifest["scenes"][: len(initial_manifest["scenes"])] == initial_manifest["scenes"]
    )
    assert reviews_path.read_bytes() == reviews_before
    assert extended_manifest["metadata"]["extended_from_samples"] == initial["samples"]
    assert extended_manifest["metadata"]["extended_from_scenes"] == initial["scenes"]


def test_validation_chip_enters_existing_registration_review(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path / "artifacts"
    raster_path = _write_qa_fixture(config.paths.root)
    initial = build_registration_review(config, raster_path, samples=4, window_pixels=64)
    review_dir = Path(initial["html"]).parent
    manifest_before = json.loads(Path(initial["manifest"]).read_text(encoding="utf-8"))
    existing_tree_ids = {sample["tree_id"] for sample in manifest_before["samples"]}
    inventory = pd.read_parquet(config.paths.root / "inventory" / "ussfo" / "inventory.parquet")
    row = inventory[
        inventory["split"].isin(["train", "validation"])
        & ~inventory["tree_id"].isin(existing_tree_ids)
    ].iloc[0]
    with rasterio.open(raster_path) as source:
        transformer = Transformer.from_crs("EPSG:4326", source.crs, always_xy=True)
        x, y = transformer.transform(row.longitude, row.latitude)
        pixel_col, pixel_row = (~source.transform) * (x, y)
    truth = pd.DataFrame(
        [
            {
                "tree_id": row.tree_id,
                "split": row.split,
                "species": row.species,
                "genus": row.genus,
                "dbh_in": row.diameter_at_breast_height,
                "longitude": row.longitude,
                "latitude": row.latitude,
                "pixel_col": pixel_col,
                "pixel_row": pixel_row,
            }
        ]
    )

    added = append_validation_chip_to_registration_review(
        config,
        raster_path,
        review_dir,
        "r000000_c000000",
        truth,
    )
    repeated = append_validation_chip_to_registration_review(
        config,
        raster_path,
        review_dir,
        "r000000_c000000",
        truth,
    )

    manifest = json.loads((review_dir / "manifest.json").read_text(encoding="utf-8"))
    assert added["added"] is True
    assert repeated == {"scene_id": added["scene_id"], "added": False}
    assert len(manifest["samples"]) == len(manifest_before["samples"]) + 1
    scene = next(scene for scene in manifest["scenes"] if scene["scene_id"] == added["scene_id"])
    assert scene["validation_chip_id"] == "r000000_c000000"
    assert (review_dir / scene["image"]).exists()
    html = (review_dir / "index.html").read_text(encoding="utf-8")
    assert "requestedSceneId" in html
    assert "setExpanded(requestedCard, true)" in html
    assert "Back to validation" in html
    assert "location.href = curationReturn" in html


def test_heuristic_refresh_preserves_existing_reviews(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path / "artifacts"
    raster_path = _write_qa_fixture(config.paths.root)
    result = build_registration_review(config, raster_path, samples=6, window_pixels=64)
    review_dir = Path(result["html"]).parent
    reviews_path = review_dir / "reviews.json"
    reviews_path.write_text(
        json.dumps({"reviews": {"sample-0000": {"status": "uncertain"}}}),
        encoding="utf-8",
    )
    before = reviews_path.read_bytes()

    refreshed = refresh_registration_heuristics(
        config,
        raster_path,
        review_dir=review_dir,
    )

    assert refreshed["samples"] == result["samples"]
    assert refreshed["reviews_preserved"] is True
    assert reviews_path.read_bytes() == before
    manifest = json.loads(Path(refreshed["manifest"]).read_text(encoding="utf-8"))
    assert all("vegetation_heuristic" in sample for sample in manifest["samples"])
    assert "heuristics_refreshed_at" in manifest["metadata"]


def test_registration_review_prioritizes_and_records_mosaic_seams(tmp_path: Path) -> None:
    config = load_config(Path(__file__).parents[1] / "configs" / "sf_naip_baseline.yaml")
    config.paths.root = tmp_path / "artifacts"
    raster_path = _write_qa_fixture(config.paths.root)
    origin_x, origin_y = 550_000.0, 4_185_000.0
    split_x = origin_x + 128 * 0.6
    raster_path.with_suffix(".manifest.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "item_id": "west",
                        "bounds": [origin_x, origin_y - 256 * 0.6, split_x, origin_y],
                    },
                    {
                        "item_id": "east",
                        "bounds": [
                            split_x,
                            origin_y - 256 * 0.6,
                            origin_x + 256 * 0.6,
                            origin_y,
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    result = build_registration_review(config, raster_path, samples=6, window_pixels=64)

    manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
    assert manifest["metadata"]["mosaic_sources"] == 2
    assert manifest["metadata"]["seam_prioritized_samples"] >= 1
    seam_samples = [sample for sample in manifest["samples"] if sample["seam_priority"]]
    seam_scenes = [scene for scene in manifest["scenes"] if scene["seam_priority"]]
    assert seam_samples
    assert seam_scenes
    assert all(sample["source_item_ids"] for sample in seam_samples)
    assert all(sample["tile_seam_distance_m"] is not None for sample in seam_samples)
    assert sum(scene["tree_count"] for scene in seam_scenes) == len(seam_samples)

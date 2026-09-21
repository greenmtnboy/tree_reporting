import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import rasterio
from rasterio.transform import from_origin

from urban_tree_ml.config import load_config
from urban_tree_ml.model_debug import (
    CHIP_COMPARE_HTML,
    MODEL_DEBUG_HTML,
    RUN_HISTORY_HTML,
    ModelDebugBundle,
    RunDebugCatalog,
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


def test_detection_only_predictions_do_not_count_as_species_errors(tmp_path: Path) -> None:
    bundle, _ = _debug_fixture(tmp_path)
    bundle.predictions['species_id'] = None
    bundle.predictions['dbh_in'] = None
    assert all(c['species_errors'] == 0 for c in bundle._chip_summaries())
    assert 'p.dbh_in==null' in MODEL_DEBUG_HTML
    assert 'p.species_id!=null' in MODEL_DEBUG_HTML


def test_inventory_name_is_separate_from_checkpoint_targets(tmp_path: Path) -> None:
    bundle, _ = _debug_fixture(tmp_path)
    path = bundle.artifact_root / "run-inputs" / bundle.training_run_id / "ussfo"
    path.mkdir(parents=True)
    tree_id = bundle.ground_truth.iloc[0].tree_id
    pd.DataFrame({"tree_id": [tree_id], "species": ["Acer rare"]}).to_parquet(
        path / "inventory.parquet", index=False)
    bundle.ground_truth.loc[0, "species_id"] = np.nan
    bundle.ground_truth.loc[0, "genus_id"] = 0
    bundle._add_ground_truth_names()
    assert bundle.ground_truth.iloc[0].inventory_species == "Acer rare"
    assert pd.isna(bundle.ground_truth.iloc[0].species)
    assert bundle.ground_truth.iloc[0].genus == bundle.taxonomy["genera"][0]
    assert pd.isna(bundle.ground_truth.iloc[0].species_id)
    assert "Genus-only target:" in MODEL_DEBUG_HTML


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
            "goal_f2": None,
            "goal_false_positive": None,
            "species_errors": 0,
            "detection_f1": 0.5,
            "error_score": 2,
        }
    ]
    chip = bundle.chip("r000000_c000000")
    assert chip["ground_truth"][0]["species"] == "Acacia dealbata"
    assert chip["predictions"][0]["prediction_id"] == 4
    assert chip["predictions"][0]["center_supervision"]["status"] == "positive"
    assert not chip["predictions"][0]["center_supervision"]["would_penalize"]
    assert chip["predictions"][1]["center_supervision"]["status"] == "negative"
    assert chip["predictions"][1]["center_supervision"]["would_penalize"]
    assert chip["confidence_threshold"] == 0.35
    assert chip["display"]["output_stride"] == bundle.config.targets.output_stride
    assert bundle.chip_image("r000000_c000000").startswith(b"\x89PNG")


def test_studio_html_links_registration_and_model_views() -> None:
    home = render_studio_home(model_available=True, run_count=3)
    registration = inject_studio_navigation("<html><body>review</body></html>")

    assert "Urban Tree Model Studio" in home
    assert "Validation artifacts loaded" in home
    assert 'href="/registration"' in home
    assert 'href="/model"' in registration
    assert 'href="/runs"' in registration
    assert "3 validation runs" in home
    assert "/api/model/summary" in MODEL_DEBUG_HTML
    assert "/api/model/chip/" in MODEL_DEBUG_HTML
    assert "Inventory truth" in MODEL_DEBUG_HTML
    assert "Match radius" in MODEL_DEBUG_HTML
    assert 'id="modal"' in MODEL_DEBUG_HTML
    assert 'role="tooltip"' in MODEL_DEBUG_HTML
    assert "Curate this chip" in MODEL_DEBUG_HTML
    assert 'class="action-link"' in MODEL_DEBUG_HTML
    assert 'value="worst"' in MODEL_DEBUG_HTML
    assert 'id="review-status"' in MODEL_DEBUG_HTML
    assert 'value="not-final"' in MODEL_DEBUG_HTML
    assert "/api/curation-status" in MODEL_DEBUG_HTML
    assert "fetch(withRun('/api/curation-status'))" in MODEL_DEBUG_HTML
    assert "Unmatched prediction" in MODEL_DEBUG_HTML
    assert "supervisionText" in MODEL_DEBUG_HTML
    assert "penalized" in MODEL_DEBUG_HTML
    assert "detection_f1-b.detection_f1" in MODEL_DEBUG_HTML
    assert "threshold=${encodeURIComponent($('threshold').value)}" in MODEL_DEBUG_HTML
    assert "return=${encodeURIComponent(returnTo)}" in MODEL_DEBUG_HTML
    assert "/api/runs" in RUN_HISTORY_HTML
    assert "/api/runs/chip/" in CHIP_COMPARE_HTML
    assert 'class="action-link"' in CHIP_COMPARE_HTML
    assert "Validation chip ID" in registration


def test_run_catalog_discovers_runs_and_compares_a_chip(tmp_path: Path) -> None:
    selected, raster_path = _debug_fixture(tmp_path)
    catalog = RunDebugCatalog(selected.config, selected.directory, raster_path)

    summary = catalog.summary()
    assert summary["selected_run_id"] == selected.run_dir.name
    assert [run["run_id"] for run in summary["runs"]] == [selected.run_dir.name]
    assert catalog.bundle().summary()["run_id"] == selected.run_dir.name
    comparison = catalog.chip_comparison("r000000_c000000")
    assert comparison["selected_run_id"] == selected.run_dir.name
    assert comparison["runs"][0]["available"] is True
    assert comparison["runs"][0]["data"]["ground_truth"][0]["tree_id"] == "tree-a"


def test_pair_comparison_does_not_load_unselected_bundles(tmp_path, monkeypatch):
    selected, raster_path = _debug_fixture(tmp_path)
    catalog = RunDebugCatalog(selected.config, selected.directory, raster_path)
    record = catalog.summary()['runs'][0]
    selected_id = record['run_id']
    records = [record, {**record, 'run_id': 'other'}, {**record, 'run_id': 'unselected'}]
    for name in ['other', 'unselected']:
        catalog._runs[name] = {**catalog._runs[selected_id], 'run_id': name}
    monkeypatch.setattr(catalog, 'summary', lambda: {'runs': records})
    bundle = catalog.bundle(selected_id)
    loaded = []
    def tracked(run_id=None):
        loaded.append(run_id or selected_id)
        assert run_id != 'unselected'
        return bundle
    monkeypatch.setattr(catalog, 'bundle', tracked)
    result = catalog.chip_comparison('r000000_c000000', selected_id, 'other')
    assert {r['run']['run_id'] for r in result['runs']} == {selected_id, 'other'}
    assert len(result['choices']) == 3
    assert set(loaded) == {selected_id, 'other'}
    with pytest.raises(ValueError, match='not compatible'):
        catalog.chip_comparison('r000000_c000000', selected_id, 'missing')


@pytest.mark.parametrize("direct_link", [False, True])
def test_run_catalog_discovers_downloads_without_restart(tmp_path: Path, direct_link) -> None:
    import shutil

    selected, raster_path = _debug_fixture(tmp_path)
    catalog = RunDebugCatalog(selected.config, selected.directory, raster_path)
    cached = catalog.bundle()
    new_run = selected.run_dir.parent / "newly-downloaded"
    shutil.copytree(selected.run_dir, new_run)
    if direct_link:
        assert catalog.bundle("newly-downloaded").evaluation_id == "newly-downloaded"
    else:
        assert "newly-downloaded" in {run["run_id"] for run in catalog.summary()["runs"]}
    assert catalog.bundle() is cached
    assert catalog.selected_run_id == selected.run_dir.name


@pytest.mark.parametrize("future_options", [False, True])
def test_run_catalog_discovers_external_city_cohort_without_mixing_curation(
    tmp_path: Path, future_options: bool,
) -> None:
    selected, raster_path = _debug_fixture(tmp_path)
    external_dir = selected.run_dir / "evaluation" / "external-usbos"
    external_dir.mkdir(parents=True)
    for name in (
        "predictions.parquet",
        "ground-truth.parquet",
        "matches.parquet",
        "taxonomy.json",
    ):
        (external_dir / name).write_bytes((selected.directory / name).read_bytes())
    metrics = selected.metrics | {
        "cohort": "external-usbos",
        "city": "USBOS",
        "dataset": "boston-naip-external-sf-vocab-v1",
        "source_raster": str(raster_path),
    }
    (external_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    boston = load_config(
        Path(__file__).parents[1] / "configs" / "boston_naip_external.yaml"
    )
    boston.paths.root = selected.config.paths.root
    saved_config = boston.model_dump(mode="json")
    # Non-default values ensure the reader parses the new contract rather than
    # dropping fields and silently restoring defaults.
    crown_policy = {
        "crown_scaled_center": True,
        "crown_center_fraction": 0.4,
        "crown_center_max_sigma_m": 3.5,
        "crown_estimated_scale": 0.6,
    }
    saved_config["targets"].update(crown_policy)
    if future_options:
        saved_config["targets"]["future_crown_training_option"] = True
        saved_config["future_training_option"] = {"enabled": True}
    (external_dir / "evaluation-metadata.json").write_text(
        json.dumps(
            {
                "created_at": "2026-09-05T12:00:00+00:00",
                "source_raster": str(raster_path),
                "config": saved_config,
            }
        ),
        encoding="utf-8",
    )

    catalog = RunDebugCatalog(selected.config, selected.directory, raster_path)
    external_id = f"{selected.run_dir.name}::external-usbos"
    by_id = {record["run_id"]: record for record in catalog.summary()["runs"]}

    assert by_id[external_id]["city"] == "USBOS"
    assert by_id[external_id]["cohort"] == "external-usbos"
    assert catalog.bundle(external_id).config.inventory.city == "USBOS"
    parsed = catalog.bundle(external_id).config
    assert parsed.split.projected_crs == boston.split.projected_crs
    assert parsed.dataset == boston.dataset
    for key, value in crown_policy.items():
        assert getattr(parsed.targets, key) == value
    assert catalog.curation_available(selected.run_dir.name)
    assert not catalog.curation_available(external_id)
    assert len(catalog.chip_comparison("r000000_c000000")["runs"]) == 1
    external_comparison = catalog.chip_comparison("r000000_c000000", external_id)
    assert external_comparison["selected_run_id"] == external_id
    assert external_comparison["curation_available"] is False
    assert [entry["run"]["city"] for entry in external_comparison["runs"]] == ["USBOS"]

    external_catalog = RunDebugCatalog(selected.config, external_dir, raster_path)
    assert external_catalog.selected_run_id == external_id


def test_invalid_known_evaluation_config_never_falls_back_to_server_city(tmp_path):
    selected, raster_path = _debug_fixture(tmp_path)
    external = selected.run_dir / "evaluation" / "validation-usbos"
    external.mkdir()
    (external / "metrics.json").write_text(json.dumps({"city": "USBOS"}))
    config = selected.config.model_dump(mode="json")
    config["inventory"]["city"] = "USBOS"
    config["targets"]["crown_center_fraction"] = -1
    (external / "evaluation-metadata.json").write_text(json.dumps({"config": config}))
    with pytest.warns(UserWarning, match="invalid config"):
        catalog = RunDebugCatalog(selected.config, selected.directory, raster_path)
    assert f"{selected.run_dir.name}::validation-usbos" not in catalog._runs

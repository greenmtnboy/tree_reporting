from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from urban_tree_ml.config import load_config

app = typer.Typer(help="Train raw overhead-imagery models for urban tree inventories.")
inventory_app = typer.Typer(help="Prepare leakage-safe municipal inventory labels.")
imagery_app = typer.Typer(help="Discover and prepare overhead imagery.")
chips_app = typer.Typer(help="Materialize aligned image chips and dense targets.")
qa_app = typer.Typer(help="Generate human-reviewable data quality artifacts.")
app.add_typer(inventory_app, name="inventory")
app.add_typer(imagery_app, name="imagery")
app.add_typer(chips_app, name="chips")
app.add_typer(qa_app, name="qa")

ConfigPath = Annotated[Path, typer.Option("--config", exists=True, dir_okay=False)]


def _print(value: object) -> None:
    typer.echo(json.dumps(value, indent=2, sort_keys=True, default=str))


@app.command("check-config")
def check_config(config_path: ConfigPath) -> None:
    config = load_config(config_path)
    _print(config.model_dump(mode="json"))


@inventory_app.command("export")
def inventory_export(config_path: ConfigPath) -> None:
    from urban_tree_ml.inventory import export_inventory

    _print(export_inventory(load_config(config_path)))


@imagery_app.command("index")
def imagery_index(config_path: ConfigPath) -> None:
    from urban_tree_ml.imagery import index_stac_coverage

    _print(index_stac_coverage(load_config(config_path)))


@imagery_app.command("fetch")
def imagery_fetch(
    config_path: ConfigPath,
    item_id: Annotated[str, typer.Option("--item-id", help="Indexed STAC item identifier")],
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace an existing validated raster"),
    ] = False,
) -> None:
    from urban_tree_ml.imagery import fetch_stac_item

    _print(fetch_stac_item(load_config(config_path), item_id, overwrite=overwrite))


@imagery_app.command("fetch-selected")
def imagery_fetch_selected(
    config_path: ConfigPath,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace existing validated rasters"),
    ] = False,
) -> None:
    from urban_tree_ml.imagery import fetch_configured_stac_items

    _print(fetch_configured_stac_items(load_config(config_path), overwrite=overwrite))


@imagery_app.command("mosaic")
def imagery_mosaic(
    config_path: ConfigPath,
    year: Annotated[str, typer.Option("--year", help="Four-digit acquisition year")],
    output: Annotated[
        Path | None,
        typer.Option("--output", dir_okay=False, help="Output VRT path"),
    ] = None,
    overwrite: Annotated[
        bool,
        typer.Option("--overwrite", help="Replace an existing VRT and manifest"),
    ] = False,
) -> None:
    from urban_tree_ml.imagery import build_vrt_mosaic

    _print(
        build_vrt_mosaic(
            load_config(config_path),
            year,
            output=output,
            overwrite=overwrite,
        )
    )


@chips_app.command("build")
def chips_build(
    config_path: ConfigPath,
    raster: Annotated[
        Path | None,
        typer.Option("--raster", exists=True, dir_okay=False, help="Aligned multiband GeoTIFF/COG"),
    ] = None,
    feedback: Annotated[
        Path | None,
        typer.Option(
            "--feedback",
            exists=True,
            dir_okay=False,
            help="Finalized registration feedback (auto-detected by default)",
        ),
    ] = None,
    without_feedback: Annotated[
        bool,
        typer.Option(
            "--without-feedback",
            help="Do not auto-detect or apply finalized registration feedback",
        ),
    ] = False,
) -> None:
    from urban_tree_ml.chips import build_chips

    config = load_config(config_path)
    raster_path = raster or config.imagery.local_raster
    if raster_path is None:
        raise typer.BadParameter(
            "provide --raster or set imagery.local_raster after mosaicking selected STAC items"
        )
    if feedback is not None and without_feedback:
        raise typer.BadParameter("--feedback and --without-feedback cannot be used together")
    _print(
        build_chips(
            config,
            raster_path,
            feedback_path=feedback,
            use_default_feedback=not without_feedback,
        )
    )


@qa_app.command("registration")
def qa_registration(
    config_path: ConfigPath,
    raster: Annotated[
        Path,
        typer.Option("--raster", exists=True, dir_okay=False, help="NAIP GeoTIFF to review"),
    ],
    samples: Annotated[
        int,
        typer.Option(
            "--samples",
            min=1,
            help="Approximate minimum tree count; complete spatial scenes stay together",
        ),
    ] = 100,
    window_pixels: Annotated[
        int,
        typer.Option("--window-pixels", min=32, help="Even-sized source-pixel review window"),
    ] = 128,
    include_test: Annotated[
        bool,
        typer.Option(
            "--include-test",
            help="Include held-out test points; never use them to estimate a correction",
        ),
    ] = False,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            file_okay=False,
            help="Review directory (defaults under artifacts)",
        ),
    ] = None,
    extend_existing: Annotated[
        bool,
        typer.Option(
            "--extend-existing",
            help="Append new scenes while preserving existing scene/sample IDs and reviews",
        ),
    ] = False,
) -> None:
    from urban_tree_ml.quality import build_registration_review

    _print(
        build_registration_review(
            load_config(config_path),
            raster,
            samples=samples,
            window_pixels=window_pixels,
            include_test=include_test,
            output_dir=output,
            extend_existing=extend_existing,
        )
    )


@qa_app.command("serve")
def qa_serve(
    config_path: ConfigPath,
    raster: Annotated[
        Path,
        typer.Option("--raster", exists=True, dir_okay=False, help="Reviewed NAIP GeoTIFF"),
    ],
    review_dir: Annotated[
        Path | None,
        typer.Option("--review-dir", exists=True, file_okay=False),
    ] = None,
    evaluation_dir: Annotated[
        Path | None,
        typer.Option(
            "--evaluation-dir",
            exists=True,
            file_okay=False,
            help="Validation evaluation bundle (auto-detected from the configured run)",
        ),
    ] = None,
    bind: Annotated[str, typer.Option("--bind")] = "127.0.0.1",
    port: Annotated[int, typer.Option("--port", min=1, max=65535)] = 8765,
) -> None:
    from urban_tree_ml.qa_server import serve_registration_review

    serve_registration_review(
        load_config(config_path),
        raster,
        review_dir=review_dir,
        evaluation_dir=evaluation_dir,
        bind=bind,
        port=port,
    )


@qa_app.command("heuristics")
def qa_heuristics(
    config_path: ConfigPath,
    raster: Annotated[
        Path,
        typer.Option("--raster", exists=True, dir_okay=False, help="Reviewed RGB-NIR raster"),
    ],
    review_dir: Annotated[
        Path | None,
        typer.Option("--review-dir", exists=True, file_okay=False),
    ] = None,
    profile_id: Annotated[
        str,
        typer.Option("--profile-id", help="Identifier persisted with heuristic decisions"),
    ] = "naip-rgbn-conservative-gray-v1",
    ndvi_p90_max: Annotated[
        float,
        typer.Option("--ndvi-p90-max", min=-1, max=1),
    ] = -0.04,
    gray_fraction_min: Annotated[
        float,
        typer.Option("--gray-fraction-min", min=0, max=1),
    ] = 0.5,
) -> None:
    from urban_tree_ml.quality import refresh_registration_heuristics

    _print(
        refresh_registration_heuristics(
            load_config(config_path),
            raster,
            review_dir=review_dir,
            profile_id=profile_id,
            ndvi_p90_max=ndvi_p90_max,
            gray_fraction_min=gray_fraction_min,
        )
    )


@qa_app.command("finalize")
def qa_finalize(
    config_path: ConfigPath,
    raster: Annotated[
        Path,
        typer.Option("--raster", exists=True, dir_okay=False, help="Reviewed NAIP GeoTIFF"),
    ],
    review_dir: Annotated[
        Path | None,
        typer.Option("--review-dir", exists=True, file_okay=False),
    ] = None,
    reviews: Annotated[
        Path | None,
        typer.Option(
            "--reviews",
            exists=True,
            dir_okay=False,
            help="Exported review JSON; omit when reviews were saved by qa serve",
        ),
    ] = None,
    minimum_training_reviews: Annotated[
        int,
        typer.Option("--minimum-training-reviews", min=1),
    ] = 20,
) -> None:
    from urban_tree_ml.feedback import finalize_registration_feedback

    _print(
        finalize_registration_feedback(
            load_config(config_path),
            raster,
            review_dir=review_dir,
            reviews_path=reviews,
            minimum_training_reviews=minimum_training_reviews,
        )
    )


@qa_app.command("snapshot")
def qa_snapshot(
    config_path: ConfigPath,
    raster: Annotated[
        Path,
        typer.Option("--raster", exists=True, dir_okay=False, help="Reviewed NAIP raster"),
    ],
    review_dir: Annotated[
        Path | None,
        typer.Option("--review-dir", exists=True, file_okay=False),
    ] = None,
    reviews: Annotated[
        Path | None,
        typer.Option(
            "--reviews",
            exists=True,
            dir_okay=False,
            help="Exported review JSON; omit when reviews were saved by qa serve",
        ),
    ] = None,
) -> None:
    from urban_tree_ml.feedback import snapshot_registration_annotations

    _print(
        snapshot_registration_annotations(
            load_config(config_path),
            raster,
            review_dir=review_dir,
            reviews_path=reviews,
        )
    )


@app.command("train")
def train(
    config_path: ConfigPath,
    resume: Annotated[
        str | None,
        typer.Option("--resume", help="Checkpoint path, 'auto', or omit for a fresh run"),
    ] = None,
) -> None:
    from urban_tree_ml.training import run_training

    _print(run_training(load_config(config_path), resume=resume))


@app.command("evaluate")
def evaluate(
    config_path: ConfigPath,
    checkpoint: Annotated[
        Path,
        typer.Option("--checkpoint", exists=True, dir_okay=False),
    ],
    split: Annotated[str, typer.Option("--split")] = "validation",
    device: Annotated[
        str,
        typer.Option("--device", help="auto, cpu, cuda, or CUDA device"),
    ] = "auto",
    allow_test: Annotated[
        bool,
        typer.Option(
            "--allow-test",
            help="Unlock the sealed test split after decisions are frozen",
        ),
    ] = False,
    cohort: Annotated[
        str | None,
        typer.Option(
            "--cohort",
            help="Output cohort name, such as external-usbos; defaults to the split name",
        ),
    ] = None,
) -> None:
    from urban_tree_ml.evaluation import run_evaluation

    _print(
        run_evaluation(
            load_config(config_path),
            checkpoint,
            split=split,
            device_name=device,
            allow_test=allow_test,
            cohort=cohort,
        )
    )


if __name__ == "__main__":
    app()

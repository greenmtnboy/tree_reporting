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
app.add_typer(inventory_app, name="inventory")
app.add_typer(imagery_app, name="imagery")
app.add_typer(chips_app, name="chips")

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


@chips_app.command("build")
def chips_build(
    config_path: ConfigPath,
    raster: Annotated[
        Path | None,
        typer.Option("--raster", exists=True, dir_okay=False, help="Aligned multiband GeoTIFF/COG"),
    ] = None,
) -> None:
    from urban_tree_ml.chips import build_chips

    config = load_config(config_path)
    raster_path = raster or config.imagery.local_raster
    if raster_path is None:
        raise typer.BadParameter(
            "provide --raster or set imagery.local_raster after mosaicking selected STAC items"
        )
    _print(build_chips(config, raster_path))


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


if __name__ == "__main__":
    app()

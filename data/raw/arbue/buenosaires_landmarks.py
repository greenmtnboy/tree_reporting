#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["pyarrow", "pytrilogy"]
# ///

import sys
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa

sys.path.insert(0, str(Path(__file__).parent.parent))
from _ingest_shared import emit

UPDATED_AT = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)
LANDMARKS: list[dict[str, str]] = [
    {
        "landmark_id": "arbue-casa-rosada",
        "name": "Casa Rosada",
        "city": "ARBUE",
        "geometry_raw": "POINT(-58.3702762 -34.6080692)",
    },
    {
        "landmark_id": "arbue-el-cabildo",
        "name": "El Cabildo",
        "city": "ARBUE",
        "geometry_raw": "POINT(-58.3736709 -34.6088795)",
    },
    {
        "landmark_id": "arbue-plaza-de-mayo",
        "name": "Plaza de Mayo",
        "city": "ARBUE",
        "geometry_raw": "POINT(-58.3721787 -34.6084453)",
    },
    {
        "landmark_id": "arbue-eco-park",
        "name": "Buenos Aires Eco Park",
        "city": "ARBUE",
        "geometry_raw": "POINT(-58.4157363 -34.5775084)",
    },
    {
        "landmark_id": "arbue-tres-de-febrero",
        "name": "Tres de Febrero Park",
        "city": "ARBUE",
        "geometry_raw": "POINT(-58.4079408 -34.5745996)",
    },
    {
        "landmark_id": "arbue-teatro-colon",
        "name": "Teatro Colon",
        "city": "ARBUE",
        "geometry_raw": "POINT(-58.3831869 -34.6010855)",
    },
    {
        "landmark_id": "arbue-caminito",
        "name": "Caminito",
        "city": "ARBUE",
        "geometry_raw": "POINT(-58.3625689 -34.6393589)",
    },
    {
        "landmark_id": "arbue-obelisco",
        "name": "Obelisco",
        "city": "ARBUE",
        "geometry_raw": "POINT(-58.3816296 -34.6037094)",
    },
    {
        "landmark_id": "arbue-floralis-generica",
        "name": "Floralis Generica",
        "city": "ARBUE",
        "geometry_raw": "POINT(-58.3940018 -34.5816892)",
    },
    {
        "landmark_id": "arbue-piramide-de-mayo",
        "name": "Piramide de Mayo",
        "city": "ARBUE",
        "geometry_raw": "POINT(-58.3721649 -34.6084064)",
    },
]


def build_table() -> pa.Table:
    return pa.table(
        {
            "landmark_id": pa.array(
                [row["landmark_id"] for row in LANDMARKS], type=pa.string()
            ),
            "name": pa.array([row["name"] for row in LANDMARKS], type=pa.string()),
            "city": pa.array([row["city"] for row in LANDMARKS], type=pa.string()),
            "geometry_raw": pa.array(
                [row["geometry_raw"] for row in LANDMARKS], type=pa.string()
            ),
            "arbue_landmark_data_updated_through": pa.array(
                [UPDATED_AT for _ in LANDMARKS], type=pa.timestamp("us", tz="UTC")
            ),
        }
    )


if __name__ == "__main__":
    emit(build_table())

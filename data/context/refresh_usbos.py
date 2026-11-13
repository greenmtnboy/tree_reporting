#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb", "numpy", "pyarrow", "rasterio", "pyproj", "requests", "shapely>=2.1"]
# ///
"""Scheduled Boston source refresh; fetch current sources and publish latest assets."""
from refresh import main

if __name__ == '__main__':
    main(default_city='USBOS', default_publish=True)

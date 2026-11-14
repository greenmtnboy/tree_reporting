#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.13"
# dependencies = ["duckdb", "numpy", "pyarrow", "rasterio", "pyproj", "requests", "shapely>=2.1"]
# ///
"""Local context seed; --publish uploads to the same stable paths as the cloud job."""
from refresh import main

if __name__ == '__main__':
    main()

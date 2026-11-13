#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = ["numpy", "pyarrow", "rasterio", "shapely>=2.1", "pyproj"]
# ///
"""Package the approved Boston study's frozen inputs; never refresh upstream data."""
import argparse
import ast
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from pyproj import CRS, Transformer
import rasterio
from rasterio.features import geometry_mask
from rasterio.shutil import copy as raster_copy
from rasterio.transform import Affine
import shapely
from shapely.geometry import box, shape

from context_release import canonical, digest, verify


def geoparquet(path, rows, geometries, epsg):
    # Include late/optional columns: the first row is often heat-ineligible.
    keys = sorted(set().union(*(row.keys() for row in rows)))
    table = pa.Table.from_pylist([{k: row.get(k) for k in keys} for row in rows])
    table = table.append_column('geometry', pa.array([shapely.to_wkb(g) for g in geometries], type=pa.binary()))
    bounds = np.array([g.bounds for g in geometries])
    geo = {'version': '1.1.0', 'primary_column': 'geometry', 'columns': {'geometry': {
        'encoding': 'WKB', 'geometry_types': sorted({g.geom_type for g in geometries}),
        'crs': CRS.from_epsg(epsg).to_json_dict(),
        'bbox': [float(bounds[:, 0].min()), float(bounds[:, 1].min()),
                 float(bounds[:, 2].max()), float(bounds[:, 3].max())]}}}
    table = table.replace_schema_metadata({b'geo': canonical(geo)})
    pq.write_table(table, path, compression='zstd')


def code_dependencies(root):
    found = set()
    def visit(name):
        p = root / 'scripts' / (name + '.py')
        if name in found or not p.exists():
            return
        found.add(name)
        for node in ast.walk(ast.parse(p.read_text(encoding='utf-8'))):
            if isinstance(node, ast.ImportFrom) and node.module:
                visit(node.module.split('.')[0])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    visit(alias.name.split('.')[0])
    visit('render_boston_canopy_grid')
    visit('render_boston_canopy_mobile')
    return [root / 'scripts' / (n + '.py') for n in sorted(found)]


def export(root, output):
    root, output = Path(root).resolve(), Path(output).resolve()
    if output.exists():
        raise ValueError('Choose a new output directory; existing releases are immutable')
    read = lambda p: json.loads(p.read_text(encoding='utf-8'))
    report_path = root / 'scripts/boston_canopy_renders/boston_canopy_heat_twelve_regions.json'
    report = read(report_path)
    provenance = report['provenance']
    fingerprint = report['canopy_comparison']['fingerprint']
    cache = root / '.tmp/boston-heat'
    tree, crowns = cache / 'usbos-trees.parquet', cache / 'usbos-crown-predictions.parquet'
    water_path = root / '.tmp' / Path(provenance['water']['cache'].replace('\\', '/')).name
    for path, expected in ((tree, fingerprint['tree_sha256']), (crowns, fingerprint['crown_sha256']),
                           (water_path, fingerprint['water_sha256'])):
        if digest(path) != expected:
            raise ValueError(f'Frozen input changed: {path.name}')
    heat_candidates = []
    for path in sorted(cache.glob('landsat-*.npz')):
        meta = read(path.with_suffix('.json'))
        if all(meta.get(k) == provenance['heat'].get(k) for k in ('year', 'bbox', 'crs', 'transform', 'dates')):
            heat_candidates.append((path, meta))
    if len(heat_candidates) != 1:
        raise ValueError('Expected exactly one matching frozen heat stack')
    heat_path, meta = heat_candidates[0]
    dates = provenance['dates']
    water = read(water_path)
    output.mkdir(parents=True)
    assets = {}
    def record(name, role, **extra):
        p = output / name
        assets[name] = {'role': role, 'bytes': p.stat().st_size, 'sha256': digest(p), **extra}
    def copy(path, role, restore=False):
        relative = path.relative_to(root).as_posix()
        name = 'replay/' + relative
        dest = output / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == '.json':
            # Retain provenance without publishing workstation/user paths.
            def portable(value):
                if isinstance(value, str):
                    return value.replace(str(root), '.').replace(root.as_posix(), '.')
                if isinstance(value, list):
                    return [portable(v) for v in value]
                if isinstance(value, dict):
                    return {k: portable(v) for k, v in value.items()}
                return value
            dest.write_bytes(canonical(portable(read(path))))
        else:
            shutil.copyfile(path, dest)
        record(name, role, **({'restore_to': relative} if restore else {}))

    used = set(report['canopy_comparison']['paired_cell_ids'])
    cells = [{**c, 'used_in_metro_plot': c['id'] in used} for c in report['cells']]
    size = report['cell_size_m']
    geoparquet(output / 'analysis_cells.parquet', cells,
               [box(c['west'], c['south'], c['west'] + size, c['south'] + size) for c in cells], 32619)
    record('analysis_cells.parquet', 'analysis', crs=report['crs'], cell_size_m=size,
           rows=len(cells), plotted_rows=len(used), daily_temperature_dates=dates,
           units={'canopy_percent': 'percent of land area', 'temperature': 'degree Celsius',
                  'land_km2': 'square kilometre', 'canopy_area_m2': 'square metre',
                  'crown_completeness': 'fraction', 'heat_coverage': 'fraction'})
    geoparquet(output / 'water.parquet', [f['properties'] for f in water['features']],
               [shape(f['geometry']) for f in water['features']], 4326)
    record('water.parquet', 'water', crs='EPSG:4326', rows=len(water['features']),
           source='massgis-water', filter=provenance['water']['filter'])
    with np.load(heat_path) as archive:
        temperatures = archive['temperature'][[meta['dates'].index(d) for d in dates]]
    water_union = shapely.union_all([shapely.make_valid(shape(f['geometry'])) for f in water['features']])
    project = Transformer.from_crs(4326, meta['crs'], always_xy=True)
    water_utm = shapely.transform(water_union, project.transform, interleaved=False)
    mean = temperatures.mean(axis=0)
    mean[geometry_mask([water_utm], mean.shape, Affine(*meta['transform']), invert=True)] = np.nan
    if hashlib.sha256(mean.tobytes()).hexdigest() != provenance['heat_common_mean_sha256']:
        raise ValueError('Frozen heat/water stack no longer reproduces the approved common-pixel mean')
    temporary = output / 'heat-intermediate.tif'
    with rasterio.open(temporary, 'w', driver='GTiff', width=temperatures.shape[2],
                       height=temperatures.shape[1], count=len(dates), dtype='float32',
                       crs=meta['crs'], transform=Affine(*meta['transform']), nodata=np.nan,
                       tiled=True, compress='deflate') as dst:
        dst.write(temperatures)
        dst.descriptions = tuple(dates)
        dst.update_tags(units='degree Celsius', quality=meta['quality'])
    raster_copy(temporary, output / 'surface_temperature.tif', driver='COG',
                compress='DEFLATE', overview_resampling='NEAREST')
    temporary.unlink()
    with rasterio.open(output / 'surface_temperature.tif') as src:
        if not np.array_equal(src.read(), temperatures, equal_nan=True) or src.descriptions != tuple(dates):
            raise ValueError('Heat raster roundtrip changed values or dates')
    record('surface_temperature.tif', 'surface-temperature', source='landsat-c2-l2',
           crs=meta['crs'], transform=meta['transform'], units='degree Celsius',
           dates=dates, delivered_pixel_m=30, native_thermal_sampling_m=100,
           nodata='NaN', quality=meta['quality'],
           processing='Per-date QA-masked mosaics. For the poster, intersect finite pixels across ALL bands and mask water; average identical land pixels on each date. Base resolution only for statistics.')

    # Original caches preserve every date and legacy loader contract. These are
    # separate from the interoperable five-band raster, not additional samples.
    replay = [tree, crowns, water_path, heat_path, heat_path.with_suffix('.json'),
              root / 'scripts/boston_canopy_renders/boston_canopy_heat.json',
              cache / Path(report['canopy_comparison']['cache'].replace('\\', '/')).name]
    for p in (tree, crowns):
        if p.with_suffix('.source.json').exists():
            replay.append(p.with_suffix('.source.json'))
    for path in replay:
        copy(path, 'frozen-replay-input', restore=True)
    copy(report_path, 'analysis-report')
    source_paths = code_dependencies(root) + [root / 'scripts/boston_sharing_frame.json',
                                             root / 'scripts/boston_heat_location_candidates.json',
                                             root / 'imagery_model/bulk_areas/usbos-metro-circle.geojson']
    # LF-normalized code hashes are stable across Windows/Linux Git checkouts.
    code = {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes().replace(b'\r\n', b'\n')).hexdigest()
            for p in source_paths}
    manifest = {'schema_version': 1, 'study': 'boston-summertime-shadeness', 'city': 'USBOS',
        'assets': assets, 'code_sha256_lf': code,
        'code_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip(),
        'code_worktree_dirty': bool(subprocess.check_output(
            ['git', 'status', '--porcelain', '--', *code], cwd=root, text=True).strip()),
        'export_runtime': {n: importlib.metadata.version(n) for n in ('numpy', 'pyarrow', 'rasterio', 'shapely', 'pyproj')},
        'sources': {
            'landsat-c2-l2': {'title': 'USGS Landsat 8/9 Collection 2 Level-2 Surface Temperature',
                'url': 'https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature',
                'access': 'https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2',
                'rights': 'USGS Landsat data are public domain; acknowledge USGS.', 'items': meta['items']},
            'massgis-water': {'title': 'MassGIS MassDEP Hydrography (1:25,000), USGS / MassDEP',
                'url': 'https://www.mass.gov/info-details/massgis-data-massdep-hydrography-125000',
                'service': provenance['water']['service'], 'rights': 'Retains provider attribution and terms; no blanket bundle licence.'},
            'trees': {'title': 'Published municipal, Arnold Arboretum, OSM, community and aerial records',
                'url': 'https://arborary.world', 'rights': 'Original source terms apply; includes © OpenStreetMap contributors (ODbL).',
                'aerial_imagery': 'USDA National Agriculture Imagery Program (NAIP)',
                'widths': report['canopy_comparison']['sources']}},
        'method': {'dates': dates, 'temperature': report['temperature'],
                   'canopy': report['canopy_comparison']['metric'], 'water_filter': report['water_filter'],
                   'sample': report['canopy_comparison']['sample'], 'interpretation': 'Correlation, not causation. Five clear-sky dates, not an all-weather summer mean. Surface, not air temperature. Trees are a November 2026 snapshot; heat is summer 2025.'},
        'reproduce': ['uv run scripts/context_release.py restore BUNDLE --checkout CHECKOUT',
                      'uv run scripts/render_boston_canopy_grid.py', 'uv run scripts/render_boston_canopy_mobile.py']}
    (output / 'manifest.json').write_bytes(canonical(manifest))
    verify(output)
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    result = export(args.source_root, args.output)
    print(f"Validated {len(result['assets'])} assets; manifest SHA-256 {digest(args.output / 'manifest.json')}")


if __name__ == '__main__':
    main()

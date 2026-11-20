"""Read-only queue preparation. Never materialize scenes or snapshot annotations."""
from functools import lru_cache
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlencode

from urban_tree_ml.review_tables import review_backfill_directory
from urban_tree_ml.mosaic_coverage import missing_chips


def model_identity(record):
    """The imagery ownership checks need metadata, not prediction dataframes."""
    return SimpleNamespace(config=record['bundle_config'], raster_path=record['raster_path'])


@lru_cache(maxsize=8)
def _split_ids(path, version, split):
    import polars as pl
    chips = pl.scan_parquet(path).filter(pl.col('split') == split).select(pl.col('chip_id').cast(pl.String))
    return frozenset(chips.collect().to_series())


@lru_cache(maxsize=32)
def _original_validation_ids(path, version, city):
    receipt = json.loads(path.read_text(encoding='utf-8'))
    entry = receipt['cities'].get(city)
    if entry is None:
        return None
    return frozenset(entry['chip_ids']) | frozenset(entry['excluded_validation_chips'])


def review_split_ids(context, record, split):
    if split not in {'train', 'validation'}:
        raise ValueError('Only training and validation queues may be prepared')
    root = Path(context.config.paths.root)
    if split == 'validation':
        # Fine-tuning trims chips.parquet to the scored subset. Its receipt
        # records both sides of that intersection on this exact city's grid.
        from urban_tree_ml.temporal_studio import record_acquisition
        receipt = root / 'runs' / record['training_run_id'] / 'curated-validation.json'
        if not record_acquisition(record) and not record['metrics'].get('prediction_only') and receipt.is_file():
            stat = receipt.stat()
            original = _original_validation_ids(receipt, (stat.st_mtime_ns, stat.st_size), record['city'].lower())
            if original is not None:
                return original
    path = Path(context.config.paths.root) / 'chips' / record['dataset'] / 'chips.parquet'
    stat = path.stat()
    return _split_ids(str(path), (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size), split)


def require_chip(context, record, chip, split):
    if chip not in review_split_ids(context, record, split):
        raise ValueError('Chip is not in the selected city dataset and split')
    if record.get('raster_path') and missing_chips(record['raster_path'],
                                                 record['bundle_config'].imagery.chip_pixels, [chip]):
        raise ValueError('This tile has insufficient source imagery coverage')


@lru_cache(maxsize=16)
def _saved_coverage(path, version):
    coverage = json.loads(path.read_text(encoding='utf-8'))
    if coverage.get('complete'):
        return frozenset(row['chip_id'] for row in coverage['chips'])
    return None


def prepare_member(context, records, manifest, chip, split, run, compare_run):
    from urban_tree_ml.qa_server import _context_matches_bundle
    if split not in {'train', 'validation'}:
        raise ValueError('Only training and validation queues may be prepared')
    def resolve(value):
        family = (value or '').split('::')[0]
        candidates = [r for r in records if r['training_run_id'] == family
                      and r['city'].lower() == context.city
                      and r['metrics'].get('split', 'validation') == split
                      and _context_matches_bundle(context, model_identity(r))]
        exact = next((r for r in candidates if r['run_id'] == value), None)
        if len(candidates) < 2:
            return exact or next(iter(candidates), None)
        for candidate in ([exact] if exact else []) + [r for r in candidates if r is not exact]:
            try:
                if chip in review_split_ids(context, candidate, split):
                    return candidate
            except FileNotFoundError:
                continue
        return candidates[0]
    record = resolve(run)
    if record is None or not _context_matches_bundle(context, model_identity(record)):
        raise ValueError('Selected model does not share this curation imagery and split')
    require_chip(context, record, chip, split)
    other = resolve(compare_run) if compare_run and compare_run != 'auto' else None
    if compare_run and compare_run != 'auto' and (other is None or not _context_matches_bundle(context, model_identity(other))):
        raise ValueError('Comparison model does not share this curation imagery and split')
    comparison = other['run_id'] if other else (compare_run or 'auto')
    scene = next((s for s in manifest['scenes'] if s.get('validation_chip_id') == chip
                  and split in s.get('splits', [])), None)
    images = []
    if scene:
        # Both URLs are read-only. In particular, do not prefetch /curate.
        relative = str(scene['image']).replace('\\', '/')
        asset = (context.directory / relative).resolve()
        asset.relative_to(context.directory.resolve())
        acquisition = getattr(context, 'acquisition_id', None)
        from urban_tree_ml.review_context import scene_image_urls
        images = list(scene_image_urls(context, scene, f'/city-assets/{acquisition or context.city}/{relative}'))
    else:
        # Read source pixels without making a scene, image file, or annotation.
        params = dict(city=context.city, chip=chip, split=split, run=record['run_id'])
        images = ['/api/review-next-image?' + urlencode({**params, 'border': border}) for border in (0, 16)]
    coverage_path = record['evaluation_dir'] / 'inference-coverage.json'
    coverage = None
    if coverage_path.exists():
        stat = coverage_path.stat()
        coverage = _saved_coverage(coverage_path, (stat.st_mtime_ns, stat.st_size))
    unavailable = coverage is not None and chip not in coverage
    if unavailable and (extra := review_backfill_directory(record)) is not None:
        path = extra / 'inference-coverage.json'
        stat = path.stat()
        unavailable = chip not in _saved_coverage(path, (stat.st_mtime_ns, stat.st_size))
    return dict(city=context.city, chip=chip, split=split, run=record['run_id'], inference_unavailable=unavailable,
                compare_run=comparison, scene=scene['scene_id'] if scene else None,
                images=images,
                prediction_url=None if unavailable else '/api/model/chip/' + chip + '?' + urlencode({'run': record['run_id']}),
                comparison_url='/api/runs/chip/' + chip + '?' + urlencode({'run': record['run_id'], 'compare_run': comparison}))


def queue_image(context, record, chip, split, border):
    from urban_tree_ml.chip_geometry import chip_offsets
    from urban_tree_ml.review_context import review_window_image
    if border not in (0, 16):
        raise ValueError('Queue preview border must be 0 or 16 pixels')
    require_chip(context, record, chip, split)
    size = context.config.imagery.chip_pixels
    row, col = chip_offsets(chip, size, id_chip_pixels=record['bundle_config'].imagery.chip_pixels)
    return review_window_image(context, col, row, size, size, border)

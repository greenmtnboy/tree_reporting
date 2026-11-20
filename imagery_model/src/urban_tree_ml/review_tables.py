"""Read frozen curator tables, preserving the prediction IDs stored by the exporter."""
import polars as pl
import pyarrow.parquet as pq


def scan_predictions(path):
    schema = pq.read_schema(path)
    indices = (schema.pandas_metadata or {}).get('index_columns', [])
    frame = pl.scan_parquet(path)
    if indices and isinstance(indices[0], str):
        [index] = indices
        frame = frame.with_columns(pl.col(index).cast(pl.Int64).alias('prediction_id'))
        return frame.drop(index) if index != 'prediction_id' else frame
    index = indices[0] if indices else {}
    rows = pl.int_range(pl.len(), dtype=pl.Int64)
    return frame.with_columns((index.get('start', 0) + rows * index.get('step', 1)).alias('prediction_id'))


def read_table(path):
    # Physical index fields in older pandas exports are metadata, not UI columns.
    schema = pq.read_schema(path)
    indices = (schema.pandas_metadata or {}).get('index_columns', [])
    return pl.read_parquet(path, columns=[c for c in schema.names if c not in indices])


def by_chip(frame):
    return {chip: group for (chip,), group in frame.partition_by('chip_id', as_dict=True).items()}


def above_threshold(threshold):
    score = pl.col('score')
    return score.is_not_nan() & (score >= threshold)


def review_backfill_directory(record):
    """Locate an atomically published companion to this frozen validation cohort."""
    directory = record.get('evaluation_dir')
    if directory is None or record['metrics'].get('split') != 'validation':
        return None
    family = directory.parent.parent
    if directory.parent.name != 'evaluation' or family.parent.name != 'runs':
        return None
    extra = family.parent.parent / 'review-inference' / family.name / 'evaluation' / directory.name
    return extra if (extra / 'review-provenance.json').is_file() else None

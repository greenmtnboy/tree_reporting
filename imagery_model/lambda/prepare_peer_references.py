"""Seed new imagery review from the same underlying city inventory.

Never changes evaluations, annotations, masks or training admission. A receipt
published last makes the companion visible to the review UI.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def select_labels(source, destination, source_manifest, destination_manifest, truth):
    """Require unchanged spatial/split coordinates before reusing output cells."""
    if source['city'] != destination['city'] or source['split'] != destination['split']:
        raise ValueError('Peer references cannot cross city or split')
    if source['split'] not in ('train', 'validation'):
        raise ValueError('Holdouts cannot become review references')
    for key in ('chip_pixels', 'resolution_m'):
        if source['config']['imagery'][key] != destination['config']['imagery'][key]:
            raise ValueError('Reference pixel grid differs')
    if source['config']['targets']['output_stride'] != destination['config']['targets']['output_stride']:
        raise ValueError('Reference output grid differs')
    if source_manifest.chip_id.duplicated().any() or destination_manifest.chip_id.duplicated().any():
        raise ValueError('Ambiguous chip membership')
    original = source_manifest.set_index('chip_id')
    target = destination_manifest.set_index('chip_id')
    if not set(target.index) <= set(original.index):
        raise ValueError('Missing source chip membership')
    columns = ['split', 'row_offset', 'column_offset']
    if not original.loc[target.index, columns].equals(target[columns]):
        raise ValueError('Peer chip coordinates or split differ')
    if not target.split.eq(source['split']).all():
        raise ValueError('Wrong source cohort')
    return truth[truth.chip_id.isin(target.index)].copy()


def inventory_labels(inventory, target, source, config, taxonomy, cutoff):
    """Project inventory into exact model windows, using normal label cell rules.

    No prior-year review state, manual trees, removals or offsets enter here.
    """
    import numpy as np
    import pandas as pd
    from pyproj import Transformer
    from urban_tree_ml.inventory import planted_after_imagery
    from urban_tree_ml.targets import PointLabel, point_label_output_cell

    frame = inventory[inventory.split_eligible & ~planted_after_imagery(inventory, cutoff)].copy()
    frame = frame[frame.split.isin(set(target.split))].copy()
    project = Transformer.from_crs('EPSG:4326', source.crs, always_xy=True)
    x,y = project.transform(frame.longitude.to_numpy(),frame.latitude.to_numpy())
    frame['pixel_col'],frame['pixel_row'] = (~source.transform)*(x,y)
    pixels,stride = config['imagery']['chip_pixels'],config['targets']['output_stride']
    frame = frame[(frame.pixel_col>=0)&(frame.pixel_row>=0)&(frame.pixel_col<source.width)&(frame.pixel_row<source.height)].copy()
    frame['chip_id'] = [f'r{int(y//pixels):06d}_c{int(x//pixels):06d}' for x,y in zip(frame.pixel_col,frame.pixel_row)]
    frame = frame.merge(target[['chip_id','split','row_offset','column_offset']], on=['chip_id','split'], how='inner',validate='many_to_one')
    cells = [point_label_output_cell(PointLabel(x=r.pixel_col-r.column_offset,y=r.pixel_row-r.row_offset),
             image_height=pixels,image_width=pixels,stride=stride) for r in frame.itertuples()]
    frame['output_x'] = [c[0] for c in cells];frame['output_y'] = [c[1] for c in cells]
    # Normal detector targets cannot distinguish two labels in one output cell.
    frame = frame[~frame.duplicated(['chip_id','output_x','output_y'],keep=False)].copy()
    for kind in ('genus','species'):
        vocabulary = taxonomy['genera' if kind=='genus' else 'species']
        ids = {v:i for i,v in enumerate(vocabulary)}
        frame[kind+'_id'] = frame[kind].map(ids).where(frame[kind+'_eligible'])
    frame['dbh_log1p'] = frame.dbh_log1p.where(frame.dbh_eligible)
    frame['crown_radius_m'] = np.nan;frame['crown_source'] = None;frame['vegetation_type'] = ''
    columns = ['tree_id','split','chip_id','longitude','latitude','pixel_col','pixel_row',
               'output_x','output_y','dbh_log1p','genus_id','species_id','crown_radius_m','crown_source','vegetation_type']
    return frame[columns].reset_index(drop=True)


def prepare_expanded_reference(root, evaluation, inventory_path, acquisition, source_dates):
    """Project inventory into a new coverage grid with explicit development splits."""
    import pandas as pd
    import rasterio
    from urban_tree_ml.peer_references import directory, reference
    existing = reference(evaluation)
    if existing:
        return existing[1]
    existing_receipt = directory(evaluation)/'reference.json'
    if existing_receipt.exists():
        # reference() already verified this empty companion's provenance.
        return read(existing_receipt)
    metadata = read(evaluation/'evaluation-metadata.json')
    config = metadata['config']
    if (not metadata.get('prediction_only') or metadata['split'] not in {'train','validation'}
            or config['imagery']['acquisition_id'] != acquisition
            or not pd.read_parquet(evaluation/'ground-truth.parquet').empty):
        raise ValueError('Expected an acquisition-qualified unlabeled development cohort')
    manifest_path = root/'chips'/metadata['dataset']/'chips.parquet'
    manifest = pd.read_parquet(manifest_path)
    target = manifest[manifest.split.eq(metadata['split'])]
    coverage = read(evaluation/'inference-coverage.json')
    if (target.chip_id.duplicated().any() or not coverage['complete']
            or {row['chip_id'] for row in coverage['chips']} != set(target.chip_id)):
        raise ValueError('Reference windows differ from inference coverage')
    cutoff = min(source_dates)[:10]
    with rasterio.open(config['imagery']['local_raster']) as source:
        labels = inventory_labels(pd.read_parquet(inventory_path), target, source, config,
                                  read(evaluation/'taxonomy.json'), cutoff)
    output = directory(evaluation)
    output.mkdir(parents=True, exist_ok=False)
    labels.to_parquet(output/'ground-truth.parquet', index=False)
    receipt = dict(policy='city_inventory_reference_v1',training_eligible=False,
        acquisition_id=acquisition,source_kind='city_inventory',inventory_sha256=sha(inventory_path),
        planting_cutoff=cutoff,destination_cohort=evaluation.name,tiles=len(target),reference_trees=len(labels),
        reference_sha256=sha(output/'ground-truth.parquet'),predictions_sha256=sha(evaluation/'predictions.parquet'),
        taxonomy_sha256=sha(evaluation/'taxonomy.json'),destination_manifest_sha256=sha(manifest_path),
        geographic_grid_verified=True)
    save_path = output/'reference.json'
    save_path.write_text(json.dumps(receipt,indent=2)+'\n',encoding='utf-8')
    assert reference(evaluation) is not None or labels.empty
    return receipt


def prepare(root, run, acquisition):
    import pandas as pd
    import rasterio
    import numpy as np
    from urban_tree_ml.peer_references import directory, reference

    record = read(root/'run-inputs'/acquisition/'acquisition.json')
    city = record['city']
    prepared = root/'run-inputs'/(run+'-'+acquisition+'-predictions')
    if not (prepared/'COMPLETE').exists():
        raise ValueError('Prediction pass is incomplete')
    cfg = read(prepared/'config.json')
    if sha(record['reference_grid']) != record['download_receipt']['source_grid_sha256']:
        raise ValueError('Source grid provenance changed')
    # The prediction model raster is a resampling of the same geographic grid.
    with rasterio.open(record['reference_grid']) as old, rasterio.open(cfg['imagery']['local_raster']) as new:
        if old.crs != new.crs or not np.allclose(old.bounds, new.bounds, rtol=0, atol=1e-6):
            raise ValueError('Geographic grids differ')
        if not np.allclose(new.res, [cfg['imagery']['resolution_m']]*2, rtol=0, atol=1e-9):
            raise ValueError('Model pixel resolution differs')
    results = []
    for split in ('train', 'validation'):
        old = root/'runs'/run/'evaluation'/f'{split}-{city}'
        if city == 'ussfo' and split == 'validation' and not old.exists():
            old = old.parent/'validation'
        new = root/'runs'/run/'evaluation'/f'{split}-{acquisition}'
        existing = reference(new)
        if existing:
            results.append(existing[1])
            continue
        source, dest = read(old/'evaluation-metadata.json'), read(new/'evaluation-metadata.json')
        if not dest.get('prediction_only') or len(pd.read_parquet(new/'ground-truth.parquet')):
            raise ValueError('Destination must be an unlabeled prediction snapshot')
        if read(old/'taxonomy.json') != read(new/'taxonomy.json'):
            raise ValueError('Taxonomy differs')
        source_manifest_path = root/'chips'/source['dataset']/'chips.parquet'
        target_manifest_path = root/'chips'/dest['dataset']/'chips.parquet'
        original = pd.read_parquet(source_manifest_path)
        target = pd.read_parquet(target_manifest_path)
        target = target[target.split.eq(split)]
        coverage = read(new/'inference-coverage.json')
        if not coverage['complete'] or {r['chip_id'] for r in coverage['chips']} != set(target.chip_id):
            raise ValueError('Prediction coverage differs')
        source_coverage = read(old/'inference-coverage.json')
        if not source_coverage['complete'] or not set(target.chip_id) <= {r['chip_id'] for r in source_coverage['chips']}:
            raise ValueError('Source reference coverage missing')
        # Check spatial membership but do not use the previously curated labels.
        select_labels(source, dest, original, target, pd.read_parquet(new/'ground-truth.parquet'))
        inventory_path = root/'run-inputs'/run/city/'inventory.parquet'
        cutoff = min(record['source_dates'])[:10]
        with rasterio.open(cfg['imagery']['local_raster']) as model_grid:
            labels = inventory_labels(pd.read_parquet(inventory_path),target,model_grid,cfg,read(new/'taxonomy.json'),cutoff)
        output = directory(new)
        output.mkdir(parents=True, exist_ok=False)
        labels.to_parquet(output/'ground-truth.parquet', index=False)
        receipt = dict(policy='city_inventory_reference_v1', training_eligible=False,
            acquisition_id=acquisition, source_kind='city_inventory', inventory_sha256=sha(inventory_path),
            planting_cutoff=cutoff,
            destination_cohort=new.name, tiles=len(target), reference_trees=len(labels),
            reference_sha256=sha(output/'ground-truth.parquet'), predictions_sha256=sha(new/'predictions.parquet'),
            taxonomy_sha256=sha(new/'taxonomy.json'), source_taxonomy_sha256=sha(old/'taxonomy.json'),
            source_manifest_sha256=sha(source_manifest_path),
            destination_manifest_sha256=sha(target_manifest_path), geographic_grid_verified=True,
            note='City inventory baseline. Prior-year curation is applied only through explicit peer import; no completion or negative masks inherited.')
        (output/'reference.json').write_text(json.dumps(receipt, indent=2)+'\n', encoding='utf-8')
        assert reference(new) is not None
        results.append(receipt)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--acquisitions', nargs='+', required=True)
    args = parser.parse_args()
    for acquisition in args.acquisitions:
        for result in prepare(Path(os.environ['TREE_ML_DATA_ROOT']), args.run, acquisition):
            print(json.dumps(result), flush=True)

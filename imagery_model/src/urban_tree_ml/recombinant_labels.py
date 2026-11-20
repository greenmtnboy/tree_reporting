"""Reference-based recombinant annotation plumbing. Read-only dry runs, never training.

Bindings contain paths and image identity, not annotation snapshots. Resolution
uses current published feedback; results are disposable diagnostics, not targets.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock

import numpy as np
from pyproj import Transformer
from rasterio.transform import Affine

from urban_tree_ml.catalog_imagery import matches_catalog_imagery
from urban_tree_ml import recombinant as r
from urban_tree_ml.boundary_shifts import source_members
from urban_tree_ml.chips import build_chips

_PREVIEW_LOCK = Lock()


def artifact_root(scope):
    return (scope.root if isinstance(scope, r.LibraryScope) else Path(scope)).resolve()


def relative(root, path):
    return Path(path).resolve().relative_to(root).as_posix()


def resolve_path(root, name):
    from urban_tree_ml.prepared_finetune import resolve
    path = resolve(root, name).resolve()
    path.relative_to(root)
    return path


def make_reference(scope, review_dir, raster, inventory):
    """Explicitly bind a photo to a curation workspace, with no copied labels."""
    root, src = artifact_root(scope), r.source(scope)
    review_dir, raster, inventory = map(Path, (review_dir, raster, inventory))
    if not inventory.is_file():
        raise ValueError('Curation inventory is missing')
    manifest = json.loads((review_dir/'manifest.json').read_text())
    meta = manifest['metadata']
    if not matches_catalog_imagery(root, meta['source_raster'], raster):
        raise ValueError('Curation workspace raster identity mismatch')
    if Path(src['raster']).resolve() not in source_members(raster):
        raise ValueError('Photo is not a member of the curation raster')
    if not meta.get('curation_imagery_id'):
        raise ValueError('Curation workspace needs a stable imagery identity')
    return dict(schema_version=1, city=src['city'], source_id=src['source_id'],
                source_sha256=src['source_sha256'],
                curation_imagery_id=meta['curation_imagery_id'],
                review_dir=relative(root, review_dir), raster=relative(root, raster),
                inventory=relative(root, inventory),
                coordinate_convention='canonical-raster-inverse-affine-v1')


def bind_reference(scope, reference):
    """Optional explicit migration: one shared binding covers legacy cuts too."""
    root = artifact_root(scope)
    checked = make_reference(scope, *(resolve_path(root, reference[k])
                                     for k in ('review_dir', 'raster', 'inventory')))
    if checked != reference:
        raise ValueError('Invalid curation reference')
    path = r.workspace(scope)/'curation-reference.json'
    if path.exists():
        if json.loads(path.read_text()) != reference:
            raise ValueError('Existing curation binding differs; explicit migration required')
        return
    with path.open('x', encoding='utf-8') as stream:
        json.dump(reference, stream, indent=2)


def load_reference(scope):
    path = r.workspace(scope)/'curation-reference.json'
    if not path.exists():
        raise ValueError('Photo library has no curation reference; bind its workspace first')
    reference = json.loads(path.read_text())
    root = artifact_root(scope)
    if reference != make_reference(scope, *(resolve_path(root, reference[k])
                                           for k in ('review_dir', 'raster', 'inventory'))):
        raise ValueError('Curation reference identity changed')
    return reference


def resolve_published(scope, config, *, capture_inputs=False, reference=None, feedback_override=None):
    """Reuse chip-builder registration/species/deletion logic without building chips."""
    ref, root = reference or load_reference(scope), artifact_root(scope)
    if config.inventory.city.lower() != ref['city']:
        raise ValueError('Config city differs from curation reference')
    review = resolve_path(root, ref['review_dir'])
    feedback_path = review/'training-feedback.json'
    inventory = resolve_path(root, ref['inventory'])
    from urban_tree_ml.boundary_shifts import digest
    before = digest(inventory)
    from urban_tree_ml.config import taxonomy_path
    taxonomy = taxonomy_path(config)
    taxonomy_hash = digest(taxonomy) if taxonomy.exists() else None
    feedback_bytes = feedback_path.read_bytes() if feedback_override is None else feedback_override
    published = json.loads(feedback_bytes)
    cfg = config.model_copy(deep=True)
    cfg.dataset = published['dataset']
    cfg.imagery.boundary_shifts = False
    # Temporary feedback copy prevents mixed revisions during resolution. It is
    # not retained in a cut, nor used to create a training dataset.
    with TemporaryDirectory(prefix='recombinant-label-check-') as temporary:
        snapshot = Path(temporary)/'training-feedback.json'
        snapshot.write_bytes(feedback_bytes)
        value = build_chips(cfg, resolve_path(root, ref['raster']),
                            feedback_path=snapshot, use_default_feedback=False,
                            inventory_source=inventory, allowed_splits={'train'},
                            source_supervision_only=True)
    if (before != digest(inventory) or (feedback_override is None and feedback_bytes != feedback_path.read_bytes())
        or taxonomy_hash != (digest(taxonomy) if taxonomy.exists() else None)):
        raise ValueError('Published inputs changed while resolving; retry')
    value['reference'] = ref
    value['revision'] = dict(inventory_sha256=before,
                            feedback_sha256=hashlib.sha256(feedback_bytes).hexdigest(),
                            source_reviews_sha256=published.get('source_reviews_sha256'),
                            taxonomy_sha256=taxonomy_hash,
                            config_sha256=r.digest(cfg.model_dump(mode='json')))
    if capture_inputs:
        inputs = {'inventory.parquet': inventory.read_bytes(),
                  'training-feedback.json': feedback_bytes,
                  'taxonomy.json': taxonomy.read_bytes()}
        if (hashlib.sha256(inputs['inventory.parquet']).hexdigest() != before
            or hashlib.sha256(inputs['taxonomy.json']).hexdigest() != taxonomy_hash):
            raise ValueError('Inputs changed while capturing export')
        value['_inputs'] = inputs
    return value


def transform_xy(x, y, source_transform, source_crs, photo):
    """Preserve continuous pixel coordinates; do not add a second half-pixel offset."""
    wx, wy = Affine(*source_transform[:6]) * (x, y)
    if source_crs != photo['crs']:
        wx, wy = Transformer.from_crs(source_crs, photo['crs'], always_xy=True).transform(wx, wy)
    return ~Affine(*photo['transform'][:6]) * (wx, wy)


def transfer_centers(recipe, tiles, masks, resolved, photo):
    """Map visible centers for diagnostics only; no implicit negative supervision.

    Regions are not yet materialized into synthetic loss masks. Every result
    remains training_ready=False, even when all visible centers can be mapped.
    """
    frame = resolved['frame']
    if frame.tree_id.astype(str).duplicated().any():
        raise ValueError('Duplicate canonical source tree identity')
    if not frame['split'].eq('train').all():
        raise ValueError('Only training source labels may be transferred')
    xs, ys = transform_xy(frame.pixel_col.to_numpy(), frame.pixel_row.to_numpy(),
                          resolved['transform'], resolved['crs'], photo)
    records, warnings, tile_points = [], [], {}
    for index, placement in enumerate(recipe['placements']):
        fg = tiles[placement['tile_id']]
        w, h = r.dimensions(fg)
        mask = masks[fg['id']]
        if mask.shape != (h, w) or (placement['width'], placement['height']) != (w, h):
            raise ValueError('Placement/mask dimensions differ')
        for layer, tile, visible, dx, dy in r.placement_layers(placement, tiles, masks):
            identifier = tile['id']
            tw, th = r.dimensions(tile)
            if (tile['source_id'] != photo['source_id'] or
                tile['source_sha256'] != photo['source_sha256'] or
                visible.shape != (th, tw)):
                raise ValueError('Tile source or dimensions differ from recipe')
            if identifier not in tile_points:
                lx, ly = xs-tile['source_x'], ys-tile['source_y']
                inside = np.isfinite(lx) & np.isfinite(ly) & (lx >= 0) & (ly >= 0) & (lx < tw) & (ly < th)
                tile_points[identifier]=[(pos,float(lx[pos]),float(ly[pos])) for pos in np.flatnonzero(inside)]
            for pos,x,y in tile_points[identifier]:
                if not visible[int(y), int(x)]:
                    continue
                row = frame.iloc[pos]
                if row.feedback_excluded or row.planted_after_imagery:
                    warnings.append(dict(placement=index, layer=layer, tree_id=str(row.tree_id),
                                         x=x+placement['x']+dx, y=y+placement['y']+dy,
                                         reason='excluded source center; requires supervision mask'))
                    continue
                radius = row.get('crown_radius_m', None)
                margin = float(radius)/.3 if radius is not None and np.isfinite(radius) and radius > 0 else None
                safe = False
                if margin is not None:
                    # Conservative enclosing box + full blend margin around the crown.
                    margin += recipe.get('blending', {}).get('width_px', 0)
                    x0, y0 = int(np.floor(x-margin)), int(np.floor(y-margin))
                    x1, y1 = int(np.ceil(x+margin))+1, int(np.ceil(y+margin))+1
                    safe = x0 >= 0 and y0 >= 0 and x1 <= tw and y1 <= th and visible[y0:y1,x0:x1].all()
                record = dict(instance_id=f'{recipe["id"]}:{index}:{layer}:{row.tree_id}',
                              source_tree_id=str(row.tree_id), placement=index, layer=layer,
                              tile_id=identifier, x=x+placement['x']+dx, y=y+placement['y']+dy,
                              species_id=int(row.species_id) if row.species_eligible else None,
                              genus_id=int(row.genus_id) if row.genus_eligible else None,
                              vegetation_type=str(row.get('vegetation_type', '') or ''),
                              crown_radius_m=float(radius) if margin is not None else None,
                              boundary_check='contained' if safe else 'needs-review')
                records.append(record)
    return dict(schema_version=1, generated_id=recipe['id'], trees=records,
                warnings=warnings, revision=resolved.get('revision', {}),
                source_region_count=len(resolved.get('regions', [])),
                training_ready=False, diagnostic_only=True,
                blockers=['Loss/ignore/group masks and reviewed coverage are not exported',
                          'Training admission and seam approval require a separate export'])


def dry_run(scope, generated_id, config, *, resolved=None):
    """Read a saved recipe and verify assets without changing the library or curation."""
    src = r.source(scope)
    recipe = next((v for v in r.state(scope).get('generated', []) if v['id'] == generated_id), None)
    if recipe is None:
        raise ValueError('Unknown generated chip')
    if recipe['source_id'] != src['source_id'] or recipe['source_sha256'] != src['source_sha256']:
        raise ValueError('Generated recipe source identity mismatch')
    tiles = recipe['tiles']
    masks = {}
    for identifier, tile in tiles.items():
        if identifier != tile['id']:
            raise ValueError('Tile identity mismatch')
        # Validates the original guarded TRAIN viewport without raster pixel IO.
        _, entry, _ = r.checked_window(scope, tile['chip'])
        if (tile['source_x'], tile['source_y']) != (entry['x']+tile['x'], entry['y']+tile['y']):
            raise ValueError('Source footprint differs from saved viewport')
        if tile['crs'] != src['crs'] or tile['source_transform'][:6] != src['transform'][:6]:
            raise ValueError('Source georeferencing differs')
        _, masks[identifier] = r.load_tile(scope, tile)
    with np.load(r.workspace(scope)/'generated'/(generated_id+'.npz'), allow_pickle=False) as values:
        if hashlib.sha256(values['image'].tobytes()).hexdigest() != recipe['pixel_sha256']:
            raise ValueError('Generated pixel hash mismatch')
    return transfer_centers(recipe, tiles, masks, resolved if resolved is not None else resolve_published(scope, config), src)


def preview_saved(scope, generated_id, context):
    if not _PREVIEW_LOCK.acquire(blocking=False):
        raise ValueError('Another annotation preview is loading; retry shortly')
    try:
        return _preview_saved(scope,generated_id,context)
    finally:
        _PREVIEW_LOCK.release()


def _preview_saved(scope, generated_id, context):
    """Resolve latest saved curation without publishing, binding or changing live files."""
    from urban_tree_ml.config import taxonomy_path, normalization_path, ReferenceConfig
    from urban_tree_ml.feedback import finalize_registration_feedback
    from urban_tree_ml.vegetation_groups import expanded_taxonomy, GROUP_CLASSES
    inventory=context.config.paths.root/'inventory'/context.config.inventory.city.lower()/'inventory.parquet'
    reference=make_reference(scope,context.directory,context.raster,inventory)
    paths=[context.directory/'manifest.json',context.directory/'reviews.json']
    captured=[p.read_bytes() for p in paths]
    taxonomy_raw=taxonomy_path(context.config).read_bytes()
    taxonomy=json.loads(taxonomy_raw)
    with TemporaryDirectory(prefix='recombinant-preview-') as folder:
        directory=Path(folder)
        # Preview categories are display-only. Preserve every inventory class ID;
        # never migrate a saved taxonomy, model head or training config here.
        taxonomy=expanded_taxonomy(taxonomy)
        vocabulary=directory/'taxonomy.json'; vocabulary.write_text(json.dumps(taxonomy))
        cfg=context.config.model_copy(deep=True)
        cfg.reference=ReferenceConfig(taxonomy_path=vocabulary,normalization_path=normalization_path(context.config))
        for p,raw in zip(paths,captured):(directory/p.name).write_bytes(raw)
        finalize_registration_feedback(cfg,context.raster,review_dir=directory,snapshot_annotations=False)
        resolved=resolve_published(scope,cfg,reference=reference,
            feedback_override=(directory/'training-feedback.json').read_bytes())
    if any(p.read_bytes()!=raw for p,raw in zip(paths,captured)):
        raise ValueError('Saved annotations changed while loading; refresh the annotation overlay')
    result=dry_run(scope,generated_id,context.config,resolved=resolved)
    if taxonomy_path(context.config).read_bytes()!=taxonomy_raw:
        raise ValueError('Source taxonomy changed while loading; refresh annotations')
    for tree in result['trees']:
        i=tree['species_id']; names=taxonomy.get('species',[])
        tree['species']=names[i] if i is not None and 0<=i<len(names) else 'Unknown'
        tree['species']={GROUP_CLASSES['copse']:'Copse',GROUP_CLASSES['shrub-mass']:'Shrub mass'}.get(tree['species'],tree['species'])
    result['annotation_source']='latest-saved-curation'
    result['saved_reviews_sha256']=hashlib.sha256(captured[1]).hexdigest()
    return result

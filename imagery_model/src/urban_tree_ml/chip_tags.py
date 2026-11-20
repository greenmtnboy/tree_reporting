"""Model-independent, acquisition-scoped chip tags. Never training labels."""
import hashlib
import json
import re
from functools import lru_cache

from urban_tree_ml.chip_geometry import chip_offsets
from urban_tree_ml.feedback import ReviewStateConflictError, _write_json_atomic

DEFAULT_TAGS = ['setting:parkland', 'setting:residential', 'setting:urban-streets',
                'setting:industrial', 'setting:woodland', 'vegetation:isolated',
                'vegetation:street-rows', 'vegetation:dense-canopy',
                'vegetation:overlapping-crowns', 'vegetation:shrub-heavy',
                'image:heavy-shadow', 'image:dark', 'image:low-contrast']


def tag_path(context):
    # The workspace is the acquisition identity, never a training run/dataset.
    return context.config.paths.annotations / context.city / context.directory.name / 'chip-tags.json'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@lru_cache(maxsize=32)
def _geometry(path, modified, size):
    import rasterio
    with rasterio.open(path) as source:
        return source.transform, source.crs.to_string()


def chip_key(bundle, chip):
    """Exact physical footprint; regular/resampled aliases share a key.

    The containing tag file scopes it to city/acquisition. Shifted footprints
    remain distinct; they do not become additional base coverage.
    """
    stat = bundle.raster_path.stat()
    transform, crs = _geometry(str(bundle.raster_path), stat.st_mtime_ns, stat.st_size)
    pixels = bundle.config.imagery.chip_pixels
    row, col = chip_offsets(chip, pixels)
    corners = [transform * point for point in ((col,row),(col+pixels,row),
                                               (col+pixels,row+pixels),(col,row+pixels))]
    return digest([crs, [[round(x, 5), round(y, 5)] for x, y in corners]])


def load_tags(context):
    path = tag_path(context)
    if not path.exists():
        return {'schema_version': 1, 'chips': {}}
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not isinstance(data.get('chips'), dict):
        raise ValueError('Unsupported chip-tag file')
    return data


def chip_tags(context, bundle, chip):
    if chip not in bundle.chip_ids or bundle.metrics.get('split') == 'test':
        raise ValueError('Unknown or sealed chip')
    data = load_tags(context)
    key = chip_key(bundle, chip)
    tags = data['chips'].get(key, {}).get('tags', [])
    vocabulary = set(DEFAULT_TAGS)
    for entry in data['chips'].values():
        vocabulary.update(entry['tags'])
    return dict(key=key, tags=tags, revision=digest(tags), categories=sorted(vocabulary))


def save_tags(context, bundle, chip, payload):
    """Caller holds the save lock; compare-and-swap only this chip.

    Independent atomic sidecar reads let backups capture a consistent old or new
    tag revision without blocking this save on the longer snapshot operation.
    """
    current = chip_tags(context, bundle, chip)
    if payload.get('revision') != current['revision']:
        raise ReviewStateConflictError('Chip tags changed in another tab; reload tags before saving')
    tags = payload.get('tags')
    if not isinstance(tags, list) or len(tags) > 30:
        raise ValueError('Provide at most 30 tags')
    normalized = []
    for tag in tags:
        if not isinstance(tag, str) or not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9 :_/-]{0,63}', tag):
            raise ValueError('Tags must be 1–64 letters, numbers, spaces, :, /, _ or -')
        normalized.append(tag.strip().lower())
    data = load_tags(context)
    data['chips'][current['key']] = {'tags': sorted(set(normalized))}
    _write_json_atomic(tag_path(context), data)
    return chip_tags(context, bundle, chip)


def tag_digest(context):
    path = tag_path(context)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def expected_genus(taxonomy, species_id):
    """Use the checkpoint taxonomy mapping, not a guessed string prefix."""
    import math
    if species_id is None or not math.isfinite(species_id) or int(species_id) != species_id:
        return None
    species_id = int(species_id)
    species = taxonomy['species']
    if not 0 <= species_id < len(species) or species[species_id].startswith('__vegetation_'):
        return None
    mapping = taxonomy.get('species_to_genus')
    if mapping is not None:
        gid = mapping[species_id]
        return taxonomy['genera'][gid] if 0 <= gid < len(taxonomy['genera']) else None
    # Legacy taxonomies lack the explicit mapping. Only known genera qualify.
    name = species[species_id].split(' ')[0]
    return name if ' ' in species[species_id] and name in taxonomy['genera'] else None


def disagreement(taxonomy, species_id, genus_id):
    import math
    expected = expected_genus(taxonomy, species_id)
    if expected is None or genus_id is None or not math.isfinite(genus_id) or int(genus_id) != genus_id or not 0 <= genus_id < len(taxonomy['genera']):
        return None
    return taxonomy['genera'][int(genus_id)] != expected

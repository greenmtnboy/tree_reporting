"""Manual, acquisition-scoped component drafts. Never admitted to training.

Only an offline allowlisted source manifest can select raster paths/windows.
The web API accepts component geometry, not paths, splits or training labels.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import random
import re
from pathlib import Path
from dataclasses import dataclass
from threading import RLock
from uuid import uuid4

import numpy as np
import rasterio
from PIL import Image
from rasterio.features import geometry_mask
from rasterio.transform import Affine
from rasterio.windows import Window

from urban_tree_ml.feedback import ReviewStateConflictError, _write_json_atomic

LOCK = RLock()
SIZES = (64, 128, 256, 512)
SIDES = ('top', 'right', 'bottom', 'left')
SURFACES = ('unknown', 'grass', 'soil', 'pavement', 'water', 'low-vegetation', 'blocked')


@dataclass(frozen=True)
class LibraryScope:
    root: Path
    folder: Path


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def workspace(root):
    if isinstance(root, LibraryScope):
        return root.folder
    return Path(root) / 'recombinant' / 'boston-manual-v1'


def libraries(root):
    base = root.root if isinstance(root, LibraryScope) else Path(root)
    folders = [workspace(base)] + sorted((base/'recombinant'/'sources').glob('*'))
    result = []
    for folder in folders:
        if not (folder/'source.json').is_file() or folder.is_symlink():
            continue
        value = source(LibraryScope(base, folder))
        result.append(dict(source_id=value['source_id'], acquisition=value['acquisition'], folder=folder))
    return result


def scoped(root, source_id=None):
    if source_id is None:
        return root
    match = next((v for v in libraries(root) if v['source_id'] == source_id), None)
    if match is None:
        raise ValueError('Unknown source photograph')
    return LibraryScope(Path(root), match['folder'])


def source(root):
    value = json.loads((workspace(root) / 'source.json').read_text())
    if value.get('schema_version') != 1 or value.get('city') != 'usbos' or value.get('training_ready') is not False:
        raise ValueError('Unsupported recombinant source')
    return value


def state(root):
    path = workspace(root) / 'library.json'
    data = json.loads(path.read_text()) if path.exists() else dict(schema_version=1, components=[], layouts=[])
    return data | {'revision': digest(data)}


def catalog(root):
    src = source(root)
    # No disk paths exposed/accepted by the browser.
    return {k: v for k, v in src.items() if k not in {'raster', 'raster_stat'}} | state(root) | {
        'component_sizes': list(SIZES), 'dimension_step': 16, 'edge_surfaces': list(SURFACES),
        'sources': [{k:v for k,v in entry.items() if k != 'folder'} for entry in libraries(root)]}


def edge_rules(value=None):
    """Conservative whole-edge contracts, explicitly curated rather than inferred."""
    if value is None:
        value = {side: dict(surface='unknown') for side in SIDES}
    if not isinstance(value, dict) or set(value) != set(SIDES):
        raise ValueError('Mark all four tile edges')
    result = {}
    for side in SIDES:
        edge = value[side]
        if not isinstance(edge, dict) or edge.get('surface') not in SURFACES:
            raise ValueError('Invalid edge material')
        result[side] = dict(surface=edge['surface'])
    return result


def compatible(a, a_side, b, b_side):
    if a['source_id'] != b['source_id'] or a['source_sha256'] != b['source_sha256']:
        return False
    left, right = edge_rules(a.get('edges'))[a_side], edge_rules(b.get('edges'))[b_side]
    return left == right and left['surface'] not in ('unknown', 'blocked')


def is_background(tile):
    return 'background' in {v.strip().lower() for v in re.split(r'[,;]', tile.get('tags', ''))}


def dimensions(tile):
    if 'width' in tile or 'height' in tile:
        w, h = tile.get('width'), tile.get('height')
        if any(type(v) is not int or v < 16 or v > 512 or v % 16 for v in (w,h)):
            raise ValueError('Width and height must be 16–512 pixels in 16px increments')
        if tile.get('size') is not None and (tile['size'] != w or w != h):
            raise ValueError('Conflicting square and rectangle dimensions')
        return w, h
    size = tile.get('size')
    if type(size) is not int or size not in SIZES:
        raise ValueError('Choose a supported legacy square or explicit 16px rectangle dimensions')
    return size, size


def load_tile(root, tile):
    w, h = dimensions(tile)
    with np.load(workspace(root)/'components'/(tile['id']+'.npz'), allow_pickle=False) as stored:
        pixels, mask = stored['image'], stored['keep_mask']
    expected = geometry(tile)[-1]
    if (pixels.shape != (4,h,w) or pixels.dtype != np.uint8 or mask.shape != (h,w)
            or not np.array_equal(mask, expected)
            or hashlib.sha256(pixels.tobytes()).hexdigest() != tile['pixel_sha256']):
        raise ValueError('Stored tile pixels/mask failed verification; generation stopped')
    return pixels, mask


def composite_edges(tile, background, mask):
    foreground = edge_rules(tile.get('edges'))
    backing = edge_rules(background.get('edges'))
    strips = (mask[0,:], mask[:,-1], mask[-1,:], mask[:,0])
    result = {}
    for side, strip in zip(SIDES, strips):
        if strip.all():
            result[side] = foreground[side]
        elif not strip.any():
            result[side] = backing[side]
        else:
            result[side] = foreground[side] if foreground[side] == backing[side] else dict(surface='blocked')
    return result


def background_fits(tile, background):
    """Repeat whole backing rectangles, with compatible internal material seams."""
    w, h = dimensions(tile); bw, bh = dimensions(background)
    return (w % bw == 0 and h % bh == 0
            and (w == bw or compatible(background, 'right', background, 'left'))
            and (h == bh or compatible(background, 'bottom', background, 'top')))


def background_parts(placement, tiles):
    """Return verified local source placements; old same-size recipes stay valid."""
    if placement.get('background_layout') == 'mixed-rect-v2':
        parts = placement.get('background_placements', [])
        fg = tiles[placement['tile_id']]; w,h = dimensions(fg)
        owner = np.full((h//16,w//16),-1,dtype=int)
        checked = []
        for i,p in enumerate(parts):
            bg = tiles[p['tile_id']]
            x,y,bw,bh = (p[k] for k in ('x','y','width','height'))
            if (any(type(v) is not int or v%16 for v in (x,y,bw,bh)) or min(x,y)<0
                or min(bw,bh)<=0 or x+bw>w or y+bh>h or dimensions(bg)!=(bw,bh)
                or not is_background(bg) or bg['source_id']!=fg['source_id']
                or bg['source_sha256']!=fg['source_sha256']):
                raise ValueError('Invalid mixed background source placement')
            area=owner[y//16:(y+bh)//16,x//16:(x+bw)//16]
            if (area>=0).any():raise ValueError('Overlapping mixed background placements')
            area[:]=i; checked.append(bg)
        if not parts or (owner<0).any():raise ValueError('Mixed background layout has gaps')
        for a,b,sa,sb in [(owner[:,:-1],owner[:,1:],'right','left'),(owner[:-1,:],owner[1:,:],'bottom','top')]:
            for i,j in np.unique(np.stack([a[a!=b],b[a!=b]],axis=1),axis=0):
                if not compatible(checked[i],sa,checked[j],sb):
                    raise ValueError('Incompatible mixed background seam')
        return parts
    identifier = placement.get('background_id')
    if not identifier:
        if placement.get('background_placements'):
            raise ValueError('Background placements require a background tile')
        return []
    fg, bg = tiles[placement['tile_id']], tiles[identifier]
    w, h = dimensions(fg); bw, bh = dimensions(bg)
    if not background_fits(fg, bg):
        raise ValueError('Background must exactly tile the cut with compatible internal edges')
    expected = [dict(tile_id=identifier, x=x, y=y, width=bw, height=bh)
                for y in range(0,h,bh) for x in range(0,w,bw)]
    saved = placement.get('background_placements')
    if (saved is None and len(expected) != 1) or (saved is not None and saved != expected):
        raise ValueError('Background source placements differ from frozen recipe')
    return expected


def backing_pixels(placement, tiles, loaded, blend_width):
    parts = background_parts(placement, tiles)
    w, h = dimensions(tiles[placement['tile_id']])
    backing = np.zeros((4,h,w),dtype=np.uint8)
    for part in parts:
        pixels, mask = loaded(part['tile_id'])
        if not mask.all():
            raise ValueError('Background must keep its entire rectangle')
        x,y,bw,bh = (part[k] for k in ('x','y','width','height'))
        backing[:,y:y+bh,x:x+bw] = pixels
    if len(parts)>1:
        if placement.get('background_layout') == 'mixed-rect-v2':
            return blend_rectangles(backing, parts, blend_width)
        return blend_grid(backing, dimensions(tiles[placement['background_id']]), blend_width)
    return backing


def placement_layers(placement, tiles, masks):
    """Each repeated source gets its own visibility mask and destination offset."""
    fg = tiles[placement['tile_id']]; mask = masks[fg['id']]
    yield 'foreground', fg, mask, 0, 0
    parts = background_parts(placement, tiles)
    if not parts and not mask.all():
        raise ValueError('Irregular foreground has no background')
    for i,part in enumerate(parts):
        tile = tiles[part['tile_id']]
        if not masks[tile['id']].all():
            raise ValueError('Background must keep its entire rectangle')
        x,y,w,h = (part[k] for k in ('x','y','width','height'))
        yield ('background' if len(parts)==1 else f'background:{i}'), tile, ~mask[y:y+h,x:x+w], x, y


def feather_alpha(mask, width):
    """Inward, pixel-grid distance feather; outside the cut is always background."""
    if not width:
        return mask.astype(np.float32)
    inside = mask.astype(bool).copy()
    distance = np.zeros(mask.shape, dtype=np.float32)
    for _ in range(width+1):
        distance += inside
        padded = np.pad(inside, 1, constant_values=False)
        inside = np.logical_and.reduce([padded[y:y+mask.shape[0],x:x+mask.shape[1]]
                                        for y in range(3) for x in range(3)])
    t = np.clip((distance-.5)/width, 0, 1)
    return t*t*(3-2*t)


def blend_grid(image, size, width):
    """Symmetric cross-seam feather. Only narrow seam bands change, no wraparound.

    Vertical then horizontal is intentional and versioned, including intersections.
    Matching weights across all four bands avoid an RGB-only processing mismatch.
    """
    result = image.astype(np.float32).copy()
    if not width:
        return image.copy()
    for axis in (2, 1):
        step = size if isinstance(size, int) else size[0 if axis==2 else 1]
        for seam in range(step, image.shape[axis], step):
            for d in range(width):
                t = 1-(d+.5)/width
                weight = .5*t*t*(3-2*t)
                left = [slice(None)]*3; right = [slice(None)]*3
                left[axis] = seam-1-d; right[axis] = seam+d
                a, b = result[tuple(left)].copy(), result[tuple(right)].copy()
                result[tuple(left)] = a*(1-weight)+b*weight
                result[tuple(right)] = b*(1-weight)+a*weight
    return np.clip(np.rint(result), 0, 255).astype(np.uint8)


def pack_rectangles(candidates, seed, width=512, height=512):
    """Bounded exact cover on a 16px grid, no rotation, crop, gaps or overlaps.

    Check every touching neighbor, including partial edges at T junctions.
    Larger footprints are favored, with seeded weighted random choice; background
    alternatives do not multiply a foreground's placement weight.
    """
    candidates=sorted(candidates,key=lambda v:v['id'])
    if not candidates or width % math.gcd(*(v['width'] for v in candidates)) or height % math.gcd(*(v['height'] for v in candidates)):
        raise ValueError('No compatible packing: dimensions cannot fill rectangle. Add smaller matching tiles, e.g. 16px background fillers.')
    cols,rows=width//16,height//16
    occupied = np.full((rows,cols), -1, dtype=np.int32)
    placed, choices = [], {}
    rng = random.Random(seed)
    reverse_x,reverse_y,column_first=[bool(rng.getrandbits(1)) for _ in range(3)]
    checks = attempts = 0

    def fits(v, x, y):
        w,h = v['width']//16,v['height']//16
        if min(x,y)<0 or x+w>cols or y+h>rows or (occupied[y:y+h,x:x+w]>=0).any():
            return False
        neighbors = []
        if x: neighbors.append(('left','right',occupied[y:y+h,x-1]))
        if y: neighbors.append(('top','bottom',occupied[y-1,x:x+w]))
        if x+w<cols: neighbors.append(('right','left',occupied[y:y+h,x+w]))
        if y+h<rows: neighbors.append(('bottom','top',occupied[y+h,x:x+w]))
        return all(compatible(v,a,placed[int(index)],b)
                   for a,b,indices in neighbors for index in np.unique(indices) if index>=0)

    while (occupied<0).any():
        depth = len(placed)
        if depth not in choices:
            view=occupied[::-1 if reverse_y else 1, ::-1 if reverse_x else 1]
            a,b=map(int,np.argwhere((view.T if column_first else view)<0)[0])
            yy,xx=(b,a) if column_first else (a,b)
            ax,ay=(cols-1-xx if reverse_x else xx),(rows-1-yy if reverse_y else yy)
            checks += len(candidates)
            if checks>500000:
                raise ValueError('Packing search limit reached. Add smaller matching background fillers or try another seed; no rules were relaxed.')
            groups = {}
            for v in candidates:
                x=ax-v['width']//16+1 if reverse_x else ax
                y=ay-v['height']//16+1 if reverse_y else ay
                if fits(v,x,y): groups.setdefault(v['tile_id'],[]).append(v|dict(x=x*16,y=y*16))
            counts={}
            for p in placed:counts[p['tile_id']]=counts.get(p['tile_id'],0)+1
            ranked = [(-math.log(max(rng.random(),1e-12))*(1+counts.get(group[0]['tile_id'],0))/math.sqrt(group[0]['width']*group[0]['height']),group)
                      for group in groups.values()]
            ranked.sort(key=lambda item:item[0],reverse=True)
            options = []
            for _,group in ranked:
                rng.shuffle(group);options.extend(group)
            choices[depth] = options
        options = choices[depth]
        if not options:
            del choices[depth]
            if not placed:
                raise ValueError('No compatible packing found. Add smaller matching background fillers; no rules were relaxed.')
            previous = placed.pop()
            px,py = previous['x']//16,previous['y']//16
            occupied[py:py+previous['height']//16,px:px+previous['width']//16] = -1
            continue
        attempts += 1
        if attempts>25000:
            raise ValueError('Packing search limit reached. Try another seed or add smaller matching tiles; no rules were relaxed.')
        v = options.pop()
        x,y=v['x']//16,v['y']//16
        occupied[y:y+v['height']//16,x:x+v['width']//16] = depth
        placed.append(v)
    return placed


def blend_rectangles(image, placements, width):
    """Feather real rectangle borders only; do not paint a fictitious square grid."""
    if not width:
        return image.copy()
    owner = np.full(image.shape[1:], -1, dtype=np.int32)
    for i,p in enumerate(placements):
        owner[p['y']:p['y']+p['height'],p['x']:p['x']+p['width']] = i
    result = image.astype(np.float32).copy()
    for axis in (2,1):
        plane = owner if axis==2 else owner.T
        view = result if axis==2 else result.transpose(0,2,1)
        for seam in range(16,plane.shape[1],16):
            left,right = plane[:,seam-1],plane[:,seam]
            boundary = (left!=right)&(left>=0)&(right>=0)
            if not boundary.any(): continue
            for d in range(width):
                valid = boundary&(plane[:,seam-1-d]==left)&(plane[:,seam+d]==right)
                t = 1-(d+.5)/width;weight=.5*t*t*(3-2*t)
                a,b=view[:,valid,seam-1-d].copy(),view[:,valid,seam+d].copy()
                view[:,valid,seam-1-d]=a*(1-weight)+b*weight
                view[:,valid,seam+d]=b*(1-weight)+a*weight
    return np.clip(np.rint(result),0,255).astype(np.uint8)


def packed_background_candidates(tile, backgrounds, mask, seed):
    """Nested exact-cover search, one level only: background inputs are whole tiles."""
    w,h=dimensions(tile)
    by_id={v['id']:v for v in backgrounds}
    choices=[dict(id=v['id'],tile_id=v['id'],source_id=v['source_id'],source_sha256=v['source_sha256'],
                  width=dimensions(v)[0],height=dimensions(v)[1],edges=edge_rules(v.get('edges')))
             for v in backgrounds if dimensions(v)[0]<=w and dimensions(v)[1]<=h]
    results=[]; seen=set()
    for variant in range(3):
        sub_seed=int(digest([seed,tile['id'],variant,'background-pack-v2'])[:16],16)
        try: packed=pack_rectangles(choices,sub_seed,w,h)
        except ValueError: continue
        parts=[{k:p[k] for k in ('tile_id','x','y','width','height')} for p in packed]
        signature=digest(parts)
        if signature in seen:continue
        seen.add(signature)
        boundaries={
            'top':[p for p in parts if p['y']==0], 'left':[p for p in parts if p['x']==0],
            'right':[p for p in parts if p['x']+p['width']==w],
            'bottom':[p for p in parts if p['y']+p['height']==h]}
        edges={}
        for side,edge_parts in boundaries.items():
            surfaces={by_id[p['tile_id']]['edges'][side]['surface'] for p in edge_parts}
            edges[side]=dict(surface=next(iter(surfaces)) if len(surfaces)==1 else 'blocked')
        results.append(dict(id=tile['id']+'+'+signature[:16],tile_id=tile['id'],
            background_id=parts[0]['tile_id'],background_layout='mixed-rect-v2',background_placements=parts,
            source_id=tile['source_id'],source_sha256=tile['source_sha256'],
            edges=composite_edges(tile,dict(edges=edges),mask)))
    if not results:
        raise ValueError(f'Irregular tile {tile.get("name",tile["id"])} ({w} x {h}px) needs packable whole rectangles tagged background from this photo. Add compatible smaller background tiles (16 x 16px can fill every grid gap). Search is bounded; try another seed if compatible pieces exist.')
    return results


def generate(root, current, payload):
    """Seeded tiling with optional same-photo background under irregular cuts."""
    size, seed = payload.get('size'), payload.get('seed')
    packing = payload.get('packing', 'uniform')
    if packing not in ('uniform', 'mixed-rect-v1'):
        raise ValueError('Unknown packing mode')
    mixed = packing == 'mixed-rect-v1'
    if (not mixed and (type(size) is not int or size not in SIZES)) or type(seed) is not int or not 0 <= seed <= 2147483647:
        raise ValueError('Choose a standard tile size and integer seed from 0 to 2147483647')
    width = payload.get('blend_width', 0)  # Legacy callers retain exact hard cuts.
    if type(width) is not int or width not in (0, 2, 4, 8):
        raise ValueError('Blend width must be 0, 2, 4 or 8 pixels')
    src = source(root)
    eligible = sorted((v for v in current['components'] if v.get('enabled', False) and v.get('borders_clear') is True
                       and v['source_id'] == src['source_id'] and v['source_sha256'] == src['source_sha256']),
                      key=lambda v: v['id'])
    if not eligible:
        raise ValueError('No enabled tiles of this size in this photograph. Save tiles in the sidebar first.')
    if len(eligible) > 500:
        raise ValueError('At most 500 eligible tiles are supported by this draft generator')
    # Verify every candidate, but do not retain hundreds of four-band arrays.
    masks = {v['id']: load_tile(root, v)[1] for v in eligible}
    backgrounds = [v for v in eligible if is_background(v) and masks[v['id']].all()]
    candidates = []
    for tile in eligible:
        if not mixed and dimensions(tile) != (size,size):
            continue
        mask = masks[tile['id']]
        if mask.all():
            candidates.append(dict(id=tile['id'], tile_id=tile['id'], background_id=None,
                                   source_id=tile['source_id'], source_sha256=tile['source_sha256'],
                                   edges=edge_rules(tile.get('edges'))))
        else:
            candidates.extend(packed_background_candidates(tile,backgrounds,mask,seed))
        if len(candidates) > 2000:
            raise ValueError('Too many tile/background pairings for this draft generator; use a smaller library.')
    if not candidates:
        raise ValueError('No enabled tiles of this size in this photograph. Save tiles in the sidebar first.')
    by_id = {v['id']: v for v in eligible}
    for candidate in candidates:
        candidate['width'], candidate['height'] = dimensions(by_id[candidate['tile_id']])
    if mixed:
        placed = pack_rectangles(candidates, seed)
    else:
        n = 512//size
        rng = random.Random(seed)
        placed, choices, attempts, checks = [], {}, 0, 0
        while len(placed) < n*n:
            i = len(placed)
            if i not in choices:
                checks += len(candidates)
                if checks > 200000:
                    raise ValueError('Compatibility search limit reached. Try another seed or add matching tiles; no rules were relaxed.')
                fitting = [v for v in candidates
                           if (i % n == 0 or compatible(placed[-1], 'right', v, 'left'))
                           and (i < n or compatible(placed[i-n], 'bottom', v, 'top'))]
                # Pick foregrounds evenly first; extra backgrounds must not multiply a
                # foreground's weight. Background choice is independently seeded.
                grouped = {}
                for v in fitting:
                    grouped.setdefault(v['tile_id'], []).append(v)
                groups = list(grouped.values())
                rng.shuffle(groups)
                for group in groups:
                    rng.shuffle(group)
                choices[i] = [v for group in groups for v in group]
            if not choices[i]:
                del choices[i]
                if not placed:
                    raise ValueError('No compatible arrangement found. Add or adjust tiles/edge markings; no rules were relaxed.')
                placed.pop()
                continue
            attempts += 1
            if attempts > 20000:
                raise ValueError('Compatibility search limit reached. Try another seed or curate more matching edges; no rules were relaxed.')
            placed.append(choices[i].pop())
        placed = [v | dict(x=i%n*size,y=i//n*size) for i,v in enumerate(placed)]
    from functools import lru_cache
    by_id = {v['id']: v for v in eligible}
    @lru_cache(maxsize=8)
    def loaded(identifier):
        return load_tile(root, by_id[identifier])
    data = np.zeros((4,512,512), dtype=np.uint8)
    unblended = np.zeros_like(data)
    for i, item in enumerate(placed):
        pixels, mask = loaded(item['tile_id'])
        raw = pixels
        if item['background_id']:
            backing = backing_pixels(item, by_id, loaded, width)
            raw = np.where(mask[None,:,:], pixels, backing_pixels(item, by_id, loaded, 0))
            alpha = feather_alpha(mask, width)[None,:,:]
            pixels = np.clip(np.rint(pixels*alpha+backing*(1-alpha)), 0, 255).astype(np.uint8)
        y, x, w, h = item['y'], item['x'], item['width'], item['height']
        data[:,y:y+h,x:x+w] = pixels
        unblended[:,y:y+h,x:x+w] = raw
    data = blend_rectangles(data, placed, width) if mixed else blend_grid(data, size, width)
    identifier = uuid4().hex
    folder = workspace(root)/'generated'
    folder.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(folder/(identifier+'.npz'), image=data, unblended_image=unblended,
                        valid_mask=np.ones((512,512),dtype=bool))
    (folder/(identifier+'.png')).write_bytes(png(data))
    (folder/(identifier+'-unblended.png')).write_bytes(png(unblended))
    used = {v['tile_id'] for v in placed} | {p['tile_id'] for v in placed for p in v.get('background_placements',[])}
    foreground_count = len({v['tile_id'] for v in placed})
    recipe = dict(id=identifier, source_id=src['source_id'], source_sha256=src['source_sha256'],
                  size=None if mixed else size, seed=seed, algorithm='rect-pack-v2' if mixed else 'edge-grid-v3-background',
                  packing=packing, grid_step=16 if mixed else size,
                  background_layout='mixed-rect-v2',
                  blending=dict(method=('rect-boundary-feather-v1' if mixed else 'boundary-feather-v1') if width else 'none', width_px=width,
                                width_m=width*.3, bands='RGB+NIR', order='backing-vertical,backing-horizontal,cutout-inward,vertical,horizontal'),
                  unblended_pixel_sha256=hashlib.sha256(unblended.tobytes()).hexdigest(),
                  tile_ids=[v['tile_id'] for v in placed], placements=placed,
                  tiles={v['id']: v for v in eligible if v['id'] in used},
                  candidates={v['id']: v for v in eligible},
                  input_revision=digest(current), pixel_sha256=hashlib.sha256(data.tobytes()).hexdigest(),
                  review_status='unreviewed', review_notes='', training_ready=False,
                  label_status='No tree labels. Seam review is not training approval.',
                  unique_tiles=foreground_count, repeated_slots=len(placed)-foreground_count,
                  background_overlays=sum(v['background_id'] is not None for v in placed), resolution_m=.3)
    current.setdefault('generated', []).append(json.loads(json.dumps(recipe)))


def checked_window(root, chip):
    src = source(root)
    entry = next((v for v in src['windows'] if v['id'] == chip), None)
    if entry is None and isinstance(chip, str):
        match = re.fullmatch(r'pan_(\d{1,8})_(\d{1,8})', chip)
        if match:
            x, y = map(int, match.groups())
            if x+512 > src['width'] or y+512 > src['height']:
                raise ValueError('Pan reaches the source photograph boundary; current view retained')
            # Authorize the full viewport BEFORE any raster read. Every pixel must
            # be covered by the union of already guarded TRAIN rectangles. This
            # permits crossing tile borders, never holes, held-out areas or guards.
            covered = np.zeros((512,512), dtype=bool)
            for allowed in src['windows']:
                if allowed.get('split') != 'train':
                    continue
                left, top = max(x,allowed['x']), max(y,allowed['y'])
                right = min(x+512,allowed['x']+allowed['size'])
                bottom = min(y+512,allowed['y']+allowed['size'])
                if right > left and bottom > top:
                    covered[top-y:bottom-y,left-x:right-x] = True
            if not covered.all():
                raise ValueError('Pan reaches unavailable training coverage or a held-out/guard area; current view retained')
            entry = dict(id=chip,x=x,y=y,size=512,split='train')
    if entry is None:
        raise ValueError('Unknown or held-out window')
    path = Path(src['raster'])
    stat = path.stat()
    if [stat.st_size, stat.st_mtime_ns] != src['raster_stat']:
        raise ValueError('Source imagery changed; re-verify the source before extracting')
    return src, entry, path


def pixels(root, chip):
    src, entry, path = checked_window(root, chip)
    with rasterio.open(path) as image:
        window = Window(entry['x'], entry['y'], 512, 512)
        data = image.read([1, 2, 3, 4], window=window)
        if data.shape != (4, 512, 512) or not (image.read_masks([1, 2, 3, 4], window=window) > 0).all():
            raise ValueError('Window contains missing imagery')
    return data


def png(data, mask=None):
    rgb = np.moveaxis(data[:3], 0, -1).astype('uint8')
    if mask is not None:
        rgb = np.dstack([rgb, mask.astype('uint8') * 255])
    stream = io.BytesIO()
    Image.fromarray(rgb).save(stream, format='PNG')
    return stream.getvalue()


def geometry(payload):
    """Integer native-pixel rectangle and a simple closed polygon inside it."""
    size, height = dimensions(payload)
    x, y = payload.get('x'), payload.get('y')
    if any(type(v) is not int for v in (x, y)):
        raise ValueError('Choose integer source coordinates')
    if x < 0 or y < 0 or x+size > 512 or y+height > 512:
        raise ValueError('Component rectangle must stay inside the source window')
    points = payload.get('polygon')
    if not isinstance(points, list) or not 3 <= len(points) <= 100:
        raise ValueError('Draw a closed cut outline with 3–100 vertices')
    for point in points:
        if (not isinstance(point, list) or len(point) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in point)
                or not x <= point[0] <= x+size or not y <= point[1] <= y+height):
            raise ValueError('Every cut point must be inside the selected square')
    # Reject self intersections instead of silently repairing the intended cut.
    def cross(a, b, c):
        return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])

    def intersects(a, b, c, d):
        if max(a[0], b[0]) < min(c[0], d[0]) or max(c[0], d[0]) < min(a[0], b[0]):
            return False
        if max(a[1], b[1]) < min(c[1], d[1]) or max(c[1], d[1]) < min(a[1], b[1]):
            return False
        return cross(a,b,c)*cross(a,b,d) <= 0 and cross(c,d,a)*cross(c,d,b) <= 0

    n = len(points)
    area = abs(sum(points[i][0]*points[(i+1)%n][1]-points[(i+1)%n][0]*points[i][1] for i in range(n)))/2
    crossed = any(intersects(points[i],points[(i+1)%n],points[j],points[(j+1)%n])
                  for i in range(n) for j in range(i+1,n) if j != i+1 and not (i == 0 and j == n-1))
    if len({tuple(p) for p in points}) != n or crossed or area < 16:
        raise ValueError('Cut outline must be simple, non-crossing, and cover at least 16 pixels')
    mask = geometry_mask([dict(type='Polygon', coordinates=[points+[points[0]]])],
                         (height, size), Affine.translation(x, y), invert=True)
    return size, x, y, points, mask


def save(root, payload):
    with LOCK:
        current = state(root)
        if payload.get('revision') != current.pop('revision'):
            raise ReviewStateConflictError('Component library changed in another tab. Reload library; your drawing is retained.')
        action = payload.get('action')
        if action == 'component':
            size, x, y, points, mask = geometry(payload)
            height = mask.shape[0]
            edges = edge_rules(payload.get('edges'))
            src, entry, _ = checked_window(root, payload.get('chip'))
            name = payload.get('name', '')
            tags = payload.get('tags', '')
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 100 or not isinstance(tags, str) or len(tags) > 300:
                raise ValueError('Provide a name (1–100 characters) and short characteristic tags')
            enabled, borders_clear = payload.get('enabled', False), payload.get('borders_clear', False)
            if type(enabled) is not bool or type(borders_clear) is not bool or (enabled and not borders_clear):
                raise ValueError('Saving for generation requires clear object/shadow borders')
            if is_background(dict(tags=tags)) and not mask.all():
                raise ValueError('A background tile must keep the whole square or rectangle')
            data = pixels(root, entry['id'])[:, y:y+height, x:x+size]
            identifier = uuid4().hex
            folder = workspace(root) / 'components'
            folder.mkdir(parents=True, exist_ok=True)
            # Immutable lossless four-band extraction + explicit keep mask. No inpainting/resizing.
            np.savez_compressed(folder / (identifier+'.npz'), image=data, keep_mask=mask)
            (folder / (identifier+'.png')).write_bytes(png(data, mask))
            record = dict(id=identifier, name=name.strip(), tags=tags.strip(), chip=entry['id'],
                          size=size if size == height else None, width=size, height=height, x=x, y=y, polygon=points, source_id=src['source_id'],
                          source_sha256=src['source_sha256'], pixel_sha256=hashlib.sha256(data.tobytes()).hexdigest(),
                          source_x=entry['x']+x, source_y=entry['y']+y, resolution_m=.3,
                          crs=src['crs'], source_transform=src['transform'], training_ready=False,
                          label_status='Not labeled or approved; geometry draft only',
                          kept_pixels=int(mask.sum()), edges=edges, enabled=enabled, borders_clear=borders_clear)
            current['components'].append(record)
        elif action == 'tile-metadata':
            tile = next((v for v in current['components'] if v['id'] == payload.get('id')), None)
            if tile is None:
                raise ValueError('Unknown library tile')
            name, tags, enabled = payload.get('name'), payload.get('tags'), payload.get('enabled')
            if (not isinstance(name, str) or not 1 <= len(name.strip()) <= 100
                    or not isinstance(tags, str) or len(tags) > 300 or type(enabled) is not bool):
                raise ValueError('Provide a tile name, short tags, and an enabled flag')
            edges = edge_rules(payload.get('edges'))
            borders_clear = payload.get('borders_clear', False)
            if type(borders_clear) is not bool or (enabled and not borders_clear):
                raise ValueError('Before enabling, confirm that no relevant object or its shadow crosses any tile edge')
            if is_background(dict(tags=tags)) and tile['kept_pixels'] != math.prod(dimensions(tile)):
                raise ValueError('A background tile must keep the whole square')
            tile.update(name=name.strip(), tags=tags.strip(), enabled=enabled, edges=edges, borders_clear=borders_clear)
        elif action == 'generate':
            generate(root, current, payload)
        elif action == 'review-generated':
            chip = next((v for v in current.get('generated', []) if v['id'] == payload.get('id')), None)
            if chip is None:
                raise ValueError('Unknown generated chip')
            status, notes = payload.get('review_status'), payload.get('review_notes')
            if status not in ('unreviewed', 'seams-accepted', 'rejected') or not isinstance(notes, str) or len(notes) > 2000:
                raise ValueError('Invalid generated-chip review')
            chip.update(review_status=status, review_notes=notes)
        elif action == 'layout':
            size = payload.get('size')
            ids = payload.get('components')
            name = payload.get('name', '')
            if (type(size) is not int or size not in SIZES or not isinstance(ids, list)
                    or len(ids) != (512//size)**2 or not isinstance(name, str) or not 1 <= len(name.strip()) <= 100):
                raise ValueError('Invalid 512 px layout')
            known = {v['id']: v for v in current['components']}
            if any(v is not None and (not isinstance(v, str) or v not in known or known[v]['size'] != size) for v in ids):
                raise ValueError('Layout components must have the same standard size')
            if not any(ids):
                raise ValueError('Place at least one component')
            current['layouts'].append(dict(id=uuid4().hex, name=name.strip(), size=size,
                                          components=ids, training_ready=False))
        else:
            raise ValueError('Unknown recombinant action')
        _write_json_atomic(workspace(root) / 'library.json', current)
        return state(root)


def component_image(root, identifier):
    if identifier not in {v['id'] for v in state(root)['components']}:
        raise ValueError('Unknown component')
    return (workspace(root) / 'components' / (identifier+'.png')).read_bytes()


def generated_image(root, identifier, variant='blended'):
    if variant not in ('blended', 'unblended'):
        raise ValueError('Unknown generated preview variant')
    recipe = next((v for v in state(root).get('generated', []) if v['id'] == identifier), None)
    if recipe is None:
        raise ValueError('Unknown generated chip')
    folder = workspace(root) / 'generated'
    raw = folder / (identifier+'-unblended.png')
    if variant == 'unblended' and recipe.get('unblended_pixel_sha256') and not raw.exists():
        raise ValueError('Saved unblended preview is missing; cannot show a valid comparison')
    # Old recipes were hard-cut only, so their normal preview is also the raw view.
    return (raw if variant == 'unblended' and raw.exists() else folder/(identifier+'.png')).read_bytes()

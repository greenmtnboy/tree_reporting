"""Opt-in 30 cm experiment, preserving existing chip footprints and source dates."""
from pathlib import Path
import math
import xml.etree.ElementTree as ET
from functools import lru_cache

import rasterio


def same_chip_footprints(first_path, first_pixels, second_path, second_pixels):
    """Only permit cross-resolution overlays for identical source imagery/grids."""
    first_path, second_path = Path(first_path).resolve(), Path(second_path).resolve()
    try:
        version = (_raster_version(first_path), _raster_version(second_path))
    except (OSError, ValueError, ET.ParseError):
        return False
    return _same_chip_footprints(first_path,first_pixels,second_path,second_pixels,version)


def _raster_version(path, seen=None):
    """Invalidate identity proofs when a VRT or any referenced asset changes."""
    seen=set() if seen is None else seen
    path=Path(path).resolve()
    if path in seen:
        raise ValueError('Cyclic VRT source')
    stat=path.stat()
    result=[(str(path),stat.st_mtime_ns,stat.st_ctime_ns,stat.st_size)]
    if path.suffix.lower()=='.vrt':
        tree=ET.parse(path)
        sources=set()
        for node in tree.findall('.//SourceFilename')+tree.findall('.//SourceDataset'):
            source=Path(node.text)
            sources.add((path.parent/source).resolve() if node.get('relativeToVRT')=='1' else source.resolve())
        for source in sorted(sources):result.extend(_raster_version(source,seen|{path}))
    return tuple(result)


@lru_cache(maxsize=512)
def _same_chip_footprints(first_path,first_pixels,second_path,second_pixels,version):
    if first_path == second_path and first_pixels == second_pixels:
        return True
    try:
        equivalent = same_vrt_sources(first_path, second_path)
        if not equivalent:
            for source, target in ((first_path,second_path),(second_path,first_path)):
                try:
                    validate_resolution_derivative(source,target)
                    equivalent = True
                    break
                except (OSError, ValueError, ET.ParseError, rasterio.errors.RasterioError):
                    pass
        if not equivalent:
            return False
        with rasterio.open(first_path) as first, rasterio.open(second_path) as second:
            return (first.crs == second.crs and first.bounds == second.bounds
                    and first.transform.c == second.transform.c and first.transform.f == second.transform.f
                    and math.isclose(first.res[0]*first_pixels, second.res[0]*second_pixels)
                    and math.isclose(first.res[1]*first_pixels, second.res[1]*second_pixels))
    except (OSError, ET.ParseError, rasterio.errors.RasterioError):
        return False


def _refined_document(source: Path, resolution_m: float):
    """Regrid direct source rasters, never the already-downsampled mosaic pixels."""
    document = ET.parse(source)
    root = document.getroot()
    if root.tag != 'VRTDataset' or root.get('subClass'):
        raise ValueError('Expected a simple source mosaic VRT')
    transform = [float(v) for v in root.findtext('GeoTransform').split(',')]
    if transform[2] or transform[4] or not math.isclose(transform[1], -transform[5]):
        raise ValueError('Expected a north-up square-pixel raster')
    factor = transform[1] / resolution_m
    if factor < 1 or not math.isclose(factor, round(factor)):
        raise ValueError('Expected integer resolution refinement')
    for key in ('rasterXSize', 'rasterYSize'):
        root.set(key, str(round(int(root.get(key)) * factor)))
    transform[1], transform[5] = resolution_m, -resolution_m
    root.find('GeoTransform').text = ', '.join(map(str, transform))
    sources = root.findall('.//SimpleSource') + root.findall('.//ComplexSource')
    if not sources:
        raise ValueError('No direct mosaic sources')
    resolutions = {}
    for entry in sources:
        name = entry.find('SourceFilename')
        path = Path(name.text)
        if name.get('relativeToVRT') == '1':
            path = (source.parent / path).resolve()
        if path.suffix.lower() == '.vrt':
            raise ValueError('Nested VRT may hide prior downsampling')
        if str(path) not in resolutions:
            with rasterio.open(path) as raster:
                resolutions[str(path)] = raster.res
        name.text = str(path)
        name.set('relativeToVRT', '0')
        rect = entry.find('DstRect')
        for key in ('xOff', 'yOff', 'xSize', 'ySize'):
            rect.set(key, str(float(rect.get(key)) * factor))
        sampling = entry.find('Resampling')
        if sampling is None:
            sampling = ET.SubElement(entry, 'Resampling')
        sampling.text = 'bilinear'
    return document, resolutions


def validate_resolution_derivative(source: Path, destination: Path):
    """Prove this VRT is exactly our regrid of the same direct source files.

    Compare the whole generated document, not basenames/bounds alone: source
    windows, band mapping, nodata and geographic placement must all agree.
    Deliberately uncached so changed files cannot retain a stale approval.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    with rasterio.open(destination) as raster:
        resolution = raster.res[0]
    expected, _ = _refined_document(source, resolution)
    actual = ET.parse(destination)
    _normalize_vrt_sources(expected, source)
    _normalize_vrt_sources(actual, destination)
    if ET.tostring(expected.getroot()) != ET.tostring(actual.getroot()):
        raise ValueError('Raster is not a verified resolution-only derivative')
    with rasterio.open(source) as old, rasterio.open(destination) as new:
        if old.bounds != new.bounds or old.crs != new.crs:
            raise ValueError('Resolution conversion changed geographic footprint')


def finer_vrt(source: Path, destination: Path, resolution_m: float = .3):
    """Regrid direct source rasters, never the already-downsampled mosaic pixels."""
    document, resolutions = _refined_document(source, resolution_m)
    if destination.exists():
        raise ValueError(f'Refusing to overwrite {destination}')
    document.write(destination, encoding='utf-8', xml_declaration=True)
    validate_resolution_derivative(source, destination)
    return resolutions


def apply_resolution_recipe(config, experiment, *, reuse_verified=False):
    """A fresh Swin run; meter-based settings and geographic split policy unchanged."""
    if not math.isclose(config.imagery.resolution_m, .6) or config.imagery.chip_pixels != 256:
        raise ValueError('30 cm recipe expects the established 60 cm / 256 grid')
    source = config.imagery.local_raster
    destination = source.with_name(f'{source.stem}-{experiment}-30cm.vrt')
    if reuse_verified and destination.exists():
        validate_resolution_derivative(source, destination)
        _, native = _refined_document(source, .3)
    else:
        native = finer_vrt(source, destination)
    expected = .3 if config.inventory.city.lower() == 'usbos' else .6
    if any(not all(math.isclose(v, expected, abs_tol=1e-6) for v in res) for res in native.values()):
        raise ValueError('Unexpected native source resolution')
    config.imagery.local_raster = destination
    config.imagery.source_resolution_m = expected
    config.imagery.resolution_m = .3
    config.imagery.chip_pixels = 512
    config.targets.gaussian_sigma_px *= 2  # expressed on the output grid
    config.evaluation.nms_kernel = 2 * (config.evaluation.nms_kernel - 1) + 1
    config.model.backbone = 'swin_tiny'
    config.model.pretrained = True
    config.model.crown_head = True
    config.training.batch_size = 2
    config.training.accumulate_grad_batches = 16  # effective batch remains 32
    config.training.epochs = 80
    return {'source_raster': str(source), 'model_raster': str(destination),
            'native_resolution_m': expected, 'resolution_m': .3,
            'chip_pixels': 512, 'footprint_m': 153.6, 'resampling': 'bilinear'}


def _normalize_vrt_sources(tree, path):
    """Canonicalize references to existing files while preserving all raster semantics."""
    nodes = tree.findall('.//SourceFilename') + tree.findall('.//SourceDataset')
    if tree.getroot().tag != 'VRTDataset' or not nodes:
        raise ValueError('Expected a VRT with explicit source pixels')
    for node in nodes:
        value = Path(node.text)
        value = (path.parent / value).resolve() if node.get('relativeToVRT') == '1' else value.resolve()
        if not value.is_file():
            raise ValueError('VRT source is missing')
        node.text = str(value)
        node.set('relativeToVRT', '0')


def same_vrt_sources(source: Path, destination: Path) -> bool:
    """Prove path-only relocated VRT copies name exactly the same local pixels."""
    def normalized(path):
        path = Path(path).resolve()
        tree = ET.parse(path)
        _normalize_vrt_sources(tree, path)
        return ET.tostring(tree.getroot())
    if Path(source).suffix.lower() != '.vrt' or Path(destination).suffix.lower() != '.vrt':
        return False
    try:
        return normalized(source) == normalized(destination)
    except (OSError, ValueError, ET.ParseError):
        return False


def same_vrt_graph(source: Path, destination: Path) -> bool:
    """Compare relocated nested VRTs, retaining every operation and leaf asset."""
    import hashlib

    def normalized(path, seen):
        path = Path(path).resolve()
        if path in seen:
            raise ValueError('Cyclic VRT source')
        if not path.is_file():
            raise ValueError('Missing raster dependency')
        if path.suffix.lower() != '.vrt':
            return str(path)
        tree = ET.parse(path)
        _normalize_vrt_sources(tree, path)
        for node in tree.findall('.//SourceFilename') + tree.findall('.//SourceDataset'):
            node.text = normalized(node.text, seen | {path})
        return hashlib.sha256(ET.tostring(tree.getroot())).hexdigest()

    try:
        return normalized(source, set()) == normalized(destination, set())
    except (OSError, ValueError, ET.ParseError):
        return False

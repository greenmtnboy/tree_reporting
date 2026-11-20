"""Identify a chip catalog's imagery without confusing equal filenames."""
from pathlib import Path


def matching_catalog_summary(root, summary, raster):
    # A supplemental model pass is a subset, not a replacement review catalog.
    return (summary.get('catalog_role') != 'supplemental_inference'
            and matches_catalog_imagery(root, summary.get('source_raster'), raster))


def resolve_catalog_raster(root, source):
    """Resolve an existing source or its exact archived artifact suffix."""
    if not source:
        return None
    root = Path(root).resolve()
    text = str(source).replace('\\', '/')
    if '..' in text.split('/'):
        return None
    candidate = Path(text)
    if not candidate.is_file():
        for folder in ('run-inputs', 'imagery'):
            marker = '/' + folder + '/'
            if marker in text:
                relocated = (root / folder / text.split(marker, 1)[1]).resolve()
                if relocated.is_relative_to(root / folder) and relocated.is_file():
                    candidate = relocated
                    break
    return candidate.resolve() if candidate.is_file() else None


def matches_catalog_imagery(root, source, raster, *, allow_resolution_derivative=False):
    candidate = resolve_catalog_raster(root, source)
    raster = Path(raster).resolve()
    if candidate is None or not raster.is_file():
        return False
    if candidate.resolve() == raster:
        return True
    from urban_tree_ml.resolution_recipe import same_vrt_sources
    if same_vrt_sources(candidate, raster):
        return True
    if allow_resolution_derivative:
        from urban_tree_ml.resolution_recipe import validate_resolution_derivative
        import xml.etree.ElementTree as ET
        import rasterio
        for first, second in ((raster,candidate),(candidate,raster)):
            try:
                validate_resolution_derivative(first,second)
                return True
            except (OSError,ValueError,ET.ParseError,rasterio.errors.RasterioError):
                pass
    return False

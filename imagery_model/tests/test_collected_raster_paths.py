from types import SimpleNamespace

from urban_tree_ml.model_debug import RunDebugCatalog
from urban_tree_ml.resolution_recipe import same_vrt_sources


def test_remote_run_input_resolves_exact_local_copy(tmp_path):
    catalog = object.__new__(RunDebugCatalog)
    config = SimpleNamespace(paths=SimpleNamespace(root=tmp_path),
                             inventory=SimpleNamespace(city='USFFX'), dataset='review')
    catalog.config = config
    catalog.raster_path = tmp_path / 'review.vrt'
    bundle = SimpleNamespace(inventory=config.inventory, dataset='v20')
    exact = tmp_path / 'run-inputs/v20/usffx/imagery-30cm.vrt'
    exact.parent.mkdir(parents=True)
    exact.write_text('frozen copy')
    other = tmp_path / 'imagery/usffx/imagery-30cm.vrt'
    other.parent.mkdir(parents=True)
    other.write_text('different imagery')
    assert catalog._resolve_local_raster('/lambda/nfs/tree/urban-tree-ml/run-inputs/v20/usffx/imagery-30cm.vrt', bundle) == exact.resolve()


def test_missing_year_never_falls_back_to_current_year_or_same_dataset(tmp_path):
    catalog=object.__new__(RunDebugCatalog)
    config=SimpleNamespace(paths=SimpleNamespace(root=tmp_path),inventory=SimpleNamespace(city='USBOS'),dataset='same')
    catalog.config=config
    current=tmp_path/'imagery/usbos/2025/imagery.vrt'
    current.parent.mkdir(parents=True);current.write_text('2025')
    catalog.raster_path=current
    missing='/remote/artifacts/imagery/usbos/2021/imagery.vrt'
    assert catalog._resolve_local_raster(missing,config)!=current
    exact=tmp_path/'imagery/usbos/2021/imagery.vrt'
    exact.parent.mkdir();exact.write_text('2021')
    assert catalog._resolve_local_raster(missing,config)==exact.resolve()


def test_path_only_vrt_relocation_requires_identical_sources_and_windows(tmp_path):
    leaf = tmp_path / 'pixels.tif'
    leaf.write_bytes(b'fixture')
    original = tmp_path / 'original.vrt'
    copied = tmp_path / 'frozen/copy.vrt'
    copied.parent.mkdir()
    template = '<VRTDataset><VRTRasterBand band="1"><SimpleSource><SourceFilename relativeToVRT="1">{}</SourceFilename><SourceBand>1</SourceBand><SrcRect xOff="0" yOff="0" xSize="10" ySize="10"/></SimpleSource></VRTRasterBand></VRTDataset>'
    original.write_text(template.format('pixels.tif'))
    copied.write_text(template.format('../pixels.tif'))
    assert same_vrt_sources(original, copied)
    copied.write_text(template.format('../pixels.tif').replace('xOff="0"', 'xOff="1"'))
    assert not same_vrt_sources(original, copied)
    copied.write_text(template.format('../missing.tif'))
    assert not same_vrt_sources(original, copied)

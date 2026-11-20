"""Image identity is city + acquisition + actual source pixels, never a basename."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from urban_tree_ml.catalog_imagery import matches_catalog_imagery
from urban_tree_ml.config import load_config
from urban_tree_ml.qa_server import _checked_review_context
from urban_tree_ml.resolution_recipe import same_chip_footprints, same_vrt_sources


def test_resolution_derivative_accepts_equivalent_paths_and_rejects_changed_pixels(tmp_path):
    import os
    import xml.etree.ElementTree as ET
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.shutil import copy
    from urban_tree_ml.resolution_recipe import finer_vrt, validate_resolution_derivative
    from urban_tree_ml.qa_server import _context_matches_bundle
    leaf = tmp_path / 'pixels.tif'
    with rasterio.open(leaf, 'w', driver='GTiff', width=8, height=8, count=4,
                       dtype='uint8', crs='EPSG:32619', transform=from_origin(1, 10, .6, .6)):
        pass
    source = tmp_path / 'source.vrt'
    copy(leaf, source, driver='VRT')
    destination = tmp_path / 'model.vrt'
    finer_vrt(source, destination, .3)
    tree = ET.parse(destination)
    for node in tree.findall('.//SourceFilename'):
        node.text = leaf.as_posix()
    tree.write(destination)
    validate_resolution_derivative(source, destination)
    assert same_chip_footprints(source, 256, destination, 512)
    context = SimpleNamespace(raster=source, config=SimpleNamespace(
        inventory=SimpleNamespace(city='USBOS'), imagery=SimpleNamespace(chip_pixels=256)))
    bundle = SimpleNamespace(raster_path=destination, config=SimpleNamespace(
        inventory=SimpleNamespace(city='USBOS'), imagery=SimpleNamespace(chip_pixels=512, acquisition_id=None)))
    assert _context_matches_bundle(context, bundle)
    for node in tree.findall('.//SourceFilename'):
        node.text = os.path.relpath(leaf, destination.parent)
        node.set('relativeToVRT', '1')
    tree.write(destination)
    validate_resolution_derivative(source, destination)
    assert same_chip_footprints(source, 256, destination, 512)
    tree.find('.//DstRect').set('xOff', '1')
    tree.write(destination)
    with pytest.raises(ValueError, match='verified resolution-only'):
        validate_resolution_derivative(source, destination)
    assert not same_chip_footprints(source, 256, destination, 512)
    assert not _context_matches_bundle(context, bundle)


@pytest.mark.parametrize('other',['usffx/2023','usbos/2021','usbos/2025','ussfo/2024'])
def test_same_basename_and_same_extent_are_not_image_identity(tmp_path,other):
    import rasterio
    from rasterio.transform import from_origin
    from rasterio.shutil import copy
    rasters=[]
    for folder in ('usbos/2023',other):
        directory=tmp_path/folder;directory.mkdir(parents=True)
        leaf=directory/'pixels.tif'
        with rasterio.open(leaf,'w',driver='GTiff',width=8,height=8,count=4,dtype='uint8',
                           crs='EPSG:32619',transform=from_origin(1,10,.6,.6)):pass
        vrt=directory/'imagery.vrt';copy(leaf,vrt,driver='VRT')
        import xml.etree.ElementTree as ET
        document=ET.parse(vrt)
        for node in document.findall('.//SourceFilename'):
            node.text=str(leaf);node.set('relativeToVRT','0')
        document.write(vrt)
        rasters.append(vrt)
    a,b=rasters
    assert not same_vrt_sources(a,b)
    assert not same_chip_footprints(a,256,b,256)
    assert not matches_catalog_imagery(tmp_path,a,b,allow_resolution_derivative=True)
    # A cached positive must not survive replacing a VRT's source reference.
    clone=tmp_path/'copy.vrt';clone.write_bytes(a.read_bytes())
    assert same_chip_footprints(a,256,clone,256)
    clone.write_bytes(b.read_bytes())
    assert not same_chip_footprints(a,256,clone,256)


@pytest.mark.parametrize('operation',['serve','snapshot','finalize','heuristics','extend'])
def test_wrong_year_workspace_rejected_before_annotation_mutation(tmp_path,operation):
    from urban_tree_ml.feedback import snapshot_registration_annotations, finalize_registration_feedback
    from urban_tree_ml.quality import refresh_registration_heuristics, build_registration_review
    cfg=load_config(Path(__file__).parents[1]/'configs/boston_naip_external.yaml')
    cfg.paths.root=tmp_path;cfg.paths.annotations=tmp_path/'annotations'
    old=tmp_path/'2023/imagery.vrt';new=tmp_path/'2025/imagery.vrt'
    for path in (old,new):path.parent.mkdir();path.write_text('fixture')
    review=tmp_path/'review';review.mkdir();(review/'index.html').write_text('fixture')
    manifest=dict(metadata=dict(source_raster=str(old),window_pixels=128,test_labels_included=False),samples=[],scenes=[])
    (review/'manifest.json').write_text(json.dumps(manifest))
    (review/'reviews.json').write_text('{"preserve":"user edits"}')
    before={p.name:p.read_bytes() for p in review.iterdir()}
    with pytest.raises(ValueError,match='imagery|different raster'):
        if operation=='serve':_checked_review_context('usbos','Boston',cfg,new,review,None)
        elif operation=='snapshot':snapshot_registration_annotations(cfg,new,review_dir=review)
        elif operation=='finalize':finalize_registration_feedback(cfg,new,review_dir=review)
        elif operation=='heuristics':refresh_registration_heuristics(cfg,new,review_dir=review)
        else:build_registration_review(cfg,new,output_dir=review,extend_existing=True)
    assert {p.name:p.read_bytes() for p in review.iterdir() if p.is_file()}==before
    assert not cfg.paths.annotations.exists()


def test_registered_year_cannot_borrow_another_year_config(tmp_path):
    from urban_tree_ml.temporal_studio import load_contexts
    cfg=SimpleNamespace(paths=SimpleNamespace(root=tmp_path),imagery=SimpleNamespace(acquisition_id='usbos-naip-2023'))
    (tmp_path/'qa').mkdir()
    path=tmp_path/'qa/temporal-acquisitions.json'
    path.write_text(json.dumps(dict(acquisitions=[dict(id='usbos-naip-2025',state='ready',config_path='fixture')])) )
    with pytest.raises(ValueError,match='another year'):
        load_contexts({'usbos':SimpleNamespace(config=cfg)},lambda *a:pytest.fail('must reject before binding'),lambda p:cfg)


def test_registry_rejects_duplicate_ids_even_when_one_not_ready(tmp_path):
    from urban_tree_ml.temporal_studio import load_contexts
    cfg=SimpleNamespace(paths=SimpleNamespace(root=tmp_path))
    (tmp_path/'qa').mkdir()
    (tmp_path/'qa/temporal-acquisitions.json').write_text(json.dumps(dict(acquisitions=[
        dict(id='usbos-naip-2025',state='pending'),dict(id='usbos-naip-2025',state='pending')])))
    with pytest.raises(ValueError,match='duplicate'):
        load_contexts({'usbos':SimpleNamespace(config=cfg)},None,None)


def test_missing_and_traversing_archived_paths_cannot_match(tmp_path):
    current=tmp_path/'imagery/usbos/2025/imagery.vrt'
    current.parent.mkdir(parents=True);current.write_text('fixture')
    assert matches_catalog_imagery(tmp_path,'/remote/artifacts/imagery/usbos/2025/imagery.vrt',current)
    assert not matches_catalog_imagery(tmp_path,'/remote/artifacts/imagery/usbos/2021/imagery.vrt',current)
    assert not matches_catalog_imagery(tmp_path,'/remote/artifacts/run-inputs/../imagery/usbos/2025/imagery.vrt',current)


def test_different_registered_years_cannot_share_review_storage(tmp_path):
    from urban_tree_ml.temporal_studio import load_contexts
    from urban_tree_ml.qa_server import ReviewContext
    cfg=SimpleNamespace(paths=SimpleNamespace(root=tmp_path),imagery=SimpleNamespace(acquisition_id=None))
    (tmp_path/'qa').mkdir()
    records=[dict(id='usbos-naip-'+year,city='usbos',label=year,state='ready',config_path='fixture',
                  raster='fixture',review_dir=str(tmp_path/'shared')) for year in ('2021','2025')]
    (tmp_path/'qa/temporal-acquisitions.json').write_text(json.dumps(dict(acquisitions=records)))
    def checked(*args):return ReviewContext('usbos','Boston',cfg,tmp_path/'raster',tmp_path/'shared',tmp_path/'eval')
    with pytest.raises(ValueError,match='share an annotation workspace'):
        load_contexts({'usbos':SimpleNamespace(config=cfg,directory=tmp_path/'original')},checked,lambda p:cfg)


@pytest.mark.parametrize('override',[False,True])
def test_export_never_substitutes_latest_mosaic_for_missing_evaluation(tmp_path,override):
    from urban_tree_ml.tile_bundle_export import main
    evaluation=tmp_path/'runs/v20/evaluation/train-usbos'
    evaluation.mkdir(parents=True)
    (tmp_path/'chips/fixture').mkdir(parents=True)
    config=dict(inventory=dict(city='USBOS'),dataset='fixture',imagery=dict(local_raster='/remote/imagery/usbos/2021/imagery.vrt'))
    (evaluation/'evaluation-metadata.json').write_text(json.dumps(dict(config=config)))
    (evaluation/'taxonomy.json').write_text('{}')
    latest=tmp_path/'imagery/usbos/2025/imagery.vrt'
    latest.parent.mkdir(parents=True);latest.write_text('different year')
    args=['--artifacts',str(tmp_path),'--run','v20','--cohort','train-usbos','--out',str(tmp_path/'out')]
    if override:args+=['--raster',str(latest)]
    with pytest.raises(SystemExit,match='Exact evaluation imagery unavailable'):
        main(args)
    assert not (tmp_path/'out').exists()

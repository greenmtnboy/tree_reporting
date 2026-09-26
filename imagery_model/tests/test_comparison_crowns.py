import subprocess

from urban_tree_ml.curation_compare import CURATION_COMPARE_JS


def test_crown_geometry_uses_metres_not_output_stride():
    helper = CURATION_COMPARE_JS.split('function predictionCrownPercent', 1)[1].split('function persistCurationModels', 1)[0]
    script = '''const assert=require('node:assert/strict');
function predictionCrownPercent''' + helper + '''
const display={resolution_m:.6,chip_pixels:256,output_stride:2};
assert.equal(predictionCrownPercent({crown_radius_m:3},display),100*6/153.6);
for(const radius of [null,undefined,0,-1,NaN,Infinity])
  assert.equal(predictionCrownPercent({crown_radius_m:radius},display),null);
assert.equal(predictionCrownPercent({crown_radius_m:3},{...display,resolution_m:0}),null);
'''
    result = subprocess.run(['node','-'],input=script,encoding='utf-8',capture_output=True)
    assert result.returncode == 0, result.stderr


def test_crowns_share_layer_cleanup_visibility_and_dimming():
    assert "crown.className = 'curation-comparison-ring comparison-crown'" in CURATION_COMPARE_JS
    assert 'pointer-events:none;z-index:3;border:1px solid' in CURATION_COMPARE_JS
    assert "wrap.querySelectorAll('.curation-comparison-ring').forEach(el => el.remove())" in CURATION_COMPARE_JS

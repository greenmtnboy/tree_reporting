import subprocess
from urban_tree_ml.quality import _render_grouped_registration_html


def test_added_crown_streetview_uses_its_center_and_invalidates_after_move():
    html = _render_grouped_registration_html([], {}, [])
    code = html[html.index('function addedCrownStreetViewTarget('):html.index('function renderStreetViewCamera(')]
    script = code + '''
const anchor={sample_id:'inventory',latitude:42,longitude:-71,target_x:10,target_y:10,transform_a:.6,transform_b:0,transform_d:0,transform_e:-.6};
const region={region_id:'crown',image_x:10,image_y:10};
const a=addedCrownStreetViewTarget(region,anchor);
if(a.latitude!==42||a.longitude!==-71||a.sample_id===anchor.sample_id)throw Error('anchor');
const b=addedCrownStreetViewTarget({...region,image_x:20,image_y:20},anchor);
if(b.latitude>=42||b.longitude<=-71||b.target_x!==20||b.sample_id===a.sample_id)throw Error('moved crown');
if(addedCrownStreetViewTarget(region,null)!==null)throw Error('missing anchor');
'''
    subprocess.run(['node','-e',script],check=True,capture_output=True,text=True)
    assert "!region.tree_id&&!event.shiftKey" in html
    assert 'Shift-click to edit; drag to move' in html

import subprocess
from urban_tree_ml.crown_overlay import CROWN_JS, inject_crown_overlay


def test_estimate_units_clamp_and_missing():
    js = "const window={crownCoefficients:{'genus:Quercus':{scale:.3,b:.7,dbh_max_cm:100,fit_level:'genus',fit_taxon:'Quercus'}}};" + CROWN_JS + """
const a=window.estimateCrown({species:'Quercus rubra',dbh_in:10});
if(Math.abs(a.width_m-.6*25.4**.7)>1e-9)throw Error('units');
if(window.estimateCrown({species:'Quercus rubra',dbh_in:500}).width_m!==.6*100**.7)throw Error('clamp');
for(const dbh_in of [null,0,-1,NaN])if(window.estimateCrown({species:'Quercus rubra',dbh_in}))throw Error('missing');
if(window.estimateCrown({species:'Quercus rubra',dbh_in:10,tree_form:'palm'}))throw Error('palm');
"""
    subprocess.run(['node','-e',js],check=True,capture_output=True,text=True)


def test_missing_table_is_explicit_not_fabricated(tmp_path,monkeypatch):
    monkeypatch.setenv('TREE_ML_DATA_ROOT',str(tmp_path/'imagery/artifacts'))
    html=inject_crown_overlay('<head></head><header></header>')
    assert 'window.estimateCrown' in html
    assert 'not measured canopy' in html


def test_overlap_unknown_crown_is_candidate_but_never_occluder():
    from urban_tree_ml.quality import _render_grouped_registration_html
    html=_render_grouped_registration_html([],{},[])
    start=html.index('const valid=sceneSamples.filter')
    end=html.index('if(!candidates.length)',start)
    js='''
const sceneSamples=[
 {sample_id:'large',width:10,x:0}, {sample_id:'unknown',x:2},
 {sample_id:'outside',x:20}, {sample_id:'human',x:1},
 {sample_id:'unknown2',x:20.1}
].map(s=>({...s,transform_a:1,transform_b:0,transform_d:0,transform_e:-1}));
const reviews={human:{source:'human'}},statusOf=()=> 'aligned',isStacked=()=>false;
const window={estimateCrown:s=>s.width?{width_m:s.width}:null};
const displayedPosition=s=>({x:s.x,y:0});
''' + html[start:end] + '''
if(JSON.stringify(candidates.map(s=>s.sample_id))!=='["unknown"]')throw Error('incorrect unknown crown handling');
'''
    subprocess.run(['node','-e',js],check=True,capture_output=True,encoding='utf-8')

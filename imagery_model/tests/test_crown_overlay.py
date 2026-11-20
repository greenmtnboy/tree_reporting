import subprocess
from urban_tree_ml.crown_overlay import CROWN_JS, inject_crown_overlay


def test_estimate_units_clamp_and_missing():
    js = "const window={crownCoefficients:{'genus:Quercus':{scale:.3,b:.7,dbh_max_cm:100,fit_level:'genus',fit_taxon:'Quercus'}}};" + CROWN_JS + """
const a=window.estimateCrown({species:'Quercus rubra',dbh_in:10});
if(Math.abs(a.width_m-1.5*.6*25.4**.7)>1e-9)throw Error('units');
if(Math.abs(window.estimateCrown({species:'Quercus rubra',dbh_in:500}).width_m-1.5*.6*100**.7)>1e-9)throw Error('clamp');
for(const dbh_in of [null,0,-1,NaN])if(window.estimateCrown({species:'Quercus rubra',dbh_in}))throw Error('missing');
if(window.estimateCrown({species:'Quercus rubra',dbh_in:10,tree_form:'palm'}))throw Error('palm');
"""
    subprocess.run(['node','-e',js],check=True,capture_output=True,text=True)


def test_estimate_past_the_genus_range_grows_at_the_tail_rate():
    js = "const window={crownCoefficients:{'genus:Salix':{scale:.418346,b:.566406,dbh_max_cm:33,tail_b:.646075,tail_dbh_max_cm:385,fit_level:'genus',fit_taxon:'Salix'}}};" + CROWN_JS + """
const edge=1.5*2*.418346*33**.566406, near=(a,b)=>Math.abs(a-b)<1e-9*b;
if(!near(window.estimateCrown({species:'Salix alba',dbh_in:33/2.54}).width_m,edge))throw Error('continuous');
if(!near(window.estimateCrown({species:'Salix alba',dbh_in:114}).width_m,edge*(114*2.54/33)**.646075))throw Error('tail');
if(!near(window.estimateCrown({species:'Salix alba',dbh_in:5000}).width_m,edge*(385/33)**.646075))throw Error('bounded');
"""
    subprocess.run(['node','-e',js],check=True,capture_output=True,text=True)


def test_genus_without_a_tallo_fit_borrows_the_matched_model_crown():
    js = """const window={predictedCrownBySample:{ginkgo:7.5,oak:9,palm:4,nodbh:5},crownCoefficients:{
 'genus:Quercus':{scale:.3,b:.7,dbh_max_cm:100,fit_level:'genus',fit_taxon:'Quercus'},
 'genus:Ginkgo':{scale:.228162,b:.708135,dbh_max_cm:770,fit_level:'division',fit_taxon:'Gymnosperm'},
 'division:Angiosperm':{scale:.35,b:.65,dbh_max_cm:385,fit_level:'division',fit_taxon:'Angiosperm'}}};""" + CROWN_JS + """
const near=(a,b)=>Math.abs(a-b)<1e-9*b;
const g=window.estimateCrown({sample_id:'ginkgo',species:'Ginkgo biloba',dbh_in:8.1});
if(g.width_m!==7.5||g.level!=='matched-prediction')throw Error('division fit should borrow the model crown');
const lone=window.estimateCrown({sample_id:'alone',species:'Ginkgo biloba',dbh_in:8.1});
if(lone.level!=='division'||!near(lone.width_m,1.5*2*.228162*(8.1*2.54)**.708135))throw Error('unmatched keeps scaled Tallo');
if(!near(window.estimateCrown({sample_id:'oak',species:'Quercus rubra',dbh_in:10}).width_m,1.5*.6*25.4**.7))throw Error('own genus fit wins');
if(window.estimateCrown({sample_id:'nodbh',species:'Quercus rubra',dbh_in:null}).width_m!==5)throw Error('no DBH borrows');
if(window.estimateCrown({sample_id:'palm',species:'Palm',dbh_in:10}).width_m!==4)throw Error('palm borrows');
if(!/nearest model prediction/.test(window.crownDescription({sample_id:'ginkgo',species:'Ginkgo biloba',dbh_in:8.1})))throw Error('label');
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

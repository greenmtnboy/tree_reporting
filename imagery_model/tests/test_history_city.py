import subprocess

from urban_tree_ml.model_debug import RUN_HISTORY_HTML


def test_shared_city_controls_history_and_all_cities_have_separate_charts():
    code = RUN_HISTORY_HTML.split('function configureHistoryCity(){', 1)[1].split('async function init(){', 1)[0]
    script = r'''
const label={hidden:false};
const elements={'studio-city':{value:'ussfo'},'history-city':{value:'',closest:()=>label},dataset:{value:'boston'},chart:{innerHTML:''}};
const $=id=>elements[id],esc=String,pct=v=>`${v*100}%`,f2=d=>d.f2;
const catalog={runs:[{city:'USSFO',dataset:'sf',run_id:'sf',v:.4},{city:'USBOS',dataset:'boston',run_id:'bos',v:.2}]};
const metrics=r=>({detection:{f2:r.v,f1:r.v}});
const compatible=()=>catalog.runs.filter(r=>!$('history-city').value||r.city===$('history-city').value);
const location={search:'?city=ussfo'};
''' + 'function configureHistoryCity(){' + code + r'''
configureHistoryCity();chart();
if($('history-city').value!=='USSFO'||!label.hidden||$('dataset').value!=='')throw Error('city/filter sync');
if(!$('chart').innerHTML.includes('San Francisco')||$('chart').innerHTML.includes('Boston'))throw Error('SF graph');
location.search='?city=usbos';configureHistoryCity();chart();
if(!$('chart').innerHTML.includes('Boston')||$('chart').innerHTML.includes('San Francisco'))throw Error('Boston graph');
location.search='?city=all';configureHistoryCity();chart();
if(($('chart').innerHTML.match(/<svg /g)||[]).length!==2)throw Error('Separate graphs required');
'''
    subprocess.run(['node', '-e', script], check=True, capture_output=True, text=True)

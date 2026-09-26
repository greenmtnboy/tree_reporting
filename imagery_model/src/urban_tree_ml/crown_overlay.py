"""Read the shared Tallo coefficient table for advisory review overlays only."""
import csv
import hashlib
import json
import os
from pathlib import Path


def inject_crown_overlay(html):
    root = Path(os.environ.get('TREE_ML_DATA_ROOT', './artifacts')).resolve()
    configured = os.environ.get('TREE_ML_CROWN_COEFFICIENTS')
    candidates = ([Path(configured)] if configured else []) + [
        root.parents[1] / 'data/raw/crown_width_coefficients.csv',
        Path(__file__).resolve().parents[3] / 'data/raw/crown_width_coefficients.csv']
    path = next((p for p in candidates if p.is_file()), None)
    rows = list(csv.DictReader(path.open(encoding='utf-8'))) if path else []
    table = {f"{r['level']}:{r['taxon']}": r for r in rows}
    payload = json.dumps(table).replace('<', '\\u003c')
    digest = hashlib.sha256(path.read_bytes()).hexdigest() if path else 'unavailable'
    script = '<script>window.crownCoefficients=' + payload + ';window.crownCoefficientHash=' + json.dumps(digest) + ';' + CROWN_JS + '</script>'
    style = '<style>.crown-circle{position:absolute;display:block;transform:translate(-50%,-50%);border:1px solid #d9f6b380;border-radius:50%;pointer-events:none;background:none;z-index:2}.crown-circle.predicted{border-color:#ecc0fc70}.tree-marker[data-status="occluded"],.tree-choice[data-status="occluded"]{border-color:#9aa9ba!important}.tree-species-label[data-status="occluded"]{color:#9aa9ba}</style>'
    style += '<style>.crown-circle[hidden]{display:none}.crown-legend{font-size:12px;color:#bfd0c3}</style>'
    legend='<p class="crown-legend">Thin green circles: inventory crown estimates; pale purple: model-derived estimates. Tallo allometry from DBH and genus, not measured canopy. Dashed halos remain the matching tolerance.</p>'
    return html.replace('<head>', '<head>'+script+style, 1).replace('</header>',legend+'</header>',1)


CROWN_JS = r"""
window.estimateCrown = function(tree){
 const radius=Number(tree.crown_radius_m);
 if(Number.isFinite(radius)&&radius>0)return {width_m:2*radius,level:'image-head',taxon:'direct crown prediction'};
 const dbh=Number(tree.dbh_in),species=String(tree.inventory_species||tree.species||'Unknown').trim(),genus=species.split(/\s+/)[0];
 if(!Number.isFinite(dbh)||dbh<=0)return null;
 const rows=window.crownCoefficients,row=rows['genus:'+genus];
 if(tree.tree_form==='palm'||species==='Palm'||species==='Cactus'||row?.family==='Arecaceae')return null;
 const fallback=['Unknown','Dead','Shrub',''].includes(species)?'global:all':tree.tree_form==='conifer'?'division:Gymnosperm':'division:Angiosperm';
 const fit=row||rows[fallback]; if(!fit)return null;
 const cm=Math.min(Math.max(dbh*2.54,1),Number(fit.dbh_max_cm));
 return {width_m:2*Number(fit.scale)*cm**Number(fit.b),level:fit.fit_level,taxon:fit.fit_taxon};
};
window.crownDescription=function(tree){const c=window.estimateCrown(tree);return c?(c.level==='image-head'?`Image-predicted crown diameter ${c.width_m.toFixed(1)} m`:`Estimated crown width ${c.width_m.toFixed(1)} m · Tallo ${c.level} fit (${c.taxon}); DBH-derived, not image-measured`):'Crown estimate unavailable'};
const calculateCrown=window.estimateCrown,crownCache=new Map();
window.estimateCrown=function(tree){
 const key=JSON.stringify([tree.inventory_species||tree.species,tree.dbh_in,tree.tree_form,tree.crown_radius_m]);
 if(crownCache.has(key))return crownCache.get(key);
 const value=calculateCrown(tree);if(crownCache.size>10000)crownCache.clear();crownCache.set(key,value);return value;
};
"""

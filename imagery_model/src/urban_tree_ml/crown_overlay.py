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
    legend='<p class="crown-legend">Thin green circles: inventory crown estimates; pale purple: model-derived estimates. Tallo allometry from DBH and genus, drawn wider for small street trees and narrower for veterans (×' + str(TALLO_REVIEW_SCALE_MAX) + ' at a sapling, ×' + str(TALLO_REVIEW_SCALE_MIN) + ' past ~43 in), not measured canopy; a genus Tallo has no fit for borrows the nearest model crown. Dashed halos remain the matching tolerance.</p>'
    return html.replace('<head>', '<head>'+script+style, 1).replace('</header>',legend+'</header>',1)


OVERLAY_DOM_JS = r"""
// Shared DOM overlay primitive: stays sharp independently of the aerial bitmap.
window.createTreeOverlay=function(tree,x,y,width,height){
 const crown=document.createElement('i');crown.className='crown-circle';
 const label=document.createElement('span');label.className='tree-species-label';
 for(const el of [crown,label]){el.style.left=`${100*x/width}%`;el.style.top=`${100*y/height}%`;if(tree.sample_id)el.dataset.sampleId=tree.sample_id;}
 label.textContent=tree.species||'Unknown species';
 return {crown,label};
};
"""

# Tallo is mostly forest trees; open-grown street trees spread wider.  Against
# Boston's LIDAR crowns (height_check 'accepted', measured DBH, ~72k trees) the
# Tallo width runs a median 1.42x short (Ginkgo 1.56, Gleditsia 1.59, Platanus
# 1.09).  A flat x1.5 was right only in the middle: against 8,863 hand-sized
# crowns in the curator (Boston, SF, NYC, DC, Fairfax; Nov 2026) the gap is
# x3.1 at DBH 0-3 in, x1.4 at 6-12 in and x0.66 past 36 in, where x1.5 drew
# veterans more than twice the width reviewers drew.  The scale is therefore
# a power of DBH fitted to those crowns (log-log), held between the bounds.
# Overlay only: the published crown model and the weak training labels are
# unscaled.
TALLO_REVIEW_SCALE_A = 3.65
TALLO_REVIEW_SCALE_P = -0.42
TALLO_REVIEW_SCALE_MIN = 0.75
TALLO_REVIEW_SCALE_MAX = 2.5


def tallo_review_scale(dbh_in: float) -> float:
    """The overlay's multiplier on a Tallo width for a stem of ``dbh_in``."""
    raw = TALLO_REVIEW_SCALE_A * max(dbh_in, 1.0) ** TALLO_REVIEW_SCALE_P
    return min(TALLO_REVIEW_SCALE_MAX, max(TALLO_REVIEW_SCALE_MIN, raw))

# Fit levels that are Tallo's own data for the tree (its genus, or relatives in
# its family).  A division or global fallback is Tallo having no data for it,
# so the overlay prefers the model's crown at that spot when one was matched.
TALLO_OWN_FIT_LEVELS = ('genus', 'family')

CROWN_JS = OVERLAY_DOM_JS + r"""
// Multiplier on a Tallo width by DBH in inches (crown_overlay.tallo_review_scale).
window.talloReviewScale=dbh=>Math.min(__SMAX__,Math.max(__SMIN__,__SA__*Math.max(dbh,1)**__SP__));
// Inventory trees with no Tallo fit of their own borrow the matched model
// prediction's crown: sample_id -> crown width in metres (set by the page).
window.predictedCrownBySample=window.predictedCrownBySample||{};
window.estimateCrown = function(tree){
 const radius=Number(tree.crown_radius_m);
 if(Number.isFinite(radius)&&radius>0)return {width_m:2*radius,level:'image-head',taxon:'direct crown prediction'};
 const matched=Number(tree.sample_id!=null?window.predictedCrownBySample[tree.sample_id]:NaN);
 const borrowed=Number.isFinite(matched)&&matched>0?{width_m:matched,level:'matched-prediction',taxon:'nearest model crown'}:null;
 const dbh=Number(tree.dbh_in),species=String(tree.inventory_species||tree.species||'Unknown').trim(),genus=species.split(/\s+/)[0];
 if(!Number.isFinite(dbh)||dbh<=0)return borrowed;
 const rows=window.crownCoefficients,row=rows['genus:'+genus];
 if(tree.tree_form==='palm'||species==='Palm'||species==='Cactus'||row?.family==='Arecaceae')return borrowed;
 const fallback=['Unknown','Dead','Shrub',''].includes(species)?'global:all':tree.tree_form==='conifer'?'division:Gymnosperm':'division:Angiosperm';
 const fit=row||rows[fallback];
 if(borrowed&&!(fit&&__OWN_LEVELS__.includes(fit.fit_level)))return borrowed;
 if(!fit)return null;
 // The fit's own curve up to its largest stem, then its tail fit's exponent
 // up to the tail's largest stem (crown_allometry_fit.crown_width_m), scaled
 // for street trees by DBH (talloReviewScale).
 const m=Number(fit.dbh_max_cm),tailB=Number(fit.tail_b||fit.b),top=Math.max(Number(fit.tail_dbh_max_cm||m),m);
 const cm=Math.min(Math.max(dbh*2.54,1),top);
 const k=window.talloReviewScale(dbh);
 return {scale:k,width_m:k*2*Number(fit.scale)*Math.min(cm,m)**Number(fit.b)*(Math.max(cm,m)/m)**tailB,level:fit.fit_level,taxon:fit.fit_taxon};
};
window.crownDescription=function(tree){const c=window.estimateCrown(tree);if(!c)return 'Crown estimate unavailable';
 if(c.level==='image-head')return `Image-predicted crown diameter ${c.width_m.toFixed(1)} m`;
 if(c.level==='matched-prediction')return `Crown width ${c.width_m.toFixed(1)} m from the nearest model prediction; no Tallo fit for this genus`;
 return `Estimated crown width ${c.width_m.toFixed(1)} m · Tallo ${c.level} fit (${c.taxon}) ×${c.scale.toFixed(2)} for a street tree this size; DBH-derived, not image-measured`};
const calculateCrown=window.estimateCrown,crownCache=new Map();
window.estimateCrown=function(tree){
 const key=JSON.stringify([tree.inventory_species||tree.species,tree.dbh_in,tree.tree_form,tree.crown_radius_m,
   tree.sample_id!=null?window.predictedCrownBySample[tree.sample_id]??null:null]);
 if(crownCache.has(key))return crownCache.get(key);
 const value=calculateCrown(tree);if(crownCache.size>10000)crownCache.clear();crownCache.set(key,value);return value;
};
""".replace('__SA__', repr(TALLO_REVIEW_SCALE_A)).replace('__SP__', repr(TALLO_REVIEW_SCALE_P)).replace('__SMIN__', repr(TALLO_REVIEW_SCALE_MIN)).replace('__SMAX__', repr(TALLO_REVIEW_SCALE_MAX)).replace('__OWN_LEVELS__', json.dumps(list(TALLO_OWN_FIT_LEVELS)))

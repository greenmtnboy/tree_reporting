"""Manual component extraction and composition; no training-label editor."""

HTML = r'''<!doctype html><html><head><meta charset="utf-8"><title>Recombinant tiles</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#101b16;color:#e5eee8;font:14px system-ui}
main{padding:20px;max-width:1700px;margin:auto}h1{margin:0 0 6px;font-size:25px}h2{font-size:17px;margin:0 0 12px}
p{line-height:1.5;color:#bdd0c3}.notice{padding:12px;background:#342e1d;color:#f1de9d;border-radius:8px}
.columns{display:grid;grid-template-columns:260px minmax(400px,1fr) 360px;gap:18px;margin-top:18px}
section{background:#182820;padding:14px;border:1px solid #354b3d;border-radius:10px;min-width:0}
label{display:block;margin:10px 0}input,select,button{font:inherit;background:#24382c;color:#eef8f0;border:1px solid #526b59;border-radius:5px;padding:7px}
button{cursor:pointer}button:hover{background:#34513e}button:disabled{opacity:.45;cursor:default}input[type=text],select{max-width:100%}
button[aria-pressed=true]{background:#425837;border-color:#ffe38e;box-shadow:0 0 0 1px #ffe38e}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}.row label{margin:3px 0}
#source-map{width:100%;height:auto;cursor:pointer;background:#16221d}canvas{display:block;max-width:100%;touch-action:none}
.canvas-scroll{overflow:auto;max-height:70vh;background:#0c120f}#editor{width:100%;cursor:crosshair}#composite{width:100%}
.edge-row{padding:6px;border-left:3px solid #ffe38e;margin:8px 0}.edge-row select{max-width:135px;font-size:12px}.edge-row label{display:inline-block;margin:4px}
#tile-preview{width:100%;max-width:256px;height:auto}#generator{display:grid;grid-template-columns:minmax(280px,512px) 1fr;gap:20px}textarea{width:100%;min-height:70px;background:#24382c;color:#eef8f0}#tile-selection{overflow-wrap:anywhere}
.checker{background-color:#243028;background-image:linear-gradient(45deg,#3b4b3f 25%,transparent 25%),linear-gradient(-45deg,#3b4b3f 25%,transparent 25%),linear-gradient(45deg,transparent 75%,#3b4b3f 75%),linear-gradient(-45deg,transparent 75%,#3b4b3f 75%);background-size:20px 20px;background-position:0 0,0 10px,10px -10px,-10px 0}
#library{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:10px;max-height:450px;overflow:auto}
.piece{border:2px solid transparent;text-align:left;padding:6px}.piece.selected{border-color:#ffda78}.piece img{width:100%;display:block;image-rendering:auto}.piece small{display:block;color:#baccbe}
#status{padding:12px;border:1px solid #5b775f;border-radius:8px;margin-top:12px;white-space:pre-wrap}#status.error{border-color:#ed8e7d;color:#ffc4b8}
.hint{font-size:12px;color:#b4cabb}#source-info{overflow-wrap:anywhere}.busy{opacity:.5;pointer-events:none}#cut-preview{width:100%;max-width:256px;height:auto}.footer-section{margin-top:18px}
#chip-preview{width:100vw;height:100vh;max-width:none;max-height:none;margin:0;border:0;background:#101b16;color:#e5eee8;padding:16px;overflow:auto}#chip-preview::backdrop{background:#000c}
#preview-stage{position:relative;width:min(calc(100vh - 280px),94vw);margin:12px auto;cursor:crosshair}#preview-canvas{width:100%;height:auto;background:#000}#preview-overlays{position:absolute;inset:0;overflow:hidden;pointer-events:none}
#preview-overlays .tree-species-label{position:absolute;display:block;transform:translate(9px,-50%);max-width:170px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font:12px/1.2 system-ui;color:#efffed;text-shadow:0 1px 2px #000,1px 0 2px #000;background:#07110d70;border-radius:3px;padding:2px 4px}
.preview-center{position:absolute;width:5px;height:5px;border:1px solid #102016;background:#b0ffbd;border-radius:50%;transform:translate(-50%,-50%)}.preview-excluded{position:absolute;color:#ff7373;transform:translate(-50%,-50%);font-size:20px}
#preview-status{min-height:3em;white-space:pre-wrap;padding:14px;border:1px solid #526b59;border-radius:8px;background:#22372b;font-size:15px}#preview-status.loading{background:#3d3519;border-color:#ffda78;color:#ffe8a1;font-weight:600}#preview-status.loading:before{content:'';display:inline-block;width:18px;height:18px;border:3px solid #ffe8a155;border-top-color:#ffe8a1;border-radius:50%;animation:preview-spin 1s linear infinite;margin-right:12px;vertical-align:middle}#preview-status.error{color:#ffc4b8;border-color:#ed8e7d}
@keyframes preview-spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){#preview-status.loading:before{animation:none}}
#preview-source{padding:12px;background:#22372b;border-radius:8px;min-height:48px}#preview-source a{color:#b7ecff;font-weight:600;margin-left:14px}#source-picked{position:absolute;width:18px;height:18px;border:2px solid #fff;box-shadow:0 0 0 2px #152b21;transform:translate(-50%,-50%);border-radius:50%;pointer-events:none}
@media(max-width:1150px){.columns{grid-template-columns:220px 1fr}.assembly{grid-column:1/-1}#composite{width:400px}}@media(max-width:700px){.columns{display:block}section{margin-top:12px}}
</style></head><body><main>
<h1>Recombinant tiles <small>· Manual lab</small></h1>
<p>Curate a tile library for each source photograph, mark compatible edges, then generate reproducible chips for seam review.</p>
<div class="notice">Drafts only — no tree labels or training admission. Edge compatibility is a manual hint, not a guarantee of realistic seams. Validation/test and guard areas remain hidden.</div>
<div id="status" role="status" aria-live="polite">Loading source and saved component library…</div>
<div class="columns" id="workspace">
<section><h2>1 · Source photograph</h2><p id="source-info" class="hint"></p>
<label>Photo library <select id="photo"></select></label>
<canvas tabindex="0" id="source-map" width="230" height="300" aria-label="Full source footprint with selectable training windows"></canvas>
<p class="hint">Click an illuminated window. Dark areas are unavailable. The overview never renders held-out pixels.</p>
<label>Training window <select id="chip"></select></label><div class="row"><button id="previous">Previous</button><button id="next">Next</button></div>
<div class="row" id="pan-controls"><button id="pan-left" aria-label="Pan left">←</button><button id="pan-up" aria-label="Pan up">↑</button><button id="pan-down" aria-label="Pan down">↓</button><button id="pan-right" aria-label="Pan right">→</button></div><p class="hint">Pan the source view with these arrows or focus the image and use arrow keys: 32 px (9.6 m), Shift+arrow 128 px (38.4 m). Cuts can cross the original tile grid; the entire view must remain within guarded training coverage. Whole-square selections stay screen-centered; custom outlines stay on their source pixels while visible.</p>
<label>Display brightness <input id="brightness" type="range" min="70" max="180" value="100"></label>
<label>Display contrast <input id="contrast" type="range" min="70" max="160" value="100"></label>
<p class="hint">Display adjustments never change saved RGB+NIR.</p></section>
<section><h2>2 · Draw a component</h2>
<div class="row"><label>Square preset <select id="size"><option value="custom">Custom rectangle</option><option value="16">16 px · 4.8 m · universal background filler</option><option value="32">32 px · 9.6 m</option><option value="64">64 px · 19.2 m</option><option value="128">128 px · 38.4 m</option><option value="256" selected>256 px · 76.8 m</option><option value="512">512 px · 153.6 m</option></select></label>
<label>Width <input id="tile-width" type="number" min="16" max="512" step="16" value="256"></label><label>Height <input id="tile-height" type="number" min="16" max="512" step="16" value="256"></label><button id="move-square" aria-pressed="true">Position box</button><button id="draw-cut" aria-pressed="false">Draw cut outline</button></div>
<p id="instruction" class="hint">Click as often as needed to position the box. The whole rectangle is selected by default; Draw cut outline is optional.</p>
<div class="canvas-scroll"><canvas tabindex="0" id="editor" width="512" height="512" aria-label="Source tile and manual cut outline"></canvas></div>
<div class="row"><button id="undo">Undo point</button><button id="close-cut">Close outline</button><button id="whole-square">Keep whole rectangle</button><button id="clear-cut">Clear cut</button></div>
<p class="hint">In Position box mode, drag a corner handle to resize; snaps to 16px increments, width and height independently. Name, tag, mark edges and save in the sidebar. Cutting is optional: click vertices, then Close outline. Saved pixels preserve all four original bands.</p>
</section>
<section class="assembly"><h2>3 · Save / edit tile</h2>
<p id="tile-selection">New tile · whole rectangle selected</p><button id="new-tile">New tile</button>
<canvas id="cut-preview" width="256" height="256" class="checker" aria-label="Current tile selection preview"></canvas>
<fieldset id="tile-fields" disabled><label>Tile name <input id="tile-name" maxlength="100" placeholder="Large maple + open lawn"></label><label>Tags <input id="tile-tags" maxlength="300" placeholder="large-tree, field, isolated"></label><button id="tag-background">Add background tag</button>
<div id="edge-fields"></div><button id="copy-edges">Use top-edge marking on all sides</button>
<p class="hint">Saving marks this tile ready for random generation. Keep relevant objects and their shadows inside its borders; no extra checkboxes.</p><button id="save-tile" disabled>Save tile</button></fieldset>
<p class="hint">First, keep objects and their shadows wholly inside the tile; otherwise recut it. Then mark each entire edge's background material. Unknown/mixed materials: choose Blocked. Matching material is required at internal seams; a blocked material edge can face the chip boundary, but clipped objects/shadows are never allowed.</p>
<p class="hint">For background-only ground, use the background tag and Keep whole rectangle. Irregular cuts get their own mixed-size background packing from this photo, combining compatible sizes and textures. A 16×16px background can fill leftover grid gaps. Internal backing seams use the selected blending; saved tiles stay unchanged.</p></section></div>
<section class="footer-section"><div class="row"><h2>Tile library · this photograph</h2><button id="reload-library">Reload library</button></div>
<p class="hint">Select to edit in the sidebar. Repositioning or recutting a saved tile creates a new tile, preserving its original and generated recipes. Saved independently of models. Local only—not included in the annotation backup job yet.</p><div id="library"></div></section>
<section class="footer-section" id="generator"><div><h2>Generated chip · seam review</h2><button id="open-preview">Fullscreen + current annotations</button><canvas id="composite" width="512" height="512" aria-label="Generated chip for seam review"></canvas><label><input type="checkbox" id="show-seams" checked> Show tile boundaries</label><label><input type="checkbox" id="show-unblended"> Compare: show unblended chip</label><p id="coverage" class="hint">Generate a chip from enabled tiles.</p></div>
<div><label>Packing <select id="layout-size"><option value="mixed" selected>Mixed rectangles · 16px grid</option><option value="64">64 px · 8 × 8</option><option value="128">128 px · 4 × 4</option><option value="256">256 px · 2 × 2</option><option value="512">512 px · 1 × 1</option></select></label>
<label>Seed <input id="seed" type="number" min="0" max="2147483647" step="1" value="20260820"></label>
<label>Edge blending <select id="blend-width"><option value="0">Off · original hard cuts</option><option value="2">Light · 2 px (0.6 m)</option><option value="4" selected>Medium · 4 px (1.2 m)</option><option value="8">Wide · 8 px (2.4 m)</option></select></label><p class="hint">For the next generated chip: feather inside cutout borders and on both sides of tile joins. Keep trees and shadows outside this entire band. Blending can soften detail; it cannot repair incompatible materials. Interiors stay unchanged; all four bands use the same weights. Compare against the saved unblended chip before accepting.</p>
<div class="row"><button id="generate">Generate and save chip</button><button id="new-seed">New seed</button></div>
<p class="hint" id="saved-library-hint">Generation uses saved library tiles only. Unsaved drawings and tile edits stay in the editor and are not included; you do not need to discard them to generate.</p>
<p class="hint">Mixed-size rectangles fill a 512px chip from this photograph. The seed varies the scan corner/direction; area-weighted choices discourage repeated use of the same tile. Backgrounds beneath irregular cuts use a nested mixed-size packer. Uniform square modes remain available. Same seed + same inputs/settings reproduces the chip under this algorithm version. Add compatible 16×16px backgrounds to fill gaps. No rotation, cropping, stretching or relaxed edge rules.</p>
<label>Saved generated chips <select id="generated-list"><option value="">Choose a generated chip…</option></select></label>
<fieldset id="review-fields" disabled><label>Seam review <select id="seam-status"><option value="unreviewed">Unreviewed</option><option value="seams-accepted">Seams accepted — still unlabeled</option><option value="rejected">Reject</option></select></label><label>Review notes <textarea id="review-notes" maxlength="2000"></textarea></label><button id="save-review">Save seam review</button></fieldset>
<p class="hint">Both generated and unblended RGB+NIR chips, blend settings, and frozen tile recipe are saved, not just this RGB view. No geographic position is assigned to a recombined chip. Seam acceptance does not create tree labels or approve training.</p></div></section>
</main>
<dialog id="chip-preview" aria-labelledby="preview-title"><div class="row"><h2 id="preview-title">Generated chip · current saved annotations</h2><button id="close-preview">Close (Esc)</button><button id="refresh-annotations">Refresh annotations</button></div>
<div class="row"><label><input id="preview-annotations" type="checkbox" checked> Tree annotations</label><label><input id="preview-names" type="checkbox" checked> Species names</label><label><input id="preview-excluded" type="checkbox"> Excluded centers</label><label><input id="preview-seams" type="checkbox"> Foreground + background seams</label><label><input id="preview-raw" type="checkbox"> Unblended</label></div>
<div id="preview-status" role="status" aria-live="polite"></div>
<div id="preview-source" role="status"><span id="source-description">Click anywhere on the chip to find its original curation location—even where annotations are missing.</span><a id="source-curate" hidden target="_blank" rel="noopener">Open source for curation ↗</a></div>
<div id="preview-stage"><canvas id="preview-canvas" width="512" height="512" aria-label="Fullscreen synthetic chip; click a patch to locate its source"></canvas><div id="preview-overlays"></div><i id="source-picked" hidden></i></div>
<p class="hint">Source curation opens in a new tab. Save corrections there, then use Refresh annotations here. At blended edges, navigation follows the original cut mask, not the blended colors.</p>
<p class="hint">Latest saved curation, not model predictions or unsaved browser edits. Green: crown contained; amber: boundary needs review; red crosses: excluded centers. Crown outlines show annotated radii, not segmentation. This diagnostic overlay does not admit a chip to training.</p></dialog><script>
(() => {
const $=id=>document.getElementById(id), base='/api/recombinant', sourceId=new URLSearchParams(location.search).get('source');
function endpoint(path=''){return base+path+(path.includes('?')?'&':'?')+(sourceId?'source='+encodeURIComponent(sourceId):'');}
let data=null, chip=null, sourceImage=null, overview=null, points=[], closed=true, whole=true, mode='square', square={x:128,y:128,width:256,height:256};
let selected=null, images=new Map(), dirty=false, metadataDirty=false, reviewDirty=false, serial=0, saving=false, generatedId=null, generatedImage=null, rawImage=null, generationTicket=0, resizing=null, suppressClick=false;
let annotationResult=null,annotationTicket=0,annotationAbort=null,sourceTicket=0;
const editor=$('editor'), ctx=editor.getContext('2d'), composite=$('composite'), cc=composite.getContext('2d');
function status(text,error=false){$('status').textContent=text;$('status').classList.toggle('error',error)}
function busy(value){saving=value;$('workspace').inert=value;$('library').inert=value;$('generator').inert=value;$('reload-library').disabled=value;draw()}
async function api(path='',options={}){const r=await fetch(endpoint(path),options);const value=await r.json();if(!r.ok)throw Error(value.error||'Request failed');return value}
function loadImage(url){return new Promise((resolve,reject)=>{const image=new Image();image.onload=()=>resolve(image);image.onerror=()=>reject(Error('Image could not be loaded. Retry the window.'));image.src=url})}
function coords(event,canvas){const r=canvas.getBoundingClientRect();return [(event.clientX-r.left)*canvas.width/r.width,(event.clientY-r.top)*canvas.height/r.height]}
function tileDims(v){return {width:v.width??v.size,height:v.height??v.size}}
function syncDimensions(){$('tile-width').value=square.width;$('tile-height').value=square.height;$('size').value=square.width===square.height&&[16,32,64,128,256,512].includes(square.width)?String(square.width):'custom'}
function resetCut(){const {x,y,width:w,height:h}=square;points=[[x,y],[x+w,y],[x+w,y+h],[x,y+h]];closed=true;whole=true;syncDimensions();draw()}
function canLeave(){return !(dirty||metadataDirty)||confirm('Discard unsaved tile changes? Saved tiles are safe.')}
function clearFields(){selected=null;$('tile-name').value='';$('tile-tags').value='';for(const side of sides)$('edge-'+side).value='unknown';dirty=metadataDirty=false;}
function forkGeometry(){selected=null;dirty=true;$('library').querySelectorAll('.piece').forEach(b=>b.classList.remove('selected'));}
function setMode(value){mode=value;$('move-square').setAttribute('aria-pressed',String(mode==='square'));$('draw-cut').setAttribute('aria-pressed',String(mode==='cut'));
 $('instruction').textContent=mode==='square'?'Click as often as needed to position the box. The whole rectangle is selected by default. Draw cut outline is optional.':'Click boundary vertices, then Close outline. The closing edge is also a cut line.';draw();}
function filter(){return `brightness(${$('brightness').value}%) contrast(${$('contrast').value}%)`}
function draw(){ctx.clearRect(0,0,512,512);if(sourceImage){ctx.save();ctx.filter=filter();ctx.drawImage(sourceImage,0,0);ctx.restore()}
 const {x,y,width:w,height:h}=square;ctx.fillStyle='#0007';ctx.fillRect(0,0,512,y);ctx.fillRect(0,y+h,512,512-y-h);ctx.fillRect(0,y,x,h);ctx.fillRect(x+w,y,512-x-w,h);
 ctx.lineWidth=2;ctx.strokeStyle='#ffe38e';ctx.strokeRect(x,y,w,h);ctx.setLineDash([]);
 if(points.length){ctx.beginPath();points.forEach(([a,b],i)=>i?ctx.lineTo(a,b):ctx.moveTo(a,b));if(closed)ctx.closePath();ctx.strokeStyle='#7dfbdd';ctx.stroke();if(closed){ctx.fillStyle='#4ae4be22';ctx.fill()}
 for(const [a,b] of points){ctx.fillStyle='#fff';ctx.fillRect(a-2,b-2,4,4)}}
 if(sourceImage&&mode==='square'){for(const [a,b] of [[x,y],[x+w,y],[x+w,y+h],[x,y+h]]){ctx.fillStyle='#ffe38e';ctx.fillRect(a-5,b-5,10,10);ctx.strokeStyle='#15211a';ctx.strokeRect(a-5,b-5,10,10)}}
 $('tile-fields').disabled=saving||!sourceImage;
 $('save-tile').disabled=saving||!sourceImage||!closed||!$('tile-name').value.trim();
 $('save-tile').textContent=selected?'Save changes':'Save tile';
 $('tile-selection').textContent=(selected?'Editing saved tile':'New tile')+' · '+w+' × '+h+'px · '+(whole?(w===h?'whole square':'whole rectangle'):closed?'custom cut':'cut outline not closed');
 const p=$('cut-preview');p.width=w;p.height=h;const pc=p.getContext('2d');
 if(closed&&sourceImage){pc.save();pc.beginPath();points.forEach(([a,b],i)=>i?pc.lineTo(a-x,b-y):pc.moveTo(a-x,b-y));pc.closePath();pc.clip();pc.drawImage(sourceImage,x,y,w,h,0,0,w,h);pc.restore()}
}
function drawMap(){if(!data||!overview)return;const c=$('source-map');c.width=overview.width;c.height=overview.height;const m=c.getContext('2d');m.drawImage(overview,0,0);
 if(chip){m.strokeStyle='#ffe38e';m.lineWidth=3;m.strokeRect(chip.x*c.width/data.width,chip.y*c.height/data.height,512*c.width/data.width,512*c.height/data.height)}}
function viewEntry(id){const listed=data.windows.find(v=>v.id===id);if(listed)return listed;const match=/^pan_(\d{1,8})_(\d{1,8})$/.exec(id);if(match)return {id,x:+match[1],y:+match[2],size:512};throw Error('Unknown source view')}
function syncView(){for(const option of [...$('chip').options])if(option.dataset.panned)option.remove();if(chip.id.startsWith('pan_')){const option=new Option('Panned view · x '+chip.x+' / y '+chip.y,chip.id);option.dataset.panned='true';$('chip').add(option)}$('chip').value=chip.id;drawMap()}
async function windowImage(id){const response=await fetch(endpoint('/image?chip='+encodeURIComponent(id)));if(!response.ok){const value=await response.json();throw Error(value.error||'Source view unavailable')}const url=URL.createObjectURL(await response.blob());try{return await loadImage(url)}finally{URL.revokeObjectURL(url)}}
async function choose(id,force=false){if(saving)return false;if(!force&&!canLeave()){syncView();return false}const ticket=++serial;busy(true);status('Loading native RGB window…');
 try{const next=viewEntry(id),image=await windowImage(id);if(ticket!==serial)return false;chip=next;sourceImage=image;clearFields();resetCut();setMode('square');syncView();draw();status(`${id} · 512 × 512 native pixels · guarded TRAIN · component labels still require review`);return true}catch(e){if(chip)syncView();status(e.message,true);return false}finally{busy(false)}}
async function pan(dx,dy){if(saving||resizing||!sourceImage||!chip)return;const x=chip.x+dx,y=chip.y+dy;if(x<0||y<0||x+512>data.width||y+512>data.height){status('Pan reaches the source photograph boundary; current view retained.',true);return}
 const keepCut=!whole&&square.x-dx>=0&&square.y-dy>=0&&square.x-dx+square.width<=512&&square.y-dy+square.height<=512;
 if(!whole&&!keepCut&&!confirm('This pan would move your custom cut outside the view. Replace it with a whole rectangle? Name, tags and edge markings will be kept.'))return;
 busy(true);status('Panning source image…');
 try{const next=viewEntry('pan_'+x+'_'+y),image=await windowImage(next.id);chip=next;sourceImage=image;forkGeometry();
 if(keepCut){square={...square,x:square.x-dx,y:square.y-dy};points=points.map(([a,b])=>[a-dx,b-dy])}else{resetCut();setMode('square')}
 syncView();draw();status('Panned view · x '+x+' / y '+y+' · guarded TRAIN. Original source pixels; no resampling.');
 }catch(e){status(e.message,true)}finally{busy(false);editor.focus({preventScroll:true})}}
for(const [id,dx,dy] of [['pan-left',-32,0],['pan-right',32,0],['pan-up',0,-32],['pan-down',0,32]])$(id).onclick=()=>pan(dx,dy);
$('workspace').addEventListener('keydown',e=>{if(e.target.closest('input,textarea,select,[contenteditable]')||e.ctrlKey||e.metaKey||e.altKey)return;const directions={ArrowLeft:[-1,0],ArrowRight:[1,0],ArrowUp:[0,-1],ArrowDown:[0,1]},d=directions[e.key];if(!d)return;e.preventDefault();const step=e.shiftKey?128:32;pan(d[0]*step,d[1]*step)});
function cornerAt(event){if(mode!=='square'||!sourceImage)return null;const [a,b]=coords(event,editor),{x,y,width:w,height:h}=square;
 return [[x,y,-1,-1],[x+w,y,1,-1],[x+w,y+h,1,1],[x,y+h,-1,1]].find(([cx,cy])=>Math.abs(a-cx)<=10&&Math.abs(b-cy)<=10)}
editor.addEventListener('pointerdown',e=>{if(saving||e.button!==0)return;const hit=cornerAt(e);if(!hit)return;
 suppressClick=true;if(!whole&&!confirm('Resizing replaces the custom cut with a whole rectangle. Keep the name, tags and edge markings?'))return;
 e.preventDefault();editor.focus({preventScroll:true});const [cx,cy,sx,sy]=hit;
 resizing={pointer:e.pointerId,sx,sy,ax:cx-sx*square.width,ay:cy-sy*square.height,square:{...square},points:points.map(p=>[...p]),whole,closed,selected,dirty,metadataDirty};
 editor.setPointerCapture(e.pointerId);
});
editor.addEventListener('pointermove',e=>{if(!resizing){const hit=cornerAt(e);editor.style.cursor=hit?(hit[2]===hit[3]?'nwse-resize':'nesw-resize'):'crosshair';return}
 const [x,y]=coords(e,editor),r=resizing;
 const snap=(distance,maximum)=>Math.max(16,Math.min(Math.floor(maximum/16)*16,Math.round(distance/16)*16));
 const width=snap((x-r.ax)*r.sx,r.sx>0?512-r.ax:r.ax),height=snap((y-r.ay)*r.sy,r.sy>0?512-r.ay:r.ay);
 forkGeometry();square={x:r.sx>0?r.ax:r.ax-width,y:r.sy>0?r.ay:r.ay-height,width,height};resetCut();
 status('Resizing rectangle · '+width+' × '+height+' px · '+(width*.3).toFixed(1)+' × '+(height*.3).toFixed(1)+' m. Snapped to 16px.');

});
editor.addEventListener('pointerup',e=>{if(!resizing)return;resizing=null;if(editor.hasPointerCapture(e.pointerId))editor.releasePointerCapture(e.pointerId)});
editor.addEventListener('pointercancel',e=>{if(!resizing)return;const r=resizing;resizing=null;square=r.square;points=r.points;whole=r.whole;closed=r.closed;selected=r.selected;dirty=r.dirty;metadataDirty=r.metadataDirty;syncDimensions();highlightTile();draw()});
editor.addEventListener('click',e=>{if(suppressClick){suppressClick=false;return}if(!sourceImage||saving)return;editor.focus({preventScroll:true});const [a,b]=coords(e,editor),{width,height}=square;
 if(mode==='square'){if(!whole&&dirty&&!confirm('Replace this custom cut with a whole rectangle?'))return;forkGeometry();square={x:Math.max(0,Math.min(512-width,Math.round(a-width/2))),y:Math.max(0,Math.min(512-height,Math.round(b-height/2))),width,height};resetCut();return}
 if(closed){status('Clear the cut or Undo a point to edit the outline.',true);return}
 if(a<square.x||b<square.y||a>square.x+width||b>square.y+height){status('Cut points must stay inside the yellow box.',true);return}
 if(points.length>=100){status('Maximum 100 vertices.',true);return}points.push([Math.round(a),Math.round(b)]);dirty=true;draw();});
$('move-square').onclick=()=>setMode('square');
$('draw-cut').onclick=()=>{if(whole){forkGeometry();points=[];closed=false;whole=false;draw()}setMode('cut')};
$('undo').onclick=()=>{if(whole)return;forkGeometry();closed=false;points.pop();draw()};
$('close-cut').onclick=()=>{if(points.length<3){status('Add at least three points.',true);return}closed=true;dirty=true;draw()};
$('whole-square').onclick=()=>{forkGeometry();resetCut();setMode('square')};
$('clear-cut').onclick=()=>{if(whole||confirm('Remove the custom cut and keep the whole rectangle?')){forkGeometry();resetCut();setMode('square')}};
function resizeInputs(width,height){if(!Number.isInteger(width)||!Number.isInteger(height)||width<16||height<16||width>512||height>512||width%16||height%16){status('Width and height must be 16–512px in 16px increments.',true);syncDimensions();return}if(!whole&&!confirm('Replace this custom cut with a resized rectangle?')){syncDimensions();return}forkGeometry();square={x:Math.min(square.x,512-width),y:Math.min(square.y,512-height),width,height};resetCut();setMode('square')}
$('size').onchange=()=>{if($('size').value!=='custom')resizeInputs(+$('size').value,+$('size').value)};
for(const id of ['tile-width','tile-height'])$(id).onchange=()=>resizeInputs(+$('tile-width').value,+$('tile-height').value);

$('new-tile').onclick=()=>{if(!canLeave())return;clearFields();resetCut();setMode('square');$('library').querySelectorAll('.piece').forEach(b=>b.classList.remove('selected'))};
for(const id of ['brightness','contrast'])$(id).oninput=()=>draw();
$('chip').onchange=()=>choose($('chip').value);
for(const [id,d] of [['previous',-1],['next',1]])$(id).onclick=()=>{if(!data)return;const i=data.windows.findIndex(v=>v.id===chip?.id);choose(data.windows[(i+d+data.windows.length)%data.windows.length].id)};
$('source-map').onclick=e=>{if(!data)return;const [x,y]=coords(e,$('source-map'));const px=x*data.width/$('source-map').width,py=y*data.height/$('source-map').height;const w=data.windows.find(v=>px>=v.x&&px<v.x+512&&py>=v.y&&py<v.y+512);if(w)choose(w.id);else status('That area is unavailable. Choose an illuminated training window.');};
async function updateLibrary(value){data={...data,...value};const container=$('library');container.replaceChildren();
 for(const piece of data.components){let image=images.get(piece.id);if(!image){image=await loadImage(endpoint('/component?id='+encodeURIComponent(piece.id)));images.set(piece.id,image)}
 const button=document.createElement('button');button.className='piece'+(selected===piece.id?' selected':'');button.dataset.id=piece.id;
 const thumb=image.cloneNode();thumb.className='checker';thumb.alt=piece.name;button.append(thumb);
 const label=document.createElement('span');label.textContent=piece.name;button.append(label);const small=document.createElement('small');small.textContent=`${tileDims(piece).width} × ${tileDims(piece).height}px · ${piece.tags||'untagged'} · ${piece.enabled?'enabled':'not enabled'}`;button.append(small);
 button.onclick=()=>selectTile(piece.id);
 container.append(button)}
 if(!data.components.length)container.textContent='No saved tiles yet. Position a box, then name, mark edges, and save it here.';
 const list=$('generated-list');list.replaceChildren(new Option('Choose a generated chip…',''));for(const v of [...(data.generated||[])].reverse())list.add(new Option(`${v.review_status} · ${v.packing==='mixed-rect-v1'?'mixed rectangles':v.size+'px'} · seed ${v.seed} · ${v.id.slice(0,8)}`,v.id));list.value=generatedId||'';}
async function persist(payload){return api('',{method:'PUT',headers:{'Content-Type':'application/json','X-Studio-Recombinant':'1'},body:JSON.stringify({...payload,revision:data.revision})})}
const sides=['top','right','bottom','left'];
function readEdges(){return Object.fromEntries(sides.map(side=>[side,{surface:$('edge-'+side).value}]))}
function setupEdges(){for(const side of sides){const row=document.createElement('div');row.className='edge-row';const title=document.createElement('strong');title.textContent=side[0].toUpperCase()+side.slice(1);row.append(title);
 const label=document.createElement('label'),select=document.createElement('select');select.id='edge-'+side;select.setAttribute('aria-label',side+' material');for(const v of data.edge_surfaces)select.add(new Option(v,v));select.onchange=()=>metadataDirty=true;label.append(select);row.append(label);$('edge-fields').append(row)}}
function highlightTile(){ $('library').querySelectorAll('.piece').forEach(b=>b.classList.toggle('selected',b.dataset.id===selected)); }
async function selectTile(id){if(saving||!canLeave())return;const tile=data.components.find(v=>v.id===id);if(!tile)return;
 if(!await choose(tile.chip,true))return;
 selected=id;square={x:tile.x,y:tile.y,...tileDims(tile)};syncDimensions();points=tile.polygon.map(p=>[...p]);closed=true;whole=tile.kept_pixels===square.width*square.height;
 $('tile-name').value=tile.name;$('tile-tags').value=tile.tags;
 for(const side of sides)$('edge-'+side).value=tile.edges?.[side]?.surface||'unknown';
 dirty=metadataDirty=false;setMode('square');highlightTile();draw();
}
for(const id of ['tile-name','tile-tags'])$(id).oninput=()=>{metadataDirty=true;draw()};
$('tag-background').onclick=()=>{const tags=$('tile-tags').value.split(/[,;]/).map(v=>v.trim()).filter(Boolean);if(!tags.some(v=>v.toLowerCase()==='background'))tags.push('background');$('tile-tags').value=tags.join(', ');metadataDirty=true;draw();if(!whole)status('Background tiles must keep the whole rectangle. Clear the cut before saving this as a background.',true)};
$('copy-edges').onclick=()=>{for(const side of sides)$('edge-'+side).value=$('edge-top').value;metadataDirty=true};
$('save-tile').onclick=async()=>{if(saving||!sourceImage||!closed||!$('tile-name').value.trim())return;
 const editing=selected;const payload={name:$('tile-name').value,tags:$('tile-tags').value,enabled:true,borders_clear:true,edges:readEdges()};
 busy(true);status('Saving tile and original four-band pixels…');
 try{const value=await persist(editing?{...payload,action:'tile-metadata',id:editing}:{...payload,action:'component',chip:chip.id,...square,polygon:points});
 selected=editing||value.components.at(-1).id;dirty=metadataDirty=false;await updateLibrary(value);highlightTile();
 status('Tile saved. Included in random generation; backgrounds are mixed-size packed under irregular cuts (16×16 fills remaining gaps). Existing generated recipes are unchanged.');
 }catch(e){status(e.message+' Your tile changes are retained.',true)}finally{busy(false)}
};
function drawComposite(){cc.clearRect(0,0,512,512);if(!generatedImage)return;cc.drawImage($('show-unblended').checked?rawImage:generatedImage,0,0);const chip=data.generated.find(v=>v.id===generatedId);if($('show-seams').checked){cc.strokeStyle='#ffe38e';cc.lineWidth=1;if(chip.placements?.every(p=>Number.isInteger(p.x)&&Number.isInteger(p.y))){for(const p of chip.placements)cc.strokeRect(p.x,p.y,p.width,p.height)}else if(chip.size){for(let a=chip.size;a<512;a+=chip.size){cc.beginPath();cc.moveTo(a,0);cc.lineTo(a,512);cc.moveTo(0,a);cc.lineTo(512,a);cc.stroke()}}}}
async function selectGenerated(id,force=false){if(!force&&reviewDirty&&!confirm('Discard unsaved seam review?')){$('generated-list').value=generatedId||'';return}const chip=data.generated?.find(v=>v.id===id);if(!chip)return;const ticket=++generationTicket;generatedId=id;generatedImage=rawImage=null;reviewDirty=false;$('generated-list').value=id;$('review-fields').disabled=true;drawComposite();
 try{const [image,raw]=await Promise.all([loadImage(endpoint('/generated?id='+encodeURIComponent(id))),loadImage(endpoint('/generated?id='+encodeURIComponent(id)+'&variant=unblended'))]);if(ticket!==generationTicket)return;generatedImage=image;rawImage=raw;$('review-fields').disabled=false;$('seam-status').value=chip.review_status;$('review-notes').value=chip.review_notes;$('coverage').textContent=chip.unique_tiles+' distinct tiles · '+chip.repeated_slots+' repeated placements · '+(chip.background_overlays||0)+' background overlays · seed '+chip.seed+' · blend '+(chip.blending?.width_px||0)+' px · RGB+NIR and unblended reference saved. Review seams before accepting.';drawComposite()}catch(e){status(e.message,true)}}
$('show-seams').onchange=drawComposite;
function drawPreview(){const c=$('preview-canvas').getContext('2d');c.clearRect(0,0,512,512);if(!generatedImage)return;c.drawImage($('preview-raw').checked?rawImage:generatedImage,0,0);
 const chip=data.generated.find(v=>v.id===generatedId);
 if($('preview-seams').checked){c.lineWidth=.6;for(const p of chip.placements||[]){c.strokeStyle='#ffe38e';c.strokeRect(p.x,p.y,p.width,p.height);c.strokeStyle='#bcbcff';c.setLineDash([2,2]);for(const b of p.background_placements||[])c.strokeRect(p.x+b.x,p.y+b.y,b.width,b.height);c.setLineDash([])}}
 const overlay=$('preview-overlays');overlay.replaceChildren();
 if($('preview-annotations').checked){for(const t of annotationResult?.trees||[]){const {crown,label}=window.createTreeOverlay(t,t.x,t.y,512,512);crown.style.borderColor=t.boundary_check==='contained'?'#91f6ab':'#ffbe59';const diameter=2*t.crown_radius_m/.3/512*100;if(Number.isFinite(diameter)&&diameter>0){crown.style.width=crown.style.height=diameter+'%';overlay.append(crown)}const center=document.createElement('i');center.className='preview-center';center.style.left=100*t.x/512+'%';center.style.top=100*t.y/512+'%';overlay.append(center);if($('preview-names').checked)overlay.append(label)}}
 if($('preview-excluded').checked){for(const t of annotationResult?.warnings||[]){if(!Number.isFinite(t.x)||!Number.isFinite(t.y))continue;const mark=document.createElement('span');mark.className='preview-excluded';mark.textContent='×';mark.style.left=100*t.x/512+'%';mark.style.top=100*t.y/512+'%';overlay.append(mark)}}}
async function refreshAnnotations(){annotationAbort?.abort();annotationAbort=new AbortController();const ticket=++annotationTicket,id=generatedId;annotationResult=null;drawPreview();$('preview-status').classList.remove('error');$('preview-status').classList.add('loading');$('preview-overlays').setAttribute('aria-busy','true');$('preview-status').textContent='Loading current saved annotations… Large inventories may take tens of seconds. You can already click a patch to open its source.';$('refresh-annotations').disabled=true;
 try{const result=await api('/annotations?id='+encodeURIComponent(id),{signal:annotationAbort.signal});if(ticket!==annotationTicket||id!==generatedId)return;annotationResult=result;const flagged=result.trees.filter(t=>t.boundary_check!=='contained').length;$('preview-status').textContent=`${result.trees.length} mapped tree annotations · ${flagged} boundary checks · ${result.warnings.length} excluded centers · saved revision ${result.saved_reviews_sha256.slice(0,10)}. Diagnostic only.`;drawPreview()}
 catch(e){if(ticket!==annotationTicket||e.name==='AbortError')return;$('preview-status').classList.add('error');$('preview-status').textContent='Annotations unavailable: '+e.message+' The chip image is still available; use Refresh annotations to retry.'}
 finally{if(ticket===annotationTicket){$('refresh-annotations').disabled=false;$('preview-status').classList.remove('loading');$('preview-overlays').setAttribute('aria-busy','false')}}}
$('open-preview').onclick=()=>{if(!generatedImage){status('Choose or generate a chip before opening the fullscreen preview.',true);return}$('preview-raw').checked=$('show-unblended').checked;annotationResult=null;$('chip-preview').showModal();drawPreview();refreshAnnotations()};
$('close-preview').onclick=()=>$('chip-preview').close();
$('chip-preview').addEventListener('close',()=>{annotationTicket++;sourceTicket++;annotationAbort?.abort();$('refresh-annotations').disabled=false;$('preview-status').classList.remove('loading');$('source-curate').hidden=true;$('source-picked').hidden=true;$('source-description').textContent='Click anywhere on the chip to find its original curation location—even where annotations are missing.'});
$('preview-canvas').onclick=async event=>{const [x,y]=coords(event,$('preview-canvas')),ticket=++sourceTicket,id=generatedId;$('source-curate').hidden=true;$('source-description').textContent='Locating original source patch and latest compatible predictions…';$('source-picked').hidden=false;$('source-picked').style.left=x/512*100+'%';$('source-picked').style.top=y/512*100+'%';try{const target=await api('/source-point?id='+encodeURIComponent(id)+'&x='+x+'&y='+y);if(ticket!==sourceTicket||id!==generatedId)return;$('source-description').textContent=target.name+' · '+target.layer+' · source pixel '+Math.floor(target.source_x)+', '+Math.floor(target.source_y)+' · '+target.prediction_status;$('source-curate').href=target.url;$('source-curate').hidden=false}catch(e){if(ticket===sourceTicket)$('source-description').textContent='Source unavailable: '+e.message}};
$('refresh-annotations').onclick=refreshAnnotations;
for(const id of ['preview-annotations','preview-names','preview-excluded','preview-seams','preview-raw'])$(id).onchange=drawPreview;
$('show-unblended').onchange=drawComposite;
$('generated-list').onchange=()=>selectGenerated($('generated-list').value);
for(const id of ['seam-status','review-notes'])$(id).oninput=()=>reviewDirty=true;
$('new-seed').onclick=()=>{$('seed').value=crypto.getRandomValues(new Uint32Array(1))[0]%2147483648};
$('generate').onclick=async()=>{if(saving)return;if(reviewDirty&&!confirm('Discard unsaved seam review?'))return;const draftPending=dirty||metadataDirty;busy(true);status('Finding compatible neighbors in the saved library and saving original-band chip…');try{const value=await persist({action:'generate',packing:$('layout-size').value==='mixed'?'mixed-rect-v1':'uniform',size:$('layout-size').value==='mixed'?null:+$('layout-size').value,seed:+$('seed').value,blend_width:+$('blend-width').value});await updateLibrary(value);await selectGenerated(value.generated.at(-1).id,true);status('Generated chip saved. Review its seams; it has no tree labels and is not admitted to training.'+(draftPending?' Unsaved tile changes are retained in the editor and were not included.':''))}catch(e){status(e.message,true)}finally{busy(false)}};
$('save-review').onclick=async()=>{if(saving||!generatedId)return;busy(true);try{const value=await persist({action:'review-generated',id:generatedId,review_status:$('seam-status').value,review_notes:$('review-notes').value});reviewDirty=false;await updateLibrary(value);status('Seam review saved. Training remains disabled.')}catch(e){status(e.message+' Your review is retained.',true)}finally{busy(false)}};
$('reload-library').onclick=async()=>{try{await updateLibrary(await api());status('Library reloaded; current drawing retained.')}catch(e){status(e.message,true)}};
window.addEventListener('beforeunload',e=>{if(dirty||metadataDirty||reviewDirty||saving){e.preventDefault();e.returnValue=''}});
$('photo').onchange=()=>{if((dirty||metadataDirty||reviewDirty)&&!confirm('Discard unsaved work and switch photo libraries?')){$('photo').value=data.source_id;return}dirty=metadataDirty=reviewDirty=false;location.href='/recombinant?city=usbos&source='+encodeURIComponent($('photo').value)};
async function init(){try{data=await api();$('source-info').textContent=`${data.source_id}\nAcquired ${data.acquisition.slice(0,10)} · ${data.windows.length} guarded training windows. Original source photograph; not the multi-photo mosaic.`;
 for(const v of data.sources)$('photo').add(new Option(`${v.source_id} · ${v.acquisition.slice(0,10)}`,v.source_id));$('photo').value=data.source_id;setupEdges();
 for(const v of data.windows)$('chip').add(new Option(v.id,v.id));overview=await loadImage(endpoint('/overview'));drawMap();await updateLibrary(data);
 await choose(data.windows.find(v=>v.id==='r000062_c000063')?.id||data.windows[0].id,true);
}catch(e){status(e.message,true)}}init();
})();</script></body></html>'''

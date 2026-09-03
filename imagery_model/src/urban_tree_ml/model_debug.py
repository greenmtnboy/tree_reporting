# The embedded, dependency-free browser UI is clearer when its HTML/JavaScript
# is not wrapped to Python's line-length limit.
# ruff: noqa: E501

from __future__ import annotations

import io
import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from urban_tree_ml.config import ProjectConfig
from urban_tree_ml.quality import _rgb_preview

_CHIP_ID = re.compile(r"^r(?P<row>\d{6})_c(?P<column>\d{6})$")


def _frame_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    return json.loads(frame.to_json(orient="records"))


def _training_curves(run_dir: Path) -> dict[str, list[dict[str, float | int]]]:
    event_paths = sorted(
        run_dir.glob("lightning_logs/**/events.out.tfevents.*"),
        key=lambda path: path.stat().st_mtime,
    )
    if not event_paths:
        return {}
    try:
        from tensorboard.backend.event_processing.event_accumulator import EventAccumulator
    except ImportError:  # pragma: no cover - train installs include tensorboard
        return {}
    accumulator = EventAccumulator(str(event_paths[-1])).Reload()
    wanted = {
        "train/loss_epoch",
        "validation/loss",
        "train/center_loss_epoch",
        "validation/center_loss",
        "train/dbh_loss_epoch",
        "validation/dbh_loss",
        "train/species_loss_epoch",
        "validation/species_loss",
    }
    return {
        tag: [
            {"step": int(item.step), "value": float(item.value)}
            for item in accumulator.Scalars(tag)
        ]
        for tag in accumulator.Tags().get("scalars", [])
        if tag in wanted
    }


class ModelDebugBundle:
    """Read-only view over one saved model evaluation for the local QA server."""

    def __init__(
        self,
        config: ProjectConfig,
        evaluation_dir: str | Path,
        raster_path: str | Path,
    ) -> None:
        self.config = config
        self.directory = Path(evaluation_dir).resolve()
        self.raster_path = Path(raster_path).resolve()
        required = {
            "metrics": self.directory / "metrics.json",
            "predictions": self.directory / "predictions.parquet",
            "ground truth": self.directory / "ground-truth.parquet",
            "matches": self.directory / "matches.parquet",
            "taxonomy": self.directory / "taxonomy.json",
        }
        missing = [name for name, path in required.items() if not path.exists()]
        if missing:
            raise FileNotFoundError(
                f"evaluation bundle is missing {', '.join(missing)} under {self.directory}"
            )
        self.metrics = json.loads(required["metrics"].read_text(encoding="utf-8"))
        self.taxonomy = json.loads(required["taxonomy"].read_text(encoding="utf-8"))
        self.predictions = pd.read_parquet(required["predictions"]).copy()
        self.predictions["prediction_id"] = self.predictions.index.astype(int)
        self.ground_truth = pd.read_parquet(required["ground truth"]).copy()
        self.matches = pd.read_parquet(required["matches"]).copy()
        self.run_dir = self.directory.parent.parent
        metadata_path = self.run_dir / "run-metadata.json"
        self.run_metadata = (
            json.loads(metadata_path.read_text(encoding="utf-8"))
            if metadata_path.exists()
            else None
        )
        self.chip_ids = sorted(
            set(self.predictions["chip_id"].astype(str))
            | set(self.ground_truth["chip_id"].astype(str))
        )
        self._image_cache: dict[str, bytes] = {}
        self._add_ground_truth_names()
        self.chips = self._chip_summaries()

    def _add_ground_truth_names(self) -> None:
        genera = self.taxonomy["genera"]
        species = self.taxonomy["species"]
        self.ground_truth["genus"] = self.ground_truth["genus_id"].map(
            lambda value: genera[int(value)] if pd.notna(value) else None
        )
        self.ground_truth["species"] = self.ground_truth["species_id"].map(
            lambda value: species[int(value)] if pd.notna(value) else None
        )
        self.ground_truth["dbh_in"] = self.ground_truth["dbh_log1p"].map(
            lambda value: float(np.expm1(value)) if pd.notna(value) else None
        )

    def _chip_summaries(self) -> list[dict[str, object]]:
        threshold = float(self.metrics["confidence_threshold"])
        radii = sorted(float(value) for value in self.matches["radius_m"].unique())
        primary_radius = radii[0] if radii else None
        predictions = self.predictions[self.predictions["score"] >= threshold]
        primary_matches = (
            self.matches[self.matches["radius_m"] == primary_radius]
            if primary_radius is not None
            else self.matches.iloc[0:0]
        )
        matched_prediction_ids = set(primary_matches["prediction_index"].astype(int))
        matched_tree_ids = set(primary_matches["tree_id"].astype(str))
        predictions_by_id = self.predictions.set_index("prediction_id", drop=False)
        truth_by_id = self.ground_truth.set_index("tree_id", drop=False)
        species_errors: dict[str, int] = {}
        for match in primary_matches.itertuples(index=False):
            prediction = predictions_by_id.loc[int(match.prediction_index)]
            truth = truth_by_id.loc[str(match.tree_id)]
            if pd.notna(truth.species_id) and int(prediction.species_id) != int(truth.species_id):
                chip_id = str(prediction.chip_id)
                species_errors[chip_id] = species_errors.get(chip_id, 0) + 1
        summaries: list[dict[str, object]] = []
        for chip_id in self.chip_ids:
            chip_predictions = predictions[predictions["chip_id"] == chip_id]
            chip_truth = self.ground_truth[self.ground_truth["chip_id"] == chip_id]
            matched_predictions = sum(
                int(value in matched_prediction_ids)
                for value in chip_predictions["prediction_id"]
            )
            matched_truth = sum(
                int(str(value) in matched_tree_ids) for value in chip_truth["tree_id"]
            )
            summaries.append(
                {
                    "chip_id": chip_id,
                    "ground_truth": len(chip_truth),
                    "predictions": len(chip_predictions),
                    "matched": matched_predictions,
                    "missed": len(chip_truth) - matched_truth,
                    "false_positive": len(chip_predictions) - matched_predictions,
                    "species_errors": species_errors.get(chip_id, 0),
                }
            )
        return summaries

    def summary(self) -> dict[str, object]:
        return {
            "metrics": self.metrics,
            "training_curves": _training_curves(self.run_dir),
            "run_metadata": self.run_metadata,
            "chips": self.chips,
            "display": {
                "chip_pixels": self.config.imagery.chip_pixels,
                "output_stride": self.config.targets.output_stride,
                "resolution_m": self.config.imagery.resolution_m,
            },
        }

    def chip(self, chip_id: str) -> dict[str, object]:
        if chip_id not in self.chip_ids:
            raise KeyError(f"unknown evaluation chip {chip_id!r}")
        predictions = self.predictions[self.predictions["chip_id"] == chip_id].copy()
        truth = self.ground_truth[self.ground_truth["chip_id"] == chip_id].copy()
        return {
            "chip_id": chip_id,
            "predictions": _frame_records(predictions),
            "ground_truth": _frame_records(truth),
        }

    def chip_image(self, chip_id: str) -> bytes:
        cached = self._image_cache.get(chip_id)
        if cached is not None:
            return cached
        match = _CHIP_ID.fullmatch(chip_id)
        if match is None or chip_id not in self.chip_ids:
            raise KeyError(f"unknown evaluation chip {chip_id!r}")
        try:
            import rasterio
            from PIL import Image
            from rasterio.windows import Window
        except ImportError as error:  # pragma: no cover
            raise RuntimeError("Install the imagery dependency group") from error
        row_offset = int(match.group("row")) * self.config.imagery.chip_pixels
        column_offset = int(match.group("column")) * self.config.imagery.chip_pixels
        window = Window(
            column_offset,
            row_offset,
            self.config.imagery.chip_pixels,
            self.config.imagery.chip_pixels,
        )
        with rasterio.open(self.raster_path) as source:
            raw = source.read(self.config.imagery.bands[:3], window=window)
        preview = Image.fromarray(_rgb_preview(raw, self.config.imagery.input_scale))
        output = io.BytesIO()
        preview.save(output, format="PNG", optimize=True)
        encoded = output.getvalue()
        if len(self._image_cache) >= 128:
            self._image_cache.pop(next(iter(self._image_cache)))
        self._image_cache[chip_id] = encoded
        return encoded


def render_studio_home(model_available: bool) -> str:
    status = "Validation artifacts loaded" if model_available else "No validation run loaded"
    return _STUDIO_HOME.replace("__MODEL_STATUS__", status)


def inject_studio_navigation(registration_html: str) -> str:
    navigation = """
<style>.studio-nav{display:flex;gap:8px;padding:10px 22px;background:#0b100d;border-bottom:1px solid #33453a}
.studio-nav a{color:#cce8d3;text-decoration:none;padding:5px 9px;border-radius:6px;background:#203027}</style>
<nav class="studio-nav"><a href="/">Studio</a><a href="/registration">Registration</a><a href="/model">Model validation</a></nav>
"""
    return registration_html.replace("<body>", f"<body>{navigation}", 1)


_STUDIO_HOME = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Urban Tree Model Studio</title><style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif}*{box-sizing:border-box}
body{margin:0;background:#0d1410;color:#edf6ef}main{max-width:1050px;margin:auto;padding:54px 24px}
h1{font-size:36px;margin:0 0 8px}.lede{color:#aabdaf;margin:0 0 34px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:18px}
.card{display:block;color:inherit;text-decoration:none;background:#17231c;border:1px solid #354b3c;border-radius:14px;padding:24px;min-height:180px}
.card:hover{border-color:#78a687;transform:translateY(-1px)}h2{margin:0 0 9px}.card p{color:#b8c9bd;line-height:1.5}.status{font-size:12px;color:#8fb49a;margin-top:22px;text-transform:uppercase;letter-spacing:.06em}
</style></head><body><main><h1>Urban Tree Model Studio</h1><p class="lede">Curate labels, inspect training behavior, and debug geospatial predictions.</p>
<div class="grid"><a class="card" href="/registration"><h2>Registration curation</h2><p>Review inventory-to-imagery alignment, exclude uncertain labels, and apply per-tree corrections.</p><div class="status">Saved feedback enabled</div></a>
<a class="card" href="/model"><h2>Model validation</h2><p>Explore loss curves, metrics, detections, misses, taxonomy errors, and DBH residuals on held-out blocks.</p><div class="status">__MODEL_STATUS__</div></a></div></main></body></html>"""


MODEL_DEBUG_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Model validation · Urban Tree Model Studio</title><style>
:root{color-scheme:dark;font-family:Inter,ui-sans-serif,system-ui,sans-serif}*{box-sizing:border-box}
body{margin:0;background:#0d1410;color:#edf6ef}.nav{display:flex;gap:8px;padding:10px 22px;background:#090e0b;border-bottom:1px solid #2d4034}.nav a{color:#cce8d3;text-decoration:none;padding:5px 9px;border-radius:6px;background:#1b2a21}
header{position:sticky;top:0;z-index:5;background:#142019ee;backdrop-filter:blur(12px);padding:18px 24px;border-bottom:1px solid #354b3c}h1{margin:0 0 5px;font-size:23px}.lede{margin:0;color:#aabdaf}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(135px,1fr));gap:10px;padding:18px 24px}.metric,.panel{background:#17231c;border:1px solid #304438;border-radius:10px;padding:13px}.metric strong{display:block;font-size:24px}.metric span{font-size:12px;color:#9eb2a4}.workspace{display:grid;grid-template-columns:minmax(300px,1fr) minmax(460px,2fr);gap:16px;padding:0 24px 24px}.panel h2{margin:0 0 12px;font-size:16px}.chart{width:100%;height:190px;background:#0e1712;border-radius:7px}.controls{display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin-top:12px}select,input{accent-color:#69be83;background:#203027;color:#edf6ef;border:1px solid #486151;border-radius:6px;padding:6px}.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(275px,1fr));gap:14px;padding:0 24px 30px}.card{background:#17231c;border:1px solid #304438;border-radius:10px;overflow:hidden}.card-head{display:flex;justify-content:space-between;padding:9px 11px;font-size:12px;color:#b9cabe}.image{position:relative;aspect-ratio:1;background:#050805;cursor:zoom-in}.image img{width:100%;height:100%;display:block}.marker{position:absolute;transform:translate(-50%,-50%);border-radius:50%;pointer-events:auto;cursor:help;z-index:3}.match-radius{position:absolute;transform:translate(-50%,-50%);border:1px dashed #d7e4da99;border-radius:50%;pointer-events:none;z-index:2}.truth{width:11px;height:11px;border:2px solid #35e5ee}.truth.missed{border-color:#ff5e6c;width:14px;height:14px}.prediction{width:8px;height:8px;background:#e850e8;border:1px solid #170817;z-index:4}.prediction.matched{background:#65e486}.prediction.wrong{background:#ffb44c}.facts{padding:9px 11px;color:#aebfb3;font-size:12px;line-height:1.5}.legend{display:flex;flex-wrap:wrap;gap:5px 12px;font-size:12px;color:#b6c8bb}.legend-item{display:inline-flex;align-items:center;gap:5px;cursor:help}.dot{display:inline-block;width:10px;height:10px;border-radius:50%}.cyan{border:2px solid #35e5ee}.green{background:#65e486}.orange{background:#ffb44c}.pink{background:#e850e8}.red{border:2px solid #ff5e6c}.halo{border:1px dashed #d7e4da}.empty{padding:50px;text-align:center;color:#afc1b4}.tooltip{position:fixed;display:none;z-index:30;max-width:280px;white-space:pre-line;padding:8px 10px;border-radius:7px;background:#060a08ee;border:1px solid #58705f;color:#eef7f0;font-size:12px;line-height:1.4;pointer-events:none;box-shadow:0 8px 28px #0009}.modal{position:fixed;inset:0;z-index:20;display:grid;place-items:center;padding:24px;background:#030604e8}.modal[hidden]{display:none}.modal-panel{width:min(92vw,900px);max-height:94vh;overflow:auto;background:#142019;border:1px solid #496252;border-radius:14px;box-shadow:0 20px 70px #000}.modal-head{display:flex;justify-content:space-between;align-items:center;padding:12px 15px}.modal-head h2{margin:0;font-size:17px}.modal-close{background:#26392d;color:#edf6ef;border:1px solid #56705e;border-radius:7px;padding:6px 10px;cursor:pointer}.modal .image{width:min(86vw,78vh,800px);margin:auto}.modal .match-radius{border-width:2px}.modal .prediction{width:10px;height:10px}.modal-note{padding:12px 16px 17px;color:#b9cabe}@media(max-width:850px){.workspace{grid-template-columns:1fr}.gallery{padding:0 10px}.metrics,header{padding-left:12px;padding-right:12px}}
</style></head><body><nav class="nav"><a href="/">Studio</a><a href="/registration">Registration</a><a href="/model">Model validation</a></nav>
<header><h1>Validation explorer</h1><p class="lede">Held-out validation blocks only · test remains sealed</p><div class="controls"><label>Radius <select id="radius" title="Maximum center-to-center distance for a spatial match"></select></label><label>Confidence <input id="threshold" type="range" min="0.05" max="0.75" step="0.01" title="Only predictions at or above this center confidence are shown"><span id="threshold-value"></span></label><label>Order <select id="sort"><option value="missed">Most missed</option><option value="false_positive">Most false positives</option><option value="species_errors">Most species errors</option><option value="matched">Most matches</option></select></label><label>Cards <select id="limit"><option>12</option><option selected>24</option><option>48</option><option>97</option></select></label><span class="legend"><span class="legend-item" title="Retained municipal inventory tree"><i class="dot cyan"></i>Inventory truth</span><span class="legend-item" title="Prediction within the selected radius and with the correct species"><i class="dot green"></i>Matched</span><span class="legend-item" title="Prediction within the selected radius but with a different species"><i class="dot orange"></i>Wrong species</span><span class="legend-item" title="Prediction with no unused inventory tree inside the selected radius"><i class="dot pink"></i>False positive</span><span class="legend-item" title="Inventory tree with no prediction inside the selected radius"><i class="dot red"></i>Missed</span><span class="legend-item" title="The selected 2 m or 4 m matching tolerance around each inventory point"><i class="dot halo"></i>Match radius</span></span></div></header>
<section id="metrics" class="metrics"></section><section class="workspace"><div class="panel"><h2>Training and validation loss</h2><svg id="curve" class="chart" viewBox="0 0 600 190"></svg></div><div class="panel"><h2>What this view answers</h2><p>Do detections land on inventory stems? Are failures spatial or taxonomic? Does DBH remain plausible? The dashed halo shows the selected center-matching tolerance. Click any chip for a larger inspection view.</p><p id="run-detail"></p></div></section><main id="gallery" class="gallery"></main>
<div id="modal" class="modal" hidden><section class="modal-panel"><div class="modal-head"><h2 id="modal-title"></h2><button id="modal-close" class="modal-close" type="button">Close</button></div><div id="modal-image" class="image"></div><div id="modal-note" class="modal-note"></div></section></div><div id="tooltip" class="tooltip" role="tooltip"></div>
<script>
let state;const chipCache=new Map(),$=id=>document.getElementById(id),pct=v=>v==null?'—':`${(100*v).toFixed(1)}%`,num=v=>v==null?'—':Number(v).toFixed(2),escapeHtml=value=>String(value).replace(/[&<>"']/g,char=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[char]));
function metric(label,value){return `<div class="metric"><strong>${value}</strong><span>${label}</span></div>`}
function showMetrics(){const radius=$('radius').value,m=state.metrics.metrics_by_match_radius_m[radius],d=m.detection,a=m.attributes_on_matched_detections,saved=Number(state.metrics.confidence_threshold).toFixed(2);$('metrics').innerHTML=metric(`precision @ ${saved}`,pct(d.precision))+metric(`recall @ ${saved}`,pct(d.recall))+metric(`F1 @ ${saved}`,pct(d.f1))+metric('average precision',pct(d.average_precision))+metric('species accuracy',pct(a.species?.accuracy))+metric('species macro-F1',pct(a.species?.macro_f1))+metric('DBH MAE',a.dbh?`${num(a.dbh.mae_in)} in`:'—')+metric('joint recall',pct(a.joint?.recall));}
function chart(){const svg=$('curve'),series=[['train/loss_epoch','#68db8b'],['validation/loss','#ffbc5b']];let values=series.flatMap(([tag])=>(state.training_curves[tag]||[]).map(x=>x.value));if(!values.length){svg.innerHTML='<text x="20" y="95" fill="#9eb2a4">No TensorBoard event was included</text>';return}const lo=Math.min(...values),hi=Math.max(...values),x=(i,n)=>35+(n<2?0:i/(n-1)*530),y=v=>155-(v-lo)/(hi-lo||1)*120;svg.innerHTML='<line x1="35" y1="155" x2="565" y2="155" stroke="#496052"/>'+series.map(([tag,color],j)=>{const a=state.training_curves[tag]||[],points=a.map((p,i)=>`${x(i,a.length)},${y(p.value)}`).join(' ');return `<polyline points="${points}" fill="none" stroke="${color}" stroke-width="3"/><text x="40" y="${20+j*18}" fill="${color}">${tag.replace('/loss_epoch','').replace('/loss','')}</text>`}).join('');}
function matchChip(data){const threshold=+$('threshold').value,radius=+$('radius').value/state.display.resolution_m/state.display.output_stride,preds=data.predictions.filter(p=>p.score>=threshold).sort((a,b)=>b.score-a.score),used=new Set(),pairs=new Map(),distances=new Map();for(const p of preds){let best=null,dist=Infinity;for(const t of data.ground_truth){if(used.has(t.tree_id))continue;const d=Math.hypot(p.output_x-t.output_x,p.output_y-t.output_y);if(d<=radius&&d<dist){best=t;dist=d}}if(best){used.add(best.tree_id);pairs.set(p.prediction_id,best);distances.set(p.prediction_id,dist*state.display.output_stride*state.display.resolution_m)}}return {preds,pairs,used,distances}}
function nearestPredictionM(t,m){return m.preds.length?Math.min(...m.preds.map(p=>Math.hypot(p.output_x-t.output_x,p.output_y-t.output_y)))*state.display.output_stride*state.display.resolution_m:null}
function titleTruth(t,matched,nearestM){return [`Inventory truth: ${t.tree_id}`,t.species||'Taxonomy unavailable',t.dbh_in==null?'DBH unavailable':`${t.dbh_in.toFixed(1)} in DBH`,matched?'Matched inside selected radius':'Missed at selected radius',nearestM==null?'No displayed predictions':`Nearest displayed prediction: ${nearestM.toFixed(1)} m`].join(' · ')}
function titlePrediction(p,t,wrong,distanceM){return [`Prediction: ${p.species}`,`${(100*p.species_confidence).toFixed(0)}% species confidence`,`${p.dbh_in.toFixed(1)} in DBH`,`${(100*p.score).toFixed(0)}% center confidence`,t?(wrong?`Spatial match to ${t.tree_id} at ${distanceM.toFixed(1)} m, wrong species`:`Matched to ${t.tree_id} at ${distanceM.toFixed(1)} m`):'No truth inside selected radius'].join(' · ')}
function chipStats(data,m){const wrong=[...m.pairs].filter(([id,t])=>{const p=m.preds.find(value=>value.prediction_id===id);return t.species_id!=null&&p.species_id!==t.species_id}).length;return {matched:m.pairs.size,missed:data.ground_truth.length-m.used.size,falsePositive:m.preds.length-m.pairs.size,wrong}}
function markerLayers(data,m){const size=state.display.chip_pixels,stride=state.display.output_stride,radiusM=+$('radius').value,radiusDiameter=200*radiusM/state.display.resolution_m/size;const at=(value,axis)=>`${100*value[axis]*stride/size}%`,halos=data.ground_truth.map(t=>`<i class="match-radius" style="left:${at(t,'output_x')};top:${at(t,'output_y')};width:${radiusDiameter}%;height:${radiusDiameter}%"></i>`).join(''),truth=data.ground_truth.map(t=>{const matched=m.used.has(t.tree_id),tip=escapeHtml(titleTruth(t,matched,nearestPredictionM(t,m)));return `<i tabindex="0" class="marker truth ${matched?'':'missed'}" style="left:${at(t,'output_x')};top:${at(t,'output_y')}" data-tip="${tip}" title="${tip}"></i>`}).join(''),predictions=m.preds.map(p=>{const t=m.pairs.get(p.prediction_id),wrong=t&&t.species_id!=null&&p.species_id!==t.species_id,tip=escapeHtml(titlePrediction(p,t,wrong,m.distances.get(p.prediction_id)));return `<i tabindex="0" class="marker prediction ${t?(wrong?'wrong':'matched'):''}" style="left:${at(p,'output_x')};top:${at(p,'output_y')}" data-tip="${tip}" title="${tip}"></i>`}).join('');return halos+truth+predictions}
function showTooltip(event){const tip=$('tooltip');tip.textContent=event.currentTarget.dataset.tip;tip.style.display='block';moveTooltip(event)}
function moveTooltip(event){const tip=$('tooltip'),clientX=Number.isFinite(event.clientX)?event.clientX:window.innerWidth/2,clientY=Number.isFinite(event.clientY)?event.clientY:window.innerHeight/2,x=Math.min(clientX+14,window.innerWidth-300),y=Math.min(clientY+14,window.innerHeight-tip.offsetHeight-12);tip.style.left=`${Math.max(8,x)}px`;tip.style.top=`${Math.max(8,y)}px`}
function bindTooltips(root){root.querySelectorAll('[data-tip]').forEach(marker=>{marker.addEventListener('pointerenter',showTooltip);marker.addEventListener('pointermove',moveTooltip);marker.addEventListener('pointerleave',()=>{$('tooltip').style.display='none'});marker.addEventListener('focus',showTooltip);marker.addEventListener('blur',()=>{$('tooltip').style.display='none'})})}
function openModal(data){const m=matchChip(data),stats=chipStats(data,m);$('modal').dataset.chip=data.chip_id;$('modal-title').textContent=`${data.chip_id} · enlarged inspection`;$('modal-image').innerHTML=`<img src="/api/model/image/${data.chip_id}.png">${markerLayers(data,m)}`;$('modal-note').textContent=`${$('radius').value} m radius · ${$('threshold').value} confidence · ${stats.matched} matched · ${stats.missed} missed · ${stats.falsePositive} false positive · ${stats.wrong} wrong species`;$('modal').hidden=false;bindTooltips($('modal-image'))}
function closeModal(){$('modal').hidden=true;$('tooltip').style.display='none'}
async function card(summary){let data=chipCache.get(summary.chip_id);if(!data){data=await fetch(`/api/model/chip/${summary.chip_id}`).then(response=>response.json());chipCache.set(summary.chip_id,data)}const m=matchChip(data),stats=chipStats(data,m);return `<article class="card"><div class="card-head"><strong>${summary.chip_id}</strong><span>${data.ground_truth.length} trees</span></div><div class="image" data-chip="${summary.chip_id}" title="Click to inspect this chip"><img loading="lazy" src="/api/model/image/${summary.chip_id}.png">${markerLayers(data,m)}</div><div class="facts">${stats.matched} matched · ${stats.missed} missed · ${stats.falsePositive} false positive · ${stats.wrong} wrong species</div></article>`}
async function gallery(){const key=$('sort').value,limit=+$('limit').value,chips=[...state.chips].sort((a,b)=>b[key]-a[key]).slice(0,limit);$('gallery').innerHTML='<div class="empty">Rendering chip overlays…</div>';const cards=await Promise.all(chips.map(card));$('gallery').innerHTML=cards.join('');$('gallery').querySelectorAll('[data-chip]').forEach(image=>image.addEventListener('click',()=>openModal(chipCache.get(image.dataset.chip))));bindTooltips($('gallery'))}
async function refresh(){showMetrics();await gallery();const chipId=$('modal').dataset.chip;if(!$('modal').hidden&&chipId)openModal(chipCache.get(chipId))}
async function init(){const response=await fetch('/api/model/summary');if(!response.ok){$('gallery').innerHTML='<div class="empty">No validation artifacts loaded.</div>';return}state=await response.json();const radii=Object.keys(state.metrics.metrics_by_match_radius_m);$('radius').innerHTML=radii.map(value=>`<option value="${value}">${value} m</option>`).join('');$('threshold').value=state.metrics.confidence_threshold;$('threshold-value').textContent=Number($('threshold').value).toFixed(2);$('run-detail').textContent=`${state.metrics.ground_truth_trees} truth trees · ${state.metrics.predictions_above_threshold} predictions at the saved threshold · ${state.metrics.chips} chips`;showMetrics();chart();await gallery();for(const id of ['radius','sort','limit'])$(id).addEventListener('change',refresh);$('threshold').addEventListener('input',()=>{$('threshold-value').textContent=Number($('threshold').value).toFixed(2)});$('threshold').addEventListener('change',refresh);$('modal-close').addEventListener('click',closeModal);$('modal').addEventListener('click',event=>{if(event.target===$('modal'))closeModal()});document.addEventListener('keydown',event=>{if(event.key==='Escape')closeModal()})}init();
</script></body></html>"""

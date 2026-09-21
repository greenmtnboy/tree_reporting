"""Training-only review assignments; metadata priorities are hypotheses, not image diagnoses."""
# ruff: noqa: E501 -- embedded JavaScript follows the Studio template convention

import argparse
import hashlib
import json
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from urban_tree_ml.feedback import load_persisted_reviews


def select(chips, labels, done_trees, explicit_done, city):
    chips = chips[(chips.split == "train") & ~chips.chip_id.isin(explicit_done)].copy()
    labels = labels[labels.split == "train"].copy()
    groups = dict(tuple(labels.groupby("chip_id")))
    records = []
    for chip in chips.itertuples():
        trees = groups.get(chip.chip_id)
        if trees is None or trees.empty or set(trees.tree_id.astype(str)) <= done_trees:
            continue
        records.append(
            {
                "chip_id": chip.chip_id,
                "trees": len(trees),
                "large": int((np.expm1(trees.dbh_log1p) >= 16).sum()),
                "collisions": int(chip.collision_excluded_count),
            }
        )
    # Cap dense-canopy allocation so arboretum-like chips cannot consume the whole batch.
    pools = [
        (
            "large trees, moderate density",
            lambda r: r["large"] > 0 and r["trees"] <= 80,
            lambda r: (-r["large"], r["chip_id"]),
            15 if city == "usbos" else 7,
        ),
        (
            "dense canopy",
            lambda r: r["trees"] > 80,
            lambda r: (-r["large"], r["chip_id"]),
            5 if city == "usbos" else 3,
        ),
        (
            "collision risk",
            lambda r: r["collisions"] > 0 and r["trees"] <= 80,
            lambda r: (-r["collisions"], r["chip_id"]),
            5,
        ),
        (
            "general coverage",
            lambda r: r["trees"] <= 80,
            lambda r: hashlib.sha256(r["chip_id"].encode()).hexdigest(),
            10 if city == "usbos" else 5,
        ),
    ]
    selected, used = [], set()
    for tag, eligible, key, count in pools:
        for row in sorted(
            (r for r in records if r["chip_id"] not in used and eligible(r)), key=key
        )[:count]:
            selected.append({**row, "tag": tag, "split": "train"})
            used.add(row["chip_id"])
    return selected


def build(root, output):
    result = {
        "version": 1,
        "source_run": "sf-boston-naip-vocab-v2-retry1",
        "policy": "Training-only metadata priorities; not confirmed shadow/offset diagnoses",
        "cities": {},
        "input_sha256": {},
    }
    for city, review in [("ussfo", "ussfo-2022-mosaic"), ("usbos", "usbos-2023-external")]:
        dataset = f"sf-boston-naip-vocab-v2-{city}"
        directory = root / "qa" / "registration" / review
        manifest = json.loads((directory / "manifest.json").read_text())
        state = load_persisted_reviews(directory)
        done = {sid for sid, r in state["scene_reviews"].items() if r.get("done")}
        done_trees = {
            str(s["tree_id"])
            for s in manifest["samples"]
            if s.get("scene_id") in done and state["reviews"].get(s["sample_id"], {}).get("status")
        }
        explicit = {
            s.get("validation_chip_id")
            for s in manifest["scenes"]
            if s["scene_id"] in done and "train" in s.get("splits", [])
        }
        paths = [root / "chips" / dataset / name for name in ["chips.parquet", "labels.parquet"]]
        chips, labels = [pd.read_parquet(p) for p in paths]
        result["cities"][city] = {
            "dataset": dataset,
            "items": select(chips, labels, done_trees, explicit, city),
        }
        for path in [*paths, directory / "manifest.json", directory / "reviews.json"]:
            if path.exists():
                result["input_sha256"][str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
    return result


def load_queue(root, city):
    # New inference-derived lists replace the default assignment batch, not its
    # archive. Reading at request time keeps live autosaves and the server intact.
    for candidate in sorted((root / 'review-queues').glob('sf-boston-train-offset-*.json'), reverse=True):
        try:
            data = json.loads(candidate.read_text())
            run = data['inference_run']
            if (root / 'runs' / run / 'COMPLETE').is_file() and city in data['cities']:
                return data['cities'][city]
        except (OSError, ValueError, KeyError):
            continue
    path = root / "review-queues" / "training-curation-20260907.json"
    if not path.exists():
        return {"dataset": None, "items": []}
    return json.loads(path.read_text())["cities"].get(city, {"dataset": None, "items": []})


def validate_training_image(context, chip):
    """Allow all catalogued training chips, never test or arbitrary raster windows."""
    candidates = []
    for path in (context.config.paths.root / 'chips').glob('*/summary.json'):
        try:
            summary = json.loads(path.read_text())
            if str(summary.get('source_raster', '')).replace('\\', '/').split('/')[-1] == context.raster.name:
                manifest = path.parent / 'chips.parquet'
                if manifest.exists():
                    candidates.append((path.stat().st_mtime, manifest))
        except (OSError, ValueError):
            continue
    if not candidates:
        raise ValueError('Training chip catalog unavailable')
    manifest = max(candidates, key=lambda pair: pair[0])[1]
    allowed = _training_image_ids(str(manifest), manifest.stat().st_mtime_ns)
    if chip not in allowed:
        raise ValueError('Chip is not in the training catalog')


@lru_cache(maxsize=8)
def _training_image_ids(path, modified):
    import pandas as pd
    frame = pd.read_parquet(path, columns=['chip_id', 'split'])
    return frozenset(frame.loc[frame.split == 'train', 'chip_id'].astype(str))


def training_image(context, chip):
    import io
    import re

    import rasterio
    from PIL import Image
    from rasterio.windows import Window

    match = re.fullmatch(r'r(\d{6})_c(\d{6})', chip)
    if not match:
        raise ValueError('Invalid chip ID')
    size = context.config.imagery.chip_pixels
    with rasterio.open(context.raster) as source:
        rgb = source.read([1, 2, 3], window=Window(
            int(match[2]) * size, int(match[1]) * size, size, size))
    output = io.BytesIO()
    Image.fromarray(np.moveaxis(rgb, 0, -1).clip(0, 255).astype('uint8')).save(output, 'PNG')
    return output.getvalue()


TRAINING_REVIEW_SCRIPT = r"""
const splitControl=document.createElement('label');
splitControl.innerHTML='Split <select id="review-split"><option value="all">All (training + validation)</option><option value="training-all">All training chips</option><option value="validation">Validation</option><option value="train">Training assignments</option></select>';
document.querySelector('header .controls').prepend(splitControl);
const trainingMode=pageParams.get('split')==='train';
$('review-split').value=['all','training-all'].includes(pageParams.get('split'))?pageParams.get('split'):trainingMode?'train':'validation';
$('review-split').onchange=()=>{const u=new URL(location.href);u.searchParams.set('split',$('review-split').value);for(const k of ['assignment','chip'])u.searchParams.delete(k);location.href=u.pathname+u.search};
async function initTraining(){
 const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 document.head.insertAdjacentHTML('beforeend','<style>[hidden]{display:none!important}</style>');
 document.title='Training chips · Chip review';document.querySelector('h1').textContent='Chip review';
 document.querySelector('header .lede').textContent='Training curation · test remains sealed · inference shown only when available';
 const city=pageParams.get('city')||document.getElementById('studio-city')?.value||'ussfo';
 const response=await fetch('/api/training-queue?'+new URLSearchParams({city}));const payload=await response.json();
 if(!response.ok){$('gallery').textContent=payload.error||'Training assignments unavailable';return}
 document.querySelectorAll('.metrics,.workspace,.legend').forEach(e=>e.hidden=true);
 for(const id of ['threshold','radius','sort'])$(id).closest('label')?.setAttribute('hidden','');
 $('run-detail').textContent=`${city} · ${payload.items.length} assigned training chips · ${payload.items.some(i=>i.inference_run)?'Prediction-guided offset candidates; not held-out scores':'Inference unavailable; no model scores'}`;
 const tags=[...new Set(payload.items.map(i=>i.tag))];
 $('assignment').innerHTML=['all',...tags].map(t=>`<option value="${esc(t)}">${esc(t==='all'?'All assignments':t)}</option>`).join('');
 $('assignment').value=tags.includes(pageParams.get('assignment'))?pageParams.get('assignment'):'all';
 $('review-status').value=pageParams.get('review-status')||(pageParams.get('unreviewed')==='1'?'pending':'all');
 const statusResponse=await fetch('/api/curation-status?'+new URLSearchParams({city}));
 if(statusResponse.ok)curationStatus=(await statusResponse.json()).chips||{};
 window.studioViewState?.restore();
 function render(){
  const tag=$('assignment').value,items=payload.items.filter(i=>(tag==='all'||i.tag===tag)&&(matchesReviewStatus(curationStatus[i.chip_id]||i)));
  $('assignment-progress').textContent=`${payload.items.filter(i=>i.reviewed).length}/${payload.items.length} reviewed · ${items.length} match filters`;
  const back=location.pathname+location.search;
  const queueId=crypto.randomUUID();
  sessionStorage.setItem('validation-curation:'+queueId,JSON.stringify({split:'train',city,run:items[0]?.inference_run||'',chips:items.map(i=>i.chip_id),returnTo:back}));
  $('gallery').innerHTML=items.slice(0,+$('limit').value).map(i=>{
   const url='/curate-training?'+new URLSearchParams({city,chip:i.chip_id,run:i.inference_run||'',return:back,queue:queueId});
   const image='/api/training-image?'+new URLSearchParams({city,chip:i.chip_id});
   const diagnosis=i.reason?`${esc(i.reason)} · median ${esc(i.median_offset_m)} m`:'Inference unavailable';
   return `<article class="card"><div class="card-head"><strong>${esc(i.chip_id)}</strong><span class="assignment-tag">${esc(i.tag)}</span>${i.reviewed?'<span>Reviewed</span>':''}<a class="action-link" href="${esc(url)}">Curate</a></div><div class="image"><a href="${esc(url)}"><img loading="lazy" src="${esc(image)}"></a></div><div class="facts">Training · ${i.trees} retained trees · ${i.large} large trees · ${i.collisions} collision exclusions · ${diagnosis}</div></article>`;
  }).join('')||'<div class="empty">No chips match these filters.</div>';
 }
 // Replace the validation-only assignment listener with the shared-view training renderer.
 const old=$('assignment'),replacement=old.cloneNode(true);replacement.value=old.value;old.replaceWith(replacement);
 for(const id of ['assignment','review-status','limit'])$(id).addEventListener('change',()=>{const u=new URL(location.href);u.searchParams.set('assignment',$('assignment').value);u.searchParams.set('review-status',$('review-status').value);u.searchParams.delete('unreviewed');u.searchParams.set('limit',$('limit').value);history.replaceState(null,'',u);render()});
 if(pageParams.has('limit'))$('limit').value=pageParams.get('limit');
 render();
}
if(['all','training-all'].includes(pageParams.get('split'))||pageParams.get('city')==='all')initAllReview();else if(trainingMode)initTraining();else init();
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.root, args.output)
    print({city: len(value["items"]) for city, value in result["cities"].items()})

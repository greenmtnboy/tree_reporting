"""Current curation coverage alongside saved (not recomputed) validation scores."""
from __future__ import annotations

import json
from collections import Counter
from urllib.parse import urlencode

from urban_tree_ml.chip_catalog import training_chip_catalog
from urban_tree_ml.feedback import load_persisted_reviews


def detection_f2(tp: int, fp: int, fn: int) -> float | None:
    denominator = 5 * tp + fp + 4 * fn
    return 5 * tp / denominator if denominator else None


def city_report(context, catalog) -> dict:
    manifest = json.loads((context.directory / "manifest.json").read_text(encoding="utf-8"))
    state = load_persisted_reviews(context.directory)
    reviews = state["reviews"]
    rows = []
    seen = set()
    candidates = [
        run for run in catalog.summary()["runs"]
        if str(run["city"]).lower() == context.city
        and run["metrics"].get("split", "validation") == "validation"
    ]
    latest = candidates[-1] if candidates else None
    bundle = catalog.bundle(latest["run_id"]) if latest else None
    scores = {row["chip_id"]: row for row in bundle.chips} if bundle else {}
    for scene in manifest["scenes"]:
        scene_id = scene["scene_id"]
        chip = scene.get("validation_chip_id")
        if chip:
            seen.add(chip)
        done = bool(state["scene_reviews"].get(scene_id, {}).get("done"))
        reviewed = sum(bool(reviews.get(s, {}).get("status")) for s in scene["sample_ids"])
        score = scores.get(chip)
        rows.append({
            "id": chip or scene_id,
            "kind": ("Training chip" if scene.get("splits") == ["train"] else "Validation chip")
            if chip else "Review scene",
            "splits": scene.get("splits", []),
            "status": "done" if done else "in-progress" if reviewed else "unreviewed",
            "reviewed_trees": reviewed, "trees": len(scene["sample_ids"]),
            "f2": detection_f2(score["matched"], score["false_positive"], score["missed"])
            if score else None,
            "url": "/registration?" + urlencode({
                "city": context.city, "scene": scene_id, "fullscreen": 1,
            }),
        })
    for chip, score in scores.items():
        if chip in seen:
            continue
        rows.append({
            "id": chip, "kind": "Validation chip", "splits": ["validation"],
            "status": "not-in-review", "reviewed_trees": None, "trees": score["ground_truth"],
            "f2": detection_f2(score["matched"], score["false_positive"], score["missed"]),
            "url": "/model?" + urlencode({"run": latest["run_id"], "chip": chip}),
        })
    split_counts = Counter(s for scene in manifest["scenes"] for s in scene.get("splits", []))
    totals = {
        key: sum(row[key] for row in scores.values())
        for key in ("matched", "false_positive", "missed")
    }
    feedback_path = context.directory / "training-feedback.json"
    feedback = json.loads(feedback_path.read_text()) if feedback_path.exists() else {}
    return {
        "city": context.city, "label": context.label,
        "training_progress": training_chip_catalog(context, manifest, state),
        "scenes": len(manifest["scenes"]),
        "done": sum(bool(r.get("done")) for r in state["scene_reviews"].values()),
        "tree_reviews": sum(bool(r.get("status")) for r in reviews.values()),
        "verdicts": dict(Counter(r["status"] for r in reviews.values() if r.get("status"))),
        "split_scenes": dict(split_counts), "regions": len(state["mask_regions"]),
        "published": feedback.get("source_reviews_sha256") == state["state_revision"],
        "evaluation": {
            "run": latest["run_id"], "cohort": latest["cohort"],
            "threshold": bundle.metrics["confidence_threshold"],
            "radius_m": min(bundle.metrics["metrics_by_match_radius_m"], key=float),
            "f2": detection_f2(totals["matched"], totals["false_positive"], totals["missed"]),
            "chips": len(scores),
        } if latest else None,
        "rows": rows,
    }


# ruff: noqa: E501
CURATION_REPORT_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Curation coverage · Model Studio</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#0c1410;color:#e5efe8;font:15px system-ui}nav{display:flex;gap:12px;flex-wrap:wrap;padding:16px;border-bottom:1px solid #344b3d}a{color:#bce4c9}main{padding:24px;max-width:1500px;margin:auto}h1{font-size:26px}.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:18px}.card{background:#16251c;border:1px solid #375340;border-radius:12px;padding:20px}.big{font-size:32px}.muted{color:#a9beb0;line-height:1.6}.controls{display:flex;gap:12px;flex-wrap:wrap;margin:24px 0 12px}select,input,button,.action{font:inherit;background:#20382a;color:#e5efe8;border:1px solid #4e7159;border-radius:6px;min-height:36px;padding:6px 12px}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:10px;border-bottom:1px solid #293f31}th{color:#b2c9b9}.table{overflow:auto}.action{display:inline-flex;text-decoration:none;align-items:center;white-space:nowrap}progress{width:100%;accent-color:#83c894}#error{color:#ffb8a9}
</style></head><body><nav><a href="/">Studio</a><a href="/registration">Registration</a><a href="/model">Validation</a><a href="/runs">Run history</a><a href="/coverage">Curation coverage</a></nav><main>
<h1>Curation coverage</h1><p class="muted">Live saved review status, alongside the latest saved validation evaluation for each city. F2 weights recall more than precision. Scores reflect the labels at evaluation time; publishing curation does not recompute them. Unmatched predictions count as metric false positives, even where training loss is ignored.</p>
<div id="cards" class="cards"></div><div class="controls"><select id="city" aria-label="City"><option value="">All cities</option></select><select id="status" aria-label="Curation status"><option value="">All statuses</option><option value="done">Done</option><option value="in-progress">In progress</option><option value="unreviewed">Unreviewed</option><option value="not-in-review">Not in review set</option></select><select id="split" aria-label="Split"><option value="">All splits</option><option>train</option><option>validation</option><option>test</option></select><select id="sort" aria-label="Sort"><option value="f2">Lowest F2 first</option><option value="id">Tile ID</option></select><input id="search" placeholder="Find tile or scene" aria-label="Find tile or scene"><button id="refresh">Refresh</button></div><p id="count" class="muted"></p><p id="error"></p><div class="table"><table><thead><tr><th>City</th><th>Tile / scene</th><th>Type</th><th>Split</th><th>Status</th><th>Reviewed trees</th><th>Saved F2</th><th></th></tr></thead><tbody id="rows"></tbody></table></div>
<p class="muted">Review scenes can be smaller than training chips and can overlap: scene counts are not unique training-chip counts. “Not in review set” does not imply no nearby trees were curated. Test is the sealed holdout; its scores are not loaded here. Mixed-split scenes count once in each represented split.</p></main><script>
const $=id=>document.getElementById(id),esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])),pct=v=>v==null?'—':(v*100).toFixed(1)+'%';let cities=[];
function render(){const rows=cities.flatMap(c=>c.rows.map(r=>({...r,city:c.city,label:c.label}))).filter(r=>(!$('city').value||r.city===$('city').value)&&(!$('status').value||r.status===$('status').value)&&(!$('split').value||r.splits.includes($('split').value))&&r.id.includes($('search').value.trim()));rows.sort((a,b)=>$('sort').value==='f2'?(a.f2??2)-(b.f2??2)||a.id.localeCompare(b.id):a.id.localeCompare(b.id));$('count').textContent=`${rows.length} tiles / scenes shown`;$('rows').innerHTML=rows.map(r=>`<tr><td>${esc(r.label)}</td><td>${esc(r.id)}</td><td>${esc(r.kind)}</td><td>${esc(r.splits.join(', '))}</td><td>${esc(r.status)}</td><td>${r.reviewed_trees==null?'—':r.reviewed_trees+' / '+r.trees}</td><td>${pct(r.f2)}</td><td><a class="action" href="${esc(r.url)}">Open</a></td></tr>`).join('')}
async function load(){try{$('error').textContent='';const response=await fetch('/api/coverage');if(!response.ok)throw Error(await response.text());cities=(await response.json()).cities;const selection=$('city').value;$('city').innerHTML='<option value="">All cities</option>'+cities.map(c=>`<option value="${esc(c.city)}">${esc(c.label)}</option>`).join('');$('city').value=selection;$('cards').innerHTML=cities.map(c=>`<article class="card"><h2>${esc(c.label)}</h2>${c.training_progress?`<div class="big">${pct(c.training_progress.fraction)} of training chips curated</div><progress aria-label="${esc(c.label)} training chips curated" value="${c.training_progress.curated}" max="${c.training_progress.total||1}"></progress><p>${c.training_progress.curated} / ${c.training_progress.total} training chips · ${c.training_progress.without_targets} without retained targets</p><a class="action" href="/curate-training?city=${encodeURIComponent(c.city)}">Curate more training chips</a>`:"<p>Training chip catalog unavailable</p>"}<p>${c.done} / ${c.scenes} review scenes done</p><p>${c.tree_reviews.toLocaleString()} tree reviews · ${c.regions} mask regions</p><p class="muted">${Object.entries(c.split_scenes).map(([s,n])=>esc(s)+': '+n).join(' · ')}<br>${Object.entries(c.verdicts).map(([s,n])=>esc(s)+': '+n).join(' · ')}</p><p>${c.published?'Published feedback is current':'Unpublished changes'}</p>${c.evaluation?`<p>Validation F2: ${pct(c.evaluation.f2)} · ${c.evaluation.chips} chips</p><p class="muted">${esc(c.evaluation.run)}<br>${esc(c.evaluation.cohort)} · confidence ${c.evaluation.threshold}${c.evaluation.radius_m?' · '+esc(c.evaluation.radius_m)+' m':''}</p>`:'<p>No saved evaluation</p>'}</article>`).join('');window.studioViewState?.restore();render()}catch(error){$('error').textContent=error.message}}
for(const id of ['city','status','split','sort','search'])$(id).addEventListener('input',render);$('refresh').addEventListener('click',load);load();
</script></body></html>"""

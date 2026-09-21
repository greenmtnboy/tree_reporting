"""Read-only DeepSeek crown-center pilot; never writes to curation or training data.

Prepare freezes completed non-test scenes; run sends only clean RGB and original
anonymous point coordinates; report compares responses with withheld human labels.
All artifacts (including raw responses and usage) stay under the explicit output.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import html
import json
import math
import os
from pathlib import Path
import statistics
import urllib.error
import urllib.request

from pyproj import Transformer

MODEL = 'deepseek-flash'
PROMPT_VERSION = 'crown-center-clean-coordinates-v1'
PROMPT = '''Locate the visible crown center for each listed inventory tree in this
overhead RGB aerial image. Input points are approximate ground/trunk positions,
not reliable crown centers. Tall crowns can be displaced by viewing angle.
Coordinates use the ORIGINAL image dimensions specified below: x increases right,
y increases downward, origin top-left. Do not use coordinates of an internal resized image.
Use crown shape, texture and nearby trees; do not treat shadows, grass, roofs or
pavement as crowns. Do not assume every inventory point has a visible tree.
Do not assign several points to one crown when individual crowns cannot be resolved.
Abstain when you cannot confidently associate an individual crown with the input.
Return JSON only: {"trees":[{"id":"p1","status":"visible","x":12.3,
"y":45.6,"confidence":0.8,"reason":"brief visual evidence"}]}.
Allowed status: visible, uncertain, occluded, no_visible_tree.
For non-visible statuses set x and y to null. Include every input id exactly once,
invent no ids, and do not predict species, DBH or additional trees.
Confidence is your subjective confidence, not a calibrated probability.
'''


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    # Pilot-owned artifacts only. Exclusive creation prevents accidental overwrites.
    with path.open('x', encoding='utf-8') as handle:
        json.dump(value, handle, indent=2, allow_nan=False)


def metric_distance(a, b, sample):
    dx, dy = a[0] - b[0], a[1] - b[1]
    return math.hypot(sample['transform_a']*dx + sample['transform_b']*dy,
                      sample['transform_d']*dx + sample['transform_e']*dy)


def human_point(sample, review, projector):
    if 'corrected_longitude' in review:
        x, y = projector.transform(review['corrected_longitude'], review['corrected_latitude'])
        ox, oy = projector.transform(sample['longitude'], sample['latitude'])
        east, north = x-ox, y-oy
    else:
        east, north = review.get('east_m', 0), review.get('north_m', 0)
    a,b,d,e = [sample['transform_'+k] for k in 'abde']
    det = a*e-b*d
    return [sample['target_x'] + (e*east-b*north)/det,
            sample['target_y'] + (-d*east+a*north)/det]


def prepare(root, output):
    output.mkdir(parents=True, exist_ok=False)
    cohorts = []
    for city, folder in [('ussfo','ussfo-2022-mosaic'),('usbos','usbos-2023-external')]:
        directory = root/'qa'/'registration'/folder
        paths = [directory/'manifest.json', directory/'reviews.json']
        before = [p.stat().st_mtime_ns for p in paths]
        raw_manifest, raw_reviews = [p.read_bytes() for p in paths]
        if before != [p.stat().st_mtime_ns for p in paths]:
            raise RuntimeError('Curation changed during snapshot; use a new output directory')
        manifest, reviews = json.loads(raw_manifest), json.loads(raw_reviews)
        if reviews.get('schema_version') != 2:
            raise ValueError('Pilot requires canonical tree-level schema 2')
        projector = Transformer.from_crs('EPSG:4326',manifest['metadata']['curation_crs'],always_xy=True)
        by_scene = {}
        for sample in manifest['samples']:
            by_scene.setdefault(sample['scene_id'], []).append(sample)
        candidates = []
        for scene in manifest['scenes']:
            samples = by_scene.get(scene['scene_id'], [])
            completion = reviews.get('scene_reviews',{}).get(scene['scene_id'],{})
            if not completion.get('done') or not scene.get('validation_chip_id'):
                continue
            if not 3 <= len(samples) <= 100 or not set(scene.get('splits',[])) <= {'train','validation'}:
                continue
            if any(s.get('split') not in {'train','validation'} for s in samples):
                continue
            records = reviews.get('tree_reviews',{})
            offsets = sum(records.get(str(s['tree_id']),{}).get('status') == 'offset' for s in samples)
            if offsets < 1:
                continue
            candidates.append((scene,samples,completion))
        # Reproducible difficulty-stratified pilot, not a city-representative sample.
        chosen = []
        for low, high in [(3,30),(31,100)]:
            group = [c for c in candidates if low <= len(c[1]) <= high]
            group.sort(key=lambda c:digest((city+c[0]['scene_id']+PROMPT_VERSION).encode()))
            chosen.extend(group[:5])
        if len(chosen) != 10:
            raise ValueError(f'{city}: need five completed chips in each density stratum, found {len(chosen)}')
        for scene,samples,completion in chosen:
            item_dir = output/(city+'-'+scene['validation_chip_id'])
            item_dir.mkdir()
            image = (directory/scene['image']).read_bytes()
            (item_dir/'image.png').write_bytes(image)
            points, truth = [], []
            for index,sample in enumerate(samples,1):
                pid = f'p{index}'
                points.append({'id':pid,'x':sample['target_x'],'y':sample['target_y']})
                review = reviews.get('tree_reviews',{}).get(str(sample['tree_id']),{})
                status = review.get('status','aligned')
                truth.append({'id':pid,'sample':sample,'review':review,'status':status,
                              'human_xy':human_point(sample,review,projector)})
            request_input = {'width':scene['image_width'],'height':scene['image_height'], 'points':points}
            save(item_dir/'input.json',request_input)
            save(item_dir/'withheld-labels.json',truth)
            cohorts.append({'city':city,'chip':scene['validation_chip_id'],'scene':scene['scene_id'],
                'directory':item_dir.name,'points':len(points),'completion':completion,
                'manifest_sha256':digest(raw_manifest),'reviews_sha256':digest(raw_reviews),
                'image_sha256':digest(image),'input_sha256':digest((item_dir/'input.json').read_bytes()),
                'labels_sha256':digest((item_dir/'withheld-labels.json').read_bytes()),
                'imagery_id':manifest['metadata'].get('curation_imagery_id'),
                'crs':manifest['metadata']['curation_crs']})
    save(output/'pilot.json',{'created_at':datetime.now(timezone.utc).isoformat(),
        'model':MODEL,'prompt_version':PROMPT_VERSION,'prompt':PROMPT,'cohort':cohorts,
        'selection':'5 sparse (3–30) and 5 dense (31–100) completed chips per city, each with >=1 offset; no test; deterministic hash order'})
    print(json.dumps({'prepared_chips':len(cohorts),'points':sum(c['points'] for c in cohorts)}),flush=True)


def validate_response(value, inputs):
    rows = value.get('trees')
    if not isinstance(rows,list): raise ValueError('Missing trees array')
    expected = {p['id'] for p in inputs['points']}
    seen = set()
    for row in rows:
        pid = row.get('id')
        if pid not in expected or pid in seen: raise ValueError('Unknown or duplicated point ID')
        seen.add(pid)
        if row.get('status') not in {'visible','uncertain','occluded','no_visible_tree'}:
            raise ValueError('Invalid status')
        confidence = row.get('confidence')
        if isinstance(confidence,bool) or not isinstance(confidence,(int,float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
            raise ValueError('Invalid confidence')
        if row['status'] == 'visible':
            for key,bound in [('x',inputs['width']),('y',inputs['height'])]:
                v = row.get(key)
                if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or not 0 <= v < bound:
                    raise ValueError('Invalid pixel coordinates')
        elif row.get('x') is not None or row.get('y') is not None:
            raise ValueError('Abstentions must not propose coordinates')
    if seen != expected: raise ValueError('Missing point IDs')
    return rows


def run(output, limit):
    key = os.environ.get('DEEPSEEK_API_KEY')
    if not key: raise ValueError('DEEPSEEK_API_KEY missing')
    pilot = json.loads((output/'pilot.json').read_text())
    pending = [c for c in pilot['cohort'] if not (output/c['directory']/'attempt.json').exists()][:limit]
    def request(chip):
        folder = output/chip['directory']
        for filename, field in [('image.png','image_sha256'),('input.json','input_sha256'),('withheld-labels.json','labels_sha256')]:
            if digest((folder/filename).read_bytes()) != chip[field]: raise ValueError('Frozen input hash mismatch')
        inputs = json.loads((folder/'input.json').read_text())
        body = {'model':pilot['model'],'max_tokens':8000,'thinking':{'type':'disabled'},
            'response_format':{'type':'json_object'},'messages':[{'role':'user','content':[
                {'type':'text','text':pilot['prompt']+'\n'+json.dumps(inputs)},
                {'type':'image_url','image_url':{'url':'data:image/png;base64,'+base64.b64encode((folder/'image.png').read_bytes()).decode(), 'detail':'original'}}]}]}
        # Claim before the paid request: reruns cannot silently rebill failed attempts.
        save(folder/'attempt.json',{'started_at':datetime.now(timezone.utc).isoformat(),'model':pilot['model'],'request_sha256':digest(json.dumps(body).encode())})
        try:
            req = urllib.request.Request('https://api.deepseek.com/chat/completions',data=json.dumps(body).encode(),
                headers={'Content-Type':'application/json','Authorization':'Bearer '+key})
            with urllib.request.urlopen(req,timeout=180) as response:
                result = json.load(response)
            save(folder/'response.json',result)
            if result['choices'][0].get('finish_reason') != 'stop': raise ValueError('Model output incomplete')
            rows = validate_response(json.loads(result['choices'][0]['message']['content']),inputs)
            save(folder/'proposals.json',rows)
            print(json.dumps({'chip':chip['directory'],'proposals':len(rows),'usage':result.get('usage')}),flush=True)
        except urllib.error.HTTPError as error:
            save(folder/'error.json',{'error':f'Provider HTTP {error.code}'})
            print(chip['directory'],f'Provider HTTP {error.code}',flush=True)
        except Exception as error:
            save(folder/'error.json',{'error':type(error).__name__,'detail':str(error)[:250]})
            print(chip['directory'],type(error).__name__,flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(request,pending))


def report(output):
    pilot = json.loads((output/'pilot.json').read_text())
    observations, cards = [], []
    usage = Counter()
    for chip in pilot['cohort']:
        folder = output/chip['directory']
        if (folder/'response.json').exists():
            response = json.loads((folder/'response.json').read_text())
            usage.update({k:v for k,v in response.get('usage',{}).items() if isinstance(v,int)})
        if not (folder/'proposals.json').exists(): continue
        proposals = {p['id']:p for p in json.loads((folder/'proposals.json').read_text())}
        labels = json.loads((folder/'withheld-labels.json').read_text())
        inputs = json.loads((folder/'input.json').read_text())
        shapes = []
        for label in labels:
            p, s = proposals[label['id']], label['sample']
            origin = [s['target_x'],s['target_y']]
            accepted = label['status'] in {'aligned','offset'}
            visible = p['status'] == 'visible'
            target = label['human_xy']
            before = metric_distance(origin,target,s) if accepted else None
            after = metric_distance([p['x'],p['y']],target,s) if accepted and visible else None
            observations.append({'city':chip['city'],'chip':chip['chip'],'id':label['id'],
                'tree_id':s['tree_id'],'human_status':label['status'],'model_status':p['status'],
                'confidence':p['confidence'],'before_m':before,'after_m':after,
                'move_m':metric_distance(origin,[p['x'],p['y']],s) if visible else None})
            shapes.append(f'<circle cx="{origin[0]}" cy="{origin[1]}" r="1.5" fill="#28dfff"/>')
            if accepted: shapes.append(f'<circle cx="{target[0]}" cy="{target[1]}" r="2.3" fill="none" stroke="#7dff72" stroke-width=".65"/>')
            if visible:
                title = html.escape(f"{label['id']} {label['status']} → {p['status']} {p['confidence']}: {p.get('reason','')}")
                shapes.append(f'<g><title>{title}</title><path d="M{origin[0]},{origin[1]} L{p["x"]},{p["y"]}" stroke="#ffab43" stroke-width=".55"/><circle cx="{p["x"]}" cy="{p["y"]}" r="2.3" fill="none" stroke="#ffab43" stroke-width=".8"/></g>')
        cards.append(f'<section><h2>{chip["directory"]}</h2><svg viewBox="0 0 {inputs["width"]} {inputs["height"]}"><image href="{chip["directory"]}/image.png" width="{inputs["width"]}" height="{inputs["height"]}"/>{"".join(shapes)}</svg></section>')
    lines = ['# DeepSeek crown-center pilot','',pilot['selection'],'',
        'Human labels were withheld from the API. Clean RGB + original anonymous pixel coordinates only. No annotations changed.',
        'This is a selected localization diagnostic, not detection AP/F2 or a representative city benchmark. Human centers are a reference, not infallible truth.', '',
        '| City | Accepted human trees | Visible proposals | Median error before → after (m) | Improved >1m | Worsened >1m |',
        '|---|---:|---:|---|---:|---:|']
    details = []
    for city in ['ussfo','usbos']:
        group = [r for r in observations if r['city']==city]
        accepted = [r for r in group if r['before_m'] is not None]
        scored = [r for r in accepted if r['after_m'] is not None]
        median = lambda key:round(statistics.median(r[key] for r in scored),2) if scored else None
        lines.append(f'| {city} | {len(accepted)} | {len(scored)} | {median("before_m")} → {median("after_m")} | {sum(r["before_m"]-r["after_m"]>1 for r in scored)} | {sum(r["after_m"]-r["before_m"]>1 for r in scored)} |')
        excluded = [r for r in group if r['before_m'] is None]
        details.extend(['',f'{city}: {len(accepted)-len(scored)} abstentions on accepted trees; {sum(r["model_status"]=="visible" for r in excluded)}/{len(excluded)} excluded/uncertain human labels nevertheless proposed visible. (These exclusions do not necessarily mean no tree exists.)',''])
        for status in ['aligned','offset']:
            subset = [r for r in scored if r['human_status']==status]
            if subset:
                details.append(f'- {city} {status}: {len(subset)} visible proposals; median error {statistics.median(r["before_m"] for r in subset):.2f} → {statistics.median(r["after_m"] for r in subset):.2f} m; {sum(r["before_m"]-r["after_m"]>1 for r in subset)} improve >1m; {sum(r["after_m"]-r["before_m"]>1 for r in subset)} worsen >1m.')
    lines += details
    lines += [f'Completed chips: {len(cards)}/{len(pilot["cohort"])}.',f'Token usage (all received responses): {dict(usage)}.', '',
        'Error medians use the identical subset with visible proposals; abstentions are reported separately. Self-reported confidence is uncalibrated. No acceptance threshold was tuned.',
        'Next: inspect wrong-tree associations and aligned controls before considering any bulk acceptance. Test chips remain sealed.']
    # Reports get versioned names so resumed runs preserve earlier outputs.
    suffix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    save(output/f'observations-{suffix}.json',observations)
    (output/f'REPORT-{suffix}.md').write_text('\n'.join(lines),encoding='utf-8')
    (output/f'overlay-{suffix}.html').write_text('<!doctype html><meta charset="utf-8"><title>Crown center pilot</title><style>body{background:#101912;color:white;font:16px system-ui;margin:24px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:20px}svg{width:100%;max-width:800px}h2{font-size:16px}</style><h1>Read-only center proposals</h1><p>Cyan: original · Green: human center · Orange: AI proposal. Hover orange rings for evidence. No changes applied.</p><main>'+''.join(cards)+'</main>',encoding='utf-8')
    print('\n'.join(lines).encode('ascii','replace').decode(),flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run','report'])
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--root',type=Path)
    parser.add_argument('--limit',type=int,default=1)
    args = parser.parse_args()
    if args.action == 'prepare':
        if args.root is None: parser.error('--root required for prepare')
        prepare(args.root,args.output)
    elif args.action == 'run':
        if not 1 <= args.limit <= 20: parser.error('--limit must be 1–20')
        run(args.output,args.limit)
    else: report(args.output)


if __name__ == '__main__':
    main()

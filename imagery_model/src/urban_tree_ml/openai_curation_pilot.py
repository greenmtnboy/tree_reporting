"""Exactly two frozen chips, OpenAI suggestions only; no live curation writes."""
import argparse
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import html
import json
import os
from pathlib import Path
import statistics
import urllib.error
import urllib.request

from urban_tree_ml.center_pilot import digest, save, validate_response, metric_distance

MODEL = 'gpt-6-astra'
PROMPT = '''Review the listed inventory points in this overhead RGB aerial image.
Primary task: identify points that clearly are NOT supported by a tree in this image.
Secondary task: propose the visible crown center when association is clear.
Input coordinates are approximate inventory ground/trunk locations, not crown centers.
Use the original image's pixel coordinates: x right, y down, origin top-left.
Return JSON {"trees":[{"id":"p1","status":"visible","x":1.2,"y":3.4,
"confidence":0.8,"clear_not_tree":false,"reason":"brief visual evidence"}]}.
Include each input ID exactly once; never invent IDs. Allowed status values:
visible, uncertain, occluded, no_visible_tree. Set x/y to null unless visible.
Set clear_not_tree=true ONLY when there is positive visible evidence of a non-tree
surface (e.g. continuous roof, open pavement, bare ground) AND no plausible associated
crown within 12 meters. For example, a dot on pavement next to an offset crown is
NOT a clear negative. Tall trees can lean in the image because of perspective.
Do not equate gray pixels, shadow, buildings hiding trees, small indistinct trees,
dense overlapping crowns or an inventory error with proof that no tree is present.
Use uncertain or occluded when association cannot be resolved; never force a negative.
If clear_not_tree=true, status must be no_visible_tree, confidence >=0.9, and give
specific visible evidence in reason. Confidence is subjective, not calibrated.
Do not assign several inventory points to a single indistinguishable crown.
No species, DBH, extra trees, or inferred historical tree removal. These are proposals
for a human reviewer, not ground-truth changes. Return JSON only.
'''


def validate(value, inputs):
    rows = validate_response(value, inputs)
    for row in rows:
        if type(row.get('clear_not_tree')) is not bool:
            raise ValueError('clear_not_tree must be boolean')
        if not isinstance(row.get('reason'),str) or not row['reason'].strip():
            raise ValueError('Visual evidence required')
        if row['clear_not_tree'] and (row['status']!='no_visible_tree' or row['confidence']<.9):
            raise ValueError('Unsafe negative proposal')
    return rows


def prepare(source, output):
    parent = json.loads((source/'pilot.json').read_text())
    chosen = []
    for city in ['ussfo','usbos']:
        # Select on input point count only, not either model's observed accuracy.
        chosen.append(min((c for c in parent['cohort'] if c['city']==city),
                          key=lambda c:(abs(c['points']-20),c['chip'])))
    output.mkdir(parents=True,exist_ok=False)
    for chip in chosen:
        folder = output/chip['directory']; folder.mkdir()
        for filename,field in [('image.png','image_sha256'),('input.json','input_sha256'),('withheld-labels.json','labels_sha256')]:
            data = (source/chip['directory']/filename).read_bytes()
            if digest(data)!=chip[field]: raise ValueError('Frozen input hash mismatch')
            (folder/filename).write_bytes(data)
        baseline = source/chip['directory']/'proposals.json'
        if baseline.exists(): (folder/'deepseek-proposals.json').write_bytes(baseline.read_bytes())
    save(output/'pilot.json',{'model':MODEL,'prompt':PROMPT,'prompt_version':'clear-negative-plus-center-v1',
        'cohort':chosen,'source':str(source),'created_at':datetime.now(timezone.utc).isoformat(),
        'selection':'One chip per city with point count closest to 20, chip ID tie-break; no outcome selection',
        'comparison_caveat':'Same images/original points/human labels, but revised prompt and reasoning: not a controlled model-only comparison'})


def run(output):
    key = os.environ.get('OPENAI_API_KEY')
    if not key: raise ValueError('OPENAI_API_KEY missing')
    pilot = json.loads((output/'pilot.json').read_text())
    if len(pilot['cohort'])!=2: raise ValueError('This pilot is limited to two chips')
    def request(chip):
        folder = output/chip['directory']
        if (folder/'attempt.json').exists(): return
        for filename,field in [('image.png','image_sha256'),('input.json','input_sha256'),('withheld-labels.json','labels_sha256')]:
            if digest((folder/filename).read_bytes())!=chip[field]: raise ValueError('Input hash mismatch')
        inputs = json.loads((folder/'input.json').read_text())
        # Read only affine scale from the withheld file, never decisions or corrections.
        sample = json.loads((folder/'withheld-labels.json').read_text())[0]['sample']
        inputs['meters_per_pixel'] = abs(sample['transform_a'])
        body = {'model':pilot['model'],'store':False,'max_output_tokens':12000,
            'reasoning':{'effort':'medium'},'text':{'format':{'type':'json_object'}},
            'input':[{'role':'user','content':[{'type':'input_text','text':pilot['prompt']+'\n'+json.dumps(inputs)},
            {'type':'input_image','detail':'original','image_url':'data:image/png;base64,'+base64.b64encode((folder/'image.png').read_bytes()).decode()}]}]}
        save(folder/'attempt.json',{'model':pilot['model'],'started_at':datetime.now(timezone.utc).isoformat(),
            'request_sha256':digest(json.dumps(body).encode()),'reasoning_effort':'medium','meters_per_pixel':inputs['meters_per_pixel']})
        try:
            req = urllib.request.Request('https://api.openai.com/v1/responses',data=json.dumps(body).encode(),
                headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'})
            with urllib.request.urlopen(req,timeout=240) as response: result = json.load(response)
            save(folder/'response.json',result)
            if result.get('status')!='completed': raise ValueError('Incomplete provider response')
            text = ''.join(c['text'] for item in result.get('output',[]) if item.get('type')=='message'
                           for c in item.get('content',[]) if c.get('type')=='output_text')
            rows = validate(json.loads(text),inputs)
            save(folder/'proposals.json',rows)
            print(json.dumps({'chip':chip['directory'],'points':len(rows),
                'clear_not_tree':sum(r['clear_not_tree'] for r in rows),'usage':result.get('usage')}),flush=True)
        except urllib.error.HTTPError as error:
            save(folder/'error.json',{'error':f'Provider HTTP {error.code}'})
            print(f'{chip["directory"]}: HTTP {error.code}',flush=True)
        except Exception as error:
            save(folder/'error.json',{'error':type(error).__name__,'detail':str(error)[:200]})
            print(f'{chip["directory"]}: {type(error).__name__}',flush=True)
    with ThreadPoolExecutor(max_workers=2) as pool: list(pool.map(request,pilot['cohort']))


def report(output):
    pilot = json.loads((output/'pilot.json').read_text())
    lines = ['# OpenAI two-chip curation pilot','',pilot['selection'],pilot['comparison_caveat'],'',
             'All decisions are proposals. No curation or training labels changed.','']
    cards=[]
    for chip in pilot['cohort']:
        folder = output/chip['directory']
        if not (folder/'proposals.json').exists():
            lines.append(f'{chip["directory"]}: no validated response.'); continue
        rows=json.loads((folder/'proposals.json').read_text())
        truth={r['id']:r for r in json.loads((folder/'withheld-labels.json').read_text())}
        inputs=json.loads((folder/'input.json').read_text())
        clear=[r for r in rows if r['clear_not_tree']]
        lines += [f'## {chip["directory"]}',f'{len(rows)} points; {len(clear)} clear-negative proposals.','']
        for row in clear:
            lines.append(f'- {row["id"]}: human={truth[row["id"]]["status"]}; confidence={row["confidence"]}; {row["reason"]}')
        for name,proposals in [('OpenAI',rows),('DeepSeek',json.loads((folder/'deepseek-proposals.json').read_text()))]:
            scored=[]; accepted=sum(r['status'] in {'aligned','offset'} for r in truth.values())
            for row in proposals:
                t=truth[row['id']]; s=t['sample']
                if t['status'] not in {'aligned','offset'} or row['status']!='visible': continue
                scored.append((metric_distance([s['target_x'],s['target_y']],t['human_xy'],s),
                               metric_distance([row['x'],row['y']],t['human_xy'],s)))
            if scored:
                lines.append(f'{name}: {len(scored)}/{accepted} accepted human trees have visible proposals; median center error {statistics.median(a for a,b in scored):.2f} -> {statistics.median(b for a,b in scored):.2f} m; improve >1m: {sum(a-b>1 for a,b in scored)}; worsen >1m: {sum(b-a>1 for a,b in scored)}. Subsets may differ between models.')
        shapes=[]; table=[]
        for row in rows:
            t=truth[row['id']]; s=t['sample']; x,y=s['target_x'],s['target_y']
            title=html.escape(f'{row["id"]}: {row["status"]}; human: {t["status"]}; {row["reason"]}')
            shapes.append(f'<circle cx="{x}" cy="{y}" r="1.2" fill="#28dfff"/>')
            if t['status'] in {'aligned','offset'}:
                hx,hy=t['human_xy'];shapes.append(f'<circle cx="{hx}" cy="{hy}" r="2" fill="none" stroke="#7dff72" stroke-width=".6"/>')
            if row['status']=='visible':
                shapes.append(f'<g><title>{title}</title><path d="M{x},{y} L{row["x"]},{row["y"]}" stroke="#ffab43" stroke-width=".5"/><circle cx="{row["x"]}" cy="{row["y"]}" r="2.4" fill="none" stroke="#ffab43" stroke-width=".8"/></g>')
            if row['clear_not_tree']:
                shapes.append(f'<g stroke="#ff526d" stroke-width="1.3"><title>{title}</title><path d="M{x-3},{y-3}l6,6 M{x-3},{y+3}l6,-6"/></g>')
            shapes.append(f'<text x="{x+2}" y="{y-2}" fill="white" stroke="black" stroke-width=".3" paint-order="stroke" font-size="3.5">{row["id"]}</text>')
            table.append('<tr>'+''.join(f'<td>{html.escape(str(v))}</td>' for v in [row['id'],row['status'],row['clear_not_tree'],t['status'],row['reason']])+'</tr>')
        cards.append(f'<section><h2>{chip["directory"]}</h2><svg viewBox="0 0 {inputs["width"]} {inputs["height"]}"><image href="{chip["directory"]}/image.png" width="{inputs["width"]}" height="{inputs["height"]}"/>{"".join(shapes)}</svg><table><tr><th>ID</th><th>AI status</th><th>Clear negative?</th><th>Human</th><th>Evidence</th></tr>{"".join(table)}</table></section>')
    lines += ['', 'Human not-tree labels are broad target exclusions, NOT confirmed non-tree ground truth. Agreement cannot establish clear-negative accuracy. Duplicate, uncertain and occluded are also not negative ground truth. A clear-negative proposal on an aligned/offset human tree is a safety disagreement requiring review. Two tiles cannot establish a safe bulk-accept threshold.']
    suffix=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
    (output/f'REPORT-{suffix}.md').write_text('\n\n'.join(lines),encoding='utf-8')
    (output/f'overlay-{suffix}.html').write_text('<!doctype html><meta charset="utf-8"><title>OpenAI curation pilot</title><style>body{background:#101912;color:white;font:16px system-ui;margin:24px}main{display:grid;grid-template-columns:repeat(auto-fit,minmax(450px,1fr));gap:24px}svg{width:100%}table{border-collapse:collapse;font-size:13px}td,th{padding:6px;border-bottom:1px solid #456;text-align:left}h2{font-size:17px}</style><h1>OpenAI: two-chip curation proposals</h1><p>Cyan: original · Green: human center · Orange: proposed center · Red ×: clear-negative proposal. Read-only; nothing applied.</p><main>'+''.join(cards)+'</main>',encoding='utf-8')
    print('\n'.join(lines).encode('ascii','replace').decode(),flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','run','report'])
    parser.add_argument('--source',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    if args.action=='prepare': prepare(args.source,args.output)
    elif args.action=='run': run(args.output)
    else: report(args.output)


if __name__=='__main__': main()

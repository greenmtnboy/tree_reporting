"""Package a visually screened shortlist; never change reviewer annotations."""
import html
import json
from pathlib import Path


def build(directory):
    data=json.loads((directory/'candidates.json').read_text())
    groups={
        'Dense canopy donors': ['r000064_c000066','r000068_c000003','r000063_c000066','r000066_c000039','r000067_c000040','r000093_c000039'],
        'Grass / hard-negative regions': ['r000055_c000034','r000040_c000099','r000110_c000058','r000054_c000061','r000093_c000085','r000028_c000081'],
    }
    lookup={r['chip_id']:r for r in data['items']}
    body=['<!doctype html><meta charset="utf-8"><title>Boston augmentation review</title>',
          '<style>body{background:#142019;color:#e7eee8;font:16px system-ui;margin:24px}section{display:grid;grid-template-columns:repeat(auto-fit,minmax(290px,1fr));gap:20px}article{background:#23372a;padding:16px;border-radius:12px}img{width:100%;image-rendering:auto}a{display:inline-block;background:#3d644c;color:white;padding:10px;border-radius:6px;text-decoration:none}p{max-width:1000px;line-height:1.5}small{display:block;margin:12px 0}</style>',
          '<h1>Boston augmentation donor review</h1><p>12 training-only candidates, visually screened. No synthetic chips or training labels have been created. Model: '+html.escape(data['run'])+'. Predictions counted at confidence 0.13. High confidence is not proof that a prediction is correct; goal F2 can ignore many extra predictions in vegetation.</p>',
          '<p>Canopy: correct visible centers, remove over-splitting, add missing visible trees, adjust crown sizes; mark genuinely hidden trees occluded. Grass: use confirmed-background regions only on areas you can confidently verify contain no visible trees. Do not mark whole mixed tiles negative. Existing not-tree / duplicate annotations are not a substitute for explicit background masks.</p>']
    selected=[]
    for group,ids in groups.items():
        body.append('<h2>'+group+'</h2><section>')
        for chip in ids:
            r=lookup[chip]; selected.append(dict(r,review_group=group))
            body.append(f'<article><h3>{chip}</h3><img src="{chip}.png"><small>{r["targets"]} retained targets · {r["predictions"]} predictions ≥0.13 · {r["confident"]} ≥0.35<br>Frozen training-label goal F2 {r["goal_f2"]:.1%}; not a held-out score</small><a href="{html.escape(r["url"],quote=True)}" target="_blank" rel="noopener">Curate this chip</a></article>')
        body.append('</section>')
    body.append('<h2>Before recombination</h2><p>Review does not auto-approve a donor. First use original chips / translated crops. If we proceed with composites, use same acquisition and preferably nearby source patches; preserve crown plus shadow and surrounding context; translate without independently rotating, flipping or resizing donors. Any rotation/flip happens after composing the whole image, with every label and mask transformed identically. Same source/date does not establish identical camera angle: inspect seams and tall-tree lean. Keep unresolved overlap areas loss-free, retain all patch provenance, and never synthesize validation/test examples.</p>')
    (directory/'review.html').write_text('\n'.join(body),encoding='utf-8')
    (directory/'review-shortlist.json').write_text(json.dumps(dict(run=data['run'],items=selected,approved_for_training=False),indent=2))
    assert len(selected)==12 and len({r['chip_id'] for r in selected})==12
    print(directory/'review.html')


if __name__=='__main__':
    import sys
    build(Path(sys.argv[1]))

"""Read-only source/curation audit; writes only a fresh benchmark directory."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import numpy as np
import pandas as pd
from pyproj import Transformer

root = Path('C:/Users/ethan/coding_projects/sf_tree_reporting/imagery_model/artifacts')
out = root/'benchmarks'/('sf-source-audit-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
out.mkdir()
provenance = {}
def fetch(name, url):
    with urlopen(url, timeout=180) as response:
        body = response.read()
        provenance[name] = dict(url=url, sha256=hashlib.sha256(body).hexdigest(), last_modified=response.headers.get('Last-Modified'))
    (out/name).write_bytes(body)
    return body

reviewdir = root/'qa/registration/ussfo-2022-mosaic'
for name in ('reviews.json', 'manifest.json'):
    data = (reviewdir/name).read_bytes()
    (out/name).write_bytes(data)
    provenance[name] = dict(sha256=hashlib.sha256(data).hexdigest())
state = json.loads((out/'reviews.json').read_bytes())
manifest = json.loads((out/'manifest.json').read_bytes())
old = pd.read_parquet(root/'run-inputs/sf-boston-naip-swin-v7-finetune/ussfo/inventory.parquet').set_index('tree_id')
fetch('published.parquet','https://storage.googleapis.com/trilogy_public_models/duckdb/trees/ussfo_tree_info_v2.parquet')
new = pd.read_parquet(out/'published.parquet')
print('Published',len(new),new.columns.tolist(),flush=True)
fetch('portal-metadata.json','https://data.sf.gov/api/views/tkzw-k3nq.json')
raw=[]
for offset in range(0,250000,50000):
    query=urlencode({'$select':'treeid,species,latitude,longitude,mapdbh,planteddate,planttype','$order':'treeid','$limit':50000,'$offset':offset})
    rows=json.loads(fetch(f'portal-{offset}.json','https://data.sf.gov/resource/tkzw-k3nq.json?'+query))
    raw.extend(rows)
    if len(rows)<50000:break
rawids={'sf-'+str(r['treeid']) for r in raw if r.get('treeid')}
primary=set(new.tree_id.astype(str)); aliases={}
for row in new.to_dict('records'):
    aliases[str(row['tree_id'])]=str(row['tree_id'])
    values=row.get('merged_tree_ids',[])
    if isinstance(values,str):
        try:values=json.loads(values)
        except ValueError:values=[v.strip() for v in values.split(',') if v.strip()]
    if isinstance(values,(list,np.ndarray)):
        for value in values:aliases[str(value)]=str(row['tree_id'])
reviews=state['tree_reviews']; samples={s['sample_id']:s for s in manifest['samples']}
done={k for k,v in state['scene_reviews'].items() if v.get('done') or v.get('more_done')}
doneids={str(samples[i]['tree_id']) for s in manifest['scenes'] if s['scene_id'] in done for i in s['sample_ids']}
records=[]
for tid,v in reviews.items():
    records.append(dict(tree_id=tid,status=v.get('status'),human=v.get('source')=='human',done=tid in doneids,
                        municipal=tid.startswith('sf-'),in_raw=tid in rawids,in_primary=tid in primary,
                        in_published=tid in aliases,primary_id=aliases.get(tid),
                        absolute_position='corrected_longitude' in v,crown='crown_radius_m' in v))
audit=pd.DataFrame(records); audit.to_csv(out/'curation-survival.csv',index=False)
stats={}
for name,mask in {'all':audit.index==audit.index,'human':audit.human,'done':audit.done,'offset':audit.status=='offset','positive':audit.status.isin(['aligned','offset']),'excluded':audit.status.isin(['not-tree','uncertain','duplicate','occluded']),'municipal':audit.municipal,'crown':audit.crown}.items():
    part=audit[mask];stats[name]={'total':len(part),'retained':int(part.in_published.sum()),'missing':int((~part.in_published).sum())}
by_status=audit.groupby('status').agg(total=('tree_id','size'),retained=('in_published','sum')).to_dict('index')
municipal_old={str(i) for i in old.index if str(i).startswith('sf-')}
summary=dict(old_count=len(old),new_count=len(new),raw_count=len(raw),raw_unique=len(rawids),old_municipal=len(municipal_old),old_municipal_in_raw=len(municipal_old&rawids),old_municipal_missing_raw=len(municipal_old-rawids),raw_added=len(rawids-municipal_old),old_primary_survive=len(set(old.index)&primary),old_with_alias_survive=len(set(old.index)&set(aliases)),curation=stats,status=by_status,added_trees=len(state.get('added_trees',{})),area_masks=len(state.get('mask_regions',[])),done_scenes=len(done),saved_at=state.get('saved_at'))
project=Transformer.from_crs('EPSG:4326',manifest['metadata']['curation_crs'],always_xy=True)
fresh=new.drop_duplicates('tree_id').set_index('tree_id')
changes=[]
for tid in set(old.index)&set(aliases):
    a=old.loc[tid];b=fresh.loc[aliases[tid]]
    ax,ay=project.transform(a.longitude,a.latitude);bx,by=project.transform(b.longitude,b.latitude)
    changes.append(dict(tree_id=tid,shift_m=float(np.hypot(ax-bx,ay-by)),curated=tid in reviews,
       species_changed=str(a.get('source_species',a.species))!=str(b.species),
       dbh_changed=str(a.diameter_at_breast_height)!=str(b.diameter_at_breast_height)))
changes=pd.DataFrame(changes);changes.to_csv(out/'source-changes.csv',index=False)
summary['changes']={}
for name,c in [('all',changes),('curated',changes[changes.curated])]:
    summary['changes'][name]=dict(count=len(c),shift_over_1m=int((c.shift_m>1).sum()),shift_over_4m=int((c.shift_m>4).sum()),species_strings_changed=int(c.species_changed.sum()),dbh_changed=int(c.dbh_changed.sum()))
# Conservative protected footprint: any explicit human tree review or completion.
# Uses each actual displayed crop's georeferencing, not arbitrary scene ordering.
protected=[]
for scene in manifest['scenes']:
    ss=[samples[i] for i in scene['sample_ids']]
    if not ss:continue
    if scene['scene_id'] not in done and not any(reviews.get(str(s['tree_id']),{}).get('source')=='human' for s in ss):continue
    s=ss[0];x,y=project.transform(s['longitude'],s['latitude'])
    a,b,d,e=[s.get('transform_'+k,0) for k in ('a','b','d','e')]
    x0=x-a*s['target_x']-b*s['target_y'];y0=y-d*s['target_x']-e*s['target_y']
    corners=[(x0+a*u+b*v,y0+d*u+e*v) for u,v in [(0,0),(scene['image_width'],0),(0,scene['image_height']),(scene['image_width'],scene['image_height'])]]
    protected.append((min(z[0] for z in corners),min(z[1] for z in corners),max(z[0] for z in corners),max(z[1] for z in corners)))
def inside(frame):
    x,y=project.transform(frame.longitude.to_numpy(),frame.latitude.to_numpy());hit=np.zeros(len(frame),dtype=bool)
    for l,b,r,t in protected:hit|=(x>=l)&(x<=r)&(y>=b)&(y<=t)
    return hit
oldhit=inside(old);newhit=inside(new)
summary['protected_footprints']=dict(scenes=len(protected),old_rows_inside=int(oldhit.sum()),old_rows_outside=int((~oldhit).sum()),new_rows_inside=int(newhit.sum()),new_rows_outside=int((~newhit).sum()),missing_curated_outside=int(sum(tid not in aliases and not oldhit[old.index.get_loc(tid)] for tid in reviews if tid in old.index)))
summary['absolute_offset_records']=int(audit[audit.status=='offset'].absolute_position.sum())
summary['many_to_one_curated_groups']=int(audit[audit.in_published].groupby('primary_id').size().gt(1).sum())
(out/'summary.json').write_text(json.dumps(summary,indent=2))
(out/'provenance.json').write_text(json.dumps(provenance,indent=2))
print(out,flush=True);print(json.dumps(summary,indent=2),flush=True)

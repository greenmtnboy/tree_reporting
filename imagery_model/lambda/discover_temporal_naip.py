"""Read-only Boston/SF NAIP catalog snapshot; never changes existing image indexes."""
import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
import requests

BBOXES = {'usbos': [-71.20,42.22,-70.92,42.42],
          'ussfo': [-122.53,37.69,-122.35,37.84]}
API = 'https://planetarycomputer.microsoft.com/api/stac/v1/search'


def discover(city, bbox, output):
    output=Path(output)
    if output.exists(): raise ValueError('Catalog snapshot already exists')
    items={}; seen=set(); url=API
    params={'collections':'naip','bbox':','.join(map(str,bbox)),
            'datetime':'2010-01-01T00:00:00Z/2026-12-31T23:59:59Z','limit':100}
    while url:
        if urlsplit(url).netloc != urlsplit(API).netloc: raise ValueError('Unexpected pagination host')
        response=requests.get(url,params=params,timeout=60);response.raise_for_status()
        if response.url in seen: raise ValueError('Repeated pagination URL')
        seen.add(response.url);page=response.json()
        for item in page['features']:
            if 'image' in item['assets']: items[item['id']]=item
        links=[x for x in page.get('links',[]) if x['rel']=='next']
        if len(links)>1 or any(x.get('method','GET')!='GET' for x in links): raise ValueError('Unsupported pagination')
        url=links[0]['href'] if links else None;params=None
    if not items: raise ValueError('No NAIP imagery returned')
    summary={}
    for item in items.values():
        year=str(item['properties'].get('naip:year') or item['properties']['datetime'][:4])
        row=summary.setdefault(year,{'items':0,'gsd_m':[]});row['items']+=1
        gsd=item['properties'].get('gsd')
        if gsd not in row['gsd_m']: row['gsd_m'].append(gsd)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(dict(city=city,bbox=bbox,created_at=datetime.now(timezone.utc).isoformat(),
        status='catalog_only_not_prepared',summary=summary,items=list(items.values())),indent=2)+'\n',encoding='utf-8')
    return {'city':city,'years':summary,'output':str(output)}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,required=True)
    args=parser.parse_args()
    for city,bbox in BBOXES.items(): print(json.dumps(discover(city,bbox,args.output_dir/(city+'-catalog.json'))),flush=True)

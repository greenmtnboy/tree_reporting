"""Render a completed species atlas with frozen training-label coverage."""
import argparse
import hashlib
import json
from pathlib import Path
import pandas as pd


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',type=Path,required=True)
    p.add_argument('--atlas',type=Path,required=True)
    args=p.parse_args()
    assert (args.atlas/'COMPLETE').exists()
    data=json.loads((args.atlas/'atlas.json').read_text())
    coverage=[];files={}
    for city in data['cohort_counts']:
        path=args.root/'runs'/data['run']/'evaluation'/('train-'+city)/'ground-truth.parquet'
        frame=pd.read_parquet(path,columns=['split','species_id','tree_id','chip_id'])
        assert set(frame.split)=={'train'}
        with path.open('rb') as f:files[str(path)]=hashlib.file_digest(f,'sha256').hexdigest()
        for label,g in frame.dropna(subset=['species_id']).groupby('species_id'):
            assert int(label)==label and 0<=label<len(data['names'])
            coverage.append(dict(city=city,species=int(label),label_rows=len(g),
                unique_ids=g.tree_id.nunique(),tiles=g.chip_id.nunique()))
    data['training_coverage']=coverage
    data['training_coverage_files']=files
    template=Path(__file__).parents[1]/'src/urban_tree_ml/species_microscope.html'
    payload=json.dumps(data,separators=(',',':')).replace('<','\\u003c')
    (args.atlas/'index.html').write_text(template.read_text(encoding='utf-8').replace('__ATLAS__',payload),encoding='utf-8')
    (args.atlas/'render-receipt.json').write_text(json.dumps(dict(training_coverage=coverage,
        training_coverage_files=files,template_sha256=hashlib.sha256(template.read_bytes()).hexdigest(),
        atlas_sha256=hashlib.sha256((args.atlas/'atlas.json').read_bytes()).hexdigest()),indent=2))
    print('Rendered',len(data['rows']),'validation centers; training coverage',len(coverage),'city/species groups')


if __name__=='__main__':main()

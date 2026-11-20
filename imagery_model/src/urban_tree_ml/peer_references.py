"""City inventory references for unlabeled imagery review, never training admission."""
import hashlib
import json
from functools import lru_cache
from pathlib import Path


def directory(evaluation):
    evaluation=Path(evaluation)
    return evaluation.parents[3]/'city-review-references'/evaluation.parent.parent.name/evaluation.name


def version(evaluation):
    target=directory(evaluation)
    paths=[target/'reference.json',target/'ground-truth.parquet',
           Path(evaluation)/'predictions.parquet',Path(evaluation)/'taxonomy.json']
    return tuple((str(p),p.stat().st_mtime_ns,p.stat().st_size) if p.exists() else (str(p),None,None) for p in paths)


def reference(evaluation):
    return _checked(str(evaluation),version(evaluation))


@lru_cache(maxsize=32)
def _checked(evaluation, signature):
    target=directory(evaluation);receipt=target/'reference.json'
    if not receipt.exists(): return None
    value=json.loads(receipt.read_text(encoding='utf-8'))
    if value.get('policy')!='city_inventory_reference_v1' or value.get('training_eligible') is not False:
        raise ValueError('Invalid city-reference policy')
    for path,key in ((target/'ground-truth.parquet','reference_sha256'),
                     (Path(evaluation)/'predictions.parquet','predictions_sha256'),
                     (Path(evaluation)/'taxonomy.json','taxonomy_sha256')):
        if hashlib.sha256(path.read_bytes()).hexdigest()!=value[key]:
            raise ValueError('City-reference provenance changed')
    if value.get('reference_trees') == 0:
        return None
    return target/'ground-truth.parquet',value


def provisional_rows(rows):
    # Inventory presence is a starting reference, not complete aerial review.
    return [{**r,'provisional_reference':True,'prediction_only':True,
             'false_positive':None,'species_errors':None,'detection_f1':None,
             'goal_f2':None,'goal_false_positive':None,'error_score':r['missed']}
            for r in rows]

"""CPU-only equivalence checks against the original pandas matcher."""
import math
import time
import numpy as np
import pandas as pd
import pytest
from urban_tree_ml.evaluation import _greedy_matches, _average_precision


def reference(predictions, truth, radius):
    used, result = set(), []
    groups = {str(k):g for k,g in truth.groupby('chip_id',sort=False)}
    for i,p in predictions.sort_values('score',ascending=False,kind='stable').iterrows():
        best, distance = None, math.inf
        candidates=groups.get(str(p.chip_id))
        if candidates is None: continue
        for j,t in candidates.iterrows():
            if int(j) in used: continue
            d=math.hypot(float(p.output_x)-float(t.output_x),float(p.output_y)-float(t.output_y))
            if d<=radius and d<distance: best,distance=int(j),d
        if best is not None: used.add(best);result.append((int(i),best,distance))
    return result


@pytest.mark.parametrize('seed',range(8))
def test_exact_matches_and_threshold_prefix(seed):
    rng=np.random.default_rng(seed)
    p=pd.DataFrame(dict(chip_id=rng.choice(['a','b','empty'],80),output_x=rng.integers(0,12,80),
                        output_y=rng.integers(0,12,80),score=rng.choice([.2,.4,.8],80)),index=np.arange(80)*3)
    t=pd.DataFrame(dict(chip_id=rng.choice(['a','b'],30),output_x=rng.integers(0,12,30),
                        output_y=rng.integers(0,12,30)),index=np.arange(30)*2)
    for radius in [0,1,math.sqrt(2),4]:
        expected=reference(p,t,radius)
        assert _greedy_matches(p,t,radius_output_px=radius)==expected
        for threshold in [.2,.4,.8,.9]:
            subset=p[p.score>=threshold]
            assert [m for m in expected if m[0] in subset.index]==reference(subset,t,radius)
        assert _average_precision(p,t,radius_output_px=radius,matches=expected)==_average_precision(p,t,radius_output_px=radius)


def test_dense_benchmark_equivalence():
    rng=np.random.default_rng(33)
    p=pd.DataFrame(dict(chip_id=['a']*512,output_x=rng.uniform(0,128,512),output_y=rng.uniform(0,128,512),score=rng.random(512)))
    t=pd.DataFrame(dict(chip_id=['a']*100,output_x=rng.uniform(0,128,100),output_y=rng.uniform(0,128,100)))
    start=time.perf_counter();old=reference(p,t,4);slow=time.perf_counter()-start
    start=time.perf_counter();new=_greedy_matches(p,t,radius_output_px=4);fast=time.perf_counter()-start
    assert old==new
    print(f'\nExact dense benchmark: {slow:.3f}s -> {fast:.3f}s ({slow/fast:.1f}x)')

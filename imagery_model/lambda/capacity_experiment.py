"""Prepare and manage the authorized eight-hour Tiny/Small ImageNet comparison."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import tarfile

import launch_curated as launch

PAIR = 'sf-boston-nyc-naip-30cm-imagenet-capacity-v1'
RUNS = {size: PAIR+'-'+size for size in ('tiny', 'small')}
PARENT_RECIPE = 'sf-boston-nyc-naip-swin-30cm-v17-curated-finetune'
PARENT_SHA = '77b3c2db89a439a4d4aa51e9df658ea0cf80f79bb3b249ba86754830597711df'


def sha(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


def prepare():
    from prepare_three_city_curated import main as freeze_three
    from urban_tree_ml.curated_validation import load_policy
    from urban_tree_ml.capacity_run import validate_base_recipe
    # Reuse the guarded data/grid preparation, not the parent's tree weights.
    # Nothing can launch until warm-start metadata has been removed and the final
    # base-only recipes, archives and pair receipt have been verified.
    tiny = launch.ROOT/'run-inputs'/RUNS['tiny']
    small = launch.ROOT/'run-inputs'/RUNS['small']
    if tiny.exists() or small.exists():
        raise ValueError('Pair input directory exists; inspect, never silently refreeze')
    freeze_three(RUNS['tiny'], PARENT_RECIPE, PARENT_SHA, True, True)
    warm = json.loads((tiny/'warm-start.json').read_text())
    recipe = {k:v for k,v in warm.items() if k not in ('checkpoint', 'checkpoint_sha256', 'mode')}
    recipe.update(mode='imagenet_fresh_heads_fresh_optimizer_v1', pair=PAIR,
                  epochs=80, learning_rate=0.0003, early_stopping_patience_checks=12,
                  warmup_source_passes=2.0)
    recipe['model'].update(backbone='swin_tiny', pretrained=True)
    validate_base_recipe(recipe)
    save(tiny/'base-start.json', recipe)
    (tiny/'warm-start.json').unlink()
    shutil.copytree(tiny, small)
    small_recipe = json.loads((small/'base-start.json').read_text())
    small_recipe['model']['backbone'] = 'swin_small'
    save(small/'base-start.json', small_recipe)
    for size, inputs in [('tiny', tiny), ('small', small)]:
        run = RUNS[size]
        policy = json.loads((inputs/'curated-nyc-joint.json').read_text())
        # Preserve identical data bytes; only the experiment-local taxonomy path
        # and policy identity need translating for the second frozen bundle.
        if size == 'small':
            old = 'run-inputs/'+RUNS['tiny']+'/'
            new = 'run-inputs/'+run+'/'
            raw = json.dumps(policy).replace(old, new)
            policy = json.loads(raw)
        policy['experiment'] = run
        save(inputs/'curated-nyc-joint.json', policy)
        load_policy(inputs)
        from urban_tree_ml.curated_nyc_joint import validate
        validate(launch.ROOT, run)
        with tarfile.open(launch.ROOT/(run+'-inputs.tar.gz'), 'w:gz') as archive:
            archive.add(inputs, arcname=run)
    # One source archive, byte-identical for both architectures.
    launch.RUN = RUNS['tiny']
    launch.pack_source()
    shutil.copy2(launch.ROOT/(RUNS['tiny']+'-source.tar.gz'),
                 launch.ROOT/(RUNS['small']+'-source.tar.gz'))
    common = {}
    for path in tiny.rglob('*'):
        if path.is_file() and (path.relative_to(tiny).parts[0] in ('ussfo','usbos','usnyc')
                               or path.name in ('validation-policy.json','center-policy.json','loss-policy.json','nyc-split-plan.json')):
            relative = path.relative_to(tiny).as_posix()
            if sha(path) != sha(small/relative):
                raise ValueError('Pair supervision mismatch: '+relative)
            common[relative] = sha(path)
    receipt = dict(pair=PAIR, runs=RUNS, hard_instance_hours=8, initialization=recipe['mode'],
        common_input_hashes=common, test_evaluated=False, automatically_promote=False,
        runs_metadata={size: dict(inputs_sha256=sha(launch.ROOT/(run+'-inputs.tar.gz')),
            source_sha256=sha(launch.ROOT/(run+'-source.tar.gz'))) for size,run in RUNS.items()})
    save(launch.ROOT/(PAIR+'-pair.json'), receipt)
    print(json.dumps({'pair': PAIR, 'runs': RUNS, 'common_frozen_files': len(common),
                      'validation_reviewed_ids': {c:len(v['reviewed_chip_ids']) for c,v in load_policy(tiny)['cities'].items()}}), flush=True)


def verify_pair():
    from urban_tree_ml.capacity_run import validate_base_recipe
    from urban_tree_ml.curated_validation import load_policy
    receipt = json.loads((launch.ROOT/(PAIR+'-pair.json')).read_text())
    for size, run in RUNS.items():
        inputs = launch.ROOT/'run-inputs'/run
        if (inputs/'warm-start.json').exists():
            raise ValueError('Tree weights must not be inherited')
        validate_base_recipe(json.loads((inputs/'base-start.json').read_text()))
        load_policy(inputs)
        for relative, digest in receipt['common_input_hashes'].items():
            if sha(inputs/relative) != digest:
                raise ValueError('Frozen common input changed: '+relative)
        for suffix, key in [('inputs','inputs_sha256'), ('source','source_sha256')]:
            if sha(launch.ROOT/(run+'-'+suffix+'.tar.gz')) != receipt['runs_metadata'][size][key]:
                raise ValueError('Frozen archive changed: '+run)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare','verify','launch','deploy','status'])
    parser.add_argument('--size', choices=list(RUNS))
    args = parser.parse_args()
    if args.action == 'prepare':
        prepare()
        return
    if args.action == 'verify':
        verify_pair()
        print('Both base-only recipes and frozen pair archives verified')
        return
    from dotenv import load_dotenv
    load_dotenv(launch.ROOT.parents[1]/'.env', override=False)
    if args.action == 'status':
        for run in RUNS.values():
            path = launch.ROOT/(run+'-instance.json')
            if path.exists():
                info = launch.api('instances/'+json.loads(path.read_text())['instance_id'])['data']
                print(json.dumps({k:info.get(k) for k in ('id','name','status','ip')}), flush=True)
        return
    if not args.size:
        parser.error('--size required')
    verify_pair()
    launch.RUN = RUNS[args.size]
    launch.HARD_LIFETIME_HOURS = 8
    for size, run in RUNS.items():
        path = launch.ROOT/(run+'-instance.json')
        if size != args.size and path.exists():
            state = json.loads(path.read_text())
            if state['experiment'] != run:
                raise ValueError('Peer launch identity mismatch')
            info = launch.api('instances/'+state['instance_id'])['data']
            if info['name'] != run:
                raise ValueError('Peer API identity mismatch')
            launch.ALLOWED_PEER_IDS.add(state['instance_id'])
    if args.action == 'launch':
        launch.launch()
    else:
        launch.deploy()


if __name__ == '__main__':
    main()

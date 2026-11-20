"""Export accepted recombinant chips as a separate train-only dataset; never train."""
import argparse
import json
from pathlib import Path

from urban_tree_ml.config import load_config
from urban_tree_ml import recombinant as r
from urban_tree_ml.recombinant_export import export_dataset


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--source',help='Prepared photo ID; default initial library')
    parser.add_argument('--config',type=Path,required=True,help='Pinned source split, targets, taxonomy and normalization config')
    parser.add_argument('--generated-ids',nargs='+',required=True)
    parser.add_argument('--destination',type=Path,required=True,help='New directory under <root>/recombinant-exports/')
    args=parser.parse_args()
    cfg=load_config(args.config); cfg.paths.root=args.root
    result=export_dataset(r.scoped(args.root,args.source),args.generated_ids,cfg,args.destination)
    print(json.dumps(result,indent=2))


if __name__=='__main__':
    main()

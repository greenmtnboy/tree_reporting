#!/usr/bin/env bash
set -euo pipefail
: "${TREE_ML_DATA_ROOT:?Persistent artifact root required}"
: "${TREE_ML_EXPERIMENT:?Unique experiment name required}"
[[ "$TREE_ML_DATA_ROOT" == /lambda/nfs/* ]]
docker build -t urban-tree-ml:joint .
docker run --rm --gpus all --ipc=host \
  -v "$TREE_ML_DATA_ROOT:$TREE_ML_DATA_ROOT" \
  -e TREE_ML_DATA_ROOT -e "TORCH_HOME=$TREE_ML_DATA_ROOT/cache/torch" \
  --entrypoint uv urban-tree-ml:joint run --frozen --no-sync python \
  -m urban_tree_ml.joint --experiment "$TREE_ML_EXPERIMENT" \
  configs/sf_naip_citywide_curated.yaml configs/boston_naip_external.yaml

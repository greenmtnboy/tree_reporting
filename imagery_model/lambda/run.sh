#!/usr/bin/env bash
set -euo pipefail

: "${TREE_ML_DATA_ROOT:?Set TREE_ML_DATA_ROOT to an attached Lambda filesystem path}"

if [[ "${TREE_ML_DATA_ROOT}" != /lambda/nfs/* ]]; then
  echo "TREE_ML_DATA_ROOT must be under /lambda/nfs so checkpoints survive instance termination." >&2
  exit 2
fi

mkdir -p "${TREE_ML_DATA_ROOT}"/{inventory,imagery,chips,runs,cache/torch}

docker build --tag urban-tree-ml:local .
docker run --rm --gpus all \
  --ipc=host \
  --user "$(id -u):$(id -g)" \
  --volume "${TREE_ML_DATA_ROOT}:${TREE_ML_DATA_ROOT}" \
  --env TREE_ML_DATA_ROOT \
  --env "TORCH_HOME=${TREE_ML_DATA_ROOT}/cache/torch" \
  urban-tree-ml:local \
  train --config configs/sf_naip_baseline.yaml --resume auto

#!/usr/bin/env bash
set -euo pipefail

: "${TREE_ML_DATA_ROOT:?Set TREE_ML_DATA_ROOT to an attached Lambda filesystem path}"
MODEL_CONFIG="${1:-${TREE_ML_CONFIG:-configs/sf_naip_smoke.yaml}}"
RESUME_MODE="${TREE_ML_RESUME:-auto}"

if [[ "${TREE_ML_DATA_ROOT}" != /lambda/nfs/* ]]; then
  echo "TREE_ML_DATA_ROOT must be under /lambda/nfs so checkpoints survive instance termination." >&2
  exit 2
fi

if [[ ! -f "${MODEL_CONFIG}" ]]; then
  echo "Model config does not exist: ${MODEL_CONFIG}" >&2
  exit 2
fi

mkdir -p "${TREE_ML_DATA_ROOT}"/{inventory,imagery,chips,runs,cache/torch}

echo "Launching ${MODEL_CONFIG} with persistent data at ${TREE_ML_DATA_ROOT}"
docker build --tag urban-tree-ml:local .
docker run --rm --gpus all \
  --ipc=host \
  --user "$(id -u):$(id -g)" \
  --volume "${TREE_ML_DATA_ROOT}:${TREE_ML_DATA_ROOT}" \
  --env TREE_ML_DATA_ROOT \
  --env "TORCH_HOME=${TREE_ML_DATA_ROOT}/cache/torch" \
  urban-tree-ml:local \
  train --config "${MODEL_CONFIG}" --resume "${RESUME_MODE}"

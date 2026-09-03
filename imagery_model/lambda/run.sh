#!/usr/bin/env bash
set -euo pipefail

: "${TREE_ML_DATA_ROOT:?Set TREE_ML_DATA_ROOT to an attached Lambda filesystem path}"
MODEL_CONFIG="${1:-${TREE_ML_CONFIG:-configs/sf_naip_smoke.yaml}}"
RESUME_MODE="${TREE_ML_RESUME:-auto}"
PREPARE_DATA="${TREE_ML_PREPARE_DATA:-0}"

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

run_model() {
  # uv's pinned interpreter is installed under /root in the image. Keep the
  # image's default user so uv can resolve it; generated artifacts remain on
  # the isolated, persistent Lambda filesystem.
  docker run --rm --gpus all \
    --ipc=host \
    --volume "${TREE_ML_DATA_ROOT}:${TREE_ML_DATA_ROOT}" \
    --env TREE_ML_DATA_ROOT \
    --env "TORCH_HOME=${TREE_ML_DATA_ROOT}/cache/torch" \
    urban-tree-ml:local "$@"
}

if [[ "${PREPARE_DATA}" == "1" ]]; then
  : "${TREE_ML_RASTER:?Set TREE_ML_RASTER to the NAIP raster on the attached filesystem}"
  if [[ "${TREE_ML_RASTER}" != "${TREE_ML_DATA_ROOT}"/* ]] || [[ ! -f "${TREE_ML_RASTER}" ]]; then
    echo "TREE_ML_RASTER must be an existing file under TREE_ML_DATA_ROOT." >&2
    exit 2
  fi
  echo "Exporting the full detection cohort and independently masked attribute labels"
  run_model inventory export --config "${MODEL_CONFIG}"
  echo "Building chips on Lambda; finalized QA feedback is auto-detected"
  run_model chips build --config "${MODEL_CONFIG}" --raster "${TREE_ML_RASTER}"
fi

run_model train --config "${MODEL_CONFIG}" --resume "${RESUME_MODE}"

#!/usr/bin/env bash
set -euo pipefail

: "${TREE_ML_DATA_ROOT:?Set TREE_ML_DATA_ROOT to an attached Lambda filesystem path}"

MODEL_CONFIG="${1:-${TREE_ML_CONFIG:-configs/boston_naip_external.yaml}}"
STAGE="${2:-${TREE_ML_EXTERNAL_STAGE:-acquire}}"
IMAGERY_YEAR="${TREE_ML_IMAGERY_YEAR:-2023}"
REVIEW_SAMPLES="${TREE_ML_REVIEW_SAMPLES:-160}"
COHORT="${TREE_ML_COHORT:-external-usbos}"
TREE_ML_RASTER="${TREE_ML_RASTER:-${TREE_ML_DATA_ROOT}/imagery/usbos/2023/usbos-2023-external.vrt}"

if [[ "${TREE_ML_DATA_ROOT}" != /lambda/nfs/* ]]; then
  echo "TREE_ML_DATA_ROOT must be under /lambda/nfs so artifacts survive termination." >&2
  exit 2
fi
if [[ ! -f "${MODEL_CONFIG}" ]]; then
  echo "Model config does not exist: ${MODEL_CONFIG}" >&2
  exit 2
fi
if [[ "${STAGE}" != "acquire" && "${STAGE}" != "evaluate" ]]; then
  echo "Stage must be acquire or evaluate." >&2
  exit 2
fi

mkdir -p "${TREE_ML_DATA_ROOT}"/{inventory,imagery,chips,runs,qa,cache/torch}
docker build --tag urban-tree-ml:local .

run_model() {
  docker run --rm \
    --volume "${TREE_ML_DATA_ROOT}:${TREE_ML_DATA_ROOT}" \
    --env TREE_ML_DATA_ROOT \
    --env "TORCH_HOME=${TREE_ML_DATA_ROOT}/cache/torch" \
    urban-tree-ml:local "$@"
}

run_model_gpu() {
  docker run --rm --gpus all --ipc=host \
    --volume "${TREE_ML_DATA_ROOT}:${TREE_ML_DATA_ROOT}" \
    --env TREE_ML_DATA_ROOT \
    --env "TORCH_HOME=${TREE_ML_DATA_ROOT}/cache/torch" \
    urban-tree-ml:local "$@"
}

echo "Exporting external-city labels with the training-owned taxonomy"
run_model inventory export --config "${MODEL_CONFIG}"

if [[ "${STAGE}" == "acquire" ]]; then
  echo "Indexing coverage and fetching the config's pinned imagery footprint"
  run_model imagery index --config "${MODEL_CONFIG}"
  run_model imagery fetch-selected --config "${MODEL_CONFIG}"
  run_model imagery mosaic \
    --config "${MODEL_CONFIG}" \
    --year "${IMAGERY_YEAR}" \
    --output "${TREE_ML_RASTER}"

  REVIEW_DIR="${TREE_ML_DATA_ROOT}/qa/registration/$(basename "${TREE_ML_RASTER}" .vrt)"
  if [[ ! -f "${REVIEW_DIR}/manifest.json" ]]; then
    echo "Generating the initial registration review"
    run_model qa registration \
      --config "${MODEL_CONFIG}" \
      --raster "${TREE_ML_RASTER}" \
      --samples "${REVIEW_SAMPLES}"
  else
    echo "Registration manifest already exists; preserving it at ${REVIEW_DIR}"
  fi
  echo "Acquisition complete. Sync and curate ${REVIEW_DIR} before the evaluate stage."
  exit 0
fi

: "${TREE_ML_CHECKPOINT:?Set TREE_ML_CHECKPOINT to the existing SF checkpoint}"
if [[ "${TREE_ML_CHECKPOINT}" != "${TREE_ML_DATA_ROOT}"/* ]] \
  || [[ ! -f "${TREE_ML_CHECKPOINT}" ]]; then
  echo "TREE_ML_CHECKPOINT must be an existing file under TREE_ML_DATA_ROOT." >&2
  exit 2
fi
if [[ "${TREE_ML_RASTER}" != "${TREE_ML_DATA_ROOT}"/* ]] \
  || [[ ! -f "${TREE_ML_RASTER}" ]]; then
  echo "TREE_ML_RASTER must be an existing file under TREE_ML_DATA_ROOT." >&2
  exit 2
fi

echo "Building external-city chips with finalized feedback and SF normalization"
run_model chips build --config "${MODEL_CONFIG}" --raster "${TREE_ML_RASTER}"
echo "Evaluating the existing SF checkpoint as cohort ${COHORT}; no training is run"
run_model_gpu evaluate \
  --config "${MODEL_CONFIG}" \
  --checkpoint "${TREE_ML_CHECKPOINT}" \
  --split validation \
  --cohort "${COHORT}" \
  --device cuda


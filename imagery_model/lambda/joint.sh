#!/usr/bin/env bash
set -euo pipefail
: "${TREE_ML_DATA_ROOT:?Persistent artifact root required}"
: "${TREE_ML_EXPERIMENT:?Unique experiment name required}"
[[ "$TREE_ML_DATA_ROOT" == /lambda/nfs/* ]]
nvidia-smi
# Make device access explicit in the container config, not only the legacy GPU
# hook, which can lose cgroup permissions after systemd reloads (NVIDIA guidance).
devices=()
for device in /dev/nvidia[0-9]* /dev/nvidiactl /dev/nvidia-uvm /dev/nvidia-uvm-tools; do
  [[ ! -c "$device" ]] || devices+=(--device "$device")
done
docker build -t urban-tree-ml:joint .
# Fail before preprocessing unless this exact image can allocate and execute on CUDA.
docker run --rm --gpus all "${devices[@]}" --entrypoint uv urban-tree-ml:joint run --frozen --no-sync python -c \
  'import torch; assert torch.cuda.is_available(), "CUDA unavailable in training container"; x=torch.ones(32, device="cuda"); assert x.sum().item()==32; print(torch.cuda.get_device_name(), torch.__version__, flush=True)'
extra=()
entrypoint=(lambda/gpu_joint.py)
if [[ "${TREE_ML_JOB:-train}" == drift ]]; then
  entrypoint=(-m urban_tree_ml.training_drift)
fi
if [[ "${TREE_ML_JOB:-train}" == train-inference ]]; then
  entrypoint=(-m urban_tree_ml.training_inference)
fi
if [[ "${TREE_ML_JOB:-train}" == ablation ]]; then
  entrypoint=(-m urban_tree_ml.arboretum_ablation)
fi
if [[ "${TREE_ML_JOB:-train}" == backbone-poc ]]; then
  entrypoint=(-m urban_tree_ml.backbone_experiment)
fi
if [[ "${TREE_ML_JOB:-train}" == deepforest ]]; then
  entrypoint=(-m urban_tree_ml.deepforest_baseline)
fi
if [[ -n "${TREE_ML_PREPARED_RUN:-}" ]]; then
  extra=(--prepared-run "$TREE_ML_PREPARED_RUN")
fi
docker run --rm --gpus all "${devices[@]}" --ipc=host \
  --name "tree-${TREE_ML_EXPERIMENT}" \
  -v "$TREE_ML_DATA_ROOT:$TREE_ML_DATA_ROOT" \
  -e TREE_ML_DATA_ROOT -e "TORCH_HOME=$TREE_ML_DATA_ROOT/cache/torch" \
  --entrypoint uv urban-tree-ml:joint run --frozen --no-sync python \
  "${entrypoint[@]}" --experiment "$TREE_ML_EXPERIMENT" \
  "${extra[@]}" \
  configs/sf_boston_vocab_v2_sf.yaml configs/sf_boston_vocab_v2_boston.yaml

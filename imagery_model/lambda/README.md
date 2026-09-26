# Training operations and historical utilities

These scripts are operator tools. Importing or executing an operational script
can allocate a paid instance, start a job, write prepared inputs, or terminate
an instance. None is run by the test suite or by installing the Python package.
Credentials come from the environment or an external key file, never this repo.

The current supervised workflow is `launch_curated.py`, `provision_joint.py`,
`joint.sh`, `gpu_joint.py`, `supervise.py`, and `collect_run.py`. The launcher
still contains the original workstation artifact root, SSH key location, and
Lambda filesystem name: inspect and adapt these before using it on another
machine. `--prepare` is a separate operation that freezes already-published
inputs; committing or backing up code does not require running it.

| Script | Purpose and limits |
| --- | --- |
| `run.sh`, `external-city.sh` | Earlier single-city training and external-city acquisition/evaluation entrypoints. |
| `run_status.py` | Inspect a named remote run; its optional numeric probe executes on the remote host. |
| `check_frozen_portability.py` | CPU integrity check of an existing frozen input directory. |
| `refreeze_retry.py` | Re-attest an existing published freeze into a new experiment, without reading live annotations. |
| `repair_preview.py` | Explicit repair of one corrupt derived PNG; retains the original and refuses a decodable preview. |
| `recover_swin_v5.py` | Historical one-shot recovery of `sf-boston-naip-swin-v5-finetune`. Allocates an A10 on execution and requests termination afterward. Retained as operational provenance, not a reusable launcher. Do not rerun for routine backup. |
| `prepare_crown_center.py` | Historical v15-to-v16 experiment preparation; contains fixed run names and workstation paths. |
| `analyze_alignment_candidates.py` | Historical read-only v3 translation analysis with a fixed local artifact root. |

The last three historical scripts are deliberately retained to preserve the
experiment record. The result reports describe their completed runs; they are
not instructions to repeat those operations.

Related provenance is also retained in `../reports/v14_goal_scores.py` and
`../reports/v15_goal_scores.py`. Those scripts create new benchmark outputs
from historical frozen labels and contain fixed local paths. The `manual_*`,
`audit_chip_freeze.py`, and `profile_scoped_save.py` files under `../tests` are
manual fixtures or local diagnostic tools, not pytest test modules. Inspect
their paths and ports before invoking them alongside a live reviewer.

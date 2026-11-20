from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd

_SPATIAL_KEYS = (
    "image",
    "center",
    "detection_mask",
    "confirmed_background_mask",
    "scoring_mask",
    "single_tree_groups",
    "dbh_mask",
    "genus_mask",
    "species_mask",
    "dbh",
    "crown",
    "crown_mask",
    "genus",
    "species",
)


def fixed_pair_recipe(seed: int, dataset: str, chip_id: str, view: int) -> dict:
    """Versioned, portable recipe: independent of epoch, row order and worker RNG."""
    if view not in (0, 1):
        raise ValueError("fixed pair view must be 0 or 1")
    key = json.dumps(["fixed_pair_v1", seed, dataset, chip_id], separators=(",", ":"))
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    first = int.from_bytes(digest[:8], "big") % 8
    second = (first + 1 + int.from_bytes(digest[8:16], "big") % 7) % 8
    # A modest +/-0.1..0.3 stop exposure change, identical for RGB and NIR.
    magnitude = 0.1 + 0.2 * int.from_bytes(digest[16:24], "big") / (2**64 - 1)
    stops = (magnitude if digest[24] & 1 else -magnitude) if view else 0.0
    return dict(chip_id=chip_id, view=view, transform=(first, second)[view],
                exposure_stops=stops, gain=2.0**stops)


def apply_dihedral(sample: dict[str, object], transform: int) -> dict[str, object]:
    """Apply one of the eight square symmetries to every spatial tensor."""
    if transform not in range(8):
        raise ValueError("dihedral transform must be between 0 and 7")

    import torch

    rotations = transform % 4
    flip = transform >= 4
    transformed = dict(sample)
    for key in _SPATIAL_KEYS:
        if key in {"crown", "crown_mask", "single_tree_groups", "scoring_mask", "confirmed_background_mask"} and key not in transformed:
            continue
        value = transformed[key]
        if not isinstance(value, torch.Tensor):
            raise TypeError(f"sample field {key!r} must be a tensor")
        value = torch.rot90(value, rotations, dims=(-2, -1))
        if flip:
            value = torch.flip(value, dims=(-1,))
        transformed[key] = value.contiguous()
    return transformed


class NpzChipDataset:
    """Torch-compatible dataset kept importable without the optional train group."""

    def __init__(
        self,
        manifest_path: str | Path,
        split: str,
        *,
        random_dihedral: bool = False,
        augmentation: str = "legacy",
        seed: int = 0,
        require_confirmed_background_mask: bool = False,
    ) -> None:
        try:
            import torch
        except ImportError as error:  # pragma: no cover
            raise RuntimeError(
                "Install the train dependency group: uv sync --group train"
            ) from error
        self._torch = torch
        self.root = Path(manifest_path).parent
        normalization = json.loads((self.root / "normalization.json").read_text(encoding="utf-8"))
        self.mean = torch.tensor(normalization["mean"], dtype=torch.float32)[:, None, None]
        self.std = torch.tensor(normalization["std"], dtype=torch.float32)[:, None, None]
        manifest = pd.read_parquet(manifest_path)
        self.rows = manifest[manifest["split"] == split].reset_index(drop=True)
        if augmentation not in ("legacy", "fixed_pair_v1"):
            raise ValueError("Unknown augmentation recipe")
        if augmentation != "legacy" and random_dihedral:
            raise ValueError("Fixed recipes cannot also use random_dihedral")
        self.random_dihedral = random_dihedral and split == "train"
        self.fixed_pair = augmentation == "fixed_pair_v1" and split == "train"
        self.seed = seed
        self.require_confirmed_background_mask = require_confirmed_background_mask
        self.dataset = self.root.name

    def __getstate__(self):
        # Windows DataLoader workers use spawn; module objects cannot be pickled.
        return {k: v for k, v in self.__dict__.items() if k != "_torch"}

    def __setstate__(self, state):
        import torch
        self.__dict__.update(state)
        self._torch = torch

    def augmentation_recipe(self, index: int) -> dict:
        row_index, view = divmod(index, 2)
        return fixed_pair_recipe(self.seed, self.dataset,
                                 str(self.rows.iloc[row_index]["chip_id"]), view)

    def __len__(self) -> int:
        return len(self.rows) * (2 if self.fixed_pair else 1)

    def __getitem__(self, index: int) -> dict[str, object]:
        import numpy as np

        if index < 0 or index >= len(self):
            raise IndexError(index)
        recipe = self.augmentation_recipe(index) if self.fixed_pair else None
        row = self.rows.iloc[index // 2 if self.fixed_pair else index]
        with np.load(self.root / row["path"]) as chip:
            if self.require_confirmed_background_mask and 'confirmed_background_mask' not in chip:
                raise ValueError('Rebuild chips with explicit confirmed-background masks before weighted training')
            image = self._torch.from_numpy(chip["image"].copy()).float()
            if recipe is not None and recipe["view"] == 1:
                # Chips store scaled [0, 1] intensities. Adjust before normalization;
                # zero/nodata stays zero, and every band receives the same gain.
                image = (image * recipe["gain"]).clamp(0, 1)
            sample = {
                "chip_id": row["chip_id"],
                "image": (image - self.mean) / self.std,
                "center": self._torch.from_numpy(chip["center"].copy()).float(),
                "detection_mask": self._torch.from_numpy(chip["detection_mask"].copy()).float(),
                "confirmed_background_mask": self._torch.from_numpy(
                    chip['confirmed_background_mask'].copy() if 'confirmed_background_mask' in chip
                    else np.zeros_like(chip['center'])).float(),
                "scoring_mask": self._torch.from_numpy(chip['scoring_mask'].copy() if 'scoring_mask' in chip else chip['detection_mask'].copy()).float(),
                "single_tree_groups": self._torch.from_numpy(chip['single_tree_groups'].copy() if 'single_tree_groups' in chip else np.zeros_like(chip['center'], dtype=np.int32)).long(),
                "dbh_mask": self._torch.from_numpy(chip["dbh_mask"].copy()).bool(),
                "genus_mask": self._torch.from_numpy(chip["genus_mask"].copy()).bool(),
                "species_mask": self._torch.from_numpy(chip["species_mask"].copy()).bool(),
                "dbh": self._torch.from_numpy(chip["dbh"].copy()).float(),
                "crown": self._torch.from_numpy(chip["crown"].copy() if "crown" in chip else np.zeros_like(chip["dbh"])).float(),
                "crown_mask": self._torch.from_numpy(chip["crown_mask"].copy() if "crown_mask" in chip else np.zeros_like(chip["dbh_mask"])).float(),
                "genus": self._torch.from_numpy(chip["genus"].copy()).long(),
                "species": self._torch.from_numpy(chip["species"].copy()).long(),
            }
        if recipe is not None:
            sample = apply_dihedral(sample, recipe["transform"])
        elif self.random_dihedral:
            transform = int(self._torch.randint(0, 8, ()).item())
            sample = apply_dihedral(sample, transform)
        return sample

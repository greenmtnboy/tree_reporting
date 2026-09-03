from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

_SPATIAL_KEYS = (
    "image",
    "center",
    "detection_mask",
    "dbh_mask",
    "genus_mask",
    "species_mask",
    "dbh",
    "genus",
    "species",
)


def apply_dihedral(sample: dict[str, object], transform: int) -> dict[str, object]:
    """Apply one of the eight square symmetries to every spatial tensor."""
    if transform not in range(8):
        raise ValueError("dihedral transform must be between 0 and 7")

    import torch

    rotations = transform % 4
    flip = transform >= 4
    transformed = dict(sample)
    for key in _SPATIAL_KEYS:
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
        self.random_dihedral = random_dihedral

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, object]:
        import numpy as np

        row = self.rows.iloc[index]
        with np.load(self.root / row["path"]) as chip:
            sample = {
                "chip_id": row["chip_id"],
                "image": (self._torch.from_numpy(chip["image"].copy()).float() - self.mean)
                / self.std,
                "center": self._torch.from_numpy(chip["center"].copy()).float(),
                "detection_mask": self._torch.from_numpy(chip["detection_mask"].copy()).float(),
                "dbh_mask": self._torch.from_numpy(chip["dbh_mask"].copy()).bool(),
                "genus_mask": self._torch.from_numpy(chip["genus_mask"].copy()).bool(),
                "species_mask": self._torch.from_numpy(chip["species_mask"].copy()).bool(),
                "dbh": self._torch.from_numpy(chip["dbh"].copy()).float(),
                "genus": self._torch.from_numpy(chip["genus"].copy()).long(),
                "species": self._torch.from_numpy(chip["species"].copy()).long(),
            }
        if self.random_dihedral:
            transform = int(self._torch.randint(0, 8, ()).item())
            sample = apply_dihedral(sample, transform)
        return sample

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd


class NpzChipDataset:
    """Torch-compatible dataset kept importable without the optional train group."""

    def __init__(self, manifest_path: str | Path, split: str) -> None:
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

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, object]:
        import numpy as np

        row = self.rows.iloc[index]
        with np.load(self.root / row["path"]) as chip:
            return {
                "chip_id": row["chip_id"],
                "image": (self._torch.from_numpy(chip["image"].copy()).float() - self.mean)
                / self.std,
                "center": self._torch.from_numpy(chip["center"].copy()).float(),
                "detection_mask": self._torch.from_numpy(chip["detection_mask"].copy()).float(),
                "attribute_mask": self._torch.from_numpy(chip["attribute_mask"].copy()).bool(),
                "dbh": self._torch.from_numpy(chip["dbh"].copy()).float(),
                "genus": self._torch.from_numpy(chip["genus"].copy()).long(),
                "species": self._torch.from_numpy(chip["species"].copy()).long(),
            }

from __future__ import annotations

import torch
from torch import nn
from torch.nn import functional as functional
from torchvision.models import ResNet34_Weights, resnet34


class ConvBlock(nn.Sequential):
    def __init__(self, input_channels: int, output_channels: int) -> None:
        super().__init__(
            nn.Conv2d(input_channels, output_channels, 3, padding=1, bias=False),
            nn.BatchNorm2d(output_channels),
            nn.ReLU(inplace=True),
        )


class RawImageryTreeModel(nn.Module):
    """Stride-2 dense point detector with DBH, genus, and species heads."""

    def __init__(
        self,
        *,
        input_channels: int,
        feature_channels: int,
        genus_classes: int,
        species_classes: int,
        pretrained: bool = True,
        crown_head: bool = False,
        backbone: str = 'resnet34',
    ) -> None:
        super().__init__()
        from urban_tree_ml.backbones import HierarchicalEncoder, adapt_input
        self.backbone_name = backbone
        if backbone == 'resnet34':
            encoder = resnet34(weights=ResNet34_Weights.DEFAULT if pretrained else None)
            encoder.conv1 = adapt_input(encoder.conv1, input_channels)
            # Retain legacy parameter paths so existing checkpoints load strictly.
            self.stem = nn.Sequential(encoder.conv1, encoder.bn1, encoder.relu)
            self.maxpool = encoder.maxpool
            self.layer1, self.layer2 = encoder.layer1, encoder.layer2
            self.layer3, self.layer4 = encoder.layer3, encoder.layer4
            widths = (64,64,128,256,512)
        else:
            self.encoder = HierarchicalEncoder(backbone,input_channels,pretrained)
            widths = self.encoder.channels

        channels = feature_channels
        self.lateral0 = nn.Conv2d(widths[0], channels, 1)
        self.lateral1 = nn.Conv2d(widths[1], channels, 1)
        self.lateral2 = nn.Conv2d(widths[2], channels, 1)
        self.lateral3 = nn.Conv2d(widths[3], channels, 1)
        self.lateral4 = nn.Conv2d(widths[4], channels, 1)
        self.smooth3 = ConvBlock(channels, channels)
        self.smooth2 = ConvBlock(channels, channels)
        self.smooth1 = ConvBlock(channels, channels)
        self.smooth0 = ConvBlock(channels, channels)

        self.center_head = self._head(channels, 1, bias=-2.19)
        self.dbh_head = self._head(channels, 1)
        self.crown_head = self._head(channels, 1) if crown_head else None
        self.genus_head = self._head(channels, genus_classes)
        self.species_head = self._head(channels, species_classes)

    @staticmethod
    def _head(input_channels: int, output_channels: int, bias: float = 0.0) -> nn.Sequential:
        head = nn.Sequential(
            ConvBlock(input_channels, input_channels),
            nn.Conv2d(input_channels, output_channels, 1),
        )
        nn.init.constant_(head[-1].bias, bias)
        return head

    @staticmethod
    def _up_add(deep: torch.Tensor, lateral: torch.Tensor) -> torch.Tensor:
        return (
            functional.interpolate(
                deep, size=lateral.shape[-2:], mode="bilinear", align_corners=False
            )
            + lateral
        )

    def _encode(self, image: torch.Tensor) -> tuple[torch.Tensor, ...]:
        """Backbone features at strides 2, 4, 8, 16, 32."""
        if self.backbone_name == 'resnet34':
            stem = self.stem(image)  # stride 2
            layer1 = self.layer1(self.maxpool(stem))  # stride 4
            layer2 = self.layer2(layer1)  # stride 8
            layer3 = self.layer3(layer2)  # stride 16
            layer4 = self.layer4(layer3)  # stride 32
            return stem, layer1, layer2, layer3, layer4
        return tuple(self.encoder(image))

    def _heads(self, pyramid: torch.Tensor) -> dict[str, torch.Tensor]:
        output = {
            "center_logits": self.center_head(pyramid).squeeze(1),
            "dbh_log1p": self.dbh_head(pyramid).squeeze(1),
            "genus_logits": self.genus_head(pyramid),
            "species_logits": self.species_head(pyramid),
        }
        if self.crown_head is not None:
            output["crown_log1p"] = self.crown_head(pyramid).squeeze(1)
        return output

    def forward(self, image: torch.Tensor) -> dict[str, torch.Tensor]:
        stem, layer1, layer2, layer3, layer4 = self._encode(image)
        pyramid = self.lateral4(layer4)
        pyramid = self.smooth3(self._up_add(pyramid, self.lateral3(layer3)))
        pyramid = self.smooth2(self._up_add(pyramid, self.lateral2(layer2)))
        pyramid = self.smooth1(self._up_add(pyramid, self.lateral1(layer1)))
        pyramid = self.smooth0(self._up_add(pyramid, self.lateral0(stem)))
        return self._heads(pyramid)

    def forward_region(self, image: torch.Tensor, start: int, size: int) -> dict[str, torch.Tensor]:
        """``forward(image)`` for output cells ``[start:start + size]`` on both
        axes only: the same values, at a fraction of the cost.

        The backbone and the coarse pyramid see the whole image, as in
        ``forward``.  The stride-4 and stride-2 layers -- most of a forward
        pass, five heads of 3x3 convolutions at full output resolution -- are
        local, so they run on the region plus the few cells their kernels
        reach (the image's own border where the region touches it, so the
        zero padding there is ``forward``'s).  Bulk prediction keeps the
        middle half of each context window and discards the rest.
        """
        stem, layer1, layer2, layer3, layer4 = self._encode(image)
        pyramid = self.lateral4(layer4)
        pyramid = self.smooth3(self._up_add(pyramid, self.lateral3(layer3)))
        pyramid = self.smooth2(self._up_add(pyramid, self.lateral2(layer2)))
        merged = self._up_add(pyramid, self.lateral1(layer1))  # stride 4, before smooth1
        if stem.shape[-2] != stem.shape[-1] or stem.shape[-1] != 2 * merged.shape[-1]:
            raise ValueError("forward_region needs a square image whose side is a multiple of 4")
        fine, coarse = stem.shape[-1], merged.shape[-1]
        if not 0 <= start <= start + size <= fine:
            raise ValueError(f"cells {start}:{start + size} are not inside the {fine}-cell output")
        # Stride-2 cells smooth0 must see for the heads to be right on the region
        # (one 3x3 each), the stride-4 cells whose upsampling covers those without
        # touching the crop's clamped edge, and the cells smooth1 must see for them.
        a, b = max(0, start - 2), min(fine, start + size + 2)
        c0, c1 = max(0, a // 2 - 1), min(coarse, (b + 1) // 2 + 1)
        d0, d1 = max(0, c0 - 1), min(coarse, c1 + 1)
        level1 = self.smooth1(merged[..., d0:d1, d0:d1])[..., c0 - d0:c1 - d0, c0 - d0:c1 - d0]
        up = functional.interpolate(
            level1, size=(2 * (c1 - c0),) * 2, mode="bilinear", align_corners=False
        )[..., a - 2 * c0:b - 2 * c0, a - 2 * c0:b - 2 * c0]
        level0 = self.smooth0(up + self.lateral0(stem[..., a:b, a:b]))
        return {
            key: value[..., start - a:start - a + size, start - a:start - a + size]
            for key, value in self._heads(level0).items()
        }

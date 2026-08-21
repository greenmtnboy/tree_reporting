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
    ) -> None:
        super().__init__()
        weights = ResNet34_Weights.DEFAULT if pretrained else None
        encoder = resnet34(weights=weights)
        if input_channels != 3:
            original = encoder.conv1
            replacement = nn.Conv2d(
                input_channels,
                original.out_channels,
                kernel_size=original.kernel_size,
                stride=original.stride,
                padding=original.padding,
                bias=False,
            )
            with torch.no_grad():
                replacement.weight[:, :3] = original.weight
                if input_channels > 3:
                    mean_weight = original.weight.mean(dim=1, keepdim=True)
                    replacement.weight[:, 3:] = mean_weight.expand(-1, input_channels - 3, -1, -1)
            encoder.conv1 = replacement

        self.stem = nn.Sequential(encoder.conv1, encoder.bn1, encoder.relu)
        self.maxpool = encoder.maxpool
        self.layer1 = encoder.layer1
        self.layer2 = encoder.layer2
        self.layer3 = encoder.layer3
        self.layer4 = encoder.layer4

        channels = feature_channels
        self.lateral0 = nn.Conv2d(64, channels, 1)
        self.lateral1 = nn.Conv2d(64, channels, 1)
        self.lateral2 = nn.Conv2d(128, channels, 1)
        self.lateral3 = nn.Conv2d(256, channels, 1)
        self.lateral4 = nn.Conv2d(512, channels, 1)
        self.smooth3 = ConvBlock(channels, channels)
        self.smooth2 = ConvBlock(channels, channels)
        self.smooth1 = ConvBlock(channels, channels)
        self.smooth0 = ConvBlock(channels, channels)

        self.center_head = self._head(channels, 1, bias=-2.19)
        self.dbh_head = self._head(channels, 1)
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

    def forward(self, image: torch.Tensor) -> dict[str, torch.Tensor]:
        stem = self.stem(image)  # stride 2
        layer1 = self.layer1(self.maxpool(stem))  # stride 4
        layer2 = self.layer2(layer1)  # stride 8
        layer3 = self.layer3(layer2)  # stride 16
        layer4 = self.layer4(layer3)  # stride 32

        pyramid = self.lateral4(layer4)
        pyramid = self.smooth3(self._up_add(pyramid, self.lateral3(layer3)))
        pyramid = self.smooth2(self._up_add(pyramid, self.lateral2(layer2)))
        pyramid = self.smooth1(self._up_add(pyramid, self.lateral1(layer1)))
        pyramid = self.smooth0(self._up_add(pyramid, self.lateral0(stem)))
        return {
            "center_logits": self.center_head(pyramid).squeeze(1),
            "dbh_log1p": self.dbh_head(pyramid).squeeze(1),
            "genus_logits": self.genus_head(pyramid),
            "species_logits": self.species_head(pyramid),
        }

"""Backbone adapters return NCHW features at strides 2, 4, 8, 16, 32."""
import torch
from torch import nn
from torchvision.models import (ConvNeXt_Tiny_Weights, Swin_T_Weights,
                                convnext_tiny, swin_t)


def adapt_input(original, input_channels):
    if input_channels == 3:
        return original
    replacement = nn.Conv2d(input_channels, original.out_channels,
                            original.kernel_size, original.stride, original.padding,
                            bias=original.bias is not None)
    with torch.no_grad():
        replacement.weight[:, :3].copy_(original.weight)
        replacement.weight[:, 3:].copy_(original.weight.mean(1, keepdim=True).expand(
            -1, input_channels - 3, -1, -1))
        if original.bias is not None:
            replacement.bias.copy_(original.bias)
    return replacement


class HierarchicalEncoder(nn.Module):
    channels = (64, 96, 192, 384, 768)

    def __init__(self, name, input_channels, pretrained):
        super().__init__()
        self.name = name
        if name == 'convnext_tiny':
            model = convnext_tiny(weights=ConvNeXt_Tiny_Weights.DEFAULT if pretrained else None)
        elif name == 'swin_tiny':
            model = swin_t(weights=Swin_T_Weights.DEFAULT if pretrained else None)
        else:
            raise ValueError(f'Unsupported hierarchical backbone: {name}')
        self.features = model.features
        self.features[0][0] = adapt_input(self.features[0][0], input_channels)
        # Preserve fine spatial evidence, rather than merely upsampling stride 4.
        self.detail = nn.Sequential(nn.Conv2d(input_channels,64,3,stride=2,padding=1,bias=False),
                                    nn.BatchNorm2d(64),nn.ReLU(inplace=True))

    def forward(self, image):
        output = [self.detail(image)]
        feature = image
        for index, layer in enumerate(self.features):
            feature = layer(feature)
            if index in (1, 3, 5, 7):
                output.append(feature.permute(0,3,1,2).contiguous()
                              if self.name == 'swin_tiny' else feature)
        return output

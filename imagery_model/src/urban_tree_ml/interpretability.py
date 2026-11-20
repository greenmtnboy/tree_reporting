"""Native detection-head inspection, separate from training and model promotion.

For the final 1x1 convolution, weight * activation is an exact additive logit
contribution. It is not input-pixel attribution or a semantic feature label.
"""
import torch
from torch import nn


def inspect_center_head(network, image):
    if network.training:
        raise ValueError('Interpretability requires eval mode')
    head = network.center_head[-1]
    if not isinstance(head, nn.Conv2d) or head.kernel_size != (1, 1) or head.out_channels != 1:
        raise ValueError('Expected a single-output linear 1x1 detection readout')
    captured = []
    hook = head.register_forward_pre_hook(lambda module, args: captured.append(args[0].detach().clone()))
    try:
        with torch.inference_mode():
            logits = network(image)['center_logits'].detach()
    finally:
        hook.remove()
    if len(captured) != 1:
        raise ValueError('Expected exactly one readout invocation')
    features = captured[0]
    weights = head.weight.detach().flatten()
    bias = head.bias.detach()[0] if head.bias is not None else logits.new_tensor(0.)
    contributions = features * weights[None, :, None, None]
    reconstructed = contributions.sum(1) + bias
    if not all(torch.isfinite(x).all() for x in (features, logits, contributions)):
        raise ValueError('Non-finite activations')
    torch.testing.assert_close(reconstructed, logits, rtol=1e-5, atol=1e-5)
    # Independently execute the actual readout with one channel zeroed. This
    # validates the interactive slider without another backbone forward pass.
    channel = int(contributions.abs().amax((0, 2, 3)).argmax())
    altered = features.clone()
    altered[:, channel] = 0
    with torch.inference_mode():
        actual = head(altered).squeeze(1)
    predicted = logits - contributions[:, channel]
    torch.testing.assert_close(actual, predicted, rtol=1e-5, atol=1e-5)
    return dict(features=features, contributions=contributions, logits=logits,
                weights=weights, bias=float(bias),
                reconstruction_max_error=float((reconstructed-logits).abs().max()),
                ablation_max_error=float((actual-predicted).abs().max()),
                ablation_checked_channel=channel)


def sample_points(logits, kernel=5, limit=24):
    """Use the production deterministic peak tie-breaking, but retain low scores."""
    from urban_tree_ml.evaluation import _select_peak_indices
    probabilities = logits.sigmoid()
    pooled = torch.nn.functional.max_pool2d(probabilities[None, None], kernel,
                                          stride=1, padding=kernel//2)[0, 0]
    locations = torch.nonzero(probabilities.eq(pooled), as_tuple=False)
    scores = probabilities[locations[:, 0], locations[:, 1]]
    chosen = _select_peak_indices(locations.cpu().numpy(), scores.cpu().numpy(),
                                  shape=tuple(logits.shape), limit=limit, nms_kernel=kernel)
    return locations[chosen]

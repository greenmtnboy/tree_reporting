"""Read-only species-head evidence at supervised centers, independent of detection."""
import torch
from torch import nn


def inspect_species_centers(network, image, y, x):
    if network.training:
        raise ValueError('Species inspection requires eval mode')
    head = network.species_head[-1]
    if not isinstance(head, nn.Conv2d) or head.kernel_size != (1, 1) or head.groups != 1:
        raise ValueError('Expected a linear 1x1 species readout')
    captured = []
    hook = head.register_forward_pre_hook(lambda module, args: captured.append(args[0].detach().clone()))
    try:
        with torch.inference_mode():
            output = network(image)
    finally:
        hook.remove()
    if len(captured) != 1 or image.shape[0] != 1:
        raise ValueError('Expected one image and one readout invocation')
    features = captured[0][0, :, y, x].T
    logits = output['species_logits'][0, :, y, x].T
    weights = head.weight.detach()[:, :, 0, 0]
    bias = head.bias.detach() if head.bias is not None else logits.new_zeros(head.out_channels)
    reconstructed = features @ weights.T + bias
    if not all(torch.isfinite(v).all() for v in (features, logits, weights, bias)):
        raise ValueError('Non-finite species evidence')
    torch.testing.assert_close(reconstructed, logits, atol=1e-5, rtol=1e-5)
    # Compare actual layer execution to removing a channel from every class logit.
    channel = int(features.abs().mean(0).argmax())
    altered = features.clone(); altered[:, channel] = 0
    with torch.inference_mode():
        actual = head(altered.T[None, :, :, None])[0, :, :, 0].T
    predicted = logits - features[:, channel, None] * weights[None, :, channel]
    torch.testing.assert_close(actual, predicted, atol=1e-5, rtol=1e-5)
    return dict(features=features, logits=logits, weights=weights, bias=bias,
                reconstruction_error=float((reconstructed-logits).abs().max()),
                zeroing_error=float((actual-predicted).abs().max()))


def species_summary(rows, names):
    """Every supported species stays visible, including zero errors / zero support."""
    result = []
    for index, name in enumerate(names):
        if name.startswith('__') or name.lower() in {'unknown', 'palm', 'tree', 'other'}:
            continue
        group = [r for r in rows if r['truth'] == index]
        n = len(group)
        result.append(dict(id=index, name=name, n=n,
            correct=sum(r['predicted'] == index for r in group),
            top5=sum(index in r['top5'] for r in group),
            tiles=len({(r['city'], r['chip']) for r in group})))
    return result

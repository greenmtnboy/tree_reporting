import pytest

torch = pytest.importorskip("torch")

from urban_tree_ml.losses import masked_centernet_focal_loss  # noqa: E402
from urban_tree_ml.training import require_finite_losses  # noqa: E402


@pytest.mark.parametrize("dtype", [torch.float32, torch.bfloat16, torch.float16])
@pytest.mark.parametrize("masked", [False, True])
def test_extreme_logits_have_finite_loss_and_gradients(dtype, masked):
    logits = torch.tensor([-1000., -8., 0., 8., 1000.], dtype=dtype, requires_grad=True)
    target = torch.tensor([1., 0., 0.5, 0., 1.])
    mask = torch.zeros(5) if masked else torch.ones(5)
    loss = masked_centernet_focal_loss(logits, target, mask)
    assert torch.isfinite(loss)
    loss.backward()
    assert torch.isfinite(logits.grad).all()
    if masked:
        assert loss.item() == 0
        assert logits.grad.eq(0).all()


def test_matches_original_formula_in_nonsaturated_range():
    logits = torch.tensor([-3., -1., 0., 1., 3.], requires_grad=True)
    target = torch.tensor([1., 0., .5, 0., 1.])
    mask = torch.tensor([1., 1., 0., 1., 1.])
    p = logits.sigmoid()
    expected = ((-p.log() * (1-p)**2 * target.eq(1)
                 -(1-p).log() * p**2 * (1-target)**4 * target.lt(1)) * mask).sum() / 2
    actual = masked_centernet_focal_loss(logits, target, mask)
    torch.testing.assert_close(actual, expected)
    torch.testing.assert_close(torch.autograd.grad(actual, logits, retain_graph=True)[0],
                               torch.autograd.grad(expected, logits)[0])


@pytest.mark.parametrize("value", [float("inf"), float("nan"), -float("inf")])
def test_nonfinite_loss_fails_instead_of_successful_early_stop(value):
    with pytest.raises(FloatingPointError, match="Nonfinite validation losses: center_loss"):
        require_finite_losses({"center_loss": torch.tensor(value)}, "validation")
    require_finite_losses({"center_loss": torch.tensor(1.)}, "validation")

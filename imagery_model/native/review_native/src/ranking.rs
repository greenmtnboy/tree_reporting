use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;

fn read_row<const N: usize>(row: &[u8]) -> [f64; N] {
    std::array::from_fn(|i| f64::from_le_bytes(row[8 * i..8 * i + 8].try_into().unwrap()))
}

fn validate_length(data: &[u8], counts: impl Iterator<Item = usize>, stride: usize) -> bool {
    counts
        .fold(Some(0usize), |total, n| total?.checked_add(n))
        .and_then(|n| n.checked_mul(stride))
        == Some(data.len())
}

/// Predictions arrive in stable descending score order and truth in file order.
/// The first unused truth at the minimum eligible distance wins an exact tie.
#[pyfunction]
pub fn match_chips(
    py: Python<'_>,
    predictions: &[u8],
    truth: &[u8],
    counts: Vec<(usize, usize)>,
    radius: f64,
) -> PyResult<Vec<(usize, usize, usize)>> {
    if !radius.is_finite()
        || radius <= 0.0
        || !validate_length(predictions, counts.iter().map(|c| c.0), 32)
        || !validate_length(truth, counts.iter().map(|c| c.1), 24)
    {
        return Err(PyValueError::new_err(
            "Invalid ranking buffer lengths or radius",
        ));
    }
    Ok(py.detach(|| {
        let mut predictions = predictions.chunks_exact(32);
        let mut truth = truth.chunks_exact(24);
        counts
            .into_iter()
            .map(|(np, nt)| {
                let trees: Vec<_> = truth.by_ref().take(nt).map(read_row::<3>).collect();
                let mut used = vec![false; nt];
                let (mut matched, mut wrong, mut ignored) = (0, 0, 0);
                for row in predictions.by_ref().take(np) {
                    let [x, y, species, mask] = read_row::<4>(row);
                    let mut nearest = None;
                    let mut best = f64::INFINITY;
                    for (i, &[tx, ty, _]) in trees.iter().enumerate() {
                        let (dx, dy) = (tx - x, ty - y);
                        if used[i] || dx.abs() > radius || dy.abs() > radius {
                            continue;
                        }
                        let distance = dx.hypot(dy);
                        if distance <= radius && distance < best {
                            nearest = Some(i);
                            best = distance;
                        }
                    }
                    if let Some(i) = nearest {
                        used[i] = true;
                        matched += 1;
                        if trees[i][2] >= 0.0 && !species.is_nan() && species != trees[i][2] {
                            wrong += 1;
                        }
                    } else if mask.is_finite() && mask <= 0.0 {
                        ignored += 1;
                    }
                }
                (matched, wrong, ignored)
            })
            .collect()
    }))
}

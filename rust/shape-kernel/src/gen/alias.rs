//! Vose alias tables and the sampler on top of the row-addressed stream.
//!
//! The build is deterministic and uses only IEEE `+ - * /`, so the Python twin reproduces it bit
//! for bit. A draw takes two words from its row: the first picks a column
//! (`below(w0, n)`), the second decides between the column and its alias (`unit(w1) < prob`).

use super::rng::{below, for_row_chunks, unit};

/// Vose's alias method. Weights must be finite, non-negative and not all zero.
pub fn build(weights: &[f64]) -> Result<(Vec<f64>, Vec<i64>), String> {
    let n = weights.len();
    if n == 0 {
        return Err("alias_build needs at least one weight".into());
    }
    let mut total = 0.0f64;
    for &w in weights {
        if !w.is_finite() || w < 0.0 {
            return Err("alias weights must be finite and non-negative".into());
        }
        total += w;
    }
    if total <= 0.0 || !total.is_finite() {
        return Err("alias weights must have a positive finite sum".into());
    }
    let nf = n as f64;
    let mut scaled: Vec<f64> = weights.iter().map(|&w| w * nf / total).collect();
    let mut prob = vec![0.0f64; n];
    let mut alias: Vec<i64> = (0..n as i64).collect();
    let mut small: Vec<usize> = Vec::new();
    let mut large: Vec<usize> = Vec::new();
    for (i, &s) in scaled.iter().enumerate() {
        if s < 1.0 {
            small.push(i);
        } else {
            large.push(i);
        }
    }
    while !small.is_empty() && !large.is_empty() {
        let s = small.pop().unwrap_or(0);
        let l = large.pop().unwrap_or(0);
        prob[s] = scaled[s];
        alias[s] = l as i64;
        scaled[l] = (scaled[l] + scaled[s]) - 1.0;
        if scaled[l] < 1.0 {
            small.push(l);
        } else {
            large.push(l);
        }
    }
    for l in large {
        prob[l] = 1.0;
        alias[l] = l as i64;
    }
    for s in small {
        prob[s] = 1.0;
        alias[s] = s as i64;
    }
    Ok((prob, alias))
}

#[inline(always)]
pub fn pick(prob: &[f64], alias: &[i64], w0: u64, w1: u64) -> i64 {
    let i = below(w0, prob.len() as u64) as usize;
    if unit(w1) < prob[i] {
        i as i64
    } else {
        alias[i]
    }
}

/// `n_rows` draws for rows `row_start ..`, reading words `slot` and `slot + 1` of each row's
/// `per_row` words.
pub fn sample(
    prob: &[f64],
    alias: &[i64],
    key: [u64; 2],
    row_start: u64,
    n_rows: usize,
    per_row: usize,
    slot: usize,
) -> Vec<i64> {
    let mut out = vec![0i64; n_rows];
    for_row_chunks(key, row_start, per_row, &mut out, true, &|words, chunk| {
        for (r, o) in chunk.iter_mut().enumerate() {
            let b = r * per_row + slot;
            *o = pick(prob, alias, words[b], words[b + 1]);
        }
    });
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn build_conserves_probability() {
        let w = [0.5, 0.25, 0.125, 0.125, 0.0];
        let (prob, alias) = build(&w).unwrap();
        let n = w.len() as f64;
        let mut mass = vec![0.0; w.len()];
        for i in 0..w.len() {
            mass[i] += prob[i] / n;
            mass[alias[i] as usize] += (1.0 - prob[i]) / n;
        }
        for (m, e) in mass.iter().zip(w.iter()) {
            assert!((m - e).abs() < 1e-12, "{m} vs {e}");
        }
    }

    #[test]
    fn rejects_bad_weights() {
        assert!(build(&[]).is_err());
        assert!(build(&[0.0, 0.0]).is_err());
        assert!(build(&[1.0, -1.0]).is_err());
        assert!(build(&[1.0, f64::NAN]).is_err());
    }
}

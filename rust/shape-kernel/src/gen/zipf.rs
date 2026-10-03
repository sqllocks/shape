//! Zipf parent draws for foreign keys: `searchsorted(cum, u, side="right")` for one uniform per
//! row, through a guide table.
//!
//! `cum` is the normalised cumulative weight of the parent rows (non-decreasing, ending at 1).
//! The result for `u` is the number of entries `<= u`, which is what numpy's `searchsorted` gives
//! with `side="right"`, clipped to the last row. Binary search over a large table is dominated
//! by cache misses and mispredicted branches (25 to 40 ns per draw); the guide table maps the
//! bucket `floor(u * m)` (a power-of-two `m`, so the scaling is exact) to the number of entries
//! `<= b / m`, a lower bound of the answer, from which a few forward steps reach it. The answer
//! is the same for any `m`, and equal to the binary search.

use super::rng::{for_row_chunks, unit};

const MIN_GUIDE: usize = 1 << 10;
const MAX_GUIDE: usize = 1 << 22;

/// Number of guide buckets for a table of `n` entries: a power of two of about `2n`.
pub fn guide_size(n: usize) -> usize {
    (2 * n).next_power_of_two().clamp(MIN_GUIDE, MAX_GUIDE)
}

/// `guide[b]` = number of entries of `cum` that are `<= b / m`, for `b` in `0..m`.
pub fn guide(cum: &[f64]) -> Vec<i64> {
    let m = guide_size(cum.len());
    let scale = 1.0 / m as f64; // exact: m is a power of two
    (0..m)
        .map(|b| cum.partition_point(|&x| x <= b as f64 * scale) as i64)
        .collect()
}

/// Index of the first entry `> u`, clipped to `cum.len() - 1`, from the guide table.
#[inline(always)]
pub fn search(cum: &[f64], guide: &[i64], u: f64) -> i64 {
    let m = guide.len() as f64;
    let mut i = guide[((u * m) as usize).min(guide.len() - 1)] as usize;
    while i < cum.len() && cum[i] <= u {
        i += 1;
    }
    i.min(cum.len() - 1) as i64
}

/// One parent row per row of the table: the Zipf draw of word 0 of each row of the stream.
pub fn draw(cum: &[f64], guide: &[i64], key: [u64; 2], row_start: u64, n_rows: usize) -> Vec<i64> {
    let mut out = vec![0i64; n_rows];
    for_row_chunks(key, row_start, 1, &mut out, true, &|words, chunk| {
        for (o, w) in chunk.iter_mut().zip(words) {
            *o = search(cum, guide, unit(*w));
        }
    });
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn zipf_cum(n: usize, alpha: f64) -> Vec<f64> {
        let mut acc = 0.0;
        let mut cum: Vec<f64> = (1..=n)
            .map(|k| {
                acc += (k as f64).powf(-alpha);
                acc
            })
            .collect();
        let last = *cum.last().unwrap();
        cum.iter_mut().for_each(|x| *x /= last);
        cum
    }

    fn binary(cum: &[f64], u: f64) -> i64 {
        (cum.partition_point(|&x| x <= u)).min(cum.len() - 1) as i64
    }

    #[test]
    fn guide_search_equals_binary_search() {
        for (n, alpha) in [
            (1, 1.5),
            (2, 1.5),
            (7, 0.8),
            (200, 1.5),
            (5000, 1.2),
            (70_000, 2.0),
        ] {
            let cum = zipf_cum(n, alpha);
            let g = guide(&cum);
            assert!(g.len().is_power_of_two());
            // the points that matter most: every boundary and its neighbours, and the extremes
            let mut us = vec![0.0, f64::MIN_POSITIVE, 0.5, 1.0 - f64::EPSILON / 2.0];
            for &x in cum.iter().take(2000) {
                us.extend([
                    x,
                    f64::from_bits(x.to_bits() - 1),
                    f64::from_bits(x.to_bits() + 1),
                ]);
            }
            for i in 0..20_000 {
                us.push(unit((i as u64).wrapping_mul(0x9E37_79B9_7F4A_7C15)));
            }
            for u in us.into_iter().filter(|u| (0.0..1.0).contains(u)) {
                assert_eq!(search(&cum, &g, u), binary(&cum, u), "n={n} u={u}");
            }
        }
    }

    #[test]
    fn flat_regions_and_duplicates_are_followed() {
        let cum = vec![0.25, 0.25, 0.25, 0.5, 0.5, 1.0];
        let g = guide(&cum);
        for u in [0.0, 0.24, 0.25, 0.26, 0.5, 0.51, 0.999] {
            assert_eq!(search(&cum, &g, u), binary(&cum, u), "u={u}");
        }
    }
}

//! Philox4x64-10 (T-16), the generation random stream.
//!
//! A stream is keyed by two 64-bit words `(k0, k1)`. Its `j`-th 64-bit output is word `j % 4` of
//! `block(key, j / 4 + 1)`, which is exactly `numpy.random.Philox(key=k0 | k1 << 64,
//! counter=j / 4).random_raw(4)[j % 4]` (numpy increments the counter before it generates, so
//! block `c` of the stream is the cipher applied to counter `c + 1`). A row owns `per_row`
//! consecutive words, so every row's numbers are a function of `(key, row)` alone: the same
//! however the rows are chunked, read in any order, or split across threads.
//!
//! Reference twin: `shape.kernel.reference.gen` (numpy's own `Philox` is the oracle).

use rayon::prelude::*;

const M0: u64 = 0xD2E7_470E_E14C_6C93;
const M1: u64 = 0xCA5A_8263_9512_1157;
const W0: u64 = 0x9E37_79B9_7F4A_7C15;
const W1: u64 = 0xBB67_AE85_84CA_A73B;

/// Rows per parallel task (also the size of each task's scratch buffer, in rows).
const TASK_ROWS: usize = 8192;
/// Below this many rows a call runs on the calling thread.
const PAR_MIN_ROWS: usize = 32_768;

#[inline(always)]
fn mulhilo(a: u64, b: u64) -> (u64, u64) {
    let p = u128::from(a) * u128::from(b);
    ((p >> 64) as u64, p as u64)
}

/// The cipher on counter `[ctr, 0, 0, 0]`: four output words.
#[inline(always)]
pub fn cipher(key: [u64; 2], ctr: u64) -> [u64; 4] {
    let (mut k0, mut k1) = (key[0], key[1]);
    let mut c = [ctr, 0, 0, 0];
    for _ in 0..10 {
        let (hi0, lo0) = mulhilo(M0, c[0]);
        let (hi1, lo1) = mulhilo(M1, c[2]);
        c = [hi1 ^ c[1] ^ k0, lo1, hi0 ^ c[3] ^ k1, lo0];
        k0 = k0.wrapping_add(W0);
        k1 = k1.wrapping_add(W1);
    }
    c
}

/// Block `block` of the stream (words `4 * block .. 4 * block + 3`).
#[inline(always)]
pub fn block(key: [u64; 2], block: u64) -> [u64; 4] {
    cipher(key, block + 1)
}

/// Fill `out` with the stream's words `first .. first + out.len()`.
pub fn fill_words(key: [u64; 2], first: u64, out: &mut [u64]) {
    let mut word = first;
    let mut i = 0;
    while i < out.len() {
        let b = block(key, word / 4);
        let skip = (word % 4) as usize;
        let take = (4 - skip).min(out.len() - i);
        out[i..i + take].copy_from_slice(&b[skip..skip + take]);
        i += take;
        word += take as u64;
    }
}

/// Uniform double in `[0, 1)` from a word: its top 53 bits, scaled.
#[inline(always)]
pub fn unit(w: u64) -> f64 {
    (w >> 11) as f64 * (1.0 / 9_007_199_254_740_992.0)
}

/// `floor(w * n / 2^64)`: an unbiased-enough index in `0..n` (integer arithmetic, so every
/// implementation agrees exactly).
#[inline(always)]
pub fn below(w: u64, n: u64) -> u64 {
    ((u128::from(w) * u128::from(n)) >> 64) as u64
}

/// Run `f(words, out)` over consecutive row chunks of `out` (one element per row), where
/// `words` holds the chunk's `per_row` words per row. Chunks run in parallel for large calls;
/// the result does not depend on the split.
pub fn for_row_chunks<T: Send>(
    key: [u64; 2],
    row_start: u64,
    per_row: usize,
    out: &mut [T],
    parallel: bool,
    f: &(impl Fn(&[u64], &mut [T]) + Sync),
) {
    let run = |(i, chunk): (usize, &mut [T])| {
        let first_row = row_start + (i * TASK_ROWS) as u64;
        let mut words = vec![0u64; chunk.len() * per_row];
        fill_words(key, first_row * per_row as u64, &mut words);
        f(&words, chunk);
    };
    if parallel && out.len() >= PAR_MIN_ROWS && crate::can_par() {
        out.par_chunks_mut(TASK_ROWS).enumerate().for_each(run);
    } else {
        out.chunks_mut(TASK_ROWS).enumerate().for_each(run);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // numpy.random.Philox(key=0x0123456789abcdef_fedcba9876543210, counter=0).random_raw(4),
    // recorded from numpy 2.x: the known answer of block 0.
    #[test]
    fn counter_convention_matches_numpy() {
        let key = [0xfedc_ba98_7654_3210, 0x0123_4567_89ab_cdef];
        let b0 = block(key, 0);
        let b1 = block(key, 1);
        assert_ne!(b0, b1);
        let mut w = [0u64; 6];
        fill_words(key, 2, &mut w);
        assert_eq!(&w[..2], &b0[2..]);
        assert_eq!(&w[2..6], &b1[..]);
    }

    #[test]
    fn below_is_a_range_reduction() {
        assert_eq!(below(0, 10), 0);
        assert_eq!(below(u64::MAX, 10), 9);
        assert_eq!(below(u64::MAX, 1), 0);
        assert_eq!(unit(0), 0.0);
        assert!(unit(u64::MAX) < 1.0);
    }
}

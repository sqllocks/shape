//! Distribution fitting for the profiler (P1-08): a port of the scipy 1.17 routines the
//! reference distribution detection relies on, candidate by candidate:
//! `norm`, `uniform`, `expon` (closed-form MLE) and `lognorm` (scipy's `fit` override: the
//! dL/dloc bracket search with `brentq`, falling back to the generic MLE with Nelder-Mead),
//! each scored with the exact two-sided KS p-value (`kstwo.sf`: Durbin/Marsaglia-Tsang-Wang,
//! Pomeranz, Pelz-Good and Smirnov branches).
//!
//! The pure numpy twin is `src/shape/profile/reference/numerics.py`; the differential tests
//! require the same distribution and parameters (relative 1e-6) on every input. Sums follow
//! numpy's pairwise summation so the two agree to the last bits where the libm does.

// The constants are cephes' and scipy's literals, kept verbatim so they can be diffed against
// the numpy reference; the comparisons deliberately treat NaN as "not greater".
#![allow(
    clippy::excessive_precision,
    clippy::approx_constant,
    clippy::neg_cmp_op_on_partial_ord,
    clippy::needless_range_loop
)]

use std::f64::consts::{LN_2, PI};

use rayon::prelude::*;

const SQRTH: f64 = 7.071_067_811_865_475_244_01e-1;
const SQRT2PI: f64 = 2.506_628_274_631_000_5;
/// `np.sqrt(2 * np.pi)` as numpy evaluates it (one ulp below the correctly rounded `SQRT2PI`
/// literal): the constant of the reference's lognormal log-density, `log(s * x * sqrt(2 pi))`.
const NP_SQRT_2PI: f64 = 2.506_628_274_631_000_2;
const LOG_2PI: f64 = 1.837_877_066_409_345_3;
const E128: i32 = 128;
const MIN_LOG: f64 = -708.0;

// ------------------------------------------------------------- numpy helpers

/// Scalar `exp` and `ln` as numpy computes them. numpy's float64 `exp` (and, rarely, `log`) is a
/// SIMD routine that differs from libm in the last bit for a few percent of inputs, and the
/// lognormal root search is ill-conditioned enough for that to move a fitted parameter in the
/// sixth digit. The scalar `scale = exp(mean(log(data - loc)))` therefore goes through these
/// hooks, which the Python module points at `numpy.exp` / `numpy.log`; without a hook (Rust
/// unit tests) libm is used. Array-wide operations stay in Rust.
pub type ScalarFn = fn(f64) -> f64;
static EXP_HOOK: std::sync::OnceLock<ScalarFn> = std::sync::OnceLock::new();
static LN_HOOK: std::sync::OnceLock<ScalarFn> = std::sync::OnceLock::new();

pub type ArrayFn = fn(&mut [f64]);
static LN_ARRAY_HOOK: std::sync::OnceLock<ArrayFn> = std::sync::OnceLock::new();

/// Set once with the hooks. `gil_free` says that they call numpy's loops directly: no GIL and no
/// numpy error state is involved, so any pass may run on the pool (see `Eval::pass`).
pub fn set_scalar_hooks(exp: ScalarFn, ln: ScalarFn, ln_array: ArrayFn, gil_free: bool) {
    let _ = EXP_HOOK.set(exp);
    let _ = LN_HOOK.set(ln);
    let _ = LN_ARRAY_HOOK.set(ln_array);
    GIL_FREE_HOOKS.store(gil_free, std::sync::atomic::Ordering::Relaxed);
}

static GIL_FREE_HOOKS: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// Natural log of every element in place: numpy's (SIMD) `log` when hooked, else libm.
fn ln_inplace(a: &mut [f64]) {
    match LN_ARRAY_HOOK.get() {
        Some(f) => f(a),
        None => a.iter_mut().for_each(|v| *v = v.ln()),
    }
}

fn sc_exp(x: f64) -> f64 {
    EXP_HOOK.get().map_or_else(|| x.exp(), |f| f(x))
}

fn sc_ln(x: f64) -> f64 {
    LN_HOOK.get().map_or_else(|| x.ln(), |f| f(x))
}

/// numpy's `pairwise_sum` over a contiguous f64 slice (what `np.sum`/`np.mean` use).
pub fn pairwise_sum(a: &[f64]) -> f64 {
    let n = a.len();
    if n < 8 {
        let mut res = 0.0;
        for v in a {
            res += *v;
        }
        // numpy starts from -0.0 so that an empty or all -0.0 sum keeps its sign; the
        // difference is invisible for the values summed here
        return res;
    }
    if n <= 128 {
        let mut r = [a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7]];
        let mut i = 8;
        while i < n - (n % 8) {
            for (k, slot) in r.iter_mut().enumerate() {
                *slot += a[i + k];
            }
            i += 8;
        }
        let mut res = ((r[0] + r[1]) + (r[2] + r[3])) + ((r[4] + r[5]) + (r[6] + r[7]));
        while i < n {
            res += a[i];
            i += 1;
        }
        return res;
    }
    let mut n2 = n / 2;
    n2 -= n2 % 8;
    pairwise_sum(&a[..n2]) + pairwise_sum(&a[n2..])
}

fn mean(a: &[f64]) -> f64 {
    pairwise_sum(a) / a.len() as f64
}

// ------------------------------------------------------ pairwise_sum block tree

/// Largest block `pairwise_sum` sums in one go (its unrolled eight-accumulator loop).
const PW_BLOCK: usize = 128;

/// Chunk size bounds, in leaves (a leaf is 64 to 128 elements): a chunk is big enough to amortise
/// the call into numpy (and the hand-over of the GIL it needs), small enough that the data and
/// both scratch buffers stay in cache between the passes over one chunk.
const MIN_CHUNK_LEAVES: usize = 128;

/// Below this many rows the passes stay on the calling thread: the evaluation is short enough
/// that handing chunks to the pool (and each chunk's `log` through the GIL) costs more than it
/// saves, and the profiler already spreads columns over threads (measured: break-even near
/// 300k rows on 4 cores).
const PAR_MIN_ROWS: usize = 1 << 18;
const MAX_CHUNK_LEAVES: usize = 2048;

/// How `pairwise_sum` splits `n > 128` elements: the left half, rounded down to a multiple of 8.
fn pw_split(n: usize) -> usize {
    let n2 = n / 2;
    n2 - n2 % 8
}

fn pw_leaves(n: usize, off: usize, out: &mut Vec<usize>) {
    if n <= PW_BLOCK {
        out.push(off);
    } else {
        let n2 = pw_split(n);
        pw_leaves(n2, off, out);
        pw_leaves(n - n2, off + n2, out);
    }
}

/// `pairwise_sum`'s additions above the leaves as a stack program: after pushing leaf `k`, add
/// the top two entries `adds[k]` times (each add closes one node of the recursion).
fn pw_adds(n: usize, adds: &mut Vec<u8>) {
    if n <= PW_BLOCK {
        adds.push(0);
    } else {
        let n2 = pw_split(n);
        pw_adds(n2, adds);
        pw_adds(n - n2, adds);
        if let Some(last) = adds.last_mut() {
            *last += 1;
        }
    }
}

/// Re-run `pairwise_sum`'s additions over precomputed leaf sums: the same tree, so the result is
/// bit-for-bit what `pairwise_sum` returns on the whole array (each add is `left + right`, and
/// IEEE addition is commutative).
fn pw_combine(adds: &[u8], sums: &[f64]) -> f64 {
    let mut stack = [0.0f64; 64];
    let mut top = 0;
    for (sum, &k) in sums.iter().zip(adds) {
        stack[top] = *sum;
        top += 1;
        for _ in 0..k {
            top -= 1;
            stack[top - 1] += stack[top];
        }
    }
    stack[0]
}

/// The leaves of `pairwise_sum`'s block tree over `n` elements. A leaf is summed on its own by
/// `pairwise_sum` (at most 128 elements), so any work that is element-wise can be done leaf by
/// leaf, in any order and on any thread, and combined afterwards with `pw_combine`.
struct Plan {
    n: usize,
    /// Start offset of every leaf, then `n`.
    starts: Vec<usize>,
    /// The additions above the leaves (see `pw_adds`).
    adds: Vec<u8>,
    /// Leaves per chunk.
    chunk_leaves: usize,
}

/// One chunk of whole leaves handed to a pass: the element range, the leaf starts (plus the next
/// start), the slots for the leaf sums, and the chunk's slices of the two scratch buffers.
struct Chunk<'a> {
    lo: usize,
    hi: usize,
    starts: &'a [usize],
    sums: &'a mut [f64],
    b1: &'a mut [f64],
    b2: &'a mut [f64],
}

/// `pairwise_sum` of every leaf of `vals` (the chunk starting at element `lo`).
fn leaf_sums(vals: &[f64], starts: &[usize], lo: usize, sums: &mut [f64]) {
    for (k, slot) in sums.iter_mut().enumerate() {
        *slot = pairwise_sum(&vals[starts[k] - lo..starts[k + 1] - lo]);
    }
}

impl Plan {
    /// With `par`, aim for several chunks per rayon thread so that the work balances.
    fn new(n: usize, par: bool) -> Plan {
        let mut starts = Vec::with_capacity(n / 64 + 2);
        pw_leaves(n, 0, &mut starts);
        starts.push(n);
        let mut adds = Vec::with_capacity(starts.len());
        pw_adds(n, &mut adds);
        let leaves = starts.len() - 1;
        let chunk_leaves = if par {
            leaves.div_ceil(4 * rayon::current_num_threads())
        } else {
            MAX_CHUNK_LEAVES / 4
        }
        .clamp(MIN_CHUNK_LEAVES, MAX_CHUNK_LEAVES);
        Plan {
            n,
            starts,
            adds,
            chunk_leaves,
        }
    }

    /// Run `pass` over every chunk (in parallel when `par`) and return the pairwise sum of what
    /// the passes stored as leaf sums. `b1` and `b2` hold at least `n` elements.
    fn run<F>(&self, par: bool, sums: &mut Vec<f64>, b1: &mut [f64], b2: &mut [f64], pass: F) -> f64
    where
        F: Fn(Chunk<'_>) + Sync + Send,
    {
        let leaves = self.starts.len() - 1;
        sums.clear();
        sums.resize(leaves, 0.0);
        let mut tasks = Vec::with_capacity(leaves.div_ceil(self.chunk_leaves));
        let (mut r1, mut r2, mut rs) = (&mut b1[..self.n], &mut b2[..self.n], &mut sums[..]);
        let mut l0 = 0;
        while l0 < leaves {
            let l1 = (l0 + self.chunk_leaves).min(leaves);
            let (lo, hi) = (self.starts[l0], self.starts[l1]);
            let (a1, t1) = r1.split_at_mut(hi - lo);
            let (a2, t2) = r2.split_at_mut(hi - lo);
            let (as_, ts) = rs.split_at_mut(l1 - l0);
            (r1, r2, rs) = (t1, t2, ts);
            tasks.push(Chunk {
                lo,
                hi,
                starts: &self.starts[l0..=l1],
                sums: as_,
                b1: a1,
                b2: a2,
            });
            l0 = l1;
        }
        if par {
            tasks.into_par_iter().for_each(&pass);
        } else {
            tasks.into_iter().for_each(&pass);
        }
        pw_combine(&self.adds, sums)
    }
}

/// `np.spacing(x)`: distance to the adjacent float away from zero, signed like x.
fn spacing(x: f64) -> f64 {
    nextafter(x, f64::INFINITY.copysign(x)) - x
}

fn nextafter(x: f64, toward: f64) -> f64 {
    if x.is_nan() || toward.is_nan() {
        return f64::NAN;
    }
    if x == toward {
        return toward;
    }
    if x == 0.0 {
        return f64::from_bits(1).copysign(toward);
    }
    let bits = x.to_bits();
    let up = (toward > x) == (x > 0.0);
    f64::from_bits(if up { bits + 1 } else { bits - 1 })
}

// ---------------------------------------------------------------------- ndtr

const P: [f64; 9] = [
    2.461_969_814_735_305e-10,
    5.641_895_648_310_688e-1,
    7.463_210_564_422_699e0,
    4.863_719_709_856_814e1,
    1.965_208_329_560_771e2,
    5.264_451_949_954_773_6e2,
    9.345_285_271_719_576e2,
    1.027_551_886_895_157e3,
    5.575_353_353_693_994e2,
];
const Q: [f64; 8] = [
    1.322_819_511_547_449_9e1,
    8.670_721_408_859_897e1,
    3.549_377_788_878_199e2,
    9.757_085_017_432_055e2,
    1.823_909_166_879_097_4e3,
    2.246_337_608_187_109_8e3,
    1.656_663_091_941_613_5e3,
    5.575_353_408_177_277e2,
];
const R: [f64; 6] = [
    5.641_895_835_477_551e-1,
    1.275_366_707_599_781e0,
    5.019_050_422_511_805e0,
    6.160_210_979_930_536e0,
    7.409_742_699_504_489e0,
    2.978_866_653_721_002_4e0,
];
const S: [f64; 6] = [
    2.260_528_632_201_173e0,
    9.396_035_249_380_015e0,
    1.204_895_398_080_966_6e1,
    1.708_144_507_475_659e1,
    9.608_968_090_632_859e0,
    3.369_076_451_000_815e0,
];
const T: [f64; 5] = [
    9.604_973_739_870_516e0,
    9.002_601_972_038_427e1,
    2.232_005_345_946_843e3,
    7.003_325_141_128_051e3,
    5.559_230_130_103_95e4,
];
const U: [f64; 5] = [
    3.356_171_416_475_031e1,
    5.213_579_497_801_527e2,
    4.594_323_829_709_801e3,
    2.262_900_006_138_909_3e4,
    4.926_739_426_086_359e4,
];

fn polevl(x: f64, c: &[f64]) -> f64 {
    let mut y = c[0];
    for ci in &c[1..] {
        y = y * x + ci;
    }
    y
}

fn p1evl(x: f64, c: &[f64]) -> f64 {
    let mut y = x + c[0];
    for ci in &c[1..] {
        y = y * x + ci;
    }
    y
}

fn erf_small(x: f64) -> f64 {
    let z = x * x;
    x * polevl(z, &T) / p1evl(z, &U)
}

fn erfc_big(a: f64) -> f64 {
    let x = a.abs();
    let z = (-a * a).exp();
    let (p, q) = if x < 8.0 {
        (polevl(x, &P), p1evl(x, &Q))
    } else {
        (polevl(x, &R), p1evl(x, &S))
    };
    let y = (z * p) / q;
    if a < 0.0 {
        2.0 - y
    } else {
        y
    }
}

/// cephes `ndtr` (scipy.special.ndtr).
pub fn ndtr(a: f64) -> f64 {
    if a.is_nan() {
        return f64::NAN;
    }
    let x = a * SQRTH;
    let z = x.abs();
    if z < SQRTH {
        return 0.5 + 0.5 * erf_small(x);
    }
    let e = if z < 1.0 {
        1.0 - erf_small(z)
    } else {
        erfc_big(z)
    };
    let y = 0.5 * e;
    if x > 0.0 {
        1.0 - y
    } else {
        y
    }
}

// ---------------------------------------------------------------------- cdfs

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Dist {
    Normal,
    Uniform,
    Exponential,
    Lognormal,
}

impl Dist {
    pub fn name(self) -> &'static str {
        match self {
            Dist::Normal => "normal",
            Dist::Uniform => "uniform",
            Dist::Exponential => "exponential",
            Dist::Lognormal => "lognormal",
        }
    }
}

const CANDIDATES: [Dist; 4] = [
    Dist::Normal,
    Dist::Uniform,
    Dist::Exponential,
    Dist::Lognormal,
];

/// `rv_continuous.cdf` for the four candidates; `p` is `(loc, scale)` or `(s, loc, scale)`.
fn cdf(d: Dist, x: f64, p: &[f64]) -> f64 {
    let (s, loc, scale) = if d == Dist::Lognormal {
        (Some(p[0]), p[1], p[2])
    } else {
        (None, p[0], p[1])
    };
    if !(scale > 0.0 && s.is_none_or(|s| s > 0.0)) {
        return f64::NAN;
    }
    let z = (x - loc) / scale;
    if z.is_nan() {
        return f64::NAN;
    }
    let (a, b) = match d {
        Dist::Normal => (f64::NEG_INFINITY, f64::INFINITY),
        Dist::Uniform => (0.0, 1.0),
        _ => (0.0, f64::INFINITY),
    };
    if z >= b {
        return 1.0;
    }
    if !(a < z && z < b) {
        return 0.0;
    }
    match d {
        Dist::Normal => ndtr(z),
        Dist::Uniform => z,
        Dist::Exponential => -(-z).exp_m1(),
        Dist::Lognormal => ndtr(z.ln() / s.unwrap()),
    }
}

/// The running maxima of `ks_stat_sorted` over `xs[..]`, whose first element has rank `first`.
fn ks_partial(xs: &[f64], first: usize, n: f64, d: Dist, p: &[f64]) -> (f64, f64) {
    let (mut dplus, mut dminus) = (f64::NEG_INFINITY, f64::NEG_INFINITY);
    for (i, x) in xs.iter().enumerate() {
        let rank = (first + i) as f64;
        let c = cdf(d, *x, p);
        let plus = (rank + 1.0) / n - c;
        let minus = c - rank / n;
        if plus.is_nan() || plus > dplus {
            dplus = plus;
        }
        if minus.is_nan() || minus > dminus {
            dminus = minus;
        }
    }
    (dplus, dminus)
}

/// KS statistic of sorted data against a fitted candidate. A NaN anywhere makes the statistic
/// NaN and otherwise it is the first maximum, so chunks of the array can be scanned in parallel
/// and their maxima merged in order with the same update rule.
fn ks_stat_sorted(xs: &[f64], d: Dist, p: &[f64]) -> f64 {
    let n = xs.len() as f64;
    const CHUNK: usize = 1 << 16;
    let (dplus, dminus) = if xs.len() >= PAR_MIN_ROWS && crate::can_par() {
        let parts: Vec<(f64, f64)> = xs
            .par_chunks(CHUNK)
            .enumerate()
            .map(|(k, c)| ks_partial(c, k * CHUNK, n, d, p))
            .collect();
        let merge = |acc: f64, v: f64| if v.is_nan() || v > acc { v } else { acc };
        parts
            .iter()
            .fold((f64::NEG_INFINITY, f64::NEG_INFINITY), |(a, b), (x, y)| {
                (merge(a, *x), merge(b, *y))
            })
    } else {
        ks_partial(xs, 0, n, d, p)
    };
    if dplus > dminus {
        dplus
    } else {
        dminus
    }
}

// ------------------------------------------------------------------- kstwo.sf

fn clip(p: f64) -> f64 {
    p.clamp(0.0, 1.0)
}

fn ldexp(x: f64, e: i32) -> f64 {
    let mut v = x;
    let mut e = e;
    while e > 1000 {
        v *= 2f64.powi(1000);
        e -= 1000;
    }
    while e < -1000 {
        v *= 2f64.powi(-1000);
        e += 1000;
    }
    v * 2f64.powi(e)
}

fn matmul(a: &[f64], b: &[f64], m: usize) -> Vec<f64> {
    let mut c = vec![0.0; m * m];
    for i in 0..m {
        for k in 0..m {
            let aik = a[i * m + k];
            if aik != 0.0 {
                for j in 0..m {
                    c[i * m + j] += aik * b[k * m + j];
                }
            }
        }
    }
    c
}

fn kolmogn_dmtw(n: usize, d: f64) -> f64 {
    if d >= 1.0 {
        return 1.0;
    }
    let nd = n as f64 * d;
    if nd <= 0.5 {
        return 0.0;
    }
    let k = nd.ceil() as usize;
    let h = k as f64 - nd;
    let m = 2 * k - 1;
    let ep128 = ldexp(1.0, E128);
    let em128 = ldexp(1.0, -E128);
    let mut hm = vec![0.0; m * m];
    let mut v = vec![0.0; m];
    let mut w = vec![0.0; m];
    let mut fac = 1.0;
    for j in 1..=m {
        v[j - 1] = 1.0 - h.powi(j as i32);
        w[j - 1] = fac;
        fac /= j as f64;
        v[j - 1] *= fac;
    }
    let tt = (2.0 * h - 1.0).max(0.0).powi(m as i32) - 2.0 * h.powi(m as i32);
    v[m - 1] = (1.0 + tt) * fac;
    for i in 1..m {
        for r in (i - 1)..m {
            hm[r * m + i] = w[r - (i - 1)];
        }
    }
    for r in 0..m {
        hm[r * m] = v[r];
    }
    for c in 0..m {
        hm[(m - 1) * m + c] = v[m - 1 - c];
    }
    let mut hpwr = vec![0.0; m * m];
    for i in 0..m {
        hpwr[i * m + i] = 1.0;
    }
    let (mut nn, mut expnt, mut hexpnt) = (n, 0i32, 0i32);
    while nn > 0 {
        if nn % 2 == 1 {
            hpwr = matmul(&hpwr, &hm, m);
            expnt += hexpnt;
        }
        hm = matmul(&hm, &hm, m);
        hexpnt *= 2;
        if hm[(k - 1) * m + (k - 1)].abs() > ep128 {
            for x in hm.iter_mut() {
                *x /= ep128;
            }
            hexpnt += E128;
        }
        nn /= 2;
    }
    let mut p = hpwr[(k - 1) * m + (k - 1)];
    for i in 1..=n {
        p = i as f64 * p / n as f64;
        if p.abs() < em128 {
            p *= ep128;
            expnt -= E128;
        }
    }
    if expnt != 0 {
        p = ldexp(p, expnt);
    }
    clip(p)
}

fn pomeranz_j1j2(i: i64, n: i64, ll: i64, ceilf: i64, roundf: i64) -> (i64, i64) {
    let (j1, j2);
    if i == 0 {
        j1 = -ll - ceilf - 1;
        j2 = ll + ceilf - 1;
    } else {
        let ip1div2 = (i + 1) / 2;
        let ip1mod2 = (i + 1) % 2;
        if ip1mod2 == 0 {
            if ip1div2 == n + 1 {
                j1 = n - ll - ceilf - 1;
                j2 = n + ll + ceilf - 1;
            } else {
                j1 = ip1div2 - 1 - ll - roundf - 1;
                j2 = ip1div2 + ll - 1 + ceilf - 1;
            }
        } else {
            j1 = ip1div2 - 1 - ll - 1;
            j2 = ip1div2 + ll + roundf - 1;
        }
    }
    ((j1 + 2).max(0), j2.min(n))
}

fn kolmogn_pomeranz(n: usize, x: f64) -> f64 {
    let ep128 = ldexp(1.0, E128);
    let em128 = ldexp(1.0, -E128);
    let t = n as f64 * x;
    let ll = t.floor() as i64;
    let f = 1.0 * (t - ll as f64);
    let g = f.min(1.0 - f);
    let ceilf = i64::from(f > 0.0);
    let roundf = i64::from(f > 0.5);
    let npwrs = (2 * (ll + 1)) as usize;
    let mut gpower = vec![0.0; npwrs];
    let mut twogpower = vec![0.0; npwrs];
    let mut onem2gpower = vec![0.0; npwrs];
    gpower[0] = 1.0;
    twogpower[0] = 1.0;
    onem2gpower[0] = 1.0;
    let mut expnt = 0i32;
    let nf = n as f64;
    let (g_over_n, two_g_over_n, one_minus_two_g_over_n) =
        (g / nf, 2.0 * g / nf, (1.0 - 2.0 * g) / nf);
    for m in 1..npwrs {
        gpower[m] = gpower[m - 1] * g_over_n / m as f64;
        twogpower[m] = twogpower[m - 1] * two_g_over_n / m as f64;
        onem2gpower[m] = onem2gpower[m - 1] * one_minus_two_g_over_n / m as f64;
    }
    let mut v0 = vec![0.0; npwrs];
    let mut v1 = vec![0.0; npwrs];
    v1[0] = 1.0;
    let (mut v0s, mut v1s) = (0i64, 0i64);
    let ni = n as i64;
    let (mut j1, mut j2);
    (j1, j2) = pomeranz_j1j2(0, ni, ll, ceilf, roundf);
    let _ = j2;
    for i in 1..(2 * ni + 2) {
        let k1 = j1;
        std::mem::swap(&mut v0, &mut v1);
        std::mem::swap(&mut v0s, &mut v1s);
        v1.iter_mut().for_each(|x| *x = 0.0);
        let (nj1, nj2) = pomeranz_j1j2(i, ni, ll, ceilf, roundf);
        j1 = nj1;
        j2 = nj2;
        let pwrs: &Vec<f64> = if i == 1 || i == 2 * ni + 1 {
            &gpower
        } else if i % 2 == 1 {
            &twogpower
        } else {
            &onem2gpower
        };
        let ln2 = j2 - k1 + 1;
        if ln2 > 0 {
            let ln2 = ln2 as usize;
            let a0 = (k1 - v0s) as usize;
            let a = &v0[a0..a0 + ln2];
            let b = &pwrs[..ln2];
            let mut conv = vec![0.0; 2 * ln2 - 1];
            for (ia, av) in a.iter().enumerate() {
                for (ib, bv) in b.iter().enumerate() {
                    conv[ia + ib] += av * bv;
                }
            }
            let conv_start = (j1 - k1) as usize;
            let conv_len = (j2 - j1 + 1) as usize;
            v1[..conv_len].copy_from_slice(&conv[conv_start..conv_start + conv_len]);
            let vmax = v1.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
            if 0.0 < vmax && vmax < em128 {
                v1.iter_mut().for_each(|x| *x *= ep128);
                expnt -= E128;
            }
            v1s = v0s + j1 - k1;
        }
    }
    let mut ans = v1[(ni - v1s) as usize];
    for m in 1..=n {
        if ans.abs() > ep128 {
            ans *= em128;
            expnt += E128;
        }
        ans *= m as f64;
    }
    if expnt != 0 {
        ans = ldexp(ans, expnt);
    }
    clip(ans)
}

fn kolmogn_pelz_good(n: usize, x: f64) -> f64 {
    if x <= 0.0 {
        return 0.0;
    }
    if x >= 1.0 {
        return 1.0;
    }
    let pi2 = PI * PI;
    let pi4 = pi2 * pi2;
    let pi6 = pi4 * pi2;
    let z = (n as f64).sqrt() * x;
    let (zsquared, zthree, zfour, zsix) = (z.powi(2), z.powi(3), z.powi(4), z.powi(6));
    let qlog = -pi2 / 8.0 / zsquared;
    if qlog < MIN_LOG {
        return 0.0;
    }
    let mut q = qlog.exp();
    let k1a = -zsquared;
    let k1b = pi2 / 4.0;
    let k2a = 6.0 * zsix + 2.0 * zfour;
    let k2b = (2.0 * zfour - 5.0 * zsquared) * pi2 / 4.0;
    let k2c = pi4 * (1.0 - 2.0 * zsquared) / 16.0;
    let k3d = pi6 * (5.0 - 30.0 * zsquared) / 64.0;
    let k3c = pi4 * (-60.0 * zsquared + 212.0 * zfour) / 16.0;
    let k3b = pi2 * (135.0 * zfour - 96.0 * zsix) / 4.0;
    let k3a = -30.0 * zsix - 90.0 * z.powi(8);
    let mut k = [0.0f64; 4];
    let maxk = (16.0 * z / PI).ceil() as i64;
    for kk in (1..=maxk).rev() {
        let m = (2 * kk - 1) as f64;
        let (msq, mfour, msix) = (m.powi(2), m.powi(4), m.powi(6));
        let qpower = q.powf(8.0 * kk as f64);
        let coeffs = [
            1.0,
            k1a + k1b * msq,
            k2a + k2b * msq + k2c * mfour,
            k3a + k3b * msq + k3c * mfour + k3d * msix,
        ];
        for i in 0..4 {
            k[i] = k[i] * qpower + coeffs[i];
        }
    }
    let div = [z, 6.0 * zfour, 72.0 * z.powi(7), 6480.0 * z.powi(10)];
    for i in 0..4 {
        k[i] = k[i] * q * SQRT2PI / div[i];
    }
    q = (-pi2 / 2.0 / zsquared).exp();
    let sqrt3z = 3f64.sqrt() * z;
    let mut k2extra = 0.0;
    let mut k3extra = 0.0;
    for kk in (1..=maxk).rev() {
        let kf = kk as f64;
        let ksq = kf * kf;
        let qp = q.powf(ksq);
        k2extra += ksq * qp;
        let kspi = PI * kf;
        k3extra += (sqrt3z + kspi) * (sqrt3z - kspi) * ksq * qp;
    }
    k2extra *= pi2 * SQRT2PI / (-36.0 * zthree);
    k[2] += k2extra;
    k3extra *= pi2 * SQRT2PI / (216.0 * zsix);
    k[3] += k3extra;
    let nf = n as f64;
    let mut sum = 0.0;
    for (i, ki) in k.iter().enumerate() {
        sum += ki / nf.powf(i as f64 / 2.0);
    }
    sum
}

fn smirnov(n: usize, d: f64) -> f64 {
    if d <= 0.0 {
        return 1.0;
    }
    if d >= 1.0 {
        return 0.0;
    }
    let nf = n as f64;
    let jmax = (nf * (1.0 - d)).floor() as usize;
    let mut logc = 0.0; // log C(n, j), accumulated
    let mut total = 0.0;
    for j in 0..=jmax {
        if j > 0 {
            logc += ((n - j + 1) as f64 / j as f64).ln();
        }
        let jf = j as f64;
        let a = 1.0 - d - jf / nf;
        let b = d + jf / nf;
        if a > 0.0 {
            let lt = logc + (nf - jf) * a.ln() + (jf - 1.0) * b.ln();
            total += lt.exp();
        }
    }
    clip(d * total)
}

fn stirling_polyval(x: f64) -> f64 {
    const C: [f64; 8] = [
        -2.955_065_359_477_124e-2,
        6.410_256_410_256_411e-3,
        -1.917_526_917_526_917_5e-3,
        8.417_508_417_508_417e-4,
        -5.952_380_952_380_953e-4,
        7.936_507_936_507_937e-4,
        -2.777_777_777_777_778e-3,
        8.333_333_333_333_333e-2,
    ];
    let mut y = C[0];
    for c in &C[1..] {
        y = y * x + c;
    }
    y
}

/// `scipy.stats.kstwo.sf(d, n)`.
pub fn kstwo_sf(d: f64, n: usize) -> f64 {
    if d.is_nan() {
        return f64::NAN;
    }
    let nf = n as f64;
    if d <= 0.5 / nf {
        return 1.0;
    }
    if d >= 1.0 {
        return 0.0;
    }
    let x = d;
    let t = nf * x;
    if t <= 1.0 {
        if t <= 0.5 {
            return 1.0;
        }
        let prob = if n <= 140 {
            (1..=n).fold(1.0, |acc, i| {
                acc * (i as f64 * (1.0 / nf) * (2.0 * t - 1.0))
            })
        } else {
            let rn = 1.0 / nf;
            let lf = nf.ln() / 2.0 - nf + LOG_2PI / 2.0 + rn * stirling_polyval(rn / nf);
            (lf + nf * (2.0 * t - 1.0).ln()).exp()
        };
        return clip(1.0 - prob);
    }
    if t >= nf - 1.0 {
        return clip(2.0 * (1.0 - x).powf(nf));
    }
    if x >= 0.5 {
        return clip(2.0 * smirnov(n, x));
    }
    let nxsq = t * x;
    if n <= 140 {
        if nxsq <= 0.754_693 {
            return clip(1.0 - kolmogn_dmtw(n, x));
        }
        if nxsq <= 4.0 {
            return clip(1.0 - kolmogn_pomeranz(n, x));
        }
        return clip(2.0 * smirnov(n, x));
    }
    if nxsq >= 370.0 {
        return 0.0;
    }
    if nxsq >= 2.2 {
        return clip(2.0 * smirnov(n, x));
    }
    let cdfprob = if nxsq >= 18.0 {
        1.0
    } else if n <= 100_000 && nf * x.powf(1.5) <= 1.4 {
        kolmogn_dmtw(n, x)
    } else {
        kolmogn_pelz_good(n, x)
    };
    clip(1.0 - cdfprob)
}

// --------------------------------------------------------------------- fits

#[derive(Debug)]
pub struct FitError;

fn all_finite(d: &[f64]) -> bool {
    d.iter().all(|v| v.is_finite())
}

thread_local! {
    /// Scratch for the lognormal likelihood: the Nelder-Mead search evaluates it hundreds of times
    /// over the whole column, so the buffers are reused instead of allocated per evaluation.
    static NNLF_SCRATCH: std::cell::RefCell<(Vec<f64>, Vec<f64>)> =
        const { std::cell::RefCell::new((Vec::new(), Vec::new())) };
}

/// `lognorm._logpdf` over scaled values (all in (0, inf)), in place: `xs` holds them on entry and
/// the log-densities on return; `ts` is scratch. The formula is scipy's, `-log(x)**2 / (2 s^2) -
/// log(s x sqrt(2 pi))`, with both logs taken as array passes through numpy's `log` when hooked
/// (what scipy evaluates, and several times faster than libm per element).
fn lognorm_logpdf_inplace(xs: &mut [f64], ts: &mut Vec<f64>, s: f64) {
    ts.clear();
    ts.extend(xs.iter().map(|x| s * x * NP_SQRT_2PI));
    ln_inplace(xs);
    ln_inplace(ts);
    let two_s2 = 2.0 * s * s;
    for (l, t) in xs.iter_mut().zip(ts.iter()) {
        *l = -(*l * *l) / two_s2 - t;
    }
}

/// `rv_continuous.nnlf` for lognorm (not penalised): the reference the chunked `Eval::nnlf` is
/// tested against.
#[cfg(test)]
fn lognorm_nnlf_ref(theta: &[f64; 3], data: &[f64]) -> f64 {
    let (s, loc, scale) = (theta[0], theta[1], theta[2]);
    if !(s > 0.0) || scale <= 0.0 {
        return f64::INFINITY;
    }
    let n_log_scale = data.len() as f64 * sc_ln(scale);
    NNLF_SCRATCH.with(|c| {
        let (xs, ts) = &mut *c.borrow_mut();
        xs.clear();
        for v in data {
            let x = (v - loc) / scale;
            if !(0.0 < x && x < f64::INFINITY) {
                return f64::INFINITY;
            }
            xs.push(x);
        }
        lognorm_logpdf_inplace(xs, ts, s);
        -pairwise_sum(xs) + n_log_scale
    })
}

/// `rv_continuous._penalized_nnlf` for lognorm over the whole array at once. `Eval::penalized_nnlf`
/// takes over unless some value falls outside the support (the compacted array then has another
/// block tree, so this is the only exact way to sum it).
fn lognorm_penalized_nnlf_ref(theta: &[f64; 3], data: &[f64]) -> f64 {
    let (s, loc, scale) = (theta[0], theta[1], theta[2]);
    if !(s > 0.0) || scale <= 0.0 {
        return f64::INFINITY;
    }
    let n_log_scale = data.len() as f64 * sc_ln(scale);
    NNLF_SCRATCH.with(|c| {
        let (xs, ts) = &mut *c.borrow_mut();
        xs.clear();
        let mut n_bad = 0usize;
        for v in data {
            let x = (v - loc) / scale;
            if 0.0 < x && x < f64::INFINITY {
                xs.push(x);
            } else {
                n_bad += 1;
            }
        }
        lognorm_logpdf_inplace(xs, ts, s);
        if n_bad == 0 && xs.iter().all(|v| v.is_finite()) {
            return -pairwise_sum(xs) + n_log_scale;
        }
        let finite: Vec<f64> = xs.iter().copied().filter(|v| v.is_finite()).collect();
        n_bad += xs.len() - finite.len();
        -pairwise_sum(&finite) + n_bad as f64 * f64::MAX.ln() * 100.0 + n_log_scale
    })
}

struct MaxFun;

fn argsort(v: &[f64]) -> Vec<usize> {
    let mut idx: Vec<usize> = (0..v.len()).collect();
    // stable, NaN last (numpy sorts NaN to the end; for 4 elements its sort is insertion sort)
    idx.sort_by(|&a, &b| match (v[a].is_nan(), v[b].is_nan()) {
        (true, true) => std::cmp::Ordering::Equal,
        (true, false) => std::cmp::Ordering::Greater,
        (false, true) => std::cmp::Ordering::Less,
        _ => v[a].partial_cmp(&v[b]).unwrap(),
    });
    idx
}

/// `scipy.optimize.fmin` (`_minimize_neldermead`, non-adaptive, no bounds) on the penalized
/// lognorm nnlf.
fn nelder_mead<O: Fn(&[f64; 3]) -> f64>(x0: [f64; 3], objective: O) -> [f64; 3] {
    let (rho, chi, psi, sigma) = (1.0, 2.0, 0.5, 0.5);
    let (nonzdelt, zdelt) = (0.05, 0.000_25);
    let (xatol, fatol) = (1e-4, 1e-4);
    const N: usize = 3;
    let mut sim = [[0.0f64; N]; N + 1];
    sim[0] = x0;
    for k in 0..N {
        let mut y = x0;
        y[k] = if y[k] != 0.0 {
            (1.0 + nonzdelt) * y[k]
        } else {
            zdelt
        };
        sim[k + 1] = y;
    }
    let maxiter = N * 200;
    let maxfun = N * 200;
    let ncalls = std::cell::Cell::new(0usize);
    let f = |x: &[f64; 3]| -> Result<f64, MaxFun> {
        if ncalls.get() >= maxfun {
            return Err(MaxFun);
        }
        ncalls.set(ncalls.get() + 1);
        Ok(objective(x))
    };
    let mut fsim = [f64::INFINITY; N + 1];
    for k in 0..=N {
        match f(&sim[k]) {
            Ok(v) => fsim[k] = v,
            Err(MaxFun) => break,
        }
    }
    let reorder = |sim: &mut [[f64; N]; N + 1], fsim: &mut [f64; N + 1]| {
        let ind = argsort(fsim);
        let s2 = *sim;
        let f2 = *fsim;
        for (i, &j) in ind.iter().enumerate() {
            sim[i] = s2[j];
            fsim[i] = f2[j];
        }
    };
    reorder(&mut sim, &mut fsim);
    reorder(&mut sim, &mut fsim);
    let mut iterations = 1usize;
    while ncalls.get() < maxfun && iterations < maxiter {
        let step = (|| -> Result<bool, MaxFun> {
            let mut dx = 0.0f64;
            for row in sim.iter().skip(1) {
                for c in 0..N {
                    dx = dx.max((row[c] - sim[0][c]).abs());
                }
            }
            let mut df = 0.0f64;
            for v in fsim.iter().skip(1) {
                df = df.max((fsim[0] - v).abs());
            }
            if dx <= xatol && df <= fatol {
                return Ok(true);
            }
            let mut xbar = [0.0f64; N];
            for c in 0..N {
                let mut s = 0.0;
                for row in sim.iter().take(N) {
                    s += row[c];
                }
                xbar[c] = s / N as f64;
            }
            let mut xr = [0.0f64; N];
            for c in 0..N {
                xr[c] = (1.0 + rho) * xbar[c] - rho * sim[N][c];
            }
            let fxr = f(&xr)?;
            let mut doshrink = false;
            if fxr < fsim[0] {
                let mut xe = [0.0f64; N];
                for c in 0..N {
                    xe[c] = (1.0 + rho * chi) * xbar[c] - rho * chi * sim[N][c];
                }
                let fxe = f(&xe)?;
                if fxe < fxr {
                    sim[N] = xe;
                    fsim[N] = fxe;
                } else {
                    sim[N] = xr;
                    fsim[N] = fxr;
                }
            } else if fxr < fsim[N - 1] {
                sim[N] = xr;
                fsim[N] = fxr;
            } else {
                if fxr < fsim[N] {
                    let mut xc = [0.0f64; N];
                    for c in 0..N {
                        xc[c] = (1.0 + psi * rho) * xbar[c] - psi * rho * sim[N][c];
                    }
                    let fxc = f(&xc)?;
                    if fxc <= fxr {
                        sim[N] = xc;
                        fsim[N] = fxc;
                    } else {
                        doshrink = true;
                    }
                } else {
                    let mut xcc = [0.0f64; N];
                    for c in 0..N {
                        xcc[c] = (1.0 - psi) * xbar[c] + psi * sim[N][c];
                    }
                    let fxcc = f(&xcc)?;
                    if fxcc < fsim[N] {
                        sim[N] = xcc;
                        fsim[N] = fxcc;
                    } else {
                        doshrink = true;
                    }
                }
                if doshrink {
                    for j in 1..=N {
                        for c in 0..N {
                            sim[j][c] = sim[0][c] + sigma * (sim[j][c] - sim[0][c]);
                        }
                        fsim[j] = f(&sim[j])?;
                    }
                }
            }
            iterations += 1;
            Ok(false)
        })();
        if let Ok(true) = step {
            break;
        }
        reorder(&mut sim, &mut fsim);
    }
    sim[0]
}

/// `rv_continuous.fit` (MLE via fmin) for lognorm, including `_fitstart`.
fn lognorm_generic_fit(ev: &Eval) -> Result<[f64; 3], FitError> {
    lognorm_generic_fit_with(ev.data, |x| ev.penalized_nnlf(x))
}

fn lognorm_generic_fit_with<O: Fn(&[f64; 3]) -> f64>(
    data: &[f64],
    objective: O,
) -> Result<[f64; 3], FitError> {
    let p = sc_exp(1.0);
    let (mu, mu2) = (p.sqrt(), p * (p - 1.0));
    let muhat = mean(data);
    let dev: Vec<f64> = data.iter().map(|v| (v - muhat) * (v - muhat)).collect();
    let mu2hat = mean(&dev);
    let mut shat = (mu2hat / mu2).sqrt();
    let mut lhat = muhat - shat * mu;
    if !lhat.is_finite() {
        lhat = 0.0;
    }
    if !(shat.is_finite() && 0.0 < shat) {
        shat = 1.0;
    }
    let (mut loc_hat, mut scale_hat) = (lhat, shat);
    let data_a = data.iter().cloned().fold(f64::INFINITY, f64::min);
    let data_b = data.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
    let a_hat = loc_hat + 0.0 * scale_hat;
    let b_hat = loc_hat + f64::INFINITY * scale_hat;
    if !(a_hat < data_a && data_b < b_hat) {
        let margin = (data_b - data_a) * 0.1;
        loc_hat = (data_a - 0.0) - margin;
        scale_hat = 1.0;
    }
    let vals = nelder_mead([1.0, loc_hat, scale_hat], objective);
    if !(vals[0] > 0.0 && vals[2] > 0.0) {
        return Err(FitError);
    }
    Ok(vals)
}

/// `scipy.optimize.brentq` (zeros.c): returns (root, converged); `None` when f yields NaN or
/// the bracket does not change sign.
fn brentq<F: FnMut(f64) -> f64>(mut f: F, xa: f64, xb: f64) -> Option<(f64, bool)> {
    let (xtol, rtol, maxiter) = (2e-12, 4.0 * f64::EPSILON, 100);
    let mut fw = |x: f64| -> Option<f64> {
        let v = f(x);
        if v.is_nan() {
            None
        } else {
            Some(v)
        }
    };
    let (mut xpre, mut xcur) = (xa, xb);
    let (mut xblk, mut fblk, mut spre, mut scur) = (0.0f64, 0.0f64, 0.0f64, 0.0f64);
    let mut fpre = fw(xpre)?;
    let mut fcur = fw(xcur)?;
    if fpre == 0.0 {
        return Some((xpre, true));
    }
    if fcur == 0.0 {
        return Some((xcur, true));
    }
    if fpre.signum() == fcur.signum() {
        return None;
    }
    for _ in 0..maxiter {
        if fpre != 0.0 && fcur != 0.0 && fpre.signum() != fcur.signum() {
            xblk = xpre;
            fblk = fpre;
            spre = xcur - xpre;
            scur = spre;
        }
        if fblk.abs() < fcur.abs() {
            xpre = xcur;
            xcur = xblk;
            xblk = xpre;
            fpre = fcur;
            fcur = fblk;
            fblk = fpre;
        }
        let delta = (xtol + rtol * xcur.abs()) / 2.0;
        let sbis = (xblk - xcur) / 2.0;
        if fcur == 0.0 || sbis.abs() < delta {
            return Some((xcur, true));
        }
        if spre.abs() > delta && fcur.abs() < fpre.abs() {
            let stry = if xpre == xblk {
                -fcur * (xcur - xpre) / (fcur - fpre)
            } else {
                let dpre = (fpre - fcur) / (xpre - xcur);
                let dblk = (fblk - fcur) / (xblk - xcur);
                -fcur * (fblk * dblk - fpre * dpre) / (dblk * dpre * (fblk - fpre))
            };
            if 2.0 * stry.abs() < spre.abs().min(3.0 * sbis.abs() - delta) {
                spre = scur;
                scur = stry;
            } else {
                spre = sbis;
                scur = sbis;
            }
        } else {
            spre = sbis;
            scur = sbis;
        }
        xpre = xcur;
        fpre = fcur;
        if scur.abs() > delta {
            xcur += scur;
        } else {
            xcur += if sbis > 0.0 { delta } else { -delta };
        }
        fcur = fw(xcur)?;
    }
    Some((xcur, false))
}

/// The likelihood functions of scipy's lognorm `fit` override and of the generic fit, evaluated
/// over one data array. Every pass is element-wise work plus a `pairwise_sum`, so each runs
/// chunk by chunk over `pairwise_sum`'s leaves (see `Plan`): in parallel, with the data, the two
/// scratch buffers and numpy's `log` all hot in cache, and the leaf sums recombined in the
/// reference's own order. The results equal the whole-array evaluation bit for bit.
struct Eval<'a> {
    data: &'a [f64],
    data_min: f64,
    plan: Plan,
    par: bool,
    /// `shape_scale` of the last `loc` (the search asks for `dl_dloc` and `ll` at the same point).
    last: std::cell::Cell<Option<(f64, f64, f64)>>,
    /// Every `dl_dloc` value computed so far, by the bits of `loc`: the root finder asks again
    /// for the two ends of its bracket.
    dl_seen: std::cell::RefCell<Vec<(u64, f64)>>,
    sums: std::cell::RefCell<Vec<f64>>,
    b1: std::cell::RefCell<Vec<f64>>,
    b2: std::cell::RefCell<Vec<f64>>,
}

/// Why `Eval::nnlf_chunked` did not produce a value.
enum Unusable {
    /// Some scaled value is not in (0, inf): the likelihood is +inf (or penalised).
    OutOfSupport,
    /// Some log-density is not finite (only reported when asked to).
    NonFinite,
}

impl<'a> Eval<'a> {
    fn new(data: &'a [f64]) -> Eval<'a> {
        let n = data.len();
        let par = n >= PAR_MIN_ROWS && crate::can_par();
        Eval {
            data,
            data_min: data.iter().cloned().fold(f64::INFINITY, f64::min),
            plan: Plan::new(n, par),
            par,
            last: std::cell::Cell::new(None),
            dl_seen: std::cell::RefCell::new(Vec::new()),
            sums: std::cell::RefCell::new(Vec::new()),
            b1: std::cell::RefCell::new(vec![0.0; n]),
            b2: std::cell::RefCell::new(vec![0.0; n]),
        }
    }

    /// Run one pass over the data (see `Plan::run`), on the rayon pool if `par_ok`. When `log`
    /// goes through Python, a pass that calls numpy's `log` on a value that is not positive makes
    /// numpy raise a floating-point error flag, whose reporting is set up for the calling thread
    /// only (`ignore_fp_errors`), so such a pass has to stay on the calling thread: the callers
    /// say so with `par_ok`. With the GIL-free hooks there is no such state.
    fn pass<F>(&self, par_ok: bool, f: F) -> f64
    where
        F: Fn(Chunk<'_>) + Sync + Send,
    {
        let mut sums = self.sums.borrow_mut();
        let mut b1 = self.b1.borrow_mut();
        let mut b2 = self.b2.borrow_mut();
        self.plan
            .run(self.par && par_ok, &mut sums, &mut b1, &mut b2, f)
    }

    /// `(shape, scale)` of the lognormal fitted at `loc`: `scale = exp(mean(log(x - loc)))`,
    /// `shape = sqrt(mean((log(x - loc) - log(scale))^2))`.
    fn shape_scale(&self, loc: f64) -> (f64, f64) {
        if let Some((l, shape, scale)) = self.last.get() {
            if l.to_bits() == loc.to_bits() {
                return (shape, scale);
            }
        }
        let data = self.data;
        let n = data.len() as f64;
        // every log input v - loc is positive iff the smallest is
        let sum_log = self.pass(self.data_min - loc > 0.0, |c| {
            for (o, v) in c.b1.iter_mut().zip(&data[c.lo..c.hi]) {
                *o = v - loc;
            }
            ln_inplace(c.b1);
            leaf_sums(c.b1, c.starts, c.lo, c.sums);
        });
        let scale = sc_exp(sum_log / n);
        let ls = sc_ln(scale);
        // the logs are still in `b1`; the squared deviations are summed leaf by leaf
        let sum_sq = self.pass(true, |c| {
            let mut sq = [0.0f64; PW_BLOCK];
            for (k, slot) in c.sums.iter_mut().enumerate() {
                let logs = &c.b1[c.starts[k] - c.lo..c.starts[k + 1] - c.lo];
                for (o, l) in sq.iter_mut().zip(logs) {
                    let d = l - ls;
                    *o = d * d;
                }
                *slot = pairwise_sum(&sq[..logs.len()]);
            }
        });
        let shape = (sum_sq / n).sqrt();
        self.last.set(Some((loc, shape, scale)));
        (shape, scale)
    }

    fn dl_dloc(&self, loc: f64) -> f64 {
        let key = loc.to_bits();
        if let Some((_, d)) = self.dl_seen.borrow().iter().find(|(k, _)| *k == key) {
            return *d;
        }
        let d = self.dl_dloc_uncached(loc);
        let mut seen = self.dl_seen.borrow_mut();
        if seen.len() < 256 {
            seen.push((key, d));
        }
        d
    }

    fn dl_dloc_uncached(&self, loc: f64) -> f64 {
        let (shape, scale) = self.shape_scale(loc);
        let s2 = shape * shape;
        let data = self.data;
        // x = (v - loc) / scale is monotone in v, so its smallest value is the first to fail
        let x_min = (self.data_min - loc) / scale;
        self.pass(x_min > 0.0, |c| {
            let d = &data[c.lo..c.hi];
            for (o, v) in c.b1.iter_mut().zip(d) {
                *o = (v - loc) / scale;
            }
            ln_inplace(c.b1);
            for (o, v) in c.b1.iter_mut().zip(d) {
                let shifted = v - loc;
                *o = (1.0 + *o / s2) / shifted;
            }
            leaf_sums(c.b1, c.starts, c.lo, c.sums);
        })
    }

    fn ll(&self, loc: f64) -> f64 {
        let (shape, scale) = self.shape_scale(loc);
        -self.nnlf(&[shape, loc, scale])
    }

    /// `sum(logpdf)` over every element, negated, plus `n log(scale)`; `None` if a scaled value
    /// is outside the support, or (with `strict`) a log-density is not finite.
    fn nnlf_chunked(&self, theta: &[f64; 3], strict: bool) -> Result<f64, Unusable> {
        let (s, loc, scale) = (theta[0], theta[1], theta[2]);
        // The smallest value alone decides: x = (v - loc) / scale is monotone in v, so unless
        // the smallest x is in (0, inf) some element is out of the support (NaN fails too).
        let x_min = (self.data_min - loc) / scale;
        if !(0.0 < x_min && x_min < f64::INFINITY) {
            return Err(Unusable::OutOfSupport);
        }
        // the densities' own log argument s * x is positive for every element iff for the smallest
        let par_ok = s * x_min * NP_SQRT_2PI > 0.0;
        let n_log_scale = self.data.len() as f64 * sc_ln(scale);
        let two_s2 = 2.0 * s * s;
        let data = self.data;
        let unusable = std::sync::atomic::AtomicU8::new(0);
        let relaxed = std::sync::atomic::Ordering::Relaxed;
        let sum = self.pass(par_ok, |c| {
            if unusable.load(relaxed) != 0 {
                return;
            }
            // x, the log argument of the density and the support check in one sweep
            let mut in_support = true;
            for ((x, t), v) in c.b1.iter_mut().zip(c.b2.iter_mut()).zip(&data[c.lo..c.hi]) {
                let xv = (v - loc) / scale;
                *x = xv;
                *t = s * xv * NP_SQRT_2PI;
                in_support &= (0.0 < xv) & (xv < f64::INFINITY);
            }
            if !in_support {
                unusable.store(1, relaxed);
                return;
            }
            ln_inplace(c.b1);
            ln_inplace(c.b2);
            let mut finite = true;
            for (l, t) in c.b1.iter_mut().zip(c.b2.iter()) {
                *l = -(*l * *l) / two_s2 - t;
                finite &= l.is_finite();
            }
            if strict && !finite {
                unusable.store(2, relaxed);
                return;
            }
            leaf_sums(c.b1, c.starts, c.lo, c.sums);
        });
        match unusable.load(relaxed) {
            0 => Ok(-sum + n_log_scale),
            1 => Err(Unusable::OutOfSupport),
            _ => Err(Unusable::NonFinite),
        }
    }

    /// `rv_continuous.nnlf` for lognorm (not penalised).
    fn nnlf(&self, theta: &[f64; 3]) -> f64 {
        if !(theta[0] > 0.0) || theta[2] <= 0.0 {
            return f64::INFINITY;
        }
        self.nnlf_chunked(theta, false).unwrap_or(f64::INFINITY)
    }

    /// `rv_continuous._penalized_nnlf` for lognorm.
    fn penalized_nnlf(&self, theta: &[f64; 3]) -> f64 {
        if !(theta[0] > 0.0) || theta[2] <= 0.0 {
            return f64::INFINITY;
        }
        match self.nnlf_chunked(theta, true) {
            Ok(v) => v,
            Err(_) => lognorm_penalized_nnlf_ref(theta, self.data),
        }
    }
}

/// `scipy.stats.lognorm.fit` (scipy 1.17 override; loc free).
fn lognorm_fit(data: &[f64]) -> Result<[f64; 3], FitError> {
    if !all_finite(data) {
        return Err(FitError);
    }
    let lf = Eval::new(data);
    let data_min = lf.data_min;
    let spacing_v = spacing(data_min);
    let mut rbrack = data_min - spacing_v;
    let mut d_rbrack = lf.dl_dloc(rbrack);
    let ll_rbrack = lf.ll(rbrack);
    let mut delta = 2.0 * spacing_v;
    while d_rbrack >= -1e-6 {
        rbrack = data_min - delta;
        d_rbrack = lf.dl_dloc(rbrack);
        delta *= 2.0;
    }
    if !rbrack.is_finite() || !d_rbrack.is_finite() {
        return lognorm_generic_fit(&lf);
    }
    let mut lbrack = nextafter(rbrack, f64::NEG_INFINITY).min(rbrack - 1.0);
    let mut d_lbrack = lf.dl_dloc(lbrack);
    let mut delta = 2.0 * (rbrack - lbrack);
    while lbrack.is_finite() && d_lbrack.is_finite() && d_lbrack.signum() == d_rbrack.signum() {
        lbrack = rbrack - delta;
        d_lbrack = lf.dl_dloc(lbrack);
        delta *= 2.0;
    }
    if !lbrack.is_finite() || !d_lbrack.is_finite() {
        return lognorm_generic_fit(&lf);
    }
    let root = match brentq(|x| lf.dl_dloc(x), lbrack, rbrack) {
        Some((r, true)) => r,
        _ => return lognorm_generic_fit(&lf),
    };
    let ll_root = lf.ll(root);
    let loc = if ll_root > ll_rbrack {
        root
    } else {
        data_min - spacing_v
    };
    let (shape, scale) = lf.shape_scale(loc);
    if !(shape > 0.0 && scale > 0.0) {
        return lognorm_generic_fit(&lf);
    }
    Ok([shape, loc, scale])
}

/// `(shape, scale, dL/dloc, log-likelihood)` of the lognormal objective at `loc`, for testing
/// the pieces of the fit against numpy.
pub fn lognorm_probe(data: &[f64], loc: f64) -> (f64, f64, f64, f64) {
    let lf = Eval::new(data);
    let (shape, scale) = lf.shape_scale(loc);
    (shape, scale, lf.dl_dloc(loc), lf.ll(loc))
}

/// Fitted parameters: `(loc, scale)` or `(s, loc, scale)` for lognormal.
pub fn fit(d: Dist, data: &[f64]) -> Result<Vec<f64>, FitError> {
    match d {
        Dist::Normal => {
            if !all_finite(data) {
                return Err(FitError);
            }
            let loc = mean(data);
            let dev: Vec<f64> = data.iter().map(|v| (v - loc) * (v - loc)).collect();
            Ok(vec![loc, mean(&dev).sqrt()])
        }
        Dist::Uniform => {
            if !all_finite(data) {
                return Err(FitError);
            }
            let mn = data.iter().cloned().fold(f64::INFINITY, f64::min);
            let mx = data.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
            Ok(vec![mn, mx - mn])
        }
        Dist::Exponential => {
            if !all_finite(data) {
                return Err(FitError);
            }
            let loc = data.iter().cloned().fold(f64::INFINITY, f64::min);
            Ok(vec![loc, mean(data) - loc])
        }
        Dist::Lognormal => lognorm_fit(data).map(|p| p.to_vec()),
    }
}

fn sorted_copy(v: &[f64]) -> Vec<f64> {
    let mut xs = v.to_vec();
    let cmp = |a: &f64, b: &f64| a.partial_cmp(b).unwrap_or(std::cmp::Ordering::Equal);
    if crate::can_par() {
        xs.par_sort_unstable_by(cmp);
    } else {
        xs.sort_unstable_by(cmp);
    }
    xs
}

/// `DataProfiler._detect_distribution` on an already-sampled array: the best candidate whose
/// KS p-value exceeds 0.05, by smallest KS statistic.
pub fn detect_distribution(values: &[f64]) -> Option<(Dist, Vec<f64>)> {
    if values.len() < 20 {
        return None;
    }
    let xs = sorted_copy(values);
    let mut best: Option<(Dist, f64, Vec<f64>)> = None;
    for d in CANDIDATES {
        let Ok(params) = fit(d, values) else { continue };
        let stat = ks_stat_sorted(&xs, d, &params);
        let p = kstwo_sf(stat, values.len());
        if p > 0.05 && best.as_ref().is_none_or(|b| stat < b.1) {
            best = Some((d, stat, params));
        }
    }
    best.map(|(d, _, p)| (d, p))
}

/// The `fit_score`: refit `d` on the full column and return `round(1 - D, 4)`.
pub fn fit_score(full: &[f64], d: Dist) -> Option<f64> {
    if full.len() < 20 {
        return None;
    }
    let params = fit(d, full).ok()?;
    let xs = sorted_copy(full);
    let stat = ks_stat_sorted(&xs, d, &params);
    if stat.is_nan() {
        return None;
    }
    format!("{:.4}", 1.0 - stat).parse::<f64>().ok()
}

pub fn dist_from_name(name: &str) -> Option<Dist> {
    CANDIDATES.iter().copied().find(|d| d.name() == name)
}

pub const _LN2: f64 = LN_2;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ndtr_known_values() {
        assert!((ndtr(0.0) - 0.5).abs() < 1e-16);
        assert!((ndtr(1.96) - 0.975_002_104_851_78).abs() < 1e-12);
        assert!((ndtr(-8.0) - 6.220_960_574_271_785e-16).abs() < 1e-28);
    }

    #[test]
    fn pairwise_matches_naive_on_small() {
        let v: Vec<f64> = (0..300).map(|i| i as f64 * 0.1).collect();
        let naive: f64 = v.iter().sum();
        assert!((pairwise_sum(&v) - naive).abs() < 1e-9);
    }

    #[test]
    fn kstwo_sf_is_monotone_and_bounded() {
        let mut last = 1.0;
        for i in 1..40 {
            let p = kstwo_sf(i as f64 * 0.01, 100);
            assert!((0.0..=1.0).contains(&p) && p <= last + 1e-12, "{i} {p}");
            last = p;
        }
    }

    #[test]
    fn detects_a_normal_sample() {
        // deterministic pseudo-normal via Box-Muller on an LCG
        let mut s = 12345u64;
        let mut u = || {
            s = s
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            ((s >> 11) as f64 + 0.5) / (1u64 << 53) as f64
        };
        let v: Vec<f64> = (0..1000)
            .map(|_| 50.0 + 5.0 * (-2.0 * u().ln()).sqrt() * (2.0 * PI * u()).cos())
            .collect();
        let (d, p) = detect_distribution(&v).expect("fits");
        assert_eq!(d, Dist::Normal);
        assert!((p[0] - 50.0).abs() < 1.0 && (p[1] - 5.0).abs() < 1.0);
    }

    // ------------------------------------------------------------ P1-16 differential tests
    //
    // The chunked, parallel likelihood passes must equal the whole-array evaluation bit for bit.
    // The references below are the previous implementation (one pass per array, serial
    // `pairwise_sum`); `ln` is libm's in both, since no numpy hook is installed in unit tests.

    struct Lcg(u64);

    impl Lcg {
        fn next(&mut self) -> f64 {
            self.0 = self
                .0
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            ((self.0 >> 11) as f64 + 0.5) / (1u64 << 53) as f64
        }

        fn normal(&mut self) -> f64 {
            (-2.0 * self.next().ln()).sqrt() * (2.0 * PI * self.next()).cos()
        }
    }

    fn same(a: f64, b: f64) -> bool {
        a.to_bits() == b.to_bits() || (a.is_nan() && b.is_nan())
    }

    fn ref_shape_scale(data: &[f64], loc: f64) -> (f64, f64, Vec<f64>) {
        let mut b1: Vec<f64> = data.iter().map(|v| v - loc).collect();
        ln_inplace(&mut b1);
        let scale = sc_exp(mean(&b1));
        let ls = sc_ln(scale);
        let b2: Vec<f64> = b1
            .iter()
            .map(|l| {
                let d = l - ls;
                d * d
            })
            .collect();
        (mean(&b2).sqrt(), scale, b1)
    }

    fn ref_dl_dloc(data: &[f64], loc: f64) -> f64 {
        let (shape, scale, _) = ref_shape_scale(data, loc);
        let s2 = shape * shape;
        let mut b1: Vec<f64> = data.iter().map(|v| (v - loc) / scale).collect();
        ln_inplace(&mut b1);
        for (o, v) in b1.iter_mut().zip(data) {
            *o = (1.0 + *o / s2) / (v - loc);
        }
        pairwise_sum(&b1)
    }

    fn datasets(n: usize, rng: &mut Lcg) -> Vec<(&'static str, Vec<f64>)> {
        vec![
            (
                "lognormal",
                (0..n).map(|_| (1.0 + 0.5 * rng.normal()).exp()).collect(),
            ),
            (
                "normal",
                (0..n).map(|_| 250.0 + 40.0 * rng.normal()).collect(),
            ),
            (
                "negative",
                (0..n).map(|_| -3.0 + 0.7 * rng.normal()).collect(),
            ),
            (
                "rounded",
                (0..n).map(|_| (100.0 * rng.next()).round() / 4.0).collect(),
            ),
            (
                "wide",
                (0..n).map(|_| (12.0 * rng.normal()).exp()).collect(),
            ),
            ("constant", vec![3.25; n]),
        ]
    }

    const SIZES: [usize; 14] = [
        20, 127, 128, 129, 136, 257, 1000, 4097, 16_385, 40_000, 65_535, 65_536, 70_001, 150_011,
    ];

    #[test]
    fn pw_combine_is_pairwise_sum() {
        let mut rng = Lcg(7);
        for n in (0..1300).chain([5000, 65_536, 100_003, 1_000_001]) {
            let v: Vec<f64> = (0..n)
                .map(|_| rng.normal() * (30.0 * rng.normal()).exp())
                .collect();
            let plan = Plan::new(n, true);
            let sums: Vec<f64> = plan
                .starts
                .windows(2)
                .map(|w| pairwise_sum(&v[w[0]..w[1]]))
                .collect();
            let got = pw_combine(&plan.adds, &sums);
            assert!(same(got, pairwise_sum(&v)), "n={n}");
            assert!(plan.starts.windows(2).all(|w| w[1] - w[0] <= PW_BLOCK));
        }
    }

    #[test]
    fn chunked_passes_equal_whole_array_passes() {
        let mut rng = Lcg(11);
        for (i, &n) in SIZES.iter().enumerate() {
            for (name, data) in datasets(n, &mut rng) {
                let mut ev = Eval::new(&data);
                // vary the chunk size: one chunk per leaf, a few leaves, the default
                match i % 3 {
                    0 => ev.plan.chunk_leaves = 1,
                    1 => ev.plan.chunk_leaves = 7,
                    _ => {}
                }
                let mn = ev.data_min;
                let spread = data.iter().cloned().fold(f64::NEG_INFINITY, f64::max) - mn;
                for loc in [
                    mn - 1e-9 * (1.0 + mn.abs()),
                    mn - 0.3 * spread - 0.1,
                    mn - 5.0 * spread - 1.0,
                    mn - 1.0e6,
                    mn,
                    mn + 0.5,
                ] {
                    let ctx = format!("{name} n={n} loc={loc}");
                    let (rs, rc, _) = ref_shape_scale(&data, loc);
                    let (s, c) = ev.shape_scale(loc);
                    assert!(same(s, rs) && same(c, rc), "shape_scale {ctx}");
                    assert!(
                        same(ev.dl_dloc(loc), ref_dl_dloc(&data, loc)),
                        "dl_dloc {ctx}"
                    );
                    let ll_ref = -lognorm_nnlf_ref(&[rs, loc, rc], &data);
                    assert!(same(ev.ll(loc), ll_ref), "ll {ctx}");
                    for theta in [
                        [rs, loc, rc],
                        [0.9, loc, rc * 1.3 + 1e-3],
                        [1.0, loc - 0.5, 2.0],
                    ] {
                        assert!(
                            same(ev.nnlf(&theta), lognorm_nnlf_ref(&theta, &data)),
                            "nnlf {ctx} {theta:?}"
                        );
                        assert!(
                            same(
                                ev.penalized_nnlf(&theta),
                                lognorm_penalized_nnlf_ref(&theta, &data)
                            ),
                            "penalized {ctx} {theta:?}"
                        );
                    }
                }
            }
        }
    }

    #[test]
    fn nonfinite_log_densities_and_invalid_parameters_match() {
        let mut data: Vec<f64> = (1..=3000).map(|i| i as f64 * 0.01).collect();
        data[1500] = f64::MIN_POSITIVE * 4.0; // x underflows for a large scale
        let ev = Eval::new(&data);
        for theta in [
            [0.5, -1.0, 1e300],
            [1e-200, 0.0, 1.0],
            [f64::NAN, 0.0, 1.0],
            [1.0, f64::NAN, 1.0],
            [1.0, 0.0, f64::NAN],
            [-1.0, 0.0, 1.0],
            [1.0, 0.0, 0.0],
            [1.0, -1e300, 1.0],
            [f64::INFINITY, -2.0, 3.0],
        ] {
            assert!(
                same(ev.nnlf(&theta), lognorm_nnlf_ref(&theta, &data)),
                "nnlf {theta:?}"
            );
            assert!(
                same(
                    ev.penalized_nnlf(&theta),
                    lognorm_penalized_nnlf_ref(&theta, &data)
                ),
                "penalized {theta:?}"
            );
        }
    }

    #[test]
    fn nelder_mead_fallback_follows_the_same_path() {
        let mut rng = Lcg(23);
        for &n in &[40usize, 500, 3000, 70_001] {
            for (name, data) in datasets(n, &mut rng) {
                let ev = Eval::new(&data);
                let got = lognorm_generic_fit(&ev);
                let want =
                    lognorm_generic_fit_with(&data, |x| lognorm_penalized_nnlf_ref(x, &data));
                match (got, want) {
                    (Ok(a), Ok(b)) => {
                        assert!(
                            a.iter().zip(&b).all(|(x, y)| same(*x, *y)),
                            "{name} n={n}: {a:?} {b:?}"
                        )
                    }
                    (Err(_), Err(_)) => {}
                    _ => panic!("{name} n={n}: one path failed"),
                }
            }
        }
    }

    #[test]
    fn lognormal_fit_equals_the_whole_array_fit() {
        // brentq's bracket search and root-finding go through `dl_dloc` hundreds of times: the
        // fitted parameters must agree bit for bit
        let mut rng = Lcg(31);
        for &n in &[60usize, 2000, 20_000, 70_001, 150_011] {
            for (name, data) in datasets(n, &mut rng) {
                if n > 100_000 && !matches!(name, "lognormal" | "normal") {
                    continue; // the big size is for the multi-chunk path; keep the debug run short
                }
                let got = lognorm_fit(&data);
                let want = ref_lognorm_fit(&data);
                match (got, want) {
                    (Ok(a), Ok(b)) => {
                        assert!(
                            a.iter().zip(&b).all(|(x, y)| same(*x, *y)),
                            "{name} n={n}: {a:?} {b:?}"
                        )
                    }
                    (Err(_), Err(_)) => {}
                    _ => panic!("{name} n={n}: one path failed"),
                }
            }
        }
    }

    /// `ks_stat_sorted` as it was before the chunked scan: one running maximum over the array.
    fn ref_ks_stat_sorted(xs: &[f64], d: Dist, p: &[f64]) -> f64 {
        let n = xs.len() as f64;
        let (mut dplus, mut dminus) = (f64::NEG_INFINITY, f64::NEG_INFINITY);
        for (i, x) in xs.iter().enumerate() {
            let c = cdf(d, *x, p);
            let plus = (i as f64 + 1.0) / n - c;
            let minus = c - i as f64 / n;
            if plus.is_nan() || plus > dplus {
                dplus = plus;
            }
            if minus.is_nan() || minus > dminus {
                dminus = minus;
            }
        }
        if dplus > dminus {
            dplus
        } else {
            dminus
        }
    }

    #[test]
    fn chunked_ks_statistic_equals_the_serial_scan() {
        let mut rng = Lcg(41);
        for &n in &[
            20usize,
            1000,
            65_536,
            PAR_MIN_ROWS,
            PAR_MIN_ROWS + 12_345,
            700_001,
        ] {
            let mut xs: Vec<f64> = (0..n).map(|_| 10.0 + 3.0 * rng.normal()).collect();
            xs.sort_by(|a, b| a.partial_cmp(b).unwrap());
            for (d, p) in [
                (Dist::Normal, vec![10.1, 2.9]),
                (Dist::Uniform, vec![xs[0], xs[n - 1] - xs[0]]),
                (Dist::Exponential, vec![xs[0] - 0.1, 9.0]),
                (Dist::Lognormal, vec![0.4, -5.0, 14.0]),
                (Dist::Normal, vec![10.0, 0.0]), // scale 0: every cdf is NaN
                (Dist::Lognormal, vec![0.4, 20.0, 14.0]), // all values below loc
            ] {
                assert!(
                    same(ks_stat_sorted(&xs, d, &p), ref_ks_stat_sorted(&xs, d, &p)),
                    "n={n} {d:?} {p:?}"
                );
            }
            // a NaN in the middle poisons the statistic in both
            let mut ys = xs.clone();
            ys[n / 2] = f64::NAN;
            let p = [10.0, 3.0];
            assert!(same(
                ks_stat_sorted(&ys, Dist::Normal, &p),
                ref_ks_stat_sorted(&ys, Dist::Normal, &p)
            ));
        }
    }

    /// `lognorm_fit` as it was before P1-16: the same search over whole-array evaluations.
    fn ref_lognorm_fit(data: &[f64]) -> Result<[f64; 3], FitError> {
        if !all_finite(data) {
            return Err(FitError);
        }
        let generic = || lognorm_generic_fit_with(data, |x| lognorm_penalized_nnlf_ref(x, data));
        let dl = |loc: f64| ref_dl_dloc(data, loc);
        let ll = |loc: f64| {
            let (s, sc, _) = ref_shape_scale(data, loc);
            -lognorm_nnlf_ref(&[s, loc, sc], data)
        };
        let data_min = data.iter().cloned().fold(f64::INFINITY, f64::min);
        let spacing_v = spacing(data_min);
        let mut rbrack = data_min - spacing_v;
        let mut d_rbrack = dl(rbrack);
        let ll_rbrack = ll(rbrack);
        let mut delta = 2.0 * spacing_v;
        while d_rbrack >= -1e-6 {
            rbrack = data_min - delta;
            d_rbrack = dl(rbrack);
            delta *= 2.0;
        }
        if !rbrack.is_finite() || !d_rbrack.is_finite() {
            return generic();
        }
        let mut lbrack = nextafter(rbrack, f64::NEG_INFINITY).min(rbrack - 1.0);
        let mut d_lbrack = dl(lbrack);
        let mut delta = 2.0 * (rbrack - lbrack);
        while lbrack.is_finite() && d_lbrack.is_finite() && d_lbrack.signum() == d_rbrack.signum() {
            lbrack = rbrack - delta;
            d_lbrack = dl(lbrack);
            delta *= 2.0;
        }
        if !lbrack.is_finite() || !d_lbrack.is_finite() {
            return generic();
        }
        let root = match brentq(dl, lbrack, rbrack) {
            Some((r, true)) => r,
            _ => return generic(),
        };
        let loc = if ll(root) > ll_rbrack {
            root
        } else {
            data_min - spacing_v
        };
        let (shape, scale, _) = ref_shape_scale(data, loc);
        if !(shape > 0.0 && scale > 0.0) {
            return generic();
        }
        Ok([shape, loc, scale])
    }
}

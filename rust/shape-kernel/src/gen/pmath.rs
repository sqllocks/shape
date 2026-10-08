//! Portable math (#768): `log`, `exp`, `pow` and `cos` of turns with the same bits on every CPU
//! and operating system.
//!
//! `f64::ln`, `f64::exp` and `f64::cos` call the platform's C library, and numpy calls
//! CPU-dispatched routines, so their last bits depend on the machine. These functions use IEEE 754
//! basic operations only (Rust never fuses `a * b + c` into a multiply-add), following fdlibm
//! (`e_log.c`, `e_exp.c`, `k_sin.c`, `k_cos.c`) with its constants given by their bit patterns.
//! Each has a numpy twin in `src/shape/kernel/reference/pmath.py` with the same operations in the
//! same order; the two agree bit for bit (`tests/kernel/test_pmath.py`).

const LN2_HI: f64 = f64::from_bits(0x3FE62E42FEE00000);
const LN2_LO: f64 = f64::from_bits(0x3DEA39EF35793C76);
const LG1: f64 = f64::from_bits(0x3FE5555555555593);
const LG2: f64 = f64::from_bits(0x3FD999999997FA04);
const LG3: f64 = f64::from_bits(0x3FD2492494229359);
const LG4: f64 = f64::from_bits(0x3FCC71C51D8E78AF);
const LG5: f64 = f64::from_bits(0x3FC7466496CB03DE);
const LG6: f64 = f64::from_bits(0x3FC39A09D078C69F);
const LG7: f64 = f64::from_bits(0x3FC2F112DF3E5244);
const SQRT2_SPLIT: f64 = f64::from_bits(0x3FF6A09C00000000);
const HFSQ_LOW: f64 = f64::from_bits(0x3FF6147A00000000);
const HFSQ_HIGH: f64 = f64::from_bits(0x3FF6B85200000000);
const TWO54: f64 = f64::from_bits(0x4350000000000000);
const TINY: f64 = f64::from_bits(0x0010000000000000);

const O_THRESHOLD: f64 = f64::from_bits(0x40862E42FEFA39EF);
const U_THRESHOLD: f64 = f64::from_bits(0xC0874910D52D3051);
const INVLN2: f64 = f64::from_bits(0x3FF71547652B82FE);
const P1: f64 = f64::from_bits(0x3FC555555555553E);
const P2: f64 = f64::from_bits(0xBF66C16C16BEBD93);
const P3: f64 = f64::from_bits(0x3F11566AAF25DE2C);
const P4: f64 = f64::from_bits(0xBEBBBD41C5D26BF1);
const P5: f64 = f64::from_bits(0x3E66376972BEA4D0);
const TWOM1000: f64 = f64::from_bits(0x0170000000000000);

const S1: f64 = f64::from_bits(0xBFC5555555555549);
const S2: f64 = f64::from_bits(0x3F8111111110F8A6);
const S3: f64 = f64::from_bits(0xBF2A01A019C161D5);
const S4: f64 = f64::from_bits(0x3EC71DE357B1FE7D);
const S5: f64 = f64::from_bits(0xBE5AE5E68A2B9CEB);
const S6: f64 = f64::from_bits(0x3DE5D93A5ACFD57C);
const C1: f64 = f64::from_bits(0x3FA555555555554C);
const C2: f64 = f64::from_bits(0xBF56C16C16C15177);
const C3: f64 = f64::from_bits(0x3EFA01A019CB1590);
const C4: f64 = f64::from_bits(0xBE927E4F809C52AD);
const C5: f64 = f64::from_bits(0x3E21EE9EBDB4B1C4);
const C6: f64 = f64::from_bits(0xBDA8FAE9BE8838D4);
const TWO_PI: f64 = f64::from_bits(0x401921FB54442D18);

const MANT_MASK: i64 = 0x000F_FFFF_FFFF_FFFF;
const ONE_BITS: i64 = 0x3FF0_0000_0000_0000;

/// Natural logarithm (`NaN` below 0, `-inf` at 0).
pub fn log(x: f64) -> f64 {
    if x.is_nan() || x < 0.0 {
        return f64::NAN;
    }
    if x == 0.0 {
        return f64::NEG_INFINITY;
    }
    if x == f64::INFINITY {
        return f64::INFINITY;
    }
    let sub = x < TINY;
    let xs = if sub { x * TWO54 } else { x };
    let b = xs.to_bits() as i64;
    let mut k = ((b >> 52) & 0x7FF) - 1023 - if sub { 54 } else { 0 };
    let mut m = f64::from_bits(((b & MANT_MASK) | ONE_BITS) as u64);
    let hfsq_form = (HFSQ_LOW..HFSQ_HIGH).contains(&m);
    if m >= SQRT2_SPLIT {
        m *= 0.5;
        k += 1;
    }
    let f = m - 1.0;
    let s = f / (2.0 + f);
    let dk = k as f64;
    let z = s * s;
    let w = z * z;
    let t1 = w * (LG2 + w * (LG4 + w * LG6));
    let t2 = z * (LG1 + w * (LG3 + w * (LG5 + w * LG7)));
    let r = t2 + t1;
    if hfsq_form {
        let hfsq = 0.5 * f * f;
        dk * LN2_HI - ((hfsq - (s * (hfsq + r) + dk * LN2_LO)) - f)
    } else {
        dk * LN2_HI - ((s * (f - r) - dk * LN2_LO) - f)
    }
}

/// `e ** x`.
pub fn exp(x: f64) -> f64 {
    if x.is_nan() {
        return f64::NAN;
    }
    if x > O_THRESHOLD {
        return f64::INFINITY;
    }
    if x < U_THRESHOLD {
        return 0.0;
    }
    let half = if x < 0.0 { -0.5 } else { 0.5 };
    let k = (x * INVLN2 + half).trunc();
    let hi = x - k * LN2_HI;
    let lo = k * LN2_LO;
    let r = hi - lo;
    let t = r * r;
    let c = r - t * (P1 + t * (P2 + t * (P3 + t * (P4 + t * P5))));
    let y = 1.0 - ((lo - (r * c) / (2.0 - c)) - hi);
    let ki = k as i64;
    if ki == 1024 {
        return y * 2.0 * f64::from_bits(((1023 + 1023) as u64) << 52);
    }
    if ki >= -1021 {
        return y * f64::from_bits(((ki + 1023) as u64) << 52);
    }
    y * f64::from_bits(((ki + 1000 + 1023) as u64) << 52) * TWOM1000
}

/// `x ** y` for `x >= 0` (`NaN` below 0): `x`, `x * x`, `sqrt(x)` or `1 / x` for `y` = 1, 2, 0.5
/// or -1, else `exp(y * log(x))`.
pub fn pow(x: f64, y: f64) -> f64 {
    if x.is_nan() || y.is_nan() || x < 0.0 {
        return f64::NAN;
    }
    if y == 0.0 || x == 1.0 {
        return 1.0;
    }
    if x == 0.0 {
        return if y > 0.0 { 0.0 } else { f64::INFINITY };
    }
    // exponents with an exact (or correctly rounded) answer from one basic operation
    if y == 1.0 {
        return x;
    }
    if y == 2.0 {
        return x * x;
    }
    if y == 0.5 {
        return x.sqrt();
    }
    if y == -1.0 {
        return 1.0 / x;
    }
    exp(y * log(x))
}

fn sin_k(x: f64) -> f64 {
    let z = x * x;
    let w = z * z;
    let r = S2 + z * (S3 + z * S4) + z * w * (S5 + z * S6);
    let v = z * x;
    x + v * (S1 + z * r)
}

fn cos_k(x: f64) -> f64 {
    let z = x * x;
    let w = z * z;
    let r = z * (C1 + z * (C2 + z * C3)) + w * w * (C4 + z * (C5 + z * C6));
    let hz = 0.5 * z;
    let one_hz = 1.0 - hz;
    one_hz + (((1.0 - one_hz) - hz) + z * r)
}

/// `cos(2 pi t)`: `t` in turns, reduced to an eighth of a turn exactly (`NaN` for infinite `t`,
/// and 1 for `|t| >= 2^50`, whole turns at that size).
pub fn cos_turns(t: f64) -> f64 {
    if !t.is_finite() {
        return f64::NAN;
    }
    let tc = if t.abs() < 1125899906842624.0 { t } else { 0.0 };
    let n = (4.0 * tc).round_ties_even();
    let r = tc - n * 0.25;
    let x = r * TWO_PI;
    match (n as i64) & 3 {
        0 => cos_k(x),
        1 => -sin_k(x),
        2 => -cos_k(x),
        _ => sin_k(x),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ulps(a: f64, b: f64) -> u64 {
        (a.to_bits() as i64 - b.to_bits() as i64).unsigned_abs()
    }

    #[test]
    fn close_to_the_c_library() {
        let mut x = 1e-300f64;
        while x < 1e300 {
            assert!(ulps(log(x), x.ln()) <= 1, "log {x}");
            x *= 1.37;
        }
        let mut x = -740.0f64;
        while x < 709.0 {
            assert!(ulps(exp(x), x.exp()) <= 1, "exp {x}");
            x += 0.0137;
        }
        let mut t = 0.0f64;
        while t < 1.0 {
            // the C library's argument 2 pi t is itself rounded (up to 4.4e-16 at t = 1)
            let want = (2.0 * std::f64::consts::PI * t).cos();
            assert!((cos_turns(t) - want).abs() <= 1.5e-15, "cos {t}");
            t += 0.000_731;
        }
    }

    #[test]
    fn special_values() {
        assert!(log(-1.0).is_nan() && log(f64::NAN).is_nan());
        assert_eq!(log(0.0), f64::NEG_INFINITY);
        assert_eq!(log(f64::INFINITY), f64::INFINITY);
        assert_eq!(log(1.0), 0.0);
        assert_eq!(exp(0.0), 1.0);
        assert_eq!(exp(1000.0), f64::INFINITY);
        assert_eq!(exp(-1000.0), 0.0);
        assert!(exp(f64::NAN).is_nan());
        assert_eq!(pow(0.0, 2.0), 0.0);
        assert_eq!(pow(0.0, -2.0), f64::INFINITY);
        assert_eq!(pow(5.0, 0.0), 1.0);
        assert!(pow(-1.0, 0.5).is_nan());
        assert_eq!(pow(3.0, 1.0), 3.0);
        assert_eq!(pow(3.0, 2.0), 9.0);
        assert_eq!(pow(2.0, 0.5), 2f64.sqrt());
        assert_eq!(pow(4.0, -1.0), 0.25);
        assert_eq!(cos_turns(0.0), 1.0);
        assert!(cos_turns(f64::INFINITY).is_nan());
    }
}

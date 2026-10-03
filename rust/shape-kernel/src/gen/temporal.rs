//! Temporal sampling: a date from per-day weights (month and day-of-week profiles, calendar
//! lifts) and a time of day from 24 hour weights (flat, or one or more Gaussian peaks).
//!
//! Days are counted from 1970-01-01 (a Thursday). Reference twin: `shape.kernel.reference.gen`.

use super::alias;
use super::rng::{below, for_row_chunks};

const US_PER_HOUR: i64 = 3_600_000_000;
const US_PER_DAY: i64 = 86_400_000_000;

/// Month (0 = January) of a day number (Howard Hinnant's `civil_from_days`).
pub fn month_of(days: i64) -> usize {
    let z = days + 719_468;
    let doe = z.rem_euclid(146_097); // the month does not depend on the 400-year era
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (m - 1) as usize
}

/// Day of week of a day number, Monday = 0.
pub fn dow_of(days: i64) -> usize {
    (days + 3).rem_euclid(7) as usize
}

/// Weight of each day `start_day .. start_day + n_days`: `month_w[month] * dow_w[weekday]`,
/// divided (with `per_bucket`) by the number of days in the range that share the day's
/// (month, weekday) pair, so every pair carries its own weight however many days it has.
pub fn day_weights(
    start_day: i64,
    n_days: usize,
    month_w: &[f64],
    dow_w: &[f64],
    per_bucket: bool,
) -> Vec<f64> {
    let mut count = [[0u64; 7]; 12];
    if per_bucket {
        for d in 0..n_days {
            let day = start_day + d as i64;
            count[month_of(day)][dow_of(day)] += 1;
        }
    }
    (0..n_days)
        .map(|d| {
            let day = start_day + d as i64;
            let (m, w) = (month_of(day), dow_of(day));
            let base = month_w[m] * dow_w[w];
            if per_bucket {
                base / count[m][w] as f64
            } else {
                base
            }
        })
        .collect()
}

/// erf by its all-positive series `2/sqrt(pi) * exp(-x^2) * sum (2x^2)^n x / (2n+1)!!`; exact to
/// about 1e-15 for |x| < 6 and +-1 beyond (the difference is below 1e-16).
pub fn erf(x: f64) -> f64 {
    let ax = x.abs();
    if ax >= 6.0 {
        return x.signum();
    }
    let x2 = ax * ax;
    let mut term = ax;
    let mut sum = ax;
    let mut n = 1.0f64;
    while term > 1e-17 * sum {
        term *= 2.0 * x2 / (2.0 * n + 1.0);
        sum += term;
        n += 1.0;
        if n > 500.0 {
            break;
        }
    }
    let r = std::f64::consts::FRAC_2_SQRT_PI * (-x2).exp() * sum;
    if x < 0.0 {
        -r
    } else {
        r
    }
}

fn normal_cdf(z: f64) -> f64 {
    0.5 * (1.0 + erf(z / std::f64::consts::SQRT_2))
}

/// Above this std (hours) the hour weights of a peak are uniform.
pub const UNIFORM_STD: f64 = 1e4;

/// Weights of the 24 hours for a mixture of equally likely Gaussian peaks (hours, `std` hours
/// wide): the probability that `floor(N(peak, std))` wrapped modulo 24 equals each hour.
pub fn hour_weights_peaks(peaks: &[f64], std: f64) -> Result<Vec<f64>, String> {
    if peaks.is_empty() || !(std.is_finite() && std > 0.0) {
        return Err("hour_weights_peaks needs peaks and a positive std".into());
    }
    if peaks.iter().any(|p| !p.is_finite()) {
        return Err("hour_weights_peaks needs finite peaks".into());
    }
    if std > UNIFORM_STD {
        // Wrapped modulo 24, a peak this wide is uniform to far below f64 precision, and the
        // sum below would take `8 * std / 24` terms per hour.
        return Ok(vec![peaks.len() as f64 / 24.0; 24]);
    }
    let k = (8.0 * std / 24.0).ceil() as i64 + 1;
    let mut w = vec![0.0f64; 24];
    for (h, out) in w.iter_mut().enumerate() {
        for &p in peaks {
            for j in -k..=k {
                let lo = h as f64 + 24.0 * j as f64 - p;
                *out += normal_cdf((lo + 1.0) / std) - normal_cdf(lo / std);
            }
        }
    }
    Ok(w)
}

/// Days `start_day .. start_day + n_days` must have their microseconds in i64 (about years
/// -290308 to 294247), or `(start_day + day) * US_PER_DAY` would wrap.
pub fn check_day_range(start_day: i64, n_days: usize) -> Result<(), String> {
    let first = i128::from(start_day) * i128::from(US_PER_DAY);
    let end = (i128::from(start_day) + n_days as i128) * i128::from(US_PER_DAY) - 1;
    if first < i128::from(i64::MIN) || end > i128::from(i64::MAX) {
        return Err(format!(
            "days {start_day}..{} are outside the timestamp range (int64 microseconds)",
            i128::from(start_day) + n_days as i128
        ));
    }
    Ok(())
}

/// Words per row used by [`sample`].
pub const WORDS_PER_ROW: usize = 5;

/// Timestamps in microseconds since the epoch for rows `row_start ..`: a day from
/// `day_weights`, an hour from `hour_weights`, and a uniform offset inside the hour (whole
/// seconds with `whole_seconds`). A row's words: 0-1 day, 2-3 hour, 4 offset.
#[allow(clippy::too_many_arguments)]
pub fn sample(
    day_weights: &[f64],
    hour_weights: &[f64],
    start_day: i64,
    key: [u64; 2],
    row_start: u64,
    n_rows: usize,
    whole_seconds: bool,
) -> Result<Vec<i64>, String> {
    if hour_weights.len() != 24 {
        return Err("hour_weights must have 24 entries".into());
    }
    check_day_range(start_day, day_weights.len())?;
    let (dp, da) = alias::build(day_weights)?;
    let (hp, ha) = alias::build(hour_weights)?;
    let mut out = vec![0i64; n_rows];
    for_row_chunks(
        key,
        row_start,
        WORDS_PER_ROW,
        &mut out,
        true,
        &|words, chunk| {
            for (r, o) in chunk.iter_mut().enumerate() {
                let w = &words[r * WORDS_PER_ROW..(r + 1) * WORDS_PER_ROW];
                let day = alias::pick(&dp, &da, w[0], w[1]);
                let hour = alias::pick(&hp, &ha, w[2], w[3]);
                let within = if whole_seconds {
                    below(w[4], 3600) as i64 * 1_000_000
                } else {
                    below(w[4], US_PER_HOUR as u64) as i64
                };
                *o = (start_day + day) * US_PER_DAY + hour * US_PER_HOUR + within;
            }
        },
    );
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn calendar_helpers() {
        assert_eq!(month_of(0), 0); // 1970-01-01
        assert_eq!(dow_of(0), 3); // a Thursday
        assert_eq!(month_of(59), 2); // 1970-03-01
        assert_eq!(month_of(-1), 11); // 1969-12-31
        assert_eq!(dow_of(-1), 2);
    }

    #[test]
    fn erf_known_values() {
        assert!((erf(0.0)).abs() < 1e-18);
        assert!((erf(1.0) - 0.842_700_792_949_714_9).abs() < 1e-14);
        assert!((erf(-0.5) + 0.520_499_877_813_046_5).abs() < 1e-14);
    }

    #[test]
    fn peaks_sum_to_the_peak_count() {
        let w = hour_weights_peaks(&[12.0, 18.0], 2.0).unwrap();
        let s: f64 = w.iter().sum();
        assert!((s - 2.0).abs() < 1e-9, "{s}");
    }
}

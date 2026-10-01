//! Exact-mode column kernels (P1-15): the per-column work of the product profiler.
//!
//! Each function reproduces, bit for bit, what the numpy/pyarrow reference in
//! `src/shape/kernel/reference/exact.py` computes (the reference twin, T-03): value counts in
//! pandas' order (count descending, ties by first appearance), numpy's pairwise sums and linear
//! percentiles, and the 1.5 x IQR outlier count. They release the GIL and use rayon where it
//! pays (never in a forked child, see `can_par`).

use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hash, Hasher};
use std::sync::Arc;

use arrow_array::cast::AsArray;
use arrow_array::types::{Float64Type, Int64Type};
use arrow_array::{Array, ArrayRef, GenericStringArray, OffsetSizeTrait};
use arrow_array::{Float64Array, Int64Array};
use arrow_schema::DataType;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use pyo3_arrow::PyArray;
use rayon::prelude::*;
use xxhash_rust::xxh3::xxh3_64;

use crate::fit::pairwise_sum;

// ------------------------------------------------------------------ numeric counting

/// A key type of the sort-based counter: totally ordered once NaN is excluded.
trait Key: Copy + Send + Sync + PartialOrd {
    fn sort(v: &mut [Self], par: bool);
    /// Equal to its own integer truncation (`x == x.astype(int64)`).
    fn whole(self) -> bool;
}

impl Key for f64 {
    fn whole(self) -> bool {
        is_whole(self)
    }
    fn sort(v: &mut [f64], par: bool) {
        if par {
            v.par_sort_unstable_by(|a, b| a.total_cmp(b));
        } else {
            v.sort_unstable_by(|a, b| a.total_cmp(b));
        }
    }
}

impl Key for i64 {
    fn whole(self) -> bool {
        true
    }
    fn sort(v: &mut [i64], par: bool) {
        if par {
            v.par_sort_unstable();
        } else {
            v.sort_unstable();
        }
    }
}

struct Counted<T> {
    sorted: Vec<T>,
    uniq: Vec<T>,
    counts: Vec<u64>,
}

fn count_sorted<T: Key>(values: &[T]) -> Counted<T> {
    let mut sorted = values.to_vec();
    let par = crate::can_par() && sorted.len() > 1_000_000;
    T::sort(&mut sorted, par);
    let mut uniq: Vec<T> = Vec::new();
    let mut counts: Vec<u64> = Vec::new();
    for &v in &sorted {
        if let Some(last) = uniq.last() {
            if *last == v {
                *counts.last_mut().expect("counts follow uniq") += 1;
                continue;
            }
        }
        uniq.push(v);
        counts.push(1);
    }
    Counted {
        sorted,
        uniq,
        counts,
    }
}

/// Indices into `uniq` of the first `need` keys in pandas' `value_counts` order (count
/// descending, ties by first appearance in row order): keys above the need-th count are always
/// selected, ties at that count are resolved by scanning rows in order until enough first
/// appearances are seen.
fn top_by_first_seen<T: Key>(values: &[T], uniq: &[T], counts: &[u64], need: usize) -> Vec<usize> {
    let k = uniq.len();
    let need = need.min(k);
    if need == 0 {
        return Vec::new();
    }
    let mut tmp = counts.to_vec();
    let c_thr = *tmp.select_nth_unstable(k - need).1;
    let n_above = counts.iter().filter(|&&c| c > c_thr).count();
    let want_ties = need - n_above;
    let cand_idx: Vec<usize> = (0..k).filter(|&i| counts[i] >= c_thr).collect();
    let cand: Vec<T> = cand_idx.iter().map(|&i| uniq[i]).collect();
    let mut first = vec![usize::MAX; cand.len()];
    let (mut found_above, mut found_ties) = (0usize, 0usize);
    for (row, v) in values.iter().enumerate() {
        if found_above >= n_above && found_ties >= want_ties {
            break;
        }
        let p = cand.partition_point(|c| c < v);
        if p < cand.len() && cand[p] == *v && first[p] == usize::MAX {
            first[p] = row;
            let c = counts[cand_idx[p]];
            if c > c_thr {
                found_above += 1;
            } else {
                found_ties += 1;
            }
        }
    }
    // (count, first-seen row, index into uniq)
    let mut above: Vec<(u64, usize, usize)> = Vec::new();
    let mut ties: Vec<(usize, usize)> = Vec::new();
    for (p, &i) in cand_idx.iter().enumerate() {
        if counts[i] > c_thr {
            above.push((counts[i], first[p], i));
        } else if first[p] != usize::MAX {
            ties.push((first[p], i));
        }
    }
    ties.sort_unstable();
    ties.truncate(want_ties);
    let mut sel = above;
    sel.extend(ties.into_iter().map(|(f, i)| (c_thr, f, i)));
    sel.sort_unstable_by(|a, b| b.0.cmp(&a.0).then(a.1.cmp(&b.1)));
    sel.into_iter().map(|(_, _, i)| i).collect()
}

/// pandas-profile rule: an enum keeps every key, anything else the first `top_n`. An enum
/// (P1-18) is within the size limits and its values repeat: distinct <= half the non-null
/// values (`n_nn`), so a unique column never qualifies.
fn need_for(card: usize, top_n: usize, row_count: usize, n_nn: usize) -> usize {
    let ratio = if row_count > 0 {
        card as f64 / row_count as f64
    } else {
        0.0
    };
    let is_enum = (card < 200 || (ratio < 0.30 && card < 50_000)) && card > 0 && 2 * card <= n_nn;
    if is_enum {
        card
    } else {
        top_n.min(card)
    }
}

fn count_numeric_impl<T: Key>(
    values: &[T],
    top_n: usize,
    row_count: usize,
    want_sorted: bool,
    want_uniq: bool,
) -> NumericCounts<T> {
    let c = count_sorted(values);
    let need = need_for(c.uniq.len(), top_n, row_count, values.len());
    let top = top_by_first_seen(values, &c.uniq, &c.counts, need);
    NumericCounts {
        cardinality: c.uniq.len(),
        all_whole: c.uniq.iter().all(|x| x.whole()),
        keys: top.iter().map(|&i| c.uniq[i]).collect(),
        counts: top.iter().map(|&i| c.counts[i]).collect(),
        sorted: want_sorted.then_some(c.sorted),
        uniq: want_uniq.then_some(c.uniq),
    }
}

/// `np.all(x == x.astype(np.int64))` for one float, with x86's out-of-range conversion.
fn is_whole(x: f64) -> bool {
    const TWO63: f64 = 9_223_372_036_854_775_808.0;
    x == -TWO63 || (x.is_finite() && x.abs() < TWO63 && x.fract() == 0.0)
}

struct NumericCounts<T> {
    cardinality: usize,
    all_whole: bool,
    keys: Vec<T>,
    counts: Vec<u64>,
    sorted: Option<Vec<T>>,
    uniq: Option<Vec<T>>,
}

fn no_nulls(arr: &ArrayRef, what: &str) -> PyResult<()> {
    if arr.null_count() > 0 {
        return Err(PyValueError::new_err(format!(
            "{what} needs an array without nulls"
        )));
    }
    Ok(())
}

fn f64_array(v: Vec<f64>) -> PyArray {
    PyArray::from_array_ref(Arc::new(Float64Array::from(v)))
}

/// Distinct count and the leading keys in pandas' `value_counts` order of a float64 or int64
/// array without nulls (NaN excluded by the caller, -0.0 already normalised to 0.0): every key
/// of an enum column (see `need_for`), else the first `top_n`.
/// Returns `{cardinality, keys, counts, sorted}`; `sorted` (the values, ascending, as float64)
/// is present when `want_sorted` is set.
#[pyfunction]
#[pyo3(signature = (values, top_n, row_count, want_sorted = false, want_uniq = false))]
fn count_numeric<'py>(
    py: Python<'py>,
    values: PyArray,
    top_n: usize,
    row_count: usize,
    want_sorted: bool,
    want_uniq: bool,
) -> PyResult<Bound<'py, PyDict>> {
    let (arr, _) = values.into_inner();
    no_nulls(&arr, "count_numeric")?;
    let out = PyDict::new(py);
    match arr.data_type() {
        DataType::Float64 => {
            let v = arr.as_primitive::<Float64Type>().values().clone();
            let r = py
                .detach(|| count_numeric_impl::<f64>(&v, top_n, row_count, want_sorted, want_uniq));
            out.set_item("cardinality", r.cardinality)?;
            out.set_item("all_whole", r.all_whole)?;
            out.set_item("keys", f64_array(r.keys))?;
            out.set_item("counts", counts_array(r.counts))?;
            out.set_item("sorted", r.sorted.map(f64_array))?;
            out.set_item("uniq", r.uniq.map(f64_array))?;
        }
        DataType::Int64 => {
            let v = arr.as_primitive::<Int64Type>().values().clone();
            let r = py
                .detach(|| count_numeric_impl::<i64>(&v, top_n, row_count, want_sorted, want_uniq));
            out.set_item("cardinality", r.cardinality)?;
            out.set_item("all_whole", r.all_whole)?;
            out.set_item(
                "keys",
                PyArray::from_array_ref(Arc::new(Int64Array::from(r.keys))),
            )?;
            out.set_item("counts", counts_array(r.counts))?;
            out.set_item(
                "sorted",
                r.sorted
                    .map(|s| f64_array(s.into_iter().map(|x| x as f64).collect())),
            )?;
            out.set_item(
                "uniq",
                r.uniq
                    .map(|u| PyArray::from_array_ref(Arc::new(Int64Array::from(u)))),
            )?;
        }
        t => {
            return Err(PyValueError::new_err(format!(
                "count_numeric needs float64 or int64, got {t}"
            )))
        }
    }
    Ok(out)
}

fn counts_array(v: Vec<u64>) -> PyArray {
    PyArray::from_array_ref(Arc::new(Int64Array::from(
        v.into_iter().map(|c| c as i64).collect::<Vec<_>>(),
    )))
}

// ------------------------------------------------------------------ numeric statistics

/// numpy's `linear` percentile positions and `_lerp` on already-sorted data.
fn percentile_sorted(sorted: &[f64], q_percent: f64) -> f64 {
    let n = sorted.len();
    let q = q_percent / 100.0;
    let vi = (n as f64 - 1.0) * q;
    let mut prev = vi.floor();
    let mut nxt = prev + 1.0;
    if vi >= n as f64 - 1.0 {
        prev = -1.0;
        nxt = -1.0;
    }
    if vi < 0.0 {
        prev = 0.0;
        nxt = 0.0;
    }
    let gamma = vi - prev;
    let at = |i: f64| -> f64 {
        if i < 0.0 {
            sorted[(n as i64 + i as i64) as usize]
        } else {
            sorted[i as usize]
        }
    };
    let (a, b) = (at(prev), at(nxt));
    let diff = b - a;
    if gamma >= 0.5 {
        b - diff * (1.0 - gamma)
    } else {
        a + diff * gamma
    }
}

const PCTS: [f64; 11] = [
    1.0, 5.0, 10.0, 25.0, 50.0, 75.0, 90.0, 95.0, 99.0, 0.5, 99.5,
];

/// Mean, sample standard deviation, quantiles and outlier count of a float64 array without
/// nulls, as numpy computes them. `sorted` (ascending) may be passed to skip the sort.
/// Returns `{mean, std, quantiles, outliers}`; `quantiles` (the 11 values p1..p99, p0.5, p99.5)
/// and `outliers` are `None` below four values.
#[pyfunction]
#[pyo3(signature = (values, sorted = None))]
fn numeric_stats<'py>(
    py: Python<'py>,
    values: PyArray,
    sorted: Option<PyArray>,
) -> PyResult<Bound<'py, PyDict>> {
    let get = |a: PyArray| -> PyResult<Float64Array> {
        let (arr, _) = a.into_inner();
        no_nulls(&arr, "numeric_stats")?;
        arr.as_primitive_opt::<Float64Type>()
            .cloned()
            .ok_or_else(|| PyValueError::new_err("numeric_stats needs float64 arrays"))
    };
    let values = get(values)?;
    let sorted = sorted.map(get).transpose()?;
    let r = py.detach(|| {
        let v = values.values();
        let cnt = v.len();
        let s = pairwise_sum(v);
        let mean = s / cnt as f64;
        let std = if cnt > 1 {
            let avg = s / cnt as f64;
            let sq: Vec<f64> = v
                .iter()
                .map(|x| {
                    let d = x - avg;
                    d * d
                })
                .collect();
            (pairwise_sum(&sq) / (cnt - 1) as f64).sqrt()
        } else {
            f64::NAN
        };
        if cnt < 4 {
            return (mean, std, None, None);
        }
        let owned;
        let xs: &[f64] = match &sorted {
            Some(s) => s.values(),
            None => {
                let mut t = v.to_vec();
                if crate::can_par() && t.len() > 1_000_000 {
                    t.par_sort_unstable_by(|a, b| a.total_cmp(b));
                } else {
                    t.sort_unstable_by(|a, b| a.total_cmp(b));
                }
                owned = t;
                &owned
            }
        };
        let qs: Vec<f64> = PCTS.iter().map(|&p| percentile_sorted(xs, p)).collect();
        let (q1, q3) = (qs[3], qs[5]);
        let iqr = q3 - q1;
        let outliers = if iqr == 0.0 {
            None
        } else {
            let lo = q1 - 1.5 * iqr;
            let hi = q3 + 1.5 * iqr;
            let left = if lo.is_nan() {
                cnt
            } else {
                xs.partition_point(|x| *x < lo)
            };
            let right = if hi.is_nan() {
                cnt
            } else {
                xs.partition_point(|x| *x <= hi)
            };
            Some(left + (cnt - right))
        };
        (mean, std, Some(qs), Some(outliers))
    });
    let out = PyDict::new(py);
    out.set_item("mean", r.0)?;
    out.set_item("std", r.1)?;
    out.set_item("quantiles", r.2)?;
    // outliers: None below four values; Some(None) when the IQR is zero (rate 0.0)
    match r.3 {
        None => {
            out.set_item("has_quantiles", false)?;
            out.set_item("outliers", py.None())?;
        }
        Some(o) => {
            out.set_item("has_quantiles", true)?;
            out.set_item("outliers", o)?;
        }
    }
    Ok(out)
}

// ------------------------------------------------------------------ string counting

#[derive(Default)]
struct IdHasher(u64);

impl Hasher for IdHasher {
    fn finish(&self) -> u64 {
        self.0
    }
    fn write(&mut self, bytes: &[u8]) {
        for b in bytes {
            self.0 = self.0.rotate_left(8) ^ u64::from(*b);
        }
    }
    fn write_u64(&mut self, v: u64) {
        self.0 = v;
    }
}

struct HKey<'a> {
    h: u64,
    s: &'a [u8],
}

impl Hash for HKey<'_> {
    fn hash<H: Hasher>(&self, state: &mut H) {
        state.write_u64(self.h);
    }
}

impl PartialEq for HKey<'_> {
    fn eq(&self, o: &Self) -> bool {
        self.h == o.h && self.s == o.s
    }
}

impl Eq for HKey<'_> {}

/// Distinct strings in first-appearance order, with their counts.
fn count_strings<O: OffsetSizeTrait>(a: &GenericStringArray<O>) -> (Vec<usize>, Vec<u64>) {
    let n = a.len();
    let par = crate::can_par() && n > 1_000_000;
    let hashes: Vec<u64> = if par {
        (0..n)
            .into_par_iter()
            .map(|i| xxh3_64(a.value(i).as_bytes()))
            .collect()
    } else {
        (0..n).map(|i| xxh3_64(a.value(i).as_bytes())).collect()
    };
    let parts = if par {
        rayon::current_num_threads().next_power_of_two().min(16)
    } else {
        1
    };
    let mask = (parts - 1) as u64;
    let one = |p: usize| -> Vec<(usize, u64)> {
        let mut map: HashMap<HKey, usize, BuildHasherDefault<IdHasher>> = HashMap::default();
        let mut entries: Vec<(usize, u64)> = Vec::new();
        for (i, &h) in hashes.iter().enumerate() {
            if h & mask != p as u64 {
                continue;
            }
            let key = HKey {
                h,
                s: a.value(i).as_bytes(),
            };
            match map.get(&key) {
                Some(&e) => entries[e].1 += 1,
                None => {
                    map.insert(key, entries.len());
                    entries.push((i, 1));
                }
            }
        }
        entries
    };
    let mut all: Vec<(usize, u64)> = if parts == 1 {
        one(0)
    } else {
        (0..parts)
            .into_par_iter()
            .map(one)
            .collect::<Vec<_>>()
            .into_iter()
            .flatten()
            .collect()
    };
    if par {
        all.par_sort_unstable_by_key(|e| e.0);
    } else {
        all.sort_unstable_by_key(|e| e.0);
    }
    all.into_iter().unzip()
}

fn value_counts_impl<O: OffsetSizeTrait>(a: &GenericStringArray<O>) -> (ArrayRef, Vec<u64>) {
    let (firsts, counts) = count_strings(a);
    let uniq = GenericStringArray::<O>::from_iter_values(firsts.iter().map(|&i| a.value(i)));
    (Arc::new(uniq), counts)
}

/// `pyarrow.compute.value_counts` of a string array without nulls, as `(values, counts)`:
/// distinct values in first-appearance order with their counts (uint64).
#[pyfunction]
fn value_counts_str(py: Python<'_>, values: PyArray) -> PyResult<(PyArray, PyArray)> {
    let (arr, _) = values.into_inner();
    no_nulls(&arr, "value_counts_str")?;
    let (uniq, counts) = match arr.data_type() {
        DataType::Utf8 => {
            let a = arr.as_string::<i32>().clone();
            py.detach(|| value_counts_impl(&a))
        }
        DataType::LargeUtf8 => {
            let a = arr.as_string::<i64>().clone();
            py.detach(|| value_counts_impl(&a))
        }
        t => {
            return Err(PyValueError::new_err(format!(
                "value_counts_str needs string or large_string, got {t}"
            )))
        }
    };
    Ok((PyArray::from_array_ref(uniq), counts_array(counts)))
}

/// Indices of the `need` largest counts, ties in index order (what a stable descending
/// argsort of `counts` gives): `numpy.argsort(-counts, kind="stable")[:need]`.
#[pyfunction]
fn top_indices(py: Python<'_>, counts: PyArray, need: usize) -> PyResult<PyArray> {
    let (arr, _) = counts.into_inner();
    no_nulls(&arr, "top_indices")?;
    let c = arr
        .as_primitive_opt::<Int64Type>()
        .ok_or_else(|| PyValueError::new_err("top_indices needs an int64 array"))?
        .values()
        .clone();
    let out = py.detach(|| {
        let mut idx: Vec<usize> = (0..c.len()).collect();
        let need = need.min(idx.len());
        let by = |a: &usize, b: &usize| c[*b].cmp(&c[*a]).then(a.cmp(b));
        if need > 0 && need < idx.len() {
            idx.select_nth_unstable_by(need - 1, by);
            idx.truncate(need);
        } else {
            idx.truncate(need);
        }
        idx.sort_unstable_by(by);
        idx.into_iter().map(|i| i as i64).collect::<Vec<_>>()
    });
    Ok(PyArray::from_array_ref(Arc::new(Int64Array::from(out))))
}

/// Days since 1970-01-01 -> (year, month 1..=12) (Howard Hinnant's civil_from_days).
fn civil(days: i64) -> (i64, usize) {
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    (if m <= 2 { y + 1 } else { y }, m as usize)
}

/// Hour-of-day (24), day-of-week from Monday (7), month (12) and year counts of a timestamp
/// array without nulls: `{hour, dow, month, year0, years}` where `years[i]` counts year
/// `year0 + i`.
#[pyfunction]
fn temporal_counts<'py>(py: Python<'py>, ts: PyArray) -> PyResult<Bound<'py, PyDict>> {
    use arrow_array::types::{
        TimestampMicrosecondType, TimestampMillisecondType, TimestampNanosecondType,
        TimestampSecondType,
    };
    use arrow_schema::TimeUnit;
    let (arr, _) = ts.into_inner();
    no_nulls(&arr, "temporal_counts")?;
    let (per_sec, vals): (i64, Vec<i64>) = match arr.data_type() {
        DataType::Timestamp(TimeUnit::Second, _) => (
            1,
            arr.as_primitive::<TimestampSecondType>().values().to_vec(),
        ),
        DataType::Timestamp(TimeUnit::Millisecond, _) => (
            1_000,
            arr.as_primitive::<TimestampMillisecondType>()
                .values()
                .to_vec(),
        ),
        DataType::Timestamp(TimeUnit::Microsecond, _) => (
            1_000_000,
            arr.as_primitive::<TimestampMicrosecondType>()
                .values()
                .to_vec(),
        ),
        DataType::Timestamp(TimeUnit::Nanosecond, _) => (
            1_000_000_000,
            arr.as_primitive::<TimestampNanosecondType>()
                .values()
                .to_vec(),
        ),
        t => {
            return Err(PyValueError::new_err(format!(
                "temporal_counts needs a timestamp array, got {t}"
            )))
        }
    };
    struct Acc {
        hour: [u64; 24],
        dow: [u64; 7],
        month: [u64; 12],
        year: std::collections::BTreeMap<i64, u64>,
    }
    let fresh = || Acc {
        hour: [0; 24],
        dow: [0; 7],
        month: [0; 12],
        year: std::collections::BTreeMap::new(),
    };
    let r = py.detach(|| {
        let per_day = 86_400 * per_sec;
        let one = |chunk: &[i64]| {
            let mut a = fresh();
            for &v in chunk {
                let days = v.div_euclid(per_day);
                let rem = v.rem_euclid(per_day);
                a.hour[(rem / (3_600 * per_sec)) as usize] += 1;
                a.dow[(days + 3).rem_euclid(7) as usize] += 1;
                let (y, m) = civil(days);
                a.month[m - 1] += 1;
                *a.year.entry(y).or_insert(0) += 1;
            }
            a
        };
        let merge = |mut a: Acc, b: Acc| {
            for i in 0..24 {
                a.hour[i] += b.hour[i];
            }
            for i in 0..7 {
                a.dow[i] += b.dow[i];
            }
            for i in 0..12 {
                a.month[i] += b.month[i];
            }
            for (y, c) in b.year {
                *a.year.entry(y).or_insert(0) += c;
            }
            a
        };
        if crate::can_par() && vals.len() > 1_000_000 {
            vals.par_chunks(65_536).map(one).reduce(fresh, merge)
        } else {
            one(&vals)
        }
    });
    let out = PyDict::new(py);
    out.set_item("hour", r.hour.to_vec())?;
    out.set_item("dow", r.dow.to_vec())?;
    out.set_item("month", r.month.to_vec())?;
    if let (Some((&lo, _)), Some((&hi, _))) = (r.year.first_key_value(), r.year.last_key_value()) {
        let years: Vec<u64> = (lo..=hi)
            .map(|y| r.year.get(&y).copied().unwrap_or(0))
            .collect();
        out.set_item("year0", lo)?;
        out.set_item("years", years)?;
    }
    Ok(out)
}

/// Python's `repr(float)` (the shortest round-trip digits; fixed notation for decimal exponents
/// in (-4, 16], else `d.ddde+XX`), which is what `str(k)` of a pandas float index gives.
fn py_float_repr(x: f64) -> String {
    if x.is_nan() {
        return "nan".to_string();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf" } else { "-inf" }.to_string();
    }
    // The shortest digit count, then that many digits correctly rounded: the shortest-digits
    // search breaks exact ties differently from Python's (closest, ties to even).
    let shortest = format!("{:e}", x.abs());
    let n_digits = shortest
        .split_once('e')
        .map_or(1, |(m, _)| m.len() - usize::from(m.contains('.')));
    let sci = format!("{:.*e}", n_digits.saturating_sub(1), x.abs());
    let (mant, exp) = sci.split_once('e').unwrap_or((sci.as_str(), "0"));
    let exp: i32 = exp.parse().unwrap_or(0);
    let digits: String = mant.chars().filter(|c| *c != '.').collect();
    let n = digits.len() as i32;
    let decpt = exp + 1;
    let mut out = String::new();
    if x.is_sign_negative() {
        out.push('-');
    }
    if -4 < decpt && decpt <= 16 {
        if decpt <= 0 {
            out.push_str("0.");
            out.push_str(&"0".repeat((-decpt) as usize));
            out.push_str(&digits);
        } else if decpt >= n {
            out.push_str(&digits);
            out.push_str(&"0".repeat((decpt - n) as usize));
            out.push_str(".0");
        } else {
            out.push_str(&digits[..decpt as usize]);
            out.push('.');
            out.push_str(&digits[decpt as usize..]);
        }
    } else {
        out.push_str(&digits[..1]);
        if n > 1 {
            out.push('.');
            out.push_str(&digits[1..]);
        }
        let e = decpt - 1;
        out.push('e');
        out.push(if e < 0 { '-' } else { '+' });
        out.push_str(&format!("{:02}", e.abs()));
    }
    out
}

/// Python's `round(x, 6)`: the correctly rounded 6-digit decimal (ties to even on the exact
/// binary value), read back as the nearest double.
fn round6_value(x: f64) -> f64 {
    if !x.is_finite() {
        return x;
    }
    format!("{x:.6}").parse::<f64>().unwrap_or(x)
}

fn float_values(arr: &ArrayRef, what: &str) -> PyResult<Float64Array> {
    no_nulls(arr, what)?;
    arr.as_primitive_opt::<Float64Type>()
        .cloned()
        .ok_or_else(|| PyValueError::new_err(format!("{what} needs a float64 array")))
}

/// `[str(float(v)) for v in values]` for a float64 array without nulls, as a string array.
#[pyfunction]
fn float_repr(py: Python<'_>, values: PyArray) -> PyResult<PyArray> {
    let (arr, _) = values.into_inner();
    let a = float_values(&arr, "float_repr")?;
    let out = py.detach(|| {
        GenericStringArray::<i32>::from_iter_values(a.values().iter().map(|&v| py_float_repr(v)))
    });
    Ok(PyArray::from_array_ref(Arc::new(out)))
}

/// `[round(float(v), 6) for v in values]` for a float64 array without nulls.
#[pyfunction]
fn round6(py: Python<'_>, values: PyArray) -> PyResult<PyArray> {
    let (arr, _) = values.into_inner();
    let a = float_values(&arr, "round6")?;
    let out =
        py.detach(|| Float64Array::from_iter_values(a.values().iter().map(|&v| round6_value(v))));
    Ok(PyArray::from_array_ref(Arc::new(out)))
}

/// Days since 1970-01-01 as `YYYY-MM-DD` (proleptic Gregorian; years 0000-9999 only).
fn civil_from_days(z: i64) -> (i64, u32, u32) {
    let z = z + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let m = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let y = yoe + era * 400 + i64::from(m <= 2);
    (y, m, d)
}

/// `date32` to text (`YYYY-MM-DD`), the cast pyarrow does for the CSV columns it parsed as
/// dates. Nulls stay null. Returns `None` when a year falls outside 0000-9999 (the caller then
/// uses pyarrow's own cast).
#[pyfunction]
fn date_iso(py: Python<'_>, values: PyArray) -> PyResult<Option<PyArray>> {
    use arrow_array::types::Date32Type;
    let (arr, _) = values.into_inner();
    let a = arr
        .as_primitive_opt::<Date32Type>()
        .ok_or_else(|| PyValueError::new_err("date_iso needs a date32 array"))?
        .clone();
    let out = py.detach(|| {
        let mut b = arrow_array::builder::StringBuilder::with_capacity(a.len(), a.len() * 10);
        let mut buf = String::with_capacity(10);
        for i in 0..a.len() {
            if a.is_null(i) {
                b.append_null();
                continue;
            }
            let (y, m, d) = civil_from_days(i64::from(a.value(i)));
            if !(0..=9999).contains(&y) {
                return None;
            }
            buf.clear();
            use std::fmt::Write;
            let _ = write!(buf, "{y:04}-{m:02}-{d:02}");
            b.append_value(&buf);
        }
        Some(b.finish())
    });
    Ok(out.map(|o| PyArray::from_array_ref(Arc::new(o))))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(count_numeric, m)?)?;
    m.add_function(wrap_pyfunction!(numeric_stats, m)?)?;
    m.add_function(wrap_pyfunction!(value_counts_str, m)?)?;
    m.add_function(wrap_pyfunction!(top_indices, m)?)?;
    m.add_function(wrap_pyfunction!(temporal_counts, m)?)?;
    m.add_function(wrap_pyfunction!(float_repr, m)?)?;
    m.add_function(wrap_pyfunction!(round6, m)?)?;
    m.add_function(wrap_pyfunction!(date_iso, m)?)?;
    Ok(())
}

//! Generation kernel (P4-03): the Philox4x64-10 stream (T-16), alias sampling, pool and string
//! assembly, and temporal sampling. Each function has a pure-Python twin in
//! `src/shape/kernel/reference/gen.py` (T-03); `docs/GENERATION_KERNEL.md` documents the contract.

pub mod alias;
pub mod relational;
pub mod rng;
pub mod strings;
pub mod temporal;

use std::sync::Arc;

use arrow_array::cast::AsArray;
use arrow_array::types::{Float64Type, Int64Type};
use arrow_array::{
    Array, ArrayRef, Float64Array, Int64Array, TimestampMicrosecondArray, UInt64Array,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3_arrow::PyArray;

fn err(e: String) -> PyErr {
    PyValueError::new_err(e)
}

fn out(a: ArrayRef) -> PyArray {
    PyArray::from_array_ref(a)
}

fn f64_values(a: PyArray, what: &str) -> PyResult<Vec<f64>> {
    let (arr, _) = a.into_inner();
    let p = arr
        .as_primitive_opt::<Float64Type>()
        .ok_or_else(|| err(format!("{what} must be a float64 array")))?;
    if p.null_count() > 0 {
        return Err(err(format!("{what} must not contain nulls")));
    }
    Ok(p.values().to_vec())
}

fn i64_array(a: PyArray, what: &str) -> PyResult<Int64Array> {
    let (arr, _) = a.into_inner();
    arr.as_primitive_opt::<Int64Type>()
        .cloned()
        .ok_or_else(|| err(format!("{what} must be an int64 array")))
}

fn check_slot(per_row: usize, slot: usize, width: usize) -> PyResult<()> {
    if per_row == 0 || slot + width > per_row {
        return Err(err(format!(
            "slot {slot} (+{width} words) does not fit in {per_row} words per row"
        )));
    }
    Ok(())
}

/// Words `row_start * per_row ..` of the stream `(k0, k1)`: `n_rows * per_row` uint64 values (row
/// `r`, word `j` at `r * per_row + j`).
#[pyfunction]
#[pyo3(signature = (k0, k1, row_start, n_rows, per_row = 1))]
fn philox_words(
    py: Python<'_>,
    k0: u64,
    k1: u64,
    row_start: u64,
    n_rows: usize,
    per_row: usize,
) -> PyResult<PyArray> {
    if per_row == 0 {
        return Err(err("per_row must be positive".into()));
    }
    let v = py.detach(|| {
        let mut words = vec![0u64; n_rows * per_row];
        fill_flat([k0, k1], row_start * per_row as u64, &mut words);
        words
    });
    Ok(out(Arc::new(UInt64Array::from(v))))
}

fn fill_flat(key: [u64; 2], first: u64, words: &mut [u64]) {
    use rayon::prelude::*;
    const CHUNK: usize = 1 << 15; // a multiple of 4, so chunks stay block aligned
    if words.len() >= 4 * CHUNK && crate::can_par() {
        words
            .par_chunks_mut(CHUNK)
            .enumerate()
            .for_each(|(i, c)| rng::fill_words(key, first + (i * CHUNK) as u64, c));
    } else {
        rng::fill_words(key, first, words);
    }
}

/// One uniform double in `[0, 1)` per row: the top 53 bits of word `slot` of each row's
/// `per_row` words.
#[pyfunction]
#[pyo3(signature = (k0, k1, row_start, n_rows, per_row = 1, slot = 0))]
fn philox_uniform(
    py: Python<'_>,
    k0: u64,
    k1: u64,
    row_start: u64,
    n_rows: usize,
    per_row: usize,
    slot: usize,
) -> PyResult<PyArray> {
    check_slot(per_row, slot, 1)?;
    let v = py.detach(|| {
        let mut o = vec![0f64; n_rows];
        rng::for_row_chunks([k0, k1], row_start, per_row, &mut o, true, &|w, c| {
            for (r, x) in c.iter_mut().enumerate() {
                *x = rng::unit(w[r * per_row + slot]);
            }
        });
        o
    });
    Ok(out(Arc::new(Float64Array::from(v))))
}

/// One standard normal per row by Box-Muller from words `slot` and `slot + 1`:
/// `sqrt(-2 ln(1 - u1)) * cos(2 pi u2)`.
#[pyfunction]
#[pyo3(signature = (k0, k1, row_start, n_rows, per_row = 2, slot = 0))]
fn philox_normal(
    py: Python<'_>,
    k0: u64,
    k1: u64,
    row_start: u64,
    n_rows: usize,
    per_row: usize,
    slot: usize,
) -> PyResult<PyArray> {
    check_slot(per_row, slot, 2)?;
    let v = py.detach(|| {
        let mut o = vec![0f64; n_rows];
        rng::for_row_chunks([k0, k1], row_start, per_row, &mut o, true, &|w, c| {
            for (r, x) in c.iter_mut().enumerate() {
                let u1 = 1.0 - rng::unit(w[r * per_row + slot]);
                let u2 = rng::unit(w[r * per_row + slot + 1]);
                *x = (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos();
            }
        });
        o
    });
    Ok(out(Arc::new(Float64Array::from(v))))
}

/// `(prob, alias)` of Vose's alias table for `weights` (float64, finite, non-negative, not all
/// zero).
#[pyfunction]
fn alias_build(weights: PyArray) -> PyResult<(PyArray, PyArray)> {
    let w = f64_values(weights, "weights")?;
    let (p, a) = alias::build(&w).map_err(err)?;
    Ok((
        out(Arc::new(Float64Array::from(p))),
        out(Arc::new(Int64Array::from(a))),
    ))
}

/// Column indices drawn from an alias table: two words per draw (`slot`, `slot + 1`).
#[pyfunction]
#[pyo3(signature = (prob, alias, k0, k1, row_start, n_rows, per_row = 2, slot = 0))]
#[allow(clippy::too_many_arguments)]
fn alias_sample(
    py: Python<'_>,
    prob: PyArray,
    alias: PyArray,
    k0: u64,
    k1: u64,
    row_start: u64,
    n_rows: usize,
    per_row: usize,
    slot: usize,
) -> PyResult<PyArray> {
    check_slot(per_row, slot, 2)?;
    let prob = f64_values(prob, "prob")?;
    let alias_a = i64_array(alias, "alias")?;
    let alias_v = alias_a.values().to_vec();
    if prob.is_empty() || prob.len() != alias_v.len() {
        return Err(err(
            "prob and alias must be non-empty and equally long".into()
        ));
    }
    if alias_v.iter().any(|a| *a < 0 || *a as usize >= prob.len()) {
        return Err(err("alias entries must lie in 0..len(prob)".into()));
    }
    let v =
        py.detach(|| alias::sample(&prob, &alias_v, [k0, k1], row_start, n_rows, per_row, slot));
    Ok(out(Arc::new(Int64Array::from(v))))
}

fn cols_of<'a>(arrays: &'a [ArrayRef]) -> PyResult<Vec<strings::Col<'a>>> {
    arrays
        .iter()
        .map(|a| strings::Col::from_array(a).map_err(err))
        .collect()
}

fn into_arrays(list: Vec<PyArray>) -> Vec<ArrayRef> {
    list.into_iter().map(|a| a.into_inner().0).collect()
}

/// `pool[indices]` as a `string` array.
#[pyfunction]
fn pool_take(py: Python<'_>, pool: PyArray, indices: PyArray) -> PyResult<PyArray> {
    let (pool, _) = pool.into_inner();
    let idx = i64_array(indices, "indices")?;
    let col = strings::Col::from_array(&pool).map_err(err)?;
    let r = py.detach(|| strings::pool_take(&col, &idx)).map_err(err)?;
    Ok(out(Arc::new(r)))
}

/// `literals[0] + col[slot0] + literals[1] + ...` (see `strings::template`).
#[pyfunction]
fn template_strings(
    py: Python<'_>,
    literals: Vec<String>,
    slots: Vec<(usize, usize)>,
    columns: Vec<PyArray>,
    n_rows: usize,
) -> PyResult<PyArray> {
    let arrays = into_arrays(columns);
    let cols = cols_of(&arrays)?;
    let r = py
        .detach(|| strings::template(&literals, &slots, &cols, n_rows))
        .map_err(err)?;
    Ok(out(Arc::new(r)))
}

/// Join columns with a separator (see `strings::join`).
#[pyfunction]
#[pyo3(signature = (columns, sep, skip_nulls = false))]
fn join_strings(
    py: Python<'_>,
    columns: Vec<PyArray>,
    sep: String,
    skip_nulls: bool,
) -> PyResult<PyArray> {
    let arrays = into_arrays(columns);
    let cols = cols_of(&arrays)?;
    let r = py
        .detach(|| strings::join(&cols, &sep, skip_nulls))
        .map_err(err)?;
    Ok(out(Arc::new(r)))
}

/// `upper`, `lower` or `title` case of every string.
#[pyfunction]
fn string_case(py: Python<'_>, array: PyArray, mode: &str) -> PyResult<PyArray> {
    let (arr, _) = array.into_inner();
    let mode = strings::CaseMode::parse(mode).map_err(err)?;
    let col = strings::Col::from_array(&arr).map_err(err)?;
    let r = py.detach(|| strings::case(&col, mode)).map_err(err)?;
    Ok(out(Arc::new(r)))
}

/// Version-4 UUID strings, two words per row.
#[pyfunction]
fn uuid4_strings(
    py: Python<'_>,
    k0: u64,
    k1: u64,
    row_start: u64,
    n_rows: usize,
) -> PyResult<PyArray> {
    let r = py
        .detach(|| strings::uuid4([k0, k1], row_start, n_rows))
        .map_err(err)?;
    Ok(out(Arc::new(r)))
}

/// `length` random characters per row from `alphabet`, `length` words per row.
#[pyfunction]
fn random_strings(
    py: Python<'_>,
    k0: u64,
    k1: u64,
    row_start: u64,
    n_rows: usize,
    length: usize,
    alphabet: &str,
) -> PyResult<PyArray> {
    let chars: Vec<char> = alphabet.chars().collect();
    let r = py
        .detach(|| strings::random_chars([k0, k1], row_start, n_rows, length, &chars))
        .map_err(err)?;
    Ok(out(Arc::new(r)))
}

/// Per-day weights for a date range (see `temporal::day_weights`).
#[pyfunction]
#[pyo3(signature = (start_day, n_days, month_weights, dow_weights, per_bucket = true))]
fn day_weights(
    start_day: i64,
    n_days: usize,
    month_weights: Vec<f64>,
    dow_weights: Vec<f64>,
    per_bucket: bool,
) -> PyResult<PyArray> {
    if month_weights.len() != 12 || dow_weights.len() != 7 {
        return Err(err("need 12 month weights and 7 day-of-week weights".into()));
    }
    let w = temporal::day_weights(start_day, n_days, &month_weights, &dow_weights, per_bucket);
    Ok(out(Arc::new(Float64Array::from(w))))
}

/// The 24 hour weights of equally likely Gaussian peaks.
#[pyfunction]
fn hour_weights_peaks(peaks: Vec<f64>, std: f64) -> PyResult<PyArray> {
    let w = temporal::hour_weights_peaks(&peaks, std).map_err(err)?;
    Ok(out(Arc::new(Float64Array::from(w))))
}

/// Timestamps (microsecond, no zone) from day and hour weights (see `temporal::sample`).
#[pyfunction]
#[pyo3(signature = (day_weights, hour_weights, start_day, k0, k1, row_start, n_rows, whole_seconds = false))]
#[allow(clippy::too_many_arguments)]
fn temporal_sample(
    py: Python<'_>,
    day_weights: PyArray,
    hour_weights: PyArray,
    start_day: i64,
    k0: u64,
    k1: u64,
    row_start: u64,
    n_rows: usize,
    whole_seconds: bool,
) -> PyResult<PyArray> {
    let dw = f64_values(day_weights, "day_weights")?;
    let hw = f64_values(hour_weights, "hour_weights")?;
    let v = py
        .detach(|| {
            temporal::sample(
                &dw,
                &hw,
                start_day,
                [k0, k1],
                row_start,
                n_rows,
                whole_seconds,
            )
        })
        .map_err(err)?;
    Ok(out(Arc::new(TimestampMicrosecondArray::from(v))))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(philox_words, m)?)?;
    m.add_function(wrap_pyfunction!(philox_uniform, m)?)?;
    m.add_function(wrap_pyfunction!(philox_normal, m)?)?;
    m.add_function(wrap_pyfunction!(alias_build, m)?)?;
    m.add_function(wrap_pyfunction!(alias_sample, m)?)?;
    m.add_function(wrap_pyfunction!(pool_take, m)?)?;
    m.add_function(wrap_pyfunction!(template_strings, m)?)?;
    m.add_function(wrap_pyfunction!(join_strings, m)?)?;
    m.add_function(wrap_pyfunction!(string_case, m)?)?;
    m.add_function(wrap_pyfunction!(uuid4_strings, m)?)?;
    m.add_function(wrap_pyfunction!(random_strings, m)?)?;
    m.add_function(wrap_pyfunction!(day_weights, m)?)?;
    m.add_function(wrap_pyfunction!(hour_weights_peaks, m)?)?;
    m.add_function(wrap_pyfunction!(temporal_sample, m)?)?;
    relational::register(m)?;
    Ok(())
}

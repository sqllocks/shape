//! Generation kernel (P4-03): the Philox4x64-10 stream (T-16), alias sampling, pool and string
//! assembly, and temporal sampling. Each function has a pure-Python twin in
//! `src/shape/kernel/reference/gen.py` (T-03); `docs/GENERATION_KERNEL.md` documents the contract.

pub mod alias;
pub mod relational;
pub mod rng;
pub mod strings;
pub mod temporal;
pub mod zipf;

use std::sync::Arc;

use arrow_array::cast::AsArray;
use arrow_array::types::{Float64Type, Int64Type};
use arrow_array::{
    Array, ArrayRef, Float64Array, Int64Array, TimestampMicrosecondArray, UInt64Array,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyTuple;
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

/// The standard normal of two words: `sqrt(-2 ln(1 - u1)) * cos(2 pi u2)`.
#[inline(always)]
fn normal_of(w1: u64, w2: u64) -> f64 {
    let u1 = 1.0 - rng::unit(w1);
    let u2 = rng::unit(w2);
    (-2.0 * u1.ln()).sqrt() * (2.0 * std::f64::consts::PI * u2).cos()
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
                *x = normal_of(w[r * per_row + slot], w[r * per_row + slot + 1]);
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

fn f64_slice<'a>(a: &'a ArrayRef, what: &str) -> PyResult<&'a [f64]> {
    let p = a
        .as_primitive_opt::<Float64Type>()
        .ok_or_else(|| err(format!("{what} must be a float64 array")))?;
    if p.null_count() > 0 {
        return Err(err(format!("{what} must not contain nulls")));
    }
    Ok(p.values())
}

/// Guide table of a normalised cumulative weight array (non-decreasing, finite): see `zipf`.
#[pyfunction]
fn zipf_guide(py: Python<'_>, cum: PyArray) -> PyResult<PyArray> {
    let (cum, _) = cum.into_inner();
    let values = f64_slice(&cum, "cum")?;
    if values.is_empty()
        || values.iter().any(|x| !x.is_finite())
        || values.windows(2).any(|w| w[1] < w[0])
    {
        return Err(err(
            "cum must be non-empty, finite and non-decreasing".into()
        ));
    }
    let g = py.detach(|| zipf::guide(values));
    Ok(out(Arc::new(Int64Array::from(g))))
}

/// One parent row per row from the Zipf draw of word 0 of each row of the stream `(k0, k1)`: the
/// index of the first entry of `cum` that is greater than the uniform, clipped to the last. With
/// `start` and `step` the result is `start + index * step`, the key a sequence primary key gives
/// that row.
#[pyfunction]
#[pyo3(signature = (cum, guide, k0, k1, row_start, n_rows, start = 0, step = 1))]
#[allow(clippy::too_many_arguments)]
fn zipf_draw(
    py: Python<'_>,
    cum: PyArray,
    guide: PyArray,
    k0: u64,
    k1: u64,
    row_start: u64,
    n_rows: usize,
    start: i64,
    step: i64,
) -> PyResult<PyArray> {
    let (cum, _) = cum.into_inner();
    let values = f64_slice(&cum, "cum")?;
    let guide = i64_array(guide, "guide")?;
    let g = guide.values();
    let m = g.len();
    if values.is_empty()
        || !m.is_power_of_two()
        || g.iter().any(|x| *x < 0 || *x as usize > values.len())
    {
        return Err(err(
            "cum must be non-empty and guide a power-of-two table of its own".into(),
        ));
    }
    let v = py.detach(|| {
        let mut v = zipf::draw(values, g, [k0, k1], row_start, n_rows);
        to_keys(&mut v, start, step);
        v
    });
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

/// numpy's `np.maximum` for two floats: a NaN in either gives NaN.
#[inline(always)]
fn np_maximum(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() {
        f64::NAN
    } else if a >= b {
        a
    } else {
        b
    }
}

/// numpy's `np.minimum` for two floats: a NaN in either gives NaN.
#[inline(always)]
fn np_minimum(a: f64, b: f64) -> f64 {
    if a.is_nan() || b.is_nan() {
        f64::NAN
    } else if a <= b {
        a
    } else {
        b
    }
}

/// `10 ** n` as numpy's `round` makes it (a table to 1e8, then repeated products of ten, which
/// stay exact up to 1e22).
fn numpy_power_of_ten(n: u32) -> f64 {
    const P10: [f64; 9] = [1e0, 1e1, 1e2, 1e3, 1e4, 1e5, 1e6, 1e7, 1e8];
    if n < 9 {
        return P10[n as usize];
    }
    let mut ret = 1e8;
    let mut k = n;
    while k > 8 {
        ret *= 10.0;
        k -= 1;
    }
    ret
}

/// `np.round(x, decimals)` for `decimals >= 0`: `rint(x * 10**d) / 10**d` (just `rint` for 0).
#[inline(always)]
fn numpy_round(x: f64, decimals: u32, factor: f64) -> f64 {
    if decimals == 0 {
        x.round_ties_even()
    } else {
        (x * factor).round_ties_even() / factor
    }
}

/// Log-normal values in one pass: `exp(mu + sigma * z)` for the standard normal `z` of each row
/// (words `0` and `1` of its two), clipped to `[low, high]` where given and rounded to `scale`
/// decimals where given, exactly as the NumPy expression
/// `round(minimum(maximum(exp(mu + sigma * philox_normal), low), high), scale)` gives. `exp` is
/// numpy's own loop (the one `np.exp` uses for the array), so the values are numpy's bit for bit.
/// Returns `None` when numpy's loop cannot be called without the GIL; the caller then does it
/// with NumPy.
#[pyfunction]
#[pyo3(signature = (k0, k1, row_start, n_rows, mu, sigma, low = None, high = None, scale = None))]
#[allow(clippy::too_many_arguments)]
fn lognormal_values(
    py: Python<'_>,
    k0: u64,
    k1: u64,
    row_start: u64,
    n_rows: usize,
    mu: f64,
    sigma: f64,
    low: Option<f64>,
    high: Option<f64>,
    scale: Option<u32>,
) -> PyResult<Option<PyArray>> {
    let Some(loops) = crate::numpy_loops_for_generation(py) else {
        return Ok(None);
    };
    if scale.is_some_and(|d| d > 22) {
        return Ok(None); // beyond the powers of ten that are exact
    }
    let factor = scale.map_or(1.0, numpy_power_of_ten);
    let v = py.detach(|| {
        let mut o = vec![0f64; n_rows];
        rng::for_row_chunks([k0, k1], row_start, 2, &mut o, true, &|w, c| {
            for (r, x) in c.iter_mut().enumerate() {
                *x = mu + sigma * normal_of(w[2 * r], w[2 * r + 1]);
            }
            loops.exp.apply(c);
            if low.is_some() || high.is_some() || scale.is_some() {
                for x in c.iter_mut() {
                    let mut y = *x;
                    if let Some(lo) = low {
                        y = np_maximum(y, lo);
                    }
                    if let Some(hi) = high {
                        y = np_minimum(y, hi);
                    }
                    if let Some(d) = scale {
                        y = numpy_round(y, d, factor);
                    }
                    *x = y;
                }
            }
        });
        o
    });
    Ok(Some(out(Arc::new(Float64Array::from(v)))))
}

/// `start + index * step` for every index (wrapping, as int64 arithmetic in NumPy does): the key
/// of a sequence primary key from the row it sits in. A no-op for `start = 0, step = 1`.
fn to_keys(indices: &mut [i64], start: i64, step: i64) {
    if start != 0 || step != 1 {
        for v in indices.iter_mut() {
            *v = start.wrapping_add(v.wrapping_mul(step));
        }
    }
}

/// One index in `0..size` per row: `min(floor(u * size), size - 1)` for the uniform `u` of word
/// `slot` of each row's `per_row` words (the draw of a uniform key or pool pick). With `start` and
/// `step` the result is `start + index * step`, the key a sequence primary key gives that row.
#[pyfunction]
#[pyo3(signature = (k0, k1, row_start, n_rows, size, per_row = 1, slot = 0, start = 0, step = 1))]
#[allow(clippy::too_many_arguments)]
fn uniform_index(
    py: Python<'_>,
    k0: u64,
    k1: u64,
    row_start: u64,
    n_rows: usize,
    size: i64,
    per_row: usize,
    slot: usize,
    start: i64,
    step: i64,
) -> PyResult<PyArray> {
    check_slot(per_row, slot, 1)?;
    if size < 1 {
        return Err(err("size must be positive".into()));
    }
    let v = py.detach(|| {
        let mut v = rng::uniform_index([k0, k1], row_start, n_rows, size, per_row, slot);
        to_keys(&mut v, start, step);
        v
    });
    Ok(out(Arc::new(Int64Array::from(v))))
}

/// `start + (row_start + i) * step` for `i` in `0..n_rows` (wrapping): a sequence column.
#[pyfunction]
fn range_values(py: Python<'_>, start: i64, step: i64, row_start: i64, n_rows: usize) -> PyArray {
    let v = py.detach(|| {
        (0..n_rows as i64)
            .map(|i| start.wrapping_add(row_start.wrapping_add(i).wrapping_mul(step)))
            .collect::<Vec<i64>>()
    });
    out(Arc::new(Int64Array::from(v)))
}

/// `pool[i]` for an index `i` drawn per row as in `uniform_index` over the pool's length (the
/// pick of the name, city and company providers), as a `string` array.
#[pyfunction]
fn pool_pick(
    py: Python<'_>,
    pool: PyArray,
    k0: u64,
    k1: u64,
    row_start: u64,
    n_rows: usize,
) -> PyResult<PyArray> {
    let (pool, _) = pool.into_inner();
    let col = strings::Col::from_array(&pool).map_err(err)?;
    if col.is_empty() {
        return Err(err("pool_pick needs a non-empty pool".into()));
    }
    let r = py
        .detach(|| {
            let index = rng::uniform_index([k0, k1], row_start, n_rows, col.len() as i64, 1, 0);
            strings::pool_take(&col, &Int64Array::from(index))
        })
        .map_err(err)?;
    Ok(out(Arc::new(r)))
}

/// The alias draw of `alias_sample`, taken from `pool` (strings): `pool[draw]` per row.
#[pyfunction]
#[pyo3(signature = (prob, alias, pool, k0, k1, row_start, n_rows, per_row = 2, slot = 0))]
#[allow(clippy::too_many_arguments)]
fn alias_pool(
    py: Python<'_>,
    prob: PyArray,
    alias: PyArray,
    pool: PyArray,
    k0: u64,
    k1: u64,
    row_start: u64,
    n_rows: usize,
    per_row: usize,
    slot: usize,
) -> PyResult<PyArray> {
    check_slot(per_row, slot, 2)?;
    let (prob_arr, _) = prob.into_inner();
    let prob = f64_slice(&prob_arr, "prob")?;
    let alias = i64_array(alias, "alias")?;
    let (pool, _) = pool.into_inner();
    let col = strings::Col::from_array(&pool).map_err(err)?;
    if prob.len() != alias.len() || prob.len() != col.len() || prob.is_empty() {
        return Err(err(
            "prob, alias and pool must have the same non-zero length".into(),
        ));
    }
    let r = py
        .detach(|| {
            let draw = alias::sample(
                prob,
                alias.values(),
                [k0, k1],
                row_start,
                n_rows,
                per_row,
                slot,
            );
            strings::pool_take(&col, &Int64Array::from(draw))
        })
        .map_err(err)?;
    Ok(out(Arc::new(r)))
}

/// The alias draw of `alias_sample`, taken from `values` (float64): `values[draw]` per row.
#[pyfunction]
#[pyo3(signature = (prob, alias, values, k0, k1, row_start, n_rows, per_row = 2, slot = 0))]
#[allow(clippy::too_many_arguments)]
fn alias_values(
    py: Python<'_>,
    prob: PyArray,
    alias: PyArray,
    values: PyArray,
    k0: u64,
    k1: u64,
    row_start: u64,
    n_rows: usize,
    per_row: usize,
    slot: usize,
) -> PyResult<PyArray> {
    check_slot(per_row, slot, 2)?;
    let (prob_arr, _) = prob.into_inner();
    let prob = f64_slice(&prob_arr, "prob")?;
    let alias = i64_array(alias, "alias")?;
    let (values_arr, _) = values.into_inner();
    let values = f64_slice(&values_arr, "values")?;
    if prob.len() != alias.len() || prob.len() != values.len() || prob.is_empty() {
        return Err(err(
            "prob, alias and values must have the same non-zero length".into(),
        ));
    }
    let v = py.detach(|| {
        alias::sample(
            prob,
            alias.values(),
            [k0, k1],
            row_start,
            n_rows,
            per_row,
            slot,
        )
        .into_iter()
        .map(|i| values[i as usize])
        .collect::<Vec<f64>>()
    });
    Ok(out(Arc::new(Float64Array::from(v))))
}

/// What one piece of `compose_strings` reads, before its arrays are borrowed.
enum PieceSpec {
    Pool {
        pool: ArrayRef,
        key: [u64; 2],
    },
    Int {
        key: [u64; 2],
        low: i64,
        high: i64,
        width: usize,
        remap: Option<(i64, i64)>,
    },
    Column {
        array: ArrayRef,
        width: usize,
        slug: bool,
    },
}

fn piece_spec(item: &Bound<'_, PyAny>) -> PyResult<PieceSpec> {
    let t = item.cast::<PyTuple>()?;
    let kind: String = t.get_item(0)?.extract()?;
    match kind.as_str() {
        // ("pool", pool, k0, k1)
        "pool" => Ok(PieceSpec::Pool {
            pool: t.get_item(1)?.extract::<PyArray>()?.into_inner().0,
            key: [t.get_item(2)?.extract()?, t.get_item(3)?.extract()?],
        }),
        // ("int", k0, k1, low, high, width, remap_from, remap_to)
        "int" => {
            let from: Option<i64> = t.get_item(6)?.extract()?;
            let to: Option<i64> = t.get_item(7)?.extract()?;
            Ok(PieceSpec::Int {
                key: [t.get_item(1)?.extract()?, t.get_item(2)?.extract()?],
                low: t.get_item(3)?.extract()?,
                high: t.get_item(4)?.extract()?,
                width: t.get_item(5)?.extract()?,
                remap: from.zip(to),
            })
        }
        // ("col", array, width, slug)
        "col" => Ok(PieceSpec::Column {
            array: t.get_item(1)?.extract::<PyArray>()?.into_inner().0,
            width: t.get_item(2)?.extract()?,
            slug: t.get_item(3)?.extract()?,
        }),
        other => Err(err(format!("unknown piece kind {other:?}"))),
    }
}

/// `literals[0] + piece + literals[1] + ...` where a piece is a pool entry picked per row, an
/// integer drawn per row, or a column of the caller (see `strings::compose`). Pieces:
/// `("pool", pool, k0, k1)`, `("int", k0, k1, low, high, width, remap_from, remap_to)` and
/// `("col", array, width, slug)`. A pool pick takes `min(floor(u * len), len - 1)` and an integer
/// `low + min(floor(u * (high - low)), high - low - 1)`, for the uniform `u` of word 0 of each row of
/// the piece's own stream; an integer equal to `remap_from` is written as `remap_to`.
#[pyfunction]
fn compose_strings(
    py: Python<'_>,
    literals: Vec<String>,
    pieces: Vec<Bound<'_, PyAny>>,
    row_start: u64,
    n_rows: usize,
) -> PyResult<PyArray> {
    let specs: Vec<PieceSpec> = pieces.iter().map(piece_spec).collect::<PyResult<_>>()?;
    for spec in &specs {
        if let PieceSpec::Int { low, high, .. } = spec {
            if high <= low {
                return Err(err("an integer piece needs low < high".into()));
            }
        }
    }
    let r = py
        .detach(|| {
            let mut built: Vec<strings::Piece<'_>> = Vec::with_capacity(specs.len());
            for spec in &specs {
                built.push(match spec {
                    PieceSpec::Pool { pool, key } => {
                        let col = strings::Col::from_array(pool)?;
                        if col.is_empty() {
                            return Err("compose needs a non-empty pool".to_string());
                        }
                        let index =
                            rng::uniform_index(*key, row_start, n_rows, col.len() as i64, 1, 0);
                        strings::Piece::Pool { pool: col, index }
                    }
                    PieceSpec::Int {
                        key,
                        low,
                        high,
                        width,
                        remap,
                    } => {
                        let mut values =
                            rng::uniform_index(*key, row_start, n_rows, high - low, 1, 0);
                        for v in values.iter_mut() {
                            *v += low;
                            if let Some((from, to)) = remap {
                                if *v == *from {
                                    *v = *to;
                                }
                            }
                        }
                        strings::Piece::Int {
                            values,
                            width: *width,
                        }
                    }
                    PieceSpec::Column { array, width, slug } => strings::Piece::Column {
                        col: strings::Col::from_array(array)?,
                        width: *width,
                        slug: *slug,
                    },
                });
            }
            strings::compose(&literals, &built, n_rows)
        })
        .map_err(err)?;
    Ok(out(Arc::new(r)))
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
    m.add_function(wrap_pyfunction!(zipf_guide, m)?)?;
    m.add_function(wrap_pyfunction!(zipf_draw, m)?)?;
    m.add_function(wrap_pyfunction!(lognormal_values, m)?)?;
    m.add_function(wrap_pyfunction!(range_values, m)?)?;
    m.add_function(wrap_pyfunction!(uniform_index, m)?)?;
    m.add_function(wrap_pyfunction!(pool_pick, m)?)?;
    m.add_function(wrap_pyfunction!(alias_pool, m)?)?;
    m.add_function(wrap_pyfunction!(alias_values, m)?)?;
    m.add_function(wrap_pyfunction!(compose_strings, m)?)?;
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

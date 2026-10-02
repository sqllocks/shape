//! Row-sequential kernels of the relational strategies (P4-04d): the passes that need the whole
//! column of a table, because the value of a row depends on the rows before it (the first row of
//! each parent, the version of a row inside its business key, a parent that is full).
//!
//! `dense_rows` and `group_sums` are the single-pass versions of the key lookups and the compute
//! phase's grouped sums that the post-passes use for a sequence key (P4-07).
//!
//! Each function is a function of its inputs and the stream key alone, never of threads or call
//! order. Reference twin: `shape.kernel.reference.relational` (every result is an integer or a
//! flag, so the twin agrees bit for bit).

use std::sync::Arc;

use arrow_array::cast::AsArray;
use arrow_array::types::{Float64Type, Int64Type};
use arrow_array::{Array, ArrayRef, BooleanArray, Float64Array, Int64Array};
use arrow_buffer::{NullBuffer, ScalarBuffer};
use pyo3::prelude::*;
use pyo3_arrow::PyArray;

use super::rng::{below, fill_words};
use super::{err, f64_values, i64_array, out};

/// Codes are dense group ids (`pyarrow` dictionary codes): `0 <= code < len`. A negative code
/// means "no group".
fn check_codes(codes: &[i64]) -> Result<(), String> {
    let n = codes.len() as i64;
    if codes.iter().any(|&c| c >= n) {
        return Err("group codes must be dense: below the number of rows".into());
    }
    Ok(())
}

/// `flags[i]` is true when no row before `i` has the same non-negative code. Rows with a negative
/// code are never first.
pub fn first_flags(codes: &[i64]) -> Result<Vec<bool>, String> {
    check_codes(codes)?;
    let mut seen = vec![false; codes.len()];
    Ok(codes
        .iter()
        .map(|&c| {
            if c < 0 {
                return false;
            }
            let slot = &mut seen[c as usize];
            let first = !*slot;
            *slot = true;
            first
        })
        .collect())
}

/// Rows grouped by code (negative codes dropped), each group in row order: `(order, starts)`
/// where group `g` is `order[starts[g]..starts[g + 1]]`.
fn group_rows(codes: &[i64]) -> (Vec<usize>, Vec<usize>) {
    let n = codes.len();
    let mut starts = vec![0usize; n + 1];
    for &c in codes {
        if c >= 0 {
            starts[c as usize + 1] += 1;
        }
    }
    for g in 0..n {
        starts[g + 1] += starts[g];
    }
    let mut fill = starts.clone();
    let mut order = vec![0usize; starts[n]];
    for (i, &c) in codes.iter().enumerate() {
        if c >= 0 {
            order[fill[c as usize]] = i;
            fill[c as usize] += 1;
        }
    }
    (order, starts)
}

/// The three arrays of [`group_order`]: rank, size and next.
pub type GroupOrder = (Vec<i64>, Vec<i64>, Vec<i64>);

/// The position of every row inside its group when the group is sorted by `keys` (ties keep row
/// order): `(rank, size, next)`. `next` is the row that follows in the group's order, or -1 for
/// the last one. Rows with a negative code get `(-1, 0, -1)`.
pub fn group_order(codes: &[i64], keys: &[i64]) -> Result<GroupOrder, String> {
    if codes.len() != keys.len() {
        return Err("codes and keys must have the same length".into());
    }
    check_codes(codes)?;
    let n = codes.len();
    let (mut order, starts) = group_rows(codes);
    let mut rank = vec![-1i64; n];
    let mut size = vec![0i64; n];
    let mut next = vec![-1i64; n];
    for g in 0..n {
        let rows = &mut order[starts[g]..starts[g + 1]];
        rows.sort_by_key(|&i| keys[i]); // stable: ties stay in row order
        let m = rows.len();
        for (v, &i) in rows.iter().enumerate() {
            rank[i] = v as i64;
            size[i] = m as i64;
            if v + 1 < m {
                next[i] = rows[v + 1] as i64;
            }
        }
    }
    Ok((rank, size, next))
}

fn mix64(mut z: u64) -> u64 {
    z ^= z >> 30;
    z = z.wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z ^= z >> 27;
    z = z.wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

/// The stream of one group of rows, keyed by the first row of the group.
pub fn group_key(key: [u64; 2], anchor: u64) -> [u64; 2] {
    [
        mix64(key[0] ^ mix64(anchor)),
        mix64(key[1] ^ anchor.wrapping_mul(0x9E37_79B9_7F4A_7C15) ^ 0xD1B5_4A32_D192_ED03),
    ]
}

/// Effective-date day offsets of an SCD type 2 table. A group of `m` rows draws `m` integers in
/// `0 .. max(total_days - min_gap * (m - 1), m)` from its own stream, sorts them and gives the
/// `v`-th row (in row order) the `v`-th value plus `min_gap * v`, capped at `total_days`. A group
/// of one row draws a single offset in `0 .. max(total_days, 1)`. Rows with a negative code get -1.
pub fn scd2_offsets(
    codes: &[i64],
    total_days: i64,
    min_gap: i64,
    key: [u64; 2],
) -> Result<Vec<i64>, String> {
    if total_days < 0 || min_gap < 0 {
        return Err("total_days and min_gap must be non-negative".into());
    }
    check_codes(codes)?;
    let (order, starts) = group_rows(codes);
    let mut result = vec![-1i64; codes.len()];
    let mut words: Vec<u64> = Vec::new();
    for g in 0..codes.len() {
        let rows = &order[starts[g]..starts[g + 1]];
        let m = rows.len();
        if m == 0 {
            continue;
        }
        let gkey = group_key(key, rows[0] as u64);
        words.clear();
        words.resize(m, 0);
        fill_words(gkey, 0, &mut words);
        if m == 1 {
            result[rows[0]] = below(words[0], total_days.max(1) as u64) as i64;
            continue;
        }
        let usable = (total_days - min_gap * (m as i64 - 1)).max(m as i64);
        let mut offsets: Vec<i64> = words
            .iter()
            .map(|&w| below(w, usable as u64) as i64)
            .collect();
        offsets.sort_unstable();
        for (v, &row) in rows.iter().enumerate() {
            result[row] = (offsets[v] + min_gap * v as i64).min(total_days);
        }
    }
    Ok(result)
}

const CAP_ATTEMPTS: u64 = 64;

/// Parent indices (`0 .. pool`) with at most `max_per_parent` rows each. Rows are visited in
/// order; a row whose parent is full draws another parent from its own words (word `row * 64 +
/// attempt`) until one has room, after 64 failed draws it takes the first parent with room after
/// its first draw, and if every parent is full it keeps that first draw.
pub fn cap_per_parent(
    indices: &[i64],
    pool: i64,
    max_per_parent: i64,
    key: [u64; 2],
) -> Result<Vec<i64>, String> {
    if pool < 1 || max_per_parent < 1 {
        return Err("pool and max_per_parent must be positive".into());
    }
    if indices.iter().any(|&i| i < 0 || i >= pool) {
        return Err("indices must lie in 0..pool".into());
    }
    let pool_u = pool as usize;
    let mut counts = vec![0u32; pool_u];
    let cap = max_per_parent.min(i64::from(u32::MAX)) as u32;
    let mut full = 0usize;
    let mut result = Vec::with_capacity(indices.len());
    let mut word = [0u64; 1];
    for (row, &index) in indices.iter().enumerate() {
        let mut chosen = index as usize;
        if counts[chosen] >= cap {
            let mut first = 0usize;
            let mut found = false;
            for attempt in 0..CAP_ATTEMPTS {
                fill_words(key, row as u64 * CAP_ATTEMPTS + attempt, &mut word);
                let candidate = below(word[0], pool as u64) as usize;
                if attempt == 0 {
                    first = candidate;
                    if full == pool_u {
                        // every parent is at the cap: spread the surplus uniformly
                        chosen = candidate;
                        found = true;
                        break;
                    }
                }
                if counts[candidate] < cap {
                    chosen = candidate;
                    found = true;
                    break;
                }
            }
            if !found {
                chosen = (0..pool_u)
                    .map(|k| (first + k) % pool_u)
                    .find(|&c| counts[c] < cap)
                    .unwrap_or(first);
            }
        }
        counts[chosen] += 1;
        if counts[chosen] == cap {
            full += 1;
        }
        result.push(chosen as i64);
    }
    Ok(result)
}

/// Row of the sequence key `start, start + 1, ...` (`size` rows) that holds each key of `keys`; null
/// where the key is null or outside the sequence.
pub fn dense_rows(keys: &Int64Array, start: i64, size: i64) -> Int64Array {
    let values = keys.values();
    let mut rows = Vec::with_capacity(values.len());
    let mut valid = Vec::with_capacity(values.len());
    for &k in values.iter() {
        let row = k.wrapping_sub(start);
        let inside = k >= start && row < size;
        rows.push(if inside { row } else { 0 });
        valid.push(inside);
    }
    if let Some(nulls) = keys.nulls() {
        for (i, v) in valid.iter_mut().enumerate() {
            *v &= nulls.is_valid(i);
        }
    }
    Int64Array::new(
        ScalarBuffer::from(rows),
        Some(NullBuffer::from(valid)).filter(|n| n.null_count() > 0),
    )
}

/// For each `u`, how many entries of the ascending `cdf` are `<= u`: NumPy's
/// `searchsorted(cdf, u, side="right")` (a NaN `u` gives `cdf.len()`, as there). This is the draw
/// of a discrete distribution from its cumulative table (a Zipf foreign key, a Poisson count).
///
/// Rust's `partition_point` is a branchless binary search; it is about three times faster than
/// NumPy's search for a table of a few thousand entries.
pub fn cdf_search(cdf: &[f64], us: &[f64]) -> Vec<i64> {
    let m = cdf.len() as i64;
    us.iter()
        .map(|&u| {
            if u.is_nan() {
                m
            } else {
                cdf.partition_point(|c| *c <= u) as i64
            }
        })
        .collect()
}

/// Per parent row of a sequence key `start, start + 1, ...` (`size` rows): the sum and the count
/// of the non-null `values` of the child rows whose key in `keys` is that parent's. Child rows are
/// added in row order (the order of a grouped sum), a child with a null key or a key outside the
/// sequence is skipped. Integer sums wrap on overflow, as Arrow's do.
fn group_sums<T: Copy + Default>(
    keys: &Int64Array,
    values: &[T],
    value_valid: Option<&NullBuffer>,
    start: i64,
    size: usize,
    add: impl Fn(T, T) -> T,
) -> (Vec<T>, Vec<i64>) {
    let mut sums = vec![T::default(); size];
    let mut counts = vec![0i64; size];
    let key_valid = keys.nulls();
    for (i, (&k, &v)) in keys.values().iter().zip(values).enumerate() {
        let row = k.wrapping_sub(start);
        if k < start || row >= size as i64 {
            continue;
        }
        if key_valid.is_some_and(|n| n.is_null(i)) || value_valid.is_some_and(|n| n.is_null(i)) {
            continue;
        }
        let r = row as usize;
        sums[r] = add(sums[r], v);
        counts[r] += 1;
    }
    (sums, counts)
}

fn values(a: PyArray, what: &str) -> PyResult<Vec<i64>> {
    Ok(i64_array(a, what)?.values().to_vec())
}

fn int_out(v: Vec<i64>) -> PyArray {
    out(Arc::new(Int64Array::from(v)) as ArrayRef)
}

/// Whether each row is the first with its (non-negative) group code.
#[pyfunction]
#[pyo3(name = "first_flags")]
fn first_flags_py(py: Python<'_>, codes: PyArray) -> PyResult<PyArray> {
    let codes = values(codes, "codes")?;
    let flags = py.detach(|| first_flags(&codes)).map_err(err)?;
    Ok(out(Arc::new(BooleanArray::from(flags)) as ArrayRef))
}

/// `(rank, size, next)` of every row inside its group sorted by `keys` (see `group_order`).
#[pyfunction]
#[pyo3(name = "group_order")]
fn group_order_py(
    py: Python<'_>,
    codes: PyArray,
    keys: PyArray,
) -> PyResult<(PyArray, PyArray, PyArray)> {
    let codes = values(codes, "codes")?;
    let keys = values(keys, "keys")?;
    let (rank, size, next) = py.detach(|| group_order(&codes, &keys)).map_err(err)?;
    Ok((int_out(rank), int_out(size), int_out(next)))
}

/// Day offsets of the effective dates of an SCD type 2 table (see `scd2_offsets`).
#[pyfunction]
#[pyo3(name = "scd2_offsets")]
fn scd2_offsets_py(
    py: Python<'_>,
    codes: PyArray,
    total_days: i64,
    min_gap: i64,
    k0: u64,
    k1: u64,
) -> PyResult<PyArray> {
    let codes = values(codes, "codes")?;
    let v = py
        .detach(|| scd2_offsets(&codes, total_days, min_gap, [k0, k1]))
        .map_err(err)?;
    Ok(int_out(v))
}

/// Parent indices with at most `max_per_parent` rows per parent (see `cap_per_parent`).
#[pyfunction]
#[pyo3(name = "cap_per_parent")]
fn cap_per_parent_py(
    py: Python<'_>,
    indices: PyArray,
    pool: i64,
    max_per_parent: i64,
    k0: u64,
    k1: u64,
) -> PyResult<PyArray> {
    let indices = values(indices, "indices")?;
    let v = py
        .detach(|| cap_per_parent(&indices, pool, max_per_parent, [k0, k1]))
        .map_err(err)?;
    Ok(int_out(v))
}

/// Rows of a sequence key for each key (see `dense_rows`); null for a key that is not in it.
#[pyfunction]
#[pyo3(name = "dense_rows")]
fn dense_rows_py(py: Python<'_>, keys: PyArray, start: i64, size: i64) -> PyResult<PyArray> {
    let keys = i64_array(keys, "keys")?;
    let rows = py.detach(|| dense_rows(&keys, start, size));
    Ok(out(Arc::new(rows) as ArrayRef))
}

/// How many entries of the ascending float64 table `cdf` are `<= u`, for each `u` (see
/// `cdf_search`): an int64 array.
#[pyfunction]
#[pyo3(name = "cdf_search")]
fn cdf_search_py(py: Python<'_>, cdf: PyArray, u: PyArray) -> PyResult<PyArray> {
    let cdf = f64_values(cdf, "cdf")?;
    let u = f64_values(u, "u")?;
    Ok(int_out(py.detach(|| cdf_search(&cdf, &u))))
}

/// `(sums, counts)` per parent row of a sequence key (see `group_sums`): `values` is int64 or
/// float64 and the sums have its type.
#[pyfunction]
#[pyo3(name = "group_sums")]
fn group_sums_py(
    py: Python<'_>,
    keys: PyArray,
    values: PyArray,
    start: i64,
    size: i64,
) -> PyResult<(PyArray, PyArray)> {
    if size < 0 {
        return Err(err("size must not be negative".into()));
    }
    let keys = i64_array(keys, "keys")?;
    let (column, _) = values.into_inner();
    if column.len() != keys.len() {
        return Err(err("keys and values must have the same length".into()));
    }
    let size = size as usize;
    if let Some(v) = column.as_primitive_opt::<Float64Type>() {
        let (sums, counts) =
            py.detach(|| group_sums(&keys, v.values(), v.nulls(), start, size, |a, b| a + b));
        return Ok((
            out(Arc::new(Float64Array::from(sums)) as ArrayRef),
            int_out(counts),
        ));
    }
    if let Some(v) = column.as_primitive_opt::<Int64Type>() {
        let (sums, counts) =
            py.detach(|| group_sums(&keys, v.values(), v.nulls(), start, size, i64::wrapping_add));
        return Ok((int_out(sums), int_out(counts)));
    }
    Err(err("values must be an int64 or float64 array".into()))
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(cdf_search_py, m)?)?;
    m.add_function(wrap_pyfunction!(dense_rows_py, m)?)?;
    m.add_function(wrap_pyfunction!(group_sums_py, m)?)?;
    m.add_function(wrap_pyfunction!(first_flags_py, m)?)?;
    m.add_function(wrap_pyfunction!(group_order_py, m)?)?;
    m.add_function(wrap_pyfunction!(scd2_offsets_py, m)?)?;
    m.add_function(wrap_pyfunction!(cap_per_parent_py, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A tiny deterministic generator of `f64` in `[0, 1)`.
    fn unit(state: &mut u64) -> f64 {
        *state = state
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (*state >> 11) as f64 / (1u64 << 53) as f64
    }

    fn naive(cdf: &[f64], u: f64) -> i64 {
        if u.is_nan() {
            return cdf.len() as i64;
        }
        cdf.iter().filter(|c| **c <= u).count() as i64
    }

    #[test]
    fn cdf_search_equals_the_plain_search_for_every_table_shape() {
        let mut state = 12345u64;
        for m in [0usize, 1, 2, 15, 16, 17, 100, 1000, 5000] {
            for kind in 0..4 {
                // an ascending table: random steps (kind 0), Zipf-like (1), with ties (2), flat
                // then steep (3)
                let mut cdf = Vec::with_capacity(m);
                let mut total = 0.0;
                for k in 0..m {
                    let step = match kind {
                        0 => unit(&mut state),
                        1 => ((k + 1) as f64).powf(-1.2),
                        2 => (unit(&mut state) * 4.0).floor(),
                        _ => {
                            if k < m / 2 {
                                1e-9
                            } else {
                                unit(&mut state)
                            }
                        }
                    };
                    total += step;
                    cdf.push(total);
                }
                let top = cdf.last().copied().unwrap_or(1.0);
                let scale = if top > 0.0 { top } else { 1.0 };
                let mut queries = Vec::new();
                for _ in 0..20_000 {
                    queries.push(unit(&mut state) * scale * 1.1 - scale * 0.05);
                }
                // the entries themselves and their neighbours, the table's ends, the bucket edges
                for &c in &cdf {
                    queries.push(c);
                    queries.push(f64::from_bits(c.to_bits().wrapping_add(1)));
                    queries.push(f64::from_bits(c.to_bits().wrapping_sub(1)));
                }
                for edge in 0..=256 {
                    queries.push(cdf.first().copied().unwrap_or(0.0) + scale * edge as f64 / 256.0);
                }
                queries.extend([f64::NAN, f64::INFINITY, f64::NEG_INFINITY, 0.0, -0.0, scale]);
                let got = cdf_search(&cdf, &queries);
                for (i, &u) in queries.iter().enumerate() {
                    assert_eq!(got[i], naive(&cdf, u), "m {m} kind {kind} u {u:e}");
                }
                // fewer queries than buckets: the plain path
                let few = cdf_search(&cdf, &queries[..10.min(queries.len())]);
                for (i, &u) in queries.iter().take(10).enumerate() {
                    assert_eq!(few[i], naive(&cdf, u));
                }
            }
        }
    }

    #[test]
    fn dense_rows_finds_the_row_of_a_sequence_key() {
        let keys = Int64Array::from(vec![Some(5), Some(7), None, Some(4), Some(9), Some(8)]);
        let rows = dense_rows(&keys, 5, 4);
        let got: Vec<Option<i64>> = rows.iter().collect();
        assert_eq!(got, vec![Some(0), Some(2), None, None, None, Some(3)]);
        assert_eq!(
            dense_rows(&Int64Array::from(vec![1, 2]), 1, 2).null_count(),
            0
        );
    }

    #[test]
    fn group_sums_adds_in_row_order_and_skips_nulls_and_strangers() {
        let keys = Int64Array::from(vec![Some(1), Some(2), Some(1), None, Some(9), Some(2)]);
        let values = [0.5, 1.0, 0.25, 7.0, 3.0, 2.0];
        let (sums, counts) = group_sums(&keys, &values, None, 1, 3, |a, b| a + b);
        assert_eq!(sums, vec![0.75, 3.0, 0.0]);
        assert_eq!(counts, vec![2, 2, 0]);
        let valid = NullBuffer::from(vec![true, true, false, true, true, true]);
        let (sums, counts) = group_sums(&keys, &values, Some(&valid), 1, 3, |a, b| a + b);
        assert_eq!(sums, vec![0.5, 3.0, 0.0]);
        assert_eq!(counts, vec![1, 2, 0]);
        let (isums, _) = group_sums(
            &keys,
            &[i64::MAX, 1, 1, 0, 0, 0],
            None,
            1,
            2,
            i64::wrapping_add,
        );
        assert_eq!(isums, vec![i64::MIN, 1]);
    }

    #[test]
    fn first_flags_marks_the_first_row_of_each_group() {
        assert_eq!(
            first_flags(&[0, 1, 0, 2, 1, 0]).unwrap(),
            vec![true, true, false, true, false, false]
        );
        assert_eq!(first_flags(&[-1, 0, -1]).unwrap(), vec![false, true, false]);
        assert!(first_flags(&[0, 3]).is_err());
    }

    #[test]
    fn group_order_sorts_inside_groups_and_keeps_ties_in_row_order() {
        let codes = [0, 1, 0, 0, 1, -1];
        let keys = [30, 5, 10, 10, 6, 0];
        let (rank, size, next) = group_order(&codes, &keys).unwrap();
        assert_eq!(rank, vec![2, 0, 0, 1, 1, -1]);
        assert_eq!(size, vec![3, 2, 3, 3, 2, 0]);
        assert_eq!(next, vec![-1, 4, 3, 0, -1, -1]);
    }

    #[test]
    fn scd2_offsets_increase_within_a_group_and_stay_in_range() {
        let codes: Vec<i64> = (0..400).map(|i| i % 40).collect();
        let off = scd2_offsets(&codes, 1000, 3, [1, 2]).unwrap();
        for g in 0..40 {
            let days: Vec<i64> = (0..10).map(|v| off[g + 40 * v]).collect();
            assert!(days.windows(2).all(|w| w[1] >= w[0] + 3), "{days:?}");
            assert!(days.iter().all(|&d| (0..=1000).contains(&d)));
        }
        assert_eq!(
            scd2_offsets(&[0, -1], 10, 1, [1, 2]).unwrap()[1],
            -1,
            "a null business key stays null"
        );
        assert_eq!(off, scd2_offsets(&codes, 1000, 3, [1, 2]).unwrap());
    }

    #[test]
    fn cap_per_parent_never_exceeds_the_cap_and_keeps_rows_with_room() {
        let idx: Vec<i64> = (0..300)
            .map(|i| if i % 3 == 0 { 0 } else { i % 10 })
            .collect();
        let capped = cap_per_parent(&idx, 10, 40, [7, 9]).unwrap();
        let mut counts = [0usize; 10];
        for &c in &capped {
            counts[c as usize] += 1;
        }
        assert!(counts.iter().all(|&c| c <= 40), "{counts:?}");
        assert_eq!(counts.iter().sum::<usize>(), 300);
        assert_eq!(&capped[..4], &idx[..4]);
        assert_eq!(capped, cap_per_parent(&idx, 10, 40, [7, 9]).unwrap());
        // 10 parents x 5 rows cannot hold 100 rows: the surplus is spread, none is lost
        let spread = cap_per_parent(&vec![0; 100], 10, 5, [1, 1]).unwrap();
        assert_eq!(spread.len(), 100);
    }
}

//! Native kernel of sqllocks-shape, exposed to Python as `shape._kernel`.
//!
//! Every function here has a pure-Python twin in `src/shape/kernel/reference/` (T-03), and
//! `SHAPE_KERNEL=auto|rust|python` picks the implementation. Data crosses the boundary through
//! the Arrow PyCapsule interface, so buffers are shared, never copied.

use arrow_array::{Array, RecordBatch};
use arrow_data::ArrayData;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3_arrow::PyArray;
use pyo3_arrow::PyRecordBatch;

/// Address of the first byte of every data buffer of every column, in column order (nulls
/// buffer first when there is one, then values, then any child buffers).
pub fn batch_buffer_addresses(batch: &RecordBatch) -> Vec<usize> {
    let mut out = Vec::new();
    for column in batch.columns() {
        let data = column.to_data();
        collect_addresses(&data, &mut out);
    }
    out
}

fn collect_addresses(data: &ArrayData, out: &mut Vec<usize>) {
    if let Some(nulls) = data.nulls() {
        out.push(nulls.buffer().as_ptr() as usize);
    }
    for buffer in data.buffers() {
        out.push(buffer.as_ptr() as usize);
    }
    for child in data.child_data() {
        collect_addresses(child, out);
    }
}

pub mod hashing;
pub mod profile;
pub mod sketch;

/// Canonical XXH3-64 hash of every element of an Arrow array (T-13). Null and NaN slots are
/// null in the returned uint64 array.
#[pyfunction]
#[pyo3(signature = (array, seed = 0))]
fn hash_array(py: Python<'_>, array: PyArray, seed: u64) -> PyResult<pyo3_arrow::PyArray> {
    let (arr, _field) = array.into_inner();
    let out = py
        .detach(|| hashing::hash_array(&arr, seed))
        .map_err(PyValueError::new_err)?;
    Ok(PyArray::from_array_ref(std::sync::Arc::new(out)))
}

/// The kernel version (equal to the Python package version).
#[pyfunction]
fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

/// Import a `RecordBatch` (any object with `__arrow_c_array__`) and hand it back. Nothing is
/// copied: the exported batch owns the very buffers that were imported.
#[pyfunction]
fn roundtrip_batch(batch: PyRecordBatch) -> PyRecordBatch {
    batch
}

/// Buffer addresses of an imported batch, as seen from Rust (see `batch_buffer_addresses`).
#[pyfunction]
fn buffer_addresses(batch: PyRecordBatch) -> Vec<usize> {
    batch_buffer_addresses(batch.as_ref())
}

/// Row count of an imported batch.
#[pyfunction]
fn num_rows(batch: PyRecordBatch) -> usize {
    batch.as_ref().num_rows()
}

#[pymodule]
fn _kernel(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("NAME", "rust")?;
    sketch::register(m)?;
    profile::register(m)?;
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(roundtrip_batch, m)?)?;
    m.add_function(wrap_pyfunction!(buffer_addresses, m)?)?;
    m.add_function(wrap_pyfunction!(num_rows, m)?)?;
    m.add_function(wrap_pyfunction!(hash_array, m)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow_array::{Int64Array, RecordBatch};
    use std::sync::Arc;

    #[test]
    fn addresses_are_stable_across_clones() {
        let col = Arc::new(Int64Array::from(vec![1, 2, 3])) as Arc<dyn Array>;
        let batch = RecordBatch::try_from_iter([("a", col)]).unwrap();
        let copy = batch.clone();
        assert_eq!(
            batch_buffer_addresses(&batch),
            batch_buffer_addresses(&copy)
        );
        assert_eq!(batch_buffer_addresses(&batch).len(), 1);
    }
}

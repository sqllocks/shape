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

pub mod exact;
pub mod fit;
pub mod hashing;
pub mod numpy_loops;
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

/// Size the global rayon pool (first call wins; later calls only report). ``0`` means all
/// cores. Returns the number of threads the kernel will use.
#[pyfunction]
#[pyo3(signature = (n = 0))]
fn set_threads(n: usize) -> usize {
    let _ = rayon::ThreadPoolBuilder::new()
        .num_threads(n)
        .build_global();
    rayon::current_num_threads()
}

/// Distribution fitting as the reference does it (see `fit.rs`): detect the best of normal, uniform,
/// exponential and lognormal on `sample` (already drawn: at most 2000 values), then score it by
/// refitting on `full` (defaults to `sample`). Returns `{distribution, distribution_params,
/// fit_score}` with `None` where the reference reports nothing. NaN must already be removed.
static NP_EXP: std::sync::OnceLock<Py<PyAny>> = std::sync::OnceLock::new();
static NP_LOG: std::sync::OnceLock<Py<PyAny>> = std::sync::OnceLock::new();

fn call_numpy(f: &'static std::sync::OnceLock<Py<PyAny>>, x: f64, fallback: fn(f64) -> f64) -> f64 {
    Python::attach(|py| {
        f.get()
            .and_then(|func| func.call1(py, (x,)).ok())
            .and_then(|r| r.extract::<f64>(py).ok())
            .unwrap_or_else(|| fallback(x))
    })
}

fn numpy_exp(x: f64) -> f64 {
    call_numpy(&NP_EXP, x, f64::exp)
}

fn numpy_log(x: f64) -> f64 {
    call_numpy(&NP_LOG, x, f64::ln)
}

/// `numpy.log` of every element, in place and without copying: the slice is exposed to numpy as a
/// writable buffer and `np.log(a, out=a)` runs on it (numpy's SIMD `log` is what the parity with
/// scipy depends on, see `fit.rs`). Falls back to libm if numpy cannot be called.
fn numpy_log_array(buf: &mut [f64]) {
    let done = Python::attach(|py| -> Option<()> {
        if buf.is_empty() {
            return Some(());
        }
        let np = py.import("numpy").ok()?;
        // SAFETY: the memoryview (and the array built on it) are dropped before this function
        // returns, while `buf` is still borrowed; nothing else touches `buf` meanwhile.
        let view = unsafe {
            let raw = pyo3::ffi::PyMemoryView_FromMemory(
                buf.as_mut_ptr().cast::<std::os::raw::c_char>(),
                std::mem::size_of_val(buf) as pyo3::ffi::Py_ssize_t,
                pyo3::ffi::PyBUF_WRITE,
            );
            Bound::from_owned_ptr_or_opt(py, raw)?
        };
        let arr = np.call_method1("frombuffer", (view, "<f8")).ok()?;
        // NaN/-inf from log(<=0) are expected results here: callers run under `ignore_fp_errors`.
        let out_kw = pyo3::types::PyDict::new(py);
        out_kw.set_item("out", &arr).ok()?;
        let res = NP_LOG.get()?.call(py, (&arr,), Some(&out_kw));
        res.ok().map(|_| ())
    });
    if done.is_none() {
        buf.iter_mut().for_each(|v| *v = v.ln());
    }
}

/// Run `f` with numpy's floating-point error reporting off (scipy's fit runs under `errstate`),
/// entering and leaving that state once per fit rather than once per `log` pass.
fn ignore_fp_errors<R>(py: Python<'_>, f: impl FnOnce() -> R) -> PyResult<R> {
    let np = py.import("numpy")?;
    let kw = pyo3::types::PyDict::new(py);
    kw.set_item("all", "ignore")?;
    let old = np.call_method("seterr", (), Some(&kw))?;
    let out = f();
    let restore: Bound<'_, pyo3::types::PyDict> = old.cast_into()?;
    np.call_method("seterr", (), Some(&restore))?;
    Ok(out)
}

/// numpy's own `log`/`exp` loops, when they can be called without the GIL (`numpy_loops`).
static NATIVE_LOOPS: std::sync::OnceLock<Option<numpy_loops::NumpyLoops>> =
    std::sync::OnceLock::new();

fn native_loops() -> Option<&'static numpy_loops::NumpyLoops> {
    NATIVE_LOOPS.get().and_then(Option::as_ref)
}

fn native_exp(x: f64) -> f64 {
    native_loops().map_or_else(|| x.exp(), |n| n.exp.scalar(x))
}

fn native_log(x: f64) -> f64 {
    native_loops().map_or_else(|| x.ln(), |n| n.log.scalar(x))
}

fn native_log_array(buf: &mut [f64]) {
    match native_loops() {
        Some(n) => n.log.apply(buf),
        None => buf.iter_mut().for_each(|v| *v = v.ln()),
    }
}

/// Point the fitting code's scalar exp/ln at numpy's (see `fit::set_scalar_hooks`): numpy's loops
/// themselves when they validate, otherwise numpy called through Python.
/// `SHAPE_NUMPY_LOOPS=python` forces the Python route.
fn install_numpy_hooks(py: Python<'_>) -> PyResult<()> {
    if NP_EXP.get().is_none() {
        let np = py.import("numpy")?;
        let _ = NP_EXP.set(np.getattr("exp")?.unbind());
        let _ = NP_LOG.set(np.getattr("log")?.unbind());
        let force_python = std::env::var("SHAPE_NUMPY_LOOPS").is_ok_and(|v| v == "python");
        let loops = NATIVE_LOOPS.get_or_init(|| {
            if force_python {
                None
            } else {
                numpy_loops::load(py)
            }
        });
        if loops.is_some() {
            fit::set_scalar_hooks(native_exp, native_log, native_log_array, true);
        } else {
            fit::set_scalar_hooks(numpy_exp, numpy_log, numpy_log_array, false);
        }
    }
    Ok(())
}

/// How the fitting code evaluates numpy's `log`/`exp`: `"native"` (numpy's own loops, no GIL) or
/// `"python"` (through the interpreter). Installed on the first fit.
#[pyfunction]
fn numpy_loops_mode(py: Python<'_>) -> PyResult<&'static str> {
    install_numpy_hooks(py)?;
    Ok(if native_loops().is_some() {
        "native"
    } else {
        "python"
    })
}

#[pyfunction]
#[pyo3(signature = (sample, full = None))]
fn fit_distribution<'py>(
    py: Python<'py>,
    sample: PyArray,
    full: Option<PyArray>,
) -> PyResult<Bound<'py, pyo3::types::PyDict>> {
    use arrow_array::cast::AsArray;
    use arrow_array::types::Float64Type;
    install_numpy_hooks(py)?;
    let get = |a: PyArray| -> PyResult<Vec<f64>> {
        let (arr, _) = a.into_inner();
        let p = arr
            .as_primitive_opt::<Float64Type>()
            .ok_or_else(|| PyValueError::new_err("fit_distribution needs float64 arrays"))?;
        if p.null_count() > 0 {
            return Err(PyValueError::new_err(
                "fit_distribution needs arrays without nulls",
            ));
        }
        Ok(p.values().to_vec())
    };
    let sample = get(sample)?;
    let full = match full {
        Some(f) => get(f)?,
        None => sample.clone(),
    };
    let (name, params, score) = ignore_fp_errors(py, || {
        py.detach(|| match fit::detect_distribution(&sample) {
            Some((d, params)) => {
                let score = fit::fit_score(&full, d);
                (Some(d), Some(params), score)
            }
            None => (None, None, None),
        })
    })?;
    let out = pyo3::types::PyDict::new(py);
    out.set_item("distribution", name.map(|d| d.name()))?;
    match (name, params) {
        (Some(d), Some(p)) => {
            let dict = pyo3::types::PyDict::new(py);
            if d == fit::Dist::Lognormal {
                dict.set_item("s", p[0])?;
                dict.set_item("loc", p[1])?;
                dict.set_item("scale", p[2])?;
            } else {
                dict.set_item("loc", p[0])?;
                dict.set_item("scale", p[1])?;
            }
            out.set_item("distribution_params", dict)?;
        }
        _ => out.set_item("distribution_params", py.None())?,
    }
    out.set_item("fit_score", score)?;
    Ok(out)
}

/// `(shape, scale, dL/dloc, loglik)` of the lognormal objective at `loc` (for differential
/// tests of the fitting pieces).
#[pyfunction]
fn lognorm_probe(py: Python<'_>, data: PyArray, loc: f64) -> PyResult<(f64, f64, f64, f64)> {
    use arrow_array::cast::AsArray;
    use arrow_array::types::Float64Type;
    install_numpy_hooks(py)?;
    let (arr, _) = data.into_inner();
    let p = arr
        .as_primitive_opt::<Float64Type>()
        .ok_or_else(|| PyValueError::new_err("lognorm_probe needs a float64 array"))?;
    let values = p.values();
    // detached: the parallel passes call numpy from rayon workers, which need the GIL
    ignore_fp_errors(py, || py.detach(|| fit::lognorm_probe(values, loc)))
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

/// Process that imported the kernel. rayon's worker threads do not survive `fork()`, so a forked
/// child (multiprocessing's fork pool) that enters the global pool would wait forever; it runs
/// serially instead.
static INIT_PID: std::sync::OnceLock<u32> = std::sync::OnceLock::new();

pub(crate) fn can_par() -> bool {
    INIT_PID.get().is_none_or(|p| *p == std::process::id())
}

#[pymodule]
fn _kernel(m: &Bound<'_, PyModule>) -> PyResult<()> {
    let _ = INIT_PID.set(std::process::id());
    m.add("NAME", "rust")?;
    sketch::register(m)?;
    profile::register(m)?;
    exact::register(m)?;
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_function(wrap_pyfunction!(numpy_loops_mode, m)?)?;
    m.add_function(wrap_pyfunction!(roundtrip_batch, m)?)?;
    m.add_function(wrap_pyfunction!(buffer_addresses, m)?)?;
    m.add_function(wrap_pyfunction!(num_rows, m)?)?;
    m.add_function(wrap_pyfunction!(hash_array, m)?)?;
    m.add_function(wrap_pyfunction!(set_threads, m)?)?;
    m.add_function(wrap_pyfunction!(fit_distribution, m)?)?;
    m.add_function(wrap_pyfunction!(lognorm_probe, m)?)?;
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

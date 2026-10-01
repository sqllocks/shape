//! numpy's own float64 `log` and `exp` inner loops, called without the GIL.
//!
//! The likelihood of the lognormal fit depends on numpy's SIMD `log` (an Intel SVML routine on
//! x86-64 Linux that differs from libm in the last bit for some inputs), so the kernel has to
//! call that very routine. Going through `numpy.log` costs a GIL acquisition per call, and the
//! profiler's worker threads hold the GIL most of the time, so the parallel passes of `fit.rs`
//! spent most of their time waiting for it. A ufunc keeps its one-dimensional loops in a public
//! C struct (`PyUFuncObject`, `numpy/ufuncobject.h`); the loop numpy picked for `d->d` at import
//! (the CPU-dispatched one) is called here directly, so the results are numpy's by
//! construction and no Python state is touched.
//!
//! The struct is read only after it is cross-checked against numpy's Python-level view of the
//! ufunc, and the loop is used only if a bitwise self-test against `numpy.log` / `numpy.exp`
//! passes; otherwise [`load`] returns `None` and the caller keeps calling numpy through Python.

use std::ffi::{c_char, c_int, c_void, CStr};

use pyo3::prelude::*;
use pyo3::types::PyAnyMethods;

type LoopFn = unsafe extern "C" fn(*mut *mut c_char, *const isize, *const isize, *mut c_void);

/// The leading fields of `PyUFuncObject` (stable since numpy 1.x; `types` is `char *`).
#[repr(C)]
struct UFuncHead {
    base: pyo3::ffi::PyObject,
    nin: c_int,
    nout: c_int,
    nargs: c_int,
    identity: c_int,
    functions: *const Option<LoopFn>,
    data: *const *mut c_void,
    ntypes: c_int,
    reserved1: c_int,
    name: *const c_char,
    types: *const c_char,
}

/// numpy type number of `float64`.
const NPY_DOUBLE: c_char = 12;

/// One numpy inner loop for `float64 -> float64`.
#[derive(Clone, Copy)]
pub struct Loop {
    f: LoopFn,
    data: *mut c_void,
}

// SAFETY: the loops are pure functions of their arguments (no global state, no GIL).
unsafe impl Send for Loop {}
unsafe impl Sync for Loop {}

impl Loop {
    /// Apply the function to every element of `buf`, in place.
    pub fn apply(&self, buf: &mut [f64]) {
        if buf.is_empty() {
            return;
        }
        let p = buf.as_mut_ptr().cast::<c_char>();
        let mut args = [p, p];
        let dims = [buf.len() as isize];
        let steps = [std::mem::size_of::<f64>() as isize; 2];
        // SAFETY: `f` is the ufunc's loop for float64 -> float64, called with one contiguous
        // input and output (the same array, which numpy's own `out=` calls also do).
        unsafe { (self.f)(args.as_mut_ptr(), dims.as_ptr(), steps.as_ptr(), self.data) }
    }

    pub fn scalar(&self, x: f64) -> f64 {
        let mut v = [x];
        self.apply(&mut v);
        v[0]
    }
}

pub struct NumpyLoops {
    pub log: Loop,
    pub exp: Loop,
}

/// The `d->d` loop of the ufunc `np.<name>`, or `None` if the ufunc does not look the way this
/// module expects.
fn find(py: Python<'_>, np: &Bound<'_, PyModule>, name: &str) -> Option<Loop> {
    let ufunc = np.getattr(name).ok()?;
    if ufunc.get_type().name().ok()? != "ufunc" {
        return None;
    }
    let get_int = |attr: &str| -> Option<i64> { ufunc.getattr(attr).ok()?.extract().ok() };
    let (nin, nout, nargs, ntypes) = (
        get_int("nin")?,
        get_int("nout")?,
        get_int("nargs")?,
        get_int("ntypes")?,
    );
    if (nin, nout, nargs) != (1, 1, 2) || !(1..128).contains(&ntypes) {
        return None;
    }
    let types: Vec<String> = ufunc.getattr("types").ok()?.extract().ok()?;
    let idx = types.iter().position(|t| t == "d->d")?;
    let _ = py;
    // SAFETY: a ufunc object is a `PyUFuncObject`; the fields read here come first in it and
    // are checked against the Python-level attributes before any pointer in them is followed.
    unsafe {
        let head = &*(ufunc.as_ptr() as *const UFuncHead);
        if (head.nin, head.nout, head.nargs) != (1, 1, 2)
            || i64::from(head.ntypes) != ntypes
            || head.functions.is_null()
            || head.data.is_null()
            || head.types.is_null()
            || head.name.is_null()
            || CStr::from_ptr(head.name).to_str().ok()? != name
            || *head.types.add(2 * idx) != NPY_DOUBLE
            || *head.types.add(2 * idx + 1) != NPY_DOUBLE
        {
            return None;
        }
        let f = (*head.functions.add(idx))?;
        Some(Loop {
            f,
            data: *head.data.add(idx),
        })
    }
}

struct Lcg(u64);

impl Lcg {
    fn next(&mut self) -> f64 {
        self.0 = self
            .0
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        ((self.0 >> 11) as f64 + 0.5) / (1u64 << 53) as f64
    }
}

fn same(a: f64, b: f64) -> bool {
    a.to_bits() == b.to_bits() || (a.is_nan() && b.is_nan())
}

/// Inputs that exercise numpy's code paths: special values, values around 1 (where `log`
/// switches branch), subnormals, and the whole exponent range.
fn probes(which: &str) -> Vec<f64> {
    let mut rng = Lcg(0x5eed);
    let mut v = vec![
        0.0,
        -0.0,
        1.0,
        f64::INFINITY,
        f64::NEG_INFINITY,
        f64::NAN,
        -1.0,
        5e-324,
        f64::MIN_POSITIVE,
        f64::MAX,
        0.5,
        std::f64::consts::E,
    ];
    for k in -40..40 {
        v.push(1.0 + k as f64 * f64::EPSILON * 1000.0);
    }
    for _ in 0..4000 {
        if which == "log" {
            v.push(rng.next() * 10f64.powf(1400.0 * rng.next() - 700.0));
        } else {
            v.push((rng.next() - 0.5) * 1500.0);
        }
    }
    for _ in 0..500 {
        v.push((rng.next() - 0.5) * 1e-8);
    }
    v
}

/// The loop must reproduce numpy bit for bit on every probe, on pieces of every length and
/// alignment, and give the same answer whole as in pieces.
fn self_test(np: &Bound<'_, PyModule>, name: &str, lp: &Loop) -> Option<()> {
    let func = np.getattr(name).ok()?;
    let input = probes(name);
    let arr = np.call_method1("array", (input.clone(),)).ok()?;
    let want: Vec<f64> = func
        .call1((arr,))
        .ok()?
        .call_method0("tolist")
        .ok()?
        .extract()
        .ok()?;
    let mut whole = input.clone();
    lp.apply(&mut whole);
    if !whole.iter().zip(&want).all(|(a, b)| same(*a, *b)) {
        return None;
    }
    // pieces of odd lengths starting at odd offsets
    let mut pieces = input.clone();
    let mut at = 0;
    for len in [1usize, 2, 7, 8, 9, 33, 1000, 3, 5, 4097].iter().cycle() {
        if at >= pieces.len() {
            break;
        }
        let end = (at + len).min(pieces.len());
        lp.apply(&mut pieces[at..end]);
        at = end;
    }
    if !pieces.iter().zip(&want).all(|(a, b)| same(*a, *b)) {
        return None;
    }
    // the scalar entry point is what the fit's scalar `exp` and `log` use
    for (x, w) in input.iter().zip(&want).take(200) {
        if !same(lp.scalar(*x), *w) {
            return None;
        }
    }
    Some(())
}

/// numpy's `log` and `exp` loops, if they can be used (see the module docs).
pub fn load(py: Python<'_>) -> Option<NumpyLoops> {
    let np = py.import("numpy").ok()?;
    let version: String = np.getattr("__version__").ok()?.extract().ok()?;
    // the layout read above is the one of numpy 2.x (and 1.x); anything newer calls numpy
    // through Python until it has been checked
    if version.split('.').next()?.parse::<u32>().ok()? != 2 {
        return None;
    }
    let log = find(py, &np, "log")?;
    let exp = find(py, &np, "exp")?;
    // `np.log(-1)` and friends raise floating-point flags that numpy reports as warnings
    let kw = pyo3::types::PyDict::new(py);
    kw.set_item("all", "ignore").ok()?;
    let old = np.call_method("seterr", (), Some(&kw)).ok()?;
    let ok = self_test(&np, "log", &log).is_some() && self_test(&np, "exp", &exp).is_some();
    if let Ok(old) = old.cast_into::<pyo3::types::PyDict>() {
        let _ = np.call_method("seterr", (), Some(&old));
    }
    ok.then_some(NumpyLoops { log, exp })
}

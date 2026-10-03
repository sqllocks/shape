//! Bounded, mergeable sketches (T-14): HyperLogLog (p=14 by default, Ertl's improved
//! estimator), KLL (k=200, deterministic compaction) and SpaceSaving (capacity 64).
//!
//! Each has a pure-Python twin in `src/shape/profile/sketches.py` that defines the exact
//! semantics; the differential tests require identical state (HLL registers, KLL levels,
//! SpaceSaving entries) for identical inputs.

use std::collections::HashMap;

use arrow_array::cast::AsArray;
use arrow_array::types::Float64Type;
use arrow_array::ArrayRef;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use pyo3_arrow::PyArray;

use crate::hashing;

// ---------------------------------------------------------------- HyperLogLog

pub struct HllCore {
    pub p: u32,
    pub registers: Vec<u8>,
}

fn sigma(mut x: f64) -> f64 {
    if x == 1.0 {
        return f64::INFINITY;
    }
    let (mut y, mut z) = (1.0_f64, x);
    loop {
        x *= x;
        let z_old = z;
        z += x * y;
        y += y;
        if z == z_old {
            return z;
        }
    }
}

fn tau(mut x: f64) -> f64 {
    if x == 0.0 || x == 1.0 {
        return 0.0;
    }
    let (mut y, mut z) = (1.0_f64, 1.0 - x);
    loop {
        x = x.sqrt();
        let z_old = z;
        y *= 0.5;
        z -= (1.0 - x) * (1.0 - x) * y;
        if z == z_old {
            return z / 3.0;
        }
    }
}

impl HllCore {
    /// Restored registers must be ranks `update_hash` can produce: at most `64 - p + 1`.
    pub fn check_registers(p: u32, registers: &[u8]) -> Result<(), String> {
        let max = 65 - p;
        if registers.iter().any(|&r| u32::from(r) > max) {
            return Err(format!(
                "an HLL register is above {max}, the largest rank for p={p}"
            ));
        }
        Ok(())
    }

    pub fn new(p: u32) -> Self {
        HllCore {
            p,
            registers: vec![0; 1usize << p],
        }
    }

    #[inline]
    pub fn update_hash(&mut self, h: u64) {
        let idx = (h >> (64 - self.p)) as usize;
        let rem = h << self.p;
        let rank = if rem == 0 {
            64 - self.p + 1
        } else {
            rem.leading_zeros() + 1
        } as u8;
        if rank > self.registers[idx] {
            self.registers[idx] = rank;
        }
    }

    pub fn merge(&mut self, o: &HllCore) {
        for (a, b) in self.registers.iter_mut().zip(&o.registers) {
            *a = (*a).max(*b);
        }
    }

    pub fn estimate(&self) -> f64 {
        let m = (1usize << self.p) as f64;
        let q = (64 - self.p) as usize;
        let mut counts = vec![0u64; q + 2];
        for &r in &self.registers {
            counts[r as usize] += 1;
        }
        let mut z = m * tau(1.0 - counts[q + 1] as f64 / m);
        for k in (1..=q).rev() {
            z = 0.5 * (z + counts[k] as f64);
        }
        z += m * sigma(counts[0] as f64 / m);
        (1.0 / (2.0 * std::f64::consts::LN_2)) * m * m / z
    }
}

// ---------------------------------------------------------------------- KLL

pub fn kll_capacity(k: u64, level: usize, levels: usize) -> usize {
    let depth = (levels - 1 - level) as u32;
    if depth > 40 {
        return 2;
    }
    let cap = ((k as u128) << depth) / 3u128.pow(depth);
    (cap as usize).max(2)
}

pub struct KllCore {
    pub k: u64,
    pub levels: Vec<Vec<f64>>,
    pub n: u64,
    compactions: u64,
}

impl KllCore {
    pub fn new(k: u64) -> Self {
        KllCore {
            k,
            levels: vec![Vec::new()],
            n: 0,
            compactions: 0,
        }
    }

    fn size(&self) -> usize {
        self.levels.iter().map(Vec::len).sum()
    }

    fn total_capacity(&self) -> usize {
        let h = self.levels.len();
        (0..h).map(|i| kll_capacity(self.k, i, h)).sum()
    }

    /// Number of compactions so far (the parity of the next one).
    pub fn compactions(&self) -> u64 {
        self.compactions
    }

    /// Restored state must be state `update` and `merge` can produce: `k >= 8`, at most 64
    /// levels (an item's weight is `2**level`), and no NaN item.
    pub fn check_parts(k: u64, levels: &[Vec<f64>]) -> Result<(), String> {
        if k < 8 {
            return Err(format!("KLL k is {k}; it must be >= 8"));
        }
        if levels.len() > 64 {
            return Err(format!("KLL has {} levels; at most 64", levels.len()));
        }
        if levels.iter().flatten().any(|x| x.is_nan()) {
            return Err("KLL holds a NaN item".into());
        }
        Ok(())
    }

    /// Rebuild a sketch from its serialized state (snapshot restore).
    pub fn from_parts(k: u64, levels: Vec<Vec<f64>>, n: u64, compactions: u64) -> Self {
        KllCore {
            k,
            levels: if levels.is_empty() {
                vec![Vec::new()]
            } else {
                levels
            },
            n,
            compactions,
        }
    }

    /// Add one value; NaN is skipped (T-13), as `update_values` skips it.
    pub fn update(&mut self, x: f64) {
        if x.is_nan() {
            return;
        }
        self.levels[0].push(x);
        self.n += 1;
        self.compress();
    }

    fn compress(&mut self) {
        while self.size() > self.total_capacity() {
            let h = self.levels.len();
            if let Some(i) = (0..h).find(|&i| self.levels[i].len() >= kll_capacity(self.k, i, h)) {
                self.compact(i);
            }
        }
    }

    fn compact(&mut self, level: usize) {
        if level + 1 == self.levels.len() {
            self.levels.push(Vec::new());
        }
        let mut vals = std::mem::take(&mut self.levels[level]);
        vals.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let leftover = if vals.len() % 2 == 1 {
            Some(vals.remove(0))
        } else {
            None
        };
        let parity = (self.compactions & 1) as usize;
        self.compactions += 1;
        self.levels[level] = leftover.into_iter().collect();
        self.levels[level + 1].extend(vals.into_iter().skip(parity).step_by(2));
    }

    pub fn merge(&mut self, o: &KllCore) {
        while self.levels.len() < o.levels.len() {
            self.levels.push(Vec::new());
        }
        for (i, v) in o.levels.iter().enumerate() {
            self.levels[i].extend_from_slice(v);
        }
        self.n += o.n;
        self.compress();
    }

    pub fn quantile(&self, q: f64) -> Option<f64> {
        let mut weighted: Vec<(f64, u64)> = Vec::new();
        for (level, vals) in self.levels.iter().enumerate() {
            weighted.extend(vals.iter().map(|&v| (v, 1u64 << level)));
        }
        if weighted.is_empty() {
            return None;
        }
        weighted.sort_by(|a, b| a.partial_cmp(b).unwrap());
        let total: u64 = weighted.iter().map(|w| w.1).sum();
        let target = q * (total as f64 - 1.0);
        let mut acc = 0u64;
        for &(v, w) in &weighted {
            if ((acc + w) as f64) > target {
                return Some(v);
            }
            acc += w;
        }
        weighted.last().map(|w| w.0)
    }
}

// -------------------------------------------------------------- SpaceSaving

/// One serialized SpaceSaving entry: (key, count, error, recency sequence).
pub type SsEntry<K> = (K, u64, u64, u64);
/// Serialized SpaceSaving state: (clock, n, entries).
pub type SsParts<K> = (u64, u64, Vec<SsEntry<K>>);

#[derive(Clone)]
struct Entry<K> {
    key: K,
    count: u64,
    err: u64,
    seq: u64,
}

/// SpaceSaving over any hashable, ordered key type (u64 hashes for the Python class; value
/// keys for the profile kernel).
pub struct SpaceSavingCore<K = u64> {
    pub capacity: usize,
    entries: Vec<Entry<K>>,
    index: HashMap<K, usize>,
    clock: u64,
    pub n: u64,
}

impl<K: Clone + Eq + std::hash::Hash + Ord> SpaceSavingCore<K> {
    pub fn new(capacity: usize) -> Self {
        SpaceSavingCore {
            capacity,
            entries: Vec::with_capacity(capacity.min(1 << 16)),
            index: HashMap::with_capacity(capacity.min(1 << 16) * 2),
            clock: 0,
            n: 0,
        }
    }

    pub fn len(&self) -> usize {
        self.entries.len()
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    pub fn update(&mut self, key: K, n: u64) {
        self.n += n;
        self.clock += 1;
        if let Some(&i) = self.index.get(&key) {
            self.entries[i].count += n;
            self.entries[i].seq = self.clock;
            return;
        }
        if self.entries.len() < self.capacity {
            self.index.insert(key.clone(), self.entries.len());
            self.entries.push(Entry {
                key,
                count: n,
                err: 0,
                seq: self.clock,
            });
            return;
        }
        let victim = (0..self.entries.len())
            .min_by_key(|&i| (self.entries[i].count, self.entries[i].seq))
            .expect("capacity > 0");
        let old = self.entries[victim].clone();
        self.index.remove(&old.key);
        self.index.insert(key.clone(), victim);
        self.entries[victim] = Entry {
            key,
            count: old.count + n,
            err: old.count,
            seq: self.clock,
        };
    }

    fn min_count(&self) -> u64 {
        if self.entries.len() >= self.capacity {
            self.entries.iter().map(|e| e.count).min().unwrap_or(0)
        } else {
            0
        }
    }

    pub fn merge(&mut self, o: &SpaceSavingCore<K>) {
        let (m1, m2) = (self.min_count(), o.min_count());
        let mut merged: HashMap<K, (u64, u64)> = HashMap::new();
        for e in &self.entries {
            let (c2, e2) = o
                .index
                .get(&e.key)
                .map_or((m2, m2), |&i| (o.entries[i].count, o.entries[i].err));
            merged.insert(e.key.clone(), (e.count + c2, e.err + e2));
        }
        for e in &o.entries {
            if !self.index.contains_key(&e.key) {
                merged.insert(e.key.clone(), (e.count + m1, e.err + m1));
            }
        }
        let mut keys: Vec<K> = merged.keys().cloned().collect();
        keys.sort_by(|a, b| merged[b].0.cmp(&merged[a].0).then(a.cmp(b)));
        keys.truncate(self.capacity);
        keys.sort();
        self.entries = keys
            .iter()
            .enumerate()
            .map(|(i, k)| Entry {
                key: k.clone(),
                count: merged[k].0,
                err: merged[k].1,
                seq: i as u64,
            })
            .collect();
        self.index = self
            .entries
            .iter()
            .enumerate()
            .map(|(i, e)| (e.key.clone(), i))
            .collect();
        self.clock = self.entries.len() as u64;
        self.n += o.n;
    }

    /// The serialized state: (clock, n, entries as (key, count, error, seq)), sorted by key so
    /// that equal states give equal bytes whatever the slot order.
    pub fn parts(&self) -> SsParts<K> {
        let mut v: Vec<SsEntry<K>> = self
            .entries
            .iter()
            .map(|e| (e.key.clone(), e.count, e.err, e.seq))
            .collect();
        v.sort_by(|a, b| a.0.cmp(&b.0));
        (self.clock, self.n, v)
    }

    /// Rebuild a summary from its serialized state (snapshot restore).
    pub fn from_parts(capacity: usize, clock: u64, n: u64, entries: Vec<SsEntry<K>>) -> Self {
        let entries: Vec<Entry<K>> = entries
            .into_iter()
            .map(|(key, count, err, seq)| Entry {
                key,
                count,
                err,
                seq,
            })
            .collect();
        let index = entries
            .iter()
            .enumerate()
            .map(|(i, e)| (e.key.clone(), i))
            .collect();
        SpaceSavingCore {
            capacity,
            entries,
            index,
            clock,
            n,
        }
    }

    /// (key, count, error), largest count first, ties by key.
    pub fn top(&self) -> Vec<(K, u64, u64)> {
        let mut v: Vec<(K, u64, u64)> = self
            .entries
            .iter()
            .map(|e| (e.key.clone(), e.count, e.err))
            .collect();
        v.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
        v
    }
}

// ------------------------------------------------------------ Python classes

fn canonical_hashes(arr: &ArrayRef) -> PyResult<arrow_array::UInt64Array> {
    hashing::hash_array(arr, 0).map_err(PyValueError::new_err)
}

#[pyclass(name = "Hll", module = "shape._kernel")]
pub struct PyHll {
    core: HllCore,
}

#[pymethods]
impl PyHll {
    #[new]
    #[pyo3(signature = (p = 14))]
    fn new(p: u32) -> PyResult<Self> {
        if !(4..=18).contains(&p) {
            return Err(PyValueError::new_err("p must be 4..18"));
        }
        Ok(PyHll {
            core: HllCore::new(p),
        })
    }

    #[getter]
    fn p(&self) -> u32 {
        self.core.p
    }

    fn update_hash(&mut self, h: u64) {
        self.core.update_hash(h);
    }

    /// Add every non-null, non-NaN element of an Arrow array (canonical hash, T-13).
    fn update_array(&mut self, py: Python<'_>, array: PyArray) -> PyResult<()> {
        let (arr, _) = array.into_inner();
        let hashes = py.detach(|| canonical_hashes(&arr))?;
        for h in hashes.iter().flatten() {
            self.core.update_hash(h);
        }
        Ok(())
    }

    /// Add raw 64-bit hashes (a uint64 Arrow array); nulls are skipped.
    fn update_hashes(&mut self, hashes: PyArray) -> PyResult<()> {
        let (arr, _) = hashes.into_inner();
        let a = arr
            .as_primitive_opt::<arrow_array::types::UInt64Type>()
            .ok_or_else(|| PyValueError::new_err("update_hashes needs a uint64 array"))?;
        for h in a.iter().flatten() {
            self.core.update_hash(h);
        }
        Ok(())
    }

    fn merge(&mut self, other: PyRef<'_, PyHll>) -> PyResult<()> {
        if self.core.p != other.core.p {
            return Err(PyValueError::new_err("incompatible HLL precision"));
        }
        self.core.merge(&other.core);
        Ok(())
    }

    fn estimate(&self) -> f64 {
        self.core.estimate()
    }

    fn registers<'py>(&self, py: Python<'py>) -> Bound<'py, PyBytes> {
        PyBytes::new(py, &self.core.registers)
    }

    #[staticmethod]
    fn from_registers(p: u32, registers: &[u8]) -> PyResult<Self> {
        let mut s = PyHll::new(p)?;
        if registers.len() != s.core.registers.len() {
            return Err(PyValueError::new_err("register count does not match p"));
        }
        HllCore::check_registers(p, registers).map_err(PyValueError::new_err)?;
        s.core.registers.copy_from_slice(registers);
        Ok(s)
    }
}

#[pyclass(name = "Kll", module = "shape._kernel")]
pub struct PyKll {
    core: KllCore,
}

#[pymethods]
impl PyKll {
    #[new]
    #[pyo3(signature = (k = 200))]
    fn new(k: u64) -> PyResult<Self> {
        if k < 8 {
            return Err(PyValueError::new_err("k must be >= 8"));
        }
        Ok(PyKll {
            core: KllCore::new(k),
        })
    }

    #[getter]
    fn k(&self) -> u64 {
        self.core.k
    }

    #[getter]
    fn n(&self) -> u64 {
        self.core.n
    }

    fn update(&mut self, x: f64) {
        self.core.update(x);
    }

    /// Add every non-null, non-NaN element of a float64 Arrow array.
    fn update_values(&mut self, values: PyArray) -> PyResult<()> {
        let (arr, _) = values.into_inner();
        let a = arr
            .as_primitive_opt::<Float64Type>()
            .ok_or_else(|| PyValueError::new_err("update_values needs a float64 array"))?;
        for x in a.iter().flatten() {
            if !x.is_nan() {
                self.core.update(x);
            }
        }
        Ok(())
    }

    fn merge(&mut self, other: PyRef<'_, PyKll>) -> PyResult<()> {
        if self.core.k != other.core.k {
            return Err(PyValueError::new_err("incompatible KLL k"));
        }
        self.core.merge(&other.core);
        Ok(())
    }

    fn quantile(&self, q: f64) -> PyResult<Option<f64>> {
        if !(0.0..=1.0).contains(&q) {
            return Err(PyValueError::new_err("q must be 0..1"));
        }
        Ok(self.core.quantile(q))
    }

    fn levels(&self) -> Vec<Vec<f64>> {
        self.core.levels.clone()
    }
}

#[pyclass(name = "SpaceSaving", module = "shape._kernel")]
pub struct PySpaceSaving {
    core: SpaceSavingCore<u64>,
}

#[pymethods]
impl PySpaceSaving {
    #[new]
    #[pyo3(signature = (capacity = 64))]
    fn new(capacity: usize) -> PyResult<Self> {
        if capacity == 0 {
            return Err(PyValueError::new_err("capacity must be >= 1"));
        }
        Ok(PySpaceSaving {
            core: SpaceSavingCore::<u64>::new(capacity),
        })
    }

    #[getter]
    fn capacity(&self) -> usize {
        self.core.capacity
    }

    #[getter]
    fn n(&self) -> u64 {
        self.core.n
    }

    fn __len__(&self) -> usize {
        self.core.len()
    }

    #[pyo3(signature = (key, n = 1))]
    fn update(&mut self, key: u64, n: u64) {
        self.core.update(key, n);
    }

    /// Count every non-null, non-NaN element of an Arrow array, keyed by canonical hash.
    fn update_array(&mut self, py: Python<'_>, array: PyArray) -> PyResult<()> {
        let (arr, _) = array.into_inner();
        let hashes = py.detach(|| canonical_hashes(&arr))?;
        for h in hashes.iter().flatten() {
            self.core.update(h, 1);
        }
        Ok(())
    }

    /// Count raw keys (a uint64 Arrow array); nulls are skipped.
    fn update_keys(&mut self, keys: PyArray) -> PyResult<()> {
        let (arr, _) = keys.into_inner();
        let a = arr
            .as_primitive_opt::<arrow_array::types::UInt64Type>()
            .ok_or_else(|| PyValueError::new_err("update_keys needs a uint64 array"))?;
        for k in a.iter().flatten() {
            self.core.update(k, 1);
        }
        Ok(())
    }

    fn merge(&mut self, other: PyRef<'_, PySpaceSaving>) -> PyResult<()> {
        if self.core.capacity != other.core.capacity {
            return Err(PyValueError::new_err("incompatible SpaceSaving capacity"));
        }
        self.core.merge(&other.core);
        Ok(())
    }

    /// (key, count, error) triples, largest count first.
    fn top(&self) -> Vec<(u64, u64, u64)> {
        self.core.top()
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyHll>()?;
    m.add_class::<PyKll>()?;
    m.add_class::<PySpaceSaving>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hll_estimates_within_bound() {
        let mut h = HllCore::new(14);
        for i in 0..200_000u64 {
            h.update_hash(hashing::hash_u64(i, 0));
        }
        let e = h.estimate();
        assert!((e - 200_000.0).abs() / 200_000.0 < 0.03, "{e}");
    }

    #[test]
    fn kll_preserves_weight_exactly() {
        let mut k = KllCore::new(200);
        for i in 0..100_003 {
            k.update((i % 977) as f64);
        }
        let w: u64 = k
            .levels
            .iter()
            .enumerate()
            .map(|(l, v)| (v.len() as u64) << l)
            .sum();
        assert_eq!(w, k.n);
    }

    #[test]
    fn space_saving_never_exceeds_capacity() {
        let mut s = SpaceSavingCore::<u64>::new(64);
        for i in 0..200_000u64 {
            s.update(hashing::hash_u64(i, 1), 1);
        }
        assert_eq!(s.len(), 64);
    }
}

//! Fused profile kernel (P1-06): one call per record batch updates every column.
//!
//! Two modes (T-15). **Exact** keeps exact distinct values with counts and every finite numeric
//! value (sort-based quantiles). **Bounded** replaces those by the T-14
//! sketches: HyperLogLog for distinct, SpaceSaving for top values, KLL for quantiles. Counts,
//! min/max, moments, text lengths, pattern classes and temporal histograms are exact in both.
//!
//! Columns are independent, so a batch is processed with rayon across columns; each column sees
//! its rows in order, which makes the bounded sketches deterministic. The Python reference
//! twin lives in `src/shape/kernel/reference/profile.py` and must produce the same output.

use std::collections::{BTreeMap, HashMap};

use arrow_array::cast::AsArray;
use arrow_array::types::*;
use arrow_array::{Array, RecordBatch};
use arrow_schema::{DataType, Schema, SchemaRef, TimeUnit};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};
use pyo3_arrow::{PyRecordBatch, PySchema};
use rayon::prelude::*;
use regex::RegexSet;

use crate::hashing;
use crate::sketch::{HllCore, KllCore, SpaceSavingCore};

pub const QUANTILES: [f64; 9] = [0.0, 0.01, 0.05, 0.25, 0.5, 0.75, 0.95, 0.99, 1.0];
pub const BOUNDED_TOP: usize = 64;
pub const PATTERN_NAMES: [&str; 12] = [
    "email", "uuid", "ssn", "mac", "ipv4", "ipv6", "iban", "postal", "date", "phone", "currency",
    "language",
];
const OCT: &str = r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)";

fn pattern_set() -> &'static RegexSet {
    static SET: std::sync::OnceLock<RegexSet> = std::sync::OnceLock::new();
    SET.get_or_init(|| {
        let ipv4 = format!(r"^{OCT}\.{OCT}\.{OCT}\.{OCT}$");
        let ipv6 = concat!(
            r"^(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$",
            r"|^(?:[0-9a-fA-F]{1,4}:){1,7}:$",
            r"|^(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}$",
            r"|^::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}$",
            r"|^::$"
        );
        // The twin and the baseline use Python `re`: there `$` also matches before a final
        // "\n", and `\s` also covers \x1c-\x1f. Spell both out so the counts agree.
        let python_re = |p: &str| p.replace('$', r"\n?\z").replace(r"\s", r"\s\x1c-\x1f");
        RegexSet::new(
            [
                r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$",
                r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$",
                r"^\d{3}-\d{2}-\d{4}$",
                r"^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$|^([0-9a-fA-F]{2}-){5}[0-9a-fA-F]{2}$",
                ipv4.as_str(),
                ipv6,
                r"^[A-Z]{2}\d{2}[A-Z0-9]{1,30}$",
                r"^\d{5}(-\d{4})?$",
                r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$",
                r"^[\+]?[\d\s\-\(\)\.]{7,20}$",
                r"^[A-Z]{3}$",
                r"^[a-z]{2}(-[A-Z]{2})?$",
            ]
            .map(python_re),
        )
        .expect("valid patterns")
    })
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Mode {
    Exact,
    Bounded,
}

/// A hashable, ordered value key (variants order: I < F < T < S).
#[derive(Clone, PartialEq, Eq, Hash, PartialOrd, Ord, Debug)]
pub enum Key {
    I(i128),
    F(u64),
    T(i64),
    S(String),
}

impl Key {
    fn hash(&self) -> u64 {
        match self {
            Key::I(v) => match i64::try_from(*v) {
                Ok(i) => hashing::hash_i64(i, 0),
                Err(_) => hashing::hash_u64(*v as u64, 0),
            },
            Key::F(bits) => hashing::hash_f64(f64::from_bits(*bits), 0).unwrap_or(0),
            Key::T(us) => hashing::hash_tagged(hashing::TAG_TS, &us.to_le_bytes(), 0),
            Key::S(s) => hashing::hash_bytes(hashing::TAG_STR, s.as_bytes(), 0),
        }
    }

    fn to_py(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        Ok(match self {
            Key::I(v) => v.into_pyobject(py)?.into_any().unbind(),
            Key::F(b) => f64::from_bits(*b).into_pyobject(py)?.into_any().unbind(),
            Key::T(v) => v.into_pyobject(py)?.into_any().unbind(),
            Key::S(s) => s.into_pyobject(py)?.into_any().unbind(),
        })
    }
}

fn float_key(x: f64) -> Key {
    Key::F(if x == 0.0 { 0.0f64 } else { x }.to_bits())
}

// ------------------------------------------------------------ value tracker

enum Tracker {
    Exact(HashMap<Key, (u64, u64)>),
    Bounded {
        hll: HllCore,
        ss: SpaceSavingCore<Key>,
    },
}

impl Tracker {
    fn new(mode: Mode) -> Self {
        match mode {
            Mode::Exact => Tracker::Exact(HashMap::new()),
            Mode::Bounded => Tracker::Bounded {
                hll: HllCore::new(14),
                ss: SpaceSavingCore::new(BOUNDED_TOP),
            },
        }
    }

    #[inline]
    fn add(&mut self, key: Key, ordinal: u64) {
        match self {
            Tracker::Exact(m) => {
                m.entry(key)
                    .and_modify(|e| e.0 += 1)
                    .or_insert((1, ordinal));
            }
            Tracker::Bounded { hll, ss } => {
                hll.update_hash(key.hash());
                ss.update(key, 1);
            }
        }
    }

    fn merge(&mut self, o: &Tracker, row_offset: u64) {
        match (self, o) {
            (Tracker::Exact(a), Tracker::Exact(b)) => {
                for (k, (c, first)) in b {
                    a.entry(k.clone())
                        .and_modify(|e| {
                            e.0 += c;
                            e.1 = e.1.min(first + row_offset);
                        })
                        .or_insert((*c, first + row_offset));
                }
            }
            (Tracker::Bounded { hll, ss }, Tracker::Bounded { hll: h2, ss: s2 }) => {
                hll.merge(h2);
                ss.merge(s2);
            }
            _ => unreachable!("modes are checked before merging"),
        }
    }

    /// (distinct, exact?)
    fn distinct(&self) -> (f64, bool) {
        match self {
            Tracker::Exact(m) => (m.len() as f64, true),
            Tracker::Bounded { hll, .. } => (hll.estimate(), false),
        }
    }

    /// (key, count, error, first-seen ordinal or None), largest count first.
    fn top(&self, n: usize) -> Vec<(Key, u64, u64, Option<u64>)> {
        match self {
            Tracker::Exact(m) => {
                let mut v: Vec<_> = m
                    .iter()
                    .map(|(k, (c, f))| (k.clone(), *c, 0, Some(*f)))
                    .collect();
                v.sort_by(|a, b| b.1.cmp(&a.1).then(a.3.cmp(&b.3)));
                v.truncate(n);
                v
            }
            Tracker::Bounded { ss, .. } => {
                let mut v: Vec<_> = ss
                    .top()
                    .into_iter()
                    .map(|(k, c, e)| (k, c, e, None))
                    .collect();
                v.truncate(n);
                v
            }
        }
    }
}

// ---------------------------------------------------------------- accumulators

struct NumAcc {
    is_int: bool,
    nan: u64,
    pos_inf: u64,
    neg_inf: u64,
    finite: u64,
    mean: f64,
    m2: f64,
    min: Option<f64>,
    max: Option<f64>,
    min_i: Option<i128>,
    max_i: Option<i128>,
    values: Vec<f64>,
    kll: Option<KllCore>,
    tracker: Tracker,
}

impl NumAcc {
    fn new(is_int: bool, mode: Mode) -> Self {
        NumAcc {
            is_int,
            nan: 0,
            pos_inf: 0,
            neg_inf: 0,
            finite: 0,
            mean: 0.0,
            m2: 0.0,
            min: None,
            max: None,
            min_i: None,
            max_i: None,
            values: Vec::new(),
            kll: (mode == Mode::Bounded).then(|| KllCore::new(200)),
            tracker: Tracker::new(mode),
        }
    }

    #[inline]
    fn add_float(&mut self, x: f64, ordinal: u64) {
        if x.is_nan() {
            self.nan += 1;
            return;
        }
        self.tracker.add(float_key(x), ordinal);
        if x.is_infinite() {
            if x > 0.0 {
                self.pos_inf += 1;
            } else {
                self.neg_inf += 1;
            }
            return;
        }
        self.add_finite(x);
    }

    #[inline]
    fn add_int(&mut self, v: i128, ordinal: u64) {
        self.tracker.add(Key::I(v), ordinal);
        self.min_i = Some(self.min_i.map_or(v, |m| m.min(v)));
        self.max_i = Some(self.max_i.map_or(v, |m| m.max(v)));
        self.add_finite(v as f64);
    }

    #[inline]
    fn add_finite(&mut self, x: f64) {
        self.finite += 1;
        let d = x - self.mean;
        self.mean += d / self.finite as f64;
        self.m2 += d * (x - self.mean);
        self.min = Some(self.min.map_or(x, |m| m.min(x)));
        self.max = Some(self.max.map_or(x, |m| m.max(x)));
        match &mut self.kll {
            Some(k) => k.update(x),
            None => self.values.push(x),
        }
    }

    fn merge(&mut self, o: &NumAcc, row_offset: u64) {
        self.nan += o.nan;
        self.pos_inf += o.pos_inf;
        self.neg_inf += o.neg_inf;
        if o.finite > 0 {
            let n = (self.finite + o.finite) as f64;
            let d = o.mean - self.mean;
            self.m2 += o.m2 + d * d * (self.finite as f64) * (o.finite as f64) / n;
            self.mean += d * (o.finite as f64) / n;
            self.finite += o.finite;
        }
        self.min = match (self.min, o.min) {
            (Some(a), Some(b)) => Some(a.min(b)),
            (a, b) => a.or(b),
        };
        self.max = match (self.max, o.max) {
            (Some(a), Some(b)) => Some(a.max(b)),
            (a, b) => a.or(b),
        };
        self.min_i = match (self.min_i, o.min_i) {
            (Some(a), Some(b)) => Some(a.min(b)),
            (a, b) => a.or(b),
        };
        self.max_i = match (self.max_i, o.max_i) {
            (Some(a), Some(b)) => Some(a.max(b)),
            (a, b) => a.or(b),
        };
        self.values.extend_from_slice(&o.values);
        if let (Some(a), Some(b)) = (&mut self.kll, &o.kll) {
            a.merge(b);
        }
        self.tracker.merge(&o.tracker, row_offset);
    }
}

#[derive(Default)]
struct TextAcc {
    min: Option<String>,
    max: Option<String>,
    lengths: BTreeMap<u64, u64>,
    patterns: [u64; 12],
    tracker: Option<Tracker>,
}

struct TemporalAcc {
    min: Option<i64>,
    max: Option<i64>,
    hour: [u64; 24],
    dow: [u64; 7],
    month: [u64; 12],
    year: BTreeMap<i64, u64>,
    tracker: Tracker,
}

enum Acc {
    Num(NumAcc),
    Bool { t: u64, f: u64 },
    Text(TextAcc),
    Temporal(TemporalAcc),
    Other,
}

struct Column {
    name: String,
    dtype: DataType,
    count: u64,
    nulls: u64,
    acc: Acc,
}

fn kind_of(dt: &DataType, mode: Mode) -> Acc {
    use DataType::*;
    match dt {
        Int8 | Int16 | Int32 | Int64 | UInt8 | UInt16 | UInt32 | UInt64 => {
            Acc::Num(NumAcc::new(true, mode))
        }
        Float16 | Float32 | Float64 | Decimal128(_, _) => Acc::Num(NumAcc::new(false, mode)),
        Boolean => Acc::Bool { t: 0, f: 0 },
        Utf8 | LargeUtf8 | Utf8View => Acc::Text(TextAcc {
            tracker: Some(Tracker::new(mode)),
            ..Default::default()
        }),
        Date32 | Date64 | Timestamp(_, _) => Acc::Temporal(TemporalAcc {
            min: None,
            max: None,
            hour: [0; 24],
            dow: [0; 7],
            month: [0; 12],
            year: BTreeMap::new(),
            tracker: Tracker::new(mode),
        }),
        _ => Acc::Other,
    }
}

fn kind_name(acc: &Acc) -> &'static str {
    match acc {
        Acc::Num(n) if n.is_int => "int",
        Acc::Num(_) => "float",
        Acc::Bool { .. } => "bool",
        Acc::Text(_) => "text",
        Acc::Temporal(_) => "temporal",
        Acc::Other => "other",
    }
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

const US_PER_DAY: i64 = 86_400_000_000;

impl TemporalAcc {
    #[inline]
    fn add(&mut self, us: i64, ordinal: u64) {
        self.tracker.add(Key::T(us), ordinal);
        self.min = Some(self.min.map_or(us, |m| m.min(us)));
        self.max = Some(self.max.map_or(us, |m| m.max(us)));
        let days = us.div_euclid(US_PER_DAY);
        let rem = us.rem_euclid(US_PER_DAY);
        self.hour[(rem / 3_600_000_000) as usize] += 1;
        self.dow[(days + 3).rem_euclid(7) as usize] += 1;
        let (y, m) = civil(days);
        self.month[m - 1] += 1;
        *self.year.entry(y).or_insert(0) += 1;
    }
}

fn unit_us(v: i64, unit: &TimeUnit) -> i64 {
    match unit {
        TimeUnit::Second => v.wrapping_mul(1_000_000),
        TimeUnit::Millisecond => v.wrapping_mul(1_000),
        TimeUnit::Microsecond => v,
        TimeUnit::Nanosecond => v.div_euclid(1_000),
    }
}

impl Column {
    fn new(name: &str, dtype: &DataType, mode: Mode) -> Self {
        Column {
            name: name.to_string(),
            dtype: dtype.clone(),
            count: 0,
            nulls: 0,
            acc: kind_of(dtype, mode),
        }
    }

    fn update(&mut self, arr: &dyn Array, row0: u64) {
        use DataType::*;
        let n = arr.len();
        self.count += n as u64;
        self.nulls += arr.logical_null_count() as u64;
        let valid = |i: usize| !arr.is_null(i);
        let dt = self.dtype.clone();
        match &mut self.acc {
            Acc::Other => {}
            Acc::Bool { t, f } => {
                let a = arr.as_boolean();
                for i in (0..n).filter(|&i| valid(i)) {
                    if a.value(i) {
                        *t += 1;
                    } else {
                        *f += 1;
                    }
                }
            }
            Acc::Num(acc) => {
                macro_rules! ints {
                    ($ty:ty) => {{
                        let a = arr.as_primitive::<$ty>();
                        for i in (0..n).filter(|&i| valid(i)) {
                            acc.add_int(a.value(i) as i128, row0 + i as u64);
                        }
                    }};
                }
                macro_rules! floats {
                    ($ty:ty, $conv:expr) => {{
                        let a = arr.as_primitive::<$ty>();
                        for i in (0..n).filter(|&i| valid(i)) {
                            acc.add_float($conv(a.value(i)), row0 + i as u64);
                        }
                    }};
                }
                match dt {
                    Int8 => ints!(Int8Type),
                    Int16 => ints!(Int16Type),
                    Int32 => ints!(Int32Type),
                    Int64 => ints!(Int64Type),
                    UInt8 => ints!(UInt8Type),
                    UInt16 => ints!(UInt16Type),
                    UInt32 => ints!(UInt32Type),
                    UInt64 => ints!(UInt64Type),
                    Float16 => floats!(Float16Type, |v: half::f16| v.to_f64()),
                    Float32 => floats!(Float32Type, |v: f32| v as f64),
                    Float64 => floats!(Float64Type, |v: f64| v),
                    Decimal128(_, scale) => {
                        let div = 10f64.powi(scale as i32);
                        floats!(Decimal128Type, |v: i128| v as f64 / div)
                    }
                    _ => unreachable!(),
                }
            }
            Acc::Text(acc) => {
                let set = pattern_set();
                let mut each = |i: usize, s: &str| {
                    let len = if s.is_ascii() {
                        s.len()
                    } else {
                        s.chars().count()
                    } as u64;
                    *acc.lengths.entry(len).or_insert(0) += 1;
                    if acc.min.as_deref().is_none_or(|m| s < m) {
                        acc.min = Some(s.to_string());
                    }
                    if acc.max.as_deref().is_none_or(|m| s > m) {
                        acc.max = Some(s.to_string());
                    }
                    if s.len() <= 200 {
                        for m in set.matches(s).iter() {
                            acc.patterns[m] += 1;
                        }
                    }
                    if let Some(t) = &mut acc.tracker {
                        t.add(Key::S(s.to_string()), row0 + i as u64);
                    }
                };
                match dt {
                    Utf8 => {
                        let a = arr.as_string::<i32>();
                        for i in (0..n).filter(|&i| valid(i)) {
                            each(i, a.value(i));
                        }
                    }
                    LargeUtf8 => {
                        let a = arr.as_string::<i64>();
                        for i in (0..n).filter(|&i| valid(i)) {
                            each(i, a.value(i));
                        }
                    }
                    _ => {
                        let a = arr.as_string_view();
                        for i in (0..n).filter(|&i| valid(i)) {
                            each(i, a.value(i));
                        }
                    }
                }
            }
            Acc::Temporal(acc) => match dt {
                Date32 => {
                    let a = arr.as_primitive::<Date32Type>();
                    for i in (0..n).filter(|&i| valid(i)) {
                        acc.add(
                            (a.value(i) as i64).wrapping_mul(US_PER_DAY),
                            row0 + i as u64,
                        );
                    }
                }
                Date64 => {
                    let a = arr.as_primitive::<Date64Type>();
                    for i in (0..n).filter(|&i| valid(i)) {
                        acc.add(a.value(i).wrapping_mul(1000), row0 + i as u64);
                    }
                }
                Timestamp(unit, _) => {
                    macro_rules! ts {
                        ($ty:ty) => {{
                            let a = arr.as_primitive::<$ty>();
                            for i in (0..n).filter(|&i| valid(i)) {
                                acc.add(unit_us(a.value(i), &unit), row0 + i as u64);
                            }
                        }};
                    }
                    match unit {
                        TimeUnit::Second => ts!(TimestampSecondType),
                        TimeUnit::Millisecond => ts!(TimestampMillisecondType),
                        TimeUnit::Microsecond => ts!(TimestampMicrosecondType),
                        TimeUnit::Nanosecond => ts!(TimestampNanosecondType),
                    }
                }
                _ => unreachable!(),
            },
        }
    }

    fn merge(&mut self, o: &Column, row_offset: u64) {
        self.count += o.count;
        self.nulls += o.nulls;
        match (&mut self.acc, &o.acc) {
            (Acc::Num(a), Acc::Num(b)) => a.merge(b, row_offset),
            (Acc::Bool { t, f }, Acc::Bool { t: t2, f: f2 }) => {
                *t += t2;
                *f += f2;
            }
            (Acc::Text(a), Acc::Text(b)) => {
                for (k, v) in &b.lengths {
                    *a.lengths.entry(*k).or_insert(0) += v;
                }
                for i in 0..12 {
                    a.patterns[i] += b.patterns[i];
                }
                if let Some(m) = &b.min {
                    if a.min.as_ref().is_none_or(|x| m < x) {
                        a.min = Some(m.clone());
                    }
                }
                if let Some(m) = &b.max {
                    if a.max.as_ref().is_none_or(|x| m > x) {
                        a.max = Some(m.clone());
                    }
                }
                if let (Some(x), Some(y)) = (&mut a.tracker, &b.tracker) {
                    x.merge(y, row_offset);
                }
            }
            (Acc::Temporal(a), Acc::Temporal(b)) => {
                a.min = match (a.min, b.min) {
                    (Some(x), Some(y)) => Some(x.min(y)),
                    (x, y) => x.or(y),
                };
                a.max = match (a.max, b.max) {
                    (Some(x), Some(y)) => Some(x.max(y)),
                    (x, y) => x.or(y),
                };
                for i in 0..24 {
                    a.hour[i] += b.hour[i];
                }
                for i in 0..7 {
                    a.dow[i] += b.dow[i];
                }
                for i in 0..12 {
                    a.month[i] += b.month[i];
                }
                for (y, c) in &b.year {
                    *a.year.entry(*y).or_insert(0) += c;
                }
                a.tracker.merge(&b.tracker, row_offset);
            }
            _ => {}
        }
    }
}

// ------------------------------------------------------------------- table

pub struct TableProfile {
    pub mode: Mode,
    pub schema: SchemaRef,
    columns: Vec<Column>,
    pub rows: u64,
}

/// numpy's `linear` percentile on sorted data (including its `_lerp`).
pub fn linear_quantile(sorted: &[f64], q: f64) -> f64 {
    let n = sorted.len();
    if n == 1 {
        return sorted[0];
    }
    let pos = q * (n as f64 - 1.0);
    let lo = pos.floor() as usize;
    let hi = (lo + 1).min(n - 1);
    let t = pos - lo as f64;
    let (a, b) = (sorted[lo], sorted[hi]);
    let diff = b - a;
    if t >= 0.5 {
        b - diff * (1.0 - t)
    } else {
        a + diff * t
    }
}

/// numpy's `linear` percentile over a histogram of integer values.
fn hist_quantile(counts: &BTreeMap<u64, u64>, total: u64, q: f64) -> f64 {
    let pos = q * (total as f64 - 1.0);
    let lo = pos.floor() as u64;
    let hi = (lo + 1).min(total - 1);
    let t = pos - lo as f64;
    let at = |rank: u64| -> f64 {
        let mut acc = 0u64;
        for (v, c) in counts {
            acc += c;
            if rank < acc {
                return *v as f64;
            }
        }
        0.0
    };
    let (a, b) = (at(lo), at(hi));
    let diff = b - a;
    if t >= 0.5 {
        b - diff * (1.0 - t)
    } else {
        a + diff * t
    }
}

impl TableProfile {
    pub fn new(schema: &Schema, mode: Mode) -> Self {
        let columns = schema
            .fields()
            .iter()
            .map(|f| Column::new(f.name(), f.data_type(), mode))
            .collect();
        TableProfile {
            mode,
            schema: std::sync::Arc::new(schema.clone()),
            columns,
            rows: 0,
        }
    }

    pub fn update(&mut self, batch: &RecordBatch) -> Result<(), String> {
        if batch.schema().fields().len() != self.columns.len()
            || batch
                .schema()
                .fields()
                .iter()
                .zip(self.schema.fields())
                .any(|(a, b)| a.data_type() != b.data_type())
        {
            return Err("batch schema differs from the profile schema".into());
        }
        let row0 = self.rows;
        if crate::can_par() {
            self.columns
                .par_iter_mut()
                .zip(batch.columns().par_iter())
                .for_each(|(col, arr)| col.update(arr.as_ref(), row0));
        } else {
            for (col, arr) in self.columns.iter_mut().zip(batch.columns()) {
                col.update(arr.as_ref(), row0);
            }
        }
        self.rows += batch.num_rows() as u64;
        Ok(())
    }

    pub fn merge(&mut self, o: &TableProfile) -> Result<(), String> {
        if self.mode != o.mode || self.schema.fields() != o.schema.fields() {
            return Err("cannot merge profiles with different schemas or modes".into());
        }
        let off = self.rows;
        for (a, b) in self.columns.iter_mut().zip(&o.columns) {
            a.merge(b, off);
        }
        self.rows += o.rows;
        Ok(())
    }

    pub fn finalize<'py>(&self, py: Python<'py>, top_n: usize) -> PyResult<Bound<'py, PyDict>> {
        let out = PyDict::new(py);
        out.set_item("rows", self.rows)?;
        out.set_item(
            "mode",
            if self.mode == Mode::Exact {
                "exact"
            } else {
                "bounded"
            },
        )?;
        let cols = PyList::empty(py);
        for c in &self.columns {
            cols.append(finalize_column(py, c, top_n)?)?;
        }
        out.set_item("columns", cols)?;
        Ok(out)
    }
}

fn top_list<'py>(py: Python<'py>, t: &Tracker, n: usize) -> PyResult<Bound<'py, PyList>> {
    let l = PyList::empty(py);
    for (k, c, e, f) in t.top(n) {
        l.append((k.to_py(py)?, c, e, f))?;
    }
    Ok(l)
}

fn finalize_column<'py>(py: Python<'py>, c: &Column, top_n: usize) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("name", &c.name)?;
    d.set_item("kind", kind_name(&c.acc))?;
    d.set_item("count", c.count)?;
    d.set_item("null_count", c.nulls)?;
    match &c.acc {
        Acc::Other => {}
        Acc::Bool { t, f } => {
            d.set_item("true_count", t)?;
            d.set_item("false_count", f)?;
        }
        Acc::Num(n) => {
            d.set_item("nan_count", n.nan)?;
            d.set_item("pos_inf_count", n.pos_inf)?;
            d.set_item("neg_inf_count", n.neg_inf)?;
            d.set_item("finite_count", n.finite)?;
            if n.is_int {
                d.set_item("min", n.min_i.map(|v| v.into_pyobject(py).unwrap()))?;
                d.set_item("max", n.max_i.map(|v| v.into_pyobject(py).unwrap()))?;
            } else {
                d.set_item("min", n.min)?;
                d.set_item("max", n.max)?;
            }
            d.set_item("mean", if n.finite > 0 { Some(n.mean) } else { None })?;
            d.set_item("m2", if n.finite > 0 { Some(n.m2) } else { None })?;
            let (dist, exact) = n.tracker.distinct();
            d.set_item("distinct", dist)?;
            d.set_item("distinct_exact", exact)?;
            d.set_item(
                "top",
                top_list(
                    py,
                    &n.tracker,
                    top_n.min(if exact { usize::MAX } else { BOUNDED_TOP }),
                )?,
            )?;
            let q = PyDict::new(py);
            if n.finite > 0 {
                match &n.kll {
                    Some(k) => {
                        for p in QUANTILES {
                            q.set_item(p, k.quantile(p))?;
                        }
                    }
                    None => {
                        let mut v = n.values.clone();
                        v.par_sort_unstable_by(|a, b| a.partial_cmp(b).unwrap());
                        for p in QUANTILES {
                            q.set_item(p, linear_quantile(&v, p))?;
                        }
                    }
                }
            }
            d.set_item("quantiles", q)?;
        }
        Acc::Text(t) => {
            let nn = c.count - c.nulls;
            d.set_item("min", &t.min)?;
            d.set_item("max", &t.max)?;
            let len = PyDict::new(py);
            len.set_item("count", nn)?;
            if nn > 0 {
                let min = *t.lengths.keys().next().unwrap();
                let max = *t.lengths.keys().next_back().unwrap();
                let sum: u128 = t.lengths.iter().map(|(l, c)| *l as u128 * *c as u128).sum();
                len.set_item("min", min)?;
                len.set_item("max", max)?;
                len.set_item("mean", sum as f64 / nn as f64)?;
                len.set_item("p95", hist_quantile(&t.lengths, nn, 0.95))?;
                let hist = PyDict::new(py);
                for (l, cnt) in &t.lengths {
                    hist.set_item(l, cnt)?;
                }
                len.set_item("hist", hist)?;
            }
            d.set_item("length", len)?;
            let pats = PyDict::new(py);
            for (i, name) in PATTERN_NAMES.iter().enumerate() {
                pats.set_item(name, t.patterns[i])?;
            }
            d.set_item("patterns", pats)?;
            if let Some(tr) = &t.tracker {
                let (dist, exact) = tr.distinct();
                d.set_item("distinct", dist)?;
                d.set_item("distinct_exact", exact)?;
                d.set_item(
                    "top",
                    top_list(
                        py,
                        tr,
                        top_n.min(if exact { usize::MAX } else { BOUNDED_TOP }),
                    )?,
                )?;
            }
        }
        Acc::Temporal(t) => {
            d.set_item("min", t.min)?;
            d.set_item("max", t.max)?;
            d.set_item("hour_hist", t.hour.to_vec())?;
            d.set_item("dow_hist", t.dow.to_vec())?;
            d.set_item("month_hist", t.month.to_vec())?;
            let y = PyDict::new(py);
            for (year, cnt) in &t.year {
                y.set_item(year, cnt)?;
            }
            d.set_item("year_hist", y)?;
            let (dist, exact) = t.tracker.distinct();
            d.set_item("distinct", dist)?;
            d.set_item("distinct_exact", exact)?;
            d.set_item(
                "top",
                top_list(
                    py,
                    &t.tracker,
                    top_n.min(if exact { usize::MAX } else { BOUNDED_TOP }),
                )?,
            )?;
        }
    }
    Ok(d)
}

// ---------------------------------------------------------------- snapshot

// Serialized bounded-mode state (stream runtime, P3-01): a restored state continues exactly as
// the original would have. Little-endian; sets and maps are written sorted so equal states give
// equal bytes. The Python twin (`kernel/reference/profile.py`) writes and reads the same bytes.
//
//   "SHPS" u8 version(1) u8 mode(1 = bounded) u64 rows u32 ncols, then per column
//   u8 kind (0 other, 1 int, 2 float, 3 bool, 4 text, 5 temporal) u64 count u64 nulls + body

const SNAP_MAGIC: &[u8; 4] = b"SHPS";
const SNAP_VERSION: u8 = 1;

struct SnapWriter(Vec<u8>);

impl SnapWriter {
    fn u8(&mut self, v: u8) {
        self.0.push(v);
    }
    fn u32(&mut self, v: u32) {
        self.0.extend_from_slice(&v.to_le_bytes());
    }
    fn u64(&mut self, v: u64) {
        self.0.extend_from_slice(&v.to_le_bytes());
    }
    fn i64(&mut self, v: i64) {
        self.0.extend_from_slice(&v.to_le_bytes());
    }
    fn i128(&mut self, v: i128) {
        self.0.extend_from_slice(&v.to_le_bytes());
    }
    fn f64(&mut self, v: f64) {
        self.0.extend_from_slice(&v.to_bits().to_le_bytes());
    }
    fn str(&mut self, v: &str) {
        self.u32(v.len() as u32);
        self.0.extend_from_slice(v.as_bytes());
    }
    fn opt_f64(&mut self, v: Option<f64>) {
        self.u8(v.is_some() as u8);
        self.f64(v.unwrap_or(0.0));
    }
    fn opt_i64(&mut self, v: Option<i64>) {
        self.u8(v.is_some() as u8);
        self.i64(v.unwrap_or(0));
    }
    fn opt_i128(&mut self, v: Option<i128>) {
        self.u8(v.is_some() as u8);
        self.i128(v.unwrap_or(0));
    }
    fn opt_str(&mut self, v: &Option<String>) {
        self.u8(v.is_some() as u8);
        if let Some(s) = v {
            self.str(s);
        }
    }
}

struct SnapReader<'a> {
    b: &'a [u8],
    i: usize,
}

impl<'a> SnapReader<'a> {
    fn take(&mut self, n: usize) -> Result<&'a [u8], String> {
        let end = self
            .i
            .checked_add(n)
            .filter(|&e| e <= self.b.len())
            .ok_or("snapshot is truncated")?;
        let out = &self.b[self.i..end];
        self.i = end;
        Ok(out)
    }
    fn arr<const N: usize>(&mut self) -> Result<[u8; N], String> {
        Ok(self.take(N)?.try_into().expect("length checked"))
    }
    fn u8(&mut self) -> Result<u8, String> {
        Ok(self.arr::<1>()?[0])
    }
    fn u32(&mut self) -> Result<u32, String> {
        Ok(u32::from_le_bytes(self.arr()?))
    }
    fn u64(&mut self) -> Result<u64, String> {
        Ok(u64::from_le_bytes(self.arr()?))
    }
    fn i64(&mut self) -> Result<i64, String> {
        Ok(i64::from_le_bytes(self.arr()?))
    }
    fn i128(&mut self) -> Result<i128, String> {
        Ok(i128::from_le_bytes(self.arr()?))
    }
    fn f64(&mut self) -> Result<f64, String> {
        Ok(f64::from_bits(u64::from_le_bytes(self.arr()?)))
    }
    fn str(&mut self) -> Result<String, String> {
        let n = self.u32()? as usize;
        String::from_utf8(self.take(n)?.to_vec()).map_err(|_| "snapshot holds invalid UTF-8".into())
    }
    fn opt_f64(&mut self) -> Result<Option<f64>, String> {
        let (f, v) = (self.u8()?, self.f64()?);
        Ok((f != 0).then_some(v))
    }
    fn opt_i64(&mut self) -> Result<Option<i64>, String> {
        let (f, v) = (self.u8()?, self.i64()?);
        Ok((f != 0).then_some(v))
    }
    fn opt_i128(&mut self) -> Result<Option<i128>, String> {
        let (f, v) = (self.u8()?, self.i128()?);
        Ok((f != 0).then_some(v))
    }
    fn opt_str(&mut self) -> Result<Option<String>, String> {
        if self.u8()? != 0 {
            Ok(Some(self.str()?))
        } else {
            Ok(None)
        }
    }
}

fn write_key(w: &mut SnapWriter, k: &Key) {
    match k {
        Key::I(v) => {
            w.u8(0);
            w.i128(*v);
        }
        Key::F(b) => {
            w.u8(1);
            w.u64(*b);
        }
        Key::T(v) => {
            w.u8(2);
            w.i64(*v);
        }
        Key::S(s) => {
            w.u8(3);
            w.str(s);
        }
    }
}

fn read_key(r: &mut SnapReader) -> Result<Key, String> {
    Ok(match r.u8()? {
        0 => Key::I(r.i128()?),
        1 => Key::F(r.u64()?),
        2 => Key::T(r.i64()?),
        3 => Key::S(r.str()?),
        t => return Err(format!("snapshot holds an unknown key tag {t}")),
    })
}

fn write_tracker(w: &mut SnapWriter, t: &Tracker) -> Result<(), String> {
    match t {
        Tracker::Exact(_) => Err("snapshots support bounded mode only".into()),
        Tracker::Bounded { hll, ss } => {
            w.u8(hll.p as u8);
            w.0.extend_from_slice(&hll.registers);
            let (clock, n, entries) = ss.parts();
            w.u32(ss.capacity as u32);
            w.u64(n);
            w.u64(clock);
            w.u32(entries.len() as u32);
            for (k, c, e, seq) in &entries {
                write_key(w, k);
                w.u64(*c);
                w.u64(*e);
                w.u64(*seq);
            }
            Ok(())
        }
    }
}

fn read_tracker(r: &mut SnapReader) -> Result<Tracker, String> {
    let p = r.u8()? as u32;
    if !(4..=18).contains(&p) {
        return Err("snapshot holds an invalid HLL precision".into());
    }
    let mut hll = HllCore::new(p);
    let registers = r.take(1usize << p)?;
    HllCore::check_registers(p, registers).map_err(|e| format!("snapshot: {e}"))?;
    hll.registers.copy_from_slice(registers);
    let capacity = r.u32()? as usize;
    if capacity == 0 {
        return Err("snapshot holds a SpaceSaving capacity of 0".into());
    }
    let n = r.u64()?;
    let clock = r.u64()?;
    let len = r.u32()? as usize;
    if len > capacity {
        return Err("snapshot holds more SpaceSaving entries than its capacity".into());
    }
    let mut entries = Vec::with_capacity(len);
    for _ in 0..len {
        let k = read_key(r)?;
        entries.push((k, r.u64()?, r.u64()?, r.u64()?));
    }
    Ok(Tracker::Bounded {
        hll,
        ss: SpaceSavingCore::from_parts(capacity, clock, n, entries),
    })
}

fn write_kll(w: &mut SnapWriter, k: &KllCore) {
    w.u64(k.k);
    w.u64(k.n);
    w.u64(k.compactions());
    w.u32(k.levels.len() as u32);
    for level in &k.levels {
        w.u32(level.len() as u32);
        for x in level {
            w.f64(*x);
        }
    }
}

fn read_kll(r: &mut SnapReader) -> Result<KllCore, String> {
    let (k, n, compactions) = (r.u64()?, r.u64()?, r.u64()?);
    let nlevels = r.u32()? as usize;
    let mut levels = Vec::with_capacity(nlevels.min(64));
    for _ in 0..nlevels {
        let len = r.u32()? as usize;
        let mut level = Vec::with_capacity(len.min(1 << 16));
        for _ in 0..len {
            level.push(r.f64()?);
        }
        levels.push(level);
    }
    KllCore::check_parts(k, &levels).map_err(|e| format!("snapshot: {e}"))?;
    Ok(KllCore::from_parts(k, levels, n, compactions))
}

fn kind_code(acc: &Acc) -> u8 {
    match acc {
        Acc::Other => 0,
        Acc::Num(n) if n.is_int => 1,
        Acc::Num(_) => 2,
        Acc::Bool { .. } => 3,
        Acc::Text(_) => 4,
        Acc::Temporal(_) => 5,
    }
}

impl Column {
    fn write_snapshot(&self, w: &mut SnapWriter) -> Result<(), String> {
        w.u8(kind_code(&self.acc));
        w.u64(self.count);
        w.u64(self.nulls);
        match &self.acc {
            Acc::Other => {}
            Acc::Bool { t, f } => {
                w.u64(*t);
                w.u64(*f);
            }
            Acc::Num(a) => {
                for v in [a.nan, a.pos_inf, a.neg_inf, a.finite] {
                    w.u64(v);
                }
                w.f64(a.mean);
                w.f64(a.m2);
                w.opt_f64(a.min);
                w.opt_f64(a.max);
                w.opt_i128(a.min_i);
                w.opt_i128(a.max_i);
                write_kll(
                    w,
                    a.kll
                        .as_ref()
                        .ok_or("snapshots support bounded mode only")?,
                );
                write_tracker(w, &a.tracker)?;
            }
            Acc::Text(a) => {
                w.opt_str(&a.min);
                w.opt_str(&a.max);
                w.u32(a.lengths.len() as u32);
                for (len, c) in &a.lengths {
                    w.u64(*len);
                    w.u64(*c);
                }
                for c in a.patterns {
                    w.u64(c);
                }
                write_tracker(
                    w,
                    a.tracker.as_ref().ok_or("text column without a tracker")?,
                )?;
            }
            Acc::Temporal(a) => {
                w.opt_i64(a.min);
                w.opt_i64(a.max);
                for c in a.hour.iter().chain(&a.dow).chain(&a.month) {
                    w.u64(*c);
                }
                w.u32(a.year.len() as u32);
                for (y, c) in &a.year {
                    w.i64(*y);
                    w.u64(*c);
                }
                write_tracker(w, &a.tracker)?;
            }
        }
        Ok(())
    }

    fn read_snapshot(&mut self, r: &mut SnapReader) -> Result<(), String> {
        if r.u8()? != kind_code(&self.acc) {
            return Err(format!(
                "snapshot column kind differs from the schema for {:?}",
                self.name
            ));
        }
        self.count = r.u64()?;
        self.nulls = r.u64()?;
        match &mut self.acc {
            Acc::Other => {}
            Acc::Bool { t, f } => {
                *t = r.u64()?;
                *f = r.u64()?;
            }
            Acc::Num(a) => {
                a.nan = r.u64()?;
                a.pos_inf = r.u64()?;
                a.neg_inf = r.u64()?;
                a.finite = r.u64()?;
                a.mean = r.f64()?;
                a.m2 = r.f64()?;
                a.min = r.opt_f64()?;
                a.max = r.opt_f64()?;
                a.min_i = r.opt_i128()?;
                a.max_i = r.opt_i128()?;
                a.kll = Some(read_kll(r)?);
                a.tracker = read_tracker(r)?;
            }
            Acc::Text(a) => {
                a.min = r.opt_str()?;
                a.max = r.opt_str()?;
                a.lengths.clear();
                for _ in 0..r.u32()? {
                    let len = r.u64()?;
                    a.lengths.insert(len, r.u64()?);
                }
                for c in a.patterns.iter_mut() {
                    *c = r.u64()?;
                }
                a.tracker = Some(read_tracker(r)?);
            }
            Acc::Temporal(a) => {
                a.min = r.opt_i64()?;
                a.max = r.opt_i64()?;
                for c in a
                    .hour
                    .iter_mut()
                    .chain(a.dow.iter_mut())
                    .chain(a.month.iter_mut())
                {
                    *c = r.u64()?;
                }
                a.year.clear();
                for _ in 0..r.u32()? {
                    let y = r.i64()?;
                    a.year.insert(y, r.u64()?);
                }
                a.tracker = read_tracker(r)?;
            }
        }
        Ok(())
    }
}

impl TableProfile {
    /// Serialize the bounded-mode state (see the format above).
    pub fn snapshot(&self) -> Result<Vec<u8>, String> {
        if self.mode != Mode::Bounded {
            return Err("snapshots support bounded mode only".into());
        }
        let mut w = SnapWriter(Vec::new());
        w.0.extend_from_slice(SNAP_MAGIC);
        w.u8(SNAP_VERSION);
        w.u8(1);
        w.u64(self.rows);
        w.u32(self.columns.len() as u32);
        for c in &self.columns {
            c.write_snapshot(&mut w)?;
        }
        Ok(w.0)
    }

    /// Rebuild a bounded-mode state from `snapshot()` bytes and the schema it was taken with.
    pub fn from_snapshot(schema: &Schema, data: &[u8]) -> Result<Self, String> {
        let mut r = SnapReader { b: data, i: 0 };
        if r.take(4)? != SNAP_MAGIC {
            return Err("not a profile snapshot".into());
        }
        if r.u8()? != SNAP_VERSION {
            return Err("unsupported profile snapshot version".into());
        }
        if r.u8()? != 1 {
            return Err("snapshots support bounded mode only".into());
        }
        let mut t = TableProfile::new(schema, Mode::Bounded);
        t.rows = r.u64()?;
        if r.u32()? as usize != t.columns.len() {
            return Err("snapshot column count differs from the schema".into());
        }
        for c in t.columns.iter_mut() {
            c.read_snapshot(&mut r)?;
        }
        if r.i != data.len() {
            return Err("snapshot has trailing bytes".into());
        }
        Ok(t)
    }
}

// ----------------------------------------------------------------- Python

fn parse_mode(mode: &str) -> PyResult<Mode> {
    match mode {
        "exact" => Ok(Mode::Exact),
        "bounded" => Ok(Mode::Bounded),
        other => Err(PyValueError::new_err(format!(
            "mode must be 'exact' or 'bounded', got {other:?}"
        ))),
    }
}

#[pyclass(name = "ProfileState", module = "shape._kernel")]
pub struct PyProfileState {
    core: TableProfile,
}

#[pymethods]
impl PyProfileState {
    #[new]
    #[pyo3(signature = (schema, mode = "exact"))]
    fn new(schema: PySchema, mode: &str) -> PyResult<Self> {
        let mode = parse_mode(mode)?;
        Ok(PyProfileState {
            core: TableProfile::new(schema.as_ref(), mode),
        })
    }

    #[getter]
    fn rows(&self) -> u64 {
        self.core.rows
    }

    #[getter]
    fn mode(&self) -> &'static str {
        if self.core.mode == Mode::Exact {
            "exact"
        } else {
            "bounded"
        }
    }

    /// Update every column from one record batch (one call per batch).
    fn update(&mut self, py: Python<'_>, batch: PyRecordBatch) -> PyResult<()> {
        let batch = batch.into_inner();
        py.detach(|| self.core.update(&batch))
            .map_err(PyValueError::new_err)
    }

    fn merge(&mut self, other: PyRef<'_, PyProfileState>) -> PyResult<()> {
        self.core.merge(&other.core).map_err(PyValueError::new_err)
    }

    #[pyo3(signature = (top_n = 500))]
    fn finalize<'py>(&self, py: Python<'py>, top_n: usize) -> PyResult<Bound<'py, PyDict>> {
        self.core.finalize(py, top_n)
    }

    /// The bounded-mode state as bytes; `from_snapshot` restores it exactly.
    fn snapshot<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyBytes>> {
        let bytes = self.core.snapshot().map_err(PyValueError::new_err)?;
        Ok(PyBytes::new(py, &bytes))
    }

    #[staticmethod]
    fn from_snapshot(schema: PySchema, data: &[u8]) -> PyResult<Self> {
        let core =
            TableProfile::from_snapshot(schema.as_ref(), data).map_err(PyValueError::new_err)?;
        Ok(PyProfileState { core })
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyProfileState>()?;
    Ok(())
}

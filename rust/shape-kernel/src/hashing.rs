//! Canonical hashing (T-13): seeded XXH3-64 over a canonical byte form of each value.
//!
//! * integers and integral floats hash equal (`1` == `1.0`); `-0.0` is `0`;
//! * NaN and null are excluded: their output slot is null;
//! * strings hash as UTF-8 bytes (`string`, `large_string` and `string_view` agree);
//! * timestamps hash as int64 microseconds (dates and ns/ms/s timestamps are normalized);
//! * the Python twin in `src/shape/kernel/reference/hashing.py` must agree bit for bit.
//!
//! Canonical bytes = one tag byte followed by the payload:
//! `01` int64 LE, `02` uint64 LE (only above i64::MAX), `03` float64 bits LE (non-integral or
//! |x| >= 2^64, incl. +-inf), `04` UTF-8, `05` binary, `06` bool (1 byte), `07` timestamp
//! (int64 us), `08` duration (int64 us), `09` time of day (int64 us), `0A` decimal
//! (i8 scale + i128 LE, trailing zeros stripped), `0B` integer beyond 64 bits (i128 LE).

use arrow_array::cast::AsArray;
use arrow_array::types::*;
use arrow_array::{Array, ArrayRef, PrimitiveArray, UInt64Array};
use arrow_buffer::{BooleanBuffer, NullBuffer};
use arrow_schema::{DataType, TimeUnit};
use rayon::prelude::*;
use xxhash_rust::xxh3::xxh3_64_with_seed;

pub const TAG_INT: u8 = 0x01;
pub const TAG_UINT: u8 = 0x02;
pub const TAG_FLOAT: u8 = 0x03;
pub const TAG_STR: u8 = 0x04;
pub const TAG_BIN: u8 = 0x05;
pub const TAG_BOOL: u8 = 0x06;
pub const TAG_TS: u8 = 0x07;
pub const TAG_DUR: u8 = 0x08;
pub const TAG_TIME: u8 = 0x09;
pub const TAG_DEC: u8 = 0x0A;
pub const TAG_BIGINT: u8 = 0x0B;

const PAR_MIN: usize = 1 << 15;
const CHUNK: usize = 1 << 13;

#[inline]
pub fn hash_tagged(tag: u8, payload: &[u8], seed: u64) -> u64 {
    let mut buf = [0u8; 18];
    buf[0] = tag;
    buf[1..=payload.len()].copy_from_slice(payload);
    xxh3_64_with_seed(&buf[..=payload.len()], seed)
}

#[inline]
pub fn hash_i64(v: i64, seed: u64) -> u64 {
    hash_tagged(TAG_INT, &v.to_le_bytes(), seed)
}

#[inline]
pub fn hash_u64(v: u64, seed: u64) -> u64 {
    if v <= i64::MAX as u64 {
        hash_i64(v as i64, seed)
    } else {
        hash_tagged(TAG_UINT, &v.to_le_bytes(), seed)
    }
}

/// `None` for NaN.
#[inline]
pub fn hash_f64(x: f64, seed: u64) -> Option<u64> {
    if x.is_nan() {
        return None;
    }
    if x.is_finite() && x == x.trunc() {
        if (-9_223_372_036_854_775_808.0..9_223_372_036_854_775_808.0).contains(&x) {
            return Some(hash_i64(x as i64, seed));
        }
        if (9_223_372_036_854_775_808.0..18_446_744_073_709_551_616.0).contains(&x) {
            return Some(hash_tagged(TAG_UINT, &(x as u64).to_le_bytes(), seed));
        }
    }
    Some(hash_tagged(TAG_FLOAT, &x.to_bits().to_le_bytes(), seed))
}

pub fn hash_decimal(v: i128, scale: i8, seed: u64) -> u64 {
    let (mut v, mut scale) = (v, scale);
    if scale < 0 {
        let mut ok = true;
        let mut cur = v;
        for _ in 0..(-(scale as i32)) {
            match cur.checked_mul(10) {
                Some(n) => cur = n,
                None => {
                    ok = false;
                    break;
                }
            }
        }
        if ok {
            v = cur;
            scale = 0;
        }
    } else {
        while scale > 0 && v % 10 == 0 {
            v /= 10;
            scale -= 1;
        }
    }
    if scale == 0 {
        if let Ok(i) = i64::try_from(v) {
            return hash_i64(i, seed);
        }
        if let Ok(u) = u64::try_from(v) {
            return hash_tagged(TAG_UINT, &u.to_le_bytes(), seed);
        }
        return hash_tagged(TAG_BIGINT, &v.to_le_bytes(), seed);
    }
    let mut p = [0u8; 17];
    p[0] = scale as u8;
    p[1..].copy_from_slice(&v.to_le_bytes());
    hash_tagged(TAG_DEC, &p, seed)
}

#[inline]
pub fn hash_bytes(tag: u8, b: &[u8], seed: u64) -> u64 {
    let mut buf = Vec::with_capacity(b.len() + 1);
    buf.push(tag);
    buf.extend_from_slice(b);
    xxh3_64_with_seed(&buf, seed)
}

fn unit_to_us(v: i64, unit: &TimeUnit) -> i64 {
    match unit {
        TimeUnit::Second => v.wrapping_mul(1_000_000),
        TimeUnit::Millisecond => v.wrapping_mul(1_000),
        TimeUnit::Microsecond => v,
        TimeUnit::Nanosecond => v.div_euclid(1_000),
    }
}

/// Hash every slot of `f(i)`; `None` results and nulls become null in the output.
fn run<F>(len: usize, nulls: Option<&NullBuffer>, f: F) -> UInt64Array
where
    F: Fn(usize) -> Option<u64> + Sync,
{
    let one = |i: usize| -> (u64, bool) {
        if nulls.is_some_and(|n| n.is_null(i)) {
            return (0, false);
        }
        match f(i) {
            Some(h) => (h, true),
            None => (0, false),
        }
    };
    let pairs: Vec<(u64, bool)> = if len >= PAR_MIN && crate::can_par() {
        (0..len.div_ceil(CHUNK))
            .into_par_iter()
            .flat_map_iter(|c| (c * CHUNK..((c + 1) * CHUNK).min(len)).map(one))
            .collect()
    } else {
        (0..len).map(one).collect()
    };
    let values: Vec<u64> = pairs.iter().map(|p| p.0).collect();
    let valid: Vec<bool> = pairs.iter().map(|p| p.1).collect();
    let nb = NullBuffer::new(BooleanBuffer::from(valid));
    UInt64Array::new(
        values.into(),
        if nb.null_count() == 0 { None } else { Some(nb) },
    )
}

fn prim<T, F>(a: &PrimitiveArray<T>, f: F) -> UInt64Array
where
    T: ArrowPrimitiveType,
    F: Fn(T::Native) -> Option<u64> + Sync,
{
    run(a.len(), a.nulls(), |i| f(a.value(i)))
}

fn time_like<T: ArrowPrimitiveType<Native = i64>>(
    a: &PrimitiveArray<T>,
    unit: &TimeUnit,
    tag: u8,
    seed: u64,
) -> UInt64Array {
    prim(a, |v| {
        Some(hash_tagged(tag, &unit_to_us(v, unit).to_le_bytes(), seed))
    })
}

/// Canonical hash of every element; null and NaN slots are null in the result.
pub fn hash_array(array: &ArrayRef, seed: u64) -> Result<UInt64Array, String> {
    use DataType::*;
    Ok(match array.data_type() {
        Int8 => prim(array.as_primitive::<Int8Type>(), |v| {
            Some(hash_i64(v as i64, seed))
        }),
        Int16 => prim(array.as_primitive::<Int16Type>(), |v| {
            Some(hash_i64(v as i64, seed))
        }),
        Int32 => prim(array.as_primitive::<Int32Type>(), |v| {
            Some(hash_i64(v as i64, seed))
        }),
        Int64 => prim(array.as_primitive::<Int64Type>(), |v| {
            Some(hash_i64(v, seed))
        }),
        UInt8 => prim(array.as_primitive::<UInt8Type>(), |v| {
            Some(hash_u64(v as u64, seed))
        }),
        UInt16 => prim(array.as_primitive::<UInt16Type>(), |v| {
            Some(hash_u64(v as u64, seed))
        }),
        UInt32 => prim(array.as_primitive::<UInt32Type>(), |v| {
            Some(hash_u64(v as u64, seed))
        }),
        UInt64 => prim(array.as_primitive::<UInt64Type>(), |v| {
            Some(hash_u64(v, seed))
        }),
        Float16 => prim(array.as_primitive::<Float16Type>(), |v| {
            hash_f64(v.to_f64(), seed)
        }),
        Float32 => prim(array.as_primitive::<Float32Type>(), |v| {
            hash_f64(v as f64, seed)
        }),
        Float64 => prim(array.as_primitive::<Float64Type>(), |v| hash_f64(v, seed)),
        Null => UInt64Array::new_null(array.len()),
        Boolean => {
            let a = array.as_boolean();
            run(a.len(), a.nulls(), |i| {
                Some(hash_tagged(TAG_BOOL, &[a.value(i) as u8], seed))
            })
        }
        Utf8 => {
            let a = array.as_string::<i32>();
            run(a.len(), a.nulls(), |i| {
                Some(hash_bytes(TAG_STR, a.value(i).as_bytes(), seed))
            })
        }
        LargeUtf8 => {
            let a = array.as_string::<i64>();
            run(a.len(), a.nulls(), |i| {
                Some(hash_bytes(TAG_STR, a.value(i).as_bytes(), seed))
            })
        }
        Utf8View => {
            let a = array.as_string_view();
            run(a.len(), a.nulls(), |i| {
                Some(hash_bytes(TAG_STR, a.value(i).as_bytes(), seed))
            })
        }
        Binary => {
            let a = array.as_binary::<i32>();
            run(a.len(), a.nulls(), |i| {
                Some(hash_bytes(TAG_BIN, a.value(i), seed))
            })
        }
        LargeBinary => {
            let a = array.as_binary::<i64>();
            run(a.len(), a.nulls(), |i| {
                Some(hash_bytes(TAG_BIN, a.value(i), seed))
            })
        }
        BinaryView => {
            let a = array.as_binary_view();
            run(a.len(), a.nulls(), |i| {
                Some(hash_bytes(TAG_BIN, a.value(i), seed))
            })
        }
        Date32 => prim(array.as_primitive::<Date32Type>(), |v| {
            Some(hash_tagged(
                TAG_TS,
                &(v as i64).wrapping_mul(86_400_000_000).to_le_bytes(),
                seed,
            ))
        }),
        Date64 => prim(array.as_primitive::<Date64Type>(), |v| {
            Some(hash_tagged(
                TAG_TS,
                &v.wrapping_mul(1_000).to_le_bytes(),
                seed,
            ))
        }),
        Time32(TimeUnit::Second) => prim(array.as_primitive::<Time32SecondType>(), |v| {
            Some(hash_tagged(
                TAG_TIME,
                &(v as i64 * 1_000_000).to_le_bytes(),
                seed,
            ))
        }),
        Time32(_) => prim(array.as_primitive::<Time32MillisecondType>(), |v| {
            Some(hash_tagged(
                TAG_TIME,
                &(v as i64 * 1_000).to_le_bytes(),
                seed,
            ))
        }),
        Time64(TimeUnit::Microsecond) => prim(array.as_primitive::<Time64MicrosecondType>(), |v| {
            Some(hash_tagged(TAG_TIME, &v.to_le_bytes(), seed))
        }),
        Time64(_) => prim(array.as_primitive::<Time64NanosecondType>(), |v| {
            Some(hash_tagged(
                TAG_TIME,
                &v.div_euclid(1_000).to_le_bytes(),
                seed,
            ))
        }),
        Timestamp(TimeUnit::Second, _) => time_like(
            array.as_primitive::<TimestampSecondType>(),
            &TimeUnit::Second,
            TAG_TS,
            seed,
        ),
        Timestamp(TimeUnit::Millisecond, _) => time_like(
            array.as_primitive::<TimestampMillisecondType>(),
            &TimeUnit::Millisecond,
            TAG_TS,
            seed,
        ),
        Timestamp(TimeUnit::Microsecond, _) => time_like(
            array.as_primitive::<TimestampMicrosecondType>(),
            &TimeUnit::Microsecond,
            TAG_TS,
            seed,
        ),
        Timestamp(TimeUnit::Nanosecond, _) => time_like(
            array.as_primitive::<TimestampNanosecondType>(),
            &TimeUnit::Nanosecond,
            TAG_TS,
            seed,
        ),
        Duration(TimeUnit::Second) => time_like(
            array.as_primitive::<DurationSecondType>(),
            &TimeUnit::Second,
            TAG_DUR,
            seed,
        ),
        Duration(TimeUnit::Millisecond) => time_like(
            array.as_primitive::<DurationMillisecondType>(),
            &TimeUnit::Millisecond,
            TAG_DUR,
            seed,
        ),
        Duration(TimeUnit::Microsecond) => time_like(
            array.as_primitive::<DurationMicrosecondType>(),
            &TimeUnit::Microsecond,
            TAG_DUR,
            seed,
        ),
        Duration(TimeUnit::Nanosecond) => time_like(
            array.as_primitive::<DurationNanosecondType>(),
            &TimeUnit::Nanosecond,
            TAG_DUR,
            seed,
        ),
        Decimal128(_, scale) => {
            let scale = *scale;
            prim(array.as_primitive::<Decimal128Type>(), move |v| {
                Some(hash_decimal(v, scale, seed))
            })
        }
        other => return Err(format!("hash_array: unsupported Arrow type {other}")),
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow_array::{Decimal128Array, Float64Array, Int64Array, StringArray};
    use std::sync::Arc;

    #[test]
    fn integers_and_integral_floats_agree() {
        assert_eq!(hash_f64(1.0, 7), Some(hash_i64(1, 7)));
        assert_eq!(hash_f64(-0.0, 7), Some(hash_i64(0, 7)));
        assert_eq!(
            hash_f64(9_223_372_036_854_775_808.0, 7),
            Some(hash_u64(1 << 63, 7))
        );
        assert_ne!(hash_f64(1.5, 7), Some(hash_i64(1, 7)));
    }

    #[test]
    fn nan_is_excluded_and_infinities_are_not() {
        assert_eq!(hash_f64(f64::NAN, 0), None);
        assert!(hash_f64(f64::INFINITY, 0).is_some());
        assert_ne!(hash_f64(f64::INFINITY, 0), hash_f64(f64::NEG_INFINITY, 0));
    }

    #[test]
    fn hash_is_xxh3_of_tag_and_payload() {
        let mut bytes = vec![TAG_INT];
        bytes.extend_from_slice(&5i64.to_le_bytes());
        assert_eq!(hash_i64(5, 42), xxh3_64_with_seed(&bytes, 42));
    }

    #[test]
    fn decimals_hash_like_integers_when_integral() {
        assert_eq!(hash_decimal(1000, 3, 1), hash_i64(1, 1));
        assert_eq!(hash_decimal(-5, 0, 1), hash_i64(-5, 1));
        // the widest payload (scale byte + i128) must fit the buffer
        assert_ne!(
            hash_decimal(i128::MAX, 3, 1),
            hash_decimal(i128::MAX - 1, 3, 1)
        );
    }

    #[test]
    fn arrays_null_and_nan_slots_are_null() {
        let a: ArrayRef = Arc::new(Float64Array::from(vec![Some(1.0), Some(f64::NAN), None]));
        let h = hash_array(&a, 0).unwrap();
        assert!(h.is_valid(0) && h.is_null(1) && h.is_null(2));
        let s: ArrayRef = Arc::new(StringArray::from(vec![Some("a"), None]));
        let h = hash_array(&s, 0).unwrap();
        assert!(h.is_valid(0) && h.is_null(1));
        let i: ArrayRef = Arc::new(Int64Array::from(vec![1, 2, 3]));
        assert_eq!(hash_array(&i, 3).unwrap().value(0), hash_i64(1, 3));
        let d: ArrayRef = Arc::new(
            Decimal128Array::from(vec![12345_i128])
                .with_precision_and_scale(10, 2)
                .unwrap(),
        );
        assert!(hash_array(&d, 0).unwrap().is_valid(0));
    }

    #[test]
    fn parallel_and_serial_paths_agree() {
        let n = PAR_MIN + 12_345;
        let big: ArrayRef = Arc::new(Int64Array::from_iter_values(0..n as i64));
        let h = hash_array(&big, 9).unwrap();
        for i in [0usize, 1, CHUNK - 1, CHUNK, n - 1] {
            assert_eq!(h.value(i), hash_i64(i as i64, 9));
        }
    }
}

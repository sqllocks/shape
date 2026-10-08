//! Pool and string assembly: gather from a pool, templates, case and joins, UUIDs and random
//! characters. Every function here produces an Arrow `string` (utf8) array and is deterministic;
//! the Python twin is `shape.kernel.reference.gen`.

use arrow_array::cast::AsArray;
use arrow_array::types::Int64Type;
use arrow_array::{Array, ArrayRef, GenericStringArray, StringArray};
use arrow_buffer::{Buffer, NullBuffer, OffsetBuffer, ScalarBuffer};
use arrow_schema::DataType;
use rayon::prelude::*;

use super::rng::{below, for_row_chunks};

/// Rows per parallel task when assembling strings.
const TASK_ROWS: usize = 16_384;
const PAR_MIN_ROWS: usize = 32_768;

/// Build a `string` array of `n` rows. `f(i, buf)` appends row `i`'s bytes (valid UTF-8) to `buf`
/// and returns whether the row is valid (a null row appends nothing).
pub fn build_utf8(
    n: usize,
    f: &(impl Fn(usize, &mut Vec<u8>) -> bool + Sync),
) -> Result<StringArray, String> {
    let n_tasks = n.div_ceil(TASK_ROWS);
    let run = |t: usize| {
        let (lo, hi) = (t * TASK_ROWS, ((t + 1) * TASK_ROWS).min(n));
        let mut bytes: Vec<u8> = Vec::with_capacity((hi - lo) * 16);
        let mut ends: Vec<usize> = Vec::with_capacity(hi - lo);
        let mut valid: Vec<bool> = Vec::with_capacity(hi - lo);
        for i in lo..hi {
            valid.push(f(i, &mut bytes));
            ends.push(bytes.len());
        }
        (bytes, ends, valid)
    };
    let parts: Vec<_> = if n >= PAR_MIN_ROWS && crate::can_par() {
        (0..n_tasks).into_par_iter().map(run).collect()
    } else {
        (0..n_tasks).map(run).collect()
    };
    let total: usize = parts.iter().map(|p| p.0.len()).sum();
    if i32::try_from(total).is_err() {
        return Err("string output exceeds 2 GiB: use smaller chunks".into());
    }
    let mut values: Vec<u8> = Vec::with_capacity(total);
    let mut offsets: Vec<i32> = Vec::with_capacity(n + 1);
    let mut validity: Vec<bool> = Vec::with_capacity(n);
    offsets.push(0);
    let mut base = 0usize;
    for (bytes, ends, valid) in parts {
        offsets.extend(ends.iter().map(|e| (base + e) as i32));
        base += bytes.len();
        values.extend_from_slice(&bytes);
        validity.extend(valid);
    }
    let nulls = if validity.iter().all(|v| *v) {
        None
    } else {
        Some(NullBuffer::from(validity))
    };
    StringArray::try_new(
        OffsetBuffer::new(ScalarBuffer::from(offsets)),
        Buffer::from_vec(values),
        nulls,
    )
    .map_err(|e| e.to_string())
}

/// A string-or-integer input column of the assembly functions.
pub enum Col<'a> {
    S32(&'a GenericStringArray<i32>),
    S64(&'a GenericStringArray<i64>),
    Int(&'a arrow_array::Int64Array),
}

impl<'a> Col<'a> {
    pub fn from_array(a: &'a ArrayRef) -> Result<Col<'a>, String> {
        match a.data_type() {
            DataType::Utf8 => Ok(Col::S32(a.as_string::<i32>())),
            DataType::LargeUtf8 => Ok(Col::S64(a.as_string::<i64>())),
            DataType::Int64 => Ok(Col::Int(a.as_primitive::<Int64Type>())),
            t => Err(format!("expected string, large_string or int64, got {t}")),
        }
    }

    pub fn len(&self) -> usize {
        match self {
            Col::S32(a) => a.len(),
            Col::S64(a) => a.len(),
            Col::Int(a) => a.len(),
        }
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    #[inline]
    pub fn is_null(&self, i: usize) -> bool {
        match self {
            Col::S32(a) => a.is_null(i),
            Col::S64(a) => a.is_null(i),
            Col::Int(a) => a.is_null(i),
        }
    }

    /// Append row `i` (not null): strings as they are, integers in decimal, zero-padded to
    /// `width` (the sign counts towards the width, as in `format(v, "0{width}d")`).
    #[inline]
    pub fn write(&self, i: usize, width: usize, buf: &mut Vec<u8>) {
        match self {
            Col::S32(a) => buf.extend_from_slice(a.value(i).as_bytes()),
            Col::S64(a) => buf.extend_from_slice(a.value(i).as_bytes()),
            Col::Int(a) => {
                use std::io::Write;
                let _ = write!(buf, "{:0width$}", a.value(i));
            }
        }
    }
}

fn check_lengths(cols: &[Col<'_>]) -> Result<usize, String> {
    let n = cols.first().map_or(0, Col::len);
    if cols.iter().any(|c| c.len() != n) {
        return Err("all columns must have the same length".into());
    }
    Ok(n)
}

/// `literals[0] + col[slots[0]] + literals[1] + ...`: `slots` pairs a column index with a
/// zero-pad width; `literals` has one more element than `slots`. A null in a used column makes the
/// row null.
pub fn template(
    literals: &[String],
    slots: &[(usize, usize)],
    cols: &[Col<'_>],
    n_rows: usize,
) -> Result<StringArray, String> {
    if literals.len() != slots.len() + 1 {
        return Err("template needs one more literal than slots".into());
    }
    if slots.iter().any(|(c, _)| *c >= cols.len()) {
        return Err("template slot refers to a missing column".into());
    }
    if !cols.is_empty() && check_lengths(cols)? != n_rows {
        return Err("n_rows must equal the column length".into());
    }
    build_utf8(n_rows, &|i, buf| {
        if slots.iter().any(|(c, _)| cols[*c].is_null(i)) {
            return false;
        }
        buf.extend_from_slice(literals[0].as_bytes());
        for (k, (c, w)) in slots.iter().enumerate() {
            cols[*c].write(i, *w, buf);
            buf.extend_from_slice(literals[k + 1].as_bytes());
        }
        true
    })
}

/// Join the columns' values with `sep`. With `skip_nulls` a null column is left out; otherwise a
/// null makes the row null. When every column is null and `skip_nulls` is set the row is "".
pub fn join(cols: &[Col<'_>], sep: &str, skip_nulls: bool) -> Result<StringArray, String> {
    let n = check_lengths(cols)?;
    build_utf8(n, &|i, buf| {
        let mut first = true;
        for c in cols {
            if c.is_null(i) {
                if skip_nulls {
                    continue;
                }
                return false;
            }
            if !first {
                buf.extend_from_slice(sep.as_bytes());
            }
            first = false;
            c.write(i, 0, buf);
        }
        true
    })
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum CaseMode {
    Upper,
    Lower,
    Title,
}

impl CaseMode {
    pub fn parse(s: &str) -> Result<CaseMode, String> {
        match s {
            "upper" => Ok(CaseMode::Upper),
            "lower" => Ok(CaseMode::Lower),
            "title" => Ok(CaseMode::Title),
            _ => Err(format!(
                "case mode must be upper, lower or title, got {s:?}"
            )),
        }
    }
}

fn convert_case(s: &str, mode: CaseMode, buf: &mut Vec<u8>) {
    if s.is_ascii() {
        let mut prev_alnum = false;
        for &b in s.as_bytes() {
            let alnum = b.is_ascii_alphanumeric();
            buf.push(match mode {
                CaseMode::Upper => b.to_ascii_uppercase(),
                CaseMode::Lower => b.to_ascii_lowercase(),
                CaseMode::Title if alnum && !prev_alnum => b.to_ascii_uppercase(),
                CaseMode::Title => b.to_ascii_lowercase(),
            });
            prev_alnum = alnum;
        }
        return;
    }
    match mode {
        CaseMode::Upper => buf.extend_from_slice(s.to_uppercase().as_bytes()),
        CaseMode::Lower => buf.extend_from_slice(s.to_lowercase().as_bytes()),
        CaseMode::Title => {
            let mut out = String::with_capacity(s.len());
            let mut prev_alnum = false;
            for c in s.chars() {
                let alnum = c.is_alphanumeric();
                if alnum && !prev_alnum {
                    out.extend(c.to_uppercase());
                } else {
                    out.extend(c.to_lowercase());
                }
                prev_alnum = alnum;
            }
            buf.extend_from_slice(out.as_bytes());
        }
    }
}

/// Change the case of every string (nulls stay null). `title` capitalises the first letter of
/// every run of alphanumeric characters and lowercases the rest.
pub fn case(col: &Col<'_>, mode: CaseMode) -> Result<StringArray, String> {
    build_utf8(col.len(), &|i, buf| {
        if col.is_null(i) {
            return false;
        }
        match col {
            Col::S32(a) => convert_case(a.value(i), mode, buf),
            Col::S64(a) => convert_case(a.value(i), mode, buf),
            Col::Int(_) => col.write(i, 0, buf),
        }
        true
    })
}

/// `pool[indices[i]]` for every row; a null pool entry or a null index gives null. Indices
/// must lie in `0..pool.len()`.
pub fn pool_take(pool: &Col<'_>, indices: &arrow_array::Int64Array) -> Result<StringArray, String> {
    if matches!(pool, Col::Int(_)) {
        return Err("pool_take needs a string pool".into());
    }
    let size = pool.len() as i64;
    for i in 0..indices.len() {
        if indices.is_valid(i) {
            let v = indices.value(i);
            if v < 0 || v >= size {
                return Err(format!("pool index {v} out of range 0..{size}"));
            }
        }
    }
    build_utf8(indices.len(), &|i, buf| {
        if indices.is_null(i) {
            return false;
        }
        let p = indices.value(i) as usize;
        if pool.is_null(p) {
            return false;
        }
        pool.write(p, 0, buf);
        true
    })
}

const HEX: &[u8; 16] = b"0123456789abcdef";

/// RFC 4122 version-4 UUIDs: each row's 16 bytes are its words 0 and 1 in little-endian order,
/// with the version and variant bits set; lowercase hex in the 8-4-4-4-12 layout.
pub fn uuid4(key: [u64; 2], row_start: u64, n_rows: usize) -> Result<StringArray, String> {
    let mut bytes = vec![0u8; n_rows * 36];
    // Each row owns a fixed 36-byte slice, so the build is a plain parallel fill.
    let mut raw = vec![[0u8; 16]; n_rows];
    for_row_chunks(key, row_start, 2, &mut raw, true, &|words, chunk| {
        for (r, o) in chunk.iter_mut().enumerate() {
            o[..8].copy_from_slice(&words[2 * r].to_le_bytes());
            o[8..].copy_from_slice(&words[2 * r + 1].to_le_bytes());
            o[6] = (o[6] & 0x0F) | 0x40;
            o[8] = (o[8] & 0x3F) | 0x80;
        }
    });
    let fill = |(r, dst): (usize, &mut [u8])| {
        let b = &raw[r];
        let mut j = 0;
        for (k, byte) in b.iter().enumerate() {
            if matches!(k, 4 | 6 | 8 | 10) {
                dst[j] = b'-';
                j += 1;
            }
            dst[j] = HEX[(byte >> 4) as usize];
            dst[j + 1] = HEX[(byte & 15) as usize];
            j += 2;
        }
    };
    if n_rows >= PAR_MIN_ROWS && crate::can_par() {
        bytes.par_chunks_mut(36).enumerate().for_each(fill);
    } else {
        bytes.chunks_mut(36).enumerate().for_each(fill);
    }
    let offsets: Vec<i32> = (0..=n_rows).map(|i| (i * 36) as i32).collect();
    StringArray::try_new(
        OffsetBuffer::new(ScalarBuffer::from(offsets)),
        Buffer::from_vec(bytes),
        None,
    )
    .map_err(|e| e.to_string())
}

/// `length` characters per row from `alphabet`: character `j` of row `r` is
/// `alphabet[below(word j of row r, len(alphabet))]`.
pub fn random_chars(
    key: [u64; 2],
    row_start: u64,
    n_rows: usize,
    length: usize,
    alphabet: &[char],
) -> Result<StringArray, String> {
    if alphabet.is_empty() {
        return Err("random_chars needs a non-empty alphabet".into());
    }
    if length == 0 {
        return build_utf8(n_rows, &|_, _| true);
    }
    // Build per task so each task's words are generated where they are used.
    let n_tasks = n_rows.div_ceil(TASK_ROWS);
    let alpha_len = alphabet.len() as u64;
    let run = |t: usize| {
        let (lo, hi) = (t * TASK_ROWS, ((t + 1) * TASK_ROWS).min(n_rows));
        let mut words = vec![0u64; (hi - lo) * length];
        super::rng::fill_words(key, (row_start + lo as u64) * length as u64, &mut words);
        let mut out = String::with_capacity((hi - lo) * length);
        let mut ends = Vec::with_capacity(hi - lo);
        for r in 0..hi - lo {
            for j in 0..length {
                out.push(alphabet[below(words[r * length + j], alpha_len) as usize]);
            }
            ends.push(out.len());
        }
        (out, ends)
    };
    let parts: Vec<_> = if n_rows >= PAR_MIN_ROWS && crate::can_par() {
        (0..n_tasks).into_par_iter().map(run).collect()
    } else {
        (0..n_tasks).map(run).collect()
    };
    let total: usize = parts.iter().map(|p| p.0.len()).sum();
    if i32::try_from(total).is_err() {
        return Err("string output exceeds 2 GiB: use smaller chunks".into());
    }
    let mut values: Vec<u8> = Vec::with_capacity(total);
    let mut offsets: Vec<i32> = Vec::with_capacity(n_rows + 1);
    offsets.push(0);
    let mut base = 0usize;
    for (s, ends) in parts {
        offsets.extend(ends.iter().map(|e| (base + e) as i32));
        base += s.len();
        values.extend_from_slice(s.as_bytes());
    }
    StringArray::try_new(
        OffsetBuffer::new(ScalarBuffer::from(offsets)),
        Buffer::from_vec(values),
        None,
    )
    .map_err(|e| e.to_string())
}

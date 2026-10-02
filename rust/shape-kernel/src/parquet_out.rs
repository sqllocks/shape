//! Native Parquet writer: one file, row groups encoded in parallel.
//!
//! `pyarrow`'s `ParquetWriter` encodes a file on one thread. This writer collects the batches it
//! is given into row groups and encodes every (row group, column) pair as its own task on the
//! rayon pool, while the caller (a Python writer thread) goes on generating; finished column
//! chunks are appended to the file in row-group order. `close` waits for the tasks and writes
//! the footer. The Python twin is the pyarrow `ParquetWriter` (`shape.builtins.sinks.files`):
//! values read back equal; only the bytes differ.

use std::collections::BTreeMap;
use std::fs::File;
use std::sync::{Arc, Condvar, Mutex};

use arrow_array::RecordBatch;
use arrow_schema::{DataType, SchemaRef};
use parquet::arrow::arrow_writer::{
    compute_leaves, ArrowColumnChunk, ArrowColumnWriter, ArrowRowGroupWriterFactory,
};
use parquet::arrow::{add_encoded_arrow_schema_to_metadata, ArrowSchemaConverter};
use parquet::basic::Compression;
use parquet::file::properties::{EnabledStatistics, WriterProperties};
use parquet::file::writer::SerializedFileWriter;
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3_arrow::{PyRecordBatch, PySchema};

/// Row groups that may be encoding or waiting to be appended at once: bounds the memory held by
/// batches and encoded chunks when the encoders fall behind the generator.
const MAX_IN_FLIGHT: usize = 4;

struct State {
    writer: Option<SerializedFileWriter<File>>,
    /// Next row group to append to the file.
    next_commit: usize,
    /// Encoded chunks per row group (indexed by column) that wait for their turn.
    done: BTreeMap<usize, Vec<Option<ArrowColumnChunk>>>,
    /// Columns still being encoded per row group.
    remaining: BTreeMap<usize, usize>,
    /// Row groups submitted and not yet appended.
    in_flight: usize,
    /// Row groups the file will have, once `finish` has been called.
    expected: Option<usize>,
    /// The footer is written and the file closed.
    finished: bool,
    error: Option<String>,
}

struct Shared {
    state: Mutex<State>,
    cv: Condvar,
    factory: ArrowRowGroupWriterFactory,
    schema: SchemaRef,
}

/// Write the footer once `finish` has said how many row groups there are and all are appended.
fn finalize(st: &mut State) {
    if st.finished || st.error.is_some() || st.expected != Some(st.next_commit) {
        return;
    }
    if let Some(writer) = st.writer.take() {
        if let Err(e) = writer.close() {
            st.error = Some(e.to_string());
        }
    }
    st.finished = true;
}

impl Shared {
    fn fail(&self, message: String) {
        let mut st = self.state.lock().unwrap();
        st.error.get_or_insert(message);
        self.cv.notify_all();
    }

    /// Store one finished column chunk; append every row group that is complete and next.
    fn finish_column(&self, rg: usize, col: usize, ncols: usize, chunk: ArrowColumnChunk) {
        let mut st = self.state.lock().unwrap();
        st.done
            .entry(rg)
            .or_insert_with(|| (0..ncols).map(|_| None).collect())[col] = Some(chunk);
        let left = st.remaining.entry(rg).or_insert(ncols);
        *left -= 1;
        loop {
            let next = st.next_commit;
            if st.remaining.get(&next).copied() != Some(0) {
                break;
            }
            st.remaining.remove(&next);
            let chunks = st.done.remove(&next).unwrap_or_default();
            if st.error.is_none() {
                let result = (|| -> Result<(), String> {
                    let writer = st.writer.as_mut().ok_or("file already closed")?;
                    let mut group = writer.next_row_group().map_err(|e| e.to_string())?;
                    for chunk in chunks.into_iter().flatten() {
                        chunk
                            .append_to_row_group(&mut group)
                            .map_err(|e| e.to_string())?;
                    }
                    group.close().map_err(|e| e.to_string())?;
                    Ok(())
                })();
                if let Err(e) = result {
                    st.error = Some(e);
                }
            }
            st.next_commit += 1;
            st.in_flight -= 1;
        }
        finalize(&mut st);
        self.cv.notify_all();
    }
}

/// A Parquet file being written. `write_batch` never blocks on encoding, except to keep at most
/// [`MAX_IN_FLIGHT`] row groups in memory; `close` returns when the file is complete.
#[pyclass(module = "shape._kernel")]
pub struct ParquetOut {
    shared: Arc<Shared>,
    pending: Vec<RecordBatch>,
    pending_rows: usize,
    row_group_rows: usize,
    next_rg: usize,
    closed: bool,
    /// Encode on the rayon pool; otherwise on the thread that calls `write_batch` and `close`.
    parallel: bool,
}

fn flat(dt: &DataType) -> bool {
    !matches!(
        dt,
        DataType::List(_)
            | DataType::LargeList(_)
            | DataType::FixedSizeList(_, _)
            | DataType::Struct(_)
            | DataType::Map(_, _)
            | DataType::Union(_, _)
            | DataType::Dictionary(_, _)
            | DataType::RunEndEncoded(_, _)
    )
}

#[pymethods]
impl ParquetOut {
    /// Create `path` and its footer-less file. `compression` is `snappy` or `none`; any other
    /// codec, or a nested column type, is a `ValueError` (the caller falls back to pyarrow).
    #[new]
    #[pyo3(signature = (path, schema, row_group_rows=262_144, compression="snappy", use_dictionary=true, dictionary_page_bytes=131_072, parallel=true))]
    fn new(
        path: &str,
        schema: PySchema,
        row_group_rows: usize,
        compression: &str,
        use_dictionary: bool,
        dictionary_page_bytes: usize,
        parallel: bool,
    ) -> PyResult<Self> {
        let schema: SchemaRef = schema.into_inner();
        if let Some(f) = schema.fields().iter().find(|f| !flat(f.data_type())) {
            return Err(PyValueError::new_err(format!(
                "column '{}' has a nested or dictionary type ({:?})",
                f.name(),
                f.data_type()
            )));
        }
        let codec = match compression {
            "snappy" => Compression::SNAPPY,
            "none" | "uncompressed" => Compression::UNCOMPRESSED,
            other => {
                return Err(PyValueError::new_err(format!(
                    "native Parquet writer does not support compression {other:?}"
                )))
            }
        };
        if row_group_rows == 0 {
            return Err(PyValueError::new_err("row_group_rows must be at least 1"));
        }
        let mut props = WriterProperties::builder()
            .set_compression(codec)
            .set_dictionary_enabled(use_dictionary)
            .set_dictionary_page_size_limit(dictionary_page_bytes)
            .set_statistics_enabled(EnabledStatistics::Chunk)
            .set_created_by("sqllocks-shape".to_string())
            .build();
        add_encoded_arrow_schema_to_metadata(&schema, &mut props);
        let props = Arc::new(props);
        let descriptor = ArrowSchemaConverter::new()
            .with_coerce_types(false)
            .convert(&schema)
            .map_err(|e| PyValueError::new_err(e.to_string()))?;
        let file = File::create(path)
            .map_err(|e| PyRuntimeError::new_err(format!("cannot create {path}: {e}")))?;
        let writer = SerializedFileWriter::new(file, descriptor.root_schema_ptr(), props)
            .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
        let factory = ArrowRowGroupWriterFactory::new(&writer, schema.clone());
        let shared = Arc::new(Shared {
            state: Mutex::new(State {
                writer: Some(writer),
                next_commit: 0,
                done: BTreeMap::new(),
                remaining: BTreeMap::new(),
                in_flight: 0,
                expected: None,
                finished: false,
                error: None,
            }),
            cv: Condvar::new(),
            factory,
            schema,
        });
        Ok(Self {
            shared,
            pending: Vec::new(),
            pending_rows: 0,
            row_group_rows,
            next_rg: 0,
            closed: false,
            parallel,
        })
    }

    /// Add a batch; a row group is handed to the encoders once `row_group_rows` rows are held.
    fn write_batch(&mut self, py: Python<'_>, batch: PyRecordBatch) -> PyResult<()> {
        if self.closed {
            return Err(PyRuntimeError::new_err("write to a closed ParquetOut"));
        }
        let batch = batch.into_inner();
        if batch.schema().fields() != self.shared.schema.fields() {
            return Err(PyValueError::new_err(
                "batch schema differs from the file's schema",
            ));
        }
        self.pending_rows += batch.num_rows();
        self.pending.push(batch);
        if self.pending_rows >= self.row_group_rows {
            self.flush(py)?;
        }
        Ok(())
    }

    /// Hand what is held to the encoders and return: the last row group to be appended writes the
    /// footer. `wait` blocks until the file is complete. Called once; later calls do nothing.
    fn finish(&mut self, py: Python<'_>) -> PyResult<()> {
        if self.closed {
            return Ok(());
        }
        self.closed = true;
        let flushed = self.flush(py);
        {
            let mut st = self.shared.state.lock().unwrap();
            st.expected = Some(self.next_rg);
            finalize(&mut st);
            self.shared.cv.notify_all();
        }
        flushed
    }

    /// Block until the file is complete (after `finish`), or raise the first error.
    fn wait(&self, py: Python<'_>) -> PyResult<()> {
        let shared = Arc::clone(&self.shared);
        py.detach(move || {
            let mut st = shared.state.lock().unwrap();
            while !st.finished && st.error.is_none() {
                st = shared.cv.wait(st).unwrap();
            }
            match st.error.clone() {
                Some(e) => Err(e),
                None => Ok(()),
            }
        })
        .map_err(PyRuntimeError::new_err)
    }

    /// `finish` and `wait`: the file is complete when this returns.
    fn close(&mut self, py: Python<'_>) -> PyResult<()> {
        let flushed = self.finish(py);
        let waited = self.wait(py);
        flushed.and(waited)
    }

    fn __enter__(slf: Py<Self>) -> Py<Self> {
        slf
    }

    #[pyo3(signature = (*_args))]
    fn __exit__(
        &mut self,
        py: Python<'_>,
        _args: &Bound<'_, pyo3::types::PyTuple>,
    ) -> PyResult<bool> {
        self.close(py)?;
        Ok(false)
    }
}

impl ParquetOut {
    fn flush(&mut self, py: Python<'_>) -> PyResult<()> {
        if self.pending.is_empty() {
            return Ok(());
        }
        let batches = Arc::new(std::mem::take(&mut self.pending));
        self.pending_rows = 0;
        let shared = Arc::clone(&self.shared);
        // Backpressure, without the GIL.
        let blocked = py.detach(|| {
            let mut st = shared.state.lock().unwrap();
            while st.in_flight >= MAX_IN_FLIGHT && st.error.is_none() {
                st = shared.cv.wait(st).unwrap();
            }
            if let Some(e) = st.error.clone() {
                return Err(e);
            }
            st.in_flight += 1;
            Ok(())
        });
        blocked.map_err(PyRuntimeError::new_err)?;
        let rg = self.next_rg;
        self.next_rg += 1;
        let writers = match self.shared.factory.create_column_writers(rg) {
            Ok(w) => w,
            Err(e) => {
                self.shared.fail(e.to_string());
                return Err(PyRuntimeError::new_err(e.to_string()));
            }
        };
        let ncols = writers.len();
        {
            let mut st = self.shared.state.lock().unwrap();
            st.remaining.insert(rg, ncols);
        }
        if ncols == 0 {
            // A file without columns: nothing to encode, but keep the bookkeeping moving.
            let mut st = self.shared.state.lock().unwrap();
            st.remaining.insert(rg, 0);
            st.in_flight -= 1;
            return Ok(());
        }
        for (col, writer) in writers.into_iter().enumerate() {
            let shared = Arc::clone(&self.shared);
            let batches = Arc::clone(&batches);
            let task = move || match encode_column(&shared.schema, &batches, col, writer) {
                Ok(chunk) => shared.finish_column(rg, col, ncols, chunk),
                Err(e) => shared.fail(e),
            };
            if self.parallel && crate::can_par() {
                rayon::spawn(task);
            } else {
                py.detach(task);
            }
        }
        Ok(())
    }
}

fn encode_column(
    schema: &SchemaRef,
    batches: &[RecordBatch],
    col: usize,
    mut writer: ArrowColumnWriter,
) -> Result<ArrowColumnChunk, String> {
    let field = schema.field(col);
    for batch in batches {
        for leaf in compute_leaves(field, batch.column(col)).map_err(|e| e.to_string())? {
            writer.write(&leaf).map_err(|e| e.to_string())?;
        }
    }
    writer.close().map_err(|e| e.to_string())
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<ParquetOut>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use arrow_schema::{Field, TimeUnit};

    #[test]
    fn flat_types_are_written_and_nested_ones_are_not() {
        for dt in [
            DataType::Int64,
            DataType::Float64,
            DataType::Utf8,
            DataType::Boolean,
            DataType::Date32,
            DataType::Timestamp(TimeUnit::Nanosecond, None),
        ] {
            assert!(flat(&dt), "{dt:?}");
        }
        let item = Arc::new(Field::new("item", DataType::Int64, true));
        assert!(!flat(&DataType::List(item)));
        assert!(!flat(&DataType::Dictionary(
            Box::new(DataType::Int32),
            Box::new(DataType::Utf8)
        )));
    }
}

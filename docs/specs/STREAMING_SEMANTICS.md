# Streaming Semantics — Draft 2

Shape distinguishes event time from processing time. Stateful operators declare windowing, allowed lateness and checkpoint semantics. Watermarks determine closable windows; events older than the watermark are handled by an explicit late-data policy. State MUST be bounded or externally checkpointed. Replay MUST be deterministic for deterministic sources/operators. Source delivery guarantees and sink commit guarantees are reported separately; Shape does not claim exactly-once when an underlying connector cannot provide it.

This draft makes those rules precise for the stream runtime (`shape.streaming.runtime`). Section 1 to 4 are implemented by P3-01; keyed state and checkpoints are added by P3-02 and P3-03.

## 1. Events and time

- A micro-batch is an Arrow `RecordBatch`. Rows within a batch are in arrival order, and batches are processed in arrival order.
- Event time is a timestamp or date column, `_shape_event_time` by default (`event_time=` names another). Timestamps are read as instants (the zone is ignored) and truncated to microseconds; dates are midnight UTC. A row whose event time is null belongs to no window: it is counted (`null_event_time`) and skipped.
- Stream rows may also carry `_shape_table` and `_shape_seq` (the idempotency key `(_shape_table, _shape_seq)`); the runtime profiles them like any other column and does not interpret them.

## 2. Windows

All windows are half-open, `[start, end)`, in microseconds since the epoch, and are profiled in bounded mode (the T-14 sketches), so a window's memory does not grow with its row count.

- **Tumbling** (`size`, optional `offset`): windows `[k*size + offset, (k+1)*size + offset)`.
- **Sliding** (`size`, `slide <= size`, optional `offset`): windows `[k*slide + offset, k*slide + offset + size)`. A row belongs to every window that contains it. The runtime profiles *panes* of `gcd(size, slide)` once and merges the panes of a window when it closes; a pane is dropped when the last window containing it has closed. A window made of a single pane is not merged.
- **Session** (`gap`): a session extends while consecutive events are less than `gap` apart, and covers `[first event, last event + gap)`. An event that falls between two sessions closer than `gap` to both merges them.
- **Global**: one window over the whole stream, emitted at the end of the stream. It needs no event time and equals bounded batch profiling of the same rows.

A closed window yields a `WindowProfile`: its kind, bounds, row count and the profile engine's table entry (the same layout as a batch profile, with `mode: "bounded"` error models).

## 3. Watermarks, allowed lateness and late data

- The **watermark** is the largest event time seen, minus `allowed_lateness`. It never moves back, and it is `None` before the first event.
- The watermark advances at **batch boundaries**: a micro-batch is classified against the watermark as it stood when the batch arrived, and then advances it. Nothing inside one batch is late, whatever its order.
- A window **closes** when the watermark reaches its end (`end <= watermark`), and is emitted exactly once, oldest first, if it holds any row. `finish()` (end of stream) closes every open window.
- A row is **late** when every window it belongs to has closed (for a session: when `event time + gap <= watermark` and the row falls inside no open session). A row that still belongs to at least one open window is accepted, and joins only the windows that are open; a window already emitted is never revised.
- The late-data policy is explicit. Late rows are counted (`late_events`) and either dropped (the default) or handed, as a batch, to `late_sink`.

## 4. Snapshots

- `snapshot()` returns a JSON-safe `dict` (format `shape-stream-window-v1`) holding the configuration, the schema, the maximum event time, the counters, and the state of every open window (the kernel's bounded-state bytes, compressed). `restore(snapshot)` rebuilds the profiler; `late_sink` is code and is passed again.
- **Restore equivalence:** a profiler snapshotted after any batch and restored in a new process gives, for the rest of the stream, exactly the windows and counters of an uninterrupted run. Both kernels (Rust and the Python reference) read each other's snapshots.
- Only bounded mode has a snapshot format.
- **Determinism:** the same batches in the same order give byte-identical output across processes and `PYTHONHASHSEED` values (hashing is seeded XXH3, T-13).

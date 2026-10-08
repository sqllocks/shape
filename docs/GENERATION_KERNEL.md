# Generation kernel

The native kernel (`rust/shape-kernel/src/gen/`, exposed as `shape._kernel`) does the
per-row work of generation: random streams, categorical sampling, string assembly and temporal
sampling. Every function has a pure-Python twin in `shape.kernel.reference.gen`; `SHAPE_KERNEL`
picks the implementation (`auto`, `rust`, `python`). Integer and string results of the two are
equal bit for bit. Float results that pass through `log`, `cos` or `exp` (`philox_normal`,
`hour_weights_peaks`) agree to a few ulp, because the two use different libm routines.

Arrays cross the boundary as Arrow arrays (the PyCapsule interface). Native results are Arrow
arrays; wrap them with `pyarrow.array(...)` to get `pyarrow` objects. Inputs are `pyarrow` arrays.

## The random stream

A stream is keyed by two unsigned 64-bit words `(k0, k1)` (`shape.generation.rng.stream_key`
gives the 128-bit key `k0 | k1 << 64`). It is Philox4x64-10 (T-16), implemented in
`gen/rng.rs`. The stream's `j`-th 64-bit output is word `j % 4` of
`numpy.random.Philox(key=k0 | k1 << 64, counter=j // 4).random_raw(4)`: numpy is the known-answer
oracle, and `tests/kernel/test_gen_kernel.py` checks the kernel against it.

A row owns `per_row` consecutive words, so row `r` reads words `r * per_row .. (r + 1) * per_row - 1`.
Every function below that draws numbers takes `(k0, k1, row_start, n_rows)` and, where a row uses
several words, `per_row` and `slot` (the first of the row's words the function reads). A result
depends on `(key, row)` alone, so it is the same however the rows are split into calls, chunks
or threads. Calls with at least 32,768 rows run on all cores (`SHAPE_THREADS` / `set_threads`).

Conversions (shared by both implementations):

* uniform: `(word >> 11) * 2**-53`, in `[0, 1)`;
* index below `n` (`n < 2**32`): `floor(word * n / 2**64)`, integer arithmetic;
* normal: Box-Muller, `sqrt(-2 ln(1 - u1)) * cos(2 pi u2)` from words `slot` and `slot + 1`.

## Functions

| Function | Words per row | Result |
|---|---|---|
| `philox_words(k0, k1, row_start, n_rows, per_row=1)` | `per_row` | uint64, `n_rows * per_row` words |
| `philox_uniform(k0, k1, row_start, n_rows, per_row=1, slot=0)` | uses 1 | float64 |
| `philox_normal(k0, k1, row_start, n_rows, per_row=2, slot=0)` | uses 2 | float64 |
| `alias_build(weights)` | | `(prob float64, alias int64)`: Vose's method, deterministic |
| `alias_sample(prob, alias, k0, k1, row_start, n_rows, per_row=2, slot=0)` | uses 2 | int64 category per row |
| `pool_take(pool, indices)` | | `string`: `pool[indices]`, nulls stay null; `pool` is string or large_string |
| `template_strings(literals, slots, columns, n_rows)` | | `string`: `literals[0] + col + literals[1] + ...`; `slots` are `(column index, zero-pad width)`; a null in a used column gives null; columns are string, large_string or int64 |
| `join_strings(columns, sep, skip_nulls=False)` | | `string`: the columns joined with `sep` |
| `string_case(array, mode)` | | `string`: `upper`, `lower` or `title` (first letter of each alphanumeric run upper, the rest lower) |
| `uuid4_strings(k0, k1, row_start, n_rows)` | 2 | `string`: version-4 UUIDs; the 16 bytes are word 0 then word 1, little-endian |
| `random_strings(k0, k1, row_start, n_rows, length, alphabet)` | `length` | `string`: `length` characters per row, one word each |
| `day_weights(start_day, n_days, month_weights, dow_weights, per_bucket=True)` | | float64 per day: `month_w[m] * dow_w[d]`, divided (with `per_bucket`) by the number of days in the range with that month and weekday, so each (month, weekday) pair carries its own weight |
| `hour_weights_peaks(peaks, std)` | | the 24 hour weights of equally likely Gaussian peaks, wrapped modulo 24 |
| `temporal_sample(day_weights, hour_weights, start_day, k0, k1, row_start, n_rows, whole_seconds=False)` | 5 | `timestamp[us]`: a day, an hour, and an offset inside the hour; words 0-1 day, 2-3 hour, 4 offset |
| `first_flags(codes)` | | `bool`: true on the first row of each non-negative group code (`0 <= code < len`); a negative code is never first |
| `group_order(codes, keys)` | | `(rank, size, next)`, all int64: the 0-based place of each row in its group sorted by `keys` (ties keep row order), the group's size, and the row that follows it (-1 for the last); a negative code gives `(-1, 0, -1)` |
| `dense_rows(keys, start, size)` | | `int64` row of the sequence key `start, start + 1, ...` (`size` rows) that holds each key; null for a null key or one outside the sequence |
| `group_sums(keys, values, start, size)` | | `(sums, counts)`: per row of that sequence, the sum (the type of `values`, int64 or float64) and the count of the non-null `values` of the child rows whose key it is; added in row order (a float sum is the sequential one), a null or unknown key is skipped, an integer sum wraps |
| `scd2_offsets(codes, total_days, min_gap, k0, k1)` | per group | int64 day offsets of SCD type 2 effective dates (see below); -1 for a negative code |
| `cap_per_parent(indices, pool, max_per_parent, k0, k1)` | up to 64 per moved row | int64 parent indices with at most `max_per_parent` rows each (see below) |

Days are numbers of days since 1970-01-01 (a Thursday); weekdays count from Monday = 0.

## Alias tables

`alias_build` follows Vose's method: weights are scaled to mean 1 (`w * n / sum`, the sum taken
left to right), indices below 1 go on the `small` stack and the others on `large`, and each pair
`(small, large)` pops the stacks' last elements. The Python twin does the same operations in the
same order, so the tables are equal. A draw takes word `slot` to pick column `i`, and word
`slot + 1` to choose between `i` (if `unit < prob[i]`) and `alias[i]`.

## Row-sequential kernels

`first_flags`, `group_order`, `scd2_offsets` and `cap_per_parent` (`rust/shape-kernel/src/gen/relational.rs`,
twin `shape.kernel.reference.relational`) are the passes where a row's value depends on the rows before it, so
they read a whole column. Strategies call them once per table through `shape.generation.kernel_relational`
and keep the result (`Engine.cached`). Every result is an integer or a flag, so native and twin are equal.
They are functions of their inputs and the stream key alone: threads, chunking and call order never matter.

* **Group codes** are dense (`pyarrow` dictionary codes, `0 <= code < len`); a negative code is "no group".
* **`scd2_offsets`**: a group of `m` rows (equal codes, in row order) draws from its own stream, keyed by
  the group's first row: `g0 = mix(k0 ^ mix(anchor))`, `g1 = mix(k1 ^ anchor * 0x9E3779B97F4A7C15 ^
  0xD1B54A32D192ED03)` with `mix` the splitmix64 finalizer, words `0 .. m - 1` read as for any stream. With
  `m = 1` the offset is `below(w0, max(total_days, 1))`. Otherwise `usable = max(total_days - min_gap * (m - 1), m)`;
  the `m` values `below(w, usable)` are sorted ascending and the `v`-th row gets
  `min(sorted[v] + min_gap * v, total_days)`.
* **`cap_per_parent`**: rows are visited in order with a count per parent. A row whose parent is full draws
  candidate parents from word `row * 64 + attempt` of the stream (`below(word, pool)`), takes the first with room,
  and after 64 failed draws the first parent with room after its first draw; if every parent is full it takes its
  first draw. A row whose parent has room keeps it.

## Microbenchmarks

The kernel benchmark script (`kernel_bench.py` in the benchmarks harness directory) times every function on 1,000,000 rows against
its Python twin and, where there is one, the numpy or Arrow call that does the same job. It checks
equivalence first and writes the `kernel_microbench` key of the harness's committed `results.json`.

`dense_rows` and `group_sums` (same file and twin) are the single-pass key lookup and grouped sum that the post-passes
(compute phase, business-rule repair) and `lookup` use when a parent's key is a sequence (`shape.generation.keypos`). A float
sum is the plain left-to-right sum in row order, so native and twin are bit-equal.

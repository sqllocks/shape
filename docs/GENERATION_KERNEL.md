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

Days are numbers of days since 1970-01-01 (a Thursday); weekdays count from Monday = 0.

## Alias tables

`alias_build` follows Vose's method: weights are scaled to mean 1 (`w * n / sum`, the sum taken
left to right), indices below 1 go on the `small` stack and the others on `large`, and each pair
`(small, large)` pops the stacks' last elements. The Python twin does the same operations in the
same order, so the tables are equal. A draw takes word `slot` to pick column `i`, and word
`slot + 1` to choose between `i` (if `unit < prob[i]`) and `alias[i]`.

## Microbenchmarks

The kernel benchmark script (`kernel_bench.py` in the benchmarks harness directory) times every function on 1,000,000 rows against
its Python twin and, where there is one, the numpy or Arrow call that does the same job. It checks
equivalence first and writes the `kernel_microbench` key of the harness's committed `results.json`.

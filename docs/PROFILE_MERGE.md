# Mergeable profiles

Daily captures roll up into weekly or monthly baselines, and the partitions of a large table
profile separately and combine, without reading the data again.

```bash
# profile each partition, keeping the mergeable sketch state
shape profile day1.parquet -o day1.shape --sketches
shape profile day2.parquet -o day2.shape --sketches

# combine them
shape profile merge day1.shape day2.shape -o week.shape --name week

# profiles written without --sketches can still be merged for their exact statistics
shape profile merge a.shape b.shape -o ab.shape --exact-only
```

```python
import shape
from shape.profile import merge_profiles, MergeError

a = shape.profile("day1.parquet", sketches=True)
b = shape.profile("day2.parquet", sketches=True)
week = merge_profiles([a, b], name="week")        # or exact_only=True without sketches
week.merged_from        # [{"name", "shape_content_id", "row_count", "sketches"}, ...]
week.content_id         # the content id of the merged profile itself
```

## What merges, and how well

| Statistic | Merged from | Result against profiling the union |
|---|---|---|
| `row_count`, `null_count`, `min_value`, `max_value` | the profiles | **exact** |
| `null_rate` | the merged counts | **exact** (rounded to 6 places as in any profile) |
| `mean`, `std` | the pairwise (Chan) update of each profile's count, mean and variance | equal up to floating-point rounding (relative 1e-9); NaN and infinite values propagate as in the union |
| `cardinality`, `cardinality_ratio` | HyperLogLog sketches | within the sketch's error (below) |
| `quantiles` (`p1`, `p5`, `p25`, `p50`, `p75`, `p95`, `p99`) | KLL sketches | within the sketch's rank error (below) |
| top values (`merge.sketch_columns.<column>.top`) | SpaceSaving sketches | counts within the sketch's error (below) |
| `is_unique` | not mergeable from sketches | unknown (`null`) |
| distribution fits, enum and pattern detection, string lengths, hour/day/year histograms, correlations, keys and relationships | depend on the whole data | unknown (`null`, empty) in a merged profile |

Unknown means `None`: a merged profile never carries a number it could not compute. The names of
the unknown fields are listed in `merge.unavailable` of the merged profile.

Empty and single-row partitions merge like any other. A column with no values in a partition
takes its type from the partitions that have values; `integer` and `float` merge to `float`, and
`date` and `datetime` to `datetime`. Any other type conflict, and profiles that do not share their
columns (or, for a dataset, their tables), are a `MergeError`.

## Error bounds of each sketch

The sketches are the bounded-mode sketches of the profile engine. Each merged column records its
error models in `merge.sketch_columns.<column>.error_models`.

**HyperLogLog** (distinct count, precision 14, 16,384 registers). Merging takes the register-wise
maximum, so the merged sketch is *identical* to the sketch of the union: merging adds no error,
and merging is exactly associative and order free. The standard error of the estimate is
1.04 / sqrt(16384) = 0.81%, so about 95% of estimates are within 1.6% of the true distinct count
(small counts are close to exact). The estimate is clamped to the number of non-null values.

**KLL** (quantiles, k = 200). The rank error is at most 2 / k = 1.0%, with 99% confidence:
the value returned for quantile `q` has a true rank within `q ± 0.01` of the data. Merging keeps
the bound, but a merge is not bit-for-bit associative: merging in a different order or grouping can
return a different value inside the bound. The error in *value* units depends on how dense the data
is near the quantile; the rank bound is what is guaranteed.

**SpaceSaving** (top values, capacity 64). For every reported value, `count - error <= true count
<= count`, and `error <= n / 64`, where `n` is the number of non-null values in the union. Every
value whose true count is more than `n / 64` is in the list. Merging follows the mergeable-summaries
rule: a value missing from a full summary is credited with that summary's smallest count and the
error terms add, so the bound holds for the union.

The tests in `tests/profile/test_merge.py` enforce each of these on merged partitions, including
empty and single-row ones, and compare associativity across groupings.

## Sketch state in the file

`shape profile --sketches` (or `shape.profile(..., sketches=True)`) reads the data one more time,
in bounded memory, and stores the sketch state of each table as an optional component,
`sketches.json`, of the `.shape` file. The manifest names it as
`"sketches": {"format": "shape-profile-sketches", "version": 1}`. The profile body and its
content id are the same with and without it, and a profile written without `--sketches` is
byte-for-byte what it was before.

`sketches.json` holds, per table, the row count, the Arrow schema and the kernel's state
snapshot (both base64). The native and the pure-Python kernel read and write the same state, and
merge it to the same result. A reader of a newer `version` than it knows refuses the file with an
error that asks for an upgrade; an unreadable `sketches.json` is an `ArtifactError`.

Limits: the sketches of two inputs combine only when their Arrow column types match (profile
the partitions with a common schema; the exact statistics merge across `integer` and `float`
regardless). `shape profile export` writes the profile only, without the sketch state.

## Lineage and content ids

A merged profile records the content id of every input, in order, in `merge.inputs`, and
`Profile.merged_from` returns them. The ids are part of the merged profile's body, so the
merged profile's own content id changes with its inputs, and merging a merged profile names that
profile, so the history stays walkable. When every input had sketch state, the merged profile has
the merged sketch state too, and can be merged again.

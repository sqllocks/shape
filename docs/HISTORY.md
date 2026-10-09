# History tools: `shape bisect` and `shape timelapse`

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" HISTORY
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for HISTORY
    ```


The registry (`docs/REGISTRY.md`) keeps every committed profile of a name with its business date.
These commands read that history; they never read data and never change the registry.

| Command | Question it answers |
|---|---|
| `shape bisect REGISTRY NAME --good REF --bad REF` | Which committed version is the first that changed? |
| `shape bisect layers --layers a,b,c --good-date D1 --bad-date D2` | In which layer of a pipeline did a change appear? |
| `shape timelapse REGISTRY NAME --column COL` | How did one column's distribution move over time? |

Python: `shape.versions.bisect`, `bisect_layers` and `timelapse`. Each returns a result object whose
`to_dict()` equals the JSON the command prints.

What the history has to hold: **raw profiles**, committed with `--allow-raw` (a share-safe profile
holds no values to compare; see "Limits"). Profiles made with `shape profile --sketches` can also
be merged into windows (`--coarse`, `--window`).

## A worked example

Plant a step in a 31-day history with `shape generate-drift`, commit each day with its business
date, and find the day again.

The day (2026-03-15) and the column (`amount`) are the ones in `feed/ground_truth.json`. The answer
key is what the tests of this feature compare against, for ranges of 1, 2 and 30 candidate
versions.

[Run this example](#local-example-2).


(The listing shows some of the lines; the command prints one per statistic.)

## `shape bisect`

<!-- example: 3 -->

Syntax reference. Replace the named arguments with your inputs.

```
shape bisect REGISTRY NAME --good REF --bad REF [--column COL] [--kind KIND] [--contract FILE]
             [--source NAME] [--verify-all] [--coarse week|month] [--json]
             [--project FILE | --no-project] [diff threshold flags]
```


`REF` is what `shape registry` accepts: `latest`, a tag, a promoted ref or a content id. The
versions of `NAME` are ordered by their `business_date` (else by commit time); the candidates are
the versions after `--good` up to and including `--bad`. A content id committed twice names its
newest commit.

**The test.** By default a version is *bad* when `shape diff` of it against the good version
reports a change, under the thresholds and ignore lists of the `shape.yml` source (`--source`, or
the only source, or the one named like `NAME`; the threshold flags of `shape diff`, `--ignore`,
`--only` and `--policy` override them as they do there). `--column` and `--kind` keep only the
changes of that column and diff kind. With `--contract FILE` a version is bad when the contract
fails (`--column` keeps only violations of that column; `--kind` does not apply).

**The bound.** For *n* candidates a bisect tests at most `ceil(log2(n)) + 2` versions: `--bad`, the
good version (only a contract can fail it) and the search over the others. The result reports
`candidates`, `evaluated` and `max_evaluations`.

**Exit codes.** 0 when the first bad version is found. 2 when `--good` itself tests bad, when
`--bad` tests good, when `--good` is not older than `--bad`, or when a version cannot be tested
(an unknown name or ref, a share-safe or non-profile version).

The result names the first bad version (`ref`, `content_id`, `business_date`), the last good one,
and the `changes` between them (`column`, `kind`, `before`, `after`, `severity`, `score`).
`changes_vs_good` (diff test) or `violations` (contract test) is the evidence for the first bad
version. When no single step between the last good and the first bad version is a change (a slow
ramp), `changes` is empty and a warning says to read `changes_vs_good`.

### `--verify-all`

Bisect assumes the change persists once it appears. `--verify-all` tests every candidate instead,
reports the same first bad version, and lists in `flips` every version that tests good *after* a
bad one, with a warning that bisect assumes the change persists.

### `--coarse week|month`

`--coarse` first bisects over windows (ISO weeks, or calendar months, of the candidates' dates)
whose versions are merged with `shape.profile.merge_profiles` (`docs/PROFILE_MERGE.md`), then
bisects inside the first bad window and the window before it (a change at the end of a window can
be diluted below the thresholds). On a history where the change persists the result is the same
version as a plain bisect.

What it saves is full per-version diffs: the window tests are diffs of merged profiles, so the
search over single versions is confined to two windows (`cost.full_profile_tests` in the result is
smaller than a plain bisect's). It does **not** read fewer profiles: merging a window reads every
profile in it (`cost.versions_read` counts them). Use it when the final search over single
versions is the expensive part, or to see which weeks to look at first.

Rules of a window test: it needs profiles made with `shape profile --sketches` (an error says so);
a merged window holds several versions' rows, so its row count is not compared with a day's
(`row_count_change` is dropped from window tests); and it works with the diff test only (a
contract's row-count and uniqueness rules are about one version). If a window tests bad but its
last version tests good, the change does not persist: the whole range is searched and a warning
says so.

### The result

`shape bisect --json` prints (and `BisectResult.to_dict()` returns) a document with
`"format": "shape-bisect"` and an integer `"version": 1`, with these keys: `name`, `mode`
(`bisect`, `verify-all`, `coarse-week`, `coarse-month`), `test` (`kind`, `column`, `change_kind`,
`contract`, `source`), `found`, `good`, `bad`, `first_bad`, `last_good`, `changes`,
`changes_vs_good` or `violations`, `candidates`, `evaluated`, `max_evaluations`, `evaluations` (the
tests in order), `cost` (`versions_read`, `full_profile_tests`, `window_tests`), `flips` and
`warnings`. It is an output, not a file Shape reads back.

## `shape bisect layers`

<!-- example: 4 -->

Syntax reference. Replace the named arguments with your inputs.

```
shape bisect layers --layers SOURCE[,SOURCE...] --good-date D1 --bad-date D2
                    [--column COL] [--map LAYER.COL=COL]... [--project shape.yml] [--json]
```


Each layer is a source of `shape.yml`, in pipeline order (raw, cleaned, published). A source's
`baseline` gives its registry and name (`kind` does not matter here), and its thresholds and
ignore lists apply. For each layer the version at `D2` is diffed against the version at `D1`: the
**newest version on or before each date**. The command reports:

- the **first layer** where a change appears (`status: origin`);
- the layers after it where the change **persists** (a changed column that is also changed at the
  origin) and where it **disappears** (it is not changed any more; this includes a layer after the
  one that removed it);
- layers before the origin are `unchanged`.

Columns match by name. A column renamed by a layer matches through `--map`: `--map
clean.total_usd=total` says that the layer `clean` calls the column `total_usd`; `--column` and the
reported columns use the canonical name (`total`). Without `--column` every column counts.

**Exit codes.** 0 when a layer shows the change, 1 when none does, 2 for unusable input (an unknown
source, a source without a baseline, no version on or before a date, `--good-date` not before
`--bad-date`, a share-safe version).

The JSON has `"format": "shape-bisect-layers"`, `"version": 1`, `good_date`, `bad_date`, `column`,
`found`, `first_layer`, `persists`, `disappears` and `layers` (per layer: `source`, `name`,
`registry`, `good`, `bad`, `changed`, `columns`, `changes`, `status`).

## `shape timelapse`

<!-- example: 5 -->

Syntax reference. Replace the named arguments with your inputs.

```
shape timelapse REGISTRY NAME --column COL [--table T] [--since DATE] [--until DATE]
                [--window day|week|month] [-o OUT.json|OUT.html] [--format json|text]
                [--project FILE | --no-project] [diff threshold flags]
```


One **frame** per version, or per merged window with `--window` (versions of the same day, ISO
week or month, merged with `merge_profiles`: profiles need `--sketches`; a window of one version is
that version). A frame holds, from the stored profile and nothing else: `row_count`, `null_rate`,
`cardinality` (the distinct estimate), `quantiles` (p1, p5, p10, p25, p50, p75, p90, p95, p99),
`mean`, `std` and `top_values` (up to five `{value, share}`, for a category column; a continuous
column's stored value counts are single values and are not shown). A merged window holds no value
counts, so its `top_values` is `null`, and its `row_count` is the sum over its versions.

- **Gaps.** A version without the column is a frame with `"gap": true` and no statistics.
- **Change points.** A frame is a change point (`"change_point": true`, with the diff kinds in
  `changes`) when `shape diff` reports a change of that column against the last earlier frame that
  has the column. The thresholds are those of the `shape.yml` source and the threshold flags. The
  first frame, and the first frame after a gap, is not a change point. Day-to-day comparisons are
  as sensitive as `shape diff` is: a heavy-tailed column can show `range_change` between two
  ordinary days; raise `min_severity` for that column (`--column-threshold COL:min_severity=high`
  or the source's `columns:` in `shape.yml`) to quiet it.
- **Share-safe versions** contribute what their safe form holds (rows, null rate, distinct count,
  quantiles, mean, standard deviation, category weights); nothing is read from data. They are not
  compared, so their `change_point` is `null` and the result says so in `notes`. A share-safe
  version cannot be merged into a window.
- **`--table`** picks the table of a dataset profile; without it the column must be in one table.
- `--since` and `--until` are inclusive dates. A column in none of the versions is an error.

**Outputs.** With no `-o` the JSON (or, with `--format text`, the text) goes to standard output.
`-o OUT.json` writes the JSON, `-o OUT.html` the page; `-o` prints what it wrote. `--format text`
prints one line per statistic with a sparkline (`·` marks a gap), a `change` line marking the
change points, and the latest top values.

The **HTML** is one file: the frames as inline JSON, inline SVG drawn by an inline script, no
network request (it works offline, and a test checks that it holds no URL). It has a play control,
a slider over the frames, a chart of any statistic with the change points as dashed lines, the
quantiles of the current frame against the first, and its top values.

The **JSON** has `"format": "shape-timelapse"`, `"version": 1`, `name`, `column`, `table`,
`window`, `since`, `until`, `source`, `frames`, `change_points` (dates) and `notes`.
`shape.versions.load_timelapse(path)` reads one back and refuses a version newer than this Shape
understands.

## Limits

- Only profiles are searched: raw data that was never profiled, and git history of the data files,
  are out of scope.
- A share-safe profile cannot be diffed or checked against a contract (it holds no values to
  compare), so `bisect` and `bisect layers` stop with exit 2 and say to commit raw profiles with
  `--allow-raw`; `timelapse` shows its safe statistics.
- Bisect assumes the change persists; use `--verify-all` to check.
- A `ramp` change builds up slowly: the first version that crosses the thresholds is reported, and
  the step before it may show no change at all.

## How the specification is read

Where the wording of the specification allows more than one reading, this is the one built:

1. **Candidates.** A search range of *n* is *n* candidate versions after the good version (a
   history of one version has nothing to compare). The bound is `ceil(log2(n)) + 2` versions tested.
2. **`--coarse` cost.** It saves full per-version diffs, not profile reads: the result reports
   `cost.full_profile_tests` (fewer than a plain bisect's) and `cost.versions_read` (not fewer).
3. **Share-safe versions.** The message says to commit raw profiles with `--allow-raw`, and that
   `--contract` needs raw profiles as well; it does not offer `--contract` as a way round.
4. **`--json`** on `bisect` and `bisect layers` is a flag that prints the JSON on standard output
   (as `shape project validate --json` does); it does not take a file path as `shape diff --json`
   does.
5. **`bisect layers` is routed before argument parsing** (as `shape profile merge` is), so a
   registry directory literally named `layers` is written `./layers`.
6. **Change points** in `timelapse` are decided against the previous frame that has the column,
   with `shape diff` at the thresholds of the source; no threshold or default is changed here.
7. **Layers.** A layer after the origin *persists* when a column changed at the origin is also
   changed in it, and otherwise *disappears*; a layer where the change comes back persists.

## Example

[Run this example](#local-example-0).

## Example

[Run this example](#local-example-1).


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
# orders.gen.json is a generation schema; plan.json plants one event
cat plan.json
# {"start": "2026-03-01", "days": 31,
#  "events": [{"kind": "distribution", "table": "orders", "column": "amount",
#              "start": "2026-03-15", "scale": 1.3}]}

shape generate-drift orders.gen.json plan.json -o feed --rows orders=200 --format parquet
# Planted 1 events over 31 days from 2026-03-01
#   e1: distribution on orders.amount from 2026-03-15

for d in $(ls feed | grep '^2026'); do
  shape profile feed/$d/orders.parquet -o day.shape --sketches --capture full
  shape registry shapes/registry commit orders day.shape --allow-raw --business-date "$d"
done
shape registry shapes/registry tag orders day1 "$(python -c 'import json; print(json.loads(open("shapes/registry/logs/orders.jsonl").readline())["content_id"])')"
```

??? info "Output (exit 0)"

    ```text {.expected}
    {
      "start": "2026-03-01",
      "days": 3,
      "events": [
        {
          "kind": "distribution",
          "table": "orders",
          "column": "amount",
          "start": "2026-03-02",
          "scale": 1.3
        }
      ]
    }
    Planted 1 events over 3 days from 2026-03-01
      e1: distribution on orders.amount from 2026-03-02

    Written 7 files to feed/ (answer key: ground_truth.json)
    shape: warning: --capture full keeps real values in day.shape; do not commit or share it
    {"shape_content_id": "0cb35faaeb87e0b7baeb655c583d4222db902b30183fb72e8e185e1ebdb04bf7", "written": "day.shape"}
    shape: warning: the registry now holds real values from the data (a raw profile); keep it as private as the data
    {"content_id": "35ea68aedbec5f4bef5eeecec5a23a0403080ca69ed7f8d67b1528271a6e1555"}
    shape: warning: --capture full keeps real values in day.shape; do not commit or share it
    {"shape_content_id": "7d00f3f08859889f695af0d6c9af650933e25fd061c096776206e16b9dae3616", "written": "day.shape"}
    shape: warning: the registry now holds real values from the data (a raw profile); keep it as private as the data
    {"content_id": "59939ad1725d4dd126afa98fef92ceb02ee48cbf16701ffee7ff672ebe244598"}
    shape: warning: --capture full keeps real values in day.shape; do not commit or share it
    {"shape_content_id": "a098e95b2f043449865a8b2d84978c9d1319816d8180e64091106b3a097a700c", "written": "day.shape"}
    shape: warning: the registry now holds real values from the data (a raw profile); keep it as private as the data
    {"content_id": "13e204a587c294a1d030d629fac07614182ce04bfbff94e5d3356e3fda0ca2d1"}
    {"content_id": "35ea68aedbec5f4bef5eeecec5a23a0403080ca69ed7f8d67b1528271a6e1555"}
    ```

<a id="local-example-1"></a>

### Example 2

<!-- example: 1 -->

```bash {.runnable-reference}
shape bisect shapes/registry orders --good day1 --bad latest
```

??? info "Output (exit 0)"

    ```text {.expected}
    first bad version: 2026-03-02  content id 59939ad1725d
    last good version: 2026-03-01  content id 35ea68aedbec
    changes between them:
      amount: mean_shift 39.5772 -> 50.9789
      amount: distribution_shift {"p05": 23.8401, "p25": 34.16625, "p5... -> {"p05": 36.809, "p25": 43.53675, "p50...
      amount: distribution_change normal -> lognormal
      salary: new_categorical_values ["50001", "50002", "50003", "50005", ... -> ["50003", "50004", "50005", "50006", ...
      churned, salary -> is_gift: dependency_broken 0.965 -> null
      region, salary -> is_gift: dependency_broken 0.965 -> null
      is_gift ~ salary: association_shift 0.2292 -> 0
    tested 2 version(s) of 2 candidate(s) (at most 3); read 3 profile(s)
    ```

<a id="local-example-2"></a>

### Example 3

<!-- example: 2 -->

```bash {.runnable-reference}
shape timelapse shapes/registry orders --column amount --format text
shape timelapse shapes/registry orders --column amount --window week -o amount.html
```

??? info "Output (exit 0)"

    ```text {.expected}
    orders.amount  2026-03-01 .. 2026-03-03  3 frames, 1 change point(s)
    row_count   ▄▄▄  200 .. 200
    null_rate   ▄▄▄  0 .. 0
    cardinality ▁▁█  199 .. 200
    mean        ▁██  39.5772 .. 51.3736
    std         ▁▄█  9.26509 .. 10.2495
    p1          ▁█▆  15.7152 .. 24.9295
    p5          ▁██  23.8401 .. 35.3844
    p10         ▁██  28.4413 .. 38.9708
    p25         ▁██  34.1662 .. 44.806
    p50         ▁██  39.11 .. 51.4485
    p75         ▁██  45.742 .. 58.3462
    p90         ▁██  51.6936 .. 64.3103
    p95         ▁██  55.0078 .. 66.5616
    p99         ▁▇█  59.1229 .. 73.3921
    change       ^   at 2026-03-02
    {"change_points": ["2026-03-02"], "frames": 2, "written": "amount.html"}
    ```

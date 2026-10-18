# Duplicate detection and entity resolution

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" RESOLVE
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for RESOLVE
    ```


`shape.resolve` finds rows that describe the same real-world entity, collapses each group into one
golden record, and measures how well it did against **known** answers: the synthetic duplicates
generator records the true clusters, so precision, recall and F1 are exact, not estimated.

Everything is deterministic. Resolution draws no random numbers (the same table and config give
the same clusters); the generator draws from the seed you give it.

## The pipeline

1. **Blocking** cuts the n x n comparisons to the pairs that share a block. A `BlockRule` turns a
   column (or several, all of which must be present) into block keys:

   | Method | Key | Use for |
   |---|---|---|
   | `key` | the normalized value | an identifier or ZIP with no typos |
   | `prefix` (`size`, default 3) | the first characters of the normalized value | names with late typos |
   | `phonetic` | Soundex codes of the words, sorted | names spelled differently |
   | `ngram` (`size`, default 3) | every character n-gram | typos anywhere |

   Normalizing folds case and accents and drops punctuation. A row with a missing value has no
   key under that rule. Two rows are candidates when they share a key under **any** rule. A block
   larger than `max_block` (default 500) is skipped and counted in the stats
   (`skipped_blocks`, `skipped_records`), never truncated; for n-gram rules that is how very
   common grams drop out.
2. **Matching** scores each candidate pair as a weighted mean of per-field similarities, each 0
   to 1. A `FieldMatch` has a `kind`:

   | Kind | Similarity |
   |---|---|
   | `exact` | 1 when the normalized values are equal, else 0 |
   | `levenshtein` | 1 - edit distance / longer length |
   | `jaro_winkler` | Jaro-Winkler |
   | `ngram` | trigram Jaccard |
   | `tokens` | Levenshtein after sorting the words (word order ignored) |
   | `text` | the larger of `jaro_winkler` and `tokens` |
   | `phonetic` | 1 when the Soundex keys are equal, else 0 |
   | `numeric` | falls linearly from 1 (equal) to 0 (a difference of `tolerance`); `relative=True` makes the tolerance a share of the larger magnitude; no tolerance means equal only |
   | `date` | the same, with the tolerance in days |

   A field missing on either side is left out of the mean (its weight does not count); a pair
   with nothing comparable scores 0. A pair is a **match** when its score is at least `threshold`.
3. **Clustering**: `connected` (default) follows every match, so A-B and B-C put A, B and C
   together; `center` is the greedy center algorithm (best score first, a cluster grows only around
   its center), which does not chain. A cluster's label is its smallest row index.
4. **Golden records**: one row per cluster, chosen column by column by a `Survivorship` rule:
   `first`, `last`, `most_common`, `longest`, `shortest`, `min`, `max`, `most_recent` (largest
   value of the `by` column) or `priority` (the `by` column ranks first in `order`). A missing
   value never survives while a present one exists; ties go to the earliest row. The golden table
   has the original columns plus `_cluster_id` and `_cluster_size`; the lineage names the row each
   value came from.

```python
from shape.resolve import BlockRule, FieldMatch, ResolveConfig, Survivorship, resolve

config = ResolveConfig(
    blocks=[BlockRule("name", "ngram", 5), BlockRule("name", "phonetic")],
    fields=[
        FieldMatch("name", "text", weight=3.0),
        FieldMatch("city", "exact", weight=0.5),
        FieldMatch("income", "numeric", tolerance=0.05, relative=True),
        FieldMatch("born", "date", tolerance=5),
    ],
    threshold=0.85,
    survivorship={"name": Survivorship("longest")},
)
result = resolve(table, config)      # a pyarrow Table
result.labels                        # one cluster label per row
result.clusters()                    # the clusters of two or more rows
result.golden.table                  # one golden record per entity
```

A `ResolveConfig` round-trips through `to_dict()` / `from_dict()` (format
`shape-resolve-config`, version 1; a newer version is refused).

## Known clusters: synthetic duplicates and scoring

`make_duplicates(table, rate=, max_copies=, fuzz=, seed=, id_column=)` keeps every input row in
place and appends damaged copies of `round(rows x rate)` rows (1 to `max_copies` each). A cell of
a copy is damaged with probability `fuzz`: a typo (substitute, delete, insert, transpose), case or
spacing, swapped or abbreviated words, a small numeric jitter, a date moved by a few days.
`id_column` gives copies fresh integer keys. The result has `table`, `labels` (the original row of
each row), `clusters` (of two or more rows) and `true_pairs`. `write_truth` / `read_truth` store
the clusters (format `shape-duplicate-truth`, version 1).

`pair_metrics(result.labels, truth_labels)` counts every pair of rows in one cluster as a pair and
returns `precision`, `recall`, `f1`, `tp`, `fp` and `fn`; `result.blocking_recall(truth)` is the
share of true pairs that blocking kept as candidates (an upper bound for recall). The tests
fix thresholds on a seeded set (precision at least 0.95, recall at least 0.85, F1 at least 0.90,
blocking recall at least 0.95, with blocking pruning at least 85% of the pairs).

The chaos `duplicates` corruption records the same truth: see `duplicate_clusters` and the
`fuzz` option in [CHAOS.md](CHAOS.md).

## Command line

[Run this example](#local-example-1).


`--block` is `COL[+COL]:METHOD[:SIZE]`; `--match` is `COL:KIND[:WEIGHT[:TOLERANCE[:relative]]]`;
`--survive` is `COL=RULE[:BY[:A,B,...]]` (for example `--survive phone=most_recent:updated`).
`--config FILE` reads a `shape-resolve-config` JSON file; flags override it. Files are CSV, Parquet
or JSON Lines (by suffix). Outputs: `--golden` (the golden table), `--clusters` (format
`shape-resolve-clusters`, version 1), `--report` (format `shape-resolve-report`, version 1: the
config, counts, blocking stats and lineage), `--json` for the summary on standard output. Exit
code 0 on success, 2 for bad input.

## Limits

- Resolution is single-table (find duplicates inside one table); it does not link two tables.
- String distances are implemented with numpy (Levenshtein, vectorized over pairs) and pure Python
  (Jaro-Winkler, n-grams); there is no kernel twin, so both kernel modes behave identically.
- Cost is dominated by the number of candidate pairs; tighten blocking before raising `max_block`.


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-1"></a>

### Example 2

<!-- example: 1 -->

```bash {.runnable-reference}
# plant duplicates in a clean file; write the table and the true clusters
shape resolve synth people.csv -o people_dup.csv --truth truth.json \
    --rate 0.25 --fuzz 0.5 --seed 21 --id-column id

# resolve them and score against the truth
shape resolve run people_dup.csv \
    --block name:ngram:5 --block name:phonetic \
    --match name:text:3 --match city:exact:0.5 \
    --match income:numeric:1:0.05:relative --match born:date:1:5 \
    --threshold 0.85 --truth truth.json \
    --golden golden.csv --clusters clusters.json --report report.json
```

??? info "Output (exit 0)"

    ```text {.expected}
    Wrote 28 rows to people_dup.csv (5 duplicate clusters, 11 true pairs)
    28 rows -> 2 entities (26 merged, 89 matched pairs of 351 candidates)
    precision 0.026  recall 0.818  f1 0.050  (blocking recall 0.818)
    ```

# The report card: is this synthetic dataset fit to use?

`shape report-card` runs the checks that are spread over `shape fidelity`, `shape fidelity --tier`
and `shape verify --source`, adds a membership-inference test, and states, per section, what
passed, what failed and what was not run. It is local: it reads files and writes a file.

```bash
shape report-card real/ synthetic/ --config verify.json --holdout holdout/ \
    --manifest out/RUN_MANIFEST.json -o card.json -o card.md -o card.html
shape report-card real.parquet synthetic.parquet --json
shape report-card real/ synthetic/ --config verify.json --require utility,privacy   # for CI
```

`REAL` and `SYNTHETIC` are read as `shape fidelity` reads them: a file, or a directory with one file
per table (two single files are compared whatever they are called). `--holdout` has the layout of
`REAL`. The configuration is the `shape-verify-config` file of [VERIFY.md](VERIFY.md); the card has
no configuration format of its own.

| Flag | Meaning |
|---|---|
| `--config VERIFY.json` | the verify configuration (`classifications`, `memorization`, `utility`, `privacy`; other keys belong to `shape verify` and are ignored) |
| `--tiers 1,2` | the fidelity tiers to run (default `1,2`; `""` runs none; tier 3 is experimental and is not part of the card) |
| `--holdout HOLDOUT` | real rows that were not given to the generator: turns on the membership-inference test |
| `--manifest RUN_MANIFEST.json` | the run manifest of the generation: adds its reproducibility tuple and recorded dataset id |
| `-o CARD` | write the card; `.json`, `.md` or `.html` by extension; repeatable |
| `--json` | print the card as JSON instead of Markdown |
| `--require SECTION,...` | sections (`fidelity`, `utility`, `privacy`) that must have run |

Exit codes: `0` every section that ran passed; `1` a section failed, or a required one was not run;
`2` unusable input (a missing path, no table in common, a bad configuration, an unknown tier or
section, or `utility` required while scikit-learn is missing).

From Python:

```python
from shape.quality import report_card

card = report_card("real/", "synthetic/", config="verify.json", tiers=(1, 2),
                   holdout="holdout/", manifest="out/RUN_MANIFEST.json")
card.to_dict(); card.to_markdown(); card.to_html()
```

The inputs may also be mappings of table name to `pyarrow.Table`. `require=("utility",)` is the one
keyword beyond the arguments above. The renderers are also `shape.quality.reportcard.render_markdown`
and `render_html`, which take a card read with `shape.quality.load_report_card(path)`.

## The sections

A section is `pass` when none of its gates failed and at least one passed, `fail` when one failed,
and `not_run` (with a `reason`) when none ran. The card is `fail` when any section that ran failed.

### fidelity

* The base scores of `shape fidelity` (`shape.generation.report.compare_tables`), overall, per table
  and per column, with its pass marks (85 overall, 70 per table). Gates: `overall_score`,
  `table_score` per table, and `coverage` (a table or column missing from the synthetic data, or a
  reference with no rows, fails).
* **Tier 1** (`docs/FIDELITY_TIERS.md`): the adversarial test, a classifier that tries to tell real
  rows from synthetic rows. Gate `tier1_adversarial_auc` passes below 0.75 (the default of
  `shape fidelity --tier 1 --max-auc`); 0.5 means the two cannot be told apart. It needs scikit-learn
  (`pip install "sqllocks-shape[advanced]"`); without it the gate is `not_run`, not failed. The card
  also lists which columns have mixture fits and temporal profiles, never their numbers.
* **Tier 2**: format preservation, string similarity and cardinality, per table. Gate
  `tier2_pass_rate` passes at 1.0 (the default of `--min-pass-rate`).

The numbers are those of the commands, on the same inputs; a test compares them.

### utility

The utility gate of `shape verify --source` (train on synthetic, test on real; see
[VERIFY.md](VERIFY.md)). It runs when the configuration has a `utility` section and scikit-learn is
installed; gate `utility_retention`. Without a `utility` section, or without scikit-learn, the section
is `not_run` and its reason says which, with the `pip install` command. Retention measures how much
of the real model's score the synthetic model keeps for **one** target you chose; it says nothing
about other targets.

### privacy

* **Memorization** (`memorization` gate of `shape verify --source`): the exact-match rate, the
  closest-row distance (min, p05, median) and the indices of reproduced rows. It fails on a reproduced
  row in a column classified at or above `memorization.fail_at`. Without `classifications` it cannot
  fail, and a note says so.
* **Membership inference** (`membership_inference`, new). With `--holdout`, for up to
  `memorization.max_rows` (default 5,000) rows of `REAL` (members) and of the holdout (non-members) it
  takes the distance to the closest synthetic row (the standardised distance of the memorization
  gate, over the numeric columns shared by the three tables) and reports the **AUC** of telling
  members from non-members by that distance, with the p05 and median of both distance distributions.
  0.5 means the distance carries no signal; 1.0 means every member is closer to the synthetic data
  than every non-member. The test fails when the AUC is **above** `privacy.max_membership_auc`
  (default **0.6**, this test's own threshold; it is not a standard). The rows sampled depend on
  `privacy.seed` (default 0) when a side has more than `max_rows` rows; the result is deterministic
  for a seed. Without `--holdout` it is `not_run` (the reason says so); it is also `not_run` for a
  table with fewer than 10 rows on either side, with no common numeric column, or with no holdout
  table of that name.

```json
{"format": "shape-verify-config", "version": 1,
 "classifications": {"people.name": "CONFIDENTIAL"},
 "utility": {"table": "people", "target": "churned", "min_retention": 0.8},
 "memorization": {"max_rows": 5000},
 "privacy": {"max_membership_auc": 0.6, "seed": 0}}
```

## `--require`

`--require utility,privacy` turns what was not run into a failure, so CI cannot pass a card that
skipped something silently. A required section fails when it is `not_run`, **and also when one of its
tests was not run**: `--require privacy` without `--holdout` fails because the membership test did
not run, and `--require fidelity` fails when scikit-learn is missing and tier 1 could not run. The
reasons are in `overall_reasons`. The one exception is `utility` with scikit-learn missing, which is
exit `2`: the environment cannot do what was asked.

## The card

`{"format": "shape-report-card", "version": 1, ...}`:

| Key | Content |
|---|---|
| `shape_version` | the Shape that made the card |
| `inputs` | for `real`, `synthetic` and `holdout`: the `dataset_id` (`shape.repro.dataset_id`, [REPRODUCIBILITY.md](REPRODUCIBILITY.md)) and the row count per table; with `--manifest`, `manifest`: the run's `reproducibility` tuple, its recorded `dataset_id` and `dataset_id_matches` (`false` when the recorded id differs from the synthetic input's, `null` when none was recorded) |
| `sections` | `fidelity`, `utility`, `privacy`, each with `status`, `metrics`, `gates` (name, `status`, `table`, `value`, `threshold`, `relation`, `messages`, `reason`), `notes` and, for `not_run`, `reason` |
| `require`, `overall`, `overall_reasons` | the required sections, `pass` or `fail`, and why it failed |

A card has no timestamp, so the same inputs give the same card. A newer `version` is refused with a
message that says to upgrade; a verify configuration without a `privacy` section still loads.

No value from the data is written to any output: the card holds scores, counts, distances, table and
column names, and the **indices** of reproduced rows. A test builds a card from data with known values
and scans the JSON, Markdown and HTML for them. A dataset id that differs from the manifest's is
flagged, not failed: it can be a real difference or only a different file format.

## What a card does not say

A passing card is evidence, not a guarantee.

* A membership test with an AUC near 0.5 means this attack, with this distance, on these numeric
  columns, found no signal. A stronger attack, other features, or an attacker who knows more may do
  better. Text columns do not enter the distance (the memorization gate covers exact reproduction).
* The holdout must be real rows the generator never saw and that come from the same population as
  `REAL`. A holdout that overlaps `REAL`, or differs from it, makes the AUC meaningless.
* The AUC is estimated from the rows given; with few rows it is noisy, so give hundreds of rows per
  side at least.
* Fidelity is judged on marginals, pairs and one classifier; passing it does not mean every use of
  the data works. Utility is one model on one target.
* The card is not a differential-privacy statement and does not test attribute inference.
* The card is a local file. Nothing is published or attached anywhere.

# Privacy Model

Status: available.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" PRIVACY_MODEL
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for PRIVACY_MODEL
    ```


A safe capture is data minimisation, not anonymisation. Aggregates, rare categories, small
cohorts, geography, history and differencing can disclose sensitive facts. Apply cohort
thresholds, sensitivity controls and release review before sharing.

## Classification levels

One ordered taxonomy ranks every label in release, redaction and propagation:
`PUBLIC < INTERNAL < CONFIDENTIAL < SECRET < TOP_SECRET`. `SENSITIVE` and `PII` are accepted aliases of `CONFIDENTIAL`: they rank the same and keep the label you gave them. An unknown label is an error, never ignored.

## Safe-by-default capture

What Shape **writes or prints** for a profile is the safe capture unless you ask for real values:
`shape profile SRC -o OUT.shape`, its `--json` summary and `--html` report, `shape.save(profile,
path)` and `shape profile registry save`. The profile that `shape.profile()` returns in memory is
not changed, so everything that compares it (the equivalence verifiers, the kernel parity checks)
sees the same values as before.

[Run this example](#local-example-0).


```python
shape.save(profile, "orders.shape")                       # capture="safe"
shape.save(profile, "orders.shape", k=10, column_k={"status": 25},
           classifications={"ssn": "CONFIDENTIAL"})
shape.save(profile, "orders.full.shape", capture="full")
```

**A sensitive column keeps statistics and formats only.** A column is sensitive when its declared
classification (`--classify COLUMN=LEVEL`, `classifications=`) ranks at or above `CONFIDENTIAL` in
the ordered taxonomy above (`PII` and `SENSITIVE` rank as `CONFIDENTIAL`), or when the rules of the
safe profile make it pattern-only: a detected personal-data pattern, `pii_pattern_floor`, or nearly
one distinct value per row. It keeps the row and null counts, the distinct estimate, the type, the
length distribution, the detected pattern and its rates, the quantile fingerprint, the mean and
spread, and, for a number, `bounds` (`lo` and `hi`, the 1st and 99th percentiles). It keeps **no**
enum values, top values, placeholder values, raw minimum or maximum, or distribution fit that
names the minimum (uniform, exponential and lognormal fits; a normal fit is the mean and spread).

**Any other column keeps category values only if every released category has at least `k` rows**
(default 5; `--k N`; `--column-k COLUMN=N`, the same flags as `shape profile safe`): it must be an
enum by the profiler's rule, a category below `k` rows folds into `__OTHER__`, a `__OTHER__` that is
itself below `k` absorbs the smallest categories until it is not, and a column with fewer than `k`
rows releases nothing. Top-value entries, histogram bins (hour, weekday, month, year) and
placeholder counts follow the same rule: a cell is absent or stands for at least `k` rows. A text
column's minimum and maximum are values like the others, so they are kept only when they are
released categories; a number's or date's are kept unless the column is sensitive. The joint
analysis keeps its statistics but not the values: conditional tables, dependency violations and
reference-pair examples follow the cell rule, and anything about a sensitive column is dropped; a
workbook's findings lose their cell values and positions. `--column-k` and `--classify` name a
column (`name`, or `table.name` in a dataset) and fail on a name that is not there, so a typo never
leaves a column unprotected.

**The artifact records how it was made.** The `.shape` manifest carries `capture`
(`{"mode": "safe", "k": 5}`, or `{"mode": "full", "k": null}`) and, for a safe capture, the
`redaction_manifest` (per column: `sensitive`, `reason`, `k`, `suppressed` and the counts of
categories and cells withheld). A column that lost something lists it in its own `redacted` map
(surface to reason, `sensitive` or `below_k`), so a reader can tell "left out" from "absent". A
profile written before this existed has no `capture` and reads as full. Redaction is idempotent and
one way: a safe capture can be saved again, never as full.

**`--capture full` is the explicit choice.** It keeps today's content, stamps `capture.mode:
"full"` and prints once on standard error: `shape: warning: --capture full keeps real values in
OUT; do not commit or share it`. `shape profile validate --safe` reports it as a finding (rule
`full-capture`, exit 1), as it does for `unsafe`, and the default `.shape` passes it (exit 0).
`shape registry ... commit` takes a safe capture without `--allow-raw` and still refuses a full one.

**Multivariate entries.** The joint analysis's `multivariate_outliers`, `pca`, `cohorts` and
`copula` (`docs/JOINT.md`) name columns in lists and hold category values (the copula's
categories, a cohort's most common value): a safe capture keeps none of them, nor
`categorical_columns`, and records `"joint": "removed: ..."` in its `redaction_manifest`.

**Sketch state and merged profiles.** The mergeable sketch state of `shape profile --sketches`
(`docs/PROFILE_MERGE.md`) holds sampled and top values, so only a full capture keeps it:
`--sketches` needs `--capture full`, and a safe capture of a profile that has it leaves it out and
records `"sketches": "removed: ..."` in its `redaction_manifest`. A merged profile keeps the
distinct counts and quantiles of its merged sketches in a safe capture, not their top values
(`"merge": "removed: ..."`). `shape profile merge` writes a safe capture unless `--capture full`
is given.

**Readers.** `shape diff` compares what both sides hold; a comparison that needs something a safe
capture left out (the range of a sensitive column, a distribution fit, folded category counts, the
placeholder values) is listed under `not_evaluable` and never counted as drift. `shape check`
reports a rule that needs it as `not evaluable: COLUMN was captured safe (statistics and formats
only); re-profile with --capture full`, lists it under `not_evaluable`, and exits 2 (1 when a rule
is also violated). `shape generate --from` and `shape plan` generate a sensitive column from its
pattern and length distribution and mark what was left out `approximate` in the plan.

This is data minimisation, not anonymisation: a safe capture still holds statistics of the data,
and the limits listed under "Minimum cohort and small cells" apply to it.

**The vault: the alternative to `--capture full`.** When generation needs the exact values (the
real status codes, categories or region names with their frequencies), `--capture full` puts them
in the clear in the file you commit. A **value vault** keeps them in a separate file, encrypted with
AES-256-GCM under a key you hold, and the `.shape` stays the safe, committable capture:

[Run this example](#local-example-2).


Only what the safe capture withheld is vaulted, column by column, as the policy says (`categories`,
`extremes`, `all`, `none`; by column, by classification or by default). Without `--vault`, nothing
changes. Data generated with a vault holds real values: treat it like the source data. See
`docs/VAULT.md`.

## The safe profile

A full profile holds value-bearing evidence (exact minimum and maximum, every enum value, top value counts). `shape profile safe PROFILE.shape -o SAFE.json` writes the form that is meant to be shared. It has no field that can hold a raw extreme or a value list, and:

- numeric extremes become winsorized `bounds` taken from the quantile fingerprint (p1/p99, or p0.5/p99.5 when widened);
- a category with fewer rows than the minimum cohort `k` (default 5, `--k N`; 11 with `--sensitive`; `--column-k COLUMN=N` per column) is folded into one `__OTHER__` bucket;
- literal category labels are kept only for a low-entropy set of letters-only labels; numeric and date categories become a coarse histogram, and anything else gets hashed keys;
- a column whose detected pattern is personal data (email, SSN, card, phone, IP, IBAN, postal code), or that has nearly one distinct value per row, keeps its pattern and length distribution only. Detection is by value and is defence in depth, not a completeness guarantee; the profile also records, per text column, the share of values that are wholly an SSN, email, IP or IBAN, or the detected pattern (`pattern_rates`) and the share that contain an SSN, email address or card number inside text (`pattern_contains_rates`), measured on every distinct value (up to 50,000 per column, then an evenly spaced sample of them), and a column where any of those families (SSN, email, card, IP, IBAN) reaches 0.1% (`pii_pattern_floor`) is pattern-only too, however few the values are;
- a column with fewer non-null rows than its `k` releases no value statistic: `mean`, `std`, `quantiles`, `bounds` and `distribution_params` are null (with one row the mean would be the value);
- a `redaction_manifest` records, per column, what was actually suppressed.

`--unsafe-full-fidelity` is the one opt-out: it turns the controls off and stamps the artifact `unsafe`.

`shape profile validate --safe ARTIFACT [--json]` scans a serialized artifact (never the data) and exits 0 only when it is proven clean, 1 on any finding, 2 on a usage error. It denies by shape rather than name: lists of more than two raw strings, numeric min/max pairs outside the length aggregates, personal-data patterns anywhere in a value, a missing table `row_count`, a missing safe-profile marker, or an `unsafe` stamp. It is a leak scanner, not approval to share.

## Minimum cohort and small cells

Every released cell must be absent or stand for at least the minimum cohort `k` (default 5). A *cell* is a category of a value count or enum, a bin of a histogram, or a top-value entry. `release_for`, `suppress_shape` and the safe profile enforce this on all of them:

- a category below `k` rows folds into one `__OTHER__` bucket; if that bucket is itself below `k`, the smallest surviving categories join it until it reaches `k`, and a column with fewer than `k` rows releases nothing;
- a histogram bin below `k` is zeroed and the proportions are renormalized; a histogram with no releasable bin is dropped;
- proportions are turned back into counts with a conservative lower bound (they are stored to six decimals), over the column's non-null rows, so the released cell is never smaller than `k` rows however the proportion was rounded;
- a value list that carries no counts cannot be checked, so it is removed;
- `release_for` applies the rule inside the profile's `joint` block when no column is classified above the target (#650): a row of a conditional table, or a dependency violation, that stands for fewer than `k` rows is withheld, and inside one the values below `k` rows fold into `__OTHER__` (conditional shares are stored to four decimals and read with a lower bound for that rounding); `cohorts`, `copula` and `multivariate_outliers`, whose cells are not checked one by one, are withheld. When any column is above the target, the whole `joint` block is withheld;
- a safe-profile column whose non-null rows are fewer than its `k` releases no value statistic (#395): `mean`, `std`, `quantiles`, `bounds` and `distribution_params` are `null`, since the mean of one row is the value. `--unsafe-full-fidelity` keeps them, and `shape profile validate --safe` reports such a statistic as a finding (rule `small-cohort-statistic`).

This limits what a single release shows. It does not stop differencing across releases (see `differencing_risk`) and is not a sharing guarantee.


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
shape profile orders.csv -o orders.shape                      # --capture safe is the default
shape profile orders.csv -o orders.shape --k 10 --column-k status=25
shape profile orders.csv -o orders.shape --classify ssn=CONFIDENTIAL --classify salary=SECRET
shape profile orders.csv -o orders.full.shape --capture full  # real values: do not commit or share
```

??? info "Output (exit 0)"

    ```text {.expected}
    /workspace/shape/src/shape/profile/reference/sources.py:430: UserWarning: orders.csv: read as integers although they look like identifiers: 'salary' (every value has 5 digits). A number loses its leading zeros; if these are identifiers, keep them as text with --string-columns salary.
      kind, table = _read_files([path], threads, csv)
    {"shape_content_id": "df628a613f3894333c23e9393dce2090bcd2e4d85b2680aa12e7ba5fb8fd1cb5", "written": "orders.shape"}
    /workspace/shape/src/shape/profile/reference/sources.py:430: UserWarning: orders.csv: read as integers although they look like identifiers: 'salary' (every value has 5 digits). A number loses its leading zeros; if these are identifiers, keep them as text with --string-columns salary.
      kind, table = _read_files([path], threads, csv)
    {"shape_content_id": "bca50b3fa97121a44d1c6395f800a57600c80d2ed40fa9348e4f16a0b7284a17", "written": "orders.shape"}
    /workspace/shape/src/shape/profile/reference/sources.py:430: UserWarning: orders.csv: read as integers although they look like identifiers: 'salary' (every value has 5 digits). A number loses its leading zeros; if these are identifiers, keep them as text with --string-columns salary.
      kind, table = _read_files([path], threads, csv)
    {"shape_content_id": "df628a613f3894333c23e9393dce2090bcd2e4d85b2680aa12e7ba5fb8fd1cb5", "written": "orders.shape"}
    /workspace/shape/src/shape/profile/reference/sources.py:430: UserWarning: orders.csv: read as integers although they look like identifiers: 'salary' (every value has 5 digits). A number loses its leading zeros; if these are identifiers, keep them as text with --string-columns salary.
      kind, table = _read_files([path], threads, csv)
    shape: warning: --capture full keeps real values in orders.full.shape; do not commit or share it
    {"shape_content_id": "0320083af8a193c72f4e302aa1bd6484eee33ddba696c5a5e3543088e9ffca2d", "written": "orders.full.shape"}
    ```

<a id="local-example-2"></a>

### Example 3

<!-- example: 2 -->

```bash {.runnable-reference}
shape profile orders.csv -o orders.shape --vault orders.shapevault \
      --vault-policy policy.json --kek file://KEK.key
shape generate --from orders.shape --vault orders.shapevault --kek file://KEK.key -f csv -o out
```

??? info "Output (exit 0)"

    ```text {.expected}
    /workspace/shape/src/shape/profile/reference/sources.py:430: UserWarning: orders.csv: read as integers although they look like identifiers: 'salary' (every value has 5 digits). A number loses its leading zeros; if these are identifiers, keep them as text with --string-columns salary.
      kind, table = _read_files([path], threads, csv)
    {"shape_content_id": "df628a613f3894333c23e9393dce2090bcd2e4d85b2680aa12e7ba5fb8fd1cb5", "vault": "orders.shapevault", "written": "orders.shape"}
    shape: note: orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    profile fit: 96 approximate, 97 not modelled, 265 preserved, 2 vault (see `shape plan`)
    shape: warning: output generated with orders.shapevault contains real values from the vault; treat it like the source data
    Wrote 1 csv files to out: 100 rows in 1 tables (0.08s)
    ```

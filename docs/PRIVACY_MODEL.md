# Privacy Model
A Shape is not automatically anonymous. Aggregates, rare categories, small cohorts, geography, history and differencing can disclose sensitive facts. Production policy should apply cohort thresholds, rare-value suppression, sensitivity propagation, history access control, release review and—where needed—formal privacy mechanisms. Shape 1.0 does not claim that k-anonymity alone provides anonymization.

## Classification levels

One ordered taxonomy ranks every label in release, redaction and propagation:
`PUBLIC < INTERNAL < CONFIDENTIAL < SECRET < TOP_SECRET`. `SENSITIVE` and `PII` are accepted aliases of `CONFIDENTIAL`: they rank the same and keep the label you gave them. An unknown label is an error, never ignored.

## Safe-by-default capture

What Shape **writes or prints** for a profile is the safe capture unless you ask for real values:
`shape profile SRC -o OUT.shape`, its `--json` summary and `--html` report, `shape.save(profile,
path)` and `shape profile registry save`. The profile that `shape.profile()` returns in memory is
not changed, so everything that compares it (the equivalence verifiers, the kernel parity checks)
sees the same values as before.

```bash
shape profile orders.csv -o orders.shape                      # --capture safe is the default
shape profile orders.csv -o orders.shape --k 10 --column-k status=25
shape profile orders.csv -o orders.shape --classify ssn=CONFIDENTIAL --classify salary=SECRET
shape profile orders.csv -o orders.full.shape --capture full  # real values: do not commit or share
```

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

**Readers.** `shape diff` compares what both sides hold; a comparison that needs something a safe
capture left out (the range of a sensitive column, a distribution fit, folded category counts, the
placeholder values) is listed under `not_evaluable` and never counted as drift. `shape check`
reports a rule that needs it as `not evaluable: COLUMN was captured safe (statistics and formats
only); re-profile with --capture full`, lists it under `not_evaluable`, and exits 2 (1 when a rule
is also violated). `shape generate --from` and `shape plan` generate a sensitive column from its
pattern and length distribution and mark what was left out `approximate` in the plan.

This is data minimisation, not anonymisation: a safe capture still holds statistics of the data,
and the limits listed under "Minimum cohort and small cells" apply to it.

## The safe profile

A full profile holds value-bearing evidence (exact minimum and maximum, every enum value, top value counts). `shape profile safe PROFILE.shape -o SAFE.json` writes the form that is meant to be shared. It has no field that can hold a raw extreme or a value list, and:

- numeric extremes become winsorized `bounds` taken from the quantile fingerprint (p1/p99, or p0.5/p99.5 when widened);
- a category with fewer rows than the minimum cohort `k` (default 5, `--k N`; 11 with `--sensitive`; `--column-k COLUMN=N` per column) is folded into one `__OTHER__` bucket;
- literal category labels are kept only for a low-entropy set of letters-only labels; numeric and date categories become a coarse histogram, and anything else gets hashed keys;
- a column whose detected pattern is personal data (email, SSN, card, phone, IP, IBAN, postal code), or that has nearly one distinct value per row, keeps its pattern and length distribution only. Detection is by value and is defence in depth, not a completeness guarantee; the profile also records, per text column, the share of values that are wholly an SSN, email, IP or IBAN, or the detected pattern (`pattern_rates`) and the share that contain an SSN, email address or card number inside text (`pattern_contains_rates`), measured on every distinct value (up to 50,000 per column, then an evenly spaced sample of them), and a column where any of those families (SSN, email, card, IP, IBAN) reaches 0.1% (`pii_pattern_floor`) is pattern-only too, however few the values are;
- a `redaction_manifest` records, per column, what was actually suppressed.

`--unsafe-full-fidelity` is the one opt-out: it turns the controls off and stamps the artifact `unsafe`.

`shape profile validate --safe ARTIFACT [--json]` scans a serialized artifact (never the data) and exits 0 only when it is proven clean, 1 on any finding, 2 on a usage error. It denies by shape rather than name: lists of more than two raw strings, numeric min/max pairs outside the length aggregates, personal-data patterns anywhere in a value, a missing table `row_count`, a missing safe-profile marker, or an `unsafe` stamp. It is a leak scanner, not proof of anonymity.

## Minimum cohort and small cells

Every released cell must be absent or stand for at least the minimum cohort `k` (default 5). A *cell* is a category of a value count or enum, a bin of a histogram, or a top-value entry. `release_for`, `suppress_shape` and the safe profile enforce this on all of them:

- a category below `k` rows folds into one `__OTHER__` bucket; if that bucket is itself below `k`, the smallest surviving categories join it until it reaches `k`, and a column with fewer than `k` rows releases nothing;
- a histogram bin below `k` is zeroed and the proportions are renormalized; a histogram with no releasable bin is dropped;
- proportions are turned back into counts with a conservative lower bound (they are stored to six decimals), over the column's non-null rows, so the released cell is never smaller than `k` rows however the proportion was rounded;
- a value list that carries no counts cannot be checked, so it is removed.

This limits what a single release shows. It does not stop differencing across releases (see `differencing_risk`) and is not an anonymity guarantee.

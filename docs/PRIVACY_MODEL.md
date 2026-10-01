# Privacy Model
A Shape is not automatically anonymous. Aggregates, rare categories, small cohorts, geography, history and differencing can disclose sensitive facts. Production policy should apply cohort thresholds, rare-value suppression, sensitivity propagation, history access control, release review and—where needed—formal privacy mechanisms. Shape 1.0 does not claim that k-anonymity alone provides anonymization.

## Classification levels

One ordered taxonomy ranks every label in release, redaction and propagation:
`PUBLIC < INTERNAL < CONFIDENTIAL < SECRET < TOP_SECRET`. `SENSITIVE` and `PII` are accepted aliases of `CONFIDENTIAL`: they rank the same and keep the label you gave them. An unknown label is an error, never ignored.

## The safe profile

A full profile holds value-bearing evidence (exact minimum and maximum, every enum value, top value counts). `shape profile safe PROFILE.shape -o SAFE.json` writes the form that is meant to be shared. It has no field that can hold a raw extreme or a value list, and:

- numeric extremes become winsorized `bounds` taken from the quantile fingerprint (p1/p99, or p0.5/p99.5 when widened);
- a category with fewer rows than the minimum cohort `k` (default 5, `--k N`; 11 with `--sensitive`; `--column-k COLUMN=N` per column) is folded into one `__OTHER__` bucket;
- literal category labels are kept only for a low-entropy set of letters-only labels; numeric and date categories become a coarse histogram, and anything else gets hashed keys;
- a column whose detected pattern is personal data (email, SSN, card, phone, IP, IBAN, postal code), or that has nearly one distinct value per row, keeps its pattern and length distribution only. Detection is by value and is defence in depth, not a completeness guarantee;
- a `redaction_manifest` records, per column, what was actually suppressed.

`--unsafe-full-fidelity` is the one opt-out: it turns the controls off and stamps the artifact `unsafe`.

`shape profile validate --safe ARTIFACT [--json]` scans a serialized artifact (never the data) and exits 0 only when it is proven clean, 1 on any finding, 2 on a usage error. It denies by shape rather than name: lists of more than two raw strings, numeric min/max pairs outside the length aggregates, personal-data patterns anywhere in a value, a missing table `row_count`, a missing safe-profile marker, or an `unsafe` stamp. It is a leak scanner, not proof of anonymity.

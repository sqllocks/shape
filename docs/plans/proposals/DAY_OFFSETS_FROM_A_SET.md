# Proposal: day offsets drawn from a set of lengths

Status: proposed. Not built.

## Problem

`derived` with `add_days` draws its offset from a `uniform`, `log_normal` or `normal`
distribution (`shape.builtins.strategies.derived.sample_days`). A length that takes a few
fixed values cannot be expressed. The `insurance` domain's policies all run exactly 365 days
(`expiration_date` is `effective_date` plus `"days": 365`), while real books mix six-month and
annual terms, and some longer ones. A continuous distribution of terms would be less realistic
than the fixed one, so the domain keeps 365 days and says so in its documentation.

The same gap affects billing cycles (28, 30 or 31 days), loan terms in months and subscription
lengths.

## Proposal

A `choice` distribution for `add_days`, weighted like `weighted_enum`:

```json
{"strategy": "derived", "source": "effective_date", "rule": "add_days",
 "params": {"distribution": "choice", "values": {"182": 0.3, "365": 0.65, "730": 0.05}}}
```

* `values` maps a whole number of days to a non-negative weight (the keys are text, as in
  `weighted_enum`); the weights have a positive sum.
* The draw uses `kernel_ops.alias_table` and two words per row from the column's stream, so it
  stays row addressed, and the same seed gives the same offsets in both kernels.
* `min` and `max` do not apply; a key that is not a whole number is a `StrategyError` naming the
  column.

A second, larger option would let `add_days` read its offset from another column of the row
(`"days_column": "term_days"`), so the term can be a `weighted_enum` column that the output keeps.

## Tests it needs

* The empirical shares of each length match the weights (a chi-squared bound at a fixed seed).
* Identical output for any chunk size and random access, and in the pure-Python kernel.
* `GenSchema.validate()` warns about an empty `values`, negative weights and non-integer keys;
  `spec_keys` lists `values` under `derived` parameters.
* The insurance domain: `expiration_date - effective_date` takes only the listed lengths, and a
  claim's incident still falls inside the term.

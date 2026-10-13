# Proposal: a category from a date's age

Status: proposed. Not built.

## Problem

A generated column cannot depend on how old a date of its own row is. The `insurance` domain's
`claim.status` (`Open`, `Under Review`, `Approved`, `Denied`, `Closed`) is drawn with fixed weights,
so a claim filed last week is as likely to be `Closed` as one filed four years ago, and an old
claim is as likely to be `Open` as a new one. Other domains have the same shape: a ticket's
state, a shipment's status, a loan's delinquency bucket.

`conditional_table` can key a draw on another column of the row, but that column has to be a
category. Nothing turns a date into one: `formula` reads only numeric and boolean columns,
`derived` copies a date or adds days to it, and `conditional` compares a column with one value.

## Proposal

A `derived` rule `age_bucket` that labels a date by its distance from a reference date:

```json
{"strategy": "derived", "source": "filed_date", "rule": "age_bucket",
 "as_of": "model.date_range.end",
 "buckets": [[30, "0-30d"], [180, "31-180d"], [null, "over 180d"]]}
```

* `as_of` is a date, or `model.date_range.end` (the schema's own range, as `temporal`'s
  `range_ref` reads it).
* `buckets` lists upper bounds in days, in increasing order, each with its label; `null` is the
  last, open bucket. A date after `as_of` falls in the first bucket; a null date gives null.
* The column is `string`. The value of a row depends only on the row's source value and the
  spec, so the strategy stays row addressed and is the same for any chunking.

`claim.status` then becomes a `conditional_table` on that column, for example most claims under
30 days `Open` or `Under Review`, and most over 180 days `Closed`, `Approved` or `Denied`. The
bucket column is a real output column; a later proposal could let a strategy produce it as an
internal column that is not written.

## Tests it needs

* Every bucket boundary (a date exactly 30 days before `as_of` is in `0-30d`), a date after
  `as_of`, a null date, `date32` and `timestamp` sources of each unit.
* Identical output for any chunk size and random access (copy
  `test_identical_for_any_chunking_and_random_access`).
* `GenSchema.validate()` errors for unordered bounds, a missing open bucket and an unknown
  `as_of`; `spec_keys` lists the new keys.
* The insurance domain: claim status by bucket, at every scale preset.

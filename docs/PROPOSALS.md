# Proposals and decision files

Status: experimental.

## Run the local examples

The examples below use disposable local files from this checkout. The [example test environment](contributing/EXAMPLES.md) sets `SHAPE_DOCS_REPO`, installs core and plugins from source, and prepares local services. Run each page in its own empty directory, in the order shown. Complete output appears beneath each command. Elapsed times, temporary paths, job IDs and generated signing keys vary; the harness validates those runtime fields and compares the remaining output exactly.

<!-- example: 999 -->

```bash {.runnable-reference}
python "$SHAPE_DOCS_REPO/scripts/docs_example_setup.py" PROPOSALS
```

??? info "Output (exit 0)"

    ```text {.expected}
    Prepared local fixtures for PROPOSALS
    ```


Some facts about a dataset cannot be settled by a profile alone: whether a column is a foreign key
to another table, whether it holds personal data, what it means, and which rules a dataset should
obey. Shape **proposes** these with the evidence and a confidence, you **accept, reject or defer**
each one, and the decision file keeps your answer, so the question does not come back after the
next re-profile. Accepted rules become a contract that `shape check` reads (see
[Rules](#rules)).

[Run this example](#local-example-0).


## Commands

`shape proposals propose PROFILE.shape -d DECISIONS.json` finds proposals and merges them into the
decision file (created if missing).

* `--data DIR` or `--data NAME=PATH` (repeatable): the profiled data. It adds value evidence
  (containment for relationships, matching values for PII). Without it Shape proposes from the
  profile alone and is less confident.
* `--kinds relationship,pii,semantic,rule,type`, `--min-confidence C` (default 0.5). The default
  is `relationship,pii,semantic`; `rule` and `type` are proposed only when asked for. Give several
  `PROFILE.shape` files only with `--kinds rule` (exit 2 otherwise).
* `--auto-accept THRESHOLD`: accept undecided proposals at or above the confidence, recorded as
  actor `auto-accept`. **Off unless given**, and it never overrides a decision.

`shape proposals list -d DECISIONS.json [--status pending|accepted|rejected|deferred|stale]
[--kind K] [--min-confidence C] [--json]` lists proposals, most confident first.

`shape proposals decide -d DECISIONS.json PROPOSAL_ID accept|reject|defer [--actor WHO] [--note TEXT]`
records a decision. The actor defaults to `$SHAPE_ACTOR`, then the login name. Deciding again
replaces the earlier decision.

`shape proposals contract -d DECISIONS.json -o CONTRACT.json [--merge EXISTING.json]` writes the
accepted rule proposals as a contract v1 (see [Rules](#rules)).

`shape generate --from PROFILE.shape --decisions DECISIONS.json` and
`shape plan PROFILE.shape --decisions DECISIONS.json` apply the decisions (see below). Exit codes
are the usual ones: 2 for a bad or too-new decision file or an unknown proposal.

## Python

```python
import shape
from shape.proposals import DecisionFile, propose, apply_decisions
from shape.generation.fit import fit_schema

profile = shape.load("shop.shape")
decisions = DecisionFile.read("decisions.json")          # or DecisionFile.empty()
decisions.update(propose(profile, "data/"))             # merge; rejected ones are skipped
decisions.decide("pii:customers.email", "rejected", actor="ana", note="test data")
decisions.write("decisions.json")
fit_schema(profile, decisions=decisions)                 # generation keeps accepted relationships
```

`propose_relationships`, `propose_pii`, `propose_semantics`, `propose_rules` and `propose_types` run one kind;
`DecisionFile.list(status=, kind=, min_confidence=)`, `.accepted(kind)` and `.entries()` read it.
`propose(profile, kinds=["rule"])` and `propose_rules(profiles, ...)` take one profile or a list of
them; `DecisionFile.to_contract(merge=None)` builds the contract.

## What is proposed

A proposal is `KIND:SUBJECT` (its id), a `claim`, a `confidence` from 0 to 1 and the `evidence`.

**Relationships** (`relationship:orders.customer_id->customers.customer_id`), single-column
candidate foreign keys. The evidence is *name* (the column is named after the parent key, or after
the parent table, singular or plural, with a key suffix), *containment* (the share of the child's
non-null rows whose value exists in the parent; needs the data), *type*, *range* (child minimum and
maximum inside the parent's) and *cardinality* (the parent column is unique, checked with the
same key discovery as `shape key`; the child has no more distinct values than the parent).
Confidence is 0.40 name + 0.35 containment + 0.10 range + 0.15 cardinality; without data the
other three are rescaled and the result is capped at 0.85. A column with no name evidence is
proposed only with the data, with every row contained, and at least 20 distinct values. A column
with under half of its rows contained is not proposed. The profiler's own detected foreign keys are
included, marked `profiler_detected`. Composite keys and one-to-one tables are not inferred.

**PII** (`pii:customers.email`): the column name (`email`, `phone`, `ssn`, `full_name`, ...) and,
with the data, the share of sampled string values that match a detector (email, SSN, phone, IPv4).

**Semantics** (`semantic:customers.city`): what a column means, from its name and the pattern the
profile found (email, uuid).

**Types** (`type:orders.zip`): columns whose type needs a second look, from the findings of
`shape types` (`shape.types_report`) and every identifier suspect. One proposal per column; the
claim is `{"type": ..., "table": ..., "column": ...}`: the narrower type the values of a declared
`string` or `float` column all parse as, the candidate type of a column that is mostly one type
(`low_confidence`), or `string` for an integer column that may hold identifiers (a ZIP, an NPI).
The evidence holds the `finding`, `declared`, `inferred`, the `confidence` and the `parse_shares`
and, for a suspect, the identifier rule's reason and the digits' `width`. Confidence is 0.9 times
the share of values that parse as the proposed type, 0.8 when the identifier rule itself would keep
the column as text, 0.6 for a suspect by name and 0.55 by width. A profile written before type
inference was recorded has no evidence and proposes nothing.

## Rules

Writing a contract by hand is slow, so most datasets have none or a thin one. A profile already
holds the evidence for most rules, so Shape proposes them, you decide, and the accepted ones become
a contract v1 file that `shape check` reads.

[Run this example](#local-example-2).


`shape proposals propose PROFILE.shape [PROFILE.shape ...] -d DECISIONS.json --kinds rule` (and
`propose_rules(profiles, ...)` in `shape.proposals`) proposes contract v1 rules. A dataset
profile gets one contract per table under `tables`; a single-table profile gets flat rules. A
dataset profile has the joint analysis (needed for `fd`) only when profiled with `--joint`; a
single table has it by default.

### What is proposed

Every proposal is `rule:TABLE.COLUMN.RULE`, `rule:TABLE.row_count` or
`rule:TABLE.fd.DETERMINANT->DEPENDENT`. Its `claim` is the exact contract v1 fragment (for a
dataset `{"tables": {"orders": {"columns": {"status": {"dtype": "string"}}}}}`, for a single table
`{"columns": {"status": {"dtype": "string"}}}`), so it can be pasted into a contract as it is. Its
`evidence` holds the profile figures it rests on and `profiles`, the number of profiles it held on.

| Rule | Claim | Proposed when | Evidence besides `rows`, `nulls`, `distinct` |
|---|---|---|---|
| `dtype` | `dtype` | every profile has the same type | |
| `nullable` | `nullable: false` | no profile has a null | |
| `unique` | `unique: true` | every profile has it unique, for an integer or text column | |
| `range` | `min`, `max` | a number or a date (no time zone; a CSV date column, read as text, too); not a unique column | `observed_min`, `observed_max` |
| `pattern` | `pattern` | every profile found the same pattern (`email`, `uuid`, ...) | `match_rate` (lowest) |
| `allowed_values` | `allowed_values` | every profile lists all of at most 20 distinct values; the claim is their union | |
| `no_placeholder` | `no_placeholder: true` | no profile found a placeholder (`N/A`, `99999`, ...) in a text or numeric column | |
| `row_count` | `row_count.min`, `.max` | the table has at least 30 rows in all | `rows`, `observed_min`, `observed_max` (of the per-profile counts) |
| `fd` | `fd: [{determinant, dependent, min_confidence: 0.99}]` | every profile lists the dependency at 0.99 or more, its determinant is not unique and its dependent not constant | `dependency_confidence` (lowest), `groups` |
| `reference_pair` | `reference_pair: [{columns, reference, min_match_rate}]` | every profile measured the pair and it matched 0.9 or more; `min_match_rate` is the lowest rate less 0.01, rounded down to two places | `match_rate`, `reference` |

* **Support.** No column rule is proposed from fewer than **30 non-null values** (summed over the
  profiles), and no row-count band from fewer than 30 rows.
* **Holds on every profile.** A rule is proposed only when it holds on every profile given. After
  deriving a rule Shape runs `shape check` of that one rule on every profile and drops it if it
  fails, so a rule proposed from N profiles passes on each of them. A table or column that is
  missing from one profile gets no rules.
* **Margins.** A `range` is widened by **10% of its span** on each side (integers: rounded up to a
  whole number; floats: outward to four decimals; dates: by that many days, at least one day,
  to whole seconds for a timestamp), but a range that was not negative does not cross zero (and
  one that was not positive does not either). A `row_count` band is the smallest observed count
  less **50%** and the largest plus 50% for one profile, **25%** for several.
* **Not proposed.** A `range` for a unique column (a key moves with every new row), `unique` for a
  float or a date, `allowed_values` for a unique, boolean or date column.
* **Ties and order.** The same profiles give the same proposals in the same order; proposals are
  sorted by confidence, then id.

### Confidence

A deterministic number from 0 to 1, in `shape.proposals.rules` (`CAPS`, `strength`,
`confidence`):

```
confidence = cap(rule) * strength(support, profiles) * factor
strength   = 1 - (10 / (support + 10)) / profiles
```

`support` is the number of non-null values the rule rests on (rows for `row_count` and `fd`, the
rows that were compared for `reference_pair`, and for `allowed_values` the count of the **rarest**
value, so a set with a value seen once is not trusted), summed over the profiles. `factor` is a
measured rate: the pattern's match rate, the lowest dependency confidence or the lowest reference
match rate; 1 for the others. The cap is how strongly such evidence implies a rule:

| Rule | cap | Rule | cap |
|---|---|---|---|
| `dtype` | 0.99 | `allowed_values` | 0.85 |
| `pattern`, `fd`, `reference_pair` | 0.95 | `range` | 0.80 |
| `nullable`, `unique` | 0.90 | `no_placeholder`, `row_count` | 0.70 |

So confidence rises with the support and with the number of profiles (for example a week of daily
captures): a `dtype` rule from 100 rows is 0.90, from 1,000 rows 0.98, and from seven profiles of
100 rows each 0.99. The default `--min-confidence 0.5` keeps a rule from exactly 30 values.

### Sensitive columns

A column classified as personal data gets **value-free rules only**: `dtype`, `nullable`,
`unique`, `pattern` and `no_placeholder`. `range` and `allowed_values` carry real values (the
smallest date of birth, the list of phone numbers), so they are not proposed, and no real value of
such a column reaches the decision file. A column is personal data when

* the decision file holds an **accepted** `pii` decision for it, or
* a `pii` proposal for it, not rejected, is at or above **0.5** (the default threshold), whether in
  the file or found by this run (by name, and by value with `--data`),

and a **rejected** `pii` decision means it is not. With several profiles the personal-data check
reads the first profile (and `--data`, which is the data of that profile). A rule already accepted whose column becomes
sensitive is no longer found and goes stale (below).

### Several profiles

Give a week of daily captures to learn what holds across them:

[Run this example](#local-example-4).


Single-table profiles are matched by position and take the first one's table name. A table
profiled in only some of them gets no rules.

### Decisions on rules, and `stale`

Re-run `propose` after a re-profile and the file keeps up:

* a **pending** rule that the new evidence no longer supports is withdrawn;
* a **rejected** rule is never proposed again;
* an **accepted** rule keeps its accepted claim for as long as it still holds on every profile
  given (the new range of a new day does not change what you accepted; the evidence and
  confidence are refreshed);
* an accepted rule whose evidence **no longer holds** (the key now repeats, nulls appeared, the
  pattern changed, the column became sensitive) is marked **`stale`**: it keeps its claim and
  evidence, the decision records that it was accepted by whom and when, and
  `shape proposals list --status stale` shows it. It is never silently dropped and is not written
  to a contract. Accept it again with `decide` once you have looked.

Auto-accept stays off unless `--auto-accept THRESHOLD` is given.

### Contract from decisions

`shape proposals contract -d DECISIONS.json -o CONTRACT.json [--merge EXISTING.json]` (and
`DecisionFile.to_contract(merge=None)`, `shape.proposals.dump_contract`) writes a contract v1 from
the accepted, non-stale rule proposals with sorted keys, so the same decisions give the same bytes.
With nothing accepted it exits 2.

`--merge` adds the rules to a copy of an existing contract and keeps everything else in it (`drift`,
other tables, `required_columns`). The existing contract is first read as `shape check` reads it: a
newer `version`, another `format`, an unknown key or rule is refused (exit 2, nothing written) with
`shape check`'s message. A rule that agrees with the one already there is no conflict. An
accepted rule that differs from a rule already there is refused, exit 2, naming both:

```
shape: error: rule:orders.amount.range conflicts with orders.amount.min in EXISTING.json
```

The contract written from the proposals of a profile passes `shape check` on that profile. On data
that later breaks a rule it fails, naming the column: duplicated keys fail `unique`, nulls in a
never-null column fail `nullable`, a column delivered as the wrong type fails `dtype` or, when the
profile still reads it as a number, its `range`. (`tests/proposals/test_rules_contract.py` runs
this on the shipped retail domain with `shape chaos` corruptions.)

Not proposed: drift thresholds or policies, and rules without a profile.

## The decision file

Text JSON, safe to commit. `format` is `shape-decisions` and `version` an integer: **1**, or **2**
for a file that holds a `rule` proposal (a file with none is still written as version 1, byte for
byte as before; a file whose last rule proposal is withdrawn goes back to version 1). Version 2
adds the `rule` kind and the decision status `stale`. Both versions are read; a version 1 file may
not hold either. A `type` proposal does not change the version (a file without `type` proposals
reads and writes as before). Keys are
sorted, proposals and decisions are in id order, times are UTC ISO 8601 (`2026-10-03T12:00:00Z`)
and confidences have four decimals, so unchanged findings change no line and one decision changes
only its own lines. A newer `version` than Shape knows (3 or more) is refused with a message that
says so. The JSON Schemas are `src/shape/schemas/decisions-v1.schema.json` and
`decisions-v2.schema.json`.

```json
{
  "format": "shape-decisions",
  "version": 1,
  "proposals": [{"id": "pii:customers.email", "kind": "pii", "subject": "customers.email",
                 "claim": {"pii": "email"}, "confidence": 0.98, "evidence": {"...": "..."},
                 "proposed_at": "2026-10-03T12:00:00Z"}],
  "decisions": [{"proposal": "pii:customers.email", "status": "rejected", "actor": "ana",
                 "at": "2026-10-04T08:30:00Z", "note": "test addresses only"}]
}
```

## How decisions are applied

* **Rejected**: never proposed again, whatever the new evidence. `apply_decisions` also removes
  a rejected relationship the profiler had detected, so generation does not use it.
* **Accepted**: kept. An accepted relationship is added to the profile (relationships, the child
  column's foreign-key flags), so `fit_schema(..., decisions=)` and `shape generate --from
  ... --decisions` generate it as a foreign key. Accepted PII and semantic decisions are read with
  `DecisionFile.accepted("pii")`; they do not change the profile.
* **Accepted type**: the column takes the proposed type. `apply_decisions` (so `shape generate
  --from ... --decisions` and `shape plan --decisions`) gives an integer column proposed as
  `string` its digits as text (a fixed width and leading zeros are kept), relabels `datetime` as
  `date` and `integer` as `float`, and turns a text column into numbers from the frequencies of its
  values when the profile lists every distinct value; when it cannot (it lacks the column, or the
  values), it fails with a message that says to re-profile. `shape profile --decisions
  DECISIONS.json` reads the accepted `type` decisions as `--types` for the table they name
  (`TABLE` is the profile's table name), and an explicit `--types` file wins for a column both
  name; `shape.profile(..., decisions=)` is the same from Python.
* **Deferred**: stays in the file, shown as `deferred`, applied as nothing.
* **Stale** (rules only): an accepted rule the evidence no longer supports; see
  [Decisions on rules](#decisions-on-rules-and-stale). Only a re-profile sets it; you cannot
  `decide` it, but you can accept or reject the rule again.
* **Pending** proposals that a later run no longer finds are withdrawn; decided ones stay.
* A re-profile keeps a proposal's first `proposed_at` and updates its evidence and confidence.
* Auto-accept is off by default.


## Complete local run

Run these commands in order after preparing the fixtures above.

<a id="local-example-0"></a>

### Example 1

<!-- example: 0 -->

```bash {.runnable-reference}
shape profile data/ --dataset -o shop.shape
shape proposals propose shop.shape --data data/ -d decisions.json
shape proposals list -d decisions.json --status pending --min-confidence 0.9
shape proposals decide -d decisions.json "relationship:orders.customer_id->customers.customer_id" \
    accept --actor ana --note "orders belong to customers"
shape generate --from shop.shape --decisions decisions.json --format csv -o out/
```

??? info "Output (exit 0)"

    ```text {.expected}
    {"shape_content_id": "54ecf06034ce4a15fdffbbb2c26aab78bf3036a0ad7dee874b907b226d896e1d", "written": "shop.shape"}
    shape: note: shop.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {
      "written": "decisions.json",
      "proposals": 29,
      "added": 29,
      "updated": 0,
      "withdrawn": 0,
      "skipped_rejected": [],
      "auto_accepted": [],
      "stale": []
    }
    1.00  pending   pii:customers.email
    0.95  pending   semantic:customers.email
    0.90  pending   relationship:address.customer_id->customers.customer_id
    0.90  pending   relationship:order_line.order_id->orders.order_id
    0.90  pending   relationship:order_line.product_id->product.product_id
    0.90  pending   relationship:order_line.promotion_id->promotion.promotion_id
    0.90  pending   relationship:orders.customer_id->customers.customer_id
    0.90  pending   relationship:orders.promotion_id->promotion.promotion_id
    0.90  pending   relationship:orders.store_id->store.store_id
    0.90  pending   relationship:product.category_id->product_category.category_id
    0.90  pending   relationship:return.order_id->orders.order_id
    {
      "proposal": "relationship:orders.customer_id->customers.customer_id",
      "status": "accepted",
      "actor": "ana",
      "at": "2026-10-09T17:01:54Z",
      "note": "orders belong to customers"
    }
    shape: note: shop.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    profile fit: 452 approximate, 199 not modelled, 615 preserved (see `shape plan`)
    Wrote 9 csv files to out/: 21,750 rows in 9 tables (0.12s)
    ```

<a id="local-example-2"></a>

### Example 3

<!-- example: 2 -->

```bash {.runnable-reference}
shape profile orders.csv -o orders.shape
shape proposals propose orders.shape -d decisions.json --kinds pii,rule
shape proposals list -d decisions.json --kind rule --min-confidence 0.85
shape proposals decide -d decisions.json rule:orders.order_id.unique accept --actor ana --note "key"
shape proposals decide -d decisions.json rule:orders.status.allowed_values accept --actor ana
shape proposals contract -d decisions.json -o contract.json
shape check orders.shape contract.json          # exit 0: the contract passes on that profile
```

??? info "Output (exit 0)"

    ```text {.expected}
    <checkout>/src/shape/profile/reference/sources.py:430: UserWarning: orders.csv: read as integers although they look like identifiers: 'salary' (every value has 5 digits). A number loses its leading zeros; if these are identifiers, keep them as text with --string-columns salary.
      kind, table = _read_files([path], threads, csv)
    {"shape_content_id": "df628a613f3894333c23e9393dce2090bcd2e4d85b2680aa12e7ba5fb8fd1cb5", "written": "orders.shape"}
    shape: note: orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {
      "written": "decisions.json",
      "proposals": 102,
      "added": 102,
      "updated": 0,
      "withdrawn": 4,
      "skipped_rejected": [],
      "auto_accepted": [],
      "stale": []
    }
    0.90  pending   rule:orders.amount.dtype
    0.90  pending   rule:orders.churned.dtype
    0.90  pending   rule:orders.city.dtype
    0.90  pending   rule:orders.country.dtype
    0.90  pending   rule:orders.customer_email.dtype
    0.90  pending   rule:orders.customer_id.dtype
    0.90  pending   rule:orders.discount_code.dtype
    0.90  pending   rule:orders.iban.dtype
    0.90  pending   rule:orders.is_gift.dtype
    0.90  pending   rule:orders.notes.dtype
    0.90  pending   rule:orders.order_date.dtype
    0.90  pending   rule:orders.order_id.dtype
    0.90  pending   rule:orders.order_total.dtype
    0.90  pending   rule:orders.placed_at.dtype
    0.90  pending   rule:orders.region.dtype
    0.90  pending   rule:orders.salary.dtype
    0.90  pending   rule:orders.shipped_at.dtype
    0.90  pending   rule:orders.ssn.dtype
    0.90  pending   rule:orders.state.dtype
    0.90  pending   rule:orders.status.dtype
    0.90  pending   rule:orders.tier.dtype
    0.90  pending   rule:orders.token.dtype
    0.90  pending   rule:orders.zip.dtype
    0.86  pending   rule:orders.customer_email.pattern
    0.86  pending   rule:orders.fd.customer_email->churned
    0.86  pending   rule:orders.fd.customer_email->customer_id
    0.86  pending   rule:orders.fd.customer_email->is_gift
    0.86  pending   rule:orders.fd.customer_email->region
    0.86  pending   rule:orders.fd.customer_id->churned
    0.86  pending   rule:orders.fd.customer_id->customer_email
    0.86  pending   rule:orders.fd.customer_id->is_gift
    0.86  pending   rule:orders.fd.customer_id->region
    0.86  pending   rule:orders.fd.order_date->churned
    0.86  pending   rule:orders.fd.order_date->placed_at
    0.86  pending   rule:orders.fd.order_date->region
    0.86  pending   rule:orders.fd.order_date->shipped_at
    0.86  pending   rule:orders.fd.placed_at->churned
    0.86  pending   rule:orders.fd.placed_at->order_date
    0.86  pending   rule:orders.fd.placed_at->region
    0.86  pending   rule:orders.fd.placed_at->shipped_at
    0.86  pending   rule:orders.fd.shipped_at->churned
    0.86  pending   rule:orders.fd.shipped_at->order_date
    0.86  pending   rule:orders.fd.shipped_at->placed_at
    0.86  pending   rule:orders.fd.shipped_at->region
    0.86  pending   rule:orders.iban.pattern
    0.86  pending   rule:orders.ssn.pattern
    0.86  pending   rule:orders.token.pattern
    0.86  pending   rule:orders.zip.pattern
    shape: error: no proposal 'rule:orders.order_id.unique' in the decision file
    {
      "proposal": "rule:orders.status.allowed_values",
      "status": "accepted",
      "actor": "ana",
      "at": "2026-10-09T17:02:00Z",
      "note": ""
    }
    {
      "written": "contract.json",
      "rules": 1
    }
    shape: note: orders.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {"passed": true, "violations": []}
    ```

<a id="local-example-4"></a>

### Example 5

<!-- example: 4 -->

```bash {.runnable-reference}
shape proposals propose mon.shape tue.shape wed.shape -d decisions.json --kinds rule
```

??? info "Output (exit 0)"

    ```text {.expected}
    shape: note: mon.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: note: tue.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    shape: note: wed.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
    {
      "written": "decisions.json",
      "proposals": 107,
      "added": 8,
      "updated": 99,
      "withdrawn": 1,
      "skipped_rejected": [],
      "auto_accepted": [],
      "stale": []
    }
    ```

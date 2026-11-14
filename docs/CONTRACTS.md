# Contracts: `shape check`

Status: available.

[Check a daily order file](DAILY_ORDERS.md) for a runnable regex and relative-date tutorial.


A contract is a JSON object (format `shape.contracts.v1`) that `shape check PROFILE.shape
CONTRACT.json` and `shape.check(profile, contract)` test a profile against. Every key is optional;
an empty contract passes. A contract against a dataset profile (several tables) has a `tables`
object that maps each table name to a contract of its own.

| Key | Meaning |
|---|---|
| `row_count` | `{"min": N, "max": N}` |
| `columns` | per column: `dtype`, `nullable`, `unique`, `max_null_rate`, `pattern`, `regex`, `allowed_values`, `min`, `max`, `distribution`, `min_true_rate`, `max_true_rate`, `no_placeholder`, `valid_as`, `strength` |
| `required_columns`, `allow_extra_columns` | which columns must be there, and whether others may be |
| `fd`, `implies`, `reference_pair`, `max_implausible_rate` | rules on the joint analysis (`docs/JOINT.md`) |
| `tables` | maps each table name in a dataset profile to its own contract |
| `timeseries`, `reconcile` | time-series and reconciliation rules; `shape check --data DATA` supplies the data ([Verification](VERIFY.md)) |
| `as_of_timezone` | IANA timezone for relative date bounds (UTC by default) |
| `drift` | the drift policy; `shape check` ignores it (`docs/DRIFT.md`) |

Exit codes of `shape check`: **0** when no rule that counts as failing is broken, **1** when one is,
**2** when the contract itself is malformed (an unknown key, rule or strength).

The contract keys and column-rule list above are checked against the v1 registries in the contract tests.
`valid_as` checks a code validator's measured valid rate; see [Reference packs](REFERENCE_PACKS.md#column-validators).
`shape validate CONTRACT.json` validates v1 contracts without a profile: structural errors exit 1;
unknown rules and malformed rule values exit 2, as in `shape check` and `shape check --dry-run`.
The dry run validates the contract without evaluating its rules or writing reports.

## Rule strength

A rule may say how much it matters with the optional key `strength`:

| Strength | A broken rule |
|---|---|
| `hard` (the default) | fails the check (exit 1) |
| `soft` | is reported as a warning, with `"strength": "soft"`, and does not fail |
| `learned` | a rule inferred from data rather than written by a person: behaves like `soft`, unless `--enforce-learned` is given, which makes it `hard` |

`shape check --strict` makes every broken rule fail, whatever its strength. `--strict` wins over
`--enforce-learned`. A contract with no `strength` anywhere behaves as before this key existed.

Where `strength` goes:

- **A column's rule object**: a word for all of the column's rules, or an object from rule name to
  strength (a rule the object does not name is `hard`).
- **`row_count`**, and each **`fd`**, **`implies`** and **`reference_pair`** entry: a word.

Anywhere else it is an error (exit 2): at the top of the contract, inside `no_placeholder`, inside
the `if` or `then` of an `implies`, on `max_implausible_rate` or `required_columns`, as an object on
`row_count`. An unknown strength, or an object that names a rule the column does not have, is a
contract error that names the column and the rule.

```json
{
  "row_count": {"min": 10000, "strength": "soft"},
  "columns": {
    "customer_id": {"dtype": "integer", "unique": true, "nullable": false},
    "email": {"max_null_rate": 0.08, "strength": "soft"},
    "age": {"min": 0, "max": 120, "strength": {"min": "hard", "max": "soft"}},
    "status": {"allowed_values": ["new", "active", "closed"], "strength": "learned"}
  },
  "fd": [{"determinant": "zip", "dependent": "city", "min_confidence": 0.99, "strength": "soft"}]
}
```

Here a broken `customer_id` rule or a negative `age` fails the check, a row count under 10,000, an
email null rate over 8%, an age over 120 or a broken `zip -> city` dependency is a warning, and a
status outside the three values is a warning until `--enforce-learned` is given.

### The result

`shape check --json` and `CheckResult.to_dict()` give `{"passed", "violations", "warnings"}`:
`violations` hold the rules that fail the check, `warnings` the broken ones that do not, and every
entry of either carries its `strength`:

```json
{
  "passed": true,
  "violations": [],
  "warnings": [
    {"column": "email", "rule": "max_null_rate", "expected": 0.08, "observed": 0.11, "strength": "soft"}
  ]
}
```

`passed` is true when `violations` is empty, and the exit code is 1 only otherwise. With `--strict`
every broken rule is in `violations` (keeping its own `strength`) and `warnings` is empty. A
contract that declares no `strength` anywhere gives exactly the result it always did, with no
`strength` on the violations and no `warnings` key. A column missing from the profile takes the
one-word strength of its rule object (`hard` when the strength is an object); a true-rate violation
takes the harder of the strengths of `min_true_rate` and `max_true_rate`; `required_columns`,
`allow_extra_columns`, `max_implausible_rate` and a missing table are `hard`.

From Python: `shape.check(profile, contract, strict=False, enforce_learned=False)`.

Writing `strength: learned` from `shape proposals` is not done yet: `learned` is for contracts
produced by tools or edited by hand.

## User-defined regular expressions

The column rule `regex` tests whole non-null values. It is independent of `pattern`,
which still compares a detected label. Both rules can appear on the same column.
The short form `"regex": "[A-Z]{3}"` requires every tested value to match. The object
form accepts `expression`, `min_match_rate` (default 1.0), and optional `strength`:

```json
{
  "columns": {
    "product_code": {"regex": {"expression": "[A-Z]{3}-[0-9]{4}", "min_match_rate": 0.9}},
    "load_date": {"regex": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"}
  }
}
```

For example, 90 matching codes out of 100 non-null values pass a minimum rate of
0.9; 89 fail. Nulls are ignored; use `nullable` or `max_null_rate` to govern them.
An empty or all-null column passes this rule. Expressions are wrapped as
`^(?:expression)$`, so `\d{3}` rejects `"1234"`.

Matching uses PyArrow's linear-time RE2 engine. RE2 has no backreferences or
lookaround. Invalid RE2 syntax, empty expressions, unknown object keys, and rates
that are not finite numbers in [0, 1] are contract errors before evaluation.
Values use the profiler's text spelling, including ISO dates and timestamps and
canonical numeric text. Nested values and nonfinite numbers without an unambiguous
profiler text conversion raise a named `ContractError` on the data path.

Without `--data`, the rule tests `value_counts_ext`. Complete stored values give an
exact result: every distinct value must be present, with counts covering all
non-null rows. Profiles store six-place proportions; the checker reconstructs
counts only when they are uniquely determined and sum to the non-null count.
Truncated strings and ambiguous rounded counts cannot prove completeness.
A known nonmatching stored value is a definite violation at the default rate of 1.
Otherwise an incomplete, folded or redacted profile is blind: `not_evaluable`
records the rule and `passed` is false. Pass `--data` or re-profile with
`capture="full"` to supply the missing evidence.

`shape check PROFILE.shape CONTRACT.json --data DATA.parquet` tests every non-null
value exactly, replacing the profile result for `regex`. From Python, pass
`data={"table": arrow_table}`. Dataset contracts put rules in each table's `columns`.

Violations record the expression and rate in `expected`. `observed` contains
`match_rate`, `non_matching_count`, `tested_count`, and up to 20 distinct
`non_matching_values`. The value list is empty for sensitive or redacted columns,
or whenever a safe capture would withhold their values, including on the data path.
Column strength and rule-local regex strength support `hard`, `soft` and `learned`;
rule-local strength takes precedence. Strict and learned enforcement work as usual.

## Relative date and timestamp bounds

`min` and `max` accept `{"as_of_offset_days": N}` for integer calendar-day offsets.
Constant bounds keep their existing behavior. A relative bound requires an explicit
batch date: `shape check PROFILE.shape CONTRACT.json --as-of 2026-10-07`, or
`shape.check(profile, contract, as_of="2026-10-07")`. Python also accepts a `date`.
There is no implicit current date; the result records the supplied date as `as_of`.

For a daily order file, this contract accepts only orders dated on the batch day:

```json
{
  "as_of_timezone": "UTC",
  "columns": {
    "order_date": {
      "min": {"as_of_offset_days": 0},
      "max": {"as_of_offset_days": 0}
    }
  }
}
```

A synthetic October 7 batch with 1,974 October 7 orders and 104 orders dated
September 27–October 6 fails with the September 27 minimum. The same contract
accepts a clean October 6 batch when checked with `--as-of 2026-10-06`.
To allow orders one day before the batch, change the minimum offset to `-1`.
For an October 7 batch, October 6 at midnight then passes, while October 5 at
23:59:59 fails.

For dates, bounds compare calendar dates inclusively. For timestamps, `min` is
midnight at the start of the resolved day, while `max` accepts every instant
before the following midnight. The expected maximum timestamp records that
exclusive next-midnight boundary. Days use UTC by default. Set `as_of_timezone`
to an IANA zone such as `America/New_York` to use local calendar days, including
DST transitions. A dataset may set the zone at the root and override it per table.
Aware timestamps are compared as instants; naive timestamps are read in that zone.
Timezone information is preserved.

The profile path parses the stored minimum or maximum as an ISO date or timestamp.
Unparseable or non-temporal observations produce the usual not-comparable violation.
`--data` uses exact column extremes. Redacted extremes remain blind, preserving the
existing safe-capture rule. Invalid offsets, unknown bound keys, invalid zones and
missing or malformed as-of dates raise `ContractError`. Strength applies separately
to `min` and `max`.

A relative-bound violation records the original offset, `as_of`, and resolved ISO
`bound` in `expected`, with the observed extreme in `observed`. Relative bounds
remain gaps in every static emitter; emitting never freezes them to a date.

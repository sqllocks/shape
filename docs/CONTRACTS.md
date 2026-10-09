# Contracts: `shape check`

Status: available.


A contract is a JSON object (format `shape.contracts.v1`) that `shape check PROFILE.shape
CONTRACT.json` and `shape.check(profile, contract)` test a profile against. Every key is optional;
an empty contract passes. A contract against a dataset profile (several tables) has a `tables`
object that maps each table name to a contract of its own.

| Key | Meaning |
|---|---|
| `row_count` | `{"min": N, "max": N}` |
| `columns` | per column: `dtype`, `nullable`, `unique`, `max_null_rate`, `pattern`, `allowed_values`, `min`, `max`, `distribution`, `min_true_rate`, `max_true_rate`, `no_placeholder` |
| `required_columns`, `allow_extra_columns` | which columns must be there, and whether others may be |
| `fd`, `implies`, `reference_pair`, `max_implausible_rate` | rules on the joint analysis (`docs/JOINT.md`) |
| `drift` | the drift policy; `shape check` ignores it (`docs/DRIFT.md`) |

Exit codes of `shape check`: **0** when no rule that counts as failing is broken, **1** when one is,
**2** when the contract itself is malformed (an unknown key, rule or strength).

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

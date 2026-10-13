# Proposal: reconciliation extensions

Status: proposed 2026-10-13 (Owner approved proposing it). Not a work package yet: the lead decides whether it becomes one in `COMPLETION_PLAN.md` section 13, files its issue, and the issue's "Wanted" list (section 4 below, ready to paste) becomes its specification. Facts about the current code were read from `main` at `304efc6`.

## 1. The problem

Reconciliation answers "did every record arrive, and arrive right?" after a load, a replication or a migration. Counts and totals per partition catch lost or doubled rows. A migration also needs two more things: the two sides name their columns differently (`CUST_NO` in the source, `customer_id` in the target), and a row can arrive with the wrong value in one field while every count and total still match (a status translated wrongly, a date shifted by a time zone, a name truncated).

Shape's reconciliation (W3-10) covers counts and aggregates well and stops there:

- **What it compares.** `src/shape/quality/reconcile.py` checks the total row count (`reconcile.count`), counts per `partition_by` group (`reconcile.partition`), keys on one side only and counts per key (`reconcile.key`, `reconcile.key_count`), and aggregates per key or over the table (`reconcile.aggregate`), with a tolerance `abs + rel * max(|s|, |t|)` (`reconcile.py:1` to `11`; rule keys at `reconcile.py:27` to `36`).
- **Key and partition names must match.** The key and partition columns are looked up under the same name on both sides (`reconcile.py:401` to `404`). Only aggregates can name a different target column (`target_column`, `reconcile.py:407`; `docs/VERIFY.md:262`).
- **No value-by-value comparison.** Rows matched by key are compared through aggregates only; no finding says "row with key 1042 has `status` `A` in the source and `C` in the target".
- **No command of its own.** Reconciliation runs as the `reconcile` rules of a verify configuration (`docs/VERIFY.md:231`), through `shape verify DATA --config CONFIG.json`, or in a contract through `shape check` (`src/shape/contracts/v1.py:42`). There is no `shape reconcile` command; a migration test has to write a verify configuration around the data it is not verifying.
- **The report is not versioned.** `VerifyReport.to_json` (`src/shape/quality/verify.py:255`) writes `shape_version`, `run_at`, paths, `passed`, `row_counts` and the gates, with no `format` or `version`, so a CI step that parses it has no contract.

What exists and is reused: the side loader (paths, globs, folders, Delta, Arrow tables, `{"table": name}`; `reconcile.py:57` to `62`), the tolerance rule, the finding shape (`reconcile.py:142` to `153`), the sample limit (`_SAMPLES = 20`, `reconcile.py:38`) and the `ReconciliationGate` (`reconcile.py:551`).

## 2. Proposed design

### a. Different names on each side

A rule gains `target_key` and `target_partition_by`, lists of the same length as `key` and `partition_by`, matched by position. Without them the target uses the source names, as today. Optionally, `key_map` translates keys that were replaced (re-keyed) on the way, from a two-column table such as the key map the declarative mapping transform proposal writes:

```json
{"name": "contacts after migration",
 "source": "legacy/CUST.parquet", "target": "crm/contact.parquet",
 "key": ["CUST_NO"], "target_key": ["contact_id"],
 "key_map": {"path": "_mapping/keys/customer.parquet", "from": "source_key", "to": "target_key"},
 "partition_by": ["REGION"], "target_partition_by": ["region_code"],
 "compare": {"columns": [
    {"column": "STAT_CD", "target_column": "status", "lookup": {"path": "lookups/status.csv", "from": "legacy_code", "to": "target_code"}},
    {"column": "CRED_LIM", "target_column": "credit_limit", "tolerance": {"abs": 0.005}},
    {"column": "OPENED", "target_column": "opened_at", "tolerance": {"seconds": 0}},
    {"column": "EMAIL", "target_column": "email", "normalize": {"trim": true, "case": "insensitive"}}],
   "max_mismatch_rows": 100000}}
```

### b. Value-by-value comparison

`compare` matches rows by key (after `key_map`) and compares listed column pairs, or every pair of same-named columns with `"columns": "all"`.

- Keys must be unique on both sides for `compare`; duplicates give a `reconcile.key_not_unique` finding with samples, and those keys are left out of the comparison.
- Per pair: numbers compare with the rule's tolerance or the pair's own; text compares exactly after optional `normalize` (`trim`, `case`, `collapse_spaces`); dates and timestamps compare with a `seconds` tolerance; a `lookup` translates source values first (expected target value), so a code translation is checked against its own table.
- Null handling: null against null is equal; null against a value is a mismatch of its own kind (`null_in_source`, `null_in_target`), counted separately from a different value.
- Findings: one `reconcile.value` finding per column pair with the mismatch count, the count of each kind, and up to `_SAMPLES` samples of `(key, source value, target value)`.
- With `--mismatches FILE` (or `"mismatches": "path"` in the rule), every mismatch row up to `max_mismatch_rows` is written to Parquet: key, column pair, kind, both values. The cap is reported when hit, never silently.

### c. The `shape reconcile` command

```
shape reconcile SOURCE TARGET [--key K ...] [--target-key K ...] [--partition-by C ...]
      [--target-partition-by C ...] [--compare all|C[=TC] ...] [--tolerance-abs X] [--tolerance-rel Y]
      [--rules RULES.json] [-o REPORT.json|.md] [--mismatches FILE.parquet] [--json]
shape reconcile --rules RULES.json [-o REPORT.json]      # several rules, sides inside the file
```

- The flags build one rule; `--rules` reads a list of rules (the `reconcile` list of a verify configuration, with its own `format` `shape-reconcile-rules` and `version`).
- Exit codes: 0 every rule passed, 1 a finding, 2 a usage error or an unreadable side (the documented exit code convention, `docs/EXIT_CODES.md`).
- **Report** `format` `shape-reconcile-report`, integer `version`: Shape version, time, each rule as run (with defaults filled in), each side's path, row count and content hash, every finding, and a summary (rows matched, only in source, only in target, mismatched per column). The Markdown report renders the same content.

## 3. Compatibility

- **Verify configuration.** `validate_reconcile_rules` refuses unknown keys (`reconcile.py:84`, `_only_keys`), so a configuration with `target_key`, `target_partition_by`, `key_map` or `compare` fails loudly on an older reader. Proposed: the `verify-config` kind (`src/shape/compat.py:179`) goes to version 2 when any of them is used, and version 1 configurations keep loading unchanged.
- **Findings.** New rule names (`reconcile.value`, `reconcile.key_not_unique`) are additive. Existing rules give the same findings for the same input.
- **Reports.** The reconcile report is new and versioned from the start. The verify report stays as it is in this package; adding `format` and `version` to it is open question 2.
- **Generation** is untouched: no generator version or seeded output changes.

## 4. Wanted (ready to paste into the issue)

Phase 1, rule extensions:
1. `target_key` and `target_partition_by` (by position, same length as `key` and `partition_by`, an error otherwise); defaults unchanged.
2. `key_map` (`path`, `from`, `to`) applied to source keys before matching; an unmapped source key is reported under `reconcile.key` as only in source, with the reason.
3. `compare` with `columns` (pairs with `target_column`, `tolerance`, `normalize`, `lookup`) or `"all"`, the null kinds of section 2b, `reconcile.value` findings with counts and samples, and `reconcile.key_not_unique`.
4. Mismatch rows to Parquet with `max_mismatch_rows`, the cap reported when hit.
5. `verify-config` version 2 for rules that use items 1 to 4; version 1 unchanged (frozen fixture).

Phase 2, the command:
6. `shape reconcile` with the flags of section 2c and `--rules` (`shape-reconcile-rules`, version 1).
7. The versioned JSON report `shape-reconcile-report` and a Markdown rendering; exit codes 0, 1, 2.
8. Documentation: a "Reconciliation" section in `docs/VERIFY.md` updated for the new keys, `docs/CLI.md` for the command, and a migration example that reconciles a mapped dataset with its source using the key map.

Acceptance beyond section 13's common items: with planted mismatches (a changed value, a null on one side, a duplicated key, a missing row) in otherwise equal tables, each is reported under its kind with the right count and key, and nothing else is reported; renamed key and partition columns give the same findings as the same tables with matching names; a numeric difference within tolerance is not reported and one just outside is; existing verify configurations give identical findings before and after the change.

## 5. Open questions for the lead
1. Whether `compare` must scale beyond memory (a sorted merge over files) in phase 1, or whole tables in memory are enough for now, with the limit documented.
2. Whether the verify report should get `format` and `version` in this package, since `shape reconcile` makes the gap visible.
3. Whether `shape reconcile` should accept a profile on one side (compare a dataset against the profile it should match), or stay data against data.

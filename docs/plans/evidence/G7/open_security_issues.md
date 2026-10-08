# G7: open high-severity and security issues (sqllocks/shape, 2026-10-04)

## Label query (as asked)

GitHub MCP `list_issues`, state OPEN, filtered by label:

| Label | Open issues |
|---|---|
| `high` | 0 (the label does not exist in the repo: `get_label` returns "not found") |
| `critical` | 0 (label does not exist) |
| `security` | 0 (label does not exist) |

The repo has 735 open issues and none of them has a severity label. The check "no high-severity
findings are open" therefore cannot be answered from labels; read by label alone it passes
vacuously. Most audit issues state their severity in the body (`Severity: high`).

## Content triage

All 735 open issues were listed. Every candidate security or privacy issue (106 bodies) was read
and rated with one rubric:
- **high:** untrusted input or the default configuration leads to disclosure of secrets or
  classified values, a signature or verification bypass, code or statement injection, or a
  credential sent to an attacker-chosen host;
- **medium:** needs non-default misuse or local access;
- **low:** hardening.

Some ratings differ from the severity stated in the issue body:
- Rated down to medium: #684, #273, #274, #413 and #482. Their bodies say high, but each is
  resource exhaustion or a crash.
- Also rated down to medium: #633, #338 and #415. Their bodies say high, but the secret reaches
  only a local file or the user's own stderr.
- Rated up to high: #629, #663 and #721. Their bodies say medium.

### High: fixed in this lane (each has a test that failed before the fix)

| # | Title | Fix | Test |
|---|---|---|---|
| 631 | fabric: the Spark router sends the bearer token to any host named by a Location header or continuationUri | `shape.scale.http.on_fabric_api`; `FabricSparkRouter._list_notebooks` and `get_or_create_notebook` refuse a URL off the Fabric API host | `tests/scale/test_spark.py::test_631_*` |
| 434 | shape-fabric: the Fabric API client sends the bearer token to any host a Location header names | `FabricApi._await` refuses an operation URL off the Fabric API host | `plugins/shape-fabric/tests/test_security_review.py::test_434_*` |
| 579 | plugins: a newline in a RECORD path makes two different file lists share one signed message | `trust._signed_list` refuses a path with `\n` or `\r` (existing signatures stay valid) | `tests/plugins/test_trust.py::test_579_*` |
| 683 | share-bundle verify passes a signed bundle with an added data file whose extension is upper case | `share_bundle._check_names` accepts only lower-case data suffixes, the ones `create` writes and the loader reads | `tests/w5_10/test_share_bundle.py::test_683_*` |
| 394 | privacy: redact_sensitive keeps min, max, enum values, samples and quantiles of a classified column | `redact_sensitive` removes every key in `policy.VALUE_KEYS` | `tests/privacy/test_release_policy_new.py::test_394_*` |
| 650 | privacy: release_for passes the joint block and column placeholders through | `placeholders` is in `VALUE_KEYS`; `release_for` withholds the top-level `joint` block when any column is classified above the target | `tests/privacy/test_release_policy_new.py::test_650_*` |

Partly fixed: #650 also asks for small cells inside `joint` to be suppressed when every column is
at or below the target. That is not done: `joint` passes unchanged when no column is classified
above the target. **Proposed fix:** apply `suppress_column_cells`-style cohort suppression to
`joint.conditionals[].table` and `joint.dependencies[].violations`.

### High: still open, with a proposed fix

| # | Title | Small and local? | Proposed fix |
|---|---|---|---|
| 724 (duplicate of 285) | io: a value or column name with a line "GO" splits the T-SQL script and runs its tail as statements | yes | `src/shape/builtins/sinks/sql.py`: in `_literal`, for the T-SQL dialects, split text on CR/LF and join the pieces with `+ NCHAR(10) +` (`CHAR` for Fabric Warehouse). In `_quote`, refuse a name that holds a control character. |
| 285 | SQL sink: GO batch injection; PostgreSQL literals depend on standard_conforming_strings | yes (with 724) | The same fix. For postgres, also emit `E'...'` with backslashes doubled, or start the script with `SET standard_conforming_strings = on`. |
| 629 | io: the single-table Excel sink stores text starting with "=" as a formula | yes | `src/shape/builtins/sinks/excel.py` `ExcelSink.write`: reuse `workbook.py`'s hardened text writer (`data_type = "s"`, illegal-character stripping, sheet-title cleaning). |
| 275 | security: bearer tokens follow HTTP redirects to any scheme and host | yes (two files) | `src/shape/scale/http.py` and `shape_fabric/kusto.py` `urllib_transport`: build an opener whose `HTTPRedirectHandler.redirect_request` returns `None`. |
| 276 | built-in sources and sinks: an abfss:// URI with any host receives the user's SAS, account key or Entra token | yes | `src/shape/builtins/sources/azure.py` `parse`: allow only Azure storage and OneLake hosts (including sovereign clouds) before a credential is attached. |
| 395 | privacy: the safe profile of a column with fewer than k rows publishes its exact value as the mean | local, but changes safe-profile output | `SafeColumnProfile.from_column`: when `base < k` and not `unsafe_full_fidelity`, drop `mean`, `std`, `quantiles`, `bounds` and `distribution_params`. This may change safe-profile parity with the baseline (not checked here), so it needs the lead's decision. |
| 533 | bridge: diff and check return raw values of classified columns through joint-analysis entries | mostly | `bridge/handlers/flow.py` `redact_entries`: also match classified names inside joint column labels and in `determinant` / `dependent`. |
| 535 | bridge: verify returns raw column extremes in range-gate messages | yes | `bridge/handlers/flow.py` `cmd_verify`: unless `include_raw`, strip the `(actual min/max: ...)` text that `quality/gates.py` adds. |
| 663 | scenario: the demo comparison page prints the real values of personal-data columns | yes | `demo/charts.py` `render_html`: skip the per-value table when the PII gate fires. |
| 721 | fabric: User Data Function results return raw min and max of classified columns by default | needs an owner decision (the issue says so) | `integrations/fabric/udf.py`: redact the summary and entries as the bridge does, with an opt-in parameter. |
| 411 | shape-fabric: the tape scrubber leaves passwords holding '}', and JSON or quoted secrets, in recordings | yes | `shape_fabric/recording.py` `_PATTERNS`: allow a quoted key, and brace-, double-quote- and single-quote-delimited values. |

### Medium and low (not fixed; one-line proposals)

Exhaustion and robustness: #684, #273, #274, #280, #282, #563, #720, #283, #286, #298, #413,
#367, #632, #450 and #637. Fix: bound the sizes, depths and loops each issue names.

Redaction of secrets in local records and errors: #633, #634, #279, #277, #278, #290, #299, #338,
#415, #371 and #664. Fix: pass the values through `redact` / `redact_text` at the point each
issue names.

Injection and paths with non-default inputs: #296, #295, #294, #284, #281, #432 and #243. Fix:
validate or quote the value each issue names.

Privacy controls:
- #669: keep the `unsafe` stamp.
- #657 and #398: report malformed or newer safe profiles as findings.
- #603, #530 and #396: detect raw and safe documents by their format.
- #596: reject `k < 2`.
- #2: PII detection by per-value rate.
- #28: confirm the raw-profile refusal, then close.

Keys and plugin trust:
- #402: apply the permission check to plain key paths.
- #580: map `csv.Error` to `PluginTrustError`.
- #588: reject signature version below 1.
- #706: resolve symlinks before an in-place sign.
- #407: reserve `manifest.sig`.
- #428: strict base64.

CI supply chain: #265, #266, #267 and #262. Pin actions to commit SHAs, and fix the SBOM,
pip-audit and secret-pattern coverage.

Files and modes: #297, #288, #289 and #525.

Other low: #451, #453, #452, #300, #287, #140, #163, #301, #37, #11, #12, #482, #548, #551, #562,
#712, #546, #544, #380 and #671.

## Static analysis

`bandit -q -r src -ll`, the command `.github/workflows/security.yml` runs, exited 1 on
`int/INT-18`:
- one **high** finding: B324, SHA-1 in `contracts/emit/ddl.py::_constraint_name`;
- one medium finding: B506, `yaml.load` with a `SafeLoader` subclass in `project/changes.py`.

Neither is a security use. Both are fixed in this lane: `usedforsecurity=False`, and a
`# nosec B506` with its reason, in the repo's existing `nosec` style. The command now exits 0.
Output: `bandit_before.txt`, `bandit_after.txt`.

# SEC-high: the high-severity findings open at Gate G7 (lane/SEC-high, from lane/G7-eval)

Status: **all 11 assigned findings fixed, plus the rest of #650. Each fix has a test, written
first, that fails before the fix. #721 was not touched (owner decision pending). The lead's
decisions of 2026-10-05 are applied (#535 as fixed; the #395 named difference in the safe-profile
parity harness), and `safe_profile_1to1/verify.py` exits 0.** §11 and §2.3 were not edited. No
gate, tolerance or D-xx/T-xx decision was changed. The only harness change is the lead-requested,
both-sides-checked named difference for #395; no other field was loosened. No workflow changed.

- Base: `lane/G7-eval` (`81b31ac`), with `int/INT-18` merged at the start (`c73e02c6`) and again at
  the end (`bf3604b3`, INT-18 at `fcbdb1a5`), and once more after the lead's decisions (`4c339fd`,
  INT-18 at `6594c830`, which has `69f6ffef`: the harness checks W3-12's `validators` on both sides).
- Machine: 4 vCPU, Python 3.11.15, Rust 1.97.0, pinned RefEngine `422e78d` (§1.2, unmodified).
- Evidence: `docs/plans/evidence/SEC-high/`.

## Issue by issue

The tests are in `tests/security/test_sec_high.py` and, for the shape-fabric plugin,
`plugins/shape-fabric/tests/test_security_review.py`. With the source changes stashed and the
tests kept, 47 of the new tests fail; the other 11 guard behaviour that must not change, such as
allowed hosts still working and a column with k rows keeping its mean
(`fail_before_fix.txt`). With the fixes, all of them pass.

| # | Fix | Tests |
|---|---|---|
| 724, 285 | `shape.security.sqltext`, shared by the SQL sink, the design DDL and the contract DDL. A name with a control character is refused. A T-SQL text value with a line break is written as `N'a' + NCHAR(10) + N'b'` (`CHAR` on Fabric Warehouse); above 8,000 bytes it gets a `CAST(... AS NVARCHAR(MAX))` prefix so concatenation cannot truncate it. A PostgreSQL literal with a backslash is written as `E'...'`. Values with no line break and no backslash keep their bytes. `design/ddl.py` passes `t.kind` through `_one_line`. | `test_724_*`, `test_285_*` |
| 629 | `ExcelSink.write` writes cells and the header through the workbook writer's `_Writer`, so formula and error text is stored as text and illegal characters are dropped. Sheet names go through `valid_sheet_names`. | `test_629_*` |
| 275 | `shape.scale.http.SameOriginRedirect` follows a 3xx only to the same scheme, host and port. The Fabric/OneLake transport and the shape-fabric Kusto transport both use it. (The polling of `Location` and `continuationUri` was already fixed by G7-eval, #631 and #434.) | `test_275_*` (core); `test_275_the_kusto_transport_*`, which runs real local HTTP servers |
| 276 | `azure.check_storage_host`, called before any credential is resolved, in `_filesystem` (abfss source and sink, Fabric mirror, shape-fabric storage) and in delta's `_storage_options`. It allows Azure Storage `dfs`/`blob` hosts in the public, US Government and China clouds, and `[<region>-]onelake.dfs\|blob.fabric.microsoft.com`. A `filesystem` option passed in is used as given. | `test_276_*` |
| 533 | `redact_entries` reads every column an entry names: the names in a joint label, the detail's `determinant`, `dependent` and `columns`, plus a whole-word match of each classified name. It also withholds `message`, `detail`, `value` and `examples`. | `test_533_*` (end to end through the bridge, and a unit test) |
| 535 | **Fixed for classified columns**, per the "Safe by default" rule (lead decision 2026-10-05). `flow.redact_gate_messages`, used by bridge `verify` 1.0 and 1.1+, drops the `(actual min\|max: ...)` text from a message about a **classified** column. The column is profiled on its own, and only when a message quotes it. A column that is not classified keeps the frozen, documented 1.1 text, and the frozen 1.1 vector stays as it is. `docs/BRIDGE.md` says exactly that. | `test_535_*[1.0, 1.2]` |
| 663 | `render_html` skips the per-value table of a column that `pii_gate_fires` classifies. Null rate and distinct count are still shown. | `test_663_*` |
| 411 | `recording`: the key may be quoted (JSON). The value may be a braced value with `}}` escapes, a double- or single-quoted string (quotes kept, so JSON stays valid) or a bare word. A connection-string `Password=`/`PWD=` value runs to the next `;`. `load()` now refuses a tape holding a secret, as `save()` does. The tape format is unchanged (FORMAT 1). Every committed tape still replays, and the scenarios still record exactly the committed tapes (`test_recorded.py`, 107 passed). | `test_411_*` |
| 650 (rest) | `cells.suppress_joint`, which `release_for` applies when nothing is above the target. A conditional row or dependency violation below k rows is withheld. Values below k inside one fold into `__OTHER__`; shares are read with a lower bound for their 4-place rounding. `cohorts`, `copula` and `multivariate_outliers`, whose cells are not checked one by one, are withheld. | `test_650_*` |
| 395 | Lead GO. A column with fewer non-null rows than its k gets null `mean`, `std`, `quantiles`, `bounds` and `distribution_params`; `--unsafe-full-fidelity` keeps them. New validator rule `small-cohort-statistic`, with k taken from the redaction manifest. | `test_395_*` |
| 721 | **Not changed**, as instructed (owner decision pending). | — |

Existing tests changed because they pinned the reported behaviour (their strictness is unchanged):
- `tests/generation/golden/writers/item.postgres.sql`: one line (`'O''Brien \ é'` becomes
  `E'O''Brien \\ é'`).
- `tests/contracts/emit/test_emit_ddl.py`:
  - the postgres CHECK literal is now an `E''` literal;
  - `test_a_backslash_is_doubled_for_mysql_only` is renamed `..._for_mysql_and_in_a_postgres_e_literal`.
- `tests/generation/test_writers.py::test_sql_names_and_headers_cannot_add_statements`:
  - a name with a line break is now asserted to be refused, with no file written;
  - the parse checks (no `Delete`, one `Create`, one `Insert`) still run on a hostile name that has
    quotes and brackets but no line break;
  - the header-comment checks are unchanged.
- `tests/scale/test_http.py`: two transport tests now patch `http._OPENER` instead of
  `urllib.request.urlopen`. Their assertions are unchanged.

## For the lead

Both questions from the previous round are decided (2026-10-05) and applied:

1. **#535.** Fixed for classified columns per the "Safe by default" rule: their extremes are
   withheld from bridge `verify` messages. An unclassified column keeps the frozen, documented 1.1
   text (`(actual max: ...)`), and the frozen 1.1 vector (`docs/bridge/vectors/1.1/verify.json`)
   stays. The "Safe by default" section of `docs/BRIDGE.md` now says exactly that.
2. **Safe-profile parity.** Merged `int/INT-18` (`4c339fd`, merge commit). It brings `69f6ffef`,
   in which the harness checks W3-12's `validators` on both sides.
   - `safe_profile_1to1/verify.py` (D2, its default): **PARITY OK, exit 0**
     (`safe_profile_parity_final.txt`). The harness has no `--negative-control` flag. See the
     negative control below.
   - #395 changes no D2 column. It does change the edge datasets: e3 and e15, plus the k11 and
     sensitive cases of the others. Their columns have fewer non-null rows than k, so the product
     withholds `mean`, `std`, `quantiles`, `bounds` and `distribution_params`, which the baseline
     releases. Both formats derive `bounds_winsorized` in the redaction manifest from `bounds`, so
     it follows them.
   - So the harness has a named difference, `SMALL_COHORT_KEYS` / `check_small_cohorts`, built
     like 13b8a03a. It applies only to a column the raw profile's counts put below
     `cfg.column_k`, and never in an unsafe case. Before withholding those keys and that flag from
     the baseline, it checks both sides:
     - the product holds each key as null and its `bounds_winsorized` is false;
     - the baseline's `mean` and `std` equal the raw profile's;
     - the baseline's `bounds_winsorized` matches its own `bounds`.
   - Every other field is compared as before. Tests: `tests/benchmarks/test_safe_profile_small_cohort.py`.
     All 15 fail without the harness change and pass with it.
   - Negative control: the same harness against plain `int/INT-18`'s product, which releases the
     statistics, reports 90 `... rows < k but the product releases it` mismatches on `edge/e3.csv`.
3. **Every other dataset** (`safe_profile_sweep_vs_int18.txt`). d1.parquet, d2.parquet and d3/d4
   (csv and parquet) exit 0. d1.csv and 38 of the 39 edge files exit 1, with the same exit on plain
   `int/INT-18`. I compared the complete, untruncated mismatch sets of the lane and of
   `int/INT-18`:
   - They are identical on every one of those datasets except `edge/e3.csv` and
     `edge/e3_uuidpk.csv`.
   - On those two, the same 10 paths (`zip5` `mean`/`std`) are reported in the named difference's
     `(baseline)` form instead.
   - They come from a dtype difference: the baseline reads `zip5`, and d1's `zip`, as integers;
     the product reads them as `postal_code` strings. That difference is in `int/INT-18` too and is
     outside this lane. It is not set aside.
4. `plugins/shape-fabric/tests/test_lakehouse.py::test_parquet_to_a_local_folder_round_trips`
   fails in this session's fresh environment in both kernels, and also on plain `int/INT-18`. The
   venv resolved pyarrow 19.0.1, the `pyproject.toml` floor, and a dictionary column reads back
   with `int32` indices where `sample_schema()` has `int8`. This lane does not touch it. The 36
   shape-fabric failures of the previous round are gone with the INT-18 merge.

## Checks run in this session (final tree, `bf3604b3`)

| Command | Result |
|---|---|
| `ruff check src tests plugins benchmarks/vs_refengine` | pass |
| `ruff format --check ...` | pass |
| `mypy` | no issues |
| `bandit -q -r src -ll` | exit 0 |
| `lint-imports`, `check_secrets`, `check_user_facing` (D-13), `check_shipped_data` and the other `make check` scripts | pass (`static_checks.txt`) |
| `pytest -m "not emulator and not live"`, both kernels | see below |

- `SHAPE_KERNEL=rust` (private TMPDIR, Fabric demo requirements and unixODBC installed):
  2 failed, 14382 passed, 23 skipped, exit 1, in 48 min (`pytest_full_rust.txt`). The 2 failures,
  `test_compat_1_0` and `test_compat_1_1`, both `[list]`, also fail on plain `int/INT-18`
  (`fcbdb1a5`) in a separate worktree. The domain descriptions that the installed shape-domains
  plugin gives differ from the vectors, and this lane touches neither.
- `SHAPE_KERNEL=python`: 2 failed, 14382 passed, 23 skipped, exit 1, in 2 h 33 min
  (`pytest_full_python.txt`). The 2 failures are the same `[list]` vectors as under the rust kernel,
  and they also fail on plain `int/INT-18`.
- `plugins/shape-fabric/tests`: 36 failed/errored, the same 36 with this lane's source changes
  stashed. For example, INT-18's remote-write confirmation refuses `synapse://` without `--yes`.

### After the lead's decisions (2026-10-05, final tree)

Fresh container: the §1 environment rebuilt (`setup_refengine.sh`, pinned RefEngine `422e78d`,
unmodified; the shape venv with the CI plugin set, the Fabric demo requirements and unixODBC).

| Command | Result |
|---|---|
| `ruff check`, `ruff format --check` (src tests plugins benchmarks/vs_refengine) | pass |
| `mypy` | no issues (634 files) |
| `bandit -q -r src -ll` | exit 0 |
| every other `make check` step (vulture, lint-imports, check_* scripts incl. D-13 `check_user_facing`, cargo fmt/clippy/test) | pass |
| `safe_profile_1to1/verify.py` (D2) | PARITY OK, exit 0 (`safe_profile_parity_final.txt`) |
| `tests/security plugins/shape-fabric/tests tests/privacy tests/bridge tests/benchmarks`, `SHAPE_KERNEL=rust` | 2568 passed, 1 failed (the lakehouse test above, also on plain INT-18) (`suites_final_rust.txt`) |
| the same, `SHAPE_KERNEL=python` | 2568 passed, 1 failed, the same test (`suites_final_python.txt`) |

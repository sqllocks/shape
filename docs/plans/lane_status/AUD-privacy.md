# AUD-privacy: audit and fix of privacy, security, artifacts and the registries

Area: `src/shape/privacy/**`, `src/shape/security/**`, `src/shape/artifact/**`,
`src/shape/registry/**`, their tests, `security/**` and the area's docs. Branch
`lane/AUD-privacy`, from `origin/build/main-plan`.

Status: **done**: every defect in the area is fixed and pushed, except the items under "Left open".

## Phase 1: findings

Baseline: `pytest tests/privacy tests/security tests/artifact tests/registry` 351 passed; area
coverage 79% (`registry/profiles.py` 18% from these tests, covered mostly by
`tests/regressions`; `privacy/dp.py` 0% here, covered by `tests/fidelity/test_dp.py`).

Severity, file and line, reproduction, expected, actual. Full reproductions are in the issues.

| # | Sev | Where | Defect | Issue |
|---|---|---|---|---|
| F1 | high | `privacy/release.py:73-78` | `redact_sensitive` drops only `topk`/`examples`; `min`, `max`, `enum_values`, `samples`, `value_counts_ext`, `quantiles` of a PII column survive. Expected: every `VALUE_KEYS` key removed. | #394 |
| F2 | high | `privacy/safe_profile.py` `from_column` | a column with fewer non-null rows than `k` keeps `mean`/`std`/`quantiles`/`bounds`/`distribution_params`; one row gives `mean == value`, and `validate --safe` says clean. Expected: withheld below `k`. | #395 |
| F3 | medium | `registry/local.py:27-47` | `is_raw_profile` raises `AttributeError` (manifest `[]`) and `RecursionError` (deep JSON); a raw profile with a UTF-8 BOM or in UTF-16 passes as not raw. Unbounded manifest read is #283. | #396, #283 |
| F4 | medium | `privacy/safe_validator.py:179-182` | `tables` that is not an object skips every `row_count` check: `{"schema_version":1,"tables":[{"row_count":0}]}` is CLEAN (exit 0). Expected: fail closed. | #398 |
| F5 | medium | `privacy/detect.py:22,34` | `detect_value("2024-01-15")` is `phone`; a date column is detected as phone with confidence 1.0. | #400 |
| F6 | medium | `artifact/keys.py:117-128` | a private key given as a plain path is read when group/others can read it; the same file as `file://` is refused. | #402 |
| F7 | low | `artifact/signing.py:159-173` | `sign_artifact` leaves the artifact mode 0600 (mkstemp). | #405 |
| F8 | low | `artifact/io.py:229-247` | the reader accepts `content_hashes` naming `manifest.sig`; signing such a file writes a duplicate member. | #407 |
| F9 | low | `registry/local.py:132-160` | torn log line → raw `JSONDecodeError` from `log`/`resolve`/`entry`; missing object → raw `FileNotFoundError`; `refs()` lists `.tmp-` files. | #409 |
| F10 | low | `privacy/safe_profile.py:136-176` | `inf`/`nan` numeric enum keys crash `_bucket_numeric` (`OverflowError`). | #412 |
| F11 | low | `privacy/dp.py:96-110` | one infinite value makes every noised value `inf`/`NaN`. | #416 |
| F12 | low | `registry/profiles.py:301-325` | a damaged `_index.json` entry gives `KeyError: 'system'` from `entries()`. | #420 |
| F13 | low | `privacy/release.py:30-38,75` | `suppress_shape` `TypeError` on `count: None`; `redact_sensitive(redact_at=())` "min() arg is an empty sequence". | #424 |
| F14 | low | `artifact/secure.py:29-37` | `SecureEnvelope.from_bytes` raises `KeyError`/`TypeError`/`JSONDecodeError`, base64 not validated. | #428 |
| F15 | high | `security/jsondepth.py:38-43` | (filed before this lane) `]` inside a string drives the depth negative, so a deep line passes and pyarrow segfaults. | #273 |
| F16 | low | `security/redact.py` | (filed before) dict/JSON/`sasl_password` forms not masked, `@` in a URI password, quadratic URL pattern; also `api_key`, `Authorization: Basic`. | #291 |
| F17 | low | `security/hardening.py:25` | (filed before) Event Hubs secret pattern is quadratic. | #292 |
| F18 | low | `privacy/cli.py` | (filed before, general CLI issue) `shape profile safe|validate --safe X.shape` prints the "not signed" notice as a raw Python warning with a source line. | #109 |
| F19 | medium | `security/names.py` | (filed before) `is_safe_name` accepts Windows-unsafe names (ADS `a:b`, `CON`, trailing dot/space, `<>"|?*`). | #243 |

Not defects, or bound by a fixed decision (for the lead, not changed):

- D1. The leak scanner checks string *values* for personal-data patterns, not mapping keys
  (`{"categorical_weights": {"a@b.co": 0.5}}` is clean). The docs say "anywhere in a value", and
  the validator is held to the pinned baseline's findings on fixtures (P7-01 parity,
  `benchmarks/vs_spindle/safe_profile_1to1`, fixture `leak_email_key_value`). Changing it is a
  parity change: escalate if wanted.
- D2. Hashed category keys are unsalted, truncated SHA-256 (`_hash_key`), so a low-entropy key
  can be recovered by a dictionary. Same parity constraint as D1; the cell is still at least `k`
  rows.
- D3. `release_for(classifications=...)` lets an explicit label lower a column's own label.
  It is an explicit caller decision, not an implicit downgrade; left as is.
- `artifact/canonical.canonical_json` (rejects floats) and `artifact/io.canonical_json`
  (allows finite floats) share a name with different rules; both are used, kept.

## Left open

- F20 (low, #554): `tests/security/test_credential_refs.py::test_core_imports_no_cloud_sdk_to_resolve_references`
  checks the whole of `sys.modules`, so it fails when an earlier test imported the Azure SDK
  (plugins installed). Pre-existing (fails on `origin/build/main-plan` in the same order). It is an
  existing test, so it is **not** changed here: for the lead (suggested fix: compare `sys.modules`
  before and after the two `resolve_reference` calls, as the next test in that file does).
- #237: registry writers on Windows, being fixed on the AUD-portability lane (see Phase 3 notes).
- D1 to D3 above: decisions for the lead, not defects.

## Phase 3: fixes

Each fix has a regression test that failed first (the failing output is in the test commit's
message), then the fix commit, pushed after each.

| Finding | Issue | Failing test commit | Fix commit |
|---|---|---|---|
| F15 JSON depth guard | #273 | 83a92ca | 84e9df7 |
| F1 redact_sensitive | #394 | 7644023 | 6952429 |
| F2 small-column value statistics | #395 | 7644023 | 5e9b5c7 (+ cb75cc2 docs) |
| F3 raw-profile sniff | #396, #283 | f9b6c75 | 8984366 |
| F4 validator fail-open | #398 | 0add5a5 | 264d2eb |
| F5 dates as phones | #400 | 0add5a5 | d92be1a |
| F6 plain-path private key | #402 | 9ec8e6c | f8abb08 (docs/SIGNING.md too) |
| F19 Windows-unsafe names | #243 (names part) | e6839cc | 67409e9 |
| F16 redact_text | #291 | 9b24be7 | a41d2b1 |
| F17 secret scanner | #292 | 27f76f3 | 95b1697 |
| F7 signing file mode | #405 | 5ad4239 | f5293c4 |
| F8 reserved members | #407 | 5ad4239 | 7341295 |
| F14 secure envelope | #428 | 5ad4239 | e7ab559 |
| F9 local registry errors | #409 | 5ad4239 | 675e50d |
| F12 profile-registry index | #420 | 5ad4239 | 86e2977 |
| F10 non-finite enum keys | #412 | 5ad4239 | 8ac806a |
| F11 DP non-finite | #416 | 5ad4239 | 03a2151 |
| F13 suppress_shape / redact_at | #424 | 5ad4239 | fadc396 |
| F18 privacy CLI notice | #109 (privacy part) | 5ad4239 | 007a408 |
| improvement: `privacy.core` tests (60% → 100%) | n/a | n/a | 9d83b02 |

Notes on scope:

- #243: only `shape.security.names` is in this lane; `shape.io.store._clean` (same issue) is not.
  `<>"|?*` stay allowed (on Windows they only fail the write); an existing Fabric-plugin test
  (`test_a_hostile_table_name_cannot_break_out_of_the_m_expression`) relies on a table name with
  `"`, and is unchanged.
- #109: only the privacy commands (`shape profile safe`, `shape profile validate --safe`).
- #237 (registry writers on Windows) is in this lane's paths but is being fixed on the
  AUD-portability lane (said in the issue); not touched here, to avoid two fixes of one line.

## Filed outside the area (not fixed here)

- #529 `benchmarks/vs_spindle/safe_profile_1to1/verify.py`: `pattern_rates` and
  `pattern_contains_rates` (added by ISS-profile #2) are not in `ADDED_KEYS`, so the verifier
  prints PARITY FAILED on the base branch. With those two keys added in a scratch copy (not
  committed) it prints `PARITY OK`, exit 0, on this branch. Exact diff for the lead:

  ```diff
  -ADDED_KEYS = {"cells_suppressed"}
  +ADDED_KEYS = {"cells_suppressed", "pattern_rates", "pattern_contains_rates"}
  ```
- #530 `src/shape/cli/registry.py` `_safe_document`: a safe-profile JSON with a byte-order mark
  skips the commit-time leak scan.

## Equivalence (outputs the verifiers compare)

- Safe profile (#395, #398, #412): `to_safe_profile` JSON for all six verifier configurations on D2
  plus the validator findings on every fixture hash to `3ac9586f...` on the base branch and after
  each change. `safe_profile_1to1/verify.py` exits 1 on base and on this branch with byte-identical
  output (the #529 keys); with #529's two keys added in a scratch copy: `PARITY OK`, exit 0.
- Differential privacy (#416): `benchmarks/vs_spindle/fidelity_tiers_1to1/run.py` (retail medium,
  the documented scale) `PASS`, exit 0. At `--scale small` it exits 1 on four tier-1
  `autocorrelation_lag1/7` fields of `return` (relative 3e-9 to 9e-9 against 1e-9); the base
  branch gives the same four mismatches with `--reuse`, and no DP field differs. A hash of DP output
  (Laplace/Gaussian, clip on/off, nulls and NaN) is `0625e85a...` before and after.
  `tests/benchmarks/test_fidelity_tiers_1to1.py`: 18 passed.

## Checks run (this session)

- `ruff check src tests plugins benchmarks/vs_spindle`: all checks passed.
- `ruff format --check src tests plugins benchmarks/vs_spindle`: 1091 files already formatted.
- `mypy`: success, no issues in 436 source files.
- `python scripts/check_user_facing.py`: clean.
- `SHAPE_KERNEL=rust pytest -m "not emulator and not live"`: 7199 passed, 4 failed. All four fail
  on `origin/build/main-plan` too: #554 (above), and three that need pyarrow newer than 19.0.1
  (`tests/iss_gaps/test_landing_and_batches.py::test_file_sinks_take_path_template_and_batch_date`,
  `tests/kernel/test_hashing.py::test_rust_equals_reference_on_a_million_values[float16]`,
  `::test_one_and_one_point_zero_hash_equal`; #333): `tests/demo/fabric/requirements.txt`
  installs pyarrow 19.0.1 in this venv.
- `plugins/shape-fabric/tests/test_lakehouse.py::test_parquet_to_a_local_folder_round_trips`
  fails the same way on base (dictionary index width under pyarrow 19.0.1).

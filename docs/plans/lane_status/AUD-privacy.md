# AUD-privacy: audit and fix of privacy, security, artifacts and the registries

Area: `src/shape/privacy/**`, `src/shape/security/**`, `src/shape/artifact/**`,
`src/shape/registry/**`, their tests, `security/**` and the area's docs. Branch
`lane/AUD-privacy`, from `origin/build/main-plan`.

Status: **in progress** (phase 3, fixing).

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

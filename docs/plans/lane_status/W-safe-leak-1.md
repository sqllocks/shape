# W-safe-leak-1 (0.9.4)

Remote verified: `https://github.com/sqllocks/shape.git` (exit 0).
`git fetch origin` and `git checkout -B lane/W-safe-leak-1 origin/main`: exit 0.
Base: `a8e476c`. No AGENTS.md exists in this checkout or its workspace ancestors.
Read CLAUDE.md, CONTRIBUTING.md, PRIVACY_MODEL.md, capture redaction, safe conversion,
CLI selection and existing privacy/capture tests. W-uv-1 is not on this main;
use the existing `.venv` and documented commands.

## Tests first

Implementation was unchanged for both red runs on origin/main.
`.venv/bin/pytest -q tests/privacy/test_safe_path_parity.py`: exit 1,
4 failed (19.22s). Full output retained locally at `/tmp/W-safe-leak-1-red.txt`.
Second run with concise tracebacks: exit 1. Exact output follows.

```text
FFFF                                                                     [100%]
=================================== FAILURES ===================================
_________________ test_sensitive_fit_is_withheld_on_every_path _________________
tests/privacy/test_safe_path_parity.py:17: in test_sensitive_fit_is_withheld_on_every_path
    @given(rows=st.integers(min_value=100, max_value=500), offset=st.integers(6, 1000))
                   ^^^
tests/privacy/test_safe_path_parity.py:36: in test_sensitive_fit_is_withheld_on_every_path
    assert column["distribution_params"] is None
E   AssertionError: assert {'loc': 6.0, 'scale': 99.0} is None
E   Failing test case: test_sensitive_fit_is_withheld_on_every_path(
E       rows=100,
E       offset=6,
E   )
__________ test_excluded_name_is_absent_from_every_safe_json[exclude] __________
tests/privacy/test_safe_path_parity.py:43: in test_excluded_name_is_absent_from_every_safe_json
    @settings(max_examples=5, deadline=None)
                   ^^^^^^^^^^^^
tests/privacy/test_safe_path_parity.py:64: in test_excluded_name_is_absent_from_every_safe_json
    assert excluded.encode() not in selected.to_json(compact=compact).encode()
E   assert b'shipping_address_aaa' not in b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n'
E    +  where b'shipping_address_aaa' = <built-in method encode of str object at 0x7f3750d168b0>()
E    +    where <built-in method encode of str object at 0x7f3750d168b0> = 'shipping_address_aaa'.encode
E    +  and   b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n' = <built-in method encode of str object at 0x2e1d5a90>()
E    +    where <built-in method encode of str object at 0x2e1d5a90> = '{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5,...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n'.encode
E    +      where '{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5,...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n' = to_json(compact=False)
E    +        where to_json = SafeProfile(tables={'table': SafeTableProfile(name='table', row_count=200, columns={'status': SafeColumnProfile(name='...'cells_suppressed': 0, 'bounds_winsorized': False, 'pattern_only': False, 'k': 5, 'sensitive': False}}}}, unsafe=False).to_json
E   Failing test case: test_excluded_name_is_absent_from_every_safe_json(
E       selection='exclude',
E       suffix='aaa',
E   )
__________ test_excluded_name_is_absent_from_every_safe_json[include] __________
tests/privacy/test_safe_path_parity.py:43: in test_excluded_name_is_absent_from_every_safe_json
    @settings(max_examples=5, deadline=None)
                   ^^^^^^^^^^^^
tests/privacy/test_safe_path_parity.py:64: in test_excluded_name_is_absent_from_every_safe_json
    assert excluded.encode() not in selected.to_json(compact=compact).encode()
E   assert b'shipping_address_aaa' not in b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n'
E    +  where b'shipping_address_aaa' = <built-in method encode of str object at 0x7f3751befcf0>()
E    +    where <built-in method encode of str object at 0x7f3751befcf0> = 'shipping_address_aaa'.encode
E    +  and   b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n' = <built-in method encode of str object at 0x2e1eae90>()
E    +    where <built-in method encode of str object at 0x2e1eae90> = '{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5,...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n'.encode
E    +      where '{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5,...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n' = to_json(compact=False)
E    +        where to_json = SafeProfile(tables={'table': SafeTableProfile(name='table', row_count=200, columns={'status': SafeColumnProfile(name='...'cells_suppressed': 0, 'bounds_winsorized': False, 'pattern_only': False, 'k': 5, 'sensitive': False}}}}, unsafe=False).to_json
E   Failing test case: test_excluded_name_is_absent_from_every_safe_json(
E       selection='include',
E       suffix='aaa',
E   )
________________ test_cli_exclusion_removes_the_manifest_entry _________________
tests/privacy/test_safe_path_parity.py:76: in test_cli_exclusion_removes_the_manifest_entry
    assert excluded.encode() not in output.read_bytes()
E   assert b'shipping_address_id' not in b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n'
E    +  where b'shipping_address_id' = <built-in method encode of str object at 0x7f376e480af0>()
E    +    where <built-in method encode of str object at 0x7f376e480af0> = 'shipping_address_id'.encode
E    +  and   b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n' = read_bytes()
E    +    where read_bytes = PosixPath('/tmp/pytest-of-agent/pytest-1/test_cli_exclusion_removes_the0/safe.json').read_bytes
----------------------------- Captured stdout call -----------------------------
{"unsafe": false, "written": "/tmp/pytest-of-agent/pytest-1/test_cli_exclusion_removes_the0/safe.json"}
----------------------------- Captured stderr call -----------------------------
shape: note: /tmp/pytest-of-agent/pytest-1/test_cli_exclusion_removes_the0/input.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
=========================== short test summary info ============================
FAILED tests/privacy/test_safe_path_parity.py::test_sensitive_fit_is_withheld_on_every_path
FAILED tests/privacy/test_safe_path_parity.py::test_excluded_name_is_absent_from_every_safe_json[exclude]
FAILED tests/privacy/test_safe_path_parity.py::test_excluded_name_is_absent_from_every_safe_json[include]
FAILED tests/privacy/test_safe_path_parity.py::test_cli_exclusion_removes_the_manifest_entry
4 failed, 4 warnings in 0.77s

```

Expanded fit-policy red run (normal fit and missing-quantile cases), exit 1:

```text
FFFFFFF.FFF.                                                             [100%]
=================================== FAILURES ===================================
E   AssertionError: assert {'loc': 6.0, 'scale': 99.0} is None
    Failing test case: test_sensitive_fit_is_withheld_on_every_path(
        rows=100,
        offset=6,
    )
/workspace/shape/tests/privacy/test_safe_path_parity.py:36: AssertionError: assert {'loc': 6.0, 'scale': 99.0} is None
E   assert b'shipping_address_aaa' not in b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n'
     +  where b'shipping_address_aaa' = <built-in method encode of str object at 0x7f19ba76a4b0>()
     +    where <built-in method encode of str object at 0x7f19ba76a4b0> = 'shipping_address_aaa'.encode
     +  and   b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n' = <built-in method encode of str object at 0x5c95470>()
     +    where <built-in method encode of str object at 0x5c95470> = '{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5,...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n'.encode
     +      where '{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5,...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n' = to_json(compact=False)
     +        where to_json = SafeProfile(tables={'table': SafeTableProfile(name='table', row_count=200, columns={'status': SafeColumnProfile(name='...'cells_suppressed': 0, 'bounds_winsorized': False, 'pattern_only': False, 'k': 5, 'sensitive': False}}}}, unsafe=False).to_json
    Failing test case: test_excluded_name_is_absent_from_every_safe_json(
        selection='exclude',
        suffix='aaa',
    )
/workspace/shape/tests/privacy/test_safe_path_parity.py:64: assert b'shipping_address_aaa' not in b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n'
E   assert b'shipping_address_aaa' not in b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n'
     +  where b'shipping_address_aaa' = <built-in method encode of str object at 0x7f19ba74de70>()
     +    where <built-in method encode of str object at 0x7f19ba74de70> = 'shipping_address_aaa'.encode
     +  and   b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n' = <built-in method encode of str object at 0x5ca8e60>()
     +    where <built-in method encode of str object at 0x5ca8e60> = '{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5,...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n'.encode
     +      where '{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5,...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n' = to_json(compact=False)
     +        where to_json = SafeProfile(tables={'table': SafeTableProfile(name='table', row_count=200, columns={'status': SafeColumnProfile(name='...'cells_suppressed': 0, 'bounds_winsorized': False, 'pattern_only': False, 'k': 5, 'sensitive': False}}}}, unsafe=False).to_json
    Failing test case: test_excluded_name_is_absent_from_every_safe_json(
        selection='include',
        suffix='aaa',
    )
/workspace/shape/tests/privacy/test_safe_path_parity.py:64: assert b'shipping_address_aaa' not in b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n'
E   assert b'shipping_address_id' not in b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n'
     +  where b'shipping_address_id' = <built-in method encode of str object at 0x7f19d7e08170>()
     +    where <built-in method encode of str object at 0x7f19d7e08170> = 'shipping_address_id'.encode
     +  and   b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n' = read_bytes()
     +    where read_bytes = PosixPath('/tmp/pytest-of-agent/pytest-2/test_cli_exclusion_removes_the0/safe.json').read_bytes
----------------------------- Captured stdout call -----------------------------
{"unsafe": false, "written": "/tmp/pytest-of-agent/pytest-2/test_cli_exclusion_removes_the0/safe.json"}
----------------------------- Captured stderr call -----------------------------
shape: note: /tmp/pytest-of-agent/pytest-2/test_cli_exclusion_removes_the0/input.shape is not signed: its origin is not verified (check it with --verify PUBKEY)
/workspace/shape/tests/privacy/test_safe_path_parity.py:76: assert b'shipping_address_id' not in b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ame": "table",\n      "primary_key": [],\n      "row_count": 200\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n'
E   AssertionError: assert {'loc': 6.0, 'scale': 99.0} == None
     +  where {'loc': 6.0, 'scale': 99.0} = SafeColumnProfile(name='order_id', dtype='integer', null_rate=None, cardinality=100, mean=None, std=None, quantiles=No...ing_length=None, hour_histogram=None, dow_histogram=None, temporal_histogram=None, validators=None, cells_suppressed=0).distribution_params
/workspace/shape/tests/privacy/test_safe_path_parity.py:107: AssertionError: assert {'loc': 6.0, 'scale': 99.0} == None
E   AssertionError: assert {'loc': 6.0, 'scale': 99.0} == None
     +  where {'loc': 6.0, 'scale': 99.0} = SafeColumnProfile(name='order_id', dtype='integer', null_rate=None, cardinality=100, mean=None, std=None, quantiles=No...ing_length=None, hour_histogram=None, dow_histogram=None, temporal_histogram=None, validators=None, cells_suppressed=0).distribution_params
/workspace/shape/tests/privacy/test_safe_path_parity.py:107: AssertionError: assert {'loc': 6.0, 'scale': 99.0} == None
E   AssertionError: assert {'loc': 6.0, 'scale': 99.0} == None
     +  where {'loc': 6.0, 'scale': 99.0} = SafeColumnProfile(name='order_id', dtype='integer', null_rate=None, cardinality=100, mean=None, std=None, quantiles=No...ing_length=None, hour_histogram=None, dow_histogram=None, temporal_histogram=None, validators=None, cells_suppressed=0).distribution_params
/workspace/shape/tests/privacy/test_safe_path_parity.py:107: AssertionError: assert {'loc': 6.0, 'scale': 99.0} == None
E   AssertionError: assert {'loc': 6.0, 'scale': 99.0} == None
     +  where {'loc': 6.0, 'scale': 99.0} = SafeColumnProfile(name='order_id', dtype='integer', null_rate=None, cardinality=100, mean=None, std=None, quantiles={'...ing_length=None, hour_histogram=None, dow_histogram=None, temporal_histogram=None, validators=None, cells_suppressed=0).distribution_params
/workspace/shape/tests/privacy/test_safe_path_parity.py:107: AssertionError: assert {'loc': 6.0, 'scale': 99.0} == None
E   AssertionError: assert {'loc': 6.0, 'scale': 99.0} == None
     +  where {'loc': 6.0, 'scale': 99.0} = SafeColumnProfile(name='order_id', dtype='integer', null_rate=None, cardinality=100, mean=None, std=None, quantiles={'...ing_length=None, hour_histogram=None, dow_histogram=None, temporal_histogram=None, validators=None, cells_suppressed=0).distribution_params
/workspace/shape/tests/privacy/test_safe_path_parity.py:107: AssertionError: assert {'loc': 6.0, 'scale': 99.0} == None
E   AssertionError: assert {'loc': 6.0, 'scale': 99.0} == None
     +  where {'loc': 6.0, 'scale': 99.0} = SafeColumnProfile(name='order_id', dtype='integer', null_rate=None, cardinality=100, mean=None, std=None, quantiles={'...ing_length=None, hour_histogram=None, dow_histogram=None, temporal_histogram=None, validators=None, cells_suppressed=0).distribution_params
/workspace/shape/tests/privacy/test_safe_path_parity.py:107: AssertionError: assert {'loc': 6.0, 'scale': 99.0} == None
=========================== short test summary info ============================
FAILED tests/privacy/test_safe_path_parity.py::test_sensitive_fit_is_withheld_on_every_path
FAILED tests/privacy/test_safe_path_parity.py::test_excluded_name_is_absent_from_every_safe_json[exclude]
FAILED tests/privacy/test_safe_path_parity.py::test_excluded_name_is_absent_from_every_safe_json[include]
FAILED tests/privacy/test_safe_path_parity.py::test_cli_exclusion_removes_the_manifest_entry
FAILED tests/privacy/test_safe_path_parity.py::test_sensitive_fit_policy_does_not_depend_on_quantiles[None-uniform]
FAILED tests/privacy/test_safe_path_parity.py::test_sensitive_fit_policy_does_not_depend_on_quantiles[None-exponential]
FAILED tests/privacy/test_safe_path_parity.py::test_sensitive_fit_policy_does_not_depend_on_quantiles[None-lognormal]
FAILED tests/privacy/test_safe_path_parity.py::test_sensitive_fit_policy_does_not_depend_on_quantiles[quantiles1-uniform]
FAILED tests/privacy/test_safe_path_parity.py::test_sensitive_fit_policy_does_not_depend_on_quantiles[quantiles1-exponential]
FAILED tests/privacy/test_safe_path_parity.py::test_sensitive_fit_policy_does_not_depend_on_quantiles[quantiles1-lognormal]
10 failed, 2 passed, 4 warnings in 0.69s

```

## Scope clarification

User confirmed: fix only the existing safe-conversion path. The initial
`shape profile TABLE` command has no `--exclude` on this base. Adding initial-capture
exclusion is a possible future lane; it is not added here. Input captures remain inputs,
not filtered outputs of safe conversion.

Initial implementation check: exit 1 (2 failed, 10 passed): the new immutability
assertion caught a shallow-copy manifest mutation. Corrected with a deep copy.

Relationship coverage: initial auto-detection fixture produced no relationship (exit 1), so it was corrected to provide an explicit valid relationship. With origin/main safe_profile.py restored temporarily, the relationship byte-leak assertion failed (exit 1). The fixed file was then restored.

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_________ test_selection_removes_relationships_naming_excluded_columns _________
tests/privacy/test_safe_path_parity.py:134: in test_selection_removes_relationships_naming_excluded_columns
    assert excluded.encode() not in selected.to_json().encode()
E   assert b'shipping_address_id' not in b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ry_key": [\n        "order_id"\n      ],\n      "row_count": 400\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n'
E    +  where b'shipping_address_id' = <built-in method encode of str object at 0x7fdbc1a3fab0>()
E    +    where <built-in method encode of str object at 0x7fdbc1a3fab0> = 'shipping_address_id'.encode
E    +  and   b'{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5...ry_key": [\n        "order_id"\n      ],\n      "row_count": 400\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n' = <built-in method encode of str object at 0x19ce63b0>()
E    +    where <built-in method encode of str object at 0x19ce63b0> = '{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5,...ry_key": [\n        "order_id"\n      ],\n      "row_count": 400\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n'.encode
E    +      where '{\n  "format": "shape-safe-profile",\n  "min_shape_version": "0.9.0",\n  "redaction_manifest": {\n    "k_default": 5,...ry_key": [\n        "order_id"\n      ],\n      "row_count": 400\n    }\n  },\n  "unsafe": false,\n  "version": 1\n}\n' = to_json()
E    +        where to_json = SafeProfile(tables={'addresses': SafeTableProfile(name='addresses', row_count=200, columns={'status': SafeColumnProfil... 'cells_suppressed': 0, 'bounds_winsorized': True, 'pattern_only': False, 'k': 5, 'sensitive': False}}}}, unsafe=False).to_json
=========================== short test summary info ============================
FAILED tests/privacy/test_safe_path_parity.py::test_selection_removes_relationships_naming_excluded_columns
1 failed in 0.55s

```

## Implementation

Safe-profile selection prunes per-column manifest entries as well as statistics,
keys and correlations, and removes any relationship whose endpoint lost a column.
It copies nested state so the source remains unchanged. No exclusion-name record
is emitted. Safe conversion drops non-normal fits for pattern-only columns, matching
safe capture. Capture applies that policy even without quantiles. Normal fits remain
allowed by the existing privacy model; explicit unsafe conversion remains the opt-out.

No format version or golden-corpus file has been changed. Questions: none about
format or goldens at this point. Initial-capture exclusion is deferred per the user.

## Check results

- New regressions green: exit 0, 13 passed (0.81s).
- Final affected privacy/capture cases: exit 0, 64 passed (1.58s).
- `python scripts/check_docs_language.py`: exit 0, 0 hits.
- `mkdocs build --strict`: exit 0, built in 16.41s.
- `python scripts/check_doc_links.py --site site`: exit 0, 0 broken links.
- `python scripts/check_user_facing.py --site site`: exit 0, clean.

Final broader-suite and make-check outcomes follow below.

- Additional 27 privacy/safe-profile/profile modules outside the primary directories
  (including CLI, benchmark, bridge, joint, kernel, demo, registry, artifact, security,
  regression and plugin modules): exit 0, 549 passed (132.71s).
- Final strict docs rebuild after wording clarification: exit 0 (17.81s).
- Final wording recheck: exit 0, 0 hits.
- Changed-file Ruff lint and formatting and `git diff --check`: each exit 0.

- Complete `tests/privacy tests/safecapture tests/profile tests/timecapsule`:
  exit 0, 1565 passed (197.58s), including the heavy profile tests and frozen-corpus checks.
- Final source/site link and user-facing wording rechecks: each exit 0.
- No format change and no changed golden/corpus file; no format or golden question.

## Sandbox and interrupted full check

The initial `make check` lost its exec-server transport. Its shell/make session
could not be resumed. The orphaned pytest worker ultimately had raw wait status 9
(SIGKILL, shell-equivalent 137); no top-level make exit code is recoverable from
that interrupted run. It reached about 24% with failures, without a final traceback
summary. No complete gate pass is claimed. The static stages before pytest each
completed successfully, including lint, formatting, mypy and repository checks.

A fresh `make check` uses `PYTEST_ADDOPTS='--maxfail=1 --tb=short'` to preserve every
gate and its coverage floor while obtaining the first failure and a complete exit
status. Final result follows below.

The file-permission failure was independently reproduced on the lane (exit 1)
and in a detached, untouched origin/main worktree (exit 1, base source import
verified). The sandbox's inherited umask is `0077`; the existing test expects a
new file not to have mode `0600`. No file-sink code or test was changed.

Origin/main output:

```text
F                                                                        [100%]
=================================== FAILURES ===================================
_____________ test_new_file_has_umask_permissions_and_no_temp_left _____________
tests/builtins/test_bugs_builtins_file_sink_atomic.py:33: in test_new_file_has_umask_permissions_and_no_temp_left
    assert mode != 0o600
E   assert 384 != 384
=========================== short test summary info ============================
FAILED tests/builtins/test_bugs_builtins_file_sink_atomic.py::test_new_file_has_umask_permissions_and_no_temp_left
1 failed in 0.55s

```

With umask `0022`, the same untouched origin/main permission test passes (exit 0,
1 passed in 0.44s), confirming the environment dependency.

A separate check of the branding failure from the interrupted run exits 1 because
this environment lacks Playwright. The strict docs build itself passes; browser
verification cannot import its required dependency. No browser check was skipped
or changed.

```text
F                                                                        [100%]
=================================== FAILURES ===================================
______________________________ test_branded_site _______________________________
tests/docs/test_brand.py:26: in test_branded_site
    spec.loader.exec_module(preview)
<frozen importlib._bootstrap_external>:999: in exec_module
    ???
<frozen importlib._bootstrap>:488: in _call_with_frames_removed
    ???
scripts/docs_brand_preview.py:14: in <module>
    from playwright.sync_api import sync_playwright
E   ModuleNotFoundError: No module named 'playwright'
=========================== short test summary info ============================
FAILED tests/docs/test_brand.py::test_branded_site - ModuleNotFoundError: No ...
1 failed in 14.64s

```

## Final full-gate result

`PYTEST_ADDOPTS='--maxfail=1 --tb=short' make check`: **exit 2**.
All static stages completed with exit 0 again. The pytest stage exited 1 at the
confirmed origin/main umask-dependent test: 1 failed, 2066 passed, 1 existing skip
(Pydantic v2 not installed), 69 deselected by the documented make target, 260 warnings
in 180.20s. No new skips or xfails were introduced. The 86% coverage gate was not
changed. Because make stopped at its first failing test stage, subsequent heavy,
pure-Python kernel and Rust stages were not reached; no complete make/coverage
pass is claimed. Heavy profile cases were included in the separate passing module run.

Exact failure and summary:

```text
=================================== FAILURES ===================================
_____________ test_new_file_has_umask_permissions_and_no_temp_left _____________
tests/builtins/test_bugs_builtins_file_sink_atomic.py:33: in test_new_file_has_umask_permissions_and_no_temp_left
    assert mode != 0o600
E   assert 384 != 384
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
=========================== short test summary info ============================
SKIPPED [1] tests/importers/test_w5_06_pydantic.py:14: Pydantic v2 is not installed
FAILED tests/builtins/test_bugs_builtins_file_sink_atomic.py::test_new_file_has_umask_permissions_and_no_temp_left
!!!!!!!!!!!!!!!!!!!!!!!!!! stopping after 1 failures !!!!!!!!!!!!!!!!!!!!!!!!!!!
1 failed, 2066 passed, 1 skipped, 69 deselected, 260 warnings in 180.20s (0:03:00)
make: *** [Makefile:25: check] Error 1
```

## Final scope and questions

Changed files: `src/shape/privacy/safe_profile.py`, `src/shape/privacy/redact.py`,
`tests/privacy/test_safe_path_parity.py`, `docs/PRIVACY_MODEL.md`, `CHANGELOG.md`,
and this status file. No safe-profile version change; no golden/corpus changes;
no format or golden question. No open implementation questions. A clean full gate
still needs a suitably configured environment; the interrupted run's other tutorial
failure markers had no recoverable tracebacks, so they are not declared resolved
or attributed to this fix. Initial-capture exclusion remains a possible future lane,
explicitly excluded from this task by the user.

Commit uses the requested prefix and DCO sign-off. Push is restricted to
`refs/heads/lane/W-safe-leak-1`; no PR, tag, force-push or other-branch push.

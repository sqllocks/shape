"""Hand-built artifacts for the validator parity check: each one is a known leak or a known
clean document. Plain dicts, no imports, so both venvs load it."""

from __future__ import annotations

from typing import Any

_COL: dict[str, Any] = {"name": "c", "dtype": "string", "null_rate": 0.0, "cardinality": 3}


def _doc(col: dict[str, Any], **top: Any) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "unsafe": False,
        "tables": {"t": {"name": "t", "row_count": 100, "columns": {"c": {**_COL, **col}}}},
        "relationships": [],
        "redaction_manifest": {},
    } | top


FIXTURES: dict[str, Any] = {
    "clean_minimal": _doc({}),
    "clean_length_minmax": _doc({"length_dist": {"min": 3.0, "mean": 5.0, "max": 9.0}}),
    "leak_bounds_minmax": _doc({"bounds": {"min": 1.0, "max": 99.0}}),
    "leak_min_value_max_value": _doc({"min_value": 4, "max_value": 400}),
    "leak_minimum_maximum": _doc({"extra": {"minimum": 1, "maximum": 2}}),
    "leak_raw_list": _doc({"top_values": ["alpha", "beta", "gamma"]}),
    "ok_two_strings": _doc({"labels": ["alpha", "beta"]}),
    "leak_nested_list": _doc({"x": [["a", "b", "c"]]}),
    "leak_email": _doc({"note": "write to jane.doe@example.com"}),
    "leak_ssn": _doc({"note": "123-45-6789"}),
    "leak_ip": _doc({"note": "10.1.2.3"}),
    "leak_iban": _doc({"note": "DE89370400440532013000"}),
    "leak_phone": _doc({"note": "+1 (555) 123-4567"}),
    "leak_email_key_value": _doc({"categorical_weights": {"a@b.co": 0.5, "__OTHER__": 0.5}}),
    "unsafe_stamp": _doc({}, unsafe=True),
    "row_count_missing": {
        "schema_version": 1,
        "tables": {"t": {"name": "t", "columns": {}}},
    },
    "row_count_zero": {
        "schema_version": 1,
        "tables": {"t": {"name": "t", "row_count": 0, "columns": {}}},
    },
    "table_not_object": {"schema_version": 1, "tables": {"t": 5}},
    "no_markers": {"tables": {"t": {"row_count": 5, "columns": {}}}},
    "legacy_columns": {"columns": {"a": {"min": 1, "max": 9, "top_values": ["x", "y", "z"]}}},
    "list_root": [1, 2, 3],
    "string_root": "hello",
}

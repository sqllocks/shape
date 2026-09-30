"""Gate G1: every phase-1 profiling bug of the plan (P1-P21, except P19, which is phase 7) has a
regression test. The table names the tests; the check below fails if an entry is missing or a
named test no longer exists."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parents[1]

PHASE1_BUGS: dict[str, list[tuple[str, str]]] = {
    "P1": [("io/test_readers.py", "test_csv_values_are_typed_not_strings")],
    "P2": [("profile/test_infer.py", "test_p2_demotion_to_text_keeps_every_value")],
    "P3": [("profile/test_infer.py", "test_p3_a_leading_none_does_not_lock_the_column_as_text")],
    "P4": [("profile/test_infer.py", "test_p4_numpy_numbers_and_decimal_are_numeric")],
    "P5": [
        (
            "kernel/test_sketches.py",
            "test_p5_space_saving_holds_at_most_capacity_after_ten_million_updates",
        ),
        ("kernel/test_sketches.py", "test_p5_top_values_does_not_grow"),
    ],
    "P6": [("kernel/test_sketches.py", "test_p6_merge_is_symmetric_and_keeps_error_terms")],
    "P7": [
        ("kernel/test_hashing.py", "test_p7_sketches_ignore_nan_and_treat_1_and_1_point_0_alike")
    ],
    "P8": [
        ("artifact/test_model_v2.py", "test_capture_with_nan_and_inf_can_be_saved_and_read_back")
    ],
    "P9": [("kernel/test_sketches.py", "test_p9_total_weight_is_preserved_exactly")],
    "P10": [
        ("capture/test_columns_kernel.py", "test_p10_arrow_nulls_are_counted_not_turned_into_nan")
    ],
    "P11": [("capture/test_columns_kernel.py", "test_p11_none_is_not_the_text_none")],
    "P12": [
        ("capture/test_columns_kernel.py", "test_p12_column_and_row_paths_emit_the_same_schema")
    ],
    "P13": [
        (
            "capture/test_columns_kernel.py",
            "test_p13_text_columns_do_not_fall_back_to_per_cell_python",
        )
    ],
    "P14": [
        (
            "contracts/test_contracts.py",
            "test_unique_ids_pass_at_5k_to_30k_rows_on_a_sketch_estimate",
        ),
        ("contracts/test_contracts.py", "test_unique_uses_the_exact_count_in_exact_mode"),
    ],
    "P15": [("contracts/test_contracts.py", "test_null_rate_of_an_empty_table_is_zero")],
    "P16": [
        (
            "profile/test_dependencies.py",
            "test_p16_fd_confidence_is_not_inflated_when_the_group_table_is_bounded",
        )
    ],
    "P17": [
        ("profile/test_engine.py", "test_p17_pearson_streams_rows"),
        ("profile/test_engine.py", "test_p17_mutual_information_keeps_a_bounded_sample"),
        ("profile/test_engine.py", "test_p17_group_and_key_evidence_is_bounded"),
    ],
    "P18": [("artifact/test_model_v2.py", "test_reader_failures_are_artifact_errors")],
    "P20": [("artifact/test_model_v2.py", "test_tuples_survive_save_and_load")],
    "P21": [
        (
            "query/test_query.py",
            "test_relationship_with_the_same_name_twice_does_not_match_anything_that_mentions_it",
        )
    ],
}


def _test_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def test_every_phase_1_bug_is_in_the_table():
    expected = {f"P{i}" for i in range(1, 22)} - {"P19"}
    assert set(PHASE1_BUGS) == expected


@pytest.mark.parametrize("bug", sorted(PHASE1_BUGS, key=lambda b: int(b[1:])))
def test_the_regression_test_exists(bug):
    for rel, name in PHASE1_BUGS[bug]:
        path = TESTS / rel
        assert path.is_file(), f"{bug}: {rel} is missing"
        assert name in _test_names(path), f"{bug}: {rel} has no test named {name}"

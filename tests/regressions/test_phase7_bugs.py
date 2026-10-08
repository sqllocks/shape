"""Gate G7: every phase-7 bug of the plan (SEC3, P19) has a regression test. The table names the
tests; the check below fails if an entry is missing or a named test no longer exists. The named
tests themselves run in their own suites, under both kernels."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

TESTS = Path(__file__).resolve().parents[1]

PHASE7_BUGS: dict[str, list[tuple[str, str]]] = {
    "SEC3": [
        ("privacy/test_taxonomy.py", "test_policy_and_taxonomy_share_one_table"),
        ("privacy/test_taxonomy.py", "test_every_label_is_accepted_by_every_entry_point"),
        ("privacy/test_taxonomy.py", "test_confidential_is_released_and_redacted_like_sensitive"),
        ("privacy/test_taxonomy.py", "test_unknown_label_is_rejected_not_ignored"),
    ],
    "P19": [
        ("artifact/test_signing.py", "test_forged_artifact_with_rewritten_hashes_fails_verify"),
        ("artifact/test_signing.py", "test_forged_and_stripped_signature_fails"),
        ("artifact/test_signing.py", "test_attacker_resigns_with_own_key_fails_trusted_key"),
    ],
}


def _test_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


def test_every_phase_7_bug_is_in_the_table():
    assert set(PHASE7_BUGS) == {"SEC3", "P19"}


@pytest.mark.parametrize("bug", sorted(PHASE7_BUGS))
def test_the_regression_test_exists(bug):
    for rel, name in PHASE7_BUGS[bug]:
        path = TESTS / rel
        assert path.is_file(), f"{bug}: {rel} is missing"
        assert name in _test_names(path), f"{bug}: {rel} has no test named {name}"

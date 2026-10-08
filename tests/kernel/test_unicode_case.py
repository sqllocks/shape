"""Issue #547: ``string_case`` is bit-equal between the native kernel and its twin on all text.

The native kernel is the contract (Rust's ``char::is_alphanumeric`` word rule and the Rust
standard library's case tables). The twin reads ``shape/kernel/reference/unicode_case.json``
outside ASCII instead of the running Python's Unicode data, so these sweeps hold on every
supported Python, whatever its Unicode version.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pyarrow as pa
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from shape.kernel import dispatch
from shape.kernel.reference import gen as ref

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "gen_unicode_case_table", ROOT / "scripts" / "gen_unicode_case_table.py"
)
assert _spec is not None and _spec.loader is not None
table_script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(table_script)

MODES = ("upper", "lower", "title")
SIGMA = "Σ"
CHARS = [chr(c) for c in table_script.code_points()]


@pytest.fixture(scope="module")
def nat():
    return dispatch._import_native()


def _assert_same(nat, values: list[str], mode: str) -> None:
    arr = pa.array(values, pa.string())
    got = pa.array(nat.string_case(arr, mode)).to_pylist()
    want = ref.string_case(arr, mode).to_pylist()
    if got != want:
        bad = [(v, g, w) for v, g, w in zip(values, got, want, strict=True) if g != w]
        raise AssertionError(f"{mode}: {len(bad)} strings differ, first {bad[:5]!r}")


def test_issue_547_examples(nat):
    arr = pa.array(["Ⓐbc", "ⓘnfo"])  # Ⓐbc, ⓘnfo: symbols (So) that are alphabetic
    assert pa.array(nat.string_case(arr, "title")).to_pylist() == ["Ⓐbc", "Ⓘnfo"]
    assert ref.string_case(arr, "title").to_pylist() == ["Ⓐbc", "Ⓘnfo"]
    # ƛ: its capital (U+A7DC) is newer than the Unicode tables of Python 3.11 to 3.13
    lam = pa.array(["ƛ"])
    assert pa.array(nat.string_case(lam, "upper")).to_pylist() == ["Ƛ"]
    assert ref.string_case(lam, "upper").to_pylist() == ["Ƛ"]


@pytest.mark.heavy
@pytest.mark.parametrize("mode", MODES)
def test_every_code_point_alone(nat, mode):
    _assert_same(nat, CHARS, mode)


@pytest.mark.heavy
@pytest.mark.parametrize("mode", MODES)
def test_every_code_point_inside_a_word(nat, mode):
    # word boundaries on both sides of every code point, ASCII and non-ASCII neighbours
    _assert_same(nat, ["a" + c + "b" for c in CHARS], mode)
    _assert_same(nat, ["é" + c + "x" for c in CHARS], mode)


@pytest.mark.heavy
def test_every_code_point_as_final_sigma_context(nat):
    _assert_same(nat, [c + SIGMA for c in CHARS], "lower")
    _assert_same(nat, ["Α" + c + SIGMA for c in CHARS], "lower")
    _assert_same(nat, ["Α" + SIGMA + c for c in CHARS], "lower")
    _assert_same(nat, ["a" + SIGMA + c + "b" for c in CHARS], "lower")


def test_sigma_contexts(nat):
    values = [
        SIGMA,
        "a" + SIGMA,
        "A" + SIGMA + " b",
        "a'" + SIGMA,
        "'" + SIGMA,
        "Α" + SIGMA + "'",
        "Α" + SIGMA + "'a",
        "x" * 40 + SIGMA,
        "ᾼ" + SIGMA,
        "ΟΔΥΣΣΕΥΣ",  # ΟΔΥΣΣΕΥΣ
        SIGMA + SIGMA + SIGMA,
    ]
    for mode in MODES:
        _assert_same(nat, values, mode)


@settings(max_examples=300, deadline=None)
@given(st.lists(st.text(max_size=12), min_size=1, max_size=20))
def test_random_text(nat, values):
    for mode in MODES:
        _assert_same(nat, values, mode)


def test_table_is_current_against_native_kernel(nat):
    table = ROOT / "src" / "shape" / "kernel" / "reference" / "unicode_case.json"
    shipped = table.read_text(encoding="utf-8")
    assert shipped == table_script.dumps(table_script.build_table(nat)), (
        "unicode_case.json is stale against the native kernel: "
        "run python scripts/gen_unicode_case_table.py"
    )

"""W1-10: ``scripts/check_v1_done.py`` and the three documents of the v1.0 promise.

The checker reads ``docs/V1_DONE.md``, confirms every pytest node id it names is collected and
every CI job exists, and exits 1 listing what does not. The document tests keep CLI_STABILITY.md,
NOT_BUILDING.md and the links to them in step with the code.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from shape.cli import stability
from shape.cli.main import _build_parser

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_v1_done", ROOT / "scripts" / "check_v1_done.py"
)
assert _spec is not None and _spec.loader is not None
done = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(done)

HEAD = "| # | Statement | Proof | Status |\n|---|---|---|---|\n"
REAL = "tests/cli/test_exit_code_classes.py::test_class_0_ok"


def _doc(tmp_path: Path, *rows: str) -> Path:
    path = tmp_path / "V1_DONE.md"
    path.write_text(HEAD + "\n".join(rows) + "\n", encoding="utf-8")
    return path


def test_the_real_document_exits_0(capsys: pytest.CaptureFixture[str]) -> None:
    assert done.main([]) == 0
    assert "0 problem(s)" in capsys.readouterr().out


def test_a_missing_test_exits_1_and_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = _doc(
        tmp_path,
        f"| V1-01 | real | `{REAL}` | done |",
        "| V1-02 | gone | `tests/cli/test_exit_code_classes.py::test_no_such_test` | done |",
    )
    assert done.main(["--doc", str(doc)]) == 1
    out = capsys.readouterr().out
    assert (
        "V1-02: test does not exist: tests/cli/test_exit_code_classes.py::test_no_such_test" in out
    )
    assert [ln for ln in out.splitlines() if ln.startswith("MISSING:")] == [
        "MISSING: V1-02: test does not exist: "
        "tests/cli/test_exit_code_classes.py::test_no_such_test"
    ]


def test_a_missing_file_exits_1_and_is_named(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = _doc(tmp_path, "| V1-01 | gone | `tests/nope/test_x.py::test_y` | done |")
    assert done.main(["--doc", str(doc)]) == 1
    assert "tests/nope/test_x.py::test_y" in capsys.readouterr().out


def test_a_parametrized_test_and_a_class_are_named_without_their_parameters() -> None:
    param = (
        "tests/cli/test_capture_sources.py::"
        "test_the_same_table_gives_the_same_model_in_every_format"
    )
    assert done.check(HEAD + f"| V1-01 | s | `{param}` | done |\n") == []
    wrong = param + "_not"
    assert len(done.check(HEAD + f"| V1-01 | s | `{wrong}` | done |\n")) == 1


def test_a_partial_name_is_not_a_match() -> None:
    partial = "tests/cli/test_exit_code_classes.py::test_class_0"
    assert len(done.check(HEAD + f"| V1-01 | s | `{partial}` | done |\n")) == 1


def test_a_ci_job_must_exist() -> None:
    ok = done.check(HEAD + "| V1-01 | s | `ci:ci.yml/rust` | done |\n")
    assert ok == []
    for bad in ("ci:ci.yml/no-such-job", "ci:no-such.yml/test", "ci:rust"):
        problems = done.check(HEAD + f"| V1-01 | s | `{bad}` | done |\n")
        assert problems == [f"V1-01: CI job does not exist: {bad}"]


def test_a_done_item_needs_proof() -> None:
    assert done.check(HEAD + "| V1-01 | s | | done |\n") == [
        "V1-01: marked done but names no test or CI job"
    ]


def test_an_open_item_names_what_closes_it() -> None:
    for status in ("open W1-14", "open: #88", "open P8-04"):
        assert done.check(HEAD + f"| V1-01 | s | | {status} |\n") == [], status
    for status in ("open", "todo", "", "done-ish"):
        problems = done.check(HEAD + f"| V1-01 | s | | {status} |\n")
        assert len(problems) == 1 and "status" in problems[0], status


def test_proof_named_by_an_open_item_must_still_exist() -> None:
    problems = done.check(
        HEAD + "| V1-01 | s | `tests/cli/test_x_none.py::test_y` | open W1-14 |\n"
    )
    assert problems == ["V1-01: test does not exist: tests/cli/test_x_none.py::test_y"]


def test_duplicate_ids_and_an_empty_document_are_problems() -> None:
    twice = HEAD + f"| V1-01 | s | `{REAL}` | done |\n| V1-01 | t | `{REAL}` | done |\n"
    assert done.check(twice) == ["V1-01: listed twice"]
    assert done.check("# nothing here\n") == ["the document has no V1-NN rows"]


def test_an_unreadable_document_and_a_usage_error_exit_2(tmp_path: Path) -> None:
    assert done.main(["--doc", str(tmp_path / "missing.md")]) == 2
    assert done.main(["--nonsense"]) == 2


def test_make_check_runs_the_checker() -> None:
    assert "scripts/check_v1_done.py" in (ROOT / "Makefile").read_text("utf-8")


def test_every_open_item_of_the_real_document_names_a_work_package_or_issue() -> None:
    text = (ROOT / "docs" / "V1_DONE.md").read_text("utf-8")
    rows = [ln for ln in text.splitlines() if re.match(r"\| V1-\d+ ", ln)]
    assert len(rows) >= 20
    assert any(ln.rstrip("| ").endswith(("W1-14", "W1-11", "W1-01")) for ln in rows)


# -- CLI_STABILITY.md --------------------------------------------------------------------------

STABILITY = (ROOT / "docs" / "CLI_STABILITY.md").read_text("utf-8")


def _backticked(row: str) -> set[str]:
    return set(re.findall(r"`([^`]+)`", row))


def test_the_stable_table_is_the_stable_set() -> None:
    row = next(ln for ln in STABILITY.splitlines() if ln.startswith("| Stable |"))
    assert _backticked(row) == set(stability.STABLE)


def test_the_experimental_examples_are_experimental_commands() -> None:
    row = next(ln for ln in STABILITY.splitlines() if ln.startswith("| Experimental |"))
    commands = set(_build_parser()._subparsers._group_actions[0].choices)  # type: ignore[union-attr]
    named = _backticked(row) - {"shape --help"}
    assert named and named <= commands and not named & stability.STABLE


def test_the_deprecation_warning_format_is_in_the_promise() -> None:
    assert "shape: warning: --OLD is deprecated and will be removed in X.Y; use --NEW" in STABILITY
    assert "at least one minor release" in STABILITY and "CHANGELOG.md" in STABILITY


def test_the_deprecation_table_lists_exactly_what_the_code_deprecates() -> None:
    table = STABILITY.split("| Deprecated | Deprecated in | Removed in | Use instead |", 1)[1]
    rows = [ln for ln in table.split("\n## ", 1)[0].splitlines()[2:] if ln.startswith("|")]
    listed = {m.group(1) for ln in rows if (m := re.match(r"\| `([^`]+)`", ln))}
    assert listed == {d["path"] for d in stability.DEPRECATIONS}
    changelog = (ROOT / "CHANGELOG.md").read_text("utf-8")
    for d in stability.DEPRECATIONS:
        assert d["path"] in changelog


def test_every_deprecation_entry_is_complete() -> None:
    for d in stability.DEPRECATIONS:
        assert set(d) >= {"path", "use", "removed_in"}
        assert re.fullmatch(r"\d+\.\d+", d["removed_in"])


def test_the_promise_is_linked_from_the_cli_and_api_docs() -> None:
    for name in ("CLI.md", "API_STABILITY.md"):
        assert "CLI_STABILITY.md" in (ROOT / "docs" / name).read_text("utf-8"), name


def test_the_promise_names_the_checks_that_enforce_it() -> None:
    for needle in (
        "scripts/cli_surface.py --check",
        "tests/cli/cli_surface_v1.json",
        "tests/cli/test_cli_surface.py",
        "tests/cli/test_exit_code_classes.py",
    ):
        assert needle in STABILITY, needle
        assert (ROOT / needle.split(" ")[0]).exists(), needle


# -- NOT_BUILDING.md ---------------------------------------------------------------------------

NOT_BUILDING = (ROOT / "docs" / "NOT_BUILDING.md").read_text("utf-8")


def _entries() -> list[tuple[str, str]]:
    parts = re.split(r"^## ", NOT_BUILDING, flags=re.MULTILINE)[1:]
    return [(p.split("\n", 1)[0], p) for p in parts]


def test_it_starts_with_the_plugin_sandbox_decision() -> None:
    entries = _entries()
    assert len(entries) >= 5
    assert "sandbox" in entries[0][0].lower()
    assert "plugins/trust-model.md" in entries[0][1]


def test_every_entry_has_a_reason_an_alternative_and_a_link_that_resolves() -> None:
    for title, body in _entries():
        for field in ("**Why.**", "**Instead.**", "**Recorded in.**"):
            assert field in body, (title, field)
        recorded = body.split("**Recorded in.**", 1)[1]
        links = re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", recorded)
        assert links, title
        for link in links:
            assert (ROOT / "docs" / link).resolve().exists(), (title, link)


def test_it_is_linked_from_contributing() -> None:
    for path in ("CONTRIBUTING.md", "docs/CONTRIBUTING.md"):
        assert "NOT_BUILDING.md" in (ROOT / path).read_text("utf-8"), path

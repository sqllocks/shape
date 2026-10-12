"""Keep build-specific transcript fields variable without hiding check failures."""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
from docs_transcripts import comparable  # noqa: E402


@pytest.mark.parametrize("size", ("1,024", "2,800,000", "3,100,000"))
def test_wheel_size_is_variable(size: str, tmp_path: Path) -> None:
    """Compare different build sizes while preserving filenames and check results."""
    actual = f"shape-0.9.1-py3-none-any.whl  {size} bytes\nchecks passed"
    shown = "shape-0.9.1-py3-none-any.whl  …\nchecks passed"
    assert comparable(actual, tmp_path, ROOT) == comparable(shown, tmp_path, ROOT)
    assert comparable(actual.replace("checks passed", "FAIL"), tmp_path, ROOT) != comparable(
        shown, tmp_path, ROOT
    )


def test_elided_run_metadata(tmp_path: Path) -> None:
    """Accept omitted runtime values while preserving session outcomes."""
    actual = "Session: 1234abcd\nStarted: 2026-01-01T12:00:00.123\n[1.2s] Done in 1.2s"
    shown = "Session: …\nStarted: …\n[…] Done in …s"
    assert comparable(actual, tmp_path, ROOT) == comparable(shown, tmp_path, ROOT)


def test_pytest_elapsed_clock_presence_is_variable(tmp_path: Path) -> None:
    """pytest adds "(H:MM:SS)" only after a minute; the pass/fail counts stay exact."""
    shown = "404 passed, 46 deselected in 61.20s (0:01:01)"
    fast = "404 passed, 46 deselected in 41.87s"
    assert comparable(fast, tmp_path, ROOT) == comparable(shown, tmp_path, ROOT)
    assert comparable(fast.replace("404 passed", "403 passed, 1 failed"), tmp_path, ROOT) != (
        comparable(shown, tmp_path, ROOT)
    )

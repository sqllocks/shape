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


def test_private_checkout_paths_preserve_warning_and_fixture_details(tmp_path: Path) -> None:
    """A copied install changes code locations, never warning or artifact evidence."""
    location = "plugins/shape-dbt/src/shape_dbt/commands.py:353"
    warning = "ArtifactNotVerifiedWarning: orders.shape is not signed"
    actual = f"{tmp_path}/{location}: {warning}\nartifact: {tmp_path}/orders.shape"
    shown = f"{ROOT}/{location}: {warning}\nartifact: {tmp_path}/orders.shape"
    normalized = comparable(actual, tmp_path, ROOT, source_checkout=tmp_path)
    assert normalized == comparable(shown, tmp_path, ROOT)
    for changed in (
        actual.replace(":353:", ":354:"),
        actual.replace("not signed", "verified"),
        actual.replace("artifact: ", "wrong artifact: "),
    ):
        assert comparable(changed, tmp_path, ROOT, source_checkout=tmp_path) != normalized
    assert "artifact: <work>/orders.shape" in normalized

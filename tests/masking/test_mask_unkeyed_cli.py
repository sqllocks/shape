"""Pin default-seed CLI bytes and the documented limit on finding PII in prose."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

GOLDEN = Path(__file__).parent / "golden" / "unkeyed"
ROOT = Path(__file__).resolve().parents[2]
INPUT = (
    "id,email,phone,notes\n"
    "1,ada@corp.io,555-201-3344,mail ada@corp.io\n"
    "2,bob@corp.io,555-201-3355,call 555-201-3344\n"
    "3,ada@corp.io,555-201-3344,ssn 123-45-6789 from 10.1.2.3\n"
    "4,,,Alice Johnson lives at 123 Main Street\n"
)


def run_mask(tmp_path: Path, *options: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "people.csv").write_text(INPUT, encoding="utf-8")
    return subprocess.run(
        [sys.executable, "-m", "shape", "mask", "people.csv", "-o", "out", *options],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize("options", [(), (), ("--seed", "42")], ids=["default", "repeat", "42"])
def test_unkeyed_default_seed_matches_golden(tmp_path, options):
    result = run_mask(tmp_path, *options)
    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout == (GOLDEN / "stdout.txt").read_text(encoding="utf-8")
    assert (tmp_path / "out" / "people.csv").read_bytes() == (GOLDEN / "people.csv").read_bytes()


def test_free_text_detection_limit_is_kept_in_code_and_docs(tmp_path):
    import pyarrow.csv as pacsv

    result = run_mask(tmp_path)
    assert result.returncode == 0, result.stderr
    before = pacsv.read_csv(tmp_path / "people.csv")
    after = pacsv.read_csv(tmp_path / "out" / "people.csv")
    assert after["notes"].equals(before["notes"])
    assert not after["email"].equals(before["email"])
    assert not after["phone"].equals(before["phone"])
    docs = " ".join((ROOT / "docs" / "MASK.md").read_text(encoding="utf-8").split())
    assert (
        "It does not look inside free text: an e-mail address inside a `notes` column is not found."
        in docs
    )

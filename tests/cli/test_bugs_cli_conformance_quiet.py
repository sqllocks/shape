"""BUGS-cli-1 #311: ``shape conformance`` prints its verdict, not the signature notices of its own
temporary artifacts; the suppression stays inside the suite."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import warnings
from pathlib import Path

from shape.artifact.io import ArtifactNotVerifiedWarning, read_artifact, write_artifact
from shape.validation.suite import conformance


def test_conformance_emits_no_signature_warnings():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        results = conformance()
    assert results and all(r.passed for r in results)
    assert [str(w.message) for w in caught if w.category is ArtifactNotVerifiedWarning] == []


def test_the_suppression_does_not_leak_out_of_the_suite(tmp_path: Path):
    path = tmp_path / "x.shape"
    write_artifact(str(path), {"format": "shape", "version": 1}, {"evidence/a": b"x"})
    with warnings.catch_warnings():
        warnings.simplefilter("always")
        conformance()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            read_artifact(str(path))
    assert any(w.category is ArtifactNotVerifiedWarning for w in caught)


def test_the_command_prints_only_the_result_on_stdout_and_nothing_on_stderr():
    r = subprocess.run(
        [str(Path(sys.executable).parent / "shape"), "conformance"],
        env={**os.environ, "PYTHONWARNINGS": "default"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert r.returncode == 0, r.stderr
    assert r.stderr == ""
    assert all(item["passed"] for item in json.loads(r.stdout))

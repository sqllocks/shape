"""The CLI profile parity harness applies the identifier rule the in-process harness applies.

ISS2-bugs (#46, owner decision of 2026-10-01) keeps an integer column that holds identifiers as
text. ``profile_1to1/verify.py`` computes the expectation for exactly its allow-listed columns
(``IDENTIFIER_RULE``) from the file's text; ``verify_cli.py`` compares the profile that
``shape profile`` writes with the same field-by-field check, so it must apply the same rule to
the same columns, and no other. Nothing here needs the baseline's venv.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
HARNESS = ROOT / "benchmarks" / "vs_refengine" / "profile_1to1"
sys.path.insert(0, str(HARNESS.parent))
sys.path.insert(0, str(HARNESS))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def harness(monkeypatch, tmp_path):
    verify = _load("verify", HARNESS / "verify.py")
    adapter = _load("adapter", HARNESS / "adapter.py")
    cli = _load("profile_1to1_verify_cli", HARNESS / "verify_cli.py")
    baseline = {"columns": {"zip": {"dtype": "integer"}}, "correlation_matrix": None}
    product = {"columns": {"zip": {"dtype": "string"}}}

    def fake_run(cmd):
        out = Path(cmd[cmd.index("-o") + 1])
        out.write_text(json.dumps(baseline))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    calls: list[tuple[str, dict, dict]] = []
    compared: list[dict] = []

    def fake_rule(ds, sp, po):
        calls.append((ds, sp, po))
        return {**sp, "rule_applied_to": ds}

    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setattr(cli, "PROFILE_DATA_DIR", tmp_path)
    monkeypatch.setattr(adapter, "profile_json", lambda path: product)
    monkeypatch.setattr(verify, "identifier_rule_baseline", fake_rule)
    monkeypatch.setattr(verify, "check_table", lambda sp, po, *a: compared.append(sp))
    return cli, calls, compared, baseline, product


def test_the_cli_harness_applies_the_identifier_rule_before_comparing(harness, tmp_path):
    cli, calls, compared, baseline, product = harness
    fails: list[str] = []
    cli.check_profile_cli(["d1.csv", "d1.parquet", "d2.csv"], tmp_path, fails)
    assert fails == []
    # every CSV the CLI profiles goes through the rule (it changes only allow-listed columns)
    assert [c[0] for c in calls] == ["d1.csv", "d2.csv"]
    assert calls[0][1] == baseline and calls[0][2] == product
    # and the comparison sees the baseline as the rule defines it
    assert [sp["rule_applied_to"] for sp in compared] == ["d1.csv", "d2.csv"]

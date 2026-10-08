from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.conformance
ROOT = Path(__file__).resolve().parents[2]


def test_requirements_registry():
    data = yaml.safe_load((ROOT / "docs/specs/requirements.yaml").read_text())
    ids = {r["id"] for r in data["requirements"]}
    assert {"SHAPE-SEC-002", "SHAPE-SEC-004", "SHAPE-PERF-001", "SHAPE-AUTO-001"} <= ids


def test_security_policy_present():
    assert (ROOT / "SECURITY.md").is_file()

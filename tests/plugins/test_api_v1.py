"""P2-01: the API v1 Protocols and the generated docs page."""

import subprocess
import sys
from pathlib import Path

import pyarrow as pa
import pytest

from shape.plugins.api import v1

ROOT = Path(__file__).resolve().parents[2]

# Section 4.3 table: group -> Protocol.
PLAN_TABLE = {
    "shape.sources": "Source",
    "shape.sinks": "Sink",
    "shape.detectors": "SemanticDetector",
    "shape.fitters": "DistributionFitter",
    "shape.strategies": "Strategy",
    "shape.distributions": "Distribution",
    "shape.calendars": "Calendar",
    "shape.domains": "Domain",
    "shape.chaos": "ChaosMutator",
    "shape.emitters": "Emitter",
    "shape.stream_sources": "StreamSource",
    "shape.transforms": "Transform",
    "shape.commands": "Command",
    "shape.reports": "ReportFormat",
}


def test_api_version():
    assert v1.SHAPE_API == "1.0"


def test_every_group_has_its_protocol():
    assert v1.GROUPS == PLAN_TABLE
    for proto_name in PLAN_TABLE.values():
        assert getattr(v1, proto_name) is v1.PROTOCOLS[proto_name]


class _Detector:
    name = "demo"

    def detect(self, values, column):
        return v1.Detection("demo", 1.0)


class _Command:
    name = "hello"
    help = "say hello"

    def configure(self, parser):
        return None

    def run(self, args):
        return 0


def test_structural_conformance():
    assert isinstance(_Detector(), v1.SemanticDetector)
    assert isinstance(_Command(), v1.Command)
    assert not isinstance(_Detector(), v1.Command)
    assert not isinstance(object(), v1.Source)


def test_detector_takes_whole_arrays():
    d = _Detector()
    assert d.detect(pa.array([1, 2]), "c") == v1.Detection("demo", 1.0)


def test_docs_page_is_current():
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "gen_plugin_api_docs.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stdout + r.stderr


def test_docs_page_lists_every_protocol():
    text = (ROOT / "docs" / "plugins" / "api-v1.md").read_text(encoding="utf-8")
    for group, proto in PLAN_TABLE.items():
        assert f"`{group}`" in text and f"## `{proto}`" in text


@pytest.mark.parametrize("proto", sorted(set(PLAN_TABLE.values())))
def test_protocol_is_runtime_checkable(proto):
    isinstance(object(), getattr(v1, proto))

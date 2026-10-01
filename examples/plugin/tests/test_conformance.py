"""The example plugin passes the Shape plugin conformance kit.

Run from the plugin's own directory after ``pip install -e .``::

    pytest
"""

import pytest
from shape.plugins import kit
from shape_example_plugin import HelloCommand, IbanDetector, LinesSource

DIST = "shape-example-plugin"
IBANS = ["DE89370400440532013000", "GB29NWBK60161331926819", "FR1420041010050500013M02606"]


@pytest.fixture
def lines_uri(tmp_path):
    path = tmp_path / "sample.txt"
    path.write_text("alpha\nbeta\ngamma\n", encoding="utf-8")
    return f"lines://{path}"


def test_source(lines_uri):
    kit.check_source(LinesSource(), lines_uri)


def test_detector():
    kit.check_detector(IbanDetector(), positives=[IBANS], negatives=[["hello", "world"]])


def test_command():
    kit.check_command(HelloCommand(), ["--name", "kit"])


def test_installed_distribution_registers_and_loads(lines_uri):
    """Checks the entry points as an installed user gets them: through the plugin host."""
    samples = {
        "shape.sources:lines": {"uri": lines_uri},
        "shape.detectors:iban": {"positives": [IBANS]},
        "shape.commands:hello": {"argv": ["--name", "kit"]},
    }
    lines = kit.check_installed(DIST, samples)
    assert len(lines) == 3 and all(ln.endswith(": ok") for ln in lines)

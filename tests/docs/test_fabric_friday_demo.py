"""The Fabric Friday talk runs its documented offline path with checked outcomes."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TALK = ROOT / "docs/talks/fabric-friday-2026-12"


def test_fabric_friday_offline_demo() -> None:
    run = subprocess.run(
        ["bash", str(TALK / "verify_demo.sh")],
        cwd=ROOT,
        env={**os.environ, "UV_OFFLINE": "1", "NO_COLOR": "1"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=240,
    )
    assert run.returncode == 0, run.stdout
    for line in (
        "violation: order_code regex",
        "violation: order_date min",
        "violation: status allowed_values",
        "drift: order_total mean_shift",
        "drift: status new_categorical_values",
        "OFFLINE DEMO VERIFIED",
    ):
        assert line in run.stdout


def test_fabric_friday_abstract_word_limit() -> None:
    body = (TALK / "ABSTRACT.md").read_text().split("\n", 1)[1]
    assert len(body.split()) <= 120

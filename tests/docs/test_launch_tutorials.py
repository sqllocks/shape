"""Run every explicitly marked page command and compare its complete output."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TUTORIALS = sorted((ROOT / "docs/tutorials").glob("*.md"))
PAGES = TUTORIALS + [ROOT / "docs/MODELS.md"]
BLOCK = re.compile(
    r'```bash {\.runnable}\n(.*?)\n```\n\n\?\?\? info "Output \(exit (\d+)\)"\n\n'
    r"    ```text {\.expected}\n(.*?)    ```",
    re.S,
)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.stem)
def test_tutorial(page: Path, tmp_path: Path) -> None:
    """A page runs in isolation, including its expected error exits."""
    markdown = page.read_text()
    blocks = BLOCK.findall(markdown)
    assert blocks, f"No runnable steps in {page}"
    assert len(blocks) == markdown.count("```bash {.runnable}"), "Missing expected output"
    env = {**os.environ, "NO_COLOR": "1", "COLUMNS": "100", "SHAPE_KERNEL": "python"}
    env["PYTHONPATH"] = str(ROOT / "src")
    for command, code, expected_block in blocks:
        expected = "\n".join(line.removeprefix("    ") for line in expected_block.splitlines())
        if expected == "(no output)":
            expected = ""
        run = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", command],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=120,
        )
        assert run.returncode == int(code), f"{command}\n{run.stdout}"
        assert run.stdout.rstrip("\n") == expected, f"{command}\n{run.stdout}"


def test_tutorial_template() -> None:
    """Tutorials have every required reader-facing section."""
    assert len(TUTORIALS) == 6
    for page in TUTORIALS:
        text = page.read_text()
        for heading in ("What you'll learn", "Prerequisites", "Time", "What's next", "Related"):
            assert f"## {heading}" in text, (page, heading)
        assert re.search(r"^## 1\. ", text, re.M)
        assert "status: available" in text

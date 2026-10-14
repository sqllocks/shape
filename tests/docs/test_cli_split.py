"""Keep the command guide split aligned with the shipped stability list."""

import re
from pathlib import Path

from shape.cli.stability import STABLE

ROOT = Path(__file__).resolve().parents[2]


def test_stable_guide_documents_only_stable_commands() -> None:
    text = (ROOT / "docs/CLI.md").read_text()
    commands = set(re.findall(r"(?:`|^|\n)shape ([a-z][a-z-]+)\b", text))
    assert commands <= STABLE, commands - STABLE


def test_experimental_guide_and_navigation() -> None:
    text = (ROOT / "docs/CLI_EXPERIMENTAL.md").read_text()
    assert "Status: experimental." in text
    assert "shape migrate" in text
    assert "shape vault" in text
    assert "CLI_EXPERIMENTAL.md" in (ROOT / "mkdocs.yml").read_text()
    assert "on every command" not in (ROOT / "docs/CLI.md").read_text()

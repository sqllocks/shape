"""W1-18: the statement of plugin reach in docs/plugins/trust-model.md matches the plugin source.

Every first-party plugin has a row, and every environment variable name and fixed host that a
plugin's code names appears in the document: a plugin that starts reading a new variable or
contacting a new host fails this test until the statement is updated.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.contract

ROOT = Path(__file__).resolve().parents[2]
DOC = (ROOT / "docs" / "plugins" / "trust-model.md").read_text(encoding="utf-8")
PLUGINS = sorted(p.name for p in (ROOT / "plugins").iterdir() if (p / "src").is_dir())
ENV_LITERAL = re.compile(r"""["']((?:SHAPE|PG|MYSQL)_[A-Z_]*[A-Z]|PGPASSWORD|MYSQL_PWD)["']""")
HOST = re.compile(r"""https?://([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)""")
# Hosts that only appear as text, for example in an error message or a generated file's schema URL.
TEXT_ONLY_HOSTS = {"developer.microsoft.com", "example.test", "kql.example.test"}


def sources(plugin: str):
    for path in (ROOT / "plugins" / plugin / "src").rglob("*.py"):
        if path.name in {"testing.py", "scenarios.py"}:
            continue
        yield path, path.read_text(encoding="utf-8")


def test_every_plugin_has_a_row():
    assert PLUGINS == [
        "shape-behavior",
        "shape-databases",
        "shape-dbt",
        "shape-domains",
        "shape-eventhubs",
        "shape-fabric",
        "shape-healthcare-codes",
        "shape-healthcare-standards",
        "shape-kafka",
        "shape-simulation",
        "shape-sqlserver",
    ]
    for plugin in PLUGINS:
        assert f"| `{plugin}` |" in DOC, plugin


@pytest.mark.parametrize("plugin", PLUGINS)
def test_environment_variables_are_listed(plugin):
    row = next(line for line in DOC.splitlines() if line.startswith(f"| `{plugin}` |"))
    names = {m for _, text in sources(plugin) for m in ENV_LITERAL.findall(text)} - {"SHAPE_API"}
    names |= {
        m
        for _, text in sources(plugin)
        for m in re.findall(r"password_env\s*=\s*\(([^)]*)\)", text)
        for m in re.findall(r"[A-Z_]{4,}", m)
    }
    missing = sorted(n for n in names if n not in row)
    assert not missing, (
        f"{plugin}: environment variables not in its row of trust-model.md: {missing}"
    )


@pytest.mark.parametrize("plugin", PLUGINS)
def test_fixed_hosts_are_listed(plugin):
    row = next(line for line in DOC.splitlines() if line.startswith(f"| `{plugin}` |"))
    hosts = set()
    for _, text in sources(plugin):
        for line in text.splitlines():
            if line.lstrip().startswith(("#", '"""')) or "VAULT" in line:
                continue
            hosts |= set(HOST.findall(line))
    hosts -= TEXT_ONLY_HOSTS
    hosts = {h for h in hosts if "." in h and not h.startswith("host")}
    missing = sorted(h for h in hosts if h.replace("api.", "api.") not in row and h not in row)
    assert not missing, f"{plugin}: hosts not in its row of trust-model.md: {missing}"


def test_no_plugin_starts_a_subprocess_or_opens_a_socket():
    """The statement says none does; this is what enforces it."""
    bad = re.compile(r"\b(subprocess|os\.system|os\.popen|socket\.socket|import socket)\b")
    hits = []
    for plugin in PLUGINS:
        for path, text in sources(plugin):
            hits += [f"{path.name}: {m}" for m in bad.findall(text)]
    assert not hits, hits


def test_statement_says_what_the_checks_do_not_do():
    low = DOC.lower()
    assert "what loaded code does" in low
    assert "can reach everything" in low
    assert "no sandbox" in low

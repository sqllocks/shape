"""W1-14 deliverable 8: the documentation is true.

The examples in ``docs/CI.md`` parse, name flags that exist and match what the commands print; the
``helpUri`` of every SARIF rule points at a heading that exists; ``docs/CLI.md`` and
``docs/PROJECT.md`` mention the new flags and the ``ci:`` block."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

from shape.cli import ci
from shape.cli.introspect import core_commands

DOCS = Path(__file__).resolve().parents[2] / "docs"
CI_MD = (DOCS / "CI.md").read_text(encoding="utf-8")


def _blocks(text: str, lang: str) -> list[str]:
    return re.findall(rf"```{lang}\n(.*?)```", text, re.S)


def _slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9 -]", "", heading.lower().replace("`", "")).strip().replace(" ", "-")


def test_every_help_uri_points_at_a_heading_that_exists():
    for command, uri in ci.HELP.items():
        assert uri.startswith(ci.DOCS), command
        name, _, anchor = uri[len(ci.DOCS) :].partition("#")
        text = (DOCS / name).read_text(encoding="utf-8")
        assert (DOCS / name).is_file()
        if anchor:
            slugs = {_slug(h) for h in re.findall(r"^#+ (.+)$", text, re.M)}
            assert anchor in slugs, f"{uri}: no heading {anchor!r} in {name}"


def test_the_xml_example_is_well_formed_and_its_counts_are_right():
    (example,) = _blocks(CI_MD, "xml")
    suite = ET.fromstring(example).find("testsuite")
    cases = suite.findall("testcase")
    assert suite.get("name") == "shape diff"
    # the example shows two of the five cases: the attributes are those of the whole run
    assert int(suite.get("tests")) >= len(cases)


def test_the_sarif_example_has_the_properties_the_text_promises():
    blocks = [b for b in _blocks(CI_MD, "json") if '"runs"' in b]
    (doc,) = [json.loads(b) for b in blocks]
    assert doc["version"] == "2.1.0" and doc["$schema"]
    driver = doc["runs"][0]["tool"]["driver"]
    assert driver["name"] == "shape" and driver["rules"][0]["helpUri"].startswith(ci.DOCS)
    result = doc["runs"][0]["results"][0]
    assert result["partialFingerprints"]["shapeFinding/v1"] == ci.fingerprint(
        result["ruleId"], result["locations"][0]["logicalLocations"][0]["fullyQualifiedName"]
    )


def test_the_workflow_example_is_valid_yaml_and_uses_real_flags():
    (flow,) = [b for b in _blocks(CI_MD, "yaml") if "jobs:" in b]
    doc = yaml.safe_load(flow)
    steps = doc["jobs"]["shape"]["steps"]
    uses = {s["uses"].split("@")[0] for s in steps if "uses" in s}
    assert "github/codeql-action/upload-sarif" in uses and "dorny/test-reporter" in uses
    assert doc["permissions"]["security-events"] == "write"
    runs = " ".join(s["run"] for s in steps if "run" in s)
    diff = next(c for c in core_commands() if c.path == "diff")
    flags = {o for a in diff.parser._actions for o in a.option_strings}
    for flag in re.findall(r"(--[a-z-]+)", runs):
        assert flag in flags, flag
    sarif_step = next(s for s in steps if s.get("uses", "").startswith("github/codeql-action"))
    assert sarif_step["if"] == "always()"


def test_the_ci_block_example_is_accepted_by_the_project_schema():
    from shape.project import problems

    (block,) = [b for b in _blocks(CI_MD, "yaml") if b.startswith("ci:")]
    doc = {"format": "shape-project", "version": 1, "sources": {"a": {"path": "x"}}}
    doc.update(yaml.safe_load(block))
    assert problems(doc) == []


def test_the_documents_name_the_new_things():
    cli = (DOCS / "CLI.md").read_text(encoding="utf-8")
    for needle in ("--json", "--dry-run", "EXIT_CODES.md", "CI.md", "--junit", "--sarif"):
        assert needle in cli, needle
    project = (DOCS / "PROJECT.md").read_text(encoding="utf-8")
    assert "`ci.junit`" in project and "{command}" in project
    for needle in ("shape-result", "shape-dry-run", "partialFingerprints", "observe mode"):
        assert needle in CI_MD, needle

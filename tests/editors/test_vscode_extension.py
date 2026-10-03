"""W6-04 deliverables 1 and 2: the VS Code extension's bundled schemas match the ones the
Python package ships, and every snippet is a valid piece of ``shape.yml``."""

from __future__ import annotations

import json
import re
from pathlib import Path

import jsonschema
import pytest
import yaml

from shape.project import ProjectError, parse_project

ROOT = Path(__file__).resolve().parents[2]
EXT = ROOT / "editors" / "vscode"
SCHEMAS = ROOT / "src" / "shape" / "schemas"
PROJECT_SCHEMA = "shape-project-v1.schema.json"
GEN_SCHEMA = "generation-spec-v1.schema.json"

# Where each snippet's text goes in a project file: the whole file (root), beside a source
# (gates), as a source under
# `sources:` (its name is the snippet's first line), or inside a source. A snippet with no entry
# here fails the test, so a new one cannot ship untested.
CONTEXT = {
    "Shape project file": "root",
    "Shape source": "sources",
    "Shape baseline: previous run": "source",
    "Shape baseline: same weekday": "source",
    "Shape baseline: rolling window": "source",
    "Shape baseline: month end": "source",
    "Shape baseline: pinned artifact": "source",
    "Shape baseline: pinned ref": "source",
    "Shape column threshold": "source",
    "Shape gate: observe": "gates",
    "Shape gate: enforce": "gates",
    "Shape owner with annotations": "source",
}
ROOT_DOC = "format: shape-project\nversion: 1\n"


def snippets() -> dict[str, dict]:
    return json.loads((EXT / "snippets" / "shape.json").read_text(encoding="utf-8"))


def fill(body: str) -> str:
    """The text a user gets after accepting every placeholder as it is: the default of
    ``${n:default}`` or the first choice of ``${n|a,b|}``; ``$0`` and bare ``$n`` vanish."""
    body = re.sub(r"\$\{\d+\|([^,|}]+)[^}]*\|\}", r"\1", body)
    for _ in range(5):  # nested defaults
        body = re.sub(r"\$\{\d+:([^${}]*)\}", r"\1", body)
    body = re.sub(r"\$\{\d+\}|\$\d+", "", body)
    assert "$" not in body.replace("\\$", ""), f"placeholder left unfilled in: {body!r}"
    return body


def indent(text: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + ln if ln else ln for ln in text.splitlines()) + "\n"


def document(name: str, text: str) -> str:
    where = CONTEXT[name]
    if where == "root":  # the snippet is the whole file
        return text
    if where == "gates":  # beside a minimal source
        return ROOT_DOC + "sources:\n  s:\n    path: data/s\n" + text
    if where == "sources":
        return ROOT_DOC + "sources:\n" + indent(text, 2)
    return ROOT_DOC + "sources:\n  s:\n    path: data/s\n" + indent(text, 4)


def test_every_snippet_has_a_test_context_and_a_prefix():
    s = snippets()
    assert set(s) == set(CONTEXT)
    for name, snip in s.items():
        assert snip["prefix"].startswith("shape-"), name
        assert snip["description"], name


@pytest.mark.parametrize("name", sorted(CONTEXT))
def test_snippet_is_valid_against_the_schema_and_the_project_loader(name, tmp_path):
    body = snippets()[name]["body"]
    text = document(name, fill("\n".join(body) if isinstance(body, list) else body))
    doc = yaml.safe_load(text)
    jsonschema.Draft202012Validator(
        json.loads((SCHEMAS / PROJECT_SCHEMA).read_text(encoding="utf-8"))
    ).validate(doc)
    parse_project(text, tmp_path / "shape.yml")  # also the rules a schema cannot state


def test_the_checks_do_reject_a_broken_snippet(tmp_path):
    bad = document("Shape baseline: rolling window", "baseline:\n  kind: rolling_window\n")
    with pytest.raises(ProjectError):
        parse_project(bad, tmp_path / "shape.yml")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            yaml.safe_load(ROOT_DOC + "gates:\n  distribution: {mode: maybe}\n"),
            json.loads((SCHEMAS / PROJECT_SCHEMA).read_text(encoding="utf-8")),
        )


def test_snippets_cover_what_the_issue_lists():
    names = " ".join(snippets())
    for needle in ("source", "baseline", "threshold", "gate", "owner"):
        assert needle in names.lower()
    for kind in ("previous run", "same weekday", "rolling window", "month end", "pinned"):
        assert kind in names
    assert "observe" in names and "enforce" in names


def test_bundled_schemas_equal_the_packages():
    """The extension never carries a stale copy: this fails when ``npm run build`` was not run
    after a schema changed."""
    copies = sorted(p.name for p in (EXT / "schemas").glob("*.json"))
    assert PROJECT_SCHEMA in copies
    for name in copies:
        source = SCHEMAS / name
        assert source.is_file(), f"{name} is bundled but no longer in src/shape/schemas"
        assert (EXT / "schemas" / name).read_bytes() == source.read_bytes(), name
    if (SCHEMAS / GEN_SCHEMA).is_file():  # W1-06's schema: bundled as soon as it exists
        assert GEN_SCHEMA in copies


def test_manifest_matches_the_branding_contract():
    pkg = json.loads((EXT / "package.json").read_text(encoding="utf-8"))
    assert pkg["publisher"] == "sqllocks" and pkg["name"] == "shape"
    assert pkg["license"] == "MIT"
    assert pkg["displayName"] == "Shape by SQLLocks"
    assert "redhat.vscode-yaml" in pkg["extensionDependencies"]
    yv = pkg["contributes"]["yamlValidation"]
    assert yv == [{"fileMatch": ["shape.yml", "shape.yaml"], "url": f"./schemas/{PROJECT_SCHEMA}"}]
    assert pkg["contributes"]["configuration"]["properties"]["shape.path"]["type"] == "string"
    assert "telemetry" not in json.dumps(pkg).lower()
    branding = (ROOT / "docs" / "BRANDING.md").read_text(encoding="utf-8")
    assert "Shape by SQLLocks" in branding and "`sqllocks/shape`" in branding

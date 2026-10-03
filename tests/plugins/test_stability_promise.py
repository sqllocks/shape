"""W1-05: the written stability promise for plugin API v1 (``docs/plugins/stability.md``).

A promise nobody enforces is not one (plan 6.4), so each claim the page makes is tied to a test:
the group table lists every group in ``GROUPS`` (so a group added later fails here until it is
under the promise), the version rules are the host's rules, and the deprecation table matches
what the Protocols mark as deprecated.
"""

from __future__ import annotations

import dataclasses
import inspect
import re
import types
from pathlib import Path

import pytest

from shape.plugins import kit
from shape.plugins.api import v1
from shape.plugins.host import PluginLoadError, check_api

ROOT = Path(__file__).resolve().parents[2]
PAGE = ROOT / "docs" / "plugins" / "stability.md"


@pytest.fixture(scope="module")
def text() -> str:
    assert PAGE.exists(), "docs/plugins/stability.md is missing"
    return PAGE.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, f"stability.md has no '## {heading}' section"
    return m.group(1)


@pytest.mark.parametrize(
    "heading",
    [
        "What is stable in 1.x",
        "What counts as a breaking change",
        "What is not a breaking change",
        "Deprecation process",
        "Declaring the API version",
        "Groups added later",
        "Checking your plugin",
    ],
)
def test_required_sections(text: str, heading: str) -> None:
    assert _section(text, heading).strip()


def test_group_table_covers_every_group(text: str) -> None:
    rows = dict(re.findall(r"^\| `(shape\.[a-z_]+)` \| `([A-Za-z]+)` \|", text, re.M))
    assert rows == v1.GROUPS, (
        "the group table in stability.md must list exactly plugin API v1's groups "
        "(a new group goes under the promise by being added here, in the baseline, "
        "the kit and the reference plugins)"
    )


def test_group_table_names_each_groups_check(text: str) -> None:
    for group, check in kit.CHECKS.items():
        assert re.search(rf"`{re.escape(group)}`.*`{check.__name__}`", text), (group, check)


def test_every_protocol_member_is_covered_by_the_promise(text: str) -> None:
    stable = _section(text, "What is stable in 1.x")
    for needle in ("Protocol", "name", "GenerationContext", "SHAPE_API", "entry-point group"):
        assert needle in stable


def test_breaking_rules_match_the_comparison(text: str) -> None:
    breaking = _section(text, "What counts as a breaking change")
    for needle in (
        "removing",
        "required",
        "default",
        "return type",
        "major version",
        "scripts/plugin_api_compat.py",
    ):
        assert needle in breaking, needle
    assert (ROOT / "scripts" / "plugin_api_compat.py").exists()
    assert (ROOT / "tests" / "plugins" / "api_v1_baseline.json").exists()


# -- how a plugin declares the API version it targets ---------------------------------------


def _module(api: object) -> types.ModuleType:
    mod = types.ModuleType("promise_mod")
    if api is not None:
        mod.SHAPE_API = api  # type: ignore[attr-defined]
    return mod


def test_declaring_is_a_module_level_string(text: str) -> None:
    section = _section(text, "Declaring the API version")
    assert 'SHAPE_API = "1.0"' in section
    assert kit.check_module_api(_module("1.0")) == "1.0"
    with pytest.raises(kit.ConformanceError):
        kit.check_module_api(_module(None))
    with pytest.raises(kit.ConformanceError):
        kit.check_module_api(_module(1.0))


@pytest.mark.parametrize("declared", ["1.0", "1.1", "1.99"])
def test_host_loads_any_1x_minor(declared: str) -> None:
    assert check_api(declared) == declared


@pytest.mark.parametrize("declared", ["2.0", "0.9", "x", "", None, 1.0])
def test_host_rejects_other_majors_and_malformed(declared: object) -> None:
    with pytest.raises(PluginLoadError):
        check_api(declared)


def test_minor_rule_is_stated(text: str) -> None:
    section = _section(text, "Declaring the API version")
    assert "any minor" in section and "major" in section


# -- deprecation ----------------------------------------------------------------------------


def _marked_deprecated() -> set[str]:
    found: set[str] = set()
    for pname, proto in v1.PROTOCOLS.items():
        if "deprecated" in (inspect.getdoc(proto) or "").lower():
            found.add(pname)
        for name, obj in vars(proto).items():
            if callable(obj) and "deprecated" in (inspect.getdoc(obj) or "").lower():
                found.add(f"{pname}.{name}")
    for n in v1.__all__:
        obj = getattr(v1, n)
        if dataclasses.is_dataclass(obj) and "deprecated" in (inspect.getdoc(obj) or "").lower():
            found.add(n)
    return found


def test_deprecation_table_matches_what_the_api_marks(text: str) -> None:
    section = _section(text, "Deprecation process")
    listed = set(re.findall(r"^\| `([A-Za-z_.]+)` \|", section, re.M))
    assert listed == _marked_deprecated(), (
        "every member whose docstring says 'Deprecated' must be listed in the deprecation "
        "table of stability.md, and every listed member must be marked in its docstring"
    )


def test_deprecation_process_states_the_rules(text: str) -> None:
    section = _section(text, "Deprecation process")
    for needle in ("remains", "1.x", "next major", "release notes", "table"):
        assert needle in section, needle


# -- groups added later ---------------------------------------------------------------------


def test_new_group_rules_are_listed(text: str) -> None:
    section = _section(text, "Groups added later")
    for needle in (
        "GROUPS",
        "PROTOCOLS",
        "api_v1_baseline.json",
        "CHECKS",
        "reference plugin",
        "stability.md",
    ):
        assert needle in section, needle


# -- the page is reachable and honest -------------------------------------------------------


def test_other_docs_link_to_the_promise() -> None:
    for rel in ("docs/plugins/authoring.md", "docs/plugins/api-v1.md", "docs/API_STABILITY.md"):
        assert "stability.md" in (ROOT / rel).read_text(encoding="utf-8"), rel


def test_kit_commands_in_the_page_exist(text: str) -> None:
    check = _section(text, "Checking your plugin")
    assert "python -m shape.plugins.kit" in check
    for name in ("check_plugin", "check_installed", "check_module_api"):
        assert name in check and hasattr(kit, name)


def test_page_does_not_name_non_public_things(text: str) -> None:
    low = text.lower()
    assert "spindle" not in low

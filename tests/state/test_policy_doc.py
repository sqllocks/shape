"""The policy document says what the code does (W1-01, issue 55, items 1 and 10)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from shape import compat
from shape.compat import KINDS, Deprecation, Kind

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "specs" / "STATE_AND_COMPATIBILITY.md"
TEXT = DOC.read_text(encoding="utf-8")
CHANGELOG = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")

HEADINGS = [
    "## 1. What every persisted file declares",
    "## 2. The read-old promise",
    "## 3. A file from a newer release",
    "## 4. Migrations",
    "## 5. Unknown fields",
    "## 6. Signed artifacts and migrations",
    "## 7. Canonical forms and content ids",
    "## 8. Dates, decimals, locale",
    "## 9. Deprecation",
    "## 10. The time-capsule corpus",
    "## 11. Changing a persisted format: the checklist",
]


@pytest.mark.parametrize("heading", HEADINGS)
def test_the_document_has_every_section(heading: str) -> None:
    assert heading in TEXT


def test_the_support_window_table_is_the_one_the_code_publishes() -> None:
    match = re.search(
        r"<!-- support-table:start -->\n(.*?)\n<!-- support-table:end -->", TEXT, re.DOTALL
    )
    assert match, "the support window table markers are missing"
    assert match.group(1) == compat.render_support_table(), (
        "regenerate the table with `python -c 'from shape import compat; "
        "print(compat.render_support_table())'`"
    )


def test_every_kind_is_in_the_kinds_table() -> None:
    section = TEXT.split("## 1. What every persisted file declares")[1].split("## 2.")[0]
    for name in KINDS:
        assert f"`{name}`" in section, name


@pytest.mark.parametrize(
    "needle",
    [
        "`format`",
        "`version`",
        "`shape_version`",
        "`min_shape_version`",
        "SHAPE_STRICT_FORMATS",
        "strict_formats",
        "shape migrate",
        "shape-migrate",
        "migrated_from",
        "source_content_id",
        "FormatDeprecationWarning",
        "UnsupportedVersionError",
        "state_vectors.json",
        "tests/timecapsule",
        "manifest.sig",
        "Ed25519",
        "retired",
        "rotation",
        "2.0",
    ],
)
def test_the_document_names_the_mechanisms(needle: str) -> None:
    assert needle in TEXT


def test_the_read_old_promise_is_stated_in_the_issue_words() -> None:
    assert re.search(
        r"every 1\.x and later release reads every format version ever released", TEXT, re.I
    )
    assert "only in a new major release" in TEXT
    assert "offline" in TEXT and "announced" in TEXT


def test_relative_links_resolve() -> None:
    for target in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", TEXT):
        if "://" in target:
            continue
        assert (DOC.parent / target).resolve().exists(), target


def test_other_documents_point_at_the_policy() -> None:
    for name in ("docs/API_STABILITY.md", "docs/RELEASE_POLICY.md", "docs/SIGNING.md"):
        assert "STATE_AND_COMPATIBILITY.md" in (ROOT / name).read_text(encoding="utf-8"), name


def test_the_changelog_announces_the_policy_and_the_command() -> None:
    unreleased = CHANGELOG.split("## Unreleased")[1]
    assert "STATE_AND_COMPATIBILITY.md" in unreleased
    assert "shape migrate" in unreleased


def missing_changelog_entries(kinds: dict[str, Kind], changelog: str) -> list[str]:
    """The deprecated versions that the changelog does not announce (under "Deprecated", naming the
    kind, its version and the release that removes it)."""
    section = changelog.split("### Deprecated")[1:] or [""]
    announced = "\n".join(section)
    out = []
    for k in kinds.values():
        for version, dep in k.deprecated.items():
            line = next((x for x in announced.splitlines() if f"`{k.name}`" in x), "")
            if f"version {version}" not in line or dep.removed_in not in line:
                out.append(f"{k.name} {version}")
    return out


def test_every_deprecation_is_announced_in_the_changelog() -> None:
    assert missing_changelog_entries(KINDS, CHANGELOG) == []


def test_the_changelog_check_finds_a_missing_announcement() -> None:
    sample = Kind(
        name="sample",
        label="sample",
        format="shape-sample",
        current=2,
        first_release={1: "1.0.0", 2: "1.0.0"},
        deprecated={
            1: Deprecation(since="1.4.0", removed_in="2.0.0", migrate_with="shape migrate")
        },
    )
    assert missing_changelog_entries({"sample": sample}, "## Unreleased\n") == ["sample 1"]
    announced = (
        "## Unreleased\n### Deprecated\n"
        "- `sample` version 1 is deprecated; Shape 2.0.0 stops reading it directly "
        "(`shape migrate` still does).\n"
    )
    assert missing_changelog_entries({"sample": sample}, announced) == []


def test_a_deprecation_must_name_a_major_release_and_a_way_out() -> None:
    for k in KINDS.values():
        for dep in k.deprecated.values():
            assert re.fullmatch(r"\d+\.0\.0", dep.removed_in), dep
            assert dep.migrate_with
            assert compat.parse_release(dep.since) is not None

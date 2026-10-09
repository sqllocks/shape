"""W7-01: the Fabric platform inventory and the guard that keeps preview APIs out of the code.

Offline: nothing here opens a socket. ``scripts/fabric_platform.py`` is the guard; these tests
check the table parser, the scanner (against fixture modules) and the real repository.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures" / "fabric_platform"
_spec = importlib.util.spec_from_file_location(
    "fabric_platform", ROOT / "scripts/fabric_platform.py"
)
assert _spec and _spec.loader
fp = importlib.util.module_from_spec(_spec)
sys.modules["fabric_platform"] = fp
_spec.loader.exec_module(fp)

HEADER = "| " + " | ".join(fp.COLUMNS) + " |\n|---|---|---|---|---|---|---|\n"
LEARN = "https://learn.microsoft.com/en-us/rest/api/fabric/"


def table(*rows: str) -> str:
    return HEADER + "".join(r + "\n" for r in rows)


def rest_row(surface="GET /v1/workspaces", status="GA", checked="2026-10-03"):
    return f"| rest | {surface} | {status} | n/a | {LEARN} | {checked} | m |"


# ---- the table ---------------------------------------------------------------------------------


def test_the_inventory_parses_and_every_row_is_complete():
    rows = fp.parse_table((ROOT / "docs/FABRIC_PLATFORM.md").read_text(encoding="utf-8"))
    assert rows
    assert {r.kind for r in rows} == set(fp.KINDS)
    for r in rows:
        assert r.status in fp.STATUSES and r.source.startswith("https://learn.microsoft.com/")


def test_parse_accepts_a_minimal_table():
    (row,) = fp.parse_table(table(rest_row()))
    assert (row.kind, row.surface, row.status, row.checked) == (
        "rest",
        "GET /v1/workspaces",
        "GA",
        date(2026, 10, 3),
    )


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("no table here\n", "no inventory table"),
        ("| Kind | Surface |\n|---|---|\n", "header must be exactly"),
        (table(), "no rows"),
        (table("| rest | GET /v1/x | GA | n/a |"), "columns"),
        (table(rest_row(status="stable")), "Status"),
        (table(rest_row(checked="03/10/2026")), "ISO date"),
        (table(rest_row(checked="2026-02-30")), "ISO date"),
        (table(rest_row(surface="/v1/workspaces")), "METHOD /v1/path"),
        (table(rest_row(surface="GET /workspaces")), "METHOD /v1/path"),
        (table(rest_row().replace("| rest |", "| soap |")), "Kind"),
        (table(rest_row().replace(LEARN, "https://example.com/")), "Microsoft Learn"),
        (table(rest_row().replace("| n/a |", "| 2026-01-01 |")), "runtimes only"),
        (table(rest_row(), rest_row()), "already listed"),
        (
            table(
                rest_row("GET /v1/workspaces/{a}/items"), rest_row("GET /v1/workspaces/{b}/items")
            ),
            "already listed",
        ),
        (table(f"| runtime | 2.0 | GA | soon | {LEARN} | 2026-10-03 | m |"), "ISO date"),
        (table(f"| runtime | 2.0 | GA | n/a | {LEARN} | 2026-10-03 | m |"), "ISO date"),
        (table(rest_row().replace("| m |", "|  |")), "Used by"),
    ],
)
def test_parse_rejects_a_malformed_table(text, message):
    with pytest.raises(fp.TableError, match=message):
        fp.parse_table(text)


def test_parse_accepts_none_announced_for_a_runtime():
    (row,) = fp.parse_table(
        table(f"| runtime | 2.0 | GA | none announced | {LEARN} | 2026-10-03 | m |")
    )
    assert row.end_of_support == "none announced"


def test_parse_stops_at_the_end_of_the_table():
    rows = fp.parse_table(table(rest_row()) + "\nText after the table | not | a row\n")
    assert len(rows) == 1


# ---- the scanner, on fixtures ------------------------------------------------------------------


def fixture_problems(name: str) -> list[str]:
    rows = fp.parse_table((FIXTURES / "inventory.md").read_text(encoding="utf-8"))
    return fp.check(rows, fp.scan_file(FIXTURES / name, ROOT))


def test_a_listed_preview_endpoint_is_reported_with_file_line_and_surface():
    (problem,) = fixture_problems("calls_preview.py")
    assert problem.startswith("tests/docs/fixtures/fabric_platform/calls_preview.py:7:")
    assert "GET /v1/workspaces/{workspace_id}/previewthings" in problem
    assert "preview" in problem


def test_an_unlisted_endpoint_is_reported():
    (problem,) = fixture_problems("calls_unlisted.py")
    assert "calls_unlisted.py:7:" in problem
    assert "POST /v1/workspaces/{workspace_id}/unlistedthings" in problem
    assert "not in the inventory" in problem


def test_an_unlisted_item_type_is_reported():
    (problem,) = fixture_problems("unlisted_item_type.py")
    assert "unlisted_item_type.py:5:" in problem
    assert "item-type KQLDashboard" in problem and "not in the inventory" in problem


def test_a_listed_ga_path_built_by_an_fstring_with_two_parameters_is_accepted():
    found = fp.scan_file(FIXTURES / "ga_two_params.py", ROOT)
    assert [(f.kind, f.surface) for f in found] == [
        ("rest", "GET /v1/workspaces/{ws}/gizmos/{item}")
    ]
    assert fixture_problems("ga_two_params.py") == []


def test_the_scanner_ignores_what_is_not_a_fabric_call():
    src = '''
"""Mentions /v1/rest/mgmt and https://api.fabric.microsoft.com/v1/x in prose."""
FABRIC_API = "https://api.fabric.microsoft.com/v1"
col = {"name": "a", "type": "integer"}
other = "https://example.com/v1/workspaces"
arg = dict(type="Notebook")
'''
    assert fp.scan_source(src, "x.py") == []


def test_the_scanner_reads_every_way_the_code_names_a_path():
    src = """
from shape.scale.http import FABRIC_API
def f(h, ws, kind, table, db):
    h.request("GET", FABRIC_API + "/workspaces")
    h.request("PATCH", f"{FABRIC_API}/workspaces/{ws}/git/myGitCredentials?x=1")
    url = f"{FABRIC_API}/workspaces/{ws}/items?type={kind}"
    other = f"{FABRIC_API}/workspaces/{ws}/items?type=Lakehouse&top=1"
    a = f"https://{db}/v1/rest/query"
    b = f"{db}/v1/rest/ingest/{quote(table, safe='')}/{db.name}?streamFormat=JSON"
    c = {"displayName": "n", "type": "Environment"}
"""
    got = {(f.kind, f.surface) for f in fp.scan_source(src, "x.py")}
    assert got == {
        ("rest", "GET /v1/workspaces"),
        ("rest", "PATCH /v1/workspaces/{ws}/git/myGitCredentials"),
        ("rest", "/v1/workspaces/{ws}/items"),
        ("item-type", "Lakehouse"),
        ("rest", "/v1/rest/query"),
        ("rest", "/v1/rest/ingest/{table}/{name}"),
        ("item-type", "Environment"),
    }


def test_a_path_without_a_method_matches_a_non_ga_row_of_that_path():
    rows = fp.parse_table(
        table(rest_row("GET /v1/things"), rest_row("POST /v1/things", status="preview"))
    )
    found = fp.scan_source('u = "https://api.fabric.microsoft.com/v1/things"\n', "x.py")
    (problem,) = fp.check(rows, found)
    assert "preview" in problem


def test_a_method_that_is_not_listed_for_a_path_is_reported():
    rows = fp.parse_table(table(rest_row("GET /v1/things")))
    found = fp.scan_source(
        'def f(h):\n    h.request("DELETE", "https://api.fabric.microsoft.com/v1/things")\n', "x.py"
    )
    (problem,) = fp.check(rows, found)
    assert "DELETE /v1/things" in problem and "not in the inventory" in problem


def test_a_retired_surface_is_reported():
    rows = fp.parse_table(table(rest_row("GET /v1/things", status="retired")))
    found = fp.scan_source('u = "https://api.fabric.microsoft.com/v1/things"\n', "x.py")
    (problem,) = fp.check(rows, found)
    assert "retired" in problem


# ---- runtimes ----------------------------------------------------------------------------------


def runtime_rows():
    return fp.parse_table((FIXTURES / "inventory.md").read_text(encoding="utf-8"))


def test_a_runtime_named_in_the_docs_but_not_listed_is_reported():
    found = fp.scan_runtimes("Use Runtime 3.0 for this.\n", "docs/X.md")
    (problem,) = fp.check(runtime_rows(), found)
    assert problem.startswith("docs/X.md:1:") and "runtime 3.0" in problem


def test_a_listed_supported_runtime_passes():
    found = fp.scan_runtimes(
        "Set the Environment to Runtime 2.0.\nFabric runtime 2.0 too.\n", "d.md"
    )
    assert len(found) == 2 and fp.check(runtime_rows(), found) == []


def test_a_runtime_past_its_end_of_support_date_is_reported():
    found = fp.scan_runtimes("Runtime 1.3 also works.\n", "plugins/p/README.md")
    (problem,) = fp.check(runtime_rows(), found)
    assert "ended support on 2026-09-30" in problem and "2026-10-03" in problem


def test_end_of_support_on_the_checked_date_is_not_earlier():
    rows = fp.parse_table(table(f"| runtime | 1.3 | GA | 2026-10-03 | {LEARN} | 2026-10-03 | m |"))
    assert fp.check(rows, fp.scan_runtimes("Runtime 1.3\n", "d.md")) == []


def test_a_language_runtime_is_not_a_fabric_runtime():
    assert fp.scan_runtimes("the Python runtime 3.11 and the .NET runtime 8.0\n", "d.md") == []


# ---- the repository ----------------------------------------------------------------------------


def test_the_guard_passes_on_the_current_code_and_docs():
    assert fp.run(ROOT) == []


def test_the_guard_scans_the_code_it_is_meant_to_scan():
    files = {p.relative_to(ROOT).as_posix() for p in fp.python_files(ROOT)}
    assert "plugins/shape-fabric/src/shape_fabric/fabric_api.py" in files
    assert "plugins/shape-fabric/src/shape_fabric/kusto.py" in files
    assert "src/shape/scale/spark.py" in files
    assert "plugins/shape-fabric/tests/test_live_git_sync.py" in files


def test_the_inventory_covers_the_surfaces_found_in_the_code():
    rows = fp.parse_table((ROOT / "docs/FABRIC_PLATFORM.md").read_text(encoding="utf-8"))
    keys = {r.key for r in rows}
    found = {f.key for p in fp.python_files(ROOT) for f in fp.scan_file(p, ROOT) if f.method}
    assert found and found <= keys
    for needed in (
        "rest POST /v1/workspaces/{}/git/updateFromGit",
        "rest POST /v1/workspaces/{}/git/commitToGit",
        "rest GET /v1/workspaces/{}/git/status",
        "item-type Notebook",
        "storage https://onelake.dfs.fabric.microsoft.com",
    ):
        assert needed in keys


def test_a_new_preview_call_in_the_code_fails_the_guard(tmp_path):
    root = tmp_path
    (root / "docs").mkdir()
    (root / "docs/FABRIC_PLATFORM.md").write_text(table(rest_row("GET /v1/workspaces")))
    src = root / "src/shape/scale"
    src.mkdir(parents=True)
    (src / "new.py").write_text(
        'def f(h):\n    h.request("GET", "https://api.fabric.microsoft.com/v1/preview")\n'
    )
    (problem,) = fp.run(root)
    assert problem.startswith("src/shape/scale/new.py:2:")


# ---- the page ----------------------------------------------------------------------------------


def test_the_last_recorded_run_line_is_not_run_yet_or_comes_from_a_result():
    lines = [
        ln
        for ln in (ROOT / "docs/FABRIC_PLATFORM.md").read_text(encoding="utf-8").splitlines()
        if ln.startswith("Last recorded run:")
    ]
    assert len(lines) == 1
    value = lines[0].split(":", 1)[1].strip()
    assert value == "not run yet" or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}, Shape \d+\.\d+\.\d+\S*, (passed|failed)", value
    ), value


def test_the_page_documents_git_sync_and_its_secrets():
    text = (ROOT / "docs/FABRIC_PLATFORM.md").read_text(encoding="utf-8")
    assert "## Git sync" in text
    for name in (
        "FABRIC_TENANT_ID",
        "FABRIC_CLIENT_ID",
        "FABRIC_CLIENT_SECRET",
        "FABRIC_WORKSPACE_ID",
        "FABRIC_GIT_REMOTE",
        "FABRIC_GIT_TOKEN",
    ):
        assert name in text


def test_contributing_and_the_plugin_readme_point_at_the_inventory():
    assert "FABRIC_PLATFORM.md" in (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
    readme = (ROOT / "plugins/shape-fabric/README.md").read_text(encoding="utf-8")
    assert "../../docs/FABRIC_PLATFORM.md" in readme

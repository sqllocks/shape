"""P8-03: the documentation site (T-24).

The site is built with ``mkdocs build --strict`` from ``mkdocs.yml``; ``scripts/mkdocs_hooks.py``
generates the CLI reference and the performance page and points links to files outside the site at
the repository; ``scripts/check_doc_links.py`` is the link check. These tests cover the
configuration, the generated pages and the link check without needing mkdocs installed.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT / "docs"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


hooks = _load("mkdocs_hooks")
links = _load("check_doc_links")
user_facing = _load("check_user_facing")


def _config() -> dict[str, Any]:
    data = yaml.load((ROOT / "mkdocs.yml").read_text(encoding="utf-8"), Loader=yaml.UnsafeLoader)
    assert isinstance(data, dict)
    return data


def _nav_pages(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [p for item in node for p in _nav_pages(item)]
    if isinstance(node, dict):
        return [p for v in node.values() for p in _nav_pages(v)]
    return []


def _site_sources() -> set[str]:
    return {
        p.relative_to(DOCS).as_posix()
        for p in DOCS.rglob("*.md")
        if p.relative_to(DOCS).parts[0] not in links.SKIP_DOCS
    }


# --- configuration -------------------------------------------------------------------------


def test_config_is_strict_and_excludes_internal_material() -> None:
    cfg = _config()
    assert cfg["docs_dir"] == "docs"
    assert cfg["strict"] is True
    assert cfg["theme"]["name"] == "material"
    excluded = cfg["exclude_docs"].split()
    assert "plans/" in excluded and "talks/" in excluded
    assert cfg["hooks"] == ["scripts/mkdocs_hooks.py"]
    assert cfg["repo_url"] == links.REPO_URL
    for key in ("omitted_files", "absolute_links", "unrecognized_links", "anchors"):
        assert cfg["validation"][key] == (
            "info" if key == "omitted_files" else "warn"
        )  # warnings fail a --strict build


def test_every_site_page_is_in_the_nav_exactly_once() -> None:
    nav = _nav_pages(_config()["nav"])
    assert len(nav) == len(set(nav)), "a page is listed twice in the nav"
    generated = {p.removeprefix("docs/") for p in links.GENERATED}
    assert set(nav) - generated <= _site_sources()
    omitted = _site_sources() - set(nav)
    assert all(
        len((DOCS / page).read_text().split()) < 220
        or page in {"SHAPE_MANIFESTO.md", "NOT_BUILDING.md"}
        for page in omitted
    )
    assert generated <= set(nav)


def test_generated_pages_agree_between_the_hook_and_the_link_check() -> None:
    assert set(links.GENERATED) == {
        f"docs/{hooks.CLI_PAGE}",
        f"docs/{hooks.PERFORMANCE_PAGE}",
        "docs/reference/api.md",
        "docs/CONTRIBUTING.md",
        "docs/GOVERNANCE.md",
        "docs/SECURITY.md",
        "docs/CODE_OF_CONDUCT.md",
        "docs/CHANGELOG.md",
    }
    for page in links.GENERATED:
        assert not (ROOT / page).exists(), "a generated page must not also have a source file"


def test_docs_extra_pins_mkdocs_as_t24_says() -> None:
    import tomllib

    extras = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "optional-dependencies"
    ]
    assert extras["docs"] == [
        "mkdocs>=1.6,<2",
        "mkdocs-material>=9.5,<10",
        "mkdocstrings[python]>=1,<2",
        "mike>=2,<3",
    ]


# --- generated CLI reference ---------------------------------------------------------------


def _surface_paths(name: str, node: dict[str, Any], prefix: str) -> list[tuple[str, str]]:
    path = f"{prefix} {name}"
    out = [(path, node["stability"])]
    for sub, child in node["subcommands"].items():
        out += _surface_paths(sub, child, path)
    return out


def test_cli_reference_documents_every_core_command_with_its_stability() -> None:
    page = hooks.cli_reference_markdown()
    baseline = json.loads((ROOT / "tests" / "cli" / "cli_surface_v1.json").read_text("utf-8"))
    for name, node in baseline["commands"].items():
        for path, level in _surface_paths(name, node, "shape"):
            heading = f"`{path}`\n\nStability: **{level}**."
            assert heading in page, f"{path} is missing from the CLI reference"


def test_cli_reference_holds_the_help_text_of_each_command() -> None:
    from shape.cli.main import _build_parser

    parser = hooks._subparsers(_build_parser())
    assert parser is not None
    page = hooks.cli_reference_markdown()
    for name in ("profile", "check", "diff", "generate"):
        assert hooks._help(parser.choices[name]) in page


def test_cli_reference_is_deterministic_and_wraps_at_a_fixed_width(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COLUMNS", "40")
    narrow = hooks.cli_reference_markdown()
    monkeypatch.setenv("COLUMNS", "250")
    assert hooks.cli_reference_markdown() == narrow
    import os

    assert os.environ["COLUMNS"] == "250"  # restored after building


# --- generated performance page ------------------------------------------------------------


def _results() -> dict[str, Any]:
    data = json.loads(hooks.RESULTS.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _verifier(status: str, code: int) -> dict[str, Any]:
    return {"status": status, "exit_code": code, "command": "verify"}


def test_performance_page_from_committed_results_shows_only_product_numbers() -> None:
    results = _results()
    page = hooks.performance_markdown(results)
    assert results["generated_utc"] in page and results["shape_commit"] in page
    if results["shape"] is None:
        assert "No product measurement has been recorded yet" in page
    # Numbers recorded for other implementations in the harness never reach the page.
    for key, tool in results.items():
        if key in ("shape", "kernel_microbench") or not isinstance(tool, dict):
            continue
        for w in (tool.get("workloads") or {}).values():
            if isinstance(w.get("median_s"), float):
                assert f"{w['median_s']:.3f}" not in page


def test_performance_page_counts_a_workload_only_after_its_verifier_passed() -> None:
    results = copy.deepcopy(_results())
    results["shape"] = {
        "workloads": {
            "profile:ok.csv": {
                "kind": "profile",
                "dataset": "ok.csv",
                "verifier": _verifier("pass", 0),
                "median_s": 1.23456,
                "median_s_1t": 2.5,
                "peak_rss_mb": 100.4,
            },
            "profile:bad.csv": {
                "kind": "profile",
                "dataset": "bad.csv",
                "verifier": _verifier("fail", 1),
                "median_s": 9.87654,
            },
            "profile:odd.csv": {
                "kind": "profile",
                "dataset": "odd.csv",
                "verifier": _verifier("pass", 3),  # an exit code other than 0 is not a pass
                "median_s": 7.65432,
            },
            "generate:retail:small": {
                "kind": "generate",
                "domain": "retail",
                "scale": "small",
                "verifier": None,
                "median_s": 5.55555,
            },
        }
    }
    page = hooks.performance_markdown(results)
    assert "| profile `ok.csv` | pass | 1.235 | 2.500 | — | 100 |" in page
    assert "| profile `bad.csv` | not counted | — | — | — | — |" in page
    assert "| profile `odd.csv` | not counted | — | — | — | — |" in page
    assert "| generate `retail` at `small` | not counted | — | — | — | — |" in page
    for hidden in ("9.877", "7.654", "5.556"):
        assert hidden not in page


def test_performance_page_omits_twin_comparisons_and_ratios() -> None:
    results = copy.deepcopy(_results())
    results["kernel_microbench"]["kernels"] = {
        "good": {
            "rows": 10,
            "native_s": 0.5,
            "reference_s": 2.0,
            "equivalent_to_reference": True,
            "speedup_vs_reference": 4.0,
        },
        "bad": {
            "rows": 10,
            "native_s": 0.1234,
            "reference_s": 2.0,
            "equivalent_to_reference": False,
        },
    }
    page = hooks.performance_markdown(results)
    assert "4.0x" not in page
    assert "reference_s" not in page
    assert "Kernel microbenchmarks" not in page
    assert "0.1234" not in page


def test_performance_page_without_any_measurement() -> None:
    page = hooks.performance_markdown({"shape": None})
    assert "No product measurement has been recorded yet" in page
    assert "Kernel microbenchmarks" not in page


def test_generated_pages_name_no_other_product() -> None:
    for page in (hooks.cli_reference_markdown(), hooks.performance_markdown(_results())):
        assert not user_facing.names_refengine(page)


# --- links to files outside the site -------------------------------------------------------


def _is_site_file(rel: str) -> bool:
    return (
        rel in _site_sources()
        or (DOCS / rel).is_file()
        and rel.split("/")[0] not in (links.SKIP_DOCS)
    )


def _rewrite(text: str, page: str = "DBT.md") -> str:
    return str(hooks.rewrite_links(text, page, DOCS, _is_site_file, links.REPO_URL))


def test_links_outside_the_site_point_at_the_repository() -> None:
    blob = f"{links.REPO_URL}/blob/main"
    tree = f"{links.REPO_URL}/tree/main"
    assert _rewrite("[c](../CHANGELOG.md)") == f"[c]({blob}/CHANGELOG.md)"
    assert _rewrite("[p](plans/COMPLETION_PLAN.md#x)") == (
        f"[p]({blob}/docs/plans/COMPLETION_PLAN.md#x)"
    )
    assert _rewrite("[e](../examples/)") == f"[e]({tree}/examples)"
    assert _rewrite("[h](../../CHANGELOG.md)", "specs/STATE_AND_COMPATIBILITY.md") == (
        f"[h]({blob}/CHANGELOG.md)"
    )
    assert _rewrite('[t](../CHANGELOG.md "title")') == f'[t]({blob}/CHANGELOG.md "title")'


def test_site_links_external_links_and_missing_targets_are_left_alone() -> None:
    for text in (
        "[a](CLI.md)",
        "[a](CLI.md#exit-codes)",
        "[a](#local)",
        "[a](https://example.com/x)",
        "[a](mailto:x@example.com)",
        "[a](../no/such/file.md)",
    ):
        assert _rewrite(text) == text


def test_links_inside_fenced_code_are_left_alone() -> None:
    text = "```\n[c](../CHANGELOG.md)\n```\n[c](../CHANGELOG.md)"
    out = _rewrite(text).split("\n")
    assert out[1] == "[c](../CHANGELOG.md)"
    assert out[3].startswith(f"[c]({links.REPO_URL}/blob/main/")


# --- link check ----------------------------------------------------------------------------


def test_link_check_passes_on_the_committed_sources(capsys: pytest.CaptureFixture[str]) -> None:
    assert links.main([]) == 0
    assert "0 broken link(s)" in capsys.readouterr().out


def _tree(tmp_path: Path) -> Path:
    (tmp_path / "docs" / "sub").mkdir(parents=True)
    (tmp_path / "docs" / "plans").mkdir()
    (tmp_path / "README.md").write_text("[ok](docs/a.md)\n", encoding="utf-8")
    (tmp_path / "docs" / "a.md").write_text(
        "# A\n[b](sub/b.md#x) [gen](reference/cli.md) [web](https://example.com/nope)\n"
        "```\n[skip](missing-in-code.md)\n```\n[ref]: sub/b.md\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "sub" / "b.md").write_text("# B\n[up](../a.md)\n", encoding="utf-8")
    (tmp_path / "docs" / "plans" / "p.md").write_text("[internal](gone.md)\n", encoding="utf-8")
    return tmp_path


def test_link_check_on_sources(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    assert links.check_sources(root) == []
    (root / "docs" / "sub" / "b.md").write_text("[x](../gone.md)\n[r]: ../also-gone.md\n", "utf-8")
    (root / "README.md").write_text("[x](docs/missing.md#top)\n", encoding="utf-8")
    assert links.check_sources(root) == [
        "README.md:1: link target does not exist: docs/missing.md#top",
        "docs/sub/b.md:1: link target does not exist: ../gone.md",
        "docs/sub/b.md:2: link target does not exist: ../also-gone.md",
    ]
    assert links.main(["--root", str(root)]) == 1


def _site(tmp_path: Path) -> Path:
    site = tmp_path / "site"
    (site / "CLI").mkdir(parents=True)
    (site / "assets").mkdir()
    (site / "assets" / "x.css").write_text("", encoding="utf-8")
    (site / "CLI" / "index.html").write_text(
        '<h2 id="exit-codes">Exit</h2><a href="../">home</a>', encoding="utf-8"
    )
    (site / "index.html").write_text(
        '<link rel="stylesheet" href="assets/x.css"><a href="CLI/#exit-codes">x</a>'
        '<a href="#top" id="top">t</a><a href="https://example.com/away">e</a>'
        f'<a href="{links.REPO_URL}/blob/main/README.md">r</a>'
        f'<a href="{links.REPO_URL}/tree/main/docs">d</a>',
        encoding="utf-8",
    )
    (site / "404.html").write_text('<a href="/shape/elsewhere/">x</a>', encoding="utf-8")
    return site


def test_link_check_on_a_built_site(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    site = _site(tmp_path)
    assert links.check_site(site, root) == []
    assert links.main(["--root", str(root), "--site", str(site)]) == 0
    (site / "index.html").write_text(
        '<a href="CLI/#no-such">a</a><a href="GONE/">b</a><a href="../../outside">c</a>'
        f'<a href="{links.REPO_URL}/blob/main/no/such.py">d</a><a href="#nowhere">e</a>',
        encoding="utf-8",
    )
    assert links.check_site(site, root) == [
        "index.html: no anchor #no-such on the target: CLI/#no-such",
        "index.html: link target is not in the site: GONE/",
        "index.html: link target is not in the site: ../../outside",
        f"index.html: repository path does not exist: {links.REPO_URL}/blob/main/no/such.py",
        "index.html: no anchor #nowhere on the target: #nowhere",
    ]
    assert links.main(["--root", str(root), "--site", str(site)]) == 1


def test_link_check_refuses_a_missing_site(tmp_path: Path) -> None:
    assert links.main(["--site", str(tmp_path / "nope")]) == 2


def test_user_facing_check_covers_a_built_site(tmp_path: Path, monkeypatch) -> None:
    # The word: the reference engine's name when REFENGINE_NAME is set (matched by the stored
    # digest); otherwise a dummy word, with the scanner pointed at the dummy's digest.
    word = os.environ.get("REFENGINE_NAME") or "zebrafy"
    if not os.environ.get("REFENGINE_NAME"):
        digest, real = hashlib.sha256(word.encode()).hexdigest(), user_facing.names_refengine
        monkeypatch.setattr(user_facing, "names_refengine", lambda t: real(t, digest, len(word)))
    site = tmp_path / "site"
    (site / "a").mkdir(parents=True)
    (site / "a" / "index.html").write_text("<p>clean</p>", encoding="utf-8")
    assert user_facing.main(["--site", str(site)]) == 0
    (site / "search.json").write_bytes(b'{"text": "ported from ' + word.encode() + b'"}')
    assert user_facing.main(["--site", str(site)]) == 1

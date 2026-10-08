"""mkdocs hooks for the documentation site (P8-03, T-24).

Two pages are generated at build time, so they cannot drift from the code:

* ``reference/cli.md``: every core command of ``shape.cli.main``, with its stability level and its
  ``--help`` text, walked from the argparse parser (plugin commands are documented on their
  plugin's page).
* ``reference/performance.md``: the committed benchmark results (``RESULTS``). Only measurements
  whose equivalence verifier exited 0 are shown (plan §6.4); a workload without a passing verifier
  is listed as not counted, with no number.

The page builders are plain functions (``cli_reference_markdown``, ``performance_markdown``) so
the tests can call them without mkdocs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "benchmarks" / "vs_refengine" / "results.json"
CLI_PAGE = "reference/cli.md"
PERFORMANCE_PAGE = "reference/performance.md"
HELP_WIDTH = "100"


def _subparsers(parser: argparse.ArgumentParser) -> argparse._SubParsersAction | None:  # type: ignore[type-arg]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    return None


def _help(parser: argparse.ArgumentParser) -> str:
    old = os.environ.get("COLUMNS")
    os.environ["COLUMNS"] = HELP_WIDTH  # argparse wraps to the terminal width; fix it
    try:
        return parser.format_help().rstrip()
    finally:
        if old is None:
            os.environ.pop("COLUMNS", None)
        else:
            os.environ["COLUMNS"] = old


def _command(
    out: list[str], path: str, parser: argparse.ArgumentParser, depth: int, level: str
) -> None:
    out += [f"{'#' * depth} `{path}`", "", f"Stability: **{level}**.", ""]
    out += ["```text", _help(parser), "```", ""]
    sub = _subparsers(parser)
    if sub is not None:
        for name, child in sub.choices.items():
            _command(out, f"{path} {name}", child, min(depth + 1, 6), level)


def cli_reference_markdown(parser: argparse.ArgumentParser | None = None) -> str:
    """The CLI reference page, generated from the live core parser."""
    if parser is None:
        sys.path.insert(0, str(ROOT / "src"))
        from shape.cli.main import _build_parser

        parser = _build_parser()  # type: ignore[no-untyped-call]
    from shape.cli.stability import level

    out = [
        "# CLI reference",
        "",
        "Generated from the `shape` command's own parser when this site was built, so it always "
        "matches the code. Commands added by plugins are documented on each plugin's page. Exit "
        "codes, errors and the stability promise are explained in [the command line](../CLI.md) "
        "and [CLI stability](../CLI_STABILITY.md).",
        "",
        "```text",
        _help(parser),
        "```",
        "",
    ]
    sub = _subparsers(parser)
    if sub is not None:
        for name, child in sub.choices.items():
            _command(out, f"shape {name}", child, 2, level(name))
    return "\n".join(out).rstrip() + "\n"


def _fmt_s(v: float) -> str:
    return f"{v:.3f}"


def _passed(w: dict[str, Any]) -> bool:
    v = w.get("verifier")
    return isinstance(v, dict) and v.get("status") == "pass" and v.get("exit_code") == 0


def _label(key: str, w: dict[str, Any]) -> str:
    if w.get("kind") == "profile":
        return f"profile `{w.get('dataset', key)}`"
    if w.get("kind") == "generate":
        return f"generate `{w.get('domain')}` at `{w.get('scale')}`"
    return f"`{key}`"


def performance_markdown(results: dict[str, Any]) -> str:
    """The performance page, generated from the committed benchmark results.

    Product numbers come from the ``shape`` entry only. A workload is shown with a number only when
    its equivalence verifier passed (status ``pass``, exit code 0); otherwise it is listed as not
    counted. Kernel microbenchmarks are shown only when the native kernel was equivalent to its
    Python twin.
    """
    machine = results.get("machine") or {}
    out = [
        "# Performance",
        "",
        "Generated from the committed benchmark results when this site was built. Every number "
        "below was produced by the repository's benchmark harness; none is typed in by hand.",
        "",
        "**Equivalence comes before timing.** A workload's time is published only after its "
        "equivalence verifier exited 0 on the timed output. A workload whose verifier did not "
        "pass is listed as *not counted*, with no number.",
        "",
        "## Run",
        "",
        "| | |",
        "|---|---|",
        f"| Recorded (UTC) | {results.get('generated_utc', 'unknown')} |",
        f"| Mode | `{results.get('mode', 'unknown')}` |",
        f"| Runs per workload (median taken) | {results.get('runs', 'unknown')} |",
        f"| Shape commit | `{results.get('shape_commit', 'unknown')}` |",
        f"| Machine | {machine.get('cores', '?')} cores, {machine.get('cpu', 'unknown CPU')} |",
        f"| Python | {machine.get('python', 'unknown')} |",
        "",
        "## Product workloads",
        "",
    ]
    product = results.get("shape")
    workloads = (product or {}).get("workloads") or {}
    if not workloads:
        out += [
            "No product measurement has been recorded yet: the committed results hold no "
            "`shape` workloads. This page fills in automatically once the harness records them.",
            "",
        ]
    else:
        out += [
            "| Workload | Verifier | Median (s) | Single-threaded median (s) | Rows "
            "| Peak RSS (MB) |",
            "|---|---|---|---|---|---|",
        ]
        for key in sorted(workloads):
            w = workloads[key]
            if _passed(w) and isinstance(w.get("median_s"), (int, float)):
                t1 = w.get("median_s_1t")
                rss = w.get("peak_rss_mb")
                out.append(
                    f"| {_label(key, w)} | pass | {_fmt_s(w['median_s'])} | "
                    f"{_fmt_s(t1) if isinstance(t1, (int, float)) else '—'} | "
                    f"{w.get('rows', '—')} | "
                    f"{f'{rss:.0f}' if isinstance(rss, (int, float)) else '—'} |"
                )
            else:
                out.append(f"| {_label(key, w)} | not counted | — | — | — | — |")
        out.append("")

    out += ["## Kernel microbenchmarks", ""]
    micro = results.get("kernel_microbench") or {}
    kernels = micro.get("kernels") or {}
    if not kernels:
        out += ["No kernel microbenchmark has been recorded yet.", ""]
    else:
        out += [
            f"The native kernel functions against their pure-Python twins, on {micro.get('rows')} "
            f"rows, median of {micro.get('runs')} runs (recorded {micro.get('measured_utc')}). "
            "A kernel is listed with a time only when its output was equivalent to its twin's. "
            "The slower twin is timed on fewer rows (the last column) and its time is scaled "
            "linearly to the same row count as the native time. "
            "See [the generation kernel](../GENERATION_KERNEL.md).",
            "",
            "| Kernel | Equivalent to twin | Native (s) | Python twin (s, scaled) | "
            "Native vs twin | Twin rows timed |",
            "|---|---|---|---|---|---|",
        ]
        for name in sorted(kernels):
            k = kernels[name]
            if k.get("equivalent_to_reference") is not True:
                out.append(f"| `{name}` | no: not counted | — | — | — | — |")
                continue
            speed = k.get("speedup_vs_reference")
            out.append(
                f"| `{name}` | yes | {k['native_s']:.4f} | {k['reference_s']:.4f} | "
                f"{f'{speed:.1f}x' if isinstance(speed, (int, float)) else '—'} | "
                f"{k.get('reference_rows_measured', k.get('rows'))} |"
            )
        out.append("")
    return "\n".join(out).rstrip() + "\n"


_LINK = re.compile(r"(\]\()(<[^>]+>|[^)\s]+)((?:\s+\"[^\"]*\")?\))")
_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")
_FENCE = re.compile(r"^\s*(```|~~~)")


def repo_link(
    target: str, page_src: str, docs_dir: Path, is_site_file: Any, repo_url: str
) -> str | None:
    """The repository URL a relative link should point to on the site, or None to leave it.

    Pages link to files outside the site (source code, examples, ``CHANGELOG.md``, the internal
    plan) with paths that work when browsing the repository. On the site those paths do not exist,
    so a link whose target is not a site file but exists in the repository is pointed at the
    repository instead. A target that exists nowhere is left as is, so ``mkdocs build --strict``
    and ``scripts/check_doc_links.py`` report it.
    """
    if not target or target.startswith(("#", "/")) or _SCHEME.match(target):
        return None
    path, _, frag = target.partition("#")
    if not path:
        return None
    resolved = os.path.normpath(os.path.join(docs_dir, os.path.dirname(page_src), path))
    rel_docs = os.path.relpath(resolved, docs_dir).replace(os.sep, "/")
    if not rel_docs.startswith("../") and is_site_file(rel_docs):
        return None
    full = Path(resolved)
    if not full.exists():
        return None
    rel_repo = full.relative_to(ROOT).as_posix()
    kind = "tree" if full.is_dir() else "blob"
    return f"{repo_url.rstrip('/')}/{kind}/main/{rel_repo}" + (f"#{frag}" if frag else "")


def rewrite_links(
    markdown: str, page_src: str, docs_dir: Path, is_site_file: Any, repo_url: str
) -> str:
    """Apply ``repo_link`` to every inline Markdown link outside fenced code blocks."""

    def sub(m: re.Match[str]) -> str:
        raw = m.group(2)
        target = raw[1:-1] if raw.startswith("<") else raw
        new = repo_link(target, page_src, docs_dir, is_site_file, repo_url)
        return m.group(0) if new is None else f"{m.group(1)}{new}{m.group(3)}"

    out: list[str] = []
    fenced = False
    for line in markdown.split("\n"):
        if _FENCE.match(line):
            fenced = not fenced
            out.append(line)
            continue
        out.append(line if fenced else _LINK.sub(sub, line))
    return "\n".join(out)


def on_page_markdown(markdown: str, page: Any, config: Any, files: Any) -> str:
    """mkdocs hook: point links to files outside the site at the repository."""

    def is_site_file(rel: str) -> bool:
        f = files.get_file_from_path(rel)
        return f is not None and not f.inclusion.is_excluded()

    return rewrite_links(
        markdown, page.file.src_uri, Path(config["docs_dir"]), is_site_file, config["repo_url"]
    )


def on_files(files: Any, config: Any) -> Any:
    """mkdocs hook: add the two generated pages to the site."""
    from mkdocs.structure.files import File

    results = json.loads(RESULTS.read_text(encoding="utf-8"))
    files.append(File.generated(config, CLI_PAGE, content=cli_reference_markdown()))
    files.append(File.generated(config, PERFORMANCE_PAGE, content=performance_markdown(results)))
    return files

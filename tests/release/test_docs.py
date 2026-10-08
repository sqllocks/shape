"""The user docs agree with the code: commands, options, extras, files and supported Pythons.

Scope: ``README.md``, ``CONTRIBUTING.md``, ``docs/**/*.md`` outside ``docs/plans`` and
``examples/plugin/README.md``. A command a first-party or example plugin provides is checked
when that plugin is installed (a core-only environment does not have it).
"""

from __future__ import annotations

import contextlib
import io
import re
import shlex
import tomllib
from functools import cache
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DOCS = sorted(
    [p for p in (ROOT / "docs").rglob("*.md") if "plans" not in p.relative_to(ROOT).parts]
    + [ROOT / "README.md", ROOT / "CONTRIBUTING.md", ROOT / "examples" / "plugin" / "README.md"]
)
WORD = re.compile(r"^[a-z][\w-]*$")
# Markdown files that a documented command writes, not files of the repository.
OUTPUT_NAMES = {"report.md"}


def _rel(p: Path) -> str:
    return p.relative_to(ROOT).as_posix()


def _commands(text: str) -> list[tuple[int, str]]:
    """(line, command) for each ``shape ...`` in a fenced block (with ``\\`` continuations) or
    in inline code."""
    out: list[tuple[int, str]] = []
    fenced = False
    buf, start = "", 0
    for n, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            s = line.strip().removeprefix("$ ")
            if buf:
                buf += " " + s.rstrip("\\")
            elif re.match(r"^(\w+=\S+ )*shape\s", s):
                buf, start = s.rstrip("\\"), n
            else:
                continue
            if not s.endswith("\\"):
                out.append((start, buf))
                buf = ""
        else:
            out += [(n, m.group(1)) for m in re.finditer(r"`(shape [^`]+)`", line)]
    return out


def _tokens(command: str) -> list[str] | None:
    command = re.split(r"\s(?:#|\||&&|;|>)", command)[0]
    command = re.sub(r"^(\w+=\S+ )*", "", command)
    if command.startswith("shape shape "):  # `docker run ... IMAGE shape ...`
        command = command[len("shape ") :]
    try:
        toks = shlex.split(command)[1:]
    except ValueError:
        return None
    while toks and toks[0] in ("--debug", "--log-json"):
        toks = toks[1:]
    return toks if toks and WORD.match(toks[0]) else None


@cache
def _help(path: tuple[str, ...]) -> tuple[int, str]:
    from shape.cli.main import main

    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = main([*path, "--help"])
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, out.getvalue()


@cache
def _plugin_commands() -> frozenset[str]:
    """Commands that a plugin in this repository (first-party or the example) declares."""
    names: set[str] = set()
    for pp in [
        *(ROOT / "plugins").glob("*/pyproject.toml"),
        ROOT / "examples/plugin/pyproject.toml",
    ]:
        eps = tomllib.loads(pp.read_text("utf-8"))["project"].get("entry-points", {})
        names |= set(eps.get("shape.commands", {}))
    return frozenset(names)


def _documented_commands() -> list[tuple[str, int, list[str]]]:
    found = []
    for doc in DOCS:
        for n, command in _commands(doc.read_text("utf-8")):
            toks = _tokens(command)
            if toks:
                found.append((_rel(doc), n, toks))
    return found


def test_the_docs_show_commands():
    assert len(_documented_commands()) > 200


def test_every_documented_command_and_option_exists():
    """Each command path and each ``--long`` or ``-x`` option the docs show is in its --help."""
    problems = []
    for where, n, toks in _documented_commands():
        path = [toks[0]]
        code, text = _help(tuple(path))
        if code != 0:
            if toks[0] not in _plugin_commands():
                problems.append(f"{where}:{n}: unknown command `shape {toks[0]}`")
            continue
        # descend into subcommands; a positional before one (`registry ROOT checkout`) is kept
        for i, tok in enumerate(toks[1:], 1):
            if tok.startswith("-"):
                break
            if not WORD.match(tok):
                continue
            sub_code, sub_text = _help(tuple(toks[: i + 1]))
            if sub_code == 0 and sub_text != text:
                path, text = toks[: i + 1], sub_text
        options = text.split("options:", 1)[-1]
        for tok in toks[1:]:
            flag = tok.split("=", 1)[0].rstrip(";,")
            if flag.startswith("--") and flag != "--help":
                if not re.search(re.escape(flag) + r"(?![\w-])", text):
                    problems.append(f"{where}:{n}: `shape {' '.join(path)}` has no {flag}")
            elif re.fullmatch(r"-[A-Za-z]", flag):
                if not re.search(r"(?<![\w-])" + re.escape(flag) + r"[ ,\n]", options):
                    problems.append(f"{where}:{n}: `shape {' '.join(path)}` has no {flag}")
    assert problems == []


def _extras(pyproject: Path) -> set[str]:
    data = tomllib.loads(pyproject.read_text("utf-8"))
    return set(data["project"].get("optional-dependencies", {}))


def test_every_documented_extra_exists():
    """``pip install 'sqllocks-shape[x]'`` and ``sqllocks-shape-NAME[x]`` name real extras."""
    problems = []
    rx = re.compile(r"sqllocks-shape(?:-([a-z]+))?\[([a-z0-9,-]+)\]")
    for doc in DOCS:
        for n, line in enumerate(doc.read_text("utf-8").splitlines(), 1):
            for m in rx.finditer(line):
                plugin, extras = m.group(1), m.group(2).split(",")
                pyproject = ROOT / (
                    f"plugins/shape-{plugin}/pyproject.toml" if plugin else "pyproject.toml"
                )
                if not pyproject.is_file():
                    problems.append(f"{_rel(doc)}:{n}: no distribution for {m.group(0)}")
                    continue
                missing = [e for e in extras if e not in _extras(pyproject)]
                if missing:
                    problems.append(f"{_rel(doc)}:{n}: {m.group(0)} has no extra {missing}")
    assert problems == []


def test_relative_links_and_cited_markdown_files_exist():
    problems = []
    tracked = {p.name for p in ROOT.rglob("*.md") if ".git" not in p.parts}
    for doc in DOCS:
        for n, line in enumerate(doc.read_text("utf-8").splitlines(), 1):
            for target in re.findall(r"\]\(([^)\s]+)\)", line):
                path = target.split("#", 1)[0]
                if path and not target.startswith(("http", "mailto:")):
                    if not (doc.parent / path).exists():
                        problems.append(f"{_rel(doc)}:{n}: broken link {target}")
            for name in re.findall(r"`([\w./-]+\.md)`", line):
                base = name.rsplit("/", 1)[-1]
                if base not in tracked and base not in OUTPUT_NAMES:
                    problems.append(f"{_rel(doc)}:{n}: cites {name}, which does not exist")
    assert problems == []


def _ci_pythons() -> list[str]:
    ci = (ROOT / ".github/workflows/ci.yml").read_text("utf-8")
    m = re.search(r"python: \[([^\]]+)\]", ci)
    assert m, "the CI test matrix lists no Python versions"
    return re.findall(r"3\.\d+", m.group(1))


def test_install_page_states_the_pythons_ci_tests():
    """T-06: the supported range is the one the CI test matrix runs."""
    text = (ROOT / "docs/INSTALL.md").read_text("utf-8")
    m = re.search(r"Supported Python: (3\.\d+)–(3\.\d+)", text)
    assert m, "docs/INSTALL.md does not state the supported Python range"
    versions = sorted(_ci_pythons(), key=lambda v: int(v.split(".")[1]))
    assert (m.group(1), m.group(2)) == (versions[0], versions[-1])


def test_install_page_lists_every_offline_lock_set():
    """The offline-lock step names one file per extra of pyproject.toml, as the script writes."""
    text = (ROOT / "docs/INSTALL.md").read_text("utf-8")
    m = re.search(r"one\s+`requirements-<extra>.txt` per extra \(([^)]*)\)", text)
    assert m, "docs/INSTALL.md no longer lists the offline-lock extras"
    listed = set(re.findall(r"`([\w-]+)`", m.group(1)))
    assert listed == _extras(ROOT / "pyproject.toml")


@pytest.mark.parametrize("doc", DOCS, ids=_rel)
def test_shape_diff_in_the_docs_compares_profiles(doc):
    """``shape diff`` reads profiles (docs/CLI.md); two captured JSON models are compared with
    ``shape compatibility``, and the diff options do not apply to them (#347)."""
    bad = [
        f"{n}: {' '.join(toks)}"
        for n, command in _commands(doc.read_text("utf-8"))
        if (toks := _tokens(command)) and toks[0] == "diff"
        for operand in [t for t in toks[1:3] if not t.startswith("-")]
        if operand.endswith(".json")
    ]
    assert bad == []


def test_readme_points_to_the_shipped_safe_profile():
    """#346: the README's privacy note names `shape profile safe`, not a "planned" feature."""
    readme = (ROOT / "README.md").read_text("utf-8")
    section = readme.split("## What a `.shape` file contains", 1)[1].split("\n## ", 1)[0]
    assert "is planned" not in section
    assert "shape profile safe" in section


def test_the_contributing_guide_names_the_checks_ci_runs():
    """#349: the guide sends contributors to `make check` (what the CI test job runs), and every
    directory and script it names exists."""
    guide = (ROOT / "docs/CONTRIBUTING.md").read_text("utf-8")
    assert "make check" in guide
    assert 'pytest -m "not emulator and not live"' in guide
    for path in re.findall(r"`((?:[\w.-]+/)+[\w.-]*)`", guide):
        assert (ROOT / path).exists(), path
    assert "docs/CONTRIBUTING.md" in (ROOT / "CONTRIBUTING.md").read_text("utf-8")

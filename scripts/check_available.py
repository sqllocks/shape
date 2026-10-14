"""Check available documentation against P-BAR and the shrink-only legacy list.

Run ``python scripts/check_available.py [--require-base]``. Exit 0 means clean,
1 means a promotion rule or ratchet fails, and 2 means usage, unreadable input,
or a required Git baseline is unavailable. See docs/AVAILABLE.md for the layout.
"""

import argparse
import importlib.util
import json
import re
import shlex
import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_docs_owners import inventory  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LEGACY = "tests/docs/available_legacy.txt"
STATUS = re.compile(r"^Status:\s*(available|experimental|coming)\b", re.MULTILINE)
ANCHOR = re.compile(r"<!--\s*claim:\s*(AV-[A-Z0-9-]+)\s*-->")
COMMAND = re.compile(r"\bshape[ \t]+([a-z][a-z0-9-]*)\b")
TICKS = re.compile(r"`([^`]+)`")
FENCE = re.compile(r"<!--\s*experimental:(start|end)\s*-->")


@dataclass
class Row:
    ident: str
    claim: str
    proof: list[str]
    evidence: list[str]
    status: str


@dataclass
class Section:
    since: str = ""
    rows: list[Row] = field(default_factory=list)


def message(page: str, rule: int, text: str) -> str:
    return f"AVAILABLE: {page}: P-BAR {rule}: {text}"


def relative_path(root: Path, name: str) -> Path | None:
    path = Path(name)
    if path.is_absolute() or ".." in path.parts:
        return None
    resolved = (root / path).resolve()
    return resolved if resolved.is_relative_to(root.resolve()) else None


def public_page(name: str) -> bool:
    path = Path(name)
    return (
        name.startswith("docs/")
        and path.suffix == ".md"
        and not {"plans", "talks", ".."}.intersection(path.parts)
    )


def page_status(text: str) -> str:
    match = STATUS.search(text)
    return match[1] if match else ""


def without_experimental(text: str) -> tuple[str, bool]:
    """Remove complete experimental blocks; reject unmatched or nested delimiters."""
    text = re.sub(
        r"`[^`\n]*`",
        lambda match: match[0].replace("experimental:", "literal-experimental:"),
        text,
    )
    output: list[str] = []
    offset = 0
    inside = False
    valid = True
    for match in FENCE.finditer(text):
        line_start = text.rfind("\n", 0, match.start()) + 1
        line_end = text.find("\n", match.end())
        if line_end < 0:
            line_end = len(text)
        if text[line_start : match.start()].strip() or text[match.end() : line_end].strip():
            valid = False
        if not inside:
            output.append(text[offset : match.start()])
        if (match[1] == "start") == inside:
            valid = False
        inside = match[1] == "start"
        offset = match.end()
    if not inside:
        output.append(text[offset:])
    return "".join(output), valid and not inside


def parse(text: str) -> tuple[dict[str, Section], list[str]]:
    sections: dict[str, Section] = {}
    problems: list[str] = []
    parts = re.split(r"^##[ \t]+(.+?)[ \t]*$", text, flags=re.MULTILINE)
    for page, body in zip(parts[1::2], parts[2::2], strict=True):

        def malformed(reason: str, section_page: str = page) -> None:
            problems.append(message(section_page, 2, "malformed section: " + reason))

        if not public_page(page):
            malformed("heading must be a public docs path")
            continue
        if page in sections:
            malformed("page is listed twice")
        section = Section()
        sections[page] = section
        since = re.findall(r"^since:[ \t]*(\S+)[ \t]*$", body, re.MULTILINE)
        first = next((line.strip() for line in body.splitlines() if line.strip()), "")
        if len(since) != 1 or first != "since: " + since[0]:
            malformed("expected one plain since: <version> line")
        else:
            section.since = since[0]
        seen: set[str] = set()
        for line in body.splitlines():
            if not line.lstrip().startswith("|"):
                continue
            cells = [cell.strip() for cell in line.strip().split("|")[1:-1]]
            if cells and (
                cells[0].lower() in {"claim", "#", "id"} or re.fullmatch(r"[-: ]+", cells[0])
            ):
                continue
            if not line.rstrip().endswith("|") or len(cells) != 5:
                malformed("expected five row cells")
                continue
            ident, claim, proof, evidence, status = cells
            if not re.fullmatch(r"AV-[A-Z0-9-]+-\d+", ident) or not claim:
                malformed("invalid claim id or empty claim")
                continue
            if ident in seen:
                malformed(ident + " is listed twice")
            seen.add(ident)
            if evidence != "none" and not TICKS.findall(evidence):
                malformed(ident + " evidence must be backtick paths or none")
            section.rows.append(
                Row(ident, claim, TICKS.findall(proof), TICKS.findall(evidence), status)
            )
        if not section.rows:
            malformed("no claim rows")
    return sections, problems


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=False)


def baseline(root: Path) -> tuple[set[str] | None, str]:
    """None with no reason means this commit introduces the legacy file."""
    try:
        base = git(root, "merge-base", "HEAD", "origin/main")
        if base.returncode:
            return None, "git, origin/main, or merge base unavailable"
        commit = base.stdout.strip()
        listing = git(root, "ls-tree", "--name-only", commit, "--", LEGACY)
        if listing.returncode:
            return None, "cannot read merge-base tree"
        if not listing.stdout.strip():
            return None, ""
        content = git(root, "show", f"{commit}:{LEGACY}")
        if content.returncode:
            return None, "cannot read merge-base legacy list"
        return set(content.stdout.splitlines()), ""
    except OSError:
        return None, "git unavailable"


def stable_commands(root: Path) -> set[str]:
    """Import only the stdlib stability module, without importing the Shape package."""
    spec = importlib.util.spec_from_file_location(
        "_available_stability", root / "src/shape/cli/stability.py"
    )
    if spec is None or spec.loader is None:
        raise ValueError("cannot import CLI stability")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return set(module.STABLE) | set(getattr(module, "STABLE_PLUGIN_COMMANDS", ()))


def job_exists(root: Path, reference: str) -> bool:
    workflow, separator, name = reference.removeprefix("ci:").partition("/")
    path = relative_path(root, ".github/workflows/" + workflow)
    if not separator or not name or path is None or not path.is_file():
        return False
    text = path.read_text("utf-8")
    match = re.search(r"^jobs:\s*$", text, re.MULTILINE)
    if not match:
        return False
    jobs = re.split(r"^\S", text[match.end() :], maxsplit=1, flags=re.MULTILINE)[0]
    return re.search(rf"^  {re.escape(name)}:\s*(?:#.*)?$", jobs, re.MULTILINE) is not None


def collected(root: Path, files: list[str]) -> tuple[set[str], str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *files],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    have = {
        line.strip()
        for line in result.stdout.splitlines()
        if "::" in line and " " not in line.strip()
    }
    return have, "pytest could not collect the named files" if result.returncode else ""


def evidence_error(root: Path, name: str, version: str) -> str:
    path = relative_path(root, name)
    if path is None or not name.startswith(("docs/evidence/", "docs/plans/evidence/")):
        return "evidence must be under docs/evidence or docs/plans/evidence"
    if not path.is_file():
        return "evidence does not exist"
    text = path.read_text("utf-8")
    try:
        if path.suffix == ".json":
            record = json.loads(text)
            if not isinstance(record, dict):
                return "evidence is not a record"
            if (
                not record.get("run_id")
                or not record.get("commit")
                or record.get("status") != "passed"
            ):
                return "transcript needs run_id, commit, and status passed"
            exit_code = record.get("exit_code")
        else:
            receipt = re.search(r"<!--\s*receipt:\s*(.*?)\s*-->", text, re.DOTALL)
            if not receipt:
                return "evidence has no machine receipt"
            record = dict(token.split("=", 1) for token in shlex.split(receipt[1]) if "=" in token)
            exit_code = int(record.get("exit", "-1"))
            record.setdefault("command", " ".join(COMMAND.findall(text)))
        if type(exit_code) is not int or exit_code != 0:
            return "evidence must record exit 0"
        release = record.get("shape", record.get("shape_version"))
        if not isinstance(release, str) or not release or not record.get("command"):
            return "evidence needs a Shape version and command"
        date.fromisoformat(str(record.get("date", "")))
        service = str(record.get("service", "")).lower()
        if (
            "emulator" in service
            or service == "azurite"
            or record.get("emulator")
            or record.get("mock")
        ):
            return "a mock or cloud emulator is not a real recorded run"
        if release == version:
            return ""
        paths = record.get("paths", [])
        if isinstance(paths, str):
            paths = paths.split(",")
        commit = record.get("commit")
        if not commit or not isinstance(paths, list) or not paths:
            return "stale evidence: needs commit and source paths"
        if any(
            not isinstance(p, str) or relative_path(root, p) is None or not (root / p).is_file()
            for p in paths
        ):
            return "stale evidence: invalid source paths"
        if not re.fullmatch(r"[0-9a-fA-F]{7,40}", str(commit)):
            return "stale evidence: invalid commit"
        diff = git(root, "diff", "--quiet", str(commit), "HEAD", "--", *paths)
        return (
            "" if diff.returncode == 0 else "stale evidence: source changed or commit unavailable"
        )
    except (ValueError, TypeError, OSError):
        return "malformed evidence or unavailable source history"


def promoted(text: str, version: str) -> str:
    sections = re.split(r"^##[ \t]+(.+?)[ \t]*$", text, flags=re.MULTILINE)
    releases = dict(zip(sections[1::2], sections[2::2], strict=True))
    body = releases.get(version, releases.get("Unreleased", ""))
    headings = re.split(r"^###[ \t]+(.+?)[ \t]*$", body, flags=re.MULTILINE)
    return dict(zip(headings[1::2], headings[2::2], strict=True)).get("Promoted", "")


def check(root: Path, *, require_base: bool = False) -> tuple[list[str], bool]:
    sections, problems = parse((root / "docs/AVAILABLE.md").read_text("utf-8"))
    version = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))["project"]["version"]
    legacy_lines = (root / LEGACY).read_text("utf-8").splitlines()
    legacy = set(legacy_lines)
    if legacy_lines != sorted(legacy):
        problems.append(f"AVAILABLE: {LEGACY}: ratchet: expected sorted unique paths")
    pages = {
        p.relative_to(root).as_posix(): p.read_text("utf-8")
        for p in (root / "docs").rglob("*.md")
        if public_page(p.relative_to(root).as_posix())
    }
    for page in sorted(legacy):
        if page not in pages or page_status(pages[page]) != "available":
            problems.append(
                f"AVAILABLE: {page}: ratchet: no longer available; remove it from {LEGACY}"
            )
    previous, reason = baseline(root)
    if reason:
        print(f"AVAILABLE: ratchet growth not checked: no baseline ({reason})", file=sys.stderr)
        if require_base:
            return problems, True
    if previous is not None:
        for page in sorted(legacy - previous):
            problems.append(f"AVAILABLE: {page}: ratchet: the legacy list may only shrink")
    for page in sections:
        if page not in pages or page_status(pages[page]) != "available":
            problems.append(message(page, 1, "a page once available is not demoted"))
    active = {
        page: text
        for page, text in pages.items()
        if page_status(text) == "available" and page not in legacy
    }
    stable = stable_commands(root)
    notes = inventory(root)
    promotion = promoted((root / "CHANGELOG.md").read_text("utf-8"), version)
    node_ids = {
        proof
        for page, section in sections.items()
        if page in active
        for row in section.rows
        for proof in row.proof
        if "::" in proof
    }
    files = sorted(
        {
            node.split("::", 1)[0]
            for node in node_ids
            if (path := relative_path(root, node.split("::", 1)[0])) is not None and path.is_file()
        }
    )
    have, collection_error = collected(root, files) if files else (set(), "")
    for page, original in sorted(active.items()):
        text, balanced = without_experimental(original)
        if not balanced:
            problems.append(message(page, 1, "malformed experimental fences"))
        declared: set[str] = set()
        for line in re.findall(r"^Covers:[ \t]*(.*)$", text, re.MULTILINE):
            for item in line.split(","):
                match = re.fullmatch(r"shape[ \t]+([a-z][a-z0-9-]*)", item.strip())
                if match:
                    declared.add(match[1])
                else:
                    problems.append(message(page, 1, "malformed Covers line"))
        mentioned = set(COMMAND.findall(text))
        for command in sorted(mentioned - declared):
            problems.append(
                message(page, 1, f"page mentions `shape {command}`; add it to Covers or fence it")
            )
        for command in sorted((mentioned | declared) - stable):
            problems.append(message(page, 1, f"`shape {command}` is experimental"))
        section = sections.get(page)
        if section is None:
            problems.append(message(page, 2, "no claim-to-proof section in docs/AVAILABLE.md"))
        else:
            anchors = ANCHOR.findall(text)
            rows = {row.ident for row in section.rows}
            if len(anchors) != len(set(anchors)):
                problems.append(message(page, 2, "duplicate claim anchor"))
            if not anchors or not rows:
                problems.append(message(page, 2, "no marked claims and proof rows"))
            for ident in sorted(set(anchors) - rows):
                problems.append(message(page, 2, ident + ": anchor has no row"))
            for ident in sorted(rows - set(anchors)):
                problems.append(message(page, 2, ident + ": row has no anchor"))
            if collection_error and any(
                "::" in proof for row in section.rows for proof in row.proof
            ):
                problems.append(message(page, 2, collection_error))
            for row in section.rows:
                if row.status != "done" or not row.proof:
                    problems.append(message(page, 2, row.ident + ": needs done status and proof"))
                for proof in row.proof:
                    exists = (
                        job_exists(root, proof)
                        if proof.startswith("ci:")
                        else "::" in proof
                        and (
                            proof in have
                            or any(item.startswith((proof + "[", proof + "::")) for item in have)
                        )
                    )
                    if not exists:
                        problems.append(
                            message(page, 2, row.ident + ": proof does not exist: " + proof)
                        )
                if re.search(r"\bservice:\s*\S+", row.claim):
                    if not row.evidence:
                        problems.append(
                            message(page, 3, row.ident + ": service needs a real recorded run")
                        )
                    for name in row.evidence:
                        error = evidence_error(root, name, version)
                        if error:
                            problems.append(message(page, 3, f"{row.ident}: {name}: {error}"))
            if section.since == version and not any(
                re.search(rf"(?<![\w/.-]){re.escape(page)}(?![\w/.-])", line)
                for line in promotion.splitlines()
            ):
                problems.append(message(page, 5, "no Promoted line for this release"))
        for owner_page, line, note in notes:
            if owner_page == page:
                problems.append(message(page, 4, f"open owner note at line {line}: {note}"))
    return problems, False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--require-base",
        action="store_true",
        help="fail if the Git ratchet baseline is unavailable",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)
    try:
        problems, unavailable = check(ROOT, require_base=args.require_base)
    except (OSError, ValueError, KeyError) as exc:
        print(f"AVAILABLE: error: {exc}", file=sys.stderr)
        return 2
    for problem in problems:
        print(problem)
    print(f"AVAILABLE: {len(problems)} problem(s)")
    return 2 if unavailable else int(bool(problems))


if __name__ == "__main__":
    sys.exit(main())

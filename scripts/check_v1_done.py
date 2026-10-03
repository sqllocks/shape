"""Check the proof named by ``docs/V1_DONE.md`` (W1-10).

    python scripts/check_v1_done.py [--doc PATH]

Every item of the definition of done is a table row ``| V1-NN | statement | proof | status |``.
The proof column names pytest node ids (``tests/x.py::test_y``) and/or CI jobs
(``ci:ci.yml/rust``, a job of ``.github/workflows/ci.yml``), each in backticks. The script confirms
that every named node id is collected by pytest (a parametrized test is named without its
parameters; a class, without its methods) and that every named CI job exists. Status is ``done``
(it needs proof) or ``open`` followed by the work package or issue that closes it (``open W1-14``,
``open #123``); an open item may name proof too, and that proof must exist.

Exit codes: 0 every named proof exists, 1 a proof does not exist or a row is malformed, 2 usage
error or the document cannot be read.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "V1_DONE.md"
WORKFLOWS = ROOT / ".github" / "workflows"

ROW = re.compile(r"^\|\s*(V1-\d+)\s*\|(.*)\|(.*)\|(.*)\|\s*$")
TICKS = re.compile(r"`([^`]+)`")
OPEN = re.compile(r"^open\b.*(\bW\d-\d+\b|\bP\d-\d+[a-z]?\b|#\d+)", re.IGNORECASE)


def parse(text: str) -> tuple[list[dict[str, object]], list[str]]:
    """The items of the document and the problems with their rows."""
    items: list[dict[str, object]] = []
    problems: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        ident, statement, proof, status = (g.strip() for g in m.groups())
        if ident in seen:
            problems.append(f"{ident}: listed twice")
        seen.add(ident)
        ticks = TICKS.findall(proof)
        ids = [t for t in ticks if "::" in t]
        jobs = [t for t in ticks if t.startswith("ci:")]
        if not statement:
            problems.append(f"{ident}: no statement")
        if status.lower() == "done":
            if not ids and not jobs:
                problems.append(f"{ident}: marked done but names no test or CI job")
        elif not OPEN.match(status):
            problems.append(
                f"{ident}: status {status!r} must be 'done' or 'open' with the work package or "
                f"issue that closes it"
            )
        items.append({"id": ident, "node_ids": ids, "jobs": jobs})
    if not items:
        problems.append("the document has no V1-NN rows")
    return items, problems


def collected(files: list[str]) -> tuple[set[str], str]:
    """The node ids pytest collects from ``files`` (relative to the repository), and any error."""
    cmd = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider", *files]
    done = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=False)
    ids = {ln.strip() for ln in done.stdout.splitlines() if "::" in ln and " " not in ln.strip()}
    error = ""
    if done.returncode not in (0,):
        error = (
            (done.stdout + done.stderr).strip().splitlines()[-1]
            if done.stdout or done.stderr
            else ""
        )
    return ids, error


def _exists(node_id: str, have: set[str]) -> bool:
    return node_id in have or any(h.startswith((node_id + "[", node_id + "::")) for h in have)


def job_exists(job: str) -> bool:
    """``ci:ci.yml/rust``: a job key of that workflow file."""
    workflow, _, name = job.removeprefix("ci:").partition("/")
    path = WORKFLOWS / workflow
    if not name or not path.is_file():
        return False
    return (
        re.search(rf"^  {re.escape(name)}:\s*$", path.read_text("utf-8"), re.MULTILINE) is not None
    )


def check(text: str) -> list[str]:
    items, problems = parse(text)
    node_ids = sorted({n for i in items for n in i["node_ids"]})  # type: ignore[attr-defined]
    missing_files = {
        n.split("::", 1)[0] for n in node_ids if not (ROOT / n.split("::", 1)[0]).is_file()
    }
    files = sorted({n.split("::", 1)[0] for n in node_ids} - missing_files)
    have: set[str] = set()
    if files:
        have, error = collected(files)
        if error and not have:
            problems.append(f"pytest could not collect the named files: {error}")
            return problems
    for i in items:
        for n in i["node_ids"]:  # type: ignore[attr-defined]
            if n.split("::", 1)[0] in missing_files or not _exists(n, have):
                problems.append(f"{i['id']}: test does not exist: {n}")
        for j in i["jobs"]:  # type: ignore[attr-defined]
            if not job_exists(j):
                problems.append(f"{i['id']}: CI job does not exist: {j}")
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--doc", type=Path, default=DOC, help="the definition of done to check")
    try:
        ns = ap.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2
    try:
        text = ns.doc.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"check_v1_done: error: {exc}", file=sys.stderr)
        return 2
    problems = check(text)
    for p in problems:
        print("MISSING:", p)
    items, _ = parse(text)
    print(f"v1 definition of done: {len(items)} item(s), {len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())

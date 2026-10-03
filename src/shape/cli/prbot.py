"""``shape ci comment`` and ``shape ci post-comment``: the pull request comment (W6-01).

``comment`` renders Markdown from ``shape-result`` documents (W1-14). The text is a pure function
of the documents, the options and the Shape version: no clock, no paths, no environment. Names
that come from the data (sources, tables, columns, kinds) are escaped so that Markdown, HTML and
``@`` mentions in them render as text. Only names are read from a document (see
:mod:`shape.cli.findings`), so a value that safe capture suppressed cannot appear.

``post-comment`` puts the text on a pull request through the GitHub REST API: one comment per
pull request, found by its marker and by its author and updated in place on every later run.
The token comes from ``GITHUB_TOKEN`` and goes nowhere but the ``Authorization`` header.
"""

from __future__ import annotations

import argparse
import http.client
import ipaddress
import json
import os
import re
import sys
import urllib.parse
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from shape.cli.findings import Result, ResultError, load_all, worst

MARKER = "<!-- shape-pr-comment -->"
DEFAULT_TITLE = "Shape data check"
DEFAULT_MAX_FINDINGS = 50
#: GitHub refuses a comment body longer than this many characters
MAX_BODY = 65536
DEFAULT_API = "https://api.github.com"
FALLBACK_AUTHOR = "github-actions[bot]"
TIMEOUT = 30
_NO_TOKEN_DETAIL = "set GITHUB_TOKEN in the environment (it is never taken from an argument)"
_REPO = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f  ]+")
#: characters that Markdown reads as syntax, in a table cell or a heading
_MARKDOWN = re.compile(r"([\\`*_\[\]()|~:!#$^])")

_PHRASE = {"pass": "pass", "drift": "drift", "fail": "fail"}


# ---- rendering -----------------------------------------------------------------------------------


def escape(text: object) -> str:
    """``text`` that renders as itself in a GitHub comment: HTML and Markdown syntax is escaped,
    line breaks become spaces and an ``@`` is followed by a zero-width space, so that a name is
    never read as a mention."""
    clean = _CONTROL.sub(" ", str(text)).strip()
    clean = clean.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    clean = _MARKDOWN.sub(r"\\\1", clean)
    return clean.replace("@", "@&#8203;")


def _cell(value: str) -> str:
    return escape(value) if value else "-"


def _count(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def _table(head: Sequence[str], rows: Sequence[Sequence[str]]) -> list[str]:
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def render(
    results: Sequence[Result],
    *,
    max_findings: int = DEFAULT_MAX_FINDINGS,
    title: str = DEFAULT_TITLE,
    version: str | None = None,
) -> str:
    """The comment for ``results``, one section per source.

    At most ``max_findings`` findings are listed over the whole comment (the most severe first
    within a source, sources in the order given); the rest are counted."""
    if version is None:
        from shape import __version__ as version
    verdict = worst([r.verdict for r in results])
    total = sum(len(r.findings) for r in results)
    planned = sum(len(r.planned) for r in results)
    out = [MARKER, f"## {escape(title)}", ""]
    summary = [_count(len(results), "source"), _count(total, "finding")]
    if planned:
        summary.append(_count(planned, "planned change"))
    out += [f"**Verdict: {_PHRASE[verdict]}** - " + ", ".join(summary), ""]
    budget = max_findings
    hidden = 0
    for r in results:
        out += [f"### Source: {escape(r.source)}", ""]
        out.append(f"`shape {escape(r.command) if r.command else 'run'}` - **{r.verdict}**")
        out.append("")
        if r.exit_code != 0 and not r.findings:
            out += [
                f"The command exited with code {r.exit_code} and listed no findings: "
                "see the job log for the reason.",
                "",
            ]
        elif not r.findings:
            out += ["No findings.", ""]
        else:
            shown = r.findings[: max(budget, 0)]
            budget -= len(shown)
            hidden += len(r.findings) - len(shown)
            rows = [
                (_cell(f.table), _cell(f.column), _cell(f.kind), _cell(f.severity)) for f in shown
            ]
            if rows:
                out += _table(("Table", "Column", "Kind", "Severity"), rows)
                out.append("")
        if r.planned:
            out += ["Planned changes (not counted as findings):", ""]
            out += _table(
                ("Table", "Column", "Kind", "Plan"),
                [
                    (_cell(f.table), _cell(f.column), _cell(f.kind), _cell(f.planned or ""))
                    for f in r.planned
                ],
            )
            out.append("")
    if hidden:
        out += [
            f"... and {_count(hidden, 'more finding')} not shown (--max-findings {max_findings}).",
            "",
        ]
    out += ["---", f"<sub>Shape {escape(version)}</sub>", ""]
    return "\n".join(out)


def summary(results: Sequence[Result]) -> dict[str, Any]:
    """The machine-readable summary of what :func:`render` shows."""
    return {
        "verdict": worst([r.verdict for r in results]),
        "findings": sum(len(r.findings) for r in results),
        "planned": sum(len(r.planned) for r in results),
        "sources": [
            {
                "source": r.source,
                "command": r.command,
                "verdict": r.verdict,
                "exit_code": r.exit_code,
                "findings": len(r.findings),
            }
            for r in results
        ],
    }


# ---- the commands --------------------------------------------------------------------------------


def _non_negative(text: str) -> int:
    try:
        value = int(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from None
    if value < 0:
        raise argparse.ArgumentTypeError("must be 0 or more")
    return value


def add_arguments(sub: Any) -> None:
    ci = sub.add_parser("ci", help="CI helpers: the pull request comment")
    cis = ci.add_subparsers(dest="ci_cmd", required=True)
    c = cis.add_parser(
        "comment",
        help="render Markdown for a pull request from shape-result documents",
        description="Render one Markdown comment from one or more shape-result documents (the "
        "output of `--json -` of `shape diff` and the other checking commands). The text carries "
        f"the hidden marker `{MARKER}`, a verdict, one section per source and a table of "
        "findings. With -o the comment goes to FILE and a JSON summary to standard output; "
        "without it the comment goes to standard output.",
    )
    c.add_argument("results", nargs="+", metavar="RESULT.json")
    c.add_argument("-o", "--output", metavar="FILE", help="write the comment here")
    c.add_argument(
        "--max-findings",
        type=_non_negative,
        default=DEFAULT_MAX_FINDINGS,
        metavar="N",
        help=f"list at most N findings, count the rest (default {DEFAULT_MAX_FINDINGS})",
    )
    c.add_argument("--title", default=DEFAULT_TITLE, metavar="TEXT", help="the comment's heading")
    c.add_argument("--json", action="store_true", help="print the summary as a shape-result")
    p = cis.add_parser(
        "post-comment",
        help="create or update the one Shape comment of a pull request",
        description="Post the comment written by `shape ci comment`. The token is read from "
        "GITHUB_TOKEN (never from an argument) and the server from GITHUB_API_URL (default "
        f"{DEFAULT_API}). A comment of the same author that carries the marker is updated, "
        "otherwise one is created. A 403 or 404 (a fork pull request, whose token cannot "
        "write) prints a notice and exits 0, so the check result still decides the gate.",
    )
    p.add_argument("--body-file", required=True, metavar="FILE")
    p.add_argument("--repo", required=True, metavar="OWNER/REPO")
    p.add_argument("--pr", required=True, type=int, metavar="N", help="pull request number")
    p.add_argument("--json", action="store_true", help="print the outcome as a shape-result")


def run(a: argparse.Namespace) -> int:
    if a.ci_cmd == "comment":
        return _comment(a)
    return _post(a)


def _comment(a: argparse.Namespace) -> int:
    try:
        results = load_all(a.results)
    except ResultError as exc:
        print(f"shape: error: {exc}", file=sys.stderr)
        return 2
    text = render(results, max_findings=a.max_findings, title=a.title)
    if not a.output:
        sys.stdout.write(text)
        return 0
    from shape.cli import ci

    ci.write_text(Path(a.output), text, "comment")
    print(json.dumps({**summary(results), "comment": str(a.output)}, indent=2))
    return 0


# ---- posting -------------------------------------------------------------------------------------


class PostError(Exception):
    """A request failed. ``status`` is the HTTP status, or None for a failure to connect."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_url(url: str, what: str) -> urllib.parse.SplitResult:
    """``url`` split, when it is one a secret may be sent to: HTTPS, or plain HTTP to a loopback
    address (for tests), with no user information and no redirect to follow. Raises
    ``ValueError`` (the text never holds ``url``) otherwise."""
    try:
        parts = urllib.parse.urlsplit(url)
        host = parts.hostname or ""
        has_user = bool(parts.username or parts.password)
    except ValueError:
        raise ValueError(f"{what} is not a valid URL") from None
    if not ((parts.scheme == "https" and host) or (parts.scheme == "http" and _is_loopback(host))):
        raise ValueError(
            f"{what} must be an https:// URL (http:// only for a loopback address); nothing "
            "is sent anywhere else"
        )
    if has_user:
        raise ValueError(f"{what} must not carry credentials")
    return parts


def check_api_url(url: str) -> urllib.parse.SplitResult:
    return check_url(url, "GITHUB_API_URL")


class Client:
    """A minimal GitHub REST client: no redirects, a timeout, and the token only in a header."""

    def __init__(self, api_url: str, token: str) -> None:
        self.parts = check_api_url(api_url)
        self.token = token

    def _scrub(self, text: str) -> str:
        return text.replace(self.token, "***") if self.token else text

    def request(self, method: str, path: str, body: Any = None) -> Any:
        base = self.parts.path.rstrip("/")
        cls = (
            http.client.HTTPSConnection
            if self.parts.scheme == "https"
            else http.client.HTTPConnection
        )
        conn = cls(self.parts.hostname or "", self.parts.port, timeout=TIMEOUT)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "shape-ci",
        }
        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, base + path, body=payload, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except (OSError, http.client.HTTPException) as exc:
            raise PostError(
                self._scrub(f"cannot reach {self.parts.hostname}: {type(exc).__name__}")
            ) from None
        finally:
            conn.close()
        if 300 <= resp.status < 400:
            raise PostError(
                f"{method} {path}: HTTP {resp.status}, a redirect (not followed)", resp.status
            )
        if resp.status >= 400:
            raise PostError(f"{method} {path}: HTTP {resp.status}", resp.status)
        if not data:
            return None
        try:
            return json.loads(data)
        except ValueError:
            raise PostError(f"{method} {path}: the response is not JSON", resp.status) from None


def _author(client: Client) -> str:
    """Who the token acts as. An installation token (the workflow's) cannot read ``/user``; it
    acts as the Actions bot."""
    try:
        me = client.request("GET", "/user")
    except PostError as exc:
        if exc.status in (401, 403, 404):
            return FALLBACK_AUTHOR
        raise
    login = me.get("login") if isinstance(me, dict) else None
    return str(login) if login else FALLBACK_AUTHOR


def _find(client: Client, repo: str, pr: int, author: str) -> int | None:
    page = 1
    while True:
        items = client.request(
            "GET", f"/repos/{repo}/issues/{pr}/comments?per_page=100&page={page}"
        )
        if not isinstance(items, list):
            raise PostError("the comment list is not a list")
        for item in items:
            if not isinstance(item, dict):
                continue
            user = item.get("user")
            login = user.get("login") if isinstance(user, dict) else None
            if login == author and MARKER in str(item.get("body", "")):
                return int(item["id"])
        if len(items) < 100:
            return None
        page += 1


def post(client: Client, repo: str, pr: int, body: str) -> str:
    """Create the comment, or update the existing one; ``"created"`` or ``"updated"``."""
    existing = _find(client, repo, pr, _author(client))
    if existing is None:
        client.request("POST", f"/repos/{repo}/issues/{pr}/comments", {"body": body})
        return "created"
    client.request("PATCH", f"/repos/{repo}/issues/comments/{existing}", {"body": body})
    return "updated"


def read_body(path: str) -> str:
    where = Path(path)
    try:
        body = where.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise FileNotFoundError(2, "No such file or directory", path) from None
    if MARKER not in body:
        raise ValueError(f"{path} does not carry {MARKER}: write it with `shape ci comment`")
    if len(body) > MAX_BODY:
        raise ValueError(
            f"{path} is {len(body)} characters and GitHub accepts {MAX_BODY}: lower --max-findings"
        )
    return body


def check_target(repo: str, pr: int) -> None:
    if not _REPO.fullmatch(repo):
        raise ValueError(f"--repo must be OWNER/REPO, got {repo!r}")
    if pr < 1:
        raise ValueError(f"--pr must be a pull request number, got {pr}")


def _post(a: argparse.Namespace) -> int:
    try:
        check_target(a.repo, a.pr)
        body = read_body(a.body_file)
        token = os.environ.get("GITHUB_TOKEN", "")
        if not token:
            raise ValueError(f"no token: {_NO_TOKEN_DETAIL}")
        client = Client(os.environ.get("GITHUB_API_URL") or DEFAULT_API, token)
    except (ValueError, OSError) as exc:
        print(f"shape: error: {_redact(exc)}", file=sys.stderr)
        return 2
    try:
        done = post(client, a.repo, a.pr, body)
    except PostError as exc:
        if exc.status in (403, 404):
            print(
                f"shape: notice: the comment was not posted ({exc}): this token cannot write to "
                f"{a.repo} (a pull request from a fork has a read-only token). The check result "
                "still decides the gate.",
                file=sys.stderr,
            )
            print(json.dumps({"posted": False, "status": exc.status}))
            return 0
        print(f"shape: error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"posted": True, "action": done, "repo": a.repo, "pr": a.pr}))
    return 0


def _redact(exc: BaseException) -> str:
    text = str(exc)
    token = os.environ.get("GITHUB_TOKEN", "")
    return text.replace(token, "***") if token else text

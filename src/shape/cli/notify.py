"""Webhook notifications of the checking commands (W6-01).

``shape.yml`` may list ``notifications:`` (``url`` and ``secret`` are ``env://`` or ``file://``
credential references, so no address or secret is in the file) and the commands ``diff``,
``check``, ``verify`` and ``fidelity`` take ``--notify REF`` to add a target for one run. After
a command has decided its result it POSTs one JSON document (format ``shape-notification``) to
every target whose ``on`` matches. The document holds names and counts, never a data value.

Delivery never changes the command's exit code: a target that cannot be reached is a warning on
standard error that names the reference and the host, not the address.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.parse
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from shape.cli.findings import MAX_LISTED, Finding, _entries, verdict_of

# The HTTP client and the project file are imported where they are used:
# every command builds the parser that this module adds --notify to, and they cost more to import
# than every other part of it (INT-18, the stream benchmark's T-19 check).

FORMAT = "shape-notification"
VERSION = 1
#: shape.project.file.NOTIFY_COMMANDS and NOTIFY_ON (a test keeps them equal)
COMMANDS = ("diff", "check", "verify", "fidelity")
ON = ("fail", "drift", "always")
SIGNATURE_HEADER = "X-Shape-Signature-256"
TIMEOUT = 10
#: the pauses before the second and the third attempt
BACKOFF = (1.0, 2.0)
ATTEMPTS = 3

#: replaced by tests, so that they do not wait
sleep: Callable[[float], None] = time.sleep


@dataclass(frozen=True, slots=True)
class Target:
    """A webhook: where (``url`` is a credential reference), when and signed with what."""

    url: str
    on: tuple[str, ...] = ("always",)
    secret: str | None = None
    commands: tuple[str, ...] | None = None

    def wants(self, command: str, verdict: str, findings: int) -> bool:
        if self.commands is not None and command not in self.commands:
            return False
        if "always" in self.on:
            return True
        if "fail" in self.on and verdict == "fail":
            return True
        return "drift" in self.on and findings > 0


# ---- the project's list --------------------------------------------------------------------------


def targets_of(notifications: Sequence[Mapping[str, Any]]) -> list[Target]:
    return [
        Target(
            url=str(n["url"]),
            on=tuple(n["on"]),
            secret=str(n["secret"]) if n.get("secret") else None,
            commands=tuple(n["commands"]) if n.get("commands") else None,
        )
        for n in notifications
    ]


# ---- the document --------------------------------------------------------------------------------


def run_url(environ: Mapping[str, str] | None = None) -> str | None:
    """The URL of the GitHub Actions run, when the environment says there is one."""
    env = os.environ if environ is None else environ
    server, repo, run = (
        env.get(k) for k in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID")
    )
    if server and repo and run:
        return f"{server.rstrip('/')}/{repo}/actions/runs/{run}"
    return None


def payload(
    command: str,
    verdict: str,
    exit_code: int,
    findings: Sequence[Finding],
    *,
    project: str | None,
    source: str | None,
    planned: int = 0,
    now: datetime | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    from shape import __version__

    moment = (now or datetime.now(UTC)).astimezone(UTC)
    ordered = sorted(findings, key=Finding.sort_key)
    return {
        "format": FORMAT,
        "version": VERSION,
        "command": command,
        "project": project,
        "source": source,
        "verdict": verdict,
        "exit_code": exit_code,
        "counts": {"findings": len(ordered), "planned": planned},
        "findings": [f.as_dict() for f in ordered[:MAX_LISTED]],
        "shape_version": __version__,
        "time": moment.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_url": run_url(environ),
    }


def body_of(doc: Mapping[str, Any]) -> bytes:
    """The exact bytes that are sent (and signed)."""
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def sign(secret: str, body: bytes) -> str:
    """The value of the signature header: ``sha256=`` and the HMAC-SHA256 of ``body``, in hex."""
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def verify(secret: str, body: bytes, header: str) -> bool:
    """Whether ``header`` is the signature of ``body`` under ``secret`` (a constant-time check)."""
    return hmac.compare_digest(sign(secret, body), header)


# ---- delivery ------------------------------------------------------------------------------------


class DeliveryError(Exception):
    """A notification was not delivered. The text never holds the address."""


def _resolve(ref: str) -> str:
    from shape.security.credrefs import CredentialReferenceError, resolve_reference

    try:
        return resolve_reference(ref)
    except CredentialReferenceError as exc:
        raise DeliveryError(str(exc)) from None


def _once(url: str, body: bytes, headers: Mapping[str, str]) -> int:
    import http.client

    from shape.cli.prbot import check_url

    parts = check_url(url, "the notification address")  # HTTPS, or loopback HTTP
    target = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    conn = cls(parts.hostname or "", parts.port, timeout=TIMEOUT)
    try:
        conn.request("POST", target, body=body, headers=dict(headers))
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def deliver(target: Target, body: bytes) -> int:
    """POST ``body`` to ``target``: the number of attempts it took. Raises
    :class:`DeliveryError` when it was not delivered.

    HTTPS only (HTTP for a loopback address), no redirects, 10 seconds per attempt, up to three
    attempts with a pause of 1 and 2 seconds after a connection error or a 5xx response, and none
    after a 4xx response (or a redirect)."""
    import http.client

    from shape.cli.prbot import check_url

    try:
        url = _resolve(target.url)
        secret = _resolve(target.secret) if target.secret else None
        check_url(url, "the notification address")
    except ValueError as exc:
        raise DeliveryError(str(exc)) from None
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "shape-notify",
        "Accept": "*/*",
    }
    if secret:
        headers[SIGNATURE_HEADER] = sign(secret, body)
    reason = ""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            status = _once(url, body, headers)
        except (OSError, http.client.HTTPException) as exc:
            reason = f"connection failed ({type(exc).__name__})"
        else:
            if 200 <= status < 300:
                return attempt
            if status < 500:
                raise DeliveryError(f"HTTP {status} (not retried)")
            reason = f"HTTP {status}"
        if attempt < ATTEMPTS:
            sleep(BACKOFF[attempt - 1])
    raise DeliveryError(f"{reason} after {ATTEMPTS} attempts")


def host_of(target: Target) -> str:
    """``scheme://host`` of a target's address, for a message; empty when it cannot be read."""
    try:
        parts = urllib.parse.urlsplit(_resolve(target.url))
    except DeliveryError:
        return ""
    return f"{parts.scheme}://{parts.hostname}" if parts.hostname else ""


def label(target: Target) -> str:
    """How a target is named in a message: its reference and the host, never the address."""
    host = host_of(target)
    return f"{target.url} ({host})" if host else target.url


def send_all(targets: Sequence[Target], doc: Mapping[str, Any]) -> list[tuple[Target, str | None]]:
    """Deliver ``doc`` to every target; each target with the failure text, or None when sent.
    A failure prints a warning on standard error and goes no further."""
    body = body_of(doc)
    out: list[tuple[Target, str | None]] = []
    for t in targets:
        try:
            deliver(t, body)
        except DeliveryError as exc:
            print(f"shape: warning: notification to {label(t)} failed: {exc}", file=sys.stderr)
            out.append((t, str(exc)))
        else:
            out.append((t, None))
    return out


# ---- what a command's run does ----------------------------------------------------------------


def _project_for(a: argparse.Namespace) -> Any:
    """The project in effect, or None (a project that cannot be read is the command's problem)."""
    from shape.project import ProjectError, find_project, load_project

    if getattr(a, "no_project", False):
        return None
    given = getattr(a, "project", None)
    try:
        path = Path(given) if given else find_project()
        return load_project(path) if path is not None else None
    except (OSError, ProjectError):
        return None


def targets_for(command: str, a: argparse.Namespace) -> list[Target]:
    """The targets of this run: the project's, and ``--notify REF`` (which is always sent)."""
    project = _project_for(a)
    found = targets_of(project.notifications) if project is not None else []
    found += [Target(url=ref, on=("always",)) for ref in (getattr(a, "notify", None) or [])]
    return [t for t in found if t.commands is None or command in t.commands]


def observer(command: str, a: argparse.Namespace) -> Callable[[int, Any], None] | None:
    """What to call with the exit code and the printed result once ``command`` has decided, or
    None when this run has no target."""
    targets = targets_for(command, a)
    if not targets:
        return None
    project = _project_for(a)

    def notify(code: int, result: Any) -> None:
        body = result if isinstance(result, dict) else {}
        entries = _entries(body)
        findings = [f for f in entries if f.planned is None]
        block = body.get("project")
        source = block.get("source") if isinstance(block, dict) else None
        verdict = verdict_of(code, len(findings))
        due = [t for t in targets if t.wants(command, verdict, len(findings))]
        if not due:
            return
        doc = payload(
            command,
            verdict,
            code,
            findings,
            project=project.name if project is not None else None,
            source=str(source) if source else None,
            planned=len(entries) - len(findings),
        )
        send_all(due, doc)

    return notify


def add_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--notify",
        action="append",
        default=[],
        metavar="REF",
        help="also send the result to this webhook (an env:// or file:// reference to its "
        "address; repeatable), on top of the notifications of shape.yml",
    )


def plan(command: str, a: argparse.Namespace) -> list[dict[str, str]]:
    """The ``send`` actions of a dry run: each target by its reference. Nothing is resolved or
    opened."""
    from shape.project.file import reference_problem

    actions = []
    for t in targets_for(command, a):
        problem = reference_problem("--notify", t.url)
        if problem:
            raise ValueError(problem)
        actions.append({"action": "send", "target": f"notification {t.url}"})
    return actions


# ---- shape notify test ---------------------------------------------------------------------------


def add_arguments(sub: Any) -> None:
    n = sub.add_parser("notify", help="webhook notifications of shape.yml")
    ns = n.add_subparsers(dest="notify_cmd", required=True)
    t = ns.add_parser(
        "test",
        help="send a test notification to every target of shape.yml",
        description='Send a notification with verdict "test" to every target of '
        "`notifications:` in shape.yml, whatever its `on` and `commands`. Exits 1 if any "
        "delivery failed, 2 if there is no project or it lists no notifications.",
    )
    t.add_argument(
        "--project", metavar="DIR", help="the folder holding shape.yml (default: search)"
    )
    t.add_argument("--json", action="store_true", help="print the outcome as a shape-result")


def run(a: argparse.Namespace) -> int:
    from shape.project import ProjectError, find_project, load_project

    try:
        if a.project:
            folder = Path(a.project)
            found = next(
                (folder / n for n in ("shape.yml", "shape.yaml") if (folder / n).is_file()), None
            )
            if found is None:
                raise FileNotFoundError(2, "no shape.yml in the folder", str(folder))
        else:
            found = find_project()
            if found is None:
                raise FileNotFoundError(2, f"no shape.yml found from {Path.cwd()} upwards", "")
        project = load_project(found)
    except (OSError, ProjectError) as exc:
        print(f"shape: error: {exc}", file=sys.stderr)
        return 2
    targets = targets_of(project.notifications)
    if not targets:
        print(f"shape: error: {project.path} lists no notifications", file=sys.stderr)
        return 2
    doc = payload("notify test", "test", 0, [], project=project.name, source=None)
    results = send_all(targets, doc)
    failed = sum(1 for _, error in results if error)
    print(
        json.dumps(
            {
                "sent": len(results) - failed,
                "failed": failed,
                "targets": [
                    {
                        "url": t.url,
                        "delivered": error is None,
                        **({"error": error} if error else {}),
                    }
                    for t, error in results
                ],
            },
            indent=2,
        )
    )
    return 1 if failed else 0

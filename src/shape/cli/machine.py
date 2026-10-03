"""``--json`` and ``--dry-run`` for every core command (W1-14).

``--json``: the command runs as usual with its standard output captured; the command prints one
``shape-result`` document on standard output (its text goes to standard error) holding the
envelope (``format``, ``version``, ``command``, ``exit_code``) and the keys of what the command
printed. A command whose ``--json`` already takes a file keeps that meaning, and ``-`` means
standard output.

``--dry-run``: the command is not run. Its inputs are read and checked, its targets are resolved
from the arguments (nothing is opened, no one is signed in), and the planned actions are printed;
nothing is written and the exit code is 0, or 2 for an input the command would refuse.

Which commands write, and where, is :data:`SPECS`; ``tests/cli/test_ci_flags_coverage.py`` walks
the parsers so a new command cannot skip it.
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import sys
from collections.abc import Callable, Iterable
from contextlib import redirect_stdout
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shape.cli.introspect import leaves

RESULT_FORMAT = "shape-result"
DRY_RUN_FORMAT = "shape-dry-run"
VERSION = 1
ENVELOPE_KEYS = ("format", "version", "command", "exit_code")
ACTIONS = ("write", "create", "delete", "send")
_JSON_HELP = (
    "print exactly one JSON document on standard output (format shape-result; the text goes to "
    "standard error)"
)
_DRY_HELP = (
    "read and check the inputs and print what the command would write or send, then exit 0 "
    "(2 for invalid input); nothing is written and no connection is opened"
)
_URI_USER = re.compile(r"(?<=://)[^/@\s]*@")


@dataclass(frozen=True)
class Spec:
    """What a writing command touches, as the names of its arguments.

    ``inputs`` are paths the command reads (they must exist); ``outputs`` are files or folders it
    writes; ``targets`` are sinks or URIs (``--sink``, ``--to``); ``plan`` adds actions the
    arguments cannot give on their own (a registry, a project)."""

    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    targets: tuple[str, ...] = ()
    plan: Callable[[argparse.Namespace, list[dict[str, str]]], None] | None = None
    #: the input may also name a source of shape.yml
    sources: bool = False


# ---- the commands that write ---------------------------------------------------------------------


def _plan_registry(a: argparse.Namespace, out: list[dict[str, str]]) -> None:
    root, name = str(a.root), str(a.name)
    cmd = a.action if hasattr(a, "action") else None
    if cmd == "commit":
        out.append({"action": "create", "target": f"{root}/{name}"})
    elif cmd == "tag":
        out.append({"action": "write", "target": f"{root}/{name}#{a.tag}"})
    elif cmd == "promote":
        out.append({"action": "write", "target": f"{root}/{name}#{a.target}"})


def _plan_init(a: argparse.Namespace, out: list[dict[str, str]]) -> None:
    from shape.project.scaffold import FOLDERS, WORKFLOW, parse_sources

    root = Path(a.folder)
    parse_sources(a.source or [])
    if root.exists() and not root.is_dir():
        raise ValueError(f"{root} is not a folder")
    for existing in ("shape.yml", "shape.yaml"):
        if (root / existing).exists() and not a.force:
            raise ValueError(
                f"{root / existing} already exists: init never overwrites a project file "
                "(use --force to replace it)"
            )
    for rel in ("shape.yml", *(f"{f}/.gitkeep" for f in FOLDERS), ".gitattributes"):
        out.append(_file_action(root / rel))
    out.append(_file_action(root / WORKFLOW))


def _plan_git_setup(a: argparse.Namespace, out: list[dict[str, str]]) -> None:
    from shape.cli.gitcmds import _git

    if not Path(a.repo).is_dir():
        raise FileNotFoundError(2, "No such file or directory", a.repo)
    top = Path(_git(["rev-parse", "--show-toplevel"], a.repo))  # reads only
    out.append({"action": "write", "target": str(top / ".git" / "config")})
    out.append(_file_action(top / ".gitattributes"))


def _plan_checkpoint(a: argparse.Namespace, out: list[dict[str, str]]) -> None:
    """A file sink keeps a checkpoint next to its output unless ``--checkpoint`` names one."""
    if not getattr(a, "checkpoint", None) and a.output and getattr(a, "sink", None) == "file":
        out.append(_file_action(f"{a.output}.checkpoint"))


def _plan_notebook(a: argparse.Namespace, out: list[dict[str, str]]) -> None:
    if not a.output:
        out.append(_file_action(f"shape_{a.scenario}_{a.mode}.ipynb"))


def _plan_keygen(a: argparse.Namespace, out: list[dict[str, str]]) -> None:
    for suffix in (".key", ".pub"):
        target = Path(f"{a.prefix}{suffix}")
        if target.exists():
            raise ValueError(f"{target} already exists: keygen never overwrites a key")
        out.append({"action": "create", "target": str(target)})


def _plan_sign(a: argparse.Namespace, out: list[dict[str, str]]) -> None:
    if not a.output:
        out.append({"action": "write", "target": str(a.shape)})


def _jobs_dir(a: argparse.Namespace) -> Path:
    from shape.scale.jobs import default_jobs_dir

    return Path(a.jobs_dir) if a.jobs_dir else default_jobs_dir()


def _plan_job(a: argparse.Namespace, out: list[dict[str, str]]) -> None:
    from shape.scale.jobs import JobNotFoundError, JobStore

    store = JobStore(_jobs_dir(a))
    try:
        record = store.get(a.job_id)
    except JobNotFoundError:
        raise ValueError(f"no job {a.job_id!r}") from None
    if record.fabric:
        out.append({"action": "send", "target": f"fabric job {a.job_id}"})
    out.append({"action": "write", "target": str(store.root / f"{a.job_id}.json")})  # type: ignore[operator]


def _plan_demo_init(a: argparse.Namespace, out: list[dict[str, str]]) -> None:
    from shape.demo.home import check_name, connections_path

    check_name(a.name, "connection name")
    out.append(_file_action(connections_path()))


def _plan_profile_registry(a: argparse.Namespace, out: list[dict[str, str]]) -> None:
    from shape.registry.profiles import SAFE_SUFFIX, SUFFIX, default_root, split_identity

    root = Path(a.root) if a.root else default_root()
    cmd = a.registry_cmd
    if cmd == "save":
        for what, value in (("system", a.system), ("name", a.name)):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", str(value)):
                raise ValueError(f"invalid {what} {value!r}: use letters, digits, . _ - only")
        out.append({"action": "create", "target": f"{root}/{a.system}/<table>/{a.name}{SUFFIX}"})
    elif cmd in ("delete", "tag"):
        system, table, name = split_identity(a.identity)
        found = [root / system / table / f"{name}{s}" for s in (SUFFIX, SAFE_SUFFIX)]
        hit = next((p for p in found if p.is_file()), None)
        if hit is None:
            raise ValueError(f"profile {a.identity!r} not found")
        out.append({"action": "delete" if cmd == "delete" else "write", "target": str(hit)})
    elif cmd == "reindex":
        out.append({"action": "write", "target": str(root / "index")})


def _plan_proposals(a: argparse.Namespace, out: list[dict[str, str]]) -> None:
    if a.proposals_cmd == "decide" and not Path(a.decisions).is_file():
        raise ValueError(f"file not found: {a.decisions}")


def _plan_bridge(a: argparse.Namespace, out: list[dict[str, str]]) -> None:
    if getattr(a, "check", None):
        out.clear()  # `--check` only reads


#: command -> what it touches. ``--json`` and ``--dry-run`` flags that name a path are outputs too
#: (found from the arguments, see :func:`_flag_outputs`).
SPECS: dict[str, Spec] = {
    "capture": Spec(inputs=("src",), outputs=("output",)),
    "profile": Spec(inputs=("src",), outputs=("output", "html"), sources=True),
    "stream-profile": Spec(outputs=("output", "checkpoint")),
    "diff": Spec(inputs=("before", "after")),
    "check": Spec(inputs=("shape", "contract")),
    "verify": Spec(
        inputs=("shape", "schema", "config", "source"), outputs=("output",), sources=True
    ),
    "fidelity": Spec(inputs=("reference", "csv"), outputs=("output",)),
    "design": Spec(inputs=("input",), outputs=("output",)),
    "from-ddl": Spec(inputs=("input_file",), outputs=("output",)),
    "keygen": Spec(plan=_plan_keygen),
    "sign": Spec(inputs=("shape", "key"), outputs=("output",), plan=_plan_sign),
    "learn": Spec(inputs=("input",), outputs=("output",)),
    "emit": Spec(
        outputs=("output", "manifest", "live_report", "live_profile", "checkpoint"),
        targets=("sink", "to"),
        plan=_plan_checkpoint,
    ),
    "stream": Spec(
        outputs=("output", "manifest", "live_report", "live_profile", "checkpoint"),
        targets=("sink", "to"),
        plan=_plan_checkpoint,
    ),
    "mask": Spec(inputs=("input",), outputs=("output",)),
    "continue": Spec(inputs=("input",), outputs=("output",), targets=("to",)),
    "time-travel": Spec(outputs=("output",), targets=("to",)),
    "chaos": Spec(inputs=("input",), outputs=("output", "ground_truth"), targets=("to",)),
    "generate-drift": Spec(inputs=("plan",), outputs=("output",)),
    "pack run": Spec(outputs=("output",)),
    "transform star": Spec(outputs=("output",)),
    "transform cdm": Spec(outputs=("output",)),
    "jobs cancel": Spec(plan=_plan_job),
    "jobs resume": Spec(plan=_plan_job),
    "proposals propose": Spec(inputs=("profile",), outputs=("decisions",)),
    "proposals decide": Spec(outputs=("decisions",), plan=_plan_proposals),
    "bridge schema": Spec(outputs=("out",), plan=_plan_bridge),
    "demo init": Spec(plan=_plan_demo_init),
    "demo notebook": Spec(outputs=("output",), plan=_plan_notebook),
    "demo report": Spec(outputs=("output",)),
    "drift": Spec(inputs=("reference", "current"), outputs=("output",)),
    "registry commit": Spec(inputs=("artifact",), plan=_plan_registry),
    "registry checkout": Spec(outputs=("output", "legacy_output"), plan=None),
    "registry tag": Spec(plan=_plan_registry),
    "registry promote": Spec(plan=_plan_registry),
    "init": Spec(plan=_plan_init),
    "git-setup": Spec(plan=_plan_git_setup),
    "profile export": Spec(inputs=("profile",), outputs=("output",)),
    "profile import": Spec(inputs=("input",), outputs=("output",)),
    "profile safe": Spec(inputs=("profile",), outputs=("output",)),
    "profile validate": Spec(inputs=("artifact",)),
    "profile registry save": Spec(inputs=("source",), plan=_plan_profile_registry),
    "profile registry delete": Spec(plan=_plan_profile_registry),
    "profile registry tag": Spec(plan=_plan_profile_registry),
    "profile registry reindex": Spec(plan=_plan_profile_registry),
}

#: commands that print only when asked and otherwise have a ``--dry-run`` of their own.
NATIVE_DRY_RUN = frozenset({"generate", "demo run", "demo cleanup"})


# ---- parsers -------------------------------------------------------------------------------------


def _has(parser: argparse.ArgumentParser, flag: str) -> argparse.Action | None:
    for act in parser._actions:
        if flag in act.option_strings:
            return act
    return None


def install(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> None:
    """Add ``--json`` where a core command lacks it and ``--dry-run`` where it writes and lacks
    it, for every command below ``parser``."""
    for words, leaf, _, _ in leaves(parser, prefix, prefix):
        path = " ".join(words)
        if path in NOT_APPLICABLE:
            continue
        if _has(leaf, "--json") is None:
            leaf.add_argument("--json", action="store_true", help=_JSON_HELP)
        own = _has(leaf, "--dry-run")
        if own is not None:
            leaf.set_defaults(native_dry_run=True)
        elif path in SPECS:
            leaf.add_argument("--dry-run", action="store_true", help=_DRY_HELP)


#: Commands that get neither flag (``tests/cli/ci_flags_exemptions.json`` says why): ``version``
#: already prints one JSON document, and ``bridge`` is a server whose standard output is its
#: protocol.
NOT_APPLICABLE = frozenset({"version", "bridge"})


def command_path(parser: argparse.ArgumentParser, ns: argparse.Namespace) -> str:
    """The command ``ns`` was parsed as (``"registry commit"``), from the sub-command arguments."""
    words: list[str] = []
    current = parser
    while True:
        actions = [a for a in current._actions if isinstance(a, argparse._SubParsersAction)]
        if not actions:
            return " ".join(words)
        name = getattr(ns, actions[0].dest, None)
        if name is None or name not in actions[0].choices:
            return " ".join(words)
        sub = actions[0].choices[name]
        canonical = next(n for n, p in actions[0].choices.items() if p is sub)
        words.append(canonical)
        current = sub


# ---- running -------------------------------------------------------------------------------------


def run(path: str, a: argparse.Namespace, fn: Callable[[], int]) -> int:
    """Run ``fn`` for the command ``path``, under ``--dry-run`` and ``--json``."""
    from shape.cli import errors

    value = getattr(a, "json", None)
    json_mode = value is True or value == "-"
    if value == "-":
        a.json = None  # the command prints its result as usual; the envelope goes to stdout
    if getattr(a, "dry_run", False) and not getattr(a, "native_dry_run", False):
        return _dry_run(path, a, json_mode)
    if not json_mode:
        return fn()
    return _enveloped(path, fn, errors)


class _Tee(io.TextIOBase):
    """Stderr that also remembers what was written."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.seen: list[str] = []

    def write(self, text: str) -> int:
        self.seen.append(text)
        return int(self.inner.write(text))

    def flush(self) -> None:
        self.inner.flush()


def parse_output(text: str) -> tuple[Any, str]:
    """``(payload, human_text)`` of what a command printed: one JSON document is the payload (and
    there is no human text); JSON lines are a list; anything else is ``{"output": text}`` and is
    also the text to show on standard error."""
    stripped = text.strip()
    if not stripped:
        return {}, ""
    try:
        return json.loads(stripped), ""
    except ValueError:
        pass
    lines = [ln for ln in stripped.splitlines() if ln.strip()]
    try:
        return [json.loads(ln) for ln in lines], ""
    except ValueError:
        return {"output": text}, text


def envelope(
    command: str, exit_code: int, payload: Any, error: str | None = None
) -> dict[str, Any]:
    """The ``shape-result`` document. A payload object's keys are kept at the top level; the
    whole payload is also under ``payload`` when it is not an object or has a key that is an
    envelope key (which the envelope's value then replaces)."""
    doc: dict[str, Any] = {}
    if isinstance(payload, dict):
        doc.update(payload)
    clash = isinstance(payload, dict) and any(k in payload for k in ENVELOPE_KEYS)
    doc.update(format=RESULT_FORMAT, version=VERSION, command=command, exit_code=exit_code)
    if error:
        doc["error"] = error
    if clash or not isinstance(payload, dict):
        doc["payload"] = payload
    return doc


def _enveloped(path: str, fn: Callable[[], int], errors: Any) -> int:
    from shape.cli import lifecycle
    from shape.security.redact import redact_text

    lifecycle.quick_exit_allowed = False  # the document is printed after the command returns
    buf = io.StringIO()
    tee = _Tee(sys.stderr)
    saved = sys.stderr
    sys.stderr = tee
    error: str | None = None
    try:
        with redirect_stdout(buf):
            code = fn()
    except errors.EXPECTED as exc:
        if errors.debug_enabled():
            raise
        code = errors.fail(exc)
        error = redact_text(errors.describe(exc))
    finally:
        sys.stderr = saved
    payload, human = parse_output(buf.getvalue())
    if human:
        sys.stderr.write(human)
    if error is None and code != 0:
        lines = [x.strip() for x in "".join(tee.seen).splitlines() if "shape: error:" in x]
        if lines:
            error = " ".join(ln.split("shape: error:", 1)[1].strip() for ln in lines)
    print(json.dumps(envelope(path, int(code or 0), payload, error), default=str))
    return int(code or 0)


# ---- dry run -------------------------------------------------------------------------------------


def redact(target: str) -> str:
    """``target`` without credentials: user information of a URI and secret-looking text."""
    from shape.security.redact import redact_text

    return str(redact_text(_URI_USER.sub("", str(target))))


def _file_action(path: Path | str) -> dict[str, str]:
    p = Path(path)
    return {"action": "write" if p.exists() else "create", "target": str(p)}


def _values(value: Any) -> list[str]:
    if value is None or value is False or value == "":
        return []
    if isinstance(value, list | tuple):
        return [str(v) for v in value if v not in (None, "")]
    return [str(value)]


def _check_input(a: argparse.Namespace, dest: str, spec: Spec) -> None:
    for value in _values(getattr(a, dest, None)):
        if value == "-" or "://" in value:
            continue  # standard input, or a URI: nothing to look at without a connection
        if os.path.exists(value) or (glob.has_magic(value) and glob.glob(value)):
            continue
        if spec.sources and _is_project_source(a, value):
            continue
        raise FileNotFoundError(2, "No such file or directory", value)


def _is_project_source(a: argparse.Namespace, name: str) -> bool:
    from shape.cli import project as project_cli

    try:
        ctx = project_cli.context(a)
    except Exception:  # noqa: BLE001 - a broken shape.yml means "not a source"
        return False
    return bool(ctx and name in ctx.project.sources)


def _check_output(path: str) -> None:
    here = Path(path).absolute()
    for parent in here.parents:
        if parent.exists():
            if not parent.is_dir():
                raise ValueError(f"cannot write {path}: {parent} is not a folder")
            break


def _target_actions(values: Iterable[str]) -> list[dict[str, str]]:
    out = []
    for v in values:
        if v in ("console", "memory"):
            continue
        scheme = v.partition("://")[0] if "://" in v else ""
        if scheme in ("file", "jsonl"):
            out.append(_file_action(v.partition("://")[2]))
        elif not scheme and v == "file":
            continue  # `--sink file` writes to -o, which is listed on its own
        elif scheme or v:
            out.append({"action": "send", "target": redact(v)})
    return out


def _flag_outputs(a: argparse.Namespace) -> list[str]:
    """Report files the checking commands write: ``--junit``, ``--sarif`` (or the ``ci:``
    defaults of shape.yml), and ``--json FILE``."""
    out = _values(getattr(a, "junit", None)) + _values(getattr(a, "sarif", None))
    value = getattr(a, "json", None)
    if isinstance(value, str) and value != "-":
        out.append(value)
    if hasattr(a, "junit"):
        from shape.cli import ci

        defaults, root = ci.project_ci(a)
        command = getattr(a, "_command", "")
        for key in ("junit", "sarif"):
            if not getattr(a, key, None):
                found = ci.resolve_path(None, defaults.get(key), command, root)
                if found is not None:
                    out.append(str(found))
    return out


def plan_actions(path: str, a: argparse.Namespace) -> list[dict[str, str]]:
    """Validate the inputs of ``path`` and return what it would do. Raises for an input the
    command would refuse (a missing file, a bad name)."""
    spec = SPECS[path]
    for dest in spec.inputs:
        _check_input(a, dest, spec)
    actions: list[dict[str, str]] = []
    outputs = [v for dest in spec.outputs for v in _values(getattr(a, dest, None))]
    outputs += _flag_outputs(a)
    for value in dict.fromkeys(outputs):
        _check_output(value)
        actions.append(_file_action(value))
    for dest in spec.targets:
        actions.extend(_target_actions(_values(getattr(a, dest, None))))
    if spec.plan is not None:
        spec.plan(a, actions)
    seen: set[tuple[str, str]] = set()
    unique = []
    for act in actions:
        key = (act["action"], act["target"])
        if key not in seen:
            seen.add(key)
            unique.append(act)
    return unique


def dry_run_document(
    path: str, actions: list[dict[str, str]], error: str | None = None
) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "format": DRY_RUN_FORMAT,
        "version": VERSION,
        "command": path,
        "actions": actions,
    }
    if error:
        doc["error"] = error
    return doc


def _dry_run(path: str, a: argparse.Namespace, json_mode: bool) -> int:
    from shape.cli import errors
    from shape.security.redact import redact_text

    try:
        a._command = path
        actions = plan_actions(path, a)
    except errors.EXPECTED as exc:
        if errors.debug_enabled():
            raise
        code = errors.fail(exc)
        if json_mode:
            doc = dry_run_document(path, [], redact_text(errors.describe(exc)))
            print(json.dumps(doc, sort_keys=True))
        return code
    if json_mode:
        print(json.dumps(dry_run_document(path, actions), sort_keys=True))
        return 0
    if not actions:
        print(f"shape {path}: nothing would be written")
    for act in actions:
        print(f"would {act['action']:<6} {act['target']}")
    return 0


__all__ = [
    "ACTIONS",
    "DRY_RUN_FORMAT",
    "RESULT_FORMAT",
    "SPECS",
    "envelope",
    "install",
    "parse_output",
    "plan_actions",
    "run",
]

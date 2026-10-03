"""The exit codes of every core command, in one place (W1-14).

``docs/EXIT_CODES.md`` is generated from :data:`CODES` (``python scripts/gen_exit_codes.py``), and
each command's ``--help`` ends with the same table (:func:`epilog`). The classes are those of
``docs/CLI.md``: 0 ok, 1 a check failed, 2 bad input, 3 and above a command's own verdict. A
command's codes are only ever described here, never changed: the registry follows the commands,
and ``tests/cli/test_exit_codes.py`` keeps the two together (every core command has an entry, the
flags that signal a verdict imply their code, and spot checks run the commands).
"""

from __future__ import annotations

import argparse
import textwrap

from shape.cli.introspect import leaves

#: Every command can end in these: 0 when it did its work, 2 for an input it cannot use (an
#: unreadable or wrong file, a bad argument, a failed expected condition that is not a verdict).
OK = "ok"
BAD = "bad input: a missing or unreadable file, the wrong kind of file, or a bad argument"
SIGNATURE = "a `--verify` public key does not verify an input, or an input's signature is invalid"
LEAK = "the leak scan found a value"

#: alias -> command
ALIASES = {"show": "inspect", "compare": "fidelity"}

#: Codes other than 0 (ok) and 2 (bad input), which every command has, by command.
_EXTRA: dict[str, dict[int, str]] = {
    "doctor": {1: "a required package is missing"},
    "conformance": {1: "a conformance case failed"},
    "version": {},
    "plugins list": {},
    "plugins info": {1: "the plugin loads with a problem (its status is not ok)"},
    "plugins doctor": {1: "a plugin failed to load"},
    "capture": {},
    "profile": {},
    "profile export": {},
    "profile import": {},
    "profile list": {},
    "profile validate": {
        1: "the profile is not well formed (or, with `--safe`, the leak scan found a value)"
    },
    "profile safe": {},
    "profile registry list": {},
    "profile registry save": {},
    "profile registry delete": {1: "the profile is not in the registry"},
    "profile registry tag": {},
    "profile registry diff": {1: "the profiles differ (with `--fail-on-diff`)"},
    "profile registry reindex": {},
    "profile registry validate": {1: "the store, the profile or the data has a problem"},
    "stream-profile": {},
    "diff": {1: "drift was found (with `--fail-on-drift`), or " + SIGNATURE},
    "inspect": {1: SIGNATURE},
    "cat": {},
    "git-setup": {},
    "design": {1: "the design has errors (warnings too with `--strict`)"},
    "from-ddl": {},
    "keygen": {},
    "sign": {},
    "validate": {1: "the schema or contract is not valid"},
    "verify": {
        1: "a validation gate failed that is enforced (a warning too with `--strict`), or "
        "the `.shape` file's signature is invalid"
    },
    "quality": {},
    "generate": {
        1: "the plan has problems (`--dry-run`), or a sink or the scale run failed",
        130: "interrupted during a scale run (the job can be resumed)",
    },
    "describe": {},
    "list": {},
    "presets": {},
    "learn": {},
    "emit": {1: "a live target failed (with `--live-fail`)"},
    "stream": {1: "a live target failed (with `--live-fail`)"},
    "mask": {},
    "continue": {},
    "time-travel": {},
    "chaos": {},
    "generate-drift": {},
    "pack run": {1: "the pack or spec is not valid, or the run failed"},
    "pack replay": {1: "the replay does not match the recorded run"},
    "pack validate": {1: "the pack or spec is not valid"},
    "pack list": {},
    "transform star": {},
    "transform cdm": {},
    "jobs list": {},
    "jobs status": {1: "the job failed"},
    "jobs cancel": {1: "the job is in a state that cannot be cancelled, or it failed"},
    "jobs resume": {1: "the job is in a state that cannot be resumed, or it failed"},
    "proposals propose": {},
    "proposals list": {},
    "proposals decide": {},
    "bridge": {1: "with `--once`: the request failed"},
    "bridge schema": {1: "the schemas in DIR differ from the ones Shape ships (with `--check`)"},
    "demo init": {1: "the connection profile could not be saved"},
    "demo list": {},
    "demo run": {1: "the run failed"},
    "demo preflight": {1: "a preflight check failed"},
    "demo cleanup": {1: "something could not be removed"},
    "demo status": {1: "a check failed"},
    "demo notebook": {},
    "demo report": {},
    "fidelity": {
        1: "a pass mark was missed",
        3: "with a REFERENCE.json profile: the certificate failed",
    },
    "key": {},
    "fd": {},
    "privacy-k": {},
    "query": {1: SIGNATURE},
    "check": {
        1: "a contract rule failed on a profile, or " + SIGNATURE,
        4: "a contract rule failed on a Shape model or evidence document",
    },
    "compatibility": {5: "the change is not compatible in the mode asked for"},
    "plan": {1: SIGNATURE},
    "certify-shapes": {3: "the certificate score is below `--threshold`"},
    "drift": {1: "drift was found"},
    "registry commit": {1: "not committed: the leak scan found a value in the artifact"},
    "registry checkout": {},
    "registry tag": {},
    "registry promote": {},
    "registry log": {},
    "registry list": {},
    "registry show": {},
    "registry diff": {},
    "init": {},
    "project validate": {},
}

#: ``quality`` is the one command whose "failed" verdict has always been 2.
_OVERRIDE: dict[str, dict[int, str]] = {
    "quality": {2: "a rule was violated, or " + BAD},
    "project validate": {2: "shape.yml is not valid, or " + BAD},
}

CODES: dict[str, dict[int, str]] = {
    path: {0: OK, 2: BAD, **extra, **_OVERRIDE.get(path, {})} for path, extra in _EXTRA.items()
}

_RAW = "\x00"  # in an epilog: the text after it is kept as written


def codes(path: str) -> dict[int, str]:
    """The exit codes of the command ``path`` (``"registry commit"``; an alias works), by code."""
    return dict(sorted(CODES[ALIASES.get(path, path)].items()))


def epilog(path: str) -> str:
    """The exit-code table for a command's ``--help``."""
    lines = ["exit codes:"]
    for code, meaning in codes(path).items():
        first, *rest = textwrap.wrap(meaning, width=64) or [""]
        lines.append(f"  {code:<3} {first}")
        lines.extend(f"      {line}" for line in rest)
    return "\n".join(lines).replace("exit codes:", "Exit codes:", 1)


class HelpFormatter(argparse.HelpFormatter):
    """Wraps text as usual, but keeps what follows a NUL as written (the exit-code table)."""

    def _fill_text(self, text: str, width: int, indent: str) -> str:
        head, sep, raw = text.partition(_RAW)
        out = super()._fill_text(head, width, indent) if head.strip() else ""
        if not sep:
            return out
        table = "\n".join(indent + line for line in raw.splitlines())
        return f"{out}\n\n{table}" if out else table


def apply(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> None:
    """Put the exit codes of every command below ``parser`` at the end of its ``--help``. A
    command that has no entry is left alone (the registry test fails for it)."""
    for words, leaf, _, _ in leaves(parser, prefix, prefix):
        path = " ".join(words)
        if path not in CODES:
            continue
        leaf.formatter_class = HelpFormatter
        leaf.epilog = (f"{leaf.epilog}\n\n" if leaf.epilog else "") + _RAW + epilog(path)

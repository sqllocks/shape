"""``shape profile safe`` and ``shape profile validate --safe``."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from collections.abc import Sequence
from typing import Any

from .safe_profile import ColumnConfig, SafeConfig, to_safe_profile
from .safe_validator import SafeProfileValidator, ValidationResult

COMMANDS = ("safe", "validate")


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="shape profile")
    sub = p.add_subparsers(dest="cmd", required=True)
    sf = sub.add_parser("safe", help="write the safe-to-share form of a profile")
    sf.add_argument("profile", metavar="PROFILE.shape")
    sf.add_argument("-o", "--output", required=True, metavar="SAFE.json")
    sf.add_argument("--k", type=int, metavar="N", help="minimum cohort (default 5)")
    sf.add_argument("--sensitive", action="store_true", help="raise the minimum cohort to 11")
    sf.add_argument(
        "--column-k", action="append", default=[], metavar="COLUMN=N", help="per-column k"
    )
    sf.add_argument(
        "--compact",
        action="store_true",
        help="write one line without null fields (smaller; reads back the same)",
    )
    sf.add_argument(
        "--columns",
        action="append",
        default=[],
        metavar="NAMES",
        help="keep only these columns (comma-separated names or * patterns; repeatable)",
    )
    sf.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="NAMES",
        help="drop these columns (comma-separated names or * patterns; repeatable)",
    )
    sf.add_argument(
        "--unsafe-full-fidelity",
        action="store_true",
        help="turn the disclosure controls off; the result is stamped unsafe and fails "
        "`validate --safe`",
    )
    va = sub.add_parser("validate", help="scan a serialized profile for value leaks")
    va.add_argument("artifact", metavar="ARTIFACT")
    va.add_argument("--safe", action="store_true", help="run the safe-profile leak scanner")
    va.add_argument("--json", action="store_true", help="machine-readable output")
    from shape.cli import ci
    from shape.cli.project import add_project_flags

    ci.add_flags(va)
    add_project_flags(va, source=False)
    from shape.cli import exitcodes, machine

    machine.install(p, ("profile",))
    exitcodes.apply(p, ("profile",))
    return p


def _column_k(items: Sequence[str]) -> dict[str, ColumnConfig]:
    out: dict[str, ColumnConfig] = {}
    for item in items:
        name, sep, n = item.rpartition("=")
        if not sep or not name or not n.isdigit():
            raise ValueError(f"--column-k expects COLUMN=N, got {item!r}")
        out[name] = ColumnConfig(k=int(n))
    return out


def _scan(path: str) -> ValidationResult:
    """Validate a JSON file, or the decoded profile inside a ``.shape`` artifact."""
    validator = SafeProfileValidator()
    if zipfile.is_zipfile(path):
        import shape

        prof = shape.load(path)
        data: Any = prof.to_dict()
        data["capture"] = prof.capture  # a profile that does not say reads as full
        if prof.redaction_manifest:  # the marker of a safe capture; a full one has none
            data["redaction_manifest"] = prof.redaction_manifest
        return validator.validate_data(data, path=path)
    return validator.validate_file(path)


def _validate(a: argparse.Namespace) -> int:
    if not a.safe:
        print("shape: error: specify --safe to run the safe-profile leak scanner", file=sys.stderr)
        return 2
    from shape.cli import ci

    t0 = ci.started()
    result = _scan(a.artifact)
    ci.write_reports(
        a, "profile validate", ci.checks_from_leaks(a.artifact, result.findings), a.artifact, t0
    )
    if a.json:
        print(json.dumps(result.to_dict(), indent=2))
    elif result.is_clean:
        print(f"CLEAN: no leaks found in {result.path}")
    else:
        print(f"LEAK: {len(result.findings)} finding(s) in {result.path}:", file=sys.stderr)
        for f in result.findings:
            print(f"  [{f.rule}] {f.path}: {f.detail}", file=sys.stderr)
    return result.exit_code


def _safe(a: argparse.Namespace) -> int:
    cfg = SafeConfig(k=a.k, sensitive=a.sensitive, columns=_column_k(a.column_k))
    safe = to_safe_profile(a.profile, cfg, unsafe_full_fidelity=a.unsafe_full_fidelity)
    include = [n for item in a.columns for n in item.split(",") if n]
    exclude = [n for item in a.exclude for n in item.split(",") if n]
    if include or exclude:
        safe = safe.select_columns(include, exclude)
    safe.save(a.output, compact=a.compact)
    print(json.dumps({"written": a.output, "unsafe": safe.unsafe}, sort_keys=True))
    return 0


def main(argv: Sequence[str]) -> int:
    """Run a ``profile safe|validate`` command: 0 ok, 1 leak found, 2 input error."""
    from shape.cli import machine

    parser = _parser()
    a = parser.parse_args(list(argv))

    def run() -> int:
        try:
            return _validate(a) if a.cmd == "validate" else _safe(a)
        except (OSError, ValueError, KeyError, ImportError, zipfile.BadZipFile) as exc:
            from shape.cli import errors

            if errors.debug_enabled():
                raise
            return errors.fail(exc)

    return machine.run(f"profile {a.cmd}", a, run)

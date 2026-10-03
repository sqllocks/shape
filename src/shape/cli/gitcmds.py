"""``shape cat`` and ``shape git-setup``: a .shape file as a git-diffable text form.

``shape cat FILE`` prints one ``path: value`` line per leaf property, sorted by path, with no
volatile field (no content hashes, no signature, no timestamps). Used as a git ``textconv``
filter, a changed property is exactly one changed line and the line names the property.
``shape git-setup`` writes that configuration into a repository, idempotently.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

# Manifest fields that change with the content or the signing key, not with a property.
_VOLATILE_MANIFEST = frozenset({"content_hashes", "shape_content_id", "source_content_id"})
_PLAIN_KEY = re.compile(r"[A-Za-z0-9_\-]+")
DEFAULT_PATTERN = "*.shape"
DEFAULT_TEXTCONV = "shape cat"
_DRIVER = "shape"
VAULT_IGNORE = "*.shapevault"


def _scalar(v: Any) -> str:
    if isinstance(v, float) and not math.isfinite(v):
        return "NaN" if math.isnan(v) else ("Infinity" if v > 0 else "-Infinity")
    return json.dumps(v, ensure_ascii=False, allow_nan=False)


def _child(path: str, k: Any) -> str:
    s = str(k)
    if not _PLAIN_KEY.fullmatch(s):
        return f"{path}[{json.dumps(s, ensure_ascii=False)}]"
    return f"{path}.{s}" if path else s


def flatten(obj: Any, path: str = "") -> list[str]:
    """``path: value`` lines for every leaf of ``obj``: keys sorted, list order kept, an empty
    container written as ``{}`` or ``[]``."""
    if isinstance(obj, dict) and obj:
        return [ln for k in sorted(obj, key=str) for ln in flatten(obj[k], _child(path, k))]
    if isinstance(obj, list | tuple) and obj:
        return [ln for i, item in enumerate(obj) for ln in flatten(item, f"{path}[{i}]")]
    if isinstance(obj, dict | list | tuple):
        return [f"{path or '$'}: {'{}' if isinstance(obj, dict) else '[]'}"]
    return [f"{path or '$'}: {_scalar(obj)}"]


def _document(path: str) -> dict[str, Any]:
    """The diffable document of a .shape artifact (sniffed by content: git hands a textconv
    filter a temporary file without the extension) or of a JSON file such as a safe profile."""
    if zipfile.is_zipfile(path):
        from shape.artifact.io import read_artifact

        manifest, parts = read_artifact(path)
        doc: dict[str, Any] = {
            "manifest": {k: v for k, v in manifest.items() if k not in _VOLATILE_MANIFEST}
        }
        if manifest.get("kind") == "profile":
            import shape

            doc["profile"] = shape.load(path).to_dict()
        else:
            from shape.artifact import read_model

            doc["model"] = read_model(path)[1]
        del parts
        return doc
    with open(path, encoding="utf-8") as fh:
        return {"json": json.load(fh)}


def render(path: str, fmt: str = "lines") -> str:
    """The text form of ``path``: ``lines`` (one property per line) or ``json`` (pretty-printed,
    sorted keys). Both end with a newline."""
    doc = _document(path)
    if fmt == "json":
        return json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n"
    out: list[str] = []
    for section in sorted(doc):
        # The profile's own paths are the readable ones: columns.email.null_rate
        out.extend(flatten(doc[section], "" if section in ("profile", "json") else section))
    return "\n".join(out) + "\n"


def cat(a: argparse.Namespace) -> int:
    text = render(a.file, "json" if a.json else "lines")
    sys.stdout.write(text)
    return 0


def _git(args: list[str], cwd: str) -> str:
    try:
        r = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, timeout=30
        )
    except FileNotFoundError as e:
        raise ValueError("git is not installed or not on PATH") from e
    if r.returncode != 0:
        raise ValueError(f"git {' '.join(args)} failed: {r.stderr.strip() or r.stdout.strip()}")
    return r.stdout.strip()


def _has_rule(lines: list[str], pattern: str) -> bool:
    for ln in lines:
        parts = ln.split()
        if parts and parts[0] == pattern and f"diff={_DRIVER}" in parts[1:]:
            return True
    return False


def git_setup(a: argparse.Namespace) -> int:
    """Write ``diff.shape.textconv`` (repository-local) and ``<pattern> diff=shape`` lines in
    the repository's ``.gitattributes``. Running it again changes nothing."""
    top = _git(["rev-parse", "--show-toplevel"], a.repo)
    patterns = a.pattern or [DEFAULT_PATTERN]
    for p in patterns:
        if not p or any(c.isspace() or c == "\x00" for c in p):
            raise ValueError(f"invalid --pattern {p!r}: no whitespace or control characters")
    command = a.command or (DEFAULT_TEXTCONV if shutil.which("shape") else None)
    if command is None:
        command = f'"{sys.executable}" -m shape.cli.main cat'
    _git(["config", "--local", f"diff.{_DRIVER}.textconv", command], top)
    attrs = Path(top) / ".gitattributes"
    if attrs.is_symlink():
        raise ValueError(f"{attrs} is a symbolic link; refusing to write through it")
    existing = attrs.read_text(encoding="utf-8").splitlines() if attrs.exists() else []
    added = [p for p in patterns if not _has_rule(existing, p)]
    if added:
        text = "\n".join(existing + [f"{p} diff={_DRIVER}" for p in added]) + "\n"
        attrs.write_text(text, encoding="utf-8")
    ignore = Path(top) / ".gitignore"
    if ignore.is_symlink():
        raise ValueError(f"{ignore} is a symbolic link; refusing to write through it")
    ignored = ignore.read_text(encoding="utf-8").splitlines() if ignore.exists() else []
    vault_ignored = VAULT_IGNORE not in (ln.strip() for ln in ignored)
    if vault_ignored:  # a value vault is encrypted, but it is not meant for version control
        ignore.write_text("\n".join([*ignored, VAULT_IGNORE]) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "repository": top,
                "textconv": command,
                "gitattributes": str(attrs),
                "patterns": patterns,
                "added": added,
                "gitignore": str(ignore),
                "gitignore_added": [VAULT_IGNORE] if vault_ignored else [],
            },
            sort_keys=True,
        )
    )
    return 0


def add_parsers(sub: Any) -> None:
    c = sub.add_parser(
        "cat",
        help="print a .shape artifact as git-diffable text (one property per line)",
        description="One `path: value` line per property, sorted, with no volatile field. "
        "Set as a git textconv filter by `shape git-setup`. A .shape file holds real values: "
        "so does this output; only `shape profile safe` JSON is meant to be shared.",
    )
    c.add_argument("file", metavar="FILE", help="a .shape artifact or a JSON file")
    c.add_argument("--json", action="store_true", help="pretty-printed JSON with sorted keys")
    g = sub.add_parser(
        "git-setup",
        help="configure this git repository to show readable diffs of .shape files",
        description="Writes `diff.shape.textconv` to the repository's local git config and "
        "`*.shape diff=shape` to .gitattributes, and adds `*.shapevault` to .gitignore (a value "
        "vault is encrypted but not meant for version control). Safe to run again.",
    )
    g.add_argument("--repo", default=".", metavar="DIR", help="a directory in the repository")
    g.add_argument(
        "--pattern",
        action="append",
        metavar="GLOB",
        help="a path pattern to diff this way (default: *.shape); repeatable, e.g. *.safe.json",
    )
    g.add_argument(
        "--command", metavar="CMD", help=f"the textconv command (default: {DEFAULT_TEXTCONV!r})"
    )


def run(a: argparse.Namespace) -> int:
    return cat(a) if a.cmd == "cat" else git_setup(a)

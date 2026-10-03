"""``shape registry ROOT ACTION ...``: the content-addressed registry of artifacts.

The registry keeps whatever bytes are committed under a name, addressed by their sha256, with a
log of commits (and the metadata they were given). It is a plain directory, so it is natural to
back up or put under git, and that is why a raw profile (real values) is refused unless
``--allow-raw`` is given: commit the safe form instead (``--safe``, or the output of
``shape profile safe``). The profile registry (``shape profile registry``) is a different store.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ACTIONS = ("commit", "checkout", "tag", "promote", "log", "list", "show", "diff")


def _dump(obj: Any) -> None:
    print(json.dumps(obj, sort_keys=True, default=str))


def _stdout_is_terminal() -> bool:
    return sys.stdout.isatty()


def add_arguments(sub: Any) -> None:
    rg = sub.add_parser(
        "registry",
        help="a content-addressed history of artifacts (commit, log, checkout, tag, promote)",
        description="Keep named, content-addressed versions of artifacts in a directory. "
        "`shape registry ROOT ACTION ...`. A raw profile holds real values and is refused: "
        "commit the safe form (--safe, or the output of `shape profile safe`). This is not "
        "`shape profile registry`, the catalog of named profiles.",
    )
    rg.add_argument("root", metavar="ROOT", help="the registry directory (created when missing)")
    acts = rg.add_subparsers(dest="action", metavar="ACTION", required=True)

    c = acts.add_parser("commit", help="record a new version of NAME")
    c.add_argument("name", metavar="NAME", help="what the versions are called")
    c.add_argument("artifact", metavar="ARTIFACT", help="the file to commit")
    c.add_argument(
        "--meta",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="record a value in the log entry (repeatable)",
    )
    c.add_argument(
        "--business-date",
        metavar="YYYY-MM-DD",
        help="record the date the data is about (same as --meta business_date=...)",
    )
    form = c.add_mutually_exclusive_group()
    form.add_argument(
        "--safe",
        action="store_true",
        help="ARTIFACT is a raw profile: commit its share-safe form (as `shape profile safe`)",
    )
    form.add_argument(
        "--allow-raw",
        action="store_true",
        help="commit a raw profile as it is: the registry then holds real values from the data",
    )
    c.add_argument("--k", type=int, metavar="N", help="with --safe: minimum cohort (default 5)")
    c.add_argument(
        "--sensitive", action="store_true", help="with --safe: raise the minimum cohort to 11"
    )

    co = acts.add_parser("checkout", help="write a version of NAME to a file")
    co.add_argument("name", metavar="NAME")
    co.add_argument("ref", metavar="REF", nargs="?", default="latest", help="default: latest")
    co.add_argument("legacy_output", metavar="OUT", nargs="?", help="same as -o")
    co.add_argument("-o", "--output", metavar="OUT", help="the file to write")

    t = acts.add_parser("tag", help="give a version of NAME a tag")
    t.add_argument("name", metavar="NAME")
    t.add_argument("tag", metavar="TAG")
    t.add_argument("ref", metavar="REF", nargs="?", default="latest", help="default: latest")

    p = acts.add_parser("promote", help="point the ref TARGET at the version SOURCE of NAME")
    p.add_argument("name", metavar="NAME")
    p.add_argument("source", metavar="SOURCE", help="a ref, tag or content id")
    p.add_argument("target", metavar="TARGET", help="the ref to set, e.g. production")

    lg = acts.add_parser("log", help="every commit of NAME, oldest first")
    lg.add_argument("name", metavar="NAME")

    acts.add_parser("list", help="the names in the registry")

    sw = acts.add_parser("show", help="one log entry of NAME")
    sw.add_argument("name", metavar="NAME")
    sw.add_argument("ref", metavar="REF", nargs="?", default="latest", help="default: latest")

    d = acts.add_parser("diff", help="what changed between two versions of NAME")
    d.add_argument("name", metavar="NAME")
    d.add_argument("ref1", metavar="REF1")
    d.add_argument("ref2", metavar="REF2")


def _readable(entry: dict[str, Any]) -> dict[str, Any]:
    """The log entry plus ``created``, the commit time in UTC (``created_at`` stays the epoch)."""
    from datetime import UTC, datetime

    out = dict(entry)
    stamp = entry.get("created_at")
    if isinstance(stamp, (int, float)):
        out["created"] = datetime.fromtimestamp(stamp, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def _metadata(a: argparse.Namespace) -> dict[str, str]:
    meta: dict[str, str] = {}
    for item in a.meta:
        key, eq, value = item.partition("=")
        if not eq or not key.strip():
            raise ValueError(f"--meta expects KEY=VALUE, got {item!r}")
        if key in meta:
            raise ValueError(f"--meta {key} was given twice")
        meta[key] = value
    if a.business_date:
        import datetime

        try:
            datetime.date.fromisoformat(a.business_date)
        except ValueError:
            raise ValueError(f"invalid business date {a.business_date!r}: use YYYY-MM-DD") from None
        if "business_date" in meta:
            raise ValueError("business_date was given twice (--meta and --business-date)")
        meta["business_date"] = a.business_date
    if "profile_form" in meta:
        raise ValueError("profile_form is set by Shape (safe or raw): drop --meta profile_form")
    return meta


def _safe_form(a: argparse.Namespace, path: str) -> bytes:
    """The safe-profile JSON of the raw profile at ``path``."""
    import shape
    from shape.cli.profiles import read_export
    from shape.privacy.safe_profile import SafeConfig, to_safe_profile

    prof = shape.load(path) if Path(path).read_bytes()[:2] == b"PK" else read_export(path)
    cfg = SafeConfig(k=a.k, sensitive=a.sensitive)
    return to_safe_profile(prof, cfg).to_json().encode("utf-8")


def _leaks(doc: Any, label: str) -> list[str]:
    """What the leak scanner finds in a safe-profile document (empty: clean)."""
    from shape.privacy.safe_validator import SafeProfileValidator

    result = SafeProfileValidator().validate_data(doc, path=label)
    return [f"[{f.rule}] {f.path}: {f.detail}" for f in result.findings]


def _safe_document(data: bytes) -> dict[str, Any] | None:
    """The parsed JSON when ``data`` claims to be a safe profile, else None."""
    if data.lstrip()[:1] != b"{":
        return None
    try:
        doc = json.loads(data)
    except ValueError:
        return None
    if isinstance(doc, dict) and "tables" in doc and "redaction_manifest" in doc:
        return doc
    return None


def _commit(r: Any, a: argparse.Namespace) -> int:
    from shape.registry.local import RawProfileError, is_raw_profile

    meta = _metadata(a)
    if (a.k is not None or a.sensitive) and not a.safe:
        raise ValueError("--k and --sensitive apply to --safe only")
    data = Path(a.artifact).read_bytes()
    raw = is_raw_profile(data)
    if a.safe:
        if not raw:
            raise ValueError(
                f"{a.artifact} is not a raw profile: --safe needs a profile written by "
                "`shape profile` (or exported by `shape profile export`)"
            )
        data = _safe_form(a, a.artifact)
        meta["profile_form"] = "safe"
    elif raw:
        if not a.allow_raw:
            raise RawProfileError(
                f"{a.artifact} is a raw profile: it holds real values from the data (up to 500 "
                "per column), and a registry is made to be shared, backed up or put under git. "
                f"Commit its safe form with `shape registry {a.root} commit {a.name} "
                f"{a.artifact} --safe` (or the output of `shape profile safe`), or pass "
                "--allow-raw to store the real values"
            )
        print(
            "shape: warning: the registry now holds real values from the data (a raw profile); "
            "keep it as private as the data",
            file=sys.stderr,
        )
        meta["profile_form"] = "raw"
    doc = _safe_document(data)
    if doc is not None:
        found = _leaks(doc, a.artifact)
        if found:
            print(
                f"shape: error: not committed: the leak scan found {len(found)} problem(s) in "
                f"{a.artifact}",
                file=sys.stderr,
            )
            for line in found[:5]:
                print(f"  {line}", file=sys.stderr)
            return 1
        meta["profile_form"] = "safe"
    _dump({"content_id": r.commit(a.name, data, meta, allow_raw=a.allow_raw)})
    return 0


def _checkout(r: Any, a: argparse.Namespace) -> int:
    out = a.output or a.legacy_output
    if a.output and a.legacy_output:
        raise ValueError("give the output file once: -o OUT, or as the last argument")
    data = r.checkout(a.name, a.ref)
    if out:
        Path(out).write_bytes(data)
        _dump({"written": out})
    elif _stdout_is_terminal():
        raise ValueError(
            "checkout writes the stored bytes, which may be binary: use -o OUT to write a file "
            "(or redirect the output)"
        )
    else:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    return 0


def _entries(r: Any, name: str) -> list[dict[str, Any]]:
    from shape.registry.local import RegistryError

    entries = r.log(name)
    if not entries:
        known = ", ".join(r.names()) or "none yet"
        raise RegistryError(f"nothing is recorded for {name!r} (names in this registry: {known})")
    return entries  # type: ignore[no-any-return]


def _list(r: Any) -> int:
    rows = []
    for name in r.names():
        entries = r.log(name)
        latest = r.entry(name) if entries else {}
        rows.append(
            {
                "name": name,
                "commits": len(entries),
                "latest": latest.get("content_id"),
                "created": _readable(latest).get("created"),
                "tags": sorted(r.tags(name)),
            }
        )
    _dump(rows)
    return 0


def _changed(a: Any, b: Any, prefix: str = "") -> dict[str, dict[str, Any]]:
    """The paths at which two JSON documents differ (objects are walked, lists compared whole)."""
    if isinstance(a, dict) and isinstance(b, dict):
        out: dict[str, dict[str, Any]] = {}
        for key in sorted(set(a) | set(b)):
            out.update(_changed(a.get(key), b.get(key), f"{prefix}.{key}" if prefix else key))
        return out
    return {} if a == b else {prefix or "<root>": {"from": a, "to": b}}


def _drift(first: bytes, second: bytes) -> dict[str, Any]:
    """``shape diff`` of two raw profile artifacts."""
    import tempfile

    import shape
    from shape.cli.main import _quiet_notices

    # The registry checked the bytes against their content id; the temporary copies' names
    # would only confuse a "not verified" note.
    with tempfile.TemporaryDirectory() as tmp, _quiet_notices():
        paths = []
        for i, blob in enumerate((first, second)):
            path = Path(tmp) / f"{i}.shape"
            path.write_bytes(blob)
            paths.append(path)
        result: dict[str, Any] = shape.diff(shape.load(paths[0]), shape.load(paths[1])).to_dict()
        return result


def _diff(r: Any, a: argparse.Namespace) -> int:
    id1, id2 = r.resolve(a.name, a.ref1), r.resolve(a.name, a.ref2)
    out: dict[str, Any] = {"name": a.name, "from": id1, "to": id2, "same": id1 == id2}
    if id1 != id2:
        first, second = r.checkout(a.name, id1), r.checkout(a.name, id2)
        from shape.registry.local import is_raw_profile

        if is_raw_profile(first) and is_raw_profile(second) and first[:2] == b"PK":
            out["drift"] = _drift(first, second)
        else:
            try:
                docs = [json.loads(x) for x in (first, second)]
            except ValueError:
                docs = []
            if len(docs) == 2 and all(isinstance(d, dict) for d in docs):
                out["changed"] = _changed(docs[0], docs[1])
            else:
                out["changed"] = None  # binary or text: only the content ids can be compared
    else:
        out["changed"] = {}
    _dump(out)
    return 0


def run(a: argparse.Namespace) -> int:
    from shape.registry import LocalRegistry

    r = LocalRegistry(a.root)
    if a.action == "commit":
        return _commit(r, a)
    if a.action == "checkout":
        return _checkout(r, a)
    if a.action == "tag":
        _dump({"content_id": r.tag(a.name, a.tag, a.ref)})
    elif a.action == "promote":
        _dump({"content_id": r.promote(a.name, a.source, a.target)})
    elif a.action == "log":
        _dump([_readable(e) for e in _entries(r, a.name)])
    elif a.action == "list":
        return _list(r)
    elif a.action == "show":
        _dump(_readable(r.entry(a.name, a.ref)))
    elif a.action == "diff":
        return _diff(r, a)
    return 0

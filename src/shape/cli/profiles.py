"""``shape profile export|import|list|validate`` and ``shape profile registry ...`` (P6-10).

Shape reads and writes only its own formats here: a ``.shape`` profile artifact and the
``shape-profile`` JSON that ``export`` writes. The registry keeps ``.shape`` profiles under
``system/table/name`` (:mod:`shape.registry.profiles`); it is not ``shape registry``, the
content-addressed artifact registry.

Nothing heavy loads at import time (T-18); each command imports what it needs when it runs.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from shape import compat

COMMANDS = ("export", "import", "list", "validate", "registry")
EXPORT_FORMAT = "shape-profile"


def routes(argv: Sequence[str]) -> bool:
    """True when ``argv`` (the arguments after ``profile``) is one of these commands. ``validate``
    with ``--safe`` stays the leak scanner of :mod:`shape.privacy.cli`."""
    if not argv or argv[0] not in COMMANDS:
        return False
    return not (argv[0] == "validate" and "--safe" in argv)


def _input_errors() -> tuple[type[BaseException], ...]:
    import zipfile

    from shape.errors import ShapeError

    return (OSError, ValueError, KeyError, ImportError, ShapeError, zipfile.BadZipFile)


_INPUT_ERRORS = _input_errors()


def _root(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--root",
        metavar="DIR",
        help="registry directory (default: $SHAPE_PROFILE_REGISTRY or ~/.shape/profiles)",
    )


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="shape profile",
        epilog="`shape profile SRC -o OUT.shape` profiles data; a file named like one of the "
        "commands above is written ./name.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export", help="write a .shape profile as portable Shape profile JSON")
    ex.add_argument("profile", metavar="PROFILE.shape")
    ex.add_argument("-o", "--output", required=True, metavar="OUT.json")
    im = sub.add_parser("import", help="read Shape profile JSON and write a .shape profile")
    im.add_argument("input", metavar="IN.json")
    im.add_argument("-o", "--output", required=True, metavar="OUT.shape")
    im.add_argument("--name", help="profile name (default: the name in the file)")
    ls = sub.add_parser("list", help="list the .shape profiles in a directory")
    ls.add_argument("directory", nargs="?", default=".", metavar="DIR")
    ls.add_argument("--json", action="store_true", help="print JSON")
    va = sub.add_parser("validate", help="check that a profile is well formed")
    va.add_argument("artifact", metavar="ARTIFACT", help="a .shape profile or exported JSON")
    va.add_argument("--json", action="store_true", help="print JSON")
    va.epilog = "`validate --safe ARTIFACT` is the leak scanner."  # routed elsewhere
    rg = sub.add_parser("registry", help="the named, tagged profile registry")
    rs = rg.add_subparsers(dest="registry_cmd", required=True)
    rl = rs.add_parser("list", help="list registry profiles")
    rl.add_argument("--system")
    rl.add_argument("--table")
    rl.add_argument("--tag", action="append", default=[], help="repeatable; all must match")
    rl.add_argument("--query", help="text in the name, description, system or table")
    rl.add_argument("--json", action="store_true")
    _root(rl)
    sv = rs.add_parser("save", help="store a profile (a .shape file, or data to profile)")
    sv.add_argument("source", metavar="SRC")
    sv.add_argument("--system", required=True)
    sv.add_argument("--name", required=True)
    sv.add_argument("--tags", default="", help="comma-separated")
    sv.add_argument("--description", default="")
    sv.add_argument("--overwrite", action="store_true", help="replace profiles that exist")
    sv.add_argument(
        "--safe",
        action="store_true",
        help="store the share-safe profile JSON (as `shape profile safe`) instead of a .shape "
        "profile",
    )
    sv.add_argument("--k", type=int, metavar="N", help="minimum cohort (default 5)")
    sv.add_argument(
        "--sensitive", action="store_true", help="with --safe: raise the minimum cohort to 11"
    )
    from shape.cli.capture import add_capture_args

    add_capture_args(sv, k=False)
    _root(sv)
    dl = rs.add_parser("delete", help="delete a profile")
    dl.add_argument("identity", metavar="SYSTEM/TABLE/NAME")
    _root(dl)
    tg = rs.add_parser("tag", help="add or remove tags")
    tg.add_argument("identity", metavar="SYSTEM/TABLE/NAME")
    tg.add_argument("tags", nargs="+", metavar="TAG")
    tg.add_argument("--remove", action="store_true")
    _root(tg)
    df = rs.add_parser("diff", help="column-by-column difference of two profiles")
    df.add_argument("a", metavar="SYSTEM/TABLE/NAME")
    df.add_argument("b", metavar="SYSTEM/TABLE/NAME")
    df.add_argument("--json", action="store_true")
    df.add_argument("--fail-on-diff", action="store_true", help="exit 1 when they differ")
    _root(df)
    ri = rs.add_parser("reindex", help="rebuild the index from the files")
    _root(ri)
    rv = rs.add_parser(
        "validate",
        help="check the store (or one profile), and optionally data against a profile",
    )
    rv.add_argument("identity", nargs="?", metavar="SYSTEM/TABLE/NAME")
    rv.add_argument("--data", metavar="SRC", help="also compare this data with the profile")
    rv.add_argument("--tolerance", type=float, default=0.05, help="null-rate drift (default 0.05)")
    rv.add_argument("--json", action="store_true")
    _root(rv)
    from shape.cli import exitcodes, machine

    machine.install(p, ("profile",))
    exitcodes.apply(p, ("profile",))
    return p


def _out(obj: Any) -> None:
    print(json.dumps(obj, indent=2, allow_nan=False))


def _err(msg: str) -> int:
    print(f"shape: error: {msg}", file=sys.stderr)
    return 2


# -- export / import / list / validate -------------------------------------------------------


def _is_shape(path: str) -> bool:
    import zipfile

    return zipfile.is_zipfile(path)


def export_document(prof: Any) -> dict[str, Any]:
    from shape.artifact import codec

    doc: dict[str, Any] = {"name": prof.name, "profile": codec.encode(prof.to_dict())}
    if prof.capture_declared:
        doc["capture"] = prof.capture
        if prof.redaction_manifest:
            doc["redaction_manifest"] = prof.redaction_manifest
    return compat.stamp("profile-export", doc)


def read_export(path: str) -> Any:
    """The ``Profile`` in a ``shape-profile`` JSON file; anything else is a ``ValueError``."""
    from shape.artifact import codec

    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except UnicodeDecodeError as e:
        raise ValueError(f"{path} is not a text file: {e}") from e
    except json.JSONDecodeError as e:
        raise ValueError(f"{path} is not valid JSON: {e}") from e
    if not isinstance(doc, dict) or doc.get("format") != EXPORT_FORMAT:
        raise ValueError(f"{path} is not a Shape profile export (format {EXPORT_FORMAT!r})")
    compat.check_readable("profile-export", doc, path)
    body = codec.decode(doc.get("profile"))
    if not isinstance(body, dict):
        raise ValueError(f"{path}: the profile is not an object")
    problems = check_profile_data(body)
    if problems:
        raise ValueError(f"{path} is not a valid profile: {problems[0]}")
    from shape.profile.reference.profile import check_capture

    capture = check_capture(doc["capture"]) if "capture" in doc else None
    redaction = doc.get("redaction_manifest")
    return _profile_class()(
        body,
        name=str(doc.get("name") or "") or None,
        capture=capture,
        redaction_manifest=redaction if isinstance(redaction, dict) else None,
    )


def _profile_class() -> Any:
    import importlib

    return importlib.import_module("shape.profile.reference.profile").Profile


def check_profile_data(data: Any) -> list[str]:
    """Structural problems of a profile dictionary (empty: well formed)."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["not an object"]
    if "tables" in data:
        tables = data["tables"]
        if not isinstance(tables, dict) or not tables:
            return ["tables: expected a non-empty object"]
        items = list(tables.items())
    elif "columns" in data:
        items = [(str(data.get("name", "")), data)]
    else:
        return ["neither a table profile (columns) nor a dataset profile (tables)"]
    for tname, t in items:
        if not isinstance(t, dict):
            problems.append(f"table {tname!r}: not an object")
            continue
        rows = t.get("row_count")
        if not isinstance(rows, int) or isinstance(rows, bool) or rows < 0:
            problems.append(f"table {tname!r}: row_count must be a non-negative integer")
        cols = t.get("columns")
        if not isinstance(cols, dict):
            problems.append(f"table {tname!r}: columns must be an object")
            continue
        for cname, c in cols.items():
            if not isinstance(c, dict) or not isinstance(c.get("dtype"), str):
                problems.append(f"table {tname!r} column {cname!r}: missing dtype")
                continue
            rate = c.get("null_rate")
            if rate is not None and not (isinstance(rate, (int, float)) and 0 <= rate <= 1):
                problems.append(f"table {tname!r} column {cname!r}: null_rate outside 0..1")
    return problems


def _load_any(path: str) -> Any:
    import shape

    if not Path(path).is_file():
        raise ValueError(f"not a file: {path}")
    return shape.load(path) if _is_shape(path) else read_export(path)


def _export(a: argparse.Namespace) -> int:
    import shape
    from shape.registry.profiles import atomic_write_text

    prof = shape.load(a.profile)
    atomic_write_text(
        Path(a.output), json.dumps(export_document(prof), indent=2, allow_nan=False) + "\n"
    )
    _out({"exported": a.output, "name": prof.name, "tables": list(prof.tables)})
    return 0


def _import(a: argparse.Namespace) -> int:
    import shape

    prof = read_export(a.input)
    if a.name:
        prof = _profile_class()(prof.to_dict(), name=a.name)
    cid = shape.save(prof, a.output)
    _out({"written": a.output, "name": prof.name, "shape_content_id": cid})
    return 0


def _describe(path: Path) -> dict[str, Any]:
    prof = _load_any(str(path))
    return {
        "file": path.name,
        "name": prof.name,
        "tables": {n: t["row_count"] for n, t in prof.tables.items()},
        "columns": sum(len(t["columns"]) for t in prof.tables.values()),
    }


def _list(a: argparse.Namespace) -> int:
    d = Path(a.directory)
    if not d.is_dir():
        return _err(f"not a directory: {d}")
    found: list[dict[str, Any]] = []
    skipped: list[str] = []
    for p in sorted(d.glob("*.shape")):
        try:
            found.append(_describe(p))
        except _INPUT_ERRORS as e:  # a .shape that is not a profile (a model, a bad file)
            skipped.append(f"{p.name}: {e}")
    if a.json:
        _out({"profiles": found, "skipped": skipped})
    else:
        for f in found:
            tables = ", ".join(f"{n} ({r:,} rows)" for n, r in f["tables"].items())
            print(f"{f['file']:<32} {f['name']:<20} {tables}")
        if not found:
            print("No profiles found.")
    for s in skipped:
        print(f"shape: skipped {s}", file=sys.stderr)
    return 0


def _validate(a: argparse.Namespace) -> int:
    path = a.artifact
    problems: list[str] = []
    prof = None
    try:
        prof = _load_any(path)
        problems = check_profile_data(prof.to_dict())
    except _INPUT_ERRORS as e:  # tampering and the wrong kind of artifact are findings
        problems = [str(e)]
    if a.json:
        _out({"artifact": path, "valid": not problems, "problems": problems})
    elif problems:
        print(f"INVALID: {len(problems)} problem(s) in {path}:", file=sys.stderr)
        for pr in problems:
            print(f"  {pr}", file=sys.stderr)
    else:
        print(f"VALID: {path}")
    return 1 if problems else 0


# -- registry --------------------------------------------------------------------------------


def _registry(a: argparse.Namespace) -> Any:
    from shape.registry.profiles import ProfileRegistry

    return ProfileRegistry(a.root)


def _reg_list(a: argparse.Namespace) -> int:
    rows = _registry(a).entries(system=a.system, table=a.table, tags=a.tag, query=a.query)
    if a.json:
        _out(rows)
    elif not rows:
        print("No profiles found.")
    else:
        print(f"{'Identity':<45} {'Tags':<30} {'Rows':>10}  Form")
        print("-" * 93)
        for e in rows:
            ident = f"{e['system']}/{e['table']}/{e['name']}"
            form = e.get("form") or ("safe capture" if e.get("capture") == "safe" else "full")
            print(f"{ident:<45} {', '.join(e['tags']):<30} {e['source_rows']:>10,}  {form}")
    return 0


def _reg_save(a: argparse.Namespace) -> int:
    import shape

    reg = _registry(a)
    if not Path(a.source).exists():
        return _err(f"not found: {a.source}")
    prof = (
        shape.load(a.source)
        if Path(a.source).is_file() and _is_shape(a.source)
        else shape.profile(a.source)
    )
    tags = [t.strip() for t in a.tags.split(",") if t.strip()]
    config = None
    capture = None
    if a.safe:
        if a.capture == "full" or a.column_k or a.classify:
            raise ValueError(
                "--safe stores the share-safe profile JSON: --capture full, --column-k and "
                "--classify apply to the .shape form"
            )
        from shape.privacy.safe_profile import SafeConfig

        config = SafeConfig(k=a.k, sensitive=a.sensitive)
    else:
        if a.sensitive:
            raise ValueError("--sensitive applies to --safe only")
        from shape.cli import capture as capture_args

        capture = capture_args.config_from_args(a)
    saved = reg.save(
        prof,
        system=a.system,
        name=a.name,
        tags=tags,
        description=a.description,
        overwrite=a.overwrite,
        safe=a.safe,
        safe_config=config,
        capture=capture,
    )
    for i in saved:
        print(f"  Saved: {i}")
    form = (
        " (safe profile JSON)."
        if capture is None
        else " (full capture)."
        if capture.mode == "full"
        else "."
    )
    print(f"Saved {len(saved)} profile(s) to the registry{form}")
    if capture is not None and capture.mode == "full":
        capture_args.warn_full(reg.root)
    return 0


def _reg_delete(a: argparse.Namespace) -> int:
    reg = _registry(a)
    if not reg.exists(a.identity):
        print(f"shape: profile {a.identity!r} not found.", file=sys.stderr)
        return 1
    reg.delete(a.identity)
    print(f"Deleted: {a.identity}")
    return 0


def _reg_tag(a: argparse.Namespace) -> int:
    tags = _registry(a).tag(a.identity, list(a.tags), remove=a.remove)
    verb = "Removed" if a.remove else "Added"
    print(f"{verb} tags {list(a.tags)} on {a.identity}; tags now: {tags}")
    return 0


def _reg_diff(a: argparse.Namespace) -> int:
    d = _registry(a).diff(a.a, a.b)
    differs = bool(
        d["added"] or d["removed"] or d["changed"] or d["rows"]["from"] != d["rows"]["to"]
    )
    if a.json:
        _out(d)
    else:
        if d["added"]:
            print(f"+ Added columns:   {d['added']}")
        if d["removed"]:
            print(f"- Removed columns: {d['removed']}")
        if d["rows"]["from"] != d["rows"]["to"]:
            print(f"~ Rows: {d['rows']['from']:,} -> {d['rows']['to']:,}")
        if d["changed"]:
            print("~ Changed columns:")
            for col, fields in d["changed"].items():
                print(f"    {col}: {', '.join(sorted(fields))}")
        if not differs:
            print("Profiles are identical.")
    return 1 if (a.fail_on_diff and differs) else 0


def _reg_reindex(a: argparse.Namespace) -> int:
    count, skipped = _registry(a).reindex()
    print(f"Reindexed {count} profile(s).")
    for s in skipped:
        print(f"shape: skipped {s}", file=sys.stderr)
    return 0


def compare_data(stored: dict[str, Any], observed: dict[str, Any], tolerance: float) -> list[str]:
    """Differences between a stored table profile and the profile of new data."""
    sc, oc = stored["columns"], observed["columns"]
    out = [f"column {c!r} is in the profile but not in the data" for c in sorted(set(sc) - set(oc))]
    out += [
        f"column {c!r} is in the data but not in the profile" for c in sorted(set(oc) - set(sc))
    ]
    for c in sorted(set(sc) & set(oc)):
        if sc[c]["dtype"] != oc[c]["dtype"]:
            out.append(f"column {c!r}: type {sc[c]['dtype']} -> {oc[c]['dtype']}")
        a, b = sc[c].get("null_rate"), oc[c].get("null_rate")
        if a is not None and b is not None and abs(a - b) > tolerance:
            out.append(f"column {c!r}: null rate {a:.3f} -> {b:.3f}")
    return out


def _reg_validate(a: argparse.Namespace) -> int:
    import shape

    reg = _registry(a)
    problems = reg.validate(a.identity)
    if a.data:
        if not a.identity:
            return _err("--data needs a profile identity")
        table = a.identity.split("/")[1]
        stored = reg.table(a.identity)
        observed = shape.profile(a.data)
        tables = observed.tables
        if table in tables:
            obs = tables[table]
        elif len(tables) == 1:
            obs = next(iter(tables.values()))
        else:
            return _err(f"{a.data} has no table {table!r}")
        problems += compare_data(stored, obs, a.tolerance)
    if a.json:
        _out({"valid": not problems, "problems": problems})
    elif problems:
        print(f"INVALID: {len(problems)} problem(s):", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
    else:
        print("VALID")
    return 1 if problems else 0


_HANDLERS = {
    "export": _export,
    "import": _import,
    "list": _list,
    "validate": _validate,
}
_REGISTRY = {
    "list": _reg_list,
    "save": _reg_save,
    "delete": _reg_delete,
    "tag": _reg_tag,
    "diff": _reg_diff,
    "reindex": _reg_reindex,
    "validate": _reg_validate,
}


def main(argv: Sequence[str]) -> int:
    """Run one of these commands; ``argv`` starts after ``profile``. 0 ok, 1 a check failed,
    2 bad input."""
    from shape.cli import machine

    parser = _parser()
    a = parser.parse_args(list(argv))
    handler = _REGISTRY[a.registry_cmd] if a.cmd == "registry" else _HANDLERS[a.cmd]

    def run() -> int:
        try:
            return handler(a)
        except _INPUT_ERRORS as e:
            from shape.cli import errors

            if errors.debug_enabled():
                raise
            return errors.fail(e)

    return machine.run(f"profile {machine.command_path(parser, a)}", a, run)

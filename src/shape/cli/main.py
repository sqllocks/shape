"""Shape command-line entry point.

Every command imports what it needs when it runs, so ``shape --version`` and argument errors
do not pay for numpy, pyarrow or the profiler (P1-11: start-up under 300 ms).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict


def _rows(path):
    """Typed rows of a CSV/Parquet/JSONL file (ints stay ints, not strings)."""
    from shape.io import iter_rows

    return iter_rows(path)


def _dump(x):
    print(json.dumps(x, sort_keys=True, default=str))


def _artifact_kind(path):
    """The ``kind`` in a ``.shape`` manifest, or None when ``path`` is not a Shape artifact."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as z:
            return json.loads(z.read("manifest.json")).get("kind")
    except (OSError, ValueError, KeyError, zipfile.BadZipFile):
        return None


def _is_profile_or_missing(path):
    """Route to the section 12.2 check/diff (exit 2 on input errors) for profile artifacts,
    and for a path that does not exist, which no legacy command can read either."""
    return _artifact_kind(path) == "profile" or not os.path.exists(path)


def _write_json(path, obj):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2, allow_nan=False)
        fh.write("\n")


def _run(fn, a):
    """Run a profile/check/diff command: 0 ok, 1 failed check or drift, 2 input error."""
    import zipfile

    from shape.errors import ShapeError

    try:
        return fn(a)
    except (
        OSError,
        ValueError,
        ImportError,
        NotImplementedError,
        KeyError,
        ShapeError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"shape: error: {exc}", file=sys.stderr)
        return 2


def _cmd_profile(a):
    import shape

    bare = a.spindle_compat is True  # --spindle-compat with no path: -o is the Spindle JSON
    if bare and not a.output:
        raise ValueError("--spindle-compat without a path needs -o OUT.json")
    if not bare and not a.output:
        raise ValueError("profile needs -o OUT.shape")
    prof = shape.profile(a.src)
    if bare:
        _write_json(a.output, prof.to_dict())
        if a.html:
            with open(a.html, "w", encoding="utf-8") as fh:
                fh.write(prof.to_html())
        if a.json:
            _write_json(a.json, prof.summary())
        _dump({"written": a.output, "format": "spindle-compat"})
        return 0
    content_id = shape.save(prof, a.output)
    if a.html:
        with open(a.html, "w", encoding="utf-8") as fh:
            fh.write(prof.to_html())
    if a.json:
        _write_json(a.json, prof.summary())
    if a.spindle_compat:
        _write_json(a.spindle_compat, prof.to_dict())
    _dump({"written": a.output, "shape_content_id": content_id})
    return 0


def _cmd_inspect(a):
    """Print what a .shape artifact holds: a profile (Spindle-shaped) or a Shape model."""
    if _artifact_kind(a.shape) == "profile":
        import shape

        prof = shape.load(a.shape)
        _dump({"kind": "profile", "name": prof.name, "profile": prof.to_dict()})
    elif str(a.shape).endswith(".shape"):
        from shape.artifact import read_model

        manifest, model = read_model(a.shape)
        _dump({"kind": "model", "manifest": manifest, "shape": model})
    else:
        with open(a.shape, encoding="utf-8") as fh:
            _dump(json.load(fh))
    return 0


def _cmd_check(a):
    import shape

    result = shape.check(shape.load(a.shape), a.contract)
    out = result.to_dict()
    if a.json:
        _write_json(a.json, out)
    _dump(out)
    return 0 if result.passed else 1


def _cmd_diff(a):
    import shape

    result = shape.diff(shape.load(a.before), shape.load(a.after))
    out = result.to_dict()
    if a.json:
        _write_json(a.json, out)
    _dump(out)
    return 1 if (a.fail_on_drift and result.drifted) else 0


def _build_parser():
    p = argparse.ArgumentParser(prog="shape", description="Shape as Code")
    p.add_argument("--version", "-V", action="store_true", help="print the version and exit")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    sub.add_parser("conformance")
    sub.add_parser("version")
    c = sub.add_parser("capture")
    c.add_argument("csv")
    c.add_argument("-o", "--output")
    pr = sub.add_parser(
        "profile",
        help="profile a file, glob, directory or Delta table "
        "(also: profile capture DATA -o OUT.json, profile diff A.json B.json)",
    )
    pr.add_argument("src", metavar="SRC")
    pr.add_argument("-o", "--output", metavar="OUT")
    pr.add_argument("--html", metavar="REPORT.html")
    pr.add_argument("--json", metavar="SUMMARY.json")
    pr.add_argument(
        "--spindle-compat",
        metavar="FULL.json",
        nargs="?",
        const=True,
        default=None,
        help="write Spindle's TableProfile JSON: to FULL.json alongside the .shape file, or, "
        "given bare, to -o instead of a .shape file",
    )
    d = sub.add_parser("diff", help="compare two profiles")
    d.add_argument("before", metavar="BASE.shape")
    d.add_argument("after", metavar="CURRENT.shape")
    d.add_argument("--json", metavar="RESULT.json")
    d.add_argument("--fail-on-drift", action="store_true")
    for name in ("show", "inspect"):
        sh = sub.add_parser(name, help="print a .shape artifact's manifest and contents")
        sh.add_argument("shape")
    va = sub.add_parser("validate", help="validate a Shape-as-Code contract document")
    va.add_argument("contract")
    qu = sub.add_parser("quality")
    qu.add_argument("csv")
    qu.add_argument("--reference")
    ge = sub.add_parser("generate")
    ge.add_argument("--rows", type=int, default=10)
    ge.add_argument("--seed", type=int, default=0)
    fi = sub.add_parser("fidelity")
    fi.add_argument("reference")
    fi.add_argument("csv")
    fi.add_argument("--tolerance", type=float, default=0.1)
    k = sub.add_parser("key")
    k.add_argument("csv")
    k.add_argument("fields", nargs="+")
    f = sub.add_parser("fd")
    f.add_argument("csv")
    f.add_argument("--determinant", nargs="+", required=True)
    f.add_argument("--dependent", required=True)
    q = sub.add_parser("privacy-k")
    q.add_argument("csv")
    q.add_argument("fields", nargs="+")
    cq = sub.add_parser("query")
    cq.add_argument("shape")
    cq.add_argument("expression")
    ck = sub.add_parser("check", help="check a profile against a contract")
    ck.add_argument("shape", metavar="PROFILE.shape")
    ck.add_argument("contract", metavar="CONTRACT.json")
    ck.add_argument("--json", metavar="RESULT.json")
    co = sub.add_parser("compatibility")
    co.add_argument("before")
    co.add_argument("after")
    co.add_argument("--mode", choices=("backward", "forward", "full"), default="backward")
    gp = sub.add_parser("plan")
    gp.add_argument("shape")
    fc = sub.add_parser("certify-shapes")
    fc.add_argument("target")
    fc.add_argument("observed")
    rg = sub.add_parser("registry")
    rg.add_argument("root")
    rg.add_argument("action", choices=("commit", "checkout", "tag", "promote", "log"))
    rg.add_argument("name")
    rg.add_argument("arg1", nargs="?")
    rg.add_argument("arg2", nargs="?")
    return p


def _profile_sub_parser():
    """Parsers of ``shape profile capture`` and ``shape profile diff``, which share the word
    ``profile`` with ``shape profile SRC``."""
    p = argparse.ArgumentParser(prog="shape profile")
    sub = p.add_subparsers(dest="sub", required=True)
    cap = sub.add_parser("capture", help="capture the categorical shape of data, without rows")
    cap.add_argument("data", metavar="DATA")
    cap.add_argument("-o", "--output", required=True, metavar="OUT.json")
    cap.add_argument("--format", dest="fmt", choices=("csv", "parquet", "jsonl"), default="csv")
    cap.add_argument("--name", default="captured")
    dif = sub.add_parser("diff", help="drift between two captured profiles")
    dif.add_argument("a", metavar="A.json")
    dif.add_argument("b", metavar="B.json")
    dif.add_argument("--threshold", type=float, default=None)
    dif.add_argument("--json", dest="as_json", action="store_true")
    dif.add_argument("--min-drift", type=float, default=0.01)
    return p


def _version():
    from shape import __version__

    return __version__


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] in (["--version"], ["-V"]):
        print(f"shape {_version()}")
        return 0
    if argv[:1] == ["profile"] and argv[1:2] in (["capture"], ["diff"]):
        from shape.cli import profile_cmds

        return _run(profile_cmds.run, _profile_sub_parser().parse_args(argv[1:]))
    a = _build_parser().parse_args(argv)
    if a.version:
        print(f"shape {_version()}")
        return 0
    if a.cmd == "profile":
        return _run(_cmd_profile, a)
    if a.cmd == "check" and _is_profile_or_missing(a.shape):
        return _run(_cmd_check, a)
    if a.cmd == "diff" and _is_profile_or_missing(a.before):
        return _run(_cmd_diff, a)
    if a.cmd == "version":
        _dump({"shape": _version(), "specification": "2", "artifact_format": 2})
        return 0
    if a.cmd == "doctor":
        checks = {"python": sys.version.split()[0]}
        for name in ("pyarrow", "cryptography", "yaml"):
            try:
                checks[name] = __import__(name).__version__
            except Exception:
                checks[name] = None
        _dump(checks)
        return 0
    if a.cmd == "conformance":
        from shape.validation.suite import conformance

        r = conformance()
        _dump([asdict(x) for x in r])
        return 0 if all(x.passed for x in r) else 1
    if a.cmd == "capture":
        from shape.artifact import write_shape
        from shape.capture import capture_rows

        obj = capture_rows(_rows(a.csv)).to_dict()
        if a.output and str(a.output).endswith(".shape"):
            cid = write_shape(a.output, obj, name=__import__("pathlib").Path(a.csv).stem)
            _dump({"written": a.output, "shape_content_id": cid})
            return 0
        raw = json.dumps(obj, sort_keys=True, indent=2, default=str)
        if a.output:
            open(a.output, "w", encoding="utf-8").write(raw + "\n")
        else:
            print(raw)
        return 0
    if a.cmd == "diff":
        from shape.drift import compare

        _dump([asdict(v) for v in compare(json.load(open(a.before)), json.load(open(a.after)))])
        return 0
    if a.cmd in ("show", "inspect"):
        return _run(_cmd_inspect, a)
    if a.cmd == "validate":
        from shape.spec import load_contract

        c = load_contract(a.contract)
        _dump({"valid": True, "name": c.name, "version": c.version, "fidelity": c.fidelity})
        return 0
    if a.cmd == "quality":
        from shape.capture import capture_rows
        from shape.quality import infer_rules, validate_rows

        rows = list(_rows(a.csv))
        ref = json.load(open(a.reference)) if a.reference else capture_rows(rows).to_dict()
        result = validate_rows(rows, infer_rules(ref))
        _dump({"passed": result.passed, "violations": [asdict(v) for v in result.violations]})
        return 0 if result.passed else 2
    if a.cmd == "generate":
        from shape.generation import Choice, GenerationPlan, SequenceStrategy

        plan = GenerationPlan(
            (("id", SequenceStrategy()), ("segment", Choice(("A", "B", "C"), (0.7, 0.2, 0.1)))),
            a.seed,
        )
        for row in plan.rows(a.rows):
            print(json.dumps(row, sort_keys=True))
        return 0
    if a.cmd == "fidelity":
        from shape.generation import certify

        ref = json.load(open(a.reference))
        cert = certify(ref, list(_rows(a.csv)), tolerance=a.tolerance)
        _dump(cert.to_dict())
        return 0 if cert.passed else 3
    if a.cmd in ("query", "check", "plan"):
        from shape.artifact import read_shape

        _, s = (
            read_shape(a.shape)
            if str(a.shape).endswith(".shape")
            else ({}, json.load(open(a.shape)))
        )
        if a.cmd == "query":
            from shape.query import query as shape_query

            _dump({"result": shape_query(s, a.expression)})
            return 0
        if a.cmd == "plan":
            from shape.generation.fidelity import plan_reconstruction

            _dump(plan_reconstruction(s).to_dict())
            return 0
        from shape.contracts import evaluate_contract

        contract = json.load(open(a.contract))
        r = evaluate_contract(s, contract)
        _dump(r.to_dict())
        return 0 if r.passed else 4
    if a.cmd in ("compatibility", "certify-shapes"):
        from shape.artifact import read_shape

        def LS(p):
            return read_shape(p)[1] if str(p).endswith(".shape") else json.load(open(p))

        if a.cmd == "compatibility":
            from shape.contracts import compatibility

            r = compatibility(LS(a.before), LS(a.after), a.mode)
            _dump(r.to_dict())
            return 0 if r.compatible else 5
        from shape.generation.fidelity import certify_shapes

        _dump(certify_shapes(LS(a.target), LS(a.observed)).to_dict())
        return 0
    if a.cmd == "registry":
        from shape.registry import LocalRegistry

        r = LocalRegistry(a.root)
        if a.action == "commit":
            if not a.arg1:
                raise SystemExit("registry commit requires artifact path")
            _dump({"content_id": r.commit(a.name, open(a.arg1, "rb").read())})
        elif a.action == "checkout":
            data = r.checkout(a.name, a.arg1 or "latest")
            if a.arg2:
                open(a.arg2, "wb").write(data)
                _dump({"written": a.arg2})
            else:
                sys.stdout.buffer.write(data)
        elif a.action == "tag":
            _dump({"content_id": r.tag(a.name, a.arg1, a.arg2 or "latest")})
        elif a.action == "promote":
            _dump({"content_id": r.promote(a.name, a.arg1, a.arg2)})
        else:
            _dump(r.log(a.name))
        return 0
    from shape.privacy.measure import k_anonymity
    from shape.profile.dependencies import candidate_key, functional_dependency

    rows = list(_rows(a.csv))
    if a.cmd == "key":
        result = candidate_key(rows, tuple(a.fields))
    elif a.cmd == "fd":
        result = functional_dependency(rows, tuple(a.determinant), a.dependent)
    else:
        result = k_anonymity(rows, tuple(a.fields))
    _dump(asdict(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

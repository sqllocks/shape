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

    from shape.artifact.io import ArtifactSignatureError
    from shape.errors import ShapeError

    try:
        return fn(a)
    except ArtifactSignatureError as exc:
        print(f"shape: signature check failed: {exc}", file=sys.stderr)
        return 1
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


def _sign_output(a, path):
    """Sign the artifact just written when ``--sign KEY`` was given."""
    if getattr(a, "sign", None):
        from shape.artifact.signing import load_private_key, sign_artifact

        return sign_artifact(path, load_private_key(a.sign))
    return None


def _verify_inputs(a):
    """``--verify PUBKEY``: every .shape input must carry a valid signature by that key."""
    from shape.artifact.signing import load_public_key, verify_artifact

    key = load_public_key(a.verify)
    for name in ("shape", "before", "after", "target", "observed"):
        path = getattr(a, name, None)
        if isinstance(path, str) and path.endswith(".shape"):
            verify_artifact(path, key)


def _cmd_keygen(a):
    from shape.artifact.signing import key_id, load_public_key, write_keypair

    priv, pub = write_keypair(a.prefix)
    _dump(
        {"private_key": str(priv), "public_key": str(pub), "key_id": key_id(load_public_key(pub))}
    )
    return 0


def _cmd_sign(a):
    from shape.artifact.signing import load_private_key, sign_artifact

    kid = sign_artifact(a.shape, load_private_key(a.key), a.output)
    _dump({"signed": a.output or a.shape, "key_id": kid})
    return 0


def _cmd_verify_signature(a):
    from shape.artifact.signing import load_public_key, verify_artifact

    if not a.key:
        raise ValueError("verifying a .shape artifact needs --key PUBLIC.pub")
    _dump({"artifact": a.shape, **verify_artifact(a.shape, load_public_key(a.key))})
    return 0


def _cmd_profile(a):
    import shape

    if not a.output:
        raise ValueError("profile needs -o OUT.shape")
    prof = shape.profile(a.src)
    content_id = shape.save(prof, a.output)
    key_id = _sign_output(a, a.output)
    if a.html:
        with open(a.html, "w", encoding="utf-8") as fh:
            fh.write(prof.to_html())
    if a.json:
        _write_json(a.json, prof.summary())
    out = {"written": a.output, "shape_content_id": content_id}
    if key_id:
        out["signed_by"] = key_id
    _dump(out)
    return 0


def _cmd_inspect(a):
    """Print what a .shape artifact holds: a profile or a Shape model."""
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


def _cmd_verify(a):
    """``shape verify``: a ``.shape`` artifact is checked for its signature, anything else is
    data for the validation gates."""
    if str(a.shape).endswith(".shape"):
        return _cmd_verify_signature(a)
    return _cmd_verify_gates(a)


def _cmd_verify_gates(a):
    """Load tables, run the gates, print the gate table; 0 pass, 1 a gate failed (or a warning
    under --strict), 2 input error."""
    from shape.quality import VerifyReport, VerifyRunner, load_gate_schema, load_tables

    tables = load_tables(a.shape, a.format)
    if not tables:
        raise ValueError(f"no {a.format} data files found in {a.shape}")
    schema = load_gate_schema(a.schema) if a.schema else None
    result = VerifyRunner(schema, a.statistical, a.shape, a.schema).run(tables)
    print(f"Shape {_version()} - Verify\n")
    print(f"Data path:   {a.shape}")
    if a.schema:
        print(f"Schema:      {a.schema}")
    print(f"Statistical: {'yes' if a.statistical else 'no'}\n")
    if result.gate_results:
        print(f"{'Gate':<28} {'Status':<8} {'Errors':>6} {'Warnings':>8}")
        print("-" * 55)
        for g in result.gate_results:
            status = "PASS" if g.passed else "FAIL"
            print(f"{g.gate_name:<28} {status:<8} {len(g.errors):>6} {len(g.warnings):>8}")
        print()
    print("Row counts:")
    for name, n in sorted(result.row_counts.items()):
        print(f"  {name}: {n:,}")
    print()
    for g in result.gate_results:
        for e in g.errors:
            print(f"  ERROR [{g.gate_name}]: {e}", file=sys.stderr)
        for w in g.warnings:
            print(f"  WARN  [{g.gate_name}]: {w}")
    print(f"\nResult: {'PASS' if result.passed else 'FAIL'}")
    if a.output:
        report = VerifyReport(result)
        text = report.to_json() if str(a.output).endswith(".json") else report.to_markdown()
        with open(a.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"Report written to {a.output}")
    has_warnings = any(g.warnings for g in result.gate_results)
    return 1 if (not result.passed or (a.strict and has_warnings)) else 0


_VERIFY_HELP = "require every .shape input to be signed by this public key (exit 1 if not)"


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
    c.add_argument("--sign", metavar="KEY", help="sign the written .shape with this private key")
    pr = sub.add_parser("profile", help="profile a file, glob, directory or Delta table")
    pr.add_argument("src", metavar="SRC")
    pr.add_argument("-o", "--output", metavar="OUT")
    pr.add_argument("--sign", metavar="KEY", help="sign the written .shape with this private key")
    pr.add_argument("--html", metavar="REPORT.html")
    pr.add_argument("--json", metavar="SUMMARY.json")
    d = sub.add_parser("diff", help="compare two profiles")
    d.add_argument("before", metavar="BASE.shape")
    d.add_argument("after", metavar="CURRENT.shape")
    d.add_argument("--json", metavar="RESULT.json")
    d.add_argument("--fail-on-drift", action="store_true")
    d.add_argument("--verify", metavar="PUBKEY", help=_VERIFY_HELP)
    for name in ("show", "inspect"):
        sh = sub.add_parser(name, help="print a .shape artifact's manifest and contents")
        sh.add_argument("shape")
        sh.add_argument("--verify", metavar="PUBKEY", help=_VERIFY_HELP)
    kg = sub.add_parser("keygen", help="generate an Ed25519 signing key pair")
    kg.add_argument("prefix", help="writes PREFIX.key (private, mode 0600) and PREFIX.pub")
    sg = sub.add_parser("sign", help="sign a .shape artifact")
    sg.add_argument("shape", metavar="ARTIFACT.shape")
    sg.add_argument("--key", required=True, metavar="PRIVATE.key")
    sg.add_argument("-o", "--output", metavar="OUT.shape", help="default: sign in place")
    va = sub.add_parser("validate", help="validate a Shape-as-Code contract document")
    va.add_argument("contract")
    vf = sub.add_parser(
        "verify",
        help="run the validation gates over data, or check a .shape artifact's signature",
    )
    vf.add_argument(
        "shape",
        metavar="DATA|ARTIFACT.shape",
        help="a data file or a directory of data files; a .shape file is checked for its "
        "signature instead (exit 1 if invalid)",
    )
    vf.add_argument("--key", metavar="PUBLIC.pub", help="public key for a .shape artifact")
    vf.add_argument("--format", choices=("auto", "csv", "parquet", "jsonl"), default="auto")
    vf.add_argument("--schema", metavar="GATES.json", help="gate schema (or Shape model v2)")
    vf.add_argument("--statistical", action="store_true", help="add KS and chi-squared tests")
    vf.add_argument("-o", "--output", metavar="REPORT", help="write a .json or .md report")
    vf.add_argument("--strict", action="store_true", help="exit 1 on warnings too")
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
    cq.add_argument("--verify", metavar="PUBKEY", help=_VERIFY_HELP)
    ck = sub.add_parser("check", help="check a profile against a contract")
    ck.add_argument("shape", metavar="PROFILE.shape")
    ck.add_argument("contract", metavar="CONTRACT.json")
    ck.add_argument("--json", metavar="RESULT.json")
    ck.add_argument("--verify", metavar="PUBKEY", help=_VERIFY_HELP)
    co = sub.add_parser("compatibility")
    co.add_argument("before")
    co.add_argument("after")
    co.add_argument("--mode", choices=("backward", "forward", "full"), default="backward")
    gp = sub.add_parser("plan")
    gp.add_argument("shape")
    gp.add_argument("--verify", metavar="PUBKEY", help=_VERIFY_HELP)
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


def _version():
    from shape import __version__

    return __version__


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] in (["--version"], ["-V"]):
        print(f"shape {_version()}")
        return 0
    a = _build_parser().parse_args(argv)
    if a.version:
        print(f"shape {_version()}")
        return 0
    if a.cmd in ("keygen", "sign", "verify"):
        return _run({"keygen": _cmd_keygen, "sign": _cmd_sign, "verify": _cmd_verify}[a.cmd], a)
    if getattr(a, "verify", None):
        rc = _run(_verify_inputs, a)
        if rc:
            return rc
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
            out = {"written": a.output, "shape_content_id": cid}
            kid = _sign_output(a, a.output)
            if kid:
                out["signed_by"] = kid
            _dump(out)
            return 0
        if getattr(a, "sign", None):
            raise SystemExit("capture --sign needs -o OUT.shape")
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
    if a.cmd in ("query", "plan") and _artifact_kind(a.shape) == "profile":
        print(
            f"shape: error: `shape {a.cmd}` does not read profiles made by `shape profile` yet "
            "(planned). Use `shape check` or `shape diff`.",
            file=sys.stderr,
        )
        return 2
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

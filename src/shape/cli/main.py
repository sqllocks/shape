"""Shape command-line entry point."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict

from shape.artifact import read_shape, write_shape
from shape.capture import capture_rows
from shape.connectors import CSVSource
from shape.contracts import compatibility, evaluate_contract
from shape.drift import compare
from shape.generation import Choice, GenerationPlan, SequenceStrategy, certify
from shape.generation.fidelity import certify_shapes, plan_reconstruction
from shape.privacy.measure import k_anonymity
from shape.profile.dependencies import candidate_key, functional_dependency
from shape.quality import infer_rules, validate_rows
from shape.query import query as shape_query
from shape.registry import LocalRegistry
from shape.spec import load_contract
from shape.validation.suite import conformance


def _rows(path):
    for b in CSVSource(path).rows():
        yield from b


def _dump(x):
    print(json.dumps(x, sort_keys=True, default=str))


def main(argv=None):
    p = argparse.ArgumentParser(prog="shape", description="Shape as Code")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor")
    sub.add_parser("conformance")
    sub.add_parser("version")
    c = sub.add_parser("capture")
    c.add_argument("csv")
    c.add_argument("-o", "--output")
    pr = sub.add_parser("profile")
    pr.add_argument("csv")
    pr.add_argument("-o", "--output", required=True)
    d = sub.add_parser("diff")
    d.add_argument("before")
    d.add_argument("after")
    sh = sub.add_parser("show")
    sh.add_argument("shape")
    ins = sub.add_parser("inspect")
    ins.add_argument("shape")
    va = sub.add_parser("validate")
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
    ck = sub.add_parser("check")
    ck.add_argument("shape")
    ck.add_argument("contract")
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
    a = p.parse_args(argv)
    if a.cmd == "version":
        from shape import __version__

        _dump({"shape": __version__, "specification": "1.0", "artifact_format": 1})
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
        r = conformance()
        _dump([asdict(x) for x in r])
        return 0 if all(x.passed for x in r) else 1
    if a.cmd in ("capture", "profile"):
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
        _dump([asdict(v) for v in compare(json.load(open(a.before)), json.load(open(a.after)))])
        return 0
    if a.cmd in ("show", "inspect"):
        if str(a.shape).endswith(".shape"):
            m, obj = read_shape(a.shape)
            _dump({"manifest": m, "shape": obj})
        else:
            _dump(json.load(open(a.shape)))
        return 0
    if a.cmd == "validate":
        c = load_contract(a.contract)
        _dump({"valid": True, "name": c.name, "version": c.version, "fidelity": c.fidelity})
        return 0
    if a.cmd == "quality":
        rows = list(_rows(a.csv))
        ref = json.load(open(a.reference)) if a.reference else capture_rows(rows).to_dict()
        result = validate_rows(rows, infer_rules(ref))
        _dump({"passed": result.passed, "violations": [asdict(v) for v in result.violations]})
        return 0 if result.passed else 2
    if a.cmd == "generate":
        plan = GenerationPlan(
            (("id", SequenceStrategy()), ("segment", Choice(("A", "B", "C"), (0.7, 0.2, 0.1)))),
            a.seed,
        )
        for row in plan.rows(a.rows):
            print(json.dumps(row, sort_keys=True))
        return 0
    if a.cmd == "fidelity":
        ref = json.load(open(a.reference))
        cert = certify(ref, list(_rows(a.csv)), tolerance=a.tolerance)
        _dump(cert.to_dict())
        return 0 if cert.passed else 3
    if a.cmd in ("query", "check", "plan"):
        _, s = (
            read_shape(a.shape)
            if str(a.shape).endswith(".shape")
            else ({}, json.load(open(a.shape)))
        )
        if a.cmd == "query":
            _dump({"result": shape_query(s, a.expression)})
            return 0
        if a.cmd == "plan":
            _dump(plan_reconstruction(s).to_dict())
            return 0
        contract = json.load(open(a.contract))
        r = evaluate_contract(s, contract)
        _dump(r.to_dict())
        return 0 if r.passed else 4
    if a.cmd == "compatibility":

        def LS(p):
            return read_shape(p)[1] if str(p).endswith(".shape") else json.load(open(p))

        r = compatibility(LS(a.before), LS(a.after), a.mode)
        _dump(r.to_dict())
        return 0 if r.compatible else 5
    if a.cmd == "certify-shapes":

        def LS(p):
            return read_shape(p)[1] if str(p).endswith(".shape") else json.load(open(p))

        r = certify_shapes(LS(a.target), LS(a.observed))
        _dump(r.to_dict())
        return 0
    if a.cmd == "registry":
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

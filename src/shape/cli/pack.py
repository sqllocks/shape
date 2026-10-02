"""``shape pack run|validate|list``: scenario packs and generation specs (P6-14).

A *pack* is a YAML file that bundles a domain, a simulation kind, chaos, validation gates and the
landing paths of a run; a *spec* (GSL, ``*.gsl.yaml``) points at a pack and sets the schema, scale,
seed, chaos and gates around it. ``run`` and ``validate`` take either. Shape ships no packs of its
own: ``list`` looks in the directories you give it (default: ``./packs``).

Nothing heavy loads at import time (T-18).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

DEFAULT_ROOT = "packs"


def add_arguments(sub: Any) -> None:
    """Register ``pack`` on the subparsers."""
    pk = sub.add_parser(
        "pack",
        help="run, validate and list scenario packs and generation specs",
        description="Scenario packs bundle a domain, a simulation kind (file_drop, stream or "
        "hybrid), chaos and validation gates. A generation spec (*.gsl.yaml) points at a pack and "
        "adds schema, scale, seed, chaos, outputs and gates.",
    )
    actions = pk.add_subparsers(dest="pack_cmd", required=True, metavar="{run,validate,list}")

    def target(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "target",
            metavar="PACK.yaml|SPEC.gsl.yaml",
            help="a pack file or a generation spec; DOMAIN/PACK_ID with --root",
        )
        p.add_argument("--root", metavar="DIR", help="a pack directory: <DIR>/<domain>/<id>.yaml")
        p.add_argument(
            "--domain", help="the domain to use (default: the pack's domain, or the spec's schema)"
        )
        p.add_argument("--json", action="store_true", help="print the result as JSON")

    ru = actions.add_parser(
        "run",
        help="run a pack or a spec and write its output and run manifest",
        description="Generate the domain, apply chaos, write the files or events the pack asks "
        "for, check its validation gates and write <run_id>_manifest.json. Exit 0 on success, 1 "
        "when the pack is invalid, a gate fails (without chaos) or generation fails, 2 for bad "
        "input.",
    )
    target(ru)
    ru.add_argument(
        "--scale", metavar="PRESET", help="scale preset (default: the spec's, or small)"
    )
    ru.add_argument("--seed", type=int, help="seed (default: the spec's, or 42)")
    ru.add_argument(
        "-o",
        "--output",
        metavar="DIR",
        default="pack_output",
        help="output directory (default: pack_output)",
    )
    va = actions.add_parser(
        "validate",
        help="check a pack or a spec against its domain without running it",
        description="Check a pack (or a spec, its pack and its schema) against the domain: "
        "entities, kind, sections, gates, chaos. Exit 0 when valid (warnings allowed), 1 when "
        "there are errors, 2 for bad input.",
    )
    target(va)
    li = actions.add_parser(
        "list",
        help="list the packs and specs in a directory",
        description="List the pack and spec files under each directory (searched recursively). "
        "Shape ships none of its own.",
    )
    li.add_argument(
        "paths", nargs="*", metavar="DIR", help=f"directories to search (default: ./{DEFAULT_ROOT})"
    )
    li.add_argument("--json", action="store_true", help="print the list as JSON")


def run(a: argparse.Namespace) -> int:
    if a.pack_cmd == "list":
        return _list(a)
    if a.pack_cmd == "validate":
        return _validate(a)
    return _run(a)


def _emit(obj: Any) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True, default=str))


def _resolve(a: argparse.Namespace) -> Path:
    """The file named by TARGET: a path, or DOMAIN/PACK_ID under --root."""
    path = Path(a.target)
    if path.is_file():
        return path
    if a.root:
        domain, _, pack_id = a.target.partition("/")
        if pack_id and Path(a.root).is_dir():
            from shape.scenario.loader import PackLoader

            PackLoader(a.root).load_from_root(domain, pack_id)  # raises with the choices
            return Path(str(a.root)) / domain / f"{pack_id}.yaml"
    raise FileNotFoundError(f"no pack or spec at {a.target}")


def _pack_domain(a: argparse.Namespace, declared: str) -> Any:
    from shape.scenario.resolve import load_pack_domain

    name = a.domain or declared
    if not name:
        raise ValueError("the pack names no domain: give --domain (see `shape list`)")
    return load_pack_domain(name)


def _validate(a: argparse.Namespace) -> int:
    from shape.scenario.gsl import GSLParser, is_spec_document
    from shape.scenario.loader import PackLoader
    from shape.scenario.resolve import validate_spec
    from shape.scenario.validator import PackValidator

    path = _resolve(a)
    if is_spec_document(path):
        spec = GSLParser().parse(path)
        result = validate_spec(spec)
        kind, ident = "spec", spec.name or path.name
    else:
        pack = PackLoader().load(path)
        result = PackValidator().validate(pack, _pack_domain(a, pack.domain))
        kind, ident = "pack", pack.id
    if a.json:
        _emit(
            {
                "kind": kind,
                "id": ident,
                "valid": result.is_valid,
                "errors": result.errors,
                "warnings": result.warnings,
            }
        )
    else:
        print(f"{kind} {ident}")
        print(result.summary())
    return 0 if result.is_valid else 1


def _run(a: argparse.Namespace) -> int:
    from shape.scenario.gsl import GSLParser, is_spec_document
    from shape.scenario.loader import PackLoader
    from shape.scenario.resolve import spec_domain, spec_pack, validate_spec
    from shape.scenario.runner import PackRunner

    path = _resolve(a)
    if is_spec_document(path):
        spec = GSLParser().parse(path)
        checked = validate_spec(spec)
        if not checked.is_valid:
            if a.json:
                _emit({"valid": False, "errors": checked.errors, "warnings": checked.warnings})
            else:
                print(checked.summary())
            return 1
        pack = spec_pack(spec)
        domain = _pack_domain(a, "") if a.domain else spec_domain(spec)
        ref = spec.scenario
        scale = a.scale or (ref.scale if ref else None) or "small"
        seed = a.seed if a.seed is not None else (ref.seed if ref else 42)
        result = PackRunner().run(pack, domain, scale, seed, a.output, spec=spec)
    else:
        pack = PackLoader().load(path)
        domain = _pack_domain(a, pack.domain)
        result = PackRunner().run(
            pack, domain, a.scale or "small", a.seed if a.seed is not None else 42, a.output
        )
    if a.json:
        _emit(
            {
                "success": result.is_success,
                "pack": result.pack_id,
                "domain": result.domain,
                "scale": result.scale,
                "elapsed_seconds": round(result.elapsed_time, 3),
                "files": result.files_written,
                "events": result.events_emitted,
                "gates": result.validation_results,
                "gate_messages": result.gate_messages,
                "errors": result.errors,
                "warnings": result.warnings,
                "manifest": result.manifest.to_dict() if result.manifest else None,
            }
        )
    else:
        print(result.summary())
        for warning in result.warnings:
            print(f"  warning: {warning}")
    return 0 if result.is_success else 1


def _list(a: argparse.Namespace) -> int:
    from shape.scenario.gsl import is_spec_document
    from shape.scenario.loader import PackError, PackLoader

    roots = [Path(p) for p in a.paths] or (
        [Path(DEFAULT_ROOT)] if Path(DEFAULT_ROOT).is_dir() else []
    )
    for root in roots:
        if not root.is_dir():
            raise FileNotFoundError(f"not a directory: {root}")
    rows: list[dict[str, str]] = []
    for root in roots:
        for file in sorted([*root.rglob("*.yaml"), *root.rglob("*.yml")]):
            try:
                if is_spec_document(file):
                    rows.append({"kind": "spec", "id": file.stem, "domain": "", "path": str(file)})
                    continue
                pack = PackLoader().load(file)
            except PackError as exc:
                rows.append(
                    {
                        "kind": "invalid",
                        "id": file.stem,
                        "domain": "",
                        "path": str(file),
                        "error": str(exc),
                    }
                )
                continue
            rows.append(
                {"kind": pack.kind, "id": pack.id, "domain": pack.domain, "path": str(file)}
            )
    if a.json:
        _emit(rows)
        return 0
    if not rows:
        where = ", ".join(str(r) for r in roots) or f"./{DEFAULT_ROOT}"
        print(
            f"no packs found in {where} (Shape ships none: write one, see docs/SCENARIO_PACKS.md)"
        )
        return 0
    print(f"{'kind':<10}{'domain':<14}{'id':<28}path")
    for r in rows:
        print(f"{r['kind']:<10}{r['domain']:<14}{r['id']:<28}{r['path']}")
    return 0

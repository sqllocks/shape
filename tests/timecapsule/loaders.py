"""Load a time-capsule file through the public reader of its kind and return its canonical form.

The canonical form is the content the reader hands back, as canonical JSON (sorted keys, no
spaces, the artifact codec's tags for non-finite floats and tuples). Version bookkeeping keys that
a later release added (the unified ``format``, ``version``, ``shape_version`` and
``min_shape_version``) are left out where they did not exist when the file was written, so the
same content gives the same canonical form whichever release wrote it.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
import tempfile
import warnings
from pathlib import Path
from typing import Any

BOOKKEEPING = {"format", "version", "shape_version", "min_shape_version"}

# Kinds in which ``version`` is the document's own content (a contract's revision) and is kept.
KEEP_VERSION = {"contract-model", "gate-schema", "verify-config", "generation-spec"}
KEEP_FORMAT = {"gate-schema", "verify-config"}


def canonical_text(value: Any) -> str:
    from shape.artifact import codec

    return json.dumps(
        codec.encode(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )


def _strip(kind: str, doc: Any) -> Any:
    if not isinstance(doc, dict):
        return doc
    drop = set(BOOKKEEPING)
    if kind in KEEP_VERSION:
        drop.discard("version")
    if kind in KEEP_FORMAT:
        drop.discard("format")
    return {k: v for k, v in doc.items() if k not in drop}


def _artifact(path: Path, corpus: Path, kind: str) -> Any:
    from shape.artifact import read_model

    key = None
    pub = corpus / "artifact" / "trusted.pub"
    if path.name.endswith("-signed.shape"):
        key = bytes.fromhex(pub.read_text())
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        read = read_model(path, verify_key=key)
    manifest, model = read
    keep = (
        "name",
        "fidelity",
        "classification",
        "metadata",
        "shape_content_id",
        "migrated_from",
        "source_content_id",
    )
    return {
        "manifest": {k: manifest[k] for k in keep if k in manifest},
        "version": manifest.get("version", manifest.get("format_version")),
        "signature": read.signature["status"],
        "model": model,
    }


def _receipt(path: Path, corpus: Path, kind: str) -> Any:
    from shape import migrate

    pub = bytes.fromhex((path.parent / "receipt-signer.pub").read_text())
    source = corpus / "artifact" / "capture-v1-signed.shape"
    doc = migrate.verify_receipt(
        path, pub, source=source, result=path.with_name(path.name.removesuffix(".receipt.json"))
    )
    return {
        "kind": doc["kind"],
        "steps": doc["steps"],
        "source": {k: v for k, v in doc["source"].items() if k != "file_name"},
        "result": {k: v for k, v in doc["result"].items() if k != "file_name"},
        "signature": {k: v for k, v in doc["signature"].items() if k != "signature"},
        "verified": True,
    }


def _signature(path: Path, corpus: Path, kind: str) -> Any:
    from shape.artifact.signing import verify_manifest_signature

    pub = bytes.fromhex((corpus / "artifact" / "trusted.pub").read_text())
    manifest_bytes = (path.parent / "manifest-v1.json").read_bytes()
    verify_manifest_signature(manifest_bytes, path.read_bytes(), pub)
    doc = json.loads(path.read_bytes())
    return {"verifies": True, "algorithm": doc["algorithm"], "key_id": doc["key_id"]}


def _profile_artifact(path: Path, corpus: Path, kind: str) -> Any:
    import shape

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        prof = shape.load(str(path))
    return {"name": prof.name, "profile": prof.to_dict()}


def _profile_export(path: Path, corpus: Path, kind: str) -> Any:
    from shape.cli.profiles import read_export

    prof = read_export(str(path))
    return {"name": prof.name, "profile": prof.to_dict()}


def _safe_profile(path: Path, corpus: Path, kind: str) -> Any:
    from shape.privacy.safe_profile import SafeProfile

    return _strip(kind, SafeProfile.load(path).to_dict())


def _model(path: Path, corpus: Path, kind: str) -> Any:
    from shape.artifact import codec
    from shape.spec.migrate import to_model

    return to_model(codec.loads(path.read_bytes()))


def _generation_schema(path: Path, corpus: Path, kind: str) -> Any:
    from shape.generation.schema import GenSchema

    return _strip(kind, GenSchema.from_dict(json.loads(path.read_text())).to_dict())


def _gsl(path: Path, corpus: Path, kind: str) -> Any:
    from shape.scenario.gsl import GSLParser

    spec = GSLParser().parse(path)
    doc = dataclasses.asdict(spec)
    for k in ("path", "_base_dir", "needs_release"):  # needs_release: added by W1-01, not content
        doc.pop(k, None)
    return doc


def _pack(path: Path, corpus: Path, kind: str) -> Any:
    from shape.scenario.loader import PackLoader

    doc = dataclasses.asdict(PackLoader().load(path))
    doc.pop("needs_release", None)  # added by W1-01 (bookkeeping, not content)
    return doc


def _registry_local(path: Path, corpus: Path, kind: str) -> Any:
    from shape.registry import LocalRegistry

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "registry"
        shutil.copytree(path, root)
        reg = LocalRegistry(root)
        out: dict[str, Any] = {}
        for name in reg.names():
            out[name] = {
                "log": reg.log(name),
                "refs": reg.refs(name),
                "tags": reg.tags(name),
                "objects": {
                    cid: hashlib.sha256(reg.checkout(name, cid)).hexdigest()
                    for cid in sorted({e["content_id"] for e in reg.log(name)})
                },
            }
        return out


def _registry_profiles(path: Path, corpus: Path, kind: str) -> Any:
    from shape.registry.profiles import ProfileRegistry

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "registry"
        shutil.copytree(path, root)
        reg = ProfileRegistry(root)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            entries = reg.entries()
            problems = reg.validate()
        return {"entries": entries, "problems": problems}


def _run_manifest(path: Path, corpus: Path, kind: str) -> Any:
    from shape.scenario.manifest import ManifestBuilder

    return _strip(kind, ManifestBuilder.from_file(path).to_dict())


def _contract_check(path: Path, corpus: Path, kind: str) -> Any:
    from shape.contracts import v1

    doc = v1._load_contract(path)
    for table in doc.get("tables", {}).values():
        v1._validate_contract(table)
    return _strip(kind, doc)


def _contract_model(path: Path, corpus: Path, kind: str) -> Any:
    from shape.spec.model import ShapeContract

    return _strip(kind, ShapeContract.from_json(path.read_text()).to_dict())


def _gate_schema(path: Path, corpus: Path, kind: str) -> Any:
    from shape.quality.gatespec import GateSchema

    return _strip(kind, GateSchema.from_dict(json.loads(path.read_text())).to_dict())


def _verify_config(path: Path, corpus: Path, kind: str) -> Any:
    from shape.quality.verifyconfig import VerifyConfig

    cfg = VerifyConfig.from_dict(json.loads(path.read_text()))
    return {
        "rules": dict(cfg.rules),
        "file_paths": list(cfg.file_paths),
        "check_data_files": cfg.check_data_files,
    }


LOADERS = {
    "artifact": _artifact,
    "signature": _signature,
    "migration-receipt": _receipt,
    "profile-artifact": _profile_artifact,
    "profile-export": _profile_export,
    "safe-profile": _safe_profile,
    "model": _model,
    "generation-schema": _generation_schema,
    "generation-spec": _gsl,
    "scenario-pack": _pack,
    "registry-layout": _registry_local,
    "profile-registry-layout": _registry_profiles,
    "run-manifest": _run_manifest,
    "contract": _contract_check,
    "contract-model": _contract_model,
    "gate-schema": _gate_schema,
    "verify-config": _verify_config,
}


def canonical_of(corpus: Path, entry: dict[str, Any]) -> str:
    """The canonical form of one corpus entry, read by the release that is installed."""
    return canonical_text(LOADERS[entry["kind"]](corpus / entry["path"], corpus, entry["kind"]))

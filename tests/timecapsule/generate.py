"""Write a time-capsule generation: one file per persisted kind and format version, produced by
the Shape code that is installed, never edited by hand.

    python tests/timecapsule/generate.py tests/timecapsule/corpus/<generation>

A generation is frozen once committed (``test_timecapsule.py`` checks each file against the digest
in its ``index.json``). The corpus grows by adding a generation, written by the Shape release that
introduces a change, so every later release has to keep reading what every earlier one wrote.

Where the real writer of a format version no longer exists (a version 1 ``.shape`` artifact), the
file is built from the documented layout with the container writer, and the index says so
(``produced_by``). The only file with no writer at all, a verify configuration (hand-authored by
users), is a documented example and is marked ``authored``.

The generation is deterministic apart from timestamps in registry logs and run manifests, which
are frozen with the files.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

SEED = b"\x01" * 32  # a published test key: never use it for anything but this corpus
ROOT = Path(__file__).resolve().parent
PACK_SOURCE = ROOT.parent / "fixtures" / "packs" / "tutorial_custom_pack.yaml"


def _tree_digest(path: Path) -> str:
    h = hashlib.sha256()
    if path.is_file():
        h.update(path.read_bytes())
    else:
        for p in sorted(x for x in path.rglob("*") if x.is_file()):
            h.update(p.relative_to(path).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def _csv(directory: Path) -> Path:
    path = directory / "orders.csv"
    lines = ["id,status,amount,email,placed"]
    for i in range(60):
        lines.append(
            f"{i},{'paid' if i % 3 else 'new'},{i * 1.5:.2f},user{i}@example.com,2024-01-{1 + i % 28:02d}"
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def generate(out: Path) -> list[dict[str, Any]]:
    import shape
    from shape import __version__
    from shape.artifact import MIGRATIONS as _  # noqa: F401  (registers the v1 -> v2 step)
    from shape.artifact import codec, signing, write_model
    from shape.artifact.io import canonical_json, sha256, write_artifact
    from shape.capture import capture_rows
    from shape.cli.profiles import export_document
    from shape.generation.domains import load_domain
    from shape.integrations.fabric.generation import domain_contract
    from shape.privacy.safe_profile import to_safe_profile
    from shape.profile.engine import profile_many
    from shape.quality.gatespec import GateSchema
    from shape.registry import LocalRegistry
    from shape.registry.profiles import ProfileRegistry
    from shape.scenario.manifest import ManifestBuilder
    from shape.spec.migrate import to_model
    from shape.spec.model import ShapeContract
    from shape.spec.model import FieldContract

    if out.exists():
        raise SystemExit(f"{out} exists: a generation is written once")
    out.mkdir(parents=True)
    entries: list[dict[str, Any]] = []
    work = Path(tempfile.mkdtemp(prefix="timecapsule-"))

    def add(
        id_: str,
        kind: str,
        version: int,
        rel: str,
        produced_by: str,
        *,
        authored: bool = False,
        note: str = "",
    ) -> None:
        entries.append(
            {
                "id": id_,
                "kind": kind,
                "format_version": version,
                "path": rel,
                "sha256": _tree_digest(out / rel),
                "produced_by": produced_by,
                "authored": authored,
                "writer_shape_version": __version__,
                "note": note,
            }
        )

    rows = [{"id": i, "status": "paid" if i % 3 else "new", "amount": i * 1.5} for i in range(40)]
    capture = capture_rows(rows)
    private_key = SEED
    public_key = signing.public_key_of(private_key)

    # --- .shape artifact -----------------------------------------------------------------
    d = out / "artifact"
    d.mkdir()
    write_model(
        d / "model-v2.shape",
        capture.to_dict() if hasattr(capture, "to_dict") else capture,
        name="orders",
        fidelity="gold",
        classification="INTERNAL",
        metadata={"purpose": "time capsule"},
    )
    add("artifact-v2", "artifact", 2, "artifact/model-v2.shape", "shape.artifact.write_model")
    shutil.copy(d / "model-v2.shape", d / "model-v2-signed.shape")
    signing.sign_artifact(d / "model-v2-signed.shape", private_key)
    add(
        "artifact-v2-signed",
        "artifact",
        2,
        "artifact/model-v2-signed.shape",
        "shape.artifact.write_model + shape.artifact.signing.sign_artifact",
    )
    # version 1: a v1 capture in shape.json. Nothing writes it any more, so the container is built
    # from the version 1 layout with the container writer; the reader migrates it.
    v1_doc = capture.to_dict() if hasattr(capture, "to_dict") else capture
    body = codec.dumps(v1_doc, sort_keys=True)
    write_artifact(
        d / "capture-v1.shape",
        {
            "format": "shape",
            "format_version": 1,
            "name": "orders",
            "shape_content_id": sha256(body),
            "fidelity": "gold",
            "classification": "INTERNAL",
            "metadata": {"purpose": "time capsule"},
        },
        {"shape.json": body},
    )
    add(
        "artifact-v1",
        "artifact",
        1,
        "artifact/capture-v1.shape",
        "shape.artifact.io.write_artifact with the version 1 manifest layout",
        note="no writer for version 1 remains; read through the migration registry",
    )
    shutil.copy(d / "capture-v1.shape", d / "capture-v1-signed.shape")
    signing.sign_artifact(d / "capture-v1-signed.shape", private_key)
    add(
        "artifact-v1-signed",
        "artifact",
        1,
        "artifact/capture-v1-signed.shape",
        "version 1 layout + shape.artifact.signing.sign_artifact",
    )
    (d / "trusted.pub").write_bytes(public_key.hex().encode())

    # --- signature file ------------------------------------------------------------------
    import zipfile

    with zipfile.ZipFile(d / "model-v2-signed.shape") as z:
        (out / "signature").mkdir()
        (out / "signature" / "manifest-v1.sig").write_bytes(z.read("manifest.sig"))
        (out / "signature" / "manifest-v1.json").write_bytes(z.read("manifest.json"))
    add(
        "signature-v1",
        "signature",
        1,
        "signature/manifest-v1.sig",
        "shape.artifact.signing.sign_artifact (member manifest.sig)",
        note="manifest-v1.json is the manifest the signature covers",
    )

    # --- profile artifact, profile export ------------------------------------------------
    csv = _csv(work)
    profile = shape.profile(str(csv))
    (out / "profile").mkdir()
    shape.save(profile, str(out / "profile" / "orders.shape"))
    add("profile-artifact-v1", "profile-artifact", 1, "profile/orders.shape", "shape.save")
    (out / "profile" / "orders.export.json").write_text(
        json.dumps(export_document(profile), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    add(
        "profile-export-v1",
        "profile-export",
        1,
        "profile/orders.export.json",
        "shape.cli.profiles.export_document",
    )

    # --- safe profile --------------------------------------------------------------------
    (out / "safe").mkdir()
    to_safe_profile(profile).save(out / "safe" / "orders.safe.json")
    add("safe-profile-v1", "safe-profile", 1, "safe/orders.safe.json", "SafeProfile.save")

    # --- model ---------------------------------------------------------------------------
    (out / "model").mkdir()
    engine_doc = profile_many({"orders": str(csv)})
    (out / "model" / "engine-v1.json").write_bytes(codec.dumps(engine_doc, sort_keys=True))
    add(
        "model-engine-v1",
        "model",
        1,
        "model/engine-v1.json",
        "shape.profile.engine.profile_many (schema_version 1)",
    )
    (out / "model" / "model-v2.json").write_bytes(codec.dumps(to_model(engine_doc), sort_keys=True))
    add(
        "model-v2",
        "model",
        2,
        "model/model-v2.json",
        "shape.spec.migrate.to_model (the shape.json body of a .shape artifact)",
    )

    # --- generation spec -----------------------------------------------------------------
    (out / "generation").mkdir()
    schema = load_domain("retail").schema
    (out / "generation" / "retail.json").write_text(
        json.dumps(schema.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    add("generation-spec-v1", "generation-spec", 1, "generation/retail.json", "GenSchema.to_dict")

    # --- scenario pack -------------------------------------------------------------------
    (out / "pack").mkdir()
    shutil.copy(PACK_SOURCE, out / "pack" / "custom_pack.yaml")
    add(
        "scenario-pack-v1",
        "scenario-pack",
        1,
        "pack/custom_pack.yaml",
        "pack YAML as users write it (tests/fixtures/packs/tutorial_custom_pack.yaml)",
        authored=True,
        note="packs are authored, not written by a command",
    )

    # --- registries ----------------------------------------------------------------------
    local = LocalRegistry(out / "registry-local")
    local.commit("orders", (out / "safe" / "orders.safe.json").read_bytes(), {"note": "capsule"})
    local.commit("orders", (out / "artifact" / "model-v2.shape").read_bytes(), {"note": "second"})
    local.tag("orders", "v1", "latest")
    add(
        "registry-local-v1",
        "registry-local",
        1,
        "registry-local",
        "shape.registry.LocalRegistry.commit/tag",
    )
    profiles = ProfileRegistry(out / "registry-profiles")
    profiles.save(profile, system="erp", name="nightly", tags=["capsule"], description="capsule")
    profiles.save(
        profile, system="erp", name="shareable", tags=["capsule"], description="safe", safe=True
    )
    add(
        "registry-profiles-v1",
        "registry-profiles",
        1,
        "registry-profiles",
        "shape.registry.profiles.ProfileRegistry.save",
    )

    # --- run manifest --------------------------------------------------------------------
    (out / "run").mkdir()
    builder = ManifestBuilder()

    class _Pack:
        id = "capsule_pack"

    builder.start(PACK_SOURCE, _Pack(), "retail", "small", 42)
    builder.record_output("orders", 60, 5, ["Files/orders.parquet"])
    builder.record_validation("schema_conformance", True)
    builder.record_chaos("duplicates", 2)
    ManifestBuilder.to_file(builder.finish(), out / "run" / "run-manifest.json")
    add(
        "run-manifest-v1",
        "run-manifest",
        1,
        "run/run-manifest.json",
        "shape.scenario.manifest.ManifestBuilder.to_file",
    )

    # --- contract files ------------------------------------------------------------------
    (out / "contract").mkdir()
    (out / "contract" / "check-contract.json").write_text(
        json.dumps(domain_contract(schema, {n: 10 for n in schema.tables}), indent=2) + "\n",
        encoding="utf-8",
    )
    add(
        "contract-check-v1",
        "contract-check",
        1,
        "contract/check-contract.json",
        "shape.integrations.fabric.generation.domain_contract",
    )
    contract = ShapeContract(
        "customer", fields=(FieldContract("id", "integer", False, sensitivity="CONFIDENTIAL"),)
    ).validate()
    (out / "contract" / "customer.contract.json").write_text(contract.to_json(), encoding="utf-8")
    add(
        "contract-model-v1",
        "contract-model",
        1,
        "contract/customer.contract.json",
        "shape.spec.ShapeContract.to_json",
    )

    # --- gate schema, verify configuration -----------------------------------------------
    (out / "quality").mkdir()
    gate = GateSchema.from_dict(profile.to_dict())
    (out / "quality" / "gate-schema.json").write_text(
        json.dumps(gate.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    add(
        "gate-schema-v1",
        "gate-schema",
        1,
        "quality/gate-schema.json",
        "shape.quality.gatespec.GateSchema.to_dict",
    )
    (out / "quality" / "verify-config.json").write_text(
        json.dumps(
            {
                "format": "shape-verify-config",
                "version": 1,
                "ranges": {"orders.amount": {"min": 0, "max": 1000}},
                "no_future": ["orders.placed"],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    add(
        "verify-config-v1",
        "verify-config",
        1,
        "quality/verify-config.json",
        "documented example (docs/VERIFY.md); users write these",
        authored=True,
    )

    shutil.rmtree(work, ignore_errors=True)
    (out / "index.json").write_bytes(canonical_json({"files": entries}) + b"\n")
    return entries


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    for e in generate(Path(sys.argv[1])):
        print(f"{e['id']:24} {e['path']}")

"""Offline migration of persisted files (``docs/specs/STATE_AND_COMPATIBILITY.md``).

``shape migrate SRC DST`` (or ``shape-migrate``) turns a file written by an older release, or with
the key names an older release used, into the current form:

* it never rewrites in place: the result is a new file, the original is kept byte for byte, and an
  existing destination is never overwritten;
* the result records ``migrated_from`` and ``source_content_id`` (an artifact's manifest, a
  document's top level) and a receipt next to it names every step, both files by SHA-256 and the
  signature of the source;
* ``dry_run`` reports the plan and writes nothing;
* a downgrade, or a version this release does not write, is refused;
* the result is read back before it is published and must have the content id the plan predicted
  and migrate to nothing (a second run is a no-op).

A signed source keeps its signature as evidence in the original file, which is not touched. The
migrated artifact is unsigned unless a key is given, and the receipt is signed with that key (an
unsigned receipt needs ``unsigned_receipt=True``, said out loud).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shape import compat
from shape.errors import ShapeError

RECEIPT_SUFFIX = ".receipt.json"
# Domain separation: a receipt signature is never valid for an artifact manifest, or the reverse.
RECEIPT_DOMAIN = b"shape-migration-receipt-signature-v1\n"
MAX_JSON_BYTES = 256 * 1024 * 1024

UNIFY_STEP = "unify-version-keys"
_MIGRATION_KEYS = ("migrated_from", "source_content_id")


class MigrationError(ShapeError, ValueError):
    """A migration was refused or did not check out; nothing is left behind."""


@dataclass(frozen=True)
class MigrationPlan:
    """What a migration would do."""

    kind: str
    source_version: int
    target_version: int
    steps: tuple[str, ...]
    source_content_id: str
    result_content_id: str
    noop: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "source_version": self.source_version,
            "target_version": self.target_version,
            "steps": list(self.steps),
            "source_content_id": self.source_content_id,
            "result_content_id": self.result_content_id,
            "noop": self.noop,
        }


@dataclass(frozen=True)
class MigrationResult:
    plan: MigrationPlan
    dry_run: bool
    written: bool
    destination: Path | None = None
    receipt: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "dry_run": self.dry_run,
            "written": self.written,
            "plan": self.plan.to_dict(),
            "destination": str(self.destination) if self.destination else None,
            "receipt": str(self.receipt) if self.receipt else None,
        }


# --- content ids ---------------------------------------------------------------------------------


def _clean(kind: str, doc: dict[str, Any]) -> dict[str, Any]:
    """``doc`` without its declaration and migration record: what its content id covers."""
    k = compat.KINDS[kind]
    drop = {*compat.BOOKKEEPING_KEYS, *_MIGRATION_KEYS, *k.legacy_version_keys}
    return {key: v for key, v in doc.items() if key not in drop}


def document_content_id(kind: str, doc: dict[str, Any]) -> str:
    """The SHA-256 of the canonical form of a JSON document, without its declaration (``format``,
    ``version``, ``shape_version``, ``min_shape_version``), the old version key names and the
    migration record: renaming keys does not change what the document says."""
    from shape.artifact import codec

    body = doc if compat.KINDS[kind].unified_in_body is False else _clean(kind, doc)
    return hashlib.sha256(codec.dumps(body, sort_keys=True)).hexdigest()


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --- what kind of file is it ---------------------------------------------------------------------

_BY_FORMAT = {k.format: k.name for k in compat.KINDS.values() if k.format != "shape"}


def sniff_kind(doc: Any) -> str | None:
    """The kind of a JSON document: by its ``format``, else by the fields that kind alone has."""
    if not isinstance(doc, dict):
        return None
    fmt = doc.get("format")
    if isinstance(fmt, str) and fmt in _BY_FORMAT:
        return _BY_FORMAT[fmt]
    keys = doc.keys()
    if doc.get("engine") == "shape-profile-engine" and "tables" in keys:
        return "model"
    if {"schema_version", "model", "tables"} <= keys:
        return "generation-schema"
    if "tables" in keys and "schema_version" in keys and ({"redaction_manifest", "unsafe"} & keys):
        return "safe-profile"
    if {"run_id", "spec_hash", "pack_id"} <= keys:
        return "run-manifest"
    if {"name", "fields"} <= keys and "tables" not in keys:
        return "contract-model"
    return None


# --- the preparation: a plan and how to build its result -----------------------------------------


@dataclass
class _Prepared:
    plan: MigrationPlan
    build: Callable[[Path], None]  # write the result to a temp path
    source_signature: dict[str, Any] | None
    source_signed: bool
    is_artifact: bool


def _target(kind: str, source: int, to: int | None) -> int:
    k = compat.KINDS[kind]
    target = k.current if to is None else to
    if target < source:
        raise MigrationError(
            f"downgrade refused: the file is version {source} and version {target} was asked "
            "(a migration only moves forward; keep the original for the older release)"
        )
    if target not in k.first_release:
        raise MigrationError(
            f"a {k.label} has no version {target} (this release writes up to {k.current})"
        )
    return target


def _declaration_complete(doc: dict[str, Any]) -> bool:
    need = (compat.FORMAT_KEY, compat.VERSION_KEY, compat.WRITER_KEY, compat.MINIMUM_KEY)
    return all(key in doc for key in need)


def _signature_info(path: Path, signature: dict[str, Any]) -> dict[str, Any] | None:
    if signature["status"] == "unsigned":
        return None
    algorithm: Any = None
    with contextlib.suppress(Exception):
        with zipfile.ZipFile(path) as z:
            doc = json.loads(z.read("manifest.sig"))
        algorithm = doc.get("algorithm") if isinstance(doc, dict) else None
    return {
        "algorithm": algorithm if isinstance(algorithm, str) else None,
        "key_id": signature.get("key_id"),
        "verified": bool(signature.get("verified")),
    }


def _profile_v1_to_v2(manifest: dict[str, Any]) -> dict[str, Any]:
    """Version 2 records how the profile was captured. A version 1 profile holds what the data
    held, so it is a full capture; the body is not touched."""
    return {**manifest, "capture": {"mode": "full", "k": None}}


# profile artifact: source version -> (step name, step); each advances exactly one version
_PROFILE_STEPS: dict[int, tuple[str, Callable[[dict[str, Any]], dict[str, Any]]]] = {
    1: ("profile-capture-full", _profile_v1_to_v2),
}


def _migrate_profile_manifest(
    manifest: dict[str, Any], version: int, target: int
) -> tuple[tuple[str, ...], dict[str, Any]]:
    names: list[str] = []
    for v in range(version, target):
        if v not in _PROFILE_STEPS:
            raise MigrationError(f"a profile artifact has no migration from {v}")
        name, step = _PROFILE_STEPS[v]
        manifest = step(manifest)
        names.append(name)
    return tuple(names), manifest


def _prepare_artifact(src: Path, to: int | None, verify_key: bytes | None) -> _Prepared:
    import warnings

    from shape.artifact import codec
    from shape.artifact.io import ArtifactError, read_artifact, sha256, write_artifact
    from shape.artifact.migrate import MIGRATIONS

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        read = read_artifact(src, verify_key=verify_key, notice=False)
    manifest, parts = read
    if manifest.get("format") != "shape":
        raise MigrationError(f"{src} is not a Shape artifact")
    kind = "profile-artifact" if manifest.get("kind") == "profile" else "artifact"
    version = compat.check_readable(kind, manifest, src, error=ArtifactError)
    target = _target(kind, version, to)
    component = "profile.json" if kind == "profile-artifact" else "shape.json"
    body = parts.get(component)
    if body is None:
        raise MigrationError(f"{src}: {component} is missing")
    source_id = manifest.get("shape_content_id")
    if not isinstance(source_id, str) or source_id != sha256(body):
        raise MigrationError(f"{src}: the content id does not match {component}")

    new_body = body
    steps: tuple[str, ...]
    migrated_manifest: dict[str, Any] = dict(manifest)
    if version < target:
        if kind == "profile-artifact":
            steps, migrated_manifest = _migrate_profile_manifest(dict(manifest), version, target)
        elif kind != "artifact":
            raise MigrationError(f"a {compat.KINDS[kind].label} has no migration from {version}")
        else:
            path = MIGRATIONS.path(version, target)
            steps = tuple(m.name for m in path)
            migrated_manifest, obj, _ = MIGRATIONS.migrate(
                dict(manifest), codec.loads(body), target
            )
            new_body = codec.dumps(obj, sort_keys=True)
    elif _declaration_complete(manifest):
        steps = ()
    else:
        steps = (UNIFY_STEP,)
    result_id = sha256(new_body)
    plan = MigrationPlan(kind, version, target, steps, source_id, result_id, noop=not steps)

    def build(out: Path) -> None:
        keep = {
            k: v
            for k, v in migrated_manifest.items()
            if k not in ("content_hashes", *compat.BOOKKEEPING_KEYS, *_MIGRATION_KEYS)
        }
        keep["shape_content_id"] = result_id
        new = compat.stamp(kind, keep, version=target)
        new["migrated_from"] = version
        new["source_content_id"] = source_id
        components = dict(parts)
        components[component] = new_body
        write_artifact(out, new, components)

    info = _signature_info(src, read.signature)
    return _Prepared(plan, build, info, info is not None, True)


def _load_json(src: Path) -> Any:
    from shape.artifact import codec

    if src.stat().st_size > MAX_JSON_BYTES:
        raise MigrationError(f"{src} is too large to migrate")
    try:
        return codec.loads(src.read_bytes())
    except (ValueError, RecursionError) as e:
        raise MigrationError(f"{src} is neither a .shape artifact nor a JSON file ({e})") from e


def _prepare_json(src: Path, to: int | None, kind: str | None) -> _Prepared:
    from shape.artifact import codec

    doc = _load_json(src)
    if kind is not None and kind not in compat.KINDS:
        raise MigrationError(
            f"{kind!r} is not a file kind (kinds: {', '.join(sorted(compat.KINDS))})"
        )
    found = kind or sniff_kind(doc)
    if found is None or not isinstance(doc, dict):
        raise MigrationError(
            f"{src} is not a file kind this release knows: pass --kind (one of "
            f"{', '.join(sorted(compat.KINDS))}) if you are sure what it is"
        )
    k = compat.KINDS[found]
    if found in ("artifact", "profile-artifact"):
        raise MigrationError(f"a {k.label} is a .shape archive, not a JSON file")
    version = compat.check_readable(found, doc, src)
    target = _target(found, version, to)
    source_id = document_content_id(found, doc)

    if found == "model":
        from shape.spec.migrate import to_model

        if version < target:
            steps: tuple[str, ...] = ("engine-v1-to-model-v2",)
            result_doc = to_model(doc)
            result_id = document_content_id(found, result_doc)
        else:
            steps, result_doc, result_id = (), doc, source_id
        plan = MigrationPlan(found, version, target, steps, source_id, result_id, noop=not steps)

        def build_model(out: Path) -> None:
            out.write_bytes(codec.dumps(result_doc, sort_keys=True))

        return _Prepared(plan, build_model, None, False, False)

    if version < target:
        raise MigrationError(f"a {k.label} has no migration from version {version}")
    steps = () if _declaration_complete(doc) else (UNIFY_STEP,)
    plan = MigrationPlan(found, version, target, steps, source_id, source_id, noop=not steps)

    def build(out: Path) -> None:
        keep = {
            key: v
            for key, v in doc.items()
            if key not in (*compat.BOOKKEEPING_KEYS, *_MIGRATION_KEYS)
        }
        new = compat.stamp(found, keep, version=target)
        new["migrated_from"] = version
        new["source_content_id"] = source_id
        out.write_text(
            json.dumps(new, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    return _Prepared(plan, build, None, False, False)


def _prepare(src: Path, to: int | None, kind: str | None, verify_key: bytes | None) -> _Prepared:
    if not src.is_file():
        raise MigrationError(f"file not found: {src}")
    if zipfile.is_zipfile(src):
        return _prepare_artifact(src, to, verify_key)
    return _prepare_json(src, to, kind)


def plan(
    src: str | os.PathLike[str],
    *,
    to: int | None = None,
    kind: str | None = None,
    verify_key: bytes | None = None,
) -> MigrationPlan:
    """What migrating ``src`` would do; reads only."""
    return _prepare(Path(src), to, kind, verify_key).plan


# --- refusals ------------------------------------------------------------------------------------


def _refuse_in_place(src: Path, dst: Path, receipt: Path) -> None:
    if src.resolve() == dst.resolve() or (dst.exists() and os.path.samefile(src, dst)):
        raise MigrationError(
            f"{dst} is the source: a migration never rewrites in place, give a new file name"
        )
    if dst.exists() or dst.is_symlink():
        raise MigrationError(f"{dst} exists: a migration never overwrites a file")
    if receipt.exists() or receipt.is_symlink():
        raise MigrationError(f"{receipt} exists: a migration never overwrites a file")
    if not dst.parent.is_dir():
        raise MigrationError(f"{dst.parent} is not a directory")


# --- the round trip ------------------------------------------------------------------------------


def _check_round_trip(prepared: _Prepared, written: Path, kind: str | None) -> None:
    """Read the result back: its content id is the planned one and migrating it again is a no-op."""
    again = _prepare(written, None, kind, None)
    expected = prepared.plan.result_content_id
    if again.plan.source_content_id != expected:
        raise MigrationError(
            f"round trip check failed: the result has content id {again.plan.source_content_id}, "
            f"the plan said {expected}"
        )
    if not again.plan.noop:
        raise MigrationError("round trip check failed: the result would still migrate")
    if again.plan.source_version != prepared.plan.target_version:
        raise MigrationError("round trip check failed: the result is not at the target version")


# --- the receipt ---------------------------------------------------------------------------------


def _receipt_message(doc: dict[str, Any]) -> bytes:
    from shape.artifact.io import canonical_json

    body = {k: v for k, v in doc.items() if k != "signature"}
    return RECEIPT_DOMAIN + canonical_json(body)


def _receipt(
    prepared: _Prepared, src: Path, built: Path, final_name: str, signature_key: bytes | None
) -> dict[str, Any]:
    """The receipt of a result that is still at ``built`` and will be published as ``final_name``;
    signed with ``signature_key`` when there is one."""
    from shape.artifact import signing

    p = prepared.plan
    doc = compat.stamp(
        "migration-receipt",
        {
            "created": compat.utc_iso(),
            "kind": p.kind,
            "source": {
                "file_name": src.name,
                "file_sha256": _sha_file(src),
                "content_id": p.source_content_id,
                "version": p.source_version,
                "signature": prepared.source_signature,
            },
            "result": {
                "file_name": final_name,
                "file_sha256": _sha_file(built),
                "content_id": p.result_content_id,
                "version": p.target_version,
            },
            "steps": list(p.steps),
        },
        aliases=False,
    )
    if signature_key is not None:
        from shape.security.crypto import sign_ed25519

        public = signing.public_key_of(signature_key)
        sig = sign_ed25519(_receipt_message(doc), signature_key)
        doc["signature"] = {
            "algorithm": signing.ALGORITHM,
            "key_id": signing.key_id(public),
            "signature": base64.b64encode(sig).decode(),
        }
    return doc


def verify_receipt(
    path: str | os.PathLike[str],
    public_key: bytes,
    *,
    source: str | os.PathLike[str] | None = None,
    result: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Check the signature of a migration receipt under ``public_key`` and, when given, that
    ``source`` and ``result`` are the files it names. Returns the receipt."""
    from shape.artifact import signing
    from shape.artifact.io import ArtifactSignatureError
    from shape.errors import ShapeSecurityError
    from shape.security.crypto import verify_ed25519

    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise ArtifactSignatureError(f"{path} is not a migration receipt ({e})") from e
    if not isinstance(doc, dict):
        raise ArtifactSignatureError(f"{path} is not a migration receipt")
    compat.check_format("migration-receipt", doc, error=ArtifactSignatureError)
    compat.check_readable("migration-receipt", doc, path, error=ArtifactSignatureError)
    sig = doc.get("signature")
    if not isinstance(sig, dict):
        raise ArtifactSignatureError("the receipt is not signed")
    if sig.get("algorithm") != signing.ALGORITHM:
        raise ArtifactSignatureError("unsupported signature algorithm")
    if sig.get("key_id") != signing.key_id(public_key):
        raise ArtifactSignatureError("receipt is signed by a different key than the trusted key")
    try:
        raw = base64.b64decode(str(sig["signature"]), validate=True)
        verify_ed25519(_receipt_message(doc), raw, public_key)
    except (KeyError, ValueError, ShapeSecurityError) as e:
        raise ArtifactSignatureError("signature does not match the receipt") from e
    for role, given in (("source", source), ("result", result)):
        if given is None:
            continue
        named = doc.get(role, {}).get("file_sha256")
        if _sha_file(Path(given)) != named:
            raise MigrationError(f"{given} is not the {role} file this receipt names")
    return doc


# --- the migration -------------------------------------------------------------------------------


def _publish(tmp: Path, dst: Path) -> None:
    """Move ``tmp`` to ``dst`` without ever replacing a file that appeared meanwhile."""
    try:
        os.link(tmp, dst)
    except FileExistsError as e:
        raise MigrationError(f"{dst} exists: a migration never overwrites a file") from e
    except OSError:
        if dst.exists():
            raise MigrationError(f"{dst} exists: a migration never overwrites a file") from None
        os.replace(tmp, dst)
        return
    os.unlink(tmp)


def migrate_file(
    src: str | os.PathLike[str],
    dst: str | os.PathLike[str],
    *,
    to: int | None = None,
    kind: str | None = None,
    dry_run: bool = False,
    sign_key: bytes | None = None,
    verify_key: bytes | None = None,
    unsigned_receipt: bool = False,
) -> MigrationResult:
    """Migrate ``src`` to a new file ``dst`` (and ``dst`` + ``.receipt.json``).

    ``to`` is the target version (default: the current one). ``sign_key`` (32 raw bytes) signs the
    migrated artifact and the receipt; a signed source needs it, or ``unsigned_receipt=True``.
    ``verify_key`` checks the source's signature first (a failure writes nothing). A file that is
    already current migrates to nothing: the result is a no-op plan and no file is written."""
    source, dest = Path(src), Path(dst)
    receipt_path = dest.with_name(dest.name + RECEIPT_SUFFIX)
    _refuse_in_place(source, dest, receipt_path)
    prepared = _prepare(source, to, kind, verify_key)
    if prepared.plan.noop:
        return MigrationResult(prepared.plan, dry_run, written=False)
    if dry_run:
        return MigrationResult(prepared.plan, True, written=False)
    if prepared.source_signed and sign_key is None and not unsigned_receipt:
        raise MigrationError(
            f"{source} is signed: the migrated file is a new file, so say who vouches for the "
            "migration: pass --sign-key to sign the receipt (and the new artifact), or "
            "--unsigned-receipt to accept an unsigned receipt. The original stays untouched."
        )

    tmps: list[Path] = []
    published: list[Path] = []
    try:
        fd, name = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-migrate-")
        os.close(fd)
        tmp = Path(name)
        tmps.append(tmp)
        prepared.build(tmp)
        _check_round_trip(prepared, tmp, prepared.plan.kind if not prepared.is_artifact else None)
        if sign_key is not None and prepared.is_artifact:
            from shape.artifact.signing import sign_artifact

            sign_artifact(tmp, sign_key)
        # the receipt names the final bytes of the result, so it is built from the temp file
        receipt_doc = _receipt(prepared, source, tmp, dest.name, sign_key)
        rfd, rname = tempfile.mkstemp(dir=dest.parent, prefix=".tmp-migrate-")
        os.close(rfd)
        rtmp = Path(rname)
        tmps.append(rtmp)
        rtmp.write_text(
            json.dumps(receipt_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        _publish(tmp, dest)
        published.append(dest)
        _publish(rtmp, receipt_path)
        published.append(receipt_path)
    except BaseException:
        for p in published:
            with contextlib.suppress(OSError):
                p.unlink()
        raise
    finally:
        for t in tmps:
            with contextlib.suppress(OSError):
                t.unlink()
    return MigrationResult(
        prepared.plan, False, written=True, destination=dest, receipt=receipt_path
    )

"""Vault operations on files: the profile's vault reference, verification, key rotation and the
git guard.

The profile artifact's manifest carries ``vault: {"vault_id", "sha256"}`` (SHA-256 of the vault
file's bytes). The signature covers ``manifest.json``, so signing the profile also signs the vault
hash with no change to the signing code.
"""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import stat
import subprocess  # nosec B404
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from shape.artifact.io import (
    ArtifactError,
    ArtifactSignatureError,
    read_artifact,
    write_artifact,
)

from .errors import (
    VaultFormatError,
    VaultInputError,
    VaultMismatchError,
    VaultReferenceError,
    VaultVersionError,
)
from .format import OpenedVault, ParsedVault, open_vault, parse_vault, seal_vault

VAULT_SUFFIX = ".shapevault"
GITIGNORE_LINE = f"*{VAULT_SUFFIX}"
_SHA = re.compile(r"[0-9a-f]{64}")
_ID = re.compile(r"[0-9a-f]{32}")


def sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# --- the profile's reference ---------------------------------------------------------------------


def vault_reference(manifest: dict[str, Any]) -> dict[str, str] | None:
    """The ``vault`` reference of a profile manifest, ``None`` when it has none (a profile written
    without a vault); a reference that is not ``{"vault_id", "sha256"}`` is an artifact error."""
    ref = manifest.get("vault")
    if ref is None:
        return None
    if (
        not isinstance(ref, dict)
        or set(ref) != {"vault_id", "sha256"}
        or not isinstance(ref["vault_id"], str)
        or not _ID.fullmatch(ref["vault_id"])
        or not isinstance(ref["sha256"], str)
        or not _SHA.fullmatch(ref["sha256"])
    ):
        raise ArtifactError("the profile's vault reference is not {vault_id, sha256}")
    return {"vault_id": ref["vault_id"], "sha256": ref["sha256"]}


def reference_for(raw: bytes) -> dict[str, str]:
    """The manifest reference of the vault ``raw``."""
    return {"vault_id": parse_vault(raw).vault_id, "sha256": sha256_hex(raw)}


# --- files ---------------------------------------------------------------------------------------


def write_file(path: str | os.PathLike[str], data: bytes, *, overwrite: bool = True) -> None:
    """Write ``data`` to ``path`` with mode 0600 where the OS has mode bits, atomically (a temp
    file in the same directory, then a rename); without ``overwrite`` an existing file is
    refused."""
    target = Path(path)
    if not overwrite and target.exists():
        raise VaultInputError(f"{target} exists and is not overwritten")
    fd, tmp = tempfile.mkstemp(dir=target.parent or ".", prefix=".vault-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        if os.name == "posix":
            os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
        if overwrite:
            os.replace(tmp, target)
        else:
            os.link(tmp, target)  # fails if it appeared meanwhile
            os.unlink(tmp)
    except FileExistsError:
        Path(tmp).unlink(missing_ok=True)
        raise VaultInputError(f"{target} exists and is not overwritten") from None
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def read_vault_bytes(path: str | os.PathLike[str]) -> bytes:
    from .format import MAX_VAULT_BYTES

    p = Path(path)
    try:
        if p.stat().st_size > MAX_VAULT_BYTES:
            raise VaultFormatError("malformed vault: file too large")
        return p.read_bytes()
    except OSError as e:
        raise VaultInputError(f"cannot read vault {p}: {e.strerror or type(e).__name__}") from None


# --- git guard -----------------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes] | None:
    try:
        # only `git`, with an argument list of our own and no shell; a path is passed as data
        return subprocess.run(  # nosec B603 B607
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            timeout=20,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def ensure_git_ignored(paths: Iterable[tuple[str | os.PathLike[str], str]]) -> None:
    """For each ``(path, what)``: when ``path`` is inside a git work tree and git does not ignore
    it, raise :class:`VaultInputError` naming the ``.gitignore`` line to add. A path outside any
    work tree, or no git at all, passes."""
    for path, what in paths:
        p = Path(path).absolute()
        directory = p.parent
        while not directory.exists() and directory != directory.parent:
            directory = directory.parent
        inside = _git(["rev-parse", "--is-inside-work-tree"], directory)
        if inside is None or inside.returncode != 0 or inside.stdout.strip() != b"true":
            continue
        ignored = _git(["check-ignore", "-q", "--no-index", "--", str(p)], directory)
        if ignored is None or ignored.returncode == 0:
            continue
        line = GITIGNORE_LINE if p.suffix == VAULT_SUFFIX else p.name
        raise VaultInputError(
            f"the {what} {p.name} is inside a git work tree and git does not ignore it: add the "
            f"line {line} to .gitignore (or keep it outside the repository)"
        )


# --- verify --------------------------------------------------------------------------------------


def _check(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"check": name, "ok": ok, "detail": detail}


def verify_vault(
    vault_path: str | os.PathLike[str],
    shape_path: str | os.PathLike[str],
    *,
    kek: bytes | None = None,
    verify_key: bytes | None = None,
) -> dict[str, Any]:
    """Check a vault against its profile: the SHA-256 and ``vault_id`` against the profile's
    ``vault`` reference, ``profile_content_id`` against the profile's content id, the profile's
    signature when ``verify_key`` is given, and with ``kek`` that the data key and every column
    authenticate. Returns ``{"ok", "checks": [...], "vault_id", "columns"}``; a malformed vault,
    a newer version or an unreadable profile raise (exit 2)."""
    raw = read_vault_bytes(vault_path)
    checks: list[dict[str, Any]] = []
    parse_failure: VaultFormatError | None = None
    parsed: ParsedVault | None = None
    try:
        parsed = parse_vault(raw)
    except VaultFormatError as e:
        # The profile's hash is checked first: bytes that differ from what the profile recorded
        # are a mismatch (exit 1), whatever state they are in; only a vault whose bytes match, or
        # a profile with no reference to compare, is reported as malformed (exit 2).
        parse_failure = e
    signature_error: str | None = None
    if verify_key is not None:
        try:
            read_artifact(shape_path, verify_key=verify_key, notice=False)
        except ArtifactSignatureError as e:
            signature_error = str(e)
    manifest, _ = read_artifact(shape_path, notice=False)
    if verify_key is not None:
        checks.append(
            _check(
                "signature", signature_error is None, signature_error or "profile signature valid"
            )
        )
    try:
        ref = vault_reference(manifest)
    except ArtifactError as e:
        raise VaultFormatError(str(e)) from None
    digest = sha256_hex(raw)
    if parse_failure is not None and (
        isinstance(parse_failure, VaultVersionError) or ref is None or ref["sha256"] == digest
    ):
        raise parse_failure
    if ref is None:
        checks.append(_check("reference", False, "the profile has no vault reference"))
    else:
        match = ref["sha256"] == digest
        checks.append(
            _check(
                "sha256",
                match,
                "the vault bytes match the profile's hash"
                if match
                else "the vault bytes differ from the hash the profile records",
            )
        )
    if parsed is None:  # the bytes differ from the recorded hash and are not a readable vault
        checks.append(_check("vault", False, "the vault is altered: it is not a readable vault"))
        return {"ok": False, "vault_id": None, "columns": [], "checks": checks}
    if ref is not None:
        same_id = ref["vault_id"] == parsed.vault_id
        checks.append(
            _check(
                "vault_id",
                same_id,
                "vault_id matches the profile's reference"
                if same_id
                else "vault_id differs from the profile's reference",
            )
        )
    pid = manifest.get("shape_content_id")
    same_profile = isinstance(pid, str) and pid == parsed.profile_content_id
    checks.append(
        _check(
            "profile_content_id",
            same_profile,
            "the vault belongs to this profile"
            if same_profile
            else "the vault belongs to another profile",
        )
    )
    columns = sorted(parsed.columns)
    if kek is not None:
        try:
            open_vault(raw, kek)
            checks.append(
                _check("decrypt", True, f"data key and {len(columns)} column(s) authenticate")
            )
        except VaultMismatchError as e:
            checks.append(_check("decrypt", False, str(e)))
    return {
        "ok": all(c["ok"] for c in checks),
        "vault_id": parsed.vault_id,
        "columns": columns,
        "checks": checks,
    }


def require_matching(
    vault_path: str | os.PathLike[str], shape_path: str | os.PathLike[str], **kw: Any
) -> bytes:
    """The vault's bytes, after :func:`verify_vault` found it matches the profile (no key check
    unless ``kek`` is passed); a mismatch raises :class:`VaultReferenceError`."""
    report = verify_vault(vault_path, shape_path, **kw)
    bad = [c for c in report["checks"] if not c["ok"]]
    if bad:
        raise VaultReferenceError("; ".join(f"{c['check']}: {c['detail']}" for c in bad))
    return read_vault_bytes(vault_path)


def attach_vault(
    shape_path: str | os.PathLike[str],
    vault_raw: bytes,
    out: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    """Rewrite the profile artifact at ``shape_path`` (into ``out`` when given) so its manifest
    carries the ``vault`` reference of ``vault_raw``; the components are byte-identical and an
    earlier signature is dropped (the manifest changed). The vault must belong to the profile.
    Returns the reference."""
    parsed = parse_vault(vault_raw)
    read = read_artifact(shape_path, notice=False)
    manifest, components = read
    if parsed.profile_content_id != manifest.get("shape_content_id"):
        raise VaultInputError("the vault belongs to another profile")
    ref = reference_for(vault_raw)
    new_manifest = {k: v for k, v in manifest.items() if k != "content_hashes"}
    new_manifest["vault"] = ref
    write_artifact(out if out is not None else shape_path, new_manifest, components)
    return ref


# --- rotation ------------------------------------------------------------------------------------


def rekey(
    shape_path: str | os.PathLike[str],
    vault_path: str | os.PathLike[str],
    *,
    kek: bytes,
    new_kek: bytes,
    out_shape: str | os.PathLike[str],
    out_vault: str | os.PathLike[str],
    signing_key: bytes | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Re-encrypt a vault under a new data key and ``new_kek`` and write the profile that refers
    to it. The inputs are never changed; the profile's components are byte-identical; the signature
    is dropped unless ``signing_key`` signs the new file. Returns what was (or with ``dry_run``
    would be) written."""
    targets = {"shape": Path(out_shape), "vault": Path(out_vault)}
    inputs = {Path(shape_path).resolve(), Path(vault_path).resolve()}
    for label, t in targets.items():
        if t.resolve() in inputs:
            raise VaultInputError(
                f"the new {label} would overwrite an input: rekey never works in place"
            )
        if t.exists():
            raise VaultInputError(f"{t} exists and is not overwritten")
    if targets["shape"].resolve() == targets["vault"].resolve():
        raise VaultInputError("the new profile and the new vault need different paths")
    read = read_artifact(shape_path, notice=False)
    manifest, components = read
    signed = read.signature["status"] != "unsigned"
    raw = require_matching(vault_path, shape_path, kek=kek)
    opened: OpenedVault = open_vault(raw, kek)
    notices: list[str] = []
    if signed and signing_key is None:
        notices.append(
            "the signature is dropped: the manifest changed (it names the new vault); sign the "
            "new profile with `shape sign` or pass --key"
        )
    plan = {
        "dry_run": dry_run,
        "writes": [str(targets["vault"]), str(targets["shape"])],
        "columns": sorted(opened.columns),
        "signed": signing_key is not None,
        "notices": notices,
    }
    if dry_run:
        return plan
    new_raw = seal_vault(
        {n: (c.policy, c.payload) for n, c in opened.columns.items()},
        opened.profile_content_id,
        new_kek,
    )
    new_manifest = {k: v for k, v in manifest.items() if k not in ("content_hashes", "vault")}
    new_manifest["vault"] = reference_for(new_raw)
    write_file(targets["vault"], new_raw, overwrite=False)
    try:
        write_artifact(targets["shape"], new_manifest, components)
        if signing_key is not None:
            from shape.artifact.signing import sign_artifact

            sign_artifact(targets["shape"], signing_key)
    except BaseException:
        for t in targets.values():
            with contextlib.suppress(OSError):
                t.unlink()
        raise
    plan["vault_id"] = new_manifest["vault"]["vault_id"]
    plan["sha256"] = new_manifest["vault"]["sha256"]
    return plan


__all__ = [
    "GITIGNORE_LINE",
    "attach_vault",
    "VAULT_SUFFIX",
    "ensure_git_ignored",
    "reference_for",
    "rekey",
    "require_matching",
    "sha256_hex",
    "vault_reference",
    "verify_vault",
    "write_file",
]

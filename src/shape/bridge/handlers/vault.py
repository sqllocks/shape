"""Metadata-only vault management (bridge 1.4), using the existing vault operations."""

from __future__ import annotations

from typing import Any

from shape.bridge.context import Context
from shape.bridge.errors import writing
from shape.bridge.handlers.common import BOOL, INT, STR, STRS, arr, mapping, nullable, obj
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command


def cmd_status(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.vault.format import inspect_vault
    from shape.vault.ops import read_vault_bytes

    return {"ok": True, **inspect_vault(read_vault_bytes(args["vault"]))}


def cmd_verify(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.vault.kek import resolve_kek
    from shape.vault.ops import verify_vault

    if args.get("verify") == "-":
        raise BridgeError("input.invalid_value", "verify must name a public key file")
    public_key = None
    if args.get("verify"):
        from shape.artifact.signing import load_public_key

        public_key = load_public_key(args["verify"])
    return verify_vault(
        args["vault"],
        args["shape"],
        kek=resolve_kek(args["kek"]) if args.get("kek") else None,
        verify_key=public_key,
    )


def cmd_rewrap(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.vault.kek import resolve_kek
    from shape.vault.ops import ensure_git_ignored, rekey

    if args.get("key") == "-":
        raise BridgeError("input.invalid_value", "key must name a signing key file")
    old, new = resolve_kek(args["kek"]), resolve_kek(args["new_kek"])
    signing_key = None
    if args.get("key"):
        # The bridge owns stdin. Encrypted signing keys need an environment passphrase.
        from shape.artifact.keys import load_private_key, read_passphrase

        signing_key = load_private_key(
            args["key"], lambda: read_passphrase(env=args.get("passphrase_env"), use_stdin=False)
        )
    if not args.get("dry_run"):
        ensure_git_ignored([(args["out_vault"], "vault")])
    with writing():
        result = rekey(
            args["shape"],
            args["vault"],
            kek=old,
            new_kek=new,
            out_shape=args["out_shape"],
            out_vault=args["out_vault"],
            signing_key=signing_key,
            dry_run=bool(args.get("dry_run")),
        )
    return {"ok": True, **result}


def cmd_policy(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.vault.errors import VaultInputError
    from shape.vault.policy import load_policy

    try:
        policy = load_policy(args["policy"])
    except VaultInputError:
        # An invalid policy may contain arbitrary data values, not just metadata.
        raise BridgeError("input.invalid_value", "invalid vault policy") from None
    result: dict[str, Any] = {"ok": True, "policy": policy.to_dict()}
    if args.get("column"):
        result["effective"] = policy.policy_for(args["column"], args.get("classification"))
    elif args.get("classification"):
        raise BridgeError("input.invalid_value", "classification requires column")
    return result


_VAULT = Arg("string", "the encrypted vault file", True, path="read")
_SHAPE = Arg("string", "the profile artifact that references the vault", True, path="read")
_KEK = Arg("string", "key-encryption key reference; never a literal key", secret=True)
_COLUMN = obj({"column": STR, "policy": STR, "ciphertext_bytes": INT})
_STATUS = obj(
    {
        "ok": BOOL,
        "format": STR,
        "version": INT,
        "shape_version": nullable(STR),
        "min_shape_version": nullable(STR),
        "vault_id": STR,
        "profile_content_id": STR,
        "algorithm": STR,
        "kek_id": STR,
        "columns": arr(_COLUMN),
    }
)
_VERIFY = obj(
    {
        "ok": BOOL,
        "vault_id": nullable(STR),
        "columns": STRS,
        "checks": arr(obj({"check": STR, "ok": BOOL, "detail": STR})),
    }
)
_REWRAP = obj(
    {
        "ok": BOOL,
        "dry_run": BOOL,
        "writes": STRS,
        "columns": STRS,
        "signed": BOOL,
        "notices": STRS,
    },
    {"vault_id": STR, "sha256": STR},
)
_POLICY = obj(
    {
        "ok": BOOL,
        "policy": obj(
            {
                "format": STR,
                "version": INT,
                "shape_version": STR,
                "min_shape_version": STR,
                "default": STR,
                "columns": mapping(STR),
                "by_classification": mapping(STR),
            }
        ),
    },
    {"effective": STR},
)

COMMANDS = [
    Command(
        "vault_status",
        "Inspect a vault's metadata without opening its values.",
        {"vault": _VAULT},
        _STATUS,
        cmd_status,
        since="1.4",
        effects=("reads_files",),
    ),
    Command(
        "vault_verify",
        "Verify a vault's profile reference and optionally its key and signature.",
        {
            "vault": _VAULT,
            "shape": _SHAPE,
            "kek": _KEK,
            "verify": Arg("string", "the profile signature public key", path="read"),
        },
        _VERIFY,
        cmd_verify,
        since="1.4",
        effects=("reads_files",),
    ),
    Command(
        "vault_rewrap",
        "Rotate vault encryption keys and write a new profile and vault.",
        {
            "vault": _VAULT,
            "shape": _SHAPE,
            "kek": Arg("string", _KEK.description, True, secret=True),
            "new_kek": Arg("string", "new key-encryption key reference", True, secret=True),
            "out_shape": Arg("string", "new profile path; never overwrites", True, path="write"),
            "out_vault": Arg("string", "new vault path; never overwrites", True, path="write"),
            "key": Arg("string", "optional signing key file", secret=True, path="read"),
            "passphrase_env": Arg(
                "string", "environment variable holding the signing key passphrase", secret=True
            ),
            "dry_run": Arg("boolean", "validate and list writes without writing"),
        },
        _REWRAP,
        cmd_rewrap,
        since="1.4",
        effects=("reads_files", "writes_files"),
    ),
    Command(
        "vault_policy",
        "Read and resolve column policy without reading vault values.",
        {
            "policy": Arg("string", "the vault policy JSON file", True, path="read"),
            "column": Arg("string", "TABLE.COLUMN whose effective policy to resolve"),
            "classification": Arg("string", "classification used for the column fallback"),
        },
        _POLICY,
        cmd_policy,
        since="1.4",
        effects=("reads_files",),
    ),
]

"""Metadata-only vault management (bridge 1.4), using the existing vault operations."""

from __future__ import annotations

from functools import wraps
from typing import Any

from shape.bridge.context import Context
from shape.bridge.errors import writing
from shape.bridge.handlers.common import BOOL, INT, STR, STRS, arr, mapping, nullable, obj
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command, Handler


def _metadata_only(handler: Handler) -> Handler:
    """Untrusted vault headers cannot turn parser messages into data-return channels."""

    @wraps(handler)
    def guarded(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
        from shape.compat import UnsupportedVersionError
        from shape.vault.errors import VaultFormatError, VaultInputError

        try:
            return handler(args, ctx)
        except UnsupportedVersionError as exc:
            # The compatibility reader validates release numbers before constructing this.
            raise BridgeError(
                "input.unsupported_format_version", str(exc), "upgrade Shape to read it"
            ) from None
        except VaultFormatError:
            raise BridgeError("input.invalid_value", "invalid vault metadata") from None
        except VaultInputError as exc:
            # read_vault_bytes wraps the OS error; do not publish locale/OS strerror text.
            if isinstance(exc.__context__, FileNotFoundError):
                raise BridgeError(
                    "input.not_found", f"vault file not found: {args['vault']}"
                ) from None
            raise

    return guarded


@_metadata_only
def cmd_status(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.compat import parse_release
    from shape.vault.format import inspect_vault
    from shape.vault.ops import read_vault_bytes

    info = inspect_vault(read_vault_bytes(args["vault"]))
    for field in ("shape_version", "min_shape_version"):
        if info[field] is not None and parse_release(info[field]) is None:
            raise BridgeError("input.invalid_value", "invalid vault release metadata")
    return {"ok": True, **info}


@_metadata_only
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


@_metadata_only
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


@_metadata_only
def cmd_policy(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.vault.errors import VaultInputError
    from shape.vault.policy import load_policy

    if any(name in args for name in ("vault", "kek", "new_policy")):
        return _write_policy(args)

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


def _write_policy(args: dict[str, Any]) -> dict[str, Any]:
    import hashlib
    import re

    from shape.artifact.canonical import canonical_json
    from shape.compat import parse_release
    from shape.vault.errors import VaultError
    from shape.vault.format import open_vault, parse_vault, seal_vault
    from shape.vault.kek import resolve_kek
    from shape.vault.ops import read_vault_bytes, write_file

    if (
        not args.get("vault")
        or not args.get("column")
        or "policy" in args
        or args.get("new_policy") not in ("none", "categories", "extremes", "all")
        or "classification" in args
        or not re.fullmatch(r"env://[A-Z_][A-Z0-9_]{0,127}", args.get("kek", ""))
    ):
        raise BridgeError("usage.invalid_argument", "invalid vault policy mutation")
    try:
        raw = read_vault_bytes(args["vault"])
        parsed = parse_vault(raw)
        for field in ("shape_version", "min_shape_version"):
            if parsed.doc.get(field) is not None and parse_release(parsed.doc[field]) is None:
                raise BridgeError("input.invalid_value", "invalid vault release metadata")
        # Mutation is stricter than legacy readers: unknown metadata is never discarded.
        if set(parsed.doc) - {
            "format",
            "version",
            "shape_version",
            "min_shape_version",
            "vault_id",
            "profile_content_id",
            "algorithm",
            "kek_id",
            "wrapped_key",
            "columns",
        }:
            raise BridgeError("input.invalid_value", "unknown vault metadata")
        kek = resolve_kek(args["kek"])
        opened = open_vault(raw, kek)
        if args["column"] not in opened.columns:
            raise BridgeError("input.invalid_value", "unknown vault column")
        before = {"columns": {name: col.policy for name, col in sorted(opened.columns.items())}}
        columns = {name: (col.policy, col.payload) for name, col in opened.columns.items()}
        columns[args["column"]] = (args["new_policy"], opened.columns[args["column"]].payload)
        updated = seal_vault(columns, opened.profile_content_id, kek)
        policy = {"columns": {name: entry[0] for name, entry in sorted(columns.items())}}
        result = {
            "ok": True,
            "before_policy": before,
            "policy": policy,
            "policy_digest": hashlib.sha256(canonical_json(policy)).hexdigest(),
            "key_reference": args["kek"],
        }
        with writing():
            write_file(args["vault"], updated)
        return result
    except VaultError:
        raise BridgeError("input.invalid_value", "vault policy mutation failed") from None


_VAULT = Arg("string", "the encrypted vault file", True, path="read")
_SHAPE = Arg("string", "the profile artifact file that references the vault", True, path="read")
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
        "Read policy, or atomically change an authenticated vault column policy (1.6).",
        {
            "policy": Arg("string", "the vault policy JSON file", True, path="read"),
            "new_policy": Arg(
                "string", "new column policy for mutation", since="1.6", alias_for="policy"
            ),
            "vault": Arg("string", "vault file to update atomically", since="1.6", path="write"),
            "kek": Arg(
                "string", "environment key reference for mutation", since="1.6", secret=True
            ),
            "column": Arg("string", "TABLE.COLUMN whose effective policy to resolve"),
            "classification": Arg("string", "classification used for the column fallback"),
        },
        {
            "oneOf": [
                _POLICY,
                obj(
                    {
                        "ok": BOOL,
                        "before_policy": obj({"columns": mapping(STR)}),
                        "policy": obj({"columns": mapping(STR)}),
                        "policy_digest": STR,
                        "key_reference": STR,
                    }
                ),
            ]
        },
        cmd_policy,
        since="1.4",
        effects=("reads_files", "writes_files"),
    ),
]

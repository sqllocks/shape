"""The file-footer fingerprint: a signed statement, inside a Parquet file or a Delta table, that its
data is synthetic and which run made it (W5-10, ``docs/FINGERPRINT.md``).

The document is JSON::

    {"format": "shape-fingerprint", "version": 1, "synthetic": true, "table": ..., "table_id": ...,
     "dataset_id": ... or null, "profile_content_id": ... or null, "reproducibility": {...},
     "key_id": ... or null, "signature": ... or null}

``table_id`` is :func:`shape.repro.dataset_id` of this table alone (so it changes with any value);
``dataset_id`` is the id of the whole run, from the run manifest. The signature is Ed25519
(extra ``[sign]``) over the canonical form (:func:`shape.artifact.canonical.canonical_json`) of the
document with ``signature`` set to null, under its own domain tag, so it is valid for nothing else.

It is stored as the Parquet footer key-value metadata key ``shape.fingerprint`` and as the Delta
table property ``shape.fingerprint``. ``deltalake`` refuses to set a table property it does not
know, so the property is added by a metadata commit written to the Delta log directly (a
``metaData`` action with the table's own metadata and one more configuration entry); every reader
keeps the entry and ignores what it does not know.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]

from shape.artifact.canonical import canonical_json

FORMAT = "shape-fingerprint"
VERSION = 1
KEY = "shape.fingerprint"
DOMAIN = b"shape-fingerprint-signature-v1\n"
_SIGN_HINT = (
    "fingerprint signing needs the 'cryptography' package: pip install 'sqllocks-shape[sign]'"
)
_COMPRESSION = {"UNCOMPRESSED": "none"}


class FingerprintError(ValueError):
    """The input is not something that can carry or be checked against a fingerprint (exit 2)."""


class NoFingerprintError(FingerprintError):
    """The file or table has no ``shape.fingerprint``."""


class FingerprintVersionError(FingerprintError):
    """The fingerprint is a newer version than this Shape reads."""


def _require_crypto() -> None:
    try:
        import cryptography  # noqa: F401
    except ImportError as e:
        raise ImportError(_SIGN_HINT) from e


def key_id(public_key: bytes) -> str:
    from shape.artifact.signing import key_id as _key_id

    return _key_id(public_key)


def _message(doc: dict[str, Any], domain: bytes = DOMAIN) -> bytes:
    return domain + canonical_json({**doc, "signature": None})


def table_id(name: str, table: pa.Table) -> str:
    """The id of ``table`` alone, as the run's ``dataset_id`` would be for a one-table run."""
    from shape.repro import dataset_id

    return dataset_id({name: table})


def build(
    name: str,
    table: pa.Table,
    *,
    dataset_id: str | None,
    reproducibility: dict[str, Any],
    profile_content_id: str | None = None,
    private_key: bytes | None = None,
) -> dict[str, Any]:
    """The fingerprint of ``table`` (called ``name``), signed when ``private_key`` is given."""
    if private_key is not None:
        _require_crypto()
    doc: dict[str, Any] = {
        "format": FORMAT,
        "version": VERSION,
        "synthetic": True,
        "table": name,
        "table_id": table_id(name, table),
        "dataset_id": dataset_id,
        "profile_content_id": profile_content_id,
        "reproducibility": dict(reproducibility),
        "key_id": None,
        "signature": None,
    }
    try:
        canonical_json(doc)
    except TypeError as e:
        raise FingerprintError(f"the reproducibility tuple cannot be signed: {e}") from e
    if private_key is not None:
        sign_document(doc, private_key)
    return doc


def sign_document(doc: dict[str, Any], private_key: bytes, domain: bytes = DOMAIN) -> None:
    """Set ``key_id`` and ``signature`` of ``doc``: Ed25519 over ``domain`` and the canonical form
    of ``doc`` with ``signature`` null. (The share-bundle attestation signs the same way under its
    own ``domain``.)"""
    _require_crypto()
    from shape.artifact.signing import public_key_of
    from shape.security.crypto import sign_ed25519

    if len(private_key) != 32:
        raise ValueError("private key must be 32 bytes")
    doc["key_id"] = key_id(public_key_of(private_key))
    doc["signature"] = base64.b64encode(sign_ed25519(_message(doc, domain), private_key)).decode()


def signature_state(doc: dict[str, Any], public_key: bytes | None, domain: bytes = DOMAIN) -> str:
    """``valid``, ``unchecked`` (signed, no key given), ``unsigned`` or ``invalid`` (a signature
    that does not match, or another key's)."""
    signature = doc.get("signature")
    if signature is None:
        return "unsigned"
    if public_key is None:
        return "unchecked"
    if len(public_key) != 32:
        raise ValueError("public key must be 32 bytes")
    if doc.get("key_id") != key_id(public_key):
        return "invalid"
    _require_crypto()
    from shape.errors import ShapeSecurityError
    from shape.security.crypto import verify_ed25519

    try:
        verify_ed25519(
            _message(doc, domain), base64.b64decode(str(signature), validate=True), public_key
        )
    except (ShapeSecurityError, binascii.Error, ValueError, TypeError):
        return "invalid"
    return "valid"


def parse(text: str | bytes) -> dict[str, Any]:
    """The fingerprint document in ``text``; a newer version or another format is refused."""
    try:
        doc = json.loads(text)
    except ValueError as e:
        raise FingerprintError(f"the fingerprint is not valid JSON: {e}") from e
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise FingerprintError(f"not a fingerprint (format must be {FORMAT!r})")
    version = doc.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise FingerprintError(f"the fingerprint's version {version!r} is not a positive integer")
    if version > VERSION:
        raise FingerprintVersionError(
            f"the fingerprint is version {version}, written by a newer Shape; this Shape reads "
            f"versions up to {VERSION}. Upgrade Shape to read it"
        )
    for key in ("table", "table_id"):
        if not isinstance(doc.get(key), str):
            raise FingerprintError(f"the fingerprint has no {key}")
    return doc


# ---- where it lives -------------------------------------------------------------------------


def kind_of(path: str | os.PathLike[str]) -> str:
    """``parquet`` for a Parquet file, ``delta`` for a Delta table directory."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"file not found: {path}")
    if p.is_dir():
        if (p / "_delta_log").is_dir():
            return "delta"
        raise FingerprintError(f"{path} is a directory but not a Delta table (no _delta_log)")
    with p.open("rb") as fh:
        head = fh.read(4)
    if head != b"PAR1":
        raise FingerprintError(f"{path} is not a Parquet file or a Delta table directory")
    return "parquet"


def _deltalake() -> Any:
    try:
        import deltalake
    except ImportError as exc:
        raise ImportError(
            "Delta fingerprints need deltalake: pip install 'sqllocks-shape[delta]'"
        ) from exc
    return deltalake


def read_text(path: str | os.PathLike[str]) -> str | None:
    """The stored fingerprint text of a Parquet file or Delta table, or ``None`` if it has none."""
    if kind_of(path) == "parquet":
        meta = pq.read_metadata(path).metadata or {}
        raw = meta.get(KEY.encode())
        return None if raw is None else raw.decode("utf-8")
    config = _deltalake().DeltaTable(str(path)).metadata().configuration
    value = config.get(KEY)
    return None if value is None else str(value)


def read_fingerprint(path: str | os.PathLike[str]) -> dict[str, Any]:
    text = read_text(path)
    if text is None:
        raise NoFingerprintError(f"{path} has no {KEY}")
    return parse(text)


def read_data(path: str | os.PathLike[str]) -> pa.Table:
    if kind_of(path) == "parquet":
        table: pa.Table = pq.read_table(path)
        return table.replace_schema_metadata(None)
    table = _deltalake().DeltaTable(str(path)).to_pyarrow_table()
    return table.replace_schema_metadata(None)


def _atomic_parquet(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".fingerprint.tmp")
    try:
        # The source is closed before the replace: Windows refuses to replace an open file.
        with pq.ParquetFile(str(path)) as source:
            meta = dict(source.schema_arrow.metadata or {})
            meta[KEY.encode()] = text.encode("utf-8")
            compression = "snappy"
            if source.metadata.num_row_groups and source.metadata.num_columns:
                name = str(source.metadata.row_group(0).column(0).compression)
                compression = _COMPRESSION.get(name, name.lower())
            schema = source.schema_arrow.with_metadata(meta)
            with pq.ParquetWriter(str(tmp), schema, compression=compression) as writer:
                for i in range(source.metadata.num_row_groups):
                    writer.write_table(source.read_row_group(i))
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def embed_text(path: str | os.PathLike[str], text: str) -> None:
    """Store ``text`` as the fingerprint of the Parquet file (rewritten atomically, row groups and
    compression kept) or Delta table (one new commit) at ``path``."""
    p = Path(path)
    if kind_of(p) == "parquet":
        _atomic_parquet(p, text)
        return
    set_delta_property(p, KEY, text)


# ---- Delta ----------------------------------------------------------------------------------

_COMMIT = re.compile(r"^(\d{20})\.json$")


def _latest_metadata(log: Path, table: Any) -> dict[str, Any]:
    """The table's current ``metaData`` action: the newest one in the JSON commits, or built from
    the table (a log that starts at a checkpoint)."""
    commits = sorted((f for f in log.iterdir() if _COMMIT.match(f.name)), reverse=True)
    for commit in commits:
        found: dict[str, Any] | None = None
        with commit.open(encoding="utf-8") as fh:
            for line in fh:
                if '"metaData"' in line:
                    found = json.loads(line)["metaData"]
        if found is not None:
            return found
    meta = table.metadata()
    return {
        "id": meta.id,
        "name": meta.name,
        "description": meta.description,
        "format": {"provider": "parquet", "options": {}},
        "schemaString": table.schema().to_json(),
        "partitionColumns": list(meta.partition_columns),
        "createdTime": meta.created_time,
        "configuration": dict(meta.configuration),
    }


def set_delta_property(path: str | os.PathLike[str], key: str, value: str) -> int:
    """Add a commit that sets table property ``key``; returns the new version. The commit file is
    created exclusively, so a writer that committed meanwhile makes this fail instead of being
    overwritten (it is retried against the new version)."""
    p = Path(path)
    log = p / "_delta_log"
    dl = _deltalake()
    for _ in range(5):
        table = dl.DeltaTable(str(p))
        meta = _latest_metadata(log, table)
        meta["configuration"] = {**(meta.get("configuration") or {}), key: value}
        version = table.version() + 1
        target = log / f"{version:020d}.json"
        lines = [
            {"metaData": meta},
            {
                "commitInfo": {
                    "timestamp": int(time.time() * 1000),
                    "operation": "SET TBLPROPERTIES",
                    "operationParameters": {"properties": json.dumps({key: "<set>"})},
                    "engineInfo": "sqllocks-shape",
                }
            },
        ]
        try:
            with target.open("x", encoding="utf-8") as fh:
                fh.write("\n".join(json.dumps(x, separators=(",", ":")) for x in lines) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        except FileExistsError:
            continue
        return int(version)
    raise OSError(f"{path}: the Delta table keeps changing; try again")


# ---- verify ---------------------------------------------------------------------------------


class Outcome:
    """The result of :func:`verify`: ``ok`` is false for a digest or signature mismatch."""

    def __init__(self, doc: dict[str, Any], digest_ok: bool, signature: str, public: bool) -> None:
        self.doc = doc
        self.digest_ok = digest_ok
        self.signature = signature
        self.public_key_given = public
        # With a key the signature must be valid; without one a signed fingerprint is unchecked.
        sig_ok = signature == "valid" if public else signature != "invalid"
        self.ok = digest_ok and sig_ok

    def messages(self) -> list[str]:
        out = [
            "table_id matches the data" if self.digest_ok else "table_id does NOT match the data",
        ]
        out.append(
            {
                "valid": "signature valid",
                "unchecked": "signed; signature not checked (give --public-key)",
                "unsigned": "not signed" + (" (a key was given)" if self.public_key_given else ""),
                "invalid": "signature does NOT match (altered, or another key)",
            }[self.signature]
        )
        return out


def verify(path: str | os.PathLike[str], public_key: bytes | None = None) -> Outcome:
    """Recompute ``table_id`` from the data at ``path`` and check it and the signature."""
    doc = read_fingerprint(path)
    table = read_data(path)
    digest_ok = table_id(str(doc["table"]), table) == doc["table_id"]
    return Outcome(doc, digest_ok, signature_state(doc, public_key), public_key is not None)


def for_sink(
    name: str, table: pa.Table, options: Any, private_key: bytes | None = None
) -> dict[str, Any]:
    """The fingerprint a sink writes for ``table``. ``options`` is ``True`` or the run's context:
    ``dataset_id``, ``reproducibility`` and ``profile_content_id``."""
    context = options if isinstance(options, dict) else {}
    return build(
        name,
        table,
        dataset_id=context.get("dataset_id"),
        reproducibility=dict(context.get("reproducibility") or {}),
        profile_content_id=context.get("profile_content_id"),
        private_key=private_key,
    )


def dump(doc: dict[str, Any]) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"))

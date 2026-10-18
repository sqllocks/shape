"""The safe-to-share bundle: generated data, the run manifest when there is one, and a signed
attestation that named checks passed (W5-10, ``docs/SHARE_BUNDLE.md``).

``create`` runs two checks of the generated tables against the source they were made from:

* ``memorization``: :class:`shape.quality.memorization.MemorizationGate` with the given
  classifications (no generated row reproduces a source row on the CONFIDENTIAL-and-above columns);
* ``top_values``: no value among the source's ``top_k`` most frequent values (ties broken by value)
  of any column classified CONFIDENTIAL or above appears in the generated column.

If either fails nothing is written. The attestation (``attestation.json``) holds the dataset id,
each check with its parameters, its pass result and counts, the Shape version and an Ed25519
signature when a key is given. It carries counts and names, never a value from the source. It is
evidence that the listed checks passed on this data, not a privacy guarantee.

``verify`` recomputes the dataset id from the bundled data and checks the signature and that every
check passed. A bundle is read member by member and its member names are checked first: nothing
outside ``attestation.json``, ``manifest.json`` and ``data/<file>`` is accepted. Every member is
then bounded (count, size, expansion) before it is read and again while it is inflated (#684).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.fingerprint import sign_document, signature_state
from shape.io.excel import MAX_EXPANSION, MAX_PLAIN_BYTES

FORMAT = "shape-share-attestation"
VERSION = 1
DOMAIN = b"shape-share-attestation-signature-v1\n"
ATTESTATION = "attestation.json"
MANIFEST = "manifest.json"
DATA_PREFIX = "data/"
DEFAULT_TOP_K = 20
MIN_LEVEL = "CONFIDENTIAL"
_DATA_SUFFIXES = (".csv", ".parquet", ".jsonl")
_MAX_JSON_BYTES = 8 * 1024 * 1024
# A bundle comes from someone else, so every member is bounded before it is read (#684). The
# expansion rule is the Excel reader's zip-bomb guard (#282, #563): a member, or the whole bundle,
# that inflates more than MAX_EXPANSION times is refused once it is also larger than
# MAX_PLAIN_BYTES. The sizes are generous for generated data (``verify`` loads every table into
# memory anyway); the member count is the ``.shape`` container reader's.
MAX_MEMBERS = 10_000
MAX_DATA_MEMBER_BYTES = 2 << 30
MAX_DATA_TOTAL_BYTES = 8 << 30
_ZIP_TIME = (1980, 1, 1, 0, 0, 0)
_MANIFEST_FORMAT = "shape-run-manifest"


class BundleError(ValueError):
    """The input or the bundle is malformed (exit 2)."""


def load_classifications(path: str | os.PathLike[str]) -> dict[str, str]:
    """``{"table.column": level}`` from a JSON file (the ``classifications`` of ``shape verify``,
    bare or under a ``"classifications"`` key). Levels are checked."""
    from shape.privacy.classification import DEFAULT_TAXONOMY

    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise BundleError(f"{path} is not valid JSON: {e}") from e
    if isinstance(doc, dict) and set(doc) == {"classifications"}:
        doc = doc["classifications"]
    if not isinstance(doc, dict) or not all(
        isinstance(k, str) and "." in k and isinstance(v, str) for k, v in doc.items()
    ):
        raise BundleError(f'{path} must map "table.column" to a classification level')
    try:
        return {k: DEFAULT_TAXONOMY.canonical(v) for k, v in doc.items()}
    except ValueError as e:
        raise BundleError(f"{path}: {e}") from e


def _restricted(classes: dict[str, str]) -> set[str]:
    from shape.privacy.classification import DEFAULT_TAXONOMY

    return {k for k, v in classes.items() if DEFAULT_TAXONOMY.at_least(v, MIN_LEVEL)}


def top_values_check(
    generated: dict[str, pa.Table],
    source: dict[str, pa.Table],
    classes: dict[str, str],
    top_k: int,
) -> dict[str, Any]:
    """The ``top_values`` check: per restricted column, how many of the source's ``top_k`` most
    frequent values appear in the generated column (a count; no value is returned)."""
    from shape.quality.memorization import _comparable

    columns: list[dict[str, Any]] = []
    for key in sorted(_restricted(classes)):
        table, column = key.split(".", 1)
        gen, src = generated.get(table), source.get(table)
        if gen is None or src is None or column not in gen.column_names:
            continue
        if column not in src.column_names:
            continue
        g, s = _comparable(gen.column(column), src.column(column))
        counted = pc.value_counts(s.combine_chunks().drop_null())
        if len(counted) == 0:
            continue
        frame = pa.table({"v": counted.field("values"), "c": counted.field("counts")})
        order = pc.sort_indices(frame, sort_keys=[("c", "descending"), ("v", "ascending")])
        top = frame.take(order.slice(0, top_k)).column("v").combine_chunks()
        hit = pc.is_in(top, value_set=g.combine_chunks().drop_null())
        columns.append(
            {
                "table": table,
                "column": column,
                "values_checked": len(top),
                "matches": int(pc.sum(hit.cast(pa.int64())).as_py() or 0),
            }
        )
    passed = all(c["matches"] == 0 for c in columns)
    return {
        "name": "top_values",
        "parameters": {"top_k": top_k, "min_level": MIN_LEVEL},
        "passed": passed,
        "counts": {"columns": columns},
    }


def memorization_check(
    generated: dict[str, pa.Table], source: dict[str, pa.Table], classes: dict[str, str]
) -> dict[str, Any]:
    """Measure repeated source rows in generated data for the bundle attestation."""
    from shape.quality.gates import ValidationContext
    from shape.quality.memorization import MemorizationGate

    result = MemorizationGate().check(
        ValidationContext(
            tables=generated,
            source_tables=source,
            config={"classifications": classes, "memorization": {"fail_at": MIN_LEVEL}},
        )
    )
    detail = result.details["tables"]
    tables = {
        name: {
            "rows": int(d["rows"]),
            "source_rows": int(d["source_rows"]),
            "reproduced_rows": int(d["reproduced_rows"]),
            "restricted_columns": len(d["columns"]) if d["restricted"] else 0,
        }
        for name, d in sorted(detail.items())
    }
    return {
        "name": "memorization",
        "parameters": {"fail_at": MIN_LEVEL},
        "passed": bool(result.passed),
        "counts": {
            "tables_compared": len(tables),
            "tables_not_compared": len(generated) - len(tables),
            "tables": tables,
        },
    }


def _manifest_file(data_dir: Path, files: list[Path]) -> Path | None:
    found = []
    for fp in sorted(data_dir.glob("*manifest.json")):
        try:
            doc = json.loads(fp.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(doc, dict) and doc.get("format") == _MANIFEST_FORMAT:
            found.append(fp)
    if len(found) > 1:
        raise BundleError(f"{data_dir} holds {len(found)} run manifests; keep the one of this run")
    return found[0] if found else None


@dataclass
class CreateResult:
    """Paths and attestation returned after creating a share bundle."""

    ok: bool
    attestation: dict[str, Any]
    problems: list[str] = field(default_factory=list)


def create(
    data_dir: str | os.PathLike[str],
    source_dir: str | os.PathLike[str],
    classifications: str | os.PathLike[str],
    out: str | os.PathLike[str],
    *,
    private_key: bytes | None = None,
    top_k: int = DEFAULT_TOP_K,
) -> CreateResult:
    """Run the checks and, when both pass, write the bundle to ``out``."""
    from shape import __version__
    from shape.quality.verify import data_files, load_tables
    from shape.repro import dataset_id

    if top_k < 1:
        raise BundleError("--top-k must be at least 1")
    if private_key is not None:
        from shape.fingerprint import _require_crypto

        _require_crypto()
    classes = load_classifications(classifications)
    data_path = Path(data_dir)
    if not data_path.is_dir():
        raise BundleError(f"{data_dir} is not a directory")
    generated = load_tables(data_path)
    source = load_tables(Path(source_dir))
    if not generated:
        raise BundleError(f"{data_dir} holds no csv, parquet or jsonl table")
    covered = [k for k in _restricted(classes) if k.split(".", 1)[0] in generated]
    if not covered:
        raise BundleError(
            "the classifications mark no column of a generated table CONFIDENTIAL or above, so "
            "the checks would pass without testing anything"
        )
    checks = [
        memorization_check(generated, source, classes),
        top_values_check(generated, source, classes, top_k),
    ]
    if checks[0]["counts"]["tables_compared"] == 0:
        raise BundleError("no generated table has a source table of the same name to compare with")
    files = data_files(data_path)
    manifest = _manifest_file(data_path, files)
    attestation: dict[str, Any] = {
        "format": FORMAT,
        "version": VERSION,
        "dataset_id": dataset_id(generated),
        "shape_version": __version__,
        "tables": {n: {"rows": t.num_rows} for n, t in sorted(generated.items())},
        "manifest_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest() if manifest else None,
        "checks": checks,
        "key_id": None,
        "signature": None,
    }
    if not all(c["passed"] for c in checks):
        return CreateResult(False, attestation, _problems(checks))
    if private_key is not None:
        sign_document(attestation, private_key, DOMAIN)
    members: dict[str, bytes] = {ATTESTATION: _dump(attestation)}
    if manifest is not None:
        members[MANIFEST] = manifest.read_bytes()
    for fp in files:
        members[DATA_PREFIX + fp.name] = fp.read_bytes()
    _write_zip(Path(out), members)
    return CreateResult(True, attestation)


def _problems(checks: list[dict[str, Any]]) -> list[str]:
    """What failed, as counts and names only."""
    out: list[str] = []
    for check in checks:
        if check["passed"]:
            continue
        if check["name"] == "memorization":
            for table, d in check["counts"]["tables"].items():
                if d["reproduced_rows"]:
                    out.append(
                        f"memorization: {table}: {d['reproduced_rows']} of {d['rows']} generated "
                        "rows reproduce a source row"
                    )
        else:
            for c in check["counts"]["columns"]:
                if c["matches"]:
                    out.append(
                        f"top_values: {c['table']}.{c['column']}: {c['matches']} of the "
                        f"{c['values_checked']} most frequent source values appear in the "
                        "generated column"
                    )
    return out or ["a check failed"]


def _dump(doc: dict[str, Any]) -> bytes:
    return (json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _write_zip(out: Path, members: dict[str, bytes]) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=out.parent, suffix=".tmp")
    os.close(fd)
    try:
        with zipfile.ZipFile(tmp_name, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in sorted(members):
                info = zipfile.ZipInfo(name, date_time=_ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                zf.writestr(info, members[name])
        os.replace(tmp_name, out)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


# ---- verify ---------------------------------------------------------------------------------

_SEGMENT = re.compile(r"^[^/\\:\x00-\x1f]+$")


def _check_names(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """The members of ``zf`` after every name is checked; nothing has been read yet."""
    seen: set[str] = set()
    members: list[zipfile.ZipInfo] = []
    for info in zf.infolist():
        name = info.filename
        if "\\" in info.orig_filename:  # Windows' zipfile turns it into "/" in ``filename``
            raise BundleError(f"the bundle holds a path outside its root: {info.orig_filename!r}")
        if name.endswith("/"):
            raise BundleError(f"the bundle holds a directory entry {name!r}")
        parts = name.split("/")
        if (
            name.startswith(("/", "\\"))
            or "\\" in name
            or any(p in ("", ".", "..") for p in parts)
            or (len(parts[0]) >= 2 and parts[0][1] == ":")
            or not all(_SEGMENT.match(p) for p in parts)
        ):
            raise BundleError(f"the bundle holds a path outside its root: {name!r}")
        if stat.S_ISLNK(info.external_attr >> 16):
            raise BundleError(f"the bundle holds a symbolic link: {name!r}")
        allowed = name in (ATTESTATION, MANIFEST) or (
            len(parts) == 2 and parts[0] == "data" and name.endswith(_DATA_SUFFIXES)
        )
        if not allowed:
            raise BundleError(f"the bundle holds an unexpected member: {name!r}")
        if name in seen:
            raise BundleError(f"the bundle lists {name!r} twice")
        seen.add(name)
        members.append(info)
    if ATTESTATION not in seen:
        raise BundleError(f"the bundle has no {ATTESTATION}")
    return members


def _expands(plain: int, packed: int) -> bool:
    return plain > MAX_PLAIN_BYTES and plain > MAX_EXPANSION * max(packed, 1)


def _check_sizes(members: list[zipfile.ZipInfo]) -> None:
    """Refuse a bundle whose declared sizes break a limit; nothing has been read yet. The sizes
    in the zip directory can lie, so :func:`_read_json` and :func:`_stream_member` count again."""
    if len(members) > MAX_MEMBERS:
        raise BundleError(f"the bundle holds more than {MAX_MEMBERS:,} members")
    plain = packed = 0
    for info in members:
        name = info.filename
        if name in (ATTESTATION, MANIFEST):
            if info.file_size > _MAX_JSON_BYTES:
                raise BundleError(f"{name} is larger than the limit of {_MAX_JSON_BYTES:,} bytes")
            continue
        if info.file_size > MAX_DATA_MEMBER_BYTES:
            raise BundleError(
                f"{name!r} is larger than the limit of {MAX_DATA_MEMBER_BYTES:,} bytes per file"
            )
        if _expands(info.file_size, info.compress_size):
            raise BundleError(
                f"{name!r} inflates more than {MAX_EXPANSION} times past {MAX_PLAIN_BYTES:,} "
                "bytes, which is not generated data"
            )
        plain += info.file_size
        packed += info.compress_size
    if plain > MAX_DATA_TOTAL_BYTES:
        raise BundleError(
            f"the bundled data is larger than the limit of {MAX_DATA_TOTAL_BYTES:,} bytes in total"
        )
    if _expands(plain, packed):
        raise BundleError(
            f"the bundled data inflates more than {MAX_EXPANSION} times past {MAX_PLAIN_BYTES:,} "
            "bytes in total, which is not generated data"
        )


def _open_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> Any:
    try:
        return zf.open(info)
    except (zipfile.BadZipFile, NotImplementedError, OSError, ValueError) as e:
        raise BundleError(f"{info.filename!r} cannot be read: {e}") from e


def _read_json(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """The member's bytes, never more than ``_MAX_JSON_BYTES`` whatever its header says."""
    with _open_member(zf, info) as src:
        try:
            raw = bytes(src.read(_MAX_JSON_BYTES + 1))
        except (zipfile.BadZipFile, EOFError, OSError, ValueError) as e:
            raise BundleError(f"{info.filename!r} cannot be read: {e}") from e
    if len(raw) > _MAX_JSON_BYTES:
        raise BundleError(f"{info.filename} is larger than the limit of {_MAX_JSON_BYTES:,} bytes")
    return raw


def _stream_member(src: Any, dst: Any, info: zipfile.ZipInfo, written: int) -> int:
    """Copy ``src`` to ``dst`` in chunks, counting the bytes actually written: more than the
    header declared, more than a limit or more than the expansion rule allows stops the copy.
    Returns the running total over all data members."""
    name = info.filename
    size = 0
    while True:
        try:
            chunk = src.read(1 << 20)
        except (zipfile.BadZipFile, EOFError, OSError, ValueError) as e:
            raise BundleError(f"{name!r} cannot be read: {e}") from e
        if not chunk:
            return written
        size += len(chunk)
        written += len(chunk)
        if size > info.file_size:
            raise BundleError(f"{name!r} inflates past the size its header declares")
        if size > MAX_DATA_MEMBER_BYTES:
            raise BundleError(
                f"{name!r} is larger than the limit of {MAX_DATA_MEMBER_BYTES:,} bytes per file"
            )
        if written > MAX_DATA_TOTAL_BYTES:
            raise BundleError(
                f"the bundled data is larger than the limit of {MAX_DATA_TOTAL_BYTES:,} bytes "
                "in total"
            )
        if _expands(size, info.compress_size):
            raise BundleError(
                f"{name!r} inflates more than {MAX_EXPANSION} times past {MAX_PLAIN_BYTES:,} "
                "bytes, which is not generated data"
            )
        dst.write(chunk)


@dataclass
class VerifyResult:
    """Verification status, manifest and attestation read from a share bundle."""

    ok: bool
    lines: list[str]
    attestation: dict[str, Any]


def verify(bundle: str | os.PathLike[str], public_key: bytes | None = None) -> VerifyResult:
    """Check ``bundle``: the dataset id of its data, the manifest hash, the signature and that
    every recorded check passed. A malformed bundle raises :class:`BundleError`."""
    from shape.quality.verify import load_tables
    from shape.repro import dataset_id

    try:
        zf = zipfile.ZipFile(bundle)
    except zipfile.BadZipFile as e:
        raise BundleError(f"{bundle} is not a zip file: {e}") from e
    with zf:
        members = _check_names(zf)
        _check_sizes(members)
        att = _parse_attestation(_read_json(zf, zf.getinfo(ATTESTATION)))
        with tempfile.TemporaryDirectory(prefix="shape-bundle-") as tmp:
            data = Path(tmp)
            manifest_bytes: bytes | None = None
            written = 0
            for info in members:
                if info.filename == MANIFEST:
                    manifest_bytes = _read_json(zf, info)
                elif info.filename.startswith(DATA_PREFIX):
                    with (
                        _open_member(zf, info) as src,
                        (data / info.filename[len(DATA_PREFIX) :]).open("wb") as dst,
                    ):
                        written = _stream_member(src, dst, info, written)
            try:
                tables = load_tables(data)
            except (ValueError, OSError, pa.ArrowException) as e:
                raise BundleError(f"the bundled data cannot be read: {e}") from e
    lines: list[str] = []
    ok = True

    def note(good: bool, text: str) -> None:
        nonlocal ok
        ok = ok and good
        lines.append(("ok: " if good else "FAILED: ") + text)

    computed = dataset_id(tables)
    if computed == att["dataset_id"]:
        note(True, "dataset_id matches the bundled data")
    else:
        note(False, "dataset_id does NOT match the bundled data (a file was changed or removed)")
    have = hashlib.sha256(manifest_bytes).hexdigest() if manifest_bytes is not None else None
    note(have == att.get("manifest_sha256"), "the run manifest matches the attestation")
    checks = att.get("checks")
    if not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks):
        raise BundleError("the attestation's checks are malformed")
    names = [c.get("name") for c in checks]
    for required in ("memorization", "top_values"):
        note(required in names, f"the attestation records the {required} check")
    for c in checks:
        note(c.get("passed") is True, f"check {c.get('name')} passed")
    state = signature_state(att, public_key, DOMAIN)
    if public_key is None:
        lines.append(
            {
                "unsigned": "note: not signed",
                "unchecked": "note: signed; signature not checked (give --public-key)",
            }[state]
        )
    else:
        note(state == "valid", {"valid": "signature valid"}.get(state, f"signature: {state}"))
    return VerifyResult(ok, lines, att)


def _parse_attestation(raw: bytes) -> dict[str, Any]:
    try:
        att = json.loads(raw)
    except ValueError as e:
        raise BundleError(f"{ATTESTATION} is not valid JSON: {e}") from e
    if not isinstance(att, dict) or att.get("format") != FORMAT:
        raise BundleError(f"{ATTESTATION} is not a share attestation (format {FORMAT!r})")
    version = att.get("version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise BundleError(f"the attestation's version {version!r} is not a positive integer")
    if version > VERSION:
        raise BundleError(
            f"the attestation is version {version}, written by a newer Shape; this Shape reads "
            f"versions up to {VERSION}. Upgrade Shape to read it"
        )
    if not isinstance(att.get("dataset_id"), str):
        raise BundleError("the attestation has no dataset_id")
    return att


__all__ = [
    "ATTESTATION",
    "BundleError",
    "CreateResult",
    "VerifyResult",
    "create",
    "load_classifications",
    "memorization_check",
    "top_values_check",
    "verify",
]

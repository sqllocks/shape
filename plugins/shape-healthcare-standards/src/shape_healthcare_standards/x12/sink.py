"""``shape.sinks`` for the X12 writers: one interchange per file, written atomically."""

from __future__ import annotations

import dataclasses
import datetime as dt
import os
import tempfile
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from ..common import TableSet, build_tables, output_path
from ..contract import ContractError
from .ack import AckOptions, build_acknowledgment
from .claims import ClaimOptions, build_claims
from .core import Delimiters, EnvelopeOptions
from .enroll import EnrollmentOptions, build_enrollment
from .remit import CLAIM_STATUS_CODE, RemitOptions, build_remittance

_ENVELOPE_FIELDS = {f.name for f in dataclasses.fields(EnvelopeOptions)} - {"delimiters"}
_DELIMITER_FIELDS = {f.name for f in dataclasses.fields(Delimiters)}


def _fields(cls: type, options: Mapping[str, Any]) -> dict[str, Any]:
    names = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in options.items() if k in names}


def envelope_options(options: Mapping[str, Any]) -> EnvelopeOptions:
    """Interchange values from sink options (``sender_id``, ``created``, ``delimiters`` ...)."""
    values = {k: v for k, v in options.items() if k in _ENVELOPE_FIELDS}
    created = values.get("created")
    if isinstance(created, str):
        values["created"] = dt.datetime.fromisoformat(created)
    delim = {k: v for k, v in options.items() if k in _DELIMITER_FIELDS}
    if "delimiters" in options:
        values["delimiters"] = options["delimiters"]
    elif delim:
        values["delimiters"] = Delimiters(**delim)
    return EnvelopeOptions(**values)


def _write_files(directory: Path, prefix: str, files: list[str]) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for n, text in enumerate(files, start=1):
        target = directory / f"{prefix}-{n:06d}.x12"
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".x12-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="ascii", newline="") as fh:
                fh.write(text)
            os.replace(tmp, target)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
        paths.append(target)
    return paths


class _X12Sink:
    """Shared ``write``: build the table set, build the interchanges, write the files.

    Option ``tables``: companion tables by contract table name (``pyarrow.Table`` or
    ``RecordBatch``); see ``companion_tables(name)`` and docs/plugins/healthcare-standards.md.
    """

    name = ""
    schemes = ("file",)
    primary = ""
    prefix = ""
    needs: tuple[str, ...] = ()
    """Tables that must be present (as the primary or in ``tables``) before anything is built."""

    def _build(self, ts: TableSet, options: Mapping[str, Any]) -> tuple[list[str], int]:
        raise NotImplementedError

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        if table != self.primary:
            raise ContractError(
                f"sink {self.name!r} writes from table {self.primary!r}, not {table!r}"
            )
        ts = build_tables(table, batches, options.get("tables"))
        for name in self.needs:
            if not ts.has(name):
                raise ContractError(
                    f"sink {self.name!r} needs the {name!r} table (as the table or in tables=)"
                )
        files, count = self._build(ts, options)
        if files:
            _write_files(output_path(uri), self.prefix, files)
        return count


class X12Claim837PSink(_X12Sink):
    """837P professional claims from ``medical_claim`` (claim_type P)."""

    name = "x12-837p"
    primary = "medical_claim"
    prefix = "837P"

    def _build(self, ts: TableSet, options: Mapping[str, Any]) -> tuple[list[str], int]:
        files = build_claims(
            ts, "P", ClaimOptions(**_fields(ClaimOptions, options)), envelope_options(options)
        )
        return files, sum(1 for c in ts.rows("medical_claim") if c["claim_type"] == "P")


class X12Claim837ISink(_X12Sink):
    """837I institutional claims from ``medical_claim`` (claim_type I)."""

    name = "x12-837i"
    primary = "medical_claim"
    prefix = "837I"

    def _build(self, ts: TableSet, options: Mapping[str, Any]) -> tuple[list[str], int]:
        files = build_claims(
            ts, "I", ClaimOptions(**_fields(ClaimOptions, options)), envelope_options(options)
        )
        return files, sum(1 for c in ts.rows("medical_claim") if c["claim_type"] == "I")


class X12Remittance835Sink(_X12Sink):
    """835 remittance advice from adjudicated ``medical_claim`` rows."""

    name = "x12-835"
    primary = "medical_claim"
    prefix = "835"

    def _build(self, ts: TableSet, options: Mapping[str, Any]) -> tuple[list[str], int]:
        files = build_remittance(
            ts, RemitOptions(**_fields(RemitOptions, options)), envelope_options(options)
        )
        count = sum(
            1
            for c in ts.rows("medical_claim")
            if str(c.get("claim_status") or "").lower() in CLAIM_STATUS_CODE
        )
        return files, count


class X12Enrollment834Sink(_X12Sink):
    """834 enrollment from ``member`` and ``eligibility``."""

    name = "x12-834"
    primary = "member"
    prefix = "834"
    needs = ("eligibility",)

    def _build(self, ts: TableSet, options: Mapping[str, Any]) -> tuple[list[str], int]:
        files = build_enrollment(
            ts, EnrollmentOptions(**_fields(EnrollmentOptions, options)), envelope_options(options)
        )
        spans = ts.by("eligibility", "member_id")
        return files, sum(1 for m in ts.rows("member") if m["member_id"] in spans)


class X12Acknowledgment277CASink(_X12Sink):
    """277CA claim acknowledgments from ``claim_acknowledgment``; one interchange per date.

    Companion tables (``tables=``): ``medical_claim`` is required; ``provider``, ``member`` and
    ``medical_claim_line`` are used when given (the line table is required for line rows).
    """

    name = "x12-277ca"
    primary = "claim_acknowledgment"
    prefix = "277CA"

    def _build(self, ts: TableSet, options: Mapping[str, Any]) -> tuple[list[str], int]:
        files = build_acknowledgment(
            ts, AckOptions(**_fields(AckOptions, options)), envelope_options(options)
        )
        return files, len(ts.rows("claim_acknowledgment"))


SinkFactory = Callable[[], _X12Sink]

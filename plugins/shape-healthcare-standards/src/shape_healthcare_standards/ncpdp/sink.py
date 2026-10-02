"""``NcpdpSink``: pharmacy claims to NCPDP-style transaction files, with a layout you supply."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from .. import common, contract
from .layout import Layout, LayoutError
from .wire import write_transaction

_BYO = (
    "the NCPDP sink needs a layout. The NCPDP Telecommunication Standard is licensed by NCPDP "
    "and this plugin ships none of its field tables: write a layout JSON from your own licensed "
    "copy (see shape_healthcare_standards.ncpdp.layout) and pass layout=<path or dict>"
)


class NcpdpSink:
    """Writes one transmission file per pharmacy claim (one transaction in each) and returns
    how many were written.

    ``uri`` is an output directory (a path or ``file://``). Files are named
    ``<rx_claim_id>.<transaction code>.ncpdp`` and each is written atomically.

    Options (each overrides the constructor value): ``layout`` (a path or an already
    parsed dict or :class:`Layout`; required, at construction or per call), ``transaction_code``
    (default: the layout's first request transaction), ``tables`` (companion tables:
    ``member``, ``provider``, ``drug_reference``).
    """

    name = "ncpdp"
    # The plugin kit requires non-empty scheme names; a bare path is accepted regardless.
    schemes = ("file",)

    def __init__(
        self,
        layout: str | Path | Mapping[str, Any] | Layout | None = None,
        *,
        transaction_code: str | None = None,
    ) -> None:
        self._layout = layout
        self._code = transaction_code

    def write(self, uri: str, table: str, batches: Iterable[pa.RecordBatch], **options: Any) -> int:
        if table != "pharmacy_claim":
            raise contract.ContractError(
                f"the NCPDP sink writes the 'pharmacy_claim' table, not {table!r}"
            )
        layout = _resolve(options.get("layout", self._layout))
        code = options.get("transaction_code", self._code)
        tables = common.build_tables(table, batches, options.get("tables"))
        out_dir = common.output_path(uri)
        out_dir.mkdir(parents=True, exist_ok=True)
        rows = tables.rows("pharmacy_claim")
        for row in rows:
            data = write_transaction([row], layout, code, tables=tables)
            tcode = layout.transaction(code).code
            _atomic_write(out_dir / f"{_safe(row['rx_claim_id'])}.{_safe(tcode)}.ncpdp", data)
        return len(rows)


def _resolve(layout: str | Path | Mapping[str, Any] | Layout | None) -> Layout:
    if layout is None:
        raise LayoutError(_BYO)
    if isinstance(layout, Layout):
        return layout
    if isinstance(layout, Mapping):
        return Layout.from_dict(dict(layout))
    return Layout.load(layout)


def _safe(text: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in str(text))


def _atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ncpdp-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

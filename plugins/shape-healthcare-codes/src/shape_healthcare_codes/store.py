"""The loader API: where built code sets live and how a domain gets one.

Search order for an asset ``X`` (a :class:`~shape_healthcare_codes.model.CodeSystem` value):

1. the user data directory: ``$SHAPE_HEALTHCARE_CODES_DIR``, else
   ``$XDG_CACHE_HOME/shape/healthcare-codes``, else ``~/.cache/shape/healthcare-codes``
   (where ``shape healthcare-codes fetch`` and :func:`~shape_healthcare_codes.byo.load_byo`
   write);
2. the data shipped inside the wheel (a small subset, see ``THIRD_PARTY_NOTICES.md``).

A built asset is ``X.arrow`` (Arrow IPC file, zstd) plus ``X.json`` (its manifest: release,
source URL, source checksums, row count, whether it is a full build or a subset).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.ipc as ipc  # type: ignore[import-untyped]

from shape_healthcare_codes.model import CodeSet

SHIPPED_DIR = Path(__file__).parent / "data"


class AssetMissing(LookupError):
    """The asset is not built on this machine; the message says how to get it."""


def user_dir() -> Path:
    env = os.environ.get("SHAPE_HEALTHCARE_CODES_DIR")
    if env:
        return Path(env)
    cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(cache) if cache else Path.home() / ".cache"
    return base / "shape" / "healthcare-codes"


def _find(asset: str, data_dir: Path | None) -> Path | None:
    dirs = [data_dir] if data_dir is not None else [user_dir(), SHIPPED_DIR]
    for d in dirs:
        p = d / f"{asset}.arrow"
        if p.is_file():
            return p
    return None


def write_asset(
    asset: str, table: pa.Table, manifest: Mapping[str, Any], data_dir: Path | None = None
) -> Path:
    """Write ``table`` and its manifest as ``asset`` in ``data_dir`` (default: the user dir)."""
    d = data_dir if data_dir is not None else user_dir()
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{asset}.arrow"
    opts = ipc.IpcWriteOptions(compression="zstd")
    with pa.OSFile(str(path), "wb") as sink, ipc.new_file(sink, table.schema, options=opts) as w:
        w.write_table(table)
    meta = dict(manifest)
    meta["rows"] = table.num_rows
    meta["bytes"] = path.stat().st_size
    (d / f"{asset}.json").write_text(
        json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return path


def read_table(asset: str, data_dir: Path | None = None) -> pa.Table:
    path = _find(asset, data_dir)
    if path is None:
        raise AssetMissing(
            f"{asset!r} is not built here. Free sets: `shape healthcare-codes fetch {asset}`. "
            f"Licensed sets: `shape healthcare-codes byo {asset} FILE`. "
            f"Searched: {data_dir or user_dir()}, {SHIPPED_DIR}"
        )
    # Read into memory (the file is zstd-compressed, so a memory map would not help) and close
    # it: an open map would stop Windows from replacing the file when an asset is rebuilt.
    with pa.OSFile(str(path), "rb") as src:
        return ipc.open_file(src).read_all()


def manifest(asset: str, data_dir: Path | None = None) -> dict[str, Any]:
    path = _find(asset, data_dir)
    if path is None:
        raise AssetMissing(f"{asset!r} is not built here")
    mp = path.with_suffix(".json")
    return dict(json.loads(mp.read_text(encoding="utf-8"))) if mp.is_file() else {}


def load(system: str, data_dir: Path | None = None) -> CodeSet:
    """The :class:`CodeSet` of ``system`` (a ``CodeSystem`` value). Raises
    :class:`AssetMissing` when it is not built."""
    table = read_table(str(system), data_dir)
    return CodeSet(str(system), table, str(manifest(str(system), data_dir).get("release", "")))


def available(data_dir: Path | None = None) -> dict[str, str]:
    """Asset id -> where it was found (``"user"`` or ``"shipped"``)."""
    out: dict[str, str] = {}
    for d, label in ((SHIPPED_DIR, "shipped"), (data_dir or user_dir(), "user")):
        if d.is_dir():
            for p in sorted(d.glob("*.arrow")):
                out[p.stem] = label
    return out

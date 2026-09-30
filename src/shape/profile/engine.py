"""The profile engine: the batch loop around the fused kernel (P1-07).

``profile_table`` reads a source through ``shape.io`` as Arrow record batches, feeds each batch to
the kernel with one call, and returns a JSON-safe document that follows
``shape/schemas/profile-engine-v1.schema.json``. ``profile_many`` profiles several inputs in one
process. Two modes (T-15): ``exact`` (the default: exact distinct counts, value counts and
quantiles, as Spindle computes them) and ``bounded`` (sketches; memory does not grow with the
number of rows, and CSV files are streamed rather than read whole).

Threads: ``SHAPE_THREADS`` (or ``EngineOptions.threads``) sizes the kernel's thread pool and
pyarrow's; unset means all cores. Both modes emit the same document layout.
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]

from shape.io import CsvOptions, open_source
from shape.kernel.dispatch import get_kernel
from shape.profile.error import ErrorModel, hll_error, kll_error, space_saving_error

SCHEMA_VERSION = 1
_BOUNDED_CSV_BLOCK = 4 << 20
MODES = ("exact", "bounded")
_EXACT_CARD = ErrorModel("exact-hash-table", True)
_EXACT_QUANTILE = ErrorModel("exact-sort", True)
_EXACT_TOP = ErrorModel("exact-value-counts", True)
_EXACT = ErrorModel("exact", True)


@dataclass(frozen=True)
class EngineOptions:
    """Engine settings. ``threads`` of ``None`` reads ``SHAPE_THREADS`` (unset: all cores)."""

    mode: str = "exact"
    batch_size: int = 1 << 17
    threads: int | None = None
    top_n: int = 500
    csv: CsvOptions | None = None

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be 'exact' or 'bounded', got {self.mode!r}")
        if self.batch_size < 1:
            raise ValueError("batch_size must be positive")
        if self.top_n < 1:
            raise ValueError("top_n must be positive")


def resolve_threads(threads: int | None = None) -> int:
    """The thread count to use: the argument, else ``SHAPE_THREADS``, else 0 (all cores)."""
    if threads is not None:
        return threads
    raw = os.environ.get("SHAPE_THREADS", "").strip()
    if not raw:
        return 0
    try:
        n = int(raw)
    except ValueError as exc:
        raise ValueError(f"SHAPE_THREADS must be a non-negative integer, got {raw!r}") from exc
    if n < 0:
        raise ValueError(f"SHAPE_THREADS must be a non-negative integer, got {raw!r}")
    return n


def configure_threads(threads: int | None = None) -> int:
    """Size the kernel and Arrow thread pools (the kernel's pool is fixed by the first call in
    a process). Returns the kernel's thread count."""
    n = resolve_threads(threads)
    if n:
        pa.set_cpu_count(n)
        pa.set_io_thread_count(n)
    return int(get_kernel().set_threads(n))


def _json_value(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return "inf" if v > 0 else "-inf"
    return v


def _column_doc(stats: dict[str, Any], arrow_type: str, mode: str) -> dict[str, Any]:
    d = dict(stats)
    d["arrow_type"] = arrow_type
    kind = d["kind"]
    exact = mode == "exact"
    models: dict[str, ErrorModel] = {"counts": _EXACT}
    if "top" in d:
        d["top"] = [[_json_value(v), c, e, f] for v, c, e, f in d["top"]]
        models["cardinality"] = _EXACT_CARD if exact else hll_error(14)
        models["top"] = _EXACT_TOP if exact else space_saving_error(64)
    if "quantiles" in d:
        d["quantiles"] = {str(q): v for q, v in d["quantiles"].items()}
        models["quantiles"] = _EXACT_QUANTILE if exact else kll_error(200)
    if kind in ("int", "float"):
        n = d["finite_count"]
        d["variance_population"] = d["m2"] / n if n else None
        d["variance_sample"] = d["m2"] / (n - 1) if n > 1 else None
    if kind == "text":
        length = d["length"]
        if "hist" in length:
            length["hist"] = {str(k): v for k, v in length["hist"].items()}
    if "year_hist" in d:
        d["year_hist"] = {str(k): v for k, v in d["year_hist"].items()}
    d["error_models"] = {k: m.to_dict() for k, m in models.items()}
    return d


def profile_table(
    source: Any, name: str | None = None, options: EngineOptions | None = None
) -> dict[str, Any]:
    """Profile one table-shaped source. Returns the table entry: ``{name, rows, columns}``."""
    opts = options or EngineOptions()
    configure_threads(opts.threads)
    csv = opts.csv
    if csv is None and opts.mode == "bounded":
        # bounded memory: never read a whole CSV at once, and keep the read-ahead small (with
        # the default 16 MiB blocks Arrow's read-ahead buffers made peak RSS creep up with size)
        csv = CsvOptions(stream=True, block_size=_BOUNDED_CSV_BLOCK)
    src = open_source(source, name=name, batch_size=opts.batch_size, csv=csv)
    state = get_kernel().ProfileState(src.schema, opts.mode)
    pool = pa.default_memory_pool()
    for i, batch in enumerate(src.batches()):
        state.update(batch)
        if opts.mode == "bounded" and i % 16 == 15:
            pool.release_unused()  # keep the allocator from holding on to freed read-ahead blocks
    result = state.finalize(opts.top_n)
    types = [str(f.type) for f in src.schema]
    return {
        "name": src.name,
        "rows": result["rows"],
        "columns": [
            _column_doc(c, t, opts.mode) for c, t in zip(result["columns"], types, strict=True)
        ],
    }


def _document(mode: str, tables: dict[str, Any]) -> dict[str, Any]:
    from shape import __version__

    return {
        "schema_version": SCHEMA_VERSION,
        "engine": "shape-profile-engine",
        "shape_version": __version__,
        "mode": mode,
        "tables": tables,
    }


def profile_many(
    sources: Mapping[str, Any] | list[Any] | tuple[Any, ...], options: EngineOptions | None = None
) -> dict[str, Any]:
    """Profile several inputs in one process (one thread pool, one document). ``sources`` is a
    mapping of table name to source, or a list of sources named by their file stems."""
    opts = options or EngineOptions()
    items: list[tuple[str | None, Any]]
    if isinstance(sources, Mapping):
        items = list(sources.items())
    else:
        items = [(None, s) for s in sources]
    tables: dict[str, Any] = {}
    for name, src in items:
        entry = profile_table(src, name, opts)
        key = entry["name"]
        if key in tables:
            raise ValueError(f"two inputs are both named {key!r}; pass a mapping to name them")
        tables[key] = entry
    return _document(opts.mode, tables)


def profile(source: Any, name: str | None = None, **kwargs: Any) -> dict[str, Any]:
    """Profile one source and return the full document. Keyword arguments build
    ``EngineOptions`` (``mode``, ``batch_size``, ``threads``, ``top_n``, ``csv``)."""
    opts = EngineOptions(**kwargs)
    entry = profile_table(source, name, opts)
    return _document(opts.mode, {entry["name"]: entry})

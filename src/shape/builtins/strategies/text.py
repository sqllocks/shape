"""Built-in strategy ``pattern``: formatted strings from a template. Row addressed
(``docs/GENERATION_STRATEGIES.md``)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation import kernel_ops
from shape.generation.arrowkit import array as arrow_array
from shape.generation.strategy_kit import StrategyError, stream, where
from shape.plugins.api.v1 import GenerationContext

SHAPE_API = "1.0"

_TOKEN = re.compile(r"\{(\w+)(?::(\d+))?\}")
# A token width is rows x width bytes of memory: a schema asking for {random:2000000000} aborted
# the process on a failed allocation (P7-04).
MAX_TOKEN_WIDTH = 4096
RANDOM_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
DIGIT_ALPHABET = "0123456789"


def _as_slot_column(column: pa.Array, width: int) -> pa.Array:
    """A referenced column in a form the kernel can place: int64 (zero-padded to ``width`` by the
    kernel), or string (left-padded with zeros here, as ``str.zfill`` would)."""
    if isinstance(column, pa.ChunkedArray):
        column = column.combine_chunks()
    t = column.type
    if pa.types.is_integer(t):
        return pc.cast(column, pa.int64())
    if not (pa.types.is_string(t) or pa.types.is_large_string(t)):
        column = pc.cast(column, pa.string())
    return pc.utf8_lpad(column, width, "0") if width else column


class Pattern:
    """A string built from ``spec['format']``.

    Tokens are ``{seq}`` or ``{seq:6}`` (the 1-based row number of the table, zero-padded to the
    width), ``{random:4}`` (that many characters from ``A-Z0-9``; 4 without a width),
    ``{digits:5}`` (that many random digits, leading zeros included: an identifier such as a ZIP
    code or an NPI; 4 without a width) and
    ``{name}`` or ``{name:3}`` (the value of another column of the same row, zero-padded).
    Text outside tokens is literal; a token naming no column is left as written. A null in a
    referenced column makes the row null. Numbers of non-integer type are formatted by Arrow.
    """

    name = "pattern"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        fmt = spec.get("format")
        if not isinstance(fmt, str) or not fmt:
            raise StrategyError(f"pattern strategy requires 'format' for column {where(ctx)}")
        literals: list[str] = [""]
        slots: list[tuple[int, int]] = []
        columns: list[pa.Array] = []
        n_random = 0
        last = 0
        for m in _TOKEN.finditer(fmt):
            literals[-1] += fmt[last : m.start()]
            last = m.end()
            token, width = m.group(1), int(m.group(2)) if m.group(2) else 0
            if width > MAX_TOKEN_WIDTH:
                raise ValueError(f"pattern width {width} is over the limit of {MAX_TOKEN_WIDTH}")
            if token == "seq":
                numbers = np.arange(
                    ctx.row_start + 1, ctx.row_start + ctx.n_rows + 1, dtype=np.int64
                )
                columns.append(arrow_array(numbers))
            elif token in ("random", "digits"):
                columns.append(
                    kernel_ops.random_strings(
                        stream(ctx, f"random{n_random}"),
                        ctx.row_start,
                        ctx.n_rows,
                        width or 4,
                        RANDOM_ALPHABET if token == "random" else DIGIT_ALPHABET,
                    )
                )
                n_random += 1
            elif token in ctx.columns:
                columns.append(_as_slot_column(ctx.columns[token], width))
            else:
                literals[-1] += m.group(0)
                continue
            slots.append((len(columns) - 1, width))  # the kernel pads integers only
            literals.append("")
        literals[-1] += fmt[last:]
        return kernel_ops.template_strings(literals, slots, columns, ctx.n_rows)


__all__ = ["SHAPE_API", "Pattern"]

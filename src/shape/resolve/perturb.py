"""Seeded, realistic damage to values: typos, case, spacing, swapped or abbreviated words, small
numeric jitter and date shifts. Used by the synthetic duplicates generator and by the chaos
``duplicates`` mutator's ``fuzz`` option. Every function draws only from the generator it is given.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection
from typing import Any

import numpy as np
import pyarrow as pa  # type: ignore[import-untyped]

_LETTERS = "abcdefghijklmnopqrstuvwxyz"


def perturb_text(s: str, rng: np.random.Generator) -> str:
    """``s`` with one realistic fault; always different from ``s`` when ``s`` is not empty."""
    if not s:
        return s
    tokens = s.split()
    ops = ["substitute", "delete", "insert", "transpose", "case", "space"]
    if len(tokens) >= 2:
        ops += ["swap", "initial"]
    op = ops[int(rng.integers(0, len(ops)))]
    pos = int(rng.integers(0, len(s)))
    out = s
    if op == "substitute":
        c = _LETTERS[int(rng.integers(0, 26))]
        out = s[:pos] + c + s[pos + 1 :]
    elif op == "delete" and len(s) > 1:
        out = s[:pos] + s[pos + 1 :]
    elif op == "insert":
        out = s[:pos] + _LETTERS[int(rng.integers(0, 26))] + s[pos:]
    elif op == "transpose" and len(s) > 1:
        k = min(pos, len(s) - 2)
        out = s[:k] + s[k + 1] + s[k] + s[k + 2 :]
    elif op == "case":
        out = s.swapcase() if s.swapcase() != s else s.upper()
    elif op == "space":
        out = f" {s} "
    elif op == "swap":
        out = (
            " ".join([tokens[-1], *tokens[1:-1], tokens[0]])
            if len(tokens) > 2
            else " ".join(reversed(tokens))
        )
    elif op == "initial":
        out = " ".join([tokens[0][:1], *tokens[1:]])
    if out == s:
        out = s + _LETTERS[int(rng.integers(0, 26))]
    return out


def perturb_number(x: float, rng: np.random.Generator, jitter: float) -> float:
    """``x`` scaled by 1 + u x ``jitter`` with u in [-1, 1]."""
    return float(x * (1.0 + (float(rng.random()) * 2.0 - 1.0) * jitter))


def perturb_date(v: Any, rng: np.random.Generator, days: int) -> Any:
    """A date or datetime moved by -days..days days."""
    shift = int(rng.integers(-days, days + 1))
    if isinstance(v, (dt.date, dt.datetime)):
        return v + dt.timedelta(days=shift)
    return v


def fuzz_table(
    table: pa.Table,
    rng: np.random.Generator,
    *,
    rate: float,
    skip: Collection[str | None] = (),
    numeric_jitter: float = 0.01,
    date_days: int = 2,
) -> pa.Table:
    """Each present cell of a text, number or date column (outside ``skip``) is damaged with
    probability ``rate``."""
    skip_set = {s for s in skip if s is not None}
    out = table
    for i, field in enumerate(table.schema):
        if field.name in skip_set:
            continue
        t = field.type
        is_text = pa.types.is_string(t) or pa.types.is_large_string(t)
        is_float = pa.types.is_floating(t)
        is_int = pa.types.is_integer(t)
        is_date = pa.types.is_date(t) or pa.types.is_timestamp(t)
        if not (is_text or is_float or is_int or is_date):
            continue
        cells = table.column(i).to_pylist()
        hit = rng.random(len(cells)) < rate
        for k, v in enumerate(cells):
            if v is None or not hit[k]:
                continue
            if is_text:
                cells[k] = perturb_text(v, rng)
            elif is_float:
                cells[k] = round(perturb_number(v, rng, numeric_jitter), 2)
            elif is_int:
                cells[k] = int(round(perturb_number(v, rng, numeric_jitter)))
            else:
                cells[k] = perturb_date(v, rng, date_days)
        out = out.set_column(i, field, pa.array(cells, type=t))
    return out

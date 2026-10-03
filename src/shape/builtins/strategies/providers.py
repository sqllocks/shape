"""Built-in strategies ``native`` and ``faker``: realistic text from reference pools.

Both take ``provider`` (default ``word``). ``native`` serves the providers below from the pools in
``pools/`` (names, companies, streets, sentences, cities, states, domains and URI paths); ``faker``
serves the same providers identically and, for any other provider, draws a pool from the optional
``faker`` package and samples it. Every draw is row addressed
(``docs/GENERATION_STRATEGIES.md``): the value of row ``r`` depends on the run seed, the table, the
column and ``r`` (and, for ``email``, on the ``first_name`` and ``last_name`` columns of the same
row), never on the chunk.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping
from functools import cache, lru_cache
from importlib import resources
from typing import Any

import numpy as np
import numpy.typing as npt
import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.compute as pc  # type: ignore[import-untyped]

from shape.generation import kernel_ops
from shape.generation.arrowkit import array as arrow_array
from shape.generation.rng import stream_key
from shape.generation.strategy_kit import StrategyError, stream, where
from shape.plugins.api.v1 import GenerationContext

SHAPE_API = "1.0"

# Identifier providers produce values that cannot belong to a real person unless the spec asks
# (ISS-gen, owner issues 11 and 12): e-mail and URI hosts are the names RFC 2606 reserves, social
# security numbers use the 9xx areas no one is assigned, and phone numbers are the fictional
# 555-01xx lines. `"domains": "realistic"` and `"range": "assignable"` give the old, realistic
# values (real mail providers, assignable numbers), which can collide with real people.
RESERVED_DOMAINS = ("example.com", "example.org", "example.net")
DOMAIN_MODES = ("reserved", "realistic")
RANGE_MODES = ("reserved", "assignable")

PYSTR_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
PYSTR_LENGTH = 12
FAKER_POOL_ROWS = 50_000  # most values a faker pool holds


def _lines(raw: bytes) -> pa.Array:
    """The newline-terminated lines of ``raw`` (UTF-8) as a string array; text after the last
    newline is not a line. Built from the bytes with numpy, with no Python object per entry: the
    same array as ``pa.array(raw.decode().split("\\n")[:-1])``, about 20 times faster."""
    raw.decode("utf-8")  # a pool that is not UTF-8 is an error, as when it was read as text
    buf = np.frombuffer(raw, dtype=np.uint8)
    ends = np.flatnonzero(buf == 10)
    offsets = np.zeros(len(ends) + 1, dtype=np.int32)
    if len(ends):
        offsets[1:] = ends - np.arange(len(ends), dtype=np.int64)  # bytes before each newline,
        # less the newlines already passed
        body = buf[: ends[-1] + 1]
        data = np.ascontiguousarray(body[body != 10])
    else:
        data = buf[:0]
    out: pa.Array = pa.Array.from_buffers(
        pa.string(), len(ends), [None, pa.py_buffer(offsets), pa.py_buffer(data)]
    )
    return out


@cache
def pool(name: str) -> pa.Array:
    """The reference pool ``name`` (``pools/<name>.txt``, one entry per line) as a string array."""
    return _lines(resources.files(__package__).joinpath(f"pools/{name}.txt").read_bytes())


@cache
def _company_stems() -> pa.Array:
    """Company names as they appear in an address: lower case, no spaces, commas or dots, cut at
    20 characters."""
    out = pc.utf8_lower(pool("company_names"))
    for char in (" ", ",", "."):
        out = pc.replace_substring(out, char, "")
    return pc.utf8_slice_codeunits(out, 0, 20)


def _pick(ctx: GenerationContext, label: str, size: int) -> npt.NDArray[np.int64]:
    """A uniform index into ``size`` entries for each row (its own stream, one word per row)."""
    u = stream(ctx, label).uniform(ctx.row_start, ctx.n_rows)
    return np.minimum((u * size).astype(np.int64), size - 1)


def _ints(ctx: GenerationContext, label: str, low: int, high: int) -> npt.NDArray[np.int64]:
    """Uniform integers in ``[low, high)``, one per row."""
    return low + _pick(ctx, label, high - low)


def _from_pool(ctx: GenerationContext, label: str, name: str) -> pa.Array:
    entries = pool(name)
    return kernel_ops.pool_take(entries, _pick(ctx, label, len(entries)))


def _slug(names: pa.Array) -> pa.Array:
    """Lower case with spaces removed (the form names take inside an e-mail address)."""
    return pc.replace_substring(pc.utf8_lower(names), " ", "")


def _first_name(ctx: GenerationContext) -> pa.Array:
    return _from_pool(ctx, "first", "first_names")


def _last_name(ctx: GenerationContext) -> pa.Array:
    return _from_pool(ctx, "last", "last_names")


def _name(ctx: GenerationContext) -> pa.Array:
    return kernel_ops.join_strings([_first_name(ctx), _last_name(ctx)], " ")


def _domains(spec: Mapping[str, Any], ctx: GenerationContext) -> str:
    mode = str(spec.get("domains", "reserved"))
    if mode not in DOMAIN_MODES:
        raise StrategyError(
            f"provider {_provider(spec)!r} takes domains {' or '.join(DOMAIN_MODES)}, "
            f"not {mode!r} ({where(ctx)})"
        )
    return mode


def _range_mode(spec: Mapping[str, Any], ctx: GenerationContext) -> str:
    mode = str(spec.get("range", "reserved"))
    if mode not in RANGE_MODES:
        raise StrategyError(
            f"provider {_provider(spec)!r} takes range {' or '.join(RANGE_MODES)}, "
            f"not {mode!r} ({where(ctx)})"
        )
    return mode


def _reserved_domain(ctx: GenerationContext) -> pa.Array:
    return kernel_ops.pool_take(
        arrow_array(list(RESERVED_DOMAINS), type=pa.string()),
        _pick(ctx, "domain", len(RESERVED_DOMAINS)),
    )


def _email(ctx: GenerationContext, spec: Mapping[str, Any]) -> pa.Array:
    # The same row's first_name and last_name columns when the table has both.
    if "first_name" in ctx.columns and "last_name" in ctx.columns:
        firsts, lasts = ctx.columns["first_name"], ctx.columns["last_name"]
    else:
        firsts, lasts = _first_name(ctx), _last_name(ctx)
    realistic = _domains(spec, ctx) == "realistic"
    domains = _from_pool(ctx, "domain", "email_domains") if realistic else _reserved_domain(ctx)
    suffix = arrow_array(_ints(ctx, "suffix", 1, 999))
    return kernel_ops.template_strings(
        ["", ".", "", "@", ""],
        [(0, 0), (1, 0), (2, 0), (3, 0)],
        [_slug(_as_string(firsts)), _slug(_as_string(lasts)), suffix, domains],
        ctx.n_rows,
    )


def _as_string(column: pa.Array) -> pa.Array:
    if isinstance(column, pa.ChunkedArray):
        column = column.combine_chunks()
    return column if pa.types.is_string(column.type) else pc.cast(column, pa.string())


def _phone_number(ctx: GenerationContext, spec: Mapping[str, Any]) -> pa.Array:
    area, exchange = _ints(ctx, "area", 200, 999), _ints(ctx, "exchange", 200, 999)
    if _range_mode(spec, ctx) == "reserved":
        # (AAA) 555-0100 to 555-0199: the lines reserved for fiction
        subscriber = _ints(ctx, "subscriber", 100, 200)
        return kernel_ops.template_strings(
            ["(", ") 555-", ""],
            [(0, 0), (1, 4)],
            [arrow_array(area), arrow_array(subscriber)],
            ctx.n_rows,
        )
    subscriber = _ints(ctx, "subscriber", 1000, 9999)
    return kernel_ops.template_strings(
        ["(", ") ", "-", ""],
        [(0, 0), (1, 0), (2, 0)],
        [arrow_array(area), arrow_array(exchange), arrow_array(subscriber)],
        ctx.n_rows,
    )


def _ssn(ctx: GenerationContext, spec: Mapping[str, Any]) -> pa.Array:
    # AAA-GG-SSSS. Reserved (default): area 900-999, which no one is assigned. Assignable: the
    # areas the SSA can issue, without 000, 666 and 900-999.
    if _range_mode(spec, ctx) == "reserved":
        area = _ints(ctx, "area", 900, 1000)
    else:
        area = _ints(ctx, "area", 1, 900)
        area = np.where(area == 666, 665, area)
    group, serial = _ints(ctx, "group", 1, 100), _ints(ctx, "serial", 1, 10_000)
    return kernel_ops.template_strings(
        ["", "-", "-", ""],
        [(0, 3), (1, 2), (2, 4)],
        [arrow_array(area), arrow_array(group), arrow_array(serial)],
        ctx.n_rows,
    )


def _ipv4(ctx: GenerationContext) -> pa.Array:
    # a.b.c.d with the first and last octet in 1..254
    parts = [
        arrow_array(_ints(ctx, "a", 1, 255)),
        arrow_array(_ints(ctx, "b", 0, 256)),
        arrow_array(_ints(ctx, "c", 0, 256)),
        arrow_array(_ints(ctx, "d", 1, 255)),
    ]
    return kernel_ops.template_strings(
        ["", ".", ".", ".", ""], [(0, 0), (1, 0), (2, 0), (3, 0)], parts, ctx.n_rows
    )


def _postcode(ctx: GenerationContext) -> pa.Array:
    # five digits, zero padded
    return kernel_ops.template_strings(
        ["", ""], [(0, 5)], [arrow_array(_ints(ctx, "zip", 501, 99_951))], ctx.n_rows
    )


def _zip_plus4(ctx: GenerationContext) -> pa.Array:
    # NNNNN-NNNN, zero padded
    return kernel_ops.template_strings(
        ["", "-", ""],
        [(0, 5), (1, 4)],
        [arrow_array(_ints(ctx, "zip", 501, 99_951)), arrow_array(_ints(ctx, "plus4", 1, 10_000))],
        ctx.n_rows,
    )


def _company(ctx: GenerationContext) -> pa.Array:
    return _from_pool(ctx, "company", "company_names")


def _street_address(ctx: GenerationContext) -> pa.Array:
    return kernel_ops.template_strings(
        ["", " ", " ", ""],
        [(0, 0), (1, 0), (2, 0)],
        [
            arrow_array(_ints(ctx, "number", 100, 9999)),
            _from_pool(ctx, "street", "street_names"),
            _from_pool(ctx, "suffix", "street_suffixes"),
        ],
        ctx.n_rows,
    )


def _sentence(ctx: GenerationContext) -> pa.Array:
    return _from_pool(ctx, "sentence", "sentences")


def _city(ctx: GenerationContext) -> pa.Array:
    return _from_pool(ctx, "city", "us_cities")


def _state_abbr(ctx: GenerationContext) -> pa.Array:
    return _from_pool(ctx, "state", "us_states")


def _uri(ctx: GenerationContext, spec: Mapping[str, Any]) -> pa.Array:
    realistic = _domains(spec, ctx) == "realistic"
    return kernel_ops.template_strings(
        ["https://", "/", ""],
        [(0, 0), (1, 0)],
        [
            _from_pool(ctx, "domain", "uri_domains") if realistic else _reserved_domain(ctx),
            _from_pool(ctx, "path", "uri_paths"),
        ],
        ctx.n_rows,
    )


def _company_email(ctx: GenerationContext, spec: Mapping[str, Any]) -> pa.Array:
    stems = _company_stems()
    # `.example` is the top-level domain RFC 2606 reserves: it never resolves
    tld = ".com" if _domains(spec, ctx) == "realistic" else ".example"
    return kernel_ops.template_strings(
        ["", ".", "@", tld],
        [(0, 0), (1, 0), (2, 0)],
        [
            _slug(_first_name(ctx)),
            _slug(_last_name(ctx)),
            kernel_ops.pool_take(stems, _pick(ctx, "company", len(stems))),
        ],
        ctx.n_rows,
    )


def _word(ctx: GenerationContext) -> pa.Array:
    """Random lower-case alphanumeric text (``pystr`` and ``word``): 12 characters per value."""
    return kernel_ops.random_strings(
        stream(ctx, "chars"), ctx.row_start, ctx.n_rows, PYSTR_LENGTH, PYSTR_ALPHABET
    )


_Provider = Callable[[GenerationContext, Mapping[str, Any]], pa.Array]


def _plain(make: Callable[[GenerationContext], pa.Array]) -> _Provider:
    return lambda ctx, spec: make(ctx)


PROVIDERS: dict[str, _Provider] = {
    "first_name": _plain(_first_name),
    "last_name": _plain(_last_name),
    "name": _plain(_name),
    "email": _email,
    "phone_number": _phone_number,
    "ssn": _ssn,
    "company": _plain(_company),
    "street_address": _plain(_street_address),
    "sentence": _plain(_sentence),
    "city": _plain(_city),
    "state_abbr": _plain(_state_abbr),
    "uri": _uri,
    "company_email": _company_email,
    "ipv4": _plain(_ipv4),
    "postcode": _plain(_postcode),
    "zip_plus4": _plain(_zip_plus4),
    "pystr": _plain(_word),
    "word": _plain(_word),
}


MAX_DIGITS = 18  # the widest zero-padded identifier (it must fit an int64)


def _digit_width(spec: Mapping[str, Any]) -> int:
    return max(1, min(int(spec.get("width", 8)), MAX_DIGITS))


def _digits(spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
    """Fixed-width digit text with leading zeros (ZIP codes, NDCs, member ids): ``spec['width']``
    digits, drawn at random."""
    width = _digit_width(spec)
    numbers = _ints(ctx, "digits", 0, 10**width)
    return kernel_ops.template_strings(["", ""], [(0, width)], [arrow_array(numbers)], ctx.n_rows)


def _digit_ids(spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
    """Fixed-width digit text counting up from 1 in row order, zero padded: unique identifiers."""
    width = _digit_width(spec)
    numbers = ctx.row_start + np.arange(1, ctx.n_rows + 1, dtype=np.int64)
    if width < MAX_DIGITS:
        numbers = numbers % 10**width
    return kernel_ops.template_strings(["", ""], [(0, width)], [arrow_array(numbers)], ctx.n_rows)


SPEC_PROVIDERS: dict[str, Callable[[Mapping[str, Any], GenerationContext], pa.Array]] = {
    "digits": _digits,
    "digit_ids": _digit_ids,
}


def _truncate(values: pa.Array, ctx: GenerationContext) -> pa.Array:
    """Cut text at the column's ``max_length`` characters (as a database column would)."""
    limit = getattr(getattr(ctx, "column_def", None), "max_length", None)
    if not limit or not pa.types.is_string(values.type):
        return values
    return pc.utf8_slice_codeunits(values, 0, int(limit))


def _provider(spec: Mapping[str, Any]) -> str:
    return str(spec.get("provider", "word"))


class Native:
    """Text from the built-in pools: ``spec['provider']`` is one of ``first_name``, ``last_name``,
    ``name``, ``email``, ``phone_number``, ``ssn``, ``company``, ``street_address``, ``sentence``,
    ``city``, ``state_abbr``, ``uri``, ``company_email``, ``ipv4``, ``postcode``, ``zip_plus4``,
    ``pystr`` or
    ``word`` (the default), or ``digits`` / ``digit_ids`` (``spec['width']`` zero-padded digits,
    random or counting up).
    The column's ``max_length`` truncates."""

    name = "native"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        provider = _provider(spec)
        if provider in SPEC_PROVIDERS:
            return SPEC_PROVIDERS[provider](spec, ctx)
        make = PROVIDERS.get(provider)
        if make is None:
            raise StrategyError(
                f"native strategy does not handle provider {provider!r} for column {where(ctx)}; "
                f"it serves {', '.join(sorted(PROVIDERS))}"
            )
        return _truncate(make(ctx, spec), ctx)


@lru_cache(maxsize=16)
def _faker_pool(locale: str, provider: str, args_json: str, key: int, size: int) -> pa.Array:
    """``size`` values of a Faker provider, drawn once with a Faker seeded from ``key``."""
    try:
        faker_class = importlib.import_module("faker").Faker
    except ImportError as exc:
        raise ImportError(
            f"the faker strategy needs the 'faker' package for provider {provider!r}; "
            "install it with: pip install faker"
        ) from exc
    fake = faker_class(locale)
    fake.seed_instance(key & 0x7FFFFFFF)
    method = getattr(fake, provider, None)
    if not callable(method):
        raise StrategyError(f"unknown faker provider {provider!r}")
    args = json.loads(args_json)
    try:
        return arrow_array([method(**args) for _ in range(size)])
    except (pa.ArrowInvalid, pa.ArrowTypeError) as exc:
        raise StrategyError(f"faker provider {provider!r} returns mixed types: {exc}") from exc


class Faker:
    """The ``native`` providers, and any other provider of the ``faker`` package.

    For an exotic provider (``spec['provider']``, with ``spec['args']`` as its keyword arguments)
    a pool of ``min(rows of the table, 50,000)`` values is drawn once from a Faker seeded by the
    run seed, the table and the column, and the column takes values from it: row ``r`` reads pool
    entry ``r`` while the table fits the pool (all values distinct), else a uniformly drawn entry.
    The ``locale`` is the model's. Without the package the strategy raises ``ImportError``.
    """

    name = "faker"
    generator_version = 1

    def generate(self, spec: Mapping[str, Any], ctx: GenerationContext) -> pa.Array:
        provider = _provider(spec)
        if provider in SPEC_PROVIDERS:
            return SPEC_PROVIDERS[provider](spec, ctx)
        make = PROVIDERS.get(provider)
        if make is not None:
            return _truncate(make(ctx, spec), ctx)
        engine = getattr(ctx, "engine", None)
        rows = engine.row_counts.get(ctx.table, FAKER_POOL_ROWS) if engine else FAKER_POOL_ROWS
        size = max(1, min(int(rows), FAKER_POOL_ROWS))
        locale = engine.schema.model.locale if engine else "en_US"
        args = spec.get("args") or {}
        entries = _faker_pool(
            str(locale or "en_US"),
            provider,
            json.dumps(args, sort_keys=True),
            stream_key(ctx.seed, ctx.table, ctx.column, "faker"),
            size,
        )
        if int(rows) <= size:
            index = np.arange(ctx.row_start, ctx.row_start + ctx.n_rows, dtype=np.int64)
        else:
            index = _pick(ctx, "faker", size)
        values = pc.take(entries, arrow_array(index))
        return _truncate(values, ctx)


__all__ = ["PROVIDERS", "SHAPE_API", "Faker", "Native", "pool"]

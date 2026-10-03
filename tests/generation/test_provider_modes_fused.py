"""The identifier providers' ``domains`` and ``range`` modes on the fused route
(``compose_strings``) give the values of the stepwise route they replace: each draw, gather and
``template_strings`` call made one after another, as written below. Both kernels, every mode,
several chunks."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np
import pyarrow as pa
import pyarrow.compute as pc
import pytest

from shape.builtins.strategies import providers as P
from shape.generation import kernel_ops
from shape.generation.arrowkit import array as arrow_array
from shape.generation.strategy_kit import StrategyError, stream
from shape.kernel import dispatch
from shape.plugins.api.v1 import GenerationContext


def _ctx(n: int, start: int, columns: dict[str, pa.Array] | None = None) -> GenerationContext:
    ctx = GenerationContext(
        seed=20261003, table="person", column="c", chunk=0, row_start=start, n_rows=n
    )
    if columns:
        ctx.columns.update(columns)
    return ctx


# ---- the stepwise route ---------------------------------------------------------------------


def _pick(ctx: GenerationContext, label: str, size: int) -> np.ndarray:
    u = stream(ctx, label).uniform(ctx.row_start, ctx.n_rows)
    return np.minimum((u * size).astype(np.int64), size - 1)


def _ints(ctx: GenerationContext, label: str, low: int, high: int) -> np.ndarray:
    return low + _pick(ctx, label, high - low)


def _from_pool(ctx: GenerationContext, label: str, entries: pa.Array) -> pa.Array:
    return kernel_ops.pool_take(entries, _pick(ctx, label, len(entries)))


def _slug(names: pa.Array) -> pa.Array:
    return pc.replace_substring(pc.utf8_lower(names), " ", "")


def _host(ctx: GenerationContext, spec: Mapping[str, Any], name: str) -> pa.Array:
    if spec.get("domains", "reserved") == "realistic":
        return _from_pool(ctx, "domain", P.pool(name))
    return _from_pool(ctx, "domain", pa.array(list(P.RESERVED_DOMAINS), type=pa.string()))


def _email(ctx: GenerationContext, spec: Mapping[str, Any]) -> pa.Array:
    if "first_name" in ctx.columns and "last_name" in ctx.columns:
        firsts, lasts = ctx.columns["first_name"], ctx.columns["last_name"]
    else:
        firsts = _from_pool(ctx, "first", P.pool("first_names"))
        lasts = _from_pool(ctx, "last", P.pool("last_names"))
    return kernel_ops.template_strings(
        ["", ".", "", "@", ""],
        [(0, 0), (1, 0), (2, 0), (3, 0)],
        [
            _slug(firsts),
            _slug(lasts),
            arrow_array(_ints(ctx, "suffix", 1, 999)),
            _host(ctx, spec, "email_domains"),
        ],
        ctx.n_rows,
    )


def _phone(ctx: GenerationContext, spec: Mapping[str, Any]) -> pa.Array:
    area, exchange = _ints(ctx, "area", 200, 999), _ints(ctx, "exchange", 200, 999)
    if spec.get("range", "reserved") == "reserved":
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
    if spec.get("range", "reserved") == "reserved":
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


def _uri(ctx: GenerationContext, spec: Mapping[str, Any]) -> pa.Array:
    return kernel_ops.template_strings(
        ["https://", "/", ""],
        [(0, 0), (1, 0)],
        [_host(ctx, spec, "uri_domains"), _from_pool(ctx, "path", P.pool("uri_paths"))],
        ctx.n_rows,
    )


def _company_email(ctx: GenerationContext, spec: Mapping[str, Any]) -> pa.Array:
    stems = P._company_stems()
    tld = ".com" if spec.get("domains", "reserved") == "realistic" else ".example"
    return kernel_ops.template_strings(
        ["", ".", "@", tld],
        [(0, 0), (1, 0), (2, 0)],
        [
            _slug(_from_pool(ctx, "first", P.pool("first_names"))),
            _slug(_from_pool(ctx, "last", P.pool("last_names"))),
            _from_pool(ctx, "company", stems),
        ],
        ctx.n_rows,
    )


STEPWISE = {
    "email": _email,
    "phone_number": _phone,
    "ssn": _ssn,
    "uri": _uri,
    "company_email": _company_email,
}
MODES: dict[str, list[dict[str, str]]] = {
    "email": [{}, {"domains": "reserved"}, {"domains": "realistic"}],
    "uri": [{}, {"domains": "reserved"}, {"domains": "realistic"}],
    "company_email": [{}, {"domains": "reserved"}, {"domains": "realistic"}],
    "phone_number": [{}, {"range": "reserved"}, {"range": "assignable"}],
    "ssn": [{}, {"range": "reserved"}, {"range": "assignable"}],
}
CASES = [(p, m) for p, modes in MODES.items() for m in modes]


@pytest.fixture(params=["rust", "python"])
def kernel(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    assert dispatch.kernel_name() == request.param
    yield request.param
    monkeypatch.undo()
    dispatch.reset()


@pytest.mark.parametrize(("provider", "mode"), CASES, ids=[f"{p}-{m}" for p, m in CASES])
@pytest.mark.parametrize(("n", "start"), [(0, 0), (1, 0), (997, 3), (70_001, 12_345)])
def test_fused_provider_equals_the_stepwise_route(kernel, provider, mode, n, start):
    spec = {"provider": provider, **mode}
    for strategy in (P.Native(), P.Faker()):
        fused = strategy.generate(spec, _ctx(n, start))
        want = STEPWISE[provider](_ctx(n, start), spec)
        assert fused.type == pa.string()
        assert fused.equals(want), (kernel, provider, mode)


@pytest.mark.parametrize("domains", ["reserved", "realistic"])
def test_email_from_the_rows_names_equals_the_stepwise_route(kernel, domains):
    firsts = pa.array(["Ann Marie", "Bo", None, "Émile", "Zoë"] * 41)
    lasts = pa.array(["De La Cruz", "Li", "Ng", "O Brien", "Ålund"] * 41)
    columns = {"first_name": firsts, "last_name": lasts}
    spec = {"provider": "email", "domains": domains}
    fused = P.Native().generate(spec, _ctx(len(firsts), 7, columns))
    assert fused.equals(_email(_ctx(len(firsts), 7, columns), spec))


def test_reserved_values_cannot_belong_to_anyone(kernel):
    ctx = _ctx(5_000, 0)
    phones = P.Native().generate({"provider": "phone_number"}, ctx).to_pylist()
    assert all(p[6:11] == "555-0" and 100 <= int(p[-4:]) <= 199 for p in phones)
    ssns = P.Native().generate({"provider": "ssn"}, ctx).to_pylist()
    assert all(900 <= int(s[:3]) <= 999 for s in ssns)
    hosts = {e.split("@")[1] for e in P.Native().generate({"provider": "email"}, ctx).to_pylist()}
    assert hosts == set(P.RESERVED_DOMAINS)


@pytest.mark.parametrize(
    ("provider", "key", "value"),
    [("email", "domains", "real"), ("uri", "domains", ""), ("ssn", "range", "any")],
)
def test_an_unknown_mode_is_an_error(provider, key, value):
    with pytest.raises(StrategyError, match=f"takes {key}"):
        P.Native().generate({"provider": provider, key: value}, _ctx(3, 0))

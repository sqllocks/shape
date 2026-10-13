"""The ``ipv4`` and ``ipv6`` providers never give a public address (``native`` 2, ``faker`` 3).

``ipv4`` draws from the RFC 5737 documentation blocks by default and from the RFC 1918 private
blocks with ``"range": "private"``; ``ipv6`` draws from the RFC 3849 documentation prefix. Every
value is inside its blocks at every scale preset, in both kernels; the same seed gives the same
values; and a spec pinned at ``native`` 1 or ``faker`` 1 or 2 still gives the old addresses
(dataset ids recorded with the code before this change).
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterator
from typing import Any

import pytest

from shape.builtins.strategies import providers
from shape.generation.engine import Engine
from shape.generation.schema import GenSchema
from shape.generation.strategy_kit import StrategyError
from shape.kernel import dispatch
from shape.repro import dataset_id

DOCUMENTATION = [
    ipaddress.ip_network(n) for n in ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24")
]
PRIVATE = [ipaddress.ip_network(n) for n in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")]
IPV6_DOCUMENTATION = ipaddress.ip_network("2001:db8::/32")
PRESETS = {"small": 1_000, "medium": 10_000, "large": 100_000}

# The dataset id of ``doc(strategy, "ipv4", 2_000)`` at its seed, recorded with the code before
# this change in both kernel modes: a pinned old version still gives these values.
BEFORE_IPV4_IDS = {
    "native": "sha256:88cc300cf4866810afc181316a57c28f25f92c9a4b36a2af10998a7418d2b7ee",
    "faker": "sha256:88cc300cf4866810afc181316a57c28f25f92c9a4b36a2af10998a7418d2b7ee",
}


def doc(
    strategy: str,
    provider: str,
    rows: int = 300,
    *,
    options: dict[str, Any] | None = None,
    **top: Any,
) -> dict[str, Any]:
    generator = {"strategy": strategy, "provider": provider, **(options or {})}
    return {
        "schema_version": 1,
        "model": {"name": "ips", "seed": 7},
        "tables": {
            "t": {
                "name": "t",
                "primary_key": ["id"],
                "columns": {
                    "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}},
                    "ip": {"name": "ip", "type": "string", "generator": generator},
                },
            }
        },
        "generation": {
            "scale": "small",
            "scales": {preset: {"t": n} for preset, n in PRESETS.items()} | {"s": {"t": rows}},
        },
        **top,
    }


def column(document: dict[str, Any], **engine: Any) -> list[str]:
    engine.setdefault("scale", "s")
    values: list[str] = (
        Engine(GenSchema.from_dict(document), **engine).generate().tables["t"]["ip"].to_pylist()
    )
    return values


def ids(document: dict[str, Any], **engine: Any) -> str:
    engine.setdefault("scale", "s")
    return dataset_id(Engine(GenSchema.from_dict(document), **engine).generate().tables)


def inside(values: list[str], blocks: list[Any]) -> bool:
    return all(any(ipaddress.ip_address(v) in block for block in blocks) for v in values)


@pytest.fixture(params=["rust", "python"])
def kernel(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    if request.param == "rust":
        pytest.importorskip("shape._kernel")
    monkeypatch.setenv("SHAPE_KERNEL", request.param)
    dispatch.reset()
    assert dispatch.kernel_name() == request.param
    yield request.param
    dispatch.reset()


@pytest.mark.parametrize("strategy", ["native", "faker"])
@pytest.mark.parametrize("preset", list(PRESETS))
def test_every_ipv4_is_a_documentation_address_at_every_scale_preset(
    kernel: str, strategy: str, preset: str
) -> None:
    values = column(doc(strategy, "ipv4"), scale=preset)
    assert len(values) == PRESETS[preset]
    assert inside(values, DOCUMENTATION)
    assert all(1 <= int(v.rsplit(".", 1)[1]) <= 254 for v in values)
    # every block is used
    assert {next(b for b in DOCUMENTATION if ipaddress.ip_address(v) in b) for v in values} == set(
        DOCUMENTATION
    )


@pytest.mark.parametrize("strategy", ["native", "faker"])
@pytest.mark.parametrize("preset", list(PRESETS))
def test_every_private_ipv4_is_an_rfc_1918_address_at_every_scale_preset(
    kernel: str, strategy: str, preset: str
) -> None:
    values = column(doc(strategy, "ipv4", options={"range": "private"}), scale=preset)
    assert len(values) == PRESETS[preset]
    assert inside(values, PRIVATE)
    assert all(1 <= int(v.rsplit(".", 1)[1]) <= 254 for v in values)
    assert {next(b for b in PRIVATE if ipaddress.ip_address(v) in b) for v in values} == set(
        PRIVATE
    )


@pytest.mark.parametrize("strategy", ["native", "faker"])
@pytest.mark.parametrize("preset", list(PRESETS))
def test_every_ipv6_is_in_the_documentation_prefix_at_every_scale_preset(
    kernel: str, strategy: str, preset: str
) -> None:
    values = column(doc(strategy, "ipv6"), scale=preset)
    assert len(values) == PRESETS[preset]
    assert all(ipaddress.ip_address(v) in IPV6_DOCUMENTATION for v in values)
    assert all(len(v) == 38 and v.startswith("2001:db8:") for v in values)
    assert len(set(values)) == len(values)


@pytest.mark.parametrize(
    ("provider", "blocks"), [("ipv4_public", DOCUMENTATION), ("ipv4_private", PRIVATE)]
)
def test_the_faker_packages_ipv4_providers_stay_in_the_same_blocks(
    provider: str, blocks: list[Any]
) -> None:
    assert inside(column(doc("faker", provider, 2_000)), blocks)


@pytest.mark.parametrize("strategy", ["native", "faker"])
@pytest.mark.parametrize(
    ("provider", "options"), [("ipv4", {}), ("ipv4", {"range": "private"}), ("ipv6", {})]
)
def test_the_same_seed_gives_the_same_addresses_for_any_chunking(
    kernel: str, strategy: str, provider: str, options: dict[str, str]
) -> None:
    document = doc(strategy, provider, 5_000, options=options)
    first = ids(document)
    assert ids(document) == first
    assert ids(document, chunk_rows=777) == first
    other = doc(strategy, provider, 5_000, options=options)
    other["model"]["seed"] = 8
    assert ids(other) != first


@pytest.mark.parametrize("strategy", ["native", "faker"])
def test_the_run_switch_does_not_make_an_address_public(strategy: str) -> None:
    document = doc(strategy, "ipv4", 1_000)
    assert column(document, identifiers="realistic") == column(document)


@pytest.mark.parametrize("strategy", ["native", "faker"])
@pytest.mark.parametrize(
    ("provider", "bad"), [("ipv4", "assignable"), ("ipv4", "public"), ("ipv6", "private")]
)
def test_any_other_range_is_refused(strategy: str, provider: str, bad: str) -> None:
    with pytest.raises(StrategyError, match=f"takes range .* not '{bad}'"):
        column(doc(strategy, provider, options={"range": bad}))


@pytest.mark.parametrize(("strategy", "old"), [("native", 1), ("faker", 1), ("faker", 2)])
def test_a_pinned_old_version_still_gives_the_old_addresses(
    kernel: str, strategy: str, old: int
) -> None:
    pinned = doc(strategy, "ipv4", 2_000, generators={strategy: old, "sequence": 1})
    assert ids(pinned) == BEFORE_IPV4_IDS[strategy]
    values = column(pinned)
    # the old form: any address with the first and last octet in 1..254, public ones included
    assert not inside(values, DOCUMENTATION + PRIVATE)
    assert ids(doc(strategy, "ipv4", 2_000)) != BEFORE_IPV4_IDS[strategy]


def test_native_1_has_no_ipv6() -> None:
    pinned = doc("native", "ipv6", generators={"native": 1, "sequence": 1})
    with pytest.raises(StrategyError, match="does not handle provider 'ipv6'"):
        column(pinned)


def test_the_versions() -> None:
    from shape.generation import versions

    assert versions.supported(providers.Native()) == (1, 2)
    assert versions.supported(providers.Faker()) == (1, 3)
    engine = Engine(GenSchema.from_dict(doc("native", "ipv4")), scale="s")
    assert engine.generator_versions == {"native": 2, "sequence": 1}

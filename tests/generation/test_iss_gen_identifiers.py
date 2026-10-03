"""ISS-gen #11 and #12: identifier providers produce values that cannot belong to a real person.

By default ``email``, ``company_email`` and ``uri`` use RFC 2606 reserved hosts, ``ssn`` uses the
9xx areas and ``phone_number`` the 555-01xx lines; ``"domains": "realistic"`` and
``"range": "assignable"`` give the baseline's realistic values. Every default test fails on the old
code (real mail providers, areas 1 to 899)."""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

import pytest

from shape.builtins.strategies import providers
from shape.builtins.strategies.providers import RESERVED_DOMAINS
from shape.generation.engine import Engine
from shape.generation.schema import GenSchema

S1 = Path(__file__).resolve().parents[2] / "benchmarks" / "vs_spindle" / "strategy_1to1"
sys.path.insert(0, str(S1))
import identifier_differences as differences  # noqa: E402

REAL_EMAIL_DOMAINS = set(providers.pool("email_domains").to_pylist())


def _values(strategy: str, provider: str, n: int = 4000, **options: Any) -> list[str]:
    doc = {
        "schema_version": 1,
        "model": {"name": "t", "seed": 5},
        "tables": {
            "t": {
                "name": "t",
                "primary_key": ["id"],
                "columns": {
                    "id": {"name": "id", "type": "integer", "generator": {"strategy": "sequence"}},
                    "x": {
                        "name": "x",
                        "type": "string",
                        "generator": {"strategy": strategy, "provider": provider, **options},
                    },
                },
            }
        },
        "relationships": [],
        "generation": {"scale": "s", "scales": {"s": {"t": n}}},
    }
    return Engine(GenSchema.from_dict(doc), seed=5).generate().tables["t"]["x"].to_pylist()


@pytest.mark.parametrize("strategy", ["native", "faker"])
def test_email_uses_reserved_domains_by_default(strategy: str) -> None:
    domains = {v.split("@")[1] for v in _values(strategy, "email")}
    assert domains == set(RESERVED_DOMAINS)
    assert not domains & REAL_EMAIL_DOMAINS


def test_company_email_uses_the_reserved_example_tld() -> None:
    values = _values("native", "company_email")
    assert all(re.fullmatch(r"[a-z']+\.[a-z']+@[a-z0-9&'-]+\.example", v) for v in values)


def test_uri_uses_reserved_hosts() -> None:
    hosts = {v.split("/")[2] for v in _values("native", "uri")}
    assert hosts == set(RESERVED_DOMAINS)


def test_ssn_is_never_issued_by_default() -> None:
    values = _values("native", "ssn", n=20_000)
    areas = {int(v[:3]) for v in values}
    assert min(areas) >= 900 and max(areas) <= 999
    assert all(re.fullmatch(r"9\d\d-\d\d-\d{4}", v) for v in values)
    assert all(1 <= int(v[4:6]) <= 99 and 1 <= int(v[7:]) <= 9999 for v in values)


def test_phone_numbers_are_the_fictional_555_lines_by_default() -> None:
    values = _values("native", "phone_number")
    assert all(re.fullmatch(r"\([2-9]\d\d\) 555-01\d\d", v) for v in values)
    assert len({v[-4:] for v in values}) == 100


def test_the_opt_in_gives_the_realistic_values() -> None:
    domains = {v.split("@")[1] for v in _values("native", "email", domains="realistic")}
    assert len(domains) > 20 and domains <= REAL_EMAIL_DOMAINS
    areas = {int(v[:3]) for v in _values("native", "ssn", range="assignable")}
    assert max(areas) <= 899 and 666 not in areas
    assert all(
        re.fullmatch(r"\([2-9]\d\d\) [2-9]\d\d-[1-9]\d{3}", v)
        for v in _values("native", "phone_number", range="assignable")
    )
    assert all(v.endswith(".com") for v in _values("native", "company_email", domains="realistic"))


@pytest.mark.parametrize(
    ("provider", "options"),
    [("email", {"domains": "gmail"}), ("ssn", {"range": "all"}), ("uri", {"domains": 1})],
)
def test_an_unknown_mode_names_the_column(provider: str, options: dict[str, Any]) -> None:
    with pytest.raises(ValueError, match=rf"provider '{provider}'.*t\.x"):
        _values("native", provider, **options)


def test_the_allow_list_names_exactly_the_providers_with_an_opt_in() -> None:
    assert set(differences.DELIBERATE) == set(differences.REALISTIC_OPTIONS)
    assert all(len(reason) > 40 for reason in differences.DELIBERATE.values())

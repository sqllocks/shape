"""The composites the harness covers, and how the baseline builds each (P6-01e).

A composite is named by a harness id, ``composite_<spec>``: ``<spec>`` is a preset name
(``composite_enterprise``) or domains joined by ``-`` for an ad-hoc combination
(``composite_retail-hr-financial``). The baseline side runs in its venv (``baseline_domain``);
the id is also the ``--domain`` of ``dump_schema.py``, ``domain_1to1/generate.py`` and
``domain_1to1/verify.py``. Standard library only until ``baseline_domain`` is called.
"""

from __future__ import annotations

import sys
from typing import Any

import _refpkg
from paths import REFENGINE_ROOT

PREFIX = "composite_"
PRESETS = (
    "enterprise",
    "healthcare_system",
    "smart_factory",
    "digital_commerce",
    "campus",
    "telecom_bundle",
)
# Ad-hoc combinations: the default shared-entity mappings (no preset), a name with underscores,
# a text primary key (capital_markets keys its tables by ticker).
ADHOC = (
    "retail+hr+financial",
    "supply_chain+real_estate+manufacturing",
    "capital_markets+marketing+education",
)


def specs() -> list[str]:
    return [*PRESETS, *ADHOC]


def harness_id(spec: str) -> str:
    return PREFIX + spec.replace("+", "-")


def is_composite(domain: str) -> bool:
    return domain.startswith(PREFIX)


def spec_of(domain: str) -> str:
    """The preset name or ``a+b+c`` that a harness id stands for."""
    body = domain[len(PREFIX) :]
    return body if body in PRESETS else body.replace("-", "+")


def children(spec: str) -> list[str]:
    """The domains of a composite, in the baseline's order (RefEngine venv for a preset)."""
    if spec in PRESETS:
        sys.path.insert(0, str(REFENGINE_ROOT))
        get_preset = _refpkg.mod("presets").get_preset

        return list(get_preset(spec).domains)
    return [part.strip() for part in spec.split("+")]


def baseline_domain(spec: str) -> Any:
    """The baseline's ``CompositeDomain`` for a preset name or ``a+b+c`` (RefEngine venv)."""
    sys.path.insert(0, str(REFENGINE_ROOT))
    _resolve_domain = _refpkg.mod("cli")._resolve_domain
    CompositeDomain = _refpkg.mod("domains.composite").CompositeDomain
    get_preset = _refpkg.mod("presets").get_preset

    shared = None
    if spec in PRESETS:
        preset = get_preset(spec)
        names, shared = list(preset.domains), preset.shared_entities or None
    else:
        names = [part.strip() for part in spec.split("+")]
    return CompositeDomain(
        domains=[_resolve_domain(n, "3nf") for n in names], shared_entities=shared
    )

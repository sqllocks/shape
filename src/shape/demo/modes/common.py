"""What the three modes share: the domains of a run, the scale it maps to, the schema."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from shape.demo.catalog import ScenarioMeta
from shape.demo.errors import DemoError
from shape.demo.params import DemoParams

if TYPE_CHECKING:
    from shape.generation.schema import GenSchema


def rows_to_scale(rows: int) -> str:
    """The scale preset a row count maps to."""
    if rows <= 2_000:
        return "small"
    if rows <= 50_000:
        return "medium"
    if rows <= 500_000:
        return "large"
    return "xlarge"


def resolve_domains(params: DemoParams, meta: ScenarioMeta | None) -> list[str]:
    """The domains a run generates: ``--domains``, else ``--domain``, else the scenario's own."""
    if params.domains:
        names = [d.strip() for d in params.domains if d.strip()]
    elif params.domain:
        names = [params.domain]
    elif meta is not None and meta.domains:
        names = list(meta.domains)
    else:
        names = ["retail"]
    if not names:
        raise DemoError("no domain to generate: give --domain, --domains or a scenario")
    labels = [domain_label(n) for n in names]
    if len(set(labels)) != len(labels):
        raise DemoError(f"a domain is named twice: {', '.join(names)}")
    return names


def domain_label(domain: str) -> str:
    """The short name of a domain in folders, schemas and table prefixes: the domain's own name,
    or for a schema file the file's name without ``.json`` and ``.schema``."""
    if domain.lower().endswith(".json") or Path(domain).is_file():
        stem = Path(domain).name
        stem = re.sub(r"(\.schema)?\.json$", "", stem, flags=re.IGNORECASE) or "schema"
        return re.sub(r"\W+", "_", stem).strip("_") or "schema"
    return re.sub(r"\W+", "_", domain).strip("_") or "domain"


def load_schema(domain: str) -> GenSchema:
    """The generation schema of a domain name or a schema file; a clear error when missing."""
    from shape.cli.generation import load_target
    from shape.generation.domains import DomainNotFoundError

    try:
        return load_target(domain)
    except DomainNotFoundError as exc:
        raise DemoError(str(exc)) from exc


def check_scale(schema: GenSchema, scale: str, domain: str) -> None:
    presets = schema.generation.scales
    if presets and scale not in presets:
        raise DemoError(
            f"domain {domain!r} has no {scale!r} scale (it has: {', '.join(presets)}); "
            "ask for fewer rows"
        )

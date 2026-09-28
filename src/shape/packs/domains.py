"""First-class reusable domain definitions and registry."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class DomainField:
    name: str
    logical_type: str
    required: bool = True
    semantic_type: str | None = None


@dataclass(frozen=True, slots=True)
class DomainRelationship:
    determinant: tuple[str, ...]
    dependent: str
    kind: str = "functional"


@dataclass(frozen=True, slots=True)
class DomainDefinition:
    name: str
    version: str
    fields: tuple[DomainField, ...]
    relationships: tuple[DomainRelationship, ...] = ()
    constraints: Mapping[str, Any] = field(default_factory=dict)
    description: str = ""


class DomainRegistry:
    def __init__(self):
        self._domains = {}
        self._generators = {}

    def register(self, domain: DomainDefinition, generator: Callable | None = None):
        key = (domain.name, domain.version)
        if key in self._domains:
            raise ValueError(f"domain already registered: {key}")
        self._domains[key] = domain
        if generator:
            self._generators[key] = generator
        return domain

    def get(self, name, version=None):
        hits = [
            (k, d)
            for k, d in self._domains.items()
            if k[0] == name and (version is None or k[1] == version)
        ]
        if not hits:
            raise KeyError(name)
        if version is None:
            hits.sort(key=lambda x: x[0][1])
        return hits[-1][1]

    def generate(self, name, n, *, version=None, **kwargs):
        d = self.get(name, version)
        fn = self._generators.get((d.name, d.version))
        if fn is None:
            raise ValueError(f"domain {d.name}@{d.version} has no generator")
        return fn(n=n, **kwargs)

    def list(self):
        return tuple(sorted(self._domains))


US_ADDRESS = DomainDefinition(
    "us_address",
    "1.0.0",
    (
        DomainField("address_line_1", "string", True, "street_address"),
        DomainField("city", "string", True, "city"),
        DomainField("county", "string", True, "county"),
        DomainField("state", "string", True, "us_state"),
        DomainField("postal_code", "string", True, "us_zip5"),
        DomainField("country", "string", True, "country"),
        DomainField("latitude", "float64", True, "latitude"),
        DomainField("longitude", "float64", True, "longitude"),
    ),
    (
        DomainRelationship(("postal_code",), "city"),
        DomainRelationship(("postal_code",), "state"),
        DomainRelationship(("postal_code",), "county"),
        DomainRelationship(("postal_code",), "latitude", "geographic"),
        DomainRelationship(("postal_code",), "longitude", "geographic"),
    ),
    {
        "country": "US",
        "postal_code_format": "ZIP5",
        "latitude_range": (-90, 90),
        "longitude_range": (-180, 180),
    },
    "Coherent US postal address/location domain.",
)


def domain_to_dict(d: DomainDefinition):
    return {
        "name": d.name,
        "version": d.version,
        "description": d.description,
        "fields": [
            {
                "name": x.name,
                "logical_type": x.logical_type,
                "required": x.required,
                "semantic_type": x.semantic_type,
            }
            for x in d.fields
        ],
        "relationships": [
            {"determinant": list(x.determinant), "dependent": x.dependent, "kind": x.kind}
            for x in d.relationships
        ],
        "constraints": dict(d.constraints),
    }


def domain_from_dict(x):
    return DomainDefinition(
        x["name"],
        x["version"],
        tuple(DomainField(**f) for f in x.get("fields", ())),
        tuple(
            DomainRelationship(tuple(r["determinant"]), r["dependent"], r.get("kind", "functional"))
            for r in x.get("relationships", ())
        ),
        x.get("constraints", {}),
        x.get("description", ""),
    )


@dataclass(frozen=True, slots=True)
class DomainIssue:
    path: str
    message: str


def validate_domain(d: DomainDefinition):
    issues = []
    names = [f.name for f in d.fields]
    if not d.name:
        issues.append(DomainIssue("name", "required"))
    if not d.version:
        issues.append(DomainIssue("version", "required"))
    if len(names) != len(set(names)):
        issues.append(DomainIssue("fields", "duplicate field names"))
    known = set(names)
    for i, r in enumerate(d.relationships):
        for x in r.determinant:
            if x not in known:
                issues.append(DomainIssue(f"relationships.{i}.determinant", f"unknown field {x}"))
        if r.dependent not in known:
            issues.append(
                DomainIssue(f"relationships.{i}.dependent", f"unknown field {r.dependent}")
            )
    return tuple(issues)


def compose_domains(name, version, *domains, description=""):
    fields = {}
    rels = []
    constraints = {}
    for d in domains:
        for f in d.fields:
            if f.name in fields and fields[f.name] != f:
                raise ValueError(f"incompatible field {f.name}")
            fields[f.name] = f
        rels.extend(d.relationships)
        constraints.update(d.constraints)
    out = DomainDefinition(
        name, version, tuple(fields.values()), tuple(dict.fromkeys(rels)), constraints, description
    )
    issues = validate_domain(out)
    if issues:
        raise ValueError(issues)
    return out


def extend_domain(
    base: DomainDefinition,
    *,
    name=None,
    version=None,
    fields=(),
    relationships=(),
    constraints=None,
    description=None,
):
    by = {f.name: f for f in base.fields}
    for f in fields:
        by[f.name] = f
    out = DomainDefinition(
        name or base.name,
        version or base.version,
        tuple(by.values()),
        base.relationships + tuple(relationships),
        {**dict(base.constraints), **dict(constraints or {})},
        base.description if description is None else description,
    )
    issues = validate_domain(out)
    if issues:
        raise ValueError(issues)
    return out


def save_domain(domain, path):
    import json

    issues = validate_domain(domain)
    if issues:
        raise ValueError(issues)
    from pathlib import Path

    Path(path).write_text(json.dumps(domain_to_dict(domain), sort_keys=True, indent=2) + "\n")


def load_domain(path):
    import json
    from pathlib import Path

    d = domain_from_dict(json.loads(Path(path).read_text()))
    issues = validate_domain(d)
    if issues:
        raise ValueError(issues)
    return d


def test_domain(domain: DomainDefinition, rows):
    issues = list(validate_domain(domain))
    required = {f.name for f in domain.fields if f.required}
    for i, row in enumerate(rows):
        missing = required - set(row if isinstance(row, dict) else vars(row))
        if missing:
            issues.append(DomainIssue(f"rows.{i}", f"missing required fields: {sorted(missing)}"))
    return tuple(issues)

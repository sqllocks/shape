"""Cross-domain composition: several domains generated as one dataset (P6-01e).

A composite merges the generation schemas of its child domains into one schema. Table names are
prefixed with the domain (``retail_customer``, ``hr_employee``), so nothing collides, and
references inside a domain follow the prefix. *Shared entities* (a person, a location, an
organisation) link the domains: a bridge column on one domain's table points at the primary
domain's table, and the engine fills it like any other foreign key.

The domains name their shared-entity tables and the named presets (``shape presets``) through an
optional ``composition()`` method on their ``shape.domains`` plugin object; this module holds no
domain knowledge. ``composition()`` returns a :class:`Composition`.

Stable interface: ``Composition``, ``EntityMapping``, ``Preset``, ``compose``, ``composition``,
``get_preset``, ``is_composite``, ``preset_names``, ``resolve`` and ``ResolvedComposite``.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from shape.errors import ShapeError
from shape.generation.domains import GROUP, LoadedDomain, domain_names, load_domain
from shape.generation.reference import register_dataset
from shape.generation.schema import GenSchema
from shape.plugins.host import PluginHost, default_host

MODES = ("3nf",)
CONCEPTS = ("person", "location", "organization")
_DATASET_STRATEGIES = ("reference_data", "record_sample", "record_field")


class CompositeError(ShapeError, ValueError):
    """A composite cannot be built: an unknown preset or domain, a repeated domain."""


@dataclass(frozen=True, slots=True)
class EntityMapping:
    """The table of ``domain`` that plays a shared concept, and its primary-key column."""

    domain: str
    table: str
    pk_column: str


@dataclass(frozen=True, slots=True)
class Preset:
    """A named composite: its ``domains`` and, optionally, explicit ``shared_entities``::

    {"person": {"primary": "hr.employee", "links": {"retail": "customer.customer_id"}}}
    """

    name: str
    description: str
    domains: tuple[str, ...]
    shared_entities: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Composition:
    """What the installed domains offer for composing: the named ``presets`` and, per concept
    (``person``, ``location``, ``organization``), the tables that play it. The first mapping of a
    concept whose domain is in a composite is the concept's primary."""

    presets: tuple[Preset, ...] = ()
    mappings: Mapping[str, tuple[EntityMapping, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ResolvedComposite:
    """A composite ready to generate: its schema (reference data already registered)."""

    name: str
    domains: tuple[str, ...]
    schema: GenSchema
    preset: Preset | None


def composition(host: PluginHost | None = None) -> Composition:
    """The presets and shared-entity mappings of the installed domains (first definition of a
    preset name wins; a concept's mappings are those of the first domain that offers any)."""
    plugins = host or default_host()
    presets: dict[str, Preset] = {}
    mappings: dict[str, tuple[EntityMapping, ...]] = {}
    for name in plugins.names(GROUP):
        offer = getattr(plugins.get(GROUP, name), "composition", None)
        if offer is None:
            continue
        found: Composition = offer()
        for preset in found.presets:
            presets.setdefault(preset.name, preset)
        for concept, entries in found.mappings.items():
            mappings.setdefault(concept, tuple(entries))
    return Composition(tuple(presets.values()), mappings)


def preset_names(host: PluginHost | None = None) -> list[str]:
    return [p.name for p in composition(host).presets]


def get_preset(name: str, host: PluginHost | None = None) -> Preset:
    for preset in composition(host).presets:
        if preset.name == name:
            return preset
    known = ", ".join(preset_names(host)) or "none installed"
    raise CompositeError(f"unknown composite preset {name!r} (the presets are: {known})")


def is_composite(spec: str, host: PluginHost | None = None) -> bool:
    """True when ``spec`` names a composite preset or lists domains joined by ``+`` (a domain's
    own name is not a composite)."""
    return "+" in spec or (
        spec not in domain_names(host or default_host()) and spec in preset_names(host)
    )


def resolve(spec: str, *, host: PluginHost | None = None) -> ResolvedComposite:
    """A composite from a preset name or a ``+`` list of domains (``retail+hr+financial``):
    builds the merged schema and registers the children's reference data."""
    plugins = host or default_host()
    known = composition(plugins)
    preset = next((p for p in known.presets if p.name == spec), None)
    if preset is not None:
        names, shared = list(preset.domains), dict(preset.shared_entities)
    else:
        names, shared = [part.strip() for part in spec.split("+")], {}
    installed = domain_names(plugins)
    for name in names:
        if name not in installed:
            what = "composite preset or domain" if len(names) == 1 else "domain"
            options = f"presets: {', '.join(p.name for p in known.presets) or 'none'}"
            raise CompositeError(
                f"no {what} named {name!r} (domains: {', '.join(installed) or 'none installed'}; "
                f"{options}); name domains joined by '+', for example retail+hr"
            )
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise CompositeError(
            f"duplicate domains in composition: {', '.join(dupes)}; each domain may appear once"
        )
    loaded = {name: load_domain(name, host=plugins) for name in names}
    schema, datasets = compose(loaded, shared or None, known.mappings)
    for dataset, table in datasets.items():
        register_dataset(dataset, table)
    return ResolvedComposite(schema.model.name, tuple(names), schema, preset)


# ---- the merge ----------------------------------------------------------------------------


def compose(
    domains: Mapping[str, LoadedDomain],
    shared_entities: Mapping[str, Mapping[str, Any]] | None = None,
    mappings: Mapping[str, Sequence[EntityMapping]] | None = None,
) -> tuple[GenSchema, dict[str, Any]]:
    """Merge ``domains`` (name to loaded domain, in composition order) into one schema.

    Returns the schema and the reference datasets it reads (name to table). A dataset two domains
    both define under one name with different content is kept apart: each domain's references
    read its own, as ``<domain>.<name>``."""
    names = list(domains)
    if not names:
        raise CompositeError("a composite needs at least one domain")
    if len(set(names)) != len(names):
        dupes = sorted({n for n in names if names.count(n) > 1})
        raise CompositeError(
            f"duplicate domains in composition: {', '.join(dupes)}; each domain may appear once"
        )
    docs = {n: copy.deepcopy(domains[n].schema.to_dict()) for n in names}
    renames, datasets = _datasets(domains)
    tables: dict[str, Any] = {}
    relationships: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    for name, doc in docs.items():
        local = set(doc["tables"])
        for table_name, table in doc["tables"].items():
            prefixed = f"{name}_{table_name}"
            columns = {
                col: _rewrite_column(column, name, local, renames.get(name, {}))
                for col, column in table["columns"].items()
            }
            tables[prefixed] = {
                **table,
                "name": prefixed,
                "description": f"[{name}] {table.get('description', '')}",
                "primary_key": list(table["primary_key"]),
                "columns": columns,
            }
        for rel in doc["relationships"]:
            relationships.append(
                {
                    **rel,
                    "name": f"{name}_{rel['name']}",
                    "parent": f"{name}_{rel['parent']}",
                    "child": f"{name}_{rel['child']}",
                    "parent_columns": list(rel["parent_columns"]),
                    "child_columns": list(rel["child_columns"]),
                    "cardinality": dict(rel.get("cardinality") or {}),
                }
            )
        for rule in doc["business_rules"]:
            rules.append(_rewrite_rule(rule, name, local))
        if doc.get("correlated_columns"):
            raise CompositeError(
                f"domain {name!r} has correlated columns, which a composite cannot merge"
            )
    cross = _cross_relationships(names, shared_entities, mappings or {}, docs)
    relationships.extend(cross)
    _ensure_bridge_columns(tables, cross)
    first = docs[names[0]]["model"]
    starts = [
        d["model"]["date_range"]["start"]
        for d in docs.values()
        if d["model"]["date_range"].get("start")
    ]
    ends = [
        d["model"]["date_range"]["end"]
        for d in docs.values()
        if d["model"]["date_range"].get("end")
    ]
    label = "composite_" + "_".join(sorted(names))
    document = {
        "schema_version": next(iter(docs.values()))["schema_version"],
        "model": {
            "name": label,
            "description": f"Cross-domain composition of: {', '.join(names)}",
            "domain": "composite",
            "schema_mode": "3nf",
            "locale": first.get("locale", "en_US"),
            "seed": first.get("seed", 42),
            "date_range": {
                "start": min(starts) if starts else "2020-01-01",
                "end": max(ends) if ends else "2025-12-31",
            },
        },
        "tables": tables,
        "relationships": relationships,
        "business_rules": rules,
        "generation": _merge_generation(docs),
        "correlated_columns": {},
    }
    return GenSchema.from_dict(document), datasets


def _datasets(
    domains: Mapping[str, LoadedDomain],
) -> tuple[dict[str, dict[str, str]], dict[str, Any]]:
    """The registered reference datasets, and per domain the names that had to change."""
    claimed: dict[str, list[tuple[str, Any]]] = {}
    for name, loaded in domains.items():
        for dataset, table in loaded.definition.reference_data.items():
            claimed.setdefault(dataset, []).append((name, table))
    renames: dict[str, dict[str, str]] = {}
    out: dict[str, Any] = {}
    for dataset, owners in claimed.items():
        first = owners[0][1]
        if all(table is first or table.equals(first) for _, table in owners):
            out[dataset] = first
            continue
        for domain, table in owners:
            renames.setdefault(domain, {})[dataset] = f"{domain}.{dataset}"
            out[f"{domain}.{dataset}"] = table
    return renames, out


def _rewrite_generator(
    gen: dict[str, Any], domain: str, local: set[str], datasets: Mapping[str, str]
) -> None:
    strategy = gen.get("strategy")
    if strategy == "foreign_key":
        table, dot, column = str(gen.get("ref", "")).partition(".")
        if dot and table in local:
            gen["ref"] = f"{domain}_{table}.{column}"
    elif strategy == "lookup":
        if gen.get("source_table") in local:
            gen["source_table"] = f"{domain}_{gen['source_table']}"
    elif strategy == "computed":
        if gen.get("child_table") in local:
            gen["child_table"] = f"{domain}_{gen['child_table']}"
    elif strategy == "derived":
        table, dot, column = str(gen.get("source", "")).partition(".")
        if dot and table in local:
            gen["source"] = f"{domain}_{table}.{column}"
    elif strategy == "conditional":
        for key in ("true_generator", "false_generator"):
            sub = gen.get(key)
            if isinstance(sub, dict):
                _rewrite_generator(sub, domain, local, datasets)
    if strategy in _DATASET_STRATEGIES and gen.get("dataset") in datasets:
        gen["dataset"] = datasets[gen["dataset"]]


def _rewrite_column(
    column: dict[str, Any], domain: str, local: set[str], datasets: Mapping[str, str]
) -> dict[str, Any]:
    out = copy.deepcopy(column)
    _rewrite_generator(out["generator"], domain, local, datasets)
    return out


def _rewrite_rule(rule: dict[str, Any], domain: str, local: set[str]) -> dict[str, Any]:
    expression = str(rule.get("rule", ""))
    for table in sorted(local, key=len, reverse=True):
        expression = re.sub(rf"(?<![\w.]){re.escape(table)}\.", f"{domain}_{table}.", expression)
    table = rule.get("table")
    return {
        **rule,
        "name": f"{domain}_{rule['name']}",
        "rule": expression,
        "table": f"{domain}_{table}" if table else None,
    }


def _merge_generation(docs: Mapping[str, dict[str, Any]]) -> dict[str, Any]:
    """Scale presets are the union of the domains' (a domain without a preset uses its first),
    row counts are keyed by the prefixed table names."""
    scale_names = sorted({s for d in docs.values() for s in d["generation"]["scales"]})
    scales: dict[str, dict[str, int]] = {}
    for scale in scale_names:
        scales[scale] = {}
        for name, doc in docs.items():
            available = doc["generation"]["scales"]
            counts = available.get(scale) or next(iter(available.values()), {})
            for table, rows in counts.items():
                scales[scale][f"{name}_{table}"] = rows
    derived: dict[str, dict[str, Any]] = {}
    for name, doc in docs.items():
        for table, spec in doc["generation"]["derived_counts"].items():
            entry = dict(spec)
            if "per_parent" in entry:
                entry["per_parent"] = f"{name}_{entry['per_parent']}"
            derived[f"{name}_{table}"] = entry
    return {"scale": "small", "scales": scales, "derived_counts": derived, "output": {}}


def _primary_key_of(docs: Mapping[str, dict[str, Any]], domain: str, table: str) -> str:
    """The column an explicit link points at: ``<table>_id`` when the table has it, else its
    one-column primary key (capital_markets keys ``company`` by ``ticker``)."""
    spec = docs[domain]["tables"].get(table)
    if spec is None:
        raise CompositeError(f"domain {domain!r} has no table {table!r}")
    if f"{table}_id" in spec["columns"]:
        return f"{table}_id"
    if len(spec["primary_key"]) == 1:
        return str(spec["primary_key"][0])
    raise CompositeError(f"cannot tell which column of {domain}.{table} the link points at")


def _cross_relationships(
    names: Sequence[str],
    shared: Mapping[str, Mapping[str, Any]] | None,
    mappings: Mapping[str, Sequence[EntityMapping]],
    docs: Mapping[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    present = set(names)
    out: list[dict[str, Any]] = []
    if shared:
        for concept, config in shared.items():
            primary = str(config.get("primary", ""))
            if "." not in primary:
                continue
            domain, table = primary.split(".", 1)
            if domain not in present:
                continue
            for link_domain, spec in (config.get("links") or {}).items():
                if link_domain not in present or "." not in spec:
                    continue
                link_table, column = spec.split(".", 1)
                out.append(
                    {
                        "name": f"xdomain_{concept}_{domain}_to_{link_domain}",
                        "parent": f"{domain}_{table}",
                        "child": f"{link_domain}_{link_table}",
                        "parent_columns": [_primary_key_of(docs, domain, table)],
                        "child_columns": [column],
                        "type": "one_to_many",
                        "cardinality": {},
                        "optional": False,
                        "explicit": True,
                        "bridge": f"shared_{concept}_{domain}_{table}_id",
                    }
                )
        return out
    for concept in CONCEPTS:
        active = [m for m in mappings.get(concept, ()) if m.domain in present]
        if len(active) < 2:
            continue
        primary = active[0]
        for linked in active[1:]:
            out.append(
                {
                    "name": f"xdomain_{concept}_{primary.domain}_to_{linked.domain}",
                    "parent": f"{primary.domain}_{primary.table}",
                    "child": f"{linked.domain}_{linked.table}",
                    "parent_columns": [primary.pk_column],
                    "child_columns": [f"shared_{concept}_{primary.domain}_{primary.table}_id"],
                    "type": "one_to_many",
                    "cardinality": {},
                    "optional": True,
                }
            )
    return out


def _ensure_bridge_columns(tables: dict[str, Any], cross: list[dict[str, Any]]) -> None:
    """Add the foreign-key column each cross-domain relationship names on its child table.

    A link that names a column the child table already has (a primary key such as
    ``customer.customer_id``) cannot make that column a reference without breaking it: the
    column keeps its own values, and the link gets a bridge column of its own,
    ``shared_<concept>_<domain>_<table>_id`` (see ``docs/GENERATION_ENGINE.md``)."""
    for rel in cross:
        child = tables.get(rel["child"])
        parent = tables.get(rel["parent"])
        explicit = rel.pop("explicit", False)
        bridge_name = rel.pop("bridge", "")
        if child is None:
            continue
        if explicit and rel["child_columns"][0] in child["columns"]:
            rel["child_columns"] = [bridge_name]
        for parent_column, bridge in zip(rel["parent_columns"], rel["child_columns"], strict=True):
            if bridge in child["columns"]:
                continue
            column_type = "integer"
            if parent is not None and parent_column in parent["columns"]:
                column_type = parent["columns"][parent_column]["type"]
            child["columns"][bridge] = {
                "name": bridge,
                "type": column_type,
                "generator": {"strategy": "foreign_key", "ref": f"{rel['parent']}.{parent_column}"},
                "nullable": rel["optional"],
                "null_rate": 0.0,
                "max_length": None,
                "precision": None,
                "scale": None,
            }


__all__ = [
    "Composition",
    "CompositeError",
    "EntityMapping",
    "Preset",
    "ResolvedComposite",
    "compose",
    "composition",
    "get_preset",
    "is_composite",
    "preset_names",
    "resolve",
]

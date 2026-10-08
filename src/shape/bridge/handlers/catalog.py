"""``list``, ``describe``, ``dry_run``, ``validate`` and ``profile_info``: what Shape can generate
and whether a schema is sound. They call what ``shape list|describe|generate --dry-run|validate``
call."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from shape.bridge.context import Context
from shape.bridge.handlers.common import (
    ANY,
    BOOL,
    INT,
    NUM,
    STR,
    STRS,
    arr,
    check_profile,
    check_scale,
    domain_profiles,
    load_schema,
    mapping,
    nullable,
    obj,
)
from shape.bridge.protocol import BridgeError
from shape.bridge.spec import Arg, Command

_DOMAIN = Arg("string", "an installed domain (see `list`) or a generation schema file", True)
_MODE = Arg("string", "the schema mode of a domain", enum=("3nf", "star"))
_PROFILE = Arg("string", "a distribution profile of the domain (default: `default`)")
_SCALE = Arg("string", "a scale preset (see `describe`)")


# ---- list ---------------------------------------------------------------------------------


def cmd_list(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape import __version__
    from shape.generation.domains import (
        domain_description,
        domain_modes,
        domain_names,
        load_domain,
    )

    rows: list[dict[str, Any]] = []
    for name in domain_names():
        try:
            loaded = load_domain(name)
            # the domain's own description (one for every mode), else its schema's
            description = domain_description(name) or loaded.schema.model.description
            profiles = ["default", *sorted(loaded.definition.profiles)]
            modes = list(domain_modes(name))
        except Exception as exc:  # one broken domain must not hide the others
            ctx.warn("domain_load_failed", f"domain {name!r} did not load: {exc}")
            description, profiles, modes = "", ["default"], []
        rows.append(
            {"name": name, "description": description, "profiles": profiles, "modes": modes}
        )
    return {"version": __version__, "domains": rows, "count": len(rows)}


# ---- describe -----------------------------------------------------------------------------


def cmd_describe(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.cli.generation import _describe

    schema = load_schema(args)
    scale = args.get("scale")
    check_scale(schema, scale)
    d = _describe(schema, scale)
    parents: dict[str, list[str]] = {name: [] for name in d["tables"]}
    for rel in d["relationships"]:
        if rel["parent"] != rel["child"] and rel["parent"] not in parents.get(rel["child"], []):
            parents.setdefault(rel["child"], []).append(rel["parent"])

    def declared(name: str) -> list[dict[str, Any]]:
        """The columns in the order the schema declares them, which is the order of the
        generated table (the CLI lists them in the order they are computed)."""
        by_name = {c["name"]: c for c in d["tables"][name]["columns"]}
        return [by_name[c] for c in schema.tables[name].columns]

    tables = {
        name: {
            "description": schema.tables[name].description or "",
            "rows": t["rows"],
            "primary_key": t["primary_key"],
            "columns": declared(name),
            "column_count": len(t["columns"]),
            "dependencies": parents.get(name, []),
        }
        for name, t in d["tables"].items()
    }
    rules = [{"name": r.name, "rule": r.rule, "type": r.type} for r in schema.business_rules]
    return {
        "domain": str(args["domain"]),
        "mode": d["mode"],
        "name": d["name"],
        "description": d["description"],
        "scale": d["scale"],
        "table_count": len(tables),
        "tables": tables,
        "generation_order": list(tables),
        "relationships": [
            {k: r[k] for k in ("name", "parent", "child", "parent_columns", "child_columns")}
            for r in d["relationships"]
        ],
        "business_rules": rules,
        "scales": {name: dict(counts) for name, counts in schema.generation.scales.items()},
    }


# ---- dry_run ------------------------------------------------------------------------------


def cmd_dry_run(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.generation.engine import Engine

    schema = load_schema(args)
    scale = args.get("scale")
    check_scale(schema, scale)
    engine = Engine(schema, scale=scale)
    plan = engine.dry_run().to_dict()
    order = engine.order  # the order generation runs in (the plan lists it by dependency level)
    planned = {name: int(plan["tables"][name]["rows"]) for name in order}
    return {
        "domain": str(args["domain"]),
        "scale": plan["scale"],
        "generation_order": order,
        "planned_rows": planned,
        "total_rows": plan["total_rows"],
        "ok": plan["ok"],
        "estimated_bytes": plan["estimated_bytes"],
        "issues": plan["issues"],
        "missing_strategies": plan["missing_strategies"],
    }


# ---- validate -----------------------------------------------------------------------------


def cmd_validate(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    from shape.cli.validate import _load, _validate_contract, kind_of

    path = Path(args["schema_path"])
    doc = _load(path)
    kind = kind_of(doc)
    if kind is None:
        raise BridgeError(
            "input.invalid_schema",
            f"{path} is neither a Shape generation schema (schema_version, model, tables) "
            "nor a contract (name, fields)",
        )
    if kind == "contract":
        result, _code = _validate_contract(doc)
        return {
            "valid": result["valid"],
            "kind": "contract",
            "table_count": 0,
            "relationship_count": 0,
            "errors": [{"location": "", "message": m} for m in result.get("errors", [])],
            "warnings": [],
            "name": result.get("name"),
        }
    from shape.generation.schema import GenSchema, GenSchemaError

    try:
        schema = GenSchema.from_dict(doc)
    except GenSchemaError as exc:
        return {
            "valid": False,
            "kind": "generation-schema",
            "table_count": 0,
            "relationship_count": 0,
            "errors": [{"location": "", "message": str(exc)}],
            "warnings": [],
            "name": None,
        }
    issues = schema.validate()
    errors = [{"location": i.location, "message": i.message} for i in issues if i.level == "error"]
    warnings = [
        {"location": i.location, "message": i.message} for i in issues if i.level == "warning"
    ]
    return {
        "valid": not errors,
        "kind": "generation-schema",
        "table_count": len(schema.tables),
        "relationship_count": len(schema.relationships),
        "errors": errors,
        "warnings": warnings,
        "name": schema.model.name,
        "mode": schema.model.schema_mode,
    }


# ---- profile_info -------------------------------------------------------------------------


def _distributions(schema: Any) -> dict[str, Any]:
    """Every weighting a schema generates with, keyed ``table.column``: the weights of a weighted
    choice, the phases of a lifecycle, the skew of a foreign key or numeric distribution
    (``{"distribution": name, **parameters}``), and each profile of a temporal column
    (``table.column.month``, ``.day_of_week``, ``.hour_of_day``)."""
    out: dict[str, Any] = {}
    for tname, table in schema.tables.items():
        for cname, column in table.columns.items():
            gen, key = column.generator, f"{tname}.{cname}"
            if column.strategy == "weighted_enum":
                out[key] = dict(gen.get("values", {}))
            elif column.strategy == "lifecycle" and isinstance(gen.get("phases"), dict):
                out[key] = {"phases": [{"name": n, "weight": w} for n, w in gen["phases"].items()]}
            elif column.strategy in ("foreign_key", "distribution") and gen.get("distribution"):
                out[key] = {"distribution": gen["distribution"], **dict(gen.get("params") or {})}
            elif column.strategy == "temporal" and isinstance(gen.get("profiles"), dict):
                for name, weights in gen["profiles"].items():
                    out[f"{key}.{name}"] = weights
    return out


def cmd_profile_info(args: dict[str, Any], ctx: Context) -> dict[str, Any]:
    """The distribution weights and derived-count ratios a domain generates with, by
    ``table.column`` (see :func:`_distributions`) and by ``child_per_parent`` (derived counts)."""
    from shape.cli.generation import _is_file

    target = str(args["domain"])
    schema = load_schema({**args, "profile": None})
    profile = args.get("profile")
    check_profile(target, profile)
    available = ["default"] if _is_file(target) else domain_profiles(target)
    distributions = _distributions(schema)
    ratios: dict[str, float] = {}
    for tname, rule in schema.generation.derived_counts.items():
        if isinstance(rule, dict) and "ratio" in rule and "per_parent" in rule:
            ratios[f"{tname}_per_{rule['per_parent']}"] = float(rule["ratio"])
    return {
        "domain": target,
        "profile": profile or "default",
        "available_profiles": available,
        "distribution_keys": sorted(distributions),
        "distributions": distributions,
        "ratio_keys": sorted(ratios),
        "ratios": ratios,
    }


_COLUMN = obj({"name": STR, "type": STR, "nullable": BOOL}, {"strategy": nullable(STR)})
_REL = obj(
    {
        "name": STR,
        "parent": STR,
        "child": STR,
        "parent_columns": STRS,
        "child_columns": STRS,
    }
)
_ISSUE = obj({"location": STR, "message": STR})

COMMANDS = [
    Command(
        "list",
        "List the installed domains.",
        {},
        obj(
            {
                "version": STR,
                "domains": arr(
                    obj({"name": STR, "description": STR, "profiles": STRS, "modes": STRS})
                ),
                "count": INT,
            }
        ),
        cmd_list,
    ),
    Command(
        "describe",
        "A domain's or schema's tables, columns, relationships, rules and scale presets.",
        {"domain": _DOMAIN, "mode": _MODE, "scale": _SCALE, "profile": _PROFILE},
        obj(
            {
                "domain": STR,
                "mode": STR,
                "name": STR,
                "scale": STR,
                "table_count": INT,
                "tables": mapping(
                    obj(
                        {
                            "description": STR,
                            "primary_key": STRS,
                            "columns": arr(_COLUMN),
                            "column_count": INT,
                            "dependencies": STRS,
                        },
                        {"rows": nullable(INT)},
                    )
                ),
                "generation_order": STRS,
                "relationships": arr(_REL),
                "business_rules": arr(obj({"name": STR, "rule": STR, "type": STR})),
                "scales": mapping(mapping(INT)),
            }
        ),
        cmd_describe,
    ),
    Command(
        "dry_run",
        "Plan a run (row counts, order, problems) without generating a row.",
        {"domain": _DOMAIN, "scale": _SCALE, "mode": _MODE, "profile": _PROFILE},
        obj(
            {
                "domain": STR,
                "scale": STR,
                "generation_order": STRS,
                "planned_rows": mapping(INT),
                "total_rows": INT,
                "ok": BOOL,
            },
            {"estimated_bytes": INT, "issues": arr(ANY), "missing_strategies": STRS},
        ),
        cmd_dry_run,
    ),
    Command(
        "validate",
        "Validate a generation schema file (or a contract).",
        {"schema_path": Arg("string", "path of the schema or contract file", True)},
        obj(
            {
                "valid": BOOL,
                "kind": STR,
                "table_count": INT,
                "relationship_count": INT,
                "errors": arr(_ISSUE),
                "warnings": arr(_ISSUE),
            },
            {"name": nullable(STR), "mode": STR},
        ),
        cmd_validate,
    ),
    Command(
        "profile_info",
        "The distribution weights and derived-count ratios a domain generates with.",
        {"domain": _DOMAIN, "mode": _MODE, "profile": _PROFILE},
        obj(
            {
                "domain": STR,
                "profile": STR,
                "available_profiles": STRS,
                "distribution_keys": STRS,
                "distributions": mapping(ANY),
                "ratio_keys": STRS,
                "ratios": mapping(NUM),
            }
        ),
        cmd_profile_info,
    ),
]

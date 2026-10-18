"""File-only dbt semantic resource import and impact traversal."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from .project import DbtColumn, DbtProjectError, DbtRelation, DbtTest, ref_target


def apply_semantic_models(
    relations: Sequence[DbtRelation], models: Sequence[Mapping[str, Any]]
) -> None:
    """Import declarations; retain SQL expressions without inferring their physical lineage."""
    by_name = {r.name: r for r in relations}
    entities: dict[str, tuple[str, str]] = {}
    pending: list[tuple[DbtRelation, str, str, str]] = []
    for model in models:
        target = ref_target(model.get("model"))
        if target is None and model.get("node_relation"):
            target = (str(model["node_relation"].get("alias")), None)
        if target is None or target[0] not in by_name:
            raise DbtProjectError(
                f"semantic model {model.get('name')!r}: model is not a described relation"
            )
        rel = by_name[target[0]]
        origin = f"semantic model {model.get('name')!r}"
        for entity in model.get("entities") or []:
            name = str(entity["name"])
            expression = str(entity.get("expr") or name)
            column = expression if expression.isidentifier() else name
            if not column.isidentifier():
                raise DbtProjectError(f"{origin} entity {name!r}: name must be a column identifier")
            col = rel.columns.setdefault(column, DbtColumn(column))
            kind = entity.get("type")
            col.meta["semantic"] = {
                "entity": name,
                "entity_type": kind,
                "source": origin,
                "expression": expression,
            }
            if column != expression:
                col.data_type = col.data_type or "varchar"
            if kind == "primary":
                if column not in rel.semantic_primary_key:
                    rel.semantic_primary_key.append(column)
                entities[name] = (rel.name, column)
                col.tests.extend([DbtTest("unique"), DbtTest("not_null")])
            elif kind == "foreign":
                pending.append((rel, column, name, origin))
            elif kind == "unique":
                col.tests.append(DbtTest("unique"))
            elif kind != "natural":
                raise DbtProjectError(f"{origin} entity {name!r}: unknown type {kind!r}")
        for dimension in model.get("dimensions") or []:
            expression = str(dimension.get("expr") or dimension["name"])
            column = expression if expression.isidentifier() else str(dimension["name"])
            if not column.isidentifier():
                raise DbtProjectError(f"{origin}: dimension name must be a column identifier")
            col = rel.columns.setdefault(column, DbtColumn(column))
            kind = dimension.get("type")
            if kind not in ("time", "categorical"):
                raise DbtProjectError(f"{origin}: unknown dimension type {kind!r}")
            col.data_type = col.data_type or ("timestamp" if kind == "time" else "varchar")
            col.meta["semantic"] = {
                **dict(col.meta.get("semantic") or {}),
                "dimension": dimension["name"],
                "expression": expression,
                "type": kind,
                "granularity": (dimension.get("type_params") or {}).get("time_granularity"),
                "source": origin,
            }
        for measure in model.get("measures") or []:
            expression = str(measure.get("expr") or measure["name"])
            column = expression if expression.isidentifier() else str(measure["name"])
            if not column.isidentifier():
                raise DbtProjectError(f"{origin}: measure name must be a column identifier")
            col = rel.columns.setdefault(column, DbtColumn(column))
            col.data_type = col.data_type or "double"
            previous = dict(col.meta.get("semantic") or {})
            measures = list(previous.get("measures") or [])
            measures.append(
                {
                    "name": measure["name"],
                    "aggregation": measure.get("agg"),
                    "expression": expression,
                    "source": origin,
                }
            )
            col.meta["semantic"] = {
                **previous,
                "measures": measures,
                "measure": measure["name"],
                "expression": expression,
                "aggregation": measure.get("agg"),
                "source": origin,
            }
    for rel, column, name, origin in pending:
        parent = entities.get(name)
        if parent is None:
            raise DbtProjectError(f"{origin} foreign entity {name!r}: no matching primary entity")
        col = rel.columns[column]
        for test in col.tests:
            if test.kind == "relationships":
                target = ref_target(test.args.get("to"))
                if target is None or (target[0], test.args.get("field")) != parent:
                    raise DbtProjectError(
                        f"{origin} foreign entity {name!r} conflicts with "
                        f"relationships test on {rel.name}.{column}"
                    )
        if col.meta.get("semantic", {}).get("expression") != column:
            col.data_type = by_name[parent[0]].columns[parent[1]].data_type
        col.tests.append(
            DbtTest("relationships", {"to": f"ref('{parent[0]}')", "field": parent[1]})
        )


_SQL_TOKEN = re.compile(
    r"--[^\n]*|/\*.*?\*/|'(?:''|[^'])*'|\$\$(?:.*?)\$\$"
    r'|"(?:""|[^"])*"|`(?:``|[^`])*`|\[(?:\]\]|[^\]])*\]|[A-Za-z_][A-Za-z0-9_]*',
    re.S,
)


def _measure_columns(expression: str, columns: set[str]) -> set[str]:
    """Lex column identifiers only: skip literals, comments, functions and table qualifiers."""
    if expression.isidentifier():
        return {expression} if not columns or expression in columns else set()
    used: set[str] = set()
    for match in _SQL_TOKEN.finditer(expression):
        token = match.group()
        if token.startswith(("'", "--", "/*", "$$")):
            continue
        tail = expression[match.end() :].lstrip()
        if tail.startswith((".", "(")):
            continue
        if token.startswith('"'):
            token = token[1:-1].replace('""', '"')
        elif token.startswith("`"):
            token = token[1:-1].replace("``", "`")
        elif token.startswith("["):
            token = token[1:-1].replace("]]", "]")
        if token in columns:
            used.add(token)
    return used


def impact(manifest: Mapping[str, Any], columns: Sequence[str]) -> dict[str, Any]:
    """Resolve metrics by declared measure names, exposures by transitive model dependencies."""
    nodes = dict(manifest.get("nodes") or {})
    nodes.update(manifest.get("sources") or {})
    semantic = manifest.get("semantic_models") or {}
    metrics = manifest.get("metrics") or {}
    exposures = manifest.get("exposures") or {}
    measures: dict[str, set[str]] = {}
    for model in semantic.values():
        target = ref_target(model.get("model"))
        if target is None:
            deps = (model.get("depends_on") or {}).get("nodes") or []
            target = (
                (str(nodes.get(deps[0], {}).get("name", deps[0].rsplit(".", 1)[-1])), None)
                if deps
                else None
            )
        if target:
            for measure in model.get("measures") or []:
                expression = str(measure.get("expr") or measure["name"])
                declared = {
                    str(column)
                    for node in nodes.values()
                    if node.get("name") == target[0]
                    for column in (node.get("columns") or {})
                }
                used = _measure_columns(expression, declared)
                measures.setdefault(str(measure["name"]), set()).update(
                    f"{target[0]}.{column}" for column in used
                )
    metric_columns: dict[str, set[str]] = {}

    def metric_deps(uid: str, visited: set[str]) -> set[str]:
        if uid in visited:
            return set()
        visited.add(uid)
        node = metrics.get(uid) or {}
        params = node.get("type_params") or {}
        direct = params.get("measure")
        named = [direct] if direct else []
        named += list(params.get("input_measures") or [])
        out: set[str] = set()
        for item in named:
            name = item.get("name") if isinstance(item, Mapping) else item
            out.update(measures.get(str(name), set()))
        dependencies = list((node.get("depends_on") or {}).get("nodes") or [])
        for item in params.get("metrics") or []:
            name = item.get("name") if isinstance(item, Mapping) else item
            dependencies += [key for key, value in metrics.items() if value.get("name") == name]
        for dep in dependencies:
            if dep in metrics:
                out.update(metric_deps(dep, visited))
        return out

    for uid, node in metrics.items():
        metric_columns[str(node.get("name") or uid)] = metric_deps(uid, set())

    def upstream(uid: str, seen: set[str]) -> set[str]:
        if uid in seen:
            return set()
        seen.add(uid)
        node = nodes.get(uid) or {}
        result = {str(node.get("name") or uid.rsplit(".", 1)[-1])}
        for dep in (node.get("depends_on") or {}).get("nodes") or []:
            result.update(upstream(dep, seen))
        return result

    exposure_models: dict[str, set[str]] = {}
    for uid, node in exposures.items():
        names: set[str] = set()
        for dep in (node.get("depends_on") or {}).get("nodes") or []:
            names.update(upstream(dep, set()))
        exposure_models[str(node.get("name") or uid)] = names
    return {
        "format": "shape-dbt-impact",
        "version": 1,
        "columns": {
            column: {
                "metrics": sorted(name for name, used in metric_columns.items() if column in used),
                "exposures": sorted(
                    name
                    for name, used in exposure_models.items()
                    if column.rsplit(".", 1)[0] in used
                ),
            }
            for column in sorted(set(columns))
        },
    }

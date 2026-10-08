"""Read a dbt project's own description of its tables: ``manifest.json`` or ``schema.yml`` /
``sources.yml`` files.

The reader is file-based: it never imports ``dbt-core`` and never runs dbt. Both inputs end in the
same neutral form, a list of :class:`DbtRelation` (a source table, a seed or a model) whose
columns carry a description, a declared ``data_type`` and the generic tests written against
them.

A test is a :class:`DbtTest`. Only the four tests Shape understands are kept in ``kind``
(``not_null``, ``unique``, ``accepted_values``, ``relationships``); a test of a package, such as
``dbt_utils.accepted_range``, keeps its full name in ``kind`` so the compiler can read its own
output back. Model-level tests (``dbt_utils.unique_combination_of_columns``,
``dbt_expectations.expect_table_row_count_to_be_between``) are in :attr:`DbtRelation.tests`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Keys of a test entry that configure the test and are not its arguments.
_TEST_CONFIG_KEYS = {"config", "name", "description", "arguments"}
_REF = re.compile(r"""ref\(\s*(?:['"][^'"]+['"]\s*,\s*)?['"]([^'"]+)['"]\s*(?:,[^)]*)?\)""")
_SOURCE = re.compile(r"""source\(\s*['"]([^'"]+)['"]\s*,\s*['"]([^'"]+)['"]\s*\)""")
_MAX_FILE_BYTES = 256 * 1024 * 1024


class DbtProjectError(ValueError):
    """The dbt input could not be read."""


@dataclass
class DbtTest:
    """One test: ``kind`` is the short name for a built-in test and ``package.name`` otherwise;
    ``args`` are its arguments (``values``, ``to``, ``field``, ``min_value`` ...)."""

    kind: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class DbtColumn:
    name: str
    description: str = ""
    data_type: str | None = None
    tests: list[DbtTest] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class DbtRelation:
    """A source table (``kind == "source"``, ``source_name`` set), a seed or a model."""

    name: str
    kind: str
    description: str = ""
    columns: dict[str, DbtColumn] = field(default_factory=dict)
    tests: list[DbtTest] = field(default_factory=list)
    source_name: str | None = None
    contract_enforced: bool = False


def _yaml() -> Any:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - pyyaml is a dependency of the plugin
        raise DbtProjectError("reading schema.yml needs PyYAML: pip install pyyaml") from exc
    return yaml


def _read_text(path: Path) -> str:
    if path.stat().st_size > _MAX_FILE_BYTES:
        raise DbtProjectError(f"{path} is larger than {_MAX_FILE_BYTES // (1024 * 1024)} MB")
    return path.read_text(encoding="utf-8")


def ref_target(expression: Any) -> tuple[str, str | None] | None:
    """The relation a ``to:`` argument points at: ``ref('customers')`` gives ``("customers",
    None)`` and ``source('raw', 'customers')`` gives ``("customers", "raw")``. A bare name is
    its own target."""
    if not isinstance(expression, str):
        return None
    text = expression.strip()
    src = _SOURCE.search(text)
    if src:
        return src.group(2), src.group(1)
    ref = _REF.search(text)
    if ref:
        return ref.group(1), None
    return (text, None) if re.fullmatch(r"[\w.]+", text) else None


# ---- schema.yml / sources.yml ------------------------------------------------------------


def _split_test(entry: Any) -> DbtTest | None:
    """``not_null``, ``{unique: {...}}``, ``{accepted_values: {values: [...]}}`` or the dbt 1.10
    form with ``arguments:``. The test of a package keeps its ``package.`` prefix."""
    if isinstance(entry, str):
        return DbtTest(entry.strip())
    if isinstance(entry, Mapping) and len(entry) == 1:
        ((name, body),) = entry.items()
        args: dict[str, Any] = {}
        if isinstance(body, Mapping):
            args = {k: v for k, v in body.items() if k not in _TEST_CONFIG_KEYS}
            nested = body.get("arguments")
            if isinstance(nested, Mapping):
                args.update(nested)
        return DbtTest(str(name).strip(), args)
    return None


def _tests_of(node: Mapping[str, Any]) -> list[DbtTest]:
    out: list[DbtTest] = []
    for key in ("data_tests", "tests"):  # ``tests`` is the name before dbt 1.8
        for entry in node.get(key) or []:
            test = _split_test(entry)
            if test is not None:
                out.append(test)
    return out


def _short(kind: str) -> str:
    return kind.rsplit(".", 1)[-1] if kind.startswith("dbt.") else kind


def _column(node: Mapping[str, Any]) -> DbtColumn:
    constraints = [
        str(c.get("type", c) if isinstance(c, Mapping) else c)
        for c in node.get("constraints") or []
    ]
    meta = dict(node.get("meta") or (node.get("config") or {}).get("meta") or {})
    col = DbtColumn(
        name=str(node["name"]),
        description=str(node.get("description") or ""),
        data_type=str(node["data_type"]) if node.get("data_type") else None,
        tests=[DbtTest(_short(t.kind), t.args) for t in _tests_of(node)],
        constraints=constraints,
        meta=meta,
    )
    if "not_null" in constraints and not any(t.kind == "not_null" for t in col.tests):
        col.tests.append(DbtTest("not_null"))
    return col


def _relation(node: Mapping[str, Any], kind: str, source: str | None = None) -> DbtRelation:
    contract = (node.get("config") or {}).get("contract") or node.get("contract") or {}
    rel = DbtRelation(
        name=str(node["name"]),
        kind=kind,
        description=str(node.get("description") or ""),
        tests=[DbtTest(_short(t.kind), t.args) for t in _tests_of(node)],
        source_name=source,
        contract_enforced=bool(isinstance(contract, Mapping) and contract.get("enforced")),
    )
    for c in node.get("columns") or []:
        col = _column(c)
        rel.columns[col.name] = col
    return rel


def read_schema_yaml(text: str, origin: str = "schema.yml") -> list[DbtRelation]:
    """The relations of one ``schema.yml`` or ``sources.yml`` text (``models:``, ``seeds:``,
    ``snapshots:`` and ``sources:``)."""
    doc = _yaml().safe_load(text) or {}
    if not isinstance(doc, Mapping):
        raise DbtProjectError(f"{origin}: expected a mapping at the top level")
    out: list[DbtRelation] = []
    for section, kind in (("models", "model"), ("seeds", "seed"), ("snapshots", "model")):
        for node in doc.get(section) or []:
            if isinstance(node, Mapping) and "name" in node:
                out.append(_relation(node, kind))
    for src in doc.get("sources") or []:
        if not isinstance(src, Mapping) or "name" not in src:
            continue
        for node in src.get("tables") or []:
            if isinstance(node, Mapping) and "name" in node:
                out.append(_relation(node, "source", str(src["name"])))
    return out


# ---- manifest.json -----------------------------------------------------------------------


def _manifest_test(node: Mapping[str, Any]) -> tuple[DbtTest, str | None] | None:
    meta = node.get("test_metadata")
    if not isinstance(meta, Mapping) or "name" not in meta:
        return None
    namespace = meta.get("namespace")
    name = f"{namespace}.{meta['name']}" if namespace else str(meta["name"])
    kwargs = {
        k: v
        for k, v in dict(meta.get("kwargs") or {}).items()
        if k not in ("model", "column_name") and not k.startswith("_")
    }
    column = node.get("column_name") or (meta.get("kwargs") or {}).get("column_name")
    return DbtTest(_short(name), kwargs), (str(column) if column else None)


def _tested_relation(
    node: Mapping[str, Any], by_id: Mapping[str, DbtRelation]
) -> DbtRelation | None:
    """The relation a test node is about. dbt names it in ``attached_node`` for a model, but not
    for a source test; the test's ``model`` argument (``get_where_subquery(source('raw', 't'))``)
    says it for both. The test's ``depends_on`` is the last resort: a ``relationships`` test
    depends on its parent as well as on the table it tests, so it is not reliable."""
    attached = node.get("attached_node")
    if attached and attached in by_id:
        return by_id[str(attached)]
    kwargs = (node.get("test_metadata") or {}).get("kwargs") or {}
    target = ref_target(kwargs.get("model"))
    if target is not None:
        name, source = target
        for rel in by_id.values():
            if rel.name == name and (rel.kind == "source") == (source is not None):
                if source is None or rel.source_name == source:
                    return rel
    deps = [d for d in (node.get("depends_on") or {}).get("nodes", []) if d in by_id]
    return by_id[deps[-1]] if deps else None


def read_manifest(path_or_doc: str | Path | Mapping[str, Any]) -> list[DbtRelation]:
    """The sources, seeds and models of a ``manifest.json`` (the file or its parsed form), with
    their tests. A model's columns come from its ``columns`` entry (so a model with a contract
    keeps each ``data_type``)."""
    if isinstance(path_or_doc, Mapping):
        doc = path_or_doc
    else:
        path = Path(path_or_doc)
        try:
            doc = json.loads(_read_text(path))
        except (OSError, json.JSONDecodeError) as exc:
            raise DbtProjectError(f"{path} is not a readable manifest.json: {exc}") from exc
    if not isinstance(doc, Mapping) or "nodes" not in doc:
        raise DbtProjectError("not a dbt manifest: there is no 'nodes' object")
    nodes: Mapping[str, Any] = doc["nodes"]
    by_id: dict[str, DbtRelation] = {}
    for uid, node in nodes.items():
        kind = node.get("resource_type")
        if kind in ("model", "seed", "snapshot"):
            by_id[uid] = _relation(
                {**node, "columns": list((node.get("columns") or {}).values())},
                "seed" if kind == "seed" else "model",
            )
    for uid, node in (doc.get("sources") or {}).items():
        by_id[uid] = _relation(
            {**node, "columns": list((node.get("columns") or {}).values())},
            "source",
            str(node.get("source_name") or ""),
        )
    for node in nodes.values():
        if node.get("resource_type") != "test":
            continue
        parsed = _manifest_test(node)
        if parsed is None:
            continue
        test, column = parsed
        rel = _tested_relation(node, by_id)
        if rel is None:
            continue
        if column:
            rel.columns.setdefault(column, DbtColumn(column)).tests.append(test)
        else:
            rel.tests.append(test)
    for rel in by_id.values():
        for col in rel.columns.values():
            if "not_null" in col.constraints and not any(t.kind == "not_null" for t in col.tests):
                col.tests.append(DbtTest("not_null"))
    return list(by_id.values())


# ---- entry point -------------------------------------------------------------------------


def read_project(inputs: Iterable[str | Path]) -> list[DbtRelation]:
    """Read ``manifest.json``, ``schema.yml`` or ``sources.yml`` files, or a directory: its
    ``target/manifest.json`` when there is one, else every ``*.yml`` / ``*.yaml`` file under it
    (``dbt_packages`` and ``target`` skipped)."""
    relations: list[DbtRelation] = []
    paths: list[Path] = []
    for item in inputs:
        p = Path(item)
        if p.is_dir():
            manifest = p / "target" / "manifest.json"
            if manifest.is_file():
                paths.append(manifest)
                continue
            skipped = {"dbt_packages", "target", "dbt_modules", "node_modules", ".git"}
            paths += sorted(
                f
                for f in p.rglob("*")
                if f.suffix in (".yml", ".yaml")
                and f.is_file()
                and not skipped & set(f.relative_to(p).parts)
                and f.name
                not in ("dbt_project.yml", "packages.yml", "profiles.yml", "selectors.yml")
            )
        elif p.is_file():
            paths.append(p)
        else:
            raise DbtProjectError(f"{p}: no such file or directory")
    for p in paths:
        if p.suffix == ".json":
            relations += read_manifest(p)
        else:
            relations += read_schema_yaml(_read_text(p), str(p))
    if not relations:
        raise DbtProjectError("no sources, seeds or models found in the dbt input")
    return relations

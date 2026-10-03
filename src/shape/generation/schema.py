"""The Shape generation schema: tables, columns with a generator each, relationships, business
rules, scale presets and modes (``3nf`` and ``star``).

One typed model for every route into generation (a domain plugin, a ``.shape`` fit, a DDL file,
a hand-written JSON file). :meth:`GenSchema.to_dict` and :meth:`GenSchema.from_dict` read and
write the JSON form described by ``shape/schemas/generation-schema-v1.json``;
:func:`schema_problems` checks a document against that schema and :meth:`GenSchema.validate`
checks the semantics (keys exist, references resolve, required generator keys).

Stable interface (P4-02 and later work packages build on it): the dataclasses below, their
property names, ``to_dict``/``from_dict``, ``validate`` and :class:`Issue`.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from functools import cache
from importlib import resources
from typing import Any

from shape.errors import ShapeSchemaError
from shape.generation.spec_keys import unknown_keys
from shape.schemacheck import validate as _validate_document
from shape.security.names import is_safe_name

SCHEMA_VERSION = 1

# Generator keys each strategy needs. A missing key is a warning from ``GenSchema.validate``;
# a strategy that is not listed is a warning too (a plugin may register it).
STRATEGY_REQUIRED_KEYS: dict[str, frozenset[str]] = {
    "sequence": frozenset(),
    "uuid": frozenset(),
    "faker": frozenset({"provider"}),
    "weighted_enum": frozenset({"values"}),
    "distribution": frozenset({"distribution"}),
    "temporal": frozenset(),
    "formula": frozenset({"expression"}),
    "derived": frozenset({"source"}),
    "correlated": frozenset({"source_column"}),
    "foreign_key": frozenset({"ref"}),
    "lookup": frozenset({"source_table", "source_column", "via"}),
    "reference_data": frozenset({"dataset"}),
    "pattern": frozenset({"format"}),
    "conditional": frozenset({"condition"}),
    "computed": frozenset({"rule", "child_table", "child_column"}),
    "lifecycle": frozenset({"phases"}),
    "self_referencing": frozenset({"pk_column"}),
    "self_ref_field": frozenset({"field"}),
    "first_per_parent": frozenset({"parent_column"}),
    "record_sample": frozenset({"dataset", "field"}),
    "record_field": frozenset({"dataset", "field"}),
    "conditional_table": frozenset({"source_column", "table", "values"}),
    "hierarchy": frozenset({"dataset", "field", "levels"}),
    "hierarchy_field": frozenset({"dataset", "field"}),
    "scd2": frozenset({"role", "business_key"}),
    "composite_foreign_key": frozenset({"ref_table", "ref_columns"}),
    "composite_fk_field": frozenset({"source_column", "ref_column"}),
    "native": frozenset(),
    "address": frozenset(),
    "bootstrap": frozenset({"dataset", "field"}),
    "constant": frozenset({"value"}),
    "choice": frozenset({"values"}),
    "empirical": frozenset({"quantiles"}),
    "normal": frozenset({"mean", "stddev"}),
    "uniform": frozenset({"low", "high"}),
}

MODES = ("3nf", "star")


def _plain_json(value: Any) -> Any:
    """A copy of ``value`` made of JSON types only. A dataclass (an ``AddressReference`` or a
    ``Location`` row of an address reference) becomes a dict, as a schema file would hold it."""

    def default(obj: Any) -> Any:
        if is_dataclass(obj) and not isinstance(obj, type):
            return asdict(obj)
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    return json.loads(json.dumps(value, default=default))


class GenSchemaError(ShapeSchemaError):
    """A generation schema document does not follow ``generation-schema-v1.json``."""


@dataclass(frozen=True, slots=True)
class Issue:
    """One finding of :meth:`GenSchema.validate`: ``level`` is ``error`` or ``warning``."""

    level: str
    message: str
    location: str


@dataclass(slots=True)
class Column:
    """One column: its logical ``type`` and the ``generator`` (a ``strategy`` plus its keys)."""

    name: str
    type: str
    generator: dict[str, Any]
    nullable: bool = False
    null_rate: float = 0.0
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None
    #: A database identity column (``IDENTITY``, ``SERIAL``, ``AUTO_INCREMENT``): an ``integer``
    #: column of the ``sequence`` strategy; a SQL writer keeps its values or lets the server number.
    identity: bool = False

    @property
    def strategy(self) -> str:
        return str(self.generator.get("strategy", ""))

    @property
    def is_foreign_key(self) -> bool:
        return self.strategy == "foreign_key"

    @property
    def fk_ref_table(self) -> str | None:
        """The table a ``foreign_key`` column points at (from ``ref = "table.column"``)."""
        if not self.is_foreign_key:
            return None
        ref = str(self.generator.get("ref", ""))
        return ref.split(".")[0] if "." in ref else None

    @property
    def fk_ref_column(self) -> str | None:
        if not self.is_foreign_key:
            return None
        ref = str(self.generator.get("ref", ""))
        return ref.split(".")[1] if "." in ref else None

    @property
    def is_computed(self) -> bool:
        return self.strategy in ("computed", "formula")


@dataclass(slots=True)
class Table:
    """A table: ordered columns and its primary key."""

    name: str
    columns: dict[str, Column]
    primary_key: list[str] = field(default_factory=list)
    description: str = ""
    cdm_mapping: str | None = None

    @property
    def column_names(self) -> list[str]:
        return list(self.columns)

    @property
    def fk_dependencies(self) -> set[str]:
        """Tables this table's foreign-key columns point at (never itself)."""
        deps: set[str] = set()
        for col in self.columns.values():
            ref = col.fk_ref_table
            if ref and ref != self.name:
                deps.add(ref)
        return deps


@dataclass(slots=True)
class Relationship:
    """A parent/child link: ``one_to_many``, ``one_to_one``, ``many_to_many`` or
    ``self_referencing``."""

    name: str
    parent: str
    child: str
    parent_columns: list[str]
    child_columns: list[str]
    type: str = "one_to_many"
    cardinality: dict[str, Any] = field(default_factory=dict)
    optional: bool = False


@dataclass(slots=True)
class BusinessRule:
    """A constraint over generated data: ``cross_table`` (``A.x >= B.y`` joined ``via`` a key),
    ``cross_column`` or ``constraint`` (``left OP right`` inside ``table``). Other types are
    carried and ignored by the rules engine."""

    name: str
    type: str
    rule: str
    table: str | None = None
    via: str | None = None
    when: str | None = None


@dataclass(slots=True)
class Generation:
    """Scale presets (``scales``: preset -> table -> rows), the current ``scale`` and how the
    remaining tables' counts derive (``derived_counts``: ``fixed``, ``per_parent`` x ``ratio`` or
    ``per_year``)."""

    scale: str = "small"
    scales: dict[str, dict[str, int]] = field(default_factory=dict)
    derived_counts: dict[str, dict[str, Any]] = field(default_factory=dict)
    output: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Model:
    """Top-level metadata. ``seed`` is the run seed (T-16); ``date_range`` holds ISO ``start``
    and ``end`` dates for temporal strategies and ``per_year`` row counts."""

    name: str = "unnamed"
    description: str = ""
    domain: str = ""
    schema_mode: str = "3nf"
    locale: str = "en_US"
    seed: int = 42
    date_range: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class GenSchema:
    """A complete generation schema. ``correlated_columns`` maps a table to ``[column_a,
    column_b, r]`` triples that the Gaussian-copula post-pass enforces."""

    model: Model
    tables: dict[str, Table]
    relationships: list[Relationship] = field(default_factory=list)
    business_rules: list[BusinessRule] = field(default_factory=list)
    generation: Generation = field(default_factory=Generation)
    correlated_columns: dict[str, list[list[Any]]] = field(default_factory=dict)

    # ---- structure ----------------------------------------------------------------------

    @property
    def table_names(self) -> list[str]:
        return list(self.tables)

    def get_children(self, table: str) -> list[Relationship]:
        return [r for r in self.relationships if r.parent == table]

    def get_parents(self, table: str) -> list[Relationship]:
        return [r for r in self.relationships if r.child == table]

    def get_relationship(self, name: str) -> Relationship | None:
        return next((r for r in self.relationships if r.name == name), None)

    # ---- JSON ---------------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """The JSON document (``generation-schema-v1.json``). Round-trips through
        :meth:`from_dict`."""
        return {
            "schema_version": SCHEMA_VERSION,
            "model": {
                "name": self.model.name,
                "description": self.model.description,
                "domain": self.model.domain,
                "schema_mode": self.model.schema_mode,
                "locale": self.model.locale,
                "seed": self.model.seed,
                "date_range": dict(self.model.date_range),
            },
            "tables": {
                t.name: {
                    "name": t.name,
                    "description": t.description,
                    "cdm_mapping": t.cdm_mapping,
                    "primary_key": list(t.primary_key),
                    "columns": {c.name: _column_doc(c) for c in t.columns.values()},
                }
                for t in self.tables.values()
            },
            "relationships": [
                {
                    "name": r.name,
                    "parent": r.parent,
                    "child": r.child,
                    "parent_columns": list(r.parent_columns),
                    "child_columns": list(r.child_columns),
                    "type": r.type,
                    "cardinality": json.loads(json.dumps(r.cardinality)),
                    "optional": r.optional,
                }
                for r in self.relationships
            ],
            "business_rules": [
                {
                    "name": b.name,
                    "type": b.type,
                    "rule": b.rule,
                    "table": b.table,
                    "via": b.via,
                    "when": b.when,
                }
                for b in self.business_rules
            ],
            "generation": {
                "scale": self.generation.scale,
                "scales": json.loads(json.dumps(self.generation.scales)),
                "derived_counts": json.loads(json.dumps(self.generation.derived_counts)),
                "output": json.loads(json.dumps(self.generation.output)),
            },
            "correlated_columns": json.loads(json.dumps(self.correlated_columns)),
        }

    @classmethod
    def from_dict(cls, doc: Any) -> GenSchema:
        """Parse a document; :class:`GenSchemaError` lists the first problems if it does not
        follow the JSON Schema."""
        problems = schema_problems(doc)
        if problems:
            more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
            raise GenSchemaError("; ".join(problems[:5]) + more)
        m, g = doc["model"], doc["generation"]
        model = Model(
            name=m["name"],
            description=m.get("description", ""),
            domain=m.get("domain", ""),
            schema_mode=m.get("schema_mode", "3nf"),
            locale=m.get("locale", "en_US"),
            seed=m.get("seed", 42),
            date_range=dict(m.get("date_range", {})),
        )
        tables: dict[str, Table] = {}
        for tname, t in doc["tables"].items():
            if not is_safe_name(tname):
                raise GenSchemaError(
                    f"tables.{tname!r}: a table name must be a plain name, not a path"
                )
            columns = {
                cname: Column(
                    name=cname,
                    type=c["type"],
                    generator=_plain_json(c["generator"]),
                    nullable=c.get("nullable", False),
                    null_rate=float(c.get("null_rate", 0.0)),
                    max_length=c.get("max_length"),
                    precision=c.get("precision"),
                    scale=c.get("scale"),
                    identity=bool(c.get("identity", False)),
                )
                for cname, c in t["columns"].items()
            }
            tables[tname] = Table(
                name=tname,
                columns=columns,
                primary_key=list(t.get("primary_key", [])),
                description=t.get("description", ""),
                cdm_mapping=t.get("cdm_mapping"),
            )
        return cls(
            model=model,
            tables=tables,
            relationships=[
                Relationship(
                    name=r["name"],
                    parent=r["parent"],
                    child=r["child"],
                    parent_columns=list(r.get("parent_columns", [])),
                    child_columns=list(r.get("child_columns", [])),
                    type=r.get("type", "one_to_many"),
                    cardinality=dict(r.get("cardinality", {})),
                    optional=r.get("optional", False),
                )
                for r in doc.get("relationships", [])
            ],
            business_rules=[
                BusinessRule(
                    name=b["name"],
                    type=b["type"],
                    rule=b["rule"],
                    table=b.get("table"),
                    via=b.get("via"),
                    when=b.get("when"),
                )
                for b in doc.get("business_rules", [])
            ],
            generation=Generation(
                scale=g.get("scale", "small"),
                scales={k: dict(v) for k, v in g.get("scales", {}).items()},
                derived_counts={k: dict(v) for k, v in g.get("derived_counts", {}).items()},
                output=dict(g.get("output", {})),
            ),
            correlated_columns={
                k: [list(p) for p in v] for k, v in doc.get("correlated_columns", {}).items()
            },
        )

    # ---- semantics ----------------------------------------------------------------------

    def validate(self) -> list[Issue]:
        """Every semantic problem of the schema: an ``error`` stops generation, a ``warning``
        does not."""
        out: list[Issue] = []
        out += self._table_issues()
        out += self._relationship_issues()
        out += self._foreign_key_issues()
        out += self._rule_issues()
        out += self._generation_issues()
        out += self._strategy_issues()
        return out

    def validate_or_raise(self) -> None:
        errors = [i for i in self.validate() if i.level == "error"]
        if errors:
            detail = "\n".join(f"  [{i.location}] {i.message}" for i in errors)
            raise GenSchemaError(f"Schema validation failed:\n{detail}")

    def _table_issues(self) -> list[Issue]:
        out: list[Issue] = []
        for tname, t in self.tables.items():
            if not t.primary_key:
                out.append(
                    Issue(
                        "warning",
                        "Table has no primary key defined: it cannot be FK-referenced",
                        f"tables.{tname}",
                    )
                )
            for pk in t.primary_key:
                if pk not in t.columns:
                    out.append(
                        Issue(
                            "error",
                            f"Primary key column '{pk}' not found in columns",
                            f"tables.{tname}.primary_key",
                        )
                    )
            if not t.columns:
                out.append(Issue("error", "Table has no columns", f"tables.{tname}"))
            for cname, c in t.columns.items():
                where = f"tables.{tname}.columns.{cname}"
                if not c.generator:
                    out.append(
                        Issue("warning", f"Column '{cname}' has no generator defined", where)
                    )
                if c.null_rate < 0 or c.null_rate > 1:
                    out.append(
                        Issue(
                            "error", f"null_rate must be between 0 and 1, got {c.null_rate}", where
                        )
                    )
                if c.identity and c.type != "integer":
                    out.append(
                        Issue(
                            "error",
                            f"Column '{cname}' is an identity column and must have type "
                            f"'integer', not '{c.type}'",
                            where,
                        )
                    )
                if c.identity and c.strategy != "sequence":
                    out.append(
                        Issue(
                            "error",
                            f"Column '{cname}' is an identity column and must use the "
                            f"'sequence' strategy, not '{c.strategy or 'none'}'",
                            where,
                        )
                    )
        return out

    def _relationship_issues(self) -> list[Issue]:
        out: list[Issue] = []
        for r in self.relationships:
            where = f"relationships.{r.name}"
            if r.parent not in self.tables:
                out.append(Issue("error", f"Parent table '{r.parent}' not found", where))
            else:
                for col in r.parent_columns:
                    if col not in self.tables[r.parent].columns:
                        out.append(
                            Issue(
                                "error", f"Parent column '{col}' not in table '{r.parent}'", where
                            )
                        )
            if r.child not in self.tables:
                out.append(Issue("error", f"Child table '{r.child}' not found", where))
            else:
                for col in r.child_columns:
                    if col not in self.tables[r.child].columns:
                        out.append(
                            Issue("error", f"Child column '{col}' not in table '{r.child}'", where)
                        )
        return out

    def _foreign_key_issues(self) -> list[Issue]:
        out: list[Issue] = []
        for tname, t in self.tables.items():
            for cname, c in t.columns.items():
                ref_table = c.fk_ref_table
                if not c.is_foreign_key or not ref_table:
                    continue
                where = f"tables.{tname}.columns.{cname}"
                if ref_table not in self.tables:
                    out.append(
                        Issue("error", f"FK references non-existent table '{ref_table}'", where)
                    )
                    continue
                ref_col = c.fk_ref_column
                if ref_col and ref_col not in self.tables[ref_table].columns:
                    out.append(
                        Issue(
                            "error",
                            f"FK references non-existent column '{ref_table}.{ref_col}'",
                            where,
                        )
                    )
        return out

    def _rule_issues(self) -> list[Issue]:
        return [
            Issue(
                "error",
                f"Rule references non-existent table '{b.table}'",
                f"business_rules.{b.name}",
            )
            for b in self.business_rules
            if b.table and b.table not in self.tables
        ]

    def _generation_issues(self) -> list[Issue]:
        g = self.generation
        if g.scale and g.scales and g.scale not in g.scales:
            return [
                Issue("warning", f"Scale '{g.scale}' not defined in scales", "generation.scale")
            ]
        return []

    def _strategy_issues(self) -> list[Issue]:
        out: list[Issue] = []
        for tname, t in self.tables.items():
            for cname, c in t.columns.items():
                if not c.generator or not c.strategy:
                    continue
                where = f"tables.{tname}.columns.{cname}.generator"
                required = STRATEGY_REQUIRED_KEYS.get(c.strategy)
                if required is None:
                    out.append(Issue("warning", f"Unknown strategy '{c.strategy}'", where))
                    continue
                for key in sorted(required):
                    if key not in c.generator:
                        out.append(
                            Issue("warning", f"Strategy '{c.strategy}' expects key '{key}'", where)
                        )
                for _, message in unknown_keys(c.strategy, c.generator):
                    out.append(Issue("warning", message, where))
        return out


def _column_doc(c: Column) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "name": c.name,
        "type": c.type,
        "generator": json.loads(json.dumps(c.generator)),
        "nullable": c.nullable,
        "null_rate": c.null_rate,
        "max_length": c.max_length,
        "precision": c.precision,
        "scale": c.scale,
    }
    if c.identity:  # absent otherwise, so a schema without identity serializes as it always did
        doc["identity"] = True
    return doc


@cache
def json_schema() -> dict[str, Any]:
    """The JSON Schema of the generation schema, as shipped in ``shape/schemas``."""
    text = resources.files("shape").joinpath("schemas/generation-schema-v1.json").read_text("utf-8")
    schema: dict[str, Any] = json.loads(text)
    return schema


def schema_problems(doc: Any) -> list[str]:
    """Every way ``doc`` departs from ``generation-schema-v1.json`` (empty when it conforms)."""
    if not isinstance(doc, dict):
        return [f"$: expected an object, got {type(doc).__name__}"]
    return _validate_document(doc, json_schema())

"""Smart inference for a schema read from DDL (P4-01b).

The DDL parser gives every column a first generator from its type and name. This pipeline
upgrades them from the schema's structure, in this order: table roles, column semantics,
foreign-key distributions, parent/child row ratios and scale presets, numeric distributions,
status and category value mixes, temporal patterns, cross-column correlations, and business
rules. Every decision is recorded as an :class:`Annotation` (what ``shape from-ddl --explain``
prints). Only placeholder generators are replaced: a column whose generator was already
configured is left alone.

    notes = SchemaInference().run(schema)   # mutates ``schema``, returns the annotations

Stable interface: :class:`SchemaInference`, :class:`Annotation`, :class:`TableRole` and
:class:`ColumnSemantic`.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from shape.generation.ddl import one_to_one_parent
from shape.generation.ddl_names import snake, word_pattern
from shape.generation.schema import BusinessRule, Column, GenSchema, Table


class TableRole(Enum):
    """The part a table plays in the schema."""

    ENTITY = auto()  # customer, employee: a core business object
    TRANSACTION = auto()  # order, claim, invoice
    TRANSACTION_DETAIL = auto()  # order line: a child of a transaction
    LOOKUP = auto()  # status codes, categories: small and referenced
    HIERARCHY = auto()  # self-referencing tree
    BRIDGE = auto()  # junction table (many to many)
    LOG = auto()  # audit log, event log, history
    DIMENSION = auto()  # ``dim_`` prefix
    FACT = auto()  # ``fact_`` prefix
    UNKNOWN = auto()


class ColumnSemantic(Enum):
    """What a column holds."""

    PRIMARY_KEY = auto()
    FOREIGN_KEY = auto()
    MONETARY = auto()
    QUANTITY = auto()
    PERCENTAGE = auto()
    MEASUREMENT = auto()
    RATING = auto()
    STATUS = auto()
    CATEGORICAL = auto()
    BOOLEAN_FLAG = auto()
    TEMPORAL_TRANSACTION = auto()
    TEMPORAL_AUDIT = auto()
    TEMPORAL_START = auto()
    TEMPORAL_END = auto()
    TEMPORAL_BIRTH = auto()
    TEMPORAL_GENERIC = auto()
    CODE = auto()
    IDENTIFIER = auto()
    TEXT_DESCRIPTION = auto()
    NAME = auto()
    EMAIL = auto()
    PHONE = auto()
    ADDRESS = auto()
    CITY = auto()
    STATE_CODE = auto()
    POSTAL_CODE = auto()
    COUNTRY = auto()
    URL = auto()
    UNKNOWN = auto()


@dataclass(frozen=True, slots=True)
class Annotation:
    """One inference decision: the rule that fired, where, and how sure it is (0 to 1)."""

    table: str
    column: str | None
    rule_id: str
    description: str
    confidence: float


@dataclass
class _Context:
    """State passed through the pipeline."""

    schema: GenSchema
    roles: dict[str, TableRole] = field(default_factory=dict)
    semantics: dict[str, dict[str, ColumnSemantic]] = field(default_factory=dict)
    children_of: dict[str, list[str]] = field(default_factory=dict)
    parents_of: dict[str, list[str]] = field(default_factory=dict)
    annotations: list[Annotation] = field(default_factory=list)

    def annotate(
        self,
        table: str,
        column: str | None,
        rule_id: str,
        description: str,
        confidence: float = 0.8,
    ) -> None:
        self.annotations.append(Annotation(table, column, rule_id, description, confidence))

    def build_graphs(self) -> None:
        for rel in self.schema.relationships:
            self.children_of.setdefault(rel.parent, []).append(rel.child)
            self.parents_of.setdefault(rel.child, []).append(rel.parent)

    def role(self, table: str) -> TableRole:
        return self.roles.get(table, TableRole.UNKNOWN)


# ---- table roles ------------------------------------------------------------------------

_LOG_PATTERNS = word_pattern(
    "log", "audit", "history", "event", "tracking", "changelog", "activity"
)
_DIM_PREFIX = re.compile(r"^(dim_|d_)", re.IGNORECASE)
_FACT_PREFIX = re.compile(r"^(fact_|f_)", re.IGNORECASE)
_ENTITY_COLUMNS = {
    "first_name",
    "last_name",
    "email",
    "phone",
    "date_of_birth",
    "birth_date",
    "ssn",
    "username",
    "login",
    "signup_date",
    "company_name",
    "org_name",
    "contact_name",
}
_TRANSACTION_COLUMNS = {
    "order_date",
    "transaction_date",
    "invoice_date",
    "purchase_date",
    "created_date",
    "total_amount",
    "subtotal",
    "grand_total",
    "order_total",
    "payment_date",
    "bill_date",
    "claim_date",
}
_DETAIL_HINTS = {
    "quantity",
    "qty",
    "unit_price",
    "line_total",
    "amount",
    "line_amount",
    "line_number",
    "item_number",
}


_DATE_WORD = word_pattern("date")
_AMOUNT_WORDS = word_pattern("amount", "total", "subtotal", "price", "cost")
_DATE_WORDS = word_pattern("date", "at")


def _classify_table(name: str, table: Table, ctx: _Context) -> TableRole:
    cols = {c.lower() for c in table.column_names}
    children = ctx.children_of.get(name, [])
    parents = ctx.parents_of.get(name, [])
    fk_count, child_count = len(parents), len(children)
    non_pk = len(table.columns) - len(table.primary_key)

    if _DIM_PREFIX.match(name):
        return TableRole.DIMENSION
    if _FACT_PREFIX.match(name):
        return TableRole.FACT
    for col in table.columns.values():
        if col.is_foreign_key and col.fk_ref_table == name:
            return TableRole.HIERARCHY
        if col.generator.get("strategy") == "self_referencing":
            return TableRole.HIERARCHY
    if _LOG_PATTERNS.search(snake(name)):
        return TableRole.LOG
    if fk_count >= 2 and non_pk <= 3:
        fk_cols = {c.name for c in table.columns.values() if c.is_foreign_key}
        if fk_cols & set(table.primary_key) and len(fk_cols) >= 2:
            return TableRole.BRIDGE
    if fk_count == 0 and child_count >= 2 and non_pk <= 5:
        return TableRole.LOOKUP
    if cols & _ENTITY_COLUMNS and child_count >= 1:
        return TableRole.ENTITY
    if fk_count == 1 and non_pk <= 8:
        if ctx.role(parents[0]) in (TableRole.TRANSACTION, TableRole.UNKNOWN) and (
            cols & _DETAIL_HINTS
        ):
            return TableRole.TRANSACTION_DETAIL
    has_amount = any(_AMOUNT_WORDS.search(snake(c)) for c in table.column_names)
    has_date = any(_DATE_WORDS.search(snake(c)) for c in table.column_names)
    if fk_count >= 1 and (cols & _TRANSACTION_COLUMNS or (has_amount and has_date)):
        return TableRole.TRANSACTION
    if fk_count == 0 and child_count >= 1:
        return TableRole.LOOKUP
    if child_count >= 3:
        return TableRole.ENTITY
    if fk_count >= 1 and (has_date or has_amount):
        return TableRole.TRANSACTION
    return TableRole.UNKNOWN


def _table_roles(ctx: _Context) -> None:
    for name, table in ctx.schema.tables.items():
        role = _classify_table(name, table, ctx)
        ctx.roles[name] = role
        ctx.annotate(name, None, f"TC-{role.name}", f"Classified as {role.name}", 0.8)


# ---- column semantics -------------------------------------------------------------------

_MONETARY = word_pattern(
    "price", "cost", "amount", "total", "subtotal", "fee", "charge", "rate", "salary", "wage",
    "pay", "revenue", "margin", "balance", "payment", "premium", "deductible", "copay", "refund",
    "credit", "debit", "deposit", "withdrawal", "budget", "spend", "income", "rent",
    "commission", "bonus", "tax_amount", "freight", "shipping_cost",
)  # fmt: skip
_QUANTITY = word_pattern(
    "quantity", "qty", "count", "units", "num_", "number_of_", "stock", "inventory",
    "on_hand", "on_order", "allocated", "reserved", "capacity", "headcount",
)  # fmt: skip
# Checked before money and quantity: ``discount_pct`` and ``tax_rate`` are percentages.
_PERCENTAGE = word_pattern(
    "pct", "percent", "percentage", "ratio", "tax_rate", "completion_rate", "yield_rate",
    "defect_rate", "churn_rate", "conversion_rate",
)  # fmt: skip
_MEASUREMENT = word_pattern(
    "weight", "height", "width", "length", "depth", "size", "area", "volume", "sqft",
    "square_feet", "lot_size", "distance", "duration", "temperature",
)  # fmt: skip
_RATING = word_pattern(
    "rating", "score", "stars", "rank", "grade", "level", "tier", "priority", "severity"
)
# ``state`` is a region (see ``_STATE``), not a status.
_STATUS = re.compile(
    r"^(status|order_status|payment_status|claim_status|"
    r"account_status|ticket_status|job_status|task_status)$",
    re.IGNORECASE,
)
_CATEGORICAL = word_pattern(
    "type", "category", "kind", "class", "group", "segment", "channel", "method", "mode",
    "source", "reason", "department", "division", "region", "territory", "zone",
)  # fmt: skip
_BOOLEAN = re.compile(
    r"^(?:(?:is|has|can|should|was|did)_|(?:flag|active|enabled|deleted|verified|approved|"
    r"published|archived|locked|primary|default)s?(?![a-z0-9]))",
    re.IGNORECASE,
)
_TEMPORAL_TXN = word_pattern(
    "order_date", "purchase_date", "transaction_date", "invoice_date",
    "sale_date", "claim_date", "booking_date", "payment_date", "ship_date",
)  # fmt: skip
_TEMPORAL_AUDIT = word_pattern(
    "created_at", "modified_at", "updated_at", "deleted_at", "created_date",
    "modified_date", "updated_date", "last_modified", "last_updated",
    "date_created", "date_modified",
)  # fmt: skip
_TEMPORAL_START = word_pattern(
    "start_date", "begin_date", "effective_date", "hire_date", "open_date",
    "enrollment_date", "signup_date", "registration_date", "issue_date",
    "inception_date", "commencement",
)  # fmt: skip
_TEMPORAL_END = word_pattern(
    "end_date", "expir*", "close_date", "due_date", "completion_date",
    "termination_date", "cancellation_date", "maturity_date",
    "discharge_date", "resolved_at", "closed_at",
)  # fmt: skip
_TEMPORAL_BIRTH = word_pattern("birth_date", "dob", "date_of_birth", "birthdate")
# A code word after another word (``country_code``); the last three only as the last word.
_CODE = re.compile(
    r"(?<=[a-z0-9]_)(?:code|number|sku|upc|ean)(?:s|es)?(?![a-z0-9])"
    r"|(?<=[a-z0-9]_)(?:no|num|ref|key|token)$",
    re.IGNORECASE,
)
_TEXT = word_pattern(
    "description", "comment", "note", "remarks", "reason", "summary", "detail", "body",
    "message", "feedback", "review", "bio", "abstract", "memo",
)  # fmt: skip
_EMAIL = word_pattern("email", "e_mail")
_PHONE = word_pattern("phone", "fax", "mobile", "cell", "tel")
_ADDRESS = word_pattern("address", "addr", "street", "line1", "line2", "address_line")
_CITY = re.compile(r"^(city|town|municipality)$", re.IGNORECASE)
_STATE = re.compile(r"^(state|province|region|state_code|state_province)$", re.IGNORECASE)
_POSTAL = word_pattern("zip", "postal", "postcode", "zip_code", "postal_code")
_COUNTRY = re.compile(r"^(country|country_code|country_name)$", re.IGNORECASE)
_URL = word_pattern("url", "website", "homepage", "link", "uri")
_NAME = word_pattern("name", "title", "label")


def _classify_column(name: str, col: Column, role: TableRole, table: Table) -> ColumnSemantic:
    s = snake(name)
    if name in table.primary_key:
        return ColumnSemantic.PRIMARY_KEY
    if col.is_foreign_key:
        return ColumnSemantic.FOREIGN_KEY

    ctype = col.type.lower()
    numeric = ctype in ("integer", "float", "decimal", "money")
    string = ctype in ("string", "text", "varchar", "nvarchar", "char")
    temporal = ctype in ("date", "datetime", "timestamp")
    boolean = ctype in ("boolean", "bit")

    if boolean or _BOOLEAN.match(s):
        return ColumnSemantic.BOOLEAN_FLAG
    if temporal or s.endswith(("_date", "_at", "_time", "_timestamp")):
        if _TEMPORAL_BIRTH.search(s):
            return ColumnSemantic.TEMPORAL_BIRTH
        if _TEMPORAL_TXN.search(s):
            return ColumnSemantic.TEMPORAL_TRANSACTION
        if _TEMPORAL_AUDIT.search(s):
            return ColumnSemantic.TEMPORAL_AUDIT
        if _TEMPORAL_START.search(s):
            return ColumnSemantic.TEMPORAL_START
        if _TEMPORAL_END.search(s):
            return ColumnSemantic.TEMPORAL_END
        if role == TableRole.TRANSACTION and _DATE_WORD.search(s):
            return ColumnSemantic.TEMPORAL_TRANSACTION
        return ColumnSemantic.TEMPORAL_GENERIC
    if numeric and _PERCENTAGE.search(s):
        return ColumnSemantic.PERCENTAGE
    if numeric and _MONETARY.search(s):
        return ColumnSemantic.MONETARY
    if ctype == "money":
        return ColumnSemantic.MONETARY
    if numeric and _QUANTITY.search(s):
        return ColumnSemantic.QUANTITY
    if numeric and _MEASUREMENT.search(s):
        return ColumnSemantic.MEASUREMENT
    if numeric and _RATING.search(s):
        return ColumnSemantic.RATING
    if string and _STATUS.search(s):
        return ColumnSemantic.STATUS
    if string and _CATEGORICAL.search(s) and (col.max_length or 255) <= 255:
        return ColumnSemantic.CATEGORICAL
    if _EMAIL.search(s):
        return ColumnSemantic.EMAIL
    if _PHONE.search(s):
        return ColumnSemantic.PHONE
    if _ADDRESS.search(s):
        return ColumnSemantic.ADDRESS
    if _CITY.match(s):
        return ColumnSemantic.CITY
    if _STATE.match(s):
        return ColumnSemantic.STATE_CODE
    if _POSTAL.search(s):
        return ColumnSemantic.POSTAL_CODE
    if _COUNTRY.match(s):
        return ColumnSemantic.COUNTRY
    if _URL.search(s):
        return ColumnSemantic.URL
    if string and _CODE.search(s):
        return ColumnSemantic.CODE
    if string and _TEXT.search(s):
        return ColumnSemantic.TEXT_DESCRIPTION
    if string and _NAME.search(s):
        return ColumnSemantic.NAME
    return ColumnSemantic.UNKNOWN


def _column_semantics(ctx: _Context) -> None:
    for name, table in ctx.schema.tables.items():
        role = ctx.role(name)
        found = ctx.semantics.setdefault(name, {})
        for cname, col in table.columns.items():
            found[cname] = _classify_column(cname, col, role, table)


# ---- foreign-key distributions ----------------------------------------------------------

_ASSIGNED = word_pattern(
    "assigned_to", "approved_by", "created_by", "reviewed_by", "managed_by", "owned_by",
    "updated_by", "modified_by", "submitted_by", "handled_by",
)  # fmt: skip
_ADDRESS_FK = word_pattern(
    "address", "location", "site", "facility", "warehouse", "branch", "office"
)


def _fk_distribution(
    ctx: _Context, col_name: str, table_name: str, parent: str, table: Table
) -> tuple[str, dict[str, Any], str, str]:
    """``(distribution, params, rule_id, description)`` for one foreign-key column."""
    role, parent_role = ctx.role(table_name), ctx.role(parent)
    if parent == table_name:
        return "self_referencing", {}, "FK-05", "Self-referencing FK — strategy unchanged"
    if _ASSIGNED.search(snake(col_name)):
        return "pareto", {"alpha": 2.0}, "FK-09", f"Agent FK ({col_name}) — pareto(alpha=2.0)"
    if _ADDRESS_FK.search(snake(parent)):
        return (
            "zipf",
            {"alpha": 1.5},
            "FK-08",
            f"FK to address/location table ({parent}) — zipf(alpha=1.5)",
        )
    if role == TableRole.BRIDGE:
        fk_cols = [c.name for c in table.columns.values() if c.is_foreign_key]
        if fk_cols and col_name == fk_cols[0]:
            return "zipf", {"alpha": 1.3}, "FK-06", "Bridge table first FK — zipf(alpha=1.3)"
        return "pareto", {"alpha": 1.16}, "FK-06", "Bridge table second FK — pareto(alpha=1.16)"
    if role == TableRole.LOG:
        return "pareto", {"alpha": 1.5}, "FK-07", "Log/audit child — pareto(alpha=1.5)"
    if role == TableRole.TRANSACTION and parent_role == TableRole.LOOKUP:
        return (
            "zipf",
            {"alpha": 1.2},
            "FK-10",
            f"Transaction FK to lookup ({parent}) — zipf(alpha=1.2)",
        )
    if parent_role == TableRole.ENTITY and role == TableRole.TRANSACTION:
        return (
            "pareto",
            {"alpha": 1.16, "max_per_parent": 50},
            "FK-01",
            f"Entity ({parent}) -> transaction child — pareto(alpha=1.16)",
        )
    if parent_role in (TableRole.LOOKUP, TableRole.DIMENSION):
        return (
            "zipf",
            {"alpha": 1.3},
            "FK-02",
            f"Lookup/reference parent ({parent}) -> child — zipf(alpha=1.3)",
        )
    if parent_role == TableRole.TRANSACTION and role == TableRole.TRANSACTION_DETAIL:
        return "uniform", {}, "FK-03", f"Transaction ({parent}) -> detail — uniform"
    if parent_role == TableRole.ENTITY:
        return (
            "pareto",
            {"alpha": 1.16, "max_per_parent": 50},
            "FK-01",
            f"Entity ({parent}) -> child — pareto(alpha=1.16)",
        )
    return "pareto", {"alpha": 1.16}, "FK-00", "Default FK distribution — pareto(alpha=1.16)"


def _fk_distributions(ctx: _Context) -> None:
    for tname, table in ctx.schema.tables.items():
        for cname, col in table.columns.items():
            if not col.is_foreign_key:
                continue
            parent = col.fk_ref_table
            if parent is None or (
                one_to_one_parent(table) == parent and cname in table.primary_key
            ):
                continue  # a one-to-one key takes each parent once (``ddl._foreign_key``)
            gen = col.generator
            dist, params, rule_id, desc = _fk_distribution(ctx, cname, tname, parent, table)
            gen["distribution"] = dist
            gen["params"] = params
            if col.nullable and cname not in table.primary_key:  # a key column is never null
                # A column property: the engine reads it there (a generator key is ignored).
                col.null_rate = 0.15
                ctx.annotate(tname, cname, "FK-04", "Nullable FK — added null_rate 0.15", 0.9)
            ctx.annotate(tname, cname, rule_id, desc, 0.8)


# ---- cardinality and scale --------------------------------------------------------------

_ADDRESS_CONTACT = word_pattern("address", "contact", "phone", "email", "location", "site")
_RETURN_REFUND = word_pattern("return", "refund", "reversal", "chargeback", "credit_memo", "void")
_LOG_EVENT = word_pattern(
    "log", "audit", "history", "event", "tracking", "changelog", "activity", "notification"
)


def _lookup_fixed_count(table: Table) -> int:
    """More columns, a larger reference table (20 to 200 rows)."""
    n = len(table.columns)
    if n <= 3:
        return 20
    if n <= 5:
        return 50
    if n <= 8:
        return 100
    return 200


def _pick_primary_parent(parents: list[str], ctx: _Context) -> str:
    priority = {TableRole.ENTITY: 0, TableRole.TRANSACTION: 1, TableRole.FACT: 2}
    return sorted(parents, key=lambda p: priority.get(ctx.role(p), 99))[0]


def _ratio(
    table: str, role: TableRole, parent: str, parent_role: TableRole
) -> tuple[float, str, str]:
    name = snake(table)
    if role == TableRole.BRIDGE:
        return 3.0, "CA-08", f"Bridge table — ratio 3.0 per parent ({parent})"
    if parent_role == TableRole.ENTITY and _ADDRESS_CONTACT.search(name):
        return 1.5, "CA-01", f"Entity ({parent}) -> address/contact — ratio 1.5"
    if parent_role == TableRole.TRANSACTION and _RETURN_REFUND.search(name):
        return 0.15, "CA-04", f"Transaction ({parent}) -> return/refund — ratio 0.15"
    if role == TableRole.TRANSACTION_DETAIL:
        return 2.5, "CA-03", f"Transaction ({parent}) -> detail — ratio 2.5"
    if parent_role == TableRole.ENTITY and (role == TableRole.LOG or _LOG_EVENT.search(name)):
        return 10.0, "CA-05", f"Entity ({parent}) -> log/event — ratio 10.0"
    if parent_role == TableRole.ENTITY and role == TableRole.TRANSACTION:
        return 5.0, "CA-02", f"Entity ({parent}) -> transaction — ratio 5.0"
    if role == TableRole.LOG or _LOG_EVENT.search(name):
        return 10.0, "CA-05", f"Parent ({parent}) -> log/event — ratio 10.0"
    if _RETURN_REFUND.search(name):
        return 0.15, "CA-04", f"Parent ({parent}) -> return/refund — ratio 0.15"
    return 3.0, "CA-09", f"Default child ratio — 3.0 per parent ({parent})"


def _cardinality(ctx: _Context) -> None:
    gen = ctx.schema.generation
    one_to_one: dict[str, str] = {}
    for name, table in ctx.schema.tables.items():
        parent_of_key = one_to_one_parent(table)
        if parent_of_key in ctx.schema.tables:
            one_to_one[name] = parent_of_key  # as many rows as its parent: set last, below
            continue
        role = ctx.role(name)
        parents = ctx.parents_of.get(name, [])
        if role in (TableRole.LOOKUP, TableRole.DIMENSION):
            fixed = _lookup_fixed_count(table)
            gen.derived_counts[name] = {"fixed": fixed}
            ctx.annotate(name, None, "CA-06", f"Lookup/reference table — fixed {fixed} rows", 0.85)
            continue
        if role == TableRole.HIERARCHY:
            gen.derived_counts[name] = {"fixed": 50}
            ctx.annotate(name, None, "CA-07", "Hierarchy table — fixed 50 rows", 0.8)
            continue
        if not parents:
            gen.scales.setdefault("small", {})[name] = 1_000
            gen.scales.setdefault("medium", {})[name] = 50_000
            gen.scales.setdefault("large", {})[name] = 500_000
            ctx.annotate(
                name, None, "CA-SCALE", "Root table — scale presets set (1K / 50K / 500K)", 0.9
            )
            continue
        parent = _pick_primary_parent(parents, ctx)
        ratio, rule_id, desc = _ratio(name, role, parent, ctx.role(parent))
        gen.derived_counts[name] = {"per_parent": parent, "ratio": ratio}
        ctx.annotate(name, None, rule_id, desc, 0.8)
    for name, parent in one_to_one.items():
        gen.derived_counts.pop(name, None)
        gen.derived_counts[name] = {"per_parent": parent, "ratio": 1.0}


# ---- numeric distributions --------------------------------------------------------------

_MEASUREMENT_HINTS: dict[str, tuple[float, float]] = {
    "weight": (5.0, 3.0),
    "height": (170.0, 15.0),
    "width": (50.0, 20.0),
    "length": (100.0, 40.0),
    "depth": (10.0, 5.0),
    "sqft": (1500.0, 500.0),
    "square_feet": (1500.0, 500.0),
    "lot_size": (8000.0, 3000.0),
    "area": (1200.0, 400.0),
    "volume": (500.0, 200.0),
    "distance": (50.0, 30.0),
    "duration": (60.0, 30.0),
    "temperature": (72.0, 10.0),
    "size": (10.0, 5.0),
}


def _is_placeholder_distribution(gen: dict[str, Any]) -> bool:
    """A ``distribution`` generator the parser assigned (uniform, or a normal with round
    parameters), not one somebody configured."""
    if gen.get("strategy") != "distribution":
        return False
    dist = gen.get("distribution", "")
    params = gen.get("params", {})
    if dist == "uniform":
        return True
    if dist == "normal":
        mean, std = params.get("mean", 0), params.get("std", 1)
        if isinstance(mean, int | float) and isinstance(std, int | float):
            return bool(mean == int(mean) and std == int(std))
    return False


def _numeric_generator(semantic: ColumnSemantic, col_name: str) -> dict[str, Any] | None:
    if semantic == ColumnSemantic.MONETARY:
        return {
            "strategy": "distribution",
            "distribution": "log_normal",
            "params": {"mean": 4.0, "sigma": 1.2, "min": 0.01, "max": 99999},
        }
    if semantic == ColumnSemantic.QUANTITY:
        return {
            "strategy": "distribution",
            "distribution": "log_normal",
            "params": {"mean": 1.5, "sigma": 0.8, "min": 1, "max": 1000},
        }
    if semantic == ColumnSemantic.PERCENTAGE:
        return {
            "strategy": "distribution",
            "distribution": "normal",
            "params": {"mean": 10, "std": 5, "min": 0, "max": 100},
        }
    if semantic == ColumnSemantic.MEASUREMENT:
        lower = snake(col_name)
        mean, std = 50.0, 20.0
        for keyword, (m, s) in _MEASUREMENT_HINTS.items():
            if word_pattern(keyword).search(lower):
                mean, std = m, s
                break
        return {
            "strategy": "distribution",
            "distribution": "normal",
            "params": {"mean": mean, "std": std},
        }
    if semantic == ColumnSemantic.RATING:
        return {
            "strategy": "distribution",
            "distribution": "normal",
            "params": {"mean": 3.5, "std": 1.0, "min": 1, "max": 5},
        }
    return None


def _numeric_distributions(ctx: _Context) -> None:
    for tname, table in ctx.schema.tables.items():
        found = ctx.semantics.get(tname, {})
        for cname, col in table.columns.items():
            semantic = found.get(cname)
            if semantic is None or not _is_placeholder_distribution(col.generator):
                continue
            new = _numeric_generator(semantic, cname)
            if new is not None:
                col.generator = new
                ctx.annotate(
                    tname,
                    cname,
                    f"ND-{semantic.name}",
                    f"Upgraded to {new['distribution']} distribution based on "
                    f"{semantic.name} semantic",
                    0.75,
                )


# ---- status and category mixes ----------------------------------------------------------

_TRANSACTION_STATUS = {
    "completed": 0.72,
    "pending": 0.10,
    "processing": 0.05,
    "shipped": 0.05,
    "cancelled": 0.05,
    "refunded": 0.03,
}
_ENTITY_STATUS = {"active": 0.82, "inactive": 0.10, "suspended": 0.05, "closed": 0.03}
_LOG_STATUS = {"success": 0.85, "warning": 0.10, "error": 0.05}
_STATUS_BY_ROLE: dict[TableRole, dict[str, float]] = {
    TableRole.TRANSACTION: _TRANSACTION_STATUS,
    TableRole.TRANSACTION_DETAIL: _TRANSACTION_STATUS,
    TableRole.FACT: _TRANSACTION_STATUS,
    TableRole.LOG: _LOG_STATUS,
    TableRole.ENTITY: _ENTITY_STATUS,
    TableRole.DIMENSION: _ENTITY_STATUS,
    TableRole.HIERARCHY: _ENTITY_STATUS,
    TableRole.LOOKUP: _ENTITY_STATUS,
    TableRole.BRIDGE: _ENTITY_STATUS,
    TableRole.UNKNOWN: _ENTITY_STATUS,
}
_CATEGORICAL_PROFILES: list[tuple[re.Pattern[str], dict[str, float]]] = [
    (
        word_pattern("priority"),
        {"low": 0.30, "medium": 0.45, "high": 0.20, "critical": 0.05},
    ),
    (
        word_pattern("severity"),
        {"info": 0.40, "warning": 0.30, "error": 0.20, "critical": 0.10},
    ),
    (
        re.compile(
            r"(?<![a-z0-9])(?:payment.?method|pay.?type|payment.?type)(?![a-z0-9])", re.IGNORECASE
        ),
        {
            "credit_card": 0.45,
            "debit_card": 0.25,
            "cash": 0.15,
            "bank_transfer": 0.10,
            "other": 0.05,
        },
    ),
    (re.compile(r"^(gender|sex)$", re.IGNORECASE), {"M": 0.49, "F": 0.51}),
    (
        re.compile(r"^(country|country_code|country_name)$", re.IGNORECASE),
        {"US": 0.60, "UK": 0.10, "CA": 0.08, "DE": 0.07, "FR": 0.05, "AU": 0.05, "Other": 0.05},
    ),
    (
        re.compile(r"(?<![a-z0-9])(?:level|tier)s?$", re.IGNORECASE),
        {"basic": 0.55, "silver": 0.25, "gold": 0.13, "platinum": 0.07},
    ),
]
_GENERIC_TYPE_PROFILE = {
    "type_1": 0.35,
    "type_2": 0.25,
    "type_3": 0.20,
    "type_4": 0.12,
    "type_5": 0.08,
}


def _is_placeholder_enum(gen: dict[str, Any]) -> bool:
    """A parser placeholder: faker or distribution, an empty or 50/50 two-value enum, or the
    parser's ``type_a``/``type_b`` template."""
    strategy = gen.get("strategy")
    if strategy in ("faker", "distribution"):
        return True
    if strategy != "weighted_enum":
        return False
    values = gen.get("values", {})
    if not values:
        return True
    if len(values) == 2:
        weights = list(values.values())
        if abs(weights[0] - weights[1]) < 0.01:
            return True
    keys = {k.lower() for k in values}
    if keys == {"type_a", "type_b"}:
        return True
    if keys == {"active", "inactive"} and len(values) == 2:
        weights = list(values.values())
        if abs(weights[0] - weights[1]) < 0.01:
            return True
    return False


def _enum_generator(
    semantic: ColumnSemantic, role: TableRole, col_name: str
) -> dict[str, Any] | None:
    if semantic == ColumnSemantic.STATUS:
        return {
            "strategy": "weighted_enum",
            "values": dict(_STATUS_BY_ROLE.get(role, _ENTITY_STATUS)),
        }
    if semantic == ColumnSemantic.CATEGORICAL:
        for pattern, profile in _CATEGORICAL_PROFILES:
            if pattern.search(snake(col_name)):
                return {"strategy": "weighted_enum", "values": dict(profile)}
        return {"strategy": "weighted_enum", "values": dict(_GENERIC_TYPE_PROFILE)}
    return None


def _enums(ctx: _Context) -> None:
    for tname, table in ctx.schema.tables.items():
        found = ctx.semantics.get(tname, {})
        role = ctx.role(tname)
        for cname, col in table.columns.items():
            semantic = found.get(cname)
            if semantic is None or not _is_placeholder_enum(col.generator):
                continue
            new = _enum_generator(semantic, role, cname)
            if new is not None:
                col.generator = new
                ctx.annotate(
                    tname,
                    cname,
                    f"EN-{semantic.name}",
                    f"Upgraded to realistic weighted_enum ({len(new['values'])} values) "
                    f"based on {semantic.name} semantic / {role.name} role",
                    0.7,
                )


# ---- temporal patterns ------------------------------------------------------------------

_SEASONAL_TRANSACTION: dict[str, Any] = {
    "strategy": "temporal",
    "pattern": "seasonal",
    "profiles": {
        "month": {
            "Jan": 0.071,
            "Feb": 0.068,
            "Mar": 0.083,
            "Apr": 0.082,
            "May": 0.085,
            "Jun": 0.083,
            "Jul": 0.084,
            "Aug": 0.085,
            "Sep": 0.082,
            "Oct": 0.084,
            "Nov": 0.088,
            "Dec": 0.106,
        },
        "day_of_week": {
            "Mon": 0.165,
            "Tue": 0.160,
            "Wed": 0.155,
            "Thu": 0.155,
            "Fri": 0.160,
            "Sat": 0.105,
            "Sun": 0.100,
        },
    },
}


def _is_placeholder_temporal(gen: dict[str, Any]) -> bool:
    strategy = gen.get("strategy")
    if strategy in ("faker", "distribution"):
        return True
    if strategy != "temporal":
        return False
    return gen.get("pattern") in ("uniform", None)


def _birth_range(date_range: dict[str, str]) -> dict[str, Any]:
    """Birth dates from 65 to 18 years before the model's end date."""
    end = date_range.get("end", "2025-12-31")
    try:
        year = int(end[:4])
    except (ValueError, IndexError):
        year = 2025
    return {
        "strategy": "temporal",
        "pattern": "uniform",
        "date_range": {"start": f"{year - 65}-01-01", "end": f"{year - 18}-12-31"},
    }


def _temporal_generator(
    semantic: ColumnSemantic,
    semantics: dict[str, ColumnSemantic],
    date_range: dict[str, str],
) -> dict[str, Any] | None:
    if semantic == ColumnSemantic.TEMPORAL_TRANSACTION:  # inside the model's dates
        return {**copy.deepcopy(_SEASONAL_TRANSACTION), "range_ref": "model.date_range"}
    if semantic == ColumnSemantic.TEMPORAL_END:
        start = next((c for c, s in semantics.items() if s == ColumnSemantic.TEMPORAL_START), None)
        if start is None:
            return None
        return {
            "strategy": "derived",
            "source": start,
            "rule": "add_days",
            "params": {"min": 1, "max": 365},
        }
    if semantic == ColumnSemantic.TEMPORAL_BIRTH:
        return _birth_range(date_range)
    return None  # audit, start and generic dates stay uniform


def _temporal_patterns(ctx: _Context) -> None:
    date_range = ctx.schema.model.date_range
    for tname, table in ctx.schema.tables.items():
        found = ctx.semantics.get(tname, {})
        for cname, col in table.columns.items():
            semantic = found.get(cname)
            if semantic is None or not _is_placeholder_temporal(col.generator):
                continue
            new = _temporal_generator(semantic, found, date_range)
            if new is not None:
                col.generator = new
                ctx.annotate(
                    tname,
                    cname,
                    f"TP-{semantic.name}",
                    f"Upgraded temporal pattern to '{new.get('pattern', new.get('strategy'))}' "
                    f"based on {semantic.name} semantic",
                    0.75,
                )


# ---- correlations -----------------------------------------------------------------------

_BASIC_STRATEGIES = frozenset(
    {
        "distribution",
        "uniform",
        "normal",
        "log_normal",
        "geometric",
        "bounded_normal",
        "random",
        "sequence",
        "faker",
        None,
    }
)


def _is_basic(col: Column) -> bool:
    return col.generator.get("strategy") in _BASIC_STRATEGIES


def _find_col(columns: dict[str, Column], *patterns: str) -> str | None:
    """The first column whose name has any of the terms as a whole word (or words)."""
    pattern = word_pattern(*patterns)
    return next((name for name in columns if pattern.search(snake(name))), None)


def _correlated(source: str, lo: float, hi: float) -> dict[str, Any]:
    return {
        "strategy": "correlated",
        "source_column": source,
        "rule": "multiply",
        "params": {"factor_min": lo, "factor_max": hi},
    }


def _correlations(ctx: _Context) -> None:
    for table, tdef in ctx.schema.tables.items():
        cols = tdef.columns
        sem = ctx.semantics.get(table, {})
        money = ColumnSemantic.MONETARY

        # CR-01: cost follows price
        cost, price = _find_col(cols, "cost"), _find_col(cols, "price")
        if (
            cost
            and price
            and cost != price
            and sem.get(cost) == money
            and sem.get(price) == money
            and _is_basic(cols[cost])
        ):
            cols[cost].generator = _correlated(price, 0.30, 0.70)
            ctx.annotate(
                table,
                cost,
                "CR-01",
                f"Cost ({cost}) correlated to price ({price}) via multiply(0.30–0.70)",
            )

        # CR-02: tax follows subtotal or amount
        tax = _find_col(cols, "tax")
        if tax and sem.get(tax) == money:
            base = _find_col(cols, "subtotal", "amount")
            if base and base != tax and _is_basic(cols[tax]):
                cols[tax].generator = _correlated(base, 0.05, 0.15)
                ctx.annotate(
                    table,
                    tax,
                    "CR-02",
                    f"Tax ({tax}) correlated to ({base}) via multiply(0.05–0.15)",
                )

        # CR-03: discount follows price or total
        discount = _find_col(cols, "discount")
        if discount and sem.get(discount) == money:
            base = _find_col(cols, "price", "total")
            if base and base != discount and _is_basic(cols[discount]):
                cols[discount].generator = _correlated(base, 0.05, 0.25)
                ctx.annotate(
                    table,
                    discount,
                    "CR-03",
                    f"Discount ({discount}) correlated to ({base}) via multiply(0.05–0.25)",
                )

        # CR-04: total = quantity * unit_price
        qty, unit = _find_col(cols, "quantity", "qty"), _find_col(cols, "unit_price")
        if qty and unit:
            total = _find_col(cols, "total", "line_total")
            if total and total not in (qty, unit) and _is_basic(cols[total]):
                cols[total].generator = {"strategy": "formula", "expression": f"{qty} * {unit}"}
                ctx.annotate(table, total, "CR-04", f"Total ({total}) = {qty} * {unit}")

        # CR-05: net = gross - tax
        net, gross, tax = _find_col(cols, "net"), _find_col(cols, "gross"), _find_col(cols, "tax")
        if net and gross and tax and len({net, gross, tax}) == 3 and _is_basic(cols[net]):
            cols[net].generator = {"strategy": "formula", "expression": f"{gross} - {tax}"}
            ctx.annotate(table, net, "CR-05", f"Net ({net}) = {gross} - {tax}")

        # CR-09: margin = price - cost
        margin = _find_col(cols, "margin")
        price, cost = _find_col(cols, "price"), _find_col(cols, "cost")
        if (
            margin
            and price
            and cost
            and len({margin, price, cost}) == 3
            and _is_basic(cols[margin])
        ):
            cols[margin].generator = {"strategy": "formula", "expression": f"{price} - {cost}"}
            ctx.annotate(table, margin, "CR-09", f"Margin ({margin}) = {price} - {cost}")
    # CR-06, CR-07 (date pairs) are the temporal pass's.
    _parent_totals(ctx)


_PARENT_TOTAL = ("total", "subtotal", "grand_total", "order_total", "total_amount")
_CHILD_AMOUNT = ("line_total", "line_amount", "amount", "total", "subtotal", "extended_amount")


def _parent_totals(ctx: _Context) -> None:
    """CR-08: a parent's total column is the sum of the child rows' amount column. Parents are
    generated before their children, so the column is a ``computed`` one that the compute phase
    fills once every table exists. It applies to a monetary total with a basic generator in a
    parent that has a single-column key, summed over one monetary amount column of a child that
    points at that key through exactly one foreign key. A child column that is itself a total
    (a chain of sums) is left alone, since the compute phase fills tables in schema order."""
    schema = ctx.schema
    money = ColumnSemantic.MONETARY

    def first(
        table: Table, semantics: dict[str, ColumnSemantic], terms: tuple[str, ...]
    ) -> str | None:
        return next(
            (
                c
                for term in terms
                for c in table.columns
                if semantics.get(c) == money and word_pattern(term).search(snake(c))
            ),
            None,
        )

    found: list[tuple[str, str, str, str]] = []  # parent, total, child, amount
    for rel in schema.relationships:
        parent, child = schema.tables.get(rel.parent), schema.tables.get(rel.child)
        if parent is None or child is None or parent is child:
            continue
        if parent.primary_key != rel.parent_columns or len(parent.primary_key) != 1:
            continue
        if [(r.parent, r.child) for r in schema.relationships].count((rel.parent, rel.child)) > 1:
            continue
        total = first(parent, ctx.semantics.get(rel.parent, {}), _PARENT_TOTAL)
        amount = first(child, ctx.semantics.get(rel.child, {}), _CHILD_AMOUNT)
        if total is not None and amount is not None and _is_basic(parent.columns[total]):
            found.append((rel.parent, total, rel.child, amount))
    totals = {(ptable, total) for ptable, total, _, _ in found}
    for ptable, total, ctable, amount in found:
        if (ctable, amount) in totals:
            continue
        schema.tables[ptable].columns[total].generator = {
            "strategy": "computed",
            "rule": "sum_children",
            "child_table": ctable,
            "child_column": amount,
        }
        ctx.annotate(
            ptable, total, "CR-08", f"Total ({total}) = SUM({ctable}.{amount}) over the child rows"
        )


# ---- business rules ---------------------------------------------------------------------


_CREATED = word_pattern("created")
_MODIFIED = word_pattern("modified", "updated")
_COST = word_pattern("cost")
_PRICE = word_pattern("price")


def _with(sem: dict[str, ColumnSemantic], *targets: ColumnSemantic) -> list[str]:
    return [c for c, s in sem.items() if s in targets]


def _business_rules(ctx: _Context) -> None:
    schema = ctx.schema
    existing = {r.name for r in schema.business_rules}

    def add(rule: BusinessRule, rule_id: str, description: str, column: str | None) -> None:
        if rule.name in existing:
            return
        existing.add(rule.name)
        schema.business_rules.append(rule)
        ctx.annotate(rule.table or "", column, rule_id, description)

    for table in schema.tables:
        sem = ctx.semantics.get(table, {})
        S = ColumnSemantic

        # BR-01: end >= start
        starts, ends = _with(sem, S.TEMPORAL_START), _with(sem, S.TEMPORAL_END)
        if starts and ends:
            add(
                BusinessRule(
                    f"{table}_date_order", "cross_column", f"{ends[0]} >= {starts[0]}", table
                ),
                "BR-01",
                f"{ends[0]} >= {starts[0]}",
                ends[0],
            )

        # BR-02: modified >= created
        audit = _with(sem, S.TEMPORAL_AUDIT)
        if len(audit) >= 2:
            created = modified = None
            for c in audit:
                lower = snake(c)
                if _CREATED.search(lower):
                    created = c
                elif _MODIFIED.search(lower):
                    modified = c
            if created and modified:
                add(
                    BusinessRule(
                        f"{table}_audit_date_order",
                        "cross_column",
                        f"{modified} >= {created}",
                        table,
                    ),
                    "BR-02",
                    f"{modified} >= {created}",
                    modified,
                )

        # BR-03: cost <= price
        cost = price = None
        for c in _with(sem, S.MONETARY):
            lower = snake(c)
            if _COST.search(lower) and cost is None:
                cost = c
            if _PRICE.search(lower) and price is None:
                price = c
        if cost and price and cost != price:
            add(
                BusinessRule(f"{table}_cost_lt_price", "cross_column", f"{cost} <= {price}", table),
                "BR-03",
                f"{cost} <= {price}",
                cost,
            )

        # BR-05 to BR-08: value ranges
        for c in _with(sem, S.MONETARY):
            add(
                BusinessRule(f"{table}_{c}_positive", "constraint", f"{c} >= 0", table),
                "BR-05",
                f"Monetary column {c} >= 0",
                c,
            )
        for c in _with(sem, S.QUANTITY):
            add(
                BusinessRule(f"{table}_{c}_positive", "constraint", f"{c} >= 1", table),
                "BR-06",
                f"Quantity column {c} >= 1",
                c,
            )
        for c in _with(sem, S.PERCENTAGE):
            add(
                BusinessRule(f"{table}_{c}_range", "constraint", f"{c} BETWEEN 0 AND 100", table),
                "BR-07",
                f"Percentage column {c} BETWEEN 0 AND 100",
                c,
            )
        for c in _with(sem, S.RATING):
            add(
                BusinessRule(f"{table}_{c}_range", "constraint", f"{c} BETWEEN 1 AND 5", table),
                "BR-08",
                f"Rating column {c} BETWEEN 1 AND 5",
                c,
            )

    # BR-04: a child's transaction date is not before its parent's (cross table)
    for rel in schema.relationships:
        child_dates = _with(ctx.semantics.get(rel.child, {}), ColumnSemantic.TEMPORAL_TRANSACTION)
        parent_dates = _with(ctx.semantics.get(rel.parent, {}), ColumnSemantic.TEMPORAL_TRANSACTION)
        if not child_dates or not parent_dates:
            continue
        via = rel.child_columns[0] if rel.child_columns else None
        add(
            BusinessRule(
                f"{rel.child}_after_{rel.parent}",
                "cross_table",
                f"{child_dates[0]} >= {parent_dates[0]}",
                rel.child,
                via,
            ),
            "BR-04",
            f"{rel.child}.{child_dates[0]} >= {rel.parent}.{parent_dates[0]} (via {via})",
            child_dates[0],
        )


# ---- pipeline ---------------------------------------------------------------------------


class SchemaInference:
    """The smart-inference pipeline over a :class:`GenSchema`."""

    def run(self, schema: GenSchema) -> list[Annotation]:
        """Upgrade ``schema`` in place; return every decision made, in the order made."""
        ctx = _Context(schema=schema)
        ctx.build_graphs()
        for stage in (
            _table_roles,
            _column_semantics,
            _fk_distributions,
            _cardinality,
            _numeric_distributions,
            _enums,
            _temporal_patterns,
            _correlations,
            _business_rules,
        ):
            stage(ctx)
        return ctx.annotations

"""The intentional differences from the baseline's DDL import (P4-01c).

P4-01b made Shape's ``from-ddl`` equal the baseline's in every field. The owner then decided
(2026-10-01) to fix five behaviours that would harm users' trust. Each fix changes a known, small
set of fields, listed here by input, mode and field. ``verify.py`` accepts a difference **only**
when it is listed: every other field must still equal the baseline, and an entry that no longer
matches a difference fails the run (so the list cannot go stale).

Schema paths are dotted keys of ``GenSchema.to_dict()``; a list of relationships or rules is
keyed by name. A path allows itself and everything below it. An annotation key is
``(rule id, table, column)``.
"""

from __future__ import annotations

from dataclasses import dataclass

BOTH = ("smart", "plain")
SMART = ("smart",)

FIXES: dict[str, str] = {
    "F1": (
        "A column-level REFERENCES clause (customer_id INT REFERENCES customer(id), with or "
        "without CONSTRAINT name, ON DELETE ..., a schema-qualified parent, or the parent key "
        "left out) is a foreign key to that parent column. The baseline ignores the clause and "
        "guesses <parent>.<child column>, a column that does not exist when the key is `id`; "
        "a column it never guessed (CustomerId) is now a key and so is a child in the "
        "row-count and key-distribution rules."
    ),
    "F2": (
        "VARBINARY(MAX), BINARY(MAX) and the BLOB types are binary and are left out like other "
        "binary columns. The baseline reads them as text columns."
    ),
    "F3": (
        "Names match whole words (CamelCase and snake_case split). The baseline matches "
        "substrings: `discount_pct` is a quantity ('count'), `parent_*` money ('rent'), "
        "`feedback_score` money ('fee'), `model` a category ('mode'), `catalog` a log table "
        "('log'); and a `state` string is a status enum instead of a state value. A percentage "
        "name (`_pct`, `tax_rate`) is a percentage, not money."
    ),
    "F4": (
        "A one-character coded string column (gender CHAR(1)) gets a short value set chosen by "
        "its name (M/F, A/I/P status, Y/N flag, else A/B/C), and a gender/sex column of any "
        "length gets M/F. The baseline gives a six-digit pattern (or a status word that does "
        "not fit in one character)."
    ),
    "F5": (
        "CR-08: a parent's total column is the sum of the child rows' amount column (a computed "
        "column, filled after generation). The baseline skips the rule."
    ),
}


@dataclass(frozen=True)
class Field:
    """A schema field (and below) that may differ, in these modes."""

    fix: str
    case: str
    path: str
    modes: tuple[str, ...] = BOTH


@dataclass(frozen=True)
class Note:
    """An inference annotation that may differ: it exists on one side only, or differs."""

    fix: str
    case: str
    rule_id: str
    table: str
    column: str | None
    modes: tuple[str, ...] = SMART


def _col(fix: str, case: str, table: str, column: str, modes: tuple[str, ...] = BOTH) -> Field:
    return Field(fix, case, f"tables.{table}.columns.{column}", modes)


def _gen(fix: str, case: str, table: str, column: str, modes: tuple[str, ...] = SMART) -> Field:
    return Field(fix, case, f"tables.{table}.columns.{column}.generator", modes)


def _rule(fix: str, case: str, name: str) -> Field:
    return Field(fix, case, f"business_rules.{name}", SMART)


def _total(case: str, table: str, column: str) -> list[Field | Note]:
    return [_gen("F5", case, table, column), Note("F5", case, "CR-08", table, column)]


def _percent(case: str, table: str, column: str, was: str = "ND-QUANTITY") -> list[Field | Note]:
    """``<column>`` was a quantity (``was``) and is now a percentage."""
    return [
        _gen("F3", case, table, column),
        Note("F3", case, was, table, column),
        Note("F3", case, "ND-PERCENTAGE", table, column),
        Note("F3", case, "BR-06", table, column),
        Note("F3", case, "BR-07", table, column),
        _rule("F3", case, f"{table}_{column}_positive"),
        _rule("F3", case, f"{table}_{column}_range"),
    ]


def _was_money(case: str, table: str, column: str) -> list[Field | Note]:
    """``<column>`` was money (a substring of its name said so) and is no longer."""
    return [
        _gen("F3", case, table, column),
        Note("F3", case, "ND-MONETARY", table, column),
        Note("F3", case, "BR-05", table, column),
        _rule("F3", case, f"{table}_{column}_positive"),
    ]


def _state(case: str, table: str, column: str = "state") -> list[Field | Note]:
    return [_gen("F3", case, table, column), Note("F3", case, "EN-STATUS", table, column)]


ALLOWED: list[Field | Note] = [
    # ---- F1: column-level REFERENCES ------------------------------------------------------
    Field("F1", "e2e_cli__inline", "relationships.fk_orders_customer_id.parent_columns"),
    Field("F1", "e2e_cli__inline", "tables.orders.columns.customer_id.generator.ref"),
    *(
        f
        for t, c in (
            ("pg_order", "customer_id"),
            ("my_order", "customer_id"),
            ("ansi_order", "customer_id"),
            ("invoice_line", "invoice_id"),
            ("catalog_item", "invoice_id"),
        )
        for f in (
            Field("F1", "fix_cases", f"tables.{t}.columns.{c}.generator.ref"),
            Field("F1", "fix_cases", f"relationships.fk_{t}_{c}.parent_columns"),
        )
    ),
    # CustomerId: a key now, so a child of customer in the row-count and distribution rules.
    _col("F1", "fix_cases", "ms_order", "CustomerId"),
    Field("F1", "fix_cases", "relationships.fk_ms_order_CustomerId"),
    Field("F1", "fix_cases", "generation.derived_counts.ms_order", SMART),
    *(
        Field("F1", "fix_cases", f"generation.scales.{p}.ms_order")
        for p in ("small", "medium", "large")
    ),
    Note("F1", "fix_cases", "FK-02", "ms_order", "CustomerId"),
    Note("F1", "fix_cases", "CA-09", "ms_order", None),
    Note("F1", "fix_cases", "CA-SCALE", "ms_order", None),
    # ---- F2: binary columns ---------------------------------------------------------------
    _col("F2", "fix_cases", "invoice_line", "photo"),
    _col("F2", "fix_cases", "invoice_line", "thumbnail"),
    _col("F2", "fix_cases", "invoice_line", "picture"),
    _col("F2", "fix_cases", "invoice_line", "big_picture"),
    _col("F2", "smart_retail", "products", "photo"),
    # ---- F3: whole-word names -------------------------------------------------------------
    *_state("adventureworks_sample", "addresses"),
    *_percent("adventureworks_sample", "order_details", "discount_pct"),
    # a self-referencing key read as money: only its rule differs
    Note("F3", "adventureworks_sample", "BR-05", "product_categories", "parent_category_id"),
    _rule("F3", "adventureworks_sample", "product_categories_parent_category_id_positive"),
    *_state("smart_retail", "addresses"),
    *_percent("smart_retail", "orders", "discount_pct"),
    _gen("F3", "smart_retail", "orders", "discount"),
    Note("F3", "smart_retail", "ND-QUANTITY", "orders", "discount"),
    Note("F3", "smart_retail", "BR-06", "orders", "discount"),
    _rule("F3", "smart_retail", "orders_discount_positive"),
    *_was_money("plural_fks", "item", "parent_item_id"),
    *_state("fix_cases", "invoice"),
    *_percent("fix_cases", "invoice", "discount_pct"),
    *_was_money("fix_cases", "invoice", "margin_pct"),
    *_was_money("fix_cases", "invoice", "tax_rate"),
    *_was_money("fix_cases", "invoice", "feedback_score"),
    *_was_money("fix_cases", "invoice", "current_value"),
    *(
        Note("F3", "fix_cases", rule, "invoice", col)
        for rule, col in (
            ("ND-PERCENTAGE", "margin_pct"),
            ("ND-PERCENTAGE", "tax_rate"),
            ("ND-RATING", "feedback_score"),
            ("BR-07", "margin_pct"),
            ("BR-07", "tax_rate"),
            ("BR-08", "feedback_score"),
        )
    ),
    *(
        _rule("F3", "fix_cases", f"invoice_{col}_range")
        for col in ("margin_pct", "tax_rate", "feedback_score")
    ),
    _gen("F3", "fix_cases", "invoice", "model"),
    Note("F3", "fix_cases", "EN-CATEGORICAL", "invoice", "model"),
    # catalog_item is a log table by the substring 'log'; it is not a log table.
    Note("F3", "fix_cases", "TC-LOG", "catalog_item", None),
    Note("F3", "fix_cases", "TC-UNKNOWN", "catalog_item", None),
    Note("F3", "fix_cases", "FK-07", "catalog_item", "invoice_id"),
    Note("F3", "fix_cases", "FK-02", "catalog_item", "invoice_id"),
    Note("F3", "fix_cases", "CA-05", "catalog_item", None),
    Note("F3", "fix_cases", "CA-09", "catalog_item", None),
    Field("F3", "fix_cases", "generation.derived_counts.catalog_item", SMART),
    Field("F3", "fix_cases", "tables.catalog_item.columns.invoice_id.generator.params", SMART),
    Field(
        "F3", "fix_cases", "tables.catalog_item.columns.invoice_id.generator.distribution", SMART
    ),
    # ---- F4: coded single-character columns -----------------------------------------------
    _gen("F4", "adventureworks_sample", "persons", "gender", BOTH),
    _gen("F4", "smart_retail", "customers", "gender", BOTH),
    *(
        _gen("F4", "fix_cases", "customer", c, BOTH)
        for c in ("gender", "marital_status", "is_vip", "tier_label")
    ),
    # ---- F5: CR-08 ------------------------------------------------------------------------
    *_total("adventureworks_sample", "sales_orders", "total_amount"),
    *_total("e2e_ddl_pipeline__sql_server_ddl", "order", "total"),
    *_total("smart_inference__ddl_plural", "orders", "total_amount"),
    *_total("smart_retail", "orders", "total"),
    *_total("fix_cases", "invoice", "total"),
]

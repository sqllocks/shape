"""The intentional differences from the baseline's DDL import (P4-01c).

P4-01b made Shape's ``from-ddl`` equal the baseline's in every field. The owner then decided
(2026-10-01) to fix five behaviours that would harm users' trust (F1 to F5), and the lead three
more (F6 to F8, round 2); ISS-gen added F9 to F11, AUD-gen F12 and on. Each fix changes a
known, small set of fields, listed here by input, mode and field. ``verify.py`` accepts a
difference **only** when it is listed: every other field must still equal the baseline, and an
entry that no longer matches a difference fails the run (so the list cannot go stale).

Schema paths are dotted keys of ``GenSchema.to_dict()``; a list of relationships or rules is
keyed by name. A path allows itself and everything below it. An annotation key is
``(rule id, table, column)``.
"""

from __future__ import annotations

from dataclasses import dataclass

BOTH = ("smart", "plain")
SMART = ("smart",)
PLAIN = ("plain",)

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
    "F6": (
        "A foreign key the DDL does not declare, guessed by the <table>_id naming convention, "
        "points at the parent's single-column primary key. The baseline points it at "
        "<parent>.<the column's own name> (customer.customer_id), which does not exist when the "
        "key is `id`, so the schema fails validation. A parent without a single-column primary "
        "key is not guessed at all (no reference to a column that is not there)."
    ),
    "F7": (
        "Generated strings fit the declared length. A six-digit pattern (country_code CHAR(2)) "
        "or a value set whose values are longer than the column (type_a in VARCHAR(4); active, "
        "inactive, pending in VARCHAR(5)) is replaced: a pattern by that many random "
        "characters, a value set by the values that fit or, if fewer than two fit, a code set. "
        "The baseline generates values the column cannot hold."
    ),
    "F8": (
        "A CamelCase key (CustomerId, CustomerID: the SQL Server convention) is recognised like "
        "customer_id, with the same rule for the parent key as F6, and so is a child in the "
        "row-count and key-distribution rules. The baseline needs the underscore."
    ),
    "F9": (
        "A column declared DECIMAL(p,s) or NUMERIC(p,s) is generated as decimal128(p,s) "
        "(ISS-gen, owner issue 24): its generator has output_type `decimal`. The baseline "
        "generates a double and the schema carries no such key. A distribution's `max` is cut "
        "to what the type holds (DECIMAL(3,1) never gets 100), where the baseline's default "
        "bound does not fit."
    ),
    "F10": (
        "A text column's `faker` generator passes max_nb_chars to the provider as `args` "
        "(ISS-gen, owner issue 9). The baseline writes it as a top-level key, which the "
        "strategy ignores, so the provider's own default length (200) was used."
    ),
    "F11": (
        "A nullable foreign key's 0.15 null rate (annotation FK-04) is written on the column "
        "(`null_rate`), where the engine reads it, not in the generator (ISS-gen, lead decision "
        "2026-10-02). The baseline puts it in the generator, where it is ignored, so the declared "
        "rate never took effect: a foreign key that was documented as nullable had no nulls."
    ),
    "F12": (
        "A type written in brackets, as SQL Server scripts write it ([int], [varchar](10), "
        "[bit], [smallmoney]), is read as that type (AUD-gen, issue #173). The baseline reads "
        "it as an unknown type and generates free text: [varchar](10) lost its length and "
        "[bit] its value set."
    ),
    "F13": (
        "A table named in the scale override (-s medium:customer=7,orders=21) gets that row "
        "count (AUD-gen, issue #176): smart inference's derived count for it is dropped. The "
        "baseline keeps the derived count, which takes precedence over the preset, so the "
        "override has no effect on any table inference gave a count."
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


def _notes(fix: str, case: str, *keys: tuple[str, str, str | None]) -> list[Field | Note]:
    return [Note(fix, case, rule, table, column) for rule, table, column in keys]


def _counts(
    fix: str,
    case: str,
    presets: tuple[str, ...],
    tables: tuple[str, ...],
    derived: bool = False,
    smart: bool = False,
) -> list[Field | Note]:
    """Row counts of ``tables`` that changed in the scale ``presets`` (and, with ``derived``, in
    the derived-count rules, smart mode only), because a table became (or stopped being) a child.
    ``smart`` limits the entries to smart mode (the rules that set them run only there)."""
    modes = SMART if smart else BOTH
    out: list[Field | Note] = [
        Field(fix, case, f"generation.scales.{p}.{t}", modes) for t in tables for p in presets
    ]
    if derived:
        out += [Field(fix, case, f"generation.derived_counts.{t}", SMART) for t in tables]
    return out


def _new_key(case: str, table: str, column: str) -> list[Field | Note]:
    """``<table>.<column>`` (CamelCase) was a plain integer column and is now a foreign key."""
    return [
        _gen("F8", case, table, column, BOTH),
        Field("F8", case, f"relationships.fk_{table}_{column}"),
    ]


def _retargeted(case: str, table: str, column: str) -> list[Field | Note]:
    """A guessed key that pointed at <parent>.<column> now points at the parent's key."""
    return [
        _gen("F6", case, table, column, BOTH),
        Field("F6", case, f"relationships.fk_{table}_{column}"),
    ]


def _dropped_key(case: str, table: str, column: str) -> list[Field | Note]:
    """A guessed key to a parent without a single-column key is no longer guessed."""
    return [
        _gen("F6", case, table, column, BOTH),
        Field("F6", case, f"relationships.fk_{table}_{column}"),
    ]


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
    # ---- F6: a guessed key points at the parent's primary key ------------------------------
    *_retargeted("fix_cases_round2", "sale", "client_id"),
    # a parent with a composite key: no key is guessed, so the child is a plain integer column
    *_dropped_key("fix_cases_round2", "shipment", "region_id"),
    Field("F6", "fix_cases_round2", "generation.derived_counts.shipment", SMART),
    *_counts("F6", "fix_cases_round2", ("large", "medium"), ("region",), derived=True, smart=True),
    *_counts("F6", "fix_cases_round2", ("large", "medium", "small"), ("shipment",)),
    *_notes(
        "F6",
        "fix_cases_round2",
        ("CA-06", "region", None),
        ("CA-SCALE", "region", None),
        ("CA-09", "shipment", None),
        ("CA-SCALE", "shipment", None),
        ("FK-02", "shipment", "region_id"),
        ("FK-04", "shipment", "region_id"),
        ("TC-LOOKUP", "region", None),
        ("TC-UNKNOWN", "region", None),
    ),
    # ---- F7: strings fit the declared length ------------------------------------------------
    *(
        _gen("F7", case, table, column, BOTH)
        for case, table, column in (
            ("fix_cases_round2", "locale", "country_code"),
            ("fix_cases_round2", "locale", "currency"),
            ("fix_cases_round2", "locale", "currency_code"),
            ("fix_cases_round2", "locale", "product_code"),
            ("fix_cases_round2", "locale", "region_type"),
            ("fix_cases_round2", "locale", "row_status"),
            ("fix_cases_round2", "locale", "status"),
            ("ddl_parser__comment_ddl", "dim_branch", "state_code"),
        )
    ),
    # ---- F8: CamelCase keys -----------------------------------------------------------------
    *_new_key("fix_cases_round2", "PurchaseOrder", "VendorId"),
    *_new_key("fix_cases_round2", "Receipt", "VendorID"),
    Field("F8", "fix_cases_round2", "generation.derived_counts.PurchaseOrder", SMART),
    Field("F8", "fix_cases_round2", "generation.derived_counts.Receipt", SMART),
    *_counts("F8", "fix_cases_round2", ("large", "medium", "small"), ("PurchaseOrder", "Receipt")),
    *_counts("F8", "fix_cases_round2", ("large", "medium"), ("Vendor",), derived=True, smart=True),
    *_notes(
        "F8",
        "fix_cases_round2",
        ("CA-06", "Vendor", None),
        ("CA-09", "PurchaseOrder", None),
        ("CA-09", "Receipt", None),
        ("CA-SCALE", "PurchaseOrder", None),
        ("CA-SCALE", "Receipt", None),
        ("CA-SCALE", "Vendor", None),
        ("FK-02", "PurchaseOrder", "VendorId"),
        ("FK-02", "Receipt", "VendorID"),
        ("TC-LOOKUP", "Vendor", None),
        ("TC-UNKNOWN", "Vendor", None),
    ),
    # quoted_and_exotic: Sales Order.CustomerID is a key now, and so a parent in the rules
    *_new_key("quoted_and_exotic", "Sales Order", "CustomerID"),
    *_counts("F8", "quoted_and_exotic", ("small", "medium", "large"), ("Sales Order",)),
    *_counts("F8", "quoted_and_exotic", ("medium", "large"), ("Customer",), smart=True),
    Field("F8", "quoted_and_exotic", "generation.derived_counts.Customer", SMART),
    Field("F8", "quoted_and_exotic", "generation.derived_counts.Sales Order", SMART),
    Field("F8", "quoted_and_exotic", "generation.derived_counts.line", SMART),
    _gen("F8", "quoted_and_exotic", "line", "order_id"),
    *_notes(
        "F8",
        "quoted_and_exotic",
        ("CA-03", "line", None),
        ("CA-06", "Customer", None),
        ("CA-06", "Sales Order", None),
        ("CA-09", "Sales Order", None),
        ("CA-09", "line", None),
        ("CA-SCALE", "Customer", None),
        ("FK-02", "line", "order_id"),
        ("FK-03", "line", "order_id"),
        ("FK-10", "Sales Order", "CustomerID"),
        ("TC-LOOKUP", "Customer", None),
        ("TC-LOOKUP", "Sales Order", None),
        ("TC-TRANSACTION", "Sales Order", None),
        ("TC-TRANSACTION_DETAIL", "line", None),
        ("TC-UNKNOWN", "Customer", None),
        ("TC-UNKNOWN", "line", None),
    ),
    # ---- F9: the declared DECIMAL type is kept ----------------------------------------------
    *(
        Field("F9", case, f"tables.{table}.columns.{column}.generator.output_type", modes)
        for case, table, column, modes in (
            ("adventureworks_sample", "order_details", "discount_pct", PLAIN),
            ("adventureworks_sample", "order_details", "line_total", BOTH),
            ("adventureworks_sample", "order_details", "unit_cost", BOTH),
            ("adventureworks_sample", "order_details", "unit_price", BOTH),
            ("adventureworks_sample", "products", "unit_cost", BOTH),
            ("adventureworks_sample", "products", "unit_price", BOTH),
            ("adventureworks_sample", "products", "weight_kg", BOTH),
            ("adventureworks_sample", "sales_orders", "freight", BOTH),
            ("adventureworks_sample", "sales_orders", "subtotal", SMART),
            ("ddl_parser__postgres_ddl", "order", "total", BOTH),
            ("ddl_parser__sql_server_ddl", "order", "total", BOTH),
            ("e2e_ddl_pipeline__postgres_ddl", "order", "total", BOTH),
            ("e2e_ddl_pipeline__sql_server_ddl", "order", "total", PLAIN),
            ("e2e_ddl_pipeline__sql_server_ddl", "order_line", "line_total", BOTH),
            ("e2e_ddl_pipeline__sql_server_ddl", "product", "price", BOTH),
            ("fix_cases", "invoice", "current_value", PLAIN),
            ("fix_cases", "invoice", "discount_pct", PLAIN),
            ("fix_cases", "invoice", "feedback_score", PLAIN),
            ("fix_cases", "invoice", "margin_pct", PLAIN),
            ("fix_cases", "invoice", "tax_rate", PLAIN),
            ("fix_cases", "invoice", "total", PLAIN),
            ("fix_cases", "invoice_line", "line_total", BOTH),
            ("fix_cases_round2", "sale", "amount", BOTH),
            ("smart_inference__ddl_plural", "order_lines", "line_total", BOTH),
            ("smart_inference__ddl_plural", "order_lines", "unit_price", BOTH),
            ("smart_inference__ddl_plural", "orders", "total_amount", PLAIN),
            ("smart_inference__ddl_plural", "products", "unit_cost", BOTH),
            ("smart_inference__ddl_plural", "products", "unit_price", BOTH),
            ("smart_inference__ddl_plural", "products", "weight_kg", BOTH),
            ("smart_retail", "fact_sales", "amount", BOTH),
            ("smart_retail", "order_items", "line_total", BOTH),
            ("smart_retail", "order_items", "unit_price", BOTH),
            ("smart_retail", "order_returns", "refund_amount", BOTH),
            ("smart_retail", "orders", "defect_pct", BOTH),
            ("smart_retail", "orders", "discount", PLAIN),
            ("smart_retail", "orders", "discount_pct", PLAIN),
            ("smart_retail", "orders", "gross_amount", BOTH),
            ("smart_retail", "orders", "net_amount", SMART),
            ("smart_retail", "orders", "subtotal", SMART),
            ("adventureworks_sample", "sales_orders", "subtotal", PLAIN),
            ("adventureworks_sample", "sales_orders", "tax_amount", BOTH),
            ("adventureworks_sample", "sales_orders", "total_amount", PLAIN),
            ("smart_retail", "orders", "net_amount", PLAIN),
            ("smart_retail", "orders", "subtotal", PLAIN),
            ("smart_retail", "orders", "tax", BOTH),
            ("smart_retail", "orders", "total", PLAIN),
            ("smart_retail", "products", "cost", BOTH),
            ("smart_retail", "products", "margin", BOTH),
            ("smart_retail", "products", "price", BOTH),
            ("smart_retail", "products", "rating", BOTH),
            ("smart_retail", "products", "weight", SMART),
            ("smart_retail", "shipment_lines", "amount", SMART),
            ("smart_retail", "products", "weight", PLAIN),
            ("smart_retail", "shipment_lines", "amount", PLAIN),
        )
    ),
    *(
        Field("F9", "fix_cases", f"tables.invoice.columns.{c}.generator.max", PLAIN)
        for c in ("feedback_score", "tax_rate")
    ),
    Field("F9", "smart_retail", "tables.products.columns.rating.generator.max", PLAIN),
    # F11 entries: a nullable FK's null rate moves from the generator to the column
    *(
        Field("F11", case, f"tables.{table}.columns.{column}.{where}", SMART)
        for case, table, column in (
            ("adventureworks_sample", "products", "category_id"),
            ("e2e_cli__inline", "orders", "customer_id"),
            ("fix_cases", "ansi_order", "customer_id"),
            ("fix_cases", "catalog_item", "invoice_id"),
            ("fix_cases", "my_order", "customer_id"),
            ("plural_fks", "item", "box_id"),
            ("plural_fks", "item", "bus_id"),
            ("plural_fks", "item", "category_id"),
            ("plural_fks", "item", "company_id"),
            ("plural_fks", "item", "status_id"),
            ("smart_retail", "audit_log", "customer_id"),
            ("smart_retail", "orders", "approved_by"),
            ("smart_retail", "orders", "shipping_address_id"),
        )
        for where in ("generator.null_rate", "null_rate")
    ),
    # F13 entries: a scale override drops the table's derived count
    *(
        Field("F13", case, f"generation.derived_counts.{table}", SMART)
        for case, table in (
            ("ddl_parser__mysql_ddl", "order"),
            ("ddl_parser__postgres_ddl", "order"),
            ("ddl_parser__sql_server_ddl", "order"),
            ("e2e_cli__inline", "customer"),
            ("e2e_cli__inline", "orders"),
            ("e2e_ddl_pipeline__postgres_ddl", "order"),
            ("e2e_ddl_pipeline__sql_server_ddl", "order"),
            ("fix_cases", "customer"),
            ("smart_inference__ddl_plural", "orders"),
            ("smart_retail", "orders"),
        )
    ),
    # F12 entries: bracket-quoted types
    *(
        Field("F12", "quoted_and_exotic", f"tables.Customer.columns.{column}")
        for column in ("AccountNumber", "ModifiedDate", "Rate", "Score", "Flag")
    ),
    *(
        Field("F12", "quoted_and_exotic", f"business_rules.{rule}", SMART)
        for rule in ("Customer_Rate_positive", "Customer_Score_range")
    ),
    *(
        Note("F12", "quoted_and_exotic", rule, "Customer", column)
        for rule, column in (("ND-RATING", "Score"), ("BR-05", "Rate"), ("BR-08", "Score"))
    ),
    # F10 entries
    *(
        Field("F10", case, f"tables.{table}.columns.{column}.generator.{key}", modes)
        for case, table, column, modes in (
            ("adventureworks_sample", "addresses", "address_line1", BOTH),
            ("adventureworks_sample", "customers", "account_number", BOTH),
            ("adventureworks_sample", "customers", "territory", PLAIN),
            ("adventureworks_sample", "inventory_log", "reason", PLAIN),
            ("adventureworks_sample", "product_categories", "name", SMART),
            ("adventureworks_sample", "products", "color", SMART),
            ("ddl_parser__comment_ddl", "dim_branch", "region", PLAIN),
            ("ddl_parser__comment_ddl", "dim_product", "default_note", BOTH),
            ("e2e_cli__inline", "customer", "name", BOTH),
            ("e2e_ddl_pipeline__sql_server_ddl", "product", "category", PLAIN),
            ("fix_cases", "catalog_item", "title", BOTH),
            ("fix_cases", "invoice", "model", PLAIN),
            ("fix_cases_round2", "Receipt", "Memo", BOTH),
            ("fix_cases_round2", "Vendor", "Name", BOTH),
            ("fix_cases_round2", "client", "name", BOTH),
            ("plural_fks", "boxes", "label", BOTH),
            ("plural_fks", "bus", "label", BOTH),
            ("smart_inference__ddl_plural", "categories", "name", BOTH),
            ("smart_inference__ddl_plural", "orders", "payment_method", PLAIN),
            ("smart_inference__ddl_plural", "products", "category", PLAIN),
            ("smart_inference__ddl_plural", "products", "name", BOTH),
            ("smart_retail", "addresses", "street", BOTH),
            ("smart_retail", "audit_log", "action", BOTH),
            ("smart_retail", "customers", "loyalty_tier", BOTH),
            ("smart_retail", "dim_store", "region", PLAIN),
            ("smart_retail", "products", "sku", SMART),
            ("adventureworks_sample", "product_categories", "name", PLAIN),
            ("adventureworks_sample", "products", "color", PLAIN),
            ("adventureworks_sample", "products", "name", BOTH),
            ("adventureworks_sample", "products", "product_number", BOTH),
            ("quoted_and_exotic", "Sales Order", "Payload", BOTH),
            ("quoted_and_exotic", "line", "long_text", BOTH),
            ("smart_retail", "order_returns", "reason", PLAIN),
            ("smart_retail", "orders", "payment_method", PLAIN),
            ("smart_retail", "products", "category", PLAIN),
            ("smart_retail", "products", "sku", PLAIN),
            ("adventureworks_sample", "sales_orders", "payment_method", PLAIN),
            ("adventureworks_sample", "sales_orders", "territory", PLAIN),
        )
        for key in ("args", "max_nb_chars")
    ),
]

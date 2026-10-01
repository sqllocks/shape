"""P4-01b: DDL import (``generation.ddl``, ``generation.ddl_infer``, ``shape from-ddl``)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from shape.cli.main import main
from shape.generation.ddl import DdlError, DdlParser, apply_scale, from_ddl
from shape.generation.ddl_infer import ColumnSemantic, SchemaInference, TableRole
from shape.generation.engine import calculate_row_counts, resolve_order
from shape.generation.schema import GenSchema

SQL_SERVER = """\
CREATE TABLE [dbo].[customer] (
    customer_id INT IDENTITY(1,1) NOT NULL,
    first_name NVARCHAR(50) NOT NULL,
    last_name NVARCHAR(50) NOT NULL,
    email NVARCHAR(100),
    is_active BIT DEFAULT 1,
    created_at DATETIME2 DEFAULT GETDATE(),
    CONSTRAINT PK_customer PRIMARY KEY (customer_id)
);

CREATE TABLE [dbo].[order] (
    order_id INT IDENTITY(1,1) NOT NULL,
    customer_id INT NOT NULL,
    order_date DATE NOT NULL,
    total DECIMAL(10,2),
    status VARCHAR(20),
    CONSTRAINT PK_order PRIMARY KEY (order_id),
    CONSTRAINT FK_order_customer FOREIGN KEY (customer_id)
        REFERENCES [dbo].[customer](customer_id)
);
"""

POSTGRES = """\
CREATE TABLE customer (
    customer_id SERIAL PRIMARY KEY,
    first_name VARCHAR(50) NOT NULL,
    email VARCHAR(100)
);
CREATE TABLE "order" (
    order_id BIGSERIAL PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES customer(customer_id),
    order_date DATE NOT NULL,
    total NUMERIC(10,2)
);
"""

MYSQL = """\
CREATE TABLE IF NOT EXISTS `customer` (
    `customer_id` INT AUTO_INCREMENT PRIMARY KEY,
    `first_name` VARCHAR(50) NOT NULL
);
CREATE TABLE `order` (
    `order_id` INT AUTO_INCREMENT PRIMARY KEY,
    `customer_id` INT NOT NULL,
    FOREIGN KEY (`customer_id`) REFERENCES `customer`(`customer_id`)
);
"""

COMMENTS = """\
CREATE TABLE dbo.dim_branch (
    branch_key      int          NOT NULL PRIMARY KEY,
    branch_name     varchar(20)  NOT NULL,  /* DALLAS, TAMPA, COLUMBUS, SAN DIEGO */
    state_code      char(2)      NOT NULL,
    region          varchar(12)  NOT NULL
);
CREATE TABLE dbo.dim_product (--line comment right after the paren
    product_key     int          NOT NULL PRIMARY KEY,
    product_name    varchar(32)  NOT NULL,--e.g. 30YR FIXED, 15YR FIXED
    default_note    varchar(20)  DEFAULT '10' /* not a fraction: 10/2 */
);
"""


def parse(sql: str) -> GenSchema:
    return DdlParser().parse_string(sql)


class TestTables:
    @pytest.mark.parametrize("sql", [SQL_SERVER, POSTGRES, MYSQL])
    def test_dialects_give_customer_and_order(self, sql: str) -> None:
        assert list(parse(sql).tables) == ["customer", "order"]

    def test_columns_keep_their_order(self) -> None:
        cols = parse(SQL_SERVER).tables["customer"].column_names
        assert cols == [
            "customer_id",
            "first_name",
            "last_name",
            "email",
            "is_active",
            "created_at",
        ]

    def test_primary_keys_inline_table_level_and_serial(self) -> None:
        assert parse(SQL_SERVER).tables["order"].primary_key == ["order_id"]
        assert parse(POSTGRES).tables["customer"].primary_key == ["customer_id"]
        assert parse(MYSQL).tables["customer"].primary_key == ["customer_id"]

    def test_composite_primary_key_with_clustered_and_order(self) -> None:
        schema = parse("CREATE TABLE t (a INT, b INT, PRIMARY KEY CLUSTERED (a ASC, [b] DESC));")
        assert schema.tables["t"].primary_key == ["a", "b"]

    def test_logical_types_and_sizes(self) -> None:
        schema = parse(
            "CREATE TABLE t (a DECIMAL(18,2), b NVARCHAR(40), c UNIQUEIDENTIFIER, d DATETIME2,"
            " e BIT, f DOUBLE PRECISION, g JSONB);"
        )
        cols = schema.tables["t"].columns
        assert [c.type for c in cols.values()] == [
            "decimal",
            "string",
            "uuid",
            "timestamp",
            "boolean",
            "float",
            "string",
        ]
        assert (cols["a"].precision, cols["a"].scale, cols["a"].max_length) == (18, 2, None)
        assert cols["b"].max_length == 40
        assert cols["g"].generator == {"strategy": "faker", "provider": "text", "max_nb_chars": 50}

    def test_binary_columns_are_left_out(self) -> None:
        schema = parse("CREATE TABLE t (id INT PRIMARY KEY, blob VARBINARY(100), raw BYTEA);")
        assert list(schema.tables["t"].columns) == ["id"]

    def test_nullability(self) -> None:
        cols = parse("CREATE TABLE t (a INT NOT NULL, b INT NULL, c INT);").tables["t"].columns
        assert [c.nullable for c in cols.values()] == [False, True, True]

    def test_model_defaults(self) -> None:
        m = parse(SQL_SERVER).model
        assert (m.name, m.domain, m.schema_mode, m.seed) == ("ddl_import", "custom", "3nf", 42)
        assert m.date_range == {"start": "2024-01-01", "end": "2025-12-31"}


class TestForeignKeys:
    @pytest.mark.parametrize("sql", [SQL_SERVER, MYSQL])
    def test_table_level_foreign_key_is_a_relationship_and_a_generator(self, sql: str) -> None:
        schema = parse(sql)
        (rel,) = schema.relationships
        assert (rel.parent, rel.child, rel.parent_columns, rel.child_columns) == (
            "customer",
            "order",
            ["customer_id"],
            ["customer_id"],
        )
        assert rel.name == "fk_order_customer_id"
        gen = schema.tables["order"].columns["customer_id"].generator
        assert gen == {
            "strategy": "foreign_key",
            "ref": "customer.customer_id",
            "distribution": "pareto",
        }

    def test_alter_table_foreign_key(self) -> None:
        schema = parse(
            "CREATE TABLE a (a_key INT PRIMARY KEY);"
            "CREATE TABLE b (b_key INT PRIMARY KEY, ak INT);"
            "ALTER TABLE b ADD CONSTRAINT fk FOREIGN KEY (ak) REFERENCES a(a_key);"
        )
        assert schema.tables["b"].columns["ak"].generator["ref"] == "a.a_key"
        assert [r.name for r in schema.relationships] == ["fk_b_ak"]

    def test_self_reference_is_a_self_referencing_strategy(self) -> None:
        schema = parse(
            "CREATE TABLE e (id INT PRIMARY KEY, mgr INT, FOREIGN KEY (mgr) REFERENCES e(id));"
        )
        assert schema.tables["e"].columns["mgr"].generator == {
            "strategy": "self_referencing",
            "pk_column": "id",
            "levels": 3,
            "root_count": 8,
        }

    @pytest.mark.parametrize(
        ("column", "table"),
        [
            ("category_id", "categories"),
            ("company_id", "companies"),
            ("box_id", "boxes"),
            ("status_id", "statuses"),
            ("bus_id", "bus"),
        ],
    )
    def test_naming_convention_finds_singular_and_plural_tables(
        self, column: str, table: str
    ) -> None:
        schema = parse(
            f"CREATE TABLE {table} ({column} INT PRIMARY KEY);"
            f"CREATE TABLE item (item_id INT PRIMARY KEY, {column} INT);"
        )
        assert schema.tables["item"].columns[column].generator["ref"] == f"{table}.{column}"
        assert [r.child for r in schema.relationships] == ["item"]

    def test_convention_does_not_make_a_table_point_at_itself_or_at_nothing(self) -> None:
        schema = parse("CREATE TABLE item (item_id INT PRIMARY KEY, other_id INT, id INT);")
        assert schema.relationships == []
        assert schema.tables["item"].columns["other_id"].generator["strategy"] == "distribution"


class TestGenerators:
    def test_first_generators_by_type_and_name(self) -> None:
        tables = parse(SQL_SERVER).tables
        gens = {n: c.generator for t in tables.values() for n, c in t.columns.items()}
        assert tables["customer"].columns["customer_id"].generator == {
            "strategy": "sequence",
            "start": 1,
        }
        assert gens["first_name"] == {"strategy": "faker", "provider": "first_name"}
        assert gens["email"] == {"strategy": "faker", "provider": "email"}
        assert gens["order_date"]["strategy"] == "temporal"
        assert gens["total"]["distribution"] == "normal"
        assert gens["status"]["strategy"] == "weighted_enum"
        assert gens["is_active"]["values"] == {"1": 0.85, "0": 0.15}

    def test_short_strings_get_a_pattern_and_long_ones_bounded_text(self) -> None:
        cols = (
            parse("CREATE TABLE t (a VARCHAR(8), b VARCHAR(2000), c VARCHAR(60));")
            .tables["t"]
            .columns
        )
        assert cols["a"].generator == {"strategy": "pattern", "format": "{seq:6}"}
        assert cols["b"].generator["max_nb_chars"] == 200
        assert cols["c"].generator["max_nb_chars"] == 60

    def test_generators_are_not_shared_between_columns(self) -> None:
        cols = parse("CREATE TABLE t (a BIT, b BIT);").tables["t"].columns
        cols["a"].generator["values"]["1"] = 0.5
        assert cols["b"].generator["values"]["1"] == 0.85


class TestScales:
    def test_roots_and_children_get_presets(self) -> None:
        scales = parse(SQL_SERVER).generation.scales
        assert scales["small"] == {"customer": 1000, "order": 2500}
        assert scales["medium"] == {"customer": 10000, "order": 25000}
        assert scales["large"] == {"customer": 100000, "order": 250000}

    def test_override_selects_the_preset_and_sets_rows(self) -> None:
        schema = parse(SQL_SERVER)
        apply_scale(schema, "medium:customer=5000, order=25")
        assert schema.generation.scale == "medium"
        assert schema.generation.scales["medium"] == {"customer": 5000, "order": 25}

    def test_a_bare_preset_name_only_selects(self) -> None:
        schema = parse(SQL_SERVER)
        apply_scale(schema, "large")
        assert schema.generation.scale == "large"

    def test_a_bad_count_is_an_error(self) -> None:
        with pytest.raises(DdlError, match="bad row count"):
            apply_scale(parse(SQL_SERVER), "small:customer=many")

    def test_the_engine_plans_the_import(self) -> None:
        schema, _ = from_ddl(SQL_SERVER, scale="small:customer=40")
        assert calculate_row_counts(schema)["customer"] == 40
        assert resolve_order(schema) == ["customer", "order"]


class TestComments:
    def test_commas_in_comments_do_not_split_columns(self) -> None:
        schema = parse(COMMENTS)
        assert schema.tables["dim_branch"].column_names == [
            "branch_key",
            "branch_name",
            "state_code",
            "region",
        ]
        assert schema.tables["dim_branch"].primary_key == ["branch_key"]
        assert schema.tables["dim_product"].column_names == [
            "product_key",
            "product_name",
            "default_note",
        ]

    def test_comment_markers_inside_string_literals_survive(self) -> None:
        schema = parse("CREATE TABLE t (id INT PRIMARY KEY, note VARCHAR(20) DEFAULT '10--20');")
        assert schema.tables["t"].column_names == ["id", "note"]


class TestLimits:
    def test_input_over_ten_megabytes_is_refused(self) -> None:
        with pytest.raises(DdlError, match="maximum size"):
            parse("--" + "x" * (10 * 1024 * 1024))

    def test_text_without_tables_gives_an_empty_schema(self) -> None:
        assert parse("SELECT 1;").tables == {}

    def test_unterminated_quoted_column_name_is_skipped(self) -> None:
        assert parse('CREATE TABLE t (id INT, "broken INT);').tables["t"].column_names == ["id"]


def smart(sql: str) -> tuple[GenSchema, list]:
    return from_ddl(sql, smart=True)


RETAIL = """\
CREATE TABLE customers (customer_id INT IDENTITY(1,1) PRIMARY KEY, first_name NVARCHAR(50),
    email NVARCHAR(100), date_of_birth DATE, created_at DATETIME2, updated_at DATETIME2,
    status VARCHAR(20));
CREATE TABLE products (product_id INT PRIMARY KEY, price DECIMAL(10,2), cost DECIMAL(10,2),
    margin DECIMAL(10,2), weight DECIMAL(8,2), rating DECIMAL(3,1), stock_quantity INT);
CREATE TABLE orders (order_id INT PRIMARY KEY, customer_id INT NOT NULL, order_date DATE,
    subtotal DECIMAL(10,2), tax_amount DECIMAL(10,2), total DECIMAL(10,2), state VARCHAR(20),
    discount_pct DECIMAL(5,2), defect_pct DECIMAL(5,2), payment_method VARCHAR(30));
CREATE TABLE order_lines (line_id INT PRIMARY KEY, order_id INT NOT NULL, quantity INT,
    unit_price DECIMAL(10,2), line_total DECIMAL(10,2));
CREATE TABLE contracts (contract_id INT PRIMARY KEY, start_date DATE, end_date DATE);
"""


class TestSmartInference:
    def test_roles(self) -> None:
        schema = parse(RETAIL)
        notes = SchemaInference().run(schema)
        roles = {n.table: n.rule_id for n in notes if n.rule_id.startswith("TC-")}
        assert roles["customers"] == "TC-ENTITY"
        assert roles["orders"] == "TC-TRANSACTION"
        assert roles["order_lines"] == "TC-TRANSACTION_DETAIL"

    def test_roles_enum_is_complete(self) -> None:
        assert {r.name for r in TableRole} >= {"ENTITY", "BRIDGE", "HIERARCHY", "LOG", "FACT"}
        assert ColumnSemantic.MONETARY.name == "MONETARY"

    def test_money_and_quantities_become_log_normal(self) -> None:
        schema, _ = smart(RETAIL)
        price = schema.tables["products"].columns["price"].generator
        assert price["distribution"] == "log_normal" and price["params"]["max"] == 99999
        qty = schema.tables["products"].columns["stock_quantity"].generator
        assert qty["params"] == {"mean": 1.5, "sigma": 0.8, "min": 1, "max": 1000}

    def test_measurement_and_rating(self) -> None:
        cols = smart(RETAIL)[0].tables["products"].columns
        assert cols["weight"].generator["params"] == {"mean": 5.0, "std": 3.0}
        assert cols["rating"].generator["params"]["max"] == 5

    def test_a_placeholder_status_follows_the_table_role(self) -> None:
        # `state` starts as a faker placeholder, so it is upgraded; a `status` column already has
        # the parser's three-value enum, which is not a placeholder and stays.
        schema, _ = smart(RETAIL)
        assert schema.tables["orders"].columns["state"].generator["values"]["completed"] == 0.72
        assert schema.tables["customers"].columns["status"].generator["values"] == {
            "active": 0.7,
            "inactive": 0.2,
            "pending": 0.1,
        }
        method = schema.tables["orders"].columns["payment_method"].generator["values"]
        assert method["credit_card"] == 0.45 and sum(method.values()) == pytest.approx(1.0)

    def test_dates(self) -> None:
        schema, _ = smart(RETAIL)
        order_date = schema.tables["orders"].columns["order_date"].generator
        assert (
            order_date["pattern"] == "seasonal" and order_date["profiles"]["month"]["Dec"] == 0.106
        )
        birth = schema.tables["customers"].columns["date_of_birth"].generator
        assert birth["date_range"] == {"start": "1960-01-01", "end": "2007-12-31"}
        end = schema.tables["contracts"].columns["end_date"].generator
        assert end == {
            "strategy": "derived",
            "source": "start_date",
            "rule": "add_days",
            "params": {"min": 1, "max": 365},
        }
        assert schema.tables["contracts"].columns["start_date"].generator["pattern"] == "uniform"

    def test_seasonal_profile_is_not_shared(self) -> None:
        schema, _ = smart(
            "CREATE TABLE a (id INT PRIMARY KEY, order_date DATE);"
            "CREATE TABLE b (id INT PRIMARY KEY, ship_date DATE, order_date DATE);"
        )
        a = schema.tables["a"].columns["order_date"].generator
        b = schema.tables["b"].columns["order_date"].generator
        a["profiles"]["month"]["Jan"] = 1
        assert b["profiles"]["month"]["Jan"] == 0.071

    def test_correlations_and_formulas(self) -> None:
        schema, _ = smart(RETAIL)
        assert schema.tables["products"].columns["cost"].generator == {
            "strategy": "correlated",
            "source_column": "price",
            "rule": "multiply",
            "params": {"factor_min": 0.30, "factor_max": 0.70},
        }
        assert schema.tables["products"].columns["margin"].generator == {
            "strategy": "formula",
            "expression": "price - cost",
        }
        assert schema.tables["order_lines"].columns["line_total"].generator == {
            "strategy": "formula",
            "expression": "quantity * unit_price",
        }
        assert (
            schema.tables["orders"].columns["tax_amount"].generator["source_column"] == "subtotal"
        )

    def test_fk_distribution_and_nullable_fk(self) -> None:
        schema, _ = smart(RETAIL.replace("customer_id INT NOT NULL", "customer_id INT NULL"))
        gen = schema.tables["orders"].columns["customer_id"].generator
        assert gen["distribution"] == "pareto"
        assert gen["params"] == {"alpha": 1.16, "max_per_parent": 50}
        assert gen["null_rate"] == 0.15

    def test_row_counts_and_ratios(self) -> None:
        schema, _ = smart(RETAIL)
        d = schema.generation.derived_counts
        assert d["orders"] == {"per_parent": "customers", "ratio": 5.0}
        assert d["order_lines"] == {"per_parent": "orders", "ratio": 2.5}
        assert schema.generation.scales["medium"]["customers"] == 50_000

    def test_business_rules(self) -> None:
        schema, _ = smart(RETAIL)
        rules = {r.name: (r.type, r.rule, r.table) for r in schema.business_rules}
        assert rules["contracts_date_order"] == (
            "cross_column",
            "end_date >= start_date",
            "contracts",
        )
        assert rules["customers_audit_date_order"][1] == "updated_at >= created_at"
        assert rules["products_cost_lt_price"][1] == "cost <= price"
        assert rules["products_price_positive"][1] == "price >= 0"
        assert rules["products_stock_quantity_positive"][1] == "stock_quantity >= 1"
        assert rules["orders_defect_pct_range"][1] == "defect_pct BETWEEN 0 AND 100"
        assert rules["products_rating_range"][1] == "rating BETWEEN 1 AND 5"

    def test_a_configured_generator_is_left_alone(self) -> None:
        schema = parse("CREATE TABLE t (id INT PRIMARY KEY, price DECIMAL(10,2));")
        mine = {
            "strategy": "distribution",
            "distribution": "normal",
            "params": {"mean": 12.5, "std": 2.5},
        }
        schema.tables["t"].columns["price"].generator = dict(mine)
        SchemaInference().run(schema)
        assert schema.tables["t"].columns["price"].generator == mine

    def test_camel_case_names_are_read_as_words(self) -> None:
        schema, _ = smart(
            "CREATE TABLE t (Id INT PRIMARY KEY, UnitPrice DECIMAL(10,2), OrderDate DATE);"
        )
        cols = schema.tables["t"].columns
        assert cols["UnitPrice"].generator["distribution"] == "log_normal"
        assert cols["OrderDate"].generator["pattern"] == "seasonal"

    def test_every_decision_is_explained(self) -> None:
        _, notes = smart(RETAIL)
        assert notes and all(0 < n.confidence <= 1 and n.description for n in notes)
        assert {n.table for n in notes} <= set(parse(RETAIL).tables)

    def test_no_smart_keeps_the_first_generators(self) -> None:
        schema, notes = from_ddl(RETAIL, smart=False)
        assert notes == []
        assert schema.tables["products"].columns["price"].generator["distribution"] == "normal"
        assert schema.business_rules == []

    def test_the_result_validates_and_round_trips(self) -> None:
        schema, _ = smart(RETAIL)
        assert [i for i in schema.validate() if i.level == "error"] == []
        assert (
            GenSchema.from_dict(json.loads(json.dumps(schema.to_dict()))).to_dict()
            == schema.to_dict()
        )

    def test_domain_names_the_schema(self) -> None:
        schema, _ = from_ddl(RETAIL, domain="shop")
        assert (schema.model.domain, schema.model.name) == ("shop", "shop_ddl_import")


class TestCommand:
    def test_writes_a_schema_and_reports(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        src = tmp_path / "shop.sql"
        src.write_text(RETAIL, encoding="utf-8")
        assert main(["from-ddl", str(src), "--explain"]) == 0
        out = capsys.readouterr().out
        assert "Tables: 5" in out and "Inferences:" in out and "--- Inference Report ---" in out
        assert "[TC-ENTITY] customers: Classified as ENTITY (confidence: 80%)" in out
        doc = json.loads((tmp_path / "shop.gen.json").read_text("utf-8"))
        assert doc["model"]["domain"] == "custom"
        assert GenSchema.from_dict(doc).tables.keys() == parse(RETAIL).tables.keys()

    def test_options(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        src, out = tmp_path / "a.sql", tmp_path / "x" / "b.json"
        out.parent.mkdir()
        src.write_text(RETAIL, encoding="utf-8")
        rc = main(
            [
                "from-ddl",
                str(src),
                "-o",
                str(out),
                "--domain",
                "shop",
                "-s",
                "large:orders=9",
                "--no-smart",
            ]
        )
        assert rc == 0
        doc = json.loads(out.read_text("utf-8"))
        assert doc["model"]["name"] == "shop_ddl_import"
        assert (
            doc["generation"]["scale"] == "large"
            and doc["generation"]["scales"]["large"]["orders"] == 9
        )
        assert doc["business_rules"] == []
        assert "Inferences" not in capsys.readouterr().out

    def test_missing_file_and_bad_scale_exit_2(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        assert main(["from-ddl", str(tmp_path / "nope.sql")]) == 2
        src = tmp_path / "a.sql"
        src.write_text(RETAIL, encoding="utf-8")
        assert main(["from-ddl", str(src), "-s", "small:orders=x"]) == 2
        assert "bad row count" in capsys.readouterr().err

    def test_help_lists_the_options(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as stop:
            main(["from-ddl", "--help"])
        assert stop.value.code == 0
        text = capsys.readouterr().out
        for flag in ("--output", "--domain", "--scale", "-s", "--smart", "--no-smart", "--explain"):
            assert flag in text

"""Prepare disposable local inputs for the documentation's reference examples."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sysconfig
import venv
import warnings
from pathlib import Path

import pyarrow as pa
import pyarrow.csv as pc
import pyarrow.parquet as pq
import yaml

import shape

ROOT = Path(__file__).resolve().parents[1]


def dump(path: str, document: object) -> None:
    """Write JSON beneath the disposable working directory."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(document, indent=2) + "\n")


def prepare(page: str) -> None:
    """Create seeded inputs and page-specific configuration without touching source files."""
    rows = [
        dict(
            order_id=i,
            customer_id=1 + i % 20,
            customer_email=f"person{i % 20}@example.test",
            status="paid",
            amount=round(10 + i * 0.571, 3),
            order_total=round(10 + i * 0.571, 3),
            placed_at=f"2026-09-{1 + i % 28:02d}T12:00:00",
            shipped_at=f"2026-09-{1 + i % 28:02d}T13:00:00",
            order_date=f"2026-09-{1 + i % 28:02d}T12:00:00",
            discount_code="SAVE",
            is_gift=i % 10 == 0,
            region="north" if i % 2 else "south",
            tier="standard",
            churned=i % 2 == 0,
            zip="10001",
            city="New York",
            state="NY",
            country="US",
            iban="GB82WEST12345698765432",
            notes="synthetic example",
            token=f"222-11-{i:04d}",
            ssn=f"222-11-{i:04d}",
            salary=50000 + i,
        )
        for i in range(1, 101)
    ]
    orders = pa.Table.from_pylist(rows)
    customers = pa.Table.from_pylist(
        [
            dict(
                customer_id=i,
                id=i,
                age=20 + i,
                name=f"Person {i}",
                email=f"person{i}@example.test",
                region="north" if i % 2 else "south",
                city="New York",
                born="1990-01-01",
                income=float(100 + i),
                churned=i % 2 == 0,
            )
            for i in range(1, 21)
        ]
    )
    for name in (
        "orders",
        "members",
        "export",
        "raw",
        "data",
        "today",
        "last_month",
        "this_month",
        "people",
        "population",
        "train",
        "serving",
        "real",
        "synthetic",
    ):
        table = customers if name in {"people", "population"} else orders
        pc.write_csv(table, name + ".csv")
        pq.write_table(table, name + ".parquet")
    pc.write_csv(customers, "customers.csv")
    pc.write_csv(customers, "customers_today.csv")
    pc.write_csv(customers, "customers_next.csv")
    pq.write_table(orders, "orders-today.parquet")
    Path("export.csv").write_text(Path("orders.csv").read_text().replace(",", ";"))
    for folder in (
        "data",
        "out",
        "real",
        "synthetic",
        "holdout",
        "reference",
        "current",
        "train",
        "serving",
        "real_data",
        "data/real",
        "data/synthetic",
    ):
        Path(folder).mkdir(parents=True, exist_ok=True)
        pc.write_csv(orders, Path(folder) / "orders.csv")
        pc.write_csv(customers, Path(folder) / "customers.csv")
    pq.write_table(orders, "data/orders.parquet")
    # A directory must not contain two formats for the same table.
    if page != "CONTAINER":
        Path("data/orders.parquet").unlink()
    single = shape.profile(orders, name="orders")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        for name in (
            "orders",
            "daily",
            "today",
            "baseline",
            "base",
            "new",
            "a",
            "b",
            "marts",
            "day1",
            "mon",
            "tue",
            "wed",
            "train",
            "serving",
            "orders-2026-03-01",
            "orders-2026-03-06",
            "orders-2026-03-11",
        ):
            shape.save(single, name + ".shape", capture="full")
        shape.save(shape.profile({"orders": orders, "customers": customers}), "shop.shape")
        shape.save(shape.profile(customers, name="customers"), "cust.shape", capture="full")
    Path("orders.json").write_text(json.dumps(rows))
    contract = {"columns": {"order_id": {"unique": True, "nullable": False}, "amount": {"min": 0}}}
    for name in ("contract.json", "orders.contract.json", "weak.json"):
        dump(name, contract)
    dump(
        "better.json",
        {
            "columns": {
                "order_id": {"unique": True},
                "order_total": {"min": 0},
                "status": {"nullable": False, "allowed_values": ["paid"]},
            }
        },
    )
    dump("verify.json", {"format": "shape-verify-config", "version": 1})
    dump("types.json", {"zip": "string", "amount": "float"})
    dump(
        "policy.json",
        {
            "format": "shape-vault-policy",
            "version": 1,
            "default": "none",
            "columns": {"orders.amount": "extremes"},
        },
    )
    from shape.generation.fit import fit_schema

    schema = fit_schema(single)
    schema_doc = schema.schema.to_dict()
    schema_doc["model"]["date_range"] = {"start": "2026-09-01", "end": "2026-09-30"}
    for definition in schema_doc["tables"]["orders"]["columns"].values():
        if definition["generator"]["strategy"] == "temporal":
            definition["generator"] = {
                "strategy": "temporal",
                "pattern": "uniform",
                "range_ref": "model.date_range",
            }
    for column in ("amount", "order_total"):
        schema_doc["tables"]["orders"]["columns"][column]["generator"] = {
            "strategy": "distribution",
            "distribution": "normal",
            "params": {"mean": 40, "std": 8},
        }
    for name in ("orders.gen.json", "shop.json", "shop.gen.json", "schema.json", "gates.json"):
        dump(name, schema_doc)
    Path("tables.sql").write_text(
        "CREATE TABLE orders (order_id INT PRIMARY KEY, customer_id INT, "
        "amount DECIMAL(10,2), status VARCHAR(20));\n"
    )
    dump(
        "plan.json",
        {
            "start": "2026-03-01",
            "days": 3,
            "events": [
                {
                    "kind": "distribution",
                    "table": "orders",
                    "column": "amount",
                    "start": "2026-03-02",
                    "scale": 1.3,
                }
            ],
        },
    )
    dump("answer.json", {"problems": []})
    pack = {
        "version": 1,
        "id": "my_custom_pack",
        "kind": "file_drop",
        "domain": "retail",
        "file_drop": {"formats": ["parquet"], "entities": ["customer", "order"]},
    }
    for name in ("my_pack.yaml", "packs/retail/local.yaml"):
        p = Path(name)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(pack))
    Path("estate.gsl.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "name": "local",
                "schema": {"type": "domain", "domain": "retail"},
                "scenario": {"pack": "my_pack.yaml", "scale": "small", "seed": 42},
            }
        )
    )
    if page == "GAMEDAY":
        dump(
            "plan.json",
            {
                "format": "shape-gameday",
                "version": 1,
                "data": "data",
                "rounds": [
                    {
                        "name": "nulls",
                        "inject": "null-flood",
                        "tables": ["orders"],
                        "checks": [
                            ["profile", "{data}", "--dataset", "-o", "{round}/base.shape"],
                            ["profile", "{round}", "--dataset", "-o", "{round}/today.shape"],
                            ["diff", "{round}/base.shape", "{round}/today.shape"],
                        ],
                        "expect": ["drift:null_rate_change"],
                    }
                ],
            },
        )
    if page == "EXCEL":
        from openpyxl import Workbook

        wb = Workbook()
        sheet = wb.active
        sheet.title = "Members"
        sheet.append(list(rows[0]))
        for row in rows:
            sheet.append(list(row.values()))
        wb.save("book.xlsx")
    if page == "DBT":
        shutil.copytree(ROOT / "examples/dbt_jaffle_shop", "my_dbt_project", dirs_exist_ok=True)
        shutil.copytree("my_dbt_project/models", "models", dirs_exist_ok=True)
        Path("my_dbt_project/packages.yml").unlink()
        dependencies = Path("local-dbt-packages").resolve()
        shutil.copytree(Path(os.environ["SHAPE_DOCS_DBT_PACKAGES"]), dependencies)
        (dependencies / "dbt_expectations/packages.yml").write_text(
            yaml.safe_dump({"packages": [{"local": str(dependencies / "dbt_date")}]})
        )
        dump_dependencies = {
            "packages": [
                {"local": str(dependencies / name)}
                for name in ("dbt_utils", "dbt_expectations", "dbt_date")
            ]
        }
        Path("local-packages.yml").write_text(yaml.safe_dump(dump_dependencies))
        Path("my_dbt_project/packages.yml").write_text(yaml.safe_dump(dump_dependencies))
        shutil.copytree(dependencies, "my_dbt_project/dbt_packages", dirs_exist_ok=True)
        Path("target").mkdir(exist_ok=True)
        import subprocess

        subprocess.run(
            ["dbt", "parse", "--project-dir", "my_dbt_project", "--profiles-dir", "my_dbt_project"],
            stdout=subprocess.DEVNULL,
            check=True,
        )
    if page in {
        "DEMO",
        "INSTALL",
        "DBT",
        "RELEASE_CHECKLIST",
        "GENERATION_STABILITY",
        "REFERENCE_PACKS",
        "FABRIC_PLATFORM",
        "CONTRACT_EMIT",
    }:
        for name in ("scripts", "src", "rust", "plugins", "examples", "ci"):
            shutil.copytree(
                ROOT / name,
                name,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns(
                    "__pycache__", ".pytest_cache", "target", "*.egg-info"
                ),
            )
        for name in (
            "pyproject.toml",
            "README.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "CHANGELOG.md",
            "mypy.ini",
            ".importlinter",
            "Makefile",
        ):
            if (ROOT / name).exists():
                shutil.copyfile(ROOT / name, name)
    project = {
        "format": "shape-project",
        "version": 1,
        "name": "local-reference",
        "sources": {
            "orders": {
                "path": "orders.csv",
                "baseline": {"kind": "pinned", "artifact": "baseline.shape"},
            },
            "shop": {
                "path": "data",
                "dataset": True,
                "baseline": {"kind": "pinned", "artifact": "shop.shape"},
            },
        },
    }
    if page in {
        "CONSUMER_CONTRACTS",
        "DICTIONARY",
        "FAIRNESS_AND_SKEW",
        "NOTIFICATIONS",
        "PARITY",
        "PR_BOT",
    }:
        Path("shape.yml").write_text(yaml.safe_dump(project))
        shutil.copyfile("today.shape", "current.shape")
        Path("current").mkdir(exist_ok=True)
        shutil.copyfile("today.shape", "current/orders.shape")
    if page == "CONSUMER_CONTRACTS":
        dump(
            "contracts/consumers/local.json",
            {
                "format": "shape-consumer-contract",
                "version": 1,
                "consumer": "local-reader",
                "owner": "support@shapedata.ai",
                "source": "orders",
                "since": "2026-01-01",
                "requires": contract,
            },
        )
    if page == "PLANNED_CHANGES":
        Path("shape-changes.yml").write_text(
            yaml.safe_dump(
                {
                    "format": "shape-planned-changes",
                    "version": 1,
                    "changes": [
                        {
                            "id": "orders-loyalty-tier",
                            "column": "loyalty_tier",
                            "kinds": ["column_added"],
                            "from": "2026-11-01",
                            "until": "2026-11-30",
                            "reason": "Example additive change",
                        }
                    ],
                }
            )
        )
    if page in {"CLI", "SIGNING"}:
        Path("new.shape").unlink(missing_ok=True)
        from shape.artifact.signing import load_private_key, sign_artifact, write_keypair
        from shape.cli.main import main as cli

        write_keypair("old", passphrase="local-example")
        private = load_private_key("old.key", "local-example")
        with contextlib.redirect_stdout(io.StringIO()):
            cli(["capture", "orders.parquet", "-o", "old.shape"])
        sign_artifact("old.shape", private)
        if page == "CLI":
            write_keypair("release", passphrase="local-example")
    if page == "PRIVACY_MODEL":
        import base64

        Path("KEK.key").write_text(base64.b64encode(bytes(range(32))).decode())
        Path("KEK.key").chmod(0o600)
        dump(
            "policy.json",
            {
                "format": "shape-vault-policy",
                "version": 1,
                "default": "none",
                "columns": {"orders.amount": "extremes"},
            },
        )
    if page == "DETECTIVE":
        dump("answer.json", {"format": "shape-detective-answer", "version": 1, "findings": []})
    if page == "MASK":
        import base64

        Path("mask.key").write_text(base64.b64encode(bytes(range(32))).decode())
        Path("mask.key").chmod(0o600)
        for name, table in (("orders", orders), ("customers", customers)):
            pq.write_table(table, Path("real_data") / (name + ".parquet"))
    if page == "PROFILE_MERGE":
        pq.write_table(orders.slice(0, 50), "day1.parquet")
        pq.write_table(orders.slice(50), "day2.parquet")
    if page == "streaming":
        from datetime import datetime, timedelta

        Path("landed/2026-06-02").mkdir(parents=True, exist_ok=True)
        landed = orders.append_column(
            "ts", pa.array([datetime(2026, 6, 2, 12) + timedelta(minutes=i) for i in range(100)])
        )
        pq.write_table(landed, "landed/part1.parquet")
        pq.write_table(landed, "landed/2026-06-02/part1.parquet")
    if page == "simulation":
        from shape.generation.domains import load_domain

        domain = load_domain("financial")
        document = domain.schema.to_dict()
        for dataset, table in domain.definition.reference_data.items():
            dump("financial-references/" + dataset + ".json", table.to_pylist())
        dump("schema.json", document)
    if page == "TRANSFORMS":
        dump(
            "star-map.json",
            {
                "dimensions": {
                    "dim_customer": {
                        "source": "customers",
                        "natural_key": "customer_id",
                        "key": "sk_customer",
                    }
                },
                "facts": {
                    "fact_order": {
                        "source": "orders",
                        "dimension_keys": {"customer_id": "dim_customer"},
                    }
                },
            },
        )
    if page == "EXCEL":
        Path("chaos.jsonl").write_text("")
        dump("plan.json", {"start": "2026-03-01", "days": 1, "events": []})
    if page == "IMPORTERS":
        dump(
            "order.schema.json",
            {
                "type": "object",
                "properties": {"id": {"type": "integer"}, "amount": {"type": "number"}},
            },
        )
        Path("api.yaml").write_text(
            yaml.safe_dump(
                {
                    "openapi": "3.0.0",
                    "components": {
                        "schemas": {
                            "Order": {"type": "object", "properties": {"id": {"type": "integer"}}}
                        }
                    },
                }
            )
        )
        dump(
            "events.avsc",
            {"type": "record", "name": "Event", "fields": [{"name": "id", "type": "long"}]},
        )
        Path("shop.proto").write_text(
            'syntax = "proto3"; message Order { int32 id = 1; double amount = 2; }'
        )
        Path("myapp").mkdir(exist_ok=True)
        Path("myapp/__init__.py").write_text("")
        Path("myapp/models.py").write_text(
            "from pydantic import BaseModel\nclass Order(BaseModel):\n"
            "    id: int\n    amount: float\n"
        )
        Path("retail.SemanticModel/definition/tables").mkdir(parents=True, exist_ok=True)
        Path("retail.SemanticModel/definition/tables/Order.tmdl").write_text(
            "table Order\n    column id\n        dataType: int64\n        sourceColumn: id\n"
        )
    if page == "REFERENCE_PACKS":
        Path("list-one.xml").write_text(
            "<ISO_4217><CcyTbl><CcyNtry><Ccy>USD</Ccy><CcyNbr>840</CcyNbr><CcyMnrUnts>2</CcyMnrUnts></CcyNtry></CcyTbl></ISO_4217>"
        )
        Path("ISO-639-2_utf-8.txt").write_text("eng|eng|en|English|anglais\n")
    if page == "DESIGN":
        from shape.cli.main import main as cli

        with contextlib.redirect_stdout(io.StringIO()):
            cli(
                [
                    "design",
                    "orders.csv",
                    "--from-data",
                    "--name",
                    "retail",
                    "-o",
                    "retail.design.json",
                ]
            )
    if page == "PROPOSALS":
        for path in Path("data").glob("*"):
            if path.is_file():
                path.unlink()
        generated = shape.generate("retail", scale="small", seed=42)
        for name, table in generated.tables.items():
            name = {"order": "orders", "customer": "customers"}.get(name, name)
            pc.write_csv(table, Path("data") / (name + ".csv"))
    if page == "fabric-commands":
        dump(
            "measures.json",
            {
                "format": "shape-dax-measures",
                "version": 1,
                "measures": [
                    {"name": "Lines", "table": "order_line", "aggregation": "count"},
                    {
                        "name": "Total",
                        "table": "order_line",
                        "aggregation": "sum",
                        "column": "line_total",
                    },
                ],
            },
        )
    if page == "CONTAINER":
        for name in ("src", "rust"):
            shutil.copytree(
                ROOT / name, name, ignore=shutil.ignore_patterns("__pycache__", "target", "*.so")
            )
        for name in (
            "Dockerfile",
            ".dockerignore",
            "pyproject.toml",
            "README.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
        ):
            shutil.copyfile(ROOT / name, name)
    if page == "DRIFT_REPORT":
        from shape.cli.main import main as cli

        with contextlib.redirect_stdout(io.StringIO()):
            from shape.registry.local import LocalRegistry

            LocalRegistry("reg")
            for name in ("orders-2026-03-01", "orders-2026-03-06", "orders-2026-03-11"):
                cli(["registry", "reg", "commit", "orders", name + ".shape", "--allow-raw"])
    if page == "PARITY":
        shutil.copytree("data", "dev-data", dirs_exist_ok=True)
    if page == "NOTIFICATIONS":
        Path("secrets").mkdir(exist_ok=True)
        Path("secrets/ops-webhook").write_text(os.environ["SHAPE_DOCS_WEBHOOK"])
    if page == "healthcare-codes":
        import importlib.util

        from shape_healthcare_codes.builders import icd10cm

        location = ROOT / "plugins/shape-healthcare-codes/tests/fixtures.py"
        spec = importlib.util.spec_from_file_location("docs_healthcare_fixtures", location)
        fixture = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fixture)
        fixture.software_zip(Path.cwd())
        for release in icd10cm.RELEASES:
            text = fixture.order_text(fixture.CM_R2)
            if release.member is None:
                Path(release.id + ".txt").write_text(text)
            else:
                Path(release.id + ".zip").write_bytes(
                    fixture.zip_bytes({"icd10cm-order-2027.txt": text})
                )
        Path("tabular.zip").write_bytes(
            fixture.zip_bytes({"icd10cm-tabular_-2027.xml": fixture.CM_TABULAR})
        )
        # The builder reads the ZIP member date. Keep this synthetic fixture
        # stable across CI days; it is not an upstream publication date.
        from zipfile import ZipFile, ZipInfo

        with ZipFile("ndc.zip", "w") as archive:
            for name, text in {
                "product.txt": fixture.NDC_PRODUCT,
                "package.txt": fixture.NDC_PACKAGE,
            }.items():
                archive.writestr(ZipInfo(name, (2026, 10, 9, 0, 0, 0)), text)
        Path("october-2026-alpha-numeric-hcpcs-file.zip").write_bytes(
            fixture.zip_bytes({"HCPC2026_OCT_ANWEB_1.txt": fixture.HCPCS_TEXT})
        )
        Path("cpt.csv").write_text("CPT,Descriptor\n00000,Local invented test code\n")
        Path("my_coefficients.csv").write_text(
            "model,version,segment,variable,coefficient\nCMS-HCC,V28,CNA,HCC1,0.1\n"
        )
    bootstrap = {
        "INSTALL",
        "DEMO",
        "DBT",
        "PROJECT",
        "SIGNING",
        "TESTING_WITH_SHAPE",
        "authoring",
        "behavior",
        "integrations",
        "simulation",
        "sqlserver",
        "streaming",
        "GENERATION_STABILITY",
        "RELEASE_CHECKLIST",
        "CONTRIBUTING",
    }
    if page in bootstrap:
        for name in (
            "src",
            "rust",
            "scripts",
            "plugins",
            "examples",
            "tests",
            "docs",
            "benchmarks",
        ):
            shutil.copytree(
                ROOT / name,
                name,
                dirs_exist_ok=True,
                ignore=shutil.ignore_patterns("__pycache__", ".venv", "target"),
            )
        for name in (
            "pyproject.toml",
            "README.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.md",
            "CHANGELOG.md",
            "mypy.ini",
            ".importlinter",
            "Makefile",
        ):
            if (ROOT / name).exists():
                shutil.copyfile(ROOT / name, name)
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(".venv")
        site_packages = next(Path(".venv/lib").glob("python*/site-packages"))
        (site_packages / "docs-parent.pth").write_text(
            "import site; site.addsitedir(" + repr(sysconfig.get_paths()["purelib"]) + ")\n"
        )
    if page == "CONTRIBUTING":
        import subprocess

        for name in ("site_overrides", ".github", "integrations", "demo"):
            shutil.copytree(ROOT / name, name, dirs_exist_ok=True)
        for name in (
            "mkdocs.yml",
            "requirements-docs.txt",
            ".gitignore",
            ".importlinter",
            "CONTRIBUTING.md",
            "GOVERNANCE.md",
            "SECURITY.md",
            "CODE_OF_CONDUCT.md",
        ):
            if (ROOT / name).exists():
                shutil.copyfile(ROOT / name, name)
        subprocess.run(["git", "init", "-q", "--initial-branch=main"], check=True)
        subprocess.run(
            [
                "git",
                "add",
                "src",
                "rust",
                "scripts",
                "plugins",
                "examples",
                "tests",
                "docs",
                "benchmarks",
                "site_overrides",
                "mkdocs.yml",
                "README.md",
                "THIRD_PARTY_NOTICES.md",
                "CONTRIBUTING.md",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    if page in {"INSTALL", "CONTRIBUTING"}:
        venv.EnvBuilder(with_pip=True, system_site_packages=True).create(
            "install-env" if page == "INSTALL" else "contributor-env"
        )
        site_packages = next(
            Path("install-env/lib" if page == "INSTALL" else "contributor-env/lib").glob(
                "python*/site-packages"
            )
        )
        (site_packages / "docs-parent.pth").write_text(
            "import site; site.addsitedir(" + repr(sysconfig.get_paths()["purelib"]) + ")\n"
        )
    print(f"Prepared local fixtures for {page}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page", help="reference page filename without .md")
    prepare(parser.parse_args().page)

"""P6-11 deliverable 2 (list, describe, dry_run, validate, profile_info): each command against the
CLI command it shares its code with, and its refusals."""

from __future__ import annotations

import json

import pytest

from shape.cli.main import main


def cli_json(capsys, *argv):
    code = main([str(a) for a in argv])
    out = capsys.readouterr()
    assert code in (0, 1), out.err
    return json.loads(out.out)


# ---- list -----------------------------------------------------------------------------------


def test_list_names_the_installed_domains_like_the_cli(api, capsys):
    result = api.ok("list")
    cli = cli_json(capsys, "list", "--json")
    assert [d["name"] for d in result["domains"]] == [d["name"] for d in cli]
    assert [d["modes"] for d in result["domains"]] == [d["modes"] for d in cli]
    assert result["count"] == len(cli)
    from shape import __version__

    assert result["version"] == __version__
    retail = next(d for d in result["domains"] if d["name"] == "retail")
    assert retail["profiles"][0] == "default" and retail["description"]


def test_a_domain_that_fails_to_load_is_a_warning_not_a_failure(api, monkeypatch):
    real = __import__("shape.generation.domains", fromlist=["load_domain"]).load_domain

    def flaky(name, **kw):
        if name == "retail":
            raise RuntimeError("broken plugin")
        return real(name, **kw)

    monkeypatch.setattr("shape.generation.domains.load_domain", flaky)
    response = api.call("list")
    assert response["ok"] and response["result"]["domains"][0]["profiles"] == ["default"]
    assert [w["code"] for w in response["warnings"]] == ["domain_load_failed"]


# ---- describe -------------------------------------------------------------------------------


def test_describe_matches_the_cli_description(api, capsys):
    result = api.ok("describe", domain="retail", scale="small")
    cli = cli_json(capsys, "describe", "retail", "--scale", "small", "--json")
    assert result["table_count"] == len(cli["tables"]) == 9
    assert set(result["generation_order"]) == set(cli["tables"])
    assert sorted(result["generation_order"]) == sorted(cli["tables"])
    for name, table in cli["tables"].items():
        got = result["tables"][name]
        assert got["rows"] == table["rows"] and got["primary_key"] == table["primary_key"]
        assert sorted(got["columns"], key=lambda c: c["name"]) == sorted(
            table["columns"], key=lambda c: c["name"]
        )
        assert got["column_count"] == len(table["columns"])
    assert [r["name"] for r in result["relationships"]] == [r["name"] for r in cli["relationships"]]
    assert [r["name"] for r in result["business_rules"]] == cli["business_rules"]
    assert set(result["scales"]) == set(cli["presets"])
    assert result["mode"] == "3nf" and result["scale"] == "small"


def test_describe_lists_columns_in_the_order_the_schema_declares_them(api):
    """(A generated table lists its columns in generation order, keys first: `preview` shows it.)"""
    from shape.generation.domains import load_domain

    schema = load_domain("retail").schema
    result = api.ok("describe", domain="retail")
    for name, table in result["tables"].items():
        assert [c["name"] for c in table["columns"]] == list(schema.tables[name].columns)


def test_describe_lists_dependencies_parents_first(api):
    result = api.ok("describe", domain="retail")
    order = result["generation_order"]
    for name, table in result["tables"].items():
        for parent in table["dependencies"]:
            assert order.index(parent) < order.index(name)
    assert "customer" in result["tables"]["order"]["dependencies"]
    assert result["tables"]["customer"]["dependencies"] == []


def test_describe_star_mode(api):
    star, nf = api.ok("describe", domain="retail", mode="star"), api.ok("describe", domain="retail")
    assert star["mode"] == "star" and nf["mode"] == "3nf" and star["name"] != nf["name"]


def test_describe_a_schema_file(api, schema_file):
    result = api.ok("describe", domain=str(schema_file))
    assert result["generation_order"] == ["customer", "order", "order_line"]
    assert result["tables"]["customer"]["rows"] == 40


@pytest.mark.parametrize(
    "args, code",
    [
        ({"domain": "nope"}, "input.unknown_domain"),
        ({"domain": "retail", "scale": "gigantic"}, "input.invalid_value"),
        ({"domain": "retail", "profile": "ghost"}, "input.invalid_value"),
        ({"domain": "/no/such/schema.json"}, "input.not_found"),
    ],
)
def test_describe_refusals(api, args, code):
    api.fail("describe", code, **args)


def test_describe_a_file_that_is_not_a_schema(api, tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{ not json")
    api.fail("describe", "input.invalid_schema", domain=str(bad))


# ---- dry_run --------------------------------------------------------------------------------


def test_dry_run_plans_the_cli_plan(api, capsys):
    result = api.ok("dry_run", domain="retail", scale="small")
    cli = cli_json(capsys, "generate", "retail", "--scale", "small", "--dry-run", "--json")
    assert sorted(result["generation_order"]) == sorted(cli["order"])
    for table, parents in api.ok("describe", domain="retail")["tables"].items():
        for parent in parents["dependencies"]:  # a parent is generated before its children
            assert result["generation_order"].index(parent) < result["generation_order"].index(
                table
            )
    assert result["planned_rows"] == {t: cli["tables"][t]["rows"] for t in cli["order"]}
    assert result["total_rows"] == cli["total_rows"] == sum(result["planned_rows"].values())
    assert result["ok"] is True and result["scale"] == "small"


def test_dry_run_default_scale_and_no_rows_generated(api, monkeypatch):
    from shape.generation.engine import Engine

    monkeypatch.setattr(
        Engine, "generate", lambda self: (_ for _ in ()).throw(AssertionError("generated"))
    )
    result = api.ok("dry_run", domain="retail")
    assert result["total_rows"] > 0


def test_dry_run_refuses_a_schema_with_a_dangling_reference(api, tmp_path, schema_file):
    doc = json.loads(schema_file.read_text())
    doc["tables"]["order"]["columns"]["customer_id"]["generator"]["ref"] = "ghost.id"
    broken = tmp_path / "broken.json"
    broken.write_text(json.dumps(doc))
    e = api.fail("dry_run", "input.invalid_schema", domain=str(broken))
    assert "ghost" in e["message"]


def test_dry_run_unknown_scale(api):
    api.fail("dry_run", "input.invalid_value", domain="retail", scale="gigantic")


# ---- validate -------------------------------------------------------------------------------


def test_validate_a_good_schema(api, schema_file, capsys):
    result = api.ok("validate", schema_path=str(schema_file))
    cli = cli_json(capsys, "validate", schema_file)
    assert result["valid"] is True and cli["valid"] is True
    assert result["table_count"] == cli["tables"] == 3
    assert result["relationship_count"] >= 2
    assert result["errors"] == [] and result["kind"] == "generation-schema"
    assert result["name"] == cli["name"]


def test_validate_reports_errors_with_locations(api, tmp_path, schema_file):
    doc = json.loads(schema_file.read_text())
    doc["tables"]["order"]["primary_key"] = ["no_such_column"]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(doc))
    result = api.ok("validate", schema_path=str(path))
    assert result["valid"] is False and result["errors"]
    assert all(set(e) == {"location", "message"} for e in result["errors"])
    assert any("no_such_column" in e["message"] for e in result["errors"])


def test_validate_a_document_that_is_not_a_schema(api, tmp_path):
    path = tmp_path / "x.json"
    path.write_text('{"hello": 1}')
    api.fail("validate", "input.invalid_schema", schema_path=str(path))


def test_validate_a_missing_file(api, tmp_path):
    api.fail("validate", "input.not_found", schema_path=str(tmp_path / "nope.json"))


def test_validate_a_contract(api, tmp_path):
    path = tmp_path / "c.json"
    path.write_text(json.dumps({"name": "c", "fields": {}}))
    result = api.ok("validate", schema_path=str(path))
    assert result["kind"] == "contract"


def test_validate_requires_the_path(api):
    api.fail("validate", "usage.missing_argument")
    api.fail("validate", "usage.invalid_argument", schema_path="")


# ---- profile_info ---------------------------------------------------------------------------


def test_profile_info_reports_the_weights_the_domain_generates_with(api):
    result = api.ok("profile_info", domain="retail")
    assert result["domain"] == "retail" and result["profile"] == "default"
    assert result["available_profiles"][0] == "default"
    assert result["distributions"]["customer.gender"] == {"M": 0.49, "F": 0.51}
    assert result["distribution_keys"] == sorted(result["distributions"])
    d = result["distributions"]
    assert d["order.customer_id"] == {"distribution": "pareto", "alpha": 1.16, "max_per_parent": 50}
    assert d["order.store_id"] == {"distribution": "zipf", "alpha": 1.3}
    assert d["order_line.quantity"] == {"distribution": "geometric", "p": 0.6, "min": 1, "max": 20}
    assert d["order.order_date.month"]["Dec"] == 0.106 and d["order.order_date.hour_of_day"][
        "peaks"
    ] == [12, 20]
    assert {p["name"] for p in d["product.product_status"]["phases"]} == {
        "introduced",
        "active",
        "discontinued",
    }
    assert result["ratios"] == {
        "address_per_customer": 1.5,
        "order_line_per_order": 2.5,
        "return_per_order": 0.17,
    }
    assert result["ratios"]["address_per_customer"] == 1.5
    assert result["ratio_keys"] == sorted(result["ratios"])
    assert abs(sum(result["distributions"]["customer.loyalty_tier"].values()) - 1.0) < 1e-9


def test_profile_info_refusals(api):
    api.fail("profile_info", "input.unknown_domain", domain="nope")
    api.fail("profile_info", "input.invalid_value", domain="retail", profile="ghost")


def test_a_listed_profile_that_cannot_be_applied_says_so(api, monkeypatch):
    from shape.generation import domains

    real = domains.load_domain

    def with_profile(name, **kw):
        loaded = real(name, **kw)
        loaded.definition.profiles["holiday"] = {"customer.gender": {"M": 0.1, "F": 0.9}}  # type: ignore[index]
        return loaded

    monkeypatch.setattr(domains, "load_domain", with_profile)
    assert "holiday" in api.ok("list")["domains"][0]["profiles"]
    api.fail("generate", "policy.capability_unavailable", domain="retail", profile="holiday")

"""``shape profile-model``: a whole semantic model as a dataset profile, against a fake sempy."""

from __future__ import annotations

import io
import json
import re
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fake_sempy import FakeFabric, FakeModel, FakeTable, install, retail
from shape_fabric import commands
from shape_fabric import semantic_source as ss
from shape_fabric.semantic_profile import profile_model

import shape
from shape.cli.main import main
from shape.errors import ShapeError
from shape.generation.learn import learn
from shape.plugins import kit
from shape.profile.reference.profile import ARTIFACT_FORMAT, ARTIFACT_FORMAT_VERSION
from shape.proposals import propose_relationships

# What a declared relationship adds to a profile; the rest is the profiler's own statistics.
KEY_FIELDS = {"is_primary_key", "is_foreign_key", "fk_ref_table", "fk_evidence", "hidden"}


@pytest.fixture(autouse=True)
def _fresh_warnings():
    ss._reset_warnings()


@pytest.fixture
def fabric(monkeypatch):
    return install(monkeypatch, FakeFabric(retail()))


def run(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(["profile-model", *argv])
    return code, out.getvalue(), err.getvalue()


def stats(table: dict) -> dict:
    return {
        name: {k: v for k, v in col.items() if k not in KEY_FIELDS}
        for name, col in table["columns"].items()
    }


def two_tables(relationships, extra_cols=()):
    a = FakeTable(
        columns=[("Id", "Int64", False), ("Label", "String", False)],
        rows=pd.DataFrame({"Id": [1, 2, 3], "Label": ["x", "y", "z"]}),
    )
    b = FakeTable(
        columns=[
            ("Id", "Int64", False),
            ("AId", "Int64", False),
            ("A2Id", "Int64", False),
            *extra_cols,
        ],
        rows=pd.DataFrame({"Id": [1, 2, 3, 4], "AId": [1, 1, 2, 3], "A2Id": [3, 2, 2, 1]}),
    )
    c = FakeTable(columns=[("Tag", "String", False)], rows=pd.DataFrame({"Tag": ["x", "y", "x"]}))
    return FakeModel(tables={"A": a, "B": b, "C": c}, relationships=relationships)


def rel(frm, fcol, to, tcol, mult="m:1", active=True):
    return {
        "From Table": frm,
        "From Column": fcol,
        "To Table": to,
        "To Column": tcol,
        "Multiplicity": mult,
        "Active": active,
    }


# --- 3: the profile --------------------------------------------------------------------------


def test_every_table_is_profiled_and_the_relationship_is_declared(fabric):
    prof = profile_model("Sales", "Retail")
    data = prof.to_dict()
    assert list(data["tables"]) == ["Customer", "Orders", "Odd 'table'", "Empty"]
    (r,) = data["relationships"]
    assert r["evidence"] == "declared" and r["source"] == "semantic model"
    assert (r["parent"], r["child"]) == ("Customer", "Orders")
    assert (r["parent_columns"], r["child_columns"]) == (["CustomerKey"], ["CustomerKey"])
    assert r["type"] == "one_to_many" and r["active"] is True
    assert r["name"] == "fk_Orders_CustomerKey"
    col = data["tables"]["Orders"]["columns"]["CustomerKey"]
    assert col["is_foreign_key"] and col["fk_ref_table"] == "Customer"
    assert col["fk_evidence"] == "declared"
    assert data["tables"]["Orders"]["detected_fks"] == {"CustomerKey": "Customer"}
    # the "one" side of a declared relationship is a key
    assert data["tables"]["Customer"]["primary_key"] == ["CustomerKey"]


def test_the_statistics_equal_profiling_the_same_rows_from_parquet(fabric, tmp_path):
    tables = {}
    for name in ("Customer", "Orders"):
        uri = ss.build("Sales", "Retail", name)
        src = ss.SemanticModelSource()
        tables[name] = pa.Table.from_batches(list(src.read(uri)), schema=src.schema(uri))
        pq.write_table(tables[name].replace_schema_metadata(None), tmp_path / f"{name}.parquet")
    from_model = profile_model("Sales", "Retail", tables=["Customer", "Orders"]).to_dict()
    from_files = shape.profile({n: str(tmp_path / f"{n}.parquet") for n in tables}).to_dict()
    for name in tables:
        a, b = from_model["tables"][name], from_files["tables"][name]
        assert a["row_count"] == b["row_count"]
        assert stats(a) == stats(b)


def test_hidden_columns_are_included_and_marked(fabric):
    cols = profile_model("Sales", "Retail").to_dict()["tables"]["Customer"]["columns"]
    assert cols["CustomerKey"]["hidden"] is True
    assert "hidden" not in cols["Name"]


def test_an_unknown_type_is_read_as_string_and_reported_once(fabric, capsys):
    data = profile_model("Sales", "Retail", tables=["Customer"]).to_dict()
    assert data["tables"]["Customer"]["columns"]["Code"]["dtype"] == "string"
    assert capsys.readouterr().err.count("Customer[Code] has type Variant") == 1


def test_tables_select_a_subset_and_relationships_to_others_are_left_out(fabric):
    data = profile_model("Sales", "Retail", tables=["Orders"]).to_dict()
    assert list(data["tables"]) == ["Orders"]
    assert data["relationships"] == []
    assert not data["tables"]["Orders"]["columns"]["CustomerKey"]["is_foreign_key"]


def test_an_unknown_table_names_it(fabric):
    with pytest.raises(ShapeError) as err:
        profile_model("Sales", "Retail", tables=["Orders", "Custmer"])
    assert str(err.value) == "semantic model Retail in workspace Sales has no table Custmer"


def test_an_empty_tables_list_is_refused(fabric):
    with pytest.raises(ShapeError, match="tables"):
        profile_model("Sales", "Retail", tables=[])


@pytest.mark.parametrize("cap,rows", [(0, 0), (1, 1), (5, 5), (6, 5), (100, 5)])
def test_max_rows_caps_every_table(fabric, cap, rows):
    data = profile_model("Sales", "Retail", tables=["Orders"], max_rows=cap).to_dict()
    assert data["tables"]["Orders"]["row_count"] == rows


def test_a_negative_max_rows_is_refused(fabric):
    with pytest.raises(ShapeError, match="max_rows"):
        profile_model("Sales", "Retail", max_rows=-1)


def test_a_model_with_no_tables_is_refused(monkeypatch):
    install(monkeypatch, FakeFabric({"W": {"M": FakeModel(tables={})}}))
    with pytest.raises(ShapeError, match="has no tables"):
        profile_model("W", "M")


def test_provenance_names_the_model(fabric):
    prov = profile_model("Sales", "Retail", max_rows=3).provenance
    assert prov == {
        "source": "semantic-model",
        "workspace": "Sales",
        "model": "Retail",
        "max_rows": 3,
    }


# --- relationships: inactive, many-to-many, the other directions -----------------------------


def test_an_inactive_relationship_is_recorded_with_active_false(monkeypatch):
    model = two_tables([rel("B", "AId", "A", "Id"), rel("B", "A2Id", "A", "Id", active=False)])
    install(monkeypatch, FakeFabric({"W": {"M": model}}))
    data = profile_model("W", "M").to_dict()
    by_col = {r["child_columns"][0]: r for r in data["relationships"]}
    assert by_col["AId"]["active"] is True and by_col["A2Id"]["active"] is False
    assert by_col["A2Id"]["evidence"] == "declared"
    # an inactive relationship is still a declared key (a role-playing dimension)
    assert data["tables"]["B"]["columns"]["A2Id"]["fk_ref_table"] == "A"


def test_a_many_to_many_relationship_is_recorded_and_not_a_foreign_key(monkeypatch):
    model = two_tables([rel("C", "Tag", "A", "Label", mult="m:m")])
    install(monkeypatch, FakeFabric({"W": {"M": model}}))
    data = profile_model("W", "M").to_dict()
    (r,) = data["relationships"]
    assert r["type"] == "many_to_many" and r["evidence"] == "declared"
    col = data["tables"]["C"]["columns"]["Tag"]
    assert not col["is_foreign_key"] and col["fk_ref_table"] is None
    assert data["tables"]["C"]["detected_fks"] == {}
    assert data["tables"]["A"]["primary_key"] != ["Label"]  # not the "one" side of a key


@pytest.mark.parametrize(
    "mult,child,parent,kind",
    [
        ("m:1", "B", "A", "one_to_many"),
        ("1:m", "B", "A", "one_to_many"),
        ("1:1", "B", "A", "one_to_one"),
    ],
)
def test_the_multiplicity_gives_the_direction(monkeypatch, mult, child, parent, kind):
    model = two_tables([rel("B", "Id", "A", "Id", mult=mult)])
    if mult == "1:m":
        model.relationships = [rel("A", "Id", "B", "Id", mult=mult)]
    install(monkeypatch, FakeFabric({"W": {"M": model}}))
    (r,) = profile_model("W", "M").to_dict()["relationships"]
    assert (r["child"], r["parent"], r["type"]) == (child, parent, kind)


def test_an_unknown_multiplicity_is_reported_and_skipped(monkeypatch, capsys):
    model = two_tables([rel("B", "AId", "A", "Id", mult="weird")])
    install(monkeypatch, FakeFabric({"W": {"M": model}}))
    assert profile_model("W", "M").to_dict()["relationships"] == []
    err = capsys.readouterr().err
    assert "shape: warning: semantic-model relationship B[AId] -> A[Id]" in err
    assert "weird" in err


def test_a_relationship_naming_an_unknown_column_is_skipped(monkeypatch, capsys):
    model = two_tables([rel("B", "Nope", "A", "Id")])
    install(monkeypatch, FakeFabric({"W": {"M": model}}))
    assert profile_model("W", "M").to_dict()["relationships"] == []
    assert "B[Nope]" in capsys.readouterr().err


# --- consumers: shape proposals and shape generate --from treat them as declared keys --------


def test_proposals_treat_a_declared_relationship_as_a_declared_key(monkeypatch):
    model = two_tables([rel("B", "AId", "A", "Id"), rel("C", "Tag", "A", "Label", mult="m:m")])
    install(monkeypatch, FakeFabric({"W": {"M": model}}))
    prof = profile_model("W", "M")
    found = {p.id: p for p in propose_relationships(prof, min_confidence=0.0)}
    assert found["relationship:B.AId->A.Id"].evidence["profiler_detected"] is True
    assert not any(pid.startswith("relationship:C.Tag") for pid in found)


def test_generation_uses_foreign_keys_and_leaves_many_to_many_out(monkeypatch):
    model = two_tables(
        [
            rel("B", "AId", "A", "Id"),
            rel("B", "A2Id", "A", "Id", active=False),
            rel("C", "Tag", "A", "Label", mult="m:m"),
        ]
    )
    install(monkeypatch, FakeFabric({"W": {"M": model}}))
    schema = learn(profile_model("W", "M"))
    assert sorted((r.child, tuple(r.child_columns)) for r in schema.relationships) == [
        ("B", ("A2Id",)),
        ("B", ("AId",)),
    ]
    gen = schema.tables["B"].columns["AId"].generator
    assert gen["strategy"] == "foreign_key" and gen["ref"] == "A.Id"


# --- the saved profile ------------------------------------------------------------------------


def test_the_saved_profile_declares_its_format_and_round_trips(fabric, tmp_path):
    out = tmp_path / "m.shape"
    prof = profile_model("Sales", "Retail")
    shape.save(prof, out, capture="full")  # W1-11: the round trip compares every value
    from shape.artifact.io import read_artifact

    manifest, _ = read_artifact(str(out))
    assert manifest["format"] == ARTIFACT_FORMAT
    assert manifest["format_version"] == ARTIFACT_FORMAT_VERSION
    assert isinstance(manifest["format_version"], int)
    again = shape.load(out)
    assert again.to_dict() == prof.to_dict()
    assert again.to_dict()["relationships"][0]["evidence"] == "declared"
    assert again.provenance == prof.provenance


# --- the command ------------------------------------------------------------------------------


def test_the_command_conforms_and_is_published():
    import importlib.metadata as md

    points = {ep.name: ep.value for ep in md.entry_points(group="shape.commands")}
    assert points["profile-model"] == "shape_fabric.commands:ProfileModelCommand"
    cmd = commands.ProfileModelCommand()
    assert cmd.name == "profile-model" and cmd.help
    assert kit.check_common(cmd, "shape.commands") is None


def test_the_command_writes_the_profile_and_prints_a_summary(fabric, tmp_path):
    out = tmp_path / "m.shape"
    code, stdout, err = run("Sales/Retail", "-o", str(out), "--tables", "Customer,Orders")
    assert code == 0, err
    assert out.is_file()
    assert "2 tables" in stdout and "1 relationship" in stdout and str(out) in stdout
    data = shape.load(out).to_dict()
    assert list(data["tables"]) == ["Customer", "Orders"]


def test_the_command_json_flag_prints_one_json_document(fabric, tmp_path):
    out = tmp_path / "m.shape"
    code, stdout, _ = run("Sales/Retail", "-o", str(out), "--json", "--max-rows", "2")
    assert code == 0
    doc = json.loads(stdout)
    assert doc["written"] == str(out)
    assert re.fullmatch(r"[0-9a-f]{64}", doc["shape_content_id"])
    assert doc["tables"] == 4 and doc["relationships"] == 1
    assert doc["max_rows"] == 2
    assert shape.load(out).to_dict()["tables"]["Orders"]["row_count"] == 2


def test_the_command_decodes_a_percent_encoded_name(monkeypatch, tmp_path):
    model = two_tables([])
    install(monkeypatch, FakeFabric({"Sales/EU": {"M": model}}))
    code, _, err = run("Sales%2FEU/M", "-o", str(tmp_path / "x.shape"))
    assert code == 0, err


@pytest.mark.parametrize("arg", ["Sales", "Sales/", "/Retail", "a/b/c", ""])
def test_a_bad_workspace_model_argument_exits_2(fabric, tmp_path, arg):
    code, _, err = run(arg, "-o", str(tmp_path / "x.shape"))
    assert code == 2
    assert err.startswith("shape: error:") and "WORKSPACE/MODEL" in err
    assert not (tmp_path / "x.shape").exists()


def test_the_command_needs_an_output(fabric):
    code, _, err = run("Sales/Retail")
    assert code == 2 and "-o" in err


def test_an_unknown_table_exits_2_naming_it(fabric, tmp_path):
    code, _, err = run("Sales/Retail", "-o", str(tmp_path / "x.shape"), "--tables", "Custmer")
    assert code == 2
    assert err.strip() == (
        "shape: error: semantic model Retail in workspace Sales has no table Custmer"
    )
    assert not (tmp_path / "x.shape").exists()


def test_an_unknown_workspace_and_model_exit_2(fabric, tmp_path):
    code, _, err = run("Nope/Retail", "-o", str(tmp_path / "x.shape"))
    assert (code, err.strip()) == (2, "shape: error: semantic-model workspace Nope not found")
    code, _, err = run("Sales/Nope", "-o", str(tmp_path / "x.shape"))
    assert (code, err.strip()) == (2, "shape: error: workspace Sales has no semantic model Nope")


@pytest.mark.parametrize("bad", ["-1", "x", "1.5"])
def test_a_bad_max_rows_exits_2(fabric, tmp_path, bad):
    code, _, err = run("Sales/Retail", "-o", str(tmp_path / "x.shape"), "--max-rows", bad)
    assert code == 2 and "max-rows" in err.replace("_", "-")


def test_without_sempy_the_command_exits_2_with_one_line(tmp_path, monkeypatch):
    import builtins
    import sys

    real = builtins.__import__

    def refuse(name, *a, **k):
        if name == "sempy" or name.startswith("sempy."):
            raise ModuleNotFoundError("No module named 'sempy'", name="sempy")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", refuse)
    for key in ("sempy", "sempy.fabric"):
        monkeypatch.delitem(sys.modules, key, raising=False)
    code, _, err = run("Sales/Retail", "-o", str(tmp_path / "x.shape"))
    assert code == 2
    assert err == (
        "shape: error: semantic-model:// needs sempy (pip install "
        "'sqllocks-shape-fabric[semantic-link]') and runs inside a Fabric notebook\n"
    )


def test_the_kit_runs_the_command_against_the_fake(fabric, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    kit.check_command(
        commands.ProfileModelCommand(), argv=["Sales/Retail", "-o", "k.shape"], expect_exit=0
    )
    assert Path("k.shape").is_file()

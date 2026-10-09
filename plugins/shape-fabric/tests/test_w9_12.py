"""W9-12 offline acceptance: semantic metadata, native role reads and preserved exports."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest
from fake_sempy import FakeFabric, FakeModel, FakeTable, install
from shape_fabric import known_answer as ka
from shape_fabric.semantic_profile import profile_model
from shape_fabric.semantic_source import SemanticModelSource

import shape
from shape.cli.main import main
from shape.drift.policy import gate
from shape.errors import ShapeError
from shape.generation.fit import fit_schema

MEASURE = {
    "name": "Own total",
    "expression": "SUM('Sales'[Amount])",
    "formatString": "0.00",
    "displayFolder": "Finance/Own",
    "hidden": True,
}
ROLE = {
    "name": "Region North",
    "modelPermission": "read",
    "tablePermissions": [{"name": "Sales", "filterExpression": "'Sales'[Region] = \"North\""}],
}


@pytest.mark.parametrize("as_profile", [False, True])
def test_wanted4_metadata_records_are_independent_of_input_documents(as_profile):
    from shape.drift.model_metadata import records
    from shape.profile.reference import Profile

    before = {
        "tables": {"Sales": {"measures": [copy.deepcopy(MEASURE)]}},
        "roles": [copy.deepcopy(ROLE)],
    }
    after = copy.deepcopy(before)
    after["tables"]["Sales"]["measures"][0]["expression"] = "COUNTROWS('Sales')"
    after["roles"][0]["tablePermissions"][0]["filterExpression"] = "FALSE()"
    left, right = (Profile(before), Profile(after)) if as_profile else (before, after)
    original_before, original_after = copy.deepcopy(before), copy.deepcopy(after)
    changes = records(left, right)
    for _, _, change in changes:
        if change["kind"] == "role_change":
            change["baseline"]["tablePermissions"][0]["filterExpression"] = "MUTATED"
            change["current"]["tablePermissions"][0]["filterExpression"] = "MUTATED"
        else:
            change["baseline"]["expression"] = "MUTATED"
            change["current"]["expression"] = "MUTATED"
    assert (left.to_dict() if as_profile else left) == original_before
    assert (right.to_dict() if as_profile else right) == original_after


def test_wanted4_plain_profiles_do_not_copy_numeric_documents(monkeypatch):
    from shape.drift.model_metadata import records

    profile = shape.profile(pa.table({"amount": [1, 2, 3]}))

    def forbidden(*args):
        raise AssertionError("metadata comparison copied the entire numeric profile")

    monkeypatch.setattr(type(profile), "to_dict", forbidden)
    assert records(profile, profile) == []


def test_wanted4_unknown_profile_interface_keeps_dictionary_fallback():
    from shape.drift.model_metadata import records

    class ProfileLike:
        def to_dict(self):
            return {"roles": [ROLE]}

    assert records(ProfileLike(), ProfileLike()) == []


@pytest.fixture
def model(monkeypatch):
    table = FakeTable(
        [("Amount", "Double", True), ("Region", "String", False)],
        pd.DataFrame({"Amount": [2.0, 3.0, 7.0], "Region": ["North", "North", "South"]}),
    )
    model = FakeModel({"Sales": table})
    model.measures = {"Sales": [copy.deepcopy(MEASURE)]}
    model.roles = [copy.deepcopy(ROLE)]
    fab = install(monkeypatch, FakeFabric({"Workspace": {"Model": model}}))
    return model, fab


def test_wanted1_profile_measures_roles_roundtrip_and_old_profile(model, tmp_path):
    profile = profile_model("Workspace", "Model")
    doc = profile.to_dict()
    assert doc["tables"]["Sales"]["measures"] == [MEASURE]
    assert doc["roles"] == [ROLE]
    shape.save(profile, tmp_path / "model.shape", capture="full")
    assert shape.load(tmp_path / "model.shape").to_dict() == json.loads(json.dumps(doc))
    model[0].measures = {}
    model[0].roles = []
    empty = profile_model("Workspace", "Model").to_dict()
    assert empty["tables"]["Sales"]["measures"] == [] and empty["roles"] == []
    old = shape.profile(pa.table({"Amount": [1, 2]}))
    shape.save(old, tmp_path / "old.shape")
    assert "roles" not in shape.load(tmp_path / "old.shape").to_dict()
    assert model[1].called("connect_semantic_model")[0]["readonly"] is True


@pytest.mark.parametrize("cap", [None, 0, 1, 100])
def test_wanted2_role_native_filters_cap_and_unknown(model, cap):
    source = SemanticModelSource()
    uri = "semantic-model://Workspace/Model/Sales"
    options = {"as_role": ROLE["name"]}
    if cap is not None:
        options["max_rows"] = cap
    batches = list(source.read(uri, **options))
    rows = pa.Table.from_batches(batches, schema=source.schema(uri, **options)).to_pydict()
    assert rows["Region"] == ["North"] * (2 if cap is None else min(2, cap))
    if cap != 0:
        assert model[1].called("evaluate_dax")[-1]["role"] == ROLE["name"]
    with pytest.raises(ShapeError, match="Region North"):
        list(source.read(uri, as_role="unknown"))
    with pytest.raises(ShapeError, match="mode"):
        list(source.read(uri, as_role=ROLE["name"], mode="onelake"))


@pytest.mark.parametrize("role", ["", 1, True, []])
def test_wanted2_invalid_role_refused_before_read(model, role):
    with pytest.raises(ShapeError, match="as_role"):
        list(SemanticModelSource().read("semantic-model://Workspace/Model/Sales", as_role=role))
    assert not model[1].called("read_table")


def test_wanted3_export_profile_keeps_own_metadata_and_unknown_dax(model, tmp_path):
    profile = profile_model("Workspace", "Model")
    path = tmp_path / "model.shape"
    shape.save(profile, path)
    out = tmp_path / "model.bim"
    assert main(["export-model", "--from-profile", str(path), "-o", str(out)]) == 0
    tom = json.loads(out.read_text())["model"]
    assert tom["roles"] == [ROLE]
    expected = {**MEASURE, "isHidden": MEASURE["hidden"]}
    del expected["hidden"]
    assert tom["tables"][0]["measures"] == [expected]
    assert main(["export-model", "retail", "--from-profile", str(path), "-o", str(out)]) == 2
    model[0].measures["Sales"].append(
        {**MEASURE, "name": "Unsupported", "expression": "CALCULATE([Own total], ALL('Sales'))"}
    )
    shape.save(profile_model("Workspace", "Model"), path)
    directory = tmp_path / "answers"
    assert (
        main(["known-answer", str(path), "--measures", "from-profile", "-o", str(directory)]) == 0
    )
    answers = ka.load_answers(directory / "answers.json")
    assert [m["name"] for m in answers["measures"]] == ["Own total"]
    assert answers["skipped"] == [
        {"slice": None, "measure": "Sales.Unsupported", "reason": "unsupported DAX expression"}
    ]
    assert (
        len(json.loads((directory / "model.bim").read_text())["model"]["tables"][0]["measures"])
        == 2
    )


def test_wanted3_known_answers_use_own_aggregates_not_defaults(model, tmp_path):
    from shape_fabric.semantic_metadata import profile_measures

    profile = profile_model("Workspace", "Model")
    schema = fit_schema(profile).schema
    tables = {"Sales": pa.table({"Amount": [2.0, 3.0, 7.0], "Region": ["North", "North", "South"]})}
    measures, skipped = profile_measures(profile, schema, tables)
    assert skipped == [] and len(measures) == 1
    queries, skipped = ka.compute_answers(schema, tables, measures, [])
    assert skipped == [] and float(queries[0]["rows"][0]["values"]["Sales.Own total"]) == 12.0
    model[0].measures = {"Sales": [{**MEASURE, "expression": "SUM('Other'[Amount])"}]}
    unsupported, skipped = profile_measures(profile_model("Workspace", "Model"), schema, tables)
    assert unsupported == [] and skipped[0]["reason"] == "unsupported DAX expression"


def test_wanted4_measure_role_changes_reported_excluded_numeric_gate(model):
    before = profile_model("Workspace", "Model")
    model[0].measures["Sales"][0]["expression"] = "AVERAGE('Sales'[Amount])"
    model[0].roles[0]["tablePermissions"][0]["filterExpression"] = "FALSE()"
    after = profile_model("Workspace", "Model")
    diff = shape.diff(before, after)
    assert {c["kind"] for c in diff.changes} == {"measure_change", "role_change"}
    assert all(c["score"] == 0 for c in diff.changes)
    assert gate(before, after, default_max=0).passed
    assert shape.diff(after, after).changes == []
    doc = after.to_dict()
    doc.pop("roles")
    doc["tables"]["Sales"].pop("measures")
    from shape.profile.reference import Profile

    missing = shape.diff(after, Profile(doc))
    assert {c["kind"] for c in missing.changes} == {"measure_change", "role_change"}


def test_wanted5_hidden_plain_profile_model_parity_and_cli(model, tmp_path):
    uri = "semantic-model://Workspace/Model/Sales"
    plain = shape.profile(uri).to_dict()
    assert shape.profile(source=uri).to_dict() == plain
    whole = profile_model("Workspace", "Model").to_dict()["tables"]["Sales"]
    assert plain["columns"] == whole["columns"]
    for name in ("Amount", "Region"):
        assert plain["columns"][name].get("hidden", False) == whole["columns"][name].get(
            "hidden", False
        )
    assert plain["columns"]["Amount"]["hidden"] is True
    assert "hidden" not in plain["columns"]["Region"]
    out = tmp_path / "plain.shape"
    assert main(["profile", uri, "-o", str(out)]) == 0
    assert shape.load(out).to_dict()["columns"]["Amount"]["hidden"] is True


def test_wanted6_documented_options():
    root = Path(__file__).resolve().parents[3]
    for path in [root / "docs/SEMANTIC_MODEL.md", root / "docs/FABRIC_PLATFORM.md"]:
        text = path.read_text()
        for option in [
            "as_role",
            "--from-profile",
            "--measures from-profile",
            "measure_change",
            "role_change",
        ]:
            assert option in text
    assert "W9-12" in (root / "CHANGELOG.md").read_text()


@pytest.mark.parametrize("call", ["list_measures", "connect_semantic_model"])
def test_wanted1_remote_metadata_errors_never_echo_credentials(model, monkeypatch, call):
    def fail(**kwargs):
        raise RuntimeError("Bearer super-private-token")

    monkeypatch.setattr(model[1], call, fail)
    with pytest.raises(ShapeError) as caught:
        profile_model("Workspace", "Model")
    assert "super-private-token" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_wanted2_role_remote_errors_never_echo_credentials(model, monkeypatch):
    def fail(**kwargs):
        raise RuntimeError("Bearer super-private-token")

    monkeypatch.setattr(model[1], "evaluate_dax", fail)
    with pytest.raises(ShapeError) as caught:
        list(
            SemanticModelSource().read(
                "semantic-model://Workspace/Model/Sales", as_role=ROLE["name"]
            )
        )
    assert "super-private-token" not in str(caught.value)
    assert caught.value.__suppress_context__


def test_wanted4_metadata_order_cosmetic_policy_and_numeric_changes(model):
    model[0].measures["Sales"].append(
        {**MEASURE, "name": "Own average", "expression": "AVERAGE('Sales'[Amount])"}
    )
    model[0].roles.append({"name": "Empty role", "modelPermission": "read", "tablePermissions": []})
    before = profile_model("Workspace", "Model")
    model[0].measures["Sales"].reverse()
    model[0].roles.reverse()
    after = profile_model("Workspace", "Model")
    assert shape.diff(before, after).changes == []
    model[0].measures["Sales"][0]["hidden"] = False
    changed = profile_model("Workspace", "Model")
    result = shape.diff(after, changed, fail_on="cosmetic")
    assert result.failed and result.changes[0]["class"] == "cosmetic"
    assert shape.diff(after, changed, thresholds={"min_severity": "high"}).changes == []
    assert shape.diff(after, changed, only_columns=["Sales.Amount"]).changes == []
    model[0].tables["Sales"].rows = pd.DataFrame({"Amount": [2.0] * 20, "Region": ["North"] * 20})
    numeric = profile_model("Workspace", "Model")
    assert not gate(after, numeric, default_max=0).passed


def test_wanted5_non_uri_profile_is_identical_to_reference_and_full_artifact(tmp_path):
    from shape.profile.reference import profile as reference_profile

    table = pa.table({"hidden": [1, 2], "visible": ["a", "b"]})
    actual, expected = shape.profile(table), reference_profile(table)
    assert actual.to_dict() == expected.to_dict()
    shape.save(actual, tmp_path / "plain.shape", capture="full")
    shape.save(expected, tmp_path / "reference.shape", capture="full")
    assert (
        shape.load(tmp_path / "plain.shape").to_dict()
        == shape.load(tmp_path / "reference.shape").to_dict()
    )


@pytest.mark.parametrize("value", [None, float("nan"), pd.NA])
def test_wanted1_nullable_optional_measure_fields_are_empty(model, monkeypatch, value):
    real = model[1].list_measures

    def optional(**kwargs):
        frame = real(**kwargs)
        frame["Format String"] = value
        frame["Measure Display Folder"] = value
        frame["Measure Hidden"] = value
        return frame

    monkeypatch.setattr(model[1], "list_measures", optional)
    measure = profile_model("Workspace", "Model").to_dict()["tables"]["Sales"]["measures"][0]
    assert measure["formatString"] == measure["displayFolder"] == ""
    assert measure["hidden"] is False


@pytest.mark.parametrize(
    "native,expected",
    [
        ("None", "none"),
        ("Read", "read"),
        ("ReadRefresh", "readRefresh"),
        ("Refresh", "refresh"),
        ("Administrator", "administrator"),
    ],
)
def test_wanted1_role_permission_json_enum_casing(model, native, expected):
    model[0].roles[0]["modelPermission"] = native
    assert profile_model("Workspace", "Model").to_dict()["roles"][0]["modelPermission"] == expected


def test_wanted1_unknown_role_permission_is_refused(model):
    model[0].roles[0]["modelPermission"] = "unknown"
    with pytest.raises(ShapeError, match="TOM roles"):
        profile_model("Workspace", "Model")


def test_wanted3_measure_home_table_can_differ_from_data_table(model):
    from shape_fabric.semantic_metadata import profile_measures

    model[0].tables["Metrics"] = FakeTable(
        [("Placeholder", "Int64", False)], pd.DataFrame({"Placeholder": [0]})
    )
    model[0].measures = {"Metrics": [copy.deepcopy(MEASURE)]}
    profile = profile_model("Workspace", "Model")
    schema = fit_schema(profile).schema
    tables = {name: pa.Table.from_pandas(table.rows) for name, table in model[0].tables.items()}
    measures, skipped = profile_measures(profile, schema, tables)
    assert skipped == []
    assert measures[0].id == "Metrics.Own total"
    assert measures[0].document()["table"] == "Metrics"
    queries, skipped = ka.compute_answers(schema, tables, measures, ["Sales.Region"])
    assert skipped == []
    assert float(queries[0]["rows"][0]["values"]["Metrics.Own total"]) == 12.0
    assert {r["key"][0]: float(r["values"]["Metrics.Own total"]) for r in queries[1]["rows"]} == {
        "North": 5.0,
        "South": 7.0,
    }
    assert "'Metrics'[Own total]" in ka.queries_dax(
        {
            "queries": queries,
            "measures": [measures[0].document()],
            "tables": {t: table.num_rows for t, table in tables.items()},
        }
    )


@pytest.mark.parametrize(
    "expression,expected",
    [
        ("COUNTROWS('Sales')", 3),
        ("SUM('Sales'[Amount])", 12),
        ("AVERAGE('Sales'[Amount])", 4),
        ("MIN('Sales'[Amount])", 2),
        ("MAX('Sales'[Amount])", 7),
    ],
)
def test_wanted3_supported_own_measure_aggregates(model, expression, expected):
    from shape_fabric.semantic_metadata import profile_measures

    model[0].measures["Sales"][0]["expression"] = expression
    profile = profile_model("Workspace", "Model")
    schema = fit_schema(profile).schema
    tables = {"Sales": pa.Table.from_pandas(model[0].tables["Sales"].rows)}
    measures, skipped = profile_measures(profile, schema, tables)
    assert skipped == []
    queries, skipped = ka.compute_answers(schema, tables, measures, [])
    assert skipped == [] and float(queries[0]["rows"][0]["values"]["Sales.Own total"]) == expected


def test_wanted4_permission_order_is_not_a_role_change(model):
    model[0].roles[0]["tablePermissions"].append({"name": "Other", "filterExpression": "TRUE()"})
    before = profile_model("Workspace", "Model")
    model[0].roles[0]["tablePermissions"].reverse()
    assert shape.diff(before, profile_model("Workspace", "Model")).changes == []


@pytest.mark.parametrize(
    "expression,expected",
    [("sum(Sales[Amount])", 12), ("COUNTROWS(Sales)", 3), ("AVERAGE ( Sales [Amount] )", 4)],
)
def test_wanted3_unquoted_table_names_are_computable(model, expression, expected):
    from shape_fabric.semantic_metadata import profile_measures

    model[0].measures["Sales"][0]["expression"] = expression
    profile = profile_model("Workspace", "Model")
    schema = fit_schema(profile).schema
    tables = {"Sales": pa.Table.from_pandas(model[0].tables["Sales"].rows)}
    measures, skipped = profile_measures(profile, schema, tables)
    assert skipped == []
    queries, skipped = ka.compute_answers(schema, tables, measures, [])
    assert skipped == [] and float(queries[0]["rows"][0]["values"]["Sales.Own total"]) == expected


def test_wanted1_older_sempy_missing_tom_has_actionable_error(model, monkeypatch):
    monkeypatch.setattr(model[1], "connect_semantic_model", None)
    with pytest.raises(ShapeError, match="connect_semantic_model.*upgrade.*0.14.2"):
        profile_model("Workspace", "Model")


@pytest.mark.parametrize(
    "call", ["list_workspaces", "list_datasets", "list_tables", "list_columns"]
)
def test_wanted2_role_catalog_errors_redact_credentials(model, monkeypatch, call):
    def fail(**kwargs):
        raise RuntimeError("Bearer super-private-token")

    monkeypatch.setattr(model[1], call, fail)
    with pytest.raises(ShapeError) as caught:
        list(
            SemanticModelSource().read(
                "semantic-model://Workspace/Model/Sales", as_role=ROLE["name"]
            )
        )
    assert "super-private-token" not in str(caught.value)
    assert caught.value.__suppress_context__

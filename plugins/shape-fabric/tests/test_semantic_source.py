"""The ``semantic-model://`` source, against a fake ``sempy`` (no Fabric)."""

from __future__ import annotations

import builtins
import importlib.metadata as md
import subprocess
import sys

import pandas as pd
import pyarrow as pa
import pytest
from fake_sempy import FakeFabric, FakeModel, FakeTable, install, retail
from shape_fabric import semantic_source as ss
from shape_fabric.semantic_source import SemanticModelSource

from shape.errors import ShapeError
from shape.plugins import kit

URI = "semantic-model://Sales/Retail/Customer"


@pytest.fixture(autouse=True)
def _fresh_warnings():
    ss._reset_warnings()


@pytest.fixture
def fabric(monkeypatch):
    return install(monkeypatch, FakeFabric(retail()))


def table_of(source, uri, **options):
    schema = source.schema(uri, **options)
    return pa.Table.from_batches(list(source.read(uri, **options)), schema=schema)


# --- 1: URI, schema, read, options --------------------------------------------------------


def test_the_entry_point_and_scheme_are_published():
    points = {ep.name: ep.value for ep in md.entry_points(group="shape.sources")}
    assert points["semantic-model"] == "shape_fabric.semantic_source:SemanticModelSource"
    assert SemanticModelSource.schemes == ("semantic-model",)
    assert SemanticModelSource().name == "semantic-model"


def test_can_open_only_its_scheme():
    s = SemanticModelSource()
    assert s.can_open(URI)
    assert not s.can_open("onelake://a/b/Tables/c")
    assert not s.can_open("/tmp/x.csv")


def test_the_kit_accepts_the_source(fabric):
    kit.check_source(SemanticModelSource(), URI, unrelated_uri="mssql://h/db/t")


@pytest.mark.parametrize(
    "uri,parts",
    [
        ("semantic-model://Sales/Retail/Customer", ("Sales", "Retail", "Customer")),
        ("semantic-model://a%2Fb/Retail/Cust%20omer", ("a/b", "Retail", "Cust omer")),
        ("semantic-model://ws/m%2Fx/t%2Fy", ("ws", "m/x", "t/y")),
        (
            "semantic-model://8b6a0f7e-1c2d-4e3f-9a4b-5c6d7e8f9a0b/1f2e3d4c-5b6a-4978-8695-a4b3c2d1e0f9/T",
            (
                "8b6a0f7e-1c2d-4e3f-9a4b-5c6d7e8f9a0b",
                "1f2e3d4c-5b6a-4978-8695-a4b3c2d1e0f9",
                "T",
            ),
        ),
    ],
)
def test_parse(uri, parts):
    assert ss.parse(uri) == parts


@pytest.mark.parametrize(
    "uri",
    [
        "semantic-model://",
        "semantic-model://Sales",
        "semantic-model://Sales/Retail",
        "semantic-model://Sales//Customer",
        "semantic-model://Sales/Retail/Customer/extra",
        "semantic-model:///Retail/Customer",
        "onelake://a/b/c",
    ],
)
def test_a_malformed_uri_is_a_shape_error_naming_the_form(uri):
    with pytest.raises(ShapeError, match=r"semantic-model://<workspace>/<model>/<table>"):
        ss.parse(uri)


def test_schema_comes_from_column_metadata_and_reads_no_rows(fabric):
    schema = SemanticModelSource().schema(URI)
    assert schema.names == ["CustomerKey", "Name", "Code", "Joined"]
    assert schema.field("CustomerKey").type == pa.int64()
    assert fabric.called("read_table") == []
    assert fabric.called("evaluate_dax") == []
    assert fabric.called("list_columns")


def test_read_gives_the_rows_in_the_schema(fabric):
    t = table_of(SemanticModelSource(), URI)
    assert t.num_rows == 4
    assert t.column("CustomerKey").to_pylist() == [1, 2, 3, 4]
    assert t.column("Name").to_pylist() == ["Ann", "Bo", None, "Di"]
    assert fabric.called("read_table")[0]["table"] == "Customer"


def test_the_workspace_and_model_may_be_guids(fabric):
    ws_id = "11111111-1111-1111-1111-000000000000"
    model_id = "00000000-0000-0000-0000-0000000000aa"
    t = table_of(SemanticModelSource(), f"semantic-model://{ws_id}/{model_id}/Customer")
    assert t.num_rows == 4


@pytest.mark.parametrize("rows,sizes", [(65_536, [4]), (3, [3, 1]), (1, [1, 1, 1, 1]), (4, [4])])
def test_batch_rows_caps_every_batch(fabric, rows, sizes):
    batches = list(SemanticModelSource().read(URI, batch_rows=rows))
    assert [b.num_rows for b in batches] == sizes


def test_the_default_batch_rows_is_65536(fabric):
    assert ss.DEFAULT_BATCH_ROWS == 65_536


@pytest.mark.parametrize("bad", [0, -1, True, 1.5, "3"])
def test_a_bad_batch_rows_is_refused(fabric, bad):
    with pytest.raises(ShapeError, match="batch_rows"):
        list(SemanticModelSource().read(URI, batch_rows=bad))


def test_columns_select_a_subset_in_the_order_given(fabric):
    s = SemanticModelSource()
    assert s.schema(URI, columns=["Name", "CustomerKey"]).names == ["Name", "CustomerKey"]
    t = table_of(s, URI, columns=["Name", "CustomerKey"])
    assert t.column_names == ["Name", "CustomerKey"] and t.num_rows == 4


def test_an_empty_or_repeating_columns_list_is_refused(fabric):
    s = SemanticModelSource()
    with pytest.raises(ShapeError, match="columns"):
        s.schema(URI, columns=[])
    with pytest.raises(ShapeError, match="more than once"):
        s.schema(URI, columns=["Name", "Name"])


def test_an_empty_table_reads_no_batches(fabric):
    s = SemanticModelSource()
    uri = "semantic-model://Sales/Retail/Empty"
    assert s.schema(uri).names == ["Id"]
    assert list(s.read(uri)) == []


def test_an_unknown_option_is_refused(fabric):
    with pytest.raises(ShapeError, match="unknown option"):
        SemanticModelSource().schema(URI, colums=["Name"])


# --- max_rows ---------------------------------------------------------------------------------


@pytest.mark.parametrize("cap,expected", [(0, 0), (1, 1), (4, 4), (5, 4), (1000, 4)])
def test_max_rows_boundaries(fabric, cap, expected):
    t = table_of(SemanticModelSource(), URI, max_rows=cap)
    assert t.num_rows == expected
    assert t.column_names == ["CustomerKey", "Name", "Code", "Joined"]


def test_max_rows_is_read_as_topn_through_dax(fabric):
    table_of(SemanticModelSource(), URI, max_rows=2)
    assert fabric.called("read_table") == []
    (call,) = fabric.called("evaluate_dax")
    assert call["dax"].startswith("EVALUATE SELECTCOLUMNS(TOPN(2, 'Customer')")
    assert table_of(SemanticModelSource(), URI, max_rows=2).column("CustomerKey").to_pylist() == [
        1,
        2,
    ]


def test_max_rows_zero_asks_sempy_for_no_rows(fabric):
    table_of(SemanticModelSource(), URI, max_rows=0)
    assert fabric.called("read_table") == []


@pytest.mark.parametrize("bad", [-1, True, 1.5, "2"])
def test_a_bad_max_rows_is_refused(fabric, bad):
    with pytest.raises(ShapeError, match="max_rows"):
        table_of(SemanticModelSource(), URI, max_rows=bad)


def test_mode_is_passed_to_read_table(fabric):
    table_of(SemanticModelSource(), URI, mode="onelake")
    assert fabric.called("read_table")[0]["mode"] == "onelake"
    table_of(SemanticModelSource(), URI)
    assert fabric.called("read_table")[1]["mode"] == "xmla"


def test_mode_with_max_rows_is_refused(fabric):
    with pytest.raises(ShapeError, match="mode"):
        table_of(SemanticModelSource(), URI, mode="onelake", max_rows=2)


def test_mode_is_refused_when_this_sempy_cannot_take_it(monkeypatch):
    install(monkeypatch, FakeFabric(retail(), read_table_has_mode=False))
    with pytest.raises(ShapeError, match="mode"):
        table_of(SemanticModelSource(), URI, mode="onelake")
    assert table_of(SemanticModelSource(), URI).num_rows == 4  # without mode it still reads


# --- 2: the type map ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,arrow",
    [
        ("Int64", pa.int64()),
        ("int64", pa.int64()),
        ("Double", pa.float64()),
        ("Decimal", pa.decimal128(19, 4)),
        ("Currency", pa.decimal128(19, 4)),
        ("String", pa.string()),
        ("Boolean", pa.bool_()),
        ("DateTime", pa.timestamp("us")),
        ("Binary", pa.binary()),
    ],
)
def test_the_type_map(name, arrow):
    assert ss.arrow_type(name) == arrow


def test_the_type_map_in_the_docs_is_the_code_map():
    from pathlib import Path

    doc = (Path(__file__).parents[3] / "docs/plugins/cloud-sources.md").read_text("utf-8")
    for name, arrow in ss.TYPE_MAP.items():
        assert f"`{name}`" in doc and str(arrow) in doc


def test_every_type_reads_into_its_arrow_type(fabric):
    s = SemanticModelSource()
    schema = s.schema("semantic-model://Sales/Retail/Orders")
    assert [str(f.type) for f in schema] == [
        "int64",
        "int64",
        "decimal128(19, 4)",
        "double",
        "bool",
        "binary",
    ]
    t = table_of(s, "semantic-model://Sales/Retail/Orders")
    assert t.column("Amount").to_pylist()[0] == pytest.approx(10.5)
    assert t.column("Paid").to_pylist() == [True, False, True, True, None]
    assert t.column("Blob").to_pylist()[-1] is None


def test_an_unknown_type_is_read_as_string_and_reported_once(fabric, capsys):
    s = SemanticModelSource()
    schema = s.schema(URI)
    assert schema.field("Code").type == pa.string()
    table_of(s, URI)
    table_of(s, URI)
    err = capsys.readouterr().err
    assert err.count("shape: warning: semantic-model column Customer[Code] has type Variant;") == 1
    assert "read as string" in err


def test_the_unknown_type_is_not_reported_for_columns_not_read(fabric, capsys):
    SemanticModelSource().schema(URI, columns=["Name"])
    assert capsys.readouterr().err == ""


def test_unknown_type_values_are_converted_to_strings(monkeypatch):
    model = FakeModel(
        tables={
            "T": FakeTable(
                columns=[("V", "Variant", False)], rows=pd.DataFrame({"V": [1, "x", None]})
            )
        }
    )
    install(monkeypatch, FakeFabric({"W": {"M": model}}))
    t = table_of(SemanticModelSource(), "semantic-model://W/M/T")
    assert t.column("V").to_pylist() == ["1", "x", None]


def test_hidden_columns_are_included(fabric):
    assert "CustomerKey" in SemanticModelSource().schema(URI).names
    assert ss.column_infos("Sales", "Retail", "Customer")[0].hidden is True


def test_a_value_that_does_not_fit_its_type_names_the_column(monkeypatch):
    model = FakeModel(
        tables={
            "T": FakeTable(columns=[("N", "Int64", False)], rows=pd.DataFrame({"N": ["abc", "d"]}))
        }
    )
    install(monkeypatch, FakeFabric({"W": {"M": model}}))
    with pytest.raises(ShapeError, match=r"T\[N\]"):
        table_of(SemanticModelSource(), "semantic-model://W/M/T")


def test_columns_without_a_data_type_column_read_as_string_and_say_so(monkeypatch, capsys):
    install(monkeypatch, FakeFabric(retail(), data_type_column=None))
    schema = SemanticModelSource().schema(URI)
    assert {str(f.type) for f in schema} == {"string"}
    assert "has type" in capsys.readouterr().err


# --- 4: sempy is imported only when a semantic-model URI is opened; failures are one line ----


def test_importing_the_module_and_the_plugin_does_not_import_sempy():
    code = (
        "import sys, shape_fabric, shape_fabric.semantic_source as m;"
        "m.SemanticModelSource().can_open('semantic-model://a/b/c');"
        "print('sempy' in sys.modules)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


def test_without_sempy_the_source_fails_with_one_line(monkeypatch):
    real = builtins.__import__

    def refuse(name, *a, **k):
        if name == "sempy" or name.startswith("sempy."):
            raise ModuleNotFoundError("No module named 'sempy'", name="sempy")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", refuse)
    for key in ("sempy", "sempy.fabric"):
        monkeypatch.delitem(sys.modules, key, raising=False)
    with pytest.raises(ShapeError) as err:
        SemanticModelSource().schema(URI)
    msg = str(err.value)
    assert "\n" not in msg
    assert msg == (
        "semantic-model:// needs sempy (pip install 'sqllocks-shape-fabric[semantic-link]') "
        "and runs inside a Fabric notebook"
    )


def test_a_sempy_that_cannot_sign_in_fails_the_same_way(monkeypatch):
    fab = FakeFabric(retail())

    def no_auth(**_):
        raise RuntimeError("no Fabric token available")

    fab.list_workspaces = no_auth  # type: ignore[method-assign]
    install(monkeypatch, fab)
    with pytest.raises(ShapeError) as err:
        SemanticModelSource().schema(URI)
    msg = str(err.value)
    assert msg.startswith("semantic-model:// needs sempy")
    assert "no Fabric token available" in msg and "\n" not in msg


def test_the_extra_is_declared_on_the_plugin_only():
    from pathlib import Path

    root = Path(__file__).parents[3]
    plugin = (root / "plugins/shape-fabric/pyproject.toml").read_text("utf-8")
    assert 'semantic-link = ["semantic-link-sempy' in plugin
    assert "semantic-link" not in (root / "pyproject.toml").read_text("utf-8")


# --- 5: errors name the object; DAX quoting ---------------------------------------------


def test_an_unknown_workspace(fabric):
    with pytest.raises(ShapeError) as err:
        SemanticModelSource().schema("semantic-model://Salez/Retail/Customer")
    assert str(err.value) == "semantic-model workspace Salez not found"


def test_an_unknown_model(fabric):
    with pytest.raises(ShapeError) as err:
        SemanticModelSource().schema("semantic-model://Sales/Retial/Customer")
    assert str(err.value) == "workspace Sales has no semantic model Retial"


def test_an_unknown_table(fabric):
    with pytest.raises(ShapeError) as err:
        SemanticModelSource().schema("semantic-model://Sales/Retail/Custmer")
    assert str(err.value) == "semantic model Retail in workspace Sales has no table Custmer"


def test_an_unknown_column(fabric):
    with pytest.raises(ShapeError) as err:
        SemanticModelSource().schema(URI, columns=["Name", "Cod"])
    assert str(err.value) == (
        "semantic model Retail in workspace Sales has no column Customer[Cod]"
    )


def test_names_are_shown_as_given_not_decoded_twice(fabric):
    with pytest.raises(ShapeError) as err:
        SemanticModelSource().schema("semantic-model://Sales/Retail/Cust%2Fx")
    assert str(err.value).endswith("has no table Cust/x")


def test_dax_quoting_keeps_a_name_inside_its_literal(fabric):
    s = SemanticModelSource()
    t = table_of(s, "semantic-model://Sales/Retail/Odd%20'table'", max_rows=2)
    assert t.column_names == ["It's [odd]", 'a"b']
    assert t.column("It's [odd]").to_pylist() == ["x", "y"]
    (call,) = fabric.called("evaluate_dax")
    assert "'Odd ''table'''" in call["dax"]
    assert "[It's [odd]]]" in call["dax"] and '[a"b]' in call["dax"]
    assert "\"c0\", 'Odd ''table'''" in call["dax"]  # results are named by position


def test_a_column_that_tries_to_end_the_expression_stays_quoted(monkeypatch):
    evil = 'x]) EVALUATE ROW("a", 1) //'
    model = FakeModel(
        tables={
            "T') EVALUATE ROW(1,1) //": FakeTable(
                columns=[(evil, "Int64", False)], rows=pd.DataFrame({evil: [1, 2]})
            )
        }
    )
    fab = install(monkeypatch, FakeFabric({"W": {"M": model}}))
    t = table_of(
        SemanticModelSource(), "semantic-model://W/M/T')%20EVALUATE%20ROW(1,1)%20%2F%2F", max_rows=1
    )
    assert t.column(evil).to_pylist() == [1]
    (call,) = fab.called("evaluate_dax")
    # the fake parses the whole expression strictly, so it only answered because both names
    # stayed inside their quoting; the table's quote is doubled in the text
    assert "TOPN(1, 'T'') EVALUATE ROW(1,1) //')" in call["dax"]


# --- shape profile semantic-model://... -------------------------------------------------------


def test_shape_profile_reads_a_semantic_model_table(fabric, tmp_path):
    import shape

    prof = shape.profile(URI)
    data = prof.to_dict()
    assert data["row_count"] == 4
    assert set(data["columns"]) == {"CustomerKey", "Name", "Code", "Joined"}
    assert data["columns"]["Name"]["null_count"] == 1


def test_the_profile_command_reads_a_semantic_model_table(fabric, tmp_path):
    import io
    from contextlib import redirect_stderr, redirect_stdout

    from shape.cli.main import main

    out = tmp_path / "customer.shape"
    with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
        code = main(["profile", URI, "-o", str(out)])
    assert code == 0 and out.is_file()


def test_the_auth_flag_does_not_apply(fabric):
    with pytest.raises(ShapeError, match="--auth does not apply"):
        SemanticModelSource().schema(URI, credential=object())


def test_the_module_declares_the_plugin_api_and_the_host_loads_the_source():
    from shape.plugins.host import default_host

    assert kit.check_module_api(ss) == "1.0"
    source = default_host().try_get("shape.sources", "semantic-model")
    assert isinstance(source, SemanticModelSource)

"""SEC-high: regression tests for the high-severity findings that blocked Gate G7.

One section per issue; every test failed before its fix. The shape-fabric plugin's tests (#411,
and the Kusto transport of #275) are in ``plugins/shape-fabric/tests/test_security_review.py``.
"""

from __future__ import annotations

import csv
import json
import re
import sys
import types
import urllib.request
from pathlib import Path
from typing import Any

import pyarrow as pa
import pytest

# ---- #724 and #285: statement injection in SQL scripts ----------------------------------------

_SEPARATOR = re.compile(r"(?im)^[ \t]*go(?:[ \t]+\d+)?[ \t]*$")


def _script(tmp_path: Path, table: pa.Table, **options: Any) -> str:
    from shape.builtins.sinks.sql import SqlSink

    target = tmp_path / "out.sql"
    SqlSink().write(str(target), "t", table.to_batches(), **options)
    return target.read_text(encoding="utf-8")


@pytest.mark.parametrize("dialect", ["tsql", "tsql-fabric-warehouse"])
def test_724_a_go_line_inside_a_value_is_not_a_batch_separator(tmp_path, dialect):
    evil = "x\nGO\nDROP TABLE victim;\ngo 5\nSELECT '"
    script = _script(tmp_path, pa.table({"s": [evil]}), sql_dialect=dialect)
    benign = _script(tmp_path, pa.table({"s": ["x"]}), sql_dialect=dialect)
    assert len(_SEPARATOR.findall(script)) == len(_SEPARATOR.findall(benign))
    assert "DROP TABLE victim;" not in script.splitlines()
    n = "N" if dialect == "tsql" else ""
    char = "NCHAR" if dialect == "tsql" else "CHAR"
    assert (
        f"{n}'x' + {char}(10) + {n}'GO' + {char}(10) + {n}'DROP TABLE victim;' + {char}(10) + "
        f"{n}'go 5' + {char}(10) + {n}'SELECT '''"
    ) in script


def test_724_carriage_returns_are_kept_as_characters():
    # INT-20 decision 1 (#724): the SQL sink splits a T-SQL value only when it holds a GO line
    from shape.builtins.sinks.sql import _literal

    assert _literal("a\r\nGO\r", "tsql") == "N'a' + NCHAR(13) + NCHAR(10) + N'GO' + NCHAR(13)"
    assert _literal("\nGO", "tsql") == "NCHAR(10) + N'GO'"
    assert _literal("plain 'text'", "tsql") == "N'plain ''text'''"  # unchanged bytes
    assert _literal("a\r\nb", "tsql") == "N'a\r\nb'"  # no GO line: unchanged bytes


def test_724_a_long_value_with_a_line_break_is_not_truncated_by_the_concatenation():
    from shape.builtins.sinks.sql import _literal

    text = "a" * 5000 + "\nGO\n" + "b"
    assert _literal(text, "tsql").startswith("CAST(N'' AS NVARCHAR(MAX)) + N'aaa")
    fabric = _literal("é" * 4100 + "\nGO\nb", "tsql-fabric")  # 8,200 bytes in UTF-8
    assert fabric.startswith("CAST('' AS VARCHAR(MAX)) + '")
    assert not _literal("a" * 3000 + "\nGO\nb", "tsql").startswith("CAST")


@pytest.mark.parametrize("dialect", ["tsql", "tsql-fabric-warehouse", "postgres", "mysql"])
@pytest.mark.parametrize("where", ["column", "table", "schema"])
def test_724_a_name_with_a_control_character_is_refused(tmp_path, dialect, where):
    # INT-20 decision 1 (#724): a T-SQL script refuses a name with a line break; the other
    # dialects quote it, and the name stays inside its quotes
    from shape.builtins.sinks.sql import SqlSink

    bad = "a\nGO\nDROP TABLE victim\n--"
    table = pa.table({bad if where == "column" else "a": [1]})
    options: dict[str, Any] = {"sql_dialect": dialect}
    if where == "schema":
        options["schema_name"] = bad
    target = tmp_path / "o.sql"
    write = lambda: SqlSink().write(  # noqa: E731
        str(target), bad if where == "table" else "t", table.to_batches(), **options
    )
    if dialect.startswith("tsql"):
        with pytest.raises(ValueError, match="line break"):
            write()
        assert not target.exists()
        return
    write()
    quote = '"' if dialect == "postgres" else "`"
    assert f"{quote}{bad}{quote}" in target.read_text(encoding="utf-8")


def test_285_the_design_ddl_and_contract_ddl_refuse_or_escape_line_breaks():
    from shape.contracts.emit import ddl as contract_ddl
    from shape.design import ddl as design_ddl

    with pytest.raises(ValueError, match="control character"):
        design_ddl._quote("x\nGO\nDROP TABLE dbo.victim\n--", "tsql")
    with pytest.raises(ValueError, match="control character"):
        contract_ddl._quote("x\rGO", "postgres")
    assert "\n" not in contract_ddl._literal("x\nGO\ny", "tsql")
    expected = "N'x' + NCHAR(10) + N'GO' + NCHAR(10) + N'y'"
    assert contract_ddl._literal("x\nGO\ny", "tsql") == expected


def test_285_postgres_literals_do_not_depend_on_standard_conforming_strings():
    from shape.builtins.sinks.sql import _literal
    from shape.contracts.emit import ddl as contract_ddl

    # E'' strings read backslashes the same way whatever standard_conforming_strings says
    assert _literal("\\'; DROP TABLE victim; --", "postgres") == "E'\\\\''; DROP TABLE victim; --'"
    assert _literal(b"\x01\x02", "postgres") == "E'\\\\x0102'"
    assert contract_ddl._literal("a\\b", "postgres") == "E'a\\\\b'"
    assert _literal("no backslash", "postgres") == "'no backslash'"  # unchanged bytes


# ---- #629: formula injection in the single-table Excel sink -----------------------------------


def test_629_the_excel_sink_stores_formula_text_as_text(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from shape.builtins.sinks.excel import ExcelSink

    target = tmp_path / "x.xlsx"
    table = pa.table({"=HYPERLINK(1)": ["=1+1", "#N/A", "ok", "a\x01b"], "n": [1, 2, 3, 4]})
    ExcelSink().write(str(target), "a/b:c", table.to_batches())
    book = openpyxl.load_workbook(target)
    assert book.sheetnames == ["a_b_c"]
    rows = [[(c.value, c.data_type) for c in r] for r in book.active.iter_rows()]
    assert rows[0][0] == ("=HYPERLINK(1)", "s")
    assert [r[0] for r in rows[1:]] == [("=1+1", "s"), ("#N/A", "s"), ("ok", "s"), ("ab", "s")]
    assert [r[1] for r in rows[1:]] == [(1, "n"), (2, "n"), (3, "n"), (4, "n")]


# ---- #275: bearer tokens follow redirects -----------------------------------------------------


def _redirect(handler: Any, old: str, new: str) -> Any:
    request = urllib.request.Request(old, headers={"Authorization": "Bearer SECRET"})
    return handler.redirect_request(request, None, 302, "Found", {}, new)


def test_275_redirects_off_the_origin_are_not_followed():
    from shape.scale.http import SameOriginRedirect

    handler = SameOriginRedirect()
    base = "https://api.fabric.microsoft.com/v1/x"
    for new in (
        "http://api.fabric.microsoft.com/v1/y",  # downgrade
        "https://attacker.example/stolen",
        "https://api.fabric.microsoft.com:8443/v1/y",
        "https://api.fabric.microsoft.com.attacker.example/v1/y",
    ):
        assert _redirect(handler, base, new) is None, new
    kept = _redirect(handler, base, "https://API.fabric.microsoft.com/v1/y")
    assert kept is not None and kept.full_url == "https://API.fabric.microsoft.com/v1/y"


def test_275_the_fabric_transport_uses_the_same_origin_opener(monkeypatch):
    from shape.scale import http
    from shape.scale.http import SameOriginRedirect

    seen: list[Any] = []

    class Opener:
        def open(self, request: Any, timeout: float) -> Any:
            seen.append(request)
            raise urllib.error.HTTPError(request.full_url, 302, "Found", {}, None)  # type: ignore[arg-type]

    monkeypatch.setattr(http, "_OPENER", Opener())
    response = http.urllib_transport("GET", "https://api.fabric.microsoft.com/v1/x", {}, None, 1)
    assert response.status == 302 and len(seen) == 1
    assert any(isinstance(h, SameOriginRedirect) for h in http.opener().handlers)


# ---- #276: storage credentials sent to any abfss host -----------------------------------------


class _RecordingAdlfs:
    calls: list[dict[str, Any]] = []

    class AzureBlobFileSystem:
        def __init__(self, **kwargs: Any) -> None:
            _RecordingAdlfs.calls.append(kwargs)


@pytest.fixture
def fake_adlfs(monkeypatch):
    module = types.ModuleType("adlfs")
    module.AzureBlobFileSystem = _RecordingAdlfs.AzureBlobFileSystem  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "adlfs", module)
    _RecordingAdlfs.calls = []
    return _RecordingAdlfs.calls


@pytest.mark.parametrize(
    "host",
    [
        "attacker.example.net",
        "acct.dfs.core.windows.net.attacker.net",
        "acct.dfs.core.windows.net:8443",
        "onelake.dfs.fabric.microsoft.com.evil.io",
        "evil.io#.dfs.core.windows.net",
        "dfs.core.windows.net",
    ],
)
def test_276_an_abfss_uri_off_azure_storage_gets_no_credential(fake_adlfs, monkeypatch, host):
    from shape.builtins.sources import azure

    monkeypatch.setenv("AZURE_STORAGE_SAS_TOKEN", "sv=2024&sig=USER_SAS_SECRET")
    with pytest.raises(ValueError, match="not an Azure Storage or OneLake host") as caught:
        azure._filesystem(azure.parse(f"abfss://raw@{host}/x"), {"sas_token": "USER_SAS_SECRET"})
    assert fake_adlfs == [] and "USER_SAS_SECRET" not in str(caught.value)


def test_276_the_sink_and_delta_paths_refuse_the_host_too(fake_adlfs, monkeypatch):
    from shape.builtins.sinks.azure import AbfssSink
    from shape.builtins.sources.delta import _storage_options

    monkeypatch.setenv("AZURE_STORAGE_SAS_TOKEN", "sv=2024&sig=USER_SAS_SECRET")
    batch = pa.record_batch({"a": [1]})
    with pytest.raises(ValueError, match="not an Azure Storage or OneLake host"):
        AbfssSink().write("abfss://raw@attacker.example.net/x", "t", [batch])
    with pytest.raises(ValueError, match="not an Azure Storage or OneLake host"):
        _storage_options("abfss://raw@attacker.example.net/x", {"sas_token": "s"})
    assert fake_adlfs == []


@pytest.mark.parametrize(
    "host",
    [
        "acct.dfs.core.windows.net",
        "acct.blob.core.windows.net",
        "ACCT.DFS.CORE.WINDOWS.NET",
        "onelake.dfs.fabric.microsoft.com",
        "westus-onelake.dfs.fabric.microsoft.com",
        "onelake.blob.fabric.microsoft.com",
        "acct.dfs.core.usgovcloudapi.net",
        "acct.blob.core.chinacloudapi.cn",
    ],
)
def test_276_azure_storage_and_onelake_hosts_still_work(fake_adlfs, host):
    from shape.builtins.sources import azure

    azure._filesystem(azure.parse(f"abfss://raw@{host}/x"), {"sas_token": "s"})
    assert len(fake_adlfs) == 1 and fake_adlfs[0]["sas_token"] == "s"


# ---- #533: joint-analysis entries in bridge diff and check ------------------------------------


def _bridge(tmp_path: Path, api: str = "1.0") -> Any:
    from shape.bridge.core import Bridge

    bridge = Bridge(tmp_path / "jobs")

    def call(command: str, options: dict[str, Any] | None = None, **args: Any) -> Any:
        request = {"api_version": api, "command": command, "args": args}
        if options:
            request["options"] = options
        return bridge.handle(json.dumps(request))

    return call


def _fd_csv(path: Path, broken: bool) -> None:
    with path.open("w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(["id", "email", "region"])
        for i in range(3000):
            k = i % 40
            region = "nsew"[k % 4]
            if broken and k < 10 and (i // 40) % 2:
                region = "nsew"[(k + 1) % 4]
            out.writerow([i, f"person{k}@secret.com", region])


def test_533_diff_and_check_withhold_values_of_a_classified_column_in_joint_entries(tmp_path):
    call = _bridge(tmp_path)
    _fd_csv(tmp_path / "ka.csv", False)
    _fd_csv(tmp_path / "kb.csv", True)
    a, b = str(tmp_path / "ka.shape"), str(tmp_path / "kb.shape")
    assert call("profile", source=str(tmp_path / "ka.csv"), output=a)["ok"]
    assert call("profile", source=str(tmp_path / "kb.csv"), output=b)["ok"]
    diff = call("diff", before=a, after=b)
    joint = [c for c in diff["result"]["changes"] if c["column"] == "email -> region"]
    assert joint and all(c["redacted"] is True for c in joint)
    assert "secret.com" not in json.dumps(diff)
    contract = tmp_path / "fd.json"
    rule = {"determinant": "email", "dependent": "region", "min_confidence": 0.99}
    contract.write_text(json.dumps({"fd": [rule]}))
    check = call("check", profile=b, contract=str(contract))
    assert check["result"]["violations"][0]["redacted"] is True
    assert "secret.com" not in json.dumps(check)
    raw = call("diff", {"include_raw_values": True}, before=a, after=b)
    assert "secret.com" in json.dumps(raw)  # the opt-in still gives the values


def test_533_redact_entries_reads_every_column_an_entry_names():
    from shape.bridge.handlers.flow import redact_entries

    secret = {"message": "worst: 'v'", "detail": {"violations": ["v"]}, "baseline": 1}
    entries = [
        {"column": "t.email -> region", **secret},
        {"column": "(a, email) -> region", **secret},
        {"column": "email ~ amount", **secret},
        {"column": "(email, b) in ref", **secret},
        {"column": "x -> y", **secret, "detail": {"determinant": ["email"], "dependent": "y"}},
        {"column": "region -> zone", **secret},
    ]
    out = redact_entries(entries, {"email", "t.email"})
    for entry in out[:5]:
        assert entry["redacted"] is True and entry["message"] is None and entry["detail"] is None
    assert out[5] == entries[5]  # an entry about unclassified columns is unchanged


# ---- #535: raw extremes in verify messages ----------------------------------------------------


@pytest.mark.parametrize("api", ["1.0", "1.2"])
def test_535_verify_withholds_the_actual_extreme_of_a_classified_column(tmp_path, api):
    call = _bridge(tmp_path, api)
    data = tmp_path / "vd"
    data.mkdir()
    with (data / "people.csv").open("w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(["id", "salary", "grade"])
        for i in range(500):
            out.writerow([i, 50000 + i * 37 + (987654 if i == 7 else 0), 1 + i % 4])
    config = tmp_path / "vc.json"
    ranges = {"people.salary": {"max": 100000}, "people.grade": {"max": 3}}
    config.write_text(json.dumps({"format": "shape-verify-config", "version": 1, "ranges": ranges}))
    result = call("verify", path=str(data), config=str(config))["result"]
    text = json.dumps(result["gates"])
    assert "1037913" not in text and "people.salary: 1 values above maximum 100000" in text
    assert "(actual max: 4.0)" in text  # grade is not classified: its message is unchanged
    raw = call("verify", {"include_raw_values": True}, path=str(data), config=str(config))
    assert "(actual max: 1037913.0)" in json.dumps(raw)


# ---- #663: the demo comparison page -----------------------------------------------------------


def test_663_the_comparison_page_shows_no_value_of_a_personal_data_column():
    from shape.demo.charts import render_html
    from shape.generation.learn import as_dataset
    from shape.profile.reference import profile

    emails = ["carla@secretcorp.example", "bo@secretcorp.example", "al@x.example", "di@y.example"]
    real = as_dataset(
        profile({"people": pa.table({"email": emails * 100, "city": ["Oslo", "Rome"] * 200})})
    )
    page = render_html(real, real, 1.0, "retail")
    assert "secretcorp" not in page and "al@x.example" not in page
    assert "<h3>city</h3>" in page and "Oslo" in page  # other columns keep their values
    assert "<td>email</td>" in page  # the column's shape is still shown


# ---- #650 (rest): small cells inside joint for profiles with nothing above the target ---------


def _joint_profile() -> dict[str, Any]:
    import random

    import shape

    rng = random.Random(2)
    city = [rng.choice(["CITYA", "CITYB", "CITYC"]) for _ in range(400)]
    state = [c.replace("CITY", "STATE") for c in city]
    city[0], state[0] = "RARECITY", "RARESTATE"
    state[5] = "ODDSTATE"
    return shape.profile(pa.table({"city": city, "state": state}), name="t").to_dict()


def test_650_release_for_suppresses_small_cells_inside_joint():
    from shape.privacy.policy import release_for

    doc = _joint_profile()
    assert "RARECITY" in json.dumps(doc["joint"]) and "ODDSTATE" in json.dumps(doc["joint"])
    released = release_for(doc, {}, "PUBLIC")
    joint = json.dumps(released.shape["joint"])
    for small in ("RARECITY", "RARESTATE", "ODDSTATE"):
        assert small not in joint, small
    assert "CITYA" in joint and "STATEA" in joint  # cells of k rows or more stay
    for conditional in released.shape["joint"]["conditionals"]:
        for row in conditional["table"].values():
            assert row["n"] >= 5
    assert any(r.startswith("joint.conditionals") for r in released.removed)


def test_650_release_for_suppresses_small_dependency_violations():
    from shape.privacy.policy import release_for

    doc = {"rows": 400, "columns": {"a": {"count": 400}, "b": {"count": 400}}}
    doc["joint"] = {
        "dependencies": [
            {
                "determinant": ["a"],
                "dependent": "b",
                "violations": [
                    {"determinant_value": "SMALL", "rows": 3, "dependent_values": {"x": 2}},
                    {
                        "determinant_value": "BIG",
                        "rows": 60,
                        "distinct_dependents": 2,
                        "dependent_values": {"y": 50, "TINY": 3, "z": 7},
                    },
                ],
            }
        ],
        "cohorts": {"categories": ["LEVEL"]},
    }
    joint = release_for(doc, {}, "PUBLIC").shape["joint"]
    (violation,) = joint["dependencies"][0]["violations"]
    assert violation["determinant_value"] == "BIG"
    assert "TINY" not in violation["dependent_values"] and violation["dependent_values"]["y"] == 50
    assert "cohorts" not in joint  # cells that cannot be checked are not released


# ---- #395: the safe profile of a column with fewer than k rows --------------------------------


def test_395_a_column_below_k_releases_no_value_statistic():
    import shape
    from shape.privacy.safe_profile import to_safe_profile

    prof = shape.profile(pa.table({"salary": [123456.0, None, None], "n": [1.0, 2.0, None]}))
    safe = to_safe_profile(prof).to_dict()
    for name in ("salary", "n"):
        col = safe["tables"]["table"]["columns"][name]
        for key in ("mean", "std", "quantiles", "bounds", "distribution_params"):
            assert col[key] is None, (name, key)
    assert "123456" not in json.dumps(safe)
    unsafe = to_safe_profile(prof, unsafe_full_fidelity=True).to_dict()
    assert unsafe["tables"]["table"]["columns"]["salary"]["mean"] == 123456.0


def test_395_a_column_with_k_rows_keeps_its_statistics():
    import shape
    from shape.privacy.safe_profile import to_safe_profile

    prof = shape.profile(pa.table({"x": [1.0, 2.0, 3.0, 4.0, 5.0, None]}))
    col = to_safe_profile(prof).to_dict()["tables"]["table"]["columns"]["x"]
    assert col["mean"] == 3.0


def test_395_the_validator_flags_a_value_statistic_of_a_column_below_k():
    import shape
    from shape.privacy.safe_profile import to_safe_profile
    from shape.privacy.safe_validator import SafeProfileValidator

    prof = shape.profile(pa.table({"salary": [123456.0]}))
    safe = to_safe_profile(prof).to_dict()
    assert SafeProfileValidator().validate_data(safe).is_clean
    safe["tables"]["table"]["columns"]["salary"]["mean"] = 123456.0  # as before the fix
    found = SafeProfileValidator().validate_data(safe)
    assert not found.is_clean
    assert [f.rule for f in found.findings] == ["small-cohort-statistic"]
    assert found.findings[0].path == "$.tables.table.columns.salary.mean"
